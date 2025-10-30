# simple_messenger.py - Упрощенная версия Tandau Messenger
from flask import Flask, render_template, request, jsonify, session, redirect, url_for
from flask_socketio import SocketIO, emit, join_room, leave_room
import sqlite3
from datetime import datetime
from werkzeug.security import generate_password_hash, check_password_hash
import random

app = Flask(__name__)
app.config['SECRET_KEY'] = 'tandau-secret-key-2024'
socketio = SocketIO(app)

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
                is_online BOOLEAN DEFAULT FALSE
            )
        ''')
        
        # Таблица сообщений
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS messages (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                username TEXT NOT NULL,
                message TEXT NOT NULL,
                timestamp TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                room TEXT DEFAULT 'public'
            )
        ''')
        
        conn.commit()

# Утилиты для работы с пользователями
def get_user_by_username(username):
    with sqlite3.connect('messenger.db') as conn:
        cursor = conn.cursor()
        cursor.execute('SELECT * FROM users WHERE username = ?', (username,))
        return cursor.fetchone()

def create_user(username, password):
    with sqlite3.connect('messenger.db') as conn:
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
    with sqlite3.connect('messenger.db') as conn:
        cursor = conn.cursor()
        cursor.execute(
            'UPDATE users SET is_online = ? WHERE username = ?',
            (is_online, username)
        )
        conn.commit()

def save_message(username, message, room='public'):
    with sqlite3.connect('messenger.db') as conn:
        cursor = conn.cursor()
        cursor.execute(
            'INSERT INTO messages (username, message, room) VALUES (?, ?, ?)',
            (username, message, room)
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
            ORDER BY timestamp DESC 
            LIMIT ?
        ''', (room, limit))
        messages = cursor.fetchall()
        return [{
            'user': msg[0],
            'message': msg[1],
            'timestamp': msg[2]
        } for msg in reversed(messages)]

def get_online_users():
    with sqlite3.connect('messenger.db') as conn:
        cursor = conn.cursor()
        cursor.execute('SELECT username FROM users WHERE is_online = TRUE')
        return [user[0] for user in cursor.fetchall()]

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
        }}
        .online-users {{
            padding: 15px;
            flex: 1;
            overflow-y: auto;
        }}
        .chat-area {{
            flex: 1;
            display: flex;
            flex-direction: column;
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
        .user-item {{
            padding: 8px 12px;
            margin: 2px 0;
            background: #f8f9fa;
            border-radius: 5px;
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
                <strong>Вы:</strong> {session['username']}
            </div>
            <div class="online-users">
                <h4>Онлайн пользователи:</h4>
                <div id="online-users-list">
                    {''.join(f'<div class="user-item">{user}</div>' for user in online_users)}
                </div>
            </div>
            <button class="logout-btn" onclick="logout()">Выйти</button>
        </div>
        
        <div class="chat-area">
            <div class="messages" id="messages">
                {''.join(f'''
                <div class="message {'own' if msg['user'] == session['username'] else 'other'}">
                    <div class="message-user">{msg['user']}</div>
                    <div>{msg['message']}</div>
                </div>
                ''' for msg in messages)}
            </div>
            
            <div class="input-area">
                <div class="input-container">
                    <input type="text" class="message-input" id="message-input" placeholder="Введите сообщение...">
                    <button class="send-btn" onclick="sendMessage()">➤</button>
                </div>
            </div>
        </div>
    </div>

    <script src="https://cdnjs.cloudflare.com/ajax/libs/socket.io/4.0.1/socket.io.js"></script>
    <script>
        const socket = io();
        const username = "{session['username']}";
        
        socket.on('connect', function() {{
            console.log('Connected to server');
        }});
        
        socket.on('new_message', function(data) {{
            addMessage(data);
        }});
        
        socket.on('user_joined', function(data) {{
            updateOnlineUsers(data.online_users);
        }});
        
        socket.on('user_left', function(data) {{
            updateOnlineUsers(data.online_users);
        }});
        
        function addMessage(data) {{
            const messages = document.getElementById('messages');
            const messageDiv = document.createElement('div');
            messageDiv.className = `message ${{data.user === username ? 'own' : 'other'}}`;
            messageDiv.innerHTML = `
                <div class="message-user">${{data.user}}</div>
                <div>${{data.message}}</div>
            `;
            messages.appendChild(messageDiv);
            messages.scrollTop = messages.scrollHeight;
        }}
        
        function sendMessage() {{
            const input = document.getElementById('message-input');
            const message = input.value.trim();
            
            if (message) {{
                socket.emit('send_message', {{
                    message: message,
                    room: 'public'
                }});
                input.value = '';
            }}
        }}
        
        function updateOnlineUsers(users) {{
            const list = document.getElementById('online-users-list');
            list.innerHTML = users.map(user => 
                `<div class="user-item">${{user}}</div>`
            ).join('');
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
        
        // Автопрокрутка при загрузке
        window.addEventListener('load', function() {{
            const messages = document.getElementById('messages');
            messages.scrollTop = messages.scrollHeight;
        }});
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

@socketio.on('disconnect')
def handle_disconnect():
    if 'username' in session:
        update_user_online_status(session['username'], False)
        emit('user_left', {
            'username': session['username'],
            'online_users': get_online_users()
        }, room='public')

@socketio.on('send_message')
def handle_send_message(data):
    if 'username' not in session:
        return
    
    message = data.get('message', '').strip()
    room = data.get('room', 'public')
    
    if not message:
        return
    
    # Сохраняем сообщение в БД
    message_id = save_message(session['username'], message, room)
    
    # Отправляем сообщение в комнату
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
    socketio.run(app, host='0.0.0.0', port=5000, debug=True)
