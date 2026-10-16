import pytest
import requests

from agent import llm
from agent.llm import GeminiModel, LLMError

OK = {"candidates": [{"content": {"parts": [{"text": "hi"}]}}]}


class Resp:
    def __init__(self, status, data=None):
        self.status_code = status
        self._data = data or {}

    def json(self):
        return self._data


def patch(monkeypatch, responses):
    urls = []

    def fake(url, headers=None, json=None, timeout=None):
        urls.append(url)
        r = responses.pop(0)
        if isinstance(r, Exception):
            raise r
        return r

    monkeypatch.setattr(llm.requests, "post", fake)
    return urls


def test_success_returns_parts(monkeypatch):
    urls = patch(monkeypatch, [Resp(200, OK)])
    parts = GeminiModel("k", "main", "fb").generate([], [], "sys")
    assert parts == [{"text": "hi"}]
    assert len(urls) == 1 and "main" in urls[0]


def test_falls_back_on_http_error(monkeypatch):
    urls = patch(monkeypatch, [Resp(429), Resp(200, OK)])
    parts = GeminiModel("k", "main", "fb").generate([], [], "sys")
    assert parts == [{"text": "hi"}]
    assert "fb" in urls[1]


def test_falls_back_on_network_error(monkeypatch):
    patch(monkeypatch, [requests.ConnectionError("x"), Resp(200, OK)])
    parts = GeminiModel("k", "main", "fb").generate([], [], "sys")
    assert parts == [{"text": "hi"}]


def test_both_fail_raises_without_leaking_key(monkeypatch):
    patch(monkeypatch, [Resp(404), Resp(500)])
    with pytest.raises(LLMError) as e:
        GeminiModel("SECRETKEY", "main", "fb").generate([], [], "sys")
    assert "404" in str(e.value) and "500" in str(e.value)
    assert "SECRETKEY" not in str(e.value)


def test_empty_candidates_raises(monkeypatch):
    patch(monkeypatch, [Resp(200, {"candidates": []}),
                        Resp(200, {"candidates": []})])
    with pytest.raises(LLMError):
        GeminiModel("k", "main", "fb").generate([], [], "sys")


def test_missing_key_raises():
    with pytest.raises(LLMError):
        GeminiModel("", "main")