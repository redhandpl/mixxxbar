# PipeWire Migration

## Overview

This document records the completed PipeWire-default audio path validated on the Raspberry Pi DAC Pro host and provides a setup/validation procedure for other hosts. PipeWire/Pulse is now the repository default; ALSA loopback remains an explicit fallback.

The selected approach is **Option A**:

```text
Mixxx
  → physical PipeWire sink
      ├─ physical DAC
      └─ sink monitor
          → ffmpeg -f pulse
          → mixxx_spectrum.py
          → BUSY Bar
```

The ALSA loopback path remains the rollback and compatibility path.

The repository does not modify host packages, user services, Mixxx settings, or audio routes. The validated target already has PipeWire/Pulse running; the setup commands below apply when configuring another host.

## Scope

In scope:

- identify the physical PipeWire sink and its monitor;
- verify that Mixxx can output through PipeWire/Pulse;
- verify that `ffmpeg` can capture the sink monitor;
- run the spectrum renderer with `--audio-format pulse`;
- use Pulse capture and the verified DAC Pro monitor as the repository defaults;
- allow other hosts to override the monitor with `--audio-device`;
- preserve ALSA as a documented fallback.

Out of scope:

- creating a dedicated virtual sink;
- custom WirePlumber routing rules;
- JACK graph management;
- automatic host reconfiguration;
- removing the existing ALSA configuration;
- changing the MIDI status path.

## Preconditions

- Linux host running Mixxx 2.5 or newer.
- Working physical audio output.
- Existing ALSA fallback configuration documented in `README.md`.
- `ffmpeg` installed.
- A valid BUSY Bar host and `BUSY_API_TOKEN` for application tests.
- Permission to install packages and manage user services.
- A rollback window during which the physical audio path can be interrupted.

System changes require separate operational approval before execution.

## Phase 1: Inventory the current audio graph

Run the following commands read-only:

```bash
pactl info
pactl list short sinks
pactl list short sources
wpctl status
ffmpeg -devices | grep pulse
```

Record:

- PipeWire server name;
- physical sink name;
- sink state and sample rate;
- monitor source name, normally ending in `.monitor`;
- whether the PulseAudio compatibility layer is available;
- whether another application is already using the physical sink.

Expected result:

```text
PipeWire is running.
pipewire-pulse is available.
A physical sink exists.
The physical sink exposes a monitor source.
ffmpeg lists the pulse input format.
```

Stop and escalate if the host exposes no physical sink, no monitor, or no PulseAudio compatibility layer.

## Phase 2: Prepare the user audio stack

Install the required components only after approval:

```bash
sudo apt update
sudo apt install -y \
  pipewire \
  pipewire-pulse \
  wireplumber \
  pipewire-alsa \
  pulseaudio-utils \
  ffmpeg
```

Enable the user services for the active desktop user:

```bash
systemctl --user enable --now pipewire
systemctl --user enable --now pipewire-pulse
systemctl --user enable --now wireplumber
```

Re-run the Phase 1 inventory after installation. Do not continue if the server name or sink graph changes unexpectedly.

## Phase 3: Route Mixxx to the physical sink

Configure Mixxx to use the PipeWire-compatible output path available on the host.

The exact Mixxx selection depends on the installed Mixxx build. Possible paths include:

- PulseAudio through `pipewire-pulse`;
- ALSA through `pipewire-alsa`;
- the PipeWire default ALSA device;
- JACK integration backed by PipeWire.

Confirm that:

- Mixxx plays audio through the physical DAC;
- the selected physical sink remains available in `pactl list short sinks`;
- the corresponding sink monitor receives the Mixxx signal;
- the ALSA `alsaloop` process is stopped during this test.

Do not run the ALSA `alsaloop` route and the PipeWire route simultaneously.

## Phase 4: Validate monitor capture

Set the monitor name discovered during inventory and run a short capture test:

```bash
ffmpeg \
  -hide_banner \
  -loglevel error \
  -f pulse \
  -i <physical-sink>.monitor \
  -t 1 \
  -ac 1 \
  -ar 22050 \
  -f null -
```

The command must exit successfully while Mixxx is playing.

The Raspberry Pi DAC Pro monitor validated for this repository is `alsa_output.platform-soc_sound.stereo-fallback.monitor`. Monitor names are host-specific. On the target host, `ffmpeg` captured a non-silent signal from this source while Mixxx played.

Then run the standalone spectrum application:

```bash
.venv/bin/python mixxx_spectrum.py \
  --audio-format pulse \
  --audio-device <physical-sink>.monitor \
  --fps 15 \
  <busybar-host>
```

Finally test the unified application:

```bash
.venv/bin/python mixxx_display.py \
  --audio-format pulse \
  --audio-device <physical-sink>.monitor \
  --spectrum-fps 15 \
  <busybar-host>
```

Both CLI entrypoints now default to `--audio-format pulse` and the verified monitor. Pass `--audio-device` to select another host's monitor; `--audio-format alsa --audio-device mixxx_capture` remains the explicit fallback. The spectrum algorithm is unchanged.

## Phase 5: Runtime defaults and host-specific services

The repository defaults and `README.md` service examples now use PipeWire/Pulse with the verified DAC Pro monitor. On another host:

- discover its monitor with `pactl list short sources` and override it with `--audio-device`;
- run PipeWire user services as the same user that runs Mixxx;
- keep `mixxx-alsaloop.service` disabled in the PipeWire profile;
- when selecting the ALSA fallback, pass `--audio-format alsa --audio-device mixxx_capture` and enable the ALSA forwarder;
- run only one audio route at a time.

The repository does not install or enable host services. Keep the ALSA configuration until PipeWire has passed the hardware smoke test and rollback has been verified.

## Validation criteria

### Audio routing

- Given Mixxx is playing, when the physical PipeWire sink is selected, then audio is audible through the physical DAC.
- Given Mixxx is playing, when the sink monitor is captured by `ffmpeg`, then the capture exits successfully.
- Given no other audio application is active, when the sink monitor is analyzed, then the spectrum follows Mixxx output.

### Application behavior

- Given the Pulse monitor is available, when `mixxx_spectrum.py` runs with `--audio-format pulse`, then frames are rendered on BUSY.
- Given the Pulse monitor is available, when `mixxx_display.py` enters spectrum mode, then the spectrum is rendered without ALSA `alsaloop`.
- Given the status MIDI path is active, when PipeWire is selected, then BPM and deck status continue to work unchanged.

### Recovery

- Given PipeWire is restarted, when the monitor becomes available again, then the application reports the capture failure clearly and can be restarted without code changes.
- Given the PipeWire path fails, when the ALSA fallback is restored, then Mixxx audio and spectrum output work through `mixxx_capture`.

## Rollback

1. Stop `mixxx_display.py` and any standalone spectrum process.
2. Stop the PipeWire-specific audio route used for the test.
3. Restore Mixxx Master output to the ALSA loopback device:

   ```text
   hw:MixxxLoopback,0,0
   ```

4. Start the ALSA forwarder:

   ```bash
   alsaloop \
     -C mixxx_capture \
     -P plughw:CARD=Pro,DEV=0 \
     -r 44100 \
     -c 2 \
     -t 50000
   ```

5. Start the application with:

   ```bash
   .venv/bin/python mixxx_display.py \
     --audio-format alsa \
     --audio-device mixxx_capture \
     <busybar-host>
   ```

6. Verify physical audio, spectrum output, and status mode.

Rollback is complete when the original ALSA path works without PipeWire monitor input.

## Troubleshooting

### The monitor contains desktop audio

Option A uses the physical sink monitor. Other applications routed to the same sink may appear in the spectrum. Stop unrelated audio applications and repeat the test. If isolation is required, plan a dedicated PipeWire sink as a separate migration.

### Mixxx does not show a PipeWire device

Verify `pipewire-pulse`, `pipewire-alsa`, and WirePlumber. Mixxx may expose the route through ALSA or JACK rather than a device literally named `PipeWire`.

### `ffmpeg` cannot open the monitor

Re-check:

```bash
pactl list short sources
wpctl status
```

Use the exact monitor source name, including the `.monitor` suffix.

### The physical DAC is silent

Verify the default sink and active routes with `pactl` or `wpctl`. Stop the PipeWire test route and perform the documented ALSA rollback if audio is needed immediately.

### The spectrum is stale after a restart

Confirm that the sink monitor still exists and restart the application after PipeWire has recreated the monitor source.

## Risks and limitations

- The physical sink monitor may include non-Mixxx audio.
- Sink and monitor names are host-specific and may change after hardware or profile changes.
- User services depend on the active user session and may require linger for boot-time startup.
- PipeWire restarts can temporarily interrupt physical audio and spectrum capture; no automatic recovery policy is configured.

## Technical review

Review the host-specific inventory before installing packages or changing Mixxx routing on another host. PipeWire changes the user audio graph and can interrupt physical playback. Verify the ALSA rollback before disabling its configuration.
