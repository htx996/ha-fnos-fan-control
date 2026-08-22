import importlib.util
import sys
import types
import unittest
from pathlib import Path
from unittest.mock import patch


SELECT_PATH = (
    Path(__file__).resolve().parents[1]
    / "custom_components"
    / "fn_nas"
    / "select.py"
)


class StubCoordinatorEntity:
    def __init__(self, coordinator):
        self.coordinator = coordinator

    def async_write_ha_state(self):
        self.wrote_state = True


class StubSelectEntity:
    @property
    def options(self):
        return self._attr_options


class StubFanManager:
    def __init__(self):
        self.mode_calls = []

    async def set_global_mode(self, mode):
        self.mode_calls.append(mode)
        return True


class StubCoordinator:
    def __init__(self):
        self.data = {
            "fans": [
                {
                    "id": "llled_cpu",
                    "name": "CPU 风扇",
                    "backend": "llled",
                    "supports_modes": False,
                }
            ],
            "fan_control": {
                "backend": "llled",
                "supports_modes": True,
                "mode": "手动",
                "available_modes": ["自动", "手动", "全速"],
                "curve_running": False,
                "stock_profile": "stock-4800plus",
            },
        }
        self.fan_manager = StubFanManager()
        self.refresh_count = 0

    async def async_request_refresh(self):
        self.refresh_count += 1


def _install_stubs():
    homeassistant = types.ModuleType("homeassistant")
    components = types.ModuleType("homeassistant.components")
    select_component = types.ModuleType("homeassistant.components.select")
    select_component.SelectEntity = StubSelectEntity
    helpers = types.ModuleType("homeassistant.helpers")
    update_coordinator = types.ModuleType("homeassistant.helpers.update_coordinator")
    update_coordinator.CoordinatorEntity = StubCoordinatorEntity

    custom_components = types.ModuleType("custom_components")
    fn_nas = types.ModuleType("custom_components.fn_nas")
    fn_nas.__path__ = [str(SELECT_PATH.parent)]
    const = types.ModuleType("custom_components.fn_nas.const")
    const.DATA_UPDATE_COORDINATOR = "coordinator"
    const.DEVICE_ID_NAS = "flynas_nas_system"
    const.DOMAIN = "fn_nas"
    fan_manager = types.ModuleType("custom_components.fn_nas.fan_manager")
    fan_manager.CONTROL_MODE_AUTO = "自动"
    fan_manager.CONTROL_MODE_MANUAL = "手动"
    fan_manager.CONTROL_MODE_FULL_SPEED = "全速"

    return {
        "homeassistant": homeassistant,
        "homeassistant.components": components,
        "homeassistant.components.select": select_component,
        "homeassistant.helpers": helpers,
        "homeassistant.helpers.update_coordinator": update_coordinator,
        "custom_components": custom_components,
        "custom_components.fn_nas": fn_nas,
        "custom_components.fn_nas.const": const,
        "custom_components.fn_nas.fan_manager": fan_manager,
    }


class FanModeSelectTests(unittest.IsolatedAsyncioTestCase):
    async def test_setup_adds_one_global_llled_mode_entity(self):
        with patch.dict(sys.modules, _install_stubs()):
            spec = importlib.util.spec_from_file_location(
                "custom_components.fn_nas.select", SELECT_PATH
            )
            module = importlib.util.module_from_spec(spec)
            sys.modules["custom_components.fn_nas.select"] = module
            spec.loader.exec_module(module)

            coordinator = StubCoordinator()
            hass = types.SimpleNamespace(
                data={"fn_nas": {"entry-1": {"coordinator": coordinator}}}
            )
            entry = types.SimpleNamespace(entry_id="entry-1")
            entities = []

            await module.async_setup_entry(hass, entry, entities.extend)

            self.assertEqual(len(entities), 1)
            entity = entities[0]
            self.assertIsInstance(entity, module.LLLEDFanModeSelect)
            self.assertEqual(entity.current_option, "手动")
            self.assertEqual(entity._attr_unique_id, "entry-1_llled_fan_control_mode")

            await entity.async_select_option("自动")

        self.assertEqual(coordinator.fan_manager.mode_calls, ["自动"])
        self.assertEqual(coordinator.refresh_count, 1)


if __name__ == "__main__":
    unittest.main()
