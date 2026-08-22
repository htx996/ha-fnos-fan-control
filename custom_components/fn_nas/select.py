import logging

from homeassistant.components.select import SelectEntity
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import DATA_UPDATE_COORDINATOR, DEVICE_ID_NAS, DOMAIN
from .fan_manager import (
    CONTROL_MODE_AUTO,
    CONTROL_MODE_FULL_SPEED,
    CONTROL_MODE_MANUAL,
)

_LOGGER = logging.getLogger(__name__)

FAN_MODE_OPTIONS = [
    CONTROL_MODE_AUTO,
    CONTROL_MODE_MANUAL,
    CONTROL_MODE_FULL_SPEED,
]


async def async_setup_entry(hass, config_entry, async_add_entities):
    domain_data = hass.data[DOMAIN][config_entry.entry_id]
    coordinator = domain_data[DATA_UPDATE_COORDINATOR]

    entities = [
        FanModeSelect(coordinator, fan, config_entry.entry_id)
        for fan in coordinator.data.get("fans", [])
        if fan.get("supports_modes")
    ]

    async_add_entities(entities)


class FanModeSelect(CoordinatorEntity, SelectEntity):
    """Select entity for hwmon pwmN_enable control modes."""

    def __init__(self, coordinator, fan_info: dict, entry_id: str):
        super().__init__(coordinator)
        self.fan_id = fan_info["id"]
        self._attr_name = f"{fan_info['name']} 模式"
        self._attr_unique_id = f"{entry_id}_fan_{self.fan_id}_mode"
        self._attr_icon = "mdi:tune"
        self._attr_options = fan_info.get("available_modes", FAN_MODE_OPTIONS)
        self._attr_device_info = {
            "identifiers": {(DOMAIN, DEVICE_ID_NAS)},
            "name": "飞牛NAS系统监控",
            "manufacturer": "飞牛",
            "model": "飞牛NAS",
        }

    @property
    def _fan_data(self) -> dict | None:
        for fan in self.coordinator.data.get("fans", []):
            if fan.get("id") == self.fan_id:
                return fan
        return None

    @property
    def available(self) -> bool:
        return super().available and self._fan_data is not None

    @property
    def current_option(self) -> str | None:
        fan = self._fan_data
        if not fan:
            return None

        mode = fan.get("control_mode")
        if mode in self.options:
            return mode
        return None

    async def async_select_option(self, option: str) -> None:
        fan = self._fan_data
        if not fan or option not in self.options:
            return

        success = await self.coordinator.fan_manager.set_mode(fan, option)
        if not success:
            _LOGGER.warning("设置风扇 %s 模式失败: %s", fan.get("name", self.fan_id), option)
            return

        self.async_write_ha_state()
        await self.coordinator.async_request_refresh()

    @property
    def extra_state_attributes(self):
        fan = self._fan_data or {}
        return {
            "风扇ID": self.fan_id,
            "PWM enable": fan.get("pwm_enable"),
            "PWM控制支持": fan.get("supports_pwm", False),
            "模式控制支持": fan.get("supports_modes", False),
            "自动模式支持": fan.get("supports_auto_mode", False),
            "可用模式": fan.get("available_modes", []),
            "最近控制结果": fan.get("last_control_result"),
            "hwmon路径": fan.get("hwmon_path"),
            "芯片": fan.get("chip"),
        }
