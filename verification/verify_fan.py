import importlib.util
import sys
import types
from verification.support import VerifyCase
from enum import IntFlag
from pathlib import Path
from verification.support import patch_modules


FAN_PATH = (
    Path(__file__).resolve().parents[1]
    / "custom_components"
    / "fn_nas"
    / "fan.py"
)


class StubFanEntityFeature(IntFlag):
    SET_SPEED = 1
    PRESET_MODE = 2


class StubCoordinatorEntity:
    def __init__(self, coordinator):
        self.coordinator = coordinator

    @property
    def available(self):
        return True


class StubFanEntity:
    pass


def _install_stubs():
    homeassistant = types.ModuleType("homeassistant")
    components = types.ModuleType("homeassistant.components")
    fan_component = types.ModuleType("homeassistant.components.fan")
    fan_component.FanEntity = StubFanEntity
    fan_component.FanEntityFeature = StubFanEntityFeature
    helpers = types.ModuleType("homeassistant.helpers")
    update_coordinator = types.ModuleType("homeassistant.helpers.update_coordinator")
    update_coordinator.CoordinatorEntity = StubCoordinatorEntity

    custom_components = types.ModuleType("custom_components")
    fn_nas = types.ModuleType("custom_components.fn_nas")
    fn_nas.__path__ = [str(FAN_PATH.parent)]
    const = types.ModuleType("custom_components.fn_nas.const")
    const.DATA_UPDATE_COORDINATOR = "coordinator"
    const.DEVICE_ID_NAS = "flynas_nas_system"
    const.DOMAIN = "fn_nas"
    fan_manager = types.ModuleType("custom_components.fn_nas.fan_manager")
    fan_manager.CONTROL_MODE_AUTO = "自动"
    fan_manager.CONTROL_MODE_FULL_SPEED = "全速"

    return {
        "homeassistant": homeassistant,
        "homeassistant.components": components,
        "homeassistant.components.fan": fan_component,
        "homeassistant.helpers": helpers,
        "homeassistant.helpers.update_coordinator": update_coordinator,
        "custom_components": custom_components,
        "custom_components.fn_nas": fn_nas,
        "custom_components.fn_nas.const": const,
        "custom_components.fn_nas.fan_manager": fan_manager,
    }


class FanEntityIdentityVerifications(VerifyCase):
    def verify_control_entity_follows_channel_when_hwmon_id_disappears(self):
        with patch_modules(sys.modules, _install_stubs()):
            spec = importlib.util.spec_from_file_location(
                "custom_components.fn_nas.fan",
                FAN_PATH,
            )
            module = importlib.util.module_from_spec(spec)
            sys.modules["custom_components.fn_nas.fan"] = module
            spec.loader.exec_module(module)

            coordinator = types.SimpleNamespace(
                data={
                    "fans": [
                        {
                            "id": "llled_sys",
                            "name": "系统风扇",
                            "channel": "sys",
                            "backend": "llled",
                            "rpm": 1053,
                            "pwm_percent": 50,
                            "supports_pwm": True,
                        }
                    ]
                }
            )
            entity = module.FlynasFanEntity(
                coordinator,
                {
                    "id": "it8613_fan3_a1b2c3d4",
                    "name": "系统风扇",
                    "chip": "it8613",
                    "index": 3,
                },
                "entry-1",
            )

        self.assertTrue(entity.available)
        self.assertEqual(entity._attr_unique_id, "entry-1_fan_channel_sys")
        self.assertEqual(entity.percentage, 50)
        self.assertEqual(entity.extra_state_attributes["控制后端"], "llled")
        self.assertEqual(
            entity.extra_state_attributes["fn_nas_dashboard_category"],
            "control",
        )
