# web_messenger.py - AURA Messenger с рабочей кнопкой создания канала и красивым отображением чатов
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

    # === Утилиты (без изменений) ===
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
                    'created_at': row[3], 'is_online': row[4], 'avatar_color': row[5],
                    'avatar_path': row[6], 'theme': row[7], 'profile_description': row[8] or ''
                }
            return None

    # Остальные утилиты без изменений (get_all_users, create_user, verify_user, save_message, get_messages_for_room, create_channel, get_channel_info, is_channel_member, get_user_channels и т.д.)
    # Я их оставляю как есть — они работают корректно.

    # === API Routes (без изменений, кроме create_channel) ===
    @app.route('/create_channel', methods=['POST'])
    def create_channel_handler():
        if 'username' not in session:
            return jsonify({'success': False, 'error': 'Не авторизован'})
        try:
            data = request.get_json()
            if not data:
                return jsonify({'success': False, 'error': 'Неверный формат данных'})
            name = data.get('name', '').strip().lower().replace(' ', '_')
            display_name = data.get('display_name', '').strip() or name.capitalize()
            description = data.get('description', '').strip()
            if not name:
                return jsonify({'success': False, 'error': 'Название канала не может быть пустым'})
            if not re.match(r'^[a-zA-Z0-9_]+$', name):
                return jsonify({'success': False, 'error': 'Только латинские буквы, цифры и _'})
            channel_id = create_channel(name, display_name, description, session['username'])
            if channel_id:
                return jsonify({
                    'success': True,
                    'channel_name': name,
                    'display_name': display_name,
                    'message': 'Канал успешно создан!'
                })
            return jsonify({'success': False, 'error': 'Канал с таким названием уже существует'})
        except Exception as e:
            print(f"Error creating channel: {e}")
            return jsonify({'success': False, 'error': 'Ошибка сервера'})

    # Остальные роуты без изменений...

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
            --bg: #0f0f23; --bg-light: #1a1a2e; --text: #ffffff; --text-light: #a0a0c0;
            --border: #3a3a5a; --glass-bg: rgba(255,255,255,0.05); --radius: 18px; --radius-sm: 12px;
        }}
        [data-theme="light"] {{
            --bg: #f8f9fa; --bg-light: #ffffff; --text: #1a1a2e; --text-light: #6b7280;
            --border: #e5e7eb; --glass-bg: rgba(0,0,0,0.02);
        }}
        /* Остальные стили без изменений до .messages */

        .messages {{
            flex: 1; overflow-y: auto; padding: 20px; display: flex; flex-direction: column; gap: 8px;
        }}
        .message-group {{
            display: flex; flex-direction: column;
            margin-bottom: 20px;
        }}
        .message-group-date {{
            text-align: center; margin: 20px 0; position: relative; color: var(--text-light); font-size: 0.8rem;
        }}
        .message-group-date::before {{
            content: ''; position: absolute; top: 50%; left: 0; right: 0; height: 1px; background: var(--border);
        }}
        .message-date-badge {{
            background: var(--glass-bg); padding: 6px 16px; border-radius: 20px; position: relative; z-index: 1;
        }}
        .message {{
            display: flex; gap: 10px; max-width: 75%; align-self: flex-start;
        }}
        .message.own {{
            align-self: flex-end; flex-direction: row-reverse;
        }}
        .message-avatar {{
            width: 36px; height: 36px; border-radius: 50%; background: var(--primary);
            color: white; display: flex; align-items: center; justify-content: center;
            font-weight: 600; flex-shrink: 0; margin-top: 8px;
        }}
        .message-content {{
            background: var(--glass-bg); border: 1px solid var(--border);
            border-radius: var(--radius); padding: 10px 14px; max-width: 100%;
        }}
        /* Хвостики сообщений */
        .message:not(.own) .message-content {{
            border-top-left-radius: 6px;
        }}
        .message.own .message-content {{
            background: linear-gradient(135deg, var(--primary), var(--primary-light));
            color: white; border: none; border-top-right-radius: 6px;
        }}
        /* Аватар только у первого сообщения в группе */
        .message.show-avatar .message-avatar {{ display: flex; }}
        .message.hide-avatar .message-avatar {{ display: none; }}
        .message.hide-avatar .message-content {{
            border-top-left-radius: var(--radius); /* для own — top-right */
        }}
        .message.own.hide-avatar .message-content {{
            border-top-right-radius: var(--radius);
        }}
        .message-text {{ line-height: 1.5; }}
        .message-time {{
            font-size: 0.75rem; color: var(--text-light); margin-top: 6px; text-align: right;
        }}
        .message.own .message-time {{ color: rgba(255,255,255,0.7); }}
        .message-file img, .message-file video {{
            max-width: 300px; border-radius: 12px; margin-top: 8px;
        }}
    </style>
</head>
<body>
    <!-- Остальная разметка без изменений -->
    <div class="app-container">
        <div class="sidebar" id="sidebar"> <!-- ... --> </div>
        <div class="chat-area" id="chat-area">
            <div class="chat-header"> <!-- ... --> </div>
            <div class="messages" id="messages">
                <div id="messages-content"></div>
            </div>
            <div class="input-area" id="input-area"> <!-- ... --> </div>
        </div>
    </div>

    <script src="https://cdnjs.cloudflare.com/ajax/libs/socket.io/4.7.2/socket.io.js"></script>
    <script>
        // ... весь предыдущий JS до loadMessages ...

        function loadMessages() {{
            fetch(`/get_messages/${{currentRoom}}`)
                .then(r => r.json())
                .then(messages => {{
                    const container = document.getElementById('messages-content');
                    container.innerHTML = '';
                    if (!messages || messages.length === 0) {{
                        container.innerHTML = `<div class="empty-state"><i class="fas fa-comments"></i><h3>Начните общение</h3><p>Отправьте первое сообщение</p></div>`;
                        return;
                    }}

                    // Группировка по дате и отправителю
                    let currentDate = null;
                    let currentSender = null;
                    let groupDiv = null;

                    messages.forEach((msg, i) => {{
                        const msgDate = msg.timestamp ? new Date(msg.timestamp).toLocaleDateString('ru-RU') : 'Сегодня';
                        const isOwn = msg.user === user;

                        // Новая дата — добавляем разделитель
                        if (msgDate !== currentDate) {{
                            const dateDiv = document.createElement('div');
                            dateDiv.className = 'message-group-date';
                            dateDiv.innerHTML = `<span class="message-date-badge">${{msgDate}}</span>`;
                            container.appendChild(dateDiv);
                            currentDate = msgDate;
                            currentSender = null;
                        }}

                        // Новая группа сообщений от отправителя
                        if (msg.user !== currentSender) {{
                            groupDiv = document.createElement('div');
                            groupDiv.className = 'message-group ' + (isOwn ? 'own-group' : '');
                            container.appendChild(groupDiv);
                            currentSender = msg.user;
                        }}

                        const messageDiv = document.createElement('div');
                        messageDiv.className = `message ${{isOwn ? 'own' : ''}} ${{msg.user === currentSender ? 'hide-avatar' : 'show-avatar'}}`;

                        let fileHtml = '';
                        if (msg.file) {{
                            if (msg.file.match(/\\.(mp4|webm|mov)$/i)) {{
                                fileHtml = `<video src="${{msg.file}}" controls class="message-file"></video>`;
                            }} else {{
                                fileHtml = `<img src="${{msg.file}}" alt="${{msg.file_name || 'файл'}}" class="message-file">`;
                            }}
                        }}

                        messageDiv.innerHTML = `
                            <div class="message-avatar" style="background-color: ${{msg.color || '#6366F1'}}">
                                ${{msg.avatar_path ? '' : msg.user.slice(0,2).toUpperCase()}}
                            </div>
                            <div class="message-content">
                                ${{(i === 0 || msg.user !== messages[i-1]?.user) ? `<div class="message-sender">${{msg.user}}</div>` : ''}}
                                <div class="message-text">${{msg.message || ''}}</div>
                                ${{fileHtml}}
                                <div class="message-time">${{msg.timestamp ? msg.timestamp.slice(11,16) : ''}}</div>
                            </div>
                        `;

                        if (msg.avatar_path) {{
                            messageDiv.querySelector('.message-avatar').style.backgroundImage = `url(${msg.avatar_path})`;
                            messageDiv.querySelector('.message-avatar').textContent = '';
                        }}

                        groupDiv.appendChild(messageDiv);
                    }});

                    container.scrollTop = container.scrollHeight;
                }});
        }}

        // Остальной JS без изменений
    </script>
</body>
</html>'''

    # Остальные роуты и SocketIO события без изменений

    return app

app = create_app()
socketio = app.extensions['socketio']

if __name__ == '__main__':
    port = int(os.environ.get('PORT', 5000))
    socketio.run(app, host='0.0.0.0', port=port, debug=True, allow_unsafe_werkzeug=True)
