"""
MCP Fallback Handler - Provides fallback mechanisms when MCP servers are unavailable
"""

import sqlite3
import json
import re
from typing import Dict, List, Any, Optional
from datetime import datetime, timezone
from pathlib import Path

class MCPFallbackHandler:
    """Handles fallback operations when MCP servers are unavailable"""
    
    def __init__(self, db_path: str = None):
        self.db_path = db_path or self._find_database()
        self.fallback_mode = False
        self.fallback_reasons = []
        
        # Define fallback implementations for critical tools
        self.fallback_tools = {
            'list_products': self._fallback_list_products,
            'search_customers': self._fallback_search_customers,
            'get_customer_orders': self._fallback_get_customer_orders,
            'get_customer_value_by_status': self._fallback_customer_value_by_status,
            'sales_by_month': self._fallback_sales_by_month
        }
        
    def _find_database(self) -> str:
        """Try to find the database file"""
        possible_paths = [
            '../db/app.db',
            'db/app.db',
            './app.db',
            '../../db/app.db'
        ]
        
        for path in possible_paths:
            if Path(path).exists():
                return str(Path(path).resolve())
        
        # Default path
        return '../db/app.db'
    
    def enable_fallback(self, reason: str):
        """Enable fallback mode"""
        self.fallback_mode = True
        self.fallback_reasons.append({
            'reason': reason,
            'timestamp': datetime.now(timezone.utc).isoformat()
        })
        print(f"[Fallback] Enabled: {reason}")
    
    def disable_fallback(self):
        """Disable fallback mode"""
        self.fallback_mode = False
        self.fallback_reasons = []
        print("[Fallback] Disabled")
    
    def is_tool_supported(self, tool_name: str) -> bool:
        """Check if a tool has fallback support"""
        return tool_name in self.fallback_tools
    
    async def execute_fallback_tool(self, tool_name: str, params: Dict[str, Any]) -> Dict[str, Any]:
        """Execute a tool using fallback implementation"""
        if not self.is_tool_supported(tool_name):
            return {
                'status': 'error',
                'error': f'No fallback implementation for tool: {tool_name}',
                'fallback': True
            }
        
        try:
            # Execute the fallback function
            fallback_func = self.fallback_tools[tool_name]
            result = fallback_func(params)
            
            # Add fallback metadata
            result['fallback'] = True
            result['fallback_reason'] = self.fallback_reasons[-1] if self.fallback_reasons else 'Unknown'
            
            return result
            
        except Exception as e:
            return {
                'status': 'error',
                'error': f'Fallback execution failed: {str(e)}',
                'fallback': True,
                'tool_name': tool_name
            }
    
    def _execute_query(self, query: str, params: tuple = ()) -> List[Dict[str, Any]]:
        """Execute a database query"""
        try:
            conn = sqlite3.connect(self.db_path)
            conn.row_factory = sqlite3.Row
            cursor = conn.cursor()
            cursor.execute(query, params)
            rows = cursor.fetchall()
            conn.close()
            
            return [dict(row) for row in rows]
        except Exception as e:
            print(f"[Fallback] Database error: {e}")
            return []
    
    def _fallback_list_products(self, params: Dict[str, Any]) -> Dict[str, Any]:
        """Fallback implementation for list_products"""
        query = """
            SELECT id, sku, name, price_cents, stock
            FROM products
            ORDER BY id ASC
        """
        
        results = self._execute_query(query)
        
        return {
            'status': 'success',
            'results': results,
            'row_count': len(results)
        }
    
    def _fallback_search_customers(self, params: Dict[str, Any]) -> Dict[str, Any]:
        """Fallback implementation for search_customers"""
        name_pattern = params.get('name_pattern', '%')
        if not name_pattern:
            name_pattern = '%'
        elif not name_pattern.startswith('%'):
            name_pattern = f'%{name_pattern}%'
        
        limit = params.get('limit', 10)
        
        query = """
            SELECT id, name, email, created_at
            FROM customers
            WHERE lower(name) LIKE lower(?)
            ORDER BY created_at DESC
            LIMIT ?
        """
        
        results = self._execute_query(query, (name_pattern, limit))
        
        return {
            'status': 'success',
            'results': results,
            'row_count': len(results)
        }
    
    def _fallback_get_customer_orders(self, params: Dict[str, Any]) -> Dict[str, Any]:
        """Fallback implementation for get_customer_orders"""
        customer_id = params.get('customer_id')
        
        if not customer_id:
            return {
                'status': 'error',
                'error': 'customer_id is required'
            }
        
        query = """
            SELECT o.id AS order_id, o.status, o.created_at,
                   p.name AS product, oi.quantity, oi.price_cents
            FROM orders o
            JOIN order_items oi ON oi.order_id = o.id
            JOIN products p ON p.id = oi.product_id
            WHERE o.customer_id = ?
            ORDER BY o.created_at DESC, o.id DESC
        """
        
        results = self._execute_query(query, (customer_id,))
        
        return {
            'status': 'success',
            'results': results,
            'row_count': len(results)
        }
    
    def _fallback_customer_value_by_status(self, params: Dict[str, Any]) -> Dict[str, Any]:
        """Fallback implementation for get_customer_value_by_status"""
        customer_id = params.get('customer_id')
        
        if not customer_id:
            return {
                'status': 'error',
                'error': 'customer_id is required'
            }
        
        query = """
            SELECT
                o.status,
                SUM(oi.quantity * oi.price_cents) AS total_value_cents
            FROM orders o
            JOIN order_items oi ON oi.order_id = o.id
            WHERE o.customer_id = ?
            GROUP BY o.status
            ORDER BY o.status
        """
        
        results = self._execute_query(query, (customer_id,))
        
        return {
            'status': 'success',
            'results': results,
            'row_count': len(results)
        }
    
    def _fallback_sales_by_month(self, params: Dict[str, Any]) -> Dict[str, Any]:
        """Fallback implementation for sales_by_month"""
        start_date = params.get('start_date', '2024-01-01T00:00:00Z')
        end_date = params.get('end_date', '2024-12-31T23:59:59Z')
        
        # Clean up date format
        start_date = start_date.replace('T', ' ').replace('Z', '')
        end_date = end_date.replace('T', ' ').replace('Z', '')
        
        query = """
            SELECT
                strftime('%Y-%m', o.created_at) AS ym,
                SUM(oi.quantity * oi.price_cents) AS total_cents
            FROM orders o
            JOIN order_items oi ON oi.order_id = o.id
            WHERE o.created_at BETWEEN ? AND ?
            GROUP BY ym
            ORDER BY ym
        """
        
        results = self._execute_query(query, (start_date, end_date))
        
        return {
            'status': 'success',
            'results': results,
            'row_count': len(results)
        }
    
    def get_fallback_status(self) -> Dict[str, Any]:
        """Get current fallback status"""
        return {
            'enabled': self.fallback_mode,
            'reasons': self.fallback_reasons,
            'supported_tools': list(self.fallback_tools.keys()),
            'database_path': self.db_path,
            'database_exists': Path(self.db_path).exists() if self.db_path else False
        }


class ToolValidator:
    """Validates tool parameters before execution"""
    
    def __init__(self):
        # Define parameter schemas for each tool
        self.tool_schemas = {
            'search_customers': {
                'required': [],
                'optional': ['name_pattern', 'limit'],
                'types': {
                    'name_pattern': str,
                    'limit': int
                },
                'defaults': {
                    'name_pattern': '%',
                    'limit': 10
                },
                'validators': {
                    'limit': lambda x: 1 <= x <= 1000
                }
            },
            'get_customer_orders': {
                'required': ['customer_id'],
                'optional': [],
                'types': {
                    'customer_id': int
                },
                'validators': {
                    'customer_id': lambda x: x > 0
                }
            },
            'get_customer_value_by_status': {
                'required': ['customer_id'],
                'optional': [],
                'types': {
                    'customer_id': int
                },
                'validators': {
                    'customer_id': lambda x: x > 0
                }
            },
            'list_products': {
                'required': [],
                'optional': [],
                'types': {},
                'validators': {}
            },
            'create_order': {
                'required': ['customer_id'],
                'optional': [],
                'types': {
                    'customer_id': int
                },
                'validators': {
                    'customer_id': lambda x: x > 0
                }
            },
            'add_order_item': {
                'required': ['order_id', 'product_id', 'quantity'],
                'optional': [],
                'types': {
                    'order_id': int,
                    'product_id': int,
                    'quantity': int
                },
                'validators': {
                    'order_id': lambda x: x > 0,
                    'product_id': lambda x: x > 0,
                    'quantity': lambda x: x >= 1
                }
            },
            'update_order_status': {
                'required': ['order_id', 'new_status'],
                'optional': [],
                'types': {
                    'order_id': int,
                    'new_status': str
                },
                'validators': {
                    'order_id': lambda x: x > 0,
                    'new_status': lambda x: x in ['pending', 'paid', 'shipped', 'cancelled']
                }
            },
            'sales_by_month': {
                'required': [],
                'optional': ['start_date', 'end_date', 'currency'],
                'types': {
                    'start_date': str,
                    'end_date': str,
                    'currency': str
                },
                'defaults': {
                    'currency': 'VND'
                },
                'validators': {
                    'start_date': lambda x: self._validate_iso_date(x),
                    'end_date': lambda x: self._validate_iso_date(x)
                }
            },
            'execute_dynamic_sql': {
                'required': ['natural_language_query'],
                'optional': ['max_results'],
                'types': {
                    'natural_language_query': str,
                    'max_results': int
                },
                'defaults': {
                    'max_results': 100
                },
                'validators': {
                    'max_results': lambda x: 1 <= x <= 1000
                }
            }
        }
    
    def _validate_iso_date(self, date_str: str) -> bool:
        """Validate ISO date format"""
        try:
            # Accept various ISO formats
            patterns = [
                r'^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z?$',
                r'^\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}$',
                r'^\d{4}-\d{2}-\d{2}$'
            ]
            return any(re.match(pattern, date_str) for pattern in patterns)
        except:
            return False
    
    def validate_tool_params(
        self, 
        tool_name: str, 
        params: Dict[str, Any]
    ) -> tuple[bool, Optional[str], Dict[str, Any]]:
        """
        Validate tool parameters
        Returns: (is_valid, error_message, processed_params)
        """
        
        # Check if tool is known
        if tool_name not in self.tool_schemas:
            # Unknown tool, pass through without validation
            return True, None, params
        
        schema = self.tool_schemas[tool_name]
        processed_params = {}
        
        # Check required parameters
        for param_name in schema['required']:
            if param_name not in params:
                return False, f"Missing required parameter: {param_name}", params
            processed_params[param_name] = params[param_name]
        
        # Process optional parameters
        for param_name in schema.get('optional', []):
            if param_name in params:
                processed_params[param_name] = params[param_name]
            elif param_name in schema.get('defaults', {}):
                processed_params[param_name] = schema['defaults'][param_name]
        
        # Type checking and conversion
        for param_name, param_value in processed_params.items():
            if param_name in schema.get('types', {}):
                expected_type = schema['types'][param_name]
                
                # Try to convert if needed
                if not isinstance(param_value, expected_type):
                    try:
                        if expected_type == int:
                            processed_params[param_name] = int(param_value)
                        elif expected_type == str:
                            processed_params[param_name] = str(param_value)
                        elif expected_type == float:
                            processed_params[param_name] = float(param_value)
                        elif expected_type == bool:
                            processed_params[param_name] = bool(param_value)
                    except (ValueError, TypeError):
                        return False, f"Parameter '{param_name}' must be of type {expected_type.__name__}", params
        
        # Custom validation
        for param_name, validator in schema.get('validators', {}).items():
            if param_name in processed_params:
                try:
                    if not validator(processed_params[param_name]):
                        return False, f"Parameter '{param_name}' failed validation", params
                except Exception as e:
                    return False, f"Parameter '{param_name}' validation error: {str(e)}", params
        
        # Check for unexpected parameters (warning only)
        all_params = set(schema.get('required', [])) | set(schema.get('optional', []))
        unexpected = set(params.keys()) - all_params
        if unexpected:
            print(f"[Validator] Warning: Unexpected parameters for {tool_name}: {unexpected}")
        
        return True, None, processed_params
    
    def get_tool_info(self, tool_name: str) -> Optional[Dict[str, Any]]:
        """Get information about a tool's parameters"""
        if tool_name not in self.tool_schemas:
            return None
        
        schema = self.tool_schemas[tool_name]
        return {
            'name': tool_name,
            'required_params': schema.get('required', []),
            'optional_params': schema.get('optional', []),
            'param_types': schema.get('types', {}),
            'defaults': schema.get('defaults', {}),
            'has_validators': bool(schema.get('validators', {}))
        }
