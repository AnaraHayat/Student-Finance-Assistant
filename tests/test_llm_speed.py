import requests

from agent import llm
from agent.llm import GeminiModel

OK = {"candidates": [{"content": {"parts": [{"text": "hi"}]}}]}


class Resp:
    def __init__(self, status, data=None):
        self.status_code = status
        self._data = data or {}

    def json(self):
        return self._data


def patch(monkeypatch, responses):
    calls = []

    def fake(url, headers=None, json=None, timeout=None):
        calls.append((url, timeout))
        r = responses.pop(0)
        if isinstance(r, Exception):
            raise r
        return r

    monkeypatch.setattr(llm.requests, "post", fake)
    return calls


def test_primary_and_fallback_have_different_timeouts(monkeypatch):
    calls = patch(monkeypatch, [requests.ReadTimeout("slow"), Resp(200, OK)])
    GeminiModel("k", "main", "fb", timeout=12,
                fallback_timeout=30).generate([], [], "s")
    assert calls[0][1] == (5, 12)
    assert calls[1][1] == (5, 30)


def test_failed_primary_is_skipped_on_next_call(monkeypatch):
    calls = patch(monkeypatch, [requests.ReadTimeout("slow"),
                                Resp(200, OK), Resp(200, OK)])
    m = GeminiModel("k", "main", "fb", cooldown=60)
    m.generate([], [], "s")
    m.generate([], [], "s")
    assert len(calls) == 3
    assert calls[2][0].endswith("/fb:generateContent")


def test_primary_is_retried_after_cooldown(monkeypatch):
    calls = patch(monkeypatch, [requests.ReadTimeout("slow"),
                                Resp(200, OK), Resp(200, OK)])
    m = GeminiModel("k", "main", "fb", cooldown=0)
    m.generate([], [], "s")
    m.generate([], [], "s")
    assert calls[2][0].endswith("/main:generateContent")


def test_from_env_reads_timeouts(monkeypatch):
    monkeypatch.setenv("LLM_API_KEY", "k")
    monkeypatch.setenv("LLM_TIMEOUT", "9")
    monkeypatch.setenv("LLM_FALLBACK_TIMEOUT", "21")
    m = GeminiModel.from_env()
    assert m.timeout == 9
    assert m.fallback_timeout == 21


def test_from_env_ignores_bad_timeout(monkeypatch):
    monkeypatch.setenv("LLM_API_KEY", "k")
    monkeypatch.setenv("LLM_TIMEOUT", "abc")
    monkeypatch.setenv("LLM_FALLBACK_TIMEOUT", "-5")
    m = GeminiModel.from_env()
    assert m.timeout == 15
    assert m.fallback_timeout == 30