"""
Shared test fixtures and utilities for model_rate_limiter tests.
"""

import asyncio
import logging
from datetime import datetime, timedelta
from unittest.mock import MagicMock

import pytest

from rate_limiter.model_rate_limiter import (
    ModelRateLimiter,
    ModelRateLimitTracker,
    RateLimitConfig,
    TimeWindowQueue,
)

# ============================================================================
# Configuration Fixtures
# ============================================================================


@pytest.fixture
def rate_limit_config():
    """Standard test configuration with reasonable limits."""
    return RateLimitConfig(rpm=10, tpm=1000, max_parallel_requests=3)


@pytest.fixture
def strict_rate_limit_config():
    """Stricter configuration for testing limit enforcement."""
    return RateLimitConfig(rpm=2, tpm=100, max_parallel_requests=1)


@pytest.fixture
def lenient_rate_limit_config():
    """Lenient configuration for tests that don't need limits."""
    return RateLimitConfig(rpm=1000, tpm=1000000, max_parallel_requests=100)


# ============================================================================
# Component Fixtures
# ============================================================================


@pytest.fixture
def time_window_queue():
    """Fresh TimeWindowQueue instance."""
    return TimeWindowQueue()


@pytest.fixture
def tracker(rate_limit_config):
    """ModelRateLimitTracker with standard config."""
    return ModelRateLimitTracker(rate_limit_config)


@pytest.fixture
def strict_tracker(strict_rate_limit_config):
    """ModelRateLimitTracker with strict config for limit testing."""
    return ModelRateLimitTracker(strict_rate_limit_config)


@pytest.fixture
def model_rate_limiter(rate_limit_config):
    """ModelRateLimiter with standard default config."""
    return ModelRateLimiter(
        default_config={
            "rpm": rate_limit_config.rpm,
            "tpm": rate_limit_config.tpm,
            "max_parallel_requests": rate_limit_config.max_parallel_requests,
        }
    )


@pytest.fixture
def multi_model_limiter():
    """ModelRateLimiter configured for multiple models."""
    return ModelRateLimiter(
        model_configs={
            "gpt-4": {"rpm": 500, "tpm": 40000, "max_parallel_requests": 10},
            "gpt-3.5": {"rpm": 3500, "tpm": 90000, "max_parallel_requests": 20},
            "claude-3": {"rpm": 1000, "tpm": 80000, "max_parallel_requests": 5},
        },
        default_config={"rpm": 10, "tpm": 1000, "max_parallel_requests": 3},
    )


# ============================================================================
# Logging Fixtures
# ============================================================================


@pytest.fixture
def capture_logs(caplog):
    """Capture log messages at INFO level."""
    caplog.set_level(logging.INFO)
    return caplog


@pytest.fixture
def capture_debug_logs(caplog):
    """Capture log messages at DEBUG level."""
    caplog.set_level(logging.DEBUG)
    return caplog


@pytest.fixture
def disable_logging():
    """Disable logging for performance-critical tests."""
    logger = logging.getLogger("model_rate_limiter")
    original_level = logger.level
    logger.setLevel(logging.CRITICAL + 1)
    yield
    logger.setLevel(original_level)


# ============================================================================
# Helper Functions
# ============================================================================


def create_test_config(**overrides):
    """
    Create a RateLimitConfig with custom overrides.

    Args:
        **overrides: Values to override in the default config

    Returns:
        RateLimitConfig with specified overrides

    Example:
        >>> config = create_test_config(rpm=100, tpm=5000)
    """
    defaults = {
        "rpm": 10,
        "tpm": 1000,
        "max_parallel_requests": 3,
    }
    defaults.update(overrides)
    return RateLimitConfig(**defaults)


async def fill_rpm_limit(tracker: ModelRateLimitTracker):
    """
    Fill the RPM limit to capacity for a tracker.

    Args:
        tracker: The tracker to fill

    Example:
        >>> await fill_rpm_limit(tracker)
        >>> assert tracker.get_current_requests() == tracker.config.rpm
    """
    for _ in range(tracker.config.rpm):
        tracker.record_request()


async def fill_tpm_limit(
    tracker: ModelRateLimitTracker, tokens_per_request: int = None
):
    """
    Fill the TPM limit to capacity for a tracker.

    Args:
        tracker: The tracker to fill
        tokens_per_request: Tokens per request (default: TPM / 2)

    Example:
        >>> await fill_tpm_limit(tracker, tokens_per_request=500)
        >>> assert tracker.get_current_tokens() >= tracker.config.tpm
    """
    if tokens_per_request is None:
        tokens_per_request = tracker.config.tpm // 2

    tokens_recorded = 0
    while tokens_recorded < tracker.config.tpm:
        tracker.record_tokens(tokens_per_request)
        tokens_recorded += tokens_per_request


def assert_wait_time_approximately(
    actual: float, expected: float, tolerance: float = 0.2
):
    """
    Assert that a wait time is approximately equal to expected.

    Args:
        actual: Actual wait time in seconds
        expected: Expected wait time in seconds
        tolerance: Acceptable difference in seconds

    Raises:
        AssertionError: If actual is not within tolerance of expected
    """
    diff = abs(actual - expected)
    assert diff <= tolerance, (
        f"Wait time {actual:.2f}s not within {tolerance:.2f}s of expected {expected:.2f}s "
        f"(difference: {diff:.2f}s)"
    )


async def wait_until(condition, timeout: float = 5.0, interval: float = 0.1):
    """
    Wait until a condition becomes true.

    Args:
        condition: Callable that returns bool
        timeout: Maximum time to wait in seconds
        interval: How often to check condition

    Raises:
        TimeoutError: If condition doesn't become true within timeout

    Example:
        >>> await wait_until(lambda: tracker.active_requests == 0, timeout=2.0)
    """
    elapsed = 0.0
    while not condition():
        if elapsed >= timeout:
            raise TimeoutError(f"Condition not met within {timeout}s")
        await asyncio.sleep(interval)
        elapsed += interval


async def measure_execution_time(coro):
    """
    Measure the execution time of a coroutine.

    Args:
        coro: Coroutine to measure

    Returns:
        Tuple of (result, execution_time_seconds)

    Example:
        >>> result, duration = await measure_execution_time(limiter.acquire("gpt-4"))
        >>> assert duration < 1.0
    """
    start = asyncio.get_event_loop().time()
    result = await coro
    end = asyncio.get_event_loop().time()
    return result, end - start


class AsyncContextStack:
    """Helper for managing multiple async context managers in tests."""

    def __init__(self):
        self.contexts = []
        self.entered = []

    def add(self, context):
        """Add a context manager to the stack."""
        self.contexts.append(context)
        return self

    async def __aenter__(self):
        """Enter all contexts."""
        for ctx in self.contexts:
            entered = await ctx.__aenter__()
            self.entered.append((ctx, entered))
        return self

    async def __aexit__(self, exc_type, exc_val, exc_tb):
        """Exit all contexts in reverse order."""
        # Exit in reverse order
        for ctx, _ in reversed(self.entered):
            await ctx.__aexit__(exc_type, exc_val, exc_tb)
        return False


# ============================================================================
# Mock Time Helpers
# ============================================================================


class MockTimeController:
    """
    Helper for controlling time in tests without actually sleeping.
    Note: This is a simple implementation. For production use, consider
    using freezegun or time-machine libraries.
    """

    def __init__(self):
        self.current_time = datetime.now()
        self.original_datetime_now = datetime.now

    def advance(self, seconds: float):
        """Advance time by specified seconds."""
        self.current_time += timedelta(seconds=seconds)

    def now(self):
        """Get current mocked time."""
        return self.current_time

    def reset(self):
        """Reset to real time."""
        self.current_time = self.original_datetime_now()


@pytest.fixture
def mock_time():
    """Provide a mock time controller."""
    return MockTimeController()


# ============================================================================
# Performance Helpers
# ============================================================================


async def run_concurrent_tasks(tasks, max_concurrent: int = None):
    """
    Run multiple tasks with optional concurrency limit.

    Args:
        tasks: List of coroutines to run
        max_concurrent: Maximum number of concurrent tasks (None = unlimited)

    Returns:
        List of results in same order as tasks

    Example:
        >>> tasks = [limiter.acquire(f"model-{i}") for i in range(10)]
        >>> results = await run_concurrent_tasks(tasks, max_concurrent=3)
    """
    if max_concurrent is None:
        return await asyncio.gather(*tasks)

    semaphore = asyncio.Semaphore(max_concurrent)

    async def limited_task(task):
        async with semaphore:
            return await task

    return await asyncio.gather(*[limited_task(t) for t in tasks])


def count_log_messages(caplog, level: str, substring: str = None) -> int:
    """
    Count log messages at a specific level, optionally containing substring.

    Args:
        caplog: pytest caplog fixture
        level: Log level name (e.g., "INFO", "DEBUG")
        substring: Optional substring to search for

    Returns:
        Count of matching log messages
    """
    count = 0
    for record in caplog.records:
        if record.levelname == level:
            if substring is None or substring in record.message:
                count += 1
    return count
