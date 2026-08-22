import logging
import asyncio
import asyncssh
import re
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers import config_validation as cv
from homeassistant.helpers import entity_registry as er

from .const import (
    DOMAIN, DATA_UPDATE_COORDINATOR, PLATFORMS, CONF_ENABLE_DOCKER, 
    CONF_HOST, DEFAULT_PORT
)
from .coordinator import FlynasCoordinator, UPSDataUpdateCoordinator
from .frontend import async_install_dashboard_assets
from .fan_identity import infer_fan_channel, stable_fan_id

_LOGGER = logging.getLogger(__name__)

_FAN_ENTITY_SUFFIXES = ("_mode_sensor", "_rpm", "_pwm", "_mode")


def _split_fan_unique_id(entry_id: str, unique_id: str) -> tuple[str, str] | None:
    prefix = f"{entry_id}_fan_"
    if not unique_id.startswith(prefix):
        return None

    fan_key = unique_id[len(prefix):]
    for suffix in _FAN_ENTITY_SUFFIXES:
        if fan_key.endswith(suffix):
            return fan_key[: -len(suffix)], suffix
    return fan_key, ""


def _registry_fan_channel(fan_key: str, current_channels: dict[str, str]) -> str | None:
    if fan_key in current_channels:
        return current_channels[fan_key]

    channel_match = re.fullmatch(r"(?:channel|llled)_(cpu|sys|sys2)", fan_key)
    if channel_match:
        return channel_match.group(1)

    it8613_match = re.fullmatch(r"it8613_fan([23])_.+", fan_key)
    if it8613_match:
        return "cpu" if it8613_match.group(1) == "2" else "sys"
    return None


def _fan_unique_id(entry_id: str, fan_key: str, suffix: str) -> str:
    return f"{entry_id}_fan_{fan_key}{suffix}"


def _migrate_fan_entity_registry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    fans: list[dict],
) -> None:
    """Migrate LLLED/hwmon duplicates to one stable physical-channel ID."""
    current_by_channel = {
        channel: fan
        for fan in fans
        if (channel := infer_fan_channel(fan))
        and stable_fan_id(fan).startswith("channel_")
    }
    if not current_by_channel:
        return

    current_channels = {
        str(fan.get("id")): channel for channel, fan in current_by_channel.items()
    }
    registry = er.async_get(hass)
    grouped_entries: dict[tuple[str, str], list[tuple[str, object]]] = {}

    for entity_id, registry_entry in list(registry.entities.items()):
        if (
            registry_entry.config_entry_id != entry.entry_id
            or registry_entry.platform != DOMAIN
        ):
            continue

        parsed = _split_fan_unique_id(entry.entry_id, registry_entry.unique_id)
        if parsed is None:
            continue
        fan_key, suffix = parsed
        channel = _registry_fan_channel(fan_key, current_channels)
        if channel not in current_by_channel:
            continue

        expected_domain = (
            "fan" if not suffix else "select" if suffix == "_mode" else "sensor"
        )
        if not entity_id.startswith(f"{expected_domain}."):
            continue
        grouped_entries.setdefault((channel, suffix), []).append(
            (entity_id, registry_entry)
        )

    removed = []
    migrated = []
    for (channel, suffix), entries in grouped_entries.items():
        canonical_key = stable_fan_id(current_by_channel[channel])
        canonical_unique_id = _fan_unique_id(entry.entry_id, canonical_key, suffix)
        winner = next(
            (item for item in entries if item[1].unique_id == canonical_unique_id),
            None,
        )
        if winner is None:
            dated_entries = [
                item
                for item in entries
                if getattr(item[1], "created_at", None) is not None
            ]
            winner = (
                min(dated_entries, key=lambda item: item[1].created_at)
                if dated_entries
                else entries[0]
            )

        for entity_id, _registry_entry in entries:
            if entity_id == winner[0]:
                continue
            registry.async_remove(entity_id)
            removed.append(entity_id)

        if winner[1].unique_id != canonical_unique_id:
            registry.async_update_entity(winner[0], new_unique_id=canonical_unique_id)
            migrated.append(winner[0])

    if migrated:
        _LOGGER.info("已迁移风扇实体到稳定通道标识: %s", ", ".join(migrated))
    if removed:
        _LOGGER.info("已清理重复的旧风扇实体: %s", ", ".join(removed))


def _remove_dxp4800pro_ghost_fan_entities(
    hass: HomeAssistant,
    entry: ConfigEntry,
    diagnostics: dict,
) -> None:
    """Remove IT8613 channels which are not wired on the DXP4800 Pro."""
    hardware = diagnostics.get("host_hardware", {})
    if (
        hardware.get("sys_vendor", "").upper() != "UGREEN"
        or hardware.get("product_name") != "DXP4800 Pro"
    ):
        return

    ghost_unique_id = re.compile(
        rf"^{re.escape(entry.entry_id)}_fan_it8613_fan(?:1|4|5)_"
    )
    registry = er.async_get(hass)
    removed = []
    for entity_id, registry_entry in list(registry.entities.items()):
        if (
            registry_entry.config_entry_id != entry.entry_id
            or registry_entry.platform != DOMAIN
            or not ghost_unique_id.match(registry_entry.unique_id)
        ):
            continue
        registry.async_remove(entity_id)
        removed.append(entity_id)

    if removed:
        _LOGGER.info("已清理 DXP4800 Pro 未接线风扇实体: %s", ", ".join(removed))


def _remove_superseded_llled_mode_entities(
    hass: HomeAssistant,
    entry: ConfigEntry,
    fan_control: dict,
) -> None:
    """Remove per-fan mode selects replaced by LLLED's global controller."""
    if fan_control.get("backend") != "llled" or not fan_control.get("available"):
        return

    old_mode_unique_id = re.compile(
        rf"^{re.escape(entry.entry_id)}_fan_.+_mode$"
    )
    registry = er.async_get(hass)
    removed = []
    for entity_id, registry_entry in list(registry.entities.items()):
        if (
            not entity_id.startswith("select.")
            or registry_entry.config_entry_id != entry.entry_id
            or registry_entry.platform != DOMAIN
            or not old_mode_unique_id.match(registry_entry.unique_id)
        ):
            continue
        registry.async_remove(entity_id)
        removed.append(entity_id)

    if removed:
        _LOGGER.info("已清理由 LLLED 全局模式替代的旧风扇模式实体: %s", ", ".join(removed))

async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry):
    config = {**entry.data, **entry.options}
    coordinator = FlynasCoordinator(hass, config, entry)
    ups_coordinator = UPSDataUpdateCoordinator(hass, coordinator.config, coordinator)
    # 直接初始化，不阻塞等待NAS上线
    hass.data.setdefault(DOMAIN, {})
    hass.data[DOMAIN][entry.entry_id] = {
        DATA_UPDATE_COORDINATOR: coordinator,
        "ups_coordinator": ups_coordinator,
        CONF_ENABLE_DOCKER: coordinator.config.get(CONF_ENABLE_DOCKER, False)
    }

    try:
        copied_assets = await async_install_dashboard_assets(hass)
        if copied_assets:
            _LOGGER.info("已安装飞牛NAS仪表盘资源: %s", ", ".join(copied_assets))
    except (OSError, AttributeError) as error:
        # Dashboard resources are optional and must never block entity setup.
        _LOGGER.warning("飞牛NAS仪表盘资源安装失败，不影响集成运行: %s", error)

    try:
        await coordinator.async_config_entry_first_refresh()
    except Exception as e:
        # 即使首次采集失败，也继续注册基础实体，避免HA里只出现空条目。
        _LOGGER.warning("飞牛NAS首次数据刷新失败，将使用默认数据加载实体: %s", str(e))

    enable_docker = coordinator.config.get(CONF_ENABLE_DOCKER, False)
    if enable_docker:
        from .docker_manager import DockerManager
        coordinator.docker_manager = DockerManager(coordinator)
        _LOGGER.debug("已启用Docker容器监控")
    else:
        coordinator.docker_manager = None
        _LOGGER.debug("未启用Docker容器监控")

    try:
        await ups_coordinator.async_config_entry_first_refresh()
    except Exception as e:
        _LOGGER.debug("UPS首次数据刷新失败，将跳过UPS实体: %s", str(e))

    _remove_dxp4800pro_ghost_fan_entities(
        hass,
        entry,
        coordinator.data.get("fan_diagnostics", {}),
    )
    _migrate_fan_entity_registry(
        hass,
        entry,
        coordinator.data.get("fans", []),
    )
    _remove_superseded_llled_mode_entities(
        hass,
        entry,
        coordinator.data.get("fan_control", {}),
    )

    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    entry.async_on_unload(entry.add_update_listener(async_update_entry))
    _LOGGER.info("飞牛NAS集成初始化完成")
    return True

async def async_update_entry(hass: HomeAssistant, entry: ConfigEntry):
    """更新配置项"""
    # 卸载现有集成
    await async_unload_entry(hass, entry)
    # 重新加载集成
    await async_setup_entry(hass, entry)

async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry):
    """卸载集成"""
    # 获取集成数据
    domain_data = hass.data.get(DOMAIN, {}).get(entry.entry_id, {})
    unload_ok = True
    
    if DATA_UPDATE_COORDINATOR in domain_data:
        coordinator = domain_data[DATA_UPDATE_COORDINATOR]
        ups_coordinator = domain_data.get("ups_coordinator")
        
        # 卸载平台
        unload_ok = await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
        
        if unload_ok:
            # 关闭主协调器的SSH连接
            await coordinator.async_disconnect()
            
            # 关闭UPS协调器（如果存在）
            if ups_coordinator:
                await ups_coordinator.async_shutdown()
            
            # 取消监控任务（如果存在）
            if hasattr(coordinator, '_ping_task') and coordinator._ping_task and not coordinator._ping_task.done():
                coordinator._ping_task.cancel()
                
            # 从DOMAIN中移除该entry的数据
            hass.data[DOMAIN].pop(entry.entry_id, None)
    
    return unload_ok
