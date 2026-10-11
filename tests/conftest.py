import os
from pathlib import Path

import httpx2
import pytest

# The app reads its config and creates its OpenAI client on import, so set both up first.
# The fake key makes any request that could reach the real API fail right away.
os.environ["FASTAPI_AI_ASSISTANT_CONFIG"] = str(Path(__file__).with_name("config.test.toml"))
os.environ["OPENAI_API_KEY"] = "sk-test-not-a-real-key"

from support import AUTH, FakeOpenAI  # noqa: E402

from fastapi_ai_assistant import main, security  # noqa: E402


@pytest.fixture
def anyio_backend():
    return "asyncio"


@pytest.fixture(autouse=True)
def fake_openai(monkeypatch):
    """
    Every test talks to a fake OpenAI client instead of the real API.
    """
    fake = FakeOpenAI()
    monkeypatch.setattr(main, "client", fake)
    return fake


@pytest.fixture(autouse=True)
def reset_rate_limits():
    security.rate_limiter.hits.clear()


@pytest.fixture
async def client(anyio_backend):
    """
    Calls the app directly (no server) with valid credentials.
    """
    transport = httpx2.ASGITransport(app=main.app)
    async with httpx2.AsyncClient(
        transport=transport, base_url="http://testserver", auth=AUTH
    ) as client:
        yield client


@pytest.fixture
async def anonymous_client(anyio_backend):
    """
    Calls the app directly (no server) without credentials.
    """
    transport = httpx2.ASGITransport(app=main.app)
    async with httpx2.AsyncClient(transport=transport, base_url="http://testserver") as client:
        yield client
