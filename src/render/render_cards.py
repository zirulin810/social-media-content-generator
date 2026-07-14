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
from ..paths import (
    PROJECT_ROOT,
    TEMPLATE_DIR,
    ensure_dirs,
    image_name,
    images_dir,
    is_stale,
)
from .browser import launch_chromium, sync_playwright_or_die
from .layout import plan_all

RATIOS = {"1x1": (1080, 1080), "4x5": (1080, 1350)}

THEME = os.environ.get("CARD_THEME", "b")     # b = 深色螢光（Human 2026-07-14 選定）
RATIO = os.environ.get("CARD_RATIO", "1x1")

# 拆卡的門檻。**這裡有兩個，不是一個。**
#
#   COMFORT_FS  低於這個字級就該拆卡了——不是「塞不下才拆」
#   MIN_FS      低於這個根本不出圖（card.js 的硬底線）
#
# 第一版我只有硬底線，結果 5 步的卡縮到 34px 剛好塞得下 → 不拆 → 一面文字牆。
# 「塞得下」和「讀得下去」是兩件事。
COMFORT_FS = int(os.environ.get("CARD_COMFORT_FS", "44"))

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
        """量尺：**讀得舒服嗎？** 不是「塞不塞得下」。

        塞得下但字級掉到 34px = 文字牆。那種卡要拆，不是硬塞。
        """
        fit = self.measure(card)
        return not fit["overflow"] and fit["fs"] >= COMFORT_FS

    def shoot(self, card: dict[str, Any], path: Path) -> dict[str, Any]:
        """截圖前先稽核。**寧可不出圖，也不要出一張被切掉的圖。**

        `overflow` 是 autofit 推論出來的，`audit` 是逐一量出來的。
        兩個都要過——第一次出圖時，autofit 因為量錯東西而漏判，
        第 11 張卡就這樣被切掉送出去了。獨立的第二道檢查是為了那個教訓。
        """
        fit = self.measure(card)
        title = card.get("title") or card.get("text") or card.get("angle") or card["type"]

        if fit["overflow"]:  # plan() 應該已經處理掉了，走到這裡是 bug
            raise PipelineError(
                ErrorCode.RENDER_OVERFLOW,
                f"{card['type']} 卡溢出且未被拆開：{title}",
                hint="layout.plan() 的拆卡邏輯有漏洞",
            )

        a = fit.get("audit") or {}
        if a.get("clipped"):
            w = a.get("worst") or {}
            raise PipelineError(
                ErrorCode.RENDER_OVERFLOW,
                f"{card['type']} 卡有元素被切掉（超出 {a['overBy']}px）：{title}\n"
                f"      元素：{w.get('tag')}｜「{w.get('text')}」",
                hint="autofit 漏判了。這張圖不出——寧可少一張，也不要發一張被切掉的圖",
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
    sync_playwright = sync_playwright_or_die()

    if ratio not in RATIOS:
        raise ValueError(f"未知的比例：{ratio}（可用：{list(RATIOS)}）")
    w, h = RATIOS[ratio]

    ensure_dirs(slug, post_index)
    out_dir = images_dir(slug, post_index)
    images: list[dict[str, Any]] = []

    with sync_playwright() as p:
        browser = launch_chromium(p)
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
        "author": src.get("author", ""),  # 選填：沒有作者的素材（課程、官方文件）不印署名
        "url": src.get("url", ""),
        "handle": os.environ.get("IG_HANDLE", "@your_handle"),
        "series": "",
    }

    out: list[Path] = []
    for i, post in enumerate(h["posts"], 1):
        d = images_dir(slug, i)
        existing = sorted(d.glob("*.png")) if d.exists() else []

        # **跳過的條件不是「有圖」，是「圖比所有輸入都新」。**
        # 輸入有三個：`highlights.json`（內容）、`templates/`（版型）、
        # **以及這個模組本身**（拆卡邏輯、字級門檻改了，圖一樣過期）。
        # **程式碼也是輸入**——漏掉它，你會拿到「用舊邏輯生成、看起來很新」的產物。
        oldest = min(existing, key=lambda p: p.stat().st_mtime) if existing else None
        stale = oldest is None or is_stale(
            oldest, highlights_path(slug), TEMPLATE_DIR, Path(__file__).parent
        )
        if not stale and not force:
            out.extend(existing)
            continue

        # 重出之前先清空。卡片從 9 張變 5 張時，舊的 `07_point_6.png` 會留在那裡
        # 變成孤兒——而你發文時很可能把它一起發出去。
        for old in existing:
            old.unlink()

        why = "" if not existing else "（圖比輸入舊，重出）"
        print(f"  第 {i} 則：{post['angle']} {why}")
        imgs = render_post(post, ctx, slug, i, ratio=ratio)
        out.extend(d / Path(m["path"]).name for m in imgs)
    return out


__all__ = ["render", "render_post", "Renderer", "RATIOS"]
