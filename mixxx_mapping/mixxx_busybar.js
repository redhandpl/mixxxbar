/*
 * Mixxx 2.5 script-only mapping for BUSYBAR Mixxx Status.
 *
 * The mapping sends status SysEx frames every 200 ms and level CC messages
 * every 40 ms. The Python bridge creates the port as a Linux virtual MIDI
 * input and forwards the decoded status to BUSY Bars over the network.
 */

var BusyBarMixxx = {
    statusTimerId: null,
    levelTimerId: null,
    previousPlaying: [false, false],
    activeDeck: 0,

    init: function () {
        this.previousPlaying = [false, false];
        this.activeDeck = 0;
        this.statusTimerId = engine.beginTimer(200, () => {
            BusyBarMixxx.sendStatus();
        });
        this.levelTimerId = engine.beginTimer(40, () => {
            BusyBarMixxx.sendLevels();
        });
        this.sendStatus();
        this.sendLevels();
    },

    shutdown: function () {
        if (this.statusTimerId !== null) {
            engine.stopTimer(this.statusTimerId);
            this.statusTimerId = null;
        }
        if (this.levelTimerId !== null) {
            engine.stopTimer(this.levelTimerId);
            this.levelTimerId = null;
        }
    },

    value: (deck, control) => {
        var value = Number(engine.getValue(`[Channel${deck}]`, control));
        return Number.isNaN(value) ? 0 : value;
    },

    masterValue: (control) => {
        var value = Number(engine.getValue("[Master]", control));
        return Number.isNaN(value) ? 0 : value;
    },

    mainValue: (control) => {
        var value = Number(engine.getValue("[Main]", control));
        return Number.isNaN(value) ? 0 : value;
    },

    clamp: (value, minimum, maximum) =>
        Math.max(minimum, Math.min(maximum, value)),

    bpmBytes: function (value) {
        var bpm = this.clamp(Math.round(Math.max(0, value) * 10), 0, 0x3fff);
        return [Math.floor(bpm / 128), bpm % 128];
    },

    remainingBytes: function (deck) {
        var duration = Math.max(0, this.value(deck, "duration"));
        var position = this.clamp(this.value(deck, "playposition"), 0, 1);
        var remaining = this.clamp(
            Math.round(duration * (1 - position)),
            0,
            0x3fff,
        );
        return [Math.floor(remaining / 128), remaining % 128];
    },

    levelByte: function (value) {
        return this.clamp(Math.round(Math.max(0, value) * 127), 0, 127);
    },

    crossfaderByte: function () {
        var crossfader = this.clamp(this.masterValue("crossfader"), -1, 1);
        return this.clamp(Math.round((crossfader + 1) * 63.5), 0, 127);
    },

    updateActiveDeck: function () {
        var playing1 = this.value(1, "play_indicator") > 0.5;
        var playing2 = this.value(2, "play_indicator") > 0.5;
        if (!playing1 && !playing2) {
            this.activeDeck = 0;
        } else if (playing1 && !playing2) {
            this.activeDeck = 1;
        } else if (!playing1 && playing2) {
            this.activeDeck = 2;
        } else if (playing1 && !this.previousPlaying[0]) {
            this.activeDeck = 1;
        } else if (playing2 && !this.previousPlaying[1]) {
            this.activeDeck = 2;
        } else if (this.activeDeck === 0) {
            this.activeDeck = 1;
        }
        this.previousPlaying = [playing1, playing2];
        return [playing1, playing2];
    },

    sendLevels: function () {
        midi.sendShortMsg(
            0xb0,
            0x10,
            this.levelByte(this.value(1, "vu_meter")),
        );
        midi.sendShortMsg(
            0xb0,
            0x11,
            this.levelByte(this.value(2, "vu_meter")),
        );
        midi.sendShortMsg(0xb0, 0x12, this.levelByte(this.value(1, "volume")));
        midi.sendShortMsg(0xb0, 0x13, this.levelByte(this.value(2, "volume")));
        midi.sendShortMsg(0xb0, 0x14, this.crossfaderByte());
        midi.sendShortMsg(
            0xb0,
            0x15,
            this.levelByte(this.mainValue("vu_meter")),
        );
    },

    sendStatus: function () {
        var playing = this.updateActiveDeck();
        var bpm1 = this.bpmBytes(this.value(1, "bpm"));
        var bpm2 = this.bpmBytes(this.value(2, "bpm"));
        var remaining1 = this.remainingBytes(1);
        var remaining2 = this.remainingBytes(2);
        var frame = [
            0xf0,
            0x7d,
            0x42,
            0x42,
            0x01,
            bpm1[0],
            bpm1[1],
            bpm2[0],
            bpm2[1],
            playing[0] ? 1 : 0,
            playing[1] ? 1 : 0,
            this.activeDeck,
            this.levelByte(this.value(1, "vu_meter")),
            this.levelByte(this.value(2, "vu_meter")),
            remaining1[0],
            remaining1[1],
            remaining2[0],
            remaining2[1],
            0xf7,
        ];
        midi.sendSysexMsg(frame, frame.length);
    },
};

// Mixxx resolves the function prefix from the mapping XML at runtime.
void BusyBarMixxx;
