"""104319 authored hammer timing through real command, health and death paths.

These server fixtures do not establish native target filters or client acceptance.
"""

import math
import os
import unittest
from dataclasses import replace
from pathlib import Path
from ow174.paths import DATA_DIR
from unittest.mock import patch

import test_mccree_flashbang as flashbang_tests
from test_game_combat import KANEZAKA_DM, FakeClient, Run, aim
from test_game_graph_analysis import make_graph, node

from ow174.game import bots, combat, hook, mover, npc
from ow174.game.collision import LoadState, World, flat_world
from ow174.game.commands import JUMP, Command
from ow174.game.content import SOLDIER, heroes
from ow174.game.mover import Mover
from ow174.game.script import expr, graph, health_queries, runtime
from ow174.game.script.driver import BodyWorld

NPC_DATA = Path(os.environ.get("ORPHEUS_PVE_NPC_DATA", str(DATA_DIR)))
NPC_MOVERS = Path(os.environ.get("ORPHEUS_NPC_MOVERS", str(DATA_DIR / "game_movers_174.json")))
BARRIER_DATA = Path(os.environ.get("ORPHEUS_REINHARDT_COLLISION", str(DATA_DIR / "reinhardt_collision_174.json")))


def controlled(fight, entity, *, tick=None):
    target = next((t for t in fight._targets() if t.entity == entity), None)
    return target is not None and (fight.is_stunned(target.owner, tick=tick) or fight.punches.controlled(entity))


def pinned(fight, entity):
    from ow174.game import reinhardt_charge
    return (reinhardt_charge.pinned(fight, entity) or entity in fight.pins.values()
            or any(c.captured == entity for c in fight.charge_contacts.values()))


def control_deadline(fight, entity):
    # Read the active canonical counter clocks; graph endpoints are inclusive.
    from ow174.game.defense_filters import last_countdown_world_tick
    deadlines = [fight.stuns.get(entity, 0), fight.source_control_deadline(entity)]
    if entity in fight.punches.controls:
        deadlines.append(math.ceil(fight.punches.controls[entity] / combat.TICK))
    for stun in fight.brigitte.stuns:
        phase = stun.instance.states.get(20)
        if stun.instance.component.entity == entity and phase is not None and phase.active:
            deadlines.append(last_countdown_world_tick(phase) + 1)
    return max(deadlines)


def reinhardt_barrier_instance(player):
    return next(i for i in player.body_script.component.instances.values() if i.graph.index == 0x95)


def set_reinhardt_shield_health(fight, body, amount):
    actor = fight.barriers.authored_for(body)
    if actor is None:
        fight.reinhardt_barriers[body].current = amount
    else:
        # Fixture input targets the actual native shield pool, not its sync cache.
        from ow174.game import barriers
        barriers.damage(fight, actor, max(0., actor.vitals.total - amount))


def any_controls(fight):
    return any(controlled(fight, t.entity) for t in fight._targets())


def punch_instance(player):
    return next(i for i in player.body_script.component.instances.values() if i.graph.index == 0xD06)


def impact_entities(fight):
    return {impact.target for impact in fight.punches.impacts}


def active_punches(fight):
    #012F keeps completed history records;0007 observed active drives only.
    active = {}
    for body, dash in fight.punches.dashes.items():
        owner = next((p for p in fight.match.players if p.body == body), None)
        script = owner.body_script if owner is not None else None
        instance = script.component.instances.get(dash.activation[0]) if script is not None else None
        state = instance.states.get(7) if instance is not None else None
        if not dash.finished and state is not None and state.active:
            active[body] = dash
    return active


def passive_decay(vitals):
    return ((vitals.temporary_decay_at, vitals.temporary_decay_rate)
            if vitals.temporary_shields > 0 else None)


_BRIGITTE_VIEWS = {}


def brigitte_shield(fight, body):
    """Read the current canonical shield; keep the source observation contract."""
    shield = fight.brigitte.barriers[body]
    key = id(shield)
    if key not in _BRIGITTE_VIEWS:
        class View:
            instance = property(lambda self: shield.instance)
            current = property(lambda self: shield.health)

            def raised(self):
                owner = next(p for p in fight.match.players if p.body == body)
                return fight.brigitte._barrier_active(owner, shield)
        _BRIGITTE_VIEWS[key] = View()
    return _BRIGITTE_VIEWS[key]


class SegmentPairTests(unittest.TestCase):
    def test_crossing_parallel_point_and_skew_segments(self):
        distance = combat.segment_pair_distance
        self.assertAlmostEqual(distance((-1, 0, 0), (1, 0, 0), (0, -1, 0), (0, 1, 0)), 0)
        self.assertAlmostEqual(distance((-1, 0, 0), (1, 0, 0), (-1, 0, 2), (1, 0, 2)), 2)
        self.assertAlmostEqual(distance((0, 0, 0), (0, 0, 0), (1, 0, 0), (2, 0, 0)), 1)
        self.assertAlmostEqual(distance((-1, 0, 0), (1, 0, 0), (0, -1, 2), (0, 1, 2)), 2)


def deployment_health(deployment):
    from ow174.game import sigma_barrier
    return deployment.vitals.total if isinstance(deployment, sigma_barrier.Barrier) else deployment.health


def shield_bash_at_deployment(case, deployment, origin_z, direction, *, expiry=None, allied=False):
    """Approach a real deployment and return the stopped pose for removal checks."""
    case.match.game_map = replace(case.match.game_map, free_for_all=False)
    case.rein.team = case.enemy.team
    case.other.team = 1 if case.enemy.team != 1 else 0
    case.assertTrue(case.match.switch_hero(case.other, heroes()[0x02E0000000000195]))
    fight = case.match.combat
    origin = (0, 2, origin_z)
    heading = (0, 0, direction)
    hit = fight._barrier_contact(case.other, origin, heading, 15)
    case.assertIsNotNone(hit)
    case.assertIs(hit[2], deployment)
    face = origin_z + direction * hit[0]
    start = face - direction * 1.8
    case.place(case.other, (0, 1, start))
    case.place(case.rein, (0, 1, face + direction * 1.2))
    yaw = 0 if direction == 1 else -32768
    hold = {case.other: (yaw, 0, 2)}
    for _ in range(35):
        case.run.step(hold)
    if expiry is not None:
        case.assertGreater(expiry - 7, fight.tick)
        while fight.tick < expiry - 7:
            case.run.step(hold)
    health = deployment_health(deployment)
    if allied:
        case.rein.team, case.other.team = case.other.team, case.enemy.team
    case.run.step({case.other: (yaw, 0, 3)})
    began = fight.tick
    dash = brigitte_shield(fight, case.other.body).instance.states[14]
    case.assertTrue(dash.active)
    for _ in range(5):
        case.run.step(hold)
        if not dash.active:
            break
    case.assertFalse(dash.active)
    stopped = tuple(case.other.mover.position)
    if allied:
        case.assertEqual(deployment_health(deployment), health)
        case.assertEqual(case.health(case.rein), 497.5)
        case.assertTrue(controlled(fight, case.rein.body))
        case.assertEqual(len(case.other.client.messages(combat.HIT)), 1)
        return hold, stopped
    case.assertGreater((stopped[2] - start) * direction, 0.8)
    case.assertGreater((face - stopped[2]) * direction, 0.4)
    case.assertLess((face - stopped[2]) * direction, 0.7)
    case.assertEqual(case.other.mover.velocity[2], 0)
    case.assertEqual(deployment_health(deployment), health)
    case.assertEqual(case.health(case.rein), 500)
    case.assertFalse(controlled(fight, case.rein.body))
    if expiry is not None:
        for _ in range(9):
            case.run.step(hold)
            if fight._barrier_contact(case.other, origin, heading, 15) is None:
                break
        case.assertIsNone(fight._barrier_contact(case.other, origin, heading, 15))
        case.assertLessEqual(fight.tick - began, 9)
        for _ in range(8):
            case.run.step(hold)
        case.assertEqual(tuple(case.other.mover.position), stopped)
        case.assertEqual(case.health(case.rein), 500)
        case.assertFalse(controlled(fight, case.rein.body))
        case.assertEqual(case.other.client.messages(combat.HIT), [])
    return hold, stopped


class HammerTests(unittest.TestCase):
    def setUp(self):
        self.run = Run(KANEZAKA_DM, ("Reinhardt", "Enemy", "Other"))
        self.match = self.run.match
        self.match.bots.clear()
        self.rein, self.enemy, self.other = self.run.players
        self.match.switch_hero(self.rein, heroes()[combat.REINHARDT])
        y = self.match.game_map.spawns[0].position[1]
        self.rein.mover.position = [0.0, y, 0.0]
        self.enemy.mover.position = [0.0, y, 3.0]
        self.other.mover.position = [0.5, y, 2.0]
        for _ in range(30):
            self.run.step({})

    def health(self, player):
        target = next(t for t in self.match.combat._targets() if t.owner is player)
        return self.match.combat.vitals_of(target).total

    @staticmethod
    def place(player, position):
        player.mover.position = list(position)
        player.mover.body = None  # recreate the stand-in physics capsule at this fixture's pose

    def advance(self, frames, buttons=1, yaw=0, pitch=0):
        for _ in range(frames):
            self.run.step({self.rein: (yaw, pitch, buttons)})

    def test_authored_windup_and_one_hit_per_victim_across_overlapping_queries(self):
        self.advance(20)
        self.assertEqual(self.health(self.enemy), 200)
        self.advance(20)
        self.assertEqual(self.health(self.enemy), 115)
        self.assertEqual(self.health(self.other), 115)
        self.advance(12)
        self.assertEqual(self.health(self.enemy), 115)
        hits = self.rein.client.messages(combat.HIT)
        self.assertEqual([h["+0x78"]["+0x4"] for h in hits], [85, 85])

    def test_flank_shield_bash_cancels_hammer_and_alias_before_contact(self):
        for button in (1, 0x800):
            with self.subTest(button=button):
                self.setUp()
                self.assertTrue(self.match.switch_hero(self.other, heroes()[0x02E0000000000195]))
                self.place(self.enemy, (0, 1, 3))
                self.place(self.other, (2.7, 1, 0))
                hold = {self.other: (-16384, 0, 2)}
                for _ in range(35):
                    self.run.step(hold)
                self.run.step({**hold, self.rein: (0, 0, button)})
                for _ in range(12):
                    self.run.step(hold)
                self.assertEqual(self.health(self.enemy), 200)
                self.run.step({self.other: (-16384, 0, 3)})
                self.run.step(hold)
                fight = self.match.combat
                self.assertEqual(self.health(self.rein), 497.5)
                self.assertEqual(control_deadline(fight, self.rein.body) - fight.tick, 47)
                for _ in range(70):
                    self.run.step(hold)
                self.assertFalse(controlled(fight, self.rein.body))
                self.assertEqual(self.health(self.enemy), 200)
                self.assertEqual(self.rein.client.messages(combat.HIT), [])

                self.place(self.other, (12, 1, 0))
                self.run.step({**hold, self.rein: (0, 0, button)})
                for _ in range(50):
                    self.run.step(hold)
                self.assertEqual(self.health(self.enemy), 115)
                self.assertEqual(len(self.rein.client.messages(combat.HIT)), 1)

    def test_static_wall_blocks_primary_and_melee_alias_without_hit_notices(self):
        for buttons in (1, 0x800):
            with self.subTest(buttons=buttons):
                self.setUp()
                charge_wall(self, 1)
                self.advance(45, buttons=buttons)
                self.assertEqual(self.health(self.enemy), 200)
                self.assertEqual(self.health(self.other), 200)
                self.assertEqual(self.rein.client.messages(combat.HIT), [])
                self.assertEqual(self.enemy.mover.position[0], 0)
                self.assertEqual(self.other.mover.position[0], .5)

    def test_hammer_and_melee_alias_hit_a_replacement_during_the_same_swing(self):
        for button in (1, 0x800):
            with self.subTest(button=button):
                self.setUp()
                self.place(self.other, (10, 1, 0))
                old = self.enemy.body
                fight = self.match.combat
                self.advance(1, buttons=button)
                for _ in range(48):
                    self.advance(1, buttons=0)
                    if self.health(self.enemy) < 200:
                        break
                self.assertEqual(self.health(self.enemy), 115)
                [(attack, (counter, _))] = fight.hammer_hits[self.rein.body].items()

                self.assertTrue(self.match.switch_hero(self.enemy, self.enemy.hero))
                self.assertNotEqual(self.enemy.body, old)
                with patch.object(self.enemy.client, 'body_id_available', side_effect=lambda entity: entity == old):
                    self.assertTrue(self.match.switch_hero(self.enemy, self.enemy.hero))
                self.assertEqual(self.enemy.body, old)
                self.place(self.enemy, (0, 1, 3))
                self.assertEqual(self.health(self.enemy), 200)
                for _ in range(10):
                    self.advance(1, buttons=0)
                    if self.health(self.enemy) < 200:
                        break
                self.assertEqual(self.health(self.enemy), 115)
                self.assertEqual(fight.hammer_hits[self.rein.body][attack][0], counter)
                self.advance(35, buttons=0)
                self.assertEqual(self.health(self.enemy), 115)
                notices = self.rein.client.messages(combat.HIT)
                self.assertEqual([notice['+0x78']['+0x4'] for notice in notices], [85, 85])

    def test_removing_occlusion_during_active_swing_allows_one_late_hit(self):
        charge_wall(self, 1)
        self.advance(35)
        self.assertEqual(self.health(self.enemy), 200)
        world = flat_world(1)
        self.match.collision_world = world
        self.match.collision_provider.state = LoadState("ready", world)
        for player in self.run.players:
            player.mover.use(world=world)
        self.advance(8)
        self.assertEqual(self.health(self.enemy), 115)
        self.advance(5)
        self.assertEqual(self.health(self.enemy), 115)

    def test_later_swing_can_hit_again_and_kill(self):
        self.advance(200)
        self.assertEqual(self.health(self.enemy), 0)
        self.assertTrue(self.match.combat.is_dead(self.enemy))
        self.assertTrue(self.enemy.body_script.component.owner_dead)

    def test_allies_and_rear_or_distant_targets_are_excluded(self):
        for button in (1, 0x800):
            with self.subTest(button=button):
                self.setUp()
                self.match.game_map = replace(self.match.game_map, free_for_all=False)
                self.enemy.team = self.rein.team
                self.other.team = self.rein.team + 1
                y = self.rein.mover.position[1]
                self.place(self.other, (0.0, y, -3.0))
                self.advance(5, buttons=0)
                self.advance(50, buttons=button)
                self.assertEqual(self.health(self.enemy), 200)
                self.assertEqual(self.health(self.other), 200)
                self.place(self.other, (0.0, y, 7.0))
                self.advance(80, buttons=button)
                self.assertEqual(self.health(self.other), 200)
                self.assertFalse(self.rein.client.messages(combat.HIT))

    def test_turning_and_late_entry_during_active_swing(self):
        y = self.rein.mover.position[1]
        self.place(self.enemy, (7.0, y, 0.0))
        self.place(self.other, (-7.0, y, 0.0))
        self.advance(30, yaw=16384)
        self.assertEqual(self.health(self.enemy), 200)
        self.place(self.enemy, (3.0, y, 0.0))
        self.advance(10, yaw=16384)
        self.assertEqual(self.health(self.enemy), 115)
        self.assertEqual(self.health(self.other), 200)

    def test_alternating_hammer_impulses_move_grounded_and_airborne_players(self):
        for button in (1, 0x800):
            with self.subTest(button=button):
                self.setUp()
                self.place(self.other, (10, 1, 0))
                for _ in range(45):
                    self.advance(1, buttons=button)
                    if self.health(self.enemy) < 200:
                        break
                self.assertEqual(self.health(self.enemy), 115)
                self.assertGreater(self.enemy.mover.velocity[0], 0)
                self.advance(15, buttons=button)
                self.assertGreater(self.enemy.mover.position[0], 0)
                self.assertEqual(self.health(self.enemy), 115)
                self.place(self.enemy, (10, 1, 0))
                self.advance(20, buttons=button)
                self.place(self.other, (0, 1, 3))
                self.advance(12, buttons=button)
                self.run.step({self.rein: (0, 0, button), self.other: (0, 0, JUMP)})
                for _ in range(70):
                    self.advance(1, buttons=button)
                    if self.health(self.other) < 200:
                        break
                self.assertEqual(self.health(self.other), 115)
                self.assertTrue(self.other.mover.airborne)
                self.assertLess(self.other.mover.velocity[0], 0)
                self.advance(65, buttons=0)
                self.assertLess(self.other.mover.position[0], 0)
                self.assertAlmostEqual(self.other.mover.velocity[0], 0, places=2)
                self.assertEqual(self.health(self.other), 115)

    def test_hammer_impulse_respects_a_lateral_wall_without_blocking_the_hit(self):
        travel = []
        for blocked in (False, True):
            with self.subTest(blocked=blocked):
                self.setUp()
                self.place(self.other, (10, 1, 0))
                if blocked:
                    x = self.enemy.mover.data.stand_radius + .03
                    world = World([
                        (-50, 1, -50, -50, 1, 50, 50, 1, 50),
                        (-50, 1, -50, 50, 1, 50, 50, 1, -50),
                        (x, 1, 2, x, 5, 4, x, 5, 2),
                        (x, 1, 2, x, 1, 4, x, 5, 4),
                    ])
                    self.match.collision_world = world
                    self.match.collision_provider.state = LoadState("ready", world)
                    for player in self.run.players:
                        player.mover.use(world=world)
                self.advance(1, buttons=1)
                self.advance(48, buttons=0)
                self.assertEqual(self.health(self.enemy), 115)
                travel.append(self.enemy.mover.position[0])
        self.assertGreater(travel[0], .05)
        self.assertLess(travel[1], .06)
        self.assertGreater(travel[0], travel[1])

    def test_hammer_impulse_finishes_after_attacker_disconnects(self):
        self.place(self.other, (10, 1, 0))
        for _ in range(45):
            self.advance(1)
            if self.health(self.enemy) < 200:
                break
        self.assertEqual(self.health(self.enemy), 115)
        self.assertGreater(self.enemy.mover.velocity[0], 0)
        before = self.enemy.mover.position[0]
        self.match.leave(self.rein)
        self.run.players.remove(self.rein)
        for _ in range(35):
            self.run.step({})
        self.assertGreater(self.enemy.mover.position[0], before)
        self.assertAlmostEqual(self.enemy.mover.velocity[0], 0, places=2)
        self.assertEqual(self.health(self.enemy), 115)

    def test_alternating_hammer_rings_resolve_pose_dependent_origins(self):
        self.place(self.rein, (2, 1, 4))
        values = {}
        for _ in range(125):
            self.advance(1, buttons=0x800, yaw=16384)
            hammer = next(i for i in self.rein.body_script.component.instances.values()
                          if i.graph.index == 0x48)
            for index in (3, 4):
                state = hammer.state(index)
                if state is None or not state.active or index in values:
                    continue
                modify = state.node.field("m_modifyHealth")
                [ring] = modify["m_knockbackRings"]
                keys = ("m_C0139FA6", "m_724CBA57")
                origins = [state.evaluate(modify[key], watch=False) for key in keys]
                values[index] = (state.evaluate(ring["m_9102DA3F"], watch=False), origins)
        self.assertEqual(set(values), {3, 4})
        for index, strength, expected in ((3, 4, (24, 9)), (4, 1, (-16, -1))):
            actual, origins = values[index]
            self.assertEqual(actual, strength)
            for vector, z in zip(origins, expected, strict=True):
                self.assertAlmostEqual(vector.x, 5, places=4)
                self.assertAlmostEqual(vector.y, 1, places=4)
                self.assertAlmostEqual(vector.z, z, places=4)

    def test_disconnect_retires_primary_and_melee_swings_before_any_late_hit(self):
        for button in (1, 0x800):
            for first_hit in (False, True):
                with self.subTest(button=button, first_hit=first_hit):
                    self.setUp()
                    self.place(self.other, (0, 1, 8))
                    if not first_hit:
                        self.place(self.enemy, (0, 1, 8))
                    self.advance(35, buttons=button)
                    fight = self.match.combat
                    old = self.rein.body
                    script = self.rein.body_script
                    hammer = next(i for i in script.component.instances.values() if i.graph.index == 0x48)
                    self.assertTrue(any(hammer.state(index).active for index in (3, 4)))
                    self.assertIn(old, fight.hammer_hits)
                    expected = 115 if first_hit else 200
                    self.assertEqual(self.health(self.enemy), expected)
                    self.match.leave(self.rein)
                    self.run.players.remove(self.rein)
                    self.assertTrue(script.retired)
                    self.assertNotIn(old, fight.hammer_hits)
                    self.place(self.enemy, (0, 1, 2))
                    self.place(self.other, (.5, 1, 2))
                    for _ in range(35):
                        self.run.step({})
                    self.assertEqual(self.health(self.enemy), expected)
                    self.assertEqual(self.health(self.other), 200)
                    self.assertNotIn(old, fight.hammer_hits)

    def test_retirement_discards_deduplication_state(self):
        self.advance(40)
        old = self.rein.body
        self.assertIn(old, self.match.combat.hammer_hits)
        self.match.switch_hero(self.rein, self.rein.hero)
        self.assertNotIn(old, self.match.combat.hammer_hits)

    def test_melee_key_runs_the_same_hammer_damage_and_windup(self):
        timelines = {}
        for button in (1, 0x800):
            with self.subTest(button=button):
                self.setUp()
                hammer = next(i for i in self.rein.body_script.component.instances.values() if i.graph.index == 0x48)
                active_frames, hit_frames = [], []
                previous = self.health(self.enemy)
                for frame in range(1, 81):
                    self.advance(1, buttons=button if frame <= 40 else 0)
                    if any(state.active for index, state in hammer.states.items() if index in (3, 4)):
                        active_frames.append(frame)
                    health = self.health(self.enemy)
                    if health != previous:
                        hit_frames.append((frame, previous - health))
                        previous = health
                    if frame == 40:
                        self.assertEqual(health, 115)
                        self.assertEqual(self.rein.body_script.buttons, button)
                self.assertTrue(active_frames)
                self.assertLess(active_frames[-1], 80)
                [(hit_frame, damage)] = hit_frames
                self.assertGreater(hit_frame, 20)
                self.assertIn(hit_frame, active_frames)
                self.assertEqual(damage, 85)
                timelines[button] = (active_frames, hit_frames)
        self.assertEqual(timelines[1], timelines[0x800])

    def test_melee_primary_handoff_keeps_attack_held_without_a_duplicate_hit(self):
        self.advance(35, buttons=0x800)
        self.assertEqual(self.health(self.enemy), 115)
        self.advance(5, buttons=0x801)
        self.advance(5, buttons=1)
        self.assertEqual(self.health(self.enemy), 115)
        self.advance(65, buttons=1)
        self.assertEqual(self.health(self.enemy), 30)

    def test_hammer_kills_retained_npc_without_respawn_or_further_hits(self):
        # A real existing training NPC, not an invented Talon profile.
        record = bots.table()["heroes"]["0x02E000000000016B"]
        y = self.rein.mover.position[1]
        npc = bots.Bot(bots.BOT_ENTITY, {
            "hero": "0x02E000000000016B", "team": 1,
            "position": (0.0, y, 3.0), "rotation": (0, 0, 0, 1), "graphs": [],
        }, record, {})
        npc.retain_corpse = True
        self.match.bots.append(npc)
        self.advance(200)
        self.assertTrue(npc.dead)
        target = next(t for t in self.match.combat._targets() if t.owner is npc)
        self.assertEqual(self.match.combat.vitals_of(target).total, 0)
        self.assertFalse(any(who is npc for _, who in self.match.combat.respawns))
        hits = len(self.rein.client.messages(combat.HIT))
        self.advance(210)
        self.assertIn(npc, self.match.bots)
        self.assertEqual(npc.movement(self.run.frame).flags, bots.DEAD)
        self.assertEqual(len(self.rein.client.messages(combat.HIT)), hits)

    def test_enemy_rifle_hits_reinhardt_armour_through_real_commands(self):
        self.place(self.other, (10.0, 1.0, 0.0))
        yaw, pitch = aim(self.match.combat._eye(self.enemy), (0.0, 2.0, 0.0))
        self.run.step({self.enemy: (yaw, pitch, 1)})
        self.assertEqual(self.health(self.rein), 485)  # native pools300+200, shared armour reduction5
        self.assertEqual(self.rein.body_script.component.entity_var(253).value(), 485)

    def test_dead_owner_cannot_damage_with_an_active_swing(self):
        for button in (1, 0x800):
            with self.subTest(button=button):
                self.setUp()
                self.place(self.other, (8, 1, 0))
                self.advance(35, buttons=button)
                self.assertEqual(self.health(self.enemy), 115)
                fight = self.match.combat
                target = fight.vitals_of(fight._target_entity(self.rein.body))
                target.damage(target.total - 1, armour_scalar=1.0)
                yaw, pitch = aim(fight._eye(self.other), fight._eye(self.rein))
                self.run.step({self.other: (yaw, pitch, 1)})
                self.assertTrue(fight.is_dead(self.rein))
                self.assertEqual(target.killer, self.other.body)
                self.place(self.other, (0, 1, 2))
                self.advance(100, buttons=button)
                self.assertEqual(self.health(self.other), 200)
                self.assertEqual(self.health(self.enemy), 115)
                self.assertEqual(len(self.rein.client.messages(combat.HIT)), 1)

    def test_reinhardt_melee_hits_brigitte_shield_once_per_swing(self):
        for case, button, z, expected in (
            ("primary", 1, 3, (165, 120)), ("alias", 0x800, 3, (165, 120)),
            ("range", 1, 5.5, (250, 200)), ("cover", 1, 3, (250, 200)),
            ("ally", 1, 3, (250, 200)), ("lowered", 1, 3, (250, 120)),
        ):
            with self.subTest(case=case):
                self.setUp()
                self.assertTrue(self.match.switch_hero(self.enemy, heroes()[0x02E0000000000195]))
                self.place(self.enemy, (0, 1, z))
                self.place(self.other, (20, 1, 0))
                fight = self.match.combat
                hold = {self.enemy: (-32768, 0, 2)}
                for _ in range(35):
                    self.run.step(hold)
                shield = brigitte_shield(fight, self.enemy.body)
                self.assertTrue(shield.raised())
                if case == "cover":
                    charge_wall(self, z=1.5)
                elif case == "ally":
                    self.match.game_map = replace(self.match.game_map, free_for_all=False)
                    self.enemy.team = self.rein.team
                elif case == "lowered":
                    hold = {self.enemy: (-32768, 0, 0)}
                    for _ in range(10):
                        self.run.step(hold)
                    self.assertFalse(shield.raised())
                self.run.step({**hold, self.rein: (0, 0, button)})
                for _ in range(43):
                    self.run.step(hold)
                self.assertEqual((shield.current, self.health(self.enemy)), expected)

    def test_active_hammer_hits_replacement_brigitte_shield_with_reused_body_id(self):
        hero = heroes()[0x02E0000000000195]
        self.assertTrue(self.match.switch_hero(self.enemy, hero))
        self.place(self.enemy, (0, 1, 3))
        self.place(self.other, (20, 1, 0))
        hold = {self.enemy: (-32768, 0, 2)}
        for _ in range(35):
            self.run.step(hold)
        fight = self.match.combat
        old = self.enemy.body
        previous = brigitte_shield(fight, old)
        self.run.step({**hold, self.rein: (0, 0, 1)})
        for _ in range(45):
            self.run.step(hold)
            if previous.current < 250:
                break
        self.assertEqual(previous.current, 165)
        records = fight.hammer_hits[self.rein.body]
        [(attack, (counter, _))] = records.items()
        self.assertTrue(self.match.switch_hero(self.enemy, heroes()[SOLDIER]))
        with patch.object(self.enemy.client, "body_id_available", side_effect={old}.__contains__):
            self.assertTrue(self.match.switch_hero(self.enemy, hero))
        self.assertEqual(self.enemy.body, old)
        for _ in range(25):
            self.place(self.enemy, (0, 1, 3))
            self.run.step(hold)
            replacement = brigitte_shield(fight, old)
            if replacement.current < 250:
                break
        self.assertIsNot(replacement, previous)
        self.assertTrue(replacement.raised())
        self.assertEqual(replacement.current, 165)
        self.assertEqual(self.health(self.enemy), 120)
        self.assertEqual(records[attack][0], counter)
        self.assertEqual(previous.current, 165)

    def test_actual_earthshatter_kills_brigitte_and_cancels_ordinary_bash(self):
        self.assertTrue(self.match.switch_hero(self.enemy, heroes()[0x02E0000000000195]))
        self.assertTrue(self.match.switch_hero(self.other, heroes()[combat.REINHARDT]))
        self.place(self.enemy, (0, 1, 6))
        self.place(self.other, (0, 1, 8))
        fight = self.match.combat
        hold = {self.enemy: (-32768, 0, 2), self.other: (-32768, 0, 0)}
        for _ in range(35):
            self.run.step(hold)
        self.other.body_script.set_ult_charge(1540)
        self.run.step({**hold, self.other: (-32768, 0, 32)})
        for _ in range(25):
            self.run.step(hold)
        self.assertEqual(self.health(self.enemy), 200)
        self.run.step({**hold, self.enemy: (-32768, 0, 3)})
        dash = brigitte_shield(fight, self.enemy.body).instance.states[14]
        self.assertTrue(dash.active)
        self.run.step(hold)
        self.assertTrue(dash.active)
        self.assertLess(self.enemy.mover.position[2], 6)
        self.run.step(hold)
        self.assertTrue(fight.is_dead(self.enemy))
        self.assertFalse(dash.active)
        stopped = tuple(self.enemy.mover.position)
        for _ in range(10):
            self.run.step(hold)
        self.assertEqual(tuple(self.enemy.mover.position), stopped)
        self.assertEqual(self.health(self.rein), 500)
        self.assertEqual(self.enemy.client.messages(combat.HIT), [])

    def test_shield_bash_stops_at_allied_and_enemy_cryo_without_late_hit(self):
        for allied in (False, True):
            with self.subTest(allied=allied):
                self.setUp()
                self.assertTrue(self.match.switch_hero(self.enemy, heroes()[0x02E0000000000195]))
                self.assertTrue(self.match.switch_hero(self.other, heroes()[combat.defenses.MEI]))
                self.place(self.enemy, (0, 1, 5))
                self.place(self.other, (0, 1, 2.3))
                if allied:
                    self.match.game_map = replace(self.match.game_map, free_for_all=False)
                    self.rein.team = 0
                    self.enemy.team = self.other.team = 1
                hold = {self.enemy: (-32768, 0, 2)}
                self.run.step({**hold, self.other: (0, 0, 8)})
                for _ in range(35):
                    self.run.step(hold)
                self.assertTrue(combat.defenses.control_immune(self.other))
                fight = self.match.combat
                self.run.step({self.enemy: (-32768, 0, 3)})
                dash = brigitte_shield(fight, self.enemy.body).instance.states[14]
                self.assertTrue(dash.active)
                for _ in range(9):
                    self.run.step(hold)
                    if not dash.active:
                        break
                self.assertFalse(dash.active)
                self.assertEqual(self.enemy.mover.velocity[2], 0)
                self.assertGreater(self.enemy.mover.position[2], 2.3)
                self.assertLess(self.enemy.mover.position[2], 4.8)
                stopped = tuple(self.enemy.mover.position)
                self.run.step({**hold, self.other: (0, 0, 2)})
                for _ in range(12):
                    self.run.step(hold)
                self.assertFalse(combat.defenses.control_immune(self.other))
                self.assertEqual(tuple(self.enemy.mover.position), stopped)
                self.assertEqual(self.health(self.rein), 500)
                self.assertEqual(self.health(self.other), 250)
                self.assertFalse(controlled(fight, self.rein.body))
                self.assertEqual(self.enemy.client.messages(combat.HIT), [])

    def test_actual_flank_shield_bash_releases_charge_victim_before_wall(self):
        self.assertTrue(self.match.switch_hero(self.other, heroes()[0x02E0000000000195]))
        self.place(self.other, (12, 1, 8))
        self.place(self.enemy, (0, 1, 4))
        charge_wall(self, z=12)
        fight = self.match.combat
        hold = {self.other: (-16384, 0, 2)}
        for _ in range(35):
            self.run.step(hold)
        self.run.step({**hold, self.rein: (0, 0, 8)})
        for _ in range(95):
            if self.rein.mover.position[2] >= 7.5:
                break
            self.run.step(hold)
        self.assertGreaterEqual(self.rein.mover.position[2], 7.5)
        self.assertIn(self.rein.body, fight.charges)
        self.assertTrue(pinned(fight, self.enemy.body))
        self.assertEqual(fight.charges[self.rein.body].pinned, self.enemy.body)
        self.assertEqual(self.health(self.enemy), 200)

        self.place(self.other, (2.7, 1, self.rein.mover.position[2] + .5))
        self.run.step({self.other: (-16384, 0, 3)})
        self.run.step(hold)
        self.assertEqual(self.health(self.rein), 497.5)
        self.assertEqual(control_deadline(fight, self.rein.body) - fight.tick, 47)
        self.assertFalse(pinned(fight, self.enemy.body))
        self.assertFalse(controlled(fight, self.enemy.body))
        self.assertFalse(fight.charges)
        self.assertFalse(fight.charges)
        for _ in range(65):
            self.run.step(hold)
        self.assertFalse(controlled(fight, self.rein.body))
        self.assertLess(self.enemy.mover.position[2], 12)
        self.assertEqual(self.health(self.enemy), 200)
        self.assertEqual(self.rein.client.messages(combat.HIT), [])


class FireStrikeTests(unittest.TestCase):
    setUp = HammerTests.setUp
    health = HammerTests.health
    place = staticmethod(HammerTests.place)
    advance = HammerTests.advance

    def cast(self):
        self.advance(1, buttons=16)
        self.advance(90, buttons=0)

    def ability(self):
        return next(i for i in self.rein.body_script.component.instances.values()
                    if i.graph.index == 0xE2).states[9]

    def test_command_releases_after_authored_windup_and_penetrates_two_victims_once(self):
        self.place(self.other, (0.0, 1.0, 6.0))
        self.advance(30, buttons=16)
        self.assertEqual(self.health(self.enemy), 200)
        self.assertFalse(self.match.combat.rockets)
        self.advance(3, buttons=0)
        [shot] = self.match.combat.rockets
        self.assertEqual(shot.speed, 25)
        self.assertAlmostEqual(shot.penetration_radius, 0.85)
        self.advance(55, buttons=0)
        self.assertEqual(self.health(self.enemy), 100)
        self.assertEqual(self.health(self.other), 100)
        self.assertEqual(len(shot.penetrated), 2)
        self.assertFalse(self.ability().flags & 1)
        self.assertGreater(self.ability().remaining(), 5.5)

    def test_fire_strike_spends_brigitte_shield_pool_and_pierces_the_wearer(self):
        self.assertTrue(self.match.switch_hero(self.enemy, heroes()[0x02E0000000000195]))
        self.place(self.enemy, (0, 1, 5))
        self.place(self.other, (0, 1, 9))
        hold = {self.enemy: (-32768, 0, 2)}
        for _ in range(35):
            self.run.step(hold)
        fight = self.match.combat
        shield = brigitte_shield(fight, self.enemy.body)
        self.assertTrue(shield.raised())
        self.assertEqual(shield.current, 250)
        self.run.step({**hold, self.rein: (0, 0, 16)})
        for _ in range(90):
            self.run.step(hold)
        self.assertEqual(shield.current, 150)
        self.assertEqual(self.health(self.enemy), 105)
        self.assertEqual(self.health(self.other), 100)
        self.assertEqual(shield.instance.find_var(0, 586).value(), 150)
        self.advance(40, buttons=0)
        self.assertFalse(shield.raised())
        self.assertEqual(shield.current, 150)
        self.advance(210, buttons=0)
        self.assertEqual(shield.current, 250)

    def test_flank_shield_bash_interrupts_windups_but_not_released_fire_strike_or_shatter(self):
        for button, damage in ((16, 100), (32, 50)):
            for released in (False, True):
                with self.subTest(button=button, released=released):
                    self.setUp()
                    self.assertTrue(self.match.switch_hero(self.enemy, heroes()[0x02E0000000000195]))
                    self.place(self.enemy, (2.7, 1, 0))
                    self.place(self.other, (0, 1, 12))
                    fight = self.match.combat
                    hold = {self.enemy: (-16384, 0, 2)}
                    for _ in range(35):
                        self.run.step(hold)

                    def launched():
                        if button == 32:
                            return fight.shatters.get(self.rein.body)
                        return next((shot for shot in fight.rockets if shot.shooter is self.rein), None)

                    if button == 32:
                        self.rein.body_script.set_ult_charge(1540)
                    self.run.step({**hold, self.rein: (0, 0, button)})
                    if button == 16:
                        self.assertTrue(self.ability().flags & self.ability().RUNNING)
                    else:
                        self.assertEqual(self.rein.body_script.component.entity_var(342).value(), 0)
                    if released:
                        for _ in range(40):
                            if launched() is not None:
                                break
                            self.run.step(hold)
                    original = launched()
                    self.assertEqual(original is not None, released)
                    self.assertEqual(self.health(self.other), 200)

                    self.run.step({self.enemy: (-16384, 0, 3)})
                    self.run.step(hold)
                    self.assertEqual(self.health(self.rein), 497.5)
                    self.assertEqual(control_deadline(fight, self.rein.body) - fight.tick, 47)
                    self.assertIs(launched(), original)
                    if button == 16:
                        self.assertFalse(self.ability().flags & self.ability().RUNNING)
                        if released:
                            self.assertGreater(self.ability().remaining(), 5)
                        else:
                            self.assertEqual(self.ability().remaining(), 0)
                    for _ in range(75):
                        self.run.step(hold)
                        if not released:
                            self.assertIsNone(launched())
                    self.assertFalse(controlled(fight, self.rein.body))
                    expected = 200 - damage if released else 200
                    self.assertEqual(self.health(self.other), expected)
                    self.assertEqual(len(self.rein.client.messages(combat.HIT)), int(released))

                    self.place(self.enemy, (12, 1, 0))
                    self.run.step({**hold, self.rein: (0, 0, button)})
                    for _ in range(65):
                        self.run.step(hold)
                    fresh_fire_strike = button == 16 and not released
                    self.assertEqual(self.health(self.other), expected - 100 * fresh_fire_strike)
                    self.assertEqual(len(self.rein.client.messages(combat.HIT)),
                                     int(released) + int(fresh_fire_strike))

    def test_enemy_earthshatter_cancels_windup_and_fresh_fire_strike_recovers(self):
        self.assertTrue(self.match.switch_hero(self.other, heroes()[combat.REINHARDT]))
        self.place(self.other, (6, 1, 0))
        self.place(self.enemy, (0, 1, 10))
        self.advance(5, buttons=0)
        self.other.body_script.set_ult_charge(1540)
        self.run.step({self.other: (-16384, 0, 32)})
        for _ in range(26):
            self.run.step({self.other: (-16384, 0, 0)})
        fight = self.match.combat
        self.assertFalse(controlled(fight, self.rein.body))
        self.run.step({self.rein: (0, 0, 16), self.other: (-16384, 0, 0)})
        ability = self.ability()
        self.assertTrue(ability.flags & ability.RUNNING)
        for _ in range(29):
            self.run.step({self.other: (-16384, 0, 0)})
            self.assertFalse(fight.rockets)
            if controlled(fight, self.rein.body):
                break
        self.assertTrue(controlled(fight, self.rein.body))
        self.assertFalse(ability.flags & ability.RUNNING)

        for _ in range(600):
            if not controlled(fight, self.rein.body) and ability.remaining() == 0:
                break
            self.advance(1, buttons=0)
            self.assertFalse(fight.rockets)
        else:
            self.fail("Fire Strike did not recover")
        self.assertEqual(self.health(self.enemy), 200)
        self.cast()
        self.assertEqual(self.health(self.enemy), 100)
        hits = self.rein.client.messages(combat.HIT)
        self.assertEqual([hit["+0x78"]["+0x0"]["+0x0"] for hit in hits], [self.enemy.body])

    def test_projectile_thickness_hits_off_axis_but_misses_beyond_extent(self):
        self.place(self.enemy, (1.2, 1.0, 4.0))
        self.place(self.other, (1.7, 1.0, 6.0))
        self.cast()
        self.assertEqual(self.health(self.enemy), 100)
        self.assertEqual(self.health(self.other), 200)

    def test_allies_are_not_damaged_or_consume_penetration(self):
        self.match.game_map = self.match.game_map.__class__(
            **{**self.match.game_map.__dict__, "free_for_all": False}
        )
        self.enemy.team = self.rein.team
        self.other.team = self.rein.team + 1
        self.place(self.other, (0.0, 1.0, 6.0))
        self.cast()
        self.assertEqual(self.health(self.enemy), 200)
        self.assertEqual(self.health(self.other), 100)

    def test_fire_strike_filters_target_team_at_contact_after_launch(self):
        for front_starts_hostile in (False, True):
            with self.subTest(front_starts_hostile=front_starts_hostile):
                HammerTests.setUp(self)
                self.match.game_map = replace(self.match.game_map, free_for_all=False)
                self.rein.team, self.other.team = 0, 1
                self.enemy.team = int(front_starts_hostile)
                self.place(self.enemy, (0, 1, 6))
                self.place(self.other, (0, 1, 10))
                self.advance(1, buttons=16)
                self.advance(31, buttons=0)
                [shot] = self.match.combat.rockets
                self.assertFalse(shot.penetrated)
                self.assertEqual(self.health(self.enemy), 200)

                self.enemy.team = int(not front_starts_hostile)
                self.advance(85, buttons=0)
                self.assertEqual(self.health(self.enemy), 200 if front_starts_hostile else 100)
                self.assertEqual(self.health(self.other), 100)
                hits = self.rein.client.messages(combat.HIT)
                expected = [self.other.body] if front_starts_hostile else [self.enemy.body, self.other.body]
                self.assertEqual([hit["+0x78"]["+0x0"]["+0x0"] for hit in hits], expected)

    def test_real_static_wall_stops_flight_before_rear_target(self):
        self.place(self.other, (0.0, 1.0, 7.0))
        wall = World([
            (-50, 1, -50, -50, 1, 50, 50, 1, 50),
            (-50, 1, -50, 50, 1, 50, 50, 1, -50),
            (-5, 1, 4, -5, 6, 4, 5, 6, 4),
            (-5, 1, 4, 5, 6, 4, 5, 1, 4),
        ])
        self.match.collision_world = wall
        self.match.collision_provider.state = LoadState("ready", wall)
        self.cast()
        self.assertEqual(self.health(self.enemy), 100)
        self.assertEqual(self.health(self.other), 200)
        self.assertFalse(self.match.combat.rockets)

    def test_cooldown_rejects_new_cast_then_recovers(self):
        self.place(self.other, (10.0, 1.0, 0.0))
        self.cast()
        self.assertEqual(self.health(self.enemy), 100)
        ability = self.ability()
        self.advance(1, buttons=16)
        self.advance(30, buttons=0)
        self.assertFalse(ability.flags & 1)
        self.assertEqual(self.health(self.enemy), 100)
        while ability.remaining() > 0.0:
            self.advance(1, buttons=0)
        self.cast()
        self.assertEqual(self.health(self.enemy), 0)

    def test_death_during_windup_cancels_projectile_release(self):
        self.advance(10, buttons=16)
        target = next(t for t in self.match.combat._targets() if t.owner is self.rein)
        self.match.combat._damage(self.enemy, target, 1000, False, None, self.run.frame)
        self.advance(90, buttons=0)
        self.assertEqual(self.health(self.enemy), 200)
        self.assertFalse(self.match.combat.rockets)
        self.assertFalse(self.ability().flags & 1)

    def test_released_unleased_projectile_hits_after_owner_disconnects(self):
        self.place(self.enemy, (0, 1, 10))
        self.place(self.other, (0, 1, 14))
        self.advance(1, buttons=16)
        self.advance(32, buttons=0)
        fight = self.match.combat
        [shot] = fight.rockets
        self.assertIsNone(shot.shot)
        self.assertEqual(self.health(self.enemy), 200)
        self.assertEqual(self.health(self.other), 200)
        self.match.leave(self.rein)
        self.run.players.remove(self.rein)
        self.assertIn(shot, fight.rockets)
        for _ in range(250):
            self.run.step({})
        self.assertEqual(self.health(self.enemy), 100)
        self.assertEqual(self.health(self.other), 100)
        self.assertNotIn(shot, fight.rockets)

    def test_flight_expires_at_authored_range(self):
        self.place(self.enemy, (0.0, 1.0, 99.0))
        self.place(self.other, (0.0, 1.0, 103.0))
        self.advance(1, buttons=16)
        self.advance(300, buttons=0)
        self.assertEqual(self.health(self.enemy), 100)
        self.assertEqual(self.health(self.other), 200)
        self.assertFalse(self.match.combat.rockets)


class ReinhardtWindupRetirementTests(unittest.TestCase):
    setUp = HammerTests.setUp
    health = HammerTests.health
    place = staticmethod(HammerTests.place)
    advance = HammerTests.advance

    def test_enemy_rifle_kills_during_windup_and_same_hero_respawn_recovers(self):
        actions = (
            (1, 0x48, 13, 3, 115),
            (0x800, 0x48, 13, 3, 115),
            (16, 0xE2, 3, 6, 100),
            (8, 0x7D, 29, 3, 200),
            (32, 0xBE, 2, 10, 150),
        )
        for button, graph_index, state_index, distance, expected in actions:
            with self.subTest(button=button):
                self.setUp()
                self.place(self.enemy, (0, 1, -6))
                self.place(self.other, (0, 1, distance))
                fight = self.match.combat
                old_body = self.rein.body
                old_script = self.rein.body_script
                instance = next(i for i in old_script.component.instances.values()
                                if i.graph.index == graph_index)
                self.assertFalse(instance.state(state_index).active)
                cost = old_script.component.entity_var(338).value()
                if button == 32:
                    old_script.set_ult_charge(cost)
                held = button if button in (1, 0x800) else 0
                self.advance(1, buttons=button)
                self.advance(9, buttons=held)
                self.assertTrue(instance.state(state_index).active)
                if button == 32:
                    self.assertEqual(old_script.component.entity_var(342).value(), 0)
                self.assertEqual(self.health(self.other), 200)
                self.assertFalse(fight.rockets or fight.shatters or fight.charges)

                vitals = fight.vitals_of(fight._target_entity(old_body))
                vitals.damage(vitals.total - 1, armour_scalar=1.0)
                angles = aim(fight._eye(self.enemy), (0, 2.2, 0))
                self.run.step({self.rein: (0, 0, held), self.enemy: (*angles, 1)})
                self.assertTrue(vitals.dead)
                self.assertEqual(vitals.killer, self.enemy.body)
                for _ in range(800):
                    self.advance(1, buttons=0)
                    self.assertFalse(fight.rockets or fight.shatters or fight.charges)
                    if self.rein.body != old_body:
                        break
                self.assertNotEqual(self.rein.body, old_body)
                self.assertTrue(old_script.retired)
                self.assertEqual(self.rein.hero.guid, combat.REINHARDT)
                self.assertEqual(self.health(self.rein), 500)
                self.assertEqual(self.health(self.other), 200)
                self.assertFalse(controlled(fight, self.other.body))
                self.assertFalse(self.rein.client.messages(combat.HIT))

                self.place(self.rein, (0, 1, 0))
                self.advance(10, buttons=0)
                if button == 32:
                    fresh_script = self.rein.body_script
                    self.assertLess(fresh_script.component.entity_var(342).value(), cost)
                    fresh_script.set_ult_charge(cost)
                self.advance(1, buttons=button)
                self.advance(52, buttons=held)
                self.assertEqual(self.health(self.other), expected)
                if button == 8:
                    self.assertTrue(pinned(fight, self.other.body))
                elif button == 32:
                    self.assertTrue(controlled(fight, self.other.body))


class ChargeMovementTests(unittest.TestCase):
    health = HammerTests.health
    place = staticmethod(HammerTests.place)
    advance = HammerTests.advance

    def setUp(self):
        HammerTests.setUp(self)
        self.place(self.enemy, (100.0, 1.0, 0.0))
        self.place(self.other, (-100.0, 1.0, 0.0))

    def charge(self):
        return next(i for i in self.rein.body_script.component.instances.values() if i.graph.index == 0x7D)

    def start(self):
        self.advance(1, buttons=8)

    def test_authored_windup_speed_duration_and_cooldown_refusal_and_recovery(self):
        self.start()
        self.advance(36, buttons=0)
        self.assertEqual(self.rein.mover.position[2], 0)
        self.advance(11, buttons=0)
        self.assertAlmostEqual(self.rein.mover.position[2], 16.5 * .016 * 10, delta=.02)
        self.advance(200, buttons=0)
        position = tuple(self.rein.mover.position)
        self.assertAlmostEqual(position[2], 16.5 * 3, delta=.3)
        self.assertNotIn(self.rein.body, self.match.combat.charges)
        self.assertGreater(self.charge().find_var(0, 61).value(), 9)
        self.start()
        self.advance(80, buttons=0)
        self.assertEqual(tuple(self.rein.mover.position), position)
        self.advance(600, buttons=0)
        self.start()
        self.advance(60, buttons=0)
        self.assertGreater(self.rein.mover.position[2], position[2] + 5)

    def test_steering_is_rate_limited_and_pitch_does_not_launch_the_body(self):
        self.start()
        self.advance(40, buttons=0)
        self.advance(30, buttons=0, yaw=16384, pitch=-12000)
        _, heading = (self.match.combat.charges[self.rein.body].activation, self.match.combat.charges[self.rein.body].heading)
        self.assertAlmostEqual(heading, math.radians(30) * .016 * 30, places=6)
        self.assertGreater(self.rein.mover.position[0], 0)
        self.assertLess(self.rein.mover.position[0], 3)
        self.assertAlmostEqual(self.rein.mover.position[1], 1, places=3)

    def test_static_wall_stops_drive_and_runs_native_cooldown(self):
        wall = World([
            (-50, 1, -50, -50, 1, 50, 50, 1, 50),
            (-50, 1, -50, 50, 1, 50, 50, 1, -50),
            (-5, 1, 8, -5, 6, 8, 5, 6, 8),
            (-5, 1, 8, 5, 6, 8, 5, 1, 8),
        ])
        self.match.collision_world = wall
        self.match.collision_provider.state = LoadState("ready", wall)
        self.rein.mover.world = wall
        self.rein.mover.body = None
        self.start()
        self.advance(90, buttons=0)
        z = self.rein.mover.position[2]
        self.assertGreater(z, 7)
        self.assertLess(z, 8)
        self.assertNotIn(self.rein.body, self.match.combat.charges)
        self.assertGreater(self.charge().find_var(0, 61).value(), 9)
        self.advance(40, buttons=0)
        self.assertEqual(self.rein.mover.position[2], z)
        self.assertEqual(self.charge().find_var(0, 59).value(), 0)

    def test_second_press_does_not_add_an_ow2_manual_cancel(self):
        self.start()
        self.advance(60, buttons=0)
        z = self.rein.mover.position[2]
        self.start()
        self.advance(10, buttons=0)
        self.assertGreater(self.rein.mover.position[2], z + 2)
        self.assertIn(self.rein.body, self.match.combat.charges)

    def test_death_and_retirement_remove_drive(self):
        self.start()
        self.advance(60, buttons=0)
        fight = self.match.combat
        self.assertIn(self.rein.body, fight.charges)
        self.place(self.enemy, (5, 1, self.rein.mover.position[2]))
        target = fight.vitals_of(fight._target_entity(self.rein.body))
        target.damage(target.total - 1, armour_scalar=1.0)
        yaw, pitch = aim(fight._eye(self.enemy), fight._eye(self.rein))
        self.run.step({self.enemy: (yaw, pitch, 1)})
        self.assertTrue(target.dead)
        self.assertEqual(target.killer, self.enemy.body)
        position = tuple(self.rein.mover.position)
        # Dead owners skip poses, so lifecycle cleanup also runs in combat.command.
        self.advance(10, buttons=0)
        self.assertEqual(tuple(self.rein.mover.position), position)
        self.assertNotIn(self.rein.body, self.match.combat.charges)
        old = self.rein.body
        self.match.switch_hero(self.rein, self.rein.hero)
        self.assertNotIn(old, self.match.combat.charges)

    def test_horizontal_drive_keeps_gravity_and_vertical_momentum_on_release(self):
        mover = Mover((0, 10, 0), world=flat_world(0))
        for frame in range(1, 6):
            mover.step_pose(Command(frame), .016, frame, horizontal_drive=(0, 16.5))
        self.assertLess(mover.position[1], 10)
        self.assertGreater(mover.position[2], 1)
        before = mover.velocity[1]
        mover.step_pose(Command(6), .016, 6)
        self.assertLess(mover.velocity[1], before)
        self.assertEqual(mover.velocity[2], 0)

    def test_live_body_retirement_discards_heading_and_the_replacement_stays_idle(self):
        self.start()
        self.advance(60, buttons=0)
        old = self.rein.body
        self.assertIn(old, self.match.combat.charges)
        self.match.switch_hero(self.rein, self.rein.hero)
        self.assertNotIn(old, self.match.combat.charges)
        self.place(self.rein, (0, 1, 0))
        self.advance(60, buttons=0)
        self.assertEqual(self.rein.mover.position[2], 0)
        self.assertNotIn(self.rein.body, self.match.combat.charges)


def charge_wall(case, z=8):
    wall = World([
        (-50, 1, -50, -50, 1, 80, 50, 1, 80),
        (-50, 1, -50, 50, 1, 80, 50, 1, -50),
        (-5, 1, z, -5, 6, z, 5, 6, z),
        (-5, 1, z, 5, 6, z, 5, 1, z),
    ])
    case.match.collision_world = wall
    case.match.collision_provider.state = LoadState("ready", wall)
    for player in case.run.players:
        player.mover.use(world=wall)


class ChargeContactTests(unittest.TestCase):
    health = HammerTests.health
    place = staticmethod(HammerTests.place)
    advance = HammerTests.advance

    def setUp(self):
        HammerTests.setUp(self)
        self.place(self.other, (10, 1, 0))

    def cast(self, frames=60):
        self.advance(1, buttons=8)
        self.advance(frames, buttons=0)

    def test_frontal_capture_transports_without_initial_damage_and_expires_without_wall_damage(self):
        self.cast()
        fight = self.match.combat
        self.assertEqual(fight.charges[self.rein.body].pinned, self.enemy.body)
        self.assertTrue(controlled(fight, self.enemy.body))
        self.assertGreater(self.enemy.mover.position[2], 7)
        self.assertEqual(self.health(self.enemy), 200)
        self.advance(200, buttons=0)
        self.assertFalse(controlled(fight, self.enemy.body))
        self.assertFalse(fight.charges)
        self.assertEqual(self.health(self.enemy), 200)

    def test_charge_interrupts_actual_roadhog_hook_windup_and_whole_hog(self):
        for button in (8, 32):
            with self.subTest(button=button):
                self.setUp()
                roadhog = next(hero for hero in heroes().values() if hero.name == "Roadhog")
                self.assertTrue(self.match.switch_hero(self.enemy, roadhog))
                self.place(self.enemy, (0, 1, 6))
                charge_wall(self, z=11)
                self.advance(10, buttons=0)
                script = self.enemy.body_script
                fight = self.match.combat
                if button == 32:
                    script.set_ult_charge(script.component.entity_var(338).value())
                    ability, = script.ultimates
                else:
                    hook = next(i for i in script.component.instances.values() if i.graph.index == 0x01C8)
                    ability = next(s for s in hook.states.values() if s.node.pos == 11)
                self.advance(1, buttons=8)
                if button == 8:
                    for _ in range(60):
                        self.advance(1, buttons=0)
                        if self.rein.mover.position[2] >= 3.15:
                            break
                    self.assertIn(self.rein.body, fight.charges)
                    self.assertFalse(pinned(fight, self.enemy.body))
                self.run.step({self.enemy: (16384, 0, button)})
                self.assertTrue(ability.flags & ability.RUNNING)
                for _ in range(70):
                    self.advance(1, buttons=0)
                    if pinned(fight, self.enemy.body):
                        break
                self.assertTrue(pinned(fight, self.enemy.body))
                self.assertEqual(self.health(self.enemy), 600)
                self.assertFalse(ability.flags & ability.RUNNING)
                self.advance(45, buttons=0)
                self.assertFalse(pinned(fight, self.enemy.body))
                self.assertEqual(self.health(self.enemy), 300)
                self.assertFalse(ability.flags & ability.RUNNING)
                if button == 8:
                    self.assertFalse(any(s.node.pos == 12 and s.counter for s in hook.states.values()))
                    self.assertGreater(ability.remaining(), 0)
                    for _ in range(600):
                        self.advance(1, buttons=0)
                        if ability.remaining() <= 0:
                            break
                    self.assertLessEqual(ability.remaining(), 0)
                    self.run.step({self.enemy: (16384, 0, 8)})
                    for _ in range(20):
                        self.advance(1, buttons=0)
                        if any(s.node.pos == 12 and s.active for s in hook.states.values()):
                            break
                    launch = next(s for s in hook.states.values() if s.node.pos == 12)
                    self.assertTrue(launch.active)
                    self.assertEqual(launch.counter, 1)

    def test_live_charge_releases_target_that_joins_reinhardts_team(self):
        self.match.game_map = replace(self.match.game_map, free_for_all=False)
        self.rein.team, self.enemy.team = 0, 1
        charge_wall(self, 12)
        self.cast()
        fight = self.match.combat
        self.assertTrue(pinned(fight, self.enemy.body))
        position = tuple(self.enemy.mover.position)
        component = self.rein.body_script.component
        instance = next(i for i in component.instances.values() if i.graph.index == 0x7D)

        self.enemy.team = self.rein.team
        self.advance(1, buttons=0)
        self.assertFalse(pinned(fight, self.enemy.body))
        self.assertNotIn(self.rein.body, fight.charges)
        self.assertFalse(instance.states[3].active)
        self.assertEqual(tuple(self.enemy.mover.position), position)
        self.advance(80, buttons=0)
        self.assertEqual(self.health(self.enemy), 200)
        self.assertFalse(self.rein.client.messages(combat.HIT))

    def test_caster_team_change_releases_pin_and_recovery_admits_new_enemy(self):
        self.match.game_map = replace(self.match.game_map, free_for_all=False)
        self.rein.team, self.enemy.team, self.other.team = 0, 1, 0
        charge_wall(self, 12)
        self.cast()
        fight = self.match.combat
        body = self.rein.body
        victim = self.enemy.body
        instance = next(i for i in self.rein.body_script.component.instances.values() if i.graph.index == 0x7D)
        self.assertEqual(fight.charges[body].pinned, victim)
        self.assertTrue(pinned(fight, victim))
        victim_pose = tuple(self.enemy.mover.position)

        self.rein.team = self.enemy.team
        self.advance(1, buttons=0)
        self.assertFalse(pinned(fight, victim))
        self.assertNotIn(body, fight.charges)
        self.assertNotIn(body, fight.charges)
        self.assertFalse(instance.states[3].active)
        self.assertEqual(tuple(self.enemy.mover.position), victim_pose)
        self.assertEqual(self.health(self.enemy), 200)
        self.assertFalse(self.rein.client.messages(combat.HIT))

        cooldown = instance.find_var(0, 61)
        self.assertGreater(cooldown.value(), 8)
        self.advance(1, buttons=8)
        self.advance(60, buttons=0)
        self.assertNotIn(body, fight.charges)
        self.assertNotIn(body, fight.charges)
        self.assertGreater(cooldown.value(), 0)
        for _ in range(650):
            if cooldown.value() <= 0:
                break
            self.advance(1, buttons=0)
        self.assertLessEqual(cooldown.value(), 0)

        self.place(self.rein, (0, 1, 0))
        self.place(self.enemy, (0, 1, 3))
        self.place(self.other, (0, 1, 5))
        self.advance(1, buttons=8)
        for _ in range(85):
            self.advance(1, buttons=0)
            if pinned(fight, self.other.body):
                break
        contact = fight.charges[body]
        self.assertEqual(contact.pinned, self.other.body)
        self.assertNotIn(victim, {entity for entity, _ in contact.hit})
        self.assertFalse(pinned(fight, victim))
        self.advance(45, buttons=0)
        self.assertEqual(self.health(self.enemy), 200)
        self.assertEqual(self.health(self.other), 0)
        self.assertFalse(pinned(fight, self.other.body))
        self.assertNotIn(body, fight.charges)

    def test_side_contact_deals_native_50_once_and_cannot_replace_captured_target(self):
        self.place(self.other, (1.6, 1, 3))
        self.cast(100)
        self.assertEqual(self.health(self.other), 150)
        self.assertEqual(self.health(self.enemy), 200)
        self.assertEqual(self.match.combat.charges[self.rein.body].pinned, self.enemy.body)
        self.assertFalse(controlled(self.match.combat, self.other.body))
        self.advance(40, buttons=0)
        self.assertEqual(self.health(self.other), 150)

    def test_second_frontal_body_is_bumped_once_while_first_remains_pinned(self):
        self.place(self.other, (0, 1, 6))
        self.cast(100)
        self.assertEqual(self.health(self.other), 150)
        self.assertEqual(self.match.combat.charges[self.rein.body].pinned, self.enemy.body)

    def test_target_capsule_hits_wall_before_owner_and_applies_300_with_armour_rule(self):
        self.match.switch_hero(self.enemy, heroes()[combat.REINHARDT])
        self.place(self.enemy, (0, 1, 3))
        charge_wall(self)
        self.cast(100)
        self.assertEqual(self.health(self.enemy), 205)  # 300 native damage minus existing armour5
        self.assertLess(self.enemy.mover.position[2], 8)
        self.assertLess(self.rein.mover.position[2], self.enemy.mover.position[2] - .5)
        self.assertFalse(controlled(self.match.combat, self.enemy.body))
        self.assertFalse(self.match.combat.charges)
        self.advance(100, buttons=0)
        self.assertEqual(self.health(self.enemy), 205)

    def test_pin_wall_hit_notice_uses_the_impact_command_frame(self):
        charge_wall(self)
        self.advance(1, buttons=8)
        for _ in range(90):
            self.advance(1, buttons=0)
            hits = self.rein.client.messages(combat.HIT)
            if hits:
                self.assertEqual(len(hits), 1)
                self.assertEqual(hits[0]["+0x84"], self.run.frame)
                return
        self.fail("expected a pin wall impact")

    def test_solid_wall_excludes_target_even_within_broad_query(self):
        charge_wall(self, 2)
        self.cast(100)
        self.assertEqual(self.health(self.enemy), 200)
        self.assertEqual(self.enemy.mover.position[2], 3)
        self.assertFalse(controlled(self.match.combat, self.enemy.body))
        self.assertFalse(self.match.combat.charges)

    def test_allied_rear_and_lateral_misses_are_excluded(self):
        self.match.game_map = self.match.game_map.__class__(
            **{**self.match.game_map.__dict__, "free_for_all": False})
        self.enemy.team = self.rein.team
        self.other.team = self.rein.team + 1
        self.place(self.other, (0, 1, -3))
        self.cast(80)
        self.assertEqual(self.enemy.mover.position[2], 3)
        self.assertEqual(self.health(self.enemy), 200)
        self.assertEqual(self.health(self.other), 200)
        self.assertIsNone(self.match.combat.charges[self.rein.body].pinned)

    def test_pinned_target_cannot_shoot_or_escape_with_throttle_jump_and_crouch(self):
        self.cast()
        for _ in range(20):
            self.run.step({self.enemy: (-32768, 0, 1)})
        self.assertEqual(self.health(self.rein), 500)
        self.assertEqual(self.enemy.body_script.buttons, 0)
        self.run.frame += 1
        self.match.update(self.run.frame * .016, self.run.frame)
        before = tuple(self.enemy.mover.position)
        self.enemy.take_commands([Command(self.run.frame, forward=127, right=127, buttons=0x25)])
        self.assertAlmostEqual(math.dist(before, self.enemy.mover.position), 0, places=3)
        self.assertFalse(self.enemy.mover.crouched)

    def test_owner_death_and_live_retirement_release_captured_control(self):
        for retirement in (False, True):
            with self.subTest(retirement=retirement):
                self.setUp()
                self.cast()
                self.assertTrue(controlled(self.match.combat, self.enemy.body))
                if retirement:
                    self.match.switch_hero(self.rein, self.rein.hero)
                else:
                    target = self.match.combat._target_entity(self.rein.body)
                    self.match.combat._damage(self.other, target, 1000, False, None, self.run.frame)
                self.assertFalse(controlled(self.match.combat, self.enemy.body))
                self.assertFalse(self.match.combat.charges)

    def test_control_from_another_source_survives_pin_release_and_expires(self):
        self.cast()
        target = self.match.combat._target_entity(self.enemy.body)
        self.match.combat.stun(target, .5)
        self.match.combat.charge_blocked(self.rein, impact=False)
        self.assertTrue(controlled(self.match.combat, self.enemy.body))
        self.advance(40, buttons=0)
        self.assertFalse(controlled(self.match.combat, self.enemy.body))

    def test_actual_allied_earthshatter_survives_charge_owner_disconnect(self):
        self.match.game_map = self.match.game_map.__class__(
            **{**self.match.game_map.__dict__, "free_for_all": False})
        self.rein.team, self.other.team, self.enemy.team = 0, 0, 1
        self.assertTrue(self.match.switch_hero(self.other, heroes()[combat.REINHARDT]))
        self.assertTrue(self.match.switch_hero(self.enemy, heroes()[combat.REINHARDT]))
        self.place(self.other, (0, 1, 2))
        self.place(self.enemy, (0, 1, 3))
        self.advance(30, buttons=0)
        fight = self.match.combat
        target = self.enemy.body
        self.advance(1, buttons=8)
        for _ in range(55):
            self.advance(1, buttons=0)
            if pinned(fight, target):
                break
        self.assertTrue(pinned(fight, target))
        self.assertEqual(self.health(self.enemy), 500)
        self.other.body_script.set_ult_charge(1540)
        self.run.step({self.other: (0, 0, 32)})
        key = (target, self.other.body, "earthshatter")
        for _ in range(65):
            self.run.step({})
            if key in fight.controls:
                break
        self.assertIn(key, fight.controls)
        self.assertTrue(pinned(fight, target))
        self.assertEqual(self.health(self.enemy), 455)
        deadline = fight.controls[key]
        self.match.leave(self.rein)
        self.run.players.remove(self.rein)
        self.assertFalse(pinned(fight, target))
        self.assertEqual(fight.controls[key], deadline)
        self.assertTrue(controlled(fight, target))
        for _ in range(10):
            self.run.step({self.enemy: (0, 0, 1)})
            self.assertEqual(self.enemy.body_script.buttons, 0)
        for _ in range(160):
            self.run.step({})
        self.assertNotIn(key, fight.controls)
        self.assertFalse(controlled(fight, target))
        self.assertEqual(self.health(self.enemy), 455)

    def test_charge_pin_outlasts_an_earlier_actual_earthshatter(self):
        self.match.game_map = self.match.game_map.__class__(
            **{**self.match.game_map.__dict__, "free_for_all": False})
        self.rein.team, self.other.team, self.enemy.team = 0, 0, 1
        self.assertTrue(self.match.switch_hero(self.other, heroes()[combat.REINHARDT]))
        self.place(self.other, (0, 1, -2))
        self.place(self.enemy, (0, 1, 8))
        self.advance(30, buttons=0)
        fight = self.match.combat
        target = self.enemy.body
        self.other.body_script.set_ult_charge(1540)
        self.run.step({self.other: (0, 0, 32)})
        key = (target, self.other.body, "earthshatter")
        for _ in range(65):
            self.run.step({})
            if key in fight.controls:
                break
        self.assertIn(key, fight.controls)
        self.assertEqual(self.health(self.enemy), 150)
        self.advance(1, buttons=8)
        for _ in range(85):
            self.advance(1, buttons=0)
            if pinned(fight, target):
                break
        self.assertTrue(pinned(fight, target))
        self.assertIn(key, fight.controls)
        for _ in range(160):
            self.advance(1, buttons=0)
            if key not in fight.controls:
                break
        self.assertNotIn(key, fight.controls)
        self.assertTrue(pinned(fight, target))
        self.run.step({self.enemy: (0, 0, 1)})
        self.assertEqual(self.enemy.body_script.buttons, 0)
        self.advance(120, buttons=0)
        self.assertFalse(controlled(fight, target))
        self.assertEqual(self.health(self.enemy), 150)

    def test_stun_interrupts_owner_drive_and_releases_victim_without_wall_damage(self):
        self.cast()
        fight = self.match.combat
        fight.stun(fight._target_entity(self.rein.body), .5)
        self.assertFalse(controlled(fight, self.enemy.body))
        self.assertTrue(controlled(fight, self.rein.body))
        self.assertFalse(fight.charges)
        z = self.rein.mover.position[2]
        self.advance(1, buttons=0)  # old Charge throttle modifiers still cover this pose
        self.assertEqual(self.rein.mover.position[2], z)
        self.advance(99, buttons=0)
        self.assertEqual(self.rein.mover.position[2], z)
        self.assertEqual(self.health(self.enemy), 200)

    def test_captured_target_death_releases_control_without_delayed_wall_damage(self):
        self.cast()
        fight = self.match.combat
        fight._damage(self.other, fight._target_entity(self.enemy.body), 1000, False, None, self.run.frame)
        self.assertFalse(controlled(fight, self.enemy.body))
        self.assertIsNone(fight.charges[self.rein.body].pinned)
        before = len(self.rein.client.messages(combat.HIT))
        self.advance(100, buttons=0)
        self.assertEqual(len(self.rein.client.messages(combat.HIT)), before)

    def test_departed_owner_releases_pin_without_waiting_for_another_pose(self):
        self.cast()
        self.assertTrue(pinned(self.match.combat, self.enemy.body))
        self.rein.gone = True
        self.advance(1, buttons=0)
        self.assertFalse(pinned(self.match.combat, self.enemy.body))
        self.assertFalse(self.match.combat.charges)
        self.assertFalse(self.match.combat.charges)

    def test_owner_disconnect_releases_captured_player_on_the_same_tick(self):
        self.cast()
        fight = self.match.combat
        old = self.rein.body
        self.assertTrue(pinned(fight, self.enemy.body))
        tick = fight.tick
        self.match.leave(self.rein)
        self.run.players.remove(self.rein)
        self.assertEqual(fight.tick, tick)
        self.assertFalse(controlled(fight, self.enemy.body))
        self.assertNotIn(old, fight.charges)
        self.assertNotIn(old, fight.charges)
        self.assertNotIn(self.enemy.body, fight.knockbacks.active)
        before = tuple(self.enemy.mover.position)
        for _ in range(15):
            self.run.frame += 1
            self.match.update(self.run.frame * .016, self.run.frame)
            self.enemy.take_commands([Command(self.run.frame, forward=127)])
        self.assertGreater(math.dist(before, self.enemy.mover.position), .25)
        self.assertEqual(self.health(self.enemy), 200)

    def test_captured_player_disconnect_allows_charge_to_capture_next_enemy(self):
        self.place(self.other, (0, 1, 20))
        self.cast()
        fight = self.match.combat
        self.assertTrue(pinned(fight, self.enemy.body))
        old = self.enemy.body
        self.match.leave(self.enemy)
        self.run.players.remove(self.enemy)
        self.assertIsNone(fight.charges[self.rein.body].pinned)
        self.assertFalse(controlled(fight, old))
        self.assertIn(self.rein.body, fight.charges)
        for _ in range(100):
            self.advance(1, buttons=0)
            if pinned(fight, self.other.body):
                break
        self.assertEqual(fight.charges[self.rein.body].pinned, self.other.body)
        self.assertEqual(self.health(self.other), 200)
        self.advance(200, buttons=0)
        self.assertFalse(controlled(fight, self.other.body))
        self.assertEqual(self.health(self.other), 200)

    def test_running_charge_captures_replacement_with_the_first_victims_id(self):
        charge_wall(self, z=14)
        fight = self.match.combat
        old = self.enemy.body
        self.advance(1, buttons=8)
        for _ in range(70):
            self.advance(1, buttons=0)
            if pinned(fight, old):
                break
        contact = fight.charges[self.rein.body]
        self.assertEqual(contact.pinned, old)
        self.assertEqual(self.health(self.enemy), 200)

        self.assertTrue(self.match.switch_hero(self.enemy, self.enemy.hero))
        self.assertNotEqual(self.enemy.body, old)
        with patch.object(self.enemy.client, 'body_id_available', side_effect=lambda entity: entity == old):
            self.assertTrue(self.match.switch_hero(self.enemy, heroes()[combat.REINHARDT]))
        self.assertEqual(self.enemy.body, old)
        self.assertIsNone(contact.pinned)
        self.assertFalse(controlled(fight, old))
        self.place(self.enemy, (0, 1, 9))

        for _ in range(35):
            self.advance(1, buttons=0)
            if pinned(fight, old):
                break
        self.assertIs(fight.charges.get(self.rein.body), contact)
        self.assertEqual(contact.pinned, old)
        self.assertEqual(self.health(self.enemy), 500)

        for _ in range(50):
            self.advance(1, buttons=0)
            if self.rein.body not in fight.charges:
                break
        self.assertNotIn(self.rein.body, fight.charges)
        self.assertFalse(pinned(fight, old))
        self.assertEqual(self.health(self.enemy), 205)
        self.assertEqual([notice['+0x78']['+0x4'] for notice in self.rein.client.messages(combat.HIT)], [295])


class ReinhardtChainHookTests(unittest.TestCase):
    health = HammerTests.health
    place = staticmethod(HammerTests.place)
    advance = HammerTests.advance

    def arena(self):
        HammerTests.setUp(self)
        self.assertTrue(self.match.switch_hero(self.enemy, heroes()[hook.ROADHOG]))
        self.place(self.enemy, (0, 1, 8))
        self.place(self.other, (20, 1, 0))
        self.advance(10, buttons=0)
        return self.match.combat

    def hook_until_flight(self, fight):
        self.run.step({self.enemy: (-32768, 0, 8)})
        for _ in range(35):
            self.run.step({self.enemy: (-32768, 0, 0)})
            if self.enemy.body in fight.hooks.flights:
                return fight.hooks.flights[self.enemy.body]
        self.fail("actual Chain Hook did not launch")

    def hook_until_pull(self, fight):
        flight = self.hook_until_flight(fight)
        for _ in range(55):
            self.run.step({self.enemy: (-32768, 0, 0)})
            if fight.hooks.pulling(self.rein.body):
                return flight
        self.fail("actual Chain Hook did not start a pull")

    def test_actual_hook_stops_charge_windup_and_drive_then_releases(self):
        for hook_at, saw_drive in ((4, False), (34, True)):
            with self.subTest(hook_at=hook_at):
                fight = self.arena()
                drove = caught = False
                captured_at = None
                for offset in range(115):
                    self.run.step({
                        self.rein: (0, 0, 8 if offset == 0 else 0),
                        self.enemy: (-32768, 0, 8 if offset == hook_at else 0),
                    })
                    drove |= self.rein.body in fight.charges
                    if fight.hooks.pulling(self.rein.body):
                        if not caught:
                            captured_at = self.rein.mover.position[2]
                        caught = True
                        self.assertTrue(controlled(fight, self.rein.body))
                        self.assertNotIn(self.rein.body, fight.charges)
                        self.assertEqual(self.health(self.rein), 475)
                    self.assertFalse(pinned(fight, self.enemy.body))
                self.assertTrue(caught)
                self.assertEqual(drove, saw_drive)
                self.assertFalse(fight.hooks.flights)
                self.assertFalse(controlled(fight, self.rein.body))
                self.assertGreater(self.rein.mover.position[2], captured_at)
                self.assertEqual(self.health(self.rein), 475)

    @unittest.skipUnless(BARRIER_DATA.is_file(), "prepared Barrier Field required")
    def test_actual_hook_spends_frontal_barrier_without_grabbing_reinhardt(self):
        self.enterContext(patch.dict(os.environ, {"ORPHEUS_REINHARDT_COLLISION": str(BARRIER_DATA)}))
        fight = self.arena()
        self.advance(12, buttons=2)
        barrier = fight.reinhardt_barriers[self.rein.body]
        self.assertTrue(barrier.active)
        self.run.step({self.rein: (0, 0, 2), self.enemy: (-32768, 0, 8)})
        launched = False
        for _ in range(55):
            self.run.step({self.rein: (0, 0, 2), self.enemy: (-32768, 0, 0)})
            launched |= self.enemy.body in fight.hooks.flights
            self.assertFalse(controlled(fight, self.rein.body))
        self.assertTrue(launched)
        self.assertTrue(barrier.active)
        self.assertEqual(barrier.current, 1570)
        self.assertEqual(self.health(self.rein), 500)
        self.assertFalse(fight.hooks.flights)

    def test_actual_hook_owner_leave_releases_live_pull_without_more_damage_or_movement(self):
        fight = self.arena()
        self.run.step({self.enemy: (-32768, 0, 8)})
        for _ in range(55):
            self.run.step({self.enemy: (-32768, 0, 0)})
            if fight.hooks.pulling(self.rein.body):
                break
        self.assertTrue(fight.hooks.pulling(self.rein.body))
        self.assertTrue(controlled(fight, self.rein.body))
        self.assertEqual(self.health(self.rein), 475)
        for _ in range(4):
            self.run.step({self.enemy: (-32768, 0, 0)})
        position = tuple(self.rein.mover.position)
        self.assertGreater(position[2], 0)
        self.match.leave(self.enemy)
        self.run.players.remove(self.enemy)
        self.assertFalse(fight.hooks.flights)
        self.assertFalse(controlled(fight, self.rein.body))
        self.advance(20, buttons=0)
        self.assertEqual(tuple(self.rein.mover.position), position)
        self.assertEqual(self.health(self.rein), 475)

    def test_actual_rifle_kill_releases_hook_before_same_hero_respawn(self):
        fight = self.arena()
        self.match.game_map = replace(self.match.game_map, free_for_all=False)
        self.other.team = self.enemy.team
        self.place(self.other, (6, 1, 0))
        old_body = self.rein.body
        old_script = self.rein.body_script
        flight = self.hook_until_pull(fight)
        self.assertEqual(flight.target, old_body)
        self.assertEqual(self.health(self.rein), 475)
        vitals = fight.vitals_of(fight._target_entity(old_body))
        vitals.damage(vitals.total - 1, armour_scalar=1.0)
        x, y, z = self.rein.mover.position
        angles = aim(fight._eye(self.other), (x, y + 1.2, z))
        self.run.step({self.other: (*angles, 1)})
        self.assertTrue(vitals.dead)
        self.assertEqual(vitals.killer, self.other.body)
        self.assertFalse(fight.hooks.flights)
        self.assertFalse(controlled(fight, old_body))
        corpse = tuple(self.rein.mover.position)
        for _ in range(800):
            self.advance(1, buttons=0)
            self.assertFalse(fight.hooks.flights)
            if self.rein.body != old_body:
                break
            self.assertEqual(tuple(self.rein.mover.position), corpse)
        self.assertNotEqual(self.rein.body, old_body)
        self.assertTrue(old_script.retired)
        self.assertEqual(self.rein.hero.guid, combat.REINHARDT)
        self.assertEqual(self.health(self.rein), 500)
        self.assertFalse(controlled(fight, self.rein.body))
        self.assertEqual(self.rein.mover.velocity, (0.0, 0.0, 0.0))

    def test_actual_hook_retirement_clears_pull_and_fresh_cast_hits_reused_body_id(self):
        fight = self.arena()
        old_body = self.rein.body
        old_script = self.rein.body_script
        old_generation = fight.replacement_ticks.get(old_body)
        original = self.hook_until_pull(fight)
        self.assertEqual(original.target, old_body)
        self.assertEqual(self.health(self.rein), 475)
        self.assertTrue(self.match.switch_hero(self.rein, self.rein.hero))
        self.assertNotEqual(self.rein.body, old_body)
        self.assertTrue(old_script.retired)
        self.assertFalse(fight.hooks.flights)
        self.assertFalse(controlled(fight, old_body))
        self.advance(1, buttons=0)
        with patch.object(self.rein.client, "body_id_available", side_effect={old_body}.__contains__):
            self.assertTrue(self.match.switch_hero(self.rein, self.rein.hero))
        self.assertEqual(self.rein.body, old_body)
        self.assertNotEqual(fight.replacement_ticks[old_body], old_generation)
        self.place(self.rein, (0, 1, 0))
        self.advance(550, buttons=0)
        self.assertEqual(self.health(self.rein), 500)
        self.assertEqual(tuple(self.rein.mover.position), (0, 1, 0))
        self.assertFalse(controlled(fight, old_body))
        self.assertFalse(fight.hooks.flights)
        fresh = self.hook_until_pull(fight)
        self.assertIsNot(fresh, original)
        self.assertEqual(fresh.target, old_body)
        self.assertEqual(self.health(self.rein), 475)
        self.assertTrue(controlled(fight, old_body))

    def test_actual_rifle_kills_hook_caster_while_flight_is_still_outbound(self):
        fight = self.arena()
        self.place(self.other, (5, 1, 8))
        flight = self.hook_until_flight(fight)
        self.assertIsNone(flight.target)
        self.assertEqual(flight.travelled, 0)
        vitals = fight.vitals_of(fight._target_entity(self.enemy.body))
        vitals.damage(vitals.total - 1, armour_scalar=1.0)
        angles = aim(fight._eye(self.other), (0, 2.2, 8))
        self.run.step({self.other: (*angles, 1), self.enemy: (-32768, 0, 0)})
        self.assertTrue(vitals.dead)
        self.assertEqual(vitals.killer, self.other.body)
        self.assertGreater(flight.travelled, 0)
        self.assertFalse(fight.hooks.flights)
        position = tuple(self.rein.mover.position)
        for _ in range(65):
            self.advance(1, buttons=0)
            self.assertFalse(controlled(fight, self.rein.body))
        self.assertEqual(tuple(self.rein.mover.position), position)
        self.assertEqual(self.health(self.rein), 500)
        self.assertFalse(fight.hooks.flights)

    def test_outbound_hook_can_hit_new_body_reusing_id_before_contact(self):
        fight = self.arena()
        original = self.rein.body
        original_vitals = fight.vitals_of(fight._target_entity(original))
        flight = self.hook_until_flight(fight)
        self.assertIsNone(flight.target)
        self.assertTrue(self.match.switch_hero(self.rein, self.rein.hero))
        self.assertNotEqual(self.rein.body, original)
        self.place(self.rein, (12, 1, 0))
        self.advance(1, buttons=0)
        with patch.object(self.rein.client, "body_id_available", side_effect={original}.__contains__):
            self.assertTrue(self.match.switch_hero(self.rein, self.rein.hero))
        self.assertEqual(self.rein.body, original)
        self.place(self.rein, (0, 1, 0))
        self.assertIs(fight.hooks.flights[self.enemy.body], flight)
        self.assertEqual(self.health(self.rein), 500)
        for _ in range(40):
            self.advance(1, buttons=0)
            if flight.target is not None:
                break
        self.assertEqual(flight.target, original)
        self.assertTrue(fight.hooks.pulling(original))
        self.assertEqual(original_vitals.total, 500)
        self.assertEqual(self.health(self.rein), 475)

    def test_actual_hook_release_keeps_earlier_earthshatter_control(self):
        fight = self.arena()
        self.assertTrue(self.match.switch_hero(self.other, heroes()[combat.REINHARDT]))
        self.place(self.other, (6, 1, 0))
        self.other.team = self.enemy.team
        self.match.game_map = replace(self.match.game_map, free_for_all=False)
        script = self.other.body_script
        script.set_ult_charge(script.component.entity_var(338).value())
        self.run.step({self.other: (-16384, 0, 32)})
        knockdown = (self.rein.body, self.other.body, "earthshatter")
        for _ in range(60):
            self.run.step({self.other: (-16384, 0, 0)})
            if knockdown in fight.controls:
                break
        deadline = fight.controls[knockdown]
        self.assertEqual(self.health(self.rein), 455)
        self.run.step({self.enemy: (-32768, 0, 8)})
        for _ in range(55):
            self.run.step({self.enemy: (-32768, 0, 0)})
            if fight.hooks.pulling(self.rein.body):
                break
        self.assertTrue(fight.hooks.pulling(self.rein.body))
        self.assertEqual(fight.controls[knockdown], deadline)
        self.assertEqual(self.health(self.rein), 430)
        self.match.leave(self.enemy)
        self.run.players.remove(self.enemy)
        self.assertFalse(fight.hooks.flights)
        self.assertEqual(fight.controls[knockdown], deadline)
        self.assertTrue(controlled(fight, self.rein.body))
        for _ in range(160):
            self.advance(1, buttons=0)
            if fight.now >= deadline:
                break
            self.assertTrue(controlled(fight, self.rein.body))
        self.assertFalse(controlled(fight, self.rein.body))
        self.assertEqual(self.health(self.rein), 430)

    def test_actual_charge_capture_of_roadhog_releases_third_players_live_hook(self):
        fight = self.arena()
        self.match.game_map = replace(self.match.game_map, free_for_all=False)
        self.other.team = self.rein.team
        self.place(self.other, (6, 1, 8))
        charge_wall(self, z=13)
        self.advance(5, buttons=0)
        caught = False
        for offset in range(90):
            self.run.step({
                self.rein: (0, 0, 8 if offset == 0 else 0),
                self.enemy: (16384, 0, 8 if offset == 32 else 0),
            })
            caught |= fight.hooks.pulling(self.other.body)
            if pinned(fight, self.enemy.body):
                break
        self.assertTrue(caught)
        self.assertTrue(pinned(fight, self.enemy.body))
        self.assertFalse(fight.hooks.flights)
        self.assertFalse(controlled(fight, self.other.body))
        self.assertEqual(self.health(self.other), 170)
        position = tuple(self.other.mover.position)
        self.advance(60, buttons=0)
        self.assertEqual(tuple(self.other.mover.position), position)
        self.assertEqual(self.health(self.other), 170)
        self.assertEqual(self.health(self.enemy), 300)
        self.assertFalse(pinned(fight, self.enemy.body))

    def test_actual_hook_respects_allies_static_cover_and_range(self):
        for reason in ("team", "cover", "range"):
            with self.subTest(reason=reason):
                fight = self.arena()
                if reason == "team":
                    self.match.game_map = replace(self.match.game_map, free_for_all=False)
                    self.enemy.team = self.rein.team
                elif reason == "cover":
                    charge_wall(self, z=4)
                else:
                    self.place(self.enemy, (0, 1, 24))
                start = tuple(self.rein.mover.position)
                self.run.step({self.enemy: (-32768, 0, 8)})
                launched = False
                for _ in range(75):
                    self.run.step({self.enemy: (-32768, 0, 0)})
                    launched |= self.enemy.body in fight.hooks.flights
                    self.assertFalse(controlled(fight, self.rein.body))
                self.assertTrue(launched)
                self.assertFalse(fight.hooks.flights)
                self.assertEqual(self.health(self.rein), 500)
                self.assertEqual(tuple(self.rein.mover.position), start)


class MutualChargeTests(unittest.TestCase):
    health = HammerTests.health
    place = staticmethod(HammerTests.place)

    def setUp(self):
        self.run = Run(KANEZAKA_DM, ("Reinhardt A", "Reinhardt B"))
        self.match = self.run.match
        self.match.bots.clear()
        self.rein, self.enemy = self.run.players
        for player in self.run.players:
            self.match.switch_hero(player, heroes()[combat.REINHARDT])
        self.place(self.rein, (0, 1, 0))
        self.place(self.enemy, (0, 1, 20))
        self.advance(30)

    def advance(self, frames, *, yaw=-32768):
        for _ in range(frames):
            self.run.step({self.enemy: (yaw, 0, 0)})

    def start(self, *, yaw=-32768):
        self.run.step({self.rein: (0, 0, 8), self.enemy: (yaw, 0, 8)})

    def phases(self):
        return [next(i for i in p.body_script.component.instances.values() if i.graph.index == 0x7D)
                for p in (self.rein, self.enemy)]

    def wait_for_counter(self, maximum=120):
        for _ in range(maximum):
            if controlled(self.match.combat, self.rein.body):
                return
            self.advance(1)
        self.fail("actual Shift commands did not produce counter-charge")

    def assert_counter(self):
        fight = self.match.combat
        self.assertTrue(controlled(fight, self.rein.body))
        self.assertTrue(controlled(fight, self.enemy.body))
        self.assertEqual(control_deadline(fight, self.rein.body) - fight.tick, 125)
        self.assertEqual(control_deadline(fight, self.enemy.body) - fight.tick, 125)
        self.assertEqual(self.health(self.rein), 500)
        self.assertEqual(self.health(self.enemy), 500)
        self.assertFalse(fight.charges)
        self.assertFalse(fight.charges)
        self.assertEqual(self.rein.client.messages(combat.HIT), [])
        self.assertEqual(self.enemy.client.messages(combat.HIT), [])
        for instance in self.phases():
            self.assertEqual(instance.find_var(0, 59).value(), 0)
            self.assertGreater(instance.find_var(0, 61).value(), 9)

    def test_running_head_on_charges_stop_both_for_authored_duration_without_damage(self):
        self.start()
        self.wait_for_counter()
        self.assert_counter()
        positions = [tuple(p.mover.position) for p in self.run.players]
        self.advance(124)
        self.assertTrue(controlled(self.match.combat, self.rein.body))
        self.assertEqual([tuple(p.mover.position) for p in self.run.players], positions)
        self.advance(1)
        self.assertFalse(controlled(self.match.combat, self.rein.body))
        self.assertFalse(controlled(self.match.combat, self.enemy.body))
        self.advance(20)
        self.assertEqual([tuple(p.mover.position) for p in self.run.players], positions)

    def test_close_windups_counter_independently_of_input_processing_order(self):
        for reverse in (False, True):
            with self.subTest(reverse=reverse):
                self.setUp()
                self.place(self.enemy, (0, 1, 3))
                if reverse:
                    self.run.players.reverse()
                self.start()
                self.assert_counter()
                self.advance(140)
                self.assertEqual(self.rein.mover.position[2], 0)
                self.assertEqual(self.enemy.mover.position[2], 3)

    def test_running_charge_counters_an_opposing_charge_still_in_windup(self):
        self.place(self.enemy, (0, 1, 12))
        self.run.step({self.rein: (0, 0, 8), self.enemy: (-32768, 0, 0)})
        self.advance(60)
        self.run.step({self.enemy: (-32768, 0, 8)})
        self.wait_for_counter(20)
        self.assert_counter()
        self.assertEqual(self.enemy.mover.position[2], 12)

    def test_allied_charges_do_not_counter_or_damage_each_other(self):
        self.match.game_map = self.match.game_map.__class__(
            **{**self.match.game_map.__dict__, "free_for_all": False})
        self.enemy.team = self.rein.team
        self.place(self.enemy, (0, 1, 3))
        self.start()
        self.advance(70)
        self.assertFalse(self.match.combat.stuns)
        self.assertEqual(self.health(self.rein), 500)
        self.assertEqual(self.health(self.enemy), 500)

    def test_same_facing_charges_do_not_counter(self):
        self.place(self.enemy, (0, 1, 3))
        self.start(yaw=0)
        self.advance(60, yaw=0)
        self.assertFalse(self.match.combat.stuns)
        self.assertEqual(len(self.match.combat.charges), 2)

    def test_perpendicular_charge_windups_do_not_counter_at_contact_range(self):
        self.place(self.enemy, (3, 1, 0))
        self.run.step({self.rein: (16384, 0, 8), self.enemy: (-32768, 0, 8)})
        for _ in range(5):
            self.run.step({self.rein: (16384, 0, 0), self.enemy: (-32768, 0, 0)})
        self.assertFalse(self.match.combat.stuns)
        for player in (self.rein, self.enemy):
            self.assertIs(player.body_script.component.entity_var(596).value(), True)

    def test_lateral_target_outside_authored_cone_does_not_counter(self):
        self.place(self.enemy, (3, 1, .1))
        self.start()
        self.advance(60)
        self.assertFalse(self.match.combat.stuns)
        self.assertEqual(len(self.match.combat.charges), 2)

    def test_static_wall_blocks_counter_and_both_end_at_the_obstacle(self):
        self.place(self.enemy, (0, 1, 3))
        charge_wall(self, 1.5)
        self.start()
        self.advance(80)
        self.assertFalse(self.match.combat.stuns)
        self.assertFalse(self.match.combat.charges)
        self.assertEqual(self.health(self.rein), 500)
        self.assertEqual(self.health(self.enemy), 500)

    def test_retiring_one_body_does_not_cancel_the_others_knockdown(self):
        self.place(self.enemy, (0, 1, 3))
        self.start()
        self.assert_counter()
        self.match.switch_hero(self.rein, self.rein.hero)
        self.assertTrue(controlled(self.match.combat, self.enemy.body))
        self.advance(125)
        self.assertFalse(controlled(self.match.combat, self.enemy.body))

    def test_both_cooldowns_expire_and_fresh_input_can_counter_again(self):
        self.place(self.enemy, (0, 1, 3))
        self.start()
        self.assert_counter()
        self.advance(650)
        self.start()
        self.assert_counter()


class CounterChargeInteractionTests(unittest.TestCase):
    setUp = MutualChargeTests.setUp
    health = HammerTests.health
    place = staticmethod(HammerTests.place)
    advance = MutualChargeTests.advance

    def prepare(self, hero, held_frames=None):
        self.match.switch_hero(self.enemy, heroes()[hero])
        self.place(self.enemy, (0, 1, 3))
        if held_frames is None:
            held_frames = 80 if hero == 0x02E000000000012F else 30
        for _ in range(held_frames):
            self.run.step({self.enemy: (-32768, 0, 2)})

    def trigger(self, hero):
        self.run.step({self.rein: (0, 0, 8),
                       self.enemy: (-32768, 0, 0 if hero == 0x02E000000000012F else 3)})
        if not controlled(self.match.combat, self.rein.body):
            self.advance(1)

    def assert_counter(self, hero):
        fight = self.match.combat
        for player in (self.rein, self.enemy):
            self.assertTrue(controlled(fight, player.body))
            self.assertEqual(control_deadline(fight, player.body) - fight.tick, 125)
            self.assertFalse(player.body_script.component.held)
        self.assertEqual(self.health(self.rein), 500)
        self.assertEqual(self.health(self.enemy), 250 if hero == 0x02E000000000012F else 200)
        self.assertFalse(fight.charges)
        self.assertEqual(self.rein.client.messages(combat.HIT), [])
        self.assertEqual(self.enemy.client.messages(combat.HIT), [])

    def test_actual_punch_and_bash_input_counter_with_their_own_two_second_profiles(self):
        for hero in (0x02E000000000012F, 0x02E0000000000195):
            with self.subTest(hero=hero):
                self.setUp()
                self.prepare(hero)
                self.assertEqual(self.match.combat._counter_duration(self.enemy.body_script), 2)
                self.trigger(hero)
                self.assert_counter(hero)
                self.advance(130)
                self.assertFalse(controlled(self.match.combat, self.enemy.body))
                self.assertFalse(controlled(self.match.combat, self.rein.body))
                self.assertEqual(self.rein.mover.position[2], 0)
                flag = self.enemy.body_script.component.entity_var(596, create=False)
                self.assertFalse(flag is not None and bool(flag.value()))

    def test_unreleased_punch_does_not_counter_until_native_charging_flag_begins(self):
        hero = 0x02E000000000012F
        self.prepare(hero)
        self.run.step({self.rein: (0, 0, 8), self.enemy: (-32768, 0, 2)})
        for _ in range(5):
            self.run.step({self.enemy: (-32768, 0, 2)})
        self.assertFalse(any_controls(self.match.combat))
        self.run.step({self.enemy: (-32768, 0, 0)})
        self.advance(1)
        self.assert_counter(hero)

    def test_held_brigitte_shield_does_not_counter_until_bash_input(self):
        hero = 0x02E0000000000195
        self.prepare(hero)
        self.run.step({self.rein: (0, 0, 8), self.enemy: (-32768, 0, 2)})
        for _ in range(5):
            self.run.step({self.enemy: (-32768, 0, 2)})
        self.assertFalse(any_controls(self.match.combat))
        self.run.step({self.enemy: (-32768, 0, 3)})
        self.assert_counter(hero)

    def test_ordinary_shield_bash_uses_armor_control_and_cooldown(self):
        self.prepare(0x02E0000000000195)
        self.place(self.enemy, (0, 1, 2.7))
        fight = self.match.combat
        shield = brigitte_shield(fight, self.enemy.body)
        hold = {self.enemy: (-32768, 0, 2)}
        self.run.step({self.enemy: (-32768, 0, 3)})
        dash = shield.instance.states[14]
        self.assertTrue(dash.active)
        self.assertEqual(self.health(self.rein), 500)
        self.run.step(hold)
        self.assertEqual(self.health(self.rein), 497.5)
        self.assertEqual(control_deadline(fight, self.rein.body) - fight.tick, 47)
        self.assertFalse(dash.active)
        self.assertEqual(len(self.enemy.client.messages(combat.HIT)), 1)
        for _ in range(47):
            self.run.step(hold)
        self.assertFalse(controlled(fight, self.rein.body))
        self.place(self.rein, (0, 1, 0))
        self.place(self.enemy, (0, 1, 2.7))
        self.run.step({self.enemy: (-32768, 0, 3)})
        self.run.step(hold)
        self.assertFalse(dash.active)
        self.assertEqual(self.health(self.rein), 497.5)
        for _ in range(430):
            self.run.step(hold)
        self.run.step({self.enemy: (-32768, 0, 3)})
        self.run.step(hold)
        self.assertEqual(self.health(self.rein), 495)
        self.assertFalse(dash.active)
        self.assertEqual(len(self.enemy.client.messages(combat.HIT)), 2)

    def test_ordinary_flank_shield_bash_interrupts_live_charge_without_a_counter(self):
        for running, keep_held in ((False, False), (False, True), (True, False), (True, True)):
            with self.subTest(running=running, keep_held=keep_held):
                self.setUp()
                self.prepare(0x02E0000000000195)
                self.place(self.enemy, (2.7, 1, 8 if running else 0))
                fight = self.match.combat
                charge = next(i for i in self.rein.body_script.component.instances.values()
                              if i.graph.index == 0x007D)
                cooldown = charge.find_var(0, 61)
                hold = {self.enemy: (-16384, 0, 2)}
                self.run.step({**hold, self.rein: (0, 0, 8)})
                if running:
                    for _ in range(95):
                        if self.rein.mover.position[2] >= 7.5:
                            break
                        self.run.step(hold)
                    self.assertGreaterEqual(self.rein.mover.position[2], 7.5)
                self.assertTrue(charge.states[40].active)
                interrupted_activation = charge.states[40].counter
                self.assertEqual(self.rein.body in fight.charges, running)
                self.assertEqual(cooldown.value(), 0)
                self.run.step({self.enemy: (-16384, 0, 3)})
                self.run.step(hold)
                self.assertEqual(self.health(self.rein), 497.5)
                self.assertEqual(self.health(self.enemy), 200)
                self.assertEqual(control_deadline(fight, self.rein.body) - fight.tick, 47)
                self.assertFalse(controlled(fight, self.enemy.body))
                self.assertFalse(charge.states[40].active)
                self.assertEqual(cooldown.value(), 10)
                self.assertFalse(fight.charges)
                self.assertFalse(fight.charges)
                self.assertEqual(len(self.enemy.client.messages(combat.HIT)), 1)
                self.assertEqual(self.rein.client.messages(combat.HIT), [])

                self.place(self.enemy, (20, 1, 20))
                held_charge = {self.rein: (0, 0, 8)}
                for elapsed in range(650):
                    if cooldown.value() <= 0:
                        break
                    remaining = cooldown.value()
                    self.run.step(held_charge if keep_held or elapsed < 60 else {})
                    self.assertLessEqual(cooldown.value(), remaining)
                    self.assertFalse(fight.charges)
                    self.assertFalse(fight.charges)
                self.assertEqual(cooldown.value(), 0)
                self.assertFalse(controlled(fight, self.rein.body))
                stopped = tuple(self.rein.mover.position)
                if not keep_held:
                    self.advance(12)
                    self.assertFalse(charge.states[40].active)
                    self.assertEqual(tuple(self.rein.mover.position), stopped)
                self.assertFalse(fight.charges)
                self.assertFalse(fight.charges)

                # A currently held start button can start a new activation once ready.
                # A released button must leave the charge stopped until a new press.
                self.run.step(held_charge)
                self.assertTrue(charge.states[40].active)
                self.assertNotEqual(charge.states[40].counter, interrupted_activation)
                for _ in range(65):
                    if fight.charges:
                        break
                    self.run.step({})
                self.assertIn(self.rein.body, fight.charges)
                self.assertGreater(self.rein.mover.position[2], stopped[2])

    def test_ordinary_shield_bash_rejects_team_cover_and_range(self):
        for reason in ("team", "cover", "range", "rear"):
            with self.subTest(reason=reason):
                self.setUp()
                self.prepare(0x02E0000000000195)
                self.place(self.enemy, (0, 1, 12 if reason == "range" else 2.7))
                yaw = 0 if reason == "rear" else -32768
                if reason == "team":
                    self.match.game_map = replace(self.match.game_map, free_for_all=False)
                    self.enemy.team = self.rein.team
                elif reason == "cover":
                    charge_wall(self, z=1.25)
                fight = self.match.combat
                self.run.step({self.enemy: (yaw, 0, 3)})
                self.assertTrue(brigitte_shield(fight, self.enemy.body).instance.states[14].active)
                for _ in range(20):
                    self.run.step({self.enemy: (yaw, 0, 2)})
                    self.assertFalse(controlled(fight, self.rein.body))
                self.assertEqual(self.health(self.rein), 500)
                self.assertEqual(self.enemy.client.messages(combat.HIT), [])

    def test_brigitte_can_start_counter_outside_reinhardts_charge_cone(self):
        hero = 0x02E0000000000195
        for reverse in (False, True):
            for cover in (False, True):
                with self.subTest(reverse=reverse, cover=cover):
                    self.setUp()
                    self.prepare(hero)
                    if reverse:
                        self.run.players.reverse()
                    if cover:
                        charge_wall(self, z=1.5)
                    rein_yaw = 12000
                    fight = self.match.combat
                    with patch.object(fight, "stun", wraps=fight.stun) as applied:
                        self.run.step({self.rein: (rein_yaw, 0, 8), self.enemy: (-32768, 0, 3)})
                        for _ in range(2):
                            if controlled(fight, self.rein.body):
                                break
                            self.run.step({self.rein: (rein_yaw, 0, 0), self.enemy: (-32768, 0, 2)})
                    if cover:
                        applied.assert_not_called()
                        for player in (self.rein, self.enemy):
                            flag = player.body_script.component.entity_var(596, create=False)
                            self.assertTrue(flag is not None and combat.expr.truthy(flag.value()))
                        continue
                    self.assert_counter(hero)
                    self.assertEqual(applied.call_count, 2)
                    # The attacker's self-knockdown is applied first. In this geometry
                    # only the Bash tracker can select its opponent.
                    self.assertIs(applied.call_args_list[0].args[0].owner, self.enemy)
                    self.assertIs(applied.call_args_list[1].args[0].owner, self.rein)
                    self.advance(125)
                    self.assertFalse(controlled(fight, self.rein.body))
                    self.assertFalse(controlled(fight, self.enemy.body))

    def test_charge_and_shield_bash_both_move_before_counter_contact(self):
        hero = 0x02E0000000000195
        for gap in (6, 7, 8, 9):
            with self.subTest(gap=gap):
                self.setUp()
                self.prepare(hero)
                self.place(self.enemy, (0, 1, 24))
                fight = self.match.combat
                self.run.step({self.rein: (0, 0, 8), self.enemy: (-32768, 0, 2)})
                for _ in range(120):
                    if self.rein.mover.position[2] >= 24 - gap:
                        break
                    self.run.step({self.enemy: (-32768, 0, 2)})
                self.assertGreaterEqual(self.rein.mover.position[2], 24 - gap)
                self.assertIn(self.rein.body, fight.charges)
                self.assertFalse(controlled(fight, self.rein.body))
                start = self.rein.mover.position[2]
                shield = brigitte_shield(fight, self.enemy.body)
                self.assertEqual(shield.current, 250)
                self.run.step({self.enemy: (-32768, 0, 3)})
                self.assertTrue(shield.instance.states[14].active)
                for _ in range(12):
                    if controlled(fight, self.rein.body):
                        break
                    self.run.step({self.enemy: (-32768, 0, 2)})
                self.assert_counter(hero)
                self.assertGreater(self.rein.mover.position[2], start)
                self.assertLess(self.enemy.mover.position[2], 24)
                self.assertEqual(shield.current, 250)
                players = self.rein, self.enemy
                end = tuple(tuple(player.mover.position) for player in players)
                self.advance(124)
                for player in players:
                    self.assertTrue(controlled(fight, player.body))
                self.advance(1)
                for player in players:
                    self.assertFalse(controlled(fight, player.body))
                    flag = player.body_script.component.entity_var(596, create=False)
                    self.assertFalse(flag is not None and combat.expr.truthy(flag.value()))
                self.advance(4)
                self.assertEqual(tuple(tuple(player.mover.position) for player in players), end)

    def test_running_charge_meets_actual_punch_and_bash(self):
        for hero in (0x02E000000000012F, 0x02E0000000000195):
            with self.subTest(hero=hero):
                self.setUp()
                self.prepare(hero, held_frames=30)
                self.place(self.enemy, (0, 1, 12))
                fight = self.match.combat
                self.run.step({self.rein: (0, 0, 8), self.enemy: (-32768, 0, 2)})
                for _ in range(80):
                    self.run.step({self.enemy: (-32768, 0, 2)})
                    if self.rein.mover.position[2] >= 8:
                        break
                self.assertIn(self.rein.body, fight.charges)
                self.assertGreaterEqual(self.rein.mover.position[2], 8)
                self.assertFalse(controlled(fight, self.rein.body))
                self.run.step({self.enemy: (-32768, 0, 0 if hero == 0x02E000000000012F else 3)})
                for _ in range(12):
                    if controlled(fight, self.rein.body):
                        break
                    self.run.step({self.enemy: (-32768, 0, 0)})
                self.assert_counter(hero)
                self.assertFalse(fight.charges)
                poses = [tuple(actor.mover.position) for actor in (self.rein, self.enemy)]
                self.advance(124)
                self.assertTrue(controlled(fight, self.rein.body))
                self.assertTrue(controlled(fight, self.enemy.body))
                self.advance(1)
                self.assertFalse(controlled(fight, self.rein.body))
                self.assertFalse(controlled(fight, self.enemy.body))
                self.advance(20)
                self.assertEqual([tuple(actor.mover.position) for actor in (self.rein, self.enemy)], poses)

    def test_rocket_punch_shield_decays_or_is_spent_by_actual_fire_strike(self):
        for fire_strike in (False, True):
            with self.subTest(fire_strike=fire_strike):
                self.setUp()
                self.prepare(0x02E000000000012F)
                self.place(self.enemy, (0, 1, 5))
                fight = self.match.combat
                self.run.step({self.enemy: (-32768, 0, 0)})
                for _ in range(10):
                    if self.health(self.rein) < 500:
                        break
                    self.run.step({self.enemy: (-32768, 0, 0)})
                self.assertLess(self.health(self.rein), 500)
                vitals = fight.vitals[self.enemy.body]
                self.assertEqual(self.health(self.enemy), 280)
                self.assertEqual(vitals.temporary_shields, 30)
                self.assertEqual(passive_decay(vitals), (fight.now + 1, 3))
                decay_start = fight.now + 1
                if fire_strike:
                    self.advance(32)
                    self.assertFalse(controlled(fight, self.rein.body))
                    self.run.step({self.rein: (0, 0, 16)})
                    for _ in range(55):
                        if self.health(self.enemy) < 250:
                            break
                        self.run.step({})
                    self.assertEqual(vitals.temporary_shields, 0)
                    self.assertAlmostEqual(self.health(self.enemy), 180, delta=1)
                    self.assertEqual(len(self.rein.client.messages(combat.HIT)), 1)
                    self.advance(2)
                    self.assertIsNone(passive_decay(vitals))
                else:
                    self.advance(62)
                    self.assertEqual(vitals.temporary_shields, 30)
                    self.advance(63)
                    self.assertAlmostEqual(vitals.temporary_shields, 30 - 3 * (fight.now - decay_start))
                    self.assertEqual(vitals.now[combat.HEALTH_POOL], 250)

    def test_ordinary_rocket_punch_damages_controls_and_pushes_reinhardt(self):
        for scenario in ("open", "wall", "distant_wall", "source_switch"):
            with self.subTest(scenario=scenario):
                self.setUp()
                self.prepare(0x02E000000000012F)
                self.place(self.enemy, (0, 1, 5))
                if scenario in ("wall", "source_switch"):
                    charge_wall(self, z=-1.35)
                elif scenario == "distant_wall":
                    charge_wall(self, z=-3)
                fight = self.match.combat
                self.run.step({self.enemy: (-32768, 0, 0)})
                for _ in range(10):
                    if self.health(self.rein) < 500:
                        break
                    self.run.step({self.enemy: (-32768, 0, 0)})
                self.assertAlmostEqual(self.health(self.rein), 409.960726, places=5)
                self.assertEqual(control_deadline(fight, self.rein.body) - fight.tick, 32)
                self.assertFalse(controlled(fight, self.enemy.body))
                self.assertNotIn(self.enemy.body, active_punches(fight))
                self.assertIn(self.rein.body, impact_entities(fight))
                if scenario == "source_switch":
                    self.assertTrue(self.match.switch_hero(self.enemy, heroes()[combat.REINHARDT]))
                    self.assertNotIn(self.rein.body, impact_entities(fight))
                self.advance(35)
                self.assertLess(self.rein.mover.position[2], 0)
                if scenario == "wall":
                    self.assertAlmostEqual(self.health(self.rein), 274.882178, places=5)
                    self.assertEqual(len(self.enemy.client.messages(combat.HIT)), 2)
                else:
                    self.assertAlmostEqual(self.health(self.rein), 409.960726, places=5)
                    self.assertEqual(len(self.enemy.client.messages(combat.HIT)), 1)
                self.assertFalse(controlled(fight, self.rein.body))
                self.assertNotIn(self.rein.body, impact_entities(fight))

    def test_rocket_punch_starts_counter_outside_reinhardts_charge_cone(self):
        hero = 0x02E000000000012F
        for reverse in (False, True):
            with self.subTest(reverse=reverse):
                self.setUp()
                self.prepare(hero)
                self.place(self.enemy, (0, 1, 3.4))
                if reverse:
                    self.run.players.reverse()
                fight = self.match.combat
                rein_yaw = 12000
                with patch.object(fight, "stun", wraps=fight.stun) as applied:
                    self.run.step({self.rein: (rein_yaw, 0, 8), self.enemy: (-32768, 0, 0)})
                    for _ in range(3):
                        if controlled(fight, self.rein.body):
                            break
                        self.run.step({self.rein: (rein_yaw, 0, 0), self.enemy: (-32768, 0, 0)})
                self.assert_counter(hero)
                self.assertLess(self.enemy.mover.position[2], 3.4)
                self.assertEqual(applied.call_count, 2)
                self.assertIs(applied.call_args_list[0].args[0].owner, self.enemy)
                self.assertIs(applied.call_args_list[1].args[0].owner, self.rein)
                self.assertNotIn(self.enemy.body, active_punches(fight))

    def test_charge_and_rocket_punch_both_move_before_counter_contact(self):
        hero = 0x02E000000000012F
        for gap in (7, 9):
            with self.subTest(gap=gap):
                self.setUp()
                self.prepare(hero, held_frames=30)
                enemy_z = 18
                self.place(self.enemy, (0, 1, enemy_z))
                fight = self.match.combat
                self.run.step({self.rein: (0, 0, 8), self.enemy: (-32768, 0, 2)})
                for _ in range(120):
                    if self.rein.mover.position[2] >= enemy_z - gap:
                        break
                    self.run.step({self.enemy: (-32768, 0, 2)})
                self.assertGreaterEqual(self.rein.mover.position[2], enemy_z - gap)
                self.assertIn(self.rein.body, fight.charges)
                self.assertFalse(controlled(fight, self.rein.body))
                start = self.rein.mover.position[2]
                self.run.step({self.enemy: (-32768, 0, 0)})
                motion = punch_instance(self.enemy).states[7]
                self.assertTrue(motion.active)
                self.assertEqual(self.enemy.mover.position[2], enemy_z)
                self.run.step({self.enemy: (-32768, 0, 0)})
                self.assertIn(self.enemy.body, active_punches(fight))
                self.assertLess(self.enemy.mover.position[2], enemy_z)
                self.assertFalse(controlled(fight, self.rein.body))
                for _ in range(16):
                    if controlled(fight, self.rein.body):
                        break
                    self.run.step({self.enemy: (-32768, 0, 0)})
                self.assert_counter(hero)
                self.assertGreater(self.rein.mover.position[2], start)
                self.assertNotIn(self.enemy.body, active_punches(fight))
                self.assertFalse(motion.active)
                players = self.rein, self.enemy
                end = tuple(tuple(player.mover.position) for player in players)
                self.advance(124)
                for player in players:
                    self.assertTrue(controlled(fight, player.body))
                self.advance(1)
                for player in players:
                    self.assertFalse(controlled(fight, player.body))
                self.advance(4)
                self.assertEqual(tuple(tuple(player.mover.position) for player in players), end)

    def test_rocket_punch_keeps_release_heading_and_ends_at_static_wall(self):
        self.prepare(0x02E000000000012F)
        self.place(self.enemy, (0, 1, 18))
        charge_wall(self, z=16)
        fight = self.match.combat
        self.run.step({self.enemy: (-32768, 0, 0)})
        motion = punch_instance(self.enemy).states[7]
        self.assertTrue(motion.active)
        self.run.step({self.enemy: (16384, 0, 0)})
        self.assertAlmostEqual(self.enemy.mover.position[0], 0, places=3)
        self.assertLess(self.enemy.mover.position[2], 17.5)
        for _ in range(5):
            if not motion.active:
                break
            self.run.step({self.enemy: (16384, 0, 0)})
        self.assertFalse(motion.active)
        self.assertNotIn(self.enemy.body, active_punches(fight))
        stopped = tuple(self.enemy.mover.position)
        self.assertGreater(stopped[2], 16)
        self.assertLess(stopped[2], 17)
        self.advance(10)
        self.assertEqual(tuple(self.enemy.mover.position), stopped)
        self.assertFalse(any_controls(fight))
        self.assertEqual(self.health(self.rein), 500)

    def test_held_rocket_punch_auto_release_counters_live_charge(self):
        hero = 0x02E000000000012F
        self.prepare(hero)
        self.place(self.enemy, (0, 1, 12))
        fight = self.match.combat
        self.run.step({self.rein: (0, 0, 8), self.enemy: (-32768, 0, 2)})
        drove = released = moved = False
        for _ in range(85):
            self.run.step({self.enemy: (-32768, 0, 2)})
            drove |= self.rein.body in fight.charges
            flag = self.enemy.body_script.component.entity_var(596, create=False)
            released |= flag is not None and flag.value() is True
            moved |= self.enemy.body in active_punches(fight) and self.enemy.mover.position[2] < 12
            if controlled(fight, self.rein.body):
                break
        self.assertTrue(drove)
        self.assertTrue(released)
        self.assertTrue(moved)
        self.assert_counter(hero)
        self.assertFalse(fight.charges)

    def test_actual_punch_and_bash_counter_filters_team_facing_cover_and_range(self):
        for hero in (0x02E000000000012F, 0x02E0000000000195):
            for reason, yaw in (("team", -32768), ("parallel", 0), ("perpendicular", 16384),
                                ("wall", -32768), ("range", -32768)):
                with self.subTest(hero=hero, reason=reason):
                    self.setUp()
                    self.prepare(hero)
                    if reason == "team":
                        self.match.game_map = replace(self.match.game_map, free_for_all=False)
                        self.enemy.team = self.rein.team
                    elif reason == "wall":
                        charge_wall(self, z=1.5)
                    elif reason == "range":
                        self.place(self.enemy, (0, 1, 10))
                    self.run.step({self.enemy: (yaw, 0, 2)})
                    attack = 0 if hero == 0x02E000000000012F else 3
                    self.run.step({self.rein: (0, 0, 8), self.enemy: (yaw, 0, attack)})
                    self.run.step({self.enemy: (yaw, 0, 0)})
                    fight = self.match.combat
                    self.assertFalse(any_controls(fight))
                    self.assertEqual(self.health(self.rein), 500)
                    self.assertEqual(self.health(self.enemy), 250 if hero == 0x02E000000000012F else 200)
                    for player in (self.rein, self.enemy):
                        self.assertIs(player.body_script.component.entity_var(596).value(), True)

    def test_interrupted_counterparts_recover_and_can_counter_again_after_cooldown(self):
        for hero in (0x02E000000000012F, 0x02E0000000000195):
            with self.subTest(hero=hero):
                self.setUp()
                self.prepare(hero)
                self.trigger(hero)
                self.assert_counter(hero)
                self.advance(650)
                for _ in range(80 if hero == 0x02E000000000012F else 30):
                    self.run.step({self.enemy: (-32768, 0, 2)})
                self.trigger(hero)
                self.assert_counter(hero)


class BobChargeFilterTests(unittest.TestCase):
    setUp = MutualChargeTests.setUp
    place = staticmethod(HammerTests.place)
    advance = MutualChargeTests.advance

    def test_live_charge_flags_drive_the_authored_reciprocal_filters(self):
        resource = graph.graph(0x1537)
        if resource is None:
            self.skipTest("B.O.B. charge graph required")
        self.run.step({self.rein: (0, 0, 8)})
        self.advance(2)
        rein = self.rein.body_script.component
        self.assertIs(rein.entity_var(596).value(), True)
        charge = next(instance for instance in rein.instances.values() if instance.graph.index == 0x007D)
        self.assertTrue(charge.state(74).active)
        bob = runtime.Component(0x22F0)
        bob_charge = bob.create(resource, init=False)
        charging = bob_charge.node_state(resource.node(106))
        impact = bob_charge.node_state(resource.node(148))
        with bob.operation():
            charging.begin()
        self.assertIs(bob.entity_var(596).value(), True)
        self.assertIsNone(bob.entity_var(6601, create=False))

        class CandidateContext(runtime.InstanceContext):
            def __init__(self, instance, candidate, facings):
                super().__init__(instance, None)
                self.candidate = candidate
                self.facings = facings

            def param(self, key):
                return expr.Entity(self.candidate.entity) if key == 8 else super().param(key)

            def member(self, entity, key):
                if entity == self.candidate.entity:
                    slot = self.candidate.entity_var(key, create=False)
                    return slot.value() if slot is not None else None
                return super().member(entity, key)

            def native(self, config):
                kind = config["$"]
                if kind == "STU_021D01CD":
                    entity = expr.to_entity(expr.evaluate(config["m_entity"], self))
                    return expr.Vec3(*combat.aim(self.facings[entity], 0))
                if kind == "STU_AC829876":
                    return True  # Fixture presents an otherwise eligible enemy body.
                if kind == "STU_25ECB55F":
                    return False  # Reinhardt body 0024 lacks fallback target tag 067D.
                return super().native(config)

        def admitted(instance, node, candidate, yaw):
            facings = {rein.entity: self.rein.mover.yaw, bob.entity: yaw}
            context = CandidateContext(instance, candidate, facings)
            return expr.truthy(expr.evaluate(node.field("m_filter")["m_condition"], context))

        tracker = charge.state(74).node
        for yaw, opposing in ((0, False), (16384, False), (-32768, True)):
            with self.subTest(yaw=yaw):
                self.assertEqual(admitted(charge, tracker, bob, yaw), opposing)
                self.assertEqual(admitted(bob_charge, resource.node(102), rein, yaw), opposing)
                self.assertEqual(admitted(bob_charge, resource.node(16), rein, yaw), not opposing)

        with bob.operation():
            charging.end(False)
            impact.begin()
        self.assertFalse(expr.truthy(bob.entity_var(596).value()))
        self.assertIs(bob.entity_var(6601).value(), True)
        self.assertTrue(admitted(charge, tracker, bob, 0))
        with bob.operation():
            impact.end(False)
        self.assertFalse(admitted(charge, tracker, bob, -32768))


class KnockbackTests(unittest.TestCase):
    setUp = ChargeContactTests.setUp
    health = HammerTests.health
    place = staticmethod(HammerTests.place)
    advance = HammerTests.advance
    cast = ChargeContactTests.cast

    def test_authored_reinhardt_scalar_applies_and_removing_its_state_removes_resistance(self):
        self.match.switch_hero(self.enemy, heroes()[combat.REINHARDT])
        self.place(self.enemy, (3, 1, 0))
        self.advance(30, buttons=0)
        fight = self.match.combat
        target = fight._target_entity(self.enemy.body)
        self.assertEqual(fight.knockback(target, (10, 5, 0), self.run.frame), (7, 3.5, 0))
        passive = next(i for i in self.enemy.body_script.component.instances.values()
                       if i.graph.index == 0xA4)
        self.assertTrue(passive.state(4).active)
        passive.state(4).request(runtime.ABORT)
        self.enemy.body_script.component.run_now()
        self.advance(3, buttons=0)
        self.assertFalse(passive.state(4).active)
        self.assertEqual(fight.knockback(fight._target_entity(self.enemy.body), (10, 5, 0), self.run.frame),
                         (10, 5, 0))

    def test_impulse_uses_gravity_friction_and_settles_on_the_floor(self):
        fight = self.match.combat
        fight.knockback(fight._target_entity(self.enemy.body), (0, 5, 10), self.run.frame)
        self.advance(10, buttons=0)
        self.assertGreater(self.enemy.mover.position[1], 1)
        self.assertGreater(self.enemy.mover.position[2], 4)
        self.advance(240, buttons=0)
        self.assertAlmostEqual(self.enemy.mover.position[1], 1, places=2)
        self.assertAlmostEqual(math.hypot(*self.enemy.mover.velocity), 0, places=2)

    def test_large_impulse_sweeps_against_wall_instead_of_tunneling(self):
        charge_wall(self)
        fight = self.match.combat
        fight.knockback(fight._target_entity(self.enemy.body), (0, 1, 50), self.run.frame)
        self.advance(40, buttons=0)
        self.assertLess(self.enemy.mover.position[2], 8)
        self.assertGreater(self.enemy.mover.position[2], 7)
        self.assertEqual(self.health(self.enemy), 200)

    def test_stun_denies_input_but_does_not_anchor_physical_knockback(self):
        fight = self.match.combat
        target = fight._target_entity(self.enemy.body)
        fight.control(target, self.rein.body, "stun", .5)
        fight.knockback(target, (10, 5, 0), self.run.frame)
        self.advance(10, buttons=0)
        self.assertTrue(controlled(fight, self.enemy.body))
        self.assertGreater(self.enemy.mover.position[0], 1)
        self.assertGreater(self.enemy.mover.position[1], 1)

    def test_pin_anchors_target_against_unrelated_impulses(self):
        self.cast()
        fight = self.match.combat
        self.assertTrue(pinned(fight, self.enemy.body))
        self.assertEqual(fight.knockback(fight._target_entity(self.enemy.body), (10, 5, 0), self.run.frame),
                         (0, 0, 0))

    def test_charge_bump_and_timeout_release_apply_resource_impulses(self):
        self.place(self.other, (1.6, 1, 3))
        self.cast(65)
        self.assertEqual(self.health(self.other), 150)
        self.assertGreater(self.other.mover.position[0], 1.6)
        self.assertGreater(self.other.mover.position[1], 1)
        self.assertGreater(self.other.mover.position[2], 3)
        self.advance(160, buttons=0)
        self.assertFalse(pinned(self.match.combat, self.enemy.body))
        self.assertEqual(self.health(self.enemy), 200)
        self.assertGreater(self.enemy.mover.velocity[1], 0)

    def test_dead_target_ignores_impulses_and_replacement_has_no_old_velocity(self):
        fight = self.match.combat
        target = fight._target_entity(self.enemy.body)
        fight._damage(self.rein, target, 1000, False, None, self.run.frame)
        self.assertEqual(fight.knockback(target, (10, 5, 0), self.run.frame), (0, 0, 0))
        self.match.switch_hero(self.enemy, self.enemy.hero)
        self.assertEqual(self.enemy.mover.velocity, (0, 0, 0))


class ReinhardtFlashbangTests(unittest.TestCase):
    health = HammerTests.health
    place = staticmethod(HammerTests.place)
    advance = HammerTests.advance
    step = flashbang_tests.FlashbangTests.step

    def setUp(self):
        if BARRIER_DATA.is_file():
            self.enterContext(patch.dict(os.environ, {"ORPHEUS_REINHARDT_COLLISION": str(BARRIER_DATA)}))
        self.run = Run(KANEZAKA_DM, ("Reinhardt", "McCree"))
        self.match = self.run.match
        self.match.bots.clear()
        self.rein, self.enemy = self.run.players
        self.match.switch_hero(self.rein, heroes()[combat.REINHARDT])
        self.match.switch_hero(self.enemy, heroes()[0x02E0000000000042])
        self.place(self.rein, (0, 1, 0))
        self.place(self.enemy, (0, 1, 3))
        self.advance(30, buttons=0)

    def cast_flash(self, **kwargs):
        flashbang_tests.FlashbangTests.cast(self, self.run, self.enemy, self.rein, **kwargs)

    def test_actual_flashbang_interrupts_hammer_and_allows_a_fresh_swing_after_expiry(self):
        for button, release in ((1, False), (1, True), (0x800, False), (0x800, True)):
            with self.subTest(button=button, release=release):
                self.setUp()
                self.advance(10, buttons=button)
                self.cast_flash(target_buttons=button)
                fight = self.match.combat
                self.assertTrue(fight.is_stunned(self.rein))
                self.assertEqual(self.health(self.rein), 480)
                before = self.health(self.enemy)
                end = fight.stuns[self.rein.body]
                while self.run.frame < end - 1:
                    self.step(self.run, self.enemy, self.rein, target_buttons=button)
                    self.assertEqual(self.health(self.enemy), before)
                if release:
                    self.advance(30, buttons=0)
                    self.assertFalse(fight.is_stunned(self.rein))
                    self.assertEqual(self.health(self.enemy), before)
                    self.advance(20, buttons=button)
                    self.assertEqual(self.health(self.enemy), before)
                    self.advance(25, buttons=button)
                    self.assertEqual(self.health(self.enemy), before - 85)
                else:
                    for _ in range(65):
                        self.step(self.run, self.enemy, self.rein, target_buttons=button)
                    self.assertLess(self.health(self.enemy), before)

    def test_actual_flashbang_stops_charge_and_it_does_not_resume_after_stun(self):
        self.place(self.enemy, (0, 1, 10))
        self.advance(1, buttons=8)
        self.advance(43, buttons=0)
        self.assertIn(self.rein.body, self.match.combat.charges)
        self.cast_flash()
        self.assertFalse(self.match.combat.charges)
        z = self.rein.mover.position[2]
        for _ in range(90):
            self.step(self.run, self.enemy, self.rein)
        self.assertEqual(self.rein.mover.position[2], z)
        self.assertFalse(self.match.combat.is_stunned(self.rein))

    @unittest.skipUnless(BARRIER_DATA.is_file(), "prepared external barrier collision required")
    def test_actual_flashbang_damages_barrier_without_stunning_or_damaging_wearer(self):
        self.advance(15, buttons=2)
        self.step(self.run, self.enemy, self.rein, cast=16, target_buttons=2)
        for _ in range(60):
            self.step(self.run, self.enemy, self.rein, target_buttons=2)
        self.assertEqual(self.health(self.rein), 500)
        self.assertFalse(self.match.combat.is_stunned(self.rein))
        self.assertEqual(self.match.combat.reinhardt_barriers[self.rein.body].current, 1575)

    def test_horizontal_flashbang_drops_and_stuns_on_range_expiry(self):
        self.place(self.enemy, (0, 1, 8.25))
        fight = self.match.combat
        self.run.step({self.enemy: (-32768, 0, 16)})
        for _ in range(40):
            if fight.rockets:
                break
            self.run.step({self.enemy: (-32768, 0, 0)})
        self.assertEqual(len(fight.rockets), 1)
        rocket = fight.rockets[0]
        self.assertEqual(rocket.gravity, 20)
        self.assertTrue(rocket.detonate_on_expiry)
        self.assertGreater(rocket.velocity[1], 0)
        self.assertAlmostEqual(rocket.left, 7 / 30)
        launch_height = rocket.position[1]
        for _ in range(20):
            if not fight.rockets:
                break
            self.assertEqual(self.health(self.rein), 500)
            self.assertFalse(fight.is_stunned(self.rein))
            self.run.step({})
        self.assertFalse(fight.rockets)
        self.assertAlmostEqual(rocket.flown, 7 / 30)
        self.assertLess(rocket.position[1], launch_height)
        self.assertGreater(rocket.position[2], 1)
        self.assertEqual(self.health(self.rein), 480)
        self.assertTrue(fight.is_stunned(self.rein))

    def test_actual_flashbang_during_charge_windup_cannot_resume_it_after_expiry(self):
        self.advance(1, buttons=8)
        self.advance(10, buttons=0)
        self.assertFalse(self.match.combat.charges)
        self.cast_flash()
        for _ in range(130):
            self.step(self.run, self.enemy, self.rein)
        self.assertEqual(self.rein.mover.position[2], 0)
        self.assertFalse(self.match.combat.charges)
        charge = next(i for i in self.rein.body_script.component.instances.values() if i.graph.index == 0x7D)
        self.assertEqual(charge.find_var(0, 59).value(), 0)
        self.assertGreater(charge.find_var(0, 61).value(), 7)

    def test_actual_flashbang_keeps_input_blocked_while_external_knockback_moves_body(self):
        self.cast_flash()
        fight = self.match.combat
        self.assertEqual(fight.knockback(fight._target_entity(self.rein.body), (10, 5, 0), self.run.frame),
                         (7, 3.5, 0))
        for _ in range(10):
            self.step(self.run, self.enemy, self.rein, target_buttons=1 | 8, forward=127)
        self.assertTrue(fight.is_stunned(self.rein))
        self.assertGreater(self.rein.mover.position[0], 1)
        self.assertGreater(self.rein.mover.position[1], 1)
        self.assertFalse(self.rein.body_script.component.held)


class ReinhardtPassiveChargeTests(unittest.TestCase):
    def setUp(self):
        HammerTests.setUp(self)

    def advance(self, frames):
        for _ in range(frames):
            self.run.step({})

    def charge(self):
        return self.rein.body_script.component.entity_var(342).value()

    def test_actual_match_passive_uses_authored_five_per_second(self):
        script = self.rein.body_script
        self.assertIs(script.component.entity_var(2426).value(), True)
        self.assertEqual(self.charge(), 0)
        self.advance(625)
        self.assertEqual(self.charge(), 50)
        self.assertEqual(script.ult_seconds, 60.0)  # legacy helper is bypassed for this Match body

    def test_actual_hammer_hit_caps_charge_and_earthshatter_spends_it(self):
        HammerTests.place(self.other, (12, 1, 0))
        self.advance(125)
        self.assertEqual(self.charge(), 10)
        script = self.rein.body_script
        self.assertEqual(script.component.entity_var(338).value(), 1540)
        script.set_ult_charge(1500)
        fight = self.match.combat
        victim = fight.vitals_of(fight._target_entity(self.enemy.body))
        for _ in range(45):
            before = self.charge()
            self.run.step({self.rein: (0, 0, 1)})
            if victim.total < 200:
                break
        self.assertEqual(victim.total, 115)
        self.assertLess(before, 1540)
        self.assertEqual(self.charge(), 1540)
        self.advance(45)
        self.assertEqual(self.charge(), 1540)
        self.run.step({self.rein: (0, 0, 32)})
        self.assertEqual(self.charge(), 0)
        self.advance(65)
        self.assertTrue(victim.dead)
        self.assertEqual(self.charge(), 0)

    def test_actual_match_passive_multipliers_suppression_and_effective_cost_cap(self):
        script = self.rein.body_script
        script.component.set(9532, 2.0)
        script.component.set(12918, 3.0)
        self.advance(125)
        self.assertEqual(self.charge(), 60)
        script.component.set(842, True)
        self.advance(125)
        self.assertEqual(self.charge(), 60)
        script.component.set(842, False)
        script.set_ult_charge(1538)
        self.advance(63)
        self.assertEqual(self.charge(), 1540)

    def test_body_capability_expression_can_disable_charge_without_a_forced_true(self):
        script = self.rein.body_script
        common = next(i for i in script.component.instances.values() if i.graph.index == 0x1C7)
        common.write((0, 59), combat.expr.Asset(0x0D80000000000016))
        script.component.set(2426, None)
        self.advance(125)
        self.assertIs(script.component.entity_var(2426).value(), False)
        self.assertEqual(self.charge(), 0)

    def test_actual_earthshatter_keeps_authored_charge_lock_after_native_channel_end(self):
        script = self.rein.body_script
        script.set_ult_charge(1540)
        self.run.step({self.rein: (0, 0, 32)})
        self.assertEqual(self.charge(), 0)
        for _ in range(100):
            if not any(state.flags & state.RUNNING for state in script.ultimates):
                break
            self.assertEqual(self.charge(), 0)
            self.advance(1)
        else:
            self.fail('Earthshatter channel did not retire')
        instance = next(i for i in script.component.instances.values() if i.graph.index == 0xBE)
        lock = instance.states[27]
        self.assertTrue(lock.active)
        self.assertEqual(lock.ms, 2500)
        self.assertIs(script.component.entity_var(842).value(), True)
        self.advance(63)
        self.assertEqual(self.charge(), 0)
        for _ in range(200):
            if not lock.active:
                break
            self.assertEqual(self.charge(), 0)
            self.advance(1)
        else:
            self.fail('Authored Earthshatter charge lock did not expire')
        self.advance(63)
        self.assertEqual(self.charge(), 5)

    def test_respawn_preserves_earned_charge_and_switching_hero_resets_it(self):
        self.advance(125)
        self.assertEqual(self.charge(), 10)
        fight = self.match.combat
        old_body = self.rein.body
        fight._damage(self.enemy, fight._target_entity(old_body), 10000, False, None, self.run.frame)
        self.assertTrue(fight.is_dead(self.rein))
        for _ in range(700):
            previous_charge = self.charge()
            self.advance(1)
            if self.rein.body != old_body:
                break
        self.assertNotEqual(self.rein.body, old_body)
        self.assertFalse(fight.is_dead(self.rein))
        self.assertGreaterEqual(previous_charge, 10)
        self.assertEqual(self.charge(), previous_charge)
        self.advance(63)
        self.assertEqual(self.charge(), previous_charge + 5)
        self.assertTrue(self.match.switch_hero(self.rein, heroes()[SOLDIER]))
        self.assertTrue(self.match.switch_hero(self.rein, heroes()[combat.REINHARDT]))
        self.assertEqual(self.charge(), 0)

    def test_spent_earthshatter_is_not_refunded_by_death_and_respawn(self):
        self.rein.body_script.set_ult_charge(1540)
        self.run.step({self.rein: (0, 0, 32)})
        self.assertEqual(self.charge(), 0)
        fight = self.match.combat
        fight._damage(self.enemy, fight._target_entity(self.rein.body), 10000, False, None, self.run.frame)
        self.assertTrue(fight.is_dead(self.rein))
        fight._respawn(self.rein)
        self.assertFalse(fight.is_dead(self.rein))
        self.assertEqual(self.charge(), 0)
        self.assertFalse(fight.shatters)

    def test_delayed_same_hero_body_creation_consumes_retained_charge_once(self):
        self.advance(63)
        self.assertEqual(self.charge(), 5)
        old_script = self.rein.body_script
        self.rein.spawned = False
        with patch.object(self.match, '_next_body_id', return_value=None):
            self.assertFalse(self.match.switch_hero(self.rein, self.rein.hero))
        self.assertIs(self.rein.body_script, old_script)
        self.assertIsNone(self.rein.retained_ult_charge)
        self.assertTrue(self.match.switch_hero(self.rein, self.rein.hero))
        self.assertEqual(self.rein.retained_ult_charge, 5)
        self.assertTrue(old_script.retired)
        self.assertIsNone(self.rein.body_script)
        self.assertTrue(self.match.switch_hero(self.rein, self.rein.hero))
        self.assertEqual(self.rein.retained_ult_charge, 5)
        self.rein.spawned = True
        self.match.own_body_updates(self.rein)
        self.assertEqual(self.charge(), 5)
        self.assertIsNone(self.rein.retained_ult_charge)

        self.rein.spawned = False
        self.assertTrue(self.match.switch_hero(self.rein, self.rein.hero))
        self.assertEqual(self.rein.retained_ult_charge, 5)
        self.assertTrue(self.match.switch_hero(self.rein, heroes()[SOLDIER]))
        self.assertTrue(self.match.switch_hero(self.rein, heroes()[combat.REINHARDT]))
        self.rein.spawned = True
        self.match.own_body_updates(self.rein)
        self.assertEqual(self.charge(), 0)

    def test_native_relation_uses_actual_controller_identity_and_retires_with_body(self):
        script = self.rein.body_script
        context = script.world.entity_possessor_context(script.entity)
        self.assertEqual(context.lookup(script.entity).possessor.current, self.rein.entity)
        self.assertIsNone(context.lookup(self.rein.entity).possessor)
        self.match.switch_hero(self.rein, heroes()[SOLDIER])
        self.assertTrue(context.retired)
        self.assertIsNone(script.world.match)
        self.assertIsNone(self.rein.body_script.world._possessor_query)


class EarthshatterTests(unittest.TestCase):
    health = HammerTests.health
    place = staticmethod(HammerTests.place)
    step = flashbang_tests.FlashbangTests.step

    def setUp(self):
        if BARRIER_DATA.is_file():
            self.enterContext(patch.dict(os.environ, {"ORPHEUS_REINHARDT_COLLISION": str(BARRIER_DATA)}))
        ChargeContactTests.setUp(self)
        self.place(self.enemy, (0, 1, 10))
        self.place(self.other, (20, 1, 0))

    def advance(self, frames, buttons=0, yaw=0):
        for _ in range(frames):
            self.run.step({self.rein: (yaw, 0, buttons)})

    def fill(self):
        self.rein.body_script.set_ult_charge(self.rein.body_script.component.entity_var(338).value())

    def cast(self, frames=90, yaw=0):
        self.fill()
        self.advance(1, 32, yaw)
        self.advance(frames, yaw=yaw)

    def test_authored_windup_front_speed_range_end_and_actual_cost_consumption(self):
        self.assertEqual(self.rein.body_script.component.entity_var(338).value(), 1540)
        self.fill()
        self.advance(1, 32)
        self.assertEqual(self.rein.body_script.component.entity_var(342).value(), 0)
        self.advance(27)
        self.assertFalse(self.match.combat.shatters)
        self.advance(1)
        wave = self.match.combat.shatters[self.rein.body]
        self.assertEqual((wave.speed, wave.reach, wave.cone, wave.stun_seconds), (40, 20, 50, 2.5))
        self.advance(10)
        self.assertAlmostEqual(wave.travelled, 6.4, places=5)
        self.assertEqual(self.health(self.enemy), 200)
        self.advance(22)
        self.assertFalse(self.match.combat.shatters)
        self.assertTrue(all(not s.flags & s.RUNNING for s in self.rein.body_script.ultimates))
        self.assertEqual(self.health(self.enemy), 150)

    def test_near_250_and_wave_50_are_separate_authored_hits_on_a_surviving_body(self):
        self.match.switch_hero(self.enemy, next(h for h in heroes().values() if h.name == "Roadhog"))
        self.place(self.enemy, (0, 1, 3))
        before = self.health(self.enemy)
        self.cast()
        self.assertEqual(self.health(self.enemy), before - 300)
        self.assertEqual([h["+0x78"]["+0x4"] for h in self.rein.client.messages(combat.HIT)], [250, 50])

    def test_far_hit_has_one_damage_event_and_exclusive_control_deadline(self):
        self.fill()
        self.advance(1, 32)
        for _ in range(80):
            self.advance(1)
            if self.health(self.enemy) < 200:
                break
        self.assertEqual(self.health(self.enemy), 150)
        self.assertEqual(control_deadline(self.match.combat, self.enemy.body) - self.match.combat.tick, 157)
        self.advance(156)
        self.assertTrue(controlled(self.match.combat, self.enemy.body))
        self.advance(1)
        self.assertFalse(controlled(self.match.combat, self.enemy.body))
        self.assertEqual(self.health(self.enemy), 150)

    def test_live_earthshatter_hits_replacement_only_when_front_reaches_it(self):
        fight = self.match.combat
        old_body = self.enemy.body
        old_key = (old_body, fight.replacement_ticks.get(old_body))
        self.fill()
        self.advance(1, 32)
        for _ in range(80):
            self.advance(1)
            if controlled(fight, old_body):
                break
        wave = fight.shatters[self.rein.body]
        self.assertEqual(wave.hit, {old_key})
        self.assertEqual(self.health(self.enemy), 150)

        self.assertTrue(self.match.switch_hero(self.enemy, self.enemy.hero))
        replacement = self.enemy.body
        self.assertNotEqual(replacement, old_body)
        self.place(self.enemy, (0, 1, 17))
        self.assertFalse(controlled(fight, old_body))
        self.advance(2)
        self.assertEqual(self.health(self.enemy), 200)
        self.assertFalse(controlled(fight, replacement))

        for _ in range(40):
            self.advance(1)
            if controlled(fight, replacement):
                break
        self.assertEqual(wave.hit, {old_key, (replacement, fight.replacement_ticks[replacement])})
        self.assertEqual(self.health(self.enemy), 150)
        self.assertEqual(control_deadline(fight, replacement) - fight.tick, 157)
        self.advance(157)
        self.assertFalse(controlled(fight, replacement))
        self.assertEqual(self.health(self.enemy), 150)

    def test_earthshatter_hits_replacement_that_reuses_the_first_victims_id(self):
        fight = self.match.combat
        old_body = self.enemy.body
        self.fill()
        self.advance(1, 32)
        for _ in range(80):
            self.advance(1)
            if controlled(fight, old_body):
                break
        self.assertTrue(controlled(fight, old_body))
        self.assertEqual(self.health(self.enemy), 150)
        self.assertIn(self.rein.body, fight.shatters)

        self.assertTrue(self.match.switch_hero(self.enemy, self.enemy.hero))
        self.assertNotEqual(self.enemy.body, old_body)
        with patch.object(self.enemy.client, 'body_id_available', side_effect=lambda entity: entity == old_body):
            self.assertTrue(self.match.switch_hero(self.enemy, self.enemy.hero))
        self.assertEqual(self.enemy.body, old_body)
        self.place(self.enemy, (0, 1, 17))
        self.advance(2)
        self.assertEqual(self.health(self.enemy), 200)
        self.assertFalse(controlled(fight, old_body))
        for _ in range(40):
            self.advance(1)
            if controlled(fight, old_body):
                break
        self.assertEqual(self.health(self.enemy), 150)
        self.assertEqual(control_deadline(fight, old_body) - fight.tick, 157)
        self.advance(157)
        self.assertFalse(controlled(fight, old_body))
        self.assertEqual(self.health(self.enemy), 150)

    @unittest.skipUnless(BARRIER_DATA.is_file(), 'prepared external barrier collision required')
    def test_earthshatter_hits_a_recycled_shield_in_an_unblocked_lane(self):
        fight = self.match.combat
        hero = heroes()[combat.REINHARDT]
        self.assertTrue(self.match.switch_hero(self.enemy, hero))
        self.place(self.enemy, (-2.5, 1, 2.5))
        self.place(self.other, (-4.5, 1, 12))
        hold = {self.enemy: (-32768, 0, 2)}
        for _ in range(30):
            self.run.step(hold)
        old_body = self.enemy.body
        old_shield = fight.reinhardt_barriers[old_body]
        self.fill()
        self.run.step({**hold, self.rein: (0, 0, 32)})
        for _ in range(60):
            self.run.step(hold)
            if old_shield.current < 1600:
                break
        self.assertEqual(old_shield.current, 1550)
        wave = fight.shatters[self.rein.body]
        self.assertTrue(wave.blocked)

        self.assertTrue(self.match.switch_hero(self.enemy, heroes()[SOLDIER]))
        with patch.object(self.enemy.client, 'body_id_available', side_effect=lambda entity: entity == old_body):
            self.assertTrue(self.match.switch_hero(self.enemy, hero))
        self.assertEqual(self.enemy.body, old_body)
        self.place(self.enemy, (6, 1, 19))
        self.run.step({self.enemy: (-32768, 0, 0)})
        for _ in range(25):
            self.run.step(hold)
            if fight.reinhardt_barriers[old_body].active:
                break
        shield = fight.reinhardt_barriers[old_body]
        self.assertIsNot(shield, old_shield)
        self.assertTrue(shield.active)
        self.assertEqual(shield.current, 1600)
        for _ in range(30):
            self.run.step(hold)
        self.assertEqual(shield.current, 1550)
        self.assertEqual(old_shield.current, 1550)
        self.assertEqual(self.health(self.enemy), 500)
        self.assertEqual(self.health(self.other), 200)
        self.assertFalse(controlled(fight, self.other.body))

    @unittest.skipUnless(BARRIER_DATA.is_file(), 'prepared external barrier collision required')
    def test_earthshatter_hits_a_later_shield_raise_in_an_unblocked_lane(self):
        fight = self.match.combat
        self.assertTrue(self.match.switch_hero(self.enemy, heroes()[combat.REINHARDT]))
        self.place(self.enemy, (-2.5, 1, 2.5))
        self.place(self.other, (-4.5, 1, 12))
        hold = {self.enemy: (-32768, 0, 2)}
        for _ in range(30):
            self.run.step(hold)
        shield = fight.reinhardt_barriers[self.enemy.body]
        state = reinhardt_barrier_instance(self.enemy).states[17]
        first_raise = state.counter
        self.fill()
        self.run.step({**hold, self.rein: (0, 0, 32)})
        for _ in range(60):
            self.run.step(hold)
            if shield.current < 1600:
                break
        self.assertEqual(shield.current, 1550)
        wave = fight.shatters[self.rein.body]
        self.assertTrue(wave.blocked)

        self.run.step({self.enemy: (-32768, 0, 0)})
        self.assertFalse(shield.active)
        self.place(self.enemy, (6, 1, 19))
        for _ in range(25):
            self.run.step(hold)
            if shield.active:
                break
        self.assertTrue(shield.active)
        self.assertNotEqual(state.counter, first_raise)
        self.assertIs(fight.shatters[self.rein.body], wave)
        self.assertEqual(shield.current, 1550)
        for _ in range(30):
            self.run.step(hold)
        self.assertEqual(shield.current, 1500)
        generation = fight.replacement_ticks.get(self.enemy.body)
        self.assertEqual(wave.shields, {
            (self.enemy.body, generation, first_raise),
            (self.enemy.body, generation, state.counter),
        })
        self.assertEqual(self.health(self.enemy), 500)
        self.assertEqual(self.health(self.other), 200)
        self.assertFalse(controlled(fight, self.other.body))

    def test_disconnect_retires_front_but_preserves_existing_knockdown(self):
        self.place(self.other, (0, 1, 18))
        fight = self.match.combat
        old = self.rein.body
        self.fill()
        self.advance(1, buttons=32)
        for _ in range(65):
            self.advance(1)
            if controlled(fight, self.enemy.body):
                break
        self.assertTrue(controlled(fight, self.enemy.body))
        self.assertEqual(self.health(self.enemy), 150)
        self.assertEqual(self.health(self.other), 200)
        self.assertIn(old, fight.shatters)
        self.match.leave(self.rein)
        self.run.players.remove(self.rein)
        self.assertNotIn(old, fight.shatters)
        self.assertTrue(controlled(fight, self.enemy.body))
        for _ in range(200):
            self.run.step({})
        self.assertFalse(controlled(fight, self.enemy.body))
        self.assertEqual(self.health(self.enemy), 150)
        self.assertEqual(self.health(self.other), 200)
        self.assertFalse(controlled(fight, self.other.body))

    def test_range_cone_and_rear_misses(self):
        for position in ((0, 1, 21.5), (5, 1, 8.66), (0, 1, -3)):
            with self.subTest(position=position):
                self.setUp()
                self.place(self.enemy, position)
                self.cast()
                self.assertEqual(self.health(self.enemy), 200)
                self.assertFalse(controlled(self.match.combat, self.enemy.body))

    def test_team_filter_and_miss_still_spend_charge(self):
        self.match.game_map = self.match.game_map.__class__(
            **{**self.match.game_map.__dict__, "free_for_all": False})
        self.enemy.team = self.rein.team
        self.cast()
        self.assertEqual(self.health(self.enemy), 200)
        self.assertLess(self.rein.body_script.component.entity_var(342).value(), 100)

    def test_wave_uses_target_team_when_the_front_arrives(self):
        self.match.game_map = replace(self.match.game_map, free_for_all=False)
        self.rein.team, self.enemy.team, self.other.team = 0, 1, 0
        self.place(self.other, (0, 1, 14))
        self.fill()
        self.advance(1, 32)
        fight = self.match.combat
        for _ in range(60):
            if self.rein.body in fight.shatters:
                break
            self.advance(1)
        wave = fight.shatters[self.rein.body]
        self.assertLess(wave.travelled, 8)
        self.enemy.team, self.other.team = 0, 1

        self.advance(65)
        self.assertEqual(self.health(self.enemy), 200)
        self.assertFalse(controlled(fight, self.enemy.body))
        self.assertEqual(self.health(self.other), 150)
        self.assertTrue(controlled(fight, self.other.body))
        self.assertEqual(wave.hit, {(self.other.body, fight.replacement_ticks.get(self.other.body))})
        self.advance(200)
        self.assertFalse(controlled(fight, self.other.body))
        self.assertEqual(self.health(self.other), 150)

    @unittest.skipUnless(BARRIER_DATA.is_file(), 'prepared external barrier collision required')
    def test_wave_uses_caster_team_for_barrier_and_body_at_contact(self):
        for shield_becomes_hostile in (False, True):
            with self.subTest(shield_becomes_hostile=shield_becomes_hostile):
                self.setUp()
                self.match.game_map = replace(self.match.game_map, free_for_all=False)
                initial_team = int(shield_becomes_hostile)
                self.rein.team, self.enemy.team, self.other.team = initial_team, 1, initial_team
                self.assertTrue(self.match.switch_hero(self.enemy, heroes()[combat.REINHARDT]))
                self.place(self.enemy, (0, 1, 13))
                self.place(self.other, (0, 1, 17))
                fight = self.match.combat
                hold = {self.enemy: (-32768, 0, 2)}
                for _ in range(30):
                    self.run.step(hold)
                shield = fight.reinhardt_barriers[self.enemy.body]
                self.assertTrue(shield.active)

                self.fill()
                self.run.step({**hold, self.rein: (0, 0, 32)})
                for _ in range(55):
                    self.run.step(hold)
                    wave = fight.shatters.get(self.rein.body)
                    if wave is not None and wave.travelled > 0:
                        break
                self.assertIsNotNone(wave)
                self.assertGreater(wave.travelled, 0)
                self.assertLess(wave.travelled, 8)
                self.assertEqual(shield.current, 1600)
                self.assertFalse(wave.hit)
                self.assertFalse(wave.shields)

                self.rein.team = 1 - initial_team
                for _ in range(42):
                    self.run.step(hold)
                self.assertEqual(shield.current, 1550 if shield_becomes_hostile else 1600)
                self.assertEqual(50 in wave.blocked, shield_becomes_hostile)
                self.assertEqual(bool(wave.shields), shield_becomes_hostile)
                self.assertEqual(self.health(self.enemy), 500)
                self.assertFalse(controlled(fight, self.enemy.body))
                self.assertEqual(self.health(self.other), 200 if shield_becomes_hostile else 150)
                self.assertEqual(controlled(fight, self.other.body), not shield_becomes_hostile)

    def test_caster_team_change_preserves_knockdown_and_updates_later_contacts(self):
        self.match.game_map = replace(self.match.game_map, free_for_all=False)
        self.rein.team, self.enemy.team, self.other.team = 0, 1, 0
        self.place(self.enemy, (0, 1, 8))
        self.place(self.other, (0, 1, 17))
        fight = self.match.combat
        self.fill()
        self.advance(1, 32)
        first = (self.enemy.body, self.rein.body, "earthshatter")
        for _ in range(65):
            self.advance(1)
            if first in fight.controls:
                break
        self.assertIn(first, fight.controls)
        self.assertLess(fight.shatters[self.rein.body].travelled, 14)
        self.assertEqual(self.health(self.enemy), 150)
        self.assertEqual(self.health(self.other), 200)
        deadline = fight.controls[first]
        self.rein.team = 1

        later = (self.other.body, self.rein.body, "earthshatter")
        for _ in range(30):
            self.advance(1)
            if later in fight.controls:
                break
        self.assertEqual(self.health(self.enemy), 150)
        self.assertEqual(fight.controls[first], deadline)
        self.assertTrue(controlled(fight, self.enemy.body))
        self.assertEqual(self.health(self.other), 150)
        self.assertGreater(fight.controls[later], deadline)
        while fight.now < deadline:
            self.advance(1)
        self.assertFalse(controlled(fight, self.enemy.body))
        self.assertTrue(controlled(fight, self.other.body))
        self.advance(25)
        self.assertFalse(controlled(fight, self.other.body))

    def test_release_heading_is_used_and_wave_does_not_turn_with_later_view(self):
        self.fill()
        self.advance(1, 32)
        self.advance(28)
        self.assertIn(self.rein.body, self.match.combat.shatters)
        self.advance(60, yaw=16384)
        self.assertEqual(self.health(self.enemy), 150)
        self.assertEqual(self.health(self.other), 200)

    def test_rotated_release_hits_east_target(self):
        self.place(self.enemy, (10, 1, 0))
        self.cast(yaw=16384)
        self.assertEqual(self.health(self.enemy), 150)

    def test_full_wall_stops_propagation(self):
        charge_wall(self, 6)
        self.cast()
        self.assertEqual(self.health(self.enemy), 200)
        self.assertFalse(controlled(self.match.combat, self.enemy.body))

    def test_wall_stops_wave_after_hitting_exposed_body_in_the_same_step(self):
        angle = math.radians(25)
        forward_x, forward_z = math.sin(angle), math.cos(angle)
        wall_z = 10.81 * forward_z
        charge_wall(self, wall_z)
        # The outermost ray grazes this capsule during the step that hits the wall.
        exposed = (10.47 * forward_x + .48 * forward_z, 1, 10.47 * forward_z - .48 * forward_x)
        self.place(self.enemy, exposed)
        self.place(self.other, (0, 1, wall_z + 1))
        fight = self.match.combat
        target = fight._target_entity(self.enemy.body)
        self.assertGreater(wall_z - target.feet[2], target.radius)

        self.cast(44)
        wave = fight.shatters[self.rein.body]
        self.assertAlmostEqual(wave.travelled, 10.24, places=4)
        self.assertNotIn(100, wave.blocked)
        self.assertEqual(self.health(self.enemy), 200)
        self.advance(1)
        self.assertIn(100, wave.blocked)
        self.assertAlmostEqual(wave.fronts[100][2], wall_z, places=4)
        self.assertEqual(self.health(self.enemy), 150)
        self.assertTrue(controlled(fight, self.enemy.body))
        self.advance(45)
        self.assertEqual(self.health(self.other), 200)
        self.assertFalse(controlled(fight, self.other.body))

    def test_wave_cannot_hit_a_capsule_touching_the_far_face_of_a_wall(self):
        wall_z = 9.75
        charge_wall(self, wall_z)
        target = self.match.combat._target_entity(self.enemy.body)
        self.place(self.enemy, (0, 1, wall_z + target.radius))
        self.cast(55)
        self.assertEqual(self.enemy.mover.position[2] - wall_z, target.radius)
        self.assertEqual(self.health(self.enemy), 200)
        self.assertFalse(controlled(self.match.combat, self.enemy.body))

    def test_near_modifier_is_occluded_and_late_entry_behind_front_is_not_a_new_hit(self):
        self.place(self.enemy, (0, 1, 3))
        charge_wall(self, 1)
        self.cast()
        self.assertEqual(self.health(self.enemy), 200)
        self.setUp()
        self.place(self.enemy, (20, 1, 0))
        self.fill()
        self.advance(1, 32)
        self.advance(52)  # front has already passed8m
        self.place(self.enemy, (0, 1, 8))
        self.advance(40)
        self.assertEqual(self.health(self.enemy), 200)

    def test_native_channel_ends_and_refilled_fresh_input_can_cast_a_second_wave(self):
        self.cast()
        self.assertEqual(self.health(self.enemy), 150)
        self.cast()
        self.assertEqual(self.health(self.enemy), 100)
        self.assertFalse(self.match.combat.shatters)

    def terrain(self, *, rise=0, gap=False):
        start = 8 if gap else 5
        level = 1 + rise
        triangles = [(-50, 1, -50, -50, 1, 5, 50, 1, 5),
                     (-50, 1, -50, 50, 1, 5, 50, 1, -50),
                     (-50, level, start, -50, level, 50, 50, level, 50),
                     (-50, level, start, 50, level, 50, 50, level, start)]
        if rise:
            triangles += [(-50, 1, 5, -50, level, 5, 50, level, 5),
                          (-50, 1, 5, 50, level, 5, 50, 1, 5)]
        world = World(triangles)
        self.match.collision_world = world
        self.match.collision_provider.state = LoadState("ready", world)
        for player in self.run.players:
            player.mover.use(world=world)
        self.place(self.enemy, (0, level, 10))

    def test_ground_front_crosses_small_step_but_not_gap_or_large_ledge(self):
        for rise, gap, damage in ((.5, False, 50), (0, True, 0), (3, False, 0)):
            with self.subTest(rise=rise, gap=gap):
                self.setUp()
                self.terrain(rise=rise, gap=gap)
                self.cast()
                self.assertEqual(self.health(self.enemy), 200 - damage)

    @unittest.skipUnless(BARRIER_DATA.is_file(), "prepared external barrier collision required")
    def test_shield_stops_wave_and_drop_or_break_does_not_restart_the_front(self):
        for outcome in ("drop", "break", "team_change"):
            with self.subTest(outcome=outcome):
                self.setUp()
                self.match.game_map = replace(self.match.game_map, free_for_all=False)
                self.match.switch_hero(self.enemy, heroes()[combat.REINHARDT])
                self.rein.team, self.enemy.team, self.other.team = 0, 1, 1
                self.place(self.enemy, (0, 1, 8))
                self.place(self.other, (0, 1, 15))
                for _ in range(30):
                    self.run.step({self.enemy: (-32768, 0, 2)})
                shield = self.match.combat.reinhardt_barriers[self.enemy.body]
                if outcome == "break":
                    set_reinhardt_shield_health(self.match.combat, self.enemy.body, 30)
                self.fill()
                self.run.step({self.rein: (0, 0, 32), self.enemy: (-32768, 0, 2)})
                for _ in range(60):
                    self.run.step({self.enemy: (-32768, 0, 2)})
                    wave = self.match.combat.shatters.get(self.rein.body)
                    if wave is not None and wave.shields:
                        break
                self.assertTrue(wave is not None and wave.shields)
                if outcome == "team_change":
                    self.enemy.team = self.rein.team
                    for _ in range(90):
                        self.run.step({self.enemy: (-32768, 0, 2)})
                    self.assertTrue(shield.active)
                    self.assertNotIn(self.rein.body, self.match.combat.shatters)
                else:
                    self.advance(60)
                self.assertEqual(self.health(self.enemy), 500)
                self.assertEqual(self.health(self.other), 200)
                self.assertFalse(controlled(self.match.combat, self.enemy.body))
                self.assertEqual(shield.current, 0 if outcome == "break" else 1550)
                if outcome == "team_change":
                    self.fill()
                    self.run.step({self.rein: (0, 0, 32), self.enemy: (-32768, 0, 2)})
                    for _ in range(90):
                        self.run.step({self.enemy: (-32768, 0, 2)})
                    self.assertEqual(self.health(self.other), 150)
                    self.assertTrue(controlled(self.match.combat, self.other.body))
                    self.assertTrue(shield.active)
                    self.assertEqual(shield.current, 1550)

    @unittest.skipUnless(BARRIER_DATA.is_file(), "prepared external barrier collision required")
    def test_shield_protects_a_nearby_body_on_its_far_side(self):
        self.assertTrue(self.match.switch_hero(self.enemy, heroes()[combat.REINHARDT]))
        self.match.game_map = replace(self.match.game_map, free_for_all=False)
        self.rein.team, self.enemy.team, self.other.team = 0, 1, 1
        self.place(self.enemy, (0, 1, 10))
        self.place(self.other, (1.2, 1, 8.95))
        hold = {self.enemy: (-32768, 0, 2)}
        for _ in range(30):
            self.run.step(hold)
        fight = self.match.combat
        shield = fight.reinhardt_barriers[self.enemy.body]
        x, _, z = self.other.mover.position
        distance = math.hypot(x, z)
        shield_distance, owner, _ = fight._barrier_contact(
            self.rein, (0, 1.1, 0), (x / distance, 0, z / distance), distance,
        )
        self.assertIs(owner, self.enemy)
        self.assertLess(distance - shield_distance, fight._target_entity(self.other.body).radius)

        self.fill()
        self.run.step({**hold, self.rein: (0, 0, 32)})
        for _ in range(65):
            self.run.step(hold)
        self.assertEqual(shield.current, 1550)
        self.assertEqual(self.health(self.other), 200)
        self.assertFalse(controlled(fight, self.other.body))

    @unittest.skipUnless(BARRIER_DATA.is_file(), "prepared external barrier collision required")
    def test_dropping_shield_does_not_block_a_ray_that_has_not_reached_it(self):
        self.assertTrue(self.match.switch_hero(self.enemy, heroes()[combat.REINHARDT]))
        self.match.game_map = replace(self.match.game_map, free_for_all=False)
        self.rein.team, self.enemy.team, self.other.team = 0, 1, 1
        self.place(self.enemy, (0, 1, 10))
        angle = math.radians(-15)
        self.place(self.other, (12 * math.sin(angle), 1, 12 * math.cos(angle)))
        hold = {self.enemy: (-27000, 0, 2)}
        for _ in range(30):
            self.run.step(hold)
        fight = self.match.combat
        shield = fight.reinhardt_barriers[self.enemy.body]
        self.fill()
        self.run.step({**hold, self.rein: (0, 0, 32)})
        for _ in range(60):
            self.run.step(hold)
            if shield.current < 1600:
                break
        wave = fight.shatters[self.rein.body]
        self.assertEqual(shield.current, 1550)
        self.assertIn(72, wave.blocked)
        self.assertNotIn(20, wave.blocked)
        stopped_front = wave.fronts[72]

        self.advance(35)
        self.assertFalse(shield.active)
        self.assertEqual(wave.fronts[72], stopped_front)
        self.assertNotIn(20, wave.blocked)
        self.assertEqual(self.health(self.enemy), 500)
        self.assertEqual(self.health(self.other), 150)
        self.assertTrue(controlled(fight, self.other.body))

    def test_insufficient_charge_cannot_start_and_retirement_retires_wave(self):
        self.rein.body_script.set_ult_charge(1000)
        self.advance(1, 32)
        self.advance(60)
        self.assertFalse(self.match.combat.shatters)
        self.fill()
        self.advance(1, 32)
        self.advance(28)
        self.assertIn(self.rein.body, self.match.combat.shatters)
        self.match.switch_hero(self.rein, self.rein.hero)
        self.advance(60)
        self.assertFalse(self.match.combat.shatters)
        self.assertEqual(self.health(self.enemy), 200)

    def test_air_cast_waits_for_ground_proximity_and_releases_on_the_floor(self):
        self.place(self.rein, (0, 6, 0))
        self.fill()
        self.advance(1, 32)
        self.advance(10)
        self.assertFalse(self.match.combat.shatters)
        self.assertEqual(self.health(self.enemy), 200)
        self.advance(120)
        self.assertEqual(self.health(self.enemy), 150)
        self.assertAlmostEqual(self.rein.mover.position[1], 1, places=2)

    def test_actual_flashbang_cancels_airborne_shatter_before_landing(self):
        self.assertTrue(self.match.switch_hero(self.other, heroes()[0x02E0000000000042]))
        self.place(self.rein, (0, 10, 0))
        self.place(self.other, (3, 10, 0))
        self.fill()
        self.advance(1, 32)
        meter = self.rein.body_script.component.entity_var(342)
        self.assertEqual(meter.value(), 0)
        self.advance(12)
        fight = self.match.combat
        self.assertNotIn(self.rein.body, fight.shatters)
        flashbang_tests.FlashbangTests.cast(self, self.run, self.other, self.rein)
        self.assertTrue(fight.is_stunned(self.rein))
        self.assertGreater(self.rein.mover.position[1], 2)
        for _ in range(160):
            self.advance(1)
            self.assertNotIn(self.rein.body, fight.shatters)
        self.assertAlmostEqual(self.rein.mover.position[1], 1, places=2)
        self.assertEqual(self.health(self.enemy), 200)
        self.assertFalse(controlled(fight, self.enemy.body))
        self.assertLess(meter.value(), 100)

    def test_windup_movement_modifiers_deny_throttle_but_release_does_not_lock_it_forever(self):
        self.fill()
        for index in range(20):
            self.run.frame += 1
            self.match.update(self.run.frame * .016, self.run.frame)
            self.rein.take_commands([Command(self.run.frame, forward=127, buttons=32 if index == 0 else 0)])
        stopped = tuple(self.rein.mover.position)
        # Authored throttle mods suppress acceleration, preserving the command
        # bytes in movement state instead of a hero-specific command rewrite.
        self.assertEqual(self.rein.mover.throttles, (0, 127))
        self.assertEqual(self.rein.mover.velocity, (0, 0, 0))
        for _ in range(5):
            self.run.frame += 1
            self.match.update(self.run.frame * .016, self.run.frame)
            self.rein.take_commands([Command(self.run.frame, forward=127)])
        self.assertEqual(tuple(self.rein.mover.position), stopped)
        self.advance(70)
        self.run.frame += 1
        self.match.update(self.run.frame * .016, self.run.frame)
        self.rein.take_commands([Command(self.run.frame, forward=127)])
        self.assertGreater(self.rein.mover.position[2], stopped[2])

    def test_actual_flashbang_interrupts_windup_without_wave_or_refund(self):
        self.match.switch_hero(self.other, heroes()[0x02E0000000000042])
        self.place(self.other, (3, 1, 0))
        self.fill()
        self.advance(1, 32)
        self.advance(5)
        flashbang_tests.FlashbangTests.cast(self, self.run, self.other, self.rein)
        self.advance(130)
        self.assertEqual(self.health(self.enemy), 200)
        self.assertFalse(self.match.combat.shatters)
        self.assertLess(self.rein.body_script.component.entity_var(342).value(), 100)

    def test_emitted_ground_front_continues_after_actual_flashbang_interrupts_caster(self):
        self.match.switch_hero(self.other, heroes()[0x02E0000000000042])
        self.place(self.other, (3, 1, 0))
        self.fill()
        self.advance(1, 32)
        self.advance(28)
        self.assertIn(self.rein.body, self.match.combat.shatters)
        flashbang_tests.FlashbangTests.cast(self, self.run, self.other, self.rein)
        self.assertTrue(self.match.combat.is_stunned(self.rein))
        self.advance(70)
        self.assertEqual(self.health(self.enemy), 150)
        self.assertFalse(self.match.combat.shatters)

    def test_emitted_front_continues_after_a_real_rifle_hit_kills_caster(self):
        fight = self.match.combat
        fight._damage(self.other, fight._target_entity(self.rein.body), 485, False, None, self.run.frame)
        self.assertEqual(self.health(self.rein), 20)
        self.place(self.other, (3, 1, 0))
        self.fill()
        self.advance(1, 32)
        self.advance(28)
        yaw, pitch = aim(fight._eye(self.other), (0, 1.5, 0))
        self.run.step({self.other: (yaw, pitch, 1)})
        self.assertTrue(fight.is_dead(self.rein))
        self.advance(70)
        self.assertEqual(self.health(self.enemy), 150)
        self.assertFalse(fight.shatters)


class CompletionPolicyTests(unittest.TestCase):
    def test_cancelled_dependency_callback_does_not_disable_a_reactivated_switch(self):
        definition = make_graph(0xF001, [node(0, "STUStatescriptStateBooleanSwitch", state=0, fields={
            "m_condition": {"$": "STUConfigVarDynamic", "m_identifier": 77, "m_60DB8F99": 0},
        })], states=[0])
        component = runtime.Component(0xA0000101)
        instance = component.create(definition)
        switch = instance.state(0)
        switch.begin()
        instance.write((0, 77), True)
        self.assertTrue(switch.flags_changed)
        switch.end(False)
        self.assertFalse(switch.flags_changed)
        switch.begin()
        self.assertTrue(switch.current)
        instance.write((0, 77), False)
        component.run_now()
        self.assertFalse(switch.current)

    def test_unrelated_strict_graphs_and_explicit_configs_remain_unsupported(self):
        for hero, index, config in ((0, 0xE2, {}), (combat.REINHARDT, 0xE3, {}),
                                    (combat.REINHARDT, 0xE2, {"m_ED97A97C": {
                                        "$": "STUConfigVarFloat", "m_value": 0.1}})):
            with self.subTest(hero=hero, graph=index, config=config):
                definition = make_graph(index, [node(0, "STU_54FEB21C", state=5, fields=config)],
                                        states=[None] * 5 + [0])
                component = runtime.Component(0xA0000101, world=BodyWorld(hero, 0, 0xA0000101), strict=True)
                instance = component.create(definition)
                with self.assertRaises(runtime.UnsupportedScriptError):
                    instance.state(5).begin()




class NPCRecordingClient(FakeClient):
    """Capture real terminal script baselines; this fixture does not simulate transport."""

    def __init__(self, player):
        super().__init__(player)
        self.deaths = []
        self.death_updates = []

    def queue_bot_death(self, entity, snapshot):
        self.deaths.append((entity, snapshot))

    def queue_bot_death_update(self, entity, snapshot):
        self.death_updates.append((entity, snapshot))

    def bot_stream(self, entity):
        # Combat fixtures declare no observer stream; projectile transport is
        # tested separately. Native projectile simulation still runs normally.
        return None


@unittest.skipUnless((NPC_DATA / "pve_npcs_174.json").is_file() and NPC_MOVERS.is_file(),
                     "external authorized NPC catalog and authored mover inputs required")
class TalonInteractionTests(unittest.TestCase):
    health = HammerTests.health
    place = staticmethod(HammerTests.place)
    advance = HammerTests.advance

    @classmethod
    def setUpClass(cls):
        cls.catalog = npc.load_catalog(NPC_DATA / "pve_npcs_174.json")
        cls.bundle = patch.object(graph, "GRAPHS_PATH", NPC_DATA / "game_graphs_174.json.gz")
        cls.bundle.start()
        cls.mover_bundle = patch.object(mover, "MOVERS_PATH", NPC_MOVERS)
        cls.mover_bundle.start()
        cls.clear_graphs()

    @staticmethod
    def clear_graphs():
        graph.graph.cache_clear()
        graph._records.cache_clear()
        graph._data.cache_clear()
        mover._movers.cache_clear()

    @classmethod
    def tearDownClass(cls):
        cls.bundle.stop()
        cls.mover_bundle.stop()
        cls.clear_graphs()

    def test_five_npc_use_authored_mover_inputs_instead_of_generic_fallback(self):
        expected = {'Trooper': (1.7, .5, 5.75), 'Enforcer': (1.9, .5, 5),
                    'Sniper': (1.75, .5, 4.5), 'Assassin': (1.5, .5, 14),
                    'Heavy Assault': (2.3, .7, 2.2)}
        self.assertEqual(mover.MOVERS_PATH, NPC_MOVERS)
        for definition in self.catalog.values():
            values = mover.mover_data(definition.body)
            self.assertEqual((values.stand_height, values.stand_radius, values.run_forward),
                             expected[definition.name])
            self.assertIn(definition.body, mover.loaded_mover_table())

    def scenario(self, definition, position=(0.0, 1.0, 3.0)):
        HammerTests.setUp(self)
        for player in self.run.players:
            player.client = NPCRecordingClient(player)
        self.place(self.enemy, (10.0, 1.0, 0.0))
        self.place(self.other, (-10.0, 1.0, 0.0))
        bot = npc.spawn(self.match, definition, position, team=1, yaw=-32768)
        self.assertIsNotNone(bot.body_script)
        self.assertEqual(bot.body, definition.body)
        target = next(t for t in self.match.combat._targets() if t.owner is bot)
        return bot, target, self.match.combat.vitals_of(target)

    def assert_reinhardt_respawns_with_fresh_barrier(self, old_body):
        fight = self.match.combat
        [deadline] = [time for time, player in fight.respawns if player is self.rein]
        while fight.now + .032 < deadline:
            self.advance(1, buttons=2)
            self.assertEqual(self.rein.body, old_body)
            self.assertFalse(fight.reinhardt_barriers[old_body].active)
        self.advance(16, buttons=2)
        self.assertNotEqual(self.rein.body, old_body)
        self.assertNotIn(old_body, fight.barriers)
        self.assertNotIn(old_body, fight.vitals)
        self.assertFalse(controlled(fight, old_body))
        self.assertFalse(self.rein.body_script.component.owner_dead)
        self.assertEqual(fight.vitals_of(fight._target_entity(self.rein.body)).total, 500)
        barrier = fight.reinhardt_barriers[self.rein.body]
        self.assertEqual(barrier.current, 1600)
        self.advance(1, buttons=0)
        self.advance(15, buttons=2)
        self.assertTrue(barrier.active)

    def test_hammer_deals_85_to_each_actual_npc_body_and_preserves_pool_order(self):
        for key, definition in self.catalog.items():
            with self.subTest(key=key, name=definition.name):
                bot, _, vitals = self.scenario(definition)
                before = vitals.total
                self.advance(45)
                self.assertEqual(vitals.total, before - 85)
                instances = self.rein.body_script.component.instances.values()
                instance = next(i for i in instances if i.graph.index == 0x48)
                selector = expr.Entity(bot.entity)
                current = instance.evaluate({"$": health_queries.CURRENT, "m_entity": selector})
                maximum = instance.evaluate({"$": health_queries.MAXIMUM, "m_entity": selector})
                self.assertEqual((current, maximum), (before - 85, before))
                for pool_kind in (0, 1, 2):
                    cfg = {"$": health_queries.FILTERED_CURRENT, "m_entity": selector,
                           "m_7E8B666C": {"$": "STU_297E1A30", "m_7E8B666C": pool_kind},
                           "m_7BE02F89": {"$": "STUConfigVarIdentifier", "m_identifier": "0x0B80000000000001"}}
                    self.assertEqual(instance.evaluate(cfg), vitals.now[pool_kind])
                if definition.shields:
                    self.assertEqual(vitals.now[2], definition.shields - 85)
                    self.assertEqual(vitals.now[0], definition.health)

    def test_hammer_impulse_moves_four_actual_npcs_but_heavy_resists(self):
        for definition in self.catalog.values():
            with self.subTest(npc=definition.name):
                bot, _, vitals = self.scenario(definition)
                fight = self.match.combat
                before = vitals.total
                for _ in range(45):
                    self.advance(1)
                    if vitals.total < before:
                        break
                self.assertEqual(vitals.total, before - 85)
                heavy = definition.name == "Heavy Assault"
                self.assertEqual(bot.entity in fight.knockbacks, not heavy)
                self.advance(65, buttons=0)
                self.assertNotIn(bot.entity, fight.knockbacks)
                if heavy:
                    self.assertEqual(bot.position, (0, 1, 3))
                else:
                    self.assertGreater(bot.position[0], 0)
                self.assertEqual(vitals.total, before - 85)

    def test_melee_alias_cover_late_contact_and_repress_on_each_actual_npc(self):
        for definition in self.catalog.values():
            with self.subTest(npc=definition.name):
                _, _, vitals = self.scenario(definition)
                before = vitals.total
                charge_wall(self, 1)
                self.advance(35, buttons=0x800)
                self.assertEqual(vitals.total, before)
                world = flat_world(1)
                self.match.collision_world = world
                self.match.collision_provider.state = LoadState("ready", world)
                for player in self.run.players:
                    player.mover.use(world=world)
                self.advance(13, buttons=0x800)
                self.assertEqual(vitals.total, before - 85)
                self.advance(30, buttons=0)
                self.assertEqual(vitals.total, before - 85)
                self.advance(45, buttons=0x800)
                self.assertEqual(vitals.total, max(0, before - 170))

    def test_primary_and_melee_alias_filter_npc_range_and_team(self):
        for definition in self.catalog.values():
            for button in (1, 0x800):
                with self.subTest(npc=definition.name, button=button):
                    bot, _, vitals = self.scenario(definition, (0, 1, 9))
                    self.match.game_map = replace(self.match.game_map, free_for_all=False)
                    self.rein.team = 1 - bot.team
                    before = vitals.total
                    self.advance(45, buttons=button)
                    self.assertEqual(vitals.total, before)
                    self.advance(30, buttons=0)

                    self.place(self.rein, (0, 1, 6))
                    self.rein.team = bot.team
                    self.advance(45, buttons=button)
                    self.assertEqual(vitals.total, before)
                    self.advance(30, buttons=0)

                    self.rein.team = 1 - bot.team
                    self.advance(45, buttons=button)
                    self.assertEqual(vitals.total, before - 85)

    def test_shatter_far_damage_and_control_on_each_actual_npc(self):
        for key, definition in self.catalog.items():
            with self.subTest(key=key):
                bot, _, vitals = self.scenario(definition, (0, 1, 10))
                before = vitals.total
                self.rein.body_script.set_ult_charge(self.rein.body_script.component.entity_var(338).value())
                self.advance(1, buttons=32)
                self.advance(90, buttons=0)
                self.assertEqual(vitals.total, before - 50)
                self.assertTrue(controlled(self.match.combat, bot.entity))
                self.advance(200, buttons=0)
                self.assertEqual(vitals.total, before - 50)
                self.assertFalse(controlled(self.match.combat, bot.entity))

    def test_shatter_near_damage_uses_original_250_plus_wave_50_on_each_actual_npc(self):
        for key, definition in self.catalog.items():
            with self.subTest(key=key):
                bot, _, vitals = self.scenario(definition)
                before = vitals.total
                self.rein.body_script.set_ult_charge(self.rein.body_script.component.entity_var(338).value())
                self.advance(1, buttons=32)
                self.advance(90, buttons=0)
                self.assertEqual(vitals.total, max(0, before - 300))
                self.assertEqual(bot.dead, before <= 300)

    def test_charge_transports_capturable_npcs_and_stops_on_heavy_6601(self):
        for key, definition in self.catalog.items():
            with self.subTest(key=key, name=definition.name):
                bot, _, vitals = self.scenario(definition)
                before = vitals.total
                charge_wall(self)
                meter = self.rein.body_script.component.entity_var(342)
                starting_charge = meter.value()
                with patch.object(runtime, 'send_instance_message', wraps=runtime.send_instance_message) as sent:
                    self.advance(1, buttons=8)
                    self.advance(55, buttons=0)
                self.assertFalse(any(call.args[1] == 0x0240000000000012 for call in sent.call_args_list))
                self.assertIn(meter.value() - starting_charge, (0, 5))
                if definition.name == "Heavy Assault":
                    self.assertFalse(controlled(self.match.combat, bot.entity))
                    self.assertEqual(bot.position[2], 3)
                    self.assertEqual(vitals.total, before)
                    self.assertFalse(self.match.combat.charges)
                    continue
                self.assertTrue(controlled(self.match.combat, bot.entity))
                self.assertGreater(bot.position[2], 5)
                self.assertEqual(vitals.total, before)
                starting_charge = meter.value()
                with patch.object(runtime, 'send_instance_message', wraps=runtime.send_instance_message) as sent:
                    self.advance(60, buttons=0)
                expected = min(300, before)
                self.assertEqual(vitals.total, before - expected)
                events = [call.args[2] for call in sent.call_args_list if call.args[1] == 0x0240000000000012]
                self.assertEqual([(event[0x13], event[0x15]) for event in events],
                                 [(expected, expr.Entity(bot.entity))])
                self.assertIn(meter.value() - starting_charge, (expected, expected + 5))
                self.assertFalse(controlled(self.match.combat, bot.entity))
                self.assertLess(bot.position[2], 8)
                self.assertFalse(self.match.combat.charges)

    def test_live_charge_releases_each_capturable_npc_on_team_change(self):
        for definition in self.catalog.values():
            if definition.name == "Heavy Assault":
                continue
            with self.subTest(npc=definition.name):
                bot, _, vitals = self.scenario(definition)
                self.match.game_map = replace(self.match.game_map, free_for_all=False)
                self.rein.team, bot.team = 0, 1
                charge_wall(self, 12)
                self.advance(1, buttons=8)
                self.advance(60, buttons=0)
                fight = self.match.combat
                self.assertTrue(pinned(fight, bot.entity))
                position = bot.position
                health = vitals.total
                bot.team = self.rein.team
                self.advance(1, buttons=0)
                self.assertFalse(pinned(fight, bot.entity))
                self.assertNotIn(self.rein.body, fight.charges)
                self.advance(80, buttons=0)
                self.assertEqual(bot.position, position)
                self.assertEqual(vitals.total, health)
                self.assertFalse(self.rein.client.messages(combat.HIT))

    def test_charge_filters_cover_range_and_team_for_each_actual_npc(self):
        for definition in self.catalog.values():
            for case in ("cover", "range", "ally"):
                with self.subTest(npc=definition.name, case=case):
                    self.charge_miss(definition, case)

    def charge_miss(self, definition, case):
        start = (0, 1, 53 if case == "range" else 5)
        bot, _, vitals = self.scenario(definition, start)
        before = vitals.total
        if case == "cover":
            charge_wall(self, 2)
        if case == "ally":
            self.match.game_map = self.match.game_map.__class__(
                **{**self.match.game_map.__dict__, "free_for_all": False})
            bot.team = self.rein.team
        fight = self.match.combat
        self.advance(1, buttons=8)
        self.advance(39, buttons=0)
        self.assertIn(self.rein.body, fight.charges)
        for _ in range(220):
            self.advance(1, buttons=0)
            self.assertFalse(controlled(fight, bot.entity))
            contact = fight.charges.get(self.rein.body)
            if contact is not None:
                self.assertNotIn(bot.entity, {entity for entity, _ in contact.hit})
        if case == "cover":
            self.assertLess(self.rein.mover.position[2], 2)
        else:
            self.assertAlmostEqual(self.rein.mover.position[2], 49.5, delta=.3)
        self.assertEqual(tuple(bot.position), start)
        self.assertEqual(vitals.total, before)
        self.assertNotIn(bot.entity, fight.knockbacks)
        self.assertNotIn(self.rein.body, fight.charges)
        self.assertNotIn(self.rein.body, fight.charges)
        charge = next(i for i in self.rein.body_script.component.instances.values() if i.graph.index == 0x7D)
        self.assertGreater(charge.find_var(0, 61).value(), 0)

    def test_charge_side_bump_or_heavy_6601_impact_preserves_recipient_response(self):
        for key, definition in self.catalog.items():
            with self.subTest(key=key):
                bot, _, vitals = self.scenario(definition, (1.6, 1, 3))
                before = vitals.total
                heavy = definition.hero == 0x02E00000000001BA
                expected = before if heavy else before - 50
                self.advance(1, buttons=8)
                self.advance(65, buttons=0)
                self.assertEqual(vitals.total, expected)
                host = bot.body_script.component.world
                if heavy:
                    # Actual Heavy graph1254/st0 sets response modifier26 to0.
                    immune = next(i for i in bot.body_script.component.instances.values()
                                  if i.graph.index == 0x1254)
                    self.assertTrue(immune.state(0).active)
                    rows = immune.state(0).node.field("m_BC5E91CF")
                    self.assertEqual(rows[0]["m_A9561CA0"], 26)
                    self.assertEqual(rows[0]["m_9B7A63EA"]["m_value"], 0)
                    self.assertEqual(bot.position, (1.6, 1, 3))
                    self.assertNotIn(bot.entity, self.match.combat.knockbacks.active)
                    self.assertEqual(host.movement_flags(bot.entity), bot.movement_flags)
                else:
                    self.assertGreater(bot.position[0], 1.6)
                    self.assertGreater(bot.position[1], 1)
                    self.assertIn(bot.entity, self.match.combat.knockbacks.active)
                    mover = self.match.combat.knockbacks.active[bot.entity]
                    self.assertTrue(mover.airborne)
                    self.assertEqual(host.movement_flags(bot.entity), mover.flags)
                self.advance(250, buttons=0)
                self.assertNotIn(bot.entity, self.match.combat.knockbacks.active)
                self.assertEqual(host.movement_flags(bot.entity), bot.movement_flags)
                self.assertAlmostEqual(bot.position[1], 1, places=2)
                self.assertEqual(vitals.total, expected)

    def test_manual_npc_removal_retires_pin_control_and_impulse_state_immediately(self):
        for pinned in (False, True):
            with self.subTest(pinned=pinned):
                bot, target, _ = self.scenario(self.catalog["1"])
                if pinned:
                    self.advance(1, buttons=8)
                    self.advance(60, buttons=0)
                    self.assertTrue(pinned(self.match.combat, bot.entity))
                else:
                    self.match.combat.knockback(target, (5, 2, 0), self.run.frame)
                    self.assertIn(bot.entity, self.match.combat.knockbacks.active)
                self.assertIn(bot.entity, npc.remove_spawned(self.match))
                self.assertFalse(controlled(self.match.combat, bot.entity))
                self.assertNotIn(bot.entity, self.match.combat.knockbacks.active)
                self.assertFalse(any(c.pinned == bot.entity
                                     for c in self.match.combat.charges.values()))

    def test_ranged_attacks_filter_cover_range_and_team_for_each_actual_npc(self):
        for definition in self.catalog.values():
            for attack, button, reach in (("Fire Strike", 16, 103), ("Earthshatter", 32, 21.5)):
                for case in ("cover", "range", "ally"):
                    with self.subTest(npc=definition.name, attack=attack, case=case):
                        self.ranged_miss(definition, button, reach, case)

    def ranged_miss(self, definition, button, reach, case):
        bot, _, vitals = self.scenario(definition, (0, 1, reach if case == "range" else 3))
        before = vitals.total
        if case == "cover":
            charge_wall(self, 2)
        if case == "ally":
            self.match.game_map = self.match.game_map.__class__(
                **{**self.match.game_map.__dict__, "free_for_all": False})
            bot.team = self.rein.team
        if button == 32:
            script = self.rein.body_script
            script.set_ult_charge(script.component.entity_var(338).value())
            self.advance(1, buttons=32)
            self.advance(28, buttons=0)
            wave = self.match.combat.shatters[self.rein.body]
            self.assertLess(script.component.entity_var(342).value(), 100)
            self.advance(65, buttons=0)
            self.assertNotIn(self.rein.body, self.match.combat.shatters)
            self.assertEqual(wave.travelled, 20)
            self.assertEqual(50 in wave.blocked, case == "cover")
            self.assertFalse(controlled(self.match.combat, bot.entity))
        else:
            self.advance(1, buttons=16)
            self.advance(32, buttons=0)
            [shot] = self.match.combat.rockets
            self.advance(260 if case == "range" else 16, buttons=0)
            self.assertFalse(shot.penetrated)
            if case != "ally":
                self.assertNotIn(shot, self.match.combat.rockets)
            if case == "range":
                self.assertAlmostEqual(shot.flown * shot.speed, 100, places=4)
            elif case == "cover":
                self.assertLess(shot.position[2], 3)
            else:
                self.assertGreater(shot.position[2], 3)
        self.assertEqual(vitals.total, before)

    def test_fire_strike_deals_100_to_each_actual_npc_body(self):
        for key, definition in self.catalog.items():
            with self.subTest(key=key, name=definition.name):
                _, _, vitals = self.scenario(definition)
                before = vitals.total
                self.advance(1, buttons=16)
                self.advance(90, buttons=0)
                self.assertEqual(vitals.total, before - 100)

    def test_hammer_static_occlusion_and_visible_hit_for_each_actual_npc(self):
        for key, definition in self.catalog.items():
            for blocked in (False, True):
                with self.subTest(key=key, blocked=blocked):
                    _, _, vitals = self.scenario(definition)
                    before = vitals.total
                    if blocked:
                        charge_wall(self, 1)
                    self.advance(45)
                    self.assertEqual(vitals.total, before if blocked else before - 85)
                    self.assertEqual(bool(self.rein.client.messages(combat.HIT)), not blocked)

    def test_hammer_terminal_hit_keeps_each_actual_npc_corpse_without_respawn(self):
        for key, definition in self.catalog.items():
            with self.subTest(key=key, name=definition.name):
                bot, target, vitals = self.scenario(definition)
                self.match.combat._damage(self.rein, target, vitals.total - 85, False, None, self.run.frame)
                self.advance(45)
                self.assertTrue(bot.dead)
                self.assertEqual(vitals.total, 0)
                self.assertIsNotNone(bot.body_script)
                self.assertTrue(bot.body_script.component.owner_dead)
                self.assertTrue(any(entity == bot.entity for entity, _ in self.rein.client.deaths))
                self.assertNotIn(bot.entity, self.match.combat.knockbacks.active)
                position = tuple(bot.position)
                self.advance(210, buttons=0)
                self.assertIn(bot, self.match.bots)
                self.assertEqual(tuple(bot.position), position)
                self.assertEqual(bot.movement(self.run.frame).flags, bots.DEAD)
                self.assertFalse(any(who is bot for _, who in self.match.combat.respawns))

    @unittest.skipUnless(BARRIER_DATA.is_file(), "prepared barrier collision required")
    def test_fire_strike_damages_barrier_and_each_actual_npc_behind_it(self):
        with patch.dict(os.environ, {"ORPHEUS_REINHARDT_COLLISION": str(BARRIER_DATA)}):
            for key, definition in self.catalog.items():
                with self.subTest(key=key, name=definition.name):
                    _, _, vitals = self.scenario(definition, (0.0, 1.0, 6.0))
                    self.match.switch_hero(self.enemy, heroes()[combat.REINHARDT])
                    self.place(self.enemy, (0.0, 1.0, 3.0))
                    for _ in range(35):
                        self.run.step({self.enemy: (-32768, 0, 2)})
                    shield = self.match.combat.reinhardt_barriers[self.enemy.body]
                    self.assertTrue(shield.active)
                    before = vitals.total
                    self.run.step({self.rein: (0, 0, 16), self.enemy: (-32768, 0, 2)})
                    for _ in range(90):
                        self.run.step({self.enemy: (-32768, 0, 2)})
                    self.assertEqual(vitals.total, before - 100)
                    self.assertEqual(shield.current, 1500)


@unittest.skipUnless(BARRIER_DATA.is_file(), "prepared external barrier collision required")
class BarrierTests(unittest.TestCase):
    health = HammerTests.health
    place = staticmethod(HammerTests.place)
    advance = HammerTests.advance

    def setUp(self):
        self.enterContext(patch.dict(os.environ, {"ORPHEUS_REINHARDT_COLLISION": str(BARRIER_DATA)}))
        HammerTests.setUp(self)
        self.match.game_map = self.match.game_map.__class__(
            **{**self.match.game_map.__dict__, "free_for_all": False}
        )
        self.rein.team = self.other.team = 0
        self.enemy.team = 1
        self.place(self.enemy, (0.0, 1.0, 6.0))
        self.place(self.other, (10.0, 1.0, 0.0))
        self.advance(15, buttons=2)

    def shield(self, player=None):
        return self.match.combat.reinhardt_barriers[(player or self.rein).body]

    def test_original_shield_actor_is_created_followed_and_destroyed_for_every_viewer(self):
        from ow174.game import observers, reinhardt_presentation, world

        fight = self.match.combat
        actor = fight.barriers.authored_for(self.rein.body)
        self.assertIsNotNone(actor)
        for viewer in self.run.players:
            creates = [u for u in viewer.client.entities
                       if u.entity == actor.entity and u.op == world.OP_CREATE]
            self.assertEqual(len(creates), 1)
            self.assertGreater(creates[0].build(0).count, 0)
            self.assertIn(actor.entity, viewer.seen)
        projection = reinhardt_presentation.projection(fight, actor.entity)
        self.assertIs(projection.component, actor.component)
        self.assertEqual(actor.component.world.body_definition, reinhardt_presentation.DEFINITION)
        self.assertGreater(observers.full_frame(projection, projection.latest_live).count, 0)
        # Exercise the production create consumer, using the same authoritative VM.
        from types import SimpleNamespace
        from ow174.game.server import Client

        link = SimpleNamespace(player=self.enemy, _bot_scripts={}, _combat_notices={},
                               _project_bot_snapshot=lambda snapshot, **kwargs: snapshot)
        Client._track_body_create(link, actor.entity)
        self.assertIs(link._bot_scripts[actor.entity].source.component, actor.component)
        self.assertTrue(link._bot_scripts[actor.entity].stream.pending)
        before = tuple(self.rein.mover.position)
        self.place(self.rein, (before[0] + 3, before[1], before[2]))
        self.advance(1, buttons=2)
        updates = [u for u in self.enemy.client.entities
                   if u.entity == actor.entity and u.movement is not None]
        self.assertTrue(updates)
        expected = reinhardt_presentation.reinhardt_barrier.frame(
            fight.barriers.reinhardt.geometry, *fight.barriers.reinhardt.actors[self.rein.body].pose(),
        )[0]
        self.assertEqual(updates[-1].movement.position, expected)
        self.advance(20, buttons=0)
        self.assertNotIn(actor.entity, fight.barriers.scripted)
        for viewer in self.run.players:
            self.assertTrue(any(u.entity == actor.entity and u.op == world.OP_DESTROY
                                for u in viewer.client.entities))

    def test_shield_bash_ends_at_barrier_before_later_drop(self):
        for early_drop in (False, True):
            with self.subTest(early_drop=early_drop):
                self.setUp()
                self.assertTrue(self.match.switch_hero(self.enemy, heroes()[0x02E0000000000195]))
                self.place(self.enemy, (0, 1, 4))
                fight = self.match.combat
                hold = {self.rein: (0, 0, 2), self.enemy: (-32768, 0, 2)}
                for _ in range(35):
                    self.run.step(hold)
                shield = self.shield()
                self.assertTrue(shield.active)
                self.run.step({**hold, self.enemy: (-32768, 0, 3)})
                dash = brigitte_shield(fight, self.enemy.body).instance.states[14]
                self.assertTrue(dash.active)
                if early_drop:
                    hold[self.rein] = (0, 0, 0)
                for _ in range(8):
                    self.run.step(hold)
                    if not dash.active:
                        break
                self.assertFalse(dash.active)
                if early_drop:
                    self.assertEqual(self.health(self.rein), 497.5)
                    self.assertTrue(controlled(fight, self.rein.body))
                else:
                    self.assertTrue(shield.active)
                    self.assertGreater(self.enemy.mover.position[2], 1.9)
                    self.assertLess(self.enemy.mover.position[2], 3)
                    self.assertEqual(self.enemy.mover.velocity[2], 0)
                    stopped = tuple(self.enemy.mover.position)
                    hold[self.rein] = (0, 0, 0)
                    for _ in range(14):
                        self.run.step(hold)
                    self.assertFalse(shield.active)
                    self.assertEqual(tuple(self.enemy.mover.position), stopped)
                    self.assertEqual(self.health(self.rein), 500)
                    self.assertFalse(controlled(fight, self.rein.body))
                self.assertEqual(shield.current, shield.maximum)

    def test_hard_control_lowers_barrier_and_held_input_recovers_after_expiry(self):
        fight = self.match.combat
        fight.control(fight._target_entity(self.rein.body), self.enemy.body, "stun", .5)
        self.advance(20, buttons=2)
        self.assertFalse(self.shield().active)
        self.advance(25, buttons=2)
        self.assertTrue(self.shield().active)

    def test_actual_rear_flashbang_lowers_barrier_until_held_input_recovers(self):
        self.assertTrue(self.match.switch_hero(self.other, heroes()[0x02E0000000000042]))
        self.other.team = self.enemy.team
        self.place(self.other, (0, 1, -3))
        fight = self.match.combat
        shield = self.shield()
        self.advance(20, buttons=2)
        self.assertTrue(shield.active)
        yaw, pitch = aim(fight._eye(self.other), (0, 2, 0))
        self.run.step({self.rein: (0, 0, 2), self.other: (yaw, pitch, 16)})
        for _ in range(45):
            self.advance(1, buttons=2)
            if fight.is_stunned(self.rein):
                break
        self.assertTrue(fight.is_stunned(self.rein))
        self.assertFalse(shield.active)
        self.assertEqual(shield.current, 1600)
        health = self.health(self.rein)
        self.fire()
        self.assertEqual(self.health(self.rein), health - 15)
        self.assertEqual(shield.current, 1600)
        self.advance(80, buttons=2)
        self.assertFalse(fight.is_stunned(self.rein))
        self.assertTrue(shield.active)
        health = self.health(self.rein)
        self.fire()
        self.assertEqual(shield.current, 1580)
        self.assertEqual(self.health(self.rein), health)

    def test_charge_passes_barrier_pins_owner_and_stops_at_wall(self):
        self.advance(15, buttons=0)
        self.match.switch_hero(self.enemy, heroes()[combat.REINHARDT])
        self.place(self.enemy, (0, 1, 3))
        charge_wall(self)
        for _ in range(30):
            self.run.step({self.enemy: (-32768, 0, 2)})
        self.assertTrue(self.shield(self.enemy).active)
        self.run.step({self.rein: (0, 0, 8), self.enemy: (-32768, 0, 2)})
        for _ in range(100):
            self.run.step({self.enemy: (-32768, 0, 2)})
        self.assertEqual(self.health(self.enemy), 205)
        self.assertEqual(self.shield(self.enemy).current, 1600)
        self.assertFalse(self.match.combat.charges)

    def fire(self, target=(0.0, 2.0, 0.0), shooter=None, *, yaw=0, pitch=0, raised=2, buttons=1):
        shooter = shooter or self.enemy
        y, p = aim(self.match.combat._eye(shooter), target)
        self.run.step({self.rein: (yaw, pitch, raised), shooter: (y, p, buttons)})

    def test_front_hit_reduces_shield_and_updates_native_health_variable_without_hero_damage(self):
        self.assertEqual(self.shield().current, 1600)
        self.fire()
        self.assertEqual(self.health(self.rein), 500)
        self.assertEqual(self.shield().current, 1580)
        instance = reinhardt_barrier_instance(self.rein)
        self.assertEqual(instance.find_var(0, 586).value(), 1580)
        self.assertEqual(self.enemy.client.messages(combat.HIT), [])

    def test_rear_shot_hits_owner_before_shield(self):
        self.place(self.enemy, (0.0, 1.0, -5.0))
        self.fire()
        self.assertEqual(self.health(self.rein), 485)
        self.assertEqual(self.shield().current, 1600)

    def test_shield_protects_ally_behind_it(self):
        self.place(self.enemy, (2.0, 1.0, 6.0))
        self.place(self.other, (2.0, 1.0, -2.0))
        self.fire((2.0, 2.0, -2.0))
        self.assertEqual(self.health(self.other), 200)
        self.assertEqual(self.shield().current, 1580)

    def test_side_miss_does_not_protect_outside_mesh(self):
        self.place(self.enemy, (5.0, 1.0, 6.0))
        self.place(self.other, (5.0, 1.0, -2.0))
        self.fire((5.0, 2.0, -2.0))
        self.assertEqual(self.health(self.other), 180)
        self.assertEqual(self.shield().current, 1600)

    def test_allied_shots_pass_through_their_barrier(self):
        self.place(self.other, (0.0, 1.0, -3.0))
        self.fire((0.0, 2.0, 6.0), shooter=self.other)
        self.assertEqual(self.health(self.enemy), 180)
        self.assertEqual(self.shield().current, 1600)

    def test_shield_is_damageable_without_a_body_along_the_shot(self):
        self.fire((2.0, 2.0, 0.0))
        self.assertEqual(self.health(self.rein), 500)
        self.assertEqual(self.shield().current, 1580)

    def test_static_wall_prevents_hammer_and_quick_melee_damage_to_shield_and_body(self):
        for hammer in (False, True):
            with self.subTest(hammer=hammer):
                if hammer:
                    self.match.switch_hero(self.enemy, heroes()[combat.REINHARDT])
                self.place(self.enemy, (0, 1, 3))
                charge_wall(self, 2)
                for _ in range(50):
                    self.run.step({self.rein: (0, 0, 2),
                                   self.enemy: (-32768, 0, 1 if hammer else 0x800)})
                self.assertEqual(self.shield().current, 1600)
                self.assertEqual(self.health(self.rein), 500)

    def test_solid_wall_in_front_of_shield_occludes_the_shot(self):
        wall = World([
            (-50, 1, -50, -50, 1, 50, 50, 1, 50),
            (-50, 1, -50, 50, 1, 50, 50, 1, -50),
            (-5, 1, 3, -5, 6, 3, 5, 6, 3),
            (-5, 1, 3, 5, 6, 3, 5, 1, 3),
        ])
        self.match.collision_world = wall
        self.match.collision_provider.state = LoadState("ready", wall)
        self.fire()
        self.assertEqual(self.health(self.rein), 500)
        self.assertEqual(self.shield().current, 1600)

    def test_aim_rotation_turns_the_actual_mesh(self):
        self.place(self.enemy, (6.0, 1.0, 0.0))
        self.place(self.other, (-10.0, 1.0, 0.0))
        self.advance(2, buttons=2, yaw=16384)
        self.fire(yaw=16384)
        self.assertEqual(self.health(self.rein), 500)
        self.assertEqual(self.shield().current, 1580)

    def test_regeneration_waits_for_drop_and_authored_delay(self):
        self.fire()
        self.advance(140, buttons=2)
        self.assertEqual(self.shield().current, 1580)
        self.advance(1, buttons=0)
        self.assertFalse(self.shield().active)
        self.advance(124, buttons=0)
        self.assertEqual(self.shield().current, 1580)
        self.advance(10, buttons=0)
        self.assertEqual(self.shield().current, 1600)

    def test_unseeded_barrier_breaks_to_rifle_then_held_input_recovers(self):
        fight = self.match.combat
        geometry = fight.reinhardt_geometry
        shield = self.shield()
        self.assertEqual(shield.current, 1600)
        for _ in range(2000):
            self.fire()
            if shield.current == 0:
                break
        self.assertEqual(shield.current, 0)
        self.assertFalse(shield.active)
        self.assertEqual(self.health(self.rein), 500)
        self.assertAlmostEqual(shield.regen_after - fight.now, geometry.regen_delay)
        self.assertAlmostEqual(shield.broken_until - fight.now, geometry.break_delay)
        while fight.now + combat.TICK < shield.regen_after:
            self.advance(1, buttons=2)
            self.assertEqual(shield.current, 0)
            self.assertFalse(shield.active)
        self.advance(2, buttons=2)
        self.assertAlmostEqual(shield.current, (fight.now - shield.regen_after) * geometry.regen_rate,
                               places=3)
        while fight.now + combat.TICK < shield.broken_until:
            self.advance(1, buttons=2)
            self.assertFalse(shield.active)
        self.advance(15, buttons=2)
        self.assertTrue(shield.active)
        before = shield.current
        self.fire()
        self.assertAlmostEqual(shield.current, before - 20, places=3)
        self.assertEqual(self.health(self.rein), 500)

    def test_terminal_shield_hit_breaks_and_cannot_be_recast_during_cooldown(self):
        set_reinhardt_shield_health(self.match.combat, self.rein.body, 20)  # pre-damaged resource fixture; terminal hit is an actual rifle input
        self.fire()
        shield = self.shield()
        self.assertEqual(shield.current, 0)
        self.assertFalse(shield.active)
        self.assertEqual(self.health(self.rein), 500)
        broken = shield.broken_until
        self.advance(160, buttons=2)
        self.assertFalse(shield.active)
        self.assertGreater(shield.current, 0)
        self.advance(1, buttons=0)
        self.advance(15, buttons=2)
        self.assertFalse(shield.active)
        while self.match.combat.now <= broken + 0.05:
            self.advance(1, buttons=0)
        self.advance(15, buttons=2)
        self.assertTrue(shield.active)
        self.assertGreater(shield.current, 0)
        before = shield.current
        self.fire()
        self.assertAlmostEqual(shield.current, before - 20, places=3)
        self.assertEqual(self.health(self.rein), 500)

    def test_fire_strike_breaks_held_barrier_then_recovered_barrier_blocks_rifle(self):
        self.assertTrue(self.match.switch_hero(self.other, heroes()[combat.REINHARDT]))
        self.other.team = self.enemy.team
        self.place(self.other, (0, 1, 6))
        self.place(self.enemy, (8, 1, 6))
        self.advance(20, buttons=2)
        shield = self.shield()
        self.assertTrue(shield.active)
        set_reinhardt_shield_health(self.match.combat, self.rein.body, 40)

        self.run.step({self.rein: (0, 0, 2), self.other: (-32768, 0, 16)})
        for _ in range(75):
            self.run.step({self.rein: (0, 0, 2), self.other: (-32768, 0, 0)})
        self.assertFalse(shield.active)
        self.assertEqual(shield.current, 0)
        self.assertEqual(self.health(self.rein), 405)
        self.place(self.other, (8, 1, 6))
        self.place(self.enemy, (0, 1, 6))
        self.advance(2, buttons=2)
        self.fire()
        self.assertEqual(self.health(self.rein), 390)
        self.assertFalse(shield.active)

        fight = self.match.combat
        while fight.now + .016 < shield.broken_until:
            self.advance(1, buttons=2)
            self.assertFalse(shield.active)
        self.assertGreater(shield.current, 20)
        self.advance(15, buttons=2)
        self.assertTrue(shield.active)
        self.assertEqual(self.health(self.rein), 390)
        before = shield.current
        self.fire()
        self.assertAlmostEqual(shield.current, before - 20, places=3)
        self.assertTrue(shield.active)
        self.assertEqual(self.health(self.rein), 390)

    def test_retirement_removes_the_old_barrier_resource(self):
        old = self.rein.body
        self.match.switch_hero(self.rein, self.rein.hero)
        self.assertNotIn(old, self.match.combat.barriers.reinhardt.actors)

    def test_helix_direct_and_splash_damage_shield_once_without_harming_owner(self):
        self.fire(buttons=2)
        self.advance(25, buttons=2)
        self.assertEqual(self.health(self.rein), 500)
        self.assertEqual(self.shield().current, 1480)
        self.assertFalse(self.match.combat.rockets)

    def test_projectile_break_does_not_spill_same_explosion_through_the_shield(self):
        set_reinhardt_shield_health(self.match.combat, self.rein.body, 1)
        self.fire(buttons=2)
        self.advance(25, buttons=2)
        self.assertEqual(self.health(self.rein), 500)
        self.assertEqual(self.shield().current, 0)
        self.assertFalse(self.shield().active)

    def test_explosion_can_hit_exposed_ally_in_front_of_shield(self):
        self.place(self.other, (1.6, 1.0, 2.0))
        self.fire(buttons=2)
        self.advance(25, buttons=2)
        self.assertEqual(self.health(self.rein), 500)
        self.assertLess(self.health(self.other), 200)
        self.assertEqual(self.shield().current, 1480)

    def test_nearby_floor_explosion_is_occluded_by_the_shield(self):
        self.fire((0.0, 1.0, 2.8), buttons=2)
        self.advance(30, buttons=2)
        self.assertEqual(self.health(self.rein), 500)
        self.assertLess(self.shield().current, 1600)

    def test_disconnected_owner_has_no_live_shield(self):
        self.rein.client = None
        self.run.players.remove(self.rein)
        self.place(self.enemy, (2.0, 1.0, 6.0))
        self.place(self.other, (2.0, 1.0, -2.0))
        self.fire((2.0, 2.0, -2.0))
        self.assertFalse(self.shield().active)
        self.assertEqual(self.health(self.other), 180)
        self.assertFalse(reinhardt_barrier_instance(self.rein).states[17].active)

    def test_owner_leave_removes_live_barrier_before_next_rifle_hit(self):
        self.place(self.other, (0, 1, -2))
        self.fire((0, 2, -2))
        self.assertEqual(self.health(self.other), 200)
        self.assertEqual(self.shield().current, 1580)
        fight = self.match.combat
        old = self.rein.body
        tick = fight.tick
        self.match.leave(self.rein)
        self.run.players.remove(self.rein)
        self.assertEqual(fight.tick, tick)
        self.assertNotIn(old, fight.barriers.reinhardt.actors)
        for _ in range(15):
            self.run.step({})
        self.fire((0, 2, -2))
        self.assertEqual(self.health(self.other), 180)
        self.assertNotIn(old, fight.barriers.reinhardt.actors)

    def test_owner_death_retires_the_live_shield_even_while_input_is_held(self):
        fight = self.match.combat
        self.assertTrue(self.shield().active)
        self.place(self.enemy, (0, 1, -6))
        target = fight.vitals_of(fight._target_entity(self.rein.body))
        target.damage(target.total - 1, armour_scalar=1.0)
        self.fire()
        self.assertTrue(target.dead)
        self.assertEqual(target.killer, self.enemy.body)
        self.assertFalse(self.shield().active)
        self.advance(12, buttons=2)
        self.place(self.enemy, (2.0, 1.0, 6.0))
        self.place(self.other, (2.0, 1.0, -2.0))
        self.fire((2.0, 2.0, -2.0))
        self.assertFalse(self.shield().active)
        self.assertEqual(self.health(self.other), 180)
        self.assertFalse(reinhardt_barrier_instance(self.rein).states[17].active)

    def test_quick_melee_damages_shield_and_owner_instead_of_being_blocked(self):
        self.place(self.enemy, (0.0, 1.0, 2.0))
        y, p = aim(self.match.combat._eye(self.enemy), (0.0, 2.0, 0.0))
        self.run.step({self.rein: (0, 0, 2), self.enemy: (y, p, 0x800)})
        self.advance(40, buttons=2)
        self.assertEqual(self.health(self.rein), 475)
        self.assertEqual(self.shield().current, 1570)

    def test_fire_strike_damages_multiple_barriers_and_bodies_once_each(self):
        for player, position in ((self.enemy, (0.0, 1.0, 3.0)), (self.other, (0.0, 1.0, 6.0))):
            self.match.switch_hero(player, heroes()[combat.REINHARDT])
            player.team = 1
            self.place(player, position)
        for _ in range(35):
            self.run.step({self.enemy: (-32768, 0, 2), self.other: (-32768, 0, 2)})
        self.run.step({self.rein: (0, 0, 16), self.enemy: (-32768, 0, 2), self.other: (-32768, 0, 2)})
        for _ in range(90):
            self.run.step({self.enemy: (-32768, 0, 2), self.other: (-32768, 0, 2)})
        for player in (self.enemy, self.other):
            self.assertEqual(self.health(player), 405)
            self.assertEqual(self.shield(player).current, 1500)

    def test_fire_strike_lethal_body_contact_precedes_its_barrier_in_the_same_step(self):
        self.assertTrue(self.match.switch_hero(self.enemy, heroes()[combat.REINHARDT]))
        self.place(self.enemy, (1, 1, 12))
        self.other.team = self.enemy.team
        self.place(self.other, (0, 1, 16))
        hold = {self.enemy: (-12288, 0, 2)}
        for _ in range(35):
            self.run.step(hold)
        fight = self.match.combat
        target = fight._target_entity(self.enemy.body)
        vitals = fight.vitals_of(target)
        vitals.now[combat.HEALTH_POOL] = 50
        vitals.now[combat.ARMOUR_POOL] = 0
        shield = self.shield(self.enemy)
        self.assertTrue(shield.active)

        self.run.step({**hold, self.rein: (0, 0, 16)})
        for _ in range(75):
            self.run.step(hold)
            if fight.rockets:
                break
        [shot] = fight.rockets
        body_distance = combat.ray_capsule(
            shot.position, shot.direction, *target.segment(), target.radius + shot.penetration_radius,
        )
        shield_distance, owner, _ = fight._barrier_contact(
            self.rein, shot.position, shot.direction, 20, shot.penetration_radius, team=shot.team,
        )
        step = shot.speed * combat.TICK
        self.assertIs(owner, self.enemy)
        self.assertLess(body_distance, shield_distance)
        self.assertEqual(body_distance // step, shield_distance // step)

        for _ in range(60):
            self.run.step(hold)
        self.assertTrue(vitals.dead)
        self.assertFalse(shield.active)
        self.assertEqual(shield.current, 1600)
        self.assertEqual(self.health(self.other), 100)
        self.assertEqual({body for body, _ in shot.penetrated}, {self.enemy.body, self.other.body})

    def fire_strike_first_barrier_hit(self):
        hero = heroes()[combat.REINHARDT]
        self.assertTrue(self.match.switch_hero(self.enemy, hero))
        self.place(self.enemy, (0, 1, 3))
        hold = {self.enemy: (-32768, 0, 2)}
        fight = self.match.combat
        for _ in range(35):
            self.run.step(hold)
        old_barrier = self.shield(self.enemy)
        self.assertTrue(old_barrier.active)
        self.run.step({**hold, self.rein: (0, 0, 16)})
        for _ in range(75):
            self.run.step(hold)
            if self.health(self.enemy) == 405:
                break
        [shot] = fight.rockets
        self.assertEqual(self.health(self.enemy), 405)
        self.assertEqual(old_barrier.current, 1500)
        return hold, shot, old_barrier

    def test_fire_strike_hits_new_barrier_when_a_body_id_is_reused(self):
        hold, shot, old_barrier = self.fire_strike_first_barrier_hit()
        fight = self.match.combat
        hero = self.enemy.hero
        old_body = self.enemy.body
        old_activation = reinhardt_barrier_instance(self.enemy).states[17].counter

        self.assertTrue(self.match.switch_hero(self.enemy, heroes()[SOLDIER]))
        self.assertNotEqual(self.enemy.body, old_body)
        with patch.object(self.enemy.client, 'body_id_available', side_effect=lambda entity: entity == old_body):
            self.assertTrue(self.match.switch_hero(self.enemy, hero))
        self.assertEqual(self.enemy.body, old_body)
        self.place(self.enemy, (0, 1, 22))
        for _ in range(25):
            self.run.step(hold)
        new_barrier = self.shield(self.enemy)
        self.assertIsNot(new_barrier, old_barrier)
        self.assertTrue(new_barrier.active)
        self.assertEqual(reinhardt_barrier_instance(self.enemy).states[17].counter, old_activation)
        self.assertEqual(new_barrier.current, 1600)
        self.assertIn(shot, fight.rockets)
        for _ in range(65):
            self.run.step(hold)
        self.assertEqual(self.health(self.enemy), 405)
        self.assertEqual(old_barrier.current, 1500)
        self.assertEqual(new_barrier.current, 1500)

    def test_fire_strike_hits_a_later_raise_without_hitting_the_same_body_again(self):
        hold, shot, shield = self.fire_strike_first_barrier_hit()
        fight = self.match.combat
        pet = reinhardt_barrier_instance(self.enemy).states[17]
        old_activation = pet.counter
        self.run.step({self.enemy: (-32768, 0, 0)})
        self.assertFalse(pet.active)
        self.place(self.enemy, (0, 1, 22))
        for _ in range(25):
            self.run.step(hold)
        self.assertIs(self.shield(self.enemy), shield)
        self.assertTrue(shield.active)
        self.assertNotEqual(pet.counter, old_activation)
        self.assertEqual(shield.current, 1500)
        self.assertIn(shot, fight.rockets)
        for _ in range(65):
            self.run.step(hold)
        self.assertEqual(shield.current, 1400)
        self.assertEqual(self.health(self.enemy), 405)

    def test_ranged_attacks_use_barrier_owners_team_at_contact(self):
        for button in (16, 32):
            for starts_hostile in (False, True):
                with self.subTest(button=button, starts_hostile=starts_hostile):
                    self.setUp()
                    self.assertTrue(self.match.switch_hero(self.enemy, heroes()[combat.REINHARDT]))
                    self.enemy.team, self.other.team = int(starts_hostile), 1
                    self.place(self.enemy, (0, 1, 7))
                    self.place(self.other, (0, 1, 12))
                    hold = {self.enemy: (-32768, 0, 2)}
                    for _ in range(35):
                        self.run.step(hold)
                    shield = self.shield(self.enemy)
                    self.assertTrue(shield.active)
                    if button == 32:
                        self.rein.body_script.set_ult_charge(1540)
                    self.run.step({**hold, self.rein: (0, 0, button)})
                    fight = self.match.combat
                    for _ in range(60):
                        released = fight.rockets if button == 16 else fight.shatters
                        if released:
                            break
                        self.run.step(hold)
                    self.assertTrue(released)
                    self.assertEqual(shield.current, 1600)
                    self.enemy.team = int(not starts_hostile)
                    for _ in range(65):
                        self.run.step(hold)
                    self.assertTrue(shield.active)
                    shield_damage = 0 if starts_hostile else (100 if button == 16 else 50)
                    self.assertEqual(shield.current, 1600 - shield_damage)
                    self.assertEqual(self.health(self.enemy), 405 if shield_damage == 100 else 500)
                    rear_damage = 100 if button == 16 else (50 if starts_hostile else 0)
                    self.assertEqual(self.health(self.other), 200 - rear_damage)
                    self.assertEqual(controlled(fight, self.other.body), button == 32 and starts_hostile)

    def test_melee_can_damage_barrier_without_reaching_owner(self):
        self.place(self.enemy, (0.0, 1.0, 3.5))
        y, p = aim(self.match.combat._eye(self.enemy), (0.0, 2.0, 0.0))
        self.run.step({self.rein: (0, 0, 2), self.enemy: (y, p, 0x800)})
        self.advance(40, buttons=2)
        self.assertEqual(self.health(self.rein), 500)
        self.assertEqual(self.shield().current, 1570)

    def test_active_hammer_hits_replacement_barrier_with_reused_body_id(self):
        for button in (1, 0x800):
            with self.subTest(button=button):
                self.setUp()
                hero = heroes()[combat.REINHARDT]
                self.assertTrue(self.match.switch_hero(self.enemy, hero))
                self.place(self.enemy, (0, 1, 3))
                hold = {self.enemy: (-32768, 0, 2)}
                for _ in range(35):
                    self.run.step(hold)
                old = self.enemy.body
                old_shield = self.shield(self.enemy)
                self.assertTrue(old_shield.active)

                fight = self.match.combat
                self.run.step({**hold, self.rein: (0, 0, button)})
                for _ in range(45):
                    self.run.step(hold)
                    if old_shield.current == 1515:
                        break
                self.assertEqual(old_shield.current, 1515)
                self.assertEqual(self.health(self.enemy), 420)
                records = fight.hammer_hits[self.rein.body]
                [(attack, (counter, _))] = records.items()

                self.assertTrue(self.match.switch_hero(self.enemy, heroes()[SOLDIER]))
                self.assertNotEqual(self.enemy.body, old)
                with patch.object(self.enemy.client, "body_id_available", side_effect={old}.__contains__):
                    self.assertTrue(self.match.switch_hero(self.enemy, hero))
                self.assertEqual(self.enemy.body, old)
                for _ in range(25):
                    # Keep the shield in reach despite the replacement body's hammer knockback.
                    self.place(self.enemy, (0, 1, 3))
                    self.run.step(hold)
                    new_shield = self.shield(self.enemy)
                    if new_shield.current < 1600:
                        break
                self.assertIsNot(new_shield, old_shield)
                self.assertTrue(new_shield.active)
                self.assertEqual(records[attack][0], counter)
                self.assertEqual(self.health(self.enemy), 420)
                self.assertEqual(new_shield.current, 1515)
                for _ in range(25):
                    self.run.step(hold)
                self.assertEqual(new_shield.current, 1515)
                self.assertEqual(old_shield.current, 1515)

    def test_hammer_and_fire_strike_damage_and_penetrate_enemy_barrier(self):
        self.match.switch_hero(self.enemy, heroes()[combat.REINHARDT])
        self.place(self.enemy, (0.0, 1.0, 3.0))
        for _ in range(35):
            self.run.step({self.enemy: (-32768, 0, 2)})
        shield = self.shield(self.enemy)
        self.assertTrue(shield.active)
        for _ in range(45):
            self.run.step({self.rein: (0, 0, 1), self.enemy: (-32768, 0, 2)})
        self.assertEqual(self.health(self.enemy), 420)
        self.assertEqual(shield.current, 1515)
        self.assertGreater(self.enemy.mover.position[0], 0)
        self.run.step({self.rein: (0, 0, 16), self.enemy: (-32768, 0, 2)})
        for _ in range(90):
            self.run.step({self.enemy: (-32768, 0, 2)})
        self.assertEqual(self.health(self.enemy), 325)
        self.assertEqual(shield.current, 1415)


if __name__ == "__main__":
    unittest.main()
