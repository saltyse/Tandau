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
from PIL import Image
import aiofiles

# Конфигурация
class Config:
    SECRET_KEY = "tandau-secret-key-2024-mobile"
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

    async def connect(self, websocket: WebSocket, username: str, user_data: dict = None):
        await websocket.accept()
        if username not in self.active_connections:
            self.active_connections[username] = []
        self.active_connections[username].append(websocket)
        
        if user_data:
            self.user_data[username] = user_data
        
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
        await self.broadcast(json.dumps({
            "type": "system",
            "id": str(uuid.uuid4()),
            "content": message,
            "timestamp": datetime.now().isoformat()
        }))

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
    file_extension = file.filename.split('.')[-1]
    filename = f"{uuid.uuid4()}.{file_extension}"
    file_path = f"uploads/{folder}/{filename}"
    
    async with aiofiles.open(file_path, 'wb') as buffer:
        content = await file.read()
        await buffer.write(content)
    
    return file_path

async def process_image(image: UploadFile) -> dict:
    file_path = await save_uploaded_file(image, "images")
    
    with Image.open(file_path) as img:
        if img.mode in ('RGBA', 'LA', 'P'):
            img = img.convert('RGB')
        
        # Для мобильных делаем меньше превью
        img.thumbnail((400, 400))
        preview_path = f"uploads/images/preview_{os.path.basename(file_path)}"
        img.save(preview_path, "JPEG", quality=85)
    
    return {
        "original": file_path,
        "preview": preview_path,
        "filename": image.filename
    }

def get_user_avatar_url(username: str, db) -> str:
    user = db.query(User).filter(User.username == username).first()
    if user and user.avatar_path:
        return f"/user_avatars/{os.path.basename(user.avatar_path)}"
    return None

# АДАПТИВНЫЙ HTML ДЛЯ МОБИЛЬНЫХ УСТРОЙСТВ
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
            position: fixed;
            width: 100%;
        }
        
        /* Контейнеры */
        .container { 
            display: flex; 
            height: 100vh;
            height: 100dvh;
            position: relative;
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
        
        .mobile-hidden {
            display: none;
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
                padding-bottom: 80px; /* Место для навигации */
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
            
            .media-buttons {
                display: none; /* Скрываем на мобильных для экономии места */
            }
            
            .chat-header {
                padding: 1rem;
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
        
        /* Базовые стили (остаются как были) */
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
        
        .user-details h3 {
            font-size: 1.1rem;
            margin-bottom: 0.2rem;
        }
        
        .status-online {
            color: #10B981;
            font-size: 0.9rem;
        }
        
        .nav-menu {
            padding: 1rem;
        }
        
        .nav-item {
            display: flex;
            align-items: center;
            padding: 1rem;
            margin: 0.5rem 0;
            border-radius: 8px;
            cursor: pointer;
            transition: all 0.3s ease;
            color: #A0A0B8;
        }
        
        .nav-item:hover, .nav-item.active {
            background: #252642;
            color: #6366F1;
        }
        
        .nav-item i {
            font-size: 1.2rem;
            margin-right: 1rem;
            width: 20px;
            text-align: center;
        }
        
        .chat-area {
            flex: 1;
            display: flex;
            flex-direction: column;
            width: calc(100% - 300px);
        }
        
        .chat-header {
            background: #1A1B2E;
            padding: 1.5rem 2rem;
            border-bottom: 1px solid #373755;
            display: flex;
            justify-content: space-between;
            align-items: center;
        }
        
        .chat-header h2 {
            font-size: 1.5rem;
        }
        
        .chat-actions {
            display: flex;
            gap: 1rem;
        }
        
        .action-btn {
            background: #252642;
            border: none;
            border-radius: 8px;
            padding: 0.5rem 1rem;
            color: white;
            cursor: pointer;
            display: flex;
            align-items: center;
            gap: 0.5rem;
        }
        
        .messages-container {
            flex: 1;
            overflow-y: auto;
            padding: 2rem;
            background: #0F0F1A;
            padding-bottom: 100px;
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
            background-size: cover;
            background-position: center;
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
        
        .message.own .message-header {
            justify-content: flex-end;
        }
        
        .message-username {
            font-weight: bold;
        }
        
        .admin-badge {
            color: #F59E0B;
            font-size: 0.8rem;
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
        
        .message-image {
            max-width: 300px;
            border-radius: 12px;
            margin-top: 0.5rem;
            cursor: pointer;
        }
        
        .message-file {
            background: #1A1B2E;
            padding: 1rem;
            border-radius: 8px;
            margin-top: 0.5rem;
            display: flex;
            align-items: center;
            gap: 0.5rem;
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
            position: sticky;
            bottom: 0;
        }
        
        .input-container {
            display: flex;
            gap: 1rem;
            align-items: center;
        }
        
        .media-buttons {
            display: flex;
            gap: 0.5rem;
        }
        
        .media-btn {
            background: #252642;
            border: none;
            border-radius: 8px;
            padding: 0.75rem;
            color: white;
            cursor: pointer;
            display: flex;
            align-items: center;
            justify-content: center;
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
        
        .message-input:focus {
            border-color: #6366F1;
        }
        
        .send-button {
            background: #6366F1;
            color: white;
            border: none;
            border-radius: 25px;
            padding: 1rem 2rem;
            font-size: 1rem;
            cursor: pointer;
            transition: background 0.3s ease;
        }
        
        .send-button:hover {
            background: #4F46E5;
        }
        
        .auth-container {
            display: flex;
            height: 100vh;
            height: 100dvh;
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
        
        .auth-left-content {
            text-align: center;
            max-width: 400px;
        }
        
        .auth-left h1 {
            font-size: 3rem;
            margin-bottom: 1rem;
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
        
        .auth-form h2 {
            font-size: 2rem;
            margin-bottom: 2rem;
            text-align: center;
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
        
        .form-input:focus {
            border-color: #6366F1;
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
        
        .auth-switch {
            text-align: center;
            color: #6366F1;
            cursor: pointer;
        }
        
        .users-list {
            background: #252642;
            margin: 1rem;
            padding: 1rem;
            border-radius: 12px;
            flex: 1;
            overflow-y: auto;
        }
        
        .user-list-item {
            padding: 0.5rem;
            margin: 0.2rem 0;
            border-radius: 6px;
            display: flex;
            align-items: center;
            gap: 0.5rem;
            cursor: pointer;
        }
        
        .user-list-item:hover {
            background: #1A1B2E;
        }
        
        .user-online-indicator {
            width: 8px;
            height: 8px;
            background: #10B981;
            border-radius: 50%;
        }
        
        .user-admin-indicator {
            color: #F59E0B;
            margin-left: auto;
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
            max-height: 90vh;
            overflow-y: auto;
        }
        
        .image-preview {
            max-width: 100%;
            max-height: 400px;
            border-radius: 8px;
        }
        
        .recording-indicator {
            display: none;
            color: #EF4444;
            align-items: center;
            gap: 0.5rem;
            margin-top: 0.5rem;
        }
        
        .pulse {
            animation: pulse 1s infinite;
        }
        
        @keyframes pulse {
            0% { opacity: 1; }
            50% { opacity: 0.5; }
            100% { opacity: 1; }
        }
        
        /* Специальные стили для iOS */
        @supports (-webkit-touch-callout: none) {
            .messages-container {
                padding-bottom: 120px;
            }
            
            .input-area {
                padding-bottom: calc(1rem + env(safe-area-inset-bottom));
            }
        }
    </style>
</head>
<body>
    <!-- Экран аутентификации -->
    <div id="authScreen" class="auth-container">
        <div class="auth-left">
            <div class="auth-left-content">
                <h1>Tandau</h1>
                <p style="font-size: 1.2rem; margin-bottom: 2rem;">Адаптивный мессенджер</p>
                <div style="text-align: left;">
                    <div style="margin: 1rem 0; font-size: 1.1rem;">📱 Полная адаптивность</div>
                    <div style="margin: 1rem 0; font-size: 1.1rem;">🌐 Реальное время</div>
                    <div style="margin: 1rem 0; font-size: 1.1rem;">👥 Приватные чаты</div>
                    <div style="margin: 1rem 0; font-size: 1.1rem;">📎 Медиа и файлы</div>
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
                    <div class="auth-switch" onclick="showRegister()">
                        Нет аккаунта? Зарегистрироваться
                    </div>
                </div>
                <div id="registerForm" style="display: none;">
                    <input type="text" id="registerUsername" class="form-input" placeholder="Имя пользователя">
                    <input type="password" id="registerPassword" class="form-input" placeholder="Пароль">
                    <input type="password" id="registerConfirm" class="form-input" placeholder="Повторите пароль">
                    <button class="auth-button" onclick="register()">Зарегистрироваться</button>
                    <div class="auth-switch" onclick="showLogin()">
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
            <button class="mobile-menu-btn" onclick="showMobileActions()">⋯</button>
        </div>

        <!-- Боковая панель -->
        <div class="sidebar" id="sidebar">
            <div class="sidebar-header">
                <h1>Tandau</h1>
                <p>Mobile Messenger</p>
            </div>
            
            <div class="user-info">
                <div class="user-avatar" id="userAvatar">US</div>
                <div class="user-details">
                    <h3 id="userName">User</h3>
                    <div class="status-online">🟢 В сети</div>
                </div>
            </div>

            <div class="nav-menu">
                <div class="nav-item active" onclick="switchChat('public')">
                    <i>🌐</i>
                    <span>Публичный чат</span>
                </div>
                <div class="nav-item" onclick="showPrivateChats()">
                    <i>👥</i>
                    <span>Приватные чаты</span>
                </div>
                <div class="nav-item" onclick="showChannels()">
                    <i>📢</i>
                    <span>Каналы</span>
                </div>
                <div class="nav-item" onclick="showSettings()">
                    <i>⚙️</i>
                    <span>Настройки</span>
                </div>
            </div>

            <div class="users-list">
                <h4 style="margin-bottom: 1rem;">Онлайн сейчас:</h4>
                <div id="onlineUsersList"></div>
            </div>
        </div>

        <!-- Область чата -->
        <div class="chat-area">
            <div class="chat-header mobile-hidden">
                <h2 id="chatTitle">🌐 Публичный чат</h2>
                <div class="chat-actions">
                    <button class="action-btn" onclick="showFileUpload()">
                        <i>📎</i> Файл
                    </button>
                    <button class="action-btn" onclick="startVoiceRecording()">
                        <i>🎤</i> Запись
                    </button>
                    <button class="action-btn" onclick="takePhoto()">
                        <i>📷</i> Фото
                    </button>
                </div>
            </div>

            <div class="messages-container" id="messagesContainer">
                <div class="system-message">
                    Добро пожаловать в Tandau Messenger! 🎉
                </div>
            </div>

            <div class="input-area">
                <div class="input-container">
                    <div class="media-buttons">
                        <button class="media-btn" onclick="showEmojiPicker()">
                            <i>😊</i>
                        </button>
                        <button class="media-btn" onclick="showImageUpload()">
                            <i>🖼️</i>
                        </button>
                    </div>
                    <textarea id="messageInput" class="message-input" 
                           placeholder="Введите сообщение..." disabled rows="1"></textarea>
                    <button id="sendButton" class="send-button" onclick="sendMessage()" disabled>
                        Отправить
                    </button>
                </div>
                <div id="recordingIndicator" class="recording-indicator">
                    <div class="pulse">●</div> Запись голосового сообщения...
                    <button onclick="stopVoiceRecording()">Остановить</button>
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
                <button class="nav-tab" onclick="switchMobileTab('contacts')">
                    <i>👥</i>
                    <span>Контакты</span>
                </button>
                <button class="nav-tab" onclick="switchMobileTab('channels')">
                    <i>📢</i>
                    <span>Каналы</span>
                </button>
                <button class="nav-tab" onclick="switchMobileTab('settings')">
                    <i>⚙️</i>
                    <span>Настройки</span>
                </button>
            </div>
        </div>
    </div>

    <!-- Модальные окна -->
    <div id="imageModal" class="modal">
        <div class="modal-content">
            <img id="modalImage" class="image-preview" src="" alt="Preview">
            <button onclick="closeModal('imageModal')" style="margin-top: 1rem; width: 100%;">Закрыть</button>
        </div>
    </div>

    <div id="fileUploadModal" class="modal">
        <div class="modal-content">
            <h3 style="margin-bottom: 1rem;">Загрузка файла</h3>
            <input type="file" id="fileInput" style="margin-bottom: 1rem; width: 100%;">
            <button onclick="uploadFile()" style="margin-right: 0.5rem;">Загрузить</button>
            <button onclick="closeModal('fileUploadModal')">Отмена</button>
        </div>
    </div>

    <div id="imageUploadModal" class="modal">
        <div class="modal-content">
            <h3 style="margin-bottom: 1rem;">Загрузка изображения</h3>
            <input type="file" id="imageInput" accept="image/*" style="margin-bottom: 1rem; width: 100%;">
            <button onclick="uploadImage()" style="margin-right: 0.5rem;">Загрузить</button>
            <button onclick="closeModal('imageUploadModal')">Отмена</button>
        </div>
    </div>

    <script>
        let ws = null;
        let currentUser = null;
        let token = null;
        let currentChat = 'public';
        let mediaRecorder = null;
        let audioChunks = [];
        let isRecording = false;
        let isMobile = window.innerWidth <= 768;

        // Определение мобильного устройства
        function checkMobile() {
            isMobile = window.innerWidth <= 768;
            if (isMobile) {
                document.body.classList.add('mobile');
                document.getElementById('mobileChatTitle').textContent = '🌐 Публичный чат';
            } else {
                document.body.classList.remove('mobile');
            }
        }

        // Мобильное меню
        function toggleSidebar() {
            const sidebar = document.getElementById('sidebar');
            sidebar.classList.toggle('mobile-visible');
        }

        function switchMobileTab(tab) {
            // Обновляем активные табы
            document.querySelectorAll('.nav-tab').forEach(t => t.classList.remove('active'));
            event.target.classList.add('active');
            
            switch(tab) {
                case 'chat':
                    // Показываем чат
                    break;
                case 'contacts':
                    alert('Контакты в разработке');
                    break;
                case 'channels':
                    alert('Каналы в разработке');
                    break;
                case 'settings':
                    alert('Настройки в разработке');
                    break;
            }
        }

        function showMobileActions() {
            // Мобильное меню действий
            if (confirm('Выберите действие:\nOK - Загрузить файл\nОтмена - Сделать фото')) {
                showFileUpload();
            } else {
                takePhoto();
            }
        }

        // Адаптация текстового поля
        function adjustTextareaHeight(textarea) {
            textarea.style.height = 'auto';
            const newHeight = Math.min(textarea.scrollHeight, isMobile ? 80 : 120);
            textarea.style.height = newHeight + 'px';
        }

        // Адаптация интерфейса при изменении размера
        window.addEventListener('resize', checkMobile);
        window.addEventListener('orientationchange', function() {
            setTimeout(checkMobile, 100);
        });

        // Остальной JavaScript код остается таким же как в предыдущей версии
        // (функции login, register, WebSocket, отправка сообщений и т.д.)
        // ... [здесь должен быть весь остальной JavaScript код из предыдущей версии]

        // Инициализация при загрузке
        checkMobile();
        showLogin();

        // Добавляем обработчик для свайпов (опционально)
        let startX = 0;
        document.addEventListener('touchstart', e => {
            startX = e.touches[0].clientX;
        });

        document.addEventListener('touchend', e => {
            const endX = e.changedTouches[0].clientX;
            const diff = startX - endX;
            
            if (Math.abs(diff) > 50) { // Минимальная дистанция свайпа
                if (diff > 0 && isMobile) {
                    // Свайп влево - скрыть сайдбар
                    document.getElementById('sidebar').classList.remove('mobile-visible');
                } else if (diff < 0 && isMobile) {
                    // Свайп вправо - показать сайдбар
                    document.getElementById('sidebar').classList.add('mobile-visible');
                }
            }
        });

        // Предотвращаем zoom на полях ввода на мобильных
        document.addEventListener('touchstart', function(e) {
            if (e.target.tagName === 'INPUT' || e.target.tagName === 'TEXTAREA') {
                document.body.style.zoom = "100%";
            }
        });

    </script>
</body>
</html>
"""

# Остальной Python код остается таким же
# ... [здесь должен быть весь остальной Python код из предыдущей версии]

if __name__ == "__main__":
    uvicorn.run(app, host="0.0.0.0", port=8000, log_level="info")