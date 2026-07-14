"""Schema 驗證：契約的執法者。

每個模組**寫檔前**呼叫 write_json()，它會先驗證再落地——
不符 schema 的產物一律不寫出去，免得下游拿到垃圾還跑得很開心。
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from jsonschema import Draft202012Validator

from .errors import ErrorCode, PipelineError
from .paths import SCHEMA_DIR

_KINDS = ("article", "highlights", "post")
_cache: dict[str, Draft202012Validator] = {}


def _validator(kind: str) -> Draft202012Validator:
    if kind not in _KINDS:
        raise ValueError(f"未知的 schema 種類：{kind}（可用：{_KINDS}）")
    if kind not in _cache:
        path = SCHEMA_DIR / f"{kind}.schema.json"
        with path.open(encoding="utf-8") as f:
            _cache[kind] = Draft202012Validator(json.load(f))
    return _cache[kind]


def _say(e: Any) -> str:
    """把 jsonschema 的訊息翻成人話。

    **驗證器的錯誤訊息是使用者介面**——而且不只給人看：schema 不合時，
    我們會把這段訊息原封不動餵回給模型請它修。訊息爛，模型就修不動。

    jsonschema 對「陣列太長」的預設訊息是把**整個陣列 dump 出來**再接一句
    `is too long`。2026-07-14 實跑時，那段訊息長達數千字，模型連修兩輪都在原地打轉——
    因為它根本看不出來「要它做什麼」。真正的意思只有一句：**9 張卡，上限 6 張。**
    """
    kw, val = e.validator, e.validator_value
    n = len(e.instance) if isinstance(e.instance, (list, str, dict)) else None

    if kw == "maxItems":
        return f"有 {n} 項，最多 {val} 項 → 刪到 {val} 項以內"
    if kw == "minItems":
        return f"只有 {n} 項，至少要 {val} 項"
    if kw == "maxLength":
        return f"{n} 字，上限 {val} 字 → 精簡成 {val} 字以內：「{str(e.instance)[:40]}…」"
    if kw == "minLength":
        return f"太短（{n} 字），至少 {val} 字"
    if kw == "required":
        return e.message  # 「'x' is a required property」本來就夠清楚
    if kw in ("enum", "const"):
        return f"值必須是 {val}，但拿到 {e.instance!r}"

    # 其他錯誤：保留原訊息，但**不准把整包資料 dump 出來**
    msg = e.message
    return msg if len(msg) <= 200 else msg[:200] + " …（訊息過長已截斷）"


def validate(kind: str, data: dict[str, Any]) -> None:
    """不符 schema 就拋 SCHEMA_INVALID，訊息帶上出錯的欄位路徑。"""
    errors = sorted(_validator(kind).iter_errors(data), key=lambda e: list(e.path))
    if not errors:
        return
    lines = []
    for e in errors[:10]:
        loc = "/".join(str(p) for p in e.path) or "(root)"
        lines.append(f"  - {loc}: {_say(e)}")
    more = f"\n  …另有 {len(errors) - 10} 個錯誤" if len(errors) > 10 else ""
    raise PipelineError(
        ErrorCode.SCHEMA_INVALID,
        f"{kind}.json 不符 schema：\n" + "\n".join(lines) + more,
        hint=f"對照 schemas/{kind}.schema.json 與 schemas/examples/{kind}.example.json",
    )


def read_json(kind: str, path: Path) -> dict[str, Any]:
    """讀取上游產物並驗證。檔案不存在 → MISSING_INPUT。"""
    if not path.exists():
        raise PipelineError(
            ErrorCode.MISSING_INPUT,
            f"找不到 {path}",
            hint=f"先跑產生 {kind}.json 的階段（見 docs/spec.md）",
        )
    with path.open(encoding="utf-8") as f:
        data = json.load(f)
    validate(kind, data)
    return data


def write_json(kind: str, path: Path, data: dict[str, Any]) -> Path:
    """先驗證再寫檔。驗證失敗就不落地。"""
    validate(kind, data)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
        f.write("\n")
    return path
