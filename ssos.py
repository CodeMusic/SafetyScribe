#!/usr/bin/env python3
# SafetyScribe OS — resilient PTT recorder/uploader with LEDs + SFX
import os, sys, time, json, subprocess, socket, threading, tempfile, signal, base64, re, wave, struct, math
from datetime import datetime
from pathlib import Path
import argparse
import requests

# ---------- Args / Config ----------
ap = argparse.ArgumentParser(description="SafetyScribe OS")
ap.add_argument("--no-sfx", action="store_true", help="disable synthetic sounds")
args = ap.parse_args()

AUDIO_DEV      = os.environ.get("SS_AUDIO_DEV", "plughw:0,0")
SAMPLE_RATE    = int(os.environ.get("SS_RATE", "48000"))   # WM8960 likes 48k
CHANNELS       = int(os.environ.get("SS_CH",   "2"))       # dual mics → stereo
SAMPLE_FMT     = "S16_LE"
RECS_DIR       = Path(os.path.expanduser("~/recs"))
ENDPOINT       = os.environ.get("SS_ENDPOINT", "https://n8n.codemusic.ca/webhook/safetyscribe")
BUTTON_GPIO    = 17
DOUBLE_TAP_MS  = 400
LED_BRIGHTNESS = float(os.environ.get("SS_LED_BRIGHTNESS", "0.25"))
WIFI_TEST_HOST = os.environ.get("SS_NET_HOST", "n8n.codemusic.ca")
WIFI_TEST_PORT = int(os.environ.get("SS_NET_PORT", "443"))
DEBUG          = os.environ.get("DEBUG", "0") == "1"
LOG_PATH       = Path(os.path.expanduser("~/safetyscribeos/ssos.log"))
SFX_ENABLED    = (os.environ.get("SS_SFX", "1") == "1") and (not args.no_sfx)

RECS_DIR.mkdir(parents=True, exist_ok=True)
LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
TMPDIR = Path("/dev/shm" if Path("/dev/shm").exists() else tempfile.gettempdir())

# ---------- Logging ----------
def log(msg, **kv):
    ts = datetime.utcnow().strftime("%Y-%m-%dT%H:%M:%SZ")
    line = {"ts": ts, "msg": str(msg), **kv}
    text = json.dumps(line, ensure_ascii=False)
    print(text, flush=True)
    try:
        with LOG_PATH.open("a", buffering=1) as f:
            f.write(text + "\n")
    except Exception:
        pass

# ---------- LEDs ----------
import gpiod
import board
import adafruit_dotstar as dotstar

dots = dotstar.DotStar(board.SCK, board.MOSI, 2, brightness=LED_BRIGHTNESS, auto_write=True)

def led(c0, c1=None):  # (r,g,b) for each LED
    if c1 is None: c1 = c0
    dots[0] = c0; dots[1] = c1

def leds_off():
    dots[0] = (0,0,0); dots[1] = (0,0,0)

_anim_stop = threading.Event()
_anim_lock = threading.Lock()
_anim_thread = None

def _animate(func):
    global _anim_thread
    with _anim_lock:
        _stop_animation()
        _anim_stop.clear()
        _anim_thread = threading.Thread(target=func, daemon=True)
        _anim_thread.start()

def _stop_animation():
    global _anim_thread
    _anim_stop.set()
    if _anim_thread and _anim_thread.is_alive():
        try: _anim_thread.join(timeout=0.2)
        except Exception: pass
    leds_off()

def anim_waiting_orange():
    def run():
        t = 0.0
        while not _anim_stop.is_set():
            b = 0.12 + 0.12*(0.5 + 0.5*math.sin(t))
            dots.brightness = b
            led((255,165,0))
            time.sleep(0.05)
            t += 0.18
        dots.brightness = LED_BRIGHTNESS
    _animate(run)

def hsv(h, s=1.0, v=1.0):
    import colorsys
    r,g,b = colorsys.hsv_to_rgb(h, s, v)
    return (int(r*255), int(g*255), int(b*255))

def anim_record_rainbow():
    def run():
        hue = 0
        while not _anim_stop.is_set():
            c = hsv((hue%360)/360.0, 1, 1)
            led(c); hue += 7
            time.sleep(0.03)
    _animate(run)

def anim_talking():
    # out-of-phase twin meters vibe
    def run():
        t = 0.0
        while not _anim_stop.is_set():
            a = 0.5 + 0.5*math.sin(t)
            b = 0.5 + 0.5*math.sin(t + math.pi)
            c0 = (int(40+215*a), int(40+215*b), 255)   # cool cyan/blue swings
            c1 = (int(40+215*b), int(40+215*a), 255)
            led(c0, c1)
            time.sleep(0.035); t += 0.32
    _animate(run)

def anim_error_strobe():
    def run():
        while not _anim_stop.is_set():
            led((255,0,30), (255,0,30)); time.sleep(0.08)
            leds_off(); time.sleep(0.08)
    _animate(run)

def flash_ok():
    led((0,255,60)); time.sleep(0.25); leds_off()

# ---------- Button ----------
chip = gpiod.Chip("gpiochip0")
btn  = chip.get_line(BUTTON_GPIO)
btn.request(consumer="safety-scribe", type=gpiod.LINE_REQ_DIR_IN,
            flags=gpiod.LINE_REQ_FLAG_BIAS_PULL_UP)

def button_pressed():  # active-low
    return btn.get_value() == 0

# ---------- Mixer auto-config (WM8960 sane defaults) ----------
def run_sh(cmd):
    try:
        out = subprocess.run(cmd, shell=True, capture_output=True, text=True, check=False)
        if DEBUG: log("sh", cmd=cmd, rc=out.returncode, stdout=out.stdout.strip(), stderr=out.stderr.strip())
        return out.returncode
    except Exception as e:
        log("sh_error", cmd=cmd, err=str(e))
        return 1

def configure_audio_mixer():
    log("mixer_config_start")
    cmds = [
        "amixer -c 0 sset 'Left Output Mixer PCM' on",
        "amixer -c 0 sset 'Right Output Mixer PCM' on",
        "amixer -c 0 sset 'Headphone' 90% unmute 2>/dev/null || true",
        "amixer -c 0 sset 'Speaker'   90% unmute 2>/dev/null || true",
        # Mic path (LINPUT1/RINPUT1) with boost + input mixer boost
        "amixer -c 0 sset 'Left Input Boost Mixer LINPUT1' 2",
        "amixer -c 0 sset 'Right Input Boost Mixer RINPUT1' 2",
        "amixer -c 0 sset 'Left Input Mixer Boost' on",
        "amixer -c 0 sset 'Right Input Mixer Boost' on",
        # Capture gains
        "amixer -c 0 sset 'Capture' 47,47",
        "amixer -c 0 sset 'ADC PCM' 195,195",
        "amixer -c 0 sset 'ADC High Pass Filter' on",
        # ALC (gentle leveling)
        "amixer -c 0 sset 'ALC Function' Stereo",
        "amixer -c 0 sset 'ALC Target' 4",
        "amixer -c 0 sset 'ALC Max Gain' 7",
        "amixer -c 0 sset 'ALC Min Gain' 0",
        "amixer -c 0 sset 'ALC Attack' 2",
        "amixer -c 0 sset 'ALC Decay' 3",
        "amixer -c 0 sset 'ALC Hold Time' 0",
        "sudo alsactl store 2>/dev/null || true",
    ]
    for c in cmds: run_sh(c)
    log("mixer_config_done")

# ---------- SFX (synthetic sounds) ----------
def synth_to_wav(frames, path, sr=SAMPLE_RATE):
    with wave.open(str(path), "wb") as w:
        w.setnchannels(2)
        w.setsampwidth(2)   # 16-bit
        w.setframerate(sr)
        w.writeframes(frames)

def tone_frames(freqL, freqR=None, dur=0.120, vol=0.35, sr=SAMPLE_RATE):
    if freqR is None: freqR = freqL
    n = int(dur * sr)
    frames = []
    for i in range(n):
        t = i / sr
        sL = int(vol * 32767 * math.sin(2*math.pi*freqL*t))
        sR = int(vol * 32767 * math.sin(2*math.pi*freqR*t))
        frames.append(struct.pack("<hh", sL, sR))
    return b"".join(frames)

def env_fade(frames, sr=SAMPLE_RATE, ms=8):
    # apply simple attack/release to avoid clicks
    if not frames: return frames
    step = int(sr * ms / 1000)
    samples = len(frames)//4
    if samples <= 2*step: return frames
    out = bytearray(frames)
    # attack
    for i in range(step):
        f = i/step
        for ch in (0,1):
            base = (i*4)+ch*2
            val = struct.unpack_from("<h", out, base)[0]
            struct.pack_into("<h", out, base, int(val*f))
    # release
    for i in range(step):
        f = 1 - (i/step)
        idx = (samples-1 - i)
        for ch in (0,1):
            base = (idx*4)+ch*2
            val = struct.unpack_from("<h", out, base)[0]
            struct.pack_into("<h", out, base, int(val*f))
    return bytes(out)

def play_frames_seq(seq):
    if not SFX_ENABLED: return
    tmp = TMPDIR / f"sfx_{int(time.time()*1000)}.wav"
    frames = b""
    for (fL, fR, d, v) in seq:
        frames += env_fade(tone_frames(fL, fR, d, v))
        frames += env_fade(tone_frames(0,0,0.010,0.0))  # tiny spacer
    synth_to_wav(frames, tmp)
    subprocess.run(["aplay", "-D", AUDIO_DEV, str(tmp)],
                   stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, check=False)
    try: tmp.unlink()
    except Exception: pass

def sfx_startup_jingle():
    # simple Star-Trek-ish triad sweep
    seq = [
        (740, 550, 0.12, 0.35),
        (880, 660, 0.14, 0.38),
        (988, 740, 0.18, 0.40),
        (1175, 880, 0.22, 0.42),
    ]
    play_frames_seq(seq)

def sfx_outro_jingle():
    seq = [
        (988, 740, 0.14, 0.35),
        (880, 660, 0.12, 0.33),
        (740, 550, 0.10, 0.30),
    ]
    play_frames_seq(seq)

def sfx_activate():
    # two-tone chirp
    seq = [(1400, 1600, 0.090, 0.38), (1900, 2100, 0.080, 0.40)]
    play_frames_seq(seq)

def sfx_release():
    seq = [(1100, 900, 0.070, 0.33), (800, 700, 0.060, 0.30)]
    play_frames_seq(seq)

def sfx_response():
    seq = [(1600, 1700, 0.060, 0.35), (2000, 1500, 0.080, 0.35), (1700,1700,0.050,0.30)]
    play_frames_seq(seq)

def sfx_from_pattern(pat):
    """
    Optional payload: {"sound_pattern":[{"fL":freq,"fR":freq2,"d":sec,"v":0..1}, ...]}
    Fields also accepted as "f","dur","vol" (mono → both).
    """
    seq = []
    for x in pat:
        fL = float(x.get("fL", x.get("f", 1000)))
        fR = float(x.get("fR", fL))
        d  = float(x.get("d",  x.get("dur", 0.08)))
        v  = float(x.get("v",  x.get("vol", 0.35)))
        seq.append((fL, fR, d, v))
    play_frames_seq(seq)

# ---------- Network gate ----------
def wait_for_wifi():
    anim_waiting_orange()
    while True:
        try:
            with socket.create_connection((WIFI_TEST_HOST, WIFI_TEST_PORT), timeout=2.5):
                break
        except Exception as e:
            if DEBUG: log("wifi_wait_connect_fail", err=str(e))
            time.sleep(1.0)
    _stop_animation()
    led((0,255,0)); log("network_ready", host=WIFI_TEST_HOST, port=WIFI_TEST_PORT)

# ---------- Recording ----------
_rec_proc = None
_current_wav = None

def start_recording():
    global _rec_proc, _current_wav
    ts = datetime.utcnow().strftime("%Y%m%dT%H%M%SZ")
    _current_wav = str(RECS_DIR / f"rec_{ts}.wav")
    cmd = ["arecord", "-D", AUDIO_DEV, "-f", SAMPLE_FMT, "-c", str(CHANNELS), "-r", str(SAMPLE_RATE), _current_wav]
    log("arecord_start", cmd=cmd, path=_current_wav)
    try:
        _rec_proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    except FileNotFoundError:
        log("arecord_not_found")
        anim_error_strobe(); time.sleep(0.8); _stop_animation()
        return
    anim_record_rainbow()
    sfx_activate()

def stop_recording():
    global _rec_proc
    if not _rec_proc: return None
    try:
        _rec_proc.terminate()
        try:
            stdout, stderr = _rec_proc.communicate(timeout=3.0)
        except subprocess.TimeoutExpired:
            _rec_proc.kill()
            stdout, stderr = _rec_proc.communicate(timeout=2.0)
    finally:
        rc = _rec_proc.returncode
        _rec_proc = None
        _stop_animation()
        flash_ok()
        sfx_release()
        log("arecord_stop", rc=rc,
            stderr=(stderr.decode(errors="ignore") if 'stderr' in locals() else None))
    return _current_wav

# ---------- Playback helpers ----------
def play_wav_file(path):
    anim_talking()
    subprocess.run(["aplay", "-D", AUDIO_DEV, str(path)],
                   stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, check=False)
    _stop_animation()

def play_audio_from_url(url):
    log("playback_fetch_start", url=url)
    try:
        with requests.get(url, stream=True, timeout=60) as r:
            r.raise_for_status()
            tmp = TMPDIR / f"dl_{int(time.time()*1000)}.wav"
            with open(tmp, "wb") as f:
                for ch in r.iter_content(8192):
                    if ch: f.write(ch)
        log("playback_local", path=str(tmp))
        play_wav_file(tmp)
        tmp.unlink(missing_ok=True)
        log("playback_done")
    except Exception as e:
        log("playback_error", err=str(e))
        anim_error_strobe(); time.sleep(0.8); _stop_animation()

def play_audio_from_base64(data_url_or_b64, filename_hint="resp.wav"):
    # accept raw base64 or data:audio/wav;base64,....
    m = re.match(r"^data:audio/[^;]+;base64,(.+)$", data_url_or_b64)
    b64 = m.group(1) if m else data_url_or_b64
    try:
        raw = base64.b64decode(b64, validate=True)
        tmp = TMPDIR / f"rx_{int(time.time()*1000)}_{filename_hint}"
        with open(tmp, "wb") as f: f.write(raw)
        log("playback_local", path=str(tmp))
        play_wav_file(tmp)
        tmp.unlink(missing_ok=True)
        log("playback_done")
    except Exception as e:
        log("playback_b64_error", err=str(e))
        anim_error_strobe(); time.sleep(0.8); _stop_animation()

# ---------- Upload + server actions ----------
def run_led_pattern(p):
    p = (p or "").strip().lower()
    if p in ("rainbow","record_rainbow","recording"):
        anim_record_rainbow(); time.sleep(1.2); _stop_animation()
    elif p in ("pulse","breathe","orange","wait","waiting"):
        anim_waiting_orange(); time.sleep(1.2); _stop_animation()
    elif p in ("green","ok","ready"):
        led((0,255,0)); time.sleep(0.5)
    elif p in ("red","error","warn"):
        anim_error_strobe(); time.sleep(0.8); _stop_animation()
    elif p in ("white","neutral"):
        led((255,255,255)); time.sleep(0.5)
    elif p in ("off","none"):
        leds_off()
    else:
        led((0,180,255)); time.sleep(0.5)  # cyan blip

def upload_and_act(wav_path):
    led((255,165,0))  # amber during upload
    try:
        size = os.path.getsize(wav_path)
    except Exception:
        size = None

    try:
        with open(wav_path, "rb") as f:
            files = {"audio": (os.path.basename(wav_path), f, "audio/wav")}
            meta  = {"filename": os.path.basename(wav_path), "device": "SafetyScribe-PiZeroW"}
            r = requests.post(ENDPOINT, files=files, data=meta, timeout=90)
            status = r.status_code
            r.raise_for_status()
            resp = r.json()
            log("upload_ok", status=status, bytes=size)
    except Exception as e:
        log("upload_fail", err=str(e), bytes=size)
        anim_error_strobe(); time.sleep(0.8); _stop_animation()
        led((0,255,0))  # recover to ready
        return

    # Parse response
    pattern = (resp.get("led") or resp.get("led_pattern") or resp.get("pattern") or "")
    audio_url = resp.get("audio_url")
    audio_field = resp.get("audio")
    sound_pat = resp.get("sound_pattern") or resp.get("sound")

    log("server_response",
        have_audio=bool(audio_url or audio_field),
        have_sound=bool(sound_pat),
        pattern=pattern)

    if sound_pat:
        try: sfx_from_pattern(sound_pat)
        except Exception as e: log("sound_pattern_error", err=str(e))

    if audio_url:
        sfx_response()
        play_audio_from_url(audio_url)
    elif isinstance(audio_field, str) and (audio_field.startswith("http://") or audio_field.startswith("https://")):
        sfx_response()
        play_audio_from_url(audio_field)
    elif isinstance(audio_field, str):
        sfx_response()
        play_audio_from_base64(audio_field, "payload.wav")

    if pattern:
        run_led_pattern(pattern)

    led((0,255,0))  # back to ready
    log("ready")

# ---------- Main loop (PTT + double-tap) ----------
def main():
    log("startup", audio_dev=AUDIO_DEV, rate=SAMPLE_RATE, ch=CHANNELS, fmt=SAMPLE_FMT, debug=DEBUG, sfx=SFX_ENABLED)
    configure_audio_mixer()
    if SFX_ENABLED: sfx_startup_jingle()
    wait_for_wifi()
    led((0,255,0))

    last_press_ms = 0
    recording = False
    ptt_mode_active = False
    now_ms = lambda: int(time.time()*1000)

    while True:
        try:
            pressed = button_pressed()

            # Push-to-talk
            if pressed and not ptt_mode_active and not recording:
                time.sleep(0.03)  # debounce
                if button_pressed():
                    ptt_mode_active = True
                    log("ptt_start")
                    start_recording()

            if ptt_mode_active and not pressed:
                log("ptt_release")
                wav = stop_recording()
                ptt_mode_active = False
                if wav and os.path.exists(wav): upload_and_act(wav)
                else: log("no_wav_generated", warn=True)

            # Double-tap toggle record
            if not ptt_mode_active:
                if pressed:
                    t = now_ms()
                    if t - last_press_ms <= DOUBLE_TAP_MS:
                        if not recording:
                            log("doubletap_record_start")
                            start_recording(); recording = True
                        else:
                            log("doubletap_record_stop")
                            wav = stop_recording(); recording = False
                            if wav and os.path.exists(wav): upload_and_act(wav)
                            else: log("no_wav_generated", warn=True)
                        last_press_ms = 0
                        time.sleep(0.2)
                    else:
                        last_press_ms = t
                        time.sleep(0.2)
                else:
                    time.sleep(0.03)

        except Exception as e:
            # Never die: signal via LEDs, then recover
            log("loop_error", err=str(e))
            anim_error_strobe(); time.sleep(0.8); _stop_animation()
            led((0,255,0))

def _cleanup(*_):
    try:
        _stop_animation()
        leds_off()
        if SFX_ENABLED: sfx_outro_jingle()
    finally:
        log("shutdown")
        sys.exit(0)

if __name__ == "__main__":
    signal.signal(signal.SIGINT,  _cleanup)
    signal.signal(signal.SIGTERM, _cleanup)
    main()