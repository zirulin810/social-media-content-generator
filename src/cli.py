"""端到端 CLI（骨架）。

目前只有參數介面與階段調度的殼；各階段的實作在對應模組裡，
串接與續跑邏輯由任務 [[端到端 CLI 串接]] 完成。

用法：
    python -m src.cli <文章.md> [--only STAGE] [--force] [--dry-run] [--ratio 4:5]
"""

from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path

from .errors import ErrorCode, PipelineError
from .paths import article_dir, slugify

STAGES = ("ingest", "analyze", "render", "compose")

# 各階段由哪張任務筆記負責實作
STAGE_OWNER = {
    "ingest": "文章讀取與正規化",
    "analyze": "重點分析與金句抽取",
    "render": "圖卡渲染器",
    "compose": "貼文文案產生器",
}


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="自動化輸出",
        description="文章 markdown → 重點金句 → 社群圖卡 → 貼文文案",
    )
    p.add_argument("article", help="本機文章 markdown 檔路徑")
    p.add_argument("--only", choices=STAGES, help="只跑單一階段（預設全跑）")
    p.add_argument("--force", action="store_true", help="重跑已有產物的階段（預設跳過）")
    p.add_argument(
        "--dry-run",
        action="store_true",
        help="只跑到 highlights，不出圖不寫文案（快速驗證分析品質）",
    )
    p.add_argument("--ratio", choices=("1:1", "4:5"), default="4:5", help="圖卡比例（預設 4:5）")
    return p


def run(args: argparse.Namespace) -> int:
    md_path = Path(args.article).expanduser()
    if not md_path.is_file():
        raise PipelineError(
            ErrorCode.SOURCE_NOT_FOUND,
            f"找不到檔案：{md_path}",
            hint="給一個存在的 .md 檔路徑",
        )

    slug = slugify(md_path.stem)
    print(f"來源：{md_path}")
    print(f"slug：{slug}")
    print(f"輸出目錄：{article_dir(slug)}")

    stages = [args.only] if args.only else list(STAGES)
    if args.dry_run:
        stages = [s for s in stages if s in ("ingest", "analyze")]

    for stage in stages:
        t0 = time.perf_counter()
        print(f"\n▶ {stage} …")
        print(f"  尚未實作，由任務筆記〈{STAGE_OWNER[stage]}〉負責。")
        print(f"  ({time.perf_counter() - t0:.2f}s)")

    print("\n骨架就位，契約已定（見 docs/spec.md）。各階段待各自的任務實作。")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        return run(args)
    except PipelineError as err:
        print(err.render(), file=sys.stderr)
        return 1
    except ValueError as err:
        print(f"參數錯誤：{err}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
