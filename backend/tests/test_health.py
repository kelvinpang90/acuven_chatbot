"""/health reports the commit the image was built from (CHAT-001).

The deploy workflow polls it until the commit it just deployed is the one
answering, so both the baked-in value and the fallback are pinned here.
"""
from unittest.mock import patch

from fastapi.testclient import TestClient

from app.config import settings
from app.main import app


def test_health_reports_the_built_commit():
    sha = "0123456789abcdef0123456789abcdef01234567"
    with patch.object(settings, "git_sha", sha):
        response = TestClient(app).get("/health")
    assert response.status_code == 200
    assert response.json() == {"status": "ok", "commit": sha}


def test_health_says_unknown_when_no_commit_was_baked_in():
    with patch.object(settings, "git_sha", ""):
        assert TestClient(app).get("/health").json()["commit"] == "unknown"


def test_the_default_is_unknown():
    assert type(settings).model_fields["git_sha"].default == "unknown"
