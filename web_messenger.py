# web_messenger.py - Улучшенная веб-версия Tandau Messenger
from flask import Flask, render_template, request, jsonify, session, redirect, url_for
from flask_socketio import SocketIO, emit, join_room, leave_room
import json
import os
import sqlite3
from datetime import datetime
from werkzeug.security import generate_password_hash, check_password_hash
import eventlet
eventlet.monkey_patch()

# Конфигурация
class Config:
    SECRET_KEY = 'tandau-secret-key-2024'
    DATABASE = 'messenger.db'

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
                last_seen TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                avatar_color TEXT DEFAULT '#6366F1'
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
                recipient_id INTEGER,
                is_read BOOLEAN DEFAULT FALSE,
                FOREIGN KEY (user_id) REFERENCES users (id),
                FOREIGN KEY (recipient_id) REFERENCES users (id)
            )
        ''')
        
        # Таблица приватных чатов
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS private_chats (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user1_id INTEGER,
                user2_id INTEGER,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                last_message_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY (user1_id) REFERENCES users (id),
                FOREIGN KEY (user2_id) REFERENCES users (id),
                UNIQUE(user1_id, user2_id)
            )
        ''')
        
        conn.commit()

# Утилиты для работы с пользователями
def get_user_by_username(username):
    with sqlite3.connect(Config.DATABASE) as conn:
        cursor = conn.cursor()
        cursor.execute('SELECT * FROM users WHERE username = ?', (username,))
        return cursor.fetchone()

def get_user_by_id(user_id):
    with sqlite3.connect(Config.DATABASE) as conn:
        cursor = conn.cursor()
        cursor.execute('SELECT * FROM users WHERE id = ?', (user_id,))
        return cursor.fetchone()

def create_user(username, password):
    with sqlite3.connect(Config.DATABASE) as conn:
        cursor = conn.cursor()
        password_hash = generate_password_hash(password)
        avatar_color = generate_avatar_color()
        try:
            cursor.execute(
                'INSERT INTO users (username, password_hash, avatar_color) VALUES (?, ?, ?)',
                (username, password_hash, avatar_color)
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

def generate_avatar_color():
    colors = ['#6366F1', '#8B5CF6', '#10B981', '#F59E0B', '#EF4444', '#3B82F6']
    import random
    return random.choice(colors)

def save_message(username, message, room='public', message_type='text', recipient_id=None):
    with sqlite3.connect(Config.DATABASE) as conn:
        cursor = conn.cursor()
        user = get_user_by_username(username)
        if user:
            cursor.execute(
                'INSERT INTO messages (user_id, username, message, message_type, room, recipient_id) VALUES (?, ?, ?, ?, ?, ?)',
                (user[0], username, message, message_type, room, recipient_id)
            )
            
            # Обновляем время последнего сообщения для приватных чатов
            if recipient_id and room.startswith('private_'):
                update_private_chat_timestamp(user[0], recipient_id)
            
            conn.commit()
            return cursor.lastrowid
    return None

def get_recent_messages(room='public', limit=50):
    with sqlite3.connect(Config.DATABASE) as conn:
        cursor = conn.cursor()
        cursor.execute('''
            SELECT username, message, message_type, timestamp, room
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
            'timestamp': msg[3],
            'room': msg[4]
        } for msg in reversed(messages)]

def get_private_messages(user1_id, user2_id, limit=50):
    with sqlite3.connect(Config.DATABASE) as conn:
        cursor = conn.cursor()
        room1 = f'private_{user1_id}_{user2_id}'
        room2 = f'private_{user2_id}_{user1_id}'
        
        cursor.execute('''
            SELECT username, message, message_type, timestamp, room
            FROM messages 
            WHERE (room = ? OR room = ?) AND recipient_id IN (?, ?)
            ORDER BY timestamp DESC 
            LIMIT ?
        ''', (room1, room2, user1_id, user2_id, limit))
        
        messages = cursor.fetchall()
        return [{
            'user': msg[0],
            'message': msg[1],
            'type': msg[2],
            'timestamp': msg[3],
            'room': msg[4]
        } for msg in reversed(messages)]

def get_online_users():
    with sqlite3.connect(Config.DATABASE) as conn:
        cursor = conn.cursor()
        cursor.execute('SELECT id, username, avatar_color FROM users WHERE is_online = TRUE')
        return [{'id': user[0], 'username': user[1], 'avatar_color': user[2]} for user in cursor.fetchall()]

def get_all_users():
    with sqlite3.connect(Config.DATABASE) as conn:
        cursor = conn.cursor()
        cursor.execute('SELECT id, username, is_online, avatar_color FROM users ORDER BY username')
        return [{'id': user[0], 'username': user[1], 'is_online': user[2], 'avatar_color': user[3]} for user in cursor.fetchall()]

def create_or_get_private_chat(user1_id, user2_id):
    with sqlite3.connect(Config.DATABASE) as conn:
        cursor = conn.cursor()
        
        # Проверяем существующий чат
        cursor.execute('''
            SELECT * FROM private_chats 
            WHERE (user1_id = ? AND user2_id = ?) OR (user1_id = ? AND user2_id = ?)
        ''', (user1_id, user2_id, user2_id, user1_id))
        
        chat = cursor.fetchone()
        if not chat:
            cursor.execute('''
                INSERT INTO private_chats (user1_id, user2_id) VALUES (?, ?)
            ''', (min(user1_id, user2_id), max(user1_id, user2_id)))
            conn.commit()
            return cursor.lastrowid
        return chat[0]

def get_user_private_chats(user_id):
    with sqlite3.connect(Config.DATABASE) as conn:
        cursor = conn.cursor()
        cursor.execute('''
            SELECT pc.*, 
                   CASE 
                       WHEN pc.user1_id = ? THEN u2.username 
                       ELSE u1.username 
                   END as partner_username,
                   CASE 
                       WHEN pc.user1_id = ? THEN u2.avatar_color 
                       ELSE u1.avatar_color 
                   END as partner_avatar_color,
                   CASE 
                       WHEN pc.user1_id = ? THEN u2.is_online 
                       ELSE u1.is_online 
                   END as partner_online,
                   (SELECT message FROM messages 
                    WHERE (room = ? OR room = ?) 
                    ORDER BY timestamp DESC LIMIT 1) as last_message
            FROM private_chats pc
            LEFT JOIN users u1 ON pc.user1_id = u1.id
            LEFT JOIN users u2 ON pc.user2_id = u2.id
            WHERE pc.user1_id = ? OR pc.user2_id = ?
            ORDER BY pc.last_message_at DESC
        ''', (user_id, user_id, user_id, 
              f'private_{user_id}_{user_id}', f'private_{user_id}_{user_id}',
              user_id, user_id))
        
        return cursor.fetchall()

def update_private_chat_timestamp(user1_id, user2_id):
    with sqlite3.connect(Config.DATABASE) as conn:
        cursor = conn.cursor()
        cursor.execute('''
            UPDATE private_chats 
            SET last_message_at = CURRENT_TIMESTAMP 
            WHERE (user1_id = ? AND user2_id = ?) OR (user1_id = ? AND user2_id = ?)
        ''', (user1_id, user2_id, user2_id, user1_id))
        conn.commit()

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
    
    current_user_id = session['user_id']
    messages = get_recent_messages()
    online_users = get_online_users()
    all_users = get_all_users()
    private_chats = get_user_private_chats(current_user_id)
    
    # Форматируем приватные чаты
    formatted_chats = []
    for chat in private_chats:
        partner_id = chat[1] if chat[1] != current_user_id else chat[2]
        partner_username = chat[4]  # partner_username из запроса
        formatted_chats.append({
            'id': chat[0],
            'partner_id': partner_id,
            'partner_username': partner_username,
            'partner_avatar_color': chat[5],
            'partner_online': chat[6],
            'last_message': chat[7] or 'Нет сообщений'
        })
    
    return render_template('chat.html', 
                         username=session['username'],
                         user_id=current_user_id,
                         messages=messages,
                         online_users=online_users,
                         all_users=all_users,
                         private_chats=formatted_chats)

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
    user_id = session['user_id']
    
    if room.startswith('private_'):
        # Получаем ID второго пользователя из названия комнаты
        parts = room.split('_')
        if len(parts) == 3:
            other_user_id = int(parts[2]) if parts[1] == str(user_id) else int(parts[1])
            messages = get_private_messages(user_id, other_user_id)
        else:
            messages = []
    else:
        messages = get_recent_messages(room)
    
    return jsonify(messages)

@app.route('/api/users/online')
def get_online_users_api():
    if 'username' not in session:
        return jsonify({'error': 'Не авторизован'}), 401
    
    online_users = get_online_users()
    return jsonify(online_users)

@app.route('/api/private-chat/<int:partner_id>')
def start_private_chat(partner_id):
    if 'username' not in session:
        return jsonify({'error': 'Не авторизован'}), 401
    
    user_id = session['user_id']
    
    # Создаем или получаем приватный чат
    chat_id = create_or_get_private_chat(user_id, partner_id)
    
    # Получаем сообщения
    messages = get_private_messages(user_id, partner_id)
    
    partner = get_user_by_id(partner_id)
    if partner:
        return jsonify({
            'success': True,
            'chat_id': chat_id,
            'partner_username': partner[1],
            'partner_avatar_color': partner[5],
            'messages': messages
        })
    
    return jsonify({'success': False, 'error': 'Пользователь не найден'})

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

@socketio.on('join_room')
def handle_join_room(data):
    if 'username' not in session:
        return
    
    room = data.get('room')
    if room:
        join_room(room)
        emit('room_joined', {'room': room})

@socketio.on('leave_room')
def handle_leave_room(data):
    if 'username' not in session:
        return
    
    room = data.get('room')
    if room:
        leave_room(room)
        emit('room_left', {'room': room})

@socketio.on('send_message')
def handle_send_message(data):
    if 'username' not in session:
        return
    
    message = data.get('message', '').strip()
    room = data.get('room', 'public')
    recipient_id = data.get('recipient_id')
    
    if not message:
        return
    
    user_id = session['user_id']
    
    # Сохраняем сообщение в БД
    message_id = save_message(session['username'], message, room, 'text', recipient_id)
    
    # Отправляем сообщение в комнату
    emit('new_message', {
        'id': message_id,
        'user': session['username'],
        'user_id': user_id,
        'message': message,
        'timestamp': datetime.now().isoformat(),
        'type': 'text',
        'room': room
    }, room=room)
    
    # Если это приватное сообщение, отправляем уведомление
    if room.startswith('private_') and recipient_id:
        emit('private_message_notification', {
            'from_user': session['username'],
            'from_user_id': user_id,
            'message': message,
            'room': room
        }, room=f'user_{recipient_id}')

@socketio.on('typing')
def handle_typing(data):
    if 'username' in session:
        room = data.get('room', 'public')
        emit('user_typing', {
            'username': session['username'],
            'is_typing': data.get('is_typing', False),
            'room': room
        }, room=room, include_self=False)

@socketio.on('start_private_chat')
def handle_start_private_chat(data):
    if 'username' not in session:
        return
    
    partner_id = data.get('partner_id')
    user_id = session['user_id']
    
    if partner_id and partner_id != user_id:
        # Создаем комнату для приватного чата
        room = f'private_{user_id}_{partner_id}'
        join_room(room)
        
        # Уведомляем партнера
        partner_room = f'user_{partner_id}'
        emit('private_chat_invitation', {
            'from_user': session['username'],
            'from_user_id': user_id,
            'room': room
        }, room=partner_room)

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
            padding: 20px;
        }
        
        .container {
            display: flex;
            width: 100%;
            max-width: 1000px;
            height: auto;
            min-height: 500px;
            background: #252642;
            border-radius: 20px;
            overflow: hidden;
            box-shadow: 0 20px 40px rgba(0,0,0,0.3);
        }
        
        .left-panel {
            flex: 1;
            background: linear-gradient(135deg, #6366F1 0%, #8B5CF6 100%);
            padding: 40px 30px;
            display: flex;
            flex-direction: column;
            justify-content: center;
        }
        
        .right-panel {
            flex: 1;
            background: #1A1B2E;
            padding: 40px 30px;
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
            margin-bottom: 30px;
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
            margin-bottom: 30px;
            text-align: center;
        }
        
        .form-group {
            margin-bottom: 15px;
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
        
        /* Мобильная адаптация */
        @media (max-width: 768px) {
            .container {
                flex-direction: column;
                height: auto;
            }
            
            .left-panel {
                padding: 30px 20px;
            }
            
            .right-panel {
                padding: 30px 20px;
            }
            
            .logo {
                font-size: 36px;
            }
            
            .form-title {
                font-size: 28px;
            }
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
            <div class="feature">👥 Групповые и личные чаты</div>
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
            transition: transform 0.3s ease;
        }
        
        .sidebar-header {
            background: linear-gradient(135deg, #6366F1 0%, #8B5CF6 100%);
            padding: 20px;
            text-align: center;
        }
        
        .logo {
            font-size: 24px;
            font-weight: bold;
            margin-bottom: 5px;
        }
        
        .subtitle {
            font-size: 14px;
            opacity: 0.9;
        }
        
        .user-info {
            background: #252642;
            padding: 15px;
            display: flex;
            align-items: center;
            gap: 12px;
        }
        
        .user-avatar {
            width: 40px;
            height: 40px;
            background: linear-gradient(135deg, #6366F1 0%, #8B5CF6 100%);
            border-radius: 50%;
            display: flex;
            align-items: center;
            justify-content: center;
            font-weight: bold;
            font-size: 16px;
        }
        
        .user-details {
            flex: 1;
        }
        
        .username {
            font-weight: bold;
            font-size: 14px;
        }
        
        .status {
            font-size: 12px;
            color: #10B981;
        }
        
        .nav {
            flex: 1;
            padding: 15px 0;
            overflow-y: auto;
        }
        
        .nav-section {
            margin-bottom: 20px;
        }
        
        .nav-title {
            padding: 10px 20px;
            font-size: 14px;
            color: #A0A0B8;
            text-transform: uppercase;
            font-weight: bold;
        }
        
        .nav-item {
            padding: 12px 20px;
            display: flex;
            align-items: center;
            gap: 12px;
            cursor: pointer;
            transition: all 0.3s ease;
            border-left: 3px solid transparent;
        }
        
        .nav-item:hover {
            background: #252642;
        }
        
        .nav-item.active {
            background: #252642;
            border-left-color: #6366F1;
        }
        
        .private-chat-item {
            display: flex;
            align-items: center;
            gap: 10px;
            padding: 8px 15px;
            cursor: pointer;
            transition: background 0.3s ease;
        }
        
        .private-chat-item:hover {
            background: #252642;
        }
        
        .private-chat-item.active {
            background: #252642;
        }
        
        .private-chat-avatar {
            width: 32px;
            height: 32px;
            border-radius: 50%;
            display: flex;
            align-items: center;
            justify-content: center;
            font-size: 12px;
            font-weight: bold;
            color: white;
        }
        
        .chat-area {
            flex: 1;
            display: flex;
            flex-direction: column;
        }
        
        .chat-header {
            background: #1A1B2E;
            padding: 15px 20px;
            border-bottom: 1px solid #373755;
            display: flex;
            align-items: center;
            justify-content: space-between;
        }
        
        .chat-title {
            font-size: 18px;
            font-weight: bold;
        }
        
        .mobile-menu-btn {
            display: none;
            background: none;
            border: none;
            color: white;
            font-size: 20px;
            cursor: pointer;
        }
        
        .messages {
            flex: 1;
            padding: 15px 20px;
            overflow-y: auto;
            display: flex;
            flex-direction: column;
            gap: 12px;
        }
        
        .message {
            max-width: 70%;
            padding: 12px 16px;
            border-radius: 18px;
            position: relative;
            animation: fadeIn 0.3s ease;
        }
        
        @keyframes fadeIn {
            from { opacity: 0; transform: translateY(10px); }
            to { opacity: 1; transform: translateY(0); }
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
            font-size: 11px;
            opacity: 0.7;
        }
        
        .message-text {
            font-size: 14px;
            line-height: 1.4;
            word-wrap: break-word;
        }
        
        .input-area {
            background: #1A1B2E;
            padding: 15px 20px;
            border-top: 1px solid #373755;
        }
        
        .input-container {
            display: flex;
            gap: 12px;
            align-items: flex-end;
        }
        
        .message-input {
            flex: 1;
            background: #252642;
            border: 2px solid #373755;
            border-radius: 25px;
            padding: 12px 18px;
            color: white;
            font-size: 14px;
            resize: none;
            max-height: 120px;
            min-height: 45px;
            font-family: inherit;
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
            width: 45px;
            height: 45px;
            display: flex;
            align-items: center;
            justify-content: center;
            cursor: pointer;
            transition: all 0.3s ease;
            flex-shrink: 0;
        }
        
        .send-btn:hover {
            background: #4F46E5;
        }
        
        .send-btn:disabled {
            background: #373755;
            cursor: not-allowed;
        }
        
        .typing-indicator {
            padding: 8px 20px;
            font-size: 13px;
            color: #A0A0B8;
            font-style: italic;
        }
        
        .logout-btn {
            background: #EF4444;
            color: white;
            border: none;
            padding: 8px 16px;
            border-radius: 8px;
            cursor: pointer;
            margin: 10px 20px;
            transition: all 0.3s ease;
            font-size: 14px;
        }
        
        .logout-btn:hover {
            background: #DC2626;
        }
        
        .welcome-message {
            text-align: center;
            padding: 30px 20px;
            color: #A0A0B8;
        }
        
        .welcome-title {
            font-size: 20px;
            margin-bottom: 8px;
            color: white;
        }
        
        .online-indicator {
            width: 8px;
            height: 8px;
            background: #10B981;
            border-radius: 50%;
            flex-shrink: 0;
        }
        
        .offline-indicator {
            width: 8px;
            height: 8px;
            background: #6B7280;
            border-radius: 50%;
            flex-shrink: 0;
        }
        
        .user-list {
            display: flex;
            flex-direction: column;
            gap: 8px;
            max-height: 200px;
            overflow-y: auto;
        }
        
        .user-item {
            display: flex;
            align-items: center;
            gap: 10px;
            padding: 8px 15px;
            cursor: pointer;
            transition: background 0.3s ease;
            font-size: 14px;
        }
        
        .user-item:hover {
            background: #252642;
        }
        
        .user-avatar-small {
            width: 28px;
            height: 28px;
            border-radius: 50%;
            display: flex;
            align-items: center;
            justify-content: center;
            font-size: 12px;
            font-weight: bold;
            color: white;
        }
        
        /* Мобильная адаптация */
        @media (max-width: 768px) {
            .container {
                flex-direction: column;
            }
            
            .sidebar {
                position: fixed;
                top: 0;
                left: 0;
                height: 100vh;
                z-index: 1000;
                transform: translateX(-100%);
            }
            
            .sidebar.active {
                transform: translateX(0);
            }
            
            .chat-area {
                width: 100%;
            }
            
            .mobile-menu-btn {
                display: block;
            }
            
            .message {
                max-width: 85%;
            }
            
            .user-info {
                padding: 12px;
            }
            
            .nav {
                padding: 10px 0;
            }
        }
        
        /* Скроллбар */
        ::-webkit-scrollbar {
            width: 6px;
        }
        
        ::-webkit-scrollbar-track {
            background: #1A1B2E;
        }
        
        ::-webkit-scrollbar-thumb {
            background: #373755;
            border-radius: 3px;
        }
        
        ::-webkit-scrollbar-thumb:hover {
            background: #6366F1;
        }
    </style>
</head>
<body>
    <div class="container">
        <!-- Боковая панель -->
        <div class="sidebar" id="sidebar">
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
                <div class="nav-section">
                    <div class="nav-title">Чаты</div>
                    <div class="nav-item active" onclick="switchRoom('public')">
                        <span>🌐</span>
                        <span>Общий чат</span>
                    </div>
                </div>
                
                <div class="nav-section">
                    <div class="nav-title">Приватные чаты</div>
                    <div id="private-chats-list">
                        {% for chat in private_chats %}
                        <div class="private-chat-item" onclick="openPrivateChat({{ chat.partner_id }}, '{{ chat.partner_username }}', '{{ chat.partner_avatar_color }}')">
                            <div class="private-chat-avatar" style="background: {{ chat.partner_avatar_color }};">
                                {{ chat.partner_username[:2].upper() }}
                            </div>
                            <div style="flex: 1;">
                                <div style="font-size: 14px; font-weight: bold;">{{ chat.partner_username }}</div>
                                <div style="font-size: 12px; color: #A0A0B8; overflow: hidden; text-overflow: ellipsis; white-space: nowrap;">
                                    {{ chat.last_message }}
                                </div>
                            </div>
                            <div class="{% if chat.partner_online %}online-indicator{% else %}offline-indicator{% endif %}"></div>
                        </div>
                        {% endfor %}
                    </div>
                </div>
                
                <div class="nav-section">
                    <div class="nav-title">Все пользователи</div>
                    <div class="user-list" id="all-users-list">
                        {% for user in all_users %}
                        {% if user.id != user_id %}
                        <div class="user-item" onclick="startPrivateChat({{ user.id }}, '{{ user.username }}', '{{ user.avatar_color }}')">
                            <div class="user-avatar-small" style="background: {{ user.avatar_color }};">
                                {{ user.username[:2].upper() }}
                            </div>
                            <span>{{ user.username }}</span>
                            <div class="{% if user.is_online %}online-indicator{% else %}offline-indicator{% endif %}"></div>
                        </div>
                        {% endif %}
                        {% endfor %}
                    </div>
                </div>
            </div>
            
            <button class="logout-btn" onclick="logout()">Выйти</button>
        </div>
        
        <!-- Область чата -->
        <div class="chat-area">
            <div class="chat-header">
                <button class="mobile-menu-btn" onclick="toggleSidebar()">☰</button>
                <div class="chat-title" id="chat-title">🌐 Общий чат</div>
                <div></div> <!-- Для выравнивания -->
            </div>
            
            <div class="messages" id="messages">
                <div class="welcome-message">
                    <div class="welcome-title">Добро пожаловать в Tandau Messenger!</div>
                    <div>Начните общение, отправив первое сообщение</div>
                </div>
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
                    <button class="send-btn" onclick="sendMessage()" id="send-btn">
                        <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
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
        const userId = {{ user_id }};
        let currentRoom = 'public';
        let currentChatType = 'public';
        let currentPartner = null;
        let typingTimer;
        
        // Подключение к WebSocket
        socket.on('connect', function() {
            console.log('Connected to server');
            joinRoom('public');
        });
        
        // Новое сообщение
        socket.on('new_message', function(data) {
            if (data.room === currentRoom) {
                addMessage(data);
            }
        });
        
        // Пользователь печатает
        socket.on('user_typing', function(data) {
            if (data.room === currentRoom) {
                const indicator = document.getElementById('typing-indicator');
                if (data.is_typing) {
                    indicator.textContent = `${data.username} печатает...`;
                    indicator.style.display = 'block';
                } else {
                    indicator.style.display = 'none';
                }
            }
        });
        
        // Приватное сообщение
        socket.on('private_message_notification', function(data) {
            if (data.room === currentRoom) {
                addMessage({
                    user: data.from_user,
                    user_id: data.from_user_id,
                    message: data.message,
                    timestamp: new Date().toISOString(),
                    type: 'text',
                    room: data.room
                });
            } else {
                // Показать уведомление о новом сообщении
                showNotification(`Новое сообщение от ${data.from_user}`);
            }
        });
        
        // Приглашение в приватный чат
        socket.on('private_chat_invitation', function(data) {
            if (confirm(`${data.from_user} приглашает вас в приватный чат. Присоединиться?`)) {
                openPrivateChat(data.from_user_id, data.from_user, '#6366F1');
            }
        });
        
        function switchRoom(room, chatTitle = '🌐 Общий чат') {
            // Покидаем предыдущую комнату
            if (currentRoom !== 'public') {
                socket.emit('leave_room', { room: currentRoom });
            }
            
            // Присоединяемся к новой комнате
            currentRoom = room;
            currentChatType = room === 'public' ? 'public' : 'private';
            document.getElementById('chat-title').textContent = chatTitle;
            
            socket.emit('join_room', { room: room });
            
            // Загружаем сообщения
            loadMessages();
            
            // Сбрасываем индикатор печатания
            document.getElementById('typing-indicator').style.display = 'none';
            
            // Обновляем активный элемент в навигации
            updateActiveNavItem(room);
            
            // На мобильных устройствах закрываем сайдбар
            if (window.innerWidth <= 768) {
                toggleSidebar();
            }
        }
        
        function openPrivateChat(partnerId, partnerUsername, partnerAvatarColor) {
            const room = `private_${userId}_${partnerId}`;
            const chatTitle = `👤 ${partnerUsername}`;
            currentPartner = {
                id: partnerId,
                username: partnerUsername,
                avatarColor: partnerAvatarColor
            };
            switchRoom(room, chatTitle);
        }
        
        function startPrivateChat(partnerId, partnerUsername, partnerAvatarColor) {
            // Создаем приватный чат через API
            fetch(`/api/private-chat/${partnerId}`)
                .then(response => response.json())
                .then(data => {
                    if (data.success) {
                        openPrivateChat(partnerId, partnerUsername, partnerAvatarColor);
                        // Уведомляем партнера
                        socket.emit('start_private_chat', { partner_id: partnerId });
                    }
                })
                .catch(error => {
                    console.error('Error starting private chat:', error);
                });
        }
        
        function loadMessages() {
            const messagesContainer = document.getElementById('messages');
            messagesContainer.innerHTML = '<div class="welcome-message"><div>Загрузка сообщений...</div></div>';
            
            fetch(`/api/messages?room=${currentRoom}`)
                .then(response => response.json())
                .then(messages => {
                    messagesContainer.innerHTML = '';
                    if (messages.length === 0) {
                        messagesContainer.innerHTML = `
                            <div class="welcome-message">
                                <div class="welcome-title">Начните общение!</div>
                                <div>Это начало ${currentChatType === 'public' ? 'общего' : 'приватного'} чата</div>
                            </div>
                        `;
                    } else {
                        messages.forEach(addMessage);
                    }
                    scrollToBottom();
                })
                .catch(error => {
                    console.error('Error loading messages:', error);
                    messagesContainer.innerHTML = '<div class="welcome-message"><div>Ошибка загрузки сообщений</div></div>';
                });
        }
        
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
        
        function sendMessage() {
            const input = document.getElementById('message-input');
            const message = input.value.trim();
            
            if (message) {
                const messageData = {
                    message: message,
                    room: currentRoom
                };
                
                if (currentChatType === 'private' && currentPartner) {
                    messageData.recipient_id = currentPartner.id;
                }
                
                socket.emit('send_message', messageData);
                input.value = '';
                adjustTextareaHeight();
                
                // Уведомляем, что перестали печатать
                socket.emit('typing', { 
                    is_typing: false,
                    room: currentRoom
                });
            }
        }
        
        function handleTyping() {
            clearTimeout(typingTimer);
            socket.emit('typing', { 
                is_typing: true,
                room: currentRoom
            });
            
            typingTimer = setTimeout(() => {
                socket.emit('typing', { 
                    is_typing: false,
                    room: currentRoom
                });
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
        
        function toggleSidebar() {
            const sidebar = document.getElementById('sidebar');
            sidebar.classList.toggle('active');
        }
        
        function updateActiveNavItem(room) {
            // Сбрасываем все активные элементы
            document.querySelectorAll('.nav-item.active, .private-chat-item.active').forEach(item => {
                item.classList.remove('active');
            });
            
            if (room === 'public') {
                document.querySelector('.nav-item').classList.add('active');
            } else {
                // Находим соответствующий приватный чат и делаем его активным
                const partnerId = room.split('_')[2];
                document.querySelectorAll('.private-chat-item').forEach(item => {
                    if (item.onclick && item.onclick.toString().includes(partnerId)) {
                        item.classList.add('active');
                    }
                });
            }
        }
        
        function showNotification(message) {
            // Простое уведомление (в реальном приложении можно использовать Toast)
            if ('Notification' in window && Notification.permission === 'granted') {
                new Notification('Tandau Messenger', {
                    body: message,
                    icon: '/favicon.ico'
                });
            } else {
                console.log('New message:', message);
            }
        }
        
        // Запрос разрешения на уведомления
        if ('Notification' in window) {
            Notification.requestPermission();
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
        
        // Адаптация к мобильным устройствам
        function checkMobile() {
            if (window.innerWidth <= 768) {
                document.getElementById('sidebar').classList.remove('active');
            } else {
                document.getElementById('sidebar').classList.add('active');
            }
        }
        
        window.addEventListener('resize', checkMobile);
        window.addEventListener('load', checkMobile);
        
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
    print("🚀 Tandau Web Messenger запущен!")
    print("📍 Доступен по адресу: http://localhost:5000")
    print("📱 Адаптирован для мобильных устройств")
    print("💬 Поддерживает общие и приватные чаты")
    socketio.run(app, host='0.0.0.0', port=5000, debug=False)
