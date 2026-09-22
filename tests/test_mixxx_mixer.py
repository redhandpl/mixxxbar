#!/usr/bin/env python3
"""Tests for the Mixxx-to-BUSY status protocol and display formatting."""

from __future__ import annotations

import unittest
from pathlib import Path
from unittest.mock import MagicMock

from mixxx_mixer import (
    BPM_X,
    DECK_ICON_RING_SEGMENTS,
    DECK_ICON_X,
    VOLUME_BAR_WIDTH,
    VOLUME_BAR_X,
    BusyBarDisplay,
    MidiOutputConnector,
    MixxxStatus,
    _midi_port_address,
    balance_marker_position,
    bpm_color,
    deck_icon_color,
    decode_sysex,
    display_lines,
    encode_sysex,
    format_remaining,
    level_fill_height,
    level_segments,
    next_animation_phase,
    remaining_color,
    update_status_from_midi_message,
)


class MixxxStatusTests(unittest.TestCase):
    def test_sysex_round_trip(self) -> None:
        status = MixxxStatus(124.6, 128.0, 215, 180, True, False, 1, 64, 127)

        self.assertEqual(decode_sysex(encode_sysex(status)), status)

    def test_invalid_sysex_is_ignored(self) -> None:
        self.assertIsNone(decode_sysex([0xF0, 0x7D, 0x42, 0x42, 0x01]))
        self.assertIsNone(
            decode_sysex([0x7D, 0x42, 0x42, 0x01] + [0, 0, 0, 0, 0, 0, 3, 0, 0])
        )
        self.assertIsNone(
            decode_sysex([0x7D, 0x42, 0x42, 0x01] + [0, 0, 0, 0, 2, 0, 0, 0, 0])
        )

    def test_virtual_midi_address_is_parsed(self) -> None:
        self.assertEqual(_midi_port_address("BUSYBAR Mixxx Status:BUSYBAR Mixxx Status 129:0"), "129:0")

    def test_level_control_change_updates_only_one_deck(self) -> None:
        status = MixxxStatus(124.6, 128.0, 215, 180, True, False, 1, 64, 127)
        message = type("MidiMessage", (), {
            "type": "control_change",
            "channel": 0,
            "control": 0x10,
            "value": 23,
        })()

        updated = update_status_from_midi_message(status, message)

        if updated is None:
            self.fail("level control change was ignored")
        self.assertEqual(updated.deck1_level, 23)
        self.assertEqual(updated.deck2_level, 127)

        volume_message = type("MidiMessage", (), {
            "type": "control_change",
            "channel": 0,
            "control": 0x12,
            "value": 19,
        })()
        updated = update_status_from_midi_message(updated, volume_message)
        if updated is None:
            self.fail("volume control change was ignored")
        self.assertEqual(updated.deck1_volume, 19)

        balance_message = type("MidiMessage", (), {
            "type": "control_change",
            "channel": 0,
            "control": 0x14,
            "value": 127,
        })()
        updated = update_status_from_midi_message(updated, balance_message)
        if updated is None:
            self.fail("crossfader control change was ignored")
        self.assertEqual(updated.crossfader, 127)

        main_message = type("MidiMessage", (), {
            "type": "control_change",
            "channel": 0,
            "control": 0x15,
            "value": 31,
        })()
        updated = update_status_from_midi_message(updated, main_message)
        if updated is None:
            self.fail("main level control change was ignored")
        self.assertEqual(updated.main_level, 31)

        full_status = update_status_from_midi_message(
            updated,
            type("MidiMessage", (), {"type": "sysex", "data": encode_sysex(status)})(),
        )
        if full_status is None:
            self.fail("full status frame was ignored")
        self.assertEqual(full_status.deck1_volume, 19)

    def test_display_includes_bpm_and_remaining_time(self) -> None:
        status = MixxxStatus(124.6, 128.0, 215, 180, True, False, 1, 64, 127)

        lines = display_lines(status)

        self.assertEqual(len(lines), 4)
        self.assertEqual(lines[0][0], "124.6")
        self.assertEqual(lines[1][0], "128.0")
        self.assertEqual(lines[2][0], "03:35")
        self.assertEqual(lines[3][0], "03:00")
        self.assertEqual(format_remaining(0), "00:00")
        self.assertEqual(remaining_color(30, True), "#FF0000FF")
        self.assertEqual(remaining_color(30, False), "#FFFFFFFF")
        self.assertEqual(remaining_color(31, True), "#FFFFFFFF")
        self.assertEqual(bpm_color(MixxxStatus(120.0, 120.15, 215, 180, True, False, 1, 64, 127)), "#00FFFFFF")
        self.assertEqual(bpm_color(MixxxStatus(120.0, 120.5, 215, 180, True, False, 1, 64, 127)), "#0080FFFF")
        self.assertEqual(bpm_color(MixxxStatus(120.0, 120.51, 215, 180, True, False, 1, 64, 127)), "#FF00FFFF")
        self.assertEqual(bpm_color(MixxxStatus(70.0, 140.0, 215, 180, True, False, 1, 64, 127)), "#00FFFFFF")
        self.assertEqual(bpm_color(MixxxStatus(70.0, 139.8, 215, 180, True, False, 1, 64, 127)), "#00FFFFFF")
        self.assertEqual(bpm_color(MixxxStatus(70.0, 139.4, 215, 180, True, False, 1, 64, 127)), "#0080FFFF")
        self.assertEqual(bpm_color(MixxxStatus(70.0, 138.0, 215, 180, True, False, 1, 64, 127)), "#FF00FFFF")
        self.assertEqual(VOLUME_BAR_X, (0, 69))
        self.assertEqual(VOLUME_BAR_WIDTH, 3)
        self.assertEqual(DECK_ICON_X, (5, 59))
        self.assertEqual(
            {(x + dx, y + dy) for x, y, width, height in DECK_ICON_RING_SEGMENTS for dx in range(width) for dy in range(height)},
            {
                (2, 0), (3, 0), (4, 0), (5, 0),
                (1, 1), (6, 1),
                (0, 2), (7, 2),
                (0, 3), (7, 3),
                (0, 4), (7, 4),
                (0, 5), (7, 5),
                (1, 6), (6, 6),
                (2, 7), (3, 7), (4, 7), (5, 7),
            },
        )
        self.assertEqual(BPM_X, (15, 39))
        self.assertEqual(level_fill_height(0), 0)
        self.assertEqual(level_fill_height(64), 8)
        self.assertEqual(level_fill_height(127), 16)
        self.assertEqual(balance_marker_position(0), 29)
        self.assertEqual(balance_marker_position(64), 35)
        self.assertEqual(balance_marker_position(127), 41)
        full_segments = {name: (y, height, color) for name, y, height, color in level_segments(127)}
        self.assertEqual(full_segments["green"], (8, 8, "#00FF00FF"))
        self.assertEqual(full_segments["yellow"], (3, 5, "#FFFF00FF"))
        self.assertEqual(full_segments["red"], (0, 3, "#FF0000FF"))
        self.assertEqual(deck_icon_color(8), "#606060FF")
        self.assertEqual(deck_icon_color(9), "#FFFFFFFF")
        self.assertEqual(deck_icon_color(127, False), "#606060FF")
        self.assertEqual(next_animation_phase(2, False), 2)
        self.assertEqual(next_animation_phase(2, True), 3)
        self.assertEqual(next_animation_phase(3, True), 0)

    def test_display_retries_after_busy_conflict(self) -> None:
        class BusyConflict(RuntimeError):
            status_code = 409

        conflict = BusyConflict("conflict")
        client = MagicMock()
        client.display_draw.side_effect = [conflict, None]
        display = BusyBarDisplay.__new__(BusyBarDisplay)
        display.clients = [("busy.local", client)]
        display.types = MagicMock()
        display._last_delivery_log = 0.0

        self.assertEqual(display._draw([], clear_before_draw=False), 1)
        self.assertEqual(display._draw([], clear_before_draw=False), 0)
        self.assertEqual(client.display_draw.call_count, 2)

    def test_display_close_clears_screen_before_closing_client(self) -> None:
        client = MagicMock()
        display = BusyBarDisplay.__new__(BusyBarDisplay)
        display.clients = [("busy.local", client)]

        display.close()

        client.display_clear.assert_called_once_with(application_name="mixxx_busybar")
        client.close.assert_called_once_with()

    def test_midi_connector_close_stops_worker(self) -> None:
        connector = MidiOutputConnector("BUSYBAR Mixxx Status")

        connector.close()

        self.assertTrue(connector.stop.is_set())

    def test_mapping_is_script_only_and_references_bridge_script(self) -> None:
        mapping = (Path(__file__).parents[1] / "mixxx_mapping" / "mixxx_busybar.midi.xml").read_text()

        self.assertIn("<MixxxMIDIPreset", mapping)
        self.assertIn('filename="mixxx_busybar.js"', mapping)
        self.assertNotIn("<control ", mapping)
        self.assertNotIn("<control>", mapping)


if __name__ == "__main__":
    unittest.main()
