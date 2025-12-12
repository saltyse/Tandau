# aura_messenger.py - AURA Messenger (единый файл)
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
import json

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

    # Создаем папки для загрузок
    try:
        os.makedirs(app.config['UPLOAD_FOLDER'], exist_ok=True)
        os.makedirs(app.config['AVATAR_FOLDER'], exist_ok=True)
        os.makedirs(app.config['FAVORITE_FOLDER'], exist_ok=True)
        os.makedirs(app.config['CHANNEL_AVATAR_FOLDER'], exist_ok=True)
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
                    avatar_color TEXT DEFAULT '#667eea',
                    avatar_path TEXT,
                    theme TEXT DEFAULT 'dark',
                    profile_description TEXT DEFAULT '',
                    accept_terms BOOLEAN DEFAULT FALSE
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
                    allow_messages BOOLEAN DEFAULT TRUE,
                    avatar_path TEXT,
                    subscriber_count INTEGER DEFAULT 0
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
            c.execute('''
                CREATE TABLE IF NOT EXISTS notes (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    username TEXT NOT NULL,
                    title TEXT,
                    content TEXT,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    is_pinned BOOLEAN DEFAULT FALSE,
                    category TEXT DEFAULT 'notes',
                    color TEXT DEFAULT '#667eea'
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
                    'theme': row[7],
                    'profile_description': row[8] or '',
                    'accept_terms': bool(row[9])
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

    def create_user(username, password, accept_terms=False):
        with sqlite3.connect('messenger.db') as conn:
            c = conn.cursor()
            try:
                # Проверяем, существует ли пользователь
                c.execute('SELECT id FROM users WHERE username = ?', (username,))
                if c.fetchone():
                    return False, "Пользователь уже существует"
                
                # Создаем пользователя
                c.execute('INSERT INTO users (username, password_hash, avatar_color, accept_terms) VALUES (?, ?, ?, ?)',
                          (username, generate_password_hash(password), 
                           random.choice(['#667eea','#8b5cf6','#10b981','#f59e0b','#ef4444','#3b82f6']),
                           accept_terms))
                
                # Добавляем пользователя в общий канал
                c.execute('INSERT OR IGNORE INTO channel_members (channel_id, username) SELECT id, ? FROM channels WHERE name="general"', (username,))
                
                # Обновляем счетчик подписчиков
                c.execute('UPDATE channels SET subscriber_count = subscriber_count + 1 WHERE name = "general"')
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

    def update_profile_description(username, description):
        with sqlite3.connect('messenger.db') as conn:
            c = conn.cursor()
            c.execute('UPDATE users SET profile_description = ? WHERE username = ?', (description, username))
            conn.commit()
            return c.rowcount > 0

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
                    'color': user_info['avatar_color'] if user_info else '#667eea',
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

    def create_channel(name, display_name, description, created_by, is_private=False):
        with sqlite3.connect('messenger.db') as conn:
            c = conn.cursor()
            try:
                # Проверяем, существует ли канал
                c.execute('SELECT id FROM channels WHERE name = ?', (name,))
                if c.fetchone():
                    return None
                
                # Создаем канал
                c.execute('INSERT INTO channels (name, display_name, description, created_by, is_private) VALUES (?, ?, ?, ?, ?)',
                          (name, display_name or name, description or '', created_by, is_private))
                channel_id = c.lastrowid
                
                # Добавляем создателя в канал как администратора
                c.execute('INSERT INTO channel_members (channel_id, username, is_admin) VALUES (?, ?, ?)',
                          (channel_id, created_by, True))
                conn.commit()
                return channel_id
            except sqlite3.IntegrityError:
                return None
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
                
                # Обновляем счетчик подписчиков
                c.execute('UPDATE channels SET subscriber_count = subscriber_count + 1 WHERE id = ?', (channel_id,))
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
                
                # Обновляем счетчик подписчиков
                c.execute('UPDATE channels SET subscriber_count = subscriber_count - 1 WHERE id = ?', (channel_id,))
                conn.commit()
                return True, "Пользователь удален"
            except Exception as e:
                return False, f"Ошибка: {str(e)}"

    def update_channel_avatar(channel_name, avatar_path, username):
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
                    c.execute('UPDATE channels SET avatar_path = ? WHERE name = ?', (avatar_path, channel_name))
                    conn.commit()
                    return True
                return False
            except Exception as e:
                print(f"Error updating channel avatar: {e}")
                return False

    def delete_channel_avatar(channel_name, username):
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
                    c.execute('UPDATE channels SET avatar_path = NULL WHERE name = ?', (channel_name,))
                    conn.commit()
                    return True
                return False
            except Exception as e:
                print(f"Error deleting channel avatar: {e}")
                return False

    def make_user_admin(channel_name, target_user, current_user):
        with sqlite3.connect('messenger.db') as conn:
            c = conn.cursor()
            try:
                # Проверяем, является ли текущий пользователь администратором
                c.execute('''
                    SELECT 1 FROM channels c 
                    JOIN channel_members cm ON c.id = cm.channel_id 
                    WHERE c.name = ? AND cm.username = ? AND cm.is_admin = 1
                ''', (channel_name, current_user))
                
                if not c.fetchone():
                    return False, "У вас нет прав администратора"
                
                # Получаем ID канала
                c.execute('SELECT id FROM channels WHERE name = ?', (channel_name,))
                channel_id = c.fetchone()
                if not channel_id:
                    return False, "Канал не найден"
                
                # Проверяем, существует ли пользователь в канале
                c.execute('SELECT 1 FROM channel_members WHERE channel_id = ? AND username = ?', (channel_id[0], target_user))
                if not c.fetchone():
                    return False, "Пользователь не найден в канале"
                
                # Назначаем администратором
                c.execute('UPDATE channel_members SET is_admin = 1 WHERE channel_id = ? AND username = ?', (channel_id[0], target_user))
                conn.commit()
                return True, "Пользователь назначен администратором"
            except Exception as e:
                return False, f"Ошибка: {str(e)}"

    def remove_admin(channel_name, target_user, current_user):
        with sqlite3.connect('messenger.db') as conn:
            c = conn.cursor()
            try:
                # Проверяем, является ли текущий пользователь администратором
                c.execute('''
                    SELECT 1 FROM channels c 
                    JOIN channel_members cm ON c.id = cm.channel_id 
                    WHERE c.name = ? AND cm.username = ? AND cm.is_admin = 1
                ''', (channel_name, current_user))
                
                if not c.fetchone():
                    return False, "У вас нет прав администратора"
                
                # Получаем ID канала
                c.execute('SELECT id FROM channels WHERE name = ?', (channel_name,))
                channel_id = c.fetchone()
                if not channel_id:
                    return False, "Канал не найден"
                
                # Проверяем, существует ли пользователь в канале
                c.execute('SELECT 1 FROM channel_members WHERE channel_id = ? AND username = ?', (channel_id[0], target_user))
                if not c.fetchone():
                    return False, "Пользователь не найден в канале"
                
                # Нельзя снять права администратора у создателя канала
                c.execute('SELECT created_by FROM channels WHERE name = ?', (channel_name,))
                created_by = c.fetchone()
                if created_by and created_by[0] == target_user:
                    return False, "Нельзя снять права администратора у создателя канала"
                
                # Нельзя снять права администратора у самого себя
                if target_user == current_user:
                    return False, "Нельзя снять права администратора у самого себя"
                
                # Снимаем права администратора
                c.execute('UPDATE channel_members SET is_admin = 0 WHERE channel_id = ? AND username = ?', (channel_id[0], target_user))
                conn.commit()
                return True, "Права администратора сняты"
            except Exception as e:
                return False, f"Ошибка: {str(e)}"

    def get_channel_info(channel_name):
        with sqlite3.connect('messenger.db') as conn:
            c = conn.cursor()
            c.execute('SELECT id, name, display_name, description, created_by, is_private, allow_messages, avatar_path, subscriber_count FROM channels WHERE name = ?', (channel_name,))
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
                    'subscriber_count': row[8] or 0
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
                SELECT c.name, c.display_name, c.description, c.is_private, c.allow_messages, c.created_by, c.avatar_path, c.subscriber_count
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
                'created_by': row[5],
                'avatar_path': row[6],
                'subscriber_count': row[7] or 0
            } for row in c.fetchall()]

    def get_all_users_data():
        with sqlite3.connect('messenger.db') as conn:
            c = conn.cursor()
            c.execute('''
                SELECT username, is_online, avatar_color, avatar_path, profile_description, created_at
                FROM users 
                ORDER BY is_online DESC, username
            ''')
            users = []
            for row in c.fetchall():
                users.append({
                    'username': row[0],
                    'online': row[1],
                    'color': row[2],
                    'avatar_path': row[3],
                    'profile_description': row[4] or '',
                    'created_at': row[5],
                    'is_current': False
                })
            return users

    def add_note(username, title, content, category='notes', color='#667eea'):
        with sqlite3.connect('messenger.db') as conn:
            c = conn.cursor()
            try:
                c.execute('INSERT INTO notes (username, title, content, category, color) VALUES (?, ?, ?, ?, ?)',
                          (username, title, content, category, color))
                conn.commit()
                return c.lastrowid
            except Exception as e:
                print(f"Error adding note: {e}")
                return None

    def get_notes(username, category=None):
        with sqlite3.connect('messenger.db') as conn:
            c = conn.cursor()
            if category:
                c.execute('''
                    SELECT id, title, content, created_at, updated_at, is_pinned, category, color
                    FROM notes 
                    WHERE username = ? AND category = ?
                    ORDER BY is_pinned DESC, updated_at DESC
                ''', (username, category))
            else:
                c.execute('''
                    SELECT id, title, content, created_at, updated_at, is_pinned, category, color
                    FROM notes 
                    WHERE username = ? 
                    ORDER BY is_pinned DESC, updated_at DESC
                ''', (username,))
            
            notes = []
            for row in c.fetchall():
                notes.append({
                    'id': row[0],
                    'title': row[1],
                    'content': row[2],
                    'created_at': row[3],
                    'updated_at': row[4],
                    'is_pinned': bool(row[5]),
                    'category': row[6],
                    'color': row[7]
                })
            return notes

    def delete_note(note_id, username):
        with sqlite3.connect('messenger.db') as conn:
            c = conn.cursor()
            c.execute('DELETE FROM notes WHERE id = ? AND username = ?', (note_id, username))
            conn.commit()
            return c.rowcount > 0

    def update_note(note_id, username, title=None, content=None, category=None, color=None):
        with sqlite3.connect('messenger.db') as conn:
            c = conn.cursor()
            try:
                update_fields = []
                update_values = []
                
                if title is not None:
                    update_fields.append('title = ?')
                    update_values.append(title)
                
                if content is not None:
                    update_fields.append('content = ?')
                    update_values.append(content)
                
                if category is not None:
                    update_fields.append('category = ?')
                    update_values.append(category)
                
                if color is not None:
                    update_fields.append('color = ?')
                    update_values.append(color)
                
                update_fields.append('updated_at = CURRENT_TIMESTAMP')
                
                if update_fields:
                    update_values.extend([note_id, username])
                    query = f"UPDATE notes SET {', '.join(update_fields)} WHERE id = ? AND username = ?"
                    c.execute(query, update_values)
                    conn.commit()
                    return c.rowcount > 0
                return False
            except Exception as e:
                print(f"Error updating note: {e}")
                return False

    def toggle_pin_note(note_id, username):
        with sqlite3.connect('messenger.db') as conn:
            c = conn.cursor()
            # Получаем текущее состояние
            c.execute('SELECT is_pinned FROM notes WHERE id = ? AND username = ?', (note_id, username))
            row = c.fetchone()
            if row:
                new_state = not bool(row[0])
                c.execute('UPDATE notes SET is_pinned = ? WHERE id = ? AND username = ?', 
                         (new_state, note_id, username))
                conn.commit()
                return new_state
            return None

    # === Основные маршруты ===
    @app.route('/')
    def index():
        if 'username' in session: 
            return redirect('/chat')
        
        # Страница входа/регистрации AURA с политиками
        return '''
        <!DOCTYPE html>
        <html lang="ru">
        <head>
            <meta charset="UTF-8">
            <meta name="viewport" content="width=device-width, initial-scale=1.0">
            <title>AURA Messenger</title>
            <link rel="stylesheet" href="https://cdnjs.cloudflare.com/ajax/libs/font-awesome/6.5.0/css/all.min.css">
            <style>
                * {
                    margin: 0;
                    padding: 0;
                    box-sizing: border-box;
                    font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif;
                }
                
                :root {
                    --primary: #667eea;
                    --primary-dark: #5a67d8;
                    --primary-light: #7c9bf2;
                    --secondary: #8b5cf6;
                    --accent: #10b981;
                    --text: #ffffff;
                    --text-light: #a0aec0;
                    --bg: #0f0f23;
                    --bg-light: #1a1a2e;
                    --border: #2d2d4d;
                    --shadow: 0 10px 25px -5px rgba(0, 0, 0, 0.3), 0 10px 10px -5px rgba(0, 0, 0, 0.2);
                    --radius: 16px;
                    --radius-sm: 10px;
                    --transition: all 0.3s cubic-bezier(0.4, 0, 0.2, 1);
                }
                
                body {
                    background: linear-gradient(135deg, #0f0f23 0%, #1a1a2e 100%);
                    min-height: 100vh;
                    display: flex;
                    align-items: center;
                    justify-content: center;
                    padding: 20px;
                    color: var(--text);
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
                    background: rgba(255, 255, 255, 0.05);
                    backdrop-filter: blur(10px);
                    -webkit-backdrop-filter: blur(10px);
                    padding: 20px 40px;
                    border-radius: 24px;
                    margin-bottom: 25px;
                    box-shadow: 0 8px 32px rgba(0, 0, 0, 0.2);
                    border: 1px solid rgba(255, 255, 255, 0.1);
                }
                
                .logo-placeholder {
                    width: 60px;
                    height: 60px;
                    border-radius: 50%;
                    background: linear-gradient(135deg, #667eea, #8b5cf6);
                    display: flex;
                    align-items: center;
                    justify-content: center;
                    color: white;
                    font-size: 28px;
                    font-weight: bold;
                    box-shadow: 0 4px 12px rgba(0, 0, 0, 0.3);
                }
                
                .app-title {
                    color: white;
                    font-size: 2.8rem;
                    font-weight: 800;
                    letter-spacing: -0.5px;
                    text-shadow: 0 2px 10px rgba(0, 0, 0, 0.3);
                }
                
                .app-subtitle {
                    color: rgba(255, 255, 255, 0.7);
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
                    border: 1px solid var(--border);
                }
                
                .auth-header {
                    display: flex;
                    background: rgba(255, 255, 255, 0.03);
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
                    background: rgba(102, 126, 234, 0.05);
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
                    background: rgba(255, 255, 255, 0.05);
                    color: var(--text);
                }
                
                .form-input:focus {
                    outline: none;
                    border-color: var(--primary);
                    box-shadow: 0 0 0 3px rgba(102, 126, 234, 0.1);
                    background: rgba(255, 255, 255, 0.08);
                }
                
                .form-input::placeholder {
                    color: var(--text-light);
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
                    background: linear-gradient(135deg, var(--primary), var(--secondary));
                    color: white;
                }
                
                .btn-primary:hover {
                    transform: translateY(-2px);
                    box-shadow: 0 6px 20px rgba(102, 126, 234, 0.4);
                }
                
                .btn-primary:active {
                    transform: translateY(0);
                }
                
                .alert {
                    padding: 14px 18px;
                    border-radius: var(--radius-sm);
                    margin-bottom: 24px;
                    display: none;
                    animation: slideIn 0.3s ease-out;
                }
                
                .alert-error {
                    background: rgba(220, 53, 69, 0.1);
                    color: #ff6b6b;
                    border-left: 4px solid #ff6b6b;
                }
                
                .alert-success {
                    background: rgba(16, 185, 129, 0.1);
                    color: #51cf66;
                    border-left: 4px solid #51cf66;
                }
                
                .terms-section {
                    margin-top: 20px;
                    padding: 20px;
                    background: rgba(255, 255, 255, 0.03);
                    border-radius: var(--radius-sm);
                    border: 1px solid var(--border);
                }
                
                .terms-checkbox {
                    display: flex;
                    align-items: center;
                    gap: 12px;
                    margin-bottom: 15px;
                    cursor: pointer;
                    user-select: none;
                }
                
                .terms-checkbox input {
                    width: 20px;
                    height: 20px;
                    border-radius: 6px;
                    border: 2px solid var(--border);
                    background: rgba(255, 255, 255, 0.05);
                    cursor: pointer;
                    position: relative;
                }
                
                .terms-checkbox input:checked {
                    background: var(--primary);
                    border-color: var(--primary);
                }
                
                .terms-checkbox input:checked::after {
                    content: '✓';
                    position: absolute;
                    top: 50%;
                    left: 50%;
                    transform: translate(-50%, -50%);
                    color: white;
                    font-size: 12px;
                    font-weight: bold;
                }
                
                .terms-links {
                    display: flex;
                    gap: 15px;
                    margin-top: 15px;
                }
                
                .terms-links a {
                    color: var(--primary-light);
                    text-decoration: none;
                    font-size: 0.9rem;
                    cursor: pointer;
                    transition: var(--transition);
                }
                
                .terms-links a:hover {
                    text-decoration: underline;
                }
                
                .modal-overlay {
                    display: none;
                    position: fixed;
                    top: 0;
                    left: 0;
                    width: 100%;
                    height: 100%;
                    background: rgba(0, 0, 0, 0.8);
                    backdrop-filter: blur(8px);
                    -webkit-backdrop-filter: blur(8px);
                    z-index: 1000;
                    animation: fadeIn 0.3s ease-out;
                    align-items: center;
                    justify-content: center;
                    padding: 20px;
                }
                
                .terms-modal {
                    background: var(--bg-light);
                    border-radius: var(--radius);
                    box-shadow: 0 20px 60px rgba(0, 0, 0, 0.4);
                    max-width: 800px;
                    width: 100%;
                    max-height: 85vh;
                    overflow: hidden;
                    animation: slideUp 0.4s cubic-bezier(0.4, 0, 0.2, 1);
                    border: 1px solid var(--border);
                }
                
                .modal-header {
                    padding: 24px 30px;
                    background: linear-gradient(135deg, var(--primary), var(--secondary));
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
                
                .modal-content h3 {
                    font-size: 1.3rem;
                    margin: 20px 0 10px;
                    color: var(--text);
                }
                
                .modal-content p {
                    color: var(--text-light);
                    line-height: 1.6;
                    margin-bottom: 15px;
                }
                
                .modal-content ul {
                    color: var(--text-light);
                    padding-left: 20px;
                    margin-bottom: 15px;
                }
                
                .modal-content li {
                    margin-bottom: 8px;
                }
                
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
                            <i class="fas fa-circle"></i>
                        </div>
                        <h1 class="app-title">AURA</h1>
                    </div>
                    <p class="app-subtitle">Быстрый и современный мессенджер для общения</p>
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
                            
                            <div class="terms-section">
                                <div class="terms-checkbox">
                                    <input type="checkbox" id="accept-terms">
                                    <span>Я принимаю условия использования</span>
                                </div>
                                <div class="terms-links">
                                    <a href="#" onclick="openTermsModal('terms'); return false;">Условия использования</a>
                                    <a href="#" onclick="openTermsModal('privacy'); return false;">Политика конфиденциальности</a>
                                </div>
                            </div>
                            
                            <button type="button" class="btn btn-primary" onclick="register()" id="register-btn">
                                <i class="fas fa-user-plus"></i>
                                Создать аккаунт
                            </button>
                        </form>
                    </div>
                </div>
            </div>

            <!-- Модальное окно условий -->
            <div class="modal-overlay" id="terms-modal">
                <div class="terms-modal">
                    <div class="modal-header">
                        <h2 id="modal-title">Условия использования</h2>
                        <button class="close-modal" onclick="closeTermsModal()">
                            <i class="fas fa-times"></i>
                        </button>
                    </div>
                    <div class="modal-content" id="terms-content">
                        <!-- Содержимое загружается динамически -->
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
                    const acceptTerms = document.getElementById('accept-terms').checked;
                    
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
                    
                    if (!acceptTerms) {
                        return showAlert('Необходимо принять условия использования');
                    }
                    
                    setLoading('register-btn', true);
                    
                    try {
                        const response = await fetch('/register', {
                            method: 'POST',
                            headers: {
                                'Content-Type': 'application/x-www-form-urlencoded',
                            },
                            body: new URLSearchParams({ 
                                username, 
                                password,
                                accept_terms: 'true'
                            })
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
                
                function openTermsModal(type) {
                    const modal = document.getElementById('terms-modal');
                    const title = document.getElementById('modal-title');
                    const content = document.getElementById('terms-content');
                    
                    if (type === 'terms') {
                        title.textContent = 'Условия использования';
                        content.innerHTML = `
                            <h3>1. Общие положения</h3>
                            <p>Добро пожаловать в AURA Messenger! Используя наш сервис, вы соглашаетесь с настоящими Условиями использования.</p>
                            
                            <h3>2. Условия использования</h3>
                            <p>2.1. Вы обязуетесь использовать сервис только в законных целях.</p>
                            <p>2.2. Запрещено распространение спама, вредоносного контента или материалов, нарушающих права третьих лиц.</p>
                            <p>2.3. Вы несете ответственность за сохранность ваших учетных данных.</p>
                            
                            <h3>3. Конфиденциальность</h3>
                            <p>Мы уважаем вашу конфиденциальность и защищаем ваши персональные данные в соответствии с нашей Политикой конфиденциальности.</p>
                            
                            <h3>4. Интеллектуальная собственность</h3>
                            <p>Все права на программное обеспечение, дизайн и контент AURA Messenger принадлежат разработчикам сервиса.</p>
                            
                            <h3>5. Ограничение ответственности</h3>
                            <p>Мы не несем ответственности за любой ущерб, возникший в результате использования или невозможности использования нашего сервиса.</p>
                            
                            <h3>6. Изменения условий</h3>
                            <p>Мы оставляем за собой право изменять настоящие Условия использования. Изменения вступают в силу с момента их публикации на сайте.</p>
                        `;
                    } else if (type === 'privacy') {
                        title.textContent = 'Политика конфиденциальности';
                        content.innerHTML = `
                            <h3>1. Сбор информации</h3>
                            <p>Мы собираем только ту информацию, которую вы предоставляете нам при регистрации и использовании сервиса.</p>
                            
                            <h3>2. Использование информации</h3>
                            <p>2.1. Ваша информация используется для предоставления услуг мессенджера.</p>
                            <p>2.2. Мы не передаем ваши персональные данные третьим лицам без вашего согласия.</p>
                            <p>2.3. Мы можем использовать обезличенные данные для улучшения работы сервиса.</p>
                            
                            <h3>3. Защита информации</h3>
                            <p>Мы принимаем разумные меры для защиты вашей информации от несанкционированного доступа, изменения или уничтожения.</p>
                            
                            <h3>4. Файлы cookie</h3>
                            <p>Мы используем файлы cookie для улучшения работы сервиса и анализа трафика.</p>
                            
                            <h3>5. Права пользователей</h3>
                            <p>Вы имеете право запросить доступ к вашим персональным данным, их исправление или удаление.</p>
                            
                            <h3>6. Контакты</h3>
                            <p>По вопросам, связанным с конфиденциальностью, вы можете связаться с нами через сервис.</p>
                        `;
                    }
                    
                    modal.style.display = 'flex';
                }
                
                function closeTermsModal() {
                    document.getElementById('terms-modal').style.display = 'none';
                }
                
                document.addEventListener('keypress', function(e) {
                    if (e.key === 'Enter') {
                        const activeForm = document.querySelector('.auth-form.active');
                        if (activeForm.id === 'login-form') login();
                        if (activeForm.id === 'register-form') register();
                    }
                });
                
                // Закрытие модального окна при клике вне его
                document.getElementById('terms-modal').addEventListener('click', function(e) {
                    if (e.target === this) {
                        closeTermsModal();
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
        accept_terms = request.form.get('accept_terms') == 'true'
        
        if not u or not p:
            return jsonify({'success': False, 'error': 'Заполните все поля'})
        
        if len(u) < 3:
            return jsonify({'success': False, 'error': 'Логин должен быть не менее 3 символов'})
        
        if len(p) < 4:
            return jsonify({'success': False, 'error': 'Пароль должен быть не менее 4 символов'})
        
        if not accept_terms:
            return jsonify({'success': False, 'error': 'Необходимо принять условия использования'})
        
        success, message = create_user(u, p, accept_terms)
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
        
        # Получаем данные для чата
        all_users = get_all_users_data()
        user_channels = get_user_channels(username)
        personal_chats = get_user_personal_chats(username)
        favorites = get_favorites(username)
        notes = get_notes(username)
        
        # Генерируем полный HTML
        return render_template_string('''
<!DOCTYPE html>
<html lang="ru" data-theme="{{ theme }}">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0, maximum-scale=1.0, user-scalable=no">
    <title>AURA Messenger - {{ username }}</title>
    <link rel="stylesheet" href="https://cdnjs.cloudflare.com/ajax/libs/font-awesome/6.5.0/css/all.min.css">
    <style>
        /* СТИЛИ AURA MESSENGER */
        :root {
            --primary: #667eea;
            --primary-dark: #5a67d8;
            --primary-light: #7c9bf2;
            --secondary: #8b5cf6;
            --accent: #10b981;
            --danger: #ef4444;
            --warning: #f59e0b;
            --success: #10b981;
            
            --bg: #0f0f23;
            --bg-light: #1a1a2e;
            --bg-lighter: #2d2d4d;
            
            --text: #ffffff;
            --text-light: #a0aec0;
            --text-lighter: #cbd5e0;
            
            --border: #2d2d4d;
            --border-light: #4a5568;
            
            --sidebar-width: 280px;
            --header-height: 70px;
            
            --radius-sm: 8px;
            --radius-md: 12px;
            --radius-lg: 16px;
            --radius-xl: 20px;
            
            --shadow-sm: 0 2px 4px rgba(0, 0, 0, 0.2);
            --shadow-md: 0 4px 12px rgba(0, 0, 0, 0.3);
            --shadow-lg: 0 10px 25px rgba(0, 0, 0, 0.4);
            
            --transition: all 0.3s cubic-bezier(0.4, 0, 0.2, 1);
        }
        
        [data-theme="light"] {
            --bg: #f8f9fa;
            --bg-light: #ffffff;
            --bg-lighter: #f1f5f9;
            
            --text: #1a1a2e;
            --text-light: #64748b;
            --text-lighter: #475569;
            
            --border: #e5e7eb;
            --border-light: #d1d5db;
        }
        
        * {
            margin: 0;
            padding: 0;
            box-sizing: border-box;
            -webkit-tap-highlight-color: transparent;
        }
        
        body {
            font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif;
            background: var(--bg);
            color: var(--text);
            height: 100vh;
            overflow: hidden;
            touch-action: manipulation;
        }
        
        /* ОСНОВНОЙ КОНТЕЙНЕР */
        .app-container {
            display: flex;
            height: 100vh;
        }
        
        /* САЙДБАР */
        .sidebar {
            width: var(--sidebar-width);
            background: var(--bg-light);
            display: flex;
            flex-direction: column;
            border-right: 1px solid var(--border);
            transition: var(--transition);
            position: relative;
            z-index: 100;
        }
        
        .sidebar-header {
            padding: 20px;
            background: linear-gradient(135deg, var(--primary), var(--secondary));
            display: flex;
            align-items: center;
            gap: 15px;
            border-bottom: 1px solid rgba(255, 255, 255, 0.1);
        }
        
        .logo {
            width: 40px;
            height: 40px;
            border-radius: 50%;
            background: rgba(255, 255, 255, 0.2);
            display: flex;
            align-items: center;
            justify-content: center;
            color: white;
            font-size: 20px;
            font-weight: bold;
        }
        
        .app-name {
            color: white;
            font-size: 1.5rem;
            font-weight: 800;
            letter-spacing: -0.5px;
        }
        
        .user-info {
            padding: 20px;
            display: flex;
            align-items: center;
            gap: 15px;
            border-bottom: 1px solid var(--border);
            cursor: pointer;
            transition: var(--transition);
        }
        
        .user-info:hover {
            background: var(--bg-lighter);
        }
        
        .avatar {
            width: 50px;
            height: 50px;
            border-radius: 50%;
            display: flex;
            align-items: center;
            justify-content: center;
            color: white;
            font-weight: bold;
            font-size: 1.2rem;
            background-size: cover;
            background-position: center;
            border: 3px solid var(--primary);
        }
        
        .user-details {
            flex: 1;
        }
        
        .username {
            font-weight: 600;
            font-size: 1.1rem;
            margin-bottom: 4px;
        }
        
        .status {
            font-size: 0.85rem;
            color: var(--text-light);
            display: flex;
            align-items: center;
            gap: 6px;
        }
        
        .status-dot {
            width: 8px;
            height: 8px;
            border-radius: 50%;
            background: var(--success);
        }
        
        .nav-tabs {
            display: flex;
            border-bottom: 1px solid var(--border);
            background: var(--bg-light);
        }
        
        .nav-tab {
            flex: 1;
            padding: 15px;
            text-align: center;
            cursor: pointer;
            font-weight: 500;
            color: var(--text-light);
            transition: var(--transition);
            border-bottom: 3px solid transparent;
        }
        
        .nav-tab:hover {
            color: var(--text);
            background: var(--bg-lighter);
        }
        
        .nav-tab.active {
            color: var(--primary);
            border-bottom-color: var(--primary);
        }
        
        .nav-content {
            flex: 1;
            overflow-y: auto;
            padding: 20px;
            -webkit-overflow-scrolling: touch;
        }
        
        /* СПИСКИ */
        .users-list, .channels-list, .chats-list, .notes-list {
            display: flex;
            flex-direction: column;
            gap: 10px;
        }
        
        .user-item, .channel-item, .chat-item, .note-item {
            display: flex;
            align-items: center;
            gap: 12px;
            padding: 12px 15px;
            background: var(--bg-lighter);
            border-radius: var(--radius-md);
            cursor: pointer;
            transition: var(--transition);
            border: 1px solid var(--border);
        }
        
        .user-item:hover, .channel-item:hover, .chat-item:hover, .note-item:hover {
            background: var(--border);
            transform: translateX(5px);
            border-color: var(--primary);
        }
        
        .item-avatar {
            width: 40px;
            height: 40px;
            border-radius: 50%;
            display: flex;
            align-items: center;
            justify-content: center;
            color: white;
            font-weight: bold;
            font-size: 0.9rem;
            background-size: cover;
            background-position: center;
        }
        
        .item-info {
            flex: 1;
        }
        
        .item-name {
            font-weight: 600;
            font-size: 0.95rem;
            margin-bottom: 4px;
        }
        
        .item-status, .item-description {
            font-size: 0.8rem;
            color: var(--text-light);
        }
        
        .item-description {
            max-height: 40px;
            overflow: hidden;
            text-overflow: ellipsis;
            display: -webkit-box;
            -webkit-line-clamp: 2;
            -webkit-box-orient: vertical;
        }
        
        .online-badge {
            width: 10px;
            height: 10px;
            border-radius: 50%;
            background: var(--success);
            margin-left: auto;
        }
        
        /* ОБЛАСТЬ ЧАТА */
        .chat-area {
            flex: 1;
            display: flex;
            flex-direction: column;
            background: var(--bg);
        }
        
        .chat-header {
            padding: 0 20px;
            height: var(--header-height);
            background: var(--bg-light);
            border-bottom: 1px solid var(--border);
            display: flex;
            align-items: center;
            justify-content: space-between;
        }
        
        .chat-info {
            display: flex;
            align-items: center;
            gap: 15px;
        }
        
        .chat-title {
            font-size: 1.3rem;
            font-weight: 700;
        }
        
        .chat-subtitle {
            font-size: 0.9rem;
            color: var(--text-light);
        }
        
        .chat-actions {
            display: flex;
            gap: 10px;
        }
        
        .action-btn {
            width: 40px;
            height: 40px;
            border-radius: 50%;
            background: var(--bg-lighter);
            border: 1px solid var(--border);
            color: var(--text);
            display: flex;
            align-items: center;
            justify-content: center;
            cursor: pointer;
            transition: var(--transition);
        }
        
        .action-btn:hover {
            background: var(--border);
            color: var(--primary);
        }
        
        /* СООБЩЕНИЯ */
        .messages-container {
            flex: 1;
            padding: 20px;
            overflow-y: auto;
            -webkit-overflow-scrolling: touch;
            display: flex;
            flex-direction: column;
            gap: 15px;
        }
        
        .message {
            display: flex;
            gap: 12px;
            max-width: 80%;
            animation: fadeIn 0.3s ease;
        }
        
        .message.own {
            align-self: flex-end;
            flex-direction: row-reverse;
        }
        
        .message-avatar {
            width: 36px;
            height: 36px;
            border-radius: 50%;
            background-size: cover;
            background-position: center;
            flex-shrink: 0;
            display: flex;
            align-items: center;
            justify-content: center;
            color: white;
            font-weight: bold;
            font-size: 0.9rem;
        }
        
        .message-content {
            background: var(--bg-light);
            padding: 12px 16px;
            border-radius: var(--radius-lg);
            border-top-left-radius: 4px;
            max-width: 100%;
            word-wrap: break-word;
            border: 1px solid var(--border);
        }
        
        .message.own .message-content {
            background: linear-gradient(135deg, var(--primary), var(--secondary));
            color: white;
            border-top-left-radius: var(--radius-lg);
            border-top-right-radius: 4px;
            border: none;
        }
        
        .message-sender {
            font-weight: 600;
            font-size: 0.9rem;
            margin-bottom: 4px;
            color: var(--text);
        }
        
        .message.own .message-sender {
            color: rgba(255, 255, 255, 0.9);
        }
        
        .message-text {
            line-height: 1.4;
            font-size: 0.95rem;
        }
        
        .message-time {
            font-size: 0.75rem;
            color: var(--text-light);
            margin-top: 6px;
            text-align: right;
        }
        
        .message.own .message-time {
            color: rgba(255, 255, 255, 0.7);
        }
        
        /* ПОЛЕ ВВОДА */
        .input-area {
            padding: 20px;
            background: var(--bg-light);
            border-top: 1px solid var(--border);
        }
        
        .input-container {
            display: flex;
            gap: 10px;
            align-items: flex-end;
        }
        
        .msg-input {
            flex: 1;
            padding: 14px 18px;
            border: 1px solid var(--border);
            border-radius: var(--radius-xl);
            background: var(--bg);
            color: var(--text);
            font-size: 1rem;
            resize: none;
            max-height: 120px;
            min-height: 50px;
            line-height: 1.4;
            transition: var(--transition);
        }
        
        .msg-input:focus {
            outline: none;
            border-color: var(--primary);
            box-shadow: 0 0 0 3px rgba(102, 126, 234, 0.1);
        }
        
        .send-btn {
            width: 50px;
            height: 50px;
            border-radius: 50%;
            background: linear-gradient(135deg, var(--primary), var(--secondary));
            color: white;
            border: none;
            cursor: pointer;
            display: flex;
            align-items: center;
            justify-content: center;
            transition: var(--transition);
            font-size: 1.2rem;
        }
        
        .send-btn:hover {
            transform: translateY(-2px);
            box-shadow: var(--shadow-md);
        }
        
        /* ЗАМЕТКИ */
        .notes-grid {
            display: grid;
            grid-template-columns: repeat(auto-fill, minmax(250px, 1fr));
            gap: 20px;
            padding: 20px;
        }
        
        .note-card {
            background: var(--bg-light);
            border-radius: var(--radius-lg);
            padding: 20px;
            border: 1px solid var(--border);
            transition: var(--transition);
            cursor: pointer;
            position: relative;
        }
        
        .note-card:hover {
            transform: translateY(-5px);
            box-shadow: var(--shadow-lg);
            border-color: var(--primary);
        }
        
        .note-card.pinned {
            border-left: 4px solid var(--warning);
        }
        
        .note-title {
            font-size: 1.2rem;
            font-weight: 700;
            margin-bottom: 10px;
            color: var(--text);
        }
        
        .note-content {
            color: var(--text-light);
            line-height: 1.5;
            margin-bottom: 15px;
            max-height: 100px;
            overflow: hidden;
            text-overflow: ellipsis;
            display: -webkit-box;
            -webkit-line-clamp: 4;
            -webkit-box-orient: vertical;
        }
        
        .note-meta {
            display: flex;
            justify-content: space-between;
            align-items: center;
            font-size: 0.8rem;
            color: var(--text-light);
        }
        
        .note-actions {
            position: absolute;
            top: 15px;
            right: 15px;
            display: flex;
            gap: 8px;
            opacity: 0;
            transition: var(--transition);
        }
        
        .note-card:hover .note-actions {
            opacity: 1;
        }
        
        .note-action-btn {
            width: 30px;
            height: 30px;
            border-radius: 50%;
            background: var(--bg);
            border: 1px solid var(--border);
            color: var(--text);
            display: flex;
            align-items: center;
            justify-content: center;
            cursor: pointer;
            transition: var(--transition);
            font-size: 0.9rem;
        }
        
        .note-action-btn:hover {
            background: var(--primary);
            color: white;
            border-color: var(--primary);
        }
        
        /* ВСЕ ПОЛЬЗОВАТЕЛИ */
        .all-users-grid {
            display: grid;
            grid-template-columns: repeat(auto-fill, minmax(200px, 1fr));
            gap: 15px;
            padding: 20px;
        }
        
        .user-card {
            background: var(--bg-light);
            border-radius: var(--radius-lg);
            padding: 20px;
            border: 1px solid var(--border);
            transition: var(--transition);
            cursor: pointer;
            text-align: center;
        }
        
        .user-card:hover {
            transform: translateY(-3px);
            box-shadow: var(--shadow-md);
            border-color: var(--primary);
        }
        
        .user-card.online {
            border-top: 3px solid var(--success);
        }
        
        .user-card.offline {
            border-top: 3px solid var(--text-light);
        }
        
        .user-card-avatar {
            width: 60px;
            height: 60px;
            border-radius: 50%;
            margin: 0 auto 15px;
            background-size: cover;
            background-position: center;
            display: flex;
            align-items: center;
            justify-content: center;
            color: white;
            font-weight: bold;
            font-size: 1.5rem;
            border: 3px solid var(--primary);
        }
        
        .user-card-name {
            font-weight: 700;
            font-size: 1.1rem;
            margin-bottom: 5px;
        }
        
        .user-card-status {
            font-size: 0.8rem;
            color: var(--text-light);
            margin-bottom: 10px;
            display: flex;
            align-items: center;
            justify-content: center;
            gap: 5px;
        }
        
        .chat-with-btn {
            padding: 8px 16px;
            background: linear-gradient(135deg, var(--primary), var(--secondary));
            color: white;
            border: none;
            border-radius: var(--radius-sm);
            cursor: pointer;
            font-weight: 500;
            transition: var(--transition);
            width: 100%;
        }
        
        .chat-with-btn:hover {
            transform: translateY(-2px);
            box-shadow: var(--shadow-sm);
        }
        
        /* МОДАЛЬНЫЕ ОКНА */
        .modal-overlay {
            display: none;
            position: fixed;
            top: 0;
            left: 0;
            width: 100%;
            height: 100%;
            background: rgba(0, 0, 0, 0.7);
            backdrop-filter: blur(5px);
            -webkit-backdrop-filter: blur(5px);
            z-index: 1000;
            align-items: center;
            justify-content: center;
            padding: 20px;
        }
        
        .modal {
            background: var(--bg-light);
            border-radius: var(--radius-xl);
            padding: 30px;
            width: 100%;
            max-width: 500px;
            max-height: 90vh;
            overflow-y: auto;
            -webkit-overflow-scrolling: touch;
            border: 1px solid var(--border);
            box-shadow: var(--shadow-lg);
            animation: slideUp 0.3s ease-out;
        }
        
        .modal-header {
            display: flex;
            justify-content: space-between;
            align-items: center;
            margin-bottom: 25px;
        }
        
        .modal-title {
            font-size: 1.5rem;
            font-weight: 700;
            color: var(--text);
        }
        
        .close-modal {
            background: none;
            border: none;
            color: var(--text-light);
            font-size: 1.5rem;
            cursor: pointer;
            transition: var(--transition);
        }
        
        .close-modal:hover {
            color: var(--text);
        }
        
        .form-group {
            margin-bottom: 20px;
        }
        
        .form-label {
            display: block;
            margin-bottom: 8px;
            font-weight: 600;
            color: var(--text);
        }
        
        .form-input, .form-textarea {
            width: 100%;
            padding: 12px 16px;
            border: 1px solid var(--border);
            border-radius: var(--radius-md);
            background: var(--bg);
            color: var(--text);
            font-size: 1rem;
            transition: var(--transition);
        }
        
        .form-textarea {
            min-height: 100px;
            resize: vertical;
        }
        
        .form-input:focus, .form-textarea:focus {
            outline: none;
            border-color: var(--primary);
        }
        
        .btn {
            padding: 12px 24px;
            border: none;
            border-radius: var(--radius-md);
            cursor: pointer;
            font-weight: 600;
            transition: var(--transition);
            display: inline-flex;
            align-items: center;
            justify-content: center;
            gap: 8px;
        }
        
        .btn-primary {
            background: linear-gradient(135deg, var(--primary), var(--secondary));
            color: white;
        }
        
        .btn-primary:hover {
            transform: translateY(-2px);
            box-shadow: var(--shadow-md);
        }
        
        .btn-secondary {
            background: var(--bg-lighter);
            color: var(--text);
            border: 1px solid var(--border);
        }
        
        .btn-secondary:hover {
            background: var(--border);
        }
        
        /* АНИМАЦИИ */
        @keyframes fadeIn {
            from { opacity: 0; transform: translateY(10px); }
            to { opacity: 1; transform: translateY(0); }
        }
        
        @keyframes slideUp {
            from { opacity: 0; transform: translateY(20px); }
            to { opacity: 1; transform: translateY(0); }
        }
        
        /* АДАПТИВНОСТЬ */
        @media (max-width: 768px) {
            .app-container {
                flex-direction: column;
            }
            
            .sidebar {
                width: 100%;
                height: auto;
                max-height: 50vh;
            }
            
            .nav-content {
                max-height: calc(50vh - 200px);
            }
            
            .all-users-grid, .notes-grid {
                grid-template-columns: repeat(auto-fill, minmax(150px, 1fr));
                gap: 10px;
                padding: 10px;
            }
            
            .message {
                max-width: 90%;
            }
        }
        
        /* СКРОЛЛБАР */
        ::-webkit-scrollbar {
            width: 8px;
        }
        
        ::-webkit-scrollbar-track {
            background: transparent;
        }
        
        ::-webkit-scrollbar-thumb {
            background: var(--border);
            border-radius: 4px;
        }
        
        ::-webkit-scrollbar-thumb:hover {
            background: var(--border-light);
        }
        
        /* УТИЛИТЫ */
        .hidden {
            display: none !important;
        }
        
        .text-center {
            text-align: center;
        }
        
        .mt-2 { margin-top: 8px; }
        .mt-3 { margin-top: 12px; }
        .mt-4 { margin-top: 16px; }
        .mb-2 { margin-bottom: 8px; }
        .mb-3 { margin-bottom: 12px; }
        .mb-4 { margin-bottom: 16px; }
        
        .empty-state {
            text-align: center;
            padding: 40px 20px;
            color: var(--text-light);
        }
        
        .empty-state i {
            font-size: 3rem;
            margin-bottom: 15px;
            color: var(--border);
        }
        
        .empty-state h3 {
            font-size: 1.2rem;
            margin-bottom: 10px;
            color: var(--text);
        }
        
        .empty-state p {
            font-size: 0.9rem;
            max-width: 300px;
            margin: 0 auto;
        }
    </style>
</head>
<body>
    <div class="app-container">
        <!-- САЙДБАР -->
        <div class="sidebar">
            <div class="sidebar-header">
                <div class="logo">
                    <i class="fas fa-circle"></i>
                </div>
                <h1 class="app-name">AURA</h1>
            </div>
            
            <div class="user-info" onclick="openUserProfile('{{ username }}')">
                <div class="avatar" id="user-avatar"></div>
                <div class="user-details">
                    <div class="username">{{ username }}</div>
                    <div class="status">
                        <div class="status-dot"></div>
                        <span>Online</span>
                    </div>
                </div>
            </div>
            
            <div class="nav-tabs">
                <div class="nav-tab active" onclick="showTab('all')">все</div>
                <div class="nav-tab" onclick="showTab('notes')">заметки</div>
                <div class="nav-tab" onclick="showTab('chats')">чаты</div>
                <div class="nav-tab" onclick="showTab('channels')">каналы</div>
            </div>
            
            <div class="nav-content">
                <!-- Содержимое будет загружено динамически -->
                <div id="all-content" class="nav-tab-content active">
                    <div class="empty-state">
                        <i class="fas fa-users"></i>
                        <h3>Все пользователи</h3>
                        <p>Выберите пользователя для начала чата</p>
                    </div>
                </div>
            </div>
        </div>
        
        <!-- ОБЛАСТЬ ЧАТА -->
        <div class="chat-area">
            <div class="chat-header">
                <div class="chat-info">
                    <div>
                        <div class="chat-title" id="chat-title">AURA Messenger</div>
                        <div class="chat-subtitle" id="chat-subtitle">Добро пожаловать!</div>
                    </div>
                </div>
                <div class="chat-actions">
                    <button class="action-btn" onclick="openSettings()" title="Настройки">
                        <i class="fas fa-cog"></i>
                    </button>
                    <button class="action-btn" onclick="window.location.href='/logout'" title="Выйти">
                        <i class="fas fa-sign-out-alt"></i>
                    </button>
                </div>
            </div>
            
            <div class="messages-container" id="messages-container">
                <div class="empty-state">
                    <i class="fas fa-comment-dots"></i>
                    <h3>Начните общение</h3>
                    <p>Выберите чат из списка слева</p>
                </div>
            </div>
            
            <div class="input-area" id="input-area" style="display: none;">
                <div class="input-container">
                    <textarea class="msg-input" id="msg-input" placeholder="Написать сообщение..." rows="1"></textarea>
                    <button class="send-btn" onclick="sendMessage()">
                        <i class="fas fa-paper-plane"></i>
                    </button>
                </div>
            </div>
        </div>
    </div>

    <!-- МОДАЛЬНЫЕ ОКНА -->
    <div class="modal-overlay" id="user-profile-modal">
        <div class="modal">
            <div class="modal-header">
                <h2 class="modal-title">Профиль</h2>
                <button class="close-modal" onclick="closeModal('user-profile-modal')">
                    <i class="fas fa-times"></i>
                </button>
            </div>
            <div id="user-profile-content">
                <!-- Содержимое профиля -->
            </div>
        </div>
    </div>
    
    <div class="modal-overlay" id="settings-modal">
        <div class="modal">
            <div class="modal-header">
                <h2 class="modal-title">Настройки</h2>
                <button class="close-modal" onclick="closeModal('settings-modal')">
                    <i class="fas fa-times"></i>
                </button>
            </div>
            <div class="form-group">
                <label class="form-label">Тема</label>
                <div style="display: flex; gap: 10px;">
                    <button class="btn btn-secondary" onclick="setTheme('light')">🌞 Светлая</button>
                    <button class="btn btn-secondary" onclick="setTheme('dark')">🌙 Тёмная</button>
                </div>
            </div>
            <div class="form-group">
                <label class="form-label">Аватар</label>
                <div style="text-align: center; margin: 20px 0;">
                    <div class="avatar" id="settings-avatar" style="width: 100px; height: 100px; margin: 0 auto 20px; cursor: pointer;" onclick="document.getElementById('avatar-input').click()"></div>
                    <input type="file" id="avatar-input" accept="image/*" style="display: none;" onchange="uploadAvatar(this)">
                </div>
            </div>
            <div class="form-group">
                <label class="form-label">О себе</label>
                <textarea class="form-textarea" id="profile-description" placeholder="Расскажите о себе..."></textarea>
            </div>
            <button class="btn btn-primary" onclick="saveProfile()">Сохранить</button>
        </div>
    </div>
    
    <div class="modal-overlay" id="new-note-modal">
        <div class="modal">
            <div class="modal-header">
                <h2 class="modal-title">Новая заметка</h2>
                <button class="close-modal" onclick="closeModal('new-note-modal')">
                    <i class="fas fa-times"></i>
                </button>
            </div>
            <div class="form-group">
                <label class="form-label">Заголовок</label>
                <input type="text" class="form-input" id="note-title" placeholder="Название заметки">
            </div>
            <div class="form-group">
                <label class="form-label">Содержимое</label>
                <textarea class="form-textarea" id="note-content" placeholder="Текст заметки..." rows="6"></textarea>
            </div>
            <div class="form-group">
                <label class="form-label">Цвет</label>
                <div style="display: flex; gap: 10px; flex-wrap: wrap;">
                    <div class="color-option" style="width: 30px; height: 30px; border-radius: 50%; background: #667eea; cursor: pointer;" onclick="selectColor('#667eea')"></div>
                    <div class="color-option" style="width: 30px; height: 30px; border-radius: 50%; background: #8b5cf6; cursor: pointer;" onclick="selectColor('#8b5cf6')"></div>
                    <div class="color-option" style="width: 30px; height: 30px; border-radius: 50%; background: #10b981; cursor: pointer;" onclick="selectColor('#10b981')"></div>
                    <div class="color-option" style="width: 30px; height: 30px; border-radius: 50%; background: #f59e0b; cursor: pointer;" onclick="selectColor('#f59e0b')"></div>
                    <div class="color-option" style="width: 30px; height: 30px; border-radius: 50%; background: #ef4444; cursor: pointer;" onclick="selectColor('#ef4444')"></div>
                </div>
            </div>
            <button class="btn btn-primary" onclick="saveNote()">Сохранить заметку</button>
        </div>
    </div>

    <script src="https://cdnjs.cloudflare.com/ajax/libs/socket.io/4.7.2/socket.io.js"></script>
    <script>
        const socket = io();
        const currentUser = "{{ username }}";
        let currentRoom = null;
        let currentRoomType = null;
        let selectedColor = '#667eea';
        
        // Данные приложения
        const appData = {
            allUsers: {{ all_users|tojson }},
            channels: {{ user_channels|tojson }},
            chats: {{ personal_chats|tojson }},
            favorites: {{ favorites|tojson }},
            notes: {{ notes|tojson }}
        };
        
        // Инициализация
        document.addEventListener('DOMContentLoaded', function() {
            loadUserAvatar();
            showTab('all');
            renderAllUsers();
            
            // Настройка поля ввода
            const msgInput = document.getElementById('msg-input');
            msgInput.addEventListener('keydown', function(e) {
                if (e.key === 'Enter' && !e.shiftKey) {
                    e.preventDefault();
                    sendMessage();
                }
            });
            
            msgInput.addEventListener('input', function() {
                this.style.height = 'auto';
                this.style.height = Math.min(this.scrollHeight, 120) + 'px';
            });
        });
        
        // Загрузка аватарки пользователя
        function loadUserAvatar() {
            const userAvatar = document.getElementById('user-avatar');
            const settingsAvatar = document.getElementById('settings-avatar');
            
            fetch(`/api/user_info/${currentUser}`)
                .then(r => r.json())
                .then(data => {
                    if (data.success) {
                        const userInfo = data.user;
                        if (userInfo.avatar_path) {
                            userAvatar.style.backgroundImage = `url(${userInfo.avatar_path})`;
                            userAvatar.textContent = '';
                            settingsAvatar.style.backgroundImage = `url(${userInfo.avatar_path})`;
                            settingsAvatar.textContent = '';
                        } else {
                            userAvatar.style.backgroundColor = userInfo.avatar_color;
                            userAvatar.textContent = currentUser.slice(0, 2).toUpperCase();
                            settingsAvatar.style.backgroundColor = userInfo.avatar_color;
                            settingsAvatar.textContent = currentUser.slice(0, 2).toUpperCase();
                        }
                        
                        // Заполняем описание профиля
                        document.getElementById('profile-description').value = userInfo.profile_description || '';
                    }
                });
        }
        
        // Переключение вкладок
        function showTab(tabName) {
            // Обновляем активные вкладки
            document.querySelectorAll('.nav-tab').forEach(tab => tab.classList.remove('active'));
            document.querySelectorAll('.nav-tab-content').forEach(content => content.classList.remove('active'));
            
            document.querySelector(`.nav-tab[onclick="showTab('${tabName}')"]`).classList.add('active');
            
            const content = document.getElementById('all-content');
            content.innerHTML = '';
            
            switch(tabName) {
                case 'all':
                    renderAllUsers();
                    break;
                case 'notes':
                    renderNotes();
                    break;
                case 'chats':
                    renderChats();
                    break;
                case 'channels':
                    renderChannels();
                    break;
            }
        }
        
        // Рендеринг всех пользователей
        function renderAllUsers() {
            const content = document.getElementById('all-content');
            
            if (appData.allUsers.length === 0) {
                content.innerHTML = `
                    <div class="empty-state">
                        <i class="fas fa-users"></i>
                        <h3>Нет других пользователей</h3>
                        <p>Пригласите друзей в AURA Messenger</p>
                    </div>
                `;
                return;
            }
            
            content.innerHTML = `
                <div class="users-list">
                    ${appData.allUsers.map(user => `
                        <div class="user-item ${user.online ? 'online' : 'offline'}" onclick="startChatWith('${user.username}')">
                            <div class="item-avatar" style="background-color: ${user.color};">
                                ${user.avatar_path ? '' : user.username.slice(0, 2).toUpperCase()}
                            </div>
                            <div class="item-info">
                                <div class="item-name">${user.username}</div>
                                <div class="item-description">${user.profile_description || 'Нет описания'}</div>
                            </div>
                            ${user.online ? '<div class="online-badge"></div>' : ''}
                        </div>
                    `).join('')}
                </div>
            `;
            
            // Загружаем аватарки
            appData.allUsers.forEach(user => {
                if (user.avatar_path) {
                    const avatar = content.querySelector(`[onclick="startChatWith('${user.username}')"] .item-avatar`);
                    avatar.style.backgroundImage = `url(${user.avatar_path})`;
                    avatar.textContent = '';
                }
            });
        }
        
        // Рендеринг заметок
        function renderNotes() {
            const content = document.getElementById('all-content');
            
            content.innerHTML = `
                <div style="margin-bottom: 20px; display: flex; justify-content: space-between; align-items: center;">
                    <h3 style="font-size: 1.2rem; font-weight: 600;">Мои заметки</h3>
                    <button class="btn btn-primary" onclick="openNewNoteModal()">
                        <i class="fas fa-plus"></i> Новая заметка
                    </button>
                </div>
            `;
            
            if (appData.notes.length === 0) {
                content.innerHTML += `
                    <div class="empty-state">
                        <i class="fas fa-sticky-note"></i>
                        <h3>Нет заметок</h3>
                        <p>Создайте свою первую заметку</p>
                    </div>
                `;
                return;
            }
            
            content.innerHTML += `
                <div class="notes-list">
                    ${appData.notes.map(note => `
                        <div class="note-item ${note.is_pinned ? 'pinned' : ''}" onclick="openNote(${note.id})">
                            <div style="flex: 1;">
                                <div class="note-title">${note.title || 'Без названия'}</div>
                                <div class="note-content">${note.content || ''}</div>
                                <div class="note-meta">
                                    <span>${new Date(note.updated_at).toLocaleDateString('ru-RU')}</span>
                                    <span>${note.category}</span>
                                </div>
                            </div>
                            <div class="note-actions">
                                <button class="note-action-btn" onclick="togglePinNote(${note.id}, event)">
                                    <i class="fas fa-thumbtack"></i>
                                </button>
                                <button class="note-action-btn" onclick="deleteNote(${note.id}, event)">
                                    <i class="fas fa-trash"></i>
                                </button>
                            </div>
                        </div>
                    `).join('')}
                </div>
            `;
        }
        
        // Рендеринг чатов
        function renderChats() {
            const content = document.getElementById('all-content');
            
            if (appData.chats.length === 0) {
                content.innerHTML = `
                    <div class="empty-state">
                        <i class="fas fa-comments"></i>
                        <h3>Нет чатов</h3>
                        <p>Начните общение с другими пользователями</p>
                    </div>
                `;
                return;
            }
            
            content.innerHTML = `
                <div class="chats-list">
                    ${appData.chats.map(chatUser => `
                        <div class="chat-item" onclick="openPrivateChat('${chatUser}')">
                            <div class="item-avatar" style="background-color: #667eea;">
                                ${chatUser.slice(0, 2).toUpperCase()}
                            </div>
                            <div class="item-info">
                                <div class="item-name">${chatUser}</div>
                                <div class="item-status">Нажмите для чата</div>
                            </div>
                        </div>
                    `).join('')}
                </div>
            `;
        }
        
        // Рендеринг каналов
        function renderChannels() {
            const content = document.getElementById('all-content');
            
            if (appData.channels.length === 0) {
                content.innerHTML = `
                    <div class="empty-state">
                        <i class="fas fa-hashtag"></i>
                        <h3>Нет каналов</h3>
                        <p>Создайте или присоединитесь к каналу</p>
                    </div>
                `;
                return;
            }
            
            content.innerHTML = `
                <div class="channels-list">
                    ${appData.channels.map(channel => `
                        <div class="channel-item" onclick="openChannel('${channel.name}')">
                            <div class="item-avatar" style="background-color: ${channel.avatar_path ? 'transparent' : '#667eea'};">
                                ${channel.avatar_path ? '' : (channel.display_name || channel.name).slice(0, 2).toUpperCase()}
                            </div>
                            <div class="item-info">
                                <div class="item-name">${channel.display_name || channel.name}</div>
                                <div class="item-description">${channel.description || 'Без описания'}</div>
                            </div>
                        </div>
                    `).join('')}
                </div>
            `;
            
            // Загружаем аватарки каналов
            appData.channels.forEach(channel => {
                if (channel.avatar_path) {
                    const avatar = content.querySelector(`[onclick="openChannel('${channel.name}')"] .item-avatar`);
                    avatar.style.backgroundImage = `url(${channel.avatar_path})`;
                    avatar.textContent = '';
                }
            });
        }
        
        // Начать чат с пользователем
        function startChatWith(username) {
            openPrivateChat(username);
        }
        
        // Открыть приватный чат
        function openPrivateChat(username) {
            currentRoom = `private_${[currentUser, username].sort().join('_')}`;
            currentRoomType = 'private';
            
            document.getElementById('chat-title').textContent = username;
            document.getElementById('chat-subtitle').textContent = 'Личный чат';
            document.getElementById('input-area').style.display = 'block';
            
            const messagesContainer = document.getElementById('messages-container');
            messagesContainer.innerHTML = '<div class="empty-state"><i class="fas fa-spinner fa-spin"></i><h3>Загрузка сообщений...</h3></div>';
            
            // Загружаем сообщения
            loadMessages(currentRoom);
            
            // Присоединяемся к комнате
            socket.emit('join', { room: currentRoom });
        }
        
        // Открыть канал
        function openChannel(channelName) {
            currentRoom = `channel_${channelName}`;
            currentRoomType = 'channel';
            
            // Получаем информацию о канале
            fetch(`/api/channel_info/${channelName}`)
                .then(r => r.json())
                .then(data => {
                    if (data.success) {
                        document.getElementById('chat-title').textContent = data.data.display_name;
                        document.getElementById('chat-subtitle').textContent = data.data.description || 'Канал';
                    }
                });
            
            document.getElementById('input-area').style.display = 'block';
            
            const messagesContainer = document.getElementById('messages-container');
            messagesContainer.innerHTML = '<div class="empty-state"><i class="fas fa-spinner fa-spin"></i><h3>Загрузка сообщений...</h3></div>';
            
            // Загружаем сообщения
            loadMessages(currentRoom);
            
            // Присоединяемся к комнате
            socket.emit('join', { room: currentRoom });
        }
        
        // Загрузить сообщения
        function loadMessages(room) {
            fetch(`/api/get_messages/${room}`)
                .then(r => r.json())
                .then(messages => {
                    const messagesContainer = document.getElementById('messages-container');
                    messagesContainer.innerHTML = '';
                    
                    if (messages.length === 0) {
                        messagesContainer.innerHTML = `
                            <div class="empty-state">
                                <i class="fas fa-comment-dots"></i>
                                <h3>Нет сообщений</h3>
                                <p>Начните общение первым</p>
                            </div>
                        `;
                        return;
                    }
                    
                    messages.forEach(msg => {
                        addMessageToChat(msg);
                    });
                    
                    messagesContainer.scrollTop = messagesContainer.scrollHeight;
                });
        }
        
        // Добавить сообщение в чат
        function addMessageToChat(data) {
            const messagesContainer = document.getElementById('messages-container');
            
            // Убираем состояние "пусто"
            const emptyState = messagesContainer.querySelector('.empty-state');
            if (emptyState) {
                emptyState.remove();
            }
            
            const message = document.createElement('div');
            message.className = `message ${data.user === currentUser ? 'own' : 'other'}`;
            
            const avatar = document.createElement('div');
            avatar.className = 'message-avatar';
            avatar.style.cursor = 'pointer';
            avatar.onclick = () => openUserProfile(data.user);
            
            // Получаем информацию о пользователе для аватарки
            fetch(`/api/user_info/${data.user}`)
                .then(r => r.json())
                .then(userInfo => {
                    if (userInfo.success) {
                        if (userInfo.user.avatar_path) {
                            avatar.style.backgroundImage = `url(${userInfo.user.avatar_path})`;
                            avatar.textContent = '';
                        } else {
                            avatar.style.backgroundColor = userInfo.user.avatar_color;
                            avatar.textContent = data.user.slice(0, 2).toUpperCase();
                        }
                    }
                });
            
            const content = document.createElement('div');
            content.className = 'message-content';
            
            if (data.user !== currentUser) {
                const sender = document.createElement('div');
                sender.className = 'message-sender';
                sender.textContent = data.user;
                content.appendChild(sender);
            }
            
            if (data.message) {
                const text = document.createElement('div');
                text.className = 'message-text';
                text.innerHTML = data.message.replace(/\\n/g, '<br>');
                content.appendChild(text);
            }
            
            const time = document.createElement('div');
            time.className = 'message-time';
            time.textContent = data.timestamp || new Date().toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' });
            content.appendChild(time);
            
            message.appendChild(avatar);
            message.appendChild(content);
            messagesContainer.appendChild(message);
            messagesContainer.scrollTop = messagesContainer.scrollHeight;
        }
        
        // Отправить сообщение
        function sendMessage() {
            const input = document.getElementById('msg-input');
            const msg = input.value.trim();
            
            if (!msg || !currentRoom) return;
            
            const messageData = {
                message: msg,
                room: currentRoom,
                type: currentRoomType
            };
            
            socket.emit('message', messageData);
            
            // Очищаем поле ввода
            input.value = '';
            input.style.height = 'auto';
        }
        
        // Socket events
        socket.on('message', (data) => {
            if (data.room === currentRoom) {
                addMessageToChat(data);
            }
        });
        
        // Модальные окна
        function openModal(modalId) {
            document.getElementById(modalId).style.display = 'flex';
        }
        
        function closeModal(modalId) {
            document.getElementById(modalId).style.display = 'none';
        }
        
        function openSettings() {
            openModal('settings-modal');
        }
        
        function openUserProfile(username) {
            fetch(`/api/user_info/${username}`)
                .then(r => r.json())
                .then(data => {
                    if (data.success) {
                        const user = data.user;
                        const content = document.getElementById('user-profile-content');
                        
                        content.innerHTML = `
                            <div style="text-align: center; margin-bottom: 30px;">
                                <div class="avatar" style="width: 100px; height: 100px; margin: 0 auto 20px; background-color: ${user.avatar_color};">
                                    ${user.avatar_path ? '' : username.slice(0, 2).toUpperCase()}
                                </div>
                                <h3 style="font-size: 1.5rem; font-weight: 700; margin-bottom: 10px;">${username}</h3>
                                <div style="color: var(--text-light); margin-bottom: 20px;">
                                    <span style="display: inline-flex; align-items: center; gap: 5px;">
                                        <div style="width: 10px; height: 10px; border-radius: 50%; background: ${user.online ? '#10b981' : '#a0aec0'};"></div>
                                        ${user.online ? 'Online' : 'Offline'}
                                    </span>
                                </div>
                            </div>
                            <div class="form-group">
                                <label class="form-label">О себе</label>
                                <div style="background: var(--bg); padding: 15px; border-radius: var(--radius-md); border: 1px solid var(--border); min-height: 100px;">
                                    ${user.profile_description || 'Пользователь не добавил информацию о себе'}
                                </div>
                            </div>
                            <div class="form-group">
                                <label class="form-label">Дата регистрации</label>
                                <div style="color: var(--text-light);">
                                    ${new Date(user.created_at).toLocaleDateString('ru-RU')}
                                </div>
                            </div>
                            ${username === currentUser ? '' : `
                                <button class="btn btn-primary" onclick="startChatWith('${username}'); closeModal('user-profile-modal');" style="width: 100%;">
                                    <i class="fas fa-comment"></i> Написать сообщение
                                </button>
                            `}
                        `;
                        
                        if (user.avatar_path) {
                            const avatar = content.querySelector('.avatar');
                            avatar.style.backgroundImage = `url(${user.avatar_path})`;
                            avatar.textContent = '';
                        }
                        
                        openModal('user-profile-modal');
                    }
                });
        }
        
        // Настройки темы
        function setTheme(theme) {
            fetch('/api/set_theme', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ theme: theme })
            })
            .then(r => r.json())
            .then(data => {
                if (data.success) {
                    document.documentElement.setAttribute('data-theme', theme);
                    closeModal('settings-modal');
                }
            });
        }
        
        // Загрузка аватарки
        function uploadAvatar(input) {
            const file = input.files[0];
            if (!file) return;
            
            const formData = new FormData();
            formData.append('avatar', file);
            
            fetch('/api/upload_avatar', {
                method: 'POST',
                body: formData
            })
            .then(r => r.json())
            .then(data => {
                if (data.success) {
                    loadUserAvatar();
                    alert('Аватарка обновлена!');
                } else {
                    alert('Ошибка загрузки аватарки');
                }
            });
        }
        
        // Сохранение профиля
        function saveProfile() {
            const description = document.getElementById('profile-description').value;
            
            fetch('/api/update_profile', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ description: description })
            })
            .then(r => r.json())
            .then(data => {
                if (data.success) {
                    closeModal('settings-modal');
                    alert('Профиль сохранен!');
                }
            });
        }
        
        // Заметки
        function openNewNoteModal() {
            selectedColor = '#667eea';
            document.getElementById('note-title').value = '';
            document.getElementById('note-content').value = '';
            openModal('new-note-modal');
        }
        
        function selectColor(color) {
            selectedColor = color;
            document.querySelectorAll('.color-option').forEach(opt => {
                opt.style.border = 'none';
            });
            event.target.style.border = '3px solid white';
        }
        
        function saveNote() {
            const title = document.getElementById('note-title').value.trim();
            const content = document.getElementById('note-content').value.trim();
            
            if (!title && !content) {
                alert('Заполните заголовок или содержимое заметки');
                return;
            }
            
            fetch('/api/add_note', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({
                    title: title,
                    content: content,
                    color: selectedColor
                })
            })
            .then(r => r.json())
            .then(data => {
                if (data.success) {
                    closeModal('new-note-modal');
                    alert('Заметка сохранена!');
                    // Обновляем список заметок
                    fetch('/api/get_notes')
                        .then(r => r.json())
                        .then(notesData => {
                            if (notesData.success) {
                                appData.notes = notesData.notes;
                                if (document.querySelector('.nav-tab.active').textContent.includes('заметки')) {
                                    renderNotes();
                                }
                            }
                        });
                }
            });
        }
        
        function openNote(noteId) {
            const note = appData.notes.find(n => n.id === noteId);
            if (note) {
                openModal('new-note-modal');
                document.getElementById('note-title').value = note.title || '';
                document.getElementById('note-content').value = note.content || '';
                selectedColor = note.color;
                
                // TODO: Режим редактирования
            }
        }
        
        function togglePinNote(noteId, event) {
            event.stopPropagation();
            
            fetch(`/api/toggle_pin_note/${noteId}`, {
                method: 'POST'
            })
            .then(r => r.json())
            .then(data => {
                if (data.success) {
                    // Обновляем данные
                    const note = appData.notes.find(n => n.id === noteId);
                    if (note) {
                        note.is_pinned = data.pinned;
                        renderNotes();
                    }
                }
            });
        }
        
        function deleteNote(noteId, event) {
            event.stopPropagation();
            
            if (!confirm('Удалить эту заметку?')) return;
            
            fetch(`/api/delete_note/${noteId}`, {
                method: 'DELETE'
            })
            .then(r => r.json())
            .then(data => {
                if (data.success) {
                    // Удаляем из данных
                    appData.notes = appData.notes.filter(n => n.id !== noteId);
                    renderNotes();
                }
            });
        }
        
        // Закрытие модальных окон при клике вне их
        document.querySelectorAll('.modal-overlay').forEach(modal => {
            modal.addEventListener('click', function(e) {
                if (e.target === this) {
                    this.style.display = 'none';
                }
            });
        });
        
        // Обработка клавиши Escape
        document.addEventListener('keydown', function(e) {
            if (e.key === 'Escape') {
                document.querySelectorAll('.modal-overlay').forEach(modal => {
                    modal.style.display = 'none';
                });
            }
        });
    </script>
</body>
</html>
        ''', 
        username=username,
        theme=theme,
        all_users=all_users,
        user_channels=user_channels,
        personal_chats=personal_chats,
        favorites=favorites,
        notes=notes)

    # === API Routes ===
    @app.route('/api/user_info/<username>')
    def user_info_handler(username):
        if 'username' not in session: 
            return jsonify({'success': False, 'error': 'Не авторизован'})
        
        user = get_user(username)
        if user:
            return jsonify({
                'success': True,
                'user': {
                    'username': user['username'],
                    'online': user['is_online'],
                    'avatar_color': user['avatar_color'],
                    'avatar_path': user['avatar_path'],
                    'theme': user['theme'],
                    'profile_description': user['profile_description'],
                    'created_at': user['created_at']
                }
            })
        return jsonify({'success': False, 'error': 'Пользователь не найден'})

    @app.route('/api/upload_avatar', methods=['POST'])
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

    @app.route('/api/update_profile', methods=['POST'])
    def update_profile_handler():
        if 'username' not in session: 
            return jsonify({'success': False, 'error': 'Не авторизован'})
        
        description = request.json.get('description', '').strip()
        success = update_profile_description(session['username'], description)
        if success:
            return jsonify({'success': True})
        return jsonify({'success': False, 'error': 'Ошибка обновления описания'})

    @app.route('/api/set_theme', methods=['POST'])
    def set_theme_handler():
        if 'username' not in session: 
            return jsonify({'success': False, 'error': 'Не авторизован'})
        theme = request.json.get('theme', 'dark')
        if theme not in ['light', 'dark', 'auto']: 
            return jsonify({'success': False, 'error': 'Неверная тема'})
        with sqlite3.connect('messenger.db') as conn:
            c = conn.cursor()
            c.execute('UPDATE users SET theme = ? WHERE username = ?', (theme, session['username']))
            conn.commit()
        return jsonify({'success': True})

    @app.route('/api/get_messages/<room>')
    def get_messages_handler(room):
        if 'username' not in session:
            return jsonify({'error': 'auth'})
        messages = get_messages_for_room(room)
        return jsonify(messages)

    @app.route('/api/channel_info/<channel_name>')
    def channel_info_handler(channel_name):
        if 'username' not in session: 
            return jsonify({'success': False, 'error': 'Не авторизован'})
        info = get_channel_info(channel_name)
        if info:
            info['is_member'] = is_channel_member(channel_name, session['username'])
            info['members'] = get_channel_members(channel_name)
            return jsonify({'success': True, 'data': info})
        return jsonify({'success': False, 'error': 'Канал не найден'})

    @app.route('/api/add_note', methods=['POST'])
    def add_note_handler():
        if 'username' not in session:
            return jsonify({'success': False, 'error': 'Не авторизован'})
        
        title = request.json.get('title', '').strip()
        content = request.json.get('content', '').strip()
        color = request.json.get('color', '#667eea')
        category = request.json.get('category', 'notes')
        
        note_id = add_note(session['username'], title, content, category, color)
        if note_id:
            return jsonify({'success': True, 'id': note_id})
        return jsonify({'success': False, 'error': 'Ошибка создания заметки'})

    @app.route('/api/get_notes')
    def get_notes_handler():
        if 'username' not in session:
            return jsonify({'success': False, 'error': 'Не авторизован'})
        
        category = request.args.get('category', None)
        notes = get_notes(session['username'], category)
        return jsonify({'success': True, 'notes': notes})

    @app.route('/api/delete_note/<int:note_id>', methods=['DELETE'])
    def delete_note_handler(note_id):
        if 'username' not in session:
            return jsonify({'success': False, 'error': 'Не авторизован'})
        
        if delete_note(note_id, session['username']):
            return jsonify({'success': True})
        return jsonify({'success': False, 'error': 'Не удалось удалить заметку'})

    @app.route('/api/toggle_pin_note/<int:note_id>', methods=['POST'])
    def toggle_pin_note_handler(note_id):
        if 'username' not in session:
            return jsonify({'success': False, 'error': 'Не авторизован'})
        
        new_state = toggle_pin_note(note_id, session['username'])
        if new_state is not None:
            return jsonify({'success': True, 'pinned': new_state})
        return jsonify({'success': False, 'error': 'Не удалось закрепить/открепить заметку'})

    @app.route('/static/<path:filename>')
    def static_files(filename):
        return send_from_directory('static', filename)

    @app.route('/health')
    def health_check():
        return jsonify({'status': 'healthy', 'service': 'AURA Messenger'})

    @app.errorhandler(404)
    def not_found(e):
        return redirect('/')

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
            recipient
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
        
        emit('message', message_data, room=room)

    return app

app = create_app()
socketio = app.extensions['socketio']

if __name__ == '__main__':
    port = int(os.environ.get('PORT', 5000))
    socketio.run(app, host='0.0.0.0', port=port, debug=True, allow_unsafe_werkzeug=True)
