# SafetyScribe

*A one-button, push-to-talk witness & wellbeing companion for Raspberry Pi.*  
It records difficult interactions, uploads securely to **n8n**, plays helpful replies, and gives clear LED/audio feedback.

The first flagship mode is **Dignity & Accountability**: when anyone is spoken to in a demeaning or abusive way (e.g., condescension, baseless claims like \`"you’re forgetting"\`), SafetyScribe lets you **toggle recording**, then automatically produces a **timestamped email report** with transcript highlights so the organization can take corrective action and prevent future harm.  
**Inspired by a real pharmacy encounter** — built to give everyday people **assistive superpowers** and **peace of mind** for themselves and their families.

![SafetyScribe banner](https://raw.githubusercontent.com/CodeMusic/SafetyScribe/main/safetyscribe.png)

---

## ✨ Core Features

- **Hold-to-Talk (PTT)** and **Double-Tap** for hands-free toggle  
  (ALSA \`arecord\`, WAV @ 48 kHz **stereo** with WM8960).
- **Uploads** to an **n8n Webhook** as multipart/form-data.
- Reads JSON; if the response contains \`audio_url\` (or \`audio\`) it **plays** it via \`aplay\`.
- **Two DotStar LEDs** for state:
  - orange breathe = connecting  
  - green solid    = ready  
  - rainbow spin   = recording  
  - white chatter  = playback  
  - red pulse      = error  
  - cyan blip      = unknown instruction
- **Auto-recovery**: signals errors (LED/tones) and keeps trying; never gets stuck.
- **Structured JSON logs** to stdout and a local log file.
- **Optional synth tones** (on by default): startup jingle, engage/release chirps, response chime, graceful shutdown.

---

## 🛡️ Dignity & Accountability (Flagship Mode)

When toggled during a difficult interaction, SafetyScribe will:

1. **Capture audio** (with clear LED indicator and optional chime).  
2. **Upload** to your **n8n** flow.  
3. **Transcribe** and **analyze** for patterns of disrespect:
   - condescension (slow/over-explained phrasing),
   - baseless memory accusations (\`"you’re forgetting"\` when untrue),
   - interruptions, dominance ratio (who talks most), sentiment shifts,
   - key quotes with **timestamps**.  
4. **Generate an email report** with a respectful, factual tone:
   - date/time, location, participants (if known),
   - short incident summary,
   - objective metrics (interruptions, dominance, negative language markers),
   - **timestamped quotes** (with quick links to audio moments),
   - attachments or links (full audio and trimmed clip),
   - clear requested action (coaching, review, follow-up).  
5. Optionally **send the report** to the organization’s official contacts and CC a trusted advocate.

> This mode aims to **restore dignity through accountability**. It documents behavior so teams can coach, improve, and prevent recurrence.

---

## 🧩 Architecture Overview

    [Button GPIO17]  ──>  [ssos.py] ── arecord ─┐
    [DotStar x2]     <──  state/anim            │   multipart/form-data
    [WM8960 HAT]     ──>  ALSA (48k stereo)  ──┼────────>  [n8n Webhook]
                                               │
                                               ├─ Transcribe (Whisper, etc.)
                                               ├─ Analyze (respect metrics)
                                               ├─ Generate report email
                                               └─ Respond JSON { audio_url?, led? }
    
    aplay + LED animation  <───────────────────────────────────────────────────────

---

## 🔧 Hardware

- Raspberry Pi (Zero/Zero W/3/4/5)  
- **WM8960** audio HAT (dual mics + headphone/speaker out)  
- **2× DotStar (APA102)** on SPI (SCK/MOSI)  
- Momentary button on **GPIO17** (active-low)

---

## 📦 Software Prereqs

    sudo apt-get update
    sudo apt-get install -y python3 python3-pip python3-dev \
      python3-libgpiod gpiod alsa-utils
    sudo usermod -aG audio,gpio,spi $USER   # re-login after

    # Python libs
    pip3 install --user adafruit-blinka adafruit-circuitpython-dotstar requests

Enable SPI via \`raspi-config → Interface Options → SPI → Enable\`.

---

## 🎚️ Recommended ALSA Setup (WM8960)

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

    # Persist mixer state
    sudo alsactl store

Sanity checks:

    # Playback
    aplay -D plughw:0,0 /usr/share/sounds/alsa/Front_Center.wav

    # Record & play (3s, stereo @ 48k)
    arecord -D plughw:0,0 -f S16_LE -r 48000 -c 2 -d 3 ~/recs/mic_test.wav
    aplay   -D plughw:0,0 ~/recs/mic_test.wav

---

## 🚀 Run the Client

Script location:

    ~/safetyscribeos/ssos.py

Run:

    cd ~/safetyscribeos
    DEBUG=1 python3 ssos.py

You should see logs like:

    {"ts":"...","msg":"startup","audio_dev":"plughw:0,0","rate":48000,"ch":2,"fmt":"S16_LE","debug":true}
    {"ts":"...","msg":"network_ready","host":"n8n.codemusic.ca","port":443}

**Controls**

- **Hold** button: record; **release**: upload.  
- **Double-tap**: toggle record on/off (hands-free).

---

## 🌐 n8n Workflow (Dignity Report)

**Webhook node**
- Method: POST  
- Path: \`safetyscribe\`  
- Options → **Field Name for Binary Data**: set to \`audio\` (or use the auto-detect trick below).

**Binary key gotcha (n8n versions vary)**  
Incoming binary arrives as \`audio\`. But, you can also auto-detect, reference:

    {{ Object.keys($binary)[0] }}

Examples:
- **Read/Write Files from Disk → Input Binary Field**: use \`{{ Object.keys($binary)[0] }}\`  
- **File path**:  
  \`/data/safetyscribe/{{ $binary[Object.keys($binary)[0]].fileName || ("recording_" + $now + ".wav") }}\`

**Suggested flow**
1. Webhook (receive WAV)  
2. Ensure dir exists (\`mkdir -p /data/safetyscribe\`)  
3. Write file to disk (keep the original filename if available)  
4. Transcribe (Whisper local/API)  
5. Analyze:
   - **Respect Index** (0–100)
   - **Dominance ratio** (agent:customer speaking time)
   - **Interruptions** with timestamps
   - **Negative language markers** (condescension, unfounded memory claims)
   - **Tone trend** over time  
6. Build **Incident Email** (see template below)  
7. Optionally **Send Email** (SMTP) to organization; CC advocate  
8. Respond to Webhook with JSON (e.g., \`{"led":"white"}\` and, if desired, \`{"audio_url":"…/reply.wav"}\`)

**Email template (example)**

    Subject: Dignity & Accountability Report ({{ $json.store.name }} · {{ $json.meta.dateTime }})

    Summary:
      • Person impacted: {{ $json.person.name || "Customer" }}
      • Location: {{ $json.store.name }} ({{ $json.store.address }})
      • Date/Time: {{ $json.meta.dateTimeLocal }}
      • Respect Index: {{ $json.metrics.respectIndex }}/100
      • Key Concerns: {{ $json.metrics.concerns.join(", ") }}

    Evidence Highlights:
      {{#each $json.highlights as |h|}}
        – [{{ h.ts }}] {{ h.quote }}
      {{/each}}

    Requested Action:
      • Review the interaction and provide coaching/remediation.
      • Reply with steps taken to prevent recurrence.

    Attachments/Links:
      • Full audio: {{ $json.links.audio }}
      • Clip (highlights): {{ $json.links.clip }}

    Notes:
      This report documents behavior to protect customer dignity and improve service quality.

---

## ⚙️ Configuration (env)

| Var            | Default                                          | Meaning                                 |
|----------------|--------------------------------------------------|-----------------------------------------|
| \`SS_AUDIO_DEV\` | \`plughw:0,0\`                                   | ALSA device for record/playback         |
| \`SS_RATE\`      | \`48000\`                                        | Sample rate                             |
| \`SS_CH\`        | \`2\`                                            | Channels                                |
| \`SS_ENDPOINT\`  | \`https://n8n.codemusic.ca/webhook/safetyscribe\`| Webhook URL                             |
| \`DEBUG\`        | \`0\`                                            | \`1\` → verbose logs                      |
| \`SS_TONES\`     | \`1\`                                            | Enable synth tones (0 = off)            |

---

## 🖍️ LED Patterns

| Pattern        | Meaning                     |
|----------------|-----------------------------|
| orange breathe | connecting / waiting        |
| green solid    | ready / idle                |
| rainbow spin   | recording                   |
| white chatter  | playback (speaking)         |
| red pulse      | error (retry soon)          |
| cyan blip      | unknown server instruction  |

---

## 🔊 Synth Tones (Optional)

Enabled by \`SS_TONES=1\`. Short 2–3-tone cues:
- **Startup jingle** (device ready)  
- **PTT engage/release** chirps  
- **Response received** chime  
- **Graceful shutdown** outro

---

## 🗒️ Logging

- JSON lines to \`~/safetyscribeos/ssos.log\` and stdout:  
  \`startup\`, \`network_ready\`, \`ptt_start\`, \`arecord_start\`, \`upload_ok\`, \`server_response\`, \`playback_error\`, \`ready\`, etc.

---

## 🧰 Run on Boot (systemd)

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

## 🔐 Privacy, Consent, and Ethics

- **Know your local laws**: some regions require **two-party consent** for audio recording.  
- Prefer **visible indicators** (LEDs/tones) when recording is active.  
- Secure storage and transport (HTTPS, access controls) are **mandatory** in health-adjacent contexts.  
- The **goal is improvement and protection**, not humiliation; reports should remain factual and respectful.

---

## 🛠️ Troubleshooting

- **No audio**: verify \`arecord\` test and ALSA mixer levels.  
- **n8n “no binary field”**: use \`{{ Object.keys($binary)[0] }}\` for cross-version compatibility.  
- **Quiet mic / ticks**: reduce \`ADC PCM\`, enable High-Pass Filter, moderate analog boost.  
- **LEDs stuck orange**: DNS / host reachability for your \`WIFI_TEST_HOST\`.

---

## 🗺️ Roadmap

- Per-LED patterns for dual-color cues (left/right).  
- Server-driven \`sound\` pattern objects (procedural beeps/phrases).  
- Local wake-word (optional).  
- Offline queue & retries with backoff.  
- On-device redaction and clip-maker for precise evidence snippets.  
- Additional report channels (secure portal uploads, case IDs).

---

## ❤️ Purpose

SafetyScribe exists so people can be **heard, believed, and protected**.  
When dignity is defended with clear evidence and calm accountability, organizations can coach, improve, and prevent harm from occurring again.
