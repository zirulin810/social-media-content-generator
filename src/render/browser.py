"""開瀏覽器這件事，只在這裡做一次。

**只用系統既有的瀏覽器。不下載。**

Playwright 預設會自己下載一顆瀏覽器，理由是「保證你我的渲染結果一模一樣」。
那個保證對團隊/CI 有價值，對一個人的專案沒有——而代價是：

  - 每次 playwright 升版就重載一組（舊的不會自己刪，堆在 %LOCALAPPDATA%\\ms-playwright\\）
  - `playwright install`（不帶參數）會裝 Chromium + Firefox + WebKit **三顆**，快 1GB

Windows 一定有 Edge，多數人也有 Chrome，兩者都是 Chromium 核心，
排版能力跟 Playwright 自帶的那顆一模一樣。所以：**用你已經有的。**

找不到就報錯，**不要偷偷下載**——Human 2026-07-14 的決定。
下載那條路是「AI 為了保險而重複安裝你已經有的東西」，成本是使用者在付。
"""

from __future__ import annotations

import os

from ..errors import ErrorCode, PipelineError

# 只找系統既有的。**沒有 bundled chromium 這個 fallback，這是刻意的。**
CHANNELS = ("msedge", "chrome", "chrome-beta", "msedge-beta")

NOT_FOUND_HINT = (
    "這台電腦上找不到 Edge 或 Chrome。三選一：\n"
    "        1. 裝 Edge 或 Chrome（Windows 通常內建 Edge，可能是被移除了）\n"
    "        2. 指定其他 Chromium 核心的瀏覽器：set CARD_BROWSER=chrome-beta\n"
    "        3. 真的要用 Playwright 自己下載的那顆（約 150MB）：\n"
    "             python -m playwright install chromium\n"
    "             set CARD_BROWSER=bundled\n"
    "\n"
    "      **不要**跑不帶參數的 `playwright install`——那會裝三顆瀏覽器，我們只用一顆。"
)


def launch_chromium(p, verbose: bool = True):
    """開一顆系統既有的 Chromium 核心瀏覽器。找不到就報錯，不下載。

    `CARD_BROWSER=bundled` 才會用 Playwright 自己下載的那顆——要明講才給。
    """
    forced = os.environ.get("CARD_BROWSER")

    if forced == "bundled":
        b = p.chromium.launch()
        if verbose:
            print("  瀏覽器：Playwright 自帶的 Chromium（你指定的）")
        return b

    candidates = [forced] if forced else list(CHANNELS)
    tried = []

    for channel in candidates:
        try:
            b = p.chromium.launch(channel=channel)
            if verbose:
                print(f"  瀏覽器：系統的 {channel}（沒有下載任何東西）")
            return b
        except Exception as e:  # noqa: BLE001 — Playwright 的錯誤型別不穩定
            tried.append(f"{channel}: {str(e).splitlines()[0][:56]}")

    raise PipelineError(
        ErrorCode.MISSING_INPUT,
        "找不到可用的系統瀏覽器\n      試過：\n      " + "\n      ".join(tried),
        hint=NOT_FOUND_HINT,
    )


def sync_playwright_or_die():
    try:
        from playwright.sync_api import sync_playwright
    except ImportError as e:
        raise PipelineError(
            ErrorCode.MISSING_INPUT,
            "沒有安裝 playwright",
            hint="pip install -r requirements.txt",
        ) from e
    return sync_playwright


__all__ = ["launch_chromium", "sync_playwright_or_die", "NOT_FOUND_HINT", "CHANNELS"]
