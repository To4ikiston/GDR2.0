import os
import time
import numpy as np
import librosa
import io
import base64
import matplotlib.pyplot as plt
import pickle  # чтобы загрузить ML-модель

from fastapi import FastAPI, WebSocket
from fastapi.staticfiles import StaticFiles
from fastapi.responses import RedirectResponse
from collections import deque

app = FastAPI()

# Подключаем /static (для index.html, main.js, alarm.wav)
app.mount("/static", StaticFiles(directory="static"), name="static")

@app.get("/")
def root():
    return RedirectResponse(url="/static/index.html")

################################################################################
# Глобальные переменные
################################################################################

SAMPLE_RATE = 16000

# --------------------- ПАРАМЕТРЫ ДЛЯ КОРРЕЛЯЦИИ ---------------------
# Храним эталон дрона (MFCC) для старого метода
drone_mfcc = None
global_threshold = 0.7        # Порог сходства
global_buffer_duration = 3    # Длина буфера (сек)
COOLDOWN = 5.0                # Задержка повторного срабатывания

corr_streak = 0               # Считаем подряд успехи корреляции
last_detection_time = 0.0     # Время последнего срабатывания

# Кольцевой буфер (чтобы накапливать 3 сек звука)
RING_BUFFER = deque()
RING_BUFFER_MAXSIZE = SAMPLE_RATE * global_buffer_duration

# --------------------- ПАРАМЕТРЫ ДЛЯ ML-МОДЕЛИ ---------------------
model = None  # здесь будет загружена drone_model.pkl


################################################################################
# Загрузка данных при старте приложения
################################################################################
@app.on_event("startup")
def startup_event():
    global drone_mfcc, model

    # 1) Загружаем эталон для корреляции
    try:
        audio, _ = librosa.load("drone_sample.wav", sr=SAMPLE_RATE)
        drone_mfcc = librosa.feature.mfcc(y=audio, sr=SAMPLE_RATE, n_mfcc=13)
        print("drone_sample.wav loaded OK (для корреляции).")
    except Exception as e:
        print("Failed to load drone_sample.wav for correlation:", e)

    # 2) Загружаем обученную ML-модель (drone_model.pkl)
    try:
        with open("drone_model.pkl", "rb") as f:
            model = pickle.load(f)
        print("drone_model.pkl loaded OK (ML-модель).")
    except Exception as e:
        print("Failed to load drone_model.pkl:", e)


################################################################################
# Вспомогательные функции
################################################################################

def clamp_audio(audio: np.ndarray) -> np.ndarray:
    """Убираем NaN, inf, и прижимаем к [-1..1]."""
    audio = np.nan_to_num(audio, nan=0.0, posinf=0.0, neginf=0.0)
    return np.clip(audio, -1.0, 1.0)

def normalize_audio(x: np.ndarray) -> np.ndarray:
    # Пик-нормализация
    x = np.nan_to_num(x, nan=0.0, posinf=0.0, neginf=0.0)
    maximum = np.max(np.abs(x))
    if maximum < 1e-9:
        return x
    x = x / maximum
    # Допустим, ещё умножить на 5
    x = x * 5.0
    # А потом, если снова превысили [-1..1], прижать:
    return np.clip(x, -1.0, 1.0)

def compute_correlation(ref_mfcc, test_audio):
    """Старый метод: корреляция MFCC c эталоном."""
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
        # Отбрасываем отрицательные корреляции
        return corr if corr > 0 else 0.0
    except Exception as ex:
        print("compute_correlation error:", ex)
        return 0.0

def extract_features_for_ml(test_audio):
    """Функция, аналогичная train_model.py:
       Берём MFCC, усредняем - для ML-модели."""
    try:
        test_audio = clamp_audio(test_audio)
        test_audio = normalize_audio(test_audio)
        # получаем MFCC
        mfcc = librosa.feature.mfcc(y=test_audio, sr=SAMPLE_RATE, n_mfcc=13)
        # усредняем по времени (axis=1 → строка)
        feats = np.mean(mfcc, axis=1)
        return feats
    except Exception as e:
        print("extract_features_for_ml error:", e)
        return None

def generate_spectrogram(wave: np.ndarray):
    """Создаем спектрограмму из массива wave (float32, -1..1).
       Возвращаем base64 PNG."""
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


################################################################################
# WebSocket — основной цикл получения звука
################################################################################
@app.websocket("/ws")
async def websocket_endpoint(ws: WebSocket):
    global RING_BUFFER, RING_BUFFER_MAXSIZE
    global last_detection_time, global_threshold, global_buffer_duration
    global corr_streak

    await ws.accept()
    print("[WS] Client connected.")

    correlation_history = []

    try:
        while True:
            msg = await ws.receive_text()
            # msg формата "AUDIO|{base64}" или "PARAMS|threshold=...,buffer=..."

            if msg.startswith("AUDIO|"):
                # 1) Раскодируем base64 → int16 → float32 в [-1..1]
                encoded_pcm = msg[6:]
                raw = base64.b64decode(encoded_pcm)
                chunk_arr = np.frombuffer(raw, dtype=np.int16).astype(np.float32) / 32768.0

                # 2) Накапливаем в кольцевой буфер
                for s in chunk_arr:
                    RING_BUFFER.append(s)
                if len(RING_BUFFER) > RING_BUFFER_MAXSIZE:
                    # если переполнилось — удалим лишнее
                    for _ in range(len(RING_BUFFER) - RING_BUFFER_MAXSIZE):
                        RING_BUFFER.popleft()

                # 3) Если буфер заполнился
                if len(RING_BUFFER) >= RING_BUFFER_MAXSIZE:
                    wave = np.array(RING_BUFFER, dtype=np.float32)

                    # ==========================
                    # СТАРЫЙ МЕТОД (Корреляция)
                    # ==========================
                     # >>> Вставляем лог RMS:
                    rms = np.sqrt(np.mean(wave**2))
                    print(f"[DEBUG] RMS = {rms:.6f}")
                    corr = compute_correlation(drone_mfcc, wave)
                    correlation_history.append(corr)

                    # Логика "corr_streak"
                    detected_corr = False
                    if corr > global_threshold:
                        corr_streak += 1
                    else:
                        corr_streak = 0

                    now = time.time()
                    if corr_streak >= 2 and (now - last_detection_time > COOLDOWN):
                        detected_corr = True
                        last_detection_time = now

                    # ==========================
                    # НОВЫЙ МЕТОД (ML-модель)
                    # ==========================
                    detected_ml = False
                    feats = extract_features_for_ml(wave)
                    if feats is not None and model is not None:
                        pred = model.predict([feats])[0]  # 0 или 1
                        if pred == 1:
                            detected_ml = True

                    # ==========================
                    # Итоговое решение (пример: ИЛИ)
                    # ==========================
                    # если хотя бы один метод сказал "дрон" → "дрон"
                    detected_final = (detected_corr or detected_ml)

                    # Генерим спектрограмму (для визуализации)
                    spec_b64 = generate_spectrogram(wave)

                    # Очистим буфер (упрощённо)
                    RING_BUFFER.clear()

                    # Формируем ответ
                    hlast = correlation_history[-20:]  # последние 20 корр
                    payload = {
                        "type": "ANALYSIS",
                        "corr": corr,
                        "detected_corr": detected_corr,
                        "detected_ml": detected_ml,
                        "detected_final": detected_final,
                        "spectrogram": spec_b64,
                        "history": hlast
                    }
                    await ws.send_json(payload)

            elif msg.startswith("PARAMS|"):
                # Обновляем порог и длину буфера
                param_str = msg[7:]
                parts = param_str.split(",")
                thr_val = float(parts[0].split("=")[1])
                buf_val = float(parts[1].split("=")[1])

                global_threshold = thr_val
                global_buffer_duration = buf_val
                RING_BUFFER_MAXSIZE = int(SAMPLE_RATE * global_buffer_duration)
                RING_BUFFER.clear()

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
