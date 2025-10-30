# web_messenger.py - Веб-версия Tandau Messenger
from flask import Flask, render_template, request, jsonify, session, redirect, url_for
from flask_socketio import SocketIO, emit, join_room, leave_room
import json
import hashlib
import os
import base64
import io
from datetime import datetime
import threading
import socket
import sqlite3
from werkzeug.security import generate_password_hash, check_password_hash
import eventlet
eventlet.monkey_patch()

# Конфигурация
class Config:
    SECRET_KEY = 'tandau-secret-key-2024'
    DATABASE = 'messenger.db'
    SERVER_HOST = "72.44.48.182"
    SERVER_PORT = 5555

app = Flask(__name__)
app.config.from_object(Config)
socketio = SocketIO(app, async_mode='eventlet', cors_allowed_origins="*")

# Инициализация базы данных
def init_db():
    with sqlite3.connect(Config.DATABASE) as conn:
        cursor = conn.cursor()
        
        # Таблица пользователей
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS users (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                username TEXT UNIQUE NOT NULL,
                password_hash TEXT NOT NULL,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                is_online BOOLEAN DEFAULT FALSE,
                last_seen TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        ''')
        
        # Таблица сообщений
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS messages (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER,
                username TEXT NOT NULL,
                message TEXT NOT NULL,
                message_type TEXT DEFAULT 'text',
                timestamp TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                room TEXT DEFAULT 'public',
                FOREIGN KEY (user_id) REFERENCES users (id)
            )
        ''')
        
        conn.commit()

# Утилиты для работы с пользователями
def get_user_by_username(username):
    with sqlite3.connect(Config.DATABASE) as conn:
        cursor = conn.cursor()
        cursor.execute('SELECT * FROM users WHERE username = ?', (username,))
        return cursor.fetchone()

def create_user(username, password):
    with sqlite3.connect(Config.DATABASE) as conn:
        cursor = conn.cursor()
        password_hash = generate_password_hash(password)
        try:
            cursor.execute(
                'INSERT INTO users (username, password_hash) VALUES (?, ?)',
                (username, password_hash)
            )
            conn.commit()
            return True
        except sqlite3.IntegrityError:
            return False

def verify_user(username, password):
    user = get_user_by_username(username)
    if user and check_password_hash(user[2], password):
        return user
    return None

def update_user_online_status(username, is_online):
    with sqlite3.connect(Config.DATABASE) as conn:
        cursor = conn.cursor()
        cursor.execute(
            'UPDATE users SET is_online = ?, last_seen = CURRENT_TIMESTAMP WHERE username = ?',
            (is_online, username)
        )
        conn.commit()

def save_message(username, message, room='public', message_type='text'):
    with sqlite3.connect(Config.DATABASE) as conn:
        cursor = conn.cursor()
        cursor.execute(
            'INSERT INTO messages (username, message, message_type, room) VALUES (?, ?, ?, ?)',
            (username, message, message_type, room)
        )
        conn.commit()

def get_recent_messages(room='public', limit=50):
    with sqlite3.connect(Config.DATABASE) as conn:
        cursor = conn.cursor()
        cursor.execute('''
            SELECT username, message, message_type, timestamp 
            FROM messages 
            WHERE room = ? 
            ORDER BY timestamp DESC 
            LIMIT ?
        ''', (room, limit))
        messages = cursor.fetchall()
        return [{
            'user': msg[0],
            'message': msg[1],
            'type': msg[2],
            'timestamp': msg[3]
        } for msg in reversed(messages)]

def get_online_users():
    with sqlite3.connect(Config.DATABASE) as conn:
        cursor = conn.cursor()
        cursor.execute('SELECT username FROM users WHERE is_online = TRUE')
        return [user[0] for user in cursor.fetchall()]

# Маршруты Flask
@app.route('/')
def index():
    if 'username' in session:
        return redirect(url_for('chat'))
    return render_template('login.html')

@app.route('/login', methods=['POST'])
def login():
    username = request.form.get('username')
    password = request.form.get('password')
    
    user = verify_user(username, password)
    if user:
        session['username'] = username
        session['user_id'] = user[0]
        update_user_online_status(username, True)
        return jsonify({'success': True})
    else:
        return jsonify({'success': False, 'error': 'Неверное имя пользователя или пароль'})

@app.route('/register', methods=['POST'])
def register():
    username = request.form.get('username')
    password = request.form.get('password')
    confirm_password = request.form.get('confirm_password')
    
    if not username or not password:
        return jsonify({'success': False, 'error': 'Заполните все поля'})
    
    if password != confirm_password:
        return jsonify({'success': False, 'error': 'Пароли не совпадают'})
    
    if len(username) < 3:
        return jsonify({'success': False, 'error': 'Имя пользователя должно быть не менее 3 символов'})
    
    if len(password) < 4:
        return jsonify({'success': False, 'error': 'Пароль должен быть не менее 4 символов'})
    
    if create_user(username, password):
        return jsonify({'success': True})
    else:
        return jsonify({'success': False, 'error': 'Пользователь с таким именем уже существует'})

@app.route('/chat')
def chat():
    if 'username' not in session:
        return redirect(url_for('index'))
    
    messages = get_recent_messages()
    online_users = get_online_users()
    
    return render_template('chat.html', 
                         username=session['username'],
                         messages=messages,
                         online_users=online_users)

@app.route('/logout')
def logout():
    if 'username' in session:
        update_user_online_status(session['username'], False)
        session.pop('username', None)
        session.pop('user_id', None)
    return redirect(url_for('index'))

@app.route('/api/messages')
def get_messages():
    if 'username' not in session:
        return jsonify({'error': 'Не авторизован'}), 401
    
    room = request.args.get('room', 'public')
    messages = get_recent_messages(room)
    return jsonify(messages)

@app.route('/api/users/online')
def get_online_users_api():
    if 'username' not in session:
        return jsonify({'error': 'Не авторизован'}), 401
    
    online_users = get_online_users()
    return jsonify(online_users)

# WebSocket события
@socketio.on('connect')
def handle_connect():
    if 'username' in session:
        join_room('public')
        update_user_online_status(session['username'], True)
        emit('user_joined', {
            'username': session['username'],
            'online_users': get_online_users()
        }, room='public', include_self=False)
        emit('online_users', get_online_users(), room='public')

@socketio.on('disconnect')
def handle_disconnect():
    if 'username' in session:
        update_user_online_status(session['username'], False)
        emit('user_left', {
            'username': session['username'],
            'online_users': get_online_users()
        }, room='public')
        leave_room('public')

@socketio.on('send_message')
def handle_send_message(data):
    if 'username' not in session:
        return
    
    message = data.get('message', '').strip()
    if not message:
        return
    
    # Сохраняем сообщение в БД
    save_message(session['username'], message)
    
    # Отправляем сообщение всем в комнате
    emit('new_message', {
        'user': session['username'],
        'message': message,
        'timestamp': datetime.now().isoformat(),
        'type': 'text'
    }, room='public')

@socketio.on('typing')
def handle_typing(data):
    if 'username' in session:
        emit('user_typing', {
            'username': session['username'],
            'is_typing': data.get('is_typing', False)
        }, room='public', include_self=False)

# HTML шаблоны
@app.route('/templates/<template_name>')
def serve_template(template_name):
    templates = {
        'login.html': '''
<!DOCTYPE html>
<html lang="ru">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>Tandau Messenger - Вход</title>
    <style>
        * {
            margin: 0;
            padding: 0;
            box-sizing: border-box;
        }
        
        body {
            font-family: 'Segoe UI', Tahoma, Geneva, Verdana, sans-serif;
            background: linear-gradient(135deg, #0F0F1A 0%, #1A1B2E 100%);
            min-height: 100vh;
            display: flex;
            align-items: center;
            justify-content: center;
            color: white;
        }
        
        .container {
            display: flex;
            width: 1000px;
            height: 600px;
            background: #252642;
            border-radius: 20px;
            overflow: hidden;
            box-shadow: 0 20px 40px rgba(0,0,0,0.3);
        }
        
        .left-panel {
            flex: 1;
            background: linear-gradient(135deg, #6366F1 0%, #8B5CF6 100%);
            padding: 60px 40px;
            display: flex;
            flex-direction: column;
            justify-content: center;
        }
        
        .right-panel {
            flex: 1;
            background: #1A1B2E;
            padding: 60px 40px;
            display: flex;
            flex-direction: column;
            justify-content: center;
        }
        
        .logo {
            font-size: 48px;
            font-weight: bold;
            margin-bottom: 10px;
        }
        
        .subtitle {
            font-size: 18px;
            opacity: 0.9;
            margin-bottom: 40px;
        }
        
        .feature {
            font-size: 16px;
            margin: 8px 0;
            display: flex;
            align-items: center;
        }
        
        .feature::before {
            content: "✓";
            margin-right: 10px;
            color: #10B981;
        }
        
        .form-title {
            font-size: 32px;
            font-weight: bold;
            margin-bottom: 40px;
            text-align: center;
        }
        
        .form-group {
            margin-bottom: 20px;
        }
        
        .form-input {
            width: 100%;
            padding: 15px 20px;
            background: #252642;
            border: 2px solid #373755;
            border-radius: 12px;
            color: white;
            font-size: 16px;
            transition: all 0.3s ease;
        }
        
        .form-input:focus {
            outline: none;
            border-color: #6366F1;
        }
        
        .form-input::placeholder {
            color: #6B6B8B;
        }
        
        .btn {
            width: 100%;
            padding: 15px;
            background: #6366F1;
            color: white;
            border: none;
            border-radius: 12px;
            font-size: 16px;
            font-weight: bold;
            cursor: pointer;
            transition: all 0.3s ease;
        }
        
        .btn:hover {
            background: #4F46E5;
            transform: translateY(-2px);
        }
        
        .btn-register {
            background: #10B981;
        }
        
        .btn-register:hover {
            background: #059669;
        }
        
        .link {
            color: #6366F1;
            text-decoration: none;
            text-align: center;
            display: block;
            margin: 15px 0;
            cursor: pointer;
        }
        
        .link:hover {
            text-decoration: underline;
        }
        
        .status {
            text-align: center;
            margin-bottom: 20px;
            padding: 10px;
            border-radius: 8px;
            font-weight: bold;
        }
        
        .status.connecting {
            background: #F59E0B;
            color: white;
        }
        
        .status.connected {
            background: #10B981;
            color: white;
        }
        
        .status.error {
            background: #EF4444;
            color: white;
        }
        
        .alert {
            padding: 12px;
            border-radius: 8px;
            margin-bottom: 20px;
            text-align: center;
            font-weight: bold;
        }
        
        .alert.error {
            background: #EF4444;
            color: white;
        }
        
        .alert.success {
            background: #10B981;
            color: white;
        }
        
        .hidden {
            display: none;
        }
    </style>
</head>
<body>
    <div class="container">
        <div class="left-panel">
            <div class="logo">Tandau</div>
            <div class="subtitle">Современный веб-мессенджер</div>
            <div class="feature">🔒 Безопасное общение</div>
            <div class="feature">🌐 Поддержка медиа</div>
            <div class="feature">👥 Групповые чаты</div>
            <div class="feature">📱 Адаптивный дизайн</div>
        </div>
        
        <div class="right-panel">
            <div id="login-form">
                <div class="form-title">Вход в систему</div>
                <div id="alert" class="alert hidden"></div>
                <div class="form-group">
                    <input type="text" id="login-username" class="form-input" placeholder="Имя пользователя">
                </div>
                <div class="form-group">
                    <input type="password" id="login-password" class="form-input" placeholder="Пароль">
                </div>
                <button class="btn" onclick="login()">Войти</button>
                <a class="link" onclick="showRegister()">Нет аккаунта? Зарегистрироваться</a>
            </div>
            
            <div id="register-form" class="hidden">
                <div class="form-title">Регистрация</div>
                <div id="register-alert" class="alert hidden"></div>
                <div class="form-group">
                    <input type="text" id="reg-username" class="form-input" placeholder="Придумайте имя пользователя">
                </div>
                <div class="form-group">
                    <input type="password" id="reg-password" class="form-input" placeholder="Придумайте пароль">
                </div>
                <div class="form-group">
                    <input type="password" id="reg-confirm" class="form-input" placeholder="Повторите пароль">
                </div>
                <button class="btn btn-register" onclick="register()">Зарегистрироваться</button>
                <a class="link" onclick="showLogin()">Уже есть аккаунт? Войти</a>
            </div>
        </div>
    </div>

    <script>
        function showAlert(message, type, form = 'login') {
            const alert = document.getElementById(form === 'login' ? 'alert' : 'register-alert');
            alert.textContent = message;
            alert.className = `alert ${type}`;
            alert.classList.remove('hidden');
        }
        
        function hideAlert(form = 'login') {
            const alert = document.getElementById(form === 'login' ? 'alert' : 'register-alert');
            alert.classList.add('hidden');
        }
        
        function showRegister() {
            document.getElementById('login-form').classList.add('hidden');
            document.getElementById('register-form').classList.remove('hidden');
            hideAlert('login');
            hideAlert('register');
        }
        
        function showLogin() {
            document.getElementById('register-form').classList.add('hidden');
            document.getElementById('login-form').classList.remove('hidden');
            hideAlert('login');
            hideAlert('register');
        }
        
        async function login() {
            const username = document.getElementById('login-username').value;
            const password = document.getElementById('login-password').value;
            
            if (!username || !password) {
                showAlert('Заполните все поля', 'error');
                return;
            }
            
            const formData = new FormData();
            formData.append('username', username);
            formData.append('password', password);
            
            try {
                const response = await fetch('/login', {
                    method: 'POST',
                    body: formData
                });
                
                const data = await response.json();
                
                if (data.success) {
                    window.location.href = '/chat';
                } else {
                    showAlert(data.error, 'error');
                }
            } catch (error) {
                showAlert('Ошибка подключения', 'error');
            }
        }
        
        async function register() {
            const username = document.getElementById('reg-username').value;
            const password = document.getElementById('reg-password').value;
            const confirm = document.getElementById('reg-confirm').value;
            
            if (!username || !password || !confirm) {
                showAlert('Заполните все поля', 'error', 'register');
                return;
            }
            
            if (password !== confirm) {
                showAlert('Пароли не совпадают', 'error', 'register');
                return;
            }
            
            if (username.length < 3) {
                showAlert('Имя пользователя должно быть не менее 3 символов', 'error', 'register');
                return;
            }
            
            if (password.length < 4) {
                showAlert('Пароль должен быть не менее 4 символов', 'error', 'register');
                return;
            }
            
            const formData = new FormData();
            formData.append('username', username);
            formData.append('password', password);
            formData.append('confirm_password', confirm);
            
            try {
                const response = await fetch('/register', {
                    method: 'POST',
                    body: formData
                });
                
                const data = await response.json();
                
                if (data.success) {
                    showAlert('Регистрация успешна! Теперь вы можете войти.', 'success', 'register');
                    setTimeout(() => showLogin(), 2000);
                } else {
                    showAlert(data.error, 'error', 'register');
                }
            } catch (error) {
                showAlert('Ошибка подключения', 'error', 'register');
            }
        }
        
        // Enter для отправки форм
        document.addEventListener('keypress', function(event) {
            if (event.key === 'Enter') {
                if (!document.getElementById('login-form').classList.contains('hidden')) {
                    login();
                } else {
                    register();
                }
            }
        });
    </script>
</body>
</html>
        ''',
        
        'chat.html': '''
<!DOCTYPE html>
<html lang="ru">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>Tandau Messenger - Чат</title>
    <style>
        * {
            margin: 0;
            padding: 0;
            box-sizing: border-box;
        }
        
        body {
            font-family: 'Segoe UI', Tahoma, Geneva, Verdana, sans-serif;
            background: #0F0F1A;
            color: white;
            height: 100vh;
            overflow: hidden;
        }
        
        .container {
            display: flex;
            height: 100vh;
        }
        
        .sidebar {
            width: 300px;
            background: #1A1B2E;
            display: flex;
            flex-direction: column;
            border-right: 1px solid #373755;
        }
        
        .sidebar-header {
            background: linear-gradient(135deg, #6366F1 0%, #8B5CF6 100%);
            padding: 30px 20px;
            text-align: center;
        }
        
        .logo {
            font-size: 28px;
            font-weight: bold;
            margin-bottom: 5px;
        }
        
        .subtitle {
            font-size: 14px;
            opacity: 0.9;
        }
        
        .user-info {
            background: #252642;
            padding: 20px;
            display: flex;
            align-items: center;
            gap: 15px;
        }
        
        .user-avatar {
            width: 50px;
            height: 50px;
            background: linear-gradient(135deg, #6366F1 0%, #8B5CF6 100%);
            border-radius: 50%;
            display: flex;
            align-items: center;
            justify-content: center;
            font-weight: bold;
            font-size: 18px;
        }
        
        .user-details {
            flex: 1;
        }
        
        .username {
            font-weight: bold;
            font-size: 16px;
        }
        
        .status {
            font-size: 12px;
            color: #10B981;
        }
        
        .nav {
            flex: 1;
            padding: 20px 0;
        }
        
        .nav-item {
            padding: 15px 20px;
            display: flex;
            align-items: center;
            gap: 15px;
            cursor: pointer;
            transition: all 0.3s ease;
        }
        
        .nav-item:hover {
            background: #252642;
        }
        
        .nav-item.active {
            background: #252642;
            border-left: 3px solid #6366F1;
        }
        
        .online-users {
            padding: 20px;
            border-top: 1px solid #373755;
        }
        
        .online-title {
            font-size: 14px;
            color: #A0A0B8;
            margin-bottom: 15px;
        }
        
        .user-list {
            display: flex;
            flex-direction: column;
            gap: 10px;
        }
        
        .online-user {
            display: flex;
            align-items: center;
            gap: 10px;
            font-size: 14px;
        }
        
        .online-indicator {
            width: 8px;
            height: 8px;
            background: #10B981;
            border-radius: 50%;
        }
        
        .chat-area {
            flex: 1;
            display: flex;
            flex-direction: column;
        }
        
        .chat-header {
            background: #1A1B2E;
            padding: 20px 30px;
            border-bottom: 1px solid #373755;
        }
        
        .chat-title {
            font-size: 20px;
            font-weight: bold;
        }
        
        .messages {
            flex: 1;
            padding: 20px 30px;
            overflow-y: auto;
            display: flex;
            flex-direction: column;
            gap: 15px;
        }
        
        .message {
            max-width: 70%;
            padding: 15px 20px;
            border-radius: 18px;
            position: relative;
        }
        
        .message.own {
            align-self: flex-end;
            background: #6366F1;
            border-bottom-right-radius: 5px;
        }
        
        .message.other {
            align-self: flex-start;
            background: #252642;
            border-bottom-left-radius: 5px;
        }
        
        .message-header {
            display: flex;
            justify-content: space-between;
            align-items: center;
            margin-bottom: 5px;
        }
        
        .message-user {
            font-weight: bold;
            font-size: 14px;
        }
        
        .message-time {
            font-size: 12px;
            opacity: 0.7;
        }
        
        .message-text {
            font-size: 15px;
            line-height: 1.4;
        }
        
        .input-area {
            background: #1A1B2E;
            padding: 20px 30px;
            border-top: 1px solid #373755;
        }
        
        .input-container {
            display: flex;
            gap: 15px;
            align-items: flex-end;
        }
        
        .message-input {
            flex: 1;
            background: #252642;
            border: 2px solid #373755;
            border-radius: 25px;
            padding: 15px 20px;
            color: white;
            font-size: 15px;
            resize: none;
            max-height: 120px;
            min-height: 50px;
        }
        
        .message-input:focus {
            outline: none;
            border-color: #6366F1;
        }
        
        .message-input::placeholder {
            color: #6B6B8B;
        }
        
        .send-btn {
            background: #6366F1;
            color: white;
            border: none;
            border-radius: 50%;
            width: 50px;
            height: 50px;
            display: flex;
            align-items: center;
            justify-content: center;
            cursor: pointer;
            transition: all 0.3s ease;
        }
        
        .send-btn:hover {
            background: #4F46E5;
            transform: scale(1.05);
        }
        
        .typing-indicator {
            padding: 10px 30px;
            font-size: 14px;
            color: #A0A0B8;
            font-style: italic;
        }
        
        .logout-btn {
            background: #EF4444;
            color: white;
            border: none;
            padding: 10px 20px;
            border-radius: 8px;
            cursor: pointer;
            margin-top: 10px;
            transition: all 0.3s ease;
        }
        
        .logout-btn:hover {
            background: #DC2626;
        }
        
        .welcome-message {
            text-align: center;
            padding: 40px;
            color: #A0A0B8;
        }
        
        .welcome-title {
            font-size: 24px;
            margin-bottom: 10px;
            color: white;
        }
    </style>
</head>
<body>
    <div class="container">
        <!-- Боковая панель -->
        <div class="sidebar">
            <div class="sidebar-header">
                <div class="logo">Tandau</div>
                <div class="subtitle">Messenger</div>
            </div>
            
            <div class="user-info">
                <div class="user-avatar">{{ username[:2].upper() }}</div>
                <div class="user-details">
                    <div class="username">{{ username }}</div>
                    <div class="status">🟢 В сети</div>
                </div>
            </div>
            
            <div class="nav">
                <div class="nav-item active">
                    <span>🌐</span>
                    <span>Публичный чат</span>
                </div>
                <div class="nav-item">
                    <span>👥</span>
                    <span>Приватные чаты</span>
                </div>
                <div class="nav-item">
                    <span>📢</span>
                    <span>Каналы</span>
                </div>
                <div class="nav-item">
                    <span>⚙️</span>
                    <span>Настройки</span>
                </div>
            </div>
            
            <div class="online-users">
                <div class="online-title">Онлайн ({online_users|length})</div>
                <div class="user-list" id="online-users-list">
                    {% for user in online_users %}
                    <div class="online-user">
                        <div class="online-indicator"></div>
                        <span>{{ user }}</span>
                    </div>
                    {% endfor %}
                </div>
                <button class="logout-btn" onclick="logout()">Выйти</button>
            </div>
        </div>
        
        <!-- Область чата -->
        <div class="chat-area">
            <div class="chat-header">
                <div class="chat-title">🌐 Публичный чат</div>
            </div>
            
            <div class="messages" id="messages">
                {% for message in messages %}
                <div class="message {% if message.user == username %}own{% else %}other{% endif %}">
                    <div class="message-header">
                        <div class="message-user">{{ message.user }}</div>
                        <div class="message-time">{{ message.timestamp[:16] }}</div>
                    </div>
                    <div class="message-text">{{ message.message }}</div>
                </div>
                {% endfor %}
            </div>
            
            <div class="typing-indicator" id="typing-indicator" style="display: none;"></div>
            
            <div class="input-area">
                <div class="input-container">
                    <textarea 
                        class="message-input" 
                        id="message-input" 
                        placeholder="Введите сообщение..." 
                        rows="1"
                    ></textarea>
                    <button class="send-btn" onclick="sendMessage()">
                        <svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
                            <line x1="22" y1="2" x2="11" y2="13"></line>
                            <polygon points="22,2 15,22 11,13 2,9"></polygon>
                        </svg>
                    </button>
                </div>
            </div>
        </div>
    </div>

    <script src="https://cdnjs.cloudflare.com/ajax/libs/socket.io/4.0.1/socket.io.js"></script>
    <script>
        const socket = io();
        const username = "{{ username }}";
        let typingTimer;
        
        // Подключение к WebSocket
        socket.on('connect', function() {
            console.log('Connected to server');
        });
        
        // Новое сообщение
        socket.on('new_message', function(data) {
            addMessage(data);
        });
        
        // Пользователь печатает
        socket.on('user_typing', function(data) {
            const indicator = document.getElementById('typing-indicator');
            if (data.is_typing) {
                indicator.textContent = `${data.username} печатает...`;
                indicator.style.display = 'block';
            } else {
                indicator.style.display = 'none';
            }
        });
        
        // Обновление списка онлайн пользователей
        socket.on('online_users', function(users) {
            updateOnlineUsers(users);
        });
        
        // Пользователь присоединился
        socket.on('user_joined', function(data) {
            addSystemMessage(`${data.username} присоединился к чату`);
            updateOnlineUsers(data.online_users);
        });
        
        // Пользователь вышел
        socket.on('user_left', function(data) {
            addSystemMessage(`${data.username} покинул чат`);
            updateOnlineUsers(data.online_users);
        });
        
        function addMessage(data) {
            const messages = document.getElementById('messages');
            const messageDiv = document.createElement('div');
            messageDiv.className = `message ${data.user === username ? 'own' : 'other'}`;
            
            const time = new Date(data.timestamp).toLocaleTimeString('ru-RU', {
                hour: '2-digit',
                minute: '2-digit'
            });
            
            messageDiv.innerHTML = `
                <div class="message-header">
                    <div class="message-user">${data.user}</div>
                    <div class="message-time">${time}</div>
                </div>
                <div class="message-text">${data.message}</div>
            `;
            
            messages.appendChild(messageDiv);
            scrollToBottom();
        }
        
        function addSystemMessage(text) {
            const messages = document.getElementById('messages');
            const messageDiv = document.createElement('div');
            messageDiv.className = 'welcome-message';
            messageDiv.innerHTML = `<div style="font-size: 14px; opacity: 0.7;">${text}</div>`;
            messages.appendChild(messageDiv);
            scrollToBottom();
        }
        
        function updateOnlineUsers(users) {
            const list = document.getElementById('online-users-list');
            list.innerHTML = '';
            
            users.forEach(user => {
                const userDiv = document.createElement('div');
                userDiv.className = 'online-user';
                userDiv.innerHTML = `
                    <div class="online-indicator"></div>
                    <span>${user}</span>
                `;
                list.appendChild(userDiv);
            });
        }
        
        function sendMessage() {
            const input = document.getElementById('message-input');
            const message = input.value.trim();
            
            if (message) {
                socket.emit('send_message', { message: message });
                input.value = '';
                adjustTextareaHeight();
                
                // Уведомляем, что перестали печатать
                socket.emit('typing', { is_typing: false });
            }
        }
        
        function handleTyping() {
            clearTimeout(typingTimer);
            socket.emit('typing', { is_typing: true });
            
            typingTimer = setTimeout(() => {
                socket.emit('typing', { is_typing: false });
            }, 1000);
        }
        
        function adjustTextareaHeight() {
            const textarea = document.getElementById('message-input');
            textarea.style.height = 'auto';
            textarea.style.height = Math.min(textarea.scrollHeight, 120) + 'px';
        }
        
        function scrollToBottom() {
            const messages = document.getElementById('messages');
            messages.scrollTop = messages.scrollHeight;
        }
        
        function logout() {
            if (confirm('Вы уверены, что хотите выйти?')) {
                window.location.href = '/logout';
            }
        }
        
        // Обработчики событий
        document.getElementById('message-input').addEventListener('input', function() {
            adjustTextareaHeight();
            handleTyping();
        });
        
        document.getElementById('message-input').addEventListener('keypress', function(e) {
            if (e.key === 'Enter' && !e.shiftKey) {
                e.preventDefault();
                sendMessage();
            }
        });
        
        // Автопрокрутка при загрузке
        window.addEventListener('load', scrollToBottom);
    </script>
</body>
</html>
        '''
    }
    
    if template_name in templates:
        return templates[template_name]
    else:
        return "Template not found", 404

if __name__ == '__main__':
    init_db()
    print("Tandau Web Messenger запущен!")
    print("Доступен по адресу: http://localhost:5000")
    socketio.run(app, host='0.0.0.0', port=5000, debug=True)
