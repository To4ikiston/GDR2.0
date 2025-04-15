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

# Теперь 3 секунды — одна попытка
BUFFER_DURATION = 3.0  
RING_BUFFER_MAXSIZE = int(SAMPLE_RATE * BUFFER_DURATION)

#COOLDOWN = 5.0
#last_detection_time = 0.0

model = None
ml_threshold = 0.8   # можно менять слайдером
rms_min = 0.05       # отсечь тихие звуки
gain_factor = 10.0   # усиление

RING_BUFFER = deque()

################################################################################
# ЗАГРУЗКА МОДЕЛИ
################################################################################
@app.on_event("startup")
def load_model():
    global model
    try:
        with open("drone_model.pkl", "rb") as f:
            model = pickle.load(f)
        print("drone_model.pkl loaded OK, predict_proba() is ready.")
    except Exception as e:
        print("Failed to load drone_model.pkl:", e)

################################################################################
# Вспомогательная функция извлечения признаков
################################################################################
def extract_features(wave: np.ndarray):
    """
    Извлекаем MFCC + проверяем RMS.
    Вернёт (feats, rms) или (None, rms), если звук слишком тихий.
    """
    rms = float(np.sqrt(np.mean(wave**2)))
    print(f"[DEBUG] RMS={rms:.3f}")

    # Отсечём слишком тихие сигналы
    if rms < rms_min:
        return None, rms

    # Нормализуем + усиливаем (gain_factor)
    mx = np.max(np.abs(wave))
    if mx < 1e-9:
        return None, rms
    wave = wave / mx
    wave *= gain_factor
    wave = np.clip(wave, -1.0, 1.0)

    try:
        mfcc = librosa.feature.mfcc(y=wave, sr=SAMPLE_RATE, n_mfcc=13)
        feats = np.mean(mfcc, axis=1)
        return feats, rms
    except Exception as ex:
        print("extract_features error:", ex)
        return None, rms

################################################################################
# WEB SOCKET
################################################################################
@app.websocket("/ws")
async def ws_endpoint(ws: WebSocket):
    global RING_BUFFER, RING_BUFFER_MAXSIZE
    global last_detection_time
    global ml_threshold, rms_min, gain_factor

    await ws.accept()
    print("[WS] Client connected (3sec attempt).")

    try:
        while True:
            msg = await ws.receive_text()

            # Пришли сырые данные звука
            if msg.startswith("AUDIO|"):
                rawb64 = msg[6:]
                raw = base64.b64decode(rawb64)
                chunk = np.frombuffer(raw, dtype=np.int16).astype(np.float32) / 32768.0

                # Копим во временном буфере
                for s in chunk:
                    RING_BUFFER.append(s)
                while len(RING_BUFFER) > RING_BUFFER_MAXSIZE:
                    RING_BUFFER.popleft()

                # Если накопилось >= 3с
                if len(RING_BUFFER) >= RING_BUFFER_MAXSIZE:
                    wave = np.array(RING_BUFFER, dtype=np.float32)
                    RING_BUFFER.clear()

                    feats, rms_val = extract_features(wave)
                    prob_dron = 0.0
                    detected_ml = False

                    if feats is not None and model is not None:
                        # predict_proba => [prob_class0, prob_class1]
                        probs = model.predict_proba([feats])[0]
                        prob_dron = probs[1]

                        print(f"[DEBUG] prob(dron)={prob_dron:.3f}, threshold={ml_threshold}, RMS={rms_val:.3f}")

                        # Если вероятность выше порога => дрон
                        if prob_dron >= ml_threshold:
                            detected_ml = True

                    else:
                        # feats = None => звук слишком тихий
                        pass

                    # Отправим результат клиенту
                    payload = {
                        "type": "ML_ANALYSIS",
                        "prob": prob_dron,
                        "rms": rms_val,
                        "detected": detected_ml
                    }
                    await ws.send_json(payload)

            # Параметры слайдеров
            elif msg.startswith("PARAMS|"):
                param_str = msg[7:]
                parts = param_str.split(",")
                for p in parts:
                    k, v = p.split("=")
                    if k == "th":
                        ml_threshold = float(v)
                    elif k == "rms":
                        rms_min = float(v)
                    elif k == "gain":
                        gain_factor = float(v)
                print(f"[WS] Updated sliders => ml_threshold={ml_threshold}, rms_min={rms_min}, gain_factor={gain_factor}")

            else:
                print("[WS] Unknown msg:", msg)

    except Exception as e:
        print("[WS] Error:", e)

    print("[WS] disconnected.")
