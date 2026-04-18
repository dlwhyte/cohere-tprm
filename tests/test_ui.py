"""Playwright UI tests for the TPRM agent frontend."""

import threading
import time
from unittest.mock import patch

import pytest
import uvicorn
from playwright.sync_api import sync_playwright


MOCK_EVENTS = [
    {"type": "entity", "entity": "Test Corp"},
    {"type": "tool", "tool": "sanctions_lookup", "label": "Checking sanctions lists"},
    {"type": "tool", "tool": "web_search", "label": "Searching the web"},
    {"type": "generating"},
    {"type": "brief", "brief": "## Entity summary\nTest Corp is a fictional company.\n\n## Risk assessment\n**LOW** — No issues found."},
]


@pytest.fixture(scope="module")
def server():
    """Start the FastAPI server in a background thread with mocked agent."""
    with patch("app.run_agent_streaming", return_value=iter(MOCK_EVENTS)):
        from app import app

        config = uvicorn.Config(app, host="127.0.0.1", port=8765, log_level="error")
        srv = uvicorn.Server(config)
        thread = threading.Thread(target=srv.run, daemon=True)
        thread.start()

        # Wait for server to be ready
        for _ in range(20):
            try:
                import httpx
                httpx.get("http://127.0.0.1:8765/", timeout=1)
                break
            except Exception:
                time.sleep(0.25)

        yield srv
        srv.should_exit = True


def test_page_loads(server):
    with sync_playwright() as p:
        browser = p.chromium.launch()
        page = browser.new_page()
        page.goto("http://127.0.0.1:8765")

        assert page.title() == "TPRM Agent"
        assert page.locator("input#query").is_visible()
        assert page.locator("button#btn").is_visible()
        assert page.locator(".sidebar").is_visible()

        browser.close()


def test_submit_query_shows_brief(server):
    with sync_playwright() as p:
        browser = p.chromium.launch()
        page = browser.new_page()
        page.goto("http://127.0.0.1:8765")

        page.fill("input#query", "Risk brief for Test Corp")
        page.click("button#btn")

        # Wait for the brief to appear
        brief = page.locator("#brief")
        brief.wait_for(state="visible", timeout=10000)

        content = brief.inner_text()
        assert "Test Corp" in content
        assert "LOW" in content

        browser.close()


def test_empty_query_does_nothing(server):
    with sync_playwright() as p:
        browser = p.chromium.launch()
        page = browser.new_page()
        page.goto("http://127.0.0.1:8765")

        page.click("button#btn")

        # Brief should not appear
        brief = page.locator("#brief")
        assert not brief.is_visible()

        browser.close()
