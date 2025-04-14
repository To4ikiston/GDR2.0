import os
import time
import numpy as np
import librosa
import io
import base64
import matplotlib.pyplot as plt

from fastapi import FastAPI, WebSocket
from fastapi.staticfiles import StaticFiles
from fastapi.responses import RedirectResponse
from collections import deque

app = FastAPI()

# Подключаем /static
app.mount("/static", StaticFiles(directory="static"), name="static")

@app.get("/")
def root():
    return RedirectResponse(url="/static/index.html")

################################################################################
# Глобальные переменные
################################################################################
corr_streak = 0

SAMPLE_RATE = 16000

# Храним эталон дрона (MFCC)
drone_mfcc = None

# Для каждой сессии можно было бы хранить настройки, 
# но пока упростим: один глобальный порог, одна длительность.
# В идеале — сделать хранение в словаре по session id. 
global_threshold = 0.7
global_buffer_duration = 3  # секунды

# Кольцевой буфер
RING_BUFFER = deque()
RING_BUFFER_MAXSIZE = SAMPLE_RATE * global_buffer_duration

# Время последнего срабатывания тревоги (для cooldown)
last_detection_time = 0.0
COOLDOWN = 5.0

################################################################################
# Загрузка дрона при старте
################################################################################
@app.on_event("startup")
def load_drone_sample():
    global drone_mfcc
    try:
        audio, _ = librosa.load("drone_sample.wav", sr=SAMPLE_RATE)
        drone_mfcc = librosa.feature.mfcc(y=audio, sr=SAMPLE_RATE, n_mfcc=13)
        print("drone_sample.wav loaded OK.")
    except Exception as e:
        print("Failed to load drone_sample.wav:", e)

################################################################################
# Вспомогательные функции
################################################################################
def clamp_audio(audio: np.ndarray) -> np.ndarray:
    audio = np.nan_to_num(audio, nan=0.0, posinf=0.0, neginf=0.0)
    return np.clip(audio, -1.0, 1.0)

def compute_correlation(ref_mfcc, test_audio):
    if ref_mfcc is None or len(test_audio) < 100:
        return 0.0
    try:
        test_audio = clamp_audio(test_audio)
        test_audio = normalize_audio(test_audio)
        test_mfcc = librosa.feature.mfcc(y=test_audio, sr=SAMPLE_RATE, n_mfcc=13)
        ml = min(ref_mfcc.shape[1], test_mfcc.shape[1])
        s_trim = ref_mfcc[:, :ml]
        t_trim = test_mfcc[:, :ml]
        corr = np.corrcoef(s_trim.flatten(), t_trim.flatten())[0, 1]
        if np.isnan(corr):
            corr = 0.0
        return corr if corr > 0 else 0
    except Exception as ex:
        print("compute_correlation error:", ex)
        return 0.0

def generate_spectrogram(wave: np.ndarray):
    """Создаем спектрограмму из массива wave (float32, -1..1).
       Возвращаем base64 PNG.
    """
    wave = clamp_audio(wave)
    fig, ax = plt.subplots(figsize=(3, 2), dpi=100)
    D = np.abs(librosa.stft(wave, n_fft=512))**2
    S = librosa.power_to_db(D, ref=np.max)
    img = librosa.display.specshow(S, sr=SAMPLE_RATE,
                                   x_axis='time', y_axis='hz', ax=ax)
    fig.colorbar(img, ax=ax, format="%+2.0f dB")
    ax.set_title("Спектрограмма")

    buf = io.BytesIO()
    fig.tight_layout()
    plt.savefig(buf, format='png')
    plt.close(fig)

    buf.seek(0)
    b64_str = base64.b64encode(buf.read()).decode('utf-8')
    return "data:image/png;base64," + b64_str

def normalize_audio(x: np.ndarray) -> np.ndarray:
    return x / np.max(np.abs(x)) if np.max(np.abs(x)) != 0 else x




################################################################################
# WebSocket
################################################################################
@app.websocket("/ws")
async def websocket_endpoint(ws: WebSocket):
    global RING_BUFFER, RING_BUFFER_MAXSIZE
    global last_detection_time, global_threshold, global_buffer_duration

    await ws.accept()
    print("[WS] Client connected.")

    # Локальная история корреляций, чтобы посылать на график
    correlation_history = []

    try:
        while True:
            # Ждём сообщение (bytes или text)
            msg = await ws.receive_text()
            # msg вида: "AUDIO|BASE64" или "PARAMS|threshold=...,buffer=..."
            # Упростим: будем ожидать text, в котором есть "type=..."

            if msg.startswith("AUDIO|"):
                # После "AUDIO|" идёт base64 PCM int16
                encoded_pcm = msg[6:]
                raw = base64.b64decode(encoded_pcm)
                chunk_arr = np.frombuffer(raw, dtype=np.int16).astype(np.float32) / 32768.0

                # Складываем в кольцевой буфер
                for s in chunk_arr:
                    RING_BUFFER.append(s)
                # Если переполнилось, dequeue выбросит старые

                # Если накопилось >= RING_BUFFER_MAXSIZE, считаем корреляцию
                if len(RING_BUFFER) >= RING_BUFFER_MAXSIZE:
                    wave = np.array(RING_BUFFER, dtype=np.float32)
                    corr = compute_correlation(drone_mfcc, wave)
                    correlation_history.append(corr)

                    # Проверка дрона
                    detected = False
                    if corr > global_threshold:
                        corr_streak += 1
                    else:
                        corr_streak = 0

                    now = time.time()
                    if corr_streak >= 2 and (now - last_detection_time > COOLDOWN):
                        detected = True
                        last_detection_time = now

                    # Генерим спектрограмму
                    spec_b64 = generate_spectrogram(wave)

                    # Сбрасываем буфер (можно «скользящее окно», но упростим)
                    RING_BUFFER.clear()

                    # Отправим JSON со структурой
                    # corr=..., detected=..., spec=...
                    # + history (последние 20 значений)
                    hlast = correlation_history[-20:]
                    payload = {
                        "type": "ANALYSIS",
                        "corr": corr,
                        "detected": detected,
                        "spectrogram": spec_b64,
                        "history": hlast
                    }
                    await ws.send_json(payload)

            elif msg.startswith("PARAMS|"):
                # например: "PARAMS|threshold=0.98,buffer=5"
                param_str = msg[7:]
                # threshold=0.98,buffer=5
                parts = param_str.split(",")
                thr_part = parts[0]  # threshold=0.98
                buf_part = parts[1]  # buffer=5

                thr_val = float(thr_part.split("=")[1])
                buf_val = float(buf_part.split("=")[1])

                global_threshold = thr_val
                global_buffer_duration = buf_val
                # пересчитаем RING_BUFFER_MAXSIZE
                RING_BUFFER_MAXSIZE = int(SAMPLE_RATE * global_buffer_duration)

                # Сбросим буфер при смене настроек
                RING_BUFFER.clear()

                # Ответим подтверждением
                resp = {
                    "type": "PARAMS_ACK",
                    "threshold": global_threshold,
                    "bufferDuration": global_buffer_duration
                }
                await ws.send_json(resp)

    except Exception as e:
        print("[WS] Error:", e)

    print("[WS] Client disconnected.")


################################################################################
# Точка входа
################################################################################
if __name__ == "__main__":
    import uvicorn
    port = int(os.environ.get("PORT", 8000))
    uvicorn.run("app:app", host="0.0.0.0", port=port)
