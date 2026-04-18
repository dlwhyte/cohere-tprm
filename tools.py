"""Tool implementations and registry for the TPRM agent."""

from __future__ import annotations

import json
import os

import httpx


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
        hits.append({
            "id": hit.get("source_id"),
            "names": hit.get("names", []),
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


# Registry: tool name -> callable. This is the allowlist.
TOOL_REGISTRY = {
    "sanctions_lookup": sanctions_lookup,
    "web_search": web_search,
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
                "to 5 potential matches. A hit is not a confirmed match — inspect "
                "the names, source, and remarks fields to judge relevance."
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
]
