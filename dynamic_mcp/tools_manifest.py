# dynamic_mcp/tools_manifest.py

# Simple manifest describing the two tools exposed by the dynamic MCP
TOOLSET = {
    "text2sql": {
        "name": "text2sql",
        "description": "Generate a read-only SQL statement from a natural language question.",
        "parameters": [
            {"name": "natural_language_query", "type": "string", "description": "The user's question"},
            {"name": "schema", "type": "string", "description": "Optional DB schema snapshot", "required": False},
            {"name": "max_results", "type": "integer", "description": "Advisory maximum number of results", "required": False}
        ]
    },
    "execute_generated_sql": {
        "name": "execute_generated_sql",
        "description": "Validate and execute a generated read-only SQL statement on the read-only DB.",
        "parameters": [
            {"name": "preview_id", "type": "string", "description": "Preview id returned by text2sql", "required": False},
            {"name": "sql", "type": "string", "description": "Raw SQL to execute (alternative to preview_id)", "required": False},
            {"name": "params", "type": "array", "description": "Optional parameters for the SQL", "required": False},
            {"name": "max_results", "type": "integer", "description": "Maximum rows to return (server enforces cap)", "required": False},
            {"name": "mode", "type": "string", "description": "preview or execute", "required": False}
        ]
    }
}
