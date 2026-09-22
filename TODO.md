# TODO

## Completed

- [x] Deploy the latest MIDI transport and ALSA auto-connection changes to the remote Mixxx host. User confirmed that the local and remote applications are synchronized.
- [x] Replace high-rate level SysEx traffic with MIDI CC messages.
- [x] Reuse persistent BusyBar HTTP clients during display updates.

## Pending

### 1. Automatic startup and recovery

Create and enable systemd user services for:

- `alsaloop` audio forwarding;
- `mixxx_display.py`;
- the required virtual MIDI and audio prerequisites.

Acceptance criteria:

- after reboot, ALSA loopback is available;
- the unified display app starts automatically;
- Mixxx starts after the virtual MIDI device exists;
- service failures trigger restart and are visible through `journalctl`;
- `loginctl enable-linger pi` is configured where boot-time startup is required.

### 2. PipeWire direct-DAC routing

**Selected approach:** Option A — route Mixxx to the physical PipeWire sink and read that sink's monitor through PulseAudio compatibility. Keep the current ALSA loopback path as the fallback.

Evaluate and, if useful, migrate from the current ALSA loopback route to PipeWire:

```text
Mixxx → PipeWire DAC sink
             └→ sink monitor → mixxx_spectrum.py
```

Acceptance criteria:

- Mixxx can select or use the physical DAC without routing Master through `MixxxLoopback`;
- the spectrum app reads a PipeWire/Pulse monitor source;
- ALSA `alsaloop` is no longer required for this mode;
- the current ALSA setup remains documented as a fallback.

### 3. End-to-end hardware validation

Add an execution-backed validation procedure covering:

```text
Mixxx → virtual MIDI → mixxx_display.py → BusyBar
Mixxx → ALSA/PipeWire audio → spectrum renderer → BusyBar
BUSY START/PRESS → status/spectrum mode switch
```

The validation should confirm:

- BPM, remaining time, deck state, and volume levels;
- 25 FPS level updates;
- spectrum rendering with each style and theme;
- switch-driven mode changes;
- clean recovery after Mixxx, ALSA, BusyBar, or network restarts.

### 4. Centralized runtime configuration

Move operational values out of long command lines into a documented configuration surface:

- BusyBar host;
- MIDI port;
- audio format and device;
- display interval;
- level and spectrum FPS;
- spectrum style and theme;
- request timeouts.

Keep `BUSY_API_TOKEN` in a separate permission-restricted environment file. Do not commit credentials.

Acceptance criteria:

- status and spectrum modes use one consistent configuration;
- command-line arguments still override configuration values;
- sensitive values remain outside version control.

### 5. Full preview demo modes

Extend the demo mode so previews can render each display mode independently and cycle through both modes:

```text
--demo --mode status --once
--demo --mode spectrum --once
--demo --mode cycle
```

## Out of scope

- Running status and spectrum as simultaneous full-screen applications.
- Replacing Mixxx's audio engine or modifying Mixxx itself.
- Automatic modification of the remote host without an explicit deployment request.
