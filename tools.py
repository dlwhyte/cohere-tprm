"""Tool implementations and registry for the TPRM agent."""

from __future__ import annotations

import json
import os
import time
from difflib import SequenceMatcher

import httpx


def _name_similarity(query: str, names: list[str]) -> float:
    """Return the best fuzzy match score (0-1) between query and a list of names."""
    query_lower = query.lower()
    best = 0.0
    for name in names:
        score = SequenceMatcher(None, query_lower, name.lower()).ratio()
        best = max(best, score)
    return round(best, 2)


def sanctions_lookup(name: str) -> dict:
    """
    Search US OFAC SDN, UN Security Council, and EU sanctions lists via
    sanctions.network. Free API, no key required. Uses fuzzy name matching.
    """
    try:
        r = httpx.get(
            "https://api.sanctions.network/rpc/search_sanctions",
            params={"name": name, "limit": 5},
            headers={"Accept": "application/json"},
            timeout=15,
        )
        r.raise_for_status()
        data = r.json()
    except Exception as e:
        err = {"error": f"sanctions_lookup failed: {e}"}
        print(f"[DEBUG sanctions_lookup] {json.dumps(err, indent=2)}")
        return err

    hits = []
    for hit in data[:5]:
        names = hit.get("names", [])
        hits.append({
            "id": hit.get("source_id"),
            "names": names,
            "name_similarity": _name_similarity(name, names),
            "type": hit.get("target_type"),
            "source": hit.get("source"),
            "listed_on": hit.get("listed_on"),
            "remarks": hit.get("remarks"),
        })

    result = {"query": name, "match_count": len(hits), "hits": hits}
    print(f"[DEBUG sanctions_lookup] {json.dumps(result, indent=2)}")
    return result


def web_search(query: str) -> dict:
    """
    Search the web via the Tavily API. Returns up to 5 results with
    titles, URLs, and content snippets. Requires TAVILY_API_KEY env var.
    """
    api_key = os.environ.get("TAVILY_API_KEY")
    if not api_key:
        return {"error": "TAVILY_API_KEY environment variable not set"}

    try:
        r = httpx.post(
            "https://api.tavily.com/search",
            json={"api_key": api_key, "query": query, "max_results": 5},
            timeout=15,
        )
        r.raise_for_status()
        data = r.json()
    except Exception as e:
        err = {"error": f"web_search failed: {e}"}
        print(f"[DEBUG web_search] {json.dumps(err, indent=2)}")
        return err

    results = []
    for item in data.get("results", [])[:5]:
        results.append({
            "title": item.get("title"),
            "url": item.get("url"),
            "content": item.get("content"),
        })

    result = {"query": query, "result_count": len(results), "results": results}
    print(f"[DEBUG web_search] {json.dumps(result, indent=2)}")
    return result


def entity_lookup(company: str) -> dict:
    """
    Look up entity information: headquarters, incorporation, key people,
    ownership structure. Combines Wikipedia summary with a targeted web search.
    """
    entity_data = {}

    # 1. Wikipedia summary — free, reliable, structured
    wiki_title = company.replace(" ", "_")
    try:
        r = httpx.get(
            f"https://en.wikipedia.org/api/rest_v1/page/summary/{wiki_title}",
            headers={"User-Agent": "cohere-tprm-learning/0.1"},
            timeout=10,
        )
        if r.status_code == 200:
            data = r.json()
            entity_data["wikipedia"] = {
                "title": data.get("title"),
                "description": data.get("description"),
                "summary": data.get("extract", "")[:1000],
            }
        else:
            entity_data["wikipedia"] = {"note": "No Wikipedia article found"}
    except Exception as e:
        entity_data["wikipedia"] = {"error": str(e)}

    # 2. Targeted web search for corporate details
    api_key = os.environ.get("TAVILY_API_KEY")
    if api_key:
        detail_query = (
            f'"{company}" (headquarters OR founded OR CEO OR '
            f"incorporation OR ownership OR subsidiary OR parent company)"
        )
        try:
            r = httpx.post(
                "https://api.tavily.com/search",
                json={"api_key": api_key, "query": detail_query, "max_results": 5},
                timeout=15,
            )
            r.raise_for_status()
            data = r.json()
            entity_data["corporate_details"] = [
                {
                    "title": item.get("title"),
                    "url": item.get("url"),
                    "content": item.get("content"),
                }
                for item in data.get("results", [])[:5]
            ]
        except Exception as e:
            entity_data["corporate_details"] = [{"error": str(e)}]
    else:
        entity_data["corporate_details"] = [{"note": "TAVILY_API_KEY not set"}]

    result = {"query": company, **entity_data}
    print(f"[DEBUG entity_lookup] {json.dumps(result, indent=2)}")
    return result


def trust_center_search(company: str) -> dict:
    """
    Search for a company's trust center, security certifications, and
    compliance posture. Looks for SOC 2, ISO 27001, PCI DSS, GDPR, PIPEDA,
    and other certifications. Uses Tavily API.
    """
    api_key = os.environ.get("TAVILY_API_KEY")
    if not api_key:
        return {"error": "TAVILY_API_KEY environment variable not set"}

    search_query = (
        f'"{company}" (trust center OR security certifications OR '
        f"SOC 2 OR ISO 27001 OR PCI DSS OR GDPR OR PIPEDA OR "
        f"penetration test OR security posture OR compliance)"
    )

    try:
        r = httpx.post(
            "https://api.tavily.com/search",
            json={"api_key": api_key, "query": search_query, "max_results": 10},
            timeout=15,
        )
        r.raise_for_status()
        data = r.json()
    except Exception as e:
        err = {"error": f"trust_center_search failed: {e}"}
        print(f"[DEBUG trust_center_search] {json.dumps(err, indent=2)}")
        return err

    results = []
    for item in data.get("results", [])[:10]:
        results.append({
            "title": item.get("title"),
            "url": item.get("url"),
            "content": item.get("content"),
        })

    result = {"query": company, "result_count": len(results), "results": results}
    print(f"[DEBUG trust_center_search] {json.dumps(result, indent=2)}")
    return result


def adverse_media(query: str, timespan: str = "3months") -> dict:
    """
    Search GDELT global news for adverse media coverage of an entity.
    Free API, no key required. Searches for the entity name combined with
    adverse keywords to surface controversies, legal issues, and scandals.
    """
    gdelt_query = f'"{query}" (scandal OR fraud OR lawsuit OR penalty OR violation OR sanction OR investigation OR settlement)'
    params = {
        "query": gdelt_query,
        "mode": "artlist",
        "format": "json",
        "maxrecords": 10,
        "timespan": timespan,
        "sort": "datedesc",
    }

    data = None
    for attempt in range(3):
        try:
            r = httpx.get(
                "https://api.gdeltproject.org/api/v2/doc/doc",
                params=params,
                timeout=15,
            )
            r.raise_for_status()
            data = r.json()
            break
        except httpx.HTTPStatusError as e:
            if e.response.status_code == 429 and attempt < 2:
                time.sleep(2 ** attempt)
                continue
            err = {"error": f"adverse_media failed: {e}"}
            print(f"[DEBUG adverse_media] {json.dumps(err, indent=2)}")
            return err
        except Exception as e:
            err = {"error": f"adverse_media failed: {e}"}
            print(f"[DEBUG adverse_media] {json.dumps(err, indent=2)}")
            return err

    articles = []
    for art in data.get("articles", [])[:10]:
        articles.append({
            "title": art.get("title"),
            "url": art.get("url"),
            "source": art.get("domain"),
            "date": art.get("seendate", "")[:8],
            "language": art.get("language"),
            "country": art.get("sourcecountry"),
        })

    result = {"query": query, "article_count": len(articles), "articles": articles}
    print(f"[DEBUG adverse_media] {json.dumps(result, indent=2)}")
    return result


EDGAR_BASE = "https://efts.sec.gov/LATEST/search-index"
EDGAR_HEADERS = {"User-Agent": "cohere-tprm-learning/0.1 dlwhyte@gmail.com"}


def sec_filing_search(company: str) -> dict:
    """
    Search SEC EDGAR for company filings (10-K, 10-Q, 8-K).
    Free API, no key required. US-listed companies only.
    """
    try:
        r = httpx.get(
            EDGAR_BASE,
            params={
                "q": f'"{company}"',
                "forms": "10-K,10-Q,8-K",
                "dateRange": "custom",
                "startdt": "2020-01-01",
                "enddt": "2026-12-31",
            },
            headers=EDGAR_HEADERS,
            timeout=15,
        )
        r.raise_for_status()
        data = r.json()
    except Exception as e:
        err = {"error": f"sec_filing_search failed: {e}"}
        print(f"[DEBUG sec_filing_search] {json.dumps(err, indent=2)}")
        return err

    raw_hits = data.get("hits", {}).get("hits", [])
    total = data.get("hits", {}).get("total", {}).get("value", 0)

    filings = []
    for hit in raw_hits[:10]:
        src = hit.get("_source", {})
        filings.append({
            "entity": (src.get("display_names") or ["Unknown"])[0],
            "form_type": (src.get("root_forms") or ["Unknown"])[0],
            "file_date": src.get("file_date"),
            "period_ending": src.get("period_ending"),
            "description": src.get("file_description"),
        })

    result = {"query": company, "total_filings": total, "filings": filings}
    print(f"[DEBUG sec_filing_search] {json.dumps(result, indent=2)}")
    return result


def sec_enforcement_search(company: str) -> dict:
    """
    Search SEC EDGAR filings for mentions of enforcement actions, penalties,
    or violations related to a company. Free API, no key required.
    """
    try:
        r = httpx.get(
            EDGAR_BASE,
            params={
                "q": f'"{company}" AND ("enforcement action" OR "penalty" OR '
                     f'"violation" OR "cease and desist" OR "settlement")',
                "dateRange": "custom",
                "startdt": "2020-01-01",
                "enddt": "2026-12-31",
            },
            headers=EDGAR_HEADERS,
            timeout=15,
        )
        r.raise_for_status()
        data = r.json()
    except Exception as e:
        err = {"error": f"sec_enforcement_search failed: {e}"}
        print(f"[DEBUG sec_enforcement_search] {json.dumps(err, indent=2)}")
        return err

    raw_hits = data.get("hits", {}).get("hits", [])
    total = data.get("hits", {}).get("total", {}).get("value", 0)

    filings = []
    for hit in raw_hits[:10]:
        src = hit.get("_source", {})
        filings.append({
            "entity": (src.get("display_names") or ["Unknown"])[0],
            "form_type": (src.get("root_forms") or ["Unknown"])[0],
            "file_date": src.get("file_date"),
            "description": src.get("file_description"),
        })

    result = {"query": company, "total_hits": total, "filings": filings}
    print(f"[DEBUG sec_enforcement_search] {json.dumps(result, indent=2)}")
    return result


# ---------- KEV cache ----------

_kev_cache: dict | None = None


def _load_kev() -> set[str]:
    """Download and cache CISA KEV CVE IDs. Returns a set of CVE IDs."""
    global _kev_cache
    if _kev_cache is not None:
        return _kev_cache

    try:
        r = httpx.get(
            "https://www.cisa.gov/sites/default/files/feeds/known_exploited_vulnerabilities.json",
            timeout=15,
        )
        r.raise_for_status()
        data = r.json()
        _kev_cache = {v.get("cveID") for v in data.get("vulnerabilities", [])}
    except Exception:
        _kev_cache = set()

    return _kev_cache


def cve_lookup(product: str) -> dict:
    """
    Search NVD for CVEs related to a product/vendor and cross-reference
    with CISA Known Exploited Vulnerabilities (KEV) catalog.
    Free APIs, no key required.
    """
    # 1. Search NVD
    try:
        r = httpx.get(
            "https://services.nvd.nist.gov/rest/json/cves/2.0",
            params={"keywordSearch": product, "resultsPerPage": 10},
            timeout=20,
        )
        r.raise_for_status()
        data = r.json()
    except Exception as e:
        err = {"error": f"cve_lookup failed: {e}"}
        print(f"[DEBUG cve_lookup] {json.dumps(err, indent=2)}")
        return err

    # 2. Load KEV for cross-referencing
    kev_ids = _load_kev()

    total = data.get("totalResults", 0)
    cves = []
    for v in data.get("vulnerabilities", [])[:10]:
        cve = v.get("cve", {})
        cve_id = cve.get("id", "")

        # Extract CVSS score
        metrics = cve.get("metrics", {})
        cvss31 = metrics.get("cvssMetricV31", [])
        cvss30 = metrics.get("cvssMetricV30", [])
        cvss_data = (cvss31 or cvss30 or [{}])[0].get("cvssData", {})
        score = cvss_data.get("baseScore")
        severity = cvss_data.get("baseSeverity")

        # Extract description
        descs = cve.get("descriptions", [])
        desc = next((d["value"] for d in descs if d["lang"] == "en"), "")

        cves.append({
            "id": cve_id,
            "published": cve.get("published", "")[:10],
            "score": score,
            "severity": severity,
            "in_kev": cve_id in kev_ids,
            "description": desc[:300],
        })

    # Sort by severity: CRITICAL > HIGH > MEDIUM > LOW
    severity_order = {"CRITICAL": 0, "HIGH": 1, "MEDIUM": 2, "LOW": 3}
    cves.sort(key=lambda c: severity_order.get(c.get("severity", ""), 99))

    result = {
        "query": product,
        "total_cves": total,
        "kev_matches": sum(1 for c in cves if c.get("in_kev")),
        "cves": cves,
    }
    print(f"[DEBUG cve_lookup] {json.dumps(result, indent=2)}")
    return result


# Registry: tool name -> callable. This is the allowlist.
TOOL_REGISTRY = {
    "sanctions_lookup": sanctions_lookup,
    "web_search": web_search,
    "entity_lookup": entity_lookup,
    "trust_center_search": trust_center_search,
    "adverse_media": adverse_media,
    "sec_filing_search": sec_filing_search,
    "sec_enforcement_search": sec_enforcement_search,
    "cve_lookup": cve_lookup,
}

# Schemas exposed to the model. JSON Schema inside Cohere's tool spec.
TOOL_SCHEMAS = [
    {
        "type": "function",
        "function": {
            "name": "sanctions_lookup",
            "description": (
                "Search US OFAC SDN, UN Security Council, and EU sanctions lists "
                "for a company or person. Uses fuzzy name matching and returns up "
                "to 5 potential matches with a name_similarity score (0-1). Only "
                "treat hits with similarity above 0.6 as relevant. Lower scores "
                "are likely false positives — note them as non-matches."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "name": {
                        "type": "string",
                        "description": "Entity name to screen.",
                    },
                },
                "required": ["name"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "web_search",
            "description": (
                "Search the web for recent information about a company, person, "
                "or topic. Returns up to 5 results with titles, URLs, and content "
                "snippets. Use this to supplement sanctions data with news, "
                "regulatory actions, or other public information."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "query": {
                        "type": "string",
                        "description": "Search query.",
                    },
                },
                "required": ["query"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "entity_lookup",
            "description": (
                "Look up entity information: who they are, where headquartered, "
                "when founded, key people (CEO, founders), ownership structure, "
                "parent company, and subsidiaries. Combines Wikipedia data with "
                "corporate detail searches. Use this to populate the Entity summary."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "company": {
                        "type": "string",
                        "description": "Company name to look up.",
                    },
                },
                "required": ["company"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "trust_center_search",
            "description": (
                "Search for a company's trust center, security certifications, "
                "and compliance posture. Looks for SOC 2, ISO 27001, PCI DSS, "
                "GDPR, PIPEDA, penetration test results, and other security "
                "credentials. Use this to assess the vendor's security maturity."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "company": {
                        "type": "string",
                        "description": "Company name to search for.",
                    },
                },
                "required": ["company"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "adverse_media",
            "description": (
                "Search GDELT global news database for adverse media coverage of "
                "a company or person. Automatically filters for scandal, fraud, "
                "lawsuit, penalty, and investigation keywords. Returns up to 10 "
                "recent articles with titles, URLs, dates, and sources."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "query": {
                        "type": "string",
                        "description": "Entity or topic to search for.",
                    },
                    "timespan": {
                        "type": "string",
                        "description": (
                            "How far back to search. Examples: '1week', '1month', "
                            "'3months'. Default '3months'."
                        ),
                    },
                },
                "required": ["query"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "sec_filing_search",
            "description": (
                "Search SEC EDGAR for company filings (10-K annual reports, "
                "10-Q quarterly reports, 8-K material events). US-listed "
                "companies only. Use this to verify a company exists as a "
                "public entity and review its disclosure history."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "company": {
                        "type": "string",
                        "description": "Company name to search for.",
                    },
                },
                "required": ["company"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "sec_enforcement_search",
            "description": (
                "Search SEC EDGAR filings for mentions of enforcement actions, "
                "penalties, violations, or settlements related to a company. "
                "Use this to identify regulatory risk and compliance history."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "company": {
                        "type": "string",
                        "description": "Company name to search for.",
                    },
                },
                "required": ["company"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "cve_lookup",
            "description": (
                "Search NVD for known vulnerabilities (CVEs) related to a "
                "product or vendor. Cross-references with CISA Known Exploited "
                "Vulnerabilities (KEV) catalog. Returns CVE IDs, CVSS scores, "
                "severity, and whether each CVE is actively exploited."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "product": {
                        "type": "string",
                        "description": "Product or vendor name to search for.",
                    },
                },
                "required": ["product"],
            },
        },
    },
]
