document.addEventListener('DOMContentLoaded', function() {
    const socket = io();
    const messagesContainer = document.getElementById('chat-messages');
    const messageInput = document.getElementById('message-input');
    const sendButton = document.getElementById('send-btn');
    const recordButton = document.getElementById('record-btn');
    const recordingStatus = document.getElementById('recording-status');
    
    let currentUser = '';
    
    // Load previous messages
    fetch('/messages')
        .then(response => response.json())
        .then(messages => {
            messages.forEach(message => {
                addMessageToChat(message);
            });
            scrollToBottom();
        });
    
    // Get current username from the page
    const userInfoElement = document.querySelector('.user-info span');
    if (userInfoElement) {
        const text = userInfoElement.textContent;
        currentUser = text.replace('Welcome, ', '').trim();
    }
    
    // Socket event handlers
    socket.on('connect', function() {
        console.log('Connected to server');
    });
    
    socket.on('new_message', function(data) {
        addMessageToChat(data);
        scrollToBottom();
    });
    
    socket.on('user_joined', function(data) {
        addSystemMessage(`${data.username} joined the chat`);
    });
    
    // Send message handler
    function sendMessage() {
        const content = messageInput.value.trim();
        if (content) {
            socket.emit('send_message', { content: content });
            messageInput.value = '';
        }
    }
    
    sendButton.addEventListener('click', sendMessage);
    
    messageInput.addEventListener('keypress', function(e) {
        if (e.key === 'Enter') {
            sendMessage();
        }
    });
    
    // Add message to chat UI
    function addMessageToChat(message) {
        const messageElement = document.createElement('div');
        messageElement.classList.add('message');
        
        const isCurrentUser = message.username === currentUser;
        messageElement.classList.add(isCurrentUser ? 'sent' : 'received');
        
        let messageContent = '';
        
        if (message.type === 'text') {
            messageContent = `
                <div class="message-content">${message.content}</div>
            `;
        } else if (message.type === 'audio') {
            messageContent = `
                <div class="message-content audio-message">
                    <audio controls>
                        <source src="${message.content}" type="audio/webm">
                        Your browser does not support the audio element.
                    </audio>
                </div>
            `;
        }
        
        messageElement.innerHTML = `
            ${messageContent}
            <div class="message-info">
                <span class="username">${message.username}</span>
                <span class="timestamp">${formatTime(message.timestamp)}</span>
            </div>
        `;
        
        messagesContainer.appendChild(messageElement);
    }
    
    // Add system message
    function addSystemMessage(content) {
        const messageElement = document.createElement('div');
        messageElement.classList.add('message', 'system');
        messageElement.innerHTML = `
            <div class="message-content system-message">
                ${content}
            </div>
        `;
        messagesContainer.appendChild(messageElement);
        scrollToBottom();
    }
    
    // Format timestamp
    function formatTime(timestamp) {
        const date = new Date(timestamp);
        return date.toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' });
    }
    
    // Scroll to bottom of chat
    function scrollToBottom() {
        messagesContainer.scrollTop = messagesContainer.scrollHeight;
    }
    
    // Initialize audio recorder
    if (window.AudioRecorder) {
        const audioRecorder = new AudioRecorder(
            recordButton,
            recordingStatus,
            function(blob) {
                // Upload the audio blob to the server
                const formData = new FormData();
                formData.append('audio', blob, 'audio-message.webm');
                
                fetch('/upload_audio', {
                    method: 'POST',
                    body: formData
                })
                .then(response => response.json())
                .then(data => {
                    if (!data.success) {
                        console.error('Failed to upload audio');
                    }
                })
                .catch(error => {
                    console.error('Error uploading audio:', error);
                });
            }
        );
    } else {
        recordButton.style.display = 'none';
        console.warn('Audio recording not supported in this browser');
    }
});



// Add this to your existing JavaScript code

// Detect mobile devices
function isMobileDevice() {
    return /Android|webOS|iPhone|iPad|iPod|BlackBerry|IEMobile|Opera Mini/i.test(navigator.userAgent);
}

// Handle mobile input focus
if (isMobileDevice()) {
    const messageInput = document.getElementById('message-input');
    const chatContainer = document.querySelector('.chat-container');
    
    if (messageInput) {
        messageInput.addEventListener('focus', function() {
            document.body.classList.add('keyboard-visible');
            chatContainer.classList.add('mobile-input-focus');
            
            // Scroll to bottom when input is focused
            setTimeout(() => {
                const messagesContainer = document.querySelector('.chat-messages');
                if (messagesContainer) {
                    messagesContainer.scrollTop = messagesContainer.scrollHeight;
                }
            }, 300);
        });
        
        messageInput.addEventListener('blur', function() {
            document.body.classList.remove('keyboard-visible');
            chatContainer.classList.remove('mobile-input-focus');
        });
    }
    
    // Adjust viewport height for mobile browsers
    function setVH() {
        const vh = window.innerHeight * 0.01;
        document.documentElement.style.setProperty('--vh', `${vh}px`);
    }
    
    setVH();
    window.addEventListener('resize', setVH);
    window.addEventListener('orientationchange', setVH);
}
