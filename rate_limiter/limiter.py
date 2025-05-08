import functools
import time
from threading import Lock
import asyncio


"""
This module implements rate limiting using the token bucket algorithm.

It includes both a synchronous `RateLimiter` class and an asynchronous `AsyncRateLimiter` class.
It also provides decorator factories for applying rate limits to regular and async functions.
"""


class _BaseRateLimiterLogic:
    """
    Base class for rate limiting that implements the token bucket algorithm.
    It's not intended to be used directly.
    """

    def __init__(self, requests_per_second: int | float):
        """Initializes the core parameters.

        Args:
            requests_per_second: The number of requests allowed per second.
                                 Must be a positive number (int or float).
                                 This defines the rate at which tokens are generated.
                                 The maximum number of tokens that can be accumulated
                                 (burst capacity) is `max(1.0, requests_per_second)`.

        Raises:
            ValueError: If 'requests_per_second' is not a positive number.
        """
        if not isinstance(requests_per_second, (int, float)) or requests_per_second <= 0.0:
            raise ValueError("requests_per_second must be a positive number.")

        self.requests_per_second = float(requests_per_second)
        # Bucket capacity is at least 1
        self.bucket_capacity = max(1.0, self.requests_per_second)
        self.allowance = self.bucket_capacity
        self.last_check = time.monotonic()

    def _update_state_and_get_wait_time(self) -> float:
        """
        Updates token allowance based on time passed, calculates necessary wait time,
        updates internal state for the next check, and consumes one token.

        This method assumes that synchronization (locking) is handled by the caller
        (i.e., the `acquire` method of the subclass).

        Returns:
            The time to wait in seconds before the request can proceed.
        """
        current_time = time.monotonic()
        time_passed = current_time - self.last_check

        # Add newly generated tokens but capped at bucket capacity
        self.allowance += time_passed * self.requests_per_second
        self.allowance = min(self.allowance, self.bucket_capacity)

        wait_time = 0.0
        if self.allowance < 1.0:
            wait_time = (1.0 - self.allowance) / self.requests_per_second
            self.allowance = 1.0

        # Update last_check to when token is granted
        self.last_check = current_time + wait_time
        self.allowance -= 1.0

        return wait_time


class RateLimiter(_BaseRateLimiterLogic):
    """
    A thread-safe rate limiter.

    Allows a fixed number of requests per second with a bucket that refills at a constant rate.
    Each request consumes one token. If the bucket is empty, the request blocks until a token is available.
    """

    def __init__(self, requests_per_second: int | float):
        """
        Initializes the rate limiter.

        Args:
            requests_per_second: The number of requests allowed per second.
                                 Must be a positive number (int or float).
                                 This defines the rate at which tokens are generated.
                                 The maximum number of tokens that can be accumulated
                                 (burst capacity) is `max(1.0, requests_per_second)`.

        Raises:
            ValueError: If 'requests_per_second' is not a positive number.
        """
        super().__init__(requests_per_second)
        self.lock = Lock()

    def acquire(self):
        """
        Acquires a token, blocking if necessary until a token is available.

        This method is thread-safe. It calculates tokens accrued since the last call,
        and adds them to the allowance (up to `bucket_capacity`). `last_check` is
        updated to the time the token was effectively granted.
        """
        self.lock.acquire()
        try:  # Use try/finally to ensure lock is always released
            wait_time = self._update_state_and_get_wait_time()
            if wait_time > 0:
                time.sleep(wait_time)
        finally:
            self.lock.release()


class AsyncRateLimiter(_BaseRateLimiterLogic):
    """
    An asyncio-compatible rate limiter.

    Allows a fixed number of requests per second for async operations.
    It functions similarly to the synchronous `RateLimiter` but uses asyncio primitives
    for locking and sleeping, making it suitable for non-blocking asynchronous code.
    """

    def __init__(self, requests_per_second: int | float):
        """
        Initializes the async rate limiter.

        Args:
            requests_per_second: The number of requests allowed per second.
                                 Must be a positive number (int or float).
                                 This defines the rate at which tokens are generated.
                                 The maximum number of tokens that can be accumulated
                                 (burst capacity) is `max(1.0, requests_per_second)`.
        Raises:
            ValueError: If 'requests_per_second' is not a positive number.
        """
        super().__init__(requests_per_second)
        self.lock = asyncio.Lock()

    async def acquire(self):
        """
        Asynchronously acquires a token, pausing if necessary until a token is available.

        This method is safe for concurrent asyncio tasks. It calculates tokens accrued
        since the last call, and adds them to the allowance (up to `bucket_capacity`).
        `last_check` is updated to the time the token was effectively granted.
        """
        async with self.lock:
            wait_time = self._update_state_and_get_wait_time()
            if wait_time > 0:
                await asyncio.sleep(wait_time)


def limit_rate(*, limiter: RateLimiter | None = None, requests_per_second: int | float | None = None):
    """
    Decorator factory that creates a rate limiter for the decorated function.

    Args:
        limiter: An existing RateLimiter instance to use.
                 If provided, 'requests_per_second' is ignored.
        requests_per_second: The desired rate limit in requests per second.
                             This is ignored if 'limiter' is provided.
                             Must be provided if 'limiter' is None.

    Returns:
        A decorator function.

    Raises:
        TypeError: If a provided 'limiter' is not an instance of RateLimiter.
        ValueError: If neither 'requests_per_second' nor 'limiter' is provided,
                    or if 'requests_per_second' is invalid when 'limiter' is not provided.
    """
    if limiter is not None:
        if not isinstance(limiter, RateLimiter):
            raise TypeError(f"Provided 'limiter' must be an instance of RateLimiter.")
    elif requests_per_second is not None:
        limiter = RateLimiter(requests_per_second)
    else:
        raise ValueError(
            "Either a 'limiter' (RateLimiter instance) or 'requests_per_second' (positive number) must be provided."
        )

    def decorator(func):
        """The actual decorator"""

        @functools.wraps(func)
        def wrapper(*args, **kwargs):
            # Apply rate limiting before calling the function
            limiter.acquire()
            return func(*args, **kwargs)

        return wrapper

    return decorator


def async_limit_rate(*, limiter: AsyncRateLimiter | None = None, requests_per_second: int | float | None = None):
    """
    Decorator factory that creates an asyncio-compatible rate limiter for the decorated async function.

    Args:
        limiter: An existing AsyncRateLimiter instance to use.
                 If provided, 'requests_per_second' is ignored.
        requests_per_second: The desired rate limit in requests per second.
                             This is ignored if 'limiter' is provided.
                             Must be provided if 'limiter' is None.

    Returns:
        A decorator function.

    Raises:
        TypeError: If a provided 'limiter' is not an instance of AsyncRateLimiter.
        ValueError: If neither 'requests_per_second' nor 'limiter' is provided,
                    or if 'requests_per_second' is invalid when 'limiter' is not provided.
    """
    if limiter is not None:
        if not isinstance(limiter, AsyncRateLimiter):
            raise TypeError(f"Provided 'limiter' must be an instance of AsyncRateLimiter.")
    elif requests_per_second is not None:
        limiter = AsyncRateLimiter(requests_per_second)
    else:
        raise ValueError(
            "Either a 'limiter' (AsyncRateLimiter instance) or 'requests_per_second' (positive number) must be provided."
        )

    def decorator(func):
        """The actual decorator"""

        @functools.wraps(func)
        async def wrapper(*args, **kwargs):
            # Apply rate limiting before calling the function
            await limiter.acquire()
            return await func(*args, **kwargs)

        return wrapper

    return decorator
