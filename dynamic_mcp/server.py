# dynamic_mcp/server.py

import os
import re
import json
import time
import threading
import sqlite3
import uuid
import traceback
from typing import Optional, Dict, Any

try:
    from fastapi import FastAPI, Request, HTTPException
    from fastapi.responses import JSONResponse
except Exception:
    # fastapi may not be installed in the environment where this file is inspected; imports are required at runtime
    pass

from .tools_manifest import TOOLSET
from .preview_cache import PreviewCache
from dotenv import load_dotenv

# Load env file from the dynamic_mcp directory (if present)
env_path = os.path.join(os.path.dirname(__file__), '.env')
load_dotenv(env_path)

# Optional LLM client (google.genai) - if not available server will use a simple stub generator
try:
    from google import genai
    LLM_AVAILABLE = True
except Exception:
    genai = None
    LLM_AVAILABLE = False

API_KEY = os.getenv('DYNAMIC_MCP_API_KEY')
# Resolve DB path: allow relative paths in .env to be relative to this package directory
_db_env = os.getenv('DYNAMIC_DB_PATH')
if _db_env:
    if os.path.isabs(_db_env):
        DB_PATH = _db_env
    else:
        DB_PATH = os.path.normpath(os.path.join(os.path.dirname(__file__), _db_env))
else:
    DB_PATH = os.path.normpath(os.path.join(os.path.dirname(__file__), '..', 'db', 'app.db'))

print(f"[dynamic_mcp] Using DB_PATH={DB_PATH}")
MAX_ROWS = int(os.getenv('DYNAMIC_MAX_ROWS', '500'))
QUERY_TIMEOUT_SECONDS = int(os.getenv('DYNAMIC_QUERY_TIMEOUT', '8'))

app = FastAPI()
preview_cache = PreviewCache(ttl_seconds=3600)

# Lightweight SQL validation helpers
FORBIDDEN_KEYWORDS = ['insert', 'update', 'delete', 'drop', 'alter', 'create', 'truncate', 'attach', 'pragma']


def _require_api_key(request: Request):
    if not API_KEY:
        return  # no auth configured
    header = request.headers.get('x-api-key') or request.headers.get('X-API-Key')
    if not header or header != API_KEY:
        raise HTTPException(status_code=401, detail='Invalid API Key')


def _is_read_only_sql(sql: str) -> bool:
    s = sql.strip()
    # Remove trailing semicolons for validation, but disallow internal semicolons
    s_no_trailing = re.sub(r';+$', '', s).strip().lower()
    if ';' in s_no_trailing:
        return False
    if not (s_no_trailing.startswith('select') or s_no_trailing.startswith('with')):
        return False
    for k in FORBIDDEN_KEYWORDS:
        if re.search(r'\b' + re.escape(k) + r'\b', s_no_trailing):
            return False
    return True


def _enforce_limit(sql: str, max_results: int) -> str:
    s = sql.strip()
    lower = s.lower()
    if ' limit ' in lower:
        return s
    return f"SELECT * FROM ({s}) AS _sub LIMIT {int(max_results)}"


def _generate_sql_with_llm(natural_query: str, schema: Optional[str] = None) -> Dict[str, Any]:
    # Prefer a real LLM client if available
    if LLM_AVAILABLE and genai is not None:
        client = genai.Client(api_key=os.getenv('GEMINI_API_KEY') or os.getenv('GOOGLE_API_KEY'))
        prompt = ("You are an expert SQL developer. Return a single, syntactically correct SQLite SELECT or WITH query\n"
                  "encoded as JSON with fields: sql (string), params (array).\n"
                  "Do NOT return any additional text. Example output: {\"sql\": \"SELECT ...\", \"params\": []}\n\n"
                  "Schema:\n" + (schema or "<no schema provided>") + "\n\nQuestion: " + natural_query)
        try:
            resp = client.models.generate_content(model='gemini-1.5-flash', contents=[prompt])
            text = getattr(resp, 'text', None) or str(resp)
            # Try to parse JSON from LLM
            try:
                parsed = json.loads(text)
                return parsed
            except Exception:
                # If LLM returned raw SQL string, wrap into json
                return {"sql": text, "params": []}
        except Exception:
            traceback.print_exc()
            return {"sql": "", "params": [], "error": "llm_failed"}
    else:
        # Stub generator: simple heuristics for common patterns
        q = natural_query.lower()
        if 'top' in q and 'customer' in q and ('sales' in q or 'revenue' in q):
            sql = "SELECT customers.id AS customer_id, customers.name, SUM(order_items.quantity*order_items.price_cents) AS total_cents FROM customers JOIN orders ON orders.customer_id = customers.id JOIN order_items ON order_items.order_id = orders.id GROUP BY customers.id, customers.name ORDER BY total_cents DESC"
            return {"sql": sql, "params": []}
        # fallback
        return {"sql": "SELECT 1 AS ok", "params": []}


@app.get('/api/toolset')
async def api_toolset():
    # Return a toolset manifest compatible with the MCP client's expectations
    return JSONResponse(content={"tools": TOOLSET})


@app.post('/mcp')
async def mcp_rpc(request: Request):
    body = await request.json()
    # Optional API key check
    _require_api_key(request)

    # Expecting JSON-RPC structure with params.method == 'tools/call'
    try:
        method = body.get('method')
        params = body.get('params') or {}
        if method != 'tools/call':
            return JSONResponse(status_code=400, content={"error": "unsupported method"})

        name = params.get('name')
        arguments = params.get('arguments') or {}

        if not name:
            return JSONResponse(status_code=400, content={"error": "tool name required"})

        # Dispatch tools
        if name == 'text2sql':
            natural = arguments.get('natural_language_query') or arguments.get('query') or ''
            schema = arguments.get('schema')
            max_results = int(arguments.get('max_results', 100))
            result = _generate_sql_with_llm(natural, schema)

            # Basic validation
            sql = result.get('sql', '') if isinstance(result, dict) else ''
            params_out = result.get('params', []) if isinstance(result, dict) else []

            if not sql:
                payload = {"type": "error", "error": "no_sql_generated"}
            else:
                preview_id = 'p_' + uuid.uuid4().hex[:10]
                preview_cache.set(preview_id, {"sql": sql, "params": params_out, "meta": {"natural": natural}})
                payload = {"sql": sql, "params": params_out, "preview_id": preview_id}

            return JSONResponse(content={"result": {"content": [{"type": "text", "text": json.dumps(payload)}]}})

        elif name == 'execute_generated_sql':
            preview_id = arguments.get('preview_id')
            sql = arguments.get('sql')
            params_in = arguments.get('params') or []
            max_results = int(arguments.get('max_results', 100))
            mode = arguments.get('mode', 'execute')

            # Resolve preview
            if preview_id and not sql:
                cached = preview_cache.get(preview_id)
                if not cached:
                    payload = {"error": "preview_not_found"}
                    return JSONResponse(content={"result": {"content": [{"type": "text", "text": json.dumps(payload)}]}})
                sql = cached.get('sql')
                params_in = cached.get('params', [])

            if not sql:
                payload = {"error": "no_sql_provided"}
                return JSONResponse(content={"result": {"content": [{"type": "text", "text": json.dumps(payload)}]}})

            # Validate SQL
            if not _is_read_only_sql(sql):
                payload = {"error": "validation_failed", "message": "only single read-only SELECT/WITH statements are allowed"}
                return JSONResponse(content={"result": {"content": [{"type": "text", "text": json.dumps(payload)}]}})

            # If mode == preview, return EXPLAIN or the final wrapped SQL
            if mode == 'preview':
                payload = {"executed_sql": _enforce_limit(sql, min(max_results, MAX_ROWS)), "params": params_in}
                return JSONResponse(content={"result": {"content": [{"type": "text", "text": json.dumps(payload)}]}})

            # Execute
            final_sql = _enforce_limit(sql, min(max_results, MAX_ROWS))
            start = time.time()
            try:
                # Run the query with a timeout by using a thread
                rows = []
                err = None

                def run_query():
                    nonlocal rows, err
                    try:
                        conn = sqlite3.connect(f'file:{DB_PATH}?mode=ro', uri=True, check_same_thread=False)
                        conn.row_factory = sqlite3.Row
                        try:
                            conn.execute('PRAGMA query_only = ON')
                        except Exception:
                            pass
                        cur = conn.cursor()
                        cur.execute(final_sql, params_in or [])
                        fetched = cur.fetchmany(min(MAX_ROWS, max_results))
                        rows = [dict(r) for r in fetched]
                        conn.close()
                    except Exception as e:
                        err = str(e)

                t = threading.Thread(target=run_query)
                t.start()
                t.join(QUERY_TIMEOUT_SECONDS)
                if t.is_alive():
                    payload = {"error": "timeout", "message": f"Query exceeded {QUERY_TIMEOUT_SECONDS}s"}
                    return JSONResponse(content={"result": {"content": [{"type": "text", "text": json.dumps(payload)}]}})
                if err:
                    payload = {"error": "execution_failed", "message": err}
                    return JSONResponse(content={"result": {"content": [{"type": "text", "text": json.dumps(payload)}]}})

                elapsed = int((time.time() - start) * 1000)
                payload = {"executed_sql": final_sql, "results": rows, "row_count": len(rows), "timing_ms": elapsed}
                return JSONResponse(content={"result": {"content": [{"type": "text", "text": json.dumps(payload)}]}})

            except Exception as e:
                traceback.print_exc()
                payload = {"error": "execution_exception", "message": str(e)}
                return JSONResponse(content={"result": {"content": [{"type": "text", "text": json.dumps(payload)}]}})

        else:
            return JSONResponse(status_code=404, content={"error": "tool not found"})

    except Exception as e:
        traceback.print_exc()
        return JSONResponse(status_code=500, content={"error": str(e)})
