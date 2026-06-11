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
        "config":        "config",
        "db":            "db",
        "gpio_handler":  "gpio_handler",
        "network":       "network",
        "translation":   "translation",
        "reminders":     "reminders",
        "handover":      "handover",
        "speaker_mode":  "speaker_mode",
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

# ─────────────────────────────────────────────────────────────────────────────
# TEST 3 – Text-to-Speech (pyttsx3, no mic needed)
# ─────────────────────────────────────────────────────────────────────────────

def test_tts():
    from translation import text_to_speech, play_audio_bytes
    import config

    info("Testing English TTS…")
    wav = text_to_speech("Smart helmet test. English voice working.", language_code="en-US")
    if len(wav) > 100:
        ok(f"English TTS produced {len(wav):,} bytes of audio.")
    else:
        fail("English TTS returned empty audio.")
        return

    play_audio_bytes(wav)
    r = ask("Did you hear an English voice? (y/n)")
    if r == "y":
        ok("English TTS playback confirmed.")
    else:
        fail("English TTS playback not heard. Check speakers / default audio device.")

    info("Testing German TTS…")
    wav_de = text_to_speech("Hallo, Helmtest auf Deutsch.", language_code="de-DE")
    if len(wav_de) > 100:
        ok(f"German TTS produced {len(wav_de):,} bytes.")
    else:
        fail("German TTS returned empty audio.")
        return

    play_audio_bytes(wav_de)
    r = ask("Did you hear a German (or any) voice? (y/n)")
    if r == "y":
        ok("German TTS playback confirmed.")
    else:
        info("No German SAPI voice installed on this Windows machine.")
        info("Install one: Settings -> Time & Language -> Speech -> Add voices -> Deutsch")
        info("Or leave it – the English voice will speak German text on the laptop.")
        info("On the Raspberry Pi, espeak-ng includes a German voice automatically.")

# ─────────────────────────────────────────────────────────────────────────────
# TEST 4 – Offline translation (ctranslate2 + OPUS-MT)
# ─────────────────────────────────────────────────────────────────────────────

def test_translation():
    info("First run will download OPUS-MT models (~100 MB each). This may take a minute.")
    info("Subsequent runs are instant (models cached locally).")
    from translation import setup_offline_models, translate_text

    setup_offline_models()
    ok("Offline models ready.")

    tests = [
        ("Safety check at gate B",            "en", "de"),
        ("Scaffolding on level 3 is complete", "en", "de"),
        ("Sicherheitskontrolle bei Tor B",     "de", "en"),
    ]
    all_ok = True
    for text, src, tgt in tests:
        result = translate_text(text, source=src, target=tgt)
        if result and result != text:
            ok(f"[{src}->{tgt}]  '{text}'  ->  '{result}'")
        else:
            fail(f"[{src}->{tgt}]  Translation failed for: '{text}'")
            all_ok = False

    if all_ok:
        ok("All translation tests passed.")

# ─────────────────────────────────────────────────────────────────────────────
# TEST 5 – Speech-to-Text (Whisper, requires microphone)
# ─────────────────────────────────────────────────────────────────────────────

def test_stt():
    info("This test records 4 seconds from your default microphone.")
    info("First run will download Whisper 'tiny' model (~75 MB).")
    r = ask("Do you have a microphone ready? (y/n)")
    if r != "y":
        info("Skipping STT test. You can run it later with:  python test_suite.py 5")
        return

    import pyaudio, wave, tempfile, config
    from translation import voice_to_text
    from faster_whisper import WhisperModel

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
        r2 = ask(f"Is that roughly what you said? (y/n)")
        if r2 == "y":
            ok("STT test passed.")
        else:
            info("Try speaking more slowly and clearly, or move closer to the mic.")
    else:
        fail("Whisper returned empty text. Check mic is working and not muted.")

# ─────────────────────────────────────────────────────────────────────────────
# TEST 6 – Full translation pipeline (STT → translate → TTS → play)
# ─────────────────────────────────────────────────────────────────────────────

def test_full_pipeline():
    info("Full pipeline: you speak English -> hear German translation.")
    r = ask("Microphone ready? (y/n)")
    if r != "y":
        info("Skipping. Run with:  python test_suite.py 6")
        return

    import pyaudio, config
    from translation import translation_pipeline, play_audio_bytes

    info("Hold ENTER, speak your message, then press ENTER again to stop.")
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

    info("Running pipeline: STT -> translate -> TTS…")
    original, translated, wav = translation_pipeline(audio_bytes)

    if not original:
        fail("No speech detected. Speak louder or check mic.")
        return

    ok(f"You said      : '{original}'")
    ok(f"Translation   : '{translated}'")
    ok(f"Audio size    : {len(wav):,} bytes")

    info("Playing translated audio…")
    play_audio_bytes(wav)
    r2 = ask("Did you hear the German translation? (y/n)")
    ok("Pipeline test passed!") if r2 == "y" else fail("Playback issue. Check audio output device.")

# ─────────────────────────────────────────────────────────────────────────────
# TEST 7 – Reminder: save, parse time, check DB, simulate trigger
# ─────────────────────────────────────────────────────────────────────────────

def test_reminders():
    import db, config
    from reminders import parse_trigger_time, save_reminder, _check_and_play_reminders
    from datetime import datetime

    # Time parsing
    cases = [
        ("Check scaffolding at 14:30",    "14:30"),
        ("Remind me at 2 pm to inspect",  "14:00"),
        ("Safety check at noon",           "12:00"),
        ("Besprechung um 14 Uhr 30",      "14:30"),
        ("Morning inspection",             "09:00"),
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

    # Save a reminder with a trigger time 1 minute from now
    now = datetime.now()
    trigger = f"{now.hour:02d}:{(now.minute + 1) % 60:02d}"
    save_reminder("This is a test reminder from the test suite.", trigger)
    ok(f"Reminder saved with trigger_time='{trigger}'")

    # Verify in DB
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

    info(f"Reminder will auto-play at {trigger}.")
    info("Simulating trigger now (forcing _check_and_play_reminders with current time)…")

    # Temporarily insert a reminder for the exact current time
    now_str = datetime.now().strftime("%H:%M")
    save_reminder("Immediate test reminder – you should hear this now.", now_str)
    _check_and_play_reminders()

    r = ask("Did you hear 'Immediate test reminder'? (y/n)")
    ok("Reminder test passed!") if r == "y" else fail("TTS playback not heard.")

# ─────────────────────────────────────────────────────────────────────────────
# TEST 8 – Handover: record message, play it back
# ─────────────────────────────────────────────────────────────────────────────

def test_handover():
    import db, config
    from translation import text_to_speech, play_audio_bytes
    from handover import _get_unplayed_handover, play_handover
    from datetime import datetime

    info("Inserting a fake handover message directly into the DB (no mic needed).")

    # Simulate worker saving a handover
    original_role = config.HELMET_ROLE
    config.HELMET_ROLE = "worker"

    conn = db.get_conn()
    conn.execute(
        """INSERT INTO handover (sender_name, sender_role, zone, message, timestamp, played)
           VALUES (?, ?, ?, ?, ?, 0)""",
        ("Test Worker", "worker", "Zone B",
         "Scaffolding on level 3 needs safety inspection before morning shift.",
         datetime.now().strftime("%Y-%m-%d %H:%M"))
    )
    conn.commit()
    conn.close()
    ok("Handover message inserted into DB as 'worker'.")

    # Now simulate manager reading it
    config.HELMET_ROLE = "manager"
    entry = _get_unplayed_handover()
    if entry:
        ok(f"Found unplayed handover: '{entry['message'][:50]}…'")
    else:
        fail("Manager could not find worker's handover message.")
        config.HELMET_ROLE = original_role
        return

    info("Playing handover via TTS…")
    play_handover(entry)

    r = ask("Did you hear the handover message? (y/n)")
    ok("Handover test passed!") if r == "y" else fail("TTS playback not heard.")

    # Verify it's marked as played
    conn = db.get_conn()
    row = conn.execute("SELECT played FROM handover WHERE id=?", (entry["id"],)).fetchone()
    conn.close()
    if row and row["played"] == 1:
        ok("Handover marked as played in DB.")
    else:
        fail("Handover not marked as played.")

    config.HELMET_ROLE = original_role

# ─────────────────────────────────────────────────────────────────────────────
# TEST 9 – Network: send audio between two local sockets (same machine)
# ─────────────────────────────────────────────────────────────────────────────

def test_network():
    import network

    TEST_PORT = 15005   # use a different port to avoid conflicts with main system
    received = []
    ready    = threading.Event()
    done     = threading.Event()

    def on_message(msg_type, meta, payload):
        received.append((msg_type, payload))
        done.set()

    # Monkey-patch port for this test
    info("Starting local TCP server on port 15005…")

    server_sock_backup = network._server_sock
    active_backup      = network._active_sock

    # Reset module state for clean test
    network._server_sock = None
    network._active_sock = None

    network.start_server(TEST_PORT, on_message)
    time.sleep(0.5)   # give server thread time to bind
    ok("Server listening.")

    info("Connecting client (same machine, 127.0.0.1)…")
    network.connect_to_server("127.0.0.1", TEST_PORT, lambda t, m, p: None)

    # Wait for connection
    deadline = time.time() + 5
    while not network.is_connected() and time.time() < deadline:
        time.sleep(0.1)

    if network.is_connected():
        ok("Client connected to server.")
    else:
        fail("Client could not connect. Check firewall / antivirus.")
        return

    # Send test payload
    test_payload = b"HELLO_HELMET_TEST_AUDIO_12345"
    info(f"Sending {len(test_payload)} bytes as MSG_TRANSLATION…")
    sent = network.send_audio(test_payload, meta={"test": True})
    if sent:
        ok("Payload sent.")
    else:
        fail("send_audio returned False.")
        return

    # Wait for receipt
    if done.wait(timeout=5):
        msg_type, payload = received[0]
        if msg_type == "translation" and payload == test_payload:
            ok(f"Received correctly: type='{msg_type}', payload={payload}")
            ok("Network test passed!")
        else:
            fail(f"Received wrong data: type={msg_type}, payload={payload}")
    else:
        fail("Timed out waiting for message. No data received in 5 s.")

# ─────────────────────────────────────────────────────────────────────────────
# TEST 10 – GPIO keyboard fallback (manager laptop simulation)
# ─────────────────────────────────────────────────────────────────────────────

def test_gpio():
    import gpio_handler as gpio

    ok(f"IS_PI       = {gpio.IS_PI}   (should be False on laptop)")
    ok(f"HAS_KEYBOARD = {gpio.HAS_KEYBOARD}")

    if not gpio.HAS_KEYBOARD:
        info("Install keyboard lib:  python -m pip install keyboard")
        info("Then run as Administrator and rerun this test.")
        return

    info("Press and HOLD the SPACE key for 2 seconds, then release…")
    gpio.wait_for_press(17)   # BTN_SPEAK = 17 -> SPACE key
    ok("Button press detected!")

    import time
    held_for = gpio.wait_for_release(17, max_seconds=5)
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
    info("No partner Pi needed - we send a UDP packet to ourselves.")

    # 1. Verify LiveCall object construction
    config.HELMET_ROLE = "manager"
    call = LiveCall("127.0.0.1")
    ok(f"LiveCall created: send_port={call._send_port}, recv_port={call._recv_port}")

    # 2. Send a raw UDP packet and receive it back
    recv_port = call._send_port   # manager sends on 5006, so we listen on 5006
    sock_recv = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    sock_recv.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    sock_recv.bind(("0.0.0.0", recv_port))
    sock_recv.settimeout(3.0)

    sock_send = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    fake_pcm = b"\x01\x02" * (_PACKET_BYTES // 2)
    header   = struct.pack(">I", 42)
    sock_send.sendto(header + fake_pcm, ("127.0.0.1", recv_port))
    ok(f"Sent {len(header + fake_pcm)} byte UDP packet to port {recv_port}.")

    try:
        data, _ = sock_recv.recvfrom(4 + _PACKET_BYTES + 64)
        seq = struct.unpack(">I", data[:4])[0]
        pcm = data[4:]
        if seq == 42 and pcm == fake_pcm:
            ok(f"Received packet correctly: seq={seq}, payload_len={len(pcm)}.")
            ok("Live Call UDP loopback test passed!")
        else:
            fail(f"Data mismatch: seq={seq} (expected 42), pcm_match={pcm==fake_pcm}")
    except socket.timeout:
        fail("UDP receive timed out. Check if port 5006 is blocked by firewall.")
    finally:
        sock_recv.close()
        sock_send.close()

    # 3. Verify mute flag
    call.mute(True)
    ok(f"call._muted = {call._muted}  (should be True)")
    call.mute(False)
    ok(f"call._muted = {call._muted}  (should be False)")
    ok("Mute toggle confirmed.")


# ─────────────────────────────────────────────────────────────────────────────
# MAIN RUNNER
# ─────────────────────────────────────────────────────────────────────────────

TESTS = [
    (1,  "Module imports",               test_imports),
    (2,  "Database initialisation",      test_database),
    (3,  "Text-to-Speech (pyttsx3)",     test_tts),
    (4,  "Offline translation (OPUS-MT)", test_translation),
    (5,  "Speech-to-Text (Whisper)",     test_stt),
    (6,  "Full pipeline (STT->TR->TTS)", test_full_pipeline),
    (7,  "Reminders (save + playback)",  test_reminders),
    (8,  "Handover (save + playback)",   test_handover),
    (9,  "Network (TCP send/receive)",   test_network),
    (10, "GPIO / keyboard fallback",     test_gpio),
    (11, "Live Call (UDP loopback)",     test_live_call),
]

if __name__ == "__main__":
    # Enable ANSI colours on Windows
    os.system("")

    print(f"\n{BOLD}Smart Helmet – Test Suite{RESET}")
    print("Run all tests in order, or pass a number to run one:")
    print("  python test_suite.py        <- all tests")
    print("  python test_suite.py 4      <- only Test 4")
    print()
    print("Recommended order for first-time setup:")
    print("  1 -> 2 -> 3 -> 4 -> 5 -> 6 -> 7 -> 8 -> 9 -> 10\n")

    if len(sys.argv) > 1:
        # Single test mode
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
        # Run all
        for num, title, fn in TESTS:
            run_test(num, title, fn)
            r = ask("Continue to next test? (y/n)")
            if r != "y":
                print("\nStopped by user.")
                break

    print(f"\n{BOLD}Done.{RESET}\n")
