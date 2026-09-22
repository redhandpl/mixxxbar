#!/usr/bin/env python3
"""Deterministic tests for the Mixxx spectrum renderer."""

from __future__ import annotations

import unittest
from unittest.mock import MagicMock, patch

from mixxx_spectrum import (
    BAND_COUNT,
    BusyBarOutput,
    DISPLAY_HEIGHT,
    DISPLAY_WIDTH,
    STYLE_NAMES,
    THEME_NAMES,
    build_pixels,
    capture_command,
    start_capture,
    encode_png,
    spectrum_heights,
)


class SpectrumTests(unittest.TestCase):
    def test_capture_command_targets_selected_linux_audio_source(self) -> None:
        command = capture_command("alsa", "hw:Loopback,1,0")

        self.assertIn("-f", command)
        self.assertIn("alsa", command)
        self.assertIn("hw:Loopback,1,0", command)
        self.assertIn("s16le", command)

    def test_empty_spectrum_has_no_peak_pixels(self) -> None:
        pixels = build_pixels([0] * BAND_COUNT, [0.0] * BAND_COUNT)

        self.assertEqual(len(pixels), DISPLAY_WIDTH * DISPLAY_HEIGHT)
        self.assertEqual(set(pixels), {(0, 0, 0)})

    def test_full_spectrum_renders_peak_and_bounded_heights(self) -> None:
        pixels = build_pixels([DISPLAY_HEIGHT - 4] * BAND_COUNT, [DISPLAY_HEIGHT] * BAND_COUNT)
        running_max = [1.0] * BAND_COUNT
        previous = [0] * BAND_COUNT
        peaks = [0.0] * BAND_COUNT

        heights = spectrum_heights([100.0] * BAND_COUNT, running_max, previous, peaks)

        self.assertEqual(len(heights), BAND_COUNT)
        self.assertTrue(all(0 <= height <= DISPLAY_HEIGHT for height in heights))
        self.assertIn((255, 255, 255), pixels)
        self.assertTrue(encode_png(pixels).startswith(b"\x89PNG\r\n\x1a\n"))

    def test_output_retries_after_busy_conflict(self) -> None:
        class BusyConflict(RuntimeError):
            status_code = 409

        conflict = BusyConflict("conflict")
        output = BusyBarOutput.__new__(BusyBarOutput)
        output.client = MagicMock()
        output.client.assets_upload.side_effect = [conflict, None]
        output.converter = MagicMock()
        output.converter.convert_for_storage.return_value = ("frame.png", b"png")
        output.types = MagicMock()
        output.frame_number = 0
        output.initial_clear = True
        output._last_delivery_log = 0.0
        pixels = [(0, 0, 0)] * (DISPLAY_WIDTH * DISPLAY_HEIGHT)

        self.assertFalse(output.show(pixels, display_timeout=3))
        self.assertTrue(output.initial_clear)
        self.assertTrue(output.show(pixels, display_timeout=3))
        self.assertFalse(output.initial_clear)

    def test_output_close_clears_screen_before_closing_client(self) -> None:
        output = BusyBarOutput.__new__(BusyBarOutput)
        output.client = MagicMock()

        output.close()

        output.client.display_clear.assert_called_once_with(application_name="mixxx_spectrum")
        output.client.close.assert_called_once_with()

    def test_missing_ffmpeg_has_a_readable_error(self) -> None:
        with patch("mixxx_spectrum.shutil.which", return_value=None):
            with self.assertRaisesRegex(RuntimeError, "ffmpeg is required for audio capture"):
                start_capture("alsa", "default")

    def test_all_styles_and_themes_render_a_frame(self) -> None:
        for style in STYLE_NAMES:
            for theme in THEME_NAMES:
                pixels = build_pixels(
                    [DISPLAY_HEIGHT // 2] * BAND_COUNT,
                    [0.0] * BAND_COUNT,
                    theme,
                    style,
                )
                self.assertEqual(len(pixels), DISPLAY_WIDTH * DISPLAY_HEIGHT)


if __name__ == "__main__":
    unittest.main()
