import importlib
import io
import os
import re
import tempfile
from pathlib import Path

import pytest


@pytest.fixture()
def app_module(monkeypatch):
    tmpdir = tempfile.TemporaryDirectory()
    db_path = Path(tmpdir.name) / "test.db"

    monkeypatch.setenv("DB_PATH", str(db_path))
    monkeypatch.setenv("SECRET_KEY", "test-secret")

    import app as app_module_ref

    importlib.reload(app_module_ref)
    app_module_ref.app.config.update(TESTING=True)
    app_module_ref.init_db()

    yield app_module_ref
    tmpdir.cleanup()


@pytest.fixture()
def client(app_module):
    return app_module.app.test_client()


def extract_csrf(html: str) -> str:
    match = re.search(r'name="csrf_token" value="([^"]+)"', html)
    assert match is not None, "CSRF token not found in HTML"
    return match.group(1)


def login_as_admin(client):
    login_page = client.get("/login")
    token = extract_csrf(login_page.get_data(as_text=True))
    response = client.post(
        "/login",
        data={"identity": "admin", "password": "admin", "csrf_token": token},
        follow_redirects=True,
    )
    assert response.status_code == 200


def test_health_endpoint(client):
    response = client.get("/health")
    assert response.status_code == 200
    payload = response.get_json()
    assert payload["status"] in {"ok", "degraded"}
    assert "model_present" in payload


def test_login_logout_flow(client):
    login_as_admin(client)

    home = client.get("/app.html")
    assert home.status_code == 200

    page = client.get("/dashboard.html")
    token = extract_csrf(page.get_data(as_text=True))
    out = client.post("/logout", data={"csrf_token": token}, follow_redirects=True)
    assert out.status_code == 200

    after = client.get("/app.html", follow_redirects=False)
    assert after.status_code == 302
    assert "/login" in after.headers["Location"]


def test_detect_upload_persists_scan(client, app_module, monkeypatch):
    login_as_admin(client)

    monkeypatch.setattr(app_module, "_run_deepfake_detection", lambda _: ("FAKE", 88.5))

    detect_page = client.get("/Detect")
    token = extract_csrf(detect_page.get_data(as_text=True))

    data = {
        "csrf_token": token,
        "notes": "test note",
        "video": (io.BytesIO(b"fake-video"), "sample.mp4"),
    }
    response = client.post("/Detect", data=data, content_type="multipart/form-data", follow_redirects=True)
    assert response.status_code == 200
    assert "Detection Result" in response.get_data(as_text=True)

    with app_module.app.app_context():
        row = app_module.get_db().execute("SELECT output, confidence FROM scans ORDER BY id DESC LIMIT 1").fetchone()
        assert row is not None
        assert row["output"] == "FAKE"


def test_detect_rejects_invalid_extension(client):
    login_as_admin(client)

    detect_page = client.get("/Detect")
    token = extract_csrf(detect_page.get_data(as_text=True))

    data = {
        "csrf_token": token,
        "video": (io.BytesIO(b"x"), "bad.txt"),
    }
    response = client.post("/Detect", data=data, content_type="multipart/form-data")
    assert response.status_code == 200
    assert "Unsupported file type" in response.get_data(as_text=True)


def test_csrf_required(client):
    response = client.post("/login", data={"identity": "admin", "password": "admin"})
    assert response.status_code == 400