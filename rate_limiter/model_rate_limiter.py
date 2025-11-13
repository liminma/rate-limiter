"""
Model-specific rate limiter for LLM APIs.

This module implements rate limiting for Language Model APIs using sliding time windows.
It tracks requests per minute (RPM), tokens per minute (TPM), and maximum parallel requests
separately for each model.

Typical usage:
    limiter = ModelRateLimiter(
        model_configs={
            "gpt-4": {"rpm": 500, "tpm": 40000, "max_parallel_requests": 10},
            "claude-3": {"rpm": 1000, "tpm": 80000, "max_parallel_requests": 5},
        },
        default_config={"rpm": 10, "tpm": 200000, "max_parallel_requests": 5}
    )

    # Preferred: Use context manager
    async with RateLimitContext(limiter, "gpt-4", estimated_tokens=8000, actual_tokens=7500):
        response = await call_api()

    # Alternative: Manual acquire/release
    await limiter.acquire("gpt-4", estimated_tokens=8000)
    try:
        response = await call_api()
    finally:
        await limiter.release("gpt-4", token_count=7500)
"""

import asyncio
import logging
from collections import deque
from dataclasses import dataclass
from datetime import datetime, timedelta

# Module-level constants
RATE_LIMIT_BUFFER_SECONDS = 0.1  # Buffer to avoid edge cases in timing
TOKEN_HISTORY_MAX_SIZE = 100  # Maximum number of token entries to keep
DEFAULT_ESTIMATED_TOKENS = 5000  # Reasonable default for LLM APIs
TIME_WINDOW_SECONDS = 60  # Rate limit window (1 minute)

# Configure module logger
logger = logging.getLogger(__name__)


@dataclass
class RateLimitConfig:
    """Configuration for rate limiting a model."""

    rpm: int = 10
    tpm: int = 200000
    max_parallel_requests: int = 5


class TimeWindowQueue:
    """Manages time-based queue with automatic cleanup."""

    def __init__(self, window_seconds: int = TIME_WINDOW_SECONDS):
        self.queue: deque = deque()
        self.window_seconds = window_seconds

    def add(self, value=None) -> None:
        """Add an entry with current timestamp."""
        self.queue.append((datetime.now(), value))

    def clean(self) -> None:
        """Remove entries older than the time window."""
        cutoff = datetime.now() - timedelta(seconds=self.window_seconds)
        while self.queue and self.queue[0][0] < cutoff:
            self.queue.popleft()

    def count(self) -> int:
        """Get count of entries within time window."""
        self.clean()
        return len(self.queue)

    def sum_values(self) -> int:
        """Sum all values within time window."""
        self.clean()
        return sum(value for _, value in self.queue if value is not None)

    def get_oldest_time(self) -> datetime | None:
        """Get timestamp of oldest entry."""
        return self.queue[0][0] if self.queue else None

    def limit_size(self, max_size: int) -> None:
        """Limit queue size by removing oldest entries."""
        while len(self.queue) > max_size:
            self.queue.popleft()


class ModelRateLimitTracker:
    """Tracks rate limit state for a single model."""

    def __init__(self, config: RateLimitConfig):
        self.config = config
        self.request_times = TimeWindowQueue()
        self.token_usage = TimeWindowQueue()
        self.active_requests = 0
        self.semaphore = asyncio.Semaphore(config.max_parallel_requests)
        self.lock = asyncio.Lock()

    def get_current_requests(self) -> int:
        """Get number of requests in current window."""
        return self.request_times.count()

    def get_current_tokens(self) -> int:
        """Get number of tokens used in current window."""
        return self.token_usage.sum_values()

    def calculate_rpm_wait_time(self) -> float:
        """Calculate wait time needed for RPM limit."""
        if self.get_current_requests() < self.config.rpm:
            return 0.0

        oldest = self.request_times.get_oldest_time()
        if oldest:
            return max(
                0.0, TIME_WINDOW_SECONDS - (datetime.now() - oldest).total_seconds()
            )
        return 0.0

    def calculate_tpm_wait_time(self, estimated_tokens: int) -> float:
        """Calculate wait time needed for TPM limit."""
        current_tokens = self.get_current_tokens()
        if current_tokens + estimated_tokens <= self.config.tpm:
            return 0.0

        oldest = self.token_usage.get_oldest_time()
        if oldest:
            return max(
                0.0, TIME_WINDOW_SECONDS - (datetime.now() - oldest).total_seconds()
            )
        return 0.0

    def record_request(self) -> None:
        """Record a new request."""
        self.request_times.add()

    def record_tokens(self, token_count: int) -> None:
        """Record token usage."""
        self.token_usage.add(token_count)
        self.token_usage.limit_size(TOKEN_HISTORY_MAX_SIZE)

    def get_stats(self) -> dict[str, str]:
        """Get current statistics."""
        return {
            "requests": f"{self.get_current_requests()}/{self.config.rpm}",
            "tokens": f"{self.get_current_tokens()}/{self.config.tpm}",
            "active": f"{self.active_requests}/{self.config.max_parallel_requests}",
        }

    async def wait_for_limits(self, model_name: str, estimated_tokens: int) -> None:
        """
        Wait if rate limits would be exceeded.

        Checks both RPM and TPM limits and sleeps if necessary.
        Must be called while holding self.lock.

        Args:
            model_name: Name of the model (for logging)
            estimated_tokens: Estimated token usage for this request
        """
        # Check RPM limit
        rpm_wait = self.calculate_rpm_wait_time()
        if rpm_wait > 0:
            current = self.get_current_requests()
            logger.info(
                f"⏳ [{model_name}] RPM limit reached: {current}/{self.config.rpm} "
                f"requests. Waiting {rpm_wait:.1f}s..."
            )
            await asyncio.sleep(rpm_wait + RATE_LIMIT_BUFFER_SECONDS)

        # Check TPM limit
        tpm_wait = self.calculate_tpm_wait_time(estimated_tokens)
        if tpm_wait > 0:
            current = self.get_current_tokens()
            logger.info(
                f"⏳ [{model_name}] TPM limit reached: {current}/{self.config.tpm} "
                f"tokens. Waiting {tpm_wait:.1f}s..."
            )
            await asyncio.sleep(tpm_wait + RATE_LIMIT_BUFFER_SECONDS)


class ModelRateLimiter:
    """
    Rate limiter that tracks limits separately per model.

    This class manages rate limiting for multiple models, each with its own
    RPM, TPM, and parallel request limits. It uses sliding time windows to
    track usage and enforces limits before allowing requests to proceed.

    Example:
        >>> limiter = ModelRateLimiter(
        ...     model_configs={
        ...         "gpt-4": {"rpm": 500, "tpm": 40000, "max_parallel_requests": 10},
        ...     },
        ...     default_config={"rpm": 10, "tpm": 200000, "max_parallel_requests": 5}
        ... )
        >>>
        >>> # Use with context manager (recommended)
        >>> async with RateLimitContext(limiter, "gpt-4", 8000, 7500):
        ...     response = await call_api()
    """

    def __init__(
        self,
        model_configs: dict[str, dict] | None = None,
        default_config: dict | None = None,
    ):
        """
        Initialize rate limiter with per-model configurations.

        Args:
            model_configs: Dictionary mapping model names to their rate limit configs.
                          Each config should contain 'rpm', 'tpm', and 'max_parallel_requests'.
            default_config: Default config for models not explicitly specified.
                           If None, uses default values from RateLimitConfig.
        """
        self.model_configs = self._parse_configs(model_configs or {})
        self.default_config = self._parse_config(default_config)
        self.trackers: dict[str, ModelRateLimitTracker] = {}

        logger.debug(
            f"ModelRateLimiter initialized with {len(self.model_configs)} model configs"
        )

    def _parse_config(self, config: dict | None) -> RateLimitConfig:
        """Convert dict config to RateLimitConfig."""
        if config is None:
            return RateLimitConfig()
        return RateLimitConfig(**config)

    def _parse_configs(self, configs: dict[str, dict]) -> dict[str, RateLimitConfig]:
        """Convert dict configs to RateLimitConfig objects."""
        return {model: self._parse_config(cfg) for model, cfg in configs.items()}

    def _get_tracker(self, model_name: str) -> ModelRateLimitTracker:
        """Get or create tracker for a model."""
        if model_name not in self.trackers:
            config = self.model_configs.get(model_name, self.default_config)
            self.trackers[model_name] = ModelRateLimitTracker(config)
            logger.debug(
                f"Created tracker for '{model_name}': "
                f"RPM={config.rpm}, TPM={config.tpm}, "
                f"Max Parallel={config.max_parallel_requests}"
            )
        return self.trackers[model_name]

    async def acquire(
        self, model_name: str, estimated_tokens: int = DEFAULT_ESTIMATED_TOKENS
    ) -> None:
        """
        Acquire permission to make a request for specific model.

        This method:
        1. Waits for an available parallel request slot (if at max)
        2. Increments active request count
        3. Waits if RPM or TPM limits would be exceeded
        4. Records the request

        Args:
            model_name: Name of the model to rate limit
            estimated_tokens: Estimated token usage for this request

        Example:
            >>> await limiter.acquire("gpt-4", estimated_tokens=8000)
            >>> try:
            ...     response = await call_api()
            ... finally:
            ...     await limiter.release("gpt-4", token_count=7500)
        """
        tracker = self._get_tracker(model_name)

        # Check if at max parallel requests
        if tracker.active_requests >= tracker.config.max_parallel_requests:
            logger.info(
                f"⏳ [{model_name}] Max parallel requests reached: "
                f"{tracker.active_requests}/{tracker.config.max_parallel_requests}. "
                f"Waiting for available slot..."
            )

        # Wait for available slot
        await tracker.semaphore.acquire()

        # Update state under lock
        async with tracker.lock:
            tracker.active_requests += 1
            logger.debug(
                f"🔄 [{model_name}] Request acquired. Active: "
                f"{tracker.active_requests}/{tracker.config.max_parallel_requests}"
            )

            # Wait for rate limits if necessary
            await tracker.wait_for_limits(model_name, estimated_tokens)

            # Record this request
            tracker.record_request()

    async def release(
        self, model_name: str, token_count: int = DEFAULT_ESTIMATED_TOKENS
    ) -> None:
        """
        Release the parallel request slot for specific model.

        This method:
        1. Releases the semaphore slot
        2. Decrements active request count
        3. Records actual token usage

        Args:
            model_name: Name of the model to release
            token_count: Actual number of tokens used in the request

        Example:
            >>> await limiter.acquire("gpt-4", estimated_tokens=8000)
            >>> try:
            ...     response = await call_api()
            ... finally:
            ...     await limiter.release("gpt-4", token_count=7500)
        """
        tracker = self._get_tracker(model_name)

        # Release semaphore immediately
        tracker.semaphore.release()

        # Update state under lock
        async with tracker.lock:
            tracker.active_requests -= 1
            logger.debug(
                f"✅ [{model_name}] Request completed. Active: "
                f"{tracker.active_requests}/{tracker.config.max_parallel_requests}"
            )

        # Record token usage (outside lock since it has its own synchronization)
        tracker.record_tokens(token_count)

    def get_stats(self) -> dict[str, dict[str, str]]:
        """
        Get current statistics for all models.

        Returns:
            Dictionary mapping model names to their current statistics.
            Each stat dict contains 'requests', 'tokens', and 'active' counts.

        Example:
            >>> stats = limiter.get_stats()
            >>> print(stats)
            {
                'gpt-4': {
                    'requests': '45/500',
                    'tokens': '35000/40000',
                    'active': '3/10'
                }
            }
        """
        all_models = set(self.trackers.keys()) | set(self.model_configs.keys())
        return {model: self._get_tracker(model).get_stats() for model in all_models}


class RateLimitContext:
    """
    Context manager for rate limit acquisition and release.

    This is the recommended way to use the rate limiter. It ensures proper
    cleanup even if exceptions occur during request processing.

    Example:
        >>> limiter = ModelRateLimiter()
        >>> async with RateLimitContext(limiter, "gpt-4", 8000, 7500) as ctx:
        ...     response = await call_api()
        ...     # Rate limit is automatically released on exit
    """

    def __init__(
        self,
        rate_limiter: ModelRateLimiter,
        model_name: str,
        estimated_tokens: int = DEFAULT_ESTIMATED_TOKENS,
        actual_tokens: int = DEFAULT_ESTIMATED_TOKENS,
    ):
        """
        Initialize the rate limit context.

        Args:
            rate_limiter: The ModelRateLimiter instance to use
            model_name: Name of the model to rate limit
            estimated_tokens: Estimated token usage (used for pre-checking limits)
            actual_tokens: Actual token usage (recorded after completion)
        """
        self.rate_limiter = rate_limiter
        self.model_name = model_name
        self.estimated_tokens = estimated_tokens
        self.actual_tokens = actual_tokens

    async def __aenter__(self):
        """Acquire rate limit on entry."""
        await self.rate_limiter.acquire(self.model_name, self.estimated_tokens)
        return self

    async def __aexit__(self, exc_type, exc_val, exc_tb):
        """Release rate limit on exit."""
        await self.rate_limiter.release(self.model_name, self.actual_tokens)
        return False
