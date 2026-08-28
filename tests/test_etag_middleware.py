from fastapi.testclient import TestClient
from app.main import app
from app.core.config import settings


def test_etag_and_if_none_match(monkeypatch):
    # ensure no excluded prefixes for the test
    monkeypatch.setattr(settings, "ETAG_EXCLUDE_PATH_PREFIXES", [])
    client = TestClient(app)
    # Use the generated OpenAPI document which is stable during a test run
    r1 = client.get("/openapi.json")
    assert r1.status_code == 200
    assert "ETag" in r1.headers
    etag = r1.headers["ETag"]
    r2 = client.get("/openapi.json", headers={"If-None-Match": etag})
    assert r2.status_code == 304
    assert r2.headers.get("ETag") == etag
