"""輸出目錄慣例（單一事實來源）。

任何模組要寫檔／找檔，都經過這裡，不要自己拼字串。
目錄結構見 docs/spec.md。
"""

from __future__ import annotations

import hashlib
import os
import re
import unicodedata
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
SCHEMA_DIR = PROJECT_ROOT / "schemas"
TEMPLATE_DIR = PROJECT_ROOT / "templates"
PROMPT_DIR = PROJECT_ROOT / "prompts"


def out_root() -> Path:
    return PROJECT_ROOT / os.environ.get("OUT_DIR", "out")


def source_dir() -> Path:
    """素材放哪裡——**沒特別指定時的預設來源**（Obsidian 的 Web Clipper 剪報）。

    可用環境變數覆寫：`set SOURCE_DIR=D:\\某個資料夾`
    """
    env = os.environ.get("SOURCE_DIR")
    if env:
        return Path(env).expanduser()
    return Path.home() / "Documents" / "Obsidian Vault" / "Clippings"


def is_stale(product: Path, *inputs: Path) -> bool:
    """產物該不該重做？**跟它的每一個輸入比時間。**

    「已經有檔案就跳過」是最省事、也最危險的續跑邏輯——**產物過期而不自知，
    比根本沒有產物更糟**：你會拿著一份看起來已經更新的東西去發文。

    2026-07-14 連續踩到兩次：
      1. 簡繁轉換上線 → 重跑分析 → 圖卡還是簡體（渲染器看到有 PNG 就跳過）
      2. 改了 prompt → 重跑文案 → 秒回，印出來的是上一輪的舊文案

    第二次尤其陰險：**我改的是 prompt，不是上游的資料。** 只比對上游產物的時間也抓不到——
    **prompt 和版型也是輸入。** 所以這個函式吃的是「所有輸入」，不是「上一階段的產物」。
    """
    if not product.exists():
        return True
    made = product.stat().st_mtime
    for src in inputs:
        if not src.exists():
            continue
        if src.is_dir():
            newest = max((p.stat().st_mtime for p in src.rglob("*") if p.is_file()), default=0)
        else:
            newest = src.stat().st_mtime
        if newest > made:
            return True
    return False


def article_dir(slug: str) -> Path:
    return out_root() / slug


def article_path(slug: str) -> Path:
    return article_dir(slug) / "article.json"


def highlights_path(slug: str) -> Path:
    return article_dir(slug) / "highlights.json"


# 一篇文章可能切成 1–3 則貼文（依資訊密度），所以產物多一層 p1/ p2/ p3/
def post_dir(slug: str, post_index: int) -> Path:
    """post_index 為 1-based，對應 highlights.posts 的順序。"""
    return article_dir(slug) / f"p{post_index}"


def post_path(slug: str, post_index: int) -> Path:
    return post_dir(slug, post_index) / "post.json"


def images_dir(slug: str, post_index: int) -> Path:
    return post_dir(slug, post_index) / "images"


# 圖卡角色：封面、結尾，加上四種內容卡
CARD_ROLES = ("cover", "point", "steps", "contrast", "quote", "outro")


def image_name(index: int, role: str, card_index: int | None = None, ext: str = "png") -> str:
    """依 spec 的命名規則產生圖檔名（相對檔名，不含資料夾）。

    >>> image_name(1, "cover")
    '01_cover.png'
    >>> image_name(2, "point", 1)
    '02_point_1.png'
    >>> image_name(99, "outro")
    '99_outro.png'
    """
    if role not in CARD_ROLES:
        raise ValueError(f"未知的圖卡角色：{role}（可用：{CARD_ROLES}）")
    if role in ("cover", "outro"):
        return f"{index:02d}_{role}.{ext}"
    if card_index is None:
        raise ValueError(f"role={role} 必須給 card_index（1-based，對應 highlights 的卡片順序）")
    return f"{index:02d}_{role}_{card_index}.{ext}"


def slugify(name: str) -> str:
    """把檔名／標題轉成資料夾名（符合 schema 的 ^[a-z0-9][a-z0-9-]*$）。

    中文檔名沒有合理的音譯，直接轉會得到空字串——所以退回雜湊化的 fallback，
    確保永遠產得出合法且穩定的 slug。人要辨識靠的是 source.title，不是資料夾名。

    >>> slugify("Why Your To-Do List Never Ends")
    'why-your-to-do-list-never-ends'
    """
    s = unicodedata.normalize("NFKD", name)
    s = s.encode("ascii", "ignore").decode("ascii").lower()
    s = re.sub(r"[^a-z0-9]+", "-", s).strip("-")
    if not s:
        s = "article-" + hashlib.sha1(name.encode("utf-8")).hexdigest()[:8]
    return s


def unique_slug(base: str) -> str:
    """同名時附 -2、-3，避免不同文章互相覆蓋產物。"""
    slug = base
    n = 2
    while article_dir(slug).exists():
        slug = f"{base}-{n}"
        n += 1
    return slug


def ensure_dirs(slug: str, post_index: int) -> Path:
    d = post_dir(slug, post_index)
    (d / "images").mkdir(parents=True, exist_ok=True)
    return d
