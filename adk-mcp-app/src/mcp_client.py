import httpx
import json
import asyncio
from typing import Dict, List, Any, Optional

class MCPClient:
    """Enhanced MCP Client for ADK integration"""
    
    def __init__(self, base_url: str, name: str = "mcp_client"):
        self.base_url = base_url.rstrip('/')
        self.name = name
        self.tools = {}
        self.is_connected = False

    async def load_tools(self) -> bool:
        """Load available tools from MCP server"""
        try:
            async with httpx.AsyncClient(timeout=10.0) as client:
                resp = await client.get(f"{self.base_url}/api/toolset")
                resp.raise_for_status()
                data = resp.json()
                
                print(f"[{self.name}] Server response keys: {list(data.keys())}")
                
                if 'tools' in data:
                    tools_data = data['tools']
                    
                    if isinstance(tools_data, dict):
                        for tool_name, tool_info in tools_data.items():
                            tool_info['name'] = tool_name
                            self.tools[tool_name] = tool_info
                            print(f"[{self.name}] Loaded tool: {tool_name}")
                    elif isinstance(tools_data, list):
                        for tool in tools_data:
                            if isinstance(tool, dict) and 'name' in tool:
                                self.tools[tool['name']] = tool
                                print(f"[{self.name}] Loaded tool: {tool['name']}")
                
                self.is_connected = True
                print(f"[{self.name}] ✓ Loaded {len(self.tools)} tools: {list(self.tools.keys())}")
                return True
                
        except Exception as e:
            print(f"[{self.name}] Error loading tools: {e}")
            self.is_connected = False
            return False

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
                
                print(f"[{self.name}] Invoking tool {tool_name} with payload: {payload}")
                url = f"{self.base_url}/mcp"
                resp = await client.post(url, json=payload)
                
                if resp.status_code == 200:
                    result = resp.json()
                    print(f"[{self.name}] ✓ Tool invocation successful")
                    
                    # Parse MCP JSON-RPC response format
                    if "result" in result:
                        mcp_result = result["result"]
                        
                        # Handle content array format
                        if isinstance(mcp_result, dict) and "content" in mcp_result:
                            content = mcp_result["content"]
                            if isinstance(content, list):
                                parsed_results = []
                                
                                for item in content:
                                    if isinstance(item, dict) and item.get("type") == "text" and "text" in item:
                                        text_content = item["text"]
                                        try:
                                            parsed_json = json.loads(text_content)
                                            parsed_results.append(parsed_json)
                                        except json.JSONDecodeError:
                                            parsed_results.append(text_content)
                                    else:
                                        parsed_results.append(item)
                                
                                return {"results": parsed_results, "status": "success"}
                            else:
                                return {"results": content, "status": "success"}
                        
                        elif isinstance(mcp_result, list):
                            return {"results": mcp_result, "status": "success"}
                        else:
                            return {"results": mcp_result, "status": "success"}
                    else:
                        return {"results": result, "status": "success"}
                else:
                    error_msg = f"HTTP {resp.status_code}: {resp.text}"
                    print(f"[{self.name}] Tool invocation failed: {error_msg}")
                    return {"error": error_msg, "status": "error"}
                    
        except Exception as e:
            error_msg = f"Tool invocation error: {e}"
            print(f"[{self.name}] {error_msg}")
            return {"error": error_msg, "status": "error"}

    def get_tool_info(self, tool_name: str) -> Optional[Dict[str, Any]]:
        """Get tool information including parameters"""
        return self.tools.get(tool_name)

    def get_available_tools(self) -> List[str]:
        """Get list of available tool names"""
        return list(self.tools.keys())

    def is_tool_available(self, tool_name: str) -> bool:
        """Check if a specific tool is available"""
        return tool_name in self.tools

    async def health_check(self) -> bool:
        """Check if MCP server is healthy"""
        try:
            async with httpx.AsyncClient(timeout=5.0) as client:
                resp = await client.get(f"{self.base_url}/api/toolset")
                return resp.status_code == 200
        except Exception:
            return False
