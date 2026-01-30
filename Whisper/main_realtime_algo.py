from fastapi import FastAPI, WebSocket, WebSocketDisconnect, Body
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
import uuid
from typing import Optional

app = FastAPI(title="Real-time Audio Transcription API", version="1.0.0")

# Initialize the Whisper model with a small model optimized for CPU
# Using "base" model for better quality while still being reasonably fast
model = WhisperModel("tiny", device="cpu", compute_type="int8")

# Global storage for final transcriptions by session ID
final_transcriptions = {}  # {rec_id: {text, segments, language, audio_file, transcription_file, timestamp, ready}}
final_transcription_lock = threading.Lock()

# Audio buffer settings
SAMPLE_RATE = 16000  # Whisper expects 16kHz
CHUNK_DURATION = 2.0  # Process every 2 seconds - longer chunks = better quality
OVERLAP_DURATION = 0  # 0.5 second overlap to catch words at boundaries
CHUNK_SIZE = int(SAMPLE_RATE * CHUNK_DURATION)  # Samples per chunk

# Progressive transcription settings
IMPROVEMENT_CHUNK_COUNT = 2  # Re-transcribe after every N chunks
# Calculate duration from chunk count (ensures they stay aligned)
IMPROVEMENT_DURATION = IMPROVEMENT_CHUNK_COUNT * CHUNK_DURATION  # Duration = chunk_count * chunk_duration

class AudioBuffer:
    """Manages audio buffer for real-time transcription.
    Uses a sliding window (max 10s). When full, old samples are dropped;
    total_added and last_processed_samples are in global sample coordinates
    so we can keep processing indefinitely."""
    def __init__(self, sample_rate=16000):
        self.sample_rate = sample_rate
        self.buffer = deque(maxlen=int(sample_rate * 10))  # Max 10 seconds buffer
        self.lock = threading.Lock()
        self.last_process_time = time.time()
        self.last_processed_samples = 0  # Global: next chunk starts here
        self.total_added = 0  # Global: total samples ever added (for sliding window)
        self.process_interval = CHUNK_DURATION

    def add_audio(self, audio_data: bytes):
        """Add audio bytes to buffer"""
        with self.lock:
            audio_array = np.frombuffer(audio_data, dtype=np.int16).astype(np.float32) / 32768.0
            n = len(audio_array)
            self.buffer.extend(audio_array)
            self.total_added += n

    def get_chunk(self, duration: float = None) -> np.ndarray:
        """Get audio chunk for processing with overlap"""
        if duration is None:
            duration = CHUNK_DURATION

        samples_needed = int(self.sample_rate * duration)
        overlap_samples = int(self.sample_rate * OVERLAP_DURATION)
        new_audio_needed = samples_needed - overlap_samples

        with self.lock:
            # Sliding window: buffer holds [total_added - len(buffer), total_added)
            # If we've dropped audio, advance last_processed_samples so we don't read before start
            start_index = self.total_added - len(self.buffer)
            if self.last_processed_samples < start_index:
                self.last_processed_samples = start_index

            unprocessed = self.total_added - self.last_processed_samples
            if unprocessed < new_audio_needed:
                return None

            start_pos = self.last_processed_samples - start_index
            end_pos = start_pos + samples_needed

            if end_pos > len(self.buffer):
                return None

            buffer_list = list(self.buffer)
            chunk = np.array(buffer_list[start_pos:end_pos])
            self.last_processed_samples = self.last_processed_samples + (samples_needed - overlap_samples)
            return chunk

    def has_enough_audio(self) -> bool:
        """Check if we have enough audio for a new chunk"""
        with self.lock:
            start_index = self.total_added - len(self.buffer)
            if self.last_processed_samples < start_index:
                self.last_processed_samples = start_index
            samples_needed = int(self.sample_rate * CHUNK_DURATION)
            overlap_samples = int(self.sample_rate * OVERLAP_DURATION)
            new_audio_needed = samples_needed - overlap_samples
            return (self.total_added - self.last_processed_samples) >= new_audio_needed

    def clear(self):
        """Clear the buffer"""
        with self.lock:
            self.buffer.clear()
            self.last_processed_samples = 0
            self.total_added = 0

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

def process_audio_chunk_fast(audio_chunk: np.ndarray) -> dict:
    """Process a chunk of audio with fast settings for immediate transcription (balanced speed/quality)"""
    if audio_chunk is None or len(audio_chunk) == 0:
        return None
    
    tmp_file_path = None
    try:
        tmp_file_path = save_audio_to_wav(audio_chunk, SAMPLE_RATE)
        
        # Fast transcription settings - balanced for reasonable quality while still being fast
        segments, info = model.transcribe(
            tmp_file_path,
            beam_size=3,  # Increased from 1 to 3 for better quality (still fast)
            language="en",
            vad_filter=True,
            vad_parameters=dict(
                min_silence_duration_ms=300,
                threshold=0.5
            ),
            condition_on_previous_text=True,  # Enable context for better quality
            compression_ratio_threshold=2.4,
            log_prob_threshold=-1.0,
            no_speech_threshold=0.5,
            initial_prompt=None,
            word_timestamps=False
        )
        
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
        print(f"Error processing audio chunk (fast): {e}")
        return None
    finally:
        if tmp_file_path and os.path.exists(tmp_file_path):
            try:
                os.unlink(tmp_file_path)
            except (PermissionError, OSError):
                pass

def process_audio_chunk_accurate(audio_chunk: np.ndarray) -> dict:
    """Process a chunk of audio with better settings for improved accuracy"""
    if audio_chunk is None or len(audio_chunk) == 0:
        return None
    
    tmp_file_path = None
    try:
        tmp_file_path = save_audio_to_wav(audio_chunk, SAMPLE_RATE)
        
        # Better transcription settings - optimized for accuracy
        segments, info = model.transcribe(
            tmp_file_path,
            beam_size=5,  # Higher beam for better accuracy
            language="en",
            vad_filter=True,
            vad_parameters=dict(
                min_silence_duration_ms=500,  # Longer silence threshold
                threshold=0.5
            ),
            condition_on_previous_text=True,  # Use context for better accuracy
            compression_ratio_threshold=2.4,
            log_prob_threshold=-1.0,
            no_speech_threshold=0.6,  # Better threshold
            initial_prompt=None,
            word_timestamps=False
        )
        
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
        print(f"Error processing audio chunk (accurate): {e}")
        return None
    finally:
        if tmp_file_path and os.path.exists(tmp_file_path):
            try:
                os.unlink(tmp_file_path)
            except (PermissionError, OSError):
                pass

def process_audio_chunk_final(audio_chunk: np.ndarray) -> dict:
    """Process full audio with best settings for final transcription"""
    if audio_chunk is None or len(audio_chunk) == 0:
        return None
    
    tmp_file_path = None
    try:
        tmp_file_path = save_audio_to_wav(audio_chunk, SAMPLE_RATE)
        
        # Best transcription settings - optimized for maximum accuracy
        segments, info = model.transcribe(
            tmp_file_path,
            beam_size=5,  # Higher beam for best accuracy
            language="en",
            vad_filter=True,
            vad_parameters=dict(
                min_silence_duration_ms=500,
                threshold=0.5
            ),
            condition_on_previous_text=True,  # Use full context
            compression_ratio_threshold=2.4,
            log_prob_threshold=-1.0,
            no_speech_threshold=0.6,
            initial_prompt=None,
            word_timestamps=True  # Enable word timestamps for final transcription
        )
        
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
        print(f"Error processing audio chunk (final): {e}")
        return None
    finally:
        if tmp_file_path and os.path.exists(tmp_file_path):
            try:
                os.unlink(tmp_file_path)
            except (PermissionError, OSError):
                pass

@app.get("/")
async def root():
    return {"message": "Real-time Audio Transcription API", "status": "ready", "websocket_endpoint": "/ws"}

@app.post("/final-transcription")
async def get_final_transcription(body: dict = Body(...)):
    """Get the final transcription for a specific session ID after WebSocket disconnection"""
    global final_transcriptions
    
    rec_id = body.get("rec_id")
    if not rec_id:
        return {
            "status": "error",
            "message": "rec_id is required in request body"
        }
    
    # Wait for transcription to be ready (with timeout)
    max_wait_time = 60  # Maximum 60 seconds to wait
    wait_interval = 0.5  # Check every 0.5 seconds
    elapsed_time = 0
    
    while elapsed_time < max_wait_time:
        with final_transcription_lock:
            if rec_id in final_transcriptions:
                transcription_data = final_transcriptions[rec_id]
                if transcription_data.get("ready", False):
                    return {
                        "status": "success",
                        "rec_id": rec_id,
                        "text": transcription_data["text"],
                        "segments": transcription_data["segments"],
                        "language": transcription_data["language"],
                        "audio_file": transcription_data["audio_file"],
                        "transcription_file": transcription_data["transcription_file"],
                        "timestamp": transcription_data["timestamp"]
                    }
        
        # Wait before checking again
        await asyncio.sleep(wait_interval)
        elapsed_time += wait_interval
    
    # Timeout - transcription not ready yet
    return {
        "status": "timeout",
        "message": f"Transcription for session {rec_id} is still being processed. Please try again later."
    }

@app.websocket("/ws")
async def websocket_endpoint(websocket: WebSocket):
    """WebSocket endpoint for real-time audio transcription with progressive improvement"""
    await websocket.accept()
    
    # Generate unique session ID for this recording
    rec_id = str(uuid.uuid4())
    
    audio_buffer = AudioBuffer(SAMPLE_RATE)
    process_task = None
    improvement_task = None
    
    # Accumulate all audio for saving to file
    accumulated_audio = []  # List of numpy arrays
    audio_file_path = None
    
    # Progressive transcription state
    # Store: (chunk_index, audio_data, fast_text, improved_text, is_improved)
    transcription_chunks = []
    chunk_counter = 0
    last_improvement_time = time.time()
    transcription_lock = asyncio.Lock()
    is_recording = True
    
    def build_full_transcription() -> str:
        """Build the full transcription from all chunks (use improved text if available)"""
        parts = []
        
        for chunk_data in transcription_chunks:
            chunk_idx, audio_data, fast_text, improved_text, is_improved = chunk_data
            
            # If this chunk has improved text, use it
            if is_improved and improved_text:
                parts.append(improved_text)
            # If chunk is marked as improved but has no improved_text, it's part of a segment
            # that was improved - skip it (the improved text is in a later chunk)
            elif is_improved and not improved_text:
                continue  # Skip this chunk - it's covered by improved text in a later chunk
            # Otherwise use fast text
            elif fast_text:
                parts.append(fast_text)
        
        return " ".join(parts)
    
    try:
        # Send initial connection message with session ID
        await websocket.send_json({
            "type": "connected",
            "message": "WebSocket connected. Start sending audio chunks.",
            "rec_id": rec_id,
            "sample_rate": SAMPLE_RATE,
            "format": "16-bit PCM, mono"
        })
        
        async def send_full_transcription():
            """Send the complete transcription to frontend"""
            async with transcription_lock:
                full_text = build_full_transcription()
                await websocket.send_json({
                    "type": "transcription",
                    "rec_id": rec_id,
                    "text": full_text,
                    "is_progressive": True
                })
        
        async def process_audio_periodically():
            """Process audio chunks immediately with fast transcription"""
            nonlocal chunk_counter, last_improvement_time
            processing = False
            
            while is_recording:
                await asyncio.sleep(0.1)  # Check frequently
                
                if processing or not audio_buffer.has_enough_audio():
                    continue
                
                chunk = audio_buffer.get_chunk()
                if chunk is not None and len(chunk) > 0:
                    audio_level = np.abs(chunk).mean()
                    if audio_level < 0.005:  # Skip silence
                        continue
                    
                    processing = True
                    
                    try:
                        # Fast transcription for immediate feedback
                        result = await asyncio.wait_for(
                            asyncio.to_thread(process_audio_chunk_fast, chunk),
                            timeout=10.0  # Increased timeout for 2-second chunks with better settings
                        )
                        
                        if result and result.get("text"):
                            async with transcription_lock:
                                # Store chunk with audio data, fast transcription, and empty improved text
                                transcription_chunks.append((
                                    chunk_counter,
                                    chunk.copy(),  # Store audio data for later improvement
                                    result["text"],  # Fast transcription
                                    "",  # Improved text (empty initially)
                                    False  # Not improved yet
                                ))
                                chunk_counter += 1
                            
                            # Send full transcription immediately
                            await send_full_transcription()
                            
                    except asyncio.TimeoutError:
                        pass
                    except Exception as e:
                        print(f"ERROR processing chunk (fast): {e}")
                    finally:
                        processing = False
        
        async def improve_transcription_periodically():
            """Periodically re-transcribe accumulated chunks with better accuracy"""
            nonlocal last_improvement_time
            
            while is_recording:
                await asyncio.sleep(1.0)  # Check every second
                
                current_time = time.time()
                time_since_improvement = current_time - last_improvement_time
                
                # Check if we should improve transcription
                async with transcription_lock:
                    should_improve = (
                        len(transcription_chunks) >= IMPROVEMENT_CHUNK_COUNT and
                        time_since_improvement >= IMPROVEMENT_DURATION and
                        len(transcription_chunks) > 0
                    )
                    
                    if not should_improve:
                        continue
                    
                    # Find unimproved chunks to improve
                    unimproved_indices = [
                        i for i, (_, _, _, improved_text, is_improved) in enumerate(transcription_chunks)
                        if not is_improved
                    ]
                    
                    if len(unimproved_indices) == 0:
                        continue
                    
                    # Get the last N unimproved chunks to improve together
                    # This gives better context for re-transcription
                    chunks_to_improve = min(IMPROVEMENT_CHUNK_COUNT, len(unimproved_indices))
                    indices_to_improve = unimproved_indices[-chunks_to_improve:]
                    
                    # Combine audio from chunks to improve
                    audio_chunks_to_improve = []
                    for idx in indices_to_improve:
                        chunk_idx, audio_data, _, _, _ = transcription_chunks[idx]
                        audio_chunks_to_improve.append(audio_data)
                    
                    if not audio_chunks_to_improve:
                        continue
                    
                    # Combine audio chunks
                    combined_audio = np.concatenate(audio_chunks_to_improve)
                
                # Re-transcribe with better accuracy (outside lock to avoid blocking)
                try:
                    result = await asyncio.wait_for(
                        asyncio.to_thread(process_audio_chunk_accurate, combined_audio),
                        timeout=15.0
                    )
                    
                    if result and result.get("text"):
                        async with transcription_lock:
                            improved_text = result["text"]
                            
                            # Update all chunks in the improved segment
                            # We'll put the improved text in the last chunk and clear earlier chunks
                            # This way the improved text replaces the fast transcriptions
                            if len(indices_to_improve) > 0:
                                last_idx = indices_to_improve[-1]
                                chunk_idx, audio_data, fast_text, _, _ = transcription_chunks[last_idx]
                                
                                # Update the last chunk with improved text
                                transcription_chunks[last_idx] = (
                                    chunk_idx, audio_data, fast_text, improved_text, True
                                )
                                
                                # Mark other chunks in the segment as improved (without improved_text)
                                # They'll be skipped in build_full_transcription since improved text covers them
                                for idx in indices_to_improve[:-1]:
                                    chunk_idx, audio_data, fast_text, _, _ = transcription_chunks[idx]
                                    transcription_chunks[idx] = (
                                        chunk_idx, audio_data, fast_text, "", True  # Keep fast_text as fallback, mark as improved
                                    )
                            
                            last_improvement_time = current_time
                        
                        # Send updated full transcription
                        await send_full_transcription()
                        
                except asyncio.TimeoutError:
                    pass
                except Exception as e:
                    print(f"ERROR improving transcription: {e}")
        
        # Start background processing tasks
        process_task = asyncio.create_task(process_audio_periodically())
        improvement_task = asyncio.create_task(improve_transcription_periodically())
        
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
                            "rec_id": rec_id,
                            "buffer_size": buffer_size,
                            "buffer_duration": buffer_duration
                        })
                
                elif "text" in data:
                    # JSON command
                    message = json.loads(data["text"])
                    
                    if message.get("type") == "ping":
                        await websocket.send_json({
                            "type": "pong",
                            "rec_id": rec_id
                        })
                    
                    elif message.get("type") == "clear":
                        audio_buffer.clear()
                        await websocket.send_json({
                            "type": "buffer_cleared",
                            "rec_id": rec_id
                        })
                    
                    elif message.get("type") == "status":
                        await websocket.send_json({
                            "type": "status",
                            "rec_id": rec_id,
                            "buffer_size": audio_buffer.size(),
                            "buffer_duration": audio_buffer.size() / SAMPLE_RATE
                        })
                
            except WebSocketDisconnect:
                break
            except Exception as e:
                await websocket.send_json({
                    "type": "error",
                    "rec_id": rec_id,
                    "message": str(e)
                })
    
    except WebSocketDisconnect:
        print("Client disconnected")
    except Exception as e:
        print(f"WebSocket error: {e}")
    finally:
        # Stop recording flag
        is_recording = False
        
        # Cancel processing tasks
        if process_task:
            process_task.cancel()
            try:
                await process_task
            except asyncio.CancelledError:
                pass
        
        if improvement_task:
            improvement_task.cancel()
            try:
                await improvement_task
            except asyncio.CancelledError:
                pass
        
        # Wait a bit for any final processing
        await asyncio.sleep(0.5)
        
        # Save accumulated audio and generate final transcription
        if accumulated_audio:
            try:
                # Combine all audio chunks
                combined_audio = np.concatenate(accumulated_audio)
                
                # Generate filename with session ID and timestamp
                timestamp = time.strftime("%Y%m%d_%H%M%S")
                audio_file_path = f"recorded_audio_{rec_id}_{timestamp}.wav"
                transcription_file_path = f"recorded_audio_{rec_id}_{timestamp}_transcription.txt"
                
                # Initialize transcription entry (not ready yet)
                with final_transcription_lock:
                    final_transcriptions[rec_id] = {
                        "text": None,
                        "segments": None,
                        "language": None,
                        "audio_file": audio_file_path,
                        "transcription_file": transcription_file_path,
                        "timestamp": timestamp,
                        "ready": False
                    }
                
                # Save to WAV file
                audio_int16 = (combined_audio * 32767.0).astype(np.int16)
                
                with wave.open(audio_file_path, 'wb') as wav_file:
                    wav_file.setnchannels(1)  # Mono
                    wav_file.setsampwidth(2)  # 16-bit
                    wav_file.setframerate(SAMPLE_RATE)
                    wav_file.writeframes(audio_int16.tobytes())
                
                print(f"Saved {len(combined_audio) / SAMPLE_RATE:.2f} seconds of audio to {audio_file_path}")
                
                # Generate final high-quality transcription
                print(f"Generating final transcription for session {rec_id}...")
                final_result = await asyncio.to_thread(process_audio_chunk_final, combined_audio)
                
                if final_result and final_result.get("text"):
                    # Save final transcription to file
                    with open(transcription_file_path, 'w', encoding='utf-8') as f:
                        f.write(final_result["text"])
                    
                    print(f"Saved final transcription to {transcription_file_path}")
                    
                    # Store in global storage for HTTP endpoint access (mark as ready)
                    with final_transcription_lock:
                        final_transcriptions[rec_id] = {
                            "text": final_result["text"],
                            "segments": final_result.get("segments", []),
                            "language": final_result.get("language"),
                            "audio_file": audio_file_path,
                            "transcription_file": transcription_file_path,
                            "timestamp": timestamp,
                            "ready": True
                        }
                    
                    # Send final transcription to frontend
                    try:
                        await websocket.send_json({
                            "type": "final_transcription",
                            "rec_id": rec_id,
                            "text": final_result["text"],
                            "segments": final_result.get("segments", []),
                            "language": final_result.get("language"),
                            "audio_file": audio_file_path,
                            "transcription_file": transcription_file_path
                        })
                    except:
                        pass  # Client may have disconnected
                
            except Exception as e:
                print(f"Error saving audio file or generating final transcription: {e}")
        
        audio_buffer.clear()

if __name__ == "__main__":
    uvicorn.run(app, host="0.0.0.0", port=8000)
