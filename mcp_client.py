import httpx
import json
import asyncio
from typing import Dict, List, Any

class MCPClient:
    def __init__(self, base_url: str):
        self.base_url = base_url.rstrip('/')
        self.tools = {}

    async def load_tools(self):
        """Load available tools from MCP Toolbox server"""
        try:
            async with httpx.AsyncClient() as client:
                resp = await client.get(f"{self.base_url}/api/toolset")
                resp.raise_for_status()
                data = resp.json()
                
                print(f"Server response keys: {list(data.keys())}")
                
                # Handle the actual response format from MCP Toolbox
                if 'tools' in data:
                    tools_data = data['tools']
                    
                    # Check if tools is a dict (object) or list
                    if isinstance(tools_data, dict):
                        # Format: {"tools": {"tool_name": {...}, ...}}
                        for tool_name, tool_info in tools_data.items():
                            # Add the tool name to the tool info
                            tool_info['name'] = tool_name
                            self.tools[tool_name] = tool_info
                            print(f"  - Loaded tool: {tool_name}")
                    elif isinstance(tools_data, list):
                        # Format: {"tools": [{"name": "tool_name", ...}, ...]}
                        for tool in tools_data:
                            if isinstance(tool, dict) and 'name' in tool:
                                self.tools[tool['name']] = tool
                                print(f"  - Loaded tool: {tool['name']}")
                
                print(f"✓ Loaded {len(self.tools)} tools: {list(self.tools.keys())}")
                
        except Exception as e:
            print(f"Error loading tools: {e}")
            import traceback
            traceback.print_exc()

    async def invoke_tool(self, tool_name: str, params: Dict[str, Any]) -> Dict[str, Any]:
        """Invoke a specific tool using MCP JSON-RPC protocol"""
        try:
            async with httpx.AsyncClient(timeout=30.0) as client:
                payload = {
                    "jsonrpc": "2.0",
                    "id": 1,
                    "method": "tools/call",
                    "params": {
                        "name": tool_name,
                        "arguments": params or {}
                    }
                }
                
                print(f"Invoking tool {tool_name} with MCP payload: {payload}")
                url = f"{self.base_url}/mcp"
                resp = await client.post(url, json=payload)
                
                if resp.status_code == 200:
                    result = resp.json()
                    print(f"✓ MCP tool invocation successful")
                    print(f"Raw MCP result: {result}")
                    
                    # Parse MCP JSON-RPC response format
                    if "result" in result:
                        mcp_result = result["result"]
                        
                        # Handle content array format (most common for MCP Toolbox)
                        if isinstance(mcp_result, dict) and "content" in mcp_result:
                            content = mcp_result["content"]
                            if isinstance(content, list):
                                parsed_results = []
                                
                                for item in content:
                                    if isinstance(item, dict) and item.get("type") == "text" and "text" in item:
                                        text_content = item["text"]
                                        try:
                                            # Try to parse JSON strings into objects
                                            parsed_json = json.loads(text_content)
                                            parsed_results.append(parsed_json)
                                        except json.JSONDecodeError:
                                            # If not JSON, keep as text
                                            parsed_results.append(text_content)
                                    else:
                                        parsed_results.append(item)
                                
                                print(f"Parsed results: {parsed_results}")
                                return {"results": parsed_results}
                            else:
                                return {"results": content}
                        
                        # Handle direct result objects
                        elif isinstance(mcp_result, list):
                            return {"results": mcp_result}
                        else:
                            return mcp_result
                    else:
                        return result
                else:
                    error_msg = f"HTTP {resp.status_code}: {resp.text}"
                    print(f"MCP invocation failed: {error_msg}")
                    return {"error": error_msg}
                    
        except Exception as e:
            error_msg = f"MCP tool invocation error: {e}"
            print(error_msg)
            return {"error": error_msg}

    def get_tool_info(self, tool_name: str) -> Dict[str, Any]:
        """Get tool information including parameters"""
        return self.tools.get(tool_name, {})

    def get_available_tools(self) -> List[str]:
        """Get list of available tool names"""
        return list(self.tools.keys())
