"""Runtime configuration for the IDEXX next-best-action app."""

from __future__ import annotations

import os
from dataclasses import dataclass
from functools import lru_cache

from databricks.sdk import WorkspaceClient


def normalize_host(host: str | None) -> str | None:
    if not host:
        return None
    if host.startswith("http://") or host.startswith("https://"):
        return host
    return f"https://{host}"


@dataclass(frozen=True)
class Settings:
    app_title: str
    catalog: str
    schema: str
    warehouse_id: str
    workspace_profile: str
    sample_visit_limit: int
    default_supervisor_endpoint: str | None
    supervisor_connect_timeout_seconds: int
    supervisor_read_timeout_seconds: int


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings(
        app_title=os.getenv("NBA_APP_TITLE", "IDEXX Next Best Action"),
        catalog=os.getenv("DATABRICKS_CATALOG", "pavan_naidu"),
        schema=os.getenv("DATABRICKS_SCHEMA", "nba"),
        warehouse_id=os.getenv("DATABRICKS_WAREHOUSE_ID", "e0c50bf18fca9e7f"),
        workspace_profile=os.getenv("DATABRICKS_PROFILE", "FEVM"),
        sample_visit_limit=int(os.getenv("NBA_SAMPLE_VISIT_LIMIT", "8")),
        default_supervisor_endpoint=os.getenv("NBA_SUPERVISOR_ENDPOINT") or None,
        supervisor_connect_timeout_seconds=int(
            os.getenv("NBA_SUPERVISOR_CONNECT_TIMEOUT_SECONDS", "15")
        ),
        supervisor_read_timeout_seconds=int(
            os.getenv("NBA_SUPERVISOR_READ_TIMEOUT_SECONDS", "300")
        ),
    )


def is_databricks_app() -> bool:
    return bool(os.getenv("DATABRICKS_APP_NAME"))


@lru_cache(maxsize=1)
def get_workspace_client() -> WorkspaceClient:
    if is_databricks_app():
        host = normalize_host(os.getenv("DATABRICKS_HOST"))
        return WorkspaceClient(host=host) if host else WorkspaceClient()

    explicit_host = normalize_host(os.getenv("DATABRICKS_HOST"))
    explicit_token = os.getenv("DATABRICKS_TOKEN")
    if explicit_host and explicit_token:
        return WorkspaceClient(host=explicit_host, token=explicit_token)

    settings = get_settings()
    os.environ.setdefault("DATABRICKS_AUTH_STORAGE", "plaintext")
    return WorkspaceClient(profile=settings.workspace_profile)
