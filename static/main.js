let ws = null;
let isActive = false;
let mediaStream = null;
let audioContext = null;
let workletNode = null;

const startBtn     = document.getElementById("startBtn");
const stopBtn      = document.getElementById("stopBtn");
const testAlarmBtn = document.getElementById("testAlarmBtn");

const statusSpan   = document.getElementById("statusSpan");
const probSpan     = document.getElementById("probSpan");
const rmsSpan      = document.getElementById("rmsSpan");
const rmsBar       = document.getElementById("rmsBar");
const resultSpan   = document.getElementById("resultSpan");
const alertP       = document.getElementById("alertP");
const alarmAudio   = document.getElementById("alarmAudio");

const thRange      = document.getElementById("thRange");
const rmsMinRange  = document.getElementById("rmsMinRange");
const gainRange    = document.getElementById("gainRange");

const thVal        = document.getElementById("thVal");
const rmsMinVal    = document.getElementById("rmsMinVal");
const gainVal      = document.getElementById("gainVal");

// текущее значение
let threshold = parseFloat(thRange.value);
let curRmsMin = parseFloat(rmsMinRange.value);
let curGain   = parseFloat(gainRange.value);

function sendParams() {
  if (ws && ws.readyState === WebSocket.OPEN) {
    // PARAMS|th=...,rms=...,gain=...
    let msg = `PARAMS|th=${threshold},rms=${curRmsMin},gain=${curGain}`;
    ws.send(msg);
  }
}

// ползунки
thRange.oninput = () => {
  threshold = parseFloat(thRange.value);
  thVal.textContent = threshold.toFixed(2);
  sendParams();
};
rmsMinRange.oninput = () => {
  curRmsMin = parseFloat(rmsMinRange.value);
  rmsMinVal.textContent = curRmsMin.toFixed(2);
  sendParams();
};
gainRange.oninput = () => {
  curGain = parseFloat(gainRange.value);
  gainVal.textContent = curGain.toFixed(0);
  sendParams();
};

startBtn.onclick = async () => {
  if (isActive) return;
  isActive = true;

  try {
    mediaStream = await navigator.mediaDevices.getUserMedia({ audio: true });
    audioContext = new AudioContext({ sampleRate: 16000 });
    await audioContext.audioWorklet.addModule("worklet-processor.js");

    const source = audioContext.createMediaStreamSource(mediaStream);
    workletNode = new AudioWorkletNode(audioContext, "pcm-writer");

    workletNode.port.onmessage = (ev) => {
      if (ws && ws.readyState === WebSocket.OPEN) {
        const base64data = btoa(String.fromCharCode(...new Uint8Array(ev.data.buffer)));
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
          let prob = data.prob || 0;
          let rms = data.rms || 0;
          let detected = data.detected;

          probSpan.textContent = prob.toFixed(3);
          rmsSpan.textContent = rms.toFixed(3);

          // RMS bar
          let pct = Math.min(100, Math.round(rms * 100));
          rmsBar.style.width = pct + "%";

          if (detected) {
            resultSpan.textContent = "ДРОН!";
            alertP.style.display = "block";
            alarmAudio.currentTime = 0;
            alarmAudio.play().catch(e => console.log("autoplay blocked:", e));
          } else {
            resultSpan.textContent = "нет";
            alertP.style.display = "none";
          }
        }
      } catch {}
    };

    ws.onclose = () => {
      console.log("[WS] closed");
      statusSpan.textContent = "ВЫКЛЮЧЕНА";
      probSpan.textContent = "0.00";
      rmsSpan.textContent = "0.00";
      rmsBar.style.width = "0%";
      resultSpan.textContent = "-";
      alertP.style.display = "none";
    };

  } catch (err) {
    alert("Ошибка при доступе к микрофону: " + err);
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
  probSpan.textContent = "0.00";
  rmsSpan.textContent = "0.00";
  rmsBar.style.width = "0%";
  resultSpan.textContent = "-";
  alertP.style.display = "none";
};

testAlarmBtn.onclick = () => {
  alarmAudio.currentTime = 0;
  alarmAudio.play().catch(e => console.log("Manual alarm error:", e));
};
