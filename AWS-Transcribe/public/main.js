const socket = io();
const output = document.getElementById("output");

let audioContext;
let workletNode;
let mic;

document.getElementById("start").onclick = start;
document.getElementById("stop").onclick = stop;

socket.on("connect", () => {
  console.log("🟢 Socket connected:", socket.id);
});

let currentTranscript = "";  // all finalized text
let partialTranscript = "";  // live partial text

socket.on("transcription", ({ text, isFinal, speaker }) => {
    if (isFinal) {
        // Move partial to finalized
        currentTranscript += text + " ";
        partialTranscript = "";
    } else {
        // Show the partial immediately
        partialTranscript = text;
    }

    // Display combined text
    let displayText = currentTranscript + partialTranscript;

    // Optional: show speaker label for medical transcription
    if (speaker !== null) {
        displayText = `[Speaker ${speaker}] ${displayText}`;
    }

    transcript.textContent = displayText;
});


socket.on("error", (e) => {
  console.error("❌ Server error:", e);
});

async function start() {
  console.log("▶️ Start clicked");

  audioContext = new AudioContext({ sampleRate: 16000 });
  console.log("🎚 AudioContext created:", audioContext.sampleRate);

  await audioContext.audioWorklet.addModule("pcm-worklet.js");
  console.log("✅ AudioWorklet loaded");

  const stream = await navigator.mediaDevices.getUserMedia({ audio: true });
  console.log("🎤 Microphone stream acquired");

  mic = audioContext.createMediaStreamSource(stream);
  workletNode = new AudioWorkletNode(audioContext, "pcm-worklet");

  workletNode.port.onmessage = (e) => {
    console.log("🎧 PCM frame:", e.data.byteLength);
    socket.emit("audio", e.data);
  };

  mic.connect(workletNode);

  console.log("🚀 Audio pipeline connected, starting AWS");
  socket.emit("start");
}

function stop() {
  console.log("⏹ Stop clicked");
  socket.emit("stop");
  audioContext?.close();
}
