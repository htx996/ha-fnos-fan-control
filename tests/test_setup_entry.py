import importlib.util
import sys
import types
import unittest
from pathlib import Path
from unittest.mock import patch


INIT_PATH = (
    Path(__file__).resolve().parents[1]
    / "custom_components"
    / "fn_nas"
    / "__init__.py"
)


class StubFlynasCoordinator:
    def __init__(self, hass, config, entry):
        self.hass = hass
        self.config = config
        self.config_entry = entry
        self.refresh_count = 0
        self.disconnect_count = 0

    async def async_config_entry_first_refresh(self):
        self.refresh_count += 1

    async def async_disconnect(self):
        self.disconnect_count += 1


class StubUPSDataUpdateCoordinator:
    def __init__(self, hass, config, coordinator):
        self.hass = hass
        self.config = config
        self.main_coordinator = coordinator
        self.refresh_count = 0

    async def async_config_entry_first_refresh(self):
        self.refresh_count += 1


class StubConfigEntries:
    def __init__(self):
        self.forward_calls = []

    async def async_forward_entry_setups(self, entry, platforms):
        self.forward_calls.append((entry, platforms))


class StubHass:
    def __init__(self):
        self.data = {}
        self.config_entries = StubConfigEntries()
        self.created_tasks = []

    def async_create_task(self, coroutine):
        self.created_tasks.append(coroutine)
        coroutine.close()


class StubEntry:
    entry_id = "entry-1"
    data = {
        "host": "192.168.2.86",
        "username": "root",
        "password": "password",
    }
    options = {}

    def __init__(self):
        self.update_listeners = []

    def add_update_listener(self, listener):
        self.update_listeners.append(listener)
        return "remove-listener"

    def async_on_unload(self, unload_callback):
        self.unload_callback = unload_callback


def _install_stubs():
    asyncssh = types.ModuleType("asyncssh")

    homeassistant = types.ModuleType("homeassistant")
    config_entries = types.ModuleType("homeassistant.config_entries")
    config_entries.ConfigEntry = object
    core = types.ModuleType("homeassistant.core")
    core.HomeAssistant = object
    helpers = types.ModuleType("homeassistant.helpers")
    helpers_config_validation = types.ModuleType("homeassistant.helpers.config_validation")
    helpers.config_validation = helpers_config_validation

    custom_components = types.ModuleType("custom_components")
    fn_nas = types.ModuleType("custom_components.fn_nas")
    fn_nas.__path__ = []

    const_module = types.ModuleType("custom_components.fn_nas.const")
    const_module.DOMAIN = "fn_nas"
    const_module.DATA_UPDATE_COORDINATOR = "coordinator"
    const_module.PLATFORMS = ["sensor", "fan", "select", "switch", "button"]
    const_module.CONF_ENABLE_DOCKER = "enable_docker"
    const_module.CONF_HOST = "host"
    const_module.DEFAULT_PORT = 22

    coordinator_module = types.ModuleType("custom_components.fn_nas.coordinator")
    coordinator_module.FlynasCoordinator = StubFlynasCoordinator
    coordinator_module.UPSDataUpdateCoordinator = StubUPSDataUpdateCoordinator

    return {
        "asyncssh": asyncssh,
        "homeassistant": homeassistant,
        "homeassistant.config_entries": config_entries,
        "homeassistant.core": core,
        "homeassistant.helpers": helpers,
        "homeassistant.helpers.config_validation": helpers_config_validation,
        "custom_components": custom_components,
        "custom_components.fn_nas": fn_nas,
        "custom_components.fn_nas.const": const_module,
        "custom_components.fn_nas.coordinator": coordinator_module,
    }


class SetupEntryTests(unittest.IsolatedAsyncioTestCase):
    async def test_setup_entry_forwards_platforms_before_returning(self):
        with patch.dict(sys.modules, _install_stubs()):
            spec = importlib.util.spec_from_file_location(
                "custom_components.fn_nas",
                INIT_PATH,
            )
            module = importlib.util.module_from_spec(spec)
            sys.modules["custom_components.fn_nas"] = module
            spec.loader.exec_module(module)

            hass = StubHass()
            entry = StubEntry()

            self.assertTrue(await module.async_setup_entry(hass, entry))

        self.assertEqual(
            hass.config_entries.forward_calls,
            [(entry, ["sensor", "fan", "select", "switch", "button"])],
        )
        self.assertFalse(hass.created_tasks)
        self.assertIsNotNone(hass.data["fn_nas"][entry.entry_id]["ups_coordinator"])


if __name__ == "__main__":
    unittest.main()
