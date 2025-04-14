let audioContext;
let mediaStream;
let workletNode;
let websocket;
let isActive = false;

// Порог, длительность (по умолчанию)
let threshold = 0.95;
let bufferDuration = 3;

// HTML-элементы
const startBtn = document.getElementById("startBtn");
const stopBtn = document.getElementById("stopBtn");
const statusSpan = document.getElementById("statusSpan");
const corrSpan = document.getElementById("corrSpan");
const mlSpan = document.getElementById("mlSpan");          // ← отображение вывода ML
const finalSpan = document.getElementById("finalSpan");    // ← итоговое решение
const alertP = document.getElementById("alertP");
const alarmAudio = document.getElementById("alarmAudio");
const spectrogramImg = document.getElementById("spectrogram");

const thresholdRange = document.getElementById("thresholdRange");
const bufferRange = document.getElementById("bufferRange");
const thrVal = document.getElementById("thrVal");
const bufVal = document.getElementById("bufVal");

// Chart.js setup
let ctx = document.getElementById("myChart").getContext("2d");
let corrChart = new Chart(ctx, {
  type: "line",
  data: {
    labels: [],             // будем пересоздавать при каждом обновлении
    datasets: [{
      label: "Корреляция",
      data: [],
      borderColor: "blue",
      fill: false
    }]
  },
  options: {
    scales: {
      y: {
        min: 0,
        max: 1
      }
    }
  }
});

// Инициализируем текстовое отображение
thrVal.textContent = threshold.toFixed(2);
bufVal.textContent = bufferDuration;

// Когда пользователь двигает слайдеры
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

// Отправляем новые параметры на сервер
function sendParams() {
  if (websocket && websocket.readyState === WebSocket.OPEN) {
    const msg = `PARAMS|threshold=${threshold},buffer=${bufferDuration}`;
    websocket.send(msg);
  }
}

// При нажатии "Активировать"
startBtn.onclick = async () => {
  if (isActive) return;
  isActive = true;

  try {
    // 1) Разрешение на микрофон
    mediaStream = await navigator.mediaDevices.getUserMedia({ audio: true });
    // 2) Создаём AudioContext (16kHz)
    audioContext = new AudioContext({ sampleRate: 16000 });
    // 3) Подгружаем worklet
    await audioContext.audioWorklet.addModule("worklet-processor.js");

    // 4) Создаём источник и ворклет
    const source = audioContext.createMediaStreamSource(mediaStream);
    workletNode = new AudioWorkletNode(audioContext, "pcm-writer");

    // Когда приходят PCM-данные, мы шлём их на сервер (base64)
    workletNode.port.onmessage = (event) => {
      if (websocket && websocket.readyState === WebSocket.OPEN) {
        const base64data = btoa(String.fromCharCode(...new Uint8Array(event.data.buffer)));
        // Отправляем как TEXT: "AUDIO|{base64}"
        websocket.send("AUDIO|" + base64data);
      }
    };

    // Соединяем узлы
    source.connect(workletNode).connect(audioContext.destination);

    // Создаём WebSocket
    let proto = (location.protocol === "https:") ? "wss" : "ws";
    let wsUrl = `${proto}://${location.host}/ws`;
    console.log("[JS] Connecting to:", wsUrl);
    websocket = new WebSocket(wsUrl);

    // Когда WebSocket открыт
    websocket.onopen = () => {
      console.log("[WS] connected.");
      statusSpan.textContent = "РАБОТАЕТ";
      sendParams(); // сразу отправим настройки
    };

    // Когда приходят сообщения
    websocket.onmessage = (msg) => {
      // Проверка, не 'PARAMS|'
      if (msg.data.startsWith && msg.data.startsWith("PARAMS|")) {
        console.log("[WS] params message, ignoring");
      } else {
        // Пытаемся парсить JSON
        try {
          let data = JSON.parse(msg.data);
          if (data.type === "ANALYSIS") {
            // Извлекаем поля
            let corr = data.corr;                     // число
            let detectedCorr = data.detected_corr;    // булево
            let detectedML = data.detected_ml;        // булево
            let detectedFinal = data.detected_final;  // булево
            let spec = data.spectrogram;              // base64
            let hist = data.history;                  // массив последних корр

            // Обновляем UI
            // 1) Показываем корреляцию
            corrSpan.textContent = corr.toFixed(3);

            // 2) ML-предiction (true/false)
            mlSpan.textContent = detectedML ? "ДРОН" : "нет";

            // 3) Итоговое решение
            finalSpan.textContent = detectedFinal ? "🚨 ДРОН!" : "ОК";

            // 4) Если detectedFinal == true → сигнал
            if (detectedFinal) {
              alertP.style.display = "block";
              alarmAudio.currentTime = 0;
              alarmAudio.play().catch(err => console.log("Autoplay blocked:", err));
            } else {
              alertP.style.display = "none";
            }

            // 5) Спектрограмма
            spectrogramImg.src = spec;

            // 6) График корреляции
            corrChart.data.labels = hist.map((_, i) => i); // индексы
            corrChart.data.datasets[0].data = hist;
            corrChart.update();
          }
          else if (data.type === "PARAMS_ACK") {
            console.log("Params ack:", data);
          }
        } catch (ex) {
          console.log("[WS] Not JSON?", msg.data);
        }
      }
    };

    // Когда WebSocket закрыт
    websocket.onclose = () => {
      console.log("[WS] closed.");
      statusSpan.textContent = "ВЫКЛЮЧЕНА";
      corrSpan.textContent = "-";
      mlSpan.textContent = "-";
      finalSpan.textContent = "ОК";
      alertP.style.display = "none";
      spectrogramImg.src = "";
      // Очистим график
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
  mlSpan.textContent = "-";
  finalSpan.textContent = "ОК";
  alertP.style.display = "none";
  spectrogramImg.src = "";
  corrChart.data.labels = [];
  corrChart.data.datasets[0].data = [];
  corrChart.update();
};

thresholdRange.value = threshold;
thrVal.textContent = threshold.toFixed(2);

bufferRange.value = bufferDuration;
bufVal.textContent = bufferDuration;
