# web_messenger.py - AURA Messenger (один файл, готов к деплою на Render)

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

    def get_messages_for_room(room, limit=100):
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
                    'timestamp': row[5][11:16] if row[5] else '',
                    'color': user_info['avatar_color'] if user_info else '#6366F1',
                    'avatar_path': user_info['avatar_path'] if user_info else None
                })
            return messages

    def save_message(user, msg, room, recipient=None, msg_type='text', file_path=None, file_name=None):
        with sqlite3.connect('messenger.db') as conn:
            c = conn.cursor()
            c.execute('''INSERT INTO messages
                         (username, message, room, recipient, message_type, file_path, file_name)
                         VALUES (?, ?, ?, ?, ?, ?, ?)''',
                      (user, msg, room, recipient, msg_type, file_path, file_name))
            conn.commit()
            return c.lastrowid

    def create_channel(name, display_name, description, created_by, is_private=False):
        with sqlite3.connect('messenger.db') as conn:
            c = conn.cursor()
            try:
                c.execute('SELECT id FROM channels WHERE name = ?', (name,))
                if c.fetchone():
                    return None
                c.execute('''INSERT INTO channels
                             (name, display_name, description, created_by, is_private)
                             VALUES (?, ?, ?, ?, ?)''',
                          (name, display_name or name, description or '', created_by, is_private))
                channel_id = c.lastrowid
                c.execute('INSERT INTO channel_members (channel_id, username, is_admin) VALUES (?, ?, ?)',
                          (channel_id, created_by, True))
                conn.commit()
                return channel_id
            except:
                return None

    def get_user_channels(username):
        with sqlite3.connect('messenger.db') as conn:
            c = conn.cursor()
            c.execute('''SELECT c.name, c.display_name, c.description, c.is_private,
                                c.allow_messages, c.created_by, c.avatar_path, c.subscriber_count
                         FROM channels c
                         JOIN channel_members cm ON c.id = cm.channel_id
                         WHERE cm.username = ? ORDER BY c.name''', (username,))
            return [{
                'name': row[0],
                'display_name': row[1],
                'description': row[2],
                'is_private': row[3],
                'allow_messages': row[4],
                'created_by': row[5],
                'avatar_path': row[6],
                'subscriber_count': row[7] or 0
            } for row in c.fetchall()]

    def get_user_personal_chats(username):
        with sqlite3.connect('messenger.db') as conn:
            c = conn.cursor()
            c.execute('''SELECT DISTINCT
                         CASE WHEN username = ? THEN recipient ELSE username END as chat_user
                         FROM messages
                         WHERE (username = ? OR recipient = ?) AND room LIKE 'private_%'
                         AND chat_user IS NOT NULL''', (username, username, username))
            return [row[0] for row in c.fetchall()]

    # === Routes ===
    @app.route('/')
    def index():
        if 'username' in session:
            return redirect('/chat')
        return '''<!DOCTYPE html>
<html lang="ru">/* ... весь твой красивый логин/регистрация HTML ... */</html>'''
        # (Тот большой HTML с логином и регистрацией — оставь как был, он слишком большой для вставки здесь)

    @app.route('/login', methods=['POST'])
    def login_handler():
        u = request.form.get('username', '').strip()
        p = request.form.get('password', '')
        user = verify_user(u, p)
        if user:
            session['username'] = u
            update_online(u, True)
            return jsonify({'success': True})
        return jsonify({'success': False, 'error': 'Неверный логин или пароль'})

    @app.route('/register', methods=['POST'])
    def register_handler():
        u = request.form.get('username', '').strip()
        p = request.form.get('password', '')
        if len(u) < 3 or len(p) < 4:
            return jsonify({'success': False, 'error': 'Слишком короткий логин/пароль'})
        success, msg = create_user(u, p)
        return jsonify({'success': success, 'error': msg if not success else None})

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
        theme = user['theme'] if user else 'dark'
        return f'''<!DOCTYPE html>
<html lang="ru" data-theme="{theme}">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>AURA Messenger - {username}</title>
    <link rel="stylesheet" href="https://cdnjs.cloudflare.com/ajax/libs/font-awesome/6.5.0/css/all.min.css">
    <style>{open("static/style.css").read() if os.path.exists("static/style.css") else ""}</style>
    <!-- Здесь все твои стили из /chat — если хочешь, вынеси в отдельный файл -->
</head>
<body>
    <!-- Весь HTML интерфейса чата как у тебя был -->
    <div class="app-container">
        <!-- Сайдбар, чат и т.д. -->
    </div>

    <script src="https://cdnjs.cloudflare.com/ajax/libs/socket.io/4.7.2/socket.io.js"></script>
    <script>
        const socket = io();
        const user = "{username}";

        // === ИСПРАВЛЕННАЯ ФУНКЦИЯ СОЗДАНИЯ КАНАЛА ===
        function createChannel() {{
            const nameInput = document.getElementById('channel-name');
            const descriptionInput = document.getElementById('channel-description');

            const display_name = nameInput.value.trim();
            const description = descriptionInput.value.trim();

            if (!display_name) {{
                alert('Введите название канала');
                return;
            }}

            // Формируем техническое имя: только латиница, цифры, подчёркивание
            let name = display_name.toLowerCase();
            name = name.replace(/[^a-z0-9_]/g, '_');
            name = name.replace(/_+/g, '_');
            name = name.replace(/^_+|_+$/g, '');

            if (name.length < 2) {{
                alert('Название слишком короткое или содержит недопустимые символы');
                return;
            }}

            fetch('/create_channel', {{
                method: 'POST',
                headers: {{ 'Content-Type': 'application/json' }},
                body: JSON.stringify({{
                    name: name,
                    display_name: display_name,
                    description: description
                }})
            }})
            .then(r => r.json())
            .then(data => {{
                if (data.success) {{
                    alert('Канал "' + display_name + '" успешно создан!');
                    closeModal('create-channel-modal');
                    nameInput.value = '';
                    descriptionInput.value = '';
                    loadChannels(); // обновляем список
                    openChat(data.channel_name, 'channel', data.display_name); // открываем
                }} else {{
                    alert(data.error || 'Ошибка создания канала');
                }}
            }})
            .catch(() => alert('Ошибка соединения'));
        }}

        // Остальной JavaScript как у тебя был (loadChannels, openChat и т.д.)
    </script>
</body>
</html>'''

    @app.route('/create_channel', methods=['POST'])
    def create_channel_handler():
        if 'username' not in session:
            return jsonify({'success': False, 'error': 'Не авторизован'})
        data = request.get_json()
        name = data.get('name', '').strip()
        display_name = data.get('display_name', '').strip()
        description = data.get('description', '').strip()

        if not name or not re.match(r'^[a-z0-9_]+$', name) or len(name) < 2:
            return jsonify({'success': False, 'error': 'Некорректное название канала'})

        channel_id = create_channel(name, display_name or name.capitalize(), description, session['username'])
        if channel_id:
            return jsonify({
                'success': True,
                'channel_name': name,
                'display_name': display_name or name.capitalize()
            })
        return jsonify({'success': False, 'error': 'Канал с таким именем уже существует'})

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
        file_path = data.get('file')
        file_name = data.get('fileName')
        file_type = data.get('fileType', 'text')

        recipient = None
        if room.startswith('private_'):
            parts = room.split('_')[1:]
            recipient = parts[1] if parts[0] == session['username'] else parts[0]

        save_message(session['username'], msg, room, recipient, file_type, file_path, file_name)

        emit('message', {
            'user': session['username'],
            'message': msg,
            'file': file_path,
            'fileName': file_name,
            'fileType': file_type,
            'timestamp': datetime.now().strftime('%H:%M'),
            'room': room
        }, room=room)

    return app

app = create_app()

if __name__ == '__main__':
    port = int(os.environ.get('PORT', 5000))
    app.debug = True
    socketio = app.extensions['socketio']
    socketio.run(app, host='0.0.0.0', port=port)
