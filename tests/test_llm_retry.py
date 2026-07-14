"""重試邏輯的測試。

503 不是「壞掉了」，是「現在很忙」。第一次跑 Gemini 就撞到 503——
當時程式直接死掉，那不是模型的問題，是我沒寫重試。
"""

from __future__ import annotations

import urllib.error

import pytest

import src.llm as llm
from src.errors import PipelineError


class _FakeResponse:
    def __init__(self, payload: bytes) -> None:
        self._payload = payload

    def read(self) -> bytes:
        return self._payload

    def __enter__(self):
        return self

    def __exit__(self, *_) -> None:
        return None


def _http_error(code: int) -> urllib.error.HTTPError:
    import io

    return urllib.error.HTTPError(
        "https://x", code, "boom", {}, io.BytesIO(b'{"error":{"message":"busy"}}')
    )


def test_transient_503_is_retried_then_succeeds(monkeypatch) -> None:
    calls = {"n": 0}

    def fake_urlopen(_req, timeout=None):
        calls["n"] += 1
        if calls["n"] < 3:
            raise _http_error(503)
        return _FakeResponse(b'{"ok": true}')

    monkeypatch.setattr(llm.urllib.request, "urlopen", fake_urlopen)
    monkeypatch.setattr(llm.time, "sleep", lambda _s: None)

    assert llm._post_json("https://x", {}, {}, verbose=False) == {"ok": True}
    assert calls["n"] == 3, "應該重試到成功"


def test_permanent_401_is_not_retried(monkeypatch) -> None:
    """key 錯了重試一百次也還是錯的。不要浪費時間，直接說。"""
    calls = {"n": 0}

    def fake_urlopen(_req, timeout=None):
        calls["n"] += 1
        raise _http_error(401)

    monkeypatch.setattr(llm.urllib.request, "urlopen", fake_urlopen)
    monkeypatch.setattr(llm.time, "sleep", lambda _s: None)

    with pytest.raises(PipelineError) as e:
        llm._post_json("https://x", {}, {}, verbose=False)
    assert calls["n"] == 1, "永久性錯誤不該重試"
    assert "key 不對" in e.value.hint


def test_gives_up_after_max_attempts(monkeypatch) -> None:
    def fake_urlopen(_req, timeout=None):
        raise _http_error(503)

    monkeypatch.setattr(llm.urllib.request, "urlopen", fake_urlopen)
    monkeypatch.setattr(llm.time, "sleep", lambda _s: None)

    with pytest.raises(PipelineError) as e:
        llm._post_json("https://x", {}, {}, verbose=False)
    assert f"試了 {llm.MAX_ATTEMPTS} 次" in e.value.message
    assert "換一個模型" in e.value.hint


def test_429_is_treated_as_transient() -> None:
    assert 429 in llm.TRANSIENT_STATUS
    assert 503 in llm.TRANSIENT_STATUS
    assert 401 not in llm.TRANSIENT_STATUS
    assert 404 not in llm.TRANSIENT_STATUS
