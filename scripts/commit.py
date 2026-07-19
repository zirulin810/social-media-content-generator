"""一鍵提交：存檔進 git 並推上遠端。訊息用時間戳＋變更檔數自動生成。

    python scripts/commit.py            （或雙擊「tools/提交.bat」）
    python scripts/commit.py 自訂訊息    帶參數就用你給的訊息

流程：add -A → commit → push。沒有變更就只 push（把上次沒推成功的補推上去）。
push 前會擋機密：追蹤清單裡若混進 .env / settings.json / .browser_profile 就中止。

第一次在新資料夾用之前，git 要先有身分（用 GitHub 的 noreply 信箱，別用私人信箱）：
    git config user.name  "你的名字"
    git config user.email "<ID>+<帳號>@users.noreply.github.com"
"""

from __future__ import annotations

import subprocess
import sys
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

# 這些絕不該進版控——push 前最後一道防線
SECRETS = (".env", "settings.json", ".browser_profile")


def git(*args: str) -> subprocess.CompletedProcess:
    return subprocess.run(["git", *args], cwd=ROOT, capture_output=True, text=True,
                          encoding="utf-8", errors="replace", check=False)


def main(argv: list[str]) -> int:
    if git("rev-parse", "--git-dir").returncode != 0:
        print("✗ 這裡還不是 git 倉庫。先 git init，並設好 user.name / user.email。")
        return 1

    # 機密防線：任何被追蹤的機密檔一律中止
    tracked = git("ls-files").stdout.splitlines()
    leaked = [f for f in tracked if any(f == s or f.startswith(s + "/") for s in SECRETS)]
    if leaked:
        print("✗ 這些機密檔被 git 追蹤了，不能提交：")
        for f in leaked:
            print(f"    {f}")
        print("  先移出追蹤：git rm --cached <檔案>（檔案本身留著），再提交。")
        return 1

    status = git("status", "--porcelain").stdout.strip()
    if status:
        n = len(status.splitlines())
        msg = " ".join(argv).strip() or f"存檔 {datetime.now():%Y-%m-%d %H:%M}（{n} 個檔案）"
        git("add", "-A")
        r = git("commit", "-m", msg)
        if r.returncode != 0:
            print("✗ commit 失敗：")
            print((r.stderr or r.stdout).strip())
            if "user.email" in (r.stderr or "") or "user.name" in (r.stderr or ""):
                print("\n  git 還不知道你是誰。設定一次（用 GitHub noreply 信箱）：")
                print('    git config user.name  "你的名字"')
                print('    git config user.email "<ID>+<帳號>@users.noreply.github.com"')
            return 1
        print(f"✓ 已存檔：{msg}")
    else:
        print("沒有新變更——直接把本機的 commit 推上遠端。")

    # push
    branch = git("rev-parse", "--abbrev-ref", "HEAD").stdout.strip()
    r = git("push")
    if r.returncode != 0:
        err = (r.stderr or r.stdout)
        if "no upstream branch" in err:
            print(f"  第一次推這個分支，設定 upstream…")
            r = git("push", "--set-upstream", "origin", branch)
    if r.returncode != 0:
        print("✗ push 失敗（commit 已存在本機，可稍後再推）：")
        print((r.stderr or r.stdout).strip())
        return 1

    print(f"✓ 已推上遠端（{branch}）")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
