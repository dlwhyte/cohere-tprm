"""
Cohere-powered TPRM agent.

Tools: sanctions screening (sanctions.network) and web search (Tavily).
Uses Cohere V2 multi-step tool use with content isolation.

Run:
    python agent.py "Risk brief for Wagner Group"
    python agent.py "Screen the company Acme Corp for sanctions"
"""

from __future__ import annotations

import json
import os
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


def run_agent(user_query: str, max_steps: int = 8) -> str | None:
    """Cohere V2 multi-step tool-use loop."""
    co = _get_client()
    messages = [
        {"role": "system", "content": SYSTEM_PROMPT},
        {"role": "user", "content": user_query},
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


if __name__ == "__main__":
    query = " ".join(sys.argv[1:]) or "Produce a risk brief for the company 'Wagner Group'."
    run_agent(query)
