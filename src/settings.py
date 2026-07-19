"""後台設定（單一事實來源）。實作任務：[[編輯台後台設定]]

把散在 schema、prompt、程式碼裡的「隱藏參數」集中到 `settings.json`，
人在編輯台的設定頁改，整條 pipeline 都遵守。

**「三種上限住三個地方」的原則（spec v3.1）沒有被打破**——三個地方還是三個地方，
只是數字的來源統一到這裡：

    編輯目標（密度、卡數目標）   → build_prompt() 填進 prompt 的模板變數
    物理上限（卡數、字數）       → schema.py 載入 schema 時套用覆寫
    執行參數（模型、溫度）       → llm.py 讀取

優先序（除錯用）：**環境變數 > settings.json > 內建預設**。
API key **不放這裡**——那是 `.env` 的事（不進版控、面板不回顯明文）。

設定檔是產物的輸入：analyze／compose 的 `is_stale` 把它算進去，
改了參數，舊產物就過期，重跑一次就長出新結果。
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

from .errors import ErrorCode, PipelineError
from .paths import PROJECT_ROOT

# 內建預設＝2026-07-15 之前寫死在各處的值。改這裡等於改「出廠設定」。
DEFAULTS: dict[str, dict[str, Any]] = {
    "generation": {
        "posts_max": 1,            # 一篇產出幾則貼文（Human 2026-07-15：一個素材一則就好）
        "cards_max": 18,           # 一則最多幾張內容卡。18＝Threads/IG 輪播上限 20 − 封面 − 出處卡。
                                   # **平台推導值，設定頁不開放**（跟 IG 2200／Threads 500 同族）——
                                   # 平台哪天改上限，改這裡的出廠值即可。
        "cards_target": "4–5",     # 卡數目標（寫進 prompt 的傾向，不是規則）
        "point_body_target": 80,   # 重點卡內文目標字數（Human 選定的密度：中等）
        "point_body_max": 180,     # 重點卡內文硬上限（拆卡預算）
        "steps_max": 6,            # 步驟卡最多幾步
        "steps_step_target": 25,   # 每步目標字數（Human 選定的密度：精簡）
        "steps_step_max": 100,     # 每步硬上限（物理極限）
        "contrast_side_target": 40,
        "contrast_side_max": 120,
        "quote_max": 40,           # 金句上限（金句卡字級最大，這是最緊的一格）
        "hook_target": 25,         # 文案 hook 的目標字數（硬上限永遠是 IG 折疊線 125）
        "title_max": 60,           # 卡片標題上限（point／steps／contrast 共用）。
                                   # 24 是**編輯目標**（prompt 管），不是牆——標題長了版面會縮字級。
                                   # 2026-07-15 Human 在編輯台被 24 擋下，把牆移回物理極限。
        "angle_max": 30,           # 封面標題（angle）上限
        "cover_hook_max": 70,      # 封面副標（hook）上限
        "summary_max_items": 7,    # summary 最多幾條（下限固定 3）
        "summary_item_max": 120,   # summary 每條上限
        "hashtags_max": 10,        # hashtag 最多幾個（下限固定 3；IG 文案端同步遵守）
    },
    "llm": {
        "provider": "gemini",      # gemini 或 anthropic
        "gemini_model": "gemini-2.5-flash",
        "anthropic_model": "claude-sonnet-5",
        "temperature": 0.4,
    },
    # 文案（caption）的編輯規格。平台硬上限（IG 2200／Threads 500／折疊線 125）**刻意不在這裡**——
    # 那是平台定的，改了只會讓文發不出去。
    "caption": {
        "body_paras_min": 2,       # 正文最少幾段
        "body_paras_max": 4,       # 正文最多幾段（再多就太碎）
        "para_target": 100,        # 每段目標字數（寫進 prompt）
        "para_max": 150,           # 每段硬上限（超過＝一坨）
        "rewrite_rounds": 2,       # 文案不合格時叫模型重寫幾輪（改不動就放行＋印警告）
    },
    # 版面字級與拆卡。**這些是拿真版面量出來的**，改了直接影響拆卡行為——設定頁會掛警語。
    "render": {
        "comfort_fs": 44,          # 舒適下限：字級低於它就拆卡（不是塞不下才拆）
        "min_fs": 34,              # 硬底線：低於它不出圖（那不叫圖卡叫掃描件）
        "max_fs": 76,              # 字級天花板
        "max_splits": 4,           # 一張卡最多拆幾張（再多是內容問題，不是版型的錯）
    },
    # 進階執行。動之前先想清楚為什麼。
    "advanced": {
        "strict_grounding": False, # True＝主張對不回原文就整批擋下（預設只標給人看，不擋）
        "chunk_threshold": 60000,  # 單次分析的字數上限（實測最長素材 2 萬字，一次塞得下）
        "max_output_tokens": 65536,# Gemini 輸出 token 上限（太小會被截斷成半截 JSON）
        "json_retries": 2,         # 模型吐爛 JSON 的重試次數（隨機事件，重跑通常就好）
        "repair_rounds": 2,        # schema 不合時把錯誤餵回去請模型修的輪數
    },
}

# 平台推導值：**檔案裡存了也不理**。cards_max＝輪播 20 − 封面 − 出處卡，
# 是平台的數字不是人的偏好——早期版本曾把它存進 settings.json（當時出廠 6），
# 不鎖的話那個凍住的 6 會永遠蓋掉後來的 18。
_PLATFORM_LOCKED = {("generation", "cards_max")}

# 各欄位的合法範圍（(section, key): (min, max)）。**擋的是打錯字，不是品味**——
# 範圍給得很寬，數字合不合理由人自己負責。
_INT_RANGE: dict[tuple[str, str], tuple[int, int]] = {
    ("generation", "posts_max"): (1, 10),
    ("generation", "point_body_target"): (10, 2000),
    ("generation", "point_body_max"): (10, 2000),
    ("generation", "steps_max"): (2, 12),
    ("generation", "steps_step_target"): (5, 2000),
    ("generation", "steps_step_max"): (5, 2000),
    ("generation", "contrast_side_target"): (5, 2000),
    ("generation", "contrast_side_max"): (5, 2000),
    ("generation", "quote_max"): (5, 500),
    ("generation", "hook_target"): (5, 125),  # 超過 IG 折疊線的目標沒有意義
    ("generation", "title_max"): (5, 200),
    ("generation", "angle_max"): (5, 200),
    ("generation", "cover_hook_max"): (5, 500),
    ("generation", "summary_max_items"): (3, 20),
    ("generation", "summary_item_max"): (20, 500),
    ("generation", "hashtags_max"): (3, 30),
    ("caption", "body_paras_min"): (1, 10),
    ("caption", "body_paras_max"): (1, 10),
    ("caption", "para_target"): (20, 1000),
    ("caption", "para_max"): (20, 1000),
    ("caption", "rewrite_rounds"): (0, 5),
    ("render", "comfort_fs"): (20, 120),
    ("render", "min_fs"): (10, 120),
    ("render", "max_fs"): (30, 200),
    ("render", "max_splits"): (1, 8),
    ("advanced", "chunk_threshold"): (1000, 500000),
    ("advanced", "max_output_tokens"): (1024, 1000000),
    ("advanced", "json_retries"): (0, 5),
    ("advanced", "repair_rounds"): (0, 5),
}

# 目標不能大於上限——不然目標就是上限（test_compose 也釘著同一件事）
_TARGET_LE_MAX = [
    ("generation", "point_body_target", "point_body_max"),
    ("generation", "steps_step_target", "steps_step_max"),
    ("generation", "contrast_side_target", "contrast_side_max"),
    ("caption", "para_target", "para_max"),
    ("caption", "body_paras_min", "body_paras_max"),
    ("render", "min_fs", "comfort_fs"),
    ("render", "comfort_fs", "max_fs"),
]

_BOOLS = {("advanced", "strict_grounding")}


def path() -> Path:
    """設定檔在哪。測試用 SETTINGS_FILE 指到暫存目錄，不弄髒真的設定。"""
    env = os.environ.get("SETTINGS_FILE")
    return Path(env) if env else PROJECT_ROOT / "settings.json"


def load() -> dict[str, Any]:
    """讀設定。檔案不存在／壞掉 → 內建預設（設定壞了不該讓 pipeline 停擺）。

    未知的鍵直接忽略——舊版設定檔碰上新版程式不炸，反過來也一樣。
    """
    data = {k: dict(v) for k, v in DEFAULTS.items()}
    try:
        raw = json.loads(path().read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return data
    for section, values in raw.items():
        if section in data and isinstance(values, dict):
            for k, v in values.items():
                if k in data[section] and (section, k) not in _PLATFORM_LOCKED:
                    data[section][k] = v
    return data


def gen(key: str) -> Any:
    return load()["generation"][key]


def llm(key: str) -> Any:
    return load()["llm"][key]


def cap(key: str) -> Any:
    """caption（文案結構）區。"""
    return load()["caption"][key]


def render(key: str) -> Any:
    """render（版面字級）區。"""
    return load()["render"][key]


def adv(key: str) -> Any:
    """advanced（進階執行）區。"""
    return load()["advanced"][key]


def mtime() -> float:
    """給快取失效用（schema.py 靠它知道設定變了）。"""
    try:
        return path().stat().st_mtime
    except OSError:
        return 0.0


def validate_settings(data: dict[str, Any]) -> None:
    """存檔前驗證。錯誤訊息是使用者介面——講清楚哪個欄位、為什麼。"""
    problems = []
    for (sec, k), (lo, hi) in _INT_RANGE.items():
        if k in data.get(sec, {}):
            v = data[sec][k]
            if not isinstance(v, int) or isinstance(v, bool) or not (lo <= v <= hi):
                problems.append(f"{sec}.{k}：要是 {lo}–{hi} 的整數，拿到 {v!r}")
    for sec, t, m in _TARGET_LE_MAX:
        d = data.get(sec, {})
        if isinstance(d.get(t), int) and isinstance(d.get(m), int) and d[t] > d[m]:
            problems.append(f"{sec}.{t}（{d[t]}）不能大於 {sec}.{m}（{d[m]}）")
    for sec, k in _BOOLS:
        if k in data.get(sec, {}) and not isinstance(data[sec][k], bool):
            problems.append(f"{sec}.{k}：要是 true／false")
    g = data.get("generation", {})
    if "cards_target" in g and not isinstance(g["cards_target"], str):
        problems.append("generation.cards_target：要是文字（例：「4–5」）")

    l = data.get("llm", {})
    if "provider" in l and l["provider"] not in ("gemini", "anthropic"):
        problems.append(f"llm.provider：只能是 gemini 或 anthropic，拿到 {l['provider']!r}")
    if "temperature" in l:
        v = l["temperature"]
        if not isinstance(v, (int, float)) or isinstance(v, bool) or not (0 <= v <= 2):
            problems.append(f"llm.temperature：要是 0–2 的數字，拿到 {v!r}")

    if problems:
        raise PipelineError(
            ErrorCode.SCHEMA_INVALID,
            "設定沒存進去：\n" + "\n".join(f"  - {p}" for p in problems),
            hint="改好再存一次；「回復預設」永遠有效",
        )


def save(data: dict[str, Any]) -> dict[str, Any]:
    """驗證後合併存檔，回傳存完的完整設定。"""
    merged = load()
    for section in DEFAULTS:
        for k, v in (data.get(section) or {}).items():
            if k in DEFAULTS[section]:
                merged[section][k] = v
    validate_settings(merged)
    p = path()
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(merged, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return merged


__all__ = ["DEFAULTS", "load", "save", "gen", "llm", "cap", "render", "adv", "mtime", "path", "validate_settings"]
