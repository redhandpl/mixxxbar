# TODO

## Completed

- [x] Deploy the latest MIDI transport and ALSA auto-connection changes to the remote Mixxx host. User confirmed that the local and remote applications are synchronized.
- [x] Replace high-rate level SysEx traffic with MIDI CC messages.
- [x] Reuse persistent BusyBar HTTP clients during display updates.
- [x] Make PipeWire/Pulse capture the default with the verified Raspberry Pi DAC Pro monitor; retain ALSA loopback as an explicit fallback. Playback and non-silent monitor capture were verified on the host.

## Pending

### 1. Automatic startup and recovery

Create and enable systemd user services for PipeWire/Pulse audio and `mixxx_display.py`. Keep the ALSA loopback forwarder opt-in for fallback use.

Acceptance criteria:

- after reboot, PipeWire/Pulse and the selected physical DAC monitor are available;
- the unified display app starts automatically with the default PipeWire capture route;
- ALSA loopback is enabled only when the fallback route is selected;
- Mixxx starts after the virtual MIDI device exists;
- service failures trigger restart and are visible through `journalctl`;
- `loginctl enable-linger pi` is configured where boot-time startup is required.

### 2. End-to-end hardware validation

Add an execution-backed validation procedure covering:

```text
Mixxx → virtual MIDI → mixxx_display.py → BusyBar
Mixxx → PipeWire monitor (default) or ALSA loopback (fallback) → spectrum renderer → BusyBar
BUSY START/PRESS → status/spectrum mode switch
```

The validation should confirm:

- BPM, remaining time, deck state, and volume levels;
- 25 FPS level updates;
- spectrum rendering with each style and theme;
- switch-driven mode changes;
- clean recovery after Mixxx, PipeWire/ALSA, BusyBar, or network restarts.

### 3. Centralized runtime configuration

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

### 4. Full preview demo modes

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
