# web_messenger.py - Tandau Messenger для Render
from flask import Flask, request, jsonify, session, redirect, send_from_directory, render_template_string
from flask_socketio import SocketIO, emit, join_room, leave_room
import sqlite3
from datetime import datetime
from werkzeug.security import generate_password_hash, check_password_hash
from werkzeug.utils import secure_filename
import random
import os
import base64

# === Конфигурация приложения ===
app = Flask(__name__, static_folder='static')
app.config['SECRET_KEY'] = os.environ.get('SECRET_KEY', 'tandau-secret-key-2025-render')
app.config['UPLOAD_FOLDER'] = 'static/uploads'
app.config['AVATAR_FOLDER'] = 'static/avatars'
app.config['FAVORITE_FOLDER'] = 'static/favorites'
app.config['MAX_CONTENT_LENGTH'] = 16 * 1024 * 1024  # 16MB

# Разрешенные расширения файлов
ALLOWED_EXT = {'png', 'jpg', 'jpeg', 'gif', 'webp', 'mp4', 'webm', 'mov', 'txt', 'pdf', 'doc', 'docx'}

# Создаем папки для загрузок
os.makedirs(app.config['UPLOAD_FOLDER'], exist_ok=True)
os.makedirs(app.config['AVATAR_FOLDER'], exist_ok=True)
os.makedirs(app.config['FAVORITE_FOLDER'], exist_ok=True)

# Инициализация SocketIO - ИСПРАВЛЕНИЕ: убрали async_mode='eventlet'
socketio = SocketIO(app, cors_allowed_origins="*")

# === Инициализация БД ===
def init_db():
    """Инициализация базы данных"""
    with sqlite3.connect('messenger.db', check_same_thread=False) as conn:
        c = conn.cursor()
        
        # Таблица пользователей
        c.execute('''
            CREATE TABLE IF NOT EXISTS users (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                username TEXT UNIQUE NOT NULL,
                password_hash TEXT NOT NULL,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                is_online BOOLEAN DEFAULT FALSE,
                avatar_color TEXT DEFAULT '#6366F1',
                avatar_path TEXT,
                theme TEXT DEFAULT 'light'
            )
        ''')
        
        # Таблица сообщений
        c.execute('''
            CREATE TABLE IF NOT EXISTS messages (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                username TEXT NOT NULL,
                message TEXT,
                timestamp TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                room TEXT DEFAULT 'public',
                recipient TEXT,
                message_type TEXT DEFAULT 'text',
                file_path TEXT,
                file_name TEXT,
                is_favorite BOOLEAN DEFAULT FALSE
            )
        ''')
        
        # Таблица каналов
        c.execute('''
            CREATE TABLE IF NOT EXISTS channels (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                name TEXT UNIQUE NOT NULL,
                display_name TEXT,
                description TEXT,
                created_by TEXT NOT NULL,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                is_private BOOLEAN DEFAULT FALSE,
                allow_messages BOOLEAN DEFAULT TRUE
            )
        ''')
        
        # Таблица участников каналов
        c.execute('''
            CREATE TABLE IF NOT EXISTS channel_members (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                channel_id INTEGER,
                username TEXT NOT NULL,
                joined_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                is_admin BOOLEAN DEFAULT FALSE,
                FOREIGN KEY (channel_id) REFERENCES channels (id),
                UNIQUE(channel_id, username)
            )
        ''')
        
        # Таблица избранного
        c.execute('''
            CREATE TABLE IF NOT EXISTS favorites (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                username TEXT NOT NULL,
                content TEXT,
                file_path TEXT,
                file_name TEXT,
                file_type TEXT DEFAULT 'text',
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                is_pinned BOOLEAN DEFAULT FALSE,
                category TEXT DEFAULT 'general'
            )
        ''')
        
        # Создаем общий канал по умолчанию
        c.execute('INSERT OR IGNORE INTO channels (name, display_name, description, created_by) VALUES (?, ?, ?, ?)',
                  ('general', 'General', 'Общий канал', 'system'))
        
        # Создаем системного пользователя
        c.execute('INSERT OR IGNORE INTO users (username, password_hash) VALUES (?, ?)',
                  ('system', generate_password_hash('system_password')))
        
        conn.commit()

init_db()

# === Утилиты ===
def allowed_file(filename):
    """Проверка расширения файла"""
    return '.' in filename and filename.rsplit('.', 1)[1].lower() in ALLOWED_EXT

def save_uploaded_file(file, folder):
    """Сохранение загруженного файла"""
    if not file or file.filename == '':
        return None, None
    if not allowed_file(file.filename):
        return None, None
    
    filename = secure_filename(f"{int(datetime.now().timestamp())}_{file.filename}")
    path = os.path.join(folder, filename)
    file.save(path)
    return f'/static/{os.path.basename(folder)}/{filename}', filename

def save_base64_file(base64_data, folder, file_extension):
    """Сохранение файла из base64"""
    try:
        if ',' in base64_data:
            base64_data = base64_data.split(',')[1]
        
        file_data = base64.b64decode(base64_data)
        filename = f"{int(datetime.now().timestamp())}.{file_extension}"
        path = os.path.join(folder, filename)
        
        with open(path, 'wb') as f:
            f.write(file_data)
        
        return f'/static/{os.path.basename(folder)}/{filename}', filename
    except Exception as e:
        print(f"Error saving base64 file: {e}")
        return None, None

def get_user(username):
    """Получение информации о пользователе"""
    with sqlite3.connect('messenger.db') as conn:
        c = conn.cursor()
        c.execute('SELECT * FROM users WHERE username = ?', (username,))
        row = c.fetchone()
        if row:
            return {
                'id': row[0],
                'username': row[1],
                'password_hash': row[2],
                'created_at': row[3],
                'is_online': row[4],
                'avatar_color': row[5],
                'avatar_path': row[6],
                'theme': row[7]
            }
        return None

def get_all_users():
    """Получение всех пользователей"""
    with sqlite3.connect('messenger.db') as conn:
        c = conn.cursor()
        c.execute('SELECT username, is_online, avatar_color, avatar_path, theme FROM users WHERE username != "system" ORDER BY username')
        return [dict(zip(['username','online','color','avatar','theme'], row)) for row in c.fetchall()]

def create_user(username, password):
    """Создание нового пользователя"""
    with sqlite3.connect('messenger.db') as conn:
        c = conn.cursor()
        try:
            # Проверяем, существует ли пользователь
            c.execute('SELECT id FROM users WHERE username = ?', (username,))
            if c.fetchone():
                return False, "Пользователь уже существует"
            
            # Создаем пользователя
            avatar_colors = ['#6366F1','#8B5CF6','#10B981','#F59E0B','#EF4444','#3B82F6']
            c.execute('INSERT INTO users (username, password_hash, avatar_color) VALUES (?, ?, ?)',
                      (username, generate_password_hash(password), random.choice(avatar_colors)))
            
            # Добавляем пользователя в общий канал
            c.execute('INSERT OR IGNORE INTO channel_members (channel_id, username) SELECT id, ? FROM channels WHERE name="general"', (username,))
            conn.commit()
            return True, "Пользователь создан успешно"
        except Exception as e:
            return False, f"Ошибка при создании пользователя: {str(e)}"

def verify_user(username, password):
    """Проверка учетных данных пользователя"""
    user = get_user(username)
    if user and check_password_hash(user['password_hash'], password):
        return user
    return None

def update_online(username, status):
    """Обновление статуса онлайн"""
    with sqlite3.connect('messenger.db') as conn:
        c = conn.cursor()
        c.execute('UPDATE users SET is_online = ? WHERE username = ?', (status, username))
        conn.commit()

def save_message(user, msg, room, recipient=None, msg_type='text', file_path=None, file_name=None):
    """Сохранение сообщения в БД"""
    with sqlite3.connect('messenger.db') as conn:
        c = conn.cursor()
        c.execute('INSERT INTO messages (username, message, room, recipient, message_type, file_path, file_name) VALUES (?, ?, ?, ?, ?, ?, ?)',
                  (user, msg, room, recipient, msg_type, file_path, file_name))
        conn.commit()
        return c.lastrowid

def get_messages_for_room(room):
    """Получение сообщений для комнаты"""
    with sqlite3.connect('messenger.db') as conn:
        c = conn.cursor()
        c.execute('''
            SELECT username, message, message_type, file_path, file_name, timestamp 
            FROM messages 
            WHERE room = ? 
            ORDER BY timestamp ASC
        ''', (room,))
        
        messages = []
        for row in c.fetchall():
            user_info = get_user(row[0])
            messages.append({
                'user': row[0],
                'message': row[1],
                'type': row[2],
                'file': row[3],
                'file_name': row[4],
                'timestamp': row[5][11:16] if row[5] else '',
                'color': user_info['avatar_color'] if user_info else '#6366F1',
                'avatar_path': user_info['avatar_path'] if user_info else None
            })
        return messages

def get_user_personal_chats(username):
    """Получение личных чатов пользователя"""
    with sqlite3.connect('messenger.db') as conn:
        c = conn.cursor()
        c.execute('''
            SELECT DISTINCT 
                CASE 
                    WHEN username = ? THEN recipient
                    ELSE username
                END as chat_user
            FROM messages 
            WHERE (username = ? OR recipient = ?) 
            AND room LIKE 'private_%'
            AND chat_user IS NOT NULL
        ''', (username, username, username))
        return [row[0] for row in c.fetchall()]

def add_to_favorites(username, content=None, file_path=None, file_name=None, file_type='text', category='general'):
    """Добавление в избранное"""
    with sqlite3.connect('messenger.db') as conn:
        c = conn.cursor()
        try:
            c.execute('INSERT INTO favorites (username, content, file_path, file_name, file_type, category) VALUES (?, ?, ?, ?, ?, ?)',
                      (username, content, file_path, file_name, file_type, category))
            conn.commit()
            return c.lastrowid
        except Exception as e:
            print(f"Error adding to favorites: {e}")
            return None

def get_favorites(username, category=None):
    """Получение избранного пользователя"""
    with sqlite3.connect('messenger.db') as conn:
        c = conn.cursor()
        if category:
            c.execute('''
                SELECT id, content, file_path, file_name, file_type, created_at, is_pinned, category 
                FROM favorites 
                WHERE username = ? AND category = ?
                ORDER BY is_pinned DESC, created_at DESC
            ''', (username, category))
        else:
            c.execute('''
                SELECT id, content, file_path, file_name, file_type, created_at, is_pinned, category 
                FROM favorites 
                WHERE username = ? 
                ORDER BY is_pinned DESC, created_at DESC
            ''', (username,))
        
        favorites = []
        for row in c.fetchall():
            favorites.append({
                'id': row[0],
                'content': row[1],
                'file_path': row[2],
                'file_name': row[3],
                'file_type': row[4],
                'created_at': row[5],
                'is_pinned': bool(row[6]),
                'category': row[7]
            })
        return favorites

def get_favorite_categories(username):
    """Получение категорий избранного"""
    with sqlite3.connect('messenger.db') as conn:
        c = conn.cursor()
        c.execute('SELECT DISTINCT category FROM favorites WHERE username = ? ORDER BY category', (username,))
        return [row[0] for row in c.fetchall()]

def delete_favorite(favorite_id, username):
    """Удаление из избранного"""
    with sqlite3.connect('messenger.db') as conn:
        c = conn.cursor()
        c.execute('DELETE FROM favorites WHERE id = ? AND username = ?', (favorite_id, username))
        conn.commit()
        return c.rowcount > 0

def toggle_pin_favorite(favorite_id, username):
    """Закрепление/открепление избранного"""
    with sqlite3.connect('messenger.db') as conn:
        c = conn.cursor()
        # Получаем текущее состояние
        c.execute('SELECT is_pinned FROM favorites WHERE id = ? AND username = ?', (favorite_id, username))
        row = c.fetchone()
        if row:
            new_state = not bool(row[0])
            c.execute('UPDATE favorites SET is_pinned = ? WHERE id = ? AND username = ?', 
                     (new_state, favorite_id, username))
            conn.commit()
            return new_state
        return None

def create_channel(name, display_name, description, created_by, is_private=False):
    """Создание канала"""
    with sqlite3.connect('messenger.db') as conn:
        c = conn.cursor()
        try:
            c.execute('INSERT INTO channels (name, display_name, description, created_by, is_private) VALUES (?, ?, ?, ?, ?)',
                      (name, display_name, description, created_by, is_private))
            channel_id = c.lastrowid
            c.execute('INSERT INTO channel_members (channel_id, username, is_admin) VALUES (?, ?, ?)',
                      (channel_id, created_by, True))
            conn.commit()
            return channel_id
        except:
            return None

def get_user_channels(username):
    """Получение каналов пользователя"""
    with sqlite3.connect('messenger.db') as conn:
        c = conn.cursor()
        c.execute('''
            SELECT c.name, c.display_name, c.description, c.is_private, c.allow_messages, c.created_by
            FROM channels c
            JOIN channel_members cm ON c.id = cm.channel_id
            WHERE cm.username = ?
            ORDER BY c.name
        ''', (username,))
        return [{
            'name': row[0],
            'display_name': row[1],
            'description': row[2],
            'is_private': row[3],
            'allow_messages': row[4],
            'created_by': row[5]
        } for row in c.fetchall()]

def get_channel_info(channel_name):
    """Получение информации о канале"""
    with sqlite3.connect('messenger.db') as conn:
        c = conn.cursor()
        c.execute('SELECT id, name, display_name, description, created_by, is_private, allow_messages FROM channels WHERE name = ?', (channel_name,))
        row = c.fetchone()
        if row:
            return {
                'id': row[0],
                'name': row[1],
                'display_name': row[2],
                'description': row[3],
                'created_by': row[4],
                'is_private': row[5],
                'allow_messages': row[6]
            }
        return None

def is_channel_member(channel_name, username):
    """Проверка, является ли пользователь участником канала"""
    with sqlite3.connect('messenger.db') as conn:
        c = conn.cursor()
        c.execute('''
            SELECT 1 FROM channel_members cm
            JOIN channels c ON cm.channel_id = c.id
            WHERE c.name = ? AND cm.username = ?
        ''', (channel_name, username))
        return c.fetchone() is not None

def get_channel_members(channel_name):
    """Получение участников канала"""
    with sqlite3.connect('messenger.db') as conn:
        c = conn.cursor()
        c.execute('''
            SELECT cm.username, cm.is_admin, u.is_online, u.avatar_path, u.avatar_color
            FROM channel_members cm
            JOIN channels c ON cm.channel_id = c.id
            JOIN users u ON cm.username = u.username
            WHERE c.name = ?
            ORDER BY cm.is_admin DESC, cm.username
        ''', (channel_name,))
        return [{
            'username': row[0], 
            'is_admin': row[1], 
            'online': row[2],
            'avatar': row[3],
            'color': row[4]
        } for row in c.fetchall()]

# === HTML Templates ===
INDEX_HTML = '''<!DOCTYPE html>
<html lang="ru">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>Tandau Messenger</title>
    <style>
        * {
            margin: 0;
            padding: 0;
            box-sizing: border-box;
            font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif;
        }
        
        :root {
            --primary: #6366f1;
            --primary-dark: #4f46e5;
            --primary-light: #818cf8;
            --secondary: #8b5cf6;
            --accent: #10b981;
            --text: #1f2937;
            --text-light: #6b7280;
            --bg: #f9fafb;
            --bg-light: #ffffff;
            --border: #e5e7eb;
            --shadow: 0 10px 25px -5px rgba(0, 0, 0, 0.1), 0 10px 10px -5px rgba(0, 0, 0, 0.04);
            --radius: 16px;
            --radius-sm: 10px;
            --transition: all 0.3s cubic-bezier(0.4, 0, 0.2, 1);
        }
        
        body {
            background: linear-gradient(135deg, #667eea 0%, #764ba2 100%);
            min-height: 100vh;
            display: flex;
            align-items: center;
            justify-content: center;
            padding: 20px;
        }
        
        .container {
            width: 100%;
            max-width: 440px;
        }
        
        .logo-section {
            text-align: center;
            margin-bottom: 40px;
            animation: fadeInDown 0.8s ease-out;
        }
        
        .logo-container {
            display: inline-flex;
            align-items: center;
            justify-content: center;
            gap: 15px;
            background: rgba(255, 255, 255, 0.1);
            backdrop-filter: blur(10px);
            padding: 20px 40px;
            border-radius: 24px;
            margin-bottom: 25px;
            box-shadow: 0 8px 32px rgba(0, 0, 0, 0.1);
            border: 1px solid rgba(255, 255, 255, 0.2);
        }
        
        .logo-placeholder {
            width: 60px;
            height: 60px;
            border-radius: 16px;
            background: linear-gradient(135deg, #667eea, #764ba2);
            display: flex;
            align-items: center;
            justify-content: center;
            color: white;
            font-size: 28px;
            font-weight: bold;
            box-shadow: 0 4px 12px rgba(0, 0, 0, 0.15);
        }
        
        .app-title {
            color: white;
            font-size: 2.8rem;
            font-weight: 800;
            letter-spacing: -0.5px;
            text-shadow: 0 2px 10px rgba(0, 0, 0, 0.2);
        }
        
        .app-subtitle {
            color: rgba(255, 255, 255, 0.9);
            font-size: 1.1rem;
            font-weight: 400;
            max-width: 300px;
            margin: 0 auto;
            line-height: 1.5;
        }
        
        .auth-card {
            background: var(--bg-light);
            border-radius: var(--radius);
            box-shadow: var(--shadow);
            overflow: hidden;
            animation: fadeInUp 0.8s ease-out 0.2s both;
        }
        
        .auth-header {
            display: flex;
            background: white;
            border-bottom: 1px solid var(--border);
        }
        
        .auth-tab {
            flex: 1;
            padding: 20px;
            text-align: center;
            font-weight: 600;
            font-size: 1.1rem;
            color: var(--text-light);
            cursor: pointer;
            transition: var(--transition);
            position: relative;
            user-select: none;
        }
        
        .auth-tab:hover {
            color: var(--primary);
            background: rgba(99, 102, 241, 0.05);
        }
        
        .auth-tab.active {
            color: var(--primary);
        }
        
        .auth-tab.active::after {
            content: "";
            position: absolute;
            bottom: 0;
            left: 20%;
            right: 20%;
            height: 3px;
            background: var(--primary);
            border-radius: 3px;
        }
        
        .auth-content {
            padding: 40px;
        }
        
        .auth-form {
            display: none;
            animation: fadeIn 0.5s ease-out;
        }
        
        .auth-form.active {
            display: block;
        }
        
        .form-group {
            margin-bottom: 24px;
        }
        
        .form-label {
            display: block;
            margin-bottom: 8px;
            color: var(--text);
            font-weight: 500;
            font-size: 0.95rem;
        }
        
        .input-with-icon {
            position: relative;
        }
        
        .input-icon {
            position: absolute;
            left: 16px;
            top: 50%;
            transform: translateY(-50%);
            color: var(--text-light);
            font-size: 1.1rem;
        }
        
        .form-input {
            width: 100%;
            padding: 16px 16px 16px 48px;
            border: 2px solid var(--border);
            border-radius: var(--radius-sm);
            font-size: 1rem;
            transition: var(--transition);
            background: white;
        }
        
        .form-input:focus {
            outline: none;
            border-color: var(--primary);
            box-shadow: 0 0 0 3px rgba(99, 102, 241, 0.1);
        }
        
        .password-toggle {
            position: absolute;
            right: 16px;
            top: 50%;
            transform: translateY(-50%);
            background: none;
            border: none;
            color: var(--text-light);
            cursor: pointer;
            font-size: 1.1rem;
        }
        
        .btn {
            width: 100%;
            padding: 16px;
            border: none;
            border-radius: var(--radius-sm);
            font-size: 1rem;
            font-weight: 600;
            cursor: pointer;
            transition: var(--transition);
            display: flex;
            align-items: center;
            justify-content: center;
            gap: 10px;
        }
        
        .btn-primary {
            background: var(--primary);
            color: white;
        }
        
        .btn-primary:hover {
            background: var(--primary-dark);
            transform: translateY(-2px);
            box-shadow: 0 6px 20px rgba(99, 102, 241, 0.3);
        }
        
        .btn-primary:active {
            transform: translateY(0);
        }
        
        .btn-google {
            background: white;
            color: var(--text);
            border: 2px solid var(--border);
            margin-top: 16px;
        }
        
        .btn-google:hover {
            background: var(--bg);
            border-color: var(--text-light);
        }
        
        .alert {
            padding: 14px 18px;
            border-radius: var(--radius-sm);
            margin-bottom: 24px;
            display: none;
            animation: slideIn 0.3s ease-out;
        }
        
        .alert-error {
            background: #fee;
            color: #c33;
            border-left: 4px solid #c33;
        }
        
        .alert-success {
            background: #efe;
            color: #363;
            border-left: 4px solid #363;
        }
        
        .terms {
            text-align: center;
            margin-top: 24px;
            color: var(--text-light);
            font-size: 0.9rem;
        }
        
        .terms a {
            color: var(--primary);
            text-decoration: none;
        }
        
        .terms a:hover {
            text-decoration: underline;
        }
        
        @keyframes fadeInUp {
            from {
                opacity: 0;
                transform: translateY(20px);
            }
            to {
                opacity: 1;
                transform: translateY(0);
            }
        }
        
        @keyframes fadeInDown {
            from {
                opacity: 0;
                transform: translateY(-20px);
            }
            to {
                opacity: 1;
                transform: translateY(0);
            }
        }
        
        @keyframes fadeIn {
            from { opacity: 0; }
            to { opacity: 1; }
        }
        
        @keyframes slideIn {
            from {
                opacity: 0;
                transform: translateX(-10px);
            }
            to {
                opacity: 1;
                transform: translateX(0);
            }
        }
        
        @media (max-width: 480px) {
            .container {
                max-width: 100%;
            }
            
            .auth-content {
                padding: 30px 20px;
            }
            
            .app-title {
                font-size: 2.2rem;
            }
            
            .logo-container {
                padding: 15px 30px;
            }
        }
        
        .loader {
            display: inline-block;
            width: 20px;
            height: 20px;
            border: 3px solid rgba(255, 255, 255, 0.3);
            border-radius: 50%;
            border-top-color: white;
            animation: spin 1s ease-in-out infinite;
        }
        
        @keyframes spin {
            to { transform: rotate(360deg); }
        }
    </style>
</head>
<body>
    <div class="container">
        <div class="logo-section">
            <div class="logo-container">
                <div class="logo-placeholder">
                    <i class="fas fa-comments"></i>
                </div>
                <h1 class="app-title">Tandau</h1>
            </div>
            <p class="app-subtitle">Быстрый и безопасный мессенджер для команд и личного общения</p>
        </div>
        
        <div class="auth-card">
            <div class="auth-header">
                <div class="auth-tab active" onclick="showTab('login')">
                    Вход
                </div>
                <div class="auth-tab" onclick="showTab('register')">
                    Регистрация
                </div>
            </div>
            
            <div class="auth-content">
                <div id="alert" class="alert"></div>
                
                <form id="login-form" class="auth-form active">
                    <div class="form-group">
                        <label class="form-label">Логин</label>
                        <div class="input-with-icon">
                            <i class="fas fa-user input-icon"></i>
                            <input type="text" class="form-input" id="login-username" placeholder="Введите ваш логин" required>
                        </div>
                    </div>
                    
                    <div class="form-group">
                        <label class="form-label">Пароль</label>
                        <div class="input-with-icon">
                            <i class="fas fa-lock input-icon"></i>
                            <input type="password" class="form-input" id="login-password" placeholder="Введите пароль" required>
                            <button type="button" class="password-toggle" onclick="togglePassword('login-password')">
                                <i class="fas fa-eye"></i>
                            </button>
                        </div>
                    </div>
                    
                    <button type="button" class="btn btn-primary" onclick="login()" id="login-btn">
                        <i class="fas fa-sign-in-alt"></i>
                        Войти в аккаунт
                    </button>
                    
                    <div class="terms">
                        Входя в систему, вы соглашаетесь с нашими 
                        <a href="#" onclick="openTermsModal()">Условиями использования</a>
                    </div>
                </form>
                
                <form id="register-form" class="auth-form">
                    <div class="form-group">
                        <label class="form-label">Придумайте логин</label>
                        <div class="input-with-icon">
                            <i class="fas fa-user-plus input-icon"></i>
                            <input type="text" class="form-input" id="register-username" placeholder="От 3 до 20 символов" required>
                        </div>
                    </div>
                    
                    <div class="form-group">
                        <label class="form-label">Придумайте пароль</label>
                        <div class="input-with-icon">
                            <i class="fas fa-lock input-icon"></i>
                            <input type="password" class="form-input" id="register-password" placeholder="Не менее 4 символов" required>
                            <button type="button" class="password-toggle" onclick="togglePassword('register-password')">
                                <i class="fas fa-eye"></i>
                            </button>
                        </div>
                    </div>
                    
                    <div class="form-group">
                        <label class="form-label">Повторите пароль</label>
                        <div class="input-with-icon">
                            <i class="fas fa-lock input-icon"></i>
                            <input type="password" class="form-input" id="register-confirm" placeholder="Повторите пароль" required>
                            <button type="button" class="password-toggle" onclick="togglePassword('register-confirm')">
                                <i class="fas fa-eye"></i>
                            </button>
                        </div>
                    </div>
                    
                    <button type="button" class="btn btn-primary" onclick="register()" id="register-btn">
                        <i class="fas fa-user-plus"></i>
                        Создать аккаунт
                    </button>
                    
                    <div class="terms">
                        Регистрируясь, вы соглашаетесь с нашими 
                        <a href="#" onclick="openTermsModal()">Условиями использования</a> 
                        и <a href="#">Политикой конфиденциальности</a>
                    </div>
                </form>
            </div>
        </div>
    </div>

    <!-- Модальное окно условий использования -->
    <div id="terms-modal" style="display:none; position:fixed; top:0; left:0; width:100%; height:100%; background:rgba(0,0,0,0.7); backdrop-filter:blur(10px); z-index:2000; align-items:center; justify-content:center;">
        <div style="background:rgba(255,255,255,0.12); backdrop-filter:blur(25px); border-radius:24px; border:1px solid rgba(255,255,255,0.18); padding:40px; max-width:500px; width:90%; color:white; text-align:center;">
            <h2 style="margin-bottom:20px;">Условия использования</h2>
            <p style="margin-bottom:30px; line-height:1.6;">
                Для подробного ознакомления с пользовательским соглашением мессенджера Tandau,
                вы можете скачать полную версию документа.
            </p>
            <a href="/static/terms.pdf" 
               style="display:inline-flex; align-items:center; gap:12px; background:linear-gradient(90deg,#667eea,#764ba2); color:white; padding:14px 28px; border-radius:14px; text-decoration:none; font-weight:600;"
               download="Условия_использования_Tandau.pdf">
                <svg width="24" height="24" viewBox="0 0 24 24" fill="currentColor">
                    <path d="M8.267 14.68c-.184 0-.308.018-.372.036v1.178c.076.018.171.023.302.023.479 0 .774-.242.774-.651 0-.366-.254-.586-.704-.586zm3.487.012c-.2 0-.33.018-.407.036v2.61c.077.018.201.018.313.018.817.006 1.349-.444 1.349-1.396.006-.83-.479-1.268-1.255-1.268z"/>
                    <path d="M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8l-6-6zm1.8 18H6.4V4.5H13V9h4.9v11.5zm-4.6-3.14c0-1.28.96-2.34 2.34-2.34.86 0 1.56.36 2.03.92l.86-1.58c-.76-.74-1.86-1.22-3.09-1.22-2.38 0-4.31 1.92-4.31 4.31 0 2.39 1.93 4.31 4.31 4.31 1.23 0 2.33-.48 3.09-1.22l-.86-1.58c-.47.56-1.17.92-2.03.92-1.38 0-2.34-1.06-2.34-2.34z"/>
                </svg>
                Скачать PDF
                <svg width="20" height="20" viewBox="0 0 24 24" fill="currentColor">
                    <path d="M19 9h-4V3H9v6H5l7 7 7-7zM5 18v2h14v-2H5z"/>
                </svg>
            </a>
            <button onclick="closeTermsModal()" style="margin-top:20px; padding:10px 20px; background:rgba(255,255,255,0.1); border:1px solid rgba(255,255,255,0.2); color:white; border-radius:8px; cursor:pointer;">
                Закрыть
            </button>
        </div>
    </div>

    <script>
        let isLoading = false;
        
        function showAlert(message, type = "error") {
            const alert = document.getElementById("alert");
            alert.textContent = message;
            alert.className = `alert alert-${type}`;
            alert.style.display = "block";
            
            setTimeout(() => {
                alert.style.display = "none";
            }, 5000);
        }
        
        function showTab(tabName) {
            if (isLoading) return;
            
            document.querySelectorAll(".auth-tab").forEach(tab => tab.classList.remove("active"));
            document.querySelectorAll(".auth-form").forEach(form => form.classList.remove("active"));
            
            document.querySelector(`.auth-tab[onclick="showTab('${tabName}')"]`).classList.add("active");
            document.getElementById(`${tabName}-form`).classList.add("active");
        }
        
        function togglePassword(inputId) {
            const input = document.getElementById(inputId);
            const button = input.nextElementSibling;
            const icon = button.querySelector("i");
            
            if (input.type === "password") {
                input.type = "text";
                icon.className = "fas fa-eye-slash";
            } else {
                input.type = "password";
                icon.className = "fas fa-eye";
            }
        }
        
        function setLoading(buttonId, loading) {
            isLoading = loading;
            const button = document.getElementById(buttonId);
            const icon = button.querySelector("i");
            
            if (loading) {
                button.disabled = true;
                button.innerHTML = '<div class="loader"></div> Загрузка...';
            } else {
                button.disabled = false;
                if (buttonId === "login-btn") {
                    button.innerHTML = '<i class="fas fa-sign-in-alt"></i> Войти в аккаунт';
                } else {
                    button.innerHTML = '<i class="fas fa-user-plus"></i> Создать аккаунт';
                }
            }
        }
        
        async function login() {
            if (isLoading) return;
            
            const username = document.getElementById("login-username").value.trim();
            const password = document.getElementById("login-password").value;
            
            if (!username || !password) {
                return showAlert("Заполните все поля");
            }
            
            setLoading("login-btn", true);
            
            try {
                const response = await fetch("/login", {
                    method: "POST",
                    headers: {
                        "Content-Type": "application/x-www-form-urlencoded",
                    },
                    body: new URLSearchParams({ username, password })
                });
                
                const data = await response.json();
                
                if (data.success) {
                    showAlert("Успешный вход! Перенаправляем...", "success");
                    setTimeout(() => {
                        window.location.href = "/chat";
                    }, 1000);
                } else {
                    showAlert(data.error || "Неверный логин или пароль");
                }
            } catch (error) {
                showAlert("Ошибка соединения. Проверьте интернет");
                console.error("Login error:", error);
            } finally {
                setLoading("login-btn", false);
            }
        }
        
        async function register() {
            if (isLoading) return;
            
            const username = document.getElementById("register-username").value.trim();
            const password = document.getElementById("register-password").value;
            const confirm = document.getElementById("register-confirm").value;
            
            if (!username || !password || !confirm) {
                return showAlert("Заполните все поля");
            }
            
            if (username.length < 3) {
                return showAlert("Логин должен быть не менее 3 символов");
            }
            
            if (username.length > 20) {
                return showAlert("Логин должен быть не более 20 символов");
            }
            
            if (password.length < 4) {
                return showAlert("Пароль должен быть не менее 4 символов");
            }
            
            if (password !== confirm) {
                return showAlert("Пароли не совпадают");
            }
            
            setLoading("register-btn", true);
            
            try {
                const response = await fetch("/register", {
                    method: "POST",
                    headers: {
                        "Content-Type": "application/x-www-form-urlencoded",
                    },
                    body: new URLSearchParams({ username, password })
                });
                
                const data = await response.json();
                
                if (data.success) {
                    showAlert("Аккаунт создан! Входим...", "success");
                    
                    setTimeout(async () => {
                        try {
                            const loginResponse = await fetch("/login", {
                                method: "POST",
                                headers: {"Content-Type": "application/x-www-form-urlencoded"},
                                body: new URLSearchParams({ username, password })
                            });
                            
                            const loginData = await loginResponse.json();
                            
                            if (loginData.success) {
                                window.location.href = "/chat";
                            } else {
                                showAlert("Автоматический вход не удался. Войдите вручную.");
                                showTab("login");
                            }
                        } catch (error) {
                            showAlert("Ошибка автоматического входа. Войдите вручную.");
                            showTab("login");
                        }
                    }, 1500);
                } else {
                    showAlert(data.error || "Ошибка регистрации");
                }
            } catch (error) {
                showAlert("Ошибка соединения. Проверьте интернет");
                console.error("Register error:", error);
            } finally {
                setLoading("register-btn", false);
            }
        }
        
        function openTermsModal() {
            document.getElementById("terms-modal").style.display = "flex";
        }
        
        function closeTermsModal() {
            document.getElementById("terms-modal").style.display = "none";
        }
        
        document.addEventListener("keypress", function(e) {
            if (e.key === "Enter") {
                const activeForm = document.querySelector(".auth-form.active");
                if (activeForm.id === "login-form") login();
                if (activeForm.id === "register-form") register();
            }
        });
        
        document.addEventListener("DOMContentLoaded", function() {
            const inputs = document.querySelectorAll(".form-input");
            inputs.forEach(input => {
                input.addEventListener("focus", function() {
                    this.parentElement.style.transform = "translateY(-2px)";
                });
                
                input.addEventListener("blur", function() {
                    this.parentElement.style.transform = "translateY(0)";
                });
            });
        });
        
        // Закрыть модальное окно при клике на фон
        document.getElementById("terms-modal").addEventListener("click", function(e) {
            if (e.target === this) {
                closeTermsModal();
            }
        });
        
        // Загружаем Font Awesome
        const faScript = document.createElement("script");
        faScript.src = "https://cdnjs.cloudflare.com/ajax/libs/font-awesome/6.5.0/js/all.min.js";
        document.head.appendChild(faScript);
    </script>
</body>
</html>'''

# === Маршруты Flask ===
@app.route('/')
def index():
    """Главная страница с авторизацией"""
    if 'username' in session:
        return redirect('/chat')
    return INDEX_HTML

@app.route('/login', methods=['POST'])
def login_handler():
    """Обработка входа"""
    username = request.form.get('username', '').strip()
    password = request.form.get('password', '')
    
    if not username or not password:
        return jsonify({'success': False, 'error': 'Заполните все поля'})
    
    user = verify_user(username, password)
    if user:
        session['username'] = username
        update_online(username, True)
        return jsonify({'success': True})
    
    return jsonify({'success': False, 'error': 'Неверный логин или пароль'})

@app.route('/register', methods=['POST'])
def register_handler():
    """Обработка регистрации"""
    username = request.form.get('username', '').strip()
    password = request.form.get('password', '')
    
    if not username or not password:
        return jsonify({'success': False, 'error': 'Заполните все поля'})
    
    if len(username) < 3:
        return jsonify({'success': False, 'error': 'Логин должен быть не менее 3 символов'})
    
    if len(password) < 4:
        return jsonify({'success': False, 'error': 'Пароль должен быть не менее 4 символов'})
    
    success, message = create_user(username, password)
    if success:
        return jsonify({'success': True})
    return jsonify({'success': False, 'error': message})

@app.route('/logout')
def logout_handler():
    """Выход из системы"""
    if 'username' in session:
        update_online(session['username'], False)
        session.pop('username', None)
    return redirect('/')

@app.route('/chat')
def chat_handler():
    """Страница чата"""
    if 'username' not in session:
        return redirect('/')
    
    username = session['username']
    user = get_user(username)
    if not user:
        session.pop('username', None)
        return redirect('/')
    
    # Более простая HTML страница чата
    return f'''<!DOCTYPE html>
<html lang="ru" data-theme="{user.get('theme', 'light')}">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>Tandau Chat - {username}</title>
    <script src="https://cdnjs.cloudflare.com/ajax/libs/socket.io/4.7.2/socket.io.js"></script>
    <style>
        :root {{
            --bg: #f8f9fa;
            --text: #333;
            --input: #fff;
            --border: #ddd;
            --accent: #667eea;
            --primary: #6366f1;
        }}
        
        [data-theme="dark"] {{
            --bg: #1a1a1a;
            --text: #eee;
            --input: #2d2d2d;
            --border: #444;
            --accent: #8b5cf6;
        }}
        
        * {{
            margin: 0;
            padding: 0;
            box-sizing: border-box;
        }}
        
        body {{
            font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif;
            background: var(--bg);
            color: var(--text);
            height: 100vh;
            display: flex;
            flex-direction: column;
        }}
        
        .header {{
            padding: 15px 20px;
            background: var(--input);
            border-bottom: 1px solid var(--border);
            display: flex;
            justify-content: space-between;
            align-items: center;
        }}
        
        .logo {{
            font-weight: 700;
            font-size: 1.2rem;
            color: var(--accent);
        }}
        
        .user-info {{
            display: flex;
            align-items: center;
            gap: 10px;
        }}
        
        .avatar {{
            width: 36px;
            height: 36px;
            border-radius: 50%;
            background: var(--accent);
            color: white;
            display: flex;
            align-items: center;
            justify-content: center;
            font-weight: bold;
        }}
        
        .logout-btn {{
            padding: 8px 16px;
            background: #dc3545;
            color: white;
            border: none;
            border-radius: 6px;
            cursor: pointer;
            font-size: 0.9rem;
        }}
        
        .chat-container {{
            flex: 1;
            display: flex;
        }}
        
        .sidebar {{
            width: 250px;
            background: var(--input);
            border-right: 1px solid var(--border);
            display: flex;
            flex-direction: column;
        }}
        
        .sidebar-section {{
            padding: 15px;
        }}
        
        .section-title {{
            font-weight: 600;
            margin-bottom: 10px;
            color: #666;
        }}
        
        .channel-list, .user-list {{
            list-style: none;
        }}
        
        .channel-item, .user-item {{
            padding: 8px 12px;
            margin: 4px 0;
            border-radius: 6px;
            cursor: pointer;
            transition: background 0.2s;
        }}
        
        .channel-item:hover, .user-item:hover {{
            background: var(--bg);
        }}
        
        .channel-item.active, .user-item.active {{
            background: var(--accent);
            color: white;
        }}
        
        .chat-area {{
            flex: 1;
            display: flex;
            flex-direction: column;
        }}
        
        .chat-header {{
            padding: 15px 20px;
            background: var(--input);
            border-bottom: 1px solid var(--border);
            font-weight: 600;
        }}
        
        .messages {{
            flex: 1;
            padding: 20px;
            overflow-y: auto;
            display: flex;
            flex-direction: column;
            gap: 15px;
        }}
        
        .message {{
            display: flex;
            gap: 12px;
            max-width: 70%;
        }}
        
        .message.own {{
            align-self: flex-end;
            flex-direction: row-reverse;
        }}
        
        .message-avatar {{
            width: 32px;
            height: 32px;
            border-radius: 50%;
            background: var(--accent);
            color: white;
            display: flex;
            align-items: center;
            justify-content: center;
            font-weight: bold;
            font-size: 0.9rem;
            flex-shrink: 0;
        }}
        
        .message-content {{
            background: var(--input);
            padding: 12px 16px;
            border-radius: 18px;
            border-top-left-radius: 4px;
            box-shadow: 0 1px 2px rgba(0,0,0,0.1);
        }}
        
        .message.own .message-content {{
            background: var(--accent);
            color: white;
            border-top-left-radius: 18px;
            border-top-right-radius: 4px;
        }}
        
        .message-text {{
            word-break: break-word;
            line-height: 1.4;
        }}
        
        .message-sender {{
            font-weight: 600;
            font-size: 0.85rem;
            margin-bottom: 4px;
        }}
        
        .message-time {{
            font-size: 0.75rem;
            color: #666;
            margin-top: 4px;
            text-align: right;
        }}
        
        .message.own .message-time {{
            color: rgba(255,255,255,0.8);
        }}
        
        .input-area {{
            padding: 15px 20px;
            background: var(--input);
            border-top: 1px solid var(--border);
        }}
        
        .input-row {{
            display: flex;
            gap: 10px;
        }}
        
        .msg-input {{
            flex: 1;
            padding: 12px 16px;
            border: 1px solid var(--border);
            border-radius: 25px;
            background: var(--bg);
            color: var(--text);
            font-size: 1rem;
            resize: none;
            min-height: 44px;
            max-height: 120px;
        }}
        
        .msg-input:focus {{
            outline: none;
            border-color: var(--accent);
        }}
        
        .send-btn {{
            width: 44px;
            height: 44px;
            border-radius: 50%;
            background: var(--accent);
            color: white;
            border: none;
            cursor: pointer;
            display: flex;
            align-items: center;
            justify-content: center;
            flex-shrink: 0;
        }}
        
        @media (max-width: 768px) {{
            .sidebar {{
                display: none;
            }}
            
            .message {{
                max-width: 85%;
            }}
        }}
    </style>
</head>
<body>
    <div class="header">
        <div class="logo">Tandau Messenger</div>
        <div class="user-info">
            <div class="avatar" id="user-avatar">{username[:2].upper()}</div>
            <button class="logout-btn" onclick="location.href='/logout'">Выйти</button>
        </div>
    </div>
    
    <div class="chat-container">
        <div class="sidebar">
            <div class="sidebar-section">
                <div class="section-title">Каналы</div>
                <ul class="channel-list" id="channel-list">
                    <li class="channel-item active" onclick="joinRoom('general')"># General</li>
                </ul>
            </div>
            
            <div class="sidebar-section">
                <div class="section-title">Пользователи</div>
                <ul class="user-list" id="user-list">
                    <!-- Пользователи загружаются динамически -->
                </ul>
            </div>
        </div>
        
        <div class="chat-area">
            <div class="chat-header" id="chat-title"># General</div>
            
            <div class="messages" id="messages">
                <div class="message">
                    <div class="message-avatar">TA</div>
                    <div class="message-content">
                        <div class="message-sender">System</div>
                        <div class="message-text">Добро пожаловать в Tandau Messenger, {username}!</div>
                        <div class="message-time">{datetime.now().strftime("%H:%M")}</div>
                    </div>
                </div>
            </div>
            
            <div class="input-area">
                <div class="input-row">
                    <textarea class="msg-input" id="msg-input" placeholder="Написать сообщение..." rows="1"></textarea>
                    <button class="send-btn" onclick="sendMessage()">
                        <svg width="20" height="20" viewBox="0 0 24 24" fill="currentColor">
                            <path d="M2.01 21L23 12 2.01 3 2 10l15 2-15 2z"/>
                        </svg>
                    </button>
                </div>
            </div>
        </div>
    </div>

    <script>
        const socket = io();
        const user = "{username}";
        let currentRoom = "general";
        
        socket.on("connect", () => {{
            console.log("Connected to server");
            joinRoom("general");
            loadUsers();
        }});
        
        socket.on("message", (data) => {{
            if (data.room === currentRoom) {{
                addMessage(data);
            }}
        }});
        
        socket.on("user_joined", (data) => {{
            console.log("User joined:", data.username);
        }});
        
        socket.on("user_left", (data) => {{
            console.log("User left:", data.username);
        }});
        
        function joinRoom(room) {{
            if (currentRoom) {{
                socket.emit("leave", {{ room: currentRoom }});
            }}
            
            currentRoom = room;
            socket.emit("join", {{ room: room }});
            document.getElementById("chat-title").textContent = "# " + (room === "general" ? "General" : room);
            
            // Обновляем активный элемент
            document.querySelectorAll(".channel-item, .user-item").forEach(item => {{
                item.classList.remove("active");
            }});
            event.currentTarget.classList.add("active");
            
            // Загружаем историю сообщений
            loadMessages(room);
        }}
        
        function loadMessages(room) {{
            fetch(`/messages/${{room}}`)
                .then(response => response.json())
                .then(messages => {{
                    const container = document.getElementById("messages");
                    container.innerHTML = "";
                    
                    if (messages.length === 0) {{
                        container.innerHTML = '<div style="text-align:center; padding:40px; color:#666;">Нет сообщений</div>';
                    }} else {{
                        messages.forEach(msg => addMessage(msg));
                    }}
                    
                    container.scrollTop = container.scrollHeight;
                }});
        }}
        
        function addMessage(data) {{
            const container = document.getElementById("messages");
            const isOwn = data.user === user;
            
            const messageDiv = document.createElement("div");
            messageDiv.className = `message ${{isOwn ? "own" : ""}}`;
            
            const avatar = document.createElement("div");
            avatar.className = "message-avatar";
            avatar.textContent = data.user.substring(0, 2).toUpperCase();
            avatar.style.backgroundColor = data.color || "#667eea";
            
            const content = document.createElement("div");
            content.className = "message-content";
            
            if (!isOwn) {{
                const sender = document.createElement("div");
                sender.className = "message-sender";
                sender.textContent = data.user;
                content.appendChild(sender);
            }}
            
            const text = document.createElement("div");
            text.className = "message-text";
            text.textContent = data.message;
            
            const time = document.createElement("div");
            time.className = "message-time";
            time.textContent = data.timestamp || new Date().toLocaleTimeString([], {{ hour: "2-digit", minute: "2-digit" }});
            
            content.appendChild(text);
            content.appendChild(time);
            messageDiv.appendChild(avatar);
            messageDiv.appendChild(content);
            container.appendChild(messageDiv);
            
            container.scrollTop = container.scrollHeight;
        }}
        
        function sendMessage() {{
            const input = document.getElementById("msg-input");
            const message = input.value.trim();
            
            if (!message) return;
            
            socket.emit("message", {{
                message: message,
                room: currentRoom,
                user: user
            }});
            
            input.value = "";
            input.focus();
            autoResizeTextarea();
        }}
        
        function loadUsers() {{
            fetch("/users")
                .then(response => response.json())
                .then(users => {{
                    const container = document.getElementById("user-list");
                    container.innerHTML = "";
                    
                    users.forEach(u => {{
                        if (u.username !== user) {{
                            const item = document.createElement("li");
                            item.className = "user-item";
                            item.textContent = u.username;
                            item.onclick = () => joinRoom(`private_${{u.username}}`);
                            container.appendChild(item);
                        }}
                    }});
                }});
        }}
        
        // Инициализация
        document.addEventListener("DOMContentLoaded", () => {{
            // Настройка поля ввода
            const input = document.getElementById("msg-input");
            input.addEventListener("keypress", (e) => {{
                if (e.key === "Enter" && !e.shiftKey) {{
                    e.preventDefault();
                    sendMessage();
                }}
            }});
            
            // Авторазмер textarea
            function autoResizeTextarea() {{
                input.style.height = "auto";
                input.style.height = Math.min(input.scrollHeight, 120) + "px";
            }}
            
            input.addEventListener("input", autoResizeTextarea);
        }});
    </script>
</body>
</html>'''

@app.route('/users')
def users_handler():
    """Получение списка пользователей"""
    if 'username' not in session:
        return jsonify({'error': 'unauthorized'})
    return jsonify(get_all_users())

@app.route('/messages/<room>')
def messages_handler(room):
    """Получение сообщений комнаты"""
    if 'username' not in session:
        return jsonify({'error': 'unauthorized'})
    return jsonify(get_messages_for_room(room))

@app.route('/user_info/<username>')
def user_info_handler(username):
    """Получение информации о пользователе"""
    if 'username' not in session:
        return jsonify({'success': False, 'error': 'unauthorized'})
    
    user = get_user(username)
    if user:
        return jsonify({
            'success': True,
            'username': user['username'],
            'online': user['is_online'],
            'avatar_color': user['avatar_color'],
            'avatar_path': user['avatar_path'],
            'theme': user['theme']
        })
    return jsonify({'success': False, 'error': 'Пользователь не найден'})

# API для избранного
@app.route('/add_to_favorites', methods=['POST'])
def add_to_favorites_handler():
    if 'username' not in session:
        return jsonify({'success': False, 'error': 'Не авторизован'})
    
    content = request.form.get('content', '').strip()
    category = request.form.get('category', 'general').strip()
    
    file_path = None
    file_name = None
    file_type = 'text'
    
    if 'file' in request.files:
        file = request.files['file']
        if file and file.filename:
            path, filename = save_uploaded_file(file, app.config['FAVORITE_FOLDER'])
            if path:
                file_path = path
                file_name = filename
                file_type = 'file'
                content = content or f"Файл: {filename}"
    
    favorite_id = add_to_favorites(
        session['username'],
        content,
        file_path,
        file_name,
        file_type,
        category
    )
    
    if favorite_id:
        return jsonify({'success': True, 'id': favorite_id})
    return jsonify({'success': False, 'error': 'Не удалось добавить в избранное'})

@app.route('/get_favorites')
def get_favorites_handler():
    if 'username' not in session:
        return jsonify({'success': False, 'error': 'Не авторизован'})
    
    category = request.args.get('category', None)
    favorites = get_favorites(session['username'], category)
    return jsonify({'success': True, 'favorites': favorites})

@app.route('/get_favorite_categories')
def get_favorite_categories_handler():
    if 'username' not in session:
        return jsonify({'success': False, 'error': 'Не авторизован'})
    
    categories = get_favorite_categories(session['username'])
    return jsonify({'success': True, 'categories': categories})

@app.route('/delete_favorite/<int:favorite_id>', methods=['DELETE'])
def delete_favorite_handler(favorite_id):
    if 'username' not in session:
        return jsonify({'success': False, 'error': 'Не авторизован'})
    
    if delete_favorite(favorite_id, session['username']):
        return jsonify({'success': True})
    return jsonify({'success': False, 'error': 'Не удалось удалить'})

@app.route('/toggle_pin_favorite/<int:favorite_id>', methods=['POST'])
def toggle_pin_favorite_handler(favorite_id):
    if 'username' not in session:
        return jsonify({'success': False, 'error': 'Не авторизован'})
    
    new_state = toggle_pin_favorite(favorite_id, session['username'])
    if new_state is not None:
        return jsonify({'success': True, 'pinned': new_state})
    return jsonify({'success': False, 'error': 'Не удалось закрепить/открепить'})

# API для каналов
@app.route('/create_channel', methods=['POST'])
def create_channel_handler():
    if 'username' not in session:
        return jsonify({'success': False, 'error': 'Не авторизован'})
    
    name = request.json.get('name', '').strip()
    display_name = request.json.get('display_name', '').strip()
    description = request.json.get('description', '').strip()
    is_private = request.json.get('is_private', False)
    
    if not name or len(name) < 2:
        return jsonify({'success': False, 'error': 'Название канала должно быть не менее 2 символов'})
    
    if not display_name:
        display_name = name.capitalize()
    
    channel_id = create_channel(name, display_name, description, session['username'], is_private)
    if channel_id:
        return jsonify({'success': True, 'channel_name': name, 'display_name': display_name})
    return jsonify({'success': False, 'error': 'Канал с таким названием уже существует'})

@app.route('/user_channels')
def user_channels_handler():
    if 'username' not in session:
        return jsonify({'success': False, 'error': 'Не авторизован'})
    return jsonify({'success': True, 'channels': get_user_channels(session['username'])})

@app.route('/personal_chats')
def personal_chats_handler():
    if 'username' not in session:
        return jsonify({'success': False, 'error': 'Не авторизован'})
    return jsonify({'success': True, 'chats': get_user_personal_chats(session['username'])})

# API для аватарок и тем
@app.route('/upload_avatar', methods=['POST'])
def upload_avatar_handler():
    if 'username' not in session:
        return jsonify({'success': False, 'error': 'Не авторизован'})
    
    if 'avatar' in request.files:
        file = request.files['avatar']
        path, filename = save_uploaded_file(file, app.config['AVATAR_FOLDER'])
    else:
        return jsonify({'success': False, 'error': 'Файл не найден'})
    
    if path:
        with sqlite3.connect('messenger.db') as conn:
            c = conn.cursor()
            c.execute('UPDATE users SET avatar_path = ? WHERE username = ?', (path, session['username']))
            conn.commit()
        return jsonify({'success': True, 'path': path})
    return jsonify({'success': False, 'error': 'Неверный формат файла'})

@app.route('/delete_avatar', methods=['POST'])
def delete_avatar_handler():
    if 'username' not in session:
        return jsonify({'success': False, 'error': 'Не авторизован'})
    with sqlite3.connect('messenger.db') as conn:
        c = conn.cursor()
        c.execute('UPDATE users SET avatar_path = NULL WHERE username = ?', (session['username'],))
        conn.commit()
    return jsonify({'success': True})

@app.route('/set_theme', methods=['POST'])
def set_theme_handler():
    if 'username' not in session:
        return jsonify({'success': False, 'error': 'Не авторизован'})
    theme = request.json.get('theme', 'light')
    if theme not in ['light', 'dark', 'auto']:
        return jsonify({'success': False, 'error': 'Неверная тема'})
    with sqlite3.connect('messenger.db') as conn:
        c = conn.cursor()
        c.execute('UPDATE users SET theme = ? WHERE username = ?', (theme, session['username']))
        conn.commit()
    return jsonify({'success': True})

# Статические файлы
@app.route('/static/<path:filename>')
def static_files(filename):
    return send_from_directory('static', filename)

# Health check
@app.route('/health')
def health_check():
    return jsonify({'status': 'healthy', 'service': 'Tandau Messenger'})

# Обработка 404
@app.errorhandler(404)
def not_found(e):
    return redirect('/')

# === SocketIO события ===
@socketio.on('connect')
def on_connect():
    if 'username' in session:
        update_online(session['username'], True)
        join_room('general')
        emit('user_joined', {'username': session['username']}, room='general')

@socketio.on('disconnect')
def on_disconnect():
    if 'username' in session:
        update_online(session['username'], False)
        emit('user_left', {'username': session['username']}, room='general')

@socketio.on('join')
def on_join(data):
    join_room(data['room'])
    if 'username' in session:
        emit('user_joined', {'username': session['username']}, room=data['room'])

@socketio.on('leave')
def on_leave(data):
    if 'username' in session:
        emit('user_left', {'username': session['username']}, room=data['room'])
    leave_room(data['room'])

@socketio.on('message')
def on_message(data):
    if 'username' not in session:
        return
    
    msg = data.get('message', '').strip()
    room = data.get('room')
    
    if not msg:
        return
    
    # Сохраняем сообщение
    msg_id = save_message(session['username'], msg, room)
    
    # Получаем информацию об отправителе
    user_info = get_user(session['username'])
    user_color = user_info['avatar_color'] if user_info else '#667eea'
    
    # Отправляем сообщение всем в комнате
    emit('message', {
        'user': session['username'],
        'message': msg,
        'color': user_color,
        'timestamp': datetime.now().strftime('%H:%M'),
        'room': room
    }, room=room)

# Запуск приложения
if __name__ == '__main__':
    port = int(os.environ.get('PORT', 5000))
    socketio.run(app, host='0.0.0.0', port=port, debug=False, allow_unsafe_werkzeug=True)
