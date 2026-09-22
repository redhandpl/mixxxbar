#!/usr/bin/env python3
"""Display a Mixxx output spectrum on a network-connected BUSY Bar.

The app captures an OS audio loopback through ffmpeg, computes 24 spectrum
bands with the Goertzel algorithm, renders one 72x16 PNG frame, and uploads it
to BUSY. Mixxx must route its master output to the selected capture source.

Examples:
    BUSY_API_TOKEN=... python mixxx_spectrum.py \
        --audio-format alsa --audio-device hw:Loopback,1,0 10.26.16.123
    BUSY_API_TOKEN=... python mixxx_spectrum.py \
        --audio-format pulse --audio-device mixxx.monitor 10.26.16.123
"""

from __future__ import annotations

import argparse
import colorsys
import logging
import math
import os
import shutil
import struct
import subprocess
import sys
import time
import zlib
from typing import Any, Sequence

APP_NAME = "mixxx_spectrum"
DISPLAY_WIDTH = 72
DISPLAY_HEIGHT = 16
SAMPLE_RATE = 22050
CHUNK_SAMPLES = 2048
BAND_COUNT = 24
BAND_FREQS = tuple(
    60.0 * (10_000.0 / 60.0) ** (index / (BAND_COUNT - 1))
    for index in range(BAND_COUNT)
)
FRAME_RING_SIZE = 4
DEFAULT_FPS = 15
DEFAULT_REQUEST_TIMEOUT = 5
DELIVERY_LOG_INTERVAL = 5.0
PEAK_COLOR = (255, 255, 255)
PEAK_FALL_PER_SEC = 9.0

THEMES = {
    "classic": [(0.0, (0.00, 0.78, 0.00)), (0.5, (1.00, 0.78, 0.00)), (1.0, (1.00, 0.12, 0.00))],
    "fire": [(0.0, (0.45, 0.00, 0.00)), (0.35, (1.00, 0.25, 0.00)), (0.7, (1.00, 0.65, 0.00)), (1.0, (1.00, 1.00, 0.75))],
    "ocean": [(0.0, (0.00, 0.10, 0.55)), (0.45, (0.00, 0.50, 0.95)), (0.8, (0.00, 0.90, 0.95)), (1.0, (0.80, 1.00, 1.00))],
    "aurora": [(0.0, (0.00, 0.35, 0.20)), (0.4, (0.00, 0.85, 0.50)), (0.7, (0.20, 0.95, 0.75)), (1.0, (0.65, 0.30, 0.95))],
}
THEME_NAMES = [*THEMES, "rainbow"]
STYLE_NAMES = ["bars", "mirror", "segments", "dots", "wave"]


# ---------------------------------------------------------------------------
# Audio analysis
# ---------------------------------------------------------------------------


def goertzel_magnitude(samples: Sequence[float], frequency: float) -> float:
    """Return the magnitude of one frequency bin."""
    n = len(samples)
    k = frequency * n / SAMPLE_RATE
    omega = 2.0 * math.pi * k / n
    cosine = math.cos(omega)
    coefficient = 2.0 * cosine
    q1 = 0.0
    q2 = 0.0
    for sample in samples:
        q0 = coefficient * q1 - q2 + sample
        q2 = q1
        q1 = q0
    real = q1 - q2 * cosine
    imaginary = q2 * math.sin(omega)
    return math.sqrt(real * real + imaginary * imaginary)


def band_magnitudes(samples: Sequence[float]) -> list[float]:
    return [goertzel_magnitude(samples, frequency) for frequency in BAND_FREQS]


def spectrum_heights(
    magnitudes: Sequence[float],
    running_max: list[float],
    previous: Sequence[int],
    peaks: list[float],
    peak_fall: float = 1.0,
) -> list[int]:
    """Convert magnitudes to smoothed 0..16 heights with peak caps."""
    heights: list[int] = []
    for index, magnitude in enumerate(magnitudes):
        running_max[index] = max(running_max[index] * 0.995, 1.0, magnitude)
        try:
            height = int(DISPLAY_HEIGHT * math.log1p(magnitude) / math.log1p(running_max[index]))
            decayed_height = int(previous[index] * 0.75)
        except (OverflowError, ValueError):
            height = 0
            decayed_height = 0
        height = max(0, min(DISPLAY_HEIGHT, max(height, decayed_height)))
        heights.append(height)
        peaks[index] = max(height, peaks[index] - peak_fall)
    return heights


# ---------------------------------------------------------------------------
# PNG and raster rendering
# ---------------------------------------------------------------------------


def _png_chunk(tag: bytes, data: bytes) -> bytes:
    payload = tag + data
    return (
        struct.pack(">I", len(data))
        + payload
        + struct.pack(">I", zlib.crc32(payload) & 0xFFFFFFFF)
    )


def encode_png(pixels: Sequence[tuple[int, int, int]]) -> bytes:
    """Encode a flat RGB buffer as a 72x16 RGBA PNG using the stdlib."""
    if len(pixels) != DISPLAY_WIDTH * DISPLAY_HEIGHT:
        raise ValueError("pixel buffer must contain exactly 72x16 pixels")
    raw = bytearray()
    for row in range(DISPLAY_HEIGHT):
        raw.append(0)
        start = row * DISPLAY_WIDTH
        for red, green, blue in pixels[start : start + DISPLAY_WIDTH]:
            raw.extend((red, green, blue, 255))
    return (
        b"\x89PNG\r\n\x1a\n"
        + _png_chunk(
            b"IHDR",
            struct.pack(">IIBBBBB", DISPLAY_WIDTH, DISPLAY_HEIGHT, 8, 6, 0, 0, 0),
        )
        + _png_chunk(b"IDAT", zlib.compress(bytes(raw), 6))
        + _png_chunk(b"IEND", b"")
    )


def _sample_theme(stops: Sequence[tuple[float, tuple[float, float, float]]], position: float) -> tuple[float, float, float]:
    position = max(0.0, min(1.0, position))
    previous = stops[0]
    for stop in stops:
        if position <= stop[0]:
            start_position, start_color = previous
            end_position, end_color = stop
            span = (end_position - start_position) or 1.0
            ratio = (position - start_position) / span
            return (
                start_color[0] + (end_color[0] - start_color[0]) * ratio,
                start_color[1] + (end_color[1] - start_color[1]) * ratio,
                start_color[2] + (end_color[2] - start_color[2]) * ratio,
            )
        previous = stop
    return stops[-1][1]


def _theme_color(band: int, position: float, theme: str) -> tuple[int, int, int]:
    position = max(0.0, min(1.0, position))
    if theme == "rainbow":
        red, green, blue = colorsys.hsv_to_rgb(
            band / max(1, BAND_COUNT),
            1.0,
            0.35 + 0.65 * position,
        )
    else:
        red, green, blue = _sample_theme(THEMES.get(theme, THEMES["classic"]), position)
    return (
        max(0, min(255, round(red * 255))),
        max(0, min(255, round(green * 255))),
        max(0, min(255, round(blue * 255))),
    )


def _px(pixels: list[tuple[int, int, int]], x: int, y: int, color: tuple[int, int, int]) -> None:
    if 0 <= x < DISPLAY_WIDTH and 0 <= y < DISPLAY_HEIGHT:
        pixels[y * DISPLAY_WIDTH + x] = color


def _col2(pixels: list[tuple[int, int, int]], x: int, y: int, color: tuple[int, int, int]) -> None:
    _px(pixels, x, y, color)
    _px(pixels, x + 1, y, color)


def _raster_bars(pixels: list[tuple[int, int, int]], heights: Sequence[int], peaks: Sequence[float], theme: str) -> None:
    for band, height in enumerate(heights):
        x = band * 3
        for y in range(DISPLAY_HEIGHT - height, DISPLAY_HEIGHT):
            _col2(pixels, x, y, _theme_color(band, (DISPLAY_HEIGHT - 1 - y) / (DISPLAY_HEIGHT - 1), theme))
        peak = math.floor(peaks[band])
        if peak > height and peak > 0:
            _col2(pixels, x, DISPLAY_HEIGHT - peak, PEAK_COLOR)


def _raster_mirror(pixels: list[tuple[int, int, int]], heights: Sequence[int], peaks: Sequence[float], theme: str) -> None:
    middle = DISPLAY_HEIGHT // 2
    for band, height in enumerate(heights):
        x = band * 3
        half = height // 2
        for y in range(middle - half, middle):
            position = (middle - y) / max(1, half) * (height / DISPLAY_HEIGHT)
            _col2(pixels, x, y, _theme_color(band, position, theme))
        for y in range(middle, middle + half):
            position = (y - middle + 1) / max(1, half) * (height / DISPLAY_HEIGHT)
            _col2(pixels, x, y, _theme_color(band, position, theme))
        peak_half = math.floor(peaks[band]) // 2
        if peak_half > half and peak_half > 0:
            _col2(pixels, x, middle - peak_half, PEAK_COLOR)
            _col2(pixels, x, middle + peak_half - 1, PEAK_COLOR)


SEGMENT_BLOCK = 2
SEGMENT_GAP = 1
SEGMENT_PITCH = SEGMENT_BLOCK + SEGMENT_GAP
SEGMENT_SLOTS = 6


def _segment_block(slot: int) -> tuple[int, int]:
    y = DISPLAY_HEIGHT - SEGMENT_BLOCK - slot * SEGMENT_PITCH
    height = SEGMENT_BLOCK
    if y < 0:
        height += y
        y = 0
    return y, height


def _raster_segments(pixels: list[tuple[int, int, int]], heights: Sequence[int], peaks: Sequence[float], theme: str) -> None:
    for band, height in enumerate(heights):
        x = band * 3
        lit = round(height / DISPLAY_HEIGHT * SEGMENT_SLOTS)
        for slot in range(lit):
            y, block_height = _segment_block(slot)
            if block_height <= 0:
                continue
            color = _theme_color(band, 1.0 - (y + block_height / 2.0) / DISPLAY_HEIGHT, theme)
            for row in range(y, y + block_height):
                _col2(pixels, x, row, color)
        peak_slot = round(peaks[band] / DISPLAY_HEIGHT * SEGMENT_SLOTS)
        if peak_slot > lit and peak_slot > 0:
            y, block_height = _segment_block(peak_slot - 1)
            for row in range(y, y + block_height):
                _col2(pixels, x, row, PEAK_COLOR)


def _raster_dots(pixels: list[tuple[int, int, int]], heights: Sequence[int], peaks: Sequence[float], theme: str) -> None:
    for band, height in enumerate(heights):
        x = band * 3
        if height > 0:
            dot_height = 2
            y = min(DISPLAY_HEIGHT - dot_height, DISPLAY_HEIGHT - height)
            color = _theme_color(band, height / DISPLAY_HEIGHT, theme)
            for row in range(y, y + dot_height):
                _col2(pixels, x, row, color)
        peak = math.floor(peaks[band])
        if peak > height and peak > 0:
            _col2(pixels, x, DISPLAY_HEIGHT - peak, PEAK_COLOR)


def _raster_wave(pixels: list[tuple[int, int, int]], heights: Sequence[int], peaks: Sequence[float], theme: str) -> None:
    tops = [DISPLAY_HEIGHT - max(1, height) for height in heights]
    for band, height in enumerate(heights):
        x = band * 3
        color = _theme_color(band, max(1, height) / DISPLAY_HEIGHT, theme)
        _col2(pixels, x, tops[band], color)
        if band < BAND_COUNT - 1:
            low, high = min(tops[band], tops[band + 1]), max(tops[band], tops[band + 1])
            for row in range(low, high + 1):
                _px(pixels, x + 2, row, color)


RASTERISERS = {
    "bars": _raster_bars,
    "mirror": _raster_mirror,
    "segments": _raster_segments,
    "dots": _raster_dots,
    "wave": _raster_wave,
}


def build_pixels(
    heights: Sequence[int],
    peaks: Sequence[float],
    theme: str = "classic",
    style: str = "bars",
) -> list[tuple[int, int, int]]:
    """Rasterize the selected style and theme into a full BUSY frame."""
    if len(heights) != BAND_COUNT or len(peaks) != BAND_COUNT:
        raise ValueError(f"expected {BAND_COUNT} bands")
    pixels = [(0, 0, 0)] * (DISPLAY_WIDTH * DISPLAY_HEIGHT)
    RASTERISERS.get(style, _raster_bars)(pixels, heights, peaks, theme)
    return pixels


# ---------------------------------------------------------------------------
# BUSY output
# ---------------------------------------------------------------------------


class BusyBarOutput:
    """Keep one HTTP client open while frames are being uploaded."""

    def __init__(self, host: str, token: str, *, request_timeout: int) -> None:
        try:
            from busylib import BusyBar, converter, types  # pyright: ignore[reportMissingImports]
        except ImportError as exc:
            raise RuntimeError(
                "Install dependencies first: python -m pip install -r requirements.txt"
            ) from exc
        self.client: Any = BusyBar(
            host,
            token=token,
            timeout=request_timeout,
            max_retries=0,
        )
        self.converter: Any = converter
        self.types: Any = types
        self.frame_number = 0
        self.initial_clear = True
        self._last_delivery_log = 0.0

    def close(self) -> None:
        try:
            self.client.display_clear(application_name=APP_NAME)
        except Exception as exc:
            logging.warning("BUSY spectrum display clear failed: %s", type(exc).__name__)
        try:
            self.client.close()
        except Exception as exc:
            logging.warning("BUSY spectrum client close failed: %s", type(exc).__name__)

    def _log_delivery_failure(self, exc: Exception) -> None:
        now = time.monotonic()
        if now - self._last_delivery_log < DELIVERY_LOG_INTERVAL:
            return
        status_code = getattr(exc, "status_code", None) or getattr(exc, "code", None)
        if status_code == 409:
            logging.warning("BUSY spectrum display conflict; retrying")
        else:
            logging.warning("BUSY spectrum display failed: %s", type(exc).__name__)
        self._last_delivery_log = now

    def show(self, pixels: Sequence[tuple[int, int, int]], *, display_timeout: int) -> bool:
        try:
            filename = f"spectrum-{self.frame_number % FRAME_RING_SIZE}.png"
            self.frame_number += 1
            stored_name, payload = self.converter.convert_for_storage(filename, encode_png(pixels))
            self.client.assets_upload(
                application_name=APP_NAME,
                filename=stored_name,
                data=payload,
            )
            self.client.display_draw(
                self.types.DisplayElements(
                    application_name=APP_NAME,
                    priority=100,
                    elements=[
                        self.types.ImageElement(
                            id="spectrum-frame",
                            type="image",
                            path=stored_name,
                            x=0,
                            y=0,
                            display=self.types.DisplayName.FRONT,
                            timeout=display_timeout,
                        )
                    ],
                ),
                clear_before_draw=self.initial_clear,
            )
        except Exception as exc:
            self._log_delivery_failure(exc)
            self.initial_clear = True
            return False
        self.initial_clear = False
        return True


# ---------------------------------------------------------------------------
# Capture and CLI
# ---------------------------------------------------------------------------


def positive_int(value: str) -> int:
    try:
        number = int(value)
    except ValueError as exc:
        raise argparse.ArgumentTypeError("must be an integer") from exc
    if number <= 0:
        raise argparse.ArgumentTypeError("must be greater than zero")
    return number


def capture_command(audio_format: str, device: str) -> list[str]:
    return [
        "ffmpeg",
        "-hide_banner",
        "-loglevel",
        "error",
        "-f",
        audio_format,
        "-i",
        device,
        "-ac",
        "1",
        "-ar",
        str(SAMPLE_RATE),
        "-f",
        "s16le",
        "-",
    ]


def start_capture(audio_format: str, device: str) -> subprocess.Popen[bytes]:
    if shutil.which("ffmpeg") is None:
        raise RuntimeError("ffmpeg is required for audio capture")
    return subprocess.Popen(
        capture_command(audio_format, device),
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )


def capture_error(process: subprocess.Popen[bytes]) -> str:
    if process.stderr is None:
        return ""
    try:
        return process.stderr.read().decode("utf-8", "replace").strip()
    except OSError:
        return ""


def demo_samples(frame: int) -> list[float]:
    frequencies = (110.0, 440.0 + frame * 4.0, 1760.0)
    return [
        sum(math.sin(2.0 * math.pi * frequency * (index + frame * CHUNK_SAMPLES) / SAMPLE_RATE)
            for frequency in frequencies)
        / len(frequencies)
        for index in range(CHUNK_SAMPLES)
    ]


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Display a Mixxx output spectrum on BUSY Bar.")
    parser.add_argument("host", help="BUSY Bar host or IP address")
    parser.add_argument("--audio-format", choices=("alsa", "pulse"), default="alsa")
    parser.add_argument("--audio-device", default="default", help="ffmpeg capture source")
    parser.add_argument("--fps", type=positive_int, default=DEFAULT_FPS)
    parser.add_argument("--style", choices=STYLE_NAMES, default="bars")
    parser.add_argument("--theme", choices=THEME_NAMES, default="classic")
    parser.add_argument("--request-timeout", type=positive_int, default=DEFAULT_REQUEST_TIMEOUT)
    parser.add_argument("--display-timeout", type=positive_int, default=3)
    parser.add_argument("--demo", action="store_true", help="use synthetic audio instead of ffmpeg")
    parser.add_argument("--once", action="store_true", help="draw one frame and exit")
    return parser.parse_args(argv)


def run(args: argparse.Namespace) -> int:
    token = os.environ.get("BUSY_API_TOKEN")
    if not token:
        print("BUSY_API_TOKEN is required", file=sys.stderr)
        return 2

    running_max = [1.0] * BAND_COUNT
    previous = [0] * BAND_COUNT
    peaks = [0.0] * BAND_COUNT
    peak_fall = max(0.3, PEAK_FALL_PER_SEC / max(1, args.fps))
    frame = 0
    interval = 1.0 / args.fps
    last_draw = 0.0
    output = BusyBarOutput(args.host, token, request_timeout=args.request_timeout)
    capture = None
    try:
        capture = None if args.demo else start_capture(args.audio_format, args.audio_device)
        while True:
            if args.demo:
                samples = demo_samples(frame)
            else:
                if capture is None or capture.stdout is None:
                    raise RuntimeError("audio capture is unavailable")
                raw = capture.stdout.read(CHUNK_SAMPLES * 2)
                if len(raw) != CHUNK_SAMPLES * 2:
                    detail = capture_error(capture)
                    suffix = f": {detail}" if detail else ""
                    raise RuntimeError(f"ffmpeg audio capture ended{suffix}")
                samples = [sample / 32768.0 for sample in struct.unpack(f"<{CHUNK_SAMPLES}h", raw)]

            magnitudes = band_magnitudes(samples)
            heights = spectrum_heights(magnitudes, running_max, previous, peaks, peak_fall)
            previous = heights
            now = time.monotonic()
            if now - last_draw >= interval:
                output.show(
                    build_pixels(heights, peaks, args.theme, args.style),
                    display_timeout=args.display_timeout,
                )
                last_draw = now
                if args.once:
                    return 0
            frame += 1
    finally:
        output.close()
        if capture is not None:
            capture.terminate()
            try:
                capture.wait(timeout=2)
            except subprocess.TimeoutExpired:
                capture.kill()
                capture.wait()


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
