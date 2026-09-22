# Feature Ideas

## Track Information Display

**Status:** Parked

### Goal

Add a third full-screen display mode to the unified `mixxx_display.py` application:

```text
status → spectrum → track information → status
```

The physical BUSY Bar `START/PRESS` button selects the next mode. No timed rotation is used.

The track information screen should show:

```text
Artist Name
Track Title
```

Long artist and title values should use the BUSY Bar's text scrolling support.

### Current Constraint

Mixxx is not currently configured for live broadcasting. The Mixxx controller JavaScript API used by this project exposes numeric controls, but it does not provide a direct, reliable artist/title text API for the active deck.

The existing MIDI status path already carries `active_deck`, but it does not carry track text.

### Proposed Data Source

Configure Mixxx live broadcasting through Icecast or Shoutcast and read the current metadata from the configured server.

Required validation:

- the server exposes current artist/title metadata;
- metadata updates when the active track changes;
- the metadata identifies the track currently on air;
- the metadata is sufficient to distinguish the active deck when both decks are loaded.

If the broadcast metadata does not expose the active deck, a local Mixxx exporter or deeper Mixxx integration is required.

### Display Contract

- Mode order: status → spectrum → track information.
- Toggle input: BUSY Bar `START/PRESS` event.
- Line 1: artist.
- Line 2: title.
- Long values: horizontal scrolling.
- Empty metadata: show a neutral fallback such as `NO TRACK`.
- Mode changes clear the previous full-screen application state once.

### Dependencies

- Mixxx live broadcast configuration;
- Icecast/Shoutcast metadata endpoint;
- metadata polling interval and failure handling;
- active-deck semantics for the selected metadata source.

No credentials or stream secrets belong in the repository.

### Acceptance Criteria

- Given Mixxx is broadcasting and a track is active, when the physical button selects track mode, then the artist appears on line 1 and the title appears on line 2.
- Given either text is longer than the available width, when track mode is active, then the text scrolls without clipping the other line.
- Given the metadata endpoint is unavailable, when track mode is active, then the screen shows a neutral fallback and the other modes remain usable.
- Given the physical button is pressed, when the current mode is track mode, then the next mode is status mode.
- Given both decks contain tracks, when the active deck changes, then the displayed metadata follows the selected active/on-air deck.

### Deferred Decisions

- Icecast versus Shoutcast metadata format.
- Polling versus a metadata push mechanism.
- Whether track mode uses the broadcast/on-air track or a deck-specific local exporter.
- Exact empty-state and color treatment.
