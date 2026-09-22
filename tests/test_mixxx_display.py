#!/usr/bin/env python3
"""Deterministic tests for physical-switch mode selection."""

from __future__ import annotations

import io
import os
import unittest
from unittest.mock import MagicMock, patch

from mixxx_display import (
    SWITCH_MODE_SPECTRUM,
    SWITCH_MODE_STATUS,
    DisplayMode,
    demo_status,
    parse_args,
    physical_toggle_event,
    run,
    run_demo,
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

        display_class.return_value.show.assert_called_once_with(demo_status(), blink_red=False)
        display_class.return_value.close.assert_called_once_with()
        output_class.return_value.close.assert_called_once_with()
        audio_class.assert_not_called()
        midi_input.assert_not_called()

    def test_demo_status_animates_runtime_values(self) -> None:
        first = demo_status(0, 0)
        started = demo_status(0, 15)
        aligned = demo_status(0, 19)
        on_deck_b = demo_status(0, 25)
        reloaded = demo_status(0, 30)
        retuned = demo_status(0, 34)

        self.assertFalse(first.deck2_playing)
        self.assertTrue(started.deck2_playing)
        self.assertNotEqual(started.deck2_level, on_deck_b.deck2_level)
        self.assertEqual(first.deck1_bpm, 125.0)
        self.assertEqual(first.deck2_bpm, 127.0)
        self.assertEqual(first.deck1_remaining_seconds, 45)
        self.assertEqual(first.deck2_remaining_seconds, 45)
        self.assertEqual(started.deck1_remaining_seconds, 30)
        self.assertEqual(started.deck2_remaining_seconds, 45)
        self.assertEqual(demo_status(0, 16).deck2_remaining_seconds, 44)
        self.assertEqual(started.deck2_bpm, 127.0)
        self.assertEqual(aligned.deck1_bpm, 125.0)
        self.assertEqual(aligned.deck2_bpm, 125.0)
        self.assertEqual(aligned.crossfader, 0)
        self.assertEqual(on_deck_b.crossfader, 127)
        self.assertEqual(on_deck_b.deck1_remaining_seconds, 20)
        self.assertEqual(reloaded.deck1_remaining_seconds, 50)
        self.assertEqual(reloaded.deck1_bpm, 123.0)
        self.assertFalse(reloaded.deck2_playing)
        self.assertEqual(reloaded.deck2_remaining_seconds, 45)
        self.assertEqual(reloaded.deck2_bpm, 127.0)
        self.assertEqual(reloaded.crossfader, 127)
        self.assertEqual(retuned.deck1_remaining_seconds, 46)
        self.assertEqual(retuned.deck1_bpm, 125.0)
        self.assertEqual(retuned.deck2_remaining_seconds, 45)
        self.assertEqual(retuned.deck2_bpm, 127.0)
        self.assertLess(retuned.crossfader, reloaded.crossfader)

    def test_demo_warning_blinks_after_thirty_seconds(self) -> None:
        args = parse_args(["--host", "busy.local", "--demo", "--display-interval", "15"])
        display = MagicMock()
        display.show.side_effect = [None, None, None, StopIteration]

        with patch("mixxx_display.time.sleep"), self.assertRaises(StopIteration):
            run_demo(args, display)

        display.show.assert_any_call(demo_status(0, 0), blink_red=False)
        display.show.assert_any_call(demo_status(1, 15), blink_red=True)
        display.show.assert_any_call(demo_status(2, 30), blink_red=False)

    def test_demo_resets_display_state_at_cycle_boundary(self) -> None:
        args = parse_args(["--host", "busy.local", "--demo", "--display-interval", "20"])
        display = MagicMock()
        display.animation_phases = [7, 7]
        display.initial_clear = False
        display.show.side_effect = [None, None, StopIteration]

        with patch("mixxx_display.time.sleep"), self.assertRaises(StopIteration):
            run_demo(args, display)

        self.assertEqual(display.animation_phases, [0, 0])
        self.assertTrue(display.initial_clear)

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
