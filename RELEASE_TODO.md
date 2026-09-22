# Release TODO

## Release target

- Application: unified `mixxx_display.py` aggregator.
- Gallery slug: `mixxx-display`.
- Gallery author: `redhandpl`.
- Copyright holder: Krzysztof Pędrys.
- License: MIT.
- Runtime requirements: Linux, Mixxx 2.5+, ALSA/Pulse, and `ffmpeg`.
- Target repository: [`maxswinkels/busybar-apps`](https://github.com/maxswinkels/busybar-apps).

## Current status

Completed:

- [x] MIT `LICENSE` added.
- [x] Root `.gitignore` added for Python caches, virtual environments, secrets, logs, recordings, and editor files.
- [x] `--demo --once` added to `mixxx_display.py`.
- [x] `--host` added with a default of `10.0.4.20`.
- [x] Existing positional host argument remains supported.
- [x] Demo mode skips MIDI, `ffmpeg`, and the BUSY switch stream.
- [x] 16 unit tests pass.
- [x] Python syntax and LSP diagnostics are clean.

## Critical path

### 1. Full preview demo modes

Extend the demo mode so previews can render each display mode independently and cycle through both modes:

```text
--demo --mode status --once
--demo --mode spectrum --once
--demo --mode cycle
```

Requirements:

- `status` uses deterministic synthetic `MixxxStatus` data and the production status renderer.
- `spectrum` uses deterministic synthetic audio and the production spectrum renderer.
- `cycle` switches modes only in demo mode; live mode remains controlled by the BUSY physical switch.
- Add deterministic unit tests for all three modes.

### 2. Prepare the gallery application

Create the release artifact under `apps/mixxx-display/` in a `busybar-apps` checkout:

```text
apps/mixxx-display/
├── app.py
├── manifest.yaml
├── requirements.txt
├── preview.png or preview.gif
└── Mixxx mapping files, if accepted by the gallery maintainers
```

Requirements:

- `app.py` is the self-contained entrypoint.
- The BUSY application ID is `mixxx-display`.
- The artifact does not depend on helper files outside its app folder.
- The existing modular source repository remains the canonical development source.

### 3. Align the application contract

Verify and test:

- `--host <ip[:port]>`, defaulting to `10.0.4.20`;
- `--demo` and `--once` behavior;
- stable element IDs;
- valid `#RRGGBBAA` colors;
- all elements remain inside the 72×16 display;
- `409` responses do not terminate the application;
- the display state is released on shutdown;
- `BUSY_API_TOKEN` remains outside version control.

### 4. Add gallery metadata

Create `manifest.yaml` with only the fields supported by the gallery schema:

- `name`;
- `author`;
- `description` with a maximum of 200 characters;
- `tags`;
- `preview`;
- optional `repo`.

Do not add a `license` field to the manifest.

### 5. Generate and validate preview

Use the gallery preview tooling:

```bash
npm ci
npm run preview -- mixxx-display --png
```

The result must be a real emulator or BUSY Bar output with dimensions `720×160`.

For a real device, use the preview recorder with an upstream BUSY host. The emulator can be used for deterministic layout validation without Mixxx hardware.

### 6. Run release validation

In the source repository:

```bash
python3 -m unittest -v \
  tests.test_mixxx_display \
  tests.test_mixxx_spectrum \
  tests.test_mixxx_mixer
```

In the `busybar-apps` checkout:

```bash
npm ci
npm run build
npm run check -- mixxx-display --run
npm run ai:sync
```

Perform one hardware end-to-end check:

```text
Mixxx → MIDI mapping → mixxx_display.py → BUSY Bar
Mixxx → ALSA/Pulse → ffmpeg → spectrum → BUSY Bar
BUSY START/PRESS → status/spectrum
```

### 7. Commit and submit

- [ ] Add all intended source and documentation files to Git.
- [ ] Verify no environment files, tokens, or recordings are included.
- [ ] Review the final release diff.
- [ ] Create the `busybar-apps` pull request.
- [ ] Wait for gallery CI and maintainer review.
- [ ] Do not merge or trigger a public deploy without explicit approval.

## Deferred work

The following items are outside the first gallery release:

- systemd automatic startup and recovery;
- PipeWire migration;
- track information mode;
- centralized runtime configuration;
- full restart and recovery matrix;
- PyPI or wheel packaging;
- simultaneous status and spectrum applications.

## Release blockers

The release is not ready until these are complete:

1. Full demo modes are implemented and tested.
2. The single-file gallery artifact exists.
3. Manifest validation passes.
4. A real 720×160 preview is generated.
5. Gallery `build`, `check`, and `ai:sync` commands pass.
6. A hardware end-to-end smoke test is completed.

## Technical review

Review is required before opening or merging the public gallery pull request because the release changes the public CLI contract, package layout, dependency handoff, and displayed application behavior.
