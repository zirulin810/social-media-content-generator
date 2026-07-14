"""供應商層的測試。

不打網路——只驗「設定錯的時候，錯誤訊息說不說得清楚」。
真正的連線由 scripts/smoke_test.py 在有 key 的機器上跑。
"""

from __future__ import annotations

import importlib

import pytest

from src.errors import ErrorCode, PipelineError


def test_missing_gemini_key_names_both_candidates(monkeypatch) -> None:
    """key 找不到時，要講清楚「我找過哪些名字」——不然人根本不知道要設什麼。"""
    import src.llm as llm

    for name in llm.GEMINI_KEY_NAMES:
        monkeypatch.delenv(name, raising=False)
    with pytest.raises(PipelineError) as e:
        llm._gemini_key()
    assert e.value.code == ErrorCode.MISSING_INPUT
    assert "GEMINI_API_KEY" in e.value.message
    assert "GOOGLE_API_KEY" in e.value.message


def test_gemini_key_accepts_either_env_name(monkeypatch) -> None:
    import src.llm as llm

    for name in llm.GEMINI_KEY_NAMES:
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("GOOGLE_API_KEY", "test-key-2")
    assert llm._gemini_key() == "test-key-2"

    monkeypatch.setenv("GEMINI_API_KEY", "test-key-1")
    assert llm._gemini_key() == "test-key-1"  # GEMINI_API_KEY 優先


def test_unknown_provider_is_rejected(monkeypatch) -> None:
    monkeypatch.setenv("LLM_PROVIDER", "openai")
    import src.llm as llm

    llm = importlib.reload(llm)
    with pytest.raises(PipelineError) as e:
        llm.get_llm()
    assert "gemini" in e.value.hint

    monkeypatch.delenv("LLM_PROVIDER")
    importlib.reload(llm)


def test_default_provider_is_gemini() -> None:
    import src.llm as llm

    assert llm.PROVIDER == "gemini"
    assert llm.get_llm() is llm.gemini
