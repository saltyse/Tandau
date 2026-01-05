# web_messenger.py - AURA Messenger
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
            c.execute('SELECT username, is_online, avatar_color, avatar_path, theme, profile_description FROM users ORDER BY username')
            return [dict(zip(['username','online','color','avatar','theme','profile_description'], row)) for row in c.fetchall()]

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
                         (username, generate_password_hash(password), 
                          random.choice(['#6366F1','#8B5CF6','#10B981','#F59E0B','#EF4444','#3B82F6'])))
                
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
                ORDER BY timestamp ASC LIMIT ?
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
                c.execute('UPDATE favorites SET is_pinned = ? WHERE id = ? AND username = ?', (new_state, favorite_id, username))
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
                SELECT DISTINCT CASE WHEN username = ? THEN recipient ELSE username END as chat_user
                FROM messages
                WHERE (username = ? OR recipient = ?) AND room LIKE 'private_%' AND chat_user IS NOT NULL
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

    # === ИСПРАВЛЕННАЯ ФУНКЦИЯ: Поиск каналов и пользователей ===
    def search_channels_and_users(search_query, username):
        results = {'users': [], 'channels': []}
        with sqlite3.connect('messenger.db') as conn:
            c = conn.cursor()
            # Поиск пользователей
            c.execute('''
                SELECT username, is_online, avatar_color, avatar_path, theme, profile_description
                FROM users
                WHERE username LIKE ? AND username != ?
                ORDER BY username
            ''', (f'%{search_query}%', username))
            for row in c.fetchall():
                results['users'].append({
                    'username': row[0],
                    'online': row[1],
                    'color': row[2],
                    'avatar': row[3],
                    'theme': row[4],
                    'profile_description': row[5] or ''
                })
            
            # Поиск каналов (включая те, где пользователь является участником)
            c.execute('''
                SELECT DISTINCT c.name, c.display_name, c.description, c.is_private, c.allow_messages, c.created_by, c.avatar_path, c.subscriber_count
                FROM channels c
                WHERE (c.name LIKE ? OR c.display_name LIKE ? OR c.description LIKE ?)
                ORDER BY c.name
            ''', (f'%{search_query}%', f'%{search_query}%', f'%{search_query}%'))
            for row in c.fetchall():
                results['channels'].append({
                    'name': row[0],
                    'display_name': row[1],
                    'description': row[2],
                    'is_private': row[3],
                    'allow_messages': row[4],
                    'created_by': row[5],
                    'avatar_path': row[6],
                    'subscriber_count': row[7] or 0
                })
        return results

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

    # === ИСПРАВЛЕННАЯ ФУНКЦИЯ СОЗДАНИЯ КАНАЛА ===
    @app.route('/create_channel', methods=['POST'])
    def create_channel_handler():
        if 'username' not in session:
            return jsonify({'success': False, 'error': 'Не авторизован'})
        
        try:
            # Проверяем Content-Type
            if request.content_type == 'application/json':
                data = request.get_json()
                if not data:
                    return jsonify({'success': False, 'error': 'Пустой JSON'})
            else:
                # Пробуем получить данные из формы
                data = request.form.to_dict()
                if not data:
                    return jsonify({'success': False, 'error': 'Нет данных'})
            
            name = data.get('name', '').strip()
            display_name = data.get('display_name', '').strip()
            description = data.get('description', '').strip()
            is_private = data.get('is_private', False)
            
            if not name:
                return jsonify({'success': False, 'error': 'Название канала не может быть пустым'})
            
            # Автоматически создаем имя канала из названия
            channel_name = name.lower().replace(' ', '_').replace('-', '_')
            # Убираем все недопустимые символы
            channel_name = re.sub(r'[^a-z0-9_]', '', channel_name)
            
            if len(channel_name) < 2:
                return jsonify({'success': False, 'error': 'Название канала слишком короткое'})
            
            if not display_name:
                display_name = name
            
            # Создаем канал
            channel_id = create_channel(channel_name, display_name, description, session['username'], is_private)
            
            if channel_id:
                # Добавляем создателя в канал как участника
                with sqlite3.connect('messenger.db') as conn:
                    c = conn.cursor()
                    c.execute('''
                        INSERT OR IGNORE INTO channel_members (channel_id, username, is_admin)
                        VALUES (?, ?, ?)
                    ''', (channel_id, session['username'], True))
                    conn.commit()
                
                return jsonify({
                    'success': True,
                    'channel_name': channel_name,
                    'display_name': display_name,
                    'message': 'Канал успешно создан!'
                })
            
            return jsonify({'success': False, 'error': 'Канал с таким названием уже существует'})
            
        except Exception as e:
            print(f"Error creating channel: {e}")
            return jsonify({'success': False, 'error': f'Ошибка сервера: {str(e)}'})

    @app.route('/channel_info/<channel_name>')
    def channel_info_handler(channel_name):
        if 'username' not in session:
            return jsonify({'success': False, 'error': 'Не авторизован'})
        info = get_channel_info(channel_name)
        if info:
            info['is_member'] = is_channel_member(channel_name, session['username'])
            return jsonify({'success': True, 'data': info})
        return jsonify({'success': False, 'error': 'Канал не найден'})

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

    # === НОВЫЙ API ЭНДПОИНТ: Поиск с исправлениями ===
    @app.route('/search_users_channels')
    def search_handler():
        if 'username' not in session:
            return jsonify({'success': False, 'error': 'Не авторизован'})
        query = request.args.get('q', '').strip()
        if not query or len(query) < 2:
            return jsonify({'success': True, 'results': {'users': [], 'channels': []}})
        results = search_channels_and_users(query, session['username'])
        return jsonify({'success': True, 'results': results})

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
        # Возвращаем HTML страницу с новым дизайном AURA
        return '''<!DOCTYPE html>
<html lang="ru">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>AURA Messenger</title>
    <link rel="stylesheet" href="https://cdnjs.cloudflare.com/ajax/libs/font-awesome/6.5.0/css/all.min.css">
    <style>
        * { margin: 0; padding: 0; box-sizing: border-box; font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif; }
        :root {
            --primary: #7c3aed; --primary-dark: #6d28d9; --primary-light: #8b5cf6;
            --secondary: #a78bfa; --accent: #10b981; --aura-glow: rgba(124, 58, 237, 0.3);
            --text: #1f2937; --text-light: #6b7280; --bg: #f9fafb; --bg-light: #ffffff;
            --border: #e5e7eb; --shadow: 0 10px 25px -5px rgba(0, 0, 0, 0.1), 0 10px 10px -5px rgba(0, 0, 0, 0.04);
            --radius: 16px; --radius-sm: 10px; --transition: all 0.3s cubic-bezier(0.4, 0, 0.2, 1);
        }
        body {
            background: linear-gradient(135deg, #7c3aed 0%, #a78bfa 100%);
            min-height: 100vh; display: flex; align-items: center; justify-content: center; padding: 20px;
        }
        .container { width: 100%; max-width: 440px; }
        .logo-section { text-align: center; margin-bottom: 40px; animation: fadeInDown 0.8s ease-out; }
        .logo-container {
            display: inline-flex; align-items: center; justify-content: center; gap: 15px;
            background: rgba(255, 255, 255, 0.15); backdrop-filter: blur(15px); -webkit-backdrop-filter: blur(15px);
            padding: 22px 45px; border-radius: 28px; margin-bottom: 25px;
            box-shadow: 0 8px 32px rgba(0, 0, 0, 0.15), 0 0 0 1px rgba(255, 255, 255, 0.1), inset 0 1px 0 rgba(255, 255, 255, 0.2);
            border: 1px solid rgba(255, 255, 255, 0.25); position: relative; overflow: hidden;
        }
        .logo-container::before {
            content: ''; position: absolute; top: -50%; left: -50%; width: 200%; height: 200%;
            background: radial-gradient(circle, var(--aura-glow) 0%, transparent 70%);
            animation: auraPulse 4s ease-in-out infinite; z-index: 0;
        }
        .logo-placeholder {
            width: 65px; height: 65px; border-radius: 18px;
            background: linear-gradient(135deg, #7c3aed, #a78bfa);
            display: flex; align-items: center; justify-content: center;
            color: white; font-size: 28px; font-weight: bold;
            box-shadow: 0 4px 15px rgba(124, 58, 237, 0.4), inset 0 1px 0 rgba(255, 255, 255, 0.3);
            position: relative; z-index: 1; border: 2px solid rgba(255, 255, 255, 0.3);
        }
        .app-title {
            color: white; font-size: 3rem; font-weight: 800; letter-spacing: -0.5px;
            text-shadow: 0 2px 10px rgba(0, 0, 0, 0.3), 0 0 20px rgba(124, 58, 237, 0.4);
            position: relative; z-index: 1;
            background: linear-gradient(135deg, #ffffff, #e0e7ff);
            -webkit-background-clip: text; -webkit-text-fill-color: transparent; background-clip: text;
        }
        .app-subtitle {
            color: rgba(255, 255, 255, 0.9); font-size: 1.15rem; font-weight: 400;
            max-width: 320px; margin: 0 auto; line-height: 1.5;
            text-shadow: 0 1px 3px rgba(0, 0, 0, 0.2);
        }
        .auth-card {
            background: var(--bg-light); border-radius: var(--radius); box-shadow: var(--shadow); overflow: hidden;
            animation: fadeInUp 0.8s ease-out 0.2s both;
        }
        .auth-header {
            display: flex; background: white; border-bottom: 1px solid var(--border);
        }
        .auth-tab {
            flex: 1; padding: 20px; text-align: center; font-weight: 600; font-size: 1.1rem;
            color: var(--text-light); cursor: pointer; transition: var(--transition);
            position: relative; user-select: none;
        }
        .auth-tab:hover { color: var(--primary); background: rgba(124, 58, 237, 0.05); }
        .auth-tab.active { color: var(--primary); }
        .auth-tab.active::after {
            content: ''; position: absolute; bottom: 0; left: 20%; right: 20%;
            height: 3px; background: linear-gradient(90deg, var(--primary), var(--primary-light));
            border-radius: 3px;
        }
        .auth-content { padding: 40px; }
        .auth-form { display: none; animation: fadeIn 0.5s ease-out; }
        .auth-form.active { display: block; }
        .form-group { margin-bottom: 24px; }
        .form-label {
            display: block; margin-bottom: 8px; color: var(--text);
            font-weight: 500; font-size: 0.95rem;
        }
        .input-with-icon { position: relative; }
        .input-icon {
            position: absolute; left: 16px; top: 50%; transform: translateY(-50%);
            color: var(--text-light); font-size: 1.1rem;
        }
        .form-input {
            width: 100%; padding: 16px 16px 16px 48px;
            border: 2px solid var(--border); border-radius: var(--radius-sm);
            font-size: 1rem; transition: var(--transition); background: white;
        }
        .form-input:focus {
            outline: none; border-color: var(--primary);
            box-shadow: 0 0 0 3px rgba(124, 58, 237, 0.1);
        }
        .password-toggle {
            position: absolute; right: 16px; top: 50%; transform: translateY(-50%);
            background: none; border: none; color: var(--text-light);
            cursor: pointer; font-size: 1.1rem;
        }
        .btn {
            width: 100%; padding: 16px; border: none; border-radius: var(--radius-sm);
            font-size: 1rem; font-weight: 600; cursor: pointer; transition: var(--transition);
            display: flex; align-items: center; justify-content: center; gap: 10px;
        }
        .btn-primary {
            background: linear-gradient(135deg, var(--primary), var(--primary-dark));
            color: white; box-shadow: 0 4px 15px rgba(124, 58, 237, 0.3);
        }
        .btn-primary:hover {
            background: linear-gradient(135deg, var(--primary-dark), #5b21b6);
            transform: translateY(-2px); box-shadow: 0 6px 20px rgba(124, 58, 237, 0.4);
        }
        .btn-primary:active { transform: translateY(0); }
        .btn-google {
            background: white; color: var(--text); border: 2px solid var(--border); margin-top: 16px;
        }
        .btn-google:hover { background: var(--bg); border-color: var(--text-light); }
        .alert {
            padding: 14px 18px; border-radius: var(--radius-sm); margin-bottom: 24px;
            display: none; animation: slideIn 0.3s ease-out;
        }
        .alert-error { background: #fee; color: #c33; border-left: 4px solid #c33; }
        .alert-success { background: #efe; color: #363; border-left: 4px solid #363; }
        .terms {
            text-align: center; margin-top: 24px; color: var(--text-light); font-size: 0.9rem;
        }
        .terms a { color: var(--primary); text-decoration: none; cursor: pointer; }
        .terms a:hover { text-decoration: underline; }
        @keyframes fadeInUp { from { opacity: 0; transform: translateY(30px); } to { opacity: 1; transform: translateY(0); } }
        @keyframes fadeInDown { from { opacity: 0; transform: translateY(-20px); } to { opacity: 1; transform: translateY(0); } }
        @keyframes fadeIn { from { opacity: 0; } to { opacity: 1; } }
        @keyframes slideIn { from { opacity: 0; transform: translateX(-10px); } to { opacity: 1; transform: translateX(0); } }
        @keyframes auraPulse { 0%, 100% { opacity: 0.5; transform: scale(1); } 50% { opacity: 0.8; transform: scale(1.1); } }
        .loader {
            display: inline-block; width: 20px; height: 20px;
            border: 3px solid rgba(255, 255, 255, 0.3); border-radius: 50%;
            border-top-color: white; animation: spin 1s ease-in-out infinite;
        }
        @keyframes spin { to { transform: rotate(360deg); } }
    </style>
</head>
<body>
    <div class="container">
        <div class="logo-section">
            <div class="logo-container">
                <div class="logo-placeholder"><i class="fas fa-aura"></i></div>
                <h1 class="app-title">AURA</h1>
            </div>
            <p class="app-subtitle">Интуитивный и безопасный мессенджер для команд и личного общения</p>
        </div>
        <div class="auth-card">
            <div class="auth-header">
                <div class="auth-tab active" onclick="showTab('login')">Вход</div>
                <div class="auth-tab" onclick="showTab('register')">Регистрация</div>
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
                        <i class="fas fa-sign-in-alt"></i> Войти в аккаунт
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
                        <i class="fas fa-user-plus"></i> Создать аккаунт
                    </button>
                    <div class="terms">
                        Регистрируясь, вы соглашаетесь с нашими <a href="#" onclick="openTermsModal(); return false;">Условиями использования</a> и <a href="#" onclick="openPrivacyModal(); return false;">Политикой конфиденциальности</a>
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
            setTimeout(() => { alert.style.display = 'none'; }, 5000);
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
            if (!username || !password) { return showAlert('Заполните все поля'); }
            setLoading('login-btn', true);
            try {
                const response = await fetch('/login', {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/x-www-form-urlencoded' },
                    body: new URLSearchParams({ username, password })
                });
                const data = await response.json();
                if (data.success) {
                    showAlert('Успешный вход! Перенаправляем...', 'success');
                    setTimeout(() => { window.location.href = '/chat'; }, 1000);
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
            if (!username || !password || !confirm) { return showAlert('Заполните все поля'); }
            if (username.length < 3) { return showAlert('Логин должен быть не менее 3 символов'); }
            if (username.length > 20) { return showAlert('Логин должен быть не более 20 символов'); }
            if (password.length < 4) { return showAlert('Пароль должен быть не менее 4 символов'); }
            if (password !== confirm) { return showAlert('Пароли не совпадают'); }
            setLoading('register-btn', true);
            try {
                const response = await fetch('/register', {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/x-www-form-urlencoded' },
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
        function openTermsModal() { alert('Условия использования - демо версия'); }
        function openPrivacyModal() { alert('Политика конфиденциальности - демо версия'); }
        document.addEventListener('keypress', function(e) {
            if (e.key === 'Enter') {
                const activeForm = document.querySelector('.auth-form.active');
                if (activeForm.id === 'login-form') login();
                if (activeForm.id === 'register-form') register();
            }
        });
    </script>
</body>
</html>'''

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
        # Генерируем HTML с улучшенным дизайном для мобильных в стиле из скриншота
        return f'''<!DOCTYPE html>
<html lang="ru" data-theme="{theme}">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0, maximum-scale=5.0, user-scalable=yes">
    <title>AURA Messenger - {username}</title>
    <link rel="stylesheet" href="https://cdnjs.cloudflare.com/ajax/libs/font-awesome/6.5.0/css/all.min.css">
    <style>
        /* ОСНОВНЫЕ СТИЛИ AURA в стиле из скриншота */
        :root {{
            --primary: #7c3aed; --primary-dark: #6d28d9; --primary-light: #8b5cf6;
            --secondary: #a78bfa; --accent: #10b981; --bg: #0f0f23; --bg-light: #1a1a2e;
            --bg-lighter: #2d2d4d; --text: #ffffff; --text-light: #a0a0c0; --text-lighter: #d0d0f0;
            --border: #3a3a5a; --border-light: #4a4a6a; --shadow: 0 10px 30px rgba(0, 0, 0, 0.5);
            --radius: 16px; --radius-sm: 12px; --radius-xs: 8px; --transition: all 0.3s cubic-bezier(0.4, 0, 0.2, 1);
            --glass-bg: rgba(255, 255, 255, 0.05); --glass-border: rgba(255, 255, 255, 0.1);
            --sidebar-width: 280px;
        }}
        [data-theme="light"] {{
            --bg: #f8f9fa; --bg-light: #ffffff; --bg-lighter: #f1f3f4;
            --text: #1a1a2e; --text-light: #6b7280; --text-lighter: #4b5563;
            --border: #e5e7eb; --border-light: #d1d5db; --glass-bg: rgba(0, 0, 0, 0.02);
            --glass-border: rgba(0, 0, 0, 0.08);
        }}
        * {{ margin: 0; padding: 0; box-sizing: border-box; -webkit-tap-highlight-color: transparent; }}
        body {{
            font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, 'Segoe UI Emoji', 'Segoe UI Symbol', sans-serif;
            background: var(--bg); color: var(--text); height: 100vh; overflow: hidden;
            touch-action: manipulation;
        }}
        /* Основной контейнер AURA */
        .app-container {{ display: flex; height: 100vh; }}
        /* Сайдбар AURA - минималистичный дизайн */
        .sidebar {{
            width: var(--sidebar-width); background: var(--bg-light); border-right: 1px solid var(--border);
            display: flex; flex-direction: column; position: relative; z-index: 10;
        }}
        /* Заголовок AURA */
        .sidebar-header {{
            padding: 20px 16px; display: flex; align-items: center; gap: 12px;
            border-bottom: 1px solid var(--border);
        }}
        .logo-placeholder {{
            width: 40px; height: 40px; border-radius: 12px;
            background: linear-gradient(135deg, var(--primary), var(--secondary));
            display: flex; align-items: center; justify-content: center;
            color: white; font-size: 20px; font-weight: bold; flex-shrink: 0;
        }}
        .app-title {{
            color: var(--text); font-size: 1.5rem; font-weight: 700; letter-spacing: -0.5px;
        }}
        /* ПОИСК AURA */
        .search-container {{ padding: 16px; border-bottom: 1px solid var(--border); }}
        .search-box {{ position: relative; }}
        .search-input {{
            width: 100%; padding: 12px 16px 12px 44px; border: 1px solid var(--border);
            border-radius: var(--radius-sm); background: var(--glass-bg);
            backdrop-filter: blur(10px); -webkit-backdrop-filter: blur(10px);
            color: var(--text); font-size: 0.9rem; transition: var(--transition);
        }}
        .search-input:focus {{
            outline: none; border-color: var(--primary); background: var(--glass-bg);
            box-shadow: 0 0 0 3px rgba(124, 58, 237, 0.1);
        }}
        .search-icon {{
            position: absolute; left: 16px; top: 50%; transform: translateY(-50%);
            color: var(--text-light); font-size: 1rem;
        }}
        /* Результаты поиска */
        .search-results {{
            position: absolute; top: calc(100% + 8px); left: 0; right: 0;
            background: var(--bg-light); border: 1px solid var(--border);
            border-radius: var(--radius-sm); box-shadow: var(--shadow);
            z-index: 1000; max-height: 400px; overflow-y: auto; display: none;
        }}
        .search-user-item, .search-channel-item {{
            padding: 12px 16px; border-bottom: 1px solid var(--border);
            cursor: pointer; transition: var(--transition);
            display: flex; align-items: center; gap: 12px;
        }}
        .search-user-item:hover, .search-channel-item:hover {{ background: var(--glass-bg); }}
        .search-user-avatar, .search-channel-avatar {{
            width: 36px; height: 36px; border-radius: 50%; background: var(--primary);
            color: white; display: flex; align-items: center; justify-content: center;
            font-weight: 600; font-size: 0.9rem; flex-shrink: 0;
        }}
        .search-channel-avatar {{ border-radius: 8px; }}
        .search-user-info, .search-channel-info {{ flex: 1; min-width: 0; }}
        .search-user-name, .search-channel-name {{
            font-size: 0.9rem; font-weight: 600; color: var(--text); margin-bottom: 2px;
        }}
        .search-user-desc, .search-channel-desc {{ font-size: 0.8rem; color: var(--text-light); }}
        /* Навигация AURA */
        .nav {{ flex: 1; overflow-y: auto; padding: 16px 8px; }}
        .nav-category {{
            padding: 8px 12px; font-size: 0.8rem; font-weight: 600;
            text-transform: uppercase; letter-spacing: 0.5px;
            color: var(--text-light); margin-bottom: 8px;
        }}
        .nav-item {{
            display: flex; align-items: center; gap: 12px; padding: 12px 16px;
            border-radius: var(--radius-sm); cursor: pointer; transition: var(--transition);
            margin-bottom: 4px; color: var(--text); text-decoration: none;
        }}
        .nav-item:hover {{ background: var(--glass-bg); }}
        .nav-item.active {{ background: rgba(124, 58, 237, 0.1); color: var(--primary); }}
        .nav-item i {{ width: 20px; text-align: center; font-size: 1.1rem; color: inherit; }}
        .nav-item-text {{ flex: 1; font-size: 0.9rem; font-weight: 500; }}
        .nav-item-badge {{
            background: var(--primary); color: white; font-size: 0.7rem;
            padding: 2px 6px; border-radius: 10px; font-weight: 600;
        }}
        /* Информация о пользователе */
        .user-info {{
            padding: 16px; border-top: 1px solid var(--border);
            display: flex; align-items: center; gap: 12px;
        }}
        .user-avatar {{
            width: 40px; height: 40px; border-radius: 50%; background: var(--primary);
            color: white; display: flex; align-items: center; justify-content: center;
            font-weight: 600; font-size: 0.9rem; flex-shrink: 0;
            cursor: pointer; position: relative;
        }}
        .user-avatar.online::after {{
            content: ''; position: absolute; bottom: 2px; right: 2px;
            width: 10px; height: 10px; background: var(--accent);
            border-radius: 50%; border: 2px solid var(--bg-light);
        }}
        .user-details {{ flex: 1; min-width: 0; }}
        .user-name {{ font-size: 0.9rem; font-weight: 600; color: var(--text); margin-bottom: 2px; }}
        .user-status {{ font-size: 0.8rem; color: var(--text-light); }}
        .user-actions {{ display: flex; gap: 8px; }}
        .user-action-btn {{
            background: none; border: none; color: var(--text-light);
            cursor: pointer; padding: 6px; border-radius: 6px;
            display: flex; align-items: center; justify-content: center;
            transition: var(--transition);
        }}
        .user-action-btn:hover {{ background: var(--glass-bg); color: var(--text); }}
        /* Основная область чата */
        .chat-area {{ flex: 1; display: flex; flex-direction: column; background: var(--bg); position: relative; }}
        /* Заголовок чата */
        .chat-header {{
            padding: 16px 20px; border-bottom: 1px solid var(--border);
            display: flex; align-items: center; gap: 16px;
            background: var(--bg-light); position: sticky; top: 0; z-index: 5;
        }}
        .back-btn {{
            display: none; background: none; border: none; color: var(--text);
            cursor: pointer; padding: 8px; font-size: 1.2rem;
        }}
        .chat-avatar {{
            width: 44px; height: 44px; border-radius: 50%; background: var(--primary);
            color: white; display: flex; align-items: center; justify-content: center;
            font-weight: 600; font-size: 1rem; flex-shrink: 0; cursor: pointer;
        }}
        .channel-avatar {{ border-radius: 12px; }}
        .chat-info {{ flex: 1; min-width: 0; }}
        .chat-title {{ font-size: 1.1rem; font-weight: 700; color: var(--text); margin-bottom: 4px; }}
        .chat-subtitle {{
            font-size: 0.85rem; color: var(--text-light);
            display: flex; align-items: center; gap: 6px;
        }}
        .status-dot {{
            width: 8px; height: 8px; border-radius: 50%; background: var(--accent);
            display: inline-block;
        }}
        .chat-actions {{ display: flex; gap: 8px; }}
        .chat-action-btn {{
            background: none; border: none; color: var(--text-light);
            cursor: pointer; padding: 8px; border-radius: 8px;
            display: flex; align-items: center; justify-content: center;
            transition: var(--transition);
        }}
        .chat-action-btn:hover {{ background: var(--glass-bg); color: var(--text); }}
        /* Сообщения AURA - стиль из скриншота */
        .messages {{ flex: 1; overflow-y: auto; padding: 20px; display: flex; flex-direction: column; gap: 24px; -webkit-overflow-scrolling: touch; }}
        .message-group {{ display: flex; flex-direction: column; gap: 4px; }}
        .message-group-date {{
            text-align: center; margin: 20px 0; position: relative;
        }}
        .message-group-date::before {{
            content: ''; position: absolute; top: 50%; left: 0; right: 0;
            height: 1px; background: var(--border); z-index: 1;
        }}
        .message-date-badge {{
            display: inline-block; padding: 6px 16px;
            background: var(--glass-bg); border: 1px solid var(--border);
            border-radius: 20px; font-size: 0.8rem; color: var(--text-light);
            position: relative; z-index: 2;
        }}
        .message {{ display: flex; gap: 12px; max-width: 80%; animation: fadeIn 0.3s ease; }}
        .message.own {{ align-self: flex-end; flex-direction: row-reverse; }}
        .message-avatar {{
            width: 36px; height: 36px; border-radius: 50%; background: var(--primary);
            color: white; display: flex; align-items: center; justify-content: center;
            font-weight: 600; font-size: 0.85rem; flex-shrink: 0; margin-top: 4px;
            cursor: pointer;
        }}
        .message-content {{
            background: var(--glass-bg); border: 1px solid var(--glass-border);
            border-radius: 18px; border-top-left-radius: 8px; padding: 12px 16px;
            max-width: 100%; word-wrap: break-word;
            backdrop-filter: blur(10px); -webkit-backdrop-filter: blur(10px);
        }}
        .message.own .message-content {{
            background: linear-gradient(135deg, var(--primary), var(--secondary));
            border-color: transparent; border-top-left-radius: 18px;
            border-top-right-radius: 8px; color: white;
        }}
        .message-sender {{
            font-weight: 700; font-size: 0.85rem; margin-bottom: 6px; color: var(--text);
        }}
        .message.own .message-sender {{ color: rgba(255, 255, 255, 0.9); }}
        .message-text {{ line-height: 1.5; font-size: 0.95rem; word-break: break-word; }}
        .message-file {{ margin-top: 10px; border-radius: 12px; overflow: hidden; max-width: 300px; }}
        .message-file img {{
            width: 100%; height: auto; border-radius: 12px;
            cursor: pointer; transition: transform 0.2s;
        }}
        .message-file img:hover {{ transform: scale(1.02); }}
        .message-file video {{ width: 100%; height: auto; border-radius: 12px; display: block; }}
        .message-time {{ font-size: 0.75rem; color: var(--text-light); margin-top: 6px; text-align: right; }}
        .message.own .message-time {{ color: rgba(255, 255, 255, 0.7); }}
        /* Поле ввода AURA */
        .input-area {{ padding: 20px; border-top: 1px solid var(--border); background: var(--bg-light); }}
        .input-container {{ display: flex; gap: 12px; align-items: flex-end; }}
        .input-actions {{ display: flex; gap: 8px; }}
        .input-action-btn {{
            background: var(--glass-bg); border: 1px solid var(--border);
            color: var(--text); cursor: pointer; padding: 10px; border-radius: 50%;
            display: flex; align-items: center; justify-content: center;
            transition: var(--transition); flex-shrink: 0;
        }}
        .input-action-btn:hover {{
            background: var(--glass-border); border-color: var(--primary);
            color: var(--primary);
        }}
        .input-wrapper {{ flex: 1; position: relative; }}
        .msg-input {{
            width: 100%; padding: 14px 16px; border: 1px solid var(--border);
            border-radius: 24px; background: var(--glass-bg); color: var(--text);
            font-size: 0.95rem; resize: none; min-height: 48px; max-height: 120px;
            line-height: 1.5; font-family: inherit; transition: var(--transition);
        }}
        .msg-input:focus {{
            outline: none; border-color: var(--primary); background: var(--glass-bg);
            box-shadow: 0 0 0 3px rgba(124, 58, 237, 0.1);
        }}
        .send-btn {{
            background: linear-gradient(135deg, var(--primary), var(--secondary));
            color: white; border: none; cursor: pointer; padding: 12px; border-radius: 50%;
            display: flex; align-items: center; justify-content: center;
            flex-shrink: 0; transition: var(--transition);
        }}
        .send-btn:hover {{
            transform: translateY(-2px); box-shadow: 0 6px 20px rgba(124, 58, 237, 0.4);
        }}
        .send-btn:active {{ transform: translateY(0); }}
        /* Эмодзи пикер */
        .emoji-container {{
            display: none; position: absolute; bottom: 80px; left: 20px; right: 20px;
            z-index: 100;
        }}
        .emoji-picker {{
            background: var(--bg-light); border: 1px solid var(--border);
            border-radius: var(--radius); padding: 16px; box-shadow: var(--shadow);
            max-height: 300px; overflow-y: auto;
        }}
        .emoji-grid {{ display: grid; grid-template-columns: repeat(8, 1fr); gap: 8px; }}
        .emoji-item {{
            font-size: 1.5rem; display: flex; align-items: center; justify-content: center;
            cursor: pointer; padding: 8px; border-radius: 8px; transition: var(--transition);
        }}
        .emoji-item:hover {{ background: var(--glass-bg); }}
        /* Модальные окна */
        .modal-overlay {{
            display: none; position: fixed; top: 0; left: 0; width: 100%; height: 100%;
            background: rgba(0, 0, 0, 0.7); backdrop-filter: blur(8px);
            -webkit-backdrop-filter: blur(8px); z-index: 1000;
            align-items: center; justify-content: center; padding: 20px;
        }}
        .modal-content {{
            background: var(--bg-light); border: 1px solid var(--border);
            border-radius: var(--radius); padding: 30px; width: 100%; max-width: 400px;
            max-height: 90vh; overflow-y: auto;
        }}
        /* Избранное */
        .favorites-grid {{
            display: grid; grid-template-columns: repeat(auto-fill, minmax(280px, 1fr));
            gap: 16px; padding: 20px;
        }}
        .favorite-item {{
            background: var(--glass-bg); border: 1px solid var(--glass-border);
            border-radius: var(--radius); padding: 20px; position: relative;
            transition: var(--transition);
        }}
        .favorite-item:hover {{
            transform: translateY(-4px); box-shadow: var(--shadow);
            border-color: var(--primary);
        }}
        .favorite-content {{ margin-bottom: 12px; font-size: 0.95rem; line-height: 1.5; }}
        .favorite-file {{ margin-top: 12px; border-radius: 12px; overflow: hidden; }}
        .favorite-file img, .favorite-file video {{
            width: 100%; height: auto; border-radius: 12px;
        }}
        .favorite-meta {{
            display: flex; justify-content: space-between; align-items: center;
            font-size: 0.8rem; color: var(--text-light); margin-top: 16px;
        }}
        .category-badge {{
            background: rgba(124, 58, 237, 0.1); color: var(--primary);
            padding: 4px 10px; border-radius: 12px; font-size: 0.75rem; font-weight: 500;
        }}
        /* Пустые состояния */
        .empty-state {{
            text-align: center; padding: 60px 20px; color: var(--text-light);
        }}
        .empty-state i {{ font-size: 3rem; margin-bottom: 16px; color: var(--border); }}
        .empty-state h3 {{ font-size: 1.2rem; margin-bottom: 8px; color: var(--text); }}
        .empty-state p {{
            font-size: 0.9rem; max-width: 300px; margin: 0 auto;
        }}
        /* Скроллбар */
        ::-webkit-scrollbar {{ width: 6px; }}
        ::-webkit-scrollbar-track {{ background: transparent; }}
        ::-webkit-scrollbar-thumb {{ background: var(--border); border-radius: 3px; }}
        ::-webkit-scrollbar-thumb:hover {{ background: var(--border-light); }}
        /* Анимации */
        @keyframes fadeIn {{ from {{ opacity: 0; transform: translateY(10px); }} to {{ opacity: 1; transform: translateY(0); }} }}
        /* Мобильная версия */
        @media (max-width: 768px) {{
            .sidebar {{
                position: fixed; top: 0; left: 0; bottom: 0;
                width: 100%; max-width: 320px;
                transform: translateX(-100%); transition: transform 0.3s ease;
                z-index: 100;
            }}
            .sidebar.active {{ transform: translateX(0); }}
            .back-btn {{ display: flex; align-items: center; justify-content: center; }}
            .messages {{ padding: 16px 12px; }}
            .message {{ max-width: 90%; }}
            .input-area {{ padding: 16px; }}
            .modal-content {{ padding: 20px; margin: 10px; }}
            .favorites-grid {{ grid-template-columns: 1fr; }}
        }}
        @media (min-width: 769px) {{ .back-btn {{ display: none; }} }}
    </style>
</head>
<body>
    <div class="app-container">
        <!-- Сайдбар AURA -->
        <div class="sidebar" id="sidebar">
            <div class="sidebar-header">
                <div class="logo-placeholder">
                    <i class="fas fa-aura"></i>
                </div>
                <h1 class="app-title">AURA</h1>
            </div>
            <!-- Поиск -->
            <div class="search-container">
                <div class="search-box">
                    <i class="fas fa-search search-icon"></i>
                    <input type="text" class="search-input" placeholder="Поиск..." id="search-input">
                    <div class="search-results" id="search-results"></div>
                </div>
            </div>
            <!-- Навигация -->
            <div class="nav">
                <div class="nav-category">Основное</div>
                <a href="#" class="nav-item active" onclick="openFavorites(event)">
                    <i class="fas fa-star"></i>
                    <span class="nav-item-text">Все заметки</span>
                </a>
                <a href="#" class="nav-item" onclick="openChat('general', 'channel', 'Общий')">
                    <i class="fas fa-hashtag"></i>
                    <span class="nav-item-text">Общий</span>
                </a>
                <a href="#" class="nav-item" onclick="openSupport()">
                    <i class="fas fa-headset"></i>
                    <span class="nav-item-text">Поддержка</span>
                </a>
                <div class="nav-category">Личные чаты</div>
                <div id="personal-chats-list">
                    <!-- Личные чаты будут загружены динамически -->
                </div>
                <div class="nav-category">Каналы</div>
                <a href="#" class="nav-item" onclick="openCreateChannel()">
                    <i class="fas fa-plus-circle"></i>
                    <span class="nav-item-text">Создать канал</span>
                </a>
                <div id="channels-list">
                    <!-- Каналы будут загружены динамически -->
                </div>
            </div>
            <!-- Информация о пользователе -->
            <div class="user-info">
                <div class="user-avatar online" id="user-avatar" onclick="openUserProfile('{username}')"></div>
                <div class="user-details">
                    <div class="user-name">{username}</div>
                    <div class="user-status">Online</div>
                </div>
                <div class="user-actions">
                    <button class="user-action-btn" onclick="openSettings()" title="Настройки">
                        <i class="fas fa-cog"></i>
                    </button>
                    <button class="user-action-btn" onclick="logout()" title="Выйти">
                        <i class="fas fa-sign-out-alt"></i>
                    </button>
                </div>
            </div>
        </div>
        <!-- Основная область чата -->
        <div class="chat-area" id="chat-area">
            <!-- Заголовок чата -->
            <div class="chat-header">
                <button class="back-btn" onclick="toggleSidebar()">
                    <i class="fas fa-bars"></i>
                </button>
                <div class="chat-avatar" id="chat-header-avatar"></div>
                <div class="chat-info">
                    <div class="chat-title" id="chat-title">Все заметки</div>
                    <div class="chat-subtitle" id="chat-subtitle">Ваши сохраненные материалы</div>
                </div>
                <div class="chat-actions" id="chat-actions"></div>
            </div>
            <!-- Сообщения / Избранное -->
            <div class="messages" id="messages">
                <div id="messages-content">
                    <!-- Контент будет загружен динамически -->
                    <div class="empty-state">
                        <i class="fas fa-star"></i>
                        <h3>Пока ничего нет</h3>
                        <p>Добавьте свои заметки, фото или видео</p>
                    </div>
                </div>
            </div>
            <!-- Поле ввода -->
            <div class="input-area" id="input-area">
                <div class="input-container">
                    <div class="input-actions">
                        <button class="input-action-btn" onclick="toggleEmojiPicker()">
                            <i class="far fa-smile"></i>
                        </button>
                        <button class="input-action-btn" onclick="document.getElementById('file-input').click()">
                            <i class="fas fa-paperclip"></i>
                        </button>
                    </div>
                    <div class="input-wrapper">
                        <textarea class="msg-input" id="msg-input" placeholder="Написать сообщение..." rows="1"></textarea>
                        <input type="file" id="file-input" style="display: none;" accept="image/*,video/*,text/*">
                    </div>
                    <button class="send-btn" onclick="sendMessage()">
                        <i class="fas fa-paper-plane"></i>
                    </button>
                </div>
                <div class="emoji-container" id="emoji-container">
                    <div class="emoji-picker">
                        <div class="emoji-grid" id="emoji-grid"></div>
                    </div>
                </div>
            </div>
        </div>
    </div>
    <!-- Модальное окно профиля -->
    <div class="modal-overlay" id="profile-modal">
        <div class="modal-content">
            <h3 style="margin-bottom: 20px;">Профиль</h3>
            <div class="user-avatar" style="width: 80px; height: 80px; font-size: 1.5rem; margin: 0 auto 20px;" id="modal-user-avatar"></div>
            <div style="text-align: center; margin-bottom: 20px;">
                <h4 id="modal-username">{username}</h4>
                <p style="color: var(--text-light); font-size: 0.9rem;" id="modal-user-description"></p>
            </div>
            <button class="btn btn-primary" onclick="closeModal('profile-modal')" style="width: 100%;">Закрыть</button>
        </div>
    </div>
    <!-- Модальное окно настроек -->
    <div class="modal-overlay" id="settings-modal">
        <div class="modal-content">
            <h3 style="margin-bottom: 20px;">Настройки</h3>
            <div style="margin-bottom: 20px;">
                <label style="display: block; margin-bottom: 8px; font-weight: 500;">Тема</label>
                <select id="theme-select" style="width: 100%; padding: 10px; border-radius: 8px; border: 1px solid var(--border); background: var(--bg-light); color: var(--text);">
                    <option value="dark">Темная</option>
                    <option value="light">Светлая</option>
                </select>
            </div>
            <button class="btn btn-primary" onclick="saveSettings()" style="width: 100%; margin-bottom: 10px;">Сохранить</button>
            <button class="btn" onclick="closeModal('settings-modal')" style="width: 100%;">Закрыть</button>
        </div>
    </div>
    <!-- Модальное окно создания канала -->
    <div class="modal-overlay" id="create-channel-modal">
        <div class="modal-content">
            <h3 style="margin-bottom: 20px;">Создать канал</h3>
            <div style="margin-bottom: 16px;">
                <input type="text" id="channel-name" placeholder="Название канала" style="width: 100%; padding: 10px; border-radius: 8px; border: 1px solid var(--border); background: var(--bg-light); color: var(--text); margin-bottom: 12px;">
                <textarea id="channel-description" placeholder="Описание (необязательно)" style="width: 100%; padding: 10px; border-radius: 8px; border: 1px solid var(--border); background: var(--bg-light); color: var(--text); min-height: 80px;"></textarea>
            </div>
            <button class="btn btn-primary" onclick="createChannel()" style="width: 100%; margin-bottom: 10px;">Создать</button>
            <button class="btn" onclick="closeModal('create-channel-modal')" style="width: 100%;">Отмена</button>
        </div>
    </div>
    <script src="https://cdnjs.cloudflare.com/ajax/libs/socket.io/4.7.2/socket.io.js"></script>
    <script>
        const socket = io();
        const user = "{username}";
        let currentRoom = "favorites";
        let currentRoomType = "favorites";
        let currentChannel = "";
        let isMobile = window.innerWidth <= 768;
        let emojiData = ["😀", "😁", "😂", "🤣", "😃", "😄", "😅", "😆", "😉", "😊", "😋", "😎", "😍", "😘", "😗", "😙", "😚", "🙂", "🤗", "🤔", "👋", "🤚", "🖐️", "✋", "🖖", "👌", "🤌", "🤏", "✌️", "🤞", "🤟", "🤘", "🤙", "👈", "👉", "👆", "🖕", "👇", "☝️", "👍", "🐶", "🐱", "🐭", "🐹", "🐰", "🦊", "🐻", "🐼", "🐨", "🐯", "🦁", "🐮", "🐷", "🐸", "🐵", "🙈", "🙉", "🙊", "🐔", "🐧", "🍏", "🍎", "🍐", "🍊", "🍋", "🍌", "🍉", "🍇", "🍓", "🫐", "🍈", "🍒", "🍑", "🥭", "🍍", "🥥", "🥝", "🍅", "🍆", "🥑", "⌚", "📱", "📲", "💻", "⌨️", "🖥️", "🖨️", "🖱️", "🖲️", "🕹️", "🗜️", "💽", "💾", "💿", "📀", "📼", "📷", "📸", "📹", "🎥"];
        
        // Инициализация
        window.onload = function() {{
            loadUserAvatar();
            loadPersonalChats();
            loadChannels();
            loadFavorites();
            initEmojis();
            checkMobile();
            
            // Событие ресайза
            window.addEventListener('resize', checkMobile);
            
            // Авторазмер textarea
            document.getElementById('msg-input').addEventListener('input', function() {{
                this.style.height = 'auto';
                this.style.height = Math.min(this.scrollHeight, 120) + 'px';
            }});
            
            // Отправка сообщения по Enter
            document.getElementById('msg-input').addEventListener('keydown', function(e) {{
                if (e.key === 'Enter' && !e.shiftKey) {{
                    e.preventDefault();
                    sendMessage();
                }}
            }});
            
            // Поиск
            document.getElementById('search-input').addEventListener('input', function(e) {{
                performSearch(e.target.value);
            }});
        }};
        
        function checkMobile() {{
            isMobile = window.innerWidth <= 768;
        }}
        
        function toggleSidebar() {{
            if (isMobile) {{
                document.getElementById('sidebar').classList.toggle('active');
            }}
        }}
        
        // Загрузка аватара пользователя
        function loadUserAvatar() {{
            const avatar = document.getElementById('user-avatar');
            avatar.textContent = user.slice(0, 2).toUpperCase();
            
            // Загружаем информацию о пользователе
            fetch('/user_info/' + user)
                .then(r => r.json())
                .then(data => {{
                    if (data.success) {{
                        if (data.avatar_path) {{
                            avatar.style.backgroundImage = `url(${{data.avatar_path}})`;
                            avatar.textContent = '';
                        }} else {{
                            avatar.style.backgroundColor = data.avatar_color;
                        }}
                    }}
                }});
        }}
        
        // Поиск
        function performSearch(query) {{
            if (query.length < 2) {{
                document.getElementById('search-results').style.display = 'none';
                return;
            }}
            fetch(`/search_users_channels?q=${{encodeURIComponent(query)}}`)
                .then(r => r.json())
                .then(data => {{
                    if (data.success) {{
                        displaySearchResults(data.results);
                    }}
                }});
        }}
        
        function displaySearchResults(results) {{
            const container = document.getElementById('search-results');
            container.innerHTML = '';
            
            if (results.users && results.users.length > 0) {{
                results.users.forEach(userData => {{
                    if (userData.username !== user) {{
                        const item = document.createElement('div');
                        item.className = 'search-user-item';
                        item.onclick = () => openChat(userData.username, 'private', userData.username);
                        item.innerHTML = `
                            <div class="search-user-avatar" style="background-color: ${{userData.color}};">
                                ${{userData.avatar ? '' : userData.username.slice(0, 2).toUpperCase()}}
                            </div>
                            <div class="search-user-info">
                                <div class="search-user-name">${{userData.username}}</div>
                                <div class="search-user-desc">${{userData.profile_description || 'Пользователь'}}</div>
                            </div>
                        `;
                        if (userData.avatar) {{
                            item.querySelector('.search-user-avatar').style.backgroundImage = `url(${{userData.avatar}})`;
                            item.querySelector('.search-user-avatar').textContent = '';
                        }}
                        container.appendChild(item);
                    }}
                }});
            }}
            
            if (results.channels && results.channels.length > 0) {{
                results.channels.forEach(channel => {{
                    const item = document.createElement('div');
                    item.className = 'search-channel-item';
                    item.onclick = () => openChat(channel.name, 'channel', channel.display_name);
                    item.innerHTML = `
                        <div class="search-channel-avatar">
                            ${{channel.avatar_path ? '' : channel.display_name.slice(0, 2).toUpperCase()}}
                        </div>
                        <div class="search-channel-info">
                            <div class="search-channel-name">${{channel.display_name}}</div>
                            <div class="search-channel-desc">${{channel.description || 'Канал'}}</div>
                        </div>
                    `;
                    container.appendChild(item);
                }});
            }}
            
            if (container.children.length > 0) {{
                container.style.display = 'block';
            }}
        }}
        
        // Загрузка личных чатов
        function loadPersonalChats() {{
            fetch('/personal_chats')
                .then(r => r.json())
                .then(data => {{
                    if (data.success) {{
                        const container = document.getElementById('personal-chats-list');
                        container.innerHTML = '';
                        if (data.chats.length === 0) {{
                            container.innerHTML = '<div style="padding: 12px 16px; color: var(--text-light); font-size: 0.9rem;">Нет личных чатов</div>';
                        }} else {{
                            data.chats.forEach(chatUser => {{
                                const item = document.createElement('a');
                                item.className = 'nav-item';
                                item.href = '#';
                                item.onclick = () => openChat(chatUser, 'private', chatUser);
                                item.innerHTML = `
                                    <i class="fas fa-user"></i>
                                    <span class="nav-item-text">${{chatUser}}</span>
                                `;
                                container.appendChild(item);
                            }});
                        }}
                    }}
                }});
        }}
        
        // Загрузка каналов
        function loadChannels() {{
            fetch('/user_channels')
                .then(r => r.json())
                .then(data => {{
                    if (data.success) {{
                        const container = document.getElementById('channels-list');
                        container.innerHTML = '';
                        data.channels.forEach(channel => {{
                            const item = document.createElement('a');
                            item.className = 'nav-item';
                            item.href = '#';
                            item.onclick = () => openChat(channel.name, 'channel', channel.display_name);
                            item.innerHTML = `
                                <i class="fas fa-hashtag"></i>
                                <span class="nav-item-text">${{channel.display_name}}</span>
                            `;
                            container.appendChild(item);
                        }});
                    }}
                }});
        }}
        
        // Открытие чата
        function openChat(target, type, title) {{
            currentRoom = type === 'channel' ? 'channel_' + target : 'private_' + [user, target].sort().join('_');
            currentRoomType = type;
            currentChannel = target;
            
            // Обновляем заголовок
            document.getElementById('chat-title').textContent = title;
            
            if (type === 'channel') {{
                document.getElementById('chat-header-avatar').className = 'chat-avatar channel-avatar';
                document.getElementById('chat-header-avatar').textContent = title.slice(0, 2).toUpperCase();
                document.getElementById('chat-subtitle').textContent = 'Канал';
                
                // Загружаем информацию о канале
                fetch(`/channel_info/${{target}}`)
                    .then(r => r.json())
                    .then(data => {{
                        if (data.success) {{
                            document.getElementById('chat-subtitle').textContent = data.data.description || 'Канал';
                        }}
                    }});
            }} else {{
                document.getElementById('chat-header-avatar').className = 'chat-avatar';
                document.getElementById('chat-header-avatar').textContent = title.slice(0, 2).toUpperCase();
                document.getElementById('chat-subtitle').innerHTML = '<span class="status-dot"></span> Online';
                
                // Загружаем информацию о пользователе
                fetch(`/user_info/${{target}}`)
                    .then(r => r.json())
                    .then(data => {{
                        if (data.success) {{
                            const avatar = document.getElementById('chat-header-avatar');
                            if (data.avatar_path) {{
                                avatar.style.backgroundImage = `url(${{data.avatar_path}})`;
                                avatar.textContent = '';
                            }} else {{
                                avatar.style.backgroundColor = data.avatar_color;
                            }}
                            document.getElementById('chat-subtitle').innerHTML = `<span class="status-dot"></span> ${{data.online ? 'Online' : 'Offline'}}`;
                        }}
                    }});
            }}
            
            // Показываем поле ввода
            document.getElementById('input-area').style.display = 'block';
            
            // Загружаем сообщения
            loadMessages();
            
            // Подключаемся к комнате
            socket.emit('join', {{ room: currentRoom }});
            
            // Скрываем сайдбар на мобильных
            if (isMobile) {{
                document.getElementById('sidebar').classList.remove('active');
            }}
        }}
        
        // Открытие избранного
        function openFavorites(e) {{
            if (e) e.preventDefault();
            currentRoom = "favorites";
            currentRoomType = "favorites";
            
            document.getElementById('chat-title').textContent = "Все заметки";
            document.getElementById('chat-subtitle').textContent = "Ваши сохраненные материалы";
            document.getElementById('chat-header-avatar').className = 'chat-avatar';
            document.getElementById('chat-header-avatar').innerHTML = '<i class="fas fa-star"></i>';
            document.getElementById('chat-header-avatar').style.background = 'linear-gradient(135deg, var(--primary), var(--secondary))';
            
            // Скрываем поле ввода
            document.getElementById('input-area').style.display = 'none';
            
            // Загружаем избранное
            loadFavorites();
            
            // Скрываем сайдбар на мобильных
            if (isMobile) {{
                document.getElementById('sidebar').classList.remove('active');
            }}
        }}
        
        // Загрузка избранного
        function loadFavorites() {{
            fetch('/get_favorites')
                .then(r => r.json())
                .then(data => {{
                    if (data.success) {{
                        const container = document.getElementById('messages-content');
                        container.innerHTML = '';
                        
                        if (data.favorites.length === 0) {{
                            container.innerHTML = `
                                <div class="empty-state">
                                    <i class="fas fa-star"></i>
                                    <h3>Пока ничего нет</h3>
                                    <p>Добавьте свои заметки, фото или видео</p>
                                    <button onclick="addFavorite()" style="margin-top: 16px; padding: 10px 20px; background: var(--primary); color: white; border: none; border-radius: var(--radius-xs); cursor: pointer;">
                                        <i class="fas fa-plus"></i> Добавить заметку
                                    </button>
                                </div>
                            `;
                        }} else {{
                            const grid = document.createElement('div');
                            grid.className = 'favorites-grid';
                            
                            data.favorites.forEach(favorite => {{
                                const item = document.createElement('div');
                                item.className = 'favorite-item';
                                
                                let content = '';
                                if (favorite.content) {{
                                    content += `<div class="favorite-content">${{favorite.content}}</div>`;
                                }}
                                if (favorite.file_path) {{
                                    if (favorite.file_type === 'image' || favorite.file_name?.match(/\\.(jpg|jpeg|png|gif|webp)$/i)) {{
                                        content += `
                                            <div class="favorite-file">
                                                <img src="${{favorite.file_path}}" alt="${{favorite.file_name}}">
                                            </div>
                                        `;
                                    }} else if (favorite.file_type === 'video' || favorite.file_name?.match(/\\.(mp4|webm|mov)$/i)) {{
                                        content += `
                                            <div class="favorite-file">
                                                <video src="${{favorite.file_path}}" controls></video>
                                            </div>
                                        `;
                                    }}
                                }}
                                
                                const date = new Date(favorite.created_at).toLocaleDateString('ru-RU');
                                const category = favorite.category !== 'general' ? `<span class="category-badge">${{favorite.category}}</span>` : '';
                                
                                item.innerHTML = `
                                    ${{content}}
                                    <div class="favorite-meta">
                                        <span>${{date}}</span>
                                        ${{category}}
                                    </div>
                                `;
                                grid.appendChild(item);
                            }});
                            
                            container.appendChild(grid);
                        }}
                    }}
                }});
        }}
        
        // Загрузка сообщений
        function loadMessages() {{
            fetch(`/get_messages/${{currentRoom}}`)
                .then(r => r.json())
                .then(messages => {{
                    const container = document.getElementById('messages-content');
                    container.innerHTML = '';
                    
                    if (!messages || messages.length === 0) {{
                        container.innerHTML = `
                            <div class="empty-state">
                                <i class="fas fa-comments"></i>
                                <h3>Начните общение</h3>
                                <p>Отправьте первое сообщение в чат</p>
                            </div>
                        `;
                    }} else {{
                        // Группируем сообщения по датам
                        const groupedMessages = {{}};
                        messages.forEach(msg => {{
                            const date = msg.timestamp ? msg.timestamp.split(' ')[0] : new Date().toLocaleDateString();
                            if (!groupedMessages[date]) {{ groupedMessages[date] = []; }}
                            groupedMessages[date].push(msg);
                        }});
                        
                        // Отображаем сообщения
                        Object.entries(groupedMessages).forEach(([date, msgs]) => {{
                            const dateDiv = document.createElement('div');
                            dateDiv.className = 'message-group-date';
                            dateDiv.innerHTML = `<span class="message-date-badge">${{date}}</span>`;
                            container.appendChild(dateDiv);
                            
                            msgs.forEach(msg => {{
                                const messageDiv = document.createElement('div');
                                messageDiv.className = `message ${{msg.user === user ? 'own' : 'other'}}`;
                                messageDiv.innerHTML = `
                                    <div class="message-avatar"></div>
                                    <div class="message-content">
                                        <div class="message-sender">${{msg.user}}</div>
                                        <div class="message-text">${{msg.message || ''}}</div>
                                        ${{msg.file ? `
                                            <div class="message-file">
                                                ${{msg.file.endsWith('.mp4') || msg.file.endsWith('.webm') || msg.file.endsWith('.mov') ? `<video src="${{msg.file}}" controls></video>` : `<img src="${{msg.file}}" alt="${{msg.file_name || 'Файл'}}">`}}
                                            </div>
                                        ` : ''}}
                                        <div class="message-time">${{msg.timestamp || ''}}</div>
                                    </div>
                                `;
                                container.appendChild(messageDiv);
                            }});
                        }});
                    }}
                    
                    // Прокручиваем вниз
                    container.scrollTop = container.scrollHeight;
                }});
        }}
        
        // Отправка сообщения
        function sendMessage() {{
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
                
                fetch('/upload_file', {{
                    method: 'POST',
                    body: formData
                }})
                .then(r => r.json())
                .then(data => {{
                    if (data.success) {{
                        fileData = data.path;
                        fileName = data.filename;
                        fileType = data.file_type;
                        sendSocketMessage(msg, fileData, fileName, fileType);
                    }}
                }});
            }} else {{
                sendSocketMessage(msg);
            }}
            
            input.value = '';
            input.style.height = 'auto';
            fileInput.value = '';
        }}
        
        function sendSocketMessage(msg, file = null, fileName = null, fileType = null) {{
            const messageData = {{
                message: msg,
                room: currentRoom,
                type: currentRoomType
            }};
            if (file) {{
                messageData.file = file;
                messageData.fileName = fileName;
                messageData.fileType = fileType;
            }}
            socket.emit('message', messageData);
        }}
        
        // Socket события
        socket.on('message', (data) => {{
            if (data.room === currentRoom) {{
                addMessage(data);
            }}
        }});
        
        function addMessage(data) {{
            const container = document.getElementById('messages-content');
            
            // Убираем пустое состояние
            const emptyState = container.querySelector('.empty-state');
            if (emptyState) {{ emptyState.remove(); }}
            
            // Добавляем дату если нужно
            const today = new Date().toLocaleDateString();
            const lastDate = container.querySelector('.message-group-date:last-child');
            if (!lastDate || !lastDate.textContent.includes(today)) {{
                const dateDiv = document.createElement('div');
                dateDiv.className = 'message-group-date';
                dateDiv.innerHTML = `<span class="message-date-badge">${{today}}</span>`;
                container.appendChild(dateDiv);
            }}
            
            // Добавляем сообщение
            const messageDiv = document.createElement('div');
            messageDiv.className = `message ${{data.user === user ? 'own' : 'other'}}`;
            messageDiv.innerHTML = `
                <div class="message-avatar"></div>
                <div class="message-content">
                    <div class="message-sender">${{data.user}}</div>
                    <div class="message-text">${{data.message || ''}}</div>
                    ${{data.file ? `
                        <div class="message-file">
                            ${{data.file.endsWith('.mp4') || data.file.endsWith('.webm') || data.file.endsWith('.mov') ? `<video src="${{data.file}}" controls></video>` : `<img src="${{data.file}}" alt="${{data.fileName || 'Файл'}}">`}}
                        </div>
                    ` : ''}}
                    <div class="message-time">${{data.timestamp || new Date().toLocaleTimeString([], {{ hour: '2-digit', minute: '2-digit' }})}}</div>
                </div>
            `;
            container.appendChild(messageDiv);
            container.scrollTop = container.scrollHeight;
        }}
        
        // Эмодзи
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
            const picker = document.getElementById('emoji-container');
            picker.style.display = picker.style.display === 'block' ? 'none' : 'block';
        }}
        
        function insertEmoji(emoji) {{
            const input = document.getElementById('msg-input');
            const start = input.selectionStart;
            const end = input.selectionEnd;
            input.value = input.value.substring(0, start) + emoji + input.value.substring(end);
            input.focus();
            input.selectionStart = input.selectionEnd = start + emoji.length;
        }}
        
        // Модальные окна
        function openUserProfile(username) {{
            fetch(`/user_info/${{username}}`)
                .then(r => r.json())
                .then(data => {{
                    if (data.success) {{
                        document.getElementById('modal-username').textContent = username;
                        document.getElementById('modal-user-description').textContent = data.profile_description || 'Нет описания';
                        const avatar = document.getElementById('modal-user-avatar');
                        if (data.avatar_path) {{
                            avatar.style.backgroundImage = `url(${{data.avatar_path}})`;
                            avatar.textContent = '';
                        }} else {{
                            avatar.style.backgroundColor = data.avatar_color;
                            avatar.textContent = username.slice(0, 2).toUpperCase();
                        }}
                        openModal('profile-modal');
                    }}
                }});
        }}
        
        function openSettings() {{
            openModal('settings-modal');
        }}
        
        function openCreateChannel() {{
            openModal('create-channel-modal');
        }}
        
        function createChannel() {{
            const name = document.getElementById('channel-name').value.trim();
            const description = document.getElementById('channel-description').value.trim();
            
            if (!name) {{
                alert('Введите название канала');
                return;
            }}
            
            // Отправляем запрос на создание канала
            fetch('/create_channel', {{
                method: 'POST',
                headers: {{ 
                    'Content-Type': 'application/json'
                }},
                body: JSON.stringify({{
                    name: name,
                    description: description
                }})
            }})
            .then(r => r.json())
            .then(data => {{
                if (data.success) {{
                    closeModal('create-channel-modal');
                    loadChannels();
                    openChat(data.channel_name, 'channel', data.display_name);
                    document.getElementById('channel-name').value = '';
                    document.getElementById('channel-description').value = '';
                }} else {{
                    alert(data.error || 'Ошибка при создании канала');
                }}
            }})
            .catch(error => {{
                console.error('Error creating channel:', error);
                alert('Ошибка соединения с сервером');
            }});
        }}
        
        function saveSettings() {{
            const theme = document.getElementById('theme-select').value;
            fetch('/set_theme', {{
                method: 'POST',
                headers: {{ 'Content-Type': 'application/json' }},
                body: JSON.stringify({{ theme: theme }})
            }})
            .then(r => r.json())
            .then(data => {{
                if (data.success) {{
                    document.documentElement.setAttribute('data-theme', theme);
                    closeModal('settings-modal');
                }}
            }});
        }}
        
        function openModal(id) {{
            document.getElementById(id).style.display = 'flex';
        }}
        
        function closeModal(id) {{
            document.getElementById(id).style.display = 'none';
        }}
        
        function openSupport() {{
            currentRoom = "support";
            currentRoomType = "support";
            document.getElementById('chat-title').textContent = "Поддержка";
            document.getElementById('chat-subtitle').textContent = "Мы всегда готовы помочь";
            document.getElementById('chat-header-avatar').className = 'chat-avatar';
            document.getElementById('chat-header-avatar').innerHTML = '<i class="fas fa-headset"></i>';
            document.getElementById('chat-header-avatar').style.background = 'linear-gradient(135deg, var(--primary), var(--secondary))';
            document.getElementById('input-area').style.display = 'none';
            
            const container = document.getElementById('messages-content');
            container.innerHTML = `
                <div style="padding: 20px;">
                    <h3 style="margin-bottom: 16px;">Центр поддержки AURA</h3>
                    <div style="background: var(--glass-bg); border: 1px solid var(--glass-border); border-radius: var(--radius); padding: 20px; margin-bottom: 16px;">
                        <h4 style="margin-bottom: 8px;">Частые вопросы</h4>
                        <p style="color: var(--text-light); margin-bottom: 12px;">Здесь вы найдете ответы на самые популярные вопросы о работе AURA Messenger.</p>
                        <button onclick="alert('FAQ будет реализован в будущем')" style="padding: 8px 16px; background: var(--primary); color: white; border: none; border-radius: var(--radius-xs); cursor: pointer;">
                            Открыть FAQ
                        </button>
                    </div>
                    <div style="background: var(--glass-bg); border: 1px solid var(--glass-border); border-radius: var(--radius); padding: 20px;">
                        <h4 style="margin-bottom: 8px;">Связаться с нами</h4>
                        <p style="color: var(--text-light); margin-bottom: 12px;">По всем вопросам и предложениям:</p>
                        <a href="https://vk.com/rsaltyyt" target="_blank" style="color: var(--primary); text-decoration: none;">https://vk.com/rsaltyyt</a>
                    </div>
                </div>
            `;
            
            if (isMobile) {{
                document.getElementById('sidebar').classList.remove('active');
            }}
        }}
        
        function logout() {{
            if (confirm('Вы уверены, что хотите выйти?')) {{
                window.location.href = '/logout';
            }}
        }}
        
        // Закрытие модальных окон и выпадающих списков при клике вне
        document.addEventListener('click', function(event) {{
            // Закрытие поиска
            const searchResults = document.getElementById('search-results');
            const searchInput = document.getElementById('search-input');
            if (searchResults.style.display === 'block' && !searchResults.contains(event.target) && !searchInput.contains(event.target)) {{
                searchResults.style.display = 'none';
            }}
            
            // Закрытие эмодзи пикера
            const emojiContainer = document.getElementById('emoji-container');
            if (emojiContainer.style.display === 'block' && !emojiContainer.contains(event.target) && !event.target.closest('.input-action-btn')) {{
                emojiContainer.style.display = 'none';
            }}
            
            // Закрытие модальных окон
            const modals = document.querySelectorAll('.modal-overlay');
            modals.forEach(modal => {{
                if (modal.style.display === 'flex' && !modal.contains(event.target)) {{
                    modal.style.display = 'none';
                }}
            }});
        }});
        
        document.addEventListener('keydown', function(event) {{
            if (event.key === 'Escape') {{
                document.getElementById('search-results').style.display = 'none';
                document.getElementById('emoji-container').style.display = 'none';
                const modals = document.querySelectorAll('.modal-overlay');
                modals.forEach(modal => modal.style.display = 'none');
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
