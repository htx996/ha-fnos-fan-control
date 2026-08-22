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
    def __init__(self, responses=None, llled_backend=None):
        self.responses = responses or []
        self.commands = []
        self.llled_fan_backend = llled_backend

    async def run_command(self, command):
        self.commands.append(command)
        if self.responses:
            response = self.responses.pop(0)
            if callable(response):
                return response(command)
            return response
        return "__FN_NAS_OK__"


class FakeLLLEDBackend:
    def __init__(self, state, percentage_result=True, mode_result=True):
        self.state = state
        self.percentage_result = percentage_result
        self.mode_result = mode_result
        self.percentage_calls = []
        self.mode_calls = []

    async def get_status(self):
        return self.state

    async def set_percentage(self, channel, percentage):
        self.percentage_calls.append((channel, percentage))
        return self.percentage_result

    async def set_mode(self, mode):
        self.mode_calls.append(mode)
        return self.mode_result


class FanManagerTests(unittest.IsolatedAsyncioTestCase):
    async def test_llled_merges_control_data_into_existing_hwmon_fan_ids(self):
        llled_state = {
            "installed": True,
            "available": True,
            "supports_control": True,
            "supports_modes": True,
            "available_modes": [CONTROL_MODE_AUTO, CONTROL_MODE_MANUAL, CONTROL_MODE_FULL_SPEED],
            "mode": CONTROL_MODE_AUTO,
            "fans": [
                {
                    "id": "llled_cpu",
                    "name": "CPU 风扇",
                    "channel": "cpu",
                    "backend": "llled",
                    "rpm": 2070,
                    "pwm_raw": 128,
                    "pwm_percent": 50,
                    "control_mode": CONTROL_MODE_AUTO,
                    "supports_pwm": True,
                    "supports_modes": False,
                    "minimum_pwm_raw": 80,
                },
                {
                    "id": "llled_sys",
                    "name": "系统风扇",
                    "channel": "sys",
                    "backend": "llled",
                    "rpm": 1053,
                    "pwm_raw": 96,
                    "pwm_percent": 38,
                    "control_mode": CONTROL_MODE_AUTO,
                    "supports_pwm": True,
                    "supports_modes": False,
                    "minimum_pwm_raw": 80,
                },
            ],
        }
        backend = FakeLLLEDBackend(llled_state)
        coordinator = FakeCoordinator(
            [
                "\n".join(
                    [
                        "host\tsys_vendor\tUGREEN",
                        "host\tproduct_name\tDXP4800 Pro",
                        "entry\t/sys/class/hwmon/hwmon5\t/sys/devices/platform/it87.656\tit8613\t2\t1800\t\t1\t110\t1\t1\t1\tUGREEN\tDXP4800 Pro",
                        "entry\t/sys/class/hwmon/hwmon5\t/sys/devices/platform/it87.656\tit8613\t3\t900\t\t1\t90\t1\t1\t1\tUGREEN\tDXP4800 Pro",
                    ]
                )
            ],
            llled_backend=backend,
        )
        manager = FanManager(coordinator)

        fans = await manager.get_fans_info()

        self.assertTrue(fans[0]["id"].startswith("it8613_fan2_"))
        self.assertTrue(fans[1]["id"].startswith("it8613_fan3_"))
        self.assertEqual([fan["backend"] for fan in fans], ["llled", "llled"])
        self.assertEqual([fan["channel"] for fan in fans], ["cpu", "sys"])
        self.assertEqual([fan["rpm"] for fan in fans], [2070, 1053])
        self.assertEqual(manager.control_state["mode"], CONTROL_MODE_AUTO)
        self.assertEqual(manager.last_diagnostics["source"], "llled+hwmon")

    async def test_llled_exposes_fans_without_hwmon_driver(self):
        llled_state = {
            "installed": True,
            "available": True,
            "supports_control": True,
            "supports_modes": True,
            "available_modes": [CONTROL_MODE_AUTO, CONTROL_MODE_MANUAL, CONTROL_MODE_FULL_SPEED],
            "mode": CONTROL_MODE_MANUAL,
            "fans": [
                {
                    "id": "llled_cpu",
                    "name": "CPU 风扇",
                    "channel": "cpu",
                    "backend": "llled",
                    "rpm": 2000,
                    "pwm_raw": 128,
                    "pwm_percent": 50,
                    "control_mode": CONTROL_MODE_MANUAL,
                    "supports_pwm": True,
                    "supports_modes": False,
                }
            ],
        }
        coordinator = FakeCoordinator([""], FakeLLLEDBackend(llled_state))
        manager = FanManager(coordinator)

        fans = await manager.get_fans_info()

        self.assertEqual([fan["id"] for fan in fans], ["llled_cpu"])
        self.assertEqual(manager.last_diagnostics["source"], "llled")
        self.assertEqual(len(coordinator.commands), 1)

    async def test_set_percentage_delegates_llled_backed_fan(self):
        backend = FakeLLLEDBackend({"fans": [], "mode": CONTROL_MODE_MANUAL})
        manager = FanManager(FakeCoordinator(llled_backend=backend))
        fan = {
            "id": "llled_cpu",
            "name": "CPU 风扇",
            "backend": "llled",
            "channel": "cpu",
            "supports_pwm": True,
        }

        self.assertTrue(await manager.set_percentage(fan, 45))

        self.assertEqual(backend.percentage_calls, [("cpu", 45)])

    async def test_set_global_mode_delegates_to_llled(self):
        backend = FakeLLLEDBackend({"fans": [], "mode": CONTROL_MODE_MANUAL})
        manager = FanManager(FakeCoordinator(llled_backend=backend))

        self.assertTrue(await manager.set_global_mode(CONTROL_MODE_AUTO))

        self.assertEqual(backend.mode_calls, [CONTROL_MODE_AUTO])

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

    async def test_get_fans_info_records_diagnostics_when_no_fans_are_found(self):
        coordinator = FakeCoordinator(
            [
                "",
                "\n".join(
                    [
                        "coretemp-isa-0000",
                        "Adapter: ISA adapter",
                        "Package id 0: +51.0°C",
                    ]
                ),
                "",
                "",
                "hwmon\t/sys/class/hwmon/hwmon0\t/sys/devices/platform/coretemp.0\tcoretemp\t\t",
            ]
        )
        manager = FanManager(coordinator)

        fans = await manager.get_fans_info()

        self.assertEqual(fans, [])
        self.assertEqual(manager.last_diagnostics["status"], "未发现风扇")
        self.assertEqual(manager.last_diagnostics["fan_count"], 0)
        self.assertEqual(manager.last_diagnostics["source"], "none")
        self.assertEqual(
            manager.last_diagnostics["hwmon_inventory"][0]["path"],
            "/sys/class/hwmon/hwmon0",
        )
        self.assertIn("sensors -u", coordinator.commands[2])

    async def test_get_fans_info_uses_direct_sysfs_fan_input_when_standard_sources_are_empty(self):
        coordinator = FakeCoordinator(
            [
                "",
                "",
                "",
                "\n".join(
                    [
                        "sysfs\t/sys/devices/platform/fn_ec\t/sys/devices/platform/fn_ec\tfn_ec\t1\t1380\tSystem Fan\t1\t128\t1\t1\t1\t/sys/devices/platform/fn_ec/fan1_input\t/sys/devices/platform/fn_ec/pwm1\t/sys/devices/platform/fn_ec/pwm1_enable",
                    ]
                ),
            ]
        )
        manager = FanManager(coordinator)

        fans = await manager.get_fans_info()

        self.assertEqual(len(fans), 1)
        self.assertEqual(fans[0]["name"], "System Fan")
        self.assertEqual(fans[0]["rpm"], 1380)
        self.assertEqual(fans[0]["pwm_percent"], 50)
        self.assertTrue(fans[0]["supports_pwm"])
        self.assertTrue(fans[0]["supports_modes"])
        self.assertEqual(fans[0]["fan_input_path"], "/sys/devices/platform/fn_ec/fan1_input")
        self.assertEqual(manager.last_diagnostics["source"], "sysfs")

    def test_parse_sysfs_candidates_includes_raw_fan_and_cooling_paths(self):
        output = "\n".join(
            [
                "candidate\t/sys/devices/platform/fn_ec/fan1_input\t1380",
                "candidate\t/sys/devices/platform/fn_ec/pwm1\t128",
                "cooling\t/sys/class/thermal/cooling_device0\tFan\t2\t10\t1",
            ]
        )
        manager = FanManager(FakeCoordinator())
        diagnostics = manager._build_diagnostics(
            "none",
            [],
            "",
            "",
            "",
            output,
            "",
        )

        self.assertEqual(
            diagnostics["sysfs_fan_candidates"][0]["path"],
            "/sys/devices/platform/fn_ec/fan1_input",
        )
        self.assertEqual(diagnostics["cooling_devices"][0]["type"], "Fan")

    def test_diagnostics_expose_host_driver_and_fan_service_inventory(self):
        inventory = "\n".join(
            [
                "host\tkernel\t6.12.18-trim   ",
                "host\tsys_vendor\tExample Vendor",
                "host\tproduct_name\tNAS Mini",
                "host\tboard_vendor\tExample Board Vendor",
                "host\tboard_name\tN100-NAS",
                "module\tloaded\tcoretemp",
                "module\tloaded\tug_it86x_cpufan",
                "module\tavailable\tnct6775",
                "service\tthermal-daemon.service",
                "service\tpwm-fancontrol.service",
                "serviceprop\tpwm-fancontrol.service\tLoadState\tloaded",
                "serviceprop\tpwm-fancontrol.service\tActiveState\tactive",
                "serviceprop\tpwm-fancontrol.service\tSubState\trunning",
                "serviceprop\tpwm-fancontrol.service\tUnitFileState\tenabled",
                "serviceprop\tpwm-fancontrol.service\tFragmentPath\t/usr/lib/systemd/system/pwm-fancontrol.service",
                "serviceprop\tpwm-fancontrol.service\tMainPID\t3241",
                "serviceprop\tpwm-fancontrol.service\tExecMainStatus\t0",
                "serviceprop\tpwm-fancontrol.service\tExecStart\t/usr/sbin/hwmonitor-480t",
                "serviceprop\tpwm-fancontrol.service\tProcessExe\t/usr/sbin/hwmonitor-480t",
                "vendor\t/proc/it86\tdirectory\t1\t1",
                "vendor\t/proc/it86/fan\tfile\t1\t1",
                "servicelog\tpwm-fancontrol.service\tStarted PWM fan control.",
                "fanscript\tpath\t/usr/trim/bin/pwm-fancontrol.sh",
                "fanscript\tsize\t842",
                "fanscript\tmode\t755",
                "fanscript\towner\troot:root",
                "fanscript\tsha256\tabc123",
                "fanscript\tsyntax\tok",
                "fanscriptline\t8\tmodprobe it87 force_id=0x8613",
                "fanscriptline\t12\tsensors -s",
                "boardconfig\tpath\t/boot/board.json",
                "boardconfig\treadable\t1",
                "boardconfig\tjson_valid\t1",
                "boardconfig\tfan_count\t1",
                "boardfan\t0\tSystem Fan\t/sys/class/thermal/thermal_zone0/temp\t/sys/class/hwmon/hwmon5/pwm3\t80\t45\t255\t1000\t1",
                "boardpath\t0\ttsysfs\t/sys/class/thermal/thermal_zone0/temp\t1\t1\t0\t51000",
                "boardpath\t0\tfsysfs\t/sys/class/hwmon/hwmon5/pwm3\t0\t0\t0\t",
                "fanbinary\tpath\t/usr/sbin/fancontrol",
                "fanbinary\tsize\t18432",
                "fanbinary\tmode\t755",
                "fanbinary\towner\troot:root",
                "fanbinary\tsha256\tdef456",
                "fanbinary\tfile_type\tELF 64-bit LSB executable",
                "fanprocess\t4217\t/usr/sbin/fancontrol\tS\t0\t/usr/sbin/fancontrol -T /sys/class/thermal/thermal_zone0/temp -F /sys/class/hwmon/hwmon5/pwm3",
                "it87info\tfilename\t/lib/modules/6.18.18/kernel/drivers/hwmon/it87.ko",
                "it87info\tversion\t1.0",
                "it87info\tvermagic\t6.18.18 SMP mod_unload",
                "it87parm\tforce_id:Override chip ID",
                "it87dry\tinsmod /lib/modules/6.18.18/kernel/drivers/hwmon/it87.ko",
                "kernellog\tit87: Found IT8613E chip at 0xa30",
                "app\tfan-control\t1\t1\t9511",
                'api\t9511\t{"auth_enabled": true, "authenticated": false}',
                "tool\tsensors-detect\t1",
            ]
        )

        diagnostics = FanManager(FakeCoordinator())._build_diagnostics(
            "none",
            [],
            "",
            "",
            "",
            "",
            inventory,
        )

        self.assertEqual(diagnostics["host_hardware"]["kernel"], "6.12.18-trim")
        self.assertEqual(diagnostics["host_hardware"]["product_name"], "NAS Mini")
        self.assertEqual(diagnostics["host_hardware"]["board_name"], "N100-NAS")
        self.assertEqual(
            diagnostics["loaded_fan_modules"],
            ["coretemp", "ug_it86x_cpufan"],
        )
        self.assertEqual(diagnostics["available_fan_modules"], ["nct6775"])
        self.assertEqual(
            diagnostics["fan_services"],
            ["thermal-daemon.service", "pwm-fancontrol.service"],
        )
        self.assertEqual(
            diagnostics["fan_service_details"]["pwm-fancontrol.service"],
            {
                "load_state": "loaded",
                "active_state": "active",
                "sub_state": "running",
                "unit_file_state": "enabled",
                "fragment_path": "/usr/lib/systemd/system/pwm-fancontrol.service",
                "main_pid": 3241,
                "exec_main_status": 0,
                "exec_start": "/usr/sbin/hwmonitor-480t",
                "process_exe": "/usr/sbin/hwmonitor-480t",
            },
        )
        self.assertEqual(
            diagnostics["vendor_fan_interfaces"],
            [
                {
                    "path": "/proc/it86",
                    "type": "directory",
                    "readable": True,
                    "writable": True,
                },
                {
                    "path": "/proc/it86/fan",
                    "type": "file",
                    "readable": True,
                    "writable": True,
                },
            ],
        )
        self.assertEqual(
            diagnostics["fan_service_logs"],
            ["Started PWM fan control."],
        )
        self.assertEqual(
            diagnostics["fan_startup_script"],
            {
                "path": "/usr/trim/bin/pwm-fancontrol.sh",
                "size": 842,
                "mode": "755",
                "owner": "root:root",
                "sha256": "abc123",
                "syntax": "ok",
                "relevant_lines": [
                    {"line": 8, "text": "modprobe it87 force_id=0x8613"},
                    {"line": 12, "text": "sensors -s"},
                ],
            },
        )
        self.assertEqual(
            diagnostics["board_fan_config"],
            {
                "path": "/boot/board.json",
                "readable": True,
                "json_valid": True,
                "fan_count": 1,
                "fans": [
                    {
                        "index": 0,
                        "name": "System Fan",
                        "tsysfs": "/sys/class/thermal/thermal_zone0/temp",
                        "fsysfs": "/sys/class/hwmon/hwmon5/pwm3",
                        "start_speed": 80,
                        "start_temp": 45,
                        "max_speed": 255,
                        "temp_div": 1000,
                        "verbose": True,
                        "paths": {
                            "tsysfs": {
                                "path": "/sys/class/thermal/thermal_zone0/temp",
                                "exists": True,
                                "readable": True,
                                "writable": False,
                                "value": "51000",
                            },
                            "fsysfs": {
                                "path": "/sys/class/hwmon/hwmon5/pwm3",
                                "exists": False,
                                "readable": False,
                                "writable": False,
                            },
                        },
                    }
                ],
            },
        )
        self.assertEqual(
            diagnostics["fancontrol_runtime"],
            {
                "binary": {
                    "path": "/usr/sbin/fancontrol",
                    "size": 18432,
                    "mode": "755",
                    "owner": "root:root",
                    "sha256": "def456",
                    "file_type": "ELF 64-bit LSB executable",
                },
                "process_count": 1,
                "processes": [
                    {
                        "pid": 4217,
                        "exe": "/usr/sbin/fancontrol",
                        "state": "S",
                        "uid": 0,
                        "cmdline": "/usr/sbin/fancontrol -T /sys/class/thermal/thermal_zone0/temp -F /sys/class/hwmon/hwmon5/pwm3",
                    }
                ],
            },
        )
        self.assertEqual(
            diagnostics["it87_module_info"],
            {
                "filename": "/lib/modules/6.18.18/kernel/drivers/hwmon/it87.ko",
                "version": "1.0",
                "vermagic": "6.18.18 SMP mod_unload",
                "parameters": ["force_id:Override chip ID"],
                "dry_run": ["insmod /lib/modules/6.18.18/kernel/drivers/hwmon/it87.ko"],
            },
        )
        self.assertEqual(
            diagnostics["fan_kernel_logs"],
            ["it87: Found IT8613E chip at 0xa30"],
        )
        self.assertEqual(
            diagnostics["fan_control_app"],
            {
                "installed": True,
                "listening": True,
                "port": 9511,
                "api_status": '{"auth_enabled": true, "authenticated": false}',
            },
        )
        self.assertEqual(diagnostics["diagnostic_tools"], {"sensors-detect": True})

    def test_parse_sensors_output_accepts_sensors_u_fan_input_lines(self):
        output = "\n".join(
            [
                "nct6798-isa-0290",
                "Adapter: ISA adapter",
                "fan1:",
                "  fan1_input: 1180.000",
                "fan2:",
                "  fan2_input: 1,230.000",
            ]
        )

        fans = FanManager(FakeCoordinator()).parse_sensors_output(output)

        self.assertEqual(len(fans), 2)
        self.assertEqual(fans[0]["name"], "fan1")
        self.assertEqual(fans[0]["rpm"], 1180)
        self.assertEqual(fans[1]["rpm"], 1230)

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

    def test_dxp4800pro_it8613_only_exposes_connected_cpu_and_system_channels(self):
        snapshot = "\n".join(
            [
                "entry\t/sys/class/hwmon/hwmon5\t/sys/devices/platform/it87.656\tit8613\t1\t0\t\t1\t128\t1\t1\t1\tUGREEN\tDXP4800 Pro",
                "entry\t/sys/class/hwmon/hwmon5\t/sys/devices/platform/it87.656\tit8613\t2\t1320\t\t1\t112\t2\t1\t1\tUGREEN\tDXP4800 Pro",
                "entry\t/sys/class/hwmon/hwmon5\t/sys/devices/platform/it87.656\tit8613\t3\t780\t\t1\t96\t2\t1\t1\tUGREEN\tDXP4800 Pro",
                "entry\t/sys/class/hwmon/hwmon5\t/sys/devices/platform/it87.656\tit8613\t4\t0\t\t1\t255\t1\t1\t1\tUGREEN\tDXP4800 Pro",
                "entry\t/sys/class/hwmon/hwmon5\t/sys/devices/platform/it87.656\tit8613\t5\t0\t\t1\t255\t1\t1\t1\tUGREEN\tDXP4800 Pro",
            ]
        )

        fans = FanManager(FakeCoordinator()).parse_hwmon_snapshot(snapshot)

        self.assertEqual([fan["index"] for fan in fans], [2, 3])
        self.assertEqual([fan["name"] for fan in fans], ["CPU 风扇", "系统风扇"])
        self.assertTrue(all(fan["supports_pwm"] for fan in fans))
        self.assertTrue(all(fan["supports_manual_mode"] for fan in fans))
        self.assertTrue(all(fan["supports_modes"] for fan in fans))
        self.assertTrue(all(not fan["supports_auto_mode"] for fan in fans))
        self.assertTrue(
            all(
                fan["available_modes"]
                == [CONTROL_MODE_MANUAL, CONTROL_MODE_FULL_SPEED]
                for fan in fans
            )
        )
        self.assertTrue(all(fan["minimum_pwm_raw"] == 80 for fan in fans))
        self.assertTrue(all(fan["minimum_pwm_percent"] == 31 for fan in fans))
        self.assertTrue(all(fan["manual_recovery_percent"] == 50 for fan in fans))

    async def test_dxp4800pro_clamps_low_pwm_and_forces_verified_manual_mode(self):
        coordinator = FakeCoordinator()
        manager = FanManager(coordinator)
        fan = {
            "name": "系统风扇",
            "pwm_path": "/sys/class/hwmon/hwmon5/pwm3",
            "pwm_enable_path": "/sys/class/hwmon/hwmon5/pwm3_enable",
            "supports_manual_mode": True,
            "supports_modes": True,
            "supports_pwm": True,
            "available_modes": [CONTROL_MODE_MANUAL, CONTROL_MODE_FULL_SPEED],
            "minimum_pwm_raw": 80,
            "minimum_pwm_percent": 31,
        }

        self.assertTrue(await manager.set_percentage(fan, 0))

        self.assertEqual(len(coordinator.commands), 1)
        self.assertIn("pwm3_enable", coordinator.commands[0])
        self.assertIn("'1'", coordinator.commands[0])
        self.assertIn("pwm3", coordinator.commands[0])
        self.assertIn("'80'", coordinator.commands[0])
        self.assertIn("sleep 1", coordinator.commands[0])
        self.assertEqual(fan["pwm_raw"], 80)
        self.assertEqual(fan["pwm_percent"], 31)
        self.assertEqual(fan["control_mode"], CONTROL_MODE_MANUAL)

    async def test_dxp4800pro_rejects_unverified_hardware_auto_mode(self):
        coordinator = FakeCoordinator()
        manager = FanManager(coordinator)
        fan = {
            "pwm_path": "/sys/class/hwmon/hwmon5/pwm3",
            "pwm_enable_path": "/sys/class/hwmon/hwmon5/pwm3_enable",
            "supports_manual_mode": True,
            "supports_modes": True,
            "supports_pwm": True,
            "available_modes": [CONTROL_MODE_MANUAL, CONTROL_MODE_FULL_SPEED],
        }

        self.assertFalse(await manager.set_mode(fan, CONTROL_MODE_AUTO))
        self.assertEqual(coordinator.commands, [])

    async def test_dxp4800pro_manual_mode_recovers_full_speed_to_50_percent(self):
        coordinator = FakeCoordinator()
        manager = FanManager(coordinator)
        fan = {
            "name": "系统风扇",
            "pwm_path": "/sys/class/hwmon/hwmon5/pwm3",
            "pwm_enable_path": "/sys/class/hwmon/hwmon5/pwm3_enable",
            "supports_manual_mode": True,
            "supports_modes": True,
            "supports_pwm": True,
            "available_modes": [CONTROL_MODE_MANUAL, CONTROL_MODE_FULL_SPEED],
            "minimum_pwm_raw": 80,
            "minimum_pwm_percent": 31,
            "manual_recovery_percent": 50,
            "control_mode": CONTROL_MODE_FULL_SPEED,
            "pwm_enable": 0,
            "pwm_raw": 255,
            "pwm_percent": 100,
        }

        self.assertTrue(await manager.set_mode(fan, CONTROL_MODE_MANUAL))

        self.assertEqual(len(coordinator.commands), 1)
        self.assertIn("pwm3_enable", coordinator.commands[0])
        self.assertIn("'1'", coordinator.commands[0])
        self.assertIn("pwm3", coordinator.commands[0])
        self.assertIn("'128'", coordinator.commands[0])
        self.assertEqual(fan["control_mode"], CONTROL_MODE_MANUAL)
        self.assertEqual(fan["pwm_percent"], 50)

    async def test_manual_pwm_transaction_records_delayed_readback_failure(self):
        coordinator = FakeCoordinator(
            ["__FN_NAS_ERROR__ mode=0 pwm=255"]
        )
        manager = FanManager(coordinator)
        fan = {
            "name": "系统风扇",
            "pwm_path": "/sys/class/hwmon/hwmon5/pwm3",
            "pwm_enable_path": "/sys/class/hwmon/hwmon5/pwm3_enable",
            "supports_manual_mode": True,
            "supports_modes": True,
            "supports_pwm": True,
        }

        with self.assertLogs(fan_manager._LOGGER, level="WARNING"):
            self.assertFalse(await manager.set_percentage(fan, 50))

        self.assertEqual(
            fan["last_control_result"],
            {
                "success": False,
                "requested_mode": 1,
                "requested_pwm": 128,
                "actual_mode": 0,
                "actual_pwm": 255,
            },
        )

    def test_sysfs_write_command_reads_back_the_requested_value(self):
        command = FanManager(FakeCoordinator())._build_write_command(
            "/sys/class/hwmon/hwmon5/pwm3_enable",
            1,
        )

        self.assertIn("actual=$(cat", command)
        self.assertIn('[ "$actual" = "$expected" ]', command)

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

        self.assertEqual(len(coordinator.commands), 1)
        self.assertIn("pwm1_enable", coordinator.commands[0])
        self.assertIn("'1'", coordinator.commands[0])
        self.assertIn("pwm1", coordinator.commands[0])
        self.assertIn("'128'", coordinator.commands[0])

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

        self.assertEqual(len(coordinator.commands), 2)
        self.assertIn("pwm1_enable", coordinator.commands[0])
        self.assertIn("'0'", coordinator.commands[0])
        self.assertIn("pwm1_enable", coordinator.commands[1])
        self.assertIn("'1'", coordinator.commands[1])
        self.assertIn("pwm1", coordinator.commands[1])
        self.assertIn("'255'", coordinator.commands[1])


if __name__ == "__main__":
    unittest.main()
