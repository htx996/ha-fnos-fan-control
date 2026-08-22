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

    fan_control = coordinator.data.get("fan_control", {})
    if fan_control.get("backend") == "llled" and fan_control.get("supports_modes"):
        entities.append(LLLEDFanModeSelect(coordinator, config_entry.entry_id))

    async_add_entities(entities)


class LLLEDFanModeSelect(CoordinatorEntity, SelectEntity):
    """Global mode selector for the single LLLED temperature controller."""

    def __init__(self, coordinator, entry_id: str):
        super().__init__(coordinator)
        self._attr_name = "风扇控制模式"
        self._attr_unique_id = f"{entry_id}_llled_fan_control_mode"
        self._attr_icon = "mdi:fan-auto"
        self._attr_options = coordinator.data["fan_control"].get(
            "available_modes", FAN_MODE_OPTIONS
        )
        self._attr_device_info = {
            "identifiers": {(DOMAIN, DEVICE_ID_NAS)},
            "name": "飞牛NAS系统监控",
            "manufacturer": "飞牛",
            "model": "飞牛NAS",
        }

    @property
    def _control_data(self) -> dict:
        return self.coordinator.data.get("fan_control", {})

    @property
    def available(self) -> bool:
        return super().available and self._control_data.get("available", False)

    @property
    def current_option(self) -> str | None:
        mode = self._control_data.get("mode")
        return mode if mode in self.options else None

    async def async_select_option(self, option: str) -> None:
        if option not in self.options:
            return
        success = await self.coordinator.fan_manager.set_global_mode(option)
        if not success:
            _LOGGER.warning("设置 LLLED 全局风扇模式失败: %s", option)
            return
        self.async_write_ha_state()
        await self.coordinator.async_request_refresh()

    @property
    def extra_state_attributes(self):
        control = self._control_data
        return {
            "控制后端": "LLLED",
            "温控守护进程": "运行中" if control.get("curve_running") else "已停止",
            "原厂曲线": control.get("stock_profile"),
            "CPU温度": control.get("cpu_temperature"),
            "硬盘最高温度": control.get("hdd_temperature"),
            "NVMe最高温度": control.get("ssd_temperature"),
            "CPU目标PWM": control.get("desired_cpu_pwm"),
            "系统风扇目标PWM": control.get("desired_system_pwm"),
            "错误": control.get("error"),
        }


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
            "控制后端": fan.get("backend", "hwmon"),
            "PWM enable": fan.get("pwm_enable"),
            "PWM控制支持": fan.get("supports_pwm", False),
            "模式控制支持": fan.get("supports_modes", False),
            "自动模式支持": fan.get("supports_auto_mode", False),
            "可用模式": fan.get("available_modes", []),
            "最近控制结果": fan.get("last_control_result"),
            "hwmon路径": fan.get("hwmon_path"),
            "芯片": fan.get("chip"),
        }
