import httpx2
import pytest
from openai import APIConnectionError
from support import (
    chat_text,
    code_interpreter_done,
    code_interpreter_started,
    error_event,
    failed_event,
    parse_sse,
    text_delta,
)

from fastapi_ai_assistant import main

pytestmark = pytest.mark.anyio

SAME_ORIGIN = {"Sec-Fetch-Site": "same-origin"}
OPENAI_ERROR = "[Error: could not get a response from OpenAI."


def connection_error() -> APIConnectionError:
    return APIConnectionError(request=httpx2.Request("POST", "https://api.openai.com/v1/responses"))


async def chat(client, **params) -> list[tuple[str, str]]:
    response = await client.get("/chat-stream", params=params, headers=SAME_ORIGIN)
    assert response.status_code == 200
    return parse_sse(response.text)


# --- Access control ---


@pytest.mark.parametrize("path", ["/", "/chat-stream?prompt=hi"])
async def test_every_page_asks_for_credentials(anonymous_client, path):
    response = await anonymous_client.get(path)
    assert response.status_code == 401
    assert response.headers["WWW-Authenticate"].startswith("Basic")


@pytest.mark.parametrize("path", ["/docs", "/redoc", "/openapi.json"])
async def test_api_docs_are_not_exposed(anonymous_client, path):
    response = await anonymous_client.get(path)
    assert response.status_code == 404


async def test_wrong_password_is_rejected(anonymous_client):
    response = await anonymous_client.get("/", auth=("tester", "wrong-password"))
    assert response.status_code == 401


async def test_index_renders_the_chat(client):
    response = await client.get("/")
    assert response.status_code == 200
    assert "/chat-stream?prompt=" in response.text
    assert 'maxlength="50"' in response.text


@pytest.mark.parametrize("site", ["cross-site", "same-site"])
async def test_requests_from_other_sites_are_rejected(client, fake_openai, site):
    response = await client.get(
        "/chat-stream", params={"prompt": "hi"}, headers={"Sec-Fetch-Site": site}
    )
    assert response.status_code == 403
    assert fake_openai.response_calls == []


@pytest.mark.parametrize("prompt", ["", "x" * 51])
async def test_prompt_length_is_limited(client, fake_openai, prompt):
    response = await client.get("/chat-stream", params={"prompt": prompt}, headers=SAME_ORIGIN)
    assert response.status_code == 422
    assert fake_openai.response_calls == []


async def test_rate_limit_is_explained_in_the_chat(client, fake_openai):
    for _ in range(3):  # MESSAGES_PER_MINUTE in config.test.toml
        await chat(client, prompt="hi")
    events = await chat(client, prompt="hi")
    assert chat_text(events).startswith("[Error: message limit reached. Try again in")
    assert events[-1] == ("message", "[DONE]")
    assert len(fake_openai.response_calls) == 3


@pytest.mark.parametrize(
    ("seconds", "text"), [(0.2, "1 s"), (59.5, "60 s"), (61, "2 min"), (5000, "2 h")]
)
def test_waits_are_rounded_up_to_a_readable_unit(seconds, text):
    assert main.format_wait(seconds) == text


# --- Chat ---


async def test_first_message_starts_a_conversation(client, fake_openai):
    events = await chat(client, prompt="What is 2 + 3?")
    assert events[0] == ("conversation", "conv_1")
    assert chat_text(events) == "Hello there"
    assert events[-1] == ("message", "[DONE]")
    [call] = fake_openai.response_calls
    assert call["conversation"] == "conv_1"
    assert call["input"] == "What is 2 + 3?"
    assert call["model"] == main.MODEL
    assert call["instructions"] == main.INSTRUCTIONS
    assert call["stream"] is True


async def test_next_messages_continue_the_conversation(client, fake_openai):
    events = await chat(client, prompt="And times 2?", conversation_id="conv_42")
    assert "conversation" not in [kind for kind, _ in events]
    assert fake_openai.conversations_created == 0
    assert fake_openai.response_calls[0]["conversation"] == "conv_42"


async def test_code_interpreter_calls_and_logs_are_shown(client, fake_openai):
    fake_openai.events = [
        text_delta("Let me run it:"),
        code_interpreter_started(),
        code_interpreter_done("5\n"),
        text_delta("It is 5."),
    ]
    events = await chat(client, prompt="2 + 3?")
    assert chat_text(events) == (
        "Let me run it:\n[Tool Call Created: code_interpreter]\n\n```\n5\n```\nIt is 5."
    )
    assert fake_openai.response_calls[0]["include"] == ["code_interpreter_call.outputs"]


@pytest.mark.parametrize(
    "failure",
    [error_event("Rate limit reached"), failed_event("Server error"), connection_error()],
    ids=["error event", "response.failed", "network error"],
)
async def test_openai_failures_are_shown_and_end_the_stream(client, fake_openai, failure):
    fake_openai.events = [text_delta("Hel"), failure, text_delta("lo")]
    events = await chat(client, prompt="hi")
    assert chat_text(events).startswith("Hel\n" + OPENAI_ERROR)
    assert events[-1] == ("message", "[DONE]")
    assert fake_openai.streams[0].closed


async def test_failure_before_streaming_is_shown(client, fake_openai):
    fake_openai.error_on_create = connection_error()
    events = await chat(client, prompt="hi")
    assert OPENAI_ERROR in chat_text(events)
    assert events[-1] == ("message", "[DONE]")
