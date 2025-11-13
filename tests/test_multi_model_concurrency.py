"""
Tests for multi-model concurrent operations.

This module verifies that multiple models can be rate-limited independently
and concurrently without interfering with each other.
"""

import asyncio
import contextlib
import time
from collections import defaultdict

import pytest

from rate_limiter.model_rate_limiter import (
    ModelRateLimiter,
    RateLimitContext,
)


class TestMultiModelIndependence:
    """Tests for independence of different models."""

    @pytest.mark.asyncio
    async def test_models_have_independent_rpm_limits(self):
        """Should enforce RPM limits independently per model."""
        limiter = ModelRateLimiter(
            model_configs={
                "fast-model": {"rpm": 10, "tpm": 10000, "max_parallel_requests": 5},
                "slow-model": {"rpm": 2, "tpm": 10000, "max_parallel_requests": 5},
            }
        )

        # Fill slow-model's RPM
        for _ in range(2):
            await limiter.acquire("slow-model")
            await limiter.release("slow-model")

        # fast-model should still work immediately
        start = time.time()
        await limiter.acquire("fast-model")
        duration = time.time() - start
        await limiter.release("fast-model")

        assert duration < 0.1  # Should be immediate

    @pytest.mark.asyncio
    async def test_models_have_independent_tpm_limits(self):
        """Should enforce TPM limits independently per model."""
        limiter = ModelRateLimiter(
            model_configs={
                "model-a": {"rpm": 100, "tpm": 500, "max_parallel_requests": 5},
                "model-b": {"rpm": 100, "tpm": 5000, "max_parallel_requests": 5},
            }
        )

        # Fill model-a's TPM
        await limiter.acquire("model-a", estimated_tokens=480)
        await limiter.release("model-a", token_count=480)

        # model-b should still work immediately with large tokens
        start = time.time()
        await limiter.acquire("model-b", estimated_tokens=1000)
        duration = time.time() - start
        await limiter.release("model-b", token_count=1000)

        assert duration < 0.1

    @pytest.mark.asyncio
    async def test_models_have_independent_parallel_limits(self):
        """Should enforce parallel limits independently per model."""
        limiter = ModelRateLimiter(
            model_configs={
                "model-a": {"rpm": 100, "tpm": 10000, "max_parallel_requests": 1},
                "model-b": {"rpm": 100, "tpm": 10000, "max_parallel_requests": 5},
            }
        )

        # Block model-a's parallel slot
        await limiter.acquire("model-a")

        # model-b should still accept multiple requests
        await limiter.acquire("model-b")
        await limiter.acquire("model-b")
        await limiter.acquire("model-b")

        tracker_b = limiter._get_tracker("model-b")
        assert tracker_b.active_requests == 3

        # Cleanup
        await limiter.release("model-a")
        await limiter.release("model-b")
        await limiter.release("model-b")
        await limiter.release("model-b")

    @pytest.mark.asyncio
    async def test_blocking_one_model_does_not_block_others(self):
        """Should not block other models when one is waiting."""
        limiter = ModelRateLimiter(
            model_configs={
                "blocked-model": {"rpm": 1, "tpm": 10000, "max_parallel_requests": 5},
                "free-model": {"rpm": 100, "tpm": 10000, "max_parallel_requests": 5},
            }
        )

        # Block blocked-model
        await limiter.acquire("blocked-model")
        await limiter.release("blocked-model")

        # Start a blocked acquire
        blocked_task = asyncio.create_task(limiter.acquire("blocked-model"))
        await asyncio.sleep(0.1)

        # free-model should still work
        start = time.time()
        await limiter.acquire("free-model")
        duration = time.time() - start
        await limiter.release("free-model")

        assert duration < 0.1

        # Cleanup
        blocked_task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await blocked_task


class TestMultiModelConcurrentOperations:
    """Tests for concurrent operations across multiple models."""

    @pytest.mark.asyncio
    async def test_concurrent_acquires_different_models(self, multi_model_limiter):
        """Should handle concurrent acquires for different models."""

        async def use_model(model_name: str, count: int):
            results = []
            for _ in range(count):
                await multi_model_limiter.acquire(model_name)
                results.append(time.time())
                await multi_model_limiter.release(model_name)
            return results

        # Run concurrently
        start = time.time()
        results = await asyncio.gather(
            use_model("gpt-4", 3),
            use_model("gpt-3.5", 3),
            use_model("claude-3", 3),
        )
        duration = time.time() - start

        # Should complete quickly (all running in parallel)
        assert duration < 5.0

        # Each model should have made 3 requests
        assert len(results[0]) == 3
        assert len(results[1]) == 3
        assert len(results[2]) == 3

    @pytest.mark.asyncio
    async def test_concurrent_contexts_different_models(self, multi_model_limiter):
        """Should handle concurrent context managers for different models."""
        results = []

        async def use_context(model_name: str):
            async with RateLimitContext(multi_model_limiter, model_name, 500, 450):
                results.append((model_name, time.time()))
                await asyncio.sleep(0.1)

        # Run concurrently
        start = time.time()
        await asyncio.gather(
            use_context("gpt-4"),
            use_context("gpt-3.5"),
            use_context("claude-3"),
        )
        duration = time.time() - start

        # Should complete quickly (running in parallel)
        assert duration < 1.0

        # All models should have been used
        models_used = [model for model, _ in results]
        assert "gpt-4" in models_used
        assert "gpt-3.5" in models_used
        assert "claude-3" in models_used

    @pytest.mark.asyncio
    async def test_high_concurrency_across_models(self):
        """Should handle high concurrency across multiple models."""
        limiter = ModelRateLimiter(
            model_configs={
                f"model-{i}": {"rpm": 50, "tpm": 10000, "max_parallel_requests": 10}
                for i in range(5)
            }
        )

        async def make_requests(model_name: str, count: int):
            for _ in range(count):
                async with RateLimitContext(limiter, model_name, 100, 100):
                    await asyncio.sleep(0.01)

        # 5 models × 10 requests each = 50 total requests
        start = time.time()
        await asyncio.gather(*[make_requests(f"model-{i}", 10) for i in range(5)])
        duration = time.time() - start

        # Should complete reasonably quickly
        assert duration < 10.0

        # All models should have correct counts
        for i in range(5):
            tracker = limiter._get_tracker(f"model-{i}")
            assert tracker.get_current_requests() == 10


class TestMultiModelStatistics:
    """Tests for statistics across multiple models."""

    @pytest.mark.asyncio
    async def test_stats_show_all_models(self, multi_model_limiter):
        """Should show stats for all configured models."""
        # Use some models
        await multi_model_limiter.acquire("gpt-4")
        await multi_model_limiter.acquire("gpt-3.5")

        stats = multi_model_limiter.get_stats()

        # Should include all configured models
        assert "gpt-4" in stats
        assert "gpt-3.5" in stats
        assert "claude-3" in stats

    @pytest.mark.asyncio
    async def test_stats_reflect_independent_state(self, multi_model_limiter):
        """Should show independent state for each model."""
        # Use models with different amounts
        await multi_model_limiter.acquire("gpt-4")
        await multi_model_limiter.acquire("gpt-3.5")
        await multi_model_limiter.acquire("gpt-3.5")

        stats = multi_model_limiter.get_stats()

        # gpt-4 should show 1 request
        assert stats["gpt-4"]["requests"].startswith("1/")

        # gpt-3.5 should show 2 requests
        assert stats["gpt-3.5"]["requests"].startswith("2/")

    @pytest.mark.asyncio
    async def test_stats_update_independently(self, multi_model_limiter):
        """Should update stats independently per model."""
        # Initial stats
        stats1 = multi_model_limiter.get_stats()
        gpt4_initial = stats1["gpt-4"]["requests"]

        # Use only gpt-3.5
        await multi_model_limiter.acquire("gpt-3.5")
        await multi_model_limiter.release("gpt-3.5")
        await asyncio.sleep(0.1)

        # gpt-4 stats should be unchanged
        stats2 = multi_model_limiter.get_stats()
        assert stats2["gpt-4"]["requests"] == gpt4_initial


class TestMultiModelResourceSharing:
    """Tests for resource sharing behavior across models."""

    @pytest.mark.asyncio
    async def test_models_do_not_share_semaphores(self):
        """Should have separate semaphores per model."""
        limiter = ModelRateLimiter(
            model_configs={
                "model-a": {"rpm": 100, "tpm": 10000, "max_parallel_requests": 1},
                "model-b": {"rpm": 100, "tpm": 10000, "max_parallel_requests": 1},
            }
        )

        # Fill both semaphores
        await limiter.acquire("model-a")
        await limiter.acquire("model-b")

        # Both should be filled independently
        tracker_a = limiter._get_tracker("model-a")
        tracker_b = limiter._get_tracker("model-b")

        assert tracker_a.active_requests == 1
        assert tracker_b.active_requests == 1

        # Cleanup
        await limiter.release("model-a")
        await limiter.release("model-b")

    @pytest.mark.asyncio
    async def test_models_do_not_share_request_counts(self):
        """Should have separate request counts per model."""
        limiter = ModelRateLimiter(
            default_config={"rpm": 100, "tpm": 100000, "max_parallel_requests": 10}
        )

        # Make requests to different models
        for _ in range(3):
            await limiter.acquire("model-a")
            await limiter.release("model-a")
            await asyncio.sleep(0.05)  # Add delay for async completion

        for _ in range(5):
            await limiter.acquire("model-b")
            await limiter.release("model-b")
            await asyncio.sleep(0.05)  # Add delay for async completion

        # Wait for all async operations to complete
        await asyncio.sleep(0.2)

        # Counts should be independent
        tracker_a = limiter._get_tracker("model-a")
        tracker_b = limiter._get_tracker("model-b")

        assert tracker_a.get_current_requests() == 3
        assert tracker_b.get_current_requests() == 5

    @pytest.mark.asyncio
    async def test_models_do_not_share_token_counts(self):
        """Should have separate token counts per model."""
        limiter = ModelRateLimiter(
            default_config={"rpm": 100, "tpm": 10000, "max_parallel_requests": 5}
        )

        await limiter.acquire("model-a", estimated_tokens=1000)
        await limiter.release("model-a", token_count=1000)
        await asyncio.sleep(0.1)

        await limiter.acquire("model-b", estimated_tokens=2000)
        await limiter.release("model-b", token_count=2000)
        await asyncio.sleep(0.1)

        tracker_a = limiter._get_tracker("model-a")
        tracker_b = limiter._get_tracker("model-b")

        assert tracker_a.get_current_tokens() == 1000
        assert tracker_b.get_current_tokens() == 2000


class TestMultiModelLoadBalancing:
    """Tests for load balancing across models."""

    @pytest.mark.asyncio
    async def test_distributes_load_across_models(self):
        """Should allow distributing load across multiple models."""
        limiter = ModelRateLimiter(
            model_configs={
                # Increased RPM to avoid hitting rate limits during test
                "model-a": {"rpm": 20, "tpm": 10000, "max_parallel_requests": 5},
                "model-b": {"rpm": 20, "tpm": 10000, "max_parallel_requests": 5},
            }
        )

        request_times = defaultdict(list)

        async def use_model(model_name: str):
            await limiter.acquire(model_name)
            request_times[model_name].append(time.time())
            await asyncio.sleep(0.05)  # Reduced from 0.1
            await limiter.release(model_name)

        # Alternate between models
        tasks = []
        for i in range(10):
            model = "model-a" if i % 2 == 0 else "model-b"
            tasks.append(use_model(model))

        start = time.time()
        await asyncio.gather(*tasks)
        duration = time.time() - start

        # Should complete reasonably quickly with proper distribution
        assert duration < 3.0  # Changed from 5.0

        # Both models should have been used
        assert len(request_times["model-a"]) == 5
        assert len(request_times["model-b"]) == 5

    @pytest.mark.asyncio
    async def test_fallback_to_different_model_on_limit(self):
        """Should allow manual fallback when one model is limited."""
        limiter = ModelRateLimiter(
            model_configs={
                "primary": {"rpm": 2, "tpm": 10000, "max_parallel_requests": 5},
                "fallback": {"rpm": 100, "tpm": 10000, "max_parallel_requests": 5},
            }
        )

        # Fill primary's RPM
        await limiter.acquire("primary")
        await limiter.release("primary")
        await limiter.acquire("primary")
        await limiter.release("primary")

        # Switch to fallback (application logic would decide this)
        start = time.time()
        await limiter.acquire("fallback")
        duration = time.time() - start
        await limiter.release("fallback")

        # Fallback should be immediate
        assert duration < 0.1


class TestMultiModelEdgeCases:
    """Tests for edge cases with multiple models."""

    @pytest.mark.asyncio
    async def test_many_models_simultaneously(self):
        """Should handle many models simultaneously."""
        num_models = 20
        limiter = ModelRateLimiter(
            model_configs={
                f"model-{i}": {"rpm": 10, "tpm": 10000, "max_parallel_requests": 3}
                for i in range(num_models)
            }
        )

        async def use_model(model_name: str):
            await limiter.acquire(model_name)
            await asyncio.sleep(0.05)
            await limiter.release(model_name)

        # Use all models concurrently
        await asyncio.gather(*[use_model(f"model-{i}") for i in range(num_models)])

        # All models should have stats
        stats = limiter.get_stats()
        assert len(stats) == num_models

    @pytest.mark.asyncio
    async def test_dynamic_model_creation(self):
        """Should create trackers dynamically for new models."""
        limiter = ModelRateLimiter(
            default_config={"rpm": 10, "tpm": 10000, "max_parallel_requests": 5}
        )

        # Initially no trackers
        assert len(limiter.trackers) == 0

        # Use various models dynamically
        models = ["model-a", "model-b", "model-c", "model-d"]
        for model in models:
            await limiter.acquire(model)
            await limiter.release(model)

        # Should have created trackers for all
        assert len(limiter.trackers) == 4
        for model in models:
            assert model in limiter.trackers

    @pytest.mark.asyncio
    async def test_mixed_configured_and_default_models(self):
        """Should handle mix of configured and default models."""
        limiter = ModelRateLimiter(
            model_configs={
                "special-model": {
                    "rpm": 100,
                    "tpm": 50000,
                    "max_parallel_requests": 10,
                },
            },
            default_config={"rpm": 10, "tpm": 10000, "max_parallel_requests": 3},
        )

        # Use configured model
        await limiter.acquire("special-model", estimated_tokens=1000)
        await limiter.release("special-model", token_count=1000)

        # Use unconfigured model (uses default)
        await limiter.acquire("regular-model", estimated_tokens=500)
        await limiter.release("regular-model", token_count=500)

        # Both should work with appropriate configs
        special_tracker = limiter._get_tracker("special-model")
        regular_tracker = limiter._get_tracker("regular-model")

        assert special_tracker.config.rpm == 100
        assert regular_tracker.config.rpm == 10

    @pytest.mark.asyncio
    async def test_model_name_collision_handling(self):
        """Should handle models with similar names correctly."""
        limiter = ModelRateLimiter(
            model_configs={
                "gpt-4": {"rpm": 100, "tpm": 10000, "max_parallel_requests": 5},
                "gpt-4-turbo": {"rpm": 200, "tpm": 20000, "max_parallel_requests": 10},
            }
        )

        # Use both models
        await limiter.acquire("gpt-4")
        await limiter.acquire("gpt-4-turbo")

        # Should maintain separate trackers
        tracker1 = limiter._get_tracker("gpt-4")
        tracker2 = limiter._get_tracker("gpt-4-turbo")

        assert tracker1 is not tracker2
        assert tracker1.config.rpm == 100
        assert tracker2.config.rpm == 200


class TestMultiModelRaceConditions:
    """Tests for race conditions in multi-model scenarios."""

    @pytest.mark.asyncio
    async def test_concurrent_tracker_creation(self):
        """Should safely create trackers concurrently."""
        limiter = ModelRateLimiter(
            default_config={"rpm": 100, "tpm": 100000, "max_parallel_requests": 15}
        )

        async def create_and_use(model_name: str):
            # Multiple tasks might try to create tracker simultaneously
            await limiter.acquire(model_name)
            await limiter.release(model_name)
            # Removed sleep here - we want all requests recorded quickly

        # Try to create same tracker from multiple coroutines
        await asyncio.gather(*[create_and_use("same-model") for _ in range(10)])

        # Wait for all async release operations to complete
        await asyncio.sleep(0.5)

        # Should have only one tracker
        assert len(limiter.trackers) == 1

        # All 10 requests should be recorded
        requests = limiter._get_tracker("same-model").get_current_requests()
        assert requests == 10, f"Expected 10 requests, got {requests}"

    @pytest.mark.asyncio
    async def test_concurrent_stats_access(self, multi_model_limiter):
        """Should handle concurrent stats access safely."""

        async def use_and_check_stats(model_name: str):
            await multi_model_limiter.acquire(model_name)
            stats = multi_model_limiter.get_stats()
            await multi_model_limiter.release(model_name)
            return stats

        # Concurrent operations with stats access
        results = await asyncio.gather(
            *[use_and_check_stats("gpt-4") for _ in range(5)]
        )

        # All should have succeeded
        assert len(results) == 5
        assert all(isinstance(r, dict) for r in results)

    @pytest.mark.asyncio
    async def test_concurrent_config_access(self):
        """Should handle concurrent access to configs safely."""
        limiter = ModelRateLimiter(
            model_configs={
                f"model-{i}": {
                    "rpm": i * 10,
                    "tpm": i * 1000,
                    "max_parallel_requests": i,
                }
                for i in range(1, 11)
            }
        )

        async def access_model(model_name: str):
            tracker = limiter._get_tracker(model_name)
            return tracker.config.rpm

        # Access different model configs concurrently
        results = await asyncio.gather(
            *[access_model(f"model-{i}") for i in range(1, 11)]
        )

        # Should get correct configs
        assert results == [i * 10 for i in range(1, 11)]
