"""發布的勞動層：開系統瀏覽器、代填圖與文案。實作任務：[[一鍵發布到 IG 與 Threads]]

**紅線：程式永遠不按「分享／發佈」。** 那一鍵是人的——這是方案一的定義。
這條紅線不是靠自律，是靠機制：所有點擊都走 `_click()`，它會擋下任何
帶著發布字眼的目標（見 NEVER_CLICK）。tests/test_publish.py 釘著這件事。

其他刻意的選擇：
- **持久化 profile**（PROJECT_ROOT/.browser_profile/，已 gitignore）：
  第一次開會是未登入狀態，人自己登入（含 2FA），cookie 記住，之後不必重登。
  程式從頭到尾**碰不到帳號密碼**。
- **selector 全部集中在 SELECTORS**：IG／Threads 改版就改這一張表，不用讀流程。
  每個 selector 都是逗號分隔的候選清單（中英文介面都認），第一個找到的贏。
- 沿用 render/browser.py 的慣例：**只用系統的 Edge／Chrome，不下載**。
"""

from __future__ import annotations

import time
from pathlib import Path
from typing import Any

from .errors import ErrorCode, PipelineError
from .paths import PROJECT_ROOT
from .render.browser import CHANNELS, NOT_FOUND_HINT, sync_playwright_or_die

PROFILE_DIR = PROJECT_ROOT / ".browser_profile"

# ---------------------------------------------------------------------------
# 紅線的機制面：**這些字眼的按鈕，_click() 一律拒絕。**
# 就算未來有人改流程、加步驟，只要走 _click() 就撞牆；繞過 _click() 會被測試抓到。
# ---------------------------------------------------------------------------
NEVER_CLICK = ("分享", "發佈", "发布", "Share", "Post", "Publish")

# selector 候選表（改版時只改這裡）。key: (平台, 步驟)
SELECTORS: dict[tuple[str, str], str] = {
    # --- Instagram ---
    ("instagram", "logged_out"): 'input[name="username"]',
    ("instagram", "create"): (
        'svg[aria-label="新增貼文"], svg[aria-label="New post"], '
        'a[href="/create/select/"], svg[aria-label="新貼文"]'
    ),
    ("instagram", "from_computer"): (
        'button:has-text("從電腦選擇"), button:has-text("Select from computer")'
    ),
    ("instagram", "next"): 'div[role="dialog"] :text-is("下一步"), div[role="dialog"] :text-is("Next")',
    ("instagram", "caption"): (
        'div[role="dialog"] div[aria-label*="撰寫"], div[role="dialog"] div[aria-label*="Write"], '
        'div[role="dialog"] div[contenteditable="true"]'
    ),
    # --- Threads ---
    ("threads", "logged_out"): 'input[autocomplete="username"], input[name="username"]',
    ("threads", "compose"): (
        'svg[aria-label="建立"], svg[aria-label="Create"], svg[aria-label="新串文"], '
        'a[href="#"] svg[aria-label="More"]'
    ),
    ("threads", "caption"): (
        'div[contenteditable="true"][role="textbox"], div[contenteditable="true"]'
    ),
    ("threads", "attach"): (
        'svg[aria-label="附加媒體"], svg[aria-label="Attach media"], input[type="file"]'
    ),
}

LOGIN_WAIT_SECONDS = 600  # 第一次使用：給人十分鐘登入（含 2FA）。等的是「發文入口出現」，不是猜


def _forbidden_word(sel: str) -> str | None:
    """selector 是不是在指名一顆發布鍵？

    看的是**整個目標值相等**，不是子字串——IG 的建立鍵叫「New post」，
    含有 Post 但不是 Post；Threads 的發布鍵就叫「Post」，指名它就是越線。
    """
    import re as _re

    for quoted in _re.findall(r"[\"']([^\"']+)[\"']", sel):
        for word in NEVER_CLICK:
            if quoted.strip().lower() == word.lower():
                return word
    return None


def _click(page, platform: str, step: str) -> None:
    """所有點擊的唯一入口。目標指名發布鍵 → 直接拒絕（紅線）。"""
    sel = SELECTORS[(platform, step)]
    word = _forbidden_word(sel)
    if word:
        raise PipelineError(
            ErrorCode.MISSING_INPUT,
            f"紅線：程式不准點「{word}」——分享鍵是人的",
        )
    el = page.locator(sel).first
    el.wait_for(state="visible", timeout=15000)
    el.click()


def _wait_for_ready(page, platform: str, ready_step: str, say) -> None:
    """等到**發文入口真的出現**才往下走。

    原本的做法是「4 秒沒看到登入框就當作已登入，然後只給發文鍵 15 秒」——
    登入畫面長得跟 selector 不一樣、cookie 牆、跳轉慢，全都會在 15 秒內翻車
    （Human 2026-07-15 實測回報）。所以改成不猜：

        看到發文入口 → 繼續
        看到登入畫面 → 提示人登入（含 2FA），**繼續等**
        兩個都沒有   → 頁面還在載入或有別的牆，**繼續等**

    上限 {LOGIN_WAIT} 分鐘。程式從頭到尾不碰帳號密碼，只是等。
    """
    ready = SELECTORS[(platform, ready_step)]
    out = SELECTORS[(platform, "logged_out")]
    deadline = time.time() + LOGIN_WAIT_SECONDS
    told = False
    while time.time() < deadline:
        try:
            if page.locator(ready).first.is_visible():
                time.sleep(1)  # 跳轉後的緩衝
                return
        except Exception:  # noqa: BLE001 — 頁面跳轉中 locator 會炸，等下一輪
            pass
        try:
            if not told and page.locator(out).first.is_visible():
                say(f"請在打開的視窗登入 {platform}（含兩步驟驗證）——我會等你，最多 {LOGIN_WAIT_SECONDS // 60} 分鐘")
                told = True
        except Exception:  # noqa: BLE001
            pass
        time.sleep(1)
    raise PipelineError(
        ErrorCode.MISSING_INPUT,
        f"{platform}：等了 {LOGIN_WAIT_SECONDS // 60} 分鐘仍看不到發文入口",
        hint="可能已登入但平台改版（改 src/publish_web.py 的 SELECTORS），先用備援手動發",
    )


def _launch_persistent(p):
    """持久化 context：登入狀態記在 .browser_profile/。只用系統瀏覽器。"""
    import os

    forced = os.environ.get("CARD_BROWSER")
    candidates = [forced] if forced and forced != "bundled" else list(CHANNELS)
    tried = []
    PROFILE_DIR.mkdir(exist_ok=True)
    for channel in candidates:
        try:
            return p.chromium.launch_persistent_context(
                str(PROFILE_DIR), channel=channel, headless=False,
                viewport=None, args=["--start-maximized"],
            )
        except Exception as e:  # noqa: BLE001
            tried.append(f"{channel}: {str(e).splitlines()[0][:56]}")
    if forced == "bundled":
        return p.chromium.launch_persistent_context(str(PROFILE_DIR), headless=False)
    raise PipelineError(
        ErrorCode.MISSING_INPUT,
        "找不到可用的系統瀏覽器\n      試過：\n      " + "\n      ".join(tried),
        hint=NOT_FOUND_HINT,
    )


def _prefill_instagram(page, payload: dict[str, Any], say) -> None:
    page.goto(payload["url"], wait_until="domcontentloaded")
    _wait_for_ready(page, "instagram", "create", say)
    _click(page, "instagram", "create")
    # 上傳圖片：IG 的選檔按鈕會開 file chooser
    with page.expect_file_chooser(timeout=15000) as fc:
        try:
            _click(page, "instagram", "from_computer")
        except Exception:  # noqa: BLE001 — 有些版本一進來就是 file input
            pass
    fc.value.set_files(payload["images"])
    # 兩次「下一步」：裁切 → 濾鏡 → 文案（**「下一步」不是發布，可以按**）
    for _ in range(2):
        _click(page, "instagram", "next")
        page.wait_for_timeout(800)
    page.locator(SELECTORS[("instagram", "caption")]).first.click()
    page.keyboard.insert_text(payload["caption"])
    # 到此為止。**「分享」由人按。**


def _prefill_threads(page, payload: dict[str, Any], say) -> None:
    page.goto(payload["url"], wait_until="domcontentloaded")
    _wait_for_ready(page, "threads", "compose", say)
    _click(page, "threads", "compose")
    box = page.locator(SELECTORS[("threads", "caption")]).first
    box.wait_for(state="visible", timeout=15000)
    box.click()
    page.keyboard.insert_text(payload["caption"])
    with page.expect_file_chooser(timeout=15000) as fc:
        _click(page, "threads", "attach")
    fc.value.set_files(payload["images"])
    # 到此為止。**「發佈」由人按。**


def prefill(payload: dict[str, Any], on_stage=None) -> None:
    """開瀏覽器 → 代填 → **停住等人**。人關掉視窗，這個函式才回來。

    on_stage(訊息)：進度回報（給編輯台的工作狀態列用）。
    """
    say = on_stage or (lambda _msg: None)
    sync_playwright = sync_playwright_or_die()
    with sync_playwright() as p:
        say("開瀏覽器（第一次會需要你登入）")
        ctx = _launch_persistent(p)
        page = ctx.pages[0] if ctx.pages else ctx.new_page()
        try:
            say("開啟平台頁面（未登入的話會等你登入）")
            if payload["platform"] == "instagram":
                _prefill_instagram(page, payload, say)
            else:
                _prefill_threads(page, payload, say)
            say("已就位——過目後請自己按「分享」；發完回編輯台標「已發布」")
        except PipelineError:
            raise
        except Exception as e:  # noqa: BLE001 — 網站改版等。講清楚，讓人走備援
            raise PipelineError(
                ErrorCode.MISSING_INPUT,
                f"代填失敗（{payload['platform']}）：{str(e).splitlines()[0][:120]}",
                hint="平台可能改版了（改 src/publish_web.py 的 SELECTORS）。"
                "先用備援：複製文案＋開圖片資料夾，手動發",
            ) from e
        # 停住，直到人把瀏覽器關掉——不能提早收工把視窗帶走
        try:
            while ctx.pages:
                time.sleep(1)
        except Exception:  # noqa: BLE001 — 視窗被關、context 死了，都算正常結束
            pass


__all__ = ["prefill", "SELECTORS", "NEVER_CLICK", "PROFILE_DIR"]
