# web_messenger.py - Tandau Messenger (Mobile Optimized)
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
        
        # Возвращаем ту же современную страницу входа/регистрации с логотипом
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
                        <!-- Блок "Политика конфиденциальности" в стиле жидкое стекло -->
                        <div class="glass-terms-container">
                            <div class="glass-header">
                                <div class="glass-icon">
                                    <i class="fas fa-shield-alt"></i>
                                </div>
                                <h2 class="glass-title">Политика конфиденциальности Tandau Messenger</h2>
                                <div class="glass-subtitle">Дата вступления в силу: 6 декабря 2025 г.</div>
                            </div>
                            
                            <div class="glass-content">
                                <div class="glass-section">
                                    <h3 class="section-title"><i class="fas fa-database"></i> 1. Сбор информации</h3>
                                    <div class="section-content">
                                        <p>Мы собираем ограниченную информацию для обеспечения работы сервиса:</p>
                                        <div class="glass-list">
                                            <div class="list-item">
                                                <div class="list-icon"><i class="fas fa-user-circle"></i></div>
                                                <div class="list-text"><span class="highlight">Учетные данные</span>: имя пользователя и хэш пароля</div>
                                            </div>
                                            <div class="list-item">
                                                <div class="list-icon"><i class="fas fa-comment-alt"></i></div>
                                                <div class="list-text"><span class="highlight">Контент сообщений</span>: текст, медиафайлы и файлы</div>
                                            </div>
                                            <div class="list-item">
                                                <div class="list-icon"><i class="fas fa-network-wired"></i></div>
                                                <div class="list-text"><span class="highlight">Технические данные</span>: IP-адрес, тип устройства, версия браузера</div>
                                            </div>
                                            <div class="list-item">
                                                <div class="list-icon"><i class="fas fa-history"></i></div>
                                                <div class="list-text"><span class="highlight">Активность</span>: время входа, активные сессии, использование функций</div>
                                            </div>
                                        </div>
                                        <p class="contact-note">Мы не собираем избыточные персональные данные. Вся информация используется строго для работы сервиса.</p>
                                    </div>
                                </div>
                                
                                <div class="glass-section">
                                    <h3 class="section-title"><i class="fas fa-cogs"></i> 2. Использование информации</h3>
                                    <div class="section-content">
                                        <p>Собранная информация используется исключительно для:</p>
                                        <div class="glass-list">
                                            <div class="list-item">
                                                <div class="list-icon"><i class="fas fa-rocket"></i></div>
                                                <div class="list-text"><span class="highlight">Работа сервиса</span>: доставка сообщений, синхронизация чатов</div>
                                            </div>
                                            <div class="list-item">
                                                <div class="list-icon"><i class="fas fa-shield"></i></div>
                                                <div class="list-text"><span class="highlight">Безопасность</span>: защита от злоупотреблений и мошенничества</div>
                                            </div>
                                            <div class="list-item">
                                                <div class="list-icon"><i class="fas fa-wrench"></i></div>
                                                <div class="list-text"><span class="highlight">Техподдержка</span>: решение технических проблем пользователей</div>
                                            </div>
                                            <div class="list-item">
                                                <div class="list-icon"><i class="fas fa-chart-line"></i></div>
                                                <div class="list-text"><span class="highlight">Аналитика</span>: улучшение пользовательского опыта (анонимно)</div>
                                            </div>
                                        </div>
                                    </div>
                                </div>
                                
                                <div class="glass-section">
                                    <h3 class="section-title"><i class="fas fa-lock"></i> 3. Защита данных</h3>
                                    <div class="section-content">
                                        <p>Мы применяем многоуровневую защиту ваших данных:</p>
                                        <div class="glass-list">
                                            <div class="list-item">
                                                <div class="list-icon"><i class="fas fa-key"></i></div>
                                                <div class="list-text"><span class="highlight">Шифрование</span>: все сообщения шифруются при передаче</div>
                                            </div>
                                            <div class="list-item">
                                                <div class="list-icon"><i class="fas fa-server"></i></div>
                                                <div class="list-text"><span class="highlight">Безопасное хранение</span>: данные хранятся на защищенных серверах</div>
                                            </div>
                                            <div class="list-item">
                                                <div class="list-icon"><i class="fas fa-user-shield"></i></div>
                                                <div class="list-text"><span class="highlight">Контроль доступа</span>: строгий доступ к данным только для технического персонала</div>
                                            </div>
                                            <div class="list-item">
                                                <div class="list-icon"><i class="fas fa-sync-alt"></i></div>
                                                <div class="list-text"><span class="highlight">Регулярные аудиты</span>: периодическая проверка систем безопасности</div>
                                            </div>
                                        </div>
                                    </div>
                                </div>
                                
                                <div class="glass-section">
                                    <h3 class="section-title"><i class="fas fa-user-check"></i> 4. Права пользователей</h3>
                                    <div class="section-content">
                                        <p>Вы имеете полный контроль над своими данными:</p>
                                        <div class="glass-list">
                                            <div class="list-item">
                                                <div class="list-icon"><i class="fas fa-eye"></i></div>
                                                <div class="list-text"><span class="highlight">Право на доступ</span>: запрос информации о хранящихся данных</div>
                                            </div>
                                            <div class="list-item">
                                                <div class="list-icon"><i class="fas fa-edit"></i></div>
                                                <div class="list-text"><span class="highlight">Право на исправление</span>: обновление неточной информации</div>
                                            </div>
                                            <div class="list-item">
                                                <div class="list-icon"><i class="fas fa-trash-alt"></i></div>
                                                <div class="list-text"><span class="highlight">Право на удаление</span>: полное удаление учетной записи и данных</div>
                                            </div>
                                            <div class="list-item">
                                                <div class="list-icon"><i class="fas fa-ban"></i></div>
                                                <div class="list-text"><span class="highlight">Право на отзыв согласия</span>: прекращение обработки данных</div>
                                            </div>
                                        </div>
                                        <p class="contact-note">Для реализации этих прав обратитесь в поддержку через контактные данные ниже.</p>
                                    </div>
                                </div>
                                
                                <div class="glass-section">
                                    <h3 class="section-title"><i class="fas fa-cookie-bite"></i> 5. Файлы cookie и технологии отслеживания</h3>
                                    <div class="section-content">
                                        <p>Мы используем минимальные технологии для улучшения опыта:</p>
                                        <div class="glass-list">
                                            <div class="list-item">
                                                <div class="list-icon"><i class="fas fa-cookie"></i></div>
                                                <div class="list-text"><span class="highlight">Сессионные куки</span>: только для поддержания входа в систему</div>
                                            </div>
                                            <div class="list-item">
                                                <div class="list-icon"><i class="fas fa-tachometer-alt"></i></div>
                                                <div class="list-text"><span class="highlight">Аналитические куки</span>: анонимная статистика использования</div>
                                            </div>
                                            <div class="list-item">
                                                <div class="list-icon"><i class="fas fa-sliders-h"></i></div>
                                                <div class="list-text"><span class="highlight">Настройки</span>: сохранение предпочтений пользователя</div>
                                            </div>
                                        </div>
                                        <p>Вы можете отключить cookies в настройках браузера, но это может ограничить функциональность.</p>
                                    </div>
                                </div>
                                
                                <div class="glass-section">
                                    <h3 class="section-title"><i class="fas fa-users"></i> 6. Третьи стороны</h3>
                                    <div class="section-content">
                                        <p>Мы не продаем и не передаем ваши данные третьим лицам.</p>
                                        <div class="glass-list negative">
                                            <div class="list-item">
                                                <div class="list-icon"><i class="fas fa-handshake-slash"></i></div>
                                                <div class="list-text"><span class="highlight">Нет продажи данных</span>: мы никогда не продаем пользовательские данные</div>
                                            </div>
                                            <div class="list-item">
                                                <div class="list-icon"><i class="fas fa-user-friends"></i></div>
                                                <div class="list-text"><span class="highlight">Ограниченный доступ</span>: данные доступны только необходимым техническим службам</div>
                                            </div>
                                            <div class="list-item">
                                                <div class="list-icon"><i class="fas fa-gavel"></i></div>
                                                <div class="list-text"><span class="highlight">Исключения по закону</span>: передача данных только по официальным запросам правоохранительных органов</div>
                                            </div>
                                        </div>
                                    </div>
                                </div>
                                
                                <div class="glass-section">
                                    <h3 class="section-title"><i class="fas fa-sync-alt"></i> 7. Изменения политики</h3>
                                    <div class="section-content">
                                        <p>Мы уведомляем пользователей о всех значительных изменениях:</p>
                                        <div class="glass-list">
                                            <div class="list-item">
                                                <div class="list-icon"><i class="fas fa-bell"></i></div>
                                                <div class="list-text"><span class="highlight">Уведомление в приложении</span>: сообщение о важных изменениях</div>
                                            </div>
                                            <div class="list-item">
                                                <div class="list-icon"><i class="fas fa-envelope"></i></div>
                                                <div class="list-text"><span class="highlight">Электронная почта</span>: рассылка при серьезных изменениях</div>
                                            </div>
                                            <div class="list-item">
                                                <div class="list-icon"><i class="fas fa-calendar-alt"></i></div>
                                                <div class="list-text"><span class="highlight">Дата вступления в силу</span>: четкое указание времени изменений</div>
                                            </div>
                                        </div>
                                        <p>Продолжая использовать сервис после изменений, вы соглашаетесь с новой версией политики.</p>
                                    </div>
                                </div>
                                
                                <div class="glass-section">
                                    <h3 class="section-title"><i class="fas fa-headset"></i> 8. Контактная информация</h3>
                                    <div class="section-content">
                                        <p>По вопросам конфиденциальности и защиты данных:</p>
                                        <a href="https://vk.com/rsaltyyt" target="_blank" class="glass-link contact-link">
                                            <i class="fab fa-vk"></i> https://vk.com/rsaltyyt
                                        </a>
                                        <p class="contact-note">Мы отвечаем на запросы в течение 7 рабочих дней. Для срочных вопросов используйте вышеуказанную ссылку.</p>
                                    </div>
                                </div>
                            </div>
                            
                            <div class="glass-footer">
                                <div class="version-info">
                                    <i class="fas fa-history"></i>
                                    <span>Актуальная версия: 2.1 (6 декабря 2025 г.)</span>
                                </div>
                                
                                <div class="download-section glass-download">
                                    <p>Полная версия документа для сохранения:</p>
                                    <a href="/static/docs/privacy_policy.pdf" class="download-btn glass-btn" download="Tandau_Политика_конфиденциальности.pdf">
                                        <i class="fas fa-file-pdf"></i>
                                        Скачать PDF (198 KB)
                                        <i class="fas fa-download"></i>
                                    </a>
                                </div>
                            </div>
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
        
        # Генерируем HTML с современным мобильным дизайном
        return f'''
<!DOCTYPE html>
<html lang="ru" data-theme="{theme}">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0, maximum-scale=1.0, user-scalable=no, viewport-fit=cover">
    <title>Tandau Chat - {username}</title>
    <link rel="stylesheet" href="https://cdnjs.cloudflare.com/ajax/libs/font-awesome/6.5.0/css/all.min.css">
    <link rel="stylesheet" href="https://cdnjs.cloudflare.com/ajax/libs/font-awesome/6.5.0/css/all.min.css">
    <style>
        :root {{
            --primary: #6366f1;
            --primary-dark: #4f46e5;
            --primary-light: #818cf8;
            --success: #10b981;
            --warning: #f59e0b;
            --danger: #ef4444;
            --gray-50: #f9fafb;
            --gray-100: #f3f4f6;
            --gray-200: #e5e7eb;
            --gray-300: #d1d5db;
            --gray-400: #9ca3af;
            --gray-500: #6b7280;
            --gray-600: #4b5563;
            --gray-700: #374151;
            --gray-800: #1f2937;
            --gray-900: #111827;
            --text-primary: var(--gray-900);
            --text-secondary: var(--gray-600);
            --bg-primary: #ffffff;
            --bg-secondary: var(--gray-50);
            --bg-tertiary: var(--gray-100);
            --border: var(--gray-200);
            --shadow-sm: 0 1px 2px 0 rgba(0, 0, 0, 0.05);
            --shadow: 0 4px 6px -1px rgba(0, 0, 0, 0.1), 0 2px 4px -1px rgba(0, 0, 0, 0.06);
            --shadow-md: 0 10px 15px -3px rgba(0, 0, 0, 0.1), 0 4px 6px -2px rgba(0, 0, 0, 0.05);
            --radius-sm: 0.375rem;
            --radius: 0.5rem;
            --radius-md: 0.75rem;
            --radius-lg: 1rem;
            --radius-xl: 1.5rem;
            --radius-full: 9999px;
            --header-height: 64px;
            --input-height: 56px;
            --safe-area-bottom: env(safe-area-inset-bottom, 0px);
        }}

        [data-theme="dark"] {{
            --primary: #818cf8;
            --primary-dark: #6366f1;
            --primary-light: #a5b4fc;
            --success: #34d399;
            --warning: #fbbf24;
            --danger: #f87171;
            --gray-50: #111827;
            --gray-100: #1f2937;
            --gray-200: #374151;
            --gray-300: #4b5563;
            --gray-400: #6b7280;
            --gray-500: #9ca3af;
            --gray-600: #d1d5db;
            --gray-700: #e5e7eb;
            --gray-800: #f3f4f6;
            --gray-900: #f9fafb;
            --text-primary: var(--gray-900);
            --text-secondary: var(--gray-600);
            --bg-primary: #1a1a1a;
            --bg-secondary: #2d2d2d;
            --bg-tertiary: #3d3d3d;
            --border: var(--gray-800);
        }}

        * {{
            margin: 0;
            padding: 0;
            box-sizing: border-box;
            -webkit-tap-highlight-color: transparent;
            -webkit-font-smoothing: antialiased;
            -moz-osx-font-smoothing: grayscale;
        }}

        body {{
            font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, Helvetica, Arial, sans-serif;
            background: var(--bg-primary);
            color: var(--text-primary);
            height: 100vh;
            height: 100dvh;
            overflow: hidden;
            touch-action: manipulation;
            overscroll-behavior: none;
        }}

        /* Основной контейнер */
        .app-container {{
            position: fixed;
            top: 0;
            left: 0;
            right: 0;
            bottom: 0;
            display: flex;
            background: var(--bg-primary);
            overflow: hidden;
        }}

        /* Сайдбар для мобильных */
        .sidebar {{
            position: fixed;
            top: 0;
            left: 0;
            bottom: 0;
            width: 100%;
            background: var(--bg-primary);
            z-index: 100;
            transform: translateX(-100%);
            transition: transform 0.3s cubic-bezier(0.4, 0, 0.2, 1);
            display: flex;
            flex-direction: column;
            overflow: hidden;
            will-change: transform;
        }}

        .sidebar.active {{
            transform: translateX(0);
            box-shadow: var(--shadow-md);
        }}

        /* Хедер сайдбара */
        .sidebar-header {{
            height: var(--header-height);
            padding: 0 16px;
            display: flex;
            align-items: center;
            justify-content: space-between;
            background: var(--bg-primary);
            border-bottom: 1px solid var(--border);
            flex-shrink: 0;
        }}

        .sidebar-header h1 {{
            font-size: 1.5rem;
            font-weight: 700;
            color: var(--primary);
            display: flex;
            align-items: center;
            gap: 8px;
        }}

        .close-sidebar {{
            width: 40px;
            height: 40px;
            border-radius: var(--radius-full);
            background: var(--bg-tertiary);
            border: none;
            color: var(--text-primary);
            display: flex;
            align-items: center;
            justify-content: center;
            cursor: pointer;
            transition: all 0.2s ease;
        }}

        .close-sidebar:hover {{
            background: var(--border);
        }}

        /* Контент сайдбара */
        .sidebar-content {{
            flex: 1;
            overflow-y: auto;
            -webkit-overflow-scrolling: touch;
            padding-bottom: calc(20px + var(--safe-area-bottom));
        }}

        /* Пользовательская секция */
        .user-section {{
            padding: 20px 16px;
            display: flex;
            align-items: center;
            gap: 12px;
            border-bottom: 1px solid var(--border);
        }}

        .user-avatar {{
            width: 48px;
            height: 48px;
            border-radius: var(--radius-full);
            background: var(--primary);
            color: white;
            display: flex;
            align-items: center;
            justify-content: center;
            font-weight: 600;
            font-size: 1.125rem;
            position: relative;
            flex-shrink: 0;
            background-size: cover;
            background-position: center;
        }}

        .user-avatar::after {{
            content: '';
            position: absolute;
            bottom: 2px;
            right: 2px;
            width: 10px;
            height: 10px;
            background: var(--success);
            border-radius: var(--radius-full);
            border: 2px solid var(--bg-primary);
        }}

        .user-info {{
            flex: 1;
            min-width: 0;
        }}

        .user-name {{
            font-weight: 600;
            font-size: 1rem;
            margin-bottom: 4px;
            overflow: hidden;
            text-overflow: ellipsis;
            white-space: nowrap;
        }}

        .user-status {{
            font-size: 0.875rem;
            color: var(--success);
            display: flex;
            align-items: center;
            gap: 4px;
        }}

        .settings-btn {{
            width: 40px;
            height: 40px;
            border-radius: var(--radius-full);
            background: var(--bg-tertiary);
            border: none;
            color: var(--text-primary);
            display: flex;
            align-items: center;
            justify-content: center;
            cursor: pointer;
            transition: all 0.2s ease;
        }}

        .settings-btn:hover {{
            background: var(--border);
        }}

        /* Навигация */
        .nav-section {{
            padding: 16px 0;
        }}

        .nav-title {{
            padding: 8px 16px;
            font-size: 0.75rem;
            font-weight: 600;
            color: var(--text-secondary);
            text-transform: uppercase;
            letter-spacing: 0.05em;
            display: flex;
            justify-content: space-between;
            align-items: center;
        }}

        .add-btn {{
            width: 28px;
            height: 28px;
            border-radius: var(--radius-full);
            background: var(--bg-tertiary);
            border: none;
            color: var(--text-primary);
            display: flex;
            align-items: center;
            justify-content: center;
            cursor: pointer;
            font-size: 0.875rem;
            transition: all 0.2s ease;
        }}

        .add-btn:hover {{
            background: var(--border);
        }}

        .nav-list {{
            list-style: none;
        }}

        .nav-item {{
            padding: 12px 16px;
            display: flex;
            align-items: center;
            gap: 12px;
            cursor: pointer;
            transition: background 0.2s ease;
            border-left: 3px solid transparent;
        }}

        .nav-item:hover {{
            background: var(--bg-tertiary);
        }}

        .nav-item.active {{
            background: var(--bg-tertiary);
            border-left-color: var(--primary);
        }}

        .nav-item-icon {{
            width: 40px;
            height: 40px;
            border-radius: var(--radius-md);
            background: var(--bg-tertiary);
            display: flex;
            align-items: center;
            justify-content: center;
            color: var(--primary);
            font-size: 1.125rem;
            flex-shrink: 0;
        }}

        .nav-item-content {{
            flex: 1;
            min-width: 0;
        }}

        .nav-item-title {{
            font-weight: 500;
            font-size: 0.9375rem;
            margin-bottom: 2px;
            overflow: hidden;
            text-overflow: ellipsis;
            white-space: nowrap;
        }}

        .nav-item-subtitle {{
            font-size: 0.8125rem;
            color: var(--text-secondary);
            overflow: hidden;
            text-overflow: ellipsis;
            white-space: nowrap;
        }}

        .nav-item-badge {{
            padding: 2px 8px;
            background: var(--primary);
            color: white;
            border-radius: var(--radius-full);
            font-size: 0.75rem;
            font-weight: 600;
        }}

        .online-dot {{
            width: 8px;
            height: 8px;
            background: var(--success);
            border-radius: var(--radius-full);
            flex-shrink: 0;
        }}

        /* Основной чат */
        .chat-container {{
            flex: 1;
            display: flex;
            flex-direction: column;
            position: relative;
            overflow: hidden;
        }}

        /* Хедер чата */
        .chat-header {{
            height: var(--header-height);
            padding: 0 16px;
            display: flex;
            align-items: center;
            justify-content: space-between;
            background: var(--bg-primary);
            border-bottom: 1px solid var(--border);
            position: relative;
            z-index: 10;
            flex-shrink: 0;
        }}

        .chat-header-left {{
            display: flex;
            align-items: center;
            gap: 12px;
            flex: 1;
            min-width: 0;
        }}

        .open-sidebar {{
            width: 40px;
            height: 40px;
            border-radius: var(--radius-full);
            background: var(--bg-tertiary);
            border: none;
            color: var(--text-primary);
            display: flex;
            align-items: center;
            justify-content: center;
            cursor: pointer;
            transition: all 0.2s ease;
            flex-shrink: 0;
        }}

        .open-sidebar:hover {{
            background: var(--border);
        }}

        .chat-info {{
            flex: 1;
            min-width: 0;
        }}

        .chat-title {{
            font-weight: 600;
            font-size: 1.125rem;
            margin-bottom: 2px;
            overflow: hidden;
            text-overflow: ellipsis;
            white-space: nowrap;
        }}

        .chat-subtitle {{
            font-size: 0.875rem;
            color: var(--text-secondary);
            overflow: hidden;
            text-overflow: ellipsis;
            white-space: nowrap;
        }}

        .chat-header-right {{
            display: flex;
            align-items: center;
            gap: 8px;
            flex-shrink: 0;
        }}

        .header-action {{
            width: 40px;
            height: 40px;
            border-radius: var(--radius-full);
            background: var(--bg-tertiary);
            border: none;
            color: var(--text-primary);
            display: flex;
            align-items: center;
            justify-content: center;
            cursor: pointer;
            transition: all 0.2s ease;
            flex-shrink: 0;
        }}

        .header-action:hover {{
            background: var(--border);
        }}

        /* Контейнер сообщений */
        .messages-container {{
            flex: 1;
            overflow-y: auto;
            -webkit-overflow-scrolling: touch;
            padding: 16px;
            display: flex;
            flex-direction: column;
            gap: 8px;
            position: relative;
        }}

        /* Даты разделители */
        .date-divider {{
            text-align: center;
            margin: 16px 0;
            position: relative;
        }}

        .date-divider span {{
            background: var(--bg-tertiary);
            color: var(--text-secondary);
            font-size: 0.8125rem;
            padding: 4px 12px;
            border-radius: var(--radius-full);
            display: inline-block;
        }}

        /* Сообщения */
        .message {{
            display: flex;
            gap: 12px;
            padding: 8px;
            border-radius: var(--radius-md);
            max-width: 85%;
            animation: fadeIn 0.3s ease;
        }}

        .message.incoming {{
            align-self: flex-start;
            background: var(--bg-secondary);
        }}

        .message.outgoing {{
            align-self: flex-end;
            background: var(--primary);
            color: white;
        }}

        .message-avatar {{
            width: 32px;
            height: 32px;
            border-radius: var(--radius-full);
            background: var(--bg-tertiary);
            display: flex;
            align-items: center;
            justify-content: center;
            font-weight: 600;
            font-size: 0.875rem;
            flex-shrink: 0;
            background-size: cover;
            background-position: center;
        }}

        .message.outgoing .message-avatar {{
            display: none;
        }}

        .message-content {{
            flex: 1;
            min-width: 0;
        }}

        .message-header {{
            display: flex;
            align-items: center;
            gap: 8px;
            margin-bottom: 4px;
        }}

        .message-sender {{
            font-weight: 600;
            font-size: 0.9375rem;
        }}

        .message-time {{
            font-size: 0.75rem;
            opacity: 0.7;
        }}

        .message.outgoing .message-sender,
        .message.outgoing .message-time {{
            color: rgba(255, 255, 255, 0.9);
        }}

        .message-text {{
            font-size: 0.9375rem;
            line-height: 1.5;
            word-break: break-word;
        }}

        .message-text a {{
            color: var(--primary);
            text-decoration: none;
        }}

        .message.outgoing .message-text a {{
            color: white;
            text-decoration: underline;
        }}

        /* Файлы в сообщениях */
        .message-files {{
            margin-top: 8px;
            display: flex;
            flex-direction: column;
            gap: 8px;
        }}

        .message-file {{
            max-width: 100%;
            border-radius: var(--radius);
            overflow: hidden;
            position: relative;
            cursor: pointer;
        }}

        .message-file img,
        .message-file video {{
            width: 100%;
            max-height: 300px;
            object-fit: cover;
            border-radius: var(--radius);
            display: block;
        }}

        .message-file-overlay {{
            position: absolute;
            bottom: 0;
            left: 0;
            right: 0;
            padding: 12px;
            background: linear-gradient(transparent, rgba(0, 0, 0, 0.7));
            color: white;
            display: flex;
            align-items: center;
            justify-content: space-between;
        }}

        .message-file-name {{
            font-size: 0.875rem;
            font-weight: 500;
            overflow: hidden;
            text-overflow: ellipsis;
            white-space: nowrap;
        }}

        .message-file-size {{
            font-size: 0.75rem;
            opacity: 0.9;
        }}

        .file-download {{
            width: 32px;
            height: 32px;
            border-radius: var(--radius-full);
            background: rgba(255, 255, 255, 0.2);
            backdrop-filter: blur(10px);
            border: none;
            color: white;
            display: flex;
            align-items: center;
            justify-content: center;
            cursor: pointer;
            transition: all 0.2s ease;
        }}

        .file-download:hover {{
            background: rgba(255, 255, 255, 0.3);
        }}

        /* Область ввода */
        .input-container {{
            padding: 16px;
            padding-bottom: calc(16px + var(--safe-area-bottom));
            background: var(--bg-primary);
            border-top: 1px solid var(--border);
            position: relative;
            z-index: 10;
            flex-shrink: 0;
        }}

        .input-wrapper {{
            display: flex;
            align-items: flex-end;
            gap: 12px;
            background: var(--bg-secondary);
            border-radius: var(--radius-xl);
            padding: 8px;
            min-height: var(--input-height);
        }}

        .input-actions {{
            display: flex;
            align-items: center;
            gap: 4px;
            flex-shrink: 0;
        }}

        .input-action {{
            width: 40px;
            height: 40px;
            border-radius: var(--radius-full);
            background: transparent;
            border: none;
            color: var(--text-secondary);
            display: flex;
            align-items: center;
            justify-content: center;
            cursor: pointer;
            transition: all 0.2s ease;
        }}

        .input-action:hover {{
            background: var(--bg-tertiary);
            color: var(--text-primary);
        }}

        .input-action.primary {{
            background: var(--primary);
            color: white;
        }}

        .input-action.primary:hover {{
            background: var(--primary-dark);
        }}

        .message-input {{
            flex: 1;
            min-height: 40px;
            max-height: 120px;
            background: transparent;
            border: none;
            color: var(--text-primary);
            font-size: 0.9375rem;
            line-height: 1.5;
            resize: none;
            outline: none;
            padding: 8px 0;
            font-family: inherit;
        }}

        .message-input::placeholder {{
            color: var(--text-secondary);
        }}

        /* Превью файлов */
        .file-previews {{
            display: flex;
            gap: 8px;
            margin-top: 8px;
            overflow-x: auto;
            -webkit-overflow-scrolling: touch;
            padding-bottom: 4px;
        }}

        .file-preview {{
            position: relative;
            flex-shrink: 0;
        }}

        .file-preview img,
        .file-preview video {{
            width: 80px;
            height: 80px;
            border-radius: var(--radius);
            object-fit: cover;
        }}

        .remove-file {{
            position: absolute;
            top: -4px;
            right: -4px;
            width: 24px;
            height: 24px;
            border-radius: var(--radius-full);
            background: var(--danger);
            color: white;
            border: none;
            display: flex;
            align-items: center;
            justify-content: center;
            cursor: pointer;
            font-size: 0.75rem;
        }}

        /* Избранное */
        .favorites-container {{
            padding: 16px;
            padding-bottom: calc(80px + var(--safe-area-bottom));
        }}

        .favorites-header {{
            margin-bottom: 16px;
        }}

        .favorites-title {{
            font-size: 1.5rem;
            font-weight: 700;
            margin-bottom: 4px;
        }}

        .favorites-subtitle {{
            color: var(--text-secondary);
            font-size: 0.9375rem;
        }}

        .favorites-grid {{
            display: grid;
            grid-template-columns: repeat(auto-fill, minmax(300px, 1fr));
            gap: 16px;
        }}

        @media (max-width: 768px) {{
            .favorites-grid {{
                grid-template-columns: 1fr;
            }}
        }}

        .favorite-item {{
            background: var(--bg-secondary);
            border-radius: var(--radius-md);
            padding: 16px;
            border: 1px solid var(--border);
            position: relative;
            transition: all 0.2s ease;
        }}

        .favorite-item:hover {{
            transform: translateY(-2px);
            box-shadow: var(--shadow);
        }}

        .favorite-item.pinned {{
            border-left: 3px solid var(--warning);
        }}

        .favorite-actions {{
            position: absolute;
            top: 12px;
            right: 12px;
            display: flex;
            gap: 4px;
            opacity: 0;
            transition: opacity 0.2s ease;
        }}

        .favorite-item:hover .favorite-actions {{
            opacity: 1;
        }}

        .favorite-action {{
            width: 28px;
            height: 28px;
            border-radius: var(--radius-full);
            background: var(--bg-tertiary);
            border: none;
            color: var(--text-primary);
            display: flex;
            align-items: center;
            justify-content: center;
            cursor: pointer;
            font-size: 0.75rem;
            transition: all 0.2s ease;
        }}

        .favorite-action:hover {{
            background: var(--border);
        }}

        .favorite-action.delete:hover {{
            background: var(--danger);
            color: white;
        }}

        .favorite-content {{
            margin-bottom: 12px;
            font-size: 0.9375rem;
            line-height: 1.5;
        }}

        .favorite-file {{
            margin-bottom: 12px;
        }}

        .favorite-file img,
        .favorite-file video {{
            width: 100%;
            max-height: 200px;
            border-radius: var(--radius);
            object-fit: cover;
        }}

        .favorite-meta {{
            display: flex;
            justify-content: space-between;
            align-items: center;
            font-size: 0.8125rem;
            color: var(--text-secondary);
        }}

        .favorite-category {{
            padding: 2px 8px;
            background: var(--bg-tertiary);
            border-radius: var(--radius-full);
            font-size: 0.75rem;
        }}

        /* Настройки канала */
        .settings-container {{
            padding: 16px;
            padding-bottom: calc(80px + var(--safe-area-bottom));
        }}

        .settings-section {{
            margin-bottom: 24px;
        }}

        .settings-title {{
            font-size: 1.125rem;
            font-weight: 600;
            margin-bottom: 16px;
            padding-bottom: 8px;
            border-bottom: 1px solid var(--border);
        }}

        .members-list {{
            background: var(--bg-secondary);
            border-radius: var(--radius);
            border: 1px solid var(--border);
            overflow: hidden;
        }}

        .member-item {{
            padding: 12px 16px;
            display: flex;
            align-items: center;
            gap: 12px;
            border-bottom: 1px solid var(--border);
        }}

        .member-item:last-child {{
            border-bottom: none;
        }}

        .member-avatar {{
            width: 40px;
            height: 40px;
            border-radius: var(--radius-full);
            background: var(--bg-tertiary);
            display: flex;
            align-items: center;
            justify-content: center;
            font-weight: 600;
            font-size: 0.875rem;
            flex-shrink: 0;
            background-size: cover;
            background-position: center;
        }}

        .member-info {{
            flex: 1;
            min-width: 0;
        }}

        .member-name {{
            font-weight: 500;
            margin-bottom: 2px;
        }}

        .member-role {{
            font-size: 0.8125rem;
            color: var(--text-secondary);
        }}

        .member-role.admin {{
            color: var(--primary);
        }}

        .member-actions {{
            display: flex;
            gap: 4px;
        }}

        .member-action {{
            width: 32px;
            height: 32px;
            border-radius: var(--radius-full);
            background: var(--bg-tertiary);
            border: none;
            color: var(--text-primary);
            display: flex;
            align-items: center;
            justify-content: center;
            cursor: pointer;
            font-size: 0.875rem;
            transition: all 0.2s ease;
        }}

        .member-action:hover {{
            background: var(--border);
        }}

        .member-action.remove:hover {{
            background: var(--danger);
            color: white;
        }}

        /* Модальные окна */
        .modal-overlay {{
            position: fixed;
            top: 0;
            left: 0;
            right: 0;
            bottom: 0;
            background: rgba(0, 0, 0, 0.5);
            backdrop-filter: blur(4px);
            display: none;
            align-items: center;
            justify-content: center;
            z-index: 1000;
            padding: 16px;
        }}

        .modal {{
            background: var(--bg-primary);
            border-radius: var(--radius-lg);
            max-width: 400px;
            width: 100%;
            max-height: 80vh;
            overflow-y: auto;
            -webkit-overflow-scrolling: touch;
            box-shadow: var(--shadow-md);
            animation: modalSlideIn 0.3s cubic-bezier(0.4, 0, 0.2, 1);
        }}

        .modal-header {{
            padding: 20px 20px 12px;
            border-bottom: 1px solid var(--border);
            display: flex;
            align-items: center;
            justify-content: space-between;
        }}

        .modal-title {{
            font-size: 1.25rem;
            font-weight: 600;
        }}

        .modal-close {{
            width: 36px;
            height: 36px;
            border-radius: var(--radius-full);
            background: var(--bg-tertiary);
            border: none;
            color: var(--text-primary);
            display: flex;
            align-items: center;
            justify-content: center;
            cursor: pointer;
            transition: all 0.2s ease;
        }}

        .modal-close:hover {{
            background: var(--border);
        }}

        .modal-body {{
            padding: 20px;
        }}

        .modal-footer {{
            padding: 12px 20px 20px;
            border-top: 1px solid var(--border);
            display: flex;
            gap: 12px;
            justify-content: flex-end;
        }}

        .form-group {{
            margin-bottom: 16px;
        }}

        .form-label {{
            display: block;
            margin-bottom: 8px;
            font-weight: 500;
            font-size: 0.9375rem;
        }}

        .form-control {{
            width: 100%;
            padding: 12px 16px;
            background: var(--bg-secondary);
            border: 1px solid var(--border);
            border-radius: var(--radius);
            color: var(--text-primary);
            font-size: 0.9375rem;
            font-family: inherit;
            transition: all 0.2s ease;
        }}

        .form-control:focus {{
            outline: none;
            border-color: var(--primary);
            box-shadow: 0 0 0 3px rgba(99, 102, 241, 0.1);
        }}

        .form-control::placeholder {{
            color: var(--text-secondary);
        }}

        .form-textarea {{
            min-height: 100px;
            resize: vertical;
        }}

        .checkbox-group {{
            display: flex;
            align-items: center;
            gap: 8px;
            cursor: pointer;
        }}

        .checkbox {{
            width: 20px;
            height: 20px;
            border-radius: var(--radius-sm);
            border: 2px solid var(--border);
            background: var(--bg-secondary);
            position: relative;
            transition: all 0.2s ease;
        }}

        .checkbox.checked {{
            background: var(--primary);
            border-color: var(--primary);
        }}

        .checkbox.checked::after {{
            content: '✓';
            position: absolute;
            top: 50%;
            left: 50%;
            transform: translate(-50%, -50%);
            color: white;
            font-size: 0.875rem;
        }}

        .checkbox-label {{
            font-size: 0.9375rem;
        }}

        .btn {{
            padding: 10px 20px;
            border-radius: var(--radius);
            font-size: 0.9375rem;
            font-weight: 500;
            border: none;
            cursor: pointer;
            transition: all 0.2s ease;
            font-family: inherit;
            display: inline-flex;
            align-items: center;
            justify-content: center;
            gap: 8px;
        }}

        .btn-primary {{
            background: var(--primary);
            color: white;
        }}

        .btn-primary:hover {{
            background: var(--primary-dark);
        }}

        .btn-secondary {{
            background: var(--bg-tertiary);
            color: var(--text-primary);
        }}

        .btn-secondary:hover {{
            background: var(--border);
        }}

        .btn-danger {{
            background: var(--danger);
            color: white;
        }}

        .btn-danger:hover {{
            background: #dc2626;
        }}

        /* Анимации */
        @keyframes fadeIn {{
            from {{ opacity: 0; transform: translateY(10px); }}
            to {{ opacity: 1; transform: translateY(0); }}
        }}

        @keyframes modalSlideIn {{
            from {{ opacity: 0; transform: translateY(20px) scale(0.95); }}
            to {{ opacity: 1; transform: translateY(0) scale(1); }}
        }}

        @keyframes slideInRight {{
            from {{ transform: translateX(100%); }}
            to {{ transform: translateX(0); }}
        }}

        @keyframes slideOutLeft {{
            from {{ transform: translateX(0); }}
            to {{ transform: translateX(-100%); }}
        }}

        @keyframes slideInLeft {{
            from {{ transform: translateX(-100%); }}
            to {{ transform: translateX(0); }}
        }}

        @keyframes slideOutRight {{
            from {{ transform: translateX(0); }}
            to {{ transform: translateX(100%); }}
        }}

        /* Утилиты */
        .hidden {{
            display: none !important;
        }}

        .empty-state {{
            text-align: center;
            padding: 48px 16px;
            color: var(--text-secondary);
        }}

        .empty-state-icon {{
            font-size: 3rem;
            margin-bottom: 16px;
            opacity: 0.5;
        }}

        .empty-state-title {{
            font-size: 1.125rem;
            font-weight: 600;
            margin-bottom: 8px;
        }}

        .empty-state-description {{
            font-size: 0.9375rem;
        }}

        .loading {{
            display: flex;
            align-items: center;
            justify-content: center;
            padding: 48px;
        }}

        .loading-spinner {{
            width: 40px;
            height: 40px;
            border: 3px solid var(--border);
            border-top-color: var(--primary);
            border-radius: var(--radius-full);
            animation: spin 1s linear infinite;
        }}

        @keyframes spin {{
            to {{ transform: rotate(360deg); }}
        }}

        /* Адаптивность */
        @media (min-width: 768px) {{
            .sidebar {{
                position: relative;
                width: 280px;
                transform: none;
                border-right: 1px solid var(--border);
                flex-shrink: 0;
            }}

            .close-sidebar {{
                display: none;
            }}

            .open-sidebar {{
                display: none;
            }}
        }}

        /* Для очень маленьких экранов */
        @media (max-width: 360px) {{
            .sidebar-header h1 {{
                font-size: 1.25rem;
            }}

            .chat-title {{
                font-size: 1rem;
            }}

            .input-wrapper {{
                padding: 6px;
            }}

            .input-action {{
                width: 36px;
                height: 36px;
            }}

            .message-input {{
                font-size: 0.875rem;
            }}
        }}

        /* Безопасные области */
        @supports (padding: max(0px)) {{
            .input-container {{
                padding-bottom: max(16px, var(--safe-area-bottom));
            }}

            .favorites-container,
            .settings-container {{
                padding-bottom: max(80px, var(--safe-area-bottom));
            }}
        }}

        /* Прокрутка */
        .scrollbar-thin {{
            scrollbar-width: thin;
            scrollbar-color: var(--border) transparent;
        }}

        .scrollbar-thin::-webkit-scrollbar {{
            width: 6px;
            height: 6px;
        }}

        .scrollbar-thin::-webkit-scrollbar-track {{
            background: transparent;
        }}

        .scrollbar-thin::-webkit-scrollbar-thumb {{
            background: var(--border);
            border-radius: 3px;
        }}

        .scrollbar-thin::-webkit-scrollbar-thumb:hover {{
            background: var(--gray-400);
        }}
    </style>
</head>
<body>
    <div class="app-container">
        <!-- Сайдбар -->
        <div class="sidebar scrollbar-thin">
            <div class="sidebar-header">
                <h1><i class="fas fa-comments"></i> Tandau</h1>
                <button class="close-sidebar">
                    <i class="fas fa-times"></i>
                </button>
            </div>
            
            <div class="sidebar-content scrollbar-thin">
                <!-- Пользовательская секция -->
                <div class="user-section">
                    <div class="user-avatar" id="user-avatar"></div>
                    <div class="user-info">
                        <div class="user-name" id="user-name">{username}</div>
                        <div class="user-status">
                            <i class="fas fa-circle"></i>
                            <span>Online</span>
                        </div>
                    </div>
                    <button class="settings-btn" id="settings-btn">
                        <i class="fas fa-cog"></i>
                    </button>
                </div>
                
                <!-- Избранное -->
                <div class="nav-section">
                    <div class="nav-title">
                        <span>Избранное</span>
                        <button class="add-btn" id="add-favorite-btn">
                            <i class="fas fa-plus"></i>
                        </button>
                    </div>
                    <ul class="nav-list">
                        <li class="nav-item" id="nav-favorites" data-view="favorites">
                            <div class="nav-item-icon">
                                <i class="fas fa-star"></i>
                            </div>
                            <div class="nav-item-content">
                                <div class="nav-item-title">Все заметки</div>
                                <div class="nav-item-subtitle" id="favorites-count">Загрузка...</div>
                            </div>
                        </li>
                    </ul>
                </div>
                
                <!-- Каналы -->
                <div class="nav-section">
                    <div class="nav-title">
                        <span>Каналы</span>
                        <button class="add-btn" id="add-channel-btn">
                            <i class="fas fa-plus"></i>
                        </button>
                    </div>
                    <ul class="nav-list" id="channels-list">
                        <li class="nav-item active" data-room="channel_general" data-type="channel" data-name="General">
                            <div class="nav-item-icon">
                                <i class="fas fa-hashtag"></i>
                            </div>
                            <div class="nav-item-content">
                                <div class="nav-item-title">General</div>
                                <div class="nav-item-subtitle">Общий канал</div>
                            </div>
                        </li>
                    </ul>
                </div>
                
                <!-- Личные чаты -->
                <div class="nav-section">
                    <div class="nav-title">
                        <span>Личные чаты</span>
                    </div>
                    <ul class="nav-list" id="personal-chats-list">
                        <!-- Динамически загружаются -->
                    </ul>
                </div>
                
                <!-- Пользователи -->
                <div class="nav-section">
                    <div class="nav-title">
                        <span>Пользователи</span>
                    </div>
                    <ul class="nav-list" id="users-list">
                        <!-- Динамически загружаются -->
                    </ul>
                </div>
            </div>
        </div>
        
        <!-- Основной контейнер -->
        <div class="chat-container">
            <!-- Хедер чата -->
            <div class="chat-header">
                <div class="chat-header-left">
                    <button class="open-sidebar">
                        <i class="fas fa-bars"></i>
                    </button>
                    <div class="chat-info">
                        <div class="chat-title" id="chat-title">Избранное</div>
                        <div class="chat-subtitle" id="chat-subtitle">Ваши сохраненные заметки</div>
                    </div>
                </div>
                <div class="chat-header-right" id="chat-header-right">
                    <!-- Кнопки действий динамически добавляются -->
                </div>
            </div>
            
            <!-- Контент (сообщения/избранное/настройки) -->
            <div id="content-container" class="scrollbar-thin">
                <!-- Избранное -->
                <div class="favorites-container" id="favorites-view">
                    <div class="favorites-header">
                        <h2 class="favorites-title">Избранное</h2>
                        <p class="favorites-subtitle">Все ваши сохраненные заметки и файлы</p>
                    </div>
                    <div class="favorites-grid" id="favorites-grid">
                        <!-- Избранное загружается динамически -->
                    </div>
                </div>
                
                <!-- Чат -->
                <div class="messages-container hidden" id="chat-view">
                    <!-- Сообщения загружаются динамически -->
                </div>
                
                <!-- Настройки канала -->
                <div class="settings-container hidden" id="settings-view">
                    <div class="settings-section">
                        <h3 class="settings-title" id="settings-channel-name">Канал</h3>
                        <div class="form-group">
                            <label class="form-label">Название канала</label>
                            <input type="text" class="form-control" id="settings-display-name" readonly>
                        </div>
                        <div class="form-group">
                            <label class="form-label">Описание</label>
                            <input type="text" class="form-control" id="settings-description" readonly>
                        </div>
                        <div class="form-group">
                            <label class="form-label">Создатель</label>
                            <input type="text" class="form-control" id="settings-created-by" readonly>
                        </div>
                    </div>
                    
                    <div class="settings-section">
                        <div class="settings-title">Участники</div>
                        <div class="members-list" id="settings-members-list">
                            <!-- Участники загружаются динамически -->
                        </div>
                    </div>
                    
                    <div class="settings-section" id="channel-actions-section">
                        <button class="btn btn-primary" id="rename-channel-btn">
                            <i class="fas fa-edit"></i> Переименовать канал
                        </button>
                    </div>
                </div>
                
                <!-- Настройки пользователя -->
                <div class="settings-container hidden" id="user-settings-view">
                    <div class="settings-section">
                        <h3 class="settings-title">Настройки профиля</h3>
                        <div class="form-group">
                            <label class="form-label">Аватар</label>
                            <div style="text-align: center;">
                                <div class="user-avatar" id="settings-avatar" style="width: 80px; height: 80px; margin: 0 auto 16px;"></div>
                                <div style="display: flex; gap: 8px; justify-content: center;">
                                    <button class="btn btn-secondary" id="change-avatar-btn">
                                        <i class="fas fa-camera"></i> Изменить
                                    </button>
                                    <button class="btn btn-danger" id="remove-avatar-btn">
                                        <i class="fas fa-trash"></i> Удалить
                                    </button>
                                </div>
                            </div>
                        </div>
                        <div class="form-group">
                            <label class="form-label">Тема</label>
                            <div style="display: flex; gap: 8px;">
                                <button class="btn btn-secondary" data-theme="light">
                                    <i class="fas fa-sun"></i> Светлая
                                </button>
                                <button class="btn btn-secondary" data-theme="dark">
                                    <i class="fas fa-moon"></i> Темная
                                </button>
                                <button class="btn btn-secondary" data-theme="auto">
                                    <i class="fas fa-adjust"></i> Авто
                                </button>
                            </div>
                        </div>
                    </div>
                    
                    <div class="settings-section">
                        <button class="btn btn-danger" id="logout-btn" style="width: 100%;">
                            <i class="fas fa-sign-out-alt"></i> Выйти
                        </button>
                    </div>
                </div>
            </div>
            
            <!-- Область ввода сообщений -->
            <div class="input-container hidden" id="input-container">
                <div class="input-wrapper">
                    <div class="input-actions">
                        <button class="input-action" id="attach-file-btn">
                            <i class="fas fa-paperclip"></i>
                        </button>
                        <input type="file" id="file-input" accept="image/*,video/*,.pdf,.doc,.docx,.txt" multiple style="display: none;">
                    </div>
                    <textarea class="message-input scrollbar-thin" id="message-input" placeholder="Написать сообщение..." rows="1"></textarea>
                    <div class="input-actions">
                        <button class="input-action primary" id="send-btn">
                            <i class="fas fa-paper-plane"></i>
                        </button>
                    </div>
                </div>
                <div class="file-previews" id="file-previews"></div>
            </div>
        </div>
    </div>

    <!-- Модальные окна -->
    <!-- Создание канала -->
    <div class="modal-overlay" id="create-channel-modal">
        <div class="modal">
            <div class="modal-header">
                <h3 class="modal-title">Создать канал</h3>
                <button class="modal-close">
                    <i class="fas fa-times"></i>
                </button>
            </div>
            <div class="modal-body">
                <div class="form-group">
                    <label class="form-label">Идентификатор</label>
                    <input type="text" class="form-control" id="channel-name" placeholder="example_channel" maxlength="50">
                    <small style="color: var(--text-secondary); font-size: 0.875rem;">Только латинские буквы, цифры и нижние подчеркивания</small>
                </div>
                <div class="form-group">
                    <label class="form-label">Отображаемое название</label>
                    <input type="text" class="form-control" id="channel-display-name" placeholder="Мой канал">
                </div>
                <div class="form-group">
                    <label class="form-label">Описание</label>
                    <textarea class="form-control form-textarea" id="channel-description" placeholder="Описание канала"></textarea>
                </div>
                <div class="form-group">
                    <div class="checkbox-group" onclick="toggleCheckbox('channel-private')">
                        <div class="checkbox" id="channel-private-checkbox"></div>
                        <span class="checkbox-label">Приватный канал</span>
                    </div>
                </div>
            </div>
            <div class="modal-footer">
                <button class="btn btn-secondary" id="cancel-create-channel">Отмена</button>
                <button class="btn btn-primary" id="submit-create-channel">Создать</button>
            </div>
        </div>
    </div>

    <!-- Добавление в избранное -->
    <div class="modal-overlay" id="add-favorite-modal">
        <div class="modal">
            <div class="modal-header">
                <h3 class="modal-title">Добавить в избранное</h3>
                <button class="modal-close">
                    <i class="fas fa-times"></i>
                </button>
            </div>
            <div class="modal-body">
                <div class="form-group">
                    <label class="form-label">Текст заметки</label>
                    <textarea class="form-control form-textarea" id="favorite-text" placeholder="Введите текст заметки..."></textarea>
                </div>
                <div class="form-group">
                    <label class="form-label">Категория</label>
                    <input type="text" class="form-control" id="favorite-category" placeholder="general">
                </div>
                <div class="form-group">
                    <label class="form-label">Файл (опционально)</label>
                    <input type="file" class="form-control" id="favorite-file">
                </div>
            </div>
            <div class="modal-footer">
                <button class="btn btn-secondary" id="cancel-add-favorite">Отмена</button>
                <button class="btn btn-primary" id="submit-add-favorite">Сохранить</button>
            </div>
        </div>
    </div>

    <!-- Переименование канала -->
    <div class="modal-overlay" id="rename-channel-modal">
        <div class="modal">
            <div class="modal-header">
                <h3 class="modal-title">Переименовать канал</h3>
                <button class="modal-close">
                    <i class="fas fa-times"></i>
                </button>
            </div>
            <div class="modal-body">
                <div class="form-group">
                    <label class="form-label">Новое название</label>
                    <input type="text" class="form-control" id="new-channel-name">
                </div>
            </div>
            <div class="modal-footer">
                <button class="btn btn-secondary" id="cancel-rename-channel">Отмена</button>
                <button class="btn btn-primary" id="submit-rename-channel">Сохранить</button>
            </div>
        </div>
    </div>

    <!-- Добавление пользователя в канал -->
    <div class="modal-overlay" id="add-user-modal">
        <div class="modal">
            <div class="modal-header">
                <h3 class="modal-title">Добавить пользователя</h3>
                <button class="modal-close">
                    <i class="fas fa-times"></i>
                </button>
            </div>
            <div class="modal-body">
                <div class="form-group">
                    <label class="form-label">Пользователь</label>
                    <select class="form-control" id="user-select">
                        <option value="">Выберите пользователя...</option>
                    </select>
                </div>
            </div>
            <div class="modal-footer">
                <button class="btn btn-secondary" id="cancel-add-user">Отмена</button>
                <button class="btn btn-primary" id="submit-add-user">Добавить</button>
            </div>
        </div>
    </div>

    <!-- Изменение аватара -->
    <div class="modal-overlay" id="avatar-modal">
        <div class="modal">
            <div class="modal-header">
                <h3 class="modal-title">Изменение аватара</h3>
                <button class="modal-close">
                    <i class="fas fa-times"></i>
                </button>
            </div>
            <div class="modal-body">
                <div style="text-align: center;">
                    <div class="user-avatar" id="modal-avatar" style="width: 120px; height: 120px; margin: 0 auto 20px;"></div>
                    <input type="file" id="avatar-file" accept="image/*" style="display: none;">
                    <div style="display: flex; gap: 8px; justify-content: center;">
                        <button class="btn btn-primary" id="upload-avatar-btn">
                            <i class="fas fa-upload"></i> Загрузить
                        </button>
                        <button class="btn btn-danger" id="delete-avatar-btn">
                            <i class="fas fa-trash"></i> Удалить
                        </button>
                    </div>
                </div>
            </div>
        </div>
    </div>

    <script src="https://cdnjs.cloudflare.com/ajax/libs/socket.io/4.7.2/socket.io.js"></script>
    <script>
        // Инициализация
        const socket = io();
        const currentUser = "{username}";
        let currentRoom = null;
        let currentRoomType = null;
        let currentRoomName = null;
        let currentView = 'favorites';
        let currentChannelSettings = null;
        let isMobile = window.innerWidth < 768;
        let favorites = [];
        let channels = [];
        let users = [];
        let personalChats = [];

        // DOM элементы
        const elements = {{
            sidebar: document.querySelector('.sidebar'),
            openSidebarBtn: document.querySelector('.open-sidebar'),
            closeSidebarBtn: document.querySelector('.close-sidebar'),
            settingsBtn: document.getElementById('settings-btn'),
            addFavoriteBtn: document.getElementById('add-favorite-btn'),
            addChannelBtn: document.getElementById('add-channel-btn'),
            userAvatar: document.getElementById('user-avatar'),
            userName: document.getElementById('user-name'),
            chatTitle: document.getElementById('chat-title'),
            chatSubtitle: document.getElementById('chat-subtitle'),
            chatHeaderRight: document.getElementById('chat-header-right'),
            contentContainer: document.getElementById('content-container'),
            favoritesView: document.getElementById('favorites-view'),
            chatView: document.getElementById('chat-view'),
            settingsView: document.getElementById('settings-view'),
            userSettingsView: document.getElementById('user-settings-view'),
            inputContainer: document.getElementById('input-container'),
            messageInput: document.getElementById('message-input'),
            sendBtn: document.getElementById('send-btn'),
            attachFileBtn: document.getElementById('attach-file-btn'),
            fileInput: document.getElementById('file-input'),
            filePreviews: document.getElementById('file-previews'),
            favoritesGrid: document.getElementById('favorites-grid'),
            channelsList: document.getElementById('channels-list'),
            personalChatsList: document.getElementById('personal-chats-list'),
            usersList: document.getElementById('users-list'),
            favoritesCount: document.getElementById('favorites-count'),
            navFavorites: document.getElementById('nav-favorites')
        }};

        // Модальные окна
        const modals = {{
            createChannel: document.getElementById('create-channel-modal'),
            addFavorite: document.getElementById('add-favorite-modal'),
            renameChannel: document.getElementById('rename-channel-modal'),
            addUser: document.getElementById('add-user-modal'),
            avatar: document.getElementById('avatar-modal')
        }};

        // Функции для работы с модальными окнами
        function openModal(modal) {{
            modal.style.display = 'flex';
            document.body.style.overflow = 'hidden';
        }}

        function closeModal(modal) {{
            modal.style.display = 'none';
            document.body.style.overflow = 'auto';
        }}

        // Функции для работы с видами
        function showView(view, data = null) {{
            currentView = view;
            
            // Скрыть все виды
            elements.favoritesView.classList.add('hidden');
            elements.chatView.classList.add('hidden');
            elements.settingsView.classList.add('hidden');
            elements.userSettingsView.classList.add('hidden');
            elements.inputContainer.classList.add('hidden');
            
            // Показать выбранный вид
            switch(view) {{
                case 'favorites':
                    elements.favoritesView.classList.remove('hidden');
                    elements.chatTitle.textContent = 'Избранное';
                    elements.chatSubtitle.textContent = 'Ваши сохраненные заметки';
                    elements.chatHeaderRight.innerHTML = '';
                    loadFavorites();
                    break;
                    
                case 'chat':
                    elements.chatView.classList.remove('hidden');
                    elements.inputContainer.classList.remove('hidden');
                    elements.chatTitle.textContent = data?.name || 'Чат';
                    elements.chatSubtitle.textContent = data?.subtitle || '';
                    
                    // Настройка кнопок действий
                    let actionsHtml = '';
                    if (currentRoomType === 'channel') {{
                        actionsHtml = `
                            <button class="header-action" id="channel-settings-btn">
                                <i class="fas fa-cog"></i>
                            </button>
                            <button class="header-action" id="add-user-btn">
                                <i class="fas fa-user-plus"></i>
                            </button>
                        `;
                    }}
                    elements.chatHeaderRight.innerHTML = actionsHtml;
                    
                    // Загрузка сообщений
                    loadMessages();
                    break;
                    
                case 'channel-settings':
                    elements.settingsView.classList.remove('hidden');
                    elements.chatTitle.textContent = 'Настройки канала';
                    elements.chatSubtitle.textContent = currentRoomName || '';
                    elements.chatHeaderRight.innerHTML = '';
                    loadChannelSettings();
                    break;
                    
                case 'user-settings':
                    elements.userSettingsView.classList.remove('hidden');
                    elements.chatTitle.textContent = 'Настройки';
                    elements.chatSubtitle.textContent = 'Профиль и внешний вид';
                    elements.chatHeaderRight.innerHTML = '';
                    break;
            }}
            
            // Закрыть сайдбар на мобильных
            if (isMobile) {{
                elements.sidebar.classList.remove('active');
            }}
        }}

        // Функция для переключения сайдбара
        function toggleSidebar() {{
            elements.sidebar.classList.toggle('active');
        }}

        // Загрузка данных пользователя
        function loadUserData() {{
            fetch(`/user_info/${{currentUser}}`)
                .then(r => r.json())
                .then(data => {{
                    if (data.success) {{
                        const avatar = elements.userAvatar;
                        if (data.avatar_path) {{
                            avatar.style.backgroundImage = `url(${{data.avatar_path}})`;
                            avatar.textContent = '';
                        }} else {{
                            avatar.style.backgroundImage = 'none';
                            avatar.style.backgroundColor = data.avatar_color;
                            avatar.textContent = currentUser.slice(0, 2).toUpperCase();
                        }}
                        
                        // Установка темы
                        document.documentElement.setAttribute('data-theme', data.theme || 'light');
                    }}
                }});
        }}

        // Загрузка избранного
        function loadFavorites() {{
            fetch('/get_favorites')
                .then(r => r.json())
                .then(data => {{
                    if (data.success) {{
                        favorites = data.favorites;
                        renderFavorites();
                        elements.favoritesCount.textContent = `${{favorites.length}} заметок`;
                    }}
                }});
        }}

        // Рендеринг избранного
        function renderFavorites() {{
            const grid = elements.favoritesGrid;
            
            if (favorites.length === 0) {{
                grid.innerHTML = `
                    <div class="empty-state">
                        <div class="empty-state-icon">
                            <i class="fas fa-star"></i>
                        </div>
                        <div class="empty-state-title">Нет избранного</div>
                        <div class="empty-state-description">Добавьте заметки, фото или видео</div>
                        <button class="btn btn-primary" onclick="openModal(modals.addFavorite)" style="margin-top: 16px;">
                            <i class="fas fa-plus"></i> Добавить заметку
                        </button>
                    </div>
                `;
                return;
            }}
            
            let html = '';
            favorites.forEach(favorite => {{
                html += `
                    <div class="favorite-item ${{favorite.is_pinned ? 'pinned' : ''}}" data-id="${{favorite.id}}">
                        <div class="favorite-actions">
                            <button class="favorite-action" onclick="togglePinFavorite(${{favorite.id}})" title="${{favorite.is_pinned ? 'Открепить' : 'Закрепить'}}">
                                <i class="fas fa-thumbtack"></i>
                            </button>
                            <button class="favorite-action delete" onclick="deleteFavorite(${{favorite.id}})" title="Удалить">
                                <i class="fas fa-trash"></i>
                            </button>
                        </div>
                        ${{favorite.content ? `<div class="favorite-content">${{favorite.content}}</div>` : ''}}
                        ${{favorite.file_path ? `
                            <div class="favorite-file">
                                ${{favorite.file_type === 'image' || favorite.file_name?.match(/\\.(jpg|jpeg|png|gif|webp)$/i) 
                                    ? `<img src="${{favorite.file_path}}" alt="${{favorite.file_name}}" onclick="openFile('${{favorite.file_path}}')">`
                                    : favorite.file_type === 'video' || favorite.file_name?.match(/\\.(mp4|webm|mov)$/i)
                                    ? `<video src="${{favorite.file_path}}" controls></video>`
                                    : `<div style="padding: 16px; background: var(--bg-tertiary); border-radius: var(--radius);">
                                        <i class="fas fa-file" style="font-size: 2rem; margin-bottom: 8px; display: block;"></i>
                                        <div style="font-weight: 500; margin-bottom: 4px;">${{favorite.file_name}}</div>
                                        <a href="${{favorite.file_path}}" target="_blank" class="btn btn-secondary" style="padding: 4px 12px;">
                                            <i class="fas fa-download"></i> Скачать
                                        </a>
                                    </div>`
                                }}
                            </div>
                        ` : ''}}
                        <div class="favorite-meta">
                            <span>${{new Date(favorite.created_at).toLocaleDateString('ru-RU')}}</span>
                            ${{favorite.category && favorite.category !== 'general' 
                                ? `<span class="favorite-category">${{favorite.category}}</span>` 
                                : ''}}
                        </div>
                    </div>
                `;
            }});
            
            grid.innerHTML = html;
        }}

        // Загрузка каналов
        function loadChannels() {{
            fetch('/user_channels')
                .then(r => r.json())
                .then(data => {{
                    if (data.success) {{
                        channels = data.channels;
                        renderChannels();
                    }}
                }});
        }}

        // Рендеринг каналов
        function renderChannels() {{
            const list = elements.channelsList;
            
            // Оставляем General
            let html = '';
            
            // Добавляем остальные каналы
            channels.forEach(channel => {{
                if (channel.name !== 'general') {{
                    html += `
                        <li class="nav-item" data-room="channel_${{channel.name}}" data-type="channel" data-name="${{channel.display_name}}">
                            <div class="nav-item-icon">
                                <i class="fas fa-hashtag"></i>
                            </div>
                            <div class="nav-item-content">
                                <div class="nav-item-title">${{channel.display_name}}</div>
                                <div class="nav-item-subtitle">${{channel.description || 'Нет описания'}}</div>
                            </div>
                        </li>
                    `;
                }}
            }});
            
            // Добавляем к существующему General
            list.innerHTML += html;
            
            // Назначаем обработчики
            document.querySelectorAll('[data-room^="channel_"]').forEach(item => {{
                item.addEventListener('click', () => {{
                    const room = item.dataset.room;
                    const type = item.dataset.type;
                    const name = item.dataset.name;
                    joinRoom(room, type, name);
                    
                    // Обновляем активный элемент
                    document.querySelectorAll('.nav-item').forEach(i => i.classList.remove('active'));
                    item.classList.add('active');
                }});
            }});
        }}

        // Загрузка пользователей
        function loadUsers() {{
            fetch('/users')
                .then(r => r.json())
                .then(data => {{
                    if (data && Array.isArray(data)) {{
                        users = data.filter(u => u.username !== currentUser);
                        renderUsers();
                    }}
                }});
        }}

        // Рендеринг пользователей
        function renderUsers() {{
            const list = elements.usersList;
            
            if (users.length === 0) {{
                list.innerHTML = '<div style="padding: 16px; color: var(--text-secondary); text-align: center;">Нет других пользователей</div>';
                return;
            }}
            
            let html = '';
            users.forEach(user => {{
                html += `
                    <li class="nav-item" data-room="private_${{[currentUser, user.username].sort().join('_')}}" data-type="private" data-name="${{user.username}}">
                        <div class="nav-item-icon">
                            <div class="user-avatar" style="width: 40px; height: 40px;">
                                ${{user.avatar 
                                    ? `<div style="width: 100%; height: 100%; border-radius: inherit; background-image: url(${{user.avatar}}); background-size: cover; background-position: center;"></div>`
                                    : `<div style="width: 100%; height: 100%; display: flex; align-items: center; justify-content: center; background-color: ${{user.color}}; color: white; font-weight: 600; border-radius: inherit;">${{user.username.slice(0, 2).toUpperCase()}}</div>`
                                }}
                            </div>
                        </div>
                        <div class="nav-item-content">
                            <div class="nav-item-title">${{user.username}}</div>
                            <div class="nav-item-subtitle" style="display: flex; align-items: center; gap: 4px;">
                                <div class="online-dot" style="background: ${{user.online ? 'var(--success)' : 'var(--gray-400)'}};"></div>
                                <span>${{user.online ? 'Online' : 'Offline'}}</span>
                            </div>
                        </div>
                    </li>
                `;
            }});
            
            list.innerHTML = html;
            
            // Назначаем обработчики
            document.querySelectorAll('[data-room^="private_"]').forEach(item => {{
                item.addEventListener('click', () => {{
                    const room = item.dataset.room;
                    const type = item.dataset.type;
                    const name = item.dataset.name;
                    joinRoom(room, type, name);
                    
                    // Обновляем активный элемент
                    document.querySelectorAll('.nav-item').forEach(i => i.classList.remove('active'));
                    item.classList.add('active');
                }});
            }});
        }}

        // Загрузка личных чатов
        function loadPersonalChats() {{
            fetch('/personal_chats')
                .then(r => r.json())
                .then(data => {{
                    if (data.success) {{
                        personalChats = data.chats;
                        renderPersonalChats();
                    }}
                }});
        }}

        // Рендеринг личных чатов
        function renderPersonalChats() {{
            const list = elements.personalChatsList;
            
            if (personalChats.length === 0) {{
                list.innerHTML = '<div style="padding: 16px; color: var(--text-secondary); text-align: center;">Нет личных чатов</div>';
                return;
            }}
            
            let html = '';
            personalChats.forEach(chatUser => {{
                html += `
                    <li class="nav-item" data-room="private_${{[currentUser, chatUser].sort().join('_')}}" data-type="private" data-name="${{chatUser}}">
                        <div class="nav-item-icon">
                            <i class="fas fa-user"></i>
                        </div>
                        <div class="nav-item-content">
                            <div class="nav-item-title">${{chatUser}}</div>
                            <div class="nav-item-subtitle">Личный чат</div>
                        </div>
                    </li>
                `;
            }});
            
            list.innerHTML = html;
            
            // Назначаем обработчики (такие же как у пользователей)
            document.querySelectorAll('[data-room^="private_"]').forEach(item => {{
                item.addEventListener('click', () => {{
                    const room = item.dataset.room;
                    const type = item.dataset.type;
                    const name = item.dataset.name;
                    joinRoom(room, type, name);
                    
                    // Обновляем активный элемент
                    document.querySelectorAll('.nav-item').forEach(i => i.classList.remove('active'));
                    item.classList.add('active');
                }});
            }});
        }}

        // Присоединение к комнате
        function joinRoom(room, type, name) {{
            if (currentRoom) {{
                socket.emit('leave', {{ room: currentRoom }});
            }}
            
            currentRoom = room;
            currentRoomType = type;
            currentRoomName = name;
            
            socket.emit('join', {{ room: room }});
            showView('chat', {{ 
                name: type === 'channel' ? `# ${{name}}` : name,
                subtitle: type === 'channel' ? 'Канал' : 'Личный чат'
            }});
        }}

        // Загрузка сообщений
        function loadMessages() {{
            if (!currentRoom) return;
            
            fetch(`/get_messages/${{currentRoom}}`)
                .then(r => r.json())
                .then(messages => {{
                    const container = elements.chatView;
                    
                    if (!messages || messages.length === 0) {{
                        container.innerHTML = `
                            <div class="empty-state">
                                <div class="empty-state-icon">
                                    <i class="fas fa-comments"></i>
                                </div>
                                <div class="empty-state-title">Начните общение</div>
                                <div class="empty-state-description">Отправьте первое сообщение</div>
                            </div>
                        `;
                        return;
                    }}
                    
                    let html = '';
                    let lastDate = null;
                    
                    messages.forEach(msg => {{
                        const messageDate = new Date(msg.timestamp).toDateString();
                        if (messageDate !== lastDate) {{
                            lastDate = messageDate;
                            html += `
                                <div class="date-divider">
                                    <span>${{new Date(msg.timestamp).toLocaleDateString('ru-RU', {{ 
                                        weekday: 'long', 
                                        year: 'numeric', 
                                        month: 'long', 
                                        day: 'numeric' 
                                    }})}}</span>
                                </div>
                            `;
                        }}
                        
                        const isOutgoing = msg.user === currentUser;
                        html += `
                            <div class="message ${{isOutgoing ? 'outgoing' : 'incoming'}}">
                                ${{!isOutgoing ? `
                                    <div class="message-avatar">
                                        ${{msg.avatar_path 
                                            ? `<div style="width: 100%; height: 100%; border-radius: inherit; background-image: url(${{msg.avatar_path}}); background-size: cover; background-position: center;"></div>`
                                            : `<div style="width: 100%; height: 100%; display: flex; align-items: center; justify-content: center; background-color: ${{msg.color}}; color: white; font-weight: 600; border-radius: inherit;">${{msg.user.slice(0, 2).toUpperCase()}}</div>`
                                        }}
                                    </div>
                                ` : ''}}
                                <div class="message-content">
                                    ${{!isOutgoing ? `
                                        <div class="message-header">
                                            <div class="message-sender">${{msg.user}}</div>
                                            <div class="message-time">${{msg.timestamp}}</div>
                                        </div>
                                    ` : ''}}
                                    ${{msg.message ? `<div class="message-text">${{msg.message.replace(/\\n/g, '<br>')}}</div>` : ''}}
                                    ${{msg.file ? `
                                        <div class="message-files">
                                            <div class="message-file">
                                                ${{msg.file.endsWith('.mp4') || msg.file.endsWith('.webm') || msg.file.endsWith('.mov')
                                                    ? `<video src="${{msg.file}}" controls></video>`
                                                    : `<img src="${{msg.file}}" alt="${{msg.file_name || 'Файл'}}" onclick="openFile('${{msg.file}}')">
                                                        <div class="message-file-overlay">
                                                            <div>
                                                                <div class="message-file-name">${{msg.file_name || 'Файл'}}</div>
                                                                ${{msg.file_size ? `<div class="message-file-size">${{msg.file_size}}</div>` : ''}}
                                                            </div>
                                                            <a href="${{msg.file}}" download class="file-download">
                                                                <i class="fas fa-download"></i>
                                                            </a>
                                                        </div>
                                                    `
                                                }}
                                            </div>
                                        </div>
                                    ` : ''}}
                                    ${{isOutgoing ? `<div class="message-time" style="text-align: right; margin-top: 4px;">${{msg.timestamp}}</div>` : ''}}
                                </div>
                            </div>
                        `;
                    }});
                    
                    container.innerHTML = html;
                    container.scrollTop = container.scrollHeight;
                }});
        }}

        // Отправка сообщения
        function sendMessage() {{
            const text = elements.messageInput.value.trim();
            const files = elements.fileInput.files;
            
            if (!text && files.length === 0) return;
            
            const messageData = {{
                message: text,
                room: currentRoom,
                type: currentRoomType
            }};
            
            // Если есть файлы
            if (files.length > 0) {{
                const reader = new FileReader();
                reader.onload = (e) => {{
                    messageData.file = e.target.result;
                    messageData.fileName = files[0].name;
                    messageData.fileType = files[0].type.startsWith('image/') ? 'image' : 'video';
                    socket.emit('message', messageData);
                    clearMessageInput();
                }};
                reader.readAsDataURL(files[0]);
            }} else {{
                socket.emit('message', messageData);
                clearMessageInput();
            }}
        }}

        // Очистка поля ввода
        function clearMessageInput() {{
            elements.messageInput.value = '';
            elements.fileInput.value = '';
            elements.filePreviews.innerHTML = '';
            elements.messageInput.style.height = 'auto';
        }}

        // Загрузка настроек канала
        function loadChannelSettings() {{
            if (!currentRoom || !currentRoomType === 'channel') return;
            
            const channelName = currentRoom.replace('channel_', '');
            fetch(`/channel_info/${{channelName}}`)
                .then(r => r.json())
                .then(data => {{
                    if (data.success) {{
                        currentChannelSettings = data.data;
                        renderChannelSettings();
                    }}
                }});
        }}

        // Рендеринг настроек канала
        function renderChannelSettings() {{
            if (!currentChannelSettings) return;
            
            // Заголовок
            document.getElementById('settings-channel-name').textContent = `Канал: ${{currentChannelSettings.display_name}}`;
            document.getElementById('settings-display-name').value = currentChannelSettings.display_name;
            document.getElementById('settings-description').value = currentChannelSettings.description || '';
            document.getElementById('settings-created-by').value = currentChannelSettings.created_by;
            
            // Участники
            const membersList = document.getElementById('settings-members-list');
            let membersHtml = '';
            
            currentChannelSettings.members.forEach(member => {{
                membersHtml += `
                    <div class="member-item">
                        <div class="member-avatar">
                            ${{member.avatar 
                                ? `<div style="width: 100%; height: 100%; border-radius: inherit; background-image: url(${{member.avatar}}); background-size: cover; background-position: center;"></div>`
                                : `<div style="width: 100%; height: 100%; display: flex; align-items: center; justify-content: center; background-color: ${{member.color}}; color: white; font-weight: 600; border-radius: inherit;">${{member.username.slice(0, 2).toUpperCase()}}</div>`
                            }}
                        </div>
                        <div class="member-info">
                            <div class="member-name">${{member.username}}</div>
                            <div class="member-role ${{member.is_admin ? 'admin' : ''}}">
                                ${{member.is_admin ? 'Администратор' : 'Участник'}}
                            </div>
                        </div>
                        ${{member.username !== currentUser && currentChannelSettings.created_by === currentUser ? `
                            <div class="member-actions">
                                <button class="member-action remove" onclick="removeUserFromChannel('${{member.username}}')">
                                    <i class="fas fa-user-minus"></i>
                                </button>
                            </div>
                        ` : ''}}
                    </div>
                `;
            }});
            
            membersList.innerHTML = membersHtml;
            
            // Показываем действия только если пользователь создатель
            const actionsSection = document.getElementById('channel-actions-section');
            if (currentChannelSettings.created_by === currentUser) {{
                actionsSection.innerHTML = `
                    <button class="btn btn-primary" id="rename-channel-btn">
                        <i class="fas fa-edit"></i> Переименовать канал
                    </button>
                `;
                
                document.getElementById('rename-channel-btn').addEventListener('click', () => {{
                    openModal(modals.renameChannel);
                    document.getElementById('new-channel-name').value = currentChannelSettings.display_name;
                }});
            }} else {{
                actionsSection.innerHTML = '';
            }}
        }}

        // Функции для работы с избранным
        function deleteFavorite(id) {{
            if (!confirm('Удалить эту заметку?')) return;
            
            fetch(`/delete_favorite/${{id}}`, {{ method: 'DELETE' }})
                .then(r => r.json())
                .then(data => {{
                    if (data.success) {{
                        loadFavorites();
                    }} else {{
                        alert('Ошибка при удалении');
                    }}
                }});
        }}

        function togglePinFavorite(id) {{
            fetch(`/toggle_pin_favorite/${{id}}`, {{ method: 'POST' }})
                .then(r => r.json())
                .then(data => {{
                    if (data.success) {{
                        loadFavorites();
                    }}
                }});
        }}

        // Функции для работы с каналами
        function removeUserFromChannel(username) {{
            if (!confirm(`Удалить пользователя ${{username}} из канала?`)) return;
            
            fetch('/remove_user_from_channel', {{
                method: 'POST',
                headers: {{ 'Content-Type': 'application/json' }},
                body: JSON.stringify({{
                    channel_name: currentRoom.replace('channel_', ''),
                    username: username
                }})
            }})
            .then(r => r.json())
            .then(data => {{
                if (data.success) {{
                    loadChannelSettings();
                    alert('Пользователь удален');
                }} else {{
                    alert(data.message || 'Ошибка');
                }}
            }});
        }}

        // Утилиты
        function openFile(url) {{
            window.open(url, '_blank');
        }}

        function toggleCheckbox(id) {{
            const checkbox = document.getElementById(`${id}-checkbox`);
            checkbox.classList.toggle('checked');
            document.getElementById(id).value = checkbox.classList.contains('checked');
        }}

        // Инициализация
        document.addEventListener('DOMContentLoaded', () => {{
            // Загрузка данных
            loadUserData();
            loadFavorites();
            loadChannels();
            loadUsers();
            loadPersonalChats();
            
            // Настройка обработчиков
            elements.openSidebarBtn.addEventListener('click', toggleSidebar);
            elements.closeSidebarBtn.addEventListener('click', toggleSidebar);
            elements.settingsBtn.addEventListener('click', () => showView('user-settings'));
            elements.addFavoriteBtn.addEventListener('click', () => openModal(modals.addFavorite));
            elements.addChannelBtn.addEventListener('click', () => openModal(modals.createChannel));
            elements.navFavorites.addEventListener('click', () => showView('favorites'));
            
            // Отправка сообщений
            elements.sendBtn.addEventListener('click', sendMessage);
            elements.messageInput.addEventListener('keydown', (e) => {{
                if (e.key === 'Enter' && !e.shiftKey) {{
                    e.preventDefault();
                    sendMessage();
                }}
            }});
            
            // Авторазмер textarea
            elements.messageInput.addEventListener('input', () => {{
                elements.messageInput.style.height = 'auto';
                elements.messageInput.style.height = Math.min(elements.messageInput.scrollHeight, 120) + 'px';
            }});
            
            // Прикрепление файлов
            elements.attachFileBtn.addEventListener('click', () => elements.fileInput.click());
            elements.fileInput.addEventListener('change', () => {{
                elements.filePreviews.innerHTML = '';
                Array.from(elements.fileInput.files).forEach((file, index) => {{
                    const reader = new FileReader();
                    reader.onload = (e) => {{
                        const isImage = file.type.startsWith('image/');
                        const isVideo = file.type.startsWith('video/');
                        
                        const preview = document.createElement('div');
                        preview.className = 'file-preview';
                        preview.innerHTML = `
                            ${{isImage ? `<img src="${{e.target.result}}">` : ''}}
                            ${{isVideo ? `<video src="${{e.target.result}}"></video>` : ''}}
                            ${{!isImage && !isVideo ? `
                                <div style="width: 80px; height: 80px; background: var(--bg-tertiary); border-radius: var(--radius); display: flex; align-items: center; justify-content: center;">
                                    <i class="fas fa-file" style="font-size: 2rem;"></i>
                                </div>
                            ` : ''}}
                            <button class="remove-file" data-index="${{index}}">
                                <i class="fas fa-times"></i>
                            </button>
                        `;
                        
                        elements.filePreviews.appendChild(preview);
                    }};
                    
                    if (file.type.startsWith('image/') || file.type.startsWith('video/')) {{
                        reader.readAsDataURL(file);
                    }} else {{
                        reader.readAsText(file);
                    }}
                }});
            }});
            
            // Удаление превью файлов
            elements.filePreviews.addEventListener('click', (e) => {{
                if (e.target.closest('.remove-file')) {{
                    const index = e.target.closest('.remove-file').dataset.index;
                    const files = Array.from(elements.fileInput.files);
                    files.splice(index, 1);
                    
                    const newFileList = new DataTransfer();
                    files.forEach(file => newFileList.items.add(file));
                    elements.fileInput.files = newFileList.files;
                    
                    // Перерисовка превью
                    elements.fileInput.dispatchEvent(new Event('change'));
                }}
            }});
            
            // Закрытие модальных окон
            document.querySelectorAll('.modal-close').forEach(btn => {{
                btn.addEventListener('click', () => {{
                    const modal = btn.closest('.modal-overlay');
                    closeModal(modal);
                }});
            }});
            
            // Клик по оверлею для закрытия
            document.querySelectorAll('.modal-overlay').forEach(modal => {{
                modal.addEventListener('click', (e) => {{
                    if (e.target === modal) {{
                        closeModal(modal);
                    }}
                }});
            }});
            
            // Создание канала
            document.getElementById('cancel-create-channel').addEventListener('click', () => closeModal(modals.createChannel));
            document.getElementById('submit-create-channel').addEventListener('click', () => {{
                const name = document.getElementById('channel-name').value.trim();
                const displayName = document.getElementById('channel-display-name').value.trim();
                const description = document.getElementById('channel-description').value.trim();
                const isPrivate = document.getElementById('channel-private-checkbox').classList.contains('checked');
                
                if (!name || !displayName) {{
                    alert('Заполните обязательные поля');
                    return;
                }}
                
                if (!/^[a-zA-Z0-9_]+$/.test(name)) {{
                    alert('Идентификатор может содержать только латинские буквы, цифры и нижние подчеркивания');
                    return;
                }}
                
                fetch('/create_channel', {{
                    method: 'POST',
                    headers: {{ 'Content-Type': 'application/json' }},
                    body: JSON.stringify({{
                        name: name,
                        display_name: displayName,
                        description: description,
                        is_private: isPrivate
                    }})
                }})
                .then(r => r.json())
                .then(data => {{
                    if (data.success) {{
                        closeModal(modals.createChannel);
                        loadChannels();
                        alert('Канал создан!');
                    }} else {{
                        alert(data.error || 'Ошибка');
                    }}
                }});
            }});
            
            // Добавление в избранное
            document.getElementById('cancel-add-favorite').addEventListener('click', () => closeModal(modals.addFavorite));
            document.getElementById('submit-add-favorite').addEventListener('click', () => {{
                const text = document.getElementById('favorite-text').value.trim();
                const category = document.getElementById('favorite-category').value.trim() || 'general';
                const fileInput = document.getElementById('favorite-file');
                const file = fileInput.files[0];
                
                if (!text && !file) {{
                    alert('Добавьте текст или файл');
                    return;
                }}
                
                const formData = new FormData();
                formData.append('content', text);
                formData.append('category', category);
                if (file) formData.append('file', file);
                
                fetch('/add_to_favorites', {{
                    method: 'POST',
                    body: formData
                }})
                .then(r => r.json())
                .then(data => {{
                    if (data.success) {{
                        closeModal(modals.addFavorite);
                        loadFavorites();
                        alert('Добавлено в избранное!');
                    }} else {{
                        alert(data.error || 'Ошибка');
                    }}
                }});
            }});
            
            // Переименование канала
            document.getElementById('cancel-rename-channel').addEventListener('click', () => closeModal(modals.renameChannel));
            document.getElementById('submit-rename-channel').addEventListener('click', () => {{
                const newName = document.getElementById('new-channel-name').value.trim();
                
                if (!newName) {{
                    alert('Введите новое название');
                    return;
                }}
                
                fetch('/rename_channel', {{
                    method: 'POST',
                    headers: {{ 'Content-Type': 'application/json' }},
                    body: JSON.stringify({{
                        channel_name: currentRoom.replace('channel_', ''),
                        new_display_name: newName
                    }})
                }})
                .then(r => r.json())
                .then(data => {{
                    if (data.success) {{
                        closeModal(modals.renameChannel);
                        loadChannelSettings();
                        loadChannels();
                        alert('Канал переименован!');
                    }} else {{
                        alert(data.error || 'Ошибка');
                    }}
                }});
            }});
            
            // Добавление пользователя
            document.getElementById('cancel-add-user').addEventListener('click', () => closeModal(modals.addUser));
            document.getElementById('submit-add-user').addEventListener('click', () => {{
                const userSelect = document.getElementById('user-select');
                const selectedUser = userSelect.value;
                
                if (!selectedUser) {{
                    alert('Выберите пользователя');
                    return;
                }}
                
                fetch('/add_user_to_channel', {{
                    method: 'POST',
                    headers: {{ 'Content-Type': 'application/json' }},
                    body: JSON.stringify({{
                        channel_name: currentRoom.replace('channel_', ''),
                        username: selectedUser
                    }})
                }})
                .then(r => r.json())
                .then(data => {{
                    if (data.success) {{
                        closeModal(modals.addUser);
                        loadChannelSettings();
                        alert('Пользователь добавлен!');
                    }} else {{
                        alert(data.message || 'Ошибка');
                    }}
                }});
            }});
            
            // Настройки пользователя
            document.querySelectorAll('[data-theme]').forEach(btn => {{
                btn.addEventListener('click', () => {{
                    const theme = btn.dataset.theme;
                    fetch('/set_theme', {{
                        method: 'POST',
                        headers: {{ 'Content-Type': 'application/json' }},
                        body: JSON.stringify({{ theme: theme }})
                    }})
                    .then(r => r.json())
                    .then(data => {{
                        if (data.success) {{
                            document.documentElement.setAttribute('data-theme', theme);
                            alert('Тема изменена!');
                        }}
                    }});
                }});
            }});
            
            document.getElementById('logout-btn').addEventListener('click', () => {{
                window.location.href = '/logout';
            }});
            
            document.getElementById('change-avatar-btn').addEventListener('click', () => {{
                openModal(modals.avatar);
                // Загружаем текущий аватар в модалку
                const modalAvatar = document.getElementById('modal-avatar');
                const currentAvatar = elements.userAvatar;
                modalAvatar.style.backgroundImage = currentAvatar.style.backgroundImage;
                modalAvatar.style.backgroundColor = currentAvatar.style.backgroundColor;
                modalAvatar.textContent = currentAvatar.textContent;
            }});
            
            document.getElementById('upload-avatar-btn').addEventListener('click', () => {{
                const input = document.createElement('input');
                input.type = 'file';
                input.accept = 'image/*';
                input.onchange = (e) => {{
                    const file = e.target.files[0];
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
                                loadUserData();
                                closeModal(modals.avatar);
                                alert('Аватар обновлен!');
                            }} else {{
                                alert(data.error || 'Ошибка');
                            }}
                        }});
                    }}
                }};
                input.click();
            }});
            
            document.getElementById('delete-avatar-btn').addEventListener('click', () => {{
                if (!confirm('Удалить аватар?')) return;
                
                fetch('/delete_avatar', {{ method: 'POST' }})
                    .then(r => r.json())
                    .then(data => {{
                        if (data.success) {{
                            loadUserData();
                            closeModal(modals.avatar);
                            alert('Аватар удален!');
                        }}
                    }});
            }});
            
            // Обработчики событий сокета
            socket.on('message', (data) => {{
                if (data.room === currentRoom && currentView === 'chat') {{
                    loadMessages();
                }}
            }});
            
            // Инициализация избранного
            showView('favorites');
            
            // Запрос на создание папки документов
            fetch('/create_docs_folder', {{ method: 'POST' }});
        }});
        
        // Обработка изменения размера окна
        window.addEventListener('resize', () => {{
            isMobile = window.innerWidth < 768;
            if (!isMobile) {{
                elements.sidebar.classList.remove('active');
            }}
        }});
        
        // Предотвращение скролла body на мобильных
        document.addEventListener('touchmove', (e) => {{
            if (e.target.closest('.scrollbar-thin')) return;
            e.preventDefault();
        }}, {{ passive: false }});
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
