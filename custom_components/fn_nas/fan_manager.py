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

    async def get_fans_info(self) -> list[dict]:
        """Discover fan sensors and PWM controls from Linux hwmon."""
        try:
            output = await self.coordinator.run_command(self._build_discovery_command())
            fans = self.parse_hwmon_snapshot(output) if output else []
            if fans:
                return fans

            sensors_output = await self.coordinator.run_command("sensors 2>/dev/null || true")
            return self.parse_sensors_output(sensors_output) if sensors_output else []
        except Exception as e:
            _LOGGER.debug("获取风扇信息失败: %s", str(e))
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

        for line in output.splitlines():
            match = re.match(r"^\s*([^:]+?)\s*:\s*([0-9,]+)\s*RPM\b", line, re.IGNORECASE)
            if not match:
                continue

            label = match.group(1).strip()
            rpm = self._to_int(match.group(2).replace(",", ""))
            if rpm is None:
                continue

            fan_id = self._make_fan_id("sensors", "lm-sensors", len(fans) + 1, label)
            if fan_id in seen_ids:
                continue
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

        return fans

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
            return int(str(value).strip())
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
