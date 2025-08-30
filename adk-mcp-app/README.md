# ADK MCP Database Assistant

An AI-powered database assistant using Google's Agent Development Kit (ADK) with dual MCP (Model Context Protocol) architecture. This application provides a conversational interface for querying and analyzing e-commerce database data through natural language.

## Features

- **🤖 Google ADK Integration**: Leverages Google's Gemini AI for natural language understanding
- **🔧 Dual MCP Architecture**: 
  - Primary MCP for safe, predefined database operations
  - Dynamic MCP for complex, AI-generated SQL queries
- **💬 Real-time Chat Interface**: WebSocket-based communication for instant responses
- **📊 Data Visualization**: Support for charts and tabular data display
- **🔍 Smart Query Processing**: Automatically selects the best tool for each query
- **📈 Analytics Tools**: Built-in sales reporting and customer analytics

## Architecture

```
┌─────────────────┐    ┌──────────────────┐    ┌─────────────────┐
│   Web Chat UI   │◄──►│   ADK Agent      │◄──►│  Primary MCP    │
│   (WebSocket)   │    │   (FastAPI)      │    │   (Safe Tools)  │
└─────────────────┘    └──────────────────┘    └─────────────────┘
                                │
                                ▼
                       ┌─────────────────┐
                       │  Dynamic MCP     │
                       │ (Text-to-SQL)    │
                       └─────────────────┘
```

## Prerequisites

- Python 3.11 or higher
- Google API Key (Gemini API access)
- Running MCP servers (primary and optionally dynamic)
- SQLite database with e-commerce schema

## Installation

1. **Clone the repository**:
```bash
cd adk-mcp-app
```

2. **Install dependencies**:
```bash
pip install -r requirements.txt
```

3. **Configure environment variables**:
```bash
cp .env.example .env
```

Edit `.env` and add your configuration:
```env
# Google AI Configuration
GOOGLE_API_KEY=your_google_api_key_here

# MCP Server URLs
MCP_TOOLBOX_URL=http://127.0.0.1:5000
MCP_DYNAMIC_URL=http://127.0.0.1:8000  # Optional

# Database Configuration
DB_PATH=../db/app.db

# Server Configuration
HOST=0.0.0.0
PORT=8080
DEBUG=true
```

## Running the Application

### 1. Start MCP Servers

First, ensure your MCP servers are running:

**Primary MCP Server** (from parent directory):
```bash
cd ..
python app.py  # Runs on port 5004
```

**Dynamic MCP Server** (optional, from parent directory):
```bash
cd dynamic_mcp
python -m uvicorn server:app --port 8000
```

### 2. Start ADK Agent Server

```bash
python main.py
```

The server will start on `http://localhost:8080`

### 3. Access the Chat Interface

Open your browser and navigate to:
```
http://localhost:8080
```

## Available Tools

### Customer Management
- **search_customers**: Find customers by name pattern
- **get_customer_orders**: Get all orders for a specific customer
- **get_customer_value_by_status**: Calculate customer spending by order status

### Product Management
- **list_products**: View all products with pricing and stock information

### Order Management
- **create_order**: Create new orders for customers
- **add_order_item**: Add items to existing orders
- **update_order_status**: Update order status (pending, paid, shipped, cancelled)

### Analytics
- **sales_by_month**: Generate monthly sales reports with visualization
- **execute_dynamic_sql**: Handle complex queries using natural language (requires Dynamic MCP)

## Usage Examples

### Basic Queries
- "Show me all products"
- "Find customers named John"
- "What are the orders for customer ID 29?"

### Analytics Queries
- "Show me sales by month"
- "What's the total revenue for the last 12 months?"
- "Display sales trends from June 2024 to October 2024"

### Complex Queries (with Dynamic MCP)
- "Which customers from California spent more than $500?"
- "Show me the top 5 customers by total sales"
- "Find all orders with products that have low stock"

## API Endpoints

### REST API
- `POST /api/chat` - Send a chat message and receive response
- `GET /health` - Check service health status

### WebSocket
- `WS /ws/{client_id}` - Real-time chat communication

### Web Interface
- `GET /` - Main chat interface

## Development

### Project Structure
```
adk-mcp-app/
├── src/
│   ├── __init__.py
│   ├── mcp_client.py        # MCP client implementation
│   ├── agent_tools.py       # ADK tool definitions
│   └── database_agent.py    # Main agent logic
├── main.py                   # FastAPI server
├── requirements.txt          # Python dependencies
├── .env.example             # Environment variables template
└── README.md                # This file
```

### Testing

Run the health check:
```bash
curl http://localhost:8080/health
```

Test the chat API:
```bash
curl -X POST http://localhost:8080/api/chat \
  -H "Content-Type: application/json" \
  -d '{"message": "Show me all products"}'
```

## Troubleshooting

### Common Issues

1. **"Agent not initialized" error**
   - Check that your Google API key is correctly set in `.env`
   - Verify MCP servers are running and accessible

2. **"No tools loaded" warning**
   - Ensure the MCP_TOOLBOX_URL is correct
   - Check that the primary MCP server is running

3. **WebSocket connection issues**
   - Check firewall settings
   - Ensure the port 8080 is not blocked

### Debug Mode

Enable debug mode in `.env`:
```env
DEBUG=true
```

This will provide detailed logging of:
- Tool invocations
- MCP communications
- Agent decision-making process

## Advanced Configuration

### Custom Agent Instructions

Modify the system instruction in `src/database_agent.py` to customize the agent's behavior:

```python
self.system_instruction = """
Your custom instructions here...
"""
```

### Adding New Tools

Add new tool definitions in `src/agent_tools.py`:

```python
types.Tool(
    function_declarations=[
        types.FunctionDeclaration(
            name="your_tool_name",
            description="Tool description",
            parameters=types.Schema(...)
        )
    ]
)
```

## Security Considerations

- **API Keys**: Never commit API keys to version control
- **SQL Injection**: The system validates all SQL queries before execution
- **CORS**: Configure appropriate origins in production
- **Authentication**: Implement user authentication for production use

## License

This project is part of the MCP Database Toolbox ecosystem.

## Support

For issues or questions:
1. Check the troubleshooting section
2. Review the MCP server logs
3. Enable debug mode for detailed diagnostics

## Future Enhancements

- [ ] Voice input/output support
- [ ] File upload for data import
- [ ] Export results to CSV/Excel
- [ ] User authentication and sessions
- [ ] Advanced charting with D3.js
- [ ] Query history and favorites
- [ ] Multi-language support
