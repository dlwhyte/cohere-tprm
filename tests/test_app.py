"""Tests for the FastAPI web server."""

from unittest.mock import patch

from fastapi.testclient import TestClient

from app import app

client = TestClient(app)


def test_static_index():
    resp = client.get("/")
    assert resp.status_code == 200
    assert "TPRM Agent" in resp.text


@patch("app.run_agent")
def test_brief_success(mock_agent):
    mock_agent.return_value = "## Entity summary\nTest entity."

    resp = client.post("/api/brief", json={"query": "Test Corp"})

    assert resp.status_code == 200
    data = resp.json()
    assert data["query"] == "Test Corp"
    assert "Test entity" in data["brief"]
    assert data["error"] is None


@patch("app.run_agent")
def test_brief_agent_returns_none(mock_agent):
    mock_agent.return_value = None

    resp = client.post("/api/brief", json={"query": "Test Corp"})

    assert resp.status_code == 200
    data = resp.json()
    assert data["brief"] is None
    assert "step limit" in data["error"]


@patch("app.run_agent")
def test_brief_agent_exception(mock_agent):
    mock_agent.side_effect = Exception("API key invalid")

    resp = client.post("/api/brief", json={"query": "Test Corp"})

    assert resp.status_code == 200
    data = resp.json()
    assert data["brief"] is None
    assert "API key invalid" in data["error"]


def test_brief_missing_query():
    resp = client.post("/api/brief", json={})
    assert resp.status_code == 422
