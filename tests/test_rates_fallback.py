import json
from decimal import Decimal as D

import pytest
import requests

from core import rates


def _offline(*args, **kwargs):
    raise requests.ConnectionError("offline")


def test_falls_back_to_cache_when_offline(tmp_path, monkeypatch):
    cache = tmp_path / "rate_cache.json"
    cache.write_text(json.dumps({
        "pkr_per_cny": "41.5", "as_of": "a", "next_update": "b"}),
        encoding="utf-8")
    monkeypatch.setattr(rates, "CACHE", cache)
    monkeypatch.setattr(rates.requests, "get", _offline)

    r = rates.get_rate()
    assert r.from_cache is True
    assert r.pkr_per_cny == D("41.5")


def test_raises_when_offline_and_no_cache(tmp_path, monkeypatch):
    monkeypatch.setattr(rates, "CACHE", tmp_path / "missing.json")
    monkeypatch.setattr(rates.requests, "get", _offline)

    with pytest.raises(FileNotFoundError):
        rates.get_rate()
