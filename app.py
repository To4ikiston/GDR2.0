import uvicorn
from fastapi import FastAPI, WebSocket, Request
from fastapi.responses import HTMLResponse
import numpy as np
import librosa
import time
from fastapi.staticfiles import StaticFiles

app = FastAPI()

# подключаем папку static
app.mount("/static", StaticFiles(directory="static"), name="static")

@app.get("/")
def redirect_root():
    return RedirectResponse(url="/static/index.html")

SAMPLE_RATE = 16000
THRESHOLD = 0.95
COOLDOWN = 5.0  # 5 сек между срабатываниями

drone_mfcc = None
last_detection_time = 0.0

@app.on_event("startup")
def load_drone_sample():
    global drone_mfcc
    try:
        audio, _ = librosa.load("drone_sample.wav", sr=SAMPLE_RATE)
        mfcc = librosa.feature.mfcc(y=audio, sr=SAMPLE_RATE, n_mfcc=13)
        drone_mfcc = mfcc
        print("drone_sample.wav loaded OK.")
    except Exception as e:
        print("Failed to load drone_sample.wav:", e)

def clamp_audio(audio: np.ndarray) -> np.ndarray:
    audio = np.nan_to_num(audio, nan=0.0, posinf=0.0, neginf=0.0)
    return np.clip(audio, -1.0, 1.0)

def compute_correlation(sample_mfcc, test_audio):
    if sample_mfcc is None:
        return 0.0
    try:
        test_audio = clamp_audio(test_audio)
        test_mfcc = librosa.feature.mfcc(y=test_audio, sr=SAMPLE_RATE, n_mfcc=13)
        ml = min(sample_mfcc.shape[1], test_mfcc.shape[1])
        s_trim = sample_mfcc[:, :ml]
        t_trim = test_mfcc[:, :ml]
        corr = np.corrcoef(s_trim.flatten(), t_trim.flatten())[0, 1]
        if np.isnan(corr):
            corr = 0.0
        return corr
    except Exception as ex:
        print("compute_correlation error:", ex)
        return 0.0

@app.websocket("/ws")
async def websocket_endpoint(ws: WebSocket):
    global last_detection_time

    await ws.accept()
    print("[WS] Client connected.")

    while True:
        try:
            data = await ws.receive_bytes()
            # data - int16 PCM
            audio_arr = np.frombuffer(data, dtype=np.int16).astype(np.float32)
            audio_arr /= 32768.0  # нормируем в [-1..1]

            corr = compute_correlation(drone_mfcc, audio_arr)
            detected = False
            now = time.time()
            if corr >= THRESHOLD:
                if now - last_detection_time > COOLDOWN:
                    detected = True
                    last_detection_time = now

            # Посылаем назад строку "corr|detected"
            msg = f"{corr:.3f}|{'true' if detected else 'false'}"
            await ws.send_text(msg)
        except Exception as e:
            print("[WS] Error:", e)
            break

    print("[WS] Client disconnected.")

@app.get("/")
async def root():
    return HTMLResponse("""
    <html>
      <head>
        <meta http-equiv="refresh" content="0; url=/static/index.html" />
      </head>
      <body>Redirecting...</body>
    </html>
    """)

if __name__ == "__main__":
    import os
    import uvicorn
    port = int(os.environ.get("PORT", 8000))
    uvicorn.run("app:app", host="0.0.0.0", port=port)
