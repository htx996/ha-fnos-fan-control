"""Resolve a physical fan across transient backend-specific identifiers."""

from collections.abc import Iterable


def infer_fan_channel(fan: dict | None) -> str | None:
    """Return the stable LLLED channel represented by a fan record."""
    if not fan:
        return None

    channel = str(fan.get("channel") or "").strip().lower()
    if channel in {"cpu", "sys", "sys2"}:
        return channel

    name = str(fan.get("name") or "").strip().lower()
    if "cpu" in name:
        return "cpu"
    if any(token in name for token in ("system 2", "system fan 2", "系统风扇 2")):
        return "sys2"
    if any(token in name for token in ("system", "系统", "chassis", "机箱")):
        return "sys"

    chip = str(fan.get("chip") or "").strip().lower()
    try:
        index = int(fan.get("index"))
    except (TypeError, ValueError):
        index = None

    if chip == "it8613":
        if index == 2:
            return "cpu"
        if index == 3:
            return "sys"
    return None


def stable_fan_id(fan: dict) -> str:
    """Return a backend-independent ID for known physical fan channels."""
    channel = infer_fan_channel(fan)
    chip = str(fan.get("chip") or "").strip().lower()
    if channel and (
        fan.get("channel") or fan.get("backend") == "llled" or chip == "it8613"
    ):
        return f"channel_{channel}"
    return str(fan["id"])


def resolve_fan_record(
    fans: Iterable[dict],
    fan_id: str,
    fan_channel: str | None,
) -> dict | None:
    """Find a fan by exact ID, then by one unambiguous physical channel."""
    records = list(fans)
    for fan in records:
        if fan.get("id") == fan_id:
            return fan

    if not fan_channel:
        return None

    matches = [fan for fan in records if infer_fan_channel(fan) == fan_channel]
    return matches[0] if len(matches) == 1 else None
