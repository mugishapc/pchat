# This must be the VERY FIRST import/statement in your application
import eventlet
eventlet.monkey_patch()

# Now import other modules
import os
from flask import Flask, render_template, request, jsonify, redirect, url_for, session
from flask_socketio import SocketIO, emit, join_room, leave_room
from models import db, bcrypt, User, Message, Conversation
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
app.config['SQLALCHEMY_DATABASE_URI'] = database_url or 'postgresql://neondb_owner:npg_OKbEBdk7xT0h@ep-gentle-frog-adxj47j1-pooler.c-2.us-east-1.aws.neon.tech/neondb?sslmode=require&channel_binding=require'

app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False

# Initialize extensions
db.init_app(app)
bcrypt.init_app(app)
migrate = Migrate(app, db)
socketio = SocketIO(app, cors_allowed_origins="*", async_mode='eventlet')

# Create database tables
with app.app_context():
    db.create_all()

# Add CORS headers
@app.after_request
def after_request(response):
    response.headers.add('Access-Control-Allow-Origin', request.headers.get('Origin', '*'))
    response.headers.add('Access-Control-Allow-Credentials', 'true')
    response.headers.add('Access-Control-Allow-Headers', 'Content-Type,Authorization')
    response.headers.add('Access-Control-Allow-Methods', 'GET,PUT,POST,DELETE,OPTIONS')
    return response

# Authentication routes
@app.route('/')
def index():
    if 'user_id' not in session:
        return redirect(url_for('login'))
    
    # Get all users except current user for conversation list
    users = User.query.filter(User.id != session['user_id']).all()
    
    # Get conversations where current user is involved
    conversations = Conversation.query.filter(
        (Conversation.user1_id == session['user_id']) | 
        (Conversation.user2_id == session['user_id'])
    ).all()
    
    # Prepare conversation data
    conversation_data = []
    for conv in conversations:
        other_user = conv.user1 if conv.user1_id != session['user_id'] else conv.user2
        conversation_data.append({
            'id': conv.id,
            'other_user_id': other_user.id,
            'other_username': other_user.username
        })
    
    return render_template('index.html', 
                          username=session.get('username'),
                          users=users,
                          conversations=conversation_data)

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
@app.route('/messages/<int:conversation_id>')
def get_messages(conversation_id):
    if 'user_id' not in session:
        return jsonify({'error': 'Not authenticated'}), 401
    
    # Check if user is part of this conversation
    conversation = Conversation.query.get(conversation_id)
    if not conversation or (conversation.user1_id != session['user_id'] and conversation.user2_id != session['user_id']):
        return jsonify({'error': 'Access denied'}), 403
    
    messages = Message.query.filter_by(conversation_id=conversation_id).order_by(Message.timestamp.asc()).all()
    message_list = []
    for msg in messages:
        message_list.append({
            'id': msg.id,
            'content': msg.content,
            'type': msg.message_type,
            'username': msg.user.username,
            'user_id': msg.user_id,
            'timestamp': msg.timestamp.isoformat()
        })
    
    return jsonify(message_list)

@app.route('/conversation/<int:other_user_id>', methods=['POST'])
def create_conversation(other_user_id):
    if 'user_id' not in session:
        return jsonify({'error': 'Not authenticated'}), 401
    
    # Check if conversation already exists
    conversation = Conversation.query.filter(
        ((Conversation.user1_id == session['user_id']) & (Conversation.user2_id == other_user_id)) |
        ((Conversation.user1_id == other_user_id) & (Conversation.user2_id == session['user_id']))
    ).first()
    
    if conversation:
        return jsonify({'conversation_id': conversation.id})
    
    # Create new conversation
    new_conversation = Conversation(
        user1_id=session['user_id'],
        user2_id=other_user_id
    )
    db.session.add(new_conversation)
    db.session.commit()
    
    return jsonify({'conversation_id': new_conversation.id})

@app.route('/upload_audio/<int:conversation_id>', methods=['POST'])
def upload_audio(conversation_id):
    if 'user_id' not in session:
        return jsonify({'error': 'Not authenticated'}), 401
    
    # Check if user is part of this conversation
    conversation = Conversation.query.get(conversation_id)
    if not conversation or (conversation.user1_id != session['user_id'] and conversation.user2_id != session['user_id']):
        return jsonify({'error': 'Access denied'}), 403
    
    audio_data = request.files['audio'].read()
    audio_base64 = base64.b64encode(audio_data).decode('utf-8')
    data_url = f"data:audio/webm;base64,{audio_base64}"
    
    # Save to database
    message = Message(
        content=data_url,
        message_type='audio',
        user_id=session['user_id'],
        conversation_id=conversation_id
    )
    db.session.add(message)
    db.session.commit()
    
    # Broadcast to conversation room
    socketio.emit('new_message', {
        'id': message.id,
        'content': data_url,
        'type': 'audio',
        'username': session['username'],
        'user_id': session['user_id'],
        'timestamp': message.timestamp.isoformat(),
        'conversation_id': conversation_id
    }, room=f'conversation_{conversation_id}')
    
    return jsonify({'success': True})

# SocketIO events
@socketio.on('connect')
def handle_connect():
    if 'user_id' in session:
        # Join user's personal room for notifications
        join_room(f'user_{session["user_id"]}')
        emit('connected', {'status': 'connected'})

@socketio.on('join_conversation')
def handle_join_conversation(data):
    if 'user_id' not in session:
        return
    
    conversation_id = data['conversation_id']
    
    # Check if user is part of this conversation
    conversation = Conversation.query.get(conversation_id)
    if conversation and (conversation.user1_id == session['user_id'] or conversation.user2_id == session['user_id']):
        join_room(f'conversation_{conversation_id}')
        emit('joined_conversation', {'conversation_id': conversation_id})

@socketio.on('leave_conversation')
def handle_leave_conversation(data):
    if 'user_id' not in session:
        return
    
    conversation_id = data['conversation_id']
    leave_room(f'conversation_{conversation_id}')
    emit('left_conversation', {'conversation_id': conversation_id})

@socketio.on('send_message')
def handle_message(data):
    if 'user_id' not in session:
        return
    
    content = data['content'].strip()
    conversation_id = data['conversation_id']
    
    if not content:
        return
    
    # Check if user is part of this conversation
    conversation = Conversation.query.get(conversation_id)
    if not conversation or (conversation.user1_id != session['user_id'] and conversation.user2_id != session['user_id']):
        return
    
    # Save to database
    message = Message(
        content=content,
        message_type='text',
        user_id=session['user_id'],
        conversation_id=conversation_id
    )
    db.session.add(message)
    db.session.commit()
    
    # Broadcast to conversation room
    emit('new_message', {
        'id': message.id,
        'content': content,
        'type': 'text',
        'username': session['username'],
        'user_id': session['user_id'],
        'timestamp': message.timestamp.isoformat(),
        'conversation_id': conversation_id
    }, room=f'conversation_{conversation_id}')

if __name__ == '__main__':
    socketio.run(app, debug=True, host='0.0.0.0', port=5000)