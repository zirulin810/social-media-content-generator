"""Pipeline 錯誤型別與錯誤碼。

契約：模組不吞錯、不回半成品。失敗一律 raise PipelineError，CLI 印出後停止。
錯誤碼定義見 docs/spec.md 的「錯誤碼」章節。
"""

from __future__ import annotations


class ErrorCode:
    SOURCE_NOT_FOUND = "SOURCE_NOT_FOUND"
    SOURCE_UNPARSEABLE = "SOURCE_UNPARSEABLE"
    ARTICLE_TOO_SHORT = "ARTICLE_TOO_SHORT"
    QUOTE_NOT_GROUNDED = "QUOTE_NOT_GROUNDED"
    SCHEMA_INVALID = "SCHEMA_INVALID"
    RENDER_OVERFLOW = "RENDER_OVERFLOW"
    MISSING_INPUT = "MISSING_INPUT"


class PipelineError(Exception):
    """帶錯誤碼與可行動提示的例外。

    hint 要寫給「看到這個錯誤的人」看：他下一步該做什麼。
    """

    def __init__(self, code: str, message: str, hint: str = "") -> None:
        self.code = code
        self.message = message
        self.hint = hint
        super().__init__(f"[{code}] {message}")

    def render(self) -> str:
        out = f"[{self.code}] {self.message}"
        if self.hint:
            out += f"\n  → {self.hint}"
        return out
