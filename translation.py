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
import time
import io
import platform
import struct
import subprocess
import tempfile
import threading
import wave
import logging
import numpy as np
import noisereduce as nr
import speaker_mode

import pyaudio
from deep_translator import GoogleTranslator
from piper import PiperVoice

import config

log = logging.getLogger(__name__)

# ─────────────────────────────────────────────────────────────────────────────
# BILINGUAL SYSTEM PROMPTS
# ─────────────────────────────────────────────────────────────────────────────

_PROMPTS = {
    # General
    "no_workers_connected":  {"en": "No workers connected.",
                              "de": "Keine Arbeiter verbunden."},
    "only_n_workers":        {"en": "Worker {n} is not connected.",
                              "de": "Arbeiter {n} ist nicht verbunden."},
    "talking_to_worker":     {"en": "Talking to worker {index}.",
                              "de": "Verbunden mit Arbeiter {index}."},
    "n_messages_received":   {"en": "{n} message{plural} received. Press P to play.",
                              "de": "{n} Nachricht{plural_de} empfangen. Drücken Sie P zum Abspielen."},
    "worker_connected":      {"en": "Worker {n} connected. Press F{n} to call, or {n} to send a message.",
                              "de": "Arbeiter {n} verbunden. Drücken Sie F{n} zum Anrufen, oder {n} für eine Nachricht."},
    "worker_disconnected": {"en": "Worker {n} disconnected.",
                            "de": "Arbeiter {n} hat die Verbindung getrennt."},
    "smart_helmet_ready":    {"en": "Smart helmet ready.",
                              "de": "Smarter Helm ist bereit."},

    # PTT / Recording
    "recording":             {"en": "Recording.",
                              "de": "Aufnahme läuft."},
    "too_short_hold":        {"en": "Too short. Hold while speaking.",
                              "de": "Zu kurz. Halten Sie die Taste beim Sprechen."},
    "not_understood_retry":  {"en": "Could not understand. Please try again.",
                              "de": "Nicht verstanden. Bitte erneut versuchen."},
    "message_sent":          {"en": "Message sent.",
                              "de": "Nachricht gesendet."},
    "partner_unreachable":   {"en": "Partner not reachable.",
                              "de": "Partner nicht erreichbar."},
    "no_worker_selected":    {"en": "No worker selected. Press 1, 2.",
                              "de": "Kein Arbeiter ausgewählt. Drücken Sie 1, 2."},
    "manager_disconnected": {"en": "Manager disconnected.",
                         "de": "Manager hat die Verbindung getrennt."},
    "no_message_to_replay":  {"en": "No message.",
                              "de": "Keine Nachricht."},
    "replaying_from_sender": {"en": "Replaying. From {sender}: {preview}",
                              "de": "Wiederholung. Von {sender}: {preview}"},

    # Mute
    "muted":                 {"en": "Muted.", "de": "Stummgeschaltet."},
    "unmuted":                {"en": "Unmuted.", "de": "Stummschaltung aufgehoben."},
    "already_in_call":       {"en": "Already in a call.",
                              "de": "Bereits in einem Anruf."},

    # Play message
    "no_messages_lang":      {"en": "No messages. Currently set to {lang_name}.",
                              "de": "Keine Nachrichten. Aktuell eingestellt auf {lang_name}."},
    "from_sender":           {"en": "From {sender}: {preview}",
                              "de": "Von {sender}: {preview}"},
    "n_messages_remaining":  {"en": "{n} message{plural} remaining.",
                              "de": "{n} Nachricht{plural_de} verbleibend."},
    "no_more_messages":      {"en": "No more messages.",
                              "de": "Keine weiteren Nachrichten."},
    
    "emergency_prompt":      {"en": "Emergency. Keep holding to record message.",
                              "de": "Notfall. Halten Sie die Taste für eine Nachricht gedrückt."},
    "emergency_message":     {"en": "Emergency. Please respond immediately.",
                              "de": "Notfall. Bitte sofort antworten."},
    "emergency_from":        {"en": "Emergency alert from {sender}.",
                              "de": "Notfallalarm von {sender}."},

    # Language config
    "lang_setup_prompt":     {"en": "Language setup. Say English or German.",
                              "de": "Spracheinrichtung. Sagen Sie English oder Deutsch."},
    "no_input_cancelled":    {"en": "No input detected. Configuration cancelled.",
                              "de": "Keine Eingabe erkannt. Konfiguration abgebrochen."},
    "lang_not_understood":   {"en": "Could not understand. Please try again.",
                              "de": "Nicht verstanden. Bitte versuchen Sie es erneut.."},
    "configured_en":         {"en": "Configured for English."},
    "configured_de":         {"en": "Konfiguriert für Deutsch."},

    # Calls
    "incoming_call":         {"en": "Incoming call from {partner}.",
                              "de": "Eingehender Anruf von {partner}."},
    "call_connected":        {"en": "Call connected.", "de": "Anruf verbunden."},
    "call_ended":            {"en": "Call ended.", "de": "Anruf beendet."},
    "call_not_answered":     {"en": "Call not answered.", "de": "Anruf nicht beantwortet."},
    "missed_call":           {"en": "Missed call.", "de": "Verpasster Anruf."},
    "calling_partner":       {"en": "Calling {partner}.", "de": "Rufe {partner} an."},
    "call_cancelled":        {"en": "Call cancelled.", "de": "Anruf abgebrochen."},

    # Handover
    "handover_playing":      {"en": "Playing handover note.",
                              "de": "Übergabenachricht wird abgespielt."},
    "handover_prompt":       {"en": "Handover recording",
                              "de": "Aufzeichnung der Übergabenotiz"},
    "handover_too_short":    {"en": "Recording too short. Please try again.",
                              "de": "Aufnahme zu kurz. Bitte versuchen Sie es erneut."},
    "handover_not_understood":{"en": "Could not understand. Please try again.",
                              "de": "Nicht verstanden. Bitte versuchen Sie es erneut."},
    "handover_saved":        {"en": "Handover note saved.",
                              "de": "Übergabenotiz gespeichert."},
    "no_handover_to_replay": {"en": "No handover to play.",
                              "de": "Keine Übergabe zum Abspielen."},

    # Reminders
    "reminder_prompt":       {"en": "Reminder recording",
                              "de": "Erinnerung wird aufgenommen"},
    "reminder_too_short":    {"en": "Recording too short. Please try again.",
                              "de": "Aufnahme zu kurz. Bitte erneut versuchen."},
    "reminder_no_time":      {"en": "No time found in your message. Please try again",
                              "de": "Keine Uhrzeit erkannt. Bitte erneut versuchen."},
    "reminder_saved":        {"en": "Reminder saved for {time}.",
                              "de": "Erinnerung gespeichert für {time}."},
    "reminder_label":        {"en": "Reminder:", "de": "Erinnerung:"},
    
}


def t(prompt_id: str, **kwargs) -> str:
    """
    Look up a system prompt in the CURRENT helmet language and format it
    with any provided kwargs (e.g. t("talking_to_worker", index=2)).
    Falls back to English if the prompt ID or language entry is missing.
    """
    entry = _PROMPTS.get(prompt_id)
    if entry is None:
        log.warning("[PROMPTS] Unknown prompt_id '%s'", prompt_id)
        return prompt_id

    lang = getattr(config, "HELMET_LANGUAGE", "en")
    template = entry.get(lang, entry.get("en", prompt_id))

    if kwargs:
        # Auto-supply English/German plural helpers if "n" was passed
        if "n" in kwargs and "plural" not in kwargs:
            kwargs["plural"] = "s" if kwargs["n"] != 1 else ""
        if "n" in kwargs and "plural_de" not in kwargs:
            kwargs["plural_de"] = "en" if kwargs["n"] != 1 else ""
        try:
            return template.format(**kwargs)
        except KeyError as exc:
            log.warning("[PROMPTS] Missing format key %s for '%s'", exc, prompt_id)
            return template

    return template

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

    log.info("[MIC] Recording started via ALSA arecord on %s (32-bit mode)...", config.ALSA_MIC_DEVICE)
    
    # Create a temporary file to prevent the 64KB pipe buffer from overflowing
    with tempfile.NamedTemporaryFile(suffix=".raw", delete=False) as f:
        tmp_path = f.name
        
    cmd = [
        "arecord",
        "-D", config.ALSA_MIC_DEVICE,
        "-f", "S32_LE",                 # Raw 32-bit frames
        "-r", "48000",                  # Native I2S hardware rate
        "-c", "1",                      # Mono
        "-t", "raw",                    
        "-q",
        tmp_path                        # Write directly to disk
    ]

    process = subprocess.Popen(cmd)
    
    start_time = time.time()
    while is_held_fn() and (time.time() - start_time) < max_seconds:
        time.sleep(0.05)
        
    process.terminate()
    try:
        process.wait(timeout=1)
    except subprocess.TimeoutExpired:
        process.kill()
    
    # Read the audio from the temporary file
    with open(tmp_path, "rb") as f:
        raw_bytes = f.read()
        
    try:
        os.unlink(tmp_path)
    except OSError:
        pass
    
    if not raw_bytes:
        return b""
        
    # --- The SPH0645 Fix ---
    audio_np = np.frombuffer(raw_bytes, dtype=np.int32)
    # Shift right by 16 bits to drop the empty zero-padding
    audio_16 = (audio_np >> 16).astype(np.int16)
    # Downsample from 48000 Hz to 16000 Hz for Whisper
    final_bytes = audio_16[::3].tobytes()
    
    duration = len(final_bytes) / 2 / config.SAMPLE_RATE
    log.info("[MIC] Recording stopped – %.1f s.", duration)
    
    return final_bytes


# ─────────────────────────────────────────────────────────────────────────────
# SPEECH-TO-TEXT  (unchanged)
# ─────────────────────────────────────────────────────────────────────────────

def _denoise_pcm(audio_bytes: bytes) -> bytes:
    """
    Apply spectral noise suppression to raw PCM (int16, mono, 16kHz).
    Reduces steady background noise (machinery, wind, engines) before STT.
    Returns cleaned PCM bytes in the same format.
    """
    try:
        audio_np = np.frombuffer(audio_bytes, dtype=np.int16).astype(np.float32)
        reduced  = nr.reduce_noise(y=audio_np, sr=config.SAMPLE_RATE, stationary=True)
        return reduced.astype(np.int16).tobytes()
    except Exception as exc:
        log.warning("[STT] Noise reduction failed, using raw audio: %s", exc)
        return audio_bytes   # fall back to original if anything goes wrong

def voice_to_text(audio_bytes: bytes, language_code: str = None) -> str:
    """Convert raw PCM → text via local Whisper model."""
    if not audio_bytes or len(audio_bytes) < config.CHUNK * 2:
        return ""
    
    audio_bytes = _denoise_pcm(audio_bytes)

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
    
    audio_bytes = _denoise_pcm(audio_bytes)

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


def _get_piper_sample_rate(voice: PiperVoice) -> int:
    """Get the voice's native sample rate, with a safe fallback."""
    try:
        return voice.config.sample_rate
    except AttributeError:
        pass
    try:
        return voice.config["sample_rate"]
    except (AttributeError, TypeError, KeyError):
        pass
    log.warning("[TTS] Could not detect Piper sample rate — defaulting to 22050 Hz.")
    return 22050


def text_to_speech(text: str, language_code: str = None) -> bytes:
    """Synthesise text → WAV bytes using Piper (in-memory, no disk I/O)."""
    if language_code is None:
        language_code = config.TARGET_LANGUAGE_CODE
    short_lang = language_code.lower().split("-")[0]

    voice = _get_piper_voice(short_lang)
    sample_rate = _get_piper_sample_rate(voice)

    wav_io = io.BytesIO()
    with wave.open(wav_io, "wb") as wav_writer:
        wav_writer.setnchannels(1)
        wav_writer.setsampwidth(2)
        wav_writer.setframerate(sample_rate)

        for chunk in voice.synthesize(text):
            audio_bytes = getattr(chunk, "audio_int16_bytes", None)
            if audio_bytes is None:
                arr = getattr(chunk, "audio_int16_array", None)
                if arr is not None:
                    audio_bytes = arr.tobytes()
            if audio_bytes is None:
                audio_bytes = bytes(chunk)   # last-resort fallback
            wav_writer.writeframes(audio_bytes)

    return wav_io.getvalue()


# ─────────────────────────────────────────────────────────────────────────────
# PLAYBACK  (unchanged)
# ─────────────────────────────────────────────────────────────────────────────

_speaker_lock = threading.Lock()

def play_audio_bytes(wav_bytes: bytes) -> None:
    """Play WAV audio bytes through the local speaker."""
    with _speaker_lock:
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
                device = speaker_mode.get_speaker_device()
                cmd = ["aplay", "--quiet"]
                if device:
                    cmd += ["-D", device]
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