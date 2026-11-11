"""Host services for the authored Junkenstein enemies."""

import math
from dataclasses import dataclass
from functools import partial
from random import Random

from ow174.game import bots, combat, content, npc, npc_navigation, pools, projectiles, voicelines
from ow174.game import world as wire
from ow174.game.commands import Command
from ow174.game.match import TEAM_BIT, _rotation
from ow174.game.mover import SPEED, Mover, mover_data
from ow174.game.script import entity_control, entity_team, expr, graph, nodes, runtime, scopes, voice_stimulus
from ow174.game.script.driver import BODY_SCALE_OVERRIDE, create_body_graphs
from ow174.game.script.modifiers import NumericModifiers

ZOMNIC = 0x02E0000000000175
ZOMBARDIER = 0x02E0000000000177
SHOCK_TIRE = 0x02E0000000000176
REAPER = 0x02E0000000000002
MERCY = 0x02E0000000000004
SYMMETRA = 0x02E0000000000016
ROADHOG = 0x02E0000000000040
JUNKRAT = 0x02E0000000000065
BOSS_PRIMARY_FIRE = 20
BOSS_PRIMARY_IDENTIFIER = 0x0D8000000000023A
REAPER_FIRE_RANGE = 20.0  # End of the authored shotgun damage falloff.
# Reach, weapon graph, and primary volley. Roadhog's reach is from 0BC6 node 12.
BOSS_PRIMARY = {
    REAPER: (REAPER_FIRE_RANGE, 0x0019, 10),
    ROADHOG: (8.749, 0x01C6, 26),
    JUNKRAT: (100.0, 0x022B, 31),  # 022B volley range. Aim also checks ballistic reach.
}
ROADHOG_SECONDARY_RANGE = (8.75, 19.75)  # 0BC6 node 14.
ROADHOG_SECONDARY_FIRE = 29
ROADHOG_SECONDARY_VOLLEY = 29
ROADHOG_HOOK_FIRE = 35  # Controller 1959's fire control 1953.
ROADHOG_HOOK_RANGE = 20.0  # 0BC6 node 25.
REAPER_BALCONY_SPAWN = (71.52854, 18.48874, -104.94691)
REAPER_BALCONY_EXIT_YAW = 3591  # Collision-verified walking exit toward the road.
GATE_REACH = {ZOMNIC: 3.0, SHOCK_TIRE: 5.0}
ZOMNIC_BODY = 0x04000000000016BE
BOSSES = frozenset((REAPER, MERCY, SYMMETRA, ROADHOG, JUNKRAT))
ENCOUNTER_VOICE_SET = 0x07A000000000013B
REAPER_ENCOUNTER_SKIN = 0x0A50000000001BEB
BOSS_BODY_CUE = 0x0EE00000000006BD
# Default voice set, authored encounter skin, and voice set after that skin's overrides.
BOSS_BODY_VOICE_SETS = {
    REAPER: (0x07A0000000000001, REAPER_ENCOUNTER_SKIN, 0x07A0000000000001),
    MERCY: (0x07A0000000000017, 0x0A5000000000196A, 0x07A000000000012C),
    ROADHOG: (0x07A0000000000053, 0x0A5000000000195C, 0x07A0000000000149),
    0x02E0000000000065: (0x07A0000000000055, 0x0A50000000001962, 0x07A0000000000145),
    0x02E0000000000016: (0x07A000000000003F, 0x0A500000000016DF, 0x07A000000000017D),
}
# 104319 enUS sample counts at 48 kHz.
BOSS_BODY_LINES = {
    0x07A0000000000001: ((0x0760000000008A2B, 172357),),
    0x07A0000000000017: ((0x0760000000008986, 114448),),
    0x07A000000000012C: ((0x0760000000008986, 114448),),
    0x07A0000000000053: ((0x0760000000008A2C, 126194),),
    0x07A0000000000149: ((0x0760000000008A2C, 126194),),
    0x07A0000000000055: ((0x0760000000008B14, 238877),),
    0x07A0000000000145: ((0x0760000000008B14, 238877),),
    0x07A000000000003F: ((0x076000000000A311, 150624), (0x076000000000A312, 102246)),
    0x07A000000000017D: ((0x076000000000A311, 150624), (0x076000000000A312, 102246)),
}
# The two authored Photon Barrier cues on 156B/65 and /66, from voice sets
# 003F and 017D. Only the first cue is for opponents of the Summoner.
SUMMONER_BARRIER_LINES = {
    0x0EE0000000000A5A: (65, 0x076000000000B5B0, 120440),
    0x0EE0000000000A5B: (66, 0x076000000000B577, 120496),
}
BOSS_MODE_SPAWN_LINES = {
    0x0EE00000000006B5: ((0x0760000000008CB1, 160173),),
    0x0EE00000000006B6: ((0x07600000000089C6, 283783),),
    0x0EE00000000006B7: ((0x0760000000008CCE, 296084),),
    0x0EE00000000006C4: ((0x0760000000008CDC, 264390),),
    0x0EE00000000008E1: ((0x076000000000A466, 296000),),
    0x0EE00000000006D6: (
        (0x0760000000008CB2, 280352),
        (0x0760000000008CB3, 250539),
        (0x076000000000A45E, 323008),
    ),
    0x0EE00000000006DA: (
        (0x0760000000008CC2, 525857),
        (0x0760000000008CC3, 384966),
        (0x076000000000A45F, 409344),
    ),
    0x0EE00000000006DD: (
        (0x0760000000008CD3, 422035),
        (0x0760000000008CD5, 411218),
        (0x076000000000A460, 368128),
    ),
    0x0EE00000000006E0: (
        (0x0760000000008CDD, 327646),
        (0x0760000000008CDE, 318741),
        (0x0760000000008CDF, 428763),
        (0x0760000000008CF0, 186821),
    ),
    0x0EE00000000008E4: ((0x076000000000A400, 323072), (0x076000000000A401, 311360)),
}


# Mode voice set 013B defeat lines: line GUID, enUS samples, killer hero, weight.
BOSS_DEFEAT_LINES = {
    0x0EE00000000006D9: (
        (0x0760000000008CBC, 136427, None, 0.5),
        (0x0760000000008CBD, 327448, 0x02E000000000013B, 1),
        (0x0760000000008CBE, 291962, 0x02E000000000006E, 1),
        (0x0760000000008CBF, 243415, 0x02E0000000000042, 1),
        (0x0760000000008CC0, 219273, 0x02E0000000000005, 1),
        (0x076000000000A3FC, 238656, 0x02E0000000000006, 1),
        (0x076000000000A3FD, 292160, 0x02E0000000000029, 1),
        (0x076000000000A3FE, 258432, 0x02E000000000000A, 1),
        (0x076000000000A3FF, 232896, 0x02E0000000000020, 1),
        (0x076000000000A61C, 341760, None, 1),
        (0x076000000000A623, 248384, None, 1),
        (0x076000000000B91A, 283214, 0x02E0000000000195, 1),
        (0x076000000000B91B, 303541, 0x02E0000000000003, 1),
        (0x076000000000E19F, 302199, 0x02E0000000000200, 1),
        (0x076000000000E1A0, 251453, 0x02E0000000000221, 1),
        (0x076000000000E1B3, 285934, 0x02E000000000021D, 1),
    ),
    0x0EE00000000006DC: (
        (0x0760000000008CC9, 395585, 0x02E0000000000042, 1),
        (0x0760000000008CCA, 376787, 0x02E000000000006E, 1),
        (0x0760000000008CCB, 397103, 0x02E000000000013B, 1),
        (0x0760000000008CCC, 306340, 0x02E0000000000005, 1),
        (0x0760000000008CCD, 427708, None, 0.5),
        (0x076000000000A430, 256064, 0x02E0000000000006, 1),
        (0x076000000000A431, 307712, 0x02E0000000000029, 1),
        (0x076000000000A432, 304128, 0x02E0000000000020, 1),
        (0x076000000000A433, 310592, 0x02E000000000000A, 1),
        (0x076000000000A61B, 412608, None, 1),
        (0x076000000000A622, 381376, None, 1),
        (0x076000000000B91C, 368558, 0x02E0000000000003, 1),
        (0x076000000000B91D, 275826, 0x02E0000000000195, 1),
        (0x076000000000E1A1, 329537, 0x02E0000000000221, 1),
        (0x076000000000E1A2, 371491, 0x02E0000000000200, 1),
        (0x076000000000E1B4, 302590, 0x02E000000000021D, 1),
    ),
    0x0EE00000000006DF: (
        (0x0760000000008CD7, 338134, 0x02E0000000000005, 1),
        (0x0760000000008CD8, 297238, 0x02E000000000006E, 1),
        (0x0760000000008CD9, 259641, 0x02E000000000013B, 1),
        (0x0760000000008CDA, 366662, 0x02E0000000000042, 1),
        (0x076000000000A434, 427904, 0x02E0000000000006, 1),
        (0x076000000000A435, 352512, 0x02E0000000000020, 1),
        (0x076000000000A436, 332480, None, 1),
        (0x076000000000A437, 286663, 0x02E000000000000A, 1),
        (0x076000000000A463, 270656, 0x02E0000000000029, 1),
        (0x076000000000A61A, 438848, None, 1),
        (0x076000000000A621, 464832, None, 1),
        (0x076000000000B91E, 332775, 0x02E0000000000003, 1),
        (0x076000000000B91F, 292200, 0x02E0000000000195, 1),
        (0x076000000000E1A3, 324936, 0x02E0000000000221, 1),
        (0x076000000000E1A4, 271888, 0x02E0000000000200, 1),
        (0x076000000000E1B5, 310529, 0x02E000000000021D, 1),
    ),
    0x0EE00000000008E3: (
        (0x076000000000A40B, 203520, 0x02E000000000006E, 1),
        (0x076000000000A40C, 357696, 0x02E0000000000042, 1),
        (0x076000000000A40E, 305600, 0x02E000000000000A, 1),
        (0x076000000000A40F, 320576, 0x02E0000000000029, 1),
        (0x076000000000A410, 279872, 0x02E0000000000006, 1),
        (0x076000000000A413, 309760, 0x02E0000000000020, 1),
        (0x076000000000A414, 329024, 0x02E000000000013B, 1),
        (0x076000000000A415, 244864, 0x02E0000000000005, 1),
        (0x076000000000A41B, 119360, None, 1),
        (0x076000000000A61E, 312512, None, 1),
        (0x076000000000A61F, 302336, None, 1),
        (0x076000000000B922, 404261, 0x02E0000000000003, 1),
        (0x076000000000B923, 335051, 0x02E0000000000195, 1),
        (0x076000000000E1A7, 350750, 0x02E0000000000221, 1),
        (0x076000000000E1A8, 242888, 0x02E0000000000200, 1),
        (0x076000000000E1B7, 297941, 0x02E000000000021D, 1),
    ),
    0x0EE00000000008E9: (
        (0x076000000000A438, 287360, 0x02E000000000000A, 1),
        (0x076000000000A439, 398784, 0x02E0000000000042, 1),
        (0x076000000000A43A, 290880, 0x02E0000000000029, 1),
        (0x076000000000A43B, 244928, 0x02E000000000006E, 1),
        (0x076000000000A43C, 287744, 0x02E0000000000005, 1),
        (0x076000000000A43D, 376320, 0x02E000000000013B, 1),
        (0x076000000000A43E, 339264, 0x02E0000000000006, 1),
        (0x076000000000A43F, 317696, None, 1),
        (0x076000000000A440, 350592, 0x02E0000000000020, 1),
        (0x076000000000A61D, 315456, None, 1),
        (0x076000000000A620, 292032, None, 1),
        (0x076000000000B920, 366401, 0x02E0000000000003, 1),
        (0x076000000000B921, 349309, 0x02E0000000000195, 1),
        (0x076000000000E1A5, 318758, 0x02E0000000000221, 1),
        (0x076000000000E1A6, 291649, 0x02E0000000000200, 1),
        (0x076000000000E1B6, 315323, 0x02E000000000021D, 1),
    ),
    0x0EE000000000091B: (
        (0x076000000000A5ED, 158912, None, 1),
        (0x076000000000A5EE, 142144, None, 1),
        (0x076000000000A5EF, 134080, None, 1),
        (0x076000000000A5F0, 131008, None, 1),
        (0x076000000000A5F1, 114624, None, 1),
        (0x076000000000B949, 132134, None, 1),
    ),
    0x0EE000000000091D: (
        (0x076000000000A5F7, 174912, None, 1),
        (0x076000000000A5F8, 140352, None, 1),
        (0x076000000000A5F9, 128704, None, 1),
        (0x076000000000A5FA, 141248, None, 1),
        (0x076000000000A5FB, 163392, None, 1),
    ),
    0x0EE000000000091E: (
        (0x076000000000A601, 166272, None, 1),
        (0x076000000000A602, 173760, None, 1),
        (0x076000000000A603, 177728, None, 1),
        (0x076000000000A604, 143424, None, 1),
        (0x076000000000A605, 134336, None, 1),
    ),
    0x0EE0000000000921: (
        (0x076000000000A60B, 155200, None, 1),
        (0x076000000000A60C, 127168, None, 1),
        (0x076000000000A60D, 109760, None, 1),
        (0x076000000000A60E, 132160, None, 1),
        (0x076000000000A60F, 108608, None, 1),
    ),
    0x0EE0000000000923: (
        (0x076000000000A615, 175296, None, 1),
        (0x076000000000A616, 139584, None, 1),
        (0x076000000000A617, 112704, None, 1),
        (0x076000000000A618, 132032, None, 1),
        (0x076000000000A619, 129344, None, 1),
    ),
}


def bind_boss_voice(match, component, hero, skin_theme):
    world = component.world
    mode = runtime.GAME_MODE_ENTITY
    body = component.entity
    rng = Random(body)
    default, encounter_skin, encounter_voice = BOSS_BODY_VOICE_SETS[hero]
    if world.voice_set(body) is None and skin_theme in (None, encounter_skin):
        world.bind_voice_set(body, encounter_voice if skin_theme else default)

    def load(guid):
        if guid == BOSS_BODY_CUE and world.voice_set(body) in BOSS_BODY_LINES:
            return voice_stimulus.Stimulus(guid, playable=True)
        if hero == SYMMETRA and guid in SUMMONER_BARRIER_LINES:
            return voice_stimulus.Stimulus(guid, playable=True)
        if guid in BOSS_MODE_SPAWN_LINES or guid in BOSS_DEFEAT_LINES:
            return voice_stimulus.Stimulus(guid, playable=True, mode=True)
        return NotImplemented

    def allow(stimulus):
        if stimulus.mode:
            return world.voice_set(mode) == ENCOUNTER_VOICE_SET
        return world.voice_set(body) in BOSS_BODY_LINES

    def submit(state, stimulus, targets):
        instance, node = state.instance, state.node
        speaker = mode if stimulus.mode else body
        placement_line = SUMMONER_BARRIER_LINES.get(stimulus.guid)
        if placement_line is not None:
            position, line, _ = placement_line
            if hero != SYMMETRA or (instance.graph.index, node.pos) != (0x156B, position) or targets != (body,):
                component.unsupported(instance, 'Summoner barrier voice targets', node.cls, node)
            if position == 66:
                return line & 0xFFFFFFFF  # The enemy team has no human listeners.
        elif stimulus.guid in BOSS_DEFEAT_LINES:
            if instance.graph.index != 0x0CCD or not 6 <= node.pos <= 15 or targets != (mode,):
                component.unsupported(instance, 'boss defeat voice targets', node.cls, node)
            killer = world.entities.get(expr.to_entity(state.evaluate(node.field('m_8C8C5285'), watch=False)))
            killer_hero = killer.hero if killer is not None else None
            eligible = [row for row in BOSS_DEFEAT_LINES[stimulus.guid] if row[2] in (None, killer_hero)]
            line, _, _, _ = rng.choices(eligible, weights=[row[3] for row in eligible])[0]
        else:
            expected_node = 61 <= node.pos <= 70 if stimulus.mode else node.pos == 77
            if instance.graph.index != 0x0CE0 or not expected_node or targets != (speaker,):
                component.unsupported(instance, 'boss spawn voice targets', node.cls, node)
            choices = (BOSS_MODE_SPAWN_LINES[stimulus.guid] if stimulus.mode
                       else BOSS_BODY_LINES[world.voice_set(body)])
            line, _ = rng.choice(choices) if len(choices) > 1 else choices[0]
        for player in match.players:
            component.consume_work('boss voice recipient')
            if player.spawned and not player.gone and player.has_body and player.team == 0:
                voicelines.queue_voice_notice(player, speaker, stimulus.guid, line, anchor=body)
        return line & 0xFFFFFFFF

    def duration(handle, guid, mode_voice):
        if not mode_voice and guid in SUMMONER_BARRIER_LINES:
            _, line, samples = SUMMONER_BARRIER_LINES[guid]
            return samples / 48000 if handle == (line & 0xFFFFFFFF) else NotImplemented
        if mode_voice == (guid != BOSS_BODY_CUE):
            rows = (BOSS_DEFEAT_LINES.get(guid, BOSS_MODE_SPAWN_LINES.get(guid, ())) if mode_voice
                    else (row for choices in BOSS_BODY_LINES.values() for row in choices))
            for line, samples, *_ in rows:
                if handle == (line & 0xFFFFFFFF):
                    return samples / 48000
        return NotImplemented

    manager = voice_stimulus.Manager(
        load=load, allow=allow,
        submit=submit, duration=duration, part=lambda *_: 0.0,
    )
    world.set_voice_context(component.entity, voice_stimulus.Context(
        component, clock=lambda: component.now / 1000, lookup=lambda: manager,
    ))


class EncounterModelControl:
    """Headless model fields written by 6CA4B0 and CA8570."""

    MASK = (1 << 45) | (1 << 15)

    def __init__(self, *placements):
        self.placements = frozenset(placements)
        self.flags = 0
        self.phase_flag = False

    def write(self, enabled):
        if enabled:
            self.flags &= ~self.MASK
        else:
            self.flags |= self.MASK

    def within_distance(self, state):
        instance, node = state.instance, state.node
        if (instance.graph.index, node.pos) not in self.placements or state.control_radius != -1.0:
            instance.component.unsupported(instance, 'encounter model rule', node.cls, node)
        return False

    def write_phase_flag(self, state, enabled, recursive):
        if recursive:
            state.instance.component.unsupported(
                state.instance, 'recursive encounter model update', state.node.cls, state.node,
            )
        self.phase_flag = enabled


@dataclass
class BossPursuit(npc_navigation.Navigator):
    """Follow collision routes on the boss's body mover."""

    drop_started: int | None = None
    drop_used: bool = False
    handoff_complete: bool = False
    witch_switch_request: tuple[int, int] | None = None

    def drop(self, actor, frame, seconds, forward):
        mover = self.mover
        throttle = bots.FORWARD if forward else 0
        mover.step_pose(Command(frame, yaw=REAPER_BALCONY_EXIT_YAW, forward=throttle), seconds, frame)
        actor.position, actor.velocity, actor.yaw = tuple(mover.position), mover.velocity, mover.yaw
        actor.pitch = mover.pitch
        actor.held, actor.keys = (0, throttle), bool(throttle)
        if throttle:
            actor.last_held, actor.input_tick = actor.held, frame
        actor.moved_tick = frame
        if not mover.airborne and (actor.position[1] < REAPER_BALCONY_SPAWN[1] - 3.0
                                   or frame - self.drop_started >= 100):
            self.drop_started = None

    def pursue(self, actor, target, frame, seconds, reach, *, firing_position=None):
        if (actor.hero == REAPER and not self.drop_used
                and math.dist(actor.position, REAPER_BALCONY_SPAWN) < 0.25):
            self.drop_used, self.drop_started = True, frame
            self.drop(actor, frame, seconds, True)
            return
        changed = self.target != target.entity
        if changed or (frame >= self.next_plan and self.search is None
                       and (not self.path or math.dist(self.goal, target.feet) > 1.0)):
            self.path, self.search = (), npc_navigation.begin(actor.position, target, reach)
            self.target, self.goal = target.entity, target.feet
            self.stuck = 0
        mover = self.mover
        if self.search is not None and npc_navigation.advance(
                self.search, mover.world, mover.data, reach, npc_navigation.PLAN_SLICE,
                firing_position=firing_position):
            self.path, self.search = self.search.path, None
            self.next_plan = frame + npc_navigation.PLAN_INTERVAL
        while self.path and math.dist(actor.position, self.path[0]) < (0.02 if len(self.path) == 1 else 0.15):
            self.path = self.path[1:]
        if self.path:
            destination = self.path[0]
            if npc_navigation.edge(mover.world, actor.position, destination[0], destination[2], mover.data):
                outcome = npc_navigation._drive(
                    actor, self, frame, seconds, destination, mover.data.run_forward,
                )
                if outcome is True:
                    self.path = self.path[1:]
                if outcome is not False and self.stuck < 8:
                    return
            self.path = ()
        actor.velocity, actor.held, actor.keys = (0.0, 0.0, 0.0), (0, 0), False
        mover.velocity = actor.velocity


def _roadhog_hook_state(component):
    return next((state for script in component.instances.values()
                 if script.graph.index == 0x01C8 and script.initialized and not script.stopped
                 for state in script.states.values()
                 if state.node.pos == 11 and state.active and isinstance(state, nodes.Ability)), None)


def hook_target_allowed(world, entity):
    # 01C8 node 17 excludes targets with either entity flag set.
    return not any(expr.truthy(world.entity_var(entity, key)) for key in (961, 1859))


def _junkrat_ballistics(component, controller):
    if not any(state.node.pos == 20 and state.active for script in controller.instances.values()
               if script.graph.index == 0x0CB5 and script.initialized and not script.stopped
               for state in script.states.values()):
        return None
    weapon = next((script for script in component.instances.values()
                   if script.graph.index == 0x022B and script.initialized and not script.stopped), None)
    if weapon is None:
        return None
    volley = weapon.node_state(weapon.graph.node(31))
    speed = expr.to_float(volley.evaluate(volley.node.field('m_projectileSpeed'), watch=False))
    gravity = projectiles.unconditional_gravity(volley)
    return (speed, gravity) if speed > 0 and gravity is not None and gravity > 0 else None


def returning_boss_ready(world, hero, component):
    """Check the 0CBC return-wave branch when the mode skips a boss intro."""
    if hero == REAPER:
        seen_var, wave_state = 8520, 6
    elif hero == ROADHOG:
        seen_var, wave_state = 8521, 4
    else:
        return False
    for key in (seen_var, 8566):
        value = world.entity_var(runtime.GAME_MODE_ENTITY, key)
        if value is NotImplemented or not expr.truthy(value):
            return False
    live = [instance for instance in component.instances.values() if not instance.stopped]
    if any(instance.graph.index == 0x0CE0 for instance in live):
        return False
    return any(instance.graph.index == 0x0CBC
               and (state := instance.states.get(wave_state)) is not None and state.active
               for instance in live)


def update_boss_combat(host, actor, component):
    """Publish the defender target and drive the boss's authored weapon inputs."""
    reach, weapon_graph, _ = BOSS_PRIMARY[actor.hero]
    roadhog = actor.hero == ROADHOG
    junkrat = actor.hero == JUNKRAT
    weapon_reach = ROADHOG_SECONDARY_RANGE[1] if roadhog else reach
    world = host.world
    controller = world.linked_controllers.get(actor.entity)
    controller_vm = world.script_components.get(controller)
    nav = actor.navigation
    overrides = [(state.instance.graph.index, state.node.pos, record.identifier, record.flag)
                 for state, record in world.ai_weapon_records[actor.entity].items()
                 if state.active and record.slot == 0]
    # Use the ordinary living-body record. Defer to any additional authored override.
    normal_overrides = [(0x08B6, 3, BOSS_PRIMARY_IDENTIFIER, False)]
    if junkrat:
        normal_overrides.append((0x0CB5, 17, BOSS_PRIMARY_IDENTIFIER, False))
    ordinary_fire = sorted(overrides) == normal_overrides
    hook = _roadhog_hook_state(component) if roadhog else None
    hook_running = hook is not None and hook.flags & (nodes.Ability.RUNNING | nodes.Ability.PUSHED)
    hook_ready = (hook is not None and not hook_running and ordinary_fire and controller_vm is not None
                  and hook.remaining() <= 0 and not component.disabled.get(ROADHOG_HOOK_FIRE)
                  and expr.truthy(hook.evaluate(hook.node.field('m_150F0D92'), watch=False))
                  and any(state.node.pos == 25 and state.active for script in controller_vm.instances.values()
                          if script.graph.index == 0x0BC6 and script.initialized and not script.stopped
                          for state in script.states.values()))
    if controller_vm is not None and not nav.handoff_complete:
        # 0CE0/4 sends 06AC on exit. Its receiver can remove 0C95 and its facing state
        # together before the next host update, so an inactive 0C95/5 alone misses that handoff.
        nav.handoff_complete = any(
            state.counter and not state.active
            for owner, graph_index, position in ((component, 0x0CE0, 4), (controller_vm, 0x0C95, 5))
            for script in owner.instances.values() if script.graph.index == graph_index and not script.stopped
            for state in script.states.values() if state.node.pos == position
        )
        if not nav.handoff_complete:
            nav.handoff_complete = returning_boss_ready(world, actor.hero, component)
    target_id = None
    selected_button = None
    ready = (controller_vm is not None and controller_vm.fault is None
            and controller in world.ai_behavior_requests
            and world.current_possessions.get(controller) == actor.entity
            and not actor.dead and not host.match.combat.is_stunned(actor, tick=host.frame)
            and not component.state_registries.get('STU_9935FBC8')
            and not world.ai_facing_records[actor.entity] and nav.handoff_complete)
    seconds = host.frame_ms / 1000
    pursuing = actor.keys or bool(nav.path) or nav.search is not None
    if nav.drop_started is not None and not actor.dead:
        nav.drop(actor, host.frame, seconds, ready and host.frame - nav.drop_started < 100)
    elif ready and hook_running:
        target_id = world._lookup_ai_linked_target(controller, 0)
        if pursuing:
            npc_navigation.stop(actor)
    elif ready:
        fight = host.match.combat
        enemies = sorted(((math.dist(actor.position, target.feet), target.entity, target)
                          for target in fight._enemies(actor)
                          if target.team == 0 and target.entity in world.entities), key=lambda item: item[:2])
        selection_reach = ROADHOG_HOOK_RANGE if hook_ready else weapon_reach
        ballistics = _junkrat_ballistics(component, controller_vm) if junkrat and ordinary_fire else None
        aim_arc = partial(npc_navigation.ballistic_aim, fight._collision(),
                          speed=ballistics[0], gravity=ballistics[1]) if ballistics is not None else None
        for distance, _, target in enemies:
            if distance > selection_reach:
                break
            if hook_ready and hook_target_allowed(world, target.entity):
                button = ROADHOG_HOOK_FIRE
            elif distance <= reach:
                button = BOSS_PRIMARY_FIRE
            elif roadhog and ROADHOG_SECONDARY_RANGE[0] <= distance <= weapon_reach:
                button = ROADHOG_SECONDARY_FIRE
            else:
                continue
            if junkrat:
                angles = aim_arc(actor.position, target) if aim_arc is not None else None
                if angles is None:
                    continue
                yaw, pitch = angles
            else:
                direction = npc_navigation.visible_aim(fight._collision(), actor.position, target)
                if direction is None:
                    continue
                x, y, z = direction
                yaw = bots.heading(x, z)
                pitch = round(-math.atan2(y, math.hypot(x, z)) * bots.YAW_UNITS / math.tau)
            world.entities[actor.entity].aim_writer(yaw, pitch, host.frame)
            target_id = target.entity
            selected_button = button
            break
        if target_id is None and enemies and (not junkrat or aim_arc is not None):
            pursuit_reach = reach if enemies[0][0] <= weapon_reach else weapon_reach
            nav.pursue(actor, enemies[0][2], host.frame, seconds, pursuit_reach, firing_position=aim_arc)
        elif pursuing:
            npc_navigation.stop(actor)
    elif pursuing:
        npc_navigation.stop(actor)
    if controller in world.ai_behavior_requests:
        world.set_ai_linked_target(controller, 0, target_id)

    controls = [(BOSS_PRIMARY_FIRE, 20 if junkrat else 24)]
    if roadhog:
        controls.append((ROADHOG_SECONDARY_FIRE, 28))
    edges = []
    for button, position in controls:
        held = button in component.held
        fire = selected_button == button and ordinary_fire and (held or any(
            isinstance(state, nodes.LogicalButton) and state.active and state.node.pos == position
            for script in component.instances.values() if script.graph.index == weapon_graph
            and script.initialized and not script.stopped for state in script.states.values()
        ))
        if fire != held:
            edges.append((button, fire))
    if roadhog and (selected_button == ROADHOG_HOOK_FIRE) != (ROADHOG_HOOK_FIRE in component.held):
        edges.append((ROADHOG_HOOK_FIRE, selected_button == ROADHOG_HOOK_FIRE))
    if edges:
        component.edges(sorted(edges, key=lambda edge: edge[1]))


def _publish_weapon_target(world, control, sender, slot, variable, entity):
    world.set_ai_linked_target(control.component.entity, slot, entity)
    saved = control.find_var(runtime.INSTANCE, variable, create=False)
    previous = expr.to_entity(saved.value()) if saved is not None else expr.INVALID_ENTITY
    if previous != (entity or expr.INVALID_ENTITY):
        runtime.send_instance_message(control, 0x0240000000000079,
                                      {0x57: slot, 0x59: expr.Entity(entity or 0)}, sender)


def update_witch_combat(host, actor, component):
    """Drive the target slots and weapon inputs consumed by 0CD2."""
    world, fight = host.world, host.match.combat
    controller = world.linked_controllers.get(actor.entity)
    controller_vm = world.script_components.get(controller)
    if controller_vm is None or controller_vm.fault is not None:
        return
    control = next((script for script in controller_vm.instances.values()
                    if script.graph.index == 0x0CD2 and script.initialized and not script.stopped), None)
    if control is None:
        return
    ready = (not actor.dead and not fight.is_stunned(actor, tick=host.frame)
             and bool(world.ai_behavior_requests.get(controller))
             and world.current_possessions.get(controller) == actor.entity
             and not component.state_registries.get('STU_9935FBC8')
             and not world.ai_facing_records[actor.entity])
    allies, enemies = [], []
    if ready:
        staff = next((script for script in component.instances.values()
                      if script.graph.index == 0x006B and script.initialized and not script.stopped), None)
        ally_filter = staff.graph.node(121).field('m_filter') if staff is not None else None
        for target in fight._targets():
            binding = world.entities.get(target.entity)
            if binding is None or target.entity == actor.entity:
                continue
            vitals = fight.vitals_of(target)
            if vitals.dead:
                continue
            distance = math.dist(actor.position, target.feet)
            if target.team == actor.team and distance <= 40:  # 0CD2/52.
                if staff is None or world._filter_entity(staff, target.entity, ally_filter) is not True:
                    continue
                current = vitals.normal_health(False)
                maximum = vitals.normal_health(True)
                if current < maximum:
                    favored = 0x0D6000000000011E in (binding.target_tags or ())  # 0CD2/51.
                    allies.append((not favored, current / maximum, distance, target.entity, target))
            elif target.team == 0 and distance <= 60:  # 0CD2/24.
                enemies.append((distance, target.entity, target))
    ally = min(allies)[-1] if allies else None
    enemy = min(enemies)[-1] if enemies else None
    for slot, variable, target in ((0, 2299, enemy), (1, 7126, ally)):
        _publish_weapon_target(world, control, actor.entity, slot, variable,
                               target.entity if target is not None else None)

    nav = actor.navigation
    target = ally or enemy
    reach = 12.0 if ally is not None else 20.0  # 0CD2/30 and /18.
    direction = None
    if target is not None and math.dist(actor.position, target.feet) <= reach:
        direction = npc_navigation.visible_aim(fight._collision(), actor.position, target)
    if direction is not None:
        x, y, z = direction
        yaw = bots.heading(x, z)
        pitch = round(-math.atan2(y, math.hypot(x, z)) * bots.YAW_UNITS / math.tau)
        world.entities[actor.entity].aim_writer(yaw, pitch, host.frame)
    if target is not None and direction is None:
        nav.pursue(actor, target, host.frame, host.frame_ms / 1000, reach)
    elif actor.keys or nav.path or nav.search is not None:
        npc_navigation.stop(actor)

    switch_node, switch_button = (44, 15) if ally is not None else (49, 16)
    switch = next((state for state in control.states.values()
                   if state.node.pos == switch_node and state.active), None)
    edges = [(button, False) for button in (15, 16) if button in component.held]
    if ready and switch is not None:
        request = (switch_node, switch.counter)
        if request != nav.witch_switch_request and switch_button not in component.held:
            nav.witch_switch_request = request
            edges.append((switch_button, True))
    weapon_graph, trigger, override = (0x006B, 118, 9) if ally is not None else (0x006D, 7, 12)
    fire = (direction is not None and not any(pressed for _, pressed in edges)
            and any(state.instance is control and state.node.pos == override and state.active
                    for state in world.ai_weapon_records[actor.entity])
            and any(state.active and state.node.pos == trigger for script in component.instances.values()
                    if script.graph.index == weapon_graph and script.initialized and not script.stopped
                    for state in script.states.values()))
    if fire != (BOSS_PRIMARY_FIRE in component.held):
        edges.append((BOSS_PRIMARY_FIRE, fire))
    if edges:
        component.edges(sorted(edges, key=lambda edge: edge[1]))


def update_symmetra_combat(host, actor, component):
    """Drive the Summoner's two weapon slots through controller 185D."""
    world, fight = host.world, host.match.combat
    controller = world.linked_controllers.get(actor.entity)
    controller_vm = world.script_components.get(controller)
    if controller_vm is None or controller_vm.fault is not None:
        return
    control = next((script for script in controller_vm.instances.values()
                    if script.graph.index == 0x185D and script.initialized and not script.stopped), None)
    if control is None:
        return
    ready = (not actor.dead and not fight.is_stunned(actor, tick=host.frame)
             and bool(world.ai_behavior_requests.get(controller))
             and world.current_possessions.get(controller) == actor.entity
             and not component.state_registries.get('STU_9935FBC8')
             and not world.ai_facing_records[actor.entity])
    primary_range = expr.to_float(control.find_var(runtime.INSTANCE, 7198).value())
    secondary_range = expr.to_float(control.find_var(runtime.INSTANCE, 4074).value())
    nearest = min(((math.dist(actor.position, target.feet), target.entity, target)
                   for target in fight._enemies(actor) if target.team == 0 and target.entity in world.entities),
                  default=None) if ready else None
    for slot, variable, radius in ((0, 8066, primary_range * 1.25), (1, 11652, secondary_range)):
        entity = nearest[1] if nearest is not None and nearest[0] <= radius else None
        _publish_weapon_target(world, control, actor.entity, slot, variable, entity)

    chosen = None
    direction = None
    if nearest is not None:
        distance, _, target = nearest
        # 185D/48 and /33 switch at the authored primary reach plus one.
        primary = distance <= primary_range + 1
        reach = primary_range + 1 if primary else secondary_range
        if distance <= reach:
            direction = npc_navigation.visible_aim(fight._collision(), actor.position, target)
        if direction is not None:
            x, y, z = direction
            yaw = bots.heading(x, z)
            pitch = round(-math.atan2(y, math.hypot(x, z)) * bots.YAW_UNITS / math.tau)
            world.entities[actor.entity].aim_writer(yaw, pitch, host.frame)
            override = 18 if primary else 21
            if any(state.instance is control and state.node.pos == override and state.active
                   for state in world.ai_weapon_records[actor.entity]):
                chosen = 20 if primary else 29
        else:
            actor.navigation.pursue(actor, target, host.frame, host.frame_ms / 1000, primary_range)
    if (nearest is None or direction is not None) and (
            actor.keys or actor.navigation.path or actor.navigation.search is not None):
        npc_navigation.stop(actor)
    edges = [(button, chosen == button) for button in (20, 29)
             if (chosen == button) != (button in component.held)]
    if edges:
        component.edges(sorted(edges, key=lambda edge: edge[1]))


def summoner_damage_event(component, target, damage, tags):
    """Feed applied combat damage into the Summoner's authored charge script."""
    gate = component.entity_var(2426, create=False)
    if component.owner_dead or gate is None or gate.value() is not True:
        return
    charge = next((script for script in component.instances.values()
                   if script.graph.index == 0x0B8B and script.initialized and not script.stopped), None)
    if charge is not None:
        runtime.send_instance_message(charge, 0x0240000000000012, {
            0x13: damage,
            0x15: expr.Entity(target),
            0x3D: tuple(expr.Asset(tag) for tag in tags),
            0x624: (),
        }, component.entity)


def bind_body_team(host, match, body):
    """Resolve team bits from current Junkenstein bodies on either side."""
    world = host.world
    if body in world.team_contexts:
        return

    def lookup(entity):
        if entity not in world.entities:
            return None
        registration = host.combatants.get(entity)
        if registration is not None:
            actor = registration[0]
            if host.combatant_component(actor) is not None:
                return entity_team.Target(entity, entity_team.FilterBits(TEAM_BIT << actor.team))
        for player in match.players:
            if not player.gone and player.has_body and player.body == entity:
                return entity_team.Target(entity, entity_team.FilterBits(player.team_bit))
        return None

    def watch(watcher, _):
        # Match teams come from live actors. Recheck through the VM's normal native polling.
        watcher.instance.context(watcher).poll_native({'$': entity_team.CLASS})

    world.set_entity_team_context(body, entity_team.Context(
        body, True, True, True, lookup=lookup, watch_filter=watch, watch_environment=watch))


def install_enemy_creator(match, host, definitions, configure_component):
    """Mount the prepared enemy allocator on a match's Junkenstein scene."""
    world = host.world
    if (match.game_map.map_guid != content.JUNKENSTEIN_MAP or world.map_guid != match.game_map.map_guid
            or world.mode != match.game_map.mode_guid or world.mode not in content.JUNKENSTEIN_MODES
            or world.hero_creator is not None or world.possessors is None
            or not callable(configure_component)):
        raise ValueError('enemy creation requires a configured Junkenstein scene')
    catalog = {}
    for definition in definitions:
        if not isinstance(definition, npc.Definition) or definition.hero in catalog:
            raise ValueError('enemy creation requires unique validated NPC definitions')
        catalog[definition.hero] = definition

    fight = match.combat
    previous_search = world.tracked_targets
    previous_damage = world.explosion_damage
    previous_route = world.objective_route
    previous_filter = world.array_filter_inputs

    def enemy_filter_bits(actor):
        return (TEAM_BIT << actor.team) | 1 | (0x10 if actor.dead else 0)

    def array_filter_inputs(entity):
        registration = host.combatants.get(entity)
        if registration is not None and host.combatant_component(registration[0]) is not None:
            return True, enemy_filter_bits(registration[0])
        return previous_filter(entity) if callable(previous_filter) else NotImplemented

    def enemy_participants():
        for entity, (actor, _) in host.combatants.items():
            if host.combatant_component(actor) is not None:
                yield entity, enemy_filter_bits(actor), 4 if actor.dead else 0

    def add_wave_health(component, amount):
        registration = host.combatants.get(component.entity)
        vitals = fight.vitals.get(component.entity)
        if (registration is None or host.combatant_component(registration[0]) is not component
                or vitals is None):
            return NotImplemented
        if vitals.dead:
            # The wave child may initialize after combat has already killed its owner.
            return None
        if vitals.wave_health is not None:
            return NotImplemented
        remove = vitals.add_wave_health(amount)
        try:
            fight._write_scene_health(component, vitals)
        except Exception:
            remove()
            raise

        def cleanup():
            remove()
            if component.fault is None and host.combatant_component(registration[0]) is component:
                fight._write_scene_health(component, vitals)

        return cleanup

    def search_spawns(state, request, complete):
        if (state.instance.graph.index != 0x0CBA or request.flag or request.extra != -1
                or request.search_scale != 0.5 or not 0 < request.radius <= 70
                or not 1 <= request.min_candidates <= request.max_candidates <= 300):
            return NotImplemented
        probes = npc_navigation.spawn_candidate_probes(fight._collision(), request, mover_data(ZOMNIC_BODY))
        return host.queue_spawn_search(state.instance.component, probes, math.ceil(request.radius * 4),
                                       request.min_candidates, complete)

    def route_active(entity):
        approach = host.objective_approaches.get(entity)
        if approach is not None:
            return not approach.route.failed
        return previous_route(entity) if callable(previous_route) else NotImplemented

    def track_explosion(state):
        instance, node = state.instance, state.node
        component = instance.component
        registration = host.combatants.get(component.entity)
        location = (instance.graph.index, node.pos)
        if (location not in ((0x0C89, 47), (0x0C89, 52), (0x2B33, 14)) or registration is None
                or host.combatant_component(registration[0]) is not component):
            return previous_search(state)
        if node.pos == 47:
            # The encounter gate has its own damage message and no health pool.
            targets = previous_search(state)
            return () if targets is NotImplemented else targets
        origin = state.evaluate(node.field('m_8DCA77B6'), watch=False)
        offset = state.evaluate(node.field('m_05BE62DD'), watch=False)
        radius = state.evaluate(node.field('m_radius'), watch=False)
        if origin != expr.Entity(component.entity) or not isinstance(offset, expr.Vec3):
            return NotImplemented
        position = world.position(origin.id)
        if position is None or type(radius) not in (int, float) or not math.isfinite(radius) or radius < 0:
            return NotImplemented
        center = tuple(expr.f32(a + b) for a, b in zip(position, offset, strict=True))
        if location == (0x2B33, 14):
            actor = registration[0]
            if actor.hero != ZOMNIC or actor.dead:
                return ()
            closest = None
            for target in fight._enemies(actor):
                component.consume_work('Volatile Zomnic proximity target')
                binding = world.entities.get(target.entity)
                if binding is None or (isinstance(target.owner, bots.Bot)
                        and world._has_target_tag(binding, 0x0D60000000000664) is not True):
                    continue
                start, end = target.segment()
                distance = combat.segment_distance(center, start, end)
                candidate = (distance, target.entity)
                if (distance <= radius + target.radius and (closest is None or candidate < closest)
                        and combat.capsule_visible(fight._collision(), center, start, end, target.radius)):
                    closest = candidate
            return () if closest is None else (closest[1],)
        condition = node.field('m_filter')['m_condition']
        victims = []
        # This scoped search uses the bound combat roster and each body's feet position.
        for target in fight._targets():
            if (target.entity == origin.id or target.entity not in world.entities
                    or math.dist(center, target.feet) > radius or fight.vitals_of(target).dead):
                continue
            with component.parameter_scope({8: expr.Entity(target.entity)}):
                if expr.truthy(state.evaluate(condition, watch=False)):
                    victims.append(target.entity)
        return tuple(victims)

    def apply_explosion(source, victim, amount):
        registration = host.combatants.get(source)
        if registration is None or host.combatant_component(registration[0]) is None:
            return previous_damage(source, victim, amount) if callable(previous_damage) else NotImplemented
        target = next((target for target in fight._targets() if target.entity == victim), None)
        if target is None or victim == source or victim not in world.entities:
            return NotImplemented
        fight._damage(registration[0], target, amount, False, None, host.frame, world_tick=host.frame)
        return True

    def create(hero, position, team, yaw, flags, *, controller_definition=None, skin_theme=None):
        profile = catalog.get(hero)
        pair = graph.junkenstein_enemy(hero)
        if pair is None:
            raise ValueError('no prepared Junkenstein body for this hero')
        body_definition = int(pair['body'], 16)
        expected_controller = int(pair['controller'], 16)
        boss = hero in BOSSES
        if (boss or controller_definition is not None) and controller_definition != expected_controller:
            raise ValueError('Junkenstein controller differs from the authored pair')
        body_record = graph.junkenstein_entity(body_definition)
        control_record = graph.junkenstein_entity(expected_controller)
        if not body_record or not control_record:
            raise ValueError('Junkenstein pair needs both prepared entity records')
        if profile is None and boss:
            hero_data = content.heroes().get(hero)
            capacity = pools.authored_pools(body_record)
            if hero_data is not None and hero_data.body == body_definition and capacity is not None:
                profile = npc.Definition(hero, body_definition, hero_data.name, *capacity)
        if profile is None or profile.body != body_definition:
            raise ValueError('no prepared Junkenstein body for this hero')

        body = fight.next_bot
        controller = body + 1
        if (match.ended or not bots.BOT_ENTITY <= body < controller < 0xD0000000
                or any(entity in world.entities for entity in (body, controller))
                or any(bot.entity in (body, controller) for bot in match.bots)):
            raise ValueError('Junkenstein enemy allocator exhausted or collided')
        spawner = {'hero': f'{hero:016X}', 'team': team, 'position': tuple(position),
                   'rotation': (0, 0, 0, 1), 'graphs': (), 'create_flags': flags, 'skin_theme': skin_theme}
        data = {'name': profile.name, 'body': f'{profile.body:016X}', 'health': profile.health}
        bot = bots.Bot(body, spawner, data, {})
        bot.yaw = bots.s16(round(yaw * bots.YAW_UNITS / 360))
        vitals = combat.Vitals(body, profile.health, profile.armour, profile.shields)
        gate = world.entities.get(0x80000000 | content.JUNKENSTEIN_GATE)
        gate_position = world.position(gate.id) if gate is not None else None
        movement = None
        approach = None
        if boss or (hero in GATE_REACH and gate_position is not None and match.collision_world is not None):
            movement = Mover(bot.position, bot.yaw, match.collision_world, mover_data(bot.body))
        if hero in BOSS_PRIMARY or hero in (MERCY, SYMMETRA):
            bot.navigation = BossPursuit(movement)
        if movement is not None and hero in GATE_REACH:
            approach = GateApproach(match, bot, gate, movement, tuple(gate_position), GATE_REACH[hero])

        def self_damage(amount):
            target = next(item for item in fight._targets() if item.owner is bot)
            fight._damage(bot, target, amount, False, None, host.frame, world_tick=host.frame)

        def scale():
            vm = world.script_components[body]
            slot = vm.entity_var(BODY_SCALE_OVERRIDE, create=False)
            value = slot.value() if slot is not None else None
            return wire.UNIT_ENTITY_SCALE if value in (None, wire.UNIT_ENTITY_SCALE) else NotImplemented

        def set_aim(yaw, pitch, frame):
            if bot.dead:
                return
            movement.yaw, movement.pitch = yaw, pitch
            if (bot.yaw, bot.pitch) != (yaw, pitch):
                bot.yaw, bot.pitch, bot.moved_tick = yaw, pitch, frame

        def face(direction, frame):
            if direction.x or direction.z:
                set_aim(bots.heading(direction.x, direction.z), movement.pitch, frame)

        try:
            binding = world.bind(
                body, hero=hero, definition=profile.body, target_tags=body_record['target_tags'],
                health=partial(vitals.normal_health, False), health_pool=vitals.pool_current,
                pool_capacity=vitals.most.get, normal_health=vitals.normal_health,
                # The wave host currently models only normal pools.
                maximum_health=partial(vitals.normal_health, True),
                self_damage=self_damage, position_reader=lambda: bot.position,
                rotation_reader=lambda: _rotation(bot.yaw),
                facing_writer=face if boss else None,
                aim_writer=set_aim if boss else None,
                velocity=lambda: bot.velocity, scale_reader=scale,
                movement_reader=movement.snapshot if movement is not None else None,
                same_team_contact=(
                    partial(movement.set_same_team_contact, team) if movement is not None else None
                ),
                pet_component_absent=body_record.get('pet_component_absent', False),
            )
            # All authored wave controllers declare 76AF64F0, the native component 92 schema.
            world.bind(controller, definition=expected_controller, component92_present=True,
                       on_fire_reader=lambda: None)
            if boss:
                world.set_owned_deployables(controller, ())
            world.set_possessor(controller, True)
            world.set_primary_possessor(body, controller)
            world.set_current_possession(controller, body)
            world.set_linked_controller(body, controller)
            world.bind_ai_behavior(controller)
            world.bind_ai_control(body)
            identifiers = control_record['ai_weapon_identifiers']
            world.ai_weapon_slots[controller] = {
                int(slot): frozenset(values) for slot, values in identifiers.items()
            }
            world.set_property_modifiers(body, NumericModifiers(host.frame))
            for entity in (body, controller):
                vm = host.add_spawned_entity_scripts(entity, junkenstein=True)
                if vm is None:
                    raise ValueError('Junkenstein body and controller both require a script component')
                if boss and entity == body:
                    world.bind_data_flow_providers(body)
                    world.set_subgraph_scopes(body, scopes.ForwardHost(vm))
                    for weapon in create_body_graphs(vm, body_record, include_initial=False):
                        weapon.initialization_pending = True
                world.set_entity_query_context(entity, ai_links=True)
                if entity == body and hero == SHOCK_TIRE:
                    # 0234 is also used by player-owned RIP-Tire. This actor is autonomous.
                    world.entity_query_context(body).lookup_participant = lambda entity, watcher: None
                configure_component(vm)
                if entity == body and boss:
                    if world.auxiliary_effect_context(body) is NotImplemented:
                        # The shared 00CB helper runs on several encounter bosses.
                        # Their headless body has no auxiliary effect engine.
                        world.set_effect_context(body, None)
                    if world.voice_context(body) is NotImplemented:
                        bind_boss_voice(match, vm, hero, skin_theme)
                    if world.entity_control_context(body) is NotImplemented:
                        model = EncounterModelControl((0x01CB, 20), (0x0CE0, 17), (0x0D03, 2))
                        world.set_entity_control_context(body, entity_control.Context(
                            vm, False, within_distance=model.within_distance, write_entity=model.write,
                            model_ready=lambda: False, route=None, extra_test=None,
                        ))
            bind_body_team(host, match, body)
            match.bots.append(bot)
            fight.vitals[body] = vitals
            fight.bind_scene_enemy(host, bot)
            if approach is not None:
                host.objective_approaches[controller] = approach
            if hero == ZOMBARDIER:
                button = body_record['ai_weapon_buttons']['0']
                control = ZombardierControl(controller, bot, host.combatant_component(bot), button)
                fight.bind_scene_zombardier(host, control)
            fight.record_replacement(body)
        except Exception:
            for entity in (body, controller):
                if entity in world.entities:
                    host.retire_entity(entity)
            if bot in match.bots:
                match.bots.remove(bot)
            fight.vitals.pop(body, None)
            raise
        fight.next_bot = controller + 1
        match.publish_bot(bot)
        return binding

    world.hero_creator = create
    world.wave_health = add_wave_health
    world.tracked_targets = track_explosion
    world.explosion_damage = apply_explosion
    world.objective_route = route_active
    world.array_filter_inputs = array_filter_inputs
    if world.enemy_participant_roster is None:
        world.enemy_participant_roster = enemy_participants
    if world.spawn_search is None:
        world.spawn_search = search_spawns


class GateApproach:
    """Move a gate attacker and signal its controller's authored arrival entry once."""

    def __init__(self, match, actor, gate, movement, gate_position, reach):
        self.match, self.actor, self.gate = match, actor, gate
        target = combat.Target(gate.id, actor.team, gate_position, 1.0, 0.25, 0.75, None)
        self.route = npc_navigation.ObjectiveApproach(movement, target, reach)

    def update(self, controller, frame, seconds):
        actor, route = self.actor, self.route
        if actor.dead:
            return
        world = controller.world
        if (world.entities.get(self.gate.id) is not self.gate
                or world.script_components.get(controller.entity) is not controller
                or world.current_possessions.get(controller.entity) != actor.entity):
            route.failed = True
        script = next((item for item in controller.instances.values() if item.graph.index == 0x0C87), None)
        if (script is None or not script.initialized or script.stopped or route.failed
                or self.match.combat.is_stunned(actor, tick=frame)):
            actor.velocity, actor.held = (0.0, 0.0, 0.0), (0, 0)
            route.mover.velocity = actor.velocity
            return
        previous = tuple(route.mover.position)
        backend = world.property_modifiers(actor.entity)
        speed_scale = 1.0
        if type(backend) is NumericModifiers and backend.records:
            _, speed_scale = backend.resolve(SPEED, 1.0, frame=frame)
        arrived = route.step(frame, seconds, budget=16, speed_scale=max(0.0, speed_scale))
        if arrived:
            script.post(runtime.Event(controller.now, 0, runtime.ENTRY, node=script.graph.node(20)))
        actor.position = tuple(route.mover.position)
        if actor.position == previous or arrived:
            actor.velocity, actor.held = (0.0, 0.0, 0.0), (0, 0)
            route.mover.velocity = actor.velocity
        else:
            actor.yaw, actor.velocity = route.mover.yaw, route.mover.velocity
            actor.held = actor.last_held = (0, bots.FORWARD)
            actor.input_tick = actor.moved_tick = frame


class ZombardierControl:
    """Publish a target and hold the weapon input while its low arc is clear."""

    def __init__(self, controller, shooter, component, button):
        self.controller = controller
        self.shooter = shooter
        self.component = component
        self.button = button

    def update(self, world, collision, targets):
        """Take the host's living targets and return any change to the body input."""
        shooter = self.shooter
        body = self.component
        held = self.button in body.held
        instance = next((item for item in body.instances.values() if item.graph.index == 0x0CA1), None)
        target_id = None
        if instance is not None and instance.initialized and not body.owner_dead:
            volley = instance.node_state(instance.graph.node(14))
            gravity = projectiles.unconditional_gravity(volley)
            speed = expr.to_float(volley.evaluate(volley.node.field('m_projectileSpeed'), watch=False))
            if gravity is not None and speed > 0:
                enemies = (target for target in targets if target.team != shooter.team)
                ranked = sorted(
                    enemies, key=lambda target: (math.dist(shooter.position, target.feet), target.entity),
                )
                for target in ranked:
                    aim = npc_navigation.ballistic_aim(collision, shooter.position, target, speed, gravity)
                    if aim is not None:
                        shooter.yaw, shooter.pitch = aim
                        target_id = target.entity
                        break
        world.set_ai_linked_target(self.controller, 0, target_id)
        ready = target_id is not None and (held or any(
            isinstance(state, nodes.LogicalButton) and state.active and state.button_id() == self.button
            for state in instance.states.values()
        ))
        return [(self.button, ready)] if ready != held else []
