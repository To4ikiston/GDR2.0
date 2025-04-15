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
app.mount("/static", StaticFiles(directory="static"), name="static")

@app.get("/")
def root():
    return RedirectResponse(url="/static/index.html")

################################################################################
# Глобальные настройки
################################################################################

SAMPLE_RATE = 16000
BUFFER_DURATION = 3  # сек записи
model = None

COOLDOWN = 5.0
last_detection_time = 0.0

# ПЕРЕМЕННЫЕ, которые управляются ползунками
rms_min = 0.05        # отсекаем тихое, ползунок [0..0.2]
gain_factor = 10.0    # усиливаем сигнал, ползунок [1..20]
ml_threshold = 0.80   # вероятность дрона, ползунок [0.5..0.99]

# Кольцевой буфер 3 сек
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
        print("drone_model.pkl loaded OK (with predict_proba hopefully).")
    except Exception as e:
        print("Failed to load drone_model.pkl:", e)

################################################################################
# Извлечение признаков
################################################################################
def extract_features(wave: np.ndarray, rms_min: float, gain: float):
    # RMS
    rms = float(np.sqrt(np.mean(wave**2)))
    print(f"[DEBUG] RMS = {rms:.3f}")

    # если слишком тихо, не анализируем
    if rms < rms_min:
        return None, rms

    # нормализуем
    mx = np.max(np.abs(wave))
    if mx < 1e-9:
        return None, rms
    wave = wave / mx
    # усиливаем
    wave *= gain
    wave = np.clip(wave, -1.0, 1.0)

    try:
        mfcc = librosa.feature.mfcc(y=wave, sr=SAMPLE_RATE, n_mfcc=13)
        feats = np.mean(mfcc, axis=1)
        return feats, rms
    except Exception as ex:
        print("extract_features error:", ex)
        return None, rms

################################################################################
# WebSocket
################################################################################
@app.websocket("/ws")
async def ws_endpoint(ws: WebSocket):
    global RING_BUFFER, RING_BUFFER_MAXSIZE
    global last_detection_time
    global rms_min, gain_factor, ml_threshold

    await ws.accept()
    print("[WS] Client connected (ML with sliders).")

    try:
        while True:
            msg = await ws.receive_text()
            if msg.startswith("AUDIO|"):
                # раскодируем PCM int16
                rawb64 = msg[6:]
                raw = base64.b64decode(rawb64)
                chunk = np.frombuffer(raw, dtype=np.int16).astype(np.float32) / 32768.0

                for sample in chunk:
                    RING_BUFFER.append(sample)
                while len(RING_BUFFER) > RING_BUFFER_MAXSIZE:
                    RING_BUFFER.popleft()

                # если собрали 3 секунды
                if len(RING_BUFFER) >= RING_BUFFER_MAXSIZE:
                    wave = np.array(RING_BUFFER, dtype=np.float32)
                    RING_BUFFER.clear()

                    feats, rms_val = extract_features(wave, rms_min, gain_factor)
                    detected = False
                    if feats is not None and model is not None:
                        # смотрим predict_proba
                        prob = model.predict_proba([feats])[0][1]
                        print(f"[DEBUG] prob(drone)={prob:.3f}, ml_threshold={ml_threshold:.2f}, RMS={rms_val:.3f}")
                        if prob > ml_threshold:
                            now = time.time()
                            if now - last_detection_time > COOLDOWN:
                                detected = True
                                last_detection_time = now

                    resp = {
                        "type": "ML_ANALYSIS",
                        "detected": detected,
                        "rms": rms_val
                    }
                    await ws.send_json(resp)

            elif msg.startswith("PARAMS|"):
                # пример "PARAMS|rms=0.10,gain=15,mlth=0.90"
                param_str = msg[7:]
                parts = param_str.split(",")
                for p in parts:
                    k, v = p.split("=")
                    if k == "rms":
                        rms_min = float(v)
                    elif k == "gain":
                        gain_factor = float(v)
                    elif k == "mlth":
                        ml_threshold = float(v)
                print(f"[WS] Updated sliders: rms_min={rms_min}, gain={gain_factor}, ml_threshold={ml_threshold}")

            else:
                print("[WS] Unknown message:", msg)

    except Exception as e:
        print("[WS] Error:", e)

    print("[WS] disconnected.")
