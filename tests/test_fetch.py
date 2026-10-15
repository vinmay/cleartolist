from datetime import date

import httpx

from ingest import cpsc


def _client(handler):
    return httpx.Client(transport=httpx.MockTransport(handler))


def test_retries_on_429_then_caches(tmp_path, monkeypatch):
    monkeypatch.setattr(cpsc, "CACHE_DIR", tmp_path)
    monkeypatch.setattr(cpsc, "REQUEST_GAP_S", 0)
    monkeypatch.setattr(cpsc.time, "sleep", lambda s: None)
    calls = []

    def handler(req):
        calls.append(req)
        assert req.url.params["RecallDateStart"] == "2026-01-01"
        if len(calls) == 1:
            return httpx.Response(429, headers={"Retry-After": "1"})
        return httpx.Response(200, json=[{"RecallID": 1}])

    c = _client(handler)
    assert cpsc.fetch_window(c, date(2026, 1, 1), date(2026, 1, 31)) == [{"RecallID": 1}]
    assert len(calls) == 2
    # second call is served from the cache
    assert cpsc.fetch_window(c, date(2026, 1, 1), date(2026, 1, 31)) == [{"RecallID": 1}]
    assert len(calls) == 2


def test_falls_back_to_stale_cache_when_api_down(tmp_path, monkeypatch):
    monkeypatch.setattr(cpsc, "CACHE_DIR", tmp_path)
    monkeypatch.setattr(cpsc, "REQUEST_GAP_S", 0)
    monkeypatch.setattr(cpsc, "CACHE_TTL_S", -1)  # everything counts as stale
    monkeypatch.setattr(cpsc.time, "sleep", lambda s: None)
    ok = _client(lambda req: httpx.Response(200, json=[{"RecallID": 2}]))
    cpsc.fetch_window(ok, date(2026, 2, 1), date(2026, 2, 28))
    down = _client(lambda req: httpx.Response(503))
    assert cpsc.fetch_window(down, date(2026, 2, 1), date(2026, 2, 28)) == [{"RecallID": 2}]
