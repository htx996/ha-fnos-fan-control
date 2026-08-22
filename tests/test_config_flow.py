import importlib.util
import sys
import types
import unittest
from pathlib import Path
from unittest.mock import patch


CONFIG_FLOW_PATH = (
    Path(__file__).resolve().parents[1]
    / "custom_components"
    / "fn_nas"
    / "config_flow.py"
)


class ReadOnlyOptionsFlow:
    @property
    def config_entry(self):
        return None


class ConfigFlowBase:
    def __init_subclass__(cls, **kwargs):
        super().__init_subclass__()


class StubSchema:
    def __init__(self, schema):
        self.schema = schema


def _identity(value=None, **kwargs):
    return value


def _install_stubs():
    homeassistant = types.ModuleType("homeassistant")
    config_entries = types.ModuleType("homeassistant.config_entries")
    config_entries.ConfigFlow = ConfigFlowBase
    config_entries.OptionsFlow = ReadOnlyOptionsFlow
    homeassistant.config_entries = config_entries

    core = types.ModuleType("homeassistant.core")
    core.callback = lambda func: func

    const = types.ModuleType("homeassistant.const")
    const.CONF_HOST = "host"
    const.CONF_PORT = "port"
    const.CONF_USERNAME = "username"
    const.CONF_PASSWORD = "password"
    const.CONF_SCAN_INTERVAL = "scan_interval"
    const.CONF_MAC = "mac"

    helpers = types.ModuleType("homeassistant.helpers")
    helpers_config_validation = types.ModuleType("homeassistant.helpers.config_validation")
    helpers.config_validation = helpers_config_validation

    asyncssh = types.ModuleType("asyncssh")
    asyncssh.Error = Exception

    voluptuous = types.ModuleType("voluptuous")
    voluptuous.Schema = StubSchema
    voluptuous.Required = _identity
    voluptuous.Optional = _identity
    voluptuous.In = _identity

    const_module = types.ModuleType("custom_components.fn_nas.const")
    const_module.DOMAIN = "fn_nas"
    const_module.DEFAULT_PORT = 22
    const_module.DEFAULT_SCAN_INTERVAL = 60
    const_module.CONF_IGNORE_DISKS = "ignore_disks"
    const_module.CONF_FAN_CONFIG_PATH = "fan_config_path"
    const_module.CONF_UPS_SCAN_INTERVAL = "ups_scan_interval"
    const_module.DEFAULT_UPS_SCAN_INTERVAL = 30
    const_module.CONF_ROOT_PASSWORD = "root_password"
    const_module.CONF_ENABLE_DOCKER = "enable_docker"

    custom_components = types.ModuleType("custom_components")
    fn_nas = types.ModuleType("custom_components.fn_nas")
    fn_nas.__path__ = []

    return {
        "asyncssh": asyncssh,
        "voluptuous": voluptuous,
        "homeassistant": homeassistant,
        "homeassistant.config_entries": config_entries,
        "homeassistant.core": core,
        "homeassistant.const": const,
        "homeassistant.helpers": helpers,
        "homeassistant.helpers.config_validation": helpers_config_validation,
        "custom_components": custom_components,
        "custom_components.fn_nas": fn_nas,
        "custom_components.fn_nas.const": const_module,
    }


class ConfigFlowTests(unittest.TestCase):
    def test_options_flow_does_not_assign_read_only_config_entry_property(self):
        entry = types.SimpleNamespace(options={}, data={}, title="192.168.2.86")
        module = types.ModuleType("custom_components.fn_nas.config_flow")
        module.__file__ = str(CONFIG_FLOW_PATH)
        module.__package__ = "custom_components.fn_nas"

        with patch.dict(sys.modules, _install_stubs()):
            spec = importlib.util.spec_from_file_location(
                "custom_components.fn_nas.config_flow",
                CONFIG_FLOW_PATH,
            )
            config_flow = importlib.util.module_from_spec(spec)
            sys.modules["custom_components.fn_nas.config_flow"] = config_flow
            spec.loader.exec_module(config_flow)

            flow = config_flow.ConfigFlow.async_get_options_flow(entry)

        self.assertIs(flow._config_entry, entry)


if __name__ == "__main__":
    unittest.main()
