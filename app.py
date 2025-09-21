# This must be the VERY FIRST import/statement in your application
import eventlet
eventlet.monkey_patch()

# Now import other modules
import os
from flask import Flask, render_template, request, jsonify, redirect, url_for, session, flash
from flask_socketio import SocketIO, emit, join_room, leave_room
from flask_sqlalchemy import SQLAlchemy
from flask_bcrypt import Bcrypt
from datetime import datetime, timedelta
import base64
from dotenv import load_dotenv
from flask_migrate import Migrate
import json
from pywebpush import webpush, WebPushException
import time

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
VAPID_PRIVATE_KEY = os.getenv('LS0tLS1CRUdJTiBQUklWQVRFIEtFWS0tLS0tCk1JR0hBZ0VBTUJNR0J5cUdTTTQ5QWdFR0NDcUdTTTQ5QXdFSEJHMHdhd0lCQVFRZ1RvQXV1N0pLTnZWSTJLWTAKaHdaOFg5S21PL0tlUXNWdFN3NExZSm5TRE9DaFJBTkNBQVFUVWJaQWY3OHlSYnRIN1VJbWNmamRhV21qVDMzVQpxbnBXWGt2ck5tRndy1JSRnFBOUJmd0ZUZ2xuSjQzV1J4emFJc0ZnZkdlWUluQzVpY2ZEM3FuNwotLS0tLUVORCBQUklWQVRFIEtFWS0tLS0tCg==')
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

# Track online users
online_users = {}
last_activity_times = {}

# Models
class User(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    username = db.Column(db.String(80), unique=True, nullable=False)
    password_hash = db.Column(db.String(120), nullable=False)
    push_subscription = db.Column(db.Text, nullable=True)
    last_seen = db.Column(db.DateTime, default=datetime.utcnow)
    
    def set_password(self, password):
        self.password_hash = bcrypt.generate_password_hash(password).decode('utf-8')
    
    def check_password(self, password):
        return bcrypt.check_password_hash(self.password_hash, password)
    
    def is_online(self):
        return self.id in online_users
    
    def get_status(self):
        if self.is_online():
            return "online"
        
        # Check if user was recently active (within 30 seconds)
        if self.last_seen and (datetime.utcnow() - self.last_seen) < timedelta(seconds=30):
            return "recently online"
        
        return "offline"

class Conversation(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    user1_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=False)
    user2_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=False)
    last_message_id = db.Column(db.Integer, db.ForeignKey('message.id'), nullable=True)

    # ✅ defaults fixed
    unread_count_user1 = db.Column(
        db.Integer,
        default=0,
        nullable=False,
        server_default="0"
    )
    unread_count_user2 = db.Column(
        db.Integer,
        default=0,
        nullable=False,
        server_default="0"
    )

    user1 = db.relationship('User', foreign_keys=[user1_id])
    user2 = db.relationship('User', foreign_keys=[user2_id])
    last_message = db.relationship('Message', foreign_keys=[last_message_id])

    # 👇 Tell SQLAlchemy to only use `Message.conversation_id`
    messages = db.relationship(
        'Message',
        backref='conversation',
        lazy=True,
        order_by="desc(Message.timestamp)",
        foreign_keys="Message.conversation_id"
    )


class Message(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    content = db.Column(db.Text, nullable=False)
    message_type = db.Column(db.String(20), default='text')  # 'text' or 'audio'
    timestamp = db.Column(db.DateTime, default=datetime.utcnow)
    user_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=False)
    conversation_id = db.Column(db.Integer, db.ForeignKey('conversation.id'), nullable=False)
    is_read = db.Column(db.Boolean, default=False)
    
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

# Update user's last seen timestamp
def update_user_activity(user_id):
    user = User.query.get(user_id)
    if user:
        user.last_seen = datetime.utcnow()
        db.session.commit()

# Authentication routes
@app.route('/')
def index():
    if 'user_id' not in session:
        return redirect(url_for('login'))
    
    # Update user activity
    update_user_activity(session['user_id'])
    
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
        
        # Get unread count for current user
        unread_count = conv.unread_count_user1 if conv.user1_id == session['user_id'] else conv.unread_count_user2
        
        conversation_data.append({
            'id': conv.id,
            'other_user_id': other_user.id,
            'other_username': other_user.username,
            'last_message': conv.last_message.content if conv.last_message else None,
            'last_message_time': conv.last_message.timestamp if conv.last_message else None,
            'unread_count': unread_count
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
    if 'user_id' in session:
        user_id = session['user_id']
        if user_id in online_users:
            del online_users[user_id]
        if user_id in last_activity_times:
            del last_activity_times[user_id]
    
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
    
    # Mark messages as read
    if conversation.user1_id == session['user_id']:
        conversation.unread_count_user1 = 0
    else:
        conversation.unread_count_user2 = 0
    
    # Update read status for messages
    Message.query.filter_by(conversation_id=conversation_id, is_read=False).update({Message.is_read: True})
    db.session.commit()
    
    messages = Message.query.filter_by(conversation_id=conversation_id).order_by(Message.timestamp.asc()).all()
    message_list = []
    for msg in messages:
        message_list.append({
            'id': msg.id,
            'content': msg.content,
            'type': msg.message_type,
            'username': msg.user.username,
            'user_id': msg.user_id,
            'timestamp': msg.timestamp.isoformat(),
            'is_read': msg.is_read
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
    
    # Update conversation last message and unread count
    conversation.last_message_id = message.id
    if conversation.user1_id == session['user_id']:
        conversation.unread_count_user2 += 1
    else:
        conversation.unread_count_user1 += 1
    
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
        'conversation_id': conversation_id,
        'is_read': message.is_read
    }, room=f'conversation_{conversation_id}')
    
    # Update conversation list for both users
    update_conversation_list(conversation, session['user_id'])
    update_conversation_list(conversation, other_user.id)
    
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

# Update conversation list for a user
def update_conversation_list(conversation, user_id):
    other_user = conversation.user1 if conversation.user1_id != user_id else conversation.user2
    
    # Get unread count for the user
    unread_count = conversation.unread_count_user1 if conversation.user1_id == user_id else conversation.unread_count_user2
    
    socketio.emit('update_conversation', {
        'conversation_id': conversation.id,
        'other_user_id': other_user.id,
        'other_username': other_user.username,
        'last_message': conversation.last_message.content if conversation.last_message else None,
        'last_message_time': conversation.last_message.timestamp.isoformat() if conversation.last_message else None,
        'unread_count': unread_count
    }, room=f'user_{user_id}')

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

# Get online status of users
@app.route('/users/status')
def get_users_status():
    if 'user_id' not in session:
        return jsonify({'error': 'Not authenticated'}), 401
    
    users = User.query.filter(User.id != session['user_id']).all()
    status_data = {}
    
    for user in users:
        status_data[user.id] = {
            'online': user.is_online(),
            'status': user.get_status(),
            'last_seen': user.last_seen.isoformat() if user.last_seen else None
        }
    
    return jsonify(status_data)

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
        user_id = session['user_id']
        # Join user's personal room for notifications
        join_room(f'user_{user_id}')
        
        # Mark user as online
        online_users[user_id] = True
        last_activity_times[user_id] = time.time()
        
        # Update user's last seen timestamp
        update_user_activity(user_id)
        
        # Notify all users about online status change
        emit('user_status', {
            'user_id': user_id,
            'status': 'online'
        }, broadcast=True)
        
        emit('connected', {'status': 'connected'})

@socketio.on('disconnect')
def handle_disconnect():
    if 'user_id' in session:
        user_id = session['user_id']
        if user_id in online_users:
            del online_users[user_id]
        
        # Notify all users about offline status change after a delay
        # to account for page refreshes
        def delayed_offline():
            time.sleep(5)  # Wait 5 seconds
            if user_id not in online_users:  # If user didn't reconnect
                emit('user_status', {
                    'user_id': user_id,
                    'status': 'offline'
                }, broadcast=True)
        
        socketio.start_background_task(delayed_offline)

@socketio.on('user_activity')
def handle_user_activity():
    if 'user_id' in session:
        user_id = session['user_id']
        last_activity_times[user_id] = time.time()
        
        # Update user's last seen timestamp in database periodically
        if user_id in online_users and time.time() - last_activity_times.get(user_id, 0) > 30:
            update_user_activity(user_id)

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
    
    # Update conversation last message and unread count
    conversation.last_message_id = message.id
    if conversation.user1_id == session['user_id']:
        conversation.unread_count_user2 += 1
    else:
        conversation.unread_count_user1 += 1
    
    db.session.commit()
    
    # Broadcast to conversation room
    emit('new_message', {
        'id': message.id,
        'content': content,
        'type': 'text',
        'username': session['username'],
        'user_id': session['user_id'],
        'timestamp': message.timestamp.isoformat(),
        'conversation_id': conversation_id,
        'is_read': message.is_read
    }, room=f'conversation_{conversation_id}')
    
    # Update conversation list for both users
    update_conversation_list(conversation, session['user_id'])
    update_conversation_list(conversation, other_user.id)
    
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
    
    # Remove from online users
    if user_id in online_users:
        del online_users[user_id]
    
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