class AudioRecorder {
    constructor(recordButton, recordingStatus, onRecordingComplete) {
        this.recordButton = recordButton;
        this.recordingStatus = recordingStatus;
        this.onRecordingComplete = onRecordingComplete;
        this.isRecording = false;
        this.mediaRecorder = null;
        this.audioChunks = [];
        
        this.init();
    }
    
    init() {
        if (!navigator.mediaDevices || !navigator.mediaDevices.getUserMedia) {
            this.recordButton.style.display = 'none';
            console.warn('getUserMedia is not supported in this browser');
            return;
        }
        
        this.recordButton.addEventListener('click', () => {
            if (this.isRecording) {
                this.stopRecording();
            } else {
                this.startRecording();
            }
        });
    }
    
    async startRecording() {
        try {
            const stream = await navigator.mediaDevices.getUserMedia({ audio: true });
            this.mediaRecorder = new MediaRecorder(stream);
            this.audioChunks = [];
            
            this.mediaRecorder.ondataavailable = (event) => {
                if (event.data.size > 0) {
                    this.audioChunks.push(event.data);
                }
            };
            
            this.mediaRecorder.onstop = () => {
                const audioBlob = new Blob(this.audioChunks, { type: 'audio/webm' });
                this.onRecordingComplete(audioBlob);
                
                // Stop all audio tracks
                stream.getTracks().forEach(track => track.stop());
            };
            
            this.mediaRecorder.start();
            this.isRecording = true;
            this.recordButton.textContent = 'Stop Recording';
            this.recordingStatus.style.display = 'inline';
        } catch (error) {
            console.error('Error starting recording:', error);
            alert('Could not access microphone. Please ensure you have granted permission.');
        }
    }
    
    stopRecording() {
        if (this.mediaRecorder && this.isRecording) {
            this.mediaRecorder.stop();
            this.isRecording = false;
            this.recordButton.textContent = 'Record Voice Message';
            this.recordingStatus.style.display = 'none';
        }
    }
}