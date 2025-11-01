# web_messenger.py - Tandau Messenger (исправленная версия для Render)
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

    # Создаем папки для загрузок
    try:
        os.makedirs(app.config['UPLOAD_FOLDER'], exist_ok=True)
        os.makedirs(app.config['AVATAR_FOLDER'], exist_ok=True)
    except:
        pass

    # Используем gevent вместо eventlet
    socketio = SocketIO(app, cors_allowed_origins="*", async_mode='gevent')

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
            c.execute('SELECT username, is_online, avatar_color, avatar_path, theme FROM users WHERE username != ? ORDER BY username', (session.get('username', ''),))
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

    def get_user_personal_chats(username):
        """Получает личные чаты пользователя с историей сообщений"""
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
            
            personal_chats = [row[0] for row in c.fetchall()]
            return personal_chats

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

    def update_channel_settings(channel_name, allow_messages=None):
        with sqlite3.connect('messenger.db') as conn:
            c = conn.cursor()
            try:
                if allow_messages is not None:
                    c.execute("UPDATE channels SET allow_messages = ? WHERE name = ?", (allow_messages, channel_name))
                    conn.commit()
                    return True
            except:
                pass
            return False

    def get_user_channels(username):
        """Получает каналы пользователя"""
        with sqlite3.connect('messenger.db') as conn:
            c = conn.cursor()
            c.execute('''
                SELECT c.name, c.description, c.is_private, c.allow_messages, c.created_by
                FROM channels c
                JOIN channel_members cm ON c.id = cm.channel_id
                WHERE cm.username = ?
                ORDER BY c.name
            ''', (username,))
            return [{
                'name': row[0],
                'description': row[1],
                'is_private': row[2],
                'allow_messages': row[3],
                'created_by': row[4]
            } for row in c.fetchall()]

    # === API Routes ===
    @app.route('/upload_avatar', methods=['POST'])
    def upload_avatar_handler():
        if 'username' not in session: return jsonify({'error': 'auth'})
        file = request.files.get('avatar')
        path = save_file(file, app.config['AVATAR_FOLDER'])
        if path:
            with sqlite3.connect('messenger.db') as conn:
                c = conn.cursor(); c.execute('UPDATE users SET avatar_path = ? WHERE username = ?', (path, session['username'])); conn.commit()
            return jsonify({'path': path})
        return jsonify({'error': 'invalid'})

    @app.route('/delete_avatar', methods=['POST'])
    def delete_avatar_handler():
        if 'username' not in session: return jsonify({'error': 'auth'})
        with sqlite3.connect('messenger.db') as conn:
            c = conn.cursor(); c.execute('UPDATE users SET avatar_path = NULL WHERE username = ?', (session['username'],)); conn.commit()
        return jsonify({'success': True})

    @app.route('/set_theme', methods=['POST'])
    def set_theme_handler():
        if 'username' not in session: return jsonify({'error': 'auth'})
        theme = request.json.get('theme', 'light')
        if theme not in ['light', 'dark', 'auto']: return jsonify({'error': 'invalid'})
        with sqlite3.connect('messenger.db') as conn:
            c = conn.cursor(); c.execute('UPDATE users SET theme = ? WHERE username = ?', (theme, session['username'])); conn.commit()
        return jsonify({'success': True})

    @app.route('/create_channel', methods=['POST'])
    def create_channel_handler():
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
    def channel_info_handler(channel_name):
        if 'username' not in session: return jsonify({'error': 'auth'})
        info = get_channel_info(channel_name)
        if info:
            info['is_member'] = is_channel_member(channel_name, session['username'])
            info['members'] = get_channel_members(channel_name)
            return jsonify(info)
        return jsonify({'error': 'Канал не найден'})

    @app.route('/invite_to_channel', methods=['POST'])
    def invite_to_channel_handler():
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
    def channel_invites_handler():
        if 'username' not in session: return jsonify({'error': 'auth'})
        return jsonify(get_channel_invites(session['username']))

    @app.route('/accept_invite', methods=['POST'])
    def accept_invite_handler():
        if 'username' not in session: return jsonify({'error': 'auth'})
        invite_id = request.json.get('invite_id')
        channel_name = accept_channel_invite(invite_id, session['username'])
        if channel_name:
            return jsonify({'success': True, 'channel_name': channel_name})
        return jsonify({'error': 'Приглашение не найдено'})

    @app.route('/update_channel_settings', methods=['POST'])
    def update_channel_settings_handler():
        if 'username' not in session: return jsonify({'error': 'auth'})
        channel_name = request.json.get('channel_name')
        allow_messages = request.json.get('allow_messages')
        
        # Проверяем права
        channel_info = get_channel_info(channel_name)
        if not channel_info or channel_info['created_by'] != session['username']:
            return jsonify({'error': 'Нет прав для изменения настроек'})
        
        if update_channel_settings(channel_name, allow_messages):
            return jsonify({'success': True})
        return jsonify({'error': 'Ошибка обновления настроек'})

    @app.route('/user_channels')
    def user_channels_handler():
        if 'username' not in session: return jsonify({'error': 'auth'})
        return jsonify(get_user_channels(session['username']))

    @app.route('/personal_chats')
    def personal_chats_handler():
        if 'username' not in session: return jsonify({'error': 'auth'})
        return jsonify(get_user_personal_chats(session['username']))

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
                    font-size: 16px;
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
    def login_handler(): 
        u, p = request.form.get('username'), request.form.get('password')
        user = verify_user(u, p)
        if user: session['username'] = u; update_online(u, True); return jsonify({'success': True})
        return jsonify({'error': 'Неверные данные'})

    @app.route('/register', methods=['POST'])
    def register_handler():
        u, p = request.form.get('username'), request.form.get('password')
        if not u or not p or len(u)<3 or len(p)<4: return jsonify({'error': 'Некорректные данные'})
        if create_user(u, p): return jsonify({'success': True})
        return jsonify({'error': 'Пользователь существует'})

    @app.route('/logout')
    def logout_handler():
        if 'username' in session: update_online(session['username'], False); session.pop('username')
        return redirect('/')

    @app.route('/chat')
    def chat_handler():
        if 'username' not in session: return redirect('/')
        user = get_user(session['username'])
        theme = user[7] if user else 'light'
        username = session['username']
        
        # Полный HTML интерфейс чата с полными стилями
        return f'''
<!DOCTYPE html>
<html lang="ru" data-theme="{theme}">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0, viewport-fit=cover">
    <meta name="apple-mobile-web-app-capable" content="yes">
    <meta name="apple-mobile-web-app-status-bar-style" content="black-translucent">
    <title>Tandau Chat</title>
    <link rel="stylesheet" href="https://cdnjs.cloudflare.com/ajax/libs/font-awesome/6.5.0/css/all.min.css">
    <style>
        :root {{
            --bg: #f8f9fa;
            --text: #333;
            --input: #fff;
            --border: #ddd;
            --accent: #667eea;
            --sidebar-width: 300px;
            --success: #10b981;
            --warning: #f59e0b;
            --error: #ef4444;
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
            -webkit-tap-highlight-color: transparent;
        }}
        
        body {{
            font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif;
            background: var(--bg);
            color: var(--text);
            height: 100vh;
            overflow: hidden;
        }}
        
        .app-container {{
            display: flex;
            height: 100vh;
            position: relative;
        }}
        
        /* Сайдбар */
        .sidebar {{
            width: var(--sidebar-width);
            background: var(--input);
            border-right: 1px solid var(--border);
            display: flex;
            flex-direction: column;
            transition: transform 0.3s ease;
        }}
        
        .sidebar-header {{
            padding: 20px;
            background: var(--accent);
            color: white;
            text-align: center;
            font-weight: 700;
            font-size: 1.2rem;
        }}
        
        .user-info {{
            padding: 15px;
            display: flex;
            gap: 12px;
            align-items: center;
            border-bottom: 1px solid var(--border);
        }}
        
        .avatar {{
            width: 44px;
            height: 44px;
            border-radius: 50%;
            background: var(--accent);
            color: white;
            display: flex;
            align-items: center;
            justify-content: center;
            font-weight: bold;
            font-size: 1.1rem;
            flex-shrink: 0;
        }}
        
        .user-details {{
            flex: 1;
            min-width: 0;
        }}
        
        .user-details strong {{
            display: block;
            font-size: 1rem;
            margin-bottom: 4px;
        }}
        
        .user-status {{
            font-size: 0.85rem;
            opacity: 0.8;
        }}
        
        .nav {{
            flex: 1;
            overflow-y: auto;
            padding: 10px;
        }}
        
        .nav-title {{
            padding: 12px 15px;
            font-size: 0.8rem;
            color: #666;
            text-transform: uppercase;
            font-weight: 600;
            letter-spacing: 0.5px;
            display: flex;
            justify-content: space-between;
            align-items: center;
        }}
        
        [data-theme="dark"] .nav-title {{
            color: #999;
        }}
        
        .nav-item {{
            padding: 12px 15px;
            cursor: pointer;
            border-radius: 10px;
            margin: 4px 0;
            transition: all 0.2s ease;
            display: flex;
            align-items: center;
            gap: 10px;
            font-size: 0.95rem;
        }}
        
        .nav-item:hover {{
            background: #f0f0f0;
        }}
        
        [data-theme="dark"] .nav-item:hover {{
            background: #333;
        }}
        
        .nav-item.active {{
            background: var(--accent);
            color: white;
        }}
        
        .nav-item i {{
            width: 20px;
            text-align: center;
        }}
        
        .add-btn {{
            background: none;
            border: none;
            color: inherit;
            cursor: pointer;
            padding: 4px;
            border-radius: 4px;
        }}
        
        .add-btn:hover {{
            background: rgba(255,255,255,0.1);
        }}
        
        /* Область чата */
        .chat-area {{
            flex: 1;
            display: flex;
            flex-direction: column;
            position: relative;
        }}
        
        .chat-header {{
            padding: 15px 20px;
            background: var(--input);
            border-bottom: 1px solid var(--border);
            font-weight: 600;
            display: flex;
            align-items: center;
            gap: 10px;
        }}
        
        .back-button {{
            display: none;
            background: none;
            border: none;
            font-size: 1.2rem;
            color: var(--text);
            cursor: pointer;
        }}
        
        .channel-actions {{
            margin-left: auto;
            display: flex;
            gap: 10px;
        }}
        
        .channel-btn {{
            background: none;
            border: none;
            color: var(--text);
            cursor: pointer;
            padding: 5px;
            border-radius: 4px;
        }}
        
        .channel-btn:hover {{
            background: rgba(0,0,0,0.1);
        }}
        
        [data-theme="dark"] .channel-btn:hover {{
            background: rgba(255,255,255,0.1);
        }}
        
        .messages {{
            flex: 1;
            padding: 15px;
            overflow-y: auto;
            display: flex;
            flex-direction: column;
        }}
        
        .msg {{
            margin: 8px 0;
            max-width: 85%;
            padding: 12px 16px;
            border-radius: 18px;
            word-wrap: break-word;
            position: relative;
            animation: messageAppear 0.3s ease;
        }}
        
        @keyframes messageAppear {{
            from {{ opacity: 0; transform: translateY(10px); }}
            to {{ opacity: 1; transform: translateY(0); }}
        }}
        
        .msg.own {{
            background: var(--accent);
            color: white;
            margin-left: auto;
            border-bottom-right-radius: 6px;
        }}
        
        .msg.other {{
            background: #e9ecef;
            color: #333;
            border-bottom-left-radius: 6px;
        }}
        
        [data-theme="dark"] .msg.other {{
            background: #333;
            color: #eee;
        }}
        
        .msg-sender {{
            font-weight: 600;
            font-size: 0.9rem;
            margin-bottom: 4px;
        }}
        
        .msg-time {{
            font-size: 0.75rem;
            opacity: 0.7;
            margin-top: 4px;
        }}
        
        .input-area {{
            padding: 15px;
            background: var(--input);
            border-top: 1px solid var(--border);
        }}
        
        .subscribe-notice {{
            text-align: center;
            padding: 20px;
            color: #666;
            font-style: italic;
        }}
        
        [data-theme="dark"] .subscribe-notice {{
            color: #999;
        }}
        
        .input-row {{
            display: flex;
            gap: 10px;
            align-items: flex-end;
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
            max-height: 120px;
            min-height: 44px;
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
            transition: all 0.2s ease;
        }}
        
        .send-btn:hover {{
            transform: scale(1.05);
        }}
        
        .send-btn:active {{
            transform: scale(0.95);
        }}
        
        .send-btn:disabled {{
            background: #ccc;
            cursor: not-allowed;
            transform: none;
        }}
        
        .file-preview {{
            margin: 8px 0;
            max-width: 200px;
            border-radius: 12px;
            overflow: hidden;
        }}
        
        .file-preview img, .file-preview video {{
            width: 100%;
            height: auto;
            display: block;
        }}
        
        .theme-toggle {{
            background: none;
            border: none;
            font-size: 1.2rem;
            color: white;
            cursor: pointer;
            padding: 5px;
        }}
        
        .mobile-menu-btn {{
            display: none;
            background: none;
            border: none;
            font-size: 1.5rem;
            color: var(--text);
            cursor: pointer;
            padding: 10px;
        }}
        
        .logout-btn {{
            margin: 10px;
            padding: 12px;
            background: #dc3545;
            color: white;
            border: none;
            border-radius: 10px;
            cursor: pointer;
            font-weight: 600;
            transition: all 0.2s ease;
        }}
        
        .logout-btn:hover {{
            background: #c82333;
        }}
        
        /* Модальные окна */
        .modal {{
            display: none;
            position: fixed;
            top: 0;
            left: 0;
            width: 100%;
            height: 100%;
            background: rgba(0,0,0,0.5);
            z-index: 2000;
            align-items: center;
            justify-content: center;
        }}
        
        .modal-content {{
            background: var(--input);
            padding: 25px;
            border-radius: 15px;
            width: 90%;
            max-width: 400px;
            max-height: 80vh;
            overflow-y: auto;
        }}
        
        .modal-header {{
            display: flex;
            justify-content: space-between;
            align-items: center;
            margin-bottom: 20px;
        }}
        
        .modal-title {{
            font-size: 1.3rem;
            font-weight: 600;
        }}
        
        .close-modal {{
            background: none;
            border: none;
            font-size: 1.5rem;
            cursor: pointer;
            color: var(--text);
        }}
        
        .form-group {{
            margin-bottom: 15px;
        }}
        
        .form-label {{
            display: block;
            margin-bottom: 5px;
            font-weight: 500;
        }}
        
        .form-control {{
            width: 100%;
            padding: 10px;
            border: 1px solid var(--border);
            border-radius: 8px;
            background: var(--bg);
            color: var(--text);
        }}
        
        .checkbox-group {{
            display: flex;
            align-items: center;
            gap: 10px;
        }}
        
        .btn {{
            padding: 10px 20px;
            border: none;
            border-radius: 8px;
            cursor: pointer;
            font-weight: 600;
            transition: all 0.2s ease;
        }}
        
        .btn-primary {{
            background: var(--accent);
            color: white;
        }}
        
        .btn-secondary {{
            background: #6c757d;
            color: white;
        }}
        
        .user-list {{
            max-height: 200px;
            overflow-y: auto;
            border: 1px solid var(--border);
            border-radius: 8px;
            margin-top: 10px;
        }}
        
        .user-item {{
            padding: 10px;
            border-bottom: 1px solid var(--border);
            cursor: pointer;
            display: flex;
            align-items: center;
            gap: 10px;
        }}
        
        .user-item:last-child {{
            border-bottom: none;
        }}
        
        .user-item:hover {{
            background: var(--bg);
        }}
        
        .invite-item {{
            padding: 15px;
            border: 1px solid var(--border);
            border-radius: 8px;
            margin-bottom: 10px;
        }}
        
        /* Мобильные стили */
        @media (max-width: 768px) {{
            .sidebar {{
                position: absolute;
                top: 0;
                left: 0;
                height: 100%;
                z-index: 1000;
                transform: translateX(-100%);
            }}
            
            .sidebar.active {{
                transform: translateX(0);
            }}
            
            .mobile-menu-btn {{
                display: block;
            }}
            
            .back-button {{
                display: block;
            }}
            
            .chat-header {{
                padding-left: 15px;
            }}
            
            .msg {{
                max-width: 90%;
            }}
            
            .user-details strong {{
                font-size: 0.9rem;
            }}
        }}
        
        @media (max-width: 480px) {{
            .messages {{
                padding: 10px;
            }}
            
            .input-area {{
                padding: 12px;
            }}
            
            .msg-input {{
                font-size: 16px;
            }}
            
            .nav-item {{
                padding: 10px 12px;
                font-size: 0.9rem;
            }}
        }}
        
        .messages::-webkit-scrollbar {{
            width: 6px;
        }}
        
        .messages::-webkit-scrollbar-track {{
            background: transparent;
        }}
        
        .messages::-webkit-scrollbar-thumb {{
            background: #ccc;
            border-radius: 3px;
        }}
        
        [data-theme="dark"] .messages::-webkit-scrollbar-thumb {{
            background: #555;
        }}
        
        @supports (-webkit-touch-callout: none) {{
            body {{
                min-height: -webkit-fill-available;
            }}
        }}
    </style>
</head>
<body>
    <div class="app-container">
        <!-- Сайдбар -->
        <div class="sidebar" id="sidebar">
            <div class="sidebar-header">
                💬 Tandau
            </div>
            <div class="user-info">
                <div class="avatar" id="user-avatar">{username[:2].upper()}</div>
                <div class="user-details">
                    <strong>{username}</strong>
                    <div class="user-status">Online</div>
                </div>
                <button class="theme-toggle" onclick="openThemeModal()" title="Сменить тему">
                    <i class="fas fa-palette"></i>
                </button>
            </div>
            <div class="nav">
                <div class="nav-title">
                    <span>Каналы</span>
                    <button class="add-btn" onclick="openCreateChannelModal()" title="Создать канал">
                        <i class="fas fa-plus"></i>
                    </button>
                </div>
                <div id="channels">
                    <div class="nav-item active" onclick="openRoom('channel_general', 'channel', '# general')">
                        <i class="fas fa-hashtag"></i>
                        <span>general</span>
                    </div>
                </div>
                
                <div class="nav-title">
                    <span>Личные чаты</span>
                </div>
                <div id="personal-chats">
                    <!-- Динамически заполняется -->
                </div>
                
                <div class="nav-title">
                    <span>Пользователи</span>
                </div>
                <div id="users">
                    <!-- Динамически заполняется -->
                </div>
                
                <div class="nav-title">
                    <span>Приглашения</span>
                </div>
                <div id="invites">
                    <!-- Динамически заполняется -->
                </div>
            </div>
            <button class="logout-btn" onclick="location.href='/logout'">
                <i class="fas fa-sign-out-alt"></i> Выйти
            </button>
        </div>
        
        <!-- Область чата -->
        <div class="chat-area">
            <div class="chat-header">
                <button class="back-button" onclick="toggleSidebar()">
                    <i class="fas fa-bars"></i>
                </button>
                <span id="chat-title"># general</span>
                <div class="channel-actions" id="channel-actions" style="display: none;">
                    <button class="channel-btn" onclick="openChannelSettings()" title="Настройки канала">
                        <i class="fas fa-cog"></i>
                    </button>
                    <button class="channel-btn" onclick="openInviteModal()" title="Пригласить пользователя">
                        <i class="fas fa-user-plus"></i>
                    </button>
                </div>
            </div>
            <div class="messages" id="messages">
                <!-- Сообщения загружаются здесь -->
            </div>
            <div class="input-area" id="input-area">
                <div class="input-row">
                    <button onclick="document.getElementById('file').click()" style="background:none;border:none;font-size:1.2rem;cursor:pointer;color:var(--text);padding:5px;">
                        <i class="fas fa-paperclip"></i>
                    </button>
                    <input type="file" id="file" accept="image/*,video/*" style="display:none" onchange="previewFile(this)">
                    <textarea class="msg-input" id="msg-input" placeholder="Написать сообщение..." rows="1" onkeydown="handleKeydown(event)"></textarea>
                    <button class="send-btn" onclick="sendMessage()" id="send-btn">
                        <i class="fas fa-paper-plane"></i>
                    </button>
                </div>
                <div id="file-preview"></div>
            </div>
        </div>
    </div>

    <!-- Модальные окна -->
    <div class="modal" id="theme-modal">
        <div class="modal-content">
            <div class="modal-header">
                <h3 class="modal-title">Выбор темы</h3>
                <button class="close-modal" onclick="closeThemeModal()">&times;</button>
            </div>
            <div class="form-group">
                <div class="user-item" onclick="setTheme('light')">
                    <i class="fas fa-sun"></i>
                    <span>Светлая</span>
                </div>
                <div class="user-item" onclick="setTheme('dark')">
                    <i class="fas fa-moon"></i>
                    <span>Темная</span>
                </div>
                <div class="user-item" onclick="setTheme('auto')">
                    <i class="fas fa-desktop"></i>
                    <span>Авто</span>
                </div>
            </div>
        </div>
    </div>

    <div class="modal" id="create-channel-modal">
        <div class="modal-content">
            <div class="modal-header">
                <h3 class="modal-title">Создать канал</h3>
                <button class="close-modal" onclick="closeCreateChannelModal()">&times;</button>
            </div>
            <div class="form-group">
                <label class="form-label">Название канала</label>
                <input type="text" class="form-control" id="channel-name" placeholder="название-канала">
            </div>
            <div class="form-group">
                <label class="form-label">Описание</label>
                <input type="text" class="form-control" id="channel-description" placeholder="Описание канала">
            </div>
            <div class="form-group">
                <div class="checkbox-group">
                    <input type="checkbox" id="channel-private">
                    <label class="form-label" for="channel-private">Приватный канал</label>
                </div>
            </div>
            <button class="btn btn-primary" onclick="createChannel()">Создать канал</button>
        </div>
    </div>

    <div class="modal" id="invite-modal">
        <div class="modal-content">
            <div class="modal-header">
                <h3 class="modal-title">Пригласить в канал</h3>
                <button class="close-modal" onclick="closeInviteModal()">&times;</button>
            </div>
            <div class="form-group">
                <label class="form-label">Выберите пользователя</label>
                <div class="user-list" id="invite-user-list">
                    <!-- Список пользователей -->
                </div>
            </div>
        </div>
    </div>

    <div class="modal" id="channel-settings-modal">
        <div class="modal-content">
            <div class="modal-header">
                <h3 class="modal-title">Настройки канала</h3>
                <button class="close-modal" onclick="closeChannelSettingsModal()">&times;</button>
            </div>
            <div class="form-group">
                <div class="checkbox-group">
                    <input type="checkbox" id="allow-messages">
                    <label class="form-label" for="allow-messages">Разрешить сообщения</label>
                </div>
            </div>
            <button class="btn btn-primary" onclick="updateChannelSettings()">Сохранить</button>
        </div>
    </div>

    <script src="https://cdnjs.cloudflare.com/ajax/libs/socket.io/4.7.2/socket.io.js"></script>
    <script>
        const socket = io();
        const user = "{username}";
        let room = "channel_general";
        let roomType = "channel";
        let currentChannel = "general";
        let isMobile = window.innerWidth <= 768;
        let channelSettings = {{}};

        socket.emit('join', {{ room: 'channel_general' }});

        // Адаптивный textarea
        const msgInput = document.getElementById('msg-input');
        msgInput.addEventListener('input', function() {{
            this.style.height = 'auto';
            this.style.height = (this.scrollHeight) + 'px';
        }});

        function handleKeydown(e) {{
            if (e.key === 'Enter' && !e.shiftKey) {{
                e.preventDefault();
                sendMessage();
            }}
        }}

        function toggleSidebar() {{
            const sidebar = document.getElementById('sidebar');
            sidebar.classList.toggle('active');
        }}

        // Функции модальных окон
        function openThemeModal() {{
            document.getElementById('theme-modal').style.display = 'flex';
        }}

        function closeThemeModal() {{
            document.getElementById('theme-modal').style.display = 'none';
        }}

        function openCreateChannelModal() {{
            document.getElementById('create-channel-modal').style.display = 'flex';
        }}

        function closeCreateChannelModal() {{
            document.getElementById('create-channel-modal').style.display = 'none';
        }}

        function openInviteModal() {{
            loadUsersForInvite();
            document.getElementById('invite-modal').style.display = 'flex';
        }}

        function closeInviteModal() {{
            document.getElementById('invite-modal').style.display = 'none';
        }}

        function openChannelSettings() {{
            document.getElementById('channel-settings-modal').style.display = 'flex';
            // Загружаем текущие настройки
            document.getElementById('allow-messages').checked = channelSettings.allow_messages !== false;
        }}

        function closeChannelSettingsModal() {{
            document.getElementById('channel-settings-modal').style.display = 'none';
        }}

        function setTheme(theme) {{
            fetch('/set_theme', {{
                method: 'POST',
                headers: {{ 'Content-Type': 'application/json' }},
                body: JSON.stringify({{ theme: theme }})
            }});
            document.documentElement.setAttribute('data-theme', theme);
            closeThemeModal();
        }}

        function createChannel() {{
            const name = document.getElementById('channel-name').value;
            const description = document.getElementById('channel-description').value;
            const isPrivate = document.getElementById('channel-private').checked;

            if (!name) {{
                alert('Введите название канала');
                return;
            }}

            fetch('/create_channel', {{
                method: 'POST',
                headers: {{ 'Content-Type': 'application/json' }},
                body: JSON.stringify({{ 
                    name: name,
                    description: description,
                    is_private: isPrivate
                }})
            }})
            .then(r => r.json())
            .then(data => {{
                if (data.success) {{
                    closeCreateChannelModal();
                    loadUserChannels();
                    openRoom('channel_' + data.channel_name, 'channel', '# ' + data.channel_name);
                }} else {{
                    alert(data.error);
                }}
            }});
        }}

        function loadUsersForInvite() {{
            fetch('/users')
                .then(r => r.json())
                .then(users => {{
                    const userList = document.getElementById('invite-user-list');
                    userList.innerHTML = '';
                    
                    users.forEach(u => {{
                        if (u.username !== user) {{
                            const item = document.createElement('div');
                            item.className = 'user-item';
                            item.innerHTML = `
                                <i class="fas fa-user"></i>
                                <span>${{u.username}}</span>
                            `;
                            item.onclick = () => inviteUser(u.username);
                            userList.appendChild(item);
                        }}
                    }});
                }});
        }}

        function inviteUser(username) {{
            fetch('/invite_to_channel', {{
                method: 'POST',
                headers: {{ 'Content-Type': 'application/json' }},
                body: JSON.stringify({{ 
                    channel_name: currentChannel,
                    username: username
                }})
            }})
            .then(r => r.json())
            .then(data => {{
                if (data.success) {{
                    alert('Приглашение отправлено!');
                    closeInviteModal();
                }} else {{
                    alert(data.error);
                }}
            }});
        }}

        function updateChannelSettings() {{
            const allowMessages = document.getElementById('allow-messages').checked;
            
            fetch('/update_channel_settings', {{
                method: 'POST',
                headers: {{ 'Content-Type': 'application/json' }},
                body: JSON.stringify({{ 
                    channel_name: currentChannel,
                    allow_messages: allowMessages
                }})
            }})
            .then(r => r.json())
            .then(data => {{
                if (data.success) {{
                    channelSettings.allow_messages = allowMessages;
                    updateInputArea();
                    closeChannelSettingsModal();
                }} else {{
                    alert(data.error);
                }}
            }});
        }}

        function sendMessage() {{
            if (!canSendMessages()) return;
            
            const input = document.getElementById('msg-input');
            const msg = input.value.trim();
            const file = document.getElementById('file').files[0];
            
            if (!msg && !file) return;
            
            const data = {{ message: msg, room: room, type: roomType }};
            
            if (file) {{
                const reader = new FileReader();
                reader.onload = (e) => {{
                    data.file = e.target.result;
                    data.fileType = file.type.startsWith('image/') ? 'image' : 'video';
                    socket.emit('message', data);
                    resetInput();
                }};
                reader.readAsDataURL(file);
            }} else {{
                socket.emit('message', data);
                resetInput();
            }}
        }}

        function canSendMessages() {{
            if (roomType === 'channel' && channelSettings.allow_messages === false) {{
                return false;
            }}
            return true;
        }}

        function updateInputArea() {{
            const inputArea = document.getElementById('input-area');
            const sendBtn = document.getElementById('send-btn');
            
            if (roomType === 'channel' && channelSettings.allow_messages === false) {{
                inputArea.innerHTML = '<div class="subscribe-notice">В этом канале можно только читать сообщения</div>';
            }} else {{
                inputArea.innerHTML = `
                    <div class="input-row">
                        <button onclick="document.getElementById('file').click()" style="background:none;border:none;font-size:1.2rem;cursor:pointer;color:var(--text);padding:5px;">
                            <i class="fas fa-paperclip"></i>
                        </button>
                        <input type="file" id="file" accept="image/*,video/*" style="display:none" onchange="previewFile(this)">
                        <textarea class="msg-input" id="msg-input" placeholder="Написать сообщение..." rows="1" onkeydown="handleKeydown(event)"></textarea>
                        <button class="send-btn" onclick="sendMessage()" id="send-btn">
                            <i class="fas fa-paper-plane"></i>
                        </button>
                    </div>
                    <div id="file-preview"></div>
                `;
                
                // Re-initialize the textarea
                const newMsgInput = document.getElementById('msg-input');
                newMsgInput.addEventListener('input', function() {{
                    this.style.height = 'auto';
                    this.style.height = (this.scrollHeight) + 'px';
                }});
            }}
        }}

        function resetInput() {{
            const input = document.getElementById('msg-input');
            const fileInput = document.getElementById('file');
            const preview = document.getElementById('file-preview');
            
            if (input) input.value = '';
            if (input) input.style.height = 'auto';
            if (fileInput) fileInput.value = '';
            if (preview) preview.innerHTML = '';
        }}

        function previewFile(input) {{
            const file = input.files[0];
            if (!file) return;
            
            const reader = new FileReader();
            reader.onload = (e) => {{
                const prev = document.getElementById('file-preview');
                prev.innerHTML = '';
                
                if (file.type.startsWith('image/')) {{
                    const img = document.createElement('img');
                    img.src = e.target.result;
                    img.className = 'file-preview';
                    prev.appendChild(img);
                }} else {{
                    const vid = document.createElement('video');
                    vid.src = e.target.result;
                    vid.controls = true;
                    vid.className = 'file-preview';
                    prev.appendChild(vid);
                }}
            }};
            reader.readAsDataURL(file);
        }}

        socket.on('message', (data) => {{
            if (data.room === room) {{
                addMessageToChat(data);
            }}
        }});

        function addMessageToChat(data) {{
            const messagesContainer = document.getElementById('messages');
            const msg = document.createElement('div');
            msg.className = `msg ${{data.user === user ? 'own' : 'other'}}`;
            
            let content = `
                <div class="msg-sender">${{data.user}}</div>
                ${{data.message ? data.message.replace(/\\n/g, '<br>') : ''}}
            `;
            
            if (data.file) {{
                if (data.fileType === 'image') {{
                    content += `<img src="${{data.file}}" class="file-preview">`;
                }} else {{
                    content += `<video src="${{data.file}}" controls class="file-preview"></video>`;
                }}
            }}
            
            content += `<div class="msg-time">${{data.timestamp || ''}}</div>`;
            msg.innerHTML = content;
            messagesContainer.appendChild(msg);
            messagesContainer.scrollTop = messagesContainer.scrollHeight;
        }}

        function openRoom(r, t, title) {{
            room = r;
            roomType = t;
            currentChannel = t === 'channel' ? r.replace('channel_', '') : '';
            
            document.getElementById('chat-title').textContent = title;
            document.querySelectorAll('.nav-item').forEach(el => el.classList.remove('active'));
            event.currentTarget.classList.add('active');
            
            document.getElementById('messages').innerHTML = '';
            
            // Показываем/скрываем кнопки управления каналом
            const channelActions = document.getElementById('channel-actions');
            if (t === 'channel') {{
                channelActions.style.display = 'flex';
                // Загружаем настройки канала
                fetch(`/channel_info/${{currentChannel}}`)
                    .then(r => r.json())
                    .then(info => {{
                        if (!info.error) {{
                            channelSettings = info;
                            updateInputArea();
                        }}
                    }});
            }} else {{
                channelActions.style.display = 'none';
                updateInputArea();
            }}
            
            // Загружаем историю
            fetch(`/get_messages/${{r}}`)
                .then(r => r.json())
                .then(messages => {{
                    messages.forEach(msg => {{
                        addMessageToChat(msg);
                    }});
                }});
            
            socket.emit('join', {{ room: r }});
            
            // На мобильных закрываем сайдбар после выбора чата
            if (isMobile) {{
                toggleSidebar();
            }}
        }}

        function loadUserChannels() {{
            fetch('/user_channels')
                .then(r => r.json())
                .then(channels => {{
                    const channelsContainer = document.getElementById('channels');
                    // Сохраняем general канал
                    const generalChannel = channelsContainer.querySelector('.nav-item');
                    channelsContainer.innerHTML = '';
                    channelsContainer.appendChild(generalChannel);
                    
                    channels.forEach(channel => {{
                        const el = document.createElement('div');
                        el.className = 'nav-item';
                        el.innerHTML = `
                            <i class="fas fa-hashtag"></i>
                            <span>${{channel.name}}</span>
                            ${{channel.is_private ? ' <i class="fas fa-lock" style="font-size: 0.8em;"></i>' : ''}}
                        `;
                        el.onclick = () => openRoom('channel_' + channel.name, 'channel', '# ' + channel.name);
                        channelsContainer.appendChild(el);
                    }});
                }});
        }}

        function loadPersonalChats() {{
            fetch('/personal_chats')
                .then(r => r.json())
                .then(chats => {{
                    const pc = document.getElementById('personal-chats');
                    pc.innerHTML = '';
                    
                    chats.forEach(chatUser => {{
                        const el = document.createElement('div');
                        el.className = 'nav-item';
                        el.innerHTML = `
                            <i class="fas fa-user"></i>
                            <span>@${{chatUser}}</span>
                        `;
                        el.onclick = () => openRoom(
                            `private_${{Math.min(user, chatUser)}}_${{Math.max(user, chatUser)}}`,
                            'private',
                            `@${{chatUser}}`
                        );
                        pc.appendChild(el);
                    }});
                }});
        }}

        function loadInvites() {{
            fetch('/channel_invites')
                .then(r => r.json())
                .then(invites => {{
                    const invitesContainer = document.getElementById('invites');
                    invitesContainer.innerHTML = '';
                    
                    invites.forEach(invite => {{
                        const el = document.createElement('div');
                        el.className = 'nav-item';
                        el.innerHTML = `
                            <i class="fas fa-envelope"></i>
                            <span># ${{invite.channel_name}}</span>
                        `;
                        el.onclick = () => acceptInvite(invite.id);
                        invitesContainer.appendChild(el);
                    }});
                }});
        }}

        function acceptInvite(inviteId) {{
            fetch('/accept_invite', {{
                method: 'POST',
                headers: {{ 'Content-Type': 'application/json' }},
                body: JSON.stringify({{ invite_id: inviteId }})
            }})
            .then(r => r.json())
            .then(data => {{
                if (data.success) {{
                    loadUserChannels();
                    loadInvites();
                    openRoom('channel_' + data.channel_name, 'channel', '# ' + data.channel_name);
                }} else {{
                    alert(data.error);
                }}
            }});
        }}

        // Обновление списка пользователей
        function loadUsers() {{
            fetch('/users')
                .then(r => r.json())
                .then(users => {{
                    const usersContainer = document.getElementById('users');
                    usersContainer.innerHTML = '';
                    
                    users.forEach(u => {{
                        const el = document.createElement('div');
                        el.className = 'nav-item';
                        el.innerHTML = `
                            <i class="fas fa-user${{u.online ? '-check' : ''}}"></i>
                            <span>${{u.username}}${{u.online ? ' (онлайн)' : ''}}</span>
                        `;
                        el.onclick = () => openRoom(
                            `private_${{Math.min(user, u.username)}}_${{Math.max(user, u.username)}}`,
                            'private',
                            `@${{u.username}}`
                        );
                        usersContainer.appendChild(el);
                    }});
                }});
        }}

        // Инициализация
        loadUserChannels();
        loadPersonalChats();
        loadUsers();
        loadInvites();
        
        // Периодическое обновление
        setInterval(() => {{
            loadUsers();
            loadInvites();
        }}, 10000);
        
        // Адаптация к изменению размера окна
        window.addEventListener('resize', () => {{
            isMobile = window.innerWidth <= 768;
            if (!isMobile) {{
                document.getElementById('sidebar').classList.remove('active');
            }}
        }});
    </script>
</body>
</html>'''

    @app.route('/users')
    def users_handler():
        return jsonify(get_all_users())

    @app.route('/online_users')
    def online_users_handler():
        return jsonify(get_online_users())

    @app.route('/get_messages/<room>')
    def get_messages_handler(room):
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
                    emit('error', {'message': 'В этом канале запрещено отправлять сообщения'}, room=request.sid)
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

    # Health check для Render
    @app.route('/health')
    def health_check():
        return jsonify({'status': 'healthy', 'service': 'Tandau Messenger'})

    # Обработка 404
    @app.errorhandler(404)
    def not_found(e):
        return redirect('/')

    return app

# === СОЗДАНИЕ ПРИЛОЖЕНИЯ ДЛЯ RENDER ===
app = create_app()
socketio = app.extensions['socketio']

if __name__ == '__main__':
    port = int(os.environ.get('PORT', 5000))
    socketio.run(app, host='0.0.0.0', port=port, debug=False)
