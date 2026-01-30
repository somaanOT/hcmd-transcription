require("dotenv").config();
const express = require("express");
const http = require("http");
const { Server } = require("socket.io");
const {
  TranscribeStreamingClient,
  StartStreamTranscriptionCommand,
} = require("@aws-sdk/client-transcribe-streaming");

const app = express();
const server = http.createServer(app);
const io = new Server(server);

app.use(express.static("public"));

const transcribeClient = new TranscribeStreamingClient({
  region: "us-west-2",
  credentials: {
    accessKeyId: process.env.AWS_ACCESS_KEY_ID,
    secretAccessKey: process.env.AWS_SECRET_ACCESS_KEY,
  },
});

io.on('connection', (socket) => {
    console.log('🟢 Client connected:', socket.id);

    let isStreaming = false;
    let audioQueue = [];

    // Receive audio from client
    socket.on('audioData', (data) => {
        if (!isStreaming) return;
        audioQueue.push(Buffer.from(data));
        console.log('📥 Audio chunk received, queue length:', audioQueue.length);
    });

    socket.on('startTranscription', async () => {
        console.log('▶️ Starting transcription');
        isStreaming = true;
        audioQueue = [];

        const audioStream = async function* () {
            while (isStreaming) {
                if (audioQueue.length === 0) {
                    await new Promise(r => setTimeout(r, 10));
                    continue;
                }
                yield { AudioEvent: { AudioChunk: audioQueue.shift() } };
            }
        };

        const command = new StartStreamTranscriptionCommand({
            LanguageCode: "en-US",
            MediaEncoding: "pcm",
            MediaSampleRateHertz: 44100, // match your actual audioContext.sampleRate
            AudioStream: audioStream(),
        });

        try {
            const response = await transcribeClient.send(command);

            for await (const event of response.TranscriptResultStream) {
                if (!event.TranscriptEvent) continue;
                const results = event.TranscriptEvent.Transcript.Results;
                if (!results.length || !results[0].Alternatives.length) continue;

                const transcript = results[0].Alternatives[0].Transcript;
                const isFinal = !results[0].IsPartial;

                console.log('📝 Transcript received:', transcript, 'Final:', isFinal);
                socket.emit('transcription', { text: transcript, isFinal });
            }
        } catch (err) {
            console.error('❌ Transcription error:', err);
            socket.emit('error', err.message);
        }
    });

    socket.on('stopTranscription', () => {
        console.log('⏹ Stopping transcription');
        isStreaming = false;
    });

    socket.on('disconnect', () => {
        console.log('🔴 Client disconnected');
        isStreaming = false;
    });
});


const PORT = process.env.PORT || 3000;
server.listen(PORT, () => {
  console.log(`🌍 Server running at http://localhost:${PORT}`);
});
