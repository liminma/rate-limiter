# rate-limiter

An easy-to-use rate limiting library when calling LLM APIs.

## Introduction

When using LLM APIs, it's often necessary to manage request rates to prevent overloading the API and avoid errors. This library offers a simple way to implement rate limiting in your applications.

## Installation

```bash
pip install git+https://github.com/liminma/rate-limiter.git
```

## Usage

### decorate a regular function

```python
import random
import time
from rate_limiter import limit_rate

@limit_rate(requests_per_second=2)
def func(request_id):
    print(f"Start request: {request_id} ...")

    # randomly sleep between 1 to 5 seconds to simulate
    # a request that takes some time to complete.
    time.sleep(random.randint(1, 5))

    print(f"Finished request: {request_id}.")


if __name__ == "__main__":
    for i in range(5):
        func(i)
```

### decorate an async function

```python
import random
import asyncio
from rate_limiter import async_limit_rate

@async_limit_rate(requests_per_second=2)
async def a_func(request_id):
    print(f"Start async request: {request_id} ...")

    # randomly pause between 1 to 5 seconds to simulate
    # an async request that takes some time to complete.
    await asyncio.sleep(random.randint(1, 5))

    print(f"Finished async request: {request_id}.")

async def main():
    await asyncio.gather(
        *(a_func(i) for i in range(5))
    )

if __name__ == "__main__":
    asyncio.run(main())
```