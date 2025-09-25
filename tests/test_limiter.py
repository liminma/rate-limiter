from rate_limiter.limiter import RateLimiter, AsyncRateLimiter, limit_rate, async_limit_rate
import unittest
from unittest.mock import patch, MagicMock, AsyncMock
import time
import asyncio


class TestRateLimiter(unittest.TestCase):

    def setUp(self):
        self.mock_monotonic_time = 0.0

    def _get_mock_monotonic_time(self):
        return self.mock_monotonic_time

    def _mock_sleep(self, seconds):
        self.mock_monotonic_time += seconds

    def test_init_valid_requests_per_second(self):
        limiter_int = RateLimiter(requests_per_second=10)
        self.assertEqual(limiter_int.requests_per_second, 10.0)
        self.assertEqual(limiter_int.bucket_capacity, 10.0)
        self.assertEqual(limiter_int.allowance, 10.0)

        limiter_float = RateLimiter(requests_per_second=0.5)
        self.assertEqual(limiter_float.requests_per_second, 0.5)
        self.assertEqual(limiter_float.bucket_capacity, 1.0)
        self.assertEqual(limiter_float.allowance, 1.0)

    def test_init_invalid_requests_per_second(self):
        with self.assertRaisesRegex(ValueError, "requests_per_second must be a positive number."):
            RateLimiter(0)
        with self.assertRaisesRegex(ValueError, "requests_per_second must be a positive number."):
            RateLimiter(-5)
        with self.assertRaisesRegex(ValueError, "requests_per_second must be a positive number."):
            RateLimiter("invalid")  # type: ignore[arg-type]

    @patch('time.sleep')
    @patch('time.monotonic')
    def test_acquire_immediate_success(self, mock_monotonic, mock_sleep):
        mock_monotonic.side_effect = self._get_mock_monotonic_time
        mock_sleep.side_effect = self._mock_sleep

        limiter = RateLimiter(5)  # capacity 5
        self.mock_monotonic_time = 100.0
        limiter.last_check = 100.0  # Synchronize last_check

        limiter.acquire()  # Should be immediate

        mock_sleep.assert_not_called()
        self.assertEqual(limiter.allowance, 4.0)  # 5 - 1
        self.assertEqual(limiter.last_check, 100.0)

    @patch('time.sleep')
    @patch('time.monotonic')
    def test_acquire_burst_capacity(self, mock_monotonic, mock_sleep):
        mock_monotonic.side_effect = self._get_mock_monotonic_time
        mock_sleep.side_effect = self._mock_sleep

        rps = 3
        limiter = RateLimiter(rps)  # capacity 3
        self.mock_monotonic_time = 200.0
        limiter.last_check = 200.0

        for i in range(int(rps)):
            limiter.acquire()
            self.assertEqual(limiter.allowance, float(rps - 1 - i))
            self.assertEqual(limiter.last_check, 200.0)  # No wait, last_check is current_time

        mock_sleep.assert_not_called()
        self.assertEqual(limiter.allowance, 0.0)

    @patch('time.sleep')
    @patch('time.monotonic')
    def test_acquire_waits_when_bucket_empty(self, mock_monotonic, mock_sleep):
        mock_monotonic.side_effect = self._get_mock_monotonic_time
        mock_sleep.side_effect = self._mock_sleep

        rps = 1
        limiter = RateLimiter(rps)  # capacity 1
        self.mock_monotonic_time = 0.0
        limiter.last_check = 0.0  # Start fresh
        limiter.allowance = 1.0

        # First acquire consumes the token
        limiter.acquire()
        mock_sleep.assert_not_called()
        self.assertEqual(limiter.allowance, 0.0)
        self.assertEqual(limiter.last_check, 0.0)  # current_time (0.0) + wait_time (0.0)

        # Second acquire should wait
        limiter.acquire()
        expected_wait_time = (1.0 - 0.0) / rps  # (1.0 - current_allowance) / rps
        mock_sleep.assert_called_once_with(expected_wait_time)
        self.assertEqual(limiter.allowance, 0.0)  # Consumed the token that just became available
        # last_check = current_time_at_call_start (0.0) + wait_time (1.0)
        self.assertEqual(limiter.last_check, 1.0)
        self.assertEqual(self._get_mock_monotonic_time(), 1.0)  # Time advanced by sleep

    @patch('time.sleep')
    @patch('time.monotonic')
    def test_acquire_refills_over_time(self, mock_monotonic, mock_sleep):
        mock_monotonic.side_effect = self._get_mock_monotonic_time
        mock_sleep.side_effect = self._mock_sleep

        rps = 2  # Generates 2 tokens/sec
        limiter = RateLimiter(rps)  # capacity 2
        self.mock_monotonic_time = 0.0
        limiter.last_check = 0.0
        limiter.allowance = 0.0  # Start with an empty bucket

        # Advance time by 0.25s, should generate 0.25 * 2 = 0.5 tokens
        self.mock_monotonic_time = 0.25

        limiter.acquire()

        # current_allowance was 0.0. time_passed = 0.25.
        # new_allowance = 0.0 + 0.25 * 2 = 0.5
        # wait_time = (1.0 - 0.5) / 2 = 0.25
        mock_sleep.assert_called_once_with(0.25)
        self.assertEqual(limiter.allowance, 0.0)
        # last_check = current_time (0.25) + wait_time (0.25) = 0.5
        self.assertEqual(limiter.last_check, 0.5)
        self.assertEqual(self._get_mock_monotonic_time(), 0.25 + 0.25)  # Time advanced

    @patch('time.sleep')
    @patch('time.monotonic')
    def test_acquire_respects_bucket_capacity_on_refill(self, mock_monotonic, mock_sleep):
        mock_monotonic.side_effect = self._get_mock_monotonic_time
        mock_sleep.side_effect = self._mock_sleep

        rps = 1
        limiter = RateLimiter(rps)  # capacity 1
        self.mock_monotonic_time = 0.0
        limiter.last_check = 0.0
        limiter.allowance = 1.0  # Full bucket

        # Advance time by 10s (way more than capacity)
        self.mock_monotonic_time = 10.0

        limiter.acquire()  # Should be immediate as bucket is full (or refilled to full)

        # time_passed = 10.0.
        # allowance = 1.0 + 10.0 * 1.0 = 11.0.
        # Capped: min(11.0, 1.0) = 1.0.
        mock_sleep.assert_not_called()
        self.assertEqual(limiter.allowance, 0.0)  # 1.0 - 1.0
        self.assertEqual(limiter.last_check, 10.0)  # current_time (10.0) + wait_time (0.0)


class TestAsyncRateLimiter(unittest.IsolatedAsyncioTestCase):  # Requires Python 3.8+

    def setUp(self):
        self.mock_monotonic_time = 0.0

    def _get_mock_monotonic_time(self):
        return self.mock_monotonic_time

    async def _mock_async_sleep(self, seconds):
        self.mock_monotonic_time += seconds

    def test_init_valid_requests_per_second_async(self):  # Not an async test method
        limiter_int = AsyncRateLimiter(requests_per_second=10)
        self.assertEqual(limiter_int.requests_per_second, 10.0)
        self.assertEqual(limiter_int.bucket_capacity, 10.0)
        self.assertEqual(limiter_int.allowance, 10.0)

        limiter_float = AsyncRateLimiter(requests_per_second=0.5)
        self.assertEqual(limiter_float.requests_per_second, 0.5)
        self.assertEqual(limiter_float.bucket_capacity, 1.0)
        self.assertEqual(limiter_float.allowance, 1.0)

    def test_init_invalid_requests_per_second_async(self):  # Not an async test method
        with self.assertRaisesRegex(ValueError, "requests_per_second must be a positive number."):
            AsyncRateLimiter(0)
        with self.assertRaisesRegex(ValueError, "requests_per_second must be a positive number."):
            AsyncRateLimiter(-5)
        with self.assertRaisesRegex(ValueError, "requests_per_second must be a positive number."):
            AsyncRateLimiter("invalid")  # type: ignore[arg-type]

    @patch('asyncio.sleep', new_callable=AsyncMock)
    @patch('time.monotonic')
    async def test_acquire_immediate_success_async(self, mock_monotonic, mock_async_sleep):
        mock_monotonic.side_effect = self._get_mock_monotonic_time
        mock_async_sleep.side_effect = self._mock_async_sleep

        limiter = AsyncRateLimiter(5)
        self.mock_monotonic_time = 100.0
        limiter.last_check = 100.0

        await limiter.acquire()

        mock_async_sleep.assert_not_called()
        self.assertEqual(limiter.allowance, 4.0)
        self.assertEqual(limiter.last_check, 100.0)

    @patch('asyncio.sleep', new_callable=AsyncMock)
    @patch('time.monotonic')
    async def test_acquire_burst_capacity_async(self, mock_monotonic, mock_async_sleep):
        mock_monotonic.side_effect = self._get_mock_monotonic_time
        mock_async_sleep.side_effect = self._mock_async_sleep

        rps = 3
        limiter = AsyncRateLimiter(rps)
        self.mock_monotonic_time = 200.0
        limiter.last_check = 200.0

        for i in range(int(rps)):
            await limiter.acquire()
            self.assertEqual(limiter.allowance, float(rps - 1 - i))
            self.assertEqual(limiter.last_check, 200.0)

        mock_async_sleep.assert_not_called()
        self.assertEqual(limiter.allowance, 0.0)

    @patch('asyncio.sleep', new_callable=AsyncMock)
    @patch('time.monotonic')
    async def test_acquire_waits_when_bucket_empty_async(self, mock_monotonic, mock_async_sleep):
        mock_monotonic.side_effect = self._get_mock_monotonic_time
        mock_async_sleep.side_effect = self._mock_async_sleep

        rps = 1
        limiter = AsyncRateLimiter(rps)
        self.mock_monotonic_time = 0.0
        limiter.last_check = 0.0
        limiter.allowance = 1.0

        await limiter.acquire()
        mock_async_sleep.assert_not_called()
        self.assertEqual(limiter.allowance, 0.0)
        self.assertEqual(limiter.last_check, 0.0)

        await limiter.acquire()
        expected_wait_time = (1.0 - 0.0) / rps
        mock_async_sleep.assert_called_once_with(expected_wait_time)
        self.assertEqual(limiter.allowance, 0.0)
        self.assertEqual(limiter.last_check, 1.0)
        self.assertEqual(self._get_mock_monotonic_time(), 1.0)

    @patch('asyncio.sleep', new_callable=AsyncMock)
    @patch('time.monotonic')
    async def test_acquire_refills_over_time_async(self, mock_monotonic, mock_async_sleep):
        mock_monotonic.side_effect = self._get_mock_monotonic_time
        mock_async_sleep.side_effect = self._mock_async_sleep

        rps = 2
        limiter = AsyncRateLimiter(rps)
        self.mock_monotonic_time = 0.0
        limiter.last_check = 0.0
        limiter.allowance = 0.0

        self.mock_monotonic_time = 0.25

        await limiter.acquire()

        mock_async_sleep.assert_called_once_with(0.25)  # wait_time = (1.0 - 0.5) / 2
        self.assertEqual(limiter.allowance, 0.0)
        self.assertEqual(limiter.last_check, 0.5)  # 0.25 (current) + 0.25 (wait)
        self.assertEqual(self._get_mock_monotonic_time(), 0.25 + 0.25)

    @patch('asyncio.sleep', new_callable=AsyncMock)
    @patch('time.monotonic')
    async def test_acquire_respects_bucket_capacity_on_refill_async(self, mock_monotonic, mock_async_sleep):
        mock_monotonic.side_effect = self._get_mock_monotonic_time
        mock_async_sleep.side_effect = self._mock_async_sleep

        rps = 1
        limiter = AsyncRateLimiter(rps)
        self.mock_monotonic_time = 0.0
        limiter.last_check = 0.0
        limiter.allowance = 1.0

        self.mock_monotonic_time = 10.0

        await limiter.acquire()

        mock_async_sleep.assert_not_called()
        self.assertEqual(limiter.allowance, 0.0)
        self.assertEqual(limiter.last_check, 10.0)


class TestLimitRateDecorator(unittest.TestCase):

    @patch('rate_limiter.limiter.RateLimiter')
    def test_decorator_with_requests_per_second(self, MockRateLimiter):
        mock_limiter_instance = MockRateLimiter.return_value
        rps = 5

        @limit_rate(requests_per_second=rps)
        def limited_function():
            return "Function called"

        # Decorator factory should have instantiated RateLimiter
        MockRateLimiter.assert_called_once_with(rps)

        result = limited_function()
        self.assertEqual(result, "Function called")
        mock_limiter_instance.acquire.assert_called_once()

    def test_decorator_with_existing_limiter(self):
        mock_limiter = MagicMock(spec=RateLimiter)

        @limit_rate(limiter=mock_limiter)
        def limited_function():
            return "Another call"

        result = limited_function()
        self.assertEqual(result, "Another call")
        mock_limiter.acquire.assert_called_once()

    def test_decorator_invalid_limiter_type(self):
        with self.assertRaisesRegex(TypeError, "Provided 'limiter' must be an instance of RateLimiter."):
            @limit_rate(limiter="not a limiter object")  # type: ignore[arg-type]
            def limited_function():
                pass  # pragma: no cover
            limited_function()  # Call to execute decorator logic if it didn't raise on definition

    def test_decorator_no_arguments(self):
        with self.assertRaisesRegex(ValueError, "Either a 'limiter' .* or 'requests_per_second' .* must be provided."):
            @limit_rate()
            def limited_function():
                pass  # pragma: no cover
            limited_function()

    def test_decorator_invalid_rps_in_factory(self):
        # This will raise ValueError from RateLimiter constructor
        with self.assertRaisesRegex(ValueError, "requests_per_second must be a positive number."):
            @limit_rate(requests_per_second=0)
            def limited_function():
                pass  # pragma: no cover
            limited_function()

    @patch('rate_limiter.limiter.RateLimiter')
    def test_decorator_preserves_function_metadata(self, MockRateLimiter):
        rps = 1

        @limit_rate(requests_per_second=rps)
        def original_function():
            """This is a docstring."""
            return "done"

        self.assertEqual(original_function.__name__, "original_function")
        self.assertEqual(original_function.__doc__, "This is a docstring.")

        # Call it to ensure functionality
        original_function()
        MockRateLimiter.return_value.acquire.assert_called_once()


class TestAsyncLimitRateDecorator(unittest.IsolatedAsyncioTestCase):

    @patch('rate_limiter.limiter.AsyncRateLimiter')
    async def test_decorator_with_requests_per_second_async(self, MockAsyncRateLimiter):
        mock_limiter_instance = MockAsyncRateLimiter.return_value
        # Make acquire an async mock if it needs to be awaited by the wrapper
        mock_limiter_instance.acquire = AsyncMock()
        rps = 5

        @async_limit_rate(requests_per_second=rps)
        async def limited_async_function():
            return "Async function called"

        MockAsyncRateLimiter.assert_called_once_with(rps)

        result = await limited_async_function()
        self.assertEqual(result, "Async function called")
        mock_limiter_instance.acquire.assert_called_once()

    async def test_decorator_with_existing_limiter_async(self):
        mock_limiter = MagicMock(spec=AsyncRateLimiter)
        # Ensure acquire is an awaitable mock
        mock_limiter.acquire = AsyncMock()

        @async_limit_rate(limiter=mock_limiter)
        async def limited_async_function():
            return "Another async call"

        result = await limited_async_function()
        self.assertEqual(result, "Another async call")
        mock_limiter.acquire.assert_called_once()

    async def test_decorator_invalid_limiter_type_async(self):
        with self.assertRaisesRegex(TypeError, "Provided 'limiter' must be an instance of AsyncRateLimiter."):
            @async_limit_rate(limiter="not an async limiter object")  # type: ignore[arg-type]
            async def limited_async_function():
                pass  # pragma: no cover
            await limited_async_function()

    async def test_decorator_no_arguments_async(self):
        with self.assertRaisesRegex(ValueError, "Either a 'limiter' .* or 'requests_per_second' .* must be provided."):
            @async_limit_rate()
            async def limited_async_function():
                pass  # pragma: no cover
            await limited_async_function()

    async def test_decorator_invalid_rps_in_factory_async(self):
        with self.assertRaisesRegex(ValueError, "requests_per_second must be a positive number."):
            @async_limit_rate(requests_per_second=0)
            async def limited_async_function():
                pass  # pragma: no cover
            await limited_async_function()

    @patch('rate_limiter.limiter.AsyncRateLimiter')
    async def test_decorator_preserves_function_metadata_async(self, MockAsyncRateLimiter):
        mock_limiter_instance = MockAsyncRateLimiter.return_value
        mock_limiter_instance.acquire = AsyncMock()
        rps = 1

        @async_limit_rate(requests_per_second=rps)
        async def original_async_function():
            """This is an async docstring."""
            return "async done"

        self.assertEqual(original_async_function.__name__, "original_async_function")
        self.assertEqual(original_async_function.__doc__, "This is an async docstring.")

        await original_async_function()
        mock_limiter_instance.acquire.assert_called_once()

    # Test that the decorated function is actually awaited
    async def test_decorated_async_function_is_awaited(self):
        rps = 100  # High rate, no blocking
        limiter = AsyncRateLimiter(rps)

        # Use a real flag to check if the async part executed
        async_part_executed = False

        @async_limit_rate(limiter=limiter)
        async def my_test_async_func():
            nonlocal async_part_executed
            await asyncio.sleep(0.001)  # Do some async work
            async_part_executed = True
            return "awaited_result"

        result = await my_test_async_func()
        self.assertTrue(async_part_executed)
        self.assertEqual(result, "awaited_result")

    # Test that the decorated sync function is actually called
    def test_decorated_sync_function_is_called(self):
        rps = 100  # High rate, no blocking
        limiter = RateLimiter(rps)

        sync_part_executed = False

        @limit_rate(limiter=limiter)
        def my_test_sync_func():
            nonlocal sync_part_executed
            sync_part_executed = True
            return "sync_result"

        result = my_test_sync_func()
        self.assertTrue(sync_part_executed)
        self.assertEqual(result, "sync_result")


if __name__ == '__main__':  # pragma: no cover
    unittest.main()
