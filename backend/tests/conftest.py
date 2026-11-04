import os
import sys
from pathlib import Path

import pytest

# Isolated settings: in-memory SQLite, no background replay thread (tests tick the engine).
os.environ.update({
    "DATABASE_URL": "sqlite://",
    "REPLAY_ENABLED": "false",
    "JWT_SECRET": "test-secret-at-least-32-characters-long",
    "SEED_ADMIN_PASSWORD": "admin-pass-123",
    "OPENAI_API_KEY": "",
})
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from fastapi.testclient import TestClient  # noqa: E402

from app.main import app  # noqa: E402

PASSWORD = "admin-pass-123"


@pytest.fixture(scope="session")
def client():
    with TestClient(app) as c:
        yield c


def token(client, email: str, password: str = PASSWORD) -> dict:
    r = client.post("/api/auth/login", data={"username": email, "password": password})
    assert r.status_code == 200, r.text
    return {"Authorization": f"Bearer {r.json()['access_token']}"}


@pytest.fixture(scope="session")
def admin(client):
    return token(client, "admin@cloudopt.dev")


@pytest.fixture(scope="session")
def viewer(client):
    return token(client, "viewer@cloudopt.dev")


@pytest.fixture(scope="session")
def tenant_viewer(client):
    return token(client, "ops@northvale.cloudopt.dev")
