# web_messenger.py - AURA Messenger (один файл, с рабочим созданием канала и красивым чатом)

from flask import Flask, request, jsonify, session, redirect, send_from_directory, render_template_string
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
    app.config['SECRET_KEY'] = os.environ.get('SECRET_KEY', 'aura-secret-key-2024')
    app.config['UPLOAD_FOLDER'] = 'static/uploads'
    app.config['AVATAR_FOLDER'] = 'static/avatars'
    app.config['FAVORITE_FOLDER'] = 'static/favorites'
    app.config['CHANNEL_AVATAR_FOLDER'] = 'static/channel_avatars'
    app.config['MAX_CONTENT_LENGTH'] = 16 * 1024 * 1024  # 16MB

    ALLOWED_EXT = {'png', 'jpg', 'jpeg', 'gif', 'webp', 'mp4', 'webm', 'mov', 'txt', 'pdf', 'doc', 'docx'}

    # Создаем папки
    for folder in [app.config['UPLOAD_FOLDER'], app.config['AVATAR_FOLDER'],
                   app.config['FAVORITE_FOLDER'], app.config['CHANNEL_AVATAR_FOLDER']]:
        os.makedirs(folder, exist_ok=True)

    socketio = SocketIO(app, cors_allowed_origins="*", async_mode='threading')

    # === Инициализация БД ===
    def init_db():
        with sqlite3.connect('messenger.db', check_same_thread=False) as conn:
            c = conn.cursor()
            c.execute('''CREATE TABLE IF NOT EXISTS users (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                username TEXT UNIQUE NOT NULL,
                password_hash TEXT NOT NULL,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                is_online BOOLEAN DEFAULT FALSE,
                avatar_color TEXT DEFAULT '#6366F1',
                avatar_path TEXT,
                theme TEXT DEFAULT 'dark',
                profile_description TEXT DEFAULT ''
            )''')
            c.execute('''CREATE TABLE IF NOT EXISTS messages (
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
            )''')
            c.execute('''CREATE TABLE IF NOT EXISTS channels (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                name TEXT UNIQUE NOT NULL,
                display_name TEXT,
                description TEXT,
                created_by TEXT NOT NULL,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                is_private BOOLEAN DEFAULT FALSE,
                allow_messages BOOLEAN DEFAULT TRUE,
                avatar_path TEXT,
                subscriber_count INTEGER DEFAULT 0
            )''')
            c.execute('''CREATE TABLE IF NOT EXISTS channel_members (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                channel_id INTEGER,
                username TEXT NOT NULL,
                joined_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                is_admin BOOLEAN DEFAULT FALSE,
                FOREIGN KEY (channel_id) REFERENCES channels (id),
                UNIQUE(channel_id, username)
            )''')
            c.execute('''CREATE TABLE IF NOT EXISTS favorites (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                username TEXT NOT NULL,
                content TEXT,
                file_path TEXT,
                file_name TEXT,
                file_type TEXT DEFAULT 'text',
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                is_pinned BOOLEAN DEFAULT FALSE,
                category TEXT DEFAULT 'general'
            )''')
            # Общий канал по умолчанию
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
        except:
            return None, None

    def get_user(username):
        with sqlite3.connect('messenger.db') as conn:
            c = conn.cursor()
            c.execute('SELECT * FROM users WHERE username = ?', (username,))
            row = c.fetchone()
            if row:
                return {
                    'id': row[0], 'username': row[1], 'password_hash': row[2],
                    'created_at': row[3], 'is_online': row[4], 'avatar_color': row[5],
                    'avatar_path': row[6], 'theme': row[7], 'profile_description': row[8] or ''
                }
            return None

    def create_user(username, password):
        with sqlite3.connect('messenger.db') as conn:
            c = conn.cursor()
            try:
                c.execute('SELECT id FROM users WHERE username = ?', (username,))
                if c.fetchone():
                    return False, "Пользователь уже существует"
                colors = ['#6366F1','#8B5CF6','#10B981','#F59E0B','#EF4444','#3B82F6']
                c.execute('INSERT INTO users (username, password_hash, avatar_color) VALUES (?, ?, ?)',
                          (username, generate_password_hash(password), random.choice(colors)))
                c.execute('INSERT OR IGNORE INTO channel_members (channel_id, username) '
                          'SELECT id, ? FROM channels WHERE name="general"', (username,))
                c.execute('UPDATE channels SET subscriber_count = subscriber_count + 1 WHERE name = "general"')
                conn.commit()
                return True, "Успешно"
            except Exception as e:
                return False, str(e)

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

    def save_message(user, msg, room, recipient=None, msg_type='text', file_path=None, file_name=None):
        with sqlite3.connect('messenger.db') as conn:
            c = conn.cursor()
            c.execute('INSERT INTO messages (username, message, room, recipient, message_type, file_path, file_name) '
                      'VALUES (?, ?, ?, ?, ?, ?, ?)',
                      (user, msg, room, recipient, msg_type, file_path, file_name))
            conn.commit()
            return c.lastrowid

    def get_messages_for_room(room, limit=500):
        with sqlite3.connect('messenger.db') as conn:
            c = conn.cursor()
            c.execute('''SELECT username, message, message_type, file_path, file_name, timestamp
                         FROM messages WHERE room = ? ORDER BY timestamp ASC LIMIT ?''', (room, limit))
            messages = []
            for row in c.fetchall():
                user_info = get_user(row[0])
                messages.append({
                    'user': row[0],
                    'message': row[1],
                    'type': row[2],
                    'file': row[3],
                    'file_name': row[4],
                    'timestamp': row[5],
                    'color': user_info['avatar_color'] if user_info else '#6366F1',
                    'avatar_path': user_info['avatar_path'] if user_info else None
                })
            return messages

    def get_user_personal_chats(username):
        with sqlite3.connect('messenger.db') as conn:
            c = conn.cursor()
            c.execute('''SELECT DISTINCT CASE WHEN username = ? THEN recipient ELSE username END as chat_user
                         FROM messages WHERE (username = ? OR recipient = ?) AND room LIKE 'private_%' ''', (username, username, username))
            return [row[0] for row in c.fetchall() if row[0]]

    def create_channel(name, display_name, description, created_by, is_private=False):
        with sqlite3.connect('messenger.db') as conn:
            c = conn.cursor()
            try:
                c.execute('SELECT id FROM channels WHERE name = ?', (name,))
                if c.fetchone():
                    return None
                c.execute('INSERT INTO channels (name, display_name, description, created_by, is_private) '
                          'VALUES (?, ?, ?, ?, ?)', (name, display_name or name, description or '', created_by, is_private))
                channel_id = c.lastrowid
                c.execute('INSERT INTO channel_members (channel_id, username, is_admin) VALUES (?, ?, ?)',
                          (channel_id, created_by, True))
                conn.commit()
                return channel_id
            except:
                return None

    def get_channel_info(channel_name):
        with sqlite3.connect('messenger.db') as conn:
            c = conn.cursor()
            c.execute('SELECT id, name, display_name, description, created_by, is_private, allow_messages, avatar_path, subscriber_count '
                      'FROM channels WHERE name = ?', (channel_name,))
            row = c.fetchone()
            if row:
                return {
                    'id': row[0], 'name': row[1], 'display_name': row[2], 'description': row[3],
                    'created_by': row[4], 'is_private': row[5], 'allow_messages': row[6],
                    'avatar_path': row[7], 'subscriber_count': row[8] or 0
                }
            return None

    def is_channel_member(channel_name, username):
        with sqlite3.connect('messenger.db') as conn:
            c = conn.cursor()
            c.execute('''SELECT 1 FROM channel_members cm JOIN channels c ON cm.channel_id = c.id
                         WHERE c.name = ? AND cm.username = ?''', (channel_name, username))
            return c.fetchone() is not None

    def get_user_channels(username):
        with sqlite3.connect('messenger.db') as conn:
            c = conn.cursor()
            c.execute('''SELECT c.name, c.display_name, c.description, c.is_private, c.allow_messages,
                                c.created_by, c.avatar_path, c.subscriber_count
                         FROM channels c JOIN channel_members cm ON c.id = cm.channel_id
                         WHERE cm.username = ? ORDER BY c.name''', (username,))
            return [{
                'name': row[0], 'display_name': row[1], 'description': row[2], 'is_private': row[3],
                'allow_messages': row[4], 'created_by': row[5], 'avatar_path': row[6],
                'subscriber_count': row[7] or 0
            } for row in c.fetchall()]

    # === API Routes ===
    @app.route('/create_channel', methods=['POST'])
    def create_channel_handler():
        if 'username' not in session:
            return jsonify({'success': False, 'error': 'Не авторизован'})
        data = request.get_json()
        if not data:
            return jsonify({'success': False, 'error': 'Нет данных'})
        name = data.get('name', '').strip()
        display_name = data.get('display_name', '').strip() or name.capitalize()
        description = data.get('description', '').strip()
        if not name or len(name) < 2 or len(name) > 50 or not re.match(r'^[a-zA-Z0-9_]+$', name):
            return jsonify({'success': False, 'error': 'Неверное название канала'})
        channel_id = create_channel(name, display_name, description, session['username'])
        if channel_id:
            return jsonify({'success': True, 'channel_name': name, 'display_name': display_name})
        return jsonify({'success': False, 'error': 'Канал уже существует'})

    @app.route('/user_channels')
    def user_channels_handler():
        if 'username' not in session:
            return jsonify({'success': False})
        return jsonify({'success': True, 'channels': get_user_channels(session['username'])})

    @app.route('/personal_chats')
    def personal_chats_handler():
        if 'username' not in session:
            return jsonify({'success': False})
        return jsonify({'success': True, 'chats': get_user_personal_chats(session['username'])})

    @app.route('/channel_info/<channel_name>')
    def channel_info_handler(channel_name):
        if 'username' not in session:
            return jsonify({'success': False})
        info = get_channel_info(channel_name)
        if info:
            info['is_member'] = is_channel_member(channel_name, session['username'])
            return jsonify({'success': True, 'data': info})
        return jsonify({'success': False})

    @app.route('/upload_file', methods=['POST'])
    def upload_file_handler():
        if 'username' not in session:
            return jsonify({'success': False})
        file = request.files.get('file')
        if not file or not file.filename:
            return jsonify({'success': False})
        path, filename = save_uploaded_file(file, app.config['UPLOAD_FOLDER'])
        if path:
            file_type = 'video' if filename.lower().endswith(('.mp4', '.webm', '.mov')) else 'image'
            return jsonify({'success': True, 'path': path, 'filename': filename, 'file_type': file_type})
        return jsonify({'success': False})

    @app.route('/get_messages/<room>')
    def get_messages_handler(room):
        if 'username' not in session:
            return jsonify([])
        return jsonify(get_messages_for_room(room))

    @app.route('/static/<path:filename>')
    def static_files(filename):
        return send_from_directory('static', filename)

    @app.route('/login', methods=['POST'])
    def login_handler():
        u = request.form.get('username', '').strip()
        p = request.form.get('password', '')
        user = verify_user(u, p)
        if user:
            session['username'] = u
            update_online(u, True)
            return jsonify({'success': True})
        return jsonify({'success': False, 'error': 'Неверные данные'})

    @app.route('/register', methods=['POST'])
    def register_handler():
        u = request.form.get('username', '').strip()
        p = request.form.get('password', '')
        if len(u) < 3 or len(p) < 4:
            return jsonify({'success': False, 'error': 'Слишком короткие данные'})
        success, msg = create_user(u, p)
        return jsonify({'success': success, 'error': msg} if not success else {'success': True})

    @app.route('/logout')
    def logout_handler():
        if 'username' in session:
            update_online(session['username'], False)
            session.pop('username')
        return redirect('/')

    @app.route('/')
    def index():
        if 'username' in session:
            return redirect('/chat')
        # Здесь весь HTML страницы входа/регистрации (оставляем как был, он красивый)
        return '''<!DOCTYPE html>
<html lang="ru">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>AURA Messenger</title>
    <link rel="stylesheet" href="https://cdnjs.cloudflare.com/ajax/libs/font-awesome/6.5.0/css/all.min.css">
    <style>
        /* Весь ваш красивый CSS для страницы входа (тот же, что был раньше) */
        /* Для экономии места здесь опущен, но вставьте свой оригинальный CSS */
        body {background: linear-gradient(135deg, #7c3aed 0%, #a78bfa 100%); min-height: 100vh; display: flex; align-items: center; justify-content: center; font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif;}
        /* ... весь остальной CSS ... */
    </style>
</head>
<body>
    <!-- Весь ваш HTML страницы входа -->
    <!-- (оставьте как был) -->
    <script>
        /* Ваш JS для входа/регистрации */
    </script>
</body>
</html>'''

    @app.route('/chat')
    def chat_handler():
        if 'username' not in session:
            return redirect('/')
        username = session['username']
        user = get_user(username)
        if not user:
            session.pop('username', None)
            return redirect('/')
        theme = user['theme']

        return f'''<!DOCTYPE html>
<html lang="ru" data-theme="{theme}">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>AURA Messenger - {username}</title>
    <link rel="stylesheet" href="https://cdnjs.cloudflare.com/ajax/libs/font-awesome/6.5.0/css/all.min.css">
    <style>
        :root {{
            --primary: #7c3aed; --primary-dark: #6d28d9; --primary-light: #8b5cf6;
            --secondary: #a78bfa; --accent: #10b981;
            --bg: #0f0f23; --bg-light: #1a1a2e; --bg-lighter: #2d2d4d;
            --text: #ffffff; --text-light: #a0a0c0; --border: #3a3a5a;
            --glass-bg: rgba(255,255,255,0.05); --glass-border: rgba(255,255,255,0.1);
            --radius: 16px; --radius-sm: 12px;
        }}
        [data-theme="light"] {{
            --bg: #f8f9fa; --bg-light: #ffffff; --text: #1a1a2e; --text-light: #6b7280;
            --border: #e5e7eb; --glass-bg: rgba(0,0,0,0.02); --glass-border: rgba(0,0,0,0.08);
        }}
        * {{margin:0;padding:0;box-sizing:border-box;}}
        body {{background:var(--bg);color:var(--text);height:100vh;overflow:hidden;font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',Roboto,sans-serif;}}
        .app-container {{display:flex;height:100vh;}}
        .sidebar {{width:280px;background:var(--bg-light);border-right:1px solid var(--border);display:flex;flex-direction:column;}}
        .sidebar-header {{padding:20px 16px;display:flex;align-items:center;gap:12px;border-bottom:1px solid var(--border);}}
        .logo-placeholder {{width:40px;height:40px;border-radius:12px;background:linear-gradient(135deg,var(--primary),var(--secondary));display:flex;align-items:center;justify-content:center;color:white;font-weight:bold;}}
        .search-container {{padding:16px;border-bottom:1px solid var(--border);}}
        .search-input {{width:100%;padding:12px 16px 12px 44px;background:var(--glass-bg);border:1px solid var(--border);border-radius:var(--radius-sm);color:var(--text);}}
        .nav {{flex:1;overflow-y:auto;padding:16px 8px;}}
        .nav-item {{display:flex;align-items:center;gap:12px;padding:12px 16px;border-radius:var(--radius-sm);cursor:pointer;color:var(--text);}}
        .nav-item.active {{background:rgba(124,58,237,0.1);color:var(--primary);}}
        .user-info {{padding:16px;border-top:1px solid var(--border);display:flex;align-items:center;gap:12px;}}
        .user-avatar {{width:40px;height:40px;border-radius:50%;background:var(--primary);color:white;display:flex;align-items:center;justify-content:center;font-weight:600;}}
        .user-avatar.online::after {{content:'';position:absolute;bottom:2px;right:2px;width:10px;height:10px;background:var(--accent);border-radius:50%;border:2px solid var(--bg-light);}}
        .chat-area {{flex:1;display:flex;flex-direction:column;background:var(--bg);}}
        .chat-header {{padding:16px 20px;border-bottom:1px solid var(--border);display:flex;align-items:center;gap:16px;background:var(--bg-light);}}
        .chat-avatar {{width:44px;height:44px;border-radius:50%;background:var(--primary);color:white;display:flex;align-items:center;justify-content:center;font-weight:600;}}
        .messages {{flex:1;overflow-y:auto;padding:20px;display:flex;flex-direction:column;gap:12px;}}
        .message-group {{display:flex;flex-direction:column;max-width:80%;align-self:flex-start;}}
        .message-group.own {{align-self:flex-end;}}
        .message-cluster {{display:flex;gap:12px;align-items:end;}}
        .message-cluster.own {{flex-direction:row-reverse;}}
        .message-avatar {{width:36px;height:36px;border-radius:50%;background:var(--primary);color:white;display:flex;align-items:center;justify-content:center;font-weight:600;flex-shrink:0;}}
        .message-bubbles {{display:flex;flex-direction:column;gap:4px;}}
        .message-bubble {{background:var(--glass-bg);border:1px solid var(--glass-border);border-radius:18px;padding:10px 14px;position:relative;}}
        .message-bubble.own {{background:linear-gradient(135deg,var(--primary),var(--secondary));color:white;border:none;}}
        .message-group:not(.own) .message-bubble:first-child {{border-top-left-radius:6px;}}
        .message-group:not(.own) .message-bubble:last-child {{border-bottom-left-radius:18px;}}
        .message-group.own .message-bubble:first-child {{border-top-right-radius:6px;}}
        .message-group.own .message-bubble:last-child {{border-bottom-right-radius:18px;}}
        .message-sender {{font-weight:600;font-size:0.85rem;margin-bottom:4px;color:var(--text-light);}}
        .message-group.own .message-sender {{color:rgba(255,255,255,0.9);}}
        .message-text {{line-height:1.5;font-size:0.95rem;}}
        .message-file {{margin-top:8px;border-radius:12px;overflow:hidden;max-width:320px;}}
        .message-file img, .message-file video {{width:100%;border-radius:12px;}}
        .message-time {{font-size:0.75rem;color:var(--text-light);text-align:right;margin-top:4px;}}
        .message-group.own .message-time {{color:rgba(255,255,255,0.7);}}
        .input-area {{padding:20px;border-top:1px solid var(--border);background:var(--bg-light);}}
        .input-container {{display:flex;gap:12px;align-items:flex-end;}}
        .msg-input {{flex:1;padding:14px 16px;border:1px solid var(--border);border-radius:24px;background:var(--glass-bg);color:var(--text);resize:none;min-height:48px;max-height:120px;}}
        .send-btn {{background:linear-gradient(135deg,var(--primary),var(--secondary));color:white;border:none;padding:12px;border-radius:50%;cursor:pointer;}}
        @media (max-width:768px) {{ .sidebar {{position:fixed;inset:0;width:100%;max-width:320px;transform:translateX(-100%);transition:transform .3s;z-index:100;}} .sidebar.active {{transform:translateX(0);}} }}
    </style>
</head>
<body>
<div class="app-container">
    <div class="sidebar" id="sidebar">
        <div class="sidebar-header"><div class="logo-placeholder">A</div><h1>AURA</h1></div>
        <div class="search-container"><input type="text" class="search-input" placeholder="Поиск..." id="search-input"></div>
        <div class="nav">
            <div class="nav-category">Каналы</div>
            <a href="#" class="nav-item" onclick="openCreateChannel()"><i class="fas fa-plus-circle"></i> Создать канал</a>
            <div id="channels-list"></div>
            <div class="nav-category">Личные чаты</div>
            <div id="personal-chats-list"></div>
        </div>
        <div class="user-info">
            <div class="user-avatar online" id="user-avatar"></div>
            <div><div>{username}</div><div style="color:var(--text-light);font-size:0.8rem;">Online</div></div>
            <div><button style="background:none;border:none;color:var(--text-light);cursor:pointer;" onclick="logout()">Выйти</button></div>
        </div>
    </div>
    <div class="chat-area">
        <div class="chat-header">
            <div class="chat-avatar" id="chat-avatar">A</div>
            <div><div id="chat-title">Выберите чат</div></div>
        </div>
        <div class="messages" id="messages"><div id="messages-content"></div></div>
        <div class="input-area">
            <div class="input-container">
                <input type="file" id="file-input" style="display:none;" accept="image/*,video/*">
                <button style="background:none;border:none;color:var(--text-light);cursor:pointer;" onclick="document.getElementById('file-input').click()">Прикрепить</button>
                <textarea class="msg-input" id="msg-input" placeholder="Сообщение..."></textarea>
                <button class="send-btn" onclick="sendMessage()">Отправить</button>
            </div>
        </div>
    </div>
</div>

<!-- Модальное окно создания канала -->
<div class="modal-overlay" id="create-channel-modal" style="display:none;position:fixed;inset:0;background:rgba(0,0,0,0.7);align-items:center;justify-content:center;z-index:1000;">
    <div style="background:var(--bg-light);padding:30px;border-radius:16px;width:90%;max-width:400px;">
        <h3>Создать канал</h3>
        <input type="text" id="channel-name" placeholder="Название (латинские буквы, цифры, _)" style="width:100%;padding:10px;margin:10px 0;border-radius:8px;border:1px solid var(--border);background:var(--bg);color:var(--text);">
        <textarea id="channel-description" placeholder="Описание (необязательно)" style="width:100%;padding:10px;margin:10px 0;border-radius:8px;border:1px solid var(--border);background:var(--bg);color:var(--text);height:80px;"></textarea>
        <button onclick="createChannel()" style="width:100%;padding:12px;background:var(--primary);color:white;border:none;border-radius:8px;cursor:pointer;">Создать</button>
        <button onclick="document.getElementById('create-channel-modal').style.display='none'" style="width:100%;margin-top:8px;padding:12px;background:none;border:1px solid var(--border);color:var(--text);border-radius:8px;cursor:pointer;">Отмена</button>
    </div>
</div>

<script src="https://cdnjs.cloudflare.com/ajax/libs/socket.io/4.7.2/socket.io.js"></script>
<script>
    const socket = io();
    const user = "{username}";
    let currentRoom = "channel_general";
    let currentRoomType = "channel";

    window.onload = () => {{
        loadChannels();
        loadPersonalChats();
        openChat('general', 'channel', 'General');
        document.getElementById('user-avatar').textContent = user.slice(0,2).toUpperCase();
        document.getElementById('msg-input').addEventListener('keydown', e => {{ if (e.key === 'Enter' && !e.shiftKey) {{ e.preventDefault(); sendMessage(); }}}});
    }};

    function loadChannels() {{
        fetch('/user_channels').then(r=>r.json()).then(data=>{{
            const list = document.getElementById('channels-list');
            list.innerHTML = '';
            data.channels.forEach(ch => {{
                const item = document.createElement('a');
                item.className = 'nav-item';
                item.href = '#';
                item.onclick = () => openChat(ch.name, 'channel', ch.display_name);
                item.innerHTML = `<i class="fas fa-hashtag"></i> ${ch.display_name}`;
                list.appendChild(item);
            }});
        }});
    }}

    function loadPersonalChats() {{
        fetch('/personal_chats').then(r=>r.json()).then(data=>{{
            const list = document.getElementById('personal-chats-list');
            list.innerHTML = '';
            data.chats.forEach(chat => {{
                const item = document.createElement('a');
                item.className = 'nav-item';
                item.href = '#';
                item.onclick = () => openChat(chat, 'private', chat);
                item.innerHTML = `<i class="fas fa-user"></i> ${chat}`;
                list.appendChild(item);
            }});
        }});
    }}

    function openChat(target, type, title) {{
        currentRoom = type === 'channel' ? 'channel_' + target : 'private_' + [user, target].sort().join('_');
        currentRoomType = type;
        document.getElementById('chat-title').textContent = title;
        document.getElementById('chat-avatar').textContent = title.slice(0,2).toUpperCase();
        loadMessages();
        socket.emit('join', {{room: currentRoom}});
    }}

    function loadMessages() {{
        fetch(`/get_messages/${{currentRoom}}`).then(r=>r.json()).then(messages=>{{
            const container = document.getElementById('messages-content');
            container.innerHTML = '';
            if (messages.length === 0) {{ container.innerHTML = '<div style="text-align:center;color:var(--text-light);">Нет сообщений</div>'; return; }}

            let currentGroup = null;
            let lastUser = null;

            messages.forEach(msg => {{
                const time = msg.timestamp ? msg.timestamp.slice(11,16) : '';
                if (msg.user !== lastUser) {{
                    currentGroup = document.createElement('div');
                    currentGroup.className = `message-group ${{msg.user === user ? 'own' : ''}}`;
                    container.appendChild(currentGroup);

                    const cluster = document.createElement('div');
                    cluster.className = `message-cluster ${{msg.user === user ? 'own' : ''}}`;
                    currentGroup.appendChild(cluster);

                    const avatar = document.createElement('div');
                    avatar.className = 'message-avatar';
                    if (msg.avatar_path) avatar.style.backgroundImage = `url(${msg.avatar_path})`;
                    else {{ avatar.style.backgroundColor = msg.color; avatar.textContent = msg.user.slice(0,2).toUpperCase(); }}
                    cluster.appendChild(avatar);

                    const bubbles = document.createElement('div');
                    bubbles.className = 'message-bubbles';
                    cluster.appendChild(bubbles);
                    currentGroup.bubbles = bubbles;

                    if (msg.user !== user) {{
                        const sender = document.createElement('div');
                        sender.className = 'message-sender';
                        sender.textContent = msg.user;
                        bubbles.appendChild(sender);
                    }}
                    lastUser = msg.user;
                }}

                const bubble = document.createElement('div');
                bubble.className = `message-bubble ${{msg.user === user ? 'own' : ''}}`;

                if (msg.message) {{
                    const text = document.createElement('div');
                    text.className = 'message-text';
                    text.textContent = msg.message;
                    bubble.appendChild(text);
                }}
                if (msg.file) {{
                    const fileDiv = document.createElement('div');
                    fileDiv.className = 'message-file';
                    if (msg.type === 'video') fileDiv.innerHTML = `<video src="${msg.file}" controls></video>`;
                    else fileDiv.innerHTML = `<img src="${msg.file}">`;
                    bubble.appendChild(fileDiv);
                }}
                const timeSpan = document.createElement('div');
                timeSpan.className = 'message-time';
                timeSpan.textContent = time;
                bubble.appendChild(timeSpan);

                currentGroup.bubbles.appendChild(bubble);
            }});
            container.scrollTop = container.scrollHeight;
        }});
    }}

    function sendMessage() {{
        const input = document.getElementById('msg-input');
        const msg = input.value.trim();
        const fileInput = document.getElementById('file-input');
        if (!msg && !fileInput.files[0]) return;

        if (fileInput.files[0]) {{
            const form = new FormData();
            form.append('file', fileInput.files[0]);
            fetch('/upload_file', {{method:'POST', body:form}}).then(r=>r.json()).then(data=>{{
                if (data.success) {{
                    socket.emit('message', {{message: msg, room: currentRoom, file: data.path, fileName: data.filename, fileType: data.file_type}});
                }}
            }});
        }} else {{
            socket.emit('message', {{message: msg, room: currentRoom}});
        }}
        input.value = '';
        fileInput.value = '';
    }}

    socket.on('message', data => {{
        if (data.room === currentRoom) loadMessages();
    }});

    function openCreateChannel() {{
        document.getElementById('create-channel-modal').style.display = 'flex';
    }}

    function createChannel() {{
        const displayName = document.getElementById('channel-name').value.trim();
        const desc = document.getElementById('channel-description').value.trim();
        if (!displayName) return alert('Введите название');
        const name = displayName.toLowerCase().replace(/\\s+/g, '_').replace(/[^a-z0-9_]/g, '');
        fetch('/create_channel', {{
            method: 'POST',
            headers: {{'Content-Type': 'application/json'}},
            body: JSON.stringify({{name, display_name: displayName, description: desc}})
        }}).then(r=>r.json()).then(data=>{{
            if (data.success) {{
                document.getElementById('create-channel-modal').style.display = 'none';
                loadChannels();
                openChat(data.channel_name, 'channel', data.display_name);
            }} else alert(data.error || 'Ошибка');
        }});
    }}

    function logout() {{
        if (confirm('Выйти?')) location.href = '/logout';
    }}
</script>
</body>
</html>'''

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

    @socketio.on('message')
    def on_message(data):
        if 'username' not in session:
            return
        msg = data.get('message', '').strip()
        room = data.get('room')
        file = data.get('file')
        file_name = data.get('fileName')
        file_type = data.get('fileType', 'image')
        save_message(session['username'], msg, room, file_path=file, file_name=file_name, msg_type=file_type)
        emit('message', {
            'user': session['username'],
            'message': msg,
            'file': file,
            'fileName': file_name,
            'type': file_type,
            'timestamp': datetime.now().strftime('%Y-%m-%d %H:%M:%S'),
            'room': room
        }, room=room)

    return app

app = create_app()
socketio = app.extensions['socketio']

if __name__ == '__main__':
    port = int(os.environ.get('PORT', 5000))
    socketio.run(app, host='0.0.0.0', port=port, debug=False)
