Dynamic MCP (text2sql)
======================

This is a minimal FastAPI-based MCP server that exposes two tools via the MCP JSON-RPC style endpoints:

- `text2sql`: generate a read-only SQL statement from a natural language query.
- `execute_generated_sql`: validate and execute the generated SQL against a read-only SQLite DB.

The server intentionally uses an in-memory preview cache (no Redis). It can use Google Gemini if configured via `GEMINI_API_KEY` / `GOOGLE_API_KEY`.

Quick start (macOS / zsh):

```bash
cd dynamic_mcp
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
# configure env
export DYNAMIC_MCP_API_KEY=your_api_key_here
export DYNAMIC_DB_PATH=/absolute/path/to/db/app.db
# run
uvicorn dynamic_mcp.server:app --host 0.0.0.0 --port 6000 --reload
```

MCP compatibility:
- GET /api/toolset returns the tool manifest similar to MCP Toolbox
- POST /mcp accepts JSON-RPC style call for `tools/call` and returns `result.content[0].text` with a JSON payload, which matches the parsing logic in `mcp_client.py` used by the app.

Security notes:
- By default, the server enforces read-only SQL (SELECT/WITH), single-statement, and an execution timeout.
- Use separate API keys and network isolation for production.
