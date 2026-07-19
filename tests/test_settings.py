"""後台設定（[[編輯台後台設定]]）：一份 settings.json，三個地方遵守。

釘死的事：
1. 沒有設定檔照樣能跑（內建預設）；未知鍵不炸（新舊版互不傷害）
2. 亂寫的值存不進去，錯誤訊息講得出哪個欄位
3. **schema 跟著設定走**：把卡數上限改成 4，第 5 張卡就被擋——而且不用重啟（快取失效）
4. **prompt 跟著設定走**：目標字數改了，組出來的 prompt 就是新數字
5. **llm 跟著設定走**，但環境變數永遠可以蓋過（除錯的手動排檔）
6. **設定檔是產物的輸入**：改了設定，analyze 就重跑
"""

from __future__ import annotations

import copy
import json
import time

import pytest

from src import settings
from src.errors import PipelineError
from src.schema import validate

ARTICLE = {
    "schema_version": "2.0", "generated_at": "2026-07-15T00:00:00+08:00",
    "origin": "article", "language": "zh",
    "source": {"slug": "t", "title": "T", "url": "https://example.com/t"},
    "paragraphs": [
        {"index": 0, "text": "這是一段測試素材。"},
        {"index": 1, "text": "這是第二段測試素材。"},
        {"index": 2, "text": "這是第三段測試素材。"},
    ],
    "body": "這是一段測試素材。\n\n這是第二段測試素材。\n\n這是第三段測試素材。",
}


def _card(i: int) -> dict:
    return {"type": "point", "title": f"卡{i}", "body": "內容。",
            "evidence": [{"para_index": 0, "source_text": "這是一段測試素材"}]}


def _hl(n_cards: int) -> dict:
    return {
        "schema_version": "3.1", "generated_at": "2026-07-15T00:00:00+08:00",
        "source": ARTICLE["source"], "summary": ["一", "二", "三"],
        "posts": [{"angle": "論點", "cards": [_card(i) for i in range(n_cards)],
                   "hashtags": ["#a", "#b", "#c"]}],
    }


@pytest.fixture(autouse=True)
def _isolated_settings(tmp_path, monkeypatch):
    """每條測試自己的設定檔——不弄髒真的 settings.json。"""
    monkeypatch.setenv("SETTINGS_FILE", str(tmp_path / "settings.json"))
    yield


def test_defaults_without_file() -> None:
    assert settings.load() == settings.DEFAULTS
    assert settings.gen("cards_max") == 18   # 輪播 20 − 封面 − 出處卡（v3.7）


def test_unknown_keys_are_ignored() -> None:
    settings.path().write_text(
        json.dumps({"generation": {"quote_max": 35, "no_such_knob": 99}, "junk": 1}),
        encoding="utf-8")
    loaded = settings.load()
    assert loaded["generation"]["quote_max"] == 35
    assert "no_such_knob" not in loaded["generation"]


def test_bad_values_are_rejected_with_named_fields() -> None:
    with pytest.raises(PipelineError) as e:
        settings.save({"generation": {"posts_max": 0}})       # 至少要產出一則
    assert "posts_max" in e.value.message
    with pytest.raises(PipelineError) as e:
        settings.save({"generation": {"point_body_target": 500, "point_body_max": 100}})
    assert "point_body_target" in e.value.message              # 目標 > 上限
    with pytest.raises(PipelineError) as e:
        settings.save({"llm": {"provider": "openai"}})
    assert "provider" in e.value.message


def test_schema_follows_settings_without_restart() -> None:
    """把金句上限改小 → 原本合法的金句被擋。改回去 → 又過。**中間沒有重啟。**"""
    h = _hl(3)
    h["posts"][0]["cards"].append({"type": "quote", "text": "一句十二個字的金句啊啊啊",
                                   "verbatim": False,
                                   "evidence": [{"para_index": 0, "source_text": "這是一段測試素材"}]})
    validate("highlights", copy.deepcopy(h))                   # 預設上限 40：合法

    settings.save({"generation": {"quote_max": 10}})
    with pytest.raises(PipelineError) as e:
        validate("highlights", copy.deepcopy(h))
    assert "quote" in e.value.message or "text" in e.value.message

    settings.save({"generation": {"quote_max": 40}})
    validate("highlights", copy.deepcopy(h))                   # 快取確實失效重建


def test_platform_derived_cards_max_ignores_stale_files() -> None:
    """cards_max 是平台推導值：早期存進檔案的 6 不准蓋掉現在的 18。"""
    settings.path().write_text('{"generation": {"cards_max": 6}}', encoding="utf-8")
    assert settings.gen("cards_max") == 18


def test_prompt_follows_settings() -> None:
    from src.analyze.extract_highlights import build_prompt
    settings.save({"generation": {"steps_step_target": 33, "cards_target": "3"}})
    prompt = build_prompt(ARTICLE)
    assert "目標 33 字" in prompt
    assert "目標 3 張" in prompt and "2–18 張" in prompt        # cards_max 是平台值，鎖 18
    assert "{steps_step_target}" not in prompt                 # 變數要全部填掉


def test_llm_follows_settings_but_env_wins(monkeypatch) -> None:
    from src import llm
    monkeypatch.delenv("GEMINI_MODEL", raising=False)
    monkeypatch.delenv("LLM_PROVIDER", raising=False)
    settings.save({"llm": {"gemini_model": "gemini-from-settings"}})
    assert llm.current_model() == "gemini-from-settings"
    monkeypatch.setenv("GEMINI_MODEL", "gemini-from-env")      # 環境變數＝手動排檔，永遠優先
    assert llm.current_model() == "gemini-from-env"


def test_settings_file_counts_as_input_for_analyze(tmp_path, monkeypatch) -> None:
    """改了設定 → 舊 highlights 過期 → extract 重跑（不是「檔案存在就跳過」）。"""
    monkeypatch.setenv("OUT_DIR", str(tmp_path / "out"))
    from src.analyze.extract_highlights import extract
    from src.paths import article_path, highlights_path

    ap = article_path("t"); ap.parent.mkdir(parents=True, exist_ok=True)
    ap.write_text(json.dumps(ARTICLE, ensure_ascii=False), encoding="utf-8")
    hp = highlights_path("t")
    hp.write_text(json.dumps(_hl(3), ensure_ascii=False), encoding="utf-8")

    calls = []
    def llm_fn(prompt: str) -> str:
        calls.append(1)
        return json.dumps({"summary": ["一", "二", "三"],
                           "posts": _hl(3)["posts"]}, ensure_ascii=False)

    extract("t", llm=llm_fn)
    assert not calls, "產物比輸入新，不該重跑"

    time.sleep(0.01)
    settings.save({"generation": {"point_body_target": 90}})   # 動一下設定
    extract("t", llm=llm_fn)
    assert calls, "設定檔也是輸入——改了就該重跑"


def test_hook_target_follows_settings() -> None:
    from src.compose.write_post import _hook_target
    assert _hook_target() == 25
    settings.save({"generation": {"hook_target": 33}})
    assert _hook_target() == 33



# --- 2026-07-15 Human 要求全開：字數雜項／文案結構／版面字級／進階執行 ---

def test_misc_length_caps_follow_settings() -> None:
    """字數雜項也走 schema 覆寫：標題、hashtag 數量改了就生效。"""
    h = _hl(3)
    validate("highlights", copy.deepcopy(h))
    settings.save({"generation": {"title_max": 5}})
    bad = copy.deepcopy(h)
    bad["posts"][0]["cards"][0]["title"] = "超過五個字的標題"
    with pytest.raises(PipelineError):
        validate("highlights", bad)

    settings.save({"generation": {"title_max": 24, "hashtags_max": 3}})
    bad2 = copy.deepcopy(h)
    bad2["posts"][0]["hashtags"] = ["#a", "#b", "#c", "#d"]
    with pytest.raises(PipelineError):
        validate("highlights", bad2)


def test_caption_shape_follows_settings() -> None:
    """正文段落規格走設定：上限改成 2 段，3 段就被抓。"""
    from src.compose.write_post import check_body
    three_paras = "一段。\n\n二段。\n\n三段。"
    assert check_body(three_paras) == ""                      # 預設 2–4 段：合格
    settings.save({"caption": {"body_paras_max": 2}})
    assert "太碎" in check_body(three_paras)                  # 改成最多 2 段：被抓


def test_max_splits_follows_settings() -> None:
    """拆卡上限走設定：max_splits=1 時，兩張才裝得下的內容拆不成。"""
    from src.render.layout import plan
    card = {"type": "point", "title": "t", "body": "第一句。第二句。"}
    fits = lambda c: len(c.get("body", "")) <= 5              # 假量尺：一句一張才裝得下
    assert len(plan(dict(card), fits)) == 2                   # 預設可拆
    settings.save({"render": {"max_splits": 1}})
    with pytest.raises(PipelineError):                        # 只准 1 張＝拆不了 → 擋
        plan(dict(card), fits)


def test_strict_grounding_flag(monkeypatch) -> None:
    """嚴格模式：設定可開；環境變數永遠可蓋過。"""
    from src.analyze.extract_highlights import _strict
    monkeypatch.delenv("STRICT_GROUNDING", raising=False)
    assert _strict() is False
    settings.save({"advanced": {"strict_grounding": True}})
    assert _strict() is True
    settings.save({"advanced": {"strict_grounding": False}})
    monkeypatch.setenv("STRICT_GROUNDING", "1")
    assert _strict() is True
