"""文章讀取與正規化的測試。

重點不是「跑得動」，而是「該擋的有擋、該剝的有剝」——
下游的幻覺防線建立在 body 忠實且乾淨的前提上。
"""

from __future__ import annotations

from pathlib import Path

import pytest

from src.errors import ErrorCode, PipelineError
from src.ingest.read_article import detect_language, normalize
from src.schema import validate

# 一份仿 Obsidian Web Clipper 的 YouTube 剪報，把所有雜訊都放進來
CLIPPING = """---
title: "測試影片標題"
source: "https://www.youtube.com/watch?v=abc123"
author:
  - "[[PAPAYA 電腦教室]]"
published: 2026-07-08
created: 2026-07-11
tags:
  - "clippings"
---
![](https://www.youtube.com/watch?v=abc123)

🔹 內容綱要
00:00 第一節
03:31 第二節

🔹 請我喝咖啡
https://buymeacoffee.com/example

#教學 #Obsidian #筆記

## Transcript

### 第一節標題

**0:03** · 這是第一段的內容，講的是一個關於知識管理的想法。筆記軟體本身不會讓你變聰明，真正有價值的是你在裡面建立的連結。很多人買了工具就以為問題解決了，其實工具只是把問題原封不動地搬了個家而已。

**0:16** · 這是第二段，裡面有 **粗體** 和 [連結](https://example.com) 還有 [[wikilink]]。重點是這些 markdown 語法都必須被剝乾淨，否則下游的金句抽取會把符號當成內容的一部分，圖卡上就會出現星號跟方括號。

### 第二節標題

**1:02** · 這是第三段，內容繼續延伸前面的論點，把它推到一個具體的結論。如果段落編號會變動，金句就定不了錨，幻覺防線也就形同虛設。所以段落一旦編號就不能重排，這是整條 pipeline 的地基。

**1:40** · 這是第四段。契約的價值不在於文件寫得多漂亮，而在於它擋得住多少種造假。擋不住的契約只是註解，擋得住的契約才是規則。這也是為什麼寫檔一律要先驗證再落地，不符 schema 的東西根本不該進得了輸出資料夾。
"""


def _fm(**over: str) -> str:
    """產生只改動指定欄位的 frontmatter 變體。"""
    lines = CLIPPING.splitlines()
    out = []
    for line in lines:
        key = line.split(":", 1)[0].strip()
        if key in over:
            if over[key] is None:
                continue
            line = f"{key}: {over[key]}"
        out.append(line)
    return "\n".join(out)


def test_clipping_normalizes_and_passes_schema() -> None:
    data = normalize(CLIPPING, Path("測試影片標題.md"))
    validate("article", data)


def test_timestamp_prefix_is_stripped() -> None:
    """`**0:03** · ` 是 Web Clipper 逐字稿的固定前綴，不剝掉會被當成內容抽成金句。"""
    data = normalize(CLIPPING, Path("t.md"))
    assert "**" not in data["body"]
    assert "0:03" not in data["body"]
    assert data["paragraphs"][0]["text"].startswith("這是第一段")


def test_channel_boilerplate_is_dropped() -> None:
    """`## Transcript` 之前全是頻道樣板：縮圖、內容綱要、贊助連結、hashtag。"""
    body = normalize(CLIPPING, Path("t.md"))["body"]
    for noise in ("內容綱要", "buymeacoffee", "#教學", "youtube.com/watch"):
        assert noise not in body, noise


def test_section_headings_are_not_paragraphs() -> None:
    """小節標題是章節標籤，不是可引用的句子——留著會變成超短段落被誤抽成金句。"""
    texts = [p["text"] for p in normalize(CLIPPING, Path("t.md"))["paragraphs"]]
    assert "第一節標題" not in texts
    assert "第二節標題" not in texts


def test_markdown_syntax_is_stripped() -> None:
    body = normalize(CLIPPING, Path("t.md"))["body"]
    assert "**" not in body and "](" not in body and "[[" not in body
    assert "粗體" in body and "連結" in body and "wikilink" in body


def test_body_equals_paragraphs_joined() -> None:
    """幻覺防線靠這個等式：source_text 要能在 body 裡被找到，也要落在對應段落裡。"""
    data = normalize(CLIPPING, Path("t.md"))
    assert data["body"] == "\n\n".join(p["text"] for p in data["paragraphs"])
    assert [p["index"] for p in data["paragraphs"]] == list(range(len(data["paragraphs"])))


def test_webclipper_frontmatter_mapping() -> None:
    """Web Clipper 用 `source:` 存連結、`author:` 是 wikilink 陣列——照 `url:`/字串讀會全滅。"""
    src = normalize(CLIPPING, Path("t.md"))["source"]
    assert src["url"] == "https://www.youtube.com/watch?v=abc123"
    assert src["author"] == "PAPAYA 電腦教室"  # [[ ]] 已剝掉
    assert src["title"] == "測試影片標題"


def test_origin_detects_video_transcript() -> None:
    assert normalize(CLIPPING, Path("t.md"))["origin"] == "video_transcript"


def test_missing_author_raises_rather_than_fabricating() -> None:
    """出處標註寧可卡住也不能造假。"""
    md = CLIPPING.replace('  - "[[PAPAYA 電腦教室]]"\n', "")
    with pytest.raises(PipelineError) as e:
        normalize(md, Path("t.md"))
    assert e.value.code == ErrorCode.SOURCE_UNPARSEABLE


def test_missing_url_raises() -> None:
    md = CLIPPING.replace('source: "https://www.youtube.com/watch?v=abc123"\n', "")
    with pytest.raises(PipelineError) as e:
        normalize(md, Path("t.md"))
    assert e.value.code == ErrorCode.SOURCE_UNPARSEABLE


def test_too_short_article_is_rejected() -> None:
    md = """---
title: "短"
source: "https://example.com/a"
author:
  - "[[某人]]"
---

一句話。

第二句。
"""
    with pytest.raises(PipelineError) as e:
        normalize(md, Path("t.md"))
    assert e.value.code == ErrorCode.ARTICLE_TOO_SHORT


def test_language_detection_distinguishes_hans_from_hant() -> None:
    """PAPAYA 是繁中頻道，但自動字幕吐出來是簡體——光看頻道會猜錯。"""
    assert detect_language("这是简体中文的内容，我们来说说这个问题") == "zh-Hans"
    assert detect_language("這是繁體中文的內容，我們來說說這個問題") == "zh-Hant"
    assert detect_language("This is plain English content about note taking.") == "en"
