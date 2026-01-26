"""
Example client for real-time transcription API

This example shows how to connect to the WebSocket endpoint and send audio chunks.
You can adapt this to work with your audio source (microphone, file, etc.)
"""

import asyncio
import websockets
import json
import pyaudio
import numpy as np
import time
import aiohttp

# Configuration
WS_URL = "ws://localhost:8000/ws"
SAMPLE_RATE = 16000
CHUNK_SIZE = 1024  # Audio chunk size in frames
FORMAT = pyaudio.paInt16
CHANNELS = 1  # Mono

async def send_audio_from_microphone():
    """Send audio from microphone to the transcription API"""
    p = pyaudio.PyAudio()
    
    # List available input devices
    print("Available audio input devices:")
    for i in range(p.get_device_count()):
        info = p.get_device_info_by_index(i)
        if info['maxInputChannels'] > 0:
            print(f"  [{i}] {info['name']} - {info['maxInputChannels']} channels")
    print()
    
    try:
        # Try to find a good microphone (prefer mono, single channel devices)
        input_device_index = None
        for i in range(p.get_device_count()):
            info = p.get_device_info_by_index(i)
            if info['maxInputChannels'] > 0:
                # Prefer devices with 1-2 channels
                if info['maxInputChannels'] <= 2:
                    input_device_index = i
                    print(f"Using device: {info['name']}")
                    break
        
        # Open microphone stream
        stream = p.open(
            format=FORMAT,
            channels=CHANNELS,
            rate=SAMPLE_RATE,
            input=True,
            input_device_index=input_device_index,  # Use selected device
            frames_per_buffer=CHUNK_SIZE
        )
        
        print(f"Connecting to {WS_URL}...")
        rec_id = None  # Store the recording ID
        
        async with websockets.connect(WS_URL) as websocket:
            # Receive initial connection message
            message = await websocket.recv()
            connection_data = json.loads(message)
            print(f"Server: {connection_data}")
            
            # Store the rec_id from connection message
            if connection_data.get("type") == "connected":
                rec_id = connection_data.get("rec_id")
                print(f"\nRecording ID: {rec_id}")
            
            print("\nRecording... Speak into your microphone. Press Ctrl+C to stop.\n")
            print("Note: Server needs ~2 seconds of audio before first transcription.\n")
            
            buffer_duration = 0.0
            chunk_count = 0
            status_lock = asyncio.Lock()
            
            async def receive_messages():
                """Receive and display transcription messages"""
                nonlocal buffer_duration
                try:
                    while True:
                        message = await websocket.recv()
                        data = json.loads(message)
                        
                        if data.get("type") == "transcription":
                            text = data.get('text', '')
                            print(f"\n[Transcription] {text}\n")
                        elif data.get("type") == "final_transcription":
                            print(f"\n[Final Transcription] {data.get('text', '')}\n")
                            print(f"Audio file: {data.get('audio_file', '')}")
                            print(f"Transcription file: {data.get('transcription_file', '')}\n")
                        elif data.get("type") == "error":
                            print(f"[Error] {data.get('message', '')}")
                        elif data.get("type") == "audio_received":
                            async with status_lock:
                                buffer_duration = data.get('buffer_duration', 0.0)
                except websockets.exceptions.ConnectionClosed:
                    pass
            
            # Start receiving messages
            receive_task = asyncio.create_task(receive_messages())
            
            # Send audio chunks
            try:
                while True:
                    # Read audio from microphone
                    audio_data = stream.read(CHUNK_SIZE, exception_on_overflow=False)
                    
                    # Check audio level for debugging
                    audio_array = np.frombuffer(audio_data, dtype=np.int16)
                    audio_level = np.abs(audio_array).mean()
                    
                    # Apply gain if audio is too quiet (amplify by 3x if below threshold)
                    if audio_level > 0 and audio_level < 500:
                        gain = 3.0
                        audio_array = (audio_array * gain).astype(np.int16)
                        audio_data = audio_array.tobytes()
                        audio_level = np.abs(audio_array).mean()
                    
                    # Show audio level indicator
                    if chunk_count % 50 == 0:  # Every ~0.5 seconds
                        async with status_lock:
                            current_buffer = buffer_duration
                        level_bar = '█' * min(int(audio_level / 500), 20)  # Adjusted scale
                        print(f"[Audio Level] {level_bar} ({int(audio_level)}) | Buffer: {current_buffer:.2f}s", end='\r')
                    
                    # Always send audio to server (let server handle silence filtering)
                    await websocket.send(audio_data)
                    
                    chunk_count += 1
                    
                    # Small delay to prevent overwhelming the server
                    await asyncio.sleep(0.01)
            
            except KeyboardInterrupt:
                print("\n\nStopping...")
            finally:
                receive_task.cancel()
                try:
                    await receive_task
                except asyncio.CancelledError:
                    pass
        
        # After WebSocket disconnects, get final transcription
        if rec_id:
            print(f"\nFetching final transcription for recording {rec_id}...")
            await get_final_transcription(rec_id)
    
    except Exception as e:
        print(f"\nError: {e}")
        import traceback
        traceback.print_exc()
    finally:
        stream.stop_stream()
        stream.close()
        p.terminate()

async def send_audio_from_file(audio_file_path: str):
    """Send audio from a file to the transcription API"""
    import wave
    
    try:
        # Open audio file
        wf = wave.open(audio_file_path, 'rb')
        sample_rate = wf.getframerate()
        channels = wf.getnchannels()
        sample_width = wf.getsampwidth()
        
        print(f"Audio file: {sample_rate}Hz, {channels} channels, {sample_width} bytes/sample")
        print(f"Connecting to {WS_URL}...")
        
        rec_id = None  # Store the recording ID
        
        async with websockets.connect(WS_URL) as websocket:
            # Receive initial connection message
            message = await websocket.recv()
            connection_data = json.loads(message)
            print(f"Server: {connection_data}")
            
            # Store the rec_id from connection message
            if connection_data.get("type") == "connected":
                rec_id = connection_data.get("rec_id")
                print(f"\nRecording ID: {rec_id}")
            
            print("\nSending audio file...\n")
            
            async def receive_messages():
                """Receive and display transcription messages"""
                try:
                    while True:
                        message = await websocket.recv()
                        data = json.loads(message)
                        
                        if data.get("type") == "transcription":
                            print(f"[Transcription] {data.get('text', '')}")
                        elif data.get("type") == "final_transcription":
                            print(f"\n[Final Transcription] {data.get('text', '')}\n")
                            print(f"Audio file: {data.get('audio_file', '')}")
                            print(f"Transcription file: {data.get('transcription_file', '')}\n")
                        elif data.get("type") == "error":
                            print(f"[Error] {data.get('message', '')}")
                except websockets.exceptions.ConnectionClosed:
                    pass
            
            # Start receiving messages
            receive_task = asyncio.create_task(receive_messages())
            
            # Send audio chunks
            chunk_size = 4096  # Send in chunks
            while True:
                audio_data = wf.readframes(chunk_size)
                if not audio_data:
                    break
                
                # If sample rate doesn't match, we'd need to resample
                # For simplicity, assuming it matches or is close
                await websocket.send(audio_data)
                await asyncio.sleep(0.1)  # Simulate real-time
            
            # Wait a bit for final transcriptions
            await asyncio.sleep(2)
            
            receive_task.cancel()
            try:
                await receive_task
            except asyncio.CancelledError:
                pass
            
            wf.close()
            print("\nFinished sending audio file.")
        
        # After WebSocket disconnects, get final transcription
        if rec_id:
            print(f"\nFetching final transcription for recording {rec_id}...")
            await get_final_transcription(rec_id)
    
    except Exception as e:
        print(f"Error: {e}")

async def get_final_transcription(rec_id: str):
    """Fetch the final transcription from the HTTP endpoint"""
    api_url = "http://localhost:8000/final-transcription"
    
    try:
        async with aiohttp.ClientSession() as session:
            async with session.post(
                api_url,
                json={"rec_id": rec_id},
                headers={"Content-Type": "application/json"}
            ) as response:
                data = await response.json()
                
                if data.get("status") == "success":
                    print("\n" + "="*60)
                    print("FINAL TRANSCRIPTION")
                    print("="*60)
                    print(f"\n{data.get('text', '')}\n")
                    print(f"Language: {data.get('language', 'unknown')}")
                    print(f"Audio file: {data.get('audio_file', '')}")
                    print(f"Transcription file: {data.get('transcription_file', '')}")
                    print(f"Timestamp: {data.get('timestamp', '')}")
                    
                    # Show segments if available
                    segments = data.get('segments', [])
                    if segments:
                        print(f"\nSegments ({len(segments)}):")
                        for i, segment in enumerate(segments, 1):
                            print(f"  {i}. [{segment.get('start', 0):.2f}s - {segment.get('end', 0):.2f}s] {segment.get('text', '')}")
                    
                    print("="*60 + "\n")
                elif data.get("status") == "timeout":
                    print(f"\n[Timeout] {data.get('message', '')}")
                    print("The transcription is still being processed. Please try again later.")
                else:
                    print(f"\n[Error] {data.get('message', 'Unknown error')}")
    
    except Exception as e:
        print(f"\nError fetching final transcription: {e}")
        import traceback
        traceback.print_exc()

if __name__ == "__main__":
    import sys
    
    if len(sys.argv) > 1:
        # Send audio from file
        audio_file = sys.argv[1]
        asyncio.run(send_audio_from_file(audio_file))
    else:
        # Send audio from microphone
        try:
            asyncio.run(send_audio_from_microphone())
        except KeyboardInterrupt:
            print("\nExiting...")
