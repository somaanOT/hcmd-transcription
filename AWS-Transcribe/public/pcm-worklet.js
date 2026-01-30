class PCMWorklet extends AudioWorkletProcessor {
  process(inputs) {
    const input = inputs[0];
    if (!input || !input[0]) return true;

    const channel = input[0];
    const pcm = new Int16Array(channel.length);

    for (let i = 0; i < channel.length; i++) {
      pcm[i] = Math.max(-1, Math.min(1, channel[i])) * 32767;
    }

    this.port.postMessage(pcm.buffer);
    return true;
  }
}

registerProcessor("pcm-worklet", PCMWorklet);
