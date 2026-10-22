from jpscreen.classify import classify

CASES = [
    ("株式会社カチタスによる当社株券に対する公開買付けに関する賛同の意見表明及び応募推奨のお知らせ", "32970", "tender_offer", "target"),
    ("株式会社東武住販（証券コード：3297）の株式に対する公開買付けの開始に関するお知らせ", "89190", "tender_offer", "acquirer"),
    ("当社親会社であるヒューリック株式会社による当社株券等に対する公開買付けに関する賛同の意見表明及び応募推奨のお知らせ", "47140", "tender_offer", "target"),
    ("マネジメント・バイアウト（MBO）の実施及び応募の推奨に関するお知らせ", "12340", None, None),
    ("自己株式の公開買付けの開始に関するお知らせ", "12340", "self_tender", "self"),
    ("株式会社BCJ-110による当社株式に対する公開買付けの結果並びに親会社及び主要株主である筆頭株主の異動に関するお知らせ", "44200", None, None),
    ("一部報道について", "87500", "rumor", "self"),
    ("独立社外取締役を主体とした「ストラテジック・レビュー」の実施に関するお知らせ", "77520", "strategic", "self"),
    ("公開買付けに準ずる行為として政令で定める買集め行為に関するお知らせ", "78270", "holding", "self"),
    ("上場維持基準（流通株式比率、純資産基準）への適合に向けた計画に基づく進捗について", "60850", "listing", "self"),
    ("自己株式取得に係る事項の決定に関するお知らせ", "59900", "buyback", "self"),
    ("自己株式の消却に関するお知らせ", "61010", "cancellation", "self"),
    ("固定資産の譲渡及び特別利益（固定資産売却益）の計上に関するお知らせ", "71220", "asset_sale", "self"),
    ("会社解散に関するお知らせ", "12340", "liquidation", "self"),
    ("連結子会社の解散に関するお知らせ", "12340", None, None),
    ("2027年３月期第２四半期（中間期）及び通期連結業績予想の修正に関するお知らせ", "80510", "guidance", "self"),
    ("株主提案に対する当社取締役会の意見に関するお知らせ", "99780", "proxy_fight", "self"),
    ("継続企業の前提に関する注記についてのお知らせ", "12340", "distress", "self"),
    ("第三者割当による新株式発行に関するお知らせ", "12340", "dilution", "self"),
    ("連結子会社からの配当金受領に関するお知らせ", "12340", None, None),
    ("公認会計士等の合併に伴う異動に関するお知らせ", "12340", None, None),
]


def test_titles():
    for title, code, kind, role in CASES:
        tag = classify(title, code)
        if kind is None:
            # the MBO line has no 公開買付 wording: routine-looking titles must not invent a tender
            assert tag is None or tag.kind not in ("tender_offer",), (title, tag)
            continue
        assert tag is not None, title
        assert tag.kind == kind, (title, tag.kind)
        if role:
            assert tag.role == role, (title, tag.role)


def test_acquirer_side_attaches_to_target():
    tag = classify("株式会社リソー教育グループ（証券コード：4714）の株券等に対する公開買付けの開始に関するお知らせ", "30030")
    assert tag.target_code == "4714"


def test_parent_and_board_flags():
    tag = classify("当社親会社であるヒューリック株式会社による当社株券等に対する公開買付けに関する賛同の意見表明及び応募推奨のお知らせ", "47140")
    assert tag.flags.get("parent") and tag.flags.get("board_view") == "support"


def test_routine_defence_policy_is_not_a_fight():
    assert classify("当社株式の大規模買付行為に関する対応方針(買収への対応方針)の継続について", "65130") is None
    assert classify("当社株式の大規模買付行為に関する対応方針(買収への対応方針)における 特別委員会の委員選任に関するお知らせ", "96330") is None
    tag = classify("大規模買付者に対する対抗措置の発動（新株予約権無償割当て）に関するお知らせ", "12340")
    assert tag is not None and tag.flags.get("defense")
