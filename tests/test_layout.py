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


# --- 2026-07-14：Human 回報「它還是沒拆，只是塞在同一頁」---

def test_split_triggers_on_comfort_not_on_hard_limit() -> None:
    """**「塞得下」和「讀得下去」是兩件事。**

    第一版的量尺是「縮到 34px 還塞不塞得下」——結果 5 步的卡剛好塞得下，
    於是不拆，變成一面文字牆。技術上讀得到，實際上沒人會讀。

    正解：量尺要問的是「字級有沒有掉到舒適下限以下」，不是「有沒有爆版」。
    這條測試把那個教訓釘死：**一張「塞得下但很擠」的卡，必須被拆。**
    """
    crowded = {"type": "steps", "title": "t", "steps": [{"text": f"第 {i} 步"} for i in range(1, 6)]}

    # 舊的量尺：只問「爆版了沒」→ 沒爆 → 不拆 → 文字牆
    fits_if_not_overflowing = lambda c: True          # noqa: E731
    assert len(plan(crowded, fits_if_not_overflowing)) == 1

    # 新的量尺：問「讀得舒服嗎」→ 5 步太擠 → 拆
    comfortable = lambda c: len(c["steps"]) <= 3      # noqa: E731
    assert len(plan(crowded, comfortable)) == 2


def test_split_chunks_are_balanced() -> None:
    """6 步、每張最多 4 步 → [3, 3]，不是 [4, 2]。

    最後一張只剩一步的卡，看起來就像做壞了。
    """
    from src.render.layout import _balanced

    assert [len(c) for c in _balanced(list(range(6)), 4)] == [3, 3]
    assert [len(c) for c in _balanced(list(range(5)), 3)] == [3, 2]
    assert [len(c) for c in _balanced(list(range(7)), 3)] == [3, 2, 2]


def test_no_orphan_single_step_card_when_avoidable() -> None:
    """5 步、每張最多 2 步 → [2, 2, 1] 無法避免；但 [2, 3] 更好時要選 [2, 3]。"""
    card = {"type": "steps", "title": "t", "steps": [{"text": f"第 {i} 步"} for i in range(1, 6)]}
    cards = plan(card, lambda c: len(c["steps"]) <= 3)
    assert [len(c["steps"]) for c in cards] == [3, 2]   # 不是 [3, 1, 1]


# ---------------------------------------------------------------------------
# 測試「測試」本身：實機腳本必須掃契約的邊界，不是掃我隨手想到的數字
#
# 2026-07-14：scripts/test_split.py 掃 2–6 步 × 30 字，但 schema 只准 2–4 步 × 60 字。
# 於是它測了 pipeline 永遠產不出來的輸入（5、6 步），
# 卻從沒測過它真的會產出的最壞情況（4 步 × 60 字）——**拆卡一次都沒被執行，測試卻是綠的。**
# ---------------------------------------------------------------------------

import json

from src.paths import PROJECT_ROOT, SCHEMA_DIR


def _limits() -> dict:
    s = json.loads((SCHEMA_DIR / "highlights.schema.json").read_text(encoding="utf-8"))
    steps = s["$defs"]["stepsCard"]["properties"]["steps"]
    return {
        "steps_max": steps["maxItems"],
        "step_chars": steps["items"]["properties"]["text"]["maxLength"],
        "point_chars": s["$defs"]["pointCard"]["properties"]["body"]["maxLength"],
    }


def test_split_script_reads_its_limits_from_the_schema() -> None:
    """實機腳本不准把上限抄成常數——抄一份就多一個會跟契約走散的地方。"""
    src = (PROJECT_ROOT / "scripts" / "test_split.py").read_text(encoding="utf-8")
    assert "highlights.schema.json" in src, "測試範圍必須讀自 schema，不是寫死的數字"
    assert "maxItems" in src and "maxLength" in src


def test_split_script_refuses_to_pass_vacuously() -> None:
    """一支從不執行受測程式碼的測試，全綠也證明不了任何事。

    上一版就是這樣過的：五列全部「保留單張」，拆卡那條路一次都沒跑到。
    所以腳本裡必須有一道「這輪到底有沒有拆到卡」的檢查。
    """
    src = (PROJECT_ROOT / "scripts" / "test_split.py").read_text(encoding="utf-8")
    assert "split_seen" in src, "腳本必須檢查拆卡路徑是否真的被執行過"
    assert "vacuous" in src



def test_schema_limits_are_physical_not_editorial() -> None:
    """schema 的上限必須訂在「版面印不出來」的地方，不是「我希望它寫多短」。

    2026-07-14：`steps` 每步上限訂 50（依據是我自己寫的一句 44 字範例），
    模型把同一件事寫成 52 字 → **兩篇文章整份被丟掉，各燒 3 次 LLM 呼叫，死在兩個字上**。
    而版面實際吃得下 129 字（`calibrate.py` 實測）。

    編輯偏好屬於 prompt（目標值），密度屬於渲染器（自動拆卡），schema 只管物理極限。
    這條測試釘住那條線：上限不准回頭訂到「編輯目標」的高度。
    """
    lim = _limits()
    assert lim["step_chars"] >= 90, (
        f"steps 每步上限 {lim['step_chars']} 太緊——那是編輯偏好不是物理極限。"
        "實測版面吃得下 129 字；訂太緊只會讓合理的內容被整份丟掉"
    )
    assert lim["point_chars"] >= 150
    s = json.loads((SCHEMA_DIR / "highlights.schema.json").read_text(encoding="utf-8"))
    assert s["$defs"]["contrastSide"]["properties"]["text"]["maxLength"] >= 100


# ---------------------------------------------------------------------------
# 產物新鮮度：**跳過的條件是「圖是新的」，不是「有圖」**
# ---------------------------------------------------------------------------

def test_render_reruns_when_the_source_is_newer() -> None:
    """簡繁轉換上線後重跑分析，圖卡卻還是簡體——**因為渲染器看到有 PNG 就跳過了。**

    產物過期而不自知，比沒有產物更危險：你會拿著一份「看起來已經更新」的東西去發文。
    """
    src = (PROJECT_ROOT / "src" / "render" / "render_cards.py").read_text(encoding="utf-8")
    assert "is_stale" in src, "跳過與否必須比對時間，不能只看檔案存不存在"
    assert "TEMPLATE_DIR" in src, "版型也是輸入——改了 card.css 就該重出圖"


def test_the_code_itself_counts_as_an_input() -> None:
    """**程式碼也是輸入。**

    2026-07-14：`is_stale` 只比對資料與 prompt。於是我改了 `write_post.py` 的 hook 邏輯，
    重跑文案 → 它說「沿用既有文案（輸入沒變）」。

    Human：「看起來你的新舊偵測壞了。」——那一次其實是對的（我只改了顯示），
    **但漏洞是真的**：改了產生邏輯，產物就過期了，而偵測完全看不到。

    你會拿到一份「**用舊邏輯生成、看起來很新**」的東西——那是最難發現的一種壞掉。
    """
    for mod in ("src/compose/write_post.py", "src/render/render_cards.py"):
        src = (PROJECT_ROOT / mod).read_text(encoding="utf-8")
        assert "Path(__file__).parent" in src, f"{mod} 沒把自己算進 is_stale 的輸入"


def test_staleness_is_measured_against_every_input() -> None:
    """**產物該不該重做，要跟它的每一個輸入比時間。**

    2026-07-14 連續踩到兩次：
      1. 重跑分析 → 圖卡還是簡體（渲染器看到有 PNG 就跳過）
      2. 改了 prompt → 重跑文案 → 秒回，印的是上一輪的舊文案

    第二次尤其陰險：**我改的是 prompt，不是上游的資料。**
    只比對「上一階段的產物」抓不到——**prompt 和版型也是輸入。**
    """
    import time

    from src.paths import is_stale

    tmp = PROJECT_ROOT / "out" / "_stale_probe"
    tmp.mkdir(parents=True, exist_ok=True)
    product, upstream, prompt = tmp / "p.json", tmp / "u.json", tmp / "prompt.md"
    try:
        upstream.write_text("u", encoding="utf-8")
        prompt.write_text("v1", encoding="utf-8")
        time.sleep(0.01)
        product.write_text("p", encoding="utf-8")
        assert not is_stale(product, upstream, prompt), "產物比輸入新，不該重做"

        time.sleep(0.01)
        prompt.write_text("v2", encoding="utf-8")  # 只動 prompt，上游資料沒變
        assert is_stale(product, upstream, prompt), "改了 prompt 就該重做——它也是輸入"
    finally:
        for f in (product, upstream, prompt):
            f.unlink(missing_ok=True)
        tmp.rmdir()


def test_render_clears_stale_images_before_redrawing() -> None:
    """卡片從 9 張變 5 張 → 舊的第 6~9 張會變成孤兒，而且很可能被一起發出去。"""
    src = (PROJECT_ROOT / "src" / "render" / "render_cards.py").read_text(encoding="utf-8")
    assert "unlink()" in src, "重出之前必須清空舊圖"
