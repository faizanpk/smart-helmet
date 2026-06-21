# translation.py – Speech-to-text (offline Whisper) + Translation (Google,
# online) + Text-to-speech (offline Piper).
#
# Libraries used:
#   faster-whisper   → Speech-to-Text  (OpenAI Whisper, CPU, int8 quantised, offline)
#   deep-translator  → Translation     (free Google Translate, ONLINE — no offline fallback)
#   piper-tts        → Text-to-Speech  (neural, offline, much better than espeak/SAPI)
#
# Note: translation requires internet at the moment of use. If site WiFi is
# down, translate_text() will raise — there is no offline fallback by design.

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
from deep_translator import GoogleTranslator
from piper import PiperVoice

import config

log = logging.getLogger(__name__)


# ─────────────────────────────────────────────────────────────────────────────
# ONE-TIME MODEL SETUP
# ─────────────────────────────────────────────────────────────────────────────

def setup_offline_models() -> None:
    """
    Pre-warm offline models at startup so the first real message isn't delayed.
    Whisper (STT) and Piper (TTS) are both offline — only translation is online.
    """
    _get_whisper()
    _get_piper_voice("en")
    _get_piper_voice("de")
    log.info("[MODELS] Whisper + Piper voices ready.")


# ─────────────────────────────────────────────────────────────────────────────
# TRANSLATION  (Google, via deep-translator)
# ─────────────────────────────────────────────────────────────────────────────

def translate_text(text: str, source: str = None, target: str = None) -> str:
    """
    Translate text using free Google Translate (deep-translator).
    source/target are short ISO-639-1 codes ("en", "de").
    Defaults: source=HELMET_LANGUAGE_SHORT, target=TARGET_LANGUAGE_SHORT.
    """
    if not text:
        return ""
    if source is None:
        source = config.HELMET_LANGUAGE_SHORT
    if target is None:
        target = config.TARGET_LANGUAGE_SHORT

    translated = GoogleTranslator(source=source, target=target).translate(text)
    log.info("[TRANSLATE] '%s' → '%s'", text, translated)
    return translated


# ─────────────────────────────────────────────────────────────────────────────
# FASTER-WHISPER – Speech-to-Text  (unchanged, fully offline)
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
                compute_type="int8",
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
# RECORDING  (unchanged)
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
    """Capture raw LINEAR16 PCM while is_held_fn() returns True."""
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
# SPEECH-TO-TEXT  (unchanged)
# ─────────────────────────────────────────────────────────────────────────────

def voice_to_text(audio_bytes: bytes, language_code: str = None) -> str:
    """Convert raw PCM → text via local Whisper model."""
    if not audio_bytes or len(audio_bytes) < config.CHUNK * 2:
        return ""

    whisper_lang = None
    if language_code:
        whisper_lang = language_code.split("-")[0].lower()

    with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as f:
        tmp_wav = f.name
    try:
        _pcm_to_wav(audio_bytes, tmp_wav)
        model = _get_whisper()
        segments, info = model.transcribe(
            tmp_wav, language=whisper_lang, beam_size=3, vad_filter=True,
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
    """Run Whisper STT only (no translation). Returns (text, detected_language)."""
    if not audio_bytes or len(audio_bytes) < config.CHUNK * 2:
        return "", ""

    with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as f:
        tmp_wav = f.name
    try:
        _pcm_to_wav(audio_bytes, tmp_wav)
        model = _get_whisper()
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
# TEXT-TO-SPEECH  (Piper — offline, neural, replaces pyttsx3)
# ─────────────────────────────────────────────────────────────────────────────

_piper_voices: dict[str, PiperVoice] = {}
_piper_lock = threading.Lock()


def _get_piper_voice(short_lang: str) -> PiperVoice:
    """Lazy-load a Piper voice model (thread-safe singleton per language)."""
    with _piper_lock:
        if short_lang not in _piper_voices:
            model_path = (config.PIPER_VOICE_EN if short_lang == "en"
                          else config.PIPER_VOICE_DE)
            if not os.path.isfile(model_path):
                raise FileNotFoundError(
                    f"Piper voice model not found at {model_path}. "
                    f"Run: python -m piper.download_voices "
                    f"{'en_US-lessac-medium' if short_lang == 'en' else 'de_DE-thorsten-medium'} "
                    f"--data-dir {config.PIPER_VOICES_DIR}"
                )
            log.info("[TTS] Loading Piper voice for '%s'...", short_lang)
            _piper_voices[short_lang] = PiperVoice.load(model_path)
    return _piper_voices[short_lang]


def text_to_speech(text: str, language_code: str = None) -> bytes:
    """Synthesise text → WAV bytes using Piper (in-memory, no disk I/O)."""
    if language_code is None:
        language_code = config.TARGET_LANGUAGE_CODE
    short_lang = language_code.lower().split("-")[0]

    voice = _get_piper_voice(short_lang)

    # Create a virtual file in RAM
    wav_io = io.BytesIO()
    
    # Piper writes the complete WAV file (headers and all) into RAM
    voice.synthesize(text, wav_io)
    
    # Return the bytes directly
    return wav_io.getvalue()


# ─────────────────────────────────────────────────────────────────────────────
# PLAYBACK  (unchanged)
# ─────────────────────────────────────────────────────────────────────────────

def play_audio_bytes(wav_bytes: bytes) -> None:
    """Play WAV audio bytes through the local speaker."""
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


def speak(text: str, language_code: str = None) -> None:
    """TTS + immediate local playback in the user's own language."""
    if language_code is None:
        language_code = config.HELMET_LANGUAGE_CODE
    log.info("[SPEAK] '%s'", text)
    wav = text_to_speech(text, language_code=language_code)
    play_audio_bytes(wav)


# ─────────────────────────────────────────────────────────────────────────────
# LANGUAGE SETTINGS  (unchanged)
# ─────────────────────────────────────────────────────────────────────────────

def load_language_setting() -> str:
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
    os.makedirs(config.DATA_DIR, exist_ok=True)
    try:
        with open(config.SETTINGS_PATH, "w", encoding="utf-8") as f:
            json.dump({"helmet_language": lang}, f)
        log.info("[LANG] Saved language preference: '%s'", lang)
    except Exception as exc:
        log.error("[LANG] Could not save settings.json: %s", exc)


def apply_language_setting(lang: str) -> None:
    config.HELMET_LANGUAGE = lang
    if lang == "en":
        config.HELMET_LANGUAGE_CODE  = "en-US"
        config.HELMET_LANGUAGE_SHORT = "en"
        config.TARGET_LANGUAGE_CODE  = "de-DE"
        config.TARGET_LANGUAGE_SHORT = "de"
    else:
        config.HELMET_LANGUAGE_CODE  = "de-DE"
        config.HELMET_LANGUAGE_SHORT = "de"
        config.TARGET_LANGUAGE_CODE  = "en-US"
        config.TARGET_LANGUAGE_SHORT = "en"
    log.info("[LANG] Applied: HELMET=%s  TARGET=%s",
             config.HELMET_LANGUAGE_CODE, config.TARGET_LANGUAGE_CODE)


def parse_config_command(text: str) -> str:
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


# ─────────────────────────────────────────────────────────────────────────────
# ALERT BEEP  (unchanged)
# ─────────────────────────────────────────────────────────────────────────────

_beep_wav_cache: bytes = b""


def _generate_beep_wav(freq: int = 880, duration: float = 0.15,
                       sample_rate: int = 8000) -> bytes:
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
    global _beep_wav_cache
    if not _beep_wav_cache:
        _beep_wav_cache = _generate_beep_wav()
    for _ in range(count):
        play_audio_bytes(_beep_wav_cache)