# web_messenger.py - Tandau Messenger с жидким стеклом
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
import json

# === Фабрика приложения ===
def create_app():
    app = Flask(__name__)
    app.config['SECRET_KEY'] = os.environ.get('SECRET_KEY', 'tandau-secret-key-2024')
    app.config['UPLOAD_FOLDER'] = 'static/uploads'
    app.config['AVATAR_FOLDER'] = 'static/avatars'
    app.config['FAVORITE_FOLDER'] = 'static/favorites'
    app.config['MAX_CONTENT_LENGTH'] = 16 * 1024 * 1024  # 16MB
    ALLOWED_EXT = {'png', 'jpg', 'jpeg', 'gif', 'webp', 'mp4', 'webm', 'mov', 'txt', 'pdf', 'doc', 'docx'}

    # Создаем папки для загрузок
    try:
        os.makedirs(app.config['UPLOAD_FOLDER'], exist_ok=True)
        os.makedirs(app.config['AVATAR_FOLDER'], exist_ok=True)
        os.makedirs(app.config['FAVORITE_FOLDER'], exist_ok=True)
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
                    file_name TEXT,
                    is_favorite BOOLEAN DEFAULT FALSE
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
                CREATE TABLE IF NOT EXISTS favorites (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    username TEXT NOT NULL,
                    content TEXT,
                    file_path TEXT,
                    file_name TEXT,
                    file_type TEXT DEFAULT 'text',
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    is_pinned BOOLEAN DEFAULT FALSE,
                    category TEXT DEFAULT 'general'
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
            row = c.fetchone()
            if row:
                return {
                    'id': row[0],
                    'username': row[1],
                    'password_hash': row[2],
                    'created_at': row[3],
                    'is_online': row[4],
                    'avatar_color': row[5],
                    'avatar_path': row[6],
                    'theme': row[7]
                }
            return None

    def get_all_users():
        with sqlite3.connect('messenger.db') as conn:
            c = conn.cursor()
            c.execute('SELECT username, is_online, avatar_color, avatar_path, theme FROM users ORDER BY username')
            return [dict(zip(['username','online','color','avatar','theme'], row)) for row in c.fetchall()]

    def get_users_except(username):
        with sqlite3.connect('messenger.db') as conn:
            c = conn.cursor()
            c.execute('SELECT username FROM users WHERE username != ? ORDER BY username', (username,))
            return [row[0] for row in c.fetchall()]

    def create_user(username, password):
        with sqlite3.connect('messenger.db') as conn:
            c = conn.cursor()
            try:
                # Проверяем, существует ли пользователь
                c.execute('SELECT id FROM users WHERE username = ?', (username,))
                if c.fetchone():
                    return False, "Пользователь уже существует"
                
                # Создаем пользователя
                c.execute('INSERT INTO users (username, password_hash, avatar_color) VALUES (?, ?, ?)',
                          (username, generate_password_hash(password), random.choice(['#6366F1','#8B5CF6','#10B981','#F59E0B','#EF4444','#3B82F6'])))
                
                # Добавляем пользователя в общий канал
                c.execute('INSERT OR IGNORE INTO channel_members (channel_id, username) SELECT id, ? FROM channels WHERE name="general"', (username,))
                conn.commit()
                return True, "Пользователь создан успешно"
            except Exception as e:
                return False, f"Ошибка при создании пользователя: {str(e)}"

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

    def save_message(user, msg, room, recipient=None, msg_type='text', file_path=None, file_name=None, is_favorite=False):
        with sqlite3.connect('messenger.db') as conn:
            c = conn.cursor()
            c.execute('INSERT INTO messages (username, message, room, recipient, message_type, file_path, file_name, is_favorite) VALUES (?, ?, ?, ?, ?, ?, ?, ?)',
                      (user, msg, room, recipient, msg_type, file_path, file_name, is_favorite))
            conn.commit()
            return c.lastrowid

    def add_to_favorites(username, content=None, file_path=None, file_name=None, file_type='text', category='general'):
        with sqlite3.connect('messenger.db') as conn:
            c = conn.cursor()
            try:
                c.execute('INSERT INTO favorites (username, content, file_path, file_name, file_type, category) VALUES (?, ?, ?, ?, ?, ?)',
                          (username, content, file_path, file_name, file_type, category))
                conn.commit()
                return c.lastrowid
            except Exception as e:
                print(f"Error adding to favorites: {e}")
                return None

    def get_favorites(username, category=None):
        with sqlite3.connect('messenger.db') as conn:
            c = conn.cursor()
            if category:
                c.execute('''
                    SELECT id, content, file_path, file_name, file_type, created_at, is_pinned, category 
                    FROM favorites 
                    WHERE username = ? AND category = ?
                    ORDER BY is_pinned DESC, created_at DESC
                ''', (username, category))
            else:
                c.execute('''
                    SELECT id, content, file_path, file_name, file_type, created_at, is_pinned, category 
                    FROM favorites 
                    WHERE username = ? 
                    ORDER BY is_pinned DESC, created_at DESC
                ''', (username,))
            
            favorites = []
            for row in c.fetchall():
                favorites.append({
                    'id': row[0],
                    'content': row[1],
                    'file_path': row[2],
                    'file_name': row[3],
                    'file_type': row[4],
                    'created_at': row[5],
                    'is_pinned': bool(row[6]),
                    'category': row[7]
                })
            return favorites

    def delete_favorite(favorite_id, username):
        with sqlite3.connect('messenger.db') as conn:
            c = conn.cursor()
            c.execute('DELETE FROM favorites WHERE id = ? AND username = ?', (favorite_id, username))
            conn.commit()
            return c.rowcount > 0

    def toggle_pin_favorite(favorite_id, username):
        with sqlite3.connect('messenger.db') as conn:
            c = conn.cursor()
            # Получаем текущее состояние
            c.execute('SELECT is_pinned FROM favorites WHERE id = ? AND username = ?', (favorite_id, username))
            row = c.fetchone()
            if row:
                new_state = not bool(row[0])
                c.execute('UPDATE favorites SET is_pinned = ? WHERE id = ? AND username = ?', 
                         (new_state, favorite_id, username))
                conn.commit()
                return new_state
            return None

    def get_favorite_categories(username):
        with sqlite3.connect('messenger.db') as conn:
            c = conn.cursor()
            c.execute('SELECT DISTINCT category FROM favorites WHERE username = ? ORDER BY category', (username,))
            return [row[0] for row in c.fetchall()]

    def get_messages_for_room(room):
        with sqlite3.connect('messenger.db') as conn:
            c = conn.cursor()
            c.execute('''
                SELECT username, message, message_type, file_path, file_name, timestamp 
                FROM messages 
                WHERE room = ? 
                ORDER BY timestamp ASC
            ''', (room,))
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
                # Проверяем права администратора
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

    def add_user_to_channel(channel_name, target_user, current_user):
        with sqlite3.connect('messenger.db') as conn:
            c = conn.cursor()
            try:
                # Проверяем права администратора
                c.execute('''
                    SELECT c.id FROM channels c 
                    JOIN channel_members cm ON c.id = cm.channel_id 
                    WHERE c.name = ? AND cm.username = ? AND cm.is_admin = 1
                ''', (channel_name, current_user))
                
                row = c.fetchone()
                if not row:
                    return False, "У вас нет прав администратора"
                
                channel_id = row[0]
                
                # Проверяем, существует ли пользователь
                c.execute('SELECT 1 FROM users WHERE username = ?', (target_user,))
                if not c.fetchone():
                    return False, "Пользователь не найден"
                
                # Проверяем, не является ли пользователь уже участником
                c.execute('SELECT 1 FROM channel_members WHERE channel_id = ? AND username = ?', (channel_id, target_user))
                if c.fetchone():
                    return False, "Пользователь уже в канале"
                
                # Добавляем пользователя
                c.execute('INSERT INTO channel_members (channel_id, username, is_admin) VALUES (?, ?, ?)',
                          (channel_id, target_user, False))
                conn.commit()
                return True, "Пользователь добавлен"
            except Exception as e:
                return False, f"Ошибка: {str(e)}"

    def remove_user_from_channel(channel_name, target_user, current_user):
        with sqlite3.connect('messenger.db') as conn:
            c = conn.cursor()
            try:
                # Проверяем права администратора
                c.execute('''
                    SELECT c.id FROM channels c 
                    JOIN channel_members cm ON c.id = cm.channel_id 
                    WHERE c.name = ? AND cm.username = ? AND cm.is_admin = 1
                ''', (channel_name, current_user))
                
                row = c.fetchone()
                if not row:
                    return False, "У вас нет прав администратора"
                
                channel_id = row[0]
                
                # Нельзя удалить самого себя
                if target_user == current_user:
                    return False, "Нельзя удалить самого себя"
                
                # Проверяем, существует ли пользователь в канале
                c.execute('SELECT 1 FROM channel_members WHERE channel_id = ? AND username = ?', (channel_id, target_user))
                if not c.fetchone():
                    return False, "Пользователь не найден в канале"
                
                # Удаляем пользователя
                c.execute('DELETE FROM channel_members WHERE channel_id = ? AND username = ?', (channel_id, target_user))
                conn.commit()
                return True, "Пользователь удален"
            except Exception as e:
                return False, f"Ошибка: {str(e)}"

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
            return jsonify({'success': False, 'error': 'Не авторизован'})
        
        if 'avatar' in request.files:
            file = request.files['avatar']
            path, filename = save_uploaded_file(file, app.config['AVATAR_FOLDER'])
        else:
            return jsonify({'success': False, 'error': 'Файл не найден'})
        
        if path:
            with sqlite3.connect('messenger.db') as conn:
                c = conn.cursor()
                c.execute('UPDATE users SET avatar_path = ? WHERE username = ?', (path, session['username']))
                conn.commit()
            return jsonify({'success': True, 'path': path})
        return jsonify({'success': False, 'error': 'Неверный формат файла'})

    @app.route('/delete_avatar', methods=['POST'])
    def delete_avatar_handler():
        if 'username' not in session: 
            return jsonify({'success': False, 'error': 'Не авторизован'})
        with sqlite3.connect('messenger.db') as conn:
            c = conn.cursor()
            c.execute('UPDATE users SET avatar_path = NULL WHERE username = ?', (session['username'],))
            conn.commit()
        return jsonify({'success': True})

    @app.route('/set_theme', methods=['POST'])
    def set_theme_handler():
        if 'username' not in session: 
            return jsonify({'success': False, 'error': 'Не авторизован'})
        theme = request.json.get('theme', 'light')
        if theme not in ['light', 'dark', 'auto']: 
            return jsonify({'success': False, 'error': 'Неверная тема'})
        with sqlite3.connect('messenger.db') as conn:
            c = conn.cursor()
            c.execute('UPDATE users SET theme = ? WHERE username = ?', (theme, session['username']))
            conn.commit()
        return jsonify({'success': True})

    @app.route('/create_channel', methods=['POST'])
    def create_channel_handler():
        if 'username' not in session: 
            return jsonify({'success': False, 'error': 'Не авторизован'})
        
        name = request.json.get('name', '').strip()
        display_name = request.json.get('display_name', '').strip()
        description = request.json.get('description', '').strip()
        is_private = request.json.get('is_private', False)
        
        if not name or len(name) < 2:
            return jsonify({'success': False, 'error': 'Название канала должно быть не менее 2 символов'})
        
        if not display_name:
            display_name = name.capitalize()
        
        channel_id = create_channel(name, display_name, description, session['username'], is_private)
        if channel_id:
            return jsonify({'success': True, 'channel_name': name, 'display_name': display_name})
        return jsonify({'success': False, 'error': 'Канал с таким названием уже существует'})

    @app.route('/rename_channel', methods=['POST'])
    def rename_channel_handler():
        if 'username' not in session: 
            return jsonify({'success': False, 'error': 'Не авторизован'})
        
        channel_name = request.json.get('channel_name')
        new_display_name = request.json.get('new_display_name', '').strip()
        
        if not new_display_name:
            return jsonify({'success': False, 'error': 'Новое название не может быть пустым'})
        
        if rename_channel(channel_name, new_display_name, session['username']):
            return jsonify({'success': True})
        return jsonify({'success': False, 'error': 'Не удалось переименовать канал или нет прав'})

    @app.route('/add_user_to_channel', methods=['POST'])
    def add_user_to_channel_handler():
        if 'username' not in session: 
            return jsonify({'success': False, 'error': 'Не авторизован'})
        
        channel_name = request.json.get('channel_name')
        target_user = request.json.get('username', '').strip()
        
        if not channel_name or not target_user:
            return jsonify({'success': False, 'error': 'Не указан канал или пользователь'})
        
        success, message = add_user_to_channel(channel_name, target_user, session['username'])
        return jsonify({'success': success, 'message': message})

    @app.route('/remove_user_from_channel', methods=['POST'])
    def remove_user_from_channel_handler():
        if 'username' not in session: 
            return jsonify({'success': False, 'error': 'Не авторизован'})
        
        channel_name = request.json.get('channel_name')
        target_user = request.json.get('username', '').strip()
        
        if not channel_name or not target_user:
            return jsonify({'success': False, 'error': 'Не указан канал или пользователь'})
        
        success, message = remove_user_from_channel(channel_name, target_user, session['username'])
        return jsonify({'success': success, 'message': message})

    @app.route('/channel_info/<channel_name>')
    def channel_info_handler(channel_name):
        if 'username' not in session: 
            return jsonify({'success': False, 'error': 'Не авторизован'})
        info = get_channel_info(channel_name)
        if info:
            info['is_member'] = is_channel_member(channel_name, session['username'])
            info['members'] = get_channel_members(channel_name)
            return jsonify({'success': True, 'data': info})
        return jsonify({'success': False, 'error': 'Канал не найден'})

    @app.route('/get_available_users')
    def get_available_users_handler():
        if 'username' not in session: 
            return jsonify({'success': False, 'error': 'Не авторизован'})
        
        channel_name = request.args.get('channel_name')
        if not channel_name:
            return jsonify({'success': False, 'error': 'Не указан канал'})
        
        # Получаем всех пользователей, кроме уже состоящих в канале
        with sqlite3.connect('messenger.db') as conn:
            c = conn.cursor()
            c.execute('''
                SELECT username 
                FROM users 
                WHERE username != ? 
                AND username NOT IN (
                    SELECT cm.username 
                    FROM channel_members cm 
                    JOIN channels c ON cm.channel_id = c.id 
                    WHERE c.name = ?
                )
                ORDER BY username
            ''', (session['username'], channel_name))
            
            users = [row[0] for row in c.fetchall()]
            return jsonify({'success': True, 'users': users})

    @app.route('/user_channels')
    def user_channels_handler():
        if 'username' not in session: 
            return jsonify({'success': False, 'error': 'Не авторизован'})
        return jsonify({'success': True, 'channels': get_user_channels(session['username'])})

    @app.route('/personal_chats')
    def personal_chats_handler():
        if 'username' not in session: 
            return jsonify({'success': False, 'error': 'Не авторизован'})
        return jsonify({'success': True, 'chats': get_user_personal_chats(session['username'])})

    @app.route('/user_info/<username>')
    def user_info_handler(username):
        if 'username' not in session: 
            return jsonify({'success': False, 'error': 'Не авторизован'})
        
        user = get_user(username)
        if user:
            return jsonify({
                'success': True,
                'username': user['username'],
                'online': user['is_online'],
                'avatar_color': user['avatar_color'],
                'avatar_path': user['avatar_path'],
                'theme': user['theme']
            })
        return jsonify({'success': False, 'error': 'Пользователь не найден'})

    # Избранное
    @app.route('/add_to_favorites', methods=['POST'])
    def add_to_favorites_handler():
        if 'username' not in session:
            return jsonify({'success': False, 'error': 'Не авторизован'})
        
        content = request.form.get('content', '').strip()
        category = request.form.get('category', 'general').strip()
        
        file_path = None
        file_name = None
        file_type = 'text'
        
        if 'file' in request.files:
            file = request.files['file']
            if file and file.filename:
                path, filename = save_uploaded_file(file, app.config['FAVORITE_FOLDER'])
                if path:
                    file_path = path
                    file_name = filename
                    file_type = 'file'
                    content = content or f"Файл: {filename}"
        
        elif request.is_json:
            data = request.json
            content = data.get('content', '').strip()
            category = data.get('category', 'general').strip()
            file_data = data.get('file')
            
            if file_data:
                file_type = data.get('fileType', 'image')
                file_extension = 'png' if file_type == 'image' else 'mp4'
                path, filename = save_base64_file(file_data, app.config['FAVORITE_FOLDER'], file_extension)
                if path:
                    file_path = path
                    file_name = filename
                    content = content or f"Медиа файл"
        
        favorite_id = add_to_favorites(
            session['username'],
            content,
            file_path,
            file_name,
            file_type,
            category
        )
        
        if favorite_id:
            return jsonify({'success': True, 'id': favorite_id})
        return jsonify({'success': False, 'error': 'Не удалось добавить в избранное'})

    @app.route('/get_favorites')
    def get_favorites_handler():
        if 'username' not in session:
            return jsonify({'success': False, 'error': 'Не авторизован'})
        
        category = request.args.get('category', None)
        favorites = get_favorites(session['username'], category)
        return jsonify({'success': True, 'favorites': favorites})

    @app.route('/get_favorite_categories')
    def get_favorite_categories_handler():
        if 'username' not in session:
            return jsonify({'success': False, 'error': 'Не авторизован'})
        
        categories = get_favorite_categories(session['username'])
        return jsonify({'success': True, 'categories': categories})

    @app.route('/delete_favorite/<int:favorite_id>', methods=['DELETE'])
    def delete_favorite_handler(favorite_id):
        if 'username' not in session:
            return jsonify({'success': False, 'error': 'Не авторизован'})
        
        if delete_favorite(favorite_id, session['username']):
            return jsonify({'success': True})
        return jsonify({'success': False, 'error': 'Не удалось удалить'})

    @app.route('/toggle_pin_favorite/<int:favorite_id>', methods=['POST'])
    def toggle_pin_favorite_handler(favorite_id):
        if 'username' not in session:
            return jsonify({'success': False, 'error': 'Не авторизован'})
        
        new_state = toggle_pin_favorite(favorite_id, session['username'])
        if new_state is not None:
            return jsonify({'success': True, 'pinned': new_state})
        return jsonify({'success': False, 'error': 'Не удалось закрепить/открепить'})

    # Статические файлы
    @app.route('/static/<path:filename>')
    def static_files(filename):
        return send_from_directory('static', filename)

    @app.route('/create_docs_folder', methods=['POST'])
    def create_docs_folder():
        try:
            # Создаем папку для документов
            docs_folder = 'static/docs'
            os.makedirs(docs_folder, exist_ok=True)
            
            # Создаем пример PDF файла Условий использования
            terms_file = os.path.join(docs_folder, 'terms_of_use.pdf')
            if not os.path.exists(terms_file):
                # Создаем простой текстовый файл (в реальном приложении здесь был бы PDF)
                with open(terms_file, 'w', encoding='utf-8') as f:
                    f.write('Tandau Messenger - Условия использования\n\n')
                    f.write('Это демонстрационный файл. В реальном приложении здесь был бы PDF документ.\n')
                
            # Создаем пример PDF файла Политики конфиденциальности
            privacy_file = os.path.join(docs_folder, 'privacy_policy.pdf')
            if not os.path.exists(privacy_file):
                # Создаем простой текстовый файл
                with open(privacy_file, 'w', encoding='utf-8') as f:
                    f.write('Tandau Messenger - Политика конфиденциальности\n\n')
                    f.write('Это демонстрационный файл. В реальном приложении здесь был бы PDF документ.\n')
            
            return jsonify({'success': True, 'message': 'Documents folder created'})
        except Exception as e:
            return jsonify({'success': False, 'error': str(e)})

    # === Основные маршруты ===
    @app.route('/')
    def index():
        if 'username' in session: 
            return redirect('/chat')
        
        # Современная страница входа/регистрации с логотипом (НЕ ИЗМЕНЯЕМ)
        return '''
        <!DOCTYPE html>
        <html lang="ru">
        <head>
            <meta charset="UTF-8">
            <meta name="viewport" content="width=device-width, initial-scale=1.0">
            <title>Tandau Messenger</title>
            <link rel="stylesheet" href="https://cdnjs.cloudflare.com/ajax/libs/font-awesome/6.5.0/css/all.min.css">
            <style>
                * {
                    margin: 0;
                    padding: 0;
                    box-sizing: border-box;
                    font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif;
                }
                
                :root {
                    --primary: #6366f1;
                    --primary-dark: #4f46e5;
                    --primary-light: #818cf8;
                    --secondary: #8b5cf6;
                    --accent: #10b981;
                    --text: #1f2937;
                    --text-light: #6b7280;
                    --bg: #f9fafb;
                    --bg-light: #ffffff;
                    --border: #e5e7eb;
                    --shadow: 0 10px 25px -5px rgba(0, 0, 0, 0.1), 0 10px 10px -5px rgba(0, 0, 0, 0.04);
                    --radius: 16px;
                    --radius-sm: 10px;
                    --transition: all 0.3s cubic-bezier(0.4, 0, 0.2, 1);
                }
                
                body {
                    background: linear-gradient(135deg, #667eea 0%, #764ba2 100%);
                    min-height: 100vh;
                    display: flex;
                    align-items: center;
                    justify-content: center;
                    padding: 20px;
                }
                
                .container {
                    width: 100%;
                    max-width: 440px;
                }
                
                .logo-section {
                    text-align: center;
                    margin-bottom: 40px;
                    animation: fadeInDown 0.8s ease-out;
                }
                
                .logo-container {
                    display: inline-flex;
                    align-items: center;
                    justify-content: center;
                    gap: 15px;
                    background: rgba(255, 255, 255, 0.1);
                    backdrop-filter: blur(10px);
                    padding: 20px 40px;
                    border-radius: 24px;
                    margin-bottom: 25px;
                    box-shadow: 0 8px 32px rgba(0, 0, 0, 0.1);
                    border: 1px solid rgba(255, 255, 255, 0.2);
                }
                
                .logo-placeholder {
                    width: 60px;
                    height: 60px;
                    border-radius: 16px;
                    background: linear-gradient(135deg, #667eea, #764ba2);
                    display: flex;
                    align-items: center;
                    justify-content: center;
                    color: white;
                    font-size: 28px;
                    font-weight: bold;
                    box-shadow: 0 4px 12px rgba(0, 0, 0, 0.15);
                }
                
                .app-title {
                    color: white;
                    font-size: 2.8rem;
                    font-weight: 800;
                    letter-spacing: -0.5px;
                    text-shadow: 0 2px 10px rgba(0, 0, 0, 0.2);
                }
                
                .app-subtitle {
                    color: rgba(255, 255, 255, 0.9);
                    font-size: 1.1rem;
                    font-weight: 400;
                    max-width: 300px;
                    margin: 0 auto;
                    line-height: 1.5;
                }
                
                .auth-card {
                    background: var(--bg-light);
                    border-radius: var(--radius);
                    box-shadow: var(--shadow);
                    overflow: hidden;
                    animation: fadeInUp 0.8s ease-out 0.2s both;
                }
                
                .auth-header {
                    display: flex;
                    background: white;
                    border-bottom: 1px solid var(--border);
                }
                
                .auth-tab {
                    flex: 1;
                    padding: 20px;
                    text-align: center;
                    font-weight: 600;
                    font-size: 1.1rem;
                    color: var(--text-light);
                    cursor: pointer;
                    transition: var(--transition);
                    position: relative;
                    user-select: none;
                }
                
                .auth-tab:hover {
                    color: var(--primary);
                    background: rgba(99, 102, 241, 0.05);
                }
                
                .auth-tab.active {
                    color: var(--primary);
                }
                
                .auth-tab.active::after {
                    content: '';
                    position: absolute;
                    bottom: 0;
                    left: 20%;
                    right: 20%;
                    height: 3px;
                    background: var(--primary);
                    border-radius: 3px;
                }
                
                .auth-content {
                    padding: 40px;
                }
                
                .auth-form {
                    display: none;
                    animation: fadeIn 0.5s ease-out;
                }
                
                .auth-form.active {
                    display: block;
                }
                
                .form-group {
                    margin-bottom: 24px;
                }
                
                .form-label {
                    display: block;
                    margin-bottom: 8px;
                    color: var(--text);
                    font-weight: 500;
                    font-size: 0.95rem;
                }
                
                .input-with-icon {
                    position: relative;
                }
                
                .input-icon {
                    position: absolute;
                    left: 16px;
                    top: 50%;
                    transform: translateY(-50%);
                    color: var(--text-light);
                    font-size: 1.1rem;
                }
                
                .form-input {
                    width: 100%;
                    padding: 16px 16px 16px 48px;
                    border: 2px solid var(--border);
                    border-radius: var(--radius-sm);
                    font-size: 1rem;
                    transition: var(--transition);
                    background: white;
                }
                
                .form-input:focus {
                    outline: none;
                    border-color: var(--primary);
                    box-shadow: 0 0 0 3px rgba(99, 102, 241, 0.1);
                }
                
                .password-toggle {
                    position: absolute;
                    right: 16px;
                    top: 50%;
                    transform: translateY(-50%);
                    background: none;
                    border: none;
                    color: var(--text-light);
                    cursor: pointer;
                    font-size: 1.1rem;
                }
                
                .btn {
                    width: 100%;
                    padding: 16px;
                    border: none;
                    border-radius: var(--radius-sm);
                    font-size: 1rem;
                    font-weight: 600;
                    cursor: pointer;
                    transition: var(--transition);
                    display: flex;
                    align-items: center;
                    justify-content: center;
                    gap: 10px;
                }
                
                .btn-primary {
                    background: var(--primary);
                    color: white;
                }
                
                .btn-primary:hover {
                    background: var(--primary-dark);
                    transform: translateY(-2px);
                    box-shadow: 0 6px 20px rgba(99, 102, 241, 0.3);
                }
                
                .btn-primary:active {
                    transform: translateY(0);
                }
                
                .btn-google {
                    background: white;
                    color: var(--text);
                    border: 2px solid var(--border);
                    margin-top: 16px;
                }
                
                .btn-google:hover {
                    background: var(--bg);
                    border-color: var(--text-light);
                }
                
                .alert {
                    padding: 14px 18px;
                    border-radius: var(--radius-sm);
                    margin-bottom: 24px;
                    display: none;
                    animation: slideIn 0.3s ease-out;
                }
                
                .alert-error {
                    background: #fee;
                    color: #c33;
                    border-left: 4px solid #c33;
                }
                
                .alert-success {
                    background: #efe;
                    color: #363;
                    border-left: 4px solid #363;
                }
                
                .terms {
                    text-align: center;
                    margin-top: 24px;
                    color: var(--text-light);
                    font-size: 0.9rem;
                }
                
                .terms a {
                    color: var(--primary);
                    text-decoration: none;
                    cursor: pointer;
                }
                
                .terms a:hover {
                    text-decoration: underline;
                }
                
                /* Стили для модального окна */
                .modal-overlay {
                    display: none;
                    position: fixed;
                    top: 0;
                    left: 0;
                    width: 100%;
                    height: 100%;
                    background: rgba(0, 0, 0, 0.7);
                    backdrop-filter: blur(8px);
                    -webkit-backdrop-filter: blur(8px);
                    z-index: 1000;
                    animation: fadeIn 0.3s ease-out;
                    align-items: center;
                    justify-content: center;
                    padding: 20px;
                }
                
                .terms-modal {
                    background: white;
                    border-radius: var(--radius);
                    box-shadow: 0 20px 60px rgba(0, 0, 0, 0.3);
                    max-width: 800px;
                    width: 100%;
                    max-height: 85vh;
                    overflow: hidden;
                    animation: slideUp 0.4s cubic-bezier(0.4, 0, 0.2, 1);
                }
                
                .modal-header {
                    padding: 24px 30px;
                    background: linear-gradient(135deg, #667eea 0%, #764ba2 100%);
                    color: white;
                    display: flex;
                    justify-content: space-between;
                    align-items: center;
                }
                
                .modal-header h2 {
                    font-size: 1.5rem;
                    font-weight: 700;
                    margin: 0;
                }
                
                .close-modal {
                    background: rgba(255, 255, 255, 0.2);
                    border: none;
                    color: white;
                    width: 36px;
                    height: 36px;
                    border-radius: 50%;
                    display: flex;
                    align-items: center;
                    justify-content: center;
                    cursor: pointer;
                    transition: all 0.2s ease;
                }
                
                .close-modal:hover {
                    background: rgba(255, 255, 255, 0.3);
                    transform: rotate(90deg);
                }
                
                .modal-content {
                    padding: 30px;
                    overflow-y: auto;
                    max-height: calc(85vh - 100px);
                }
                
                /* Стили для блока "жидкое стекло" - Условия использования */
                .glass-terms-container {
                    background: rgba(255, 255, 255, 0.1);
                    backdrop-filter: blur(20px);
                    -webkit-backdrop-filter: blur(20px);
                    border-radius: 24px;
                    border: 1px solid rgba(255, 255, 255, 0.2);
                    padding: 40px;
                    margin: 20px 0;
                    box-shadow: 
                        0 20px 60px rgba(0, 0, 0, 0.15),
                        inset 0 1px 0 rgba(255, 255, 255, 0.2);
                    position: relative;
                    overflow: hidden;
                }
                
                .glass-terms-container::before {
                    content: '';
                    position: absolute;
                    top: 0;
                    left: 0;
                    right: 0;
                    height: 1px;
                    background: linear-gradient(90deg, transparent, rgba(255, 255, 255, 0.4), transparent);
                }
                
                .glass-header {
                    text-align: center;
                    margin-bottom: 40px;
                    position: relative;
                    padding-bottom: 30px;
                }
                
                .glass-header::after {
                    content: '';
                    position: absolute;
                    bottom: 0;
                    left: 25%;
                    right: 25%;
                    height: 2px;
                    background: linear-gradient(90deg, transparent, #667eea, #764ba2, transparent);
                    border-radius: 2px;
                }
                
                .glass-icon {
                    width: 80px;
                    height: 80px;
                    margin: 0 auto 25px;
                    background: linear-gradient(135deg, rgba(102, 126, 234, 0.2), rgba(118, 75, 162, 0.2));
                    backdrop-filter: blur(10px);
                    border-radius: 50%;
                    display: flex;
                    align-items: center;
                    justify-content: center;
                    border: 1px solid rgba(255, 255, 255, 0.3);
                    box-shadow: 0 10px 30px rgba(0, 0, 0, 0.1);
                }
                
                .glass-icon i {
                    font-size: 36px;
                    background: linear-gradient(135deg, #667eea, #764ba2);
                    -webkit-background-clip: text;
                    -webkit-text-fill-color: transparent;
                    background-clip: text;
                }
                
                .glass-title {
                    font-size: 2.2rem;
                    font-weight: 800;
                    margin-bottom: 10px;
                    background: linear-gradient(135deg, #667eea, #764ba2);
                    -webkit-background-clip: text;
                    -webkit-text-fill-color: transparent;
                    background-clip: text;
                    letter-spacing: -0.5px;
                }
                
                .glass-subtitle {
                    color: rgba(0, 0, 0, 0.7);
                    font-size: 1.1rem;
                    font-weight: 500;
                }
                
                .glass-content {
                    margin-bottom: 40px;
                }
                
                .glass-section {
                    background: rgba(255, 255, 255, 0.05);
                    backdrop-filter: blur(10px);
                    border-radius: 20px;
                    padding: 30px;
                    margin-bottom: 25px;
                    border: 1px solid rgba(0, 0, 0, 0.1);
                    transition: all 0.3s ease;
                    position: relative;
                    overflow: hidden;
                }
                
                .glass-section:hover {
                    background: rgba(255, 255, 255, 0.08);
                    border-color: rgba(102, 126, 234, 0.3);
                    transform: translateY(-2px);
                    box-shadow: 0 10px 30px rgba(0, 0, 0, 0.2);
                }
                
                .section-title {
                    font-size: 1.4rem;
                    margin-bottom: 20px;
                    color: #333;
                    display: flex;
                    align-items: center;
                    gap: 15px;
                    font-weight: 700;
                }
                
                .section-title i {
                    color: #667eea;
                    font-size: 1.3rem;
                }
                
                .section-content {
                    color: rgba(0, 0, 0, 0.9);
                    line-height: 1.7;
                }
                
                .section-content p {
                    margin-bottom: 20px;
                }
                
                .glass-list {
                    margin: 25px 0;
                }
                
                .glass-list.negative .list-icon {
                    background: linear-gradient(135deg, rgba(220, 53, 69, 0.2), rgba(220, 53, 69, 0.1));
                    border-color: rgba(220, 53, 69, 0.3);
                }
                
                .glass-list.negative .list-icon i {
                    background: linear-gradient(135deg, #dc3545, #e35d6a);
                    -webkit-background-clip: text;
                    -webkit-text-fill-color: transparent;
                }
                
                .list-item {
                    display: flex;
                    align-items: center;
                    gap: 20px;
                    margin-bottom: 18px;
                    padding: 15px 20px;
                    background: rgba(255, 255, 255, 0.03);
                    border-radius: 16px;
                    border: 1px solid rgba(0, 0, 0, 0.05);
                    transition: all 0.3s ease;
                }
                
                .list-item:hover {
                    background: rgba(255, 255, 255, 0.06);
                    border-color: rgba(0, 0, 0, 0.1);
                    transform: translateX(5px);
                }
                
                .list-icon {
                    width: 50px;
                    height: 50px;
                    min-width: 50px;
                    background: linear-gradient(135deg, rgba(102, 126, 234, 0.2), rgba(118, 75, 162, 0.2));
                    backdrop-filter: blur(5px);
                    border-radius: 14px;
                    display: flex;
                    align-items: center;
                    justify-content: center;
                    border: 1px solid rgba(0, 0, 0, 0.1);
                }
                
                .list-icon i {
                    font-size: 22px;
                    background: linear-gradient(135deg, #667eea, #764ba2);
                    -webkit-background-clip: text;
                    -webkit-text-fill-color: transparent;
                    background-clip: text;
                }
                
                .list-text {
                    flex: 1;
                    font-size: 1.05rem;
                    color: rgba(0, 0, 0, 0.9);
                    line-height: 1.5;
                }
                
                .highlight {
                    background: linear-gradient(135deg, rgba(102, 126, 234, 0.3), rgba(118, 75, 162, 0.3));
                    color: white;
                    padding: 2px 8px;
                    border-radius: 8px;
                    font-weight: 700;
                    border: 1px solid rgba(0, 0, 0, 0.2);
                }
                
                .glass-link {
                    display: inline-flex;
                    align-items: center;
                    gap: 12px;
                    padding: 16px 28px;
                    background: rgba(255, 255, 255, 0.1);
                    backdrop-filter: blur(10px);
                    border-radius: 16px;
                    color: #333;
                    text-decoration: none;
                    font-weight: 600;
                    border: 1px solid rgba(0, 0, 0, 0.2);
                    transition: all 0.3s ease;
                    margin: 15px 0;
                }
                
                .glass-link:hover {
                    background: rgba(255, 255, 255, 0.15);
                    border-color: rgba(102, 126, 234, 0.4);
                    transform: translateY(-2px);
                    box-shadow: 0 10px 25px rgba(0, 0, 0, 0.2);
                }
                
                .glass-link i {
                    font-size: 1.3rem;
                    color: #667eea;
                }
                
                .contact-link {
                    background: linear-gradient(135deg, rgba(0, 119, 255, 0.2), rgba(0, 91, 187, 0.2));
                    border-color: rgba(0, 119, 255, 0.3);
                }
                
                .contact-link i {
                    color: #0077ff;
                }
                
                .contact-note {
                    font-size: 0.95rem;
                    color: rgba(0, 0, 0, 0.7);
                    margin-top: 15px;
                    padding-left: 20px;
                    border-left: 3px solid rgba(102, 126, 234, 0.5);
                }
                
                .version-info {
                    display: inline-flex;
                    align-items: center;
                    gap: 12px;
                    padding: 12px 24px;
                    background: rgba(102, 126, 234, 0.1);
                    border-radius: 12px;
                    border: 1px solid rgba(102, 126, 234, 0.3);
                    margin-top: 20px;
                }
                
                .version-info i {
                    color: #667eea;
                    font-size: 1.2rem;
                }
                
                .version-info span {
                    color: #333;
                    font-weight: 500;
                }
                
                .glass-footer {
                    padding-top: 40px;
                    border-top: 1px solid rgba(0, 0, 0, 0.1);
                }
                
                .accept-terms {
                    margin-bottom: 40px;
                }
                
                .checkbox-container {
                    display: flex;
                    align-items: center;
                    cursor: pointer;
                    font-size: 1.1rem;
                    color: #333;
                    user-select: none;
                    padding: 20px;
                    background: rgba(255, 255, 255, 0.05);
                    border-radius: 16px;
                    border: 2px solid rgba(0, 0, 0, 0.1);
                    transition: all 0.3s ease;
                }
                
                .checkbox-container:hover {
                    background: rgba(255, 255, 255, 0.08);
                    border-color: rgba(102, 126, 234, 0.4);
                }
                
                .checkbox-container input {
                    position: absolute;
                    opacity: 0;
                    cursor: pointer;
                    height: 0;
                    width: 0;
                }
                
                .checkmark {
                    position: relative;
                    height: 28px;
                    width: 28px;
                    min-width: 28px;
                    background: rgba(255, 255, 255, 0.1);
                    border-radius: 8px;
                    margin-right: 20px;
                    border: 2px solid rgba(0, 0, 0, 0.3);
                    transition: all 0.3s ease;
                }
                
                .checkbox-container:hover .checkmark {
                    background: rgba(102, 126, 234, 0.2);
                    border-color: rgba(102, 126, 234, 0.5);
                }
                
                .checkbox-container input:checked ~ .checkmark {
                    background: linear-gradient(135deg, #667eea, #764ba2);
                    border-color: transparent;
                }
                
                .checkmark::after {
                    content: '';
                    position: absolute;
                    display: none;
                    left: 9px;
                    top: 4px;
                    width: 8px;
                    height: 14px;
                    border: solid white;
                    border-width: 0 3px 3px 0;
                    transform: rotate(45deg);
                }
                
                .checkbox-container input:checked ~ .checkmark::after {
                    display: block;
                }
                
                .checkbox-text {
                    flex: 1;
                    font-weight: 500;
                }
                
                .glass-download {
                    background: linear-gradient(135deg, rgba(102, 126, 234, 0.1), rgba(118, 75, 162, 0.1));
                    backdrop-filter: blur(10px);
                    border: 2px dashed rgba(102, 126, 234, 0.4);
                    padding: 30px;
                    border-radius: 20px;
                    text-align: center;
                }
                
                .glass-download p {
                    color: #333;
                    font-size: 1.1rem;
                    margin-bottom: 20px;
                    font-weight: 500;
                }
                
                .glass-btn {
                    display: inline-flex;
                    align-items: center;
                    justify-content: center;
                    gap: 15px;
                    padding: 18px 40px;
                    background: linear-gradient(135deg, #667eea, #764ba2);
                    color: white;
                    text-decoration: none;
                    border-radius: 16px;
                    font-weight: 700;
                    font-size: 1.1rem;
                    transition: all 0.3s ease;
                    box-shadow: 0 10px 30px rgba(102, 126, 234, 0.3);
                    border: none;
                    cursor: pointer;
                }
                
                .glass-btn:hover {
                    transform: translateY(-3px);
                    box-shadow: 0 15px 40px rgba(102, 126, 234, 0.4);
                }
                
                .glass-btn:active {
                    transform: translateY(-1px);
                }
                
                .glass-btn i:first-child {
                    font-size: 1.5rem;
                }
                
                .glass-btn i:last-child {
                    font-size: 1.2rem;
                    opacity: 0.9;
                }
                
                /* Анимации */
                @keyframes fadeInUp {
                    from {
                        opacity: 0;
                        transform: translateY(30px);
                    }
                    to {
                        opacity: 1;
                        transform: translateY(0);
                    }
                }
                
                @keyframes fadeInDown {
                    from {
                        opacity: 0;
                        transform: translateY(-20px);
                    }
                    to {
                        opacity: 1;
                        transform: translateY(0);
                    }
                }
                
                @keyframes fadeIn {
                    from { opacity: 0; }
                    to { opacity: 1; }
                }
                
                @keyframes slideIn {
                    from {
                        opacity: 0;
                        transform: translateX(-10px);
                    }
                    to {
                        opacity: 1;
                        transform: translateX(0);
                    }
                }
                
                @keyframes slideUp {
                    from {
                        opacity: 0;
                        transform: translateY(30px) scale(0.95);
                    }
                    to {
                        opacity: 1;
                        transform: translateY(0) scale(1);
                    }
                }
                
                .glass-terms-container {
                    animation: fadeInUp 0.8s ease-out;
                }
                
                /* Адаптивность */
                @media (max-width: 768px) {
                    .container {
                        max-width: 100%;
                    }
                    
                    .auth-content {
                        padding: 30px 20px;
                    }
                    
                    .app-title {
                        font-size: 2.2rem;
                    }
                    
                    .logo-container {
                        padding: 15px 30px;
                    }
                    
                    .modal-content {
                        padding: 20px;
                    }
                    
                    .modal-header {
                        padding: 20px;
                    }
                    
                    .terms-modal {
                        max-height: 90vh;
                    }
                    
                    .glass-terms-container {
                        padding: 25px 20px;
                        margin: 20px 0;
                        border-radius: 20px;
                    }
                    
                    .glass-title {
                        font-size: 1.8rem;
                    }
                    
                    .glass-icon {
                        width: 60px;
                        height: 60px;
                    }
                    
                    .glass-icon i {
                        font-size: 28px;
                    }
                    
                    .glass-section {
                        padding: 20px;
                    }
                    
                    .section-title {
                        font-size: 1.2rem;
                    }
                    
                    .list-item {
                        flex-direction: column;
                        text-align: center;
                        gap: 15px;
                        padding: 20px;
                    }
                    
                    .list-icon {
                        width: 60px;
                        height: 60px;
                    }
                    
                    .glass-link {
                        padding: 14px 20px;
                        font-size: 0.95rem;
                    }
                    
                    .glass-btn {
                        padding: 16px 30px;
                        font-size: 1rem;
                    }
                    
                    .checkbox-text {
                        font-size: 1rem;
                    }
                }
                
                .loader {
                    display: inline-block;
                    width: 20px;
                    height: 20px;
                    border: 3px solid rgba(255, 255, 255, 0.3);
                    border-radius: 50%;
                    border-top-color: white;
                    animation: spin 1s ease-in-out infinite;
                }
                
                @keyframes spin {
                    to { transform: rotate(360deg); }
                }
            </style>
        </head>
        <body>
            <div class="container">
                <div class="logo-section">
                    <div class="logo-container">
                        <div class="logo-placeholder">
                            <i class="fas fa-comments"></i>
                        </div>
                        <h1 class="app-title">Tandau</h1>
                    </div>
                    <p class="app-subtitle">Быстрый и безопасный мессенджер для команд и личного общения</p>
                </div>
                
                <div class="auth-card">
                    <div class="auth-header">
                        <div class="auth-tab active" onclick="showTab('login')">
                            Вход
                        </div>
                        <div class="auth-tab" onclick="showTab('register')">
                            Регистрация
                        </div>
                    </div>
                    
                    <div class="auth-content">
                        <div id="alert" class="alert"></div>
                        
                        <form id="login-form" class="auth-form active">
                            <div class="form-group">
                                <label class="form-label">Логин</label>
                                <div class="input-with-icon">
                                    <i class="fas fa-user input-icon"></i>
                                    <input type="text" class="form-input" id="login-username" placeholder="Введите ваш логин" required>
                                </div>
                            </div>
                            
                            <div class="form-group">
                                <label class="form-label">Пароль</label>
                                <div class="input-with-icon">
                                    <i class="fas fa-lock input-icon"></i>
                                    <input type="password" class="form-input" id="login-password" placeholder="Введите пароль" required>
                                    <button type="button" class="password-toggle" onclick="togglePassword('login-password')">
                                        <i class="fas fa-eye"></i>
                                    </button>
                                </div>
                            </div>
                            
                            <button type="button" class="btn btn-primary" onclick="login()" id="login-btn">
                                <i class="fas fa-sign-in-alt"></i>
                                Войти в аккаунт
                            </button>
                            
                            <div class="terms">
                                Входя в систему, вы соглашаетесь с нашими <a href="#" onclick="openTermsModal(); return false;">Условиями использования</a>
                            </div>
                        </form>
                        
                        <form id="register-form" class="auth-form">
                            <div class="form-group">
                                <label class="form-label">Придумайте логин</label>
                                <div class="input-with-icon">
                                    <i class="fas fa-user-plus input-icon"></i>
                                    <input type="text" class="form-input" id="register-username" placeholder="От 3 до 20 символов" required>
                                </div>
                            </div>
                            
                            <div class="form-group">
                                <label class="form-label">Придумайте пароль</label>
                                <div class="input-with-icon">
                                    <i class="fas fa-lock input-icon"></i>
                                    <input type="password" class="form-input" id="register-password" placeholder="Не менее 4 символов" required>
                                    <button type="button" class="password-toggle" onclick="togglePassword('register-password')">
                                        <i class="fas fa-eye"></i>
                                    </button>
                                </div>
                            </div>
                            
                            <div class="form-group">
                                <label class="form-label">Повторите пароль</label>
                                <div class="input-with-icon">
                                    <i class="fas fa-lock input-icon"></i>
                                    <input type="password" class="form-input" id="register-confirm" placeholder="Повторите пароль" required>
                                    <button type="button" class="password-toggle" onclick="togglePassword('register-confirm')">
                                        <i class="fas fa-eye"></i>
                                    </button>
                                </div>
                            </div>
                            
                            <button type="button" class="btn btn-primary" onclick="register()" id="register-btn">
                                <i class="fas fa-user-plus"></i>
                                Создать аккаунт
                            </button>
                            
                            <div class="terms">
                                Регистрируясь, вы соглашаетесь с нашими <a href="#" onclick="openTermsModal(); return false;">Условиями использования</a> и <a href="#" onclick="openPrivacyModal(); return false;">Политикой конфиденциальности</a>
                            </div>
                        </form>
                    </div>
                </div>
            </div>

            <!-- Модальное окно Условий использования -->
            <div class="modal-overlay" id="terms-modal">
                <div class="terms-modal">
                    <div class="modal-header">
                        <h2><i class="fas fa-file-contract"></i> Условия использования</h2>
                        <button class="close-modal" onclick="closeTermsModal()">
                            <i class="fas fa-times"></i>
                        </button>
                    </div>
                    <div class="modal-content">
                        <!-- Блок "Условия использования" в стиле жидкое стекло -->
                        <div class="glass-terms-container">
                            <div class="glass-header">
                                <div class="glass-icon">
                                    <i class="fas fa-file-contract"></i>
                                </div>
                                <h2 class="glass-title">Условия использования Tandau Messenger</h2>
                                <div class="glass-subtitle">Дата вступления в силу: 6 декабря 2025 г.</div>
                            </div>
                            
                            <div class="glass-content">
                                <div class="glass-section">
                                    <h3 class="section-title"><i class="fas fa-user-check"></i> Регистрация и учетная запись</h3>
                                    <div class="section-content">
                                        <p>Регистрируясь в Tandau Messenger, вы подтверждаете что:</p>
                                        <div class="glass-list">
                                            <div class="list-item">
                                                <div class="list-icon"><i class="fas fa-birthday-cake"></i></div>
                                                <div class="list-text">Вы достигли возраста <span class="highlight">14 лет</span> на момент регистрации</div>
                                            </div>
                                            <div class="list-item">
                                                <div class="list-icon"><i class="fas fa-user-shield"></i></div>
                                                <div class="list-text">Предоставленная информация является точной и достоверной</div>
                                            </div>
                                            <div class="list-item">
                                                <div class="list-icon"><i class="fas fa-key"></i></div>
                                                <div class="list-text">Вы несете ответственность за сохранность учетных данных</div>
                                            </div>
                                        </div>
                                    </div>
                                </div>
                                
                                <div class="glass-section">
                                    <h3 class="section-title"><i class="fas fa-comments"></i> Правила общения</h3>
                                    <div class="section-content">
                                        <p>В Tandau Messenger запрещается:</p>
                                        <div class="glass-list negative">
                                            <div class="list-item">
                                                <div class="list-icon"><i class="fas fa-ban"></i></div>
                                                <div class="list-text">Распространение спама и вредоносного контента</div>
                                            </div>
                                            <div class="list-item">
                                                <div class="list-icon"><i class="fas fa-ban"></i></div>
                                                <div class="list-text">Нарушение прав других пользователей</div>
                                            </div>
                                            <div class="list-item">
                                                <div class="list-icon"><i class="fas fa-ban"></i></div>
                                                <div class="list-text">Использование для противоправной деятельности</div>
                                            </div>
                                            <div class="list-item">
                                                <div class="list-icon"><i class="fas fa-ban"></i></div>
                                                <div class="list-text">Создание фишинговых или мошеннических аккаунтов</div>
                                            </div>
                                        </div>
                                    </div>
                                </div>
                                
                                <div class="glass-section">
                                    <h3 class="section-title"><i class="fas fa-lock"></i> Конфиденциальность</h3>
                                    <div class="section-content">
                                        <p>Ваша конфиденциальность важна для нас. Подробная информация о защите данных:</p>
                                        <a href="#" class="glass-link" onclick="openPrivacyModal(); closeTermsModal(); return false;">
                                            <i class="fas fa-shield-alt"></i> Политика конфиденциальности
                                        </a>
                                    </div>
                                </div>
                                
                                <div class="glass-section">
                                    <h3 class="section-title"><i class="fas fa-headset"></i> Контактная информация</h3>
                                    <div class="section-content">
                                        <p>По всем вопросам, связанным с условиями использования:</p>
                                        <a href="https://vk.com/rsaltyyt" target="_blank" class="glass-link contact-link">
                                            <i class="fab fa-vk"></i> https://vk.com/rsaltyyt
                                        </a>
                                        <p class="contact-note">Обращайтесь по указанной ссылке для получения поддержки и ответов на вопросы</p>
                                    </div>
                                </div>
                                
                                <div class="glass-section">
                                    <h3 class="section-title"><i class="fas fa-sync-alt"></i> Изменения условий</h3>
                                    <div class="section-content">
                                        <p>Мы оставляем за собой право вносить изменения в Условия использования. Актуальная версия всегда доступна на этой странице.</p>
                                        <div class="version-info">
                                            <i class="fas fa-history"></i>
                                            <span>Последнее обновление: 6 декабря 2025 года</span>
                                        </div>
                                    </div>
                                </div>
                            </div>
                            
                            <div class="glass-footer">
                                <div class="accept-terms">
                                    <label class="checkbox-container">
                                        <input type="checkbox" id="accept-terms-checkbox">
                                        <span class="checkmark"></span>
                                        <span class="checkbox-text">Я прочитал(а) и принимаю Условия использования</span>
                                    </label>
                                </div>
                                
                                <div class="download-section glass-download">
                                    <p>Полная версия документа:</p>
                                    <a href="/static/docs/terms_of_use.pdf" class="download-btn glass-btn" download="Tandau_Условия_использования.pdf">
                                        <i class="fas fa-file-pdf"></i>
                                        Скачать PDF (156 KB)
                                        <i class="fas fa-download"></i>
                                    </a>
                                </div>
                            </div>
                        </div>
                    </div>
                </div>
            </div>

            <!-- Модальное окно Политики конфиденциальности -->
            <div class="modal-overlay" id="privacy-modal">
                <div class="terms-modal">
                    <div class="modal-header">
                        <h2><i class="fas fa-shield-alt"></i> Политика конфиденциальности</h2>
                        <button class="close-modal" onclick="closePrivacyModal()">
                            <i class="fas fa-times"></i>
                        </button>
                    </div>
                    <div class="modal-content">
                        <p><strong>Дата вступления в силу:</strong> 6 декабря 2025 г.</p>
                        
                        <h3>1. Сбор информации</h3>
                        <p>Мы собираем следующую информацию:</p>
                        <ul>
                            <li>Имя пользователя и контактные данные</li>
                            <li>Информацию об использовании сервиса</li>
                            <li>Технические данные (IP-адрес, тип устройства)</li>
                            <li>Содержание сообщений (хранится только для обеспечения работы сервиса)</li>
                        </ul>
                        
                        <h3>2. Использование информации</h3>
                        <p>Собранная информация используется для:</p>
                        <ul>
                            <li>Предоставления и улучшения сервиса</li>
                            <li>Обеспечения безопасности</li>
                            <li>Поддержки пользователей</li>
                            <li>Анализа использования сервиса</li>
                        </ul>
                        
                        <h3>3. Защита данных</h3>
                        <p>Мы используем современные технологии шифрования для защиты ваших данных. Все сообщения шифруются при передаче.</p>
                        
                        <h3>4. Права пользователей</h3>
                        <p>Вы имеете право:</p>
                        <ul>
                            <li>Получить доступ к своим данным</li>
                            <li>Исправить неточную информацию</li>
                            <li>Удалить свою учетную запись</li>
                            <li>Отозвать согласие на обработку данных</li>
                        </ul>
                        
                        <h3>5. Файлы cookie</h3>
                        <p>Мы используем файлы cookie для улучшения работы сервиса. Вы можете отключить их в настройках браузера.</p>
                        
                        <h3>6. Третьи стороны</h3>
                        <p>Мы не передаем ваши данные третьим лицам без вашего согласия, за исключением случаев, предусмотренных законом.</p>
                        
                        <h3>7. Изменения политики</h3>
                        <p>Мы будем уведомлять пользователей о значительных изменениях в политике конфиденциальности.</p>
                        
                        <h3>8. Контакты</h3>
                        <p>По вопросам конфиденциальности: <a href="mailto:privacy@tandau.com">privacy@tandau.com</a></p>
                        
                        <div class="download-section">
                            <p>Скачать полную версию Политики конфиденциальности в формате PDF:</p>
                            <a href="https://github.com/saltyse/Tandau/blob/main/Условия%20использования%20сервиса%20Tandau.pdf" class="download-btn" download="Tandau_Политика_конфиденциальности.pdf">
                                <i class="fas fa-file-pdf"></i>
                                Скачать PDF документ
                                <i class="fas fa-download"></i>
                            </a>
                            <p style="margin-top: 10px; font-size: 0.8rem; color: #666;">Размер файла: 142 KB</p>
                        </div>
                    </div>
                </div>
            </div>

            <script>
                let isLoading = false;
                
                function showAlert(message, type = 'error') {
                    const alert = document.getElementById('alert');
                    alert.textContent = message;
                    alert.className = `alert alert-${type}`;
                    alert.style.display = 'block';
                    
                    setTimeout(() => {
                        alert.style.display = 'none';
                    }, 5000);
                }
                
                function showTab(tabName) {
                    if (isLoading) return;
                    
                    document.querySelectorAll('.auth-tab').forEach(tab => tab.classList.remove('active'));
                    document.querySelectorAll('.auth-form').forEach(form => form.classList.remove('active'));
                    
                    document.querySelector(`.auth-tab[onclick="showTab('${tabName}')"]`).classList.add('active');
                    document.getElementById(`${tabName}-form`).classList.add('active');
                }
                
                function togglePassword(inputId) {
                    const input = document.getElementById(inputId);
                    const button = input.nextElementSibling;
                    const icon = button.querySelector('i');
                    
                    if (input.type === 'password') {
                        input.type = 'text';
                        icon.className = 'fas fa-eye-slash';
                    } else {
                        input.type = 'password';
                        icon.className = 'fas fa-eye';
                    }
                }
                
                function setLoading(buttonId, loading) {
                    isLoading = loading;
                    const button = document.getElementById(buttonId);
                    const icon = button.querySelector('i');
                    
                    if (loading) {
                        button.disabled = true;
                        button.innerHTML = '<div class="loader"></div> Загрузка...';
                    } else {
                        button.disabled = false;
                        if (buttonId === 'login-btn') {
                            button.innerHTML = '<i class="fas fa-sign-in-alt"></i> Войти в аккаунт';
                        } else {
                            button.innerHTML = '<i class="fas fa-user-plus"></i> Создать аккаунт';
                        }
                    }
                }
                
                async function login() {
                    if (isLoading) return;
                    
                    const username = document.getElementById('login-username').value.trim();
                    const password = document.getElementById('login-password').value;
                    
                    if (!username || !password) {
                        return showAlert('Заполните все поля');
                    }
                    
                    setLoading('login-btn', true);
                    
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
                            showAlert('Успешный вход! Перенаправляем...', 'success');
                            setTimeout(() => {
                                window.location.href = '/chat';
                            }, 1000);
                        } else {
                            showAlert(data.error || 'Неверный логин или пароль');
                        }
                    } catch (error) {
                        showAlert('Ошибка соединения. Проверьте интернет');
                        console.error('Login error:', error);
                    } finally {
                        setLoading('login-btn', false);
                    }
                }
                
                async function register() {
                    if (isLoading) return;
                    
                    const username = document.getElementById('register-username').value.trim();
                    const password = document.getElementById('register-password').value;
                    const confirm = document.getElementById('register-confirm').value;
                    
                    if (!username || !password || !confirm) {
                        return showAlert('Заполните все поля');
                    }
                    
                    if (username.length < 3) {
                        return showAlert('Логин должен быть не менее 3 символов');
                    }
                    
                    if (username.length > 20) {
                        return showAlert('Логин должен быть не более 20 символов');
                    }
                    
                    if (password.length < 4) {
                        return showAlert('Пароль должен быть не менее 4 символов');
                    }
                    
                    if (password !== confirm) {
                        return showAlert('Пароли не совпадают');
                    }
                    
                    setLoading('register-btn', true);
                    
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
                            
                            setTimeout(async () => {
                                try {
                                    const loginResponse = await fetch('/login', {
                                        method: 'POST',
                                        headers: {'Content-Type': 'application/x-www-form-urlencoded'},
                                        body: new URLSearchParams({ username, password })
                                    });
                                    
                                    const loginData = await loginResponse.json();
                                    
                                    if (loginData.success) {
                                        window.location.href = '/chat';
                                    } else {
                                        showAlert('Автоматический вход не удался. Войдите вручную.');
                                        showTab('login');
                                    }
                                } catch (error) {
                                    showAlert('Ошибка автоматического входа. Войдите вручную.');
                                    showTab('login');
                                }
                            }, 1500);
                        } else {
                            showAlert(data.error || 'Ошибка регистрации');
                        }
                    } catch (error) {
                        showAlert('Ошибка соединения. Проверьте интернет');
                        console.error('Register error:', error);
                    } finally {
                        setLoading('register-btn', false);
                    }
                }
                
                // Функции для модальных окон
                function openTermsModal() {
                    document.getElementById('terms-modal').style.display = 'flex';
                    document.body.style.overflow = 'hidden';
                }
                
                function closeTermsModal() {
                    document.getElementById('terms-modal').style.display = 'none';
                    document.body.style.overflow = 'auto';
                }
                
                function openPrivacyModal() {
                    document.getElementById('privacy-modal').style.display = 'flex';
                    document.body.style.overflow = 'hidden';
                }
                
                function closePrivacyModal() {
                    document.getElementById('privacy-modal').style.display = 'none';
                    document.body.style.overflow = 'auto';
                }
                
                // Закрытие модальных окон при клике вне их
                document.addEventListener('click', function(event) {
                    const termsModal = document.getElementById('terms-modal');
                    const privacyModal = document.getElementById('privacy-modal');
                    
                    if (event.target === termsModal) {
                        closeTermsModal();
                    }
                    if (event.target === privacyModal) {
                        closePrivacyModal();
                    }
                });
                
                // Закрытие модальных окон по клавише ESC
                document.addEventListener('keydown', function(event) {
                    if (event.key === 'Escape') {
                        closeTermsModal();
                        closePrivacyModal();
                    }
                });
                
                document.addEventListener('keypress', function(e) {
                    if (e.key === 'Enter') {
                        const activeForm = document.querySelector('.auth-form.active');
                        if (activeForm.id === 'login-form') login();
                        if (activeForm.id === 'register-form') register();
                    }
                });
                
                document.addEventListener('DOMContentLoaded', function() {
                    const inputs = document.querySelectorAll('.form-input');
                    inputs.forEach(input => {
                        input.addEventListener('focus', function() {
                            this.parentElement.style.transform = 'translateY(-2px)';
                        });
                        
                        input.addEventListener('blur', function() {
                            this.parentElement.style.transform = 'translateY(0)';
                        });
                    });
                    
                    // Создаем папку для документов и пример PDF файлов
                    fetch('/create_docs_folder', { method: 'POST' })
                        .then(response => response.json())
                        .then(data => {
                            if (data.success) {
                                console.log('Documents folder created');
                            }
                        });
                    
                    // Инициализация чекбокса принятия условий
                    const termsCheckbox = document.getElementById('accept-terms-checkbox');
                    if (termsCheckbox) {
                        termsCheckbox.addEventListener('change', function() {
                            const registerBtn = document.getElementById('register-btn');
                            const loginBtn = document.getElementById('login-btn');
                            
                            if (registerBtn) {
                                registerBtn.disabled = !this.checked;
                            }
                            if (loginBtn) {
                                loginBtn.disabled = !this.checked;
                            }
                        });
                        
                        // По умолчанию активируем кнопки
                        termsCheckbox.checked = true;
                        termsCheckbox.dispatchEvent(new Event('change'));
                    }
                });
            </script>
        </body>
        </html>
        '''

    @app.route('/login', methods=['POST'])
    def login_handler(): 
        u = request.form.get('username', '').strip()
        p = request.form.get('password', '')
        
        if not u or not p:
            return jsonify({'success': False, 'error': 'Заполните все поля'})
        
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
        
        if not u or not p:
            return jsonify({'success': False, 'error': 'Заполните все поля'})
        
        if len(u) < 3:
            return jsonify({'success': False, 'error': 'Логин должен быть не менее 3 символов'})
        
        if len(p) < 4:
            return jsonify({'success': False, 'error': 'Пароль должен быть не менее 4 символов'})
        
        success, message = create_user(u, p)
        if success:
            return jsonify({'success': True})
        return jsonify({'success': False, 'error': message})

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
        if not user:
            session.pop('username', None)
            return redirect('/')
        
        theme = user['theme']
        
        # Генерируем HTML с эффектом жидкого стекла
        return f'''
<!DOCTYPE html>
<html lang="ru" data-theme="{theme}">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0, maximum-scale=1.0, user-scalable=no">
    <title>Tandau Chat - {username}</title>
    <link rel="stylesheet" href="https://cdnjs.cloudflare.com/ajax/libs/font-awesome/6.5.0/css/all.min.css">
    <style>
        :root {{
            --glass-bg: rgba(255, 255, 255, 0.15);
            --glass-border: rgba(255, 255, 255, 0.25);
            --glass-shadow: 0 8px 32px rgba(0, 0, 0, 0.1);
            --glass-blur: 20px;
            --primary-gradient: linear-gradient(135deg, #667eea 0%, #764ba2 100%);
            --secondary-gradient: linear-gradient(135deg, #f093fb 0%, #f5576c 100%);
            --accent: #667eea;
            --accent-dark: #5a67d8;
            --text: #2d3748;
            --text-light: #718096;
            --bg: #f7fafc;
            --input-bg: rgba(255, 255, 255, 0.7);
            --border: rgba(255, 255, 255, 0.3);
            --sidebar-width: 300px;
            --favorite-color: #ffd700;
            --online: #10b981;
            --offline: #9ca3af;
        }}
        
        [data-theme="dark"] {{
            --glass-bg: rgba(30, 30, 40, 0.4);
            --glass-border: rgba(255, 255, 255, 0.15);
            --glass-shadow: 0 8px 32px rgba(0, 0, 0, 0.3);
            --text: #e2e8f0;
            --text-light: #a0aec0;
            --bg: #1a202c;
            --input-bg: rgba(45, 55, 72, 0.7);
            --border: rgba(255, 255, 255, 0.1);
        }}
        
        * {{
            margin: 0;
            padding: 0;
            box-sizing: border-box;
            -webkit-tap-highlight-color: transparent;
        }}
        
        body {{
            font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif;
            background: var(--primary-gradient);
            color: var(--text);
            height: 100vh;
            overflow: hidden;
            touch-action: manipulation;
        }}
        
        /* Основной контейнер с эффектом стекла */
        .app-container {{
            display: flex;
            height: 100vh;
            position: relative;
            backdrop-filter: blur(10px);
            background: rgba(255, 255, 255, 0.05);
        }}
        
        /* Сайдбар - жидкое стекло */
        .sidebar {{
            width: var(--sidebar-width);
            background: var(--glass-bg);
            backdrop-filter: blur(var(--glass-blur));
            -webkit-backdrop-filter: blur(var(--glass-blur));
            border-right: 1px solid var(--glass-border);
            box-shadow: var(--glass-shadow);
            display: flex;
            flex-direction: column;
            position: relative;
            z-index: 10;
        }}
        
        .sidebar-header {{
            padding: 20px;
            background: rgba(255, 255, 255, 0.1);
            backdrop-filter: blur(10px);
            text-align: center;
            font-weight: 700;
            font-size: 1.2rem;
            display: flex;
            align-items: center;
            justify-content: center;
            gap: 12px;
            border-bottom: 1px solid var(--border);
        }}
        
        .logo-placeholder {{
            width: 40px;
            height: 40px;
            border-radius: 12px;
            background: var(--primary-gradient);
            display: flex;
            align-items: center;
            justify-content: center;
            color: white;
            font-size: 20px;
            font-weight: bold;
            box-shadow: 0 4px 15px rgba(102, 126, 234, 0.4);
        }}
        
        .app-title {{
            color: white;
            font-size: 1.8rem;
            font-weight: 800;
            letter-spacing: -0.5px;
            text-shadow: 0 2px 10px rgba(0, 0, 0, 0.2);
        }}
        
        .user-info {{
            padding: 20px 15px;
            display: flex;
            gap: 12px;
            align-items: center;
            border-bottom: 1px solid var(--border);
            background: rgba(255, 255, 255, 0.05);
        }}
        
        .avatar {{
            width: 44px;
            height: 44px;
            border-radius: 50%;
            background: var(--primary-gradient);
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
            border: 2px solid rgba(255, 255, 255, 0.3);
            box-shadow: 0 4px 15px rgba(0, 0, 0, 0.2);
        }}
        
        .user-details {{
            flex: 1;
        }}
        
        .user-details strong {{
            display: block;
            font-size: 1rem;
            margin-bottom: 4px;
            color: white;
        }}
        
        .user-status {{
            font-size: 0.85rem;
            opacity: 0.9;
            display: flex;
            align-items: center;
            gap: 5px;
            color: var(--online);
        }}
        
        .status-dot {{
            width: 8px;
            height: 8px;
            border-radius: 50%;
            background: var(--online);
            box-shadow: 0 0 10px var(--online);
        }}
        
        .nav {{
            flex: 1;
            overflow-y: auto;
            padding: 15px;
            -webkit-overflow-scrolling: touch;
        }}
        
        .nav-title {{
            padding: 12px 15px;
            font-size: 0.8rem;
            color: rgba(255, 255, 255, 0.7);
            text-transform: uppercase;
            font-weight: 600;
            letter-spacing: 0.5px;
            display: flex;
            justify-content: space-between;
            align-items: center;
            margin-top: 10px;
        }}
        
        .nav-item {{
            padding: 12px 15px;
            cursor: pointer;
            border-radius: 12px;
            margin: 4px 0;
            transition: all 0.3s ease;
            display: flex;
            align-items: center;
            gap: 12px;
            user-select: none;
            color: rgba(255, 255, 255, 0.9);
            backdrop-filter: blur(5px);
            background: rgba(255, 255, 255, 0.05);
            border: 1px solid rgba(255, 255, 255, 0.1);
        }}
        
        .nav-item:hover {{
            background: rgba(255, 255, 255, 0.1);
            transform: translateX(5px);
            border-color: rgba(255, 255, 255, 0.2);
        }}
        
        .nav-item.active {{
            background: rgba(255, 255, 255, 0.15);
            color: white;
            border-color: rgba(255, 255, 255, 0.3);
            box-shadow: 0 4px 15px rgba(0, 0, 0, 0.1);
        }}
        
        .nav-item i {{
            width: 20px;
            text-align: center;
            font-size: 1.1rem;
        }}
        
        .add-btn {{
            background: rgba(255, 255, 255, 0.1);
            border: 1px solid rgba(255, 255, 255, 0.2);
            color: white;
            cursor: pointer;
            padding: 4px 8px;
            border-radius: 6px;
            display: flex;
            align-items: center;
            justify-content: center;
            transition: all 0.2s ease;
        }}
        
        .add-btn:hover {{
            background: rgba(255, 255, 255, 0.2);
            transform: scale(1.1);
        }}
        
        /* Область чата - жидкое стекло */
        .chat-area {{
            flex: 1;
            display: flex;
            flex-direction: column;
            background: var(--glass-bg);
            backdrop-filter: blur(var(--glass-blur));
            -webkit-backdrop-filter: blur(var(--glass-blur));
            border-left: 1px solid var(--glass-border);
            box-shadow: var(--glass-shadow);
        }}
        
        .chat-header {{
            padding: 20px;
            background: rgba(255, 255, 255, 0.1);
            backdrop-filter: blur(10px);
            border-bottom: 1px solid var(--border);
            font-weight: 600;
            font-size: 1.3rem;
            color: white;
            display: flex;
            align-items: center;
            gap: 15px;
        }}
        
        .channel-actions {{
            margin-left: auto;
            display: flex;
            gap: 10px;
        }}
        
        .channel-btn {{
            background: rgba(255, 255, 255, 0.1);
            border: 1px solid rgba(255, 255, 255, 0.2);
            color: white;
            cursor: pointer;
            padding: 8px;
            border-radius: 8px;
            display: flex;
            align-items: center;
            justify-content: center;
            transition: all 0.2s ease;
        }}
        
        .channel-btn:hover {{
            background: rgba(255, 255, 255, 0.2);
            transform: translateY(-2px);
        }}
        
        .messages {{
            flex: 1;
            padding: 20px;
            overflow-y: auto;
            display: flex;
            flex-direction: column;
            gap: 15px;
            -webkit-overflow-scrolling: touch;
        }}
        
        /* Стили сообщений с эффектом стекла */
        .message {{
            display: flex;
            align-items: flex-start;
            gap: 15px;
            animation: fadeIn 0.3s ease;
            max-width: 85%;
        }}
        
        .message.own {{
            align-self: flex-end;
            flex-direction: row-reverse;
        }}
        
        .message-avatar {{
            width: 40px;
            height: 40px;
            border-radius: 50%;
            background: var(--primary-gradient);
            color: white;
            display: flex;
            align-items: center;
            justify-content: center;
            font-weight: bold;
            font-size: 1rem;
            flex-shrink: 0;
            background-size: cover;
            background-position: center;
            border: 2px solid rgba(255, 255, 255, 0.3);
            box-shadow: 0 4px 15px rgba(0, 0, 0, 0.1);
        }}
        
        .message-content {{
            background: rgba(255, 255, 255, 0.15);
            backdrop-filter: blur(10px);
            padding: 15px;
            border-radius: 18px;
            border-top-left-radius: 4px;
            border: 1px solid rgba(255, 255, 255, 0.2);
            box-shadow: 0 4px 15px rgba(0, 0, 0, 0.1);
            max-width: 100%;
        }}
        
        .message.own .message-content {{
            background: rgba(102, 126, 234, 0.3);
            border-top-left-radius: 18px;
            border-top-right-radius: 4px;
            border-color: rgba(255, 255, 255, 0.3);
        }}
        
        .message-sender {{
            font-weight: 600;
            font-size: 0.9rem;
            margin-bottom: 5px;
            color: white;
        }}
        
        .message-text {{
            word-break: break-word;
            line-height: 1.4;
            color: white;
        }}
        
        .message-file {{
            margin-top: 10px;
            border-radius: 12px;
            overflow: hidden;
            max-width: 100%;
        }}
        
        .message-file img {{
            max-width: 100%;
            max-height: 300px;
            border-radius: 8px;
            cursor: pointer;
            transition: transform 0.2s;
            border: 1px solid rgba(255, 255, 255, 0.2);
        }}
        
        .message-file img:hover {{
            transform: scale(1.02);
        }}
        
        .message-file video {{
            max-width: 100%;
            max-height: 300px;
            border-radius: 8px;
            border: 1px solid rgba(255, 255, 255, 0.2);
        }}
        
        .message-time {{
            font-size: 0.75rem;
            color: rgba(255, 255, 255, 0.7);
            margin-top: 5px;
            text-align: right;
        }}
        
        /* Область ввода - жидкое стекло */
        .input-area {{
            background: rgba(255, 255, 255, 0.1);
            backdrop-filter: blur(15px);
            -webkit-backdrop-filter: blur(15px);
            border-top: 1px solid var(--border);
            padding: 20px;
            box-shadow: 0 -2px 20px rgba(0, 0, 0, 0.1);
        }}
        
        .input-row {{
            display: flex;
            gap: 10px;
            align-items: flex-end;
        }}
        
        .attachment-btn {{
            background: rgba(255, 255, 255, 0.15);
            border: 1px solid rgba(255, 255, 255, 0.3);
            color: white;
            cursor: pointer;
            font-size: 1.2rem;
            padding: 12px;
            border-radius: 50%;
            width: 48px;
            height: 48px;
            display: flex;
            align-items: center;
            justify-content: center;
            flex-shrink: 0;
            transition: all 0.3s ease;
        }}
        
        .attachment-btn:hover {{
            background: rgba(255, 255, 255, 0.25);
            transform: translateY(-2px);
            box-shadow: 0 4px 15px rgba(0, 0, 0, 0.2);
        }}
        
        .msg-input {{
            flex: 1;
            padding: 14px 18px;
            border: 1px solid rgba(255, 255, 255, 0.3);
            border-radius: 25px;
            background: rgba(255, 255, 255, 0.1);
            backdrop-filter: blur(10px);
            color: white;
            font-size: 1rem;
            resize: none;
            max-height: 120px;
            min-height: 48px;
            line-height: 1.4;
            transition: all 0.3s ease;
            box-shadow: 0 2px 10px rgba(0, 0, 0, 0.1);
        }}
        
        .msg-input:focus {{
            outline: none;
            border-color: var(--accent);
            background: rgba(255, 255, 255, 0.15);
            box-shadow: 0 4px 20px rgba(102, 126, 234, 0.3);
        }}
        
        .msg-input::placeholder {{
            color: rgba(255, 255, 255, 0.6);
        }}
        
        .send-btn {{
            width: 48px;
            height: 48px;
            border-radius: 50%;
            background: var(--primary-gradient);
            color: white;
            border: none;
            cursor: pointer;
            display: flex;
            align-items: center;
            justify-content: center;
            flex-shrink: 0;
            transition: all 0.3s ease;
            box-shadow: 0 4px 15px rgba(102, 126, 234, 0.4);
        }}
        
        .send-btn:hover {{
            transform: translateY(-2px);
            box-shadow: 0 6px 20px rgba(102, 126, 234, 0.6);
        }}
        
        .send-btn:active {{
            transform: translateY(0);
        }}
        
        /* Избранное - сетка с эффектом стекла */
        .favorites-grid {{
            display: grid;
            grid-template-columns: repeat(auto-fill, minmax(250px, 1fr));
            gap: 20px;
            padding: 20px;
        }}
        
        .favorite-item {{
            background: rgba(255, 255, 255, 0.1);
            backdrop-filter: blur(10px);
            border-radius: 16px;
            padding: 20px;
            border: 1px solid rgba(255, 255, 255, 0.2);
            position: relative;
            transition: all 0.3s ease;
            box-shadow: 0 4px 15px rgba(0, 0, 0, 0.1);
        }}
        
        .favorite-item:hover {{
            transform: translateY(-5px);
            box-shadow: 0 8px 25px rgba(0, 0, 0, 0.2);
            border-color: rgba(255, 255, 255, 0.3);
        }}
        
        .favorite-item.pinned {{
            border-left: 4px solid var(--favorite-color);
            background: rgba(255, 215, 0, 0.05);
        }}
        
        .favorite-content {{
            margin-bottom: 15px;
            word-break: break-word;
            color: white;
        }}
        
        .favorite-file {{
            max-width: 100%;
            border-radius: 12px;
            overflow: hidden;
            margin-bottom: 15px;
            border: 1px solid rgba(255, 255, 255, 0.2);
        }}
        
        .favorite-file img, .favorite-file video {{
            width: 100%;
            height: auto;
            display: block;
            border-radius: 12px;
        }}
        
        .favorite-meta {{
            display: flex;
            justify-content: space-between;
            align-items: center;
            font-size: 0.8rem;
            color: rgba(255, 255, 255, 0.7);
            margin-top: 15px;
        }}
        
        .favorite-actions {{
            position: absolute;
            top: 10px;
            right: 10px;
            display: flex;
            gap: 5px;
            opacity: 0;
            transition: opacity 0.3s ease;
        }}
        
        .favorite-item:hover .favorite-actions {{
            opacity: 1;
        }}
        
        .favorite-action-btn {{
            background: rgba(0, 0, 0, 0.5);
            backdrop-filter: blur(5px);
            color: white;
            border: 1px solid rgba(255, 255, 255, 0.2);
            width: 32px;
            height: 32px;
            border-radius: 8px;
            display: flex;
            align-items: center;
            justify-content: center;
            cursor: pointer;
            font-size: 0.9rem;
            transition: all 0.2s ease;
        }}
        
        .favorite-action-btn:hover {{
            background: rgba(0, 0, 0, 0.7);
            transform: scale(1.1);
        }}
        
        .category-badge {{
            display: inline-block;
            padding: 4px 12px;
            background: rgba(102, 126, 234, 0.3);
            color: white;
            border-radius: 20px;
            font-size: 0.75rem;
            border: 1px solid rgba(255, 255, 255, 0.2);
        }}
        
        /* Фильтры категорий */
        .categories-filter {{
            display: flex;
            gap: 10px;
            padding: 15px 20px;
            background: rgba(255, 255, 255, 0.1);
            backdrop-filter: blur(10px);
            border-bottom: 1px solid var(--border);
            overflow-x: auto;
            -webkit-overflow-scrolling: touch;
        }}
        
        .category-filter-btn {{
            padding: 8px 16px;
            background: rgba(255, 255, 255, 0.1);
            border: 1px solid rgba(255, 255, 255, 0.2);
            border-radius: 20px;
            cursor: pointer;
            font-size: 0.9rem;
            transition: all 0.3s ease;
            color: rgba(255, 255, 255, 0.8);
            white-space: nowrap;
        }}
        
        .category-filter-btn.active {{
            background: var(--primary-gradient);
            color: white;
            border-color: transparent;
            box-shadow: 0 4px 15px rgba(102, 126, 234, 0.4);
        }}
        
        /* Настройки канала */
        .settings-content {{
            padding: 20px;
        }}
        
        .settings-section {{
            background: rgba(255, 255, 255, 0.1);
            backdrop-filter: blur(10px);
            border-radius: 16px;
            padding: 20px;
            margin-bottom: 20px;
            border: 1px solid rgba(255, 255, 255, 0.2);
        }}
        
        .settings-title {{
            font-size: 1.1rem;
            font-weight: 600;
            margin-bottom: 15px;
            color: white;
            padding-bottom: 10px;
            border-bottom: 1px solid rgba(255, 255, 255, 0.2);
        }}
        
        .member-list {{
            max-height: 300px;
            overflow-y: auto;
            -webkit-overflow-scrolling: touch;
        }}
        
        .member-item {{
            display: flex;
            align-items: center;
            justify-content: space-between;
            padding: 12px 15px;
            border-bottom: 1px solid rgba(255, 255, 255, 0.1);
            transition: all 0.2s ease;
        }}
        
        .member-item:hover {{
            background: rgba(255, 255, 255, 0.05);
        }}
        
        .member-info {{
            display: flex;
            align-items: center;
            gap: 12px;
        }}
        
        .member-avatar {{
            width: 36px;
            height: 36px;
            border-radius: 50%;
            display: flex;
            align-items: center;
            justify-content: center;
            font-weight: bold;
            font-size: 0.9rem;
            background: var(--primary-gradient);
            color: white;
            background-size: cover;
            background-position: center;
            border: 2px solid rgba(255, 255, 255, 0.3);
        }}
        
        .member-name {{
            font-size: 0.95rem;
            color: white;
        }}
        
        .member-role {{
            font-size: 0.75rem;
            color: rgba(255, 255, 255, 0.7);
            padding: 4px 10px;
            background: rgba(255, 255, 255, 0.1);
            border-radius: 12px;
            border: 1px solid rgba(255, 255, 255, 0.2);
        }}
        
        .member-role.admin {{
            background: rgba(102, 126, 234, 0.3);
            color: white;
            border-color: rgba(102, 126, 234, 0.5);
        }}
        
        /* Модальные окна с эффектом стекла */
        .modal {{
            display: none;
            position: fixed;
            top: 0;
            left: 0;
            width: 100%;
            height: 100%;
            background: rgba(0, 0, 0, 0.5);
            backdrop-filter: blur(5px);
            -webkit-backdrop-filter: blur(5px);
            z-index: 1000;
            align-items: center;
            justify-content: center;
            padding: 20px;
        }}
        
        .modal-content {{
            background: rgba(255, 255, 255, 0.15);
            backdrop-filter: blur(20px);
            -webkit-backdrop-filter: blur(20px);
            padding: 30px;
            border-radius: 20px;
            width: 100%;
            max-width: 500px;
            max-height: 90vh;
            overflow-y: auto;
            -webkit-overflow-scrolling: touch;
            border: 1px solid rgba(255, 255, 255, 0.2);
            box-shadow: 0 20px 60px rgba(0, 0, 0, 0.3);
            color: white;
        }}
        
        .modal-content h3 {{
            margin-bottom: 20px;
            color: white;
            font-size: 1.5rem;
        }}
        
        .form-group {{
            margin-bottom: 20px;
        }}
        
        .form-label {{
            display: block;
            margin-bottom: 8px;
            color: rgba(255, 255, 255, 0.9);
            font-weight: 500;
        }}
        
        .form-control {{
            width: 100%;
            padding: 12px 16px;
            border: 1px solid rgba(255, 255, 255, 0.3);
            border-radius: 12px;
            background: rgba(255, 255, 255, 0.1);
            color: white;
            font-size: 1rem;
            transition: all 0.3s ease;
        }}
        
        .form-control:focus {{
            outline: none;
            border-color: var(--accent);
            background: rgba(255, 255, 255, 0.15);
            box-shadow: 0 0 0 3px rgba(102, 126, 234, 0.3);
        }}
        
        .btn {{
            padding: 12px 24px;
            border: none;
            border-radius: 12px;
            cursor: pointer;
            font-weight: 500;
            transition: all 0.3s ease;
            font-size: 1rem;
        }}
        
        .btn-primary {{
            background: var(--primary-gradient);
            color: white;
            box-shadow: 0 4px 15px rgba(102, 126, 234, 0.4);
        }}
        
        .btn-primary:hover {{
            transform: translateY(-2px);
            box-shadow: 0 6px 20px rgba(102, 126, 234, 0.6);
        }}
        
        .btn-secondary {{
            background: rgba(255, 255, 255, 0.1);
            color: white;
            border: 1px solid rgba(255, 255, 255, 0.2);
        }}
        
        .btn-secondary:hover {{
            background: rgba(255, 255, 255, 0.2);
            transform: translateY(-2px);
        }}
        
        /* Анимации */
        @keyframes fadeIn {{
            from {{ opacity: 0; transform: translateY(10px); }}
            to {{ opacity: 1; transform: translateY(0); }}
        }}
        
        /* Скроллбар */
        ::-webkit-scrollbar {{
            width: 8px;
        }}
        
        ::-webkit-scrollbar-track {{
            background: rgba(255, 255, 255, 0.05);
            border-radius: 4px;
        }}
        
        ::-webkit-scrollbar-thumb {{
            background: rgba(255, 255, 255, 0.2);
            border-radius: 4px;
        }}
        
        ::-webkit-scrollbar-thumb:hover {{
            background: rgba(255, 255, 255, 0.3);
        }}
        
        /* Адаптивность */
        @media (max-width: 768px) {{
            .sidebar {{
                position: fixed;
                top: 0;
                left: 0;
                bottom: 0;
                width: 100%;
                max-width: 300px;
                transform: translateX(-100%);
                transition: transform 0.3s ease;
                z-index: 1000;
            }}
            
            .sidebar.active {{
                transform: translateX(0);
            }}
            
            .menu-toggle {{
                display: block;
                position: absolute;
                top: 20px;
                left: 20px;
                background: rgba(255, 255, 255, 0.1);
                border: 1px solid rgba(255, 255, 255, 0.2);
                color: white;
                padding: 10px;
                border-radius: 8px;
                cursor: pointer;
                z-index: 1001;
            }}
            
            .back-btn {{
                display: block;
                background: none;
                border: none;
                color: white;
                cursor: pointer;
                font-size: 1.2rem;
                padding: 5px;
            }}
            
            .chat-header {{
                padding: 15px;
            }}
            
            .messages {{
                padding: 15px;
                padding-bottom: 80px;
            }}
            
            .input-area {{
                position: fixed;
                bottom: 0;
                left: 0;
                right: 0;
                padding: 15px;
                z-index: 900;
            }}
            
            .favorites-grid {{
                grid-template-columns: 1fr;
                gap: 15px;
                padding: 15px;
                padding-bottom: 80px;
            }}
            
            .message {{
                max-width: 90%;
            }}
        }}
        
        @media (min-width: 769px) {{
            .menu-toggle, .back-btn {{
                display: none;
            }}
        }}
        
        .empty-chat, .empty-favorites {{
            text-align: center;
            padding: 60px 20px;
            color: rgba(255, 255, 255, 0.7);
        }}
        
        .empty-chat i, .empty-favorites i {{
            font-size: 4rem;
            margin-bottom: 20px;
            opacity: 0.5;
        }}
        
        .logout-btn {{
            margin: 20px;
            padding: 12px;
            background: rgba(220, 53, 69, 0.3);
            color: white;
            border: 1px solid rgba(220, 53, 69, 0.3);
            border-radius: 12px;
            cursor: pointer;
            font-weight: 600;
            display: flex;
            align-items: center;
            justify-content: center;
            gap: 8px;
            transition: all 0.3s ease;
        }}
        
        .logout-btn:hover {{
            background: rgba(220, 53, 69, 0.5);
            transform: translateY(-2px);
        }}
    </style>
</head>
<body>
    <div class="app-container">
        <!-- Кнопка меню для мобильных -->
        <button class="menu-toggle" onclick="toggleSidebar()">
            <i class="fas fa-bars"></i>
        </button>
        
        <!-- Сайдбар -->
        <div class="sidebar" id="sidebar">
            <div class="sidebar-header">
                <div class="logo-placeholder">
                    <i class="fas fa-comments"></i>
                </div>
                <h1 class="app-title">Tandau</h1>
            </div>
            
            <div class="user-info">
                <div class="avatar" id="user-avatar" onclick="openAvatarModal()"></div>
                <div class="user-details">
                    <strong>{username}</strong>
                    <div class="user-status">
                        <div class="status-dot"></div>
                        Online
                    </div>
                </div>
                <button class="channel-btn" onclick="openThemeModal()" title="Сменить тему">
                    <i class="fas fa-palette"></i>
                </button>
            </div>
            
            <div class="nav">
                <div class="nav-title">
                    <span>Избранное</span>
                    <button class="add-btn" onclick="openAddFavoriteModal()" title="Добавить заметку">
                        <i class="fas fa-plus"></i>
                    </button>
                </div>
                <div id="favorites-nav">
                    <div class="nav-item" onclick="openFavorites()">
                        <i class="fas fa-star"></i>
                        <span>Все заметки</span>
                    </div>
                </div>
                
                <div class="nav-title">
                    <span>Каналы</span>
                    <button class="add-btn" onclick="openCreateChannelModal()">
                        <i class="fas fa-plus"></i>
                    </button>
                </div>
                <div id="channels">
                    <div class="nav-item" onclick="openRoom('channel_general', 'channel', 'General')">
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
        <div class="chat-area" id="chat-area">
            <div class="chat-header">
                <button class="back-btn" onclick="goBack()">
                    <i class="fas fa-arrow-left"></i>
                </button>
                <span id="chat-title">Добро пожаловать!</span>
                <div class="channel-actions" id="channel-actions" style="display: none;">
                    <button class="channel-btn" onclick="openChannelSettings()">
                        <i class="fas fa-cog"></i>
                    </button>
                    <button class="channel-btn" onclick="openRenameModal()">
                        <i class="fas fa-edit"></i>
                    </button>
                </div>
            </div>
            
            <div class="categories-filter" id="categories-filter" style="display: none;">
                <button class="category-filter-btn active" onclick="filterFavorites('all')">Все</button>
            </div>
            
            <div class="messages" id="messages">
                <div class="empty-chat" id="welcome-message">
                    <i class="fas fa-comments"></i>
                    <h3>Добро пожаловать в Tandau Messenger!</h3>
                    <p>Выберите чат, канал или избранное чтобы начать общение</p>
                </div>
                
                <div id="favorites-grid" class="favorites-grid" style="display: none;"></div>
                
                <div id="channel-settings" style="display: none;"></div>
                
                <div id="chat-messages" class="message-container" style="display: none;"></div>
            </div>
            
            <div class="input-area" id="input-area" style="display: none;">
                <div class="input-row">
                    <button class="attachment-btn" onclick="document.getElementById('file-input').click()" title="Прикрепить файл">
                        <i class="fas fa-paperclip"></i>
                    </button>
                    <input type="file" id="file-input" accept="image/*,video/*,text/*,.pdf,.doc,.docx" style="display:none" onchange="handleFileSelect(this)">
                    <textarea class="msg-input" id="msg-input" placeholder="Написать сообщение..." rows="1" onkeydown="handleKeydown(event)"></textarea>
                    <button class="send-btn" onclick="sendMessage()" title="Отправить">
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
                <button class="btn btn-primary" onclick="setTheme('light')" style="margin-bottom: 10px; width: 100%;">
                    <i class="fas fa-sun"></i> Светлая тема
                </button>
                <button class="btn btn-primary" onclick="setTheme('dark')" style="margin-bottom: 10px; width: 100%;">
                    <i class="fas fa-moon"></i> Темная тема
                </button>
                <button class="btn btn-primary" onclick="setTheme('auto')" style="width: 100%;">
                    <i class="fas fa-adjust"></i> Автоматически
                </button>
            </div>
            <button class="btn btn-secondary" onclick="closeThemeModal()" style="width: 100%;">Закрыть</button>
        </div>
    </div>

    <div class="modal" id="avatar-modal">
        <div class="modal-content">
            <h3>Смена аватарки</h3>
            <div class="avatar-upload" style="text-align: center;">
                <div class="avatar" id="avatar-preview" style="width: 100px; height: 100px; margin: 0 auto 20px; cursor: pointer;" onclick="document.getElementById('avatar-input').click()"></div>
                <input type="file" id="avatar-input" accept="image/*" style="display:none" onchange="previewAvatar(this)">
                <div style="display: flex; gap: 10px; justify-content: center; margin-top: 20px;">
                    <button class="btn btn-primary" onclick="uploadAvatar()">Загрузить</button>
                    <button class="btn btn-secondary" onclick="removeAvatar()">Удалить</button>
                    <button class="btn btn-secondary" onclick="closeAvatarModal()">Отмена</button>
                </div>
            </div>
        </div>
    </div>

    <div class="modal" id="create-channel-modal">
        <div class="modal-content">
            <h3>Создать канал</h3>
            <div class="form-group">
                <input type="text" class="form-control" id="channel-name" placeholder="Идентификатор канала (латинские буквы, цифры, _)">
                <input type="text" class="form-control" id="channel-display-name" placeholder="Отображаемое название">
                <input type="text" class="form-control" id="channel-description" placeholder="Описание">
                <label style="color: white; display: flex; align-items: center; gap: 10px; margin-top: 10px;">
                    <input type="checkbox" id="channel-private">
                    Приватный канал
                </label>
            </div>
            <div style="display: flex; gap: 10px;">
                <button class="btn btn-primary" onclick="createChannel()">Создать</button>
                <button class="btn btn-secondary" onclick="closeCreateChannelModal()">Отмена</button>
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
                <button class="btn btn-secondary" onclick="closeRenameModal()">Отмена</button>
            </div>
        </div>
    </div>

    <div class="modal" id="add-user-modal">
        <div class="modal-content">
            <h3>Добавить пользователя в канал</h3>
            <div class="form-group">
                <label class="form-label">Пользователь</label>
                <select class="form-control" id="user-select" style="padding: 12px;">
                    <option value="">Выберите пользователя...</option>
                </select>
            </div>
            <div style="display: flex; gap: 10px;">
                <button class="btn btn-primary" onclick="addUserToChannel()">Добавить</button>
                <button class="btn btn-secondary" onclick="closeAddUserModal()">Отмена</button>
            </div>
        </div>
    </div>

    <div class="modal" id="add-favorite-modal">
        <div class="modal-content">
            <h3>Добавить в избранное</h3>
            <div class="form-group">
                <label class="form-label">Текст заметки</label>
                <textarea class="form-control" id="favorite-content" placeholder="Введите текст заметки..." rows="4"></textarea>
            </div>
            <div class="form-group">
                <label class="form-label">Категория</label>
                <input type="text" class="form-control" id="favorite-category" placeholder="Например: идеи, ссылки, работа" value="general">
            </div>
            <div class="form-group">
                <label class="form-label">Файл (опционально)</label>
                <input type="file" class="form-control" id="favorite-file" accept="image/*,video/*,text/*,.pdf,.doc,.docx" style="border: none; background: transparent;">
                <div id="favorite-file-preview" style="margin-top: 10px;"></div>
            </div>
            <div style="display: flex; gap: 10px;">
                <button class="btn btn-primary" onclick="saveFavorite()">Сохранить</button>
                <button class="btn btn-secondary" onclick="closeAddFavoriteModal()">Отмена</button>
            </div>
        </div>
    </div>

    <script src="https://cdnjs.cloudflare.com/ajax/libs/socket.io/4.7.2/socket.io.js"></script>
    <script>
        const socket = io();
        const user = "{username}";
        let currentRoom = "";
        let roomType = "";
        let currentChannel = "";
        let currentCategory = "all";
        let isMobile = window.innerWidth <= 768;

        // Инициализация при загрузке
        window.onload = function() {{
            checkMobile();
            loadUserAvatar();
            loadUserChannels();
            loadUsers();
            loadPersonalChats();
            loadFavoritesCategories();
            
            if (isMobile) {{
                document.getElementById('sidebar').classList.remove('active');
            }}
            
            window.addEventListener('resize', checkMobile);
        }};

        function checkMobile() {{
            isMobile = window.innerWidth <= 768;
        }}

        function toggleSidebar() {{
            document.getElementById('sidebar').classList.toggle('active');
        }}

        function goBack() {{
            if (isMobile) {{
                document.getElementById('sidebar').classList.add('active');
            }}
        }}

        // Загрузка аватарки пользователя
        function loadUserAvatar() {{
            fetch('/user_info/' + user)
                .then(r => r.json())
                .then(userInfo => {{
                    if (userInfo.success) {{
                        const avatar = document.getElementById('user-avatar');
                        const preview = document.getElementById('avatar-preview');
                        
                        if (userInfo.avatar_path) {{
                            avatar.style.backgroundImage = `url(${{userInfo.avatar_path}})`;
                            avatar.textContent = '';
                            preview.style.backgroundImage = `url(${{userInfo.avatar_path}})`;
                            preview.textContent = '';
                        }} else {{
                            avatar.style.background = userInfo.avatar_color || 'var(--primary-gradient)';
                            avatar.textContent = user.slice(0, 2).toUpperCase();
                            preview.style.background = userInfo.avatar_color || 'var(--primary-gradient)';
                            preview.textContent = user.slice(0, 2).toUpperCase();
                        }}
                    }}
                }});
        }}

        // Загрузка категорий избранного
        function loadFavoritesCategories() {{
            fetch('/get_favorite_categories')
                .then(r => r.json())
                .then(data => {{
                    if (data.success) {{
                        const filterContainer = document.getElementById('categories-filter');
                        filterContainer.innerHTML = '';
                        
                        const allBtn = document.createElement('button');
                        allBtn.className = 'category-filter-btn active';
                        allBtn.textContent = 'Все';
                        allBtn.onclick = () => filterFavorites('all');
                        filterContainer.appendChild(allBtn);
                        
                        data.categories.forEach(category => {{
                            const btn = document.createElement('button');
                            btn.className = 'category-filter-btn';
                            btn.textContent = category || 'Без категории';
                            btn.onclick = () => filterFavorites(category);
                            filterContainer.appendChild(btn);
                        }});
                    }}
                }});
        }}

        // Загрузка избранного
        function loadFavorites(category = null) {{
            let url = '/get_favorites';
            if (category && category !== 'all') {{
                url += `?category=${{encodeURIComponent(category)}}`;
            }}
            
            fetch(url)
                .then(r => r.json())
                .then(data => {{
                    if (data.success) {{
                        const grid = document.getElementById('favorites-grid');
                        grid.style.display = 'grid';
                        
                        if (data.favorites.length === 0) {{
                            grid.innerHTML = `
                                <div class="empty-favorites" style="grid-column: 1/-1;">
                                    <i class="fas fa-star"></i>
                                    <h3>Пока ничего нет</h3>
                                    <p>Добавьте свои заметки, фото или видео</p>
                                    <button class="btn btn-primary" onclick="openAddFavoriteModal()" style="margin-top: 15px;">
                                        <i class="fas fa-plus"></i> Добавить заметку
                                    </button>
                                </div>
                            `;
                        }} else {{
                            grid.innerHTML = '';
                            data.favorites.forEach(favorite => {{
                                const item = createFavoriteItem(favorite);
                                grid.appendChild(item);
                            }});
                        }}
                    }}
                }});
        }}

        // Создание элемента избранного
        function createFavoriteItem(favorite) {{
            const item = document.createElement('div');
            item.className = `favorite-item ${{favorite.is_pinned ? 'pinned' : ''}}`;
            item.id = `favorite-${{favorite.id}}`;
            
            let contentHTML = '';
            
            if (favorite.content) {{
                contentHTML += `<div class="favorite-content">${{favorite.content}}</div>`;
            }}
            
            if (favorite.file_path) {{
                if (favorite.file_type === 'image' || favorite.file_name.match(/\.(jpg|jpeg|png|gif|webp)$/i)) {{
                    contentHTML += `
                        <div class="favorite-file">
                            <img src="${{favorite.file_path}}" alt="${{favorite.file_name}}" onclick="openFilePreview('${{favorite.file_path}}')">
                        </div>
                    `;
                }} else if (favorite.file_type === 'video' || favorite.file_name.match(/\.(mp4|webm|mov)$/i)) {{
                    contentHTML += `
                        <div class="favorite-file">
                            <video src="${{favorite.file_path}}" controls></video>
                        </div>
                    `;
                }} else {{
                    contentHTML += `
                        <div class="favorite-content">
                            <i class="fas fa-file"></i> ${{favorite.file_name}}
                            <br>
                            <a href="${{favorite.file_path}}" target="_blank" style="color: white; opacity: 0.8;">Скачать</a>
                        </div>
                    `;
                }}
            }}
            
            const category = favorite.category && favorite.category !== 'general' ? 
                `<span class="category-badge">${{favorite.category}}</span>` : '';
            
            const date = new Date(favorite.created_at).toLocaleDateString('ru-RU', {{
                day: 'numeric',
                month: 'short',
                year: 'numeric'
            }});
            
            item.innerHTML = `
                <div class="favorite-actions">
                    <button class="favorite-action-btn" onclick="togglePinFavorite(${{favorite.id}})" title="${{favorite.is_pinned ? 'Открепить' : 'Закрепить'}}">
                        <i class="fas fa-thumbtack"></i>
                    </button>
                    <button class="favorite-action-btn" onclick="deleteFavorite(${{favorite.id}})" title="Удалить">
                        <i class="fas fa-trash"></i>
                    </button>
                </div>
                ${{contentHTML}}
                <div class="favorite-meta">
                    <span>${{date}}</span>
                    ${{category}}
                </div>
            `;
            
            return item;
        }}

        // Функции для работы с чатом (остальные функции аналогичны предыдущей версии)
        // ... (остальной JavaScript код остается таким же, как в предыдущей версии)
        
        // Основные функции для работы с мессенджером
        function openFavorites() {{
            currentRoom = "favorites";
            roomType = "favorites";
            
            document.getElementById('chat-title').textContent = 'Избранное';
            document.getElementById('categories-filter').style.display = 'flex';
            document.getElementById('welcome-message').style.display = 'none';
            document.getElementById('channel-settings').style.display = 'none';
            document.getElementById('chat-messages').style.display = 'none';
            document.getElementById('input-area').style.display = 'none';
            document.getElementById('channel-actions').style.display = 'none';
            
            loadFavorites(currentCategory === 'all' ? null : currentCategory);
            
            if (isMobile) {{
                document.getElementById('sidebar').classList.remove('active');
            }}
        }}

        function openRoom(room, type, title) {{
            currentRoom = room;
            roomType = type;
            currentChannel = type === 'channel' ? room.replace('channel_', '') : '';
            
            document.getElementById('chat-title').textContent = type === 'channel' ? '# ' + title : title;
            document.getElementById('categories-filter').style.display = 'none';
            document.getElementById('welcome-message').style.display = 'none';
            document.getElementById('favorites-grid').style.display = 'none';
            document.getElementById('channel-settings').style.display = 'none';
            document.getElementById('chat-messages').style.display = 'block';
            document.getElementById('input-area').style.display = 'flex';
            
            if (type === 'channel') {{
                document.getElementById('channel-actions').style.display = 'flex';
            }} else {{
                document.getElementById('channel-actions').style.display = 'none';
            }}
            
            // Загружаем сообщения
            loadMessages(room);
            
            if (isMobile) {{
                document.getElementById('sidebar').classList.remove('active');
            }}
        }}

        function loadMessages(roomName) {{
            fetch('/get_messages/' + roomName)
                .then(r => r.json())
                .then(messages => {{
                    const messagesContainer = document.getElementById('chat-messages');
                    messagesContainer.innerHTML = '';
                    
                    if (messages && Array.isArray(messages) && messages.length > 0) {{
                        messages.forEach(msg => {{
                            addMessageToChat(msg);
                        }});
                    }}
                    
                    messagesContainer.scrollTop = messagesContainer.scrollHeight;
                }});
        }}

        function addMessageToChat(data) {{
            const messagesContainer = document.getElementById('chat-messages');
            
            const message = document.createElement('div');
            message.className = `message ${{data.user === user ? 'own' : 'other'}}`;
            
            const avatar = document.createElement('div');
            avatar.className = 'message-avatar';
            avatar.style.background = data.color || 'var(--primary-gradient)';
            
            if (data.avatar_path) {{
                avatar.style.backgroundImage = `url(${{data.avatar_path}})`;
            }} else if (data.user !== user) {{
                avatar.textContent = data.user.slice(0, 2).toUpperCase();
            }}
            
            const content = document.createElement('div');
            content.className = 'message-content';
            
            if (data.user !== user) {{
                const sender = document.createElement('div');
                sender.className = 'message-sender';
                sender.textContent = data.user;
                content.appendChild(sender);
            }}
            
            if (data.message) {{
                const text = document.createElement('div');
                text.className = 'message-text';
                text.innerHTML = data.message.replace(/\\n/g, '<br>');
                content.appendChild(text);
            }}
            
            if (data.file) {{
                const fileContainer = document.createElement('div');
                fileContainer.className = 'message-file';
                
                if (data.file.match(/\.(mp4|webm|mov)$/)) {{
                    const video = document.createElement('video');
                    video.src = data.file;
                    video.controls = true;
                    fileContainer.appendChild(video);
                }} else {{
                    const img = document.createElement('img');
                    img.src = data.file;
                    img.alt = data.file_name || 'Файл';
                    img.onclick = () => window.open(data.file, '_blank');
                    fileContainer.appendChild(img);
                }}
                
                content.appendChild(fileContainer);
            }}
            
            const time = document.createElement('div');
            time.className = 'message-time';
            time.textContent = data.timestamp || new Date().toLocaleTimeString([], {{ hour: '2-digit', minute: '2-digit' }});
            content.appendChild(time);
            
            message.appendChild(avatar);
            message.appendChild(content);
            messagesContainer.appendChild(message);
            
            messagesContainer.scrollTop = messagesContainer.scrollHeight;
        }}

        function sendMessage() {{
            const input = document.getElementById('msg-input');
            const msg = input.value.trim();
            const fileInput = document.getElementById('file-input');
            
            if (!msg && !fileInput.files[0]) return;
            
            const data = {{ 
                message: msg, 
                room: currentRoom, 
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

        socket.on('message', (data) => {{
            if (data.room === currentRoom) {{
                addMessageToChat(data);
            }}
        }});

        // Остальные функции (handleKeydown, resetInput, autoResizeTextarea и т.д.)
        // ... (остальной JavaScript код остается таким же)
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
        messages = get_messages_for_room(room)
        return jsonify(messages)

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
        user_color = user_info['avatar_color'] if user_info else '#6366F1'
        user_avatar_path = user_info['avatar_path'] if user_info else None
        
        # Отправляем сообщение
        emit('message', {
            'user': session['username'], 
            'message': msg, 
            'file': file_path, 
            'fileType': file_type,
            'fileName': file_name or saved_file_name,
            'color': user_color,
            'avatar_path': user_avatar_path,
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
    socketio.run(app, host='0.0.0.0', port=port, debug=True, allow_unsafe_werkzeug=True)
