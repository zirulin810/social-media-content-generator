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


def test_missing_author_is_allowed_but_never_fabricated() -> None:
    """**作者可以沒有，但不准編。**（Human 2026-07-14）

    有些素材本來就沒有個人作者——Google 的課程、官方文件、機構出的白皮書。
    原本這裡直接報錯，理由是「出處標註寧可卡住也不能造假」。
    **但那條理由保護的是「出處」，不是「作者」**——我把兩件事綁在一起了。

    現在：沒有作者 → `source` 裡**根本不會有 `author` 這個鍵**
    （不是空字串——那會讓下游分不出「沒有」和「空的」）。
    """
    md = CLIPPING.replace('  - "[[PAPAYA 電腦教室]]"\n', "")
    src = normalize(md, Path("t.md"))["source"]
    assert "author" not in src, "沒有作者就不要有這個欄位"
    assert src["url"], "但出處還在"

    # **不准拿別的東西湊一個作者出來**（標題、網域、檔名都不行）
    assert "測試影片標題" not in str(src.get("author", ""))


OWN_NOTE = """---
Date: 2026-06-16
Source: "[Kaggle](https://www.kaggle.com/competitions/5-day-ai-agents/discussion/708280)"
Status: 完成
---
# Introduction to Agents

Vibe Coding 與 Agentic Engineering 是[[Spectrum|光譜]]兩端的存在，差異不在於用不用 AI，
而在於架構、驗證與人類判斷，其中最大的分水嶺是[[Tests and Evals|驗證]]。週末的原型可以純憑感覺，
但支付系統的 API 不行——後者的錯誤會直接變成別人的損失，而不是你自己的時間。

想從 Vibe Coding 移到另一端，靠的是上下文品質，而不是提示詞技巧。六種上下文類型需要在
靜態與動態之間做取捨，並透過漸進式揭露的三層載入架構讓技能保持輕量。這件事的關鍵不是把
所有東西都塞給模型，而是每次只給它當下需要的那一層——**上下文不是越多越好，是越準越好。**

生成本身已經不是瓶頸，問題在於生成出來的東西夠不夠可信。驗證、判斷與方向，才是新的工藝。
八成以上的專業開發者已經每天在用 AI 寫程式，但真正的分水嶺不在「寫得多快」，
而在「能不能證明它是對的」——**這也是為什麼測試與評估變成了最重要的那一環。**
"""


def test_a_hand_written_note_is_not_a_web_clipping() -> None:
    """**人自己的筆記模板跟 Web Clipper 不一樣，兩種都要吃。**

    實際踩到：`5-Day AI Agents -1.md` 明明有來源，卻被判定成「沒有來源」。兩個原因——

    1. **鍵的大小寫**：Web Clipper 寫 `source:`，人寫 `Source:`。
       大小寫敏感的解析，會讓「明明有來源」的筆記被擋在門外。
    2. **值的格式**：Web Clipper 存裸網址，人存 markdown 連結 `[Kaggle](https://…)`。

    這兩個都是「同一件事的不同寫法」——**解析器該適應現實，不是要現實適應解析器。**
    """
    src = normalize(OWN_NOTE, Path("5-Day AI Agents -1.md"))["source"]
    assert src["url"] == "https://www.kaggle.com/competitions/5-day-ai-agents/discussion/708280"
    assert "author" not in src, "課程沒有個人作者——允許，但不准編一個出來"


def test_wikilink_alias_wins() -> None:
    """`[[Spectrum|光譜]]` 在文章裡讀起來是「光譜」，不是「Spectrum」。

    原本的 regex 取的是**檔名**（連結目標），那會讓圖卡上出現一堆英文檔名。
    """
    paras = normalize(OWN_NOTE, Path("t.md"))["paragraphs"]
    body = "\n".join(p["text"] for p in paras)
    assert "光譜" in body and "驗證" in body
    assert "Spectrum" not in body and "Tests and Evals" not in body
    assert "[[" not in body


def test_missing_url_raises() -> None:
    """**來源不能沒有。**

    「這是誰講的」可以不知道，「這是從哪來的」不能不知道——
    少了前者只是資訊不全，少了後者就是**沒標來源的轉貼**。
    """
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
