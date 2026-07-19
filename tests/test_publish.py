"""一鍵發布（[[一鍵發布到 IG 與 Threads]]）：方案一的紅線與資料層。

最重要的一條：**程式永遠不按「分享／發佈」。**
不是靠自律——所有點擊走 `_click()`，帶發布字眼的目標一律拒絕；
SELECTORS 表裡也不准出現那些字眼。這兩件事都在這裡釘死。
"""

from __future__ import annotations

import json
import struct
import zlib

import pytest

from src.errors import PipelineError
from src.publish import PLATFORMS, mark_published, payload
from src.publish_web import NEVER_CLICK, SELECTORS, _click
from src.schema import validate


def _png(path, w=1080, h=1080):
    sig = b"\x89PNG\r\n\x1a\n"
    ihdr = struct.pack(">II5B", w, h, 8, 2, 0, 0, 0)
    def chunk(tag, data):
        c = struct.pack(">I", len(data)) + tag + data
        return c + struct.pack(">I", zlib.crc32(tag + data) & 0xFFFFFFFF)
    path.write_bytes(sig + chunk(b"IHDR", ihdr) + chunk(b"IEND", b""))


@pytest.fixture()
def a_post(tmp_path, monkeypatch):
    monkeypatch.setenv("OUT_DIR", str(tmp_path))
    from src.paths import images_dir, post_path

    d = images_dir("t", 1); d.mkdir(parents=True)
    for name in ("01_cover.png", "02_point_1.png", "99_outro.png"):
        _png(d / name)
    pj = {
        "schema_version": "3.0", "generated_at": "2026-07-15T00:00:00+08:00",
        "source": {"slug": "t", "title": "T", "url": "https://example.com/t"},
        "post_index": 1, "angle": "論點",
        "images": [{"path": "images/01_cover.png", "role": "cover", "ratio": "1:1"}],
        "posts": [
            {"platform": "instagram", "caption": "hook\n\n正文。\n\n#a #b",
             "image_paths": ["images/01_cover.png", "images/02_point_1.png", "images/99_outro.png"],
             "attribution": "原文：T\nhttps://example.com/t", "hashtags": ["#a", "#b"]},
            {"platform": "threads", "caption": "hook\n\n正文。",
             "image_paths": ["images/01_cover.png", "images/02_point_1.png", "images/99_outro.png"],
             "attribution": "原文：T\nhttps://example.com/t"},
        ],
    }
    p = post_path("t", 1)
    p.write_text(json.dumps(pj, ensure_ascii=False), encoding="utf-8")
    return p


# --- 紅線：程式不按分享 ---

def test_selectors_never_target_the_share_button() -> None:
    """selector 表裡不准指名任何發布鍵——想按也選不到。

    判定是「整個目標值相等」：IG 的建立鍵叫「New post」（含 Post 但不是 Post），
    合法；指名「Post」「Share」「分享」「發佈」本身，違規。
    """
    from src.publish_web import _forbidden_word

    for (platform, step), sel in SELECTORS.items():
        word = _forbidden_word(sel)
        assert word is None, f"SELECTORS[{platform},{step}] 指名了發布鍵「{word}」"


def test_click_refuses_share_words(monkeypatch) -> None:
    """就算未來有人把分享鍵塞進表裡，_click() 也會當場拒絕。"""
    import src.publish_web as pw
    monkeypatch.setitem(pw.SELECTORS, ("instagram", "evil"), 'button:has-text("分享")')
    with pytest.raises(PipelineError) as e:
        _click(page=None, platform="instagram", step="evil")   # 根本不會碰到 page
    assert "分享" in e.value.message


def test_prefill_flows_only_click_through_the_guard() -> None:
    """流程程式碼裡的點擊只准走 _click()——繞過守門員就是繞過紅線。"""
    from pathlib import Path
    src = (Path(__file__).parent.parent / "src/publish_web.py").read_text(encoding="utf-8")
    assert "page.click(" not in src, "不准直接 page.click——一律走 _click()"
    assert "NEVER_CLICK" in src and "_click" in src


# --- 資料層 ---

def test_payload_threads_has_no_hashtags_and_order_is_kept(a_post) -> None:
    ig = payload("t", 1, "instagram")
    th = payload("t", 1, "threads")
    assert "#a" in ig["caption"] and "#a" not in th["caption"]
    assert [p.split("/")[-1].split("\\")[-1] for p in th["images"]] == \
        ["01_cover.png", "02_point_1.png", "99_outro.png"]


def test_payload_refuses_missing_image(a_post, tmp_path, monkeypatch) -> None:
    from src.paths import images_dir
    (images_dir("t", 1) / "02_point_1.png").unlink()
    with pytest.raises(PipelineError):
        payload("t", 1, "instagram")


def test_unknown_platform_is_rejected(a_post) -> None:
    with pytest.raises(PipelineError):
        payload("t", 1, "facebook")


def test_mark_published_records_a_human_action(a_post) -> None:
    r = mark_published("t", 1, "instagram")
    pj = r["post"]
    validate("post", pj)                                        # 契約收得下 published_at
    ig = next(p for p in pj["posts"] if p["platform"] == "instagram")
    th = next(p for p in pj["posts"] if p["platform"] == "threads")
    assert ig.get("published_at") and not th.get("published_at")
    again = json.loads(a_post.read_text(encoding="utf-8"))      # 真的落了地
    assert next(p for p in again["posts"] if p["platform"] == "instagram")["published_at"]
