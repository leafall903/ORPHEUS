"""Roster-selected conversation playback for Junkenstein's authored 0CBD cues."""

import json
import math
from dataclasses import dataclass
from functools import cache
from random import Random
from weakref import WeakKeyDictionary

from ow174.game import voicelines
from ow174.game.script import expr, runtime, voice_stimulus

STIMULUS_NODES = {0x086B: 21, 0x086C: 22, 0x086D: 23, 0x0852: 24, 0x0854: 25}
# Unconditional mode-owner cues in the authored 013B.05F narrator set.
MODE_LINES = {
    0x08F6: (0x0C8C, 228, ((0xA45B, 551872),)),  # Endless main objective held.
    0x0CB8: (0x0C8C, 271, ((0x8CE0, 707534),)),  # The castle fell.
    0x0E51: (0x0C8C, 315, ((0xE3E4, 406511),)),
    0x0E5A: (0x0C8C, 315, ((0xE39F, 245859),)),  # Mystery Swap
    0x0E5B: (0x0C8C, 315, ((0xE397, 385645),)),
    0x0E5D: (0x0C8C, 314, ((0xE396, 380238),)),
    0x0E5F: (0x0C8C, 315, ((0xE399, 411866),)),
    0x0E60: (0x0CB3, 190, ((0xE39E, 368467),)),  # Shocking Surprise death cue
    0x06D7: (0x1063, 29, ((0x8CB4, 261355), (0x8CB5, 192361), (0x8CB6, 273493))),
    0x08E6: (0x1063, 42, ((0xA3FB, 389216), (0xA461, 450816), (0xA462, 346304))),
    0x08E7: (0x1063, 35, ((0xA42E, 329408), (0xA42F, 242752))),
    0x08E8: (0x1063, 33, ((0x8CC1, 328635), (0xA42C, 362880), (0xA42D, 293568))),
    0x08EA: (0x1063, 39, ((0x8BE1, 257530), (0x8CDB, 261092), (0xA464, 281408),
                       (0xA465, 287296))),
    0x08EB: (0x1062, 176, ((0xA417, 522112),)),
    0x08EC: (0x1062, 174, ((0xA418, 488128),)),
    0x08ED: (0x1062, 175, ((0xA419, 483464),)),
    0x091A: (0x1063, 30, ((0xA5E8, 148544), (0xA5E9, 106944), (0xA5EA, 125504),
                       (0xA5EB, 171968), (0xA5EC, 125248))),
    0x091C: (0x1063, 31, ((0xA5F2, 158272), (0xA5F3, 104896), (0xA5F4, 114496),
                       (0xA5F5, 124736), (0xA5F6, 105392))),
    0x091F: (0x1063, 34, ((0xA5FC, 96832), (0xA5FD, 191424), (0xA5FE, 147392),
                       (0xA5FF, 153792), (0xA600, 127040))),
    0x0920: (0x1063, 37, ((0xA606, 109856), (0xA607, 70656), (0xA608, 156544),
                       (0xA609, 102144), (0xA60A, 103232))),
    0x0922: (0x1063, 40, ((0xA610, 129600), (0xA611, 124032), (0xA612, 93504),
                       (0xA613, 119552), (0xA614, 113088))),
}
MODE_HERO_LINES = {
    0x0CB7: (221, {
        0x004: (0x8CE1, 630603), 0x040: (0x8CE2, 632515), 0x002: (0x8CE3, 589970),
        0x065: (0x8CE4, 810400), 0x016: (0xA3EE, 603456), None: (0x8CF1, 393805),
    }),
    0x0924: (241, {
        0x06E: (0xA3E6, 591680), 0x005: (0xA3E7, 528384), 0x042: (0xA3E8, 551808),
        0x13B: (0xA3E9, 690912), 0x00A: (0xA3EA, 561694), 0x020: (0xA3EB, 544320),
        0x006: (0xA3EC, 676736), 0x029: (0xA3ED, 686656), 0x195: (0xB912, 564566),
        0x003: (0xB913, 420515), 0x221: (0xE19D, 417593), 0x200: (0xE19E, 569093),
    }),
}
GATE_CUES = {0x0EE00000000006C7: 50, 0x0EE00000000006B8: 51, 0x0EE00000000006B9: 52}
# Authored 013B.05F encounter narrator lines and their 104319 enUS samples at 48 kHz.
GATE_LINES = {
    0x0EE00000000006C7: ((0x8C71, 420716, None), (0x8CE8, 283980, 0x02E0000000000176)),
    0x0EE00000000006B8: ((0x885F, 194670, None), (0x8860, 159974, None), (0x8870, 187612, None),
                        (0x8871, 181610, None), (0x8C80, 232663, None)),
    0x0EE00000000006B9: ((0x89C2, 152851, None), (0x8C81, 299349, None), (0x8C82, 208786, None),
                        (0x8CE9, 224529, None)),
}


@dataclass(frozen=True)
class Line:
    guid: int
    hero: int
    duration: float
    conditional: bool


@cache
def load(path):
    data = json.loads(path.read_text(encoding='utf-8'))
    if (data['format_version'], data['build'], data['locale']) != (1, 104319, 'enUS'):
        raise ValueError('Junkenstein conversations require the 104319 enUS export')
    lines = {}
    for row in data['lines']:
        media, = row['media']
        key = row['line']
        index, resource_type = key.split('.')
        if resource_type != '06F' or key in lines or media['samples'] <= 0 or media['rate'] != 48000:
            raise ValueError('invalid Junkenstein conversation line')
        lines[key] = Line(0x0760000000000000 | int(index, 16), int(row['hero'], 16),
                          media['samples'] / media['rate'], row['criteria'] is not None)

    conversations = {stimulus: [] for stimulus in STIMULUS_NODES}
    for row in data['conversations']:
        parts = {}
        for entry in row['Lines']:
            key, step = entry.split(':')
            parts.setdefault(int(step), []).append(lines[key])
        weight = row['Weight']
        if (not parts or sorted(parts) != list(range(1, len(parts) + 1)) or row['Criteria'] is not None
                or not math.isfinite(weight) or weight <= 0):
            raise ValueError('invalid Junkenstein conversation sequence')
        conversations[row['Stimulus']].append((tuple(tuple(parts[i]) for i in sorted(parts)), weight))
    if any(not options for options in conversations.values()):
        raise ValueError('Junkenstein conversation stimulus is missing')
    return {cue: tuple(options) for cue, options in conversations.items()}


class Playback:
    def __init__(self, match, conversations, seed):
        self.match = match
        self.conversations = conversations
        self.rng = Random(seed)
        self.active = WeakKeyDictionary()
        self.durations = {line.guid & 0xFFFFFFFF: line.duration
                          for options in conversations.values() for parts, _ in options
                          for choices in parts for line in choices}

    def context(self, component):
        manager = voice_stimulus.Manager(load=self.stimulus, allow=lambda _: True, submit=self.submit,
                                        duration=self.duration, part=self.part)
        return voice_stimulus.Context(component, clock=lambda: component.now / 1000, lookup=lambda: manager)

    def stimulus(self, guid):
        cue = guid & 0xFFFFFFFFFFFF
        if guid >> 48 == 0x0EE0 and (cue in self.conversations or cue in MODE_LINES or cue in MODE_HERO_LINES):
            return voice_stimulus.Stimulus(guid, playable=True, mode=True)
        return NotImplemented

    @staticmethod
    def _viewer(player):
        return (player.spawned and not player.gone and player.has_body and player.team == 0
                and player.client is not None)

    def _ready(self, player):
        return self._viewer(player) and not self.match.combat.is_dead(player)

    def submit(self, state, stimulus, targets):
        cue = stimulus.guid & 0xFFFFFFFFFFFF
        if cue in MODE_LINES or cue in MODE_HERO_LINES:
            by_hero = MODE_HERO_LINES.get(cue)
            graph_index, position = (0x0C8C, by_hero[0]) if by_hero else MODE_LINES[cue][:2]
            mode = runtime.GAME_MODE_ENTITY
            if (state.instance.graph.index, state.node.pos, targets) != (graph_index, position, (mode,)):
                return NotImplemented
            world = state.instance.component.world
            if world.voice_set(mode) != 0x07A000000000013B:
                return 0
            if by_hero:
                selected = state.evaluate(state.node.field('m_8C8C5285'), watch=False)
                if isinstance(selected, expr.Entity):
                    binding = world.entities.get(selected.id)
                    hero = binding.hero if binding is not None else None
                else:
                    hero = selected.guid if isinstance(selected, expr.Asset) else None
                options = by_hero[1]
                index = hero & 0xFFFFFFFFFFFF if hero is not None and hero >> 48 == 0x02E0 else None
                selected_line = options.get(index, options.get(None))
                if selected_line is None:
                    return 0
                line, _ = selected_line
            else:
                lines = MODE_LINES[cue][2]
                line, _ = self.rng.choice(lines) if len(lines) > 1 else lines[0]
            for player in self.match.players:
                if self._viewer(player) and player.mode_script.delivered:
                    voicelines.queue_voice_notice(player, mode, stimulus.guid, 0x0760000000000000 | line,
                                                  anchor=None)
            return line
        if state.instance.graph.index != 0x0CBD or state.node.pos != STIMULUS_NODES[cue] or targets:
            return NotImplemented
        speakers = {}
        for player in self.match.players:
            if self._ready(player):
                speakers.setdefault(player.hero.guid, player)
        eligible = []
        for parts, weight in self.conversations[cue]:
            # Conditional variants use a separate native hero criterion.
            options = tuple(tuple(line for line in choices if line.hero in speakers and not line.conditional)
                            for choices in parts)
            if all(options):
                eligible.append((options, weight))
        if not eligible:
            return 0
        options, _ = self.rng.choices(eligible, weights=[weight for _, weight in eligible])[0]
        lines = tuple(self.rng.choice(choices) for choices in options)
        self.active[state] = (stimulus.guid, tuple((line, speakers[line.hero], speakers[line.hero].body)
                                                  for line in lines))
        if not self.part(state, 1):
            return 0
        return lines[0].guid & 0xFFFFFFFF

    def duration(self, handle, guid, mode):
        cue = guid & 0xFFFFFFFFFFFF
        if mode:
            narration = MODE_LINES.get(cue)
            conditional = MODE_HERO_LINES.get(cue)
            lines = conditional[1].values() if conditional else narration[2] if narration else ()
            for line, samples in lines:
                if line == handle:
                    return samples / 48000
        return self.durations.get(handle, NotImplemented)

    def part(self, state, number):
        playback = self.active.get(state)
        if playback is None:
            return 0.0
        stimulus, lines = playback
        if not 0 < number <= len(lines):
            self.active.pop(state, None)
            return 0.0
        line, speaker, body = lines[number - 1]
        if speaker.body != body or speaker.hero.guid != line.hero or not self._ready(speaker):
            self.active.pop(state, None)
            return 0.0
        for viewer in self.match.players:
            if self._ready(viewer):
                self.match.ensure_remote_body(viewer, speaker)
                voicelines.queue_voice_notice(viewer, body, stimulus, line.guid, anchor=body)
        return line.duration


def bind_gate_voice(match, component, encounter_voice_set):
    world = component.world
    mode = runtime.GAME_MODE_ENTITY
    rng = Random(component.entity)

    def load(guid):
        if guid in GATE_CUES:
            return voice_stimulus.Stimulus(guid, playable=True, mode=True)
        return NotImplemented

    def submit(state, stimulus, targets):
        if (state.instance.graph.index != 0x0CA7 or state.node.pos != GATE_CUES[stimulus.guid]
                or targets != (mode,)):
            return NotImplemented
        attacker_id = expr.to_entity(state.evaluate(state.node.field('m_8C8C5285'), watch=False))
        attacker = world.entities.get(attacker_id)
        hero = attacker.hero if attacker is not None else None
        choices = [line for line, _, required in GATE_LINES[stimulus.guid] if required in (None, hero)]
        line = rng.choice(choices)
        for player in match.players:
            component.consume_work('gate voice recipient')
            if player.spawned and not player.gone and player.has_body and player.team == 0:
                voicelines.queue_voice_notice(player, mode, stimulus.guid, 0x0760000000000000 | line,
                                              anchor=component.entity)
        return line

    def duration(handle, guid, mode_voice):
        if mode_voice:
            for line, samples, _ in GATE_LINES.get(guid, ()):
                if handle == line:
                    return samples / 48000
        return NotImplemented

    manager = voice_stimulus.Manager(load=load, allow=lambda _: world.voice_set(mode) == encounter_voice_set,
                                    submit=submit, duration=duration, part=lambda *_: 0.0)
    world.set_voice_context(component.entity, voice_stimulus.Context(
        component, clock=lambda: component.now / 1000, lookup=lambda: manager))
