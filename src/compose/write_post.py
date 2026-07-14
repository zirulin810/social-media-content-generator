"""階段 4：highlights.json + images/ → post.json

實作任務：[[貼文文案產生器]]

契約：
- IG：caption 前 125 字要能獨立成立（其餘會被折疊）；hashtag 5–10 個
- Threads：≤ 500 字（見 THREADS_MAX_CHARS），少用 hashtag
- 每則貼文結尾必帶原文出處（標題 + 作者 + source.url）
- 語氣依 article.origin 調整：article →「這篇文章」；video_transcript →「這支影片」
- prompt 放 prompts/caption_ig.md、prompts/caption_threads.md
"""

from __future__ import annotations

from pathlib import Path

from ..paths import highlights_path, post_path
from ..schema import read_json

THREADS_MAX_CHARS = 500
IG_MAX_CHARS = 500  # 比 IG 的 2200 硬上限更緊（見〈文章來源清單與挑選標準〉）
IG_FOLD_CHARS = 125  # IG 貼文超過這個長度會被折疊成「...更多」


def compose(slug: str, force: bool = False) -> Path:
    path = post_path(slug)
    if path.exists() and not force:
        return path
    highlights = read_json("highlights", highlights_path(slug))  # noqa: F841
    raise NotImplementedError(
        "見任務筆記〈貼文文案產生器〉。產出 instagram / threads 兩版，"
        "以 write_json('post', path, data) 寫出。"
    )


__all__ = ["compose", "THREADS_MAX_CHARS", "IG_FOLD_CHARS"]
