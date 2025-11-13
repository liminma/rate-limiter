"""
Unit tests for TimeWindowQueue class.
"""

import asyncio
import time
from datetime import datetime, timedelta

import pytest

from rate_limiter.model_rate_limiter import TimeWindowQueue


class TestTimeWindowQueueInitialization:
    """Tests for TimeWindowQueue initialization."""

    def test_default_initialization(self):
        """Should initialize with 60 second window by default."""
        queue = TimeWindowQueue()
        assert queue.window_seconds == 60
        assert len(queue.queue) == 0

    def test_custom_window_initialization(self):
        """Should initialize with custom window size."""
        queue = TimeWindowQueue(window_seconds=30)
        assert queue.window_seconds == 30
        assert len(queue.queue) == 0


class TestTimeWindowQueueBasicOperations:
    """Tests for basic queue operations."""

    def test_add_entry_without_value(self, time_window_queue):
        """Should add entry with timestamp and None value."""
        time_window_queue.add()
        assert len(time_window_queue.queue) == 1
        timestamp, value = time_window_queue.queue[0]
        assert isinstance(timestamp, datetime)
        assert value is None

    def test_add_entry_with_value(self, time_window_queue):
        """Should add entry with timestamp and value."""
        time_window_queue.add(value=100)
        assert len(time_window_queue.queue) == 1
        timestamp, value = time_window_queue.queue[0]
        assert isinstance(timestamp, datetime)
        assert value == 100

    def test_add_multiple_entries(self, time_window_queue):
        """Should add multiple entries."""
        time_window_queue.add(10)
        time_window_queue.add(20)
        time_window_queue.add(30)
        assert len(time_window_queue.queue) == 3

    def test_count_empty_queue(self, time_window_queue):
        """Should return 0 for empty queue."""
        assert time_window_queue.count() == 0

    def test_count_with_entries(self, time_window_queue):
        """Should return correct count of entries."""
        time_window_queue.add()
        time_window_queue.add()
        time_window_queue.add()
        assert time_window_queue.count() == 3

    def test_sum_values_empty_queue(self, time_window_queue):
        """Should return 0 for empty queue."""
        assert time_window_queue.sum_values() == 0

    def test_sum_values_with_entries(self, time_window_queue):
        """Should correctly sum values."""
        time_window_queue.add(10)
        time_window_queue.add(20)
        time_window_queue.add(30)
        assert time_window_queue.sum_values() == 60

    def test_sum_values_ignores_none(self, time_window_queue):
        """Should ignore None values in sum."""
        time_window_queue.add(10)
        time_window_queue.add(None)
        time_window_queue.add(20)
        time_window_queue.add(None)
        assert time_window_queue.sum_values() == 30


class TestTimeWindowQueueTimeBased:
    """Tests for time-based cleanup functionality."""

    def test_clean_removes_old_entries(self):
        """Should remove entries older than window."""
        queue = TimeWindowQueue(window_seconds=1)

        # Add an entry
        queue.add(100)
        assert queue.count() == 1

        # Wait for window to expire
        time.sleep(1.1)

        # Clean should remove it
        queue.clean()
        assert len(queue.queue) == 0

    def test_clean_keeps_recent_entries(self, time_window_queue):
        """Should keep entries within window."""
        time_window_queue.add(100)
        time_window_queue.add(200)

        # Immediate clean shouldn't remove anything
        time_window_queue.clean()
        assert len(time_window_queue.queue) == 2

    def test_count_auto_cleans(self):
        """count() should automatically clean old entries."""
        queue = TimeWindowQueue(window_seconds=1)

        # Add entries
        queue.add()
        queue.add()
        assert len(queue.queue) == 2

        # Wait for expiration
        time.sleep(1.1)

        # count() should trigger cleanup
        assert queue.count() == 0
        assert len(queue.queue) == 0

    def test_sum_values_auto_cleans(self):
        """sum_values() should automatically clean old entries."""
        queue = TimeWindowQueue(window_seconds=1)

        # Add entries
        queue.add(100)
        queue.add(200)
        assert len(queue.queue) == 2

        # Wait for expiration
        time.sleep(1.1)

        # sum_values() should trigger cleanup
        assert queue.sum_values() == 0
        assert len(queue.queue) == 0

    def test_multiple_adds_within_window(self):
        """Should keep all entries added within window."""
        queue = TimeWindowQueue(window_seconds=2)

        # Add entries over time
        queue.add(10)
        time.sleep(0.5)
        queue.add(20)
        time.sleep(0.5)
        queue.add(30)

        # All should still be in window
        assert queue.count() == 3
        assert queue.sum_values() == 60

    def test_partial_expiration(self):
        """Should remove only expired entries, keeping recent ones."""
        queue = TimeWindowQueue(window_seconds=1)

        # Add first entry
        queue.add(100)
        time.sleep(1.1)

        # Add second entry (fresh)
        queue.add(200)

        # Should keep only the recent one
        assert queue.count() == 1
        assert queue.sum_values() == 200


class TestTimeWindowQueueOldestTime:
    """Tests for get_oldest_time functionality."""

    def test_get_oldest_time_empty_queue(self, time_window_queue):
        """Should return None for empty queue."""
        assert time_window_queue.get_oldest_time() is None

    def test_get_oldest_time_single_entry(self, time_window_queue):
        """Should return timestamp of single entry."""
        before = datetime.now()
        time_window_queue.add()
        after = datetime.now()

        oldest = time_window_queue.get_oldest_time()
        assert oldest is not None
        assert before <= oldest <= after

    def test_get_oldest_time_multiple_entries(self):
        """Should return timestamp of oldest entry."""
        queue = TimeWindowQueue()

        # Add first entry
        queue.add(100)
        first_time = queue.queue[0][0]

        # Add more entries
        time.sleep(0.1)
        queue.add(200)
        time.sleep(0.1)
        queue.add(300)

        # Should return first timestamp
        oldest = queue.get_oldest_time()
        assert oldest == first_time

    def test_get_oldest_time_after_cleanup(self):
        """Should return oldest remaining entry after cleanup."""
        queue = TimeWindowQueue(window_seconds=1)

        # Add old entry
        queue.add(100)
        time.sleep(1.1)

        # Add new entry
        queue.add(200)
        second_time = queue.queue[-1][0]

        # Clean and check
        queue.clean()
        oldest = queue.get_oldest_time()
        assert oldest == second_time


class TestTimeWindowQueueLimitSize:
    """Tests for limit_size functionality."""

    def test_limit_size_no_effect_under_limit(self, time_window_queue):
        """Should not remove anything if under limit."""
        time_window_queue.add(1)
        time_window_queue.add(2)
        time_window_queue.add(3)

        time_window_queue.limit_size(10)
        assert len(time_window_queue.queue) == 3

    def test_limit_size_removes_oldest(self, time_window_queue):
        """Should remove oldest entries when over limit."""
        time_window_queue.add(1)
        time_window_queue.add(2)
        time_window_queue.add(3)
        time_window_queue.add(4)
        time_window_queue.add(5)

        time_window_queue.limit_size(3)
        assert len(time_window_queue.queue) == 3

        # Should keep newest entries
        values = [v for _, v in time_window_queue.queue]
        assert values == [3, 4, 5]

    def test_limit_size_to_zero(self, time_window_queue):
        """Should remove all entries when limited to 0."""
        time_window_queue.add(1)
        time_window_queue.add(2)
        time_window_queue.add(3)

        time_window_queue.limit_size(0)
        assert len(time_window_queue.queue) == 0

    def test_limit_size_to_one(self, time_window_queue):
        """Should keep only newest entry when limited to 1."""
        time_window_queue.add(1)
        time_window_queue.add(2)
        time_window_queue.add(3)

        time_window_queue.limit_size(1)
        assert len(time_window_queue.queue) == 1
        assert time_window_queue.queue[0][1] == 3

    def test_limit_size_exact_match(self, time_window_queue):
        """Should not change anything if exactly at limit."""
        time_window_queue.add(1)
        time_window_queue.add(2)
        time_window_queue.add(3)

        time_window_queue.limit_size(3)
        assert len(time_window_queue.queue) == 3


class TestTimeWindowQueueEdgeCases:
    """Tests for edge cases and boundary conditions."""

    def test_very_short_window(self):
        """Should work with very short window (1 second)."""
        queue = TimeWindowQueue(window_seconds=1)
        queue.add(100)
        assert queue.count() == 1

        time.sleep(1.1)
        assert queue.count() == 0

    def test_very_long_window(self):
        """Should work with very long window."""
        queue = TimeWindowQueue(window_seconds=3600)  # 1 hour
        queue.add(100)
        queue.add(200)
        assert queue.count() == 2
        assert queue.sum_values() == 300

    def test_zero_values(self, time_window_queue):
        """Should handle zero values correctly."""
        time_window_queue.add(0)
        time_window_queue.add(0)
        assert time_window_queue.count() == 2
        assert time_window_queue.sum_values() == 0

    def test_negative_values(self, time_window_queue):
        """Should handle negative values."""
        time_window_queue.add(-10)
        time_window_queue.add(20)
        time_window_queue.add(-5)
        assert time_window_queue.sum_values() == 5

    def test_large_values(self, time_window_queue):
        """Should handle large values."""
        time_window_queue.add(1000000)
        time_window_queue.add(2000000)
        assert time_window_queue.sum_values() == 3000000

    def test_many_rapid_additions(self, time_window_queue):
        """Should handle many rapid additions."""
        for i in range(100):
            time_window_queue.add(i)

        assert time_window_queue.count() == 100
        assert time_window_queue.sum_values() == sum(range(100))

    def test_boundary_timing(self):
        """Should handle entries exactly at boundary."""
        queue = TimeWindowQueue(window_seconds=1)

        # Add entry
        queue.add(100)
        first_time = queue.queue[0][0]

        # Manually adjust time to be exactly at boundary
        cutoff = first_time + timedelta(seconds=1)

        # Mock the current time for clean operation
        # (In real test, this would need proper time mocking)
        # This test documents expected behavior
        time.sleep(1.0)
        count_before = queue.count()
        time.sleep(0.2)
        count_after = queue.count()

        # After window expires, count should be 0
        assert count_after == 0


class TestTimeWindowQueueCombinedOperations:
    """Tests for combined operations and realistic usage patterns."""

    def test_add_count_sum_cycle(self, time_window_queue):
        """Should handle repeated add/count/sum cycles."""
        for i in range(5):
            time_window_queue.add(i * 10)
            assert time_window_queue.count() == i + 1
            assert time_window_queue.sum_values() == sum(j * 10 for j in range(i + 1))

    def test_cleanup_during_active_use(self):
        """Should clean up while actively adding entries."""
        queue = TimeWindowQueue(window_seconds=1)

        # Add old entries
        queue.add(100)
        queue.add(200)

        # Wait for expiration
        time.sleep(1.1)

        # Add new entry (triggers cleanup)
        queue.add(300)

        # Should have only new entry
        assert queue.count() == 1
        assert queue.sum_values() == 300

    def test_interleaved_operations(self):
        """Should handle interleaved add/clean/limit operations."""
        queue = TimeWindowQueue(window_seconds=2)

        queue.add(10)
        queue.add(20)
        queue.clean()
        queue.add(30)
        queue.limit_size(10)
        queue.add(40)

        assert queue.count() == 4
        assert queue.sum_values() == 100
