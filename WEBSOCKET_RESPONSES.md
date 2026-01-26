# WebSocket API Response Examples

This document shows all the response types that the frontend/consumer will receive from the WebSocket server.

## 1. Connection Established

**Type:** `connected`

Sent immediately when WebSocket connection is established. Contains a unique `rec_id` that identifies this recording session.

```json
{
  "type": "connected",
  "message": "WebSocket connected. Start sending audio chunks.",
  "rec_id": "550e8400-e29b-41d4-a716-446655440000",
  "sample_rate": 16000,
  "format": "16-bit PCM, mono"
}
```

**Important:** Store the `rec_id` from this message - you'll need it to retrieve the final transcription after disconnection.

---

## 2. Progressive Transcription Updates

**Type:** `transcription`

Sent every time a new chunk is transcribed (immediately) or when existing chunks are improved. The `text` field contains the **entire transcription** from the beginning, not just the new chunk.

### Example 1: First chunk transcribed (fast, low accuracy)
```json
{
  "type": "transcription",
  "rec_id": "550e8400-e29b-41d4-a716-446655440000",
  "text": "Hello",
  "is_progressive": true
}
```

### Example 2: Second chunk added (older text may be improved)
```json
{
  "type": "transcription",
  "rec_id": "550e8400-e29b-41d4-a716-446655440000",
  "text": "Hello world",
  "is_progressive": true
}
```

### Example 3: After improvement - older text is now better quality
```json
{
  "type": "transcription",
  "rec_id": "550e8400-e29b-41d4-a716-446655440000",
  "text": "Hello, world. How are you",
  "is_progressive": true
}
```

### Example 4: More chunks and improvements
```json
{
  "type": "transcription",
  "rec_id": "550e8400-e29b-41d4-a716-446655440000",
  "text": "Hello, world. How are you today? I'm doing fine",
  "is_progressive": true
}
```

**Note:** The frontend should **replace** the entire displayed text with the new `text` value each time this message is received. Older sentences will improve in quality over time, while newer sentences start with lower accuracy and improve later.

---

## 3. Audio Buffer Status

**Type:** `audio_received`

Sent periodically (approximately every 0.5 seconds) to acknowledge audio reception.

```json
{
  "type": "audio_received",
  "rec_id": "550e8400-e29b-41d4-a716-446655440000",
  "buffer_size": 8000,
  "buffer_duration": 0.5
}
```

**Fields:**
- `rec_id`: Unique session identifier
- `buffer_size`: Number of audio samples in buffer
- `buffer_duration`: Duration of audio in buffer (seconds)

---

## 4. Ping/Pong (Keep-Alive)

**Type:** `pong`

Response to a `ping` message from the client.

```json
{
  "type": "pong",
  "rec_id": "550e8400-e29b-41d4-a716-446655440000"
}
```

---

## 5. Buffer Cleared

**Type:** `buffer_cleared`

Response when client sends a `clear` command.

```json
{
  "type": "buffer_cleared",
  "rec_id": "550e8400-e29b-41d4-a716-446655440000"
}
```

---

## 6. Status Response

**Type:** `status`

Response to a `status` request from the client.

```json
{
  "type": "status",
  "rec_id": "550e8400-e29b-41d4-a716-446655440000",
  "buffer_size": 16000,
  "buffer_duration": 1.0
}
```

---

## 7. Error Messages

**Type:** `error`

Sent when an error occurs during processing.

```json
{
  "type": "error",
  "rec_id": "550e8400-e29b-41d4-a716-446655440000",
  "message": "Error processing audio chunk: timeout"
}
```

---

## 8. Final Transcription

**Type:** `final_transcription`

Sent when the recording session ends. Contains the final high-quality transcription of the entire recording.

```json
{
  "type": "final_transcription",
  "rec_id": "550e8400-e29b-41d4-a716-446655440000",
  "text": "Hello, world. How are you today? I'm doing fine, thank you for asking. This is the final, high-quality transcription of the entire recording.",
  "segments": [
    {
      "start": 0.0,
      "end": 2.5,
      "text": "Hello, world."
    },
    {
      "start": 2.5,
      "end": 5.0,
      "text": "How are you today?"
    },
    {
      "start": 5.0,
      "end": 8.5,
      "text": "I'm doing fine, thank you for asking."
    },
    {
      "start": 8.5,
      "end": 12.0,
      "text": "This is the final, high-quality transcription of the entire recording."
    }
  ],
  "language": "en",
  "audio_file": "recorded_audio_550e8400-e29b-41d4-a716-446655440000_20260126_000047.wav",
  "transcription_file": "recorded_audio_550e8400-e29b-41d4-a716-446655440000_20260126_000047_transcription.txt"
}
```

**Fields:**
- `rec_id`: Unique session identifier
- `text`: Complete final transcription (highest quality)
- `segments`: Array of transcription segments with timestamps
- `language`: Detected language code
- `audio_file`: Path to saved audio file (includes rec_id)
- `transcription_file`: Path to saved transcription text file (includes rec_id)

---

## Typical Message Flow

Here's what a typical session looks like:

```
1. Client connects
   → {"type": "connected", "rec_id": "550e8400-...", ...}
   → Store rec_id for later use

2. Client sends audio chunks
   → {"type": "audio_received", "rec_id": "550e8400-...", "buffer_size": 8000, ...} (periodic)

3. First transcription (fast, immediate)
   → {"type": "transcription", "rec_id": "550e8400-...", "text": "Hello", "is_progressive": true}

4. Second transcription (fast, immediate)
   → {"type": "transcription", "rec_id": "550e8400-...", "text": "Hello world", "is_progressive": true}

5. Improvement happens (older text gets better)
   → {"type": "transcription", "rec_id": "550e8400-...", "text": "Hello, world. How are", "is_progressive": true}

6. More chunks...
   → {"type": "transcription", "rec_id": "550e8400-...", "text": "Hello, world. How are you today?", "is_progressive": true}

7. Client disconnects / recording ends
   → {"type": "final_transcription", "rec_id": "550e8400-...", "text": "...", "segments": [...], ...}

8. Frontend calls POST /final-transcription with rec_id
   → Returns final transcription (waits if not ready yet)
```

---

## Frontend Implementation Notes

1. **Session ID**: Always store the `rec_id` from the `connected` message. You'll need it to retrieve the final transcription after disconnection.

2. **Display Updates**: Always replace the entire transcription text when receiving a `transcription` message. Don't append - the server sends the complete text each time.

3. **Progressive Quality**: Users will see:
   - Immediate but lower-quality transcriptions for new speech
   - Older transcriptions improving in quality over time
   - Final high-quality transcription when recording ends

4. **Final Transcription**: 
   - The WebSocket may send a `final_transcription` message, but it's recommended to also call the HTTP endpoint after disconnection
   - Use the stored `rec_id` to retrieve the specific transcription for your session
   - The endpoint will wait for the transcription to be ready if it's still processing

5. **Error Handling**: Always handle `error` messages and display them to the user.

---

## HTTP Endpoint: Get Final Transcription

**Endpoint:** `POST /final-transcription`

After the WebSocket disconnects, the frontend should call this HTTP endpoint with the `rec_id` to retrieve the final transcription for that specific session. The endpoint will wait (up to 60 seconds) for the transcription to be ready if it's still being processed.

### Request Body

```json
{
  "rec_id": "550e8400-e29b-41d4-a716-446655440000"
}
```

### Success Response (200 OK)

When a final transcription is available:

```json
{
  "status": "success",
  "rec_id": "550e8400-e29b-41d4-a716-446655440000",
  "text": "Hello, world. How are you today? I'm doing fine, thank you for asking. This is the final, high-quality transcription of the entire recording.",
  "segments": [
    {
      "start": 0.0,
      "end": 2.5,
      "text": "Hello, world."
    },
    {
      "start": 2.5,
      "end": 5.0,
      "text": "How are you today?"
    },
    {
      "start": 5.0,
      "end": 8.5,
      "text": "I'm doing fine, thank you for asking."
    },
    {
      "start": 8.5,
      "end": 12.0,
      "text": "This is the final, high-quality transcription of the entire recording."
    }
  ],
  "language": "en",
  "audio_file": "recorded_audio_550e8400-e29b-41d4-a716-446655440000_20260126_000047.wav",
  "transcription_file": "recorded_audio_550e8400-e29b-41d4-a716-446655440000_20260126_000047_transcription.txt",
  "timestamp": "20260126_000047"
}
```

### Error Response (200 OK)

When `rec_id` is missing:

```json
{
  "status": "error",
  "message": "rec_id is required in request body"
}
```

### Timeout Response (200 OK)

When the transcription is still being processed after 60 seconds:

```json
{
  "status": "timeout",
  "message": "Transcription for session 550e8400-e29b-41d4-a716-446655440000 is still being processed. Please try again later."
}
```

### Usage Example

```javascript
// Store rec_id when WebSocket connects
let sessionId = null;

// When WebSocket receives 'connected' message
websocket.onmessage = (event) => {
  const data = JSON.parse(event.data);
  if (data.type === 'connected') {
    sessionId = data.rec_id;
    console.log('Session ID:', sessionId);
  }
};

// After WebSocket disconnects
async function getFinalTranscription(sessionId) {
  try {
    const response = await fetch('http://localhost:8000/final-transcription', {
      method: 'POST',
      headers: {
        'Content-Type': 'application/json',
      },
      body: JSON.stringify({
        rec_id: sessionId
      })
    });
    
    const data = await response.json();
    
    if (data.status === 'success') {
      console.log('Final transcription:', data.text);
      console.log('Audio file:', data.audio_file);
      console.log('Transcription file:', data.transcription_file);
      return data;
    } else if (data.status === 'timeout') {
      console.log('Transcription still processing:', data.message);
      // Retry after a delay
      setTimeout(() => getFinalTranscription(sessionId), 5000);
    } else {
      console.log('Error:', data.message);
    }
  } catch (error) {
    console.error('Error fetching final transcription:', error);
  }
}

// Call after WebSocket disconnects
websocket.onclose = () => {
  if (sessionId) {
    getFinalTranscription(sessionId);
  }
};
```

**Important Notes:**
- The endpoint will **wait** (polling every 0.5 seconds) for up to 60 seconds for the transcription to be ready
- Each session has a unique `rec_id` - make sure to use the correct one
- File names include the `rec_id` to prevent conflicts between concurrent sessions
- If the transcription takes longer than 60 seconds, you'll get a timeout response and should retry
