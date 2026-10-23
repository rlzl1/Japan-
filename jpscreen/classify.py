"""Japanese timely-disclosure taxonomy, mirroring the Korea Filing Radar's categories.

Every TDnet title is mapped to a catalyst kind (a document is read and sized) or a warning kind (name only).
Groups and categories keep the Korean ids so both radars read the same way:

  special  : tender, sale_process, control, mna, spinoff, activist, proxy, lawsuit, liquidation,
             capital_return, backdoor, listing (Japan: TSE listing-criteria pressure)
  returns  : buyback, cancellation, dividend, valueup, insider
  business : contract, guidance (Japan: forecast revisions)
  assets   : asset_sale (land / buildings / cross-shareholdings / businesses sold, gains booked)
  warning  : w_distress, w_dilution, w_other
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Optional

from .util import nfkc, norm_code


def squash(s: str) -> str:
    """NFKC + drop whitespace and decorative punctuation, like the Korean `norm`."""
    return re.sub(r"[\s　・･\.\[\]「」『』【】]", "", nfkc(s or ""))


CATEGORIES = [
    ("tender", "Tender offers & going-private", "special"),
    ("sale_process", "Sale processes & strategic reviews", "special"),
    ("control", "Change of controlling shareholder", "special"),
    ("mna", "Mergers & share exchanges", "special"),
    ("spinoff", "Spin-offs & demergers", "special"),
    ("activist", "Activists & new 5% stakes", "special"),
    ("proxy", "Shareholder proposals & proxy fights", "special"),
    ("lawsuit", "Control lawsuits", "special"),
    ("liquidation", "Liquidations", "special"),
    ("capital_return", "Cash capital returns", "special"),
    ("backdoor", "Reverse mergers & backdoor listings", "special"),
    ("listing", "Listing-criteria pressure", "special"),
    ("buyback", "Buybacks", "returns"),
    ("cancellation", "Share cancellations", "returns"),
    ("dividend", "Dividends", "returns"),
    ("valueup", "Value-up & return plans", "returns"),
    ("insider", "Controlling shareholder buying", "returns"),
    ("contract", "Big orders", "business"),
    ("guidance", "Guidance raised", "business"),
    ("asset_sale", "Asset sales & hidden-asset gains", "assets"),
    ("w_distress", "Distress & delisting", "warning"),
    ("w_dilution", "Dilution", "warning"),
    ("w_other", "Withdrawn deals & other", "warning"),
]
CAT_LABEL = {c[0]: c[1] for c in CATEGORIES}
CAT_GROUP = {c[0]: c[2] for c in CATEGORIES}
GROUPS = [("special", "Special situations"), ("returns", "Shareholder returns"), ("business", "Business"),
          ("assets", "Assets"), ("warning", "Warnings")]

KIND_TO_CAT = {
    "tender_offer": "tender", "self_tender": "tender", "squeeze_out": "tender",
    "strategic": "sale_process", "rumor": "sale_process",
    "control_change": "control",
    "merger": "mna", "share_swap": "mna",
    "split": "spinoff",
    "holding": "activist",
    "proxy_fight": "proxy",
    "control_dispute": "lawsuit",
    "liquidation": "liquidation",
    "capital_return": "capital_return",
    "reverse_merger": "backdoor",
    "listing": "listing",
    "buyback": "buyback", "cancellation": "cancellation", "dividend": "dividend",
    "value_up": "valueup", "return_policy": "valueup", "insider": "insider",
    "contract": "contract", "guidance": "guidance", "asset_sale": "asset_sale",
}
SPECIAL_SITUATIONS = {k for k, c in KIND_TO_CAT.items() if CAT_GROUP[c] == "special"}

CATALYST_LABEL = {
    "tender_offer": "Tender offer", "self_tender": "Self-tender", "squeeze_out": "Squeeze-out",
    "strategic": "Sale / strategic review", "rumor": "Answer to press report",
    "control_change": "New controlling shareholder", "merger": "Merger / integration",
    "share_swap": "Share exchange", "split": "Demerger / spin-off", "holding": "New 5% holder",
    "proxy_fight": "Shareholder proposal", "control_dispute": "Control lawsuit", "liquidation": "Liquidation",
    "capital_return": "Cash capital return", "reverse_merger": "Reverse merger", "listing": "Listing criteria",
    "buyback": "Buyback", "cancellation": "Share cancellation", "dividend": "Dividend",
    "value_up": "Value-up plan", "return_policy": "Shareholder-return plan", "insider": "Controlling shareholder buying",
    "contract": "Big order", "guidance": "Forecast revision", "asset_sale": "Asset sale",
}

# ---------------------------------------------------------------- vocab

SKIP = re.compile(r"訂正|公認会計士|監査法人|会計監査人|資料の一部|英文|English|補足説明資料|説明会|動画|(?<!買付条件等の)(?<!条件の)変更登記|株主優待|決算短信|"
                  r"四半期報告書|半期報告書の提出|有価証券報告書の提出|招集通知|定時株主総会|独立役員|役員の異動|人事|組織変更|"
                  r"代表取締役の異動|月次|月度|売上(高)?速報|営業状況|ETF|投資法人|REIT|インフラファンド|上場投資信託")
RESULT = re.compile(r"結果|取得状況|取得の状況|取得終了|取得完了|買付けの終了|処分の完了|消却完了|消却の完了|払込完了|完了に関する")
SUBSIDIARY = re.compile(r"(連結)?子会社|孫会社|関係会社|持分法適用")
MBO = re.compile(r"MBO|マネジメント・?バイアウト")
TENDER = re.compile(r"公開買付")
SELF = re.compile(r"自己株式の公開買付|自己株式公開買付|自社株公開買付")
OWN_SHARES = re.compile(r"当社(株式|株券|普通株式|の株式|の株券|株)|当社に対する|当社を")


@dataclass
class Tag:
    kind: str                    # catalyst or warning kind
    signal: str                  # 'catalyst' | 'warning'
    label: str = ""
    target_code: Optional[str] = None     # code the event is about (may differ from the discloser)
    role: str = "self"           # target | acquirer | self
    flags: dict = field(default_factory=dict)

    @property
    def cat(self) -> str:
        if self.signal == "warning":
            return {"distress": "w_distress", "dilution": "w_dilution"}.get(self.kind, "w_other")
        return KIND_TO_CAT.get(self.kind, "valueup")

    @property
    def group(self) -> str:
        return CAT_GROUP[self.cat]


def codes_in(title: str) -> list[str]:
    return [norm_code(m.group(1)) for m in re.finditer(r"(?:証券)?コード\s*[:：]?\s*([0-9][0-9A-Z]{3})", nfkc(title))]


def classify(title: str, code: str) -> Optional[Tag]:
    """Map one TDnet title to a Tag, or None for routine/noise disclosures."""
    raw = nfkc(title)
    n = squash(raw)
    code = norm_code(code)

    w = classify_warning(n)
    if w:
        return w
    k = classify_catalyst(n, raw, code)
    return k


def classify_catalyst(n: str, raw: str, code: str) -> Optional[Tag]:
    if SKIP.search(n) and not TENDER.search(n):
        return None
    other = [c for c in codes_in(raw) if c != code]
    sub = bool(SUBSIDIARY.search(n))

    # -------- tender offers
    if TENDER.search(n):
        if "訂正" in n and not re.search(r"買付条件等の変更|買付価格の引上げ", n):
            return None
        if "準ずる行為" in n or "買集め" in n or "買い集め" in n:
            return Tag("holding", "catalyst", flags={"creeping": True})
        if re.search(r"撤回|中止|不成立|不応募", n) and "不応募契約" not in n:
            return None  # handled as a warning
        if RESULT.search(n) and not re.search(r"買付条件等の変更|開始", n):
            return None  # completion report: the opportunity is over
        flags = {}
        if MBO.search(n):
            flags["mbo"] = True
        if re.search(r"親会社|支配株主|その他の関係会社|主要株主である筆頭株主", n):
            flags["parent"] = True
        if re.search(r"買付条件等の変更|買付価格の引上げ|価格の変更", n):
            flags["terms_change"] = True
        if re.search(r"開始予定|実施予定|実施に向けた|開始時期|予定に関する", n):
            flags["planned"] = True
        if re.search(r"反対", n):
            flags["board_view"] = "oppose"
        elif re.search(r"賛同.*応募推奨|応募を推奨", n):
            flags["board_view"] = "support"
        elif re.search(r"応募中立|応募.*株主の判断に委ね|中立", n):
            flags["board_view"] = "neutral"
        if SELF.search(n):
            return Tag("self_tender", "catalyst", flags=flags)
        if code and code in codes_in(raw):
            return Tag("tender_offer", "catalyst", role="target", flags=flags)
        if other:
            # the bidder is announcing: attach the event to the listed target
            return Tag("tender_offer", "catalyst", target_code=other[0], role="acquirer", flags=flags)
        if OWN_SHARES.search(n) or "当社" in n:
            return Tag("tender_offer", "catalyst", role="target", flags=flags)
        if sub:
            return None  # a bid for an unlisted subsidiary
        return Tag("tender_offer", "catalyst", role="acquirer", flags=flags)

    if RESULT.search(n):
        return None

    # -------- standing takeover-defence policies: renewals and committee changes are routine, not a fight
    if re.search(r"買収への対応方針|大規模買付行為(等)?(に関する|への)対応(方針|策)|買収防衛策", n) and not re.search(
            r"対抗措置|無償割当|発動|大規模買付者|情報提供(の)?要請|株主意思|特定株主|買付者", n):
        return None

    # -------- squeeze-outs (after a successful bid)
    if re.search(r"株式等売渡請求|売渡請求|スクイーズアウト", n) or (
            "株式併合" in n and re.search(r"臨時株主総会|上場廃止|整理銘柄", n) and "単元" not in n):
        return Tag("squeeze_out", "catalyst")

    # -------- strategic reviews, press-report answers, special committees
    if re.search(r"一部報道|報道について|報道に関する|報道等について|憶測", n):
        return Tag("rumor", "catalyst")
    if re.search(r"特別委員会の設置|特別委員会設置|戦略的(選択肢|代替案|検討)|ストラテジック|非公開化(の)?検討|"
                 r"資本政策(の)?(見直し|検討)|経営統合(に関する|に向けた)?(基本合意|検討|協議)|"
                 r"買収提案|買収の提案|法的拘束力(を)?(有|持)しない(初期的)?(な)?提案|意向表明書|初期的提案|株式の売却(の)?検討", n):
        return Tag("strategic", "catalyst")

    # -------- reverse mergers
    if re.search(r"不適当な合併|実質的な存続性|実質的存続性|逆取得|テクニカル上場|裏口上場", n):
        return Tag("reverse_merger", "catalyst")

    # -------- liquidation (the listed company itself, not a subsidiary or fund)
    if re.search(r"解散|清算|残余財産", n) and not sub and not re.search(r"ファンド|組合|投資事業", n) and not re.search(
            r"(有限公司|Ltd|Inc|GmbH|LLC|Limited|Corporation|株式会社|合同会社|有限会社|Co)[^当]{0,40}(の)?(解散|清算)", n, re.I):
        return Tag("liquidation", "catalyst")

    # -------- control changes
    if re.search(r"親会社(及び|並びに|の)?.*異動|主要株主である筆頭株主の異動|筆頭株主の異動|その他の関係会社の異動|"
                 r"支配株主の異動|主要株主の異動", n) and "子会社の異動" not in n:
        return Tag("control_change", "catalyst")

    # -------- mergers / share exchanges / demergers
    if "株式交換" in n:
        if other:
            return Tag("share_swap", "catalyst", target_code=other[0], role="acquirer")
        if re.search(r"当社を.*完全子会社|当社の完全子会社化|による当社", n):
            return Tag("share_swap", "catalyst", role="target")
        if sub or "簡易" in n:
            return None  # mopping up an unlisted subsidiary
        return Tag("share_swap", "catalyst", role="acquirer")
    if re.search(r"合併|経営統合|共同持株会社|株式移転", n):
        if sub or "簡易" in n or "略式" in n:
            return None
        if other:
            return Tag("merger", "catalyst", target_code=other[0], role="acquirer")
        return Tag("merger", "catalyst")
    if re.search(r"スピンオフ|株式分配|会社分割|吸収分割|新設分割|分割準備会社|持株会社体制", n):
        if (re.search(r"簡易|略式", n) or sub) and "スピンオフ" not in n:
            return None
        return Tag("split", "catalyst", flags={
            "spinoff": bool(re.search(r"スピンオフ|株式分配|分割型", n)),
            "physical_split": bool(re.search(r"持株会社体制|分社|子会社(へ|に)(承継|移転)", n))})

    # -------- activists, proposals, lawsuits
    if re.search(r"株主提案|委任状勧誘|臨時株主総会(の)?招集(の)?請求|株主総会招集請求", n):
        return Tag("proxy_fight", "catalyst")
    if re.search(r"仮処分|差止|検査役", n) or (re.search(r"訴訟|提訴", n) and re.search(r"株主|総会|新株予約権|取締役", n)):
        return Tag("control_dispute", "catalyst")
    if re.search(r"大規模買付|大量取得行為|買収防衛|買収への対応方針|新株予約権(の)?無償割当", n):
        return Tag("holding", "catalyst", flags={"defense": True})
    if re.search(r"大量保有|株主からの(書簡|提案|要請)|書簡(の)?受領|公開書簡", n):
        return Tag("holding", "catalyst", flags={"selling": bool(re.search(r"売却|譲渡", n))})

    # -------- listing criteria
    if re.search(r"監理銘柄.*解除|指定(の)?解除", n) or (re.search(r"上場維持基準", n) and re.search(r"適合(したこと|及び|のお知らせ)", n)
                                                     and not re.search(r"不適合|適合しない", n)):
        return Tag("listing", "catalyst", flags={"resolved": True})   # the all-clear after an exchange review
    if re.search(r"上場維持基準", n):
        if re.search(r"不適合|適合しない|改善期間|に向けた計画|未達|に関する計画|進捗", n):
            return Tag("listing", "catalyst", flags={
                "breach": bool(re.search(r"不適合|改善期間|未達|に向けた計画|進捗", n)) and "撤回" not in n,
                "improvement_period": "改善期間" in n})
        return None
    if re.search(r"市場区分(の)?(変更|選択|再選択)|スタンダード市場への|上場廃止(の)?(申請|予定)", n):
        return Tag("listing", "catalyst", flags={"market_change": True, "upgrade": "プライム" in n,
                                                  "voluntary_delisting": "上場廃止" in n})

    # -------- capital returns
    if re.search(r"資本剰余金を(配当)?原資|払戻|有償減資", n):
        return Tag("capital_return", "catalyst")

    # -------- shareholder returns (a buyback that also cancels is read as a buyback with to_cancel)
    if re.search(r"自己株式(の)?取得|自己株式取得|自己株式の買付|自社株買い|立会外買付|ToSTNeT", n, re.I):
        return Tag("buyback", "catalyst", flags={"tostnet": bool(re.search(r"立会外|ToSTNeT", n, re.I)),
                                                  "to_cancel": bool(re.search(r"消却", n)),
                                                  "specific_holder": "特定の株主" in n})
    if re.search(r"自己株式(の)?消却|自社株(式)?消却", n):
        return Tag("cancellation", "catalyst")

    # -------- a large holder selling its stake (overhang, sometimes a prelude to a new owner)
    if re.search(r"(大株主|主要株主|筆頭株主|親会社)による当社株式(の)?(譲渡|売却|売出)", n):
        return Tag("control_change", "catalyst", flags={"selling": True})

    # -------- asset sales and gains (hidden assets crystallising)
    if re.search(r"固定資産(の)?(譲渡|売却)|土地(の)?(譲渡|売却)|不動産(の)?(譲渡|売却)|建物(の)?(譲渡|売却)|"
                 r"投資有価証券(の)?売却益|政策保有株式(の)?(売却|縮減)|譲渡益|売却益|事業(の)?譲渡|"
                 r"(子会社|関係会社)?株式(の)?譲渡", n) and not re.search(r"売却損|評価損|減損", n):
        return Tag("asset_sale", "catalyst")

    # -------- forecasts (before value-up/dividends: '業績予想及び配当予想の修正' is read for both)
    if re.search(r"業績予想(の)?修正|上方修正|予想値と(実績|決算)値との差異|通期業績予想|業績予想(及び|および|並びに)配当予想", n):
        return Tag("guidance", "catalyst")
    if re.search(r"資本コストや株価を意識|株価を意識した経営|株主還元方針|株主還元の(強化|拡充)|中期経営計画|"
                 r"配当方針|配当政策|DOE|累進配当|PBR|企業価値向上", n):
        return Tag("value_up", "catalyst")
    if re.search(r"配当|増配|復配", n) and not re.search(r"無配|減配|見送り|ETF|分配金", n) and not sub:
        return Tag("dividend", "catalyst", flags={
            "special": bool(re.search(r"特別配当|記念配当", n)), "initiation": bool(re.search(r"初配|復配|配当開始", n))})
    if re.search(r"特別利益", n) and re.search(r"有価証券|固定資産|不動産|土地", n) and not re.search(
            r"売却損|評価損|補助金|圧縮|保険金", n):
        return Tag("asset_sale", "catalyst")

    # -------- business
    if re.search(r"大型受注|大口受注|受注(に関する|のお知らせ)|受注獲得", n):
        return Tag("contract", "catalyst")
    return None


DISTRESS = [
    (r"民事再生", "Civil rehabilitation filing"),
    (r"会社更生", "Corporate reorganisation filing"),
    (r"破産", "Bankruptcy filing"),
    (r"事業再生ADR|私的整理", "Out-of-court restructuring"),
    (r"債務超過", "Negative equity (債務超過)"),
    (r"継続企業の前提", "Going-concern note"),
    (r"整理銘柄|上場廃止(の)?決定|上場廃止基準(に)?該当", "Delisting decided"),
    (r"監理銘柄", "Under exchange review (監理銘柄)"),
    (r"特別注意銘柄|改善報告書", "Special alert designation (特別注意銘柄)"),
    (r"不適正意見|意見不表明|限定付適正", "Audit opinion issue"),
    (r"提出(期限)?(の)?延長|延長承認", "Late filing"),
    (r"不適切(な)?(会計|取引)|不正(行為|会計)|第三者委員会|特別調査委員会|社内調査委員会", "Accounting irregularity / investigation"),
    (r"課徴金|業務停止|行政処分", "Regulatory sanction"),
    (r"期限の利益(の)?喪失|債務不履行|デフォルト", "Default"),
]
DILUTION = [
    (r"行使価額修正条項付|MSワラント", "Floating-price warrants (MS warrants)"),
    (r"第三者割当", "Third-party allotment"),
    (r"公募(による)?(新株式)?発行|新株式発行|募集株式の発行", "New share issue"),
    (r"新株予約権(の)?発行|新株予約権付社債|転換社債", "Warrants / convertibles"),
    (r"自己株式(の)?処分", "Treasury share sale"),
    (r"株式(の)?売出し|売出しに関する", "Secondary offering (overhang)"),
]


def classify_warning(n: str) -> Optional[Tag]:
    if re.search(r"訂正|英文", n):
        return None
    if re.search(r"(撤回|中止|白紙|解消|解除|終了)", n) and re.search(r"公開買付|合併|経営統合|株式交換|資本業務提携|買収", n):
        if not RESULT.search(n) or "撤回" in n:
            return Tag("withdrawn", "warning", label="Deal withdrawn or terminated")
    if re.search(r"子会社.*上場(承認|申請)|子会社の(新規)?上場", n):
        return Tag("double_listing", "warning", label="Subsidiary IPO (double listing)")
    if re.search(r"減配|無配", n) and "配当" in n:
        return Tag("dividend_cut", "warning", label="Dividend cut")
    for pat, label in DISTRESS:
        if re.search(pat, n) and not re.search(r"解消|解除|指定解除|該当しない|子会社", n):
            return Tag("distress", "warning", label=label)
    if re.search(r"ストックオプション|株式報酬|譲渡制限付株式|従業員持株|ESOP|業績連動", n):
        return None
    for pat, label in DILUTION:
        if re.search(pat, n) and not RESULT.search(n) and "払込完了" not in n:
            return Tag("dilution", "warning", label=label)
    return None


def category_label(cat: str) -> str:
    return CAT_LABEL.get(cat, cat)
