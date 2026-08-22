import importlib.util
import sys
import types
import unittest
from pathlib import Path
from unittest.mock import patch


SENSOR_PATH = (
    Path(__file__).resolve().parents[1]
    / "custom_components"
    / "fn_nas"
    / "sensor.py"
)


class StubCoordinatorEntity:
    def __init__(self, coordinator):
        self.coordinator = coordinator


class StubSensorEntity:
    pass


class StubSensorDeviceClass:
    BATTERY = "battery"
    TEMPERATURE = "temperature"


class StubSensorStateClass:
    MEASUREMENT = "measurement"


class StubUnitOfTemperature:
    CELSIUS = "°C"


class StubCoordinator:
    host = "192.168.2.86"

    data = {
        "fan_diagnostics": {
            "status": "未发现风扇",
            "fan_count": 0,
            "source": "none",
            "hwmon_entry_count": 0,
            "hwmon_inventory": [
                {
                    "path": "/sys/class/hwmon/hwmon0",
                    "device": "/sys/devices/platform/coretemp.0",
                    "chip": "coretemp",
                    "fan_files": [],
                    "pwm_files": [],
                }
            ],
            "sysfs_entry_count": 0,
            "sysfs_fan_candidates": [
                {"path": "/sys/devices/platform/fn_ec/fan1_input", "value": "1380"}
            ],
            "cooling_devices": [
                {
                    "path": "/sys/class/thermal/cooling_device0",
                    "type": "Fan",
                    "cur_state": 2,
                    "max_state": 10,
                    "writable": True,
                }
            ],
            "sensors_fan_lines": [],
            "sensors_u_fan_lines": [],
            "host_hardware": {
                "kernel": "6.12.18-trim",
                "product_name": "NAS Mini",
                "board_name": "N100-NAS",
            },
            "loaded_fan_modules": ["coretemp"],
            "available_fan_modules": ["nct6775"],
            "fan_services": ["thermal-daemon.service"],
            "fan_service_details": {
                "pwm-fancontrol.service": {
                    "active_state": "active",
                    "sub_state": "running",
                    "exec_start": "/usr/sbin/hwmonitor-480t",
                    "process_exe": "/usr/sbin/hwmonitor-480t",
                }
            },
            "vendor_fan_interfaces": [
                {
                    "path": "/proc/it86/fan",
                    "type": "file",
                    "readable": True,
                    "writable": True,
                }
            ],
            "fan_service_logs": ["Started PWM fan control."],
            "fan_startup_script": {
                "path": "/usr/trim/bin/pwm-fancontrol.sh",
                "syntax": "ok",
                "relevant_lines": [
                    {"line": 8, "text": "modprobe it87 force_id=0x8613"}
                ],
            },
            "it87_module_info": {
                "filename": "/lib/modules/6.18.18/kernel/drivers/hwmon/it87.ko",
                "parameters": ["force_id:Override chip ID"],
                "dry_run": ["insmod /lib/modules/6.18.18/kernel/drivers/hwmon/it87.ko"],
            },
            "fan_kernel_logs": ["it87: Found IT8613E chip at 0xa30"],
            "fan_control_app": {
                "installed": False,
                "listening": False,
                "port": 9511,
                "api_status": "",
            },
            "diagnostic_tools": {"sensors-detect": True},
            "hint": "没有发现风扇",
        }
    }


def _install_stubs():
    homeassistant = types.ModuleType("homeassistant")
    components = types.ModuleType("homeassistant.components")
    sensor_component = types.ModuleType("homeassistant.components.sensor")
    sensor_component.SensorEntity = StubSensorEntity
    sensor_component.SensorDeviceClass = StubSensorDeviceClass
    sensor_component.SensorStateClass = StubSensorStateClass
    helpers = types.ModuleType("homeassistant.helpers")
    update_coordinator = types.ModuleType("homeassistant.helpers.update_coordinator")
    update_coordinator.CoordinatorEntity = StubCoordinatorEntity
    const = types.ModuleType("homeassistant.const")
    const.UnitOfTemperature = StubUnitOfTemperature

    custom_components = types.ModuleType("custom_components")
    fn_nas = types.ModuleType("custom_components.fn_nas")
    fn_nas.__path__ = []
    fn_nas_const = types.ModuleType("custom_components.fn_nas.const")
    fn_nas_const.DOMAIN = "fn_nas"
    fn_nas_const.HDD_TEMP = "temperature"
    fn_nas_const.HDD_HEALTH = "health"
    fn_nas_const.HDD_STATUS = "status"
    fn_nas_const.SYSTEM_INFO = "system"
    fn_nas_const.ICON_DISK = "mdi:harddisk"
    fn_nas_const.ICON_TEMPERATURE = "mdi:thermometer"
    fn_nas_const.ICON_HEALTH = "mdi:heart-pulse"
    fn_nas_const.ATTR_DISK_MODEL = "硬盘型号"
    fn_nas_const.ATTR_SERIAL_NO = "序列号"
    fn_nas_const.ATTR_POWER_ON_HOURS = "通电时间"
    fn_nas_const.ATTR_TOTAL_CAPACITY = "总容量"
    fn_nas_const.ATTR_HEALTH_STATUS = "健康状态"
    fn_nas_const.DEVICE_ID_NAS = "flynas_nas_system"
    fn_nas_const.DATA_UPDATE_COORDINATOR = "coordinator"
    fn_nas_const.ICON_FAN = "mdi:fan"
    fn_nas_const.FAN_RPM = "fan_rpm"
    fn_nas_const.FAN_PWM = "fan_pwm"
    fn_nas_const.FAN_CONTROL_MODE = "fan_control_mode"
    fn_nas_const.FAN_DISCOVERY = "fan_discovery"

    return {
        "homeassistant": homeassistant,
        "homeassistant.components": components,
        "homeassistant.components.sensor": sensor_component,
        "homeassistant.helpers": helpers,
        "homeassistant.helpers.update_coordinator": update_coordinator,
        "homeassistant.const": const,
        "custom_components": custom_components,
        "custom_components.fn_nas": fn_nas,
        "custom_components.fn_nas.const": fn_nas_const,
    }


class FanDiscoverySensorTests(unittest.TestCase):
    def test_fan_discovery_sensor_exposes_diagnostics_attributes(self):
        with patch.dict(sys.modules, _install_stubs()):
            spec = importlib.util.spec_from_file_location(
                "custom_components.fn_nas.sensor",
                SENSOR_PATH,
            )
            module = importlib.util.module_from_spec(spec)
            sys.modules["custom_components.fn_nas.sensor"] = module
            spec.loader.exec_module(module)

            entity = module.FanDiscoverySensor(StubCoordinator(), "entry-1")

        self.assertEqual(entity.native_value, "未发现风扇")
        self.assertEqual(entity._attr_unique_id, "entry-1_fan_discovery_status")
        self.assertEqual(entity.extra_state_attributes["来源"], "none")
        self.assertEqual(entity.extra_state_attributes["风扇数量"], 0)
        self.assertEqual(
            entity.extra_state_attributes["hwmon候选"][0]["path"],
            "/sys/class/hwmon/hwmon0",
        )
        self.assertEqual(
            entity.extra_state_attributes["sysfs候选"][0]["path"],
            "/sys/devices/platform/fn_ec/fan1_input",
        )
        self.assertEqual(entity.extra_state_attributes["散热设备"][0]["type"], "Fan")
        self.assertEqual(entity.extra_state_attributes["主机硬件"]["board_name"], "N100-NAS")
        self.assertEqual(entity.extra_state_attributes["已加载风扇模块"], ["coretemp"])
        self.assertEqual(entity.extra_state_attributes["可用风扇模块"], ["nct6775"])
        self.assertEqual(entity.extra_state_attributes["相关服务"], ["thermal-daemon.service"])
        self.assertEqual(
            entity.extra_state_attributes["风扇服务详情"]["pwm-fancontrol.service"]["active_state"],
            "active",
        )
        self.assertEqual(
            entity.extra_state_attributes["厂商风扇接口"][0]["path"],
            "/proc/it86/fan",
        )
        self.assertEqual(entity.extra_state_attributes["风扇服务日志"], ["Started PWM fan control."])
        self.assertEqual(
            entity.extra_state_attributes["风扇启动脚本"]["relevant_lines"][0]["line"],
            8,
        )
        self.assertEqual(
            entity.extra_state_attributes["it87模块信息"]["parameters"],
            ["force_id:Override chip ID"],
        )
        self.assertEqual(
            entity.extra_state_attributes["风扇内核日志"],
            ["it87: Found IT8613E chip at 0xa30"],
        )
        self.assertFalse(entity.extra_state_attributes["风扇控制应用"]["installed"])
        self.assertEqual(entity.extra_state_attributes["诊断工具"], {"sensors-detect": True})


if __name__ == "__main__":
    unittest.main()
