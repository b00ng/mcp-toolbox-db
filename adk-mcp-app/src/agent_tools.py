import json
from typing import Dict, List, Any, Optional
from google.genai import types
from .mcp_client import MCPClient

class DatabaseAgentTools:
    """ADK Agent Tools for database operations via MCP"""
    
    def __init__(self, primary_mcp: MCPClient, dynamic_mcp: Optional[MCPClient] = None):
        self.primary_mcp = primary_mcp
        self.dynamic_mcp = dynamic_mcp
        self.tools = []
        self._register_tools()
    
    def _register_tools(self):
        """Register all available tools for the ADK agent"""
        
        # Customer management tools
        self.tools.extend([
            types.Tool(
                function_declarations=[
                    types.FunctionDeclaration(
                        name="search_customers",
                        description="Search for customers by name pattern (case-insensitive)",
                        parameters=types.Schema(
                            type=types.Type.OBJECT,
                            properties={
                                "name_pattern": types.Schema(
                                    type=types.Type.STRING,
                                    description="Name pattern to search for (e.g., 'john', 'smith')"
                                ),
                                "limit": types.Schema(
                                    type=types.Type.INTEGER,
                                    description="Maximum number of results to return",
                                    default=10
                                )
                            },
                            required=["name_pattern"]
                        )
                    )
                ]
            ),
            
            types.Tool(
                function_declarations=[
                    types.FunctionDeclaration(
                        name="get_customer_orders",
                        description="Get all orders and items for a specific customer",
                        parameters=types.Schema(
                            type=types.Type.OBJECT,
                            properties={
                                "customer_id": types.Schema(
                                    type=types.Type.INTEGER,
                                    description="ID of the customer whose orders to fetch"
                                )
                            },
                            required=["customer_id"]
                        )
                    )
                ]
            ),
            
            types.Tool(
                function_declarations=[
                    types.FunctionDeclaration(
                        name="get_customer_value_by_status",
                        description="Calculate total value of orders for a customer grouped by status",
                        parameters=types.Schema(
                            type=types.Type.OBJECT,
                            properties={
                                "customer_id": types.Schema(
                                    type=types.Type.INTEGER,
                                    description="ID of the customer"
                                )
                            },
                            required=["customer_id"]
                        )
                    )
                ]
            )
        ])
        
        # Product management tools
        self.tools.append(
            types.Tool(
                function_declarations=[
                    types.FunctionDeclaration(
                        name="list_products",
                        description="List all products with price and stock information",
                        parameters=types.Schema(
                            type=types.Type.OBJECT,
                            properties={}
                        )
                    )
                ]
            )
        )
        
        # Order management tools
        self.tools.extend([
            types.Tool(
                function_declarations=[
                    types.FunctionDeclaration(
                        name="create_order",
                        description="Create a new order for a customer with pending status",
                        parameters=types.Schema(
                            type=types.Type.OBJECT,
                            properties={
                                "customer_id": types.Schema(
                                    type=types.Type.INTEGER,
                                    description="ID of the customer placing the order"
                                )
                            },
                            required=["customer_id"]
                        )
                    )
                ]
            ),
            
            types.Tool(
                function_declarations=[
                    types.FunctionDeclaration(
                        name="add_order_item",
                        description="Add an item to an existing order",
                        parameters=types.Schema(
                            type=types.Type.OBJECT,
                            properties={
                                "order_id": types.Schema(
                                    type=types.Type.INTEGER,
                                    description="ID of the order"
                                ),
                                "product_id": types.Schema(
                                    type=types.Type.INTEGER,
                                    description="ID of the product to add"
                                ),
                                "quantity": types.Schema(
                                    type=types.Type.INTEGER,
                                    description="Quantity of product to add (must be >= 1)"
                                )
                            },
                            required=["order_id", "product_id", "quantity"]
                        )
                    )
                ]
            ),
            
            types.Tool(
                function_declarations=[
                    types.FunctionDeclaration(
                        name="update_order_status",
                        description="Update order status (pending, paid, shipped, cancelled)",
                        parameters=types.Schema(
                            type=types.Type.OBJECT,
                            properties={
                                "order_id": types.Schema(
                                    type=types.Type.INTEGER,
                                    description="ID of the order to update"
                                ),
                                "new_status": types.Schema(
                                    type=types.Type.STRING,
                                    description="New status (pending, paid, shipped, cancelled)"
                                )
                            },
                            required=["order_id", "new_status"]
                        )
                    )
                ]
            )
        ])
        
        # Analytics tools
        self.tools.append(
            types.Tool(
                function_declarations=[
                    types.FunctionDeclaration(
                        name="sales_by_month",
                        description="Get sales data aggregated by month for chart generation",
                        parameters=types.Schema(
                            type=types.Type.OBJECT,
                            properties={
                                "start_date": types.Schema(
                                    type=types.Type.STRING,
                                    description="Start date in ISO format (e.g., 2024-01-01T00:00:00Z)"
                                ),
                                "end_date": types.Schema(
                                    type=types.Type.STRING,
                                    description="End date in ISO format (e.g., 2024-12-31T23:59:59Z)"
                                ),
                                "currency": types.Schema(
                                    type=types.Type.STRING,
                                    description="Currency code for display (e.g., VND, USD)",
                                    default="VND"
                                )
                            }
                        )
                    )
                ]
            )
        )
        
        # Dynamic SQL tool (if dynamic MCP is available)
        if self.dynamic_mcp:
            self.tools.append(
                types.Tool(
                    function_declarations=[
                        types.FunctionDeclaration(
                            name="execute_dynamic_sql",
                            description="Execute complex database queries from natural language",
                            parameters=types.Schema(
                                type=types.Type.OBJECT,
                                properties={
                                    "natural_language_query": types.Schema(
                                        type=types.Type.STRING,
                                        description="Natural language description of the query"
                                    ),
                                    "max_results": types.Schema(
                                        type=types.Type.INTEGER,
                                        description="Maximum number of results to return",
                                        default=100
                                    )
                                },
                                required=["natural_language_query"]
                            )
                        )
                    ]
                )
            )

    async def execute_tool(self, tool_name: str, parameters: Dict[str, Any]) -> Dict[str, Any]:
        """Execute a tool and return formatted results"""
        
        try:
            # Route dynamic SQL to dynamic MCP if available
            if tool_name == "execute_dynamic_sql" and self.dynamic_mcp:
                return await self._execute_dynamic_sql(parameters)
            
            # Route all other tools to primary MCP
            elif self.primary_mcp.is_tool_available(tool_name):
                result = await self.primary_mcp.invoke_tool(tool_name, parameters)
                return self._format_result(tool_name, result, parameters)
            
            else:
                return {
                    "status": "error",
                    "error": f"Tool '{tool_name}' not available",
                    "available_tools": self.get_available_tool_names()
                }
                
        except Exception as e:
            return {
                "status": "error",
                "error": f"Tool execution failed: {str(e)}",
                "tool_name": tool_name,
                "parameters": parameters
            }

    async def _execute_dynamic_sql(self, parameters: Dict[str, Any]) -> Dict[str, Any]:
        """Execute dynamic SQL using the dynamic MCP server"""
        
        natural_query = parameters.get('natural_language_query')
        max_results = parameters.get('max_results', 100)
        
        try:
            # Step 1: Generate SQL from natural language
            text2sql_result = await self.dynamic_mcp.invoke_tool('text2sql', {
                'natural_language_query': natural_query,
                'max_results': max_results
            })
            
            if text2sql_result.get('status') == 'error':
                return {
                    "status": "error",
                    "error": f"SQL generation failed: {text2sql_result.get('error')}",
                    "step": "text2sql"
                }
            
            # Extract preview_id or SQL from results
            results = text2sql_result.get('results', [])
            if not results:
                return {
                    "status": "error",
                    "error": "No SQL generated from natural language query",
                    "step": "text2sql"
                }
            
            first_result = results[0] if isinstance(results, list) else results
            
            # Step 2: Execute the generated SQL
            if isinstance(first_result, dict) and first_result.get('preview_id'):
                exec_params = {
                    'preview_id': first_result.get('preview_id'),
                    'max_results': max_results
                }
            else:
                exec_params = {
                    'sql': first_result.get('sql', ''),
                    'params': first_result.get('params', []),
                    'max_results': max_results
                }
            
            exec_result = await self.dynamic_mcp.invoke_tool('execute_generated_sql', exec_params)
            
            if exec_result.get('status') == 'error':
                return {
                    "status": "error",
                    "error": f"SQL execution failed: {exec_result.get('error')}",
                    "step": "execute_generated_sql"
                }
            
            # Format the final result
            exec_data = exec_result.get('results', [])
            final_data = exec_data[0] if isinstance(exec_data, list) and exec_data else exec_data
            
            return {
                "status": "success",
                "tool_name": "execute_dynamic_sql",
                "natural_query": natural_query,
                "generated_sql": final_data.get('executed_sql') or final_data.get('sql', ''),
                "results": final_data.get('results', []),
                "row_count": len(final_data.get('results', [])),
                "timing_ms": final_data.get('timing_ms'),
                "parameters": parameters
            }
            
        except Exception as e:
            return {
                "status": "error",
                "error": f"Dynamic SQL execution failed: {str(e)}",
                "tool_name": "execute_dynamic_sql",
                "parameters": parameters
            }

    def _format_result(self, tool_name: str, result: Dict[str, Any], parameters: Dict[str, Any]) -> Dict[str, Any]:
        """Format tool execution results for consistent response structure"""
        
        if result.get('status') == 'error':
            return {
                "status": "error",
                "error": result.get('error'),
                "tool_name": tool_name,
                "parameters": parameters
            }
        
        results_data = result.get('results', [])
        
        return {
            "status": "success",
            "tool_name": tool_name,
            "results": results_data,
            "row_count": len(results_data) if isinstance(results_data, list) else 1,
            "parameters": parameters
        }

    def get_available_tool_names(self) -> List[str]:
        """Get list of all available tool names"""
        tool_names = []
        for tool in self.tools:
            for func_decl in tool.function_declarations:
                tool_names.append(func_decl.name)
        return tool_names

    def get_tools_for_agent(self) -> List[types.Tool]:
        """Get all tools formatted for ADK agent registration"""
        return self.tools
