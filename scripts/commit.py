"""存檔：把目前的改動提交進 git，訊息用時間戳自動生成。

    提交.bat   （或 python scripts/commit.py）
    python scripts/commit.py "自己寫的訊息"

**這不是在寫改動說明，是在存檔。** 訊息長這樣：

    存檔 2026-07-14 18:32（12 個檔案）

想寫像樣的說明，就自己下 `git commit`——這支的用途是「先存起來再說」。
（第一版我讓模型讀 diff 寫 commit message，那是**把一件簡單的事做複雜了**。
Human：「我是指你可以用日期時間之類的隨便生成一個 message 就好」。）

**在你的機器上跑，不在 AI 的沙箱跑。** 沙箱的掛載層讀到的檔案是過期的——
用它 `git add`，很可能把舊版甚至截斷的內容提交進去。
**那比沒有 git 更糟：你會以為那份存檔是好的。**
"""

from __future__ import annotations

import subprocess
import sys
import tempfile
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.paths import PROJECT_ROOT  # noqa: E402


def git(*args: str, check: bool = True) -> str:
    r = subprocess.run(
        ["git", *args],
        cwd=PROJECT_ROOT,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
    )
    if check and r.returncode:
        raise RuntimeError(f"git {' '.join(args)}\n{r.stderr.strip()}")
    return (r.stdout or "").strip()


def main() -> int:
    try:
        git("--version")
    except FileNotFoundError:
        print("✗ 沒有安裝 git：https://git-scm.com/download/win")
        return 1

    if not (PROJECT_ROOT / ".git").exists():
        print("✗ 這裡還不是 git repo —— 先跑「初始化Git.bat」")
        return 1

    status = git("status", "--short")
    if not status:
        print("沒有任何變更，不用提交。")
        return 0

    print("=" * 66)
    print("  這次存檔的內容")
    print("=" * 66)
    git("add", "-A")
    print(git("diff", "--cached", "--stat"))

    n = len([ln for ln in git("diff", "--cached", "--name-only").splitlines() if ln.strip()])
    msg = " ".join(sys.argv[1:]).strip() or f"存檔 {datetime.now():%Y-%m-%d %H:%M}（{n} 個檔案）"

    # **訊息走檔案，不走命令列**：Windows 的 cmd 傳中文給 git 會變成亂碼。
    with tempfile.NamedTemporaryFile("w", suffix=".txt", delete=False, encoding="utf-8") as f:
        f.write(msg + "\n")
        path = f.name
    try:
        git("config", "i18n.commitEncoding", "utf-8")
        git("commit", "-F", path)
    finally:
        Path(path).unlink(missing_ok=True)

    print("\n" + "=" * 66)
    print(git("log", "--oneline", "-5"))
    print("=" * 66)
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except RuntimeError as e:
        print(f"\n✗ {e}")
        raise SystemExit(1)
