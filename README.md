# rate-limiter

A Python library for rate limiting using the token bucket algorithm, with support for both simple requests-per-second limiting and advanced model-specific rate limiting for LLM APIs.

## Features

- 🚀 **Two rate limiting strategies**:
  - **RPS Limiters**: Simple requests-per-second limiting with token bucket algorithm
  - **Model Rate Limiters**: Advanced rate limiting for LLM APIs (RPM, TPM, parallel requests)
- ⚡ **Async & Sync Support**: Works with both synchronous and asynchronous code
- 🎯 **Easy to Use**: Decorators and context managers for seamless integration
- 🔒 **Thread-Safe**: Built-in synchronization for concurrent operations
- 📊 **Statistics**: Monitor rate limit usage in real-time
- 🪶 **Zero Dependencies**: No external dependencies required
- 🐍 **Modern Python**: Requires Python 3.10+

## Installation

```bash
pip install git+https://github.com/liminma/rate-limiter.git
```

Or using [uv](https://github.com/astral-sh/uv):

```bash
uv add git+https://github.com/owner/repo.git
```

## Quick Start

### Simple Rate Limiting (Requests Per Second)

```python
from rate_limiter import RateLimiter, limit_rate

# Using the limiter directly
limiter = RateLimiter(requests_per_second=10)

for i in range(20):
    limiter.acquire()
    print(f"Request {i+1}")  # Executes at 10 requests/second

# Using as a decorator
@limit_rate(requests_per_second=5)
def make_api_call():
    return "API response"

# Automatically rate limited to 5 calls/second
for _ in range(10):
    result = make_api_call()
```

### Model Rate Limiting (LLM APIs)

```python
from rate_limiter import ModelRateLimiter, RateLimitContext

# Configure per-model limits
limiter = ModelRateLimiter(
    model_configs={
        "gpt-4": {"rpm": 500, "tpm": 40000, "max_parallel_requests": 10},
        "claude-3": {"rpm": 1000, "tpm": 80000, "max_parallel_requests": 5},
    },
    default_config={"rpm": 10, "tpm": 200000, "max_parallel_requests": 5}
)

# Use with context manager (recommended)
async with RateLimitContext(limiter, "gpt-4", estimated_tokens=8000, actual_tokens=7500):
    response = await call_llm_api()
    # Rate limits automatically managed
```

## Usage

### Basic Rate Limiting (RPS)

#### Synchronous Usage

```python
from rate_limiter import RateLimiter

limiter = RateLimiter(requests_per_second=10)

def process_item(item):
    limiter.acquire()  # Blocks until token is available
    # Your rate-limited code here
    print(f"Processing {item}")

for item in range(100):
    process_item(item)
```

#### Asynchronous Usage

```python
from rate_limiter import AsyncRateLimiter
import asyncio

limiter = AsyncRateLimiter(requests_per_second=10)

async def fetch_data(url):
    await limiter.acquire()  # Async wait for token
    # Your rate-limited async code here
    return f"Data from {url}"

async def main():
    tasks = [fetch_data(f"https://api.example.com/{i}") for i in range(100)]
    results = await asyncio.gather(*tasks)

asyncio.run(main())
```

#### Using Decorators

```python
from rate_limiter import limit_rate, async_limit_rate

# Synchronous decorator
@limit_rate(requests_per_second=5)
def send_email(to, subject, body):
    # Rate limited to 5 calls/second
    print(f"Sending email to {to}")

# Asynchronous decorator
@async_limit_rate(requests_per_second=10)
async def fetch_user(user_id):
    # Rate limited to 10 calls/second
    return f"User {user_id}"
```

#### Sharing a Limiter Across Functions

```python
from rate_limiter import RateLimiter, limit_rate

# Create a shared limiter
shared_limiter = RateLimiter(requests_per_second=10)

# Use the same limiter for multiple functions
@limit_rate(limiter=shared_limiter)
def function_a():
    print("Function A")

@limit_rate(limiter=shared_limiter)
def function_b():
    print("Function B")

# Both functions share the same rate limit (10 req/s total)
```

### Model Rate Limiting (LLM APIs)

The `ModelRateLimiter` is designed for LLM APIs where you need to track:
- **RPM** (Requests Per Minute)
- **TPM** (Tokens Per Minute)
- **Parallel Requests** (Maximum concurrent requests)

Each model can have its own configuration.

#### Configuration

```python
from rate_limiter import ModelRateLimiter

limiter = ModelRateLimiter(
    model_configs={
        "gpt-4": {
            "rpm": 500,              # 500 requests per minute
            "tpm": 40000,            # 40K tokens per minute
            "max_parallel_requests": 10  # Max 10 concurrent requests
        },
        "gpt-3.5-turbo": {
            "rpm": 3500,
            "tpm": 90000,
            "max_parallel_requests": 20
        },
    },
    default_config={
        "rpm": 10,
        "tpm": 200000,
        "max_parallel_requests": 5
    }
)
```

#### Using Context Manager (Recommended)

```python
from rate_limiter import RateLimitContext

async def call_llm(model, prompt):
    # Estimate tokens before the call
    estimated_tokens = estimate_tokens(prompt)

    async with RateLimitContext(
        limiter,
        model_name=model,
        estimated_tokens=estimated_tokens,
        actual_tokens=estimated_tokens  # Updated after API call if known
    ):
        response = await api_client.create_completion(
            model=model,
            prompt=prompt
        )
        # If you get actual token count from response, you can update it
        # by passing actual_tokens to the context manager
        return response

# The context manager automatically:
# 1. Waits for available rate limit capacity
# 2. Acquires a parallel request slot
# 3. Records the request and token usage
# 4. Releases the slot on completion (even if exception occurs)
```

#### Manual Acquire/Release

```python
async def call_llm_manual(model, prompt):
    estimated_tokens = estimate_tokens(prompt)

    # Acquire rate limit
    await limiter.acquire(model, estimated_tokens=estimated_tokens)

    try:
        response = await api_client.create_completion(
            model=model,
            prompt=prompt
        )
        actual_tokens = response.usage.total_tokens
        return response
    finally:
        # Always release, even on error
        await limiter.release(model, token_count=actual_tokens)
```

#### Monitoring Statistics

```python
# Get current statistics for all models
stats = limiter.get_stats()
print(stats)
# Output:
# {
#     'gpt-4': {
#         'requests': '45/500',      # 45 requests out of 500 RPM limit
#         'tokens': '35000/40000',   # 35K tokens out of 40K TPM limit
#         'active': '3/10'           # 3 active requests out of 10 max
#     },
#     'gpt-3.5-turbo': {
#         'requests': '120/3500',
#         'tokens': '15000/90000',
#         'active': '5/20'
#     }
# }
```

#### Handling Different Token Counts

```python
async with RateLimitContext(
    limiter,
    model_name="gpt-4",
    estimated_tokens=10000,  # Used for pre-checking if request can proceed
    actual_tokens=8500       # Actual usage recorded after completion
):
    # If estimated > actual: You're being conservative (recommended)
    # If estimated < actual: May cause temporary TPM limit violations
    response = await call_api()
```

## Examples

### Example 1: API Client with Rate Limiting

```python
from rate_limiter import AsyncRateLimiter
import aiohttp
import asyncio

class RateLimitedAPIClient:
    def __init__(self, base_url, requests_per_second=10):
        self.base_url = base_url
        self.limiter = AsyncRateLimiter(requests_per_second)

    async def get(self, endpoint):
        await self.limiter.acquire()
        async with aiohttp.ClientSession() as session:
            async with session.get(f"{self.base_url}/{endpoint}") as resp:
                return await resp.json()

# Usage
client = RateLimitedAPIClient("https://api.example.com", requests_per_second=5)
results = await asyncio.gather(*[client.get(f"users/{i}") for i in range(50)])
```

### Example 2: Multi-Model LLM Application

```python
from rate_limiter import ModelRateLimiter, RateLimitContext
import asyncio

class LLMOrchestrator:
    def __init__(self):
        self.limiter = ModelRateLimiter(
            model_configs={
                "gpt-4": {"rpm": 500, "tpm": 40000, "max_parallel_requests": 10},
                "gpt-3.5-turbo": {"rpm": 3500, "tpm": 90000, "max_parallel_requests": 50},
                "claude-3": {"rpm": 1000, "tpm": 80000, "max_parallel_requests": 5},
            }
        )

    async def generate(self, model, prompt):
        estimated = len(prompt.split()) * 1.3  # Rough token estimate

        async with RateLimitContext(
            self.limiter,
            model_name=model,
            estimated_tokens=int(estimated),
            actual_tokens=int(estimated)
        ):
            # Your LLM API call here
            response = await self._call_llm_api(model, prompt)
            return response

    async def batch_generate(self, requests):
        """Process multiple requests across different models."""
        tasks = [
            self.generate(req["model"], req["prompt"])
            for req in requests
        ]
        return await asyncio.gather(*tasks)

    def show_stats(self):
        """Display current rate limit status."""
        stats = self.limiter.get_stats()
        for model, stat in stats.items():
            print(f"{model}: {stat}")

# Usage
orchestrator = LLMOrchestrator()
requests = [
    {"model": "gpt-4", "prompt": "Explain quantum computing"},
    {"model": "gpt-3.5-turbo", "prompt": "Write a haiku about Python"},
    {"model": "claude-3", "prompt": "Summarize this article..."},
]
results = await orchestrator.batch_generate(requests)
orchestrator.show_stats()
```

### Example 3: Burst Capacity

The token bucket algorithm allows for bursts up to the bucket capacity:

```python
from rate_limiter import RateLimiter
import time

limiter = RateLimiter(requests_per_second=10)

# Initial burst: Up to 10 requests can go through immediately
start = time.time()
for i in range(15):
    limiter.acquire()
    print(f"Request {i+1} at {time.time() - start:.2f}s")

# Output shows:
# - First 10 requests execute immediately (burst capacity)
# - Remaining 5 requests are rate limited to 10 req/s
```

## API Reference

### RPS Limiters

- **`RateLimiter(requests_per_second)`**: Synchronous rate limiter
  - `.acquire()`: Block until token is available

- **`AsyncRateLimiter(requests_per_second)`**: Asynchronous rate limiter
  - `.acquire()`: Async wait until token is available

- **`@limit_rate(requests_per_second=X)`**: Decorator for sync functions
- **`@limit_rate(limiter=limiter_instance)`**: Decorator with shared limiter

- **`@async_limit_rate(requests_per_second=X)`**: Decorator for async functions
- **`@async_limit_rate(limiter=limiter_instance)`**: Decorator with shared limiter

### Model Rate Limiters

- **`ModelRateLimiter(model_configs, default_config)`**: Multi-model rate limiter
  - `.acquire(model_name, estimated_tokens)`: Acquire rate limit for a model
  - `.release(model_name, token_count)`: Release rate limit for a model
  - `.get_stats()`: Get current statistics for all models

- **`RateLimitContext(limiter, model_name, estimated_tokens, actual_tokens)`**: Context manager
  - Automatically handles acquire/release
  - Ensures cleanup on exceptions

- **`RateLimitConfig`**: Configuration dataclass
  - `rpm`: Requests per minute
  - `tpm`: Tokens per minute
  - `max_parallel_requests`: Maximum concurrent requests

## Testing

### Run All Tests

```bash
uv run pytest tests/ -v
```

Some tests are slow. To skip slow tests:

```bash
uv run pytest tests/ -v -m "not slow"
```

To run slow tests only:

```bash
uv run pytest tests/ -v -m "slow"
```

### Run Specific Test File

For example:

```bash
uv run pytest tests/test_model_rate_limiter.py -v
uv run pytest tests/test_rate_limit_context.py -v
```

### Run Specific Test Class

For example:

```bash
uv run pytest tests/test_time_window_queue.py::TestTimeWindowQueueBasicOperations -v
```

### Run Specific Test

For example:

```bash
uv run pytest tests/test_model_rate_limiter.py::TestModelRateLimiterAcquire::test_acquire_succeeds_when_under_limits -v
```

## How It Works

### Token Bucket Algorithm (RPS Limiters)

The `RateLimiter` and `AsyncRateLimiter` use the classic token bucket algorithm:

1. **Bucket Capacity**: Maximum tokens that can accumulate (burst capacity)
2. **Refill Rate**: Tokens are added at `requests_per_second` rate
3. **Token Consumption**: Each request consumes one token
4. **Blocking**: If no tokens available, wait until one is generated

This allows for natural bursting while maintaining average rate limits.

### Sliding Window (Model Rate Limiters)

The `ModelRateLimiter` uses sliding time windows to track:

1. **Request Times**: Tracks all requests in the last 60 seconds
2. **Token Usage**: Tracks all token usage in the last 60 seconds
3. **Active Requests**: Semaphore-based limiting of concurrent requests

Each model maintains independent state with automatic cleanup of old entries.
