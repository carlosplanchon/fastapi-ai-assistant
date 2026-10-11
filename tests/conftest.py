import os
import threading
import time
from pathlib import Path

import httpx2
import pytest
import uvicorn

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


@pytest.fixture
def server_url():
    """
    Run the app with uvicorn in a background thread, on a free port.
    """
    config = uvicorn.Config(main.app, host="127.0.0.1", port=0, log_level="warning")
    server = uvicorn.Server(config)
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()
    deadline = time.monotonic() + 10
    while not server.started:
        assert time.monotonic() < deadline, "uvicorn did not start"
        time.sleep(0.01)
    port = server.servers[0].sockets[0].getsockname()[1]
    yield f"http://127.0.0.1:{port}"
    server.should_exit = True
    thread.join(timeout=10)
