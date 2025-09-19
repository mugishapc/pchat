# This must be the VERY FIRST import/statement in your application
import eventlet
eventlet.monkey_patch()

# Now import other modules
import os
from flask import Flask, render_template, request, jsonify, redirect, url_for, session
from flask_socketio import SocketIO, emit, join_room
from models import db, bcrypt, User, Message
from datetime import datetime
import base64
from dotenv import load_dotenv
from flask_migrate import Migrate

# Load environment variables from .env file
load_dotenv()

app = Flask(__name__)
app.config['SECRET_KEY'] = os.getenv('SECRET_KEY', 'd29c234ca310aa6990092d4b6cd4c4854585c51e1f73bf4de510adca03f5bc4e')

# Database configuration - fixed the issue
database_url = os.getenv('DATABASE_URL')
if database_url and database_url.startswith('postgres://'):
    database_url = database_url.replace('postgres://', 'postgresql://', 1)
app.config['SQLALCHEMY_DATABASE_URI'] = 'postgresql://neondb_owner:npg_OKbEBdk7xT0h@ep-gentle-frog-adxj47j1-pooler.c-2.us-east-1.aws.neon.tech/neondb?sslmode=require&channel_binding=require'

app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False

# Initialize extensions
db.init_app(app)
bcrypt.init_app(app)
migrate = Migrate(app, db)
socketio = SocketIO(app, cors_allowed_origins="*", async_mode='eventlet')

# Create database tables
with app.app_context():
    db.create_all()

# Authentication routes
@app.route('/')
def index():
    if 'user_id' not in session:
        return redirect(url_for('login'))
    return render_template('index.html', username=session.get('username'))

@app.route('/register', methods=['GET', 'POST'])
def register():
    if request.method == 'POST':
        username = request.form['username']
        password = request.form['password']
        
        if User.query.filter_by(username=username).first():
            return render_template('register.html', error='Username already exists')
        
        user = User(username=username)
        user.set_password(password)
        db.session.add(user)
        db.session.commit()
        
        session['user_id'] = user.id
        session['username'] = user.username
        return redirect(url_for('index'))
    
    return render_template('register.html')

@app.route('/login', methods=['GET', 'POST'])
def login():
    if request.method == 'POST':
        username = request.form['username']
        password = request.form['password']
        
        user = User.query.filter_by(username=username).first()
        if user and user.check_password(password):
            session['user_id'] = user.id
            session['username'] = user.username
            return redirect(url_for('index'))
        
        return render_template('login.html', error='Invalid username or password')
    
    return render_template('login.html')

@app.route('/logout')
def logout():
    session.clear()
    return redirect(url_for('login'))

# API routes
@app.route('/messages')
def get_messages():
    if 'user_id' not in session:
        return jsonify({'error': 'Not authenticated'}), 401
    
    messages = Message.query.order_by(Message.timestamp.asc()).all()
    message_list = []
    for msg in messages:
        message_list.append({
            'id': msg.id,
            'content': msg.content,
            'type': msg.message_type,
            'username': msg.user.username,
            'timestamp': msg.timestamp.isoformat()
        })
    
    return jsonify(message_list)

@app.route('/upload_audio', methods=['POST'])
def upload_audio():
    if 'user_id' not in session:
        return jsonify({'error': 'Not authenticated'}), 401
    
    audio_data = request.files['audio'].read()
    audio_base64 = base64.b64encode(audio_data).decode('utf-8')
    data_url = f"data:audio/webm;base64,{audio_base64}"
    
    # Save to database
    message = Message(
        content=data_url,
        message_type='audio',
        user_id=session['user_id']
    )
    db.session.add(message)
    db.session.commit()
    
    # Broadcast to all clients
    socketio.emit('new_message', {
        'id': message.id,
        'content': data_url,
        'type': 'audio',
        'username': session['username'],
        'timestamp': message.timestamp.isoformat()
    })
    
    return jsonify({'success': True})

# SocketIO events
@socketio.on('connect')
def handle_connect():
    if 'user_id' in session:
        join_room('main')
        emit('user_joined', {'username': session['username']}, room='main')

@socketio.on('send_message')
def handle_message(data):
    if 'user_id' not in session:
        return
    
    content = data['content'].strip()
    if not content:
        return
    
    # Save to database
    message = Message(
        content=content,
        message_type='text',
        user_id=session['user_id']
    )
    db.session.add(message)
    db.session.commit()
    
    # Broadcast to all clients
    emit('new_message', {
        'id': message.id,
        'content': content,
        'type': 'text',
        'username': session['username'],
        'timestamp': message.timestamp.isoformat()
    }, room='main')

if __name__ == '__main__':
    socketio.run(app, debug=True, host='0.0.0.0', port=5000)