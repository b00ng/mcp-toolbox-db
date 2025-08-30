"""
MCP Connection Monitor - Monitors MCP server health and manages automatic recovery
"""

import asyncio
import time
from typing import Dict, List, Any, Optional, Callable
from datetime import datetime, timedelta
from enum import Enum
from dataclasses import dataclass, field

class HealthStatus(Enum):
    HEALTHY = "healthy"
    DEGRADED = "degraded"
    UNHEALTHY = "unhealthy"
    UNKNOWN = "unknown"

@dataclass
class ServerHealth:
    """Health information for a single MCP server"""
    name: str
    url: str
    status: HealthStatus = HealthStatus.UNKNOWN
    last_check: Optional[datetime] = None
    last_success: Optional[datetime] = None
    consecutive_failures: int = 0
    response_time_ms: float = 0
    available_tools: List[str] = field(default_factory=list)
    error_message: Optional[str] = None

class MCPConnectionMonitor:
    """Monitors MCP server connections and manages failover"""
    
    def __init__(
        self,
        check_interval: int = 30,  # seconds
        failure_threshold: int = 3,
        recovery_threshold: int = 2
    ):
        self.check_interval = check_interval
        self.failure_threshold = failure_threshold
        self.recovery_threshold = recovery_threshold
        
        # Tracked servers
        self.servers: Dict[str, ServerHealth] = {}
        
        # Monitoring state
        self.monitoring = False
        self.monitor_task: Optional[asyncio.Task] = None
        
        # Callbacks
        self.status_change_callbacks: List[Callable] = []
        self.failure_callbacks: List[Callable] = []
        self.recovery_callbacks: List[Callable] = []
        
        # Statistics
        self.total_checks = 0
        self.total_failures = 0
        self.start_time = datetime.now()
    
    def add_server(self, name: str, url: str, client):
        """Add a server to monitor"""
        self.servers[name] = ServerHealth(name=name, url=url)
        # Store client reference for health checks
        setattr(self.servers[name], '_client', client)
        print(f"[Monitor] Added server: {name} ({url})")
    
    def remove_server(self, name: str):
        """Remove a server from monitoring"""
        if name in self.servers:
            del self.servers[name]
            print(f"[Monitor] Removed server: {name}")
    
    async def start_monitoring(self):
        """Start the monitoring loop"""
        if self.monitoring:
            print("[Monitor] Already monitoring")
            return
        
        self.monitoring = True
        self.monitor_task = asyncio.create_task(self._monitor_loop())
        print(f"[Monitor] Started monitoring with {self.check_interval}s interval")
    
    async def stop_monitoring(self):
        """Stop the monitoring loop"""
        self.monitoring = False
        if self.monitor_task:
            self.monitor_task.cancel()
            try:
                await self.monitor_task
            except asyncio.CancelledError:
                pass
        print("[Monitor] Stopped monitoring")
    
    async def _monitor_loop(self):
        """Main monitoring loop"""
        while self.monitoring:
            try:
                await self._check_all_servers()
                await asyncio.sleep(self.check_interval)
            except asyncio.CancelledError:
                break
            except Exception as e:
                print(f"[Monitor] Error in monitoring loop: {e}")
                await asyncio.sleep(self.check_interval)
    
    async def _check_all_servers(self):
        """Check health of all servers"""
        self.total_checks += 1
        
        tasks = []
        for server_name in self.servers:
            tasks.append(self._check_server_health(server_name))
        
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)
    
    async def _check_server_health(self, server_name: str):
        """Check health of a single server"""
        server = self.servers.get(server_name)
        if not server:
            return
        
        client = getattr(server, '_client', None)
        if not client:
            server.status = HealthStatus.UNKNOWN
            server.error_message = "No client configured"
            return
        
        start_time = time.time()
        old_status = server.status
        
        try:
            # Perform health check
            health_result = await client.health_check()
            
            # Update metrics
            server.response_time_ms = (time.time() - start_time) * 1000
            server.last_check = datetime.now()
            
            # Check if connection test passed
            if health_result.get('connection_test') == 'success':
                server.last_success = datetime.now()
                server.consecutive_failures = 0
                server.error_message = None
                
                # Update available tools
                server.available_tools = health_result.get('tools_loaded', [])
                
                # Determine health status
                if server.available_tools:
                    server.status = HealthStatus.HEALTHY
                else:
                    server.status = HealthStatus.DEGRADED
                    server.error_message = "No tools available"
            else:
                self._handle_check_failure(server, health_result.get('connection_test', 'Unknown error'))
                
        except Exception as e:
            self._handle_check_failure(server, str(e))
        
        # Notify if status changed
        if old_status != server.status:
            await self._notify_status_change(server, old_status)
    
    def _handle_check_failure(self, server: ServerHealth, error: str):
        """Handle a failed health check"""
        server.consecutive_failures += 1
        server.error_message = error
        server.last_check = datetime.now()
        self.total_failures += 1
        
        if server.consecutive_failures >= self.failure_threshold:
            server.status = HealthStatus.UNHEALTHY
        else:
            server.status = HealthStatus.DEGRADED
    
    async def _notify_status_change(self, server: ServerHealth, old_status: HealthStatus):
        """Notify callbacks about status change"""
        print(f"[Monitor] {server.name}: {old_status.value} -> {server.status.value}")
        
        # Status change callbacks
        for callback in self.status_change_callbacks:
            try:
                await callback(server.name, old_status, server.status)
            except Exception as e:
                print(f"[Monitor] Error in status change callback: {e}")
        
        # Failure callbacks
        if server.status == HealthStatus.UNHEALTHY and old_status != HealthStatus.UNHEALTHY:
            for callback in self.failure_callbacks:
                try:
                    await callback(server.name, server.error_message)
                except Exception as e:
                    print(f"[Monitor] Error in failure callback: {e}")
        
        # Recovery callbacks
        elif server.status == HealthStatus.HEALTHY and old_status != HealthStatus.HEALTHY:
            for callback in self.recovery_callbacks:
                try:
                    await callback(server.name)
                except Exception as e:
                    print(f"[Monitor] Error in recovery callback: {e}")
    
    def add_status_change_callback(self, callback: Callable):
        """Add a callback for status changes"""
        self.status_change_callbacks.append(callback)
    
    def add_failure_callback(self, callback: Callable):
        """Add a callback for server failures"""
        self.failure_callbacks.append(callback)
    
    def add_recovery_callback(self, callback: Callable):
        """Add a callback for server recovery"""
        self.recovery_callbacks.append(callback)
    
    def get_server_status(self, server_name: str) -> Optional[ServerHealth]:
        """Get current status of a server"""
        return self.servers.get(server_name)
    
    def get_all_statuses(self) -> Dict[str, ServerHealth]:
        """Get status of all servers"""
        return self.servers.copy()
    
    def get_healthy_servers(self) -> List[str]:
        """Get list of healthy servers"""
        return [
            name for name, server in self.servers.items()
            if server.status == HealthStatus.HEALTHY
        ]
    
    def get_statistics(self) -> Dict[str, Any]:
        """Get monitoring statistics"""
        uptime = datetime.now() - self.start_time
        
        return {
            'uptime_seconds': uptime.total_seconds(),
            'total_checks': self.total_checks,
            'total_failures': self.total_failures,
            'servers_monitored': len(self.servers),
            'healthy_servers': len(self.get_healthy_servers()),
            'check_interval': self.check_interval,
            'failure_threshold': self.failure_threshold,
            'server_summary': {
                name: {
                    'status': server.status.value,
                    'consecutive_failures': server.consecutive_failures,
                    'response_time_ms': server.response_time_ms,
                    'last_check': server.last_check.isoformat() if server.last_check else None
                }
                for name, server in self.servers.items()
            }
        }


class BatchExecutor:
    """Executes multiple tool calls in batch with optimizations"""
    
    def __init__(self, max_concurrent: int = 5):
        self.max_concurrent = max_concurrent
        self.execution_stats = {
            'total_batches': 0,
            'total_tools': 0,
            'total_time_ms': 0,
            'failures': 0
        }
    
    async def execute_batch(
        self,
        tool_calls: List[Dict[str, Any]],
        client,
        parallel: bool = True,
        stop_on_error: bool = False
    ) -> List[Dict[str, Any]]:
        """
        Execute a batch of tool calls
        
        Args:
            tool_calls: List of dicts with 'tool_name' and 'params'
            client: MCP client to use
            parallel: Execute in parallel if True, sequential if False
            stop_on_error: Stop execution on first error if True
        
        Returns:
            List of results in the same order as tool_calls
        """
        
        self.execution_stats['total_batches'] += 1
        self.execution_stats['total_tools'] += len(tool_calls)
        
        start_time = time.time()
        
        if parallel:
            results = await self._execute_parallel(tool_calls, client, stop_on_error)
        else:
            results = await self._execute_sequential(tool_calls, client, stop_on_error)
        
        execution_time_ms = (time.time() - start_time) * 1000
        self.execution_stats['total_time_ms'] += execution_time_ms
        
        # Count failures
        failures = sum(1 for r in results if r.get('status') == 'error')
        self.execution_stats['failures'] += failures
        
        print(f"[Batch] Executed {len(tool_calls)} tools in {execution_time_ms:.1f}ms ({failures} failures)")
        
        return results
    
    async def _execute_parallel(
        self,
        tool_calls: List[Dict[str, Any]],
        client,
        stop_on_error: bool
    ) -> List[Dict[str, Any]]:
        """Execute tools in parallel with concurrency limit"""
        
        results = [None] * len(tool_calls)
        semaphore = asyncio.Semaphore(self.max_concurrent)
        
        async def execute_with_semaphore(index: int, tool_call: Dict[str, Any]):
            async with semaphore:
                try:
                    result = await client.invoke_tool(
                        tool_call['tool_name'],
                        tool_call.get('params', {})
                    )
                    results[index] = result
                    
                    if stop_on_error and result.get('status') == 'error':
                        raise Exception(f"Tool {tool_call['tool_name']} failed")
                        
                except Exception as e:
                    results[index] = {
                        'status': 'error',
                        'error': str(e),
                        'tool_name': tool_call['tool_name']
                    }
                    if stop_on_error:
                        raise
        
        tasks = [
            execute_with_semaphore(i, tool_call)
            for i, tool_call in enumerate(tool_calls)
        ]
        
        try:
            await asyncio.gather(*tasks, return_exceptions=not stop_on_error)
        except Exception as e:
            print(f"[Batch] Parallel execution stopped: {e}")
        
        return results
    
    async def _execute_sequential(
        self,
        tool_calls: List[Dict[str, Any]],
        client,
        stop_on_error: bool
    ) -> List[Dict[str, Any]]:
        """Execute tools sequentially"""
        
        results = []
        
        for tool_call in tool_calls:
            try:
                result = await client.invoke_tool(
                    tool_call['tool_name'],
                    tool_call.get('params', {})
                )
                results.append(result)
                
                if stop_on_error and result.get('status') == 'error':
                    print(f"[Batch] Sequential execution stopped at {tool_call['tool_name']}")
                    break
                    
            except Exception as e:
                error_result = {
                    'status': 'error',
                    'error': str(e),
                    'tool_name': tool_call['tool_name']
                }
                results.append(error_result)
                
                if stop_on_error:
                    break
        
        # Fill remaining with None if stopped early
        while len(results) < len(tool_calls):
            results.append({
                'status': 'error',
                'error': 'Skipped due to previous error',
                'tool_name': tool_calls[len(results)]['tool_name']
            })
        
        return results
    
    def get_statistics(self) -> Dict[str, Any]:
        """Get batch execution statistics"""
        avg_time = 0
        if self.execution_stats['total_batches'] > 0:
            avg_time = self.execution_stats['total_time_ms'] / self.execution_stats['total_batches']
        
        success_rate = 0
        if self.execution_stats['total_tools'] > 0:
            success_rate = ((self.execution_stats['total_tools'] - self.execution_stats['failures']) 
                          / self.execution_stats['total_tools'] * 100)
        
        return {
            'total_batches': self.execution_stats['total_batches'],
            'total_tools_executed': self.execution_stats['total_tools'],
            'total_failures': self.execution_stats['failures'],
            'success_rate': f"{success_rate:.1f}%",
            'average_batch_time_ms': f"{avg_time:.1f}",
            'max_concurrent': self.max_concurrent
        }
    
    def reset_statistics(self):
        """Reset execution statistics"""
        self.execution_stats = {
            'total_batches': 0,
            'total_tools': 0,
            'total_time_ms': 0,
            'failures': 0
        }
