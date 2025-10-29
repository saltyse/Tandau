from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.responses import HTMLResponse
from datetime import datetime
import json
import uuid
from typing import Dict, List
import uvicorn

# Конфигурация
class Config:
    THEME = {
        'primary': '#6366F1',
        'primary_dark': '#4F46E5', 
        'primary_light': '#8B5CF6',
        'secondary': '#10B981',
        'accent': '#F59E0B',
        'danger': '#EF4444',
        'success': '#10B981',
        'background': '#0F0F1A',
        'surface': '#1A1B2E',
        'card': '#252642',
        'text_primary': '#FFFFFF',
        'text_secondary': '#A0A0B8',
        'border': '#373755',
    }

# Менеджер соединений
class ConnectionManager:
    def __init__(self):
        self.active_connections: Dict[str, List[WebSocket]] = {}
        self.messages_history = []
        self.users = {}  # Простое хранилище пользователей

    async def connect(self, websocket: WebSocket, username: str):
        await websocket.accept()
        if username not in self.active_connections:
            self.active_connections[username] = []
        self.active_connections[username].append(websocket)
        
        self.users[username] = {
            'joined_at': datetime.now(),
            'is_online': True
        }
        
        # Отправляем историю новому пользователю
        for msg in self.messages_history[-20:]:
            await websocket.send_text(json.dumps(msg))
        
        await self.broadcast_system_message(f"🟢 {username} присоединился к чату")
        await self.broadcast_user_list()

    def disconnect(self, websocket: WebSocket, username: str):
        if username in self.active_connections:
            self.active_connections[username].remove(websocket)
            if not self.active_connections[username]:
                del self.active_connections[username]
                if username in self.users:
                    self.users[username]['is_online'] = False

    async def send_to_user(self, username: str, message: str):
        if username in self.active_connections:
            for connection in self.active_connections[username]:
                try:
                    await connection.send_text(message)
                except:
                    continue

    async def broadcast(self, message: str):
        for connections in self.active_connections.values():
            for connection in connections:
                try:
                    await connection.send_text(message)
                except:
                    continue

    async def broadcast_user_list(self):
        online_users = list(self.active_connections.keys())
        await self.broadcast(json.dumps({
            "type": "user_list", 
            "users": online_users
        }))

    async def broadcast_system_message(self, message: str):
        system_msg = {
            "type": "system",
            "id": str(uuid.uuid4()),
            "content": message,
            "timestamp": datetime.now().isoformat()
        }
        self.messages_history.append(system_msg)
        await self.broadcast(json.dumps(system_msg))

    async def send_message(self, message_data: dict):
        message = {
            "type": "message",
            "id": str(uuid.uuid4()),
            "username": message_data["username"],
            "content": message_data["content"],
            "timestamp": datetime.now().isoformat()
        }
        self.messages_history.append(message)
        await self.broadcast(json.dumps(message))

# FastAPI приложение
app = FastAPI(title="Tandau Messenger")
manager = ConnectionManager()

# HTML с адаптивным дизайном
HTML = """
<!DOCTYPE html>
<html lang="ru">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0, maximum-scale=1.0, user-scalable=no">
    <title>Tandau Messenger</title>
    <style>
        * { 
            margin: 0; 
            padding: 0; 
            box-sizing: border-box;
            -webkit-tap-highlight-color: transparent;
        }
        
        body { 
            font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, Oxygen, Ubuntu, sans-serif; 
            background: #0F0F1A; 
            color: #FFFFFF;
            height: 100vh;
            height: 100dvh;
            overflow: hidden;
        }
        
        .container { 
            display: flex; 
            height: 100vh;
            height: 100dvh;
        }
        
        /* Боковая панель */
        .sidebar {
            width: 300px;
            background: #1A1B2E;
            border-right: 1px solid #373755;
            display: flex;
            flex-direction: column;
            transition: transform 0.3s ease;
        }
        
        /* Мобильная навигация */
        .mobile-header {
            display: none;
            background: #1A1B2E;
            padding: 1rem;
            border-bottom: 1px solid #373755;
            align-items: center;
            justify-content: space-between;
            position: sticky;
            top: 0;
            z-index: 100;
        }
        
        .mobile-menu-btn {
            background: none;
            border: none;
            color: white;
            font-size: 1.5rem;
            cursor: pointer;
            padding: 0.5rem;
        }
        
        .mobile-nav {
            display: none;
            position: fixed;
            bottom: 0;
            left: 0;
            right: 0;
            background: #1A1B2E;
            border-top: 1px solid #373755;
            padding: 0.5rem;
            z-index: 1000;
        }
        
        .nav-tabs {
            display: flex;
            justify-content: space-around;
        }
        
        .nav-tab {
            display: flex;
            flex-direction: column;
            align-items: center;
            padding: 0.5rem;
            color: #A0A0B8;
            background: none;
            border: none;
            font-size: 0.8rem;
            cursor: pointer;
            flex: 1;
            max-width: 80px;
        }
        
        .nav-tab.active {
            color: #6366F1;
        }
        
        .nav-tab i {
            font-size: 1.2rem;
            margin-bottom: 0.2rem;
        }
        
        /* Адаптивные стили */
        @media (max-width: 768px) {
            .sidebar {
                position: fixed;
                top: 0;
                left: 0;
                height: 100%;
                z-index: 1000;
                transform: translateX(-100%);
                width: 280px;
            }
            
            .sidebar.mobile-visible {
                transform: translateX(0);
            }
            
            .mobile-header {
                display: flex;
            }
            
            .mobile-nav {
                display: block;
            }
            
            .chat-area {
                width: 100%;
            }
            
            .user-info {
                margin: 0.5rem;
                padding: 1rem;
            }
            
            .messages-container {
                padding: 1rem;
                padding-bottom: 80px;
            }
            
            .message {
                margin-bottom: 1rem;
            }
            
            .message-content {
                max-width: 85% !important;
            }
            
            .message-bubble {
                padding: 0.8rem 1rem;
                font-size: 0.9rem;
            }
            
            .input-area {
                padding: 1rem;
                padding-bottom: calc(1rem + env(safe-area-inset-bottom));
            }
            
            .message-input {
                padding: 0.8rem 1rem;
                font-size: 1rem;
            }
            
            .send-button {
                padding: 0.8rem 1.2rem;
                font-size: 0.9rem;
            }
            
            .chat-header {
                display: none;
            }
        }
        
        @media (max-width: 480px) {
            .message-content {
                max-width: 80% !important;
            }
            
            .user-avatar {
                width: 35px !important;
                height: 35px !important;
            }
            
            .message-avatar {
                width: 32px !important;
                height: 32px !important;
            }
            
            .auth-form {
                width: 90% !important;
                padding: 1.5rem !important;
            }
        }
        
        /* Десктоп стили */
        @media (min-width: 769px) {
            .mobile-header, .mobile-nav {
                display: none !important;
            }
        }
        
        /* Базовые стили */
        .sidebar-header {
            background: linear-gradient(135deg, #6366F1, #8B5CF6);
            padding: 2rem;
            text-align: center;
        }
        
        .user-info {
            background: #252642;
            margin: 1rem;
            padding: 1.5rem;
            border-radius: 12px;
            display: flex;
            align-items: center;
            gap: 1rem;
        }
        
        .user-avatar {
            width: 50px;
            height: 50px;
            background: linear-gradient(135deg, #6366F1, #8B5CF6);
            border-radius: 50%;
            display: flex;
            align-items: center;
            justify-content: center;
            font-weight: bold;
            font-size: 1.2rem;
        }
        
        .chat-area {
            flex: 1;
            display: flex;
            flex-direction: column;
        }
        
        .chat-header {
            background: #1A1B2E;
            padding: 1.5rem 2rem;
            border-bottom: 1px solid #373755;
        }
        
        .messages-container {
            flex: 1;
            overflow-y: auto;
            padding: 2rem;
            background: #0F0F1A;
        }
        
        .message {
            margin-bottom: 1.5rem;
            display: flex;
            gap: 1rem;
        }
        
        .message.own {
            flex-direction: row-reverse;
        }
        
        .message-avatar {
            width: 40px;
            height: 40px;
            background: linear-gradient(135deg, #6366F1, #8B5CF6);
            border-radius: 50%;
            display: flex;
            align-items: center;
            justify-content: center;
            font-weight: bold;
            font-size: 0.9rem;
        }
        
        .message-content {
            max-width: 60%;
        }
        
        .message.own .message-content {
            text-align: right;
        }
        
        .message-header {
            display: flex;
            align-items: center;
            gap: 0.5rem;
            margin-bottom: 0.5rem;
        }
        
        .message-username {
            font-weight: bold;
        }
        
        .message-time {
            font-size: 0.8rem;
            color: #6B6B8B;
        }
        
        .message-bubble {
            background: #252642;
            padding: 1rem 1.5rem;
            border-radius: 18px;
            display: inline-block;
            max-width: 100%;
            word-wrap: break-word;
        }
        
        .message.own .message-bubble {
            background: #6366F1;
        }
        
        .system-message {
            text-align: center;
            color: #A0A0B8;
            font-style: italic;
            margin: 1rem 0;
        }
        
        .input-area {
            background: #1A1B2E;
            padding: 1.5rem 2rem;
            border-top: 1px solid #373755;
        }
        
        .input-container {
            display: flex;
            gap: 1rem;
            align-items: center;
        }
        
        .message-input {
            flex: 1;
            background: #252642;
            border: 1px solid #373755;
            border-radius: 25px;
            padding: 1rem 1.5rem;
            color: white;
            font-size: 1rem;
            outline: none;
            resize: none;
            height: 50px;
            font-family: inherit;
        }
        
        .send-button {
            background: #6366F1;
            color: white;
            border: none;
            border-radius: 25px;
            padding: 1rem 2rem;
            font-size: 1rem;
            cursor: pointer;
        }
        
        .auth-container {
            display: flex;
            height: 100vh;
            background: linear-gradient(135deg, #4F46E5, #0F0F1A);
            flex-direction: column;
        }
        
        @media (min-width: 769px) {
            .auth-container {
                flex-direction: row;
            }
        }
        
        .auth-left {
            flex: 1;
            background: #6366F1;
            display: flex;
            align-items: center;
            justify-content: center;
            color: white;
            padding: 2rem;
        }
        
        .auth-right {
            flex: 1;
            background: #1A1B2E;
            display: flex;
            align-items: center;
            justify-content: center;
            padding: 2rem;
        }
        
        .auth-form {
            width: 400px;
            padding: 2rem;
        }
        
        .form-input {
            width: 100%;
            background: #252642;
            border: 1px solid #373755;
            border-radius: 12px;
            padding: 1rem 1.5rem;
            color: white;
            font-size: 1rem;
            margin-bottom: 1.5rem;
            outline: none;
        }
        
        .auth-button {
            width: 100%;
            background: #6366F1;
            color: white;
            border: none;
            border-radius: 12px;
            padding: 1rem;
            font-size: 1.1rem;
            cursor: pointer;
            margin-bottom: 1rem;
        }
        
        .users-list {
            background: #252642;
            margin: 1rem;
            padding: 1rem;
            border-radius: 12px;
            flex: 1;
            overflow-y: auto;
        }
    </style>
</head>
<body>
    <!-- Экран аутентификации -->
    <div id="authScreen" class="auth-container">
        <div class="auth-left">
            <div style="text-align: center;">
                <h1 style="font-size: 3rem; margin-bottom: 1rem;">Tandau</h1>
                <p style="font-size: 1.2rem; margin-bottom: 2rem;">Простой мессенджер</p>
                <div style="text-align: left;">
                    <div style="margin: 1rem 0; font-size: 1.1rem;">📱 Адаптивный дизайн</div>
                    <div style="margin: 1rem 0; font-size: 1.1rem;">🌐 Реальное время</div>
                    <div style="margin: 1rem 0; font-size: 1.1rem;">💬 Групповой чат</div>
                </div>
            </div>
        </div>
        <div class="auth-right">
            <div class="auth-form">
                <h2>Вход в чат</h2>
                <div>
                    <input type="text" id="usernameInput" class="form-input" placeholder="Введите ваше имя">
                    <button class="auth-button" onclick="login()">Войти в чат</button>
                    <div style="text-align: center; color: #A0A0B8;">
                        Просто введите имя и начинайте общаться!
                    </div>
                </div>
            </div>
        </div>
    </div>

    <!-- Основной интерфейс -->
    <div id="mainScreen" class="container" style="display: none;">
        <!-- Мобильный хедер -->
        <div class="mobile-header">
            <button class="mobile-menu-btn" onclick="toggleSidebar()">☰</button>
            <h2>💬 Чат</h2>
            <button class="mobile-menu-btn" onclick="showMobileMenu()">⋯</button>
        </div>

        <!-- Боковая панель -->
        <div class="sidebar" id="sidebar">
            <div class="sidebar-header">
                <h1>Tandau</h1>
                <p>Simple Messenger</p>
            </div>
            
            <div class="user-info">
                <div class="user-avatar" id="userAvatar">US</div>
                <div>
                    <h3 id="userName">User</h3>
                    <div style="color: #10B981;">🟢 В сети</div>
                </div>
            </div>

            <div class="users-list">
                <h4 style="margin-bottom: 1rem;">Онлайн сейчас:</h4>
                <div id="onlineUsersList"></div>
            </div>
        </div>

        <!-- Область чата -->
        <div class="chat-area">
            <div class="chat-header">
                <h2>🌐 Публичный чат</h2>
            </div>

            <div class="messages-container" id="messagesContainer">
                <div class="system-message">
                    Добро пожаловать в Tandau Messenger! 🎉
                </div>
            </div>

            <div class="input-area">
                <div class="input-container">
                    <textarea id="messageInput" class="message-input" 
                           placeholder="Введите сообщение..." disabled rows="1"></textarea>
                    <button id="sendButton" class="send-button" onclick="sendMessage()" disabled>
                        Отправить
                    </button>
                </div>
            </div>
        </div>

        <!-- Мобильная навигация -->
        <div class="mobile-nav">
            <div class="nav-tabs">
                <button class="nav-tab active" onclick="switchMobileTab('chat')">
                    <i>💬</i>
                    <span>Чат</span>
                </button>
                <button class="nav-tab" onclick="switchMobileTab('users')">
                    <i>👥</i>
                    <span>Люди</span>
                </button>
                <button class="nav-tab" onclick="showInfo()">
                    <i>ℹ️</i>
                    <span>Инфо</span>
                </button>
            </div>
        </div>
    </div>

    <script>
        let ws = null;
        let currentUser = null;
        let isMobile = window.innerWidth <= 768;

        // Мобильные функции
        function checkMobile() {
            isMobile = window.innerWidth <= 768;
        }

        function toggleSidebar() {
            const sidebar = document.getElementById('sidebar');
            sidebar.classList.toggle('mobile-visible');
        }

        function switchMobileTab(tab) {
            document.querySelectorAll('.nav-tab').forEach(t => t.classList.remove('active'));
            event.target.classList.add('active');
            
            if (tab === 'users') {
                toggleSidebar();
            }
        }

        function showMobileMenu() {
            const action = confirm('Действия:\nOK - Обновить чат\nОтмена - Выйти');
            if (!action) {
                location.reload();
            }
        }

        function showInfo() {
            alert('Tandau Messenger v1.0\nПростой чат в реальном времени');
        }

        // Аутентификация
        function login() {
            const username = document.getElementById('usernameInput').value.trim();
            
            if (!username) {
                alert('Пожалуйста, введите ваше имя');
                return;
            }

            if (username.length < 2) {
                alert('Имя должно быть не менее 2 символов');
                return;
            }

            currentUser = username;
            startWebSocket();
            showMainScreen();
        }

        function showMainScreen() {
            document.getElementById('authScreen').style.display = 'none';
            document.getElementById('mainScreen').style.display = 'flex';
            document.getElementById('userName').textContent = currentUser;
            document.getElementById('userAvatar').textContent = currentUser.substring(0, 2).toUpperCase();
            document.getElementById('messageInput').disabled = false;
            document.getElementById('sendButton').disabled = false;
            
            // Фокус на поле ввода
            document.getElementById('messageInput').focus();
        }

        // WebSocket
        function startWebSocket() {
            const protocol = window.location.protocol === 'https:' ? 'wss:' : 'ws:';
            const wsUrl = `${protocol}//${window.location.host}/ws?username=${encodeURIComponent(currentUser)}`;
            
            ws = new WebSocket(wsUrl);

            ws.onopen = function() {
                console.log('WebSocket connected');
            };

            ws.onmessage = function(event) {
                const data = JSON.parse(event.data);
                handleWebSocketMessage(data);
            };

            ws.onclose = function() {
                console.log('WebSocket disconnected');
                // Пытаемся переподключиться
                setTimeout(() => {
                    if (currentUser) {
                        startWebSocket();
                    }
                }, 3000);
            };

            ws.onerror = function(error) {
                console.error('WebSocket error:', error);
            };
        }

        function handleWebSocketMessage(data) {
            switch (data.type) {
                case 'message':
                    displayMessage(data);
                    break;
                case 'system':
                    displaySystemMessage(data);
                    break;
                case 'user_list':
                    updateOnlineUsers(data.users);
                    break;
            }
        }

        function displayMessage(message) {
            const container = document.getElementById('messagesContainer');
            const messageDiv = document.createElement('div');
            messageDiv.className = `message ${message.username === currentUser ? 'own' : ''}`;
            
            messageDiv.innerHTML = `
                <div class="message-avatar">
                    ${message.username.substring(0, 2).toUpperCase()}
                </div>
                <div class="message-content">
                    <div class="message-header">
                        <span class="message-username">${message.username}</span>
                        <span class="message-time">${formatTime(message.timestamp)}</span>
                    </div>
                    <div class="message-bubble">
                        ${message.content}
                    </div>
                </div>
            `;
            
            container.appendChild(messageDiv);
            container.scrollTop = container.scrollHeight;
        }

        function displaySystemMessage(message) {
            const container = document.getElementById('messagesContainer');
            const messageDiv = document.createElement('div');
            messageDiv.className = 'system-message';
            messageDiv.textContent = message.content;
            container.appendChild(messageDiv);
            container.scrollTop = container.scrollHeight;
        }

        function updateOnlineUsers(users) {
            const container = document.getElementById('onlineUsersList');
            container.innerHTML = '';
            
            users.forEach(user => {
                const userDiv = document.createElement('div');
                userDiv.style.cssText = 'padding: 0.5rem; margin: 0.2rem 0; border-radius: 6px; display: flex; align-items: center; gap: 0.5rem;';
                userDiv.innerHTML = `
                    <div style="width: 8px; height: 8px; background: #10B981; border-radius: 50%;"></div>
                    <span>${user}</span>
                `;
                container.appendChild(userDiv);
            });
        }

        function sendMessage() {
            const input = document.getElementById('messageInput');
            const content = input.value.trim();
            
            if (content && ws && ws.readyState === WebSocket.OPEN) {
                const message = {
                    content: content
                };
                
                ws.send(JSON.stringify(message));
                input.value = '';
                adjustTextareaHeight(input);
            }
        }

        function formatTime(timestamp) {
            return new Date(timestamp).toLocaleTimeString('ru-RU', { 
                hour: '2-digit', minute: '2-digit' 
            });
        }

        function adjustTextareaHeight(textarea) {
            textarea.style.height = 'auto';
            textarea.style.height = Math.min(textarea.scrollHeight, 80) + 'px';
        }

        // Обработчики событий
        document.getElementById('messageInput').addEventListener('keypress', function(e) {
            if (e.key === 'Enter' && !e.shiftKey) {
                e.preventDefault();
                sendMessage();
            }
        });

        document.getElementById('messageInput').addEventListener('input', function() {
            adjustTextareaHeight(this);
        });

        document.getElementById('usernameInput').addEventListener('keypress', function(e) {
            if (e.key === 'Enter') {
                login();
            }
        });

        window.addEventListener('resize', checkMobile);
        checkMobile();
    </script>
</body>
</html>
"""

@app.get("/")
async def root():
    return HTMLResponse(HTML)

@app.websocket("/ws")
async def websocket_endpoint(websocket: WebSocket, username: str = "Anonymous"):
    await manager.connect(websocket, username)
    
    try:
        while True:
            data = await websocket.receive_text()
            message_data = json.loads(data)
            
            # Отправляем сообщение всем
            await manager.send_message({
                "username": username,
                "content": message_data["content"]
            })
            
    except WebSocketDisconnect:
        manager.disconnect(websocket, username)
        await manager.broadcast_system_message(f"🔴 {username} покинул чат")
        await manager.broadcast_user_list()

if __name__ == "__main__":
    uvicorn.run(app, host="0.0.0.0", port=8000, log_level="info")
