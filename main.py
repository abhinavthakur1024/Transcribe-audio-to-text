# main.py
import os
import time
import json
import queue
import threading
import re
from vosk import Model, KaldiRecognizer
import sounddevice as sd
from transformers import pipeline

# ------------- CONFIG -------------
SAMPLE_RATE = 16000
VOSK_MODEL_PATH = "models/vosk-model-small-en-us-0.15"   # <- set to your downloaded model folder
SUMMARIZER_MODEL = "sshleifer/distilbart-cnn-12-6"       # small, CPU-friendly summarizer
WORDS_TO_TRIGGER_SUMMARY = 120                           # number of words buffered before summary
TIME_TRIGGER_SECONDS = 45                                # also create summary at least every N seconds
MAX_CHUNK_WORDS = 250                                    # summarizer chunk size
# ----------------------------------

if not os.path.exists(VOSK_MODEL_PATH):
    raise SystemExit(f"Vosk model not found at {VOSK_MODEL_PATH}. Download and unzip into that path.")

print("Loading Vosk model...")
model = Model(VOSK_MODEL_PATH)
rec = KaldiRecognizer(model, SAMPLE_RATE)
rec.SetWords(False)   # we only need text; set to True if word timestamps needed

print("Loading summarizer (this may take ~30s first run while model downloads)...")
summarizer = pipeline("summarization", model=SUMMARIZER_MODEL)

audio_q = queue.Queue()

def audio_callback(indata, frames, time_info, status):
    """sounddevice RawInputStream callback; receives bytes-like data"""
    # indata is already bytes when using RawInputStream with dtype='int16'
    audio_q.put(bytes(indata))

def summarize_text(text):
    """Chunk the text into manageable pieces and summarize each chunk."""
    words = text.strip().split()
    if not words:
        return ""
    chunks = [" ".join(words[i:i+MAX_CHUNK_WORDS]) for i in range(0, len(words), MAX_CHUNK_WORDS)]
    outs = []
    for c in chunks:
        try:
            out = summarizer(c, max_length=130, min_length=20, do_sample=False)
            outs.append(out[0]["summary_text"])
        except Exception as e:
            # if summarizer fails for a chunk, skip but continue
            outs.append("")
            print("[WARN] summarizer error for chunk:", e)
    # join partial summaries into one
    return " ".join([o for o in outs if o])

def bulletify(text):
    """Turn a paragraph summary into short bullets (split by sentences)."""
    sents = re.split(r'(?<=[.!?])\s+', text)
    bullets = [s.strip() for s in sents if s.strip()]
    return bullets

def consumer_loop():
    """Main consumer: read audio chunks, feed Vosk, show partials/finals, generate rolling summaries."""
    buffer_text = ""
    last_summary_time = time.time()

    print("\n--- Listening (live captions + rolling summaries). Press Ctrl+C to stop. ---\n")
    while True:
        data = audio_q.get()
        if rec.AcceptWaveform(data):
            res = json.loads(rec.Result())
            text = res.get("text", "").strip()
            if text:
                # final caption chunk
                print("\n[CAPTION] ", text)
                buffer_text += " " + text
        else:
            partial = json.loads(rec.PartialResult()).get("partial", "")
            # show partial inline (overwrites same line)
            print(f"\r[PARTIAL] {partial}", end="", flush=True)

        now = time.time()
        num_words = len(buffer_text.split())
        if (num_words >= WORDS_TO_TRIGGER_SUMMARY) or (now - last_summary_time >= TIME_TRIGGER_SECONDS and num_words > 5):
            print("\n\n--- Generating rolling summary of recent speech ---")
            try:
                summary = summarize_text(buffer_text)
                if summary:
                    bullets = bulletify(summary)
                    print("[SUMMARY]")
                    for b in bullets:
                        print(" •", b)
                else:
                    print("[SUMMARY] (empty)")
            except Exception as e:
                print("[ERROR] summarization failed:", e)

            # keep a short overlap of the last N words so we don't lose context
            keep = 30
            words = buffer_text.split()
            buffer_text = " ".join(words[-keep:]) if len(words) > keep else buffer_text
            last_summary_time = now
            print("\n--- Listening (resumed) ---\n", end="")

def main():
    consumer_thread = threading.Thread(target=consumer_loop, daemon=True)
    consumer_thread.start()

    # open microphone stream; RawInputStream gives bytes suitable for Vosk
    try:
        with sd.RawInputStream(samplerate=SAMPLE_RATE, blocksize=8000, dtype='int16',
                              channels=1, callback=audio_callback):
            while True:
                time.sleep(0.1)
    except KeyboardInterrupt:
        print("\n\nStopped by user.")
    except Exception as e:
        print("\nError with audio input:", e)

if __name__ == "__main__":
    main()
