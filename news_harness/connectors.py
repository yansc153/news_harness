"""Connector-boundary checks for fixture preflight."""

from __future__ import annotations

from typing import Any

from .constants import REQUIRED_CONNECTOR_PERMISSIONS


def check_connector_runtime_boundary(config: dict[str, Any]) -> tuple[bool, dict[str, Any]]:
    boundary = config.get("connector_boundary", {})
    permissions = set(boundary.get("required_permissions", []))
    missing_permissions = sorted(REQUIRED_CONNECTOR_PERMISSIONS - permissions)
    pass_status = (
        boundary.get("connector_runtime_status") == "fixture_only"
        and boundary.get("real_connectors_enabled") is False
        and boundary.get("upstream_tools_execution_allowed") is False
        and boundary.get("network_fetch_allowed") is False
        and boundary.get("browser_automation_allowed") is False
        and not missing_permissions
    )
    return pass_status, {
        "connector_runtime_status": boundary.get("connector_runtime_status"),
        "real_connectors_enabled": boundary.get("real_connectors_enabled"),
        "upstream_tools_execution_allowed": boundary.get("upstream_tools_execution_allowed"),
        "network_fetch_allowed": boundary.get("network_fetch_allowed"),
        "browser_automation_allowed": boundary.get("browser_automation_allowed"),
        "missing_permissions": missing_permissions,
    }
