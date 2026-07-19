"""編輯台的本機伺服器（雙擊「編輯台.bat」啟動）。實作任務：[[發布前預覽介面]]

    python scripts/editor_server.py          # 開在 http://127.0.0.1:8765/

**不引進新的執行期依賴**：整支只用標準庫（http.server）。
畫面在 templates/editor.html；資料層的決策全在 src/editor.py——
這支只是接線員：收請求、叫對的函式、把錯誤講清楚。

只綁 127.0.0.1。這是單人本機工具，不對外。
"""

from __future__ import annotations

import json
import os
import re
import sys
import threading
import time
import traceback
import uuid
import webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, unquote, urlparse

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src import settings  # noqa: E402
from src.errors import PipelineError  # noqa: E402
from src.paths import (  # noqa: E402
    PROJECT_ROOT,
    article_path,
    highlights_path,
    out_root,
    post_path,
    slugify,
)

# .env（沒有就算了——key 可能本來就是系統環境變數）
def _load_env() -> None:
    env = PROJECT_ROOT / ".env"
    if not env.is_file():
        return
    for line in env.read_text(encoding="utf-8").splitlines():
        line = line.split("#", 1)[0].strip()
        if "=" in line:
            k, v = line.split("=", 1)
            os.environ.setdefault(k.strip(), v.strip())


_load_env()

PORT = int(os.environ.get("EDITOR_PORT", "8765"))

# 素材從面板拖進來時落地在這裡（`_` 開頭＝其他批次腳本會跳過它）
INBOX = "_inbox"

CONFIG_KEYS = ("handle", "theme", "ratio")
CONFIG_DEFAULT = {"handle": os.environ.get("IG_HANDLE", "@your_handle"),
                  "theme": "b", "ratio": "1x1"}

MIME = {".html": "text/html; charset=utf-8", ".css": "text/css; charset=utf-8",
        ".js": "text/javascript; charset=utf-8", ".json": "application/json; charset=utf-8",
        ".png": "image/png", ".svg": "image/svg+xml", ".md": "text/plain; charset=utf-8"}

# ---------------------------------------------------------------------------
# 背景工作（分析要叫 LLM，幾十秒起跳——不能讓 HTTP 請求掛著等）
# ---------------------------------------------------------------------------
JOBS: dict[str, dict] = {}
_JOBS_LOCK = threading.Lock()


def _job_update(job_id: str, **kw) -> None:
    with _JOBS_LOCK:
        JOBS.setdefault(job_id, {}).update(kw)


def _run_job(job_id: str, fn) -> None:
    def wrap() -> None:
        try:
            fn(job_id)
            _job_update(job_id, status="done")
        except PipelineError as e:
            _job_update(job_id, status="error", error=e.render())
        except Exception as e:  # noqa: BLE001 — 背景執行緒的錯誤必須被看見
            _job_update(job_id, status="error", error=f"{type(e).__name__}: {e}",
                        trace=traceback.format_exc()[-2000:])
    _job_update(job_id, status="running", stage="", started=time.time())
    threading.Thread(target=wrap, daemon=True).start()


# ---------------------------------------------------------------------------
# 工作內容
# ---------------------------------------------------------------------------

def _ingest_job(filename: str, content: str, brief: str, briefs=None):
    """素材拖進來 → 存檔 → 讀取正規化 → 抽知識卡。

    `briefs`＝每則一格的題目與走向（清單長度＝要產出幾則，格子留白＝模型判斷）。
    """
    def run(job_id: str) -> None:
        from src.analyze.extract_highlights import extract
        from src.ingest.read_article import read

        safe = re.sub(r"[\\/:*?\"<>|]", "_", filename) or "dropped.md"
        inbox = out_root() / INBOX
        inbox.mkdir(parents=True, exist_ok=True)
        md = inbox / safe
        md.write_text(content, encoding="utf-8")

        slug = slugify(md.stem)
        _job_update(job_id, stage="讀取正規化", slug=slug)
        read(md)
        _job_update(job_id, stage="抽知識卡（叫模型）")
        extract(slug, brief=brief or None, briefs=briefs)
        # 一路跑到底（Human 2026-07-15）：拖進來就連圖跟文案一起備好。
        # 之後改了卡片也不浪費——is_stale 會讓過期的部分重生，人改過的文案有不覆蓋防線。
        from src.compose.write_post import compose
        from src.render.render_cards import render

        cfg = _load_config()
        if cfg.get("handle"):
            os.environ["IG_HANDLE"] = cfg["handle"]
        _job_update(job_id, stage="出圖（用系統瀏覽器截圖）")
        render(slug, ratio=cfg.get("ratio", "1x1"), theme=cfg.get("theme", "b"))
        _job_update(job_id, stage="寫文案")
        compose(slug)
        _job_update(job_id, stage="完成", slug=slug)
    return run


def _ensure_fresh(job_id: str, slug: str) -> None:
    """圖與文案跟上最新的編輯。**is_stale 決定要不要重出**——沒改動就秒過。

    「出圖＋文案」按鈕已退役（Human 2026-07-15）：拖放時全跑，之後的對齊
    在發布／輸出前自動做，人不需要記得任何順序。
    """
    from src.compose.write_post import compose
    from src.render.render_cards import render

    cfg = _load_config()
    if cfg.get("handle"):
        os.environ["IG_HANDLE"] = cfg["handle"]
    _job_update(job_id, stage="同步圖與文案（沒改動就很快）")
    render(slug, ratio=cfg.get("ratio", "1x1"), theme=cfg.get("theme", "b"))
    compose(slug)


def _export_local_job(slug: str, post_index: int, open_after: bool):
    def run(job_id: str) -> None:
        from src.editor import export_local

        _ensure_fresh(job_id, slug)
        _job_update(job_id, stage="輸出到本地資料夾")
        dest = export_local(slug, post_index)
        if open_after:
            _open_folder(dest)
        _job_update(job_id, stage="完成", path=str(dest))
    return run


def _export_job(slug: str, theme: str, ratio: str, handle: str):
    """出圖＋文案。**出圖是純函式**（文字＋主題＋比例 → PNG），不需要確認。

    文案階段自己會遵守 human_edited（人改過的 caption 一個字都不動，只更新圖片清單）。
    """
    def run(job_id: str) -> None:
        from src.compose.write_post import compose
        from src.render.render_cards import render

        if handle:
            os.environ["IG_HANDLE"] = handle
        _job_update(job_id, stage="出圖（用系統瀏覽器截圖）")
        render(slug, ratio=ratio, theme=theme, force=True)
        _job_update(job_id, stage="寫文案")
        compose(slug)
        _job_update(job_id, stage="完成")
    return run


# ---------------------------------------------------------------------------
# 資料組裝
# ---------------------------------------------------------------------------

def _config_path() -> Path:
    return out_root() / "_editor.json"


def _load_config() -> dict:
    cfg = dict(CONFIG_DEFAULT)
    try:
        cfg.update({k: v for k, v in json.loads(
            _config_path().read_text(encoding="utf-8")).items() if k in CONFIG_KEYS})
    except (OSError, json.JSONDecodeError):
        pass
    return cfg


def _save_config(data: dict) -> dict:
    cfg = _load_config()
    cfg.update({k: v for k, v in data.items() if k in CONFIG_KEYS})
    _config_path().parent.mkdir(parents=True, exist_ok=True)
    _config_path().write_text(json.dumps(cfg, ensure_ascii=False, indent=2), encoding="utf-8")
    return cfg


def _read_raw(path: Path) -> dict | None:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None


def _state() -> dict:
    """側欄要的東西：每篇素材的標題、幾則貼文、文案好了沒。"""
    articles = []
    root = out_root()
    if root.is_dir():
        for d in sorted(root.iterdir()):
            if not d.is_dir() or d.name.startswith("_"):
                continue
            h = _read_raw(highlights_path(d.name))
            if not h:
                continue
            n = len(h.get("posts", []))
            articles.append({
                "slug": d.name,
                "title": h.get("source", {}).get("title", d.name),
                "n_posts": n,
                "human_edited": bool(h.get("human_edited")),
                "captions": [post_path(d.name, i).is_file() for i in range(1, n + 1)],
            })
    cfg = _load_config()
    all_settings = settings.load()
    cfg["render"] = all_settings["render"]      # 編輯台的卡片 iframe 要注入字級覆寫
    cfg["gen"] = {"cards_max": all_settings["generation"]["cards_max"]}  # 新增卡片的上限提示
    return {"articles": articles, "config": cfg}


def _article(slug: str) -> dict:
    h = _read_raw(highlights_path(slug))
    if not h:
        raise PipelineError("MISSING_INPUT", f"找不到 {slug} 的 highlights.json")
    posts = {}
    for i in range(1, len(h.get("posts", [])) + 1):
        posts[str(i)] = _read_raw(post_path(slug, i))
    return {"highlights": h, "posts": posts}


def _material(slug: str, cap: int = 12000) -> str:
    """建議用的素材＝正規化後的原文。建議不能超出素材講過的東西。"""
    a = _read_raw(article_path(slug))
    if not a:
        return ""
    body = "\n\n".join(p.get("text", "") for p in a.get("paragraphs", []))
    return body[:cap]


def _publish_job(slug: str, post_index: int, platform: str):
    """半自動發布：開瀏覽器、代填、停在分享前（[[一鍵發布到 IG 與 Threads]]）。

    job 會一直是 running 直到人把瀏覽器關掉——這是刻意的：視窗是人的工作檯。
    """
    def run(job_id: str) -> None:
        from src.publish import payload
        from src.publish_web import prefill

        _ensure_fresh(job_id, slug)
        data = payload(slug, post_index, platform)
        prefill(data, on_stage=lambda msg: _job_update(job_id, stage=msg))
        _job_update(job_id, stage="瀏覽器已關閉")
    return run


def _open_folder(path: Path) -> None:
    """備援按鈕：幫人把圖片資料夾打開（Windows 用 explorer；其他平台盡力）。"""
    import subprocess
    import sys as _sys

    if not path.is_dir():
        raise PipelineError("MISSING_INPUT", f"資料夾不存在：{path}",
                            hint="先按「出圖＋文案」")
    if _sys.platform.startswith("win"):
        os.startfile(str(path))  # noqa: S606
    elif _sys.platform == "darwin":
        subprocess.Popen(["open", str(path)])
    else:
        subprocess.Popen(["xdg-open", str(path)])


# ---------------------------------------------------------------------------
# 後台設定（[[編輯台後台設定]]）
# ---------------------------------------------------------------------------

# 面板可編輯的 prompt 檔（白名單——不開放亂寫任意路徑）
PROMPT_FILES = ("highlights", "caption", "suggest")


def _prompt_path(name: str) -> Path:
    if name not in PROMPT_FILES:
        raise PipelineError("MISSING_INPUT", f"不認識的 prompt：{name}",
                            hint=f"可用：{', '.join(PROMPT_FILES)}")
    return PROJECT_ROOT / "prompts" / f"{name}.md"


def _prompt_default_path(name: str) -> Path:
    return PROJECT_ROOT / "prompts" / "_defaults" / f"{name}.md"


def _env_path() -> Path:
    return PROJECT_ROOT / ".env"


def _has_key(*names: str) -> bool:
    if any(os.environ.get(n) for n in names):
        return True
    try:
        content = _env_path().read_text(encoding="utf-8")
    except OSError:
        return False
    for line in content.splitlines():
        k, _, v = line.partition("=")
        if k.strip() in names and v.strip():
            return True
    return False


def _write_env(updates: dict[str, str]) -> None:
    """把 key 合併寫進 .env（**不進版控、不回顯**）。同時設進目前的環境，立即生效。"""
    lines: list[str] = []
    try:
        lines = _env_path().read_text(encoding="utf-8").splitlines()
    except OSError:
        pass
    for key, val in updates.items():
        val = val.strip()
        if not val:
            continue
        done = False
        for i, line in enumerate(lines):
            if line.split("#", 1)[0].partition("=")[0].strip() == key:
                lines[i] = f"{key}={val}"
                done = True
                break
        if not done:
            lines.append(f"{key}={val}")
        os.environ[key] = val
    _env_path().write_text("\n".join(lines) + "\n", encoding="utf-8")


def _settings_payload() -> dict:
    return {
        "settings": settings.load(),
        "defaults": settings.DEFAULTS,
        "prompts": {n: _prompt_path(n).read_text(encoding="utf-8") for n in PROMPT_FILES},
        "keys": {
            "gemini": _has_key("GEMINI_API_KEY", "GOOGLE_API_KEY"),
            "anthropic": _has_key("ANTHROPIC_API_KEY"),
        },
    }


# ---------------------------------------------------------------------------
# HTTP
# ---------------------------------------------------------------------------

class Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    # -- helpers --
    def _json(self, data, status: int = 200) -> None:
        blob = json.dumps(data, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(blob)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(blob)

    def _file(self, rel: str) -> None:
        path = (PROJECT_ROOT / rel).resolve()
        # 只准拿專案資料夾裡的東西，而且只准 templates/ 與 out/
        ok_roots = (PROJECT_ROOT / "templates", out_root())
        if not any(str(path).startswith(str(r.resolve())) for r in ok_roots) or not path.is_file():
            self._json({"error": f"not found: {rel}"}, 404)
            return
        blob = path.read_bytes()
        self.send_response(200)
        self.send_header("Content-Type", MIME.get(path.suffix, "application/octet-stream"))
        self.send_header("Content-Length", str(len(blob)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(blob)

    def _body(self) -> dict:
        n = int(self.headers.get("Content-Length") or 0)
        if n <= 0:
            return {}
        return json.loads(self.rfile.read(n).decode("utf-8"))

    def log_message(self, fmt: str, *args) -> None:  # 安靜一點，錯誤才印
        if args and str(args[1]).startswith(("4", "5")):
            super().log_message(fmt, *args)

    # -- GET --
    def do_GET(self) -> None:  # noqa: N802
        u = urlparse(self.path)
        q = {k: v[0] for k, v in parse_qs(u.query).items()}
        route = unquote(u.path)
        try:
            if route in ("/", "/index.html"):
                self._file("templates/editor.html")
            elif route.startswith(("/templates/", "/out/")):
                self._file(route.lstrip("/"))
            elif route == "/api/state":
                self._json(_state())
            elif route == "/api/article":
                self._json(_article(q["slug"]))
            elif route == "/api/settings":
                self._json(_settings_payload())
            elif route == "/api/job":
                with _JOBS_LOCK:
                    job = dict(JOBS.get(q.get("id", ""), {"status": "unknown"}))
                self._json(job)
            else:
                self._json({"error": f"unknown route: {route}"}, 404)
        except PipelineError as e:
            self._json({"error": e.render()}, 422)
        except Exception as e:  # noqa: BLE001
            self._json({"error": f"{type(e).__name__}: {e}"}, 500)

    # -- POST --
    def do_POST(self) -> None:  # noqa: N802
        route = unquote(urlparse(self.path).path)
        try:
            body = self._body()
            if route == "/api/save/highlights":
                from src.editor import save_highlights
                save_highlights(body["slug"], body["data"])
                self._json({"ok": True})
            elif route == "/api/save/post":
                from src.editor import save_post
                save_post(body["slug"], int(body["post_index"]), body)
                self._json({"ok": True})
            elif route == "/api/suggest":
                from src.editor import suggest
                out = suggest(body["kind"], body["content"], body.get("instruction", ""),
                              material=_material(body.get("slug", "")))
                self._json({"suggestion": out})
            elif route == "/api/ingest":
                job_id = uuid.uuid4().hex[:12]
                briefs = body.get("briefs")
                if briefs is not None:
                    briefs = [str(b) for b in briefs][:10]  # schema 的失控上限
                _run_job(job_id, _ingest_job(body.get("filename", "dropped.md"),
                                             body["content"], body.get("brief", ""),
                                             briefs=briefs))
                self._json({"job": job_id})
            elif route == "/api/export":
                cfg = _load_config()
                job_id = uuid.uuid4().hex[:12]
                _run_job(job_id, _export_job(
                    body["slug"],
                    body.get("theme", cfg["theme"]),
                    body.get("ratio", cfg["ratio"]),
                    cfg.get("handle", ""),
                ))
                self._json({"job": job_id})
            elif route == "/api/config":
                self._json(_save_config(body))
            elif route == "/api/settings":
                saved = settings.save(body.get("settings") or {})
                self._json({"settings": saved})
            elif route == "/api/prompt":
                # 存 prompt；restore=True 就從 _defaults/ 抄回出廠版
                name = body["name"]
                path = _prompt_path(name)
                if body.get("restore"):
                    path.write_text(_prompt_default_path(name).read_text(encoding="utf-8"),
                                    encoding="utf-8")
                else:
                    content = str(body.get("content", ""))
                    if not content.strip():
                        raise PipelineError("MISSING_INPUT", "prompt 不能存成空的",
                                            hint="要回出廠版就按「回復預設」")
                    path.write_text(content, encoding="utf-8")
                self._json({"content": path.read_text(encoding="utf-8")})
            elif route == "/api/delete-article":
                from src.editor import delete_article
                removed = delete_article(body["slug"])
                self._json({"removed": removed})
            elif route == "/api/export-local":
                job_id = uuid.uuid4().hex[:12]
                _run_job(job_id, _export_local_job(body["slug"], int(body["post_index"]),
                                                   bool(body.get("open"))))
                self._json({"job": job_id})
            elif route == "/api/delete-post":
                from src.editor import invalidate_posts_from
                removed = invalidate_posts_from(body["slug"], int(body["from_index"]))
                self._json({"removed": removed})
            elif route == "/api/publish":
                job_id = uuid.uuid4().hex[:12]
                _run_job(job_id, _publish_job(body["slug"], int(body["post_index"]),
                                              body["platform"]))
                self._json({"job": job_id})
            elif route == "/api/published":
                from src.publish import mark_published
                r = mark_published(body["slug"], int(body["post_index"]), body["platform"])
                self._json({"post": r["post"]})
            elif route == "/api/open-folder":
                from src.paths import images_dir
                _open_folder(images_dir(body["slug"], int(body["post_index"])))
                self._json({"ok": True})
            elif route == "/api/llm-keys":
                # 只寫 .env，永不回傳明文
                updates = {}
                if body.get("gemini_key"):
                    updates["GEMINI_API_KEY"] = str(body["gemini_key"])
                if body.get("anthropic_key"):
                    updates["ANTHROPIC_API_KEY"] = str(body["anthropic_key"])
                if updates:
                    _write_env(updates)
                self._json({"keys": {
                    "gemini": _has_key("GEMINI_API_KEY", "GOOGLE_API_KEY"),
                    "anthropic": _has_key("ANTHROPIC_API_KEY"),
                }})
            else:
                self._json({"error": f"unknown route: {route}"}, 404)
        except PipelineError as e:
            self._json({"error": e.render()}, 422)
        except (KeyError, ValueError, json.JSONDecodeError) as e:
            self._json({"error": f"請求格式不對：{type(e).__name__}: {e}"}, 400)
        except Exception as e:  # noqa: BLE001
            self._json({"error": f"{type(e).__name__}: {e}"}, 500)


def main() -> int:
    port = PORT
    for _ in range(10):  # 埠被占就往上找
        try:
            server = ThreadingHTTPServer(("127.0.0.1", port), Handler)
            break
        except OSError:
            port += 1
    else:
        print(f"✗ {PORT}–{PORT + 9} 全被占用了")
        return 1

    url = f"http://127.0.0.1:{port}/"
    print("=" * 60)
    print(f"  編輯台開在  {url}")
    print("  關掉：這個視窗按 Ctrl+C（或直接關視窗）")
    print("=" * 60)
    if os.environ.get("EDITOR_NO_BROWSER", "") in ("", "0"):
        threading.Timer(0.6, lambda: webbrowser.open(url)).start()
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\n收工。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
