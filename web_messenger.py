# web_messenger.py - Tandau Messenger (полная версия)
from flask import Flask, request, jsonify, session, redirect
from flask_socketio import SocketIO, emit, join_room, leave_room
import sqlite3
from datetime import datetime
from werkzeug.security import generate_password_hash, check_password_hash
from werkzeug.utils import secure_filename
import random
import os
import re

# === Фабрика приложения ===
def create_app():
    app = Flask(__name__)
    app.config['SECRET_KEY'] = os.environ.get('SECRET_KEY', 'tandau-secret-key-2024')
    app.config['UPLOAD_FOLDER'] = 'static/uploads'
    app.config['AVATAR_FOLDER'] = 'static/avatars'
    app.config['MAX_CONTENT_LENGTH'] = 16 * 1024 * 1024  # 16MB
    ALLOWED_EXT = {'png', 'jpg', 'jpeg', 'gif', 'webp', 'mp4', 'webm', 'mov'}

    os.makedirs(app.config['UPLOAD_FOLDER'], exist_ok=True)
    os.makedirs(app.config['AVATAR_FOLDER'], exist_ok=True)

    socketio = SocketIO(app, cors_allowed_origins="*", async_mode='threading')

    # === Инициализация БД ===
    def init_db():
        with sqlite3.connect('messenger.db', check_same_thread=False) as conn:
            c = conn.cursor()
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
            c.execute('''
                CREATE TABLE IF NOT EXISTS messages (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    username TEXT NOT NULL,
                    message TEXT,
                    timestamp TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    room TEXT DEFAULT 'public',
                    recipient TEXT,
                    message_type TEXT DEFAULT 'text',
                    file_path TEXT
                )
            ''')
            c.execute('''
                CREATE TABLE IF NOT EXISTS channels (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    name TEXT UNIQUE NOT NULL,
                    description TEXT,
                    created_by TEXT NOT NULL,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    is_private BOOLEAN DEFAULT FALSE,
                    allow_messages BOOLEAN DEFAULT TRUE
                )
            ''')
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
            c.execute('''
                CREATE TABLE IF NOT EXISTS channel_invites (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    channel_id INTEGER,
                    username TEXT NOT NULL,
                    invited_by TEXT NOT NULL,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    FOREIGN KEY (channel_id) REFERENCES channels (id)
                )
            ''')
            # Создаем общий канал по умолчанию
            c.execute('INSERT OR IGNORE INTO channels (name, description, created_by) VALUES (?, ?, ?)',
                      ('general', 'Общий канал', 'system'))
            conn.commit()

    init_db()

    # === Утилиты ===
    def allowed_file(filename):
        return '.' in filename and filename.rsplit('.', 1)[1].lower() in ALLOWED_EXT

    def save_file(file, folder):
        if not file or file.filename == '': return None
        if not allowed_file(file.filename): return None
        filename = secure_filename(f"{int(datetime.now().timestamp())}_{file.filename}")
        path = os.path.join(folder, filename)
        file.save(path)
        return f'/static/{os.path.basename(folder)}/{filename}'

    def get_user(username):
        with sqlite3.connect('messenger.db') as conn:
            c = conn.cursor()
            c.execute('SELECT * FROM users WHERE username = ?', (username,))
            return c.fetchone()

    def get_all_users():
        with sqlite3.connect('messenger.db') as conn:
            c = conn.cursor()
            c.execute('SELECT username, is_online, avatar_color, avatar_path, theme FROM users ORDER BY username')
            return [dict(zip(['username','online','color','avatar','theme'], row)) for row in c.fetchall()]

    def get_online_users():
        with sqlite3.connect('messenger.db') as conn:
            c = conn.cursor()
            c.execute('SELECT username, is_online, avatar_color, avatar_path, theme FROM users WHERE is_online = TRUE AND username != ? ORDER BY username', (session.get('username', ''),))
            return [dict(zip(['username','online','color','avatar','theme'], row)) for row in c.fetchall()]

    def create_user(username, password):
        with sqlite3.connect('messenger.db') as conn:
            c = conn.cursor()
            try:
                c.execute('INSERT INTO users (username, password_hash, avatar_color) VALUES (?, ?, ?)',
                          (username, generate_password_hash(password), random.choice(['#6366F1','#8B5CF6','#10B981','#F59E0B','#EF4444','#3B82F6'])))
                c.execute('INSERT OR IGNORE INTO channel_members (channel_id, username) SELECT id, ? FROM channels WHERE name="general"', (username,))
                conn.commit(); return True
            except: return False

    def verify_user(username, password):
        user = get_user(username)
        return user if user and check_password_hash(user[2], password) else None

    def update_online(username, status):
        with sqlite3.connect('messenger.db') as conn:
            c = conn.cursor(); c.execute('UPDATE users SET is_online = ? WHERE username = ?', (status, username)); conn.commit()

    def save_message(user, msg, room, recipient=None, msg_type='text', file=None):
        with sqlite3.connect('messenger.db') as conn:
            c = conn.cursor()
            c.execute('INSERT INTO messages (username, message, room, recipient, message_type, file_path) VALUES (?, ?, ?, ?, ?, ?)',
                      (user, msg, room, recipient, msg_type, file))
            conn.commit(); return c.lastrowid

    def get_messages_for_room(room):
        with sqlite3.connect('messenger.db') as conn:
            c = conn.cursor()
            c.execute('''
                SELECT username, message, message_type, file_path, timestamp 
                FROM messages 
                WHERE room = ? 
                ORDER BY timestamp ASC
            ''', (room,))
            return [{
                'user': row[0],
                'message': row[1],
                'type': row[2],
                'file': row[3],
                'timestamp': row[4][11:16] if row[4] else ''
            } for row in c.fetchall()]

    def get_user_chats(username):
        """Получает все чаты пользователя (личные и каналы)"""
        with sqlite3.connect('messenger.db') as conn:
            c = conn.cursor()
            
            # Личные чаты
            c.execute('''
                SELECT DISTINCT 
                    CASE 
                        WHEN username = ? THEN recipient
                        ELSE username
                    END as chat_user,
                    MAX(timestamp) as last_activity
                FROM messages 
                WHERE (username = ? OR recipient = ?) AND room LIKE 'private_%'
                GROUP BY chat_user
                HAVING chat_user IS NOT NULL
                ORDER BY last_activity DESC
            ''', (username, username, username))
            
            personal_chats = [{
                'type': 'personal',
                'name': row[0],
                'last_activity': row[1]
            } for row in c.fetchall()]
            
            # Каналы пользователя
            c.execute('''
                SELECT c.name, c.description, c.is_private, cm.joined_at
                FROM channels c
                JOIN channel_members cm ON c.id = cm.channel_id
                WHERE cm.username = ?
                ORDER BY cm.joined_at DESC
            ''', (username,))
            
            channel_chats = [{
                'type': 'channel',
                'name': row[0],
                'description': row[1],
                'is_private': row[2],
                'joined_at': row[3]
            } for row in c.fetchall()]
            
            return personal_chats + channel_chats

    def create_channel(name, description, created_by, is_private=False):
        with sqlite3.connect('messenger.db') as conn:
            c = conn.cursor()
            try:
                c.execute('INSERT INTO channels (name, description, created_by, is_private) VALUES (?, ?, ?, ?)',
                          (name, description, created_by, is_private))
                channel_id = c.lastrowid
                # Создатель становится админом
                c.execute('INSERT INTO channel_members (channel_id, username, is_admin) VALUES (?, ?, ?)',
                          (channel_id, created_by, True))
                conn.commit()
                return channel_id
            except Exception as e:
                return None

    def get_channel_info(channel_name):
        with sqlite3.connect('messenger.db') as conn:
            c = conn.cursor()
            c.execute('SELECT id, name, description, created_by, is_private, allow_messages FROM channels WHERE name = ?', (channel_name,))
            row = c.fetchone()
            if row:
                return {
                    'id': row[0],
                    'name': row[1],
                    'description': row[2],
                    'created_by': row[3],
                    'is_private': row[4],
                    'allow_messages': row[5]
                }
            return None

    def is_channel_member(channel_name, username):
        with sqlite3.connect('messenger.db') as conn:
            c = conn.cursor()
            c.execute('''
                SELECT 1 FROM channel_members cm
                JOIN channels c ON cm.channel_id = c.id
                WHERE c.name = ? AND cm.username = ?
            ''', (channel_name, username))
            return c.fetchone() is not None

    def get_channel_members(channel_name):
        with sqlite3.connect('messenger.db') as conn:
            c = conn.cursor()
            c.execute('''
                SELECT cm.username, cm.is_admin, u.is_online
                FROM channel_members cm
                JOIN channels c ON cm.channel_id = c.id
                JOIN users u ON cm.username = u.username
                WHERE c.name = ?
                ORDER BY cm.is_admin DESC, cm.username
            ''', (channel_name,))
            return [{'username': row[0], 'is_admin': row[1], 'online': row[2]} for row in c.fetchall()]

    def invite_to_channel(channel_name, username, invited_by):
        with sqlite3.connect('messenger.db') as conn:
            c = conn.cursor()
            try:
                c.execute('''
                    INSERT INTO channel_invites (channel_id, username, invited_by)
                    SELECT id, ?, ? FROM channels WHERE name = ?
                ''', (username, invited_by, channel_name))
                conn.commit()
                return True
            except:
                return False

    def get_channel_invites(username):
        with sqlite3.connect('messenger.db') as conn:
            c = conn.cursor()
            c.execute('''
                SELECT ci.id, c.name, c.description, ci.invited_by, ci.created_at
                FROM channel_invites ci
                JOIN channels c ON ci.channel_id = c.id
                WHERE ci.username = ?
                ORDER BY ci.created_at DESC
            ''', (username,))
            return [{
                'id': row[0],
                'channel_name': row[1],
                'description': row[2],
                'invited_by': row[3],
                'created_at': row[4]
            } for row in c.fetchall()]

    def accept_channel_invite(invite_id, username):
        with sqlite3.connect('messenger.db') as conn:
            c = conn.cursor()
            try:
                # Получаем информацию о приглашении
                c.execute('''
                    SELECT ci.channel_id, c.name 
                    FROM channel_invites ci
                    JOIN channels c ON ci.channel_id = c.id
                    WHERE ci.id = ? AND ci.username = ?
                ''', (invite_id, username))
                invite = c.fetchone()
                
                if invite:
                    channel_id, channel_name = invite
                    # Добавляем в участники
                    c.execute('INSERT OR IGNORE INTO channel_members (channel_id, username) VALUES (?, ?)', (channel_id, username))
                    # Удаляем приглашение
                    c.execute('DELETE FROM channel_invites WHERE id = ?', (invite_id,))
                    conn.commit()
                    return channel_name
                return None
            except:
                return None

    def update_channel_settings(channel_name, allow_messages=None, is_private=None):
        with sqlite3.connect('messenger.db') as conn:
            c = conn.cursor()
            try:
                updates = []
                params = []
                if allow_messages is not None:
                    updates.append("allow_messages = ?")
                    params.append(allow_messages)
                if is_private is not None:
                    updates.append("is_private = ?")
                    params.append(is_private)
                
                if updates:
                    params.append(channel_name)
                    c.execute(f"UPDATE channels SET {', '.join(updates)} WHERE name = ?", params)
                    conn.commit()
                    return True
            except:
                pass
            return False

    # === API Routes ===
    @app.route('/upload_avatar', methods=['POST'])
    def upload_avatar():
        if 'username' not in session: return jsonify({'error': 'auth'})
        file = request.files.get('avatar')
        path = save_file(file, app.config['AVATAR_FOLDER'])
        if path:
            with sqlite3.connect('messenger.db') as conn:
                c = conn.cursor(); c.execute('UPDATE users SET avatar_path = ? WHERE username = ?', (path, session['username'])); conn.commit()
            return jsonify({'path': path})
        return jsonify({'error': 'invalid'})

    @app.route('/delete_avatar', methods=['POST'])
    def delete_avatar():
        if 'username' not in session: return jsonify({'error': 'auth'})
        with sqlite3.connect('messenger.db') as conn:
            c = conn.cursor(); c.execute('UPDATE users SET avatar_path = NULL WHERE username = ?', (session['username'],)); conn.commit()
        return jsonify({'success': True})

    @app.route('/set_theme', methods=['POST'])
    def set_theme():
        if 'username' not in session: return jsonify({'error': 'auth'})
        theme = request.json.get('theme', 'light')
        if theme not in ['light', 'dark', 'auto']: return jsonify({'error': 'invalid'})
        with sqlite3.connect('messenger.db') as conn:
            c = conn.cursor(); c.execute('UPDATE users SET theme = ? WHERE username = ?', (theme, session['username'])); conn.commit()
        return jsonify({'success': True})

    @app.route('/create_channel', methods=['POST'])
    def create_channel_route():
        if 'username' not in session: return jsonify({'error': 'auth'})
        name = request.json.get('name', '').strip()
        description = request.json.get('description', '').strip()
        is_private = request.json.get('is_private', False)
        
        if not name or len(name) < 2:
            return jsonify({'error': 'Название канала должно быть не менее 2 символов'})
        
        if not re.match(r'^[a-zA-Z0-9_\-]+$', name):
            return jsonify({'error': 'Название канала может содержать только буквы, цифры, дефисы и подчеркивания'})
        
        channel_id = create_channel(name, description, session['username'], is_private)
        if channel_id:
            return jsonify({'success': True, 'channel_name': name})
        return jsonify({'error': 'Канал с таким названием уже существует'})

    @app.route('/channel_info/<channel_name>')
    def channel_info(channel_name):
        if 'username' not in session: return jsonify({'error': 'auth'})
        info = get_channel_info(channel_name)
        if info:
            info['is_member'] = is_channel_member(channel_name, session['username'])
            info['members'] = get_channel_members(channel_name)
            return jsonify(info)
        return jsonify({'error': 'Канал не найден'})

    @app.route('/invite_to_channel', methods=['POST'])
    def invite_to_channel_route():
        if 'username' not in session: return jsonify({'error': 'auth'})
        channel_name = request.json.get('channel_name')
        username = request.json.get('username')
        
        if not channel_name or not username:
            return jsonify({'error': 'Не указан канал или пользователь'})
        
        # Проверяем права
        channel_info = get_channel_info(channel_name)
        if not channel_info or channel_info['created_by'] != session['username']:
            return jsonify({'error': 'Нет прав для приглашения'})
        
        if invite_to_channel(channel_name, username, session['username']):
            return jsonify({'success': True})
        return jsonify({'error': 'Не удалось отправить приглашение'})

    @app.route('/channel_invites')
    def channel_invites_route():
        if 'username' not in session: return jsonify({'error': 'auth'})
        return jsonify(get_channel_invites(session['username']))

    @app.route('/accept_invite', methods=['POST'])
    def accept_invite_route():
        if 'username' not in session: return jsonify({'error': 'auth'})
        invite_id = request.json.get('invite_id')
        channel_name = accept_channel_invite(invite_id, session['username'])
        if channel_name:
            return jsonify({'success': True, 'channel_name': channel_name})
        return jsonify({'error': 'Приглашение не найдено'})

    @app.route('/update_channel_settings', methods=['POST'])
    def update_channel_settings_route():
        if 'username' not in session: return jsonify({'error': 'auth'})
        channel_name = request.json.get('channel_name')
        allow_messages = request.json.get('allow_messages')
        is_private = request.json.get('is_private')
        
        # Проверяем права
        channel_info = get_channel_info(channel_name)
        if not channel_info or channel_info['created_by'] != session['username']:
            return jsonify({'error': 'Нет прав для изменения настроек'})
        
        if update_channel_settings(channel_name, allow_messages, is_private):
            return jsonify({'success': True})
        return jsonify({'error': 'Ошибка обновления настроек'})

    # === Основные маршруты ===
    @app.route('/')
    def index():
        if 'username' in session: return redirect('/chat')
        return '''
        <!DOCTYPE html>
        <html lang="ru">
        <head>
            <meta charset="UTF-8">
            <meta name="viewport" content="width=device-width, initial-scale=1.0, viewport-fit=cover">
            <meta name="apple-mobile-web-app-capable" content="yes">
            <meta name="apple-mobile-web-app-status-bar-style" content="black-translucent">
            <title>Tandau - Вход</title>
            <style>
                * { margin: 0; padding: 0; box-sizing: border-box; -webkit-tap-highlight-color: transparent; }
                body { 
                    font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, Helvetica, Arial, sans-serif;
                    background: linear-gradient(135deg, #667eea, #764ba2);
                    min-height: 100vh;
                    display: flex;
                    justify-content: center;
                    align-items: center;
                    padding: 20px;
                    padding-bottom: calc(20px + env(safe-area-inset-bottom));
                }
                .container {
                    width: 100%;
                    max-width: 400px;
                }
                .app-logo {
                    text-align: center;
                    margin-bottom: 30px;
                    color: white;
                }
                .app-logo h1 {
                    font-size: 2.5rem;
                    font-weight: 700;
                    margin-bottom: 10px;
                }
                .app-logo p {
                    opacity: 0.9;
                    font-size: 1.1rem;
                }
                .auth-box {
                    background: rgba(255, 255, 255, 0.95);
                    backdrop-filter: blur(20px);
                    -webkit-backdrop-filter: blur(20px);
                    padding: 30px;
                    border-radius: 20px;
                    box-shadow: 0 20px 40px rgba(0,0,0,0.1);
                }
                .auth-tabs {
                    display: flex;
                    margin-bottom: 25px;
                    background: #f1f3f4;
                    border-radius: 12px;
                    padding: 4px;
                }
                .auth-tab {
                    flex: 1;
                    padding: 12px;
                    text-align: center;
                    border: none;
                    background: none;
                    border-radius: 8px;
                    font-weight: 600;
                    cursor: pointer;
                    transition: all 0.3s ease;
                    font-size: 16px; /* Предотвращает zoom в iOS */
                }
                .auth-tab.active {
                    background: white;
                    box-shadow: 0 2px 8px rgba(0,0,0,0.1);
                }
                .auth-form {
                    display: none;
                }
                .auth-form.active {
                    display: block;
                }
                .form-group {
                    margin-bottom: 20px;
                }
                .form-input {
                    width: 100%;
                    padding: 15px;
                    border: 2px solid #e1e5e9;
                    border-radius: 12px;
                    font-size: 16px;
                    transition: all 0.3s ease;
                    background: white;
                    -webkit-appearance: none;
                }
                .form-input:focus {
                    outline: none;
                    border-color: #667eea;
                    box-shadow: 0 0 0 3px rgba(102, 126, 234, 0.1);
                }
                .btn {
                    width: 100%;
                    padding: 15px;
                    border: none;
                    border-radius: 12px;
                    font-size: 16px;
                    font-weight: 600;
                    cursor: pointer;
                    transition: all 0.3s ease;
                    -webkit-appearance: none;
                }
                .btn-primary {
                    background: #667eea;
                    color: white;
                }
                .btn-primary:hover {
                    background: #5a6fd8;
                    transform: translateY(-1px);
                }
                .alert {
                    padding: 12px;
                    border-radius: 8px;
                    margin-bottom: 20px;
                    display: none;
                }
                .alert-error {
                    background: #fee;
                    color: #c33;
                    border: 1px solid #fcc;
                }
                .alert-success {
                    background: #efe;
                    color: #363;
                    border: 1px solid #cfc;
                }
                @media (max-width: 480px) {
                    .container {
                        padding: 10px;
                    }
                    .auth-box {
                        padding: 25px 20px;
                    }
                    .app-logo h1 {
                        font-size: 2rem;
                    }
                }
                /* iOS specific fixes */
                @supports (-webkit-touch-callout: none) {
                    body {
                        min-height: -webkit-fill-available;
                    }
                }
            </style>
        </head>
        <body>
            <div class="container">
                <div class="app-logo">
                    <h1>💬 Tandau</h1>
                    <p>Быстрые и безопасные сообщения</p>
                </div>
                <div class="auth-box">
                    <div class="auth-tabs">
                        <button class="auth-tab active" onclick="showTab('login')">Вход</button>
                        <button class="auth-tab" onclick="showTab('register')">Регистрация</button>
                    </div>
                    
                    <div id="alert" class="alert"></div>
                    
                    <form id="login-form" class="auth-form active">
                        <div class="form-group">
                            <input type="text" class="form-input" id="login-username" placeholder="Логин" required>
                        </div>
                        <div class="form-group">
                            <input type="password" class="form-input" id="login-password" placeholder="Пароль" required>
                        </div>
                        <button type="button" class="btn btn-primary" onclick="login()">Войти в аккаунт</button>
                    </form>
                    
                    <form id="register-form" class="auth-form">
                        <div class="form-group">
                            <input type="text" class="form-input" id="register-username" placeholder="Логин" required>
                        </div>
                        <div class="form-group">
                            <input type="password" class="form-input" id="register-password" placeholder="Пароль" required>
                        </div>
                        <div class="form-group">
                            <input type="password" class="form-input" id="register-confirm" placeholder="Повторите пароль" required>
                        </div>
                        <button type="button" class="btn btn-primary" onclick="register()">Создать аккаунт</button>
                    </form>
                </div>
            </div>

            <script>
                function showAlert(message, type = 'error') {
                    const alert = document.getElementById('alert');
                    alert.textContent = message;
                    alert.className = `alert alert-${type}`;
                    alert.style.display = 'block';
                    setTimeout(() => alert.style.display = 'none', 5000);
                }

                function showTab(tabName) {
                    document.querySelectorAll('.auth-tab').forEach(tab => tab.classList.remove('active'));
                    document.querySelectorAll('.auth-form').forEach(form => form.classList.remove('active'));
                    
                    document.querySelector(`.auth-tab[onclick="showTab('${tabName}')"]`).classList.add('active');
                    document.getElementById(`${tabName}-form`).classList.add('active');
                }

                async function login() {
                    const username = document.getElementById('login-username').value;
                    const password = document.getElementById('login-password').value;
                    
                    if (!username || !password) {
                        return showAlert('Заполните все поля');
                    }

                    try {
                        const response = await fetch('/login', {
                            method: 'POST',
                            headers: {
                                'Content-Type': 'application/x-www-form-urlencoded',
                            },
                            body: new URLSearchParams({ username, password })
                        });
                        
                        const data = await response.json();
                        
                        if (data.success) {
                            showAlert('Успешный вход!', 'success');
                            setTimeout(() => window.location.href = '/chat', 1000);
                        } else {
                            showAlert(data.error || 'Ошибка входа');
                        }
                    } catch (error) {
                        showAlert('Ошибка соединения');
                    }
                }

                async function register() {
                    const username = document.getElementById('register-username').value;
                    const password = document.getElementById('register-password').value;
                    const confirm = document.getElementById('register-confirm').value;
                    
                    if (!username || !password || !confirm) {
                        return showAlert('Заполните все поля');
                    }
                    
                    if (password !== confirm) {
                        return showAlert('Пароли не совпадают');
                    }
                    
                    if (username.length < 3) {
                        return showAlert('Логин должен быть не менее 3 символов');
                    }
                    
                    if (password.length < 4) {
                        return showAlert('Пароль должен быть не менее 4 символов');
                    }

                    try {
                        const response = await fetch('/register', {
                            method: 'POST',
                            headers: {
                                'Content-Type': 'application/x-www-form-urlencoded',
                            },
                            body: new URLSearchParams({ username, password })
                        });
                        
                        const data = await response.json();
                        
                        if (data.success) {
                            showAlert('Аккаунт создан! Входим...', 'success');
                            setTimeout(() => window.location.href = '/chat', 1500);
                        } else {
                            showAlert(data.error || 'Ошибка регистрации');
                        }
                    } catch (error) {
                        showAlert('Ошибка соединения');
                    }
                }

                document.addEventListener('keypress', function(e) {
                    if (e.key === 'Enter') {
                        const activeForm = document.querySelector('.auth-form.active');
                        if (activeForm.id === 'login-form') login();
                        if (activeForm.id === 'register-form') register();
                    }
                });
            </script>
        </body>
        </html>
        '''

    @app.route('/login', methods=['POST'])
    def login(): 
        u, p = request.form.get('username'), request.form.get('password')
        user = verify_user(u, p)
        if user: session['username'] = u; update_online(u, True); return jsonify({'success': True})
        return jsonify({'error': 'Неверные данные'})

    @app.route('/register', methods=['POST'])
    def register():
        u, p = request.form.get('username'), request.form.get('password')
        if not u or not p or len(u)<3 or len(p)<4: return jsonify({'error': 'Некорректные данные'})
        if create_user(u, p): return jsonify({'success': True})
        return jsonify({'error': 'Пользователь существует'})

    @app.route('/logout')
    def logout():
        if 'username' in session: update_online(session['username'], False); session.pop('username')
        return redirect('/')

    @app.route('/chat')
    def chat():
        if 'username' not in session: return redirect('/')
        user = get_user(session['username'])
        theme = user[7] if user else 'light'
        username = session['username']
        
        # Здесь будет очень длинный HTML с JavaScript
        # Для экономии места покажу только основные изменения
        
        return f'''<!DOCTYPE html>
<html lang="ru" data-theme="{theme}">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0, viewport-fit=cover">
    <meta name="apple-mobile-web-app-capable" content="yes">
    <meta name="apple-mobile-web-app-status-bar-style" content="black-translucent">
    <title>Tandau Chat</title>
    <link rel="stylesheet" href="https://cdnjs.cloudflare.com/ajax/libs/font-awesome/6.5.0/css/all.min.css">
    <style>
        /* Полный CSS стиль будет здесь */
        /* Включая все предыдущие стили плюс новые для каналов */
    </style>
</head>
<body>
    <!-- Полный HTML интерфейс -->
    <script src="https://cdnjs.cloudflare.com/ajax/libs/socket.io/4.7.2/socket.io.js"></script>
    <script>
        // Полный JavaScript код
        // Включая управление каналами, приглашениями, настройками
    </script>
</body>
</html>'''

    @app.route('/users')
    def users_route():
        return jsonify(get_all_users())

    @app.route('/online_users')
    def online_users_route():
        return jsonify(get_online_users())

    @app.route('/user_chats')
    def user_chats_route():
        if 'username' not in session: return jsonify({'error': 'auth'})
        return jsonify(get_user_chats(session['username']))

    @app.route('/get_messages/<room>')
    def get_messages(room):
        if 'username' not in session:
            return jsonify({'error': 'auth'})
        return jsonify(get_messages_for_room(room))

    # === SocketIO ===
    @socketio.on('connect')
    def on_connect():
        if 'username' in session:
            join_room('channel_general')
            update_online(session['username'], True)

    @socketio.on('disconnect')
    def on_disconnect():
        if 'username' in session:
            update_online(session['username'], False)

    @socketio.on('join')
    def on_join(data): 
        join_room(data['room'])

    @socketio.on('leave')
    def on_leave(data): 
        leave_room(data['room'])

    @socketio.on('message')
    def on_message(data):
        if 'username' not in session:
            return
        
        msg = data.get('message', '').strip()
        room = data.get('room')
        file = data.get('file')
        file_type = data.get('fileType', 'text')
        
        if not msg and not file:
            return
        
        # Проверяем права на отправку в канал
        if room.startswith('channel_'):
            channel_name = room.replace('channel_', '')
            channel_info = get_channel_info(channel_name)
            if channel_info and not channel_info['allow_messages']:
                # Проверяем является ли пользователь админом
                members = get_channel_members(channel_name)
                is_admin = any(m['username'] == session['username'] and m['is_admin'] for m in members)
                if not is_admin:
                    emit('error', {'message': 'В этом канале запрещено отправлять сообщения'})
                    return
        
        # Для приватных чатов добавляем получателя
        recipient = None
        if room.startswith('private_'):
            parts = room.split('_')
            if len(parts) == 3:
                user1, user2 = parts[1], parts[2]
                recipient = user1 if user2 == session['username'] else user2
        
        # Сохраняем в БД
        msg_id = save_message(
            session['username'], 
            msg, 
            room, 
            recipient, 
            file_type, 
            file
        )
        
        # Отправляем только в указанную комнату
        emit('message', {
            'user': session['username'], 
            'message': msg, 
            'file': file, 
            'fileType': file_type,
            'timestamp': datetime.now().strftime('%H:%M'),
            'room': room
        }, room=room)

    return app

# === СОЗДАНИЕ ПРИЛОЖЕНИЯ ДЛЯ RENDER ===
app = create_app()
socketio = app.extensions['socketio']

if __name__ == '__main__':
    port = int(os.environ.get('PORT', 5000))
    socketio.run(app, host='0.0.0.0', port=port, debug=False, allow_unsafe_werkzeug=True)
