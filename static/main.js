let audioContext;
let mediaStream;
let processorNode;
let websocket;
let isRunning = false;

const startBtn = document.getElementById("startBtn");
const stopBtn = document.getElementById("stopBtn");
const statusSpan = document.getElementById("statusSpan");
const corrSpan = document.getElementById("corrSpan");
const alertP = document.getElementById("alertP");
const alarmAudio = document.getElementById("alarmAudio");

// При нажатии "Активировать"
startBtn.onclick = async () => {
  if (isRunning) return;

  try {
    // Запрашиваем микрофон
    mediaStream = await navigator.mediaDevices.getUserMedia({ audio: true, video: false });
    audioContext = new AudioContext({ sampleRate: 16000 });
    const source = audioContext.createMediaStreamSource(mediaStream);

    // Создаем ScriptProcessor / AudioWorklet / MediaStreamTrackProcessor
    // Пример: createScriptProcessor(4096, 1, 1)
    processorNode = audioContext.createScriptProcessor(4096, 1, 1);

    // Когда приходят аудиоданные
    processorNode.onaudioprocess = (event) => {
      // Получаем PCM из inputBuffer
      const inputBuffer = event.inputBuffer.getChannelData(0);
      // Преобразуем Float32 -> Int16 (сервер ждет int16)
      const int16Data = new Int16Array(inputBuffer.length);
      for (let i = 0; i < inputBuffer.length; i++) {
        // от -1..1 => -32768..32767
        let sample = inputBuffer[i] * 32768;
        if (sample > 32767) sample = 32767;
        if (sample < -32768) sample = -32768;
        int16Data[i] = sample;
      }

      // Отправляем на сервер по WebSocket
      if (websocket && websocket.readyState === WebSocket.OPEN) {
        websocket.send(int16Data.buffer);
      }
    };

    source.connect(processorNode);
    processorNode.connect(audioContext.destination); // Или audioContext.createGain() без выхода, чтобы не слышать эхо

    // Открываем WebSocket
    websocket = new WebSocket(`wss://${window.location.host}/ws`);
    // Если локально: `ws://localhost:8000/ws` или `wss://YOUR_DOMAIN/ws`
    
    websocket.onopen = () => {
      console.log("WS connected.");
      statusSpan.textContent = "РАБОТАЕТ";
    };
    websocket.onmessage = (msg) => {
      // Сервер присылает строку вида "0.853|false" => corr|detected
      const parts = msg.data.split("|");
      if (parts.length === 2) {
        const corr = parseFloat(parts[0]);
        const detected = (parts[1] === "true");
        corrSpan.textContent = corr.toFixed(3);
        
        if (detected) {
          // Показываем надпись
          alertP.style.display = "inline";
          // Проигрываем alarm (может быть заблокирован автоплей!)
          alarmAudio.currentTime = 0;
          alarmAudio.play().catch(err => {
            console.log("Autoplay blocked:", err);
          });
        } else {
          alertP.style.display = "none";
        }
      }
    };
    websocket.onclose = () => {
      console.log("WS closed.");
      statusSpan.textContent = "ВЫКЛЮЧЕНА";
      corrSpan.textContent = "-";
      alertP.style.display = "none";
    };

    isRunning = true;
    statusSpan.textContent = "РАБОТАЕТ";
  } catch (e) {
    console.error("Error in startBtn:", e);
    alert("Не удалось получить доступ к микрофону.");
  }
};

// При нажатии "Отключить"
stopBtn.onclick = () => {
  if (!isRunning) return;
  isRunning = false;

  // Отключаем mediaStream
  if (mediaStream) {
    mediaStream.getTracks().forEach(track => track.stop());
  }
  mediaStream = null;

  // Закрываем AudioContext
  if (processorNode) {
    processorNode.disconnect();
    processorNode = null;
  }
  if (audioContext) {
    audioContext.close();
    audioContext = null;
  }

  // Закрываем WebSocket
  if (websocket) {
    websocket.close();
    websocket = null;
  }

  statusSpan.textContent = "ВЫКЛЮЧЕНА";
  corrSpan.textContent = "-";
  alertP.style.display = "none";
};
