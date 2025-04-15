let ws;
let isActive = false;
let mediaStream = null;
let audioContext = null;
let workletNode = null;

const startBtn = document.getElementById("startBtn");
const stopBtn  = document.getElementById("stopBtn");
const statusSpan = document.getElementById("statusSpan");
const alertP = document.getElementById("alertP");
const mlResultSpan = document.getElementById("mlResultSpan");
const alarmAudio = document.getElementById("alarmAudio");

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
    };

    ws.onmessage = (msg) => {
      // ожидаем JSON
      try {
        let data = JSON.parse(msg.data);
        if (data.type === "ML_ANALYSIS") {
          // data.detected = true/false
          mlResultSpan.textContent = data.detected ? "ДРОН" : "нет";

          if (data.detected) {
            alertP.style.display = "block";
            alarmAudio.currentTime = 0;
            alarmAudio.play().catch(e => console.log("autoPlay error", e));
          } else {
            alertP.style.display = "none";
          }
        }
      } catch {}
    };

    ws.onclose = () => {
      console.log("[WS] closed");
      statusSpan.textContent = "ВЫКЛЮЧЕНА";
      mlResultSpan.textContent = "-";
      alertP.style.display = "none";
    };

  } catch (err) {
    console.log("Error audio:", err);
    alert("Не удалось подключить микрофон: " + err);
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
};
