"""階段 3：highlights.json + templates/ → p<N>/images/*.png

實作任務：[[圖卡渲染器]]

分工（刻意的）：
    layout.py       決定「拆不拆、怎麼拆」——純邏輯，測試不需要瀏覽器
    render_cards.py 決定「怎麼量、怎麼截」——這裡才碰 Playwright

量尺就是真的瀏覽器：把卡片渲染出來，讓 card.js 的 autofit 二分搜尋字級，
回報「在可讀性下限之上塞不塞得下」。**不用估的，直接量。**
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

from ..errors import ErrorCode, PipelineError
from ..paths import PROJECT_ROOT, TEMPLATE_DIR, ensure_dirs, image_name, images_dir
from .layout import plan_all

RATIOS = {"1x1": (1080, 1080), "4x5": (1080, 1350)}

THEME = os.environ.get("CARD_THEME", "b")     # b = 深色螢光（Human 2026-07-14 選定）
RATIO = os.environ.get("CARD_RATIO", "1x1")

# 一則貼文的圖卡順序：封面 → 內容卡 → 結尾
COVER_IDX, OUTRO_IDX = 1, 99


def _card_url() -> str:
    return (TEMPLATE_DIR / "card.html").as_uri()


class Renderer:
    """開一次瀏覽器，把一則貼文的所有卡片量完、拆完、截完。"""

    def __init__(self, page, ctx: dict[str, Any]) -> None:
        self.page = page
        self.ctx = ctx

    def measure(self, card: dict[str, Any]) -> dict[str, Any]:
        """渲染一張卡，回傳 autofit 的結果 {fs, overflow}。"""
        return self.page.evaluate(
            "([card, ctx]) => renderCard(card, ctx)", [card, self.ctx]
        )

    def fits(self, card: dict[str, Any]) -> bool:
        """量尺：在可讀性下限之上塞得下嗎？"""
        return not self.measure(card)["overflow"]

    def shoot(self, card: dict[str, Any], path: Path) -> dict[str, Any]:
        fit = self.measure(card)
        if fit["overflow"]:  # plan() 應該已經處理掉了，走到這裡是 bug
            raise PipelineError(
                ErrorCode.RENDER_OVERFLOW,
                f"{card['type']} 卡溢出且未被拆開：{card.get('title') or card.get('text')}",
                hint="layout.plan() 的拆卡邏輯有漏洞",
            )
        self.page.screenshot(path=str(path), scale="css")
        return fit


def render_post(
    highlights_post: dict[str, Any],
    ctx: dict[str, Any],
    slug: str,
    post_index: int,
    theme: str = THEME,
    ratio: str = RATIO,
) -> list[dict[str, Any]]:
    """把一則貼文的知識卡渲染成 PNG。回傳 post.json 要用的 images 清單。"""
    try:
        from playwright.sync_api import sync_playwright
    except ImportError as e:  # pragma: no cover
        raise PipelineError(
            ErrorCode.MISSING_INPUT,
            "沒有安裝 playwright",
            hint="pip install -r requirements.txt && playwright install chromium",
        ) from e

    if ratio not in RATIOS:
        raise ValueError(f"未知的比例：{ratio}（可用：{list(RATIOS)}）")
    w, h = RATIOS[ratio]

    ensure_dirs(slug, post_index)
    out_dir = images_dir(slug, post_index)
    images: list[dict[str, Any]] = []

    with sync_playwright() as p:
        browser = p.chromium.launch()
        page = browser.new_page(viewport={"width": w, "height": h})
        page.goto(_card_url())
        page.evaluate(
            "([t, r]) => { document.body.dataset.theme = t; document.body.dataset.ratio = r; }",
            [theme, ratio],
        )
        # 字體要載完才能量，也才能截——否則量到的是 fallback 字體的寬度
        page.evaluate("document.fonts.ready")

        r = Renderer(page, ctx)

        cover = {"type": "cover", **highlights_post.get("cover", {}),
                 "angle": highlights_post["angle"], "hook": highlights_post.get("hook"),
                 "stat": highlights_post.get("stat")}
        content = plan_all(highlights_post["cards"], r.fits)   # ← 這裡拆卡
        deck = [cover] + content + [{"type": "outro"}]

        seq = 0
        for card in deck:
            t = card["type"]
            if t == "cover":
                idx, ci = COVER_IDX, None
            elif t == "outro":
                idx, ci = OUTRO_IDX, None
            else:
                seq += 1
                idx, ci = seq + 1, seq

            name = image_name(idx, t, ci)
            fit = r.shoot(card, out_dir / name)
            entry: dict[str, Any] = {
                "path": f"images/{name}",
                "role": t,
                "ratio": ratio.replace("x", ":"),
                "bytes": (out_dir / name).stat().st_size,
            }
            if ci is not None:
                entry["card_index"] = ci
            images.append(entry)
            print(f"    {name}  字級 {fit['fs']}px" + ("  ← 拆卡" if card.get("pager") else ""))

        browser.close()

    return images


def render(slug: str, ratio: str = RATIO, force: bool = False) -> list[Path]:
    """讀 highlights.json，把每一則貼文都出圖。"""
    from ..paths import article_path, highlights_path
    from ..schema import read_json

    h = read_json("highlights", highlights_path(slug))
    article = read_json("article", article_path(slug))
    src = h["source"]

    ctx = {
        "title": src["title"],
        "author": src["author"],
        "url": src.get("url", ""),
        "handle": os.environ.get("IG_HANDLE", "@your_handle"),
        "series": "",
    }

    out: list[Path] = []
    for i, post in enumerate(h["posts"], 1):
        d = images_dir(slug, i)
        if d.exists() and any(d.glob("*.png")) and not force:
            out.extend(sorted(d.glob("*.png")))
            continue
        print(f"  第 {i} 則：{post['angle']}")
        imgs = render_post(post, ctx, slug, i, ratio=ratio)
        out.extend(d / Path(m["path"]).name for m in imgs)
    return out


__all__ = ["render", "render_post", "Renderer", "RATIOS"]
