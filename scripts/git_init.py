"""把這個專案放進 git —— 在你的機器上跑，不在 AI 的沙箱裡跑。

    初始化Git.bat   （或 python scripts/git_init.py）

**為什麼由你跑**：AI 的沙箱掛載層寫不出健全的 `.git`（實測 config 讀回來是壞的）。
git 的檔案操作對檔案系統的一致性很敏感，隔一層網路掛載就不可靠。

**為什麼現在才做（該罵）**：
`.gitignore` 從第一天就寫好了，卻沒人跑 `git init`——它躺在那裡純粹是裝飾。
這段期間我改壞過東西（三張筆記被截斷、schema 從 v1 覆蓋到 v3），
有 git 的話那些全都是一個 `git checkout` 的事，卻是靠人工重建。
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def git(*args: str, check: bool = True) -> str:
    r = subprocess.run(
        ["git", *args], cwd=ROOT, capture_output=True, text=True, encoding="utf-8", errors="replace"
    )
    if check and r.returncode:
        raise RuntimeError(f"git {' '.join(args)}\n{r.stderr.strip()}")
    return (r.stdout or "").strip()


def main() -> int:
    print("=" * 62)
    print("  自動化輸出 — 初始化 git")
    print("=" * 62)

    try:
        print(f"\n{git('--version')}")
    except FileNotFoundError:
        print("\n✗ 沒有安裝 git。")
        print("  下載：https://git-scm.com/download/win")
        print("  裝完重開這個視窗再跑一次。")
        return 1

    if (ROOT / ".git").exists():
        print(f"\n已經是 git repo 了。目前狀態：\n")
        print(git("status", "--short") or "  （沒有未提交的變更）")
        return 0

    print(f"\n[1/4] git init → {ROOT}")
    git("init", "-b", "main")

    # 沒設過身分的話，用專案內設定，不動全域
    if not git("config", "user.email", check=False):
        print("[2/4] 設定提交者（只在這個專案內，不動你的全域設定）")
        git("config", "user.name", "HaMiGua")
        git("config", "user.email", "meow920810@gmail.com")
    else:
        print(f"[2/4] 已有提交者身分：{git('config', 'user.name')} <{git('config', 'user.email')}>")

    print("[3/4] 加入檔案（.venv、out、.env 已在 .gitignore 排除）")
    git("add", "-A")
    files = [ln for ln in git("status", "--short").splitlines() if ln.strip()]
    print(f"      {len(files)} 個檔案")

    print("[4/4] 第一次提交")
    git(
        "commit",
        "-m",
        "初始提交：文章 -> 知識卡 -> IG 圖卡 pipeline\n\n"
        "- 契約 v3.0（docs/spec.md）：知識卡取代金句，每條主張帶 evidence\n"
        "- ingest：Web Clipper 剪報正規化（剝時間戳、樣板、簡繁偵測）\n"
        "- analyze：Gemini 抽知識卡，schema 不合就把錯誤餵回去請它修\n"
        "- render：autofit 字級 + 塞不下就拆卡（不砍內容）\n"
        "- templates：兩個主題（深色螢光 / 編輯大字）\n"
        "- 84 條測試",
    )

    print("\n" + "=" * 62)
    print("  好了。之後：")
    print("    git log --oneline        看歷史")
    print("    git diff                 看改了什麼")
    print("    git checkout -- <檔案>   把改壞的檔案救回來  ← 這個最重要")
    print("=" * 62)
    print(f"\n  {git('log', '--oneline')}")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except RuntimeError as e:
        print(f"\n✗ {e}")
        raise SystemExit(1)
