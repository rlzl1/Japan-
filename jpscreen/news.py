"""Company news for the portfolio feed.

  Kabutan (s.kabutan.jp/stocks/<code>/news/): TDnet filings with permanent PDF links plus Kabutan's own short news
  (results, revisions, market moves). Google News RSS: wider press coverage, kept only when the headline names the
  company. Titles stay in Japanese here; English titles come from title_en() or the refresh's translation step.
"""
from __future__ import annotations

import datetime as dt
import html
import logging
import re
from typing import Optional
from xml.etree import ElementTree as ET

from .http import Http, HttpError
from .util import nfkc

log = logging.getLogger(__name__)

KABUTAN = "https://s.kabutan.jp/stocks/{code}/news/"
GNEWS = "https://news.google.com/rss/search"

KABU_CAT = {"開示": "filing", "決算": "results", "修正": "results", "材料": "news", "注目": "news", "特集": "news",
            "テク": "news", "市況": "news", "速報": "news"}


def _clean(s: str) -> str:
    return re.sub(r"\s+", " ", html.unescape(re.sub(r"<[^>]+>", " ", s or ""))).strip()


def kabutan_news(http: Http, code: str, ttl: float = 3 * 3600) -> list[dict]:
    try:
        page = http.get_text(KABUTAN.format(code=code), ttl=ttl, allow_404=True)
    except HttpError as e:
        log.info("kabutan %s: %s", code, e)
        return []
    out = []
    for li in re.findall(r'<li class="py-2 border-b border-slate-200">(.*?)</li>', page or "", re.S):
        href = re.search(r'href="([^"]+)"', li)
        tm = re.search(r'datetime="(\d{4}-\d{2}-\d{2})', li)
        lab = re.search(r'<span class="[a-z_\-]+[^"]*rounded-sm[^>]*>\s*([^<]+?)\s*</span>', li)
        body = re.sub(r"<time.*?</time>|<span.*?</span>", "", li, flags=re.S)
        title = _clean(body)
        if not href or not tm or not title:
            continue
        url = href.group(1)
        if url.startswith("/"):
            url = "https://s.kabutan.jp" + url
        label = (lab.group(1) if lab else "").strip()
        filing = label == "開示" or "tdnet-pdf" in url
        m = re.search(r"/(\d{18})\.pdf", url)
        out.append(dict(id=("t" + m.group(1)) if m else "k" + re.sub(r"\W", "", url.split("/news/")[-1])[:40],
                        date=tm.group(1), kind="filing" if filing else "news", ja=nfkc(title)[:200],
                        cat=KABU_CAT.get(label, "news") if not filing else "filing", url=url,
                        src="TDnet" if filing else "Kabutan"))
    return [it for it in out if not is_noise(it)]


def google_news(http: Http, name: str, ttl: float = 3 * 3600, days: int = 120) -> list[dict]:
    if not name or len(name) < 2:
        return []
    try:
        data = http.get(GNEWS, params={"q": f"\"{name}\"", "hl": "ja", "gl": "JP", "ceid": "JP:ja"}, ttl=ttl)
        root = ET.fromstring(data)
    except (HttpError, ET.ParseError) as e:
        log.info("gnews %s: %s", name, e)
        return []
    cutoff = dt.date.today() - dt.timedelta(days=days)
    out = []
    for it in root.iter("item"):
        title = _clean(it.findtext("title") or "")
        link = (it.findtext("link") or "").strip()
        src = _clean(it.findtext("source") or "")
        try:
            d = dt.datetime.strptime((it.findtext("pubDate") or "")[:25].strip(), "%a, %d %b %Y %H:%M:%S").date()
        except ValueError:
            continue
        if d < cutoff or not link.startswith("https://"):
            continue
        if src and title.endswith(" - " + src):
            title = title[: -len(src) - 3]
        if name not in nfkc(title) or is_noise(dict(kind="news", ja=nfkc(title), src=src)):
            continue            # quote/portal pages, not stories
        gid = re.sub(r"\W", "", link.split("/")[-1])[-40:]
        out.append(dict(id="g" + gid, date=d.isoformat(), kind="news", ja=nfkc(title)[:200], cat="news", url=link,
                        src=src[:30] or "Google News"))
    return out


# Market-wide columns and screens that merely list the stock, quote/portal pages, consumer reviews, mirrors of filings
NOISE = re.compile(r"^★?本日の|【ボリンジャー|ゴールデンクロス|デッドクロス|個人投資家の予想から|特集\s*[>＞]|注目個別銘柄|"
                   r"ストップ(高|安)銘柄|上抜け=|大引け[)）]|前引け[)）]|ランキング|クチコミ|感想・|株価・株式情報|掲示板|"
                   r"企業情報\(|決算情報$|【\d[0-9A-Z]{3}】|適時開示|日経会社情報|\[\d[0-9A-Z]{3}\]:")


def is_noise(it: dict) -> bool:
    """A news item that is not a story about this company (filings are never noise)."""
    if it.get("kind") != "news":
        return False
    src = it.get("src") or ""
    return bool(NOISE.search(nfkc(it.get("ja") or ""))) or (src != "Kabutan" and bool(PORTAL_SRC.search(src)))


PORTAL_SRC = re.compile(r"Yahoo!?ファイナンス|みんかぶ|株探|かぶたん|IR BANK|Kabutan|minkabu|株マップ|バフェット・コード|ifis|Ullet|もぐナビ|"
                        r"株予報|日経会社情報|会社四季報オンライン.*銘柄", re.I)


def short_name(name: str) -> str:
    n = nfkc(name or "")
    n = re.sub(r"株式会社|\(株\)|（株）|㈱|ホールディングス$|ＨＤ$|HD$", "", n).strip()
    return n


# English titles for routine Japanese disclosure titles; anything else is translated in the refresh.
TITLE_EN = [
    (r"第([1-3１-３])四半期.*決算短信", lambda m: f"Q{nfkc(m.group(1))} earnings report"),
    (r"(中間期|第2四半期).*決算短信", "H1 earnings report"),
    (r"決算短信", "Full-year earnings report"),
    (r"決算説明(資料|会)", "Results presentation"),
    (r"業績予想(および|及び|並びに)配当予想の修正", "Earnings and dividend forecast revision"),
    (r"業績予想(の)?修正|業績予想に関するお知らせ", "Earnings forecast revision"),
    (r"予想値と(実績|決算)値との差異", "Results vs forecast difference"),
    (r"配当予想の修正", "Dividend forecast revision"),
    (r"剰余金の配当", "Dividend decision"),
    (r"自己株式(の)?取得(状況|結果)|取得状況|自己株券買付状況報告書", "Buyback progress"),
    (r"決算補足説明資料|決算補足資料", "Results supplementary material"),
    (r"株式の立会外分売終了", "Off-floor secondary sale completed"),
    (r"株式の立会外分売", "Off-floor secondary sale"),
    (r"株式(の)?無償割当", "Free share allotment"),
    (r"株式併合", "Reverse stock split"),
    (r"業績連動型株式報酬|株式報酬制度", "Share-based compensation"),
    (r"自己株式(の)?処分", "Disposal of treasury shares"),
    (r"定款(の)?一部変更", "Amendment to the articles"),
    (r"自己株式(の)?消却", "Share cancellation"),
    (r"自己株式(の)?取得|自己株式立会外買付", "Share buyback"),
    (r"公開買付", "Tender offer"),
    (r"株式分割", "Stock split"),
    (r"株主優待", "Shareholder perks"),
    (r"主要株主.*異動|筆頭株主.*異動", "Change in major shareholder"),
    (r"代表取締役.*異動", "Change of representative director"),
    (r"役員(の)?異動|役員人事|人事異動|執行役員", "Board and management changes"),
    (r"コーポレート・?ガバナンス", "Corporate governance report"),
    (r"定時株主総会.*招集|招集通知", "AGM notice"),
    (r"株主総会.*(決議|結果)|議決権行使結果", "AGM results"),
    (r"上場維持基準", "TSE listing criteria"),
    (r"資本コストや株価を意識|株価を意識した経営", "Plan on cost of capital and share price"),
    (r"中期経営計画", "Medium-term business plan"),
    (r"固定資産(の)?(譲渡|売却)", "Sale of fixed assets"),
    (r"投資有価証券(の)?売却", "Sale of investment securities"),
    (r"特別利益", "Extraordinary gain"),
    (r"特別損失|減損", "Extraordinary loss / impairment"),
    (r"月次|月度", "Monthly sales report"),
    (r"有価証券報告書", "Annual securities report"),
    (r"半期報告書", "Half-year securities report"),
    (r"臨時報告書", "Extraordinary report"),
    (r"大量保有報告書|変更報告書", "5%+ holder report"),
    (r"独立役員届出書", "Independent director notification"),
    (r"内部統制報告書", "Internal control report"),
    (r"確認書", "Confirmation letter"),
]


def title_en(ja: str) -> str:
    t = nfkc(ja or "")
    for pat, en in TITLE_EN:
        m = re.search(pat, t)
        if m:
            return en(m) if callable(en) else en
    return ""


def filing_cat(ja: str) -> str:
    t = nfkc(ja or "")
    if re.search(r"決算|業績予想|予想値|有価証券報告書|半期報告書|月次", t):
        return "results"
    if re.search(r"配当", t):
        return "dividend"
    if re.search(r"自己株式|自社株", t):
        return "buyback"
    if re.search(r"株主|大量保有|変更報告書", t):
        return "shareholders"
    if re.search(r"ガバナンス|役員|取締役|総会|招集", t):
        return "governance"
    if re.search(r"公開買付|合併|株式交換|分割|譲渡|取得|提携|上場", t):
        return "corporate"
    return "filing"
