"""螢光筆標記（`**重點**`）只屬於圖卡。

2026-07-15 Human 回報：`out/_sample/` 的範例卡片都有螢光重點，實際產出卻沒有——
根因是範例是手寫的，而 prompts/highlights.md 從來沒叫模型標重點。

修法分三層，這裡全部釘死：
1. 分析 prompt 教模型標（prompt 裡必須有這一章，別讓它被改掉）
2. 文案端剝乾淨：digest 與 clean() 都不准讓星號流進 caption（IG 不渲染 markdown）
3. 編輯台的建議：卡片文字保留標記、caption 建議剝掉
"""

from __future__ import annotations

from src.compose.write_post import _cards_digest, clean
from src.editor import suggest
from src.paths import PROMPT_DIR


def test_analyze_prompt_teaches_the_model_to_mark_keywords() -> None:
    """範例卡有螢光、實際產出沒有——因為從來沒人教模型。這一章不准消失。"""
    prompt = (PROMPT_DIR / "highlights.md").read_text(encoding="utf-8")
    assert "重點標記" in prompt and "**……**" in prompt


def test_digest_strips_marks_before_captioning() -> None:
    """餵給文案模型的卡片摘要不能帶星號——模型會有樣學樣寫進 caption。"""
    post = {"cards": [
        {"type": "point", "title": "標題", "body": "真正的分水嶺是**怎麼驗證產出**。"},
        {"type": "steps", "title": "步", "steps": [{"text": "先做**這件事**"}, {"text": "再做那件"}]},
        {"type": "contrast", "title": "對", "wrong": {"text": "**錯法**"}, "right": {"text": "**對法**"}},
        {"type": "quote", "text": "**一句話**"},
    ]}
    digest = _cards_digest(post)
    assert "**" not in digest
    assert "怎麼驗證產出" in digest  # 剝的是星號，不是字


def test_clean_strips_marks_by_default_but_cards_can_keep_them() -> None:
    s = "重點是**這四個字**。"
    assert clean(s) == "重點是這四個字。"            # caption：純文字
    assert clean(s, keep_marks=True) == s            # 圖卡：螢光筆語法保留


def test_card_suggestions_keep_marks_and_caption_suggestions_do_not() -> None:
    def card_llm(prompt: str) -> str:
        return '{"title": "標題", "body": "重點是**這裡**。"}'

    def caption_llm(prompt: str) -> str:
        return '{"hook": "重點是**這裡**？", "body": "第一段。\\n\\n第二段。", "hashtags": ["#a"]}'

    card = suggest("point", {"title": "t", "body": "b"}, "改", llm=card_llm)
    assert "**這裡**" in card["body"], "卡片建議的螢光筆標記不准被剝掉"

    cap = suggest("caption", {"hook": "h", "body": "b", "hashtags": []}, "改", llm=caption_llm)
    assert "**" not in cap["hook"], "caption 是純文字，星號會被讀者看到"
