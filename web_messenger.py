# web_messenger.py - AURA Messenger (полная версия в ОДНОМ файле)

from flask import Flask, request, jsonify, session, redirect
from flask_socketio import SocketIO, emit, join_room, leave_room
import sqlite3
from datetime import datetime
from werkzeug.security import generate_password_hash, check_password_hash
from werkzeug.utils import secure_filename
import random
import os
import re

app = Flask(__name__)
app.config['SECRET_KEY'] = os.environ.get('SECRET_KEY', 'aura-secret-key-2024')
app.config['UPLOAD_FOLDER'] = 'static/uploads'
app.config['AVATAR_FOLDER'] = 'static/avatars'
app.config['MAX_CONTENT_LENGTH'] = 16 * 1024 * 1024

ALLOWED_EXT = {'png', 'jpg', 'jpeg', 'gif', 'webp', 'mp4', 'webm', 'mov'}

# Создаём папки
os.makedirs('static/uploads', exist_ok=True)
os.makedirs('static/avatars', exist_ok=True)

socketio = SocketIO(app, cors_allowed_origins="*")

# === База данных ===
def init_db():
    with sqlite3.connect('messenger.db') as conn:
        c = conn.cursor()
        c.execute('''CREATE TABLE IF NOT EXISTS users (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            username TEXT UNIQUE NOT NULL,
            password_hash TEXT NOT NULL,
            avatar_color TEXT DEFAULT '#6366F1',
            theme TEXT DEFAULT 'dark'
        )''')
        c.execute('''CREATE TABLE IF NOT EXISTS messages (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            username TEXT NOT NULL,
            message TEXT,
            room TEXT NOT NULL,
            timestamp TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            file_path TEXT,
            file_name TEXT
        )''')
        c.execute('''CREATE TABLE IF NOT EXISTS channels (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT UNIQUE NOT NULL,
            display_name TEXT NOT NULL,
            description TEXT DEFAULT '',
            created_by TEXT NOT NULL
        )''')
        c.execute('''CREATE TABLE IF NOT EXISTS channel_members (
            channel_id INTEGER,
            username TEXT,
            UNIQUE(channel_id, username)
        )''')
        # Создаём общий канал
        c.execute('INSERT OR IGNORE INTO channels (name, display_name, created_by) VALUES (?, ?, ?)',
                  ('general', 'General', 'system'))
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
        c.execute('SELECT username, avatar_color, theme FROM users WHERE username = ?', (username,))
        row = c.fetchone()
        return {'username': row[0], 'color': row[1], 'theme': row[2]} if row else None

def create_user(username, password):
    with sqlite3.connect('messenger.db') as conn:
        c = conn.cursor()
        c.execute('SELECT 1 FROM users WHERE username = ?', (username,))
        if c.fetchone(): return False
        colors = ['#6366F1','#8B5CF6','#10B981','#F59E0B','#EF4444','#3B82F6']
        c.execute('INSERT INTO users (username, password_hash, avatar_color) VALUES (?, ?, ?)',
                  (username, generate_password_hash(password), random.choice(colors)))
        # Добавляем в общий канал
        c.execute('INSERT OR IGNORE INTO channel_members (channel_id, username) SELECT id, ? FROM channels WHERE name="general"', (username,))
        conn.commit()
        return True

def verify_user(username, password):
    with sqlite3.connect('messenger.db') as conn:
        c = conn.cursor()
        c.execute('SELECT password_hash FROM users WHERE username = ?', (username,))
        row = c.fetchone()
        return check_password_hash(row[0], password) if row else False

def create_channel(name, display_name, description, creator):
    with sqlite3.connect('messenger.db') as conn:
        c = conn.cursor()
        c.execute('SELECT 1 FROM channels WHERE name = ?', (name,))
        if c.fetchone(): return False
        c.execute('INSERT INTO channels (name, display_name, description, created_by) VALUES (?, ?, ?, ?)',
                  (name, display_name, description, creator))
        channel_id = c.lastrowid
        c.execute('INSERT INTO channel_members (channel_id, username) VALUES (?, ?)', (channel_id, creator))
        conn.commit()
        return True

def get_channels(username):
    with sqlite3.connect('messenger.db') as conn:
        c = conn.cursor()
        c.execute('''SELECT c.name, c.display_name FROM channels c
                     JOIN channel_members cm ON c.id = cm.channel_id
                     WHERE cm.username = ?''', (username,))
        return [{'name': r[0], 'display_name': r[1]} for r in c.fetchall()]

# === Маршруты ===
@app.route('/')
def index():
    if 'username' in session:
        return redirect('/chat')
    return '''
    <!DOCTYPE html>
    <html lang="ru"><head><meta charset="UTF-8"><title>AURA Login</title>
    <style>body{background:#0f0f23;color:#fff;font-family:system-ui;display:flex;justify-content:center;align-items:center;height:100vh;margin:0;}
    .card{background:#1a1a2e;padding:40px;border-radius:16px;width:400px;text-align:center;}
    input,button{width:100%;padding:12px;margin:10px 0;border-radius:12px;border:none;}
    button{background:#7c3aed;color:white;cursor:pointer;}</style></head>
    <body><div class="card">
    <h1>AURA</h1>
    <input id="user" placeholder="Логин"><input id="pass" type="password" placeholder="Пароль">
    <button onclick="fetch('/login',{method:'POST',headers:{'Content-Type':'application/x-www-form-urlencoded'},
    body:'username='+document.getElementById('user').value+'&password='+document.getElementById('pass').value})
    .then(r=>r.json()).then(d=>d.success?location.href='/chat':alert('Ошибка'))">Войти</button>
    <button onclick="fetch('/register',{method:'POST',headers:{'Content-Type':'application/x-www-form-urlencoded'},
    body:'username='+document.getElementById('user').value+'&password='+document.getElementById('pass').value})
    .then(r=>r.json()).then(d=>d.success?location.href='/chat':alert(d.error||'Ошибка'))">Регистрация</button>
    </div></body></html>
    '''

@app.route('/login', methods=['POST'])
def login():
    username = request.form['username'].strip()
    password = request.form['password']
    if verify_user(username, password):
        session['username'] = username
        return jsonify({'success': True})
    return jsonify({'success': False})

@app.route('/register', methods=['POST'])
def register():
    username = request.form['username'].strip()
    password = request.form['password']
    if len(username) < 3 or len(password) < 4:
        return jsonify({'error': 'Слишком коротко'})
    if create_user(username, password):
        session['username'] = username
        return jsonify({'success': True})
    return jsonify({'error': 'Пользователь существует'})

@app.route('/logout')
def logout():
    session.pop('username', None)
    return redirect('/')

@app.route('/chat')
def chat():
    if 'username' not in session:
        return redirect('/')
    username = session['username']
    return f'''
<!DOCTYPE html>
<html lang="ru">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>AURA - {username}</title>
    <link rel="stylesheet" href="https://cdnjs.cloudflare.com/ajax/libs/font-awesome/6.5.0/css/all.min.css">
    <style>
        :root {{ --primary: #7c3aed; --bg: #0f0f23; --bg2: #1a1a2e; --text: #fff; --text2: #aaa; --border: #333; }}
        body {{ margin:0; height:100vh; display:flex; background:var(--bg); color:var(--text); font-family:system-ui; }}
        .sidebar {{ width:280px; background:var(--bg2); border-right:1px solid var(--border); padding:20px; display:flex; flex-direction:column; }}
        .sidebar h1 {{ margin:0 0 20px; text-align:center; }}
        .channels {{ flex:1; overflow-y:auto; }}
        .nav-item {{ padding:12px; border-radius:12px; cursor:pointer; margin:5px 0; }}
        .nav-item:hover {{ background:rgba(124,58,237,0.3); }}
        .nav-item.active {{ background:var(--primary); }}
        .chat {{ flex:1; display:flex; flex-direction:column; }}
        .messages {{ flex:1; overflow-y:auto; padding:20px; }}
        .message {{ margin:10px 0; display:flex; align-items:flex-start; }}
        .message.own {{ flex-direction:row-reverse; text-align:right; }}
        .msg-bubble {{ max-width:70%; padding:12px 16px; border-radius:18px; background:var(--bg2); }}
        .msg-bubble.own {{ background:var(--primary); color:white; }}
        .input-area {{ padding:20px; background:var(--bg2); display:flex; gap:10px; }}
        input[type=text] {{ flex:1; padding:14px; border-radius:24px; border:none; background:#333; color:white; }}
        button {{ background:var(--primary); color:white; border:none; padding:14px; border-radius:50%; cursor:pointer; width:50px; height:50px; }}
        .modal {{ display:none; position:fixed; inset:0; background:rgba(0,0,0,0.8); justify-content:center; align-items:center; }}
        .modal-content {{ background:var(--bg2); padding:30px; border-radius:16px; width:400px; }}
        .modal input, .modal textarea {{ width:100%; padding:10px; margin:10px 0; border-radius:8px; background:#333; color:white; border:none; }}
    </style>
</head>
<body>
    <div class="sidebar">
        <h1>AURA</h1>
        <div class="channels" id="channels-list"></div>
        <div class="nav-item" onclick="document.getElementById('create-modal').style.display='flex'">+ Создать канал</div>
        <div style="margin-top:auto;">{username} | <a href="/logout" style="color:#aaa;">Выйти</a></div>
    </div>
    <div class="chat">
        <div class="messages" id="messages">Выберите канал или создайте новый</div>
        <div class="input-area">
            <input type="text" id="msg-input" placeholder="Сообщение..." onkeydown="if(event.key==='Enter') sendMessage()">
            <button onclick="sendMessage()"><i class="fas fa-paper-plane"></i></button>
        </div>
    </div>

    <!-- Модалка создания канала -->
    <div class="modal" id="create-modal">
        <div class="modal-content">
            <h2>Создать канал</h2>
            <input id="channel-display" placeholder="Название канала">
            <textarea id="channel-desc" placeholder="Описание (необязательно)"></textarea>
            <button onclick="createChannel()">Создать</button>
            <button onclick="document.getElementById('create-modal').style.display='none'">Отмена</button>
        </div>
    </div>

    <script src="https://cdnjs.cloudflare.com/ajax/libs/socket.io/4.7.2/socket.io.js"></script>
    <script>
        const socket = io();
        const user = "{username}";
        let currentRoom = "channel_general";

        socket.emit('join', {{room: currentRoom}});

        function loadChannels() {{
            fetch('/channels').then(r => r.json()).then(channels => {{
                const list = document.getElementById('channels-list');
                list.innerHTML = '';
                channels.forEach(ch => {{
                    const div = document.createElement('div');
                    div.className = 'nav-item';
                    div.textContent = ch.display_name;
                    div.onclick = () => {{
                        currentRoom = 'channel_' + ch.name;
                        socket.emit('join', {{room: currentRoom}});
                        document.querySelectorAll('.nav-item').forEach(i=>i.classList.remove('active'));
                        div.classList.add('active');
                        document.getElementById('messages').innerHTML = '';
                    }};
                    list.appendChild(div);
                }});
            }});
        }}

        function createChannel() {{
            let display_name = document.getElementById('channel-display').value.trim();
            let description = document.getElementById('channel-desc').value.trim();
            if (!display_name) return alert('Введите название');

            let name = display_name.toLowerCase().replace(/[^a-z0-9_]/g, '_').replace(/_+/g, '_').replace(/^_|_$/g, '');
            if (name.length < 2) return alert('Недопустимое имя канала');

            fetch('/create_channel', {{
                method: 'POST',
                headers: {{'Content-Type': 'application/json'}},
                body: JSON.stringify({{name, display_name, description}})
            }}).then(r => r.json()).then(res => {{
                if (res.success) {{
                    alert('Канал создан!');
                    document.getElementById('create-modal').style.display = 'none';
                    loadChannels();
                }} else {{
                    alert(res.error || 'Ошибка');
                }}
            }});
        }}

        function sendMessage() {{
            let msg = document.getElementById('msg-input').value.trim();
            if (!msg) return;
            socket.emit('message', {{message: msg, room: currentRoom}});
            document.getElementById('msg-input').value = '';
        }}

        socket.on('message', data => {{
            let div = document.createElement('div');
            div.className = 'message ' + (data.user === user ? 'own' : '');
            div.innerHTML = `<div class="msg-bubble ${data.user === user ? 'own' : ''}">
                <strong>${data.user}</strong><br>${data.message}<br>
                <small>${data.time}</small>
            </div>`;
            document.getElementById('messages').appendChild(div);
            document.getElementById('messages').scrollTop = document.getElementById('messages').scrollHeight;
        }});

        loadChannels();
    </script>
</body>
</html>
'''

@app.route('/channels')
def channels():
    if 'username' not in session: return jsonify([])
    return jsonify(get_channels(session['username']))

@app.route('/create_channel', methods=['POST'])
def create_channel_route():
    if 'username' not in session: return jsonify({'error': 'Нет авторизации'})
    data = request.json
    name = data.get('name')
    display_name = data.get('display_name')
    description = data.get('description', '')
    if not name or not re.match(r'^[a-z0-9_]+$', name) or len(name) < 2:
        return jsonify({'error': 'Некорректное имя'})
    if create_channel(name, display_name, description, session['username']):
        return jsonify({'success': True})
    return jsonify({'error': 'Канал уже существует'})

# === SocketIO ===
@socketio.on('join')
def on_join(data):
    join_room(data['room'])

@socketio.on('message')
def on_message(data):
    room = data['room']
    msg = data['message']
    time = datetime.now().strftime('%H:%M')
    emit('message', {'user': session['username'], 'message': msg, 'time': time}, room=room)

if __name__ == '__main__':
    port = int(os.environ.get('PORT', 5000))
    socketio.run(app, host='0.0.0.0', port=port, allow_unsafe_werkzeug=True)
