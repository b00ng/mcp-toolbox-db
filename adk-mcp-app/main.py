"""
ADK MCP Database Assistant - FastAPI Server with WebSocket Support
"""

import os
import json
import asyncio
from typing import Dict, Any, Optional
from datetime import datetime
from contextlib import asynccontextmanager

from fastapi import FastAPI, WebSocket, WebSocketDisconnect, HTTPException
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from dotenv import load_dotenv
from src.database_agent import DatabaseAgent
from src.mcp_orchestrator import MCPOrchestrator
from src.error_recovery import ErrorRecoveryManager

# Load environment variables
load_dotenv()

# Global agent instance
agent: Optional[DatabaseAgent] = None
orchestrator: Optional[MCPOrchestrator] = None
error_manager: Optional[ErrorRecoveryManager] = None

# WebSocket connection manager
class ConnectionManager:
    def __init__(self):
        self.active_connections: Dict[str, WebSocket] = {}

    async def connect(self, websocket: WebSocket, client_id: str):
        await websocket.accept()
        self.active_connections[client_id] = websocket
        print(f"[WebSocket] Client {client_id} connected. Total connections: {len(self.active_connections)}")

    def disconnect(self, client_id: str):
        if client_id in self.active_connections:
            del self.active_connections[client_id]
            print(f"[WebSocket] Client {client_id} disconnected. Total connections: {len(self.active_connections)}")

    async def send_message(self, message: Dict[str, Any], client_id: str):
        if client_id in self.active_connections:
            websocket = self.active_connections[client_id]
            await websocket.send_json(message)

    async def broadcast(self, message: Dict[str, Any]):
        for client_id, websocket in self.active_connections.items():
            try:
                await websocket.send_json(message)
            except Exception as e:
                print(f"[WebSocket] Error broadcasting to {client_id}: {e}")

manager = ConnectionManager()

# Request/Response models
class ChatMessage(BaseModel):
    message: str
    session_id: Optional[str] = None

class ChatResponse(BaseModel):
    type: str
    message: Optional[str] = None
    data: Optional[Any] = None
    timestamp: str

# Lifespan context manager for startup/shutdown
@asynccontextmanager
async def lifespan(app: FastAPI):
    # Startup
    global agent, orchestrator, error_manager
    print("[Server] Starting ADK MCP Database Assistant...")
    
    # Initialize the agent
    api_key = os.getenv('GOOGLE_API_KEY') or os.getenv('GEMINI_API_KEY')
    if not api_key:
        print("[Server] ERROR: No Google/Gemini API key found in environment")
        raise ValueError("API key required")
    
    primary_mcp_url = os.getenv('MCP_TOOLBOX_URL', 'http://127.0.0.1:5000')
    dynamic_mcp_url = os.getenv('MCP_DYNAMIC_URL')
    use_orchestrator = os.getenv('USE_ORCHESTRATOR', 'true').lower() == 'true'
    
    if use_orchestrator:
        print("[Server] Using enhanced orchestrator with failover and recovery")
        
        # Initialize orchestrator
        orchestrator = MCPOrchestrator(
            primary_url=primary_mcp_url,
            dynamic_url=dynamic_mcp_url,
            database_path=os.getenv('DATABASE_PATH', 'db/app.db')
        )
        
        # Initialize error recovery manager
        error_manager = ErrorRecoveryManager()
        
        # Initialize orchestrator
        success = await orchestrator.initialize()
        if not success:
            print("[Server] WARNING: Orchestrator initialization failed, falling back to basic mode")
            orchestrator = None
            error_manager = None
        else:
            print("[Server] ✓ Orchestrator initialized successfully")
    
    # Initialize the agent (always needed)
    agent = DatabaseAgent(
        api_key=api_key,
        primary_mcp_url=primary_mcp_url,
        dynamic_mcp_url=dynamic_mcp_url
    )
    
    # Set orchestrator in agent if available
    if orchestrator:
        agent.orchestrator = orchestrator
        agent.error_manager = error_manager
    
    # Initialize the agent
    success = await agent.initialize()
    if not success:
        print("[Server] WARNING: Agent initialization incomplete, some features may not work")
    else:
        print("[Server] ✓ Agent initialized successfully")
    
    yield
    
    # Shutdown
    print("[Server] Shutting down ADK MCP Database Assistant...")
    
    if orchestrator:
        await orchestrator.shutdown()

# Create FastAPI app
app = FastAPI(
    title="ADK MCP Database Assistant",
    description="AI-powered database assistant using Google ADK and MCP",
    version="1.0.0",
    lifespan=lifespan
)

# Configure CORS
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],  # In production, specify actual origins
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Health check endpoint
@app.get("/health")
async def health_check():
    """Check the health status of the service"""
    if agent:
        health_status = await agent.health_check()
        
        # Add orchestrator status if available
        if orchestrator:
            health_status["orchestrator"] = orchestrator.get_status()
        
        # Add error recovery status if available
        if error_manager:
            health_status["error_recovery"] = error_manager.get_statistics()
        
        return JSONResponse(content=health_status)
    else:
        return JSONResponse(
            content={
                "status": "unhealthy",
                "error": "Agent not initialized"
            },
            status_code=503
        )

# Orchestrator status endpoint
@app.get("/api/orchestrator/status")
async def get_orchestrator_status():
    """Get detailed orchestrator status"""
    if not orchestrator:
        return JSONResponse(
            content={"error": "Orchestrator not enabled"},
            status_code=404
        )
    
    return JSONResponse(content={
        "orchestrator": orchestrator.get_status(),
        "error_recovery": error_manager.get_statistics() if error_manager else None,
        "timestamp": datetime.utcnow().isoformat()
    })

# Chat endpoint (REST API)
@app.post("/api/chat")
async def chat(message: ChatMessage):
    """Process a chat message via REST API"""
    if not agent:
        raise HTTPException(status_code=503, detail="Agent not initialized")
    
    try:
        # Process the message
        response = await agent.process_message(
            user_message=message.message,
            session_id=message.session_id
        )
        
        # Add timestamp
        response["timestamp"] = datetime.utcnow().isoformat()
        
        return JSONResponse(content=response)
        
    except Exception as e:
        print(f"[API] Error processing message: {e}")
        return JSONResponse(
            content={
                "type": "error",
                "message": f"Error processing message: {str(e)}",
                "timestamp": datetime.utcnow().isoformat()
            },
            status_code=500
        )

# WebSocket endpoint for real-time chat
@app.websocket("/ws/{client_id}")
async def websocket_endpoint(websocket: WebSocket, client_id: str):
    """WebSocket endpoint for real-time chat"""
    await manager.connect(websocket, client_id)
    
    try:
        while True:
            # Receive message from client
            data = await websocket.receive_json()
            
            # Extract message
            user_message = data.get("message", "")
            message_type = data.get("type", "chat")
            
            if message_type == "ping":
                # Handle ping/pong for connection keep-alive
                await manager.send_message({
                    "type": "pong",
                    "timestamp": datetime.utcnow().isoformat()
                }, client_id)
                continue
            
            elif message_type == "chat":
                # Send typing indicator
                await manager.send_message({
                    "type": "typing",
                    "timestamp": datetime.utcnow().isoformat()
                }, client_id)
                
                # Process the message
                if agent:
                    response = await agent.process_message(
                        user_message=user_message,
                        session_id=client_id
                    )
                    
                    # Add metadata
                    response["timestamp"] = datetime.utcnow().isoformat()
                    response["session_id"] = client_id
                    
                    # Send response
                    await manager.send_message(response, client_id)
                else:
                    await manager.send_message({
                        "type": "error",
                        "message": "Agent not initialized",
                        "timestamp": datetime.utcnow().isoformat()
                    }, client_id)
            
            elif message_type == "history":
                # Get conversation history
                if agent:
                    history = agent.get_conversation_history(limit=data.get("limit", 10))
                    await manager.send_message({
                        "type": "history",
                        "data": history,
                        "timestamp": datetime.utcnow().isoformat()
                    }, client_id)
            
            elif message_type == "clear":
                # Clear conversation history
                if agent:
                    agent.clear_conversation_history()
                    await manager.send_message({
                        "type": "cleared",
                        "message": "Conversation history cleared",
                        "timestamp": datetime.utcnow().isoformat()
                    }, client_id)
                    
    except WebSocketDisconnect:
        manager.disconnect(client_id)
    except Exception as e:
        print(f"[WebSocket] Error with client {client_id}: {e}")
        manager.disconnect(client_id)

# Serve a simple HTML page for testing
@app.get("/")
async def get_index():
    """Serve the main chat interface"""
    html_content = """
    <!DOCTYPE html>
    <html>
    <head>
        <title>ADK MCP Database Assistant</title>
        <style>
            body {
                font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif;
                max-width: 800px;
                margin: 0 auto;
                padding: 20px;
                background: #f5f5f5;
            }
            h1 {
                color: #333;
                text-align: center;
            }
            #chat-container {
                background: white;
                border-radius: 8px;
                padding: 20px;
                height: 500px;
                overflow-y: auto;
                margin-bottom: 20px;
                box-shadow: 0 2px 4px rgba(0,0,0,0.1);
            }
            .message {
                margin: 10px 0;
                padding: 10px;
                border-radius: 8px;
            }
            .user-message {
                background: #007bff;
                color: white;
                text-align: right;
                margin-left: 20%;
            }
            .assistant-message {
                background: #f1f1f1;
                color: #333;
                margin-right: 20%;
            }
            .error-message {
                background: #dc3545;
                color: white;
            }
            .typing-indicator {
                color: #666;
                font-style: italic;
            }
            #input-container {
                display: flex;
                gap: 10px;
            }
            #message-input {
                flex: 1;
                padding: 10px;
                border: 1px solid #ddd;
                border-radius: 4px;
                font-size: 16px;
            }
            button {
                padding: 10px 20px;
                background: #007bff;
                color: white;
                border: none;
                border-radius: 4px;
                cursor: pointer;
                font-size: 16px;
            }
            button:hover {
                background: #0056b3;
            }
            button:disabled {
                background: #ccc;
                cursor: not-allowed;
            }
            #status {
                text-align: center;
                padding: 10px;
                margin-bottom: 10px;
                border-radius: 4px;
            }
            .connected {
                background: #d4edda;
                color: #155724;
            }
            .disconnected {
                background: #f8d7da;
                color: #721c24;
            }
            pre {
                background: #f4f4f4;
                padding: 10px;
                border-radius: 4px;
                overflow-x: auto;
            }
            table {
                width: 100%;
                border-collapse: collapse;
                margin: 10px 0;
            }
            th, td {
                border: 1px solid #ddd;
                padding: 8px;
                text-align: left;
            }
            th {
                background: #f2f2f2;
            }
        </style>
    </head>
    <body>
        <h1>🤖 ADK MCP Database Assistant</h1>
        <div id="status" class="disconnected">Disconnected</div>
        <div id="chat-container"></div>
        <div id="input-container">
            <input type="text" id="message-input" placeholder="Ask about customers, orders, or products..." disabled>
            <button id="send-button" disabled>Send</button>
            <button id="clear-button" disabled>Clear</button>
        </div>

        <script>
            const clientId = 'client-' + Math.random().toString(36).substr(2, 9);
            let ws = null;
            let isConnected = false;

            const chatContainer = document.getElementById('chat-container');
            const messageInput = document.getElementById('message-input');
            const sendButton = document.getElementById('send-button');
            const clearButton = document.getElementById('clear-button');
            const statusDiv = document.getElementById('status');

            function connect() {
                const protocol = window.location.protocol === 'https:' ? 'wss:' : 'ws:';
                const wsUrl = `${protocol}//${window.location.host}/ws/${clientId}`;
                
                ws = new WebSocket(wsUrl);

                ws.onopen = () => {
                    console.log('Connected to WebSocket');
                    isConnected = true;
                    statusDiv.textContent = 'Connected';
                    statusDiv.className = 'connected';
                    messageInput.disabled = false;
                    sendButton.disabled = false;
                    clearButton.disabled = false;
                    
                    // Send initial message
                    addMessage('assistant', 'Hello! I\\'m your database assistant. You can ask me about customers, orders, products, or sales analytics.');
                };

                ws.onmessage = (event) => {
                    const data = JSON.parse(event.data);
                    handleMessage(data);
                };

                ws.onerror = (error) => {
                    console.error('WebSocket error:', error);
                    addMessage('error', 'Connection error occurred');
                };

                ws.onclose = () => {
                    console.log('Disconnected from WebSocket');
                    isConnected = false;
                    statusDiv.textContent = 'Disconnected - Reconnecting...';
                    statusDiv.className = 'disconnected';
                    messageInput.disabled = true;
                    sendButton.disabled = true;
                    clearButton.disabled = true;
                    
                    // Attempt to reconnect after 3 seconds
                    setTimeout(connect, 3000);
                };
            }

            function handleMessage(data) {
                console.log('Received:', data);
                
                if (data.type === 'typing') {
                    // Show typing indicator
                    showTypingIndicator();
                } else if (data.type === 'text') {
                    removeTypingIndicator();
                    addMessage('assistant', data.message);
                } else if (data.type === 'data') {
                    removeTypingIndicator();
                    addDataMessage(data);
                } else if (data.type === 'chart') {
                    removeTypingIndicator();
                    addChartMessage(data);
                } else if (data.type === 'error') {
                    removeTypingIndicator();
                    addMessage('error', data.message);
                } else if (data.type === 'cleared') {
                    chatContainer.innerHTML = '';
                    addMessage('assistant', data.message);
                }
            }

            function addMessage(type, content) {
                const messageDiv = document.createElement('div');
                messageDiv.className = `message ${type}-message`;
                
                if (typeof content === 'string') {
                    messageDiv.textContent = content;
                } else {
                    messageDiv.innerHTML = content;
                }
                
                chatContainer.appendChild(messageDiv);
                chatContainer.scrollTop = chatContainer.scrollHeight;
            }

            function addDataMessage(data) {
                let html = `<strong>${data.message}</strong>`;
                
                if (data.results && data.results.length > 0) {
                    html += '<table>';
                    
                    // Get headers from first result
                    const headers = Object.keys(data.results[0]);
                    html += '<tr>';
                    headers.forEach(header => {
                        html += `<th>${header}</th>`;
                    });
                    html += '</tr>';
                    
                    // Add rows
                    data.results.forEach(row => {
                        html += '<tr>';
                        headers.forEach(header => {
                            html += `<td>${row[header] || ''}</td>`;
                        });
                        html += '</tr>';
                    });
                    
                    html += '</table>';
                }
                
                addMessage('assistant', html);
            }

            function addChartMessage(data) {
                let html = `<strong>${data.message}</strong><br>`;
                html += `<p>${data.summary}</p>`;
                
                // For now, show data as a simple list
                // In a real implementation, you'd use Chart.js or similar
                if (data.data && data.data.length > 0) {
                    html += '<pre>' + JSON.stringify(data.data, null, 2) + '</pre>';
                }
                
                addMessage('assistant', html);
            }

            function showTypingIndicator() {
                removeTypingIndicator();
                const typingDiv = document.createElement('div');
                typingDiv.id = 'typing-indicator';
                typingDiv.className = 'message assistant-message typing-indicator';
                typingDiv.textContent = 'Assistant is typing...';
                chatContainer.appendChild(typingDiv);
                chatContainer.scrollTop = chatContainer.scrollHeight;
            }

            function removeTypingIndicator() {
                const indicator = document.getElementById('typing-indicator');
                if (indicator) {
                    indicator.remove();
                }
            }

            function sendMessage() {
                const message = messageInput.value.trim();
                if (!message || !isConnected) return;

                // Add user message to chat
                addMessage('user', message);

                // Send to server
                ws.send(JSON.stringify({
                    type: 'chat',
                    message: message
                }));

                // Clear input
                messageInput.value = '';
            }

            function clearChat() {
                if (!isConnected) return;
                
                ws.send(JSON.stringify({
                    type: 'clear'
                }));
            }

            // Event listeners
            sendButton.addEventListener('click', sendMessage);
            clearButton.addEventListener('click', clearChat);
            messageInput.addEventListener('keypress', (e) => {
                if (e.key === 'Enter') {
                    sendMessage();
                }
            });

            // Connect on load
            connect();

            // Keep connection alive with periodic pings
            setInterval(() => {
                if (isConnected) {
                    ws.send(JSON.stringify({ type: 'ping' }));
                }
            }, 30000);
        </script>
    </body>
    </html>
    """
    return HTMLResponse(content=html_content)

# Run the server
if __name__ == "__main__":
    import uvicorn
    
    host = os.getenv("HOST", "0.0.0.0")
    port = int(os.getenv("PORT", 8080))
    debug = os.getenv("DEBUG", "true").lower() == "true"
    
    print(f"[Server] Starting server on {host}:{port}")
    print(f"[Server] Debug mode: {debug}")
    print(f"[Server] Open http://localhost:{port} in your browser to access the chat interface")
    
    uvicorn.run(
        "main:app",
        host=host,
        port=port,
        reload=debug,
        log_level="info" if debug else "warning"
    )
