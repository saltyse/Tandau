# web_messenger.py - Tandau Messenger (единый файл) с эмодзи
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
                    allow_messages BOOLEAN DEFAULT TRUE,
                    avatar_path TEXT
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
            c.execute('SELECT id, name, display_name, description, created_by, is_private, allow_messages, avatar_path FROM channels WHERE name = ?', (channel_name,))
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
                    'avatar_path': row[7]
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
                SELECT c.name, c.display_name, c.description, c.is_private, c.allow_messages, c.created_by, c.avatar_path
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
                'avatar_path': row[6]
            } for row in c.fetchall()]

    # === API Routes ===
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
                    -webkit-backdrop-filter: blur(10px);
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
                                Регистрируясь, вы соглашаетесь с нашими <a href="#" onclick="openTermsModal(); return false;">Условиями использования</a>
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
                        <div style="max-width: 800px; margin: 0 auto; line-height: 1.6;">
                            <h3 style="margin-bottom: 20px; color: #333;">Условия использования Tandau Messenger</h3>
                            <p style="margin-bottom: 15px;">Дата вступления в силу: 6 декабря 2025 г.</p>
                            
                            <h4 style="margin: 25px 0 15px 0; color: #444;">Регистрация и учетная запись</h4>
                            <p style="margin-bottom: 15px;">Регистрируясь в Tandau Messenger, вы подтверждаете что:</p>
                            <ul style="margin-bottom: 25px; padding-left: 20px;">
                                <li style="margin-bottom: 8px;">Вы достигли возраста 14 лет на момент регистрации</li>
                                <li style="margin-bottom: 8px;">Предоставленная информация является точной и достоверной</li>
                                <li style="margin-bottom: 8px;">Вы несете ответственность за сохранность учетных данных</li>
                            </ul>
                            
                            <h4 style="margin: 25px 0 15px 0; color: #444;">Правила общения</h4>
                            <p style="margin-bottom: 15px;">В Tandau Messenger запрещается:</p>
                            <ul style="margin-bottom: 25px; padding-left: 20px;">
                                <li style="margin-bottom: 8px;">Распространение спама и вредоносного контента</li>
                                <li style="margin-bottom: 8px;">Нарушение прав других пользователей</li>
                                <li style="margin-bottom: 8px;">Использование для противоправной деятельности</li>
                                <li style="margin-bottom: 8px;">Создание фишинговых или мошеннических аккаунтов</li>
                            </ul>
                            
                            <h4 style="margin: 25px 0 15px 0; color: #444;">Контактная информация</h4>
                            <p style="margin-bottom: 15px;">По всем вопросам, связанным с условиями использования:</p>
                            <p style="margin-bottom: 25px; padding: 15px; background: #f5f5f5; border-radius: 8px;">
                                <i class="fab fa-vk"></i> https://vk.com/rsaltyyt
                            </p>
                            
                            <h4 style="margin: 25px 0 15px 0; color: #444;">Изменения условий</h4>
                            <p style="margin-bottom: 25px;">Мы оставляем за собой право вносить изменения в Условия использования. Актуальная версия всегда доступна на этой странице.</p>
                            
                            <div style="margin-top: 30px; padding-top: 20px; border-top: 1px solid #eee;">
                                <p style="margin-bottom: 15px;">Последнее обновление: 6 декабря 2025 года</p>
                            </div>
                        </div>
                    </div>
                </div>
            </div>

            <script>
                function showTab(tabName) {
                    // Переключение вкладок
                    document.querySelectorAll('.auth-tab').forEach(tab => {
                        tab.classList.remove('active');
                    });
                    document.querySelectorAll('.auth-form').forEach(form => {
                        form.classList.remove('active');
                    });
                    
                    if (tabName === 'login') {
                        document.querySelectorAll('.auth-tab')[0].classList.add('active');
                        document.getElementById('login-form').classList.add('active');
                    } else {
                        document.querySelectorAll('.auth-tab')[1].classList.add('active');
                        document.getElementById('register-form').classList.add('active');
                    }
                }
                
                function togglePassword(inputId) {
                    const input = document.getElementById(inputId);
                    const toggleBtn = input.nextElementSibling;
                    
                    if (input.type === 'password') {
                        input.type = 'text';
                        toggleBtn.innerHTML = '<i class="fas fa-eye-slash"></i>';
                    } else {
                        input.type = 'password';
                        toggleBtn.innerHTML = '<i class="fas fa-eye"></i>';
                    }
                }
                
                function showAlert(message, type = 'error') {
                    const alertDiv = document.getElementById('alert');
                    alertDiv.textContent = message;
                    alertDiv.className = 'alert alert-' + type;
                    alertDiv.style.display = 'block';
                    
                    setTimeout(() => {
                        alertDiv.style.display = 'none';
                    }, 5000);
                }
                
                async function login() {
                    const username = document.getElementById('login-username').value.trim();
                    const password = document.getElementById('login-password').value.trim();
                    const btn = document.getElementById('login-btn');
                    const originalText = btn.innerHTML;
                    
                    if (!username || !password) {
                        showAlert('Введите логин и пароль');
                        return;
                    }
                    
                    btn.innerHTML = '<span class="loader"></span>';
                    btn.disabled = true;
                    
                    try {
                        const response = await fetch('/login', {
                            method: 'POST',
                            headers: { 'Content-Type': 'application/json' },
                            body: JSON.stringify({ username, password })
                        });
                        
                        const data = await response.json();
                        
                        if (data.success) {
                            window.location.href = '/chat';
                        } else {
                            showAlert(data.error || 'Ошибка входа');
                            btn.innerHTML = originalText;
                            btn.disabled = false;
                        }
                    } catch (error) {
                        showAlert('Ошибка соединения с сервером');
                        btn.innerHTML = originalText;
                        btn.disabled = false;
                    }
                }
                
                async function register() {
                    const username = document.getElementById('register-username').value.trim();
                    const password = document.getElementById('register-password').value.trim();
                    const confirmPassword = document.getElementById('register-confirm').value.trim();
                    const btn = document.getElementById('register-btn');
                    const originalText = btn.innerHTML;
                    
                    if (!username || !password || !confirmPassword) {
                        showAlert('Заполните все поля');
                        return;
                    }
                    
                    if (username.length < 3 || username.length > 20) {
                        showAlert('Логин должен быть от 3 до 20 символов');
                        return;
                    }
                    
                    if (password.length < 4) {
                        showAlert('Пароль должен быть не менее 4 символов');
                        return;
                    }
                    
                    if (password !== confirmPassword) {
                        showAlert('Пароли не совпадают');
                        return;
                    }
                    
                    btn.innerHTML = '<span class="loader"></span>';
                    btn.disabled = true;
                    
                    try {
                        const response = await fetch('/register', {
                            method: 'POST',
                            headers: { 'Content-Type': 'application/json' },
                            body: JSON.stringify({ username, password })
                        });
                        
                        const data = await response.json();
                        
                        if (data.success) {
                            window.location.href = '/chat';
                        } else {
                            showAlert(data.error || 'Ошибка регистрации');
                            btn.innerHTML = originalText;
                            btn.disabled = false;
                        }
                    } catch (error) {
                        showAlert('Ошибка соединения с сервером');
                        btn.innerHTML = originalText;
                        btn.disabled = false;
                    }
                }
                
                function openTermsModal() {
                    document.getElementById('terms-modal').style.display = 'flex';
                }
                
                function closeTermsModal() {
                    document.getElementById('terms-modal').style.display = 'none';
                }
                
                // Закрытие модального окна при клике вне его
                document.addEventListener('click', function(event) {
                    const modal = document.getElementById('terms-modal');
                    if (event.target === modal) {
                        closeTermsModal();
                    }
                });
                
                // Закрытие по клавише ESC
                document.addEventListener('keydown', function(event) {
                    if (event.key === 'Escape') {
                        closeTermsModal();
                    }
                });
            </script>
        </body>
        </html>'''

    @app.route('/login', methods=['POST'])
    def login_handler():
        data = request.get_json()
        if not data:
            return jsonify({'success': False, 'error': 'Неверный формат данных'})
        
        username = data.get('username', '').strip()
        password = data.get('password', '').strip()
        
        if not username or not password:
            return jsonify({'success': False, 'error': 'Заполните все поля'})
        
        user = verify_user(username, password)
        if user:
            session['username'] = username
            update_online(username, True)
            return jsonify({'success': True})
        
        return jsonify({'success': False, 'error': 'Неверный логин или пароль'})

    @app.route('/register', methods=['POST'])
    def register_handler():
        data = request.get_json()
        if not data:
            return jsonify({'success': False, 'error': 'Неверный формат данных'})
        
        username = data.get('username', '').strip()
        password = data.get('password', '').strip()
        
        if not username or not password:
            return jsonify({'success': False, 'error': 'Заполните все поля'})
        
        if len(username) < 3 or len(username) > 20:
            return jsonify({'success': False, 'error': 'Логин должен быть от 3 до 20 символов'})
        
        if len(password) < 4:
            return jsonify({'success': False, 'error': 'Пароль должен быть не менее 4 символов'})
        
        success, message = create_user(username, password)
        if success:
            session['username'] = username
            update_online(username, True)
            return jsonify({'success': True})
        
        return jsonify({'success': False, 'error': message})

    @app.route('/chat')
    def chat_handler():
        if 'username' not in session:
            return redirect('/')
        
        username = session['username']
        
        # Получаем информацию о пользователе для передачи в шаблон
        user_info = get_user(username)
        if not user_info:
            return redirect('/logout')
        
        # Здесь будет основной интерфейс мессенджера
        return f'''
        <!DOCTYPE html>
        <html lang="ru">
        <head>
            <meta charset="UTF-8">
            <meta name="viewport" content="width=device-width, initial-scale=1.0">
            <title>Tandau Messenger - {username}</title>
            <link rel="stylesheet" href="https://cdnjs.cloudflare.com/ajax/libs/font-awesome/6.5.0/css/all.min.css">
            <style>
                * {{
                    margin: 0;
                    padding: 0;
                    box-sizing: border-box;
                    font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif;
                }}
                
                :root {{
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
                }}
                
                body {{
                    background: var(--bg);
                    color: var(--text);
                    height: 100vh;
                    overflow: hidden;
                }}
                
                .app-container {{
                    display: flex;
                    height: 100vh;
                }}
                
                .sidebar {{
                    width: 280px;
                    background: var(--bg-light);
                    border-right: 1px solid var(--border);
                    display: flex;
                    flex-direction: column;
                    transition: transform 0.3s ease;
                }}
                
                .sidebar-header {{
                    padding: 20px;
                    border-bottom: 1px solid var(--border);
                    display: flex;
                    align-items: center;
                    gap: 12px;
                }}
                
                .logo-placeholder {{
                    width: 40px;
                    height: 40px;
                    border-radius: 12px;
                    background: linear-gradient(135deg, #667eea, #764ba2);
                    display: flex;
                    align-items: center;
                    justify-content: center;
                    color: white;
                    font-size: 20px;
                    font-weight: bold;
                }}
                
                .app-title {{
                    font-size: 1.5rem;
                    font-weight: 800;
                    color: var(--text);
                }}
                
                .user-info {{
                    padding: 20px;
                    border-bottom: 1px solid var(--border);
                    display: flex;
                    align-items: center;
                    gap: 12px;
                }}
                
                .avatar {{
                    width: 50px;
                    height: 50px;
                    border-radius: 50%;
                    background: var(--accent);
                    display: flex;
                    align-items: center;
                    justify-content: center;
                    color: white;
                    font-weight: bold;
                    font-size: 1.2rem;
                    background-size: cover;
                    background-position: center;
                    cursor: pointer;
                }}
                
                .user-details {{
                    flex: 1;
                }}
                
                .user-status {{
                    display: flex;
                    align-items: center;
                    gap: 6px;
                    font-size: 0.85rem;
                    color: var(--text-light);
                    margin-top: 4px;
                }}
                
                .status-dot {{
                    width: 8px;
                    height: 8px;
                    background: #10b981;
                    border-radius: 50%;
                }}
                
                .channel-btn {{
                    background: var(--bg);
                    border: 1px solid var(--border);
                    width: 36px;
                    height: 36px;
                    border-radius: 50%;
                    display: flex;
                    align-items: center;
                    justify-content: center;
                    color: var(--text);
                    cursor: pointer;
                    transition: var(--transition);
                }}
                
                .channel-btn:hover {{
                    background: var(--primary-light);
                    color: white;
                    border-color: var(--primary-light);
                }}
                
                .nav {{
                    flex: 1;
                    overflow-y: auto;
                    padding: 20px;
                }}
                
                .nav-title {{
                    display: flex;
                    justify-content: space-between;
                    align-items: center;
                    margin-bottom: 15px;
                    font-weight: 600;
                    color: var(--text-light);
                    font-size: 0.9rem;
                    text-transform: uppercase;
                    letter-spacing: 0.5px;
                }}
                
                .add-btn {{
                    background: none;
                    border: none;
                    color: var(--accent);
                    cursor: pointer;
                    font-size: 1rem;
                    width: 24px;
                    height: 24px;
                    border-radius: 6px;
                    display: flex;
                    align-items: center;
                    justify-content: center;
                }}
                
                .add-btn:hover {{
                    background: var(--bg);
                }}
                
                .nav-item {{
                    display: flex;
                    align-items: center;
                    gap: 12px;
                    padding: 12px 15px;
                    border-radius: var(--radius-sm);
                    cursor: pointer;
                    transition: var(--transition);
                    margin-bottom: 5px;
                    user-select: none;
                }}
                
                .nav-item:hover {{
                    background: var(--bg);
                }}
                
                .nav-item.active {{
                    background: var(--primary-light);
                    color: white;
                }}
                
                .nav-item.active:hover {{
                    background: var(--primary);
                }}
                
                .chat-area {{
                    flex: 1;
                    display: flex;
                    flex-direction: column;
                    background: var(--bg);
                }}
                
                .chat-header {{
                    padding: 20px;
                    background: var(--bg-light);
                    border-bottom: 1px solid var(--border);
                    display: flex;
                    align-items: center;
                    gap: 15px;
                }}
                
                .back-btn {{
                    display: none;
                    background: none;
                    border: none;
                    font-size: 1.2rem;
                    color: var(--text);
                    cursor: pointer;
                }}
                
                .channel-header-avatar {{
                    width: 40px;
                    height: 40px;
                    border-radius: 50%;
                    background: var(--primary);
                    display: flex;
                    align-items: center;
                    justify-content: center;
                    color: white;
                    font-weight: bold;
                    background-size: cover;
                    background-position: center;
                    cursor: pointer;
                }}
                
                .messages {{
                    flex: 1;
                    overflow-y: auto;
                    padding: 20px;
                    background: var(--bg);
                }}
                
                .message-container {{
                    display: flex;
                    flex-direction: column;
                    gap: 20px;
                }}
                
                .message {{
                    display: flex;
                    gap: 12px;
                    max-width: 70%;
                }}
                
                .message.own {{
                    margin-left: auto;
                    flex-direction: row-reverse;
                }}
                
                .message-avatar {{
                    width: 36px;
                    height: 36px;
                    border-radius: 50%;
                    flex-shrink: 0;
                    display: flex;
                    align-items: center;
                    justify-content: center;
                    color: white;
                    font-weight: bold;
                    font-size: 0.9rem;
                    background-size: cover;
                    background-position: center;
                }}
                
                .message-content {{
                    background: var(--bg-light);
                    padding: 12px 16px;
                    border-radius: 18px;
                    border-top-left-radius: 4px;
                    box-shadow: var(--shadow);
                    max-width: 100%;
                }}
                
                .message.own .message-content {{
                    background: var(--primary);
                    color: white;
                    border-top-left-radius: 18px;
                    border-top-right-radius: 4px;
                }}
                
                .message-sender {{
                    font-weight: 600;
                    font-size: 0.9rem;
                    margin-bottom: 4px;
                }}
                
                .message.own .message-sender {{
                    display: none;
                }}
                
                .message-text {{
                    word-wrap: break-word;
                    line-height: 1.4;
                }}
                
                .message-time {{
                    font-size: 0.75rem;
                    color: var(--text-light);
                    margin-top: 4px;
                    text-align: right;
                }}
                
                .message.own .message-time {{
                    color: rgba(255, 255, 255, 0.8);
                }}
                
                .message-file {{
                    margin-top: 10px;
                }}
                
                .message-file img, .message-file video {{
                    max-width: 200px;
                    max-height: 150px;
                    border-radius: 8px;
                    cursor: pointer;
                }}
                
                .input-area {{
                    padding: 20px;
                    background: var(--bg-light);
                    border-top: 1px solid var(--border);
                }}
                
                .input-row {{
                    display: flex;
                    gap: 10px;
                    align-items: flex-end;
                }}
                
                .emoji-btn, .attachment-btn {{
                    background: var(--bg);
                    border: 1px solid var(--border);
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
                
                .emoji-btn:hover, .attachment-btn:hover {{
                    background: var(--primary-light);
                    color: white;
                    border-color: var(--primary-light);
                }}
                
                .msg-input {{
                    flex: 1;
                    padding: 12px 16px;
                    border: 1px solid var(--border);
                    border-radius: 22px;
                    background: var(--bg);
                    color: var(--text);
                    font-size: 1rem;
                    resize: none;
                    max-height: 120px;
                    min-height: 44px;
                    line-height: 1.4;
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
                    transition: var(--transition);
                }}
                
                .send-btn:hover {{
                    background: var(--primary-dark);
                    transform: translateY(-2px);
                }}
                
                .send-btn:active {{
                    transform: translateY(0);
                }}
                
                .logout-btn {{
                    margin: 20px;
                    padding: 12px;
                    background: #dc3545;
                    color: white;
                    border: none;
                    border-radius: var(--radius-sm);
                    cursor: pointer;
                    font-weight: 600;
                    display: flex;
                    align-items: center;
                    justify-content: center;
                    gap: 8px;
                }}
                
                .logout-btn:hover {{
                    background: #c82333;
                }}
                
                /* Эмодзи */
                .emoji-in-message {{
                    font-size: 1.2em;
                    vertical-align: middle;
                }}
                
                /* Адаптивность */
                @media (max-width: 768px) {{
                    .sidebar {{
                        position: absolute;
                        top: 0;
                        left: 0;
                        bottom: 0;
                        z-index: 100;
                        transform: translateX(-100%);
                    }}
                    
                    .sidebar.active {{
                        transform: translateX(0);
                    }}
                    
                    .chat-area {{
                        width: 100%;
                    }}
                    
                    .back-btn {{
                        display: block;
                    }}
                    
                    .message {{
                        max-width: 85%;
                    }}
                }}
            </style>
        </head>
        <body>
            <div class="app-container">
                <!-- Сайдбар -->
                <div class="sidebar" id="sidebar">
                    <div class="sidebar-header">
                        <div class="logo-placeholder">
                            <i class="fas fa-comments"></i>
                        </div>
                        <h1 class="app-title">Tandau</h1>
                    </div>
                    
                    <div class="user-info">
                        <div class="avatar" id="user-avatar" onclick="openAvatarModal()">
                            {username[:2].upper()}
                        </div>
                        <div class="user-details">
                            <strong>{username}</strong>
                            <div class="user-status">
                                <div class="status-dot"></div>
                                Online
                            </div>
                        </div>
                        <button class="channel-btn" onclick="openSettings()">
                            <i class="fas fa-cog"></i>
                        </button>
                    </div>
                    
                    <div class="nav">
                        <div class="nav-title">
                            <span>Чаты</span>
                        </div>
                        
                        <div class="nav-item" onclick="openChat('general')">
                            <i class="fas fa-hashtag"></i>
                            <span>General</span>
                        </div>
                        
                        <div class="nav-title">
                            <span>Избранное</span>
                            <button class="add-btn" onclick="addToFavorites()">
                                <i class="fas fa-plus"></i>
                            </button>
                        </div>
                        
                        <div id="favorites-list">
                            <!-- Избранное будет загружено динамически -->
                        </div>
                        
                        <div class="nav-title">
                            <span>Пользователи</span>
                        </div>
                        
                        <div id="users-list">
                            <!-- Пользователи будут загружены динамически -->
                        </div>
                    </div>
                    
                    <button class="logout-btn" onclick="logout()">
                        <i class="fas fa-sign-out-alt"></i> Выйти
                    </button>
                </div>
                
                <!-- Область чата -->
                <div class="chat-area">
                    <div class="chat-header">
                        <button class="back-btn" onclick="toggleSidebar()">
                            <i class="fas fa-bars"></i>
                        </button>
                        <div class="channel-header-avatar" id="current-chat-avatar">
                            <i class="fas fa-hashtag"></i>
                        </div>
                        <div>
                            <div style="font-weight: 600;" id="chat-title">General</div>
                            <div style="font-size: 0.8rem; color: #666;" id="chat-status">Онлайн</div>
                        </div>
                    </div>
                    
                    <div class="messages" id="messages">
                        <div class="message-container" id="message-container">
                            <!-- Сообщения будут загружены динамически -->
                        </div>
                    </div>
                    
                    <div class="input-area">
                        <div class="input-row">
                            <button class="emoji-btn" onclick="toggleEmojiPicker()">
                                😊
                            </button>
                            <button class="attachment-btn" onclick="attachFile()">
                                <i class="fas fa-paperclip"></i>
                            </button>
                            <textarea class="msg-input" id="msg-input" placeholder="Написать сообщение..." rows="1" onkeydown="handleKeydown(event)"></textarea>
                            <button class="send-btn" onclick="sendMessage()">
                                <i class="fas fa-paper-plane"></i>
                            </button>
                        </div>
                    </div>
                </div>
            </div>

            <script src="https://cdnjs.cloudflare.com/ajax/libs/socket.io/4.7.2/socket.io.js"></script>
            <script>
                const socket = io();
                const username = "{username}";
                let currentRoom = "general";
                
                // Инициализация
                window.onload = function() {{
                    loadUsers();
                    loadFavorites();
                    loadMessages(currentRoom);
                    socket.emit('join', {{ room: currentRoom }});
                    
                    // Проверка мобильного устройства
                    if (window.innerWidth <= 768) {{
                        document.getElementById('sidebar').classList.remove('active');
                    }}
                }};
                
                function toggleSidebar() {{
                    const sidebar = document.getElementById('sidebar');
                    sidebar.classList.toggle('active');
                }}
                
                function openChat(roomName) {{
                    currentRoom = roomName;
                    document.getElementById('chat-title').textContent = roomName.charAt(0).toUpperCase() + roomName.slice(1);
                    loadMessages(roomName);
                    socket.emit('leave', {{ room: currentRoom }});
                    socket.emit('join', {{ room: roomName }});
                    
                    // Закрываем сайдбар на мобильных
                    if (window.innerWidth <= 768) {{
                        toggleSidebar();
                    }}
                }}
                
                function loadMessages(room) {{
                    fetch('/get_messages/' + room)
                        .then(r => r.json())
                        .then(messages => {{
                            const container = document.getElementById('message-container');
                            container.innerHTML = '';
                            
                            if (messages && Array.isArray(messages)) {{
                                messages.forEach(msg => {{
                                    addMessage(msg);
                                }});
                            }}
                            
                            // Прокручиваем к последнему сообщению
                            container.scrollTop = container.scrollHeight;
                        }});
                }}
                
                function addMessage(data) {{
                    const container = document.getElementById('message-container');
                    const messageDiv = document.createElement('div');
                    messageDiv.className = `message ${{data.user === username ? 'own' : 'other'}}`;
                    
                    // Аватар
                    const avatar = document.createElement('div');
                    avatar.className = 'message-avatar';
                    avatar.style.backgroundColor = data.color || '#6366f1';
                    if (data.user !== username) {{
                        avatar.textContent = data.user.slice(0, 2).toUpperCase();
                    }}
                    
                    // Контент
                    const content = document.createElement('div');
                    content.className = 'message-content';
                    
                    // Отправитель (только для чужих сообщений)
                    if (data.user !== username) {{
                        const sender = document.createElement('div');
                        sender.className = 'message-sender';
                        sender.textContent = data.user;
                        content.appendChild(sender);
                    }}
                    
                    // Текст сообщения
                    if (data.message) {{
                        const text = document.createElement('div');
                        text.className = 'message-text';
                        text.textContent = data.message;
                        content.appendChild(text);
                    }}
                    
                    // Файл
                    if (data.file) {{
                        const fileDiv = document.createElement('div');
                        fileDiv.className = 'message-file';
                        
                        if (data.file.endsWith('.mp4') || data.file.endsWith('.webm')) {{
                            const video = document.createElement('video');
                            video.src = data.file;
                            video.controls = true;
                            video.style.maxWidth = '200px';
                            video.style.maxHeight = '150px';
                            fileDiv.appendChild(video);
                        }} else {{
                            const img = document.createElement('img');
                            img.src = data.file;
                            img.alt = 'Файл';
                            img.style.maxWidth = '200px';
                            img.style.maxHeight = '150px';
                            img.onclick = () => window.open(data.file, '_blank');
                            fileDiv.appendChild(img);
                        }}
                        
                        content.appendChild(fileDiv);
                    }}
                    
                    // Время
                    const time = document.createElement('div');
                    time.className = 'message-time';
                    time.textContent = data.timestamp || new Date().toLocaleTimeString([], {{ hour: '2-digit', minute: '2-digit' }});
                    content.appendChild(time);
                    
                    // Собираем сообщение
                    messageDiv.appendChild(avatar);
                    messageDiv.appendChild(content);
                    container.appendChild(messageDiv);
                    
                    // Прокручиваем к последнему сообщению
                    container.scrollTop = container.scrollHeight;
                }}
                
                function sendMessage() {{
                    const input = document.getElementById('msg-input');
                    const message = input.value.trim();
                    
                    if (!message) return;
                    
                    const messageData = {{
                        message: message,
                        room: currentRoom,
                        user: username
                    }};
                    
                    socket.emit('message', messageData);
                    input.value = '';
                    input.focus();
                }}
                
                function handleKeydown(e) {{
                    if (e.key === 'Enter' && !e.shiftKey) {{
                        e.preventDefault();
                        sendMessage();
                    }}
                }}
                
                function attachFile() {{
                    const input = document.createElement('input');
                    input.type = 'file';
                    input.accept = 'image/*,video/*';
                    input.onchange = (e) => {{
                        const file = e.target.files[0];
                        if (file) {{
                            const reader = new FileReader();
                            reader.onload = (e) => {{
                                const messageData = {{
                                    message: '',
                                    room: currentRoom,
                                    user: username,
                                    file: e.target.result
                                }};
                                socket.emit('message', messageData);
                            }};
                            reader.readAsDataURL(file);
                        }}
                    }};
                    input.click();
                }}
                
                function toggleEmojiPicker() {{
                    // Простая реализация эмодзи
                    const emojis = ['😀', '😂', '🥰', '😎', '🤔', '👍', '❤️', '🔥', '🎉', '🚀'];
                    const input = document.getElementById('msg-input');
                    const randomEmoji = emojis[Math.floor(Math.random() * emojis.length)];
                    input.value += randomEmoji;
                    input.focus();
                }}
                
                function loadUsers() {{
                    fetch('/users')
                        .then(r => r.json())
                        .then(users => {{
                            const container = document.getElementById('users-list');
                            container.innerHTML = '';
                            
                            if (users && Array.isArray(users)) {{
                                users.forEach(user => {{
                                    if (user.username !== username) {{
                                        const userDiv = document.createElement('div');
                                        userDiv.className = 'nav-item';
                                        userDiv.onclick = () => openPrivateChat(user.username);
                                        userDiv.innerHTML = `
                                            <div class="message-avatar" style="background-color: ${{user.color || '#6366f1'}}; width: 24px; height: 24px; font-size: 0.8rem;">
                                                ${{user.username.slice(0, 2).toUpperCase()}}
                                            </div>
                                            <span>${{user.username}}</span>
                                            <span style="margin-left: auto; font-size: 0.7rem; color: ${{user.online ? '#10b981' : '#6b7280'}}">
                                                ${{user.online ? '●' : '○'}}
                                            </span>
                                        `;
                                        container.appendChild(userDiv);
                                    }}
                                }});
                            }}
                        }});
                }}
                
                function openPrivateChat(otherUser) {{
                    const roomName = ['private', username, otherUser].sort().join('_');
                    currentRoom = roomName;
                    document.getElementById('chat-title').textContent = otherUser;
                    document.getElementById('chat-status').textContent = 'Приватный чат';
                    loadMessages(roomName);
                    socket.emit('leave', {{ room: currentRoom }});
                    socket.emit('join', {{ room: roomName }});
                    
                    if (window.innerWidth <= 768) {{
                        toggleSidebar();
                    }}
                }}
                
                function loadFavorites() {{
                    // Загрузка избранного
                    fetch('/get_favorites')
                        .then(r => r.json())
                        .then(data => {{
                            if (data.success) {{
                                const container = document.getElementById('favorites-list');
                                container.innerHTML = '';
                                
                                data.favorites.forEach(favorite => {{
                                    const favDiv = document.createElement('div');
                                    favDiv.className = 'nav-item';
                                    favDiv.innerHTML = `
                                        <i class="fas fa-star"></i>
                                        <span>${{favorite.content?.substring(0, 20) || 'Избранное'}}${{favorite.content?.length > 20 ? '...' : ''}}</span>
                                    `;
                                    container.appendChild(favDiv);
                                }});
                            }}
                        }});
                }}
                
                function addToFavorites() {{
                    const content = prompt('Введите текст для избранного:');
                    if (content) {{
                        fetch('/add_to_favorites', {{
                            method: 'POST',
                            headers: {{ 'Content-Type': 'application/json' }},
                            body: JSON.stringify({{ content: content }})
                        }})
                        .then(r => r.json())
                        .then(data => {{
                            if (data.success) {{
                                loadFavorites();
                                alert('Добавлено в избранное!');
                            }}
                        }});
                    }}
                }}
                
                function openSettings() {{
                    alert('Настройки будут реализованы позже');
                }}
                
                function openAvatarModal() {{
                    alert('Смена аватарки будет реализована позже');
                }}
                
                function logout() {{
                    fetch('/logout', {{ method: 'POST' }})
                        .then(() => {{
                            window.location.href = '/';
                        }});
                }}
                
                // Socket event handlers
                socket.on('connect', function() {{
                    console.log('Connected to server');
                }});
                
                socket.on('disconnect', function() {{
                    console.log('Disconnected from server');
                }});
                
                socket.on('message', function(data) {{
                    if (data.room === currentRoom) {{
                        addMessage(data);
                    }}
                }});
                
                // Автоматическое изменение высоты поля ввода
                document.getElementById('msg-input').addEventListener('input', function() {{
                    this.style.height = 'auto';
                    this.style.height = Math.min(this.scrollHeight, 120) + 'px';
                }});
                
                // Обработка изменения размера окна
                window.addEventListener('resize', function() {{
                    if (window.innerWidth > 768) {{
                        document.getElementById('sidebar').classList.add('active');
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

    @app.route('/get_favorites')
    def get_favorites_handler():
        if 'username' not in session:
            return jsonify({'success': False, 'error': 'Не авторизован'})
        
        favorites = get_favorites(session['username'])
        return jsonify({'success': True, 'favorites': favorites})

    @app.route('/add_to_favorites', methods=['POST'])
    def add_to_favorites_handler():
        if 'username' not in session:
            return jsonify({'success': False, 'error': 'Не авторизован'})
        
        data = request.get_json()
        content = data.get('content', '').strip()
        
        if not content:
            return jsonify({'success': False, 'error': 'Введите текст'})
        
        favorite_id = add_to_favorites(session['username'], content)
        if favorite_id:
            return jsonify({'success': True, 'id': favorite_id})
        return jsonify({'success': False, 'error': 'Не удалось добавить в избранное'})

    @app.route('/logout', methods=['POST'])
    def logout_handler():
        if 'username' in session:
            update_online(session['username'], False)
            session.pop('username', None)
        return jsonify({'success': True})

    # === SocketIO ===
    @socketio.on('connect')
    def on_connect():
        if 'username' in session:
            join_room('general')
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
        
        # Для приватных чатов
        recipient = None
        if room.startswith('private_'):
            parts = room.split('_')
            if len(parts) == 3:
                user1, user2 = parts[1], parts[2]
                recipient = user1 if user2 == session['username'] else user2
        
        # Сохраняем сообщение
        msg_id = save_message(
            session['username'], 
            msg, 
            room, 
            recipient, 
            'text', 
            file,
            None
        )
        
        # Получаем цвет пользователя
        user_info = get_user(session['username'])
        user_color = user_info['avatar_color'] if user_info else '#6366F1'
        
        # Отправляем сообщение в комнату
        emit('message', {
            'user': session['username'], 
            'message': msg, 
            'color': user_color,
            'timestamp': datetime.now().strftime('%H:%M'),
            'room': room,
            'file': file
        }, room=room)

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
