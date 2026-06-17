"""Small Databricks transport wrapper for the app runtime."""

from __future__ import annotations

import json
import logging
import time
from functools import cached_property
from typing import Any

import requests

from .config import get_settings, get_workspace_client, normalize_host


LOGGER = logging.getLogger(__name__)


def quote_sql(value: str) -> str:
    return "'" + value.replace("'", "''") + "'"


def maybe_json_load(value: Any) -> Any:
    if not isinstance(value, str):
        return value
    try:
        return json.loads(value)
    except json.JSONDecodeError:
        return value


def parse_responses_api_body(response: requests.Response) -> dict[str, Any]:
    try:
        payload = response.json()
    except ValueError:
        payload = None

    if isinstance(payload, dict):
        return payload

    events: list[dict[str, Any]] = []
    for line in response.text.splitlines():
        if not line.startswith("data:"):
            continue
        raw_data = line[5:].strip()
        if not raw_data or raw_data == "[DONE]":
            continue
        parsed = maybe_json_load(raw_data)
        if isinstance(parsed, dict):
            if parsed.get("error_code"):
                raise RuntimeError(
                    f"{parsed.get('error_code')}: {parsed.get('message', 'Supervisor request failed.')}"
                )
            events.append(parsed)

    for event in reversed(events):
        if event.get("output") is not None or event.get("id") is not None:
            return event

    body_preview = response.text.strip()[:500]
    raise RuntimeError(
        body_preview or "Supervisor request returned an unreadable responses payload."
    )


class DatabricksRuntimeClient:
    """Owns Databricks auth, SQL statement execution, and Responses API calls."""

    @cached_property
    def settings(self):
        return get_settings()

    @cached_property
    def workspace_client(self):
        return get_workspace_client()

    @cached_property
    def api_host(self) -> str:
        host = normalize_host(self.workspace_client.config.host)
        if not host:
            raise RuntimeError("Unable to resolve Databricks workspace host.")
        return host.rstrip("/")

    def request_headers(self) -> dict[str, str]:
        headers = self.workspace_client.config.authenticate()
        headers["Content-Type"] = "application/json"
        headers["Accept"] = "application/json"
        return headers

    def app_request_headers(self) -> dict[str, str]:
        headers = self.request_headers()
        authorization = headers.get("Authorization") or headers.get("authorization")
        if authorization:
            headers.setdefault("X-Databricks-Authorization", authorization)
        return headers

    def execute_statement(self, statement: str) -> dict[str, Any]:
        response = requests.post(
            f"{self.api_host}/api/2.0/sql/statements/",
            headers=self.request_headers(),
            json={
                "statement": statement,
                "warehouse_id": self.settings.warehouse_id,
                "wait_timeout": "50s",
            },
            timeout=90,
        )
        response.raise_for_status()
        payload = response.json()
        state = payload.get("status", {}).get("state")
        if state != "SUCCEEDED":
            raise RuntimeError(payload.get("status", {}).get("error", {}).get("message", str(payload)))
        return payload

    def fetch_rows(self, statement: str) -> list[list[Any]]:
        payload = self.execute_statement(statement)
        return payload.get("result", {}).get("data_array", [])

    def fetch_json_scalar(self, statement: str) -> Any:
        rows = self.fetch_rows(statement)
        if not rows or not rows[0]:
            return None
        return maybe_json_load(rows[0][0])

    def current_runtime_config(self) -> dict[str, str]:
        fq = f"{self.settings.catalog}.{self.settings.schema}.runtime_config"
        try:
            rows = self.fetch_rows(f"SELECT config_key, config_value FROM {fq}")
        except Exception as exc:  # noqa: BLE001
            LOGGER.warning("Unable to load runtime_config: %s", exc)
            return {}

        return {
            str(row[0]): str(row[1])
            for row in rows
            if row and row[0] is not None and row[1] is not None
        }

    def resolve_supervisor_endpoint(self) -> str:
        runtime_config = self.current_runtime_config()
        endpoint = runtime_config.get("supervisor_endpoint") or self.settings.default_supervisor_endpoint
        if not endpoint:
            raise RuntimeError("Supervisor endpoint is not provisioned in runtime_config.")
        return endpoint

    def call_model_serving_responses_endpoint(self, endpoint: str, prompt: str) -> dict[str, Any]:
        request_body = {
            "model": endpoint,
            "stream": False,
            "input": [{"role": "user", "content": prompt}],
        }
        candidate_paths = [
            "/api/2.0/serving-endpoints/responses",
            "/serving-endpoints/responses",
        ]
        last_error: Exception | None = None
        for path in candidate_paths:
            for attempt in range(1, 4):
                try:
                    response = requests.post(
                        f"{self.api_host}{path}",
                        headers=self.request_headers(),
                        json=request_body,
                        timeout=(
                            self.settings.supervisor_connect_timeout_seconds,
                            self.settings.supervisor_read_timeout_seconds,
                        ),
                    )
                    response.raise_for_status()
                    payload = parse_responses_api_body(response)
                    if isinstance(payload, dict) and payload.get("error_code"):
                        raise RuntimeError(
                            f"{payload.get('error_code')}: {payload.get('message', 'Supervisor request failed.')}"
                        )
                    return payload
                except requests.HTTPError as exc:
                    status_code = exc.response.status_code if exc.response is not None else None
                    if status_code in {429, 502, 503, 504} and attempt < 3:
                        LOGGER.warning(
                            "Transient supervisor HTTP %s from %s on attempt %s/3; retrying.",
                            status_code,
                            path,
                            attempt,
                        )
                        time.sleep(min(2 ** (attempt - 1), 5))
                        continue
                    last_error = exc
                    if status_code in {400, 404, 405}:
                        break
                    raise RuntimeError(
                        f"Supervisor endpoint returned HTTP {status_code or 'unknown'}."
                    ) from exc
                except requests.Timeout as exc:
                    raise RuntimeError(
                        "Supervisor response exceeded the live request timeout "
                        f"({self.settings.supervisor_read_timeout_seconds}s)."
                    ) from exc
                except requests.RequestException as exc:
                    raise RuntimeError(f"Supervisor request failed: {exc}") from exc

        if last_error is not None:
            raise last_error
        raise RuntimeError("Supervisor request did not return a response.")

    def call_responses_endpoint(self, endpoint: str, prompt: str) -> dict[str, Any]:
        return self.call_model_serving_responses_endpoint(endpoint, prompt)

    def call_app_responses_endpoint(
        self,
        app_url: str,
        prompt: str,
        *,
        custom_agent_model_id: str | None = None,
    ) -> dict[str, Any]:
        normalized_url = normalize_host(app_url)
        if not normalized_url:
            raise RuntimeError("Custom agent app URL is not provisioned in runtime_config.")

        request_body = {
            "stream": False,
            "input": [{"role": "user", "content": prompt}],
        }
        if custom_agent_model_id:
            request_body["custom_inputs"] = {"model_id": custom_agent_model_id}
        url = f"{normalized_url.rstrip('/')}/responses"
        for attempt in range(1, 4):
            try:
                response = requests.post(
                    url,
                    headers=self.app_request_headers(),
                    json=request_body,
                    timeout=(
                        self.settings.supervisor_connect_timeout_seconds,
                        self.settings.supervisor_read_timeout_seconds,
                    ),
                )
                response.raise_for_status()
                payload = parse_responses_api_body(response)
                if isinstance(payload, dict) and payload.get("error_code"):
                    raise RuntimeError(
                        f"{payload.get('error_code')}: {payload.get('message', 'Custom agent request failed.')}"
                    )
                return payload
            except requests.HTTPError as exc:
                status_code = exc.response.status_code if exc.response is not None else None
                if status_code in {429, 502, 503, 504} and attempt < 3:
                    LOGGER.warning(
                        "Transient custom agent HTTP %s on attempt %s/3; retrying.",
                        status_code,
                        attempt,
                    )
                    time.sleep(min(2 ** (attempt - 1), 5))
                    continue
                raise RuntimeError(
                    f"Custom agent app returned HTTP {status_code or 'unknown'}."
                ) from exc
            except requests.Timeout as exc:
                raise RuntimeError(
                    "Custom agent response exceeded the live request timeout "
                    f"({self.settings.supervisor_read_timeout_seconds}s)."
                ) from exc
            except requests.RequestException as exc:
                raise RuntimeError(f"Custom agent request failed: {exc}") from exc

        raise RuntimeError("Custom agent request did not return a response.")
