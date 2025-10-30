# tandau.py — Современный мессенджер Tandau
import tkinter as tk
from tkinter import ttk, messagebox, scrolledtext, filedialog
from PIL import Image, ImageTk, ImageOps, ImageDraw
import json
import hashlib
import os
import base64
import io
from datetime import datetime
import threading
import socket
import cv2
import numpy as np
import subprocess
import platform
import shutil
import time
import pyaudio
import wave
import tempfile
import requests
from urllib.parse import urlparse

# === КОНФИГУРАЦИЯ ===
class Config:
    SERVER_HOST = "72.44.48.182"
    SERVER_PORT = 5555
    THEME = {
        'primary': '#6366F1',
        'primary_dark': '#4F46E5',
        'primary_light': '#8B5CF6',
        'secondary': '#10B981',
        'accent': '#F59E0B',
        'danger': '#EF4444',
        'success': '#10B981',
        'warning': '#F59E0B',
        'background': '#0F0F1A',
        'surface': '#1A1B2E',
        'card': '#252642',
        'text_primary': '#FFFFFF',
        'text_secondary': '#A0A0B8',
        'text_light': '#6B6B8B',
        'border': '#373755',
        'white': '#FFFFFF',
        'gradient_start': '#6366F1',
        'gradient_end': '#8B5CF6'
    }
    FONTS = {
        'h1': ('Segoe UI', 32, 'bold'),
        'h2': ('Segoe UI', 24, 'bold'),
        'h3': ('Segoe UI', 20, 'bold'),
        'body_large': ('Segoe UI', 16),
        'body': ('Segoe UI', 14),
        'body_small': ('Segoe UI', 12),
        'caption': ('Segoe UI', 11)
    }

class GradientFrame(tk.Canvas):
    def __init__(self, parent, colors=[Config.THEME['gradient_start'], Config.THEME['gradient_end']], **kwargs):
        super().__init__(parent, **kwargs)
        self.colors = colors
        self.bind("<Configure>", self._draw_gradient)
    
    def _draw_gradient(self, event=None):
        self.delete("gradient")
        width = self.winfo_width()
        height = self.winfo_height()
        
        if width <= 1 or height <= 1:
            return
            
        for i in range(width):
            ratio = i / width
            r = int(int(self.colors[0][1:3], 16) * (1 - ratio) + int(self.colors[1][1:3], 16) * ratio)
            g = int(int(self.colors[0][3:5], 16) * (1 - ratio) + int(self.colors[1][3:5], 16) * ratio)
            b = int(int(self.colors[0][5:7], 16) * (1 - ratio) + int(self.colors[1][5:7], 16) * ratio)
            color = f"#{r:02x}{g:02x}{b:02x}"
            self.create_line(i, 0, i, height, tags=("gradient",), fill=color)

class ModernButton(tk.Canvas):
    def __init__(self, parent, text, command=None, width=120, height=40, 
                 bg_color=Config.THEME['primary'], text_color='white', 
                 corner_radius=15, font=Config.FONTS['body'], 
                 hover_effect=True, **kwargs):
        super().__init__(parent, width=width, height=height, 
                        highlightthickness=0, **kwargs)
        self.command = command
        self.bg_color = bg_color
        self.text_color = text_color
        self.corner_radius = corner_radius
        self.font = font
        self.text = text
        self.hover_effect = hover_effect
        self.is_hovered = False
        
        self.bind("<Button-1>", self._on_click)
        self.bind("<Enter>", self._on_enter)
        self.bind("<Leave>", self._on_leave)
        
        self.draw_button()
    
    def draw_button(self, hover=False):
        self.delete("all")
        self.is_hovered = hover
        
        if hover and self.hover_effect:
            color = self._lighten_color(self.bg_color, 20)
            shadow_color = self._lighten_color(self.bg_color, -20)
        else:
            color = self.bg_color
            shadow_color = self._lighten_color(self.bg_color, -30)
        
        # Тень
        if self.hover_effect:
            self.create_rounded_rect(3, 3, self.winfo_width()+3, self.winfo_height()+3, 
                                   self.corner_radius, fill=shadow_color, outline="")
        
        # Основная кнопка
        self.create_rounded_rect(0, 0, self.winfo_width(), self.winfo_height(), 
                               self.corner_radius, fill=color, outline="")
        
        # Текст
        self.create_text(self.winfo_width()//2, self.winfo_height()//2,
                        text=self.text, fill=self.text_color, font=self.font)
    
    def create_rounded_rect(self, x1, y1, x2, y2, radius, **kwargs):
        points = [x1+radius, y1,
                 x2-radius, y1,
                 x2, y1,
                 x2, y1+radius,
                 x2, y2-radius,
                 x2, y2,
                 x2-radius, y2,
                 x1+radius, y2,
                 x1, y2,
                 x1, y2-radius,
                 x1, y1+radius,
                 x1, y1]
        return self.create_polygon(points, **kwargs, smooth=True)
    
    def _lighten_color(self, color, amount=10):
        """Осветлить или затемнить цвет"""
        color = color.lstrip('#')
        rgb = tuple(int(color[i:i+2], 16) for i in (0, 2, 4))
        
        new_rgb = []
        for channel in rgb:
            new_channel = max(0, min(255, channel + amount))
            new_rgb.append(new_channel)
        
        return f"#{new_rgb[0]:02x}{new_rgb[1]:02x}{new_rgb[2]:02x}"
    
    def _on_click(self, event):
        # Анимация нажатия
        self.draw_button(hover=False)
        self.after(100, lambda: self.draw_button(hover=self.is_hovered))
        
        if self.command:
            self.after(150, self.command)
    
    def _on_enter(self, event):
        self.draw_button(hover=True)
    
    def _on_leave(self, event):
        self.draw_button(hover=False)

class ModernEntry(tk.Frame):
    def __init__(self, parent, placeholder="", width=300, height=50, 
                 show=None, font=Config.FONTS['body'], icon=None, **kwargs):
        super().__init__(parent, **kwargs)
        self.configure(bg=Config.THEME['background'])
        
        self.placeholder = placeholder
        self.show = show
        self.font = font
        self.icon = icon
        self.is_focused = False
        
        # Создаем canvas для красивого бордера
        self.canvas = tk.Canvas(self, width=width, height=height, 
                               highlightthickness=0, bg=Config.THEME['background'])
        self.canvas.pack()
        
        # Иконка если есть
        self.icon_label = None
        if icon:
            self.icon_label = tk.Label(self.canvas, text=icon, font=('Segoe UI', 14),
                                     fg=Config.THEME['text_light'], bg=Config.THEME['background'])
            self.icon_window = self.canvas.create_window(15, height//2, 
                                                       window=self.icon_label, anchor='w')
        
        # Поле ввода
        entry_x = 45 if icon else 15
        self.entry = tk.Entry(self, font=font, relief='flat', bd=0, 
                             bg=Config.THEME['card'], fg=Config.THEME['text_primary'],
                             insertbackground=Config.THEME['text_primary'],
                             selectbackground=Config.THEME['primary_light'],
                             show=show)
        self.entry_window = self.canvas.create_window(entry_x, height//2, 
                                                    window=self.entry, anchor='w',
                                                    width=width-entry_x-15)
        
        self.entry.bind('<FocusIn>', self._on_focus_in)
        self.entry.bind('<FocusOut>', self._on_focus_out)
        self.entry.bind('<KeyRelease>', self._on_key_release)
        
        self.draw_border()
        self.show_placeholder()
    
    def draw_border(self, focused=False):
        self.canvas.delete("border")
        self.is_focused = focused
        
        if focused:
            color = Config.THEME['primary']
            width = 2
            # Анимация фокуса
            if self.icon_label:
                self.icon_label.config(fg=Config.THEME['primary'])
        else:
            color = Config.THEME['border']
            width = 1
            if self.icon_label:
                self.icon_label.config(fg=Config.THEME['text_light'])
        
        # Создаем скругленный прямоугольник для бордера
        self.create_rounded_rect(2, 2, self.canvas.winfo_width()-2, 
                                      self.canvas.winfo_height()-2, 12,
                                      outline=color, width=width, tags="border")
    
    def create_rounded_rect(self, x1, y1, x2, y2, radius, **kwargs):
        points = [x1+radius, y1,
                 x2-radius, y1,
                 x2, y1,
                 x2, y1+radius,
                 x2, y2-radius,
                 x2, y2,
                 x2-radius, y2,
                 x1+radius, y2,
                 x1, y2,
                 x1, y2-radius,
                 x1, y1+radius,
                 x1, y1]
        return self.canvas.create_polygon(points, **kwargs, smooth=True)
    
    def show_placeholder(self):
        if not self.entry.get() and self.placeholder:
            self.entry.insert(0, self.placeholder)
            self.entry.config(fg=Config.THEME['text_light'])
    
    def hide_placeholder(self):
        if self.entry.get() == self.placeholder:
            self.entry.delete(0, tk.END)
            self.entry.config(fg=Config.THEME['text_primary'])
    
    def _on_focus_in(self, event):
        self.hide_placeholder()
        self.draw_border(focused=True)
    
    def _on_focus_out(self, event):
        self.show_placeholder()
        self.draw_border(focused=False)
    
    def _on_key_release(self, event):
        pass
    
    def get(self):
        text = self.entry.get()
        return "" if text == self.placeholder else text

class ConnectionDialog:
    def __init__(self, parent, app):
        self.parent = parent
        self.app = app
        self.dialog = None
        self.create_dialog()
    
    def create_dialog(self):
        self.dialog = tk.Toplevel(self.parent)
        self.dialog.title("Подключение к серверу")
        self.dialog.geometry("450x350")
        self.dialog.configure(bg=Config.THEME['surface'])
        self.dialog.resizable(False, False)
        self.dialog.transient(self.parent)
        self.dialog.grab_set()
        
        # Центрирование
        self.dialog.geometry("+%d+%d" % (
            self.parent.winfo_rootx() + self.parent.winfo_width()//2 - 225,
            self.parent.winfo_rooty() + self.parent.winfo_height()//2 - 175
        ))
        
        # Заголовок с градиентом
        header = GradientFrame(self.dialog, height=80, colors=[Config.THEME['primary'], Config.THEME['primary_light']])
        header.pack(fill='x')
        
        title = tk.Label(header, text="Настройки подключения", 
                        font=Config.FONTS['h3'], fg=Config.THEME['white'],
                        bg=Config.THEME['primary'])
        title.place(relx=0.5, rely=0.5, anchor='center')
        
        # Основной контент
        content = tk.Frame(self.dialog, bg=Config.THEME['surface'], padx=30, pady=30)
        content.pack(fill='both', expand=True)
        
        # Поля ввода
        tk.Label(content, text="Адрес сервера", font=Config.FONTS['body_small'],
                bg=Config.THEME['surface'], fg=Config.THEME['text_secondary'],
                anchor='w').pack(fill='x', pady=(5, 2))
        
        self.host_entry = ModernEntry(content, placeholder="72.44.48.182", 
                                     width=380, height=50, icon="🌐")
        self.host_entry.pack(pady=(0, 20))
        self.host_entry.entry.insert(0, Config.SERVER_HOST)
        self.host_entry.hide_placeholder()
        
        tk.Label(content, text="Порт", font=Config.FONTS['body_small'],
                bg=Config.THEME['surface'], fg=Config.THEME['text_secondary'],
                anchor='w').pack(fill='x', pady=(5, 2))
        
        self.port_entry = ModernEntry(content, placeholder="5555", 
                                     width=380, height=50, icon="🔌")
        self.port_entry.pack(pady=(0, 30))
        self.port_entry.entry.insert(0, str(Config.SERVER_PORT))
        self.port_entry.hide_placeholder()
        
        # Кнопки
        button_frame = tk.Frame(content, bg=Config.THEME['surface'])
        button_frame.pack(pady=20)
        
        ModernButton(button_frame, "Подключить", command=self.test_connection,
                    width=160, height=50, bg_color=Config.THEME['primary']).pack(side=tk.LEFT, padx=10)
        
        ModernButton(button_frame, "Отмена", command=self.dialog.destroy,
                    width=120, height=50, bg_color=Config.THEME['text_light']).pack(side=tk.LEFT, padx=10)
    
    def test_connection(self):
        host = self.host_entry.get()
        port = self.port_entry.get()
        
        try:
            port = int(port)
            # Тестируем подключение
            test_socket = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            test_socket.settimeout(5)
            test_socket.connect((host, port))
            test_socket.close()
            
            # Обновляем конфигурацию
            Config.SERVER_HOST = host
            Config.SERVER_PORT = port
            
            messagebox.showinfo("Успех", "Подключение установлено успешно!")
            self.dialog.destroy()
            
        except Exception as e:
            messagebox.showerror("Ошибка", f"Не удалось подключиться:\n{str(e)}")

class ModernChatBubble:
    def __init__(self, parent, message, is_own=False, is_admin=False, avatar_image=None, on_delete=None):
        self.parent = parent
        self.message = message
        self.is_own = is_own
        self.is_admin = is_admin
        self.avatar_image = avatar_image
        self.on_delete = on_delete
        
    def create_widget(self):
        main_frame = tk.Frame(self.parent, bg=Config.THEME['background'])
        main_frame.pack(fill='x', padx=20, pady=4)
        
        content_frame = tk.Frame(main_frame, bg=Config.THEME['background'])
        content_frame.pack(side=tk.RIGHT if self.is_own else tk.LEFT, 
                          anchor='e' if self.is_own else 'w')
        
        if not self.is_own:
            self._create_avatar(content_frame)
        
        text_container = tk.Frame(content_frame, bg=Config.THEME['background'])
        text_container.pack(side=tk.LEFT if self.is_own else tk.RIGHT)
        
        if not self.is_own:
            self._create_sender_info(text_container)
        
        self._create_message_bubble(text_container)
        
        return main_frame

    def _create_avatar(self, parent):
        avatar_container = tk.Frame(parent, bg=Config.THEME['background'])
        avatar_container.pack(side=tk.LEFT, padx=(0, 10))
        
        # Создаем градиентный аватар
        avatar_canvas = tk.Canvas(avatar_container, width=42, height=42, 
                                 highlightthickness=0, bg=Config.THEME['background'])
        avatar_canvas.pack()
        
        # Градиент для аватара
        colors = [Config.THEME['primary'], Config.THEME['primary_light']]
        for i in range(42):
            ratio = i / 42
            r = int(int(colors[0][1:3], 16) * (1 - ratio) + int(colors[1][1:3], 16) * ratio)
            g = int(int(colors[0][3:5], 16) * (1 - ratio) + int(colors[1][3:5], 16) * ratio)
            b = int(int(colors[0][5:7], 16) * (1 - ratio) + int(colors[1][5:7], 16) * ratio)
            color = f"#{r:02x}{g:02x}{b:02x}"
            avatar_canvas.create_line(0, i, 42, i, fill=color)
        
        initials = self.message['user'][:2].upper() if self.message.get('user') else "??"
        avatar_canvas.create_text(21, 21, text=initials, fill='white', 
                                 font=('Segoe UI', 12, 'bold'))

    def _create_sender_info(self, parent):
        name_frame = tk.Frame(parent, bg=Config.THEME['background'])
        name_frame.pack(anchor='w', pady=(0, 5))
        
        name_label = tk.Label(name_frame, text=self.message.get('user', 'Unknown'), 
                            font=Config.FONTS['body_small'], fg=Config.THEME['text_secondary'], 
                            bg=Config.THEME['background'])
        name_label.pack(side=tk.LEFT)
        
        if self.is_admin:
            admin_label = tk.Label(name_frame, text=" 👑", 
                                 font=Config.FONTS['caption'], fg=Config.THEME['accent'], 
                                 bg=Config.THEME['background'])
            admin_label.pack(side=tk.LEFT, padx=(2, 0))

    def _create_message_bubble(self, parent):
        bubble_frame = tk.Frame(parent, bg=Config.THEME['background'])
        bubble_frame.pack(fill='x', pady=(2, 0))
        
        bubble_color = Config.THEME['primary'] if self.is_own else Config.THEME['card']
        text_color = Config.THEME['white'] if self.is_own else Config.THEME['text_primary']
        
        # Основное сообщение
        if self.message.get('message'):
            msg_frame = tk.Frame(bubble_frame, bg=bubble_color)
            msg_frame.pack(anchor='e' if self.is_own else 'w', padx=0, pady=0)
            
            msg_label = tk.Label(
                msg_frame, text=self.message['message'], 
                font=Config.FONTS['body'], fg=text_color, bg=bubble_color, 
                justify='left', wraplength=400, padx=16, pady=12
            )
            msg_label.pack(anchor='e' if self.is_own else 'w')
        
        # Время и действия
        self._create_message_footer(parent)

    def _create_message_footer(self, parent):
        time_frame = tk.Frame(parent, bg=Config.THEME['background'])
        time_frame.pack(fill='x', pady=(8, 0))
        
        try:
            t = datetime.fromisoformat(self.message['timestamp'])
            time_text = t.strftime("%H:%M")
        except:
            time_text = "??:??"
            
        time_label = tk.Label(time_frame, text=time_text, 
                            font=Config.FONTS['caption'], 
                            fg=Config.THEME['text_light'], 
                            bg=Config.THEME['background'])
        
        if self.is_own:
            time_label.pack(side=tk.RIGHT)
            if self.on_delete:
                del_btn = ModernButton(time_frame, "🗑️", command=self.on_delete,
                                      width=30, height=25, bg_color=Config.THEME['danger'],
                                      font=Config.FONTS['caption'])
                del_btn.pack(side=tk.RIGHT, padx=(5, 0))
        else:
            time_label.pack(side=tk.LEFT)

class ModernSidePanel:
    def __init__(self, parent, app):
        self.parent = parent
        self.app = app
        self.create_widgets()
    
    def create_widgets(self):
        # Основной фрейм боковой панели
        self.side_panel = tk.Frame(self.parent, bg=Config.THEME['surface'], width=300)
        self.side_panel.pack(side=tk.LEFT, fill=tk.Y)
        self.side_panel.pack_propagate(False)
        
        # Заголовок с градиентом
        header = GradientFrame(self.side_panel, height=140, 
                             colors=[Config.THEME['primary'], Config.THEME['primary_light']])
        header.pack(fill='x')
        
        title_label = tk.Label(header, text="Tandau", 
                              font=('Segoe UI', 28, 'bold'), fg=Config.THEME['white'], 
                              bg=Config.THEME['primary'])
        title_label.place(relx=0.5, rely=0.4, anchor='center')
        
        subtitle_label = tk.Label(header, text="Messenger", 
                                font=('Segoe UI', 16), fg=Config.THEME['white'], 
                                bg=Config.THEME['primary'])
        subtitle_label.place(relx=0.5, rely=0.7, anchor='center')
        
        # Информация о пользователе
        user_frame = tk.Frame(self.side_panel, bg=Config.THEME['card'], padx=20, pady=20)
        user_frame.pack(fill=tk.X, pady=(0, 15))
        
        # Градиентный аватар
        avatar_frame = tk.Frame(user_frame, width=60, height=60, bg=Config.THEME['card'])
        avatar_frame.pack(side=tk.LEFT)
        avatar_frame.pack_propagate(False)
        
        avatar_canvas = GradientFrame(avatar_frame, width=60, height=60,
                                    colors=[Config.THEME['primary'], Config.THEME['primary_light']])
        avatar_canvas.pack(fill='both', expand=True)
        
        user_initials = tk.Label(avatar_canvas, 
                               text=self.app.current_user[:2].upper() if self.app.current_user else "US",
                               font=('Segoe UI', 16, 'bold'), fg=Config.THEME['white'], 
                               bg=Config.THEME['primary'])
        user_initials.place(relx=0.5, rely=0.5, anchor='center')
        
        # Информация
        user_info = tk.Frame(user_frame, bg=Config.THEME['card'])
        user_info.pack(side=tk.LEFT, fill=tk.X, expand=True, padx=(15, 0))
        
        username_label = tk.Label(user_info, text=self.app.current_user or "User", 
                                font=Config.FONTS['body_large'], fg=Config.THEME['text_primary'], 
                                bg=Config.THEME['card'], anchor='w')
        username_label.pack(fill=tk.X)
        
        status_label = tk.Label(user_info, text="🟢 В сети", 
                              font=Config.FONTS['caption'], fg=Config.THEME['success'], 
                              bg=Config.THEME['card'], anchor='w')
        status_label.pack(fill=tk.X)
        
        # Навигация
        nav_frame = tk.Frame(self.side_panel, bg=Config.THEME['surface'])
        nav_frame.pack(fill=tk.X, padx=15, pady=20)
        
        nav_items = [
            ("🌐", "Публичный чат", self.show_public_chat),
            ("👥", "Приватные чаты", self.show_private_chats),
            ("📢", "Каналы", self.show_channels),
            ("⚙️", "Настройки", self.show_settings),
            ("🔗", "Подключение", self.show_connection)
        ]
        
        for icon, text, command in nav_items:
            nav_btn = tk.Frame(nav_frame, bg=Config.THEME['surface'], cursor='hand2')
            nav_btn.pack(fill=tk.X, pady=8)
            nav_btn.bind('<Button-1>', lambda e, cmd=command: cmd())
            
            icon_label = tk.Label(nav_btn, text=icon, font=('Segoe UI', 16),
                                bg=Config.THEME['surface'], fg=Config.THEME['text_secondary'])
            icon_label.pack(side=tk.LEFT, padx=(10, 15))
            icon_label.bind('<Button-1>', lambda e, cmd=command: cmd())
            
            text_label = tk.Label(nav_btn, text=text, font=Config.FONTS['body'],
                                bg=Config.THEME['surface'], fg=Config.THEME['text_secondary'])
            text_label.pack(side=tk.LEFT, fill=tk.X, expand=True)
            text_label.bind('<Button-1>', lambda e, cmd=command: cmd())
            
            # Hover эффект
            def on_enter(e, btn=nav_btn, icon_lbl=icon_label, text_lbl=text_label):
                btn.config(bg=Config.THEME['card'])
                icon_lbl.config(bg=Config.THEME['card'], fg=Config.THEME['primary'])
                text_lbl.config(bg=Config.THEME['card'], fg=Config.THEME['primary'])
            
            def on_leave(e, btn=nav_btn, icon_lbl=icon_label, text_lbl=text_label):
                btn.config(bg=Config.THEME['surface'])
                icon_lbl.config(bg=Config.THEME['surface'], fg=Config.THEME['text_secondary'])
                text_lbl.config(bg=Config.THEME['surface'], fg=Config.THEME['text_secondary'])
            
            nav_btn.bind('<Enter>', on_enter)
            nav_btn.bind('<Leave>', on_leave)
            icon_label.bind('<Enter>', on_enter)
            icon_label.bind('<Leave>', on_leave)
            text_label.bind('<Enter>', on_enter)
            text_label.bind('<Leave>', on_leave)
        
        # Статус сервера
        self.status_frame = tk.Frame(self.side_panel, bg=Config.THEME['surface'])
        self.status_frame.pack(side=tk.BOTTOM, fill=tk.X, padx=15, pady=20)
        
        status_content = tk.Frame(self.status_frame, bg=Config.THEME['card'], padx=15, pady=12)
        status_content.pack(fill=tk.X)
        
        self.status_indicator = tk.Label(status_content, text="⚪", 
                                       font=Config.FONTS['body_large'], 
                                       bg=Config.THEME['card'])
        self.status_indicator.pack(side=tk.LEFT)
        
        status_text_frame = tk.Frame(status_content, bg=Config.THEME['card'])
        status_text_frame.pack(side=tk.LEFT, fill=tk.X, expand=True, padx=(10, 0))
        
        tk.Label(status_text_frame, text="Статус сервера", 
                font=Config.FONTS['caption'], fg=Config.THEME['text_light'],
                bg=Config.THEME['card']).pack(anchor='w')
        
        self.status_label = tk.Label(status_text_frame, text="Проверка...", 
                                   font=Config.FONTS['body_small'], 
                                   fg=Config.THEME['text_secondary'],
                                   bg=Config.THEME['card'])
        self.status_label.pack(anchor='w')
        
        # Запускаем проверку статуса
        self.app.check_server_status(self.update_status)
    
    def update_status(self, is_online):
        if is_online:
            self.status_indicator.config(text="🟢", fg=Config.THEME['success'])
            self.status_label.config(text="Сервер онлайн", fg=Config.THEME['success'])
        else:
            self.status_indicator.config(text="🔴", fg=Config.THEME['danger'])
            self.status_label.config(text="Сервер офлайн", fg=Config.THEME['danger'])
    
    def show_public_chat(self):
        self.app.current_chat_type = "public"
        self.app.current_private_chat_with = None
        self.app.current_channel_id = None
        self.app.update_chat_display()
    
    def show_private_chats(self):
        messagebox.showinfo("Приватные чаты", "Функция приватных чатов скоро будет доступна")
    
    def show_channels(self):
        messagebox.showinfo("Каналы", "Функция каналов скоро будет доступна")
    
    def show_settings(self):
        messagebox.showinfo("Настройки", "Настройки приложения скоро будут доступны")
    
    def show_connection(self):
        ConnectionDialog(self.parent, self.app)

class ModernMessengerApp:
    def __init__(self, root):
        self.root = root
        self.root.title("Tandau Messenger")
        self.root.geometry("1200x800")
        self.root.configure(bg=Config.THEME['background'])
        self.root.minsize(1000, 700)
        
        self.client_socket = None
        self.receive_thread = None
        self.current_user = None
        self.is_admin = False
        self.current_chat_type = "public"
        self.current_private_chat_with = None
        self.current_channel_id = None
        self.current_image_path = None
        self.current_video_path = None
        
        try:
            self.audio = pyaudio.PyAudio()
        except:
            self.audio = None
            
        self.is_recording = False
        self.audio_frames = []
        
        self.avatar_cache = {}
        self.server_connected = False
        
        # Создаем необходимые директории
        for dir in ['chat_images', 'chat_videos', 'voice_messages', 'user_avatars']:
            os.makedirs(dir, exist_ok=True)
        
        # Автоматическое подключение к серверу при запуске
        self.auto_connect_to_server()
        
        self.create_auth_screen()
    
    def auto_connect_to_server(self):
        """Автоматическое подключение к серверу при запуске"""
        def connect():
            try:
                self.client_socket = socket.socket()
                self.client_socket.settimeout(5)
                self.client_socket.connect((Config.SERVER_HOST, Config.SERVER_PORT))
                welcome = self.client_socket.recv(1024).decode('utf-8')
                print(f"Автоматическое подключение: {welcome}")
                self.root.after(0, lambda: self.update_connection_status(True))
            except Exception as e:
                print(f"Автоматическое подключение не удалось: {e}")
                self.root.after(0, lambda: self.update_connection_status(False))
        
        # Запускаем в отдельном потоке
        threading.Thread(target=connect, daemon=True).start()
    
    def update_connection_status(self, connected):
        """Обновление статуса подключения"""
        self.server_connected = connected
        if hasattr(self, 'status_label'):
            if connected:
                self.status_label.config(text="🟢 Сервер подключен", fg=Config.THEME['success'])
            else:
                self.status_label.config(text="🔴 Сервер отключен", fg=Config.THEME['danger'])
    
    def check_server_status(self, callback):
        def check():
            try:
                test_socket = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
                test_socket.settimeout(3)
                test_socket.connect((Config.SERVER_HOST, Config.SERVER_PORT))
                test_socket.close()
                self.root.after(0, lambda: callback(True))
            except:
                self.root.after(0, lambda: callback(False))
        
        threading.Thread(target=check, daemon=True).start()
    
    def connect_to_server(self):
        """Подключение к серверу (для повторных попыток)"""
        try:
            if self.client_socket:
                self.client_socket.close()
            
            self.client_socket = socket.socket()
            self.client_socket.settimeout(10)
            self.client_socket.connect((Config.SERVER_HOST, Config.SERVER_PORT))
            welcome = self.client_socket.recv(1024).decode('utf-8')
            print(f"Подключение: {welcome}")
            self.update_connection_status(True)
            return True
        except Exception as e:
            print(f"Connection error: {e}")
            self.update_connection_status(False)
            return False
    
    def register(self):
        """Регистрация нового пользователя"""
        username = self.reg_username_entry.get()
        password = self.reg_password_entry.get()
        confirm_password = self.reg_confirm_entry.get()
        
        if not username or not password:
            messagebox.showerror("Ошибка", "Пожалуйста, заполните все поля")
            return
        
        if password != confirm_password:
            messagebox.showerror("Ошибка", "Пароли не совпадают")
            return
        
        if len(username) < 3:
            messagebox.showerror("Ошибка", "Имя пользователя должно быть не менее 3 символов")
            return
        
        if len(password) < 4:
            messagebox.showerror("Ошибка", "Пароль должен быть не менее 4 символов")
            return
        
        # Проверяем подключение к серверу
        if not self.server_connected:
            if not self.connect_to_server():
                messagebox.showerror("Ошибка", "Нет подключения к серверу")
                return
        
        try:
            # Отправляем запрос на регистрацию
            self.client_socket.send(f"REGISTER:{username}:{password}".encode('utf-8'))
            response = self.client_socket.recv(1024).decode('utf-8')
            
            if response == "OK":
                messagebox.showinfo("Успех", "Регистрация прошла успешно! Теперь вы можете войти.")
                self.show_login()
            else:
                messagebox.showerror("Ошибка", response)
                
        except Exception as e:
            messagebox.showerror("Ошибка", f"Ошибка при регистрации: {str(e)}")
    
    def login(self):
        """Вход пользователя"""
        username = self.login_username_entry.get()
        password = self.login_password_entry.get()
        
        if not username or not password:
            messagebox.showerror("Ошибка", "Пожалуйста, заполните все поля")
            return
        
        # Проверяем подключение к серверу
        if not self.server_connected:
            if not self.connect_to_server():
                messagebox.showerror("Ошибка", "Нет подключения к серверу")
                return
        
        try:
            # Отправляем credentials
            self.client_socket.send(f"LOGIN:{username}:{password}".encode('utf-8'))
            response = self.client_socket.recv(1024).decode('utf-8')
            
            if response == "OK":
                self.current_user = username
                self.start_receive_thread()
                self.create_messenger_screen()
            else:
                messagebox.showerror("Ошибка", "Неверный логин или пароль")
                
        except Exception as e:
            messagebox.showerror("Ошибка", f"Ошибка при входе: {str(e)}")
    
    def start_receive_thread(self):
        self.receive_thread = threading.Thread(target=self.receive_messages, daemon=True)
        self.receive_thread.start()
    
    def receive_messages(self):
        while True:
            try:
                msg = self.client_socket.recv(4096).decode('utf-8')
                if not msg: 
                    break
                self.root.after(0, lambda m=msg: self.handle_server_message(m))
            except Exception as e:
                print(f"Receive error: {e}")
                break
    
    def handle_server_message(self, msg):
        print(f"Received: {msg}")
        if msg.startswith("MSG:"):
            try:
                data = json.loads(msg[4:])
                if self.current_chat_type == "public":
                    self.display_message(data)
            except json.JSONDecodeError:
                print("Invalid JSON received")
        elif msg.startswith("PRIVATE:"):
            parts = msg[8:].split(':', 1)
            if len(parts) == 2 and parts[0] == self.current_private_chat_with:
                self.display_message(json.loads(parts[1]))
        elif msg.startswith("CHANNEL:"):
            parts = msg[8:].split(':', 2)
            if len(parts) == 3 and parts[0] == self.current_channel_id and parts[1] == "MSG":
                self.display_message(json.loads(parts[2]))
    
    def display_message(self, msg):
        if not hasattr(self, 'scrollable_frame'):
            return
            
        is_own = msg.get('user') == self.current_user
        avatar = self.get_user_avatar(msg.get('user', ''))
        
        bubble = ModernChatBubble(
            self.scrollable_frame, msg, is_own,
            msg.get('is_admin', False), avatar
        )
        bubble.create_widget().pack(fill='x', pady=2)
        
        # Автоскролл к новому сообщению
        if hasattr(self, 'canvas'):
            self.canvas.yview_moveto(1.0)
    
    def send_message(self):
        if not self.server_connected:
            messagebox.showerror("Ошибка", "Нет подключения к серверу")
            return
            
        text = self.message_entry.get().strip()
        if not text and not self.current_image_path and not self.current_video_path:
            return
        
        try:
            if self.current_chat_type == "public":
                self.client_socket.send(f"MSG:{text}".encode('utf-8'))
            elif self.current_chat_type == "private":
                self.client_socket.send(f"PRIVATE:{self.current_private_chat_with}:{text}".encode('utf-8'))
            elif self.current_chat_type == "channel":
                self.client_socket.send(f"CHANNEL:{self.current_channel_id}:MSG:{text}".encode('utf-8'))
            
            self.message_entry.delete(0, tk.END)
            
        except Exception as e:
            messagebox.showerror("Ошибка", f"Не удалось отправить сообщение: {str(e)}")
    
    def get_user_avatar(self, username):
        if username in self.avatar_cache:
            return self.avatar_cache[username]
        
        path = os.path.join("user_avatars", f"{username}_avatar.png")
        if os.path.exists(path):
            try:
                img = Image.open(path).resize((36, 36), Image.LANCZOS)
                photo = ImageTk.PhotoImage(img)
                self.avatar_cache[username] = photo
                return photo
            except Exception as e:
                print(f"Error loading avatar: {e}")
        
        return None

    def create_auth_screen(self):
        """Создание экрана аутентификации (вход/регистрация)"""
        # Очистка окна
        for widget in self.root.winfo_children():
            widget.destroy()
        
        # Основной контейнер с градиентом
        main_frame = GradientFrame(self.root, 
                                 colors=[Config.THEME['primary_dark'], Config.THEME['background']])
        main_frame.pack(fill='both', expand=True)
        
        # Контейнер для контента
        content_container = tk.Frame(main_frame, bg=Config.THEME['background'])
        content_container.place(relx=0.5, rely=0.5, anchor='center', width=1000, height=600)
        
        # Левая часть - описание
        left_frame = tk.Frame(content_container, bg=Config.THEME['primary'], width=500)
        left_frame.pack(side=tk.LEFT, fill='both', expand=True)
        
        left_content = tk.Frame(left_frame, bg=Config.THEME['primary'])
        left_content.place(relx=0.5, rely=0.5, anchor='center')
        
        title_label = tk.Label(left_content, text="Tandau", 
                              font=('Segoe UI', 48, 'bold'), 
                              fg=Config.THEME['white'], 
                              bg=Config.THEME['primary'])
        title_label.pack(pady=(0, 10))
        
        subtitle_label = tk.Label(left_content, text="Современный мессенджер", 
                                font=('Segoe UI', 18), 
                                fg=Config.THEME['white'], 
                                bg=Config.THEME['primary'])
        subtitle_label.pack(pady=(0, 40))
        
        features = [
            "🔒 Безопасное общение",
            "🌐 Поддержка медиа", 
            "👥 Групповые чаты",
            "📱 Синхронизация"
        ]
        
        for feature in features:
            feature_label = tk.Label(left_content, text=feature, 
                                   font=Config.FONTS['body_large'], 
                                   fg=Config.THEME['white'], 
                                   bg=Config.THEME['primary'])
            feature_label.pack(pady=8)
        
        # Правая часть - форма аутентификации
        self.right_frame = tk.Frame(content_container, bg=Config.THEME['surface'], width=500)
        self.right_frame.pack(side=tk.RIGHT, fill='both', expand=True)
        
        # Статус подключения
        self.status_label = tk.Label(self.right_frame, text="🟡 Подключение...", 
                                   font=Config.FONTS['body_small'],
                                   fg=Config.THEME['warning'],
                                   bg=Config.THEME['surface'])
        self.status_label.pack(pady=(40, 20))
        
        # Показываем экран входа по умолчанию
        self.show_login()
    
    def show_login(self):
        """Показать форму входа"""
        # Очистка правой части
        for widget in self.right_frame.winfo_children():
            if widget != self.status_label:
                widget.destroy()
        
        login_container = tk.Frame(self.right_frame, bg=Config.THEME['surface'])
        login_container.pack(expand=True, padx=50, pady=20)
        
        # Заголовок формы
        form_title = tk.Label(login_container, text="Вход в систему", 
                            font=Config.FONTS['h2'], 
                            fg=Config.THEME['text_primary'], 
                            bg=Config.THEME['surface'])
        form_title.pack(pady=(0, 40))
        
        # Поля ввода
        input_frame = tk.Frame(login_container, bg=Config.THEME['surface'])
        input_frame.pack(pady=20)
        
        self.login_username_entry = ModernEntry(input_frame, placeholder="Введите имя пользователя", 
                                              width=380, height=50, icon="👤")
        self.login_username_entry.pack(pady=(0, 20))
        
        self.login_password_entry = ModernEntry(input_frame, placeholder="Введите пароль", 
                                              width=380, height=50, icon="🔒", show='•')
        self.login_password_entry.pack(pady=(0, 30))
        
        # Кнопка входа
        login_btn = ModernButton(input_frame, "Войти", command=self.login,
                                width=380, height=50, 
                                bg_color=Config.THEME['primary'])
        login_btn.pack(pady=10)
        
        # Ссылка на регистрацию
        reg_link = tk.Label(input_frame, text="Нет аккаунта? Зарегистрироваться", 
                          font=Config.FONTS['caption'], 
                          fg=Config.THEME['primary'],
                          bg=Config.THEME['surface'], cursor='hand2')
        reg_link.pack(pady=10)
        reg_link.bind('<Button-1>', lambda e: self.show_register())
        
        # Ссылка на настройки подключения
        connection_link = tk.Label(input_frame, text="Настройки подключения →", 
                                 font=Config.FONTS['caption'], 
                                 fg=Config.THEME['primary'],
                                 bg=Config.THEME['surface'], cursor='hand2')
        connection_link.pack(pady=5)
        connection_link.bind('<Button-1>', lambda e: ConnectionDialog(self.root, self))
        
        # Привязка Enter для входа
        self.root.bind('<Return>', lambda e: self.login())
    
    def show_register(self):
        """Показать форму регистрации"""
        # Очистка правой части
        for widget in self.right_frame.winfo_children():
            if widget != self.status_label:
                widget.destroy()
        
        reg_container = tk.Frame(self.right_frame, bg=Config.THEME['surface'])
        reg_container.pack(expand=True, padx=50, pady=20)
        
        # Заголовок формы
        form_title = tk.Label(reg_container, text="Регистрация", 
                            font=Config.FONTS['h2'], 
                            fg=Config.THEME['text_primary'], 
                            bg=Config.THEME['surface'])
        form_title.pack(pady=(0, 40))
        
        # Поля ввода
        input_frame = tk.Frame(reg_container, bg=Config.THEME['surface'])
        input_frame.pack(pady=20)
        
        self.reg_username_entry = ModernEntry(input_frame, placeholder="Придумайте имя пользователя", 
                                            width=380, height=50, icon="👤")
        self.reg_username_entry.pack(pady=(0, 15))
        
        self.reg_password_entry = ModernEntry(input_frame, placeholder="Придумайте пароль", 
                                            width=380, height=50, icon="🔒", show='•')
        self.reg_password_entry.pack(pady=(0, 15))
        
        self.reg_confirm_entry = ModernEntry(input_frame, placeholder="Повторите пароль", 
                                           width=380, height=50, icon="🔒", show='•')
        self.reg_confirm_entry.pack(pady=(0, 30))
        
        # Кнопка регистрации
        reg_btn = ModernButton(input_frame, "Зарегистрироваться", command=self.register,
                              width=380, height=50, 
                              bg_color=Config.THEME['secondary'])
        reg_btn.pack(pady=10)
        
        # Ссылка на вход
        login_link = tk.Label(input_frame, text="Уже есть аккаунт? Войти", 
                            font=Config.FONTS['caption'], 
                            fg=Config.THEME['primary'],
                            bg=Config.THEME['surface'], cursor='hand2')
        login_link.pack(pady=10)
        login_link.bind('<Button-1>', lambda e: self.show_login())
        
        # Ссылка на настройки подключения
        connection_link = tk.Label(input_frame, text="Настройки подключения →", 
                                 font=Config.FONTS['caption'], 
                                 fg=Config.THEME['primary'],
                                 bg=Config.THEME['surface'], cursor='hand2')
        connection_link.pack(pady=5)
        connection_link.bind('<Button-1>', lambda e: ConnectionDialog(self.root, self))
        
        # Привязка Enter для регистрации
        self.root.bind('<Return>', lambda e: self.register())

    def create_messenger_screen(self):
        # Очистка окна
        for widget in self.root.winfo_children():
            widget.destroy()
        
        # Создание боковой панели
        self.side_panel = ModernSidePanel(self.root, self)
        
        # Основная область чата
        main_chat_frame = tk.Frame(self.root, bg=Config.THEME['background'])
        main_chat_frame.pack(side=tk.LEFT, fill='both', expand=True)
        
        # Заголовок чата
        chat_header = tk.Frame(main_chat_frame, bg=Config.THEME['surface'], height=80)
        chat_header.pack(fill='x')
        chat_header.pack_propagate(False)
        
        chat_title = tk.Label(chat_header, text="🌐 Публичный чат", 
                             font=Config.FONTS['h3'], 
                             fg=Config.THEME['text_primary'], 
                             bg=Config.THEME['surface'])
        chat_title.pack(side=tk.LEFT, padx=30, pady=25)
        
        # Область сообщений
        chat_container = tk.Frame(main_chat_frame, bg=Config.THEME['background'])
        chat_container.pack(fill='both', expand=True, padx=20, pady=10)
        
        # Canvas и Scrollbar для сообщений
        self.canvas = tk.Canvas(chat_container, bg=Config.THEME['background'], highlightthickness=0)
        scrollbar = ttk.Scrollbar(chat_container, orient="vertical", command=self.canvas.yview)
        self.scrollable_frame = tk.Frame(self.canvas, bg=Config.THEME['background'])
        
        self.scrollable_frame.bind(
            "<Configure>",
            lambda e: self.canvas.configure(scrollregion=self.canvas.bbox("all"))
        )
        
        self.canvas.create_window((0, 0), window=self.scrollable_frame, anchor="nw")
        self.canvas.configure(yscrollcommand=scrollbar.set)
        
        self.canvas.pack(side="left", fill="both", expand=True)
        scrollbar.pack(side="right", fill="y")
        
        # Панель ввода сообщения
        input_frame = tk.Frame(main_chat_frame, bg=Config.THEME['surface'], height=100)
        input_frame.pack(fill='x', side=tk.BOTTOM)
        input_frame.pack_propagate(False)
        
        input_container = tk.Frame(input_frame, bg=Config.THEME['surface'])
        input_container.pack(fill='both', padx=20, pady=20)
        
        # Кнопки медиа
        media_frame = tk.Frame(input_container, bg=Config.THEME['surface'])
        media_frame.pack(side=tk.LEFT, fill='y')
        
        # Кнопки действий
        action_buttons = [
            ("📎", "Прикрепить файл"),
            ("🎤", "Голосовое сообщение"),
            ("📷", "Сделать фото")
        ]
        
        for icon, tooltip in action_buttons:
            btn = ModernButton(media_frame, icon, width=45, height=45, 
                             bg_color=Config.THEME['card'], font=('Segoe UI', 16))
            btn.pack(side=tk.LEFT, padx=(0, 10))
        
        # Поле ввода сообщения
        self.message_entry = ModernEntry(input_container, placeholder="Введите сообщение...", 
                                        width=400, height=45, icon="💬")
        self.message_entry.pack(side=tk.LEFT, fill='x', expand=True, padx=(10, 10))
        self.message_entry.entry.bind('<Return>', lambda e: self.send_message())
        
        # Кнопка отправки
        send_btn = ModernButton(input_container, "Отправить", 
                               command=self.send_message, width=100, height=45,
                               bg_color=Config.THEME['primary'])
        send_btn.pack(side=tk.RIGHT)
        
        # Показываем приветственное сообщение
        self.update_chat_display()

    def update_chat_display(self):
        # Очистка текущих сообщений
        if hasattr(self, 'scrollable_frame'):
            for widget in self.scrollable_frame.winfo_children():
                widget.destroy()
        
        # Приветственное сообщение
        welcome_frame = tk.Frame(self.scrollable_frame, bg=Config.THEME['background'])
        welcome_frame.pack(fill='x', pady=40)
        
        welcome_label = tk.Label(welcome_frame, 
                               text="Добро пожаловать в Tandau Messenger! 🎉", 
                               font=Config.FONTS['h3'], 
                               fg=Config.THEME['text_primary'], 
                               bg=Config.THEME['background'])
        welcome_label.pack()
        
        subtitle_label = tk.Label(welcome_frame, 
                                text="Начните общение, отправив первое сообщение", 
                                font=Config.FONTS['body'], 
                                fg=Config.THEME['text_secondary'], 
                                bg=Config.THEME['background'])
        subtitle_label.pack(pady=10)

# === ЗАПУСК ПРИЛОЖЕНИЯ ===
if __name__ == "__main__":
    try:
        root = tk.Tk()
        
        # Устанавливаем темную тему для ttk
        style = ttk.Style()
        style.theme_use('clam')
        
        # Настраиваем цвета для ttk
        style.configure("TScrollbar", background=Config.THEME['surface'],
                       troughcolor=Config.THEME['background'])
        
        app = ModernMessengerApp(root)
        
        # Центрирование окна
        root.update_idletasks()
        x = (root.winfo_screenwidth() - root.winfo_reqwidth()) // 2
        y = (root.winfo_screenheight() - root.winfo_reqheight()) // 2
        root.geometry(f"+{x}+{y}")
        
        root.mainloop()
        
    except Exception as e:
        error_msg = f"Не удалось запустить приложение:\n{str(e)}"
        print(error_msg)
        
        # Пробуем показать ошибку через messagebox
        try:
            import tkinter.messagebox as mb
            mb.showerror("Ошибка", error_msg)
        except:
            # Если не работает GUI, просто выводим в консоль
            print("Графический интерфейс недоступен")
