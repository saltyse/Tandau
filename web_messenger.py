# web_messenger.py - AURA Messenger (полная версия в одном файле)

from flask import Flask, request, jsonify, session, redirect, send_from_directory
from flask_socketio import SocketIO, emit, join_room, leave_room
import sqlite3
from datetime import datetime
from werkzeug.security import generate_password_hash, check_password_hash
from werkzeug.utils import secure_filename
import random
import os
import re

def create_app():
    app = Flask(__name__)
    app.config['SECRET_KEY'] = os.environ.get('SECRET_KEY', 'aura-secret-key-2024')
    app.config['UPLOAD_FOLDER'] = 'static/uploads'
    app.config['AVATAR_FOLDER'] = 'static/avatars'
    app.config['FAVORITE_FOLDER'] = 'static/favorites'
    app.config['CHANNEL_AVATAR_FOLDER'] = 'static/channel_avatars'
    app.config['MAX_CONTENT_LENGTH'] = 16 * 1024 * 1024

    ALLOWED_EXT = {'png', 'jpg', 'jpeg', 'gif', 'webp', 'mp4', 'webm', 'mov', 'txt', 'pdf', 'doc', 'docx'}

    # Создаём папки
    for folder in [app.config['UPLOAD_FOLDER'], app.config['AVATAR_FOLDER'],
                   app.config['FAVORITE_FOLDER'], app.config['CHANNEL_AVATAR_FOLDER']]:
        os.makedirs(folder, exist_ok=True)

    socketio = SocketIO(app, cors_allowed_origins="*", async_mode='threading')

    # === База данных ===
    def init_db():
        with sqlite3.connect('messenger.db') as conn:
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
                file_name TEXT
            )''')
            c.execute('''CREATE TABLE IF NOT EXISTS channels (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                name TEXT UNIQUE NOT NULL,
                display_name TEXT,
                description TEXT,
                created_by TEXT NOT NULL,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                is_private BOOLEAN DEFAULT FALSE,
                avatar_path TEXT,
                subscriber_count INTEGER DEFAULT 0
            )''')
            c.execute('''CREATE TABLE IF NOT EXISTS channel_members (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                channel_id INTEGER,
                username TEXT NOT NULL,
                is_admin BOOLEAN DEFAULT FALSE,
                FOREIGN KEY (channel_id) REFERENCES channels (id),
                UNIQUE(channel_id, username)
            )''')
            c.execute('INSERT OR IGNORE INTO channels (name, display_name, description, created_by) VALUES (?, ?, ?, ?)',
                      ('general', 'General', 'Общий канал', 'system'))
            conn.commit()

    init_db()

    # === Утилиты ===
    def allowed_file(filename):
        return '.' in filename and filename.rsplit('.', 1)[1].lower() in ALLOWED_EXT

    def save_uploaded_file(file, folder):
        if not file or file.filename == '': return None, None
        if not allowed_file(file.filename): return None, None
        filename = secure_filename(f"{int(datetime.now().timestamp())}_{file.filename}")
        path = os.path.join(folder, filename)
        file.save(path)
        return f'/static/{os.path.basename(folder)}/{filename}', filename

    def get_user(username):
        with sqlite3.connect('messenger.db') as conn:
            c = conn.cursor()
            c.execute('SELECT * FROM users WHERE username = ?', (username,))
            row = c.fetchone()
            if row:
                return {
                    'id': row[0], 'username': row[1], 'password_hash': row[2],
                    'is_online': row[4], 'avatar_color': row[5], 'avatar_path': row[6],
                    'theme': row[7], 'profile_description': row[8] or ''
                }
            return None

    def create_user(username, password):
        with sqlite3.connect('messenger.db') as conn:
            c = conn.cursor()
            c.execute('SELECT id FROM users WHERE username = ?', (username,))
            if c.fetchone(): return False, "Пользователь уже существует"
            colors = ['#6366F1','#8B5CF6','#10B981','#F59E0B','#EF4444','#3B82F6']
            c.execute('INSERT INTO users (username, password_hash, avatar_color) VALUES (?, ?, ?)',
                      (username, generate_password_hash(password), random.choice(colors)))
            c.execute('INSERT OR IGNORE INTO channel_members (channel_id, username) SELECT id, ? FROM channels WHERE name="general"', (username,))
            conn.commit()
            return True, "OK"

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
            c.execute('INSERT INTO messages (username, message, room, recipient, message_type, file_path, file_name) VALUES (?, ?, ?, ?, ?, ?, ?)',
                      (user, msg, room, recipient, msg_type, file_path, file_name))
            conn.commit()

    def get_messages_for_room(room, limit=100):
        with sqlite3.connect('messenger.db') as conn:
            c = conn.cursor()
            c.execute('''SELECT username, message, message_type, file_path, file_name, timestamp
                         FROM messages WHERE room = ? ORDER BY timestamp ASC LIMIT ?''', (room, limit))
            messages = []
            for row in c.fetchall():
                user_info = get_user(row[0])
                messages.append({
                    'user': row[0], 'message': row[1], 'type': row[2], 'file': row[3], 'file_name': row[4],
                    'timestamp': row[5][11:16] if row[5] else '',
                    'color': user_info['avatar_color'] if user_info else '#6366F1',
                    'avatar_path': user_info['avatar_path'] if user_info else None
                })
            return messages

    def create_channel(name, display_name, description, created_by):
        with sqlite3.connect('messenger.db') as conn:
            c = conn.cursor()
            try:
                c.execute('SELECT id FROM channels WHERE name = ?', (name,))
                if c.fetchone(): return None
                c.execute('INSERT INTO channels (name, display_name, description, created_by) VALUES (?, ?, ?, ?)',
                          (name, display_name or name.capitalize(), description or '', created_by))
                channel_id = c.lastrowid
                c.execute('INSERT INTO channel_members (channel_id, username, is_admin) VALUES (?, ?, 1)', (channel_id, created_by))
                conn.commit()
                return channel_id
            except: return None

    def get_user_channels(username):
        with sqlite3.connect('messenger.db') as conn:
            c = conn.cursor()
            c.execute('''SELECT c.name, c.display_name, c.description
                         FROM channels c JOIN channel_members cm ON c.id = cm.channel_id
                         WHERE cm.username = ? ORDER BY c.name''', (username,))
            return [{'name': r[0], 'display_name': r[1], 'description': r[2]} for r in c.fetchall()]

    def get_user_personal_chats(username):
        with sqlite3.connect('messenger.db') as conn:
            c = conn.cursor()
            c.execute('''SELECT DISTINCT CASE WHEN username = ? THEN recipient ELSE username END
                         FROM messages WHERE (username = ? OR recipient = ?) AND room LIKE 'private_%' ''', (username, username, username))
            return [r[0] for r in c.fetchall() if r[0]]

    # === Маршруты ===
    @app.route('/')
    def index():
        if 'username' in session:
            return redirect('/chat')
        return redirect('/chat')  # временно — можно вернуть страницу логина

    @app.route('/login', methods=['POST'])
    def login():
        username = request.form.get('username', '').strip()
        password = request.form.get('password', '')
        user = verify_user(username, password)
        if user:
            session['username'] = username
            update_online(username, True)
            return jsonify({'success': True})
        return jsonify({'success': False, 'error': 'Неверно'})

    @app.route('/register', methods=['POST'])
    def register():
        username = request.form.get('username', '').strip()
        password = request.form.get('password', '')
        if len(username) < 3 or len(password) < 4:
            return jsonify({'success': False, 'error': 'Короткий логин/пароль'})
        success, msg = create_user(username, password)
        return jsonify({'success': success, 'error': msg if not success else None})

    @app.route('/logout')
    def logout():
        if 'username' in session:
            update_online(session['username'], False)
            session.pop('username')
        return redirect('/')

    @app.route('/create_channel', methods=['POST'])
    def create_channel_handler():
        if 'username' not in session:
            return jsonify({'success': False, 'error': 'Не авторизован'})
        data = request.get_json()
        name = data.get('name')
        display_name = data.get('display_name')
        description = data.get('description', '')

        if not name or not re.match(r'^[a-z0-9_]+$', name) or len(name) < 2:
            return jsonify({'success': False, 'error': 'Некорректное имя канала'})

        if create_channel(name, display_name, description, session['username']):
            return jsonify({'success': True, 'channel_name': name, 'display_name': display_name or name.capitalize()})
        return jsonify({'success': False, 'error': 'Канал уже существует'})

    @app.route('/user_channels')
    def user_channels():
        if 'username' not in session: return jsonify([])
        return jsonify(get_user_channels(session['username']))

    @app.route('/personal_chats')
    def personal_chats():
        if 'username' not in session: return jsonify([])
        return jsonify(get_user_personal_chats(session['username']))

    @app.route('/upload_file', methods=['POST'])
    def upload_file():
        if 'username' not in session or 'file' not in request.files:
            return jsonify({'success': False})
        file = request.files['file']
        path, filename = save_uploaded_file(file, app.config['UPLOAD_FOLDER'])
        if path:
            return jsonify({'success': True, 'path': path, 'filename': filename})
        return jsonify({'success': False})

    @app.route('/chat')
    def chat():
        if 'username' not in session:
            return redirect('/')
        username = session['username']
        return f"""
<!DOCTYPE html>
<html lang="ru" data-theme="dark">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>AURA - {username}</title>
    <link rel="stylesheet" href="https://cdnjs.cloudflare.com/ajax/libs/font-awesome/6.5.0/css/all.min.css">
    <style>
        :root {{ --primary: #7c3aed; --bg: #0f0f23; --bg-light: #1a1a2e; --text: #fff; --text-light: #a0a0c0; --border: #3a3a5a; }}
        body {{ background: var(--bg); color: var(--text); font-family: system-ui; height: 100vh; margin: 0; display: flex; }}
        .sidebar {{ width: 280px; background: var(--bg-light); border-right: 1px solid var(--border); padding: 20px; }}
        .chat-area {{ flex: 1; display: flex; flex-direction: column; }}
        .messages {{ flex: 1; overflow-y: auto; padding: 20px; }}
        .input-area {{ padding: 20px; background: var(--bg-light); }}
        .nav-item {{ padding: 12px; border-radius: 12px; cursor: pointer; margin-bottom: 8px; }}
        .nav-item:hover {{ background: rgba(124,58,237,0.2); }}
        .modal-overlay {{ display: none; position: fixed; inset: 0; background: rgba(0,0,0,0.8); justify-content: center; align-items: center; }}
        .modal-content {{ background: var(--bg-light); padding: 30px; border-radius: 16px; width: 400px; }}
    </style>
</head>
<body>
    <div class="sidebar">
        <h1>AURA</h1>
        <div id="channels-list"></div>
        <div class="nav-item" onclick="openCreateChannel()">+ Создать канал</div>
        <div style="margin-top: auto;">{username} <a href="/logout">Выйти</a></div>
    </div>
    <div class="chat-area">
        <div class="messages" id="messages"></div>
        <div class="input-area">
            <input type="text" id="msg-input" placeholder="Сообщение..." style="width:100%; padding:12px; border-radius:24px; border:none; background:#333; color:white;">
            <button onclick="sendMessage()">Отправить</button>
        </div>
    </div>

    <!-- Модалка создания канала -->
    <div class="modal-overlay" id="create-channel-modal">
        <div class="modal-content">
            <h3>Создать канал</h3>
            <input type="text" id="channel-name" placeholder="Отображаемое название" style="width:100%; padding:10px; margin:10px 0; border-radius:8px; border:1px solid #555; background:#222; color:white;">
            <textarea id="channel-description" placeholder="Описание (необязательно)" style="width:100%; padding:10px; margin:10px 0; border-radius:8px; border:1px solid #555; background:#222; color:white;"></textarea>
            <button onclick="createChannel()">Создать</button>
            <button onclick="closeModal('create-channel-modal')">Отмена</button>
        </div>
    </div>

    <script src="https://cdnjs.cloudflare.com/ajax/libs/socket.io/4.7.2/socket.io.js"></script>
    <script>
        const socket = io();
        const user = "{username}";
        let currentRoom = "channel_general";

        socket.emit('join', {{room: currentRoom}});

        function loadChannels() {{
            fetch('/user_channels').then(r => r.json()).then(channels => {{
                const list = document.getElementById('channels-list');
                list.innerHTML = '';
                channels.forEach(ch => {{
                    const item = document.createElement('div');
                    item.className = 'nav-item';
                    item.textContent = ch.display_name;
                    item.onclick = () => openChat('channel_' + ch.name, ch.display_name);
                    list.appendChild(item);
                }});
            }});
        }}

        function openChat(room, title) {{
            currentRoom = room;
            document.querySelector('.messages').innerHTML = '<p>Загрузка...</p>';
            socket.emit('join', {{room}});
        }}

        function createChannel() {{
            const display_name = document.getElementById('channel-name').value.trim();
            const description = document.getElementById('channel-description').value.trim();
            if (!display_name) return alert('Введите название');

            let name = display_name.toLowerCase().replace(/[^a-z0-9_]/g, '_').replace(/_+/g, '_').replace(/^_|_$/g, '');
            if (name.length < 2) return alert('Слишком короткое имя');

            fetch('/create_channel', {{
                method: 'POST',
                headers: {{'Content-Type': 'application/json'}},
                body: JSON.stringify({{name, display_name, description}})
            }}).then(r => r.json()).then(data => {{
                if (data.success) {{
                    alert('Канал создан!');
                    closeModal('create-channel-modal');
                    loadChannels();
                    openChat('channel_' + data.channel_name, data.display_name);
                }} else {{
                    alert(data.error);
                }}
            }});
        }}

        function openCreateChannel() {{
            document.getElementById('create-channel-modal').style.display = 'flex';
        }}

        function closeModal(id) {{
            document.getElementById(id).style.display = 'none';
        }}

        loadChannels();
    </script>
</body>
</html>
"""

    # === SocketIO ===
    @socketio.on('message')
    def handle_message(data):
        msg = data.get('message', '')
        room = data.get('room', 'channel_general')
        save_message(session['username'], msg, room)
        emit('message', {{'user': session['username'], 'message': msg, 'timestamp': datetime.now().strftime('%H:%M')}}, room=room)

    @socketio.on('join')
    def on_join(data):
        join_room(data['room'])

    return app

app = create_app()

if __name__ == '__main__':
    port = int(os.environ.get('PORT', 5000))
    from flask_socketio import SocketIO
    socketio = app.extensions['socketio']
    socketio.run(app, host='0.0.0.0', port=port, debug=False)
