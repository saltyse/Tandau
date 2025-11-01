# web_messenger.py - Tandau Messenger (полная рабочая версия)
from flask import Flask, request, jsonify, session, redirect, send_from_directory
from flask_socketio import SocketIO, emit, join_room, leave_room
import sqlite3
from datetime import datetime
from werkzeug.security import generate_password_hash, check_password_hash
from werkzeug.utils import secure_filename
import random
import os
import re
import base64

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
                    file_path TEXT,
                    file_name TEXT
                )
            ''')
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
            c.execute('INSERT OR IGNORE INTO channels (name, display_name, description, created_by) VALUES (?, ?, ?, ?)',
                      ('general', 'General', 'Общий канал', 'system'))
            conn.commit()

    init_db()

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
        file.save(path)
        return f'/static/{os.path.basename(folder)}/{filename}', filename

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

    def save_message(user, msg, room, recipient=None, msg_type='text', file_path=None, file_name=None):
        with sqlite3.connect('messenger.db') as conn:
            c = conn.cursor()
            c.execute('INSERT INTO messages (username, message, room, recipient, message_type, file_path, file_name) VALUES (?, ?, ?, ?, ?, ?, ?)',
                      (user, msg, room, recipient, msg_type, file_path, file_name))
            conn.commit(); return c.lastrowid

    def get_messages_for_room(room):
        with sqlite3.connect('messenger.db') as conn:
            c = conn.cursor()
            c.execute('''
                SELECT username, message, message_type, file_path, file_name, timestamp 
                FROM messages 
                WHERE room = ? 
                ORDER BY timestamp ASC
            ''', (room,))
            return [{
                'user': row[0],
                'message': row[1],
                'type': row[2],
                'file': row[3],
                'file_name': row[4],
                'timestamp': row[5][11:16] if row[5] else ''
            } for row in c.fetchall()]

    def get_user_personal_chats(username):
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

    def create_channel(name, display_name, description, created_by, is_private=False):
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

    def rename_channel(channel_name, new_display_name, username):
        with sqlite3.connect('messenger.db') as conn:
            c = conn.cursor()
            try:
                c.execute('''
                    SELECT 1 FROM channels c 
                    JOIN channel_members cm ON c.id = cm.channel_id 
                    WHERE c.name = ? AND cm.username = ? AND cm.is_admin = 1
                ''', (channel_name, username))
                
                if c.fetchone():
                    c.execute('UPDATE channels SET display_name = ? WHERE name = ?', (new_display_name, channel_name))
                    conn.commit()
                    return True
                return False
            except:
                return False

    def get_channel_info(channel_name):
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

    def get_user_channels(username):
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

    # === API Routes ===
    @app.route('/upload_avatar', methods=['POST'])
    def upload_avatar_handler():
        if 'username' not in session: 
            return jsonify({'error': 'auth'})
        
        if 'avatar' in request.files:
            file = request.files['avatar']
            path, filename = save_uploaded_file(file, app.config['AVATAR_FOLDER'])
        else:
            return jsonify({'error': 'no file'})
        
        if path:
            with sqlite3.connect('messenger.db') as conn:
                c = conn.cursor()
                c.execute('UPDATE users SET avatar_path = ? WHERE username = ?', (path, session['username']))
                conn.commit()
            return jsonify({'success': True, 'path': path})
        return jsonify({'error': 'invalid'})

    @app.route('/delete_avatar', methods=['POST'])
    def delete_avatar_handler():
        if 'username' not in session: 
            return jsonify({'error': 'auth'})
        with sqlite3.connect('messenger.db') as conn:
            c = conn.cursor()
            c.execute('UPDATE users SET avatar_path = NULL WHERE username = ?', (session['username'],))
            conn.commit()
        return jsonify({'success': True})

    @app.route('/set_theme', methods=['POST'])
    def set_theme_handler():
        if 'username' not in session: 
            return jsonify({'error': 'auth'})
        theme = request.json.get('theme', 'light')
        if theme not in ['light', 'dark', 'auto']: 
            return jsonify({'error': 'invalid'})
        with sqlite3.connect('messenger.db') as conn:
            c = conn.cursor()
            c.execute('UPDATE users SET theme = ? WHERE username = ?', (theme, session['username']))
            conn.commit()
        return jsonify({'success': True})

    @app.route('/create_channel', methods=['POST'])
    def create_channel_handler():
        if 'username' not in session: 
            return jsonify({'error': 'auth'})
        
        name = request.json.get('name', '').strip()
        display_name = request.json.get('display_name', '').strip()
        description = request.json.get('description', '').strip()
        is_private = request.json.get('is_private', False)
        
        if not name or len(name) < 2:
            return jsonify({'error': 'Название канала должно быть не менее 2 символов'})
        
        if not display_name:
            display_name = name.capitalize()
        
        channel_id = create_channel(name, display_name, description, session['username'], is_private)
        if channel_id:
            return jsonify({'success': True, 'channel_name': name, 'display_name': display_name})
        return jsonify({'error': 'Канал с таким названием уже существует'})

    @app.route('/rename_channel', methods=['POST'])
    def rename_channel_handler():
        if 'username' not in session: 
            return jsonify({'error': 'auth'})
        
        channel_name = request.json.get('channel_name')
        new_display_name = request.json.get('new_display_name', '').strip()
        
        if not new_display_name:
            return jsonify({'error': 'Новое название не может быть пустым'})
        
        if rename_channel(channel_name, new_display_name, session['username']):
            return jsonify({'success': True})
        return jsonify({'error': 'Не удалось переименовать канал или нет прав'})

    @app.route('/channel_info/<channel_name>')
    def channel_info_handler(channel_name):
        if 'username' not in session: 
            return jsonify({'error': 'auth'})
        info = get_channel_info(channel_name)
        if info:
            info['is_member'] = is_channel_member(channel_name, session['username'])
            info['members'] = get_channel_members(channel_name)
            return jsonify(info)
        return jsonify({'error': 'Канал не найден'})

    @app.route('/user_channels')
    def user_channels_handler():
        if 'username' not in session: 
            return jsonify({'error': 'auth'})
        return jsonify(get_user_channels(session['username']))

    @app.route('/personal_chats')
    def personal_chats_handler():
        if 'username' not in session: 
            return jsonify({'error': 'auth'})
        return jsonify(get_user_personal_chats(session['username']))

    @app.route('/user_info/<username>')
    def user_info_handler(username):
        if 'username' not in session: 
            return jsonify({'error': 'auth'})
        
        user = get_user(username)
        if user:
            return jsonify({
                'username': user[1],
                'online': user[4],
                'avatar_color': user[5],
                'avatar_path': user[6],
                'theme': user[7]
            })
        return jsonify({'error': 'Пользователь не найден'})

    # Статические файлы
    @app.route('/static/<path:filename>')
    def static_files(filename):
        return send_from_directory('static', filename)

    # === Основные маршруты ===
    @app.route('/')
    def index():
        if 'username' in session: 
            return redirect('/chat')
        return '''
        <!DOCTYPE html>
        <html lang="ru">
        <head>
            <meta charset="UTF-8">
            <meta name="viewport" content="width=device-width, initial-scale=1.0">
            <title>Tandau - Вход</title>
            <style>
                * { margin: 0; padding: 0; box-sizing: border-box; }
                body { 
                    font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif;
                    background: linear-gradient(135deg, #667eea, #764ba2);
                    min-height: 100vh;
                    display: flex;
                    justify-content: center;
                    align-items: center;
                    padding: 20px;
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
                .auth-box {
                    background: white;
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
                }
                .form-input:focus {
                    outline: none;
                    border-color: #667eea;
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
                }
                .btn-primary {
                    background: #667eea;
                    color: white;
                }
                .btn-primary:hover {
                    background: #5a6fd8;
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
                        <button type="button" class="btn btn-primary" onclick="login()">Войти</button>
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
        if user: 
            session['username'] = u
            update_online(u, True)
            return jsonify({'success': True})
        return jsonify({'error': 'Неверные данные'})

    @app.route('/register', methods=['POST'])
    def register_handler():
        u, p = request.form.get('username'), request.form.get('password')
        if not u or not p or len(u)<3 or len(p)<4: 
            return jsonify({'error': 'Некорректные данные'})
        if create_user(u, p): 
            return jsonify({'success': True})
        return jsonify({'error': 'Пользователь существует'})

    @app.route('/logout')
    def logout_handler():
        if 'username' in session: 
            update_online(session['username'], False)
            session.pop('username')
        return redirect('/')

    @app.route('/chat')
    def chat_handler():
        if 'username' not in session: 
            return redirect('/')
        user = get_user(session['username'])
        theme = user[7] if user else 'light'
        username = session['username']
        
        return f'''
<!DOCTYPE html>
<html lang="ru" data-theme="{theme}">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
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
            font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif;
            background: var(--bg);
            color: var(--text);
            height: 100vh;
            overflow: hidden;
        }}
        
        .app-container {{
            display: flex;
            height: 100vh;
        }}
        
        /* Сайдбар */
        .sidebar {{
            width: var(--sidebar-width);
            background: var(--input);
            border-right: 1px solid var(--border);
            display: flex;
            flex-direction: column;
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
            background-size: cover;
            background-position: center;
            cursor: pointer;
        }}
        
        .user-details {{
            flex: 1;
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
        
        /* Область чата */
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
            display: flex;
            align-items: center;
            gap: 10px;
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
        
        .msg-header {{
            display: flex;
            align-items: center;
            gap: 8px;
            margin-bottom: 4px;
        }}
        
        .msg-avatar {{
            width: 24px;
            height: 24px;
            border-radius: 50%;
            background: var(--accent);
            font-size: 0.7rem;
            display: flex;
            align-items: center;
            justify-content: center;
            color: white;
            background-size: cover;
            background-position: center;
        }}
        
        .msg-sender {{
            font-weight: 600;
            font-size: 0.9rem;
        }}
        
        .msg-time {{
            font-size: 0.75rem;
            opacity: 0.7;
            margin-top: 4px;
        }}
        
        .file-preview {{
            margin: 8px 0;
            max-width: 300px;
            border-radius: 12px;
            overflow: hidden;
        }}
        
        .file-preview img, .file-preview video {{
            width: 100%;
            height: auto;
            display: block;
            cursor: pointer;
        }}
        
        .input-area {{
            padding: 15px;
            background: var(--input);
            border-top: 1px solid var(--border);
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
        }}
        
        .form-group {{
            margin-bottom: 15px;
        }}
        
        .form-control {{
            width: 100%;
            padding: 10px;
            border: 1px solid var(--border);
            border-radius: 8px;
            background: var(--bg);
            color: var(--text);
        }}
        
        .avatar-upload {{
            text-align: center;
            margin: 20px 0;
        }}
        
        .avatar-preview {{
            width: 100px;
            height: 100px;
            border-radius: 50%;
            margin: 0 auto 15px;
            background: var(--accent);
            background-size: cover;
            background-position: center;
            cursor: pointer;
        }}
        
        .theme-btn {{
            padding: 10px 20px;
            margin: 5px;
            border: none;
            border-radius: 8px;
            background: var(--accent);
            color: white;
            cursor: pointer;
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
        }}
    </style>
</head>
<body>
    <div class="app-container">
        <!-- Сайдбар -->
        <div class="sidebar">
            <div class="sidebar-header">
                💬 Tandau
            </div>
            <div class="user-info">
                <div class="avatar" id="user-avatar" onclick="openAvatarModal()"></div>
                <div class="user-details">
                    <strong>{username}</strong>
                    <div class="user-status">Online</div>
                </div>
                <button class="channel-btn" onclick="openThemeModal()" title="Сменить тему">
                    <i class="fas fa-palette"></i>
                </button>
            </div>
            <div class="nav">
                <div class="nav-title">
                    <span>Каналы</span>
                    <button class="add-btn" onclick="openCreateChannelModal()">
                        <i class="fas fa-plus"></i>
                    </button>
                </div>
                <div id="channels">
                    <div class="nav-item active" onclick="openRoom('channel_general', 'channel', 'General')">
                        <i class="fas fa-hashtag"></i>
                        <span>General</span>
                    </div>
                </div>
                
                <div class="nav-title">
                    <span>Личные чаты</span>
                </div>
                <div id="personal-chats"></div>
                
                <div class="nav-title">
                    <span>Пользователи</span>
                </div>
                <div id="users"></div>
            </div>
            <button class="logout-btn" onclick="location.href='/logout'">
                <i class="fas fa-sign-out-alt"></i> Выйти
            </button>
        </div>
        
        <!-- Область чата -->
        <div class="chat-area">
            <div class="chat-header">
                <span id="chat-title"># General</span>
                <div class="channel-actions" id="channel-actions" style="display: none;">
                    <button class="channel-btn" onclick="openChannelSettings()">
                        <i class="fas fa-cog"></i>
                    </button>
                    <button class="channel-btn" onclick="openRenameModal()">
                        <i class="fas fa-edit"></i>
                    </button>
                </div>
            </div>
            <div class="messages" id="messages"></div>
            <div class="input-area">
                <div class="input-row">
                    <button onclick="document.getElementById('file-input').click()" style="background:none;border:none;font-size:1.2rem;cursor:pointer;color:var(--text);">
                        <i class="fas fa-paperclip"></i>
                    </button>
                    <input type="file" id="file-input" accept="image/*,video/*" style="display:none" onchange="handleFileSelect(this)">
                    <textarea class="msg-input" id="msg-input" placeholder="Написать сообщение..." rows="1" onkeydown="handleKeydown(event)"></textarea>
                    <button class="send-btn" onclick="sendMessage()">
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
            <h3>Выбор темы</h3>
            <div class="form-group">
                <button class="theme-btn" onclick="setTheme('light')">🌞 Светлая</button>
                <button class="theme-btn" onclick="setTheme('dark')">🌙 Темная</button>
                <button class="theme-btn" onclick="setTheme('auto')">⚙️ Авто</button>
            </div>
            <button class="btn btn-primary" onclick="closeThemeModal()">Закрыть</button>
        </div>
    </div>

    <div class="modal" id="avatar-modal">
        <div class="modal-content">
            <h3>Смена аватарки</h3>
            <div class="avatar-upload">
                <div class="avatar-preview" id="avatar-preview" onclick="document.getElementById('avatar-input').click()"></div>
                <input type="file" id="avatar-input" accept="image/*" style="display:none" onchange="previewAvatar(this)">
                <div style="display: flex; gap: 10px; justify-content: center; margin-top: 15px;">
                    <button class="btn btn-primary" onclick="uploadAvatar()">Загрузить</button>
                    <button class="btn" onclick="removeAvatar()">Удалить</button>
                </div>
            </div>
            <button class="btn" onclick="closeAvatarModal()">Закрыть</button>
        </div>
    </div>

    <div class="modal" id="create-channel-modal">
        <div class="modal-content">
            <h3>Создать канал</h3>
            <div class="form-group">
                <input type="text" class="form-control" id="channel-name" placeholder="Идентификатор канала">
                <input type="text" class="form-control" id="channel-display-name" placeholder="Отображаемое название">
                <input type="text" class="form-control" id="channel-description" placeholder="Описание">
                <label><input type="checkbox" id="channel-private"> Приватный канал</label>
            </div>
            <div style="display: flex; gap: 10px;">
                <button class="btn btn-primary" onclick="createChannel()">Создать</button>
                <button class="btn" onclick="closeCreateChannelModal()">Отмена</button>
            </div>
        </div>
    </div>

    <div class="modal" id="rename-modal">
        <div class="modal-content">
            <h3>Переименовать канал</h3>
            <div class="form-group">
                <input type="text" class="form-control" id="channel-rename-input" placeholder="Новое название">
            </div>
            <div style="display: flex; gap: 10px;">
                <button class="btn btn-primary" onclick="renameChannel()">Переименовать</button>
                <button class="btn" onclick="closeRenameModal()">Отмена</button>
            </div>
        </div>
    </div>

    <script src="https://cdnjs.cloudflare.com/ajax/libs/socket.io/4.7.2/socket.io.js"></script>
    <script>
        const socket = io();
        const user = "{username}";
        let room = "channel_general";
        let roomType = "channel";
        let currentChannel = "general";

        // Загрузка аватарки пользователя
        function loadUserAvatar() {{
            fetch('/user_info/' + user)
                .then(r => r.json())
                .then(userInfo => {{
                    const avatar = document.getElementById('user-avatar');
                    if (userInfo.avatar_path) {{
                        avatar.style.backgroundImage = `url(${{userInfo.avatar_path}})`;
                        avatar.textContent = '';
                    }} else {{
                        avatar.style.backgroundImage = 'none';
                        avatar.style.backgroundColor = userInfo.avatar_color;
                        avatar.textContent = user.slice(0, 2).toUpperCase();
                    }}
                }});
        }}

        // Загрузка аватарки другого пользователя
        function getUserAvatar(username, color, callback) {{
            fetch('/user_info/' + username)
                .then(r => r.json())
                .then(userInfo => {{
                    if (userInfo.avatar_path) {{
                        callback(userInfo.avatar_path);
                    }} else {{
                        callback(null, color);
                    }}
                }});
        }}

        // Добавление сообщения в чат
        function addMessageToChat(data) {{
            const messagesContainer = document.getElementById('messages');
            const msg = document.createElement('div');
            msg.className = `msg ${{data.user === user ? 'own' : 'other'}}`;
            
            getUserAvatar(data.user, data.color || '#6366F1', (avatarPath, color) => {{
                let avatarHtml = '';
                if (avatarPath) {{
                    avatarHtml = `<div class="msg-avatar" style="background-image: url(${{avatarPath}})"></div>`;
                }} else {{
                    avatarHtml = `<div class="msg-avatar" style="background-color: ${{color}}">${{data.user.slice(0, 2).toUpperCase()}}</div>`;
                }}
                
                let content = `
                    <div class="msg-header">
                        ${{avatarHtml}}
                        <div class="msg-sender">${{data.user}}</div>
                    </div>
                    ${{data.message ? data.message.replace(/\\n/g, '<br>') : ''}}
                `;
                
                if (data.file) {{
                    if (data.fileType === 'image') {{
                        content += `<div class="file-preview"><img src="${{data.file}}"></div>`;
                    }} else {{
                        content += `<div class="file-preview"><video src="${{data.file}}" controls></video></div>`;
                    }}
                }}
                
                content += `<div class="msg-time">${{data.timestamp || ''}}</div>`;
                msg.innerHTML = content;
                messagesContainer.appendChild(msg);
                messagesContainer.scrollTop = messagesContainer.scrollHeight;
            }});
        }}

        // Функции для работы с аватарками
        function openAvatarModal() {{
            document.getElementById('avatar-modal').style.display = 'flex';
            const preview = document.getElementById('avatar-preview');
            fetch('/user_info/' + user)
                .then(r => r.json())
                .then(userInfo => {{
                    if (userInfo.avatar_path) {{
                        preview.style.backgroundImage = `url(${{userInfo.avatar_path}})`;
                        preview.textContent = '';
                    }} else {{
                        preview.style.backgroundImage = 'none';
                        preview.style.backgroundColor = userInfo.avatar_color;
                        preview.textContent = user.slice(0, 2).toUpperCase();
                    }}
                }});
        }}

        function closeAvatarModal() {{
            document.getElementById('avatar-modal').style.display = 'none';
        }}

        function previewAvatar(input) {{
            const file = input.files[0];
            if (file) {{
                const reader = new FileReader();
                reader.onload = (e) => {{
                    const preview = document.getElementById('avatar-preview');
                    preview.style.backgroundImage = `url(${{e.target.result}})`;
                    preview.textContent = '';
                }};
                reader.readAsDataURL(file);
            }}
        }}

        function uploadAvatar() {{
            const fileInput = document.getElementById('avatar-input');
            const file = fileInput.files[0];
            
            if (file) {{
                const formData = new FormData();
                formData.append('avatar', file);
                
                fetch('/upload_avatar', {{
                    method: 'POST',
                    body: formData
                }})
                .then(r => r.json())
                .then(data => {{
                    if (data.success) {{
                        loadUserAvatar();
                        closeAvatarModal();
                        alert('Аватарка обновлена!');
                    }} else {{
                        alert('Ошибка загрузки аватарки');
                    }}
                }});
            }} else {{
                alert('Выберите файл');
            }}
        }}

        function removeAvatar() {{
            fetch('/delete_avatar', {{ method: 'POST' }})
                .then(r => r.json())
                .then(data => {{
                    if (data.success) {{
                        loadUserAvatar();
                        closeAvatarModal();
                        alert('Аватарка удалена!');
                    }}
                }});
        }}

        // Функции для работы с темами
        function openThemeModal() {{
            document.getElementById('theme-modal').style.display = 'flex';
        }}

        function closeThemeModal() {{
            document.getElementById('theme-modal').style.display = 'none';
        }}

        function setTheme(theme) {{
            fetch('/set_theme', {{
                method: 'POST',
                headers: {{ 'Content-Type': 'application/json' }},
                body: JSON.stringify({{ theme: theme }})
            }})
            .then(r => r.json())
            .then(data => {{
                if (data.success) {{
                    document.documentElement.setAttribute('data-theme', theme);
                    closeThemeModal();
                }}
            }});
        }}

        // Функции для работы с каналами
        function openCreateChannelModal() {{
            document.getElementById('create-channel-modal').style.display = 'flex';
        }}

        function closeCreateChannelModal() {{
            document.getElementById('create-channel-modal').style.display = 'none';
        }}

        function openRenameModal() {{
            document.getElementById('rename-modal').style.display = 'flex';
        }}

        function closeRenameModal() {{
            document.getElementById('rename-modal').style.display = 'none';
        }}

        function createChannel() {{
            const name = document.getElementById('channel-name').value;
            const displayName = document.getElementById('channel-display-name').value;
            const description = document.getElementById('channel-description').value;
            const isPrivate = document.getElementById('channel-private').checked;
            
            if (!name) {{
                alert('Введите идентификатор канала');
                return;
            }}
            
            fetch('/create_channel', {{
                method: 'POST',
                headers: {{ 'Content-Type': 'application/json' }},
                body: JSON.stringify({{
                    name: name,
                    display_name: displayName || name,
                    description: description,
                    is_private: isPrivate
                }})
            }})
            .then(r => r.json())
            .then(data => {{
                if (data.success) {{
                    closeCreateChannelModal();
                    loadUserChannels();
                    alert('Канал создан!');
                }} else {{
                    alert(data.error);
                }}
            }});
        }}

        function renameChannel() {{
            const newName = document.getElementById('channel-rename-input').value;
            if (!newName) {{
                alert('Введите новое название');
                return;
            }}
            
            fetch('/rename_channel', {{
                method: 'POST',
                headers: {{ 'Content-Type': 'application/json' }},
                body: JSON.stringify({{
                    channel_name: currentChannel,
                    new_display_name: newName
                }})
            }})
            .then(r => r.json())
            .then(data => {{
                if (data.success) {{
                    document.getElementById('chat-title').textContent = '# ' + newName;
                    closeRenameModal();
                    loadUserChannels();
                    alert('Канал переименован!');
                }} else {{
                    alert(data.error);
                }}
            }});
        }}

        // Загрузка каналов пользователя
        function loadUserChannels() {{
            fetch('/user_channels')
                .then(r => r.json())
                .then(channels => {{
                    const channelsContainer = document.getElementById('channels');
                    channelsContainer.innerHTML = '';
                    
                    channels.forEach(channel => {{
                        const el = document.createElement('div');
                        el.className = 'nav-item';
                        el.innerHTML = `
                            <i class="fas fa-hashtag"></i>
                            <span>${{channel.display_name}}</span>
                        `;
                        el.onclick = () => openRoom('channel_' + channel.name, 'channel', channel.display_name);
                        channelsContainer.appendChild(el);
                    }});
                }});
        }}

        // Открытие комнаты
        function openRoom(r, t, title) {{
            room = r;
            roomType = t;
            currentChannel = t === 'channel' ? r.replace('channel_', '') : '';
            
            document.getElementById('chat-title').textContent = '# ' + title;
            document.querySelectorAll('.nav-item').forEach(el => el.classList.remove('active'));
            event.currentTarget.classList.add('active');
            
            document.getElementById('messages').innerHTML = '';
            
            // Показываем/скрываем кнопки управления каналом
            const channelActions = document.getElementById('channel-actions');
            if (t === 'channel') {{
                channelActions.style.display = 'flex';
            }} else {{
                channelActions.style.display = 'none';
            }}
            
            // Загружаем историю
            fetch('/get_messages/' + r)
                .then(r => r.json())
                .then(messages => {{
                    messages.forEach(msg => {{
                        addMessageToChat(msg);
                    }});
                }});
            
            socket.emit('join', {{ room: r }});
        }}

        // Отправка сообщения
        function sendMessage() {{
            const input = document.getElementById('msg-input');
            const msg = input.value.trim();
            const fileInput = document.getElementById('file-input');
            
            if (!msg && !fileInput.files[0]) return;
            
            const data = {{ 
                message: msg, 
                room: room, 
                type: roomType 
            }};
            
            if (fileInput.files[0]) {{
                const reader = new FileReader();
                reader.onload = (e) => {{
                    data.file = e.target.result;
                    data.fileType = fileInput.files[0].type.startsWith('image/') ? 'image' : 'video';
                    data.fileName = fileInput.files[0].name;
                    socket.emit('message', data);
                    resetInput();
                }};
                reader.readAsDataURL(fileInput.files[0]);
            }} else {{
                socket.emit('message', data);
                resetInput();
            }}
        }}

        function resetInput() {{
            document.getElementById('msg-input').value = '';
            document.getElementById('file-input').value = '';
            document.getElementById('file-preview').innerHTML = '';
        }}

        function handleKeydown(e) {{
            if (e.key === 'Enter' && !e.shiftKey) {{
                e.preventDefault();
                sendMessage();
            }}
        }}

        function handleFileSelect(input) {{
            const file = input.files[0];
            if (file) {{
                const reader = new FileReader();
                reader.onload = (e) => {{
                    const preview = document.getElementById('file-preview');
                    if (file.type.startsWith('image/')) {{
                        preview.innerHTML = `<img src="${{e.target.result}}" style="max-width: 200px; border-radius: 8px;">`;
                    }} else {{
                        preview.innerHTML = `<video src="${{e.target.result}}" controls style="max-width: 200px; border-radius: 8px;"></video>`;
                    }}
                }};
                reader.readAsDataURL(file);
            }}
        }}

        // Socket events
        socket.on('message', (data) => {{
            if (data.room === room) {{
                addMessageToChat(data);
            }}
        }});

        // Инициализация
        loadUserAvatar();
        loadUserChannels();
        
        // Загрузка пользователей
        fetch('/users')
            .then(r => r.json())
            .then(users => {{
                const usersContainer = document.getElementById('users');
                usersContainer.innerHTML = '';
                
                users.forEach(u => {{
                    if (u.username !== user) {{
                        const el = document.createElement('div');
                        el.className = 'nav-item';
                        el.innerHTML = `
                            <i class="fas fa-user${{u.online ? '-check' : ''}}"></i>
                            <span>${{u.username}}</span>
                        `;
                        el.onclick = () => openRoom(
                            'private_' + [user, u.username].sort().join('_'),
                            'private',
                            u.username
                        );
                        usersContainer.appendChild(el);
                    }}
                }});
            }});

        // Загрузка личных чатов
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
                        <span>${{chatUser}}</span>
                    `;
                    el.onclick = () => openRoom(
                        'private_' + [user, chatUser].sort().join('_'),
                        'private',
                        chatUser
                    );
                    pc.appendChild(el);
                }});
            }});

        socket.emit('join', {{ room: 'channel_general' }});
    </script>
</body>
</html>'''

    @app.route('/users')
    def users_handler():
        return jsonify(get_all_users())

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
        file_data = data.get('file')
        file_type = data.get('fileType', 'text')
        file_name = data.get('fileName')
        
        if not msg and not file_data:
            return
        
        # Сохраняем файл если есть
        file_path = None
        saved_file_name = None
        
        if file_data and file_type in ['image', 'video']:
            file_path, saved_file_name = save_base64_file(
                file_data, 
                app.config['UPLOAD_FOLDER'], 
                'png' if file_type == 'image' else 'mp4'
            )
        
        # Для приватных чатов
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
            file_path,
            file_name or saved_file_name
        )
        
        # Получаем информацию об отправителе для аватарки
        user_info = get_user(session['username'])
        user_color = user_info[5] if user_info else '#6366F1'
        
        # Отправляем сообщение
        emit('message', {
            'user': session['username'], 
            'message': msg, 
            'file': file_path, 
            'fileType': file_type,
            'fileName': file_name or saved_file_name,
            'color': user_color,
            'timestamp': datetime.now().strftime('%H:%M'),
            'room': room
        }, room=room)

    # Health check
    @app.route('/health')
    def health_check():
        return jsonify({'status': 'healthy', 'service': 'Tandau Messenger'})

    @app.errorhandler(404)
    def not_found(e):
        return redirect('/')

    return app

app = create_app()
socketio = app.extensions['socketio']

if __name__ == '__main__':
    port = int(os.environ.get('PORT', 5000))
    socketio.run(app, host='0.0.0.0', port=port, debug=False, allow_unsafe_werkzeug=True)
