"""編輯台的資料層（純邏輯，不碰 HTTP）。實作任務：[[發布前預覽介面]]

分工（比照 layout.py ↔ render_cards.py 的慣例）：

    src/editor.py              人的編輯**如何落地**——決策，純函式，可以不開伺服器測
    scripts/editor_server.py   怎麼收請求、怎麼開瀏覽器——勞動
    templates/editor.html      畫面

這張任務的三條紅線，全部在**資料層**執行（介面擋不擋得住不重要，資料層要擋得住）：

1. **文字是唯一的事實來源。** 人改了就是最終版：存檔時標 `human_edited`，
   analyze／compose 看到這個標記就不再覆蓋（見 extract_highlights.extract、
   write_post.compose_post）。
2. **卡片被人改過，evidence 就拿掉。** 人改了字，原本的「這句話出自第 N 段」
   就是一個不再成立的宣稱——資料層不該繼續存著它。這件事不出現在任何畫面上。
3. **LLM 只提議，不覆蓋。** suggest() 只回一份「建議的 JSON」，
   套不套用由前端的人逐條決定；這個模組沒有任何一條路徑會拿 LLM 的輸出直接寫檔。
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

from .errors import ErrorCode, PipelineError
from .llm import LLMFn, get_llm
from .paths import PROMPT_DIR, highlights_path, post_path
from .schema import read_json, validate, write_json

# ---------------------------------------------------------------------------
# 一、人的編輯落地（highlights.json）
# ---------------------------------------------------------------------------

# 各卡型的 evidence 藏在哪裡。**這張表是唯一的事實來源**——
# 卡型增加時改這裡，不要在別處再寫一份「怎麼剝 evidence」。
_EVIDENCE_KEYS = {
    "point": ("evidence",),
    "quote": ("evidence",),
    "steps": (),   # evidence 在每一步裡（見 _strip_evidence）
    "contrast": (),  # evidence 在 wrong / right 裡
}


def _strip_evidence(card: dict[str, Any]) -> None:
    """把一張卡的 evidence 全部拿掉（就地修改）。

    金句卡另外把 `verbatim` 改成 False——人改過的句子不再保證是作者的原話，
    資料層不能繼續宣稱「逐字」。
    """
    for key in _EVIDENCE_KEYS.get(card.get("type", ""), ()):
        card.pop(key, None)
    if card.get("type") == "steps":
        for step in card.get("steps", []):
            if isinstance(step, dict):
                step.pop("evidence", None)
    if card.get("type") == "contrast":
        for side in ("wrong", "right"):
            if isinstance(card.get(side), dict):
                card[side].pop("evidence", None)
    if card.get("type") == "quote":
        card["verbatim"] = False


def finalize_highlights(data: dict[str, Any]) -> dict[str, Any]:
    """人按下儲存之後、寫檔之前的最後一站（就地修改並回傳）。

    - `edited: true` 的卡 → 剝掉 evidence（紅線 2）
    - 整份檔案標 `human_edited`（紅線 1：從此 analyze 不覆蓋、--force 也不覆蓋）
    """
    for post in data.get("posts", []):
        for card in post.get("cards", []):
            if card.get("edited"):
                card["edited"] = True  # 正規化（前端可能塞 1 / "true"）
                _strip_evidence(card)
    data["human_edited"] = True
    return data


def save_highlights(slug: str, data: dict[str, Any]) -> Path:
    """驗證後落地。不合 schema 就拋錯（那些上限是版面的物理極限，該擋）。"""
    data = finalize_highlights(data)
    validate("highlights", data)
    return write_json("highlights", highlights_path(slug), data)


# ---------------------------------------------------------------------------
# 二、人的編輯落地（post.json 的文案）
# ---------------------------------------------------------------------------

def merge_post_edit(old: dict[str, Any], payload: dict[str, Any]) -> dict[str, Any]:
    """把人改過的 caption / hashtags 合併進既有的 post.json（就地修改並回傳）。

    只收文字欄位——圖片清單、出處紀錄那些是程式的事，人在編輯台改不到它們。
    """
    by_platform = {p.get("platform"): p for p in payload.get("posts", [])}
    for entry in old.get("posts", []):
        edit = by_platform.get(entry.get("platform"))
        if not edit:
            continue
        if isinstance(edit.get("caption"), str) and edit["caption"].strip():
            entry["caption"] = edit["caption"]
        if entry.get("platform") == "instagram" and isinstance(edit.get("hashtags"), list):
            tags = [str(t).strip() for t in edit["hashtags"] if str(t).strip()]
            tags = [t if t.startswith("#") else f"#{t}" for t in tags]
            if tags:
                entry["hashtags"] = tags
            else:
                entry.pop("hashtags", None)
    old["human_edited"] = True
    return old


def save_post(slug: str, post_index: int, payload: dict[str, Any]) -> Path:
    path = post_path(slug, post_index)
    if not path.exists():
        raise PipelineError(
            ErrorCode.MISSING_INPUT,
            f"第 {post_index} 則還沒有文案（post.json 不存在）",
            hint="先按「出圖＋文案」讓 pipeline 產出初稿，之後才有東西可改",
        )
    old = read_json("post", path)
    merged = merge_post_edit(old, payload)
    validate("post", merged)
    return write_json("post", path, merged)


def invalidate_posts_from(slug: str, index: int) -> list[str]:
    """人刪了一則貼文之後，把「編號從 index 起」的 post.json 全部作廢（刪檔）。

    為什麼要刪：刪掉第 2 則後，原本的第 3 則遞補成第 2 則——磁碟上舊的 `p2/post.json`
    卻還是**前一個第 2 則**的文案。它要是標著 `human_edited`，compose 的不覆蓋防線
    反而會**忠實地保住一份張冠李戴的文案**。編號對不上的紀錄，留著比刪掉更危險。

    只刪 post.json（文案紀錄）；圖檔不動——下次「出圖＋文案」會整批重出對齊。
    人已在面板上確認過刪除，這是那個決定的資料層收尾。
    """
    removed: list[str] = []
    i = index
    while post_path(slug, i).exists() or post_path(slug, i).parent.exists():
        pj = post_path(slug, i)
        if pj.exists():
            pj.unlink()
            removed.append(str(pj))
        i += 1
        if i > 50:  # 安全煞車
            break
    return removed


_SLUG_RE = re.compile(r"^[a-z0-9][a-z0-9-]*$")


def delete_article(slug: str) -> str:
    """人在側欄刪素材：刪掉 `out/<slug>/` 整個資料夾（產物）。

    - **來源 md 不動**——Clippings 裡的檔案是人的，不是產物
    - slug 必須合法（schema 的 pattern），路徑必須落在 out/ 裡面——
      亂造的 slug 打不到別的資料夾
    - 人已在面板上確認過；這是那個決定的資料層執行
    """
    import shutil

    if not _SLUG_RE.match(slug or ""):
        raise PipelineError(ErrorCode.MISSING_INPUT, f"不合法的 slug：{slug!r}")
    from .paths import article_dir, out_root

    target = article_dir(slug).resolve()
    root = out_root().resolve()
    if root not in target.parents:
        raise PipelineError(ErrorCode.MISSING_INPUT, f"路徑不在 out/ 裡：{target}")
    if not target.is_dir():
        raise PipelineError(ErrorCode.MISSING_INPUT, f"找不到素材：{slug}")
    shutil.rmtree(target)
    return str(target)


def export_local(slug: str, post_index: int) -> Path:
    """把一則貼文輸出到本地暫存資料夾——發布失效時的保險。

    `暫存/<slug>-p<N>/`：
      01.png、02.png…       照 post.json 的 image_paths 順序重新命名（發文時照序拖）
      文案_IG.txt            含 hashtag
      文案_Threads.txt       無 hashtag（Threads 的 # 只是純文字，不放）
      建議話題.txt           給 Threads 話題標籤用（一則只能掛一個；獨立成檔，
                             整檔複製文案時才不會把它一起貼進貼文）
    重複輸出就整個覆蓋（它是暫存區，不是檔案庫）。
    """
    import shutil

    from .paths import PROJECT_ROOT, post_dir
    from .schema import read_json

    pj = read_json("post", post_path(slug, post_index))
    base = post_dir(slug, post_index)
    dest = PROJECT_ROOT / "暫存" / f"{slug}-p{post_index}"
    if dest.exists():
        shutil.rmtree(dest)
    dest.mkdir(parents=True)

    by = {e["platform"]: e for e in pj["posts"]}
    ig, th = by.get("instagram"), by.get("threads")
    paths = (ig or th)["image_paths"]
    for i, rel in enumerate(paths, 1):
        src = base / rel
        if not src.is_file():
            raise PipelineError(ErrorCode.MISSING_INPUT, f"圖片不見了：{src}",
                                hint="先按「出圖＋文案」重出")
        shutil.copy2(src, dest / f"{i:02d}{src.suffix}")
    if ig:
        (dest / "文案_IG.txt").write_text(ig["caption"], encoding="utf-8")
    if th:
        (dest / "文案_Threads.txt").write_text(th["caption"], encoding="utf-8")
    # Threads 的話題標籤（一則只能掛一個）：從 IG hashtag 挑第一個當建議，發文時手動選
    tags = (ig or {}).get("hashtags") or []
    if th and tags:
        (dest / "建議話題.txt").write_text(tags[0].lstrip("#"), encoding="utf-8")
    return dest


# ---------------------------------------------------------------------------
# 三、LLM 建議（只提議，不覆蓋）
# ---------------------------------------------------------------------------

# 各種建議範圍的「物理上限」提醒。**這是講給模型聽的傾向**，
# 真正的執法在 schema（存檔時驗）與渲染器（塞不下就拆／擋）。
_LIMITS = {
    "point": "title ≤ 24 字；body ≤ 180 字（目標 80 字）",
    "steps": "2–6 步；每步 ≤ 100 字（目標 25 字，精簡、字大、一眼看完）",
    "contrast": "title ≤ 24 字；wrong.text 與 right.text 各 ≤ 120 字",
    "quote": "text ≤ 40 字",
    "cover": "angle ≤ 30 字；hook ≤ 70 字",
    "caption": "hook 一句話、不超過 125 字（IG 折疊線）；正文 2–3 段、每段目標 100 字；"
    "hook＋正文須塞進 Threads 的 500 字",
    "redraft": "angle ≤ 30 字；hook ≤ 70 字；2–6 張卡（目標 4–5 張）；"
    "point 的 body ≤ 180（目標 80）；steps 每步 ≤ 100（目標 25）；"
    "contrast 每邊 ≤ 120；quote ≤ 40",
}

_KINDS = tuple(_LIMITS)


def build_suggest_prompt(kind: str, content: dict[str, Any], instruction: str,
                         material: str = "") -> str:
    """組建議用的 prompt。範本在 prompts/suggest.md——照專案慣例，prompt 不寫死在程式裡。"""
    if kind not in _KINDS:
        raise PipelineError(ErrorCode.MISSING_INPUT, f"未知的建議範圍：{kind}",
                            hint=f"可用：{', '.join(_KINDS)}")
    template = (PROMPT_DIR / "suggest.md").read_text(encoding="utf-8")
    return (
        template.replace("{instruction}", instruction.strip() or "（人沒有寫指示——就把這段文字修得更好讀）")
        .replace("{limits}", _LIMITS[kind])
        .replace("{material}", material.strip() or "（無——只依據「現有內容」本身）")
        .replace("{content}", json.dumps(content, ensure_ascii=False, indent=2))
    )


def _parse_json(text: str) -> dict[str, Any]:
    """從模型回應挖出 JSON。對真實換行寬容（文案本來就是多段落的東西）。"""
    text = re.sub(r"^```(?:json)?\s*|\s*```$", "", text.strip()).strip()
    a, b = text.find("{"), text.rfind("}")
    if a == -1 or b == -1:
        raise PipelineError(ErrorCode.SCHEMA_INVALID, "建議回應裡找不到 JSON", hint=text[:120])
    blob = text[a : b + 1]
    try:
        return json.loads(blob)
    except json.JSONDecodeError:
        pass
    try:
        return json.loads(blob, strict=False)
    except json.JSONDecodeError as e:
        raise PipelineError(ErrorCode.SCHEMA_INVALID, f"建議回應不是合法 JSON：{e}") from e


def _clean_texts(node: Any, keep_marks: bool = True) -> Any:
    """建議裡的每一段文字都過一次 clean（剝 emoji、轉台灣正體）——機械的事程式做。

    `keep_marks`：圖卡文字的 `**重點**` 是版型的螢光筆語法（card.js 渲染成 <mark>），
    卡片建議要**保留**；caption 是純文字（IG／Threads 不渲染 markdown），文案建議要剝掉。
    """
    from .compose.write_post import clean  # 延遲載入：suggest 才需要，存檔不需要

    if isinstance(node, str):
        return clean(node, keep_marks=keep_marks)
    if isinstance(node, list):
        return [_clean_texts(x, keep_marks) for x in node]
    if isinstance(node, dict):
        return {k: (_clean_texts(v, keep_marks) if k != "type" else v) for k, v in node.items()}
    return node


def suggest(kind: str, content: dict[str, Any], instruction: str,
            material: str = "", llm: LLMFn | None = None) -> dict[str, Any]:
    """要一份修改建議。**回傳的只是建議**——這個函式不寫任何檔案。"""
    llm = llm or get_llm()
    prompt = build_suggest_prompt(kind, content, instruction, material)
    raw = _parse_json(llm(prompt))
    # caption 是純文字，螢光筆標記剝掉；圖卡文字的建議保留標記
    return _clean_texts(raw, keep_marks=(kind != "caption"))


__all__ = [
    "finalize_highlights",
    "invalidate_posts_from",
    "delete_article",
    "export_local",
    "save_highlights",
    "merge_post_edit",
    "save_post",
    "suggest",
    "build_suggest_prompt",
]
