"""
Comprehensive error recovery system for MCP operations.
Provides specific recovery strategies for different error types.
"""

import asyncio
import logging
from typing import Dict, Any, Optional, List, Callable, Awaitable
from datetime import datetime, timedelta
from enum import Enum
from dataclasses import dataclass
import json
import traceback

logger = logging.getLogger(__name__)


class ErrorType(Enum):
    """Types of errors that can occur."""
    CONNECTION_ERROR = "connection_error"
    TIMEOUT_ERROR = "timeout_error"
    VALIDATION_ERROR = "validation_error"
    RATE_LIMIT_ERROR = "rate_limit_error"
    AUTHENTICATION_ERROR = "authentication_error"
    SERVER_ERROR = "server_error"
    UNKNOWN_ERROR = "unknown_error"


class RecoveryAction(Enum):
    """Recovery actions that can be taken."""
    RETRY = "retry"
    RETRY_WITH_BACKOFF = "retry_with_backoff"
    FALLBACK = "fallback"
    CIRCUIT_BREAK = "circuit_break"
    REFRESH_AUTH = "refresh_auth"
    CLEAR_CACHE = "clear_cache"
    RESTART_CONNECTION = "restart_connection"
    FAIL = "fail"


@dataclass
class ErrorContext:
    """Context information about an error."""
    error_type: ErrorType
    error_message: str
    timestamp: datetime
    tool_name: Optional[str] = None
    parameters: Optional[Dict[str, Any]] = None
    stack_trace: Optional[str] = None
    retry_count: int = 0


@dataclass
class RecoveryPlan:
    """Plan for recovering from an error."""
    primary_action: RecoveryAction
    fallback_actions: List[RecoveryAction]
    max_retries: int = 3
    retry_delay: float = 1.0
    backoff_factor: float = 2.0
    circuit_break_duration: timedelta = timedelta(minutes=5)


class CircuitBreaker:
    """
    Circuit breaker pattern implementation.
    """
    
    class State(Enum):
        CLOSED = "closed"  # Normal operation
        OPEN = "open"  # Failing, reject requests
        HALF_OPEN = "half_open"  # Testing recovery
    
    def __init__(
        self,
        failure_threshold: int = 5,
        recovery_timeout: timedelta = timedelta(minutes=1),
        success_threshold: int = 2
    ):
        """
        Initialize circuit breaker.
        
        Args:
            failure_threshold: Failures before opening circuit
            recovery_timeout: Time before attempting recovery
            success_threshold: Successes needed to close circuit
        """
        self.failure_threshold = failure_threshold
        self.recovery_timeout = recovery_timeout
        self.success_threshold = success_threshold
        
        self.state = self.State.CLOSED
        self.failure_count = 0
        self.success_count = 0
        self.last_failure_time: Optional[datetime] = None
        self.state_changed_at = datetime.now()
    
    def call_succeeded(self):
        """Record a successful call."""
        if self.state == self.State.HALF_OPEN:
            self.success_count += 1
            if self.success_count >= self.success_threshold:
                self._close()
        elif self.state == self.State.CLOSED:
            self.failure_count = 0
    
    def call_failed(self):
        """Record a failed call."""
        self.last_failure_time = datetime.now()
        
        if self.state == self.State.CLOSED:
            self.failure_count += 1
            if self.failure_count >= self.failure_threshold:
                self._open()
        elif self.state == self.State.HALF_OPEN:
            self._open()
    
    def can_execute(self) -> bool:
        """Check if execution is allowed."""
        if self.state == self.State.CLOSED:
            return True
        elif self.state == self.State.OPEN:
            if self._should_attempt_recovery():
                self._half_open()
                return True
            return False
        else:  # HALF_OPEN
            return True
    
    def _should_attempt_recovery(self) -> bool:
        """Check if recovery should be attempted."""
        if not self.last_failure_time:
            return True
        return datetime.now() - self.last_failure_time > self.recovery_timeout
    
    def _open(self):
        """Open the circuit."""
        self.state = self.State.OPEN
        self.state_changed_at = datetime.now()
        self.failure_count = 0
        self.success_count = 0
        logger.warning("Circuit breaker opened")
    
    def _close(self):
        """Close the circuit."""
        self.state = self.State.CLOSED
        self.state_changed_at = datetime.now()
        self.failure_count = 0
        self.success_count = 0
        logger.info("Circuit breaker closed")
    
    def _half_open(self):
        """Enter half-open state."""
        self.state = self.State.HALF_OPEN
        self.state_changed_at = datetime.now()
        self.success_count = 0
        logger.info("Circuit breaker half-open")
    
    def get_status(self) -> Dict[str, Any]:
        """Get circuit breaker status."""
        return {
            "state": self.state.value,
            "failure_count": self.failure_count,
            "success_count": self.success_count,
            "state_changed_at": self.state_changed_at.isoformat(),
            "last_failure_time": self.last_failure_time.isoformat() if self.last_failure_time else None
        }


class ErrorRecoveryManager:
    """
    Manages error recovery strategies and execution.
    """
    
    def __init__(self):
        """Initialize error recovery manager."""
        self.error_strategies = self._initialize_strategies()
        self.circuit_breakers: Dict[str, CircuitBreaker] = {}
        self.error_history: List[ErrorContext] = []
        self.recovery_callbacks: Dict[ErrorType, List[Callable]] = {}
        
        # Statistics
        self.error_counts: Dict[ErrorType, int] = {et: 0 for et in ErrorType}
        self.recovery_success_counts: Dict[RecoveryAction, int] = {ra: 0 for ra in RecoveryAction}
        self.recovery_failure_counts: Dict[RecoveryAction, int] = {ra: 0 for ra in RecoveryAction}
    
    def _initialize_strategies(self) -> Dict[ErrorType, RecoveryPlan]:
        """Initialize recovery strategies for each error type."""
        return {
            ErrorType.CONNECTION_ERROR: RecoveryPlan(
                primary_action=RecoveryAction.RESTART_CONNECTION,
                fallback_actions=[RecoveryAction.RETRY_WITH_BACKOFF, RecoveryAction.FALLBACK],
                max_retries=5,
                retry_delay=2.0
            ),
            ErrorType.TIMEOUT_ERROR: RecoveryPlan(
                primary_action=RecoveryAction.RETRY_WITH_BACKOFF,
                fallback_actions=[RecoveryAction.FALLBACK],
                max_retries=3,
                retry_delay=1.0
            ),
            ErrorType.VALIDATION_ERROR: RecoveryPlan(
                primary_action=RecoveryAction.FAIL,
                fallback_actions=[],
                max_retries=0
            ),
            ErrorType.RATE_LIMIT_ERROR: RecoveryPlan(
                primary_action=RecoveryAction.RETRY_WITH_BACKOFF,
                fallback_actions=[RecoveryAction.CIRCUIT_BREAK],
                max_retries=3,
                retry_delay=5.0,
                backoff_factor=3.0
            ),
            ErrorType.AUTHENTICATION_ERROR: RecoveryPlan(
                primary_action=RecoveryAction.REFRESH_AUTH,
                fallback_actions=[RecoveryAction.FAIL],
                max_retries=2
            ),
            ErrorType.SERVER_ERROR: RecoveryPlan(
                primary_action=RecoveryAction.CIRCUIT_BREAK,
                fallback_actions=[RecoveryAction.FALLBACK],
                circuit_break_duration=timedelta(minutes=10)
            ),
            ErrorType.UNKNOWN_ERROR: RecoveryPlan(
                primary_action=RecoveryAction.RETRY,
                fallback_actions=[RecoveryAction.FALLBACK, RecoveryAction.FAIL],
                max_retries=2
            )
        }
    
    def classify_error(self, error: Exception) -> ErrorType:
        """
        Classify an error into an error type.
        
        Args:
            error: The exception to classify
            
        Returns:
            Classified error type
        """
        error_str = str(error).lower()
        
        if any(term in error_str for term in ["connection", "connect", "network"]):
            return ErrorType.CONNECTION_ERROR
        elif any(term in error_str for term in ["timeout", "timed out"]):
            return ErrorType.TIMEOUT_ERROR
        elif any(term in error_str for term in ["validation", "invalid", "missing required"]):
            return ErrorType.VALIDATION_ERROR
        elif any(term in error_str for term in ["rate limit", "too many requests", "429"]):
            return ErrorType.RATE_LIMIT_ERROR
        elif any(term in error_str for term in ["unauthorized", "authentication", "401", "403"]):
            return ErrorType.AUTHENTICATION_ERROR
        elif any(term in error_str for term in ["server error", "500", "502", "503"]):
            return ErrorType.SERVER_ERROR
        else:
            return ErrorType.UNKNOWN_ERROR
    
    def create_error_context(
        self,
        error: Exception,
        tool_name: Optional[str] = None,
        parameters: Optional[Dict[str, Any]] = None,
        retry_count: int = 0
    ) -> ErrorContext:
        """
        Create error context from an exception.
        
        Args:
            error: The exception
            tool_name: Name of the tool that failed
            parameters: Parameters used
            retry_count: Number of retries attempted
            
        Returns:
            Error context
        """
        error_type = self.classify_error(error)
        
        return ErrorContext(
            error_type=error_type,
            error_message=str(error),
            timestamp=datetime.now(),
            tool_name=tool_name,
            parameters=parameters,
            stack_trace=traceback.format_exc(),
            retry_count=retry_count
        )
    
    async def handle_error(
        self,
        error_context: ErrorContext,
        retry_func: Optional[Callable[[], Awaitable[Any]]] = None,
        fallback_func: Optional[Callable[[], Awaitable[Any]]] = None
    ) -> Tuple[bool, Any]:
        """
        Handle an error with appropriate recovery strategy.
        
        Args:
            error_context: Context about the error
            retry_func: Function to retry the operation
            fallback_func: Function for fallback operation
            
        Returns:
            Tuple of (success, result)
        """
        # Record error
        self._record_error(error_context)
        
        # Get recovery plan
        recovery_plan = self.error_strategies.get(
            error_context.error_type,
            self.error_strategies[ErrorType.UNKNOWN_ERROR]
        )
        
        # Execute recovery plan
        success, result = await self._execute_recovery_plan(
            error_context,
            recovery_plan,
            retry_func,
            fallback_func
        )
        
        # Update statistics
        if success:
            self.recovery_success_counts[recovery_plan.primary_action] += 1
        else:
            self.recovery_failure_counts[recovery_plan.primary_action] += 1
        
        # Execute callbacks
        await self._execute_callbacks(error_context.error_type, error_context, success)
        
        return success, result
    
    async def _execute_recovery_plan(
        self,
        error_context: ErrorContext,
        recovery_plan: RecoveryPlan,
        retry_func: Optional[Callable[[], Awaitable[Any]]],
        fallback_func: Optional[Callable[[], Awaitable[Any]]]
    ) -> Tuple[bool, Any]:
        """Execute a recovery plan."""
        # Try primary action
        success, result = await self._execute_recovery_action(
            recovery_plan.primary_action,
            error_context,
            recovery_plan,
            retry_func,
            fallback_func
        )
        
        if success:
            return True, result
        
        # Try fallback actions
        for action in recovery_plan.fallback_actions:
            success, result = await self._execute_recovery_action(
                action,
                error_context,
                recovery_plan,
                retry_func,
                fallback_func
            )
            
            if success:
                return True, result
        
        return False, None
    
    async def _execute_recovery_action(
        self,
        action: RecoveryAction,
        error_context: ErrorContext,
        recovery_plan: RecoveryPlan,
        retry_func: Optional[Callable[[], Awaitable[Any]]],
        fallback_func: Optional[Callable[[], Awaitable[Any]]]
    ) -> Tuple[bool, Any]:
        """Execute a specific recovery action."""
        logger.info(f"Executing recovery action: {action.value}")
        
        if action == RecoveryAction.RETRY:
            if retry_func and error_context.retry_count < recovery_plan.max_retries:
                try:
                    result = await retry_func()
                    return True, result
                except Exception as e:
                    logger.error(f"Retry failed: {e}")
                    return False, None
        
        elif action == RecoveryAction.RETRY_WITH_BACKOFF:
            if retry_func and error_context.retry_count < recovery_plan.max_retries:
                delay = recovery_plan.retry_delay * (
                    recovery_plan.backoff_factor ** error_context.retry_count
                )
                await asyncio.sleep(delay)
                try:
                    result = await retry_func()
                    return True, result
                except Exception as e:
                    logger.error(f"Retry with backoff failed: {e}")
                    return False, None
        
        elif action == RecoveryAction.FALLBACK:
            if fallback_func:
                try:
                    result = await fallback_func()
                    return True, result
                except Exception as e:
                    logger.error(f"Fallback failed: {e}")
                    return False, None
        
        elif action == RecoveryAction.CIRCUIT_BREAK:
            if error_context.tool_name:
                breaker = self._get_circuit_breaker(error_context.tool_name)
                breaker.call_failed()
                return False, None
        
        elif action == RecoveryAction.FAIL:
            return False, None
        
        # Actions that require external implementation
        elif action in [RecoveryAction.REFRESH_AUTH, RecoveryAction.CLEAR_CACHE, RecoveryAction.RESTART_CONNECTION]:
            logger.warning(f"Recovery action {action.value} requires external implementation")
            return False, None
        
        return False, None
    
    def _get_circuit_breaker(self, tool_name: str) -> CircuitBreaker:
        """Get or create circuit breaker for a tool."""
        if tool_name not in self.circuit_breakers:
            self.circuit_breakers[tool_name] = CircuitBreaker()
        return self.circuit_breakers[tool_name]
    
    def _record_error(self, error_context: ErrorContext):
        """Record error in history."""
        self.error_history.append(error_context)
        self.error_counts[error_context.error_type] += 1
        
        # Keep only recent history (last 1000 errors)
        if len(self.error_history) > 1000:
            self.error_history = self.error_history[-1000:]
    
    async def _execute_callbacks(
        self,
        error_type: ErrorType,
        error_context: ErrorContext,
        recovery_success: bool
    ):
        """Execute registered callbacks for an error type."""
        if error_type in self.recovery_callbacks:
            for callback in self.recovery_callbacks[error_type]:
                try:
                    await callback(error_context, recovery_success)
                except Exception as e:
                    logger.error(f"Callback execution failed: {e}")
    
    def register_callback(
        self,
        error_type: ErrorType,
        callback: Callable[[ErrorContext, bool], Awaitable[None]]
    ):
        """
        Register a callback for an error type.
        
        Args:
            error_type: Type of error to register for
            callback: Async callback function
        """
        if error_type not in self.recovery_callbacks:
            self.recovery_callbacks[error_type] = []
        self.recovery_callbacks[error_type].append(callback)
    
    def get_statistics(self) -> Dict[str, Any]:
        """
        Get error recovery statistics.
        
        Returns:
            Statistics dictionary
        """
        return {
            "error_counts": {et.value: count for et, count in self.error_counts.items()},
            "recovery_success_counts": {ra.value: count for ra, count in self.recovery_success_counts.items()},
            "recovery_failure_counts": {ra.value: count for ra, count in self.recovery_failure_counts.items()},
            "circuit_breakers": {
                name: breaker.get_status()
                for name, breaker in self.circuit_breakers.items()
            },
            "recent_errors": [
                {
                    "type": ec.error_type.value,
                    "message": ec.error_message,
                    "timestamp": ec.timestamp.isoformat(),
                    "tool": ec.tool_name
                }
                for ec in self.error_history[-10:]
            ]
        }
    
    def reset_statistics(self):
        """Reset all statistics."""
        self.error_counts = {et: 0 for et in ErrorType}
        self.recovery_success_counts = {ra: 0 for ra in RecoveryAction}
        self.recovery_failure_counts = {ra: 0 for ra in RecoveryAction}
        self.error_history.clear()
        logger.info("Error recovery statistics reset")


# Tuple import for type hints
from typing import Tuple
