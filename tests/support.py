"""Helpers shared by the tests: credentials, an SSE parser and a fake OpenAI client."""

import asyncio
from types import SimpleNamespace

from openai.types.conversations import Conversation
from openai.types.responses import (
    Response,
    ResponseCodeInterpreterToolCall,
    ResponseError,
    ResponseErrorEvent,
    ResponseFailedEvent,
    ResponseOutputItemAddedEvent,
    ResponseOutputItemDoneEvent,
    ResponseTextDeltaEvent,
)
from openai.types.responses.response_code_interpreter_tool_call import OutputLogs

# Must match tests/config.test.toml.
AUTH = ("tester", "test-password")


def parse_sse(body: str) -> list[tuple[str, str]]:
    """
    Split an SSE body into (event type, data) pairs, the way EventSource does.
    """
    events = []
    for block in body.replace("\r\n", "\n").split("\n\n"):
        event_type, data = "message", []
        for line in block.split("\n"):
            field, _, value = line.partition(":")
            value = value.removeprefix(" ")
            if field == "event":
                event_type = value
            elif field == "data":
                data.append(value)
        if data:
            events.append((event_type, "\n".join(data)))
    return events


def chat_text(events: list[tuple[str, str]]) -> str:
    """
    The text the chat UI shows: every message event except the final "[DONE]".
    """
    return "".join(data for kind, data in events if kind == "message" and data != "[DONE]")


# Real SDK event classes; model_construct() skips the fields the app doesn't read.


def text_delta(delta: str) -> ResponseTextDeltaEvent:
    return ResponseTextDeltaEvent.model_construct(type="response.output_text.delta", delta=delta)


def code_interpreter_started() -> ResponseOutputItemAddedEvent:
    item = ResponseCodeInterpreterToolCall.model_construct(
        type="code_interpreter_call", outputs=None
    )
    return ResponseOutputItemAddedEvent.model_construct(
        type="response.output_item.added", item=item
    )


def code_interpreter_done(logs: str) -> ResponseOutputItemDoneEvent:
    output = OutputLogs.model_construct(type="logs", logs=logs)
    item = ResponseCodeInterpreterToolCall.model_construct(
        type="code_interpreter_call", outputs=[output]
    )
    return ResponseOutputItemDoneEvent.model_construct(type="response.output_item.done", item=item)


def error_event(message: str) -> ResponseErrorEvent:
    return ResponseErrorEvent.model_construct(type="error", message=message)


def failed_event(message: str) -> ResponseFailedEvent:
    error = ResponseError.model_construct(code="server_error", message=message)
    return ResponseFailedEvent.model_construct(
        type="response.failed", response=Response.model_construct(error=error)
    )


class FakeStream:
    """
    Stands in for openai.AsyncStream: an async iterator that is also an async
    context manager. An Exception among the events is raised when reached.
    """

    def __init__(self, events: list, delay: float) -> None:
        self.events = events
        self.delay = delay
        self.sent = 0
        self.closed = False

    async def __aenter__(self) -> "FakeStream":
        return self

    async def __aexit__(self, *exc_info: object) -> None:
        await self.close()

    async def close(self) -> None:
        self.closed = True

    async def __aiter__(self):
        for event in self.events:
            await asyncio.sleep(self.delay)  # Waits like the network would, without blocking.
            if isinstance(event, Exception):
                raise event
            self.sent += 1
            yield event


class FakeOpenAI:
    """
    Stands in for AsyncOpenAI: records the calls and answers every response
    request with `events`, one every `delay` seconds.
    """

    def __init__(self) -> None:
        self.events: list = [text_delta("Hello"), text_delta(" there")]
        self.delay = 0.0
        self.error_on_create: Exception | None = None
        self.conversations_created = 0
        self.response_calls: list[dict] = []
        self.streams: list[FakeStream] = []
        self.conversations = SimpleNamespace(create=self._create_conversation)
        self.responses = SimpleNamespace(create=self._create_response)

    async def _create_conversation(self, **kwargs: object) -> Conversation:
        self.conversations_created += 1
        return Conversation.model_construct(id=f"conv_{self.conversations_created}")

    async def _create_response(self, **kwargs: object) -> FakeStream:
        self.response_calls.append(kwargs)
        if self.error_on_create is not None:
            raise self.error_on_create
        stream = FakeStream(self.events, self.delay)
        self.streams.append(stream)
        return stream
