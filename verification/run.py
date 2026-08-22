"""Run all repository verification cases."""

import asyncio
import importlib
import inspect
from pathlib import Path
import traceback

from verification.support import VerifyCase


def _module_names():
    directory = Path(__file__).resolve().parent
    return [
        f"verification.{path.stem}"
        for path in sorted(directory.glob("verify_*.py"))
    ]


def _case_methods(module):
    for _name, case_type in inspect.getmembers(module, inspect.isclass):
        if (
            case_type is VerifyCase
            or not issubclass(case_type, VerifyCase)
            or case_type.__module__ != module.__name__
        ):
            continue
        for method_name in sorted(name for name in dir(case_type) if name.startswith("verify_")):
            yield case_type, method_name


async def _run_case(case_type, method_name):
    result = getattr(case_type(), method_name)()
    if inspect.isawaitable(result):
        await result


def main() -> int:
    completed = 0
    failures = 0
    for module_name in _module_names():
        module = importlib.import_module(module_name)
        for case_type, method_name in _case_methods(module):
            label = f"{module_name}.{case_type.__name__}.{method_name}"
            try:
                asyncio.run(_run_case(case_type, method_name))
            except Exception:
                failures += 1
                print(f"{label} ... FAILED")
                traceback.print_exc()
            else:
                completed += 1
                print(f"{label} ... ok")

    print(f"\nCompleted {completed + failures} checks: {completed} passed, {failures} failed")
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
