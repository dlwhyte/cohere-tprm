"""Tests for TPRM agent tools."""

from unittest.mock import patch, MagicMock

from tools import sanctions_lookup, web_search, TOOL_REGISTRY, TOOL_SCHEMAS


# ---------- Registry / schema tests ----------

def test_registry_has_all_tools():
    assert "sanctions_lookup" in TOOL_REGISTRY
    assert "web_search" in TOOL_REGISTRY


def test_schemas_match_registry():
    schema_names = {t["function"]["name"] for t in TOOL_SCHEMAS}
    assert schema_names == set(TOOL_REGISTRY.keys())


def test_all_schemas_have_required_fields():
    for schema in TOOL_SCHEMAS:
        func = schema["function"]
        assert "name" in func
        assert "description" in func
        assert "parameters" in func
        assert func["parameters"]["type"] == "object"


# ---------- sanctions_lookup tests ----------

MOCK_SANCTIONS_RESPONSE = [
    {
        "source_id": "EU.6778.46",
        "names": ["Wagner Group", "Groupe Wagner", "Grupa Wagner"],
        "target_type": "entity",
        "source": "eu",
        "listed_on": "2021-12-13",
        "remarks": None,
    },
]


@patch("tools.httpx.get")
def test_sanctions_lookup_success(mock_get):
    mock_resp = MagicMock()
    mock_resp.json.return_value = MOCK_SANCTIONS_RESPONSE
    mock_resp.raise_for_status.return_value = None
    mock_get.return_value = mock_resp

    result = sanctions_lookup("Wagner Group")

    assert result["query"] == "Wagner Group"
    assert result["match_count"] == 1
    assert result["hits"][0]["id"] == "EU.6778.46"
    assert result["hits"][0]["source"] == "eu"
    assert "Wagner Group" in result["hits"][0]["names"]


@patch("tools.httpx.get")
def test_sanctions_lookup_empty(mock_get):
    mock_resp = MagicMock()
    mock_resp.json.return_value = []
    mock_resp.raise_for_status.return_value = None
    mock_get.return_value = mock_resp

    result = sanctions_lookup("Nonexistent Corp")

    assert result["match_count"] == 0
    assert result["hits"] == []


@patch("tools.httpx.get")
def test_sanctions_lookup_api_error(mock_get):
    mock_get.side_effect = Exception("Connection refused")

    result = sanctions_lookup("Test")

    assert "error" in result
    assert "Connection refused" in result["error"]


# ---------- web_search tests ----------

MOCK_TAVILY_RESPONSE = {
    "results": [
        {
            "title": "Wagner Group - Wikipedia",
            "url": "https://en.wikipedia.org/wiki/Wagner_Group",
            "content": "Russian PMC",
        },
        {
            "title": "Wagner Group | Britannica",
            "url": "https://www.britannica.com/topic/Wagner-Group",
            "content": "Russian mercenary group",
        },
    ],
}


@patch.dict("os.environ", {"TAVILY_API_KEY": "test-key"})
@patch("tools.httpx.post")
def test_web_search_success(mock_post):
    mock_resp = MagicMock()
    mock_resp.json.return_value = MOCK_TAVILY_RESPONSE
    mock_resp.raise_for_status.return_value = None
    mock_post.return_value = mock_resp

    result = web_search("Wagner Group")

    assert result["query"] == "Wagner Group"
    assert result["result_count"] == 2
    assert result["results"][0]["title"] == "Wagner Group - Wikipedia"

    # Verify the API was called correctly
    call_kwargs = mock_post.call_args
    assert call_kwargs[1]["json"]["query"] == "Wagner Group"
    assert call_kwargs[1]["json"]["max_results"] == 5


@patch.dict("os.environ", {}, clear=True)
def test_web_search_missing_api_key():
    result = web_search("test")

    assert "error" in result
    assert "TAVILY_API_KEY" in result["error"]


@patch.dict("os.environ", {"TAVILY_API_KEY": "test-key"})
@patch("tools.httpx.post")
def test_web_search_api_error(mock_post):
    mock_post.side_effect = Exception("timeout")

    result = web_search("test")

    assert "error" in result
    assert "timeout" in result["error"]
