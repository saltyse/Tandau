# web_messenger.py - Tandau Messenger (единый файл)
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
                    theme TEXT DEFAULT 'light',
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

    # ИСПРАВЛЕННАЯ ФУНКЦИЯ СОЗДАНИЯ КАНАЛА
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
        theme = request.json.get('theme', 'light')
        if theme not in ['light', 'dark', 'auto']: 
            return jsonify({'success': False, 'error': 'Неверная тема'})
        with sqlite3.connect('messenger.db') as conn:
            c = conn.cursor()
            c.execute('UPDATE users SET theme = ? WHERE username = ?', (theme, session['username']))
            conn.commit()
        return jsonify({'success': True})

    # ИСПРАВЛЕННЫЙ МАРШРУТ СОЗДАНИЯ КАНАЛА
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
        
        # Генерируем HTML с мобильной адаптацией
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
        
        /* Основной контейнер для мобильной версии */
        .app-container {{
            display: flex;
            height: 100vh;
            position: relative;
        }}
        
        /* Сайдбар - виден по умолчанию на мобилке */
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
        
        /* Область чата - скрыта по умолчанию на мобилке */
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
            border: 2px solid var(--accent);
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
            cursor: pointer;
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
        
        .action-btn.admin {{
            background: #ffc107;
            color: #000;
            border-color: #ffc107;
        }}
        
        /* Стили области ввода сообщений - жидкое стекло */
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
        
        /* Кнопка эмодзи */
        .emoji-btn {{
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
            position: relative;
        }}
        
        .emoji-btn:hover {{
            background: rgba(255, 255, 255, 0.9);
            transform: translateY(-2px);
            box-shadow: 0 4px 12px rgba(0,0,0,0.1);
        }}
        
        [data-theme="dark"] .emoji-btn {{
            background: rgba(255, 255, 255, 0.1);
            border: 1px solid rgba(255, 255, 255, 0.2);
        }}
        
        [data-theme="dark"] .emoji-btn:hover {{
            background: rgba(255, 255, 255, 0.2);
        }}
        
        .emoji-btn.active {{
            background: rgba(102, 126, 234, 0.9);
            color: white;
            border-color: rgba(102, 126, 234, 0.6);
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
        
        /* Стили для блока эмодзи */
        .emoji-container {{
            display: none;
            position: absolute;
            bottom: 70px;
            left: 15px;
            z-index: 1001;
            animation: fadeInUp 0.2s ease-out;
        }}
        
        .emoji-picker {{
            background: rgba(255, 255, 255, 0.9);
            backdrop-filter: blur(20px);
            -webkit-backdrop-filter: blur(20px);
            border-radius: 20px;
            border: 1px solid rgba(255, 255, 255, 0.3);
            box-shadow: 
                0 15px 50px rgba(0, 0, 0, 0.15),
                inset 0 1px 0 rgba(255, 255, 255, 0.4);
            padding: 15px;
            width: 300px;
            max-height: 400px;
            overflow-y: auto;
            -webkit-overflow-scrolling: touch;
        }}
        
        [data-theme="dark"] .emoji-picker {{
            background: rgba(45, 45, 45, 0.9);
            border: 1px solid rgba(255, 255, 255, 0.15);
        }}
        
        .emoji-header {{
            display: flex;
            justify-content: space-between;
            align-items: center;
            margin-bottom: 12px;
            padding-bottom: 10px;
            border-bottom: 1px solid rgba(0, 0, 0, 0.1);
        }}
        
        [data-theme="dark"] .emoji-header {{
            border-bottom: 1px solid rgba(255, 255, 255, 0.1);
        }}
        
        .emoji-title {{
            font-weight: 600;
            font-size: 0.9rem;
            color: var(--text);
        }}
        
        .emoji-search {{
            width: 100%;
            padding: 8px 12px;
            border: 1px solid rgba(0, 0, 0, 0.1);
            border-radius: 10px;
            background: rgba(255, 255, 255, 0.7);
            margin-bottom: 10px;
            font-size: 0.9rem;
            color: var(--text);
        }}
        
        [data-theme="dark"] .emoji-search {{
            background: rgba(255, 255, 255, 0.1);
            border: 1px solid rgba(255, 255, 255, 0.2);
            color: white;
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
            background: rgba(102, 126, 234, 0.2);
            transform: scale(1.1);
        }}
        
        .emoji-categories {{
            display: flex;
            gap: 5px;
            margin-bottom: 10px;
            overflow-x: auto;
            padding-bottom: 5px;
            -webkit-overflow-scrolling: touch;
        }}
        
        .emoji-category-btn {{
            padding: 6px 12px;
            border: none;
            background: rgba(255, 255, 255, 0.5);
            border-radius: 12px;
            cursor: pointer;
            font-size: 0.8rem;
            white-space: nowrap;
            transition: all 0.2s ease;
        }}
        
        .emoji-category-btn:hover {{
            background: rgba(255, 255, 255, 0.7);
        }}
        
        .emoji-category-btn.active {{
            background: var(--accent);
            color: white;
        }}
        
        /* Большой блок эмодзи с кнопкой + */
        .emoji-plus-btn {{
            width: 36px;
            height: 36px;
            border-radius: 50%;
            background: rgba(255, 255, 255, 0.3);
            border: 1px solid rgba(255, 255, 255, 0.4);
            display: flex;
            align-items: center;
            justify-content: center;
            cursor: pointer;
            transition: all 0.2s ease;
            margin-left: auto;
        }}
        
        .emoji-plus-btn:hover {{
            background: rgba(255, 255, 255, 0.5);
            transform: scale(1.1);
        }}
        
        .emoji-full-container {{
            display: none;
            position: absolute;
            bottom: 70px;
            left: 15px;
            right: 15px;
            z-index: 1002;
            animation: fadeInUp 0.2s ease-out;
        }}
        
        .emoji-full-picker {{
            background: rgba(255, 255, 255, 0.95);
            backdrop-filter: blur(25px);
            -webkit-backdrop-filter: blur(25px);
            border-radius: 24px;
            border: 1px solid rgba(255, 255, 255, 0.4);
            box-shadow: 
                0 25px 70px rgba(0, 0, 0, 0.2),
                inset 0 1px 0 rgba(255, 255, 255, 0.5);
            padding: 20px;
            max-height: 500px;
            overflow-y: auto;
            -webkit-overflow-scrolling: touch;
        }}
        
        [data-theme="dark"] .emoji-full-picker {{
            background: rgba(30, 30, 40, 0.95);
            border: 1px solid rgba(255, 255, 255, 0.2);
        }}
        
        .emoji-full-grid {{
            display: grid;
            grid-template-columns: repeat(10, 1fr);
            gap: 10px;
        }}
        
        @media (max-width: 768px) {{
            .emoji-grid {{
                grid-template-columns: repeat(6, 1fr);
            }}
            
            .emoji-full-grid {{
                grid-template-columns: repeat(8, 1fr);
            }}
            
            .emoji-picker {{
                width: 280px;
                max-height: 350px;
            }}
            
            .emoji-full-picker {{
                max-height: 450px;
            }}
            
            .emoji-container {{
                left: 10px;
                right: 10px;
            }}
            
            .emoji-full-container {{
                left: 10px;
                right: 10px;
            }}
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
        
        /* Анимации */
        @keyframes fadeIn {{
            from {{ opacity: 0; transform: translateY(10px); }}
            to {{ opacity: 1; transform: translateY(0); }}
        }}
        
        @keyframes fadeInUp {{
            from {{ opacity: 0; transform: translateY(20px); }}
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
        
        /* Модальное окно настроек канала */
        .channel-settings-modal .modal-content {{
            max-width: 600px;
        }}
        
        .channel-avatar-section {{
            text-align: center;
            margin-bottom: 30px;
        }}
        
        .channel-avatar-preview {{
            width: 120px;
            height: 120px;
            border-radius: 50%;
            margin: 0 auto 15px;
            background: linear-gradient(135deg, #667eea, #764ba2);
            background-size: cover;
            background-position: center;
            cursor: pointer;
            border: 3px solid var(--accent);
        }}
        
        .channel-info-section {{
            margin-bottom: 30px;
        }}
        
        .channel-description {{
            margin-top: 15px;
        }}
        
        .channel-description textarea {{
            width: 100%;
            padding: 10px;
            border: 1px solid var(--border);
            border-radius: 8px;
            background: var(--bg);
            color: var(--text);
            font-size: 1rem;
            resize: vertical;
            min-height: 80px;
        }}
        
        .channel-description .btn {{
            margin-top: 10px;
        }}
        
        .channel-members-section {{
            margin-bottom: 30px;
        }}
        
        .member-actions-section {{
            display: flex;
            gap: 10px;
            margin-top: 5px;
        }}
        
        /* Медиа запросы для мобильных устройств */
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
            
            .channel-avatar-preview {{
                width: 100px;
                height: 100px;
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
        
        /* Стили для аватарок каналов в списке */
        .channel-avatar {{
            width: 30px;
            height: 30px;
            border-radius: 50%;
            background: var(--accent);
            color: white;
            display: flex;
            align-items: center;
            justify-content: center;
            font-weight: bold;
            font-size: 0.8rem;
            background-size: cover;
            background-position: center;
            flex-shrink: 0;
            margin-right: 10px;
        }}
        
        /* СТИЛИ ДЛЯ БЛОКА СОЗДАНИЯ КАНАЛА С ЖИДКИМ СТЕКЛОМ */
        .glass-modal-overlay {{
            display: none;
            position: fixed;
            top: 0;
            left: 0;
            width: 100%;
            height: 100%;
            background: rgba(0, 0, 0, 0.7);
            backdrop-filter: blur(12px);
            -webkit-backdrop-filter: blur(12px);
            z-index: 2000;
            animation: fadeIn 0.3s ease-out;
            align-items: center;
            justify-content: center;
            padding: 20px;
        }}
        
        .glass-modal-container {{
            background: rgba(255, 255, 255, 0.15);
            backdrop-filter: blur(20px);
            -webkit-backdrop-filter: blur(20px);
            border-radius: 28px;
            border: 1px solid rgba(255, 255, 255, 0.25);
            padding: 40px;
            width: 100%;
            max-width: 500px;
            max-height: 90vh;
            overflow-y: auto;
            -webkit-overflow-scrolling: touch;
            box-shadow: 
                0 25px 60px rgba(0, 0, 0, 0.25),
                inset 0 1px 0 rgba(255, 255, 255, 0.3);
            position: relative;
            animation: slideUp 0.4s cubic-bezier(0.4, 0, 0.2, 1);
        }}
        
        [data-theme="dark"] .glass-modal-container {{
            background: rgba(30, 30, 40, 0.25);
            border: 1px solid rgba(255, 255, 255, 0.15);
        }}
        
        .glass-modal-header {{
            text-align: center;
            margin-bottom: 35px;
            position: relative;
            padding-bottom: 25px;
        }}
        
        .glass-modal-header::after {{
            content: '';
            position: absolute;
            bottom: 0;
            left: 20%;
            right: 20%;
            height: 2px;
            background: linear-gradient(90deg, transparent, #667eea, #764ba2, transparent);
            border-radius: 2px;
        }}
        
        .glass-modal-icon {{
            width: 70px;
            height: 70px;
            margin: 0 auto 20px;
            background: linear-gradient(135deg, rgba(102, 126, 234, 0.2), rgba(118, 75, 162, 0.2));
            backdrop-filter: blur(10px);
            border-radius: 20px;
            display: flex;
            align-items: center;
            justify-content: center;
            border: 1px solid rgba(255, 255, 255, 0.3);
            box-shadow: 0 10px 30px rgba(0, 0, 0, 0.15);
        }}
        
        .glass-modal-icon i {{
            font-size: 32px;
            background: linear-gradient(135deg, #667eea, #764ba2);
            -webkit-background-clip: text;
            -webkit-text-fill-color: transparent;
            background-clip: text;
        }}
        
        .glass-modal-title {{
            font-size: 1.8rem;
            font-weight: 800;
            margin-bottom: 8px;
            background: linear-gradient(135deg, #667eea, #764ba2);
            -webkit-background-clip: text;
            -webkit-text-fill-color: transparent;
            background-clip: text;
            letter-spacing: -0.5px;
        }}
        
        .glass-modal-subtitle {{
            color: rgba(255, 255, 255, 0.85);
            font-size: 1rem;
            font-weight: 400;
        }}
        
        [data-theme="dark"] .glass-modal-subtitle {{
            color: rgba(255, 255, 255, 0.75);
        }}
        
        .glass-form-group {{
            margin-bottom: 25px;
        }}
        
        .glass-form-label {{
            display: block;
            margin-bottom: 10px;
            font-weight: 600;
            color: white;
            font-size: 0.95rem;
            display: flex;
            align-items: center;
            gap: 10px;
        }}
        
        [data-theme="dark"] .glass-form-label {{
            color: rgba(255, 255, 255, 0.9);
        }}
        
        .glass-form-label i {{
            font-size: 1.1rem;
            color: #667eea;
        }}
        
        .glass-form-input {{
            width: 100%;
            padding: 16px 20px;
            border: 1px solid rgba(255, 255, 255, 0.3);
            border-radius: 16px;
            background: rgba(255, 255, 255, 0.1);
            backdrop-filter: blur(10px);
            -webkit-backdrop-filter: blur(10px);
            color: white;
            font-size: 1rem;
            transition: all 0.3s ease;
            box-shadow: inset 0 2px 4px rgba(0, 0, 0, 0.1);
        }}
        
        .glass-form-input:focus {{
            outline: none;
            border-color: rgba(102, 126, 234, 0.6);
            background: rgba(255, 255, 255, 0.15);
            box-shadow: 
                inset 0 2px 4px rgba(0, 0, 0, 0.1),
                0 0 0 3px rgba(102, 126, 234, 0.2);
        }}
        
        .glass-form-input::placeholder {{
            color: rgba(255, 255, 255, 0.6);
        }}
        
        [data-theme="dark"] .glass-form-input {{
            border: 1px solid rgba(255, 255, 255, 0.2);
            background: rgba(255, 255, 255, 0.08);
            color: white;
        }}
        
        [data-theme="dark"] .glass-form-input:focus {{
            border-color: rgba(102, 126, 234, 0.5);
            background: rgba(255, 255, 255, 0.12);
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
            background: rgba(255, 255, 255, 0.05);
            border-radius: 14px;
            border: 1px solid rgba(255, 255, 255, 0.1);
            transition: all 0.3s ease;
        }}
        
        .glass-form-checkbox:hover {{
            background: rgba(255, 255, 255, 0.08);
            border-color: rgba(102, 126, 234, 0.3);
        }}
        
        .glass-form-checkbox input {{
            width: 20px;
            height: 20px;
            border-radius: 6px;
            border: 2px solid rgba(255, 255, 255, 0.4);
            background: rgba(255, 255, 255, 0.1);
            cursor: pointer;
            position: relative;
            appearance: none;
            -webkit-appearance: none;
        }}
        
        .glass-form-checkbox input:checked {{
            background: linear-gradient(135deg, #667eea, #764ba2);
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
            color: white;
            font-weight: 500;
        }}
        
        [data-theme="dark"] .glass-form-checkbox-text {{
            color: rgba(255, 255, 255, 0.9);
        }}
        
        .glass-modal-buttons {{
            display: flex;
            gap: 15px;
            margin-top: 35px;
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
            background: linear-gradient(135deg, #667eea, #764ba2);
            color: white;
            box-shadow: 0 8px 25px rgba(102, 126, 234, 0.3);
        }}
        
        .glass-btn-primary:hover {{
            transform: translateY(-3px);
            box-shadow: 0 12px 35px rgba(102, 126, 234, 0.4);
        }}
        
        .glass-btn-primary:active {{
            transform: translateY(-1px);
        }}
        
        .glass-btn-secondary {{
            background: rgba(255, 255, 255, 0.1);
            border: 1px solid rgba(255, 255, 255, 0.3);
            color: white;
        }}
        
        .glass-btn-secondary:hover {{
            background: rgba(255, 255, 255, 0.15);
            transform: translateY(-2px);
        }}
        
        .glass-close-btn {{
            position: absolute;
            top: 20px;
            right: 20px;
            background: rgba(255, 255, 255, 0.15);
            border: 1px solid rgba(255, 255, 255, 0.25);
            color: white;
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
            background: rgba(255, 255, 255, 0.25);
            transform: rotate(90deg);
        }}
        
        .glass-form-hint {{
            font-size: 0.85rem;
            color: rgba(255, 255, 255, 0.7);
            margin-top: 6px;
            margin-left: 34px;
            font-style: italic;
        }}
        
        .glass-channel-preview {{
            background: rgba(255, 255, 255, 0.08);
            border-radius: 16px;
            padding: 20px;
            margin-top: 10px;
            border: 1px dashed rgba(255, 255, 255, 0.2);
            text-align: center;
        }}
        
        .glass-channel-preview h4 {{
            color: white;
            margin-bottom: 15px;
            font-size: 1.1rem;
            font-weight: 600;
        }}
        
        .preview-channel-avatar {{
            width: 60px;
            height: 60px;
            border-radius: 50%;
            background: linear-gradient(135deg, #667eea, #764ba2);
            margin: 0 auto 15px;
            display: flex;
            align-items: center;
            justify-content: center;
            color: white;
            font-weight: bold;
            font-size: 1.2rem;
            border: 3px solid rgba(255, 255, 255, 0.3);
        }}
        
        .preview-channel-name {{
            color: white;
            font-size: 1.3rem;
            font-weight: 700;
            margin-bottom: 8px;
        }}
        
        .preview-channel-desc {{
            color: rgba(255, 255, 255, 0.8);
            font-size: 0.95rem;
            margin-bottom: 15px;
        }}
        
        .preview-channel-badge {{
            display: inline-block;
            padding: 6px 12px;
            background: rgba(102, 126, 234, 0.3);
            color: white;
            border-radius: 20px;
            font-size: 0.85rem;
            font-weight: 600;
        }}
        
        /* Анимации */
        @keyframes fadeIn {{
            from {{ opacity: 0; }}
            to {{ opacity: 1; }}
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
        
        @keyframes pulse {{
            0% {{ transform: scale(1); }}
            50% {{ transform: scale(1.05); }}
            100% {{ transform: scale(1); }}
        }}
        
        .pulse-animation {{
            animation: pulse 2s infinite;
        }}
        
        /* Стили для модального окна настроек канала с кнопкой закрыть */
        .channel-settings-header {{
            display: flex;
            justify-content: space-between;
            align-items: center;
            margin-bottom: 25px;
            padding-bottom: 15px;
            border-bottom: 1px solid var(--border);
        }}
        
        .channel-settings-header h3 {{
            margin: 0;
            font-size: 1.3rem;
            display: flex;
            align-items: center;
            gap: 10px;
            color: var(--text);
        }}
        
        .channel-settings-close-btn {{
            background: none;
            border: none;
            color: var(--text);
            cursor: pointer;
            width: 36px;
            height: 36px;
            border-radius: 50%;
            display: flex;
            align-items: center;
            justify-content: center;
            font-size: 1.2rem;
            transition: all 0.2s ease;
            position: relative;
            z-index: 10;
        }}
        
        .channel-settings-close-btn:hover {{
            background: rgba(0, 0, 0, 0.05);
            transform: rotate(90deg);
        }}
        
        [data-theme="dark"] .channel-settings-close-btn {{
            color: var(--text);
        }}
        
        [data-theme="dark"] .channel-settings-close-btn:hover {{
            background: rgba(255, 255, 255, 0.1);
        }}
        
        /* Стили для мобильной версии настроек канала */
        @media (max-width: 768px) {{
            .channel-settings-modal .modal-content {{
                max-height: 85vh;
                padding: 20px 15px;
                margin: 10px;
                overflow-y: auto;
            }}
            
            .channel-settings-header {{
                padding: 10px 0 15px 0;
                margin-bottom: 20px;
                position: sticky;
                top: 0;
                background: var(--input);
                z-index: 10;
            }}
            
            .channel-settings-header h3 {{
                font-size: 1.2rem;
            }}
            
            .channel-avatar-preview {{
                width: 80px;
                height: 80px;
            }}
            
            .member-list {{
                max-height: 250px;
            }}
            
            .member-item {{
                padding: 10px;
                flex-direction: column;
                align-items: flex-start;
                gap: 10px;
            }}
            
            .member-info {{
                width: 100%;
            }}
            
            .member-actions {{
                width: 100%;
                justify-content: flex-end;
            }}
            
            .channel-description textarea {{
                min-height: 60px;
            }}
            
            .channel-avatar-section {{
                margin-bottom: 20px;
            }}
            
            .channel-info-section {{
                margin-bottom: 20px;
            }}
            
            .channel-members-section {{
                margin-bottom: 20px;
            }}
        }}
        
        /* Улучшения для мобильных устройств */
        .mobile-optimized {{
            -webkit-touch-callout: none;
            -webkit-user-select: none;
            user-select: none;
        }}
        
        .touch-friendly-btn {{
            min-height: 44px;
            min-width: 44px;
            padding: 12px;
        }}
        
        /* Адаптивные кнопки в настройках канала */
        .mobile-action-buttons {{
            display: flex;
            flex-wrap: wrap;
            gap: 8px;
            margin-top: 10px;
        }}
        
        .mobile-action-btn {{
            flex: 1;
            min-width: 120px;
            padding: 10px;
            font-size: 0.9rem;
        }}
        
        @media (max-width: 480px) {{
            .mobile-action-btn {{
                min-width: 100%;
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
        
        /* Анимация для кнопки закрыть */
        .close-btn-animation {{
            transition: transform 0.3s ease;
        }}
        
        .close-btn-animation:hover {{
            transform: rotate(90deg);
        }}
        
        /* НОВЫЕ СТИЛИ ДЛЯ БЛОКА ПРОФИЛЯ ПОЛЬЗОВАТЕЛЯ */
        .profile-modal-overlay {{
            display: none;
            position: fixed;
            top: 0;
            left: 0;
            width: 100%;
            height: 100%;
            background: rgba(0, 0, 0, 0.7);
            backdrop-filter: blur(10px);
            -webkit-backdrop-filter: blur(10px);
            z-index: 2001;
            animation: fadeIn 0.3s ease-out;
            align-items: center;
            justify-content: center;
            padding: 20px;
        }}
        
        .profile-modal-container {{
            background: rgba(255, 255, 255, 0.95);
            backdrop-filter: blur(20px);
            -webkit-backdrop-filter: blur(20px);
            border-radius: 24px;
            border: 1px solid rgba(255, 255, 255, 0.3);
            padding: 30px;
            width: 100%;
            max-width: 400px;
            max-height: 90vh;
            overflow-y: auto;
            -webkit-overflow-scrolling: touch;
            box-shadow: 
                0 25px 60px rgba(0, 0, 0, 0.2),
                inset 0 1px 0 rgba(255, 255, 255, 0.4);
            position: relative;
            animation: slideUp 0.4s cubic-bezier(0.4, 0, 0.2, 1);
        }}
        
        [data-theme="dark"] .profile-modal-container {{
            background: rgba(30, 30, 40, 0.95);
            border: 1px solid rgba(255, 255, 255, 0.2);
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
            background: linear-gradient(90deg, transparent, #667eea, #764ba2, transparent);
            border-radius: 2px;
        }}
        
        .profile-avatar-large {{
            width: 100px;
            height: 100px;
            margin: 0 auto 20px;
            border-radius: 50%;
            background: linear-gradient(135deg, #667eea, #764ba2);
            display: flex;
            align-items: center;
            justify-content: center;
            color: white;
            font-weight: bold;
            font-size: 2rem;
            background-size: cover;
            background-position: center;
            border: 4px solid rgba(255, 255, 255, 0.3);
            box-shadow: 0 10px 30px rgba(0, 0, 0, 0.2);
        }}
        
        .profile-username {{
            font-size: 1.5rem;
            font-weight: 800;
            margin-bottom: 5px;
            color: #333;
        }}
        
        [data-theme="dark"] .profile-username {{
            color: white;
        }}
        
        .profile-status {{
            display: flex;
            align-items: center;
            justify-content: center;
            gap: 8px;
            color: #666;
            font-size: 0.9rem;
            margin-bottom: 5px;
        }}
        
        [data-theme="dark"] .profile-status {{
            color: #ccc;
        }}
        
        .status-dot-large {{
            width: 10px;
            height: 10px;
            border-radius: 50%;
            background: #10b981;
        }}
        
        .profile-description {{
            margin: 25px 0;
            text-align: center;
        }}
        
        .profile-description-label {{
            font-size: 0.9rem;
            color: #666;
            margin-bottom: 10px;
            font-weight: 600;
        }}
        
        [data-theme="dark"] .profile-description-label {{
            color: #ccc;
        }}
        
        .profile-description-text {{
            background: rgba(255, 255, 255, 0.1);
            backdrop-filter: blur(10px);
            border-radius: 12px;
            padding: 15px;
            color: #333;
            font-size: 0.95rem;
            line-height: 1.5;
            min-height: 80px;
            border: 1px solid rgba(0, 0, 0, 0.1);
        }}
        
        [data-theme="dark"] .profile-description-text {{
            background: rgba(255, 255, 255, 0.05);
            color: #eee;
            border: 1px solid rgba(255, 255, 255, 0.1);
        }}
        
        .profile-description-edit {{
            display: none;
        }}
        
        .profile-description-textarea {{
            width: 100%;
            padding: 15px;
            border: 1px solid rgba(0, 0, 0, 0.1);
            border-radius: 12px;
            background: rgba(255, 255, 255, 0.1);
            backdrop-filter: blur(10px);
            color: #333;
            font-size: 0.95rem;
            line-height: 1.5;
            min-height: 100px;
            resize: vertical;
        }}
        
        [data-theme="dark"] .profile-description-textarea {{
            background: rgba(255, 255, 255, 0.05);
            color: #eee;
            border: 1px solid rgba(255, 255, 255, 0.1);
        }}
        
        .profile-actions {{
            display: flex;
            gap: 10px;
            margin-top: 15px;
        }}
        
        .profile-action-btn {{
            flex: 1;
            padding: 12px;
            border: none;
            border-radius: 12px;
            font-weight: 600;
            font-size: 0.9rem;
            cursor: pointer;
            transition: all 0.3s ease;
            display: flex;
            align-items: center;
            justify-content: center;
            gap: 8px;
        }}
        
        .profile-edit-btn {{
            background: rgba(102, 126, 234, 0.1);
            border: 1px solid rgba(102, 126, 234, 0.3);
            color: #667eea;
        }}
        
        .profile-edit-btn:hover {{
            background: rgba(102, 126, 234, 0.2);
            transform: translateY(-2px);
        }}
        
        .profile-save-btn {{
            background: linear-gradient(135deg, #667eea, #764ba2);
            color: white;
            box-shadow: 0 4px 15px rgba(102, 126, 234, 0.3);
        }}
        
        .profile-save-btn:hover {{
            transform: translateY(-2px);
            box-shadow: 0 6px 20px rgba(102, 126, 234, 0.4);
        }}
        
        .profile-cancel-btn {{
            background: rgba(220, 53, 69, 0.1);
            border: 1px solid rgba(220, 53, 69, 0.3);
            color: #dc3545;
        }}
        
        .profile-cancel-btn:hover {{
            background: rgba(220, 53, 69, 0.2);
            transform: translateY(-2px);
        }}
        
        .profile-close-btn {{
            position: absolute;
            top: 15px;
            right: 15px;
            background: rgba(255, 255, 255, 0.15);
            border: 1px solid rgba(255, 255, 255, 0.25);
            color: #333;
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
        
        [data-theme="dark"] .profile-close-btn {{
            background: rgba(255, 255, 255, 0.1);
            border: 1px solid rgba(255, 255, 255, 0.2);
            color: white;
        }}
        
        .profile-close-btn:hover {{
            background: rgba(255, 255, 255, 0.25);
            transform: rotate(90deg);
        }}
        
        /* Адаптивность для профиля */
        @media (max-width: 768px) {{
            .profile-modal-container {{
                padding: 20px 15px;
                margin: 10px;
                max-height: 85vh;
            }}
            
            .profile-avatar-large {{
                width: 80px;
                height: 80px;
                font-size: 1.5rem;
            }}
            
            .profile-username {{
                font-size: 1.3rem;
            }}
            
            .profile-actions {{
                flex-direction: column;
            }}
            
            .profile-action-btn {{
                width: 100%;
            }}
        }}
        
        /* Стили для аватарок в личных чатах */
        .private-chat-avatar {{
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
            cursor: pointer;
        }}
        
        .private-chat-header {{
            display: flex;
            align-items: center;
            gap: 10px;
        }}
        
        .private-chat-info {{
            display: flex;
            flex-direction: column;
        }}
        
        .private-chat-name {{
            font-weight: 600;
            font-size: 1rem;
        }}
        
        .private-chat-status {{
            font-size: 0.8rem;
            color: #666;
            display: flex;
            align-items: center;
            gap: 5px;
        }}
        
        .private-chat-status-dot {{
            width: 8px;
            height: 8px;
            border-radius: 50%;
            background: #10b981;
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
                    <button class="add-btn" onclick="openCreateChannelGlassModal()">
                        <i class="fas fa-plus"></i>
                    </button>
                </div>
                <div id="channels">
                    <!-- Каналы будут загружены динамически с аватарками -->
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
                <div id="chat-header-content">
                    <!-- Содержимое заголовка чата будет меняться -->
                </div>
                <div class="channel-actions" id="channel-actions" style="display: none;">
                    <button class="channel-btn" onclick="openChannelSettingsModal()">
                        <i class="fas fa-cog"></i>
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
                
                <!-- Блок эмодзи (маленький) -->
                <div class="emoji-container" id="emoji-container">
                    <div class="emoji-picker">
                        <div class="emoji-header">
                            <div class="emoji-title">Эмодзи</div>
                            <button class="emoji-plus-btn" onclick="toggleFullEmojiPicker()">
                                <i class="fas fa-plus"></i>
                            </button>
                        </div>
                        <input type="text" class="emoji-search" placeholder="Поиск эмодзи..." id="emoji-search" oninput="searchEmojis()">
                        <div class="emoji-categories">
                            <button class="emoji-category-btn active" onclick="filterEmojis('all')">Все</button>
                            <button class="emoji-category-btn" onclick="filterEmojis('smileys')">😊</button>
                            <button class="emoji-category-btn" onclick="filterEmojis('people')">👋</button>
                            <button class="emoji-category-btn" onclick="filterEmojis('animals')">🐱</button>
                            <button class="emoji-category-btn" onclick="filterEmojis('food')">🍕</button>
                            <button class="emoji-category-btn" onclick="filterEmojis('objects')">📱</button>
                        </div>
                        <div class="emoji-grid" id="emoji-grid">
                            <!-- Эмодзи будут добавлены динамически -->
                        </div>
                    </div>
                </div>
                
                <!-- Большой блок эмодзи -->
                <div class="emoji-full-container" id="emoji-full-container">
                    <div class="emoji-full-picker">
                        <div class="emoji-header">
                            <div class="emoji-title">Все эмодзи</div>
                            <button class="emoji-plus-btn" onclick="toggleFullEmojiPicker()">
                                <i class="fas fa-times"></i>
                            </button>
                        </div>
                        <input type="text" class="emoji-search" placeholder="Поиск эмодзи..." id="full-emoji-search" oninput="searchFullEmojis()">
                        <div class="emoji-categories">
                            <button class="emoji-category-btn active" onclick="filterFullEmojis('all')">Все</button>
                            <button class="emoji-category-btn" onclick="filterFullEmojis('smileys')">😊 Смайлы</button>
                            <button class="emoji-category-btn" onclick="filterFullEmojis('people')">👋 Люди</button>
                            <button class="emoji-category-btn" onclick="filterFullEmojis('animals')">🐱 Животные</button>
                            <button class="emoji-category-btn" onclick="filterFullEmojis('food')">🍕 Еда</button>
                            <button class="emoji-category-btn" onclick="filterFullEmojis('objects')">📱 Объекты</button>
                            <button class="emoji-category-btn" onclick="filterFullEmojis('symbols')">🔣 Символы</button>
                            <button class="emoji-category-btn" onclick="filterFullEmojis('flags')">🚩 Флаги</button>
                        </div>
                        <div class="emoji-full-grid" id="full-emoji-grid">
                            <!-- Все эмодзи будут добавлены динамически -->
                        </div>
                    </div>
                </div>
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

    <!-- НОВОЕ МОДАЛЬНОЕ ОКНО ПРОФИЛЯ ПОЛЬЗОВАТЕЛЯ -->
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
                <div class="profile-description-edit" id="profile-description-edit">
                    <textarea class="profile-description-textarea" id="profile-description-textarea" placeholder="Расскажите о себе..."></textarea>
                    <div class="profile-actions" id="profile-edit-actions" style="display: none;">
                        <button class="profile-action-btn profile-save-btn" onclick="saveProfileDescription()">
                            <i class="fas fa-save"></i> Сохранить
                        </button>
                        <button class="profile-action-btn profile-cancel-btn" onclick="cancelProfileEdit()">
                            <i class="fas fa-times"></i> Отмена
                        </button>
                    </div>
                </div>
            </div>
            
            <div class="profile-actions" id="profile-view-actions">
                <button class="profile-action-btn profile-edit-btn" onclick="editProfileDescription()" id="profile-edit-btn">
                    <i class="fas fa-edit"></i> Редактировать описание
                </button>
            </div>
        </div>
    </div>

    <!-- СТАРОЕ МОДАЛЬНОЕ ОКНО СОЗДАНИЯ КАНАЛА (оставлено для обратной совместимости) -->
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

    <!-- НОВОЕ МОДАЛЬНОЕ ОКНО СОЗДАНИЯ КАНАЛА В СТИЛЕ ЖИДКОЕ СТЕКЛО -->
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
                       placeholder="Например: team_chat, projects, news" 
                       oninput="updateChannelPreview()">
                <div class="glass-form-hint">Только латинские буквы, цифры и символ подчеркивания</div>
            </div>
            
            <div class="glass-form-group">
                <label class="glass-form-label">
                    <i class="fas fa-font"></i>
                    Отображаемое название
                </label>
                <input type="text" class="glass-form-input" id="glass-channel-display-name" 
                       placeholder="Например: Командный чат, Проекты, Новости"
                       oninput="updateChannelPreview()">
                <div class="glass-form-hint">Пользователи будут видеть это название</div>
            </div>
            
            <div class="glass-form-group">
                <label class="glass-form-label">
                    <i class="fas fa-align-left"></i>
                    Описание (необязательно)
                </label>
                <textarea class="glass-form-input glass-form-textarea" id="glass-channel-description" 
                          placeholder="Расскажите о назначении канала..."
                          oninput="updateChannelPreview()"></textarea>
            </div>
            
            <div class="glass-form-group">
                <label class="glass-form-checkbox">
                    <input type="checkbox" id="glass-channel-private" onchange="updateChannelPreview()">
                    <span class="glass-form-checkbox-text">Приватный канал (только по приглашению)</span>
                </label>
            </div>
            
            <div id="channel-preview" class="glass-channel-preview" style="display: none;">
                <h4>Предпросмотр канала</h4>
                <div class="preview-channel-avatar" id="preview-channel-avatar">
                    <i class="fas fa-hashtag"></i>
                </div>
                <div class="preview-channel-name" id="preview-channel-name">Название канала</div>
                <div class="preview-channel-desc" id="preview-channel-desc">Описание канала</div>
                <div class="preview-channel-badge" id="preview-channel-badge">Публичный канал</div>
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

    <!-- Модальное окно настроек канала с кнопкой закрыть и мобильной адаптацией -->
    <div class="modal channel-settings-modal" id="channel-settings-modal">
        <div class="modal-content">
            <div class="channel-settings-header">
                <h3><i class="fas fa-cog"></i> Настройки канала</h3>
                <button class="channel-settings-close-btn close-btn-animation" onclick="closeChannelSettingsModal()" title="Закрыть">
                    <i class="fas fa-times"></i>
                </button>
            </div>
            
            <div class="channel-avatar-section">
                <div class="channel-avatar-preview" id="channel-avatar-preview" onclick="document.getElementById('channel-avatar-input').click()"></div>
                <input type="file" id="channel-avatar-input" accept="image/*" style="display:none" onchange="previewChannelAvatar(this)">
                <div class="mobile-action-buttons">
                    <button class="btn btn-primary mobile-action-btn" onclick="uploadChannelAvatar()">
                        <i class="fas fa-upload"></i> Загрузить аватарку
                    </button>
                    <button class="btn btn-secondary mobile-action-btn" onclick="removeChannelAvatar()">
                        <i class="fas fa-trash"></i> Удалить аватарку
                    </button>
                </div>
            </div>
            
            <div class="channel-info-section">
                <h4 style="margin-bottom: 15px; color: var(--text);">Информация о канале</h4>
                <div class="form-group">
                    <label class="form-label">Название канала</label>
                    <div style="display: flex; gap: 10px; flex-wrap: wrap;">
                        <input type="text" class="form-control" id="channel-edit-name" placeholder="Название канала" style="flex: 1; min-width: 200px;">
                        <button class="btn btn-primary touch-friendly-btn" onclick="renameChannelFromModal()">
                            <i class="fas fa-edit"></i> Изменить
                        </button>
                    </div>
                </div>
                
                <div class="channel-description">
                    <label class="form-label">Описание канала</label>
                    <textarea class="form-control" id="channel-edit-description" placeholder="Добавьте описание канала..." rows="3"></textarea>
                    <button class="btn btn-primary touch-friendly-btn" onclick="updateChannelDescription()" style="margin-top: 10px;">
                        <i class="fas fa-save"></i> Сохранить описание
                    </button>
                </div>
            </div>
            
            <div class="channel-members-section">
                <h4 style="margin-bottom: 15px; display: flex; justify-content: space-between; align-items: center; color: var(--text);">
                    <span>Участники канала</span>
                    <button class="btn btn-primary touch-friendly-btn" onclick="openAddUserModalFromSettings()" style="padding: 5px 10px; font-size: 0.9rem;">
                        <i class="fas fa-user-plus"></i> Добавить
                    </button>
                </h4>
                <div class="member-list" id="channel-members-list">
                    <!-- Участники будут загружены динамически -->
                </div>
            </div>
            
            <div style="display: flex; gap: 10px; margin-top: 30px; flex-wrap: wrap;">
                <button class="btn btn-secondary touch-friendly-btn" onclick="closeChannelSettingsModal()" style="flex: 1; min-width: 120px;">
                    <i class="fas fa-times"></i> Закрыть
                </button>
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
        let currentProfileUser = "";

        // Определение мобильного устройства
        function checkMobile() {{
            isMobile = window.innerWidth <= 768;
            if (!isMobile) {{
                // На десктопе всегда показываем оба блока
                document.getElementById('sidebar').classList.remove('hidden');
                document.getElementById('chat-area').classList.add('active');
            }}
        }}

        // Переключение сайдбара
        function toggleSidebar() {{
            const sidebar = document.getElementById('sidebar');
            sidebar.classList.toggle('hidden');
        }}

        // Возврат к списку чатов
        function goBack() {{
            if (isMobile) {{
                document.getElementById('sidebar').classList.remove('hidden');
                document.getElementById('chat-area').classList.remove('active');
            }}
        }}

        // Инициализация при загрузке
        window.onload = function() {{
            checkMobile();
            loadUserAvatar();
            loadUserChannels();
            loadUsers();
            loadPersonalChats();
            loadFavoritesCategories();
            loadFavorites();
            initEmojis();
            
            // На мобильных устройствах показываем только сайдбар
            if (isMobile) {{
                document.getElementById('chat-area').classList.remove('active');
            }} else {{
                // На десктопе открываем избранное по умолчанию
                openFavorites();
            }}
            
            // Слушаем изменения размера окна
            window.addEventListener('resize', checkMobile);
            
            // Настраиваем управление клавиатурой для мобильных
            setupMobileKeyboard();
            
            // Скрываем эмодзи при клике вне блока
            document.addEventListener('click', function(event) {{
                const emojiContainer = document.getElementById('emoji-container');
                const emojiFullContainer = document.getElementById('emoji-full-container');
                const emojiBtn = document.querySelector('.emoji-btn');
                
                if (emojiContainer.style.display === 'block' && 
                    !emojiContainer.contains(event.target) && 
                    !emojiBtn.contains(event.target)) {{
                    emojiContainer.style.display = 'none';
                    emojiBtn.classList.remove('active');
                }}
                
                if (emojiFullContainer.style.display === 'block' && 
                    !emojiFullContainer.contains(event.target) && 
                    !emojiBtn.contains(event.target)) {{
                    emojiFullContainer.style.display = 'none';
                    emojiBtn.classList.remove('active');
                }}
            }});
            
            // Скрываем профиль при клике вне блока
            document.addEventListener('click', function(event) {{
                const profileModal = document.getElementById('profile-modal');
                if (profileModal.style.display === 'flex' && event.target === profileModal) {{
                    closeProfileModal();
                }}
            }});
        }};

        // Управление клавиатурой на мобильных устройствах
        function setupMobileKeyboard() {{
            if (!isMobile) return;
            
            const msgInput = document.getElementById('msg-input');
            const messagesContainer = document.getElementById('messages');
            
            msgInput.addEventListener('focus', function() {{
                // Прокручиваем к последнему сообщению при фокусе на поле ввода
                setTimeout(() => {{
                    if (messagesContainer.scrollHeight > messagesContainer.clientHeight) {{
                        messagesContainer.scrollTop = messagesContainer.scrollHeight;
                    }}
                }}, 300);
            }});
            
            msgInput.addEventListener('blur', function() {{
                // Мягкая прокрутка при скрытии клавиатуры
                setTimeout(() => {{
                    messagesContainer.scrollTop = messagesContainer.scrollHeight;
                }}, 100);
            }});
        }}

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

        // Открытие избранного
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
            
            // Обновляем заголовок чата
            const chatHeader = document.getElementById('chat-header-content');
            chatHeader.innerHTML = `
                <div style="flex: 1;">
                    <div style="font-weight: 600;" id="chat-title">Избранное</div>
                    <div style="font-size: 0.8rem; color: #666;" id="channel-description"></div>
                </div>
            `;
            
            // На мобильных устройствах переключаемся в режим чата
            if (isMobile) {{
                document.getElementById('sidebar').classList.add('hidden');
                document.getElementById('chat-area').classList.add('active');
            }}
            
            // Обновляем активные элементы в навигации
            document.querySelectorAll('.nav-item').forEach(el => el.classList.remove('active'));
            event.currentTarget.classList.add('active');
            
            loadFavorites(currentCategory === 'all' ? null : currentCategory);
        }}

        // НОВАЯ ФУНКЦИЯ: Открытие профиля пользователя
        function openUserProfile(username) {{
            currentProfileUser = username;
            
            fetch('/user_info/' + username)
                .then(r => r.json())
                .then(userInfo => {{
                    if (userInfo.success) {{
                        const profileModal = document.getElementById('profile-modal');
                        const profileAvatar = document.getElementById('profile-avatar-large');
                        const profileName = document.getElementById('profile-username');
                        const statusText = document.getElementById('profile-status-text');
                        const descriptionText = document.getElementById('profile-description-text');
                        const descriptionEdit = document.getElementById('profile-description-edit');
                        const profileEditBtn = document.getElementById('profile-edit-btn');
                        
                        // Устанавливаем аватарку
                        if (userInfo.avatar_path) {{
                            profileAvatar.style.backgroundImage = `url(${{userInfo.avatar_path}})`;
                            profileAvatar.textContent = '';
                        }} else {{
                            profileAvatar.style.backgroundImage = 'none';
                            profileAvatar.style.backgroundColor = userInfo.avatar_color;
                            profileAvatar.textContent = username.slice(0, 2).toUpperCase();
                        }}
                        
                        // Устанавливаем имя пользователя
                        profileName.textContent = username;
                        
                        // Устанавливаем статус
                        statusText.textContent = userInfo.online ? 'Online' : 'Offline';
                        
                        // Устанавливаем описание профиля
                        const description = userInfo.profile_description || 'Пользователь еще не добавил информацию о себе';
                        descriptionText.textContent = description;
                        
                        // Показываем/скрываем кнопку редактирования
                        if (username === user) {{
                            profileEditBtn.style.display = 'flex';
                        }} else {{
                            profileEditBtn.style.display = 'none';
                        }}
                        
                        // Скрываем редактор описания
                        descriptionText.style.display = 'block';
                        descriptionEdit.style.display = 'none';
                        document.getElementById('profile-edit-actions').style.display = 'none';
                        document.getElementById('profile-view-actions').style.display = 'flex';
                        
                        // Показываем модальное окно
                        profileModal.style.display = 'flex';
                        document.body.style.overflow = 'hidden';
                    }}
                }});
        }}

        // НОВАЯ ФУНКЦИЯ: Закрытие профиля пользователя
        function closeProfileModal() {{
            const profileModal = document.getElementById('profile-modal');
            profileModal.style.display = 'none';
            document.body.style.overflow = 'auto';
            currentProfileUser = "";
        }}

        // НОВАЯ ФУНКЦИЯ: Редактирование описания профиля
        function editProfileDescription() {{
            const descriptionText = document.getElementById('profile-description-text');
            const descriptionEdit = document.getElementById('profile-description-edit');
            const textarea = document.getElementById('profile-description-textarea');
            
            // Заполняем текстовое поле текущим описанием
            textarea.value = descriptionText.textContent;
            
            // Переключаем на режим редактирования
            descriptionText.style.display = 'none';
            descriptionEdit.style.display = 'block';
            document.getElementById('profile-edit-actions').style.display = 'flex';
            document.getElementById('profile-view-actions').style.display = 'none';
            
            // Фокус на текстовое поле
            setTimeout(() => {{
                textarea.focus();
            }}, 100);
        }}

        // НОВАЯ ФУНКЦИЯ: Отмена редактирования профиля
        function cancelProfileEdit() {{
            const descriptionText = document.getElementById('profile-description-text');
            const descriptionEdit = document.getElementById('profile-description-edit');
            
            descriptionText.style.display = 'block';
            descriptionEdit.style.display = 'none';
            document.getElementById('profile-edit-actions').style.display = 'none';
            document.getElementById('profile-view-actions').style.display = 'flex';
        }}

        // НОВАЯ ФУНКЦИЯ: Сохранение описания профиля
        function saveProfileDescription() {{
            const textarea = document.getElementById('profile-description-textarea');
            const description = textarea.value.trim();
            
            fetch('/update_profile_description', {{
                method: 'POST',
                headers: {{ 'Content-Type': 'application/json' }},
                body: JSON.stringify({{
                    description: description
                }})
            }})
            .then(r => r.json())
            .then(data => {{
                if (data.success) {{
                    // Обновляем текст описания
                    const descriptionText = document.getElementById('profile-description-text');
                    descriptionText.textContent = description || 'Пользователь еще не добавил информацию о себе';
                    
                    // Возвращаемся к виду просмотра
                    cancelProfileEdit();
                    
                    // Показываем уведомление
                    alert('Описание профиля обновлено!');
                }} else {{
                    alert(data.error || 'Ошибка при обновлении описания');
                }}
            }});
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

        // НОВАЯ ФУНКЦИЯ: Открытие модального окна создания канала в стиле жидкое стекло
        function openCreateChannelGlassModal() {{
            document.getElementById('create-channel-glass-modal').style.display = 'flex';
            document.body.style.overflow = 'hidden';
            // Очищаем поля и обновляем превью
            document.getElementById('glass-channel-name').value = '';
            document.getElementById('glass-channel-display-name').value = '';
            document.getElementById('glass-channel-description').value = '';
            document.getElementById('glass-channel-private').checked = false;
            updateChannelPreview();
        }}

        function closeCreateChannelGlassModal() {{
            document.getElementById('create-channel-glass-modal').style.display = 'none';
            document.body.style.overflow = 'auto';
        }}

        // Функция обновления предпросмотра канала
        function updateChannelPreview() {{
            const name = document.getElementById('glass-channel-name').value.trim();
            const displayName = document.getElementById('glass-channel-display-name').value.trim();
            const description = document.getElementById('glass-channel-description').value.trim();
            const isPrivate = document.getElementById('glass-channel-private').checked;
            
            const preview = document.getElementById('channel-preview');
            const previewAvatar = document.getElementById('preview-channel-avatar');
            const previewName = document.getElementById('preview-channel-name');
            const previewDesc = document.getElementById('preview-channel-desc');
            const previewBadge = document.getElementById('preview-channel-badge');
            
            if (name || displayName) {{
                preview.style.display = 'block';
                
                // Устанавливаем аватарку
                if (displayName) {{
                    previewAvatar.textContent = displayName.slice(0, 2).toUpperCase();
                }} else if (name) {{
                    previewAvatar.textContent = name.slice(0, 2).toUpperCase();
                }} else {{
                    previewAvatar.innerHTML = '<i class="fas fa-hashtag"></i>';
                }}
                
                // Устанавливаем название
                previewName.textContent = displayName || name || 'Название канала';
                
                // Устанавливаем описание
                previewDesc.textContent = description || 'Описание канала';
                
                // Устанавливаем бейдж
                previewBadge.textContent = isPrivate ? 'Приватный канал' : 'Публичный канал';
                previewBadge.style.background = isPrivate ? 
                    'rgba(220, 53, 69, 0.3)' : 
                    'rgba(102, 126, 234, 0.3)';
            }} else {{
                preview.style.display = 'none';
            }}
        }}

        // НОВАЯ ФУНКЦИЯ: Создание канала через красивое модальное окно
        async function createChannelGlass() {{
            const name = document.getElementById('glass-channel-name').value.trim();
            const displayName = document.getElementById('glass-channel-display-name').value.trim();
            const description = document.getElementById('glass-channel-description').value.trim();
            const isPrivate = document.getElementById('glass-channel-private').checked;
            
            if (!name) {{
                alert('Введите идентификатор канала');
                document.getElementById('glass-channel-name').focus();
                return;
            }}
            
            // Проверка имени канала
            if (!/^[a-zA-Z0-9_]+$/.test(name)) {{
                alert('Идентификатор канала может содержать только латинские буквы, цифры и символ подчеркивания');
                document.getElementById('glass-channel-name').focus();
                return;
            }}
            
            if (name.length < 2) {{
                alert('Идентификатор канала должен быть не менее 2 символов');
                document.getElementById('glass-channel-name').focus();
                return;
            }}
            
            if (name.length > 50) {{
                alert('Идентификатор канала должен быть не более 50 символов');
                document.getElementById('glass-channel-name').focus();
                return;
            }}
            
            const btn = document.getElementById('create-channel-glass-btn');
            const originalText = btn.innerHTML;
            btn.innerHTML = '<i class="fas fa-spinner fa-spin"></i> Создание...';
            btn.disabled = true;
            
            try {{
                const response = await fetch('/create_channel', {{
                    method: 'POST',
                    headers: {{ 'Content-Type': 'application/json' }},
                    body: JSON.stringify({{
                        name: name,
                        display_name: displayName || name,
                        description: description,
                        is_private: isPrivate
                    }})
                }});
                
                const data = await response.json();
                
                if (data.success) {{
                    closeCreateChannelGlassModal();
                    loadUserChannels();
                    
                    // Показываем анимацию успеха
                    showNotification('Канал создан успешно!', 'success');
                    
                    // Автоматически открываем созданный канал
                    setTimeout(() => {{
                        // Находим элемент созданного канала в списке и открываем его
                        const channelName = data.channel_name;
                        openRoom('channel_' + channelName, 'channel', data.display_name);
                    }}, 1000);
                }} else {{
                    alert(data.error || 'Ошибка при создании канала');
                    btn.innerHTML = originalText;
                    btn.disabled = false;
                }}
            }} catch (error) {{
                console.error('Error creating channel:', error);
                alert('Ошибка соединения с сервером');
                btn.innerHTML = originalText;
                btn.disabled = false;
            }}
        }}

        // Функция для показа уведомлений
        function showNotification(message, type = 'success') {{
            // Создаем элемент уведомления
            const notification = document.createElement('div');
            notification.style.cssText = `
                position: fixed;
                top: 20px;
                right: 20px;
                padding: 15px 25px;
                background: ${{type === 'success' ? 'linear-gradient(135deg, #10b981, #059669)' : 'linear-gradient(135deg, #ef4444, #dc2626)'}};
                color: white;
                border-radius: 12px;
                box-shadow: 0 10px 25px rgba(0,0,0,0.2);
                z-index: 9999;
                font-weight: 600;
                display: flex;
                align-items: center;
                gap: 10px;
                animation: slideInRight 0.3s ease, fadeOut 0.3s ease 2.7s;
                animation-fill-mode: forwards;
            `;
            
            notification.innerHTML = `
                <i class="fas fa-${{type === 'success' ? 'check-circle' : 'exclamation-circle'}}"></i>
                ${{message}}
            `;
            
            document.body.appendChild(notification);
            
            // Удаляем уведомление через 3 секунды
            setTimeout(() => {{
                if (notification.parentNode) {{
                    notification.parentNode.removeChild(notification);
                }}
            }}, 3000);
        }}

        // СТАРАЯ ФУНКЦИЯ создания канала (оставлена для обратной совместимости)
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

        function openAddUserModalFromSettings() {{
            closeChannelSettingsModal();
            openAddUserModal();
        }}

        function closeAddUserModal() {{
            document.getElementById('add-user-modal').style.display = 'none';
            document.getElementById('user-select').value = '';
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
                    document.getElementById('chat-title').textContent = newName;
                    closeRenameModal();
                    loadUserChannels();
                    alert('Канал переименован!');
                }} else {{
                    alert(data.error || 'Ошибка при переименовании канала');
                }}
            }});
        }}

        function renameChannelFromModal() {{
            const newName = document.getElementById('channel-edit-name').value.trim();
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
                    document.getElementById('chat-title').textContent = newName;
                    loadUserChannels();
                    loadChannelInfo();
                    alert('Канал переименован!');
                }} else {{
                    alert(data.error || 'Ошибка при переименовании канала');
                }}
            }});
        }}

        function updateChannelDescription() {{
            const description = document.getElementById('channel-edit-description').value.trim();
            
            fetch('/update_channel_description', {{
                method: 'POST',
                headers: {{ 'Content-Type': 'application/json' }},
                body: JSON.stringify({{
                    channel_name: currentChannel,
                    description: description
                }})
            }})
            .then(r => r.json())
            .then(data => {{
                if (data.success) {{
                    document.getElementById('channel-description').textContent = description;
                    loadChannelInfo();
                    alert('Описание канала обновлено!');
                }} else {{
                    alert(data.error || 'Ошибка при обновлении описания канала');
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
                    loadChannelMembers();
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
                    loadChannelMembers();
                    alert(data.message || 'Пользователь удален');
                }} else {{
                    alert(data.message || 'Ошибка при удалении пользователя');
                }}
            }});
        }}

        function makeUserAdmin(username) {{
            if (!confirm(`Назначить пользователя ${{username}} администратором канала?`)) return;
            
            fetch('/make_admin', {{
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
                    loadChannelMembers();
                    alert(data.message || 'Пользователь назначен администратором');
                }} else {{
                    alert(data.message || 'Ошибка при назначении администратора');
                }}
            }});
        }}

        function removeUserAdmin(username) {{
            if (!confirm(`Снять права администратора у пользователя ${{username}}?`)) return;
            
            fetch('/remove_admin', {{
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
                    loadChannelMembers();
                    alert(data.message || 'Права администратора сняты');
                }} else {{
                    alert(data.message || 'Ошибка при снятии прав администратора');
                }}
            }});
        }}

        // Функции для работы с аватаркой канала
        function previewChannelAvatar(input) {{
            const file = input.files[0];
            if (file) {{
                const reader = new FileReader();
                reader.onload = (e) => {{
                    const preview = document.getElementById('channel-avatar-preview');
                    preview.style.backgroundImage = `url(${{e.target.result}})`;
                    preview.textContent = '';
                }};
                reader.readAsDataURL(file);
            }}
        }}

        function uploadChannelAvatar() {{
            const fileInput = document.getElementById('channel-avatar-input');
            const file = fileInput.files[0];
            
            if (file) {{
                const formData = new FormData();
                formData.append('avatar', file);
                formData.append('channel_name', currentChannel);
                
                fetch('/upload_channel_avatar', {{
                    method: 'POST',
                    body: formData
                }})
                .then(r => r.json())
                .then(data => {{
                    if (data.success) {{
                        updateChannelAvatar(data.path);
                        alert('Аватарка канала обновлена!');
                    }} else {{
                        alert(data.error || 'Ошибка загрузки аватарки канала');
                    }}
                }});
            }} else {{
                alert('Выберите файл');
            }}
        }}

        function removeChannelAvatar() {{
            if (!confirm('Удалить аватарку канала?')) return;
            
            fetch('/delete_channel_avatar', {{
                method: 'POST',
                headers: {{ 'Content-Type': 'application/json' }},
                body: JSON.stringify({{
                    channel_name: currentChannel
                }})
            }})
            .then(r => r.json())
            .then(data => {{
                if (data.success) {{
                    updateChannelAvatar(null);
                    alert('Аватарка канала удалена!');
                }} else {{
                    alert(data.error || 'Ошибка удаления аватарки канала');
                }}
            }});
        }}

        function updateChannelAvatar(avatarPath) {{
            const channelAvatar = document.querySelector('.channel-header-avatar');
            const previewAvatar = document.getElementById('channel-avatar-preview');
            
            if (channelAvatar) {{
                if (avatarPath) {{
                    channelAvatar.style.backgroundImage = `url(${{avatarPath}})`;
                    channelAvatar.textContent = '';
                }} else {{
                    channelAvatar.style.backgroundImage = 'none';
                    channelAvatar.style.backgroundColor = '#667eea';
                    channelAvatar.textContent = currentChannel.slice(0, 2).toUpperCase();
                }}
            }}
            
            if (previewAvatar) {{
                if (avatarPath) {{
                    previewAvatar.style.backgroundImage = `url(${{avatarPath}})`;
                    previewAvatar.textContent = '';
                }} else {{
                    previewAvatar.style.backgroundImage = 'none';
                    previewAvatar.style.backgroundColor = '#667eea';
                    previewAvatar.textContent = currentChannel.slice(0, 2).toUpperCase();
                }}
            }}
        }}

        // Открытие настроек канала
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
                        
                        const previewAvatar = document.getElementById('channel-avatar-preview');
                        if (channelInfo.avatar_path) {{
                            previewAvatar.style.backgroundImage = `url(${{channelInfo.avatar_path}})`;
                            previewAvatar.textContent = '';
                        }} else {{
                            previewAvatar.style.backgroundImage = 'none';
                            previewAvatar.style.backgroundColor = '#667eea';
                            previewAvatar.textContent = currentChannel.slice(0, 2).toUpperCase();
                        }}
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
                            
                            const isCurrentUser = member.username === user;
                            const isCreator = data.data.created_by === member.username;
                            const canManage = data.data.created_by === user && !isCurrentUser;
                            
                            memberItem.innerHTML = `
                                <div class="member-info">
                                    <div class="member-avatar" style="background-color: ${{member.color}};">
                                        ${{member.avatar ? '' : member.username.slice(0, 2).toUpperCase()}}
                                    </div>
                                    <div class="member-name">
                                        ${{member.username}}
                                        ${{isCreator ? '<span class="member-role admin">Создатель</span>' : 
                                          member.is_admin ? '<span class="member-role admin">Админ</span>' : 
                                          '<span class="member-role">Участник</span>'}}
                                    </div>
                                </div>
                                ${{canManage ? `
                                    <div class="member-actions-section">
                                        ${{!member.is_admin ? 
                                            `<button class="action-btn admin" onclick="makeUserAdmin('${{member.username}}')" title="Назначить администратором">
                                                <i class="fas fa-user-shield"></i>
                                            </button>` : 
                                            `<button class="action-btn" onclick="removeUserAdmin('${{member.username}}')" title="Снять права администратора">
                                                <i class="fas fa-user-times"></i>
                                            </button>`}}
                                        <button class="action-btn remove" onclick="removeUserFromChannel('${{member.username}}')" title="Удалить из канала">
                                            <i class="fas fa-user-minus"></i>
                                        </button>
                                    </div>
                                ` : ''}}
                            `;
                            
                            membersList.appendChild(memberItem);
                            
                            // Загружаем аватарку если есть
                            if (member.avatar) {{
                                const avatar = memberItem.querySelector('.member-avatar');
                                avatar.style.backgroundImage = `url(${{member.avatar}})`;
                                avatar.textContent = '';
                            }}
                        }});
                    }}
                }});
        }}

        // Загрузка каналов пользователя с аватарками
        function loadUserChannels() {{
            fetch('/user_channels')
                .then(r => r.json())
                .then(data => {{
                    if (data.success) {{
                        const channelsContainer = document.getElementById('channels');
                        channelsContainer.innerHTML = '';
                        
                        // Добавляем пользовательские каналы
                        data.channels.forEach(channel => {{
                            const el = document.createElement('div');
                            el.className = 'nav-item' + (room === 'channel_' + channel.name ? ' active' : '');
                            
                            // Создаем аватарку канала
                            const channelAvatar = document.createElement('div');
                            channelAvatar.className = 'channel-avatar';
                            channelAvatar.style.backgroundColor = '#667eea';
                            
                            if (channel.avatar_path) {{
                                channelAvatar.style.backgroundImage = `url(${{channel.avatar_path}})`;
                                channelAvatar.textContent = '';
                            }} else {{
                                channelAvatar.textContent = channel.display_name ? channel.display_name.slice(0, 2).toUpperCase() : channel.name.slice(0, 2).toUpperCase();
                            }}
                            
                            el.appendChild(channelAvatar);
                            
                            const nameSpan = document.createElement('span');
                            nameSpan.textContent = channel.display_name || channel.name;
                            el.appendChild(nameSpan);
                            
                            el.onclick = () => openRoom('channel_' + channel.name, 'channel', channel.display_name || channel.name);
                            channelsContainer.appendChild(el);
                        }});
                    }}
                }});
        }}

        // Загрузка пользователей с аватарками
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
                                
                                // Создаем аватарку вместо иконки
                                const avatarDiv = document.createElement('div');
                                avatarDiv.className = `user-avatar ${{u.online ? 'online' : ''}}`;
                                avatarDiv.style.backgroundColor = u.color || '#6366F1';
                                
                                if (u.avatar) {{
                                    avatarDiv.style.backgroundImage = `url(${{u.avatar}})`;
                                }} else {{
                                    avatarDiv.textContent = u.username.slice(0, 2).toUpperCase();
                                }}
                                
                                el.appendChild(avatarDiv);
                                
                                const nameSpan = document.createElement('span');
                                nameSpan.textContent = u.username;
                                el.appendChild(nameSpan);
                                
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

        // Загрузка личных чатов с аватарками
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
                            
                            // Получаем информацию о пользователе для аватарки
                            fetch('/user_info/' + chatUser)
                                .then(r => r.json())
                                .then(userInfo => {{
                                    if (userInfo.success) {{
                                        const avatarDiv = document.createElement('div');
                                        avatarDiv.className = 'user-avatar';
                                        avatarDiv.style.backgroundColor = userInfo.avatar_color || '#6366F1';
                                        
                                        if (userInfo.avatar_path) {{
                                            avatarDiv.style.backgroundImage = `url(${{userInfo.avatar_path}})`;
                                        }} else {{
                                            avatarDiv.textContent = chatUser.slice(0, 2).toUpperCase();
                                        }}
                                        
                                        el.insertBefore(avatarDiv, el.firstChild);
                                    }}
                                }});
                            
                            const nameSpan = document.createElement('span');
                            nameSpan.textContent = chatUser;
                            el.appendChild(nameSpan);
                            
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
            
            document.getElementById('categories-filter').style.display = 'none';
            document.getElementById('favorites-grid').style.display = 'none';
            document.getElementById('channel-settings').style.display = 'none';
            document.getElementById('chat-messages').style.display = 'block';
            document.getElementById('input-area').style.display = 'flex';
            
            // Обновляем заголовок чата в зависимости от типа комнаты
            const chatHeader = document.getElementById('chat-header-content');
            
            if (t === 'channel') {{
                // Для каналов
                chatHeader.innerHTML = `
                    <div class="channel-header-avatar" id="channel-header-avatar" onclick="openChannelSettingsModal()"></div>
                    <div style="flex: 1;">
                        <div style="font-weight: 600;" id="chat-title">${{title}}</div>
                        <div style="font-size: 0.8rem; color: #666;" id="channel-description"></div>
                    </div>
                `;
                
                // Показываем кнопки управления каналом
                document.getElementById('channel-actions').style.display = 'flex';
                loadChannelHeaderInfo();
            }} else if (t === 'private') {{
                // Для личных чатов
                chatHeader.innerHTML = `
                    <div class="private-chat-header">
                        <div class="private-chat-avatar" id="private-chat-avatar" onclick="openUserProfile('${{title}}')"></div>
                        <div class="private-chat-info">
                            <div class="private-chat-name" id="chat-title">${{title}}</div>
                            <div class="private-chat-status">
                                <div class="private-chat-status-dot"></div>
                                <span id="channel-description">Online</span>
                            </div>
                        </div>
                    </div>
                `;
                
                // Загружаем информацию о пользователе для заголовка
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
                            
                            // Обновляем статус
                            const status = document.getElementById('channel-description');
                            status.textContent = userInfo.online ? 'Online' : 'Offline';
                        }}
                    }});
                
                // Скрываем кнопки управления каналом
                document.getElementById('channel-actions').style.display = 'none';
            }}
            
            // На мобильных устройствах переключаемся в режим чата
            if (isMobile) {{
                document.getElementById('sidebar').classList.add('hidden');
                document.getElementById('chat-area').classList.add('active');
                
                // Убедимся, что поле ввода всегда видно
                setTimeout(() => {{
                    const inputArea = document.getElementById('input-area');
                    if (inputArea) {{
                        inputArea.style.display = 'flex';
                        inputArea.style.position = 'fixed';
                        inputArea.style.bottom = '0';
                        inputArea.style.left = '0';
                        inputArea.style.right = '0';
                        inputArea.style.zIndex = '1000';
                    }}
                }}, 50);
            }}
            
            // Обновляем активные элементы в навигации
            document.querySelectorAll('.nav-item').forEach(el => el.classList.remove('active'));
            event.currentTarget.classList.add('active');
            
            // Очищаем чат и показываем заглушку
            const chatMessages = document.getElementById('chat-messages');
            chatMessages.innerHTML = '<div class="empty-chat"><i class="fas fa-comments"></i><h3>Начните общение</h3><p>Отправьте сообщение, чтобы начать чат</p></div>';
            
            // Загружаем историю
            loadMessages(r);
            
            // Присоединяемся к комнате через сокет
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

        // Загрузка сообщений комнаты
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
                        messagesContainer.innerHTML = '<div class="empty-chat"><i class="fas fa-comments"></i><h3>Начните общение</h3><p>Отправьте сообщение, чтобы начать чат</p></div>';
                    }}
                    
                    messagesContainer.scrollTop = messagesContainer.scrollHeight;
                }})
                .catch(error => console.error('Error loading messages:', error));
        }}

        // Добавление сообщения в чат (с поддержкой аватарок в личных чатах)
        function addMessageToChat(data, roomName = '') {{
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
            avatar.style.cursor = 'pointer';
            avatar.onclick = () => openUserProfile(data.user);
            
            // Загружаем информацию об отправителе для аватарки
            fetch('/user_info/' + data.user)
                .then(r => r.json())
                .then(userInfo => {{
                    if (userInfo.success) {{
                        if (userInfo.avatar_path) {{
                            avatar.style.backgroundImage = `url(${{userInfo.avatar_path}})`;
                            avatar.textContent = '';
                        }} else {{
                            avatar.style.backgroundColor = userInfo.avatar_color || data.color || '#6366F1';
                            avatar.textContent = data.user.slice(0, 2).toUpperCase();
                        }}
                    }} else {{
                        avatar.style.backgroundColor = data.color || '#6366F1';
                        avatar.textContent = data.user.slice(0, 2).toUpperCase();
                    }}
                }})
                .catch(() => {{
                    avatar.style.backgroundColor = data.color || '#6366F1';
                    avatar.textContent = data.user.slice(0, 2).toUpperCase();
                }});
            
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

        // ИСПРАВЛЕННАЯ ФУНКЦИЯ отправки сообщения
        async function sendMessage() {{
            const input = document.getElementById('msg-input');
            const msg = input.value.trim();
            const fileInput = document.getElementById('file-input');
            
            if (!msg && !fileInput.files[0]) return;
            
            let fileData = null;
            let fileName = null;
            let fileType = null;
            
            // Если есть файл, загружаем его через HTTP
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
                    console.error('File upload error:', error);
                    return;
                }}
            }}
            
            // Отправляем через WebSocket
            const messageData = {{
                message: msg,
                room: room,
                type: roomType
            }};
            
            // Добавляем информацию о файле если есть
            if (fileData) {{
                messageData.file = fileData;
                messageData.fileName = fileName;
                messageData.fileType = fileType;
            }}
            
            socket.emit('message', messageData);
            
            // Сбрасываем поле ввода сразу
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

        // Socket events - ИСПРАВЛЕННЫЙ ОБРАБОТЧИК
        socket.on('message', (data) => {{
            // Показываем сообщение только если мы в этой же комнате
            if (data.room === room) {{
                addMessageToChat(data, room);
            }}
        }});

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

        // ФУНКЦИИ ДЛЯ ЭМОДЗИ
        const emojiCategories = {{
            'smileys': ['😀', '😁', '😂', '🤣', '😃', '😄', '😅', '😆', '😉', '😊', '😋', '😎', '😍', '😘', '😗', '😙', '😚', '🙂', '🤗', '🤔'],
            'people': ['👋', '🤚', '🖐️', '✋', '🖖', '👌', '🤌', '🤏', '✌️', '🤞', '🤟', '🤘', '🤙', '👈', '👉', '👆', '🖕', '👇', '☝️', '👍'],
            'animals': ['🐶', '🐱', '🐭', '🐹', '🐰', '🦊', '🐻', '🐼', '🐻‍❄️', '🐨', '🐯', '🦁', '🐮', '🐷', '🐸', '🐵', '🙈', '🙉', '🙊', '🐔'],
            'food': ['🍏', '🍎', '🍐', '🍊', '🍋', '🍌', '🍉', '🍇', '🍓', '🫐', '🍈', '🍒', '🍑', '🥭', '🍍', '🥥', '🥝', '🍅', '🍆', '🥑'],
            'objects': ['⌚', '📱', '📲', '💻', '⌨️', '🖥️', '🖨️', '🖱️', '🖲️', '🕹️', '🗜️', '💽', '💾', '💿', '📀', '📼', '📷', '📸', '📹', '🎥'],
            'symbols': ['❤️', '🧡', '💛', '💚', '💙', '💜', '🖤', '🤍', '🤎', '💔', '❤️‍🔥', '❤️‍🩹', '❣️', '💕', '💞', '💓', '💗', '💖', '💘', '💝'],
            'flags': ['🏁', '🚩', '🎌', '🏴', '🏳️', '🏳️‍🌈', '🏳️‍⚧️', '🏴‍☠️', '🇦🇫', '🇦🇱', '🇩🇿', '🇦🇸', '🇦🇩', '🇦🇴', '🇦🇮', '🇦🇶', '🇦🇬', '🇦🇷', '🇦🇲', '🇦🇼']
        }};

        function initEmojis() {{
            // Инициализируем маленький блок эмодзи
            const emojiGrid = document.getElementById('emoji-grid');
            let allEmojis = [];
            
            // Собираем все эмодзи из всех категорий для маленького блока
            Object.values(emojiCategories).forEach(category => {{
                category.forEach(emoji => {{
                    if (!allEmojis.includes(emoji)) {{
                        allEmojis.push(emoji);
                    }}
                }});
            }});
            
            // Ограничиваем количество для маленького блока
            allEmojis = allEmojis.slice(0, 48);
            
            // Заполняем маленький блок
            allEmojis.forEach(emoji => {{
                const emojiItem = document.createElement('div');
                emojiItem.className = 'emoji-item';
                emojiItem.textContent = emoji;
                emojiItem.setAttribute('data-category', 'all');
                emojiItem.onclick = () => insertEmoji(emoji);
                emojiGrid.appendChild(emojiItem);
            }});
            
            // Инициализируем большой блок эмодзи
            const fullEmojiGrid = document.getElementById('full-emoji-grid');
            let allFullEmojis = [];
            
            // Собираем все эмодзи для большого блока
            Object.entries(emojiCategories).forEach(([category, emojis]) => {{
                emojis.forEach(emoji => {{
                    const emojiItem = document.createElement('div');
                    emojiItem.className = 'emoji-item';
                    emojiItem.textContent = emoji;
                    emojiItem.setAttribute('data-category', category);
                    emojiItem.onclick = () => insertEmoji(emoji);
                    fullEmojiGrid.appendChild(emojiItem);
                    allFullEmojis.push(emojiItem);
                }});
            }});
            
            // Сохраняем все элементы для фильтрации
            window.allEmojiItems = document.querySelectorAll('#full-emoji-grid .emoji-item');
        }}

        function toggleEmojiPicker() {{
            const emojiContainer = document.getElementById('emoji-container');
            const emojiFullContainer = document.getElementById('emoji-full-container');
            const emojiBtn = document.querySelector('.emoji-btn');
            
            if (emojiFullContainer.style.display === 'block') {{
                emojiFullContainer.style.display = 'none';
            }}
            
            if (emojiContainer.style.display === 'block') {{
                emojiContainer.style.display = 'none';
                emojiBtn.classList.remove('active');
            }} else {{
                emojiContainer.style.display = 'block';
                emojiBtn.classList.add('active');
                // Сброс поиска
                document.getElementById('emoji-search').value = '';
                // Показываем все эмодзи
                filterEmojis('all');
            }}
        }}

        function toggleFullEmojiPicker() {{
            const emojiContainer = document.getElementById('emoji-container');
            const emojiFullContainer = document.getElementById('emoji-full-container');
            const emojiBtn = document.querySelector('.emoji-btn');
            
            if (emojiContainer.style.display === 'block') {{
                emojiContainer.style.display = 'none';
            }}
            
            if (emojiFullContainer.style.display === 'block') {{
                emojiFullContainer.style.display = 'none';
                emojiBtn.classList.remove('active');
            }} else {{
                emojiFullContainer.style.display = 'block';
                emojiBtn.classList.add('active');
                // Сброс поиска
                document.getElementById('full-emoji-search').value = '';
                // Показываем все эмодзи
                filterFullEmojis('all');
            }}
        }}

        function insertEmoji(emoji) {{
            const input = document.getElementById('msg-input');
            const start = input.selectionStart;
            const end = input.selectionEnd;
            
            // Вставляем эмодзи в текущую позицию курсора
            input.value = input.value.substring(0, start) + emoji + input.value.substring(end);
            
            // Перемещаем курсор после вставленного эмодзи
            input.selectionStart = input.selectionEnd = start + emoji.length;
            
            // Фокус на поле ввода
            input.focus();
            
            // Автоматически меняем размер текстового поля
            autoResizeTextarea();
        }}

        function filterEmojis(category) {{
            const emojiItems = document.querySelectorAll('#emoji-grid .emoji-item');
            const categoryBtns = document.querySelectorAll('#emoji-container .emoji-category-btn');
            
            // Обновляем активную кнопку категории
            categoryBtns.forEach(btn => btn.classList.remove('active'));
            event.currentTarget.classList.add('active');
            
            // Показываем/скрываем эмодзи в зависимости от категории
            emojiItems.forEach(item => {{
                if (category === 'all' || item.getAttribute('data-category') === category) {{
                    item.style.display = 'flex';
                }} else {{
                    item.style.display = 'none';
                }}
            }});
        }}

        function filterFullEmojis(category) {{
            const categoryBtns = document.querySelectorAll('#emoji-full-container .emoji-category-btn');
            
            // Обновляем активную кнопку категории
            categoryBtns.forEach(btn => btn.classList.remove('active'));
            event.currentTarget.classList.add('active');
            
            // Показываем/скрываем эмодзи в зависимости от категории
            window.allEmojiItems.forEach(item => {{
                if (category === 'all' || item.getAttribute('data-category') === category) {{
                    item.style.display = 'flex';
                }} else {{
                    item.style.display = 'none';
                }}
            }});
        }}

        function searchEmojis() {{
            const searchTerm = document.getElementById('emoji-search').value.toLowerCase();
            const emojiItems = document.querySelectorAll('#emoji-grid .emoji-item');
            
            emojiItems.forEach(item => {{
                // Поскольку эмодзи не имеют текстового описания, просто показываем все
                // при пустом поиске или скрываем при непустом (так как нет данных для поиска)
                if (!searchTerm) {{
                    item.style.display = 'flex';
                }} else {{
                    // В реальном приложении здесь было бы сравнение с названиями эмодзи
                    item.style.display = 'none';
                }}
            }});
        }}

        function searchFullEmojis() {{
            const searchTerm = document.getElementById('full-emoji-search').value.toLowerCase();
            
            // Сброс категорий при поиске
            const categoryBtns = document.querySelectorAll('#emoji-full-container .emoji-category-btn');
            categoryBtns.forEach(btn => btn.classList.remove('active'));
            
            if (searchTerm) {{
                // Активируем категорию "Все" при поиске
                categoryBtns[0].classList.add('active');
            }}
            
            // В реальном приложении здесь была бы полноценная поисковая система
            // по названиям эмодзи, но для демо просто показываем все
            window.allEmojiItems.forEach(item => {{
                if (!searchTerm) {{
                    item.style.display = 'flex';
                }} else {{
                    // Показываем все для демонстрации
                    item.style.display = 'flex';
                }}
            }});
        }}

        // Инициализация Socket.IO
        socket.on('connect', function() {{
            console.log('Connected to server');
        }});

        socket.on('disconnect', function() {{
            console.log('Disconnected from server');
        }});
        
        // Закрытие модальных окон при клике вне их
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
        
        // Закрытие по клавише ESC
        document.addEventListener('keydown', function(event) {{
            if (event.key === 'Escape') {{
                closeCreateChannelGlassModal();
                closeProfileModal();
                
                // Также закрываем эмодзи
                const emojiContainer = document.getElementById('emoji-container');
                const emojiFullContainer = document.getElementById('emoji-full-container');
                const emojiBtn = document.querySelector('.emoji-btn');
                
                if (emojiContainer.style.display === 'block') {{
                    emojiContainer.style.display = 'none';
                    emojiBtn.classList.remove('active');
                }}
                
                if (emojiFullContainer.style.display === 'block') {{
                    emojiFullContainer.style.display = 'none';
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

    # ИСПРАВЛЕННЫЙ ОБРАБОТЧИК СООБЩЕНИЙ - ОТПРАВЛЯЕТСЯ ТОЛЬКО ОДИН РАЗ
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
        
        # Получаем информацию об отправителе для аватарки
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
        
        # Отправляем сообщение всем в комнате ТОЛЬКО ОДИН РАЗ
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
