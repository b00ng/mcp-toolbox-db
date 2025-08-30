import os
import asyncio
from typing import Dict, List, Any, Optional
from datetime import datetime, timezone
from google import genai
from google.genai import types
from .mcp_client import MCPClient
from .agent_tools import DatabaseAgentTools

class DatabaseAgent:
    """ADK Database Agent with MCP integration"""
    
    def __init__(self, api_key: str, primary_mcp_url: str, dynamic_mcp_url: Optional[str] = None):
        # Initialize Gemini client
        self.client = genai.Client(api_key=api_key)
        self.model_name = 'gemini-2.0-flash-exp'
        
        # Initialize MCP clients
        self.primary_mcp = MCPClient(primary_mcp_url, name="primary_mcp")
        self.dynamic_mcp = MCPClient(dynamic_mcp_url, name="dynamic_mcp") if dynamic_mcp_url else None
        
        # Initialize agent tools
        self.agent_tools = DatabaseAgentTools(self.primary_mcp, self.dynamic_mcp)
        
        # Agent configuration
        self.agent_name = os.getenv('AGENT_NAME', 'DatabaseAssistant')
        self.agent_description = os.getenv('AGENT_DESCRIPTION', 
            'AI assistant for e-commerce database queries and analytics')
        
        # Conversation history
        self.conversation_history = []
        
        # System instruction for the agent
        self.system_instruction = """
You are a helpful and precise e-commerce database assistant. Your primary function is to help users query and analyze data about customers, products, and orders.

## Available Tools:

### Customer Management:
- **search_customers**: Find customers by name pattern
- **get_customer_orders**: Get all orders for a specific customer
- **get_customer_value_by_status**: Calculate customer spending by order status

### Product Management:
- **list_products**: View all products with pricing and stock

### Order Management:
- **create_order**: Create new orders
- **add_order_item**: Add items to orders
- **update_order_status**: Update order status (pending, paid, shipped, cancelled)

### Analytics:
- **sales_by_month**: Generate monthly sales reports with charts
- **execute_dynamic_sql**: Handle complex queries using natural language (when available)

## Guidelines:
1. Always use the most specific tool for the task
2. For sales analytics and time-series data, use sales_by_month
3. For complex or ad-hoc queries, use execute_dynamic_sql
4. Provide clear, formatted responses with relevant data
5. When showing results, summarize key findings
6. If a query returns many results, highlight the most important ones
7. Be proactive in suggesting related queries or insights

## Response Format:
- Use tables for structured data when appropriate
- Include totals and summaries for numerical data
- Highlight important patterns or anomalies
- Suggest follow-up questions when relevant
"""

    async def initialize(self) -> bool:
        """Initialize the agent and load MCP tools"""
        try:
            print(f"[{self.agent_name}] Initializing agent...")
            
            # Load primary MCP tools
            success = await self.primary_mcp.load_tools()
            if not success:
                print(f"[{self.agent_name}] Warning: Failed to load primary MCP tools")
                return False
            
            # Load dynamic MCP tools if available
            if self.dynamic_mcp:
                dynamic_success = await self.dynamic_mcp.load_tools()
                if not dynamic_success:
                    print(f"[{self.agent_name}] Warning: Failed to load dynamic MCP tools")
            
            print(f"[{self.agent_name}] ✓ Agent initialized successfully")
            print(f"[{self.agent_name}] Available tools: {self.agent_tools.get_available_tool_names()}")
            
            return True
            
        except Exception as e:
            print(f"[{self.agent_name}] Error during initialization: {e}")
            return False

    async def process_message(self, user_message: str, session_id: Optional[str] = None) -> Dict[str, Any]:
        """Process a user message and return the agent's response"""
        
        try:
            # Add user message to history
            self.conversation_history.append({
                "role": "user",
                "content": user_message,
                "timestamp": datetime.now(timezone.utc).isoformat()
            })
            
            # Prepare tools for the agent
            tools = self.agent_tools.get_tools_for_agent()
            
            # Configure the generation
            config = types.GenerateContentConfig(
                system_instruction=self.system_instruction,
                tools=tools,
                tool_config=types.ToolConfig(
                    function_calling_config=types.FunctionCallingConfig(
                        mode='AUTO'
                    )
                ),
                temperature=0.7,
                top_p=0.95,
                top_k=40,
                max_output_tokens=2048
            )
            
            # Generate response with tool calling
            response = self.client.models.generate_content(
                model=self.model_name,
                contents=[user_message],
                config=config
            )
            
            # Check for function calls
            function_calls = getattr(response, 'function_calls', [])
            
            if function_calls:
                # Execute the function call
                function_call = function_calls[0]
                tool_name = function_call.name
                tool_args = dict(function_call.args) if function_call.args else {}
                
                print(f"[{self.agent_name}] Executing tool: {tool_name} with args: {tool_args}")
                
                # Execute the tool
                tool_result = await self.agent_tools.execute_tool(tool_name, tool_args)
                
                # Format the response based on tool result
                if tool_result.get('status') == 'success':
                    response_data = self._format_tool_response(tool_name, tool_result)
                else:
                    response_data = {
                        "type": "error",
                        "message": f"Tool execution failed: {tool_result.get('error')}",
                        "tool_name": tool_name,
                        "parameters": tool_args
                    }
                
                # Add assistant response to history
                self.conversation_history.append({
                    "role": "assistant",
                    "content": response_data,
                    "tool_used": tool_name,
                    "timestamp": datetime.now(timezone.utc).isoformat()
                })
                
                return response_data
                
            else:
                # No tool call, return text response
                text_response = getattr(response, 'text', 'I understand your request, but I need more specific information to help you.')
                
                response_data = {
                    "type": "text",
                    "message": text_response
                }
                
                # Add to history
                self.conversation_history.append({
                    "role": "assistant",
                    "content": text_response,
                    "timestamp": datetime.now(timezone.utc).isoformat()
                })
                
                return response_data
                
        except Exception as e:
            print(f"[{self.agent_name}] Error processing message: {e}")
            import traceback
            traceback.print_exc()
            
            return {
                "type": "error",
                "message": f"Error processing your request: {str(e)}"
            }

    def _format_tool_response(self, tool_name: str, tool_result: Dict[str, Any]) -> Dict[str, Any]:
        """Format tool execution results for the response"""
        
        results = tool_result.get('results', [])
        row_count = tool_result.get('row_count', 0)
        
        # Special formatting for sales_by_month (chart data)
        if tool_name == 'sales_by_month':
            return self._format_sales_chart(results, tool_result.get('parameters', {}))
        
        # Special formatting for dynamic SQL
        elif tool_name == 'execute_dynamic_sql':
            return {
                "type": "dynamic_sql",
                "message": f"Executed dynamic query: {tool_result.get('natural_query')}",
                "sql": tool_result.get('generated_sql'),
                "results": results,
                "row_count": row_count,
                "timing_ms": tool_result.get('timing_ms')
            }
        
        # Default formatting for other tools
        else:
            # Create a summary message
            if row_count == 0:
                summary = f"No results found for {tool_name}"
            elif row_count == 1:
                summary = f"Found 1 result for {tool_name}"
            else:
                summary = f"Found {row_count} results for {tool_name}"
            
            return {
                "type": "data",
                "message": summary,
                "tool_name": tool_name,
                "results": results,
                "row_count": row_count,
                "parameters": tool_result.get('parameters', {})
            }

    def _format_sales_chart(self, results: List[Dict], parameters: Dict) -> Dict[str, Any]:
        """Format sales data for chart visualization"""
        
        # Transform results into chart-friendly format
        chart_data = []
        total_sales = 0
        
        for row in results:
            if isinstance(row, dict):
                month = row.get('ym', '')
                amount = row.get('total_cents', 0)
                
                chart_data.append({
                    "x": f"{month}-01T00:00:00Z",
                    "y": amount
                })
                total_sales += amount
        
        # Calculate summary statistics
        if chart_data:
            peak = max(chart_data, key=lambda x: x['y'])
            trough = min(chart_data, key=lambda x: x['y'])
            avg_sales = total_sales / len(chart_data)
            
            summary = (
                f"Total sales: {total_sales/100:,.2f} {parameters.get('currency', 'VND')} "
                f"over {len(chart_data)} months. "
                f"Peak: {peak['y']/100:,.2f} in {peak['x'][:7]}. "
                f"Lowest: {trough['y']/100:,.2f} in {trough['x'][:7]}. "
                f"Average: {avg_sales/100:,.2f} per month."
            )
        else:
            summary = "No sales data available for the selected period."
        
        return {
            "type": "chart",
            "message": "Sales by Month Analysis",
            "chart_type": "bar",
            "data": chart_data,
            "summary": summary,
            "spec": {
                "title": "Monthly Sales",
                "xLabel": "Month",
                "yLabel": f"Sales ({parameters.get('currency', 'VND')})",
                "currency": parameters.get('currency', 'VND')
            },
            "parameters": parameters
        }

    def get_conversation_history(self, limit: int = 10) -> List[Dict[str, Any]]:
        """Get recent conversation history"""
        return self.conversation_history[-limit:] if limit else self.conversation_history

    def clear_conversation_history(self):
        """Clear the conversation history"""
        self.conversation_history = []
        print(f"[{self.agent_name}] Conversation history cleared")

    async def health_check(self) -> Dict[str, Any]:
        """Check the health status of the agent and its dependencies"""
        
        health_status = {
            "agent": self.agent_name,
            "status": "healthy",
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "components": {}
        }
        
        # Check primary MCP
        primary_health = await self.primary_mcp.health_check()
        health_status["components"]["primary_mcp"] = {
            "url": self.primary_mcp.base_url,
            "connected": primary_health,
            "tools_loaded": len(self.primary_mcp.get_available_tools())
        }
        
        # Check dynamic MCP if available
        if self.dynamic_mcp:
            dynamic_health = await self.dynamic_mcp.health_check()
            health_status["components"]["dynamic_mcp"] = {
                "url": self.dynamic_mcp.base_url,
                "connected": dynamic_health,
                "tools_loaded": len(self.dynamic_mcp.get_available_tools())
            }
        
        # Check Gemini API
        try:
            # Simple test to check if API is accessible
            test_response = self.client.models.generate_content(
                model=self.model_name,
                contents=["test"],
                config=types.GenerateContentConfig(max_output_tokens=1)
            )
            health_status["components"]["gemini_api"] = {
                "connected": True,
                "model": self.model_name
            }
        except Exception as e:
            health_status["components"]["gemini_api"] = {
                "connected": False,
                "error": str(e)
            }
            health_status["status"] = "degraded"
        
        # Overall status
        if not primary_health:
            health_status["status"] = "unhealthy"
        elif self.dynamic_mcp and not dynamic_health:
            health_status["status"] = "degraded"
        
        return health_status
