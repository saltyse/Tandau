# web_messenger.py - Tandau Messenger (мобильная адаптация)
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
                .app-logo p {
                    opacity: 0.9;
                    font-size: 1.1rem;
                }
                .auth-box {
                    background: rgba(255, 255, 255, 0.95);
                    backdrop-filter: blur(10px);
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
                    background: white;
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
                    // Обновляем активные табы
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

                // Enter для отправки форм
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
        return f'''<!DOCTYPE html>
<html lang="ru" data-theme="{theme}">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0, user-scalable=no">
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
                font-size: 16px; /* Предотвращает zoom в iOS */
            }}
            
            .nav-item {{
                padding: 10px 12px;
                font-size: 0.9rem;
            }}
        }}
        
        /* Скрыть scrollbar но оставить функциональность */
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
                <button class="theme-toggle" onclick="toggleTheme()" title="Сменить тему">
                    <i class="fas fa-moon"></i>
                </button>
            </div>
            <div class="nav">
                <div class="nav-title">
                    <span>Каналы</span>
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
                <div id="private-chats">
                    <!-- Динамически заполняется -->
                </div>
                
                <div class="nav-title">
                    <span>Пользователи</span>
                </div>
                <div id="users">
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
            </div>
            <div class="messages" id="messages">
                <!-- Сообщения загружаются здесь -->
            </div>
            <div class="input-area">
                <div class="input-row">
                    <button onclick="document.getElementById('file').click()" style="background:none;border:none;font-size:1.2rem;cursor:pointer;color:var(--text);padding:5px;">
                        <i class="fas fa-paperclip"></i>
                    </button>
                    <input type="file" id="file" accept="image/*,video/*" style="display:none" onchange="previewFile(this)">
                    <textarea class="msg-input" id="msg-input" placeholder="Написать сообщение..." rows="1" onkeydown="handleKeydown(event)"></textarea>
                    <button class="send-btn" onclick="sendMessage()">
                        <i class="fas fa-paper-plane"></i>
                    </button>
                </div>
                <div id="file-preview"></div>
            </div>
        </div>
    </div>

    <script src="https://cdnjs.cloudflare.com/ajax/libs/socket.io/4.7.2/socket.io.js"></script>
    <script>
        const socket = io();
        const user = "{username}";
        let room = "channel_general", type = "channel";
        let isMobile = window.innerWidth <= 768;

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

        function toggleTheme() {{
            const currentTheme = document.documentElement.getAttribute('data-theme');
            const newTheme = currentTheme === 'light' ? 'dark' : 'light';
            fetch('/set_theme', {{
                method: 'POST',
                headers: {{ 'Content-Type': 'application/json' }},
                body: JSON.stringify({{ theme: newTheme }})
            }});
            document.documentElement.setAttribute('data-theme', newTheme);
        }}

        function sendMessage() {{
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
                    resetInput();
                }};
                reader.readAsDataURL(file);
            }} else {{
                socket.emit('message', data);
                resetInput();
            }}
        }}

        function resetInput() {{
            const input = document.getElementById('msg-input');
            const fileInput = document.getElementById('file');
            const preview = document.getElementById('file-preview');
            
            input.value = '';
            input.style.height = 'auto';
            fileInput.value = '';
            preview.innerHTML = '';
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
            type = t;
            
            document.getElementById('chat-title').textContent = title;
            document.querySelectorAll('.nav-item').forEach(el => el.classList.remove('active'));
            event.currentTarget.classList.add('active');
            
            document.getElementById('messages').innerHTML = '';
            
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

        function loadPrivateChats() {{
            fetch('/users').then(r => r.json()).then(users => {{
                const pc = document.getElementById('private-chats');
                pc.innerHTML = '';
                
                users.forEach(us => {{
                    if (us.online) {{
                        const el = document.createElement('div');
                        el.className = 'nav-item';
                        el.innerHTML = `
                            <i class="fas fa-user"></i>
                            <span>@${{us.username}}</span>
                        `;
                        el.onclick = () => openRoom(
                            `private_${{Math.min(user, us.username)}}_${{Math.max(user, us.username)}}`,
                            'private',
                            `@${{us.username}}`
                        );
                        pc.appendChild(el);
                    }}
                }});
            }});
        }}

        // Обновление списка пользователей
        setInterval(() => {{
            fetch('/users').then(r => r.json()).then(users => {{
                const usersContainer = document.getElementById('users');
                usersContainer.innerHTML = '';
                
                users.forEach(us => {{
                    const el = document.createElement('div');
                    el.className = 'nav-item';
                    el.innerHTML = `
                        <i class="fas fa-user${{us.online ? '-check' : ''}}"></i>
                        <span>${{us.username}}${{us.online ? ' (онлайн)' : ''}}</span>
                    `;
                    el.onclick = () => openRoom(
                        `private_${{Math.min(user, us.username)}}_${{Math.max(user, us.username)}}`,
                        'private',
                        `@${{us.username}}`
                    );
                    usersContainer.appendChild(el);
                }});
                
                loadPrivateChats();
            }});
        }}, 5000);

        // Инициализация
        loadPrivateChats();
        
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
