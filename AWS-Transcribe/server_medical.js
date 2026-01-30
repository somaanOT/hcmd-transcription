require("dotenv").config();
const express = require("express");
const http = require("http");
const path = require("path");
const { Server } = require("socket.io");

const {
  TranscribeStreamingClient,
  StartMedicalStreamTranscriptionCommand,
} = require("@aws-sdk/client-transcribe-streaming");

const app = express();
const server = http.createServer(app);
const io = new Server(server);

app.use(express.static("public"));

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

  // Receive audio from frontend
  socket.on("audioData", (data) => {
    if (!isTranscribing) return;
    audioQueue.push(Buffer.from(data));
    console.log("📥 Audio chunk received, queue length:", audioQueue.length);
  });

  socket.on("startTranscription", async () => {
    console.log("▶️ Starting MEDICAL transcription");
    if (isTranscribing) return;

    isTranscribing = true;
    audioQueue = [];
    chunkCount = 0;

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
      MediaSampleRateHertz: 44100, // match your actual AudioContext sampleRate
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

        console.log("📝 Transcript:", transcript, "Final:", isFinal, "Speaker:", speaker);

        socket.emit("transcription", { text: transcript, isFinal, speaker });
      }
    } catch (err) {
      console.error("❌ Transcription error:", err);
      socket.emit("error", err.message);
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
  console.log(`🌍 Server running at http://localhost:${PORT}`);
});
