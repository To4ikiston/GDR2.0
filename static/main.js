let audioContext;
let mediaStream;
let workletNode;
let websocket;
let isActive = false;

let threshold = 0.95;
let bufferDuration = 3;

const startBtn = document.getElementById("startBtn");
const stopBtn = document.getElementById("stopBtn");
const statusSpan = document.getElementById("statusSpan");
const corrSpan = document.getElementById("corrSpan");
const alertP = document.getElementById("alertP");
const alarmAudio = document.getElementById("alarmAudio");
const spectrogramImg = document.getElementById("spectrogram");

const thresholdRange = document.getElementById("thresholdRange");
const bufferRange = document.getElementById("bufferRange");
const thrVal = document.getElementById("thrVal");
const bufVal = document.getElementById("bufVal");

// Chart.js setup
let ctx = document.getElementById('myChart').getContext('2d');
let corrChart = new Chart(ctx, {
  type: 'line',
  data: {
    labels: [],
    datasets: [{
      label: 'Корреляция',
      data: [],
      borderColor: 'blue',
      fill: false
    }]
  },
  options: {
    scales: {
      y: { min: 0, max: 1 }
    }
  }
});

thresholdRange.oninput = () => {
  threshold = parseFloat(thresholdRange.value);
  thrVal.textContent = threshold.toFixed(2);
  sendParams();
};

bufferRange.oninput = () => {
  bufferDuration = parseInt(bufferRange.value);
  bufVal.textContent = bufferDuration;
  sendParams();
};

// отправить новые параметры на сервер
function sendParams() {
  if (websocket && websocket.readyState === WebSocket.OPEN) {
    const msg = `PARAMS|threshold=${threshold},buffer=${bufferDuration}`;
    websocket.send(msg);
  }
}

// при нажатии "Активировать"
startBtn.onclick = async () => {
  if (isActive) return;
  isActive = true;

  try {
    mediaStream = await navigator.mediaDevices.getUserMedia({ audio: true });
    audioContext = new AudioContext({ sampleRate: 16000 });
    await audioContext.audioWorklet.addModule("worklet-processor.js");

    const source = audioContext.createMediaStreamSource(mediaStream);
    workletNode = new AudioWorkletNode(audioContext, "pcm-writer");

    // когда из ворклет-процессора приходят данные (int16 -> base64)
    workletNode.port.onmessage = (event) => {
      if (websocket && websocket.readyState === WebSocket.OPEN) {
        const base64data = btoa(String.fromCharCode(...new Uint8Array(event.data.buffer)));
        // Отправляем как TEXT: "AUDIO|{base64}"
        websocket.send("AUDIO|" + base64data);
      }
    };

    source.connect(workletNode).connect(audioContext.destination);

    // Установим WebSocket
    let proto = (location.protocol === "https:") ? "wss" : "ws";
    let wsUrl = `${proto}://${location.host}/ws`;
    console.log("[JS] Connecting to:", wsUrl);
    websocket = new WebSocket(wsUrl);

    websocket.onopen = () => {
      console.log("[WS] connected.");
      statusSpan.textContent = "РАБОТАЕТ";
      // Сразу отправим текущие настройки
      sendParams();
    };

    websocket.onmessage = (msg) => {
      // либо JSON, либо text
      if (msg.data.startsWith && msg.data.startsWith("PARAMS|")) {
        // игнорим
      } else {
        try {
          let data = JSON.parse(msg.data);
          if (data.type === "ANALYSIS") {
            let corr = data.corr;
            let detected = data.detected;
            let spec = data.spectrogram;
            let hist = data.history; // массив последних corr

            corrSpan.textContent = corr.toFixed(3);
            if (detected) {
              alertP.style.display = "block";
              alarmAudio.currentTime = 0;
              alarmAudio.play().catch(err => console.log("Autoplay blocked:", err));
            } else {
              alertP.style.display = "none";
            }

            // Обновляем спектрограмму
            spectrogramImg.src = spec;

            // Обновляем график
            corrChart.data.labels = hist.map((_, i) => i); // просто индексы
            corrChart.data.datasets[0].data = hist;
            corrChart.update();

          } else if (data.type === "PARAMS_ACK") {
            console.log("Params ack:", data);
          }
        } catch (ex) {
          console.log("[WS] Not JSON?", msg.data);
        }
      }
    };

    websocket.onclose = () => {
      console.log("[WS] closed.");
      statusSpan.textContent = "ВЫКЛЮЧЕНА";
      corrSpan.textContent = "-";
      alertP.style.display = "none";
      spectrogramImg.src = "";
      // очистим график
      corrChart.data.labels = [];
      corrChart.data.datasets[0].data = [];
      corrChart.update();
    };

  } catch (err) {
    console.error("Error starting audio:", err);
    alert("Не удалось получить доступ к микрофону. " + err);
    isActive = false;
  }
};

// при нажатии "Отключить"
stopBtn.onclick = () => {
  if (!isActive) return;
  isActive = false;

  if (mediaStream) {
    mediaStream.getTracks().forEach(track => track.stop());
    mediaStream = null;
  }
  if (audioContext) {
    audioContext.close();
    audioContext = null;
  }
  if (websocket) {
    websocket.close();
    websocket = null;
  }
  statusSpan.textContent = "ВЫКЛЮЧЕНА";
  corrSpan.textContent = "-";
  alertP.style.display = "none";
  spectrogramImg.src = "";
  corrChart.data.labels = [];
  corrChart.data.datasets[0].data = [];
  corrChart.update();
};
