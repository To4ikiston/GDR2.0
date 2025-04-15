class PCMWriterProcessor extends AudioWorkletProcessor {
  process(inputs, outputs, parameters) {
    const input = inputs[0][0];
    if (input) {
      // float32 -> int16
      const int16data = new Int16Array(input.length);
      for (let i = 0; i < input.length; i++) {
        let val = input[i] * 32768;
        if (val > 32767) val = 32767;
        if (val < -32768) val = -32768;
        int16data[i] = val;
      }
      this.port.postMessage({ buffer: int16data });
    }
    return true;
  }
}
registerProcessor("pcm-writer", PCMWriterProcessor);
