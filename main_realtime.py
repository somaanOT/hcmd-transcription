from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from faster_whisper import WhisperModel
import asyncio
import numpy as np
from collections import deque
import time
import uvicorn

# ==========================
# Configuration
# ==========================
SAMPLE_RATE = 16000

CHUNK_DURATION = 0.7
OVERLAP_DURATION = 0.4

CHUNK_SAMPLES = int(CHUNK_DURATION * SAMPLE_RATE)
OVERLAP_SAMPLES = int(OVERLAP_DURATION * SAMPLE_RATE)

MIN_AUDIO_LEVEL = 0.005
SILENCE_FINALIZE_SEC = 1.0

MODEL_NAME = "small.en"

# ==========================
# App & Model
# ==========================
app = FastAPI(title="Streaming Transcription (CPU)")

model = WhisperModel(
    MODEL_NAME,
    device="cpu",
    compute_type="int8"
)

# ==========================
# Streaming Audio Buffer
# ==========================
class StreamingAudioBuffer:
    def __init__(self):
        self.buffer = deque()
        self.processed_samples = 0

    def add(self, pcm_bytes: bytes):
        audio = np.frombuffer(pcm_bytes, dtype=np.int16).astype(np.float32) / 32768.0
        self.buffer.extend(audio)

    def available_samples(self):
        return len(self.buffer) - self.processed_samples

    def get_chunk(self):
        if self.available_samples() < CHUNK_SAMPLES:
            return None

        start = max(0, self.processed_samples - OVERLAP_SAMPLES)
        end = start + CHUNK_SAMPLES

        if end > len(self.buffer):
            return None

        chunk = np.array(list(self.buffer)[start:end], dtype=np.float32)
        self.processed_samples = end - OVERLAP_SAMPLES
        return chunk

    def clear(self):
        self.buffer.clear()
        self.processed_samples = 0

# ==========================
# WebSocket Endpoint
# ==========================
@app.websocket("/ws")
async def websocket_endpoint(ws: WebSocket):
    await ws.accept()

    buffer = StreamingAudioBuffer()

    rolling_prompt = ""
    last_voice_time = time.time()
    finalized_text = ""

    try:
        await ws.send_json({
            "type": "connected",
            "sample_rate": SAMPLE_RATE,
            "model": MODEL_NAME
        })

        async def decoder_loop():
            nonlocal rolling_prompt, finalized_text, last_voice_time

            while True:
                await asyncio.sleep(0.1)

                chunk = buffer.get_chunk()
                if chunk is None:
                    # finalize on silence
                    if finalized_text and time.time() - last_voice_time > SILENCE_FINALIZE_SEC:
                        await ws.send_json({
                            "type": "final",
                            "text": finalized_text.strip()
                        })
                        finalized_text = ""
                        rolling_prompt = ""
                    continue

                if np.abs(chunk).mean() < MIN_AUDIO_LEVEL:
                    continue

                last_voice_time = time.time()

                segments, _ = model.transcribe(
                    chunk,
                    sample_rate=SAMPLE_RATE,
                    beam_size=2,
                    best_of=2,
                    temperature=0.0,
                    condition_on_previous_text=True,
                    initial_prompt=rolling_prompt[-300:],
                    vad_filter=False,
                    word_timestamps=False
                )

                partial_text = ""

                for seg in segments:
                    if seg.end > OVERLAP_DURATION:
                        partial_text += seg.text

                if partial_text.strip():
                    finalized_text += partial_text
                    rolling_prompt += partial_text
                    rolling_prompt = rolling_prompt[-500:]

                    await ws.send_json({
                        "type": "partial",
                        "text": finalized_text.strip()
                    })

        decoder_task = asyncio.create_task(decoder_loop())

        while True:
            msg = await ws.receive()
            if "bytes" in msg:
                buffer.add(msg["bytes"])
            elif "text" in msg:
                if msg["text"] == "clear":
                    buffer.clear()
                    rolling_prompt = ""
                    finalized_text = ""
                    await ws.send_json({"type": "cleared"})

    except WebSocketDisconnect:
        pass
    finally:
        decoder_task.cancel()
        buffer.clear()

# ==========================
# Run
# ==========================
if __name__ == "__main__":
    uvicorn.run(app, host="0.0.0.0", port=8000)
