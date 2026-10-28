import pytest
from fastapi.testclient import TestClient

from app.main import create_app


@pytest.mark.parametrize(
    "origin",
    ["http://localhost:5173", "http://127.0.0.1:5173"],
)
def test_cors_allows_local_frontend_origins(origin, tmp_path, monkeypatch):
    monkeypatch.delenv("CORS_ORIGINS", raising=False)
    app = create_app("sqlite:///{}".format(tmp_path / "cors.db"), seed_data=False)

    with TestClient(app) as client:
        response = client.options(
            "/health",
            headers={
                "Origin": origin,
                "Access-Control-Request-Method": "GET",
            },
        )

    assert response.status_code == 200
    assert response.headers["access-control-allow-origin"] == origin


def test_cors_parses_configured_origins_and_ignores_empty_entries(tmp_path, monkeypatch):
    origin = "https://flyo-demo.example"
    monkeypatch.setenv("CORS_ORIGINS", " ,  {}, , ".format(origin))
    app = create_app("sqlite:///{}".format(tmp_path / "cors-configured.db"), seed_data=False)

    with TestClient(app) as client:
        response = client.options(
            "/health",
            headers={
                "Origin": origin,
                "Access-Control-Request-Method": "GET",
            },
        )

    assert response.status_code == 200
    assert response.headers["access-control-allow-origin"] == origin


def test_cors_rejects_wildcard_origin(tmp_path, monkeypatch):
    monkeypatch.setenv("CORS_ORIGINS", "https://flyo-demo.example, *")

    with pytest.raises(ValueError, match="must not contain"):
        create_app("sqlite:///{}".format(tmp_path / "cors-wildcard.db"), seed_data=False)
