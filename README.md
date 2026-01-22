# Audio Transcription API

A FastAPI-based service for transcribing audio files using faster-whisper.

## Features

- Fast audio transcription using faster-whisper
- Optimized for CPU usage with small model (tiny)
- Supports various audio formats (mp3, wav, m4a, etc.)
- Returns detailed transcription with timestamps

## Installation

1. Install dependencies:
```bash
pip install -r requirements.txt
```

## Usage

### Start the server:
```bash
python main.py
```

Or using uvicorn directly:
```bash
uvicorn main:app --reload
```

The API will be available at `http://localhost:8000`

### API Endpoints

#### POST `/transcribe`
Upload an audio file for transcription.

**Request:**
- Method: POST
- Content-Type: multipart/form-data
- Body: audio file

**Response:**
```json
{
  "transcription": "The transcribed text...",
  "language": "en",
  "language_probability": 0.99,
  "segments": [
    {
      "start": 0.0,
      "end": 2.5,
      "text": "Segment text"
    }
  ]
}
```

### Example using curl:
```bash
curl -X POST "http://localhost:8000/transcribe" -F "file=@your_audio_file.mp3"
```

### Example using Python requests:
```python
import requests

url = "http://localhost:8000/transcribe"
files = {"file": open("your_audio_file.mp3", "rb")}
response = requests.post(url, files=files)
print(response.json())
```

## Model Options

The default model is "tiny" which is optimized for CPU. You can change it in `main.py`:
- `"tiny"` - Fastest, least accurate (default for CPU)
- `"base"` - Better accuracy, slower
- `"small"` - Good balance (may be slow on CPU)

## Real-time Transcription

For real-time streaming transcription, use `main_realtime.py`:

```bash
python main_realtime.py
```

This provides a WebSocket endpoint at `ws://localhost:8000/ws` for streaming audio and receiving transcriptions in real-time.

### WebSocket API

**Endpoint:** `ws://localhost:8000/ws`

**Audio Format:**
- 16-bit PCM, mono
- 16kHz sample rate
- Send raw audio bytes

**Message Types:**

1. **Send Audio (Binary):** Send raw audio bytes directly
2. **Commands (JSON):**
   - `{"type": "ping"}` - Check connection
   - `{"type": "clear"}` - Clear audio buffer
   - `{"type": "status"}` - Get buffer status

**Server Responses (JSON):**
- `{"type": "connected"}` - Connection established
- `{"type": "transcription", "text": "...", "segments": [...]}` - Transcription result
- `{"type": "audio_received"}` - Audio chunk received
- `{"type": "error", "message": "..."}` - Error occurred

### Example Client

See `client_example.py` for a complete example of how to connect and send audio:

```bash
# From microphone
python client_example.py

# From audio file
python client_example.py your_audio.wav
```

**Note:** The client example requires additional dependencies:
```bash
pip install websockets pyaudio numpy
```

## API Documentation

Once the server is running, visit:
- Swagger UI: `http://localhost:8000/docs`
- ReDoc: `http://localhost:8000/redoc`
