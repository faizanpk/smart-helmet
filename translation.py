# translation.py – Fully OFFLINE audio pipeline. No API keys required.
#
# Libraries used:
#   faster-whisper  → Speech-to-Text  (OpenAI Whisper, CPU, int8 quantised)
#   ctranslate2     → Translation     (Helsinki OPUS-MT, same engine as Whisper)
#   sentencepiece   → Tokeniser for OPUS-MT models
#   huggingface-hub → Model download helper (one-time internet, then cached)
#   pyttsx3         → Text-to-Speech  (espeak on Pi/Linux, SAPI on Windows)
#
# Disk footprint after first run (~300 MB total, fully cached):
#   Whisper tiny     ~75 MB   (faster-whisper cache)
#   OPUS-MT en→de   ~100 MB  (HuggingFace cache)
#   OPUS-MT de→en   ~100 MB  (HuggingFace cache)
#
# WHY ctranslate2 for translation instead of argostranslate?
#   argostranslate pulls in torch + stanza + spacy (>2 GB) – too heavy for Pi.
#   ctranslate2 is already installed as a faster-whisper dependency,
#   uses the same efficient quantised runtime, and needs no extra frameworks.
#
# Public API (identical to cloud version – nothing else needs to change):
#   setup_offline_models()             – call once at startup
#   record_until_release(is_held_fn)   – hold-to-talk PCM capture
#   voice_to_text(audio_bytes, lang)   – STT via Whisper
#   translate_text(text, src, tgt)     – offline OPUS-MT translation
#   text_to_speech(text, lang_code)    – pyttsx3 → WAV bytes
#   play_audio_bytes(wav_bytes)        – plays WAV through speaker
#   speak(text, lang_code)             – TTS + immediate local playback
#   translation_pipeline(audio_bytes)  – full send-side pipeline

import os
import json
import math
import io
import platform
import struct
import subprocess
import tempfile
import threading
import wave
import logging

import pyaudio
import pyttsx3
import sentencepiece as spm
import ctranslate2

import config

log = logging.getLogger(__name__)

# ─── HuggingFace model IDs for Helsinki OPUS-MT (CTranslate2 converted) ──────
# These repos contain CTranslate2-format weights (no PyTorch needed).
_OPUS_MODEL_IDS = {
    ("en", "de"): "Helsinki-NLP/opus-mt-en-de",
    ("de", "en"): "Helsinki-NLP/opus-mt-de-en",
}

# Local cache directory for translation models
_MODEL_CACHE_DIR = os.path.join(config.BASE_DIR, "data", "translation_models")


# ─────────────────────────────────────────────────────────────────────────────
# ONE-TIME OFFLINE MODEL SETUP
# ─────────────────────────────────────────────────────────────────────────────

def setup_offline_models() -> None:
    """
    Download and cache offline models on first run (needs internet once).
    Call this once at startup in main.py.

    Downloads:
      • Whisper tiny – cached by faster-whisper / HuggingFace automatically
      • OPUS-MT en→de and de→en – downloaded to data/translation_models/
    """
    os.makedirs(_MODEL_CACHE_DIR, exist_ok=True)
    _ensure_translation_models()
    log.info("[OFFLINE] All models ready.")


def _ensure_translation_models() -> None:
    """Download OPUS-MT CTranslate2 models (en↔de) if not already cached."""
    from huggingface_hub import snapshot_download

    # Always download both directions regardless of current helmet language
    pairs = [("en", "de"), ("de", "en")]
    for from_code, to_code in pairs:
        model_id = _OPUS_MODEL_IDS.get((from_code, to_code))
        if model_id is None:
            log.error("[TRANSLATE] No model defined for %s→%s", from_code, to_code)
            continue
        local_dir = os.path.join(_MODEL_CACHE_DIR, f"{from_code}-{to_code}")
        if os.path.isdir(local_dir) and os.listdir(local_dir):
            log.info("[TRANSLATE] Model %s→%s already cached.", from_code, to_code)
            continue
        log.info("[TRANSLATE] Downloading OPUS-MT %s→%s (~100 MB, first time only)…",
                 from_code, to_code)
        snapshot_download(
            repo_id=model_id,
            local_dir=local_dir,
            ignore_patterns=["*.msgpack", "*.h5", "flax_model*", "tf_model*",
                             "pytorch_model*", "*.bin"],
        )
        log.info("[TRANSLATE] Cached %s→%s to %s", from_code, to_code, local_dir)


# ─────────────────────────────────────────────────────────────────────────────
# OPUS-MT TRANSLATION  (ctranslate2, fully offline)
# ─────────────────────────────────────────────────────────────────────────────

# Singleton translators – loaded on first use
_translators: dict[tuple[str, str], tuple] = {}   # (src, tgt) → (translator, sp_model)
_translator_lock = threading.Lock()


def _get_translator(from_code: str, to_code: str):
    """Lazy-load CTranslate2 translator + SentencePiece tokeniser (singleton)."""
    key = (from_code, to_code)
    with _translator_lock:
        if key not in _translators:
            local_dir = os.path.join(_MODEL_CACHE_DIR, f"{from_code}-{to_code}")
            if not os.path.isdir(local_dir):
                raise FileNotFoundError(
                    f"Translation model not found at {local_dir}. "
                    "Run setup_offline_models() first."
                )
            log.info("[TRANSLATE] Loading OPUS-MT %s→%s model…", from_code, to_code)
            translator = ctranslate2.Translator(
                local_dir,
                device="cpu",
                inter_threads=1,
                intra_threads=2,   # use 2 CPU threads on Pi
            )
            # Load SentencePiece model (source.spm or tokenizer.model)
            sp_path = _find_spm(local_dir)
            sp_model = spm.SentencePieceProcessor()
            sp_model.Load(sp_path)
            _translators[key] = (translator, sp_model)
            log.info("[TRANSLATE] OPUS-MT %s→%s ready.", from_code, to_code)
    return _translators[key]


def _find_spm(model_dir: str) -> str:
    """Find the SentencePiece model file inside a model directory."""
    for name in ("source.spm", "tokenizer.model", "sentencepiece.bpe.model"):
        path = os.path.join(model_dir, name)
        if os.path.isfile(path):
            return path
    raise FileNotFoundError(f"No SentencePiece model found in {model_dir}")


def translate_text(text: str, source: str = None, target: str = None) -> str:
    """
    Translate text offline using CTranslate2 + Helsinki OPUS-MT.
    source/target are short ISO-639-1 codes ("en", "de").
    Defaults: source=HELMET_LANGUAGE_SHORT, target=TARGET_LANGUAGE_SHORT.
    """
    if not text:
        return ""
    if source is None:
        source = config.HELMET_LANGUAGE_SHORT
    if target is None:
        target = config.TARGET_LANGUAGE_SHORT

    translator, sp_model = _get_translator(source, target)

    # Tokenise with SentencePiece
    tokens = sp_model.EncodeAsPieces(text)

    # Translate
    results = translator.translate_batch([tokens])
    output_tokens = results[0].hypotheses[0]

    # Decode output tokens
    translated = sp_model.DecodePieces(output_tokens)
    log.info("[TRANSLATE] '%s' → '%s'", text, translated)
    return translated


# ─────────────────────────────────────────────────────────────────────────────
# FASTER-WHISPER – Speech-to-Text  (fully offline after model download)
# ─────────────────────────────────────────────────────────────────────────────

_whisper_model = None
_whisper_lock  = threading.Lock()


def _get_whisper():
    """Lazy-load the Whisper model singleton (thread-safe)."""
    global _whisper_model
    with _whisper_lock:
        if _whisper_model is None:
            log.info("[STT] Loading Whisper '%s' (first call)…", config.WHISPER_MODEL_SIZE)
            from faster_whisper import WhisperModel
            _whisper_model = WhisperModel(
                config.WHISPER_MODEL_SIZE,
                device="cpu",
                compute_type="int8",   # quantised for Pi speed
            )
            log.info("[STT] Whisper ready.")
    return _whisper_model


def _pcm_to_wav(audio_bytes: bytes, path: str) -> None:
    """Write raw LINEAR16 PCM to a WAV file."""
    with wave.open(path, "wb") as wf:
        wf.setnchannels(config.CHANNELS)
        wf.setsampwidth(2)
        wf.setframerate(config.SAMPLE_RATE)
        wf.writeframes(audio_bytes)


# ─────────────────────────────────────────────────────────────────────────────
# RECORDING
# ─────────────────────────────────────────────────────────────────────────────

def _find_pyaudio_device(p: pyaudio.PyAudio, alsa_name: str, is_input: bool):
    for i in range(p.get_device_count()):
        info = p.get_device_info_by_index(i)
        if alsa_name in info.get("name", ""):
            if is_input and info["maxInputChannels"] > 0:
                return i
            if not is_input and info["maxOutputChannels"] > 0:
                return i
    log.warning("[AUDIO] ALSA device '%s' not found – using system default.", alsa_name)
    return None


def record_until_release(is_held_fn, max_seconds: float = None) -> bytes:
    """
    Capture raw LINEAR16 PCM while is_held_fn() returns True.
    Returns raw PCM bytes (mono, 16-bit, SAMPLE_RATE Hz).
    """
    if max_seconds is None:
        max_seconds = config.RECORD_SECONDS_MAX

    p = pyaudio.PyAudio()
    input_idx = None
    if platform.system() == "Linux":
        input_idx = _find_pyaudio_device(p, config.ALSA_MIC_DEVICE, is_input=True)

    stream = p.open(
        format=pyaudio.paInt16,
        channels=config.CHANNELS,
        rate=config.SAMPLE_RATE,
        input=True,
        input_device_index=input_idx,
        frames_per_buffer=config.CHUNK,
    )

    log.info("[MIC] Recording started.")
    frames = []
    max_chunks = int(config.SAMPLE_RATE / config.CHUNK * max_seconds)

    for _ in range(max_chunks):
        if not is_held_fn():
            break
        data = stream.read(config.CHUNK, exception_on_overflow=False)
        frames.append(data)

    stream.stop_stream()
    stream.close()
    p.terminate()

    duration = len(frames) * config.CHUNK / config.SAMPLE_RATE
    log.info("[MIC] Recording stopped – %.1f s.", duration)
    return b"".join(frames)


# ─────────────────────────────────────────────────────────────────────────────
# SPEECH-TO-TEXT
# ─────────────────────────────────────────────────────────────────────────────

def voice_to_text(audio_bytes: bytes, language_code: str = None) -> str:
    """
    Convert raw PCM → text via local Whisper model.
    language_code is BCP-47 (e.g. "en-US"); short code extracted for Whisper.
    Pass None for Whisper auto-detection.
    """
    if not audio_bytes or len(audio_bytes) < config.CHUNK * 2:
        return ""

    whisper_lang = None
    if language_code:
        whisper_lang = language_code.split("-")[0].lower()   # "en-US" → "en"

    with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as f:
        tmp_wav = f.name
    try:
        _pcm_to_wav(audio_bytes, tmp_wav)
        model = _get_whisper()
        segments, info = model.transcribe(
            tmp_wav,
            language=whisper_lang,
            beam_size=3,
            vad_filter=True,   # automatically skip silence
        )
        text = " ".join(seg.text.strip() for seg in segments).strip()
        if text:
            log.info("[STT] '%s' (lang: %s)", text, info.language)
        else:
            log.warning("[STT] No speech detected.")
        return text
    finally:
        try:
            os.unlink(tmp_wav)
        except OSError:
            pass


def transcribe_only(audio_bytes: bytes) -> tuple:
    """
    Run Whisper STT only (no translation).
    Returns (text: str, detected_language: str).
    detected_language is Whisper's auto-detected short code, e.g. 'en' or 'de'.
    Returns ('', '') on failure or silence.
    """
    if not audio_bytes or len(audio_bytes) < config.CHUNK * 2:
        return "", ""

    with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as f:
        tmp_wav = f.name
    try:
        _pcm_to_wav(audio_bytes, tmp_wav)
        model = _get_whisper()
        # No language hint – let Whisper auto-detect language
        segments, info = model.transcribe(tmp_wav, beam_size=3, vad_filter=True)
        text = " ".join(seg.text.strip() for seg in segments).strip()
        detected = (info.language or "").lower()
        if text:
            log.info("[STT] '%s' (auto-detected lang: %s)", text, detected)
        else:
            log.warning("[STT] No speech detected.")
        return text, detected
    finally:
        try:
            os.unlink(tmp_wav)
        except OSError:
            pass


# ─────────────────────────────────────────────────────────────────────────────
# TEXT-TO-SPEECH  (pyttsx3, fully offline)
# ─────────────────────────────────────────────────────────────────────────────

_tts_lock = threading.Lock()   # pyttsx3 is not thread-safe


def text_to_speech(text: str, language_code: str = None) -> bytes:
    """
    Synthesise text → WAV bytes using pyttsx3 (offline).
    language_code is BCP-47 (e.g. "de-DE"). Defaults to TARGET_LANGUAGE_CODE.
    """
    if language_code is None:
        language_code = config.TARGET_LANGUAGE_CODE

    short_lang = language_code.lower().split("-")[0]   # "de-DE" → "de"

    with _tts_lock:
        engine = pyttsx3.init()
        try:
            _set_pyttsx3_voice(engine, short_lang)
            engine.setProperty("rate", 160)
            engine.setProperty("volume", 1.0)

            with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as f:
                tmp_path = f.name

            engine.save_to_file(text, tmp_path)
            engine.runAndWait()
        finally:
            engine.stop()

    try:
        with open(tmp_path, "rb") as f:
            return f.read()
    finally:
        try:
            os.unlink(tmp_path)
        except OSError:
            pass


def _set_pyttsx3_voice(engine, short_lang: str) -> None:
    """Select the best available voice for the target language."""
    voices = engine.getProperty("voices")
    for voice in voices:
        vid  = (voice.id or "").lower()
        vlang = ""
        if voice.languages:
            raw = voice.languages[0]
            vlang = (raw.decode("utf-8", errors="ignore") if isinstance(raw, bytes)
                     else str(raw)).lower()
        if short_lang in vid or short_lang in vlang:
            engine.setProperty("voice", voice.id)
            log.debug("[TTS] Voice: %s", voice.id)
            return
    log.warning("[TTS] No voice for '%s' – using default.", short_lang)


# ─────────────────────────────────────────────────────────────────────────────
# PLAYBACK
# ─────────────────────────────────────────────────────────────────────────────

def play_audio_bytes(wav_bytes: bytes) -> None:
    """
    Play WAV audio bytes through the local speaker.
    Linux/Pi → aplay with configured ALSA device.
    Windows  → winsound (built-in).
    """
    if not wav_bytes:
        return

    with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as f:
        f.write(wav_bytes)
        tmp_path = f.name

    try:
        if platform.system() == "Windows":
            import winsound
            winsound.PlaySound(tmp_path, winsound.SND_FILENAME)
        else:
            cmd = ["aplay", "--quiet"]
            if config.ALSA_SPK_DEVICE:
                cmd += ["-D", config.ALSA_SPK_DEVICE]
            cmd.append(tmp_path)
            subprocess.run(cmd, check=True)
    except Exception as exc:
        log.error("[AUDIO] Playback error: %s", exc)
    finally:
        try:
            os.unlink(tmp_path)
        except OSError:
            pass


# ──────────────────────────────────────────────────────────────────────────────
# LANGUAGE SETTINGS  (voice configuration + persistence)
# ──────────────────────────────────────────────────────────────────────────────

def load_language_setting() -> str:
    """
    Load the saved language preference from data/settings.json.
    Returns "en" or "de". Falls back to config.HELMET_LANGUAGE if not saved.
    """
    try:
        if os.path.isfile(config.SETTINGS_PATH):
            with open(config.SETTINGS_PATH, "r", encoding="utf-8") as f:
                data = json.load(f)
            lang = data.get("helmet_language", "")
            if lang in ("en", "de"):
                log.info("[LANG] Loaded saved language: '%s'", lang)
                return lang
    except Exception as exc:
        log.warning("[LANG] Could not read settings.json: %s", exc)
    return config.HELMET_LANGUAGE


def save_language_setting(lang: str) -> None:
    """Persist language preference to data/settings.json."""
    os.makedirs(config.DATA_DIR, exist_ok=True)
    try:
        with open(config.SETTINGS_PATH, "w", encoding="utf-8") as f:
            json.dump({"helmet_language": lang}, f)
        log.info("[LANG] Saved language preference: '%s'", lang)
    except Exception as exc:
        log.error("[LANG] Could not save settings.json: %s", exc)


def apply_language_setting(lang: str) -> None:
    """
    Update all config module variables to match the chosen language.
    lang is "en" or "de".
    Call this at startup (after load_language_setting) and after voice config.
    """
    config.HELMET_LANGUAGE = lang
    if lang == "en":
        config.HELMET_LANGUAGE_CODE  = "en-US"
        config.HELMET_LANGUAGE_SHORT = "en"
        config.TARGET_LANGUAGE_CODE  = "de-DE"
        config.TARGET_LANGUAGE_SHORT = "de"
    else:  # "de"
        config.HELMET_LANGUAGE_CODE  = "de-DE"
        config.HELMET_LANGUAGE_SHORT = "de"
        config.TARGET_LANGUAGE_CODE  = "en-US"
        config.TARGET_LANGUAGE_SHORT = "en"
    log.info("[LANG] Applied: HELMET=%s  TARGET=%s",
             config.HELMET_LANGUAGE_CODE, config.TARGET_LANGUAGE_CODE)


def parse_config_command(text: str) -> str:
    """
    Parse a voice language configuration command.
    Returns "en", "de", or "" (not recognised).

    Recognised phrases (case-insensitive):
      EN: "english", "englisch", "in english", "auf englisch"
      DE: "german",  "deutsch",  "in german",  "auf deutsch"
    """
    t = text.lower()
    en_keywords = ("english", "englisch", "in english", "auf englisch")
    de_keywords = ("german",  "deutsch",  "in german",  "auf deutsch")
    for kw in en_keywords:
        if kw in t:
            return "en"
    for kw in de_keywords:
        if kw in t:
            return "de"
    return ""


# ──────────────────────────────────────────────────────────────────────────────
# ALERT BEEP  (pure-Python WAV tone, no external library needed)
# ──────────────────────────────────────────────────────────────────────────────

_beep_wav_cache: bytes = b""   # generated once, reused


def _generate_beep_wav(freq: int = 880, duration: float = 0.15,
                       sample_rate: int = 8000) -> bytes:
    """Generate a single short sine-wave beep as WAV bytes."""
    n_samples = int(sample_rate * duration)
    raw = b"".join(
        struct.pack("<h", int(28000 * math.sin(2 * math.pi * freq * i / sample_rate)))
        for i in range(n_samples)
    )
    buf = io.BytesIO()
    with wave.open(buf, "wb") as wf:
        wf.setnchannels(1)
        wf.setsampwidth(2)
        wf.setframerate(sample_rate)
        wf.writeframes(raw)
    return buf.getvalue()


def play_alert_beep(count: int = 3) -> None:
    """
    Play N short beeps through the speaker to alert the user.
    Beep WAV is generated once and cached for speed.
    """
    global _beep_wav_cache
    if not _beep_wav_cache:
        _beep_wav_cache = _generate_beep_wav()
    for _ in range(count):
        play_audio_bytes(_beep_wav_cache)


def speak(text: str, language_code: str = None) -> None:
    """TTS + immediate local playback in the user's own language."""
    if language_code is None:
        language_code = config.HELMET_LANGUAGE_CODE
    log.info("[SPEAK] '%s'", text)
    wav = text_to_speech(text, language_code=language_code)
    play_audio_bytes(wav)


# ─────────────────────────────────────────────────────────────────────────────
# FULL SEND-SIDE PIPELINE
# ─────────────────────────────────────────────────────────────────────────────

def translation_pipeline(audio_bytes: bytes) -> tuple[str, str, bytes]:
    """
    Full pipeline (no cloud):
      PCM → Whisper STT → OPUS-MT translate → pyttsx3 TTS → WAV bytes

    Returns (original_text, translated_text, wav_bytes).
    Returns ("", "", b"") on failure.
    """
    original = voice_to_text(audio_bytes, language_code=config.HELMET_LANGUAGE_CODE)
    if not original:
        return "", "", b""

    translated = translate_text(original)
    wav_bytes  = text_to_speech(translated, language_code=config.TARGET_LANGUAGE_CODE)
    return original, translated, wav_bytes