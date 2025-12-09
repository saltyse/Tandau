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

    @app.route('/upload_channel_avatar', methods=['POST'])
    def upload_channel_avatar_handler():
        if 'username' not in session: 
            return jsonify({'success': False, 'error': 'Не авторизован'})
        
        channel_name = request.form.get('channel_name')
        if not channel_name:
            return jsonify({'success': False, 'error': 'Не указан канал'})
        
        if 'avatar' in request.files:
            file = request.files['avatar']
            path, filename = save_uploaded_file(file, app.config['CHANNEL_AVATAR_FOLDER'])
        else:
            return jsonify({'success': False, 'error': 'Файл не найден'})
        
        if path:
            success = update_channel_avatar(channel_name, path, session['username'])
            if success:
                return jsonify({'success': True, 'path': path})
            else:
                return jsonify({'success': False, 'error': 'Нет прав для изменения аватарки канала'})
        return jsonify({'success': False, 'error': 'Неверный формат файла'})

    @app.route('/delete_channel_avatar', methods=['POST'])
    def delete_channel_avatar_handler():
        if 'username' not in session: 
            return jsonify({'success': False, 'error': 'Не авторизован'})
        
        channel_name = request.json.get('channel_name')
        if not channel_name:
            return jsonify({'success': False, 'error': 'Не указан канал'})
        
        success = delete_channel_avatar(channel_name, session['username'])
        if success:
            return jsonify({'success': True})
        else:
            return jsonify({'success': False, 'error': 'Нет прав для удаления аватарки канала'})

    @app.route('/update_channel_description', methods=['POST'])
    def update_channel_description_handler():
        if 'username' not in session: 
            return jsonify({'success': False, 'error': 'Не авторизован'})
        
        channel_name = request.json.get('channel_name')
        description = request.json.get('description', '').strip()
        
        if not channel_name:
            return jsonify({'success': False, 'error': 'Не указан канал'})
        
        success = update_channel_description(channel_name, description, session['username'])
        if success:
            return jsonify({'success': True})
        else:
            return jsonify({'success': False, 'error': 'Нет прав для изменения описания канала'})

    @app.route('/make_admin', methods=['POST'])
    def make_admin_handler():
        if 'username' not in session: 
            return jsonify({'success': False, 'error': 'Не авторизован'})
        
        channel_name = request.json.get('channel_name')
        target_user = request.json.get('username', '').strip()
        
        if not channel_name or not target_user:
            return jsonify({'success': False, 'error': 'Не указан канал или пользователь'})
        
        success, message = make_user_admin(channel_name, target_user, session['username'])
        return jsonify({'success': success, 'message': message})

    @app.route('/remove_admin', methods=['POST'])
    def remove_admin_handler():
        if 'username' not in session: 
            return jsonify({'success': False, 'error': 'Не авторизован'})
        
        channel_name = request.json.get('channel_name')
        target_user = request.json.get('username', '').strip()
        
        if not channel_name or not target_user:
            return jsonify({'success': False, 'error': 'Не указан канал или пользователь'})
        
        success, message = remove_admin(channel_name, target_user, session['username'])
        return jsonify({'success': success, 'message': message})

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
        
        try:
            data = request.get_json()
            if not data:
                return jsonify({'success': False, 'error': 'Неверный формат данных'})
            
            name = data.get('name', '').strip()
            display_name = data.get('display_name', '').strip()
            description = data.get('description', '').strip()
            is_private = data.get('is_private', False)
            
            if not name:
                return jsonify({'success': False, 'error': 'Название канала не может быть пустым'})
            
            if len(name) < 2:
                return jsonify({'success': False, 'error': 'Название канала должно быть не менее 2 символов'})
            
            if len(name) > 50:
                return jsonify({'success': False, 'error': 'Название канала должно быть не более 50 символов'})
            
            if not re.match(r'^[a-zA-Z0-9_]+$', name):
                return jsonify({'success': False, 'error': 'Идентификатор канала может содержать только латинские буквы, цифры и символ подчеркивания'})
            
            if not display_name:
                display_name = name.capitalize()
            
            channel_id = create_channel(name, display_name, description, session['username'], is_private)
            if channel_id:
                return jsonify({
                    'success': True, 
                    'channel_name': name, 
                    'display_name': display_name,
                    'message': 'Канал успешно создан!'
                })
            return jsonify({'success': False, 'error': 'Канал с таким названием уже существует'})
        except Exception as e:
            print(f"Error creating channel: {e}")
            return jsonify({'success': False, 'error': f'Ошибка сервера: {str(e)}'})

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

    # Новый маршрут для загрузки файлов через HTTP
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
                with open(terms_file, 'w', encoding='utf-8') as f:
                    f.write('Tandau Messenger - Условия использования\n\n')
                    f.write('Это демонстрационный файл. В реальном приложении здесь был бы PDF документ.\n')
                
            # Создаем пример PDF файла Политики конфиденциальности
            privacy_file = os.path.join(docs_folder, 'privacy_policy.pdf')
            if not os.path.exists(privacy_file):
                with open(privacy_file, 'w', encoding='utf-8') as f:
                    f.write('Tandau Messenger - Политика конфиденциальности\n\n')
                    f.write('Это демонстрационный файл. В реальном приложении здесь был бы PDF документ.\n')
            
            return jsonify({'success': True, 'message': 'Documents folder created'})
        except Exception as e:
            return jsonify({'success': False, 'error': str(e)})

    # === Маршруты для аутентификации ===
    @app.route('/register', methods=['POST'])
    def register_handler():
        username = request.json.get('username', '').strip()
        password = request.json.get('password', '').strip()
        
        if not username or not password:
            return jsonify({'success': False, 'error': 'Заполните все поля'})
        
        if len(username) < 3 or len(username) > 20:
            return jsonify({'success': False, 'error': 'Логин должен быть от 3 до 20 символов'})
        
        if len(password) < 4:
            return jsonify({'success': False, 'error': 'Пароль должен быть не менее 4 символов'})
        
        success, message = create_user(username, password)
        if success:
            return jsonify({'success': True, 'message': message})
        else:
            return jsonify({'success': False, 'error': message})

    @app.route('/login', methods=['POST'])
    def login_handler():
        username = request.json.get('username', '').strip()
        password = request.json.get('password', '').strip()
        
        if not username or not password:
            return jsonify({'success': False, 'error': 'Заполните все поля'})
        
        user = verify_user(username, password)
        if user:
            session['username'] = username
            update_online(username, True)
            return jsonify({'success': True, 'message': 'Успешный вход'})
        else:
            return jsonify({'success': False, 'error': 'Неверный логин или пароль'})

    @app.route('/logout')
    def logout_handler():
        if 'username' in session:
            update_online(session['username'], False)
            session.pop('username', None)
        return redirect('/')

    # === Основные маршруты ===
    @app.route('/')
    def index():
        if 'username' in session: 
            return redirect('/chat')
        
        # Исправленная страница входа/регистрации с логотипом
        return r'''
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
                                Регистрируясь, вы соглашаетесь с нашими <a href="#" onclick="openTermsModal(); return false;">Условиями использования</a>
                            </div>
                        </form>
                    </div>
                </div>
            </div>

            <script>
                function showTab(tabName) {
                    // Переключаем табы
                    document.querySelectorAll('.auth-tab').forEach(tab => {
                        tab.classList.remove('active');
                    });
                    document.querySelectorAll('.auth-form').forEach(form => {
                        form.classList.remove('active');
                    });
                    
                    if (tabName === 'login') {
                        document.querySelector('.auth-tab:nth-child(1)').classList.add('active');
                        document.getElementById('login-form').classList.add('active');
                    } else {
                        document.querySelector('.auth-tab:nth-child(2)').classList.add('active');
                        document.getElementById('register-form').classList.add('active');
                    }
                    
                    // Очищаем сообщения
                    const alert = document.getElementById('alert');
                    alert.style.display = 'none';
                    alert.className = 'alert';
                }
                
                function togglePassword(inputId) {
                    const input = document.getElementById(inputId);
                    const toggle = input.nextElementSibling;
                    
                    if (input.type === 'password') {
                        input.type = 'text';
                        toggle.innerHTML = '<i class="fas fa-eye-slash"></i>';
                    } else {
                        input.type = 'password';
                        toggle.innerHTML = '<i class="fas fa-eye"></i>';
                    }
                }
                
                function showAlert(message, type) {
                    const alert = document.getElementById('alert');
                    alert.textContent = message;
                    alert.className = `alert alert-${type}`;
                    alert.style.display = 'block';
                }
                
                async function login() {
                    const username = document.getElementById('login-username').value.trim();
                    const password = document.getElementById('login-password').value.trim();
                    const btn = document.getElementById('login-btn');
                    
                    if (!username || !password) {
                        showAlert('Заполните все поля', 'error');
                        return;
                    }
                    
                    const originalText = btn.innerHTML;
                    btn.innerHTML = '<span class="loader"></span> Вход...';
                    btn.disabled = true;
                    
                    try {
                        const response = await fetch('/login', {
                            method: 'POST',
                            headers: { 'Content-Type': 'application/json' },
                            body: JSON.stringify({ username, password })
                        });
                        
                        const data = await response.json();
                        
                        if (data.success) {
                            showAlert('Успешный вход! Перенаправление...', 'success');
                            setTimeout(() => {
                                window.location.href = '/chat';
                            }, 1000);
                        } else {
                            showAlert(data.error || 'Ошибка входа', 'error');
                            btn.innerHTML = originalText;
                            btn.disabled = false;
                        }
                    } catch (error) {
                        showAlert('Ошибка соединения с сервером', 'error');
                        btn.innerHTML = originalText;
                        btn.disabled = false;
                    }
                }
                
                async function register() {
                    const username = document.getElementById('register-username').value.trim();
                    const password = document.getElementById('register-password').value.trim();
                    const confirm = document.getElementById('register-confirm').value.trim();
                    const btn = document.getElementById('register-btn');
                    
                    if (!username || !password || !confirm) {
                        showAlert('Заполните все поля', 'error');
                        return;
                    }
                    
                    if (password !== confirm) {
                        showAlert('Пароли не совпадают', 'error');
                        return;
                    }
                    
                    if (username.length < 3 || username.length > 20) {
                        showAlert('Логин должен быть от 3 до 20 символов', 'error');
                        return;
                    }
                    
                    if (password.length < 4) {
                        showAlert('Пароль должен быть не менее 4 символов', 'error');
                        return;
                    }
                    
                    const originalText = btn.innerHTML;
                    btn.innerHTML = '<span class="loader"></span> Регистрация...';
                    btn.disabled = true;
                    
                    try {
                        const response = await fetch('/register', {
                            method: 'POST',
                            headers: { 'Content-Type': 'application/json' },
                            body: JSON.stringify({ username, password })
                        });
                        
                        const data = await response.json();
                        
                        if (data.success) {
                            showAlert(data.message + ' Теперь выполните вход.', 'success');
                            setTimeout(() => {
                                showTab('login');
                                document.getElementById('login-username').value = username;
                                document.getElementById('login-password').value = password;
                                btn.innerHTML = originalText;
                                btn.disabled = false;
                            }, 1500);
                        } else {
                            showAlert(data.error || 'Ошибка регистрации', 'error');
                            btn.innerHTML = originalText;
                            btn.disabled = false;
                        }
                    } catch (error) {
                        showAlert('Ошибка соединения с сервером', 'error');
                        btn.innerHTML = originalText;
                        btn.disabled = false;
                    }
                }
                
                function openTermsModal() {
                    alert('Условия использования Tandau Messenger\n\n1. Вы обязуетесь использовать сервис в законных целях.\n2. Вы несете ответственность за содержание своих сообщений.\n3. Мы оставляем за собой право блокировать аккаунты за нарушение правил.\n\nПолная версия доступна по ссылке: https://vk.com/rsaltyyt');
                }
            </script>
        </body>
        </html>'''

    @app.route('/chat')
    def chat():
        if 'username' not in session:
            return redirect('/')
        
        # ИСПРАВЛЕННАЯ СТРАНИЦА ЧАТА
        return f'''<!DOCTYPE html>
<html lang="ru">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>Tandau Messenger - Чат</title>
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
            --sidebar-width: 280px;
        }}
        
        [data-theme="dark"] {{
            --primary: #818cf8;
            --primary-dark: #6366f1;
            --primary-light: #a5b4fc;
            --secondary: #a78bfa;
            --accent: #34d399;
            --text: #f9fafb;
            --text-light: #d1d5db;
            --bg: #111827;
            --bg-light: #1f2937;
            --border: #374151;
            --shadow: 0 10px 25px -5px rgba(0, 0, 0, 0.5), 0 10px 10px -5px rgba(0, 0, 0, 0.4);
        }}
        
        body {{
            background: var(--bg);
            color: var(--text);
            min-height: 100vh;
            overflow-x: hidden;
        }}
        
        .app-container {{
            display: flex;
            height: 100vh;
            position: relative;
        }}
        
        /* Сайдбар */
        .sidebar {{
            width: var(--sidebar-width);
            background: var(--bg-light);
            border-right: 1px solid var(--border);
            display: flex;
            flex-direction: column;
            transition: transform 0.3s ease;
            z-index: 100;
            height: 100%;
            overflow-y: auto;
        }}
        
        .sidebar.hidden {{
            transform: translateX(-100%);
        }}
        
        .sidebar-header {{
            padding: 20px;
            display: flex;
            align-items: center;
            gap: 12px;
            border-bottom: 1px solid var(--border);
            background: var(--bg-light);
        }}
        
        .menu-toggle {{
            background: none;
            border: none;
            color: var(--text);
            font-size: 1.2rem;
            cursor: pointer;
            display: none;
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
            background: linear-gradient(135deg, #667eea, #764ba2);
            -webkit-background-clip: text;
            -webkit-text-fill-color: transparent;
            background-clip: text;
        }}
        
        .user-info {{
            padding: 20px;
            display: flex;
            align-items: center;
            gap: 12px;
            border-bottom: 1px solid var(--border);
        }}
        
        .avatar {{
            width: 48px;
            height: 48px;
            border-radius: 50%;
            background: var(--accent);
            display: flex;
            align-items: center;
            justify-content: center;
            color: white;
            font-weight: bold;
            font-size: 1.2rem;
            cursor: pointer;
            background-size: cover;
            background-position: center;
        }}
        
        .user-details {{
            flex: 1;
        }}
        
        .user-details strong {{
            display: block;
            margin-bottom: 4px;
        }}
        
        .user-status {{
            display: flex;
            align-items: center;
            gap: 6px;
            font-size: 0.85rem;
            color: var(--text-light);
        }}
        
        .status-dot {{
            width: 8px;
            height: 8px;
            background: #10b981;
            border-radius: 50%;
        }}
        
        .channel-btn {{
            background: none;
            border: none;
            color: var(--text-light);
            font-size: 1.1rem;
            cursor: pointer;
            padding: 8px;
            border-radius: 8px;
        }}
        
        .channel-btn:hover {{
            background: var(--bg);
        }}
        
        .nav {{
            flex: 1;
            overflow-y: auto;
            padding: 20px 0;
        }}
        
        .nav-title {{
            padding: 0 20px 10px;
            font-size: 0.85rem;
            font-weight: 600;
            color: var(--text-light);
            text-transform: uppercase;
            letter-spacing: 0.5px;
            display: flex;
            justify-content: space-between;
            align-items: center;
        }}
        
        .add-btn {{
            background: none;
            border: none;
            color: var(--text-light);
            cursor: pointer;
            font-size: 0.9rem;
            padding: 2px 8px;
            border-radius: 6px;
        }}
        
        .add-btn:hover {{
            background: var(--border);
        }}
        
        .nav-item {{
            display: flex;
            align-items: center;
            gap: 12px;
            padding: 12px 20px;
            cursor: pointer;
            transition: var(--transition);
            user-select: none;
        }}
        
        .nav-item:hover {{
            background: var(--bg);
        }}
        
        .nav-item.active {{
            background: var(--primary);
            color: white;
        }}
        
        .nav-item i {{
            font-size: 1rem;
            width: 20px;
            text-align: center;
        }}
        
        /* Область чата */
        .chat-area {{
            flex: 1;
            display: flex;
            flex-direction: column;
            background: var(--bg);
            height: 100%;
        }}
        
        .chat-area.active {{
            position: fixed;
            top: 0;
            left: 0;
            right: 0;
            bottom: 0;
            z-index: 50;
        }}
        
        .chat-header {{
            padding: 20px;
            border-bottom: 1px solid var(--border);
            display: flex;
            align-items: center;
            gap: 15px;
            background: var(--bg-light);
        }}
        
        .back-btn {{
            background: none;
            border: none;
            color: var(--text);
            font-size: 1.2rem;
            cursor: pointer;
            display: none;
        }}
        
        .channel-header-avatar {{
            width: 40px;
            height: 40px;
            border-radius: 50%;
            background: linear-gradient(135deg, #667eea, #764ba2);
            display: flex;
            align-items: center;
            justify-content: center;
            color: white;
            font-weight: bold;
            font-size: 1rem;
            background-size: cover;
            background-position: center;
        }}
        
        .channel-actions {{
            display: flex;
            gap: 10px;
        }}
        
        /* Категории фильтра */
        .categories-filter {{
            padding: 15px 20px;
            display: flex;
            gap: 10px;
            overflow-x: auto;
            background: var(--bg-light);
            border-bottom: 1px solid var(--border);
        }}
        
        .category-filter-btn {{
            padding: 6px 15px;
            background: var(--bg);
            border: 1px solid var(--border);
            border-radius: 20px;
            color: var(--text);
            font-size: 0.9rem;
            cursor: pointer;
            white-space: nowrap;
        }}
        
        .category-filter-btn.active {{
            background: var(--primary);
            color: white;
            border-color: var(--primary);
        }}
        
        /* Сетка избранного */
        .favorites-grid {{
            display: grid;
            grid-template-columns: repeat(auto-fill, minmax(300px, 1fr));
            gap: 20px;
            padding: 20px;
            overflow-y: auto;
            flex: 1;
        }}
        
        .favorite-item {{
            background: var(--bg-light);
            border-radius: var(--radius);
            border: 1px solid var(--border);
            padding: 20px;
            position: relative;
            transition: var(--transition);
        }}
        
        .favorite-item:hover {{
            transform: translateY(-2px);
            box-shadow: var(--shadow);
        }}
        
        .favorite-item.pinned {{
            border-color: var(--accent);
            border-left: 4px solid var(--accent);
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
            background: var(--bg);
            border: 1px solid var(--border);
            border-radius: 6px;
            width: 30px;
            height: 30px;
            display: flex;
            align-items: center;
            justify-content: center;
            cursor: pointer;
            color: var(--text);
        }}
        
        .favorite-action-btn:hover {{
            background: var(--primary);
            color: white;
        }}
        
        .favorite-content {{
            margin-bottom: 15px;
            font-size: 0.95rem;
            line-height: 1.5;
        }}
        
        .favorite-file {{
            margin-bottom: 15px;
        }}
        
        .favorite-file img, .favorite-file video {{
            max-width: 100%;
            border-radius: var(--radius-sm);
        }}
        
        .favorite-meta {{
            display: flex;
            justify-content: space-between;
            align-items: center;
            font-size: 0.8rem;
            color: var(--text-light);
            margin-top: 15px;
            padding-top: 15px;
            border-top: 1px solid var(--border);
        }}
        
        .category-badge {{
            background: var(--primary-light);
            color: white;
            padding: 2px 8px;
            border-radius: 10px;
            font-size: 0.75rem;
        }}
        
        .empty-favorites {{
            grid-column: 1 / -1;
            text-align: center;
            padding: 60px 20px;
            color: var(--text-light);
        }}
        
        .empty-favorites i {{
            font-size: 3rem;
            margin-bottom: 20px;
            opacity: 0.3;
        }}
        
        /* Сообщения чата */
        .messages {{
            flex: 1;
            overflow-y: auto;
            padding: 20px;
            display: flex;
            flex-direction: column;
            gap: 15px;
        }}
        
        .message {{
            display: flex;
            gap: 12px;
            max-width: 80%;
        }}
        
        .message.own {{
            align-self: flex-end;
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
            border-radius: 18px;
            padding: 12px 16px;
            position: relative;
            box-shadow: 0 2px 8px rgba(0,0,0,0.05);
        }}
        
        .message.own .message-content {{
            background: var(--primary);
            color: white;
        }}
        
        .message-sender {{
            font-size: 0.85rem;
            font-weight: 600;
            margin-bottom: 4px;
            color: var(--text-light);
        }}
        
        .message.own .message-sender {{
            color: rgba(255,255,255,0.9);
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
            color: rgba(255,255,255,0.7);
        }}
        
        .message-file {{
            margin-top: 10px;
        }}
        
        .message-file img {{
            max-width: 200px;
            max-height: 150px;
            border-radius: 8px;
            cursor: pointer;
        }}
        
        .message-file video {{
            max-width: 300px;
            max-height: 200px;
            border-radius: 8px;
        }}
        
        /* Область ввода */
        .input-area {{
            background: var(--bg-light);
            border-top: 1px solid var(--border);
            padding: 20px;
        }}
        
        .input-row {{
            display: flex;
            gap: 10px;
            align-items: flex-end;
        }}
        
        .emoji-btn {{
            background: var(--bg);
            border: 1px solid var(--border);
            color: var(--text);
            cursor: pointer;
            font-size: 1.4rem;
            padding: 10px;
            border-radius: 50%;
            width: 48px;
            height: 48px;
            display: flex;
            align-items: center;
            justify-content: center;
            flex-shrink: 0;
        }}
        
        .attachment-btn {{
            background: var(--bg);
            border: 1px solid var(--border);
            color: var(--text);
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
        }}
        
        .msg-input {{
            flex: 1;
            padding: 12px 16px;
            border: 1px solid var(--border);
            border-radius: 24px;
            background: var(--bg);
            color: var(--text);
            font-size: 1rem;
            resize: none;
            max-height: 120px;
            min-height: 48px;
            line-height: 1.4;
        }}
        
        .msg-input:focus {{
            outline: none;
            border-color: var(--primary);
        }}
        
        .send-btn {{
            width: 48px;
            height: 48px;
            border-radius: 50%;
            background: var(--primary);
            color: white;
            border: none;
            cursor: pointer;
            display: flex;
            align-items: center;
            justify-content: center;
            flex-shrink: 0;
        }}
        
        .send-btn:hover {{
            background: var(--primary-dark);
        }}
        
        /* Блок эмодзи */
        .emoji-picker-container {{
            position: absolute;
            bottom: 80px;
            left: 20px;
            z-index: 1000;
            display: none;
        }}
        
        .emoji-picker-glass {{
            background: var(--bg-light);
            border-radius: 12px;
            border: 1px solid var(--border);
            padding: 15px;
            width: 300px;
            max-height: 300px;
            overflow: hidden;
            box-shadow: var(--shadow);
        }}
        
        .emoji-grid {{
            display: grid;
            grid-template-columns: repeat(8, 1fr);
            gap: 5px;
            overflow-y: auto;
            max-height: 200px;
        }}
        
        .emoji-item {{
            font-size: 1.5rem;
            cursor: pointer;
            padding: 5px;
            border-radius: 5px;
            text-align: center;
        }}
        
        .emoji-item:hover {{
            background: var(--bg);
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
            z-index: 1000;
            align-items: center;
            justify-content: center;
            padding: 20px;
        }}
        
        .modal-content {{
            background: var(--bg-light);
            padding: 25px;
            border-radius: 15px;
            width: 100%;
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
            font-size: 16px;
        }}
        
        .form-control:focus {{
            outline: none;
            border-color: var(--accent);
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
            margin: 20px;
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
        
        /* Адаптивность */
        @media (max-width: 768px) {{
            .menu-toggle {{
                display: block;
            }}
            
            .back-btn {{
                display: block;
            }}
            
            .sidebar {{
                position: fixed;
                top: 0;
                left: 0;
                bottom: 0;
                transform: translateX(-100%);
            }}
            
            .sidebar.hidden {{
                transform: translateX(-100%);
            }}
            
            .sidebar:not(.hidden) {{
                transform: translateX(0);
            }}
            
            .favorites-grid {{
                grid-template-columns: 1fr;
            }}
            
            .message {{
                max-width: 90%;
            }}
        }}
        
        .emoji-in-message {{
            font-size: 1.2em;
            vertical-align: middle;
        }}
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
                    <strong>{session['username']}</strong>
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
        
        <!-- Область чата -->
        <div class="chat-area" id="chat-area">
            <div class="chat-header">
                <button class="back-btn" onclick="goBack()">
                    <i class="fas fa-arrow-left"></i>
                </button>
                <div class="channel-header-avatar" id="channel-header-avatar" onclick="openChannelSettingsModal()"></div>
                <div style="flex: 1;">
                    <div style="font-weight: 600;" id="chat-title">Избранное</div>
                    <div style="font-size: 0.8rem; color: #666;" id="channel-description"></div>
                </div>
                <div class="channel-actions" id="channel-actions" style="display: none;">
                    <button class="channel-btn" onclick="openChannelSettingsModal()">
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
                    <button class="emoji-btn" onclick="toggleEmojiPicker()" title="Эмодзи">
                        😊
                    </button>
                    <button class="attachment-btn" onclick="document.getElementById('file-input').click()" title="Прикрепить файл">
                        <i class="fas fa-paperclip"></i>
                    </button>
                    <input type="file" id="file-input" accept="image/*,video/*,text/*,.pdf,.doc,.docx" style="display:none" onchange="handleFileSelect(this)">
                    <textarea class="msg-input" id="msg-input" placeholder="Написать сообщение..." rows="1" onkeydown="handleKeydown(event)"></textarea>
                    <button class="send-btn" onclick="sendMessage()" title="Отправить">
                        <i class="fas fa-paper-plane"></i>
                    </button>
                </div>
                
                <div class="emoji-picker-container" id="emoji-picker">
                    <div class="emoji-picker-glass">
                        <div class="emoji-grid" id="emoji-grid"></div>
                    </div>
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
        const user = "{session['username']}";
        let room = "favorites";
        let roomType = "favorites";
        let currentChannel = "";
        let currentCategory = "all";
        let isMobile = window.innerWidth <= 768;
        
        // Базовые эмодзи
        const emojis = ["😀", "😃", "😄", "😁", "😆", "😅", "😂", "🤣", "😊", "😇", "🙂", "🙃", "😉", "😌", "😍", "🥰", "😘", "😗", "😙", "😚", "😋", "😛", "😝", "😜", "🤪", "🤨", "🧐", "🤓", "😎", "🥸", "🤩", "🥳"];
        
        function checkMobile() {{
            isMobile = window.innerWidth <= 768;
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
            initEmojiPicker();
            
            if (isMobile) {{
                document.getElementById('chat-area').classList.remove('active');
            }} else {{
                openFavorites();
            }}
            
            window.addEventListener('resize', checkMobile);
            
            // Загружаем тему пользователя
            fetch('/user_info/' + user)
                .then(r => r.json())
                .then(userInfo => {{
                    if (userInfo.success && userInfo.theme) {{
                        document.documentElement.setAttribute('data-theme', userInfo.theme);
                    }}
                }});
        }};
        
        function initEmojiPicker() {{
            const emojiGrid = document.getElementById('emoji-grid');
            emojis.forEach(emoji => {{
                const emojiItem = document.createElement('div');
                emojiItem.className = 'emoji-item';
                emojiItem.textContent = emoji;
                emojiItem.onclick = () => insertEmoji(emoji);
                emojiGrid.appendChild(emojiItem);
            }});
        }}
        
        function insertEmoji(emoji) {{
            const input = document.getElementById('msg-input');
            const cursorPos = input.selectionStart;
            const textBefore = input.value.substring(0, cursorPos);
            const textAfter = input.value.substring(cursorPos);
            
            input.value = textBefore + emoji + textAfter;
            input.focus();
            input.selectionStart = input.selectionEnd = cursorPos + emoji.length;
            
            closeEmojiPicker();
        }}
        
        function toggleEmojiPicker() {{
            const emojiPicker = document.getElementById('emoji-picker');
            emojiPicker.style.display = emojiPicker.style.display === 'block' ? 'none' : 'block';
        }}
        
        function closeEmojiPicker() {{
            document.getElementById('emoji-picker').style.display = 'none';
        }}
        
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
        
        function createFavoriteItem(favorite) {{
            const item = document.createElement('div');
            item.className = `favorite-item ${{favorite.is_pinned ? 'pinned' : ''}}`;
            item.id = `favorite-${{favorite.id}}`;
            
            let contentHTML = '';
            
            if (favorite.content) {{
                contentHTML += `<div class="favorite-content">${{favorite.content}}</div>`;
            }}
            
            if (favorite.file_path) {{
                if (favorite.file_type === 'image' || favorite.file_name.match(/\\.(jpg|jpeg|png|gif|webp)$/i)) {{
                    contentHTML += `
                        <div class="favorite-file">
                            <img src="${{favorite.file_path}}" alt="${{favorite.file_name}}" onclick="openFilePreview('${{favorite.file_path}}')">
                        </div>
                    `;
                }} else if (favorite.file_type === 'video' || favorite.file_name.match(/\\.(mp4|webm|mov)$/i)) {{
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
        
        function filterFavorites(category) {{
            currentCategory = category;
            event?.currentTarget.classList.add('active');
            loadFavorites(category === 'all' ? null : category);
        }}
        
        function openFavorites() {{
            room = "favorites";
            roomType = "favorites";
            
            document.getElementById('chat-title').textContent = 'Избранное';
            document.getElementById('channel-description').textContent = '';
            document.getElementById('channel-header-avatar').style.display = 'none';
            document.getElementById('categories-filter').style.display = 'flex';
            document.getElementById('favorites-grid').style.display = 'grid';
            document.getElementById('channel-settings').style.display = 'none';
            document.getElementById('chat-messages').style.display = 'none';
            document.getElementById('input-area').style.display = 'none';
            document.getElementById('channel-actions').style.display = 'none';
            
            if (isMobile) {{
                document.getElementById('sidebar').classList.add('hidden');
                document.getElementById('chat-area').classList.add('active');
            }}
            
            loadFavorites(currentCategory === 'all' ? null : currentCategory);
        }}
        
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
        
        function openCreateChannelModal() {{
            document.getElementById('create-channel-modal').style.display = 'flex';
        }}
        
        function closeCreateChannelModal() {{
            document.getElementById('create-channel-modal').style.display = 'none';
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
        
        function loadUserChannels() {{
            fetch('/user_channels')
                .then(r => r.json())
                .then(data => {{
                    if (data.success) {{
                        const channelsContainer = document.getElementById('channels');
                        channelsContainer.innerHTML = '';
                        
                        data.channels.forEach(channel => {{
                            const el = document.createElement('div');
                            el.className = 'nav-item' + (room === 'channel_' + channel.name ? ' active' : '');
                            el.innerHTML = `<i class="fas fa-hashtag"></i><span>${{channel.display_name || channel.name}}</span>`;
                            el.onclick = () => openRoom('channel_' + channel.name, 'channel', channel.display_name || channel.name);
                            channelsContainer.appendChild(el);
                        }});
                    }}
                }});
        }}
        
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
                                el.innerHTML = `<i class="fas fa-user"></i><span>${{u.username}}</span>`;
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
                            el.innerHTML = `<i class="fas fa-user-friends"></i><span>${{chatUser}}</span>`;
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
            
            closeEmojiPicker();
            
            if (isMobile) {{
                document.getElementById('sidebar').classList.add('hidden');
                document.getElementById('chat-area').classList.add('active');
            }}
            
            const chatMessages = document.getElementById('chat-messages');
            chatMessages.innerHTML = '<div class="empty-chat"><i class="fas fa-comments"></i><h3>Начните общение</h3><p>Отправьте сообщение, чтобы начать чат</p></div>';
            
            const channelActions = document.getElementById('channel-actions');
            const channelAvatar = document.getElementById('channel-header-avatar');
            if (t === 'channel') {{
                channelActions.style.display = 'flex';
                channelAvatar.style.display = 'flex';
            }} else {{
                channelActions.style.display = 'none';
                channelAvatar.style.display = 'none';
            }}
            
            loadMessages(r);
            socket.emit('join', {{ room: r }});
        }}
        
        function loadMessages(roomName) {{
            fetch('/get_messages/' + roomName)
                .then(r => r.json())
                .then(messages => {{
                    const messagesContainer = document.getElementById('chat-messages');
                    messagesContainer.innerHTML = '';
                    
                    if (messages && Array.isArray(messages) && messages.length > 0) {{
                        messages.forEach(msg => {{
                            addMessageToChat(msg, roomName);
                        }});
                    }}
                    
                    messagesContainer.scrollTop = messagesContainer.scrollHeight;
                }});
        }}
        
        function addMessageToChat(data, roomName = '') {{
            const messagesContainer = document.getElementById('chat-messages');
            
            const emptyChat = messagesContainer.querySelector('.empty-chat');
            if (emptyChat) {{
                emptyChat.remove();
            }}
            
            const message = document.createElement('div');
            message.className = `message ${{data.user === user ? 'own' : 'other'}}`;
            
            const avatar = document.createElement('div');
            avatar.className = 'message-avatar';
            avatar.style.backgroundColor = data.color || '#6366F1';
            if (data.user !== user) {{
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
                text.textContent = data.message;
                content.appendChild(text);
            }}
            
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
            
            const time = document.createElement('div');
            time.className = 'message-time';
            time.textContent = data.timestamp || new Date().toLocaleTimeString([], {{ hour: '2-digit', minute: '2-digit' }});
            content.appendChild(time);
            
            message.appendChild(avatar);
            message.appendChild(content);
            messagesContainer.appendChild(message);
            
            messagesContainer.scrollTop = messagesContainer.scrollHeight;
        }}
        
        async function sendMessage() {{
            const input = document.getElementById('msg-input');
            const msg = input.value.trim();
            const fileInput = document.getElementById('file-input');
            
            if (!msg && !fileInput.files[0]) return;
            
            let fileData = null;
            let fileName = null;
            let fileType = null;
            
            if (fileInput.files[0]) {{
                const formData = new FormData();
                formData.append('file', fileInput.files[0]);
                
                try {{
                    const response = await fetch('/upload_file', {{
                        method: 'POST',
                        body: formData
                    }});
                    
                    const data = await response.json();
                    if (data.success) {{
                        fileData = data.path;
                        fileName = data.filename;
                        fileType = data.file_type;
                    }} else {{
                        alert('Ошибка загрузки файла: ' + data.error);
                        return;
                    }}
                }} catch (error) {{
                    alert('Ошибка соединения при загрузке файла');
                    return;
                }}
            }}
            
            const messageData = {{
                message: msg,
                room: room,
                type: roomType
            }};
            
            if (fileData) {{
                messageData.file = fileData;
                messageData.fileName = fileName;
                messageData.fileType = fileType;
            }}
            
            socket.emit('message', messageData);
            
            input.value = '';
            document.getElementById('file-preview').innerHTML = '';
            fileInput.value = '';
            closeEmojiPicker();
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
                            <div style="display: flex; align-items: center; gap: 10px; margin-top: 10px;">
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
                            <div style="display: flex; align-items: center; gap: 10px; margin-top: 10px;">
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
                            <div style="display: flex; align-items: center; gap: 10px; margin-top: 10px; padding: 10px; background: var(--bg); border-radius: 8px;">
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
        
        socket.on('message', (data) => {{
            if (data.room === room) {{
                addMessageToChat(data, room);
            }}
        }});
        
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
                    
                    loadFavorites(currentCategory === 'all' ? null : currentCategory);
                }}
            }});
        }}
        
        function openFilePreview(filePath) {{
            window.open(filePath, '_blank');
        }}
        
        socket.on('connect', function() {{
            console.log('Connected to server');
        }});
        
        socket.on('disconnect', function() {{
            console.log('Disconnected from server');
        }});
    </script>
</body>
</html>'''

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
        file_path = data.get('file')
        file_name = data.get('fileName')
        file_type = data.get('fileType', 'text')
        
        # Для приватных чатов
        recipient = None
        if room.startswith('private_'):
            parts = room.split('_')
            if len(parts) == 3:
                user1, user2 = parts[1], parts[2]
                recipient = user1 if user2 == session['username'] else user2
        
        # Сохраняем сообщение в БД
        msg_id = save_message(
            session['username'], 
            msg, 
            room, 
            recipient, 
            file_type, 
            file_path,
            file_name
        )
        
        # Получаем информацию об отправителе
        user_info = get_user(session['username'])
        user_color = user_info['avatar_color'] if user_info else '#6366F1'
        user_avatar_path = user_info['avatar_path'] if user_info else None
        
        # Подготавливаем данные для отправки
        message_data = {
            'user': session['username'], 
            'message': msg, 
            'color': user_color,
            'avatar_path': user_avatar_path,
            'timestamp': datetime.now().strftime('%H:%M'),
            'room': room
        }
        
        # Добавляем информацию о файле если есть
        if file_path:
            message_data['file'] = file_path
            message_data['fileName'] = file_name
            message_data['fileType'] = file_type
        
        # Отправляем сообщение всем в комнате
        emit('message', message_data, room=room)

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
