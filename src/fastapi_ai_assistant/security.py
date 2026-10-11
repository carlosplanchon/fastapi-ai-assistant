#!/usr/bin/env python3

import secrets
import time
from collections import defaultdict, deque
from collections.abc import Callable
from typing import Annotated

from fastapi import Depends, HTTPException, Request, status
from fastapi.security import HTTPBasic, HTTPBasicCredentials
from starlette.datastructures import MutableHeaders
from starlette.types import ASGIApp, Message, Receive, Scope, Send

from fastapi_ai_assistant.shared import CONFIG

auth_config = CONFIG.get("auth", {})
USERNAME: str = auth_config.get("USERNAME", "")
PASSWORD: str = auth_config.get("PASSWORD", "")

# FastAPI decodes HTTP Basic credentials as ASCII, so other characters could never log in.
if not (USERNAME and PASSWORD and (USERNAME + PASSWORD).isascii()):
    raise SystemExit("config.toml: set an ASCII USERNAME and PASSWORD in the [auth] section.")

limits_config = CONFIG.get("limits", {})
MESSAGES_PER_MINUTE: int = limits_config.get("MESSAGES_PER_MINUTE", 10)
MESSAGES_PER_DAY: int = limits_config.get("MESSAGES_PER_DAY", 200)
MAX_PROMPT_CHARS: int = limits_config.get("MAX_PROMPT_CHARS", 2000)

basic_auth = HTTPBasic(realm="AI Chat")


def authenticate(credentials: Annotated[HTTPBasicCredentials, Depends(basic_auth)]) -> str:
    """
    Check the HTTP Basic credentials against config.toml and return the username.
    compare_digest takes the same time however close a wrong guess is.
    """
    is_correct_username = secrets.compare_digest(
        credentials.username.encode("utf8"), USERNAME.encode("utf8")
    )
    is_correct_password = secrets.compare_digest(
        credentials.password.encode("utf8"), PASSWORD.encode("utf8")
    )
    if not (is_correct_username and is_correct_password):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Incorrect username or password",
            headers={"WWW-Authenticate": 'Basic realm="AI Chat"'},
        )
    return credentials.username


def reject_cross_site(request: Request) -> None:
    """
    Refuse requests that another website makes the user's browser send (CSRF):
    the browser would attach the saved credentials and spend OpenAI credits.
    Browsers tag every request with Sec-Fetch-Site; other clients don't send it.
    """
    site = request.headers.get("sec-fetch-site")
    if site is not None and site not in ("same-origin", "none"):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN, detail="Cross-site requests are not allowed"
        )


class RateLimiter:
    """
    Sliding-window rate limiter: for each key, every (limit, window) rule allows
    at most `limit` hits in the last `window` seconds.
    The hits live in memory, which is enough for a single uvicorn process;
    several processes would need a shared store such as Redis.
    """

    def __init__(
        self, rules: list[tuple[int, float]], clock: Callable[[], float] = time.monotonic
    ) -> None:
        if any(limit < 1 for limit, _ in rules):
            raise ValueError("Rate limits must be at least 1")
        self.rules = rules
        self.clock = clock
        self.hits: dict[str, deque[float]] = defaultdict(deque)

    def acquire(self, key: str) -> float:
        """
        Record a hit for `key` and return 0 when every rule allows it.
        Otherwise record nothing and return the seconds to wait before retrying.
        """
        now = self.clock()
        hits = self.hits[key]
        longest_window = max(window for _, window in self.rules)
        while hits and hits[0] <= now - longest_window:
            hits.popleft()

        wait = 0.0
        for limit, window in self.rules:
            recent = [hit for hit in hits if hit > now - window]
            if len(recent) >= limit:
                # A slot frees up when the oldest of the last `limit` hits leaves the window.
                wait = max(wait, recent[-limit] + window - now)

        if wait == 0:
            hits.append(now)
        return wait


rate_limiter = RateLimiter([(MESSAGES_PER_MINUTE, 60), (MESSAGES_PER_DAY, 24 * 60 * 60)])

# Sent with every response. The chat page replaces the Content-Security-Policy
# with a stricter one that lets its own scripts run (see page_policy).
SECURITY_HEADERS = {
    "Content-Security-Policy": "default-src 'none'; frame-ancestors 'none'",
    "X-Frame-Options": "DENY",  # What older browsers understand of frame-ancestors 'none'
    "X-Content-Type-Options": "nosniff",
    "Referrer-Policy": "no-referrer",
    "Cross-Origin-Opener-Policy": "same-origin",
}


class SecurityHeaders:
    """
    Adds SECURITY_HEADERS to every response that doesn't set them already.
    A pure ASGI middleware: unlike BaseHTTPMiddleware, it leaves streaming responses
    and the detection of disconnected clients (the Stop button) untouched.
    """

    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        async def send_with_headers(message: Message) -> None:
            if message["type"] == "http.response.start":
                headers = MutableHeaders(scope=message)
                for name, value in SECURITY_HEADERS.items():
                    headers.setdefault(name, value)
            await send(message)

        await self.app(scope, receive, send_with_headers)


def page_policy(nonce: str) -> str:
    """
    Content Security Policy of the chat page: only the scripts and styles that carry
    this response's nonce run, so markup that slips into the page can't execute anything.
    """
    return "; ".join(
        [
            "default-src 'none'",
            f"script-src 'nonce-{nonce}'",
            f"style-src-elem 'nonce-{nonce}'",
            "style-src-attr 'unsafe-inline'",  # KaTeX lays out formulas with style attributes
            "font-src https://cdn.jsdelivr.net",  # KaTeX fonts
            "connect-src 'self'",
            "img-src 'self'",
            "base-uri 'none'",
            "form-action 'self'",
            "frame-ancestors 'none'",
        ]
    )
