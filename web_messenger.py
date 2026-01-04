# web_messenger.py - AURA Messenger (исправлено для Render.com)

from flask import Flask, request, jsonify, session, redirect, send_from_directory
from flask_socketio import SocketIO, emit, join_room
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

    ALLOWED_EXT = {'png', 'jpg', 'jpeg', 'gif', 'webp', 'mp4', 'webm', 'mov'}

    os.makedirs('static/uploads', exist_ok=True)
    os.makedirs('static/avatars', exist_ok=True)
    os.makedirs('static/favorites', exist_ok=True)
    os.makedirs('static/channel_avatars', exist_ok=True)

    # ВАЖНО: Убрали async_mode='threading' — Render использует eventlet/gevent
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
                avatar_path TEXT,
                theme TEXT DEFAULT 'dark'
            )''')
            c.execute('''CREATE TABLE IF NOT EXISTS messages (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                username TEXT NOT NULL,
                message TEXT,
                timestamp TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                room TEXT NOT NULL,
                message_type TEXT DEFAULT 'text',
                file_path TEXT,
                file_name TEXT
            )''')
            c.execute('''CREATE TABLE IF NOT EXISTS channels (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                name TEXT UNIQUE NOT NULL,
                display_name TEXT,
                description TEXT,
                created_by TEXT NOT NULL
            )''')
            c.execute('''CREATE TABLE IF NOT EXISTS channel_members (
                channel_id INTEGER,
                username TEXT NOT NULL,
                is_admin BOOLEAN DEFAULT FALSE,
                PRIMARY KEY(channel_id, username)
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
            c.execute('SELECT avatar_color, avatar_path FROM users WHERE username = ?', (username,))
            row = c.fetchone()
            if row:
                return {'avatar_color': row[0], 'avatar_path': row[1]}
            return {'avatar_color': '#6366F1', 'avatar_path': None}

    def create_user(username, password):
        with sqlite3.connect('messenger.db') as conn:
            c = conn.cursor()
            try:
                c.execute('SELECT 1 FROM users WHERE username = ?', (username,))
                if c.fetchone(): return False, "Пользователь существует"
                colors = ['#6366F1','#8B5CF6','#10B981','#F59E0B','#EF4444','#3B82F6']
                c.execute('INSERT INTO users (username, password_hash, avatar_color) VALUES (?, ?, ?)',
                          (username, generate_password_hash(password), random.choice(colors)))
                conn.commit()
                return True, ""
            except: return False, "Ошибка"

    def verify_user(username, password):
        with sqlite3.connect('messenger.db') as conn:
            c = conn.cursor()
            c.execute('SELECT password_hash FROM users WHERE username = ?', (username,))
            row = c.fetchone()
            if row and check_password_hash(row[0], password):
                return True
            return False

    def create_channel(name, display_name, description, created_by):
        with sqlite3.connect('messenger.db') as conn:
            c = conn.cursor()
            try:
                c.execute('SELECT 1 FROM channels WHERE name = ?', (name,))
                if c.fetchone(): return None
                c.execute('INSERT INTO channels (name, display_name, description, created_by) VALUES (?, ?, ?, ?)',
                          (name, display_name, description, created_by))
                channel_id = c.lastrowid
                c.execute('INSERT INTO channel_members (channel_id, username, is_admin) VALUES (?, ?, 1)',
                          (channel_id, created_by))
                conn.commit()
                return name
            except: return None

    def get_user_channels(username):
        with sqlite3.connect('messenger.db') as conn:
            c = conn.cursor()
            c.execute('''SELECT c.name, c.display_name FROM channels c
                         JOIN channel_members cm ON c.id = cm.channel_id
                         WHERE cm.username = ?''', (username,))
            return [{'name': r[0], 'display_name': r[1]} for r in c.fetchall()]

    def get_messages(room):
        with sqlite3.connect('messenger.db') as conn:
            c = conn.cursor()
            c.execute('SELECT username, message, file_path, file_name, message_type, timestamp FROM messages WHERE room = ? ORDER BY timestamp',
                      (room,))
            msgs = []
            for row in c.fetchall():
                user_info = get_user(row[0])
                msgs.append({
                    'user': row[0],
                    'message': row[1],
                    'file': row[2],
                    'file_name': row[3],
                    'type': row[4],
                    'timestamp': row[5],
                    'color': user_info['avatar_color'],
                    'avatar_path': user_info['avatar_path']
                })
            return msgs

    # === Роуты ===
    @app.route('/')
    def index():
        if 'username' in session:
            return redirect('/chat')
        return '''
        <!DOCTYPE html>
        <html lang="ru"><head><meta charset="UTF-8"><title>AURA</title><style>
            body{background:linear-gradient(135deg,#7c3aed,#a78bfa);height:100vh;display:flex;align-items:center;justify-content:center;color:white;font-family:sans-serif;}
            .card{background:white;color:#333;padding:40px;border-radius:16px;width:90%;max-width:400px;}
            input,button{width:100%;padding:12px;margin:10px 0;border-radius:8px;border:1px solid #ccc;}
            button{background:#7c3aed;color:white;border:none;cursor:pointer;}
        </style></head><body>
        <div class="card">
            <h2>Вход</h2>
            <input id="u" placeholder="Логин"><input id="p" type="password" placeholder="Пароль">
            <button onclick="fetch('/login',{method:'POST',headers:{'Content-Type':'application/x-www-form-urlencoded'},body:'username='+document.getElementById('u').value+'&password='+document.getElementById('p').value}).then(r=>r.json()).then(d=>d.success?location.href='/chat':alert('Ошибка'))">Войти</button>
            <h2 style="margin-top:20px;">Регистрация</h2>
            <input id="ru" placeholder="Логин"><input id="rp" type="password" placeholder="Пароль">
            <button onclick="fetch('/register',{method:'POST',headers:{'Content-Type':'application/x-www-form-urlencoded'},body:'username='+document.getElementById('ru').value+'&password='+document.getElementById('rp').value}).then(r=>r.json()).then(d=>d.success?location.href='/chat':alert(d.error||'Ошибка'))">Создать</button>
        </div>
        </body></html>
        '''

    @app.route('/login', methods=['POST'])
    def login():
        u = request.form.get('username','').strip()
        p = request.form.get('password','')
        if verify_user(u, p):
            session['username'] = u
            return jsonify({'success': True})
        return jsonify({'success': False})

    @app.route('/register', methods=['POST'])
    def register():
        u = request.form.get('username','').strip()
        p = request.form.get('password','')
        if len(u)<3 or len(p)<4: return jsonify({'success': False, 'error': 'Короткие данные'})
        success, msg = create_user(u, p)
        return jsonify({'success': success, 'error': msg})

    @app.route('/logout')
    def logout():
        session.pop('username', None)
        return redirect('/')

    @app.route('/chat')
    def chat():
        if 'username' not in session: return redirect('/')
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
                :root {{--primary:#7c3aed;--bg:#0f0f23;--bg-light:#1a1a2e;--text:#fff;--text-light:#a0a0c0;--border:#3a3a5a;--glass:rgba(255,255,255,0.05);--radius:16px;}}
                *{{margin:0;padding:0;box-sizing:border-box;}}
                body{{background:var(--bg);color:var(--text);height:100vh;display:flex;font-family:sans-serif;}}
                .sidebar{{width:280px;background:var(--bg-light);border-right:1px solid var(--border);padding:20px;display:flex;flex-direction:column;gap:16px;}}
                .nav-item{{padding:12px;border-radius:12px;cursor:pointer;display:flex;align-items:center;gap:12px;}}
                .nav-item:hover{{background:var(--glass);}}
                .chat-area{{flex:1;display:flex;flex-direction:column;}}
                .messages{{flex:1;overflow-y:auto;padding:20px;display:flex;flex-direction:column;gap:12px;}}
                .message-group{{display:flex;flex-direction:column;max-width:80%;align-self:flex-start;}}
                .message-group.own{{align-self:flex-end;}}
                .message-cluster{{display:flex;gap:12px;align-items:end;}}
                .message-cluster.own{{flex-direction:row-reverse;}}
                .message-avatar{{width:36px;height:36px;border-radius:50%;background:var(--primary);color:white;display:flex;align-items:center;justify-content:center;font-weight:bold;}}
                .message-bubble{{background:var(--glass);border:1px solid var(--border);border-radius:18px;padding:10px 14px;}}
                .message-bubble.own{{background:var(--primary);color:white;}}
                .message-group:not(.own) .message-bubble:last-child{{border-bottom-left-radius:18px;}}
                .message-group.own .message-bubble:last-child{{border-bottom-right-radius:18px;}}
                .input-area{{padding:20px;background:var(--bg-light);border-top:1px solid var(--border);display:flex;gap:12px;}}
                textarea{{flex:1;padding:14px;border-radius:24px;background:var(--glass);border:1px solid var(--border);color:var(--text);resize:none;}}
                button{{background:var(--primary);color:white;border:none;padding:12px;border-radius:50%;cursor:pointer;}}
            </style>
        </head>
        <body>
            <div class="sidebar">
                <h2>AURA</h2>
                <div onclick="document.getElementById('create-modal').style.display='flex'" class="nav-item"><i class="fas fa-plus"></i> Создать канал</div>
                <div id="channels-list"></div>
            </div>
            <div class="chat-area">
                <div class="messages" id="messages">
                    <div id="content"></div>
                </div>
                <div class="input-area">
                    <input type="file" id="file" style="display:none;">
                    <button onclick="document.getElementById('file').click()">📎</button>
                    <textarea id="input" placeholder="Сообщение..."></textarea>
                    <button onclick="send()">➤</button>
                </div>
            </div>

            <div id="create-modal" style="display:none;position:fixed;inset:0;background:rgba(0,0,0,0.8);align-items:center;justify-content:center;">
                <div style="background:var(--bg-light);padding:30px;border-radius:16px;width:90%;max-width:400px;">
                    <h3>Новый канал</h3>
                    <input id="cname" placeholder="Название" style="width:100%;padding:10px;margin:10px 0;border-radius:8px;border:1px solid var(--border);">
                    <button onclick="createChannel()" style="width:100%;padding:12px;background:var(--primary);color:white;border:none;border-radius:8px;">Создать</button>
                    <button onclick="document.getElementById('create-modal').style.display='none'" style="width:100%;margin-top:8px;padding:12px;background:transparent;border:1px solid var(--border);color:var(--text);border-radius:8px;">Отмена</button>
                </div>
            </div>

            <script src="https://cdnjs.cloudflare.com/ajax/libs/socket.io/4.7.2/socket.io.js"></script>
            <script>
                const socket = io();
                const user = "{username}";
                let room = "channel_general";

                function loadChannels() {{
                    fetch('/user_channels').then(r=>r.json()).then(d=>{{
                        const list = document.getElementById('channels-list');
                        list.innerHTML = '';
                        d.channels.forEach(ch=>{{
                            const el = document.createElement('div');
                            el.className = 'nav-item';
                            el.onclick = () => openChannel(ch.name, ch.display_name);
                            el.innerHTML = `# ${{ch.display_name}}`;
                            list.appendChild(el);
                        }});
                    }});
                }}

                function openChannel(name, title) {{
                    room = 'channel_' + name;
                    document.querySelectorAll('.nav-item').forEach(e=>e.style.background='');
                    event.target.style.background = 'rgba(124,58,237,0.2)';
                    loadMessages();
                    socket.emit('join', {{room}});
                }}

                function loadMessages() {{
                    fetch(`/messages/${{room}}`).then(r=>r.json()).then(msgs=>{{
                        const cont = document.getElementById('content');
                        cont.innerHTML = '';
                        let lastUser = null;
                        let group = null;

                        msgs.forEach(m=>{{
                            if (m.user !== lastUser) {{
                                group = document.createElement('div');
                                group.className = `message-group ${{m.user===user?'own':''}}`;
                                cont.appendChild(group);

                                const cluster = document.createElement('div');
                                cluster.className = `message-cluster ${{m.user===user?'own':''}}`;
                                group.appendChild(cluster);

                                const ava = document.createElement('div');
                                ava.className = 'message-avatar';
                                ava.textContent = m.user.slice(0,2).toUpperCase();
                                ava.style.backgroundColor = m.color;
                                if (m.avatar_path) ava.style.backgroundImage = `url(${{m.avatar_path}})`;
                                cluster.appendChild(ava);

                                const bubbles = document.createElement('div');
                                bubbles.className = 'message-bubbles';
                                cluster.appendChild(bubbles);
                                group.bubbles = bubbles;

                                if (m.user !== user) {{
                                    const sender = document.createElement('div');
                                    sender.style.fontWeight = '600';
                                    sender.style.marginBottom = '4px';
                                    sender.style.color = '#ccc';
                                    sender.textContent = m.user;
                                    bubbles.appendChild(sender);
                                }}
                            }}

                            const bubble = document.createElement('div');
                            bubble.className = `message-bubble ${{m.user===user?'own':''}}`;
                            if (m.message) bubble.innerHTML += `<div>${{m.message}}</div>`;
                            if (m.file) {{
                                if (m.type === 'video') bubble.innerHTML += `<video src="${{m.file}}" controls style="max-width:300px;border-radius:12px;"></video>`;
                                else bubble.innerHTML += `<img src="${{m.file}}" style="max-width:300px;border-radius:12px;">`;
                            }}
                            bubble.innerHTML += `<div style="font-size:0.75rem;margin-top:4px;text-align:right;color:rgba(255,255,255,0.7);">${{m.timestamp.slice(11,16)}}</div>`;
                            group.bubbles.appendChild(bubble);

                            lastUser = m.user;
                        }});

                        cont.scrollTop = cont.scrollHeight;  // ИСПРАВЛЕНО: правильный scrollTop
                    }});
                }}

                function send() {{
                    const input = document.getElementById('input');
                    const msg = input.value.trim();
                    const file = document.getElementById('file');
                    if (!msg && !file.files[0]) return;

                    if (file.files[0]) {{
                        const fd = new FormData();
                        fd.append('file', file.files[0]);
                        fetch('/upload', {{method:'POST', body:fd}}).then(r=>r.json()).then(d=>{{
                            if (d.success) socket.emit('msg', {{msg, room, file:d.path, type:d.type}});
                        }});
                    }} else {{
                        socket.emit('msg', {{msg, room}});
                    }}
                    input.value = ''; file.value = '';
                }}

                socket.on('msg', () => loadMessages());

                function createChannel() {{
                    const name = document.getElementById('cname').value.trim();
                    if (!name) return;
                    const safe = name.toLowerCase().replace(/\\s/g,'_').replace(/[^a-z0-9_]/g,'');
                    fetch('/create_channel', {{method:'POST',headers:{{'Content-Type':'application/json'}},body:JSON.stringify({{name:safe,display_name:name}})}})
                    .then(r=>r.json()).then(d=>{{
                        if (d.success) {{
                            document.getElementById('create-modal').style.display='none';
                            loadChannels();
                            openChannel(d.name, name);
                        }}
                    }});
                }}

                window.onload = () => {{
                    loadChannels();
                    openChannel('general', 'General');
                    document.getElementById('input').addEventListener('keydown', e=>{{
                        if (e.key==='Enter' && !e.shiftKey) {{e.preventDefault(); send();}}
                    }});
                }};
            </script>
        </body>
        </html>
        '''

    @app.route('/user_channels')
    def user_channels():
        if 'username' not in session: return jsonify({'channels':[]})
        return jsonify({'channels': get_user_channels(session['username'])})

    @app.route('/messages/<room>')
    def messages(room):
        if 'username' not in session: return jsonify([])
        return jsonify(get_messages(room))

    @app.route('/upload', methods=['POST'])
    def upload():
        if 'username' not in session: return jsonify({'success':False})
        file = request.files.get('file')
        if not file: return jsonify({'success':False})
        path, name = save_uploaded_file(file, app.config['UPLOAD_FOLDER'])
        if path:
            typ = 'video' if name.lower().endswith(('mp4','webm','mov')) else 'image'
            return jsonify({'success':True, 'path':path, 'type':typ})
        return jsonify({'success':False})

    @app.route('/create_channel', methods=['POST'])
    def create_ch():
        if 'username' not in session: return jsonify({'success':False})
        data = request.json
        name = data.get('name')
        display = data.get('display_name', name)
        if create_channel(name, display, '', session['username']):
            return jsonify({'success':True, 'name':name})
        return jsonify({'success':False})

    @app.route('/static/<path:p>')
    def static_files(p):
        return send_from_directory('static', p)

    # === SocketIO ===
    @socketio.on('join')
    def on_join(data):
        join_room(data['room'])

    @socketio.on('msg')
    def on_msg(data):
        msg = data.get('msg','')
        room = data.get('room')
        file = data.get('file')
        ftype = data.get('type','image')
        with sqlite3.connect('messenger.db') as conn:
            c = conn.cursor()
            c.execute('INSERT INTO messages (username, message, room, file_path, message_type) VALUES (?, ?, ?, ?, ?)',
                      (session['username'], msg, room, file, ftype))
            conn.commit()
        emit('msg', {}, room=room, include_self=False)

    return app

app = create_app()

if __name__ == '__main__':
    port = int(os.environ.get('PORT', 5000))
    socketio.run(app, host='0.0.0.0', port=port)
