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


def validate(kind: str, data: dict[str, Any]) -> None:
    """不符 schema 就拋 SCHEMA_INVALID，訊息帶上出錯的欄位路徑。"""
    errors = sorted(_validator(kind).iter_errors(data), key=lambda e: list(e.path))
    if not errors:
        return
    lines = []
    for e in errors[:10]:
        loc = "/".join(str(p) for p in e.path) or "(root)"
        lines.append(f"  - {loc}: {e.message}")
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
