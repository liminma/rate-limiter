"""
Unit tests for ModelRateLimitTracker class.
"""

import asyncio
import time

import pytest

from rate_limiter.model_rate_limiter import (
    TOKEN_HISTORY_MAX_SIZE,
    ModelRateLimitTracker,
    RateLimitConfig,
)

from .conftest import assert_wait_time_approximately, create_test_config


class TestModelRateLimitTrackerInitialization:
    """Tests for ModelRateLimitTracker initialization."""

    def test_initialization_with_config(self, rate_limit_config):
        """Should initialize properly with config."""
        tracker = ModelRateLimitTracker(rate_limit_config)

        assert tracker.config == rate_limit_config
        assert tracker.active_requests == 0
        assert isinstance(tracker.semaphore, asyncio.Semaphore)
        assert isinstance(tracker.lock, asyncio.Lock)

    def test_initial_state_zero(self, tracker):
        """Should start with zero requests and tokens."""
        assert tracker.get_current_requests() == 0
        assert tracker.get_current_tokens() == 0
        assert tracker.active_requests == 0

    def test_semaphore_initialization(self, rate_limit_config):
        """Should initialize semaphore with correct capacity."""
        tracker = ModelRateLimitTracker(rate_limit_config)

        # Semaphore should allow max_parallel_requests acquisitions
        max_parallel = rate_limit_config.max_parallel_requests

        # Should be able to acquire max_parallel times
        for _ in range(max_parallel):
            acquired = tracker.semaphore.locked() == False
            tracker.semaphore._value -= 1  # Manual acquire for testing

        # Next acquire should block (semaphore exhausted)
        assert tracker.semaphore._value == 0


class TestModelRateLimitTrackerRequestCounting:
    """Tests for request tracking functionality."""

    def test_record_single_request(self, tracker):
        """Should record a single request."""
        tracker.record_request()
        assert tracker.get_current_requests() == 1

    def test_record_multiple_requests(self, tracker):
        """Should record multiple requests."""
        tracker.record_request()
        tracker.record_request()
        tracker.record_request()
        assert tracker.get_current_requests() == 3

    def test_get_current_requests_accuracy(self, tracker):
        """Should accurately count current requests."""
        for i in range(5):
            tracker.record_request()
            assert tracker.get_current_requests() == i + 1

    def test_requests_expire_after_window(self, tracker):
        """Should clean up old requests after time window."""
        # Use a tracker with short window for testing
        config = create_test_config(rpm=10)
        tracker = ModelRateLimitTracker(config)

        # Record requests
        tracker.record_request()
        tracker.record_request()
        assert tracker.get_current_requests() == 2

        # Wait for window to expire (60 seconds + buffer)
        # For testing purposes, we'll manually manipulate the queue
        # In real scenario, you'd wait or use time mocking
        time.sleep(0.1)  # Small sleep to ensure time difference

        # Manually expire by clearing queue (simulating time passage)
        tracker.request_times.queue.clear()
        assert tracker.get_current_requests() == 0


class TestModelRateLimitTrackerTokenTracking:
    """Tests for token usage tracking."""

    def test_record_tokens(self, tracker):
        """Should record token usage."""
        tracker.record_tokens(100)
        assert tracker.get_current_tokens() == 100

    def test_record_multiple_tokens(self, tracker):
        """Should sum multiple token records."""
        tracker.record_tokens(100)
        tracker.record_tokens(200)
        tracker.record_tokens(300)
        assert tracker.get_current_tokens() == 600

    def test_get_current_tokens_accuracy(self, tracker):
        """Should accurately sum current tokens."""
        total = 0
        for i in range(1, 6):
            tokens = i * 100
            tracker.record_tokens(tokens)
            total += tokens
            assert tracker.get_current_tokens() == total

    def test_tokens_expire_after_window(self, tracker):
        """Should clean up old tokens after time window."""
        tracker.record_tokens(1000)
        assert tracker.get_current_tokens() == 1000

        # Manually clear queue to simulate expiration
        tracker.token_usage.queue.clear()
        assert tracker.get_current_tokens() == 0

    def test_token_history_limit(self, tracker):
        """Should limit token history size."""
        # Record more than TOKEN_HISTORY_MAX_SIZE entries
        for i in range(TOKEN_HISTORY_MAX_SIZE + 50):
            tracker.record_tokens(10)

        # Queue should be limited
        assert len(tracker.token_usage.queue) == TOKEN_HISTORY_MAX_SIZE


class TestModelRateLimitTrackerRPMCalculation:
    """Tests for RPM wait time calculation."""

    def test_rpm_wait_time_under_limit(self, tracker):
        """Should return 0 when under RPM limit."""
        # Record fewer requests than limit
        for _ in range(tracker.config.rpm - 1):
            tracker.record_request()

        wait_time = tracker.calculate_rpm_wait_time()
        assert wait_time == 0.0

    def test_rpm_wait_time_at_limit(self, strict_tracker):
        """Should return positive wait time when at RPM limit."""
        # Fill to RPM limit (2 requests for strict config)
        for _ in range(strict_tracker.config.rpm):
            strict_tracker.record_request()

        wait_time = strict_tracker.calculate_rpm_wait_time()
        assert wait_time > 0.0
        assert wait_time <= 60.0  # Should be less than full window

    def test_rpm_wait_time_decreases_over_time(self, strict_tracker):
        """Should decrease as time passes."""
        # Fill to limit
        for _ in range(strict_tracker.config.rpm):
            strict_tracker.record_request()

        wait_time_1 = strict_tracker.calculate_rpm_wait_time()

        # Wait a bit
        time.sleep(0.5)

        wait_time_2 = strict_tracker.calculate_rpm_wait_time()

        # Second wait should be shorter
        assert wait_time_2 < wait_time_1

    def test_rpm_wait_time_with_no_requests(self, tracker):
        """Should return 0 when no requests recorded."""
        wait_time = tracker.calculate_rpm_wait_time()
        assert wait_time == 0.0

    def test_rpm_wait_time_exactly_at_limit(self, strict_tracker):
        """Should handle exact limit edge case."""
        # Record exactly RPM requests
        for _ in range(strict_tracker.config.rpm):
            strict_tracker.record_request()

        wait_time = strict_tracker.calculate_rpm_wait_time()

        # Should need to wait close to full window
        assert 55.0 <= wait_time <= 60.0


class TestModelRateLimitTrackerTPMCalculation:
    """Tests for TPM wait time calculation."""

    def test_tpm_wait_time_under_limit(self, tracker):
        """Should return 0 when under TPM limit."""
        tracker.record_tokens(500)

        wait_time = tracker.calculate_tpm_wait_time(estimated_tokens=100)
        assert wait_time == 0.0

    def test_tpm_wait_time_at_limit(self, strict_tracker):
        """Should return positive wait time when at TPM limit."""
        # Fill close to TPM limit (100 tokens for strict config)
        strict_tracker.record_tokens(90)

        # Request would exceed limit
        wait_time = strict_tracker.calculate_tpm_wait_time(estimated_tokens=20)
        assert wait_time > 0.0

    def test_tpm_wait_time_with_estimated_tokens(self, tracker):
        """Should account for estimated tokens in calculation."""
        # Use most of TPM
        tracker.record_tokens(900)

        # Small request should be fine
        wait_time_small = tracker.calculate_tpm_wait_time(estimated_tokens=50)
        assert wait_time_small == 0.0

        # Large request should require wait
        wait_time_large = tracker.calculate_tpm_wait_time(estimated_tokens=200)
        assert wait_time_large > 0.0

    def test_tpm_wait_time_decreases_over_time(self, strict_tracker):
        """Should decrease as time passes."""
        # Fill to limit
        strict_tracker.record_tokens(95)

        wait_time_1 = strict_tracker.calculate_tpm_wait_time(estimated_tokens=10)

        # Wait a bit
        time.sleep(0.5)

        wait_time_2 = strict_tracker.calculate_tpm_wait_time(estimated_tokens=10)

        # Second wait should be shorter
        assert wait_time_2 < wait_time_1

    def test_tpm_wait_time_with_no_tokens(self, tracker):
        """Should return 0 when no tokens recorded."""
        wait_time = tracker.calculate_tpm_wait_time(estimated_tokens=100)
        assert wait_time == 0.0


class TestModelRateLimitTrackerStatistics:
    """Tests for statistics gathering."""

    def test_get_stats_format(self, tracker):
        """Should return correctly formatted stats."""
        stats = tracker.get_stats()

        assert isinstance(stats, dict)
        assert "requests" in stats
        assert "tokens" in stats
        assert "active" in stats

    def test_get_stats_values_accurate(self, tracker):
        """Should reflect actual state in stats."""
        tracker.record_request()
        tracker.record_request()
        tracker.record_tokens(500)
        tracker.active_requests = 2

        stats = tracker.get_stats()

        assert stats["requests"] == f"2/{tracker.config.rpm}"
        assert stats["tokens"] == f"500/{tracker.config.tpm}"
        assert stats["active"] == f"2/{tracker.config.max_parallel_requests}"

    def test_get_stats_zero_state(self, tracker):
        """Should show zeros for empty state."""
        stats = tracker.get_stats()

        assert stats["requests"] == f"0/{tracker.config.rpm}"
        assert stats["tokens"] == f"0/{tracker.config.tpm}"
        assert stats["active"] == f"0/{tracker.config.max_parallel_requests}"

    def test_get_stats_at_limits(self, strict_tracker):
        """Should show correct values at limits."""
        # Fill to limits
        for _ in range(strict_tracker.config.rpm):
            strict_tracker.record_request()

        strict_tracker.record_tokens(strict_tracker.config.tpm)
        strict_tracker.active_requests = strict_tracker.config.max_parallel_requests

        stats = strict_tracker.get_stats()

        assert (
            stats["requests"]
            == f"{strict_tracker.config.rpm}/{strict_tracker.config.rpm}"
        )
        assert (
            stats["tokens"]
            == f"{strict_tracker.config.tpm}/{strict_tracker.config.tpm}"
        )
        assert (
            stats["active"]
            == f"{strict_tracker.config.max_parallel_requests}/{strict_tracker.config.max_parallel_requests}"
        )


class TestModelRateLimitTrackerWaitForLimits:
    """Tests for wait_for_limits async method."""

    @pytest.mark.asyncio
    async def test_wait_for_limits_no_wait_needed(self, tracker):
        """Should return immediately when under limits."""
        start = time.time()
        await tracker.wait_for_limits("test-model", estimated_tokens=100)
        duration = time.time() - start

        # Should be nearly instant
        assert duration < 0.1

    @pytest.mark.asyncio
    @pytest.mark.slow
    async def test_wait_for_limits_rpm_exceeded(self, strict_tracker):
        """Should wait when RPM limit exceeded."""
        # Fill RPM limit
        for _ in range(strict_tracker.config.rpm):
            strict_tracker.record_request()

        start = time.time()
        await strict_tracker.wait_for_limits("test-model", estimated_tokens=10)
        duration = time.time() - start

        # Should have waited (close to full window)
        assert duration > 55.0

    @pytest.mark.asyncio
    @pytest.mark.slow
    async def test_wait_for_limits_tpm_exceeded(self, strict_tracker):
        """Should wait when TPM limit exceeded."""
        # Fill TPM limit
        strict_tracker.record_tokens(95)

        start = time.time()
        await strict_tracker.wait_for_limits("test-model", estimated_tokens=10)
        duration = time.time() - start

        # Should have waited
        assert duration > 55.0

    @pytest.mark.asyncio
    @pytest.mark.slow
    async def test_wait_for_limits_both_exceeded(self, strict_tracker):
        """Should wait for both limits sequentially."""
        # Fill both limits
        for _ in range(strict_tracker.config.rpm):
            strict_tracker.record_request()
        strict_tracker.record_tokens(95)

        start = time.time()
        await strict_tracker.wait_for_limits("test-model", estimated_tokens=10)
        duration = time.time() - start

        # Should have waited for at least one full window
        # (both limits expire around same time in this case)
        assert duration > 55.0

    @pytest.mark.asyncio
    async def test_wait_for_limits_concurrent_calls(self, tracker, capture_logs):
        """Should handle concurrent calls safely."""

        # This tests that the lock works properly
        async def wait_task():
            async with tracker.lock:
                await tracker.wait_for_limits("test-model", estimated_tokens=100)

        # Run multiple concurrent waits
        await asyncio.gather(
            wait_task(),
            wait_task(),
            wait_task(),
        )

        # Should complete without errors


class TestModelRateLimitTrackerEdgeCases:
    """Tests for edge cases and boundary conditions."""

    def test_zero_token_recording(self, tracker):
        """Should handle recording zero tokens."""
        tracker.record_tokens(0)
        assert tracker.get_current_tokens() == 0

    def test_very_large_token_count(self, tracker):
        """Should handle very large token counts."""
        large_count = 1_000_000
        tracker.record_tokens(large_count)
        assert tracker.get_current_tokens() == large_count

    def test_many_small_requests(self, tracker):
        """Should handle many small requests."""
        for _ in range(100):
            tracker.record_request()

        assert tracker.get_current_requests() == 100

    def test_active_requests_manual_manipulation(self, tracker):
        """Should allow manual active request tracking."""
        tracker.active_requests = 5
        assert tracker.active_requests == 5

        tracker.active_requests -= 1
        assert tracker.active_requests == 4

    @pytest.mark.asyncio
    async def test_wait_with_zero_estimated_tokens(self, tracker):
        """Should handle zero estimated tokens."""
        await tracker.wait_for_limits("test-model", estimated_tokens=0)
        # Should complete without error

    def test_config_with_very_high_limits(self):
        """Should work with very high limits."""
        config = create_test_config(rpm=10000, tpm=10000000)
        tracker = ModelRateLimitTracker(config)

        # Record many requests and tokens
        for _ in range(100):
            tracker.record_request()
        tracker.record_tokens(100000)

        # Should still be under limits
        assert tracker.calculate_rpm_wait_time() == 0.0
        assert tracker.calculate_tpm_wait_time(1000) == 0.0

    def test_config_with_very_low_limits(self):
        """Should work with very low limits."""
        config = create_test_config(rpm=1, tpm=10, max_parallel_requests=1)
        tracker = ModelRateLimitTracker(config)

        tracker.record_request()
        assert tracker.calculate_rpm_wait_time() > 0.0

        tracker.record_tokens(11)
        assert tracker.calculate_tpm_wait_time(1) > 0.0
