"""
Cohere-powered TPRM agent.

Tools: sanctions screening, web search, trust center, adverse media, SEC EDGAR.
Uses Cohere V2 multi-step tool use with content isolation.

Run:
    python agent.py "Risk brief for Wagner Group"
    python agent.py "Screen the company Acme Corp for sanctions"
"""

from __future__ import annotations

import json
import os
import re
import sys
import warnings

warnings.filterwarnings("ignore", message="urllib3.*OpenSSL")

import cohere
from dotenv import load_dotenv

from prompts import SYSTEM_PROMPT
from tools import TOOL_REGISTRY, TOOL_SCHEMAS

load_dotenv()

MODEL = "command-r-plus-08-2024"


def _get_client() -> cohere.ClientV2:
    return cohere.ClientV2(os.environ["COHERE_API_KEY"])


def _extract_entity(query: str) -> str:
    """Best-effort extraction of the entity name from the user query."""
    # Remove common preambles
    cleaned = re.sub(
        r"(?i)^(risk brief for|screen|check|look up|search for|brief on)\s+",
        "",
        query.strip(),
    )
    # Remove quotes and "the company" prefix
    cleaned = re.sub(r"(?i)^the company\s+", "", cleaned)
    cleaned = cleaned.strip("'\"")
    return cleaned or query


def _required_tools(entity: str) -> dict:
    """Return the tool name -> args mapping for mandatory pre-screening."""
    return {
        "entity_lookup": {"company": entity},
        "sanctions_lookup": {"name": entity},
        "sec_filing_search": {"company": entity},
        "sec_enforcement_search": {"company": entity},
        "trust_center_search": {"company": entity},
        "adverse_media": {"query": entity},
        "cve_lookup": {"product": entity},
        "web_search": {"query": entity},
    }


def _compute_risk(pre_results: dict) -> str:
    """
    Deterministic risk rating based on tool results.
    Returns HIGH, MEDIUM, or LOW with a reason.
    """
    reasons_high = []
    reasons_medium = []

    # Check sanctions
    sanctions = pre_results.get("sanctions_lookup", {})
    for hit in sanctions.get("hits", []):
        if hit.get("name_similarity", 0) >= 0.6:
            reasons_high.append(
                f"Confirmed sanctions listing: {hit.get('source', '').upper()} "
                f"(ID: {hit.get('id')}, similarity: {hit.get('name_similarity')})"
            )

    # Check SEC enforcement
    enforcement = pre_results.get("sec_enforcement_search", {})
    if enforcement.get("total_hits", 0) > 0:
        reasons_high.append(
            f"SEC enforcement hits: {enforcement['total_hits']} filings "
            f"mentioning enforcement actions or penalties"
        )

    # Check CVE/KEV
    cves = pre_results.get("cve_lookup", {})
    kev_count = cves.get("kev_matches", 0)
    if kev_count > 0:
        reasons_high.append(
            f"CISA KEV: {kev_count} actively exploited vulnerability(ies) found"
        )
    elif cves.get("total_cves", 0) > 0:
        # Check for CRITICAL/HIGH CVEs
        critical_high = [
            c for c in cves.get("cves", [])
            if c.get("severity") in ("CRITICAL", "HIGH")
        ]
        if critical_high:
            reasons_medium.append(
                f"CVEs: {len(critical_high)} CRITICAL/HIGH severity "
                f"vulnerability(ies) found out of {cves['total_cves']} total"
            )

    # Check adverse media
    adverse = pre_results.get("adverse_media", {})
    if adverse.get("article_count", 0) > 3:
        reasons_medium.append(
            f"Adverse media: {adverse['article_count']} articles found"
        )

    # Check adverse media fallback
    fallback = pre_results.get("adverse_media_fallback", {})
    if fallback.get("result_count", 0) > 2:
        reasons_medium.append(
            f"Adverse web results: {fallback['result_count']} results for "
            f"scandal/lawsuit/penalty queries"
        )

    if reasons_high:
        return f"HIGH — {'; '.join(reasons_high)}"
    elif reasons_medium:
        return f"MEDIUM — {'; '.join(reasons_medium)}"
    else:
        return "LOW — No sanctions, enforcement actions, or significant adverse media found"


def _pre_screen(entity: str) -> dict:
    """
    Run all required tools upfront before the model starts.
    Returns a dict of tool_name -> result.
    """
    results = {}

    # Always run these tools
    required_tools = _required_tools(entity)

    for tool_name, args in required_tools.items():
        print(f"[pre-screen] -> {tool_name}({args})")
        try:
            results[tool_name] = TOOL_REGISTRY[tool_name](**args)
        except Exception as e:
            results[tool_name] = {"error": f"{tool_name} raised: {e}"}

    # If adverse_media failed, run a fallback web search with adverse keywords
    adverse = results.get("adverse_media", {})
    if "error" in adverse or adverse.get("article_count", 0) == 0:
        fallback_query = f"{entity} scandal lawsuit penalty investigation"
        print(f"[pre-screen] -> web_search (adverse fallback)({fallback_query})")
        try:
            results["adverse_media_fallback"] = TOOL_REGISTRY["web_search"](
                query=fallback_query
            )
        except Exception as e:
            results["adverse_media_fallback"] = {"error": str(e)}

    return results


def run_agent(user_query: str, max_steps: int = 8) -> str | None:
    """Cohere V2 multi-step tool-use loop with mandatory pre-screening."""
    co = _get_client()

    # Extract entity and run all tools upfront
    entity = _extract_entity(user_query)
    print(f"[agent] entity: {entity}")
    pre_results = _pre_screen(entity)

    # Format pre-screen results as context for the model
    context_parts = []
    for tool_name, result in pre_results.items():
        context_parts.append(
            f"<tool_result tool=\"{tool_name}\">{json.dumps(result)}</tool_result>"
        )
    pre_screen_context = "\n\n".join(context_parts)

    # Compute risk rating deterministically
    risk_rating = _compute_risk(pre_results)
    print(f"[agent] computed risk: {risk_rating}")

    messages = [
        {"role": "system", "content": SYSTEM_PROMPT},
        {
            "role": "user",
            "content": (
                f"{user_query}\n\n"
                f"The following tool results have already been gathered for "
                f"'{entity}'. Use ONLY this data to write the brief. Do NOT "
                f"make up any facts. If a tool returned an error, note it as "
                f"an information gap.\n\n{pre_screen_context}\n\n"
                f"MANDATORY RISK RATING (computed from tool data — do NOT "
                f"override this): **{risk_rating}**"
            ),
        },
    ]

    for step in range(max_steps):
        resp = co.chat(model=MODEL, messages=messages, tools=TOOL_SCHEMAS)
        msg = resp.message

        if getattr(msg, "tool_plan", None):
            print(f"\n[step {step}] plan: {msg.tool_plan}")

        # No tool calls means the model has produced the final answer.
        if not msg.tool_calls:
            final = "".join(
                c.text for c in (msg.content or []) if getattr(c, "type", "") == "text"
            )
            print("\n" + "=" * 60 + "\nBRIEF\n" + "=" * 60)
            print(final)
            return final

        # Record the assistant turn (required for the next call to have context).
        messages.append({
            "role": "assistant",
            "tool_calls": [
                {
                    "id": tc.id,
                    "type": "function",
                    "function": {
                        "name": tc.function.name,
                        "arguments": tc.function.arguments,
                    },
                }
                for tc in msg.tool_calls
            ],
            "tool_plan": msg.tool_plan,
        })

        # Execute each tool call. Only tools in the registry are callable.
        for tc in msg.tool_calls:
            name = tc.function.name
            try:
                args = json.loads(tc.function.arguments)
            except json.JSONDecodeError:
                args = {}

            print(f"[step {step}] -> {name}({args})")

            if name not in TOOL_REGISTRY:
                result = {"error": f"tool '{name}' not in allowlist"}
            else:
                try:
                    result = TOOL_REGISTRY[name](**args)
                except TypeError as e:
                    result = {"error": f"bad arguments to {name}: {e}"}
                except Exception as e:
                    result = {"error": f"{name} raised: {e}"}

            # Wrap in tags to signal "untrusted data, not instructions".
            wrapped = f"<tool_result>{json.dumps(result)}</tool_result>"

            messages.append({
                "role": "tool",
                "tool_call_id": tc.id,
                "content": wrapped,
            })

    print(f"\n[halted after {max_steps} steps without final answer]")
    return None


TOOL_LABELS = {
    "entity_lookup": "Looking up entity details",
    "sanctions_lookup": "Checking sanctions lists",
    "sec_filing_search": "Searching SEC filings",
    "sec_enforcement_search": "Checking SEC enforcement actions",
    "trust_center_search": "Searching trust centers",
    "adverse_media": "Scanning adverse media",
    "cve_lookup": "Checking vulnerabilities (CVE/KEV)",
    "web_search": "Searching the web",
}


def run_agent_streaming(user_query: str, max_steps: int = 8):
    """
    Generator that yields status events during pre-screening and the final brief.
    Events: {"type": "tool", "tool": "...", "label": "..."} during pre-screen,
            {"type": "generating"} when model is writing,
            {"type": "brief", "brief": "..."} on completion,
            {"type": "error", "message": "..."} on failure.
    """
    entity = _extract_entity(user_query)
    yield {"type": "entity", "entity": entity}

    # Pre-screen with progress events
    required_tools = _required_tools(entity)

    pre_results = {}
    for tool_name, args in required_tools.items():
        label = TOOL_LABELS.get(tool_name, tool_name)
        yield {"type": "tool", "tool": tool_name, "label": label}
        try:
            pre_results[tool_name] = TOOL_REGISTRY[tool_name](**args)
        except Exception as e:
            pre_results[tool_name] = {"error": f"{tool_name} raised: {e}"}

    # Adverse media fallback
    adverse = pre_results.get("adverse_media", {})
    if "error" in adverse or adverse.get("article_count", 0) == 0:
        fallback_query = f"{entity} scandal lawsuit penalty investigation"
        yield {"type": "tool", "tool": "web_search", "label": "Searching adverse media (fallback)"}
        try:
            pre_results["adverse_media_fallback"] = TOOL_REGISTRY["web_search"](
                query=fallback_query
            )
        except Exception as e:
            pre_results["adverse_media_fallback"] = {"error": str(e)}

    # Compute risk rating deterministically
    risk_rating = _compute_risk(pre_results)

    # Generate brief
    yield {"type": "generating"}

    context_parts = []
    for tool_name, result in pre_results.items():
        context_parts.append(
            f"<tool_result tool=\"{tool_name}\">{json.dumps(result)}</tool_result>"
        )
    pre_screen_context = "\n\n".join(context_parts)

    try:
        co = _get_client()
        messages = [
            {"role": "system", "content": SYSTEM_PROMPT},
            {
                "role": "user",
                "content": (
                    f"{user_query}\n\n"
                    f"The following tool results have already been gathered for "
                    f"'{entity}'. Use ONLY this data to write the brief. Do NOT "
                    f"make up any facts. If a tool returned an error, note it as "
                    f"an information gap.\n\n{pre_screen_context}\n\n"
                    f"MANDATORY RISK RATING (computed from tool data — do NOT "
                    f"override this): **{risk_rating}**"
                ),
            },
        ]

        for step in range(max_steps):
            resp = co.chat(model=MODEL, messages=messages, tools=TOOL_SCHEMAS)
            msg = resp.message

            if not msg.tool_calls:
                final = "".join(
                    c.text for c in (msg.content or [])
                    if getattr(c, "type", "") == "text"
                )
                yield {"type": "brief", "brief": final}
                return

            messages.append({
                "role": "assistant",
                "tool_calls": [
                    {
                        "id": tc.id,
                        "type": "function",
                        "function": {
                            "name": tc.function.name,
                            "arguments": tc.function.arguments,
                        },
                    }
                    for tc in msg.tool_calls
                ],
                "tool_plan": msg.tool_plan,
            })

            for tc in msg.tool_calls:
                name = tc.function.name
                try:
                    args = json.loads(tc.function.arguments)
                except json.JSONDecodeError:
                    args = {}

                if name not in TOOL_REGISTRY:
                    result = {"error": f"tool '{name}' not in allowlist"}
                else:
                    try:
                        result = TOOL_REGISTRY[name](**args)
                    except TypeError as e:
                        result = {"error": f"bad arguments to {name}: {e}"}
                    except Exception as e:
                        result = {"error": f"{name} raised: {e}"}

                wrapped = f"<tool_result>{json.dumps(result)}</tool_result>"
                messages.append({
                    "role": "tool",
                    "tool_call_id": tc.id,
                    "content": wrapped,
                })

        yield {"type": "error", "message": f"Halted after {max_steps} steps"}
    except Exception as e:
        yield {"type": "error", "message": str(e)}


if __name__ == "__main__":
    query = " ".join(sys.argv[1:]) or "Produce a risk brief for the company 'Wagner Group'."
    run_agent(query)
