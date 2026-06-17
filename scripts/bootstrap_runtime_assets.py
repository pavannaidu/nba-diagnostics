#!/usr/bin/env python3
"""Provision runtime recommendation assets for the hybrid DAB workflow."""

from __future__ import annotations

import argparse
import json

from runtime_lib import (
    BootstrapResult,
    SPACE_TITLE,
    SUPERVISOR_DISPLAY_NAME,
    apply_runtime_sql,
    build_workspace_client,
    ensure_genie_space,
    ensure_knowledge_assistant,
    ensure_supervisor_agent,
    grant_runtime_permissions,
    stage_guidance_documents,
    write_runtime_config,
)


CUSTOM_AGENT_MODEL_OPTIONS = [
    {"id": "databricks-claude-sonnet-4-5", "label": "Claude Sonnet 4.5"},
]


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--catalog", default="pavan_naidu")
    parser.add_argument("--schema", default="nba")
    parser.add_argument("--warehouse-id", required=True)
    parser.add_argument("--guidance-volume-name", default="runtime_guidance")
    parser.add_argument("--app-name", required=True)
    parser.add_argument("--custom-agent-app-name", default=None)
    parser.add_argument("--profile", default=None)
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    client = build_workspace_client(args.profile)
    current_user = client.current_user.me()
    parent_path = f"/Users/{current_user.user_name}"

    apply_runtime_sql(client, args.warehouse_id, args.catalog, args.schema)
    guidance_path = stage_guidance_documents(
        client,
        args.catalog,
        args.schema,
        args.guidance_volume_name,
    )
    genie_space = ensure_genie_space(client, args.catalog, args.schema, args.warehouse_id, parent_path)
    knowledge_assistant = ensure_knowledge_assistant(client, guidance_path)
    supervisor = ensure_supervisor_agent(
        client,
        args.catalog,
        args.schema,
        genie_space["space_id"],
        knowledge_assistant["knowledge_assistant_id"],
    )

    permission_result = grant_runtime_permissions(
        client,
        args.warehouse_id,
        args.catalog,
        args.schema,
        args.app_name,
        current_user.user_name,
        genie_space["space_id"],
        knowledge_assistant["knowledge_assistant_id"],
        knowledge_assistant.get("endpoint_name", ""),
        supervisor["name"],
        supervisor["endpoint_name"],
        custom_agent_app_name=args.custom_agent_app_name,
    )

    write_runtime_config(
        client,
        args.warehouse_id,
        args.catalog,
        args.schema,
        {
            "genie_space_id": genie_space["space_id"],
            "genie_space_title": genie_space.get("title", SPACE_TITLE),
            "knowledge_assistant_id": knowledge_assistant["knowledge_assistant_id"],
            "knowledge_assistant_name": knowledge_assistant["name"],
            "knowledge_assistant_endpoint": knowledge_assistant["endpoint_name"],
            "guidance_volume_path": guidance_path,
            "app_service_principal": permission_result["app_service_principal"],
            "debug_user_name": permission_result["debug_user_name"],
            "supervisor_display_name": supervisor.get("display_name", SUPERVISOR_DISPLAY_NAME),
            "supervisor_label": "Supervisor Agent",
            "supervisor_endpoint": supervisor["endpoint_name"],
            "supervisor_name": supervisor["name"],
            "custom_agent_app_name": args.custom_agent_app_name or "",
            "custom_agent_app_url": permission_result.get("custom_agent_app_url", ""),
            "custom_agent_label": "Custom Agent",
            "custom_agent_default_model_id": CUSTOM_AGENT_MODEL_OPTIONS[0]["id"],
            "custom_agent_model_options": json.dumps(CUSTOM_AGENT_MODEL_OPTIONS, separators=(",", ":")),
            "default_agent_target_id": "supervisor",
        },
    )

    result = BootstrapResult(
        guidance_path=guidance_path,
        genie_space_id=genie_space["space_id"],
        knowledge_assistant_id=knowledge_assistant["knowledge_assistant_id"],
        knowledge_assistant_name=knowledge_assistant["name"],
        knowledge_assistant_endpoint=knowledge_assistant["endpoint_name"],
        supervisor_name=supervisor["name"],
        supervisor_endpoint=supervisor["endpoint_name"],
        custom_agent_app_name=args.custom_agent_app_name or "",
        custom_agent_app_url=permission_result.get("custom_agent_app_url", ""),
        app_service_principal=permission_result["app_service_principal"],
        custom_agent_service_principal=permission_result.get("custom_agent_service_principal", ""),
        debug_user_name=permission_result["debug_user_name"],
    )
    print(json.dumps(result.__dict__, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    exit_code = main()
    if exit_code:
        raise SystemExit(exit_code)
