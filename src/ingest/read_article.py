"""階段 1：本機 markdown → article.json

實作任務：[[文章讀取與正規化]]

契約（見 docs/spec.md）：
- 輸入：本機 .md 檔路徑
- 輸出：out/<slug>/article.json，須通過 schemas/article.schema.json
- 正規化：剝 frontmatter、剝 markdown 語法、去空段，段落編號連續且**定了就不能重排**
  （para_index 是金句的定錨點）
- body 必須是 paragraphs 以 \n\n 接起來的純文字——金句的 source_text 要能在裡面被找到

**不做簡繁轉換。** body 忠實保留原文，因為幻覺防線要拿它比對 source_text。
轉繁是分析階段輸出時才做（見 [[重點分析與金句抽取]]）。
"""

from __future__ import annotations

import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from ..errors import ErrorCode, PipelineError
from ..paths import slugify

MIN_PARAGRAPHS = 3
MIN_CHARS = 300

VIDEO_HOSTS = ("youtube.com", "youtu.be", "vimeo.com", "bilibili.com")

# --- Web Clipper 逐字稿的固定形狀（實際看過 Clippings/ 才定出來的） ---
# 每段開頭掛著粗體時間戳：`**0:03** · 嗨，大家好…`
TIMESTAMP_PREFIX = re.compile(r"^\*\*\d{1,2}:\d{2}(?::\d{2})?\*\*\s*[·•]\s*")
# 「內容綱要」那種目錄行：`00:00 Obsidian 安裝與基本操作`
TOC_LINE = re.compile(r"^\d{1,2}:\d{2}(?::\d{2})?\s+\S")

# --- markdown 語法 ---
IMAGE = re.compile(r"!\[[^\]]*\]\([^)]*\)")
LINK = re.compile(r"\[([^\]]*)\]\([^)]*\)")
WIKILINK = re.compile(r"\[\[([^\]|]*)(?:\|[^\]]*)?\]\]")
HEADING_LINE = re.compile(r"^\s*#{1,6}\s+")
EMPHASIS = re.compile(r"(\*\*|__|\*|_|`)")
BLOCKQUOTE = re.compile(r"^>\s*")
HR = re.compile(r"^\s*([-*_])\s*(\1\s*){2,}$")
CODE_FENCE = re.compile(r"^\s*```")
BARE_URL_LINE = re.compile(r"^\s*(https?://\S+\s*)+$")
HASHTAG_LINE = re.compile(r"^\s*(#\S+\s*)+$")


def _parse_frontmatter(text: str) -> tuple[dict[str, Any], str]:
    """剝 YAML frontmatter。只認得 Web Clipper 產出的簡單結構，不引入 yaml 依賴。

    支援：key: value、key: "value"、key:\n  - "[[wikilink]]"（陣列）
    """
    if not text.startswith("---"):
        return {}, text
    end = text.find("\n---", 3)
    if end == -1:
        return {}, text
    raw = text[3:end]
    body = text[end + 4 :].lstrip("\n")

    meta: dict[str, Any] = {}
    key: str | None = None
    for line in raw.splitlines():
        if not line.strip():
            continue
        if line.lstrip().startswith("- ") and key:
            item = line.lstrip()[2:].strip().strip('"').strip("'")
            meta.setdefault(key, [])
            if isinstance(meta[key], list):
                meta[key].append(item)
            continue
        m = re.match(r"^([A-Za-z_][\w-]*):\s*(.*)$", line)
        if not m:
            continue
        key, value = m.group(1), m.group(2).strip()
        meta[key] = value.strip('"').strip("'") if value else []
    return meta, body


def _clean_author(raw: Any) -> str | None:
    """Web Clipper 的 author 是陣列，值長得像 `[[PAPAYA 電腦教室]]`。取第一個、剝掉 [[ ]]。"""
    if isinstance(raw, list):
        raw = raw[0] if raw else None
    if not raw or not isinstance(raw, str):
        return None
    m = WIKILINK.match(raw.strip())
    author = m.group(1) if m else raw.strip()
    return author or None


def _strip_inline(line: str) -> str:
    line = IMAGE.sub("", line)
    line = LINK.sub(r"\1", line)
    line = WIKILINK.sub(r"\1", line)
    line = BLOCKQUOTE.sub("", line)
    line = EMPHASIS.sub("", line)
    # 跳脫字元：\[music\] 這種在 YouTube 逐字稿裡很常見
    line = re.sub(r"\\([\[\]_*`])", r"\1", line)
    line = re.sub(r"\[(music|音樂|applause|laughter)\]", "", line, flags=re.I)
    return line.strip()


def _to_paragraphs(body: str) -> list[str]:
    """把 markdown 正文切成乾淨的段落。

    Web Clipper 的 YouTube 剪報有個好用的性質：`## Transcript` 之前全是頻道樣板
    （縮圖、影片簡介、贊助連結、其他影片的推薦）。直接從 Transcript 之後開始切，
    比逐條過濾雜訊可靠得多。
    """
    m = re.search(r"^##\s+Transcript\s*$", body, re.MULTILINE)
    if m:
        body = body[m.end() :]

    paragraphs: list[str] = []
    in_code = False
    for block in re.split(r"\n\s*\n", body):
        lines: list[str] = []
        for line in block.splitlines():
            if CODE_FENCE.match(line):
                in_code = not in_code
                continue
            if in_code:
                continue
            if HR.match(line) or TOC_LINE.match(line.strip()):
                continue
            # 小節標題整行丟掉：它是章節標籤，不是可引用的句子。
            # 留著的話會變成一個超短段落，模型很可能把它當金句抽出來。
            if HEADING_LINE.match(line):
                continue
            if BARE_URL_LINE.match(line) or HASHTAG_LINE.match(line):
                continue
            line = TIMESTAMP_PREFIX.sub("", line.strip())
            line = _strip_inline(line)
            if line:
                lines.append(line)
        text = " ".join(lines).strip()
        text = re.sub(r"\s{2,}", " ", text)
        if text:
            paragraphs.append(text)
    return paragraphs


# 只在簡體出現的常用字。用來分辨 zh-Hans / zh-Hant——
# PAPAYA 是繁中頻道，但 YouTube 自動字幕吐出來是簡體，光看頻道猜會猜錯。
SIMPLIFIED_ONLY = set("们个这说经过时来对国还没关软网络题笔记为么么问题动开发实现认识样点儿两内层")
CJK = re.compile(r"[\u4e00-\u9fff]")


def detect_language(text: str) -> str:
    """回傳 BCP-47：zh-Hans / zh-Hant / en。

    語言只影響分析階段要不要翻譯／轉繁，不影響 body（body 一律保留原文）。
    """
    cjk = CJK.findall(text)
    if len(cjk) < len(text) * 0.1:
        return "en"
    hits = sum(1 for c in cjk if c in SIMPLIFIED_ONLY)
    return "zh-Hans" if hits >= 3 else "zh-Hant"


def normalize(md_text: str, file: Path) -> dict[str, Any]:
    """把 markdown 全文正規化成 article.json 的內容（不落地）。"""
    meta, body = _parse_frontmatter(md_text)

    title = (meta.get("title") or "").strip() if isinstance(meta.get("title"), str) else ""
    title = title or file.stem
    # Web Clipper 用 `source:` 存原文連結，不是 `url:`
    url = meta.get("source") or meta.get("url") or None
    author = _clean_author(meta.get("author"))

    if not author:
        raise PipelineError(
            ErrorCode.SOURCE_UNPARSEABLE,
            f"frontmatter 沒有 author：{file.name}",
            hint="補上 `author:` 再跑。出處標註寧可卡住也不能造假",
        )
    if not url:
        raise PipelineError(
            ErrorCode.SOURCE_UNPARSEABLE,
            f"frontmatter 沒有原文連結（`source:` 或 `url:`）：{file.name}",
            hint="補上原文／原影片網址再跑。貼文必須帶得出出處",
        )

    paragraphs = _to_paragraphs(body)
    text_total = sum(len(p) for p in paragraphs)
    if len(paragraphs) < MIN_PARAGRAPHS or text_total < MIN_CHARS:
        raise PipelineError(
            ErrorCode.ARTICLE_TOO_SHORT,
            f"正規化後只有 {len(paragraphs)} 段 / {text_total} 字（門檻：{MIN_PARAGRAPHS} 段 / {MIN_CHARS} 字）",
            hint="太短抽不出 3 句像樣的金句。換一篇",
        )

    origin = "video_transcript" if any(h in url for h in VIDEO_HOSTS) else "article"
    lang = detect_language("\n".join(paragraphs))

    source: dict[str, Any] = {
        "slug": slugify(file.stem),
        "title": title,
        "author": author,
        "url": url,
        "file": str(file),
    }
    published = meta.get("published")
    if isinstance(published, str) and published:
        source["published_at"] = published

    return {
        "schema_version": "2.0",
        "generated_at": datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds"),
        "source": source,
        "origin": origin,
        "language": lang,
        "word_count": text_total,
        "paragraphs": [{"index": i, "text": p} for i, p in enumerate(paragraphs)],
        "body": "\n\n".join(paragraphs),
    }


def read(md_path: Path, force: bool = False) -> Path:
    """讀取 markdown、正規化、寫出 article.json，回傳檔案路徑。"""
    from ..paths import article_path
    from ..schema import write_json

    md_path = Path(md_path)
    if not md_path.is_file():
        raise PipelineError(
            ErrorCode.SOURCE_NOT_FOUND,
            f"找不到檔案：{md_path}",
            hint="給一個存在的 .md 檔路徑（例：Obsidian 的 Clippings 資料夾）",
        )
    try:
        md_text = md_path.read_text(encoding="utf-8")
    except UnicodeDecodeError as e:
        raise PipelineError(
            ErrorCode.SOURCE_UNPARSEABLE,
            f"檔案不是 UTF-8 編碼：{md_path.name}（{e}）",
            hint="用編輯器另存成 UTF-8",
        ) from e

    data = normalize(md_text, md_path)
    out = article_path(data["source"]["slug"])
    if out.exists() and not force:
        return out
    return write_json("article", out, data)


__all__ = ["read", "normalize", "MIN_PARAGRAPHS", "MIN_CHARS"]
