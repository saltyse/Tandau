from flask import Flask, render_template_string, request, session, redirect
from flask_socketio import SocketIO, emit
from datetime import datetime
import json
import os
import eventlet
eventlet.monkey_patch()

app = Flask(__name__)
app.config['SECRET_KEY'] = os.environ.get('SECRET_KEY', os.urandom(24))
socketio = SocketIO(app, 
                   cors_allowed_origins="*",
                   async_mode='eventlet',
                   logger=True,
                   engineio_logger=False)

# Временное хранилище данных
messages = [
    {
        'username': 'Система',
        'text': 'Добро пожаловать в Glass Chat! Начните общение прямо сейчас.',
        'timestamp': datetime.now().isoformat()
    }
]
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
            --warning-color: #ffa502;
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
            overflow-x: hidden;
        }

        .container {
            width: 100%;
            max-width: 1200px;
            display: flex;
            gap: 20px;
            height: 90vh;
            min-height: 600px;
        }

        /* Стиль для боковой панели */
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
            display: flex;
            flex-direction: column;
        }

        .user-profile {
            text-align: center;
            margin-bottom: 30px;
            padding-bottom: 20px;
            border-bottom: 1px solid var(--glass-border);
            flex-shrink: 0;
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
            transition: transform 0.3s ease;
        }

        .avatar:hover {
            transform: scale(1.05);
        }

        .username {
            font-size: 22px;
            color: var(--text-primary);
            font-weight: 600;
            margin-bottom: 5px;
            overflow: hidden;
            text-overflow: ellipsis;
            white-space: nowrap;
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
            animation: pulse 2s infinite;
        }

        .contacts {
            margin-top: 20px;
            flex-shrink: 0;
        }

        .contacts h3 {
            color: var(--text-primary);
            margin-bottom: 15px;
            font-size: 18px;
            font-weight: 500;
            display: flex;
            align-items: center;
            gap: 10px;
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
            border: 1px solid transparent;
        }

        .contact:hover {
            background: rgba(255, 255, 255, 0.15);
            border-color: rgba(255, 255, 255, 0.2);
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
            flex-shrink: 0;
        }

        .contact-info {
            flex: 1;
            min-width: 0;
        }

        .contact-info h4 {
            color: var(--text-primary);
            font-size: 16px;
            margin-bottom: 3px;
            overflow: hidden;
            text-overflow: ellipsis;
            white-space: nowrap;
        }

        .contact-info p {
            color: var(--text-secondary);
            font-size: 12px;
            overflow: hidden;
            text-overflow: ellipsis;
            white-space: nowrap;
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
            min-width: 0;
        }

        .chat-header {
            padding: 20px 30px;
            background: rgba(255, 255, 255, 0.15);
            border-bottom: 1px solid var(--glass-border);
            display: flex;
            align-items: center;
            justify-content: space-between;
            flex-shrink: 0;
        }

        .chat-info {
            display: flex;
            align-items: center;
            gap: 15px;
            min-width: 0;
        }

        .chat-title {
            color: var(--text-primary);
            font-size: 22px;
            font-weight: 600;
            overflow: hidden;
            text-overflow: ellipsis;
            white-space: nowrap;
        }

        .chat-subtitle {
            color: var(--text-secondary);
            font-size: 14px;
            overflow: hidden;
            text-overflow: ellipsis;
            white-space: nowrap;
        }

        .chat-actions {
            display: flex;
            gap: 15px;
            flex-shrink: 0;
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
            flex-shrink: 0;
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
            min-height: 0;
        }

        .message {
            max-width: 70%;
            padding: 15px 20px;
            border-radius: 20px;
            position: relative;
            animation: fadeIn 0.3s ease;
            word-wrap: break-word;
            overflow-wrap: break-word;
        }

        @keyframes fadeIn {
            from { opacity: 0; transform: translateY(10px); }
            to { opacity: 1; transform: translateY(0); }
        }

        @keyframes pulse {
            0% { opacity: 1; }
            50% { opacity: 0.5; }
            100% { opacity: 1; }
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
            flex-wrap: wrap;
            gap: 10px;
        }

        .message-sender {
            font-weight: 600;
            color: var(--text-primary);
            font-size: 14px;
            overflow: hidden;
            text-overflow: ellipsis;
            white-space: nowrap;
        }

        .message-time {
            color: var(--text-secondary);
            font-size: 12px;
            flex-shrink: 0;
        }

        .message-text {
            color: var(--text-primary);
            line-height: 1.5;
            font-size: 15px;
        }

        .message.received .message-sender {
            color: #e6e6fa;
        }

        .input-container {
            padding: 20px 30px;
            background: rgba(255, 255, 255, 0.1);
            border-top: 1px solid var(--glass-border);
            flex-shrink: 0;
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
            min-width: 0;
        }

        .message-input:focus {
            border-color: rgba(255, 255, 255, 0.4);
            background: rgba(255, 255, 255, 0.15);
            box-shadow: 0 0 0 3px rgba(123, 104, 238, 0.2);
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
            flex-shrink: 0;
        }

        .send-btn:hover {
            transform: scale(1.05);
            box-shadow: 0 5px 15px rgba(106, 90, 205, 0.4);
        }

        .send-btn:active {
            transform: scale(0.95);
        }

        /* Стиль для блока условий использования */
        .terms-section {
            margin-top: auto;
            padding: 20px;
            background: rgba(0, 0, 0, 0.2);
            border-radius: 15px;
            border: 1px solid rgba(255, 255, 255, 0.1);
            flex-shrink: 0;
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
            max-height: 150px;
            overflow-y: auto;
            padding-right: 10px;
        }

        .terms-content p {
            margin-bottom: 8px;
            padding-left: 10px;
            position: relative;
        }

        .terms-content p:before {
            content: "•";
            position: absolute;
            left: 0;
            color: var(--accent-color);
        }

        .terms-content::-webkit-scrollbar {
            width: 5px;
        }

        .terms-content::-webkit-scrollbar-thumb {
            background: rgba(255, 255, 255, 0.3);
            border-radius: 10px;
        }

        /* Адаптивность */
        @media (max-width: 992px) {
            .container {
                flex-direction: column;
                height: auto;
                min-height: 100vh;
            }
            
            .sidebar {
                width: 100%;
                max-height: 400px;
                order: 2;
            }
            
            .chat-container {
                order: 1;
                min-height: 500px;
            }
            
            .message {
                max-width: 85%;
            }
            
            .contacts {
                display: grid;
                grid-template-columns: repeat(auto-fill, minmax(200px, 1fr));
                gap: 10px;
            }
            
            .contact {
                margin-bottom: 0;
            }
        }

        @media (max-width: 576px) {
            .container {
                padding: 10px;
                gap: 10px;
            }
            
            .chat-header {
                padding: 15px;
                flex-wrap: wrap;
                gap: 10px;
            }
            
            .chat-info {
                width: 100%;
                justify-content: space-between;
            }
            
            .chat-actions {
                width: 100%;
                justify-content: center;
            }
            
            .input-container {
                padding: 15px;
            }
            
            .input-wrapper {
                gap: 10px;
            }
            
            .message-input {
                padding: 12px 20px;
            }
            
            .messages-container {
                padding: 15px;
            }
            
            .sidebar {
                padding: 15px;
            }
        }

        /* Уведомления */
        .notification {
            position: fixed;
            top: 20px;
            right: 20px;
            padding: 15px 25px;
            background: rgba(255, 255, 255, 0.95);
            border-radius: 10px;
            border-left: 4px solid var(--success-color);
            box-shadow: 0 5px 15px rgba(0, 0, 0, 0.2);
            transform: translateX(150%);
            transition: transform 0.3s ease;
            z-index: 1000;
            max-width: 300px;
        }

        .notification.show {
            transform: translateX(0);
        }

        .notification-title {
            font-weight: 600;
            color: #333;
            margin-bottom: 5px;
        }

        .notification-message {
            color: #666;
            font-size: 14px;
        }

        /* Загрузка */
        .loading {
            display: none;
            text-align: center;
            padding: 20px;
            color: var(--text-secondary);
        }

        .loading.show {
            display: block;
        }

        .typing-indicator {
            display: flex;
            align-items: center;
            gap: 5px;
            padding: 10px 20px;
            background: rgba(255, 255, 255, 0.1);
            border-radius: 20px;
            width: fit-content;
            margin: 5px 0;
        }

        .typing-dot {
            width: 8px;
            height: 8px;
            border-radius: 50%;
            background: var(--text-secondary);
            animation: typing 1.4s infinite;
        }

        .typing-dot:nth-child(2) {
            animation-delay: 0.2s;
        }

        .typing-dot:nth-child(3) {
            animation-delay: 0.4s;
        }

        @keyframes typing {
            0%, 60%, 100% {
                transform: translateY(0);
            }
            30% {
                transform: translateY(-10px);
            }
        }

        /* Скроллбар */
        .messages-container::-webkit-scrollbar {
            width: 8px;
        }

        .messages-container::-webkit-scrollbar-track {
            background: rgba(255, 255, 255, 0.1);
            border-radius: 4px;
        }

        .messages-container::-webkit-scrollbar-thumb {
            background: rgba(255, 255, 255, 0.3);
            border-radius: 4px;
        }

        .messages-container::-webkit-scrollbar-thumb:hover {
            background: rgba(255, 255, 255, 0.4);
        }
    </style>
</head>
<body>
    <!-- Уведомление -->
    <div class="notification" id="notification">
        <div class="notification-title" id="notification-title"></div>
        <div class="notification-message" id="notification-message"></div>
    </div>

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
                <h3><i class="fas fa-users"></i> Контакты <span class="online-count" id="contacts-online">(1 онлайн)</span></h3>
                <div class="contact active" data-chat="general">
                    <div class="contact-avatar"><i class="fas fa-comments"></i></div>
                    <div class="contact-info">
                        <h4>Общий чат</h4>
                        <p>Все участники</p>
                    </div>
                </div>
                <div class="contact" data-chat="anna">
                    <div class="contact-avatar">АК</div>
                    <div class="contact-info">
                        <h4>Анна К.</h4>
                        <p class="contact-status">В сети</p>
                    </div>
                </div>
                <div class="contact" data-chat="maxim">
                    <div class="contact-avatar">МС</div>
                    <div class="contact-info">
                        <h4>Максим С.</h4>
                        <p class="contact-status">Был(а) недавно</p>
                    </div>
                </div>
                <div class="contact" data-chat="olga">
                    <div class="contact-avatar">ОИ</div>
                    <div class="contact-info">
                        <h4>Ольга И.</h4>
                        <p class="contact-status">Не в сети</p>
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
                    <p>Уважайте других участников общения.</p>
                    <p>Запрещены оскорбления и дискриминация.</p>
                    <p>Конфиденциальная информация не приветствуется.</p>
                    <p>Спам и реклама запрещены.</p>
                    <p>Администрация модерирует чат.</p>
                    <p>Используйте чат по назначению.</p>
                </div>
            </div>
        </div>

        <!-- Основной чат -->
        <div class="chat-container">
            <div class="chat-header">
                <div class="chat-info">
                    <div>
                        <div class="chat-title" id="chat-title">Общий чат</div>
                        <div class="chat-subtitle" id="chat-subtitle">
                            <span id="online-count">1 участник онлайн</span>
                            <span id="typing-indicator" style="display: none; margin-left: 10px;">
                                <span class="typing-dot"></span>
                                <span class="typing-dot"></span>
                                <span class="typing-dot"></span>
                            </span>
                        </div>
                    </div>
                </div>
                <div class="chat-actions">
                    <div class="icon-btn" title="Настройки" id="settings-btn">
                        <i class="fas fa-cog"></i>
                    </div>
                    <div class="icon-btn" title="Поиск" id="search-btn">
                        <i class="fas fa-search"></i>
                    </div>
                    <div class="icon-btn" title="Уведомления" id="notifications-btn">
                        <i class="fas fa-bell"></i>
                        <span class="badge" style="display: none;">3</span>
                    </div>
                </div>
            </div>

            <div class="messages-container" id="messages-container">
                <!-- Сообщения загружаются здесь -->
                <div class="loading" id="loading">
                    <i class="fas fa-spinner fa-spin"></i> Загрузка сообщений...
                </div>
            </div>

            <div class="input-container">
                <div class="input-wrapper">
                    <div class="icon-btn" id="attach-btn" title="Прикрепить файл">
                        <i class="fas fa-paperclip"></i>
                    </div>
                    <input type="text" class="message-input" id="message-input" 
                           placeholder="Напишите сообщение..." autocomplete="off"
                           autofocus>
                    <button class="send-btn" id="send-btn" title="Отправить">
                        <i class="fas fa-paper-plane"></i>
                    </button>
                </div>
            </div>
        </div>
    </div>

    <script src="https://cdnjs.cloudflare.com/ajax/libs/socket.io/4.5.0/socket.io.js"></script>
    <script>
        // Инициализация
        document.addEventListener('DOMContentLoaded', function() {
            // Подключение к WebSocket
            const socket = io();
            
            // Получение имени пользователя
            let username = "{{ username|default('Гость') }}";
            document.getElementById('current-username').textContent = username;
            
            // Элементы DOM
            const messagesContainer = document.getElementById('messages-container');
            const messageInput = document.getElementById('message-input');
            const sendBtn = document.getElementById('send-btn');
            const onlineCount = document.getElementById('online-count');
            const loading = document.getElementById('loading');
            const contactsOnline = document.getElementById('contacts-online');
            const typingIndicator = document.getElementById('typing-indicator');
            const notification = document.getElementById('notification');
            
            let isTyping = false;
            let typingTimeout;
            let currentChat = 'general';
            
            // Функция показа уведомления
            function showNotification(title, message, type = 'info') {
                const notificationTitle = document.getElementById('notification-title');
                const notificationMessage = document.getElementById('notification-message');
                
                notificationTitle.textContent = title;
                notificationMessage.textContent = message;
                
                // Цвет в зависимости от типа
                const colors = {
                    'info': '#7b68ee',
                    'success': '#32cd32',
                    'warning': '#ffa502',
                    'error': '#ff4757'
                };
                
                notification.style.borderLeftColor = colors[type] || colors.info;
                notification.classList.add('show');
                
                setTimeout(() => {
                    notification.classList.remove('show');
                }, 3000);
            }
            
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
                
                // Показать уведомление для новых сообщений (кроме своих)
                if (!isSent && document.hidden) {
                    showNotification(message.username, message.text, 'info');
                }
            }
            
            // Функция отправки сообщения
            function sendMessage() {
                const text = messageInput.value.trim();
                if (text) {
                    const message = {
                        text: text,
                        username: username,
                        chat: currentChat
                    };
                    
                    socket.emit('send_message', message);
                    addMessage(message, true);
                    messageInput.value = '';
                    
                    // Сброс индикатора набора
                    socket.emit('typing', false);
                    isTyping = false;
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
            
            // Индикатор набора текста
            messageInput.addEventListener('input', () => {
                if (!isTyping) {
                    isTyping = true;
                    socket.emit('typing', true);
                }
                
                clearTimeout(typingTimeout);
                typingTimeout = setTimeout(() => {
                    if (isTyping) {
                        isTyping = false;
                        socket.emit('typing', false);
                    }
                }, 1000);
            });
            
            // Переключение чатов
            document.querySelectorAll('.contact').forEach(contact => {
                contact.addEventListener('click', function() {
                    document.querySelectorAll('.contact').forEach(c => c.classList.remove('active'));
                    this.classList.add('active');
                    
                    currentChat = this.dataset.chat;
                    const chatName = this.querySelector('h4').textContent;
                    document.getElementById('chat-title').textContent = chatName;
                    
                    // Загрузка истории для выбранного чата
                    loadChatHistory(currentChat);
                });
            });
            
            // Загрузка истории чата
            function loadChatHistory(chatId) {
                loading.classList.add('show');
                messagesContainer.innerHTML = '';
                messagesContainer.appendChild(loading);
                
                fetch(`/get_messages/${chatId}`)
                    .then(response => response.json())
                    .then(messages => {
                        loading.classList.remove('show');
                        messages.forEach(msg => {
                            addMessage(msg, msg.username === username);
                        });
                        
                        if (messages.length === 0) {
                            const emptyMessage = document.createElement('div');
                            emptyMessage.className = 'message received';
                            emptyMessage.innerHTML = `
                                <div class="message-header">
                                    <span class="message-sender">Система</span>
                                    <span class="message-time">${new Date().toLocaleTimeString([], {hour: '2-digit', minute:'2-digit'})}</span>
                                </div>
                                <div class="message-text">
                                    Чат "${document.getElementById('chat-title').textContent}" создан. Начните общение!
                                </div>
                            `;
                            messagesContainer.appendChild(emptyMessage);
                        }
                    })
                    .catch(error => {
                        console.error('Ошибка загрузки истории:', error);
                        loading.classList.remove('show');
                        showNotification('Ошибка', 'Не удалось загрузить историю чата', 'error');
                    });
            }
            
            // Кнопки действий
            document.getElementById('settings-btn').addEventListener('click', () => {
                showNotification('Настройки', 'Раздел настроек в разработке', 'info');
            });
            
            document.getElementById('search-btn').addEventListener('click', () => {
                const searchTerm = prompt('Введите текст для поиска:');
                if (searchTerm) {
                    showNotification('Поиск', `Поиск: "${searchTerm}"`, 'info');
                }
            });
            
            document.getElementById('notifications-btn').addEventListener('click', () => {
                showNotification('Уведомления', 'Новых уведомлений нет', 'info');
            });
            
            document.getElementById('attach-btn').addEventListener('click', () => {
                showNotification('Вложение', 'Функция отправки файлов в разработке', 'info');
            });
            
            // Обработка входящих сообщений
            socket.on('receive_message', (message) => {
                if (message.chat === currentChat) {
                    addMessage(message, message.username === username);
                }
            });
            
            // Обновление онлайн статуса
            socket.on('update_online_count', (data) => {
                onlineCount.textContent = `${data.count} участник${data.count % 10 == 1 ? '' : 'а'} онлайн`;
                contactsOnline.textContent = `(${data.count} онлайн)`;
            });
            
            // Индикатор набора текста
            socket.on('user_typing', (data) => {
                if (data.username !== username && data.chat === currentChat) {
                    if (data.isTyping) {
                        typingIndicator.style.display = 'inline-flex';
                        typingIndicator.previousElementSibling.style.display = 'none';
                    } else {
                        typingIndicator.style.display = 'none';
                        typingIndicator.previousElementSibling.style.display = 'inline';
                    }
                }
            });
            
            // Обработка подключения
            socket.on('connect', () => {
                console.log('Подключено к чату');
                socket.emit('user_joined', { 
                    username: username,
                    chat: currentChat 
                });
                
                showNotification('Подключено', 'Вы успешно подключились к чату', 'success');
            });
            
            socket.on('disconnect', () => {
                showNotification('Отключено', 'Соединение потеряно. Переподключение...', 'warning');
            });
            
            socket.on('connect_error', () => {
                showNotification('Ошибка', 'Не удалось подключиться к серверу', 'error');
            });
            
            // Загрузка начальной истории
            loadChatHistory(currentChat);
            
            // Обновление статуса вкладки
            document.addEventListener('visibilitychange', function() {
                if (!document.hidden) {
                    document.title = 'Glass Chat';
                }
            });
            
            // Фокус на поле ввода
            messageInput.focus();
        });
    </script>
</body>
</html>
'''

@app.route('/')
def index():
    """Главная страница с чатом"""
    username = session.get('username', 'Гость')
    return render_template_string(HTML_TEMPLATE, username=username)

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

@app.route('/get_messages/<chat_id>')
def get_messages(chat_id):
    """Получение истории сообщений для конкретного чата"""
    # Фильтруем сообщения по чату (в демо возвращаем все)
    filtered_messages = [msg for msg in messages if msg.get('chat') == chat_id or not msg.get('chat')]
    return json.dumps(filtered_messages)

@socketio.on('connect')
def handle_connect():
    """Обработка подключения пользователя"""
    print(f'Клиент подключен: {request.sid}')
    emit('update_online_count', {'count': len(users_online)}, broadcast=True)

@socketio.on('disconnect')
def handle_disconnect():
    """Обработка отключения пользователя"""
    if request.sid in users_online:
        username = users_online[request.sid]
        del users_online[request.sid]
        
        # Отправляем уведомление о выходе
        notification = {
            'username': 'Система',
            'text': f'{username} покинул(а) чат',
            'chat': 'general'
        }
        emit('receive_message', notification, broadcast=True)
    
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
        'text': f'{username} присоединился(ась) к чату',
        'chat': data.get('chat', 'general')
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
        'chat': data.get('chat', 'general'),
        'timestamp': datetime.now().isoformat()
    }
    
    # Сохраняем сообщение
    messages.append(message)
    
    # Ограничиваем количество сообщений в памяти
    if len(messages) > 100:
        messages.pop(0)
    
    # Отправляем сообщение всем подключенным клиентам
    emit('receive_message', message, broadcast=True)

@socketio.on('typing')
def handle_typing(data):
    """Обработка индикатора набора текста"""
    username = users_online.get(request.sid, 'Аноним')
    typing_data = {
        'username': username,
        'isTyping': data,
        'chat': 'general'  # В расширенной версии можно передавать конкретный чат
    }
    emit('user_typing', typing_data, broadcast=True)

if __name__ == '__main__':
    # Для разработки
    if os.environ.get('RENDER'):
        # На Render используем production настройки
        socketio.run(app, 
                    host='0.0.0.0',
                    port=int(os.environ.get('PORT', 5000)),
                    debug=False,
                    log_output=True)
    else:
        # Для локальной разработки
        socketio.run(app, 
                    debug=True, 
                    host='0.0.0.0', 
                    port=5000,
                    allow_unsafe_werkzeug=True)from flask import Flask, render_template_string, request, session, redirect
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

