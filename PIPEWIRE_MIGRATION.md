# PipeWire Migration

## Overview

This document describes the planned migration from the current ALSA loopback path to PipeWire for Mixxx audio capture.

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

The existing ALSA loopback path remains the rollback and compatibility path.

No PipeWire packages, user services, Mixxx settings, or host audio routes are changed by this repository plan.

## Scope

In scope:

- identify the physical PipeWire sink and its monitor;
- verify that Mixxx can output through PipeWire/Pulse;
- verify that `ffmpeg` can capture the sink monitor;
- run the spectrum renderer with `--audio-format pulse`;
- update the documented runtime command after successful validation;
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
- Existing ALSA configuration documented in `README.md`.
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

The existing application code already supports `--audio-format pulse`; this migration changes the runtime audio source, not the spectrum algorithm.

## Phase 5: Update runtime documentation and services

After successful manual validation:

- document the tested sink monitor name or discovery procedure in `README.md`;
- update the unified application command to use `--audio-format pulse`;
- remove the PipeWire profile's dependency on `mixxx-alsaloop.service`;
- keep the ALSA service and commands documented as fallback;
- document that only one audio route may be active at a time;
- record the tested sample rate and channel configuration.

Do not delete the ALSA configuration until the PipeWire path has passed the hardware smoke test and rollback has been verified.

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
- PipeWire restarts can temporarily interrupt both physical audio and spectrum capture.
- No automatic recovery policy is introduced by this migration plan.

## Technical review

Review this document and the host-specific inventory before executing Phase 2. The migration changes the user audio graph and can interrupt physical playback. A tested rollback is required before removing or disabling the ALSA fallback.
