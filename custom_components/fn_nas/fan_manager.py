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
        inventory_output = ""

        try:
            hwmon_output = await self.coordinator.run_command(self._build_discovery_command())
            fans = self.parse_hwmon_snapshot(hwmon_output) if hwmon_output else []
            if fans:
                self.last_diagnostics = self._build_diagnostics(
                    "hwmon", fans, hwmon_output, sensors_output, sensors_u_output, inventory_output
                )
                return fans

            sensors_output = await self.coordinator.run_command("sensors 2>/dev/null || true")
            fans = self.parse_sensors_output(sensors_output) if sensors_output else []
            if fans:
                self.last_diagnostics = self._build_diagnostics(
                    "sensors", fans, hwmon_output, sensors_output, sensors_u_output, inventory_output
                )
                return fans

            sensors_u_output = await self.coordinator.run_command("sensors -u 2>/dev/null || true")
            fans = self.parse_sensors_output(sensors_u_output) if sensors_u_output else []
            if fans:
                self.last_diagnostics = self._build_diagnostics(
                    "sensors -u", fans, hwmon_output, sensors_output, sensors_u_output, inventory_output
                )
                return fans

            inventory_output = await self.coordinator.run_command(self._build_inventory_command())
            self.last_diagnostics = self._build_diagnostics(
                "none", [], hwmon_output, sensors_output, sensors_u_output, inventory_output
            )
            return []
        except Exception as e:
            _LOGGER.debug("获取风扇信息失败: %s", str(e))
            self.last_diagnostics = self._build_diagnostics(
                "error", [], hwmon_output, sensors_output, sensors_u_output, inventory_output, str(e)
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

            index = self._to_int(index_text)
            if index is None:
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

            name = label.strip() if label.strip() else f"风扇 {index}"
            control_mode = self._control_mode_from_enable(pwm_enable)
            pwm_path = f"{hwmon_path}/pwm{index}" if has_pwm else None
            pwm_enable_path = (
                f"{hwmon_path}/pwm{index}_enable"
                if pwm_enable is not None
                else None
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
                    "supports_pwm": has_pwm and pwm_writable,
                    "supports_modes": pwm_enable_path is not None and mode_writable,
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
                    "supports_modes": False,
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

    async def set_percentage(self, fan: dict, percentage: int | float) -> bool:
        """Set a PWM fan to a Home Assistant percentage value."""
        if not fan.get("supports_pwm") or not fan.get("pwm_path"):
            return False

        pwm_percent = max(0, min(100, int(round(float(percentage)))))
        pwm_raw = self._percent_to_pwm_raw(pwm_percent)

        # Most hwmon drivers require manual mode before pwmN writes take effect.
        if fan.get("supports_modes") and fan.get("pwm_enable_path"):
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

        if not fan.get("supports_modes") or not fan.get("pwm_enable_path"):
            return False

        if not await self._write_sysfs(fan["pwm_enable_path"], MODE_TO_PWM_ENABLE[mode]):
            return False

        fan["control_mode"] = mode
        fan["pwm_enable"] = MODE_TO_PWM_ENABLE[mode]
        return True

    def _build_discovery_command(self) -> str:
        script = r'''
for hwmon in /sys/class/hwmon/hwmon*; do
    [ -d "$hwmon" ] || continue
    chip="$(cat "$hwmon/name" 2>/dev/null || true)"
    device="$(readlink -f "$hwmon/device" 2>/dev/null || readlink -f "$hwmon" 2>/dev/null || printf "%s" "$hwmon")"

    print_entry() {
        idx="$1"
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

        printf "entry\t%s\t%s\t%s\t%s\t%s\t%s\t%s\t%s\t%s\t%s\t%s\n" \
            "$hwmon" "$device" "$chip" "$idx" "$rpm" "$label" \
            "$has_pwm" "$pwm" "$pwm_enable" "$pwm_writable" "$mode_writable"
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

    def _build_inventory_command(self) -> str:
        script = r'''
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
        script = (
            f"printf %s {self._single_quote(value)} > {shlex.quote(path)} "
            f"&& echo {OK_TOKEN} || echo {ERROR_TOKEN}"
        )
        return f"sh -c {shlex.quote(script)}"

    def _control_mode_from_enable(self, pwm_enable: int | None) -> str | None:
        if pwm_enable is None:
            return None
        return PWM_ENABLE_TO_MODE.get(pwm_enable, f"{CONTROL_MODE_UNKNOWN}({pwm_enable})")

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
            "sensors_fan_lines": [],
            "sensors_u_fan_lines": [],
            "hint": "等待下一次扫描",
        }

    def _build_diagnostics(
        self,
        source: str,
        fans: list[dict],
        hwmon_output: str,
        sensors_output: str,
        sensors_u_output: str,
        inventory_output: str,
        error: str | None = None,
    ) -> dict:
        status = f"发现 {len(fans)} 个风扇" if fans else "未发现风扇"
        if source == "error":
            status = "风扇扫描失败"

        hint = "已通过只读发现命令找到风扇"
        if not fans:
            hint = (
                "没有在 /sys/class/hwmon、sensors 或 sensors -u 中发现风扇转速；"
                "请查看此实体属性里的 hwmon候选 和 sensors摘要。"
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
            "sensors_fan_lines": self._summarize_fan_lines(sensors_output),
            "sensors_u_fan_lines": self._summarize_fan_lines(sensors_u_output),
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
