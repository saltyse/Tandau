# web_messenger.py - Tandau Messenger (Single File Version)
import os
import sqlite3
import base64
import json
import random
import re
from datetime import datetime
from werkzeug.security import generate_password_hash, check_password_hash
from werkzeug.utils import secure_filename

from flask import Flask, request, jsonify, session, redirect, send_from_directory
from flask_socketio import SocketIO, emit, join_room, leave_room

# === Конфигурация ===
app = Flask(__name__)
app.config['SECRET_KEY'] = os.environ.get('SECRET_KEY', 'tandau-secret-key-2024')

# На Render используем временные папки в /tmp для загрузок
app.config['UPLOAD_FOLDER'] = '/tmp/static/uploads'
app.config['AVATAR_FOLDER'] = '/tmp/static/avatars'
app.config['FAVORITE_FOLDER'] = '/tmp/static/favorites'
app.config['MAX_CONTENT_LENGTH'] = 16 * 1024 * 1024  # 16MB

ALLOWED_EXT = {'png', 'jpg', 'jpeg', 'gif', 'webp', 'mp4', 'webm', 'mov', 'txt', 'pdf', 'doc', 'docx'}

# Создаем папки для загрузок
for folder in [app.config['UPLOAD_FOLDER'], app.config['AVATAR_FOLDER'], app.config['FAVORITE_FOLDER']]:
    os.makedirs(folder, exist_ok=True)

socketio = SocketIO(app, cors_allowed_origins="*", async_mode='threading')

# === Утилиты ===
def allowed_file(filename):
    return '.' in filename and filename.rsplit('.', 1)[1].lower() in ALLOWED_EXT

def save_uploaded_file(file, folder):
    if not file or file.filename == '':
        return None, None
    if not allowed_file(file.filename):
        return None, None
    
    filename = secure_filename(f"{int(datetime.now().timestamp())}_{file.filename}")
    path = os.path.join(folder, filename)
    try:
        file.save(path)
        # Возвращаем путь для веб-доступа
        return f'/static/{os.path.basename(folder)}/{filename}', filename
    except Exception as e:
        print(f"Error saving file: {e}")
        return None, None

def save_base64_file(base64_data, folder, file_extension):
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

# === Инициализация БД ===
def init_db():
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
        
        conn.commit()

# Инициализируем БД
init_db()

# === Функции БД ===
def get_user(username):
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
    with sqlite3.connect('messenger.db') as conn:
        c = conn.cursor()
        c.execute('SELECT username, is_online, avatar_color, avatar_path, theme FROM users ORDER BY username')
        return [dict(zip(['username','online','color','avatar','theme'], row)) for row in c.fetchall()]

def create_user(username, password):
    with sqlite3.connect('messenger.db') as conn:
        c = conn.cursor()
        try:
            # Проверяем, существует ли пользователь
            c.execute('SELECT id FROM users WHERE username = ?', (username,))
            if c.fetchone():
                return False, "Пользователь уже существует"
            
            # Создаем пользователя
            c.execute('INSERT INTO users (username, password_hash, avatar_color) VALUES (?, ?, ?)',
                      (username, generate_password_hash(password), 
                       random.choice(['#6366F1','#8B5CF6','#10B981','#F59E0B','#EF4444','#3B82F6'])))
            
            # Добавляем пользователя в общий канал
            c.execute('INSERT OR IGNORE INTO channel_members (channel_id, username) SELECT id, ? FROM channels WHERE name="general"', (username,))
            conn.commit()
            return True, "Пользователь создан успешно"
        except Exception as e:
            return False, f"Ошибка при создании пользователя: {str(e)}"

def verify_user(username, password):
    user = get_user(username)
    if user and check_password_hash(user['password_hash'], password):
        return user
    return None

def update_online(username, status):
    with sqlite3.connect('messenger.db') as conn:
        c = conn.cursor()
        c.execute('UPDATE users SET is_online = ? WHERE username = ?', (status, username))
        conn.commit()

def save_message(user, msg, room, recipient=None, msg_type='text', file_path=None, file_name=None, is_favorite=False):
    with sqlite3.connect('messenger.db') as conn:
        c = conn.cursor()
        c.execute('''
            INSERT INTO messages (username, message, room, recipient, message_type, file_path, file_name, is_favorite) 
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        ''', (user, msg, room, recipient, msg_type, file_path, file_name, is_favorite))
        conn.commit()
        return c.lastrowid

def get_messages_for_room(room, limit=100):
    with sqlite3.connect('messenger.db') as conn:
        c = conn.cursor()
        c.execute('''
            SELECT username, message, message_type, file_path, file_name, timestamp 
            FROM messages 
            WHERE room = ? 
            ORDER BY timestamp ASC
            LIMIT ?
        ''', (room, limit))
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

# === API Routes ===
@app.route('/')
def index():
    if 'username' in session:
        return redirect('/chat')
    
    # Современная страница входа/регистрации
    return '''
    <!DOCTYPE html>
    <html lang="ru">
    <head>
        <meta charset="UTF-8">
        <meta name="viewport" content="width=device-width, initial-scale=1.0">
        <title>Tandau Messenger</title>
        <style>
            * { margin: 0; padding: 0; box-sizing: border-box; }
            body { 
                background: linear-gradient(135deg, #667eea 0%, #764ba2 100%);
                min-height: 100vh; display: flex; align-items: center; justify-content: center; 
                font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif;
            }
            .container { 
                background: white; padding: 40px; border-radius: 20px; 
                box-shadow: 0 20px 60px rgba(0,0,0,0.3); max-width: 400px; width: 90%;
            }
            h1 { text-align: center; margin-bottom: 30px; color: #333; }
            .tab-buttons { display: flex; margin-bottom: 20px; }
            .tab-btn { 
                flex: 1; padding: 12px; background: #f0f0f0; border: none; 
                cursor: pointer; font-size: 16px; transition: all 0.3s;
            }
            .tab-btn.active { background: #667eea; color: white; }
            .form { display: none; }
            .form.active { display: block; }
            input { 
                width: 100%; padding: 12px; margin: 10px 0; border: 1px solid #ddd; 
                border-radius: 8px; font-size: 16px;
            }
            button[type="submit"] {
                width: 100%; padding: 14px; background: #667eea; color: white;
                border: none; border-radius: 8px; font-size: 16px; cursor: pointer;
                margin-top: 10px;
            }
            .alert { padding: 10px; margin: 10px 0; border-radius: 5px; display: none; }
            .error { background: #fee; color: #c33; }
            .success { background: #efe; color: #363; }
        </style>
    </head>
    <body>
        <div class="container">
            <h1>📱 Tandau Messenger</h1>
            <div class="tab-buttons">
                <button class="tab-btn active" onclick="showTab('login')">Вход</button>
                <button class="tab-btn" onclick="showTab('register')">Регистрация</button>
            </div>
            
            <div id="alert" class="alert"></div>
            
            <form id="login-form" class="form active">
                <input type="text" id="login-username" placeholder="Логин" required>
                <input type="password" id="login-password" placeholder="Пароль" required>
                <button type="button" onclick="login()">Войти</button>
            </form>
            
            <form id="register-form" class="form">
                <input type="text" id="register-username" placeholder="Логин (мин. 3 символа)" required>
                <input type="password" id="register-password" placeholder="Пароль (мин. 4 символа)" required>
                <input type="password" id="register-confirm" placeholder="Повторите пароль" required>
                <button type="button" onclick="register()">Зарегистрироваться</button>
            </form>
        </div>
        
        <script>
            function showAlert(message, type = 'error') {
                const alert = document.getElementById('alert');
                alert.textContent = message;
                alert.className = `alert ${type}`;
                alert.style.display = 'block';
                setTimeout(() => alert.style.display = 'none', 3000);
            }
            
            function showTab(tabName) {
                document.querySelectorAll('.tab-btn').forEach(btn => btn.classList.remove('active'));
                document.querySelectorAll('.form').forEach(form => form.classList.remove('active'));
                
                document.querySelector(`.tab-btn[onclick*="${tabName}"]`).classList.add('active');
                document.getElementById(`${tabName}-form`).classList.add('active');
            }
            
            async function login() {
                const username = document.getElementById('login-username').value.trim();
                const password = document.getElementById('login-password').value;
                
                if (!username || !password) {
                    return showAlert('Заполните все поля');
                }
                
                const response = await fetch('/login', {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/x-www-form-urlencoded' },
                    body: new URLSearchParams({ username, password })
                });
                
                const data = await response.json();
                if (data.success) {
                    window.location.href = '/chat';
                } else {
                    showAlert(data.error || 'Неверный логин или пароль');
                }
            }
            
            async function register() {
                const username = document.getElementById('register-username').value.trim();
                const password = document.getElementById('register-password').value;
                const confirm = document.getElementById('register-confirm').value;
                
                if (!username || !password || !confirm) {
                    return showAlert('Заполните все поля');
                }
                if (username.length < 3) {
                    return showAlert('Логин должен быть не менее 3 символов');
                }
                if (password.length < 4) {
                    return showAlert('Пароль должен быть не менее 4 символов');
                }
                if (password !== confirm) {
                    return showAlert('Пароли не совпадают');
                }
                
                const response = await fetch('/register', {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/x-www-form-urlencoded' },
                    body: new URLSearchParams({ username, password })
                });
                
                const data = await response.json();
                if (data.success) {
                    showAlert('Аккаунт создан! Входим...', 'success');
                    setTimeout(() => login(), 1000);
                } else {
                    showAlert(data.error || 'Ошибка регистрации');
                }
            }
            
            document.addEventListener('keypress', (e) => {
                if (e.key === 'Enter') {
                    const activeForm = document.querySelector('.form.active');
                    if (activeForm.id === 'login-form') login();
                    if (activeForm.id === 'register-form') register();
                }
            });
        </script>
    </body>
    </html>
    '''

@app.route('/login', methods=['POST'])
def login_handler():
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
    username = request.form.get('username', '').strip()
    password = request.form.get('password', '')
    
    if not username or not password:
        return jsonify({'success': False, 'error': 'Заполните все поля'})
    
    if len(username) < 3:
        return jsonify({'success': False, 'error': 'Логин должен быть не менее 3 символов'})
    
    if len(password) < 4:
        return jsonify({'success': False, 'error': 'Пароль должен быть не менее 4 символов'})
    
    success, message = create_user(username, password)
    return jsonify({'success': success, 'error': message if not success else None})

@app.route('/logout')
def logout_handler():
    if 'username' in session:
        update_online(session['username'], False)
        session.pop('username', None)
    return redirect('/')

@app.route('/chat')
def chat_handler():
    if 'username' not in session:
        return redirect('/')
    
    username = session['username']
    user = get_user(username)
    if not user:
        session.pop('username', None)
        return redirect('/')
    
    # Основной интерфейс чата
    return f'''
<!DOCTYPE html>
<html lang="ru">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>Tandau Chat - {username}</title>
    <style>
        * {{ margin: 0; padding: 0; box-sizing: border-box; }}
        body {{ 
            font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif;
            background: #f0f2f5; height: 100vh; display: flex;
        }}
        
        /* Сайдбар */
        .sidebar {{
            width: 300px; background: white; display: flex; flex-direction: column;
            border-right: 1px solid #ddd;
        }}
        .sidebar-header {{
            padding: 20px; background: #667eea; color: white; font-weight: bold;
            display: flex; align-items: center; gap: 10px;
        }}
        .user-info {{
            padding: 15px; border-bottom: 1px solid #ddd; display: flex; align-items: center; gap: 10px;
        }}
        .avatar {{
            width: 40px; height: 40px; border-radius: 50%; background: #667eea;
            color: white; display: flex; align-items: center; justify-content: center;
            font-weight: bold;
        }}
        .nav {{
            flex: 1; overflow-y: auto; padding: 10px;
        }}
        .nav-title {{
            padding: 10px; color: #666; font-size: 12px; text-transform: uppercase;
        }}
        .nav-item {{
            padding: 12px 15px; cursor: pointer; border-radius: 8px; margin: 5px 0;
            display: flex; align-items: center; gap: 10px;
        }}
        .nav-item:hover {{ background: #f0f2f5; }}
        .nav-item.active {{ background: #667eea; color: white; }}
        
        /* Основной чат */
        .chat-area {{
            flex: 1; display: flex; flex-direction: column;
        }}
        .chat-header {{
            padding: 15px 20px; background: white; border-bottom: 1px solid #ddd;
            font-weight: bold; display: flex; align-items: center; gap: 10px;
        }}
        .messages {{
            flex: 1; padding: 20px; overflow-y: auto; display: flex; flex-direction: column;
            gap: 15px;
        }}
        .message {{
            display: flex; align-items: flex-start; gap: 10px; max-width: 70%;
        }}
        .message.own {{ align-self: flex-end; flex-direction: row-reverse; }}
        .message-avatar {{
            width: 32px; height: 32px; border-radius: 50%; background: #667eea;
            color: white; display: flex; align-items: center; justify-content: center;
            font-weight: bold; font-size: 12px; flex-shrink: 0;
        }}
        .message-content {{
            background: white; padding: 10px 15px; border-radius: 18px;
            box-shadow: 0 1px 2px rgba(0,0,0,0.1); max-width: 100%;
        }}
        .message.own .message-content {{ background: #667eea; color: white; }}
        .message-sender {{ font-weight: bold; font-size: 14px; margin-bottom: 5px; }}
        .message-text {{ word-break: break-word; }}
        .message-time {{ font-size: 11px; color: #999; margin-top: 5px; text-align: right; }}
        .message-file img, .message-file video {{
            max-width: 300px; max-height: 200px; border-radius: 10px; margin-top: 10px;
        }}
        
        /* Поле ввода */
        .input-area {{
            padding: 20px; background: white; border-top: 1px solid #ddd;
        }}
        .input-row {{
            display: flex; gap: 10px; align-items: flex-end;
        }}
        textarea {{
            flex: 1; padding: 12px; border: 1px solid #ddd; border-radius: 20px;
            resize: none; font-size: 14px; max-height: 100px; min-height: 40px;
        }}
        .send-btn {{
            width: 40px; height: 40px; border-radius: 50%; background: #667eea;
            color: white; border: none; cursor: pointer; display: flex;
            align-items: center; justify-content: center;
        }}
        .file-preview {{
            margin-top: 10px; padding: 10px; background: #f8f9fa;
            border-radius: 10px; display: flex; align-items: center; gap: 10px;
        }}
        .file-preview img {{
            width: 50px; height: 50px; border-radius: 5px; object-fit: cover;
        }}
        
        /* Мобильная адаптация */
        @media (max-width: 768px) {{
            .sidebar {{ width: 100%; position: absolute; z-index: 1000; height: 100%; }}
            .chat-area {{ width: 100%; position: absolute; z-index: 900; height: 100%; }}
            .message {{ max-width: 85%; }}
            .message-file img, .message-file video {{ max-width: 200px; max-height: 150px; }}
        }}
    </style>
</head>
<body>
    <!-- Сайдбар -->
    <div class="sidebar" id="sidebar">
        <div class="sidebar-header">
            <div class="avatar" style="background: #667eea;">T</div>
            <span>Tandau Messenger</span>
        </div>
        
        <div class="user-info">
            <div class="avatar" id="user-avatar">{username[:2].upper()}</div>
            <div>
                <strong>{username}</strong><br>
                <small style="color: #4CAF50;">Online</small>
            </div>
        </div>
        
        <div class="nav">
            <div class="nav-title">Каналы</div>
            <div class="nav-item active" onclick="openRoom('channel_general', 'General')">
                <div style="width: 20px; text-align: center;">#</div>
                <span>General</span>
            </div>
            
            <div class="nav-title">Избранное</div>
            <div class="nav-item" onclick="openFavorites()">
                <div style="width: 20px; text-align: center;">⭐</div>
                <span>Избранное</span>
            </div>
            
            <div class="nav-title">Пользователи</div>
            <div id="users-list"></div>
        </div>
        
        <div style="padding: 15px;">
            <button onclick="location.href='/logout'" style="
                width: 100%; padding: 10px; background: #dc3545; color: white;
                border: none; border-radius: 8px; cursor: pointer;
            ">Выйти</button>
        </div>
    </div>
    
    <!-- Основной чат -->
    <div class="chat-area" id="chat-area">
        <div class="chat-header">
            <button onclick="toggleSidebar()" style="
                background: none; border: none; font-size: 18px; cursor: pointer;
                display: none;
            " id="menu-toggle">☰</button>
            <span id="chat-title">General</span>
        </div>
        
        <div class="messages" id="messages">
            <div style="text-align: center; padding: 40px; color: #666;">
                <div style="font-size: 48px; margin-bottom: 20px;">💬</div>
                <h3>Добро пожаловать в Tandau Messenger!</h3>
                <p>Начните общение, отправив сообщение</p>
            </div>
        </div>
        
        <div class="input-area">
            <div class="input-row">
                <button onclick="document.getElementById('file-input').click()" style="
                    background: none; border: none; font-size: 20px; cursor: pointer;
                    color: #667eea; padding: 10px;
                ">📎</button>
                <input type="file" id="file-input" style="display: none;" 
                       accept="image/*,video/*" onchange="previewFile(this)">
                <textarea id="message-input" placeholder="Введите сообщение..." 
                         onkeydown="handleKeydown(event)"></textarea>
                <button class="send-btn" onclick="sendMessage()">➤</button>
            </div>
            <div id="file-preview"></div>
        </div>
    </div>
    
    <script src="https://cdnjs.cloudflare.com/ajax/libs/socket.io/4.7.2/socket.io.js"></script>
    <script>
        const socket = io();
        const user = "{username}";
        let currentRoom = "channel_general";
        let isMobile = window.innerWidth <= 768;
        
        // Инициализация
        window.onload = function() {{
            loadUsers();
            loadMessages(currentRoom);
            socket.emit('join', {{ room: currentRoom }});
            
            if (isMobile) {{
                document.getElementById('menu-toggle').style.display = 'block';
                document.getElementById('sidebar').style.display = 'none';
            }}
            
            // Авторазмер textarea
            const textarea = document.getElementById('message-input');
            textarea.addEventListener('input', function() {{
                this.style.height = 'auto';
                this.style.height = Math.min(this.scrollHeight, 100) + 'px';
            }});
        }};
        
        // Переключение сайдбара на мобильных
        function toggleSidebar() {{
            const sidebar = document.getElementById('sidebar');
            if (sidebar.style.display === 'none') {{
                sidebar.style.display = 'flex';
            }} else {{
                sidebar.style.display = 'none';
            }}
        }}
        
        // Загрузка пользователей
        async function loadUsers() {{
            const response = await fetch('/users');
            const users = await response.json();
            
            const usersList = document.getElementById('users-list');
            usersList.innerHTML = '';
            
            users.forEach(u => {{
                if (u.username !== user) {{
                    const div = document.createElement('div');
                    div.className = 'nav-item';
                    div.innerHTML = `
                        <div class="avatar" style="
                            width: 30px; height: 30px; font-size: 12px;
                            background-color: ${{u.color || '#667eea'}};
                        ">${{u.username.slice(0, 2).toUpperCase()}}</div>
                        <span>${{u.username}}</span>
                    `;
                    div.onclick = () => openPrivateChat(u.username);
                    usersList.appendChild(div);
                }}
            }});
        }}
        
        // Открытие комнаты
        function openRoom(room, title) {{
            if (isMobile) {{
                document.getElementById('sidebar').style.display = 'none';
            }}
            
            currentRoom = room;
            document.getElementById('chat-title').textContent = title;
            
            // Обновляем активный элемент
            document.querySelectorAll('.nav-item').forEach(el => {{
                el.classList.remove('active');
            }});
            event.currentTarget.classList.add('active');
            
            // Загружаем сообщения
            loadMessages(room);
            
            // Присоединяемся к комнате
            socket.emit('leave', {{ room: currentRoom }});
            socket.emit('join', {{ room: room }});
        }}
        
        // Открытие личного чата
        function openPrivateChat(otherUser) {{
            const room = 'private_' + [user, otherUser].sort().join('_');
            openRoom(room, otherUser);
        }}
        
        // Открытие избранного
        function openFavorites() {{
            document.getElementById('messages').innerHTML = `
                <div style="padding: 20px;">
                    <h3>⭐ Избранное</h3>
                    <p style="color: #666; margin: 20px 0;">Функция избранного будет добавлена в следующем обновлении</p>
                </div>
            `;
            document.getElementById('input-area').style.display = 'none';
            document.getElementById('chat-title').textContent = 'Избранное';
        }}
        
        // Загрузка сообщений
        async function loadMessages(room) {{
            const response = await fetch('/get_messages/' + room);
            const messages = await response.json();
            
            const messagesContainer = document.getElementById('messages');
            messagesContainer.innerHTML = '';
            document.getElementById('input-area').style.display = 'block';
            
            if (messages && messages.length > 0) {{
                messages.forEach(msg => {{
                    addMessageToChat(msg);
                }});
            }} else {{
                messagesContainer.innerHTML = `
                    <div style="text-align: center; padding: 40px; color: #666;">
                        <div style="font-size: 48px; margin-bottom: 20px;">💭</div>
                        <h3>Начните общение</h3>
                        <p>Отправьте первое сообщение</p>
                    </div>
                `;
            }}
            
            messagesContainer.scrollTop = messagesContainer.scrollHeight;
        }}
        
        // Добавление сообщения в чат
        function addMessageToChat(data) {{
            const messagesContainer = document.getElementById('messages');
            
            // Удаляем placeholder, если он есть
            const placeholder = messagesContainer.querySelector('h3');
            if (placeholder && placeholder.textContent.includes('Добро пожаловать')) {{
                messagesContainer.innerHTML = '';
            }}
            
            const messageDiv = document.createElement('div');
            messageDiv.className = `message ${{data.user === user ? 'own' : ''}}`;
            
            const avatar = document.createElement('div');
            avatar.className = 'message-avatar';
            avatar.style.backgroundColor = data.color || '#667eea';
            avatar.textContent = data.user.slice(0, 2).toUpperCase();
            
            const content = document.createElement('div');
            content.className = 'message-content';
            
            if (data.user !== user) {{
                const sender = document.createElement('div');
                sender.className = 'message-sender';
                sender.textContent = data.user;
                content.appendChild(sender);
            }}
            
            if (data.message) {{
                const text = document.createElement('div');
                text.className = 'message-text';
                text.textContent = data.message;
                content.appendChild(text);
            }}
            
            if (data.file) {{
                const fileContainer = document.createElement('div');
                fileContainer.className = 'message-file';
                
                if (data.file.match(/\.(mp4|webm|mov)$/i)) {{
                    const video = document.createElement('video');
                    video.src = data.file;
                    video.controls = true;
                    fileContainer.appendChild(video);
                }} else {{
                    const img = document.createElement('img');
                    img.src = data.file;
                    img.alt = 'Изображение';
                    img.style.cursor = 'pointer';
                    img.onclick = () => window.open(data.file, '_blank');
                    fileContainer.appendChild(img);
                }}
                
                content.appendChild(fileContainer);
            }}
            
            const time = document.createElement('div');
            time.className = 'message-time';
            time.textContent = data.timestamp || new Date().toLocaleTimeString([], {{ hour: '2-digit', minute: '2-digit' }});
            content.appendChild(time);
            
            messageDiv.appendChild(avatar);
            messageDiv.appendChild(content);
            messagesContainer.appendChild(messageDiv);
            
            // Прокрутка вниз
            messagesContainer.scrollTop = messagesContainer.scrollHeight;
        }}
        
        // Отправка сообщения
        function sendMessage() {{
            const input = document.getElementById('message-input');
            const message = input.value.trim();
            const fileInput = document.getElementById('file-input');
            
            if (!message && !fileInput.files[0]) return;
            
            // Создаем FormData для отправки файла
            const formData = new FormData();
            formData.append('message', message);
            formData.append('room', currentRoom);
            
            if (fileInput.files[0]) {{
                formData.append('file', fileInput.files[0]);
            }}
            
            // Отправляем через fetch для надежности
            fetch('/send_message', {{
                method: 'POST',
                body: formData
            }})
            .then(response => response.json())
            .then(data => {{
                if (data.success) {{
                    // Добавляем свое сообщение в чат
                    addMessageToChat({{
                        user: user,
                        message: message,
                        file: data.file_path,
                        color: '#667eea',
                        timestamp: new Date().toLocaleTimeString([], {{ hour: '2-digit', minute: '2-digit' }})
                    }});
                    
                    // Очищаем поля
                    input.value = '';
                    input.style.height = 'auto';
                    document.getElementById('file-preview').innerHTML = '';
                    fileInput.value = '';
                }}
            }})
            .catch(error => console.error('Error:', error));
        }}
        
        // Предпросмотр файла
        function previewFile(input) {{
            const file = input.files[0];
            if (file) {{
                const reader = new FileReader();
                reader.onload = (e) => {{
                    const preview = document.getElementById('file-preview');
                    if (file.type.startsWith('image/')) {{
                        preview.innerHTML = `
                            <img src="${{e.target.result}}" style="width: 100px; height: 100px; object-fit: cover; border-radius: 5px;">
                            <div>
                                <div>${{file.name}}</div>
                                <button onclick="document.getElementById('file-preview').innerHTML=''; document.getElementById('file-input').value='';" 
                                        style="background:none; border:none; color:#dc3545; cursor:pointer;">
                                    Удалить
                                </button>
                            </div>
                        `;
                    }} else if (file.type.startsWith('video/')) {{
                        preview.innerHTML = `
                            <video src="${{e.target.result}}" style="width: 100px; height: 100px; object-fit: cover; border-radius: 5px;"></video>
                            <div>${{file.name}}</div>
                            <button onclick="document.getElementById('file-preview').innerHTML=''; document.getElementById('file-input').value='';"
                                    style="background:none; border:none; color:#dc3545; cursor:pointer;">
                                Удалить
                            </button>
                        `;
                    }}
                }};
                reader.readAsDataURL(file);
            }}
        }}
        
        // Обработка клавиши Enter
        function handleKeydown(e) {{
            if (e.key === 'Enter' && !e.shiftKey) {{
                e.preventDefault();
                sendMessage();
            }}
        }}
        
        // Socket события
        socket.on('message', (data) => {{
            if (data.room === currentRoom) {{
                addMessageToChat(data);
            }}
        }});
        
        // Ресайз окна
        window.addEventListener('resize', () => {{
            isMobile = window.innerWidth <= 768;
            if (!isMobile) {{
                document.getElementById('sidebar').style.display = 'flex';
                document.getElementById('menu-toggle').style.display = 'none';
            }} else {{
                document.getElementById('menu-toggle').style.display = 'block';
            }}
        }});
    </script>
</body>
</html>
'''

@app.route('/users')
def users_handler():
    return jsonify(get_all_users())

@app.route('/get_messages/<room>')
def get_messages_handler(room):
    if 'username' not in session:
        return jsonify({'error': 'auth'})
    messages = get_messages_for_room(room)
    return jsonify(messages)

@app.route('/send_message', methods=['POST'])
def send_message_handler():
    if 'username' not in session:
        return jsonify({'success': False, 'error': 'Не авторизован'})
    
    username = session['username']
    message = request.form.get('message', '').strip()
    room = request.form.get('room', 'channel_general')
    file = request.files.get('file')
    
    # Сохраняем файл если есть
    file_path = None
    file_name = None
    message_type = 'text'
    
    if file and file.filename:
        file_path, file_name = save_uploaded_file(file, app.config['UPLOAD_FOLDER'])
        if file_path:
            message_type = 'image' if file.filename.lower().endswith(('.png', '.jpg', '.jpeg', '.gif', '.webp')) else 'video'
        else:
            return jsonify({'success': False, 'error': 'Неверный формат файла'})
    
    # Сохраняем сообщение в БД
    msg_id = save_message(username, message, room, None, message_type, file_path, file_name)
    
    # Получаем информацию о пользователе
    user_info = get_user(username)
    
    # Отправляем через SocketIO
    socketio.emit('message', {
        'user': username,
        'message': message,
        'file': file_path,
        'file_name': file_name,
        'type': message_type,
        'color': user_info['avatar_color'] if user_info else '#6366F1',
        'avatar_path': user_info['avatar_path'] if user_info else None,
        'timestamp': datetime.now().strftime('%H:%M'),
        'room': room
    }, room=room)
    
    return jsonify({'success': True, 'file_path': file_path})

# Маршрут для статических файлов
@app.route('/static/<folder>/<filename>')
def serve_static(folder, filename):
    if folder == 'uploads':
        folder_path = app.config['UPLOAD_FOLDER']
    elif folder == 'avatars':
        folder_path = app.config['AVATAR_FOLDER']
    elif folder == 'favorites':
        folder_path = app.config['FAVORITE_FOLDER']
    else:
        return 'Not found', 404
    
    return send_from_directory(folder_path, filename)

# === SocketIO обработчики ===
@socketio.on('connect')
def handle_connect():
    if 'username' in session:
        update_online(session['username'], True)

@socketio.on('disconnect')
def handle_disconnect():
    if 'username' in session:
        update_online(session['username'], False)

@socketio.on('join')
def handle_join(data):
    room = data.get('room', 'channel_general')
    join_room(room)

@socketio.on('leave')
def handle_leave(data):
    room = data.get('room')
    if room:
        leave_room(room)

# Health check для Render
@app.route('/health')
def health_check():
    return jsonify({'status': 'healthy', 'service': 'Tandau Messenger'})

# Обработка ошибок
@app.errorhandler(404)
def not_found(e):
    return redirect('/')

if __name__ == '__main__':
    port = int(os.environ.get('PORT', 5000))
    socketio.run(app, host='0.0.0.0', port=port, debug=True, allow_unsafe_werkzeug=True)
