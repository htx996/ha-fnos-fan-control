import importlib.util
import json
from pathlib import Path
import unittest


BACKEND_PATH = (
    Path(__file__).resolve().parents[1]
    / "custom_components"
    / "fn_nas"
    / "llled_fan_backend.py"
)
SPEC = importlib.util.spec_from_file_location("fn_nas_llled_fan_backend", BACKEND_PATH)
llled_backend = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(llled_backend)

LLLEDFanBackend = llled_backend.LLLEDFanBackend


def llled_status(**overrides):
    payload = {
        "ok": True,
        "supported": True,
        "available": True,
        "model": "dxp4800_pro",
        "backend": "ugreenctl",
        "min_pwm": 40,
        "write_confirmation_required": True,
        "write_confirmation_acknowledged": True,
        "cpu_fan_present": True,
        "cpu_pwm": 128,
        "sys_pwm": 96,
        "sys2_pwm": -1,
        "cpu_rpm": 2070,
        "sys_rpm": 1053,
        "sys2_rpm": 0,
        "fan_curve": {
            "enabled": False,
            "running": False,
            "profile": "stock-4800plus",
            "minimum_pwm": 64,
            "stock_curve": {
                "available": True,
                "profile": "stock-4800plus",
            },
        },
    }
    payload.update(overrides)
    return payload


class FakeCoordinator:
    def __init__(self, responses):
        self.responses = list(responses)
        self.commands = []

    async def run_command(self, command):
        self.commands.append(command)
        return self.responses.pop(0) if self.responses else ""


class LLLEDFanBackendTests(unittest.IsolatedAsyncioTestCase):
    def test_parse_status_builds_two_controllable_fans_and_global_mode(self):
        backend = LLLEDFanBackend(FakeCoordinator([]))

        state = backend.parse_status(llled_status())

        self.assertTrue(state["available"])
        self.assertEqual(state["mode"], "手动")
        self.assertEqual(state["stock_profile"], "stock-4800plus")
        self.assertEqual([fan["channel"] for fan in state["fans"]], ["cpu", "sys"])
        self.assertEqual([fan["name"] for fan in state["fans"]], ["CPU 风扇", "系统风扇"])
        self.assertEqual(state["fans"][0]["rpm"], 2070)
        self.assertEqual(state["fans"][0]["pwm_percent"], 50)
        self.assertTrue(state["fans"][0]["supports_pwm"])

    def test_parse_status_marks_auto_mode_when_curve_daemon_is_running(self):
        payload = llled_status()
        payload["fan_curve"]["running"] = True

        state = LLLEDFanBackend(FakeCoordinator([])).parse_status(payload)

        self.assertEqual(state["mode"], "自动")
        self.assertEqual(state["fans"][0]["control_mode"], "自动")
        self.assertFalse(state["fans"][0]["supports_modes"])

    def test_parse_status_keeps_monitoring_when_write_confirmation_is_missing(self):
        state = LLLEDFanBackend(FakeCoordinator([])).parse_status(
            llled_status(write_confirmation_acknowledged=False)
        )

        self.assertTrue(state["available"])
        self.assertFalse(state["supports_control"])
        self.assertTrue(all(not fan["supports_pwm"] for fan in state["fans"]))

    async def test_get_status_discovers_and_invokes_installed_llled_cgi(self):
        payload = llled_status()
        coordinator = FakeCoordinator(
            ["__FN_NAS_LLLED_API__/var/apps/App.Native.UGreenLED/ui/api.cgi\n" + json.dumps(payload)]
        )
        backend = LLLEDFanBackend(coordinator)

        state = await backend.get_status()

        self.assertTrue(state["installed"])
        self.assertEqual(state["api_path"], "/var/apps/App.Native.UGreenLED/ui/api.cgi")
        self.assertIn("PATH_INFO=/bios/status", coordinator.commands[0])
        self.assertIn("REQUEST_METHOD=GET", coordinator.commands[0])

    async def test_set_percentage_uses_llled_fan_endpoint(self):
        coordinator = FakeCoordinator([json.dumps(llled_status(cpu_pwm=102))])
        backend = LLLEDFanBackend(coordinator)
        backend.state = backend.parse_status(llled_status())

        result = await backend.set_percentage("cpu", 40)

        self.assertTrue(result)
        self.assertIn("PATH_INFO=/bios/fan", coordinator.commands[0])
        self.assertIn("channel=cpu", coordinator.commands[0])
        self.assertIn("pwm=102", coordinator.commands[0])
        self.assertIn("confirm=firmware-reversed", coordinator.commands[0])

    async def test_set_auto_mode_uses_exact_stock_profile_from_status(self):
        auto_payload = llled_status()
        auto_payload["fan_curve"]["running"] = True
        coordinator = FakeCoordinator([json.dumps(auto_payload)])
        backend = LLLEDFanBackend(coordinator)
        backend.state = backend.parse_status(llled_status())

        result = await backend.set_mode("自动")

        self.assertTrue(result)
        self.assertIn("PATH_INFO=/bios/fan-curve", coordinator.commands[0])
        self.assertIn("action=start", coordinator.commands[0])
        self.assertIn("mode=stock-4800plus", coordinator.commands[0])
        self.assertIn("confirm=firmware-reversed", coordinator.commands[0])

    async def test_set_full_speed_writes_both_fans_after_stopping_curve(self):
        stopped = llled_status()
        full_cpu = llled_status(cpu_pwm=255)
        full_system = llled_status(cpu_pwm=255, sys_pwm=255)
        coordinator = FakeCoordinator(
            [json.dumps(stopped), json.dumps(full_cpu), json.dumps(full_system)]
        )
        backend = LLLEDFanBackend(coordinator)
        backend.state = backend.parse_status(llled_status())

        result = await backend.set_mode("全速")

        self.assertTrue(result)
        self.assertEqual(len(coordinator.commands), 3)
        self.assertIn("action=stop", coordinator.commands[0])
        self.assertIn("channel=cpu", coordinator.commands[1])
        self.assertIn("channel=sys", coordinator.commands[2])
        self.assertIn("pwm=255", coordinator.commands[1])
        self.assertIn("pwm=255", coordinator.commands[2])

    async def test_set_manual_recovers_to_half_speed_when_stopped_curve_was_at_full(self):
        automatic = llled_status(cpu_pwm=255, sys_pwm=255)
        automatic["fan_curve"]["running"] = True
        stopped_at_full = llled_status(cpu_pwm=255, sys_pwm=255)
        recovered_cpu = llled_status(cpu_pwm=128, sys_pwm=255)
        recovered_both = llled_status(cpu_pwm=128, sys_pwm=128)
        coordinator = FakeCoordinator(
            [
                json.dumps(stopped_at_full),
                json.dumps(recovered_cpu),
                json.dumps(recovered_both),
            ]
        )
        backend = LLLEDFanBackend(coordinator)
        backend.state = backend.parse_status(automatic)

        result = await backend.set_mode("手动")

        self.assertTrue(result)
        self.assertEqual(len(coordinator.commands), 3)
        self.assertIn("action=stop", coordinator.commands[0])
        self.assertIn("pwm=128", coordinator.commands[1])
        self.assertIn("pwm=128", coordinator.commands[2])


if __name__ == "__main__":
    unittest.main()
