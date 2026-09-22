#!/usr/bin/env python3
"""Deterministic tests for physical-switch mode selection."""

from __future__ import annotations

import io
import os
import unittest
from unittest.mock import patch

from mixxx_display import (
    SWITCH_MODE_SPECTRUM,
    SWITCH_MODE_STATUS,
    DisplayMode,
    demo_status,
    parse_args,
    physical_toggle_event,
    run,
    switch_position,
)


class SwitchModeTests(unittest.TestCase):
    def test_switch_event_position_is_extracted(self) -> None:
        state = {"updates": [{"input": {"switch_event": {"position": 3}}}]}

        self.assertEqual(switch_position(state), 3)
        self.assertIsNone(switch_position({"updates": []}))

    def test_start_button_press_is_a_toggle_event(self) -> None:
        state = {"updates": [{"input": {"button_event": {"button": "START", "action": "PRESS"}}}]}

        self.assertEqual(physical_toggle_event(state), "button:START")
        self.assertIsNone(physical_toggle_event({"updates": []}))

    def test_spectrum_style_and_theme_arguments(self) -> None:
        args = parse_args(["--style", "segments", "--theme", "fire", "busy.local"])

        self.assertEqual(args.style, "segments")
        self.assertEqual(args.theme, "fire")

    def test_demo_arguments_default_to_gallery_host(self) -> None:
        args = parse_args(["--demo", "--once"])

        self.assertEqual(args.host, "10.0.4.20")
        self.assertTrue(args.demo)
        self.assertTrue(args.once)

    def test_demo_once_draws_without_midi_or_audio(self) -> None:
        args = parse_args(["--host", "busy.local", "--demo", "--once"])

        with (
            patch("mixxx_display.BusyBarDisplay") as display_class,
            patch("mixxx_display.BusyBarOutput") as output_class,
            patch("mixxx_display.AudioReader") as audio_class,
            patch("mixxx_display.open_midi_input") as midi_input,
        ):
            self.assertEqual(run(args), 0)

        display_class.return_value.show.assert_called_once_with(demo_status())
        display_class.return_value.close.assert_called_once_with()
        output_class.return_value.close.assert_called_once_with()
        audio_class.assert_not_called()
        midi_input.assert_not_called()

    def test_live_mode_requires_token_without_echoing_value(self) -> None:
        with patch.dict(os.environ, {}, clear=True), patch("sys.stderr", new_callable=io.StringIO) as stderr:
            self.assertEqual(run(parse_args(["--host", "busy.local"])), 2)

        self.assertEqual(stderr.getvalue().strip(), "BUSY_API_TOKEN is required")

    def test_mode_toggles_without_time_rotation(self) -> None:
        mode = DisplayMode()

        self.assertEqual(mode.get(), SWITCH_MODE_STATUS)
        self.assertEqual(mode.toggle(), SWITCH_MODE_SPECTRUM)
        self.assertEqual(mode.toggle(), SWITCH_MODE_STATUS)


if __name__ == "__main__":
    unittest.main()
