import logging
from typing import Any

from homeassistant.components.fan import FanEntity, FanEntityFeature
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import DATA_UPDATE_COORDINATOR, DEVICE_ID_NAS, DOMAIN
from .fan_identity import infer_fan_channel, resolve_fan_record, stable_fan_id
from .fan_manager import (
    CONTROL_MODE_AUTO,
    CONTROL_MODE_FULL_SPEED,
)

_LOGGER = logging.getLogger(__name__)

PRESET_MODES = [CONTROL_MODE_AUTO, CONTROL_MODE_FULL_SPEED]


def _preset_modes_for_fan(fan: dict) -> list[str]:
    return fan.get("available_modes", PRESET_MODES)


async def async_setup_entry(hass, config_entry, async_add_entities):
    domain_data = hass.data[DOMAIN][config_entry.entry_id]
    coordinator = domain_data[DATA_UPDATE_COORDINATOR]

    entities = [
        FlynasFanEntity(coordinator, fan, config_entry.entry_id)
        for fan in coordinator.data.get("fans", [])
        if fan.get("supports_pwm")
    ]

    async_add_entities(entities)


class FlynasFanEntity(CoordinatorEntity, FanEntity):
    """Home Assistant native fan entity backed by hwmon PWM."""

    def __init__(self, coordinator, fan_info: dict, entry_id: str):
        super().__init__(coordinator)
        self.fan_id = fan_info["id"]
        self.fan_channel = infer_fan_channel(fan_info)
        self.entity_fan_id = stable_fan_id(fan_info)
        self._attr_name = f"{fan_info['name']} 控制"
        self._attr_unique_id = f"{entry_id}_fan_{self.entity_fan_id}"
        self._attr_icon = "mdi:fan"
        self._attr_device_info = {
            "identifiers": {(DOMAIN, DEVICE_ID_NAS)},
            "name": "飞牛NAS系统监控",
            "manufacturer": "飞牛",
            "model": "飞牛NAS",
        }

    @property
    def _fan_data(self) -> dict | None:
        return resolve_fan_record(
            self.coordinator.data.get("fans", []),
            self.fan_id,
            self.fan_channel,
        )

    @property
    def available(self) -> bool:
        return super().available and self._fan_data is not None

    @property
    def supported_features(self) -> FanEntityFeature:
        fan = self._fan_data or {}
        features = FanEntityFeature.SET_SPEED
        if fan.get("supports_modes") and _preset_modes_for_fan(fan):
            features |= FanEntityFeature.PRESET_MODE
        return features

    @property
    def percentage(self) -> int | None:
        fan = self._fan_data
        if not fan:
            return None
        return fan.get("pwm_percent")

    @property
    def speed_count(self) -> int:
        return 100

    @property
    def is_on(self) -> bool | None:
        fan = self._fan_data
        if not fan:
            return None

        pwm_percent = fan.get("pwm_percent")
        if pwm_percent is not None:
            return pwm_percent > 0

        rpm = fan.get("rpm")
        if rpm is not None:
            return rpm > 0

        return None

    @property
    def preset_modes(self) -> list[str] | None:
        fan = self._fan_data
        if fan and fan.get("supports_modes"):
            return _preset_modes_for_fan(fan)
        return None

    @property
    def preset_mode(self) -> str | None:
        fan = self._fan_data
        if not fan:
            return None

        mode = fan.get("control_mode")
        if mode in _preset_modes_for_fan(fan):
            return mode
        return None

    async def async_set_percentage(self, percentage: int) -> None:
        fan = self._fan_data
        if not fan:
            return

        success = await self.coordinator.fan_manager.set_percentage(fan, percentage)
        if not success:
            _LOGGER.warning("设置风扇 %s 百分比失败", fan.get("name", self.fan_id))
            return

        self.async_write_ha_state()
        await self.coordinator.async_request_refresh()

    async def async_turn_on(
        self,
        percentage: int | None = None,
        preset_mode: str | None = None,
        **kwargs: Any,
    ) -> None:
        if preset_mode:
            await self.async_set_preset_mode(preset_mode)
            return

        target_percentage = percentage if percentage is not None else self.percentage
        if not target_percentage:
            target_percentage = 100

        await self.async_set_percentage(target_percentage)

    async def async_turn_off(self, **kwargs: Any) -> None:
        await self.async_set_percentage(0)

    async def async_set_preset_mode(self, preset_mode: str) -> None:
        fan = self._fan_data
        if not fan or preset_mode not in _preset_modes_for_fan(fan):
            raise ValueError(f"Unsupported preset mode: {preset_mode}")

        success = await self.coordinator.fan_manager.set_mode(fan, preset_mode)
        if not success:
            _LOGGER.warning("设置风扇 %s 模式失败: %s", fan.get("name", self.fan_id), preset_mode)
            return

        self.async_write_ha_state()
        await self.coordinator.async_request_refresh()

    @property
    def extra_state_attributes(self):
        fan = self._fan_data or {}
        return {
            "风扇ID": self.fan_id,
            "当前风扇ID": fan.get("id"),
            "控制后端": fan.get("backend", "hwmon"),
            "LLLED通道": infer_fan_channel(fan) or self.fan_channel,
            "转速": fan.get("rpm"),
            "PWM原始值": fan.get("pwm_raw"),
            "控制模式": fan.get("control_mode"),
            "PWM控制支持": fan.get("supports_pwm", False),
            "模式控制支持": fan.get("supports_modes", False),
            "自动模式支持": fan.get("supports_auto_mode", False),
            "可用模式": fan.get("available_modes", []),
            "最低安全PWM": fan.get("minimum_pwm_percent"),
            "退出全速PWM": fan.get("manual_recovery_percent"),
            "最近控制结果": fan.get("last_control_result"),
            "hwmon路径": fan.get("hwmon_path"),
            "芯片": fan.get("chip"),
        }
