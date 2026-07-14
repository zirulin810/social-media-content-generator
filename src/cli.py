"""端到端 CLI：一支指令，一篇文章 → 圖卡 + 文案。

    python -m src.cli "Clippings\\某篇文章.md"
    python -m src.cli "Clippings\\某篇文章.md" --only analyze
    python -m src.cli "Clippings\\*.md"                    # 整批
    python -m src.cli "Clippings\\某篇.md" --dry-run       # 只到知識卡，先看內容品質

實作任務：[[端到端 CLI 串接]]

**設計原則：這支 CLI 不做任何決定。**

它只負責「按順序呼叫四個階段、把錯誤講清楚、告訴你東西在哪」。
所有的判斷都在各階段裡：要不要重跑（`is_stale`）、拆不拆卡、退不退回重寫。
**CLI 是接線員，不是主管。** 這樣一來，`.bat` 跑得到的東西，CLI 一定跑得到，
不會出現「兩條路徑行為不一致」這種最難查的 bug。
"""

from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path
from typing import Any

from .errors import ErrorCode, PipelineError
from .paths import (
    PROMPT_DIR,
    TEMPLATE_DIR,
    article_dir,
    article_path,
    highlights_path,
    images_dir,
    is_stale,
    post_path,
    slugify,
)

STAGES = ("ingest", "analyze", "render", "compose")

STAGE_LABEL = {
    "ingest": "讀取正規化",
    "analyze": "抽知識卡",
    "render": "出圖卡",
    "compose": "寫文案",
}


def _stage_is_fresh(stage: str, slug: str, n_posts: int) -> bool:
    """這個階段的產物還新鮮嗎？**判斷邏輯跟各階段自己用的是同一套**（`paths.is_stale`）。

    這裡只是為了「印出 ↩ 沿用 / ✎ 重跑」——真正決定跳不跳過的，仍然是各階段自己。
    **不要在這裡另外實作一份跳過邏輯**，那會變成第二份事實來源。
    """
    src = Path(__file__).parent
    if stage == "ingest":
        return not is_stale(article_path(slug), src / "ingest")
    if stage == "analyze":
        return not is_stale(highlights_path(slug), article_path(slug), PROMPT_DIR, src / "analyze")
    if stage == "render":
        for i in range(1, n_posts + 1):
            pngs = sorted(images_dir(slug, i).glob("*.png"))
            if not pngs:
                return False
            oldest = min(pngs, key=lambda p: p.stat().st_mtime)
            if is_stale(oldest, highlights_path(slug), TEMPLATE_DIR, src / "render"):
                return False
        return n_posts > 0
    if stage == "compose":
        for i in range(1, n_posts + 1):
            if is_stale(
                post_path(slug, i),
                highlights_path(slug),
                images_dir(slug, i),
                PROMPT_DIR,
                src / "compose",
            ):
                return False
        return n_posts > 0
    return False


def _n_posts(slug: str) -> int:
    from .schema import read_json

    if not highlights_path(slug).is_file():
        return 0
    return len(read_json("highlights", highlights_path(slug))["posts"])


def run_one(md_path: Path, args: argparse.Namespace) -> dict[str, Any]:
    """跑一篇文章。回傳這一篇的結果摘要（給批次模式彙總用）。"""
    from .analyze.extract_highlights import extract
    from .compose.write_post import compose
    from .ingest.read_article import read
    from .render.render_cards import render

    slug = slugify(md_path.stem)

    print("\n" + "=" * 74)
    print(f"  {md_path.name}")
    print(f"  → out/{slug}/")
    print("=" * 74)

    stages = [args.only] if args.only else list(STAGES)
    if args.dry_run:
        stages = [s for s in stages if s in ("ingest", "analyze")]

    for stage in stages:
        fresh = not args.force and _stage_is_fresh(stage, slug, _n_posts(slug))
        t0 = time.perf_counter()

        if stage == "ingest":
            read(md_path, force=args.force)
        elif stage == "analyze":
            extract(slug, force=args.force)
        elif stage == "render":
            render(slug, ratio=args.ratio.replace(":", "x"), force=args.force)
        elif stage == "compose":
            compose(slug, force=args.force)

        mark = "↩ 沿用" if fresh else f"✎ 跑完（{time.perf_counter() - t0:.1f}s）"
        print(f"  {STAGE_LABEL[stage]:<6} {mark}")

    n = _n_posts(slug)
    pngs = sum(len(list(images_dir(slug, i).glob("*.png"))) for i in range(1, n + 1))
    posts = sum(1 for i in range(1, n + 1) if post_path(slug, i).is_file())
    return {"slug": slug, "posts": n, "images": pngs, "captions": posts}


def _collect(article: str | None) -> list[Path]:
    """要跑哪些檔案？

    **沒給檔案就跑整個素材資料夾**——一支叫「跑一篇」的東西，雙擊它應該要做事，
    而不是罵你少給參數。
    """
    if not article:
        src = source_dir()
        if not src.is_dir():
            raise PipelineError(
                ErrorCode.SOURCE_NOT_FOUND,
                f"找不到素材資料夾：{src}",
                hint="把 .md 拖到這個 .bat 上，或設定 SOURCE_DIR 環境變數",
            )
        files = sorted(src.glob("*.md"))
        if not files:
            raise PipelineError(
                ErrorCode.SOURCE_NOT_FOUND,
                f"{src} 裡沒有任何 .md",
                hint="用 Obsidian 的 Web Clipper 剪幾篇文章進來",
            )
        print(f"素材資料夾：{src}（{len(files)} 篇）")
        return files

    # glob 展開：Windows 的 cmd 不會幫你展開萬用字元，所以自己來
    raw = Path(article).expanduser()
    files = sorted(raw.parent.glob(raw.name)) if any(c in str(raw) for c in "*?") else [raw]
    if not files or not all(f.is_file() for f in files):
        raise PipelineError(
            ErrorCode.SOURCE_NOT_FOUND,
            f"找不到檔案：{article}",
            hint='給一個存在的 .md 檔路徑（可用萬用字元："Clippings\\*.md"），或不給參數＝跑整個素材資料夾',
        )
    return files


def run(args: argparse.Namespace) -> int:
    files = _collect(args.article)

    results: list[dict[str, Any]] = []
    failed: list[tuple[str, PipelineError]] = []

    for f in files:
        try:
            results.append(run_one(f, args))
        except PipelineError as e:
            # **一篇壞掉不該讓整批陪葬**——記下來，繼續跑下一篇。
            print(f"\n✗ {e.render()}")
            failed.append((f.name, e))

    print("\n" + "=" * 74)
    for r in results:
        print(
            f"  {r['slug'][:44]:<46}"
            f"{r['posts']} 則貼文 · {r['images']} 張圖 · {r['captions']} 份文案"
        )
    if failed:
        print(f"\n  ✗ {len(failed)} 篇失敗：")
        for name, e in failed:
            print(f"      {name}：{e.message.splitlines()[0]}")
    print("=" * 74)

    if results and not args.dry_run:
        print(f"\n  產物在：{article_dir(results[0]['slug']).parent}")
        print("  下一步：看一眼圖卡與文案，你敢不敢用自己的名字發出去。")

    return 1 if failed else 0


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="自動化輸出",
        description="一篇文章 → 知識卡 → 社群圖卡 → 貼文文案",
    )
    p.add_argument("article", help='本機 .md 檔路徑（可用萬用字元："Clippings\\*.md"）')
    p.add_argument("--only", choices=STAGES, help="只跑單一階段（預設全跑）")
    p.add_argument(
        "--force",
        action="store_true",
        help="重跑所有階段（預設：產物比輸入新就沿用）",
    )
    p.add_argument(
        "--dry-run",
        action="store_true",
        help="只跑到知識卡，不出圖不寫文案（先看內容品質）",
    )
    p.add_argument("--ratio", choices=("1:1", "4:5"), default="1:1", help="圖卡比例（預設 1:1）")
    return p


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        return run(args)
    except PipelineError as err:
        print(err.render(), file=sys.stderr)
        return 1
    except KeyboardInterrupt:
        print("\n中斷了。已完成的階段不會重跑。", file=sys.stderr)
        return 130


if __name__ == "__main__":
    raise SystemExit(main())
