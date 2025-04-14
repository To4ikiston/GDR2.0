class PCMWriterProcessor extends AudioWorkletProcessor {
  constructor(options) {
    super(options);
  }

  process(inputs, outputs, parameters) {
    const input = inputs[0][0];
    if (input) {
      // Преобразуем Float32 -1..1 -> int16
      const int16data = new Int16Array(input.length);
      for (let i = 0; i < input.length; i++) {
        let sample = input[i] * 32768;
        if (sample > 32767) sample = 32767;
        if (sample < -32768) sample = -32768;
        int16data[i] = sample;
      }
      // Передадим в main.js (Uint8Array)
      this.port.postMessage({ buffer: int16data });
    }
    return true;
  }
}

registerProcessor("pcm-writer", PCMWriterProcessor);
