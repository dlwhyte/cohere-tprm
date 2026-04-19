"""Playwright UI tests for the TPRM agent frontend."""

import threading
import time
from unittest.mock import patch

import pytest
import uvicorn
from playwright.sync_api import sync_playwright


MOCK_EVENTS = [
    {"type": "entity", "entity": "Test Corp"},
    {"type": "tool", "tool": "entity_lookup", "label": "Looking up entity details"},
    {"type": "tool", "tool": "sanctions_lookup", "label": "Checking sanctions lists"},
    {"type": "tool", "tool": "web_search", "label": "Searching the web"},
    {"type": "tool_done", "tool": "entity_lookup"},
    {"type": "tool_done", "tool": "sanctions_lookup"},
    {"type": "tool_done", "tool": "web_search"},
    {"type": "generating"},
    {"type": "delta", "text": "## Entity summary\n"},
    {"type": "delta", "text": "Test Corp is a fictional company.\n\n"},
    {"type": "delta", "text": "## Risk assessment\n"},
    {"type": "delta", "text": "**LOW** — No issues found."},
    {"type": "brief", "brief": "## Entity summary\nTest Corp is a fictional company.\n\n## Risk assessment\n**LOW** — No issues found."},
]


def _make_mock():
    """Return a fresh iterator of mock events each time it's called."""
    return iter(list(MOCK_EVENTS))


@pytest.fixture(scope="module")
def server():
    """Start the FastAPI server in a background thread with mocked agent."""
    with patch("app.run_agent_streaming", side_effect=lambda *a, **kw: _make_mock()):
        from app import app

        config = uvicorn.Config(app, host="127.0.0.1", port=8765, log_level="error")
        srv = uvicorn.Server(config)
        thread = threading.Thread(target=srv.run, daemon=True)
        thread.start()

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


def test_empty_state_visible(server):
    with sync_playwright() as p:
        browser = p.chromium.launch()
        page = browser.new_page()
        page.goto("http://127.0.0.1:8765")

        # Empty state should be visible
        empty = page.locator("#empty-state")
        assert empty.is_visible()
        assert "Screen any vendor" in empty.inner_text()

        # Example chips should be present
        chips = page.locator(".example-chip")
        assert chips.count() == 3

        browser.close()


def test_sbom_upload_input_exists(server):
    with sync_playwright() as p:
        browser = p.chromium.launch()
        page = browser.new_page()
        page.goto("http://127.0.0.1:8765")

        sbom_input = page.locator("input#sbom")
        assert sbom_input.count() == 1

        file_label = page.locator("#file-label")
        assert file_label.inner_text() == "SBOM"

        browser.close()


def test_submit_query_shows_brief(server):
    with sync_playwright() as p:
        browser = p.chromium.launch()
        page = browser.new_page()
        page.goto("http://127.0.0.1:8765")

        page.fill("input#query", "Risk brief for Test Corp")
        page.click("button#btn")

        brief = page.locator("#brief")
        brief.wait_for(state="visible", timeout=10000)

        content = brief.inner_text()
        assert "Test Corp" in content
        assert "LOW" in content

        browser.close()


def test_brief_has_collapsible_sections(server):
    with sync_playwright() as p:
        browser = p.chromium.launch()
        page = browser.new_page()
        page.goto("http://127.0.0.1:8765")

        page.fill("input#query", "Risk brief for Test Corp")
        page.click("button#btn")

        brief = page.locator("#brief")
        brief.wait_for(state="visible", timeout=10000)

        # Brief should contain <details> elements
        details = brief.locator("details")
        assert details.count() >= 2

        # Sections should be open by default
        first_detail = details.first
        assert first_detail.get_attribute("open") is not None or first_detail.get_attribute("open") == ""

        browser.close()


def test_export_buttons_appear_after_brief(server):
    with sync_playwright() as p:
        browser = p.chromium.launch()
        page = browser.new_page()
        page.goto("http://127.0.0.1:8765")

        # Export bar should be hidden initially
        export_bar = page.locator("#export-bar")
        assert not export_bar.is_visible()

        page.fill("input#query", "Risk brief for Test Corp")
        page.click("button#btn")

        page.locator("#brief").wait_for(state="visible", timeout=10000)

        # Export bar should now be visible
        assert export_bar.is_visible()
        assert page.locator("#copy-btn").is_visible()
        assert page.locator("#pdf-btn").is_visible()

        browser.close()


def test_empty_query_does_nothing(server):
    with sync_playwright() as p:
        browser = p.chromium.launch()
        page = browser.new_page()
        page.goto("http://127.0.0.1:8765")

        page.click("button#btn")

        brief = page.locator("#brief")
        assert not brief.is_visible()

        browser.close()


def test_history_shows_after_brief(server):
    with sync_playwright() as p:
        browser = p.chromium.launch()
        page = browser.new_page()
        page.goto("http://127.0.0.1:8765")

        # Clear localStorage
        page.evaluate("localStorage.clear()")
        page.reload()

        page.fill("input#query", "Risk brief for Test Corp")
        page.click("button#btn")

        page.locator("#brief").wait_for(state="visible", timeout=10000)

        # History should now have an entry
        history_items = page.locator(".history-list li")
        assert history_items.count() >= 1

        # Should contain the query text
        assert "Test Corp" in history_items.first.inner_text()

        browser.close()
