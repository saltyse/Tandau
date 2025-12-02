from flask import Flask, render_template, send_file, request
import os

app = Flask(__name__)

# Конфигурация
app.config['UPLOAD_FOLDER'] = 'static/downloads'
app.config['ALLOWED_EXTENSIONS'] = {'exe', 'docx', 'apk', 'dmg'}

# Главная страница
@app.route('/')
def index():
    return render_template('index.html')

# Страница скачивания
@app.route('/download')
def download_page():
    return render_template('download.html')

# Пользовательское соглашение
@app.route('/agreement')
def agreement():
    return render_template('agreement.html')

# Контакты
@app.route('/contacts')
def contacts():
    return render_template('contacts.html')

# API для скачивания файлов
@app.route('/download/<platform>')
def download_file(platform):
    files = {
        'windows': 'Tandau_Setup.exe',
        'android': 'Tandau_Android.apk',
        'macos': 'Tandau_Mac.dmg',
        'agreement': 'user_agreement.docx'
    }
    
    if platform in files:
        file_path = os.path.join(app.config['UPLOAD_FOLDER'], files[platform])
        if os.path.exists(file_path):
            return send_file(file_path, as_attachment=True)
    
    return "Файл не найден", 404

# Статистика скачиваний (базовая)
download_stats = {
    'windows': 0,
    'android': 0,
    'macos': 0,
    'agreement': 0
}

@app.route('/api/download/<platform>', methods=['POST'])
def track_download(platform):
    if platform in download_stats:
        download_stats[platform] += 1
        return {'status': 'success', 'count': download_stats[platform]}
    return {'status': 'error'}, 400

if __name__ == '__main__':
    # Создаем папки, если их нет
    os.makedirs('static/downloads', exist_ok=True)
    os.makedirs('static/css', exist_ok=True)
    os.makedirs('templates', exist_ok=True)
    
    app.run(debug=True, host='0.0.0.0', port=5000)
