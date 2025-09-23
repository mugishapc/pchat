class AudioRecorder {
    constructor(recordButton, recordingStatus, onRecordingComplete) {
        this.recordButton = recordButton;
        this.recordingStatus = recordingStatus;
        this.onRecordingComplete = onRecordingComplete;
        this.isRecording = false;
        this.mediaRecorder = null;
        this.audioChunks = [];
        this.recordingStartTime = null;
        this.timerInterval = null;
        
        this.init();
    }
    
    init() {
        if (!navigator.mediaDevices || !navigator.mediaDevices.getUserMedia) {
            this.recordButton.style.display = 'none';
            console.warn('getUserMedia is not supported in this browser');
            return;
        }
        
        // Touch events for mobile support
        this.recordButton.addEventListener('mousedown', (e) => this.startRecording(e));
        this.recordButton.addEventListener('touchstart', (e) => {
            e.preventDefault();
            this.startRecording(e);
        });
        
        document.addEventListener('mouseup', (e) => this.stopRecording(e));
        document.addEventListener('touchend', (e) => {
            e.preventDefault();
            this.stopRecording(e);
        });
        
        // Prevent context menu on long press
        this.recordButton.addEventListener('contextmenu', (e) => e.preventDefault());
    }
    
    async startRecording(e) {
        if (this.isRecording) return;
        
        try {
            const stream = await navigator.mediaDevices.getUserMedia({ 
                audio: {
                    echoCancellation: true,
                    noiseSuppression: true,
                    sampleRate: 44100
                } 
            });
            
            this.mediaRecorder = new MediaRecorder(stream, {
                mimeType: 'audio/webm;codecs=opus'
            });
            
            this.audioChunks = [];
            this.recordingStartTime = Date.now();
            
            this.mediaRecorder.ondataavailable = (event) => {
                if (event.data.size > 0) {
                    this.audioChunks.push(event.data);
                }
            };
            
            this.mediaRecorder.onstop = () => {
                const recordingDuration = Date.now() - this.recordingStartTime;
                
                // Only send if recording was longer than 1 second
                if (recordingDuration > 1000 && this.audioChunks.length > 0) {
                    const audioBlob = new Blob(this.audioChunks, { type: 'audio/webm' });
                    this.onRecordingComplete(audioBlob, recordingDuration);
                }
                
                // Stop all audio tracks
                stream.getTracks().forEach(track => track.stop());
            };
            
            this.mediaRecorder.start(100); // Collect data every 100ms
            this.isRecording = true;
            
            // Update UI
            this.recordButton.classList.add('recording');
            this.recordingStatus.style.display = 'flex';
            
            // Start timer
            this.startTimer();
            
            // Add visual feedback
            this.recordButton.innerHTML = '🎙️ Recording...';
            
        } catch (error) {
            console.error('Error starting recording:', error);
            alert('Could not access microphone. Please ensure you have granted permission.');
        }
    }
    
    stopRecording(e) {
        if (!this.isRecording || !this.mediaRecorder) return;
        
        // Check if this is a valid stop (not dragging away from button)
        if (e && this.recordButton.contains(e.target)) {
            // Normal stop - send recording
            this.mediaRecorder.stop();
        } else {
            // Cancel recording
            this.mediaRecorder.stop();
            this.audioChunks = []; // Clear chunks to prevent sending
        }
        
        this.isRecording = false;
        this.recordButton.classList.remove('recording');
        this.recordingStatus.style.display = 'none';
        this.recordButton.innerHTML = '🎤';
        
        // Clear timer
        this.stopTimer();
    }
    
    startTimer() {
        this.stopTimer();
        let seconds = 0;
        
        this.timerInterval = setInterval(() => {
            seconds++;
            const minutes = Math.floor(seconds / 60);
            const remainingSeconds = seconds % 60;
            
            this.recordingStatus.textContent = 
                `${minutes.toString().padStart(2, '0')}:${remainingSeconds.toString().padStart(2, '0')}`;
        }, 1000);
    }
    
    stopTimer() {
        if (this.timerInterval) {
            clearInterval(this.timerInterval);
            this.timerInterval = null;
        }
    }
    
    // Cancel recording without sending
    cancelRecording() {
        if (this.isRecording && this.mediaRecorder) {
            this.mediaRecorder.stop();
            this.audioChunks = [];
            this.isRecording = false;
            this.recordButton.classList.remove('recording');
            this.recordingStatus.style.display = 'none';
            this.recordButton.innerHTML = '🎤';
            this.stopTimer();
        }
    }
}