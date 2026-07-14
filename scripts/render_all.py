"""把已分析的素材全部出成圖卡。

    python scripts/render_all.py            （或雙擊「出圖.bat」）
    python scripts/render_all.py --force    重出（預設會跳過已有圖的貼文）
    python scripts/render_all.py --ratio 4x5

吃的是 `out/<slug>/highlights.json`——**你自己跑出來的真東西**，不是 samples/ 裡我手寫的樣本。
（樣本仍在：`scripts/render_sample.py`，那是版型的實驗場，不是產線。）

一篇文章 → 1–3 則貼文 → 每則一整組 PNG，落在 `out/<slug>/p<N>/images/`。
"""

from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.errors import PipelineError  # noqa: E402
from src.paths import PROJECT_ROOT, highlights_path, out_root  # noqa: E402
from src.render.render_cards import render  # noqa: E402
from src.schema import read_json  # noqa: E402


def slugs_with_highlights() -> list[str]:
    root = out_root()
    if not root.is_dir():
        return []
    return sorted(
        d.name for d in root.iterdir()
        if d.is_dir() and not d.name.startswith("_") and highlights_path(d.name).is_file()
    )


def main() -> int:
    ap = argparse.ArgumentParser(description="把 out/ 裡所有已分析的素材出成圖卡")
    ap.add_argument("--ratio", choices=("1x1", "4x5"), default="1x1")
    ap.add_argument("--force", action="store_true", help="已經有圖了也重出")
    ap.add_argument("slug", nargs="?", help="只出這一篇（預設全部）")
    args = ap.parse_args()

    todo = [args.slug] if args.slug else slugs_with_highlights()
    if not todo:
        print("out/ 裡沒有任何 highlights.json——先跑「分析全部素材.bat」")
        return 1

    print(f"要出圖的素材：{len(todo)} 篇｜比例 {args.ratio}\n")

    failed: list[str] = []
    for slug in todo:
        h = read_json("highlights", highlights_path(slug))
        title = h["source"]["title"]
        print("=" * 74)
        print(f"  {title[:60]}")
        print("=" * 74)
        t0 = time.time()
        try:
            paths = render(slug, ratio=args.ratio, force=args.force)
        except PipelineError as e:
            # 一篇出不來不該讓其他篇陪葬——記下來，繼續跑
            print(f"✗ {e.render()}\n")
            failed.append(slug)
            continue
        print(f"  共 {len(paths)} 張，{time.time() - t0:.1f}s → out/{slug}/\n")

    print("=" * 74)
    if failed:
        print(f"  {len(todo) - len(failed)}/{len(todo)} 篇出圖完成；失敗：{', '.join(failed)}")
    else:
        print(f"  {len(todo)} 篇全部出圖完成 → {out_root()}")
    print("=" * 74)
    return 1 if failed else 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except PipelineError as e:
        print(f"\n✗ {e.render()}")
        raise SystemExit(1) from None
