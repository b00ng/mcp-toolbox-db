"""
Enhanced MCP Client with retry logic, connection pooling, and better error handling
"""

import httpx
import json
import asyncio
from typing import Dict, List, Any, Optional, Callable
from datetime import datetime, timedelta
from dataclasses import dataclass
from enum import Enum

class MCPConnectionState(Enum):
    DISCONNECTED = "disconnected"
    CONNECTING = "connecting"
    CONNECTED = "connected"
    ERROR = "error"
    RECONNECTING = "reconnecting"

@dataclass
class MCPToolMetrics:
    """Metrics for tool execution"""
    tool_name: str
    execution_count: int = 0
    success_count: int = 0
    failure_count: int = 0
    total_duration_ms: float = 0
    last_execution: Optional[datetime] = None
    last_error: Optional[str] = None
    
    @property
    def success_rate(self) -> float:
        if self.execution_count == 0:
            return 0.0
        return (self.success_count / self.execution_count) * 100
    
    @property
    def average_duration_ms(self) -> float:
        if self.success_count == 0:
            return 0.0
        return self.total_duration_ms / self.success_count

class EnhancedMCPClient:
    """Enhanced MCP Client with advanced features"""
    
    def __init__(
        self, 
        base_url: str, 
        name: str = "mcp_client",
        max_retries: int = 3,
        retry_delay: float = 1.0,
        timeout: float = 30.0,
        connection_pool_size: int = 10
    ):
        self.base_url = base_url.rstrip('/')
        self.name = name
        self.max_retries = max_retries
        self.retry_delay = retry_delay
        self.timeout = timeout
        
        # Connection state
        self.state = MCPConnectionState.DISCONNECTED
        self.tools = {}
        
        # Metrics tracking
        self.metrics: Dict[str, MCPToolMetrics] = {}
        
        # Connection pool for better performance
        self.client_pool: List[httpx.AsyncClient] = []
        self.pool_size = connection_pool_size
        self.pool_index = 0
        
        # Callbacks for state changes
        self.state_callbacks: List[Callable] = []
        
        # Cache for tool results (with TTL)
        self.cache: Dict[str, tuple[Any, datetime]] = {}
        self.cache_ttl = timedelta(minutes=5)
        
    async def initialize(self) -> bool:
        """Initialize the client and connection pool"""
        try:
            self._set_state(MCPConnectionState.CONNECTING)
            
            # Create connection pool
            for _ in range(self.pool_size):
                client = httpx.AsyncClient(
                    timeout=httpx.Timeout(self.timeout),
                    limits=httpx.Limits(max_keepalive_connections=5)
                )
                self.client_pool.append(client)
            
            # Load tools
            success = await self.load_tools()
            
            if success:
                self._set_state(MCPConnectionState.CONNECTED)
            else:
                self._set_state(MCPConnectionState.ERROR)
            
            return success
            
        except Exception as e:
            print(f"[{self.name}] Initialization failed: {e}")
            self._set_state(MCPConnectionState.ERROR)
            return False
    
    async def cleanup(self):
        """Clean up resources"""
        for client in self.client_pool:
            await client.aclose()
        self.client_pool.clear()
        self._set_state(MCPConnectionState.DISCONNECTED)
    
    def _get_client(self) -> httpx.AsyncClient:
        """Get next client from pool (round-robin)"""
        if not self.client_pool:
            raise RuntimeError("Client pool not initialized")
        
        client = self.client_pool[self.pool_index]
        self.pool_index = (self.pool_index + 1) % len(self.client_pool)
        return client
    
    def _set_state(self, state: MCPConnectionState):
        """Update connection state and notify callbacks"""
        old_state = self.state
        self.state = state
        
        if old_state != state:
            print(f"[{self.name}] State changed: {old_state.value} -> {state.value}")
            for callback in self.state_callbacks:
                try:
                    callback(self.name, old_state, state)
                except Exception as e:
                    print(f"[{self.name}] Error in state callback: {e}")
    
    def add_state_callback(self, callback: Callable):
        """Add a callback for state changes"""
        self.state_callbacks.append(callback)
    
    async def load_tools(self) -> bool:
        """Load available tools from MCP server with retry logic"""
        for attempt in range(self.max_retries):
            try:
                client = self._get_client()
                resp = await client.get(f"{self.base_url}/api/toolset")
                
                if resp.status_code == 200:
                    data = resp.json()
                    
                    if 'tools' in data:
                        tools_data = data['tools']
                        
                        if isinstance(tools_data, dict):
                            for tool_name, tool_info in tools_data.items():
                                tool_info['name'] = tool_name
                                self.tools[tool_name] = tool_info
                                # Initialize metrics for each tool
                                if tool_name not in self.metrics:
                                    self.metrics[tool_name] = MCPToolMetrics(tool_name)
                        elif isinstance(tools_data, list):
                            for tool in tools_data:
                                if isinstance(tool, dict) and 'name' in tool:
                                    tool_name = tool['name']
                                    self.tools[tool_name] = tool
                                    if tool_name not in self.metrics:
                                        self.metrics[tool_name] = MCPToolMetrics(tool_name)
                    
                    print(f"[{self.name}] ✓ Loaded {len(self.tools)} tools")
                    return True
                
                elif resp.status_code >= 500:
                    # Server error, retry
                    if attempt < self.max_retries - 1:
                        await asyncio.sleep(self.retry_delay * (attempt + 1))
                        continue
                    
            except httpx.RequestError as e:
                print(f"[{self.name}] Attempt {attempt + 1}/{self.max_retries} failed: {e}")
                if attempt < self.max_retries - 1:
                    await asyncio.sleep(self.retry_delay * (attempt + 1))
                    continue
            
            except Exception as e:
                print(f"[{self.name}] Unexpected error loading tools: {e}")
                break
        
        print(f"[{self.name}] Failed to load tools after {self.max_retries} attempts")
        return False
    
    def _get_cache_key(self, tool_name: str, params: Dict[str, Any]) -> str:
        """Generate cache key for tool invocation"""
        return f"{tool_name}:{json.dumps(params, sort_keys=True)}"
    
    def _get_cached_result(self, cache_key: str) -> Optional[Any]:
        """Get cached result if still valid"""
        if cache_key in self.cache:
            result, timestamp = self.cache[cache_key]
            if datetime.now() - timestamp < self.cache_ttl:
                return result
            else:
                del self.cache[cache_key]
        return None
    
    def _set_cached_result(self, cache_key: str, result: Any):
        """Cache a result with timestamp"""
        self.cache[cache_key] = (result, datetime.now())
    
    async def invoke_tool(
        self, 
        tool_name: str, 
        params: Dict[str, Any],
        use_cache: bool = True
    ) -> Dict[str, Any]:
        """Invoke a tool with retry logic and caching"""
        
        # Check cache first
        if use_cache:
            cache_key = self._get_cache_key(tool_name, params)
            cached_result = self._get_cached_result(cache_key)
            if cached_result is not None:
                print(f"[{self.name}] Using cached result for {tool_name}")
                return cached_result
        
        # Update metrics
        if tool_name not in self.metrics:
            self.metrics[tool_name] = MCPToolMetrics(tool_name)
        
        metric = self.metrics[tool_name]
        metric.execution_count += 1
        start_time = asyncio.get_event_loop().time()
        
        for attempt in range(self.max_retries):
            try:
                client = self._get_client()
                
                payload = {
                    "jsonrpc": "2.0",
                    "id": 1,
                    "method": "tools/call",
                    "params": {
                        "name": tool_name,
                        "arguments": params or {}
                    }
                }
                
                resp = await client.post(f"{self.base_url}/mcp", json=payload)
                
                if resp.status_code == 200:
                    result = resp.json()
                    
                    # Parse result
                    parsed_result = self._parse_mcp_result(result)
                    
                    # Update metrics on success
                    duration_ms = (asyncio.get_event_loop().time() - start_time) * 1000
                    metric.success_count += 1
                    metric.total_duration_ms += duration_ms
                    metric.last_execution = datetime.now()
                    
                    # Cache result
                    if use_cache:
                        self._set_cached_result(cache_key, parsed_result)
                    
                    return parsed_result
                
                elif resp.status_code >= 500:
                    # Server error, retry
                    if attempt < self.max_retries - 1:
                        await asyncio.sleep(self.retry_delay * (attempt + 1))
                        continue
                else:
                    # Client error, don't retry
                    error_msg = f"HTTP {resp.status_code}: {resp.text}"
                    metric.failure_count += 1
                    metric.last_error = error_msg
                    metric.last_execution = datetime.now()
                    return {"error": error_msg, "status": "error"}
                    
            except httpx.RequestError as e:
                print(f"[{self.name}] Attempt {attempt + 1}/{self.max_retries} failed for {tool_name}: {e}")
                if attempt < self.max_retries - 1:
                    await asyncio.sleep(self.retry_delay * (attempt + 1))
                    continue
                
                metric.failure_count += 1
                metric.last_error = str(e)
                metric.last_execution = datetime.now()
                
            except Exception as e:
                print(f"[{self.name}] Unexpected error invoking {tool_name}: {e}")
                metric.failure_count += 1
                metric.last_error = str(e)
                metric.last_execution = datetime.now()
                return {"error": str(e), "status": "error"}
        
        # All retries failed
        error_msg = f"Failed after {self.max_retries} attempts"
        metric.failure_count += 1
        metric.last_error = error_msg
        metric.last_execution = datetime.now()
        return {"error": error_msg, "status": "error"}
    
    def _parse_mcp_result(self, result: Dict[str, Any]) -> Dict[str, Any]:
        """Parse MCP JSON-RPC response"""
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
    
    def get_tool_info(self, tool_name: str) -> Optional[Dict[str, Any]]:
        """Get tool information including parameters"""
        return self.tools.get(tool_name)
    
    def get_available_tools(self) -> List[str]:
        """Get list of available tool names"""
        return list(self.tools.keys())
    
    def is_tool_available(self, tool_name: str) -> bool:
        """Check if a specific tool is available"""
        return tool_name in self.tools
    
    async def health_check(self) -> Dict[str, Any]:
        """Comprehensive health check"""
        health = {
            "name": self.name,
            "url": self.base_url,
            "state": self.state.value,
            "tools_loaded": len(self.tools),
            "connection_pool_size": len(self.client_pool),
            "cache_size": len(self.cache),
            "metrics": {}
        }
        
        # Add tool metrics
        for tool_name, metric in self.metrics.items():
            health["metrics"][tool_name] = {
                "executions": metric.execution_count,
                "success_rate": f"{metric.success_rate:.1f}%",
                "avg_duration_ms": f"{metric.average_duration_ms:.1f}",
                "last_execution": metric.last_execution.isoformat() if metric.last_execution else None,
                "last_error": metric.last_error
            }
        
        # Test connection
        try:
            client = self._get_client()
            resp = await client.get(f"{self.base_url}/api/toolset")
            health["connection_test"] = "success" if resp.status_code == 200 else f"failed ({resp.status_code})"
        except Exception as e:
            health["connection_test"] = f"failed ({str(e)})"
        
        return health
    
    def get_metrics_summary(self) -> Dict[str, Any]:
        """Get summary of all tool metrics"""
        summary = {
            "total_executions": sum(m.execution_count for m in self.metrics.values()),
            "total_successes": sum(m.success_count for m in self.metrics.values()),
            "total_failures": sum(m.failure_count for m in self.metrics.values()),
            "tools": {}
        }
        
        for tool_name, metric in self.metrics.items():
            summary["tools"][tool_name] = {
                "success_rate": metric.success_rate,
                "avg_duration_ms": metric.average_duration_ms,
                "last_error": metric.last_error
            }
        
        return summary
