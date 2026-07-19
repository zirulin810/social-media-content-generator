"""編輯台（[[發布前預覽介面]]）的三條紅線，全部在資料層釘死。

1. **文字是唯一的事實來源**：人存過檔（human_edited），analyze 連 --force 都不覆蓋
2. **人改過的卡，evidence 拿掉**：資料層不存著不成立的宣稱；金句改過就不再是 verbatim
3. **LLM 永遠不覆蓋人的編輯**：compose 看到 human_edited 的 post.json 只更新圖片清單

另外釘：出處開關「錯了也安全」——沒宣告原創（original）就必須有結尾卡。
"""

from __future__ import annotations

import copy
import json

import pytest

from src.editor import finalize_highlights, merge_post_edit
from src.errors import PipelineError
from src.schema import validate

CARD_POINT = {
    "type": "point", "title": "重點", "body": "說明。",
    "evidence": [{"para_index": 0, "source_text": "the original sentence"}],
}
CARD_STEPS = {
    "type": "steps", "title": "步驟",
    "steps": [
        {"text": "第一步", "evidence": [{"para_index": 0, "source_text": "step one here"}]},
        {"text": "第二步", "evidence": [{"para_index": 1, "source_text": "step two here"}]},
    ],
}
CARD_QUOTE = {
    "type": "quote", "text": "一句話", "verbatim": True,
    "evidence": [{"para_index": 0, "source_text": "a quoted sentence"}],
}

HL = {
    "schema_version": "3.1",
    "generated_at": "2026-07-15T00:00:00+08:00",
    "source": {"slug": "t", "title": "T", "url": "https://example.com/t"},
    "summary": ["一", "二", "三"],
    "posts": [{
        "angle": "論點",
        "hook": "副標",
        "cards": [copy.deepcopy(CARD_POINT), copy.deepcopy(CARD_STEPS), copy.deepcopy(CARD_QUOTE)],
        "hashtags": ["#a", "#b", "#c"],
    }],
}


# --- 紅線 2：人改過的卡，evidence 拿掉 ---

def test_edited_card_loses_its_evidence() -> None:
    """人改了字，「這句話出自第 N 段」就是不再成立的宣稱——不能留在資料層。"""
    h = copy.deepcopy(HL)
    h["posts"][0]["cards"][0]["edited"] = True
    out = finalize_highlights(h)
    assert "evidence" not in out["posts"][0]["cards"][0]
    validate("highlights", out)  # 拿掉之後仍然合約（edited 卡不要求 evidence）


def test_untouched_card_keeps_evidence() -> None:
    """沒動過的卡不受牽連——那些宣稱仍然成立。"""
    h = copy.deepcopy(HL)
    h["posts"][0]["cards"][0]["edited"] = True
    out = finalize_highlights(h)
    assert "evidence" in out["posts"][0]["cards"][1]["steps"][0]


def test_edited_steps_lose_per_step_evidence() -> None:
    h = copy.deepcopy(HL)
    h["posts"][0]["cards"][1]["edited"] = True
    out = finalize_highlights(h)
    for step in out["posts"][0]["cards"][1]["steps"]:
        assert "evidence" not in step
    validate("highlights", out)


def test_edited_quote_is_no_longer_verbatim() -> None:
    """人改過的句子不再保證是作者的原話——資料層不能繼續宣稱「逐字」。"""
    h = copy.deepcopy(HL)
    h["posts"][0]["cards"][2]["edited"] = True
    out = finalize_highlights(h)
    assert out["posts"][0]["cards"][2]["verbatim"] is False


def test_saving_marks_the_whole_file_human_edited() -> None:
    out = finalize_highlights(copy.deepcopy(HL))
    assert out["human_edited"] is True


# --- schema：模型產的卡仍然必帶 evidence（編輯台不是後門） ---

def test_schema_still_requires_evidence_for_model_cards() -> None:
    h = copy.deepcopy(HL)
    del h["posts"][0]["cards"][0]["evidence"]  # 沒標 edited 就拿掉 evidence
    with pytest.raises(PipelineError):
        validate("highlights", h)


# --- 紅線 1：human_edited 之後 analyze 不覆蓋（連 --force 都不行） ---

def test_analyze_never_overwrites_human_edits(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("OUT_DIR", str(tmp_path))
    from src.analyze.extract_highlights import extract
    from src.paths import highlights_path

    edited = copy.deepcopy(HL)
    edited["human_edited"] = True
    edited["posts"][0]["angle"] = "人改過的標題"
    path = highlights_path("t")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(edited, ensure_ascii=False), encoding="utf-8")

    def bomb(prompt: str) -> str:  # 只要碰模型就是覆蓋——直接炸
        raise AssertionError("human_edited 的檔案不准重新分析")

    out = extract("t", force=True, llm=bomb)  # force 也不准
    kept = json.loads(out.read_text(encoding="utf-8"))
    assert kept["posts"][0]["angle"] == "人改過的標題"


# --- 紅線 3：LLM 永遠不覆蓋人的 caption ---

def _png(path, w=1080, h=1080):
    import struct, zlib
    sig = b"\x89PNG\r\n\x1a\n"
    ihdr = struct.pack(">II5B", w, h, 8, 2, 0, 0, 0)
    def chunk(tag, data):
        c = struct.pack(">I", len(data)) + tag + data
        return c + struct.pack(">I", zlib.crc32(tag + data) & 0xFFFFFFFF)
    path.write_bytes(sig + chunk(b"IHDR", ihdr) + chunk(b"IEND", b""))


def test_compose_keeps_human_caption_and_only_refreshes_images(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("OUT_DIR", str(tmp_path))
    from src.compose.write_post import compose_post
    from src.paths import highlights_path, images_dir, post_path

    h = copy.deepcopy(HL)
    hp = highlights_path("t"); hp.parent.mkdir(parents=True, exist_ok=True)
    hp.write_text(json.dumps(h, ensure_ascii=False), encoding="utf-8")

    d = images_dir("t", 1); d.mkdir(parents=True, exist_ok=True)
    _png(d / "01_cover.png"); _png(d / "02_point_1.png"); _png(d / "99_outro.png")

    old = {
        "schema_version": "3.0", "generated_at": "2026-07-15T00:00:00+08:00",
        "source": h["source"], "post_index": 1, "angle": "論點",
        "human_edited": True,
        "images": [{"path": "images/01_cover.png", "role": "cover", "ratio": "1:1"}],
        "posts": [
            {"platform": "instagram", "caption": "人親手改過的文案", "image_paths": ["images/01_cover.png"], "attribution": "原文：T\nhttps://example.com/t"},
            {"platform": "threads", "caption": "人親手改過的文案", "image_paths": ["images/01_cover.png"], "attribution": "原文：T\nhttps://example.com/t"},
        ],
    }
    pp = post_path("t", 1); pp.parent.mkdir(parents=True, exist_ok=True)
    pp.write_text(json.dumps(old, ensure_ascii=False), encoding="utf-8")

    def bomb(prompt: str) -> str:
        raise AssertionError("human_edited 的文案不准叫 LLM 重寫")

    compose_post("t", 1, llm=bomb, force=True)
    kept = json.loads(pp.read_text(encoding="utf-8"))
    assert kept["posts"][0]["caption"] == "人親手改過的文案"          # 字，一個都沒動
    assert len(kept["images"]) == 3                                    # 圖片清單有更新
    assert "images/99_outro.png" in kept["posts"][0]["image_paths"]


# --- 出處開關：預設要選「錯了也安全」的那一邊 ---

def test_missing_outro_is_blocked_unless_declared_original(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("OUT_DIR", str(tmp_path))
    from src.compose.write_post import collect_images
    from src.paths import images_dir

    d = images_dir("t", 1); d.mkdir(parents=True, exist_ok=True)
    _png(d / "01_cover.png"); _png(d / "02_point_1.png")  # 沒有 outro

    with pytest.raises(PipelineError):
        collect_images("t", 1)                     # 預設：擋
    assert collect_images("t", 1, original=True)   # 人已明確宣告原創：放行


def test_render_skips_outro_only_for_declared_original() -> None:
    """出圖端也要遵守：original 才略過結尾卡（用原始碼釘住，不開瀏覽器）。"""
    from pathlib import Path
    src = (Path(__file__).parent.parent / "src/render/render_cards.py").read_text(encoding="utf-8")
    assert 'get("original")' in src, "render_post 必須依 post.original 決定出不出結尾卡"


# --- caption 合併：只收文字欄位 ---

def test_merge_post_edit_touches_text_only() -> None:
    old = {
        "posts": [
            {"platform": "instagram", "caption": "舊", "image_paths": ["images/01_cover.png"], "attribution": "a", "hashtags": ["#x"]},
            {"platform": "threads", "caption": "舊", "image_paths": ["images/01_cover.png"], "attribution": "a"},
        ],
    }
    out = merge_post_edit(old, {"posts": [
        {"platform": "instagram", "caption": "新的字", "hashtags": ["tag", "#ok"]},
        {"platform": "threads", "caption": "新的字", "image_paths": ["images/evil.png"]},
    ]})
    assert out["human_edited"] is True
    assert out["posts"][0]["caption"] == "新的字"
    assert out["posts"][0]["hashtags"] == ["#tag", "#ok"]      # 自動補 #
    assert out["posts"][1]["image_paths"] == ["images/01_cover.png"]  # 圖片清單是程式的事，人改不到



# --- 刪除整則之後，編號對不上的 post.json 一律作廢 ---

def test_deleting_a_post_invalidates_shifted_captions(tmp_path, monkeypatch) -> None:
    """刪掉第 2 則後，舊的 p2/p3 post.json 若留著，human_edited 防線反而會保住
    一份張冠李戴的文案——所以從刪除點起全部作廢（圖檔不動，重出圖會對齊）。"""
    monkeypatch.setenv("OUT_DIR", str(tmp_path))
    from src.editor import invalidate_posts_from
    from src.paths import post_path

    for i in (1, 2, 3):
        pp = post_path("t", i); pp.parent.mkdir(parents=True, exist_ok=True)
        pp.write_text("{}", encoding="utf-8")

    removed = invalidate_posts_from("t", 2)
    assert post_path("t", 1).exists()          # 第 1 則沒事
    assert not post_path("t", 2).exists()      # 遞補位作廢
    assert not post_path("t", 3).exists()      # 孤兒也作廢
    assert len(removed) == 2
