# web_messenger.py - Tandau Messenger (единый файл с дизайном AURA)
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
        
        # Генерируем HTML с мобильной адаптацией и дизайном AURA
        return f'''
<!DOCTYPE html>
<html lang="ru" data-theme="{theme}">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0, maximum-scale=1.0, user-scalable=no">
    <title>AURA - {username}</title>
    <link rel="stylesheet" href="https://cdnjs.cloudflare.com/ajax/libs/font-awesome/6.5.0/css/all.min.css">
    <style>
        :root {{
            --primary: #6366f1;
            --primary-dark: #4f46e5;
            --primary-light: #818cf8;
            --secondary: #8b5cf6;
            --accent: #10b981;
            --bg: #0f172a;
            --sidebar-bg: #1e293b;
            --card-bg: #334155;
            --text: #f1f5f9;
            --text-light: #94a3b8;
            --border: #475569;
            --shadow: 0 10px 25px -5px rgba(0, 0, 0, 0.5);
            --radius: 12px;
            --radius-sm: 8px;
        }}
        
        [data-theme="light"] {{
            --bg: #f8fafc;
            --sidebar-bg: #ffffff;
            --card-bg: #ffffff;
            --text: #1e293b;
            --text-light: #64748b;
            --border: #e2e8f0;
            --shadow: 0 10px 25px -5px rgba(0, 0, 0, 0.1);
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
        
        /* Основной контейнер */
        .app-container {{
            display: flex;
            height: 100vh;
            position: relative;
        }}
        
        /* Сайдбар в стиле AURA */
        .sidebar {{
            width: 100%;
            background: var(--sidebar-bg);
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
        
        .sidebar-header {{
            padding: 20px;
            text-align: center;
            font-weight: 700;
            font-size: 1.5rem;
            display: flex;
            align-items: center;
            justify-content: center;
            gap: 12px;
            position: relative;
            background: linear-gradient(135deg, #667eea 0%, #764ba2 100%);
            color: white;
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
        
        .app-title {{
            color: white;
            font-size: 1.8rem;
            font-weight: 800;
            letter-spacing: 1px;
        }}
        
        /* ПОИСК секция */
        .search-section {{
            padding: 15px;
            border-bottom: 1px solid var(--border);
        }}
        
        .search-input {{
            width: 100%;
            padding: 12px 15px;
            border: 1px solid var(--border);
            border-radius: var(--radius-sm);
            background: var(--card-bg);
            color: var(--text);
            font-size: 0.9rem;
        }}
        
        .search-input:focus {{
            outline: none;
            border-color: var(--primary);
        }}
        
        /* Навигация */
        .nav {{
            flex: 1;
            overflow-y: auto;
            padding: 15px;
            -webkit-overflow-scrolling: touch;
        }}
        
        .nav-category {{
            margin-bottom: 25px;
        }}
        
        .category-title {{
            font-size: 0.8rem;
            color: var(--text-light);
            text-transform: uppercase;
            font-weight: 600;
            letter-spacing: 0.5px;
            margin-bottom: 10px;
            padding-left: 5px;
        }}
        
        .nav-items {{
            display: flex;
            flex-direction: column;
            gap: 5px;
        }}
        
        .nav-item {{
            display: flex;
            align-items: center;
            gap: 12px;
            padding: 12px 15px;
            border-radius: var(--radius-sm);
            cursor: pointer;
            transition: all 0.2s ease;
            text-decoration: none;
            color: var(--text);
        }}
        
        .nav-item:hover {{
            background: var(--card-bg);
        }}
        
        .nav-item.active {{
            background: var(--primary);
            color: white;
        }}
        
        .nav-item i {{
            width: 20px;
            text-align: center;
            font-size: 1.1rem;
        }}
        
        .badge {{
            margin-left: auto;
            background: var(--accent);
            color: white;
            padding: 2px 8px;
            border-radius: 10px;
            font-size: 0.7rem;
            font-weight: 600;
        }}
        
        /* Стили для каналов в навигации */
        .channel-item {{
            display: flex;
            align-items: center;
            gap: 10px;
            padding: 10px 15px;
            border-radius: var(--radius-sm);
            cursor: pointer;
            transition: all 0.2s ease;
        }}
        
        .channel-item:hover {{
            background: var(--card-bg);
        }}
        
        .channel-item.active {{
            background: var(--primary);
            color: white;
        }}
        
        .channel-avatar {{
            width: 30px;
            height: 30px;
            border-radius: 50%;
            background: var(--primary);
            color: white;
            display: flex;
            align-items: center;
            justify-content: center;
            font-weight: bold;
            font-size: 0.8rem;
            flex-shrink: 0;
        }}
        
        .channel-info {{
            flex: 1;
            min-width: 0;
        }}
        
        .channel-name {{
            font-size: 0.9rem;
            font-weight: 500;
            white-space: nowrap;
            overflow: hidden;
            text-overflow: ellipsis;
        }}
        
        .channel-meta {{
            font-size: 0.75rem;
            color: var(--text-light);
            margin-top: 2px;
        }}
        
        .join-btn {{
            background: var(--accent);
            color: white;
            border: none;
            padding: 4px 10px;
            border-radius: 6px;
            font-size: 0.7rem;
            cursor: pointer;
            flex-shrink: 0;
        }}
        
        /* Область чата */
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
        
        .chat-header {{
            padding: 15px 20px;
            background: var(--sidebar-bg);
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
            display: none;
        }}
        
        .chat-header-info {{
            flex: 1;
        }}
        
        .chat-title {{
            font-weight: 600;
            font-size: 1.1rem;
        }}
        
        .chat-subtitle {{
            font-size: 0.8rem;
            color: var(--text-light);
            margin-top: 2px;
        }}
        
        .chat-actions {{
            display: flex;
            gap: 10px;
        }}
        
        .chat-action-btn {{
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
            transition: all 0.2s ease;
        }}
        
        .chat-action-btn:hover {{
            background: var(--card-bg);
        }}
        
        /* Сообщения */
        .messages {{
            flex: 1;
            padding: 20px;
            overflow-y: auto;
            display: flex;
            flex-direction: column;
            gap: 15px;
            -webkit-overflow-scrolling: touch;
        }}
        
        .message {{
            display: flex;
            gap: 12px;
            max-width: 85%;
        }}
        
        .message.own {{
            align-self: flex-end;
            flex-direction: row-reverse;
        }}
        
        .message-avatar {{
            width: 36px;
            height: 36px;
            border-radius: 50%;
            background: var(--primary);
            color: white;
            display: flex;
            align-items: center;
            justify-content: center;
            font-weight: bold;
            font-size: 0.9rem;
            flex-shrink: 0;
            margin-top: 5px;
        }}
        
        .message-content {{
            background: var(--card-bg);
            padding: 12px 16px;
            border-radius: 18px;
            border-top-left-radius: 4px;
            box-shadow: 0 2px 8px rgba(0,0,0,0.1);
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
        
        .message-text {{
            word-break: break-word;
            line-height: 1.4;
        }}
        
        .message-time {{
            font-size: 0.75rem;
            color: var(--text-light);
            margin-top: 5px;
            text-align: right;
        }}
        
        .message.own .message-time {{
            color: rgba(255,255,255,0.8);
        }}
        
        /* Область ввода */
        .input-area {{
            padding: 15px 20px;
            background: var(--sidebar-bg);
            border-top: 1px solid var(--border);
        }}
        
        .input-row {{
            display: flex;
            gap: 10px;
            align-items: flex-end;
        }}
        
        .input-actions {{
            display: flex;
            gap: 5px;
        }}
        
        .input-action-btn {{
            background: var(--card-bg);
            border: none;
            color: var(--text);
            cursor: pointer;
            width: 40px;
            height: 40px;
            border-radius: 50%;
            display: flex;
            align-items: center;
            justify-content: center;
            transition: all 0.2s ease;
        }}
        
        .input-action-btn:hover {{
            background: var(--primary);
            color: white;
        }}
        
        .msg-input {{
            flex: 1;
            padding: 12px 16px;
            border: 1px solid var(--border);
            border-radius: 25px;
            background: var(--card-bg);
            color: var(--text);
            font-size: 1rem;
            resize: none;
            min-height: 44px;
            max-height: 120px;
            line-height: 1.4;
        }}
        
        .msg-input:focus {{
            outline: none;
            border-color: var(--primary);
        }}
        
        .send-btn {{
            background: var(--primary);
            color: white;
            border: none;
            width: 44px;
            height: 44px;
            border-radius: 50%;
            cursor: pointer;
            display: flex;
            align-items: center;
            justify-content: center;
            transition: all 0.2s ease;
        }}
        
        .send-btn:hover {{
            background: var(--primary-dark);
            transform: translateY(-2px);
        }}
        
        /* Избранное */
        .favorites-grid {{
            display: grid;
            grid-template-columns: repeat(auto-fill, minmax(250px, 1fr));
            gap: 15px;
            padding: 20px;
        }}
        
        .favorite-item {{
            background: var(--card-bg);
            border-radius: var(--radius);
            padding: 15px;
            border: 1px solid var(--border);
            transition: transform 0.2s ease;
        }}
        
        .favorite-item:hover {{
            transform: translateY(-2px);
            box-shadow: var(--shadow);
        }}
        
        .favorite-content {{
            margin-bottom: 10px;
            word-break: break-word;
        }}
        
        .favorite-meta {{
            display: flex;
            justify-content: space-between;
            align-items: center;
            font-size: 0.8rem;
            color: var(--text-light);
            margin-top: 10px;
        }}
        
        .category-badge {{
            background: var(--primary);
            color: white;
            padding: 2px 8px;
            border-radius: 10px;
            font-size: 0.7rem;
        }}
        
        /* Пользователи в личных чатах */
        .user-item {{
            display: flex;
            align-items: center;
            gap: 12px;
            padding: 12px 15px;
            border-radius: var(--radius-sm);
            cursor: pointer;
            transition: all 0.2s ease;
        }}
        
        .user-item:hover {{
            background: var(--card-bg);
        }}
        
        .user-avatar {{
            width: 36px;
            height: 36px;
            border-radius: 50%;
            background: var(--primary);
            color: white;
            display: flex;
            align-items: center;
            justify-content: center;
            font-weight: bold;
            font-size: 0.9rem;
            flex-shrink: 0;
        }}
        
        .user-info {{
            flex: 1;
        }}
        
        .user-name {{
            font-weight: 500;
            font-size: 0.9rem;
        }}
        
        .user-status {{
            font-size: 0.75rem;
            color: var(--text-light);
            margin-top: 2px;
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
        
        /* Кнопка выхода */
        .logout-btn {{
            margin: 20px 15px;
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
            transition: all 0.2s ease;
        }}
        
        .logout-btn:hover {{
            background: #c82333;
            transform: translateY(-2px);
        }}
        
        /* Адаптивность для мобильных */
        @media (max-width: 768px) {{
            .menu-toggle {{
                display: block;
            }}
            
            .back-btn {{
                display: block;
            }}
            
            .app-title {{
                font-size: 1.4rem;
            }}
            
            .favorites-grid {{
                grid-template-columns: 1fr;
                padding: 15px;
            }}
            
            .message {{
                max-width: 95%;
            }}
            
            .input-area {{
                position: fixed;
                bottom: 0;
                left: 0;
                right: 0;
                padding: 12px 15px;
                background: var(--sidebar-bg);
                border-top: 1px solid var(--border);
                z-index: 1000;
            }}
            
            .messages {{
                padding-bottom: 80px;
            }}
            
            .chat-area.active {{
                position: fixed;
                top: 0;
                left: 0;
                right: 0;
                bottom: 0;
                z-index: 1000;
            }}
        }}
        
        @media (min-width: 769px) {{
            .sidebar {{
                width: 280px;
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
        
        /* Скроллбар */
        ::-webkit-scrollbar {{
            width: 6px;
        }}
        
        ::-webkit-scrollbar-track {{
            background: transparent;
        }}
        
        ::-webkit-scrollbar-thumb {{
            background: var(--border);
            border-radius: 3px;
        }}
        
        ::-webkit-scrollbar-thumb:hover {{
            background: var(--primary);
        }}
        
        /* Анимации */
        @keyframes fadeIn {{
            from {{ opacity: 0; transform: translateY(10px); }}
            to {{ opacity: 1; transform: translateY(0); }}
        }}
        
        /* Пустые состояния */
        .empty-state {{
            text-align: center;
            padding: 60px 20px;
            color: var(--text-light);
        }}
        
        .empty-state i {{
            font-size: 3rem;
            margin-bottom: 20px;
            opacity: 0.3;
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
                <h1 class="app-title">AURA</h1>
            </div>
            
            <div class="search-section">
                <input type="text" class="search-input" placeholder="ПОИСК" id="search-input">
            </div>
            
            <div class="nav">
                <!-- Эссе и заметки -->
                <div class="nav-category">
                    <div class="category-title">Эссе</div>
                    <div class="nav-items">
                        <a class="nav-item active" href="#" onclick="openFavorites()">
                            <i class="fas fa-star"></i>
                            <span>Все заметки</span>
                        </a>
                        <a class="nav-item" href="#">
                            <i class="fas fa-user-friends"></i>
                            <span>личные каналы</span>
                        </a>
                        <a class="nav-item" href="#">
                            <i class="fas fa-bookmark"></i>
                            <span>полезное</span>
                        </a>
                        <a class="nav-item" href="#">
                            <i class="fas fa-heart"></i>
                            <span>избранное</span>
                        </a>
                    </div>
                </div>
                
                <!-- Каналы -->
                <div class="nav-category">
                    <div class="category-title">Каналы</div>
                    <div id="channels-list">
                        <!-- Каналы будут загружены динамически -->
                    </div>
                </div>
                
                <!-- Личные чаты -->
                <div class="nav-category">
                    <div class="category-title">Личные чаты</div>
                    <div id="personal-chats-list">
                        <!-- Личные чаты будут загружены динамически -->
                    </div>
                </div>
                
                <!-- Пользователи -->
                <div class="nav-category">
                    <div class="category-title">Пользователи</div>
                    <div id="users-list">
                        <!-- Пользователи будут загружены динамически -->
                    </div>
                </div>
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
                <div class="chat-header-info">
                    <div class="chat-title" id="chat-title">AURA</div>
                    <div class="chat-subtitle" id="chat-subtitle">Мессенджер нового поколения</div>
                </div>
                <div class="chat-actions" id="chat-actions" style="display: none;">
                    <button class="chat-action-btn">
                        <i class="fas fa-info-circle"></i>
                    </button>
                    <button class="chat-action-btn">
                        <i class="fas fa-ellipsis-v"></i>
                    </button>
                </div>
            </div>
            
            <div class="messages" id="messages">
                <!-- Избранное -->
                <div id="favorites-container" class="favorites-grid"></div>
                
                <!-- Чат -->
                <div id="chat-container" style="display: none;"></div>
            </div>
            
            <div class="input-area" id="input-area" style="display: none;">
                <div class="input-row">
                    <div class="input-actions">
                        <button class="input-action-btn">
                            <i class="fas fa-paperclip"></i>
                        </button>
                        <button class="input-action-btn">
                            <i class="fas fa-smile"></i>
                        </button>
                    </div>
                    <textarea class="msg-input" id="msg-input" placeholder="Написать сообщение..." rows="1"></textarea>
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
        const user = "{username}";
        let room = "favorites";
        let roomType = "favorites";
        let currentChannel = "";
        let isMobile = window.innerWidth <= 768;

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
            loadUserChannels();
            loadUsers();
            loadPersonalChats();
            loadFavorites();
            
            // На мобильных устройствах показываем только сайдбар
            if (isMobile) {{
                document.getElementById('chat-area').classList.remove('active');
            }} else {{
                // На десктопе открываем избранное по умолчанию
                openFavorites();
            }}
            
            // Слушаем изменения размера окна
            window.addEventListener('resize', checkMobile);
        }};

        // Открытие избранного
        function openFavorites() {{
            room = "favorites";
            roomType = "favorites";
            
            document.getElementById('chat-title').textContent = 'Все заметки';
            document.getElementById('chat-subtitle').textContent = 'Ваши сохраненные материалы';
            document.getElementById('chat-actions').style.display = 'none';
            document.getElementById('favorites-container').style.display = 'grid';
            document.getElementById('chat-container').style.display = 'none';
            document.getElementById('input-area').style.display = 'none';
            
            // Обновляем активные элементы
            document.querySelectorAll('.nav-item').forEach(item => {{
                item.classList.remove('active');
            }});
            event.currentTarget.classList.add('active');
            
            // На мобильных устройствах переключаемся в режим чата
            if (isMobile) {{
                document.getElementById('sidebar').classList.add('hidden');
                document.getElementById('chat-area').classList.add('active');
            }}
            
            loadFavorites();
        }}

        // Загрузка избранного
        function loadFavorites() {{
            fetch('/get_favorites')
                .then(r => r.json())
                .then(data => {{
                    if (data.success) {{
                        const container = document.getElementById('favorites-container');
                        
                        if (data.favorites.length === 0) {{
                            container.innerHTML = `
                                <div class="empty-state" style="grid-column: 1 / -1;">
                                    <i class="fas fa-star"></i>
                                    <h3>Пока ничего нет</h3>
                                    <p>Добавьте свои заметки, фото или видео</p>
                                </div>
                            `;
                        }} else {{
                            container.innerHTML = '';
                            data.favorites.forEach(favorite => {{
                                const item = document.createElement('div');
                                item.className = 'favorite-item';
                                
                                let contentHTML = '';
                                
                                if (favorite.content) {{
                                    contentHTML += `<div class="favorite-content">${{favorite.content}}</div>`;
                                }}
                                
                                if (favorite.file_path) {{
                                    if (favorite.file_type === 'image' || favorite.file_name.match(/\.(jpg|jpeg|png|gif|webp)$/i)) {{
                                        contentHTML += `
                                            <div style="margin-bottom: 10px;">
                                                <img src="${{favorite.file_path}}" alt="${{favorite.file_name}}" 
                                                     style="max-width: 100%; border-radius: 8px; cursor: pointer;"
                                                     onclick="window.open('${{favorite.file_path}}', '_blank')">
                                            </div>
                                        `;
                                    }}
                                }}
                                
                                const category = favorite.category && favorite.category !== 'general' ? 
                                    `<span class="category-badge">${{favorite.category}}</span>` : '';
                                
                                const date = new Date(favorite.created_at).toLocaleDateString('ru-RU', {{
                                    day: 'numeric',
                                    month: 'short'
                                }});
                                
                                item.innerHTML = `
                                    ${{contentHTML}}
                                    <div class="favorite-meta">
                                        <span>${{date}}</span>
                                        ${{category}}
                                    </div>
                                `;
                                
                                container.appendChild(item);
                            }});
                        }}
                    }}
                }});
        }}

        // Загрузка каналов
        function loadUserChannels() {{
            fetch('/user_channels')
                .then(r => r.json())
                .then(data => {{
                    if (data.success) {{
                        const container = document.getElementById('channels-list');
                        container.innerHTML = '';
                        
                        data.channels.forEach(channel => {{
                            const item = document.createElement('div');
                            item.className = 'channel-item';
                            
                            item.innerHTML = `
                                <div class="channel-avatar">
                                    ${{channel.display_name ? channel.display_name.slice(0, 2).toUpperCase() : channel.name.slice(0, 2).toUpperCase()}}
                                </div>
                                <div class="channel-info">
                                    <div class="channel-name">${{channel.display_name || channel.name}}</div>
                                    <div class="channel-meta">канал • активность</div>
                                </div>
                                <button class="join-btn" onclick="openChannel('${{channel.name}}', '${{channel.display_name || channel.name}}')">вступить</button>
                            `;
                            
                            container.appendChild(item);
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
                        const container = document.getElementById('users-list');
                        container.innerHTML = '';
                        
                        users.forEach(u => {{
                            if (u.username !== user) {{
                                const item = document.createElement('div');
                                item.className = 'user-item';
                                
                                item.innerHTML = `
                                    <div class="user-avatar" style="background-color: ${{u.color || '#6366F1'}};">
                                        ${{u.username.slice(0, 2).toUpperCase()}}
                                    </div>
                                    <div class="user-info">
                                        <div class="user-name">${{u.username}}</div>
                                        <div class="user-status">
                                            <div class="status-dot"></div>
                                            <span>${{u.online ? 'онлайн' : 'оффлайн'}}</span>
                                        </div>
                                    </div>
                                `;
                                
                                item.onclick = () => openPrivateChat(u.username);
                                container.appendChild(item);
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
                        const container = document.getElementById('personal-chats-list');
                        container.innerHTML = '';
                        
                        data.chats.forEach(chatUser => {{
                            const item = document.createElement('div');
                            item.className = 'user-item';
                            
                            item.innerHTML = `
                                <div class="user-avatar">
                                    ${{chatUser.slice(0, 2).toUpperCase()}}
                                </div>
                                <div class="user-info">
                                    <div class="user-name">${{chatUser}}</div>
                                    <div class="user-status">
                                        <div class="status-dot"></div>
                                        <span>последнее сообщение</span>
                                    </div>
                                </div>
                            `;
                            
                            item.onclick = () => openPrivateChat(chatUser);
                            container.appendChild(item);
                        }});
                    }}
                }});
        }}

        // Открытие канала
        function openChannel(channelName, displayName) {{
            room = 'channel_' + channelName;
            roomType = 'channel';
            currentChannel = channelName;
            
            document.getElementById('chat-title').textContent = displayName;
            document.getElementById('chat-subtitle').textContent = 'Канал';
            document.getElementById('chat-actions').style.display = 'flex';
            document.getElementById('favorites-container').style.display = 'none';
            document.getElementById('chat-container').style.display = 'block';
            document.getElementById('input-area').style.display = 'block';
            
            // На мобильных устройствах переключаемся в режим чата
            if (isMobile) {{
                document.getElementById('sidebar').classList.add('hidden');
                document.getElementById('chat-area').classList.add('active');
            }}
            
            // Очищаем чат и показываем заглушку
            const chatContainer = document.getElementById('chat-container');
            chatContainer.innerHTML = '<div class="empty-state"><i class="fas fa-comments"></i><h3>Начните общение</h3><p>Отправьте сообщение, чтобы начать чат</p></div>';
            
            // Загружаем историю
            loadMessages(room);
            
            // Присоединяемся к комнате через сокет
            socket.emit('join', {{ room: room }});
        }}

        // Открытие личного чата
        function openPrivateChat(username) {{
            room = 'private_' + [user, username].sort().join('_');
            roomType = 'private';
            
            document.getElementById('chat-title').textContent = username;
            document.getElementById('chat-subtitle').textContent = 'Личный чат';
            document.getElementById('chat-actions').style.display = 'flex';
            document.getElementById('favorites-container').style.display = 'none';
            document.getElementById('chat-container').style.display = 'block';
            document.getElementById('input-area').style.display = 'block';
            
            // На мобильных устройствах переключаемся в режим чата
            if (isMobile) {{
                document.getElementById('sidebar').classList.add('hidden');
                document.getElementById('chat-area').classList.add('active');
            }}
            
            // Очищаем чат и показываем заглушку
            const chatContainer = document.getElementById('chat-container');
            chatContainer.innerHTML = '<div class="empty-state"><i class="fas fa-comments"></i><h3>Начните общение</h3><p>Отправьте сообщение, чтобы начать чат</p></div>';
            
            // Загружаем историю
            loadMessages(room);
            
            // Присоединяемся к комнате через сокет
            socket.emit('join', {{ room: room }});
        }}

        // Загрузка сообщений
        function loadMessages(roomName) {{
            fetch('/get_messages/' + roomName)
                .then(r => r.json())
                .then(messages => {{
                    const container = document.getElementById('chat-container');
                    container.innerHTML = '';
                    
                    if (messages && Array.isArray(messages) && messages.length > 0) {{
                        messages.forEach(msg => {{
                            addMessageToChat(msg);
                        }});
                    }}
                    
                    container.scrollTop = container.scrollHeight;
                }});
        }}

        // Добавление сообщения в чат
        function addMessageToChat(data) {{
            const container = document.getElementById('chat-container');
            
            // Удаляем пустой экран, если он есть
            const emptyState = container.querySelector('.empty-state');
            if (emptyState) {{
                emptyState.remove();
            }}
            
            const message = document.createElement('div');
            message.className = `message ${{data.user === user ? 'own' : 'other'}}`;
            
            message.innerHTML = `
                <div class="message-avatar" style="background-color: ${{data.color || '#6366F1'}};">
                    ${{data.user.slice(0, 2).toUpperCase()}}
                </div>
                <div class="message-content">
                    <div class="message-sender">${{data.user}}</div>
                    <div class="message-text">${{data.message}}</div>
                    <div class="message-time">${{data.timestamp}}</div>
                </div>
            `;
            
            container.appendChild(message);
            container.scrollTop = container.scrollHeight;
        }}

        // Отправка сообщения
        function sendMessage() {{
            const input = document.getElementById('msg-input');
            const msg = input.value.trim();
            
            if (!msg) return;
            
            socket.emit('message', {{
                message: msg,
                room: room,
                type: roomType
            }});
            
            input.value = '';
            input.style.height = 'auto';
        }}

        // Автоматическое изменение высоты текстового поля
        document.getElementById('msg-input').addEventListener('input', function() {{
            this.style.height = 'auto';
            this.style.height = Math.min(this.scrollHeight, 120) + 'px';
        }});

        // Обработка нажатия Enter для отправки
        document.getElementById('msg-input').addEventListener('keydown', function(e) {{
            if (e.key === 'Enter' && !e.shiftKey) {{
                e.preventDefault();
                sendMessage();
            }}
        }});

        // Socket events
        socket.on('connect', function() {{
            console.log('Connected to server');
        }});

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

    # ИСПРАВЛЕННЫЙ ОБРАБОТЧИК СООБЩЕНИЙ
    @socketio.on('message')
    def on_message(data):
        if 'username' not in session:
            return
        
        msg = data.get('message', '').strip()
        room = data.get('room')
        
        # Для приватных чатов
        recipient = None
        if room.startswith('private_'):
            parts = room.split('_')
            if len(parts) == 3:
                user1, user2 = parts[1], parts[2]
                recipient = user1 if user2 == session['username'] else user2
        
        # Сохраняем сообщение в БД
        save_message(
            session['username'], 
            msg, 
            room, 
            recipient
        )
        
        # Получаем информацию об отправителе
        user_info = get_user(session['username'])
        user_color = user_info['avatar_color'] if user_info else '#6366F1'
        
        # Отправляем сообщение всем в комнате
        emit('message', {{
            'user': session['username'], 
            'message': msg, 
            'color': user_color,
            'timestamp': datetime.now().strftime('%H:%M'),
            'room': room
        }}, room=room)

    # Health check
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
