from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from faster_whisper import WhisperModel
import uvicorn
import asyncio
import io
import wave
import numpy as np
import tempfile
import os
import json
from collections import deque
import threading
import time

app = FastAPI(title="Real-time Audio Transcription API", version="1.0.0")

# Initialize the Whisper model with a small model optimized for CPU
# Using "tiny" model for CPU - fastest for low latency
model = WhisperModel("base", device="cpu", compute_type="int8")

# Audio buffer settings
SAMPLE_RATE = 16000  # Whisper expects 16kHz
CHUNK_DURATION = 1  # Process every 1.5 seconds for low latency
OVERLAP_DURATION = 0  # 0.5 second overlap to catch words at boundaries
CHUNK_SIZE = int(SAMPLE_RATE * CHUNK_DURATION)  # Samples per chunk

class AudioBuffer:
    """Manages audio buffer for real-time transcription"""
    def __init__(self, sample_rate=16000):
        self.sample_rate = sample_rate
        self.buffer = deque(maxlen=int(sample_rate * 10))  # Max 10 seconds buffer
        self.lock = threading.Lock()
        self.last_process_time = time.time()
        self.last_processed_samples = 0  # Track how much we've processed
        self.process_interval = CHUNK_DURATION
        
    def add_audio(self, audio_data: bytes):
        """Add audio bytes to buffer"""
        with self.lock:
            # Convert bytes to numpy array (assuming 16-bit PCM)
            audio_array = np.frombuffer(audio_data, dtype=np.int16).astype(np.float32) / 32768.0
            self.buffer.extend(audio_array)
    
    def get_chunk(self, duration: float = None) -> np.ndarray:
        """Get audio chunk for processing with overlap"""
        if duration is None:
            duration = CHUNK_DURATION
        
        samples_needed = int(self.sample_rate * duration)
        overlap_samples = int(self.sample_rate * OVERLAP_DURATION)
        
        with self.lock:
            # Check if we have enough new audio since last processing
            new_audio_needed = samples_needed - overlap_samples
            if len(self.buffer) < self.last_processed_samples + new_audio_needed:
                return None
            
            # Get chunk starting from overlap before last processed position
            start_pos = max(0, self.last_processed_samples - overlap_samples)
            end_pos = start_pos + samples_needed
            
            if end_pos > len(self.buffer):
                return None
            
            # Convert deque to list and extract chunk
            buffer_list = list(self.buffer)
            chunk = np.array(buffer_list[start_pos:end_pos])
            
            # Update last processed position (advance by chunk minus overlap)
            self.last_processed_samples = end_pos - overlap_samples
            
            return chunk
    
    def has_enough_audio(self) -> bool:
        """Check if we have enough audio for a new chunk"""
        with self.lock:
            samples_needed = int(self.sample_rate * CHUNK_DURATION)
            overlap_samples = int(self.sample_rate * OVERLAP_DURATION)
            new_audio_needed = samples_needed - overlap_samples
            return len(self.buffer) >= self.last_processed_samples + new_audio_needed
    
    def clear(self):
        """Clear the buffer"""
        with self.lock:
            self.buffer.clear()
            self.last_processed_samples = 0
    
    def size(self) -> int:
        """Get current buffer size in samples"""
        with self.lock:
            return len(self.buffer)

def save_audio_to_wav(audio_data: np.ndarray, sample_rate: int = 16000) -> str:
    """Save numpy audio array to temporary WAV file"""
    tmp_file = tempfile.NamedTemporaryFile(delete=False, suffix='.wav')
    tmp_file_path = tmp_file.name
    tmp_file.close()
    
    # Ensure audio is in the right format
    if audio_data.dtype != np.float32:
        audio_data = audio_data.astype(np.float32)
    
    # Normalize if needed
    if np.abs(audio_data).max() > 1.0:
        audio_data = audio_data / np.abs(audio_data).max()
    
    # Convert to int16 for WAV
    audio_int16 = (audio_data * 32767.0).astype(np.int16)
    
    with wave.open(tmp_file_path, 'wb') as wav_file:
        wav_file.setnchannels(1)  # Mono
        wav_file.setsampwidth(2)  # 16-bit
        wav_file.setframerate(sample_rate)
        wav_file.writeframes(audio_int16.tobytes())
    
    return tmp_file_path

def process_audio_chunk(audio_chunk: np.ndarray) -> dict:
    """Process a chunk of audio and return transcription"""
    if audio_chunk is None or len(audio_chunk) == 0:
        return None
    
    # Save to temporary WAV file
    tmp_file_path = None
    try:
        tmp_file_path = save_audio_to_wav(audio_chunk, SAMPLE_RATE)
        
        # Transcribe the audio chunk with optimized settings for low latency
        segments, info = model.transcribe(
            tmp_file_path,
            beam_size=1,  # Minimum beam for fastest processing
            language="en",  # Set to None for auto-detection
            vad_filter=True,  # Use VAD to skip silence quickly
            vad_parameters=dict(
                min_silence_duration_ms=250,  # Short silence threshold for fast detection
                threshold=0.5  # Moderate threshold
            ),
            condition_on_previous_text=False,  # Faster processing (no context dependency)
            compression_ratio_threshold=2.4,
            log_prob_threshold=-1.0,
            no_speech_threshold=0.5,  # Lower threshold to detect speech faster
            initial_prompt=None,  # No prompt for faster processing
            word_timestamps=False  # Disable word timestamps for speed
        )
        
        # Collect segments
        transcription_text = ""
        segments_list = []
        
        for segment in segments:
            transcription_text += segment.text + " "
            segments_list.append({
                "start": segment.start,
                "end": segment.end,
                "text": segment.text
            })
        
        result = {
            "text": transcription_text.strip(),
            "segments": segments_list,
            "language": info.language if hasattr(info, 'language') else None
        }
        
        return result if result["text"] else None
        
    except Exception as e:
        print(f"Error processing audio chunk: {e}")
        return None
    finally:
        # Clean up temporary file
        if tmp_file_path and os.path.exists(tmp_file_path):
            try:
                os.unlink(tmp_file_path)
            except (PermissionError, OSError):
                pass

@app.get("/")
async def root():
    return {"message": "Real-time Audio Transcription API", "status": "ready", "websocket_endpoint": "/ws"}

@app.websocket("/ws")
async def websocket_endpoint(websocket: WebSocket):
    """WebSocket endpoint for real-time audio transcription"""
    await websocket.accept()
    
    audio_buffer = AudioBuffer(SAMPLE_RATE)
    process_task = None
    
    # Accumulate all audio for saving to file
    accumulated_audio = []  # List of numpy arrays
    audio_file_path = None
    
    try:
        # Send initial connection message
        await websocket.send_json({
            "type": "connected",
            "message": "WebSocket connected. Start sending audio chunks.",
            "sample_rate": SAMPLE_RATE,
            "format": "16-bit PCM, mono"
        })
        
        async def process_audio_periodically():
            """Periodically process audio chunks for low latency"""
            processing = False  # Prevent overlapping processing
            
            while True:
                # Check more frequently for low latency (every 200ms)
                await asyncio.sleep(0.2)
                
                # Skip if already processing or not enough audio
                if processing or not audio_buffer.has_enough_audio():
                    continue
                
                # Get chunk if we have enough audio
                chunk = audio_buffer.get_chunk()
                if chunk is not None and len(chunk) > 0:
                    # Quick check: skip if audio is too quiet (likely silence)
                    audio_level = np.abs(chunk).mean()
                    if audio_level < 0.005:  # Very quiet, likely silence
                        continue
                    
                    processing = True
                    
                    # Process in thread pool to avoid blocking, with timeout
                    try:
                        # Reduced timeout for faster failure (10 seconds for tiny model)
                        result = await asyncio.wait_for(
                            asyncio.to_thread(process_audio_chunk, chunk),
                            timeout=10.0
                        )
                        
                        if result and result.get("text"):
                            # Send immediately when we get a result - no delay
                            await websocket.send_json({
                                "type": "transcription",
                                "text": result["text"],
                                "segments": result["segments"],
                                "language": result.get("language")
                            })
                    except asyncio.TimeoutError:
                        pass  # Silently skip timeout to avoid log spam
                    except Exception as e:
                        print(f"ERROR processing chunk: {e}")
                    finally:
                        processing = False
        
        # Start background processing task
        process_task = asyncio.create_task(process_audio_periodically())
        
        # Receive audio chunks
        while True:
            try:
                # Receive message (can be binary audio or JSON commands)
                data = await websocket.receive()
                
                if "bytes" in data:
                    # Binary audio data
                    audio_bytes = data["bytes"]
                    audio_buffer.add_audio(audio_bytes)
                    
                    # Accumulate audio for saving
                    audio_array = np.frombuffer(audio_bytes, dtype=np.int16).astype(np.float32) / 32768.0
                    accumulated_audio.append(audio_array)
                    
                    buffer_size = audio_buffer.size()
                    buffer_duration = buffer_size / SAMPLE_RATE
                    
                    # Send acknowledgment (only occasionally to reduce spam)
                    if buffer_size % (SAMPLE_RATE // 2) < len(audio_bytes) // 2:  # Every ~0.5 seconds
                        await websocket.send_json({
                            "type": "audio_received",
                            "buffer_size": buffer_size,
                            "buffer_duration": buffer_duration
                        })
                
                elif "text" in data:
                    # JSON command
                    message = json.loads(data["text"])
                    
                    if message.get("type") == "ping":
                        await websocket.send_json({"type": "pong"})
                    
                    elif message.get("type") == "clear":
                        audio_buffer.clear()
                        await websocket.send_json({"type": "buffer_cleared"})
                    
                    elif message.get("type") == "status":
                        await websocket.send_json({
                            "type": "status",
                            "buffer_size": audio_buffer.size(),
                            "buffer_duration": audio_buffer.size() / SAMPLE_RATE
                        })
                
            except WebSocketDisconnect:
                break
            except Exception as e:
                await websocket.send_json({
                    "type": "error",
                    "message": str(e)
                })
    
    except WebSocketDisconnect:
        print("Client disconnected")
    except Exception as e:
        print(f"WebSocket error: {e}")
    finally:
        # Cancel processing task
        if process_task:
            process_task.cancel()
            try:
                await process_task
            except asyncio.CancelledError:
                pass
        
        # Save accumulated audio to file
        if accumulated_audio:
            try:
                # Combine all audio chunks
                combined_audio = np.concatenate(accumulated_audio)
                
                # Generate filename with timestamp
                timestamp = time.strftime("%Y%m%d_%H%M%S")
                audio_file_path = f"recorded_audio_{timestamp}.wav"
                
                # Save to WAV file
                audio_int16 = (combined_audio * 32767.0).astype(np.int16)
                
                with wave.open(audio_file_path, 'wb') as wav_file:
                    wav_file.setnchannels(1)  # Mono
                    wav_file.setsampwidth(2)  # 16-bit
                    wav_file.setframerate(SAMPLE_RATE)
                    wav_file.writeframes(audio_int16.tobytes())
                
                print(f"Saved {len(combined_audio) / SAMPLE_RATE:.2f} seconds of audio to {audio_file_path}")
            except Exception as e:
                print(f"Error saving audio file: {e}")
        
        audio_buffer.clear()

if __name__ == "__main__":
    uvicorn.run(app, host="0.0.0.0", port=8000)
