#!/bin/bash

# ADK MCP Database Assistant - Quick Start Script

echo "🚀 Starting ADK MCP Database Assistant..."
echo ""

# Check if .env file exists
if [ ! -f .env ]; then
    echo "⚠️  No .env file found. Creating from .env.example..."
    cp .env.example .env
    echo "📝 Please edit .env file with your configuration:"
    echo "   - Add your Google/Gemini API key"
    echo "   - Configure MCP server URLs"
    echo "   - Set database path"
    echo ""
    echo "Then run this script again."
    exit 1
fi

# Check if virtual environment exists
if [ ! -d "venv" ]; then
    echo "📦 Creating virtual environment..."
    python3 -m venv venv
fi

# Activate virtual environment
echo "🔧 Activating virtual environment..."
source venv/bin/activate

# Install/upgrade dependencies
echo "📚 Installing dependencies..."
pip install -q --upgrade pip
pip install -q -r requirements.txt

# Check if MCP servers are running
echo ""
echo "🔍 Checking MCP servers..."

# Check primary MCP
PRIMARY_MCP_URL=${MCP_TOOLBOX_URL:-http://127.0.0.1:5000}
if curl -s -o /dev/null -w "%{http_code}" "$PRIMARY_MCP_URL/api/toolset" | grep -q "200"; then
    echo "✅ Primary MCP server is running at $PRIMARY_MCP_URL"
else
    echo "⚠️  Primary MCP server is not responding at $PRIMARY_MCP_URL"
    echo "   Please start it with: python app.py (from parent directory)"
fi

# Check dynamic MCP (optional)
if [ ! -z "$MCP_DYNAMIC_URL" ]; then
    if curl -s -o /dev/null -w "%{http_code}" "$MCP_DYNAMIC_URL/api/toolset" | grep -q "200"; then
        echo "✅ Dynamic MCP server is running at $MCP_DYNAMIC_URL"
    else
        echo "⚠️  Dynamic MCP server is not responding at $MCP_DYNAMIC_URL"
        echo "   This is optional but enables advanced SQL generation"
    fi
fi

echo ""
echo "🎯 Starting ADK Agent Server..."
echo "   Access the chat interface at: http://localhost:8080"
echo "   Press Ctrl+C to stop the server"
echo ""

# Start the server
python main.py
