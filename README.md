# rate-limiter

A Python library for rate limiting, with support for both simple requests-per-second limiting and advanced model-specific rate limiting for LLM APIs.

## Features

- 🚀 **Two rate limiting strategies**:
  - **RPS Limiters**: Simple requests-per-second limiting with token bucket algorithm
  - **Model Rate Limiters**: Advanced rate limiting for LLM APIs (RPM, TPM, parallel requests) using sliding time windows
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
uv add git+https://github.com/liminma/rate-limiter.git
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
        "gemini-2.5-pro": {"rpm": 1000, "tpm": 80000, "max_parallel_requests": 5},
    },
    default_config={"rpm": 10, "tpm": 20000, "max_parallel_requests": 5}
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
        "gemini-2.5-pro": {
            "rpm": 3500,
            "tpm": 90000,
            "max_parallel_requests": 20
        },
    },
    default_config={
        "rpm": 10,
        "tpm": 20000,
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
#     'gemini-2.5-pro': {
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
    {"model": "gemini-2.5-pro", "prompt": "Write a haiku about Python"},
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

### Example 4: Integrating with `google-adk`

#### By creating a plugin

```python
import logging

from google.adk.agents import LlmAgent
from google.adk.agents.callback_context import CallbackContext
from google.adk.models.base_llm import BaseLlm
from google.adk.models.llm_request import LlmRequest
from google.adk.models.llm_response import LlmResponse
from google.adk.plugins.base_plugin import BasePlugin

from rate_limiter import ModelRateLimiter


plugin_logger = logging.getLogger("rate_limiter.rate_limiter_plugin")

class RateLimiterPlugin(BasePlugin):
    """A custom plugin that applies rate limiting to calling LLM APIs."""

    def __init__(
        self,
        model_configs: dict[str, dict] | None = None,
        default_config: dict | None = None,
    ) -> None:
        super().__init__(name="rate_limiter")
        self.rate_limiter: ModelRateLimiter = ModelRateLimiter(
            model_configs=model_configs, default_config=default_config
        )

    async def before_model_callback(
        self, *, callback_context: CallbackContext, llm_request: LlmRequest
    ) -> None:
        model_name = self._get_model_name(callback_context)
        if model_name:
            # Acquire a slot
            plugin_logger.debug(f"[Plugin] Acquire request slot for model '{model_name}' in agent '{callback_context._invocation_context.agent.name}'")
            await self.rate_limiter.acquire(model_name)

    async def after_model_callback(
        self, *, callback_context: CallbackContext, llm_response: LlmResponse
    ) -> LlmResponse | None:
        model_name = self._get_model_name(callback_context)
        if model_name:
            # Release the slot
            plugin_logger.debug(f"[Plugin] Release request slot for model '{model_name}' in agent '{callback_context._invocation_context.agent.name}'")
            await self.rate_limiter.release(model_name)

    def _get_model_name(self, callback_context: CallbackContext) -> str | None:
        agent = callback_context._invocation_context.agent
        if isinstance(agent, LlmAgent):
            return self._llmagent_model_name(agent)
        else:
            return None

    def _llmagent_model_name(self, agent: LlmAgent) -> str | None:
        if isinstance(agent.model, BaseLlm):
            return agent.model.model.strip()

        model_name = agent.model.strip()
        if model_name:
            return agent.model.strip()
        else:
            ancestor_agent = agent.parent_agent
            while ancestor_agent is not None:
                if isinstance(ancestor_agent, LlmAgent):
                    return self._llmagent_model_name(ancestor_agent)
                ancestor_agent = ancestor_agent.parent_agent
            return None
```

then, use the plugin in a runner:

```python
import logging
from google.adk.runners import Runner


logging.basicConfig(level=logging.INFO)
logging.getLogger("rate_limiter").setLevel(logging.DEBUG)

GEMINI_RATE_CONFIGS = {
    "gemini-2.5-flash-lite": {"rpm": 15, "tpm": 250000, "max_parallel_requests": 5},
    "gemini-2.5-flash": {"rpm": 10, "tpm": 250000, "max_parallel_requests": 3},
    "gemini-2.5-pro": {"rpm": 2, "tpm": 150000, "max_parallel_requests": 1},
}
rate_limiter_plugin = RateLimiterPlugin(GEMINI_RATE_CONFIGS)

runner = Runner(
        agent=customer_support_agent,
        app_name=app_name,
        session_service=session_service,
        plugins=[rate_limiter_plugin]
    )
```

#### Or, by creating a custom model

First, create a subclass of `Gemini` model that's rate limited.

```python
from google.adk.models.google_llm import Gemini
from rate_limiter import ModelRateLimiter, RateLimitContext


class RateLimitedGemini(Gemini):
    """Gemini model that is aware of rate limits."""

    def __init__(
        self,
        *args,
        rate_limiter: ModelRateLimiter,
        **kwargs,
    ):
        """
        Initialize rate-limited Gemini model.

        Args:
            rate_limiter: ModelRateLimiter instance.
            *args: Positional arguments for the parent Gemini class.
            **kwargs: Keyword arguments for the parent Gemini class.
        """
        super().__init__(*args, **kwargs)
        # Store as a private attribute to avoid Pydantic validation issues.
        self._rate_limiter = rate_limiter

    @property
    def rate_limiter(self) -> ModelRateLimiter:
        """Access the rate limiter instance."""
        return self._rate_limiter

    async def generate_content_async(self, *args, **kwargs):
        """
        Generate content with rate limiting.

        This method wraps the parent's async generator with rate limiting.
        """
        async with RateLimitContext(self.rate_limiter, self.model):
            # Call parent method which returns an async generator
            async for response in super().generate_content_async(*args, **kwargs):
                yield response
```

then, initialize an `google.adk.agents.Agent` with a `RateLimitedGemini` model:

```python
from google.adk.agents import Agent
from google.adk.tools import google_search
from google.genai import types


# configure model limits
GEMINI_RATE_CONFIGS = {
    "gemini-2.5-pro": {"rpm": 2, "tpm": 125000, "max_parallel_requests": 2},
    "gemini-2.5-flash": {"rpm": 10, "tpm": 250000, "max_parallel_requests": 5},
    "gemini-2.5-flash-lite": {"rpm": 15, "tpm": 250000, "max_parallel_requests": 10},
}
limiter = ModelRateLimiter(GEMINI_RATE_CONFIGS)

# configure retry options
retry_config=types.HttpRetryOptions(
    attempts=5,  # Maximum retry attempts
    exp_base=7,  # Delay multiplier
    initial_delay=1,
    http_status_codes=[429, 500, 503, 504], # Retry on these HTTP errors
)

# initialize an agent
agent = Agent(
    name="helpful_assistant",
    model=RateLimitedGemini(
        rate_limiter=limiter,
        model="gemini-2.5-flash-lite",
        retry_options=retry_config
    ),
    description="A simple agent",
    instruction="You are a helpful assistant. You have the Google Search tool available to you to search for the latest information.",
    tools=[google_search]
)
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
