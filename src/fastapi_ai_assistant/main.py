#!/usr/bin/env python3

import logging
import math
import secrets
from collections.abc import AsyncIterator
from pathlib import Path
from typing import Annotated

import uvicorn
from fastapi import Depends, FastAPI, HTTPException, Request, status
from fastapi.middleware import Middleware
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates
from openai import AsyncOpenAI, OpenAIError
from pydantic import BaseModel, Field
from sse_starlette import EventSourceResponse  # SSE streaming utility

from fastapi_ai_assistant.security import (
    MAX_PROMPT_CHARS,
    SecurityHeaders,
    authenticate,
    page_policy,
    rate_limiter,
    reject_cross_site,
)
from fastapi_ai_assistant.shared import CONFIG

logger = logging.getLogger(__name__)

MODEL = "gpt-5.5"
INSTRUCTIONS = "You are a personal math tutor. Write and run code to answer math questions."

# Async client: waiting for OpenAI never blocks the event loop, so tokens reach
# the browser as they are generated and other requests keep being served.
client = AsyncOpenAI()


async def stream_reply(prompt: str, conversation_id: str | None) -> AsyncIterator[dict[str, str]]:
    """
    Async generator that yields the assistant's reply as SSE events.
    It always ends with "[DONE]", also when OpenAI fails, so the UI never hangs.
    """
    try:
        if conversation_id is None:
            # First message of this page: start a conversation and send its id
            # to the browser, which passes it back with the next messages.
            conversation = await client.conversations.create()
            conversation_id = conversation.id
            yield {"event": "conversation", "data": conversation_id}

        stream = await client.responses.create(
            model=MODEL,
            instructions=INSTRUCTIONS,
            input=prompt,
            conversation=conversation_id,
            tools=[{"type": "code_interpreter", "container": {"type": "auto"}}],
            include=["code_interpreter_call.outputs"],  # Needed to receive the logs.
            stream=True,
        )
        # Leaving this block closes the connection to OpenAI, also when the user
        # hits Stop and sse-starlette cancels this generator.
        async with stream:
            async for event in stream:
                if event.type == "response.output_text.delta":
                    yield {"data": event.delta}
                elif event.type == "response.output_item.added":
                    if event.item.type == "code_interpreter_call":
                        yield {"data": "\n[Tool Call Created: code_interpreter]\n"}
                elif event.type == "response.output_item.done":
                    if event.item.type == "code_interpreter_call":
                        for output in event.item.outputs or []:
                            if output.type == "logs":
                                # In a code block, so the chat doesn't render them as Markdown.
                                yield {"data": f"\n```\n{output.logs.rstrip()}\n```\n"}
                # The SDK yields these as regular events instead of raising.
                elif event.type == "error":
                    raise OpenAIError(event.message)
                elif event.type == "response.failed":
                    raise OpenAIError(f"Response failed: {event.response.error}")
    except Exception:
        # The SSE response has already started, so the only way to report a
        # failure (OpenAI error, network drop, bug) is inside the stream itself.
        logger.exception("Could not stream the reply")
        yield {"data": "\n[Error: could not get a response from OpenAI. Check the server logs.]"}

    yield {"data": "[DONE]"}


def format_wait(seconds: float) -> str:
    """
    Round a wait up to a short human unit, e.g. "40 s", "3 min" or "5 h".
    """
    if seconds < 60:
        return f"{math.ceil(seconds)} s"
    if seconds < 60 * 60:
        return f"{math.ceil(seconds / 60)} min"
    return f"{math.ceil(seconds / (60 * 60))} h"


# Setup Jinja2 templates (next to this file, so the app runs from any directory)
templates = Jinja2Templates(directory=Path(__file__).parent / "templates")

root_path: str = CONFIG["app"]["ROOT_PATH"]

if root_path != "":
    root_prefix = f"/{root_path}"
else:
    root_prefix = ""

# Every route requires the credentials from config.toml. The automatic API docs
# (/docs, /redoc, /openapi.json) are turned off: FastAPI serves them without dependencies.
app = FastAPI(
    root_path=root_prefix,
    title="AI Chat App",
    dependencies=[Depends(authenticate)],
    docs_url=None,
    redoc_url=None,
    openapi_url=None,
    middleware=[Middleware(SecurityHeaders)],
)


@app.get("/", response_class=HTMLResponse)
def get_index(request: Request):
    """
    Return the main page (Jinja template), with a new nonce for its Content Security Policy.
    """
    nonce = secrets.token_urlsafe(16)
    return templates.TemplateResponse(
        request,
        "index.html",
        {"root_prefix": root_prefix, "max_prompt_chars": MAX_PROMPT_CHARS, "csp_nonce": nonce},
        headers={"Content-Security-Policy": page_policy(nonce)},
    )


class ChatMessage(BaseModel):
    # In the request body rather than the URL, so messages don't end up in access logs.
    prompt: str = Field(min_length=1, max_length=MAX_PROMPT_CHARS)
    conversation_id: str | None = None


@app.post(
    "/chat-stream",
    response_class=EventSourceResponse,
    dependencies=[Depends(reject_cross_site)],
)
async def chat_stream(username: Annotated[str, Depends(authenticate)], message: ChatMessage):
    """
    This endpoint uses SSE to stream AI responses in real time.
    """
    wait = rate_limiter.acquire(username)
    if wait:
        logger.warning("Rate limit reached for user %r", username)
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail=f"Message limit reached. Try again in {format_wait(wait)}.",
            headers={"Retry-After": str(math.ceil(wait))},
        )
    return EventSourceResponse(stream_reply(message.prompt, message.conversation_id))


def main() -> None:
    uvicorn.run(
        app,
        host="0.0.0.0",
        port=8000,
        log_config={
            "version": 1,
            "disable_existing_loggers": False,
            "formatters": {
                "default": {
                    "()": "uvicorn.logging.DefaultFormatter",
                    "fmt": "%(asctime)s - %(levelname)s - %(message)s",
                    "datefmt": "%Y-%m-%d %H:%M:%S",
                }
            },
            "handlers": {
                "default": {
                    "formatter": "default",
                    "class": "logging.StreamHandler",
                    "stream": "ext://sys.stdout",
                }
            },
            "loggers": {
                "uvicorn": {"handlers": ["default"], "level": "INFO"},
                "fastapi_ai_assistant": {"handlers": ["default"], "level": "INFO"},
            },
        },
    )


if __name__ == "__main__":
    main()
