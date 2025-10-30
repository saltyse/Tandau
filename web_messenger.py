# web_messenger.py - Tandau Messenger с рабочими личными чатами
from flask import Flask, render_template, request, jsonify, session, redirect, url_for
from flask_socketio import SocketIO, emit, join_room, leave_room
import sqlite3
from datetime import datetime
from werkzeug.security import generate_password_hash, check_password_hash
import random
import os

app = Flask(__name__)
app.config['SECRET_KEY'] = os.environ.get('SECRET_KEY', 'tandau-secret-key-2024')
socketio = SocketIO(app, cors_allowed_origins="*")

# Инициализация базы данных
def init_db():
    with sqlite3.connect('messenger.db') as conn:
        cursor = conn.cursor()
        
        # Таблица пользователей
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS users (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                username TEXT UNIQUE NOT NULL,
                password_hash TEXT NOT NULL,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                is_online BOOLEAN DEFAULT FALSE,
                avatar_color TEXT DEFAULT '#6366F1'
            )
        ''')
        
        # Таблица сообщений
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS messages (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                username TEXT NOT NULL,
                message TEXT NOT NULL,
                timestamp TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                room TEXT DEFAULT 'public',
                recipient TEXT
            )
        ''')
        
        conn.commit()

# Утилиты для работы с пользователями
def get_user_by_username(username):
    with sqlite3.connect('messenger.db') as conn:
        cursor = conn.cursor()
        cursor.execute('SELECT * FROM users WHERE username = ?', (username,))
        return cursor.fetchone()

def get_all_users():
    with sqlite3.connect('messenger.db') as conn:
        cursor = conn.cursor()
        cursor.execute('SELECT username, is_online, avatar_color FROM users WHERE username != ? ORDER BY username', (session.get('username', ''),))
        return [{'username': user[0], 'is_online': user[1], 'avatar_color': user[2]} for user in cursor.fetchall()]

def create_user(username, password):
    with sqlite3.connect('messenger.db') as conn:
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
    with sqlite3.connect('messenger.db') as conn:
        cursor = conn.cursor()
        cursor.execute(
            'UPDATE users SET is_online = ? WHERE username = ?',
            (is_online, username)
        )
        conn.commit()

def generate_avatar_color():
    colors = ['#6366F1', '#8B5CF6', '#10B981', '#F59E0B', '#EF4444', '#3B82F6']
    return random.choice(colors)

def save_message(username, message, room='public', recipient=None):
    with sqlite3.connect('messenger.db') as conn:
        cursor = conn.cursor()
        cursor.execute(
            'INSERT INTO messages (username, message, room, recipient) VALUES (?, ?, ?, ?)',
            (username, message, room, recipient)
        )
        conn.commit()
        return cursor.lastrowid

def get_recent_messages(room='public', limit=50):
    with sqlite3.connect('messenger.db') as conn:
        cursor = conn.cursor()
        cursor.execute('''
            SELECT username, message, timestamp
            FROM messages 
            WHERE room = ? 
            ORDER BY timestamp ASC 
            LIMIT ?
        ''', (room, limit))
        messages = cursor.fetchall()
        return [{
            'user': msg[0],
            'message': msg[1],
            'timestamp': msg[2]
        } for msg in messages]

def get_private_messages(user1, user2, limit=50):
    with sqlite3.connect('messenger.db') as conn:
        cursor = conn.cursor()
        room = f'private_{min(user1, user2)}_{max(user1, user2)}'
        
        cursor.execute('''
            SELECT username, message, timestamp
            FROM messages 
            WHERE room = ? 
            ORDER BY timestamp ASC 
            LIMIT ?
        ''', (room, limit))
        
        messages = cursor.fetchall()
        return [{
            'user': msg[0],
            'message': msg[1],
            'timestamp': msg[2]
        } for msg in messages]

def get_private_chats(username):
    with sqlite3.connect('messenger.db') as conn:
        cursor = conn.cursor()
        cursor.execute('''
            SELECT DISTINCT 
                CASE 
                    WHEN username = ? THEN recipient
                    ELSE username
                END as partner
            FROM messages 
            WHERE (username = ? OR recipient = ?) AND room LIKE 'private_%'
        ''', (username, username, username))
        
        chats = cursor.fetchall()
        result = []
        for chat in chats:
            partner = chat[0]
            if partner:  # Проверяем, что partner не None
                partner_info = get_user_by_username(partner)
                if partner_info:
                    # Получаем последнее сообщение
                    cursor.execute('''
                        SELECT message FROM messages 
                        WHERE room = ? 
                        ORDER BY timestamp DESC LIMIT 1
                    ''', (f'private_{min(username, partner)}_{max(username, partner)}',))
                    last_message = cursor.fetchone()
                    
                    result.append({
                        'partner': partner,
                        'avatar_color': partner_info[5],
                        'is_online': partner_info[4],
                        'last_message': last_message[0] if last_message else 'Нет сообщений'
                    })
        return result

def get_online_users():
    with sqlite3.connect('messenger.db') as conn:
        cursor = conn.cursor()
        cursor.execute('SELECT username, avatar_color FROM users WHERE is_online = TRUE AND username != ?', (session.get('username', ''),))
        return [{'username': user[0], 'avatar_color': user[1]} for user in cursor.fetchall()]

# Маршруты Flask
@app.route('/')
def index():
    if 'username' in session:
        return redirect(url_for('chat'))
    return '''
<!DOCTYPE html>
<html>
<head>
    <title>Tandau Messenger - Вход</title>
    <style>
        body {
            font-family: Arial, sans-serif;
            background: linear-gradient(135deg, #667eea 0%, #764ba2 100%);
            margin: 0;
            padding: 20px;
            display: flex;
            justify-content: center;
            align-items: center;
            min-height: 100vh;
        }
        .container {
            background: white;
            padding: 40px;
            border-radius: 10px;
            box-shadow: 0 10px 30px rgba(0,0,0,0.2);
            width: 100%;
            max-width: 400px;
        }
        h1 {
            text-align: center;
            color: #333;
            margin-bottom: 30px;
        }
        .form-group {
            margin-bottom: 20px;
        }
        input {
            width: 100%;
            padding: 12px;
            border: 1px solid #ddd;
            border-radius: 5px;
            font-size: 16px;
            box-sizing: border-box;
        }
        button {
            width: 100%;
            padding: 12px;
            background: #667eea;
            color: white;
            border: none;
            border-radius: 5px;
            font-size: 16px;
            cursor: pointer;
        }
        button:hover {
            background: #5a6fd8;
        }
        .switch-form {
            text-align: center;
            margin-top: 20px;
        }
        .alert {
            padding: 10px;
            background: #f8d7da;
            color: #721c24;
            border-radius: 5px;
            margin-bottom: 20px;
            display: none;
        }
    </style>
</head>
<body>
    <div class="container">
        <h1>Tandau Messenger</h1>
        <div id="alert" class="alert"></div>
        
        <div id="login-form">
            <div class="form-group">
                <input type="text" id="login-username" placeholder="Имя пользователя">
            </div>
            <div class="form-group">
                <input type="password" id="login-password" placeholder="Пароль">
            </div>
            <button onclick="login()">Войти</button>
            <div class="switch-form">
                <a href="#" onclick="showRegister()">Нет аккаунта? Зарегистрироваться</a>
            </div>
        </div>
        
        <div id="register-form" style="display: none;">
            <div class="form-group">
                <input type="text" id="reg-username" placeholder="Имя пользователя">
            </div>
            <div class="form-group">
                <input type="password" id="reg-password" placeholder="Пароль">
            </div>
            <div class="form-group">
                <input type="password" id="reg-confirm" placeholder="Повторите пароль">
            </div>
            <button onclick="register()">Зарегистрироваться</button>
            <div class="switch-form">
                <a href="#" onclick="showLogin()">Уже есть аккаунт? Войти</a>
            </div>
        </div>
    </div>

    <script>
        function showAlert(message) {
            const alert = document.getElementById('alert');
            alert.textContent = message;
            alert.style.display = 'block';
        }
        
        function showRegister() {
            document.getElementById('login-form').style.display = 'none';
            document.getElementById('register-form').style.display = 'block';
            document.getElementById('alert').style.display = 'none';
        }
        
        function showLogin() {
            document.getElementById('register-form').style.display = 'none';
            document.getElementById('login-form').style.display = 'block';
            document.getElementById('alert').style.display = 'none';
        }
        
        async function login() {
            const username = document.getElementById('login-username').value;
            const password = document.getElementById('login-password').value;
            
            if (!username || !password) {
                showAlert('Заполните все поля');
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
                    showAlert(data.error);
                }
            } catch (error) {
                showAlert('Ошибка подключения');
            }
        }
        
        async function register() {
            const username = document.getElementById('reg-username').value;
            const password = document.getElementById('reg-password').value;
            const confirm = document.getElementById('reg-confirm').value;
            
            if (!username || !password || !confirm) {
                showAlert('Заполните все поля');
                return;
            }
            
            if (password !== confirm) {
                showAlert('Пароли не совпадают');
                return;
            }
            
            if (username.length < 3) {
                showAlert('Имя пользователя должно быть не менее 3 символов');
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
                    showAlert('Регистрация успешна! Теперь вы можете войти.');
                    setTimeout(() => showLogin(), 2000);
                } else {
                    showAlert(data.error);
                }
            } catch (error) {
                showAlert('Ошибка подключения');
            }
        }
        
        // Enter для отправки форм
        document.addEventListener('keypress', function(event) {
            if (event.key === 'Enter') {
                if (document.getElementById('login-form').style.display !== 'none') {
                    login();
                } else {
                    register();
                }
            }
        });
    </script>
</body>
</html>
    '''

@app.route('/login', methods=['POST'])
def login():
    username = request.form.get('username')
    password = request.form.get('password')
    
    user = verify_user(username, password)
    if user:
        session['username'] = username
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
    all_users = get_all_users()
    private_chats = get_private_chats(session['username'])
    
    return f'''
<!DOCTYPE html>
<html>
<head>
    <title>Tandau Messenger - Чат</title>
    <style>
        body {{
            margin: 0;
            padding: 0;
            font-family: Arial, sans-serif;
            background: #f0f2f5;
            height: 100vh;
            overflow: hidden;
        }}
        .container {{
            display: flex;
            height: 100vh;
        }}
        .sidebar {{
            width: 300px;
            background: white;
            border-right: 1px solid #ddd;
            display: flex;
            flex-direction: column;
        }}
        .header {{
            background: #667eea;
            color: white;
            padding: 20px;
            text-align: center;
        }}
        .user-info {{
            padding: 15px;
            background: #f8f9fa;
            border-bottom: 1px solid #ddd;
            display: flex;
            align-items: center;
            gap: 10px;
        }}
        .user-avatar {{
            width: 40px;
            height: 40px;
            border-radius: 50%;
            background: #667eea;
            display: flex;
            align-items: center;
            justify-content: center;
            color: white;
            font-weight: bold;
            font-size: 16px;
        }}
        .nav {{
            flex: 1;
            overflow-y: auto;
            padding: 10px 0;
        }}
        .nav-section {{
            margin-bottom: 20px;
        }}
        .nav-title {{
            padding: 10px 15px;
            font-size: 14px;
            color: #666;
            text-transform: uppercase;
            font-weight: bold;
            border-bottom: 1px solid #eee;
        }}
        .nav-item {{
            padding: 12px 15px;
            cursor: pointer;
            border-left: 3px solid transparent;
            transition: all 0.3s ease;
        }}
        .nav-item:hover {{
            background: #f8f9fa;
        }}
        .nav-item.active {{
            background: #f8f9fa;
            border-left-color: #667eea;
        }}
        .private-chat-item {{
            padding: 10px 15px;
            cursor: pointer;
            display: flex;
            align-items: center;
            gap: 10px;
            border-bottom: 1px solid #f0f0f0;
            transition: background 0.3s ease;
        }}
        .private-chat-item:hover {{
            background: #f8f9fa;
        }}
        .private-chat-item.active {{
            background: #f8f9fa;
        }}
        .private-chat-avatar {{
            width: 32px;
            height: 32px;
            border-radius: 50%;
            display: flex;
            align-items: center;
            justify-content: center;
            color: white;
            font-weight: bold;
            font-size: 12px;
            flex-shrink: 0;
        }}
        .private-chat-info {{
            flex: 1;
            min-width: 0;
        }}
        .private-chat-name {{
            font-size: 14px;
            font-weight: bold;
            margin-bottom: 2px;
        }}
        .private-chat-last {{
            font-size: 12px;
            color: #666;
            white-space: nowrap;
            overflow: hidden;
            text-overflow: ellipsis;
        }}
        .user-item {{
            padding: 8px 15px;
            cursor: pointer;
            display: flex;
            align-items: center;
            gap: 10px;
            border-bottom: 1px solid #f0f0f0;
            transition: background 0.3s ease;
        }}
        .user-item:hover {{
            background: #f8f9fa;
        }}
        .user-avatar-small {{
            width: 28px;
            height: 28px;
            border-radius: 50%;
            display: flex;
            align-items: center;
            justify-content: center;
            color: white;
            font-weight: bold;
            font-size: 11px;
            flex-shrink: 0;
        }}
        .online-indicator {{
            width: 8px;
            height: 8px;
            background: #10B981;
            border-radius: 50%;
            margin-left: auto;
            flex-shrink: 0;
        }}
        .offline-indicator {{
            width: 8px;
            height: 8px;
            background: #6B7280;
            border-radius: 50%;
            margin-left: auto;
            flex-shrink: 0;
        }}
        .chat-area {{
            flex: 1;
            display: flex;
            flex-direction: column;
        }}
        .chat-header {{
            background: white;
            padding: 15px 20px;
            border-bottom: 1px solid #ddd;
        }}
        .chat-title {{
            font-size: 18px;
            font-weight: bold;
            color: #333;
        }}
        .messages {{
            flex: 1;
            padding: 20px;
            overflow-y: auto;
            background: white;
        }}
        .message {{
            margin-bottom: 15px;
            padding: 10px 15px;
            border-radius: 10px;
            max-width: 70%;
            word-wrap: break-word;
        }}
        .message.own {{
            background: #667eea;
            color: white;
            margin-left: auto;
        }}
        .message.other {{
            background: #f1f3f4;
            color: #333;
        }}
        .message-user {{
            font-weight: bold;
            margin-bottom: 5px;
            font-size: 14px;
        }}
        .input-area {{
            padding: 20px;
            background: white;
            border-top: 1px solid #ddd;
        }}
        .input-container {{
            display: flex;
            gap: 10px;
        }}
        .message-input {{
            flex: 1;
            padding: 12px;
            border: 1px solid #ddd;
            border-radius: 25px;
            font-size: 16px;
        }}
        .send-btn {{
            background: #667eea;
            color: white;
            border: none;
            border-radius: 50%;
            width: 50px;
            height: 50px;
            cursor: pointer;
            font-size: 18px;
        }}
        .logout-btn {{
            background: #dc3545;
            color: white;
            border: none;
            padding: 10px;
            margin: 10px;
            border-radius: 5px;
            cursor: pointer;
        }}
        .user-list {{
            max-height: 200px;
            overflow-y: auto;
        }}
        .welcome-message {{
            text-align: center;
            padding: 40px 20px;
            color: #666;
        }}
    </style>
</head>
<body>
    <div class="container">
        <div class="sidebar">
            <div class="header">
                <h2>Tandau Messenger</h2>
            </div>
            <div class="user-info">
                <div class="user-avatar">{session['username'][:2].upper()}</div>
                <div>
                    <strong>{session['username']}</strong>
                    <div style="font-size: 12px; color: #666;">🟢 В сети</div>
                </div>
            </div>
            
            <div class="nav">
                <div class="nav-section">
                    <div class="nav-title">Чаты</div>
                    <div class="nav-item active" onclick="switchRoom('public', '🌐 Общий чат')">
                        🌐 Общий чат
                    </div>
                </div>
                
                <div class="nav-section">
                    <div class="nav-title">Личные чаты</div>
                    <div id="private-chats-list">
                        {"".join(f'''
                        <div class="private-chat-item" onclick="openPrivateChat('{chat['partner']}')">
                            <div class="private-chat-avatar" style="background: {chat['avatar_color']};">
                                {chat['partner'][:2].upper()}
                            </div>
                            <div class="private-chat-info">
                                <div class="private-chat-name">{chat['partner']}</div>
                                <div class="private-chat-last">{chat['last_message'][:30]}{'...' if len(chat['last_message']) > 30 else ''}</div>
                            </div>
                            <div class="{'online-indicator' if chat['is_online'] else 'offline-indicator'}"></div>
                        </div>
                        ''' for chat in private_chats)}
                        {"<div style='padding: 10px 15px; color: #666; font-size: 14px;'>Нет личных чатов</div>" if not private_chats else ""}
                    </div>
                </div>
                
                <div class="nav-section">
                    <div class="nav-title">Все пользователи</div>
                    <div class="user-list" id="all-users-list">
                        {"".join(f'''
                        <div class="user-item" onclick="startPrivateChat('{user['username']}', '{user['avatar_color']}')">
                            <div class="user-avatar-small" style="background: {user['avatar_color']};">
                                {user['username'][:2].upper()}
                            </div>
                            <span style="flex: 1;">{user['username']}</span>
                            <div class="{'online-indicator' if user['is_online'] else 'offline-indicator'}"></div>
                        </div>
                        ''' for user in all_users)}
                    </div>
                </div>
            </div>
            
            <button class="logout-btn" onclick="logout()">Выйти</button>
        </div>
        
        <div class="chat-area">
            <div class="chat-header">
                <div class="chat-title" id="chat-title">🌐 Общий чат</div>
            </div>
            
            <div class="messages" id="messages">
                {"".join(f'''
                <div class="message {'own' if msg['user'] == session['username'] else 'other'}">
                    <div class="message-user">{msg['user']}</div>
                    <div>{msg['message']}</div>
                </div>
                ''' for msg in messages)}
                {"<div class='welcome-message'><div style='font-size: 18px; margin-bottom: 10px;'>Добро пожаловать в Tandau Messenger!</div><div>Начните общение, отправив первое сообщение</div></div>" if not messages else ""}
            </div>
            
            <div class="input-area">
                <div class="input-container">
                    <input type="text" class="message-input" id="message-input" placeholder="Введите сообщение..." autocomplete="off">
                    <button class="send-btn" onclick="sendMessage()">➤</button>
                </div>
            </div>
        </div>
    </div>

    <script src="https://cdnjs.cloudflare.com/ajax/libs/socket.io/4.0.1/socket.io.js"></script>
    <script>
        const socket = io();
        const username = "{session['username']}";
        let currentRoom = 'public';
        let currentChatType = 'public';
        let currentPartner = null;
        
        socket.on('connect', function() {{
            console.log('Connected to server');
            joinRoom('public');
        }});
        
        socket.on('new_message', function(data) {{
            console.log('New message received:', data);
            if (data.room === currentRoom) {{
                addMessage(data);
            }}
        }});
        
        socket.on('private_message', function(data) {{
            console.log('Private message received:', data);
            if (data.room === currentRoom) {{
                addMessage(data);
            }} else {{
                // Показать уведомление о новом сообщении
                showNotification(`Новое сообщение от ${{data.user}}`);
                // Обновить список чатов
                updatePrivateChats();
            }}
        }});
        
        socket.on('user_joined', function(data) {{
            updateOnlineUsers(data.online_users);
        }});
        
        socket.on('user_left', function(data) {{
            updateOnlineUsers(data.online_users);
        }});
        
        function joinRoom(room) {{
            socket.emit('join_room', {{ room: room }});
        }}
        
        function switchRoom(room, chatTitle = '🌐 Общий чат', chatType = 'public', partner = null) {{
            // Покидаем предыдущую комнату
            if (currentRoom && currentRoom !== room) {{
                socket.emit('leave_room', {{ room: currentRoom }});
            }}
            
            // Присоединяемся к новой комнате
            currentRoom = room;
            currentChatType = chatType;
            currentPartner = partner;
            document.getElementById('chat-title').textContent = chatTitle;
            
            joinRoom(room);
            
            // Загружаем сообщения
            loadMessages();
            
            // Обновляем активный элемент в навигации
            updateActiveNavItem(room, chatType);
        }}
        
        function openPrivateChat(partner) {{
            const room = `private_${{Math.min(username, partner)}}_${{Math.max(username, partner)}}`;
            const chatTitle = `👤 ${{partner}}`;
            switchRoom(room, chatTitle, 'private', partner);
        }}
        
        function startPrivateChat(partner, avatarColor) {{
            openPrivateChat(partner);
        }}
        
        function loadMessages() {{
            const messagesContainer = document.getElementById('messages');
            messagesContainer.innerHTML = '<div style="text-align: center; padding: 20px; color: #666;">Загрузка сообщений...</div>';
            
            fetch(`/api/messages?room=${{currentRoom}}`)
                .then(response => response.json())
                .then(messages => {{
                    messagesContainer.innerHTML = '';
                    if (messages.length === 0) {{
                        messagesContainer.innerHTML = `
                            <div class="welcome-message">
                                <div style="font-size: 18px; margin-bottom: 10px;">Начните общение!</div>
                                <div>Это начало ${{currentChatType === 'public' ? 'общего' : 'личного'}} чата</div>
                            </div>
                        `;
                    }} else {{
                        messages.forEach(addMessage);
                    }}
                    scrollToBottom();
                }})
                .catch(error => {{
                    console.error('Error loading messages:', error);
                    messagesContainer.innerHTML = '<div style="text-align: center; padding: 20px; color: #666;">Ошибка загрузки сообщений</div>';
                }});
        }}
        
        function addMessage(data) {{
            const messages = document.getElementById('messages');
            const messageDiv = document.createElement('div');
            messageDiv.className = `message ${{data.user === username ? 'own' : 'other'}}`;
            messageDiv.innerHTML = `
                <div class="message-user">${{data.user}}</div>
                <div>${{data.message}}</div>
            `;
            messages.appendChild(messageDiv);
            scrollToBottom();
        }}
        
        function sendMessage() {{
            const input = document.getElementById('message-input');
            const message = input.value.trim();
            
            if (message) {{
                const messageData = {{
                    message: message,
                    room: currentRoom,
                    chat_type: currentChatType
                }};
                
                if (currentChatType === 'private' && currentPartner) {{
                    messageData.recipient = currentPartner;
                }}
                
                console.log('Sending message:', messageData);
                socket.emit('send_message', messageData);
                input.value = '';
            }}
        }}
        
        function updateOnlineUsers(users) {{
            // Можно добавить логику обновления статусов пользователей
            console.log('Online users updated:', users);
        }}
        
        function updatePrivateChats() {{
            // Перезагружаем страницу для обновления списка чатов
            // В реальном приложении лучше сделать AJAX запрос
            window.location.reload();
        }}
        
        function updateActiveNavItem(room, chatType) {{
            // Сбрасываем все активные элементы
            document.querySelectorAll('.nav-item.active, .private-chat-item.active').forEach(item => {{
                item.classList.remove('active');
            }});
            
            if (chatType === 'public') {{
                document.querySelector('.nav-item').classList.add('active');
            }} else {{
                // Находим соответствующий приватный чат и делаем его активным
                const partner = room.split('_')[1] === username ? room.split('_')[2] : room.split('_')[1];
                document.querySelectorAll('.private-chat-item').forEach(item => {{
                    if (item.querySelector('.private-chat-name').textContent === partner) {{
                        item.classList.add('active');
                    }}
                }});
            }}
        }}
        
        function showNotification(message) {{
            // Простое уведомление
            if ('Notification' in window && Notification.permission === 'granted') {{
                new Notification('Tandau Messenger', {{
                    body: message,
                    icon: '/favicon.ico'
                }});
            }} else {{
                // Fallback уведомление
                alert(message);
            }}
        }}
        
        function logout() {{
            if (confirm('Вы уверены, что хотите выйти?')) {{
                window.location.href = '/logout';
            }}
        }}
        
        // Enter для отправки сообщения
        document.getElementById('message-input').addEventListener('keypress', function(e) {{
            if (e.key === 'Enter') {{
                sendMessage();
            }}
        }});
        
        // Запрос разрешения на уведомления
        if ('Notification' in window) {{
            Notification.requestPermission();
        }}
        
        // Автопрокрутка при загрузке
        window.addEventListener('load', function() {{
            scrollToBottom();
        }});
        
        function scrollToBottom() {{
            const messages = document.getElementById('messages');
            messages.scrollTop = messages.scrollHeight;
        }}
    </script>
</body>
</html>
    '''

@app.route('/logout')
def logout():
    if 'username' in session:
        update_user_online_status(session['username'], False)
        session.pop('username', None)
    return redirect(url_for('index'))

@app.route('/api/messages')
def get_messages():
    if 'username' not in session:
        return jsonify({'error': 'Не авторизован'}), 401
    
    room = request.args.get('room', 'public')
    
    if room.startswith('private_'):
        # Извлекаем участников приватного чата из названия комнаты
        parts = room.split('_')
        if len(parts) == 3:
            user1, user2 = parts[1], parts[2]
            messages = get_private_messages(user1, user2)
        else:
            messages = []
    else:
        messages = get_recent_messages(room)
    
    return jsonify(messages)

# WebSocket события
@socketio.on('connect')
def handle_connect():
    if 'username' in session:
        join_room('public')
        join_room(f"user_{session['username']}")  # Комната для уведомлений
        update_user_online_status(session['username'], True)
        
        # Уведомляем всех о новом пользователе
        online_users = get_online_users()
        emit('user_joined', {
            'username': session['username'],
            'online_users': online_users
        }, room='public', include_self=False)

@socketio.on('disconnect')
def handle_disconnect():
    if 'username' in session:
        update_user_online_status(session['username'], False)
        online_users = get_online_users()
        emit('user_left', {
            'username': session['username'],
            'online_users': online_users
        }, room='public')

@socketio.on('join_room')
def handle_join_room(data):
    if 'username' not in session:
        return
    
    room = data.get('room')
    if room:
        join_room(room)
        print(f"User {session['username']} joined room {room}")

@socketio.on('leave_room')
def handle_leave_room(data):
    if 'username' not in session:
        return
    
    room = data.get('room')
    if room:
        leave_room(room)
        print(f"User {session['username']} left room {room}")

@socketio.on('send_message')
def handle_send_message(data):
    if 'username' not in session:
        return
    
    message = data.get('message', '').strip()
    room = data.get('room', 'public')
    chat_type = data.get('chat_type', 'public')
    recipient = data.get('recipient')
    
    if not message:
        return
    
    print(f"Message from {session['username']} to room {room}: {message}")
    
    # Сохраняем сообщение в БД
    message_id = save_message(session['username'], message, room, recipient)
    
    # Отправляем сообщение в комнату
    if chat_type == 'private':
        emit('private_message', {
            'id': message_id,
            'user': session['username'],
            'message': message,
            'timestamp': datetime.now().isoformat(),
            'room': room
        }, room=room)
        
        # Отправляем уведомление получателю
        if recipient:
            emit('private_message', {
                'id': message_id,
                'user': session['username'],
                'message': message,
                'timestamp': datetime.now().isoformat(),
                'room': room
            }, room=f'user_{recipient}')
    else:
        emit('new_message', {
            'id': message_id,
            'user': session['username'],
            'message': message,
            'timestamp': datetime.now().isoformat(),
            'room': room
        }, room=room)

if __name__ == '__main__':
    init_db()
    print("🚀 Tandau Web Messenger запущен!")
    print("📍 Доступен по адресу: http://localhost:5000")
    print("💬 Поддерживает общие и личные чаты")
    print("👥 Личные чаты теперь работают!")
    
    socketio.run(app, host='0.0.0.0', port=5000, debug=False, allow_unsafe_werkzeug=True)
