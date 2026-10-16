import os
import time

import requests

BASE = "https://generativelanguage.googleapis.com/v1beta/models"
CONNECT_TIMEOUT = 5
DEFAULT_TIMEOUT = 15
DEFAULT_FALLBACK_TIMEOUT = 30
DEFAULT_COOLDOWN = 120


class LLMError(Exception):
    pass


def _env_float(name, default):
    try:
        value = float(os.getenv(name, default))
    except ValueError:
        return default
    return value if value > 0 else default


class GeminiModel:
    def __init__(self, api_key, model, fallback_model=None,
                 timeout=DEFAULT_TIMEOUT,
                 fallback_timeout=DEFAULT_FALLBACK_TIMEOUT,
                 cooldown=DEFAULT_COOLDOWN):
        if not api_key:
            raise LLMError("missing API key")
        self.api_key = api_key
        self.model = model
        self.fallback_model = fallback_model
        self.timeout = timeout
        self.fallback_timeout = fallback_timeout
        self.cooldown = cooldown
        self._primary_down_until = 0.0

    @classmethod
    def from_env(cls):
        from dotenv import load_dotenv
        load_dotenv()
        return cls(os.getenv("LLM_API_KEY", ""),
                   os.getenv("LLM_MODEL", "gemini-3.8-flash"),
                   os.getenv("LLM_FALLBACK_MODEL") or None,
                   _env_float("LLM_TIMEOUT", DEFAULT_TIMEOUT),
                   _env_float("LLM_FALLBACK_TIMEOUT", DEFAULT_FALLBACK_TIMEOUT))

    @staticmethod
    def _note(name, started, status):
        print(f"[llm] {name}: {status} after {time.time() - started:.1f}s",
              flush=True)

    def _failed(self, name):
        if self.fallback_model and name == self.model:
            self._primary_down_until = time.monotonic() + self.cooldown

    def generate(self, contents, tools, system):
        body = {"contents": contents, "tools": tools,
                "systemInstruction": {"parts": [{"text": system}]}}
        headers = {"x-goog-api-key": self.api_key}

        attempts = [(self.model, self.timeout)]
        if self.fallback_model:
            attempts.append((self.fallback_model, self.fallback_timeout))
            if time.monotonic() < self._primary_down_until:
                attempts.reverse()

        errors = []
        for name, read_timeout in attempts:
            started = time.time()
            try:
                r = requests.post(f"{BASE}/{name}:generateContent",
                                  headers=headers, json=body,
                                  timeout=(CONNECT_TIMEOUT, read_timeout))
            except requests.RequestException as e:
                self._note(name, started, type(e).__name__)
                errors.append(f"{name}: {type(e).__name__}")
                self._failed(name)
                continue
            if r.status_code != 200:
                self._note(name, started, f"HTTP {r.status_code}")
                errors.append(f"{name}: HTTP {r.status_code}")
                self._failed(name)
                continue
            candidates = r.json().get("candidates") or []
            parts = (candidates[0].get("content", {}).get("parts")
                     if candidates else None)
            if not parts:
                self._note(name, started, "empty response")
                errors.append(f"{name}: empty response")
                self._failed(name)
                continue
            self._note(name, started, "ok")
            if name == self.model:
                self._primary_down_until = 0.0
            return parts
        raise LLMError("; ".join(errors))