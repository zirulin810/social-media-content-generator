"""模型供應商層。

pipeline 只需要一件事：把 prompt 丟進去，把文字拿回來。
誰來做這件事（Gemini / Claude / 未來的其他人）是可抽換的——所以集中在這裡，
其他模組一律 `from ..llm import get_llm`，不准直接 import 任何 SDK。

用 REST 直呼，不裝 SDK：
- 少一個依賴，少一個壞掉的理由
- 出事時看得到原始的 HTTP 狀態碼與錯誤訊息，不必隔著一層 SDK 猜
"""

from __future__ import annotations

import json
import os
import random
import socket
import time
import urllib.error
import urllib.request
from typing import Callable

from . import settings
from .errors import ErrorCode, PipelineError

LLMFn = Callable[[str], str]

PROVIDER = os.environ.get("LLM_PROVIDER", "gemini").lower()

# Gemini 的 key 在不同教學裡有兩種常見命名，兩個都認
GEMINI_KEY_NAMES = ("GEMINI_API_KEY", "GOOGLE_API_KEY")
GEMINI_MODEL = os.environ.get("GEMINI_MODEL", "gemini-2.5-flash")
GEMINI_BASE = "https://generativelanguage.googleapis.com/v1beta"

ANTHROPIC_MODEL = os.environ.get("ANTHROPIC_MODEL", "claude-sonnet-5")


# ---------------------------------------------------------------------------
# 執行參數改由後台設定管（[[編輯台後台設定]]）。
# 優先序：**環境變數 > settings.json > 內建預設**——環境變數是除錯用的手動排檔。
# 上面的模組常數保留（import 時的快照，測試在用），實際執行一律走這些函式：
# 設定頁改了模型，**不用重啟**就生效。
# ---------------------------------------------------------------------------

def provider() -> str:
    env = os.environ.get("LLM_PROVIDER")
    return (env or str(settings.llm("provider"))).lower()


def gemini_model() -> str:
    return os.environ.get("GEMINI_MODEL") or str(settings.llm("gemini_model"))


def anthropic_model() -> str:
    return os.environ.get("ANTHROPIC_MODEL") or str(settings.llm("anthropic_model"))


def temperature() -> float:
    env = os.environ.get("LLM_TEMPERATURE")
    return float(env) if env else float(settings.llm("temperature"))

TIMEOUT = 180  # 大 prompt + 大輸出，120 秒不夠

# 暫時性錯誤：伺服器忙、額度節流。這些要重試，不是放棄。
# 503 UNAVAILABLE 在 Gemini 上很常見——尤其是熱門模型 + 大 prompt。
TRANSIENT_STATUS = {408, 429, 500, 502, 503, 504}
MAX_ATTEMPTS = 5
BACKOFF_BASE = 2.0  # 2s → 4s → 8s → 16s


def _gemini_key() -> str:
    for name in GEMINI_KEY_NAMES:
        if os.environ.get(name):
            return os.environ[name]
    raise PipelineError(
        ErrorCode.MISSING_INPUT,
        f"找不到 Gemini API key（找過 {' / '.join(GEMINI_KEY_NAMES)}）",
        hint="設成系統環境變數，或寫進專案的 .env",
    )


def _post_json(url: str, payload: dict, headers: dict, verbose: bool = True) -> dict:
    """POST 一個 JSON，暫時性錯誤自動重試（指數退避 + 抖動）。

    503 不是「壞掉了」，是「現在很忙，等一下再來」。之前沒重試，
    一遇到就整條 pipeline 死掉——那不是模型的問題，是我的問題。
    """
    req = urllib.request.Request(
        url,
        data=json.dumps(payload).encode("utf-8"),
        headers={"Content-Type": "application/json", **headers},
        method="POST",
    )

    for attempt in range(1, MAX_ATTEMPTS + 1):
        try:
            with urllib.request.urlopen(req, timeout=TIMEOUT) as resp:
                return json.loads(resp.read().decode("utf-8"))

        except urllib.error.HTTPError as e:
            body = e.read().decode("utf-8", "replace")[:400]
            transient = e.code in TRANSIENT_STATUS
            if not transient or attempt == MAX_ATTEMPTS:
                raise PipelineError(
                    ErrorCode.MISSING_INPUT,
                    f"API 回 HTTP {e.code}（試了 {attempt} 次）：{body}",
                    hint=_http_hint(e.code),
                ) from e
            wait = BACKOFF_BASE ** attempt + random.uniform(0, 1)
            if verbose:
                print(f"    HTTP {e.code}（暫時性）→ {wait:.1f}s 後重試（{attempt}/{MAX_ATTEMPTS - 1}）")
            time.sleep(wait)

        # 讀取逾時跟 503 一樣是暫時性的——大 prompt 有時就是慢。
        # 之前只接了 URLError，TimeoutError 直接噴 traceback 給人看，很難看。
        except (urllib.error.URLError, socket.timeout, TimeoutError) as e:
            reason = getattr(e, "reason", e)
            if attempt == MAX_ATTEMPTS:
                raise PipelineError(
                    ErrorCode.MISSING_INPUT,
                    f"連不到 API 或逾時（試了 {attempt} 次）：{reason}",
                    hint="網路問題，或模型太忙。等一下再跑，或換 GEMINI_MODEL=gemini-2.0-flash",
                ) from e
            wait = BACKOFF_BASE ** attempt + random.uniform(0, 1)
            if verbose:
                print(f"    連線問題（{reason}）→ {wait:.1f}s 後重試（{attempt}/{MAX_ATTEMPTS - 1}）")
            time.sleep(wait)

    raise AssertionError("unreachable")  # pragma: no cover


def _http_hint(code: int) -> str:
    return {
        401: "key 不對",
        403: "key 沒開通 Generative Language API",
        404: "模型名稱不存在——設環境變數 GEMINI_MODEL 換一個（煙霧測試會列出可用清單）",
        429: "額度用完或速率超限。等一下，或換 gemini-2.5-flash-lite 這種較不熱門的模型",
        503: "模型忙不過來（重試多次仍失敗）。換一個模型試試：set GEMINI_MODEL=gemini-2.0-flash",
    }.get(code, "看上面的原始錯誤訊息")


def list_models() -> list[str]:
    """列出這把 key 實際能用的模型。模型名稱會改，別把它寫死在腦子裡——問就好。"""
    url = f"{GEMINI_BASE}/models?key={_gemini_key()}"
    with urllib.request.urlopen(url, timeout=TIMEOUT) as resp:
        data = json.loads(resp.read().decode("utf-8"))
    return [
        m["name"].removeprefix("models/")
        for m in data.get("models", [])
        if "generateContent" in m.get("supportedGenerationMethods", [])
    ]


def gemini(prompt: str) -> str:
    url = f"{GEMINI_BASE}/models/{gemini_model()}:generateContent?key={_gemini_key()}"
    data = _post_json(
        url,
        {
            "contents": [{"parts": [{"text": prompt}]}],
            "generationConfig": {
                "temperature": temperature(),
                # v3 的產出（1–3 則貼文 × 每張卡的 evidence）比 v2 大得多。
                # 而且 Gemini 2.5 的 **thinking token 會吃掉 output 預算**——
                # 它先想一大堆，剩下的額度不夠寫完 JSON，就從中間被切斷。
                # 這是我第二次栽在截斷上：第一次是額度太小，第二次是額度被 thinking 吃掉。
                "maxOutputTokens": int(settings.adv("max_output_tokens")),
                "thinkingConfig": {"thinkingBudget": 0},  # 這是結構化抽取，不需要它先想
                # 直接要求回 JSON，省掉「模型愛加 markdown 圍欄」的那一類麻煩
                "responseMimeType": "application/json",
            },
        },
        headers={},
    )
    cand = (data.get("candidates") or [{}])[0]
    reason = cand.get("finishReason")

    # 截斷是沉默的殺手：拿到半截 JSON，parse 只會說「找不到 JSON」，
    # 讓人以為是格式問題。這裡直接把真正的原因講出來。
    if reason == "MAX_TOKENS":
        raise PipelineError(
            ErrorCode.SCHEMA_INVALID,
            "Gemini 的輸出被 maxOutputTokens 截斷了（回傳半截 JSON）",
            hint="調高 src/llm.py 的 maxOutputTokens，或在 prompt 裡要求更精簡的 evidence",
        )
    try:
        return cand["content"]["parts"][0]["text"]
    except (KeyError, IndexError) as e:
        raise PipelineError(
            ErrorCode.SCHEMA_INVALID,
            f"Gemini 回應裡沒有文字內容（finishReason={reason}）",
            hint=f"可能被安全過濾擋下。promptFeedback={data.get('promptFeedback')}",
        ) from e


def anthropic(prompt: str) -> str:
    key = os.environ.get("ANTHROPIC_API_KEY")
    if not key:
        raise PipelineError(
            ErrorCode.MISSING_INPUT,
            "沒有 ANTHROPIC_API_KEY",
            hint="設環境變數，或把 LLM_PROVIDER 改成 gemini",
        )
    data = _post_json(
        "https://api.anthropic.com/v1/messages",
        {
            "model": anthropic_model(),
            "max_tokens": 8192,
            "messages": [{"role": "user", "content": prompt}],
        },
        headers={"x-api-key": key, "anthropic-version": "2023-06-01"},
    )
    return data["content"][0]["text"]


PROVIDERS: dict[str, LLMFn] = {"gemini": gemini, "anthropic": anthropic}


def get_llm() -> LLMFn:
    p = provider()
    if p not in PROVIDERS:
        raise PipelineError(
            ErrorCode.MISSING_INPUT,
            f"未知的 LLM_PROVIDER：{p}",
            hint=f"可用：{', '.join(PROVIDERS)}",
        )
    return PROVIDERS[p]


def current_model() -> str:
    return gemini_model() if provider() == "gemini" else anthropic_model()


__all__ = ["get_llm", "list_models", "current_model", "PROVIDER", "LLMFn"]
