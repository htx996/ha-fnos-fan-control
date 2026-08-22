import importlib.util
from pathlib import Path
import unittest


FAN_MANAGER_PATH = (
    Path(__file__).resolve().parents[1]
    / "custom_components"
    / "fn_nas"
    / "fan_manager.py"
)
SPEC = importlib.util.spec_from_file_location("fn_nas_fan_manager", FAN_MANAGER_PATH)
fan_manager = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(fan_manager)

CONTROL_MODE_AUTO = fan_manager.CONTROL_MODE_AUTO
CONTROL_MODE_FULL_SPEED = fan_manager.CONTROL_MODE_FULL_SPEED
CONTROL_MODE_MANUAL = fan_manager.CONTROL_MODE_MANUAL
FanManager = fan_manager.FanManager


class FakeCoordinator:
    def __init__(self, responses=None):
        self.responses = responses or []
        self.commands = []

    async def run_command(self, command):
        self.commands.append(command)
        if self.responses:
            response = self.responses.pop(0)
            if callable(response):
                return response(command)
            return response
        return "__FN_NAS_OK__"


class FanManagerTests(unittest.IsolatedAsyncioTestCase):
    async def test_get_fans_info_uses_sensors_output_when_hwmon_has_no_fans(self):
        coordinator = FakeCoordinator(
            [
                "",
                "\n".join(
                    [
                        "nct6798-isa-0290",
                        "Adapter: ISA adapter",
                        "CPU Fan:     1180 RPM",
                        "System Fan:   900 RPM",
                        "SYSTIN:       +27.8°C",
                    ]
                ),
            ]
        )

        fans = await FanManager(coordinator).get_fans_info()

        self.assertEqual(len(fans), 2)
        self.assertEqual(fans[0]["name"], "CPU Fan")
        self.assertEqual(fans[0]["rpm"], 1180)
        self.assertFalse(fans[0]["supports_pwm"])
        self.assertFalse(fans[0]["supports_modes"])
        self.assertIn("sensors", coordinator.commands[1])

    def test_parse_hwmon_snapshot_discovers_rpm_pwm_and_mode_without_hardcoded_hwmon(self):
        snapshot = "\n".join(
            [
                "entry\t/sys/class/hwmon/hwmon2\t/sys/devices/platform/nct6775.656\tnct6798\t1\t1180\tCPU Fan\t1\t128\t2\t1\t1",
                "entry\t/sys/class/hwmon/hwmon7\t/sys/devices/platform/nct6775.656\tnct6798\t2\t900\tSystem Fan\t1\t64\t1\t1\t1",
            ]
        )

        fans = FanManager(FakeCoordinator()).parse_hwmon_snapshot(snapshot)

        self.assertEqual(len(fans), 2)
        self.assertTrue(fans[0]["id"].startswith("nct6798_fan1_"))
        self.assertNotIn("hwmon2", fans[0]["id"])
        self.assertEqual(fans[0]["rpm"], 1180)
        self.assertEqual(fans[0]["pwm_raw"], 128)
        self.assertEqual(fans[0]["pwm_percent"], 50)
        self.assertEqual(fans[0]["control_mode"], CONTROL_MODE_AUTO)
        self.assertTrue(fans[0]["supports_pwm"])
        self.assertTrue(fans[0]["supports_modes"])

        self.assertEqual(fans[1]["control_mode"], CONTROL_MODE_MANUAL)

    def test_parse_hwmon_snapshot_keeps_monitor_only_fans_when_pwm_is_missing_or_read_only(self):
        snapshot = "\n".join(
            [
                "entry\t/sys/class/hwmon/hwmon0\t/sys/devices/pci0000:00/coretemp.0\tcoretemp\t1\t1500\tCPU Fan\t0\t\t\t0\t0",
                "entry\t/sys/class/hwmon/hwmon1\t/sys/devices/platform/it87.656\tit8792\t2\t1000\tRear Fan\t1\t90\t1\t0\t1",
            ]
        )

        fans = FanManager(FakeCoordinator()).parse_hwmon_snapshot(snapshot)

        self.assertEqual(fans[0]["rpm"], 1500)
        self.assertIsNone(fans[0]["pwm_percent"])
        self.assertFalse(fans[0]["supports_pwm"])
        self.assertFalse(fans[0]["supports_modes"])

        self.assertEqual(fans[1]["pwm_percent"], 35)
        self.assertFalse(fans[1]["supports_pwm"])
        self.assertTrue(fans[1]["supports_modes"])

    async def test_set_percentage_switches_to_manual_mode_and_writes_scaled_pwm_value(self):
        coordinator = FakeCoordinator()
        manager = FanManager(coordinator)
        fan = {
            "pwm_path": "/sys/class/hwmon/hwmon2/pwm1",
            "pwm_enable_path": "/sys/class/hwmon/hwmon2/pwm1_enable",
            "supports_modes": True,
            "supports_pwm": True,
        }

        self.assertTrue(await manager.set_percentage(fan, 50))

        self.assertEqual(len(coordinator.commands), 2)
        self.assertIn("pwm1_enable", coordinator.commands[0])
        self.assertIn("'1'", coordinator.commands[0])
        self.assertIn("pwm1", coordinator.commands[1])
        self.assertIn("'128'", coordinator.commands[1])

    async def test_set_full_speed_falls_back_to_manual_100_percent_if_pwm_enable_zero_fails(self):
        def response(command):
            if "pwm1_enable" in command and "'0'" in command:
                return "__FN_NAS_ERROR__"
            return "__FN_NAS_OK__"

        coordinator = FakeCoordinator([response, response, response])
        manager = FanManager(coordinator)
        fan = {
            "pwm_path": "/sys/class/hwmon/hwmon2/pwm1",
            "pwm_enable_path": "/sys/class/hwmon/hwmon2/pwm1_enable",
            "supports_modes": True,
            "supports_pwm": True,
        }

        self.assertTrue(await manager.set_mode(fan, CONTROL_MODE_FULL_SPEED))

        self.assertEqual(len(coordinator.commands), 3)
        self.assertIn("pwm1_enable", coordinator.commands[0])
        self.assertIn("'0'", coordinator.commands[0])
        self.assertIn("pwm1_enable", coordinator.commands[1])
        self.assertIn("'1'", coordinator.commands[1])
        self.assertIn("pwm1", coordinator.commands[2])
        self.assertIn("'255'", coordinator.commands[2])


if __name__ == "__main__":
    unittest.main()
