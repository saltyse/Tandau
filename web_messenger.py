from flask import Flask, render_template_string, request, session, redirect
from flask_socketio import SocketIO, emit
from datetime import datetime
import json
import os

app = Flask(__name__)
app.config['SECRET_KEY'] = os.urandom(24)
socketio = SocketIO(app, cors_allowed_origins="*")

# Временное хранилище данных
messages = []
users_online = {}

HTML_TEMPLATE = '''
<!DOCTYPE html>
<html lang="ru">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>Glass Chat Messenger</title>
    <link rel="stylesheet" href="https://cdnjs.cloudflare.com/ajax/libs/font-awesome/6.4.0/css/all.min.css">
    <style>
        :root {
            --primary-color: rgba(106, 90, 205, 0.8);
            --secondary-color: rgba(147, 112, 219, 0.7);
            --glass-bg: rgba(255, 255, 255, 0.15);
            --glass-border: rgba(255, 255, 255, 0.2);
            --glass-shadow: 0 8px 32px 0 rgba(31, 38, 135, 0.37);
            --text-primary: #ffffff;
            --text-secondary: rgba(255, 255, 255, 0.8);
            --accent-color: #7b68ee;
            --success-color: #32cd32;
            --danger-color: #ff4757;
        }

        * {
            margin: 0;
            padding: 0;
            box-sizing: border-box;
            font-family: 'Segoe UI', system-ui, -apple-system, sans-serif;
        }

        body {
            background: linear-gradient(135deg, #667eea 0%, #764ba2 100%);
            min-height: 100vh;
            display: flex;
            justify-content: center;
            align-items: center;
            padding: 20px;
        }

        .container {
            width: 100%;
            max-width: 1200px;
            display: flex;
            gap: 20px;
            height: 90vh;
        }

        /* Стиль для боковой панели как в условиях использования */
        .sidebar {
            width: 300px;
            background: rgba(255, 255, 255, 0.1);
            backdrop-filter: blur(15px);
            -webkit-backdrop-filter: blur(15px);
            border-radius: 20px;
            border: 1px solid var(--glass-border);
            box-shadow: var(--glass-shadow);
            padding: 25px;
            overflow-y: auto;
        }

        .user-profile {
            text-align: center;
            margin-bottom: 30px;
            padding-bottom: 20px;
            border-bottom: 1px solid var(--glass-border);
        }

        .avatar {
            width: 80px;
            height: 80px;
            border-radius: 50%;
            background: linear-gradient(135deg, var(--primary-color), var(--secondary-color));
            display: flex;
            align-items: center;
            justify-content: center;
            margin: 0 auto 15px;
            font-size: 32px;
            color: white;
            border: 3px solid rgba(255, 255, 255, 0.3);
        }

        .username {
            font-size: 22px;
            color: var(--text-primary);
            font-weight: 600;
            margin-bottom: 5px;
        }

        .status {
            font-size: 14px;
            color: var(--success-color);
            display: flex;
            align-items: center;
            justify-content: center;
            gap: 5px;
        }

        .status-dot {
            width: 8px;
            height: 8px;
            border-radius: 50%;
            background: var(--success-color);
        }

        .contacts {
            margin-top: 20px;
        }

        .contacts h3 {
            color: var(--text-primary);
            margin-bottom: 15px;
            font-size: 18px;
            font-weight: 500;
        }

        .contact {
            display: flex;
            align-items: center;
            gap: 12px;
            padding: 12px;
            border-radius: 12px;
            margin-bottom: 10px;
            transition: all 0.3s ease;
            cursor: pointer;
            background: rgba(255, 255, 255, 0.05);
        }

        .contact:hover {
            background: rgba(255, 255, 255, 0.15);
        }

        .contact.active {
            background: rgba(123, 104, 238, 0.3);
            border-left: 4px solid var(--accent-color);
        }

        .contact-avatar {
            width: 40px;
            height: 40px;
            border-radius: 50%;
            background: linear-gradient(135deg, #6a5acd, #9370db);
            display: flex;
            align-items: center;
            justify-content: center;
            color: white;
            font-weight: 600;
        }

        .contact-info h4 {
            color: var(--text-primary);
            font-size: 16px;
            margin-bottom: 3px;
        }

        .contact-info p {
            color: var(--text-secondary);
            font-size: 12px;
        }

        /* Основной стиль чата - жидкое стекло */
        .chat-container {
            flex: 1;
            display: flex;
            flex-direction: column;
            background: rgba(255, 255, 255, 0.1);
            backdrop-filter: blur(20px);
            -webkit-backdrop-filter: blur(20px);
            border-radius: 25px;
            border: 1px solid var(--glass-border);
            box-shadow: var(--glass-shadow);
            overflow: hidden;
        }

        .chat-header {
            padding: 20px 30px;
            background: rgba(255, 255, 255, 0.15);
            border-bottom: 1px solid var(--glass-border);
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
            color: var(--text-primary);
            font-size: 22px;
            font-weight: 600;
        }

        .chat-subtitle {
            color: var(--text-secondary);
            font-size: 14px;
        }

        .chat-actions {
            display: flex;
            gap: 15px;
        }

        .icon-btn {
            width: 40px;
            height: 40px;
            border-radius: 50%;
            background: rgba(255, 255, 255, 0.1);
            border: 1px solid var(--glass-border);
            color: var(--text-primary);
            display: flex;
            align-items: center;
            justify-content: center;
            cursor: pointer;
            transition: all 0.3s ease;
        }

        .icon-btn:hover {
            background: rgba(255, 255, 255, 0.2);
            transform: translateY(-2px);
        }

        .messages-container {
            flex: 1;
            padding: 25px;
            overflow-y: auto;
            display: flex;
            flex-direction: column;
            gap: 15px;
        }

        .message {
            max-width: 70%;
            padding: 15px 20px;
            border-radius: 20px;
            position: relative;
            animation: fadeIn 0.3s ease;
        }

        @keyframes fadeIn {
            from { opacity: 0; transform: translateY(10px); }
            to { opacity: 1; transform: translateY(0); }
        }

        .message.sent {
            align-self: flex-end;
            background: linear-gradient(135deg, var(--primary-color), var(--secondary-color));
            border-bottom-right-radius: 5px;
        }

        .message.received {
            align-self: flex-start;
            background: rgba(255, 255, 255, 0.2);
            border: 1px solid var(--glass-border);
            border-bottom-left-radius: 5px;
        }

        .message-header {
            display: flex;
            justify-content: space-between;
            align-items: center;
            margin-bottom: 8px;
        }

        .message-sender {
            font-weight: 600;
            color: var(--text-primary);
            font-size: 14px;
        }

        .message-time {
            color: var(--text-secondary);
            font-size: 12px;
        }

        .message-text {
            color: var(--text-primary);
            line-height: 1.5;
            font-size: 15px;
        }

        .message.received .message-sender {
            color: #e6e6fa;
        }

        .message.received .message-text {
            color: var(--text-primary);
        }

        .input-container {
            padding: 20px 30px;
            background: rgba(255, 255, 255, 0.1);
            border-top: 1px solid var(--glass-border);
        }

        .input-wrapper {
            display: flex;
            gap: 15px;
            align-items: center;
        }

        .message-input {
            flex: 1;
            padding: 15px 25px;
            background: rgba(255, 255, 255, 0.1);
            border: 1px solid var(--glass-border);
            border-radius: 25px;
            color: var(--text-primary);
            font-size: 15px;
            outline: none;
            transition: all 0.3s ease;
        }

        .message-input:focus {
            border-color: rgba(255, 255, 255, 0.4);
            background: rgba(255, 255, 255, 0.15);
        }

        .message-input::placeholder {
            color: rgba(255, 255, 255, 0.6);
        }

        .send-btn {
            width: 50px;
            height: 50px;
            border-radius: 50%;
            background: linear-gradient(135deg, var(--primary-color), var(--secondary-color));
            border: none;
            color: white;
            font-size: 20px;
            cursor: pointer;
            transition: all 0.3s ease;
            display: flex;
            align-items: center;
            justify-content: center;
        }

        .send-btn:hover {
            transform: scale(1.05);
            box-shadow: 0 5px 15px rgba(106, 90, 205, 0.4);
        }

        /* Стиль для блока условий использования */
        .terms-section {
            margin-top: 30px;
            padding: 20px;
            background: rgba(0, 0, 0, 0.2);
            border-radius: 15px;
            border: 1px solid rgba(255, 255, 255, 0.1);
        }

        .terms-title {
            color: var(--text-primary);
            font-size: 16px;
            margin-bottom: 15px;
            display: flex;
            align-items: center;
            gap: 10px;
        }

        .terms-content {
            color: var(--text-secondary);
            font-size: 13px;
            line-height: 1.6;
            max-height: 200px;
            overflow-y: auto;
            padding-right: 10px;
        }

        .terms-content::-webkit-scrollbar {
            width: 5px;
        }

        .terms-content::-webkit-scrollbar-thumb {
            background: rgba(255, 255, 255, 0.3);
            border-radius: 10px;
        }

        /* Адаптивность */
        @media (max-width: 768px) {
            .container {
                flex-direction: column;
                height: auto;
            }
            
            .sidebar {
                width: 100%;
                max-height: 300px;
            }
            
            .message {
                max-width: 85%;
            }
        }

        /* Анимации */
        .pulse {
            animation: pulse 2s infinite;
        }

        @keyframes pulse {
            0% { box-shadow: 0 0 0 0 rgba(123, 104, 238, 0.7); }
            70% { box-shadow: 0 0 0 10px rgba(123, 104, 238, 0); }
            100% { box-shadow: 0 0 0 0 rgba(123, 104, 238, 0); }
        }
    </style>
</head>
<body>
    <div class="container">
        <!-- Боковая панель -->
        <div class="sidebar">
            <div class="user-profile">
                <div class="avatar">
                    <i class="fas fa-user"></i>
                </div>
                <div class="username" id="current-username">Demo User</div>
                <div class="status">
                    <span class="status-dot"></span>
                    <span id="user-status">online</span>
                </div>
            </div>

            <div class="contacts">
                <h3><i class="fas fa-users"></i> Контакты</h3>
                <div class="contact active">
                    <div class="contact-avatar">ГЧ</div>
                    <div class="contact-info">
                        <h4>Групповой чат</h4>
                        <p>Все участники</p>
                    </div>
                </div>
                <div class="contact">
                    <div class="contact-avatar">АК</div>
                    <div class="contact-info">
                        <h4>Анна К.</h4>
                        <p>В сети</p>
                    </div>
                </div>
                <div class="contact">
                    <div class="contact-avatar">МС</div>
                    <div class="contact-info">
                        <h4>Максим С.</h4>
                        <p>Был(а) 5 мин назад</p>
                    </div>
                </div>
                <div class="contact">
                    <div class="contact-avatar">ОИ</div>
                    <div class="contact-info">
                        <h4>Ольга И.</h4>
                        <p>Не в сети</p>
                    </div>
                </div>
            </div>

            <!-- Блок условий использования -->
            <div class="terms-section">
                <div class="terms-title">
                    <i class="fas fa-shield-alt"></i>
                    <span>Правила чата</span>
                </div>
                <div class="terms-content">
                    <p>1. Уважайте других участников общения.</p>
                    <p>2. Запрещены оскорбления и дискриминация.</p>
                    <p>3. Конфиденциальная информация не приветствуется.</p>
                    <p>4. Спам и реклама запрещены.</p>
                    <p>5. Администрация оставляет за собой право модерировать чат.</p>
                    <p>6. Используйте чат по назначению.</p>
                    <p>7. Сообщения сохраняются для улучшения качества сервиса.</p>
                    <p>8. При нарушении правил доступ к чату может быть ограничен.</p>
                </div>
            </div>
        </div>

        <!-- Основной чат -->
        <div class="chat-container">
            <div class="chat-header">
                <div class="chat-info">
                    <div>
                        <div class="chat-title">Групповой чат</div>
                        <div class="chat-subtitle" id="online-count">Участников онлайн: 1</div>
                    </div>
                </div>
                <div class="chat-actions">
                    <div class="icon-btn" title="Настройки">
                        <i class="fas fa-cog"></i>
                    </div>
                    <div class="icon-btn" title="Поиск">
                        <i class="fas fa-search"></i>
                    </div>
                    <div class="icon-btn pulse" title="Новые уведомления">
                        <i class="fas fa-bell"></i>
                    </div>
                </div>
            </div>

            <div class="messages-container" id="messages-container">
                <!-- Сообщения будут здесь -->
                <div class="message received">
                    <div class="message-header">
                        <span class="message-sender">Система</span>
                        <span class="message-time">{{ current_time }}</span>
                    </div>
                    <div class="message-text">
                        Добро пожаловать в чат! Здесь отображаются все сообщения.
                    </div>
                </div>
            </div>

            <div class="input-container">
                <div class="input-wrapper">
                    <div class="icon-btn">
                        <i class="fas fa-paperclip"></i>
                    </div>
                    <input type="text" class="message-input" id="message-input" 
                           placeholder="Напишите сообщение..." autocomplete="off">
                    <button class="send-btn" id="send-btn">
                        <i class="fas fa-paper-plane"></i>
                    </button>
                </div>
            </div>
        </div>
    </div>

    <script src="https://cdnjs.cloudflare.com/ajax/libs/socket.io/4.5.0/socket.io.js"></script>
    <script>
        // Подключение к WebSocket
        const socket = io();
        
        // Получение имени пользователя из сессии
        let username = "{{ username|default('Гость') }}";
        document.getElementById('current-username').textContent = username;
        
        // Элементы DOM
        const messagesContainer = document.getElementById('messages-container');
        const messageInput = document.getElementById('message-input');
        const sendBtn = document.getElementById('send-btn');
        const onlineCount = document.getElementById('online-count');
        
        // Функция добавления сообщения
        function addMessage(message, isSent = false) {
            const messageDiv = document.createElement('div');
            messageDiv.className = `message ${isSent ? 'sent' : 'received'}`;
            
            const time = new Date().toLocaleTimeString([], {hour: '2-digit', minute:'2-digit'});
            
            messageDiv.innerHTML = `
                <div class="message-header">
                    <span class="message-sender">${message.username || 'Неизвестный'}</span>
                    <span class="message-time">${time}</span>
                </div>
                <div class="message-text">${message.text}</div>
            `;
            
            messagesContainer.appendChild(messageDiv);
            messagesContainer.scrollTop = messagesContainer.scrollHeight;
        }
        
        // Отправка сообщения
        function sendMessage() {
            const text = messageInput.value.trim();
            if (text) {
                const message = {
                    text: text,
                    username: username
                };
                
                socket.emit('send_message', message);
                addMessage(message, true);
                messageInput.value = '';
            }
        }
        
        // Обработчики событий
        sendBtn.addEventListener('click', sendMessage);
        
        messageInput.addEventListener('keypress', (e) => {
            if (e.key === 'Enter' && !e.shiftKey) {
                e.preventDefault();
                sendMessage();
            }
        });
        
        // Обработка входящих сообщений
        socket.on('receive_message', (message) => {
            addMessage(message, false);
        });
        
        // Обновление онлайн статуса
        socket.on('update_online_count', (data) => {
            onlineCount.textContent = `Участников онлайн: ${data.count}`;
        });
        
        // Обработка подключения
        socket.on('connect', () => {
            console.log('Подключено к чату');
            socket.emit('user_joined', { username: username });
        });
        
        // Загрузка истории сообщений при подключении
        fetch('/get_messages')
            .then(response => response.json())
            .then(messages => {
                messages.forEach(msg => {
                    addMessage(msg, msg.username === username);
                });
            });
        
        // Добавление иконок для функциональных кнопок
        document.querySelectorAll('.icon-btn').forEach(btn => {
            btn.addEventListener('click', function() {
                const icon = this.querySelector('i');
                if (icon.classList.contains('fa-bell')) {
                    icon.classList.remove('pulse');
                }
                
                // Здесь можно добавить логику для каждой кнопки
                if (icon.classList.contains('fa-search')) {
                    alert('Функция поиска в разработке');
                } else if (icon.classList.contains('fa-cog')) {
                    alert('Настройки будут доступны позже');
                } else if (icon.classList.contains('fa-paperclip')) {
                    alert('Отправка файлов в разработке');
                }
            });
        });
        
        // Имитация новых сообщений (для демо)
        setTimeout(() => {
            addMessage({
                username: "Анна К.",
                text: "Привет всем! Как ваши дела?"
            }, false);
        }, 2000);
        
        setTimeout(() => {
            addMessage({
                username: "Максим С.",
                text: "Привет! Всё отлично, работаю над новым проектом."
            }, false);
        }, 4000);
    </script>
</body>
</html>
'''

@app.route('/')
def index():
    """Главная страница с чатом"""
    username = session.get('username', 'Гость')
    current_time = datetime.now().strftime('%H:%M')
    return render_template_string(HTML_TEMPLATE, 
                                 username=username, 
                                 current_time=current_time)

@app.route('/login')
def login():
    """Страница логина (заглушка)"""
    session['username'] = 'Демо Пользователь'
    return redirect('/')

@app.route('/logout')
def logout():
    """Выход из системы"""
    session.pop('username', None)
    return redirect('/')

@app.route('/get_messages')
def get_messages():
    """Получение истории сообщений"""
    return json.dumps(messages)

@socketio.on('connect')
def handle_connect():
    """Обработка подключения пользователя"""
    print(f'Клиент подключен: {request.sid}')
    
    # Обновляем счетчик онлайн
    emit('update_online_count', {'count': len(users_online)}, broadcast=True)

@socketio.on('disconnect')
def handle_disconnect():
    """Обработка отключения пользователя"""
    if request.sid in users_online:
        del users_online[request.sid]
    
    # Обновляем счетчик онлайн
    emit('update_online_count', {'count': len(users_online)}, broadcast=True)
    print(f'Клиент отключен: {request.sid}')

@socketio.on('user_joined')
def handle_user_joined(data):
    """Обработка присоединения пользователя к чату"""
    username = data.get('username', 'Аноним')
    users_online[request.sid] = username
    
    # Отправляем уведомление о новом пользователе
    notification = {
        'username': 'Система',
        'text': f'{username} присоединился к чату'
    }
    emit('receive_message', notification, broadcast=True)
    
    # Обновляем счетчик онлайн
    emit('update_online_count', {'count': len(users_online)}, broadcast=True)

@socketio.on('send_message')
def handle_send_message(data):
    """Обработка отправки сообщения"""
    message = {
        'username': data.get('username', 'Аноним'),
        'text': data.get('text', ''),
        'timestamp': datetime.now().isoformat()
    }
    
    # Сохраняем сообщение (в реальном приложении - в БД)
    messages.append(message)
    
    # Отправляем сообщение всем подключенным клиентам
    emit('receive_message', message, broadcast=True)

if __name__ == '__main__':
    socketio.run(app, debug=True, host='0.0.0.0', port=5000)
