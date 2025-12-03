# app.py - точка входа для Render
import eventlet
eventlet.monkey_patch()

from web_messenger import create_app, socketio

app = create_app()

if __name__ == '__main__':
    port = int(os.environ.get('PORT', 5000))
    socketio.run(app, host='0.0.0.0', port=port)
