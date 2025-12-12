# web_messenger.py - AURA Messenger (единый файл)
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
                    avatar_color TEXT DEFAULT '#6366F1',
                    avatar_path TEXT,
                    theme TEXT DEFAULT 'dark',
                    profile_description TEXT DEFAULT ''
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
                    'profile_description': row[8] or ''
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

    @app.route('/update_profile_description', methods=['POST'])
    def update_profile_description_handler():
        if 'username' not in session: 
            return jsonify({'success': False, 'error': 'Не авторизован'})
        
        description = request.json.get('description', '').strip()
        success = update_profile_description(session['username'], description)
        if success:
            return jsonify({'success': True})
        return jsonify({'success': False, 'error': 'Ошибка обновления описания'})

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
        theme = request.json.get('theme', 'dark')
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
                'theme': user['theme'],
                'profile_description': user['profile_description']
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
                    f.write('AURA Messenger - Условия использования\n\n')
                    f.write('Это демонстрационный файл. В реальном приложении здесь был бы PDF документ.\n')
                
            privacy_file = os.path.join(docs_folder, 'privacy_policy.pdf')
            if not os.path.exists(privacy_file):
                with open(privacy_file, 'w', encoding='utf-8') as f:
                    f.write('AURA Messenger - Политика конфиденциальности\n\n')
                    f.write('Это демонстрационный файл. В реальном приложении здесь был бы PDF документ.\n')
            
            return jsonify({'success': True, 'message': 'Documents folder created'})
        except Exception as e:
            return jsonify({'success': False, 'error': str(e)})

    # === Основные маршруты ===
    @app.route('/')
    def index():
        if 'username' in session: 
            return redirect('/chat')
        
        # Страница входа/регистрации AURA
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
                    border-radius: 16px;
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
                
                .btn-google {
                    background: rgba(255, 255, 255, 0.05);
                    color: var(--text);
                    border: 2px solid var(--border);
                    margin-top: 16px;
                }
                
                .btn-google:hover {
                    background: rgba(255, 255, 255, 0.08);
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
                    background: rgba(220, 53, 69, 0.1);
                    color: #ff6b6b;
                    border-left: 4px solid #ff6b6b;
                }
                
                .alert-success {
                    background: rgba(16, 185, 129, 0.1);
                    color: #51cf66;
                    border-left: 4px solid #51cf66;
                }
                
                .terms {
                    text-align: center;
                    margin-top: 24px;
                    color: var(--text-light);
                    font-size: 0.9rem;
                }
                
                .terms a {
                    color: var(--primary-light);
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
                
                .glass-terms-container {
                    background: rgba(255, 255, 255, 0.03);
                    backdrop-filter: blur(20px);
                    -webkit-backdrop-filter: blur(20px);
                    border-radius: 24px;
                    border: 1px solid rgba(255, 255, 255, 0.1);
                    padding: 40px;
                    margin: 20px 0;
                    box-shadow: 
                        0 20px 60px rgba(0, 0, 0, 0.2),
                        inset 0 1px 0 rgba(255, 255, 255, 0.05);
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
                    background: linear-gradient(90deg, transparent, rgba(255, 255, 255, 0.1), transparent);
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
                    background: linear-gradient(90deg, transparent, var(--primary), var(--secondary), transparent);
                    border-radius: 2px;
                }
                
                .glass-icon {
                    width: 80px;
                    height: 80px;
                    margin: 0 auto 25px;
                    background: rgba(102, 126, 234, 0.1);
                    backdrop-filter: blur(10px);
                    border-radius: 50%;
                    display: flex;
                    align-items: center;
                    justify-content: center;
                    border: 1px solid rgba(255, 255, 255, 0.1);
                    box-shadow: 0 10px 30px rgba(0, 0, 0, 0.2);
                }
                
                .glass-icon i {
                    font-size: 36px;
                    background: linear-gradient(135deg, var(--primary), var(--secondary));
                    -webkit-background-clip: text;
                    -webkit-text-fill-color: transparent;
                    background-clip: text;
                }
                
                .glass-title {
                    font-size: 2.2rem;
                    font-weight: 800;
                    margin-bottom: 10px;
                    background: linear-gradient(135deg, var(--primary), var(--secondary));
                    -webkit-background-clip: text;
                    -webkit-text-fill-color: transparent;
                    background-clip: text;
                    letter-spacing: -0.5px;
                }
                
                .glass-subtitle {
                    color: var(--text-light);
                    font-size: 1.1rem;
                    font-weight: 500;
                }
                
                .glass-content {
                    margin-bottom: 40px;
                }
                
                .glass-section {
                    background: rgba(255, 255, 255, 0.02);
                    backdrop-filter: blur(10px);
                    border-radius: 20px;
                    padding: 30px;
                    margin-bottom: 25px;
                    border: 1px solid var(--border);
                    transition: all 0.3s ease;
                    position: relative;
                    overflow: hidden;
                }
                
                .glass-section:hover {
                    background: rgba(255, 255, 255, 0.03);
                    border-color: var(--primary);
                    transform: translateY(-2px);
                    box-shadow: 0 10px 30px rgba(0, 0, 0, 0.3);
                }
                
                .section-title {
                    font-size: 1.4rem;
                    margin-bottom: 20px;
                    color: var(--text);
                    display: flex;
                    align-items: center;
                    gap: 15px;
                    font-weight: 700;
                }
                
                .section-title i {
                    color: var(--primary);
                    font-size: 1.3rem;
                }
                
                .section-content {
                    color: var(--text);
                    line-height: 1.7;
                    opacity: 0.9;
                }
                
                .section-content p {
                    margin-bottom: 20px;
                }
                
                .glass-list {
                    margin: 25px 0;
                }
                
                .glass-list.negative .list-icon {
                    background: rgba(220, 53, 69, 0.1);
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
                    background: rgba(255, 255, 255, 0.01);
                    border-radius: 16px;
                    border: 1px solid var(--border);
                    transition: all 0.3s ease;
                }
                
                .list-item:hover {
                    background: rgba(255, 255, 255, 0.02);
                    border-color: var(--primary);
                    transform: translateX(5px);
                }
                
                .list-icon {
                    width: 50px;
                    height: 50px;
                    min-width: 50px;
                    background: rgba(102, 126, 234, 0.1);
                    backdrop-filter: blur(5px);
                    border-radius: 14px;
                    display: flex;
                    align-items: center;
                    justify-content: center;
                    border: 1px solid var(--border);
                }
                
                .list-icon i {
                    font-size: 22px;
                    background: linear-gradient(135deg, var(--primary), var(--secondary));
                    -webkit-background-clip: text;
                    -webkit-text-fill-color: transparent;
                }
                
                .list-text {
                    flex: 1;
                    font-size: 1.05rem;
                    color: var(--text);
                    line-height: 1.5;
                }
                
                .highlight {
                    background: linear-gradient(135deg, var(--primary), var(--secondary));
                    color: white;
                    padding: 2px 8px;
                    border-radius: 8px;
                    font-weight: 700;
                    border: 1px solid var(--border);
                }
                
                .glass-link {
                    display: inline-flex;
                    align-items: center;
                    gap: 12px;
                    padding: 16px 28px;
                    background: rgba(255, 255, 255, 0.03);
                    backdrop-filter: blur(10px);
                    border-radius: 16px;
                    color: var(--text);
                    text-decoration: none;
                    font-weight: 600;
                    border: 1px solid var(--border);
                    transition: all 0.3s ease;
                    margin: 15px 0;
                }
                
                .glass-link:hover {
                    background: rgba(255, 255, 255, 0.05);
                    border-color: var(--primary);
                    transform: translateY(-2px);
                    box-shadow: 0 10px 25px rgba(0, 0, 0, 0.3);
                }
                
                .glass-link i {
                    font-size: 1.3rem;
                    color: var(--primary);
                }
                
                .contact-link {
                    background: rgba(0, 119, 255, 0.1);
                    border-color: rgba(0, 119, 255, 0.3);
                }
                
                .contact-link i {
                    color: #0077ff;
                }
                
                .contact-note {
                    font-size: 0.95rem;
                    color: var(--text-light);
                    margin-top: 15px;
                    padding-left: 20px;
                    border-left: 3px solid var(--primary);
                }
                
                .version-info {
                    display: inline-flex;
                    align-items: center;
                    gap: 12px;
                    padding: 12px 24px;
                    background: rgba(102, 126, 234, 0.1);
                    border-radius: 12px;
                    border: 1px solid var(--primary);
                    margin-top: 20px;
                }
                
                .version-info i {
                    color: var(--primary);
                    font-size: 1.2rem;
                }
                
                .version-info span {
                    color: var(--text);
                    font-weight: 500;
                }
                
                .glass-footer {
                    padding-top: 40px;
                    border-top: 1px solid var(--border);
                }
                
                .accept-terms {
                    margin-bottom: 40px;
                }
                
                .checkbox-container {
                    display: flex;
                    align-items: center;
                    cursor: pointer;
                    font-size: 1.1rem;
                    color: var(--text);
                    user-select: none;
                    padding: 20px;
                    background: rgba(255, 255, 255, 0.02);
                    border-radius: 16px;
                    border: 2px solid var(--border);
                    transition: all 0.3s ease;
                }
                
                .checkbox-container:hover {
                    background: rgba(255, 255, 255, 0.03);
                    border-color: var(--primary);
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
                    background: rgba(255, 255, 255, 0.05);
                    border-radius: 8px;
                    margin-right: 20px;
                    border: 2px solid var(--border);
                    transition: all 0.3s ease;
                }
                
                .checkbox-container:hover .checkmark {
                    background: rgba(102, 126, 234, 0.1);
                    border-color: var(--primary);
                }
                
                .checkbox-container input:checked ~ .checkmark {
                    background: linear-gradient(135deg, var(--primary), var(--secondary));
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
                    background: rgba(102, 126, 234, 0.05);
                    backdrop-filter: blur(10px);
                    border: 2px dashed var(--primary);
                    padding: 30px;
                    border-radius: 20px;
                    text-align: center;
                }
                
                .glass-download p {
                    color: var(--text);
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
                    background: linear-gradient(135deg, var(--primary), var(--secondary));
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
                            <i class="fas fa-bolt"></i>
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
        
        # Генерируем HTML с дизайном AURA
        return f'''
<!DOCTYPE html>
<html lang="ru" data-theme="{theme}">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0, maximum-scale=1.0, user-scalable=no">
    <title>AURA Messenger - {username}</title>
    <link rel="stylesheet" href="https://cdnjs.cloudflare.com/ajax/libs/font-awesome/6.5.0/css/all.min.css">
    <style>
        /* ОСНОВНЫЕ СТИЛИ AURA */
        :root {{
            --bg: #0f0f23;
            --text: #ffffff;
            --input: #1a1a2e;
            --border: #2d2d4d;
            --accent: #667eea;
            --sidebar-width: 320px;
            --favorite-color: #ffd700;
            --primary: #667eea;
            --primary-dark: #5a67d8;
            --primary-light: #7c9bf2;
            --secondary: #8b5cf6;
            --success: #10b981;
        }}
        
        [data-theme="light"] {{
            --bg: #f8f9fa;
            --text: #1a1a2e;
            --input: #ffffff;
            --border: #e5e7eb;
            --accent: #667eea;
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
        
        /* Основной контейнер AURA */
        .app-container {{
            display: flex;
            height: 100vh;
            position: relative;
        }}
        
        /* Сайдбар AURA - левая панель */
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
            border-right: 1px solid var(--border);
        }}
        
        .sidebar.hidden {{
            transform: translateX(-100%);
        }}
        
        /* Заголовок AURA */
        .sidebar-header {{
            padding: 25px 20px;
            background: linear-gradient(135deg, #0f0f23 0%, #1a1a2e 100%);
            text-align: center;
            font-weight: 700;
            display: flex;
            align-items: center;
            justify-content: center;
            gap: 12px;
            position: relative;
            border-bottom: 1px solid var(--border);
        }}
        
        .menu-toggle {{
            position: absolute;
            left: 20px;
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
            background: linear-gradient(135deg, var(--primary), var(--secondary));
            display: flex;
            align-items: center;
            justify-content: center;
            font-size: 20px;
            font-weight: bold;
            box-shadow: 0 4px 12px rgba(0, 0, 0, 0.3);
        }}
        
        .app-title {{
            color: white;
            font-size: 2rem;
            font-weight: 800;
            letter-spacing: -0.5px;
        }}
        
        /* ПОИСК AURA */
        .search-container {{
            padding: 20px;
            border-bottom: 1px solid var(--border);
        }}
        
        .search-box {{
            position: relative;
        }}
        
        .search-input {{
            width: 100%;
            padding: 12px 16px 12px 44px;
            border: 1px solid var(--border);
            border-radius: 12px;
            background: rgba(255, 255, 255, 0.05);
            color: var(--text);
            font-size: 0.95rem;
            transition: all 0.2s ease;
        }}
        
        .search-input:focus {{
            outline: none;
            border-color: var(--accent);
            background: rgba(255, 255, 255, 0.08);
        }}
        
        .search-icon {{
            position: absolute;
            left: 16px;
            top: 50%;
            transform: translateY(-50%);
            color: var(--text-light);
            font-size: 1rem;
        }}
        
        /* Навигационные категории AURA */
        .nav-categories {{
            display: flex;
            padding: 0 20px;
            border-bottom: 1px solid var(--border);
            overflow-x: auto;
            -webkit-overflow-scrolling: touch;
        }}
        
        .nav-category {{
            padding: 12px 16px;
            font-size: 0.85rem;
            font-weight: 500;
            color: var(--text-light);
            cursor: pointer;
            transition: all 0.2s ease;
            white-space: nowrap;
            border-bottom: 2px solid transparent;
        }}
        
        .nav-category:hover {{
            color: var(--text);
        }}
        
        .nav-category.active {{
            color: var(--accent);
            border-bottom-color: var(--accent);
        }}
        
        /* Информация о пользователе AURA */
        .user-info {{
            padding: 20px;
            display: flex;
            gap: 15px;
            align-items: center;
            border-bottom: 1px solid var(--border);
        }}
        
        .avatar {{
            width: 48px;
            height: 48px;
            border-radius: 50%;
            background: var(--accent);
            color: white;
            display: flex;
            align-items: center;
            justify-content: center;
            font-weight: bold;
            font-size: 1.2rem;
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
            font-size: 1.1rem;
            margin-bottom: 4px;
        }}
        
        .user-status {{
            font-size: 0.85rem;
            opacity: 0.8;
            display: flex;
            align-items: center;
            gap: 6px;
        }}
        
        .status-dot {{
            width: 8px;
            height: 8px;
            border-radius: 50%;
            background: var(--success);
        }}
        
        /* Основная навигация AURA */
        .nav {{
            flex: 1;
            overflow-y: auto;
            padding: 15px;
            -webkit-overflow-scrolling: touch;
        }}
        
        .nav-title {{
            padding: 12px 15px;
            font-size: 0.8rem;
            color: var(--text-light);
            text-transform: uppercase;
            font-weight: 600;
            letter-spacing: 0.5px;
            display: flex;
            justify-content: space-between;
            align-items: center;
        }}
        
        .nav-item {{
            padding: 12px 15px;
            cursor: pointer;
            border-radius: 10px;
            margin: 4px 0;
            transition: all 0.2s ease;
            display: flex;
            align-items: center;
            gap: 12px;
            user-select: none;
            background: transparent;
            border: 1px solid transparent;
        }}
        
        .nav-item:hover {{
            background: rgba(255, 255, 255, 0.05);
            border-color: var(--border);
        }}
        
        .nav-item.active {{
            background: rgba(var(--primary-rgb), 0.1);
            border-color: var(--accent);
            color: var(--accent);
        }}
        
        .nav-item i {{
            width: 20px;
            text-align: center;
            font-size: 1rem;
        }}
        
        .add-btn {{
            background: none;
            border: none;
            color: var(--text-light);
            cursor: pointer;
            padding: 4px;
            border-radius: 4px;
            display: flex;
            align-items: center;
            justify-content: center;
            font-size: 0.9rem;
        }}
        
        .add-btn:hover {{
            color: var(--text);
            background: rgba(255, 255, 255, 0.05);
        }}
        
        /* Каналы AURA с информацией о подписчиках */
        .channel-item {{
            display: flex;
            align-items: center;
            justify-content: space-between;
            width: 100%;
        }}
        
        .channel-info {{
            display: flex;
            align-items: center;
            gap: 12px;
            flex: 1;
        }}
        
        .channel-avatar {{
            width: 36px;
            height: 36px;
            border-radius: 50%;
            background: var(--accent);
            color: white;
            display: flex;
            align-items: center;
            justify-content: center;
            font-weight: bold;
            font-size: 0.9rem;
            background-size: cover;
            background-position: center;
            flex-shrink: 0;
        }}
        
        .channel-name {{
            flex: 1;
            font-size: 0.95rem;
        }}
        
        .channel-stats {{
            display: flex;
            align-items: center;
            gap: 8px;
            font-size: 0.8rem;
            color: var(--text-light);
        }}
        
        .subscriber-count {{
            display: flex;
            align-items: center;
            gap: 4px;
        }}
        
        .join-btn {{
            padding: 6px 12px;
            background: var(--accent);
            color: white;
            border: none;
            border-radius: 8px;
            font-size: 0.8rem;
            cursor: pointer;
            transition: all 0.2s ease;
            font-weight: 500;
        }}
        
        .join-btn:hover {{
            background: var(--primary-dark);
            transform: translateY(-1px);
        }}
        
        /* Личные чаты AURA */
        .chat-item {{
            display: flex;
            align-items: center;
            gap: 12px;
            padding: 12px 15px;
            cursor: pointer;
            border-radius: 10px;
            margin: 4px 0;
            transition: all 0.2s ease;
        }}
        
        .chat-item:hover {{
            background: rgba(255, 255, 255, 0.05);
        }}
        
        .chat-avatar {{
            width: 40px;
            height: 40px;
            border-radius: 50%;
            background: var(--accent);
            color: white;
            display: flex;
            align-items: center;
            justify-content: center;
            font-weight: bold;
            font-size: 0.9rem;
            background-size: cover;
            background-position: center;
            flex-shrink: 0;
        }}
        
        .chat-info {{
            flex: 1;
            min-width: 0;
        }}
        
        .chat-name {{
            font-size: 0.95rem;
            font-weight: 500;
            margin-bottom: 2px;
        }}
        
        .chat-last-message {{
            font-size: 0.85rem;
            color: var(--text-light);
            white-space: nowrap;
            overflow: hidden;
            text-overflow: ellipsis;
        }}
        
        /* Кнопка выхода AURA */
        .logout-btn {{
            margin: 20px;
            padding: 12px;
            background: rgba(220, 53, 69, 0.1);
            color: #dc3545;
            border: 1px solid rgba(220, 53, 69, 0.3);
            border-radius: 10px;
            cursor: pointer;
            font-weight: 600;
            display: flex;
            align-items: center;
            justify-content: center;
            gap: 8px;
            transition: all 0.2s ease;
        }}
        
        .logout-btn:hover {{
            background: rgba(220, 53, 69, 0.2);
            transform: translateY(-1px);
        }}
        
        /* Область чата AURA */
        .chat-area {{
            flex: 1;
            display: flex;
            flex-direction: column;
            position: absolute;
            top: 0;
            left: 0;
            right: 0;
            bottom: 0;
            background: var(--bg);
            z-index: 900;
            transform: translateX(100%);
            transition: transform 0.3s cubic-bezier(0.4, 0, 0.2, 1);
        }}
        
        .chat-area.active {{
            transform: translateX(0);
        }}
        
        /* Заголовок чата AURA */
        .chat-header {{
            padding: 20px;
            background: var(--input);
            border-bottom: 1px solid var(--border);
            display: flex;
            align-items: center;
            gap: 15px;
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
        
        .chat-header-avatar {{
            width: 44px;
            height: 44px;
            border-radius: 50%;
            background: var(--accent);
            color: white;
            display: flex;
            align-items: center;
            justify-content: center;
            font-weight: bold;
            font-size: 1rem;
            flex-shrink: 0;
            background-size: cover;
            background-position: center;
            cursor: pointer;
        }}
        
        .chat-header-info {{
            flex: 1;
        }}
        
        .chat-title {{
            font-size: 1.2rem;
            font-weight: 600;
            margin-bottom: 4px;
        }}
        
        .chat-subtitle {{
            font-size: 0.9rem;
            color: var(--text-light);
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
            padding: 8px;
            border-radius: 8px;
            display: flex;
            align-items: center;
            justify-content: center;
            transition: all 0.2s ease;
        }}
        
        .channel-btn:hover {{
            background: rgba(255, 255, 255, 0.05);
            color: var(--accent);
        }}
        
        /* Сообщения AURA */
        .messages {{
            flex: 1;
            padding: 20px;
            overflow-y: auto;
            display: flex;
            flex-direction: column;
            gap: 16px;
            -webkit-overflow-scrolling: touch;
        }}
        
        .message {{
            display: flex;
            align-items: flex-start;
            gap: 12px;
            max-width: 85%;
            animation: fadeIn 0.3s ease;
        }}
        
        .message.own {{
            align-self: flex-end;
            flex-direction: row-reverse;
        }}
        
        .message-avatar {{
            width: 36px;
            height: 36px;
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
            cursor: pointer;
        }}
        
        .message-content {{
            background: var(--input);
            padding: 12px 16px;
            border-radius: 18px;
            border-top-left-radius: 4px;
            max-width: 100%;
            word-wrap: break-word;
            border: 1px solid var(--border);
        }}
        
        .message.own .message-content {{
            background: linear-gradient(135deg, var(--primary), var(--secondary));
            color: white;
            border-top-left-radius: 18px;
            border-top-right-radius: 4px;
            border: none;
        }}
        
        .message-sender {{
            font-weight: 600;
            font-size: 0.9rem;
            margin-bottom: 6px;
            color: var(--text);
        }}
        
        .message.own .message-sender {{
            color: rgba(255, 255, 255, 0.9);
        }}
        
        .message-text {{
            line-height: 1.4;
            font-size: 0.95rem;
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
            color: var(--text-light);
            margin-top: 6px;
            text-align: right;
        }}
        
        .message.own .message-time {{
            color: rgba(255, 255, 255, 0.7);
        }}
        
        /* Поле ввода AURA */
        .input-area {{
            padding: 20px;
            background: var(--input);
            border-top: 1px solid var(--border);
        }}
        
        .input-row {{
            display: flex;
            gap: 10px;
            align-items: flex-end;
        }}
        
        .attachment-btn {{
            background: rgba(255, 255, 255, 0.05);
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
            transition: all 0.2s ease;
        }}
        
        .attachment-btn:hover {{
            background: rgba(255, 255, 255, 0.1);
            border-color: var(--accent);
            color: var(--accent);
        }}
        
        .emoji-btn {{
            background: rgba(255, 255, 255, 0.05);
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
            transition: all 0.2s ease;
        }}
        
        .emoji-btn:hover {{
            background: rgba(255, 255, 255, 0.1);
            border-color: var(--accent);
            color: var(--accent);
        }}
        
        .emoji-btn.active {{
            background: var(--accent);
            color: white;
            border-color: var(--accent);
        }}
        
        .msg-input {{
            flex: 1;
            padding: 14px 18px;
            border: 1px solid var(--border);
            border-radius: 24px;
            background: rgba(255, 255, 255, 0.05);
            color: var(--text);
            font-size: 1rem;
            resize: none;
            max-height: 120px;
            min-height: 48px;
            line-height: 1.4;
            transition: all 0.2s ease;
        }}
        
        .msg-input:focus {{
            outline: none;
            border-color: var(--accent);
            background: rgba(255, 255, 255, 0.08);
        }}
        
        .send-btn {{
            width: 48px;
            height: 48px;
            border-radius: 50%;
            background: linear-gradient(135deg, var(--primary), var(--secondary));
            color: white;
            border: none;
            cursor: pointer;
            display: flex;
            align-items: center;
            justify-content: center;
            flex-shrink: 0;
            transition: all 0.2s ease;
            font-size: 1.2rem;
        }}
        
        .send-btn:hover {{
            transform: translateY(-2px);
            box-shadow: 0 6px 20px rgba(102, 126, 234, 0.4);
        }}
        
        .send-btn:active {{
            transform: translateY(0);
        }}
        
        /* Блок эмодзи AURA */
        .emoji-container {{
            display: none;
            position: absolute;
            bottom: 80px;
            left: 20px;
            z-index: 1001;
            animation: fadeInUp 0.2s ease-out;
        }}
        
        .emoji-picker {{
            background: var(--input);
            backdrop-filter: blur(20px);
            -webkit-backdrop-filter: blur(20px);
            border-radius: 20px;
            border: 1px solid var(--border);
            box-shadow: 0 15px 50px rgba(0, 0, 0, 0.3);
            padding: 15px;
            width: 300px;
            max-height: 400px;
            overflow-y: auto;
            -webkit-overflow-scrolling: touch;
        }}
        
        .emoji-header {{
            display: flex;
            justify-content: space-between;
            align-items: center;
            margin-bottom: 12px;
            padding-bottom: 10px;
            border-bottom: 1px solid var(--border);
        }}
        
        .emoji-title {{
            font-weight: 600;
            font-size: 0.9rem;
            color: var(--text);
        }}
        
        .emoji-search {{
            width: 100%;
            padding: 8px 12px;
            border: 1px solid var(--border);
            border-radius: 10px;
            background: rgba(255, 255, 255, 0.05);
            margin-bottom: 10px;
            font-size: 0.9rem;
            color: var(--text);
        }}
        
        .emoji-search:focus {{
            outline: none;
            border-color: var(--accent);
        }}
        
        .emoji-grid {{
            display: grid;
            grid-template-columns: repeat(8, 1fr);
            gap: 8px;
        }}
        
        .emoji-item {{
            font-size: 1.5rem;
            width: 36px;
            height: 36px;
            display: flex;
            align-items: center;
            justify-content: center;
            cursor: pointer;
            border-radius: 10px;
            transition: all 0.2s ease;
            user-select: none;
        }}
        
        .emoji-item:hover {{
            background: rgba(var(--primary-rgb), 0.2);
            transform: scale(1.1);
        }}
        
        /* Избранное AURA (Все заметки) */
        .favorites-grid {{
            display: grid;
            grid-template-columns: repeat(auto-fill, minmax(280px, 1fr));
            gap: 20px;
            padding: 20px;
        }}
        
        .favorite-item {{
            background: var(--input);
            border-radius: 16px;
            padding: 20px;
            border: 1px solid var(--border);
            position: relative;
            transition: all 0.2s ease;
        }}
        
        .favorite-item:hover {{
            transform: translateY(-4px);
            box-shadow: 0 10px 30px rgba(0, 0, 0, 0.3);
            border-color: var(--accent);
        }}
        
        .favorite-item.pinned {{
            border-left: 4px solid var(--favorite-color);
        }}
        
        .favorite-content {{
            margin-bottom: 15px;
            word-break: break-word;
            font-size: 0.95rem;
            line-height: 1.5;
        }}
        
        .favorite-file {{
            max-width: 100%;
            border-radius: 12px;
            overflow: hidden;
            margin-bottom: 15px;
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
            color: var(--text-light);
            margin-top: 15px;
        }}
        
        .favorite-actions {{
            position: absolute;
            top: 15px;
            right: 15px;
            display: flex;
            gap: 8px;
            opacity: 0;
            transition: opacity 0.2s ease;
        }}
        
        .favorite-item:hover .favorite-actions {{
            opacity: 1;
        }}
        
        .favorite-action-btn {{
            background: rgba(0, 0, 0, 0.5);
            color: white;
            border: none;
            width: 32px;
            height: 32px;
            border-radius: 50%;
            display: flex;
            align-items: center;
            justify-content: center;
            cursor: pointer;
            font-size: 0.9rem;
            transition: all 0.2s ease;
        }}
        
        .favorite-action-btn:hover {{
            background: var(--accent);
            transform: scale(1.1);
        }}
        
        .category-badge {{
            display: inline-block;
            padding: 4px 10px;
            background: rgba(var(--primary-rgb), 0.1);
            color: var(--accent);
            border-radius: 12px;
            font-size: 0.75rem;
            font-weight: 500;
            border: 1px solid rgba(var(--primary-rgb), 0.3);
        }}
        
        /* Категории избранного AURA */
        .categories-filter {{
            display: flex;
            gap: 10px;
            padding: 20px;
            background: var(--input);
            border-bottom: 1px solid var(--border);
            flex-wrap: wrap;
            overflow-x: auto;
            -webkit-overflow-scrolling: touch;
        }}
        
        .category-filter-btn {{
            padding: 8px 16px;
            background: rgba(255, 255, 255, 0.05);
            border: 1px solid var(--border);
            border-radius: 20px;
            cursor: pointer;
            font-size: 0.9rem;
            transition: all 0.2s ease;
            white-space: nowrap;
            color: var(--text);
        }}
        
        .category-filter-btn:hover {{
            background: rgba(255, 255, 255, 0.1);
            border-color: var(--accent);
        }}
        
        .category-filter-btn.active {{
            background: var(--accent);
            color: white;
            border-color: var(--accent);
        }}
        
        /* Пустые состояния AURA */
        .empty-state {{
            text-align: center;
            padding: 60px 20px;
            color: var(--text-light);
        }}
        
        .empty-state i {{
            font-size: 3rem;
            margin-bottom: 20px;
            color: var(--border);
        }}
        
        .empty-state h3 {{
            font-size: 1.3rem;
            margin-bottom: 10px;
            color: var(--text);
        }}
        
        .empty-state p {{
            font-size: 0.95rem;
            max-width: 300px;
            margin: 0 auto 20px;
            line-height: 1.5;
        }}
        
        /* Модальные окна AURA */
        .modal {{
            display: none;
            position: fixed;
            top: 0;
            left: 0;
            width: 100%;
            height: 100%;
            background: rgba(0, 0, 0, 0.7);
            backdrop-filter: blur(8px);
            -webkit-backdrop-filter: blur(8px);
            z-index: 2000;
            align-items: center;
            justify-content: center;
            padding: 20px;
        }}
        
        .modal-content {{
            background: var(--input);
            padding: 30px;
            border-radius: 20px;
            width: 100%;
            max-width: 500px;
            max-height: 90vh;
            overflow-y: auto;
            -webkit-overflow-scrolling: touch;
            border: 1px solid var(--border);
            box-shadow: 0 25px 60px rgba(0, 0, 0, 0.4);
        }}
        
        .form-group {{
            margin-bottom: 20px;
        }}
        
        .form-label {{
            display: block;
            margin-bottom: 8px;
            font-weight: 500;
            color: var(--text);
            font-size: 0.95rem;
        }}
        
        .form-control {{
            width: 100%;
            padding: 12px 16px;
            border: 1px solid var(--border);
            border-radius: 12px;
            background: rgba(255, 255, 255, 0.05);
            color: var(--text);
            font-size: 1rem;
            transition: all 0.2s ease;
        }}
        
        .form-control:focus {{
            outline: none;
            border-color: var(--accent);
            background: rgba(255, 255, 255, 0.08);
        }}
        
        .btn {{
            padding: 12px 24px;
            border: none;
            border-radius: 12px;
            cursor: pointer;
            font-weight: 600;
            transition: all 0.2s ease;
            font-size: 0.95rem;
            display: inline-flex;
            align-items: center;
            justify-content: center;
            gap: 8px;
        }}
        
        .btn-primary {{
            background: linear-gradient(135deg, var(--primary), var(--secondary));
            color: white;
        }}
        
        .btn-primary:hover {{
            transform: translateY(-2px);
            box-shadow: 0 8px 25px rgba(102, 126, 234, 0.4);
        }}
        
        .btn-secondary {{
            background: rgba(255, 255, 255, 0.05);
            color: var(--text);
            border: 1px solid var(--border);
        }}
        
        .btn-secondary:hover {{
            background: rgba(255, 255, 255, 0.1);
            border-color: var(--accent);
        }}
        
        /* Настройки темы AURA */
        .theme-buttons {{
            display: flex;
            gap: 10px;
            margin-top: 15px;
        }}
        
        .theme-btn {{
            flex: 1;
            padding: 15px;
            border: none;
            border-radius: 12px;
            cursor: pointer;
            font-weight: 500;
            background: rgba(255, 255, 255, 0.05);
            color: var(--text);
            border: 1px solid var(--border);
            transition: all 0.2s ease;
        }}
        
        .theme-btn:hover {{
            background: rgba(255, 255, 255, 0.1);
            transform: translateY(-2px);
        }}
        
        .theme-btn.active {{
            background: var(--accent);
            color: white;
            border-color: var(--accent);
        }}
        
        /* Аватары AURA */
        .avatar-upload {{
            text-align: center;
            margin: 20px 0;
        }}
        
        .avatar-preview {{
            width: 120px;
            height: 120px;
            border-radius: 50%;
            margin: 0 auto 20px;
            background: linear-gradient(135deg, var(--primary), var(--secondary));
            background-size: cover;
            background-position: center;
            cursor: pointer;
            border: 3px solid var(--accent);
            display: flex;
            align-items: center;
            justify-content: center;
            color: white;
            font-weight: bold;
            font-size: 2rem;
        }}
        
        /* Создание канала AURA */
        .glass-modal-overlay {{
            display: none;
            position: fixed;
            top: 0;
            left: 0;
            width: 100%;
            height: 100%;
            background: rgba(0, 0, 0, 0.8);
            backdrop-filter: blur(12px);
            -webkit-backdrop-filter: blur(12px);
            z-index: 2000;
            animation: fadeIn 0.3s ease-out;
            align-items: center;
            justify-content: center;
            padding: 20px;
        }}
        
        .glass-modal-container {{
            background: rgba(255, 255, 255, 0.05);
            backdrop-filter: blur(20px);
            -webkit-backdrop-filter: blur(20px);
            border-radius: 24px;
            border: 1px solid rgba(255, 255, 255, 0.1);
            padding: 40px;
            width: 100%;
            max-width: 500px;
            max-height: 90vh;
            overflow-y: auto;
            -webkit-overflow-scrolling: touch;
            box-shadow: 0 25px 60px rgba(0, 0, 0, 0.4);
            position: relative;
            animation: slideUp 0.4s cubic-bezier(0.4, 0, 0.2, 1);
        }}
        
        .glass-modal-header {{
            text-align: center;
            margin-bottom: 30px;
            position: relative;
            padding-bottom: 20px;
        }}
        
        .glass-modal-header::after {{
            content: '';
            position: absolute;
            bottom: 0;
            left: 20%;
            right: 20%;
            height: 2px;
            background: linear-gradient(90deg, transparent, var(--primary), var(--secondary), transparent);
            border-radius: 2px;
        }}
        
        .glass-modal-icon {{
            width: 70px;
            height: 70px;
            margin: 0 auto 20px;
            background: rgba(102, 126, 234, 0.1);
            backdrop-filter: blur(10px);
            border-radius: 20px;
            display: flex;
            align-items: center;
            justify-content: center;
            border: 1px solid rgba(255, 255, 255, 0.1);
            box-shadow: 0 10px 30px rgba(0, 0, 0, 0.2);
        }}
        
        .glass-modal-icon i {{
            font-size: 32px;
            background: linear-gradient(135deg, var(--primary), var(--secondary));
            -webkit-background-clip: text;
            -webkit-text-fill-color: transparent;
            background-clip: text;
        }}
        
        .glass-modal-title {{
            font-size: 1.8rem;
            font-weight: 800;
            margin-bottom: 8px;
            background: linear-gradient(135deg, var(--primary), var(--secondary));
            -webkit-background-clip: text;
            -webkit-text-fill-color: transparent;
            background-clip: text;
            letter-spacing: -0.5px;
        }}
        
        .glass-modal-subtitle {{
            color: var(--text-light);
            font-size: 1rem;
            font-weight: 400;
        }}
        
        .glass-form-group {{
            margin-bottom: 25px;
        }}
        
        .glass-form-label {{
            display: block;
            margin-bottom: 10px;
            font-weight: 600;
            color: var(--text);
            font-size: 0.95rem;
            display: flex;
            align-items: center;
            gap: 10px;
        }}
        
        .glass-form-label i {{
            font-size: 1.1rem;
            color: var(--primary);
        }}
        
        .glass-form-input {{
            width: 100%;
            padding: 16px 20px;
            border: 1px solid var(--border);
            border-radius: 16px;
            background: rgba(255, 255, 255, 0.05);
            color: var(--text);
            font-size: 1rem;
            transition: all 0.3s ease;
        }}
        
        .glass-form-input:focus {{
            outline: none;
            border-color: var(--accent);
            background: rgba(255, 255, 255, 0.08);
            box-shadow: 0 0 0 3px rgba(102, 126, 234, 0.1);
        }}
        
        .glass-form-input::placeholder {{
            color: var(--text-light);
        }}
        
        .glass-form-textarea {{
            min-height: 100px;
            resize: vertical;
        }}
        
        .glass-form-checkbox {{
            display: flex;
            align-items: center;
            gap: 12px;
            cursor: pointer;
            user-select: none;
            padding: 15px;
            background: rgba(255, 255, 255, 0.02);
            border-radius: 14px;
            border: 1px solid var(--border);
            transition: all 0.3s ease;
        }}
        
        .glass-form-checkbox:hover {{
            background: rgba(255, 255, 255, 0.05);
            border-color: var(--accent);
        }}
        
        .glass-form-checkbox input {{
            width: 20px;
            height: 20px;
            border-radius: 6px;
            border: 2px solid var(--border);
            background: rgba(255, 255, 255, 0.05);
            cursor: pointer;
            position: relative;
            appearance: none;
            -webkit-appearance: none;
        }}
        
        .glass-form-checkbox input:checked {{
            background: linear-gradient(135deg, var(--primary), var(--secondary));
            border-color: transparent;
        }}
        
        .glass-form-checkbox input:checked::after {{
            content: '✓';
            position: absolute;
            top: 50%;
            left: 50%;
            transform: translate(-50%, -50%);
            color: white;
            font-size: 12px;
            font-weight: bold;
        }}
        
        .glass-form-checkbox-text {{
            flex: 1;
            color: var(--text);
            font-weight: 500;
        }}
        
        .glass-modal-buttons {{
            display: flex;
            gap: 15px;
            margin-top: 30px;
        }}
        
        .glass-btn {{
            flex: 1;
            padding: 18px;
            border: none;
            border-radius: 16px;
            font-weight: 700;
            font-size: 1rem;
            cursor: pointer;
            transition: all 0.3s ease;
            display: flex;
            align-items: center;
            justify-content: center;
            gap: 12px;
        }}
        
        .glass-btn-primary {{
            background: linear-gradient(135deg, var(--primary), var(--secondary));
            color: white;
            box-shadow: 0 8px 25px rgba(102, 126, 234, 0.3);
        }}
        
        .glass-btn-primary:hover {{
            transform: translateY(-3px);
            box-shadow: 0 12px 35px rgba(102, 126, 234, 0.4);
        }}
        
        .glass-btn-secondary {{
            background: rgba(255, 255, 255, 0.05);
            border: 1px solid var(--border);
            color: var(--text);
        }}
        
        .glass-btn-secondary:hover {{
            background: rgba(255, 255, 255, 0.1);
            transform: translateY(-2px);
        }}
        
        .glass-close-btn {{
            position: absolute;
            top: 20px;
            right: 20px;
            background: rgba(255, 255, 255, 0.05);
            border: 1px solid var(--border);
            color: var(--text);
            width: 36px;
            height: 36px;
            border-radius: 50%;
            display: flex;
            align-items: center;
            justify-content: center;
            cursor: pointer;
            transition: all 0.3s ease;
            z-index: 10;
        }}
        
        .glass-close-btn:hover {{
            background: rgba(255, 255, 255, 0.1);
            transform: rotate(90deg);
        }}
        
        /* Анимации */
        @keyframes fadeIn {{
            from {{ opacity: 0; }}
            to {{ opacity: 1; }}
        }}
        
        @keyframes fadeInUp {{
            from {{
                opacity: 0;
                transform: translateY(20px);
            }}
            to {{
                opacity: 1;
                transform: translateY(0);
            }}
        }}
        
        @keyframes slideUp {{
            from {{
                opacity: 0;
                transform: translateY(30px) scale(0.95);
            }}
            to {{
                opacity: 1;
                transform: translateY(0) scale(1);
            }}
        }}
        
        /* Скроллбар */
        ::-webkit-scrollbar {{
            width: 8px;
        }}
        
        ::-webkit-scrollbar-track {{
            background: transparent;
        }}
        
        ::-webkit-scrollbar-thumb {{
            background: var(--border);
            border-radius: 4px;
        }}
        
        ::-webkit-scrollbar-thumb:hover {{
            background: var(--accent);
        }}
        
        /* Медиа-запросы для мобильных */
        @media (max-width: 768px) {{
            .menu-toggle {{
                display: block;
            }}
            
            .back-btn {{
                display: block;
            }}
            
            .sidebar-header {{
                padding: 20px;
            }}
            
            .app-title {{
                font-size: 1.8rem;
            }}
            
            .nav-categories {{
                padding: 0 15px;
            }}
            
            .nav-category {{
                padding: 10px 12px;
                font-size: 0.8rem;
            }}
            
            .user-info {{
                padding: 15px;
            }}
            
            .avatar {{
                width: 44px;
                height: 44px;
                font-size: 1.1rem;
            }}
            
            .favorites-grid {{
                grid-template-columns: 1fr;
                gap: 15px;
                padding: 15px;
            }}
            
            .message-content {{
                max-width: 90%;
            }}
            
            .modal-content {{
                padding: 20px;
                margin: 10px;
            }}
            
            .input-area {{
                position: fixed;
                bottom: 0;
                left: 0;
                right: 0;
                padding: 15px;
                background: var(--input);
                border-top: 1px solid var(--border);
                z-index: 1000;
            }}
            
            .msg-input {{
                padding: 12px 16px;
                font-size: 16px;
            }}
            
            .messages {{
                padding-bottom: 80px;
            }}
            
            .chat-header {{
                padding: 15px;
            }}
            
            .glass-modal-container {{
                padding: 25px 20px;
            }}
            
            .glass-modal-title {{
                font-size: 1.5rem;
            }}
            
            .glass-modal-icon {{
                width: 60px;
                height: 60px;
            }}
            
            .glass-modal-icon i {{
                font-size: 26px;
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
        }}
        
        /* Профиль пользователя AURA */
        .profile-modal-overlay {{
            display: none;
            position: fixed;
            top: 0;
            left: 0;
            width: 100%;
            height: 100%;
            background: rgba(0, 0, 0, 0.8);
            backdrop-filter: blur(10px);
            -webkit-backdrop-filter: blur(10px);
            z-index: 2001;
            animation: fadeIn 0.3s ease-out;
            align-items: center;
            justify-content: center;
            padding: 20px;
        }}
        
        .profile-modal-container {{
            background: var(--input);
            backdrop-filter: blur(20px);
            -webkit-backdrop-filter: blur(20px);
            border-radius: 24px;
            border: 1px solid var(--border);
            padding: 30px;
            width: 100%;
            max-width: 400px;
            max-height: 90vh;
            overflow-y: auto;
            -webkit-overflow-scrolling: touch;
            box-shadow: 0 25px 60px rgba(0, 0, 0, 0.4);
            position: relative;
            animation: slideUp 0.4s cubic-bezier(0.4, 0, 0.2, 1);
        }}
        
        .profile-modal-header {{
            text-align: center;
            margin-bottom: 25px;
            position: relative;
            padding-bottom: 20px;
        }}
        
        .profile-modal-header::after {{
            content: '';
            position: absolute;
            bottom: 0;
            left: 25%;
            right: 25%;
            height: 2px;
            background: linear-gradient(90deg, transparent, var(--primary), var(--secondary), transparent);
            border-radius: 2px;
        }}
        
        .profile-avatar-large {{
            width: 100px;
            height: 100px;
            margin: 0 auto 20px;
            border-radius: 50%;
            background: linear-gradient(135deg, var(--primary), var(--secondary));
            display: flex;
            align-items: center;
            justify-content: center;
            color: white;
            font-weight: bold;
            font-size: 2rem;
            background-size: cover;
            background-position: center;
            border: 4px solid var(--accent);
            box-shadow: 0 10px 30px rgba(0, 0, 0, 0.3);
        }}
        
        .profile-username {{
            font-size: 1.5rem;
            font-weight: 800;
            margin-bottom: 5px;
            color: var(--text);
        }}
        
        .profile-status {{
            display: flex;
            align-items: center;
            justify-content: center;
            gap: 8px;
            color: var(--text-light);
            font-size: 0.9rem;
            margin-bottom: 5px;
        }}
        
        .status-dot-large {{
            width: 10px;
            height: 10px;
            border-radius: 50%;
            background: var(--success);
        }}
        
        .profile-description {{
            margin: 25px 0;
            text-align: center;
        }}
        
        .profile-description-label {{
            font-size: 0.9rem;
            color: var(--text-light);
            margin-bottom: 10px;
            font-weight: 600;
        }}
        
        .profile-description-text {{
            background: rgba(255, 255, 255, 0.02);
            backdrop-filter: blur(10px);
            border-radius: 12px;
            padding: 15px;
            color: var(--text);
            font-size: 0.95rem;
            line-height: 1.5;
            min-height: 80px;
            border: 1px solid var(--border);
        }}
        
        .profile-close-btn {{
            position: absolute;
            top: 15px;
            right: 15px;
            background: rgba(255, 255, 255, 0.05);
            border: 1px solid var(--border);
            color: var(--text);
            width: 32px;
            height: 32px;
            border-radius: 50%;
            display: flex;
            align-items: center;
            justify-content: center;
            cursor: pointer;
            transition: all 0.3s ease;
            z-index: 10;
        }}
        
        .profile-close-btn:hover {{
            background: rgba(255, 255, 255, 0.1);
            transform: rotate(90deg);
        }}
        
        /* Поддержка AURA */
        .support-content {{
            padding: 30px;
            max-width: 600px;
            margin: 0 auto;
        }}
        
        .support-section {{
            background: var(--input);
            border-radius: 16px;
            padding: 25px;
            margin-bottom: 20px;
            border: 1px solid var(--border);
        }}
        
        .support-section h3 {{
            font-size: 1.2rem;
            margin-bottom: 15px;
            color: var(--text);
            display: flex;
            align-items: center;
            gap: 10px;
        }}
        
        .support-section p {{
            color: var(--text-light);
            line-height: 1.6;
            margin-bottom: 15px;
        }}
        
        .support-contact {{
            display: flex;
            align-items: center;
            gap: 15px;
            padding: 15px;
            background: rgba(255, 255, 255, 0.02);
            border-radius: 12px;
            border: 1px solid var(--border);
            margin-top: 15px;
        }}
        
        .support-contact i {{
            font-size: 1.5rem;
            color: var(--accent);
        }}
        
        .support-contact a {{
            color: var(--accent);
            text-decoration: none;
            font-weight: 500;
        }}
        
        .support-contact a:hover {{
            text-decoration: underline;
        }}
        
        /* Утилитарные классы */
        .hidden {{
            display: none !important;
        }}
        
        .text-center {{
            text-align: center;
        }}
        
        .mt-2 {{ margin-top: 8px; }}
        .mt-3 {{ margin-top: 12px; }}
        .mt-4 {{ margin-top: 16px; }}
        .mb-2 {{ margin-bottom: 8px; }}
        .mb-3 {{ margin-bottom: 12px; }}
        .mb-4 {{ margin-bottom: 16px; }}
        
        .flex {{
            display: flex;
        }}
        
        .flex-col {{
            flex-direction: column;
        }}
        
        .items-center {{
            align-items: center;
        }}
        
        .justify-between {{
            justify-content: space-between;
        }}
        
        .gap-2 {{ gap: 8px; }}
        .gap-3 {{ gap: 12px; }}
        .gap-4 {{ gap: 16px; }}
        
        .w-full {{
            width: 100%;
        }}
    </style>
</head>
<body>
    <div class="app-container">
        <!-- Сайдбар AURA -->
        <div class="sidebar" id="sidebar">
            <div class="sidebar-header">
                <button class="menu-toggle" onclick="toggleSidebar()">
                    <i class="fas fa-bars"></i>
                </button>
                <div class="logo-placeholder">
                    <i class="fas fa-bolt"></i>
                </div>
                <h1 class="app-title">AURA</h1>
            </div>
            
            <!-- ПОИСК AURA -->
            <div class="search-container">
                <div class="search-box">
                    <i class="fas fa-search search-icon"></i>
                    <input type="text" class="search-input" placeholder="Поиск..." id="search-input">
                </div>
            </div>
            
            <!-- Навигационные категории AURA -->
            <div class="nav-categories">
                <div class="nav-category active" onclick="showNavCategory('all')">все</div>
                <div class="nav-category" onclick="showNavCategory('personal')">личные</div>
                <div class="nav-category" onclick="showNavCategory('channels')">каналы</div>
                <div class="nav-category" onclick="showNavCategory('useful')">полезное</div>
                <div class="nav-category" onclick="showNavCategory('favorites')">избранное</div>
            </div>
            
            <!-- Информация о пользователе AURA -->
            <div class="user-info">
                <div class="avatar" id="user-avatar" onclick="openUserProfile('{username}')"></div>
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
            
            <!-- Основная навигация AURA -->
            <div class="nav" id="nav-content">
                <!-- Содержимое навигации будет загружено динамически -->
                <div class="empty-state">
                    <i class="fas fa-comments"></i>
                    <h3>Начните общение</h3>
                    <p>Выберите чат или канал для начала общения</p>
                </div>
            </div>
            
            <!-- Кнопка выхода AURA -->
            <button class="logout-btn" onclick="location.href='/logout'">
                <i class="fas fa-sign-out-alt"></i> Выйти из аккаунта
            </button>
        </div>
        
        <!-- Область чата AURA -->
        <div class="chat-area" id="chat-area">
            <div class="chat-header">
                <button class="back-btn" onclick="goBack()">
                    <i class="fas fa-arrow-left"></i>
                </button>
                <div id="chat-header-content">
                    <!-- Заголовок чата будет загружен динамически -->
                </div>
                <div class="channel-actions" id="channel-actions" style="display: none;">
                    <button class="channel-btn" onclick="openChannelSettingsModal()">
                        <i class="fas fa-cog"></i>
                    </button>
                </div>
            </div>
            
            <!-- Фильтр категорий для избранного -->
            <div class="categories-filter" id="categories-filter" style="display: none;">
                <button class="category-filter-btn active" onclick="filterFavorites('all')">Все</button>
                <!-- Категории будут добавлены динамически -->
            </div>
            
            <!-- Основное содержимое -->
            <div class="messages" id="messages">
                <!-- Для избранного показываем сетку заметок -->
                <div id="favorites-grid" class="favorites-grid"></div>
                
                <!-- Для поддержки -->
                <div id="support-content" class="support-content" style="display: none;"></div>
                
                <!-- Для чата показываем сообщения -->
                <div id="chat-messages" class="message-container" style="display: none;"></div>
            </div>
            
            <!-- Поле ввода AURA -->
            <div class="input-area" id="input-area" style="display: none;">
                <div class="input-row">
                    <button class="attachment-btn" onclick="document.getElementById('file-input').click()" title="Прикрепить файл">
                        <i class="fas fa-paperclip"></i>
                    </button>
                    <button class="emoji-btn" onclick="toggleEmojiPicker()" title="Эмодзи">
                        <i class="far fa-smile"></i>
                    </button>
                    <input type="file" id="file-input" accept="image/*,video/*,text/*,.pdf,.doc,.docx" style="display:none" onchange="handleFileSelect(this)">
                    <textarea class="msg-input" id="msg-input" placeholder="Написать сообщение..." rows="1" onkeydown="handleKeydown(event)"></textarea>
                    <button class="send-btn" onclick="sendMessage()" title="Отправить">
                        <i class="fas fa-paper-plane"></i>
                    </button>
                </div>
                <div id="file-preview"></div>
                
                <!-- Блок эмодзи -->
                <div class="emoji-container" id="emoji-container">
                    <div class="emoji-picker">
                        <div class="emoji-header">
                            <div class="emoji-title">Эмодзи</div>
                        </div>
                        <input type="text" class="emoji-search" placeholder="Поиск эмодзи..." id="emoji-search" oninput="searchEmojis()">
                        <div class="emoji-grid" id="emoji-grid">
                            <!-- Эмодзи будут добавлены динамически -->
                        </div>
                    </div>
                </div>
            </div>
        </div>
    </div>

    <!-- Модальные окна AURA -->
    
    <!-- Тема -->
    <div class="modal" id="theme-modal">
        <div class="modal-content">
            <h3>Выбор темы</h3>
            <div class="form-group">
                <div class="theme-buttons">
                    <button class="theme-btn" onclick="setTheme('light')">🌞 Светлая</button>
                    <button class="theme-btn" onclick="setTheme('dark')">🌙 Темная</button>
                    <button class="theme-btn" onclick="setTheme('auto')">⚙️ Авто</button>
                </div>
            </div>
            <div style="display: flex; gap: 10px; margin-top: 20px;">
                <button class="btn btn-secondary" onclick="closeThemeModal()">Закрыть</button>
            </div>
        </div>
    </div>
    
    <!-- Аватар -->
    <div class="modal" id="avatar-modal">
        <div class="modal-content">
            <h3>Смена аватарки</h3>
            <div class="avatar-upload">
                <div class="avatar-preview" id="avatar-preview" onclick="document.getElementById('avatar-input').click()"></div>
                <input type="file" id="avatar-input" accept="image/*" style="display:none" onchange="previewAvatar(this)">
                <div style="display: flex; gap: 10px; justify-content: center; margin-top: 20px;">
                    <button class="btn btn-primary" onclick="uploadAvatar()">Загрузить</button>
                    <button class="btn btn-secondary" onclick="removeAvatar()">Удалить</button>
                </div>
            </div>
            <div style="display: flex; gap: 10px; margin-top: 20px;">
                <button class="btn btn-secondary" onclick="closeAvatarModal()">Закрыть</button>
            </div>
        </div>
    </div>
    
    <!-- Профиль пользователя -->
    <div class="profile-modal-overlay" id="profile-modal">
        <div class="profile-modal-container">
            <button class="profile-close-btn" onclick="closeProfileModal()">
                <i class="fas fa-times"></i>
            </button>
            
            <div class="profile-modal-header">
                <div class="profile-avatar-large" id="profile-avatar-large"></div>
                <div class="profile-username" id="profile-username"></div>
                <div class="profile-status">
                    <div class="status-dot-large"></div>
                    <span id="profile-status-text">Online</span>
                </div>
            </div>
            
            <div class="profile-description">
                <div class="profile-description-label">О себе</div>
                <div class="profile-description-text" id="profile-description-text">
                    Пользователь еще не добавил информацию о себе
                </div>
            </div>
        </div>
    </div>
    
    <!-- Создание канала в стиле AURA -->
    <div class="glass-modal-overlay" id="create-channel-glass-modal">
        <div class="glass-modal-container">
            <button class="glass-close-btn" onclick="closeCreateChannelGlassModal()">
                <i class="fas fa-times"></i>
            </button>
            
            <div class="glass-modal-header">
                <div class="glass-modal-icon">
                    <i class="fas fa-hashtag"></i>
                </div>
                <h2 class="glass-modal-title">Создать новый канал</h2>
                <p class="glass-modal-subtitle">Создайте пространство для общения и совместной работы</p>
            </div>
            
            <div class="glass-form-group">
                <label class="glass-form-label">
                    <i class="fas fa-hashtag"></i>
                    Идентификатор канала
                </label>
                <input type="text" class="glass-form-input" id="glass-channel-name" 
                       placeholder="Например: team_chat, projects, news">
            </div>
            
            <div class="glass-form-group">
                <label class="glass-form-label">
                    <i class="fas fa-font"></i>
                    Отображаемое название
                </label>
                <input type="text" class="glass-form-input" id="glass-channel-display-name" 
                       placeholder="Например: Командный чат, Проекты, Новости">
            </div>
            
            <div class="glass-form-group">
                <label class="glass-form-label">
                    <i class="fas fa-align-left"></i>
                    Описание (необязательно)
                </label>
                <textarea class="glass-form-input glass-form-textarea" id="glass-channel-description" 
                          placeholder="Расскажите о назначении канала..."></textarea>
            </div>
            
            <div class="glass-form-group">
                <label class="glass-form-checkbox">
                    <input type="checkbox" id="glass-channel-private">
                    <span class="glass-form-checkbox-text">Приватный канал (только по приглашению)</span>
                </label>
            </div>
            
            <div class="glass-modal-buttons">
                <button class="glass-btn glass-btn-secondary" onclick="closeCreateChannelGlassModal()">
                    <i class="fas fa-times"></i>
                    Отмена
                </button>
                <button class="glass-btn glass-btn-primary" onclick="createChannelGlass()" id="create-channel-glass-btn">
                    <i class="fas fa-plus"></i>
                    Создать канал
                </button>
            </div>
        </div>
    </div>
    
    <!-- Добавление в избранное -->
    <div class="modal" id="add-favorite-modal">
        <div class="modal-content">
            <h3>Добавить заметку</h3>
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
    
    <!-- Настройки канала -->
    <div class="modal" id="channel-settings-modal">
        <div class="modal-content">
            <h3>Настройки канала</h3>
            <div class="form-group">
                <label class="form-label">Название канала</label>
                <div style="display: flex; gap: 10px;">
                    <input type="text" class="form-control" id="channel-edit-name" placeholder="Название канала" style="flex: 1;">
                    <button class="btn btn-primary" onclick="renameChannelFromModal()">
                        <i class="fas fa-edit"></i>
                    </button>
                </div>
            </div>
            
            <div class="form-group">
                <label class="form-label">Описание канала</label>
                <textarea class="form-control" id="channel-edit-description" placeholder="Добавьте описание канала..." rows="3"></textarea>
                <button class="btn btn-primary" onclick="updateChannelDescription()" style="margin-top: 10px;">
                    <i class="fas fa-save"></i> Сохранить описание
                </button>
            </div>
            
            <div class="form-group">
                <h4 style="margin-bottom: 15px;">Участники канала</h4>
                <div class="member-list" id="channel-members-list">
                    <!-- Участники будут загружены динамически -->
                </div>
                <button class="btn btn-primary" onclick="openAddUserModalFromSettings()" style="margin-top: 10px;">
                    <i class="fas fa-user-plus"></i> Добавить пользователя
                </button>
            </div>
            
            <div style="display: flex; gap: 10px; margin-top: 20px;">
                <button class="btn btn-secondary" onclick="closeChannelSettingsModal()">Закрыть</button>
            </div>
        </div>
    </div>
    
    <!-- Добавление пользователя в канал -->
    <div class="modal" id="add-user-modal">
        <div class="modal-content">
            <h3>Добавить пользователя в канал</h3>
            <div class="form-group">
                <label class="form-label">Пользователь</label>
                <select class="form-control" id="user-select">
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

    <script src="https://cdnjs.cloudflare.com/ajax/libs/socket.io/4.7.2/socket.io.js"></script>
    <script>
        const socket = io();
        const user = "{username}";
        let room = "favorites";
        let roomType = "favorites";
        let currentChannel = "";
        let currentCategory = "all";
        let isMobile = window.innerWidth <= 768;
        let currentNavCategory = "all";
        let emojiData = [
            "😀", "😁", "😂", "🤣", "😃", "😄", "😅", "😆", "😉", "😊", "😋", "😎", "😍", "😘", "😗", "😙", "😚", "🙂", "🤗", "🤔",
            "👋", "🤚", "🖐️", "✋", "🖖", "👌", "🤌", "🤏", "✌️", "🤞", "🤟", "🤘", "🤙", "👈", "👉", "👆", "🖕", "👇", "☝️", "👍",
            "🐶", "🐱", "🐭", "🐹", "🐰", "🦊", "🐻", "🐼", "🐨", "🐯", "🦁", "🐮", "🐷", "🐸", "🐵", "🙈", "🙉", "🙊", "🐔", "🐧",
            "🍏", "🍎", "🍐", "🍊", "🍋", "🍌", "🍉", "🍇", "🍓", "🫐", "🍈", "🍒", "🍑", "🥭", "🍍", "🥥", "🥝", "🍅", "🍆", "🥑",
            "⌚", "📱", "📲", "💻", "⌨️", "🖥️", "🖨️", "🖱️", "🖲️", "🕹️", "🗜️", "💽", "💾", "💿", "📀", "📼", "📷", "📸", "📹", "🎥"
        ];

        // Определение мобильного устройства
        function checkMobile() {{
            isMobile = window.innerWidth <= 768;
            if (!isMobile) {{
                document.getElementById('sidebar').classList.remove('hidden');
                document.getElementById('chat-area').classList.add('active');
            }}
        }}

        // Переключение сайдбара
        function toggleSidebar() {{
            const sidebar = document.getElementById('sidebar');
            sidebar.classList.toggle('hidden');
        }}

        // Возврат к списку
        function goBack() {{
            if (isMobile) {{
                document.getElementById('sidebar').classList.remove('hidden');
                document.getElementById('chat-area').classList.remove('active');
            }}
        }}

        // Показать категорию навигации
        function showNavCategory(category) {{
            currentNavCategory = category;
            
            // Обновляем активные кнопки
            document.querySelectorAll('.nav-category').forEach(btn => {{
                btn.classList.remove('active');
            }});
            event?.currentTarget.classList.add('active');
            
            // Загружаем соответствующее содержимое
            loadNavContent(category);
        }}

        // Загрузка содержимого навигации
        function loadNavContent(category) {{
            const navContent = document.getElementById('nav-content');
            
            switch(category) {{
                case 'all':
                    loadAllContent();
                    break;
                case 'personal':
                    loadPersonalChats();
                    break;
                case 'channels':
                    loadUserChannels();
                    break;
                case 'useful':
                    loadSupportContent();
                    break;
                case 'favorites':
                    loadFavoritesNav();
                    break;
            }}
        }}

        // Загрузка всего содержимого
        function loadAllContent() {{
            const navContent = document.getElementById('nav-content');
            navContent.innerHTML = `
                <div class="nav-title">
                    <span>Все заметки</span>
                    <button class="add-btn" onclick="openAddFavoriteModal()">
                        <i class="fas fa-plus"></i>
                    </button>
                </div>
                <div id="all-notes-list">
                    <!-- Заметки будут загружены динамически -->
                </div>
                
                <div class="nav-title">
                    <span>Поддержка</span>
                </div>
                <div class="nav-item" onclick="openSupport()">
                    <i class="fas fa-headset"></i>
                    <span>Центр поддержки</span>
                </div>
                
                <div class="nav-title">
                    <span>Каналы</span>
                    <button class="add-btn" onclick="openCreateChannelGlassModal()">
                        <i class="fas fa-plus"></i>
                    </button>
                </div>
                <div id="all-channels-list">
                    <!-- Каналы будут загружены динамически -->
                </div>
                
                <div class="nav-title">
                    <span>Личные чаты</span>
                </div>
                <div id="all-chats-list">
                    <!-- Чаты будут загружены динамически -->
                </div>
            `;
            
            // Загружаем данные
            loadFavoritesNav();
            loadAllChannels();
            loadAllChats();
        }}

        // Инициализация при загрузке
        window.onload = function() {{
            checkMobile();
            loadUserAvatar();
            loadNavContent('all');
            initEmojis();
            
            // На мобильных устройствах показываем только сайдбар
            if (isMobile) {{
                document.getElementById('chat-area').classList.remove('active');
            }} else {{
                openFavorites();
            }}
            
            window.addEventListener('resize', checkMobile);
            
            // Скрываем эмодзи при клике вне блока
            document.addEventListener('click', function(event) {{
                const emojiContainer = document.getElementById('emoji-container');
                const emojiBtn = document.querySelector('.emoji-btn');
                
                if (emojiContainer.style.display === 'block' && 
                    !emojiContainer.contains(event.target) && 
                    !emojiBtn.contains(event.target)) {{
                    emojiContainer.style.display = 'none';
                    emojiBtn.classList.remove('active');
                }}
            }});
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

        // Загрузка всех каналов
        function loadAllChannels() {{
            fetch('/user_channels')
                .then(r => r.json())
                .then(data => {{
                    if (data.success) {{
                        const container = document.getElementById('all-channels-list');
                        if (container) {{
                            container.innerHTML = '';
                            data.channels.forEach(channel => {{
                                const el = document.createElement('div');
                                el.className = 'nav-item';
                                el.innerHTML = `
                                    <div class="channel-item">
                                        <div class="channel-info">
                                            <div class="channel-avatar" style="background-color: #667eea;">
                                                ${{channel.avatar_path ? '' : (channel.display_name || channel.name).slice(0, 2).toUpperCase()}}
                                            </div>
                                            <div class="channel-name">${{channel.display_name || channel.name}}</div>
                                        </div>
                                        <div class="channel-stats">
                                            <span class="subscriber-count">
                                                <i class="fas fa-user"></i>
                                                ${{channel.subscriber_count || 0}}
                                            </span>
                                            <button class="join-btn" onclick="joinChannel('${{channel.name}}')">
                                                вступить
                                            </button>
                                        </div>
                                    </div>
                                `;
                                
                                // Загружаем аватарку если есть
                                if (channel.avatar_path) {{
                                    const avatar = el.querySelector('.channel-avatar');
                                    avatar.style.backgroundImage = `url(${{channel.avatar_path}})`;
                                    avatar.textContent = '';
                                }}
                                
                                container.appendChild(el);
                            }});
                        }}
                    }}
                }});
        }}

        // Загрузка всех чатов
        function loadAllChats() {{
            fetch('/personal_chats')
                .then(r => r.json())
                .then(data => {{
                    if (data.success) {{
                        const container = document.getElementById('all-chats-list');
                        if (container) {{
                            container.innerHTML = '';
                            data.chats.forEach(chatUser => {{
                                const el = document.createElement('div');
                                el.className = 'chat-item';
                                el.onclick = () => openRoom(
                                    'private_' + [user, chatUser].sort().join('_'),
                                    'private',
                                    chatUser
                                );
                                
                                // Получаем информацию о пользователе
                                fetch('/user_info/' + chatUser)
                                    .then(r => r.json())
                                    .then(userInfo => {{
                                        if (userInfo.success) {{
                                            el.innerHTML = `
                                                <div class="chat-avatar" style="background-color: ${{userInfo.avatar_color}};">
                                                    ${{userInfo.avatar_path ? '' : chatUser.slice(0, 2).toUpperCase()}}
                                                </div>
                                                <div class="chat-info">
                                                    <div class="chat-name">${{chatUser}}</div>
                                                    <div class="chat-last-message">Начните общение</div>
                                                </div>
                                            `;
                                            
                                            if (userInfo.avatar_path) {{
                                                const avatar = el.querySelector('.chat-avatar');
                                                avatar.style.backgroundImage = `url(${{userInfo.avatar_path}})`;
                                                avatar.textContent = '';
                                            }}
                                        }}
                                    }});
                                
                                container.appendChild(el);
                            }});
                        }}
                    }}
                }});
        }}

        // Загрузка избранного в навигацию
        function loadFavoritesNav() {{
            fetch('/get_favorites')
                .then(r => r.json())
                .then(data => {{
                    if (data.success) {{
                        const container = document.getElementById('all-notes-list');
                        if (container) {{
                            if (data.favorites.length === 0) {{
                                container.innerHTML = '<div class="nav-item">Нет заметок</div>';
                            }} else {{
                                container.innerHTML = '';
                                data.favorites.slice(0, 5).forEach(favorite => {{
                                    const el = document.createElement('div');
                                    el.className = 'nav-item';
                                    el.onclick = () => openFavorites();
                                    el.innerHTML = `
                                        <i class="fas fa-star"></i>
                                        <span>${{favorite.content ? favorite.content.substring(0, 30) : 'Файл'}}${{favorite.content && favorite.content.length > 30 ? '...' : ''}}</span>
                                    `;
                                    container.appendChild(el);
                                }});
                                
                                if (data.favorites.length > 5) {{
                                    const moreEl = document.createElement('div');
                                    moreEl.className = 'nav-item';
                                    moreEl.onclick = () => openFavorites();
                                    moreEl.innerHTML = `
                                        <i class="fas fa-ellipsis-h"></i>
                                        <span>Еще ${{data.favorites.length - 5}} заметок</span>
                                    `;
                                    container.appendChild(moreEl);
                                }}
                            }}
                        }}
                    }}
                }});
        }}

        // Загрузка поддержки
        function loadSupportContent() {{
            const navContent = document.getElementById('nav-content');
            navContent.innerHTML = `
                <div class="nav-title">
                    <span>Поддержка</span>
                </div>
                <div class="nav-item" onclick="openSupport()">
                    <i class="fas fa-headset"></i>
                    <span>Центр поддержки</span>
                </div>
                <div class="nav-item" onclick="openFAQ()">
                    <i class="fas fa-question-circle"></i>
                    <span>Частые вопросы</span>
                </div>
                <div class="nav-item" onclick="openContact()">
                    <i class="fas fa-envelope"></i>
                    <span>Связаться с нами</span>
                </div>
            `;
        }}

        // Открытие поддержки
        function openSupport() {{
            room = "support";
            roomType = "support";
            
            document.getElementById('chat-title').textContent = 'Поддержка';
            document.getElementById('categories-filter').style.display = 'none';
            document.getElementById('favorites-grid').style.display = 'none';
            document.getElementById('chat-messages').style.display = 'none';
            document.getElementById('input-area').style.display = 'none';
            document.getElementById('channel-actions').style.display = 'none';
            document.getElementById('support-content').style.display = 'block';
            
            const chatHeader = document.getElementById('chat-header-content');
            chatHeader.innerHTML = `
                <div class="chat-header-avatar" style="background: linear-gradient(135deg, var(--primary), var(--secondary));">
                    <i class="fas fa-headset"></i>
                </div>
                <div class="chat-header-info">
                    <div class="chat-title">Поддержка</div>
                    <div class="chat-subtitle">Мы всегда готовы помочь</div>
                </div>
            `;
            
            const supportContent = document.getElementById('support-content');
            supportContent.innerHTML = `
                <div class="support-section">
                    <h3><i class="fas fa-question-circle"></i> Частые вопросы</h3>
                    <p>Здесь вы найдете ответы на самые популярные вопросы о работе AURA Messenger.</p>
                    <button class="btn btn-primary" onclick="openFAQ()" style="width: 100%; margin-top: 10px;">
                        <i class="fas fa-list"></i> Открыть FAQ
                    </button>
                </div>
                
                <div class="support-section">
                    <h3><i class="fas fa-bug"></i> Сообщить о проблеме</h3>
                    <p>Если вы столкнулись с проблемой, пожалуйста, опишите ее как можно подробнее.</p>
                    <button class="btn btn-primary" onclick="reportProblem()" style="width: 100%; margin-top: 10px;">
                        <i class="fas fa-exclamation-triangle"></i> Сообщить о проблеме
                    </button>
                </div>
                
                <div class="support-contact">
                    <i class="fas fa-envelope"></i>
                    <div>
                        <strong>Электронная почта</strong>
                        <div><a href="mailto:support@aura-messenger.com">support@aura-messenger.com</a></div>
                    </div>
                </div>
            `;
            
            if (isMobile) {{
                document.getElementById('sidebar').classList.add('hidden');
                document.getElementById('chat-area').classList.add('active');
            }}
        }}

        // Открытие избранного (Все заметки)
        function openFavorites() {{
            room = "favorites";
            roomType = "favorites";
            
            document.getElementById('chat-title').textContent = 'Все заметки';
            document.getElementById('categories-filter').style.display = 'flex';
            document.getElementById('favorites-grid').style.display = 'grid';
            document.getElementById('support-content').style.display = 'none';
            document.getElementById('chat-messages').style.display = 'none';
            document.getElementById('input-area').style.display = 'none';
            document.getElementById('channel-actions').style.display = 'none';
            
            const chatHeader = document.getElementById('chat-header-content');
            chatHeader.innerHTML = `
                <div class="chat-header-avatar" style="background: linear-gradient(135deg, var(--primary), var(--secondary));">
                    <i class="fas fa-star"></i>
                </div>
                <div class="chat-header-info">
                    <div class="chat-title">Все заметки</div>
                    <div class="chat-subtitle">Ваши сохраненные материалы</div>
                </div>
            `;
            
            if (isMobile) {{
                document.getElementById('sidebar').classList.add('hidden');
                document.getElementById('chat-area').classList.add('active');
            }}
            
            loadFavoritesCategories();
            loadFavorites(currentCategory);
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
                        
                        if (data.favorites.length === 0) {{
                            grid.innerHTML = `
                                <div class="empty-state" style="grid-column: 1 / -1;">
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
                            <a href="${{favorite.file_path}}" target="_blank" style="font-size: 0.8rem; color: var(--accent);">Скачать</a>
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

        // Фильтрация избранного
        function filterFavorites(category) {{
            currentCategory = category;
            
            document.querySelectorAll('.category-filter-btn').forEach(btn => {{
                btn.classList.remove('active');
            }});
            event?.currentTarget.classList.add('active');
            
            loadFavorites(category === 'all' ? null : category);
        }}

        // Открытие профиля пользователя
        function openUserProfile(username) {{
            fetch('/user_info/' + username)
                .then(r => r.json())
                .then(userInfo => {{
                    if (userInfo.success) {{
                        const profileModal = document.getElementById('profile-modal');
                        const profileAvatar = document.getElementById('profile-avatar-large');
                        const profileName = document.getElementById('profile-username');
                        const statusText = document.getElementById('profile-status-text');
                        const descriptionText = document.getElementById('profile-description-text');
                        
                        if (userInfo.avatar_path) {{
                            profileAvatar.style.backgroundImage = `url(${{userInfo.avatar_path}})`;
                            profileAvatar.textContent = '';
                        }} else {{
                            profileAvatar.style.backgroundImage = 'none';
                            profileAvatar.style.backgroundColor = userInfo.avatar_color;
                            profileAvatar.textContent = username.slice(0, 2).toUpperCase();
                        }}
                        
                        profileName.textContent = username;
                        statusText.textContent = userInfo.online ? 'Online' : 'Offline';
                        descriptionText.textContent = userInfo.profile_description || 'Пользователь еще не добавил информацию о себе';
                        
                        profileModal.style.display = 'flex';
                        document.body.style.overflow = 'hidden';
                    }}
                }});
        }}

        function closeProfileModal() {{
            document.getElementById('profile-modal').style.display = 'none';
            document.body.style.overflow = 'auto';
        }}

        // Функции для аватара
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

        // Функции для темы
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

        // Функции для каналов
        function openCreateChannelGlassModal() {{
            document.getElementById('create-channel-glass-modal').style.display = 'flex';
            document.body.style.overflow = 'hidden';
        }}

        function closeCreateChannelGlassModal() {{
            document.getElementById('create-channel-glass-modal').style.display = 'none';
            document.body.style.overflow = 'auto';
        }}

        function createChannelGlass() {{
            const name = document.getElementById('glass-channel-name').value.trim();
            const displayName = document.getElementById('glass-channel-display-name').value.trim();
            const description = document.getElementById('glass-channel-description').value.trim();
            const isPrivate = document.getElementById('glass-channel-private').checked;
            
            if (!name) {{
                alert('Введите идентификатор канала');
                document.getElementById('glass-channel-name').focus();
                return;
            }}
            
            if (!/^[a-zA-Z0-9_]+$/.test(name)) {{
                alert('Идентификатор канала может содержать только латинские буквы, цифры и символ подчеркивания');
                document.getElementById('glass-channel-name').focus();
                return;
            }}
            
            const btn = document.getElementById('create-channel-glass-btn');
            const originalText = btn.innerHTML;
            btn.innerHTML = '<i class="fas fa-spinner fa-spin"></i> Создание...';
            btn.disabled = true;
            
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
                    closeCreateChannelGlassModal();
                    loadAllChannels();
                    alert('Канал создан успешно!');
                    
                    setTimeout(() => {{
                        const channelName = data.channel_name;
                        openRoom('channel_' + channelName, 'channel', data.display_name);
                    }}, 1000);
                }} else {{
                    alert(data.error || 'Ошибка при создании канала');
                    btn.innerHTML = originalText;
                    btn.disabled = false;
                }}
            }});
        }}

        function joinChannel(channelName) {{
            fetch('/add_user_to_channel', {{
                method: 'POST',
                headers: {{ 'Content-Type': 'application/json' }},
                body: JSON.stringify({{
                    channel_name: channelName,
                    username: user
                }})
            }})
            .then(r => r.json())
            .then(data => {{
                if (data.success) {{
                    alert('Вы присоединились к каналу!');
                    loadAllChannels();
                }} else {{
                    alert(data.message || 'Ошибка при присоединении к каналу');
                }}
            }});
        }}

        // Открытие комнаты
        function openRoom(r, t, title) {{
            room = r;
            roomType = t;
            currentChannel = t === 'channel' ? r.replace('channel_', '') : '';
            
            document.getElementById('categories-filter').style.display = 'none';
            document.getElementById('favorites-grid').style.display = 'none';
            document.getElementById('support-content').style.display = 'none';
            document.getElementById('chat-messages').style.display = 'block';
            document.getElementById('input-area').style.display = 'flex';
            
            const chatHeader = document.getElementById('chat-header-content');
            
            if (t === 'channel') {{
                chatHeader.innerHTML = `
                    <div class="chat-header-avatar" id="channel-header-avatar"></div>
                    <div class="chat-header-info">
                        <div class="chat-title" id="chat-title">${{title}}</div>
                        <div class="chat-subtitle" id="channel-description"></div>
                    </div>
                `;
                
                document.getElementById('channel-actions').style.display = 'flex';
                loadChannelHeaderInfo();
            }} else if (t === 'private') {{
                chatHeader.innerHTML = `
                    <div class="chat-header-avatar" id="private-chat-avatar"></div>
                    <div class="chat-header-info">
                        <div class="chat-title" id="chat-title">${{title}}</div>
                        <div class="chat-subtitle" id="channel-description">Online</div>
                    </div>
                `;
                
                fetch('/user_info/' + title)
                    .then(r => r.json())
                    .then(userInfo => {{
                        if (userInfo.success) {{
                            const avatar = document.getElementById('private-chat-avatar');
                            if (userInfo.avatar_path) {{
                                avatar.style.backgroundImage = `url(${{userInfo.avatar_path}})`;
                                avatar.textContent = '';
                            }} else {{
                                avatar.style.backgroundColor = userInfo.avatar_color;
                                avatar.textContent = title.slice(0, 2).toUpperCase();
                            }}
                            
                            const status = document.getElementById('channel-description');
                            status.textContent = userInfo.online ? 'Online' : 'Offline';
                        }}
                    }});
                
                document.getElementById('channel-actions').style.display = 'none';
            }}
            
            if (isMobile) {{
                document.getElementById('sidebar').classList.add('hidden');
                document.getElementById('chat-area').classList.add('active');
            }}
            
            const chatMessages = document.getElementById('chat-messages');
            chatMessages.innerHTML = '<div class="empty-state"><i class="fas fa-comments"></i><h3>Начните общение</h3><p>Отправьте сообщение, чтобы начать чат</p></div>';
            
            loadMessages(r);
            socket.emit('join', {{ room: r }});
        }}

        function loadChannelHeaderInfo() {{
            fetch(`/channel_info/${{encodeURIComponent(currentChannel)}}`)
                .then(r => r.json())
                .then(data => {{
                    if (data.success) {{
                        const channelInfo = data.data;
                        document.getElementById('channel-description').textContent = channelInfo.description || '';
                        
                        const channelAvatar = document.getElementById('channel-header-avatar');
                        if (channelAvatar) {{
                            if (channelInfo.avatar_path) {{
                                channelAvatar.style.backgroundImage = `url(${{channelInfo.avatar_path}})`;
                                channelAvatar.textContent = '';
                            }} else {{
                                channelAvatar.style.backgroundImage = 'none';
                                channelAvatar.style.backgroundColor = '#667eea';
                                channelAvatar.textContent = currentChannel.slice(0, 2).toUpperCase();
                            }}
                        }}
                    }}
                }});
        }}

        // Загрузка сообщений
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
                    }} else {{
                        messagesContainer.innerHTML = '<div class="empty-state"><i class="fas fa-comments"></i><h3>Начните общение</h3><p>Отправьте сообщение, чтобы начать чат</p></div>';
                    }}
                    
                    messagesContainer.scrollTop = messagesContainer.scrollHeight;
                }});
        }}

        // Добавление сообщения в чат
        function addMessageToChat(data, roomName = '') {{
            const messagesContainer = document.getElementById('chat-messages');
            
            const emptyChat = messagesContainer.querySelector('.empty-state');
            if (emptyChat) {{
                emptyChat.remove();
            }}
            
            const message = document.createElement('div');
            message.className = `message ${{data.user === user ? 'own' : 'other'}}`;
            
            const avatar = document.createElement('div');
            avatar.className = 'message-avatar';
            avatar.style.cursor = 'pointer';
            avatar.onclick = () => openUserProfile(data.user);
            
            fetch('/user_info/' + data.user)
                .then(r => r.json())
                .then(userInfo => {{
                    if (userInfo.success) {{
                        if (userInfo.avatar_path) {{
                            avatar.style.backgroundImage = `url(${{userInfo.avatar_path}})`;
                            avatar.textContent = '';
                        }} else {{
                            avatar.style.backgroundColor = userInfo.avatar_color || data.color || '#667eea';
                            avatar.textContent = data.user.slice(0, 2).toUpperCase();
                        }}
                    }} else {{
                        avatar.style.backgroundColor = data.color || '#667eea';
                        avatar.textContent = data.user.slice(0, 2).toUpperCase();
                    }}
                }});
            
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

        // Отправка сообщения
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
            input.style.height = 'auto';
            document.getElementById('file-preview').innerHTML = '';
            fileInput.value = '';
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
                            <div style="display: flex; align-items: center; gap: 10px; padding: 10px; background: rgba(255, 255, 255, 0.05); border-radius: 8px;">
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
                            <div style="display: flex; align-items: center; gap: 10px; padding: 10px; background: rgba(255, 255, 255, 0.05); border-radius: 8px;">
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
                            <div style="display: flex; align-items: center; gap: 10px; padding: 10px; background: rgba(255, 255, 255, 0.05); border-radius: 8px;">
                                <i class="fas fa-file" style="font-size: 2rem; color: var(--accent);"></i>
                                <div style="flex: 1;">
                                    <div style="font-weight: 500;">${{file.name}}</div>
                                    <div style="font-size: 0.8rem; color: var(--text-light);">${{(file.size / 1024).toFixed(1)}} KB</div>
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
                addMessageToChat(data, room);
            }}
        }});

        // Функции для избранного
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
                        preview.innerHTML = `<div style="padding: 10px; background: rgba(255, 255, 255, 0.05); border-radius: 8px;">
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
                    loadFavoritesNav();
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

        // Функции для эмодзи
        function initEmojis() {{
            const emojiGrid = document.getElementById('emoji-grid');
            emojiData.forEach(emoji => {{
                const emojiItem = document.createElement('div');
                emojiItem.className = 'emoji-item';
                emojiItem.textContent = emoji;
                emojiItem.onclick = () => insertEmoji(emoji);
                emojiGrid.appendChild(emojiItem);
            }});
        }}

        function toggleEmojiPicker() {{
            const emojiContainer = document.getElementById('emoji-container');
            const emojiBtn = document.querySelector('.emoji-btn');
            
            if (emojiContainer.style.display === 'block') {{
                emojiContainer.style.display = 'none';
                emojiBtn.classList.remove('active');
            }} else {{
                emojiContainer.style.display = 'block';
                emojiBtn.classList.add('active');
                document.getElementById('emoji-search').value = '';
                searchEmojis();
            }}
        }}

        function insertEmoji(emoji) {{
            const input = document.getElementById('msg-input');
            const start = input.selectionStart;
            const end = input.selectionEnd;
            
            input.value = input.value.substring(0, start) + emoji + input.value.substring(end);
            input.selectionStart = input.selectionEnd = start + emoji.length;
            input.focus();
            autoResizeTextarea();
        }}

        function searchEmojis() {{
            const searchTerm = document.getElementById('emoji-search').value.toLowerCase();
            const emojiItems = document.querySelectorAll('#emoji-grid .emoji-item');
            
            emojiItems.forEach(item => {{
                if (!searchTerm) {{
                    item.style.display = 'flex';
                }} else {{
                    item.style.display = 'none';
                }}
            }});
        }}

        // Настройки канала
        function openChannelSettingsModal() {{
            if (!currentChannel) return;
            
            document.getElementById('channel-settings-modal').style.display = 'flex';
            loadChannelInfo();
            loadChannelMembers();
        }}

        function closeChannelSettingsModal() {{
            document.getElementById('channel-settings-modal').style.display = 'none';
        }}

        function loadChannelInfo() {{
            fetch(`/channel_info/${{encodeURIComponent(currentChannel)}}`)
                .then(r => r.json())
                .then(data => {{
                    if (data.success) {{
                        const channelInfo = data.data;
                        document.getElementById('channel-edit-name').value = channelInfo.display_name;
                        document.getElementById('channel-edit-description').value = channelInfo.description || '';
                    }}
                }});
        }}

        function loadChannelMembers() {{
            fetch(`/channel_info/${{encodeURIComponent(currentChannel)}}`)
                .then(r => r.json())
                .then(data => {{
                    if (data.success) {{
                        const membersList = document.getElementById('channel-members-list');
                        membersList.innerHTML = '';
                        
                        data.data.members.forEach(member => {{
                            const memberItem = document.createElement('div');
                            memberItem.className = 'member-item';
                            memberItem.style.cssText = 'display: flex; align-items: center; justify-content: space-between; padding: 10px; border-bottom: 1px solid var(--border);';
                            
                            const isCurrentUser = member.username === user;
                            const isCreator = data.data.created_by === member.username;
                            const canManage = data.data.created_by === user && !isCurrentUser;
                            
                            memberItem.innerHTML = `
                                <div style="display: flex; align-items: center; gap: 10px;">
                                    <div style="width: 32px; height: 32px; border-radius: 50%; background-color: ${{member.color}}; display: flex; align-items: center; justify-content: center; color: white; font-weight: bold; font-size: 0.8rem;">
                                        ${{member.avatar ? '' : member.username.slice(0, 2).toUpperCase()}}
                                    </div>
                                    <div>
                                        <div>${{member.username}}</div>
                                        <div style="font-size: 0.8rem; color: var(--text-light);">
                                            ${{isCreator ? 'Создатель' : member.is_admin ? 'Админ' : 'Участник'}}
                                        </div>
                                    </div>
                                </div>
                                ${{canManage ? `
                                    <div style="display: flex; gap: 5px;">
                                        ${{!member.is_admin ? 
                                            `<button onclick="makeUserAdmin('${{member.username}}')" style="padding: 5px 10px; background: var(--accent); color: white; border: none; border-radius: 6px; cursor: pointer;">
                                                Админ
                                            </button>` : 
                                            `<button onclick="removeUserAdmin('${{member.username}}')" style="padding: 5px 10px; background: #6c757d; color: white; border: none; border-radius: 6px; cursor: pointer;">
                                                Убрать
                                            </button>`}}
                                        <button onclick="removeUserFromChannel('${{member.username}}')" style="padding: 5px 10px; background: #dc3545; color: white; border: none; border-radius: 6px; cursor: pointer;">
                                            Удалить
                                        </button>
                                    </div>
                                ` : ''}}
                            `;
                            
                            membersList.appendChild(memberItem);
                        }});
                    }}
                }});
        }}

        // Закрытие модальных окон
        document.addEventListener('click', function(event) {{
            const glassModal = document.getElementById('create-channel-glass-modal');
            if (event.target === glassModal) {{
                closeCreateChannelGlassModal();
            }}
            
            const profileModal = document.getElementById('profile-modal');
            if (event.target === profileModal) {{
                closeProfileModal();
            }}
        }});
        
        document.addEventListener('keydown', function(event) {{
            if (event.key === 'Escape') {{
                closeCreateChannelGlassModal();
                closeProfileModal();
                
                const emojiContainer = document.getElementById('emoji-container');
                const emojiBtn = document.querySelector('.emoji-btn');
                
                if (emojiContainer.style.display === 'block') {{
                    emojiContainer.style.display = 'none';
                    emojiBtn.classList.remove('active');
                }}
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
        return jsonify({'status': 'healthy', 'service': 'AURA Messenger'})

    @app.errorhandler(404)
    def not_found(e):
        return redirect('/')

    return app

app = create_app()
socketio = app.extensions['socketio']

if __name__ == '__main__':
    port = int(os.environ.get('PORT', 5000))
    socketio.run(app, host='0.0.0.0', port=port, debug=True, allow_unsafe_werkzeug=True)
