"""Verify static operating-system information parsing and caching."""

import importlib.util
from pathlib import Path

from verification.support import VerifyCase


SYSTEM_MANAGER_PATH = (
    Path(__file__).resolve().parents[1]
    / "custom_components"
    / "fn_nas"
    / "system_manager.py"
)
SPEC = importlib.util.spec_from_file_location("fn_nas_system_manager", SYSTEM_MANAGER_PATH)
MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)
SystemManager = MODULE.SystemManager


class StubCoordinator:
    def __init__(self):
        self.calls = 0

    async def run_command(self, _command):
        self.calls += 1
        return (
            'PRETTY_NAME="Debian GNU/Linux 12 (bookworm)"\n'
            'NAME="Debian GNU/Linux"\n'
            'VERSION_ID="12"\n'
            '__FN_NAS_KERNEL__=6.18.18.c952-trim\n'
        )


class SystemManagerVerifications(VerifyCase):
    def verify_parse_os_info_returns_release_and_kernel_details(self):
        result = SystemManager.parse_os_info(
            'PRETTY_NAME="Debian GNU/Linux 12 (bookworm)"\n'
            'VERSION_ID="12"\n'
            '__FN_NAS_KERNEL__=6.18.18.c952-trim\n'
        )

        self.assertEqual(result["operating_system"], "Debian GNU/Linux 12 (bookworm)")
        self.assertEqual(result["os_version"], "12")
        self.assertEqual(result["kernel_version"], "6.18.18.c952-trim")

    async def verify_os_info_is_read_once_per_integration_load(self):
        coordinator = StubCoordinator()
        manager = SystemManager(coordinator)

        first = await manager.get_os_info()
        second = await manager.get_os_info()

        self.assertEqual(first, second)
        self.assertEqual(coordinator.calls, 1)
