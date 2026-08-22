"""Install static files used by the optional Lovelace dashboard."""

from pathlib import Path
import shutil


DASHBOARD_ASSET_FILES = ("fn_nas.png", "istoreos.png")


def copy_dashboard_assets(source: Path, target: Path) -> list[str]:
    """Copy changed dashboard assets from a fixed allowlist."""
    copied = []
    target.mkdir(parents=True, exist_ok=True)

    for filename in DASHBOARD_ASSET_FILES:
        source_file = source / filename
        target_file = target / filename
        if not source_file.is_file():
            continue
        if target_file.is_file() and target_file.read_bytes() == source_file.read_bytes():
            continue
        shutil.copy2(source_file, target_file)
        copied.append(filename)

    return copied


async def async_install_dashboard_assets(hass) -> list[str]:
    """Install dashboard assets without blocking Home Assistant's event loop."""
    source = Path(hass.config.path("custom_components", "fn_nas", "frontend"))
    target = Path(hass.config.path("www", "community", "fn_nas"))
    return await hass.async_add_executor_job(copy_dashboard_assets, source, target)
