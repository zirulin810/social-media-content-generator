"""編輯台改版第二輪（[[編輯台改版第二輪]]）的規則與後端。

1. **一個素材一則貼文**：出廠 posts_max=1，prompt 的措辭要通順（不能出現「2–1 則」）；
   「幾則」是編輯規則不是物理極限——舊的多則資料照樣合約
2. **卡片上限＝輪播上限的鏡子**：出廠 cards_max=18（20 − 封面 − 出處卡）；
   拆卡膨脹超過 20 由渲染端擋
3. **刪素材**：只刪 out/<slug>/ 產物、來源 md 不動、亂造的 slug 打不到別的路徑
4. **輸出到本地暫存**：圖片照發文順序重新命名＋兩版文案 txt
"""

from __future__ import annotations

import json
import struct
import zlib
from pathlib import Path

import pytest

from src import settings
from src.errors import PipelineError
from src.schema import validate


def test_factory_defaults_one_post_eighteen_cards() -> None:
    assert settings.DEFAULTS["generation"]["posts_max"] == 1
    assert settings.DEFAULTS["generation"]["cards_max"] == 18  # 輪播 20 − 封面 − 出處卡


def test_posts_rule_reads_naturally_for_one_and_many(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("SETTINGS_FILE", str(tmp_path / "s.json"))
    from src.analyze.extract_highlights import build_prompt
    art = {"source": {"slug": "t", "title": "T"}, "language": "zh",
           "paragraphs": [{"index": 0, "text": "素材。"}]}
    one = build_prompt(art)                                    # 出廠＝1 則
    assert "固定產出 1 則" in one and "2–1 則" not in one   # 「2–18 張」含子字串 2–1，要比對到「則」
    settings.save({"generation": {"posts_max": 3}})
    many = build_prompt(art)                                   # 批次腳本：則數也是明定的，不是「最多」
    assert "固定產出 3 則" in many
    assert "{posts_rule}" not in one and "{overflow_rule}" not in one


def test_legacy_multi_post_files_still_validate() -> None:
    """posts_max=1 是給模型的規則，不是 schema 的刀——舊的 3 則資料照樣讀得進來。"""
    card = {"type": "point", "title": "卡", "body": "內容。",
            "evidence": [{"para_index": 0, "source_text": "the source"}]}
    h = {"schema_version": "3.1", "generated_at": "2026-07-15T00:00:00+08:00",
         "source": {"slug": "t", "title": "T", "url": "https://example.com/t"},
         "summary": ["一", "二", "三"],
         "posts": [{"angle": f"論點{i}", "cards": [dict(card), dict(card)],
                    "hashtags": ["#a", "#b", "#c"]} for i in range(3)]}
    validate("highlights", h)                                  # 不炸


def test_render_guards_the_carousel_limit() -> None:
    src = (Path(__file__).parent.parent / "src/render/render_cards.py").read_text(encoding="utf-8")
    assert "PLATFORM_MEDIA_MAX = 20" in src
    assert "len(deck) > PLATFORM_MEDIA_MAX" in src, "拆卡膨脹超過輪播上限必須在出圖前擋下"


# --- 刪素材 ---

def test_delete_article_removes_products_only(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("OUT_DIR", str(tmp_path / "out"))
    from src.editor import delete_article
    from src.paths import article_dir

    d = article_dir("some-slug"); d.mkdir(parents=True)
    (d / "highlights.json").write_text("{}", encoding="utf-8")
    removed = delete_article("some-slug")
    assert not d.exists() and "some-slug" in removed


def test_delete_article_refuses_path_tricks(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("OUT_DIR", str(tmp_path / "out"))
    from src.editor import delete_article
    for evil in ("../secrets", "a/../../b", "_inbox", "UPPER", "", "有中文"):
        with pytest.raises(PipelineError):
            delete_article(evil)


# --- 輸出到本地暫存 ---

def _png(path):
    sig = b"\x89PNG\r\n\x1a\n"
    ihdr = struct.pack(">II5B", 1080, 1080, 8, 2, 0, 0, 0)
    def chunk(tag, data):
        c = struct.pack(">I", len(data)) + tag + data
        return c + struct.pack(">I", zlib.crc32(tag + data) & 0xFFFFFFFF)
    path.write_bytes(sig + chunk(b"IHDR", ihdr) + chunk(b"IEND", b""))


def test_export_local_renames_in_publish_order(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("OUT_DIR", str(tmp_path / "out"))
    from src.editor import export_local
    from src.paths import images_dir, post_path

    d = images_dir("t", 1); d.mkdir(parents=True)
    for n in ("01_cover.png", "02_point_1.png", "99_outro.png"):
        _png(d / n)
    pj = {"schema_version": "3.0", "generated_at": "2026-07-15T00:00:00+08:00",
          "source": {"slug": "t", "title": "T", "url": "https://example.com/t"},
          "post_index": 1, "angle": "論點",
          "images": [{"path": "images/01_cover.png", "role": "cover", "ratio": "1:1"}],
          "posts": [
              {"platform": "instagram", "caption": "IG 文案\n\n#a",
               "image_paths": ["images/01_cover.png", "images/02_point_1.png", "images/99_outro.png"],
               "attribution": "原文：T\nhttps://example.com/t", "hashtags": ["#a", "#b", "#c"]},
              {"platform": "threads", "caption": "Threads 文案",
               "image_paths": ["images/01_cover.png", "images/02_point_1.png", "images/99_outro.png"],
               "attribution": "原文：T\nhttps://example.com/t"},
          ]}
    post_path("t", 1).write_text(json.dumps(pj, ensure_ascii=False), encoding="utf-8")

    dest = export_local("t", 1)
    try:
        names = sorted(x.name for x in dest.iterdir())
        assert names == ["01.png", "02.png", "03.png", "文案_IG.txt", "文案_Threads.txt"]
        assert (dest / "文案_IG.txt").read_text(encoding="utf-8").startswith("IG 文案")
        assert "#" not in (dest / "文案_Threads.txt").read_text(encoding="utf-8")
        # 重複輸出＝覆蓋，不堆疊
        dest2 = export_local("t", 1)
        assert dest2 == dest and sorted(x.name for x in dest.iterdir()) == names
    finally:
        import shutil
        shutil.rmtree(dest, ignore_errors=True)  # 測試不留垃圾在真的專案資料夾



# --- 2026-07-15 Human：則數由介面明定（每則一格），不再讓模型決定 ---

ARTICLE3 = {
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


def test_briefs_fix_the_post_count_and_carry_topics() -> None:
    from src.analyze.extract_highlights import build_prompt
    two = build_prompt(ARTICLE3, briefs=["入門介紹", ""])
    assert "固定產出 2 則" in two
    assert "第 1 則：入門介紹" in two
    assert "第 2 則：（未指定" in two
    one_blank = build_prompt(ARTICLE3, briefs=[""])
    assert "固定產出 1 則" in one_blank and "各則題目" not in one_blank


def test_wrong_post_count_is_sent_back_for_repair() -> None:
    """介面說 2 則，模型給 1 則 → 餵回去修；修不好就明白地失敗，不默默收貨。"""
    from src.analyze.extract_highlights import analyze

    card = {"type": "point", "title": "卡", "body": "內容。",
            "evidence": [{"para_index": 0, "source_text": "這是一段測試素材"}]}
    one_post = {"summary": ["一", "二", "三"],
                "posts": [{"angle": "只有一則", "cards": [dict(card), dict(card)],
                           "hashtags": ["#a", "#b", "#c"]}]}
    calls = []
    def llm(prompt):
        calls.append(prompt)
        return json.dumps(one_post, ensure_ascii=False)

    with pytest.raises(PipelineError) as e:
        analyze(ARTICLE3, llm=llm, briefs=["入門", "實戰"])
    assert "2 則" in e.value.message and len(calls) >= 2   # 至少餵回去修過
    assert "你給了 1 則" in calls[-1]                       # 修復指令講得出差在哪
