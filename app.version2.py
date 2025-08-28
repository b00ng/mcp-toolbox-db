from flask import Flask, render_template, request, jsonify, Response
import asyncio
import os
import json
from dotenv import load_dotenv
from flask_cors import CORS
from mcp_client import MCPClient
from datetime import datetime, timezone
from dateutil.relativedelta import relativedelta
from datetime import datetime, timezone, timedelta
import calendar
from google import genai
from google.genai import types
from dynamic_sql_handler import DynamicSQLHandler

load_dotenv()

# LLM client (used as fallback generator)
client = genai.Client(api_key=os.getenv('GEMINI_API_KEY') or os.getenv('GOOGLE_API_KEY'))

# DB path
DB_PATH = os.getenv('DB_PATH', '/Users/khangvt/projects/mcp-toolbox-db/db/app.db')

# Primary MCP (for safe, predefined tools)
mcp_client = MCPClient(os.getenv('MCP_TOOLBOX_URL', 'http://127.0.0.1:5000'))

# Optional dynamic MCP (for text->SQL generation & execution)
MCP_DYNAMIC_URL = os.getenv('MCP_DYNAMIC_URL')
mcp_dynamic = MCPClient(MCP_DYNAMIC_URL) if MCP_DYNAMIC_URL else None

# Local fallback dynamic SQL handler (uses local LLM directly)
dynamic_sql_handler = DynamicSQLHandler(DB_PATH, client, dynamic_mcp_client=mcp_dynamic)

app = Flask(__name__)
CORS(app)
app.config['SECRET_KEY'] = 'your-secret-key'

model_name = 'gemini-2.5-flash'

# Utility / helper functions (copied from app.py)
def iso_utc(dt: datetime) -> str:
    return dt.replace(tzinfo=timezone.utc).isoformat().replace('+00:00', 'Z')

def parse_iso(s: str) -> datetime:
    s = s.strip()
    if s.endswith('Z'):
        s = s[:-1] + '+00:00'
    return datetime.fromisoformat(s)

def first_day_of_month(dt: datetime) -> datetime:
    return datetime(dt.year, dt.month, 1, tzinfo=timezone.utc)

def last_day_of_month(dt: datetime) -> datetime:
    last_day = calendar.monthrange(dt.year, dt.month)[1]
    return datetime(dt.year, dt.month, last_day, 23, 59, 59, tzinfo=timezone.utc)

def add_months(dt: datetime, k: int) -> datetime:
    return dt + relativedelta(months=k)

def default_trailing_12_months():
    now = datetime.now(timezone.utc)
    end_m = last_day_of_month(now)
    start_m = first_day_of_month(add_months(now, -11))
    return iso_utc(start_m), iso_utc(end_m)

def summarize_series_xy(data_xy):
    if not data_xy:
        return 'No data available for the selected period.'
    total = sum(p['y'] for p in data_xy)
    peak = max(data_xy, key=lambda p: p['y'])
    trough = min(data_xy, key=lambda p: p['y'])
    months = len(data_xy)
    trend = ''
    if months >= 6:
        mid = months // 2
        first_half = sum(p['y'] for p in data_xy[:mid])
        second_half = sum(p['y'] for p in data_xy[mid:])
        if first_half > 0:
            delta = (second_half - first_half) / first_half
            trend = f"Trend: {'up' if delta >= 0 else 'down'} {abs(delta)*100:.1f}% over the last half-period. "
    return (
        f"Total {total} cents over {months} months. "
        f"Peak at {peak['x']} with {peak['y']} cents; "
        f"lowest at {trough['x']} with {trough['y']} cents. "
        f"{trend}".strip()
    )

def month_range(start_iso: str, end_iso: str):
    start = first_day_of_month(parse_iso(start_iso))
    end = first_day_of_month(parse_iso(end_iso))
    months = []
    cur = start
    while cur <= end:
        months.append(iso_utc(cur))
        cur = add_months(cur, 1)
    return months


def run_async(coro):
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    try:
        return loop.run_until_complete(coro)
    finally:
        loop.close()


def normalize_sales_by_month(raw_rows, start_iso: str, end_iso: str):
    by_month = {}
    for r in raw_rows:
        ym = r.get('ym')
        if not ym:
            continue
        ms = f"{ym}-01T00:00:00Z"
        val = int(r.get('total_cents') or 0)
        by_month[ms] = val
    data = []
    for ms in month_range(start_iso, end_iso):
        data.append({'x': ms, 'y': by_month.get(ms, 0)})
    return data


def mcp_tools_to_gemini_functions(mcp_tools_dict):
    functions = []
    for name, tool_info in mcp_tools_dict.items():
        properties = {}
        required = []
        for param in tool_info.get('parameters', []):
            param_name = param.get('name')
            param_type = param.get('type', 'string')
            type_mapping = {'string': 'string', 'integer': 'number', 'number': 'number', 'boolean': 'boolean'}
            properties[param_name] = {
                'type': type_mapping.get(param_type, 'string'),
                'description': param.get('description', f'Parameter {param_name}')
            }
            if param.get('required', True):
                required.append(param_name)
        function_def = {
            'name': name,
            'description': tool_info.get('description', f'Database tool: {name}'),
            'parameters': {'type': 'object', 'properties': properties, 'required': required}
        }
        functions.append(function_def)
    return functions

# Load tools on startup
try:
    print('Loading MCP tools from primary MCP...')
    run_async(mcp_client.load_tools())
    available_tools = mcp_client.get_available_tools()
    print(f'Primary MCP available tools: {available_tools}')
    if not available_tools:
        print('Warning: No tools loaded from primary MCP server')

    if mcp_dynamic:
        try:
            print('Loading tools from dynamic MCP (text2sql)...')
            run_async(mcp_dynamic.load_tools())
            print(f'Dynamic MCP available tools: {mcp_dynamic.get_available_tools()}')
        except Exception as e:
            print(f'Warning: failed to load tools from dynamic MCP: {e}')
except Exception as e:
    print(f'Error during startup: {e}')


@app.route('/')
def index():
    return render_template('index.html')


@app.route('/api/chat', methods=['POST'])
def chat():
    try:
        body = request.get_json(force=True) or {}
        user_message = body.get('message', '')
        # Test hook: allow bypassing the LLM by specifying a tool and args directly
        force_tool = body.get('force_tool')
        force_args = body.get('force_args') or {}
        if not user_message:
            return jsonify({'error': 'Message is required'}), 400

        available_tools = mcp_client.get_available_tools()
        if not available_tools:
            resp = client.models.generate_content(model=model_name, contents=[user_message])
            return jsonify({'response': getattr(resp, 'text', 'Tools unavailable.')})

        tools_info = {name: mcp_client.get_tool_info(name) for name in available_tools}
        gemini_functions = mcp_tools_to_gemini_functions(tools_info)
        tool = types.Tool(function_declarations=gemini_functions)

        config = types.GenerateContentConfig(
            system_instruction=(
                'You are a helpful and precise e-commerce database assistant. Your primary function is to use the available tools to answer user questions about customers, products, and orders.\n'
                'Carefully analyze the user\'s request to select the most appropriate tool and parameters.\n\n'
                '- For **sales volume over time**, use the `sales_by_month` tool.\n'
                "- For complex or ad-hoc questions not covered by specific tools, you MUST use `execute_dynamic_sql`. Pass the user's full question as the `natural_language_query` parameter.\n"
                '- For `sales_by_month`, if dates are missing, use the trailing 12 months.\n'
                '- Always respond based on the tool\'s output.'
            ),
            tools=[tool],
            tool_config=types.ToolConfig(function_calling_config=types.FunctionCallingConfig(mode='ANY')),
        )

        # If test hook provided, skip calling Gemini and use forced tool
        if force_tool:
            fcalls = []
            # create a lightweight object similar to a function_call
            class FCall:
                def __init__(self, name, args):
                    self.name = name
                    self.args = args
            fcalls = [FCall(force_tool, force_args)]
        else:
            resp = client.models.generate_content(model=model_name, contents=[user_message], config=config)
            fcalls = getattr(resp, 'function_calls', [])

        if fcalls:
            function_call = fcalls[0]
            tool_name = function_call.name
            args = dict(function_call.args) if function_call.args else {}

            # Route execute_dynamic_sql to the dynamic MCP if available
            if tool_name == 'execute_dynamic_sql':
                natural_query = args.get('natural_language_query')
                max_res = args.get('max_results', 100)

                # Prefer remote dynamic MCP
                if mcp_dynamic:
                    # 1) text2sql to get preview or SQL
                    payload = {'natural_language_query': natural_query, 'max_results': max_res}
                    text2sql_result = run_async(mcp_dynamic.invoke_tool('text2sql', payload))

                    if isinstance(text2sql_result, dict) and 'error' in text2sql_result:
                        return jsonify({'type': 'error', 'summary': f"text2sql failed: {text2sql_result.get('error')}"}), 500

                    results = text2sql_result.get('results', text2sql_result)
                    if isinstance(results, list) and results:
                        first = results[0]
                        if isinstance(first, dict) and first.get('preview_id'):
                            preview_id = first.get('preview_id')
                            exec_payload = {'preview_id': preview_id, 'max_results': max_res}
                        else:
                            exec_payload = {'sql': first.get('sql'), 'params': first.get('params', []), 'max_results': max_res}
                    else:
                        return jsonify({'type': 'error', 'summary': 'text2sql returned no usable result.'}), 500

                    exec_res = run_async(mcp_dynamic.invoke_tool('execute_generated_sql', exec_payload))
                    if isinstance(exec_res, dict) and 'error' in exec_res:
                        return jsonify({'type': 'error', 'summary': f"execute_generated_sql failed: {exec_res.get('error')}"}), 500

                    exec_results = exec_res.get('results', exec_res)
                    # exec_results often is a list with a dict payload
                    if isinstance(exec_results, list) and exec_results:
                        payload = exec_results[0]
                    elif isinstance(exec_results, dict):
                        payload = exec_results
                    else:
                        payload = exec_res

                    # If payload contains error
                    if isinstance(payload, dict) and payload.get('error'):
                        return jsonify({'type': 'error', 'summary': payload.get('message') or payload.get('error')}), 500

                    # Success path: return rows summary
                    rows = payload.get('results', []) if isinstance(payload, dict) else []
                    summary = (
                        f"Dynamic query for: \"{natural_query}\"\n\n"
                        f"Generated SQL (preview_id: {payload.get('preview_id') or exec_payload.get('preview_id', '')}):\n{payload.get('executed_sql') or payload.get('sql') or ''}\n\n"
                        f"Results ({len(rows)} rows):\n{json.dumps(rows, indent=2)}"
                    )
                    return jsonify({'type': 'text', 'summary': summary})

                # Fallback: use local handler
                result_payload = dynamic_sql_handler.execute_query(natural_query, max_res)
                if result_payload['status'] == 'success':
                    summary = (
                        f"Dynamic query for: \"{natural_query}\"\n\n"
                        f"Generated SQL:\n{result_payload['generated_sql']}\n\n"
                        f"Results ({len(result_payload['results'])} rows):\n"
                        f"{json.dumps(result_payload['results'], indent=2)}"
                    )
                else:
                    summary = (
                        f"Failed dynamic query for: \"{natural_query}\"\n\n"
                        f"Error: {result_payload['error']}"
                    )
                return jsonify({"type": "text", "summary": summary})

            # Special handling for sales_by_month (same as original)
            if tool_name == 'sales_by_month':
                if 'start_date' not in args or 'end_date' not in args:
                    start_date, end_date = default_trailing_12_months()
                    args['start_date'], args['end_date'] = start_date, end_date

                tool_result = run_async(mcp_client.invoke_tool(tool_name, args))

                if isinstance(tool_result, dict) and 'error' in tool_result:
                    error_details = tool_result.get('error', {})
                    error_message = error_details.get('message', str(error_details))
                    return jsonify({"type": "error", "summary": f"Tool '{tool_name}' failed: {error_message}"}), 500

                rows = tool_result.get('results', [])
                data_xy = normalize_sales_by_month(rows, args['start_date'], args['end_date'])

                return jsonify({
                    "type": "chart",
                    "metric": "sales_by_month",
                    "spec": {
                        "kind": "bar", "xField": "month_start", "yField": "total_cents",
                        "unit": "month", "currency": args.get('currency', 'VND'), "title": "Total sales by month"
                    },
                    "data": data_xy,
                    "summary": summarize_series_xy(data_xy),
                    "tool_used": tool_name,
                    "function_args": args,
                })

            # Generic handling for other tools (call primary MCP)
            else:
                tool_result = run_async(mcp_client.invoke_tool(tool_name, args))

                if isinstance(tool_result, dict) and 'error' in tool_result:
                    error_details = tool_result.get('error', {})
                    error_message = error_details.get('message', str(error_details))
                    return jsonify({"type": "error", "summary": f"Tool '{tool_name}' failed: {error_message}"}), 500

                results = tool_result.get('results', tool_result)
                summary = (
                    f"Tool '{tool_name}' executed successfully.\n\n"
                    f"Arguments: {json.dumps(args, indent=2)}\n\n"
                    f"Result:\n{json.dumps(results, indent=2)}"
                )

                return jsonify({
                    "type": "text",
                    "summary": summary,
                    "tool_used": tool_name,
                })

        # Fallback for no tool call
        text_response = getattr(resp, 'text', 'I could not process your request with a tool.')
        return jsonify({"type": "text", "summary": text_response})

    except Exception as e:
        import traceback
        traceback.print_exc()
        return jsonify({'error': f'Chat error: {str(e)}'}), 500


@app.route('/api/tools')
def get_tools():
    tools_info = {name: mcp_client.get_tool_info(name) for name in mcp_client.get_available_tools()}
    gemini_functions = mcp_tools_to_gemini_functions(tools_info) if tools_info else []
    return jsonify({
        'available_tools': mcp_client.get_available_tools(),
        'mcp_tools_info': tools_info,
        'gemini_functions': gemini_functions
    })


@app.route('/debug/mcp')
def debug_mcp():
    try:
        run_async(mcp_client.load_tools())
        return jsonify({
            'mcp_url': os.getenv('MCP_TOOLBOX_URL'),
            'mcp_dynamic_url': MCP_DYNAMIC_URL,
            'available_tools': mcp_client.get_available_tools(),
            'tools_details': {name: mcp_client.get_tool_info(name) for name in mcp_client.get_available_tools()},
            'dynamic_tools': mcp_dynamic.get_available_tools() if mcp_dynamic else [],
            'status': 'connected' if mcp_client.get_available_tools() else 'no_tools'
        })
    except Exception as e:
        return jsonify({'error': str(e), 'mcp_url': os.getenv('MCP_TOOLBOX_URL'), 'status': 'error'})


if __name__ == '__main__':
    app.run(debug=True, port=int(os.getenv('PORT', '5009')))
