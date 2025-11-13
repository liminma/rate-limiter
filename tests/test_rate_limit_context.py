"""
Unit tests for RateLimitContext class.
"""

import asyncio
import contextlib
import time

import pytest

from rate_limiter.model_rate_limiter import (
    DEFAULT_ESTIMATED_TOKENS,
    ModelRateLimiter,
    RateLimitContext,
)


class TestRateLimitContextBasic:
    """Tests for basic context manager functionality."""

    @pytest.mark.asyncio
    async def test_context_manager_enters(self, model_rate_limiter):
        """Should successfully enter context."""
        async with RateLimitContext(
            model_rate_limiter, "test-model", 1000, 1000
        ) as ctx:
            assert ctx is not None
            assert isinstance(ctx, RateLimitContext)

    @pytest.mark.asyncio
    async def test_context_manager_exits(self, model_rate_limiter):
        """Should successfully exit context."""
        entered = False
        exited = False

        try:
            async with RateLimitContext(model_rate_limiter, "test-model", 1000, 1000):
                entered = True
            exited = True
        except Exception:
            pass

        assert entered
        assert exited

    @pytest.mark.asyncio
    async def test_context_manager_calls_acquire(self, model_rate_limiter):
        """Should call acquire on enter."""
        async with RateLimitContext(model_rate_limiter, "test-model", 1000, 1000):
            tracker = model_rate_limiter._get_tracker("test-model")
            assert tracker.active_requests == 1
            assert tracker.get_current_requests() == 1

    @pytest.mark.asyncio
    async def test_context_manager_calls_release(self, model_rate_limiter):
        """Should call release on exit."""
        async with RateLimitContext(model_rate_limiter, "test-model", 1000, 900):
            pass

        # Give async release time to complete
        await asyncio.sleep(0.1)

        tracker = model_rate_limiter._get_tracker("test-model")
        assert tracker.active_requests == 0
        assert tracker.get_current_tokens() == 900


class TestRateLimitContextParameters:
    """Tests for parameter passing."""

    @pytest.mark.asyncio
    async def test_passes_estimated_tokens_to_acquire(self, model_rate_limiter):
        """Should pass estimated_tokens to acquire."""
        async with RateLimitContext(
            model_rate_limiter, "test-model", estimated_tokens=5000, actual_tokens=4500
        ):
            # Should use estimated_tokens for rate limit checking
            # Verified by not blocking/waiting
            pass

    @pytest.mark.asyncio
    async def test_passes_actual_tokens_to_release(self, model_rate_limiter):
        """Should pass actual_tokens to release."""
        async with RateLimitContext(
            model_rate_limiter, "test-model", estimated_tokens=5000, actual_tokens=4321
        ):
            pass

        await asyncio.sleep(0.1)

        tracker = model_rate_limiter._get_tracker("test-model")
        assert tracker.get_current_tokens() == 4321

    @pytest.mark.asyncio
    async def test_uses_default_tokens_when_not_specified(self, model_rate_limiter):
        """Should use DEFAULT_ESTIMATED_TOKENS when not specified."""
        async with RateLimitContext(model_rate_limiter, "test-model"):
            pass

        await asyncio.sleep(0.1)

        tracker = model_rate_limiter._get_tracker("test-model")
        assert tracker.get_current_tokens() == DEFAULT_ESTIMATED_TOKENS

    @pytest.mark.asyncio
    async def test_model_name_parameter(self, model_rate_limiter):
        """Should use specified model name."""
        async with RateLimitContext(model_rate_limiter, "specific-model", 1000, 1000):
            pass

        await asyncio.sleep(0.1)

        # Should have created tracker for specific model
        assert "specific-model" in model_rate_limiter.trackers


class TestRateLimitContextExceptionHandling:
    """Tests for exception handling."""

    @pytest.mark.asyncio
    async def test_releases_on_exception(self, model_rate_limiter):
        """Should call release even when exception occurs."""
        try:
            async with RateLimitContext(model_rate_limiter, "test-model", 1000, 800):
                raise ValueError("Test exception")
        except ValueError:
            pass

        await asyncio.sleep(0.1)

        tracker = model_rate_limiter._get_tracker("test-model")
        assert tracker.active_requests == 0
        assert tracker.get_current_tokens() == 800

    @pytest.mark.asyncio
    async def test_propagates_exception(self, model_rate_limiter):
        """Should propagate exceptions, not suppress them."""
        with pytest.raises(ValueError, match="Test exception"):
            async with RateLimitContext(model_rate_limiter, "test-model", 1000, 1000):
                raise ValueError("Test exception")

    @pytest.mark.asyncio
    async def test_releases_on_cancellation(self, model_rate_limiter):
        """Should handle asyncio.CancelledError properly."""

        async def cancel_task():
            async with RateLimitContext(model_rate_limiter, "test-model", 1000, 750):
                await asyncio.sleep(10)  # Long sleep

        task = asyncio.create_task(cancel_task())
        await asyncio.sleep(0.1)

        # Cancel the task
        task.cancel()

        with contextlib.suppress(asyncio.CancelledError):
            await task

        await asyncio.sleep(0.1)

        # Should have cleaned up
        tracker = model_rate_limiter._get_tracker("test-model")
        assert tracker.active_requests == 0

    @pytest.mark.asyncio
    @pytest.mark.slow
    async def test_multiple_exceptions_in_context(self, model_rate_limiter):
        """Should handle context with multiple exception types."""
        exceptions_to_test = [
            ValueError("Value error"),
            RuntimeError("Runtime error"),
            TypeError("Type error"),
            KeyError("Key error"),
        ]

        for exc in exceptions_to_test:
            try:
                async with RateLimitContext(
                    model_rate_limiter, "test-model", 1000, 900
                ):
                    raise exc
            except Exception:
                pass

            await asyncio.sleep(0.1)

            tracker = model_rate_limiter._get_tracker("test-model")
            assert tracker.active_requests == 0


class TestRateLimitContextIntegration:
    """Tests for integration scenarios."""

    @pytest.mark.asyncio
    async def test_full_lifecycle(self, model_rate_limiter):
        """Should handle complete request lifecycle."""
        # Before context
        tracker = model_rate_limiter._get_tracker("test-model")
        assert tracker.active_requests == 0
        assert tracker.get_current_requests() == 0
        assert tracker.get_current_tokens() == 0

        # During context
        async with RateLimitContext(model_rate_limiter, "test-model", 2000, 1800):
            assert tracker.active_requests == 1
            assert tracker.get_current_requests() == 1

        await asyncio.sleep(0.1)

        # After context
        assert tracker.active_requests == 0
        assert tracker.get_current_requests() == 1
        assert tracker.get_current_tokens() == 1800

    @pytest.mark.asyncio
    async def test_nested_context_managers_same_model(self):
        """Should handle nested contexts for same model."""
        limiter = ModelRateLimiter(
            default_config={"rpm": 100, "tpm": 10000, "max_parallel_requests": 5}
        )

        async with RateLimitContext(limiter, "test-model", 1000, 900):
            tracker = limiter._get_tracker("test-model")
            assert tracker.active_requests == 1

            async with RateLimitContext(limiter, "test-model", 1000, 850):
                assert tracker.active_requests == 2

            await asyncio.sleep(0.1)
            assert tracker.active_requests == 1

        await asyncio.sleep(0.1)
        assert tracker.active_requests == 0
        assert tracker.get_current_tokens() == 1750  # 900 + 850

    @pytest.mark.asyncio
    async def test_context_managers_different_models(self, multi_model_limiter):
        """Should handle contexts for different models."""
        async with (
            RateLimitContext(multi_model_limiter, "gpt-4", 1000, 900),
            RateLimitContext(multi_model_limiter, "gpt-3.5", 2000, 1800),
            RateLimitContext(multi_model_limiter, "claude-3", 1500, 1400),
        ):
            # All three should be active
            assert multi_model_limiter._get_tracker("gpt-4").active_requests == 1
            assert multi_model_limiter._get_tracker("gpt-3.5").active_requests == 1
            assert multi_model_limiter._get_tracker("claude-3").active_requests == 1

    @pytest.mark.asyncio
    async def test_sequential_contexts(self):
        """Should handle sequential context usage."""
        # Use fresh limiter with high limits to avoid rate limiting
        limiter = ModelRateLimiter(
            default_config={"rpm": 100, "tpm": 100000, "max_parallel_requests": 10}
        )

        for _ in range(5):
            async with RateLimitContext(limiter, "test-model", 1000, 900):
                tracker = limiter._get_tracker("test-model")
                assert tracker.active_requests == 1

            await asyncio.sleep(0.15)

        # Additional wait to ensure all async operations complete
        await asyncio.sleep(0.2)

        tracker = limiter._get_tracker("test-model")
        assert tracker.active_requests == 0
        assert tracker.get_current_requests() == 5
        assert tracker.get_current_tokens() == 4500

    @pytest.mark.asyncio
    async def test_concurrent_contexts_same_model(self):
        """Should handle concurrent contexts for same model."""
        limiter = ModelRateLimiter(
            default_config={"rpm": 100, "tpm": 10000, "max_parallel_requests": 10}
        )

        async def use_context(delay=0.0):
            await asyncio.sleep(delay)
            async with RateLimitContext(limiter, "test-model", 500, 450):
                await asyncio.sleep(0.2)

        # Run multiple contexts concurrently
        await asyncio.gather(
            use_context(0),
            use_context(0.05),
            use_context(0.1),
        )

        await asyncio.sleep(0.1)

        tracker = limiter._get_tracker("test-model")
        assert tracker.active_requests == 0
        assert tracker.get_current_requests() == 3
        assert tracker.get_current_tokens() == 1350


class TestRateLimitContextWithRateLimits:
    """Tests for context manager respecting rate limits."""

    @pytest.mark.asyncio
    async def test_context_respects_parallel_limit(self):
        """Should block when parallel limit reached."""
        limiter = ModelRateLimiter(
            default_config={"rpm": 100, "tpm": 10000, "max_parallel_requests": 2}
        )

        async def long_context():
            async with RateLimitContext(limiter, "test-model", 500, 450):
                await asyncio.sleep(0.5)

        # Start two contexts (fills parallel limit)
        task1 = asyncio.create_task(long_context())
        task2 = asyncio.create_task(long_context())
        await asyncio.sleep(0.1)

        # Third should wait
        start = time.time()
        task3 = asyncio.create_task(long_context())
        await asyncio.sleep(0.1)

        # Third should not have completed yet
        assert not task3.done()

        # Wait for all to complete
        await asyncio.gather(task1, task2, task3)
        duration = time.time() - start

        # Should have taken longer than single execution
        assert duration > 0.5

    @pytest.mark.asyncio
    async def test_context_with_different_token_estimates(self):
        """Should handle varying token estimates."""
        # Use fresh limiter with high limits to avoid rate limiting
        limiter = ModelRateLimiter(
            default_config={"rpm": 100, "tpm": 100000, "max_parallel_requests": 10}
        )

        async with RateLimitContext(
            limiter, "test-model", estimated_tokens=1000, actual_tokens=950
        ):
            pass

        # Wait for async release to complete
        await asyncio.sleep(0.2)

        async with RateLimitContext(
            limiter, "test-model", estimated_tokens=5000, actual_tokens=4800
        ):
            pass

        # Wait for async release to complete
        await asyncio.sleep(0.2)

        tracker = limiter._get_tracker("test-model")
        assert tracker.get_current_tokens() == 5750  # 950 + 4800


class TestRateLimitContextEdgeCases:
    """Tests for edge cases."""

    @pytest.mark.asyncio
    async def test_zero_estimated_tokens(self, model_rate_limiter):
        """Should handle zero estimated tokens."""
        async with RateLimitContext(
            model_rate_limiter, "test-model", estimated_tokens=0, actual_tokens=0
        ):
            pass

        await asyncio.sleep(0.1)

        tracker = model_rate_limiter._get_tracker("test-model")
        assert tracker.get_current_tokens() == 0

    @pytest.mark.asyncio
    async def test_very_large_tokens(self, model_rate_limiter):
        """Should handle very large token counts."""
        async with RateLimitContext(
            model_rate_limiter,
            "test-model",
            estimated_tokens=1_000_000,
            actual_tokens=999_999,
        ):
            pass

        await asyncio.sleep(0.1)

        tracker = model_rate_limiter._get_tracker("test-model")
        assert tracker.get_current_tokens() == 999_999

    @pytest.mark.asyncio
    async def test_mismatched_estimated_and_actual(self, model_rate_limiter):
        """Should handle large difference between estimated and actual."""
        async with RateLimitContext(
            model_rate_limiter, "test-model", estimated_tokens=10000, actual_tokens=500
        ):
            pass

        await asyncio.sleep(0.1)

        tracker = model_rate_limiter._get_tracker("test-model")
        # Should record actual, not estimated
        assert tracker.get_current_tokens() == 500

    @pytest.mark.asyncio
    async def test_empty_model_name(self, model_rate_limiter):
        """Should handle empty model name."""
        async with RateLimitContext(
            model_rate_limiter, "", estimated_tokens=1000, actual_tokens=900
        ):
            pass

        await asyncio.sleep(0.1)

        # Should create tracker with empty name
        assert "" in model_rate_limiter.trackers

    @pytest.mark.asyncio
    async def test_very_quick_context(self, model_rate_limiter):
        """Should handle very quick entry/exit."""
        start = time.time()
        async with RateLimitContext(model_rate_limiter, "test-model"):
            pass
        duration = time.time() - start

        # Should be very fast
        assert duration < 0.1

        await asyncio.sleep(0.1)

        tracker = model_rate_limiter._get_tracker("test-model")
        assert tracker.active_requests == 0

    @pytest.mark.asyncio
    async def test_context_with_await_inside(self, model_rate_limiter):
        """Should work with async operations inside context."""

        async def async_operation():
            await asyncio.sleep(0.1)
            return "result"

        async with RateLimitContext(model_rate_limiter, "test-model"):
            result = await async_operation()

        assert result == "result"

        await asyncio.sleep(0.1)

        tracker = model_rate_limiter._get_tracker("test-model")
        assert tracker.active_requests == 0


class TestRateLimitContextAttributes:
    """Tests for context object attributes."""

    @pytest.mark.asyncio
    async def test_context_has_correct_attributes(self, model_rate_limiter):
        """Should have all expected attributes."""
        ctx = RateLimitContext(model_rate_limiter, "test-model", 1000, 900)

        assert hasattr(ctx, "rate_limiter")
        assert hasattr(ctx, "model_name")
        assert hasattr(ctx, "estimated_tokens")
        assert hasattr(ctx, "actual_tokens")

        assert ctx.rate_limiter is model_rate_limiter
        assert ctx.model_name == "test-model"
        assert ctx.estimated_tokens == 1000
        assert ctx.actual_tokens == 900

    @pytest.mark.asyncio
    async def test_context_return_value(self, model_rate_limiter):
        """Should return self from __aenter__."""
        async with RateLimitContext(model_rate_limiter, "test-model") as ctx:
            assert isinstance(ctx, RateLimitContext)
            assert ctx.model_name == "test-model"
