# web_messenger.py - Tandau Messenger (Render-совместимая версия)
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
import logging
from urllib.parse import urlparse

# Настройка логирования
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# === Фабрика приложения ===
def create_app():
    app = Flask(__name__)
    app.config['SECRET_KEY'] = os.environ.get('SECRET_KEY', 'tandau-secret-key-2024')
    
    # Важные настройки для Render
    app.config['PREFERRED_URL_SCHEME'] = 'https'
    
    # Используем временные директории на Render для загрузок
    upload_base_dir = os.path.join(os.getcwd(), 'uploads')
    
    # Создаем структуру папок
    app.config['UPLOAD_FOLDER'] = os.path.join(upload_base_dir, 'media')
    app.config['AVATAR_FOLDER'] = os.path.join(upload_base_dir, 'avatars')
    app.config['FAVORITE_FOLDER'] = os.path.join(upload_base_dir, 'favorites')
    app.config['STATIC_FOLDER'] = os.path.join(os.getcwd(), 'static')
    
    # Создаем статическую папку если её нет
    static_dir = app.config['STATIC_FOLDER']
    if not os.path.exists(static_dir):
        os.makedirs(static_dir, exist_ok=True)
    
    # Создаем символические ссылки для статических файлов
    for folder_name in ['media', 'avatars', 'favorites']:
        source_dir = os.path.join(upload_base_dir, folder_name)
        target_dir = os.path.join(static_dir, folder_name)
        
        if not os.path.exists(source_dir):
            os.makedirs(source_dir, exist_ok=True)
        
        # Создаем символическую ссылку если её нет
        if not os.path.exists(target_dir):
            try:
                os.symlink(source_dir, target_dir)
                logger.info(f"Created symlink: {target_dir} -> {source_dir}")
            except Exception as e:
                logger.warning(f"Could not create symlink: {e}")
                # Если симлинк не получился, просто создаем папку
                if not os.path.exists(target_dir):
                    os.makedirs(target_dir, exist_ok=True)
    
    app.config['MAX_CONTENT_LENGTH'] = 16 * 1024 * 1024  # 16MB
    ALLOWED_EXT = {'png', 'jpg', 'jpeg', 'gif', 'webp', 'mp4', 'webm', 'mov', 'txt', 'pdf', 'doc', 'docx'}
    
    # Используем правильный URL для статических файлов
    app.config['SERVER_NAME'] = None  # Отключаем SERVER_NAME для правильной работы ссылок
    
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
        if not filename:
            return False
        return '.' in filename and filename.rsplit('.', 1)[1].lower() in ALLOWED_EXT

    def save_uploaded_file(file, folder):
        if not file or file.filename == '': 
            return None, None
        if not allowed_file(file.filename): 
            return None, None
        
        filename = secure_filename(f"{int(datetime.now().timestamp())}_{file.filename}")
        path = os.path.join(folder, filename)
        
        try:
            file.save(path)
            # Возвращаем URL для доступа к файлу
            folder_name = os.path.basename(folder)
            return f'/static/{folder_name}/{filename}', filename
        except Exception as e:
            logger.error(f"Error saving file: {e}")
            return None, None

    def save_base64_file(base64_data, folder, file_extension):
        try:
            if ',' in base64_data:
                base64_data = base64_data.split(',')[1]
            
            file_data = base64.b64decode(base64_data)
            filename = f"{int(datetime.now().timestamp())}.{file_extension}"
            path = os.path.join(folder, filename)
            
            with open(path, 'wb') as f:
                f.write(file_data)
            
            folder_name = os.path.basename(folder)
            return f'/static/{folder_name}/{filename}', filename
        except Exception as e:
            logger.error(f"Error saving base64 file: {e}")
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
                # Исправляем URL файлов если нужно
                file_path = row[3]
                if file_path and not file_path.startswith(('http://', 'https://', '/')):
                    file_path = '/' + file_path.lstrip('/')
                
                messages.append({
                    'user': row[0],
                    'message': row[1],
                    'type': row[2],
                    'file': file_path,
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
                logger.error(f"Error adding to favorites: {e}")
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
                # Исправляем URL файлов если нужно
                file_path = row[2]
                if file_path and not file_path.startswith(('http://', 'https://', '/')):
                    file_path = '/' + file_path.lstrip('/')
                
                favorites.append({
                    'id': row[0],
                    'content': row[1],
                    'file_path': file_path,
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
            members = []
            for row in c.fetchall():
                # Исправляем URL аватарки если нужно
                avatar_path = row[3]
                if avatar_path and not avatar_path.startswith(('http://', 'https://', '/')):
                    avatar_path = '/' + avatar_path.lstrip('/')
                
                members.append({
                    'username': row[0], 
                    'is_admin': row[1], 
                    'online': row[2],
                    'avatar': avatar_path,
                    'color': row[4]
                })
            return members

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
            # Исправляем URL аватарки если нужно
            avatar_path = user['avatar_path']
            if avatar_path and not avatar_path.startswith(('http://', 'https://', '/')):
                avatar_path = '/' + avatar_path.lstrip('/')
            
            return jsonify({
                'success': True,
                'username': user['username'],
                'online': user['is_online'],
                'avatar_color': user['avatar_color'],
                'avatar_path': avatar_path,
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

    # Статические файлы - КРИТИЧЕСКИ ВАЖНЫЙ РОУТ
    @app.route('/static/<path:filename>')
    def static_files(filename):
        """Обработчик статических файлов для Render"""
        try:
            # Пробуем найти файл в разных местах
            possible_paths = [
                os.path.join(app.config['STATIC_FOLDER'], filename),
                os.path.join(app.config['UPLOAD_FOLDER'], filename.split('/', 1)[-1] if '/' in filename else filename),
                os.path.join(app.config['AVATAR_FOLDER'], filename.split('/', 1)[-1] if '/' in filename else filename),
                os.path.join(app.config['FAVORITE_FOLDER'], filename.split('/', 1)[-1] if '/' in filename else filename),
            ]
            
            for file_path in possible_paths:
                if os.path.exists(file_path) and os.path.isfile(file_path):
                    # Логируем успешную загрузку
                    logger.info(f"Serving static file: {filename} from {file_path}")
                    return send_from_directory(os.path.dirname(file_path), os.path.basename(file_path))
            
            # Если файл не найден, возвращаем 404
            logger.warning(f"Static file not found: {filename}")
            return "File not found", 404
            
        except Exception as e:
            logger.error(f"Error serving static file {filename}: {e}")
            return str(e), 500

    # Основные маршруты (остаются без изменений)
    @app.route('/')
    def index():
        if 'username' in session: 
            return redirect('/chat')
        
        # Вернуть HTML страницу входа/регистрации (как в оригинале)
        return '''
        <!DOCTYPE html>
        <html lang="ru">
        <head>
            <meta charset="UTF-8">
            <meta name="viewport" content="width=device-width, initial-scale=1.0">
            <title>Tandau Messenger</title>
            <link rel="stylesheet" href="https://cdnjs.cloudflare.com/ajax/libs/font-awesome/6.5.0/css/all.min.css">
            <style>
                /* Весь CSS из оригинального кода */
                /* ... */
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

    <!-- Модальные окна -->
    <div class="modal-overlay" id="terms-modal">
        <div class="terms-modal">
            <div class="modal-header">
                <h2><i class="fas fa-file-contract"></i> Условия использования</h2>
                <button class="close-modal" onclick="closeTermsModal()">
                    <i class="fas fa-times"></i>
                </button>
            </div>
            <div class="modal-content">
                <!-- Содержимое модального окна -->
                <div class="glass-terms-container">
                    <div class="glass-header">
                        <div class="glass-icon">
                            <i class="fas fa-file-contract"></i>
                        </div>
                        <h2 class="glass-title">Условия использования Tandau Messenger</h2>
                        <div class="glass-subtitle">Дата вступления в силу: 6 декабря 2025 г.</div>
                    </div>
                    <div class="glass-content">
                        <!-- ... остальное содержимое ... -->
                    </div>
                </div>
            </div>
        </div>
    </div>

    <script>
        // JavaScript код из оригинального файла
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
        
        // Остальной JavaScript код...
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
        
        # HTML для чата (аналогичный оригиналу)
        return f'''
<!DOCTYPE html>
<html lang="ru" data-theme="{theme}">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0, maximum-scale=1.0, user-scalable=no">
    <title>Tandau Chat - {username}</title>
    <link rel="stylesheet" href="https://cdnjs.cloudflare.com/ajax/libs/font-awesome/6.5.0/css/all.min.css">
    <style>
        /* Все стили из оригинального файла */
        /* ... */
    </style>
</head>
<body>
    <div class="app-container">
        <!-- Сайдбар -->
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
        let isMobile = window.innerWidth <= 768;

        // Все функции JavaScript из оригинального файла
        // ... (остальной JavaScript код)
        
        // Socket events
        socket.on('message', (data) => {{
            if (data.room === room) {{
                addMessageToChat(data, room);
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
            folder = app.config['UPLOAD_FOLDER']
            file_extension = 'png' if file_type == 'image' else 'mp4'
            file_path, saved_file_name = save_base64_file(
                file_data, 
                folder, 
                file_extension
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
        
        # Исправляем URL аватарки если нужно
        if user_avatar_path and not user_avatar_path.startswith(('http://', 'https://', '/')):
            user_avatar_path = '/' + user_avatar_path.lstrip('/')
        
        # Исправляем URL файла если нужно
        if file_path and not file_path.startswith(('http://', 'https://', '/')):
            file_path = '/' + file_path.lstrip('/')
        
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
    socketio.run(app, host='0.0.0.0', port=port, debug=False, allow_unsafe_werkzeug=True)
