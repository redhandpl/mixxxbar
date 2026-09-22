# BUSY Bar Mixxx Apps

## Overview

This project connects Mixxx to a network-connected BUSY Bar and turns a DJ session into a live 72×16 performance dashboard.

The recommended application, `mixxx_display.py`, combines two views in one process:

- **Status mode** — displays both deck BPM values, remaining track time, play state, active-deck activity, deck and master levels, crossfader position, and animated deck indicators.
- **Spectrum mode** — captures Mixxx's master output through ALSA or Pulse/PipeWire and renders a 24-band spectrum with selectable styles and colour themes.

Press the physical BUSY Bar `START/PRESS` button to switch between the two full-screen views. Switch-position events are also supported as a fallback. The application does not rotate views on a timer.

The data flow is:

```text
Mixxx MIDI mapping → Mixxx status → mixxx_display.py → BUSY Bar
Mixxx master audio → ALSA/Pulse capture → spectrum renderer → BUSY Bar
BUSY START/PRESS → status/spectrum mode switch
```

The repository also contains the underlying standalone components:

- `mixxx_mixer.py` — Mixxx MIDI status bridge and status-only display.
- `mixxx_spectrum.py` — audio capture and spectrum-only display.
- `mixxx_mapping/` — the Mixxx controller mapping that exports status and level data.

The application targets a Linux host running Mixxx 2.5 or newer. Examples below use `10.26.16.123` as the BUSY Bar address; replace it with the address of your device.

## Prerequisites

The instructions target a Debian/Raspberry Pi OS Linux host running Mixxx.

Install the system packages:

```bash
sudo apt update
sudo apt install -y python3 python3-venv ffmpeg alsa-utils
```

The host needs:

- Mixxx 2.5 or newer;
- a working ALSA playback device;
- the `snd-aloop` kernel module;
- `alsaloop`, `aplay`, and `arecord`;
- network access from the Mixxx host to the BUSY Bar;
- a BUSY API token.

## Install the repository

Place the repository at `/home/pi/busybar` or adjust every path in the service examples below.

```bash
cd /home/pi/busybar
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt
```

Create a token file readable only by the `pi` user. Do not commit it:

```bash
cat > /home/pi/busybar/busybar.env <<'EOF'
BUSY_API_TOKEN=replace-with-the-BUSY-API-token
EOF
chmod 600 /home/pi/busybar/busybar.env
```

## Run the unified switch-controlled application

Start `mixxx_display.py` before Mixxx. It creates the virtual MIDI port that Mixxx must enumerate at startup.

```bash
cd /home/pi/busybar
set -a
. ./busybar.env
set +a

.venv/bin/python mixxx_display.py \
  --port "BUSYBAR Mixxx Status" \
  --audio-format alsa \
  --audio-device mixxx_capture \
  --display-interval 0.2 \
  --level-fps 25 \
  --spectrum-fps 15 \
  --style bars \
  --theme classic \
  10.26.16.123
```

The spectrum mode accepts the same styles and themes as `mixxx_spectrum.py` through `--style` and `--theme`.

The first mode is the BPM/deck status view. A physical BUSY Bar `START` button press toggles between status and spectrum. Switch-position events are also accepted as a fallback. Input events are read from BUSY's `/api/status/ws` stream. No timed rotation is used.

In Mixxx, load the `BUSYBAR Mixxx Status` mapping after the unified app is running. The standalone `mixxx_mixer.py` and `mixxx_spectrum.py` processes are not needed when using this mode.

## Configure the Mixxx MIDI status mapping

The MIDI mapping exports BPM, play state, deck activity, and VU levels through a virtual ALSA MIDI port. The bridge creates the port; Mixxx must load the mapping from its user controller directory.

```bash
mkdir -p /home/pi/.mixxx/controllers
cp /home/pi/busybar/mixxx_mapping/mixxx_busybar.js \
   /home/pi/busybar/mixxx_mapping/mixxx_busybar.midi.xml \
   /home/pi/.mixxx/controllers/
```

The bridge must be running before Mixxx starts. It creates the virtual MIDI device, and Mixxx enumerates that device during startup. Starting the bridge after Mixxx requires a complete Mixxx restart.

Start the bridge before starting Mixxx:

```bash
cd /home/pi/busybar
set -a
. ./busybar.env
set +a

.venv/bin/python mixxx_mixer.py \
  --port "BUSYBAR Mixxx Status" \
  --display-interval 0.2 \
  --level-fps 25 \
  10.26.16.123
```

The Mixxx mapping sends status frames every 40 ms. `--level-fps 25` updates only the two volume bars at 25 FPS; `--display-interval` controls the slower BPM, remaining-time, and deck layout redraw. A centered 13-pixel top line shows the Mixxx crossfader balance. The 3-pixel center bar shows the `[Main]` master VU level. Volume bars are segmented: green from 0–50%, yellow from 50–80%, and red above 80%. Remaining time at 30 seconds or less alternates between white and red every 0.5 seconds. BPM colors show synchronization: cyan up to 0.15 BPM difference, blue up to 0.5, the same cyan/blue colors after normalizing a 1:2 or 2:1 relationship, and magenta for other larger differences; empty decks stay white. Deck icons use the channel fader (`volume`), not the VU meter: below 7% fader volume, the icon is dimmed.

In Mixxx:

1. Open **Preferences → Controllers**.
2. Select the `BUSYBAR Mixxx Status` virtual MIDI device.
3. Load the `BUSYBAR Mixxx Status` mapping.
4. Apply the mapping.

The existing Arduino MIDI controller uses a separate mapping and remains unchanged. The bridge periodically connects Mixxx's ALSA PortMidi output to its virtual input with `aconnect`; `alsa-utils` is therefore required.

Audio capture failures include the ffmpeg error detail in the application log.

## Configure the ALSA loopback for Mixxx audio

The spectrum application does not read audio from Mixxx's MIDI mapping. Mixxx must send its Master output to an ALSA loopback, and the loopback must be shared between the physical output forwarder and the spectrum application.

### Load and persist `snd-aloop`

Create the module configuration:

```bash
sudo tee /etc/modprobe.d/mixxx-loopback.conf >/dev/null <<'EOF'
options snd-aloop id=MixxxLoopback pcm_substreams=2
EOF

sudo tee /etc/modules-load.d/mixxx-loopback.conf >/dev/null <<'EOF'
snd-aloop
EOF
```

Load it immediately without rebooting:

```bash
sudo modprobe snd-aloop
```

Verify that the card exists:

```bash
cat /proc/asound/cards
aplay -l
arecord -l
```

The card should be named `MixxxLoopback` and expose playback and capture devices.

### Create a shared capture source

Create `/home/pi/.asoundrc`:

```bash
cat > /home/pi/.asoundrc <<'EOF'
pcm.mixxx_dsnoop {
    type dsnoop
    ipc_key 4242
    slave {
        pcm "hw:MixxxLoopback,1,0"
        format S16_LE
        rate 44100
        channels 2
    }
}

pcm.mixxx_capture {
    type plug
    slave.pcm "mixxx_dsnoop"
}
EOF
```

`mixxx_capture` lets both `alsaloop` and `mixxx_spectrum.py` read the same Mixxx output. Do not use `hw:MixxxLoopback,1,1` for the spectrum; separate loopback substreams do not mirror the same playback stream.

### Configure Mixxx output

In Mixxx **Preferences → Sound Hardware**:

1. Select the `ALSA` sound API.
2. Set the Master output to `hw:MixxxLoopback,0,0`.
3. Keep the sample rate at `44100` Hz.
4. Configure headphones separately if required.

### Forward the loopback to the physical output

Find the physical playback card:

```bash
aplay -l
```

The current host uses the Raspberry Pi DAC Pro as `CARD=Pro,DEV=0`. Start the forwarder:

```bash
alsaloop \
  -C mixxx_capture \
  -P plughw:CARD=Pro,DEV=0 \
  -r 44100 \
  -c 2 \
  -t 50000
```

Replace `CARD=Pro,DEV=0` with the actual physical output shown by `aplay -l` on a different host.

The resulting path is:

```text
Mixxx Master
  → hw:MixxxLoopback,0,0
    → hw:MixxxLoopback,1,0
      → mixxx_capture
        ├─ alsaloop → physical DAC
        └─ mixxx_spectrum.py
```

## Optional: PipeWire monitor instead of ALSA loopback

Installing `pactl` alone does not add a `PipeWire` entry to Mixxx. `pactl` is only a command-line client. The PipeWire server and its PulseAudio compatibility layer must also be installed and running.

Install the optional audio stack:

```bash
sudo apt install -y pipewire pipewire-pulse wireplumber pipewire-alsa pulseaudio-utils
systemctl --user enable --now pipewire
systemctl --user enable --now pipewire-pulse
systemctl --user enable --now wireplumber
```

Verify the server and available monitor sources:

```bash
pactl info | grep "Server Name"
pactl list short sinks
pactl list short sources
```

The server name should contain `PipeWire`. A monitor source normally ends with `.monitor`.

PipeWire may not appear as a literal Mixxx **Sound API** entry. Mixxx can use it through an ALSA default device, JACK integration, or the PulseAudio compatibility layer, depending on the installed Mixxx build and host configuration.

For a PipeWire monitor, stop the ALSA `alsaloop` route and configure Mixxx to use the PipeWire-routed DAC. Then run the spectrum app with:

```bash
.venv/bin/python mixxx_spectrum.py \
  --audio-format pulse \
  --audio-device <sink-monitor-name> \
  --fps 15 \
  10.26.16.123
```

Do not run the ALSA loopback route and the PipeWire route at the same time. They are alternative audio paths.

## Run the spectrum application manually

Keep `alsaloop` running in one terminal. Start the spectrum application in another:

```bash
cd /home/pi/busybar
set -a
. ./busybar.env
set +a

.venv/bin/python mixxx_spectrum.py \
  --audio-format alsa \
  --audio-device mixxx_capture \
  --fps 15 \
  10.26.16.123
```

The application captures 24 frequency bands, applies attack/decay smoothing and peak caps, renders a full 72x16 PNG, and uploads rotating frame assets to the BUSY Bar.

Available render styles:

- `bars` — bottom-anchored vertical bars;
- `mirror` — bars grow around the horizontal center;
- `segments` — discrete stacked LED blocks;
- `dots` — one moving dot per band;
- `wave` — a connected oscilloscope-style contour.

Available themes:

- `classic`;
- `fire`;
- `ocean`;
- `aurora`;
- `rainbow`.

Select them with `--style` and `--theme`, for example:

```bash
.venv/bin/python mixxx_spectrum.py \
  --audio-format alsa \
  --audio-device mixxx_capture \
  --style segments \
  --theme fire \
  --fps 15 \
  10.26.16.123
```

Run a synthetic frame without audio input to validate the BUSY connection:

```bash
set -a
. ./busybar.env
set +a

.venv/bin/python mixxx_spectrum.py \
  --demo \
  --once \
  10.26.16.123
```

Validate the capture source directly:

```bash
ffmpeg \
  -hide_banner \
  -loglevel error \
  -f alsa \
  -i mixxx_capture \
  -t 1 \
  -ac 1 \
  -ar 22050 \
  -f null -
```

## Start automatically after reboot

The following systemd user services start the loopback forwarder and one BUSY application after reboot. They assume the `pi` user and `/home/pi/busybar` paths.

The MIDI bridge must start before Mixxx. If Mixxx is launched automatically by a desktop or sway session, make that autostart entry depend on the selected display service (`mixxx-display.service` is recommended); otherwise Mixxx will not see the virtual MIDI device.

### ALSA forwarder service

Create `~/.config/systemd/user/mixxx-alsaloop.service`:

```ini
[Unit]
Description=Forward Mixxx ALSA loopback to the physical DAC
After=default.target

[Service]
ExecStart=/usr/bin/alsaloop -C mixxx_capture -P plughw:CARD=Pro,DEV=0 -r 44100 -c 2 -t 50000
Restart=always
RestartSec=2

[Install]
WantedBy=default.target
```

### Unified switch-controlled service

Create `~/.config/systemd/user/mixxx-display.service`:

```ini
[Unit]
Description=Switch-controlled Mixxx status and spectrum display
Requires=mixxx-alsaloop.service
After=mixxx-alsaloop.service

[Service]
WorkingDirectory=/home/pi/busybar
EnvironmentFile=/home/pi/busybar/busybar.env
Environment=MIDO_BACKEND=mido.backends.rtmidi/LINUX_ALSA
ExecStart=/home/pi/busybar/.venv/bin/python /home/pi/busybar/mixxx_display.py --port BUSYBAR Mixxx Status --audio-format alsa --audio-device mixxx_capture --display-interval 0.2 --level-fps 25 --spectrum-fps 15 --style bars --theme classic 10.26.16.123
Restart=always
RestartSec=3

[Install]
WantedBy=default.target
```

The service starts in status mode. Each physical BUSY Bar `START` button press toggles between status and spectrum. Use this service instead of the two standalone display services below.

### Spectrum service

Create `~/.config/systemd/user/mixxx-spectrum.service`:

```ini
[Unit]
Description=Display the Mixxx spectrum on BUSY Bar
Requires=mixxx-alsaloop.service
After=mixxx-alsaloop.service

[Service]
WorkingDirectory=/home/pi/busybar
EnvironmentFile=/home/pi/busybar/busybar.env
ExecStart=/home/pi/busybar/.venv/bin/python /home/pi/busybar/mixxx_spectrum.py --audio-format alsa --audio-device mixxx_capture --fps 15 10.26.16.123
Restart=always
RestartSec=3

[Install]
WantedBy=default.target
```

### Status bridge service

Create `~/.config/systemd/user/mixxx-busybar.service` and use this service instead of the spectrum service when the BPM/deck status view is wanted:

```ini
[Unit]
Description=Display Mixxx BPM and deck status on BUSY Bar
After=default.target

[Service]
WorkingDirectory=/home/pi/busybar
EnvironmentFile=/home/pi/busybar/busybar.env
Environment=MIDO_BACKEND=mido.backends.rtmidi/LINUX_ALSA
ExecStart=/home/pi/busybar/.venv/bin/python /home/pi/busybar/mixxx_mixer.py --port BUSYBAR Mixxx Status --display-interval 0.2 10.26.16.123
Restart=always
RestartSec=3

[Install]
WantedBy=default.target
```

Do not enable `mixxx-display.service` together with either standalone display service. They draw full-screen content with different application names.

### Enable services

For the unified switch-controlled mode:

```bash
loginctl enable-linger pi
systemctl --user daemon-reload
systemctl --user enable --now mixxx-alsaloop.service
systemctl --user enable --now mixxx-display.service
```

For standalone operation, enable only one of `mixxx-spectrum.service` or `mixxx-busybar.service` instead of `mixxx-display.service`.

Check service state and logs:

```bash
systemctl --user status mixxx-alsaloop.service
systemctl --user status mixxx-spectrum.service
journalctl --user -u mixxx-alsaloop.service -f
journalctl --user -u mixxx-spectrum.service -f
```

## Troubleshooting

### `No such device: hw:Loopback`

The configured card name is `MixxxLoopback`, not `Loopback`:

```bash
arecord -l
```

Use:

```text
hw:MixxxLoopback,1,0
```

### `Device or resource busy`

Check which process owns the ALSA device:

```bash
fuser -v /dev/snd/pcmC5D1c /dev/snd/pcmC1D0p
```

One `alsaloop` process should own the physical DAC and one shared `dsnoop` capture session should serve both consumers.

### `ffmpeg audio capture ended`

Check that:

- Mixxx Master output is `hw:MixxxLoopback,0,0`;
- `alsaloop` is running with `-C mixxx_capture`;
- the spectrum app uses `--audio-device mixxx_capture`;
- the `mixxx_capture` definition exists in `/home/pi/.asoundrc`;
- `arecord -l` still shows `MixxxLoopback`.

### BusyBar stays blank

Check the network and token:

```bash
ping -c 2 10.26.16.123
```

Run the synthetic frame test with `--demo --once`. It removes audio routing from the diagnostic path.

## Limitations

- The spectrum app requires an audio loopback. It does not read PCM data from Mixxx's MIDI mapping.
- The repository does not configure system audio automatically.
- The spectrum and status apps are mutually exclusive on one full-screen BUSY Bar.
- ALSA card numbers can change; use stable card names from `aplay -l` where possible.
- The spectrum renderer uses pure Python Goertzel analysis and is intentionally CPU-light rather than a full FFT implementation.

## Validation

Run the local tests:

```bash
python3 -m unittest -v \
  tests.test_mixxx_display \
  tests.test_mixxx_spectrum \
  tests.test_mixxx_mixer
```
