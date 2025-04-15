let ws = null;
let isActive = false;
let mediaStream = null;
let audioContext = null;
let workletNode = null;

const startBtn     = document.getElementById("startBtn");
const stopBtn      = document.getElementById("stopBtn");
const testAlarmBtn = document.getElementById("testAlarmBtn");

const statusSpan   = document.getElementById("statusSpan");
const mlSpan       = document.getElementById("mlSpan");
const alertP       = document.getElementById("alertP");
const alarmAudio   = document.getElementById("alarmAudio");
const rmsSpan      = document.getElementById("rmsSpan");
const rmsBar       = document.getElementById("rmsBar");

// Слайдеры
const rmsMinRange  = document.getElementById("rmsMinRange");
const gainRange    = document.getElementById("gainRange");
const mlThreshRange= document.getElementById("mlThreshRange");

const rmsMinVal    = document.getElementById("rmsMinVal");
const gainVal      = document.getElementById("gainVal");
const mlThreshVal  = document.getElementById("mlThreshVal");

// текущие значения
let rmsMin    = parseFloat(rmsMinRange.value);
let gain      = parseFloat(gainRange.value);
let mlThresh  = parseFloat(mlThreshRange.value);

// При изменении слайдеров
rmsMinRange.oninput = () => {
  rmsMin = parseFloat(rmsMinRange.value);
  rmsMinVal.textContent = rmsMin.toFixed(2);
  sendParams();
};
gainRange.oninput = () => {
  gain = parseFloat(gainRange.value);
  gainVal.textContent = gain.toFixed(0);
  sendParams();
};
mlThreshRange.oninput = () => {
  mlThresh = parseFloat(mlThreshRange.value);
  mlThreshVal.textContent = mlThresh.toFixed(2);
  sendParams();
};

function sendParams() {
  if (ws && ws.readyState === WebSocket.OPEN) {
    // отправим, например, "PARAMS|rms=0.05,gain=10,mlth=0.80"
    let msg = `PARAMS|rms=${rmsMin},gain=${gain},mlth=${mlThresh}`;
    ws.send(msg);
  }
}

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
      if (ws && ws.readyState === WebSocket.OPEN) {
        const base64data = btoa(String.fromCharCode(...new Uint8Array(evt.data.buffer)));
        ws.send("AUDIO|" + base64data);
      }
    };
    source.connect(workletNode).connect(audioContext.destination);

    let proto = (location.protocol === 'https:') ? 'wss' : 'ws';
    let wsUrl = `${proto}://${location.host}/ws`;
    ws = new WebSocket(wsUrl);

    ws.onopen = () => {
      console.log("[WS] connected");
      statusSpan.textContent = "РАБОТАЕТ";
      sendParams(); // отправим начальные значения
    };

    ws.onmessage = (msg) => {
      try {
        let data = JSON.parse(msg.data);
        if (data.type === "ML_ANALYSIS") {
          // detected, rms
          mlSpan.textContent = data.detected ? "ДРОН" : "нет";
          let curRms = data.rms || 0;
          rmsSpan.textContent = curRms.toFixed(2);

          let pct = Math.min(100, Math.round(curRms * 100));
          rmsBar.style.width = pct + "%";

          if (data.detected) {
            alertP.style.display = "block";
            alarmAudio.currentTime = 0;
            alarmAudio.play().catch(e => console.log("autoPlay blocked:", e));
          } else {
            alertP.style.display = "none";
          }
        }
      } catch {}
    };

    ws.onclose = () => {
      console.log("[WS] closed");
      statusSpan.textContent = "ВЫКЛЮЧЕНА";
      mlSpan.textContent = "-";
      alertP.style.display = "none";
      rmsSpan.textContent = "0.00";
      rmsBar.style.width = "0%";
    };

  } catch (err) {
    alert("Ошибка микрофона: " + err);
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
  mlSpan.textContent = "-";
  alertP.style.display = "none";
  rmsSpan.textContent = "0.00";
  rmsBar.style.width = "0%";
};

testAlarmBtn.onclick = () => {
  alarmAudio.currentTime = 0;
  alarmAudio.play().catch(e => console.log("Manual play blocked:", e));
};
