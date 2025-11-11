# SafetyScribe

Push-to-talk voice link for Raspberry Pi that records, uploads to **n8n**, plays spoken replies, and gives rich LED/audio feedback.  
The first shipped use-case is **Psychological Protection Mode**—a compassionate capture/respond loop that helps you name feelings, spot distortions, and anchor boundaries—while the platform stays general-purpose for assistants and automations.

![SafetyScribe banner](https://raw.githubusercontent.com/CodeMusic/SafetyScribe/main/safetyscribe.png)

---

## ✨ Features

- Hold-to-talk **PTT** and **double-tap** to toggle hands-free recording (ALSA `arecord`, WAV @ 48 kHz stereo). 
- Uploads the WAV to an **n8n Webhook** as `multipart/form-data`.
- Reads JSON response; if it includes `audio_url` (or `audio`) the file is fetched and **played** with `aplay`.
- **DotStar LED** animations (two LEDs) for state:
  - orange breathe = connecting
  - green solid = ready
  - rainbow spin = recording
  - white chatter = playback
  - red pulse = error
  - cyan blip = unknown instruction
- **Auto-recovery**: errors are signaled (LED/tones) and the loop keeps trying.
- **Structured logging** to file + stdout.
- **Optional synth tones** (on by default): startup jingle, PTT engage/release chirps, response chime, graceful shutdown.

---

## 🧘 Psychological Protection Mode (v1)

A reference n8n flow can:
- Transcribe the audio,
- Run a gentle cognitive check (feelings, needs, boundaries, cognitive distortions),
- Return a short validating reflection + next-step prompt,
- Optionally return TTS audio and an LED pattern.

*This is not therapy; it’s a self-support mirror. Escalate to professionals when needed.*

---

## 🧩 Architecture

    [Button GPIO17]  ──>  [ssos.py] ── arecord ─┐
    [DotStar x2]     <──  state/anim            │  multipart/form-data
    [WM8960 HAT]     ──>  ALSA (48k stereo)  ──┼────────>  [n8n Webhook]
                                               │            │
                                               │        route/transcribe/reflect
                                               │            │
                                               └──── <──────┴── JSON:
                                                    { "audio_url"| "audio", "led", "sound"? }
                                                            │
                                              aplay   <─────┘

---

## 🔧 Hardware

- Raspberry Pi (Zero/Zero W/3/4/5).  
- Audio HAT: **WM8960** (stereo mics, HP/speaker out).  
- **2× DotStar (APA102)** on SPI (SCK/MOSI).  
- Momentary button on **GPIO17** (active-low).

---

## 📦 Software prerequisites

    sudo apt-get update
    sudo apt-get install -y python3 python3-pip python3-dev \
      python3-libgpiod gpiod alsa-utils

    # add user to device groups, then re-login
    sudo usermod -aG audio,gpio,spi $USER

    # Python libs
    pip3 install --user adafruit-blinka adafruit-circuitpython-dotstar requests

*Enable SPI via raspi-config → Interface Options → SPI → Enable.*

---

## 🎚️ ALSA setup (good defaults for WM8960)

    # Input routing & analog boost
    amixer -c 0 sset 'Left Input Boost Mixer LINPUT1' 2
    amixer -c 0 sset 'Right Input Boost Mixer RINPUT1' 2
    amixer -c 0 sset 'Left Input Mixer Boost' on
    amixer -c 0 sset 'Right Input Mixer Boost' on

    # ADC / capture (balanced; avoids harsh digital gain)
    amixer -c 0 sset 'Capture' 47,47
    amixer -c 0 sset 'ADC PCM' 195,195
    amixer -c 0 sset 'ADC High Pass Filter' on

    # Optional ALC (soft touch)
    amixer -c 0 sset 'ALC Function' Stereo
    amixer -c 0 sset 'ALC Target' 4
    amixer -c 0 sset 'ALC Max Gain' 7
    amixer -c 0 sset 'ALC Min Gain' 0
    amixer -c 0 sset 'ALC Attack' 2
    amixer -c 0 sset 'ALC Decay' 3
    amixer -c 0 sset 'ALC Hold Time' 0

    # Persist
    sudo alsactl store

Sanity checks:

    # Playback
    aplay -D plughw:0,0 /usr/share/sounds/alsa/Front_Center.wav

    # Record & play back (3s, stereo @ 48k)
    arecord -D plughw:0,0 -f S16_LE -r 48000 -c 2 -d 3 ~/recs/mic_test.wav
    aplay   -D plughw:0,0 ~/recs/mic_test.wav

---

## 🚀 Run

Place the script at:

    ~/safetyscribeos/ssos.py

Run:

    cd ~/safetyscribeos
    DEBUG=1 python3 ssos.py

You should see JSON logs like:

    {"ts":"...","msg":"startup","audio_dev":"plughw:0,0","rate":48000,"ch":2,"fmt":"S16_LE","debug":true}
    {"ts":"...","msg":"network_ready","host":"n8n.codemusic.ca","port":443}

**Controls**

- **Hold** button ⇒ record; **release** ⇒ upload.  
- **Double-tap** ⇒ toggle record on/off (hands-free).

---

## 🌐 n8n workflow

**Webhook node**

- Method: POST  
- Path: `safetyscribe` (or your choice)  
- *Field Name for Binary Data*: **audio** (matches client)  
- Respond: *When Last Node Finishes* → *First Entry JSON*

**Binary key gotcha** (n8n versions differ): the incoming binary may be keyed as `audio`, `audio0`, or `audio00`. Use this expression to reference the first binary key:

    {{ Object.keys($binary)[0] }}

Example: **Read/Write Files from Disk** → *Input Binary Field*:

    {{ Object.keys($binary)[0] }}

File path example:

    /data/safetyscribe/{{ $binary[Object.keys($binary)[0]].fileName || ("recording_" + $now + ".wav") }}

**Minimal flow**

1) Webhook (receives WAV)  
2) (Optional) Write File to Disk  
3) Transcribe (Whisper or service)  
4) Reflect / label (LLM)  
5) TTS (generate reply)  
6) **Respond to Webhook** with JSON:

    {
      "led": "white",
      "audio_url": "https://example.com/reply.wav"
    }

**Fields understood by the client**

- `audio_url` **or** `audio`  
- `led` / `led_pattern` / `pattern`  
- *(future)* `sound` pattern object

---

## ⚙️ Configuration (env)

| Var              | Default                                          | Meaning                                   |
|------------------|--------------------------------------------------|-------------------------------------------|
| \`SS_AUDIO_DEV\` | \`plughw:0,0\`                                   | ALSA device for record/playback           |
| \`SS_RATE\`      | \`48000\`                                        | Sample rate                               |
| \`SS_CH\`        | \`2\`                                            | Channels (2 = stereo)                     |
| \`SS_ENDPOINT\`  | \`https://n8n.codemusic.ca/webhook/safetyscribe\`| Webhook URL                               |
| \`DEBUG\`        | \`0\`                                            | \`1\` → verbose logs                       |
| \`SS_TONES\`     | \`1\`                                            | Enable synth tones (0 = off)              |

---

## 🖍️ LED patterns

| Pattern        | Meaning                     |
|----------------|-----------------------------|
| orange breathe | connecting / waiting        |
| green solid    | ready / idle                |
| rainbow spin   | recording                   |
| white chatter  | playback (speaking)         |
| red pulse      | error (retry soon)          |
| cyan blip      | unknown server instruction  |

---

## 🔊 Synth tones (optional)

Enabled by \`SS_TONES=1\`. Short, 2–3-tone cues:

- Startup jingle (on ready)  
- PTT engage + release chirps  
- Response received chime  
- Graceful shutdown outro

---

## ♻️ Reliability & recovery

- Network check gate before ready (orange → green).  
- Upload failures: red pulse, log line, loop continues.  
- Playback failures: brief red blink sequence, continue loop.  
- Any crash is logged; SIGINT/SIGTERM trigger cleanup (LEDs off, outro tone if enabled).

---

## 🗒️ Logging

- Structured JSON lines to \`~/safetyscribeos/ssos.log\` and stdout.  
- Examples: \`startup\`, \`network_ready\`, \`ptt_start\`, \`arecord_start\`, \`upload_ok\`, \`server_response\`, \`playback_error\`, \`ready\`.

---

## 🧰 Run on boot (systemd)

Create:

    /etc/systemd/system/safetyscribe.service

Contents:

    [Unit]
    Description=SafetyScribe Voice Link
    After=network-online.target
    Wants=network-online.target

    [Service]
    User=pi
    Environment=SS_ENDPOINT=https://n8n.codemusic.ca/webhook/safetyscribe
    Environment=SS_TONES=1
    WorkingDirectory=/home/pi/safetyscribeos
    ExecStart=/usr/bin/python3 /home/pi/safetyscribeos/ssos.py
    Restart=always
    RestartSec=2

    [Install]
    WantedBy=multi-user.target

Enable:

    sudo systemctl daemon-reload
    sudo systemctl enable --now safetyscribe

---

## 🔐 Security & privacy

- Audio is sent only to your configured webhook.  
- Prefer HTTPS endpoints you control.  
- If storing files, apply encryption and access controls.  
- Logs may include filenames/URLs—treat as sensitive.

---

## 🛠️ Troubleshooting

- No audio in recordings: check \`arecord\` test and ALSA mixer levels above.  
- n8n shows “no binary field”: use \`{{ Object.keys($binary)[0] }}\`.  
- Quiet mic or ticks: reduce \`ADC PCM\`, keep analog boost moderate, enable High-Pass Filter.  
- LEDs stuck orange: DNS/host reachability for \`WIFI_TEST_HOST\`.

---

## 🗺️ Roadmap

- Double Tap aforementioned functionality 
- Per-LED patterns (stereo color cues).  
- Server-driven \`sound\` pattern objects (procedural beeps/phrases).  
- Local wake-word (optional, post-MVP).  
- Caching & offline queue.  
- Additional modalities (status display, haptics).


---