"""Tests for the FastAPI web server."""

import json
from unittest.mock import patch

from fastapi.testclient import TestClient

from app import app

client = TestClient(app)


def test_static_index():
    resp = client.get("/")
    assert resp.status_code == 200
    assert "TPRM Agent" in resp.text


def _parse_sse(resp):
    """Parse SSE events from response text."""
    events = []
    for line in resp.text.strip().split("\n"):
        if line.startswith("data: "):
            events.append(json.loads(line[6:]))
    return events


@patch("app.run_agent_streaming")
def test_brief_success(mock_stream):
    mock_stream.return_value = iter([
        {"type": "entity", "entity": "Test Corp"},
        {"type": "tool", "tool": "sanctions_lookup", "label": "Checking sanctions"},
        {"type": "generating"},
        {"type": "brief", "brief": "## Entity summary\nTest entity."},
    ])

    resp = client.post("/api/brief", data={"query": "Test Corp"})

    assert resp.status_code == 200
    events = _parse_sse(resp)
    assert events[0]["type"] == "entity"
    assert events[-1]["type"] == "brief"
    assert "Test entity" in events[-1]["brief"]


@patch("app.run_agent_streaming")
def test_brief_error(mock_stream):
    mock_stream.return_value = iter([
        {"type": "entity", "entity": "Test Corp"},
        {"type": "error", "message": "API key invalid"},
    ])

    resp = client.post("/api/brief", data={"query": "Test Corp"})

    assert resp.status_code == 200
    events = _parse_sse(resp)
    assert any(e["type"] == "error" for e in events)


@patch("app.run_agent_streaming")
def test_brief_exception(mock_stream):
    mock_stream.side_effect = Exception("connection failed")

    resp = client.post("/api/brief", data={"query": "Test Corp"})

    assert resp.status_code == 200
    events = _parse_sse(resp)
    assert any(e["type"] == "error" for e in events)


def test_brief_missing_query():
    resp = client.post("/api/brief", data={})
    assert resp.status_code == 422


@patch("app.run_agent_streaming")
def test_brief_with_sbom(mock_stream):
    mock_stream.return_value = iter([
        {"type": "entity", "entity": "Test Corp"},
        {"type": "tool", "tool": "sbom_analysis", "label": "Analyzing SBOM"},
        {"type": "brief", "brief": "## SBOM analysis\n8 vulnerable components."},
    ])

    sbom_content = b'{"bomFormat": "CycloneDX", "components": []}'
    resp = client.post(
        "/api/brief",
        data={"query": "Test Corp"},
        files={"sbom": ("test.json", sbom_content, "application/json")},
    )

    assert resp.status_code == 200
    events = _parse_sse(resp)
    assert events[-1]["type"] == "brief"
    mock_stream.assert_called_once()
    call_kwargs = mock_stream.call_args
    assert call_kwargs[1]["sbom_json"] is not None
