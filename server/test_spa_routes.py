"""React SPA serving and compatibility route tests."""

from fastapi.testclient import TestClient

from server import main


def test_frontend_routes_serve_single_spa_entry(tmp_path, monkeypatch):
    marker = '<div id="root"></div>'
    (tmp_path / "index.html").write_text(marker, encoding="utf-8")
    monkeypatch.setattr(main, "_WEB_DIST", tmp_path)
    client = TestClient(main.app)

    for path in ("/", "/login", "/dashboard", "/users", "/workplan", "/notifications", "/announcements"):
        response = client.get(path)
        assert response.status_code == 200
        assert marker in response.text
        assert response.headers["cache-control"].startswith("no-store")


def test_spa_fallback_does_not_hide_api_404s(tmp_path, monkeypatch):
    (tmp_path / "index.html").write_text("spa", encoding="utf-8")
    monkeypatch.setattr(main, "_WEB_DIST", tmp_path)
    client = TestClient(main.app)

    assert client.get("/future/react/route").text == "spa"
    response = client.get("/api/not-a-real-endpoint")
    assert response.status_code == 404
    assert response.headers["content-type"].startswith("application/json")
