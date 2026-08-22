import hashlib
import logging
import re
import shlex

_LOGGER = logging.getLogger(__name__)

CONTROL_MODE_AUTO = "自动"
CONTROL_MODE_MANUAL = "手动"
CONTROL_MODE_FULL_SPEED = "全速"
CONTROL_MODE_UNKNOWN = "未知"

PWM_ENABLE_TO_MODE = {
    0: CONTROL_MODE_FULL_SPEED,
    1: CONTROL_MODE_MANUAL,
    2: CONTROL_MODE_AUTO,
}

MODE_TO_PWM_ENABLE = {
    CONTROL_MODE_FULL_SPEED: 0,
    CONTROL_MODE_MANUAL: 1,
    CONTROL_MODE_AUTO: 2,
}

OK_TOKEN = "__FN_NAS_OK__"
ERROR_TOKEN = "__FN_NAS_ERROR__"

DXP4800PRO_MINIMUM_PWM_RAW = 80


class FanManager:
    """Read and control fan hwmon data through the coordinator SSH layer."""

    def __init__(self, coordinator):
        self.coordinator = coordinator
        self.last_diagnostics = self._empty_diagnostics()

    async def get_fans_info(self) -> list[dict]:
        """Discover fan sensors and PWM controls from Linux hwmon."""
        hwmon_output = ""
        sensors_output = ""
        sensors_u_output = ""
        sysfs_output = ""
        inventory_output = ""

        try:
            hwmon_output = await self.coordinator.run_command(self._build_discovery_command())
            fans = self.parse_hwmon_snapshot(hwmon_output) if hwmon_output else []
            if fans:
                self.last_diagnostics = self._build_diagnostics(
                    "hwmon", fans, hwmon_output, sensors_output, sensors_u_output, sysfs_output, inventory_output
                )
                return fans

            sensors_output = await self.coordinator.run_command("sensors 2>/dev/null || true")
            fans = self.parse_sensors_output(sensors_output) if sensors_output else []
            if fans:
                self.last_diagnostics = self._build_diagnostics(
                    "sensors", fans, hwmon_output, sensors_output, sensors_u_output, sysfs_output, inventory_output
                )
                return fans

            sensors_u_output = await self.coordinator.run_command("sensors -u 2>/dev/null || true")
            fans = self.parse_sensors_output(sensors_u_output) if sensors_u_output else []
            if fans:
                self.last_diagnostics = self._build_diagnostics(
                    "sensors -u", fans, hwmon_output, sensors_output, sensors_u_output, sysfs_output, inventory_output
                )
                return fans

            sysfs_output = await self.coordinator.run_command(self._build_sysfs_discovery_command())
            fans = self.parse_sysfs_snapshot(sysfs_output) if sysfs_output else []
            if fans:
                self.last_diagnostics = self._build_diagnostics(
                    "sysfs", fans, hwmon_output, sensors_output, sensors_u_output, sysfs_output, inventory_output
                )
                return fans

            inventory_output = await self.coordinator.run_command(self._build_inventory_command())
            self.last_diagnostics = self._build_diagnostics(
                "none", [], hwmon_output, sensors_output, sensors_u_output, sysfs_output, inventory_output
            )
            return []
        except Exception as e:
            _LOGGER.debug("获取风扇信息失败: %s", str(e))
            self.last_diagnostics = self._build_diagnostics(
                "error", [], hwmon_output, sensors_output, sensors_u_output, sysfs_output, inventory_output, str(e)
            )
            return []

    def parse_hwmon_snapshot(self, output: str) -> list[dict]:
        """Parse tab-separated hwmon records produced by the discovery command."""
        fans = []
        seen_ids = set()

        for line in output.splitlines():
            if not line.startswith("entry\t"):
                continue

            parts = line.split("\t")
            if len(parts) < 12:
                _LOGGER.debug("跳过无法解析的风扇记录: %s", line)
                continue

            (
                _record_type,
                hwmon_path,
                device_path,
                chip_name,
                index_text,
                rpm_text,
                label,
                has_pwm_text,
                pwm_text,
                pwm_enable_text,
                pwm_writable_text,
                mode_writable_text,
            ) = parts[:12]

            system_vendor = parts[12].strip() if len(parts) > 12 else ""
            product_name = parts[13].strip() if len(parts) > 13 else ""

            index = self._to_int(index_text)
            if index is None:
                continue
            if not self._is_supported_fan_channel(
                chip_name,
                system_vendor,
                product_name,
                index,
            ):
                continue

            rpm = self._to_int(rpm_text)
            pwm_raw = self._to_int(pwm_text)
            pwm_enable = self._to_int(pwm_enable_text)
            has_pwm = has_pwm_text == "1"
            pwm_writable = pwm_writable_text == "1"
            mode_writable = mode_writable_text == "1"
            fan_id = self._make_fan_id(chip_name, device_path, index, label)

            if fan_id in seen_ids:
                continue
            seen_ids.add(fan_id)

            name = label.strip() if label.strip() else self._default_fan_name(
                chip_name,
                system_vendor,
                product_name,
                index,
            )
            control_mode = self._control_mode_from_enable(pwm_enable)
            pwm_path = f"{hwmon_path}/pwm{index}" if has_pwm else None
            pwm_enable_path = (
                f"{hwmon_path}/pwm{index}_enable"
                if pwm_enable is not None
                else None
            )
            supports_pwm = has_pwm and pwm_writable
            supports_manual_mode = pwm_enable_path is not None and mode_writable
            is_dxp4800pro = self._is_dxp4800pro_it8613(
                chip_name,
                system_vendor,
                product_name,
            )
            control_capabilities = self._control_capabilities(
                supports_pwm,
                supports_manual_mode,
                is_dxp4800pro,
            )

            fans.append(
                {
                    "id": fan_id,
                    "name": name,
                    "index": index,
                    "chip": chip_name or "unknown",
                    "hwmon_path": hwmon_path,
                    "device_path": device_path,
                    "fan_input_path": f"{hwmon_path}/fan{index}_input",
                    "pwm_path": pwm_path,
                    "pwm_enable_path": pwm_enable_path,
                    "rpm": rpm,
                    "pwm_raw": pwm_raw,
                    "pwm_percent": self._pwm_raw_to_percent(pwm_raw) if has_pwm else None,
                    "pwm_enable": pwm_enable,
                    "control_mode": control_mode,
                    "supports_pwm": supports_pwm,
                    **control_capabilities,
                }
            )

        return fans

    def parse_sysfs_snapshot(self, output: str) -> list[dict]:
        """Parse direct /sys fan records not linked through /sys/class/hwmon."""
        fans = []
        seen_ids = set()

        for line in output.splitlines():
            if not line.startswith("sysfs\t"):
                continue

            parts = (line.split("\t") + [""] * 17)[:17]
            (
                _record_type,
                sysfs_dir,
                device_path,
                chip_name,
                index_text,
                rpm_text,
                label,
                has_pwm_text,
                pwm_text,
                pwm_enable_text,
                pwm_writable_text,
                mode_writable_text,
                fan_input_path,
                pwm_path,
                pwm_enable_path,
                system_vendor,
                product_name,
            ) = parts

            index = self._to_int(index_text) or len(fans) + 1
            if not self._is_supported_fan_channel(
                chip_name,
                system_vendor,
                product_name,
                index,
            ):
                continue
            rpm = self._to_int(rpm_text)
            pwm_raw = self._to_int(pwm_text)
            pwm_enable = self._to_int(pwm_enable_text)
            has_pwm = has_pwm_text == "1"
            pwm_writable = pwm_writable_text == "1"
            mode_writable = mode_writable_text == "1"
            name = label.strip() if label.strip() else self._default_fan_name(
                chip_name,
                system_vendor,
                product_name,
                index,
            )
            fan_id = self._make_fan_id(chip_name or "sysfs", device_path or sysfs_dir, index, name)

            if fan_id in seen_ids:
                continue
            seen_ids.add(fan_id)

            supports_pwm = has_pwm and pwm_writable and bool(pwm_path)
            supports_manual_mode = bool(pwm_enable_path) and mode_writable
            is_dxp4800pro = self._is_dxp4800pro_it8613(
                chip_name,
                system_vendor,
                product_name,
            )
            control_capabilities = self._control_capabilities(
                supports_pwm,
                supports_manual_mode,
                is_dxp4800pro,
            )

            fans.append(
                {
                    "id": fan_id,
                    "name": name,
                    "index": index,
                    "chip": chip_name or "sysfs",
                    "hwmon_path": sysfs_dir,
                    "device_path": device_path or sysfs_dir,
                    "fan_input_path": fan_input_path or None,
                    "pwm_path": pwm_path if has_pwm and pwm_path else None,
                    "pwm_enable_path": pwm_enable_path if pwm_enable_path else None,
                    "rpm": rpm,
                    "pwm_raw": pwm_raw,
                    "pwm_percent": self._pwm_raw_to_percent(pwm_raw) if has_pwm else None,
                    "pwm_enable": pwm_enable,
                    "control_mode": self._control_mode_from_enable(pwm_enable),
                    "supports_pwm": supports_pwm,
                    **control_capabilities,
                }
            )

        return fans

    def parse_sensors_output(self, output: str) -> list[dict]:
        """Parse fan RPM lines from lm-sensors output as a monitor-only fallback."""
        fans = []
        seen_ids = set()
        current_section = None

        def add_fan(label: str, rpm_text: str) -> None:
            rpm = self._to_int(rpm_text)
            if rpm is None:
                return

            fan_id = self._make_fan_id("sensors", "lm-sensors", len(fans) + 1, label)
            if fan_id in seen_ids:
                return
            seen_ids.add(fan_id)

            fans.append(
                {
                    "id": fan_id,
                    "name": label or f"风扇 {len(fans) + 1}",
                    "index": len(fans) + 1,
                    "chip": "sensors",
                    "hwmon_path": None,
                    "device_path": "lm-sensors",
                    "fan_input_path": None,
                    "pwm_path": None,
                    "pwm_enable_path": None,
                    "rpm": rpm,
                    "pwm_raw": None,
                    "pwm_percent": None,
                    "pwm_enable": None,
                    "control_mode": None,
                    "supports_pwm": False,
                    "supports_manual_mode": False,
                    "supports_modes": False,
                    "supports_auto_mode": False,
                    "available_modes": [],
                    "minimum_pwm_raw": None,
                    "minimum_pwm_percent": None,
                    "manual_recovery_percent": None,
                }
            )

        for line in output.splitlines():
            stripped = line.strip()
            if not stripped:
                continue

            section_match = re.match(r"^([^:]*fan[^:]*)\s*:\s*$", stripped, re.IGNORECASE)
            if section_match:
                current_section = section_match.group(1).strip()
                continue

            rpm_match = re.match(r"^\s*([^:]+?)\s*:\s*([0-9,.]+)\s*RPM\b", line, re.IGNORECASE)
            if rpm_match:
                add_fan(rpm_match.group(1).strip(), rpm_match.group(2))
                continue

            input_match = re.match(
                r"^\s*((?:[^:]*fan[^:]*)_input)\s*:\s*([0-9,.]+)\b",
                line,
                re.IGNORECASE,
            )
            if input_match:
                label = current_section or input_match.group(1).strip().removesuffix("_input")
                add_fan(label, input_match.group(2))

        return fans

    def parse_hwmon_inventory(self, output: str) -> list[dict]:
        """Parse bounded hwmon inventory records for user-visible diagnostics."""
        inventory = []
        for line in output.splitlines():
            if not line.startswith("hwmon\t"):
                continue

            parts = (line.split("\t") + ["", "", "", "", "", ""])[:6]
            _record_type, path, device, chip, fan_files, pwm_files = parts
            inventory.append(
                {
                    "path": path,
                    "device": device,
                    "chip": chip or "unknown",
                    "fan_files": fan_files.split() if fan_files else [],
                    "pwm_files": pwm_files.split() if pwm_files else [],
                }
            )

            if len(inventory) >= 20:
                break

        return inventory

    def parse_sysfs_candidates(self, output: str) -> list[dict]:
        """Parse bounded raw /sys fan/PWM paths for diagnostics."""
        candidates = []
        for line in output.splitlines():
            if not line.startswith("candidate\t"):
                continue

            parts = (line.split("\t") + ["", ""])[:3]
            _record_type, path, value = parts
            candidates.append({"path": path, "value": value})
            if len(candidates) >= 80:
                break

        return candidates

    def parse_cooling_devices(self, output: str) -> list[dict]:
        """Parse Linux thermal cooling devices for diagnostics only."""
        cooling_devices = []
        for line in output.splitlines():
            if not line.startswith("cooling\t"):
                continue

            parts = (line.split("\t") + ["", "", "", "", ""])[:6]
            _record_type, path, device_type, cur_state, max_state, writable = parts
            cooling_devices.append(
                {
                    "path": path,
                    "type": device_type,
                    "cur_state": self._to_int(cur_state),
                    "max_state": self._to_int(max_state),
                    "writable": writable == "1",
                }
            )
            if len(cooling_devices) >= 40:
                break

        return cooling_devices

    def parse_host_hardware(self, output: str) -> dict:
        """Parse a non-sensitive DMI and kernel inventory."""
        allowed_fields = {
            "kernel",
            "sys_vendor",
            "product_name",
            "product_version",
            "board_vendor",
            "board_name",
            "board_version",
            "bios_vendor",
            "bios_version",
        }
        hardware = {}
        for line in output.splitlines():
            if not line.startswith("host\t"):
                continue
            parts = (line.split("\t", 2) + ["", ""])[:3]
            _record_type, key, value = parts
            value = value.strip()
            if key in allowed_fields and value:
                hardware[key] = value[:160]
        return hardware

    def parse_driver_modules(self, output: str, state: str) -> list[str]:
        """Parse loaded or installed candidate fan/thermal kernel modules."""
        modules = []
        prefix = f"module\t{state}\t"
        for line in output.splitlines():
            if not line.startswith(prefix):
                continue
            name = line[len(prefix):].strip()
            if name and name not in modules:
                modules.append(name[:80])
            if len(modules) >= 40:
                break
        return modules

    def parse_fan_services(self, output: str) -> list[str]:
        """Parse bounded service names without exposing process arguments."""
        services = []
        for line in output.splitlines():
            if not line.startswith("service\t"):
                continue
            name = line.split("\t", 1)[1].strip()
            if name and name not in services:
                services.append(name[:120])
            if len(services) >= 30:
                break
        return services

    def parse_fan_service_details(self, output: str) -> dict[str, dict]:
        """Parse bounded systemd properties for detected fan services."""
        property_names = {
            "LoadState": "load_state",
            "ActiveState": "active_state",
            "SubState": "sub_state",
            "UnitFileState": "unit_file_state",
            "FragmentPath": "fragment_path",
            "MainPID": "main_pid",
            "ExecMainStatus": "exec_main_status",
            "ExecStart": "exec_start",
            "ProcessExe": "process_exe",
        }
        numeric_properties = {"MainPID", "ExecMainStatus"}
        details = {}

        for line in output.splitlines():
            if not line.startswith("serviceprop\t"):
                continue
            parts = (line.split("\t", 3) + ["", "", "", ""])[:4]
            _record_type, service, property_name, value = parts
            key = property_names.get(property_name)
            if not service or not key:
                continue
            parsed_value = (
                self._to_int(value)
                if property_name in numeric_properties
                else value.strip()[:240]
            )
            if parsed_value is None or parsed_value == "":
                continue
            details.setdefault(service[:120], {})[key] = parsed_value

        return details

    def parse_vendor_fan_interfaces(self, output: str) -> list[dict]:
        """Parse presence and permissions of known UGREEN fan interfaces."""
        interfaces = []
        for line in output.splitlines():
            if not line.startswith("vendor\t"):
                continue
            parts = (line.split("\t") + ["", "", "", "", ""])[:5]
            _record_type, path, interface_type, readable, writable = parts
            if not path:
                continue
            interfaces.append(
                {
                    "path": path[:200],
                    "type": interface_type[:40],
                    "readable": readable == "1",
                    "writable": writable == "1",
                }
            )
            if len(interfaces) >= 20:
                break
        return interfaces

    def parse_fan_service_logs(self, output: str) -> list[str]:
        """Parse recent, bounded log messages from the stock fan service."""
        logs = []
        for line in output.splitlines():
            if not line.startswith("servicelog\t"):
                continue
            parts = (line.split("\t", 2) + ["", "", ""])[:3]
            _record_type, _service, message = parts
            message = message.strip()
            if message:
                logs.append(message[:240])
            if len(logs) >= 20:
                break
        return logs

    def parse_fan_startup_script(self, output: str) -> dict:
        """Parse metadata and redacted relevant lines from the fnOS fan script."""
        script = {}
        relevant_lines = []
        numeric_fields = {"size"}

        for line in output.splitlines():
            if line.startswith("fanscript\t"):
                parts = (line.split("\t", 2) + ["", "", ""])[:3]
                _record_type, key, value = parts
                if key in {"path", "size", "mode", "owner", "sha256", "syntax"}:
                    parsed_value = self._to_int(value) if key in numeric_fields else value.strip()[:240]
                    if parsed_value is not None and parsed_value != "":
                        script[key] = parsed_value
            elif line.startswith("fanscriptline\t"):
                parts = (line.split("\t", 2) + ["", "", ""])[:3]
                _record_type, line_number, text = parts
                parsed_line_number = self._to_int(line_number)
                text = text.strip()
                if parsed_line_number is not None and text:
                    relevant_lines.append({"line": parsed_line_number, "text": text[:240]})
                if len(relevant_lines) >= 80:
                    break

        if relevant_lines:
            script["relevant_lines"] = relevant_lines
        return script

    def parse_board_fan_config(self, output: str) -> dict:
        """Parse the fnOS board fan configuration and configured path state."""
        config = {}
        fans = []
        fans_by_index = {}

        for line in output.splitlines():
            if line.startswith("boardconfig\t"):
                parts = (line.split("\t", 2) + ["", ""])[:3]
                _record_type, key, value = parts
                if key == "path" and value:
                    config[key] = value.strip()[:240]
                elif key in {"readable", "json_valid"}:
                    config[key] = value.strip().lower() in {"1", "true", "yes"}
                elif key == "fan_count":
                    fan_count = self._to_int(value)
                    if fan_count is not None:
                        config[key] = fan_count
            elif line.startswith("boardfan\t"):
                parts = (line.split("\t") + [""] * 10)[:10]
                (
                    _record_type,
                    index_text,
                    name,
                    temp_path,
                    fan_path,
                    start_speed,
                    start_temp,
                    max_speed,
                    temp_div,
                    verbose,
                ) = parts
                index = self._to_int(index_text)
                if index is None or len(fans) >= 20:
                    continue

                fan = {
                    "index": index,
                    "name": name.strip()[:160] or f"风扇 {index + 1}",
                    "tsysfs": temp_path.strip()[:240],
                    "fsysfs": fan_path.strip()[:240],
                }
                for key, value in (
                    ("start_speed", start_speed),
                    ("start_temp", start_temp),
                    ("max_speed", max_speed),
                    ("temp_div", temp_div),
                ):
                    parsed_value = self._to_int(value)
                    if parsed_value is not None:
                        fan[key] = parsed_value
                if verbose.strip():
                    fan["verbose"] = verbose.strip().lower() in {"1", "true", "yes"}

                fans.append(fan)
                fans_by_index[index] = fan

        for line in output.splitlines():
            if not line.startswith("boardpath\t"):
                continue
            parts = (line.split("\t", 7) + [""] * 8)[:8]
            (
                _record_type,
                index_text,
                path_kind,
                path,
                exists,
                readable,
                writable,
                value,
            ) = parts
            index = self._to_int(index_text)
            fan = fans_by_index.get(index)
            if fan is None or path_kind not in {"tsysfs", "fsysfs"}:
                continue

            path_info = {
                "path": path.strip()[:240],
                "exists": exists == "1",
                "readable": readable == "1",
                "writable": writable == "1",
            }
            if value.strip():
                path_info["value"] = value.strip()[:160]
            fan.setdefault("paths", {})[path_kind] = path_info

        if fans:
            config["fans"] = fans
        return config

    def parse_fancontrol_runtime(self, output: str) -> dict:
        """Parse metadata and running process state for the fnOS fan controller."""
        binary = {}
        processes = []

        for line in output.splitlines():
            if line.startswith("fanbinary\t"):
                parts = (line.split("\t", 2) + ["", ""])[:3]
                _record_type, key, value = parts
                if key == "size":
                    parsed_value = self._to_int(value)
                    if parsed_value is not None:
                        binary[key] = parsed_value
                elif key in {"path", "mode", "owner", "sha256", "file_type"} and value.strip():
                    binary[key] = value.strip()[:240]
            elif line.startswith("fanprocess\t"):
                parts = (line.split("\t", 5) + [""] * 6)[:6]
                _record_type, pid_text, executable, state, uid_text, command_line = parts
                pid = self._to_int(pid_text)
                uid = self._to_int(uid_text)
                if pid is None or len(processes) >= 10:
                    continue
                processes.append(
                    {
                        "pid": pid,
                        "exe": executable.strip()[:240],
                        "state": state.strip()[:40],
                        "uid": uid,
                        "cmdline": command_line.strip()[:240],
                    }
                )

        return {
            "binary": binary,
            "process_count": len(processes),
            "processes": processes,
        }

    def parse_it87_module_info(self, output: str) -> dict:
        """Parse installed it87 metadata and dry-run module loading output."""
        info = {}
        parameters = []
        dry_run = []

        for line in output.splitlines():
            if line.startswith("it87info\t"):
                parts = (line.split("\t", 2) + ["", "", ""])[:3]
                _record_type, key, value = parts
                value = value.strip()
                if key in {"filename", "version", "description", "vermagic"} and value:
                    info[key] = value[:240]
            elif line.startswith("it87parm\t"):
                value = line.split("\t", 1)[1].strip()
                if value and len(parameters) < 40:
                    parameters.append(value[:240])
            elif line.startswith("it87dry\t"):
                value = line.split("\t", 1)[1].strip()
                if value and len(dry_run) < 20:
                    dry_run.append(value[:240])

        if parameters:
            info["parameters"] = parameters
        if dry_run:
            info["dry_run"] = dry_run
        return info

    def parse_fan_kernel_logs(self, output: str) -> list[str]:
        """Parse bounded kernel messages related to fan controller discovery."""
        logs = []
        for line in output.splitlines():
            if not line.startswith("kernellog\t"):
                continue
            message = line.split("\t", 1)[1].strip()
            if message:
                logs.append(message[:240])
            if len(logs) >= 40:
                break
        return logs

    def parse_fan_control_app(self, output: str) -> dict:
        """Parse the optional fnOS fan-control FPK availability."""
        app = {
            "installed": False,
            "listening": False,
            "port": 9511,
            "api_status": "",
        }

        for line in output.splitlines():
            if line.startswith("app\tfan-control\t"):
                parts = (line.split("\t") + ["", "", "", "", ""])[:5]
                _record_type, _app_name, installed, listening, port = parts
                app["installed"] = installed == "1"
                app["listening"] = listening == "1"
                app["port"] = self._to_int(port) or 9511
            elif line.startswith("api\t"):
                parts = (line.split("\t", 2) + ["", "", ""])[:3]
                _record_type, port, status = parts
                if (self._to_int(port) or 9511) == app["port"]:
                    app["api_status"] = status[:240]

        return app

    def parse_diagnostic_tools(self, output: str) -> dict[str, bool]:
        """Parse presence checks for safe, read-only diagnostic tools."""
        tools = {}
        for line in output.splitlines():
            if not line.startswith("tool\t"):
                continue
            parts = (line.split("\t") + ["", "", ""])[:3]
            _record_type, name, available = parts
            if name:
                tools[name[:80]] = available == "1"
        return tools

    async def set_percentage(self, fan: dict, percentage: int | float) -> bool:
        """Set a PWM fan to a Home Assistant percentage value."""
        if not fan.get("supports_pwm") or not fan.get("pwm_path"):
            return False

        pwm_percent = max(0, min(100, int(round(float(percentage)))))
        pwm_raw = self._percent_to_pwm_raw(pwm_percent)
        minimum_pwm_raw = self._to_int(fan.get("minimum_pwm_raw"))
        if minimum_pwm_raw is not None and pwm_raw < minimum_pwm_raw:
            pwm_raw = minimum_pwm_raw
            pwm_percent = self._pwm_raw_to_percent(pwm_raw)

        # Most hwmon drivers require manual mode before pwmN writes take effect.
        supports_manual_mode = fan.get(
            "supports_manual_mode",
            fan.get("supports_modes", False),
        )
        if supports_manual_mode and fan.get("pwm_enable_path"):
            if not await self._write_sysfs(fan["pwm_enable_path"], MODE_TO_PWM_ENABLE[CONTROL_MODE_MANUAL]):
                _LOGGER.warning("无法将风扇 %s 切换到手动模式", fan.get("name", fan.get("id")))
                return False

        if not await self._write_sysfs(fan["pwm_path"], pwm_raw):
            _LOGGER.warning("无法写入风扇 %s PWM 值", fan.get("name", fan.get("id")))
            return False

        fan["pwm_raw"] = pwm_raw
        fan["pwm_percent"] = pwm_percent
        fan["control_mode"] = CONTROL_MODE_MANUAL
        fan["pwm_enable"] = MODE_TO_PWM_ENABLE[CONTROL_MODE_MANUAL]
        return True

    async def set_mode(self, fan: dict, mode: str) -> bool:
        """Set the hwmon control mode when pwmN_enable is writable."""
        if mode not in MODE_TO_PWM_ENABLE:
            return False

        available_modes = fan.get("available_modes")
        if available_modes is not None and mode not in available_modes:
            _LOGGER.debug(
                "风扇 %s 未启用未经验证的硬件模式: %s",
                fan.get("name", fan.get("id")),
                mode,
            )
            return False

        supports_manual_mode = fan.get(
            "supports_manual_mode",
            fan.get("supports_modes", False),
        )

        if mode == CONTROL_MODE_FULL_SPEED:
            if fan.get("supports_modes") and fan.get("pwm_enable_path"):
                if await self._write_sysfs(fan["pwm_enable_path"], MODE_TO_PWM_ENABLE[mode]):
                    fan["control_mode"] = mode
                    fan["pwm_enable"] = MODE_TO_PWM_ENABLE[mode]
                    fan["pwm_percent"] = 100
                    fan["pwm_raw"] = 255
                    return True

                _LOGGER.debug("pwm_enable=0 不可用，回退为手动 100%% PWM")

            return await self.set_percentage(fan, 100)

        if not supports_manual_mode or not fan.get("pwm_enable_path"):
            return False

        recovery_percent = self._to_int(fan.get("manual_recovery_percent"))
        if (
            mode == CONTROL_MODE_MANUAL
            and recovery_percent is not None
            and (
                fan.get("control_mode") == CONTROL_MODE_FULL_SPEED
                or fan.get("pwm_enable") == MODE_TO_PWM_ENABLE[CONTROL_MODE_FULL_SPEED]
                or (self._to_int(fan.get("pwm_percent")) or 0) >= 100
            )
        ):
            return await self.set_percentage(fan, recovery_percent)

        if not await self._write_sysfs(fan["pwm_enable_path"], MODE_TO_PWM_ENABLE[mode]):
            return False

        fan["control_mode"] = mode
        fan["pwm_enable"] = MODE_TO_PWM_ENABLE[mode]
        return True

    def _build_discovery_command(self) -> str:
        script = r'''
system_vendor="$(cat /sys/class/dmi/id/sys_vendor 2>/dev/null | tr "\t\r\n" "   " | cut -c1-160)"
product_name="$(cat /sys/class/dmi/id/product_name 2>/dev/null | tr "\t\r\n" "   " | cut -c1-160)"
printf "host\tkernel\t%s\n" "$(uname -r 2>/dev/null | tr "\t\r\n" "   " | cut -c1-160)"
[ -n "$system_vendor" ] && printf "host\tsys_vendor\t%s\n" "$system_vendor"
[ -n "$product_name" ] && printf "host\tproduct_name\t%s\n" "$product_name"

for hwmon in /sys/class/hwmon/hwmon*; do
    [ -d "$hwmon" ] || continue
    chip="$(cat "$hwmon/name" 2>/dev/null || true)"
    device="$(readlink -f "$hwmon/device" 2>/dev/null || readlink -f "$hwmon" 2>/dev/null || printf "%s" "$hwmon")"

    print_entry() {
        idx="$1"
        if [ "$chip" = "it8613" ] && [ "$system_vendor" = "UGREEN" ] && [ "$product_name" = "DXP4800 Pro" ]; then
            case "$idx" in
                2|3) ;;
                *) return ;;
            esac
        fi
        rpm=""
        label=""
        has_pwm=0
        pwm=""
        pwm_enable=""
        pwm_writable=0
        mode_writable=0

        [ -r "$hwmon/fan${idx}_input" ] && rpm="$(cat "$hwmon/fan${idx}_input" 2>/dev/null || true)"
        [ -r "$hwmon/fan${idx}_label" ] && label="$(cat "$hwmon/fan${idx}_label" 2>/dev/null | tr "\t\n" "  " || true)"
        [ -z "$label" ] && [ -r "$hwmon/pwm${idx}_label" ] && label="$(cat "$hwmon/pwm${idx}_label" 2>/dev/null | tr "\t\n" "  " || true)"

        if [ -e "$hwmon/pwm${idx}" ]; then
            has_pwm=1
            pwm="$(cat "$hwmon/pwm${idx}" 2>/dev/null || true)"
            [ -w "$hwmon/pwm${idx}" ] && pwm_writable=1
        fi

        if [ -e "$hwmon/pwm${idx}_enable" ]; then
            pwm_enable="$(cat "$hwmon/pwm${idx}_enable" 2>/dev/null || true)"
            [ -w "$hwmon/pwm${idx}_enable" ] && mode_writable=1
        fi

        printf "entry\t%s\t%s\t%s\t%s\t%s\t%s\t%s\t%s\t%s\t%s\t%s\t%s\t%s\n" \
            "$hwmon" "$device" "$chip" "$idx" "$rpm" "$label" \
            "$has_pwm" "$pwm" "$pwm_enable" "$pwm_writable" "$mode_writable" \
            "$system_vendor" "$product_name"
    }

    for fan_input in "$hwmon"/fan*_input; do
        [ -e "$fan_input" ] || continue
        base="${fan_input##*/}"
        idx="${base#fan}"
        idx="${idx%_input}"
        print_entry "$idx"
    done

    for pwm_file in "$hwmon"/pwm[0-9]*; do
        [ -e "$pwm_file" ] || continue
        case "$pwm_file" in
            *_enable|*_mode|*_freq) continue ;;
        esac
        base="${pwm_file##*/}"
        idx="${base#pwm}"
        case "$idx" in
            ""|*[!0-9]*) continue ;;
        esac
        [ -e "$hwmon/fan${idx}_input" ] && continue
        print_entry "$idx"
    done
done
'''
        return f"sh -c {shlex.quote(script)}"

    def _build_sysfs_discovery_command(self) -> str:
        script = r'''
system_vendor="$(cat /sys/class/dmi/id/sys_vendor 2>/dev/null | tr "\t\r\n" "   " | cut -c1-160)"
product_name="$(cat /sys/class/dmi/id/product_name 2>/dev/null | tr "\t\r\n" "   " | cut -c1-160)"
seen_dirs=""

emit_dir() {
    dir="$1"
    [ -d "$dir" ] || return
    case " $seen_dirs " in
        *" $dir "*) return ;;
    esac
    seen_dirs="${seen_dirs} ${dir}"

    chip="$(cat "$dir/name" 2>/dev/null || basename "$dir")"
    device="$(readlink -f "$dir" 2>/dev/null || printf "%s" "$dir")"
    printed_indexes=""
    fallback_idx=100

    print_entry() {
        idx="$1"
        fan_path="$2"
        label="$3"
        if [ "$chip" = "it8613" ] && [ "$system_vendor" = "UGREEN" ] && [ "$product_name" = "DXP4800 Pro" ]; then
            case "$idx" in
                2|3) ;;
                *) return ;;
            esac
        fi
        rpm=""
        has_pwm=0
        pwm=""
        pwm_enable=""
        pwm_writable=0
        mode_writable=0
        pwm_path=""
        pwm_enable_path=""

        [ -r "$fan_path" ] && rpm="$(cat "$fan_path" 2>/dev/null || true)"
        if [ -z "$label" ] && [ -r "$dir/fan${idx}_label" ]; then
            label="$(cat "$dir/fan${idx}_label" 2>/dev/null | tr "\t\n" "  " || true)"
        fi
        if [ -z "$label" ] && [ -r "$dir/pwm${idx}_label" ]; then
            label="$(cat "$dir/pwm${idx}_label" 2>/dev/null | tr "\t\n" "  " || true)"
        fi

        if [ -e "$dir/pwm${idx}" ]; then
            has_pwm=1
            pwm_path="$dir/pwm${idx}"
            pwm="$(cat "$pwm_path" 2>/dev/null || true)"
            [ -w "$pwm_path" ] && pwm_writable=1
        fi

        if [ -e "$dir/pwm${idx}_enable" ]; then
            pwm_enable_path="$dir/pwm${idx}_enable"
            pwm_enable="$(cat "$pwm_enable_path" 2>/dev/null || true)"
            [ -w "$pwm_enable_path" ] && mode_writable=1
        fi

        printf "sysfs\t%s\t%s\t%s\t%s\t%s\t%s\t%s\t%s\t%s\t%s\t%s\t%s\t%s\t%s\t%s\t%s\n" \
            "$dir" "$device" "$chip" "$idx" "$rpm" "$label" \
            "$has_pwm" "$pwm" "$pwm_enable" "$pwm_writable" "$mode_writable" \
            "$fan_path" "$pwm_path" "$pwm_enable_path" "$system_vendor" "$product_name"
    }

    for fan_input in "$dir"/fan*_input "$dir"/*fan*input*; do
        [ -f "$fan_input" ] || continue
        base="${fan_input##*/}"
        idx="$(printf "%s" "$base" | sed -n 's/^fan\([0-9][0-9]*\)_input$/\1/p')"
        label=""
        if [ -z "$idx" ]; then
            fallback_idx=$((fallback_idx + 1))
            idx="$fallback_idx"
            label="$base"
        fi
        case " $printed_indexes " in
            *" $idx "*) continue ;;
        esac
        printed_indexes="${printed_indexes} ${idx}"
        print_entry "$idx" "$fan_input" "$label"
    done

    for pwm_file in "$dir"/pwm[0-9]*; do
        [ -f "$pwm_file" ] || continue
        case "$pwm_file" in
            *_enable|*_mode|*_freq) continue ;;
        esac
        base="${pwm_file##*/}"
        idx="${base#pwm}"
        case "$idx" in
            ""|*[!0-9]*) continue ;;
        esac
        case " $printed_indexes " in
            *" $idx "*) continue ;;
        esac
        printed_indexes="${printed_indexes} ${idx}"
        print_entry "$idx" "" ""
    done
}

find /sys -type f \( -name "fan*_input" -o -name "*fan*input*" -o -name "pwm[0-9]*" \) 2>/dev/null | head -n 200 | while IFS= read -r path; do
    emit_dir "${path%/*}"
done

find /sys -maxdepth 8 \( -iname "*fan*" -o -iname "*pwm*" \) 2>/dev/null | head -n 80 | while IFS= read -r path; do
    value=""
    if [ -f "$path" ] && [ -r "$path" ]; then
        value="$(cat "$path" 2>/dev/null | tr "\t\n" "  " | cut -c1-120 || true)"
    fi
    printf "candidate\t%s\t%s\n" "$path" "$value"
done

for cooling in /sys/class/thermal/cooling_device*; do
    [ -d "$cooling" ] || continue
    ctype="$(cat "$cooling/type" 2>/dev/null || true)"
    cur_state="$(cat "$cooling/cur_state" 2>/dev/null || true)"
    max_state="$(cat "$cooling/max_state" 2>/dev/null || true)"
    writable=0
    [ -w "$cooling/cur_state" ] && writable=1
    printf "cooling\t%s\t%s\t%s\t%s\t%s\n" "$cooling" "$ctype" "$cur_state" "$max_state" "$writable"
done
'''
        return f"sh -c {shlex.quote(script)}"

    def _build_inventory_command(self) -> str:
        script = r'''
sanitize_value() {
    tr "\t\r\n" "   " | cut -c1-160
}

for hwmon in /sys/class/hwmon/hwmon*; do
    [ -d "$hwmon" ] || continue
    chip="$(cat "$hwmon/name" 2>/dev/null || true)"
    device="$(readlink -f "$hwmon/device" 2>/dev/null || readlink -f "$hwmon" 2>/dev/null || printf "%s" "$hwmon")"
    fan_files=""
    pwm_files=""

    for fan_file in "$hwmon"/fan*; do
        [ -e "$fan_file" ] || continue
        fan_files="${fan_files} ${fan_file##*/}"
    done

    for pwm_file in "$hwmon"/pwm*; do
        [ -e "$pwm_file" ] || continue
        pwm_files="${pwm_files} ${pwm_file##*/}"
    done

    fan_files="${fan_files# }"
    pwm_files="${pwm_files# }"
    printf "hwmon\t%s\t%s\t%s\t%s\t%s\n" "$hwmon" "$device" "$chip" "$fan_files" "$pwm_files"
done

printf "host\tkernel\t%s\n" "$(uname -r 2>/dev/null | sanitize_value)"
for key in sys_vendor product_name product_version board_vendor board_name board_version bios_vendor bios_version; do
    value="$(cat "/sys/class/dmi/id/$key" 2>/dev/null | sanitize_value)"
    [ -n "$value" ] && printf "host\t%s\t%s\n" "$key" "$value"
done

candidate_modules="it87 nct6775 nct6683 f71882fg w83627ehf w83627hf sch5627 sch5636 coretemp asus_ec_sensors asus_wmi_sensors gigabyte_wmi dell_smm_hwmon thinkpad_acpi ug_it86x_cpufan ug_it86x_sio"
if command -v lsmod >/dev/null 2>&1; then
    for module in $candidate_modules; do
        if lsmod 2>/dev/null | awk 'NR > 1 {print $1}' | grep -qx "$module"; then
            printf "module\tloaded\t%s\n" "$module"
        fi
    done
fi

if command -v modinfo >/dev/null 2>&1; then
    for module in $candidate_modules; do
        if modinfo -n "$module" >/dev/null 2>&1; then
            printf "module\tavailable\t%s\n" "$module"
        fi
    done
fi

if command -v systemctl >/dev/null 2>&1; then
    systemctl list-unit-files --type=service --no-legend 2>/dev/null \
        | awk '{print $1}' \
        | grep -Ei '(fan|thermal|hwmon|cool|it87|nct)' \
        | head -n 30 \
        | while IFS= read -r service; do
            [ -n "$service" ] && printf "service\t%s\n" "$service"
        done

    service_name="pwm-fancontrol.service"
    load_state="$(systemctl show "$service_name" --property=LoadState --value 2>/dev/null | awk 'NR == 1 {print $1}')"
    if [ -n "$load_state" ] && [ "$load_state" != "not-found" ]; then
        printf "service\t%s\n" "$service_name"
        for property in LoadState ActiveState SubState UnitFileState FragmentPath MainPID ExecMainStatus ExecStart; do
            value="$(systemctl show "$service_name" --property="$property" --value 2>/dev/null | sanitize_value)"
            [ -n "$value" ] && printf "serviceprop\t%s\t%s\t%s\n" "$service_name" "$property" "$value"
        done

        main_pid="$(systemctl show "$service_name" --property=MainPID --value 2>/dev/null | tr -cd '0-9')"
        if [ -n "$main_pid" ] && [ "$main_pid" != "0" ]; then
            process_exe="$(readlink -f "/proc/$main_pid/exe" 2>/dev/null | sanitize_value)"
            [ -n "$process_exe" ] && printf "serviceprop\t%s\tProcessExe\t%s\n" "$service_name" "$process_exe"
        fi

        if command -v journalctl >/dev/null 2>&1; then
            journalctl -b -u "$service_name" -n 40 --no-pager -o cat 2>/dev/null \
                | grep -Ei '(fan|pwm|it86|hwmon|error|fail|start|stop|module|driver)' \
                | tail -n 20 \
                | while IFS= read -r message; do
                    message="$(printf "%s" "$message" | sanitize_value)"
                    [ -n "$message" ] && printf "servicelog\t%s\t%s\n" "$service_name" "$message"
                done
        fi
    fi
fi

for path in /proc/it86 /proc/it86/fan /proc/it86/startup /sys/module/ug_it86x_cpufan /sys/module/ug_it86x_sio; do
    [ -e "$path" ] || continue
    interface_type="other"
    [ -d "$path" ] && interface_type="directory"
    [ -f "$path" ] && interface_type="file"
    readable=0
    writable=0
    [ -r "$path" ] && readable=1
    [ -w "$path" ] && writable=1
    printf "vendor\t%s\t%s\t%s\t%s\n" "$path" "$interface_type" "$readable" "$writable"
done

fan_script="/usr/trim/bin/pwm-fancontrol.sh"
if [ -f "$fan_script" ]; then
    printf "fanscript\tpath\t%s\n" "$fan_script"
    script_size="$(stat -c '%s' "$fan_script" 2>/dev/null || true)"
    script_mode="$(stat -c '%a' "$fan_script" 2>/dev/null || true)"
    script_owner="$(stat -c '%U:%G' "$fan_script" 2>/dev/null || true)"
    [ -n "$script_size" ] && printf "fanscript\tsize\t%s\n" "$script_size"
    [ -n "$script_mode" ] && printf "fanscript\tmode\t%s\n" "$script_mode"
    [ -n "$script_owner" ] && printf "fanscript\towner\t%s\n" "$script_owner"

    if command -v sha256sum >/dev/null 2>&1; then
        script_sha256="$(sha256sum "$fan_script" 2>/dev/null | awk '{print $1}')"
        [ -n "$script_sha256" ] && printf "fanscript\tsha256\t%s\n" "$script_sha256"
    fi

    syntax="unavailable"
    if command -v bash >/dev/null 2>&1; then
        if bash -n "$fan_script" >/dev/null 2>&1; then syntax="ok"; else syntax="error"; fi
    elif command -v sh >/dev/null 2>&1; then
        if sh -n "$fan_script" >/dev/null 2>&1; then syntax="ok"; else syntax="error"; fi
    fi
    printf "fanscript\tsyntax\t%s\n" "$syntax"

    awk '
        {
            lower = tolower($0)
            if (lower ~ /(password|passwd|token|secret|private[_-]?key|api[_-]?key)/ || lower ~ /:\/\/.*@/) next
            if (lower ~ /(modprobe|insmod|rmmod|it87|it8613|fan|pwm|hwmon|sensors|\/sys\/|\/proc\/)/) {
                gsub(/\t/, " ", $0)
                printf "fanscriptline\t%d\t%s\n", NR, substr($0, 1, 240)
            }
        }
    ' "$fan_script" 2>/dev/null | head -n 80
fi

board_json="/boot/board.json"
printf "boardconfig\tpath\t%s\n" "$board_json"
board_readable=0
[ -r "$board_json" ] && board_readable=1
printf "boardconfig\treadable\t%s\n" "$board_readable"
if [ "$board_readable" = "1" ] && command -v jq >/dev/null 2>&1; then
    board_json_valid=0
    if jq -e 'type == "object" and ((.fan // []) | type == "array")' "$board_json" >/dev/null 2>&1; then
        board_json_valid=1
    fi
    printf "boardconfig\tjson_valid\t%s\n" "$board_json_valid"

    if [ "$board_json_valid" = "1" ]; then
        board_fan_count="$(jq -r '(.fan // []) | length' "$board_json" 2>/dev/null | head -n 1)"
        printf "boardconfig\tfan_count\t%s\n" "$board_fan_count"

        jq -r '
            ((.fan // [])[0:20] | to_entries[])
            | [
                (.key | tostring),
                (.value.name // ""),
                (.value.tsysfs // ""),
                (.value.fsysfs // ""),
                (.value.start_speed // ""),
                (.value.start_temp // ""),
                (.value.max_speed // ""),
                (.value.temp_div // ""),
                (.value.verbose // "")
            ]
            | @tsv
            | "boardfan\t\(.)"
        ' "$board_json" 2>/dev/null | head -n 20

        tab="$(printf '\t')"
        jq -r '
            ((.fan // [])[0:20] | to_entries[]) as $entry
            | [
                {kind: "tsysfs", path: ($entry.value.tsysfs // "")},
                {kind: "fsysfs", path: ($entry.value.fsysfs // "")}
            ][]
            | select((.path | type) == "string" and (.path | length) > 0)
            | [$entry.key, .kind, .path]
            | @tsv
        ' "$board_json" 2>/dev/null | head -n 40 | while IFS="$tab" read -r fan_index path_kind configured_path; do
            path_exists=0
            path_readable=0
            path_writable=0
            path_value=""
            [ -e "$configured_path" ] && path_exists=1
            [ -r "$configured_path" ] && path_readable=1
            [ -w "$configured_path" ] && path_writable=1

            case "$configured_path" in
                /sys/*|/proc/*)
                    if [ -f "$configured_path" ] && [ "$path_readable" = "1" ]; then
                        path_value="$(head -c 160 "$configured_path" 2>/dev/null | sanitize_value)"
                    fi
                    ;;
            esac

            configured_path="$(printf "%s" "$configured_path" | sanitize_value)"
            printf "boardpath\t%s\t%s\t%s\t%s\t%s\t%s\t%s\n" \
                "$fan_index" "$path_kind" "$configured_path" "$path_exists" \
                "$path_readable" "$path_writable" "$path_value"
        done
    fi
fi

fan_binary="/usr/sbin/fancontrol"
if [ -e "$fan_binary" ]; then
    fan_binary_real="$(readlink -f "$fan_binary" 2>/dev/null || printf "%s" "$fan_binary")"
    printf "fanbinary\tpath\t%s\n" "$fan_binary_real"
    binary_size="$(stat -c '%s' "$fan_binary" 2>/dev/null || true)"
    binary_mode="$(stat -c '%a' "$fan_binary" 2>/dev/null || true)"
    binary_owner="$(stat -c '%U:%G' "$fan_binary" 2>/dev/null || true)"
    [ -n "$binary_size" ] && printf "fanbinary\tsize\t%s\n" "$binary_size"
    [ -n "$binary_mode" ] && printf "fanbinary\tmode\t%s\n" "$binary_mode"
    [ -n "$binary_owner" ] && printf "fanbinary\towner\t%s\n" "$binary_owner"

    if command -v sha256sum >/dev/null 2>&1; then
        binary_sha256="$(sha256sum "$fan_binary" 2>/dev/null | awk '{print $1}')"
        [ -n "$binary_sha256" ] && printf "fanbinary\tsha256\t%s\n" "$binary_sha256"
    fi
    if command -v file >/dev/null 2>&1; then
        binary_type="$(file -b "$fan_binary" 2>/dev/null | sanitize_value)"
        [ -n "$binary_type" ] && printf "fanbinary\tfile_type\t%s\n" "$binary_type"
    fi

    process_count=0
    for process_dir in /proc/[0-9]*; do
        [ -d "$process_dir" ] || continue
        process_pid="${process_dir##*/}"
        process_exe="$(readlink -f "$process_dir/exe" 2>/dev/null || true)"
        process_cmdline="$(tr '\000' ' ' < "$process_dir/cmdline" 2>/dev/null | sanitize_value)"
        is_fancontrol=0
        [ "$process_exe" = "$fan_binary_real" ] && is_fancontrol=1
        case "$process_cmdline" in
            "$fan_binary"|"$fan_binary "*) is_fancontrol=1 ;;
        esac
        [ "$is_fancontrol" = "1" ] || continue

        process_state="$(awk '/^State:/ {print $2; exit}' "$process_dir/status" 2>/dev/null | sanitize_value)"
        process_uid="$(awk '/^Uid:/ {print $2; exit}' "$process_dir/status" 2>/dev/null | sanitize_value)"
        process_exe="$(printf "%s" "$process_exe" | sanitize_value)"
        printf "fanprocess\t%s\t%s\t%s\t%s\t%s\n" \
            "$process_pid" "$process_exe" "$process_state" "$process_uid" "$process_cmdline"
        process_count=$((process_count + 1))
        [ "$process_count" -ge 10 ] && break
    done
fi

if command -v modinfo >/dev/null 2>&1 && modinfo it87 >/dev/null 2>&1; then
    for field in filename version description vermagic; do
        value="$(modinfo -F "$field" it87 2>/dev/null | head -n 1 | sanitize_value)"
        [ -n "$value" ] && printf "it87info\t%s\t%s\n" "$field" "$value"
    done
    modinfo -p it87 2>/dev/null | head -n 40 | while IFS= read -r parameter; do
        parameter="$(printf "%s" "$parameter" | sanitize_value)"
        [ -n "$parameter" ] && printf "it87parm\t%s\n" "$parameter"
    done
fi

if command -v modprobe >/dev/null 2>&1; then
    modprobe -n -v it87 2>/dev/null | head -n 20 | while IFS= read -r dry_run; do
        dry_run="$(printf "%s" "$dry_run" | sanitize_value)"
        [ -n "$dry_run" ] && printf "it87dry\t%s\n" "$dry_run"
    done
fi

if command -v dmesg >/dev/null 2>&1; then
    dmesg 2>/dev/null \
        | grep -Ei '(it87|it8613|super.?io|pwm-fan|fan control|hwmon)' \
        | tail -n 40 \
        | while IFS= read -r kernel_message; do
            kernel_message="$(printf "%s" "$kernel_message" | sanitize_value)"
            [ -n "$kernel_message" ] && printf "kernellog\t%s\n" "$kernel_message"
        done
fi

app_installed=0
[ -d /var/apps/fan-control ] && app_installed=1
app_listening=0
if command -v ss >/dev/null 2>&1; then
    if ss -lnt 2>/dev/null | awk 'NR > 1 {print $4}' | grep -Eq '(^|:|\])9511$'; then
        app_listening=1
    fi
fi
printf "app\tfan-control\t%s\t%s\t9511\n" "$app_installed" "$app_listening"

if [ "$app_listening" = "1" ]; then
    api_status=""
    if command -v curl >/dev/null 2>&1; then
        api_status="$(curl -fsS --max-time 2 http://127.0.0.1:9511/api/auth/status 2>/dev/null | sanitize_value)"
    elif command -v wget >/dev/null 2>&1; then
        api_status="$(wget -qO- -T 2 http://127.0.0.1:9511/api/auth/status 2>/dev/null | sanitize_value)"
    fi
    [ -n "$api_status" ] && printf "api\t9511\t%s\n" "$api_status"
fi

if command -v sensors-detect >/dev/null 2>&1; then
    printf "tool\tsensors-detect\t1\n"
else
    printf "tool\tsensors-detect\t0\n"
fi
'''
        return f"sh -c {shlex.quote(script)}"

    async def _write_sysfs(self, path: str, value: int) -> bool:
        if not path or not path.startswith("/sys/"):
            return False

        command = self._build_write_command(path, value)
        try:
            result = await self.coordinator.run_command(command)
        except Exception as e:
            _LOGGER.debug("写入 sysfs 失败: %s", str(e))
            return False

        return OK_TOKEN in result and ERROR_TOKEN not in result

    def _build_write_command(self, path: str, value: int) -> str:
        quoted_path = shlex.quote(path)
        script = (
            f"expected={self._single_quote(value)}; "
            f"if printf %s \"$expected\" > {quoted_path} "
            f"&& actual=$(cat {quoted_path} 2>/dev/null) "
            f"&& [ \"$actual\" = \"$expected\" ]; then "
            f"echo {OK_TOKEN}; else echo {ERROR_TOKEN}; fi"
        )
        return f"sh -c {shlex.quote(script)}"

    def _control_mode_from_enable(self, pwm_enable: int | None) -> str | None:
        if pwm_enable is None:
            return None
        return PWM_ENABLE_TO_MODE.get(pwm_enable, f"{CONTROL_MODE_UNKNOWN}({pwm_enable})")

    def _is_supported_fan_channel(
        self,
        chip_name: str,
        system_vendor: str,
        product_name: str,
        index: int,
    ) -> bool:
        """Reject unwired IT8613 channels on the exact DXP4800 Pro model."""
        if self._is_dxp4800pro_it8613(chip_name, system_vendor, product_name):
            return index in {2, 3}
        return True

    def _is_dxp4800pro_it8613(
        self,
        chip_name: str,
        system_vendor: str,
        product_name: str,
    ) -> bool:
        """Identify the exact board whose IT8613 auto curve is unverified."""
        return (
            (chip_name or "").strip().lower() == "it8613"
            and (system_vendor or "").strip().upper() == "UGREEN"
            and (product_name or "").strip() == "DXP4800 Pro"
        )

    def _control_capabilities(
        self,
        supports_pwm: bool,
        supports_manual_mode: bool,
        is_dxp4800pro: bool,
    ) -> dict:
        """Describe only mode operations that are safe for the detected board."""
        available_modes = []
        if supports_manual_mode:
            available_modes = [
                CONTROL_MODE_MANUAL,
                CONTROL_MODE_FULL_SPEED,
            ]
            if not is_dxp4800pro:
                available_modes.insert(0, CONTROL_MODE_AUTO)

        minimum_pwm_raw = (
            DXP4800PRO_MINIMUM_PWM_RAW
            if is_dxp4800pro and supports_pwm
            else None
        )
        return {
            "supports_manual_mode": supports_manual_mode,
            "supports_modes": bool(available_modes),
            "supports_auto_mode": CONTROL_MODE_AUTO in available_modes,
            "available_modes": available_modes,
            "minimum_pwm_raw": minimum_pwm_raw,
            "minimum_pwm_percent": self._pwm_raw_to_percent(minimum_pwm_raw),
            "manual_recovery_percent": 50 if is_dxp4800pro and supports_pwm else None,
        }

    def _default_fan_name(
        self,
        chip_name: str,
        system_vendor: str,
        product_name: str,
        index: int,
    ) -> str:
        """Return model-aware names when hwmon does not provide labels."""
        if self._is_dxp4800pro_it8613(chip_name, system_vendor, product_name):
            return {2: "CPU 风扇", 3: "系统风扇"}.get(index, f"风扇 {index}")
        return f"风扇 {index}"

    def _make_fan_id(self, chip_name: str, device_path: str, index: int, label: str) -> str:
        chip_slug = self._slug(chip_name) or "hwmon"
        stable_source = "|".join([chip_name or "", device_path or "", str(index), label or ""])
        digest = hashlib.sha1(stable_source.encode("utf-8")).hexdigest()[:8]
        return f"{chip_slug}_fan{index}_{digest}"

    def _slug(self, value: str) -> str:
        return re.sub(r"[^a-z0-9_]+", "_", (value or "").lower()).strip("_")

    def _to_int(self, value: str | None) -> int | None:
        if value is None:
            return None
        try:
            cleaned = str(value).strip().replace(",", "")
            return int(float(cleaned)) if "." in cleaned else int(cleaned)
        except (TypeError, ValueError):
            return None

    def _pwm_raw_to_percent(self, pwm_raw: int | None) -> int | None:
        if pwm_raw is None:
            return None
        pwm_raw = max(0, min(255, pwm_raw))
        return int(round((pwm_raw / 255) * 100))

    def _percent_to_pwm_raw(self, percentage: int) -> int:
        return int(round((max(0, min(100, percentage)) / 100) * 255))

    def _single_quote(self, value: int | str) -> str:
        return "'" + str(value).replace("'", "'\"'\"'") + "'"

    def _empty_diagnostics(self) -> dict:
        return {
            "status": "未扫描",
            "fan_count": 0,
            "source": "none",
            "hwmon_entry_count": 0,
            "hwmon_inventory": [],
            "sysfs_entry_count": 0,
            "sysfs_fan_candidates": [],
            "cooling_devices": [],
            "sensors_fan_lines": [],
            "sensors_u_fan_lines": [],
            "host_hardware": {},
            "loaded_fan_modules": [],
            "available_fan_modules": [],
            "fan_services": [],
            "fan_service_details": {},
            "vendor_fan_interfaces": [],
            "fan_service_logs": [],
            "fan_startup_script": {},
            "board_fan_config": {},
            "fancontrol_runtime": {
                "binary": {},
                "process_count": 0,
                "processes": [],
            },
            "it87_module_info": {},
            "fan_kernel_logs": [],
            "fan_control_app": {
                "installed": False,
                "listening": False,
                "port": 9511,
                "api_status": "",
            },
            "diagnostic_tools": {},
            "hint": "等待下一次扫描",
        }

    def _build_diagnostics(
        self,
        source: str,
        fans: list[dict],
        hwmon_output: str,
        sensors_output: str,
        sensors_u_output: str,
        sysfs_output: str,
        inventory_output: str,
        error: str | None = None,
    ) -> dict:
        status = f"发现 {len(fans)} 个风扇" if fans else "未发现风扇"
        if source == "error":
            status = "风扇扫描失败"

        host_hardware = self.parse_host_hardware(inventory_output or hwmon_output)
        loaded_modules = self.parse_driver_modules(inventory_output, "loaded")
        available_modules = self.parse_driver_modules(inventory_output, "available")
        fan_services = self.parse_fan_services(inventory_output)
        fan_service_details = self.parse_fan_service_details(inventory_output)
        vendor_interfaces = self.parse_vendor_fan_interfaces(inventory_output)
        fan_service_logs = self.parse_fan_service_logs(inventory_output)
        fan_startup_script = self.parse_fan_startup_script(inventory_output)
        board_fan_config = self.parse_board_fan_config(inventory_output)
        fancontrol_runtime = self.parse_fancontrol_runtime(inventory_output)
        it87_module_info = self.parse_it87_module_info(inventory_output)
        fan_kernel_logs = self.parse_fan_kernel_logs(inventory_output)
        fan_control_app = self.parse_fan_control_app(inventory_output)
        diagnostic_tools = self.parse_diagnostic_tools(inventory_output)

        hint = "已通过只读发现命令找到风扇"
        if not fans:
            hint = (
                "没有在标准 hwmon、sensors、sensors -u 或直接 /sys 扫描中发现风扇转速；"
                "请查看主机硬件、已加载风扇模块、可用风扇模块和风扇控制应用。"
            )
            if "pwm-fancontrol.service" in fan_service_details:
                hint = (
                    "检测到 pwm-fancontrol.service，但它没有向标准 hwmon 暴露风扇；"
                    "请查看fnOS板级风扇配置、fancontrol运行状态和it87模块信息。"
                )
        if error:
            hint = f"扫描命令异常: {error}"

        return {
            "status": status,
            "fan_count": len(fans),
            "source": source,
            "hwmon_entry_count": len(
                [line for line in hwmon_output.splitlines() if line.startswith("entry\t")]
            ),
            "hwmon_inventory": self.parse_hwmon_inventory(inventory_output) if inventory_output else [],
            "sysfs_entry_count": len(
                [line for line in sysfs_output.splitlines() if line.startswith("sysfs\t")]
            ),
            "sysfs_fan_candidates": self.parse_sysfs_candidates(sysfs_output),
            "cooling_devices": self.parse_cooling_devices(sysfs_output),
            "sensors_fan_lines": self._summarize_fan_lines(sensors_output),
            "sensors_u_fan_lines": self._summarize_fan_lines(sensors_u_output),
            "host_hardware": host_hardware,
            "loaded_fan_modules": loaded_modules,
            "available_fan_modules": available_modules,
            "fan_services": fan_services,
            "fan_service_details": fan_service_details,
            "vendor_fan_interfaces": vendor_interfaces,
            "fan_service_logs": fan_service_logs,
            "fan_startup_script": fan_startup_script,
            "board_fan_config": board_fan_config,
            "fancontrol_runtime": fancontrol_runtime,
            "it87_module_info": it87_module_info,
            "fan_kernel_logs": fan_kernel_logs,
            "fan_control_app": fan_control_app,
            "diagnostic_tools": diagnostic_tools,
            "hint": hint,
            "error": error,
        }

    def _summarize_fan_lines(self, output: str) -> list[str]:
        lines = []
        for line in output.splitlines():
            stripped = line.strip()
            if not stripped:
                continue

            lowered = stripped.lower()
            if "fan" not in lowered and "rpm" not in lowered and "pwm" not in lowered:
                continue

            lines.append(stripped[:200])
            if len(lines) >= 30:
                break

        return lines
