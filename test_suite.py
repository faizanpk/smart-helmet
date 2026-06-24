# test_suite.py – Interactive step-by-step test runner for the Smart Helmet system.
#
# Run with:  python test_suite.py
# Each test prints PASS / FAIL and explains what to check.
# You can run individual tests by passing the test number:
#   python test_suite.py 3      ← run only Test 3

import sys
import os
import time
import threading
import traceback

# ─── Helpers ──────────────────────────────────────────────────────────────────

GREEN  = ""
RED    = ""
YELLOW = ""
CYAN   = ""
RESET  = ""
BOLD   = ""

def header(n, title):
    print(f"\n{BOLD}{CYAN}{'='*60}{RESET}")
    print(f"{BOLD}{CYAN}  Test {n}: {title}{RESET}")
    print(f"{BOLD}{CYAN}{'='*60}{RESET}")

def ok(msg=""):
    print(f"  [PASS]  {msg}")

def fail(msg=""):
    print(f"  [FAIL]  {msg}")

def info(msg):
    print(f"  [INFO]  {msg}")

def ask(prompt):
    return input(f"\n  >> {prompt} ").strip().lower()

def run_test(n, title, fn):
    header(n, title)
    try:
        fn()
    except Exception as e:
        fail(f"Exception: {e}")
        traceback.print_exc()

# ─────────────────────────────────────────────────────────────────────────────
# TEST 1 – Import check (all modules)
# ─────────────────────────────────────────────────────────────────────────────

def test_imports():
    modules = {
        "config":         "config",
        "db":             "db",
        "gpio_handler":   "gpio_handler",
        "network":        "network",
        "peer_network":   "peer_network",
        "message_store":  "message_store",
        "led_handler":    "led_handler",
        "call_manager":   "call_manager",
        "live_call":       "live_call",
        "translation":    "translation",
        "reminders":      "reminders",
        "handover":       "handover",
        # "speaker_mode": "speaker_mode",  # ← remove this comment if you don't have this file
    }
    all_ok = True
    for name, mod in modules.items():
        try:
            __import__(mod)
            ok(f"{name}.py")
        except Exception as e:
            fail(f"{name}.py  ->  {e}")
            all_ok = False
    if all_ok:
        ok("All modules imported successfully.")
    else:
        fail("Fix import errors above before continuing.")

# ─────────────────────────────────────────────────────────────────────────────
# TEST 2 – Database initialisation
# ─────────────────────────────────────────────────────────────────────────────

def test_database():
    import db, config
    db.init_db()
    ok(f"Database created at: {config.DB_PATH}")

    conn = db.get_conn()
    tables = [r[0] for r in conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table'"
    ).fetchall()]
    conn.close()

    for t in ("reminders", "handover"):
        if t in tables:
            ok(f"Table '{t}' exists.")
        else:
            fail(f"Table '{t}' missing!")

    # Confirm the handover migration columns exist (sender_id, played_by)
    conn = db.get_conn()
    cols = {row[1] for row in conn.execute("PRAGMA table_info(handover)").fetchall()}
    conn.close()
    for col in ("sender_id", "played_by"):
        if col in cols:
            ok(f"handover.{col} column present (multi-worker fix applied).")
        else:
            fail(f"handover.{col} column MISSING — multi-worker fix not applied!")

# ─────────────────────────────────────────────────────────────────────────────
# TEST 3 – Text-to-Speech (Piper, neural, offline)
# ─────────────────────────────────────────────────────────────────────────────

def test_tts():
    from translation import text_to_speech, play_audio_bytes

    info("Testing English TTS (Piper)...")
    wav = text_to_speech("Smart helmet test. English voice working.", language_code="en-US")
    if len(wav) > 100:
        ok(f"English TTS produced {len(wav):,} bytes of audio.")
    else:
        fail("English TTS returned empty audio.")
        return

    play_audio_bytes(wav)
    r = ask("Did you hear an English voice? (y/n)")
    ok("English TTS playback confirmed.") if r == "y" else fail("Check speakers / default audio device.")

    info("Testing German TTS (Piper)...")
    wav_de = text_to_speech("Hallo, Helmtest auf Deutsch.", language_code="de-DE")
    if len(wav_de) > 100:
        ok(f"German TTS produced {len(wav_de):,} bytes.")
    else:
        fail("German TTS returned empty audio.")
        return

    play_audio_bytes(wav_de)
    r = ask("Did you hear a German voice? (y/n)")
    if r == "y":
        ok("German TTS playback confirmed.")
    else:
        fail("German voice not heard.")
        info("Confirm de_DE-thorsten-medium.onnx exists in data/piper_voices/")

# ─────────────────────────────────────────────────────────────────────────────
# TEST 4 – Translation (Google Translate via deep-translator, ONLINE)
# ─────────────────────────────────────────────────────────────────────────────

def test_translation():
    info("Testing online translation via Google Translate (deep-translator).")
    info("Requires internet — there is no offline fallback by design.")
    from translation import translate_text

    tests = [
        ("Safety check at gate B",            "en", "de"),
        ("Scaffolding on level 3 is complete", "en", "de"),
        ("Sicherheitskontrolle bei Tor B",     "de", "en"),
    ]
    all_ok = True
    for text, src, tgt in tests:
        try:
            result = translate_text(text, source=src, target=tgt)
            if result and result != text:
                ok(f"[{src}->{tgt}]  '{text}'  ->  '{result}'")
            else:
                fail(f"[{src}->{tgt}]  Translation failed for: '{text}'")
                all_ok = False
        except Exception as e:
            fail(f"[{src}->{tgt}]  Exception (likely no internet): {e}")
            all_ok = False

    if all_ok:
        ok("All translation tests passed.")
    else:
        info("If all failed, check internet connectivity on THIS device specifically.")

# ─────────────────────────────────────────────────────────────────────────────
# TEST 5 – Speech-to-Text (Whisper, offline, requires microphone)
# ─────────────────────────────────────────────────────────────────────────────

def test_stt():
    info("This test records 4 seconds from your default microphone.")
    info("First run loads Whisper model (size set by config.WHISPER_MODEL_SIZE).")
    r = ask("Do you have a microphone ready? (y/n)")
    if r != "y":
        info("Skipping STT test. You can run it later with:  python test_suite.py 5")
        return

    import pyaudio, config
    from translation import voice_to_text

    info("Speak NOW – recording for 4 seconds…")
    p = pyaudio.PyAudio()
    stream = p.open(format=pyaudio.paInt16, channels=1,
                    rate=config.SAMPLE_RATE, input=True,
                    frames_per_buffer=config.CHUNK)
    frames = []
    for _ in range(int(config.SAMPLE_RATE / config.CHUNK * 4)):
        frames.append(stream.read(config.CHUNK, exception_on_overflow=False))
    stream.stop_stream(); stream.close(); p.terminate()

    audio_bytes = b"".join(frames)
    ok(f"Recorded {len(audio_bytes):,} bytes of audio.")

    info("Transcribing with Whisper…")
    text = voice_to_text(audio_bytes, language_code=config.HELMET_LANGUAGE_CODE)
    if text:
        ok(f"Whisper recognised: '{text}'")
        r2 = ask("Is that roughly what you said? (y/n)")
        ok("STT test passed.") if r2 == "y" else info("Try speaking more slowly, or move closer to the mic.")
    else:
        fail("Whisper returned empty text. Check mic is working and not muted.")

# ─────────────────────────────────────────────────────────────────────────────
# TEST 6 – Full pipeline (STT → translate → TTS → play)
# ─────────────────────────────────────────────────────────────────────────────

def test_full_pipeline():
    info("Full pipeline: you speak -> auto-detect language -> translate -> hear result.")
    info("(Whisper STT -> Google Translate -> Piper TTS)")
    r = ask("Microphone ready? (y/n)")
    if r != "y":
        info("Skipping. Run with:  python test_suite.py 6")
        return

    import pyaudio, config
    from translation import transcribe_only, translate_text, text_to_speech, play_audio_bytes

    input("  Press ENTER to START recording…")
    p = pyaudio.PyAudio()
    stream = p.open(format=pyaudio.paInt16, channels=1,
                    rate=config.SAMPLE_RATE, input=True,
                    frames_per_buffer=config.CHUNK)
    frames = []
    stop_event = threading.Event()

    def record():
        while not stop_event.is_set():
            frames.append(stream.read(config.CHUNK, exception_on_overflow=False))
    t = threading.Thread(target=record, daemon=True)
    t.start()

    input("  Press ENTER to STOP recording…")
    stop_event.set(); t.join(timeout=1)
    stream.stop_stream(); stream.close(); p.terminate()

    audio_bytes = b"".join(frames)
    ok(f"Captured {len(audio_bytes):,} bytes.")

    info("Running STT (Whisper)…")
    original, detected_lang = transcribe_only(audio_bytes)
    if not original:
        fail("No speech detected. Speak louder or check mic.")
        return
    ok(f"You said      : '{original}'  (detected lang: {detected_lang})")

    target = "de" if detected_lang == "en" else "en"
    info(f"Translating {detected_lang} -> {target} (Google Translate)…")
    try:
        translated = translate_text(original, source=detected_lang, target=target)
    except Exception as e:
        fail(f"Translation failed (check internet): {e}")
        return
    ok(f"Translation   : '{translated}'")

    info("Synthesising with Piper TTS…")
    target_code = "de-DE" if target == "de" else "en-US"
    wav = text_to_speech(translated, language_code=target_code)
    ok(f"Audio size    : {len(wav):,} bytes")

    info("Playing translated audio…")
    play_audio_bytes(wav)
    r2 = ask("Did you hear the translation? (y/n)")
    ok("Pipeline test passed!") if r2 == "y" else fail("Playback issue. Check audio output device.")

# ─────────────────────────────────────────────────────────────────────────────
# TEST 7 – Reminder: save, parse time, check DB, simulate trigger
# ─────────────────────────────────────────────────────────────────────────────

def test_reminders():
    import db
    from reminders import parse_trigger_time, save_reminder, _check_and_play_reminders
    from datetime import datetime

    cases = [
        ("Check scaffolding at 14:30",    "14:30"),
        ("Remind me at 2 pm to inspect",  "14:00"),
        ("Safety check at noon",           "12:00"),
        ("Besprechung um 14 Uhr 30",      "14:30"),
        ("Morning inspection",             "09:00"),
        ("Erinnerung fürs Abendessen um 19 Uhr", "19:00"),  # regression check for the substring-match bug
    ]
    all_ok = True
    for text, expected in cases:
        got = parse_trigger_time(text)
        if got == expected:
            ok(f"parse '{text}' -> '{got}'")
        else:
            fail(f"parse '{text}' -> got '{got}', expected '{expected}'")
            all_ok = False

    if not all_ok:
        fail("Time parser has issues.")
        return

    now = datetime.now()
    trigger = f"{now.hour:02d}:{(now.minute + 1) % 60:02d}"
    save_reminder("This is a test reminder from the test suite.", trigger)
    ok(f"Reminder saved with trigger_time='{trigger}'")

    conn = db.get_conn()
    row = conn.execute(
        "SELECT * FROM reminders WHERE trigger_time=? AND done=0", (trigger,)
    ).fetchone()
    conn.close()
    if row:
        ok(f"DB row confirmed: id={row['id']}, message='{row['message'][:40]}…'")
    else:
        fail("Reminder not found in DB.")
        return

    info("Simulating an immediate trigger…")
    now_str = datetime.now().strftime("%H:%M")
    save_reminder("Immediate test reminder – you should hear this now.", now_str)
    _check_and_play_reminders()

    r = ask("Did you hear 'Immediate test reminder'? (y/n)")
    ok("Reminder test passed!") if r == "y" else fail("TTS playback not heard.")

# ─────────────────────────────────────────────────────────────────────────────
# TEST 8 – Handover: save, playback, and multi-worker independent-read fix
# ─────────────────────────────────────────────────────────────────────────────

def test_handover():
    import db, config
    from handover import _get_unplayed_handover, play_handover
    from datetime import datetime

    original_role = config.HELMET_ROLE
    original_id   = config.HELMET_ID

    info("Part A: basic save + playback (worker -> manager).")
    config.HELMET_ROLE, config.HELMET_ID = "worker", "w-01"

    conn = db.get_conn()
    conn.execute(
        """INSERT INTO handover
           (sender_name, sender_id, sender_role, zone, message, timestamp, played_by)
           VALUES (?, ?, ?, ?, ?, ?, ?)""",
        (None, "w-01", "worker", "Zone B",
         "Scaffolding on level 3 needs safety inspection before morning shift.",
         datetime.now().strftime("%Y-%m-%d %H:%M"), "")
    )
    conn.commit()
    conn.close()
    ok("Handover message inserted as worker 'w-01'.")

    config.HELMET_ROLE, config.HELMET_ID = "manager", "manager"
    entry = _get_unplayed_handover()
    if entry:
        ok(f"Manager found unplayed handover: '{entry['message'][:50]}…'")
    else:
        fail("Manager could not find worker's handover message.")
        config.HELMET_ROLE, config.HELMET_ID = original_role, original_id
        return

    play_handover(entry)
    r = ask("Did you hear the handover message? (y/n)")
    ok("Basic handover playback passed!") if r == "y" else fail("TTS playback not heard.")

    conn = db.get_conn()
    row = conn.execute("SELECT played_by FROM handover WHERE id=?", (entry["id"],)).fetchone()
    conn.close()
    played_list = (row["played_by"] or "").split(",") if row else []
    if "manager" in played_list:
        ok(f"Marked played by 'manager' (played_by='{row['played_by']}').")
    else:
        fail("Handover not correctly marked as played for manager.")

    info("\nPart B: multi-worker independent-read fix (manager broadcasts, BOTH workers read it).")
    config.HELMET_ROLE, config.HELMET_ID = "manager", "manager"
    conn = db.get_conn()
    conn.execute(
        """INSERT INTO handover
           (sender_name, sender_id, sender_role, zone, message, timestamp, played_by)
           VALUES (?, ?, ?, ?, ?, ?, ?)""",
        (None, "manager", "manager", None,
         "Crane moves to zone D at 3pm, all teams clear the area.",
         datetime.now().strftime("%Y-%m-%d %H:%M"), "")
    )
    conn.commit()
    conn.close()
    ok("Manager broadcast handover inserted.")

    config.HELMET_ROLE, config.HELMET_ID = "worker", "w-01"
    entry_w01 = _get_unplayed_handover()
    if entry_w01:
        play_handover(entry_w01)
        ok("Worker w-01 read the manager's broadcast.")
    else:
        fail("Worker w-01 could not find the manager's broadcast.")
        config.HELMET_ROLE, config.HELMET_ID = original_role, original_id
        return

    config.HELMET_ROLE, config.HELMET_ID = "worker", "w-02"
    entry_w02 = _get_unplayed_handover()
    if entry_w02 and entry_w02["id"] == entry_w01["id"]:
        ok("Worker w-02 ALSO found the same broadcast — multi-reader fix confirmed!")
        r3 = ask("Play it for w-02 as well, to confirm audio works for the second reader too? (y/n)")
        if r3 == "y":
            play_handover(entry_w02)
            r4 = ask("Did you hear it? (y/n)")
            ok("Multi-worker handover test fully passed!") if r4 == "y" else fail("Playback issue for w-02.")
    else:
        fail("Worker w-02 could NOT find the broadcast — multi-reader fix may have regressed!")

    config.HELMET_ROLE, config.HELMET_ID = original_role, original_id

# ─────────────────────────────────────────────────────────────────────────────
# TEST 9 – Network: manager server + worker client handshake + voice message
# ─────────────────────────────────────────────────────────────────────────────

def test_network():
    import network, config

    TEST_PORT = 15005   # different port to avoid clashing with main system
    received = []
    done = threading.Event()

    def on_manager_message(msg_type, meta, payload):
        received.append((msg_type, meta, payload))
        done.set()

    original_role = config.HELMET_ROLE
    original_id   = config.HELMET_ID

    info("Starting local TCP server (as manager) on port 15005…")
    network.start_server(TEST_PORT, on_manager_message)
    time.sleep(0.5)
    ok("Server listening.")

    info("Connecting as worker client to 127.0.0.1…")
    config.HELMET_ID = "w-01"
    network.connect_to_server("127.0.0.1", TEST_PORT, lambda t, m, p: None)

    deadline = time.time() + 5
    while "w-01" not in network.get_worker_ids() and time.time() < deadline:
        time.sleep(0.1)

    if "w-01" in network.get_worker_ids():
        ok("Worker handshake (MSG_HELLO) completed — registered as 'w-01'.")
    else:
        fail("Worker did not register within 5 seconds. Check firewall.")
        config.HELMET_ID = original_id
        return

    info("Sending a voice message from worker to manager…")
    sent = network.send_voice_message("Test message from suite", "en")
    if not sent:
        fail("send_voice_message returned False.")
        config.HELMET_ID = original_id
        return
    ok("Message sent.")

    if done.wait(timeout=5):
        msg_type, meta, payload = received[0]
        if msg_type == network.MSG_VOICE_MESSAGE and meta.get("text") == "Test message from suite":
            ok(f"Manager received correctly: '{meta.get('text')}' from '{meta.get('sender_id')}'")
            ok("Network test passed!")
        else:
            fail(f"Received unexpected data: type={msg_type}, meta={meta}")
    else:
        fail("Timed out waiting for message. No data received in 5 s.")

    config.HELMET_ROLE, config.HELMET_ID = original_role, original_id

# ─────────────────────────────────────────────────────────────────────────────
# TEST 10 – GPIO keyboard fallback (manager/worker laptop simulation)
# ─────────────────────────────────────────────────────────────────────────────

def test_gpio():
    import gpio_handler as gpio
    import config

    ok(f"IS_PI        = {gpio.IS_PI}   (should be False on laptop)")
    ok(f"HAS_KEYBOARD = {gpio.HAS_KEYBOARD}")

    if not gpio.HAS_KEYBOARD:
        info("Install keyboard lib:  python -m pip install keyboard")
        info("Then run as Administrator and rerun this test.")
        return

    info("Press and HOLD the SPACE key for 2 seconds, then release…")
    gpio.wait_for_press(config.BTN_SPEAK_MANAGER)   # SPACE key
    ok("Button press detected!")

    held_for = gpio.wait_for_release(config.BTN_SPEAK_MANAGER, max_seconds=5)
    ok(f"Button released after {held_for:.2f} s.")

    if held_for >= 1.0:
        ok("Hold-to-talk behaviour confirmed.")
    else:
        info("Held for less than 1 s – try holding longer for real use.")

# ─────────────────────────────────────────────────────────────────────────────
# TEST 11 – Live Call: UDP audio loopback (no partner needed)
# ─────────────────────────────────────────────────────────────────────────────

def test_live_call():
    import socket, struct, config
    from live_call import LiveCall, _FRAMES_PER_PACKET, _PACKET_BYTES

    info("Tests Live Call UDP audio streaming using a loopback on localhost.")
    info("No partner Pi needed — we send a UDP packet to ourselves.")

    original_role = config.HELMET_ROLE
    config.HELMET_ROLE = "manager"

    call = LiveCall()
    call.start("127.0.0.1", port_offset=0)
    time.sleep(0.3)   # let sender/receiver/player threads spin up
    ok(f"LiveCall started: send_port={call._send_port}, recv_port={call._recv_port}")

    sock_send = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    fake_pcm = b"\x01\x02" * (_PACKET_BYTES // 2)
    header   = struct.pack(">I", 42)
    sock_send.sendto(header + fake_pcm, ("127.0.0.1", call._recv_port))
    ok(f"Sent {len(header + fake_pcm)} byte UDP packet to port {call._recv_port}.")

    time.sleep(0.5)
    info("If your speakers are on, you may have just heard a brief audio blip.")

    call.mute(True)
    ok(f"call._muted = {call._muted}  (should be True)")
    call.mute(False)
    ok(f"call._muted = {call._muted}  (should be False)")
    ok("Mute toggle confirmed.")

    call.stop()
    ok("Call stopped cleanly.")
    sock_send.close()
    config.HELMET_ROLE = original_role

# ─────────────────────────────────────────────────────────────────────────────
# MAIN RUNNER
# ─────────────────────────────────────────────────────────────────────────────

TESTS = [
    (1,  "Module imports",                 test_imports),
    (2,  "Database initialisation",        test_database),
    (3,  "Text-to-Speech (Piper)",         test_tts),
    (4,  "Translation (Google, online)",   test_translation),
    (5,  "Speech-to-Text (Whisper)",       test_stt),
    (6,  "Full pipeline (STT->TR->TTS)",   test_full_pipeline),
    (7,  "Reminders (save + playback)",    test_reminders),
    (8,  "Handover (+ multi-worker fix)",  test_handover),
    (9,  "Network (handshake + message)", test_network),
    (10, "GPIO / keyboard fallback",       test_gpio),
    (11, "Live Call (UDP loopback)",       test_live_call),
]

if __name__ == "__main__":
    os.system("")  # enable ANSI colours on Windows

    print(f"\n{BOLD}Smart Helmet – Test Suite{RESET}")
    print("Run all tests in order, or pass a number to run one:")
    print("  python test_suite.py        <- all tests")
    print("  python test_suite.py 4      <- only Test 4")
    print()
    print("Recommended order for first-time setup:")
    print("  1 -> 2 -> 3 -> 4 -> 5 -> 6 -> 7 -> 8 -> 9 -> 10 -> 11\n")

    if len(sys.argv) > 1:
        try:
            n = int(sys.argv[1])
            match = [(num, title, fn) for num, title, fn in TESTS if num == n]
            if not match:
                print(f"No test number {n}. Valid: 1–{len(TESTS)}")
                sys.exit(1)
            run_test(*match[0])
        except ValueError:
            print(f"Usage: python test_suite.py [1-{len(TESTS)}]")
            sys.exit(1)
    else:
        for num, title, fn in TESTS:
            run_test(num, title, fn)
            r = ask("Continue to next test? (y/n)")
            if r != "y":
                print("\nStopped by user.")
                break

    print(f"\n{BOLD}Done.{RESET}\n")