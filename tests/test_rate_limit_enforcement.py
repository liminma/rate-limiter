"""
Tests for strict rate limit enforcement.

This module verifies that rate limits are strictly enforced and cannot be exceeded.
"""

import asyncio
import time
from datetime import datetime, timedelta

import pytest

from rate_limiter.model_rate_limiter import (
    ModelRateLimiter,
    RateLimitContext,
)

from .conftest import create_test_config


class TestRPMEnforcement:
    """Tests for RPM (Requests Per Minute) limit enforcement."""

    @pytest.mark.asyncio
    @pytest.mark.slow
    async def test_rpm_limit_strictly_enforced(self):
        """Should never exceed RPM limit."""
        limiter = ModelRateLimiter(
            default_config={"rpm": 5, "tpm": 100000, "max_parallel_requests": 10}
        )

        # Try to make more requests than allowed in quick succession
        requests_made = []

        for _ in range(7):  # Try 7 requests (limit is 5)
            _ = time.time()
            await limiter.acquire("test-model", estimated_tokens=100)
            requests_made.append(time.time())
            await limiter.release("test-model", token_count=100)

        # Count requests in any 60-second window
        max_in_window = 0
        for i in range(len(requests_made)):
            window_start = requests_made[i]
            count = sum(
                1 for t in requests_made if window_start <= t < window_start + 60
            )
            max_in_window = max(max_in_window, count)

        # Should never exceed RPM limit
        assert max_in_window <= 5

    @pytest.mark.asyncio
    @pytest.mark.slow
    async def test_rpm_limit_with_context_manager(self):
        """Should enforce RPM limit when using context manager."""
        limiter = ModelRateLimiter(
            default_config={"rpm": 3, "tpm": 100000, "max_parallel_requests": 10}
        )

        request_times = []

        # Make 5 requests (limit is 3)
        for i in range(5):
            async with RateLimitContext(limiter, "test-model", 500, 500):
                request_times.append(datetime.now())

        # Check that no more than 3 requests happened in first minute
        first_minute = request_times[0] + timedelta(seconds=60)
        requests_in_first_minute = sum(1 for t in request_times if t < first_minute)

        assert requests_in_first_minute <= 3

    @pytest.mark.asyncio
    @pytest.mark.slow
    async def test_rpm_resets_after_window(self):
        """Should allow new requests after window expires."""
        limiter = ModelRateLimiter(
            default_config={"rpm": 2, "tpm": 100000, "max_parallel_requests": 5}
        )

        # Fill the limit
        await limiter.acquire("test-model")
        await limiter.release("test-model")
        await limiter.acquire("test-model")
        await limiter.release("test-model")

        # Third request should wait
        start = time.time()
        await limiter.acquire("test-model")
        wait_time = time.time() - start
        await limiter.release("test-model")

        # Should have waited close to full window
        assert wait_time > 55.0
        assert wait_time < 65.0  # Allow some overhead

    @pytest.mark.asyncio
    @pytest.mark.slow
    async def test_rpm_enforcement_concurrent_requests(self):
        """Should enforce RPM limit with concurrent requests."""
        limiter = ModelRateLimiter(
            default_config={"rpm": 3, "tpm": 100000, "max_parallel_requests": 10}
        )

        request_times = []

        async def make_request():
            await limiter.acquire("test-model")
            request_times.append(time.time())
            await limiter.release("test-model")

        # Launch 6 concurrent requests (limit is 3)
        start = time.time()
        await asyncio.gather(*[make_request() for _ in range(6)])
        total_time = time.time() - start

        # Should take at least one window to complete
        assert total_time > 55.0

        # Verify no window had more than 3 requests
        for i in range(len(request_times)):
            window_count = sum(
                1
                for t in request_times
                if request_times[i] <= t < request_times[i] + 60
            )
            assert window_count <= 3


class TestTPMEnforcement:
    """Tests for TPM (Tokens Per Minute) limit enforcement."""

    @pytest.mark.asyncio
    @pytest.mark.slow
    async def test_tpm_limit_strictly_enforced(self):
        """Should never exceed TPM limit."""
        limiter = ModelRateLimiter(
            default_config={"rpm": 100, "tpm": 1000, "max_parallel_requests": 10}
        )

        # Record all token usage with timestamps
        token_records = []

        # Make requests that together exceed TPM
        for i in range(5):
            await limiter.acquire("test-model", estimated_tokens=300)
            token_records.append((time.time(), 300))
            await limiter.release("test-model", token_count=300)

        # Check that no 60-second window exceeds TPM
        tracker = limiter._get_tracker("test-model")
        max_tokens = tracker.config.tpm

        for i in range(len(token_records)):
            window_start = token_records[i][0]
            window_tokens = sum(
                tokens
                for t, tokens in token_records
                if window_start <= t < window_start + 60
            )
            assert window_tokens <= max_tokens

    @pytest.mark.asyncio
    @pytest.mark.slow
    async def test_tpm_limit_with_varying_sizes(self):
        """Should enforce TPM with varying token sizes."""
        limiter = ModelRateLimiter(
            default_config={"rpm": 100, "tpm": 500, "max_parallel_requests": 10}
        )

        token_sizes = [100, 150, 200, 100, 150]

        for tokens in token_sizes:
            await limiter.acquire("test-model", estimated_tokens=tokens)
            await limiter.release("test-model", token_count=tokens)

        tracker = limiter._get_tracker("test-model")
        current_tokens = tracker.get_current_tokens()

        # Total should not exceed TPM
        assert current_tokens <= tracker.config.tpm

    @pytest.mark.asyncio
    @pytest.mark.slow
    async def test_tpm_waits_when_exceeded(self):
        """Should wait when TPM would be exceeded."""
        limiter = ModelRateLimiter(
            default_config={"rpm": 100, "tpm": 500, "max_parallel_requests": 10}
        )

        # Fill close to TPM limit
        await limiter.acquire("test-model", estimated_tokens=450)
        await limiter.release("test-model", token_count=450)

        # Next request should wait
        start = time.time()
        await limiter.acquire("test-model", estimated_tokens=100)
        wait_time = time.time() - start
        await limiter.release("test-model", token_count=100)

        # Should have waited
        assert wait_time > 55.0

    @pytest.mark.asyncio
    @pytest.mark.slow
    async def test_tpm_enforcement_with_context_manager(self):
        """Should enforce TPM when using context manager."""
        limiter = ModelRateLimiter(
            default_config={"rpm": 100, "tpm": 600, "max_parallel_requests": 10}
        )

        # Make requests totaling more than TPM
        for _ in range(4):
            async with RateLimitContext(limiter, "test-model", 200, 200):
                pass

        tracker = limiter._get_tracker("test-model")
        await asyncio.sleep(0.2)  # Wait for async release

        # Should not exceed TPM in any window
        assert tracker.get_current_tokens() <= tracker.config.tpm


class TestMaxParallelEnforcement:
    """Tests for max parallel requests enforcement."""

    @pytest.mark.asyncio
    @pytest.mark.slow
    async def test_max_parallel_strictly_enforced(self):
        """Should never exceed max parallel requests."""
        limiter = ModelRateLimiter(
            default_config={"rpm": 100, "tpm": 10000, "max_parallel_requests": 3}
        )

        active_count_history = []

        async def tracked_request(delay: float):
            await limiter.acquire("test-model")
            tracker = limiter._get_tracker("test-model")
            active_count_history.append(tracker.active_requests)
            await asyncio.sleep(delay)
            await limiter.release("test-model")

        # Launch 6 concurrent requests (limit is 3)
        await asyncio.gather(*[tracked_request(0.3) for _ in range(6)])

        # Should never have exceeded max parallel
        assert max(active_count_history) <= 3

    @pytest.mark.asyncio
    async def test_max_parallel_blocks_excess_requests(self):
        """Should block requests when at max parallel."""
        limiter = ModelRateLimiter(
            default_config={"rpm": 100, "tpm": 10000, "max_parallel_requests": 2}
        )

        # Start two long-running requests
        async def long_request():
            async with RateLimitContext(limiter, "test-model", 100, 100):
                await asyncio.sleep(0.5)

        task1 = asyncio.create_task(long_request())
        task2 = asyncio.create_task(long_request())
        await asyncio.sleep(0.1)

        # Third request should be blocked
        start = time.time()
        task3 = asyncio.create_task(long_request())
        await asyncio.sleep(0.1)

        # Task 3 should not have completed yet
        assert not task3.done()

        # Wait for all to complete
        await asyncio.gather(task1, task2, task3)
        duration = time.time() - start

        # Should have taken longer than single execution
        assert duration > 0.5

    @pytest.mark.asyncio
    async def test_max_parallel_releases_slots_properly(self):
        """Should properly release slots for reuse."""
        limiter = ModelRateLimiter(
            default_config={"rpm": 100, "tpm": 10000, "max_parallel_requests": 2}
        )

        # Cycle through multiple requests
        for i in range(5):
            async with RateLimitContext(limiter, "test-model", 100, 100):
                tracker = limiter._get_tracker("test-model")
                assert tracker.active_requests <= 2

            await asyncio.sleep(0.1)

        # All slots should be released
        tracker = limiter._get_tracker("test-model")
        assert tracker.active_requests == 0

    @pytest.mark.asyncio
    async def test_max_parallel_per_model_independent(self):
        """Should enforce max parallel independently per model."""
        limiter = ModelRateLimiter(
            model_configs={
                "model-a": {"rpm": 100, "tpm": 10000, "max_parallel_requests": 2},
                "model-b": {"rpm": 100, "tpm": 10000, "max_parallel_requests": 2},
            }
        )

        async def use_model(model_name: str):
            async with RateLimitContext(limiter, model_name, 100, 100):
                await asyncio.sleep(0.2)

        # Launch 4 requests: 2 for each model
        await asyncio.gather(
            use_model("model-a"),
            use_model("model-a"),
            use_model("model-b"),
            use_model("model-b"),
        )

        # Both models should have released all slots
        assert limiter._get_tracker("model-a").active_requests == 0
        assert limiter._get_tracker("model-b").active_requests == 0


class TestCombinedLimitEnforcement:
    """Tests for enforcement of all limits together."""

    @pytest.mark.asyncio
    @pytest.mark.slow
    async def test_all_limits_enforced_simultaneously(self):
        """Should enforce RPM, TPM, and parallel limits together."""
        limiter = ModelRateLimiter(
            default_config={"rpm": 3, "tpm": 500, "max_parallel_requests": 2}
        )

        request_count = 0
        max_active = 0

        async def make_request(tokens: int):
            nonlocal request_count, max_active

            await limiter.acquire("test-model", estimated_tokens=tokens)
            request_count += 1

            tracker = limiter._get_tracker("test-model")
            max_active = max(max_active, tracker.active_requests)

            await asyncio.sleep(0.1)
            await limiter.release("test-model", token_count=tokens)

        # Make requests that could violate multiple limits
        start = time.time()
        await asyncio.gather(*[make_request(150) for _ in range(6)])
        duration = time.time() - start

        # Verify all limits were respected
        tracker = limiter._get_tracker("test-model")

        # Max parallel never exceeded
        assert max_active <= 2

        # Should have taken time due to limits
        assert duration > 55.0  # At least one window wait

        # Final state should be clean
        assert tracker.active_requests == 0

    @pytest.mark.asyncio
    @pytest.mark.slow
    async def test_rpm_limit_reached_before_tpm(self):
        """Should wait for RPM even when TPM is not reached."""
        limiter = ModelRateLimiter(
            default_config={"rpm": 2, "tpm": 10000, "max_parallel_requests": 5}
        )

        # Make small token requests that hit RPM first
        for _ in range(2):
            await limiter.acquire("test-model", estimated_tokens=10)
            await limiter.release("test-model", token_count=10)

        # Third request should wait despite low token usage
        start = time.time()
        await limiter.acquire("test-model", estimated_tokens=10)
        wait_time = time.time() - start
        await limiter.release("test-model", token_count=10)

        assert wait_time > 55.0

    @pytest.mark.asyncio
    @pytest.mark.slow
    async def test_tpm_limit_reached_before_rpm(self):
        """Should wait for TPM even when RPM is not reached."""
        limiter = ModelRateLimiter(
            default_config={"rpm": 100, "tpm": 500, "max_parallel_requests": 5}
        )

        # Make one large request that fills TPM
        await limiter.acquire("test-model", estimated_tokens=450)
        await limiter.release("test-model", token_count=450)

        # Second request should wait despite RPM available
        start = time.time()
        await limiter.acquire("test-model", estimated_tokens=100)
        wait_time = time.time() - start
        await limiter.release("test-model", token_count=100)

        assert wait_time > 55.0

    @pytest.mark.asyncio
    async def test_parallel_limit_with_rpm_and_tpm(self):
        """Should enforce parallel limit even with RPM/TPM available."""
        limiter = ModelRateLimiter(
            default_config={"rpm": 100, "tpm": 10000, "max_parallel_requests": 1}
        )

        async def request_with_delay():
            async with RateLimitContext(limiter, "test-model", 100, 100):
                await asyncio.sleep(0.5)

        # Launch 3 requests
        start = time.time()
        await asyncio.gather(*[request_with_delay() for _ in range(3)])
        duration = time.time() - start

        # Should have been serialized due to parallel limit
        assert duration > 1.4  # 3 * 0.5 - some overhead


class TestRateLimitAccuracy:
    """Tests for accuracy of rate limiting."""

    @pytest.mark.asyncio
    @pytest.mark.slow
    async def test_wait_time_accuracy(self):
        """Should wait for accurate amount of time."""
        limiter = ModelRateLimiter(
            default_config={"rpm": 2, "tpm": 10000, "max_parallel_requests": 5}
        )

        # Fill RPM
        await limiter.acquire("test-model")
        await limiter.release("test-model")
        await limiter.acquire("test-model")
        await limiter.release("test-model")

        # Time the wait
        start = time.time()
        await limiter.acquire("test-model")
        actual_wait = time.time() - start
        await limiter.release("test-model")

        # Should wait close to 60 seconds
        assert 55.0 <= actual_wait <= 65.0

    @pytest.mark.asyncio
    @pytest.mark.slow
    async def test_sliding_window_behavior(self):
        """Should implement true sliding window, not fixed window."""
        limiter = ModelRateLimiter(
            default_config={"rpm": 2, "tpm": 10000, "max_parallel_requests": 5}
        )

        # Make first request
        time1 = time.time()
        await limiter.acquire("test-model")
        await limiter.release("test-model")

        # Wait 30 seconds
        await asyncio.sleep(30)

        # Make second request
        time2 = time.time()
        await limiter.acquire("test-model")
        await limiter.release("test-model")

        # Third request should wait ~30 seconds (not 60)
        start = time.time()
        await limiter.acquire("test-model")
        wait_time = time.time() - start
        await limiter.release("test-model")

        # Should wait approximately 30 seconds
        assert 25.0 <= wait_time <= 35.0

    @pytest.mark.asyncio
    @pytest.mark.slow
    async def test_buffer_time_added(self):
        """Should add buffer time to prevent edge cases."""
        limiter = ModelRateLimiter(
            default_config={"rpm": 2, "tpm": 10000, "max_parallel_requests": 5}
        )

        # Fill limit
        await limiter.acquire("test-model")
        await limiter.release("test-model")
        await limiter.acquire("test-model")
        await limiter.release("test-model")

        # Next should wait at least 60s + buffer
        start = time.time()
        await limiter.acquire("test-model")
        wait_time = time.time() - start
        await limiter.release("test-model")

        # Should include buffer (0.1s from RATE_LIMIT_BUFFER_SECONDS)
        assert wait_time >= 55.1


class TestRateLimitEdgeCases:
    """Tests for edge cases in rate limiting."""

    @pytest.mark.asyncio
    async def test_rapid_acquire_release_cycles(self):
        """Should handle rapid acquire/release cycles."""
        limiter = ModelRateLimiter(
            default_config={"rpm": 10, "tpm": 10000, "max_parallel_requests": 5}
        )

        # Rapid cycles
        for _ in range(8):
            await limiter.acquire("test-model", estimated_tokens=50)
            await limiter.release("test-model", token_count=50)

        tracker = limiter._get_tracker("test-model")
        assert tracker.active_requests == 0
        assert tracker.get_current_requests() == 8

    @pytest.mark.asyncio
    @pytest.mark.slow
    async def test_exactly_at_limit(self):
        """Should handle being exactly at limit."""
        limiter = ModelRateLimiter(
            default_config={"rpm": 3, "tpm": 300, "max_parallel_requests": 5}
        )

        # Make exactly RPM requests
        for _ in range(3):
            await limiter.acquire("test-model", estimated_tokens=100)
            await limiter.release("test-model", token_count=100)

        # Next should wait
        start = time.time()
        await limiter.acquire("test-model", estimated_tokens=100)
        wait_time = time.time() - start
        await limiter.release("test-model", token_count=100)

        assert wait_time > 55.0

    @pytest.mark.asyncio
    @pytest.mark.slow
    async def test_zero_config_values(self):
        """Should handle edge case configs."""
        # Very low limits
        limiter = ModelRateLimiter(
            default_config={"rpm": 1, "tpm": 10, "max_parallel_requests": 1}
        )

        await limiter.acquire("test-model", estimated_tokens=5)
        await limiter.release("test-model", token_count=5)

        # Second request should wait
        start = time.time()
        await limiter.acquire("test-model", estimated_tokens=5)
        wait_time = time.time() - start
        await limiter.release("test-model", token_count=5)

        assert wait_time > 55.0
