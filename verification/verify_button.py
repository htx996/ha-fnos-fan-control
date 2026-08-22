import importlib.util
from pathlib import Path
import sys
import types

from verification.support import VerifyCase, patch_modules


BUTTON_PATH = (
    Path(__file__).resolve().parents[1]
    / "custom_components"
    / "fn_nas"
    / "button.py"
)


class StubButtonEntity:
    pass


class StubCoordinatorEntity:
    def __init__(self, coordinator):
        self.coordinator = coordinator

    def async_write_ha_state(self):
        self.wrote_state = True


class StubVMManager:
    def __init__(self):
        self.calls = []

    async def control_vm(self, name, action):
        self.calls.append((name, action))
        return True


class StubDockerManager:
    def __init__(self):
        self.calls = []

    async def control_container(self, name, action):
        self.calls.append((name, action))
        return True


class StubCoordinator:
    def __init__(self):
        self.data = {
            "vms": [{"name": "ha", "title": "Homeassistant", "state": "shut off"}],
            "docker_containers": [{"name": "app", "status": "exited"}],
        }
        self.vm_manager = StubVMManager()
        self.docker_manager = StubDockerManager()
        self.shutdown_count = 0
        self.reboot_count = 0
        self.refresh_count = 0

    async def shutdown_system(self):
        self.shutdown_count += 1

    async def reboot_system(self):
        self.reboot_count += 1

    def async_add_listener(self, _listener):
        return None

    async def async_request_refresh(self):
        self.refresh_count += 1


class StubServices:
    def __init__(self):
        self.calls = []

    async def async_call(self, domain, service, data):
        self.calls.append((domain, service, data))


def _install_stubs():
    homeassistant = types.ModuleType("homeassistant")
    components = types.ModuleType("homeassistant.components")
    button_component = types.ModuleType("homeassistant.components.button")
    button_component.ButtonEntity = StubButtonEntity
    helpers = types.ModuleType("homeassistant.helpers")
    entity = types.ModuleType("homeassistant.helpers.entity")
    entity.EntityCategory = types.SimpleNamespace(CONFIG="config")
    update_coordinator = types.ModuleType("homeassistant.helpers.update_coordinator")
    update_coordinator.CoordinatorEntity = StubCoordinatorEntity

    custom_components = types.ModuleType("custom_components")
    fn_nas = types.ModuleType("custom_components.fn_nas")
    fn_nas.__path__ = [str(BUTTON_PATH.parent)]
    const = types.ModuleType("custom_components.fn_nas.const")
    const.DOMAIN = "fn_nas"
    const.DATA_UPDATE_COORDINATOR = "coordinator"
    const.DEVICE_ID_NAS = "flynas_nas_system"
    const.CONF_ENABLE_DOCKER = "enable_docker"
    const.CONF_MAC = "mac"
    dashboard = types.ModuleType("custom_components.fn_nas.dashboard")
    dashboard.dashboard_metadata = lambda category, role, order: {
        "fn_nas_dashboard_category": category,
        "fn_nas_dashboard_role": role,
        "fn_nas_dashboard_order": order,
    }

    return {
        "homeassistant": homeassistant,
        "homeassistant.components": components,
        "homeassistant.components.button": button_component,
        "homeassistant.helpers": helpers,
        "homeassistant.helpers.entity": entity,
        "homeassistant.helpers.update_coordinator": update_coordinator,
        "custom_components": custom_components,
        "custom_components.fn_nas": fn_nas,
        "custom_components.fn_nas.const": const,
        "custom_components.fn_nas.dashboard": dashboard,
    }


class ButtonVerifications(VerifyCase):
    async def verify_setup_adds_independent_actions(self):
        with patch_modules(sys.modules, _install_stubs()):
            spec = importlib.util.spec_from_file_location(
                "custom_components.fn_nas.button", BUTTON_PATH
            )
            module = importlib.util.module_from_spec(spec)
            sys.modules["custom_components.fn_nas.button"] = module
            spec.loader.exec_module(module)

            coordinator = StubCoordinator()
            services = StubServices()
            hass = types.SimpleNamespace(
                data={
                    "fn_nas": {
                        "entry-1": {
                            "coordinator": coordinator,
                            "enable_docker": True,
                        }
                    }
                },
                services=services,
            )
            entry = types.SimpleNamespace(
                entry_id="entry-1",
                data={"mac": "00:11:22:33:44:55"},
            )
            entities = []

            await module.async_setup_entry(hass, entry, entities.extend)

            self.assertEqual(len(entities), 9)
            names = [entity._attr_name for entity in entities]
            for name in (
                "开机",
                "关机",
                "重启",
                "Homeassistant 开机",
                "Homeassistant 关机",
                "Homeassistant 重启",
                "app 启动",
                "app 停止",
                "app 重启",
            ):
                self.assertIn(name, names)

            power_on = entities[0]
            power_on.hass = hass
            self.assertTrue(power_on.available)
            await power_on.async_press()
            await entities[1].async_press()
            await entities[3].async_press()
            await entities[4].async_press()
            await entities[6].async_press()
            await entities[7].async_press()

        self.assertEqual(
            services.calls,
            [
                (
                    "wake_on_lan",
                    "send_magic_packet",
                    {"mac": "00:11:22:33:44:55"},
                )
            ],
        )
        self.assertEqual(coordinator.shutdown_count, 1)
        self.assertEqual(
            coordinator.vm_manager.calls,
            [("ha", "start"), ("ha", "shutdown")],
        )
        self.assertEqual(
            coordinator.docker_manager.calls,
            [("app", "start"), ("app", "stop")],
        )
        self.assertEqual(coordinator.refresh_count, 2)
        self.assertEqual(coordinator.data["vms"][0]["state"], "shut off")
        self.assertEqual(
            coordinator.data["docker_containers"][0]["status"], "exited"
        )

    async def verify_wake_button_does_not_require_coordinator(self):
        with patch_modules(sys.modules, _install_stubs()):
            spec = importlib.util.spec_from_file_location(
                "custom_components.fn_nas.button", BUTTON_PATH
            )
            module = importlib.util.module_from_spec(spec)
            sys.modules["custom_components.fn_nas.button"] = module
            spec.loader.exec_module(module)

            entry = types.SimpleNamespace(
                entry_id="entry-1",
                data={"mac": "00:11:22:33:44:55"},
            )
            entity = module.PowerOnButton(entry)

        self.assertTrue(entity.available)
        self.assertEqual(
            entity.extra_state_attributes["fn_nas_dashboard_role"],
            "power_on",
        )
