let ws = null;
let isActive = false;

let mediaStream = null;
let audioContext = null;
let workletNode = null;

const startBtn      = document.getElementById("startBtn");
const stopBtn       = document.getElementById("stopBtn");
const testAlarmBtn  = document.getElementById("testAlarmBtn");
const statusSpan    = document.getElementById("statusSpan");
const mlResultSpan  = document.getElementById("mlResultSpan");
const alertP        = document.getElementById("alertP");
const alarmAudio    = document.getElementById("alarmAudio");
const rmsSpan       = document.getElementById("rmsSpan");
const rmsBar        = document.getElementById("rmsBar");

startBtn.onclick = async () => {
  if (isActive) return;
  isActive = true;

  try {
    mediaStream = await navigator.mediaDevices.getUserMedia({ audio: true });
    audioContext = new AudioContext({ sampleRate: 16000 });
    await audioContext.audioWorklet.addModule("worklet-processor.js");

    const source = audioContext.createMediaStreamSource(mediaStream);
    workletNode = new AudioWorkletNode(audioContext, "pcm-writer");

    workletNode.port.onmessage = (evt) => {
      // PCM int16
      if (ws && ws.readyState === WebSocket.OPEN) {
        const base64data = btoa(String.fromCharCode(...new Uint8Array(evt.data.buffer)));
        ws.send("AUDIO|" + base64data);
      }
    };

    source.connect(workletNode).connect(audioContext.destination);

    let proto = (location.protocol === "https:") ? "wss" : "ws";
    let wsUrl = `${proto}://${location.host}/ws`;
    ws = new WebSocket(wsUrl);

    ws.onopen = () => {
      console.log("[WS] connected");
      statusSpan.textContent = "РАБОТАЕТ";
    };

    ws.onmessage = (msg) => {
      try {
        const data = JSON.parse(msg.data);
        if (data.type === "ML_ANALYSIS") {
          // data.detected, data.rms
          mlResultSpan.textContent = data.detected ? "ДРОН" : "нет";
          if (data.detected) {
            alertP.style.display = "block";
            alarmAudio.currentTime = 0;
            alarmAudio.play().catch(e => console.log("Autoplay blocked:", e));
          } else {
            alertP.style.display = "none";
          }
          // RMS
          rmsSpan.textContent = data.rms.toFixed(2);
          let pct = Math.min(100, Math.round(data.rms * 100));
          rmsBar.style.width = pct + "%";
          if (pct < 10) {
            rmsBar.style.background = "gray";
          } else if (pct < 40) {
            rmsBar.style.background = "orange";
          } else {
            rmsBar.style.background = "red";
          }
        }
      } catch (ex) {
        console.log("[WS] Not JSON or error:", ex);
      }
    };

    ws.onclose = () => {
      console.log("[WS] closed");
      statusSpan.textContent = "ВЫКЛЮЧЕНА";
      mlResultSpan.textContent = "-";
      alertP.style.display = "none";
      rmsSpan.textContent = "0.00";
      rmsBar.style.width = "0%";
    };

  } catch (err) {
    console.error("Error audio:", err);
    alert("Не удалось включить микрофон: " + err);
    isActive = false;
  }
};

stopBtn.onclick = () => {
  if (!isActive) return;
  isActive = false;

  if (mediaStream) {
    mediaStream.getTracks().forEach(t => t.stop());
    mediaStream = null;
  }
  if (audioContext) {
    audioContext.close();
    audioContext = null;
  }
  if (ws) {
    ws.close();
    ws = null;
  }
  statusSpan.textContent = "ВЫКЛЮЧЕНА";
  mlResultSpan.textContent = "-";
  alertP.style.display = "none";
  rmsSpan.textContent = "0.00";
  rmsBar.style.width = "0%";
};

testAlarmBtn.onclick = () => {
  // Просто вручную проигрываем звук
  alarmAudio.currentTime = 0;
  alarmAudio.play().catch(err => console.log("Autoplay error:", err));
};
