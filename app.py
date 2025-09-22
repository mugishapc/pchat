# This must be the VERY FIRST import/statement in your application
import eventlet
eventlet.monkey_patch()

# Now import other modules
import os
from flask import Flask, render_template, request, jsonify, redirect, url_for, session, flash, abort, current_app
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
from werkzeug.utils import secure_filename
from flask import send_from_directory


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
app.config['VAPID_PRIVATE_KEY'] = os.getenv('VAPID_PRIVATE_KEY', "LS0tLS1CRUdJTiBQUklWQVRFIEtFWS0tLS0tCk1JR0hBZ0VBTUJNR0J5...")
app.config['VAPID_PUBLIC_KEY'] = os.getenv('VAPID_PUBLIC_KEY', "BBNRtkB_vzJFu0ftQiZx-N1paaNPfdSqelZeS-s2YXCu...")
app.config['VAPID_CLAIMS'] = {"sub": "mailto:mpc0679@gmail.com"}
app.config["UPLOAD_FOLDER"] = os.path.join(os.getcwd(), "static/uploads")

# Ensure upload folder exists
os.makedirs(app.config["UPLOAD_FOLDER"], exist_ok=True)

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

# Track typing status and audio recording status
typing_users = {}
recording_users = {}

# Models
class User(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    username = db.Column(db.String(80), unique=True, nullable=False)
    password_hash = db.Column(db.String(120), nullable=False)
    push_subscription = db.Column(db.Text, nullable=True)
    last_seen = db.Column(db.DateTime, default=datetime.utcnow)
    profile_picture = db.Column(db.String(255), nullable=True)  # file path only
    bio = db.Column(db.String(200), nullable=True)

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
    
    def get_profile_picture_url(self):
        if self.profile_picture:
            # Ensure the URL starts with static/ if it's a relative path
            if self.profile_picture.startswith('uploads/'):
                return f"/static/{self.profile_picture}"
            elif not self.profile_picture.startswith(('http://', 'https://', '/static/')):
                return f"/static/uploads/{self.profile_picture}"
            return self.profile_picture
        return None
    
    def has_active_status(self):
        from datetime import datetime
        return Status.query.filter(
            Status.user_id == self.id,
            Status.expires_at > datetime.utcnow()
        ).first() is not None
    
    def get_active_status(self):
        from datetime import datetime
        return Status.query.filter(
            Status.user_id == self.id,
            Status.expires_at > datetime.utcnow()
        ).first()

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
    is_deleted = db.Column(db.Boolean, default=False)
    
    user = db.relationship('User', backref=db.backref('messages', lazy=True))


# Add this to your existing models (after User model)
class Status(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=False)
    content = db.Column(db.Text, nullable=False)  # text status or image path
    status_type = db.Column(db.String(20), default='text')  # 'text' or 'image'
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    expires_at = db.Column(db.DateTime, nullable=False)
    
    user = db.relationship('User', backref=db.backref('statuses', lazy=True))
    
    def is_expired(self):
        return datetime.utcnow() > self.expires_at

class StatusViewer(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    status_id = db.Column(db.Integer, db.ForeignKey('status.id'), nullable=False)
    user_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=False)
    viewed_at = db.Column(db.DateTime, default=datetime.utcnow)
    
    status = db.relationship('Status', backref=db.backref('viewers', lazy=True))
    user = db.relationship('User', backref=db.backref('viewed_statuses', lazy=True))



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
            'last_message': conv.last_message.content if conv.last_message and not conv.last_message.is_deleted else None,
            'last_message_time': conv.last_message.timestamp if conv.last_message and not conv.last_message.is_deleted else None,
            'unread_count': unread_count,
            'other_user_profile_picture': other_user.get_profile_picture_url(),
            'other_user_bio': other_user.bio
        })
    
    # Get current user data
    current_user = User.query.get(session['user_id'])
    
    return render_template('index.html', 
                          username=session.get('username'),
                          users=users,
                          conversations=conversation_data,
                          current_user=current_user,
                          vapid_public_key=app.config['VAPID_PUBLIC_KEY'])

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
        if user_id in typing_users:
            del typing_users[user_id]
        if user_id in recording_users:
            del recording_users[user_id]
    
    session.clear()
    return redirect(url_for('login'))

# Settings routes - FIXED: Moved these BEFORE the catch-all route
@app.route('/settings')
def settings():
    if 'user_id' not in session:
        return redirect(url_for('login'))
    
    user = User.query.get(session['user_id'])
    return render_template('settings.html', user=user, username=session.get('username'))


@app.route('/edit_profile', methods=['GET', 'POST'])
def edit_profile():
    if 'user_id' not in session:
        return redirect(url_for('login'))
    
    user = User.query.get(session['user_id'])
    
    if request.method == 'POST':
        username = request.form.get('username', '').strip()
        bio = request.form.get('bio', '').strip()
        
        # Check if username is taken by another user
        if username != user.username and User.query.filter_by(username=username).first():
            flash('Username already exists', 'error')
            return render_template('edit_profile.html', user=user)
        
        user.username = username
        user.bio = bio
        
        # Handle profile picture upload
        if 'profile_picture' in request.files:
            profile_pic = request.files['profile_picture']
            if profile_pic and profile_pic.filename != '':
                # Generate unique filename
                timestamp = int(time.time())
                filename = secure_filename(profile_pic.filename)
                filename = f"{timestamp}_{filename}"
                filepath = os.path.join(app.config["UPLOAD_FOLDER"], filename)
                profile_pic.save(filepath)
                user.profile_picture = f"uploads/{filename}"  # store only relative path
        
        db.session.commit()
        session['username'] = username
        
        # Broadcast profile update to ALL connected clients
        socketio.emit('profile_updated', {
            'user_id': user.id,
            'username': user.username,
            'profile_picture': user.get_profile_picture_url(),
            'bio': user.bio
        })
        
        flash('Profile updated successfully', 'success')
        return redirect(url_for('settings'))
    
    return render_template('edit_profile.html', user=user)

@app.route('/uploads/<filename>')
def uploaded_file(filename):
    return send_from_directory(app.config['UPLOAD_FOLDER'], filename)



@app.route('/about_us')
def about_us():
    if 'user_id' not in session:
        return redirect(url_for('login'))
    return render_template('about_us.html', username=session.get('username'))

@app.route('/terms')
def terms():
    if 'user_id' not in session:
        return redirect(url_for('login'))
    return render_template('terms.html', username=session.get('username'))

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
    
    messages = Message.query.filter_by(conversation_id=conversation_id, is_deleted=False).order_by(Message.timestamp.asc()).all()
    message_list = []
    for msg in messages:
        message_list.append({
            'id': msg.id,
            'content': msg.content,
            'type': msg.message_type,
            'username': msg.user.username,
            'user_id': msg.user_id,
            'timestamp': msg.timestamp.isoformat(),
            'is_read': msg.is_read,
            'user_profile_picture': msg.user.get_profile_picture_url()
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

@app.route('/delete_conversation/<int:conversation_id>', methods=['DELETE'])
def delete_conversation(conversation_id):
    if 'user_id' not in session:
        return jsonify({'error': 'Not authenticated'}), 401
    
    # Check if user is part of this conversation
    conversation = db.session.get(Conversation, conversation_id)
    if not conversation or (conversation.user1_id != session['user_id'] and conversation.user2_id != session['user_id']):
        return jsonify({'error': 'Access denied'}), 403
    
    # Mark all messages as deleted
    Message.query.filter_by(conversation_id=conversation_id).update({Message.is_deleted: True})
    
    # Update conversation last message to None
    conversation.last_message_id = None
    db.session.commit()
    
    return jsonify({'success': True})

@app.route('/delete_message/<int:message_id>', methods=['DELETE'])
def delete_message(message_id):
    if 'user_id' not in session:
        return jsonify({'error': 'Not authenticated'}), 401
    
    # Get the message
    message = db.session.get(Message, message_id)
    if not message:
        return jsonify({'error': 'Message not found'}), 404
    
    # Check if user owns this message
    if message.user_id != session['user_id']:
        return jsonify({'error': 'Access denied'}), 403
    
    # Mark message as deleted
    message.is_deleted = True
    db.session.commit()
    
    # Emit event to update clients
    socketio.emit('message_deleted', {
        'message_id': message_id,
        'conversation_id': message.conversation_id
    }, room=f'conversation_{message.conversation_id}')
    
    return jsonify({'success': True})

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
        'is_read': message.is_read,
        'user_profile_picture': User.query.get(session['user_id']).get_profile_picture_url()
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
        'last_message': conversation.last_message.content if conversation.last_message and not conversation.last_message.is_deleted else None,
        'last_message_time': conversation.last_message.timestamp.isoformat() if conversation.last_message and not conversation.last_message.is_deleted else None,
        'unread_count': unread_count,
        'other_user_profile_picture': other_user.get_profile_picture_url(),
        'other_user_bio': other_user.bio
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
            'last_seen': user.last_seen.isoformat() if user.last_seen else None,
            'username': user.username,
            'profile_picture': user.get_profile_picture_url(),
            'bio': user.bio
        }
    
    return jsonify(status_data)

# Get user profile data
@app.route('/user/profile/<int:user_id>')
def get_user_profile(user_id):
    if 'user_id' not in session:
        return jsonify({'error': 'Not authenticated'}), 401
    
    user = User.query.get(user_id)
    if not user:
        return jsonify({'error': 'User not found'}), 404
    
    return jsonify({
        'id': user.id,
        'username': user.username,
        'profile_picture': user.get_profile_picture_url(),
        'bio': user.bio,
        'status': user.get_status(),
        'last_seen': user.last_seen.isoformat() if user.last_seen else None
    })

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
            vapid_private_key=app.config['VAPID_PRIVATE_KEY'],
            vapid_claims=app.config['VAPID_CLAIMS']
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
        
        # Check and emit active status
        active_status = Status.query.filter(
            Status.user_id == user_id,
            Status.expires_at > datetime.utcnow()
        ).first()
        
        if active_status:
            emit('status_updated', {
                'user_id': user_id,
                'username': session['username'],
                'has_status': True,
                'status_type': active_status.status_type
            }, broadcast=True)
        
        # Emit connected confirmation
        emit('connected', {'status': 'connected'})
        
        # Schedule delayed offline check
        def delayed_offline():
            time.sleep(5)  # Wait 5 seconds
            if user_id not in online_users:  # If user didn't reconnect
                socketio.emit(
                    'user_status',
                    {'user_id': user_id, 'status': 'offline'},
                    namespace='/',
                    room=None
                )
        
        socketio.start_background_task(delayed_offline)

@socketio.on('disconnect')
def handle_disconnect():
    if 'user_id' in session:
        user_id = session['user_id']
        if user_id in online_users:
            del online_users[user_id]
        if user_id in typing_users:
            del typing_users[user_id]
        if user_id in recording_users:
            del recording_users[user_id]

        # Notify all users about offline status change after a delay
        def delayed_offline():
            time.sleep(5)  # Wait 5 seconds
            if user_id not in online_users:  # If user didn't reconnect
                # Broadcast to all clients
                socketio.emit(
                    'user_status',
                    {'user_id': user_id, 'status': 'offline'},
                    namespace='/',
                    room=None
                )

        # Start the background task using Eventlet
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

@socketio.on('typing_start')
def handle_typing_start(data):
    if 'user_id' not in session:
        return
    
    conversation_id = data.get('conversation_id')
    if not conversation_id:
        return
    
    # Check if user is part of this conversation
    conversation = db.session.get(Conversation, conversation_id)
    if not conversation or (conversation.user1_id != session['user_id'] and conversation.user2_id != session['user_id']):
        return
    
    # Store typing status
    typing_users[session['user_id']] = {
        'conversation_id': conversation_id,
        'timestamp': time.time()
    }
    
    # Notify other users in the conversation
    emit('user_typing', {
        'user_id': session['user_id'],
        'username': session['username'],
        'conversation_id': conversation_id,
        'is_typing': True
    }, room=f'conversation_{conversation_id}', include_self=False)

@socketio.on('typing_stop')
def handle_typing_stop(data):
    if 'user_id' not in session:
        return
    
    conversation_id = data.get('conversation_id')
    if not conversation_id:
        return
    
    # Check if user is part of this conversation
    conversation = db.session.get(Conversation, conversation_id)
    if not conversation or (conversation.user1_id != session['user_id'] and conversation.user2_id != session['user_id']):
        return
    
    # Remove typing status
    if session['user_id'] in typing_users:
        del typing_users[session['user_id']]
    
    # Notify other users in the conversation
    emit('user_typing', {
        'user_id': session['user_id'],
        'username': session['username'],
        'conversation_id': conversation_id,
        'is_typing': False
    }, room=f'conversation_{conversation_id}', include_self=False)

@socketio.on('recording_start')
def handle_recording_start(data):
    if 'user_id' not in session:
        return
    
    conversation_id = data.get('conversation_id')
    if not conversation_id:
        return
    
    # Check if user is part of this conversation
    conversation = db.session.get(Conversation, conversation_id)
    if not conversation or (conversation.user1_id != session['user_id'] and conversation.user2_id != session['user_id']):
        return
    
    # Store recording status
    recording_users[session['user_id']] = {
        'conversation_id': conversation_id,
        'timestamp': time.time()
    }
    
    # Notify other users in the conversation
    emit('user_recording', {
        'user_id': session['user_id'],
        'username': session['username'],
        'conversation_id': conversation_id,
        'is_recording': True
    }, room=f'conversation_{conversation_id}', include_self=False)

@socketio.on('recording_stop')
def handle_recording_stop(data):
    if 'user_id' not in session:
        return
    
    conversation_id = data.get('conversation_id')
    if not conversation_id:
        return
    
    # Check if user is part of this conversation
    conversation = db.session.get(Conversation, conversation_id)
    if not conversation or (conversation.user1_id != session['user_id'] and conversation.user2_id != session['user_id']):
        return
    
    # Remove recording status
    if session['user_id'] in recording_users:
        del recording_users[session['user_id']]
    
    # Notify other users in the conversation
    emit('user_recording', {
        'user_id': session['user_id'],
        'username': session['username'],
        'conversation_id': conversation_id,
        'is_recording': False
    }, room=f'conversation_{conversation_id}', include_self=False)

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
    
    # Remove typing status if it exists
    if session['user_id'] in typing_users:
        del typing_users[session['user_id']]
        # Notify other users that typing has stopped
        emit('user_typing', {
            'user_id': session['user_id'],
            'username': session['username'],
            'conversation_id': conversation_id,
            'is_typing': False
        }, room=f'conversation_{conversation_id}', include_self=False)
    
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
    
    # Get current user's profile picture
    current_user = User.query.get(session['user_id'])
    
    # Broadcast to conversation room
    emit('new_message', {
        'id': message.id,
        'content': content,
        'type': 'text',
        'username': session['username'],
        'user_id': session['user_id'],
        'timestamp': message.timestamp.isoformat(),
        'conversation_id': conversation_id,
        'is_read': message.is_read,
        'user_profile_picture': current_user.get_profile_picture_url()
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

@socketio.on('message_deleted')
def handle_message_deleted(data):
    if 'user_id' not in session:
        return
    
    message_id = data.get('message_id')
    conversation_id = data.get('conversation_id')
    
    # Check if user is part of this conversation
    conversation = db.session.get(Conversation, conversation_id)
    if not conversation or (conversation.user1_id != session['user_id'] and conversation.user2_id != session['user_id']):
        return
    
    # Broadcast to conversation room
    emit('message_deleted', {
        'message_id': message_id,
        'conversation_id': conversation_id
    }, room=f'conversation_{conversation_id}')

# Handle profile updates
@socketio.on('profile_updated')
def handle_profile_updated(data):
    # This will broadcast the profile update to all connected clients
    emit('profile_updated', data, broadcast=True)

@app.route('/check_auth')
def check_auth():
    if 'user_id' in session:
        user = User.query.get(session['user_id'])
        return jsonify({
            'authenticated': True,
            'user_id': session['user_id'],
            'username': session.get('username', ''),
            'profile_picture': user.get_profile_picture_url() if user else None,
            'bio': user.bio if user else None
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

@app.route('/chat/<int:conversation_id>')
def chat(conversation_id):
    if 'user_id' not in session:
        return redirect(url_for('login'))
    
    user_id = session['user_id']
    
    # Verify user has access to this conversation
    conversation = Conversation.query.get_or_404(conversation_id)
    if user_id not in [conversation.user1_id, conversation.user2_id]:
        abort(403)
    
    # Determine the other user
    if conversation.user1_id == user_id:
        other_user = User.query.get(conversation.user2_id)
    else:
        other_user = User.query.get(conversation.user1_id)
    
    return render_template('chat.html', 
                         conversation=conversation,
                         other_user=other_user,
                         current_user=User.query.get(user_id),
                         vapid_public_key=app.config['VAPID_PUBLIC_KEY'])

# FIXED: This catch-all route should be the VERY LAST route
@app.route('/<path:path>')
def catch_all(path):
    known_routes = ['login', 'register', 'chat', 'offline', 'settings', 'edit_profile', 'about_us', 'terms']
    if path in known_routes:
        return redirect(url_for(path))
    return render_template('base.html')


# Status routes

@app.route('/status')
def status_page():
    if 'user_id' not in session:
        return redirect(url_for('login'))
    
    # Get current user
    current_user = User.query.get(session['user_id'])
    current_time = datetime.utcnow()
    
    # Get all users who have active statuses (except current user)
    users_with_status = User.query.filter(
        User.id != session['user_id']
    ).all()
    
    # Filter users who have active statuses
    users_with_active_status = []
    for user in users_with_status:
        active_status = Status.query.filter(
            Status.user_id == user.id,
            Status.expires_at > current_time
        ).first()
        if active_status:
            users_with_active_status.append(user)
    
    # Get current user's active status
    current_user_status = Status.query.filter(
        Status.user_id == session['user_id'],
        Status.expires_at > current_time
    ).order_by(Status.created_at.desc()).first()
    
    # Get viewed statuses for current user
    viewed_status_ids = []
    if current_user:
        viewed_statuses = StatusViewer.query.filter_by(user_id=current_user.id).all()
        viewed_status_ids = [sv.status_id for sv in viewed_statuses]
    
    return render_template('status.html', 
                         username=session.get('username'),
                         current_user=current_user,
                         users_with_status=users_with_active_status,
                         current_user_status=current_user_status,
                         viewed_status_ids=viewed_status_ids,
                         current_time=current_time)



# Status upload route - ONLY ONE FUNCTION WITH THIS NAME
@app.route('/status/upload', methods=['POST'])
def upload_status():
    try:
        if 'user_id' not in session:
            return jsonify({'error': 'Not authenticated'}), 401

        status_type = request.form.get('type', 'text')
        content = request.form.get('content', '').strip()

        if not content:
            return jsonify({'error': 'Status content is required'}), 400

        # Delete existing status
        Status.query.filter_by(user_id=session['user_id']).delete()

        expires_at = datetime.utcnow() + timedelta(hours=24)

        if status_type == 'image':
            if 'image' not in request.files:
                return jsonify({'error': 'No image file provided'}), 400

            image_file = request.files['image']
            if image_file and image_file.filename != '':
                timestamp = int(time.time())
                filename = secure_filename(image_file.filename)
                filename = f"status_{timestamp}_{filename}"
                filepath = os.path.join(app.config["UPLOAD_FOLDER"], filename)
                os.makedirs(os.path.dirname(filepath), exist_ok=True)
                image_file.save(filepath)
                content = f"uploads/{filename}"

        status = Status(
            user_id=session['user_id'],
            content=content,
            status_type=status_type,
            expires_at=expires_at
        )

        db.session.add(status)
        db.session.commit()

        socketio.emit('status_updated', {
            'user_id': session['user_id'],
            'username': session.get('username'),
            'has_status': True,
            'status_type': status_type
        }, broadcast=True)

        return jsonify({'success': True, 'status_id': status.id})

    except Exception as e:
        # Return JSON even on error
        return jsonify({'error': str(e)}), 500



@app.route('/status/viewers/<int:status_id>')
def get_status_viewers(status_id):
    if 'user_id' not in session:
        return jsonify({'error': 'Not authenticated'}), 401
    
    status = Status.query.get_or_404(status_id)
    
    # Check if current user owns the status
    if status.user_id != session['user_id']:
        return jsonify({'error': 'Access denied'}), 403
    
    viewers = []
    for viewer in status.viewers:
        viewers.append({
            'username': viewer.user.username,
            'profile_picture': viewer.user.get_profile_picture_url(),
            'viewed_at': viewer.viewed_at.isoformat()
        })
    
    return jsonify(viewers)

@app.route('/status/delete', methods=['POST'])
def delete_status():
    if 'user_id' not in session:
        return jsonify({'error': 'Not authenticated'}), 401
    
    try:
        # Delete user's status
        Status.query.filter_by(user_id=session['user_id']).delete()
        db.session.commit()
        
        # Broadcast status removal
        socketio.emit('status_updated', {
            'user_id': session['user_id'],
            'username': session['username'],
            'has_status': False
        }, broadcast=True)
        
        return jsonify({'success': True})
    
    except Exception as e:
        return jsonify({'error': str(e)}), 500

@app.route('/status/user/<int:user_id>')
def get_user_status(user_id):
    if 'user_id' not in session:
        return jsonify({'error': 'Not authenticated'}), 401
    
    # Get active status for the user
    status = Status.query.filter(
        Status.user_id == user_id,
        Status.expires_at > datetime.utcnow()
    ).order_by(Status.created_at.desc()).first()
    
    if not status:
        return jsonify({'error': 'No active status'}), 404
    
    # Check if current user has viewed this status
    has_viewed = StatusViewer.query.filter_by(
        status_id=status.id,
        user_id=session['user_id']
    ).first() is not None
    
    # Mark as viewed if not already viewed
    if not has_viewed:
        viewer = StatusViewer(
            status_id=status.id,
            user_id=session['user_id']
        )
        db.session.add(viewer)
        db.session.commit()
        has_viewed = True  # Update after adding
    
    status_data = {
        'id': status.id,
        'content': status.content,
        'type': status.status_type,
        'created_at': status.created_at.isoformat(),
        'expires_at': status.expires_at.isoformat(),
        'user': {
            'id': status.user.id,
            'username': status.user.username,
            'profile_picture': status.user.get_profile_picture_url()
        },
        'has_viewed': has_viewed,
        'viewer_count': StatusViewer.query.filter_by(status_id=status.id).count()
    }
    
    # If it's an image status, provide the full URL
    if status.status_type == 'image' and status.content.startswith('uploads/'):
        status_data['content_url'] = f"/static/{status.content}"
    
    return jsonify(status_data)


if __name__ == '__main__':
    socketio.run(app, debug=True, host='0.0.0.0', port=5000)