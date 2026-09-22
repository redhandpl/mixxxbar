#!/usr/bin/env python3
"""Bridge Mixxx MIDI status to BUSY Bars over the network.

The companion Mixxx mapping emits a small SysEx frame on a virtual MIDI port.
This process reads the frame and renders the latest status on one or more BUSY
Bars. The BUSY Bars only need network access from this host.

Usage:
    BUSY_API_TOKEN=... python mixxx_mixer.py --port "BUSYBAR Mixxx Status" 10.26.16.113
    python mixxx_mixer.py --dry-run --once --port "BUSYBAR Mixxx Status" 10.26.16.113
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import re
import subprocess
import sys
import threading
import time
from dataclasses import asdict, dataclass, replace
from typing import Any, Sequence

APP_NAME = "mixxx_busybar"
DEFAULT_MIDI_PORT = "BUSYBAR Mixxx Status"
DEFAULT_DISPLAY_INTERVAL = 1.0
DEFAULT_LEVEL_FPS = 25.0
REMAINING_WARNING_SECONDS = 30
REMAINING_BLINK_INTERVAL = 0.5
DEFAULT_STATUS_TIMEOUT = 1.0
DEFAULT_DISPLAY_TIMEOUT = 3
DEFAULT_REQUEST_TIMEOUT = 5
DELIVERY_LOG_INTERVAL = 5.0
MIDI_CONNECT_INTERVAL = 1.0
DISPLAY_WIDTH = 72
DISPLAY_HEIGHT = 16
BPM_X = (15, 39)
BPM_WIDTH = 18
BPM_ROW_Y = (2, 9)
BALANCE_X = 29
BALANCE_WIDTH = 13
SEPARATOR_X = 35
VOLUME_BAR_X = (0, 69)
VOLUME_BAR_WIDTH = 3
VOLUME_BAR_BACKGROUND = "#303030FF"
MAIN_VOLUME_X = 34
MAIN_VOLUME_Y = 1
MAIN_VOLUME_WIDTH = 3
MAIN_VOLUME_HEIGHT = DISPLAY_HEIGHT - MAIN_VOLUME_Y
DECK_ICON_X = (5, 59)
DECK_ICON_Y = 4
DECK_ICON_SIZE = 8
DECK_ICON_RADIUS = 4
DECK_MARKER_POSITIONS = ((3, 0), (6, 3), (3, 6), (0, 3))
DECK_ICON_DIM_LEVEL = 127 * 0.07
DECK_ICON_DIM_COLOR = "#606060FF"

COLORS = {
    "ok": "#00FF00FF",
    "warning": "#FFFF00FF",
    "critical": "#FF0000FF",
    "unknown": "#FFFFFFFF",
}
BPM_SYNC_COLOR = "#00FFFFFF"
BPM_CLOSE_COLOR = "#0080FFFF"
BPM_DRIFT_COLOR = "#FF00FFFF"
BPM_SYNC_THRESHOLD = 0.15
BPM_CLOSE_THRESHOLD = 0.5

# 0x7D is the MIDI non-commercial manufacturer ID. The remaining bytes are
# the application marker and protocol version.
SYSEX_START = 0xF0
SYSEX_END = 0xF7
SYSEX_HEADER = (0x7D, 0x42, 0x42, 0x01)
SYSEX_DATA_LENGTH = 17
LEVEL_MIDI_STATUS = 0xB0
LEVEL_MIDI_CONTROLS = {
    0x10: "deck1_level",
    0x11: "deck2_level",
    0x12: "deck1_volume",
    0x13: "deck2_volume",
    0x14: "crossfader",
    0x15: "main_level",
}


@dataclass(frozen=True)
class MixxxStatus:
    """Current status exported by the Mixxx mapping."""

    deck1_bpm: float
    deck2_bpm: float
    deck1_remaining_seconds: int
    deck2_remaining_seconds: int
    deck1_playing: bool
    deck2_playing: bool
    active_deck: int
    deck1_level: int
    deck2_level: int
    deck1_volume: int = 127
    deck2_volume: int = 127
    crossfader: int = 64
    main_level: int = 127


def _clamp(value: int, minimum: int, maximum: int) -> int:
    return max(minimum, min(maximum, value))


def _encode_14bit(value: int) -> tuple[int, int]:
    value = _clamp(value, 0, 0x3FFF)
    return value // 128, value % 128


def _decode_14bit(high: int, low: int) -> int:
    return high * 128 + low


def encode_sysex(status: MixxxStatus) -> tuple[int, ...]:
    """Encode status as a complete MIDI SysEx message."""
    if status.active_deck not in (0, 1, 2):
        raise ValueError("active_deck must be 0, 1, or 2")

    bpm1 = _encode_14bit(round(max(0.0, status.deck1_bpm) * 10))
    bpm2 = _encode_14bit(round(max(0.0, status.deck2_bpm) * 10))
    remaining1 = _encode_14bit(max(0, status.deck1_remaining_seconds))
    remaining2 = _encode_14bit(max(0, status.deck2_remaining_seconds))
    return (
        SYSEX_START,
        *SYSEX_HEADER,
        *bpm1,
        *bpm2,
        1 if status.deck1_playing else 0,
        1 if status.deck2_playing else 0,
        status.active_deck,
        _clamp(status.deck1_level, 0, 127),
        _clamp(status.deck2_level, 0, 127),
        *remaining1,
        *remaining2,
        SYSEX_END,
    )


def decode_sysex(data: Sequence[int]) -> MixxxStatus | None:
    """Decode a complete or mido-style payload-only SysEx message."""
    payload = list(data)
    if payload and payload[0] == SYSEX_START:
        payload = payload[1:]
    if payload and payload[-1] == SYSEX_END:
        payload = payload[:-1]

    if len(payload) != SYSEX_DATA_LENGTH or tuple(payload[:4]) != SYSEX_HEADER:
        return None
    if any(value < 0 or value > 127 for value in payload):
        return None

    if payload[8] not in (0, 1) or payload[9] not in (0, 1):
        return None

    active_deck = payload[10]
    if active_deck not in (0, 1, 2):
        return None

    return MixxxStatus(
        deck1_bpm=_decode_14bit(payload[4], payload[5]) / 10,
        deck2_bpm=_decode_14bit(payload[6], payload[7]) / 10,
        deck1_remaining_seconds=_decode_14bit(payload[13], payload[14]),
        deck2_remaining_seconds=_decode_14bit(payload[15], payload[16]),
        deck1_playing=bool(payload[8]),
        deck2_playing=bool(payload[9]),
        active_deck=active_deck,
        deck1_level=payload[11],
        deck2_level=payload[12],
    )


def update_status_from_midi_message(
    current: MixxxStatus | None,
    message: Any,
) -> MixxxStatus | None:
    """Merge a full status SysEx or a high-rate level CC into the snapshot."""
    if getattr(message, "type", None) == "sysex":
        status = decode_sysex(getattr(message, "data", ()))
        if status is None or current is None:
            return status
        return replace(
            status,
            deck1_volume=current.deck1_volume,
            deck2_volume=current.deck2_volume,
            crossfader=current.crossfader,
            main_level=current.main_level,
        )
    if getattr(message, "type", None) != "control_change":
        return None
    if getattr(message, "channel", -1) != LEVEL_MIDI_STATUS - 0xB0:
        return None
    field = LEVEL_MIDI_CONTROLS.get(getattr(message, "control", -1))
    if field is None or current is None:
        return None
    try:
        value = int(message.value)
    except (TypeError, ValueError):
        return None
    return replace(current, **{field: _clamp(value, 0, 127)})


def status_from_midi_message(message: Any) -> MixxxStatus | None:
    """Decode a complete status frame, ignoring level-only MIDI messages."""
    return update_status_from_midi_message(None, message)


def format_bpm(value: float) -> str:
    return "--.-" if value <= 0 else f"{value:.1f}"


def format_remaining(seconds: int) -> str:
    minutes, remaining_seconds = divmod(max(0, seconds), 60)
    return f"{minutes:02d}:{remaining_seconds:02d}"


def level_fill_height(value: int, height: int = DISPLAY_HEIGHT) -> int:
    return round(_clamp(value, 0, 127) / 127 * height)


def level_segments(
    value: int,
    *,
    height: int = DISPLAY_HEIGHT,
    y_offset: int = 0,
) -> list[tuple[str, int, int, str]]:
    """Return bottom-up volume segments clipped to the current level."""
    fill_height = level_fill_height(value, height)
    green_end = round(height * 0.50)
    yellow_end = round(height * 0.80)
    boundaries = (
        ("green", 0, green_end, COLORS["ok"]),
        ("yellow", green_end, yellow_end, COLORS["warning"]),
        ("red", yellow_end, height, COLORS["critical"]),
    )
    segments: list[tuple[str, int, int, str]] = []
    for name, start, end, color in boundaries:
        visible_height = max(0, min(fill_height, end) - start)
        draw_height = max(1, visible_height)
        y = y_offset + height - start - draw_height
        segments.append((name, y, draw_height, color if visible_height else VOLUME_BAR_BACKGROUND))
    return segments


def deck_icon_color(level: int) -> str:
    return DECK_ICON_DIM_COLOR if level < DECK_ICON_DIM_LEVEL else COLORS["unknown"]


def balance_marker_position(value: int) -> int:
    return BALANCE_X + round(_clamp(value, 0, 127) / 127 * (BALANCE_WIDTH - 1))


def remaining_color(seconds: int, blink_red: bool) -> str:
    if blink_red and 0 < seconds <= REMAINING_WARNING_SECONDS:
        return COLORS["critical"]
    return COLORS["unknown"]


def bpm_color(status: MixxxStatus) -> str:
    if status.deck1_bpm <= 0 or status.deck2_bpm <= 0:
        return COLORS["unknown"]
    difference = abs(status.deck1_bpm - status.deck2_bpm)
    if difference <= BPM_SYNC_THRESHOLD + 1e-9:
        return BPM_SYNC_COLOR
    if difference <= BPM_CLOSE_THRESHOLD + 1e-9:
        return BPM_CLOSE_COLOR
    lower = min(status.deck1_bpm, status.deck2_bpm)
    higher = max(status.deck1_bpm, status.deck2_bpm)
    half_double_difference = abs(higher / 2 - lower)
    if half_double_difference <= BPM_SYNC_THRESHOLD + 1e-9:
        return BPM_SYNC_COLOR
    if half_double_difference <= BPM_CLOSE_THRESHOLD + 1e-9:
        return BPM_CLOSE_COLOR
    return BPM_DRIFT_COLOR


def display_lines(
    status: MixxxStatus,
    *,
    blink_red: bool = False,
) -> list[tuple[str, str, int, int]]:
    """Return BPM and remaining-time lines with layout columns and rows."""
    bpm_text_color = bpm_color(status)
    return [
        (format_bpm(status.deck1_bpm), bpm_text_color, 0, 0),
        (format_bpm(status.deck2_bpm), bpm_text_color, 1, 0),
        (
            format_remaining(status.deck1_remaining_seconds),
            remaining_color(status.deck1_remaining_seconds, blink_red),
            0,
            1,
        ),
        (
            format_remaining(status.deck2_remaining_seconds),
            remaining_color(status.deck2_remaining_seconds, blink_red),
            1,
            1,
        ),
    ]


def next_animation_phase(phase: int, playing: bool) -> int:
    if not playing:
        return phase
    return (phase + 1) % len(DECK_MARKER_POSITIONS)


def positive_float(value: str) -> float:
    try:
        number = float(value)
    except ValueError as exc:
        raise argparse.ArgumentTypeError("must be a number") from exc
    if number <= 0:
        raise argparse.ArgumentTypeError("must be greater than zero")
    return number


class BusyBarDisplay:
    """Render Mixxx status on network-connected BUSY Bars."""

    def __init__(
        self,
        hosts: list[str],
        token: str,
        *,
        request_timeout: int = DEFAULT_REQUEST_TIMEOUT,
        display_timeout: int = DEFAULT_DISPLAY_TIMEOUT,
    ) -> None:
        try:
            from busylib import BusyBar, types  # pyright: ignore[reportMissingImports]
        except ImportError as exc:
            raise RuntimeError(
                "Install dependencies first: python -m pip install -r requirements.txt"
            ) from exc
        self.busy_bar: Any = BusyBar
        self.types: Any = types
        self.hosts = hosts
        self.token = token
        self.request_timeout = request_timeout
        self.display_timeout = display_timeout
        self.clients: list[tuple[str, Any]] = [
            (
                host,
                self.busy_bar(
                    host,
                    token=token,
                    timeout=request_timeout,
                    max_retries=0,
                ),
            )
            for host in hosts
        ]
        self.initial_clear = True
        self.animation_phases = [0, 0]
        self._last_delivery_log = 0.0

    def _level_elements(self, status: MixxxStatus) -> list[Any]:
        elements: list[Any] = []
        for name, level, bar_x, bar_y, bar_width, bar_height in (
            ("deck1", status.deck1_level, VOLUME_BAR_X[0], 0, VOLUME_BAR_WIDTH, DISPLAY_HEIGHT),
            ("deck2", status.deck2_level, VOLUME_BAR_X[1], 0, VOLUME_BAR_WIDTH, DISPLAY_HEIGHT),
            ("main", status.main_level, MAIN_VOLUME_X, MAIN_VOLUME_Y, MAIN_VOLUME_WIDTH, MAIN_VOLUME_HEIGHT),
        ):
            elements.append(
                self.types.RectangleElement(
                    id=f"mixxx-volume-{name}-background",
                    type="rectangle",
                    x=bar_x,
                    y=bar_y,
                    width=bar_width,
                    height=bar_height,
                    fill="solid",
                    fill_colors=[VOLUME_BAR_BACKGROUND],
                    border_width=0,
                    display=self.types.DisplayName.FRONT,
                    timeout=self.display_timeout,
                )
            )
            for segment, y, height, color in level_segments(
                level,
                height=bar_height,
                y_offset=bar_y,
            ):
                elements.append(
                    self.types.RectangleElement(
                        id=f"mixxx-volume-{name}-{segment}",
                        type="rectangle",
                        x=bar_x,
                        y=y,
                        width=bar_width,
                        height=height,
                        fill="solid",
                        fill_colors=[color],
                        border_width=0,
                        display=self.types.DisplayName.FRONT,
                        timeout=self.display_timeout,
                    )
                )
        return elements

    def _layout_elements(self, status: MixxxStatus, *, blink_red: bool = False) -> list[Any]:
        elements: list[Any] = [
            self.types.TextElement(
                id=f"mixxx-info-{column}-{row}",
                type="text",
                text=text,
                font="small",
                color=color,
                align="top_left",
                x=BPM_X[column],
                y=BPM_ROW_Y[row],
                width=BPM_WIDTH,
                display=self.types.DisplayName.FRONT,
                timeout=self.display_timeout,
            )
            for text, color, column, row in display_lines(status, blink_red=blink_red)
        ]
        elements.extend(
            (
                self.types.RectangleElement(
                    id="mixxx-balance-background",
                    type="rectangle",
                    x=BALANCE_X,
                    y=0,
                    width=BALANCE_WIDTH,
                    height=1,
                    fill="solid",
                    fill_colors=[VOLUME_BAR_BACKGROUND],
                    border_width=0,
                    display=self.types.DisplayName.FRONT,
                    timeout=self.display_timeout,
                ),
                self.types.RectangleElement(
                    id="mixxx-balance-marker",
                    type="rectangle",
                    x=balance_marker_position(status.crossfader),
                    y=0,
                    width=1,
                    height=1,
                    fill="solid",
                    fill_colors=[COLORS["ok"]],
                    border_width=0,
                    display=self.types.DisplayName.FRONT,
                    timeout=self.display_timeout,
                ),
            )
        )
        elements.append(
            self.types.RectangleElement(
                id="mixxx-separator",
                type="rectangle",
                x=SEPARATOR_X,
                y=1,
                width=1,
                height=DISPLAY_HEIGHT - 1,
                fill="solid",
                fill_colors=[COLORS["unknown"]],
                border_width=0,
                display=self.types.DisplayName.FRONT,
                timeout=self.display_timeout,
            )
        )
        for deck, x, volume in (
            (1, DECK_ICON_X[0], status.deck1_volume),
            (2, DECK_ICON_X[1], status.deck2_volume),
        ):
            icon_color = deck_icon_color(volume)
            phase = (self.animation_phases[deck - 1] + deck - 1) % len(DECK_MARKER_POSITIONS)
            marker_x, marker_y = DECK_MARKER_POSITIONS[phase]
            elements.extend(
                (
                    self.types.RectangleElement(
                        id=f"mixxx-deck-{deck}-circle",
                        type="rectangle",
                        x=x,
                        y=DECK_ICON_Y,
                        width=DECK_ICON_SIZE,
                        height=DECK_ICON_SIZE,
                        radius=DECK_ICON_RADIUS,
                        fill="none",
                        border_width=1,
                        border_color=icon_color,
                        display=self.types.DisplayName.FRONT,
                        timeout=self.display_timeout,
                    ),
                    self.types.RectangleElement(
                        id=f"mixxx-deck-{deck}-hub",
                        type="rectangle",
                        x=x + 3,
                        y=DECK_ICON_Y + 3,
                        width=2,
                        height=2,
                        fill="solid",
                        fill_colors=[icon_color],
                        border_width=0,
                        display=self.types.DisplayName.FRONT,
                        timeout=self.display_timeout,
                    ),
                    self.types.RectangleElement(
                        id=f"mixxx-deck-{deck}-marker",
                        type="rectangle",
                        x=x + marker_x,
                        y=DECK_ICON_Y + marker_y,
                        width=2,
                        height=2,
                        fill="solid",
                        fill_colors=[icon_color],
                        border_width=0,
                        display=self.types.DisplayName.FRONT,
                        timeout=self.display_timeout,
                    ),
                )
            )
        return elements

    def close(self) -> None:
        for host, client in self.clients:
            try:
                client.display_clear(application_name=APP_NAME)
            except Exception as exc:
                logging.warning("BUSY display clear failed for %s: %s", host, type(exc).__name__)
            try:
                client.close()
            except Exception as exc:
                logging.warning("BUSY client close failed for %s: %s", host, type(exc).__name__)

    def _log_delivery_failure(self, host: str, exc: Exception) -> None:
        now = time.monotonic()
        if now - self._last_delivery_log < DELIVERY_LOG_INTERVAL:
            return
        status_code = getattr(exc, "status_code", None) or getattr(exc, "code", None)
        if status_code == 409:
            logging.warning("BUSY display conflict for %s; retrying", host)
        else:
            logging.warning("BUSY display failed for %s: %s", host, type(exc).__name__)
        self._last_delivery_log = now

    def _draw(self, elements: list[Any], *, clear_before_draw: bool) -> int:
        failures = 0
        for host, busy_bar in self.clients:
            try:
                busy_bar.display_draw(
                    self.types.DisplayElements(
                        application_name=APP_NAME,
                        priority=100,
                        elements=elements,
                    ),
                    clear_before_draw=clear_before_draw,
                )
            except Exception as exc:  # one unreachable BUSY must not stop the bridge
                self._log_delivery_failure(host, exc)
                failures += 1
        return failures

    def show(self, status: MixxxStatus, *, blink_red: bool = False) -> int:
        self.animation_phases = [
            next_animation_phase(phase, playing)
            for phase, playing in zip(
                self.animation_phases,
                (status.deck1_playing, status.deck2_playing),
            )
        ]
        failures = self._draw(
            self._layout_elements(status, blink_red=blink_red) + self._level_elements(status),
            clear_before_draw=self.initial_clear,
        )
        self.initial_clear = failures > 0
        return failures

    def show_levels(self, status: MixxxStatus) -> int:
        failures = self._draw(self._level_elements(status), clear_before_draw=False)
        self.initial_clear = failures > 0
        return failures


def open_midi_input(port_name: str, *, virtual: bool) -> Any:
    try:
        import mido  # type: ignore[reportMissingImports]
    except ImportError as exc:
        raise RuntimeError(
            "Install MIDI dependencies first: python -m pip install -r requirements.txt"
        ) from exc

    try:
        backend = os.environ.get("MIDO_BACKEND")
        if backend is None and sys.platform.startswith("linux"):
            backend = "mido.backends.rtmidi/LINUX_ALSA"
        if backend is not None:
            mido.set_backend(backend)
        return mido.open_ioport(
            port_name,
            virtual=virtual,
            client_name=port_name if virtual else None,
        )
    except Exception as exc:
        mode = "virtual" if virtual else "existing"
        raise RuntimeError(f"Cannot open {mode} MIDI input {port_name!r}: {exc}") from exc


def _midi_port_address(name: str) -> str | None:
    match = re.search(r"(\d+):(\d+)$", name)
    return f"{match.group(1)}:{match.group(2)}" if match else None


def _alsa_output_ports() -> list[str]:
    try:
        result = subprocess.run(
            ["aconnect", "-l"],
            check=False,
            capture_output=True,
            text=True,
        )
    except OSError:
        return []
    ports: list[str] = []
    client_id: str | None = None
    client_name: str | None = None
    for line in result.stdout.splitlines():
        client = re.match(r"^client (\d+): '([^']+)'", line)
        if client:
            client_id, client_name = client.groups()
            continue
        port = re.match(r"^\s+(\d+) '([^']+)'", line)
        if port and client_id and client_name and client_name.startswith("Client-"):
            ports.append(f"{client_id}:{port.group(1)}")
    return ports


class MidiOutputConnector:
    """Connect Mixxx's ALSA PortMidi output to the bridge input port."""

    def __init__(self, port_name: str) -> None:
        self.port_name = port_name
        self.stop = threading.Event()
        self.thread = threading.Thread(target=self._run, name="midi-connect", daemon=True)
        self.connected: set[tuple[str, str]] = set()

    def start(self) -> None:
        self.thread.start()

    def close(self) -> None:
        self.stop.set()
        if self.thread.is_alive():
            self.thread.join(timeout=2)

    def _run(self) -> None:
        try:
            import mido  # type: ignore[reportMissingImports]
        except ImportError:
            return
        while not self.stop.is_set():
            bridge_input = next(
                (
                    _midi_port_address(name)
                    for name in mido.get_input_names()
                    if name.startswith(f"{self.port_name}:")
                ),
                None,
            )
            if bridge_input:
                for source in _alsa_output_ports():
                    connection = (source, bridge_input)
                    if connection in self.connected:
                        continue
                    try:
                        result = subprocess.run(
                            ["aconnect", source, bridge_input],
                            check=False,
                            capture_output=True,
                            text=True,
                        )
                    except OSError:
                        return
                    if result.returncode == 0:
                        self.connected.add(connection)
                        logging.info("Connected ALSA MIDI %s to %s", source, bridge_input)
            self.stop.wait(MIDI_CONNECT_INTERVAL)


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Bridge Mixxx status from MIDI to network-connected BUSY Bars."
    )
    parser.add_argument("hosts", nargs="+", help="BUSY Bar host or IP addresses")
    parser.add_argument("--port", default=DEFAULT_MIDI_PORT, help="MIDI port name")
    parser.add_argument(
        "--existing-port",
        action="store_true",
        help="listen on an existing MIDI port instead of creating a virtual one",
    )
    parser.add_argument("--display-interval", type=positive_float, default=DEFAULT_DISPLAY_INTERVAL)
    parser.add_argument("--level-fps", type=positive_float, default=DEFAULT_LEVEL_FPS)
    parser.add_argument("--status-timeout", type=positive_float, default=DEFAULT_STATUS_TIMEOUT)
    parser.add_argument("--request-timeout", type=int, default=DEFAULT_REQUEST_TIMEOUT)
    parser.add_argument("--display-timeout", type=int, default=DEFAULT_DISPLAY_TIMEOUT)
    parser.add_argument("--dry-run", action="store_true", help="print status without contacting BUSY")
    parser.add_argument("--once", action="store_true", help="print or display the first valid status and exit")
    return parser.parse_args(argv)


def run(args: argparse.Namespace) -> int:
    if not args.dry_run and not os.environ.get("BUSY_API_TOKEN"):
        print("BUSY_API_TOKEN is required", file=sys.stderr)
        return 2

    display = None
    if not args.dry_run:
        display = BusyBarDisplay(
            args.hosts,
            os.environ["BUSY_API_TOKEN"],
            request_timeout=args.request_timeout,
            display_timeout=args.display_timeout,
        )

    with open_midi_input(args.port, virtual=not args.existing_port) as port:
        logging.info(
            "Listening on %s MIDI port %s",
            "virtual" if not args.existing_port else "existing",
            args.port,
        )
        if not args.existing_port:
            MidiOutputConnector(args.port).start()
        latest: MixxxStatus | None = None
        last_received: float | None = None
        next_layout = 0.0
        next_levels = 0.0
        layout_rendered = False
        warning_active = False
        level_interval = 1.0 / args.level_fps
        while True:
            now = time.monotonic()
            for message in port.iter_pending():
                status = update_status_from_midi_message(latest, message)
                if status is not None:
                    latest = status
                    last_received = now

            if last_received is not None and now - last_received > args.status_timeout:
                latest = None
                last_received = None
                layout_rendered = False
                warning_active = False

            if latest is not None:
                current_warning = (
                    0 < latest.deck1_remaining_seconds <= REMAINING_WARNING_SECONDS
                    or 0 < latest.deck2_remaining_seconds <= REMAINING_WARNING_SECONDS
                )
                if current_warning != warning_active:
                    warning_active = current_warning
                    next_layout = 0.0

                if args.dry_run:
                    if not layout_rendered or now >= next_layout:
                        print(json.dumps(asdict(latest), sort_keys=True), flush=True)
                        layout_rendered = True
                        next_layout = now + args.display_interval
                        next_levels = now + level_interval
                        if args.once:
                            return 0
                elif display is not None:
                    if not layout_rendered or now >= next_layout:
                        blink_red = (
                            (now // REMAINING_BLINK_INTERVAL) % 2 == 0
                        )
                        display.show(latest, blink_red=blink_red)
                        layout_rendered = True
                        layout_interval = min(
                            args.display_interval,
                            REMAINING_BLINK_INTERVAL,
                        ) if warning_active else args.display_interval
                        next_layout = now + layout_interval
                        next_levels = now + level_interval
                        if args.once:
                            return 0
                    elif now >= next_levels:
                        display.show_levels(latest)
                        next_levels = now + level_interval
            time.sleep(0.005)


def main(argv: list[str] | None = None) -> int:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    try:
        return run(parse_args(argv))
    except KeyboardInterrupt:
        return 0
    except RuntimeError as exc:
        print(str(exc), file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
