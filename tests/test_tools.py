"""Tests for TPRM agent tools."""

import json
from unittest.mock import patch, MagicMock

from tools import (
    sanctions_lookup, web_search, entity_lookup, trust_center_search,
    adverse_media, sec_filing_search, sec_enforcement_search, cve_lookup,
    sbom_analysis, _parse_sbom,
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


# ---------- entity_lookup tests ----------

MOCK_WIKI_RESPONSE = {
    "title": "Shopify",
    "description": "Canadian e-commerce company",
    "extract": "Shopify Inc. is a Canadian multinational e-commerce company headquartered in Ottawa, Ontario.",
}

MOCK_ENTITY_TAVILY_RESPONSE = {
    "results": [
        {
            "title": "Shopify Inc - Company Profile - GlobalData",
            "url": "https://example.com/shopify-profile",
            "content": "Founded in 2006 by Tobias Lutke. CEO: Tobias Lutke.",
        },
    ],
}


@patch.dict("os.environ", {"TAVILY_API_KEY": "test-key"})
@patch("tools.httpx.post")
@patch("tools.httpx.get")
def test_entity_lookup_success(mock_get, mock_post):
    # Wikipedia response
    wiki_resp = MagicMock()
    wiki_resp.status_code = 200
    wiki_resp.json.return_value = MOCK_WIKI_RESPONSE
    mock_get.return_value = wiki_resp

    # Tavily response
    tavily_resp = MagicMock()
    tavily_resp.json.return_value = MOCK_ENTITY_TAVILY_RESPONSE
    tavily_resp.raise_for_status.return_value = None
    mock_post.return_value = tavily_resp

    result = entity_lookup("Shopify")

    assert result["query"] == "Shopify"
    assert result["wikipedia"]["title"] == "Shopify"
    assert "Canadian" in result["wikipedia"]["summary"]
    assert len(result["corporate_details"]) == 1
    assert "Tobias Lutke" in result["corporate_details"][0]["content"]


@patch.dict("os.environ", {"TAVILY_API_KEY": "test-key"})
@patch("tools.httpx.post")
@patch("tools.httpx.get")
def test_entity_lookup_no_wikipedia(mock_get, mock_post):
    wiki_resp = MagicMock()
    wiki_resp.status_code = 404
    mock_get.return_value = wiki_resp

    tavily_resp = MagicMock()
    tavily_resp.json.return_value = {"results": []}
    tavily_resp.raise_for_status.return_value = None
    mock_post.return_value = tavily_resp

    result = entity_lookup("Unknown Corp XYZ")

    assert result["wikipedia"]["note"] == "No Wikipedia article found"


@patch.dict("os.environ", {}, clear=True)
@patch("tools.httpx.get")
def test_entity_lookup_no_tavily_key(mock_get):
    wiki_resp = MagicMock()
    wiki_resp.status_code = 200
    wiki_resp.json.return_value = MOCK_WIKI_RESPONSE
    mock_get.return_value = wiki_resp

    result = entity_lookup("Shopify")

    assert "Canadian" in result["wikipedia"]["summary"]
    assert result["corporate_details"][0]["note"] == "TAVILY_API_KEY not set"


# ---------- trust_center_search tests ----------

MOCK_TRUST_CENTER_RESPONSE = {
    "results": [
        {
            "title": "Acme Corp Trust Center",
            "url": "https://trust.acme.com",
            "content": "SOC 2 Type II certified. ISO 27001:2022 certified.",
        },
        {
            "title": "Acme Corp Security | G2",
            "url": "https://www.g2.com/products/acme/security",
            "content": "Acme Corp has completed SOC 2 and maintains PCI DSS compliance.",
        },
    ],
}


@patch.dict("os.environ", {"TAVILY_API_KEY": "test-key"})
@patch("tools.httpx.post")
def test_trust_center_search_success(mock_post):
    mock_resp = MagicMock()
    mock_resp.json.return_value = MOCK_TRUST_CENTER_RESPONSE
    mock_resp.raise_for_status.return_value = None
    mock_post.return_value = mock_resp

    result = trust_center_search("Acme Corp")

    assert result["query"] == "Acme Corp"
    assert result["result_count"] == 2
    assert "SOC 2" in result["results"][0]["content"]


@patch.dict("os.environ", {}, clear=True)
def test_trust_center_search_missing_api_key():
    result = trust_center_search("test")

    assert "error" in result
    assert "TAVILY_API_KEY" in result["error"]


@patch.dict("os.environ", {"TAVILY_API_KEY": "test-key"})
@patch("tools.httpx.post")
def test_trust_center_search_api_error(mock_post):
    mock_post.side_effect = Exception("timeout")

    result = trust_center_search("test")

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


# ---------- cve_lookup tests ----------

MOCK_NVD_RESPONSE = {
    "totalResults": 2,
    "vulnerabilities": [
        {
            "cve": {
                "id": "CVE-2024-1234",
                "published": "2024-03-15T00:00:00.000",
                "descriptions": [{"lang": "en", "value": "Critical RCE in Acme product"}],
                "metrics": {
                    "cvssMetricV31": [{
                        "cvssData": {"baseScore": 9.8, "baseSeverity": "CRITICAL"},
                    }],
                },
            },
        },
        {
            "cve": {
                "id": "CVE-2024-5678",
                "published": "2024-02-10T00:00:00.000",
                "descriptions": [{"lang": "en", "value": "XSS in Acme widget"}],
                "metrics": {
                    "cvssMetricV31": [{
                        "cvssData": {"baseScore": 4.3, "baseSeverity": "MEDIUM"},
                    }],
                },
            },
        },
    ],
}


@patch("tools._load_kev", return_value={"CVE-2024-1234"})
@patch("tools.httpx.get")
def test_cve_lookup_success(mock_get, mock_kev):
    mock_resp = MagicMock()
    mock_resp.json.return_value = MOCK_NVD_RESPONSE
    mock_resp.raise_for_status.return_value = None
    mock_get.return_value = mock_resp

    result = cve_lookup("Acme")

    assert result["query"] == "Acme"
    assert result["total_cves"] == 2
    assert result["kev_matches"] == 1
    assert result["cves"][0]["id"] == "CVE-2024-1234"
    assert result["cves"][0]["severity"] == "CRITICAL"
    assert result["cves"][0]["in_kev"] is True
    assert result["cves"][1]["in_kev"] is False


@patch("tools._load_kev", return_value=set())
@patch("tools.httpx.get")
def test_cve_lookup_empty(mock_get, mock_kev):
    mock_resp = MagicMock()
    mock_resp.json.return_value = {"totalResults": 0, "vulnerabilities": []}
    mock_resp.raise_for_status.return_value = None
    mock_get.return_value = mock_resp

    result = cve_lookup("Nonexistent Product")

    assert result["total_cves"] == 0
    assert result["kev_matches"] == 0
    assert result["cves"] == []


@patch("tools.httpx.get")
def test_cve_lookup_api_error(mock_get):
    mock_get.side_effect = Exception("NVD unavailable")

    result = cve_lookup("Test")

    assert "error" in result
    assert "NVD unavailable" in result["error"]


# ---------- sbom_analysis tests ----------

MOCK_CYCLONEDX_SBOM = {
    "bomFormat": "CycloneDX",
    "specVersion": "1.5",
    "components": [
        {"type": "library", "name": "lodash", "version": "4.17.21", "purl": "pkg:npm/lodash@4.17.21"},
        {"type": "library", "name": "openssl", "version": "3.0.8", "purl": "pkg:generic/openssl@3.0.8"},
    ],
}

MOCK_SPDX_SBOM = {
    "spdxVersion": "SPDX-2.3",
    "packages": [
        {
            "name": "requests",
            "versionInfo": "2.28.0",
            "externalRefs": [{"referenceType": "purl", "referenceLocator": "pkg:pypi/requests@2.28.0"}],
        },
        {"name": "NOASSERTION", "versionInfo": ""},
    ],
}


def test_parse_sbom_cyclonedx():
    components = _parse_sbom(MOCK_CYCLONEDX_SBOM)
    assert len(components) == 2
    assert components[0]["name"] == "lodash"
    assert components[0]["version"] == "4.17.21"


def test_parse_sbom_spdx():
    components = _parse_sbom(MOCK_SPDX_SBOM)
    assert len(components) == 1  # NOASSERTION filtered out
    assert components[0]["name"] == "requests"


def test_sbom_analysis_invalid_json():
    result = sbom_analysis("not valid json {{{")
    assert "error" in result
    assert "Invalid SBOM JSON" in result["error"]


def test_sbom_analysis_no_components():
    result = sbom_analysis('{"bomFormat": "CycloneDX", "components": []}')
    assert "error" in result
    assert "No components found" in result["error"]


@patch("tools._load_kev", return_value={"CVE-2023-0286"})
@patch("tools.httpx.get")
def test_sbom_analysis_success(mock_get, mock_kev):
    mock_resp = MagicMock()
    mock_resp.json.return_value = {
        "totalResults": 1,
        "vulnerabilities": [{
            "cve": {
                "id": "CVE-2023-0286",
                "descriptions": [{"lang": "en", "value": "OpenSSL vuln"}],
                "metrics": {"cvssMetricV31": [{"cvssData": {"baseScore": 9.8, "baseSeverity": "CRITICAL"}}]},
            },
        }],
    }
    mock_resp.raise_for_status.return_value = None
    mock_get.return_value = mock_resp

    result = sbom_analysis(json.dumps(MOCK_CYCLONEDX_SBOM))

    assert result["total_components"] == 2
    assert result["vulnerable_components"] >= 1
    assert result["kev_matches"] >= 1
