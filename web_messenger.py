from fastapi import FastAPI, WebSocket, WebSocketDisconnect, HTTPException, UploadFile, File, Form
from fastapi.responses import HTMLResponse, FileResponse
from fastapi.staticfiles import StaticFiles
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
from sqlalchemy import create_engine, Column, String, Integer, Boolean, DateTime, Text
from sqlalchemy.ext.declarative import declarative_base
from sqlalchemy.orm import sessionmaker
from datetime import datetime, timedelta
from jose import JWTError, jwt
from passlib.context import CryptContext
import json
import uuid
import os
import base64
from io import BytesIO
from typing import Dict, List, Optional
import uvicorn
import aiofiles

# Конфигурация
class Config:
    SECRET_KEY = "tandau-secret-key-2024-mobile-simple"
    ALGORITHM = "HS256"
    ACCESS_TOKEN_EXPIRE_MINUTES = 1440
    DATABASE_URL = "sqlite:///./tandau.db"
    
    THEME = {
        'primary': '#6366F1',
        'primary_dark': '#4F46E5',
        'primary_light': '#8B5CF6',
        'secondary': '#10B981',
        'accent': '#F59E0B',
        'danger': '#EF4444',
        'success': '#10B981',
        'warning': '#F59E0B',
        'background': '#0F0F1A',
        'surface': '#1A1B2E',
        'card': '#252642',
        'text_primary': '#FFFFFF',
        'text_secondary': '#A0A0B8',
        'text_light': '#6B6B8B',
        'border': '#373755',
        'white': '#FFFFFF',
        'gradient_start': '#6366F1',
        'gradient_end': '#8B5CF6'
    }

# Создаем директории
os.makedirs("uploads/images", exist_ok=True)
os.makedirs("uploads/voices", exist_ok=True)
os.makedirs("uploads/files", exist_ok=True)
os.makedirs("user_avatars", exist_ok=True)

Base = declarative_base()

class User(Base):
    __tablename__ = "users"
    id = Column(Integer, primary_key=True, index=True)
    username = Column(String(50), unique=True, index=True)
    password_hash = Column(String(255))
    is_admin = Column(Boolean, default=False)
    avatar_path = Column(String(255))
    created_at = Column(DateTime, default=datetime.utcnow)

class Message(Base):
    __tablename__ = "messages"
    id = Column(Integer, primary_key=True, index=True)
    username = Column(String(50))
    content = Column(Text)
    message_type = Column(String(20), default="text")
    file_path = Column(String(255))
    timestamp = Column(DateTime, default=datetime.utcnow)
    chat_type = Column(String(20), default="public")

engine = create_engine(Config.DATABASE_URL, connect_args={"check_same_thread": False})
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
Base.metadata.create_all(bind=engine)

pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto")

def get_password_hash(password):
    return pwd_context.hash(password)

def verify_password(plain_password, hashed_password):
    return pwd_context.verify(plain_password, hashed_password)

def create_access_token(data: dict):
    to_encode = data.copy()
    expire = datetime.utcnow() + timedelta(minutes=Config.ACCESS_TOKEN_EXPIRE_MINUTES)
    to_encode.update({"exp": expire})
    return jwt.encode(to_encode, Config.SECRET_KEY, algorithm=Config.ALGORITHM)

def verify_token(token: str):
    try:
        payload = jwt.decode(token, Config.SECRET_KEY, algorithms=[Config.ALGORITHM])
        return payload.get("sub")
    except JWTError:
        return None

class ConnectionManager:
    def __init__(self):
        self.active_connections: Dict[str, List[WebSocket]] = {}
        self.user_data: Dict[str, dict] = {}
        self.messages_history = []

    async def connect(self, websocket: WebSocket, username: str, user_data: dict = None):
        await websocket.accept()
        if username not in self.active_connections:
            self.active_connections[username] = []
        self.active_connections[username].append(websocket)
        
        if user_data:
            self.user_data[username] = user_data
        
        # Отправляем историю сообщений
        for msg in self.messages_history[-50:]:
            await websocket.send_text(json.dumps(msg))
        
        await self.broadcast_system_message(f"🟢 {username} присоединился к чату")
        await self.broadcast_user_list()

    def disconnect(self, websocket: WebSocket, username: str):
        if username in self.active_connections:
            self.active_connections[username].remove(websocket)
            if not self.active_connections[username]:
                del self.active_connections[username]
                if username in self.user_data:
                    del self.user_data[username]

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
        users = list(self.active_connections.keys())
        user_data = []
        for username in users:
            user_info = {
                "username": username,
                "is_online": True,
                "is_admin": self.user_data.get(username, {}).get("is_admin", False)
            }
            user_data.append(user_info)
        
        await self.broadcast(json.dumps({
            "type": "user_list", 
            "users": user_data
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
            "message_type": message_data.get("message_type", "text"),
            "file_data": message_data.get("file_data"),
            "timestamp": datetime.now().isoformat(),
            "is_admin": message_data.get("is_admin", False)
        }
        self.messages_history.append(message)
        await self.broadcast(json.dumps(message))

class UserRegister(BaseModel):
    username: str
    password: str

class UserLogin(BaseModel):
    username: str
    password: str

app = FastAPI(title="Tandau Messenger Mobile")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.mount("/uploads", StaticFiles(directory="uploads"), name="uploads")
app.mount("/user_avatars", StaticFiles(directory="user_avatars"), name="user_avatars")

manager = ConnectionManager()

def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()

async def save_uploaded_file(file: UploadFile, folder: str) -> str:
    file_extension = file.filename.split('.')[-1] if '.' in file.filename else 'file'
    filename = f"{uuid.uuid4()}.{file_extension}"
    file_path = f"uploads/{folder}/{filename}"
    
    async with aiofiles.open(file_path, 'wb') as buffer:
        content = await file.read()
        await buffer.write(content)
    
    return file_path

def get_user_avatar_url(username: str, db) -> str:
    user = db.query(User).filter(User.username == username).first()
    if user and user.avatar_path:
        return f"/user_avatars/{os.path.basename(user.avatar_path)}"
    return None

# HTML с адаптивным дизайном (упрощенный)
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
        
        /* Контейнеры */
        .container { 
            display: flex; 
            height: 100vh;
            height: 100dvh;
        }
        
        /* Боковая панель - скрыта на мобильных */
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
        
        /* Адаптивные стили для мобильных */
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
            
            .input-container {
                gap: 0.5rem;
            }
            
            .message-input {
                padding: 0.8rem 1rem;
                font-size: 1rem;
            }
            
            .send-button {
                padding: 0.8rem 1.2rem;
                font-size: 0.9rem;
                white-space: nowrap;
            }
            
            .chat-header {
                padding: 1rem;
                display: none;
            }
            
            .chat-actions {
                gap: 0.5rem;
            }
            
            .action-btn {
                padding: 0.4rem 0.8rem;
                font-size: 0.8rem;
            }
            
            .message-image {
                max-width: 250px !important;
            }
        }
        
        @media (max-width: 480px) {
            .message-content {
                max-width: 80% !important;
            }
            
            .message-bubble {
                max-width: 100%;
            }
            
            .user-avatar {
                width: 35px !important;
                height: 35px !important;
                font-size: 0.8rem !important;
            }
            
            .message-avatar {
                width: 32px !important;
                height: 32px !important;
                font-size: 0.8rem !important;
            }
            
            .auth-form {
                width: 90% !important;
                padding: 1.5rem !important;
            }
            
            .auth-left h1 {
                font-size: 2.5rem !important;
            }
        }
        
        /* Десктоп стили */
        @media (min-width: 769px) {
            .mobile-header {
                display: none !important;
            }
            
            .mobile-nav {
                display: none !important;
            }
            
            .sidebar {
                transform: translateX(0) !important;
            }
        }
        
        /* Базовые стили */
        .sidebar-header {
            background: linear-gradient(135deg, #6366F1, #8B5CF6);
            padding: 2rem;
            text-align: center;
        }
        
        .sidebar-header h1 {
            font-size: 1.8rem;
            font-weight: bold;
            margin-bottom: 0.5rem;
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
            flex-shrink: 0;
            background-size: cover;
            background-position: center;
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
            display: flex;
            justify-content: space-between;
            align-items: center;
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
            flex-shrink: 0;
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
        
        .modal {
            display: none;
            position: fixed;
            top: 0;
            left: 0;
            width: 100%;
            height: 100%;
            background: rgba(0,0,0,0.8);
            z-index: 1000;
            align-items: center;
            justify-content: center;
            padding: 1rem;
        }
        
        .modal-content {
            background: #1A1B2E;
            padding: 2rem;
            border-radius: 12px;
            max-width: 500px;
            width: 100%;
        }
    </style>
</head>
<body>
    <!-- Экран аутентификации -->
    <div id="authScreen" class="auth-container">
        <div class="auth-left">
            <div style="text-align: center;">
                <h1 style="font-size: 3rem; margin-bottom: 1rem;">Tandau</h1>
                <p style="font-size: 1.2rem; margin-bottom: 2rem;">Адаптивный мессенджер</p>
                <div style="text-align: left;">
                    <div style="margin: 1rem 0; font-size: 1.1rem;">📱 Полная адаптивность</div>
                    <div style="margin: 1rem 0; font-size: 1.1rem;">🌐 Реальное время</div>
                    <div style="margin: 1rem 0; font-size: 1.1rem;">💬 Групповой чат</div>
                </div>
            </div>
        </div>
        <div class="auth-right">
            <div class="auth-form">
                <h2 id="authTitle">Вход в систему</h2>
                <div id="loginForm">
                    <input type="text" id="loginUsername" class="form-input" placeholder="Имя пользователя">
                    <input type="password" id="loginPassword" class="form-input" placeholder="Пароль">
                    <button class="auth-button" onclick="login()">Войти</button>
                    <div style="text-align: center; color: #6366F1; cursor: pointer;" onclick="showRegister()">
                        Нет аккаунта? Зарегистрироваться
                    </div>
                </div>
                <div id="registerForm" style="display: none;">
                    <input type="text" id="registerUsername" class="form-input" placeholder="Имя пользователя">
                    <input type="password" id="registerPassword" class="form-input" placeholder="Пароль">
                    <input type="password" id="registerConfirm" class="form-input" placeholder="Повторите пароль">
                    <button class="auth-button" onclick="register()">Зарегистрироваться</button>
                    <div style="text-align: center; color: #6366F1; cursor: pointer;" onclick="showLogin()">
                        Уже есть аккаунт? Войти
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
            <h2 id="mobileChatTitle">🌐 Публичный чат</h2>
            <button class="mobile-menu-btn" onclick="showMobileMenu()">⋯</button>
        </div>

        <!-- Боковая панель -->
        <div class="sidebar" id="sidebar">
            <div class="sidebar-header">
                <h1>Tandau</h1>
                <p>Mobile Messenger</p>
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
                <button class="nav-tab" onclick="switchMobileTab('profile')">
                    <i>👤</i>
                    <span>Профиль</span>
                </button>
            </div>
        </div>
    </div>

    <script>
        let ws = null;
        let currentUser = null;
        let token = null;
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
            alert('Мобильное меню:\n1. Настройки\n2. Сменить чат\n3. Выйти');
        }

        // Аутентификация
        async function login() {
            const username = document.getElementById('loginUsername').value;
            const password = document.getElementById('loginPassword').value;

            if (!username || !password) {
                alert('Пожалуйста, заполните все поля');
                return;
            }

            try {
                const response = await fetch('/api/auth/login', {
                    method: 'POST',
                    headers: {'Content-Type': 'application/json'},
                    body: JSON.stringify({username, password})
                });

                const data = await response.json();

                if (response.ok) {
                    token = data.access_token;
                    currentUser = username;
                    startWebSocket();
                    showMainScreen();
                } else {
                    alert(data.detail || 'Ошибка входа');
                }
            } catch (error) {
                alert('Ошибка подключения к серверу');
            }
        }

        async function register() {
            const username = document.getElementById('registerUsername').value;
            const password = document.getElementById('registerPassword').value;
            const confirm = document.getElementById('registerConfirm').value;

            if (!username || !password || !confirm) {
                alert('Пожалуйста, заполните все поля');
                return;
            }

            if (password !== confirm) {
                alert('Пароли не совпадают');
                return;
            }

            try {
                const response = await fetch('/api/auth/register', {
                    method: 'POST',
                    headers: {'Content-Type': 'application/json'},
                    body: JSON.stringify({username, password})
                });

                const data = await response.json();

                if (response.ok) {
                    alert('Регистрация успешна!');
                    showLogin();
                } else {
                    alert(data.detail || 'Ошибка регистрации');
                }
            } catch (error) {
                alert('Ошибка подключения к серверу');
            }
        }

        function showLogin() {
            document.getElementById('authTitle').textContent = 'Вход в систему';
            document.getElementById('loginForm').style.display = 'block';
            document.getElementById('registerForm').style.display = 'none';
        }

        function showRegister() {
            document.getElementById('authTitle').textContent = 'Регистрация';
            document.getElementById('loginForm').style.display = 'none';
            document.getElementById('registerForm').style.display = 'block';
        }

        function showMainScreen() {
            document.getElementById('authScreen').style.display = 'none';
            document.getElementById('mainScreen').style.display = 'flex';
            document.getElementById('userName').textContent = currentUser;
            document.getElementById('userAvatar').textContent = currentUser.substring(0, 2).toUpperCase();
            document.getElementById('messageInput').disabled = false;
            document.getElementById('sendButton').disabled = false;
            
            if (isMobile) {
                document.getElementById('mobileChatTitle').textContent = '💬 Чат';
            }
        }

        // WebSocket
        function startWebSocket() {
            const protocol = window.location.protocol === 'https:' ? 'wss:' : 'ws:';
            const wsUrl = `${protocol}//${window.location.host}/ws?token=${token}`;
            
            ws = new WebSocket(wsUrl);

            ws.onopen = function() {
                console.log('Connected');
            };

            ws.onmessage = function(event) {
                const data = JSON.parse(event.data);
                handleWebSocketMessage(data);
            };

            ws.onclose = function() {
                console.log('Disconnected');
                setTimeout(() => {
                    if (currentUser) startWebSocket();
                }, 3000);
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
                    <span>${user.username}</span>
                `;
                container.appendChild(userDiv);
            });
        }

        function sendMessage() {
            const input = document.getElementById('messageInput');
            const content = input.value.trim();
            
            if (content && ws && ws.readyState === WebSocket.OPEN) {
                ws.send(JSON.stringify({content: content}));
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

        window.addEventListener('resize', checkMobile);
        checkMobile();
        showLogin();
    </script>
</body>
</html>
"""

@app.get("/")
async def root():
    return HTMLResponse(HTML)

@app.post("/api/auth/register")
async def register(user: UserRegister, db: SessionLocal = Depends(get_db)):
    existing_user = db.query(User).filter(User.username == user.username).first()
    if existing_user:
        raise HTTPException(status_code=400, detail="Username already registered")
    
    hashed_password = get_password_hash(user.password)
    db_user = User(username=user.username, password_hash=hashed_password)
    db.add(db_user)
    db.commit()
    
    return {"message": "User created successfully"}

@app.post("/api/auth/login")
async def login(user: UserLogin, db: SessionLocal = Depends(get_db)):
    db_user = db.query(User).filter(User.username == user.username).first()
    if not db_user or not verify_password(user.password, db_user.password_hash):
        raise HTTPException(status_code=401, detail="Incorrect username or password")
    
    access_token = create_access_token(data={"sub": db_user.username})
    return {"access_token": access_token, "token_type": "bearer"}

@app.websocket("/ws")
async def websocket_endpoint(websocket: WebSocket, token: str):
    username = verify_token(token)
    if not username:
        await websocket.close()
        return

    db = SessionLocal()
    user = db.query(User).filter(User.username == username).first()
    user_data = {"is_admin": user.is_admin if user else False}
    db.close()

    await manager.connect(websocket, username, user_data)
    
    try:
        while True:
            data = await websocket.receive_text()
            message_data = json.loads(data)
            
            db = SessionLocal()
            db_message = Message(
                username=username,
                content=message_data["content"]
            )
            db.add(db_message)
            db.commit()
            db.close()
            
            await manager.send_message({
                "username": username,
                "content": message_data["content"],
                "is_admin": user_data["is_admin"]
            })
            
    except WebSocketDisconnect:
        manager.disconnect(websocket, username)
        await manager.broadcast_system_message(f"🔴 {username} покинул чат")
        await manager.broadcast_user_list()

if __name__ == "__main__":
    uvicorn.run(app, host="0.0.0.0", port=8000, log_level="info")
