#!/usr/bin/env python3
"""
Smoke test against the REAL OpenAI API, so it spends a little credit.

It runs the app's own streaming code (stream_reply) through four checks:
1. A first message starts a conversation and its reply streams in.
2. The model runs Python with the code interpreter.
3. A follow-up message remembers the first one.
4. After a reply is stopped halfway (like the Stop button), the conversation still works.

Run it from the folder that contains config.toml:
    export OPENAI_API_KEY="sk-..."
    uv run python scripts/smoke_test.py
"""

import asyncio
import logging
import os
import sys
import time
from contextlib import aclosing
from dataclasses import dataclass

if not os.environ.get("OPENAI_API_KEY"):
    sys.exit("Set OPENAI_API_KEY first: this script talks to the real OpenAI API.")

# Errors inside stream_reply are logged; show them with their traceback.
logging.basicConfig(level=logging.WARNING, format="%(levelname)s %(name)s: %(message)s")

from fastapi_ai_assistant.main import MODEL, stream_reply  # noqa: E402

TIMEOUT_S = 120
CODE_WORD = "7319"


@dataclass
class Reply:
    conversation_id: str | None
    text: str = ""
    chunks: int = 0
    first_token_s: float | None = None
    timed_out: bool = False

    @property
    def failed(self) -> bool:
        return self.timed_out or "[Error:" in self.text

    def summary(self) -> str:
        text = self.text.strip().replace("\n", " ⏎ ")
        text = text if len(text) <= 150 else text[:150] + "…"
        first_token = "-" if self.first_token_s is None else f"{self.first_token_s:.1f} s"
        timeout = f", timed out after {TIMEOUT_S} s" if self.timed_out else ""
        return f"{self.chunks} chunk(s), first token after {first_token}{timeout}: {text!r}"


async def ask(prompt: str, conversation_id: str | None, stop_after_chunks: int = 0) -> Reply:
    """
    Send one message through stream_reply and collect what the browser would receive.
    With stop_after_chunks, stop reading after that many chunks, like the Stop button.
    """
    reply = Reply(conversation_id)
    start = time.monotonic()
    try:
        async with asyncio.timeout(TIMEOUT_S):
            # aclosing() closes the generator, and with it the connection to OpenAI,
            # also when we stop early: the same thing happens when a browser disconnects.
            async with aclosing(stream_reply(prompt, conversation_id)) as events:
                async for event in events:
                    if event.get("event") == "conversation":
                        reply.conversation_id = event["data"]
                    elif event["data"] != "[DONE]":
                        if reply.first_token_s is None:
                            reply.first_token_s = time.monotonic() - start
                        reply.text += event["data"]
                        reply.chunks += 1
                        if reply.chunks == stop_after_chunks:
                            break
    except TimeoutError:
        reply.timed_out = True
    return reply


async def run() -> bool:
    print(f"Smoke test against the real OpenAI API, model {MODEL}\n")
    results = []

    def check(name: str, passed: bool, reply: Reply) -> None:
        results.append(passed)
        print(f"{'✓' if passed else '✗'} {name}\n    {reply.summary()}\n")

    first = await ask(f"Remember this code: {CODE_WORD}. Reply only with 'ok'.", None)
    check(
        "1. A new conversation streams its reply",
        first.conversation_id is not None and first.chunks > 0 and not first.failed,
        first,
    )
    if first.conversation_id is None or first.failed:
        print("Stopping here: fix this first (see the error logged above, if any).")
        return False
    print(f"    Conversation: {first.conversation_id}\n")

    code = await ask(
        "Use Python to compute the square root of 2 with 30 decimal places and print it.",
        first.conversation_id,
    )
    check(
        "2. The code interpreter runs Python",
        "[Tool Call Created: code_interpreter]" in code.text and not code.failed,
        code,
    )

    memory = await ask(
        "What was the code I asked you to remember? Reply only with the number.",
        first.conversation_id,
    )
    check("3. The conversation remembers earlier messages", CODE_WORD in memory.text, memory)

    await ask("Count from 1 to 300, one number per line.", first.conversation_id, 5)
    after_stop = await ask("Are you still there? Reply only with 'yes'.", first.conversation_id)
    check(
        "4. The conversation still works after a reply is stopped halfway",
        after_stop.chunks > 0 and not after_stop.failed,
        after_stop,
    )

    print(f"{sum(results)}/{len(results)} checks passed.")
    return all(results)


if __name__ == "__main__":
    sys.exit(0 if asyncio.run(run()) else 1)
