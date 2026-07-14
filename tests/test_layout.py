"""拆卡邏輯的測試。

**核心原則：塞不下就拆卡，不砍內容。**

這些測試用假的「量尺」（fits），不開瀏覽器——決策邏輯本來就不該綁在 Chromium 上。
"""

from __future__ import annotations

import pytest

from src.errors import ErrorCode, PipelineError
from src.render.layout import plan, plan_all


def budget(max_units: int):
    """假的量尺：一張卡的「內容量」不超過 max_units 就塞得下。

    步驟卡 = 步數；重點卡 = 說明字數 / 20；其他 = 文字長度 / 20。
    """
    def fits(card):
        if card["type"] == "steps":
            return len(card["steps"]) <= max_units
        if card["type"] == "point":
            return len(card.get("body", "")) / 20 <= max_units
        text = card.get("text") or card.get("angle") or ""
        return len(text) / 20 <= max_units
    return fits


STEPS5 = {
    "type": "steps",
    "title": "讓 AI 讀懂你的筆記庫",
    "steps": [{"text": f"第 {i} 步"} for i in range(1, 6)],
}


def test_card_that_fits_is_left_alone() -> None:
    assert plan(STEPS5, budget(10)) == [STEPS5]


def test_five_steps_split_into_two_cards_not_truncated() -> None:
    """5 步塞不下 → 拆成兩張，**不是砍成 3 步**。

    砍掉第 4、5 步會讓讀者照著做卻失敗——那才是真正的本末倒置。
    """
    cards = plan(STEPS5, budget(3))
    assert len(cards) == 2
    # 每一步都還在
    total = [s["text"] for c in cards for s in c["steps"]]
    assert total == [f"第 {i} 步" for i in range(1, 6)]


def test_split_steps_keep_continuous_numbering() -> None:
    """第二張要從第 4 步接下去，不能又從 1 開始。"""
    cards = plan(STEPS5, budget(3))
    assert cards[0]["startIndex"] == 1
    assert cards[1]["startIndex"] == 4


def test_split_cards_get_a_pager() -> None:
    """讀者要知道還有下一張。"""
    cards = plan(STEPS5, budget(3))
    assert cards[0]["pager"] == "1 / 2"
    assert cards[1]["pager"] == "2 / 2"
    assert "續" in cards[1]["kicker"]


def test_long_point_splits_at_sentence_boundary() -> None:
    """重點卡從句號切，不從字數切——切在句子中間就是把話講一半。

    量尺只給 20 字，三句各 11/13/10 字 → 貪婪裝箱裝不下任何兩句，所以拆成 3 張。
    張數不是重點，**一個字都沒少**才是。
    """
    card = {
        "type": "point",
        "title": "為什麼要開自動更新",
        "body": "開啟自動更新內部連結。重新命名時連結會跟著更新。這個設定預設是關的。",
    }
    cards = plan(card, budget(1))
    assert len(cards) >= 2
    assert "".join(c["body"] for c in cards) == card["body"]   # 一個字都沒少
    for c in cards:
        assert c["body"].endswith(("。", "！", "？"))            # 每張都切在句子邊界
    assert cards[0]["pager"] == f"1 / {len(cards)}"
    assert "續" in cards[-1]["kicker"]


def test_point_packs_greedily_rather_than_one_sentence_per_card() -> None:
    """量尺放寬時，兩句該擠在同一張，不要浪費版面。"""
    card = {
        "type": "point",
        "title": "t",
        "body": "第一句話。第二句話。第三句話。第四句話。第五句話。第六句話。",
    }
    cards = plan(card, budget(1))       # 20 字一張，每句 5 字 → 每張 4 句
    assert len(cards) == 2
    assert "".join(c["body"] for c in cards) == card["body"]


def test_unsplittable_card_raises_instead_of_shrinking_forever() -> None:
    """對照卡與金句卡的結構不可分割。

    塞不下就該回頭改文案，**不是把字縮到看不見**，也不是默默截掉半個字送出去。
    """
    quote = {"type": "quote", "text": "這是一句非常非常長的金句" * 10}
    with pytest.raises(PipelineError) as e:
        plan(quote, budget(1))
    assert e.value.code == ErrorCode.RENDER_OVERFLOW
    assert "拆不開" in e.value.message


def test_single_step_too_long_cannot_be_split() -> None:
    """只有一步、而那一步太長 —— 拆不動，要回頭改內容。"""
    card = {"type": "steps", "title": "x", "steps": [{"text": "很長" * 100}]}
    with pytest.raises(PipelineError):
        plan(card, budget(0))


def test_plan_all_expands_the_whole_deck() -> None:
    deck = [
        {"type": "point", "title": "短", "body": "短短"},
        STEPS5,
    ]
    out = plan_all(deck, budget(3))
    assert len(out) == 3          # 1 張 point + 2 張拆開的 steps
    assert out[0]["type"] == "point"
    assert [c["type"] for c in out[1:]] == ["steps", "steps"]


def test_stale_pager_from_upstream_is_stripped() -> None:
    """`pager` / `startIndex` 是**渲染器擁有的**欄位，不接受上游帶進來。

    上游偷偷塞一個 pager 進來，就會印出一個假的「2 / 2」——而且不會有人發現。
    這是實際跑 sample 時真的踩到的。
    """
    card = {"type": "point", "title": "t", "body": "短", "pager": "2 / 2", "startIndex": 7}
    out = plan(card, budget(10))
    assert "pager" not in out[0]
    assert "startIndex" not in out[0]
