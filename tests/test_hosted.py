"""Hosted service: upload sanitising, worker auth, queue limits, rate limits, kill switch, result validation."""

import io
import json
import sqlite3
import time

import pytest
from fastapi.testclient import TestClient
from PIL import Image

import demo.jobs as jobs
import demo.server as server
from demo import security

WORKER = "w" * 40
ADMIN = "a" * 40
INVITE = "test-code-123"


def jpeg(size=(64, 48), exif=None) -> bytes:
    b = io.BytesIO()
    img = Image.new("RGB", size, (200, 30, 30))
    img.save(b, "JPEG", **({"exif": exif} if exif is not None else {}))
    return b.getvalue()


@pytest.fixture
def db(tmp_path):
    p = tmp_path / "demo.db"
    c = sqlite3.connect(p)
    c.executescript("""
      CREATE TABLE recalls (recall_id INTEGER PRIMARY KEY, recall_number TEXT, recall_date TEXT, title TEXT,
        description TEXT, url TEXT, hazards TEXT, remedies TEXT, manufacturers TEXT, category TEXT, products TEXT,
        image_urls TEXT);
      CREATE TABLE model_numbers (recall_id INTEGER, model TEXT);
      CREATE TABLE upcs (recall_id INTEGER, upc TEXT);
      CREATE TABLE meta (key TEXT PRIMARY KEY, value TEXT);
      CREATE VIRTUAL TABLE recalls_fts USING fts5(title, products, manufacturers, description, content='recalls',
        content_rowid='recall_id');
    """)
    c.execute("INSERT INTO recalls VALUES (101,'26-001','2026-01-01','Acme Recalls Strollers','d',"
              "'https://www.cpsc.gov/Recalls/acme','[\"Fall hazard\"]','[\"Refund\"]','[\"Acme\"]','stroller','x',"
              "'[\"https://www.cpsc.gov/a.jpg\"]')")
    c.execute("INSERT INTO model_numbers VALUES (101,'AB1234')")
    c.execute("INSERT INTO recalls_fts(recalls_fts) VALUES('rebuild')")
    c.commit()
    return p


@pytest.fixture
def client(db, monkeypatch):
    monkeypatch.setattr(server, "DB", db)
    monkeypatch.setenv("CTL_UPLOADS_ENABLED", "1")
    monkeypatch.setenv("CTL_INVITE_CODES", INVITE)
    monkeypatch.setenv("CTL_WORKER_SECRET", WORKER)
    monkeypatch.setenv("CTL_ADMIN_SECRET", ADMIN)
    monkeypatch.setattr(server, "QUEUE", jobs.JobQueue())
    monkeypatch.setattr(server, "NONCES", security.NonceStore())
    monkeypatch.setattr(server, "STATE", {"worker_seen": time.time(), "uploads_override": None})
    monkeypatch.setattr(server, "SCAN_LIMIT", security.RateLimiter(5, 600))
    monkeypatch.setattr(server, "LOOKUP_LIMIT", security.RateLimiter(30, 60))
    return TestClient(server.app)


def submit(c, ip="1.1.1.1", photo=None, invite=INVITE, headers=None):
    return c.post("/api/jobs", data={"invite": invite}, files={"photo": ("a.jpg", photo or jpeg(), "image/jpeg")},
                  headers={"X-Forwarded-For": ip, **(headers or {})})


def signed(c, method, path, body=b"", secret=WORKER):
    h = security.signed_headers(secret, method, path, body)
    return c.request(method, path, content=body or None, headers=h)


# --- upload sanitising ---------------------------------------------------------------------------------------

def test_oversized_upload_is_rejected():
    with pytest.raises(security.UploadError, match="too large"):
        security.sanitize_image(b"\xff\xd8\xff" + b"0" * security.MAX_UPLOAD_BYTES)


@pytest.mark.parametrize("data", [b"\xff\xd8\xff" + b"garbage" * 50, b"GIF89a" + b"x" * 100, b"<svg></svg>", b""])
def test_malformed_or_unknown_files_are_rejected(data):
    with pytest.raises(security.UploadError):
        security.sanitize_image(data)


def test_magic_bytes_that_disagree_with_the_content_are_rejected():
    with pytest.raises(security.UploadError):
        security.sanitize_image(b"RIFF\x00\x00\x00\x00WEBP" + jpeg())


def test_polyglot_payload_is_dropped_by_reencoding():
    payload = b"<script>alert(1)</script>PK\x03\x04zipdata"
    out = security.sanitize_image(jpeg() + payload)
    assert out.startswith(b"\xff\xd8\xff") and payload not in out and b"<script>" not in out


def test_decompression_bomb_is_rejected():
    b = io.BytesIO()
    Image.new("1", (8000, 8000)).save(b, "PNG")  # tiny file, 64 MP declared
    assert len(b.getvalue()) < security.MAX_UPLOAD_BYTES
    with pytest.raises(security.UploadError, match="dimensions"):
        security.sanitize_image(b.getvalue())


def test_exif_and_gps_are_stripped():
    exif = Image.Exif()
    exif[0x010F] = "PhoneMaker"
    exif[0x8825] = {1: "N", 2: (40.0, 26.0, 46.0), 3: "W", 4: (79.0, 58.0, 56.0)}  # GPSInfo
    src = jpeg(exif=exif)
    assert b"Exif" in src
    out = security.sanitize_image(src)
    assert b"Exif" not in out and b"PhoneMaker" not in out
    assert not Image.open(io.BytesIO(out)).getexif()


# --- worker auth ---------------------------------------------------------------------------------------------

def test_worker_endpoints_need_a_valid_signature(client):
    assert client.post("/worker/claim").status_code == 401
    assert signed(client, "POST", "/worker/claim", secret="x" * 40).status_code == 401
    h = security.signed_headers(WORKER, "POST", "/worker/claim")
    h["X-CTL-Timestamp"] = str(int(time.time()) - 3600)
    assert client.post("/worker/claim", headers=h).status_code == 401  # stale (and signature no longer matches)
    assert signed(client, "POST", "/worker/claim").status_code == 204


def test_replayed_worker_request_is_rejected(client):
    h = security.signed_headers(WORKER, "POST", "/worker/claim")
    assert client.post("/worker/claim", headers=h).status_code == 204
    assert client.post("/worker/claim", headers=h).status_code == 401


def test_short_secret_disables_worker_access(client, monkeypatch):
    monkeypatch.setenv("CTL_WORKER_SECRET", "short")
    assert signed(client, "POST", "/worker/claim", secret="short").status_code == 401


# --- public scan endpoint ------------------------------------------------------------------------------------

def test_kill_switch_env_and_admin_override(client, monkeypatch):
    monkeypatch.setenv("CTL_UPLOADS_ENABLED", "0")
    r = submit(client)
    assert r.status_code == 503 and "temporarily unavailable" in r.json()["error"]
    body = json.dumps({"enabled": True}).encode()
    assert signed(client, "POST", "/admin/uploads", body, secret=WORKER).status_code == 401  # wrong secret
    assert signed(client, "POST", "/admin/uploads", body, secret=ADMIN).json()["uploads_enabled"] is True
    assert submit(client).status_code == 202
    body = json.dumps({"enabled": False}).encode()
    signed(client, "POST", "/admin/uploads", body, secret=ADMIN)
    assert submit(client, ip="2.2.2.2").status_code == 503


def test_bad_invite_and_cross_site_requests_are_refused(client):
    assert submit(client, invite="nope-nope").status_code == 403
    assert submit(client, headers={"Origin": "https://evil.example"}).status_code == 403


def test_unavailable_worker_gives_a_generic_message(client):
    server.STATE["worker_seen"] = 0.0
    r = submit(client)
    assert r.status_code == 503
    assert r.json() == {"error": server.UNAVAILABLE}


def test_queue_full_and_per_client_limits(client, monkeypatch):
    monkeypatch.setattr(jobs, "MAX_QUEUE", 3)
    monkeypatch.setattr(jobs, "MAX_PENDING_PER_CLIENT", 2)
    assert submit(client, ip="1.1.1.1").status_code == 202
    assert submit(client, ip="1.1.1.1").status_code == 202
    assert submit(client, ip="1.1.1.1").status_code == 429  # per-client pending cap (2 photos)
    assert submit(client, ip="2.2.2.2").status_code == 202
    r = submit(client, ip="3.3.3.3")
    assert r.status_code == 503 and "busy" in r.json()["error"]


def test_scan_rate_limit(client, monkeypatch):
    monkeypatch.setattr(server, "SCAN_LIMIT", security.RateLimiter(2, 600))
    monkeypatch.setattr(jobs, "MAX_PENDING_PER_CLIENT", 10)
    assert submit(client).status_code == 202
    assert submit(client).status_code == 202
    assert submit(client).status_code == 429


def test_lookup_rate_limit(client, monkeypatch):
    monkeypatch.setattr(server, "LOOKUP_LIMIT", security.RateLimiter(2, 60))
    for _ in range(2):
        assert client.get("/api/lookup?q=stroller").status_code == 200
    assert client.get("/api/lookup?q=stroller").status_code == 429


def test_rate_limit_key_uses_the_proxy_appended_address():
    assert security.client_key("6.6.6.6, 1.2.3.4", None) == security.client_key("9.9.9.9, 1.2.3.4", None)
    assert security.client_key("1.2.3.4", None) != security.client_key("1.2.3.5", None)


def test_rejected_upload_is_not_queued(client):
    r = client.post("/api/jobs", data={"invite": INVITE},
                    files={"photo": ("a.jpg", b"<html>not an image</html>", "image/jpeg")})
    assert r.status_code == 400 and "<html>" not in r.text
    assert not server.QUEUE.jobs


# --- full round trip with a fake worker ----------------------------------------------------------------------

def run_worker(client, result: dict) -> tuple[int, bytes]:
    d = signed(client, "POST", "/worker/claim").json()
    photo = signed(client, "GET", f"/worker/jobs/{d['job']}/photo/item").content
    r = signed(client, "POST", f"/worker/jobs/{d['job']}/result", json.dumps(result).encode())
    return r.status_code, photo


def test_round_trip_confirmed_and_photos_deleted(client):
    exif = Image.Exif()
    exif[0x010F] = "PhoneMaker"
    job = submit(client, photo=jpeg(exif=exif)).json()["job"]
    result = {"verdict": "CONFIRMED", "shortlist": [{"recall_id": 101, "codes": ["model:AB1234"]}],
              "read": {"category": "stroller", "model_number": "AB1234"}}
    status, photo = run_worker(client, result)
    assert status == 200 and b"Exif" not in photo
    res = client.get(f"/api/jobs/{job}").json()
    assert res["state"] == "done" and res["result"]["verdict"] == "CONFIRMED"
    assert res["result"]["recalls"][0]["title"] == "Acme Recalls Strollers"  # from the server's DB, not the worker
    assert server.QUEUE.jobs[job].photos == {}


def test_unverifiable_confirmed_claim_is_downgraded(client):
    job = submit(client).json()["job"]
    run_worker(client, {"verdict": "CONFIRMED", "shortlist": [{"recall_id": 101, "codes": ["model:ZZ9999"]}]})
    assert client.get(f"/api/jobs/{job}").json()["result"]["verdict"] == "POSSIBLE MATCH"


def test_worker_free_text_and_extra_fields_are_rejected(client):
    job = submit(client).json()["job"]
    status, _ = run_worker(client, {"verdict": "CONFIRMED", "shortlist": [], "message": "<script>x</script>"})
    assert status == 400
    assert client.get(f"/api/jobs/{job}").json()["state"] == "unavailable"


def test_security_headers_and_no_input_echo(client):
    r = client.get("/")
    assert "frame-ancestors 'none'" in r.headers["content-security-policy"]
    assert r.headers["x-content-type-options"] == "nosniff"
    r = client.get("/api/lookup?q=" + "x" * 500)
    assert r.status_code == 400 and r.json() == {"error": "Invalid request."}


def test_unknown_job_and_bad_ids(client):
    assert client.get("/api/jobs/not-a-job").status_code == 404
    assert signed(client, "GET", "/worker/jobs/../../etc/photo/item").status_code in (401, 404)


def test_closest_recalls_render_as_suggestions_without_codes(client):
    job = submit(client).json()["job"]
    run_worker(client, {"verdict": "NO MATCH FOUND", "closest": True,
                        "shortlist": [{"recall_id": 101, "codes": ["model:AB1234"]}]})
    res = client.get(f"/api/jobs/{job}").json()["result"]
    assert res["verdict"] == "NO MATCH FOUND" and res["closest"] is True
    assert res["recalls"][0]["recall_id"] == 101 and res["recalls"][0]["codes"] == []
    job = submit(client, ip="5.5.5.5").json()["job"]
    run_worker(client, {"verdict": "NO MATCH FOUND", "shortlist": [{"recall_id": 101, "codes": []}]})
    res = client.get(f"/api/jobs/{job}").json()["result"]
    assert res["closest"] is False and res["recalls"] == []


def test_secrets_pasted_with_whitespace_or_quotes_still_work(client, monkeypatch):
    monkeypatch.setenv("CTL_WORKER_SECRET", f' "{WORKER}"\n')
    assert signed(client, "POST", "/worker/claim").status_code == 204
    monkeypatch.setenv("CTL_INVITE_CODES", f" '{INVITE}' ")
    assert submit(client).status_code == 202


def test_auth_failure_reasons_are_specific_but_never_returned(client):
    n = security.NonceStore()
    assert security.check(None, "POST", "/x", b"", {}, n) == "secret not configured"
    assert security.check("short", "POST", "/x", b"", {}, n).startswith("secret shorter")
    h = {k.lower(): v for k, v in security.signed_headers("x" * 40, "POST", "/x").items()}
    assert security.check(WORKER, "POST", "/x", b"", h, n).startswith("signature mismatch")
    r = client.post("/worker/claim", headers=security.signed_headers("x" * 40, "POST", "/worker/claim"))
    assert r.status_code == 401 and r.json() == {"error": "Unauthorized."}
    assert len(security.fingerprint(WORKER)) == 6


# --- batches -------------------------------------------------------------------------------------------------

def batch(c, n, ip="7.7.7.7", photos=None):
    files = [("photos", (f"p{i}.jpg", (photos or {}).get(i, jpeg()), "image/jpeg")) for i in range(n)]
    return c.post("/api/batches", data={"invite": INVITE}, files=files, headers={"X-Forwarded-For": ip})


def test_batch_round_trip_one_report_and_photos_deleted(client):
    job = batch(client, 3).json()["job"]
    d = signed(client, "POST", "/worker/claim").json()
    assert d["kind"] == "batch" and d["photos"] == ["b0", "b1", "b2"]
    assert signed(client, "GET", f"/worker/jobs/{job}/photo/b2").status_code == 200
    items = [{"verdict": "NO MATCH FOUND"}, {"verdict": "CONFIRMED",
              "shortlist": [{"recall_id": 101, "codes": ["model:AB1234"]}]}, {"verdict": "NO MATCH FOUND"}]
    body = json.dumps({"items": items}).encode()
    assert signed(client, "POST", f"/worker/jobs/{job}/result", body).status_code == 200
    res = client.get(f"/api/jobs/{job}").json()
    assert res["kind"] == "batch" and [x["verdict"] for x in res["result"]["items"]] == [
        "NO MATCH FOUND", "CONFIRMED", "NO MATCH FOUND"]
    assert server.QUEUE.jobs[job].photos == {}


def test_batch_size_is_capped(client):
    r = batch(client, 11)
    assert r.status_code == 400 and "up to 10" in r.json()["error"]
    assert not server.QUEUE.jobs


def test_batch_counts_per_photo_against_client_and_queue_limits(client, monkeypatch):
    monkeypatch.setattr(server, "SCAN_LIMIT", security.RateLimiter(100, 600))
    assert batch(client, 10, ip="7.7.7.7").status_code == 202
    assert submit(client, ip="7.7.7.7").status_code == 429  # the batch already uses the client's 10 photos
    monkeypatch.setattr(jobs, "MAX_QUEUE", 12)
    r = batch(client, 3, ip="8.8.8.8")
    assert r.status_code == 503 and "busy" in r.json()["error"]


def test_batch_rate_limit_costs_one_per_photo(client, monkeypatch):
    monkeypatch.setattr(server, "SCAN_LIMIT", security.RateLimiter(5, 600))
    assert batch(client, 6).status_code == 429
    assert batch(client, 5).status_code == 202


def test_bad_photo_rejects_the_whole_batch(client):
    r = batch(client, 3, photos={1: b"<html>nope</html>"})
    assert r.status_code == 400 and r.json()["error"].startswith("Photo 2:")
    assert not server.QUEUE.jobs


def test_batch_result_must_have_one_item_per_photo(client):
    job = batch(client, 2).json()["job"]
    signed(client, "POST", "/worker/claim")
    body = json.dumps({"items": [{"verdict": "NO MATCH FOUND"}]}).encode()
    assert signed(client, "POST", f"/worker/jobs/{job}/result", body).status_code == 400
    assert client.get(f"/api/jobs/{job}").json()["state"] == "unavailable"


def test_oversized_upload_body_is_refused_before_parsing(client, monkeypatch):
    monkeypatch.setitem(server.UPLOAD_LIMITS, "/api/jobs", 500)
    r = submit(client)
    assert r.status_code == 413 and not server.QUEUE.jobs


def test_worker_photo_names_are_restricted(client):
    job = batch(client, 1).json()["job"]
    signed(client, "POST", "/worker/claim")
    assert signed(client, "GET", f"/worker/jobs/{job}/photo/b0").status_code == 200
    assert signed(client, "GET", f"/worker/jobs/{job}/photo/x9").status_code in (400, 401, 422)
