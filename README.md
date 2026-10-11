# fastapi_ai_assistant

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

3. **Create** your config file:
   ```bash
   cp config.template.toml config.toml
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

2. **Navigate** to:
   ```
   http://localhost:8000/
   ```

3. **Chat** with the AI Assistant:
   - Enter a message and press **Send**.
   - Watch as the response arrives token-by-token.
   - Click **Stop** to end the generation at any time.

---

## Configuration

`config.toml` has a single setting:

- **`ROOT_PATH`**: leave it empty (`""`) to serve the app at `/`. When the app runs behind a reverse proxy under a sub-path (e.g. `https://example.com/chat/`), set it to that path without slashes (`"chat"`).

---

## Code Structure

```bash
fastapi-ai-assistant/
├── src/fastapi_ai_assistant/
│   ├── main.py             # FastAPI app: SSE endpoint + Responses API streaming
│   ├── shared.py           # Loads config.toml
│   └── templates/
│       └── index.html      # Chat UI (HTML, Alpine.js, TailwindCSS)
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
