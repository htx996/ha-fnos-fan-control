"""Shared metadata used by the optional Lovelace dashboard."""

DASHBOARD_CATEGORY = "fn_nas_dashboard_category"
DASHBOARD_ROLE = "fn_nas_dashboard_role"
DASHBOARD_ORDER = "fn_nas_dashboard_order"


def dashboard_metadata(category: str, role: str, order: int = 100) -> dict:
    """Return namespaced attributes used by auto-entities filters."""
    return {
        DASHBOARD_CATEGORY: category,
        DASHBOARD_ROLE: role,
        DASHBOARD_ORDER: order,
    }
