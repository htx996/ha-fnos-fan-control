"""Small dependency-free verification helpers."""

from contextlib import contextmanager
import logging


_MISSING = object()


class _RecordCollector(logging.Handler):
    def __init__(self, level: int):
        super().__init__(level)
        self.records = []

    def emit(self, record):
        self.records.append(record)


class _LogCapture:
    def __init__(self, logger: logging.Logger, level: str | int):
        self.logger = logger
        self.level = (
            logging.getLevelNamesMapping()[level.upper()]
            if isinstance(level, str)
            else level
        )
        self.handler = _RecordCollector(self.level)
        self.previous_level = logger.level

    def __enter__(self):
        if self.logger.getEffectiveLevel() > self.level:
            self.logger.setLevel(self.level)
        self.logger.addHandler(self.handler)
        return self

    def __exit__(self, exc_type, _exc_value, _traceback):
        self.logger.removeHandler(self.handler)
        self.logger.setLevel(self.previous_level)
        if exc_type is None and not self.handler.records:
            raise AssertionError(f"No log records at level {self.level} or higher")
        return False


class VerifyCase:
    """Assertion methods used by repository verification cases."""

    def assertEqual(self, first, second, message=None):
        if first != second:
            raise AssertionError(message or f"{first!r} != {second!r}")

    def assertTrue(self, value, message=None):
        if not value:
            raise AssertionError(message or f"Expected truthy value, got {value!r}")

    def assertFalse(self, value, message=None):
        if value:
            raise AssertionError(message or f"Expected falsey value, got {value!r}")

    def assertIs(self, first, second, message=None):
        if first is not second:
            raise AssertionError(message or f"{first!r} is not {second!r}")

    def assertIsNone(self, value, message=None):
        if value is not None:
            raise AssertionError(message or f"Expected None, got {value!r}")

    def assertIsNotNone(self, value, message=None):
        if value is None:
            raise AssertionError(message or "Expected a non-None value")

    def assertIn(self, member, container, message=None):
        if member not in container:
            raise AssertionError(message or f"{member!r} not found in {container!r}")

    def assertNotIn(self, member, container, message=None):
        if member in container:
            raise AssertionError(message or f"{member!r} unexpectedly found in {container!r}")

    def assertIsInstance(self, value, expected_type, message=None):
        if not isinstance(value, expected_type):
            raise AssertionError(
                message or f"{value!r} is not an instance of {expected_type!r}"
            )

    def assertLogs(self, logger, level="INFO"):
        return _LogCapture(logger, level)


@contextmanager
def patch_modules(module_map, updates):
    """Temporarily replace selected entries in a module mapping."""
    original = {key: module_map.get(key, _MISSING) for key in updates}
    module_map.update(updates)
    try:
        yield
    finally:
        for key, value in original.items():
            if value is _MISSING:
                module_map.pop(key, None)
            else:
                module_map[key] = value
