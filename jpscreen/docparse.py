"""Read the numbers that size each catalyst out of a TDnet PDF's text.

Mirrors the Korean `parse_catalyst`: every field is best-effort and stays None when not found.
Texts are NFKC-normalised by `pdf_bytes_to_text`, so digits/colons are ASCII here.
"""
from __future__ import annotations

import re
from typing import Optional

from .util import nfkc, parse_date

YEN_UNITS = {"円": 1, "千円": 1e3, "万円": 1e4, "百万円": 1e6, "億円": 1e8, "十億円": 1e9}


def flat(text: str) -> str:
    return re.sub(r"\s+", " ", nfkc(text or ""))


def tight(text: str) -> str:
    """Remove every space: pdftotext splits Japanese words with layout spaces (買 付 け 等 の 価 格)."""
    return re.sub(r"\s+", "", nfkc(text or ""))


def to_num(s: Optional[str]) -> Optional[float]:
    if s is None:
        return None
    t = s.replace(",", "").replace(" ", "")
    neg = t.startswith(("△", "▲", "-", "−"))
    t = re.sub(r"[^\d.]", "", t)
    if not t or t == ".":
        return None
    try:
        v = float(t)
    except ValueError:
        return None
    return -v if neg else v


AMOUNT_RE = re.compile(r"(約)?\s*([△▲\-]?[\d,]+(?:\.\d+)?)\s*(億)?\s*([\d,]+)?\s*(十億円|百万円|千円|万円|億円|円)")


def parse_amount(s: str) -> Optional[float]:
    """'約1,234百万円' / '35億4,000万円' / '12億円' / '1,234,567千円' -> yen."""
    m = AMOUNT_RE.search(s or "")
    if not m:
        return None
    lead = to_num(m.group(2))
    if lead is None:
        return None
    if m.group(3):  # 35億4,000万円
        rest = to_num(m.group(4)) if m.group(4) else 0
        unit = YEN_UNITS.get(m.group(5), 1)
        return lead * 1e8 + (rest or 0) * (unit if unit < 1e8 else 0)
    return lead * YEN_UNITS[m.group(5)]


def amount_after(text: str, labels: list[str], window: int = 80) -> Optional[float]:
    t = tight(text)
    for lab in labels:
        for m in re.finditer(lab, t):
            v = parse_amount(t[m.end():m.end() + window])
            if v is not None:
                return v
    return None


def number_after(text: str, labels: list[str], window: int = 60, unit: str = "") -> Optional[float]:
    t = tight(text)
    for lab in labels:
        for m in re.finditer(lab, t):
            seg = t[m.end():m.end() + window]
            mm = re.search(r"([△▲\-]?[\d,]+(?:\.\d+)?)" + unit, seg)
            if mm:
                v = to_num(mm.group(1))
                if v is not None:
                    return v
    return None


def pct_after(text: str, labels: list[str], window: int = 80) -> Optional[float]:
    return number_after(text, labels, window, unit=r"\s*[%％]")


def _period(t: str) -> tuple[Optional[str], Optional[str]]:
    m = re.search(r"(\d{4})年(\d{1,2})月(\d{1,2})日[^0-9]{0,12}?から(\d{4})年(\d{1,2})月(\d{1,2})日", t)
    if not m:
        return None, None
    a = parse_date(f"{m.group(1)}-{m.group(2)}-{m.group(3)}")
    b = parse_date(f"{m.group(4)}-{m.group(5)}-{m.group(6)}")
    return (a.isoformat() if a else None), (b.isoformat() if b else None)


# ------------------------------------------------------------------ tender offers

def parse_tender(text: str) -> dict:
    t = tight(text)
    out: dict = {}
    # the terms table first ("普通株式 当社株式1株につき、金2,020円"); the loose form last, because an opinion statement
    # also recounts earlier proposals ("本公開買付価格を1,629円とする提案")
    m = re.search(r"普通株式[^0-9円。]{0,16}?1株(?:につき|当たり)[、,]?金?([\d,]+(?:\.\d+)?)円", t) or \
        re.search(r"買付け等の価格[^0-9円]{0,40}?1株(?:につき|当たり)[、,]?金?([\d,]+(?:\.\d+)?)円", t) or \
        re.search(r"(?:公開買付価格|買付け等の価格|買付価格)(?:は|:)?[^0-9]{0,30}?([\d,]{2,}(?:\.\d+)?)円", t) or \
        re.search(r"1株(?:当たり|につき)[^0-9]{0,30}?([\d,]{3,}(?:\.\d+)?)円", t)     # squeeze-outs: the cash per share
    out["price"] = to_num(m.group(1)) if m else None
    # price after a bump: '…を1株につき金X円から金Y円に変更'
    bumps = [(to_num(a), to_num(b)) for a, b in
             re.findall(r"金?([\d,]{2,})円から金?([\d,]{2,})円(?:に|へ)(?:変更|引き上げ|引上げ)", t)]
    bumps = [x for x in bumps if x[0] and x[1] and x[1] > x[0]]
    if bumps:  # the latest bump carries the highest price
        out["old_price"], out["price"] = max(bumps, key=lambda x: x[1])
        out["bumps"] = len(set(bumps))
    i = t.find("買付け等の期間")
    if i < 0:
        i = t.find("公開買付期間")
    out["start"], out["end"] = _period(t[max(0, i - 150):i + 200] if i >= 0 else t)
    if out["start"] is None:
        out["start"], out["end"] = _period(t)
    m = re.search(r"買付予定数の上限[^0-9―\-ー]{0,20}([―\-ー]|[\d,]+)", t)
    if m:
        out["upper_limit"] = None if m.group(1) in "―-ー" else to_num(m.group(1))
        out["partial"] = out["upper_limit"] is not None
    if re.search(r"上限(?:は|を)設定して(?:おりません|いません|おらず|いない)|上限を設け(?:ておりません|ていません|ず)", t):
        out["upper_limit"], out["partial"] = None, False      # a table read "買付予定数 | 下限 | 上限" column-wise is not a cap
    m = re.search(r"買付予定数の下限[^0-9―\-ー]{0,20}([―\-ー]|[\d,]+)", t)
    if m:
        out["lower_limit"] = None if m.group(1) in "―-ー" else to_num(m.group(1))
    m = re.search(r"公開買付けの目的([^0-9]{2,20}?)(?:買付け等の期間|\d)", t)
    purpose = m.group(1) if m else ""
    out["purpose"] = purpose[:30]
    out["delisting"] = bool(re.search(r"完全子会社化|非公開化|上場廃止となる(見込み|予定)|上場廃止(と|に)なる|全ての株式.*取得", t[:30000]))
    out["mbo"] = bool(re.search(r"(マネジメント・?バイアウト|MBO)(\(|（)?(MBO)?(\)|）)?(に該当|として|の一環|を実施)", t[:12000]))
    if re.search(r"賛同.{0,20}応募(を)?推奨|応募推奨", t[:20000]):
        out["board_view"] = "support"
    elif re.search(r"応募.{0,15}(中立|株主(の)?(皆様の)?判断に委ね)", t[:20000]):
        out["board_view"] = "neutral"
    elif re.search(r"反対(の)?意見|反対する", t[:20000]):
        out["board_view"] = "oppose"
    elif re.search(r"意見(を)?留保|意見の表明を留保", t[:20000]):
        out["board_view"] = "reserve"
    m = re.search(r"(?:公開買付者|買付者)(?:の)?(?:名称|商号)[:]?((?:株式会社|合同会社)?[^\s、。(（]{2,40}?(?:株式会社|合同会社|LLC|L\.P\.|Limited|Ltd\.?|Inc\.?|HoldingsInc\.?)?)(?:所在地|\(|（|\d)", t)
    if not m:
        m = re.search(r"([^\s、。「」()（）]{2,40}?(?:株式会社|合同会社|LLC|Limited|L\.P\.))による(?:当社|株式会社)", t[:3000])
    out["bidder"] = m.group(1)[:60] if m else None
    m = re.search(r"買付予定数.{0,60}?所有割合[^0-9]{0,20}([\d.]+)%", t)
    out["pct_of_shares"] = to_num(m.group(1)) if (m and out.get("partial")) else None
    m = re.search(r"(?:プレミアム|プレミアムを加えた).{0,80}?([\d.]+)%", t)
    out["premium_stated"] = to_num(m.group(1)) if m else None
    return out


def parse_squeeze(text: str) -> dict:
    t = tight(text)
    m = re.search(r"(?:1株(?:当たり|につき)|1株あたり)[^0-9]{0,20}([\d,]{2,})円", t)
    return {"price": to_num(m.group(1)) if m else None,
            "delisting_date": (lambda d: d.isoformat() if d else None)(parse_date(
                (re.search(r"上場廃止日[^0-9]{0,10}(\d{4}年\d{1,2}月\d{1,2}日)", t) or [None, None])[1]))}


# ------------------------------------------------------------------ strategic / rumours

DEAL_WORDS = ("公開買付", "TOB", "MBO", "マネジメント・バイアウト", "非公開化", "買収", "完全子会社化", "経営統合", "売却",
              "株式譲渡", "資本提携", "特別委員会", "戦略的", "合併", "スクイーズアウト", "上場廃止", "初期的な提案",
              "法的拘束力", "意向表明", "買収提案", "株式の取得に係る")
RETURN_WORDS = ("自己株式", "自社株", "消却", "増配", "配当方針", "株主還元", "DOE", "総還元性向")


def parse_rumor(text: str) -> dict:
    t = tight(text)
    head = t[:2500]
    kws = [w for w in DEAL_WORDS + RETURN_WORDS if w in head]
    out = {"keywords": kws[:6]}
    out["confirmed_review"] = bool(re.search(r"検討(している|を進めている|中であること|していること)(は|が)?事実", head)
                                   or re.search(r"(協議|検討)を(行って|進めて)(いる|おります)", head))
    out["denied"] = bool(re.search(r"(その)?ような事実(は)?(一切)?ありません|検討している事実(は)?ありません|"
                                   r"協議(を)?(している|を行っている)事実(は)?ありません", head)) and not out["confirmed_review"]
    out["unconfirmed"] = "決定した事実はありません" in head or "決定した事実はございません" in head
    body = head
    tel = re.search(r"(TEL|ＴＥＬ|電話)[^)）]{0,30}[)）]", head[:600])
    if tel:
        body = head[tel.end():]   # drop the standard letterhead (date, 各位, company, representative, contact)
    k = re.search(r"(お知らせ|について|に関して)(?=[^。]{0,4}(当社|当行|本日|昨日|[0-9]+月|[0-9]{4}年))", body[:200])
    if k:
        body = body[k.end():]
    m = re.search(r"[^。]*(報道|提案|特別委員会|検討|ストラテジック|経営統合)[^。]*。", body)
    out["snippet"] = (m.group(0) if m else body[:160])[:200]
    out["special_committee"] = "特別委員会" in t[:8000]
    out["proposal"] = bool(re.search(r"初期的な?提案|法的拘束力(の|を)?(ない|有しない)|意向表明書|買収提案|買収の提案", t[:8000]))
    return out


# ------------------------------------------------------------------ holders and control

def parse_holder_change(text: str) -> dict:
    t = tight(text)
    out: dict = {}
    m = re.search(r"(?:異動する株主|株主)の概要.{0,20}?(?:名称|氏名|商号)[:]?([^\s所在住]{2,40}?)(?:所在地|住所|\()", t)
    out["holder"] = m.group(1) if m else None
    before = re.search(r"異動前.{0,140}?([\d.]+)%", t)
    after = re.search(r"異動後.{0,140}?([\d.]+)%", t)
    out["pct_before"] = to_num(before.group(1)) if before else None
    out["pct_after"] = to_num(after.group(1)) if after else None
    if out["pct_before"] is not None and out["pct_after"] is not None:
        out["direction"] = "up" if out["pct_after"] > out["pct_before"] else "down"
    out["parent_change"] = "親会社" in t[:600]
    out["tob_related"] = "公開買付" in t[:3000]
    return out


def parse_holding(text: str) -> dict:
    out = parse_holder_change(text)
    t = tight(text)
    m = re.search(r"(?:保有目的)[:]?([^。]{2,60})", t)
    out["purpose"] = m.group(1)[:60] if m else None
    out["control"] = bool(re.search(r"重要提案|経営(への)?関与|経営参加|支配権", t[:6000]))
    m = re.search(r"(?:買集め|買い集め)(?:を行った|行為を行った)?者[^\s]{0,10}?(?:名称|氏名)[:]?([^\s所在住]{2,40}?)(?:所在地|住所)", t)
    if m:
        out["holder"] = m.group(1)
    return out


def parse_proxy(text: str) -> dict:
    t = tight(text)
    m = re.search(r"(?:提案株主|株主)(?:の)?(?:名称|氏名)[:]?([^\s所在住]{2,40}?)(?:所在地|住所|\(|（|保有)", t)
    agenda = re.findall(r"第\d号議案[:]?([^第]{2,40})", t[:6000])
    return {"holder": m.group(1) if m else None, "agenda": "; ".join(a[:30] for a in agenda[:4])}


# ------------------------------------------------------------------ mergers and exchanges

def parse_mna(text: str) -> dict:
    t = tight(text)
    out: dict = {}
    m = re.search(r"(?:株式交換比率|合併比率|株式移転比率|割当比率|割当ての内容).{0,120}?(\d+(?:\.\d+)?)[:：](\d+(?:\.\d+)?)", t)
    if not m:
        m = re.search(r"(?:株式交換|合併)に係る割当(?:比率|ての内容)\D{0,40}?(\d+(?:\.\d+)?)\D{1,20}?(\d+(?:\.\d+)?)", t)
    if m:
        out["ratio_parent"], out["ratio_target"] = to_num(m.group(1)), to_num(m.group(2))
    codes = re.findall(r"(?:証券)?コード[:]?([0-9][0-9A-Z]{3})", t[:4000])
    out["codes"] = list(dict.fromkeys(codes))
    out["delisting"] = bool(re.search(r"上場廃止(となる|となります|の予定|される予定)", t[:20000]))
    out["wholly_owned"] = "完全子会社" in t[:6000]
    out["unlisted_target"] = "非上場" in t[:6000]
    m = re.search(r"(?:株式交換|合併)(?:の)?(?:効力発生日|効力発生予定日)[^0-9]{0,6}(\d{4}年\d{1,2}月\d{1,2}日)", t)
    d = parse_date(m.group(1)) if m else None
    out["effective"] = d.isoformat() if d else None
    out["physical_split"] = bool(re.search(r"分社型|持株会社体制|子会社(へ|に)(承継|移転)", t[:8000]))
    out["spinoff"] = bool(re.search(r"スピンオフ|株式分配|分割型分割", t[:8000]))
    out["backdoor"] = any(not re.search(r"該当しない|該当せず|該当する見込みはない|該当しません", t[m.end():m.end() + 60])
                          for m in re.finditer(r"不適当な合併|実質的な存続性|実質的存続性|逆取得", t[:20000]))
    return out


# ------------------------------------------------------------------ liquidation / listing

def parse_liquidation(text: str) -> dict:
    t = tight(text)
    m = re.search(r"(?:解散|清算)(?:の)?(?:理由|経緯|目的)[:]?([^。]{4,120})", t)
    out = {"reason": (m.group(1) if m else "")[:120]}
    m = re.search(r"残余財産.{0,60}?1株(?:当たり|あたり|につき)[^0-9]{0,20}([\d,]+(?:\.\d+)?)円", t)
    out["distribution_per_share"] = to_num(m.group(1)) if m else None
    out["delisting"] = "上場廃止" in t
    return out


def parse_listing(text: str) -> dict:
    t = tight(text)
    crit = [c for c in ("流通株式時価総額", "流通株式比率", "流通株式数", "純資産", "売買代金", "株主数", "時価総額") if c in t[:6000]]
    m = re.search(r"改善期間.{0,40}?(\d{4}年\d{1,2}月\d{1,2}日)", t)
    d = parse_date(m.group(1)) if m else None
    return {"criteria": crit[:4], "deadline": d.isoformat() if d else None,
            "non_compliant": bool(re.search(r"適合していない|不適合|満たしていない|未達", t[:8000])),
            "considering_options": bool(re.search(r"非公開化|MBO|上場廃止|市場区分の変更|親会社", t[:12000]))}


# ------------------------------------------------------------------ shareholder returns

def parse_buyback(text: str) -> dict:
    t = tight(text)
    out: dict = {}
    m = re.search(r"取得(し得る|する)株式の総数[:]?(?:普通株式)?[^0-9]{0,10}([\d,]+)(万)?株", t)
    if m:
        out["shares"] = to_num(m.group(2)) * (1e4 if m.group(3) else 1)
    out["pct_of_shares"] = pct_after(t, [r"発行済株式(総数)?(\(|（)?自己株式を除く(\)|）)?に対する割合",
                                         r"に対する割合"], window=40)
    out["amount"] = amount_after(t, [r"株式の取得価額の総額", r"取得価額の総額", r"取得総額", r"買付総額"])
    if out.get("amount") is None:
        m = re.search(r"取得価額(?:の)?総額[^0-9]{0,10}([\d,.]+)(億)?([\d,]+)?(百万円|千円|万円|億円|円)", t)
        if m:
            out["amount"] = parse_amount(m.group(0)[m.group(0).find(m.group(1)):])
    s, e = _period(t)
    out["start"], out["end"] = s, e
    out["to_cancel"] = bool(re.search(r"消却", t[:4000]))
    out["tostnet"] = bool(re.search(r"ToSTNeT|立会外", t[:3000], re.I))
    m = re.search(r"買付価格[^0-9]{0,20}([\d,]+(?:\.\d+)?)円", t)
    out["tostnet_price"] = to_num(m.group(1)) if (m and out["tostnet"]) else None
    out["specific_holder"] = "特定の株主" in t[:2000] or "特定株主" in t[:2000]
    return out


def parse_cancellation(text: str) -> dict:
    t = tight(text)
    m = re.search(r"消却(する|した)?株式の数[:]?(?:普通株式)?[^0-9]{0,6}([\d,]+)株", t)
    pct = pct_after(t, [r"消却前の発行済株式(の)?総数に対する割合", r"発行済株式総数に対する割合", r"に対する割合"], 40)
    return {"shares": to_num(m.group(2)) if m else None, "pct_of_issued": pct}


YEN_CELL = re.compile(r"([△▲\-]?[\d,]+)円(?:(\d{1,2})銭)?|([△▲\-]?[\d,]+\.\d+)円")


def _yen_cells(line: str) -> list[float]:
    vals = []
    for m in YEN_CELL.finditer(nfkc(line).replace(" ", "")):
        if m.group(3):
            vals.append(to_num(m.group(3)))
        else:
            v = to_num(m.group(1))
            if v is not None and m.group(2):
                v = v + (float(m.group(2)) / 100 if v >= 0 else -float(m.group(2)) / 100)
            vals.append(v)
    return [v for v in vals if v is not None]


def _lines(text: str) -> list[str]:
    return [ln for ln in nfkc(text or "").splitlines() if ln.strip()]


def _label_line(lines: list[str], pattern: str) -> Optional[str]:
    rx = re.compile(pattern)
    for ln in lines:
        if rx.search(re.sub(r"\s+", "", ln)):
            return ln
    return None


def parse_dividend(text: str) -> dict:
    t = tight(text)
    lines = _lines(text)
    out: dict = {"special": bool(re.search(r"(特別配当|記念配当)[^0-9]{0,6}[1-9][\d,]*円", t[:6000])),
                 "initiation": bool(re.search(r"初配|復配|配当(の)?開始", t[:4000]))}
    # annual table (配当予想の修正 / ご参考 年間配当金): the last cell of a row is the annual total
    new_row = _label_line(lines, r"^(今回修正予想|今回予想|修正後予想|今回決定|直近配当予想|直近の配当予想)")
    prev_row = _label_line(lines, r"^(前回予想|前回発表予想|修正前|当初予想|\(参考\)当初予想)")
    prior_row = _label_line(lines, r"^(前期実績|\(参考\)前期実績|前年同期実績)")
    nv, pv, yv = (_yen_cells(r)[-1:] if r else [] for r in (new_row, prev_row, prior_row))
    out["dps"] = nv[0] if nv else None
    out["prev_forecast"] = pv[0] if pv else None
    out["prior_year"] = yv[0] if yv else None
    # single-payment table (剰余金の配当): 決定額 | 直近の配当予想 | 前期実績
    row = _label_line(lines, r"1株当たり配当金")
    if row and out["dps"] is None:
        cells = _yen_cells(row)
        if cells:
            out["dps"] = cells[0]
            if len(cells) >= 3:
                out["prev_forecast"], out["prior_year"] = cells[1], cells[2]
            elif len(cells) == 2:
                out["prior_year"] = cells[1]
    for k in ("dps", "prev_forecast", "prior_year"):
        if out.get(k) is not None and not (0 < out[k] < 1e5):
            out[k] = None
    base = out["prior_year"] or out["prev_forecast"]
    out["raise_pct"] = ((out["dps"] / base - 1) * 100) if (out["dps"] and base and base > 0) else None
    if out["raise_pct"] is not None and not (-60 <= out["raise_pct"] <= 400):
        out["raise_pct"] = None  # interim vs annual or a split: not comparable
    out["doe"] = "DOE" in t or "株主資本配当率" in t
    return out


NUM_TOKEN = re.compile(r"[△▲\-]?[\d,]+(?:\.\d+)?|[―\-ー－]")


def _row_numbers(line: Optional[str], label_end: str) -> list[Optional[float]]:
    if not line:
        return []
    s = nfkc(line)
    m = re.search(label_end, s.replace(" ", ""))
    # drop the label: everything up to the closing ')' of '(A)'/'(B)' or the label word
    cut = s.find(")") + 1 if ")" in s[:40] else len(re.match(r"^\s*\S+", s).group(0))
    vals = []
    for tok in re.findall(r"[△▲]?-?[\d,]+(?:\.\d+)?|(?<=\s)[―ー－-](?=\s|$)", s[cut:]):
        vals.append(None if tok in "―ー－-" else to_num(tok))
    return vals


GUIDE_NEW = re.compile(r"^(今回修正予想|今回予想|修正予想|実績値|今回発表予想|当期実績)")
GUIDE_PREV = re.compile(r"^(前回発表予想|前回予想|前回公表予想)")
GUIDE_PRIOR = re.compile(r"^(\(参考\))?前期(通期)?実績")


def _numbers_near(lines: list[str], i: int, label: str) -> list[Optional[float]]:
    """The row's numbers: on the label's own line, or on the next line when the PDF split the row."""
    vals = _row_numbers(lines[i], label)
    if not any(v is not None for v in vals) and i + 1 < len(lines):
        nxt = lines[i + 1]
        if re.match(r"^\s*[△▲\-―ー－\d]", nfkc(nxt)):
            vals = _row_numbers("x " + nxt, label)
    return vals


def parse_guidance(text: str) -> dict:
    """Forecast revisions, and first forecasts after one was withheld (未定): the full-year consolidated table,
    against the previous forecast or, for a first forecast, against last year's actual result."""
    lines = _lines(text)
    sq = [re.sub(r"\s+", "", nfkc(ln)) for ln in lines]
    rows = [i for i, x in enumerate(sq) if GUIDE_NEW.search(x)]
    out: dict = {}
    names = ["sales", "op", "ord", "ni"]
    if rows:
        def header_score(i):
            prev_row = max([r for r in rows if r < i], default=-1)
            head = "".join(sq[max(prev_row + 1, i - 16):i])
            sc = 2 if "通期" in head else 0
            if re.search(r"第2四半期|中間期|累計", head):
                sc -= 1
            if "個別" in head and "連結" in "".join(sq):
                sc -= 2
            return sc
        bi = max(rows, key=lambda i: (header_score(i), -i))
        prev_rows = [i for i, x in enumerate(sq[:bi]) if GUIDE_PREV.search(x)]
        ai = prev_rows[-1] if prev_rows and bi - prev_rows[-1] <= 8 else None
        ra = _numbers_near(lines, ai, "A") if ai is not None else []
        rb = _numbers_near(lines, bi, "B")
        if not any(v is not None for v in ra[:4]):
            # a first forecast (the previous one was "-" / 未定): compare with last year's actual result
            pi = next((i for i in range(bi + 1, min(bi + 10, len(sq))) if GUIDE_PRIOR.search(sq[i])), None)
            if pi is not None:
                ra = _numbers_near(lines, pi, "")
                if any(v is not None for v in ra[:4]):
                    out["first_forecast"] = True
        for i, nm in enumerate(names):
            va = ra[i] if i < len(ra) else None
            vb = rb[i] if i < len(rb) else None
            out[nm + "_prev"], out[nm + "_new"] = va, vb
            if va is not None and vb is not None:
                out[nm + "_chg"] = ((vb - va) / abs(va) * 100) if va else None
                out[nm + "_delta"] = vb - va
    out["direction"] = None
    for nm in ("op", "ni", "sales"):
        d = out.get(nm + "_delta")
        if d:
            out["direction"] = "up" if d > 0 else "down"
            break
    if out["direction"] is None:
        t = tight(text)
        if re.search(r"上方修正|上回る見込み|上回る", t[:3000]):
            out["direction"] = "up"
        elif re.search(r"下方修正|下回る見込み|下回る", t[:3000]):
            out["direction"] = "down"
    div = parse_dividend(text)
    if div.get("raise_pct") is not None:
        out["div_raise_pct"] = div["raise_pct"]
        out["dps"] = div.get("dps")
    return out


def parse_value_up(text: str) -> dict:
    t = tight(text)
    words = ("DOE", "総還元性向", "配当性向", "累進配当", "自己株式", "消却", "ROE", "PBR", "政策保有株式",
             "資本コスト", "非事業用資産", "不動産の売却", "株主還元")
    out = {"mentions": [w for w in words if w in t]}
    out["payout_target"] = pct_after(t, [r"総還元性向", r"配当性向"], 30)
    out["roe_target"] = pct_after(t, [r"ROE"], 30)
    out["doe_target"] = pct_after(t, [r"DOE", r"株主資本配当率"], 30)
    return out


# ------------------------------------------------------------------ business and assets

def parse_asset_sale(text: str) -> dict:
    t = tight(text)
    out: dict = {}
    out["proceeds"] = amount_after(t, [r"譲渡価額", r"譲渡価格", r"売却価額", r"売却価格", r"譲渡金額"])
    out["book_value"] = amount_after(t, [r"帳簿価額", r"簿価"])
    out["gain"] = amount_after(t, [r"(?:譲渡|売却)益", r"特別利益(?:として)?", r"利益(?:として)?約"], window=60)
    if "非開示" in t[:6000] and out["proceeds"] is None:
        out["undisclosed_price"] = True
    kinds = [k for k in ("土地", "建物", "投資有価証券", "政策保有株式", "子会社株式", "事業", "賃貸") if k in t[:4000]]
    out["asset"] = "・".join(kinds[:3])
    out["real_estate"] = bool(re.search(r"土地|建物|不動産|賃貸", t[:4000]))
    out["cross_holdings"] = bool(re.search(r"投資有価証券|政策保有", t[:4000]))
    return out


def parse_contract(text: str) -> dict:
    t = tight(text)
    return {"amount": amount_after(t, [r"受注金額", r"契約金額", r"受注額", r"総額"]),
            "pct_of_revenue": pct_after(t, [r"売上高に対する割合", r"売上高比", r"に占める割合"], 40)}


PARSERS = {
    "tender_offer": parse_tender, "self_tender": parse_tender, "squeeze_out": parse_squeeze,
    "rumor": parse_rumor, "strategic": parse_rumor,
    "control_change": parse_holder_change, "holding": parse_holding,
    "proxy_fight": parse_proxy, "control_dispute": parse_proxy,
    "merger": parse_mna, "share_swap": parse_mna, "split": parse_mna, "reverse_merger": parse_mna,
    "liquidation": parse_liquidation, "listing": parse_listing,
    "buyback": parse_buyback, "cancellation": parse_cancellation, "dividend": parse_dividend,
    "value_up": parse_value_up, "guidance": parse_guidance, "asset_sale": parse_asset_sale,
    "contract": parse_contract, "capital_return": parse_dividend,
}


def parse_catalyst(kind: str, text: str) -> dict:
    fn = PARSERS.get(kind)
    out = dict(kind=kind)
    if fn and text:
        try:
            out.update(fn(text))
        except Exception as e:  # a parser bug must never stop a scan
            out["parse_error"] = str(e)[:120]
    return out
