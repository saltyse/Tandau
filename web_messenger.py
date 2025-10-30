# app.py - Простой рабочий мессенджер
from flask import Flask, request, jsonify, session, redirect
from flask_socketio import SocketIO, emit, join_room
import sqlite3
from datetime import datetime
from werkzeug.security import generate_password_hash, check_password_hash
import random
import os

app = Flask(__name__)
app.config['SECRET_KEY'] = 'simple-secret-key-2024'
socketio = SocketIO(app)

# Инициализация базы данных
def init_db():
    conn = sqlite3.connect('messenger.db')
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
            room TEXT DEFAULT 'general'
        )
    ''')
    
    conn.commit()
    conn.close()

# Простые функции для работы с БД
def get_user(username):
    conn = sqlite3.connect('messenger.db')
    cursor = conn.cursor()
    cursor.execute('SELECT * FROM users WHERE username = ?', (username,))
    user = cursor.fetchone()
    conn.close()
    return user

def create_user(username, password):
    try:
        conn = sqlite3.connect('messenger.db')
        cursor = conn.cursor()
        password_hash = generate_password_hash(password)
        cursor.execute(
            'INSERT INTO users (username, password_hash) VALUES (?, ?)',
            (username, password_hash)
        )
        conn.commit()
        conn.close()
        return True
    except:
        return False

def save_message(username, message, room='general'):
    conn = sqlite3.connect('messenger.db')
    cursor = conn.cursor()
    cursor.execute(
        'INSERT INTO messages (username, message, room) VALUES (?, ?, ?)',
        (username, message, room)
    )
    conn.commit()
    conn.close()

def get_messages(room='general', limit=50):
    conn = sqlite3.connect('messenger.db')
    cursor = conn.cursor()
    cursor.execute('''
        SELECT username, message, timestamp 
        FROM messages 
        WHERE room = ? 
        ORDER BY timestamp ASC 
        LIMIT ?
    ''', (room, limit))
    messages = cursor.fetchall()
    conn.close()
    return [{'user': msg[0], 'message': msg[1], 'timestamp': msg[2]} for msg in messages]

def get_online_users():
    conn = sqlite3.connect('messenger.db')
    cursor = conn.cursor()
    cursor.execute('SELECT username FROM users WHERE is_online = TRUE')
    users = [user[0] for user in cursor.fetchall()]
    conn.close()
    return users

def update_online_status(username, online):
    conn = sqlite3.connect('messenger.db')
    cursor = conn.cursor()
    cursor.execute('UPDATE users SET is_online = ? WHERE username = ?', (online, username))
    conn.commit()
    conn.close()

# Маршруты
@app.route('/')
def index():
    if 'username' in session:
        return redirect('/chat')
    
    return '''
    <!DOCTYPE html>
    <html>
    <head>
        <title>Messenger - Вход</title>
        <meta name="viewport" content="width=device-width, initial-scale=1.0">
        <style>
            body { font-family: Arial; background: #667eea; margin: 0; padding: 20px; display: flex; justify-content: center; align-items: center; min-height: 100vh; }
            .container { background: white; padding: 30px; border-radius: 10px; box-shadow: 0 10px 30px rgba(0,0,0,0.2); width: 100%; max-width: 400px; }
            h1 { text-align: center; color: #333; margin-bottom: 25px; }
            .form-group { margin-bottom: 15px; }
            input { width: 100%; padding: 12px; border: 1px solid #ddd; border-radius: 5px; font-size: 16px; box-sizing: border-box; }
            button { width: 100%; padding: 12px; background: #667eea; color: white; border: none; border-radius: 5px; font-size: 16px; cursor: pointer; }
            .alert { padding: 10px; background: #f8d7da; color: #721c24; border-radius: 5px; margin-bottom: 15px; display: none; text-align: center; }
        </style>
    </head>
    <body>
        <div class="container">
            <h1>Messenger</h1>
            <div id="alert" class="alert"></div>
            <div class="form-group"><input type="text" id="username" placeholder="Имя пользователя"></div>
            <div class="form-group"><input type="password" id="password" placeholder="Пароль"></div>
            <button onclick="login()">Войти</button>
            <div style="text-align: center; margin-top: 15px;">
                <a href="#" onclick="showRegister()">Нет аккаунта? Зарегистрироваться</a>
            </div>
        </div>
        <script>
            function showAlert(msg) { const a = document.getElementById('alert'); a.textContent = msg; a.style.display = 'block'; }
            async function login() {
                const username = document.getElementById('username').value;
                const password = document.getElementById('password').value;
                if (!username || !password) { showAlert('Заполните все поля'); return; }
                try {
                    const r = await fetch('/login', { method: 'POST', headers: {'Content-Type': 'application/json'}, body: JSON.stringify({username, password}) });
                    const data = await r.json();
                    if (data.success) window.location.href = '/chat';
                    else showAlert(data.error);
                } catch(e) { showAlert('Ошибка подключения'); }
            }
            function showRegister() {
                document.querySelector('button').textContent = 'Зарегистрироваться';
                document.querySelector('button').onclick = register;
                document.querySelector('a').textContent = 'Уже есть аккаунт? Войти';
                document.querySelector('a').onclick = showLogin;
            }
            function showLogin() {
                document.querySelector('button').textContent = 'Войти';
                document.querySelector('button').onclick = login;
                document.querySelector('a').textContent = 'Нет аккаунта? Зарегистрироваться';
                document.querySelector('a').onclick = showRegister;
            }
            async function register() {
                const username = document.getElementById('username').value;
                const password = document.getElementById('password').value;
                if (!username || !password) { showAlert('Заполните все поля'); return; }
                try {
                    const r = await fetch('/register', { method: 'POST', headers: {'Content-Type': 'application/json'}, body: JSON.stringify({username, password}) });
                    const data = await r.json();
                    if (data.success) { showAlert('Регистрация успешна!'); setTimeout(showLogin, 2000); }
                    else showAlert(data.error);
                } catch(e) { showAlert('Ошибка подключения'); }
            }
            document.addEventListener('keypress', e => { if(e.key === 'Enter') document.querySelector('button').onclick(); });
        </script>
    </body>
    </html>
    '''

@app.route('/login', methods=['POST'])
def login():
    data = request.get_json()
    username = data.get('username')
    password = data.get('password')
    
    user = get_user(username)
    if user and check_password_hash(user[2], password):
        session['username'] = username
        update_online_status(username, True)
        return jsonify({'success': True})
    return jsonify({'success': False, 'error': 'Неверные данные'})

@app.route('/register', methods=['POST'])
def register():
    data = request.get_json()
    username = data.get('username')
    password = data.get('password')
    
    if not username or not password:
        return jsonify({'success': False, 'error': 'Заполните все поля'})
    
    if len(username) < 3:
        return jsonify({'success': False, 'error': 'Имя слишком короткое'})
    
    if create_user(username, password):
        return jsonify({'success': True})
    return jsonify({'success': False, 'error': 'Пользователь существует'})

@app.route('/chat')
def chat():
    if 'username' not in session:
        return redirect('/')
    
    messages = get_messages()
    online_users = get_online_users()
    
    return f'''
    <!DOCTYPE html>
    <html>
    <head>
        <title>Messenger - Чат</title>
        <meta name="viewport" content="width=device-width, initial-scale=1.0">
        <style>
            * {{ margin: 0; padding: 0; box-sizing: border-box; }}
            body {{ font-family: Arial; background: #f0f2f5; height: 100vh; overflow: hidden; }}
            .container {{ display: flex; height: 100vh; }}
            .sidebar {{ width: 250px; background: white; border-right: 1px solid #ddd; display: flex; flex-direction: column; }}
            .header {{ background: #667eea; color: white; padding: 20px; text-align: center; }}
            .user-info {{ padding: 15px; background: #f8f9fa; border-bottom: 1px solid #ddd; }}
            .online-users {{ padding: 15px; flex: 1; }}
            .chat-area {{ flex: 1; display: flex; flex-direction: column; }}
            .chat-header {{ background: white; padding: 15px 20px; border-bottom: 1px solid #ddd; }}
            .messages {{ flex: 1; padding: 20px; overflow-y: auto; background: white; }}
            .message {{ margin-bottom: 10px; padding: 10px 15px; border-radius: 10px; max-width: 80%; }}
            .own {{ background: #667eea; color: white; margin-left: auto; }}
            .other {{ background: #e9ecef; }}
            .input-area {{ padding: 15px; background: white; border-top: 1px solid #ddd; }}
            .input-container {{ display: flex; gap: 10px; }}
            .message-input {{ flex: 1; padding: 12px; border: 1px solid #ddd; border-radius: 25px; }}
            .send-btn {{ background: #667eea; color: white; border: none; border-radius: 50%; width: 50px; height: 50px; cursor: pointer; }}
            .logout-btn {{ background: #dc3545; color: white; border: none; padding: 10px; margin: 10px; border-radius: 5px; cursor: pointer; }}
            @media (max-width: 768px) {{
                .sidebar {{ position: fixed; top: 0; left: 0; height: 100vh; transform: translateX(-100%); z-index: 1000; }}
                .sidebar.active {{ transform: translateX(0); }}
                .mobile-menu {{ display: block; background: none; border: none; font-size: 20px; cursor: pointer; }}
            }}
        </style>
    </head>
    <body>
        <div class="container">
            <div class="sidebar" id="sidebar">
                <div class="header"><h2>Messenger</h2></div>
                <div class="user-info"><strong>{session['username']}</strong><div>🟢 В сети</div></div>
                <div class="online-users">
                    <h4>Онлайн ({len(online_users)}):</h4>
                    {'<br>'.join(online_users)}
                </div>
                <button class="logout-btn" onclick="logout()">Выйти</button>
            </div>
            <div class="chat-area">
                <div class="chat-header">
                    <button class="mobile-menu" onclick="toggleSidebar()" style="display:none">☰</button>
                    <h3>Общий чат</h3>
                </div>
                <div class="messages" id="messages">
                    {''.join(f'<div class="message {"own" if msg["user"] == session["username"] else "other"}"><strong>{msg["user"]}</strong><br>{msg["message"]}</div>' for msg in messages)}
                </div>
                <div class="input-area">
                    <div class="input-container">
                        <input type="text" class="message-input" id="messageInput" placeholder="Сообщение...">
                        <button class="send-btn" onclick="sendMessage()">➤</button>
                    </div>
                </div>
            </div>
        </div>
        <script src="https://cdnjs.cloudflare.com/ajax/libs/socket.io/4.0.1/socket.io.js"></script>
        <script>
            const socket = io();
            const username = "{session['username']}";
            
            socket.on('connect', () => {{ console.log('Connected'); }});
            socket.on('new_message', (data) => {{
                const messages = document.getElementById('messages');
                const div = document.createElement('div');
                div.className = `message ${{data.user === username ? 'own' : 'other'}}`;
                div.innerHTML = `<strong>${{data.user}}</strong><br>${{data.message}}`;
                messages.appendChild(div);
                messages.scrollTop = messages.scrollHeight;
            }});
            
            function sendMessage() {{
                const input = document.getElementById('messageInput');
                const message = input.value.trim();
                if (message) {{
                    socket.emit('send_message', {{message: message}});
                    input.value = '';
                }}
            }}
            
            function logout() {{
                if (confirm('Выйти?')) window.location.href = '/logout';
            }}
            
            function toggleSidebar() {{
                document.getElementById('sidebar').classList.toggle('active');
            }}
            
            document.getElementById('messageInput').addEventListener('keypress', (e) => {{
                if (e.key === 'Enter') sendMessage();
            }});
            
            // Мобильная адаптация
            if (window.innerWidth <= 768) {{
                document.querySelector('.mobile-menu').style.display = 'block';
            }}
        </script>
    </body>
    </html>
    '''

@app.route('/logout')
def logout():
    if 'username' in session:
        update_online_status(session['username'], False)
        session.pop('username', None)
    return redirect('/')

# WebSocket
@socketio.on('connect')
def handle_connect():
    if 'username' in session:
        join_room('general')
        update_online_status(session['username'], True)

@socketio.on('disconnect')
def handle_disconnect():
    if 'username' in session:
        update_online_status(session['username'], False)

@socketio.on('send_message')
def handle_message(data):
    if 'username' not in session:
        return
    
    message = data.get('message', '').strip()
    if message:
        save_message(session['username'], message)
        emit('new_message', {
            'user': session['username'],
            'message': message,
            'timestamp': datetime.now().isoformat()
        }, room='general')

if __name__ == '__main__':
    init_db()
    print("🚀 Messenger запущен: http://localhost:5000")
    socketio.run(app, host='0.0.0.0', port=5000, debug=True, allow_unsafe_werkzeug=True)
