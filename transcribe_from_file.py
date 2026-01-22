"""
Transcribe an audio file from the current folder.

Simply change the FILENAME variable below to the name of the audio file you want to transcribe.
"""

from faster_whisper import WhisperModel
import os

# Initialize the Whisper model with a small model optimized for CPU
# Using "tiny" model for CPU - very fast but slightly less accurate
# You can change to "base" for better accuracy but slower processing
model = WhisperModel("tiny", device="cpu", compute_type="int8")

# ============================================
# CHANGE THIS TO YOUR AUDIO FILE NAME
# ============================================
FILENAME = "recorded_audio_20260122_024115.wav"  # Change this to your file name
# ============================================

def transcribe_file(filename: str):
    """Transcribe an audio file and print the results"""
    
    # Check if file exists
    if not os.path.exists(filename):
        print(f"Error: File '{filename}' not found in current directory.")
        print(f"Current directory: {os.getcwd()}")
        print("\nAvailable audio files in current directory:")
        for file in os.listdir("."):
            if file.lower().endswith(('.wav', '.mp3', '.m4a', '.flac', '.ogg', '.webm')):
                print(f"  - {file}")
        return
    
    print(f"Transcribing: {filename}")
    print("This may take a moment...\n")
    
    try:
        # Transcribe the audio file
        segments, info = model.transcribe(
            filename,
            beam_size=5,  # Smaller beam size for faster CPU processing
            language="en"  # Set to None for auto-detection
        )
        
        # Collect all segments
        transcription_text = ""
        segments_list = []
        
        print("Transcription Results:")
        print("=" * 60)
        
        for segment in segments:
            transcription_text += segment.text + " "
            segments_list.append({
                "start": segment.start,
                "end": segment.end,
                "text": segment.text
            })
            # Print each segment as it's processed
            print(f"[{segment.start:.2f}s - {segment.end:.2f}s] {segment.text}")
        
        print("=" * 60)
        print("\nFull Transcription:")
        print("-" * 60)
        print(transcription_text.strip())
        print("-" * 60)
        
        # Print metadata
        print(f"\nLanguage: {info.language}")
        if hasattr(info, 'language_probability'):
            print(f"Language Probability: {info.language_probability:.2%}")
        
        # Optionally save to text file
        output_filename = filename.rsplit('.', 1)[0] + '_transcription.txt'
        with open(output_filename, 'w', encoding='utf-8') as f:
            f.write("Transcription Results\n")
            f.write("=" * 60 + "\n\n")
            f.write(f"File: {filename}\n")
            f.write(f"Language: {info.language}\n")
            if hasattr(info, 'language_probability'):
                f.write(f"Language Probability: {info.language_probability:.2%}\n")
            f.write("\n" + "=" * 60 + "\n\n")
            f.write("Full Transcription:\n")
            f.write("-" * 60 + "\n")
            f.write(transcription_text.strip())
            f.write("\n" + "-" * 60 + "\n\n")
            f.write("Segments with Timestamps:\n")
            f.write("-" * 60 + "\n")
            for seg in segments_list:
                f.write(f"[{seg['start']:.2f}s - {seg['end']:.2f}s] {seg['text']}\n")
        
        print(f"\nTranscription also saved to: {output_filename}")
        
    except Exception as e:
        print(f"Error transcribing file: {e}")
        import traceback
        traceback.print_exc()

if __name__ == "__main__":
    transcribe_file(FILENAME)
