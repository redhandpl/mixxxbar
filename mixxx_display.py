#!/usr/bin/env python3
"""Unified Mixxx display controlled by the BUSY Bar physical switch.

The BUSY Bar switch toggles between the status layout and the spectrum layout.
The switch is read from BUSY's /api/status/ws stream. No timed rotation is
used; the initial mode is the Mixxx status layout.

Usage:
    BUSY_API_TOKEN=... python mixxx_display.py 10.26.16.123
    python mixxx_display.py --host 127.0.0.1:8080 --demo --once
"""

from __future__ import annotations

import argparse
import asyncio
import logging
import os
import queue
import struct
import sys
import threading
import time
from typing import Any

try:
    from .mixxx_mixer import (
        DEFAULT_DISPLAY_INTERVAL,
        DEFAULT_DISPLAY_TIMEOUT,
        DEFAULT_LEVEL_FPS,
        DEFAULT_REQUEST_TIMEOUT,
        REMAINING_BLINK_INTERVAL,
        REMAINING_WARNING_SECONDS,
        BusyBarDisplay,
        MidiOutputConnector,
        MixxxStatus,
        open_midi_input,
        update_status_from_midi_message,
    )
    from .mixxx_spectrum import (
        BAND_COUNT,
        CHUNK_SAMPLES,
        DEFAULT_FPS as DEFAULT_SPECTRUM_FPS,
        STYLE_NAMES,
        THEME_NAMES,
        BusyBarOutput,
        band_magnitudes,
        build_pixels,
        capture_error,
        spectrum_heights,
        start_capture,
    )
except ImportError:
    from mixxx_mixer import (
        DEFAULT_DISPLAY_INTERVAL,
        DEFAULT_DISPLAY_TIMEOUT,
        DEFAULT_LEVEL_FPS,
        DEFAULT_REQUEST_TIMEOUT,
        REMAINING_BLINK_INTERVAL,
        REMAINING_WARNING_SECONDS,
        BusyBarDisplay,
        MidiOutputConnector,
        MixxxStatus,
        open_midi_input,
        update_status_from_midi_message,
    )
    from mixxx_spectrum import (
        BAND_COUNT,
        CHUNK_SAMPLES,
        DEFAULT_FPS as DEFAULT_SPECTRUM_FPS,
        STYLE_NAMES,
        THEME_NAMES,
        BusyBarOutput,
        band_magnitudes,
        build_pixels,
        capture_error,
        spectrum_heights,
        start_capture,
    )

SWITCH_MODE_STATUS = "status"
SWITCH_MODE_SPECTRUM = "spectrum"


class DisplayMode:
    def __init__(self) -> None:
        self._mode = SWITCH_MODE_STATUS
        self._lock = threading.Lock()

    def get(self) -> str:
        with self._lock:
            return self._mode

    def toggle(self) -> str:
        with self._lock:
            self._mode = (
                SWITCH_MODE_SPECTRUM
                if self._mode == SWITCH_MODE_STATUS
                else SWITCH_MODE_STATUS
            )
            return self._mode


def switch_position(state: Any) -> int | None:
    """Extract a physical switch event from a decoded BUSY state message."""
    if not isinstance(state, dict):
        return None
    for update in state.get("updates", []):
        if not isinstance(update, dict):
            continue
        input_update = update.get("input")
        if not isinstance(input_update, dict):
            continue
        event = input_update.get("switch_event")
        if isinstance(event, dict) and "position" in event:
            try:
                return int(event["position"])
            except (TypeError, ValueError):
                return None
    return None


def physical_toggle_event(state: Any) -> str | None:
    """Return a start-button press or switch-position event token from a dict."""
    if not isinstance(state, dict):
        return None
    for update in state.get("updates", []):
        if not isinstance(update, dict):
            continue
        input_update = update.get("input")
        if not isinstance(input_update, dict):
            continue
        button = input_update.get("button_event")
        if isinstance(button, dict):
            button_name = button.get("button")
            action = button.get("action")
            if button_name in ("START", 2) and action in ("PRESS", 0):
                return "button:START"
        position = switch_position({"updates": [{"input": input_update}]})
        if position is not None:
            return f"switch:{position}"
    return None


def protobuf_toggle_event(state: Any) -> str | None:
    """Return a start-button or switch event from a decoded BUSY protobuf."""
    for update in state.updates:
        if not update.HasField("input"):
            continue
        input_event = update.input
        if input_event.HasField("button_event"):
            button = input_event.button_event
            if button.button == 2 and button.action == 0:  # START/PRESS
                return "button:START"
        if input_event.HasField("switch_event"):
            return f"switch:{input_event.switch_event.position}"
    return None


async def monitor_switch(host: str, token: str, mode: DisplayMode, stop: threading.Event) -> None:
    """Toggle display mode from BUSY start-button or switch events."""
    from busylib import AsyncBusyBar  # pyright: ignore[reportMissingImports]
    from busylib.state_stream_proto import state_pb2  # pyright: ignore[reportMissingImports]

    last_event: str | None = None
    while not stop.is_set():
        try:
            async with AsyncBusyBar(host, token=token, timeout=5, max_retries=0) as busy_bar:
                stream: Any = busy_bar.stream_status_ws(decode_protobuf=False)
                while not stop.is_set():
                    try:
                        raw_state = await asyncio.wait_for(stream.__anext__(), timeout=1)
                    except asyncio.TimeoutError:
                        continue
                    except StopAsyncIteration:
                        break
                    if not isinstance(raw_state, bytes):
                        continue
                    state = state_pb2.State()
                    state.ParseFromString(raw_state)
                    event = protobuf_toggle_event(state)
                    if event is None:
                        last_event = None
                        continue
                    if event == last_event:
                        continue
                    last_event = event
                    logging.info("BUSY %s; display mode=%s", event, mode.toggle())
        except Exception as exc:
            if stop.is_set():
                return
            logging.warning("BUSY switch stream failed: %s", type(exc).__name__)
            await asyncio.sleep(2)


class AudioReader:
    """Read ffmpeg PCM chunks in a worker so MIDI and switch events stay responsive."""

    def __init__(self, audio_format: str, audio_device: str) -> None:
        self.audio_format = audio_format
        self.audio_device = audio_device
        self.samples: queue.Queue[list[float]] = queue.Queue(maxsize=2)
        self.stop = threading.Event()
        self.process: Any = None
        self.thread: threading.Thread | None = None

    def start(self) -> None:
        self.process = start_capture(self.audio_format, self.audio_device)
        self.thread = threading.Thread(target=self._read, name="mixxx-audio", daemon=True)
        self.thread.start()

    def _read(self) -> None:
        if self.process is None or self.process.stdout is None:
            return
        while not self.stop.is_set():
            raw = self.process.stdout.read(CHUNK_SAMPLES * 2)
            if len(raw) != CHUNK_SAMPLES * 2:
                if not self.stop.is_set():
                    detail = capture_error(self.process)
                    suffix = f": {detail}" if detail else ""
                    logging.warning("Audio capture ended%s", suffix)
                return
            values = [sample / 32768.0 for sample in struct.unpack(f"<{CHUNK_SAMPLES}h", raw)]
            try:
                self.samples.put_nowait(values)
            except queue.Full:
                try:
                    self.samples.get_nowait()
                except queue.Empty:
                    pass
                self.samples.put_nowait(values)

    def latest(self) -> list[float] | None:
        latest = None
        while True:
            try:
                latest = self.samples.get_nowait()
            except queue.Empty:
                return latest

    def close(self) -> None:
        self.stop.set()
        if self.process is not None:
            self.process.terminate()
            try:
                self.process.wait(timeout=2)
            except Exception:
                self.process.kill()
                self.process.wait()
        if self.thread is not None:
            self.thread.join(timeout=2)


def positive_float(value: str) -> float:
    try:
        number = float(value)
    except ValueError as exc:
        raise argparse.ArgumentTypeError("must be a number") from exc
    if number <= 0:
        raise argparse.ArgumentTypeError("must be greater than zero")
    return number


def demo_status() -> MixxxStatus:
    """Return a deterministic status frame for previews and local checks."""
    return MixxxStatus(
        deck1_bpm=124.6,
        deck2_bpm=128.0,
        deck1_remaining_seconds=215,
        deck2_remaining_seconds=180,
        deck1_playing=True,
        deck2_playing=True,
        active_deck=1,
        deck1_level=96,
        deck2_level=72,
        deck1_volume=110,
        deck2_volume=96,
        crossfader=64,
        main_level=104,
    )


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Switch between Mixxx status and spectrum on a BUSY Bar."
    )
    parser.add_argument("host", nargs="?", help="BUSY Bar host or IP address")
    parser.add_argument("--host", dest="host_option", help="BUSY Bar host or IP address")
    parser.add_argument("--port", default="BUSYBAR Mixxx Status", help="virtual MIDI port name")
    parser.add_argument("--audio-format", choices=("alsa", "pulse"), default="alsa")
    parser.add_argument("--audio-device", default="mixxx_capture")
    parser.add_argument("--display-interval", type=positive_float, default=DEFAULT_DISPLAY_INTERVAL)
    parser.add_argument("--level-fps", type=positive_float, default=DEFAULT_LEVEL_FPS)
    parser.add_argument("--spectrum-fps", type=positive_float, default=DEFAULT_SPECTRUM_FPS)
    parser.add_argument("--style", choices=STYLE_NAMES, default="bars")
    parser.add_argument("--theme", choices=THEME_NAMES, default="classic")
    parser.add_argument("--request-timeout", type=int, default=DEFAULT_REQUEST_TIMEOUT)
    parser.add_argument("--display-timeout", type=int, default=DEFAULT_DISPLAY_TIMEOUT)
    parser.add_argument(
        "--demo",
        action="store_true",
        help="use deterministic synthetic Mixxx status instead of MIDI and audio input",
    )
    parser.add_argument(
        "--once",
        action="store_true",
        help="draw one demo frame and exit",
    )
    args = parser.parse_args(argv)
    if args.host is not None and args.host_option is not None:
        parser.error("specify the BUSY Bar host either positionally or with --host")
    if args.once and not args.demo:
        parser.error("--once requires --demo")
    args.host = args.host_option or args.host or "10.0.4.20"
    return args


def run_demo(args: argparse.Namespace, display: BusyBarDisplay) -> int:
    status = demo_status()
    while True:
        display.show(status)
        if args.once:
            return 0
        time.sleep(args.display_interval)


def run(args: argparse.Namespace) -> int:
    token = os.environ.get("BUSY_API_TOKEN")
    if not token and not args.demo:
        print("BUSY_API_TOKEN is required", file=sys.stderr)
        return 2
    token = token or ""

    status_display: BusyBarDisplay | None = None
    spectrum_display: BusyBarOutput | None = None
    mode = DisplayMode()
    stop = threading.Event()
    audio: AudioReader | None = None
    switch_thread: threading.Thread | None = None
    midi_connector: MidiOutputConnector | None = None

    latest_status: MixxxStatus | None = None
    last_status_received: float | None = None
    next_layout = 0.0
    next_levels = 0.0
    next_spectrum = 0.0
    warning_active = False
    rendered_mode: str | None = None
    level_interval = 1.0 / args.level_fps
    spectrum_interval = 1.0 / args.spectrum_fps
    running_max = [1.0] * BAND_COUNT
    previous_heights = [0] * BAND_COUNT
    peaks = [0.0] * BAND_COUNT

    try:
        status_display = BusyBarDisplay(
            [args.host],
            token,
            request_timeout=args.request_timeout,
            display_timeout=args.display_timeout,
        )
        spectrum_display = BusyBarOutput(
            args.host,
            token,
            request_timeout=args.request_timeout,
        )
        assert status_display is not None
        assert spectrum_display is not None

        if args.demo:
            return run_demo(args, status_display)

        audio = AudioReader(args.audio_format, args.audio_device)
        switch_thread = threading.Thread(
            target=lambda: asyncio.run(monitor_switch(args.host, token, mode, stop)),
            name="busybar-switch",
            daemon=True,
        )
        audio.start()
        switch_thread.start()
        with open_midi_input(args.port, virtual=True) as midi_port:
            midi_connector = MidiOutputConnector(args.port)
            midi_connector.start()
            while True:
                now = time.monotonic()
                for message in midi_port.iter_pending():
                    status = update_status_from_midi_message(latest_status, message)
                    if status is not None:
                        latest_status = status
                        last_status_received = now

                if last_status_received is not None and now - last_status_received > 1.0:
                    latest_status = None
                    last_status_received = None
                    warning_active = False

                current_mode = mode.get()
                if current_mode != rendered_mode:
                    status_display.initial_clear = True
                    spectrum_display.initial_clear = True
                    rendered_mode = current_mode
                    next_layout = 0.0
                    next_levels = 0.0
                    next_spectrum = 0.0
                    warning_active = False

                if current_mode == SWITCH_MODE_STATUS and latest_status is not None:
                    current_warning = (
                        0 < latest_status.deck1_remaining_seconds <= REMAINING_WARNING_SECONDS
                        or 0 < latest_status.deck2_remaining_seconds <= REMAINING_WARNING_SECONDS
                    )
                    if current_warning != warning_active:
                        warning_active = current_warning
                        next_layout = 0.0
                    if now >= next_layout:
                        blink_red = (now // REMAINING_BLINK_INTERVAL) % 2 == 0
                        status_display.show(latest_status, blink_red=blink_red)
                        layout_interval = min(
                            args.display_interval,
                            REMAINING_BLINK_INTERVAL,
                        ) if warning_active else args.display_interval
                        next_layout = now + layout_interval
                        next_levels = now + level_interval
                    elif now >= next_levels:
                        status_display.show_levels(latest_status)
                        next_levels = now + level_interval
                elif current_mode == SWITCH_MODE_SPECTRUM and now >= next_spectrum:
                    samples = audio.latest()
                    if samples is not None:
                        magnitudes = band_magnitudes(samples)
                        heights = spectrum_heights(
                            magnitudes,
                            running_max,
                            previous_heights,
                            peaks,
                            peak_fall=max(0.3, 9.0 / max(1, args.spectrum_fps)),
                        )
                        previous_heights = heights
                        spectrum_display.show(
                            build_pixels(heights, peaks, args.theme, args.style),
                            display_timeout=args.display_timeout,
                        )
                        next_spectrum = now + spectrum_interval
                time.sleep(0.005)
    finally:
        stop.set()
        if midi_connector is not None:
            midi_connector.close()
        if switch_thread is not None:
            switch_thread.join(timeout=2)
        if audio is not None:
            audio.close()
        if status_display is not None:
            status_display.close()
        if spectrum_display is not None:
            spectrum_display.close()


def main(argv: list[str] | None = None) -> int:
    try:
        return run(parse_args(argv))
    except KeyboardInterrupt:
        return 0
    except (OSError, RuntimeError) as exc:
        print(str(exc), file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
