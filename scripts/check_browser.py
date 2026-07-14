"""查一下：這台電腦有哪些瀏覽器可以用，以及 Playwright 到底偷偷下載了多少東西。

    python scripts/check_browser.py
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.errors import PipelineError  # noqa: E402
from src.render.browser import CHANNELS, sync_playwright_or_die  # noqa: E402


def human(n: float) -> str:
    for unit in ("B", "KB", "MB", "GB"):
        if n < 1024:
            return f"{n:.0f} {unit}"
        n /= 1024
    return f"{n:.1f} TB"


def main() -> int:
    sync_playwright = sync_playwright_or_die()

    print("=" * 60)
    print("  這台電腦上，Playwright 用得動哪些瀏覽器？")
    print("=" * 60 + "\n")

    usable = []
    with sync_playwright() as p:
        for channel in CHANNELS:
            try:
                b = p.chromium.launch(channel=channel)
                ver = b.version
                b.close()
                print(f"  ✓ 系統的 {channel:<16} {ver}")
                usable.append(channel)
            except Exception:  # noqa: BLE001
                print(f"  ✗ 系統的 {channel:<16} 沒有")

        try:
            b = p.chromium.launch()
            print(f"  · Playwright 下載的     {b.version}  （**我們不用它**）")
            b.close()
        except Exception:  # noqa: BLE001
            print("  · Playwright 下載的     沒有  （很好，本來就不需要）")

    print()
    if usable:
        print(f"  → 會用：系統的 {usable[0]}。**不需要下載任何東西。**")
    else:
        print("  → 找不到系統瀏覽器。Windows 通常內建 Edge——被移除了嗎？")

    # Playwright 偷偷下載了多少？
    cache = Path(os.environ.get("LOCALAPPDATA", Path.home())) / "ms-playwright"
    if cache.exists() and any(cache.iterdir()):
        total = 0
        rows = []
        for d in sorted(cache.iterdir()):
            if not d.is_dir():
                continue
            size = sum(f.stat().st_size for f in d.rglob("*") if f.is_file())
            total += size
            rows.append((d.name, size))

        if rows:
            print(f"\n  Playwright 下載的瀏覽器快取：{cache}")
            for name, size in rows:
                print(f"    {name:<46} {human(size):>9}")
            print(f"    {'合計':<46} {human(total):>9}")
            print("\n  這些**我們一個都用不到**（除非你設 CARD_BROWSER=bundled）。")
            print("  清掉：雙擊「清除下載的瀏覽器.bat」，或 python -m playwright uninstall --all")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except PipelineError as e:
        print(f"\n✗ {e.render()}")
        raise SystemExit(1) from None
