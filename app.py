import os
import time
import numpy as np
import librosa
import pickle
import base64

from fastapi import FastAPI, WebSocket
from fastapi.staticfiles import StaticFiles
from fastapi.responses import RedirectResponse
from collections import deque

app = FastAPI()

# Подключаем статические файлы
app.mount("/static", StaticFiles(directory="static"), name="static")

@app.get("/")
def root():
    return RedirectResponse(url="/static/index.html")

################################################################################
# Глобальные переменные
################################################################################

SAMPLE_RATE = 16000
BUFFER_DURATION = 3  # 3 секунды
model = None         # ML-модель

COOLDOWN = 5.0
last_detection_time = 0.0

RING_BUFFER = deque()
RING_BUFFER_MAXSIZE = int(SAMPLE_RATE * BUFFER_DURATION)

################################################################################
# Загрузка модели
################################################################################
@app.on_event("startup")
def load_model():
    global model
    try:
        with open("drone_model.pkl", "rb") as f:
            model = pickle.load(f)
        print("drone_model.pkl loaded OK.")
    except Exception as e:
        print("Failed to load drone_model.pkl:", e)

################################################################################
# Функция извлечения признаков
################################################################################
def extract_features(wave: np.ndarray):
    # Логируем RMS, чтобы понять громкость
    rms = float(np.sqrt(np.mean(wave**2)))
    print(f"[DEBUG] RMS = {rms:.5f}")

    # Упростим нормализацию: пик + умножим на 10
    mx = np.max(np.abs(wave))
    if mx < 1e-9:
        return None, rms
    wave = wave / mx
    wave *= 10.0
    wave = np.clip(wave, -1, 1)

    try:
        mfcc = librosa.feature.mfcc(y=wave, sr=SAMPLE_RATE, n_mfcc=13)
        feats = np.mean(mfcc, axis=1)  # усреднение
        return feats, rms
    except Exception as ex:
        print("extract_features error:", ex)
        return None, rms

################################################################################
# WebSocket
################################################################################
@app.websocket("/ws")
async def ws_endpoint(ws: WebSocket):
    global RING_BUFFER, RING_BUFFER_MAXSIZE, last_detection_time, model

    await ws.accept()
    print("[WS] Client connected (ML + RMS).")

    try:
        while True:
            msg = await ws.receive_text()
            if msg.startswith("AUDIO|"):
                base64pcm = msg[6:]
                raw = base64.b64decode(base64pcm)
                chunk_arr = np.frombuffer(raw, dtype=np.int16).astype(np.float32) / 32768.0

                # накапливаем
                for sample in chunk_arr:
                    RING_BUFFER.append(sample)
                while len(RING_BUFFER) > RING_BUFFER_MAXSIZE:
                    RING_BUFFER.popleft()

                # если накопили 3 секунды
                if len(RING_BUFFER) >= RING_BUFFER_MAXSIZE:
                    wave = np.array(RING_BUFFER, dtype=np.float32)
                    RING_BUFFER.clear()

                    feats, rms = extract_features(wave)
                    detected = False
                    if feats is not None and model is not None:
                        pred = model.predict([feats])[0]
                        if pred == 1:
                            now = time.time()
                            if now - last_detection_time > COOLDOWN:
                                detected = True
                                last_detection_time = now

                    payload = {
                        "type": "ML_ANALYSIS",
                        "detected": detected,
                        "rms": rms
                    }
                    print("[DEBUG] sending:", payload)
                    await ws.send_json(payload)

            else:
                print("[WS] Unknown message:", msg)

    except Exception as e:
        print("[WS] error:", e)

    print("[WS] disconnected.")
