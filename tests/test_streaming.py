"""End-to-end checks against a real uvicorn server, where the streaming actually happens."""

import threading
import time

import httpx2
import pytest
import uvicorn
from support import AUTH, text_delta

from fastapi_ai_assistant import main


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


def stream_chat(server_url):
    return httpx2.stream(
        "GET", f"{server_url}/chat-stream", params={"prompt": "hi"}, auth=AUTH, timeout=10
    )


def test_tokens_reach_the_client_while_being_generated(server_url, fake_openai):
    fake_openai.events = [text_delta(f"token{i} ") for i in range(4)]
    fake_openai.delay = 0.25
    arrivals = []
    with stream_chat(server_url) as response:
        for line in response.iter_lines():
            if line.startswith("data: token"):
                arrivals.append(time.monotonic())
    assert len(arrivals) == 4
    # OpenAI produces them 0.25 s apart; a buffered response would deliver them all at once.
    assert arrivals[-1] - arrivals[0] > 0.5


def test_stop_closes_the_openai_stream(server_url, fake_openai):
    fake_openai.events = [text_delta(f"token{i} ") for i in range(40)]  # 4 s in total.
    fake_openai.delay = 0.1
    with stream_chat(server_url) as response:
        for line in response.iter_lines():
            if line.startswith("data: token"):
                break  # The user hits Stop after the first token.
    stream = fake_openai.streams[0]
    deadline = time.monotonic() + 2
    while not stream.closed and time.monotonic() < deadline:
        time.sleep(0.02)
    assert stream.closed
    assert stream.sent < len(fake_openai.events)
