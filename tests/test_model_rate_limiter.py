"""
Unit tests for ModelRateLimiter class.
"""

import asyncio
import time

import pytest

from rate_limiter.model_rate_limiter import (
    DEFAULT_ESTIMATED_TOKENS,
    ModelRateLimiter,
    RateLimitConfig,
)


class TestModelRateLimiterInitialization:
    """Tests for ModelRateLimiter initialization."""

    def test_initialization_no_config(self):
        """Should initialize with default config when none provided."""
        limiter = ModelRateLimiter()
        assert limiter.default_config is not None
        assert isinstance(limiter.default_config, RateLimitConfig)
        assert len(limiter.trackers) == 0

    def test_initialization_with_model_configs(self):
        """Should parse model configs correctly."""
        limiter = ModelRateLimiter(
            model_configs={
                "gpt-4": {"rpm": 500, "tpm": 40000, "max_parallel_requests": 10},
                "gpt-3.5": {"rpm": 3500, "tpm": 90000, "max_parallel_requests": 20},
            }
        )

        assert len(limiter.model_configs) == 2
        assert "gpt-4" in limiter.model_configs
        assert "gpt-3.5" in limiter.model_configs
        assert limiter.model_configs["gpt-4"].rpm == 500
        assert limiter.model_configs["gpt-3.5"].rpm == 3500

    def test_initialization_with_default_config(self):
        """Should use provided default config."""
        limiter = ModelRateLimiter(
            default_config={"rpm": 100, "tpm": 10000, "max_parallel_requests": 5}
        )

        assert limiter.default_config.rpm == 100
        assert limiter.default_config.tpm == 10000
        assert limiter.default_config.max_parallel_requests == 5

    def test_initialization_creates_rate_limit_configs(self):
        """Should convert dicts to RateLimitConfig objects."""
        limiter = ModelRateLimiter(model_configs={"model-1": {"rpm": 10}})

        assert isinstance(limiter.model_configs["model-1"], RateLimitConfig)


class TestModelRateLimiterTrackerManagement:
    """Tests for tracker creation and management."""

    def test_get_tracker_creates_new(self, model_rate_limiter):
        """Should create new tracker on first access."""
        assert len(model_rate_limiter.trackers) == 0

        tracker = model_rate_limiter._get_tracker("test-model")

        assert tracker is not None
        assert len(model_rate_limiter.trackers) == 1
        assert "test-model" in model_rate_limiter.trackers

    def test_get_tracker_reuses_existing(self, model_rate_limiter):
        """Should reuse existing tracker."""
        tracker1 = model_rate_limiter._get_tracker("test-model")
        tracker2 = model_rate_limiter._get_tracker("test-model")

        assert tracker1 is tracker2
        assert len(model_rate_limiter.trackers) == 1

    def test_get_tracker_uses_model_config(self, multi_model_limiter):
        """Should use model-specific config when available."""
        tracker = multi_model_limiter._get_tracker("gpt-4")

        assert tracker.config.rpm == 500
        assert tracker.config.tpm == 40000
        assert tracker.config.max_parallel_requests == 10

    def test_get_tracker_uses_default_config(self, multi_model_limiter):
        """Should use default config for unconfigured models."""
        tracker = multi_model_limiter._get_tracker("unknown-model")

        assert tracker.config.rpm == 10
        assert tracker.config.tpm == 1000
        assert tracker.config.max_parallel_requests == 3

    def test_multiple_models_separate_trackers(self, model_rate_limiter):
        """Should create separate tracker for each model."""
        tracker1 = model_rate_limiter._get_tracker("model-1")
        tracker2 = model_rate_limiter._get_tracker("model-2")
        tracker3 = model_rate_limiter._get_tracker("model-3")

        assert tracker1 is not tracker2
        assert tracker2 is not tracker3
        assert len(model_rate_limiter.trackers) == 3


class TestModelRateLimiterAcquire:
    """Tests for acquire method."""

    @pytest.mark.asyncio
    async def test_acquire_succeeds_when_under_limits(self, model_rate_limiter):
        """Should complete immediately when under all limits."""
        start = time.time()
        await model_rate_limiter.acquire("test-model", estimated_tokens=100)
        duration = time.time() - start

        assert duration < 0.1

    @pytest.mark.asyncio
    async def test_acquire_increments_active_requests(self, model_rate_limiter):
        """Should increment active request count."""
        await model_rate_limiter.acquire("test-model")

        tracker = model_rate_limiter._get_tracker("test-model")
        assert tracker.active_requests == 1

    @pytest.mark.asyncio
    async def test_acquire_records_request(self, model_rate_limiter):
        """Should record the request."""
        await model_rate_limiter.acquire("test-model")

        tracker = model_rate_limiter._get_tracker("test-model")
        assert tracker.get_current_requests() == 1

    @pytest.mark.asyncio
    async def test_acquire_with_custom_estimated_tokens(self, model_rate_limiter):
        """Should use custom estimated tokens."""
        await model_rate_limiter.acquire("test-model", estimated_tokens=8000)
        # Should complete without error (verified by not raising)

    @pytest.mark.asyncio
    async def test_acquire_uses_default_tokens_when_not_specified(
        self, model_rate_limiter
    ):
        """Should use DEFAULT_ESTIMATED_TOKENS when not specified."""
        await model_rate_limiter.acquire("test-model")
        # Implicitly uses DEFAULT_ESTIMATED_TOKENS
        # Verified by not raising and completing successfully

    @pytest.mark.asyncio
    async def test_acquire_acquires_semaphore(self, model_rate_limiter):
        """Should acquire semaphore slot."""
        await model_rate_limiter.acquire("test-model")

        tracker = model_rate_limiter._get_tracker("test-model")
        # Semaphore should have one less slot available
        assert tracker.semaphore._value == tracker.config.max_parallel_requests - 1

    @pytest.mark.asyncio
    async def test_acquire_waits_for_semaphore(self):
        """Should block when at max parallel requests."""
        limiter = ModelRateLimiter(
            default_config={"rpm": 100, "tpm": 10000, "max_parallel_requests": 2}
        )

        # Acquire max parallel slots
        await limiter.acquire("test-model")
        await limiter.acquire("test-model")

        # Third acquire should need to wait for release
        # We'll test this doesn't hang indefinitely
        async def third_acquire():
            await limiter.acquire("test-model")

        # Start the third acquire (it will block)
        task = asyncio.create_task(third_acquire())

        # Give it a moment to block
        await asyncio.sleep(0.1)
        assert not task.done()

        # Release one slot
        await limiter.release("test-model")

        # Now third should complete
        await asyncio.wait_for(task, timeout=1.0)
        assert task.done()

    @pytest.mark.asyncio
    async def test_acquire_multiple_models_independent(self, multi_model_limiter):
        """Should handle multiple models independently."""
        await multi_model_limiter.acquire("gpt-4")
        await multi_model_limiter.acquire("gpt-3.5")
        await multi_model_limiter.acquire("claude-3")

        # Each model should have one active request
        assert multi_model_limiter._get_tracker("gpt-4").active_requests == 1
        assert multi_model_limiter._get_tracker("gpt-3.5").active_requests == 1
        assert multi_model_limiter._get_tracker("claude-3").active_requests == 1


class TestModelRateLimiterRelease:
    """Tests for release method."""

    @pytest.mark.asyncio
    async def test_release_decrements_active_requests(self, model_rate_limiter):
        """Should decrement active request count."""
        await model_rate_limiter.acquire("test-model")
        assert model_rate_limiter._get_tracker("test-model").active_requests == 1

        await model_rate_limiter.release("test-model")
        assert model_rate_limiter._get_tracker("test-model").active_requests == 0

    @pytest.mark.asyncio
    async def test_release_releases_semaphore(self, model_rate_limiter):
        """Should release semaphore slot."""
        tracker = model_rate_limiter._get_tracker("test-model")
        initial_value = tracker.semaphore._value

        await model_rate_limiter.acquire("test-model")
        assert tracker.semaphore._value == initial_value - 1

        await model_rate_limiter.release("test-model")
        # Give async release time to complete
        await asyncio.sleep(0.1)
        assert tracker.semaphore._value == initial_value

    @pytest.mark.asyncio
    async def test_release_records_tokens(self, model_rate_limiter):
        """Should record token usage."""
        await model_rate_limiter.acquire("test-model")
        await model_rate_limiter.release("test-model", token_count=500)

        # Give async release time to complete
        await asyncio.sleep(0.1)

        tracker = model_rate_limiter._get_tracker("test-model")
        assert tracker.get_current_tokens() == 500

    @pytest.mark.asyncio
    async def test_release_with_custom_token_count(self, model_rate_limiter):
        """Should use custom token count."""
        await model_rate_limiter.acquire("test-model")
        await model_rate_limiter.release("test-model", token_count=12345)

        await asyncio.sleep(0.1)

        tracker = model_rate_limiter._get_tracker("test-model")
        assert tracker.get_current_tokens() == 12345

    @pytest.mark.asyncio
    async def test_release_is_async(self, model_rate_limiter):
        """Should be properly awaitable."""
        await model_rate_limiter.acquire("test-model")

        # Should be awaitable
        result = model_rate_limiter.release("test-model")
        assert asyncio.iscoroutine(result)
        await result


class TestModelRateLimiterAcquireReleasePairing:
    """Tests for acquire/release lifecycle."""

    @pytest.mark.asyncio
    async def test_acquire_then_release_full_cycle(self, model_rate_limiter):
        """Should handle complete acquire/release cycle."""
        # Acquire
        await model_rate_limiter.acquire("test-model", estimated_tokens=1000)
        tracker = model_rate_limiter._get_tracker("test-model")
        assert tracker.active_requests == 1
        assert tracker.get_current_requests() == 1

        # Release
        await model_rate_limiter.release("test-model", token_count=950)
        await asyncio.sleep(0.1)

        assert tracker.active_requests == 0
        assert tracker.get_current_tokens() == 950

    @pytest.mark.asyncio
    async def test_multiple_acquire_release_cycles(self):
        """Should handle multiple cycles."""
        # Use a fresh limiter with high limits to avoid any rate limiting
        limiter = ModelRateLimiter(
            default_config={"rpm": 100, "tpm": 100000, "max_parallel_requests": 10}
        )

        for _ in range(5):
            await limiter.acquire("test-model")
            await limiter.release("test-model", token_count=100)
            await asyncio.sleep(0.15)

        # Additional wait to ensure all async operations complete
        await asyncio.sleep(0.2)

        tracker = limiter._get_tracker("test-model")
        assert tracker.active_requests == 0
        assert tracker.get_current_requests() == 5
        assert tracker.get_current_tokens() == 500

    @pytest.mark.asyncio
    async def test_parallel_requests_respect_max(self):
        """Should enforce max parallel requests limit."""
        limiter = ModelRateLimiter(
            default_config={"rpm": 100, "tpm": 10000, "max_parallel_requests": 2}
        )

        # Acquire two slots
        await limiter.acquire("test-model")
        await limiter.acquire("test-model")

        tracker = limiter._get_tracker("test-model")
        assert tracker.active_requests == 2

        # Third acquire should block
        async def try_third():
            await limiter.acquire("test-model")
            return "completed"

        task = asyncio.create_task(try_third())
        await asyncio.sleep(0.1)
        assert not task.done()

        # Release one
        await limiter.release("test-model")
        await asyncio.sleep(0.1)

        # Third should now complete
        result = await asyncio.wait_for(task, timeout=1.0)
        assert result == "completed"

    @pytest.mark.asyncio
    async def test_release_allows_next_acquire(self):
        """Should allow waiting acquire after release."""
        limiter = ModelRateLimiter(
            default_config={"rpm": 100, "tpm": 10000, "max_parallel_requests": 1}
        )

        # Fill the slot
        await limiter.acquire("test-model")

        # Start second acquire (will wait)
        async def second_acquire():
            start = time.time()
            await limiter.acquire("test-model")
            return time.time() - start

        task = asyncio.create_task(second_acquire())
        await asyncio.sleep(0.1)

        # Release first
        await limiter.release("test-model")

        # Second should complete quickly
        duration = await asyncio.wait_for(task, timeout=1.0)
        assert duration < 1.0


class TestModelRateLimiterMultiModel:
    """Tests for multiple model handling."""

    @pytest.mark.asyncio
    async def test_different_models_independent_limits(self, multi_model_limiter):
        """Should maintain independent limits per model."""
        # Use different models
        await multi_model_limiter.acquire("gpt-4")
        await multi_model_limiter.acquire("gpt-3.5")

        tracker_4 = multi_model_limiter._get_tracker("gpt-4")
        tracker_35 = multi_model_limiter._get_tracker("gpt-3.5")

        assert tracker_4.active_requests == 1
        assert tracker_35.active_requests == 1
        assert tracker_4.get_current_requests() == 1
        assert tracker_35.get_current_requests() == 1

    @pytest.mark.asyncio
    async def test_different_configs_per_model(self, multi_model_limiter):
        """Should use different configs per model."""
        tracker_4 = multi_model_limiter._get_tracker("gpt-4")
        tracker_35 = multi_model_limiter._get_tracker("gpt-3.5")

        assert tracker_4.config.rpm == 500
        assert tracker_35.config.rpm == 3500
        assert tracker_4.config.rpm != tracker_35.config.rpm

    @pytest.mark.asyncio
    async def test_model_a_blocked_model_b_proceeds(self):
        """Should allow model B to proceed when model A is blocked."""
        limiter = ModelRateLimiter(
            model_configs={
                "model-a": {"rpm": 1, "tpm": 100, "max_parallel_requests": 1},
                "model-b": {"rpm": 100, "tpm": 10000, "max_parallel_requests": 10},
            }
        )

        # Block model-a
        await limiter.acquire("model-a")

        # model-b should still work
        start = time.time()
        await limiter.acquire("model-b")
        duration = time.time() - start

        assert duration < 0.1  # Should be immediate


class TestModelRateLimiterStatistics:
    """Tests for statistics gathering."""

    def test_get_stats_all_models(self, multi_model_limiter):
        """Should return stats for all models."""
        stats = multi_model_limiter.get_stats()

        assert isinstance(stats, dict)
        assert "gpt-4" in stats
        assert "gpt-3.5" in stats
        assert "claude-3" in stats

    def test_get_stats_includes_configured_models(self, multi_model_limiter):
        """Should include even unused models."""
        # Don't use any model
        stats = multi_model_limiter.get_stats()

        # Should still show all configured models
        assert len(stats) >= 3

    def test_get_stats_structure(self, model_rate_limiter):
        """Should return correctly structured stats."""
        stats = model_rate_limiter.get_stats()

        assert isinstance(stats, dict)

        # Use a model to generate stats
        model_rate_limiter._get_tracker("test-model")
        stats = model_rate_limiter.get_stats()

        assert "test-model" in stats
        assert "requests" in stats["test-model"]
        assert "tokens" in stats["test-model"]
        assert "active" in stats["test-model"]

    @pytest.mark.asyncio
    async def test_get_stats_reflects_activity(self, model_rate_limiter):
        """Should reflect current activity."""
        await model_rate_limiter.acquire("test-model")

        stats = model_rate_limiter.get_stats()

        # Should show activity
        assert "1/" in stats["test-model"]["requests"]
        assert "1/" in stats["test-model"]["active"]


class TestModelRateLimiterEdgeCases:
    """Tests for edge cases and boundary conditions."""

    @pytest.mark.asyncio
    async def test_acquire_with_zero_estimated_tokens(self, model_rate_limiter):
        """Should handle zero estimated tokens."""
        await model_rate_limiter.acquire("test-model", estimated_tokens=0)
        # Should complete without error

    @pytest.mark.asyncio
    async def test_acquire_with_very_large_estimated_tokens(self, model_rate_limiter):
        """Should handle very large token estimates."""
        await model_rate_limiter.acquire("test-model", estimated_tokens=1_000_000)
        # Should complete (might wait if exceeds TPM)

    @pytest.mark.asyncio
    async def test_release_without_acquire(self, model_rate_limiter):
        """Should handle release without prior acquire (edge case)."""
        # This is not recommended usage but shouldn't crash
        await model_rate_limiter.release("test-model")
        # Should complete without error

    @pytest.mark.asyncio
    async def test_concurrent_acquires_same_model(self):
        """Should handle concurrent acquires for same model safely."""
        limiter = ModelRateLimiter(
            default_config={"rpm": 100, "tpm": 10000, "max_parallel_requests": 5}
        )

        # Concurrent acquires
        await asyncio.gather(
            limiter.acquire("test-model"),
            limiter.acquire("test-model"),
            limiter.acquire("test-model"),
        )

        tracker = limiter._get_tracker("test-model")
        assert tracker.active_requests == 3
        assert tracker.get_current_requests() == 3

    @pytest.mark.asyncio
    async def test_concurrent_acquires_different_models(self, multi_model_limiter):
        """Should handle concurrent acquires for different models."""
        await asyncio.gather(
            multi_model_limiter.acquire("gpt-4"),
            multi_model_limiter.acquire("gpt-3.5"),
            multi_model_limiter.acquire("claude-3"),
        )

        # Each should have one active
        assert multi_model_limiter._get_tracker("gpt-4").active_requests == 1
        assert multi_model_limiter._get_tracker("gpt-3.5").active_requests == 1
        assert multi_model_limiter._get_tracker("claude-3").active_requests == 1

    @pytest.mark.asyncio
    async def test_model_name_with_special_characters(self, model_rate_limiter):
        """Should handle model names with special characters."""
        model_names = [
            "model-with-dashes",
            "model_with_underscores",
            "model.with.dots",
            "model:with:colons",
        ]

        for name in model_names:
            await model_rate_limiter.acquire(name)
            await model_rate_limiter.release(name)
