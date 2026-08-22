"""Optional LLLED fan backend accessed through the existing SSH connection."""

from __future__ import annotations

import json
import logging
import shlex
from urllib.parse import urlencode


_LOGGER = logging.getLogger(__name__)

CONTROL_MODE_AUTO = "自动"
CONTROL_MODE_MANUAL = "手动"
CONTROL_MODE_FULL_SPEED = "全速"

LLLED_API_MARKER = "__FN_NAS_LLLED_API__"
LLLED_API_CANDIDATES = (
    "/var/apps/App.Native.UGreenLED/ui/api.cgi",
    "/var/apps/App.Native.UGreenLED/target/ui/api.cgi",
    "/var/apps/@appcenter/App.Native.UGreenLED/ui/api.cgi",
    "/var/apps/@appcenter/App.Native.UGreenLED/target/ui/api.cgi",
)


class LLLEDFanBackend:
    """Read and control an installed LLLED application without bundling it."""

    def __init__(self, coordinator):
        self.coordinator = coordinator
        self.state = self._empty_state()
        self.last_error = None

    async def get_status(self) -> dict:
        """Discover LLLED and return its protected BIOS/fan status."""
        output = await self.coordinator.run_command(
            self._build_cgi_command("/bios/status", "GET")
        )
        api_path, payload = self._parse_cgi_output(output)
        if payload is None:
            self.state = self._empty_state()
            self.state["installed"] = api_path is not None
            self.state["api_path"] = api_path
            self.state["error"] = "LLLED 风扇接口没有返回有效状态" if api_path else None
            return self.state

        state = self.parse_status(payload)
        state["installed"] = True
        state["api_path"] = api_path
        self.state = state
        return state

    def parse_status(self, payload: dict) -> dict:
        """Convert the LLLED BIOS status document to integration data."""
        curve = payload.get("fan_curve") if isinstance(payload.get("fan_curve"), dict) else {}
        stock_curve = (
            curve.get("stock_curve")
            if isinstance(curve.get("stock_curve"), dict)
            else {}
        )
        available = bool(
            payload.get("ok")
            and payload.get("supported")
            and payload.get("available")
        )
        confirmation_required = bool(payload.get("write_confirmation_required"))
        confirmation_acknowledged = bool(
            payload.get("write_confirmation_acknowledged")
        )
        supports_control = available and (
            not confirmation_required or confirmation_acknowledged
        )

        model = str(payload.get("model") or "unknown")
        reported_minimum = self._to_int(payload.get("min_pwm")) or 40
        manual_minimum = max(40, reported_minimum)
        if model == "dxp4800_pro":
            manual_minimum = max(80, manual_minimum)

        fan_specs = []
        if payload.get("cpu_fan_present") is not False:
            fan_specs.append(("cpu", "CPU 风扇", "cpu_pwm", "cpu_rpm"))
        fan_specs.append(("sys", "系统风扇", "sys_pwm", "sys_rpm"))
        if self._to_int(payload.get("sys2_pwm")) not in {None, -1}:
            fan_specs.append(("sys2", "系统风扇 2", "sys2_pwm", "sys2_rpm"))

        raw_fans = []
        for channel, name, pwm_key, rpm_key in fan_specs:
            pwm_raw = self._to_int(payload.get(pwm_key))
            rpm = self._to_int(payload.get(rpm_key))
            if pwm_raw in {None, -1} and (rpm is None or rpm <= 0):
                continue
            raw_fans.append((channel, name, pwm_raw, rpm))

        curve_running = bool(curve.get("running"))
        if curve_running:
            mode = CONTROL_MODE_AUTO
        elif raw_fans and all(pwm_raw == 255 for _, _, pwm_raw, _ in raw_fans):
            mode = CONTROL_MODE_FULL_SPEED
        else:
            mode = CONTROL_MODE_MANUAL

        fans = []
        for index, (channel, name, pwm_raw, rpm) in enumerate(raw_fans, start=1):
            fans.append(
                {
                    "id": f"llled_{channel}",
                    "name": name,
                    "index": index,
                    "channel": channel,
                    "chip": "LLLED/ugreenctl",
                    "backend": "llled",
                    "hwmon_path": None,
                    "device_path": "LLLED",
                    "fan_input_path": None,
                    "pwm_path": None,
                    "pwm_enable_path": None,
                    "rpm": rpm,
                    "pwm_raw": pwm_raw,
                    "pwm_percent": self._pwm_raw_to_percent(pwm_raw),
                    "pwm_enable": None,
                    "control_mode": mode,
                    "supports_pwm": supports_control and pwm_raw not in {None, -1},
                    "supports_manual_mode": supports_control,
                    # LLLED auto mode is global, so per-fan mode controls stay hidden.
                    "supports_modes": False,
                    "supports_auto_mode": supports_control,
                    "available_modes": [],
                    "minimum_pwm_raw": manual_minimum,
                    "minimum_pwm_percent": self._pwm_raw_to_percent(manual_minimum),
                    "manual_recovery_percent": 50,
                    "last_control_result": None,
                }
            )

        stock_profile = stock_curve.get("profile") if stock_curve.get("available") else None
        curve_minimum = self._to_int(curve.get("minimum_pwm")) or 64
        error = payload.get("fan_error") or payload.get("error") or None
        if confirmation_required and not confirmation_acknowledged:
            error = "需要先在 LLLED 中确认风扇写入风险"

        return {
            "installed": True,
            "available": available,
            "api_path": None,
            "backend": "llled",
            "model": model,
            "hardware_backend": payload.get("backend"),
            "supports_control": supports_control,
            "supports_modes": supports_control and bool(fans) and bool(stock_profile),
            "available_modes": (
                [CONTROL_MODE_AUTO, CONTROL_MODE_MANUAL, CONTROL_MODE_FULL_SPEED]
                if supports_control and fans and stock_profile
                else []
            ),
            "mode": mode,
            "curve_running": curve_running,
            "curve_enabled": bool(curve.get("enabled")),
            "stock_profile": stock_profile,
            "curve_minimum_pwm": curve_minimum,
            "minimum_pwm_raw": manual_minimum,
            "write_confirmation_required": confirmation_required,
            "write_confirmation_acknowledged": confirmation_acknowledged,
            "cpu_temperature": self._to_number(curve.get("cpu_celsius")),
            "hdd_temperature": self._to_number(curve.get("hdd_celsius")),
            "ssd_temperature": self._to_number(curve.get("ssd_celsius")),
            "desired_cpu_pwm": self._to_int(curve.get("desired_cpu_pwm")),
            "desired_system_pwm": self._to_int(curve.get("desired_system_pwm")),
            "applied_cpu_pwm": self._to_int(curve.get("applied_cpu_pwm")),
            "applied_system_pwm": self._to_int(curve.get("applied_system_pwm")),
            "fans": fans,
            "error": error,
        }

    async def set_percentage(self, channel: str, percentage: int | float) -> bool:
        """Stop automatic control through LLLED and set one fixed fan speed."""
        fan = self._fan_for_channel(channel)
        if not self.state.get("supports_control") or fan is None:
            return False

        requested = max(0, min(100, int(round(float(percentage)))))
        pwm_raw = int(round((requested / 100) * 255))
        pwm_raw = max(self._to_int(fan.get("minimum_pwm_raw")) or 40, pwm_raw)
        payload = await self._request(
            "/bios/fan",
            "POST",
            {"channel": channel, "pwm": pwm_raw},
        )
        if payload is None or not payload.get("ok"):
            return False

        self.state = self.parse_status(payload)
        actual = self._fan_for_channel(channel)
        success = actual is not None and actual.get("pwm_raw") == pwm_raw
        self._record_control_result(channel, success, pwm_raw, actual)
        return success

    async def set_mode(self, mode: str) -> bool:
        """Switch the single LLLED controller between auto, manual, and full."""
        if mode not in self.state.get("available_modes", []):
            return False

        previous_mode = self.state.get("mode")
        if mode == CONTROL_MODE_AUTO:
            profile = self.state.get("stock_profile")
            if not profile:
                return False
            payload = await self._request(
                "/bios/fan-curve",
                "POST",
                {
                    "action": "start",
                    "mode": profile,
                    "interval": 10,
                    "downshift": 60,
                    "minimum": self.state.get("curve_minimum_pwm") or 64,
                    "require_storage": "false",
                },
            )
            return self._accept_mode_response(payload, CONTROL_MODE_AUTO)

        payload = await self._request(
            "/bios/fan-curve", "POST", {"action": "stop"}
        )
        if payload is None or not payload.get("ok"):
            return False
        self.state = self.parse_status(payload)

        target_percentage = 100 if mode == CONTROL_MODE_FULL_SPEED else None
        if mode == CONTROL_MODE_MANUAL and (
            previous_mode == CONTROL_MODE_FULL_SPEED
            or self.state.get("mode") == CONTROL_MODE_FULL_SPEED
        ):
            target_percentage = 50

        if target_percentage is not None:
            for fan in list(self.state.get("fans", [])):
                if not await self.set_percentage(fan["channel"], target_percentage):
                    return False

        if mode == CONTROL_MODE_MANUAL:
            self.state["mode"] = CONTROL_MODE_MANUAL
            for fan in self.state.get("fans", []):
                fan["control_mode"] = CONTROL_MODE_MANUAL
            return True

        return self.state.get("mode") == CONTROL_MODE_FULL_SPEED

    async def _request(self, path: str, method: str, query: dict | None = None) -> dict | None:
        request_query = dict(query or {})
        if (
            method == "POST"
            and self.state.get("write_confirmation_required")
            and self.state.get("write_confirmation_acknowledged")
        ):
            # LLLED still requires its per-request token after the user has
            # acknowledged the risk in the LLLED UI.
            request_query["confirm"] = "firmware-reversed"
        output = await self.coordinator.run_command(
            self._build_cgi_command(path, method, request_query)
        )
        _api_path, payload = self._parse_cgi_output(output)
        if payload is None:
            self.last_error = "LLLED 接口没有返回有效 JSON"
            return None
        if not payload.get("ok"):
            self.last_error = str(payload.get("error") or "LLLED 操作失败")
            _LOGGER.warning("LLLED 风扇操作失败: %s", self.last_error)
        else:
            self.last_error = None
        return payload

    def _accept_mode_response(self, payload: dict | None, expected: str) -> bool:
        if payload is None or not payload.get("ok"):
            return False
        self.state = self.parse_status(payload)
        return self.state.get("mode") == expected

    def _record_control_result(
        self,
        channel: str,
        success: bool,
        requested_pwm: int,
        actual: dict | None,
    ) -> None:
        result = {
            "success": success,
            "backend": "llled",
            "requested_pwm": requested_pwm,
            "actual_pwm": actual.get("pwm_raw") if actual else None,
            "error": self.last_error,
        }
        if actual is not None:
            actual["last_control_result"] = result

    def _fan_for_channel(self, channel: str) -> dict | None:
        for fan in self.state.get("fans", []):
            if fan.get("channel") == channel:
                return fan
        return None

    def _build_cgi_command(
        self,
        path: str,
        method: str,
        query: dict | None = None,
    ) -> str:
        query_string = urlencode(query or {})
        candidates = " ".join(shlex.quote(candidate) for candidate in LLLED_API_CANDIDATES)
        script = (
            "api=''; "
            f"for candidate in {candidates}; do "
            "if [ -f \"$candidate\" ]; then api=\"$candidate\"; break; fi; done; "
            "[ -n \"$api\" ] || exit 0; "
            f"printf '{LLLED_API_MARKER}%s\\n' \"$api\"; "
            f"env REQUEST_METHOD={shlex.quote(method)} "
            f"PATH_INFO={shlex.quote(path)} "
            f"QUERY_STRING={shlex.quote(query_string)} CONTENT_LENGTH=0 "
            "bash \"$api\""
        )
        return f"sh -c {shlex.quote(script)}"

    def _parse_cgi_output(self, output: str) -> tuple[str | None, dict | None]:
        api_path = None
        payload = None
        for line in (output or "").splitlines():
            stripped = line.strip()
            if stripped.startswith(LLLED_API_MARKER):
                api_path = stripped[len(LLLED_API_MARKER):].strip() or None
                continue
            if not stripped.startswith("{"):
                continue
            try:
                candidate = json.loads(stripped)
            except json.JSONDecodeError:
                continue
            if isinstance(candidate, dict):
                payload = candidate
        return api_path, payload

    def _empty_state(self) -> dict:
        return {
            "installed": False,
            "available": False,
            "api_path": None,
            "backend": None,
            "supports_control": False,
            "supports_modes": False,
            "available_modes": [],
            "mode": None,
            "curve_running": False,
            "fans": [],
            "error": None,
        }

    @staticmethod
    def _to_int(value) -> int | None:
        try:
            return int(float(value))
        except (TypeError, ValueError):
            return None

    @staticmethod
    def _to_number(value) -> int | float | None:
        try:
            number = float(value)
        except (TypeError, ValueError):
            return None
        return int(number) if number.is_integer() else number

    @staticmethod
    def _pwm_raw_to_percent(pwm_raw: int | None) -> int | None:
        if pwm_raw is None or pwm_raw < 0:
            return None
        return int(round((max(0, min(255, pwm_raw)) / 255) * 100))
