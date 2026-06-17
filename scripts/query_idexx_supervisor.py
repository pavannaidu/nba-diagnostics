#!/usr/bin/env python3
"""Query the live IDEXX Supervisor Agent endpoint from the terminal."""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
from typing import Any, Dict, List


DEFAULT_PROFILE = "FEVM"
DEFAULT_ENDPOINT = "mas-f8497733-endpoint"


def run_response_query(prompt: str, endpoint: str, profile: str) -> Dict[str, Any]:
    env = os.environ.copy()
    env["DATABRICKS_AUTH_STORAGE"] = "plaintext"
    body = {
        "model": endpoint,
        "input": [
            {
                "role": "user",
                "content": prompt,
            }
        ],
    }
    cmd = [
        "databricks",
        "api",
        "post",
        "/serving-endpoints/responses",
        "-p",
        profile,
        "-o",
        "json",
        "--json",
        json.dumps(body),
    ]
    result = subprocess.run(cmd, env=env, capture_output=True, text=True, check=True)
    return json.loads(result.stdout)


def extract_assistant_text(payload: Dict[str, Any]) -> List[str]:
    texts: List[str] = []
    for item in payload.get("output", []):
        if item.get("type") != "message":
            continue
        if item.get("role") != "assistant":
            continue
        for content in item.get("content", []):
            if content.get("type") == "output_text":
                text = content.get("text")
                if text:
                    texts.append(text)
    return texts


def extract_tool_calls(payload: Dict[str, Any]) -> List[Dict[str, Any]]:
    return [item for item in payload.get("output", []) if item.get("type") == "function_call"]


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("prompt", help="Prompt to send to the live supervisor endpoint.")
    parser.add_argument("--profile", default=DEFAULT_PROFILE)
    parser.add_argument("--endpoint", default=DEFAULT_ENDPOINT)
    parser.add_argument("--show-tools", action="store_true", help="Show tool calls in addition to the final answer.")
    parser.add_argument("--raw", action="store_true", help="Print the full raw JSON payload.")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    payload = run_response_query(args.prompt, args.endpoint, args.profile)

    if args.raw:
        print(json.dumps(payload, indent=2))
        return 0

    texts = extract_assistant_text(payload)
    if args.show_tools:
        tool_calls = extract_tool_calls(payload)
        if tool_calls:
            print("Tool calls:")
            for tool_call in tool_calls:
                print(f"- {tool_call.get('name')}: {tool_call.get('arguments')}")
            print()

    if texts:
        print(texts[-1])
    else:
        print(json.dumps(payload, indent=2))
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except subprocess.CalledProcessError as exc:
        sys.stderr.write(exc.stderr)
        raise
