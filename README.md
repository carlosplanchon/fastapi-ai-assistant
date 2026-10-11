# fastapi_ai_assistant

[![CI](https://github.com/carlosplanchon/fastapi-ai-assistant/actions/workflows/ci.yml/badge.svg)](https://github.com/carlosplanchon/fastapi-ai-assistant/actions/workflows/ci.yml)

This repository contains a **FastAPI** application that streams answers from an AI math tutor built on the OpenAI **[Responses API](https://developers.openai.com/api/reference/resources/responses/methods/create)** and **[Conversations API](https://developers.openai.com/api/reference/resources/conversations/methods/create)**. Tokens reach the browser in real time through Server-Sent Events (SSE), and the model can write and run Python with the built-in **Code Interpreter** tool.

> **Note:** earlier versions of this project followed the OpenAI *Assistants* QuickStart. OpenAI shut down the Assistants API on August 26, 2026, so the app now uses the Responses and Conversations APIs instead.

![Chat interface screenshot](assets/chat_screenshot.png)

---

## Overview

- **OpenAI Responses + Conversations APIs**  
  Each page load starts its own conversation, which stores the chat history on OpenAI's side. Every new message is answered with a streamed response linked to that conversation.
- **FastAPI**  
  We wrap the OpenAI calls in a FastAPI server with an SSE endpoint for real-time token streaming. It uses the async OpenAI client, so the server keeps serving other requests while a reply is being generated.
- **Streaming Chat UI**  
  A simple front-end using **Alpine.js** & **TailwindCSS** to let users enter a prompt, see the streamed tokens in real time, and stop the generation if desired.

---

## How It Works

1. **Start a Conversation**  
   On the first message, the server creates a conversation (`client.conversations.create()`) and sends its id to the browser as a `conversation` SSE event.
2. **Send a Message**  
   The browser opens `GET /chat-stream?prompt=...&conversation_id=...` and the server calls `client.responses.create(..., conversation=conversation_id, stream=True)`. OpenAI prepends the conversation history and stores the new turn automatically.
3. **Stream the Answer**  
   Each `response.output_text.delta` event is forwarded to the browser as an SSE message, followed by a final `[DONE]`.
4. **Run Code When Needed**  
   When the model uses Code Interpreter, the UI shows a tool call marker and the execution logs.

---

## Features

1. **Real-Time Streaming**  
   Uses Server-Sent Events (SSE) via [`sse_starlette`](https://github.com/sysid/sse-starlette) to stream tokens as soon as OpenAI generates them.
2. **Code Interpreter Tool**  
   Demonstrates how to enable the built-in `code_interpreter` tool for advanced math, scripting, and code execution.
3. **FastAPI + Alpine.js**  
   Lightweight Python backend with a minimal, reactive JavaScript frontend.
4. **Stop Generation**  
   A **Stop** button in the UI closes the SSE connection; the server then cancels the stream and closes its connection to OpenAI.
5. **Error Handling**  
   If OpenAI fails, the error is logged on the server, shown in the chat, and the stream ends cleanly.
6. **Access Control**  
   A username and password protect the whole app, and per-user rate limits cap what it can spend on OpenAI. See [Security](#security).
7. **Formatted Replies**  
   Replies are rendered as Markdown with LaTeX math (`$...$`, `$$...$$`, `\(...\)`, `\[...\]`) while they stream in, using [marked](https://marked.js.org/) and [KaTeX](https://katex.org/).

---

## Installation

Requires **Python 3.11+** and an OpenAI API key.

1. **Clone** the repository:
   ```bash
   git clone https://github.com/carlosplanchon/fastapi-ai-assistant.git
   cd fastapi-ai-assistant
   ```

2. **Install** dependencies with [uv](https://docs.astral.sh/uv/) (uses the versions pinned in `uv.lock`):
   ```bash
   uv sync
   ```
   or with pip:
   ```bash
   pip install .
   ```

3. **Create** your config file and set a password in its `[auth]` section (the app won't start without one):
   ```bash
   cp config.template.toml config.toml
   python -c "import secrets; print(secrets.token_urlsafe(24))"  # A good random password
   ```

4. **Set** your `OPENAI_API_KEY` environment variable:
   ```bash
   export OPENAI_API_KEY="sk-..."
   ```

---

## Usage

1. **Run the FastAPI application** from the directory that contains `config.toml`:
   ```bash
   uv run fastapi-ai-assistant
   ```
   or
   ```bash
   uv run uvicorn fastapi_ai_assistant.main:app --host 0.0.0.0 --port 8000
   ```
   If you installed with pip, run the same commands without the `uv run` prefix.

2. **Navigate** to the URL below and log in with the username and password from `config.toml`:
   ```
   http://localhost:8000/
   ```

3. **Chat** with the AI Assistant:
   - Enter a message and press **Send**.
   - Watch as the response arrives token-by-token.
   - Click **Stop** to end the generation at any time.

---

## Configuration

The app reads `config.toml` from the current directory. To use another file, set the `FASTAPI_AI_ASSISTANT_CONFIG` environment variable to its path.

- **`[app]`**
  - **`ROOT_PATH`**: leave it empty (`""`) to serve the app at `/`. When the app runs behind a reverse proxy under a sub-path (e.g. `https://example.com/chat/`), set it to that path without slashes (`"chat"`).
- **`[auth]`**
  - **`USERNAME`** and **`PASSWORD`**: the credentials the browser asks for. Both are required and must be ASCII.
- **`[limits]`** (optional; these are the defaults)
  - **`MESSAGES_PER_MINUTE = 10`** and **`MESSAGES_PER_DAY = 200`**: how many messages each user can send. Past a limit, the chat says how long to wait.
  - **`MAX_PROMPT_CHARS = 2000`**: the longest message accepted.

---

## Security

Every message costs OpenAI credits, so the app protects them:

- **Authentication**: every page and endpoint requires the HTTP Basic credentials from `config.toml`, compared in constant time. FastAPI's automatic API docs (`/docs`, `/redoc`, `/openapi.json`) are turned off, because FastAPI would serve them without credentials.
- **Rate limits**: per-user caps per minute and per day, plus a maximum message length.
- **CSRF protection**: `/chat-stream` rejects requests that other websites trigger from your browser (the browser marks them with the `Sec-Fetch-Site` header). There is no CORS middleware, since the UI is served from the same origin.
- **Safe rendering of replies**: the model's output is untrusted, so the Markdown HTML goes through [DOMPurify](https://github.com/cure53/DOMPurify) with an allowlist: no scripts, images, styles, classes or Alpine attributes, so a reply can't run code, restyle the page or send data to other sites. Math is cut out first and rendered by KaTeX, whose risky commands (`\href`, `\htmlData`, ...) stay disabled.
- **Pinned frontend libraries**: the CDN scripts load exact versions with [Subresource Integrity](https://developer.mozilla.org/en-US/docs/Web/Security/Defenses/Subresource_Integrity) hashes, so the browser rejects any modified file. The exception is the Tailwind Play CDN, which is meant for demos.

If you expose the app beyond your machine:

- **Serve it over HTTPS** (e.g. behind a reverse proxy). HTTP Basic sends the password with every request, encoded but not encrypted.
- **Run a single process.** Rate limits are kept in memory, so each process counts on its own and they reset on restart.
- **Messages travel in the URL** (`/chat-stream?prompt=...`), so they can show up in proxy or access logs.

---

## Development

Install the development tools, then run the same checks as CI:

```bash
uv sync --extra dev
uv run pytest            # Tests, with a fake OpenAI client (no API calls, no costs)
uv run ruff check        # Lint
uv run black --check .   # Formatting
uv run mypy src          # Type checks
```

GitHub Actions runs these checks on every push to `main` and on every pull request, with Python 3.11 to 3.14. It also builds the wheel and checks that it ships the chat template. See `.github/workflows/ci.yml`.

Dependabot opens weekly pull requests to update the CI actions and `uv.lock`, skipping releases younger than a week (security fixes are not delayed). It doesn't cover the frontend libraries in `index.html`: to update one, change its version and its `integrity` hash together.

### Smoke test against the real API

The tests use a fake OpenAI client, so they never check the real integration. `scripts/smoke_test.py` does: it runs the app's streaming code against the real API and checks that a conversation streams its replies, that the code interpreter runs Python, that the conversation remembers earlier messages, and that it still works after a reply is stopped halfway. It spends a little OpenAI credit, so CI doesn't run it. Run it from the folder that contains `config.toml`, especially after Dependabot updates `openai`:

```bash
export OPENAI_API_KEY="sk-..."
uv run python scripts/smoke_test.py   # ✓ or ✗ per check; exit code 1 if any fails
```

---

## Code Structure

```bash
fastapi-ai-assistant/
├── .github/
│   ├── dependabot.yml      # Weekly updates for the CI actions and uv.lock
│   └── workflows/
│       └── ci.yml          # Lint, type checks, tests and wheel build on GitHub Actions
├── src/fastapi_ai_assistant/
│   ├── main.py             # FastAPI app: SSE endpoint + Responses API streaming
│   ├── security.py         # Authentication, CSRF check and rate limits
│   ├── shared.py           # Loads config.toml
│   └── templates/
│       └── index.html      # Chat UI (Alpine.js, TailwindCSS, marked, KaTeX, DOMPurify)
├── scripts/
│   └── smoke_test.py       # Checks against the real OpenAI API (spends credit)
├── tests/                  # pytest suite, with a fake OpenAI client
├── config.template.toml    # Copy it to config.toml
├── pyproject.toml          # Project metadata and dependencies
└── uv.lock                 # Pinned dependency versions
```

- **`main.py`** implements:
  - **Conversation Management**: `await client.conversations.create()`
  - **Response Streaming**: `await client.responses.create(..., stream=True)`, consumed with `async for`.
  - **FastAPI Endpoints**:
    - `GET /`: Serves the chat UI.
    - `GET /chat-stream?prompt=...&conversation_id=...`: Streams the AI response via SSE.

- **`index.html`**:
  - A Tailwind-styled chat interface with **Alpine.js** for reactivity.
  - Subscribes to the `/chat-stream` endpoint using `EventSource` and keeps the conversation id sent by the server.
  - Displays real-time tokens as they arrive and offers a **Stop** button.
  - Renders replies as sanitized Markdown with KaTeX math (`renderMarkdown()`); your own messages are shown as plain text.

---

## Customization

- **Model and Tools**:  
  Change `MODEL` or the `tools` list in `main.py` to suit your needs.
- **Instructions**:  
  Edit `INSTRUCTIONS` in `main.py` to define the assistant's personality or domain knowledge. They are sent with every response.
- **UI/Styling**:  
  Modify `index.html` or integrate a different frontend approach.

---

## Contributing

Feel free to open issues or submit pull requests if you have suggestions or improvements.

---

## License

This project is provided under the [MIT License](https://opensource.org/licenses/MIT).  
See the [LICENSE](LICENSE) file for more details.

---

**Happy coding!** Check out the [OpenAI conversation state guide](https://developers.openai.com/api/docs/guides/conversation-state) for more information. If you have questions or run into any issues, please open an [issue](https://github.com/carlosplanchon/fastapi-ai-assistant/issues).
