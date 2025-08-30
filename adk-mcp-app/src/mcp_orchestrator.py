"""
MCP Orchestrator for graceful degradation and error recovery.
Manages switching between enhanced MCP clients and fallback handlers.
"""

import asyncio
import logging
from typing import Dict, Any, Optional, List, Tuple
from datetime import datetime, timedelta
from enum import Enum
from dataclasses import dataclass, field

from .enhanced_mcp_client import EnhancedMCPClient
from .mcp_fallback_handler import MCPFallbackHandler
from .mcp_connection_monitor import MCPConnectionMonitor, ServerHealth

logger = logging.getLogger(__name__)


class ExecutionMode(Enum):
    """Execution modes for the orchestrator."""
    PRIMARY = "primary"  # Using enhanced MCP client
    FALLBACK = "fallback"  # Using direct database access
    DEGRADED = "degraded"  # Limited functionality
    RECOVERY = "recovery"  # Attempting to restore primary mode


@dataclass
class RecoveryStrategy:
    """Configuration for recovery attempts."""
    max_retries: int = 3
    initial_delay: float = 1.0
    max_delay: float = 60.0
    backoff_factor: float = 2.0
    recovery_window: timedelta = field(default_factory=lambda: timedelta(minutes=5))


@dataclass
class ExecutionResult:
    """Result of tool execution with metadata."""
    success: bool
    result: Any
    mode: ExecutionMode
    execution_time: float
    error: Optional[str] = None
    retries: int = 0


class MCPOrchestrator:
    """
    Orchestrates MCP operations with automatic failover and recovery.
    """
    
    def __init__(
        self,
        primary_url: str,
        dynamic_url: Optional[str] = None,
        database_path: Optional[str] = None,
        recovery_strategy: Optional[RecoveryStrategy] = None
    ):
        """
        Initialize the orchestrator.
        
        Args:
            primary_url: URL for primary MCP server
            dynamic_url: URL for dynamic MCP server (optional)
            database_path: Path to database for fallback (optional)
            recovery_strategy: Recovery configuration
        """
        self.primary_url = primary_url
        self.dynamic_url = dynamic_url
        self.database_path = database_path
        self.recovery_strategy = recovery_strategy or RecoveryStrategy()
        
        # Initialize components
        self.enhanced_client = EnhancedMCPClient(primary_url, dynamic_url)
        self.fallback_handler = MCPFallbackHandler(database_path) if database_path else None
        self.monitor = MCPConnectionMonitor(check_interval=30)
        
        # State management
        self.current_mode = ExecutionMode.PRIMARY
        self.mode_history: List[Tuple[datetime, ExecutionMode]] = []
        self.recovery_attempts = 0
        self.last_recovery_attempt: Optional[datetime] = None
        
        # Error tracking
        self.consecutive_errors = 0
        self.error_threshold = 3
        self.error_history: List[Tuple[datetime, str]] = []
        
        # Performance metrics
        self.mode_metrics: Dict[ExecutionMode, Dict[str, Any]] = {
            mode: {"executions": 0, "successes": 0, "total_time": 0.0}
            for mode in ExecutionMode
        }
        
        # Setup callbacks
        self._setup_monitor_callbacks()
    
    def _setup_monitor_callbacks(self):
        """Setup callbacks for connection monitor events."""
        self.monitor.on_status_change = self._handle_status_change
        self.monitor.on_failure = self._handle_failure
        self.monitor.on_recovery = self._handle_recovery
    
    async def initialize(self) -> bool:
        """
        Initialize all components and start monitoring.
        
        Returns:
            True if initialization successful
        """
        try:
            # Initialize enhanced client
            await self.enhanced_client.initialize()
            
            # Start monitoring
            asyncio.create_task(self.monitor.start_monitoring(
                self.primary_url,
                self.dynamic_url
            ))
            
            # Test primary connection
            if await self._test_primary_connection():
                self.current_mode = ExecutionMode.PRIMARY
                logger.info("Orchestrator initialized in PRIMARY mode")
            elif self.fallback_handler:
                self.current_mode = ExecutionMode.FALLBACK
                logger.warning("Orchestrator initialized in FALLBACK mode")
            else:
                self.current_mode = ExecutionMode.DEGRADED
                logger.error("Orchestrator initialized in DEGRADED mode")
            
            self._record_mode_change(self.current_mode)
            return True
            
        except Exception as e:
            logger.error(f"Failed to initialize orchestrator: {e}")
            return False
    
    async def execute_tool(
        self,
        tool_name: str,
        parameters: Dict[str, Any],
        server_type: str = "primary"
    ) -> ExecutionResult:
        """
        Execute a tool with automatic failover and recovery.
        
        Args:
            tool_name: Name of the tool to execute
            parameters: Tool parameters
            server_type: Target server type
            
        Returns:
            ExecutionResult with execution details
        """
        start_time = datetime.now()
        retries = 0
        
        # Try primary execution
        if self.current_mode in [ExecutionMode.PRIMARY, ExecutionMode.RECOVERY]:
            result = await self._try_primary_execution(
                tool_name, parameters, server_type
            )
            if result.success:
                self._handle_successful_execution(result)
                return result
            retries = result.retries
        
        # Try fallback if available
        if self.fallback_handler and self._can_use_fallback(tool_name):
            result = await self._try_fallback_execution(
                tool_name, parameters, retries
            )
            if result.success:
                self._handle_successful_execution(result)
                return result
        
        # Return degraded result
        execution_time = (datetime.now() - start_time).total_seconds()
        return ExecutionResult(
            success=False,
            result=None,
            mode=ExecutionMode.DEGRADED,
            execution_time=execution_time,
            error="All execution methods failed",
            retries=retries
        )
    
    async def _try_primary_execution(
        self,
        tool_name: str,
        parameters: Dict[str, Any],
        server_type: str
    ) -> ExecutionResult:
        """Try executing with primary MCP client."""
        start_time = datetime.now()
        retries = 0
        last_error = None
        
        for attempt in range(self.recovery_strategy.max_retries):
            try:
                result = await self.enhanced_client.invoke_tool(
                    tool_name, parameters, server_type
                )
                
                execution_time = (datetime.now() - start_time).total_seconds()
                return ExecutionResult(
                    success=True,
                    result=result,
                    mode=ExecutionMode.PRIMARY,
                    execution_time=execution_time,
                    retries=retries
                )
                
            except Exception as e:
                last_error = str(e)
                retries += 1
                
                if attempt < self.recovery_strategy.max_retries - 1:
                    delay = min(
                        self.recovery_strategy.initial_delay * (
                            self.recovery_strategy.backoff_factor ** attempt
                        ),
                        self.recovery_strategy.max_delay
                    )
                    await asyncio.sleep(delay)
        
        execution_time = (datetime.now() - start_time).total_seconds()
        return ExecutionResult(
            success=False,
            result=None,
            mode=ExecutionMode.PRIMARY,
            execution_time=execution_time,
            error=last_error,
            retries=retries
        )
    
    async def _try_fallback_execution(
        self,
        tool_name: str,
        parameters: Dict[str, Any],
        previous_retries: int
    ) -> ExecutionResult:
        """Try executing with fallback handler."""
        start_time = datetime.now()
        
        try:
            result = await self.fallback_handler.execute_fallback(
                tool_name, parameters
            )
            
            execution_time = (datetime.now() - start_time).total_seconds()
            return ExecutionResult(
                success=True,
                result=result,
                mode=ExecutionMode.FALLBACK,
                execution_time=execution_time,
                retries=previous_retries
            )
            
        except Exception as e:
            execution_time = (datetime.now() - start_time).total_seconds()
            return ExecutionResult(
                success=False,
                result=None,
                mode=ExecutionMode.FALLBACK,
                execution_time=execution_time,
                error=str(e),
                retries=previous_retries
            )
    
    def _can_use_fallback(self, tool_name: str) -> bool:
        """Check if fallback is available for the tool."""
        if not self.fallback_handler:
            return False
        return self.fallback_handler.supports_tool(tool_name)
    
    async def _test_primary_connection(self) -> bool:
        """Test if primary connection is working."""
        try:
            # Try a simple operation
            tools = await self.enhanced_client.get_available_tools("primary")
            return len(tools) > 0
        except Exception:
            return False
    
    def _handle_successful_execution(self, result: ExecutionResult):
        """Handle successful execution."""
        self.consecutive_errors = 0
        
        # Update metrics
        metrics = self.mode_metrics[result.mode]
        metrics["executions"] += 1
        metrics["successes"] += 1
        metrics["total_time"] += result.execution_time
        
        # Attempt recovery if in fallback mode
        if result.mode == ExecutionMode.FALLBACK and self._should_attempt_recovery():
            asyncio.create_task(self._attempt_recovery())
    
    def _should_attempt_recovery(self) -> bool:
        """Check if recovery should be attempted."""
        if not self.last_recovery_attempt:
            return True
        
        time_since_last = datetime.now() - self.last_recovery_attempt
        return time_since_last > self.recovery_strategy.recovery_window
    
    async def _attempt_recovery(self):
        """Attempt to recover primary connection."""
        if self.current_mode == ExecutionMode.PRIMARY:
            return
        
        self.last_recovery_attempt = datetime.now()
        self.recovery_attempts += 1
        
        logger.info(f"Attempting recovery (attempt {self.recovery_attempts})")
        
        # Switch to recovery mode
        self._switch_mode(ExecutionMode.RECOVERY)
        
        # Test primary connection
        if await self._test_primary_connection():
            logger.info("Recovery successful - switching to PRIMARY mode")
            self._switch_mode(ExecutionMode.PRIMARY)
            self.recovery_attempts = 0
        else:
            logger.warning("Recovery failed - reverting to previous mode")
            self._switch_mode(ExecutionMode.FALLBACK if self.fallback_handler else ExecutionMode.DEGRADED)
    
    def _switch_mode(self, new_mode: ExecutionMode):
        """Switch execution mode."""
        if new_mode != self.current_mode:
            old_mode = self.current_mode
            self.current_mode = new_mode
            self._record_mode_change(new_mode)
            logger.info(f"Execution mode changed: {old_mode} -> {new_mode}")
    
    def _record_mode_change(self, mode: ExecutionMode):
        """Record mode change in history."""
        self.mode_history.append((datetime.now(), mode))
        
        # Keep only recent history (last 100 entries)
        if len(self.mode_history) > 100:
            self.mode_history = self.mode_history[-100:]
    
    def _handle_status_change(self, server: str, old_status: ServerHealth, new_status: ServerHealth):
        """Handle server status change from monitor."""
        logger.info(f"Server {server} status changed: {old_status} -> {new_status}")
        
        if server == self.primary_url:
            if new_status == ServerHealth.UNHEALTHY:
                if self.fallback_handler:
                    self._switch_mode(ExecutionMode.FALLBACK)
                else:
                    self._switch_mode(ExecutionMode.DEGRADED)
            elif new_status == ServerHealth.HEALTHY and self.current_mode != ExecutionMode.PRIMARY:
                asyncio.create_task(self._attempt_recovery())
    
    def _handle_failure(self, server: str, error: str):
        """Handle server failure from monitor."""
        logger.error(f"Server {server} failed: {error}")
        self.consecutive_errors += 1
        self.error_history.append((datetime.now(), error))
        
        # Keep only recent errors
        if len(self.error_history) > 100:
            self.error_history = self.error_history[-100:]
        
        if self.consecutive_errors >= self.error_threshold:
            logger.warning(f"Error threshold reached ({self.consecutive_errors})")
            if self.current_mode == ExecutionMode.PRIMARY:
                if self.fallback_handler:
                    self._switch_mode(ExecutionMode.FALLBACK)
                else:
                    self._switch_mode(ExecutionMode.DEGRADED)
    
    def _handle_recovery(self, server: str):
        """Handle server recovery from monitor."""
        logger.info(f"Server {server} recovered")
        if server == self.primary_url and self.current_mode != ExecutionMode.PRIMARY:
            asyncio.create_task(self._attempt_recovery())
    
    def get_status(self) -> Dict[str, Any]:
        """
        Get current orchestrator status.
        
        Returns:
            Status dictionary
        """
        return {
            "current_mode": self.current_mode.value,
            "consecutive_errors": self.consecutive_errors,
            "recovery_attempts": self.recovery_attempts,
            "last_recovery_attempt": self.last_recovery_attempt.isoformat() if self.last_recovery_attempt else None,
            "mode_metrics": {
                mode.value: {
                    **metrics,
                    "success_rate": metrics["successes"] / metrics["executions"] if metrics["executions"] > 0 else 0,
                    "avg_time": metrics["total_time"] / metrics["executions"] if metrics["executions"] > 0 else 0
                }
                for mode, metrics in self.mode_metrics.items()
            },
            "recent_mode_changes": [
                {"time": t.isoformat(), "mode": m.value}
                for t, m in self.mode_history[-10:]
            ],
            "monitor_status": self.monitor.get_statistics()
        }
    
    async def shutdown(self):
        """Shutdown the orchestrator and cleanup resources."""
        logger.info("Shutting down orchestrator")
        
        # Stop monitoring
        await self.monitor.stop_monitoring()
        
        # Close connections
        await self.enhanced_client.close()
        
        logger.info("Orchestrator shutdown complete")
