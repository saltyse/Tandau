# web_messenger.py - Tandau Messenger (исправленная версия для Render)
from flask import Flask, request, jsonify, session, redirect
from flask_socketio import SocketIO, emit, join_room, leave_room
import sqlite3
from datetime import datetime
from werkzeug.security import generate_password_hash, check_password_hash
from werkzeug.utils import secure_filename
import random
import os

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

    # Убираем async_mode или используем 'threading'
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
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                )
            ''')
            c.execute('''
                CREATE TABLE IF NOT EXISTS channel_members (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    channel_id INTEGER,
                    username TEXT NOT NULL,
                    joined_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    FOREIGN KEY (channel_id) REFERENCES channels (id),
                    UNIQUE(channel_id, username)
                )
            ''')
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

    # === Аватарки ===
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

    # === Темы ===
    @app.route('/set_theme', methods=['POST'])
    def set_theme():
        if 'username' not in session: return jsonify({'error': 'auth'})
        theme = request.json.get('theme', 'light')
        if theme not in ['light', 'dark']: return jsonify({'error': 'invalid'})
        with sqlite3.connect('messenger.db') as conn:
            c = conn.cursor(); c.execute('UPDATE users SET theme = ? WHERE username = ?', (theme, session['username'])); conn.commit()
        return jsonify({'success': True})

    # === Маршруты ===
    @app.route('/')
    def index():
        if 'username' in session: return redirect('/chat')
        return '''
        <!DOCTYPE html><html><head><title>Tandau - Вход</title><style>
            body{font-family:Arial;background:linear-gradient(135deg,#667eea,#764ba2);margin:0;display:flex;justify-content:center;align-items:center;height:100vh}
            .box{background:#fff;padding:40px;border-radius:12px;box-shadow:0 10px 30px rgba(0,0,0,.2);width:380px}
            input,button{width:100%;padding:12px;margin:8px 0;border-radius:8px;border:1px solid #ddd;font-size:16px}
            button{background:#667eea;color:#fff;border:none;cursor:pointer}
            .switch{text-align:center;margin-top:15px}
            .alert{padding:10px;background:#f8d7da;color:#721c24;border-radius:5px;margin:10px 0;display:none}
        </style></head><body>
        <div class="box"><h2>Tandau Messenger</h2><div id="a" class="alert"></div>
        <div id="login"><input id="lu" placeholder="Логин"><input id="lp" type="password" placeholder="Пароль">
        <button onclick="login()">Войти</button><div class="switch"><a href="#" onclick="showReg()">Регистрация</a></div></div>
        <div id="reg" style="display:none"><input id="ru" placeholder="Логин"><input id="rp" type="password" placeholder="Пароль">
        <input id="rc" type="password" placeholder="Повторить"><button onclick="reg()">Создать</button>
        <div class="switch"><a href="#" onclick="showLogin()">Войти</a></div></div></div>
        <script>
        function a(m,t='error'){const x=document.getElementById('a');x.textContent=m;x.className='alert';x.style.background=t==='success'?'#d4edda':'#f8d7da';x.style.display='block'}
        function showReg(){document.getElementById('login').style.display='none';document.getElementById('reg').style.display='block'}
        function showLogin(){document.getElementById('reg').style.display='none';document.getElementById('login').style.display='block'}
        async function login(){const u=document.getElementById('lu').value,p=document.getElementById('lp').value;if(!u||!p)return a('Заполните поля')
        const r=await fetch('/login',{method:'POST',body:new URLSearchParams({username:u,password:p})})
        const d=await r.json();d.success?location.href='/chat':a(d.error)}
        async function reg(){const u=document.getElementById('ru').value,p=document.getElementById('rp').value,c=document.getElementById('rc').value
        if(!u||!p||!c)return a('Заполните поля');if(p!==c)return a('Пароли не совпадают');if(u.length<3)return a('Логин ≥3 символа')
        const r=await fetch('/register',{method:'POST',body:new URLSearchParams({username:u,password:p})})
        const d=await r.json();d.success?a('Успешно! Вход...','success')||setTimeout(()=>location.href='/chat',1500):a(d.error)}
        </script></body></html>
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
        return f'''<!DOCTYPE html>
<html data-theme="{theme}">
<head>
    <meta charset="utf-8"><title>Tandau Chat</title>
    <link rel="stylesheet" href="https://cdnjs.cloudflare.com/ajax/libs/font-awesome/6.5.0/css/all.min.css">
    <style>
        :root{{--bg:#f8f9fa;--text:#333;--input:#fff;--border:#ddd;--accent:#667eea}}
        [data-theme="dark"]{{--bg:#1a1a1a;--text:#eee;--input:#2d2d2d;--border:#444;--accent:#8b5cf6}}
        body{{margin:0;font-family:Arial;background:var(--bg);color:var(--text);height:100vh;display:flex}}
        .sidebar{{width:300px;background:var(--input);border-right:1px solid var(--border);display:flex;flex-direction:column}}
        .header{{padding:15px;background:var(--accent);color:#fff;text-align:center}}
        .user-info{{padding:15px;display:flex;gap:10px;align-items:center;border-bottom:1px solid var(--border)}}
        .avatar{{width:40px;height:40px;border-radius:50%;background:var(--accent);color:#fff;display:flex;align-items:center;justify-content:center;font-weight:bold}}
        .nav{{flex:1;overflow-y:auto;padding:10px}}
        .nav-title{{padding:8px 15px;font-size:13px;color:#666;text-transform:uppercase;font-weight:bold;display:flex;justify-content:space-between;align-items:center}}
        .nav-item{{padding:10px 15px;cursor:pointer;border-radius:8px;margin:4px 0;transition:0.2s}}
        .nav-item:hover{{background:#f0f0f0}} [data-theme="dark"] .nav-item:hover{{background:#333}}
        .nav-item.active{{background:var(--accent);color:#fff}}
        .chat-area{{flex:1;display:flex;flex-direction:column}}
        .chat-header{{padding:15px;background:var(--input);border-bottom:1px solid var(--border);font-weight:bold}}
        .messages{{flex:1;padding:20px;overflow-y:auto}}
        .msg{{margin:10px 0;max-width:70%;padding:10px 15px;border-radius:18px;word-wrap:break-word}}
        .msg.own{{background:var(--accent);color:#fff;margin-left:auto}}
        .msg.other{{background:#e9ecef;color:#333}} [data-theme="dark"] .msg.other{{background:#333;color:#eee}}
        .input-area{{padding:15px;background:var(--input);border-top:1px solid var(--border)}}
        .input-row{{display:flex;gap:10px;align-items:center}}
        .msg-input{{flex:1;padding:12px;border:1px solid var(--border);border-radius:25px;background:var(--bg);color:var(--text)}}
        .send-btn{{width:44px;height:44px;border-radius:50%;background:var(--accent);color:#fff;border:none;cursor:pointer}}
        .file-preview{{margin:5px 0;max-width:200px;border-radius:8px}}
        .theme-toggle{{margin-left:auto;cursor:pointer;font-size:20px}}
    </style>
</head>
<body>
<div class="sidebar">
    <div class="header">Tandau</div>
    <div class="user-info">
        <div class="avatar" id="user-avatar">{username[:2].upper()}</div>
        <div><strong>{username}</strong><div style="font-size:12px">Online</div></div>
        <i class="fas fa-moon theme-toggle" onclick="toggleTheme()" title="Сменить тему"></i>
    </div>
    <div class="nav">
        <div class="nav-title">Каналы</div>
        <div id="channels"><div class="nav-item active" onclick="openRoom('channel_general', 'channel', '# general')"># general</div></div>
        <div class="nav-title">Личные чаты</div>
        <div id="private-chats"></div>
        <div class="nav-title">Пользователи</div>
        <div id="users"></div>
    </div>
    <button onclick="location.href='/logout'" style="margin:10px;padding:10px;background:#dc3545;color:#fff;border:none;border-radius:8px;cursor:pointer">Выйти</button>
</div>
<div class="chat-area">
    <div class="chat-header" id="chat-title"># general</div>
    <div class="messages" id="messages"></div>
    <div class="input-area">
        <div class="input-row">
            <button onclick="document.getElementById('file').click()" style="background:none;border:none;font-size:20px;cursor:pointer"><i class="fas fa-paperclip"></i></button>
            <input type="file" id="file" accept="image/*,video/*" style="display:none" onchange="previewFile(this)">
            <div id="file-preview"></div>
            <input type="text" class="msg-input" id="msg-input" placeholder="Сообщение..." onkeypress="if(event.key==='Enter')send()">
            <button class="send-btn" onclick="send()"><i class="fas fa-paper-plane"></i></button>
        </div>
    </div>
</div>

<script src="https://cdnjs.cloudflare.com/ajax/libs/socket.io/4.7.2/socket.io.js"></script>
<script>
    const socket = io();
    const user = "{username}";
    let room = "channel_general", type = "channel";

    socket.emit('join', {{ room: 'channel_general' }});

    function toggleTheme() {{
        const t = document.documentElement.getAttribute('data-theme') === 'light' ? 'dark' : 'light';
        fetch('/set_theme', {{method:'POST',headers:{{'Content-Type':'application/json'}},body:JSON.stringify({{theme:t}})}});
        document.documentElement.setAttribute('data-theme', t);
    }}

    function send() {{
        const input = document.getElementById('msg-input');
        const msg = input.value.trim();
        const file = document.getElementById('file').files[0];
        if (!msg && !file) return;
        const data = {{ message: msg, room: room, type: type }};
        if (file) {{
            const reader = new FileReader();
            reader.onload = (e) => {{
                data.file = e.target.result;
                data.fileType = file.type.startsWith('image/') ? 'image' : 'video';
                socket.emit('message', data);
            }};
            reader.readAsDataURL(file);
        }} else {{
            socket.emit('message', data);
        }}
        input.value = ''; document.getElementById('file').value = ''; document.getElementById('file-preview').innerHTML = '';
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
                img.src = e.target.result; img.className = 'file-preview';
                prev.appendChild(img);
            }} else {{
                const vid = document.createElement('video');
                vid.src = e.target.result; vid.controls = true; vid.className = 'file-preview';
                prev.appendChild(vid);
            }}
        }};
        reader.readAsDataURL(file);
    }}

    socket.on('message', (data) => {{
        const msg = document.createElement('div');
        msg.className = `msg ${{data.user === user ? 'own' : 'other'}}`;
        let content = `<strong>${{data.user}}</strong> <small>${{data.timestamp || ''}}</small><br>`;
        if (data.file) {{
            if (data.fileType === 'image') content += `<img src="${{data.file}}" class="file-preview">`;
            else content += `<video src="${{data.file}}" controls class="file-preview"></video>`;
        }}
        if (data.message) content += data.message.replace(/\\n/g, '<br>');
        msg.innerHTML = content;
        document.getElementById('messages').appendChild(msg);
        document.getElementById('messages').scrollTop = document.getElementById('messages').scrollHeight;
    }});

    function openRoom(r, t, title) {{
        room = r; type = t;
        document.getElementById('chat-title').textContent = title;
        document.querySelectorAll('.nav-item').forEach(el => el.classList.remove('active'));
        event.target.classList.add('active');
        document.getElementById('messages').innerHTML = '';
        
        // Загружаем историю сообщений
        fetch(`/get_messages/${{r}}`)
            .then(r => r.json())
            .then(messages => {{
                messages.forEach(msg => {{
                    const msgEl = document.createElement('div');
                    msgEl.className = `msg ${{msg.user === user ? 'own' : 'other'}}`;
                    let content = `<strong>${{msg.user}}</strong> <small>${{msg.timestamp}}</small><br>`;
                    if (msg.file) {{
                        if (msg.type === 'image') content += `<img src="${{msg.file}}" class="file-preview">`;
                        else content += `<video src="${{msg.file}}" controls class="file-preview"></video>`;
                    }}
                    if (msg.message) content += msg.message.replace(/\\n/g, '<br>');
                    msgEl.innerHTML = content;
                    document.getElementById('messages').appendChild(msgEl);
                }});
                document.getElementById('messages').scrollTop = document.getElementById('messages').scrollHeight;
            }});
        
        socket.emit('join', {{ room: r }});
    }}

    setInterval(() => {{
        fetch('/users').then(r => r.json()).then(users => {{
            const u = document.getElementById('users');
            u.innerHTML = '';
            users.forEach(us => {{
                const el = document.createElement('div');
                el.className = 'nav-item';
                el.textContent = us.username + (us.online ? ' (онлайн)' : '');
                el.onclick = () => openRoom(`private_${{Math.min(user, us.username)}}_${{Math.max(user, us.username)}}`, 'private', `@${{us.username}}`);
                u.appendChild(el);
            }});
        }});
    }}, 3000);
</script>
</body></html>'''

    @app.route('/users')
    def users_route():
        return jsonify(get_all_users())

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
        
        # Сохраняем в БД
        msg_id = save_message(
            session['username'], 
            msg, 
            room, 
            None, 
            file_type, 
            file
        )
        
        # Отправляем всем в комнате
        emit('message', {
            'user': session['username'], 
            'message': msg, 
            'file': file, 
            'fileType': file_type,
            'timestamp': datetime.now().strftime('%H:%M')
        }, room=room)

    return app

# === СОЗДАНИЕ ПРИЛОЖЕНИЯ ДЛЯ RENDER ===
app = create_app()
socketio = app.extensions['socketio']

if __name__ == '__main__':
    port = int(os.environ.get('PORT', 5000))
    socketio.run(app, host='0.0.0.0', port=port, debug=False)
