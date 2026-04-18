"""Tests for TPRM agent tools."""

from unittest.mock import patch, MagicMock

from tools import (
    sanctions_lookup, web_search, adverse_media,
    sec_filing_search, sec_enforcement_search,
    TOOL_REGISTRY, TOOL_SCHEMAS,
)


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
    assert result["hits"][0]["name_similarity"] > 0.6


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


# ---------- adverse_media tests ----------

MOCK_GDELT_RESPONSE = {
    "articles": [
        {
            "title": "Wagner Group accused of war crimes in Mali",
            "url": "https://example.com/article1",
            "domain": "reuters.com",
            "seendate": "20240315T120000Z",
            "language": "English",
            "sourcecountry": "United Kingdom",
        },
        {
            "title": "EU expands Wagner Group sanctions",
            "url": "https://example.com/article2",
            "domain": "bbc.co.uk",
            "seendate": "20240310T080000Z",
            "language": "English",
            "sourcecountry": "United Kingdom",
        },
    ],
}


@patch("tools.httpx.get")
def test_adverse_media_success(mock_get):
    mock_resp = MagicMock()
    mock_resp.json.return_value = MOCK_GDELT_RESPONSE
    mock_resp.raise_for_status.return_value = None
    mock_get.return_value = mock_resp

    result = adverse_media("Wagner Group")

    assert result["query"] == "Wagner Group"
    assert result["article_count"] == 2
    assert result["articles"][0]["source"] == "reuters.com"
    assert result["articles"][0]["date"] == "20240315"


@patch("tools.httpx.get")
def test_adverse_media_empty(mock_get):
    mock_resp = MagicMock()
    mock_resp.json.return_value = {"articles": []}
    mock_resp.raise_for_status.return_value = None
    mock_get.return_value = mock_resp

    result = adverse_media("Nonexistent Corp")

    assert result["article_count"] == 0
    assert result["articles"] == []


@patch("tools.httpx.get")
def test_adverse_media_api_error(mock_get):
    mock_get.side_effect = Exception("GDELT unavailable")

    result = adverse_media("Test")

    assert "error" in result
    assert "GDELT unavailable" in result["error"]


# ---------- sec_filing_search tests ----------

MOCK_EDGAR_FILING_RESPONSE = {
    "hits": {
        "total": {"value": 2, "relation": "eq"},
        "hits": [
            {
                "_source": {
                    "display_names": ["ACME CORP  (ACME)  (CIK 0001234567)"],
                    "root_forms": ["10-K"],
                    "file_date": "2024-03-15",
                    "period_ending": "2023-12-31",
                    "file_description": "Annual Report",
                },
            },
            {
                "_source": {
                    "display_names": ["ACME CORP  (ACME)  (CIK 0001234567)"],
                    "root_forms": ["8-K"],
                    "file_date": "2024-01-10",
                    "period_ending": None,
                    "file_description": "Current Report",
                },
            },
        ],
    },
}


@patch("tools.httpx.get")
def test_sec_filing_search_success(mock_get):
    mock_resp = MagicMock()
    mock_resp.json.return_value = MOCK_EDGAR_FILING_RESPONSE
    mock_resp.raise_for_status.return_value = None
    mock_get.return_value = mock_resp

    result = sec_filing_search("Acme Corp")

    assert result["query"] == "Acme Corp"
    assert result["total_filings"] == 2
    assert result["filings"][0]["form_type"] == "10-K"
    assert result["filings"][0]["file_date"] == "2024-03-15"
    assert "ACME CORP" in result["filings"][0]["entity"]


@patch("tools.httpx.get")
def test_sec_filing_search_empty(mock_get):
    mock_resp = MagicMock()
    mock_resp.json.return_value = {"hits": {"total": {"value": 0}, "hits": []}}
    mock_resp.raise_for_status.return_value = None
    mock_get.return_value = mock_resp

    result = sec_filing_search("Nonexistent Corp")

    assert result["total_filings"] == 0
    assert result["filings"] == []


@patch("tools.httpx.get")
def test_sec_filing_search_api_error(mock_get):
    mock_get.side_effect = Exception("EDGAR unavailable")

    result = sec_filing_search("Test")

    assert "error" in result
    assert "EDGAR unavailable" in result["error"]


# ---------- sec_enforcement_search tests ----------

MOCK_EDGAR_ENFORCEMENT_RESPONSE = {
    "hits": {
        "total": {"value": 1, "relation": "eq"},
        "hits": [
            {
                "_source": {
                    "display_names": ["ACME CORP  (ACME)  (CIK 0001234567)"],
                    "root_forms": ["10-Q"],
                    "file_date": "2024-05-08",
                    "file_description": "Quarterly Report",
                },
            },
        ],
    },
}


@patch("tools.httpx.get")
def test_sec_enforcement_search_success(mock_get):
    mock_resp = MagicMock()
    mock_resp.json.return_value = MOCK_EDGAR_ENFORCEMENT_RESPONSE
    mock_resp.raise_for_status.return_value = None
    mock_get.return_value = mock_resp

    result = sec_enforcement_search("Acme Corp")

    assert result["query"] == "Acme Corp"
    assert result["total_hits"] == 1
    assert result["filings"][0]["form_type"] == "10-Q"
    assert result["filings"][0]["file_date"] == "2024-05-08"


@patch("tools.httpx.get")
def test_sec_enforcement_search_empty(mock_get):
    mock_resp = MagicMock()
    mock_resp.json.return_value = {"hits": {"total": {"value": 0}, "hits": []}}
    mock_resp.raise_for_status.return_value = None
    mock_get.return_value = mock_resp

    result = sec_enforcement_search("Clean Corp")

    assert result["total_hits"] == 0
    assert result["filings"] == []


@patch("tools.httpx.get")
def test_sec_enforcement_search_api_error(mock_get):
    mock_get.side_effect = Exception("EDGAR down")

    result = sec_enforcement_search("Test")

    assert "error" in result
    assert "EDGAR down" in result["error"]
