# This must be the VERY FIRST import/statement in your application
import eventlet
eventlet.monkey_patch()

# Now import other modules
import os
from flask import Flask, render_template, request, jsonify, redirect, url_for, session, flash
from flask_socketio import SocketIO, emit, join_room, leave_room
from flask_sqlalchemy import SQLAlchemy
from flask_bcrypt import Bcrypt
from datetime import datetime
import base64
from dotenv import load_dotenv
from flask_migrate import Migrate
import json
from pywebpush import webpush, WebPushException

# Load environment variables from .env file
load_dotenv()

app = Flask(__name__)
app.config['SECRET_KEY'] = os.getenv('SECRET_KEY', 'd29c234ca310aa6990092d4b6cd4c4854585c51e1f73bf4de510adca03f5bc4e')

# Database configuration - fixed the issue
database_url = os.getenv('DATABASE_URL')
if database_url and database_url.startswith('postgres://'):
    database_url = database_url.replace('postgres://', 'postgresql://', 1)
app.config['SQLALCHEMY_DATABASE_URI'] = database_url or 'sqlite:///app.db'

app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False

# VAPID keys for web push notifications
VAPID_PRIVATE_KEY = os.getenv('LS0tLS1CRUdJTiBQUklWQVRFIEtFWS0tLS0tCk1JR0hBZ0VBTUJNR0J5cUdTTTQ5QWdFR0NDcUdTTTQ5QXdFSEJHMHdhd0lCQVFRZ1RvQXV1N0pLTnZWSTJLWTAKaHdaOFg5S21PL0tlUXNWdFN3NExZSm5TRE9DaFJBTkNBQVFUVWJaQWY3OHlSYnRIN1VJbWNmamRhV21qVDMzVQpxbnBXWGt2ck5tRndyZ1JSRnFBOUJmd0ZUZ2xuSjQzV1J4emFJc0ZnZkdlWUluQzVpY2ZEM3FuNwotLS0tLUVORCBQUklWQVRFIEtFWS0tLS0tCg==')
VAPID_PUBLIC_KEY = os.getenv('BBNRtkB_vzJFu0ftQiZx-N1paaNPfdSqelZeS-s2YXCuBFEWoD0F_AVOCWcnjdZHHNoiwWB8Z5gicLmJx8Peqfs=')
VAPID_CLAIMS = {
    "sub": "mailto:mpc0679@gmail.com"
}

# Initialize extensions
db = SQLAlchemy()
bcrypt = Bcrypt()
db.init_app(app)
bcrypt.init_app(app)
migrate = Migrate(app, db)
socketio = SocketIO(app, cors_allowed_origins="*", async_mode='eventlet')

# Models
class User(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    username = db.Column(db.String(80), unique=True, nullable=False)
    password_hash = db.Column(db.String(120), nullable=False)
    push_subscription = db.Column(db.Text, nullable=True)
    
    def set_password(self, password):
        self.password_hash = bcrypt.generate_password_hash(password).decode('utf-8')
    
    def check_password(self, password):
        return bcrypt.check_password_hash(self.password_hash, password)

class Conversation(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    user1_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=False)
    user2_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=False)
    
    user1 = db.relationship('User', foreign_keys=[user1_id])
    user2 = db.relationship('User', foreign_keys=[user2_id])
    
    messages = db.relationship('Message', backref='conversation', lazy=True)

class Message(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    content = db.Column(db.Text, nullable=False)
    message_type = db.Column(db.String(20), default='text')  # 'text' or 'audio'
    timestamp = db.Column(db.DateTime, default=datetime.utcnow)
    user_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=False)
    conversation_id = db.Column(db.Integer, db.ForeignKey('conversation.id'), nullable=False)
    
    user = db.relationship('User', backref=db.backref('messages', lazy=True))

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
                          conversations=conversation_data,
                          vapid_public_key=VAPID_PUBLIC_KEY)

@app.route('/register', methods=['GET', 'POST'])
def register():
    if request.method == 'POST':
        # Use get() instead of direct access to avoid KeyError
        username = request.form.get('username', '').strip()
        password = request.form.get('password', '').strip()
        
        if not username or not password:
            flash('Username and password are required', 'error')
            return render_template('register.html')
        
        if User.query.filter_by(username=username).first():
            flash('Username already exists', 'error')
            return render_template('register.html')
        
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
        # Use get() instead of direct access to avoid KeyError
        username = request.form.get('username', '').strip()
        password = request.form.get('password', '').strip()
        
        if not username or not password:
            flash('Username and password are required', 'error')
            return render_template('login.html')
        
        user = User.query.filter_by(username=username).first()
        if user and user.check_password(password):
            session['user_id'] = user.id
            session['username'] = user.username
            return redirect(url_for('index'))
        
        flash('Invalid username or password', 'error')
        return render_template('login.html')
    
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
    conversation = db.session.get(Conversation, conversation_id)
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
    conversation = db.session.get(Conversation, conversation_id)
    if not conversation or (conversation.user1_id != session['user_id'] and conversation.user2_id != session['user_id']):
        return jsonify({'error': 'Access denied'}), 403
    
    # Check if audio file exists in request
    if 'audio' not in request.files:
        return jsonify({'error': 'No audio file provided'}), 400
    
    audio_file = request.files['audio']
    if audio_file.filename == '':
        return jsonify({'error': 'No audio file selected'}), 400
    
    audio_data = audio_file.read()
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
    
    # Get the other user in the conversation
    other_user = conversation.user1 if conversation.user1_id != session['user_id'] else conversation.user2
    
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
    
    # Send push notification to the other user
    if other_user.push_subscription:
        try:
            send_push_notification(
                other_user.push_subscription,
                f"New voice message from {session['username']}",
                "You received a new voice message",
                conversation_id
            )
        except Exception as e:
            print(f"Failed to send push notification: {e}")
    
    return jsonify({'success': True})

# Save push subscription
@app.route('/save_subscription', methods=['POST'])
def save_subscription():
    if 'user_id' not in session:
        return jsonify({'error': 'Not authenticated'}), 401
    
    subscription = request.json.get('subscription')
    if not subscription:
        return jsonify({'error': 'No subscription provided'}), 400
    
    user = db.session.get(User, session['user_id'])
    if user:
        user.push_subscription = json.dumps(subscription)
        db.session.commit()
        return jsonify({'success': True})
    
    return jsonify({'error': 'User not found'}), 404

# Send push notification function
def send_push_notification(subscription_json, title, body, conversation_id):
    if not subscription_json:
        return
    
    try:
        subscription = json.loads(subscription_json)
        payload = {
            'title': title,
            'body': body,
            'icon': '/static/icons/icon-192x192.png',
            'badge': '/static/icons/icon-72x72.png',
            'data': {
                'conversation_id': conversation_id,
                'url': '/'
            }
        }
        
        webpush(
            subscription_info=subscription,
            data=json.dumps(payload),
            vapid_private_key=VAPID_PRIVATE_KEY,
            vapid_claims=VAPID_CLAIMS
        )
    except WebPushException as e:
        print(f"Web push failed: {e}")
        if e.response and e.response.json():
            print(f"Response: {e.response.json()}")
    except Exception as e:
        print(f"Error sending push notification: {e}")

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
    
    conversation_id = data.get('conversation_id')
    if not conversation_id:
        return
    
    # Check if user is part of this conversation
    conversation = db.session.get(Conversation, conversation_id)
    if conversation and (conversation.user1_id == session['user_id'] or conversation.user2_id == session['user_id']):
        join_room(f'conversation_{conversation_id}')
        emit('joined_conversation', {'conversation_id': conversation_id})

@socketio.on('leave_conversation')
def handle_leave_conversation(data):
    if 'user_id' not in session:
        return
    
    conversation_id = data.get('conversation_id')
    if not conversation_id:
        return
    
    leave_room(f'conversation_{conversation_id}')
    emit('left_conversation', {'conversation_id': conversation_id})

@socketio.on('send_message')
def handle_message(data):
    if 'user_id' not in session:
        return
    
    content = data.get('content', '').strip()
    conversation_id = data.get('conversation_id')
    
    if not content or not conversation_id:
        return
    
    # Check if user is part of this conversation
    conversation = db.session.get(Conversation, conversation_id)
    if not conversation or (conversation.user1_id != session['user_id'] and conversation.user2_id != session['user_id']):
        return
    
    # Get the other user in the conversation
    other_user = conversation.user1 if conversation.user1_id != session['user_id'] else conversation.user2
    
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
    
    # Send push notification to the other user
    if other_user.push_subscription:
        try:
            send_push_notification(
                other_user.push_subscription,
                f"New message from {session['username']}",
                content,
                conversation_id
            )
        except Exception as e:
            print(f"Failed to send push notification: {e}")

@app.route('/check_auth')
def check_auth():
    if 'user_id' in session:
        return jsonify({
            'authenticated': True,
            'user_id': session['user_id'],
            'username': session.get('username', '')
        })
    else:
        return jsonify({'authenticated': False})

@app.route('/delete_account', methods=['POST'])
def delete_account():
    if 'user_id' not in session:
        return jsonify({'error': 'Not authenticated'}), 401
    
    user_id = session['user_id']
    user = db.session.get(User, user_id)
    
    if not user:
        return jsonify({'error': 'User not found'}), 404
    
    # Delete all messages sent by the user
    Message.query.filter_by(user_id=user_id).delete()
    
    # Delete conversations where user is involved
    conversations = Conversation.query.filter(
        (Conversation.user1_id == user_id) | 
        (Conversation.user2_id == user_id)
    ).all()
    
    for conv in conversations:
        # Delete all messages in these conversations
        Message.query.filter_by(conversation_id=conv.id).delete()
        # Delete the conversation itself
        db.session.delete(conv)
    
    # Finally delete the user
    db.session.delete(user)
    db.session.commit()
    
    # Clear session
    session.clear()
    
    return jsonify({'success': True, 'message': 'Account deleted successfully'})

# Serve service worker with correct MIME type
@app.route('/sw.js')
def serve_sw():
    return app.send_static_file('service-worker.js'), 200, {'Content-Type': 'application/javascript'}

# Serve manifest with correct MIME type
@app.route('/manifest.json')
def serve_manifest():
    return app.send_static_file('manifest.json'), 200, {'Content-Type': 'application/json'}

# Serve offline page
@app.route('/offline')
def offline():
    return render_template('offline.html')

if __name__ == '__main__':
    socketio.run(app, debug=True, host='0.0.0.0', port=5000)