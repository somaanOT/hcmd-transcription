from fastapi import FastAPI, File, UploadFile, HTTPException
from fastapi.responses import JSONResponse
import tempfile
import os
import time
from faster_whisper import WhisperModel
import uvicorn

app = FastAPI(title="Audio Transcription API", version="1.0.0")

# Initialize the Whisper model with a small model optimized for CPU
# Using "tiny" model for CPU - very fast but slightly less accurate
# You can change to "base" for better accuracy but slower processing
model = WhisperModel("tiny", device="cpu", compute_type="int8")

@app.get("/")
async def root():
    return {"message": "Audio Transcription API", "status": "ready"}

@app.post("/transcribe")
async def transcribe_audio(file: UploadFile = File(...)):
    """
    Transcribe an audio file to text.
    
    Accepts audio files in various formats (mp3, wav, m4a, etc.)
    Returns the transcribed text.
    """
    # Validate file type
    if not file.content_type or not file.content_type.startswith("audio/"):
        raise HTTPException(
            status_code=400,
            detail="File must be an audio file"
        )
    
    # Create a temporary file to save the uploaded audio
    tmp_file_path = None
    try:
        # Save uploaded file to temporary location
        content = await file.read()
        
        # Create temporary file and write content
        with tempfile.NamedTemporaryFile(delete=False, suffix=os.path.splitext(file.filename)[1]) as tmp_file:
            tmp_file.write(content)
            tmp_file_path = tmp_file.name
        
        # File is now closed, safe to transcribe
        # Transcribe the audio file
        segments, info = model.transcribe(
            tmp_file_path,
            beam_size=5,  # Smaller beam size for faster CPU processing
            language="en"  # You can set to None for auto-detection
        )
        
        # Collect all segments into a single text
        transcription_text = ""
        segments_list = []
        
        for segment in segments:
            transcription_text += segment.text + " "
            segments_list.append({
                "start": segment.start,
                "end": segment.end,
                "text": segment.text
            })
        
        # Prepare response
        response = JSONResponse(content={
            "transcription": transcription_text.strip(),
            "language": info.language,
            "language_probability": info.language_probability,
            "segments": segments_list
        })
        
        # Clean up temporary file after response is prepared
        if tmp_file_path and os.path.exists(tmp_file_path):
            try:
                os.unlink(tmp_file_path)
            except (PermissionError, OSError):
                # On Windows, sometimes the file is still locked
                # Try again after a brief delay
                time.sleep(0.1)
                try:
                    os.unlink(tmp_file_path)
                except (PermissionError, OSError):
                    pass  # File will be cleaned up by OS eventually
        
        return response
        
    except Exception as e:
        # Clean up temporary file in case of error
        if tmp_file_path and os.path.exists(tmp_file_path):
            try:
                os.unlink(tmp_file_path)
            except (PermissionError, OSError):
                pass  # File will be cleaned up by OS eventually
        raise HTTPException(
            status_code=500,
            detail=f"Error transcribing audio: {str(e)}"
        )

if __name__ == "__main__":
    uvicorn.run(app, host="0.0.0.0", port=8001)
