require("dotenv").config();
const express = require("express");
const http = require("http");
const path = require("path");
const fs = require("fs");
const { Server } = require("socket.io");
const Database = require("better-sqlite3");

const {
  TranscribeStreamingClient,
  StartMedicalStreamTranscriptionCommand,
} = require("@aws-sdk/client-transcribe-streaming");

const app = express();
const server = http.createServer(app);
const io = new Server(server);

app.use(express.static("public"));

// /info – recordings database page (latest first)
app.get("/info", (req, res) => {
  res.sendFile(path.join(__dirname, "public", "info.html"));
});

// SQLite database
const db = new Database(path.join(__dirname, "recordings.db"));
db.pragma("journal_mode = WAL");

db.exec(`
  CREATE TABLE IF NOT EXISTS recordings (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    metadata TEXT,
    transcription TEXT DEFAULT '',
    createdAt TEXT NOT NULL,
    transcriptionEndedAt TEXT
  )
`);

const insertRecording = db.prepare(`
  INSERT INTO recordings (metadata, transcription, createdAt)
  VALUES (?, ?, datetime('now'))
`);

const updateRecording = db.prepare(`
  UPDATE recordings
  SET transcription = ?, transcriptionEndedAt = datetime('now')
  WHERE id = ?
`);

// Recordings directory for WAV files
const RECORDINGS_DIR = path.join(__dirname, "recordings");
if (!fs.existsSync(RECORDINGS_DIR)) {
  fs.mkdirSync(RECORDINGS_DIR, { recursive: true });
}

/** Build a WAV file buffer from raw 16-bit PCM chunks (mono, 44100 Hz). */
function buildWavBuffer(pcmChunks) {
  const totalBytes = pcmChunks.reduce((sum, c) => sum + c.length, 0);
  const sampleRate = 44100;
  const numChannels = 1;
  const bitsPerSample = 16;
  const byteRate = sampleRate * numChannels * (bitsPerSample / 8);
  const dataSize = totalBytes;
  const headerSize = 44;
  const fileSize = headerSize + dataSize;

  const header = Buffer.alloc(44);
  let offset = 0;

  header.write("RIFF", offset); offset += 4;
  header.writeUInt32LE(fileSize - 8, offset); offset += 4;
  header.write("WAVE", offset); offset += 4;
  header.write("fmt ", offset); offset += 4;
  header.writeUInt32LE(16, offset); offset += 4; // chunk size
  header.writeUInt16LE(1, offset); offset += 2;  // PCM
  header.writeUInt16LE(numChannels, offset); offset += 2;
  header.writeUInt32LE(sampleRate, offset); offset += 4;
  header.writeUInt32LE(byteRate, offset); offset += 4;
  header.writeUInt16LE(numChannels * (bitsPerSample / 8), offset); offset += 2;
  header.writeUInt16LE(bitsPerSample, offset); offset += 2;
  header.write("data", offset); offset += 4;
  header.writeUInt32LE(dataSize, offset);

  return Buffer.concat([header, ...pcmChunks]);
}

// API: list all recordings (latest first)
const selectRecordings = db.prepare(`
  SELECT id, metadata, transcription, createdAt, transcriptionEndedAt
  FROM recordings
  ORDER BY id DESC
`);
app.get("/api/recordings", (req, res) => {
  try {
    const rows = selectRecordings.all();
    res.json(rows);
  } catch (err) {
    console.error("List recordings error:", err);
    res.status(500).json({ error: err.message });
  }
});

// Serve recording by id: GET /3.wav (only numeric id to avoid conflicting with static files)
app.get("/:id.wav", (req, res) => {
  const id = req.params.id;
  if (!/^\d+$/.test(id)) return res.status(404).send("Not found");
  const filePath = path.join(RECORDINGS_DIR, `${id}.wav`);
  if (!fs.existsSync(filePath)) {
    return res.status(404).send("Recording not found");
  }
  res.setHeader("Content-Type", "audio/wav");
  res.sendFile(path.resolve(filePath));
});

// ⚠️ Always use environment variables for AWS credentials (never hardcode in production)
const transcribeClient = new TranscribeStreamingClient({
  region: "us-west-2",
  credentials: {
    accessKeyId: process.env.AWS_ACCESS_KEY_ID,
    secretAccessKey: process.env.AWS_SECRET_ACCESS_KEY,
  },
});

io.on("connection", (socket) => {
  console.log("🟢 Client connected:", socket.id);

  let isTranscribing = false;
  let audioQueue = [];
  let chunkCount = 0;

  // Per-session state for this recording
  let recordId = null;
  let fullTranscript = "";
  let recordingChunks = []; // copy of audio for saving WAV

  // Receive audio from frontend
  socket.on("audioData", (data) => {
    if (!isTranscribing) return;
    const buf = Buffer.from(data);
    audioQueue.push(buf);
    if (recordId !== null) recordingChunks.push(buf);
    console.log("📥 Audio chunk received, queue length:", audioQueue.length);
  });

  socket.on("startTranscription", async (metadata = {}) => {
    console.log("▶️ Starting MEDICAL transcription", metadata);
    if (isTranscribing) return;

    isTranscribing = true;
    audioQueue = [];
    chunkCount = 0;
    fullTranscript = "";
    recordingChunks = [];

    try {
      const metadataJson = JSON.stringify(metadata);
      const result = insertRecording.run(metadataJson, "");
      recordId = result.lastInsertRowid;
      console.log("📝 Created recording row id:", recordId);
    } catch (err) {
      console.error("❌ DB insert error:", err);
      socket.emit("error", "Failed to create recording");
      isTranscribing = false;
      return;
    }

    const audioStream = async function* () {
      console.log("🎧 Audio stream generator started");

      while (isTranscribing) {
        if (audioQueue.length === 0) {
          await new Promise((r) => setTimeout(r, 10));
          continue;
        }

        const chunk = audioQueue.shift();
        chunkCount++;
        console.log(`📤 Sending chunk ${chunkCount} to AWS (${chunk.length} bytes)`);

        yield {
          AudioEvent: {
            AudioChunk: chunk,
          },
        };
      }

      console.log("🛑 Audio stream generator stopped");
    };

    const command = new StartMedicalStreamTranscriptionCommand({
      LanguageCode: "en-US",
      MediaEncoding: "pcm",
      MediaSampleRateHertz: 44100,
      Specialty: "PRIMARYCARE",
      Type: "CONVERSATION",
      ShowSpeakerLabel: true,
      AudioStream: audioStream(),
    });

    try {
      console.log("🚀 Sending StartMedicalStreamTranscriptionCommand to AWS");

      const response = await transcribeClient.send(command);

      console.log("✅ AWS Medical stream opened, waiting for transcripts");

      for await (const event of response.TranscriptResultStream) {
        if (!event.TranscriptEvent) continue;

        const results = event.TranscriptEvent.Transcript.Results;
        if (!results || results.length === 0) continue;

        const result = results[0];
        if (!result.Alternatives || result.Alternatives.length === 0) continue;

        const transcript = result.Alternatives[0].Transcript;
        const isFinal = !result.IsPartial;
        const speaker = result.ChannelId || null;

        if (isFinal) fullTranscript += (fullTranscript ? " " : "") + transcript;

        console.log("📝 Transcript:", transcript, "Final:", isFinal, "Speaker:", speaker);

        socket.emit("transcription", { text: transcript, isFinal, speaker });
      }
    } catch (err) {
      console.error("❌ Transcription error:", err);
      socket.emit("error", err.message);
    } finally {
      // Save transcription and WAV when stream ends (stop or error)
      if (recordId !== null) {
        try {
          updateRecording.run(fullTranscript.trim(), recordId);
          if (recordingChunks.length > 0) {
            const wavPath = path.join(RECORDINGS_DIR, `${recordId}.wav`);
            fs.writeFileSync(wavPath, buildWavBuffer(recordingChunks));
            console.log("💾 Saved WAV:", wavPath);
          }
        } catch (e) {
          console.error("❌ Failed to finalize recording:", e);
        }
        recordId = null;
        recordingChunks = [];
      }
      isTranscribing = false;
    }
  });

  socket.on("stopTranscription", () => {
    console.log("⏹ Stopping transcription");
    isTranscribing = false;
  });

  socket.on("disconnect", () => {
    console.log("🔴 Client disconnected");
    isTranscribing = false;
  });
});

const PORT = process.env.PORT || 3000;
server.listen(PORT, () => {
  console.log("🌍 Server running at http://localhost:" + PORT);
  console.log("   Recordings DB: recordings.db | WAV files: recordings/");
  console.log("   Audio URL: http://localhost:" + PORT + "/<id>.wav");
});
