from html.parser import HTMLParser
from urllib.parse import urlsplit

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


async def chat(client, **message) -> list[tuple[str, str]]:
    response = await client.post("/chat-stream", json=message, headers=SAME_ORIGIN)
    assert response.status_code == 200
    return parse_sse(response.text)


class ScriptsAndStyles(HTMLParser):
    """
    Collects the attributes of every <script>, <style> and stylesheet <link> in a page.
    """

    def __init__(self) -> None:
        super().__init__()
        self.found: list[dict] = []

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        if tag in ("script", "style") or (
            tag == "link" and "stylesheet" in (attrs.get("rel") or "")
        ):
            self.found.append(attrs)


def scripts_and_styles(html: str) -> list[dict]:
    parser = ScriptsAndStyles()
    parser.feed(html)
    return parser.found


def csp_directives(policy: str) -> dict[str, str]:
    """
    "default-src 'none'; script-src 'nonce-x'" -> {"default-src": "'none'", "script-src": ...}
    """
    return dict(directive.strip().partition(" ")[::2] for directive in policy.split(";"))


# --- Access control ---


@pytest.mark.parametrize(("method", "path"), [("GET", "/"), ("POST", "/chat-stream")])
async def test_every_page_asks_for_credentials(anonymous_client, method, path):
    response = await anonymous_client.request(method, path)
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
    assert "fetch(`/chat-stream`" in response.text
    assert 'maxlength="50"' in response.text


async def test_files_from_other_sites_are_pinned_with_sri(client):
    # Whoever controls a CDN URL without an integrity hash can run code in the chat.
    response = await client.get("/")
    external = [
        attrs
        for attrs in scripts_and_styles(response.text)
        if urlsplit(attrs.get("src") or attrs.get("href") or "").netloc  # Another site
    ]
    assert external  # The page does load its libraries from a CDN.
    for attrs in external:
        url = attrs.get("src") or attrs.get("href")
        assert (attrs.get("integrity") or "").startswith("sha384-"), f"no SRI hash: {url}"
        assert attrs.get("crossorigin") == "anonymous", f"no crossorigin: {url}"


async def test_page_has_a_strict_content_security_policy(client):
    first, second = await client.get("/"), await client.get("/")
    directives = csp_directives(first.headers["Content-Security-Policy"])
    nonce = directives["script-src"].removeprefix("'nonce-").removesuffix("'")

    # Only scripts with this response's nonce run: no 'unsafe-inline', no 'unsafe-eval'.
    assert directives["script-src"] == f"'nonce-{nonce}'"
    assert directives["default-src"] == "'none'"
    assert directives["frame-ancestors"] == "'none'"
    tags = scripts_and_styles(first.text)
    assert tags and all(attrs.get("nonce") == nonce for attrs in tags)
    # A new nonce on every load, so a nonce seen once is useless later.
    assert second.headers["Content-Security-Policy"] != first.headers["Content-Security-Policy"]


async def test_every_response_has_the_security_headers(client, anonymous_client):
    responses = [
        await client.get("/"),
        await client.post("/chat-stream", json={"prompt": "hi"}, headers=SAME_ORIGIN),
        await client.get("/no-such-page"),
        await anonymous_client.get("/"),
    ]
    for response in responses:
        assert "frame-ancestors 'none'" in response.headers["Content-Security-Policy"]
        assert response.headers["X-Frame-Options"] == "DENY"
        assert response.headers["X-Content-Type-Options"] == "nosniff"
        assert response.headers["Referrer-Policy"] == "no-referrer"


async def test_messages_cannot_travel_in_the_url(client, fake_openai):
    response = await client.get("/chat-stream", params={"prompt": "hi"}, headers=SAME_ORIGIN)
    assert response.status_code == 405
    assert fake_openai.response_calls == []


@pytest.mark.parametrize("site", ["cross-site", "same-site"])
async def test_requests_from_other_sites_are_rejected(client, fake_openai, site):
    response = await client.post(
        "/chat-stream", json={"prompt": "hi"}, headers={"Sec-Fetch-Site": site}
    )
    assert response.status_code == 403
    assert fake_openai.response_calls == []


async def test_json_sent_as_plain_text_is_rejected(client, fake_openai):
    # A form on another site can send this body without a CORS preflight, but FastAPI only
    # reads JSON from requests labeled as JSON: a second line of defense against CSRF.
    response = await client.post(
        "/chat-stream", content='{"prompt": "hi"}', headers={"Content-Type": "text/plain"}
    )
    assert response.status_code == 422
    assert fake_openai.response_calls == []


@pytest.mark.parametrize("prompt", ["", "x" * 51])
async def test_prompt_length_is_limited(client, fake_openai, prompt):
    response = await client.post("/chat-stream", json={"prompt": prompt}, headers=SAME_ORIGIN)
    assert response.status_code == 422
    assert fake_openai.response_calls == []


async def test_rate_limit_answers_429_with_the_wait(client, fake_openai):
    for _ in range(3):  # MESSAGES_PER_MINUTE in config.test.toml
        await chat(client, prompt="hi")
    response = await client.post("/chat-stream", json={"prompt": "hi"}, headers=SAME_ORIGIN)
    assert response.status_code == 429
    assert response.json()["detail"].startswith("Message limit reached. Try again in")
    assert int(response.headers["Retry-After"]) > 0
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
