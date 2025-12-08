# web_messenger.py - Tandau Messenger с меню канала
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
    app.config['CHANNEL_AVATAR_FOLDER'] = 'static/channel_avatars'
    app.config['FAVORITE_FOLDER'] = 'static/favorites'
    app.config['MAX_CONTENT_LENGTH'] = 16 * 1024 * 1024  # 16MB
    ALLOWED_EXT = {'png', 'jpg', 'jpeg', 'gif', 'webp', 'mp4', 'webm', 'mov', 'txt', 'pdf', 'doc', 'docx'}

    # Создаем папки для загрузок
    try:
        os.makedirs(app.config['UPLOAD_FOLDER'], exist_ok=True)
        os.makedirs(app.config['AVATAR_FOLDER'], exist_ok=True)
        os.makedirs(app.config['CHANNEL_AVATAR_FOLDER'], exist_ok=True)
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
            # Добавляем поле для аватарки канала
            c.execute('''
                CREATE TABLE IF NOT EXISTS channels (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    name TEXT UNIQUE NOT NULL,
                    display_name TEXT,
                    description TEXT,
                    created_by TEXT NOT NULL,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    is_private BOOLEAN DEFAULT FALSE,
                    allow_messages BOOLEAN DEFAULT TRUE,
                    avatar_path TEXT,
                    avatar_color TEXT DEFAULT '#667eea'
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
            # Создаем общий канал по умолчанию с цветом аватарки
            c.execute('INSERT OR IGNORE INTO channels (name, display_name, description, created_by, avatar_color) VALUES (?, ?, ?, ?, ?)',
                      ('general', 'General', 'Общий канал', 'system', '#667eea'))
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

    def get_messages_for_room(room, limit=100):
        with sqlite3.connect('messenger.db') as conn:
            c = conn.cursor()
            c.execute('''
                SELECT username, message, message_type, file_path, file_name, timestamp 
                FROM messages 
                WHERE room = ? 
                ORDER BY timestamp ASC
                LIMIT ?
            ''', (room, limit))
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

    # Модифицированная функция создания канала с поддержкой аватарки
    def create_channel(name, display_name, description, created_by, is_private=False, avatar_path=None, avatar_color=None):
        with sqlite3.connect('messenger.db') as conn:
            c = conn.cursor()
            try:
                if not avatar_color:
                    avatar_color = random.choice(['#667eea', '#764ba2', '#10b981', '#f59e0b', '#ef4444', '#3b82f6'])
                
                c.execute('''
                    INSERT INTO channels (name, display_name, description, created_by, is_private, avatar_path, avatar_color) 
                    VALUES (?, ?, ?, ?, ?, ?, ?)
                ''', (name, display_name, description, created_by, is_private, avatar_path, avatar_color))
                channel_id = c.lastrowid
                c.execute('INSERT INTO channel_members (channel_id, username, is_admin) VALUES (?, ?, ?)',
                          (channel_id, created_by, True))
                conn.commit()
                return channel_id
            except Exception as e:
                print(f"Error creating channel: {e}")
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

    def update_channel_description(channel_name, description, username):
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
                    c.execute('UPDATE channels SET description = ? WHERE name = ?', (description, channel_name))
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

    def set_channel_admin(channel_name, target_user, is_admin, current_user):
        with sqlite3.connect('messenger.db') as conn:
            c = conn.cursor()
            try:
                # Проверяем права администратора (только создатель может назначать админов)
                c.execute('''
                    SELECT created_by FROM channels WHERE name = ?
                ''', (channel_name,))
                
                row = c.fetchone()
                if not row or row[0] != current_user:
                    return False, "Только создатель канала может назначать администраторов"
                
                channel_id = c.execute('SELECT id FROM channels WHERE name = ?', (channel_name,)).fetchone()[0]
                
                # Проверяем, существует ли пользователь в канале
                c.execute('SELECT 1 FROM channel_members WHERE channel_id = ? AND username = ?', (channel_id, target_user))
                if not c.fetchone():
                    return False, "Пользователь не найден в канале"
                
                # Обновляем права администратора
                c.execute('UPDATE channel_members SET is_admin = ? WHERE channel_id = ? AND username = ?',
                          (is_admin, channel_id, target_user))
                conn.commit()
                
                action = "назначен" if is_admin else "снят"
                return True, f"Пользователь {action} администратором"
            except Exception as e:
                return False, f"Ошибка: {str(e)}"

    # Модифицированная функция получения информации о канале
    def get_channel_info(channel_name):
        with sqlite3.connect('messenger.db') as conn:
            c = conn.cursor()
            c.execute('''
                SELECT id, name, display_name, description, created_by, is_private, allow_messages, avatar_path, avatar_color 
                FROM channels WHERE name = ?
            ''', (channel_name,))
            row = c.fetchone()
            if row:
                return {
                    'id': row[0],
                    'name': row[1],
                    'display_name': row[2],
                    'description': row[3],
                    'created_by': row[4],
                    'is_private': row[5],
                    'allow_messages': row[6],
                    'avatar_path': row[7],
                    'avatar_color': row[8]
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

    # Модифицированная функция получения каналов пользователя с аватарками
    def get_user_channels(username):
        with sqlite3.connect('messenger.db') as conn:
            c = conn.cursor()
            c.execute('''
                SELECT c.name, c.display_name, c.description, c.is_private, c.allow_messages, c.created_by, c.avatar_path, c.avatar_color
                FROM channels c
                JOIN channel_members cm ON c.id = cm.channel_id
                WHERE cm.username = ?
                ORDER BY c.name
            ''', (username,))
            channels = []
            for row in c.fetchall():
                channels.append({
                    'name': row[0],
                    'display_name': row[1],
                    'description': row[2],
                    'is_private': row[3],
                    'allow_messages': row[4],
                    'created_by': row[5],
                    'avatar_path': row[6],
                    'avatar_color': row[7]
                })
            return channels

    # Новая функция для загрузки аватарки канала
    @app.route('/upload_channel_avatar', methods=['POST'])
    def upload_channel_avatar_handler():
        if 'username' not in session: 
            return jsonify({'success': False, 'error': 'Не авторизован'})
        
        channel_name = request.form.get('channel_name')
        if not channel_name:
            return jsonify({'success': False, 'error': 'Не указан канал'})
        
        # Проверяем права администратора
        channel_info = get_channel_info(channel_name)
        if not channel_info or channel_info['created_by'] != session['username']:
            return jsonify({'success': False, 'error': 'Нет прав для изменения канала'})
        
        if 'avatar' in request.files:
            file = request.files['avatar']
            path, filename = save_uploaded_file(file, app.config['CHANNEL_AVATAR_FOLDER'])
        elif 'avatar_base64' in request.form:
            base64_data = request.form['avatar_base64']
            path, filename = save_base64_file(base64_data, app.config['CHANNEL_AVATAR_FOLDER'], 'png')
        else:
            return jsonify({'success': False, 'error': 'Файл не найден'})
        
        if path:
            with sqlite3.connect('messenger.db') as conn:
                c = conn.cursor()
                c.execute('UPDATE channels SET avatar_path = ? WHERE name = ?', (path, channel_name))
                conn.commit()
            return jsonify({'success': True, 'path': path})
        return jsonify({'success': False, 'error': 'Неверный формат файла'})

    @app.route('/delete_channel_avatar', methods=['POST'])
    def delete_channel_avatar_handler():
        if 'username' not in session: 
            return jsonify({'success': False, 'error': 'Не авторизован'})
        
        channel_name = request.json.get('channel_name')
        if not channel_name:
            return jsonify({'success': False, 'error': 'Не указан канал'})
        
        # Проверяем права администратора
        channel_info = get_channel_info(channel_name)
        if not channel_info or channel_info['created_by'] != session['username']:
            return jsonify({'success': False, 'error': 'Нет прав для изменения канала'})
        
        with sqlite3.connect('messenger.db') as conn:
            c = conn.cursor()
            c.execute('UPDATE channels SET avatar_path = NULL WHERE name = ?', (channel_name,))
            conn.commit()
        return jsonify({'success': True})

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

    # Модифицированный обработчик создания канала
    @app.route('/create_channel', methods=['POST'])
    def create_channel_handler():
        if 'username' not in session: 
            return jsonify({'success': False, 'error': 'Не авторизован'})
        
        name = request.json.get('name', '').strip()
        display_name = request.json.get('display_name', '').strip()
        description = request.json.get('description', '').strip()
        is_private = request.json.get('is_private', False)
        avatar_color = request.json.get('avatar_color')
        
        if not name or len(name) < 2:
            return jsonify({'success': False, 'error': 'Название канала должно быть не менее 2 символов'})
        
        if not display_name:
            display_name = name.capitalize()
        
        channel_id = create_channel(name, display_name, description, session['username'], is_private, avatar_color=avatar_color)
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

    @app.route('/update_channel_description', methods=['POST'])
    def update_channel_description_handler():
        if 'username' not in session: 
            return jsonify({'success': False, 'error': 'Не авторизован'})
        
        channel_name = request.json.get('channel_name')
        description = request.json.get('description', '').strip()
        
        if update_channel_description(channel_name, description, session['username']):
            return jsonify({'success': True})
        return jsonify({'success': False, 'error': 'Не удалось обновить описание канала или нет прав'})

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

    @app.route('/set_channel_admin', methods=['POST'])
    def set_channel_admin_handler():
        if 'username' not in session: 
            return jsonify({'success': False, 'error': 'Не авторизован'})
        
        channel_name = request.json.get('channel_name')
        target_user = request.json.get('username', '').strip()
        is_admin = request.json.get('is_admin', False)
        
        if not channel_name or not target_user:
            return jsonify({'success': False, 'error': 'Не указан канал или пользователь'})
        
        success, message = set_channel_admin(channel_name, target_user, is_admin, session['username'])
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

    @app.route('/upload_file', methods=['POST'])
    def upload_file_handler():
        if 'username' not in session:
            return jsonify({'success': False, 'error': 'Не авторизован'})
        
        if 'file' not in request.files:
            return jsonify({'success': False, 'error': 'Файл не найден'})
        
        file = request.files['file']
        if not file or file.filename == '':
            return jsonify({'success': False, 'error': 'Файл не выбран'})
        
        path, filename = save_uploaded_file(file, app.config['UPLOAD_FOLDER'])
        if path:
            return jsonify({
                'success': True, 
                'path': path,
                'filename': filename,
                'file_type': 'image' if filename.lower().endswith(('.png', '.jpg', '.jpeg', '.gif', '.webp')) else 'video'
            })
        return jsonify({'success': False, 'error': 'Неверный формат файла'})

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

    @app.route('/static/<path:filename>')
    def static_files(filename):
        return send_from_directory('static', filename)

    @app.route('/create_docs_folder', methods=['POST'])
    def create_docs_folder():
        try:
            docs_folder = 'static/docs'
            os.makedirs(docs_folder, exist_ok=True)
            
            terms_file = os.path.join(docs_folder, 'terms_of_use.pdf')
            if not os.path.exists(terms_file):
                with open(terms_file, 'w', encoding='utf-8') as f:
                    f.write('Tandau Messenger - Условия использования\n\n')
                    f.write('Это демонстрационный файл. В реальном приложении здесь был бы PDF документ.\n')
                
            privacy_file = os.path.join(docs_folder, 'privacy_policy.pdf')
            if not os.path.exists(privacy_file):
                with open(privacy_file, 'w', encoding='utf-8') as f:
                    f.write('Tandau Messenger - Политика конфиденциальности\n\n')
                    f.write('Это демонстрационный файл. В реальном приложении здесь был бы PDF документ.\n')
            
            return jsonify({'success': True, 'message': 'Documents folder created'})
        except Exception as e:
            return jsonify({'success': False, 'error': str(e)})

    @app.route('/')
    def index():
        if 'username' in session: 
            return redirect('/chat')
        
        # ... (остальной код страницы входа остается без изменений) ...
        # Для экономии места не дублирую весь HTML код страницы входа
        
        return '''
        <!DOCTYPE html>
        <html lang="ru">
        <head>
            <meta charset="UTF-8">
            <meta name="viewport" content="width=device-width, initial-scale=1.0">
            <title>Tandau Messenger</title>
            <link rel="stylesheet" href="https://cdnjs.cloudflare.com/ajax/libs/font-awesome/6.5.0/css/all.min.css">
            <style>
                /* Стили страницы входа остаются без изменений */
                /* ... */
            </style>
        </head>
        <body>
            <!-- HTML код страницы входа остается без изменений -->
            <!-- ... -->
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
            -webkit-tap-highlight-color: transparent;
        }}
        
        body {{
            font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif;
            background: var(--bg);
            color: var(--text);
            height: 100vh;
            overflow: hidden;
            touch-action: manipulation;
        }}
        
        .app-container {{
            display: flex;
            height: 100vh;
            position: relative;
        }}
        
        .sidebar {{
            width: 100%;
            background: var(--input);
            display: flex;
            flex-direction: column;
            position: absolute;
            top: 0;
            left: 0;
            bottom: 0;
            z-index: 1000;
            transition: transform 0.3s cubic-bezier(0.4, 0, 0.2, 1);
        }}
        
        .sidebar.hidden {{
            transform: translateX(-100%);
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
            position: relative;
        }}
        
        .menu-toggle {{
            position: absolute;
            left: 20px;
            background: none;
            border: none;
            color: white;
            font-size: 1.2rem;
            cursor: pointer;
            display: none;
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
            -webkit-overflow-scrolling: touch;
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
            user-select: none;
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
        
        .channel-avatar {{
            width: 32px;
            height: 32px;
            border-radius: 8px;
            display: flex;
            align-items: center;
            justify-content: center;
            font-size: 14px;
            font-weight: bold;
            color: white;
            background-size: cover;
            background-position: center;
            flex-shrink: 0;
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
        
        .chat-area {{
            flex: 1;
            display: flex;
            flex-direction: column;
            position: absolute;
            top: 0;
            left: 0;
            right: 0;
            bottom: 0;
            background: #cfe7ff;
            z-index: 900;
            transform: translateX(100%);
            transition: transform 0.3s cubic-bezier(0.4, 0, 0.2, 1);
        }}
        
        .chat-area.active {{
            transform: translateX(0);
        }}
        
        .chat-header {{
            padding: 15px 20px;
            background: var(--input);
            border-bottom: 1px solid var(--border);
            font-weight: 600;
            display: flex;
            align-items: center;
            gap: 10px;
            position: relative;
        }}
        
        .back-btn {{
            background: none;
            border: none;
            color: var(--text);
            cursor: pointer;
            font-size: 1.2rem;
            padding: 5px;
            margin-right: 5px;
            display: none;
        }}
        
        .channel-header-avatar {{
            width: 36px;
            height: 36px;
            border-radius: 8px;
            display: flex;
            align-items: center;
            justify-content: center;
            font-weight: bold;
            color: white;
            background-size: cover;
            background-position: center;
            cursor: pointer;
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
            -webkit-overflow-scrolling: touch;
        }}
        
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
            max-width: 85%;
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
            max-width: 100%;
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
            overflow-x: auto;
            -webkit-overflow-scrolling: touch;
        }}
        
        .category-filter-btn {{
            padding: 6px 12px;
            background: var(--bg);
            border: 1px solid var(--border);
            border-radius: 20px;
            cursor: pointer;
            font-size: 0.9rem;
            transition: all 0.2s ease;
            white-space: nowrap;
        }}
        
        .category-filter-btn.active {{
            background: var(--accent);
            color: white;
            border-color: var(--accent);
        }}
        
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
            -webkit-overflow-scrolling: touch;
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
        
        .input-area {{
            background: rgba(255, 255, 255, 0.85);
            backdrop-filter: blur(10px);
            -webkit-backdrop-filter: blur(10px);
            border-top: 1px solid rgba(255, 255, 255, 0.2);
            padding: 15px 20px;
            box-shadow: 0 -2px 20px rgba(0, 0, 0, 0.1);
        }}
        
        [data-theme="dark"] .input-area {{
            background: rgba(45, 45, 45, 0.85);
            border-top: 1px solid rgba(255, 255, 255, 0.1);
        }}
        
        .input-row {{
            display: flex;
            gap: 10px;
            align-items: flex-end;
        }}
        
        .attachment-btn {{
            background: rgba(255, 255, 255, 0.7);
            border: 1px solid rgba(255, 255, 255, 0.3);
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
            backdrop-filter: blur(5px);
            -webkit-backdrop-filter: blur(5px);
            transition: all 0.2s ease;
        }}
        
        .attachment-btn:hover {{
            background: rgba(255, 255, 255, 0.9);
            transform: translateY(-2px);
            box-shadow: 0 4px 12px rgba(0,0,0,0.1);
        }}
        
        [data-theme="dark"] .attachment-btn {{
            background: rgba(255, 255, 255, 0.1);
            border: 1px solid rgba(255, 255, 255, 0.2);
        }}
        
        [data-theme="dark"] .attachment-btn:hover {{
            background: rgba(255, 255, 255, 0.2);
        }}
        
        .msg-input {{
            flex: 1;
            padding: 12px 16px;
            border: 1px solid rgba(255, 255, 255, 0.3);
            border-radius: 25px;
            background: rgba(255, 255, 255, 0.7);
            backdrop-filter: blur(5px);
            -webkit-backdrop-filter: blur(5px);
            color: var(--text);
            font-size: 1rem;
            resize: none;
            max-height: 120px;
            min-height: 44px;
            line-height: 1.4;
            transition: all 0.2s ease;
            box-shadow: 0 2px 8px rgba(0,0,0,0.05);
        }}
        
        .msg-input:focus {{
            outline: none;
            border-color: var(--accent);
            background: rgba(255, 255, 255, 0.9);
            box-shadow: 0 4px 12px rgba(102, 126, 234, 0.2);
        }}
        
        [data-theme="dark"] .msg-input {{
            background: rgba(255, 255, 255, 0.1);
            border: 1px solid rgba(255, 255, 255, 0.2);
            color: white;
        }}
        
        [data-theme="dark"] .msg-input:focus {{
            background: rgba(255, 255, 255, 0.15);
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
            box-shadow: 0 4px 12px rgba(102, 126, 234, 0.3);
        }}
        
        .send-btn:hover {{
            background: var(--primary-dark);
            transform: translateY(-2px);
            box-shadow: 0 6px 16px rgba(102, 126, 234, 0.4);
        }}
        
        .send-btn:active {{
            transform: translateY(0);
        }}
        
        .file-preview {{
            margin-top: 10px;
            padding: 10px;
            background: rgba(255, 255, 255, 0.6);
            backdrop-filter: blur(5px);
            border-radius: 12px;
            border: 1px dashed rgba(255, 255, 255, 0.4);
        }}
        
        .file-preview img, .file-preview video {{
            max-width: 200px;
            max-height: 150px;
            border-radius: 8px;
        }}
        
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
            padding: 20px;
        }}
        
        .modal-content {{
            background: var(--input);
            padding: 25px;
            border-radius: 15px;
            width: 100%;
            max-width: 500px;
            max-height: 90vh;
            overflow-y: auto;
            -webkit-overflow-scrolling: touch;
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
            font-size: 16px;
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
            user-select: none;
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
        
        .channel-avatar-preview {{
            width: 120px;
            height: 120px;
            border-radius: 20px;
            margin: 0 auto 15px;
            background: var(--accent);
            background-size: cover;
            background-position: center;
            cursor: pointer;
            border: 3px solid var(--accent);
        }}
        
        .avatar-colors {{
            display: flex;
            gap: 10px;
            flex-wrap: wrap;
            margin-top: 15px;
            justify-content: center;
        }}
        
        .color-option {{
            width: 40px;
            height: 40px;
            border-radius: 8px;
            cursor: pointer;
            border: 3px solid transparent;
            transition: all 0.2s ease;
        }}
        
        .color-option:hover {{
            transform: scale(1.1);
        }}
        
        .color-option.selected {{
            border-color: var(--text);
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
            margin: 20px 10px 10px 10px;
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
        
        /* Новые стили для меню канала */
        .channel-menu {{
            display: none;
            position: fixed;
            top: 0;
            left: 0;
            width: 100%;
            height: 100%;
            background: rgba(0,0,0,0.7);
            z-index: 3000;
            align-items: center;
            justify-content: center;
            padding: 20px;
            backdrop-filter: blur(5px);
        }}
        
        .channel-menu-content {{
            background: var(--input);
            border-radius: 20px;
            width: 100%;
            max-width: 600px;
            max-height: 80vh;
            overflow: hidden;
            box-shadow: 0 20px 60px rgba(0,0,0,0.3);
        }}
        
        .channel-menu-header {{
            padding: 20px 25px;
            background: linear-gradient(135deg, #667eea 0%, #764ba2 100%);
            color: white;
            display: flex;
            justify-content: space-between;
            align-items: center;
        }}
        
        .channel-menu-header h3 {{
            margin: 0;
            font-size: 1.3rem;
            font-weight: 600;
        }}
        
        .close-channel-menu {{
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
        }}
        
        .close-channel-menu:hover {{
            background: rgba(255, 255, 255, 0.3);
            transform: rotate(90deg);
        }}
        
        .channel-menu-tabs {{
            display: flex;
            background: var(--bg);
            border-bottom: 1px solid var(--border);
        }}
        
        .channel-menu-tab {{
            flex: 1;
            padding: 15px;
            text-align: center;
            font-weight: 500;
            cursor: pointer;
            transition: all 0.2s ease;
            border-bottom: 3px solid transparent;
        }}
        
        .channel-menu-tab:hover {{
            background: var(--input);
        }}
        
        .channel-menu-tab.active {{
            border-bottom-color: var(--accent);
            color: var(--accent);
        }}
        
        .channel-menu-body {{
            padding: 25px;
            max-height: 60vh;
            overflow-y: auto;
        }}
        
        .channel-menu-section {{
            display: none;
            animation: fadeIn 0.3s ease;
        }}
        
        .channel-menu-section.active {{
            display: block;
        }}
        
        .channel-info-display {{
            display: flex;
            align-items: center;
            gap: 20px;
            margin-bottom: 25px;
            padding: 20px;
            background: var(--bg);
            border-radius: 15px;
        }}
        
        .channel-info-avatar {{
            width: 80px;
            height: 80px;
            border-radius: 20px;
            background-size: cover;
            background-position: center;
            flex-shrink: 0;
            border: 3px solid var(--accent);
        }}
        
        .channel-info-text {{
            flex: 1;
        }}
        
        .channel-info-text h4 {{
            margin: 0 0 8px 0;
            font-size: 1.4rem;
            font-weight: 600;
        }}
        
        .channel-info-text p {{
            margin: 0 0 10px 0;
            color: var(--text);
            opacity: 0.8;
            line-height: 1.5;
        }}
        
        .channel-info-meta {{
            font-size: 0.9rem;
            color: #666;
        }}
        
        .channel-form-group {{
            margin-bottom: 20px;
        }}
        
        .channel-form-label {{
            display: block;
            margin-bottom: 8px;
            font-weight: 500;
            color: var(--text);
        }}
        
        .channel-form-input {{
            width: 100%;
            padding: 12px 15px;
            border: 1px solid var(--border);
            border-radius: 10px;
            background: var(--bg);
            color: var(--text);
            font-size: 1rem;
        }}
        
        .channel-form-input:focus {{
            outline: none;
            border-color: var(--accent);
        }}
        
        .channel-form-textarea {{
            width: 100%;
            padding: 12px 15px;
            border: 1px solid var(--border);
            border-radius: 10px;
            background: var(--bg);
            color: var(--text);
            font-size: 1rem;
            resize: vertical;
            min-height: 100px;
        }}
        
        .channel-form-actions {{
            display: flex;
            gap: 10px;
            margin-top: 25px;
        }}
        
        .channel-member-item {{
            display: flex;
            align-items: center;
            justify-content: space-between;
            padding: 15px;
            border-bottom: 1px solid var(--border);
        }}
        
        .channel-member-info {{
            display: flex;
            align-items: center;
            gap: 15px;
        }}
        
        .channel-member-avatar {{
            width: 40px;
            height: 40px;
            border-radius: 50%;
            background-size: cover;
            background-position: center;
            display: flex;
            align-items: center;
            justify-content: center;
            font-weight: bold;
            font-size: 0.9rem;
            color: white;
        }}
        
        .channel-member-details {{
            flex: 1;
        }}
        
        .channel-member-name {{
            font-weight: 500;
            margin-bottom: 4px;
        }}
        
        .channel-member-role {{
            font-size: 0.8rem;
            color: #666;
        }}
        
        .channel-member-admin {{
            color: var(--accent);
            font-weight: 600;
        }}
        
        .channel-member-actions {{
            display: flex;
            gap: 8px;
        }}
        
        .channel-member-btn {{
            padding: 6px 12px;
            border: none;
            border-radius: 6px;
            cursor: pointer;
            font-size: 0.85rem;
            transition: all 0.2s ease;
        }}
        
        .channel-member-btn.admin {{
            background: var(--accent);
            color: white;
        }}
        
        .channel-member-btn.remove {{
            background: #dc3545;
            color: white;
        }}
        
        .channel-member-btn.add {{
            background: #28a745;
            color: white;
        }}
        
        .no-members {{
            text-align: center;
            padding: 40px 20px;
            color: #666;
        }}
        
        .no-members i {{
            font-size: 3rem;
            margin-bottom: 15px;
            color: #ccc;
        }}
        
        ::-webkit-scrollbar {{
            width: 6px;
        }}
        
        ::-webkit-scrollbar-track {{
            background: transparent;
        }}
        
        ::-webkit-scrollbar-thumb {{
            background: #ccc;
            border-radius: 3px;
        }}
        
        [data-theme="dark"] ::-webkit-scrollbar-thumb {{
            background: #555;
        }}
        
        @keyframes fadeIn {{
            from {{ opacity: 0; transform: translateY(10px); }}
            to {{ opacity: 1; transform: translateY(0); }}
        }}
        
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
        
        @media (max-width: 768px) {{
            .menu-toggle {{
                display: block;
            }}
            
            .back-btn {{
                display: block;
            }}
            
            .sidebar-header {{
                padding: 15px 20px;
            }}
            
            .app-title {{
                font-size: 1.5rem;
            }}
            
            .logo-placeholder {{
                width: 35px;
                height: 35px;
                font-size: 18px;
            }}
            
            .user-info {{
                padding: 15px;
            }}
            
            .avatar {{
                width: 40px;
                height: 40px;
                font-size: 1rem;
            }}
            
            .favorites-grid {{
                grid-template-columns: 1fr;
                gap: 10px;
                padding: 15px;
            }}
            
            .message-content {{
                max-width: 90%;
            }}
            
            .modal-content {{
                padding: 20px;
                margin: 10px;
            }}
            
            .channel-menu-content {{
                max-height: 90vh;
                margin: 10px;
            }}
            
            .channel-menu-body {{
                padding: 20px;
                max-height: 70vh;
            }}
            
            .channel-info-display {{
                flex-direction: column;
                text-align: center;
                gap: 15px;
            }}
            
            .categories-filter {{
                padding: 10px;
                gap: 8px;
            }}
            
            .category-filter-btn {{
                padding: 5px 10px;
                font-size: 0.8rem;
            }}
            
            .input-area {{
                position: fixed;
                bottom: 0;
                left: 0;
                right: 0;
                padding: 12px 15px;
                background: rgba(255, 255, 255, 0.9);
                backdrop-filter: blur(15px);
                -webkit-backdrop-filter: blur(15px);
                border-top: 1px solid rgba(255, 255, 255, 0.3);
                z-index: 1000;
                box-shadow: 0 -2px 20px rgba(0, 0, 0, 0.15);
            }}
            
            [data-theme="dark"] .input-area {{
                background: rgba(45, 45, 45, 0.9);
                border-top: 1px solid rgba(255, 255, 255, 0.1);
            }}
            
            .msg-input {{
                padding: 12px 14px;
                font-size: 16px;
                min-height: 44px;
                background: rgba(255, 255, 255, 0.8);
            }}
            
            [data-theme="dark"] .msg-input {{
                background: rgba(255, 255, 255, 0.15);
            }}
            
            .attachment-btn, .send-btn {{
                width: 44px;
                height: 44px;
                flex-shrink: 0;
            }}
            
            .messages {{
                padding-bottom: 80px !important;
                height: calc(100vh - 140px) !important;
            }}
            
            .favorites-grid {{
                padding-bottom: 80px;
            }}
            
            .chat-header {{
                padding: 12px 15px;
                min-height: 56px;
            }}
            
            .chat-area.active {{
                position: fixed;
                top: 0;
                left: 0;
                right: 0;
                bottom: 0;
                z-index: 1000;
                background: #cfe7ff;
            }}
            
            .logout-btn {{
                margin-top: 30px;
                margin-bottom: 20px;
            }}
        }}
        
        @media (min-width: 769px) {{
            .sidebar {{
                width: var(--sidebar-width);
                position: relative;
                transform: none !important;
            }}
            
            .chat-area {{
                position: relative;
                transform: none !important;
            }}
            
            .menu-toggle {{
                display: none;
            }}
            
            .back-btn {{
                display: none;
            }}
            
            .logout-btn {{
                margin-top: 30px;
                margin-bottom: 20px;
            }}
        }}
        
        .no-select {{
            -webkit-touch-callout: none;
            -webkit-user-select: none;
            user-select: none;
        }}
        
        .user-avatar {{
            width: 30px;
            height: 30px;
            border-radius: 50%;
            display: flex;
            align-items: center;
            justify-content: center;
            font-weight: bold;
            font-size: 0.8rem;
            background-size: cover;
            background-position: center;
            flex-shrink: 0;
            color: white;
        }}
        
        .user-avatar.online {{
            position: relative;
        }}
        
        .user-avatar.online::after {{
            content: '';
            position: absolute;
            bottom: 0;
            right: 0;
            width: 8px;
            height: 8px;
            background: #10b981;
            border-radius: 50%;
            border: 2px solid var(--input);
        }}
    </style>
</head>
<body>
    <div class="app-container">
        <div class="sidebar" id="sidebar">
            <div class="sidebar-header">
                <button class="menu-toggle" onclick="toggleSidebar()">
                    <i class="fas fa-bars"></i>
                </button>
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
                <div id="channels"></div>
                
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
        
        <div class="chat-area" id="chat-area">
            <div class="chat-header">
                <button class="back-btn" onclick="goBack()">
                    <i class="fas fa-arrow-left"></i>
                </button>
                <div class="channel-header-avatar" id="channel-header-avatar" onclick="openChannelMenu()"></div>
                <div style="flex: 1;">
                    <div id="chat-title">Избранное</div>
                    <div id="channel-description" style="font-size: 0.8rem; color: #666; margin-top: 2px;"></div>
                </div>
                <div class="channel-actions" id="channel-actions" style="display: none;">
                    <button class="channel-btn" onclick="openChannelSettings()">
                        <i class="fas fa-cog"></i>
                    </button>
                </div>
            </div>
            
            <div class="categories-filter" id="categories-filter" style="display: none;">
                <button class="category-filter-btn active" onclick="filterFavorites('all')">Все</button>
            </div>
            
            <div class="messages" id="messages">
                <div id="favorites-grid" class="favorites-grid"></div>
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

    <!-- Меню канала -->
    <div class="channel-menu" id="channel-menu">
        <div class="channel-menu-content">
            <div class="channel-menu-header">
                <h3 id="channel-menu-title">Настройки канала</h3>
                <button class="close-channel-menu" onclick="closeChannelMenu()">
                    <i class="fas fa-times"></i>
                </button>
            </div>
            
            <div class="channel-menu-tabs">
                <div class="channel-menu-tab active" onclick="showChannelMenuTab('info')">
                    <i class="fas fa-info-circle"></i> Информация
                </div>
                <div class="channel-menu-tab" onclick="showChannelMenuTab('members')">
                    <i class="fas fa-users"></i> Участники
                </div>
            </div>
            
            <div class="channel-menu-body">
                <!-- Вкладка информации -->
                <div class="channel-menu-section active" id="channel-info-tab">
                    <div class="channel-info-display" id="channel-info-display">
                        <div class="channel-info-avatar" id="channel-menu-avatar"></div>
                        <div class="channel-info-text">
                            <h4 id="channel-menu-name">Название канала</h4>
                            <p id="channel-menu-description">Описание канала</p>
                            <div class="channel-info-meta">
                                <span id="channel-menu-members">0 участников</span>
                                • <span id="channel-menu-created">Создан: </span>
                            </div>
                        </div>
                    </div>
                    
                    <div class="channel-form-group">
                        <label class="channel-form-label">Название канала</label>
                        <input type="text" class="channel-form-input" id="channel-menu-name-input" placeholder="Введите новое название">
                        <button class="btn btn-primary" style="margin-top: 10px; width: 100%;" onclick="updateChannelName()">
                            <i class="fas fa-save"></i> Сохранить название
                        </button>
                    </div>
                    
                    <div class="channel-form-group">
                        <label class="channel-form-label">Описание канала</label>
                        <textarea class="channel-form-textarea" id="channel-menu-description-input" placeholder="Введите описание канала"></textarea>
                        <button class="btn btn-primary" style="margin-top: 10px; width: 100%;" onclick="updateChannelDescription()">
                            <i class="fas fa-save"></i> Сохранить описание
                        </button>
                    </div>
                    
                    <div class="channel-form-group">
                        <label class="channel-form-label">Аватарка канала</label>
                        <div class="avatar-upload">
                            <div class="channel-avatar-preview" id="channel-menu-avatar-preview" onclick="document.getElementById('channel-menu-avatar-input').click()"></div>
                            <input type="file" id="channel-menu-avatar-input" accept="image/*" style="display:none" onchange="previewChannelMenuAvatar(this)">
                            <div class="avatar-colors">
                                <div class="color-option selected" style="background-color: #667eea;" onclick="selectChannelMenuColor('#667eea')"></div>
                                <div class="color-option" style="background-color: #764ba2;" onclick="selectChannelMenuColor('#764ba2')"></div>
                                <div class="color-option" style="background-color: #10b981;" onclick="selectChannelMenuColor('#10b981')"></div>
                                <div class="color-option" style="background-color: #f59e0b;" onclick="selectChannelMenuColor('#f59e0b')"></div>
                                <div class="color-option" style="background-color: #ef4444;" onclick="selectChannelMenuColor('#ef4444')"></div>
                                <div class="color-option" style="background-color: #3b82f6;" onclick="selectChannelMenuColor('#3b82f6')"></div>
                            </div>
                            <div class="channel-form-actions">
                                <button class="btn btn-primary" onclick="uploadChannelMenuAvatar()" style="flex: 1;">
                                    <i class="fas fa-upload"></i> Загрузить
                                </button>
                                <button class="btn btn-secondary" onclick="removeChannelMenuAvatar()" style="flex: 1;">
                                    <i class="fas fa-trash"></i> Удалить
                                </button>
                            </div>
                        </div>
                    </div>
                </div>
                
                <!-- Вкладка участников -->
                <div class="channel-menu-section" id="channel-members-tab">
                    <div class="channel-form-group">
                        <label class="channel-form-label">Добавить участника</label>
                        <div style="display: flex; gap: 10px;">
                            <select class="channel-form-input" id="channel-add-user-select" style="flex: 1;">
                                <option value="">Выберите пользователя...</option>
                            </select>
                            <button class="btn btn-primary" onclick="addChannelMember()">
                                <i class="fas fa-user-plus"></i> Добавить
                            </button>
                        </div>
                    </div>
                    
                    <div class="channel-form-group">
                        <label class="channel-form-label">Участники канала</label>
                        <div class="member-list" id="channel-members-list">
                            <!-- Список участников будет загружен здесь -->
                        </div>
                    </div>
                </div>
            </div>
        </div>
    </div>

    <!-- Остальные модальные окна -->
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
                <div class="avatar-colors" style="margin-top: 15px;">
                    <div class="color-option selected" style="background-color: #667eea;" onclick="selectCreateChannelColor('#667eea')"></div>
                    <div class="color-option" style="background-color: #764ba2;" onclick="selectCreateChannelColor('#764ba2')"></div>
                    <div class="color-option" style="background-color: #10b981;" onclick="selectCreateChannelColor('#10b981')"></div>
                    <div class="color-option" style="background-color: #f59e0b;" onclick="selectCreateChannelColor('#f59e0b')"></div>
                    <div class="color-option" style="background-color: #ef4444;" onclick="selectCreateChannelColor('#ef4444')"></div>
                    <div class="color-option" style="background-color: #3b82f6;" onclick="selectCreateChannelColor('#3b82f6')"></div>
                </div>
            </div>
            <div style="display: flex; gap: 10px;">
                <button class="btn btn-primary" onclick="createChannel()">Создать</button>
                <button class="btn btn-secondary" onclick="closeCreateChannelModal()">Отмена</button>
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
        let isMobile = window.innerWidth <= 768;
        let selectedChannelColor = '#667eea';
        let createChannelColor = '#667eea';
        let channelMenuChannel = null;
        let channelMenuChannelInfo = null;

        function checkMobile() {{
            isMobile = window.innerWidth <= 768;
            if (!isMobile) {{
                document.getElementById('sidebar').classList.remove('hidden');
                document.getElementById('chat-area').classList.add('active');
            }}
        }}

        function toggleSidebar() {{
            const sidebar = document.getElementById('sidebar');
            sidebar.classList.toggle('hidden');
        }}

        function goBack() {{
            if (isMobile) {{
                document.getElementById('sidebar').classList.remove('hidden');
                document.getElementById('chat-area').classList.remove('active');
            }}
        }}

        window.onload = function() {{
            checkMobile();
            loadUserAvatar();
            loadUserChannels();
            loadUsers();
            loadPersonalChats();
            loadFavoritesCategories();
            loadFavorites();
            
            if (isMobile) {{
                document.getElementById('chat-area').classList.remove('active');
            }} else {{
                openFavorites();
            }}
            
            window.addEventListener('resize', checkMobile);
            setupMobileKeyboard();
        }};

        function setupMobileKeyboard() {{
            if (!isMobile) return;
            
            const msgInput = document.getElementById('msg-input');
            const messagesContainer = document.getElementById('messages');
            
            msgInput.addEventListener('focus', function() {{
                setTimeout(() => {{
                    if (messagesContainer.scrollHeight > messagesContainer.clientHeight) {{
                        messagesContainer.scrollTop = messagesContainer.scrollHeight;
                    }}
                }}, 300);
            }});
            
            msgInput.addEventListener('blur', function() {{
                setTimeout(() => {{
                    messagesContainer.scrollTop = messagesContainer.scrollHeight;
                }}, 100);
            }});
        }}

        // Меню канала
        function openChannelMenu() {{
            if (!currentChannel) return;
            
            channelMenuChannel = currentChannel;
            document.getElementById('channel-menu').style.display = 'flex';
            loadChannelMenuInfo();
        }}

        function closeChannelMenu() {{
            document.getElementById('channel-menu').style.display = 'none';
            channelMenuChannel = null;
            channelMenuChannelInfo = null;
        }}

        function showChannelMenuTab(tabName) {{
            document.querySelectorAll('.channel-menu-tab').forEach(tab => {{
                tab.classList.remove('active');
            }});
            
            document.querySelectorAll('.channel-menu-section').forEach(section => {{
                section.classList.remove('active');
            }});
            
            event.currentTarget.classList.add('active');
            document.getElementById(`channel-${{tabName}}-tab`).classList.add('active');
            
            if (tabName === 'members') {{
                loadChannelMembers();
            }}
        }}

        async function loadChannelMenuInfo() {{
            try {{
                const response = await fetch(`/channel_info/${{encodeURIComponent(channelMenuChannel)}}`);
                const data = await response.json();
                
                if (data.success) {{
                    channelMenuChannelInfo = data.data;
                    
                    // Обновляем информацию в меню
                    document.getElementById('channel-menu-title').textContent = `Настройки: ${{data.data.display_name}}`;
                    document.getElementById('channel-menu-name').textContent = data.data.display_name;
                    document.getElementById('channel-menu-description').textContent = data.data.description || 'Нет описания';
                    document.getElementById('channel-menu-members').textContent = `${{data.data.members.length}} участников`;
                    document.getElementById('channel-menu-created').textContent = `Создатель: ${{data.data.created_by}}`;
                    
                    // Аватарка
                    const avatar = document.getElementById('channel-menu-avatar');
                    const preview = document.getElementById('channel-menu-avatar-preview');
                    
                    if (data.data.avatar_path) {{
                        avatar.style.backgroundImage = `url(${{data.data.avatar_path}})`;
                        preview.style.backgroundImage = `url(${{data.data.avatar_path}})`;
                        preview.textContent = '';
                    }} else {{
                        avatar.style.backgroundImage = 'none';
                        preview.style.backgroundImage = 'none';
                        avatar.style.backgroundColor = data.data.avatar_color;
                        preview.style.backgroundColor = data.data.avatar_color;
                        preview.textContent = data.data.display_name.slice(0, 2).toUpperCase();
                    }}
                    
                    // Поля ввода
                    document.getElementById('channel-menu-name-input').value = data.data.display_name;
                    document.getElementById('channel-menu-description-input').value = data.data.description || '';
                    
                    // Цвета
                    selectedChannelColor = data.data.avatar_color;
                    updateChannelMenuColorSelection();
                    
                    // Загружаем доступных пользователей
                    loadAvailableUsers();
                }}
            }} catch (error) {{
                console.error('Error loading channel info:', error);
                alert('Ошибка загрузки информации о канале');
            }}
        }}

        function updateChannelMenuColorSelection() {{
            document.querySelectorAll('#channel-menu .color-option').forEach(el => {{
                el.classList.remove('selected');
                if (el.style.backgroundColor === selectedChannelColor) {{
                    el.classList.add('selected');
                }}
            }});
        }}

        function selectChannelMenuColor(color) {{
            selectedChannelColor = color;
            updateChannelMenuColorSelection();
            
            const preview = document.getElementById('channel-menu-avatar-preview');
            preview.style.backgroundImage = 'none';
            preview.style.backgroundColor = color;
            preview.textContent = channelMenuChannelInfo?.display_name?.slice(0, 2).toUpperCase() || 'CH';
        }}

        function previewChannelMenuAvatar(input) {{
            const file = input.files[0];
            if (file) {{
                const reader = new FileReader();
                reader.onload = (e) => {{
                    const preview = document.getElementById('channel-menu-avatar-preview');
                    preview.style.backgroundImage = `url(${{e.target.result}})`;
                    preview.textContent = '';
                }};
                reader.readAsDataURL(file);
            }}
        }}

        async function uploadChannelMenuAvatar() {{
            const fileInput = document.getElementById('channel-menu-avatar-input');
            const file = fileInput.files[0];
            
            if (file) {{
                const formData = new FormData();
                formData.append('avatar', file);
                formData.append('channel_name', channelMenuChannel);
                
                try {{
                    const response = await fetch('/upload_channel_avatar', {{
                        method: 'POST',
                        body: formData
                    }});
                    
                    const data = await response.json();
                    
                    if (data.success) {{
                        loadChannelMenuInfo();
                        loadUserChannels();
                        updateChannelHeaderAvatar();
                        alert('Аватарка канала обновлена!');
                    }} else {{
                        alert(data.error || 'Ошибка загрузки аватарки');
                    }}
                }} catch (error) {{
                    alert('Ошибка соединения');
                }}
            }} else {{
                // Обновляем только цвет
                updateChannelAvatarColor();
            }}
        }}

        async function updateChannelAvatarColor() {{
            try {{
                const response = await fetch('/channel_info/' + channelMenuChannel);
                const data = await response.json();
                
                if (data.success) {{
                    const formData = new FormData();
                    formData.append('channel_name', channelMenuChannel);
                    formData.append('avatar_color', selectedChannelColor);
                    
                    // В реальном приложении здесь должен быть API endpoint для обновления цвета
                    // Для демонстрации просто показываем сообщение
                    alert('Цвет аватарки обновлен!');
                    loadChannelMenuInfo();
                    loadUserChannels();
                    updateChannelHeaderAvatar();
                }}
            }} catch (error) {{
                console.error('Error updating channel color:', error);
            }}
        }}

        async function removeChannelMenuAvatar() {{
            if (!confirm('Удалить аватарку канала?')) return;
            
            try {{
                const response = await fetch('/delete_channel_avatar', {{
                    method: 'POST',
                    headers: {{ 'Content-Type': 'application/json' }},
                    body: JSON.stringify({{ channel_name: channelMenuChannel }})
                }});
                
                const data = await response.json();
                
                if (data.success) {{
                    loadChannelMenuInfo();
                    loadUserChannels();
                    updateChannelHeaderAvatar();
                    alert('Аватарка канала удалена!');
                }}
            }} catch (error) {{
                alert('Ошибка удаления аватарки');
            }}
        }}

        async function updateChannelName() {{
            const newName = document.getElementById('channel-menu-name-input').value.trim();
            
            if (!newName) {{
                alert('Введите новое название');
                return;
            }}
            
            try {{
                const response = await fetch('/rename_channel', {{
                    method: 'POST',
                    headers: {{ 'Content-Type': 'application/json' }},
                    body: JSON.stringify({{
                        channel_name: channelMenuChannel,
                        new_display_name: newName
                    }})
                }});
                
                const data = await response.json();
                
                if (data.success) {{
                    loadChannelMenuInfo();
                    loadUserChannels();
                    updateChannelHeader();
                    alert('Название канала обновлено!');
                }} else {{
                    alert(data.error || 'Ошибка обновления названия');
                }}
            }} catch (error) {{
                alert('Ошибка соединения');
            }}
        }}

        async function updateChannelDescription() {{
            const description = document.getElementById('channel-menu-description-input').value.trim();
            
            try {{
                const response = await fetch('/update_channel_description', {{
                    method: 'POST',
                    headers: {{ 'Content-Type': 'application/json' }},
                    body: JSON.stringify({{
                        channel_name: channelMenuChannel,
                        description: description
                    }})
                }});
                
                const data = await response.json();
                
                if (data.success) {{
                    loadChannelMenuInfo();
                    updateChannelHeader();
                    alert('Описание канала обновлено!');
                }} else {{
                    alert(data.error || 'Ошибка обновления описания');
                }}
            }} catch (error) {{
                alert('Ошибка соединения');
            }}
        }}

        async function loadAvailableUsers() {{
            try {{
                const response = await fetch(`/get_available_users?channel_name=${{encodeURIComponent(channelMenuChannel)}}`);
                const data = await response.json();
                
                if (data.success) {{
                    const select = document.getElementById('channel-add-user-select');
                    select.innerHTML = '<option value="">Выберите пользователя...</option>';
                    
                    data.users.forEach(username => {{
                        const option = document.createElement('option');
                        option.value = username;
                        option.textContent = username;
                        select.appendChild(option);
                    }});
                }}
            }} catch (error) {{
                console.error('Error loading available users:', error);
            }}
        }}

        async function loadChannelMembers() {{
            try {{
                const response = await fetch(`/channel_info/${{encodeURIComponent(channelMenuChannel)}}`);
                const data = await response.json();
                
                if (data.success) {{
                    const membersList = document.getElementById('channel-members-list');
                    membersList.innerHTML = '';
                    
                    if (data.data.members.length === 0) {{
                        membersList.innerHTML = `
                            <div class="no-members">
                                <i class="fas fa-users"></i>
                                <h4>Нет участников</h4>
                                <p>Добавьте участников в канал</p>
                            </div>
                        `;
                    }} else {{
                        data.data.members.forEach(member => {{
                            const isCurrentUser = member.username === user;
                            const isCreator = data.data.created_by === member.username;
                            const canManage = data.data.created_by === user && !isCurrentUser;
                            
                            const memberItem = document.createElement('div');
                            memberItem.className = 'channel-member-item';
                            
                            memberItem.innerHTML = `
                                <div class="channel-member-info">
                                    <div class="channel-member-avatar" style="background-color: ${{member.color}};">
                                        ${{member.avatar ? '' : member.username.slice(0, 2).toUpperCase()}}
                                    </div>
                                    <div class="channel-member-details">
                                        <div class="channel-member-name">
                                            ${{member.username}}
                                            ${{isCreator ? '<span style="color: #f59e0b; margin-left: 5px;">👑</span>' : ''}}
                                        </div>
                                        <div class="channel-member-role ${{member.is_admin ? 'channel-member-admin' : ''}}">
                                            ${{isCreator ? 'Создатель' : (member.is_admin ? 'Администратор' : 'Участник')}}
                                        </div>
                                    </div>
                                </div>
                                ${{canManage ? `
                                    <div class="channel-member-actions">
                                        <button class="channel-member-btn admin" onclick="toggleChannelAdmin('${{member.username}}', ${{!member.is_admin}})">
                                            ${{member.is_admin ? 'Снять админа' : 'Назначить админом'}}
                                        </button>
                                        <button class="channel-member-btn remove" onclick="removeChannelMember('${{member.username}}')">
                                            Удалить
                                        </button>
                                    </div>
                                ` : ''}}
                            `;
                            
                            membersList.appendChild(memberItem);
                            
                            // Устанавливаем аватарку если есть
                            if (member.avatar) {{
                                const avatar = memberItem.querySelector('.channel-member-avatar');
                                avatar.style.backgroundImage = `url(${{member.avatar}})`;
                                avatar.textContent = '';
                            }}
                        }});
                    }}
                }}
            }} catch (error) {{
                console.error('Error loading channel members:', error);
            }}
        }}

        async function addChannelMember() {{
            const select = document.getElementById('channel-add-user-select');
            const selectedUser = select.value;
            
            if (!selectedUser) {{
                alert('Выберите пользователя');
                return;
            }}
            
            try {{
                const response = await fetch('/add_user_to_channel', {{
                    method: 'POST',
                    headers: {{ 'Content-Type': 'application/json' }},
                    body: JSON.stringify({{
                        channel_name: channelMenuChannel,
                        username: selectedUser
                    }})
                }});
                
                const data = await response.json();
                
                if (data.success) {{
                    alert(data.message || 'Пользователь добавлен');
                    loadChannelMembers();
                    loadAvailableUsers();
                    select.value = '';
                }} else {{
                    alert(data.message || 'Ошибка при добавлении пользователя');
                }}
            }} catch (error) {{
                alert('Ошибка соединения');
            }}
        }}

        async function removeChannelMember(username) {{
            if (!confirm(`Удалить пользователя ${{username}} из канала?`)) return;
            
            try {{
                const response = await fetch('/remove_user_from_channel', {{
                    method: 'POST',
                    headers: {{ 'Content-Type': 'application/json' }},
                    body: JSON.stringify({{
                        channel_name: channelMenuChannel,
                        username: username
                    }})
                }});
                
                const data = await response.json();
                
                if (data.success) {{
                    alert(data.message || 'Пользователь удален');
                    loadChannelMembers();
                    loadAvailableUsers();
                }} else {{
                    alert(data.message || 'Ошибка при удалении пользователя');
                }}
            }} catch (error) {{
                alert('Ошибка соединения');
            }}
        }}

        async function toggleChannelAdmin(username, makeAdmin) {{
            const action = makeAdmin ? 'назначить администратором' : 'снять с администратора';
            
            if (!confirm(`${{action.charAt(0).toUpperCase() + action.slice(1)}} пользователя ${{username}}?`)) return;
            
            try {{
                const response = await fetch('/set_channel_admin', {{
                    method: 'POST',
                    headers: {{ 'Content-Type': 'application/json' }},
                    body: JSON.stringify({{
                        channel_name: channelMenuChannel,
                        username: username,
                        is_admin: makeAdmin
                    }})
                }});
                
                const data = await response.json();
                
                if (data.success) {{
                    alert(data.message || 'Права обновлены');
                    loadChannelMembers();
                }} else {{
                    alert(data.message || 'Ошибка обновления прав');
                }}
            }} catch (error) {{
                alert('Ошибка соединения');
            }}
        }}

        // Остальные функции остаются без изменений
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

        function updateChannelHeader() {{
            if (!currentChannel) return;
            
            fetch(`/channel_info/${{encodeURIComponent(currentChannel)}}`)
                .then(r => r.json())
                .then(data => {{
                    if (data.success) {{
                        document.getElementById('chat-title').textContent = data.data.display_name;
                        document.getElementById('channel-description').textContent = data.data.description || '';
                        
                        const headerAvatar = document.getElementById('channel-header-avatar');
                        if (data.data.avatar_path) {{
                            headerAvatar.style.backgroundImage = `url(${{data.data.avatar_path}})`;
                            headerAvatar.textContent = '';
                        }} else {{
                            headerAvatar.style.backgroundImage = 'none';
                            headerAvatar.style.backgroundColor = data.data.avatar_color;
                            headerAvatar.textContent = data.data.display_name.slice(0, 2).toUpperCase();
                        }}
                    }}
                }});
        }}

        function updateChannelHeaderAvatar() {{
            if (!currentChannel) return;
            
            const headerAvatar = document.getElementById('channel-header-avatar');
            headerAvatar.style.display = 'block';
            
            fetch(`/channel_info/${{encodeURIComponent(currentChannel)}}`)
                .then(r => r.json())
                .then(data => {{
                    if (data.success) {{
                        if (data.data.avatar_path) {{
                            headerAvatar.style.backgroundImage = `url(${{data.data.avatar_path}})`;
                            headerAvatar.textContent = '';
                        }} else {{
                            headerAvatar.style.backgroundImage = 'none';
                            headerAvatar.style.backgroundColor = data.data.avatar_color;
                            headerAvatar.textContent = data.data.display_name ? data.data.display_name.slice(0, 2).toUpperCase() : 'CH';
                        }}
                    }}
                }});
        }}

        // ... остальной JavaScript код остается без изменений ...

        function openRoom(r, t, title) {{
            room = r;
            roomType = t;
            currentChannel = t === 'channel' ? r.replace('channel_', '') : '';
            
            document.getElementById('chat-title').textContent = title;
            document.getElementById('categories-filter').style.display = 'none';
            document.getElementById('favorites-grid').style.display = 'none';
            document.getElementById('channel-settings').style.display = 'none';
            document.getElementById('chat-messages').style.display = 'block';
            document.getElementById('input-area').style.display = 'flex';
            
            if (isMobile) {{
                document.getElementById('sidebar').classList.add('hidden');
                document.getElementById('chat-area').classList.add('active');
            }}
            
            document.querySelectorAll('.nav-item').forEach(el => el.classList.remove('active'));
            event.currentTarget.classList.add('active');
            
            const chatMessages = document.getElementById('chat-messages');
            chatMessages.innerHTML = '<div class="empty-chat"><i class="fas fa-comments"></i><h3>Начните общение</h3><p>Отправьте сообщение, чтобы начать чат</p></div>';
            
            const channelActions = document.getElementById('channel-actions');
            const headerAvatar = document.getElementById('channel-header-avatar');
            const channelDescription = document.getElementById('channel-description');
            
            if (t === 'channel') {{
                channelActions.style.display = 'flex';
                headerAvatar.style.display = 'block';
                channelDescription.style.display = 'block';
                updateChannelHeader();
            }} else {{
                channelActions.style.display = 'none';
                headerAvatar.style.display = 'none';
                channelDescription.style.display = 'none';
            }}
            
            loadMessages(r);
            socket.emit('join', {{ room: r }});
        }}

        // ... остальной код JavaScript ...

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
        file_path = data.get('file')
        file_name = data.get('fileName')
        file_type = data.get('fileType', 'text')
        
        recipient = None
        if room.startswith('private_'):
            parts = room.split('_')
            if len(parts) == 3:
                user1, user2 = parts[1], parts[2]
                recipient = user1 if user2 == session['username'] else user2
        
        msg_id = save_message(
            session['username'], 
            msg, 
            room, 
            recipient, 
            file_type, 
            file_path,
            file_name
        )
        
        user_info = get_user(session['username'])
        user_color = user_info['avatar_color'] if user_info else '#667eea'
        user_avatar_path = user_info['avatar_path'] if user_info else None
        
        message_data = {
            'user': session['username'], 
            'message': msg, 
            'color': user_color,
            'avatar_path': user_avatar_path,
            'timestamp': datetime.now().strftime('%H:%M'),
            'room': room
        }
        
        if file_path:
            message_data['file'] = file_path
            message_data['fileName'] = file_name
            message_data['fileType'] = file_type
        
        emit('message', message_data, room=room)

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
