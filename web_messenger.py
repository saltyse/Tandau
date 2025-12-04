# web_messenger.py - Tandau Messenger
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
                    'color': user_info['avatar_color'] if user_info else '#6366F1'
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

    # === Основные маршруты ===
    @app.route('/')
    def index():
        if 'username' in session: 
            return redirect('/chat')
        
        # Современная страница входа/регистрации с логотипом
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
                }
                
                .terms a:hover {
                    text-decoration: underline;
                }
                
                @keyframes fadeInUp {
                    from {
                        opacity: 0;
                        transform: translateY(20px);
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
                
                @media (max-width: 480px) {
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
                                Входя в систему, вы соглашаетесь с нашими <a href="#">Условиями использования</a>
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
                                Регистрируясь, вы соглашаетесь с нашими <a href="#">Условиями использования</a> и <a href="#">Политикой конфиденциальности</a>
                            </div>
                        </form>
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
        
        # Генерируем HTML с новым функционалом Избранного
        return f'''
<!DOCTYPE html>
<html lang="ru" data-theme="{theme}">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>Tandau Chat - {username}</title>
    <link rel="stylesheet" href="https://cdnjs.cloudflare.com/ajax/libs/font-awesome/6.5.0/css/all.min.css">
    <style>
        :root {{
            --bg: #f8f9fa;
            --text: #333;
            --input: #fff;
            --border: #ddd;
            --accent: #667eea;
            --sidebar-width: 300px;
            --favorite-color: #ffd700;
            --primary: #6366f1;
            --primary-dark: #4f46e5;
            --primary-light: #818cf8;
        }}
        
        [data-theme="dark"] {{
            --bg: #1a1a1a;
            --text: #eee;
            --input: #2d2d2d;
            --border: #444;
            --accent: #8b5cf6;
            --favorite-color: #ffed4e;
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
        }}
        
        /* Сайдбар */
        .sidebar {{
            width: var(--sidebar-width);
            background: var(--input);
            border-right: 1px solid var(--border);
            display: flex;
            flex-direction: column;
        }}
        
        .sidebar-header {{
            padding: 20px;
            background: linear-gradient(135deg, #667eea 0%, #764ba2 100%);
            color: white;
            text-align: center;
            font-weight: 700;
            font-size: 1.2rem;
            display: flex;
            align-items: center;
            justify-content: center;
            gap: 12px;
        }}
        
        .logo-placeholder {{
            width: 40px;
            height: 40px;
            border-radius: 12px;
            background: rgba(255, 255, 255, 0.2);
            display: flex;
            align-items: center;
            justify-content: center;
            font-size: 20px;
            font-weight: bold;
            box-shadow: 0 4px 12px rgba(0, 0, 0, 0.15);
        }}
        
        .app-title {{
            color: white;
            font-size: 1.8rem;
            font-weight: 800;
            letter-spacing: -0.5px;
        }}
        
        .user-info {{
            padding: 20px 15px;
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
            background-size: cover;
            background-position: center;
            cursor: pointer;
            border: 2px solid var(--accent);
        }}
        
        .user-details {{
            flex: 1;
        }}
        
        .user-details strong {{
            display: block;
            font-size: 1rem;
            margin-bottom: 4px;
        }}
        
        .user-status {{
            font-size: 0.85rem;
            opacity: 0.8;
            display: flex;
            align-items: center;
            gap: 5px;
        }}
        
        .status-dot {{
            width: 8px;
            height: 8px;
            border-radius: 50%;
            background: #10b981;
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
            display: flex;
            justify-content: space-between;
            align-items: center;
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
        
        .nav-item.favorite {{
            border-left: 3px solid var(--favorite-color);
        }}
        
        .nav-item i {{
            width: 20px;
            text-align: center;
        }}
        
        .add-btn {{
            background: none;
            border: none;
            color: inherit;
            cursor: pointer;
            padding: 4px;
            border-radius: 4px;
            display: flex;
            align-items: center;
            justify-content: center;
        }}
        
        /* Область чата */
        .chat-area {{
            flex: 1;
            display: flex;
            flex-direction: column;
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
        
        .channel-actions {{
            margin-left: auto;
            display: flex;
            gap: 10px;
        }}
        
        .channel-btn {{
            background: none;
            border: none;
            color: var(--text);
            cursor: pointer;
            padding: 5px;
            border-radius: 4px;
            display: flex;
            align-items: center;
            justify-content: center;
        }}
        
        .messages {{
            flex: 1;
            padding: 15px;
            overflow-y: auto;
            display: flex;
            flex-direction: column;
        }}
        
        /* Стили сообщений */
        .message-container {{
            display: flex;
            flex-direction: column;
            gap: 8px;
        }}
        
        .message {{
            display: flex;
            align-items: flex-start;
            gap: 12px;
            padding: 8px 0;
            animation: fadeIn 0.3s ease;
        }}
        
        .message.own {{
            flex-direction: row-reverse;
        }}
        
        .message-avatar {{
            width: 32px;
            height: 32px;
            border-radius: 50%;
            background: var(--accent);
            color: white;
            display: flex;
            align-items: center;
            justify-content: center;
            font-weight: bold;
            font-size: 0.9rem;
            flex-shrink: 0;
            background-size: cover;
            background-position: center;
        }}
        
        .message-content {{
            max-width: 70%;
            background: var(--input);
            padding: 12px 16px;
            border-radius: 18px;
            border-top-left-radius: 4px;
            box-shadow: 0 1px 2px rgba(0,0,0,0.1);
        }}
        
        .message.own .message-content {{
            background: var(--accent);
            color: white;
            border-top-left-radius: 18px;
            border-top-right-radius: 4px;
        }}
        
        .message-sender {{
            font-weight: 600;
            font-size: 0.9rem;
            margin-bottom: 4px;
            color: var(--text);
        }}
        
        .message.own .message-sender {{
            color: white;
        }}
        
        .message-text {{
            word-break: break-word;
            line-height: 1.4;
        }}
        
        .message-file {{
            margin-top: 8px;
            border-radius: 12px;
            overflow: hidden;
            max-width: 300px;
        }}
        
        .message-file img {{
            max-width: 100%;
            max-height: 300px;
            border-radius: 8px;
            cursor: pointer;
            transition: transform 0.2s;
        }}
        
        .message-file img:hover {{
            transform: scale(1.02);
        }}
        
        .message-file video {{
            max-width: 100%;
            max-height: 300px;
            border-radius: 8px;
        }}
        
        .message-time {{
            font-size: 0.75rem;
            color: #666;
            margin-top: 4px;
            text-align: right;
        }}
        
        .message.own .message-time {{
            color: rgba(255,255,255,0.8);
        }}
        
        /* Стили для избранного */
        .favorites-grid {{
            display: grid;
            grid-template-columns: repeat(auto-fill, minmax(200px, 1fr));
            gap: 15px;
            padding: 20px;
        }}
        
        .favorite-item {{
            background: var(--input);
            border-radius: 12px;
            padding: 15px;
            border: 1px solid var(--border);
            position: relative;
            transition: transform 0.2s ease;
        }}
        
        .favorite-item:hover {{
            transform: translateY(-2px);
            box-shadow: 0 4px 12px rgba(0,0,0,0.1);
        }}
        
        .favorite-item.pinned {{
            border-left: 4px solid var(--favorite-color);
        }}
        
        .favorite-content {{
            margin-bottom: 10px;
            word-break: break-word;
        }}
        
        .favorite-file {{
            max-width: 100%;
            border-radius: 8px;
            overflow: hidden;
            margin-bottom: 10px;
        }}
        
        .favorite-file img, .favorite-file video {{
            width: 100%;
            height: auto;
            display: block;
            border-radius: 8px;
        }}
        
        .favorite-meta {{
            display: flex;
            justify-content: space-between;
            align-items: center;
            font-size: 0.8rem;
            color: #666;
            margin-top: 10px;
        }}
        
        .favorite-actions {{
            position: absolute;
            top: 10px;
            right: 10px;
            display: flex;
            gap: 5px;
            opacity: 0;
            transition: opacity 0.2s ease;
        }}
        
        .favorite-item:hover .favorite-actions {{
            opacity: 1;
        }}
        
        .favorite-action-btn {{
            background: rgba(0,0,0,0.7);
            color: white;
            border: none;
            width: 28px;
            height: 28px;
            border-radius: 50%;
            display: flex;
            align-items: center;
            justify-content: center;
            cursor: pointer;
            font-size: 0.8rem;
        }}
        
        .category-badge {{
            display: inline-block;
            padding: 2px 8px;
            background: var(--accent);
            color: white;
            border-radius: 12px;
            font-size: 0.7rem;
            margin-top: 5px;
        }}
        
        .empty-favorites {{
            text-align: center;
            padding: 60px 20px;
            color: #666;
        }}
        
        .empty-favorites i {{
            font-size: 3rem;
            margin-bottom: 20px;
            color: #ccc;
        }}
        
        .categories-filter {{
            display: flex;
            gap: 10px;
            padding: 15px;
            background: var(--input);
            border-bottom: 1px solid var(--border);
            flex-wrap: wrap;
        }}
        
        .category-filter-btn {{
            padding: 6px 12px;
            background: var(--bg);
            border: 1px solid var(--border);
            border-radius: 20px;
            cursor: pointer;
            font-size: 0.9rem;
            transition: all 0.2s ease;
        }}
        
        .category-filter-btn.active {{
            background: var(--accent);
            color: white;
            border-color: var(--accent);
        }}
        
        /* Стили для настроек канала */
        .settings-content {{
            padding: 20px;
        }}
        
        .settings-section {{
            margin-bottom: 30px;
        }}
        
        .settings-title {{
            font-size: 1.1rem;
            font-weight: 600;
            margin-bottom: 15px;
            color: var(--text);
            padding-bottom: 8px;
            border-bottom: 1px solid var(--border);
        }}
        
        .member-list {{
            background: var(--bg);
            border-radius: 10px;
            border: 1px solid var(--border);
            max-height: 300px;
            overflow-y: auto;
        }}
        
        .member-item {{
            display: flex;
            align-items: center;
            justify-content: space-between;
            padding: 12px 15px;
            border-bottom: 1px solid var(--border);
        }}
        
        .member-item:last-child {{
            border-bottom: none;
        }}
        
        .member-info {{
            display: flex;
            align-items: center;
            gap: 10px;
        }}
        
        .member-avatar {{
            width: 32px;
            height: 32px;
            border-radius: 50%;
            display: flex;
            align-items: center;
            justify-content: center;
            font-weight: bold;
            font-size: 0.9rem;
            background-size: cover;
            background-position: center;
        }}
        
        .member-name {{
            font-size: 0.95rem;
        }}
        
        .member-role {{
            font-size: 0.8rem;
            color: #666;
            padding: 2px 8px;
            background: var(--bg);
            border-radius: 12px;
            border: 1px solid var(--border);
        }}
        
        .member-role.admin {{
            background: var(--accent);
            color: white;
            border-color: var(--accent);
        }}
        
        .member-actions {{
            display: flex;
            gap: 5px;
        }}
        
        .action-btn {{
            background: var(--bg);
            border: 1px solid var(--border);
            border-radius: 6px;
            padding: 4px 10px;
            font-size: 0.8rem;
            cursor: pointer;
            transition: all 0.2s ease;
        }}
        
        .action-btn:hover {{
            background: var(--accent);
            color: white;
            border-color: var(--accent);
        }}
        
        .action-btn.remove {{
            background: #dc3545;
            color: white;
            border-color: #dc3545;
        }}
        
        /* Область ввода сообщения */
        .input-area {{
            padding: 15px 20px;
            background: var(--input);
            border-top: 1px solid var(--border);
        }}
        
        .input-row {{
            display: flex;
            gap: 10px;
            align-items: flex-end;
        }}
        
        .attachment-btn {{
            background: none;
            border: none;
            color: var(--text);
            cursor: pointer;
            font-size: 1.2rem;
            padding: 10px;
            border-radius: 50%;
            width: 44px;
            height: 44px;
            display: flex;
            align-items: center;
            justify-content: center;
            flex-shrink: 0;
        }}
        
        .attachment-btn:hover {{
            background: rgba(0,0,0,0.05);
        }}
        
        [data-theme="dark"] .attachment-btn:hover {{
            background: rgba(255,255,255,0.1);
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
            line-height: 1.4;
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
            transition: background 0.2s;
        }}
        
        .send-btn:hover {{
            background: var(--primary-dark);
        }}
        
        .file-preview {{
            margin-top: 10px;
            padding: 10px;
            background: var(--bg);
            border-radius: 12px;
            border: 1px dashed var(--border);
        }}
        
        .file-preview img, .file-preview video {{
            max-width: 200px;
            max-height: 150px;
            border-radius: 8px;
        }}
        
        /* Модальные окна */
        .modal {{
            display: none;
            position: fixed;
            top: 0;
            left: 0;
            width: 100%;
            height: 100%;
            background: rgba(0,0,0,0.5);
            z-index: 2000;
            align-items: center;
            justify-content: center;
        }}
        
        .modal-content {{
            background: var(--input);
            padding: 25px;
            border-radius: 15px;
            width: 90%;
            max-width: 500px;
            max-height: 90vh;
            overflow-y: auto;
        }}
        
        .form-group {{
            margin-bottom: 15px;
        }}
        
        .form-label {{
            display: block;
            margin-bottom: 5px;
            font-weight: 500;
        }}
        
        .form-control {{
            width: 100%;
            padding: 10px;
            border: 1px solid var(--border);
            border-radius: 8px;
            background: var(--bg);
            color: var(--text);
        }}
        
        .form-control:focus {{
            outline: none;
            border-color: var(--accent);
        }}
        
        .select-control {{
            width: 100%;
            padding: 10px;
            border: 1px solid var(--border);
            border-radius: 8px;
            background: var(--bg);
            color: var(--text);
            font-size: 1rem;
        }}
        
        .btn {{
            padding: 10px 20px;
            border: none;
            border-radius: 8px;
            cursor: pointer;
            font-weight: 500;
            transition: all 0.2s ease;
        }}
        
        .btn-primary {{
            background: var(--accent);
            color: white;
        }}
        
        .btn-primary:hover {{
            opacity: 0.9;
        }}
        
        .btn-secondary {{
            background: #6c757d;
            color: white;
        }}
        
        .avatar-upload {{
            text-align: center;
            margin: 20px 0;
        }}
        
        .avatar-preview {{
            width: 100px;
            height: 100px;
            border-radius: 50%;
            margin: 0 auto 15px;
            background: var(--accent);
            background-size: cover;
            background-position: center;
            cursor: pointer;
            border: 3px solid var(--accent);
        }}
        
        .theme-btn {{
            padding: 10px 20px;
            margin: 5px;
            border: none;
            border-radius: 8px;
            background: var(--accent);
            color: white;
            cursor: pointer;
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
            display: flex;
            align-items: center;
            justify-content: center;
            gap: 8px;
        }}
        
        /* Скроллбар */
        ::-webkit-scrollbar {{
            width: 8px;
        }}
        
        ::-webkit-scrollbar-track {{
            background: transparent;
        }}
        
        ::-webkit-scrollbar-thumb {{
            background: #ccc;
            border-radius: 4px;
        }}
        
        [data-theme="dark"] ::-webkit-scrollbar-thumb {{
            background: #555;
        }}
        
        /* Анимации */
        @keyframes fadeIn {{
            from {{ opacity: 0; transform: translateY(10px); }}
            to {{ opacity: 1; transform: translateY(0); }}
        }}
        
        /* Пустой чат */
        .empty-chat {{
            display: flex;
            flex-direction: column;
            align-items: center;
            justify-content: center;
            height: 100%;
            color: #666;
            text-align: center;
            padding: 40px;
        }}
        
        .empty-chat i {{
            font-size: 4rem;
            margin-bottom: 20px;
            opacity: 0.3;
        }}
    </style>
</head>
<body>
    <div class="app-container">
        <!-- Сайдбар -->
        <div class="sidebar">
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
                    <div class="nav-item favorite" onclick="openFavorites()">
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
        <div class="chat-area">
            <div class="chat-header">
                <span id="chat-title">Избранное</span>
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
                <!-- Категории будут добавлены динамически -->
            </div>
            
            <div class="messages" id="messages">
                <!-- Для избранного показываем сетку заметок -->
                <div id="favorites-grid" class="favorites-grid"></div>
                
                <!-- Для настроек канала -->
                <div id="channel-settings" style="display: none;"></div>
                
                <!-- Для чата показываем сообщения -->
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
                <button class="theme-btn" onclick="setTheme('light')">🌞 Светлая</button>
                <button class="theme-btn" onclick="setTheme('dark')">🌙 Темная</button>
                <button class="theme-btn" onclick="setTheme('auto')">⚙️ Авто</button>
            </div>
            <button class="btn btn-secondary" onclick="closeThemeModal()">Закрыть</button>
        </div>
    </div>

    <div class="modal" id="avatar-modal">
        <div class="modal-content">
            <h3>Смена аватарки</h3>
            <div class="avatar-upload">
                <div class="avatar-preview" id="avatar-preview" onclick="document.getElementById('avatar-input').click()"></div>
                <input type="file" id="avatar-input" accept="image/*" style="display:none" onchange="previewAvatar(this)">
                <div style="display: flex; gap: 10px; justify-content: center; margin-top: 15px;">
                    <button class="btn btn-primary" onclick="uploadAvatar()">Загрузить</button>
                    <button class="btn btn-secondary" onclick="removeAvatar()">Удалить</button>
                </div>
            </div>
            <button class="btn btn-secondary" onclick="closeAvatarModal()">Закрыть</button>
        </div>
    </div>

    <div class="modal" id="create-channel-modal">
        <div class="modal-content">
            <h3>Создать канал</h3>
            <div class="form-group">
                <input type="text" class="form-control" id="channel-name" placeholder="Идентификатор канала (латинские буквы, цифры, _)">
                <input type="text" class="form-control" id="channel-display-name" placeholder="Отображаемое название">
                <input type="text" class="form-control" id="channel-description" placeholder="Описание">
                <label><input type="checkbox" id="channel-private"> Приватный канал</label>
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
                <select class="select-control" id="user-select">
                    <option value="">Выберите пользователя...</option>
                    <!-- Пользователи будут загружены динамически -->
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
                <input type="file" class="form-control" id="favorite-file" accept="image/*,video/*,text/*,.pdf,.doc,.docx">
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
        let room = "favorites";
        let roomType = "favorites";
        let currentChannel = "";
        let currentCategory = "all";

        // Инициализация при загрузке
        window.onload = function() {{
            loadUserAvatar();
            loadUserChannels();
            loadUsers();
            loadPersonalChats();
            loadFavoritesCategories();
            loadFavorites();
            
            // Открываем избранное по умолчанию
            openFavorites();
        }};

        // Загрузка аватарки пользователя
        function loadUserAvatar() {{
            fetch('/user_info/' + user)
                .then(r => r.json())
                .then(userInfo => {{
                    if (userInfo.success) {{
                        const avatar = document.getElementById('user-avatar');
                        if (userInfo.avatar_path) {{
                            avatar.style.backgroundImage = `url(${{userInfo.avatar_path}})`;
                            avatar.textContent = '';
                        }} else {{
                            avatar.style.backgroundImage = 'none';
                            avatar.style.backgroundColor = userInfo.avatar_color;
                            avatar.textContent = user.slice(0, 2).toUpperCase();
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
                        
                        // Добавляем кнопку "Все"
                        const allBtn = document.createElement('button');
                        allBtn.className = 'category-filter-btn active';
                        allBtn.textContent = 'Все';
                        allBtn.onclick = () => filterFavorites('all');
                        filterContainer.appendChild(allBtn);
                        
                        // Добавляем категории
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
                        
                        if (data.favorites.length === 0) {{
                            grid.innerHTML = `
                                <div class="empty-favorites">
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
                            <a href="${{favorite.file_path}}" target="_blank" style="font-size: 0.8rem;">Скачать</a>
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

        // Фильтрация избранного по категории
        function filterFavorites(category) {{
            currentCategory = category;
            
            // Обновляем активную кнопку
            document.querySelectorAll('.category-filter-btn').forEach(btn => {{
                btn.classList.remove('active');
            }});
            event?.currentTarget.classList.add('active');
            
            loadFavorites(category === 'all' ? null : category);
        }}

        // Функции для работы с избранным
        function openAddFavoriteModal() {{
            document.getElementById('add-favorite-modal').style.display = 'flex';
            document.getElementById('favorite-file').addEventListener('change', function(e) {{
                const file = e.target.files[0];
                const preview = document.getElementById('favorite-file-preview');
                
                if (file) {{
                    if (file.type.startsWith('image/')) {{
                        const reader = new FileReader();
                        reader.onload = (e) => {{
                            preview.innerHTML = `<img src="${{e.target.result}}" style="max-width: 100%; border-radius: 8px;">`;
                        }};
                        reader.readAsDataURL(file);
                    }} else if (file.type.startsWith('video/')) {{
                        const reader = new FileReader();
                        reader.onload = (e) => {{
                            preview.innerHTML = `<video src="${{e.target.result}}" controls style="max-width: 100%; border-radius: 8px;"></video>`;
                        }};
                        reader.readAsDataURL(file);
                    }} else {{
                        preview.innerHTML = `<div style="padding: 10px; background: #f0f0f0; border-radius: 8px;">
                            <i class="fas fa-file"></i> ${{file.name}}
                        </div>`;
                    }}
                }}
            }});
        }}

        function closeAddFavoriteModal() {{
            document.getElementById('add-favorite-modal').style.display = 'none';
            document.getElementById('favorite-content').value = '';
            document.getElementById('favorite-category').value = 'general';
            document.getElementById('favorite-file').value = '';
            document.getElementById('favorite-file-preview').innerHTML = '';
        }}

        function saveFavorite() {{
            const content = document.getElementById('favorite-content').value.trim();
            const category = document.getElementById('favorite-category').value.trim() || 'general';
            const fileInput = document.getElementById('favorite-file');
            const file = fileInput.files[0];
            
            if (!content && !file) {{
                alert('Добавьте текст или файл');
                return;
            }}
            
            const formData = new FormData();
            formData.append('content', content);
            formData.append('category', category);
            
            if (file) {{
                formData.append('file', file);
            }}
            
            fetch('/add_to_favorites', {{
                method: 'POST',
                body: formData
            }})
            .then(r => r.json())
            .then(data => {{
                if (data.success) {{
                    closeAddFavoriteModal();
                    loadFavoritesCategories();
                    loadFavorites(currentCategory === 'all' ? null : currentCategory);
                    alert('Добавлено в избранное!');
                }} else {{
                    alert(data.error || 'Ошибка при сохранении');
                }}
            }});
        }}

        function deleteFavorite(favoriteId) {{
            if (!confirm('Удалить эту заметку?')) return;
            
            fetch(`/delete_favorite/${{favoriteId}}`, {{
                method: 'DELETE'
            }})
            .then(r => r.json())
            .then(data => {{
                if (data.success) {{
                    document.getElementById(`favorite-${{favoriteId}}`).remove();
                    
                    // Если удалили последний элемент, показываем пустой экран
                    const grid = document.getElementById('favorites-grid');
                    if (grid.children.length === 0) {{
                        loadFavorites(currentCategory === 'all' ? null : currentCategory);
                    }}
                }} else {{
                    alert('Ошибка при удалении');
                }}
            }});
        }}

        function togglePinFavorite(favoriteId) {{
            fetch(`/toggle_pin_favorite/${{favoriteId}}`, {{
                method: 'POST'
            }})
            .then(r => r.json())
            .then(data => {{
                if (data.success) {{
                    const item = document.getElementById(`favorite-${{favoriteId}}`);
                    if (data.pinned) {{
                        item.classList.add('pinned');
                    }} else {{
                        item.classList.remove('pinned');
                    }}
                    
                    // Перезагружаем чтобы обновить порядок
                    loadFavorites(currentCategory === 'all' ? null : currentCategory);
                }}
            }});
        }}

        function openFilePreview(filePath) {{
            const win = window.open(filePath, '_blank');
            if (win) {{
                win.focus();
            }}
        }}

        // Открытие избранного
        function openFavorites() {{
            room = "favorites";
            roomType = "favorites";
            
            document.getElementById('chat-title').textContent = 'Избранное';
            document.getElementById('categories-filter').style.display = 'flex';
            document.getElementById('favorites-grid').style.display = 'grid';
            document.getElementById('channel-settings').style.display = 'none';
            document.getElementById('chat-messages').style.display = 'none';
            document.getElementById('input-area').style.display = 'none';
            document.getElementById('channel-actions').style.display = 'none';
            
            // Обновляем активные элементы в навигации
            document.querySelectorAll('.nav-item').forEach(el => el.classList.remove('active'));
            event.currentTarget.classList.add('active');
            
            loadFavorites(currentCategory === 'all' ? null : currentCategory);
        }}

        // Функции для работы с аватарками
        function openAvatarModal() {{
            document.getElementById('avatar-modal').style.display = 'flex';
            const preview = document.getElementById('avatar-preview');
            fetch('/user_info/' + user)
                .then(r => r.json())
                .then(userInfo => {{
                    if (userInfo.success) {{
                        if (userInfo.avatar_path) {{
                            preview.style.backgroundImage = `url(${{userInfo.avatar_path}})`;
                            preview.textContent = '';
                        }} else {{
                            preview.style.backgroundImage = 'none';
                            preview.style.backgroundColor = userInfo.avatar_color;
                            preview.textContent = user.slice(0, 2).toUpperCase();
                        }}
                    }}
                }});
        }}

        function closeAvatarModal() {{
            document.getElementById('avatar-modal').style.display = 'none';
        }}

        function previewAvatar(input) {{
            const file = input.files[0];
            if (file) {{
                const reader = new FileReader();
                reader.onload = (e) => {{
                    const preview = document.getElementById('avatar-preview');
                    preview.style.backgroundImage = `url(${{e.target.result}})`;
                    preview.textContent = '';
                }};
                reader.readAsDataURL(file);
            }}
        }}

        function uploadAvatar() {{
            const fileInput = document.getElementById('avatar-input');
            const file = fileInput.files[0];
            
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
                        loadUserAvatar();
                        closeAvatarModal();
                        alert('Аватарка обновлена!');
                    }} else {{
                        alert(data.error || 'Ошибка загрузки аватарки');
                    }}
                }});
            }} else {{
                alert('Выберите файл');
            }}
        }}

        function removeAvatar() {{
            fetch('/delete_avatar', {{ method: 'POST' }})
                .then(r => r.json())
                .then(data => {{
                    if (data.success) {{
                        loadUserAvatar();
                        closeAvatarModal();
                        alert('Аватарка удалена!');
                    }}
                }});
        }}

        // Функции для работы с темами
        function openThemeModal() {{
            document.getElementById('theme-modal').style.display = 'flex';
        }}

        function closeThemeModal() {{
            document.getElementById('theme-modal').style.display = 'none';
        }}

        function setTheme(theme) {{
            fetch('/set_theme', {{
                method: 'POST',
                headers: {{ 'Content-Type': 'application/json' }},
                body: JSON.stringify({{ theme: theme }})
            }})
            .then(r => r.json())
            .then(data => {{
                if (data.success) {{
                    document.documentElement.setAttribute('data-theme', theme);
                    closeThemeModal();
                }}
            }});
        }}

        // Функции для работы с каналами
        function openCreateChannelModal() {{
            document.getElementById('create-channel-modal').style.display = 'flex';
        }}

        function closeCreateChannelModal() {{
            document.getElementById('create-channel-modal').style.display = 'none';
        }}

        function openRenameModal() {{
            document.getElementById('rename-modal').style.display = 'flex';
            document.getElementById('channel-rename-input').value = document.getElementById('chat-title').textContent.replace('# ', '');
        }}

        function closeRenameModal() {{
            document.getElementById('rename-modal').style.display = 'none';
        }}

        function openAddUserModal() {{
            document.getElementById('add-user-modal').style.display = 'flex';
            
            // Загружаем доступных пользователей
            fetch(`/get_available_users?channel_name=${{encodeURIComponent(currentChannel)}}`)
                .then(r => r.json())
                .then(data => {{
                    if (data.success) {{
                        const select = document.getElementById('user-select');
                        select.innerHTML = '<option value="">Выберите пользователя...</option>';
                        
                        data.users.forEach(username => {{
                            const option = document.createElement('option');
                            option.value = username;
                            option.textContent = username;
                            select.appendChild(option);
                        }});
                    }}
                }});
        }}

        function closeAddUserModal() {{
            document.getElementById('add-user-modal').style.display = 'none';
            document.getElementById('user-select').value = '';
        }}

        function createChannel() {{
            const name = document.getElementById('channel-name').value.trim();
            const displayName = document.getElementById('channel-display-name').value.trim();
            const description = document.getElementById('channel-description').value.trim();
            const isPrivate = document.getElementById('channel-private').checked;
            
            if (!name) {{
                alert('Введите идентификатор канала');
                return;
            }}
            
            // Проверка имени канала
            if (!/^[a-zA-Z0-9_]+$/.test(name)) {{
                alert('Идентификатор канала может содержать только латинские буквы, цифры и символ подчеркивания');
                return;
            }}
            
            fetch('/create_channel', {{
                method: 'POST',
                headers: {{ 'Content-Type': 'application/json' }},
                body: JSON.stringify({{
                    name: name,
                    display_name: displayName || name,
                    description: description,
                    is_private: isPrivate
                }})
            }})
            .then(r => r.json())
            .then(data => {{
                if (data.success) {{
                    closeCreateChannelModal();
                    loadUserChannels();
                    alert('Канал создан!');
                }} else {{
                    alert(data.error || 'Ошибка при создании канала');
                }}
            }});
        }}

        function renameChannel() {{
            const newName = document.getElementById('channel-rename-input').value.trim();
            if (!newName) {{
                alert('Введите новое название');
                return;
            }}
            
            fetch('/rename_channel', {{
                method: 'POST',
                headers: {{ 'Content-Type': 'application/json' }},
                body: JSON.stringify({{
                    channel_name: currentChannel,
                    new_display_name: newName
                }})
            }})
            .then(r => r.json())
            .then(data => {{
                if (data.success) {{
                    document.getElementById('chat-title').textContent = '# ' + newName;
                    closeRenameModal();
                    loadUserChannels();
                    alert('Канал переименован!');
                }} else {{
                    alert(data.error || 'Ошибка при переименовании канала');
                }}
            }});
        }}

        function addUserToChannel() {{
            const selectedUser = document.getElementById('user-select').value;
            if (!selectedUser) {{
                alert('Выберите пользователя');
                return;
            }}
            
            fetch('/add_user_to_channel', {{
                method: 'POST',
                headers: {{ 'Content-Type': 'application/json' }},
                body: JSON.stringify({{
                    channel_name: currentChannel,
                    username: selectedUser
                }})
            }})
            .then(r => r.json())
            .then(data => {{
                if (data.success) {{
                    closeAddUserModal();
                    openChannelSettings(); // Обновляем настройки
                    alert(data.message || 'Пользователь добавлен');
                }} else {{
                    alert(data.message || 'Ошибка при добавлении пользователя');
                }}
            }});
        }}

        function removeUserFromChannel(username) {{
            if (!confirm(`Удалить пользователя ${{username}} из канала?`)) return;
            
            fetch('/remove_user_from_channel', {{
                method: 'POST',
                headers: {{ 'Content-Type': 'application/json' }},
                body: JSON.stringify({{
                    channel_name: currentChannel,
                    username: username
                }})
            }})
            .then(r => r.json())
            .then(data => {{
                if (data.success) {{
                    openChannelSettings(); // Обновляем настройки
                    alert(data.message || 'Пользователь удален');
                }} else {{
                    alert(data.message || 'Ошибка при удалении пользователя');
                }}
            }});
        }}

        // Открытие настроек канала
        function openChannelSettings() {{
            room = "settings_" + currentChannel;
            roomType = "settings";
            
            document.getElementById('chat-title').textContent = 'Настройки канала';
            document.getElementById('categories-filter').style.display = 'none';
            document.getElementById('favorites-grid').style.display = 'none';
            document.getElementById('chat-messages').style.display = 'none';
            document.getElementById('input-area').style.display = 'none';
            document.getElementById('channel-actions').style.display = 'none';
            
            // Загружаем информацию о канале
            fetch(`/channel_info/${{encodeURIComponent(currentChannel)}}`)
                .then(r => r.json())
                .then(data => {{
                    if (data.success) {{
                        renderChannelSettings(data.data);
                    }}
                }});
        }}

        // Рендеринг настроек канала
        function renderChannelSettings(channelInfo) {{
            const settingsContainer = document.getElementById('channel-settings');
            settingsContainer.innerHTML = '';
            settingsContainer.style.display = 'block';
            
            // Создаем HTML для настроек
            let settingsHTML = `
                <div class="settings-content">
                    <div class="settings-section">
                        <div class="settings-title">Информация о канале</div>
                        <div style="margin-bottom: 15px;">
                            <strong>Название:</strong> ${{channelInfo.display_name || channelInfo.name}}
                        </div>
                        <div style="margin-bottom: 15px;">
                            <strong>Описание:</strong> ${{channelInfo.description || 'Нет описания'}}
                        </div>
                        <div style="margin-bottom: 15px;">
                            <strong>Создатель:</strong> ${{channelInfo.created_by}}
                        </div>
                        <div>
                            <strong>Тип:</strong> ${{channelInfo.is_private ? 'Приватный' : 'Публичный'}}
                        </div>
                    </div>
                    
                    <div class="settings-section">
                        <div class="settings-title" style="display: flex; justify-content: space-between; align-items: center;">
                            <span>Участники (${{channelInfo.members.length}})</span>
                            <button class="action-btn" onclick="openAddUserModal()" style="padding: 4px 12px;">
                                <i class="fas fa-user-plus"></i> Добавить
                            </button>
                        </div>
                        <div class="member-list" id="members-list">
            `;
            
            // Добавляем участников
            channelInfo.members.forEach(member => {{
                const isCurrentUser = member.username === user;
                settingsHTML += `
                    <div class="member-item">
                        <div class="member-info">
                            <div class="member-avatar" style="background-color: ${{member.color}};">
                                ${{member.avatar ? '' : member.username.slice(0, 2).toUpperCase()}}
                            </div>
                            <div class="member-name">
                                ${{member.username}}
                                ${{member.is_admin ? '<span class="member-role admin">Админ</span>' : '<span class="member-role">Участник</span>'}}
                            </div>
                        </div>
                `;
                
                // Кнопки действий (только для администраторов и не для себя)
                if (channelInfo.created_by === user && !isCurrentUser) {{
                    settingsHTML += `
                        <div class="member-actions">
                            <button class="action-btn remove" onclick="removeUserFromChannel('${{member.username}}')" title="Удалить из канала">
                                <i class="fas fa-user-minus"></i>
                            </button>
                        </div>
                    `;
                }}
                
                settingsHTML += `</div>`;
            }});
            
            settingsHTML += `
                </div>
            </div>
            
            <div class="settings-section">
                <div class="settings-title">Управление каналом</div>
                <div style="display: flex; gap: 10px;">
                    <button class="btn btn-primary" onclick="openRenameModal()">
                        <i class="fas fa-edit"></i> Переименовать
                    </button>
                    <button class="btn btn-secondary" onclick="openRoom('channel_' + currentChannel, 'channel', currentChannel)">
                        <i class="fas fa-arrow-left"></i> Вернуться в чат
                    </button>
                </div>
            </div>
        </div>
    `;
    
    settingsContainer.innerHTML = settingsHTML;
    
    // Загружаем аватарки участников
    channelInfo.members.forEach(member => {{
        if (member.avatar) {{
            const avatar = settingsContainer.querySelector(`.member-avatar[style*="${{member.color}}"]`);
            if (avatar) {{
                avatar.style.backgroundImage = `url(${{member.avatar}})`;
                avatar.textContent = '';
            }}
        }}
    }});
}}

// Загрузка каналов пользователя
function loadUserChannels() {{
    fetch('/user_channels')
        .then(r => r.json())
        .then(data => {{
            if (data.success) {{
                const channelsContainer = document.getElementById('channels');
                channelsContainer.innerHTML = '';
                
                // Добавляем общий канал
                const generalEl = document.createElement('div');
                generalEl.className = 'nav-item' + (room === 'channel_general' ? ' active' : '');
                generalEl.innerHTML = `
                    <i class="fas fa-hashtag"></i>
                    <span>General</span>
                `;
                generalEl.onclick = () => openRoom('channel_general', 'channel', 'General');
                channelsContainer.appendChild(generalEl);
                
                // Добавляем пользовательские каналы
                data.channels.forEach(channel => {{
                    if (channel.name !== 'general') {{
                        const el = document.createElement('div');
                        el.className = 'nav-item' + (room === 'channel_' + channel.name ? ' active' : '');
                        el.innerHTML = `
                            <i class="fas fa-hashtag"></i>
                            <span>${{channel.display_name}}</span>
                        `;
                        el.onclick = () => openRoom('channel_' + channel.name, 'channel', channel.display_name);
                        channelsContainer.appendChild(el);
                    }}
                }});
            }}
        }});
}}

// Загрузка пользователей
function loadUsers() {{
    fetch('/users')
        .then(r => r.json())
        .then(users => {{
            if (users && Array.isArray(users)) {{
                const usersContainer = document.getElementById('users');
                usersContainer.innerHTML = '';
                
                users.forEach(u => {{
                    if (u.username !== user) {{
                        const el = document.createElement('div');
                        el.className = 'nav-item';
                        el.innerHTML = `
                            <i class="fas fa-user${{u.online ? '-check' : ''}}"></i>
                            <span>${{u.username}}</span>
                        `;
                        el.onclick = () => openRoom(
                            'private_' + [user, u.username].sort().join('_'),
                            'private',
                            u.username
                        );
                        usersContainer.appendChild(el);
                    }}
                }});
            }}
        }});
}}

// Загрузка личных чатов
function loadPersonalChats() {{
    fetch('/personal_chats')
        .then(r => r.json())
        .then(data => {{
            if (data.success) {{
                const pc = document.getElementById('personal-chats');
                pc.innerHTML = '';
                
                data.chats.forEach(chatUser => {{
                    const el = document.createElement('div');
                    el.className = 'nav-item';
                    el.innerHTML = `
                        <i class="fas fa-user"></i>
                        <span>${{chatUser}}</span>
                    `;
                    el.onclick = () => openRoom(
                        'private_' + [user, chatUser].sort().join('_'),
                        'private',
                        chatUser
                    );
                    pc.appendChild(el);
                }});
            }}
        }});
}}

// Открытие комнаты (чат или канал)
function openRoom(r, t, title) {{
    room = r;
    roomType = t;
    currentChannel = t === 'channel' ? r.replace('channel_', '') : '';
    
    document.getElementById('chat-title').textContent = t === 'channel' ? '# ' + title : title;
    document.getElementById('categories-filter').style.display = 'none';
    document.getElementById('favorites-grid').style.display = 'none';
    document.getElementById('channel-settings').style.display = 'none';
    document.getElementById('chat-messages').style.display = 'block';
    document.getElementById('input-area').style.display = 'flex';
    
    // Обновляем активные элементы в навигации
    document.querySelectorAll('.nav-item').forEach(el => el.classList.remove('active'));
    event.currentTarget.classList.add('active');
    
    // Очищаем чат и показываем заглушку
    const chatMessages = document.getElementById('chat-messages');
    chatMessages.innerHTML = '<div class="empty-chat"><i class="fas fa-comments"></i><h3>Начните общение</h3><p>Отправьте сообщение, чтобы начать чат</p></div>';
    
    // Показываем/скрываем кнопки управления каналом
    const channelActions = document.getElementById('channel-actions');
    if (t === 'channel') {{
        channelActions.style.display = 'flex';
    }} else {{
        channelActions.style.display = 'none';
    }}
    
    // Загружаем историю
    loadMessages(r);
    
    // Присоединяемся к комнате через сокет
    socket.emit('join', {{ room: r }});
}}

// Загрузка сообщений комнаты
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
            }} else {{
                messagesContainer.innerHTML = '<div class="empty-chat"><i class="fas fa-comments"></i><h3>Начните общение</h3><p>Отправьте сообщение, чтобы начать чат</p></div>';
            }}
            
            messagesContainer.scrollTop = messagesContainer.scrollHeight;
        }})
        .catch(error => console.error('Error loading messages:', error));
}}

// Добавление сообщения в чат
function addMessageToChat(data) {{
    const messagesContainer = document.getElementById('chat-messages');
    
    // Удаляем пустой экран, если он есть
    const emptyChat = messagesContainer.querySelector('.empty-chat');
    if (emptyChat) {{
        emptyChat.remove();
    }}
    
    const message = document.createElement('div');
    message.className = `message ${{data.user === user ? 'own' : 'other'}}`;
    
    // Создаем аватарку
    const avatar = document.createElement('div');
    avatar.className = 'message-avatar';
    avatar.style.backgroundColor = data.color || '#6366F1';
    if (data.user !== user) {{
        avatar.textContent = data.user.slice(0, 2).toUpperCase();
    }}
    
    // Создаем контент сообщения
    const content = document.createElement('div');
    content.className = 'message-content';
    
    // Добавляем отправителя (только для чужих сообщений)
    if (data.user !== user) {{
        const sender = document.createElement('div');
        sender.className = 'message-sender';
        sender.textContent = data.user;
        content.appendChild(sender);
    }}
    
    // Добавляем текст сообщения
    if (data.message) {{
        const text = document.createElement('div');
        text.className = 'message-text';
        text.innerHTML = data.message.replace(/\\n/g, '<br>');
        content.appendChild(text);
    }}
    
    // Добавляем файл, если есть
    if (data.file) {{
        const fileContainer = document.createElement('div');
        fileContainer.className = 'message-file';
        
        if (data.file.endsWith('.mp4') || data.file.endsWith('.webm') || data.file.endsWith('.mov')) {{
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
    
    // Добавляем время
    const time = document.createElement('div');
    time.className = 'message-time';
    time.textContent = data.timestamp || new Date().toLocaleTimeString([], {{ hour: '2-digit', minute: '2-digit' }});
    content.appendChild(time);
    
    // Собираем сообщение
    message.appendChild(avatar);
    message.appendChild(content);
    messagesContainer.appendChild(message);
    
    // Прокручиваем к последнему сообщению
    messagesContainer.scrollTop = messagesContainer.scrollHeight;
}}

// Отправка сообщения
function sendMessage() {{
    const input = document.getElementById('msg-input');
    const msg = input.value.trim();
    const fileInput = document.getElementById('file-input');
    
    if (!msg && !fileInput.files[0]) return;
    
    const data = {{ 
        message: msg, 
        room: room, 
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

function resetInput() {{
    document.getElementById('msg-input').value = '';
    document.getElementById('file-input').value = '';
    document.getElementById('file-preview').innerHTML = '';
    autoResizeTextarea();
}}

function handleKeydown(e) {{
    if (e.key === 'Enter' && !e.shiftKey) {{
        e.preventDefault();
        sendMessage();
    }}
}}

function autoResizeTextarea() {{
    const textarea = document.getElementById('msg-input');
    textarea.style.height = 'auto';
    textarea.style.height = Math.min(textarea.scrollHeight, 120) + 'px';
}}

document.getElementById('msg-input').addEventListener('input', autoResizeTextarea);

function handleFileSelect(input) {{
    const file = input.files[0];
    if (file) {{
        const reader = new FileReader();
        reader.onload = (e) => {{
            const preview = document.getElementById('file-preview');
            if (file.type.startsWith('image/')) {{
                preview.innerHTML = `
                    <div style="display: flex; align-items: center; gap: 10px;">
                        <img src="${{e.target.result}}" style="width: 60px; height: 60px; border-radius: 8px; object-fit: cover;">
                        <div>
                            <div style="font-weight: 500;">${{file.name}}</div>
                            <button onclick="document.getElementById('file-preview').innerHTML = ''; document.getElementById('file-input').value = '';" style="background: none; border: none; color: #dc3545; cursor: pointer; font-size: 0.9rem;">
                                <i class="fas fa-times"></i> Удалить
                            </button>
                        </div>
                    </div>
                `;
            }} else if (file.type.startsWith('video/')) {{
                preview.innerHTML = `
                    <div style="display: flex; align-items: center; gap: 10px;">
                        <video src="${{e.target.result}}" style="width: 60px; height: 60px; border-radius: 8px; object-fit: cover;"></video>
                        <div>
                            <div style="font-weight: 500;">${{file.name}}</div>
                            <button onclick="document.getElementById('file-preview').innerHTML = ''; document.getElementById('file-input').value = '';" style="background: none; border: none; color: #dc3545; cursor: pointer; font-size: 0.9rem;">
                                <i class="fas fa-times"></i> Удалить
                            </button>
                        </div>
                    </div>
                `;
            }} else {{
                preview.innerHTML = `
                    <div style="display: flex; align-items: center; gap: 10px; padding: 10px; background: var(--bg); border-radius: 8px;">
                        <i class="fas fa-file" style="font-size: 2rem; color: var(--accent);"></i>
                        <div style="flex: 1;">
                            <div style="font-weight: 500;">${{file.name}}</div>
                            <div style="font-size: 0.8rem; color: #666;">${{(file.size / 1024).toFixed(1)}} KB</div>
                        </div>
                        <button onclick="document.getElementById('file-preview').innerHTML = ''; document.getElementById('file-input').value = '';" style="background: none; border: none; color: #dc3545; cursor: pointer;">
                            <i class="fas fa-times"></i>
                        </button>
                    </div>
                `;
            }}
        }};
        reader.readAsDataURL(file);
    }}
}}

// Socket events
socket.on('message', (data) => {{
    if (data.room === room) {{
        addMessageToChat(data);
    }}
}});
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
        
        # Отправляем сообщение
        emit('message', {
            'user': session['username'], 
            'message': msg, 
            'file': file_path, 
            'fileType': file_type,
            'fileName': file_name or saved_file_name,
            'color': user_color,
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
