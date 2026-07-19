"""發布的資料層（純邏輯，不碰瀏覽器）。實作任務：[[一鍵發布到 IG 與 Threads]]

分工（比照 layout.py ↔ render_cards.py）：

    src/publish.py       發什麼、記什麼——決策，純函式，可測
    src/publish_web.py   怎麼開瀏覽器、怎麼代填——勞動（Playwright）

**方案一（Human 2026-07-15 選定）：程式幫到最後一步，「分享」那一鍵永遠是人的。**
所以這個模組只做兩件事：組出要發的東西（payload）、記下人已經發了（mark_published）。
"""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .errors import ErrorCode, PipelineError
from .paths import post_dir, post_path
from .schema import read_json, validate, write_json

PLATFORMS = ("instagram", "threads")

PLATFORM_URL = {
    "instagram": "https://www.instagram.com/",
    "threads": "https://www.threads.net/",
}


def payload(slug: str, post_index: int, platform: str) -> dict[str, Any]:
    """組出「要發的東西」：caption ＋ 依序的圖片絕對路徑。

    - IG 用 instagram 版（含 hashtag）；Threads 用 threads 版（**無 hashtag**）
    - 圖片順序照 post.json 的 `image_paths`——那是發布時的排圖順序，契約寫明的
    """
    if platform not in PLATFORMS:
        raise PipelineError(ErrorCode.MISSING_INPUT, f"未知的平台：{platform}",
                            hint=f"可用：{', '.join(PLATFORMS)}")
    pj = read_json("post", post_path(slug, post_index))
    entry = next((p for p in pj["posts"] if p["platform"] == platform), None)
    if entry is None:
        raise PipelineError(ErrorCode.MISSING_INPUT, f"post.json 裡沒有 {platform} 版")

    base = post_dir(slug, post_index)
    images: list[Path] = []
    for rel in entry["image_paths"]:
        img = base / rel
        if not img.is_file():
            raise PipelineError(
                ErrorCode.MISSING_INPUT,
                f"圖片不見了：{img}",
                hint="先按「出圖＋文案」重出，再發布",
            )
        images.append(img)

    return {
        "platform": platform,
        "url": PLATFORM_URL[platform],
        "caption": entry["caption"],
        "images": [str(i) for i in images],
        "published_at": entry.get("published_at"),
    }


def mark_published(slug: str, post_index: int, platform: str) -> dict[str, Any]:
    """人按完平台的「分享」，回編輯台標「已發布」。**只記人的動作，不做別的。**"""
    if platform not in PLATFORMS:
        raise PipelineError(ErrorCode.MISSING_INPUT, f"未知的平台：{platform}")
    path = post_path(slug, post_index)
    pj = read_json("post", path)
    for entry in pj["posts"]:
        if entry["platform"] == platform:
            entry["published_at"] = (
                datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds")
            )
            break
    else:
        raise PipelineError(ErrorCode.MISSING_INPUT, f"post.json 裡沒有 {platform} 版")
    validate("post", pj)
    return {"path": str(write_json("post", path, pj)), "post": pj}


__all__ = ["payload", "mark_published", "PLATFORMS", "PLATFORM_URL"]
