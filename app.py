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
# Глобальные переменные
################################################################################

SAMPLE_RATE = 16000
BUFFER_DURATION = 3        # 3 секунды
model = None               # ML-модель
COOLDOWN = 5.0             # задержка между сигналами
last_detection_time = 0.0  # когда последний раз сигналили

# Кольцевой буфер на 3 сек
RING_BUFFER = deque()
RING_BUFFER_MAXSIZE = SAMPLE_RATE * BUFFER_DURATION

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
# Минимальная функция извлечения признаков
################################################################################
def extract_features(wave: np.ndarray):
    # Упростим: пик-нормализация + умножим на 5
    mx = np.max(np.abs(wave))
    if mx < 1e-9:
        return None
    wave = wave / mx
    wave *= 5.0
    wave = np.clip(wave, -1, 1)

    # MFCC
    try:
        mfcc = librosa.feature.mfcc(y=wave, sr=SAMPLE_RATE, n_mfcc=13)
        feats = np.mean(mfcc, axis=1)  # усреднение
        return feats
    except:
        return None

################################################################################
# WebSocket
################################################################################
@app.websocket("/ws")
async def ws_endpoint(ws: WebSocket):
    global RING_BUFFER, RING_BUFFER_MAXSIZE, last_detection_time

    await ws.accept()
    print("[WS] Client connected (ML-only).")

    try:
        while True:
            msg = await ws.receive_text()
            if msg.startswith("AUDIO|"):
                encoded_pcm = msg[6:]
                raw = base64.b64decode(encoded_pcm)
                chunk_arr = np.frombuffer(raw, dtype=np.int16).astype(np.float32) / 32768.0

                # накапливаем в буфер
                for sample in chunk_arr:
                    RING_BUFFER.append(sample)
                while len(RING_BUFFER) > RING_BUFFER_MAXSIZE:
                    RING_BUFFER.popleft()

                # если накопили 3 сек
                if len(RING_BUFFER) >= RING_BUFFER_MAXSIZE:
                    wave = np.array(RING_BUFFER, dtype=np.float32)

                    # очистим буфер
                    RING_BUFFER.clear()

                    # Извлекаем признаки
                    feats = extract_features(wave)
                    detected = False
                    if feats is not None and model is not None:
                        pred = model.predict([feats])[0]  # 0 или 1
                        if pred == 1:
                            # проверим cooldown
                            now = time.time()
                            if now - last_detection_time > COOLDOWN:
                                detected = True
                                last_detection_time = now
                    
                    # отсылаем назад
                    payload = {
                        "type": "ML_ANALYSIS",
                        "detected": detected
                    }
                    await ws.send_json(payload)

            else:
                print("[WS] Unknown message:", msg)

    except Exception as e:
        print("[WS] Error:", e)

    print("[WS] disconnected.")


if __name__ == "__main__":
    import uvicorn
    uvicorn.run("app:app", host="0.0.0.0", port=8000)
