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
    monkeypatch.setattr(server, "QUOTA", server.DailyQuota())
    from demo.store import Store

    monkeypatch.setattr(server, "STORE", Store("sqlite://memory"))
    monkeypatch.setattr(server, "COUNTS", server.analytics.Counts(server.STORE))
    monkeypatch.setattr(server, "EVENT_LIMIT", security.RateLimiter(60, 60))
    monkeypatch.setattr(server, "STATS_LIMIT", security.RateLimiter(10, 600))
    monkeypatch.setattr(server, "SIGNUP_LIMIT", security.RateLimiter(5, 3600))
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


# --- public daily quota and sample scans -------------------------------------------------------------------------

def test_scans_without_a_code_use_the_daily_quota(client, monkeypatch):
    monkeypatch.setenv("CTL_PUBLIC_DAILY_SCANS", "2")
    monkeypatch.setattr(jobs, "MAX_PENDING_PER_CLIENT", 10)
    assert submit(client, invite="").status_code == 202
    assert submit(client, invite="", ip="2.2.2.2").status_code == 202
    r = submit(client, invite="", ip="3.3.3.3")
    assert r.status_code == 429 and "free checks are used up" in r.json()["error"]
    assert client.get("/api/meta").json()["free_scans_today"] is False
    assert submit(client, ip="3.3.3.3").status_code == 202  # a valid invite code skips the quota


def test_a_wrong_code_is_refused_not_treated_as_no_code(client):
    assert submit(client, invite="wrong-code").status_code == 403


def test_quota_is_given_back_when_the_queue_is_full(client, monkeypatch):
    monkeypatch.setenv("CTL_PUBLIC_DAILY_SCANS", "5")
    monkeypatch.setattr(jobs, "MAX_QUEUE", 0)
    assert submit(client, invite="").status_code == 503
    assert server.QUOTA.left() == 5


def test_rejected_upload_does_not_use_the_quota(client, monkeypatch):
    monkeypatch.setenv("CTL_PUBLIC_DAILY_SCANS", "1")
    r = client.post("/api/jobs", files={"photo": ("a.jpg", b"not an image", "image/jpeg")})
    assert r.status_code == 400 and server.QUOTA.left() == 1


def test_batch_without_a_code_counts_each_photo_against_the_quota(client, monkeypatch):
    monkeypatch.setenv("CTL_PUBLIC_DAILY_SCANS", "4")
    files = [("photos", (f"p{i}.jpg", jpeg(), "image/jpeg")) for i in range(5)]
    r = client.post("/api/batches", files=files, headers={"X-Forwarded-For": "9.9.9.9"})
    assert r.status_code == 429 and server.QUOTA.left() == 4
    assert client.post("/api/batches", files=files[:4], headers={"X-Forwarded-For": "9.9.9.9"}).status_code == 202
    assert server.QUOTA.left() == 0


def test_quota_resets_each_utc_day(monkeypatch):
    q = server.DailyQuota()
    monkeypatch.setenv("CTL_PUBLIC_DAILY_SCANS", "1")
    assert q.take(1) and not q.take(1)
    q.day = "2000-01-01"
    assert q.take(1)


def test_sample_scans_need_no_code_and_run_a_real_job(client, monkeypatch):
    monkeypatch.setenv("CTL_PUBLIC_DAILY_SCANS", "0")
    r = client.post("/api/samples/recalled", headers={"X-Forwarded-For": "4.4.4.4"})
    assert r.status_code == 202
    d = signed(client, "POST", "/worker/claim").json()
    assert d["photos"] == ["item", "label"] and d["kind"] == "single"
    assert signed(client, "GET", f"/worker/jobs/{d['job']}/photo/label").content.startswith(b"\xff\xd8")
    assert client.post("/api/samples/nope").status_code == 404


def test_sample_scans_obey_kill_switch_rate_limit_queue_and_worker(client, monkeypatch):
    monkeypatch.setenv("CTL_UPLOADS_ENABLED", "0")
    assert client.post("/api/samples/check").status_code == 503
    monkeypatch.setenv("CTL_UPLOADS_ENABLED", "1")
    monkeypatch.setattr(server, "SCAN_LIMIT", security.RateLimiter(1, 600))
    assert client.post("/api/samples/check").status_code == 202
    assert client.post("/api/samples/check").status_code == 429
    monkeypatch.setattr(server, "SCAN_LIMIT", security.RateLimiter(100, 600))
    monkeypatch.setattr(jobs, "MAX_QUEUE", 1)
    assert client.post("/api/samples/nomatch", headers={"X-Forwarded-For": "6.6.6.6"}).status_code == 503
    server.STATE["worker_seen"] = 0.0
    assert client.post("/api/samples/nomatch", headers={"X-Forwarded-For": "6.6.6.7"}).status_code == 503
    assert client.post("/api/samples/check", headers={"Origin": "https://evil.example"}).status_code == 403


# --- invite requests -----------------------------------------------------------------------------------------------

def ask(c, ip="1.2.3.4", **kw):
    body = {"email": "Seller@Example.com", "consent": True} | kw
    return c.post("/api/invite-requests", json=body, headers={"X-Forwarded-For": ip})


def test_invite_request_stores_only_email_time_and_categories(client):
    r = ask(client, alerts=True, categories=["stroller", "toy"])
    assert r.status_code == 202 and r.json()["message"] == "Thanks. We'll email you a code within a day."
    rows = server.STORE.all()
    assert len(rows) == 1 and rows[0][0] == "seller@example.com" and rows[0][3] == "stroller,toy"
    assert len(rows[0]) == 4  # email, created_at, alerts, categories: no address, no user agent


def test_honeypot_looks_fine_but_stores_nothing(client):
    r = ask(client, website="http://spam.example")
    assert r.status_code == 202 and r.json()["ok"] and server.STORE.all() == []


def test_invite_request_needs_consent_valid_email_and_known_categories(client):
    assert ask(client, consent=False).status_code == 400
    assert ask(client, email="not-an-email").status_code == 400
    assert ask(client, email="a@b.c<script>").status_code == 400
    assert ask(client, alerts=True, categories=["guns"]).status_code == 400
    assert ask(client, alerts=True, categories=[]).status_code == 400
    assert client.post("/api/invite-requests", json={"email": "a@b.co", "consent": True, "ip": "x"}).status_code == 400
    assert server.STORE.all() == []


def test_invite_requests_are_rate_limited_and_same_origin(client):
    for _ in range(5):
        assert ask(client).status_code == 202
    assert ask(client).status_code == 429
    r = client.post("/api/invite-requests", json={"email": "a@b.co", "consent": True},
                    headers={"Origin": "https://evil.example", "X-Forwarded-For": "8.8.4.4"})
    assert r.status_code == 403


def test_invite_requests_closed_without_a_database(client, monkeypatch):
    from demo.store import Store

    monkeypatch.setattr(server, "STORE", Store(None))
    r = ask(client)
    assert r.status_code == 503 and "closed" in r.json()["error"]
    assert client.get("/api/meta").json()["invite_requests"] is False


def test_signup_export_needs_the_admin_signature(client):
    ask(client)
    assert client.get("/admin/invite-requests").status_code == 401
    assert signed(client, "GET", "/admin/invite-requests", secret=WORKER).status_code == 401
    r = signed(client, "GET", "/admin/invite-requests", secret=ADMIN)
    assert r.status_code == 200 and "seller@example.com" in r.text


def test_gallery_has_one_example_per_verdict_and_no_measurement_page(client):
    cur = client.get("/api/gallery?set=curated").json()
    assert len(cur) == 4 and all(i["set"] == "curated" for i in cur)
    assert {(i.get("final") or i["item"])["verdict"] for i in cur} == {"CONFIRMED", "POSSIBLE MATCH", "NO MATCH FOUND"}
    assert [i["id"] for i in cur if i.get("lane") == "buying"] == ["curated-buying"]
    assert not any("photo_source" in i or "truth" in i for i in cur)
    assert client.get("/api/gallery?set=sample").status_code == 400
    assert client.get("/how-we-measured").status_code == 404


# --- sharing ---------------------------------------------------------------------------------------------------

def test_recall_link_page_and_api(client):
    d = client.get("/api/recall/101").json()
    assert d["recall_id"] == 101 and d["heading"] and d["hazard_tag"]
    assert client.get("/api/recall/999").status_code == 404
    assert client.get("/api/recall/abc").status_code == 400
    r = client.get("/recall/101")
    assert r.status_code == 200 and "Recalled: Acme Strollers" in r.text
    assert 'property="og:image" content="https://cleartolist.onrender.com/static/og.png"' in r.text
    assert "/recall/101" in r.text
    for bad in ("/recall/999", "/recall/abc", "/recall/-1", "/recall/1.5", "/recall/99999999999"):
        r = client.get(bad)
        assert r.status_code == 404 and r.headers["content-type"].startswith("text/html") and "Page not found" in r.text


def test_recall_page_escapes_recall_text(client, db):
    import sqlite3

    c = sqlite3.connect(db)
    c.execute("INSERT INTO recalls VALUES (102,'x','2026-01-01','<script>alert(1)</script> Recalls Toys Due to Fire',"
              "'d','https://www.cpsc.gov/x','[]','[]','[]','toy','x','[]')")
    c.commit()
    r = client.get("/recall/102")
    assert "<script>alert(1)</script>" not in r.text and "&lt;script&gt;" in r.text


# --- anonymous usage counts -----------------------------------------------------------------------------------------

def totals():
    server.COUNTS.flush()
    out = {}
    for _, m, n in server.STORE.counts("2000-01-01"):
        out[m] = out.get(m, 0) + n
    return out


def basic(password, user="admin"):
    import base64

    return {"Authorization": "Basic " + base64.b64encode(f"{user}:{password}".encode()).decode()}


def test_page_views_and_ref_allowlist(client):
    client.get("/")
    client.get("/?ref=reddit")
    client.get("/?ref=DEV")
    client.get("/?ref=https://evil.example/track?id=123")
    client.get("/", headers={"Referer": "https://news.example/some/article"})
    t = totals()
    assert t["page_view"] == 5 and t["ref_direct"] == 2 and t["ref_reddit"] == 1 and t["ref_dev"] == 1
    assert t["ref_other"] == 1
    assert all(m in server.analytics.METRICS for m in t)


def test_scan_lookup_signup_and_verdict_counts(client):
    client.get("/api/lookup?q=stroller")
    client.post("/api/samples/check")
    job = submit(client, ip="5.5.5.6").json()["job"]
    signed(client, "POST", "/worker/claim")
    signed(client, "POST", "/worker/claim")
    signed(client, "POST", f"/worker/jobs/{job}/result", json.dumps({"verdict": "NO MATCH FOUND"}).encode())
    ask(client, alerts=True, categories=["toy"])
    t = totals()
    assert t["lookup"] == 1 and t["sample_scan"] == 1 and t["real_scan"] == 1
    assert t["verdict_no_match"] == 1 and t["invite_request"] == 1 and t["alert_signup"] == 1


def test_events_accept_only_known_values(client):
    ok = {"Content-Type": "application/json"}
    assert client.post("/api/events", json={"event": "copy_link"}, headers=ok).status_code == 204
    assert client.post("/api/events", json={"event": "ask_seller"}).status_code == 204
    body = {"event": "helpful", "answer": "yes", "tier": "POSSIBLE MATCH"}
    assert client.post("/api/events", json=body).status_code == 204
    assert client.post("/api/events", json={"event": "helpful", "answer": "yes"}).status_code == 400
    assert client.post("/api/events", json={"event": "visit", "url": "https://x"}).status_code == 400
    evil = {"Origin": "https://evil.example"}
    assert client.post("/api/events", json={"event": "copy_link"}, headers=evil).status_code == 403
    t = totals()
    assert t == {"copy_link": 1, "ask_seller": 1, "helpful_yes_possible": 1}


def test_stats_page_needs_the_admin_secret_and_is_rate_limited(client):
    r = client.get("/admin/stats")
    assert r.status_code == 401 and "Basic" in r.headers["www-authenticate"]
    assert client.get("/admin/stats", headers=basic("wrong-password-" * 3)).status_code == 401
    assert client.get("/admin/stats", headers=basic(WORKER)).status_code == 401
    client.get("/?ref=facebook")
    r = client.get("/admin/stats", headers=basic(ADMIN))
    assert r.status_code == 200 and "Funnel" in r.text and "ref_facebook" in r.text
    for _ in range(10):
        client.get("/admin/stats", headers=basic("nope"))
    assert client.get("/admin/stats", headers=basic(ADMIN)).status_code == 429


def test_no_ip_address_or_user_agent_is_stored(client):
    h = {"X-Forwarded-For": "203.0.113.77", "User-Agent": "SpecialAgent/9.9 (FingerprintMe)",
         "Referer": "https://tracker.example/x"}
    client.get("/?ref=reddit", headers=h)
    client.get("/api/lookup?q=crib", headers=h)
    client.post("/api/events", json={"event": "copy_link"}, headers=h)
    ask(client, ip="203.0.113.77")
    server.COUNTS.flush()
    dump = repr(server.STORE.counts("2000-01-01")) + repr(server.STORE.all())
    for leak in ("203.0.113.77", "SpecialAgent", "FingerprintMe", "tracker.example"):
        assert leak not in dump


def test_site_title_and_preview_match_the_headline(client):
    r = client.get("/")
    assert "<title>ClearToList: Is it recalled? Check before you list or buy.</title>" in r.text
    assert 'property="og:title" content="ClearToList: Is it recalled? Check before you list or buy."' in r.text
    shared = client.get("/recall/101").text
    head = shared.split("og:image")[0]
    assert "<title>Recalled: Acme Strollers</title>" in shared and "Is it recalled? Check before" not in head


def test_privacy_and_terms_pages(client):
    for path, words in (("/privacy", "We delete them"), ("/terms", "No guarantee")):
        r = client.get(path)
        assert r.status_code == 200 and words in r.text and "cleartolist@gmail.com" in r.text
    home = client.get("/").text
    assert 'href="/privacy"' in home and 'href="/terms"' in home and "mailto:cleartolist@gmail.com" in home
    assert "Built by V" not in home and "Famartin" in client.get("/terms").text


def test_pages_scripts_and_styles_are_revalidated(client):
    for path in ("/", "/static/app.js", "/static/app.css", "/privacy"):
        assert client.get(path).headers["cache-control"] == "no-cache"


def test_no_read_only_endpoint_reveals_whether_the_worker_is_up(client):
    def snapshot():
        out = {}
        for path in ("/api/meta", "/healthz", "/api/gallery", "/api/lookup?q=stroller", "/api/recall/101", "/"):
            r = client.get(path, headers={"X-Forwarded-For": "7.7.7.8"})
            out[path] = (r.status_code, r.text)
        return out
    server.STATE["worker_seen"] = time.time()
    up = snapshot()
    server.STATE["worker_seen"] = 0.0
    down = snapshot()
    assert up == down
    assert "scanning" not in client.get("/api/meta").json()


def test_unavailable_and_kill_switch_give_the_same_answer(client, monkeypatch):
    server.STATE["worker_seen"] = 0.0
    a = client.post("/api/samples/check", headers={"X-Forwarded-For": "1.0.0.1"})
    monkeypatch.setenv("CTL_UPLOADS_ENABLED", "0")
    server.STATE["worker_seen"] = time.time()
    b = client.post("/api/samples/check", headers={"X-Forwarded-For": "1.0.0.2"})
    assert (a.status_code, a.json()) == (b.status_code, b.json()) == (
        503, {"error": "Scanning is temporarily unavailable. Model number lookup still works."})


def test_favicons_are_served_and_named_routes_still_work(client):
    assert client.get("/favicon.ico").headers["content-type"] == "image/x-icon"
    assert client.get("/favicon.svg").headers["content-type"].startswith("image/svg+xml")
    for icon in ("/apple-touch-icon.png", "/icon-192.png", "/icon-512.png"):
        assert client.get(icon).content.startswith(b"\x89PNG")
    for page in ("/", "/privacy", "/terms", "/no-such-page"):
        html = client.get(page).text
        assert 'src="/static/brand/mark.svg"' in html and 'href="/icon-192.png"' in html
    assert client.get("/privacy").status_code == 200 and client.get("/healthz").status_code == 200
    assert 'rel="apple-touch-icon"' in client.get("/").text


def test_html_404_for_pages_and_json_404_for_the_api(client):
    r = client.get("/no-such-page")
    assert r.status_code == 404 and r.headers["content-type"].startswith("text/html") and "Page not found" in r.text
    r = client.get("/some/deeper/path")
    assert r.status_code == 404 and "Page not found" in r.text
    r = client.get("/api/recall/999")
    assert r.status_code == 404 and r.json() == {"error": "No recall with that number."}
    r = client.get("/api/nope")
    assert r.status_code == 404 and r.headers["content-type"].startswith("application/json")


def test_robots_and_sitemap(client):
    r = client.get("/robots.txt")
    assert r.status_code == 200 and "Allow: /" in r.text
    assert "Sitemap: https://cleartolist.onrender.com/sitemap.xml" in r.text
    r = client.get("/sitemap.xml")
    assert r.headers["content-type"].startswith("application/xml")
    assert all(f"<loc>https://cleartolist.onrender.com{p}</loc>" in r.text for p in ("/", "/privacy", "/terms"))


def test_public_daily_quota_defaults_to_200(monkeypatch):
    monkeypatch.delenv("CTL_PUBLIC_DAILY_SCANS", raising=False)
    assert server.DailyQuota().limit() == 200


def test_heic_photos_are_converted_to_clean_jpeg():
    import pillow_heif

    pillow_heif.register_heif_opener()
    b = io.BytesIO()
    exif = Image.Exif()
    exif[0x010F] = "PhoneMaker"
    Image.new("RGB", (120, 90), (10, 120, 200)).save(b, format="HEIF", exif=exif.tobytes())
    data = b.getvalue()
    assert data[4:12] == b"ftypheic" and security.sniff(data) == "HEIF"
    out = security.sanitize_image(data)
    assert out.startswith(b"\xff\xd8\xff") and b"PhoneMaker" not in out


def test_heic_upload_round_trip(client):
    import pillow_heif

    pillow_heif.register_heif_opener()
    b = io.BytesIO()
    Image.new("RGB", (120, 90), (10, 120, 200)).save(b, format="HEIF")
    r = client.post("/api/jobs", files={"photo": ("IMG_0001.HEIC", b.getvalue(), "image/heic")},
                    headers={"X-Forwarded-For": "4.3.2.1"})
    assert r.status_code == 202
    d = signed(client, "POST", "/worker/claim").json()
    assert signed(client, "GET", f"/worker/jobs/{d['job']}/photo/item").content.startswith(b"\xff\xd8\xff")


def test_model_number_lookup_never_touches_the_scanner_queue(client, monkeypatch):
    monkeypatch.setattr(jobs, "MAX_QUEUE", 0)  # queue full
    monkeypatch.setenv("CTL_UPLOADS_ENABLED", "0")  # uploads off
    monkeypatch.setenv("CTL_PUBLIC_DAILY_SCANS", "0")  # no free checks left
    server.STATE["worker_seen"] = 0.0  # no worker
    before = dict(server.QUEUE.counts), len(server.QUEUE.jobs)
    r = client.get("/api/lookup?q=AB1234")
    assert r.status_code == 200 and r.json()["results"][0]["recall_id"] == 101
    assert (dict(server.QUEUE.counts), len(server.QUEUE.jobs)) == before


def test_busy_and_quota_messages_are_recognisable(client, monkeypatch):
    monkeypatch.setattr(jobs, "MAX_QUEUE", 0)
    r = submit(client, ip="2.4.6.8")
    assert r.status_code == 503 and "busy" in r.json()["error"]
    monkeypatch.setattr(jobs, "MAX_QUEUE", 20)
    monkeypatch.setenv("CTL_PUBLIC_DAILY_SCANS", "0")
    r = submit(client, invite="", ip="2.4.6.9")
    assert r.status_code == 429 and "free checks are used up" in r.json()["error"]


def test_admin_invites_page_lists_requests_behind_the_admin_secret(client):
    ask(client, email="first@example.com")
    ask(client, email="second@example.com", ip="1.2.3.5", alerts=True, categories=["stroller"])
    assert client.get("/admin/invites").status_code == 401
    assert client.get("/admin/invites", headers=basic(WORKER)).status_code == 401
    r = client.get("/admin/invites", headers=basic(ADMIN))
    assert r.status_code == 200 and "2 total" in r.text and "Strollers" in r.text
    assert r.text.index("second@example.com") < r.text.index("first@example.com")


def test_hyphenated_brands_search_as_one_phrase():
    assert server.fts_query("fisher-price") == '"fisher price"'
    assert server.fts_query("Graco  high chair") == '"graco" "high" "chair"'
    assert server.fts_query("--") == ""
