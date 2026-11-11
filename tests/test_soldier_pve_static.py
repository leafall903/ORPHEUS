"""Executed Soldier interactions with prepared real PvE bodies and mover capsules.

Set ORPHEUS_PVE_DATA_DIR and ORPHEUS_PVE_MOVERS_FILE to external prepared data.
The body VM remains permissive and collision/transport are explicit fixtures.
These cases do not claim enemy AI, difficulty scaling or client acceptance.
"""

import logging
import math
import os
import unittest
from contextlib import nullcontext
from pathlib import Path
from unittest.mock import patch

import test_soldier_combat_lane as soldier_fixture
import test_soldier_skill_interrupts as movement_fixture
from test_game_combat import FakeClient, Run, aim, in_world

from ow174.game import (
    combat,
    mover,
    npc,
    npc_console,
    npc_control,
    projectiles,
    stats,
    ultimate,
    zarya_barrier,
)
from ow174.game.collision import World, flat_world
from ow174.game.commands import JUMP, Command
from ow174.game.content import heroes
from ow174.game.script import expr, graph
from ow174.game.script.decode import ClientBody
from ow174.game.script.driver import OWNER_HEALTH, BodyStream
from ow174.game.script.nodes import Ability, TrackTargets


class NpcRecordingClient(FakeClient):
    def __init__(self, player):
        super().__init__(player)
        self.bot_deaths = []
        self.bot_death_updates = []

    def queue_bot_death(self, entity, event):
        self.bot_deaths.append((entity, event))

    def queue_bot_death_update(self, entity, event):
        self.bot_death_updates.append((entity, event))

    def bot_stream(self, entity):
        return None  # no NPC script ACK/observer projectile presentation is modeled here


class SoldierPveStaticTests(unittest.TestCase):
    input = soldier_fixture.SoldierCombatLaneTests.input
    vitals = soldier_fixture.SoldierCombatLaneTests.vitals
    start_visor = soldier_fixture.SoldierCombatLaneTests.start_visor
    move = movement_fixture.SoldierSkillInterruptTests.move
    sprint = movement_fixture.SoldierSkillInterruptTests.sprint

    @classmethod
    def setUpClass(cls):
        data_dir = os.environ.get("ORPHEUS_PVE_DATA_DIR")
        movers_file = os.environ.get("ORPHEUS_PVE_MOVERS_FILE")
        if not data_dir or not movers_file:
            raise unittest.SkipTest("explicit prepared five-NPC script/catalog/mover inputs are required")
        cls.data_dir = Path(data_dir)
        cls.catalog = npc.load_catalog(cls.data_dir / "pve_npcs_174.json")
        if {row.name for row in cls.catalog.values()} != {
            "Trooper", "Enforcer", "Sniper", "Assassin", "Heavy Assault",
        }:
            raise ValueError("the complete five-class NPC catalog is required")
        cls.edits = [patch.object(graph, "GRAPHS_PATH", cls.data_dir / "game_graphs_174.json.gz"),
                     patch.object(mover, "MOVERS_PATH", Path(movers_file))]
        for edit in cls.edits:
            edit.start()
        cls.reset_catalogs()

    @staticmethod
    def reset_catalogs():
        graph._data.cache_clear()
        graph._records.cache_clear()
        graph.graph.cache_clear()
        mover._movers.cache_clear()

    @classmethod
    def tearDownClass(cls):
        for edit in reversed(cls.edits):
            edit.stop()
        cls.reset_catalogs()

    def arena(self, key, position=(0.0, 0.0, 1.0), team=1):
        logging.getLogger("ow174.script").setLevel(logging.ERROR)
        run = Run()
        run.match.bots.clear()
        run.match._pending_bot_scripts.clear()
        run.match.collision_world = flat_world(0.0)
        soldier, = run.players
        soldier.client = NpcRecordingClient(soldier)
        soldier.mover.position = (0.0, 0.0, -3.0)
        definition = self.catalog[key]
        enemy = npc.spawn(run.match, definition, position, team=team)
        self.assertIsNotNone(enemy.body_script)
        self.assertIn(enemy.body, mover._movers())  # forbid silent Soldier-capsule fallback
        self.assertEqual(enemy.hero, definition.hero)
        self.assertEqual(enemy.body, definition.body)
        self.assertEqual(enemy.body_script.component.vars[OWNER_HEALTH].value(),
                         definition.health + definition.armour + definition.shields)
        return run, soldier, enemy

    def test_melee_from_sprint_hits_all_five_pve_bodies(self):
        for key, definition in self.catalog.items():
            with self.subTest(npc=definition.name):
                run, soldier, actor = self.arena(key, (0, 0, 3))
                soldier.mover.position = [0, 0, -3]
                soldier.mover.body = None
                self.sprint(run, soldier)
                pool = self.vitals(run, actor)
                initial = pool.total
                point = (0, 0.8, 3)
                for _ in range(30):
                    speed = self.move(run, soldier, buttons=0x800, target=point)
                    if pool.total < initial:
                        break
                self.assertEqual(pool.total, initial - 30)
                self.assertAlmostEqual(speed, 8.25, delta=0.01)
                for _ in range(5):
                    speed = self.move(run, soldier)
                self.assertAlmostEqual(speed, 5.5, delta=0.01)
                self.assertEqual(pool.total, initial - 30)

    def test_melee_rejects_early_repress_and_hits_after_recovery_on_each_pve_body(self):
        for key, definition in self.catalog.items():
            with self.subTest(npc=definition.name):
                run, soldier, actor = self.arena(key, (0, 0, -1.5))
                pool = self.vitals(run, actor)
                initial = pool.total
                ammo = soldier.body_script.component.instances[11].vars[53]
                rounds = ammo.value()
                self.input(run, soldier, 0x800)
                # Heavy's active movement modifier 26 rejects the impulse.
                expected_force = 0 if definition.name == "Heavy Assault" else 1
                self.assertEqual(actor.velocity, (0, 0, expected_force))
                self.assertEqual(actor.impulse_mover is not None, bool(expected_force))
                self.input(run, soldier)
                self.input(run, soldier, 0x800)
                for _ in range(70):
                    self.input(run, soldier)
                self.assertEqual(pool.total, initial - 30)
                self.assertEqual(len(soldier.client.messages(combat.HIT)), 1)
                self.input(run, soldier, 0x800)
                for _ in range(40):
                    self.input(run, soldier)
                self.assertEqual(pool.total, initial - 60)
                self.assertEqual(len(soldier.client.messages(combat.HIT)), 2)
                self.assertEqual(ammo.value(), rounds)

    def test_melee_displacement_changes_the_next_swing_on_each_pve_body(self):
        for key, definition in self.catalog.items():
            with self.subTest(npc=definition.name):
                radius = mover.mover_data(definition.body).stand_radius
                start = (0, .75, -.5 + radius - .025)
                run, soldier, actor = self.arena(key, start)
                soldier.mover.position = list(soldier.mover.position)
                soldier.mover.body = None
                pool = self.vitals(run, actor)
                initial = pool.total
                resists = definition.name == "Heavy Assault"
                for _ in range(10):
                    run.step({})
                run.step({soldier: (0, 0, 0x800)})
                self.assertEqual(pool.total, initial - 30)
                melee = next(i for i in soldier.body_script.component.instances.values() if i.graph.index == 0x0043)
                for _ in range(70):
                    run.step({})
                if resists:
                    self.assertEqual(actor.position, start)
                else:
                    self.assertGreater(actor.position[2], start[2] + .1)
                    self.assertIsNone(actor.impulse_mover)
                run.step({soldier: (0, 0, 0x800)})
                self.assertEqual(melee.states[4].counter, 2)
                self.assertEqual(pool.total, initial - (60 if resists else 30))
                if not resists:
                    for _ in range(12):
                        run.frame += 1
                        run.match.update(run.frame * combat.TICK, run.frame)
                        soldier.take_commands([Command(run.frame, forward=127)])
                    for _ in range(60):
                        run.step({})
                    run.step({soldier: (0, 0, 0x800)})
                    self.assertEqual(melee.states[4].counter, 3)
                    self.assertEqual(pool.total, initial - 60)
                self.assertEqual(len(soldier.client.messages(combat.HIT)), 2)

    def test_lethal_melee_leaves_each_pve_corpse_without_post_death_impulse(self):
        for key, definition in self.catalog.items():
            with self.subTest(npc=definition.name):
                run, soldier, actor = self.arena(key, (0, 0, -1.5))
                pool = self.vitals(run, actor)
                angles = aim(run.match.combat._eye(soldier), (0, 0.8, -1.5))
                ammo = soldier.body_script.component.instances[11].vars[53]
                for _ in range(5000):
                    if pool.total <= 30:
                        break
                    self.input(run, soldier, 1 if ammo.value() else 4, *angles)
                self.assertGreater(pool.total, 0)
                self.assertLessEqual(pool.total, 30)
                self.input(run, soldier, 0x800)
                self.assertTrue(pool.dead)
                self.assertTrue(actor.dead)
                self.assertEqual(pool.killer, soldier.body)
                self.assertEqual(actor.velocity, (0, 0, 0))
                self.assertIsNone(actor.impulse_mover)
                self.assertIn(actor, run.match.bots)


    def test_melee_checks_current_range_cover_and_team_on_each_pve_body(self):
        for key, definition in self.catalog.items():
            for gate in ("range", "cover", "team"):
                with self.subTest(npc=definition.name, gate=gate):
                    run, soldier, actor = self.arena(key, (0, 0, -1.5))
                    pool = self.vitals(run, actor)
                    initial = pool.total
                    position, team = actor.position, actor.team
                    world = run.match.collision_world
                    if gate == "range":
                        actor.position = (0, 0, 3)
                    elif gate == "team":
                        actor.team = soldier.team
                    else:
                        run.match.collision_world = World([
                            (-5, -1, -2.8, 5, -1, -2.8, 5, 5, -2.8),
                            (-5, -1, -2.8, 5, 5, -2.8, -5, 5, -2.8),
                        ])
                    for _ in range(8):
                        self.input(run, soldier)
                    self.input(run, soldier, 0x800)
                    self.assertEqual(pool.total, initial)
                    self.assertEqual(actor.velocity, (0, 0, 0))
                    self.assertIsNone(actor.impulse_mover)
                    for _ in range(75):
                        self.input(run, soldier)
                    self.assertEqual(pool.total, initial)
                    self.assertFalse(soldier.client.messages(combat.HIT))
                    actor.position, actor.team = position, team
                    run.match.collision_world = world
                    for _ in range(8):
                        self.input(run, soldier)
                    self.input(run, soldier, 0x800)
                    self.assertEqual(pool.total, initial - 30)
                    self.assertEqual(len(soldier.client.messages(combat.HIT)), 1)


    def test_melee_angular_boundary_and_recovery_on_each_pve_body(self):
        for key, definition in self.catalog.items():
            with self.subTest(npc=definition.name):
                run, soldier, actor = self.arena(key, (0, .75, -1.5))
                soldier.mover.position = list(soldier.mover.position)
                script = soldier.body_script
                script.ult_seconds = 0
                charge = script.component.entity_var(342)
                pool = self.vitals(run, actor)
                initial = pool.total
                body_radius = mover.mover_data(definition.body).stand_radius
                # The target axis crosses eye height and sits 1.5 m forward.
                # The swung centre traces a 1.5 m arc about the eye.
                boundary = round(2 * math.asin((1 + body_radius) / 3) * 32768 / math.pi)
                outside, inside = boundary + 32, boundary - 32
                for _ in range(8):
                    run.step({})
                run.step({soldier: (outside, 0, 0x800)})
                melee = next(i for i in script.component.instances.values() if i.graph.index == 0x0043)
                contact = melee.states[melee.graph.node(10).state]
                self.assertEqual(contact.counter, 1)
                self.assertEqual(pool.total, initial)
                self.assertEqual(charge.value(), 0)
                self.assertEqual(actor.velocity, (0, 0, 0))
                self.assertFalse(soldier.client.messages(combat.HIT))

                run.step({})
                run.step({soldier: (inside, 0, 0x800)})
                self.assertEqual(contact.counter, 1)
                self.assertEqual(pool.total, initial)
                for _ in range(75):
                    run.step({})
                self.assertEqual(contact.counter, 1)
                self.assertEqual(pool.total, initial)
                run.step({soldier: (inside, 0, 0x800)})
                self.assertEqual(contact.counter, 2)
                self.assertEqual(pool.total, initial - 30)
                self.assertEqual(charge.value(), 30)
                self.assertEqual(len(soldier.client.messages(combat.HIT)), 1)

    def test_melee_uses_visible_capsule_surface_when_wall_occludes_axis(self):
        floor = [
            (-10, 0, -10, -10, 0, 10, 10, 0, 10),
            (-10, 0, -10, 10, 0, 10, 10, 0, -10),
        ]
        for key, definition in self.catalog.items():
            with self.subTest(npc=definition.name):
                run, soldier, actor = self.arena(key, (0, .75, -1.5))
                soldier.mover.position = list(soldier.mover.position)
                pool = self.vitals(run, actor)
                initial = pool.total
                surface = actor.position[2] - mover.mover_data(definition.body).stand_radius
                for offset, damage in ((-.02, 0), (.02, 30)):
                    z = surface + offset
                    wall = [(-3, 0, z, 3, 0, z, 3, 4, z), (-3, 0, z, 3, 4, z, -3, 4, z)]
                    run.match.collision_world = World(floor + wall + [
                        (*triangle[:3], *triangle[6:9], *triangle[3:6]) for triangle in wall
                    ])
                    for _ in range(75):
                        run.step({})
                    origin = run.match.combat._eye(soldier)
                    axis_distance = actor.position[2] - origin[2]
                    self.assertAlmostEqual(
                        combat.ray_world(run.match.collision_world, origin, (0, 0, 1), axis_distance),
                        z - origin[2],
                    )
                    run.step({soldier: (0, 0, 0x800)})
                    self.assertEqual(pool.total, initial - damage)
                    self.assertEqual(len(soldier.client.messages(combat.HIT)), int(damage > 0))

    def test_melee_uses_historical_range_on_each_pve_body(self):
        near, far = (0, 0, -1.5), (0, 0, 3)
        for key, definition in self.catalog.items():
            for moved_in in (False, True):
                with self.subTest(npc=definition.name, moved_in=moved_in):
                    start, finish = (far, near) if moved_in else (near, far)
                    run, soldier, actor = self.arena(key, start)
                    pool = self.vitals(run, actor)
                    initial = pool.total
                    for _ in range(8):
                        self.input(run, soldier)
                    actor.position = finish
                    self.input(run, soldier, 0x800)
                    self.assertEqual(pool.total, initial - (0 if moved_in else 30))
                    for _ in range(75):
                        self.input(run, soldier)
                    self.input(run, soldier, 0x800)
                    self.assertEqual(pool.total, initial - 30)
                    self.assertEqual(len(soldier.client.messages(combat.HIT)), 1)


    def test_melee_filters_current_team_with_historical_position_on_each_pve_body(self):
        near, far = (0, 0, -1.5), (0, 0, 3)
        for key, definition in self.catalog.items():
            for current_team in (0, 1):
                with self.subTest(npc=definition.name, current_team=current_team):
                    run, soldier, actor = self.arena(key, near, team=1 - current_team)
                    pool = self.vitals(run, actor)
                    initial = pool.total
                    for _ in range(8):
                        self.input(run, soldier)
                    actor.position, actor.team = far, current_team
                    self.input(run, soldier, 0x800)
                    self.assertEqual(pool.total, initial - 30 * current_team)
                    self.assertEqual(len(soldier.client.messages(combat.HIT)), current_team)
                    actor.position, actor.team = near, 1
                    for _ in range(75):
                        self.input(run, soldier)
                    self.input(run, soldier, 0x800)
                    self.assertEqual(pool.total, initial - 30 * (current_team + 1))
                    self.assertEqual(len(soldier.client.messages(combat.HIT)), current_team + 1)


    def test_melee_cooldown_boundary_buffers_last_frame_on_each_pve_body(self):
        for key, definition in self.catalog.items():
            for frames_early in (1, 0):
                with self.subTest(npc=definition.name, frames_early=frames_early):
                    run, soldier, actor = self.arena(key, (0, 0, -1.5))
                    pool = self.vitals(run, actor)
                    initial = pool.total
                    self.input(run, soldier, 0x800)
                    for _ in range(30):
                        self.input(run, soldier)
                    self.assertEqual(pool.total, initial - 30)
                    component = soldier.body_script.component
                    melee = next(i for i in component.instances.values() if i.graph.index == 0x0043)
                    cooldown, = (state for state in melee.states.values()
                                 if isinstance(state, Ability) and state.active and state.cur == 1)
                    frame_ms = component.frame_ms
                    deadline = cooldown.last + 1000
                    for _ in range(64):
                        if component.now >= deadline - (frames_early + 1) * frame_ms:
                            break
                        self.input(run, soldier)
                    self.input(run, soldier, 0x800)
                    offset = component.now - deadline + frames_early * frame_ms
                    self.assertIn(offset, range(frame_ms))
                    for _ in range(40):
                        self.input(run, soldier)
                    self.assertGreaterEqual(cooldown.last, deadline)
                    self.assertEqual(pool.total, initial - 60)
                    self.assertEqual(len(soldier.client.messages(combat.HIT)), 2)


    def test_visor_from_sprint_hits_all_five_pve_bodies(self):
        for key, definition in self.catalog.items():
            with self.subTest(npc=definition.name):
                run, soldier, actor = self.arena(key, (3, 0, 10))
                soldier.mover.position = [0, 0, -3]
                soldier.mover.body = None
                script = soldier.body_script
                script.ult_seconds = 0
                script.set_ult_charge(script.component.entity_var(338).value())
                self.sprint(run, soldier)
                pool = self.vitals(run, actor)
                initial = pool.total
                for _ in range(15):
                    speed = self.move(run, soldier, buttons=0x20)
                self.assertAlmostEqual(speed, 5.5, delta=0.01)
                self.assertEqual(script.component.entity_var(342).value(), 0)
                self.move(run, soldier, buttons=1, forward=0)
                self.assertEqual(pool.total, initial)
                for _ in range(80):
                    self.move(run, soldier, forward=0)
                self.move(run, soldier, buttons=1, forward=0)
                self.assertEqual(pool.total, initial - 20)
                for _ in range(400):
                    self.move(run, soldier, forward=0)
                ammo = script.component.instances[11].vars[53]
                rounds = ammo.value()
                self.move(run, soldier, buttons=1, forward=0)
                self.assertEqual(ammo.value(), rounds - 1)
                self.assertEqual(pool.total, initial - 20)

    def test_field_from_sprint_heals_all_five_allied_pve_bodies(self):
        for key, definition in self.catalog.items():
            with self.subTest(npc=definition.name):
                run, soldier, actor = self.arena(key, (0, 0, 3), team=0)
                soldier.mover.position = [0, 0, -3]
                soldier.mover.body = None
                self.sprint(run, soldier)
                fight = run.match.combat
                pool = self.vitals(run, actor)
                initial = pool.total
                pool.damage(50)  # Fixture injury isolates Field from enemy fire.
                for _ in range(15):
                    speed = self.move(run, soldier, buttons=0x10)
                self.assertAlmostEqual(speed, 5.5, delta=0.01)
                self.assertEqual(pool.total, initial - 50)
                for _ in range(95):
                    self.move(run, soldier, buttons=0x10, forward=0)
                field, = [shot for shot in fight.projectiles.shots
                          if shot.player is soldier and shot.spawn == projectiles.FIELD_ENTITY]
                self.assertIsNotNone(field.field_tick)
                self.assertGreater(pool.total, initial - 50)
                for _ in range(95):
                    self.move(run, soldier, forward=0)
                self.assertEqual(pool.total, initial)
                self.assertEqual(actor.body_script.component.vars[OWNER_HEALTH].value(), initial)
                self.assertAlmostEqual(soldier.stats.value(stats.HEALING), 50)

    def test_helix_exits_sprint_and_hits_each_actual_pve_body(self):
        for key, definition in self.catalog.items():
            with self.subTest(npc=definition.name):
                run, soldier, actor = self.arena(key, (0, 0, 15))
                soldier.mover.position = [0, 0, -3]
                soldier.mover.body = None
                self.sprint(run, soldier)
                fight = run.match.combat
                pool = self.vitals(run, actor)
                initial = pool.total
                for _ in range(15):
                    speed = self.move(run, soldier, buttons=2, target=(0, 0.8, 15))
                    self.assertFalse(fight.rockets)
                self.assertAlmostEqual(speed, 5.5, delta=0.01)
                self.assertEqual(pool.total, initial)
                for _ in range(45):
                    self.move(run, soldier, buttons=2, forward=0, target=(0, 0.8, 15))
                self.assertEqual(pool.total, initial - 120)
                self.move(run, soldier, forward=0)
                for _ in range(25):
                    self.move(run, soldier, buttons=2, forward=0, target=(0, 0.8, 15))
                    self.assertFalse(fight.rockets)
                self.assertEqual(pool.total, initial - 120)
                for _ in range(400):
                    self.move(run, soldier, forward=0)
                self.move(run, soldier, buttons=2, forward=0, target=(0, 0.8, 15))
                self.assertTrue(fight.rockets)
                for _ in range(45):
                    self.move(run, soldier, forward=0)
                    if pool.total < initial - 120:
                        break
                self.assertEqual(pool.total, max(0, initial - 240))
                if pool.total == 0:
                    self.assertTrue(actor.dead)
                    self.assertEqual(pool.killer, soldier.body)

    def test_in_flight_helix_checks_current_teams(self):
        for key, definition in self.catalog.items():
            for changed, current_team in (("target", 0), ("target", 1), ("shooter", 0), ("shooter", 1)):
                with self.subTest(npc=definition.name, changed=changed, current_team=current_team):
                    run, soldier, actor = self.arena(key, (0, 0, 15), team=1 - current_team)
                    fight = run.match.combat
                    pool = self.vitals(run, actor)
                    initial = pool.total
                    yaw, pitch = aim(fight._eye(soldier), (0, 0.8, 15))
                    self.input(run, soldier, 2, yaw, pitch)
                    rocket, = fight.rockets
                    self.assertEqual(pool.total, initial)
                    self.assertEqual(rocket.team, soldier.team)
                    if changed == "target":
                        actor.team = current_team
                    else:
                        soldier.team = 1 - soldier.team
                        self.assertNotEqual(rocket.team, soldier.team)
                    for _ in range(145):
                        self.input(run, soldier)
                        if rocket not in fight.rockets:
                            break
                    self.assertNotIn(rocket, fight.rockets)
                    self.assertEqual(pool.total, initial - 120 * current_team)
                    self.assertEqual(bool(soldier.client.messages(combat.HIT)), bool(current_team))


    def test_in_flight_helix_checks_current_cover_for_each_pve_body(self):
        for key, definition in self.catalog.items():
            for covered_at_contact in (False, True):
                with self.subTest(npc=definition.name, covered_at_contact=covered_at_contact):
                    run, soldier, actor = self.arena(key, (0, 0, 15))
                    fight = run.match.combat
                    pool = self.vitals(run, actor)
                    initial = pool.total
                    clear = run.match.collision_world
                    cover = World([
                        (-8, -1, 7, 8, -1, 7, 8, 8, 7),
                        (-8, -1, 7, 8, 8, 7, -8, 8, 7),
                    ])
                    if not covered_at_contact:
                        run.match.collision_world = cover
                    yaw, pitch = aim(fight._eye(soldier), (0, 0.8, 15))
                    self.input(run, soldier, 2, yaw, pitch)
                    rocket, = fight.rockets
                    launch = rocket.position
                    for _ in range(5):
                        self.input(run, soldier)
                    self.assertIn(rocket, fight.rockets)
                    self.assertGreater(rocket.position[2], launch[2])
                    self.assertEqual(pool.total, initial)
                    run.match.collision_world = cover if covered_at_contact else clear
                    for _ in range(40):
                        self.input(run, soldier)
                        if rocket not in fight.rockets:
                            break
                    self.assertNotIn(rocket, fight.rockets)
                    self.assertEqual(pool.total, initial - (0 if covered_at_contact else 120))
                    self.assertEqual(bool(soldier.client.messages(combat.HIT)), not covered_at_contact)


    def test_helix_cooldown_boundary_controls_second_impact_on_each_pve_body(self):
        for key, definition in self.catalog.items():
            for frames_early in (1, 0):
                with self.subTest(npc=definition.name, frames_early=frames_early):
                    run, soldier, actor = self.arena(key, (0, 0, 5))
                    fight = run.match.combat
                    pool = self.vitals(run, actor)
                    initial = pool.total
                    yaw, pitch = aim(fight._eye(soldier), (0, 0.8, 5))
                    self.input(run, soldier, 2, yaw, pitch)
                    for _ in range(30):
                        self.input(run, soldier)
                    self.assertEqual(pool.total, initial - 120)
                    component = soldier.body_script.component
                    rifle = component.instances[11]
                    cooldown, = (state for state in rifle.states.values()
                                 if isinstance(state, Ability) and state.active and state.cur == 6)
                    frame_ms = component.frame_ms
                    deadline = cooldown.last + 6000
                    while component.now < deadline - (frames_early + 1) * frame_ms:
                        self.input(run, soldier)
                    self.input(run, soldier, 2, yaw, pitch)
                    offset = component.now - deadline + frames_early * frame_ms
                    self.assertIn(offset, range(frame_ms))
                    self.assertEqual(bool(fight.rockets), frames_early == 0)
                    for _ in range(30):
                        self.input(run, soldier)
                    expected = initial - 120 if frames_early else max(0, initial - 240)
                    self.assertEqual(pool.total, expected)


    def test_helix_tap_during_sprint_out_requires_fresh_press(self):
        for key, definition in self.catalog.items():
            with self.subTest(npc=definition.name):
                run, soldier, actor = self.arena(key, (0, 0, 15))
                soldier.mover.position = [0, 0, -3]
                soldier.mover.body = None
                self.sprint(run, soldier)
                fight = run.match.combat
                pool = self.vitals(run, actor)
                initial = pool.total
                point = (0, 0.8, 15)
                self.move(run, soldier, buttons=2, target=point)
                self.assertFalse(fight.rockets)
                for _ in range(45):
                    speed = self.move(run, soldier, target=point)
                    self.assertFalse(fight.rockets)
                self.assertAlmostEqual(speed, 5.5, delta=0.01)
                self.assertEqual(pool.total, initial)
                self.move(run, soldier, buttons=2, forward=0, target=point)
                self.assertTrue(fight.rockets)
                for _ in range(45):
                    self.move(run, soldier, forward=0, target=point)
                self.assertEqual(pool.total, initial - 120)

    def test_heavy_special_input_reaches_authored_health_states(self):
        for action, graph_id, ability_pos, search_pos, health_pos in (
            (31, 0x140E, 9, 6, 5),
            (35, 0x1408, 82, 96, 35),
        ):
            with self.subTest(action=action):
                run, soldier, heavy = self.arena("5", (0, 0, -1.5))
                pool = self.vitals(run, soldier)
                heavy.yaw = -32768
                if action == 35:
                    soldier.mover.position = (0.8, 0, -3)
                component = heavy.body_script.component
                instance = next(i for i in component.instances.values() if i.graph.index == graph_id)
                ability = instance.states[instance.graph.node(ability_pos).state]
                self.assertTrue(ability.active)
                self.assertEqual(component.world.ai_weapon_button(0), 20)
                search_node = instance.graph.node(search_pos)
                health_node = instance.graph.node(health_pos)
                self.assertNotIn(health_node.state, instance.states)
                component.edges([(action, True)])
                for _ in range(100):
                    self.input(run, soldier)
                self.assertGreater(instance.states[search_node.state].counter, 0)
                self.assertGreater(instance.states[health_node.state].counter, 0)
                if action == 31:
                    health = instance.states[health_node.state]
                    settings = health_node.field("m_modifyHealth")
                    self.assertEqual(health.evaluate(settings["m_amount"]), 80)
                    self.assertEqual(health.evaluate(settings["m_E33EFEE1"]), 20)
                    self.assertEqual(health.evaluate(settings["m_10399AE7"]), 8)
                else:
                    splash_search = instance.states[instance.graph.node(32).state]
                    self.assertEqual(splash_search.evaluate(splash_search.node.field("m_radius")), 2)
                    self.assertEqual(splash_search.targets, (expr.Entity(soldier.body),))
                    health = instance.states[health_node.state]
                    self.assertEqual(health.evaluate(health_node.field("m_FA3ECC43")), splash_search.targets)
                    self.assertEqual(health.evaluate(health_node.field("m_modifyHealth")["m_amount"]), 50)
                    direction = instance.find_var(expr.INSTANCE, 3192).value()
                    self.assertAlmostEqual(direction.x, 0)
                    self.assertEqual((direction.y, direction.z), (0, -1))
                    movement = instance.states[instance.graph.node(71).state]
                    self.assertTrue(movement.active)
                    row = movement.node.field("m_BC5E91CF")[0]
                    self.assertEqual(row["$"], "STU_09F9B1FE")
                    vector = movement.evaluate(row["m_9B7A63EA"], watch=False)
                    self.assertAlmostEqual(vector.x, 0)
                    self.assertEqual((vector.y, vector.z), (0, -15))
                    self.assertEqual(heavy.position, (0, 0, -1.5))
                expected = 120 if action == 31 else 150
                self.assertEqual(pool.total, expected)
                component.edges([(action, False)])
                self.input(run, soldier)
                self.assertNotIn(action, component.held)


    def test_heavy_action35_splash_damages_only_visible_enemies_in_range(self):
        for gate in ("clear", "range", "cover", "team"):
            with self.subTest(gate=gate):
                run, soldier, heavy = self.arena("5", (0, 0, -1.5), team=0 if gate == "team" else 1)
                heavy.yaw = -32768
                soldier.mover.position = [5 if gate == "range" else 0.8, 0, -3]
                soldier.mover.body = None
                if gate == "cover":
                    run.match.collision_world = World([
                        (-20, 0, -20, -20, 0, 20, 20, 0, 20),
                        (-20, 0, -20, 20, 0, 20, 20, 0, -20),
                        (-5, -1, -2.4, 5, -1, -2.4, 5, 6, -2.4),
                        (-5, -1, -2.4, 5, 6, -2.4, -5, 6, -2.4),
                    ])
                component = heavy.body_script.component
                component.edges([(35, True)])
                for _ in range(70):
                    run.step({})
                charge = next(i for i in component.instances.values() if i.graph.index == 0x1408)
                contact = charge.states[charge.graph.node(35).state]
                self.assertEqual(contact.counter, 1)
                self.assertEqual(self.vitals(run, soldier).total, 150 if gate == "clear" else 200)

    def test_heavy_action35_uses_target_flag_for_the_close_contact_branch(self):
        for marked in (False, True):
            with self.subTest(marked=marked):
                run, soldier, heavy = self.arena("5", (0, 0, -2.7))
                heavy.yaw = -32768
                soldier.mover.position = (0.22, 0, -3)
                soldier.body_script.component.set(961, marked)
                component = heavy.body_script.component
                charge = next(i for i in component.instances.values() if i.graph.index == 0x1408)
                component.edges([(35, True)])
                for _ in range(55):
                    self.input(run, soldier)
                contact = charge.states.get(charge.graph.node(102).state)
                self.assertEqual(bool(contact and contact.counter), marked)
                self.assertEqual(self.vitals(run, soldier).total, 25 if marked else 150)

    def test_heavy_action35_captures_soldier_and_wall_impact_ends_control(self):
        for gate in ("impact", "measured", "open", "cover", "range", "team"):
            with self.subTest(gate=gate):
                run, soldier, heavy = self.arena("5", (0, 0, 0), team=0 if gate == "team" else 1)
                soldier.mover.position = [8 if gate == "range" else 0, 0, -3.5]
                soldier.mover.body = None
                heavy.yaw = -32768
                if gate in ("impact", "measured", "cover"):
                    z = -2 if gate == "cover" else -6
                    run.match.collision_world = World([
                        (-20, 0, -20, -20, 0, 20, 20, 0, 20),
                        (-20, 0, -20, 20, 0, 20, 20, 0, -20),
                        (-5, -1, z, 5, -1, z, 5, 6, z),
                        (-5, -1, z, 5, 6, z, -5, 6, z),
                    ])
                fight = run.match.combat
                component = heavy.body_script.component
                charge = next(i for i in component.instances.values() if i.graph.index == 0x1408)
                pool = self.vitals(run, soldier)
                if gate == "measured":
                    # A larger fixture pool exposes the whole impact amount.
                    pool.most[combat.HEALTH_POOL] = pool.now[combat.HEALTH_POOL] = 350
                ammo = soldier.body_script.component.instances[11].vars[53]
                rounds = ammo.value()
                component.edges([(35, True)])
                for _ in range(65):
                    run.step({})
                captured = gate in ("impact", "measured", "open")
                self.assertEqual(fight.is_controlled(soldier), captured)
                self.assertEqual(pool.total, 350 if gate == "measured" else 200)
                if captured:
                    self.assertEqual(charge.find_var(expr.INSTANCE, 536).value(), expr.Entity(soldier.body))
                    self.assertLess(soldier.mover.position[2], -3.5)
                    run.step({soldier: (0, 0, 1)})
                    self.assertEqual(ammo.value(), rounds)
                    run.step({})
                component.edges([(35, False)])
                for _ in range(220):
                    run.step({})
                    if heavy.body_script.charge_velocity() is None and not fight.is_controlled(soldier):
                        break
                self.assertFalse(fight.is_controlled(soldier))
                self.assertNotIn(heavy.entity, fight.pins)
                self.assertIsNone(heavy.body_script.charge_velocity())
                self.assertEqual(pool.total, {"impact": 0, "measured": 50}.get(gate, 200))
                if gate == "impact":
                    self.assertTrue(pool.dead)
                    self.assertEqual(pool.killer, heavy.entity)
                if gate == "open":
                    for _ in range(10):
                        run.step({soldier: (0, 0, 1)})
                    self.assertLess(ammo.value(), rounds)
                if gate == "measured":
                    cooldown = charge.find_var(expr.INSTANCE, 61)
                    self.assertGreater(cooldown.value(), 14)
                    heavy.position = (0, 0, 0)
                    heavy.impulse_mover = None
                    soldier.mover.position = [0, 0, -3.5]
                    soldier.mover.body = None
                    component.edges([(35, True)])
                    run.step({})
                    component.edges([(35, False)])
                    for _ in range(980):
                        run.step({})
                        if cooldown.value() == 0:
                            break
                    self.assertEqual(cooldown.value(), 0)
                    self.assertEqual(heavy.position, (0, 0, 0))
                    self.assertEqual(pool.total, 50)
                    component.edges([(35, True)])
                    for _ in range(90):
                        run.step({})
                        if pool.dead:
                            break
                    self.assertTrue(pool.dead)
                    self.assertFalse(fight.is_controlled(soldier))

    def test_actual_flashbang_and_sleep_dart_interrupt_heavy_capture(self):
        for hero, buttons, intercept_z in (
            (0x02E0000000000042, 0x10, -2.5),
            (0x02E000000000013B, 0x08, -5.5),
        ):
            with self.subTest(hero=hero):
                self.check_heavy_capture_interruption(hero, buttons, intercept_z)

    def heavy_capture_arena(self):
        run, soldier, heavy = self.arena("5", (0, 0, 0))
        soldier.mover.position = [0, 0, -3.5]
        soldier.mover.body = None
        heavy.yaw = -32768
        return run, soldier, heavy

    def begin_heavy_capture(self, run, soldier, heavy):
        fight = run.match.combat
        heavy.body_script.component.edges([(35, True)])
        for _ in range(65):
            run.step({})
            if fight.is_controlled(soldier):
                return
        self.fail("Heavy action 35 did not capture Soldier")

    def test_prelaunch_helix_hits_heavy_during_live_capture(self):
        for lethal, forced in ((False, False), (True, False), (False, True)):
            with self.subTest(lethal=lethal, forced=forced):
                run, soldier, heavy = self.heavy_capture_arena()
                fight = run.match.combat
                own, target = self.vitals(run, soldier), self.vitals(run, heavy)
                if lethal:
                    target.damage(target.total - 110)  # Leave enough health to require the blast.
                initial = target.total
                source = soldier.body
                component = heavy.body_script.component
                component.edges([(35, True)])
                for _ in range(50):
                    run.step({})
                self.assertFalse(fight.is_controlled(soldier))
                angles = aim(fight._eye(soldier), (0, 0.8, 0))
                run.step({soldier: (*angles, 2)})
                rocket, = fight.rockets
                self.assertEqual(rocket.source_entity, source)

                for _ in range(8):
                    run.step({})
                    if fight.is_controlled(soldier):
                        break
                self.assertTrue(fight.is_controlled(soldier))
                self.assertIn(rocket, fight.rockets)
                self.assertEqual(target.total, initial)
                blast_impulse = fight._blast_impulse

                def impulse(recipient, point, blast, scalar):
                    blast_impulse(recipient, point, blast, scalar)
                    if recipient.owner is heavy:
                        # Add an external force after the real rocket hit and
                        # before the capture settles for this world frame.
                        heavy.apply_impulse((0, 0, 20), fight.tick, fight._collision(),
                                            category=combat.MOVER)

                intercept = patch.object(fight, "_blast_impulse", side_effect=impulse) if forced else nullcontext()
                with intercept:
                    for _ in range(8):
                        run.step({})
                        if rocket not in fight.rockets:
                            break
                self.assertNotIn(rocket, fight.rockets)
                self.assertEqual(target.total, max(0, initial - 120))
                self.assertTrue(0 < own.total < 200)  # Close blast reaches the caster.
                if forced:
                    self.assertGreater(heavy.velocity[2], 0)
                self.assertIs(heavy.dead, lethal)
                self.assertIs(fight.is_controlled(soldier), not lethal)
                if lethal:
                    self.assertEqual(target.killer, source)
                    self.assertTrue(component.owner_dead)
                    self.assertNotIn(heavy.entity, fight.pins)
                    ammo = soldier.body_script.component.instances[11].vars[53]
                    rounds = ammo.value()
                    remaining = own.total
                    stopped = heavy.position
                    for _ in range(40):
                        run.step({soldier: (*angles, 1)})
                    self.assertLess(ammo.value(), rounds)
                    self.assertEqual(own.total, remaining)
                    self.assertEqual(heavy.position, stopped)

    def test_heavy_capture_releases_on_body_replacement_or_source_removal(self):
        for remove in (False, True):
            with self.subTest(remove=remove):
                run, soldier, heavy = self.heavy_capture_arena()
                fight = run.match.combat
                self.begin_heavy_capture(run, soldier, heavy)
                body = soldier.body
                self.assertEqual(fight.pins[heavy.entity], body)
                if remove:
                    self.assertIn(heavy.entity, npc.remove_spawned(run.match))
                    self.assertNotIn(heavy, run.match.bots)
                else:
                    self.assertTrue(run.match.switch_hero(soldier, soldier.hero))
                    self.assertNotEqual(soldier.body, body)
                    soldier.mover.position = [0, 0, -3.5]
                    soldier.mover.body = None
                self.assertNotIn(heavy.entity, fight.pins)
                self.assertFalse(fight.is_controlled(soldier))
                ammo = soldier.body_script.component.instances[11].vars[53]
                rounds = ammo.value()
                for _ in range(45):
                    run.step({soldier: (0, 0, 1)})
                    self.assertFalse(fight.is_controlled(soldier))
                self.assertLess(ammo.value(), rounds)
                self.assertEqual(self.vitals(run, soldier).total, 200)

    def test_heavy_capture_rejects_helix_and_fresh_press_after_release_hits(self):
        run, soldier, heavy = self.heavy_capture_arena()
        fight = run.match.combat
        target = self.vitals(run, heavy)
        initial = target.total
        self.begin_heavy_capture(run, soldier, heavy)
        angles = aim(fight._eye(soldier), (0, 0.8, heavy.position[2]))
        run.step({soldier: (*angles, 2)})
        for _ in range(5):
            run.step({})
            self.assertTrue(fight.is_controlled(soldier))
            self.assertFalse(fight.rockets)
        npc_control.stop(run.match)
        for _ in range(10):
            run.step({})
            self.assertFalse(fight.rockets)
        self.assertEqual(target.total, initial)
        self.assertFalse(fight.is_controlled(soldier))
        angles = aim(fight._eye(soldier), (0, 0.8, heavy.position[2]))
        run.step({soldier: (*angles, 2)})
        rocket, = fight.rockets
        for _ in range(20):
            run.step({})
            if rocket not in fight.rockets:
                break
        self.assertNotIn(rocket, fight.rockets)
        self.assertEqual(target.total, initial - 120)

    def test_heavy_capture_rejects_new_reload_but_allows_admitted_refill(self):
        for admitted in (False, True):
            with self.subTest(admitted=admitted):
                run, soldier, heavy = self.heavy_capture_arena()
                fight = run.match.combat
                rifle = soldier.body_script.component.instances[11]
                ammo, reloading = rifle.vars[53], rifle.vars[51]
                target = self.vitals(run, heavy)
                before = target.total
                run.step({soldier: (16384, 0, 1)})
                self.assertEqual(ammo.value(), 29)
                self.assertEqual(target.total, before)
                for _ in range(40):
                    run.step({})
                if admitted:
                    run.step({soldier: (0, 0, 0x400)})
                    self.assertTrue(reloading.value())
                self.begin_heavy_capture(run, soldier, heavy)
                self.assertEqual(fight.pins.get(heavy.entity), soldier.body)
                for _ in range(60 if admitted else 8):
                    x, y, z = heavy.position
                    angles = aim(fight._eye(soldier), (x, y + .8, z))
                    run.step({soldier: (*angles, 0x401 if admitted else 0x400)})
                    self.assertEqual(fight.pins.get(heavy.entity), soldier.body)
                    if not admitted:
                        self.assertFalse(reloading.value())
                        self.assertEqual(ammo.value(), 29)
                    elif ammo.value() == 30:
                        break
                self.assertEqual(ammo.value(), 30 if admitted else 29)
                if admitted:
                    for _ in range(8):
                        x, y, z = heavy.position
                        angles = aim(fight._eye(soldier), (x, y + .8, z))
                        run.step({soldier: (*angles, 1)})
                        self.assertEqual(fight.pins.get(heavy.entity), soldier.body)
                        self.assertEqual(ammo.value(), 30)
                self.assertEqual(target.total, before)
                self.assertGreater(npc_control.stop(run.match), 0)
                for _ in range(100):
                    run.step({})
                self.assertFalse(fight.is_controlled(soldier))
                self.assertFalse(reloading.value())
                self.assertEqual(ammo.value(), 30 if admitted else 29)
                self.assertEqual(target.total, before)
                if not admitted:
                    run.step({soldier: (0, 0, 0x400)})
                    self.assertTrue(reloading.value())
                    for _ in range(100):
                        run.step({})
                    self.assertFalse(reloading.value())
                    self.assertEqual(ammo.value(), 30)
                x, y, z = heavy.position
                angles = aim(fight._eye(soldier), (x, y + .8, z))
                run.step({soldier: (*angles, 1)})
                self.assertEqual(ammo.value(), 29)
                self.assertEqual(target.total, before - 20)

    def heavy_barrier_caster(self, run, soldier):
        ally = run.match.add_player(2, "Zarya", soldier.hero.guid, soldier.team, False)
        in_world(run.match, ally)
        run.players.append(ally)
        self.assertTrue(run.match.switch_hero(ally, heroes()[zarya_barrier.HERO]))
        ally.mover.position = [2.5, 0, -3.5]
        ally.mover.body = None
        return ally

    def test_projected_barrier_rejects_heavy_capture_or_releases_live_victim(self):
        for late in (False, True):
            with self.subTest(late=late):
                run, soldier, heavy = self.heavy_capture_arena()
                heavy.position = (0, 0, -1.5)
                fight = run.match.combat
                run.match.collision_world = World([
                    (-20, 0, -20, -20, 0, 20, 20, 0, 20),
                    (-20, 0, -20, 20, 0, 20, 20, 0, -20),
                    (-5, -1, -6, 5, -1, -6, 5, 6, -6),
                    (-5, -1, -6, 5, 6, -6, -5, 6, -6),
                ])
                ally = self.heavy_barrier_caster(run, soldier)
                run.step({})
                if late:
                    self.begin_heavy_capture(run, soldier, heavy)
                    for _ in range(7):
                        run.step({})
                    self.assertTrue(fight.is_controlled(soldier))
                    self.assertLess(soldier.mover.position[2], -3.5)
                x, y, z = soldier.mover.position
                angles = aim(fight._eye(ally), (x, y + 0.9, z))
                run.step({ally: (*angles, 0x10)})
                projected = fight.zarya_barriers.projected
                self.assertEqual(projected[ally.body][1:], (200, soldier.body))
                if not late:
                    heavy.body_script.component.edges([(35, True)])
                for _ in range(75):
                    run.step({})
                    self.assertFalse(fight.is_controlled(soldier))
                self.assertEqual(self.vitals(run, soldier).total, 200)
                self.assertNotIn(heavy.entity, fight.pins)
                self.assertEqual(projected[ally.body][1], 200 if late else 150)
                energy = expr.to_float(ally.body_script.component.entity_var(87).value())
                self.assertAlmostEqual(energy, 0 if late else 10)
                for _ in range(65):
                    run.step({})
                self.assertNotIn(ally.body, projected)
                self.assertEqual(self.vitals(run, soldier).total, 200)

    def test_heavy_barrier_break_protects_initial_contact_and_next_sweep_can_capture(self):
        run, soldier, heavy = self.heavy_capture_arena()
        heavy.position = (0, 0, -1.5)
        ally = self.heavy_barrier_caster(run, soldier)
        enemy = run.match.add_player(3, "Enemy Soldier", soldier.hero.guid, heavy.team, False)
        in_world(run.match, enemy)
        run.players.append(enemy)
        self.assertTrue(run.match.switch_hero(enemy, soldier.hero))
        enemy.mover.position = [-4, 0, -3.5]
        enemy.mover.body = None
        fight = run.match.combat
        pool = self.vitals(run, soldier)
        run.step({})
        point = (0, 0.9, -3.5)
        run.step({ally: (*aim(fight._eye(ally), point), 0x10)})
        projected = fight.zarya_barriers.projected
        self.assertEqual(projected[ally.body][1:], (200, soldier.body))
        angles = aim(fight._eye(enemy), point)
        run.step({enemy: (*angles, 2)})
        for _ in range(48):
            run.step({enemy: (*angles, 1)})
            if projected[ally.body][1] <= 40:
                break
        self.assertEqual(projected[ally.body][1], 40)
        self.assertEqual(pool.total, 200)

        heavy.body_script.component.edges([(35, True)])
        for _ in range(60):
            run.step({})
            if ally.body not in projected:
                break
        self.assertNotIn(ally.body, projected)
        self.assertAlmostEqual(ally.body_script.component.entity_var(87).value(), 40)
        self.assertEqual(heavy.position, (0, 0, -1.5))
        self.assertEqual(pool.total, 200)
        self.assertFalse(fight.is_controlled(soldier))
        run.step({})
        self.assertLess(heavy.position[2], -1.5)
        self.assertTrue(fight.is_controlled(soldier))
        self.assertEqual(pool.total, 200)

    def test_heavy_capture_carries_wounded_soldier_out_of_active_field(self):
        run, soldier, heavy = self.heavy_capture_arena()
        heavy.position = (0, 0, -1.5)
        fight = run.match.combat
        pool = self.vitals(run, soldier)
        soldier.body_script.ult_seconds = 0
        component = heavy.body_script.component
        component.edges([(31, True)])
        run.step({})
        component.edges([(31, False)])
        for _ in range(40):
            run.step({})
        self.assertEqual(pool.total, 120)
        self.assertEqual(pool.ult_healing_deficit, 80)
        # Settle the wound's knockback, then stage the separate Field/capture contact.
        for _ in range(120):
            run.step({})
        soldier.mover.position = [0, 0, -3.5]
        soldier.mover.body = None
        run.step({soldier: (0, 0, 0x10)})
        for _ in range(80):
            run.step({})
            if any(s.spawn == projectiles.FIELD_ENTITY and s.field_tick is not None
                   for s in fight.projectiles.shots):
                break
        field, = [s for s in fight.projectiles.shots if s.spawn == projectiles.FIELD_ENTITY]
        self.assertIsNotNone(field.field_tick)
        self.begin_heavy_capture(run, soldier, heavy)
        before = pool.total
        self.assertLess(before, 200)
        for _ in range(5):
            run.step({})
        self.assertTrue(fight.is_controlled(soldier))
        self.assertGreater(pool.total, before)
        for _ in range(30):
            run.step({})
        self.assertTrue(fight.is_controlled(soldier))
        self.assertIn(field, fight.projectiles.shots)
        outside = pool.total
        self.assertLess(outside, 200)
        self.assertLess(soldier.mover.position[2], -7)
        for _ in range(10):
            run.step({})
        self.assertEqual(pool.total, outside)
        npc_control.stop(run.match)
        self.assertFalse(fight.is_controlled(soldier))
        self.assertAlmostEqual(soldier.stats.value(stats.HEALING), outside - 120)
        charge = soldier.body_script.component.entity_var(342).value()
        self.assertAlmostEqual(charge, outside - 120, delta=0.0001)
        for _ in range(100):
            self.move(run, soldier, target=(0, 0.8, -3.5))
            if pool.total > outside:
                break
        self.assertGreater(pool.total, outside)
        self.assertIn(field, fight.projectiles.shots)
        for _ in range(140):
            self.move(run, soldier, forward=0)
            if pool.total == 200:
                break
        self.assertEqual(pool.total, 200)
        self.assertEqual(pool.ult_healing_deficit, 0)
        self.assertAlmostEqual(soldier.body_script.component.entity_var(342).value(), 80, delta=0.0001)

    def test_heavy_capture_interrupts_field_and_visor_windup(self):
        for button, graph_id, windup_pos in ((0x10, 0x0257, 6), (0x20, 0x0259, 91)):
            with self.subTest(graph=graph_id):
                run, soldier, heavy = self.heavy_capture_arena()
                fight = run.match.combat
                script = soldier.body_script
                script.ult_seconds = 0
                if button == 0x20:
                    script.set_ult_charge(script.component.entity_var(338).value())
                ability = next(i for i in script.component.instances.values() if i.graph.index == graph_id)
                heavy.body_script.component.edges([(35, True)])
                for _ in range(47):
                    run.step({})
                run.step({soldier: (0, 0, button)})
                windup = ability.states[ability.graph.node(windup_pos).state]
                self.assertTrue(windup.active)
                self.assertFalse(fight.is_controlled(soldier))
                launch = ability.states[ability.graph.node(8).state] if button == 0x10 else None
                if launch is not None:
                    self.assertTrue(launch.active)
                    self.assertFalse(fight.projectiles.shots)
                for _ in range(12):
                    run.step({})
                    if fight.is_controlled(soldier):
                        break
                self.assertTrue(fight.is_controlled(soldier))
                self.assertFalse(windup.active)
                if launch is not None:
                    self.assertFalse(launch.active)
                npc_control.stop(run.match)
                self.assertFalse(fight.is_controlled(soldier))
                for _ in range(100):
                    run.step({})
                    self.assertFalse(any(s.spawn == projectiles.FIELD_ENTITY for s in fight.projectiles.shots))
                if button == 0x20:
                    self.assertEqual(ability.state(6).counter, 0)
                    self.assertEqual(script.component.entity_var(342).value(), 0)
                else:
                    run.step({soldier: (0, 0, button)})
                    for _ in range(80):
                        run.step({})
                        if any(s.field_tick is not None for s in fight.projectiles.shots):
                            break
                    field, = [s for s in fight.projectiles.shots if s.spawn == projectiles.FIELD_ENTITY]
                    self.assertIsNotNone(field.field_tick)

    def test_field_canister_launched_before_heavy_capture_opens_during_pin(self):
        run, soldier, heavy = self.heavy_capture_arena()
        fight = run.match.combat
        heavy.body_script.component.edges([(35, True)])
        for _ in range(25):
            run.step({})
        run.step({soldier: (0, 0, 0x10)})
        for _ in range(40):
            run.step({})
            if fight.projectiles.shots:
                break
        canister, = fight.projectiles.shots
        self.assertEqual(canister.spawn, projectiles.FIELD_ENTITY)
        self.assertIsNone(canister.field_tick)
        self.assertFalse(fight.is_controlled(soldier))
        for _ in range(12):
            run.step({})
            if fight.is_controlled(soldier):
                break
        self.assertTrue(fight.is_controlled(soldier))
        self.assertIn(canister, fight.projectiles.shots)
        self.assertIsNone(canister.field_tick)
        for _ in range(25):
            run.step({})
            if canister.field_tick is not None:
                break
        self.assertIsNotNone(canister.field_tick)
        self.assertTrue(fight.is_controlled(soldier))

    def test_heavy_capture_preserves_active_visor_and_released_fire_uses_remaining_time(self):
        run, soldier, heavy = self.heavy_capture_arena()
        fight = run.match.combat
        script = soldier.body_script
        script.ult_seconds = 0
        script.set_ult_charge(script.component.entity_var(338).value())
        run.step({soldier: (0, 0, 0x20)})
        visor = next(i for i in script.component.instances.values() if i.graph.index == 0x0259)
        for _ in range(80):
            run.step({})
        countdown = visor.states[6]
        self.assertTrue(countdown.active)
        start, counter = countdown.start, countdown.counter
        self.assertEqual(script.component.entity_var(342).value(), 0)
        self.begin_heavy_capture(run, soldier, heavy)
        ammo = script.component.instances[11].vars[53]
        rounds = ammo.value()
        target = self.vitals(run, heavy)
        before = target.total
        for _ in range(3):
            run.step({soldier: (0, 0, 1)})
        self.assertEqual(ammo.value(), rounds)
        self.assertEqual(target.total, before)
        self.assertTrue(countdown.active)
        self.assertEqual((countdown.start, countdown.counter), (start, counter))
        npc_control.stop(run.match)
        self.assertFalse(fight.is_controlled(soldier))
        yaw, pitch = aim(fight._eye(soldier), (0, 0.8, heavy.position[2]))
        run.step({soldier: (yaw + 4000, pitch, 1)})
        self.assertEqual(target.total, before - 20)
        while script.component.now < start + 6000:
            run.step({})
        self.assertFalse(countdown.active)
        remaining = target.total
        rounds = ammo.value()
        run.step({soldier: (yaw + 4000, pitch, 1)})
        self.assertEqual(ammo.value(), rounds - 1)
        self.assertEqual(target.total, remaining)

    def test_heavy_manual_stop_ends_live_capture_without_wall_damage(self):
        run, soldier, heavy = self.heavy_capture_arena()
        fight = run.match.combat
        component = heavy.body_script.component
        self.begin_heavy_capture(run, soldier, heavy)
        self.assertEqual(npc_control.stop(run.match), 1)
        self.assertIsNone(heavy.body_script.charge_velocity())
        self.assertNotIn(35, component.held)
        self.assertFalse(fight.is_controlled(soldier))
        stopped = heavy.position
        for _ in range(60):
            run.step({})
        self.assertEqual(heavy.position, stopped)
        self.assertEqual(self.vitals(run, soldier).total, 200)
        self.assertNotIn(heavy.entity, fight.pins)

    def check_heavy_capture_interruption(self, hero, buttons, intercept_z):
        run, soldier, heavy = self.heavy_capture_arena()
        fight = run.match.combat
        support = run.match.add_player(2, "Support", soldier.hero.guid, soldier.team, False)
        in_world(run.match, support)
        run.players.append(support)
        run.match.switch_hero(support, heroes()[hero])
        support.mover.position = [2, 0, intercept_z]
        support.mover.body = None
        component = heavy.body_script.component
        charge = next(i for i in component.instances.values() if i.graph.index == 0x1408)
        self.begin_heavy_capture(run, soldier, heavy)
        angles = aim(fight._eye(support), (0, 0.8, intercept_z))
        run.step({support: (*angles, buttons)})
        for _ in range(40):
            run.step({support: (*angles, 0)})
            if fight.is_stunned(heavy):
                break
        self.assertTrue(fight.is_stunned(heavy))
        self.assertFalse(fight.is_controlled(soldier))
        self.assertIsNone(heavy.body_script.charge_velocity())
        self.assertIsNone(charge.find_var(expr.INSTANCE, 536).value())
        self.assertGreater(charge.find_var(expr.INSTANCE, 61).value(), 0)
        self.assertNotIn(35, component.held)
        self.assertEqual(self.vitals(run, soldier).total, 200)

        ammo = soldier.body_script.component.instances[11].vars[53]
        rounds = ammo.value()
        run.step({soldier: (0, 0, 1)})
        self.assertLess(ammo.value(), rounds)
        stopped = heavy.position
        if buttons == 0x08:
            deadline = fight.sleeps[heavy.entity].end
        else:
            deadline = fight.stuns[heavy.entity]
        while run.frame <= deadline:
            run.step({})
        self.assertFalse(fight.is_stunned(heavy))
        self.assertEqual(heavy.position, stopped)
        self.assertFalse(fight.is_controlled(soldier))

    def test_heavy_action35_moves_through_world_update_and_stops_at_wall(self):
        for wall in (False, True):
            with self.subTest(wall=wall):
                run, soldier, heavy = self.arena("5", (0, 0, -1))
                soldier.mover.position = [4, 0, -3]
                soldier.mover.body = None
                heavy.yaw = -32768
                if wall:
                    run.match.collision_world = World([
                        (-20, 0, -20, -20, 0, 20, 20, 0, 20),
                        (-20, 0, -20, 20, 0, 20, 20, 0, -20),
                        (-5, -1, -3, 5, -1, -3, 5, 6, -3),
                        (-5, -1, -3, 5, 6, -3, -5, 6, -3),
                    ])
                component = heavy.body_script.component
                component.edges([(35, True)])
                for _ in range(65):
                    run.step({})
                self.assertLess(heavy.position[2], -1.5)
                if wall:
                    self.assertGreater(heavy.position[2], -3)
                    self.assertAlmostEqual(heavy.velocity[2], 0, delta=0.01)
                else:
                    self.assertLess(heavy.position[2], -3)
                    self.assertAlmostEqual(heavy.velocity[2], -15, delta=0.01)


    def test_heavy_action35_release_eventually_retires_velocity_override(self):
        run, soldier, heavy = self.arena("5", (0, 0, -1))
        soldier.mover.position = [4, 0, -3]
        soldier.mover.body = None
        heavy.yaw = -32768
        script = heavy.body_script
        script.component.edges([(35, True)])
        for _ in range(65):
            run.step({})
        self.assertLess(heavy.position[2], -3)
        self.assertIsNotNone(script.charge_velocity())
        script.component.edges([(35, False)])
        for _ in range(400):
            run.step({})
            if script.charge_velocity() is None and heavy.impulse_mover is None:
                break
        self.assertNotIn(35, script.component.held)
        self.assertIsNone(script.charge_velocity())
        self.assertIsNone(heavy.impulse_mover)
        stopped = heavy.position
        for _ in range(20):
            run.step({})
        self.assertEqual(heavy.position, stopped)

    def test_heavy_action35_wall_rays_use_live_origins_and_authored_lengths(self):
        for wall_z, top, expected in (
            (None, 5, (False, False)), (-1.75, 0.5, (True, False)),
            (-1.75, 5, (True, True)), (-2.5, 5, (False, True)), (-3.5, 5, (False, False)),
        ):
            with self.subTest(wall_z=wall_z, top=top):
                run, soldier, heavy = self.arena("5", (0, 0, -1))
                heavy.yaw = -32768
                soldier.mover.position = (4, 0, -3)
                if wall_z is not None:
                    run.match.collision_world = World([
                        (-5, -1, wall_z, 5, -1, wall_z, 5, top, wall_z),
                        (-5, -1, wall_z, 5, top, wall_z, -5, top, wall_z),
                    ])
                component = heavy.body_script.component
                charge = next(i for i in component.instances.values() if i.graph.index == 0x1408)
                results = tuple(charge.find_var(expr.INSTANCE, key) for key in (2824, 13757))
                self.assertEqual(tuple(result.value() for result in results), (None, None))
                component.edges([(35, True)])
                for _ in range(100):
                    self.input(run, soldier)
                    if all(result.value() is not None for result in results):
                        break
                for result, hit in zip(results, expected, strict=True):
                    self.assertIs(result.value(), hit)
                self.input(run, soldier)
                wall_switch = charge.states[charge.graph.node(169).state]
                low_obstacle = expected[0] and not expected[1]
                self.assertIs(wall_switch.current, low_obstacle)
                self.assertEqual(charge.find_var(expr.INSTANCE, 59).value(), 0 if low_obstacle else 1)
                for position in (51, 160):
                    state = charge.states.get(charge.graph.node(position).state)
                    self.assertEqual(bool(state and state.counter), low_obstacle)
                self.assertEqual(heavy.position, (0, 0, -1))


    def test_heavy_action31_cooldown_rejects_early_tap_and_recovers_damage(self):
        run, soldier, heavy = self.arena("5", (0, 0, -1))
        heavy.yaw = -32768
        pool = self.vitals(run, soldier)
        component = heavy.body_script.component
        melee = next(i for i in component.instances.values() if i.graph.index == 0x140E)
        ability = melee.states[melee.graph.node(9).state]
        contact_node = melee.graph.node(5)
        component.edges([(31, True)])
        self.input(run, soldier)
        component.edges([(31, False)])
        for _ in range(40):
            self.input(run, soldier)
            if pool.total < 200:
                break
        self.assertEqual(pool.total, 120)
        contact = melee.states[contact_node.state]
        self.assertEqual(contact.counter, 1)
        self.assertGreater(ability.remaining(), 0)

        component.edges([(31, True)])
        self.input(run, soldier)
        component.edges([(31, False)])
        for _ in range(90):
            self.input(run, soldier)
        self.assertEqual(ability.remaining(), 0)
        self.assertEqual(contact.counter, 1)
        self.assertEqual(pool.total, 120)

        component.edges([(31, True)])
        self.input(run, soldier)
        component.edges([(31, False)])
        for _ in range(60):
            self.input(run, soldier)
        self.assertEqual(contact.counter, 2)
        self.assertEqual(pool.total, 40)


    def test_soldier_rifle_kills_heavy_during_action31_windup(self):
        run, soldier, heavy = self.arena("5", (0, 0, -1))
        fight = run.match.combat
        heavy.yaw = -32768
        own, target = self.vitals(run, soldier), self.vitals(run, heavy)
        target.damage(target.total - 20)  # Isolate the lethal rifle hit during windup.
        component = heavy.body_script.component
        melee = next(i for i in component.instances.values() if i.graph.index == 0x140E)
        windup = melee.graph.node(20)
        contact = melee.graph.node(5)
        component.edges([(31, True)])
        for _ in range(10):
            self.input(run, soldier)
            state = melee.states.get(windup.state)
            if state is not None and state.active:
                break
        self.assertIsNotNone(state)
        self.assertTrue(state.active)
        self.assertNotIn(contact.state, melee.states)
        self.assertEqual(own.total, 200)
        yaw, pitch = aim(fight._eye(soldier), (0, 0.8, -1))
        self.input(run, soldier, 1, yaw, pitch)
        self.assertTrue(heavy.dead)
        self.assertEqual(target.killer, soldier.body)
        self.assertTrue(component.owner_dead)
        for _ in range(65):
            self.input(run, soldier)
        self.assertEqual(own.total, 200)
        self.assertFalse(melee.states[contact.state].active)


    def test_heavy_action31_wound_funds_visor_through_soldier_field(self):
        run, soldier, heavy = self.arena("5", (0, 0, -1))
        heavy.yaw = -32768
        pool, target = self.vitals(run, soldier), self.vitals(run, heavy)
        script = soldier.body_script
        script.ult_seconds = 0
        cost = script.component.entity_var(338).value()
        charge = script.component.entity_var(342)
        script.set_ult_charge(cost - 80)
        component = heavy.body_script.component
        component.edges([(31, True)])
        self.input(run, soldier)
        component.edges([(31, False)])
        for _ in range(40):
            self.input(run, soldier)
        self.assertEqual(pool.total, 120)
        self.assertEqual(pool.ult_healing_deficit, 80)
        self.input(run, soldier, 0x20)
        self.assertEqual(charge.value(), cost - 80)

        self.input(run, soldier, 0x10)
        for _ in range(260):
            self.input(run, soldier)
        self.assertEqual(pool.total, 200)
        self.assertEqual(pool.ult_healing_deficit, 0)
        self.assertAlmostEqual(soldier.stats.value(stats.HEALING), 80)
        self.assertEqual(charge.value(), cost)

        self.input(run, soldier, 0x20)
        self.assertEqual(charge.value(), 0)
        for _ in range(80):
            self.input(run, soldier)
        before = target.total
        self.input(run, soldier, 1)
        self.assertEqual(target.total, before - 20)
        self.assertEqual(charge.value(), 0)


    def test_manual_trooper_strike_respects_cooldown_without_primary_fire(self):
        run, soldier, trooper = self.arena("1", (0, 0, -1.75))
        soldier.mover.position = [0, 0, -3]
        soldier.mover.body = None
        pool = self.vitals(run, soldier)
        angles = aim(run.match.combat._eye(soldier), (0, 0.8, -1.75))
        for _ in range(15):
            run.step({soldier: (*angles, 0)})
        console = npc_console.Console(self.catalog)
        expected = {"handled": True, "action": "strike", "entity": trooper.entity}
        component = trooper.body_script.component
        melee = next(i for i in component.instances.values() if i.graph.index == 0x1422)
        ability = melee.states[melee.graph.node(8).state]
        self.assertEqual(console.press("}", run.match, soldier), expected)
        for _ in range(30):
            run.step({soldier: (*angles, 0)})
            if pool.total < 200:
                break
        self.assertEqual(pool.total, 170)
        contact = melee.states[melee.graph.node(4).state]
        self.assertEqual(contact.counter, 1)
        self.assertGreater(ability.remaining(), 0)
        self.assertEqual(console.press("}", run.match, soldier), expected)
        for _ in range(75):
            run.step({soldier: (*angles, 0)})
        self.assertEqual(pool.total, 170)
        self.assertEqual(contact.counter, 1)
        self.assertEqual(ability.remaining(), 0)
        self.assertEqual(console.press("}", run.match, soldier), expected)
        for _ in range(30):
            run.step({soldier: (*angles, 0)})
        self.assertEqual(pool.total, 140)
        self.assertEqual(contact.counter, 2)
        self.assertFalse(trooper.firing)
        self.assertNotIn(31, component.held)
        self.assertNotIn(20, component.held)

    def test_trooper_and_heavy_melee_move_soldier_and_barrier_blocks_force(self):
        for key, source_z, damage in (("1", -1.75, 30), ("5", -1, 80)):
            for protected in (False, True):
                with self.subTest(npc=self.catalog[key].name, protected=protected):
                    run, soldier, actor = self.arena(key, (0, 0, source_z))
                    soldier.mover.position = [0, 0, -3]
                    soldier.mover.body = None
                    actor.yaw = -32768
                    fight = run.match.combat
                    if protected:
                        zarya = self.heavy_barrier_caster(run, soldier)
                    for _ in range(15):
                        run.step({})
                    if protected:
                        angles = aim(fight._eye(zarya), (0, 0.9, -3))
                        run.step({zarya: (*angles, 0x10)})
                        self.assertEqual(fight.zarya_barriers.projected[zarya.body][1:], (200, soldier.body))
                    actor.body_script.component.edges([(31, True), (31, False)])
                    for frame in range(30):
                        # Trooper's small impulse dissipates in ground friction.
                        # A real jump exposes the horizontal effect in air.
                        run.step({soldier: (0, 0, JUMP if key == "1" and frame == 11 else 0)})
                    self.assertEqual(self.vitals(run, soldier).total, 200 if protected else 200 - damage)
                    if protected:
                        self.assertAlmostEqual(soldier.mover.position[2], -3)
                        self.assertEqual(fight.zarya_barriers.projected[zarya.body][1], 200 - damage)
                        self.assertAlmostEqual(zarya.body_script.component.entity_var(87).value(), damage / 5)
                    else:
                        self.assertLess(soldier.mover.position[2], -3.1 if key == "1" else -6)
                    self.assertIsNotNone(actor.body_script)

    def test_trooper_melee_checks_authored_distance_cover_and_team(self):
        for gate in ("inside", "edge", "cover", "team"):
            with self.subTest(gate=gate):
                run, soldier, trooper = self.arena("1", (0, 0, -1.5 if gate == "edge" else -1.75),
                                                   team=0 if gate == "team" else 1)
                trooper.yaw = -32768
                if gate == "cover":
                    run.match.collision_world = World([
                        (-5, -1, -2.4, 5, -1, -2.4, 5, 6, -2.4),
                        (-5, -1, -2.4, 5, 6, -2.4, -5, 6, -2.4),
                    ])
                component = trooper.body_script.component
                melee = next(i for i in component.instances.values() if i.graph.index == 0x1422)
                for _ in range(15):
                    self.input(run, soldier)
                component.edges([(31, True), (31, False)])
                for _ in range(30):
                    self.input(run, soldier)
                search = melee.states[melee.graph.node(5).state]
                self.assertEqual(search.counter, 1)
                self.assertEqual(search.targets, (expr.Entity(soldier.body),) if gate in ("inside", "edge") else ())
                self.assertEqual(self.vitals(run, soldier).total, 170 if gate == "inside" else 200)
                for _ in range(20):
                    self.input(run, soldier)
                explosion = melee.states[melee.graph.node(27).state]
                self.assertEqual(explosion.counter, int(gate == "inside"))
                self.assertFalse(explosion.active)
                if gate == "inside":
                    contact = melee.states[melee.graph.node(4).state]
                    self.assertEqual(explosion.start, contact.start)
                    self.assertEqual(explosion.ended_at, contact.ended_at)
                    self.assertGreater(explosion.ended_at - explosion.start, 0)
                    self.assertLess(explosion.ended_at - explosion.start, 800)
                else:
                    self.assertEqual(soldier.mover.velocity, (0, 0, 0))

    def test_soldier_rifle_kills_trooper_during_melee_windup(self):
        run, soldier, trooper = self.arena("1", (0, 0, -1.75))
        soldier.mover.position = [0, 0, -3]
        soldier.mover.body = None
        fight = run.match.combat
        own, target = self.vitals(run, soldier), self.vitals(run, trooper)
        target.damage(target.total - 20)  # Isolate the lethal rifle shot.
        angles = aim(fight._eye(soldier), (0, 0.8, -1.75))
        for _ in range(15):
            run.step({soldier: (*angles, 0)})
        console = npc_console.Console(self.catalog)
        self.assertEqual(console.press("}", run.match, soldier)["entity"], trooper.entity)
        run.step({})
        component = trooper.body_script.component
        melee = next(i for i in component.instances.values() if i.graph.index == 0x1422)
        self.assertTrue(melee.states[melee.graph.node(14).state].active)
        contact = melee.graph.node(4).state
        self.assertNotIn(contact, melee.states)
        run.step({soldier: (*angles, 1)})
        self.assertTrue(trooper.dead)
        self.assertEqual(target.killer, soldier.body)
        self.assertTrue(component.owner_dead)
        for _ in range(35):
            run.step({})
        self.assertEqual(own.total, 200)
        self.assertFalse(contact in melee.states and melee.states[contact].counter)

    def test_actual_flashbang_interrupts_trooper_and_heavy_melee_then_fresh_strike_hits(self):
        for key, source_z, graph_id, ability_pos, contact_pos, damage in (
            ("1", -1.75, 0x1422, 8, 4, 30),
            ("5", -1, 0x140E, 9, 5, 80),
        ):
            with self.subTest(npc=self.catalog[key].name):
                run, soldier, actor = self.arena(key, (0, 0, source_z))
                soldier.mover.position = [0, 0, -3]
                soldier.mover.body = None
                support = run.match.add_player(2, "Support", soldier.hero.guid, soldier.team, False)
                in_world(run.match, support)
                support.client = NpcRecordingClient(support)
                run.players.append(support)
                self.assertTrue(run.match.switch_hero(support, heroes()[0x02E0000000000042]))
                support.mover.position = [2, 0, source_z]
                support.mover.body = None
                fight = run.match.combat
                angles = aim(fight._eye(support), (0, .8, source_z))
                pool = self.vitals(run, soldier)
                for _ in range(15):
                    run.step({support: (*angles, 0)})

                console = npc_console.Console(self.catalog)
                self.assertEqual(console.press("}", run.match, soldier)["entity"], actor.entity)
                run.step({support: (*angles, 0x10)})
                melee = next(i for i in actor.body_script.component.instances.values() if i.graph.index == graph_id)
                ability = melee.states[melee.graph.node(ability_pos).state]
                contact_state = melee.graph.node(contact_pos).state
                self.assertTrue(ability.flags & Ability.RUNNING)
                self.assertNotIn(contact_state, melee.states)
                for _ in range(18):
                    if fight.is_stunned(actor):
                        break
                    run.step({support: (*angles, 0)})
                self.assertTrue(fight.is_stunned(actor))
                self.assertFalse(ability.flags & Ability.RUNNING)
                with self.assertRaisesRegex(ValueError, "eligible attacker"):
                    console.press("}", run.match, soldier)
                for _ in range(110):
                    self.assertEqual(pool.total, 200)
                    self.assertFalse(contact_state in melee.states and melee.states[contact_state].counter)
                    if not fight.is_stunned(actor) and ability.remaining() == 0:
                        break
                    run.step({})
                self.assertFalse(fight.is_stunned(actor))
                self.assertEqual(ability.remaining(), 0)
                self.assertEqual(console.press("}", run.match, soldier)["entity"], actor.entity)
                for _ in range(45):
                    run.step({})
                    if pool.total < 200:
                        break
                self.assertEqual(pool.total, 200 - damage)
                self.assertEqual(melee.states[contact_state].counter, 1)

    def test_manual_heavy_strike_respects_cooldown_without_primary_fire(self):
        run, soldier, heavy = self.arena("5", (0, 0, -1))
        pool = self.vitals(run, soldier)
        yaw, pitch = aim(run.match.combat._eye(soldier), (0, 0.8, -1))
        soldier.mover.position = [0, 0, -3]
        soldier.mover.body = None
        run.step({soldier: (yaw, pitch, 0)})
        console = npc_console.Console(self.catalog)
        result = console.press("}", run.match, soldier)
        self.assertEqual(result, {"handled": True, "action": "strike", "entity": heavy.entity})
        for _ in range(40):
            run.step({soldier: (yaw, pitch, 0)})
            if pool.total < 200:
                break
        self.assertEqual(pool.total, 120)
        component = heavy.body_script.component
        melee = next(i for i in component.instances.values() if i.graph.index == 0x140E)
        ability = melee.states[melee.graph.node(9).state]
        self.assertGreater(ability.remaining(), 0)
        self.assertLess(soldier.mover.position[2], -3)
        yaw, pitch = aim(run.match.combat._eye(soldier), (0, 0.8, -1))
        run.step({soldier: (yaw, pitch, 0)})
        self.assertEqual(console.press("}", run.match, soldier), result)
        for _ in range(90):
            run.step({soldier: (yaw, pitch, 0)})
        self.assertEqual(pool.total, 120)
        self.assertEqual(ability.remaining(), 0)
        soldier.mover.position = [0, 0, -3]
        soldier.mover.body = None
        yaw, pitch = aim(run.match.combat._eye(soldier), (0, 0.8, -1))
        run.step({soldier: (yaw, pitch, 0)})
        self.assertEqual(console.press("}", run.match, soldier), result)
        for _ in range(60):
            run.step({soldier: (yaw, pitch, 0)})
        self.assertEqual(pool.total, 40)
        self.assertFalse(heavy.firing)
        self.assertNotIn(31, heavy.body_script.component.held)
        self.assertNotIn(20, heavy.body_script.component.held)


    def test_heavy_action31_rejects_range_cover_and_team(self):
        for gate in ("range", "cover", "team"):
            with self.subTest(gate=gate):
                position = (0, 0, 5 if gate == "range" else -1)
                run, soldier, heavy = self.arena("5", position, team=0 if gate == "team" else 1)
                heavy.yaw = -32768
                if gate == "cover":
                    run.match.collision_world = World([
                        (-10, 0, -10, -10, 0, 10, 10, 0, 10),
                        (-10, 0, -10, 10, 0, 10, 10, 0, -10),
                        (-5, -1, -2, 5, -1, -2, 5, 5, -2),
                        (-5, -1, -2, 5, 5, -2, -5, 5, -2),
                    ])
                component = heavy.body_script.component
                component.edges([(31, True)])
                self.input(run, soldier)
                component.edges([(31, False)])
                for _ in range(65):
                    self.input(run, soldier)
                instance = next(i for i in component.instances.values() if i.graph.index == 0x140E)
                self.assertGreater(instance.states[instance.graph.node(5).state].counter, 0)
                self.assertEqual(self.vitals(run, soldier).total, 200)
                self.assertEqual(soldier.mover.velocity, (0, 0, 0))


    def sprint_npc_arena(self, key):
        # Give the Sniper room to complete its longer windup while Soldier approaches.
        run, soldier, actor = self.arena(key, (0, 0, 28 if key == "3" else 15))
        soldier.mover.position = [0, 0, -3]
        soldier.mover.body = None
        return run, soldier, actor

    def test_four_actual_npc_primaries_wound_sprinting_soldier_without_cancelling_sprint(self):
        for key in ("1", "2", "3", "5"):
            with self.subTest(npc=self.catalog[key].name):
                run, soldier, actor = self.sprint_npc_arena(key)
                fight = run.match.combat
                own, enemy = self.vitals(run, soldier), self.vitals(run, actor)
                self.sprint(run, soldier)
                self.assertEqual(npc_control.start(run.match, soldier), actor.entity)
                for _ in range(180):
                    speed = self.move(run, soldier)
                    if own.total < 200:
                        break
                self.assertTrue(0 < own.total < 200, own.total)
                self.assertFalse(fight.is_controlled(soldier))
                self.assertAlmostEqual(speed, 8.25, delta=0.01)
                self.assertGreater(npc_control.stop(run.match), 0)
                wounded, initial = own.total, enemy.total
                for _ in range(8):
                    self.assertAlmostEqual(self.move(run, soldier), 8.25, delta=0.01)
                rounds = soldier.body_script.component.instances[11].vars[53]
                before = rounds.value()
                for _ in range(45):
                    x, y, z = actor.position
                    speed = self.move(run, soldier, buttons=1, target=(x, y + .8, z))
                    if enemy.total < initial:
                        break
                self.assertLess(rounds.value(), before)
                self.assertLess(enemy.total, initial)
                self.assertAlmostEqual(speed, 5.5, delta=0.01)
                self.assertLessEqual(own.total, wounded)
                self.assertFalse(own.dead)

    def test_four_actual_npc_primaries_end_sprint_on_death_and_fresh_body_can_sprint(self):
        for key in ("1", "2", "3", "5"):
            with self.subTest(npc=self.catalog[key].name):
                run, soldier, actor = self.sprint_npc_arena(key)
                fight = run.match.combat
                own, enemy = self.vitals(run, soldier), self.vitals(run, actor)
                self.sprint(run, soldier)
                old_body, old_script = soldier.body, soldier.body_script
                own.damage(own.total - 1)
                self.assertEqual(npc_control.start(run.match, soldier), actor.entity)
                for _ in range(180):
                    speed = self.move(run, soldier)
                    if own.dead:
                        break
                    self.assertAlmostEqual(speed, 8.25, delta=.01)
                self.assertTrue(fight.is_dead(soldier))
                self.assertEqual(own.killer, actor.entity)
                self.assertTrue(old_script.component.owner_dead)
                self.assertGreater(npc_control.stop(run.match), 0)
                corpse = tuple(soldier.mover.position)
                ammo = old_script.component.instances[11].vars[53]
                rounds, enemy_health = ammo.value(), enemy.total
                for _ in range(12):
                    self.move(run, soldier, buttons=8 | 1)
                self.assertEqual(tuple(soldier.mover.position), corpse)
                self.assertEqual(ammo.value(), rounds)
                self.assertEqual(enemy.total, enemy_health)

                (deadline,) = (when for when, owner in fight.respawns if owner is soldier)
                tick = math.ceil(deadline / combat.TICK)
                run.match.update(tick * combat.TICK, tick, frames=False)
                self.assertNotEqual(soldier.body, old_body)
                self.assertEqual(self.vitals(run, soldier).total, 200)
                self.assertFalse(fight.is_dead(soldier))
                self.sprint(run, soldier)

    def test_heavy_charge_interrupts_sprint_and_fresh_input_restarts_it(self):
        run, soldier, heavy = self.heavy_capture_arena()
        fight = run.match.combat
        self.sprint(run, soldier)
        heavy.position = (0, 0, soldier.mover.position[2] + 14)
        sprint = next(
            state for instance in soldier.body_script.component.instances.values()
            for state in instance.states.values() if isinstance(state, Ability) and state.buttons()[0] == 35
        )
        self.assertTrue(sprint.flags & Ability.RUNNING)
        heavy.body_script.component.edges([(35, True)])
        for _ in range(95):
            speed = self.move(run, soldier)
            if fight.is_controlled(soldier):
                break
            self.assertAlmostEqual(speed, 8.25, delta=.01)
        self.assertEqual(fight.pins.get(heavy.entity), soldier.body)
        self.assertFalse(sprint.flags & Ability.RUNNING)
        ammo = soldier.body_script.component.instances[11].vars[53]
        before = ammo.value()
        for _ in range(6):
            self.move(run, soldier, buttons=8 | 1)
            self.assertTrue(fight.is_controlled(soldier))
            self.assertFalse(sprint.flags & Ability.RUNNING)
            self.assertEqual(ammo.value(), before)
        self.assertGreater(npc_control.stop(run.match), 0)
        for _ in range(25):
            speed = self.move(run, soldier)
        self.assertFalse(fight.is_controlled(soldier))
        self.assertFalse(sprint.flags & Ability.RUNNING)
        self.assertAlmostEqual(speed, 5.5, delta=.01)
        self.move(run, soldier, buttons=8)
        for _ in range(10):
            speed = self.move(run, soldier)
        self.assertTrue(sprint.flags & Ability.RUNNING)
        self.assertAlmostEqual(speed, 8.25, delta=.01)

    def project_onto_sprinting_soldier(self, run, soldier, ally, *, hp=None):
        fight = run.match.combat
        x, y, z = soldier.mover.position
        ally.mover.position = [x + 2.5, y, z + 1]
        ally.mover.body = None
        yaw, pitch = aim(fight._eye(ally), (x, y + .9, z))
        ally.take_commands([Command(soldier.body_script.frame, yaw=yaw, pitch=pitch, buttons=16)])
        projected = fight.zarya_barriers.projected
        stamp, amount, recipient = projected[ally.body]
        self.assertEqual((amount, recipient), (200, soldier.body))
        if hp is not None:
            projected[ally.body] = stamp, hp, recipient
        return projected

    def test_projected_barrier_protects_sprint_from_actual_npc_weapon_fire(self):
        for key, slot2 in (("1", False), ("2", False), ("3", False), ("3", True), ("5", False)):
            for breaking in (False, True):
                with self.subTest(npc=self.catalog[key].name, slot2=slot2, breaking=breaking):
                    run, soldier, actor = self.sprint_npc_arena(key)
                    ally = self.heavy_barrier_caster(run, soldier)
                    ally.client = NpcRecordingClient(ally)
                    own = self.vitals(run, soldier)
                    if slot2:
                        actor.body_script.component.edges([(16, True), (16, False)])
                    self.sprint(run, soldier)
                    self.assertEqual(npc_control.start(run.match, soldier), actor.entity)
                    if key == "3" and not slot2:
                        # Start the charged rifle before casting the two-second barrier.
                        for _ in range(55):
                            self.assertAlmostEqual(self.move(run, soldier), 8.25, delta=.01)
                            self.assertEqual(own.total, 200)
                    projected = self.project_onto_sprinting_soldier(
                        run, soldier, ally, hp=1 if breaking else None,
                    )
                    hp = 1 if breaking else 200
                    for _ in range(110):
                        self.assertAlmostEqual(self.move(run, soldier), 8.25, delta=.01)
                        remaining = projected.get(ally.body, (None, 0))[1]
                        if remaining < hp:
                            break
                        self.assertEqual(own.total, 200)
                    self.assertLess(remaining, hp)
                    if not breaking:
                        self.assertGreater(remaining, 0)
                        self.assertEqual(own.total, 200)
                        if slot2:
                            self.assertEqual(remaining, 195)
                        continue
                    self.assertEqual(remaining, 0)
                    # One Enforcer pellet breaks the shield, leaving 24 two-damage pellets.
                    after_break = 152 if key == "2" else 200
                    self.assertEqual(own.total, after_break)
                    for _ in range(160):
                        self.assertAlmostEqual(self.move(run, soldier), 8.25, delta=.01)
                        if own.total < after_break:
                            break
                    self.assertTrue(0 < own.total < after_break)
                    if slot2:
                        self.assertEqual(own.total, 195)
                    self.assertFalse(run.match.combat.is_controlled(soldier))

    def test_npc_melee_during_sprint_with_and_without_projected_barrier(self):
        for key, approach, damage in (("1", 3.0, 30), ("5", 4.5, 80)):
            for shield_hp in (0, 200, 1):
                with self.subTest(npc=self.catalog[key].name, shield_hp=shield_hp):
                    run, soldier, actor = self.sprint_npc_arena(key)
                    fight = run.match.combat
                    own = self.vitals(run, soldier)
                    if shield_hp:
                        ally = self.heavy_barrier_caster(run, soldier)
                    self.sprint(run, soldier)
                    actor.position = (0, 0, soldier.mover.position[2] + approach)
                    if shield_hp:
                        projected = self.project_onto_sprinting_soldier(
                            run, soldier, ally, hp=1 if shield_hp == 1 else None,
                        )
                    self.assertEqual(npc_control.strike(run.match, soldier), actor.entity)
                    sprint = next(
                        state for instance in soldier.body_script.component.instances.values()
                        for state in instance.states.values()
                        if isinstance(state, Ability) and state.buttons()[0] == 35
                    )
                    for _ in range(30):
                        speed = self.move(run, soldier)
                        self.assertTrue(sprint.flags & Ability.RUNNING)
                        self.assertFalse(fight.is_controlled(soldier))
                        if shield_hp:
                            self.assertEqual(own.total, 200)
                            self.assertAlmostEqual(speed, 8.25, delta=.01)
                            if projected[ally.body][1] < shield_hp:
                                break
                        elif own.total < 200:
                            break
                    if shield_hp:
                        self.assertEqual(projected[ally.body][1], max(0, shield_hp - damage))
                    else:
                        self.assertEqual(own.total, 200 - damage)
                    if not shield_hp and key == "5":
                        self.assertGreater(soldier.mover.velocity[1], 0)
                        self.assertLess(soldier.mover.velocity[2], 0)
                        for _ in range(100):
                            self.move(run, soldier)
                            self.assertTrue(sprint.flags & Ability.RUNNING)
                            if abs(soldier.mover.velocity[2] - 8.25) < .01:
                                break
                    self.assertAlmostEqual(soldier.mover.velocity[2], 8.25, delta=.01)

    def test_projected_barrier_keeps_sprint_through_heavy_capture_contact(self):
        for breaking in (False, True):
            with self.subTest(breaking=breaking):
                run, soldier, heavy = self.heavy_capture_arena()
                ally = self.heavy_barrier_caster(run, soldier)
                fight = run.match.combat
                self.sprint(run, soldier)
                heavy.position = (0, 0, soldier.mover.position[2] + 9)
                hp = 40 if breaking else 200
                projected = self.project_onto_sprinting_soldier(
                    run, soldier, ally, hp=hp if breaking else None,
                )
                heavy.body_script.component.edges([(35, True)])
                for _ in range(95):
                    speed = self.move(run, soldier)
                    self.assertFalse(fight.is_controlled(soldier))
                    self.assertAlmostEqual(speed, 8.25, delta=.01)
                    row = projected.get(ally.body)
                    if row is None or row[1] < hp:
                        break
                self.assertEqual(self.vitals(run, soldier).total, 200)
                self.assertNotIn(heavy.entity, fight.pins)
                if breaking:
                    self.assertEqual(projected.get(ally.body, (None, 0))[1], 0)
                    for _ in range(5):
                        self.move(run, soldier)
                        if fight.is_controlled(soldier):
                            break
                    self.assertEqual(fight.pins.get(heavy.entity), soldier.body)
                else:
                    self.assertEqual(projected[ally.body][1:], (150, soldier.body))
                    self.assertGreater(npc_control.stop(run.match), 0)
                    for _ in range(8):
                        self.assertAlmostEqual(self.move(run, soldier), 8.25, delta=.01)
                        self.assertFalse(fight.is_controlled(soldier))

    def test_heavy_assault_kills_soldier_during_helix_sprint_out(self):
        run, soldier, heavy = self.arena("5", (0, 0, -12))
        soldier.mover.position = [0, 0, -3]
        soldier.mover.body = None
        self.sprint(run, soldier)
        fight = run.match.combat
        pool = self.vitals(run, soldier)
        heavy.attack_target, heavy.firing = soldier.entity, True
        for _ in range(300):
            speed = self.move(run, soldier)
            if pool.total <= 5:
                break
        self.assertEqual(pool.total, 5)
        self.assertAlmostEqual(speed, 8.25, delta=0.01)
        for _ in range(15):
            self.move(run, soldier, buttons=2)
            self.assertFalse(fight.rockets)
            if fight.is_dead(soldier):
                break
        self.assertTrue(fight.is_dead(soldier), pool.total)
        self.assertEqual(pool.killer, heavy.entity)
        heavy.firing = False
        for _ in range(45):
            self.move(run, soldier, buttons=2)
            self.assertFalse(fight.rockets)
            self.assertFalse(fight.projectiles.shots)

    def test_launched_helix_hits_each_pve_body_after_heavy_kills_soldier(self):
        for key, definition in self.catalog.items():
            for lethal in (False, True):
                with self.subTest(npc=definition.name, lethal=lethal):
                    run, soldier, actor = self.arena(key, (0, 0, 25))
                    heavy = npc.spawn(run.match, self.catalog["5"], (0, 0, -12), team=1)
                    fight = run.match.combat
                    own, target = self.vitals(run, soldier), self.vitals(run, actor)
                    if lethal:
                        target.damage(target.total - 110)  # Fixture wound leaves splash to finish the kill.
                    initial = target.total
                    script = soldier.body_script
                    script.ult_seconds = 0
                    charge = script.component.vars[342]
                    source = soldier.body
                    heavy.attack_target, heavy.firing = soldier.entity, True
                    for _ in range(300):
                        self.input(run, soldier)
                        if own.total <= 5:
                            break
                    self.assertEqual(own.total, 5)
                    yaw, pitch = aim(fight._eye(soldier), (0, 0.8, 25))
                    self.input(run, soldier, 2, yaw, pitch)
                    rocket, = fight.rockets
                    for _ in range(25):
                        if fight.is_dead(soldier):
                            break
                        self.input(run, soldier)
                    self.assertTrue(fight.is_dead(soldier))
                    self.assertEqual(own.killer, heavy.entity)
                    self.assertEqual(charge.value(), 0)
                    self.assertIn(rocket, fight.rockets)
                    self.assertEqual(target.total, initial)
                    for _ in range(45):
                        self.input(run, soldier)
                        if rocket not in fight.rockets:
                            break
                    self.assertNotIn(rocket, fight.rockets)
                    self.assertEqual(target.total, max(0, initial - 120))
                    self.assertEqual(charge.value(), 0)
                    if lethal:
                        self.assertTrue(actor.dead)
                        self.assertEqual(target.killer, source)


    def test_body_replacement_retires_helix_before_impact_on_each_pve_body(self):
        for key, definition in self.catalog.items():
            with self.subTest(npc=definition.name):
                run, soldier, actor = self.arena(key, (0, 0, 25))
                fight = run.match.combat
                pool = self.vitals(run, actor)
                initial = pool.total
                old_body = soldier.body
                yaw, pitch = aim(fight._eye(soldier), (0, 0.8, 25))
                self.input(run, soldier, 2, yaw, pitch)
                rocket, = fight.rockets
                self.assertEqual(rocket.source_entity, old_body)
                for _ in range(5):
                    self.input(run, soldier)
                self.assertIn(rocket, fight.rockets)
                run.match.switch_hero(soldier, soldier.hero)
                self.assertNotEqual(soldier.body, old_body)
                self.assertNotIn(rocket, fight.rockets)
                script = soldier.body_script
                script.ult_seconds = 0
                charge = script.component.vars[342]
                for _ in range(45):
                    self.input(run, soldier)
                self.assertEqual(pool.total, initial)
                self.assertEqual(charge.value(), 0)
                self.assertFalse(soldier.client.messages(combat.HIT))

                soldier.mover.position = [0, 0, -3]
                soldier.mover.body = None
                yaw, pitch = aim(fight._eye(soldier), (0, 0.8, 25))
                self.input(run, soldier, 2, yaw, pitch)
                fresh, = fight.rockets
                self.assertEqual(fresh.source_entity, soldier.body)
                for _ in range(45):
                    self.input(run, soldier)
                    if fresh not in fight.rockets:
                        break
                self.assertEqual(pool.total, initial - 120)
                self.assertGreater(charge.value(), 0)


    def test_admitted_field_releases_once_without_further_owner_commands(self):
        for key, definition in self.catalog.items():
            expected = None
            for mode in ("posed", "idle", "jump"):
                with self.subTest(npc=definition.name, mode=mode):
                    run, soldier, ally = self.arena(key)
                    soldier.mover.position = [0, 0, -3]
                    soldier.mover.body = None
                    soldier.body_script.ult_seconds = 0
                    fight, pool = run.match.combat, self.vitals(run, ally)
                    full = pool.total
                    angles = aim(fight._eye(soldier), (0, .8, 1))
                    run.step({soldier: (*angles, 1)})
                    self.assertLess(pool.total, full)
                    wound = full - pool.total
                    ally.team = soldier.team
                    charge = soldier.body_script.component.entity_var(342)
                    before_field = charge.value()
                    run.step({soldier: (0, 0, 16)})
                    accepted = run.frame
                    self.assertFalse(fight.projectiles.shots)
                    end = accepted + 80
                    if mode == "posed":
                        for _ in range(80):
                            run.step({})
                    else:
                        for tick in ((end,) if mode == "jump" else range(accepted + 1, end + 1)):
                            run.match.update(tick * combat.TICK, tick)
                        run.frame = end
                        last_pose = soldier.mover.last_command
                        soldier.take_commands([Command(accepted + 2, buttons=16)])
                        self.assertIs(soldier.mover.last_command, last_pose)
                    shot, = fight.projectiles.shots
                    self.assertEqual(shot.spawn, projectiles.FIELD_ENTITY)
                    self.assertIsNotNone(shot.field_tick)
                    self.assertEqual(pool.total, full)
                    self.assertEqual(ally.body_script.component.vars[OWNER_HEALTH].value(), full)
                    self.assertAlmostEqual(charge.value() - before_field, wound, places=4)
                    observed = (shot.frame, shot.field_tick, shot.where, charge.value())
                    if expected is None:
                        expected = observed
                    self.assertEqual(observed, expected)
                    run.step({soldier: (0, 0, 16)})
                    for _ in range(40):
                        run.step({})
                    self.assertEqual(fight.projectiles.shots, [shot])

    def test_released_field_canister_distinguishes_lethal_hit_from_body_replacement(self):
        for key, definition in self.catalog.items():
            for lethal in (True, False):
                with self.subTest(npc=definition.name, lethal=lethal):
                    run, soldier, ally = self.arena(key)
                    soldier.mover.position = [0, 0, -3]
                    soldier.mover.body = None
                    soldier.body_script.ult_seconds = 0
                    fight = run.match.combat
                    own, recipient = self.vitals(run, soldier), self.vitals(run, ally)
                    full = recipient.total
                    angles = aim(fight._eye(soldier), (0, .8, 1))
                    run.step({soldier: (*angles, 1)})
                    self.assertLess(recipient.total, full)
                    ally.team = soldier.team
                    wound = recipient.total
                    attacker = run.match.add_player(
                        2, "Enemy Soldier", soldier.hero.guid, 1 - soldier.team, False,
                    )
                    in_world(run.match, attacker)
                    attacker.client = NpcRecordingClient(attacker)
                    run.players.append(attacker)
                    self.assertTrue(run.match.switch_hero(attacker, soldier.hero))
                    attacker.mover.position = [0, 0, -9]
                    attacker.mover.body = None
                    own.damage(own.total - 15)
                    run.step({soldier: (0, 0, 16)})
                    for _ in range(50):
                        if fight.projectiles.shots:
                            break
                        run.step({})
                    canister, = fight.projectiles.shots
                    self.assertEqual(canister.spawn, projectiles.FIELD_ENTITY)
                    self.assertIsNone(canister.field_tick)
                    run.step({})
                    self.assertGreater(canister.field_due, fight.tick + 1)
                    self.assertEqual(recipient.total, wound)
                    old_body = soldier.body
                    old_charge = soldier.body_script.component.entity_var(342)
                    charge_before = old_charge.value()
                    if lethal:
                        angles = aim(fight._eye(attacker), (0, .8, -3))
                        run.step({attacker: (*angles, 1)})
                        self.assertTrue(fight.is_dead(soldier))
                        self.assertEqual(own.killer, attacker.body)
                        self.assertEqual(soldier.body, old_body)
                        self.assertIn(canister, fight.projectiles.shots)
                        self.assertIsNone(canister.field_tick)
                    else:
                        self.assertTrue(run.match.switch_hero(soldier, soldier.hero))
                        self.assertNotEqual(soldier.body, old_body)
                        self.assertNotIn(canister, fight.projectiles.shots)

                    while fight.tick < canister.field_due + 4:
                        run.step({})
                    if lethal:
                        self.assertEqual(canister.field_tick, canister.field_due)
                        self.assertAlmostEqual(recipient.total - wound, 4 * 35 * combat.TICK)
                        self.assertEqual(old_charge.value(), charge_before)
                        self.assertTrue(run.match.switch_hero(soldier, soldier.hero))
                    else:
                        self.assertIsNone(canister.field_tick)
                        self.assertEqual(recipient.total, wound)
                    self.assertNotIn(canister, fight.projectiles.shots)
                    self.assertNotEqual(soldier.body, old_body)
                    after_retirement = recipient.total
                    for _ in range(20):
                        run.step({})
                    self.assertEqual(recipient.total, after_retirement)
                    self.assertAlmostEqual(
                        ally.body_script.component.vars[OWNER_HEALTH].value(), recipient.total, places=4,
                    )
                    soldier.body_script.ult_seconds = 0
                    fresh_charge = soldier.body_script.component.entity_var(342)
                    charge_before = fresh_charge.value()
                    soldier.mover.position = [0, 0, -3]
                    soldier.mover.body = None
                    run.step({soldier: (0, 0, 16)})
                    for _ in range(130):
                        run.step({})
                        if recipient.total == full:
                            break
                    fresh, = fight.projectiles.shots
                    self.assertEqual(fresh.body, soldier.body)
                    self.assertEqual(fresh.spawn, projectiles.FIELD_ENTITY)
                    self.assertIsNotNone(fresh.field_tick)
                    self.assertEqual(recipient.total, full)
                    self.assertEqual(ally.body_script.component.vars[OWNER_HEALTH].value(), full)
                    self.assertAlmostEqual(
                        fresh_charge.value() - charge_before, full - after_retirement, places=4,
                    )


    def test_soldier_field_restores_wounds_from_four_actual_npc_weapons(self):
        for key in ("1", "2", "3", "5"):
            with self.subTest(npc=self.catalog[key].name):
                run, soldier, attacker = self.arena(key, (0, 0, 5))
                soldier.mover.position = [0, 0, -3]
                soldier.mover.body = None
                soldier.body_script.ult_seconds = 0
                fight = run.match.combat
                pool = self.vitals(run, soldier)
                attacker.attack_target, attacker.firing = soldier.entity, True
                for _ in range(180):
                    run.step({})
                    if pool.total < 200:
                        break
                self.assertLess(pool.total, 200)
                attacker.firing = False
                for _ in range(25):
                    run.step({})
                wounded = pool.total
                self.assertGreater(wounded, 0)
                self.assertEqual(soldier.body_script.component.vars[OWNER_HEALTH].value(), wounded)
                self.assertEqual(soldier.stats.value(stats.HEALING), 0)
                self.assertAlmostEqual(pool.ult_healing_deficit, 200 - wounded)
                charge = soldier.body_script.component.entity_var(342)
                self.assertEqual(charge.value(), 0)
                run.step({soldier: (0, 0, 16)})
                for _ in range(260):
                    run.step({})
                self.assertTrue(any(shot.spawn == projectiles.FIELD_ENTITY and shot.field_tick is not None
                                    for shot in fight.projectiles.shots))
                self.assertEqual(pool.total, 200)
                self.assertEqual(soldier.body_script.component.vars[OWNER_HEALTH].value(), 200)
                self.assertAlmostEqual(soldier.stats.value(stats.HEALING), 200 - wounded)
                self.assertAlmostEqual(charge.value(), 200 - wounded, delta=0.0001)
                self.assertEqual(pool.ult_healing_deficit, 0)

    def test_field_heals_between_trooper_hits_during_a_world_frame_gap(self):
        def under_fire(health, skip):
            run, soldier, trooper = self.arena("1", (0, 0, 5))
            soldier.mover.position = [0, 0, -3]
            soldier.mover.body = None
            soldier.body_script.ult_seconds = 0
            fight = run.match.combat
            pool = self.vitals(run, soldier)
            run.step({soldier: (0, 0, 16)})
            for _ in range(90):
                if any(shot.field_tick is not None for shot in fight.projectiles.shots):
                    break
                run.step({})
            field, = fight.projectiles.shots
            self.assertEqual(field.field_tick, run.frame)
            pool.damage(200 - health)
            trooper.attack_target, trooper.firing = soldier.entity, True

            ticks = 25
            if skip:
                endpoint = run.frame + ticks
                run.match.update(endpoint * combat.TICK, endpoint)
            else:
                for _ in range(ticks):
                    run.step({})
            return (
                pool.total, pool.dead, soldier.stats.value(stats.HEALING), pool.ult_healing_deficit,
                soldier.body_script.component.vars[OWNER_HEALTH].value(),
                soldier.body_script.component.entity_var(342).value(),
            )

        # With 26.5 HP the second hit is lethal before that frame's heal.
        for health, remaining, healing, dead in ((30, 4, 14, False), (26.5, 0, 13.44, True)):
            with self.subTest(initial_health=health):
                stepped = under_fire(health, skip=False)
                self.assertEqual(stepped[1], dead)
                self.assertAlmostEqual(stepped[0], remaining, places=5)
                self.assertAlmostEqual(stepped[2], healing, places=5)
                skipped = under_fire(health, skip=True)
                self.assertEqual(skipped[1], stepped[1])
                for actual, expected in zip(skipped, stepped, strict=True):
                    self.assertAlmostEqual(actual, expected, places=5)

    def sniper_weapon_arena(self, slot=2):
        run, soldier, sniper = self.arena("3", (0, 0, 18))
        soldier.mover.position = [0, 0, -3]
        soldier.mover.body = None
        component = sniper.body_script.component
        self.assertEqual(component.entity_var(31).value(), 1)
        if slot == 2:
            component.edges([(16, True), (16, False)])
        run.step({})
        self.assertEqual(component.entity_var(31).value(), slot)
        self.assertNotIn(16, component.held)
        self.assertEqual(npc_control.start(run.match, soldier), sniper.entity)
        return run, soldier, sniper

    def wait_for_sniper_shot(self, run):
        fight = run.match.combat
        for _ in range(165):
            run.step({})
            if fight.rockets:
                shot, = fight.rockets
                return shot
        self.fail("Sniper did not launch a shot")

    def test_sniper_weapon_manager_switch_keeps_in_flight_slot2_payload(self):
        run, soldier, sniper = self.sniper_weapon_arena()
        fight = run.match.combat
        pool = self.vitals(run, soldier)
        component = sniper.body_script.component
        guns = {i.graph.index: i for i in component.instances.values() if i.graph.index in (0x1122, 0x1134)}
        shot = self.wait_for_sniper_shot(run)
        self.assertEqual((shot.definition, shot.speed, shot.direct.amount), (0x04000000000023BB, 200, 5))
        self.assertEqual(shot.source_entity, sniper.entity)
        self.assertEqual(pool.total, 200)
        primary = guns[0x1122].states.get(guns[0x1122].graph.node(6).state)
        self.assertFalse(primary and primary.counter)
        sniper.firing = False
        component.edges([(15, True), (15, False)])
        run.step({})
        self.assertEqual(component.entity_var(31).value(), 1)
        self.assertNotIn(20, component.held)
        self.assertIn(shot, fight.rockets)
        for _ in range(20):
            run.step({})
        self.assertFalse(fight.rockets)
        self.assertEqual(pool.total, 195)
        self.assertEqual(npc_control.start(run.match, soldier), sniper.entity)
        shot = self.wait_for_sniper_shot(run)
        self.assertEqual((shot.definition, shot.speed, shot.direct.amount), (0x0400000000001E80, 200, 100))
        sniper.firing = False
        for _ in range(20):
            run.step({})
        self.assertFalse(fight.rockets)
        self.assertEqual(pool.total, 95)
        for gun in guns.values():
            self.assertIsNone(gun.graph.node(6).field("m_24E351A4"))
            self.assertEqual(gun.vars[53].value(), 20)

    @staticmethod
    def sniper_cover():
        return World([
            (-20, 0, -20, -20, 0, 24, 20, 0, 24),
            (-20, 0, -20, 20, 0, 24, 20, 0, -20),
            (-5, -1, 5, 5, -1, 5, 5, 6, 5),
            (-5, -1, 5, 5, 6, 5, -5, 6, 5),
        ])

    def test_sniper_both_slots_check_current_cover_team_aim_and_range_in_flight(self):
        for slot, definition in ((1, 0x0400000000001E80), (2, 0x04000000000023BB)):
            for gate in ("cover", "team", "miss", "range"):
                with self.subTest(slot=slot, gate=gate):
                    run, soldier, sniper = self.sniper_weapon_arena(slot)
                    fight = run.match.combat
                    shot = self.wait_for_sniper_shot(run)
                    self.assertEqual(shot.definition, definition)
                    sniper.firing = False
                    if gate == "cover":
                        run.match.collision_world = self.sniper_cover()
                    elif gate == "team":
                        soldier.team = sniper.team
                    else:
                        soldier.mover.position = [4, 0, -3] if gate == "miss" else [0, 0, -86]
                        soldier.mover.body = None
                    for _ in range(45):
                        run.step({})
                    self.assertFalse(fight.rockets)
                    self.assertEqual(self.vitals(run, soldier).total, 200)
                    if gate == "range":
                        self.assertAlmostEqual(shot.flown * shot.speed, 100)

    def test_sniper_slot2_cooldown_rejects_early_repress_and_field_heals_both_hits(self):
        run, soldier, sniper = self.sniper_weapon_arena()
        fight = run.match.combat
        pool = self.vitals(run, soldier)
        soldier.body_script.ult_seconds = 0
        component = sniper.body_script.component
        slot = next(i for i in component.instances.values() if i.graph.index == 0x1134)
        cooldown = slot.find_var(expr.INSTANCE, 173)
        first = self.wait_for_sniper_shot(run)
        sniper.firing = False
        run.step({})
        self.assertNotIn(20, component.held)
        self.assertGreater(cooldown.value(), 0)
        self.assertEqual(npc_control.start(run.match, soldier), sniper.entity)
        run.step({})
        sniper.firing = False
        for _ in range(35):
            run.step({})
            self.assertTrue(all(shot is first for shot in fight.rockets))
        self.assertEqual(cooldown.value(), 0)
        self.assertEqual(pool.total, 195)
        self.assertEqual(npc_control.start(run.match, soldier), sniper.entity)
        second = self.wait_for_sniper_shot(run)
        self.assertIsNot(second, first)
        self.assertEqual(second.definition, first.definition)
        sniper.firing = False
        for _ in range(20):
            run.step({})
        self.assertEqual(pool.total, 190)
        self.assertEqual(pool.ult_healing_deficit, 10)
        run.step({soldier: (0, 0, 0x10)})
        for _ in range(80):
            run.step({})
            if pool.total == 200:
                break
        self.assertEqual(pool.total, 200)
        self.assertAlmostEqual(soldier.stats.value(stats.HEALING), 10)
        self.assertAlmostEqual(soldier.body_script.component.entity_var(342).value(), 10)

    def test_actual_flashbang_cancels_sniper_windup_in_both_slots(self):
        for graph_id, windup_pos, amount in ((0x1122, 53, 100), (0x1134, 35, 5)):
            with self.subTest(graph=graph_id):
                run, soldier, sniper = self.arena("3", (0, 0, 18))
                soldier.mover.position = [0, 0, -3]
                soldier.mover.body = None
                support = run.match.add_player(2, "Support", soldier.hero.guid, soldier.team, False)
                in_world(run.match, support)
                support.client = NpcRecordingClient(support)
                run.players.append(support)
                self.assertTrue(run.match.switch_hero(support, heroes()[0x02E0000000000042]))
                support.mover.position = [2, 0, 18]
                support.mover.body = None
                fight = run.match.combat
                component = sniper.body_script.component
                if graph_id == 0x1134:
                    component.edges([(16, True), (16, False)])
                weapon = next(i for i in component.instances.values() if i.graph.index == graph_id)
                run.step({})
                self.assertEqual(npc_control.start(run.match, soldier), sniper.entity)
                run.step({})
                windup = weapon.states[weapon.graph.node(windup_pos).state]
                self.assertTrue(windup.active)
                self.assertFalse(fight.rockets)
                angles = aim(fight._eye(support), (0, 0.8, 18))
                run.step({support: (*angles, 0x10)})
                for _ in range(30):
                    run.step({support: (*angles, 0)})
                    if fight.is_stunned(sniper):
                        break
                self.assertTrue(fight.is_stunned(sniper))
                sniper.firing = False
                self.assertFalse(windup.active)
                self.assertNotIn(20, component.held)
                for _ in range(145):
                    run.step({})
                    self.assertFalse(any(r.shooter is sniper for r in fight.rockets))
                self.assertFalse(fight.is_stunned(sniper))
                self.assertEqual(self.vitals(run, soldier).total, 200)
                self.assertEqual(npc_control.start(run.match, soldier), sniper.entity)
                shot = self.wait_for_sniper_shot(run)
                self.assertEqual(shot.direct.amount, amount)
                sniper.firing = False
                for _ in range(12):
                    run.step({})
                self.assertEqual(self.vitals(run, soldier).total, 200 - amount)

    def test_sniper_manual_stop_discards_primary_charge_before_fresh_engagement(self):
        run, soldier, sniper = self.arena("3", (0, 0, 18))
        soldier.mover.position = [0, 0, -3]
        soldier.mover.body = None
        fight = run.match.combat
        component = sniper.body_script.component
        weapon = next(i for i in component.instances.values() if i.graph.index == 0x1122)
        run.step({})
        self.assertEqual(npc_control.start(run.match, soldier), sniper.entity)
        for _ in range(85):
            run.step({})
        windup = weapon.states[weapon.graph.node(53).state]
        self.assertTrue(windup.active)
        old_deadline = windup.start + 2000
        self.assertFalse(fight.rockets)
        self.assertEqual(npc_control.stop(run.match), 1)
        run.step({})
        self.assertFalse(windup.active)
        self.assertNotIn(20, component.held)
        self.assertEqual(npc_control.start(run.match, soldier), sniper.entity)
        run.step({})
        self.assertTrue(windup.active)
        self.assertLess(component.now, old_deadline)
        while component.now <= old_deadline:
            run.step({})
            self.assertFalse(fight.rockets)
        shot = self.wait_for_sniper_shot(run)
        self.assertEqual(shot.direct.amount, 100)
        sniper.firing = False
        for _ in range(12):
            run.step({})
        self.assertEqual(self.vitals(run, soldier).total, 100)

    def test_sniper_primary_windup_restarts_after_controller_gate_recovery(self):
        for gate in ("cover", "range", "team"):
            with self.subTest(gate=gate):
                run, soldier, sniper = self.arena("3", (0, 0, 18))
                soldier.mover.position = [0, 0, -3]
                soldier.mover.body = None
                fight = run.match.combat
                component = sniper.body_script.component
                weapon = next(i for i in component.instances.values() if i.graph.index == 0x1122)
                run.step({})
                self.assertEqual(npc_control.start(run.match, soldier), sniper.entity)
                for _ in range(85):
                    run.step({})
                windup = weapon.states[weapon.graph.node(53).state]
                self.assertTrue(windup.active)
                old_start, old_counter = windup.start, windup.counter
                self.assertFalse(fight.rockets)

                clear = run.match.collision_world
                team = soldier.team
                if gate == "cover":
                    run.match.collision_world = self.sniper_cover()
                elif gate == "range":
                    soldier.mover.position = [0, 0, -86]
                    soldier.mover.body = None
                else:
                    soldier.team = sniper.team
                run.step({})
                self.assertFalse(windup.active)
                self.assertNotIn(20, component.held)
                self.assertTrue(sniper.firing)
                self.assertEqual(sniper.attack_target, soldier.entity)

                run.match.collision_world = clear
                soldier.team = team
                soldier.mover.position = [0, 0, -3]
                soldier.mover.body = None
                run.step({})
                self.assertIn(20, component.held)
                self.assertTrue(windup.active)
                self.assertEqual(windup.counter, old_counter + 1)
                self.assertGreater(windup.start, old_start)
                self.assertLess(component.now, old_start + 2000)
                while component.now <= old_start + 2000:
                    run.step({})
                    self.assertFalse(fight.rockets)
                shot = self.wait_for_sniper_shot(run)
                self.assertEqual(shot.direct.amount, 100)
                sniper.firing = False
                for _ in range(12):
                    run.step({})
                self.assertEqual(self.vitals(run, soldier).total, 100)

    def test_soldier_rifle_kills_sniper_before_or_after_each_slot_launch(self):
        for graph_id, windup_pos, amount in ((0x1122, 53, 100), (0x1134, 35, 5)):
            for launched in (False, True):
                with self.subTest(graph=graph_id, launched=launched):
                    run, soldier, sniper = self.arena("3", (0, 0, 18))
                    soldier.mover.position = [0, 0, -3]
                    soldier.mover.body = None
                    fight = run.match.combat
                    own, target = self.vitals(run, soldier), self.vitals(run, sniper)
                    target.damage(target.total - 20)  # Isolate the lethal rifle hit.
                    component = sniper.body_script.component
                    if graph_id == 0x1134:
                        component.edges([(16, True), (16, False)])
                    weapon = next(i for i in component.instances.values() if i.graph.index == graph_id)
                    run.step({})
                    self.assertEqual(npc_control.start(run.match, soldier), sniper.entity)
                    if launched:
                        shot = self.wait_for_sniper_shot(run)
                        self.assertEqual(shot.direct.amount, amount)
                    else:
                        run.step({})
                        self.assertTrue(weapon.states[weapon.graph.node(windup_pos).state].active)
                        self.assertFalse(fight.rockets)
                    angles = aim(fight._eye(soldier), (0, 0.8, 18))
                    run.step({soldier: (*angles, 1)})
                    self.assertTrue(sniper.dead)
                    self.assertTrue(component.owner_dead)
                    self.assertEqual(target.killer, soldier.body)
                    self.assertEqual(own.total, 200)
                    if launched:
                        self.assertIn(shot, fight.rockets)
                        self.assertEqual(shot.source_entity, sniper.entity)
                    for _ in range(145):
                        run.step({})
                    self.assertFalse(fight.rockets)
                    self.assertEqual(own.total, 200 - amount if launched else 200)

    def test_actual_sniper_wound_field_charge_unlocks_visor_hit_and_expiry(self):
        run, soldier, sniper = self.arena("3", (3, 0, 5))
        soldier.mover.position = [0, 0, -3]
        soldier.mover.body = None
        script = soldier.body_script
        script.ult_seconds = 0
        charge = script.component.entity_var(342)
        cost = script.component.entity_var(338).value()
        script.set_ult_charge(cost - 50)
        soldier_pool = self.vitals(run, soldier)
        sniper_pool = self.vitals(run, sniper)
        sniper_health = sniper_pool.total

        run.step({soldier: (0, 0, 0x20)})
        self.assertEqual(charge.value(), cost - 50)
        run.step({soldier: (0, 0, 1)})
        self.assertEqual(sniper_pool.total, sniper_health)
        sniper.attack_target, sniper.firing = soldier.entity, True
        for _ in range(180):
            run.step({})
            if soldier_pool.total < 200:
                break
        self.assertEqual(soldier_pool.total, 100)
        sniper.firing = False

        run.step({soldier: (0, 0, 16)})
        for _ in range(260):
            run.step({})
        self.assertEqual(soldier_pool.total, 200)
        self.assertEqual(charge.value(), cost)
        run.step({soldier: (0, 0, 0x20)})
        self.assertEqual(charge.value(), 0)
        for _ in range(80):
            run.step({})
        run.step({soldier: (0, 0, 1)})
        self.assertEqual(sniper_pool.total, sniper_health - 20)
        self.assertEqual(charge.value(), 0)
        for _ in range(400):
            run.step({})
        ammo = script.component.instances[11].vars[53]
        rounds = ammo.value()
        run.step({soldier: (0, 0, 1)})
        self.assertEqual(ammo.value(), rounds - 1)
        self.assertEqual(sniper_pool.total, sniper_health - 20)

    def test_lethal_enemy_input_clears_visor_locks_in_owner_frames(self):
        for key in (*self.catalog, None):
            with self.subTest(attacker=self.catalog[key].name if key else "enemy player"):
                if key:
                    position = (0, 0, -1 if key == "4" else 3)
                    run, soldier, enemy = self.arena(key, position)
                    enemy_body = enemy.entity
                else:
                    run, soldier, enemy = soldier_fixture.SoldierCombatLaneTests.arena(self)
                    enemy.mover.position = [0, 0, 3]
                    enemy.mover.body = None
                    enemy_body = enemy.body
                soldier.mover.position = [0, 0, -3]
                soldier.mover.body = None
                script = soldier.body_script
                script.ult_seconds = 0
                script.set_ult_charge(script.component.entity_var(338).value())
                run.step({soldier: (0, 0, 0x20)})
                for _ in range(80):
                    run.step({})
                visor = next(i for i in script.component.instances.values() if i.graph.index == 0x0259)
                search = visor.states[7]
                client = ClientBody.for_hero(soldier.hero.guid)
                old_body = soldier.body
                stream = BodyStream(old_body)
                update = stream.full(*script.tree_frame())
                client.apply(update)
                stream.arrived(update.chunk_last)
                self.assertTrue(search.active)
                self.assertEqual(client.vars[(0, 2337)], (expr.Entity(enemy_body),))
                self.assertEqual(client.vars[(0, 3544)], expr.Entity(enemy_body))

                fight = run.match.combat
                own = self.vitals(run, soldier)
                own.damage(own.total - 1)
                if key:
                    x, y, z = enemy.position
                    angles = aim(fight._eye(soldier), (x, y + .8, z))
                    run.step({soldier: (*angles, 0)})
                    self.assertEqual(npc_control.start(run.match, soldier), enemy_body)
                    for _ in range(180):
                        run.step({})
                        if own.dead:
                            break
                    self.assertGreater(npc_control.stop(run.match), 0)
                else:
                    x, y, z = soldier.mover.position
                    angles = aim(fight._eye(enemy), (x, y + .8, z))
                    for _ in range(45):
                        run.step({enemy: (*angles, 1)})
                        if own.dead:
                            break
                self.assertTrue(own.dead)
                self.assertEqual(own.killer, enemy_body)
                self.assertFalse(visor.states[6].active)
                self.assertFalse(search.active)
                self.assertIsNone(search.last_refresh)

                payload, keys, snapshot = script.delta(stream.base, stream.touched(), script.frame, False)
                update = stream.delta(payload, snapshot, keys, script.frame)
                client.apply(update)
                stream.arrived(update.chunk_last)
                self.assertIsNone(client.vars[(0, 2337)])
                self.assertIsNone(client.vars[(0, 3544)])

                ammo = script.component.instances[11].vars[53]
                rounds, before = ammo.value(), self.vitals(run, enemy).total
                run.step({soldier: (0, 0, 1 | 0x20)})
                self.assertEqual(ammo.value(), rounds)
                self.assertEqual(self.vitals(run, enemy).total, before)
                due, = (due for due, owner in fight.respawns if owner is soldier)
                run.frame = math.ceil(due / combat.TICK) + 1
                run.match.update(run.frame * combat.TICK, run.frame)
                self.assertNotEqual(soldier.body, old_body)
                self.assertEqual(self.vitals(run, soldier).total, 200)
                soldier.body_script.ult_seconds = 0
                run.step({soldier: (0, 0, 0x20)})
                fresh = soldier.body_script.component
                self.assertEqual(fresh.entity_var(342).value(), 0)
                for identifier in (2337, 3544):
                    var = fresh.entity_var(identifier, create=False)
                    self.assertIsNone(var.value() if var is not None else None)

    def test_actual_heavy_assault_kills_soldier_during_visor(self):
        run, soldier, heavy = self.arena("5", (3, 0, 5))
        soldier.mover.position = [0, 0, -3]
        soldier.mover.body = None
        script = soldier.body_script
        script.ult_seconds = 0
        script.set_ult_charge(script.component.entity_var(338).value())
        run.step({soldier: (0, 0, 0x20)})
        for _ in range(80):
            run.step({})
        fight = run.match.combat
        visor = next(state for instance in soldier.body_script.component.instances.values()
                     if instance.graph.index == 0x0259
                     for state in instance.states.values()
                     if isinstance(state, TrackTargets) and state.active)
        heavy_pool = self.vitals(run, heavy)
        initial = heavy_pool.total
        run.step({soldier: (0, 0, 1)})
        self.assertLess(heavy_pool.total, initial)
        heavy.attack_target, heavy.firing = soldier.entity, True
        for _ in range(270):
            self.assertTrue(visor.active)
            run.step({})
            if fight.is_dead(soldier):
                break
        self.assertTrue(fight.is_dead(soldier), self.vitals(run, soldier).total)
        self.assertEqual(self.vitals(run, soldier).killer, heavy.entity)
        self.assertFalse(visor.active)
        heavy.firing = False
        surviving_health = heavy_pool.total
        run.step({soldier: (0, 0, 1 | 2 | 0x10 | 0x20 | 0x800)})
        for _ in range(45):
            run.step({})
        self.assertEqual(heavy_pool.total, surviving_health)
        self.assertFalse(any(shot.player is soldier for shot in fight.projectiles.shots))

        old_body = soldier.body
        due, = [due for due, player in fight.respawns if player is soldier]
        run.frame = math.ceil(due / combat.TICK) + 1
        run.match.update(run.frame * combat.TICK, run.frame)
        self.assertNotEqual(soldier.body, old_body)
        self.assertFalse(fight.is_dead(soldier))
        self.assertEqual(self.vitals(run, soldier).total, 200)
        self.assertEqual(soldier.body_script.component.entity_var(342).value(), 0)
        soldier.mover.position = [0, 0, -3]
        soldier.mover.body = None
        run.step({})
        ammo = soldier.body_script.component.instances[11].vars[53]
        rounds = ammo.value()
        run.step({soldier: (0, 0, 1)})
        self.assertEqual(ammo.value(), rounds - 1)
        self.assertEqual(heavy_pool.total, surviving_health)
        for _ in range(15):
            run.step({})
        x, y, z = heavy.position
        angles = aim(fight._eye(soldier), (x, y + 0.8, z))
        run.step({soldier: (*angles, 1)})
        self.assertEqual(ammo.value(), rounds - 2)
        self.assertLess(heavy_pool.total, surviving_health)

    def test_ana_rifle_and_soldier_field_share_health_on_all_five_npc_bodies(self):
        for key, definition in self.catalog.items():
            for scoped in (False, True):
                with self.subTest(npc=definition.name, scoped=scoped):
                    run, soldier, actor = self.arena(key)
                    soldier.body_script.ult_seconds = 0
                    fight = run.match.combat
                    ana_hero = heroes()[0x02E000000000013B]
                    ana = run.match.add_player(2, "Ana", ana_hero.guid, soldier.team, False)
                    in_world(run.match, ana)
                    run.players.append(ana)
                    self.assertTrue(run.match.switch_hero(ana, ana_hero))
                    soldier.mover.position = list(soldier.mover.position)
                    soldier.mover.body = None
                    ana.mover.position = [2, 0, -3]
                    ana.mover.body = None
                    run.step({})
                    pool = self.vitals(run, actor)
                    initial = pool.total
                    point = (0, .8, 1)
                    angles = aim(fight._eye(soldier), point)
                    run.step({soldier: (*angles, 1)})
                    run.step({})
                    after_soldier = pool.total
                    self.assertLess(after_soldier, initial)
                    angles = aim(fight._eye(ana), point)
                    if scoped:
                        for _ in range(35):
                            run.step({ana: (*angles, 2)})
                    for healing in (False, True):
                        run.step({ana: (*angles, 3 if scoped else 1)})
                        for _ in range(52):
                            run.step({ana: (*angles, 2 if scoped else 0)})
                        self.assertFalse(fight.biotic_doses)
                        self.assertEqual(pool.total, after_soldier if healing else after_soldier - 70)
                        self.assertEqual(actor.body_script.component.vars[OWNER_HEALTH].value(), pool.total)
                        actor.team = soldier.team
                    self.assertEqual(ana.stats.value(stats.HEALING), 70)
                    self.assertEqual(ana.stats.value(stats.HERO_DAMAGE), 70)
                    charge = soldier.body_script.component.entity_var(342)
                    before_field = charge.value()
                    run.step({soldier: (0, 0, 16)})
                    for _ in range(120):
                        run.step({})
                    self.assertEqual(pool.total, initial)
                    self.assertEqual(actor.body_script.component.vars[OWNER_HEALTH].value(), initial)
                    self.assertAlmostEqual(
                        charge.value() - before_field, initial - after_soldier, delta=0.0001,
                    )
                    self.assertEqual(pool.ult_healing_deficit, 0)

    def test_rifle_melee_helix_and_visor_deal_authored_damage_to_all_five_bodies(self):
        for key, definition in self.catalog.items():
            for weapon, damage in (("rifle", 20), ("melee", 30), ("helix", 120), ("visor", 20)):
                with self.subTest(npc=definition.name, weapon=weapon):
                    position = (3.0, 0.0, 3.0) if weapon == "visor" else (0.0, 0.0, -1.5)
                    run, soldier, enemy = self.arena(key, position)
                    pool = self.vitals(run, enemy)
                    before = pool.total
                    yaw, pitch = aim(run.match.combat._eye(soldier),
                                     (position[0], 0.8, position[2]))
                    if weapon == "visor":
                        self.start_visor(run, soldier)
                        self.input(run, soldier, 1)
                    else:
                        self.input(run, soldier, {"rifle": 1, "melee": 0x800, "helix": 2}[weapon], yaw, pitch)
                    for _ in range(25):
                        self.input(run, soldier)
                    self.assertEqual(before - pool.total, damage)
                    if definition.shields:
                        self.assertLess(pool.now[2], definition.shields)
                        self.assertEqual(pool.now[1], 0)  # variable 221 is NOT armour for these NPCs

    def test_rifle_critical_falloff_and_max_range_on_each_pve_body(self):
        for key, definition in self.catalog.items():
            profile = mover.mover_data(definition.body)
            for hit_height, multiplier in ((0.8, 1), (1.4, 2)):
                # Horizontal shots pass through the chosen capsule height.
                # Offset the axis so the first surface contact is at the
                # specified distance, including the upper spherical cap.
                axis_height = max(profile.stand_radius, profile.stand_height - profile.stand_radius)
                above_axis = max(0.0, hit_height - axis_height)
                surface_offset = math.sqrt(profile.stand_radius ** 2 - above_axis ** 2)
                for distance, base_damage in ((29.75, 20), (30.25, 19.825), (40, 13),
                                              (49.75, 6.175), (50.25, 6), (179.75, 6), (180.25, 0)):
                    with self.subTest(npc=definition.name, distance=distance, multiplier=multiplier):
                        position = (0, 1.55 - hit_height, -3 + distance + surface_offset)
                        run, soldier, actor = self.arena(key, position)
                        script = soldier.body_script
                        script.ult_seconds = 0
                        ammo = script.component.instances[11].vars[53]
                        charge = script.component.entity_var(342)
                        pool = self.vitals(run, actor)
                        initial = pool.total
                        before_charge = charge.value()
                        damage = base_damage * multiplier
                        self.assertEqual(run.match.combat._eye(soldier), (0.0, 1.55, -3.0))

                        self.input(run, soldier, 1)

                        self.assertAlmostEqual(initial - pool.total, damage, places=5)
                        self.assertEqual(actor.body_script.component.vars[OWNER_HEALTH].value(), pool.total)
                        self.assertEqual(ammo.value(), 29)
                        self.assertAlmostEqual(charge.value() - before_charge, damage, places=5)
                        self.assertAlmostEqual(soldier.stats.value(stats.HERO_DAMAGE), damage, places=5)
                        notices = soldier.client.messages(combat.HIT)
                        expected = [{
                            "+0x0": {"+0x0": actor.entity},
                            "+0x4": round(damage),
                            "+0x8": combat.CRITICAL if multiplier == 2 else 0,
                        }] if damage else []
                        self.assertEqual([notice["+0x78"] for notice in notices], expected)

    def test_heavy_assault_kills_before_manual_reload_refill(self):
        run, soldier, heavy = self.arena("5", (3, 0, 5))
        soldier.mover.position = [0, 0, -3]
        soldier.mover.body = None
        fight = run.match.combat
        pool = self.vitals(run, soldier)
        old_body = soldier.body
        rifle = soldier.body_script.component.instances[11]
        ammo = rifle.vars[53]
        run.step({soldier: (16384, 0, 1)})
        self.assertEqual(ammo.value(), 29)
        heavy.attack_target, heavy.firing = soldier.entity, True
        for _ in range(300):
            run.step({})
            if pool.total <= 5:
                break
        self.assertEqual(pool.total, 5)
        run.step({soldier: (0, 0, 0x400)})
        self.assertTrue(rifle.vars[51].value())
        for _ in range(30):
            run.step({})
            if fight.is_dead(soldier):
                break
        self.assertTrue(fight.is_dead(soldier))
        self.assertEqual(pool.killer, heavy.entity)
        self.assertFalse(rifle.vars[51].value())
        heavy.firing = False
        for _ in range(100):
            run.step({soldier: (0, 0, 0x400 | 1)})
        self.assertEqual(ammo.value(), 29)
        due, = [due for due, player in fight.respawns if player is soldier]
        run.frame = math.ceil(due / combat.TICK) + 1
        run.match.update(run.frame * combat.TICK, run.frame)
        self.assertNotEqual(soldier.body, old_body)
        new_ammo = soldier.body_script.component.instances[11].vars[53]
        self.assertEqual(new_ammo.value(), 30)
        soldier.mover.position = [0, 0, -3]
        soldier.mover.body = None
        run.step({})
        target_pool = self.vitals(run, heavy)
        initial = target_pool.total
        yaw, pitch = aim(fight._eye(soldier), (3, 0.8, 5))
        run.step({soldier: (yaw, pitch, 1)})
        self.assertEqual(new_ammo.value(), 29)
        self.assertEqual(target_pool.total, initial - 20)
        self.assertEqual(ammo.value(), 29)

    def test_melee_and_helix_preserve_ammo_when_interrupting_reload_after_refill(self):
        for key, definition in self.catalog.items():
            for buttons, damage in ((0x800, 30), (2, 120)):
                with self.subTest(npc=definition.name, buttons=buttons):
                    run, soldier, actor = self.arena(key, (0, 0, -1.5))
                    pool = self.vitals(run, actor)
                    initial = pool.total
                    yaw, pitch = aim(run.match.combat._eye(soldier), (0, 0.8, -1.5))
                    rifle = soldier.body_script.component.instances[11]
                    ammo = rifle.vars[53]
                    self.input(run, soldier, 1, yaw=16384)
                    self.assertEqual(ammo.value(), 29)
                    for _ in range(40):
                        self.input(run, soldier)
                    self.input(run, soldier, 0x400)
                    self.assertTrue(rifle.vars[51].value())
                    for _ in range(60):
                        self.input(run, soldier)
                        if ammo.value() == 30:
                            break
                    self.assertEqual(ammo.value(), 30)
                    self.assertTrue(rifle.vars[51].value())
                    self.input(run, soldier, buttons, yaw, pitch)
                    for _ in range(30):
                        self.input(run, soldier)
                    self.assertFalse(rifle.vars[51].value())
                    self.assertEqual(ammo.value(), 30)
                    self.assertEqual(pool.total, initial - damage)
                    for _ in range(45):
                        self.input(run, soldier, 1, yaw, pitch)
                        if ammo.value() < 30:
                            break
                    self.assertEqual(ammo.value(), 29)
                    self.assertEqual(pool.total, initial - damage - 20)

    def test_sprint_interrupts_manual_reload_then_rifle_hits_all_five_pve_bodies(self):
        for key, definition in self.catalog.items():
            with self.subTest(npc=definition.name):
                run, soldier, actor = self.arena(key, (0, 0, 15))
                soldier.mover.position = [0, 0, -3]
                soldier.mover.body = None
                pool = self.vitals(run, actor)
                initial = pool.total
                rifle = soldier.body_script.component.instances[11]
                ammo = rifle.vars[53]
                self.move(run, soldier, buttons=1, forward=0, target=(10, 0.8, -3))
                self.assertEqual(ammo.value(), 29)
                for _ in range(40):
                    speed = self.move(run, soldier)
                self.assertAlmostEqual(speed, 5.5, delta=0.01)
                self.move(run, soldier, buttons=0x400)
                self.assertTrue(rifle.vars[51].value())
                self.move(run, soldier, buttons=8)
                for _ in range(10):
                    speed = self.move(run, soldier)
                self.assertAlmostEqual(speed, 8.25, delta=0.01)
                self.assertFalse(rifle.vars[51].value())
                for _ in range(100):
                    self.move(run, soldier, forward=0)
                self.assertEqual(ammo.value(), 29)
                self.assertEqual(pool.total, initial)
                self.move(run, soldier, buttons=1, forward=0, target=(0, 0.8, 15))
                self.assertEqual(ammo.value(), 28)
                self.assertEqual(pool.total, initial - 20)

    def test_melee_interrupts_manual_reload_on_all_five_pve_bodies(self):
        for key, definition in self.catalog.items():
            with self.subTest(npc=definition.name):
                run, soldier, actor = self.arena(key, (0, 0, -1.5))
                pool = self.vitals(run, actor)
                initial = pool.total
                yaw, pitch = aim(run.match.combat._eye(soldier), (0, 0.8, -1.5))
                rifle = soldier.body_script.component.instances[11]
                ammo = rifle.vars[53]
                self.input(run, soldier, 1, yaw=16384)
                self.assertEqual(ammo.value(), 29)
                for _ in range(40):
                    self.input(run, soldier)
                self.input(run, soldier, 0x400)
                self.assertTrue(rifle.vars[51].value())
                self.input(run, soldier, 0x800, yaw, pitch)
                for _ in range(100):
                    self.input(run, soldier)
                self.assertFalse(rifle.vars[51].value())
                self.assertEqual(ammo.value(), 29)
                self.assertEqual(pool.total, initial - 30)
                self.input(run, soldier, 1, yaw, pitch)
                self.assertEqual(ammo.value(), 28)
                self.assertEqual(pool.total, initial - 50)

    def test_field_interrupts_manual_reload_and_heals_all_five_pve_bodies(self):
        for key, definition in self.catalog.items():
            with self.subTest(npc=definition.name):
                run, soldier, actor = self.arena(key, team=0)
                pool = self.vitals(run, actor)
                initial = pool.total
                pool.damage(25)  # Fixture wound isolates actual Field healing.
                rifle = soldier.body_script.component.instances[11]
                self.input(run, soldier, 1, yaw=16384)
                self.assertEqual(rifle.vars[53].value(), 29)
                for _ in range(40):
                    self.input(run, soldier)
                self.input(run, soldier, 0x400)
                self.assertTrue(rifle.vars[51].value())
                self.input(run, soldier, 0x10)
                for _ in range(140):
                    self.input(run, soldier)
                self.assertFalse(rifle.vars[51].value())
                self.assertEqual(rifle.vars[53].value(), 29)
                self.assertEqual(pool.total, initial)
                self.assertAlmostEqual(soldier.stats.value(stats.HEALING), 25)
                actor.team = 1
                yaw, pitch = aim(run.match.combat._eye(soldier), (0, 0.8, 1))
                self.input(run, soldier, 1, yaw, pitch)
                self.assertEqual(rifle.vars[53].value(), 28)
                self.assertEqual(pool.total, initial - 20)

    def test_helix_and_rifle_recover_after_reload_input_on_all_five_pve_bodies(self):
        for key, definition in self.catalog.items():
            with self.subTest(npc=definition.name):
                run, soldier, actor = self.arena(key, (0, 0, 5))
                fight = run.match.combat
                pool = self.vitals(run, actor)
                initial = pool.total
                yaw, pitch = aim(fight._eye(soldier), (0, 0.8, 5))
                rifle = soldier.body_script.component.instances[11]
                self.input(run, soldier, 1, yaw=16384)
                self.assertEqual(rifle.vars[53].value(), 29)
                for _ in range(40):
                    self.input(run, soldier)
                self.input(run, soldier, 0x400)
                self.assertTrue(rifle.vars[51].value())
                self.input(run, soldier, 2, yaw, pitch)
                self.assertTrue(fight.rockets)
                for _ in range(30):
                    self.input(run, soldier, 0, yaw, pitch)
                self.assertEqual(pool.total, initial - 120)
                self.assertEqual(rifle.vars[53].value(), 29)
                self.assertFalse(rifle.vars[51].value())
                self.input(run, soldier, 1, yaw, pitch)
                self.assertEqual(rifle.vars[53].value(), 28)
                self.assertEqual(pool.total, initial - 140)
                for _ in range(100):
                    self.input(run, soldier)
                self.assertEqual(rifle.vars[53].value(), 28)
                self.input(run, soldier, 0x400)
                self.assertTrue(rifle.vars[51].value())
                for _ in range(100):
                    self.input(run, soldier)
                self.assertFalse(rifle.vars[51].value())
                self.assertEqual(rifle.vars[53].value(), 30)

    def test_visor_activation_during_manual_reload_hits_all_five_pve_bodies(self):
        for key, definition in self.catalog.items():
            with self.subTest(npc=definition.name):
                run, soldier, actor = self.arena(key, (3, 0, 3))
                script = soldier.body_script
                script.ult_seconds = 0
                cost = script.component.entity_var(338).value()
                script.set_ult_charge(cost - 1)
                rifle = script.component.instances[11]
                ammo = rifle.vars[53]
                pool = self.vitals(run, actor)
                initial = pool.total
                self.input(run, soldier, 1, yaw=16384)
                self.assertEqual(ammo.value(), 29)
                for _ in range(40):
                    self.input(run, soldier)
                self.input(run, soldier, 0x400)
                self.assertTrue(rifle.vars[51].value())
                self.input(run, soldier, 0x20)
                self.assertTrue(rifle.vars[51].value())
                self.assertEqual(ammo.value(), 29)
                self.assertEqual(script.component.vars[342].value(), cost - 1)
                self.input(run, soldier)
                script.set_ult_charge(cost)
                self.input(run, soldier, 0x20)
                self.assertEqual(script.component.vars[342].value(), 0)
                self.assertFalse(rifle.vars[51].value())
                for _ in range(80):
                    self.input(run, soldier)
                self.assertEqual(ammo.value(), 30)
                self.assertEqual(pool.total, initial)
                self.input(run, soldier, 1)
                self.assertEqual(ammo.value(), 29)
                self.assertEqual(pool.total, initial - 20)

    def test_helix_fires_during_empty_magazine_reload_on_all_five_pve_bodies(self):
        for key, definition in self.catalog.items():
            with self.subTest(npc=definition.name):
                run, soldier, actor = self.arena(key, (0, 0, 5))
                fight = run.match.combat
                pool = self.vitals(run, actor)
                initial = pool.total
                rifle = soldier.body_script.component.instances[11]
                ammo = rifle.vars[53]
                for _ in range(225):
                    self.input(run, soldier, 1, yaw=16384)
                    if rifle.vars[51].value():
                        break
                self.assertTrue(rifle.vars[51].value())
                self.assertEqual(ammo.value(), 0)
                self.assertEqual(pool.total, initial)
                yaw, pitch = aim(fight._eye(soldier), (0, 0.8, 5))
                reload_anim = rifle.states[39]
                start = reload_anim.start
                self.input(run, soldier, 2, yaw, pitch)
                self.assertTrue(fight.rockets)
                self.assertTrue(rifle.vars[51].value())
                self.assertEqual(reload_anim.start, start)
                for _ in range(30):
                    self.input(run, soldier)
                self.assertEqual(ammo.value(), 0)
                self.assertEqual(pool.total, initial - 120)
                for _ in range(160):
                    self.input(run, soldier, 1, yaw, pitch)
                    if pool.total < initial - 120:
                        break
                self.assertEqual(ammo.value(), 29)
                self.assertEqual(pool.total, initial - 140)

    def test_melee_during_empty_magazine_reload_on_all_five_pve_bodies(self):
        for key, definition in self.catalog.items():
            with self.subTest(npc=definition.name):
                run, soldier, actor = self.arena(key, (0, 0, -1.5))
                pool = self.vitals(run, actor)
                initial = pool.total
                rifle = soldier.body_script.component.instances[11]
                ammo = rifle.vars[53]
                for _ in range(225):
                    self.input(run, soldier, 1, yaw=16384)
                    if rifle.vars[51].value():
                        break
                self.assertTrue(rifle.vars[51].value())
                self.assertEqual(ammo.value(), 0)
                self.assertEqual(pool.total, initial)
                yaw, pitch = aim(run.match.combat._eye(soldier), (0, 0.8, -1.5))
                self.input(run, soldier, 0x800, yaw, pitch)
                self.assertFalse(rifle.vars[51].value())
                for _ in range(30):
                    self.input(run, soldier)
                self.assertEqual(ammo.value(), 0)
                self.assertEqual(pool.total, initial - 30)
                for _ in range(160):
                    self.input(run, soldier, 1, yaw, pitch)
                    if pool.total < initial - 30:
                        break
                self.assertEqual(ammo.value(), 29)
                self.assertEqual(pool.total, initial - 50)

    def test_sprint_interrupts_empty_magazine_reload_on_all_five_pve_bodies(self):
        for key, definition in self.catalog.items():
            with self.subTest(npc=definition.name):
                run, soldier, actor = self.arena(key, (0, 0, 15))
                soldier.mover.position = [0, 0, -3]
                soldier.mover.body = None
                pool = self.vitals(run, actor)
                initial = pool.total
                rifle = soldier.body_script.component.instances[11]
                ammo = rifle.vars[53]
                for _ in range(225):
                    self.move(run, soldier, buttons=1, forward=0, target=(10, 0.8, -3))
                    if rifle.vars[51].value():
                        break
                self.assertTrue(rifle.vars[51].value())
                self.assertEqual(ammo.value(), 0)
                for _ in range(8):
                    self.move(run, soldier)
                self.move(run, soldier, buttons=8)
                for _ in range(10):
                    speed = self.move(run, soldier)
                self.assertAlmostEqual(speed, 8.25, delta=0.01)
                self.assertFalse(rifle.vars[51].value())
                self.assertEqual(ammo.value(), 0)
                self.assertEqual(pool.total, initial)
                self.move(run, soldier, forward=0)
                for _ in range(160):
                    self.move(run, soldier, buttons=1, forward=0, target=(0, 0.8, 15))
                    if pool.total < initial:
                        break
                self.assertEqual(ammo.value(), 29)
                self.assertEqual(pool.total, initial - 20)

    def test_field_during_empty_magazine_reload_on_all_five_pve_bodies(self):
        for key, definition in self.catalog.items():
            with self.subTest(npc=definition.name):
                run, soldier, actor = self.arena(key, team=0)
                pool = self.vitals(run, actor)
                initial = pool.total
                pool.damage(25)  # Fixture wound for the allied Field recipient.
                rifle = soldier.body_script.component.instances[11]
                ammo = rifle.vars[53]
                for _ in range(225):
                    self.input(run, soldier, 1, yaw=16384)
                    if rifle.vars[51].value():
                        break
                self.assertTrue(rifle.vars[51].value())
                self.assertEqual(ammo.value(), 0)
                self.input(run, soldier, 0x10)
                self.assertFalse(rifle.vars[51].value())
                for _ in range(140):
                    self.input(run, soldier)
                self.assertEqual(pool.total, initial)
                self.assertAlmostEqual(soldier.stats.value(stats.HEALING), 25)
                actor.team = 1
                yaw, pitch = aim(run.match.combat._eye(soldier), (0, 0.8, 1))
                for _ in range(160):
                    self.input(run, soldier, 1, yaw, pitch)
                    if pool.total < initial:
                        break
                self.assertEqual(ammo.value(), 29)
                self.assertEqual(pool.total, initial - 20)

    def test_visor_cancels_active_empty_reload_and_hits_all_five_pve_bodies(self):
        for key, definition in self.catalog.items():
            with self.subTest(npc=definition.name):
                run, soldier, actor = self.arena(key, (3, 0, 3))
                script = soldier.body_script
                script.ult_seconds = 0
                script.set_ult_charge(script.component.entity_var(338).value())
                charge = script.component.entity_var(342)
                rifle = script.component.instances[11]
                ammo = rifle.vars[53]
                pool = self.vitals(run, actor)
                initial = pool.total
                for _ in range(225):
                    self.input(run, soldier, 1, yaw=16384)
                    if rifle.vars[51].value():
                        break
                self.assertTrue(rifle.vars[51].value())
                self.assertEqual(ammo.value(), 0)
                self.assertEqual(pool.total, initial)
                self.input(run, soldier, 0x20)
                self.assertEqual(charge.value(), 0)
                self.assertFalse(rifle.vars[51].value())
                for _ in range(80):
                    self.input(run, soldier)
                    self.assertFalse(rifle.vars[51].value())
                self.assertEqual(ammo.value(), 30)
                self.input(run, soldier, 1)
                self.assertEqual(ammo.value(), 29)
                self.assertEqual(pool.total, initial - 20)

    def test_visor_empty_magazine_reload_and_resume_on_all_five_pve_bodies(self):
        for key, definition in self.catalog.items():
            with self.subTest(npc=definition.name):
                run, soldier, actor = self.arena(key, (3, 0, 3))
                self.start_visor(run, soldier)
                pool = self.vitals(run, actor)
                initial = pool.total
                rifle = soldier.body_script.component.instances[11]
                ammo = rifle.vars[53]
                self.assertEqual(ammo.value(), 30)
                for _ in range(210):
                    self.input(run, soldier, 1, yaw=16384)
                    if ammo.value() == 0:
                        break
                self.assertEqual(ammo.value(), 0)
                self.assertEqual(pool.total, initial)
                self.input(run, soldier)
                self.input(run, soldier, 1)
                self.assertEqual(ammo.value(), 0)
                self.assertEqual(pool.total, initial)
                for _ in range(100):
                    self.input(run, soldier)
                    if ammo.value() == 30:
                        break
                self.assertEqual(ammo.value(), 30)
                self.assertEqual(pool.total, initial)
                self.assertTrue(rifle.vars[51].value())
                self.input(run, soldier, 1)
                self.assertEqual(ammo.value(), 30)
                for _ in range(75):
                    self.input(run, soldier)
                    if not rifle.vars[51].value():
                        break
                self.assertFalse(rifle.vars[51].value())
                for _ in range(20):
                    self.input(run, soldier, 1)
                    if ammo.value() < 30:
                        break
                self.assertEqual(ammo.value(), 29)
                self.assertEqual(pool.total, initial - 20)

    def test_visor_retargets_after_killing_each_retained_pve_body(self):
        for key, definition in self.catalog.items():
            with self.subTest(npc=definition.name):
                run, soldier, first = self.arena(key, (1.5, 0, 3))
                soldier.mover.position = [0, 0, -3]
                soldier.mover.body = None
                second = npc.spawn(run.match, self.catalog["1"], (-3, 0, 4), team=1)
                self.assertIsNotNone(second.body_script)
                first_pool = self.vitals(run, first)
                second_pool = self.vitals(run, second)
                first_pool.damage(first_pool.total - 1)  # Fixture wound isolates the lethal Visor hit.
                second_health = second_pool.total
                script = soldier.body_script
                script.ult_seconds = 0
                script.set_ult_charge(script.component.entity_var(338).value())
                run.step({soldier: (0, 0, 0x20)})
                for _ in range(80):
                    run.step({})
                visor = next(i for i in script.component.instances.values() if i.graph.index == 0x0259)
                first_id, second_id = expr.Entity(first.entity), expr.Entity(second.entity)
                locks = visor.find_var(1, 2337)
                self.assertEqual(locks.value(), (first_id, second_id))
                run.step({soldier: (0, 0, 1)})
                self.assertTrue(first.dead)
                self.assertTrue(first.retain_corpse)
                self.assertTrue(first.body_script.component.owner_dead)
                self.assertEqual(first_pool.killer, soldier.body)
                self.assertEqual(second_pool.total, second_health)
                for _ in range(15):
                    run.step({})
                self.assertIn(first, run.match.bots)
                self.assertEqual(locks.value(), (second_id,))
                self.assertEqual(visor.find_var(1, 3544).value(), second_id)
                ammo = soldier.body_script.component.instances[11].vars[53]
                rounds = ammo.value()
                run.step({soldier: (0, 0, 1)})
                self.assertEqual(ammo.value(), rounds - 1)
                self.assertEqual(first_pool.total, 0)
                self.assertEqual(second_pool.total, second_health - 20)

    def test_visor_projected_bounds_and_distance_rank_each_pve_body_over_far_player(self):
        for key, definition in self.catalog.items():
            with self.subTest(npc=definition.name):
                # The close body misses the raw ray by 2 cm, but its projected edge
                # is close enough for the distance term to outweigh the difference.
                radius = mover.mover_data(definition.body).stand_radius
                run, soldier, near = self.arena(key, (radius + .02, 0, 3))
                soldier.mover.position = [0, 0, -3]
                soldier.mover.body = None
                far = run.match.add_player(2, "Far", soldier.hero.guid, 1, False)
                in_world(run.match, far)
                far.client = NpcRecordingClient(far)
                run.players.append(far)
                self.assertTrue(run.match.switch_hero(far, soldier.hero))
                far.mover.position = [0, 0, 15]
                far.mover.body = None
                near_pool, far_pool = (self.vitals(run, actor) for actor in (near, far))
                near_health, far_health = near_pool.total, far_pool.total
                script = soldier.body_script
                script.ult_seconds = 0
                angles = aim(run.match.combat._eye(soldier), (0, .8, 15))
                run.step({soldier: (*angles, 1)})
                self.assertEqual(near_pool.total, near_health)
                self.assertLess(far_pool.total, far_health)
                far_health = far_pool.total

                script.set_ult_charge(script.component.vars[338].value())
                run.step({soldier: (*angles, 0x20)})
                for _ in range(80):
                    run.step({soldier: (*angles, 0)})
                visor = next(i for i in script.component.instances.values() if i.graph.index == 0x0259)
                near_id, far_id = expr.Entity(near.entity), expr.Entity(far.body)
                self.assertEqual(visor.find_var(1, 2337).value(), (near_id, far_id))
                self.assertEqual(visor.find_var(1, 3544).value(), near_id)
                run.step({soldier: (*angles, 1)})
                self.assertEqual(near_pool.total, near_health - 20)
                self.assertEqual(far_pool.total, far_health)

    def test_live_visor_lock_list_ranks_five_npcs_and_player_and_clears_in_owner_stream(self):
        run, soldier, first = self.arena("1", (1.8, 0, 9))
        soldier.mover.position = list(soldier.mover.position)
        soldier.mover.body = None
        actors = [first]
        for key, x in (("2", 2.8), ("3", 4), ("4", 5.5), ("5", 7)):
            actors.append(npc.spawn(run.match, self.catalog[key], (x, 0, 9), team=1))
        enemy = run.match.add_player(2, "Enemy", soldier.hero.guid, 1, False)
        in_world(run.match, enemy)
        enemy.client = NpcRecordingClient(enemy)
        run.players.append(enemy)
        self.assertTrue(run.match.switch_hero(enemy, soldier.hero))
        enemy.mover.position = [-3.4, 0, 9]
        enemy.mover.body = None
        script = soldier.body_script
        visor = next(i for i in script.component.instances.values() if i.graph.index == 0x0259)
        client = ClientBody.for_hero(soldier.hero.guid)
        stream = BodyStream(soldier.body)
        update = stream.full(*script.tree_frame())
        client.apply(update)
        stream.arrived(update.chunk_last)

        def publish(expected, first_target):
            self.assertEqual(visor.find_var(1, 2337).value(), expected)
            self.assertEqual(visor.find_var(1, 3544).value(), first_target)
            payload, keys, snapshot = script.delta(stream.base, stream.touched(), script.frame, False)
            update = stream.delta(payload, snapshot, keys, script.frame)
            frame = client.apply(update)
            stream.arrived(update.chunk_last)
            self.assertEqual(client.vars[(0, 2337)], expected)
            self.assertEqual(client.vars[(0, 3544)], first_target)
            return frame

        ids = [expr.Entity(actor.entity) for actor in actors]
        ranked = (*ids[:2], expr.Entity(enemy.body), *ids[2:])
        script.ult_seconds = 0
        script.set_ult_charge(script.component.vars[338].value())
        run.step({soldier: (0, 0, 0x20)})
        for _ in range(80):
            run.step({})
        search = visor.states[7]
        started = search.start // script.component.frame_ms
        while (run.frame - started) % 3:
            run.step({})

        def refresh(commands):
            for _ in range(3):
                run.step(commands)

        publish(ranked, ranked[0])
        first.team = soldier.team
        first_pool, second_pool = (self.vitals(run, actor) for actor in actors[:2])
        first_health, second_health = first_pool.total, second_pool.total
        run.step({soldier: (0, 0, 1)})
        for _ in range(2):
            unchanged = publish(ranked, ranked[0])
            self.assertNotIn(2337, unchanged.entity_vars)
            self.assertNotIn(3544, unchanged.entity_vars)
            run.step({})
        publish(ranked[1:], ranked[1])
        self.assertEqual(first_pool.total, first_health)
        self.assertEqual(second_pool.total, second_health - 20)

        world = run.match.collision_world
        run.match.collision_world = World([
            (-15, -2, 0, 15, -2, 0, 15, 6, 0),
            (-15, -2, 0, 15, 6, 0, -15, 6, 0),
        ])
        refresh({})
        publish((), None)
        run.match.collision_world = world
        first.team = enemy.team
        refresh({})
        publish(ranked, ranked[0])
        yaw, pitch = aim(run.match.combat._eye(soldier), (4, 1, 9))
        refresh({soldier: (yaw, pitch, 0)})
        reordered = visor.find_var(1, 2337).value()
        self.assertCountEqual(reordered, ranked)
        publish(reordered, ids[2])
        refresh({soldier: (-32768, 0, 0)})
        publish((), None)
        refresh({})
        publish(ranked, ranked[0])

        first.team = soldier.team
        run.frame += 8
        run.match.update(run.frame * combat.TICK, run.frame)
        publish(ranked[1:], ranked[1])
        for _ in range(400):
            run.step({})
        self.assertFalse(visor.states[7].active)
        publish(None, None)

    def test_visor_observer_receives_lock_changes_and_clears_at_expiry_and_death(self):
        run, soldier, first = self.arena("1", (1.8, 0, 9))
        soldier.mover.position = [0, 0, -3]
        soldier.mover.body = None
        actors = [first]
        for key, x in (("2", 2.8), ("3", 4), ("4", 5.5), ("5", 7)):
            actors.append(npc.spawn(run.match, self.catalog[key], (x, 0, 9), team=1))
        enemy = run.match.add_player(2, "Observer", soldier.hero.guid, 1, False)
        in_world(run.match, enemy)
        enemy.client = NpcRecordingClient(enemy)
        run.players.append(enemy)
        self.assertTrue(run.match.switch_hero(enemy, soldier.hero))
        enemy.mover.position = [-3.4, 0, 9]
        enemy.mover.body = None
        fight = run.match.combat
        for _ in range(12):
            run.step({})
        stream = fight.observers.acknowledged_stream(enemy, soldier.body)
        self.assertIsNotNone(stream)
        decoder = ClientBody.for_hero(soldier.hero.guid)
        cursor = 0

        def read_observer():
            nonlocal cursor
            frames = []
            for update in enemy.client.entities[cursor:]:
                if update.stream is stream:
                    frame = decoder.apply(update)
                    self.assertFalse(frame.owner)
                    frames.append(frame)
            cursor = len(enemy.client.entities)
            return frames

        def wait_for_clear():
            # World ticks and observer send ticks have separate cadences.
            for _ in range(16):
                read_observer()
                if decoder.vars[(0, 2337)] is None and decoder.vars[(0, 3544)] is None:
                    return
                run.frame += 1
                run.match.update(run.frame * combat.TICK, run.frame)
            self.fail("Observer did not receive both Visor lock clears")

        self.assertTrue(read_observer()[0].full)
        self.assertIsNone(decoder.vars.get((0, 2337)))
        script = soldier.body_script
        script.ult_seconds = 0
        script.set_ult_charge(script.component.entity_var(338).value())
        run.step({soldier: (0, 0, 0x20)})
        for _ in range(90):
            run.step({})
        ids = [expr.Entity(actor.entity) for actor in actors]
        ranked = (*ids[:2], expr.Entity(enemy.body), *ids[2:])
        self.assertTrue(read_observer())
        self.assertEqual(decoder.vars[(0, 2337)], ranked)
        self.assertEqual(decoder.vars[(0, 3544)], ranked[0])

        first.team = soldier.team
        for _ in range(12):
            run.step({})
        self.assertTrue(read_observer())
        self.assertEqual(decoder.vars[(0, 2337)], ranked[1:])
        self.assertEqual(decoder.vars[(0, 3544)], ranked[1])

        run.frame += 400
        run.match.update(run.frame * combat.TICK, run.frame)
        wait_for_clear()
        script.set_ult_charge(script.component.entity_var(338).value())
        run.step({soldier: (0, 0, 0x20)})
        for _ in range(90):
            run.step({})
        self.assertTrue(read_observer())
        self.assertEqual(decoder.vars[(0, 2337)], ranked[1:])
        self.assertEqual(decoder.vars[(0, 3544)], ranked[1])

        own = self.vitals(run, soldier)
        own.damage(own.total - 1)
        angles = aim(fight._eye(enemy), (0, .8, -3))
        run.step({enemy: (*angles, 1)})
        self.assertTrue(own.dead)
        self.assertEqual(own.killer, enemy.body)
        final_command = script.frame
        self.assertIsNone(script.component.entity_var(2337).value())
        self.assertIsNone(script.component.entity_var(3544).value())
        wait_for_clear()
        self.assertEqual(script.frame, final_command)
        self.assertIs(fight.observers.acknowledged_stream(enemy, soldier.body), stream)

    def test_visor_uses_the_viewed_target_when_current_lock_has_changed(self):
        for key, definition in self.catalog.items():
            for delay in (0, 64):
                with self.subTest(npc=definition.name, view_delay_ms=delay):
                    run, soldier, actor = self.arena(key, (1.5, 0, 3))
                    soldier.mover.position = [0, 0, -3]
                    soldier.mover.body = None
                    other = run.match.add_player(2, "Enemy", soldier.hero.guid, 1, False)
                    in_world(run.match, other)
                    other.client = NpcRecordingClient(other)
                    run.players.append(other)
                    self.assertTrue(run.match.switch_hero(other, soldier.hero))
                    other.mover.position = [-3.0, 0, 6]
                    other.mover.body = None
                    npc_pool, player_pool = (self.vitals(run, target) for target in (actor, other))
                    npc_health, player_health = npc_pool.total, player_pool.total
                    script = soldier.body_script
                    script.ult_seconds = 0
                    script.set_ult_charge(script.component.vars[338].value())
                    run.step({soldier: (0, 0, 0x20)})
                    for _ in range(80):
                        run.step({})
                    visor = next(i for i in script.component.instances.values() if i.graph.index == 0x0259)
                    npc_id, player_id = expr.Entity(actor.entity), expr.Entity(other.body)
                    locks = visor.find_var(1, 2337)
                    self.assertEqual(locks.value(), (npc_id, player_id))
                    actor.position = (10, 0, -3)
                    for _ in range(3):
                        run.step({})
                    self.assertEqual(locks.value(), (player_id,))
                    tick = run.frame + 1
                    run.match.update(tick * combat.TICK, tick)
                    soldier.take_commands([Command(tick, buttons=1, extra=delay)])
                    self.assertEqual(locks.value(), (player_id,))
                    self.assertEqual(npc_pool.total, npc_health - (20 if delay else 0))
                    self.assertEqual(player_pool.total, player_health - (0 if delay else 20))

    def test_held_visor_retargets_and_reacquires_each_pve_body(self):
        exposed = (1.5, 0, 3)
        for key, definition in self.catalog.items():
            for gate, position in (
                ("wall", (2, 0, 6)), ("range", (3, 0, 50)),
                ("cone", (10, 0, -1)), ("team", exposed),
            ):
                with self.subTest(npc=definition.name, gate=gate):
                    run, soldier, first = self.arena(key, exposed)
                    second = npc.spawn(run.match, self.catalog["1"], (-3, 0, 3), team=1)
                    run.match.collision_world = World([
                        (0, -10, 4, 10, -10, 4, 10, 10, 4),
                        (0, -10, 4, 10, 10, 4, 0, 10, 4),
                    ])
                    first_pool = self.vitals(run, first)
                    second_pool = self.vitals(run, second)
                    first_health, second_health = first_pool.total, second_pool.total
                    self.start_visor(run, soldier)
                    ammo = soldier.body_script.component.instances[11].vars[53]
                    for phase, expected in enumerate((
                        (first_health - 20, second_health),
                        (first_health - 20, second_health - 20),
                        (first_health - 40, second_health - 20),
                    )):
                        if phase == 1:
                            first.position = position
                            if gate == "team":
                                first.team = soldier.team
                        elif phase == 2:
                            first.position = exposed
                            first.team = second.team
                        rounds = ammo.value()
                        for _ in range(15):
                            self.input(run, soldier, 1)
                            if ammo.value() < rounds:
                                break
                        self.assertEqual(ammo.value(), rounds - 1)
                        self.assertEqual((first_pool.total, second_pool.total), expected)


    def test_held_visor_fire_returns_to_manual_aim_on_each_pve_body(self):
        for key, definition in self.catalog.items():
            with self.subTest(npc=definition.name):
                run, soldier, actor = self.arena(key, (3, 0, 3))
                pool = self.vitals(run, actor)
                before = pool.total
                self.start_visor(run, soldier)
                script = soldier.body_script
                visor = next(i for i in script.component.instances.values() if i.graph.index == 0x0259)
                search, = (state for state in visor.states.values() if state.active
                           and isinstance(state, TrackTargets)
                           and (state.node.field("m_8A2905A0") or {}).get("m_identifier") == 2337)
                for _ in range(325):
                    self.input(run, soldier)
                self.assertTrue(search.active)
                self.input(run, soldier, 1)
                self.assertEqual(pool.total, before - 20)
                for _ in range(100):
                    if not search.active:
                        break
                    self.input(run, soldier, 1)
                self.assertFalse(search.active)
                wounded = pool.total
                self.assertGreater(wounded, 20)
                ammo = script.component.instances[11].vars[53]
                rounds = ammo.value()
                for _ in range(15):
                    self.input(run, soldier, 1)
                    if ammo.value() < rounds:
                        break
                self.assertEqual(ammo.value(), rounds - 1)
                self.assertEqual(pool.total, wounded)
                self.assertEqual(script.component.vars[342].value(), 0)

                yaw, pitch = aim(run.match.combat._eye(soldier), (3, 0.8, 3))
                rounds = ammo.value()
                for _ in range(15):
                    self.input(run, soldier, 1, yaw, pitch)
                    if ammo.value() < rounds:
                        break
                self.assertEqual(ammo.value(), rounds - 1)
                self.assertEqual(pool.total, wounded - 20)
                self.assertGreater(script.component.vars[342].value(), 0)


    def test_visor_filters_all_five_pve_bodies_by_team_wall_range_and_cone(self):
        for key, definition in self.catalog.items():
            for case, position in (
                ("clear", (3, 0, 3)), ("ally", (3, 0, 3)), ("wall", (3, 0, 3)),
                ("range", (3, 0, 50)), ("cone", (10, 0, -1)),
            ):
                with self.subTest(npc=definition.name, case=case):
                    run, soldier, actor = self.arena(key, position, team=0 if case == "ally" else 1)
                    if case == "wall":
                        run.match.collision_world = World([
                            (-10, -10, 0, 10, -10, 0, 10, 10, 0),
                            (-10, -10, 0, 10, 10, 0, -10, 10, 0),
                        ])
                    self.start_visor(run, soldier)
                    pool = self.vitals(run, actor)
                    initial = pool.total
                    ammo = soldier.body_script.component.instances[11].vars[53]
                    rounds = ammo.value()
                    self.input(run, soldier, 1)
                    self.assertEqual(ammo.value(), rounds - 1)
                    self.assertEqual(pool.total, initial - (20 if case == "clear" else 0))

    def test_visor_rifle_uses_exposed_axis_of_each_real_npc_and_helix_keeps_aim(self):
        for key, definition in self.catalog.items():
            for exposed in (True, False):
                with self.subTest(npc=definition.name, exposed=exposed):
                    run, soldier, enemy = self.arena(key, (1.5, 0, 3))
                    soldier.mover.position = [0, 0, -3]
                    soldier.mover.body = None
                    fight = run.match.combat
                    origin = fight._eye(soldier)
                    target = next(t for t in fight._targets() if t.owner is enemy)
                    centre_y = target.feet[1] + target.height / 2
                    _, upper = target.segment()
                    self.assertGreater(upper[1], centre_y)
                    cover = (origin[1] + (centre_y + upper[1]) / 2) / 2 if exposed else 4
                    surfaces = [
                        (-20, 0, -20, -20, 0, 20, 20, 0, 20),
                        (-20, 0, -20, 20, 0, 20, 20, 0, -20),
                        (.3, 0, 0, 1.2, 0, 0, 1.2, cover, 0),
                        (.3, 0, 0, 1.2, cover, 0, .3, cover, 0),
                        (-6, 0, 15, 6, 0, 15, 6, 5, 15),
                        (-6, 0, 15, 6, 5, 15, -6, 5, 15),
                    ]
                    run.match.collision_world = World(surfaces + [
                        (*a[0:3], *a[6:9], *a[3:6]) for a in surfaces[2:]
                    ])
                    script = soldier.body_script
                    script.ult_seconds = 0
                    script.set_ult_charge(script.component.vars[338].value())
                    run.step({soldier: (0, 0, 0x20)})
                    for _ in range(80):
                        run.step({})
                    pool = fight.vitals_of(target)
                    before = pool.total
                    run.step({soldier: (0, 0, 1)})
                    self.assertEqual(before - pool.total, 20 if exposed else 0)
                    if exposed:
                        run.step({})
                        run.step({soldier: (0, 0, 2)})
                        rocket, = fight.rockets
                        self.assertEqual(rocket.direction, combat.aim(0, 0))
                        for _ in range(45):
                            run.step({})
                        self.assertNotIn(rocket, fight.rockets)
                        self.assertEqual(before - pool.total, 20)

    def test_all_five_real_bodies_respect_static_wall_for_rifle_melee_and_helix(self):
        for key, definition in self.catalog.items():
            for buttons in (1, 0x800, 2):
                with self.subTest(npc=definition.name, buttons=buttons):
                    run, soldier, enemy = self.arena(key, (0.0, 0.0, -0.8))
                    run.match.collision_world = World([
                        (-10, -10, -2, 10, -10, -2, 10, 10, -2),
                        (-10, -10, -2, 10, 10, -2, -10, 10, -2),
                    ])
                    pool = self.vitals(run, enemy)
                    before = pool.total
                    yaw, pitch = aim(run.match.combat._eye(soldier), (0.0, 0.8, -0.8))
                    self.input(run, soldier, buttons, yaw, pitch)
                    for _ in range(25):
                        self.input(run, soldier)
                    self.assertEqual(pool.total, before)

    def test_helix_near_self_splash_preserves_all_five_enemy_or_ally_filters_and_credit(self):
        for key, definition in self.catalog.items():
            for ally in (False, True):
                with self.subTest(npc=definition.name, ally=ally):
                    run, soldier, actor = self.arena(key, (1.1, 0.0, -2.8), team=0 if ally else 1)
                    soldier.body_script.ult_seconds = 0
                    run.match.collision_world = World([
                        (-10, -10, -2.4, 10, -10, -2.4, 10, 10, -2.4),
                        (-10, -10, -2.4, 10, 10, -2.4, -10, 10, -2.4),
                    ])
                    before = self.vitals(run, actor).total
                    self.input(run, soldier, 2)
                    for _ in range(30):
                        self.input(run, soldier)
                    self.assertFalse(run.match.combat.rockets)
                    self.assertEqual(self.vitals(run, soldier).total, 160)
                    expected = 0 if ally else 80
                    self.assertEqual(self.vitals(run, actor).total, before - expected)
                    self.assertEqual(soldier.stats.value(stats.HERO_DAMAGE), expected)
                    self.assertEqual(soldier.body_script.component.entity_var(342).value(), expected)
                    self.assertEqual(len(soldier.client.messages(combat.HIT)), 0 if ally else 1)

    def test_wall_helix_splash_falloff_and_occlusion_on_each_pve_body(self):
        floor = [
            (-20, 0, -20, -20, 0, 20, 20, 0, 20),
            (-20, 0, -20, 20, 0, 20, 20, 0, -20),
        ]
        wall = [(-8, 0, 4, 8, 0, 4, 8, 4, 4), (-8, 0, 4, 8, 4, 4, -8, 4, 4)]
        world = World(floor + wall + [
            (*triangle[:3], *triangle[6:9], *triangle[3:6]) for triangle in wall
        ])
        for key, definition in self.catalog.items():
            radius = mover.mover_data(definition.body).stand_radius
            for distance, covered, expected in (
                (.48, False, 80), (.52, False, 80), (.98, False, 80),
                (1.02, False, 79.6), (2, False, 60),
                (2.98, False, 40.4), (3.02, False, 0), (2, True, 0),
            ):
                with self.subTest(npc=definition.name, distance=distance, covered=covered):
                    dz = .25 if covered else -.25
                    x = math.sqrt((distance + radius) ** 2 - dz ** 2)
                    run, soldier, actor = self.arena(key, (x, .75, 4 + dz))
                    soldier.mover.position = list(soldier.mover.position)
                    script = soldier.body_script
                    script.ult_seconds = 0
                    run.match.collision_world = world
                    fight = run.match.combat
                    pool = self.vitals(run, actor)
                    initial = pool.total
                    run.step({soldier: (0, 0, 2)})
                    rocket, = fight.rockets
                    self.assertEqual(rocket.blast.rings, ((1, 1), (3, .5)))
                    self.assertEqual(rocket.blast.impulse_rings, ((.5, 1, 1), (3, .75, .25)))
                    self.assertEqual(rocket.blast.amount, 80)
                    self.assertEqual(pool.total, initial)
                    for _ in range(30):
                        run.step({})
                        if rocket not in fight.rockets:
                            break
                    self.assertNotIn(rocket, fight.rockets)
                    self.assertAlmostEqual(rocket.flown * rocket.speed, 7, places=4)
                    self.assertAlmostEqual(pool.total, initial - expected, places=4)
                    self.assertAlmostEqual(soldier.stats.value(stats.HERO_DAMAGE), expected, places=4)
                    self.assertAlmostEqual(script.component.entity_var(342).value(), expected, places=4)
                    self.assertEqual(len(soldier.client.messages(combat.HIT)), int(expected > 0))
                    if not expected or definition.name == "Heavy Assault":
                        self.assertIsNone(actor.impulse_mover)
                        self.assertEqual(actor.velocity, (0, 0, 0))
                    else:
                        scale = 1 - max(0, distance - .5) / 10
                        vx, vy, vz = actor.velocity
                        self.assertGreater(vx, 0)
                        self.assertLess(vz, 0)
                        self.assertAlmostEqual(math.hypot(vx, vz), 4 * scale, places=4)
                        self.assertAlmostEqual(vy, 6 * scale, places=4)

    def test_one_helix_settles_mixed_contacts_on_all_five_pve_bodies(self):
        keys = tuple(self.catalog)
        outcomes = (
            ("survive", None, None, (40, 80, 80)),
            ("die_on_direct", 30, 55, (30, 0, 55)),
            ("die_on_splash", 55, None, (40, 15, 80)),
        )
        cases = ((index, key, outcome) for index, key in enumerate(keys) for outcome in outcomes)
        for index, key, (result, direct_hp, near_hp, amounts) in cases:
            with self.subTest(direct=self.catalog[key].name, result=result):
                run, soldier, direct = self.arena(key, (0, 0, 4))
                soldier.mover.position = [0, 0, -3]
                soldier.mover.body = None
                script = soldier.body_script
                script.ult_seconds = 0
                fight = run.match.combat
                eye = fight._eye(soldier)
                angles = aim(eye, (0, .8, 4))
                direction = combat.aim(*angles)
                target = next(t for t in fight._targets() if t.owner is direct)
                distance = combat.ray_capsule(eye, direction, *target.segment(), target.radius)
                self.assertIsNotNone(distance)
                _, y, z = (origin + axis * distance for origin, axis in zip(eye, direction, strict=True))
                others = keys[index + 1:] + keys[:index]
                near_key, far_key, ally_key, covered_key = others
                near_radius = mover.mover_data(self.catalog[near_key].body).stand_radius
                far_radius = mover.mover_data(self.catalog[far_key].body).stand_radius
                feet = y - .8
                near = npc.spawn(run.match, self.catalog[near_key], (near_radius + .8, feet, z), team=1)
                far = npc.spawn(run.match, self.catalog[far_key], (-far_radius - 2, feet, z), team=1)
                ally = npc.spawn(run.match, self.catalog[ally_key], (0, feet, z - 2), team=soldier.team)
                covered = npc.spawn(run.match, self.catalog[covered_key], (2, feet, z + 1), team=1)
                wall = z + .4
                run.match.collision_world = World([
                    (-20, 0, -20, -20, 0, 20, 20, 0, 20),
                    (-20, 0, -20, 20, 0, 20, 20, 0, -20),
                    (.15, 0, wall, 5, 0, wall, 5, 4, wall),
                    (.15, 0, wall, 5, 4, wall, .15, 4, wall),
                ])
                targets = (direct, near, far, ally, covered)
                pools = [self.vitals(run, actor) for actor in targets]
                # Isolate the impact phase that can kill while leaving the geometry unchanged.
                for pool, remaining in zip(pools[:2], (direct_hp, near_hp), strict=True):
                    if remaining is not None:
                        pool.damage(pool.total - remaining)
                before = [pool.total for pool in pools]
                run.step({soldier: (*angles, 2)})
                rocket, = fight.rockets
                for _ in range(30):
                    run.step({})
                    if rocket not in fight.rockets:
                        break
                self.assertNotIn(rocket, fight.rockets)
                self.assertAlmostEqual(rocket.flown * rocket.speed, distance)
                impact, direct_splash, near_splash = amounts
                expected_damage = (impact + direct_splash, near_splash, 60, 0, 0)
                for initial, pool, expected in zip(before, pools, expected_damage, strict=True):
                    self.assertAlmostEqual(initial - pool.total, expected, places=5)
                expected_hits = [
                    (direct.entity, impact, result == "die_on_direct"),
                    (near.entity, near_splash, near_hp is not None),
                    (far.entity, 60, False),
                ]
                if direct_splash:
                    expected_hits.append((direct.entity, direct_splash, result == "die_on_splash"))
                hits = [message["+0x78"] for message in soldier.client.messages(combat.HIT)]
                self.assertCountEqual(
                    [(hit["+0x0"]["+0x0"], hit["+0x4"], bool(hit["+0x8"] & combat.KILLING)) for hit in hits],
                    expected_hits,
                )
                total = sum(expected_damage)
                self.assertAlmostEqual(soldier.stats.value(stats.HERO_DAMAGE), total, places=5)
                self.assertAlmostEqual(script.component.entity_var(342).value(), total, places=4)
                deaths = []
                for actor, pool, killed in zip(targets[:2], pools[:2], (direct_hp, near_hp), strict=True):
                    self.assertEqual(actor.dead, killed is not None)
                    if killed is not None:
                        deaths.append(actor.entity)
                        self.assertEqual(pool.killer, soldier.body)
                        self.assertIn(actor, run.match.bots)
                        self.assertIsNone(actor.impulse_mover)
                self.assertCountEqual([entity for entity, _ in soldier.client.bot_deaths], deaths)
                self.assertEqual(soldier.stats.value(stats.ELIMINATIONS), len(deaths))
                self.assertIsNone(ally.impulse_mover)
                self.assertIsNone(covered.impulse_mover)

    def test_helix_range_expiry_does_not_explode_near_any_of_the_five_real_bodies(self):
        for key, definition in self.catalog.items():
            with self.subTest(npc=definition.name):
                run, soldier, actor = self.arena(key, (0.0, 0.0, 99.0))
                soldier.body_script.ult_seconds = 0
                before = self.vitals(run, actor).total
                self.input(run, soldier, 2)
                rocket, = run.match.combat.rockets
                self.assertAlmostEqual(rocket.left + rocket.flown, 2)  # input helper already swept one tick
                for _ in range(160):
                    self.input(run, soldier)
                self.assertFalse(run.match.combat.rockets)
                self.assertAlmostEqual(rocket.flown, 2)
                self.assertEqual(self.vitals(run, actor).total, before)
                self.assertEqual(self.vitals(run, soldier).total, 200)

    def test_field_heals_allied_bodies_but_excludes_enemy_and_dead_bodies(self):
        for key, definition in self.catalog.items():
            for team in (0, 1):
                with self.subTest(npc=definition.name, team=team):
                    run, soldier, enemy = self.arena(key, (0.0, 0.0, -3.0), team=team)
                    pool = self.vitals(run, enemy)
                    pool.damage(50)
                    before = pool.total
                    run.match.combat.heal_area(soldier, soldier.mover.position, 5, 35)
                    self.assertEqual(pool.total, before + (35 if team == 0 else 0))
                    pool.died_at = run.match.combat.now
                    dead_health = pool.total
                    run.match.combat.heal_area(soldier, soldier.mover.position, 5, 35)
                    self.assertEqual(pool.total, dead_health)

    def test_authored_field_deployment_heals_all_five_with_filters_expiry_and_cooldown_recovery(self):
        for key, definition in self.catalog.items():
            for case in ("visible", "enemy", "dead", "wall", "range"):
                with self.subTest(npc=definition.name, case=case):
                    position = (0.0, 0.0, 5.0 if case == "range" else 1.0)
                    run, soldier, actor = self.arena(key, position, team=1 if case == "enemy" else 0)
                    soldier.body_script.ult_seconds = 0
                    fight, pool = run.match.combat, self.vitals(run, actor)
                    pool.damage(100)  # fixture injury, not an authored incoming-attack claim
                    if case == "dead":
                        target = next(t for t in fight._targets() if t.owner is actor)
                        fight._damage(soldier, target, pool.total, False, None, fight.tick)
                        self.assertTrue(actor.dead and actor.body_script.component.owner_dead)
                    if case == "wall":
                        run.match.collision_world = World([
                            (-10, -10, -1, 10, -10, -1, 10, 10, -1),
                            (-10, -10, -1, 10, 10, -1, -10, 10, -1),
                        ])
                    before = pool.total
                    self.input(run, soldier, 0x10)
                    for _ in range(180):
                        self.input(run, soldier)
                    self.assertEqual(pool.total > before, case == "visible")
                    if case != "visible":
                        self.assertEqual(pool.total, before)
                        continue
                    for _ in range(220):
                        self.input(run, soldier)
                    self.assertEqual(pool.total, sum(pool.most.values()))
                    pool.damage(10)
                    stopped = pool.total
                    self.input(run, soldier, 0x10)  # before authored cooldown recovery
                    for _ in range(80):
                        self.input(run, soldier)
                    self.assertEqual(pool.total, stopped)  # old field expired; new cast rejected
                    for _ in range(1000):
                        self.input(run, soldier)
                    self.input(run, soldier, 0x10)
                    for _ in range(100):
                        self.input(run, soldier)
                    self.assertEqual(pool.total, sum(pool.most.values()))

    def test_field_cooldown_boundary_controls_healing_on_each_pve_body(self):
        for key, definition in self.catalog.items():
            for frames_early in (1, 0):
                with self.subTest(npc=definition.name, frames_early=frames_early):
                    run, soldier, actor = self.arena(key, (0, 0, 1), team=0)
                    soldier.body_script.ult_seconds = 0
                    pool = self.vitals(run, actor)
                    full = pool.total
                    pool.damage(10)
                    self.input(run, soldier, 0x10)
                    for _ in range(100):
                        self.input(run, soldier)
                    self.assertEqual(pool.total, full)
                    component = soldier.body_script.component
                    field = next(i for i in component.instances.values() if i.graph.index == 0x0257)
                    cooldown, = (state for state in field.states.values()
                                 if isinstance(state, Ability) and state.active and state.cur == 15)
                    frame_ms = component.frame_ms
                    deadline = cooldown.last + 15000
                    for _ in range(950):
                        if component.now >= deadline - (frames_early + 1) * frame_ms:
                            break
                        self.input(run, soldier)
                    pool.damage(10)
                    self.input(run, soldier, 0x10)
                    offset = component.now - deadline + frames_early * frame_ms
                    self.assertIn(offset, range(frame_ms))
                    for _ in range(100):
                        self.input(run, soldier)
                    self.assertEqual(pool.total, full - 10 if frames_early else full)


    def test_actual_field_range_and_static_cover_boundaries_for_each_npc(self):
        for key, definition in self.catalog.items():
            for boundary in ("range", "cover"):
                for offset in (-.05, .05):
                    with self.subTest(npc=definition.name, boundary=boundary, offset=offset):
                        run, soldier, actor = self.arena(key, (0, 0, 1), team=0)
                        soldier.body_script.ult_seconds = 0
                        fight, pool = run.match.combat, self.vitals(run, actor)
                        pool.damage(50)
                        before = pool.total
                        self.input(run, soldier, 0x10)
                        for _ in range(80):
                            if fight.projectiles.shots:
                                break
                            self.input(run, soldier)
                        shot, = fight.projectiles.shots
                        self.assertEqual(shot.spawn, projectiles.FIELD_ENTITY)
                        self.assertIsNone(shot.field_tick)
                        for _ in range(80):
                            self.input(run, soldier)
                            if shot.field_tick is not None:
                                break
                        self.assertIsNotNone(shot.field_tick)
                        self.assertEqual(pool.total, before)
                        origin = projectiles.field_centre(shot)
                        actor.position = (origin[0], 0, actor.position[2])
                        target = next(t for t in fight._targets() if t.owner is actor)
                        a, b = target.segment()
                        dy = min(max(origin[1], a[1]), b[1]) - origin[1]
                        if boundary == "range":
                            z = origin[2] + math.sqrt((5 + target.radius) ** 2 - dy ** 2)
                            actor.position = (origin[0], 0, z + offset)
                            expected = offset < 0
                        else:
                            dz = actor.position[2] - origin[2]
                            z = actor.position[2] - target.radius * dz / math.hypot(dz, dy) + offset
                            # A fixed two-sided plane isolates the capsule surface boundary.
                            wall = [(-10, -4, z, 10, -4, z, 10, 4, z), (-10, -4, z, 10, 4, z, -10, 4, z)]
                            run.match.collision_world = World(wall + [
                                (*t[:3], *t[6:9], *t[3:6]) for t in wall
                            ])
                            expected = offset > 0
                        for _ in range(75):
                            self.input(run, soldier)
                        self.assertIsNotNone(shot.field_tick)
                        if expected:
                            self.assertGreater(pool.total, before)
                        else:
                            self.assertEqual(pool.total, before)

    def test_deployed_field_rechecks_each_npc_team_range_and_cover(self):
        for key, definition in self.catalog.items():
            with self.subTest(npc=definition.name):
                run, soldier, actor = self.arena(key, (0, 0, 1), team=0)
                soldier.body_script.ult_seconds = 0
                fight, pool = run.match.combat, self.vitals(run, actor)
                pool.damage(100)
                self.input(run, soldier, 0x10)
                for _ in range(80):
                    self.input(run, soldier)
                    if pool.total > sum(pool.most.values()) - 100:
                        break
                shot, = fight.projectiles.shots
                self.assertIsNotNone(shot.field_tick)
                world, position, team = run.match.collision_world, actor.position, actor.team
                wall = World([
                    (-10, -4, -1, 10, -4, -1, 10, 4, -1),
                    (-10, -4, -1, 10, 4, -1, -10, 4, -1),
                ])
                for lost in ("team", "range", "cover"):
                    with self.subTest(lost=lost):
                        if lost == "team":
                            actor.team = 1 - team
                        elif lost == "range":
                            actor.position = (0, 0, 10)
                        else:
                            run.match.collision_world = wall
                        before = pool.total
                        for _ in range(12):
                            self.input(run, soldier)
                        self.assertEqual(pool.total, before)
                        actor.team, actor.position, run.match.collision_world = team, position, world
                        self.input(run, soldier)
                        self.assertAlmostEqual(pool.total - before, 35 * combat.TICK, places=6)
                        self.assertIn(shot, fight.projectiles.shots)

    def test_actual_ana_grenades_gate_one_field_on_all_five_npcs_and_soldier(self):
        ana_hero = heroes()[0x02E000000000013B]

        def add_ana(run, slot, team, position):
            player = run.match.add_player(slot, "Ana", ana_hero.guid, team, False)
            in_world(run.match, player)
            player.client = NpcRecordingClient(player)
            run.players.append(player)
            self.assertTrue(run.match.switch_hero(player, ana_hero))
            player.mover.position = position
            player.mover.body = None
            return player

        def cast_field(run, soldier):
            fight = run.match.combat
            run.step({soldier: (0, 0, 16)})
            for _ in range(80):
                run.step({})
                if any(shot.field_tick is not None for shot in fight.projectiles.shots):
                    field, = fight.projectiles.shots
                    return field
            self.fail("Biotic Field did not deploy")

        for jump, boost in ((False, False), (True, False), (False, True), (True, True)):
            with self.subTest(jump=jump, boost=boost):
                run, soldier, trooper = self.arena("1", (-1.4, 0, -2), team=0)
                soldier.mover.position = [0, 0, -3]
                soldier.mover.body = None
                actors = [trooper]
                for key, x, z in (("2", -.7, -1.7), ("3", 0, -1.5), ("4", .7, -1.7), ("5", 1.4, -2)):
                    actors.append(npc.spawn(run.match, self.catalog[key], (x, 0, z), team=soldier.team))
                ana = add_ana(run, 2, 1, [0, 0, -7])
                support = add_ana(run, 3, soldier.team, [4, 0, -7]) if boost else None
                fight = run.match.combat
                soldier.body_script.ult_seconds = 0
                charge = soldier.body_script.component.entity_var(342)
                recipients = (soldier.body, *(actor.entity for actor in actors))
                pools = [self.vitals(run, actor) for actor in (soldier, *actors)]
                initial = [pool.total for pool in pools]
                field = cast_field(run, soldier)
                angles = aim(fight._eye(ana), tuple(soldier.mover.position))
                run.step({ana: (*angles, 16)})
                for _ in range(35):
                    if all(entity in fight.heal_modifiers for entity in recipients):
                        break
                    run.step({})
                deadline, = {fight.heal_modifiers[entity].anti_until for entity in recipients}
                self.assertEqual(deadline - run.frame, 250)
                wounded = [pool.total for pool in pools]
                for before, after in zip(initial, wounded, strict=True):
                    self.assertLess(after, before)
                if support is not None:
                    angles = aim(fight._eye(support), tuple(soldier.mover.position))
                    run.step({support: (*angles, 16)})
                    for _ in range(35):
                        if all(fight.heal_modifiers[entity].boost_until > deadline + 3
                               for entity in recipients):
                            break
                        run.step({})
                    for entity in recipients:
                        modifier = fight.heal_modifiers[entity]
                        self.assertEqual(modifier.anti_until, deadline)
                        self.assertLess(modifier.boost_from, deadline)
                        self.assertGreater(modifier.boost_until, deadline + 3)
                    self.assertEqual([pool.total for pool in pools], wounded)
                    self.assertEqual(support.stats.value(stats.HEALING), 0)
                self.assertEqual(soldier.stats.value(stats.HEALING), 0)
                self.assertEqual(charge.value(), 0)
                end = deadline + 3
                if jump:
                    run.frame = end
                    run.match.update(end * combat.TICK, end)
                else:
                    while run.frame < end:
                        run.step({})
                        if run.frame < deadline:
                            self.assertEqual([pool.total for pool in pools], wounded)
                            self.assertEqual(charge.value(), 0)
                expected = 4 * 35 * combat.TICK * (1.5 if boost else 1)
                for entity, pool, before in zip(recipients, pools, wounded, strict=True):
                    self.assertAlmostEqual(pool.total - before, expected, places=6)
                    self.assertEqual(entity in fight.heal_modifiers, boost)
                self.assertIn(field, fight.projectiles.shots)
                self.assertAlmostEqual(soldier.stats.value(stats.HEALING), len(pools) * expected, places=5)
                self.assertAlmostEqual(charge.value(), len(pools) * expected, places=4)

                expires = field.field_tick + math.ceil(5 / combat.TICK)
                expected_heals = []
                for entity, full, before in zip(recipients, initial, wounded, strict=True):
                    boosted_ticks = 0
                    if boost:
                        boosted_ticks = min(expires, fight.heal_modifiers[entity].boost_until) - deadline
                    total = (expires - deadline + .5 * boosted_ticks) * 35 * combat.TICK
                    expected_heals.append(min(total, full - before))
                if jump:
                    run.frame = expires
                    run.match.update(expires * combat.TICK, expires)
                else:
                    while run.frame < expires:
                        run.step({})
                self.assertNotIn(field, fight.projectiles.shots)
                for pool, before, total in zip(pools, wounded, expected_heals, strict=True):
                    self.assertAlmostEqual(pool.total - before, total, places=6)
                healing = soldier.stats.value(stats.HEALING)
                points = charge.value()
                self.assertAlmostEqual(healing, sum(expected_heals), places=5)
                self.assertAlmostEqual(points, sum(expected_heals), delta=.002)
                after_expiry = [pool.total for pool in pools]
                run.step({soldier: (0, 0, 16)})
                for _ in range(12):
                    run.step({})
                self.assertFalse(fight.projectiles.shots)
                self.assertEqual([pool.total for pool in pools], after_expiry)
                self.assertEqual(soldier.stats.value(stats.HEALING), healing)
                self.assertEqual(charge.value(), points)

                run.frame += 1000
                run.match.update(run.frame * combat.TICK, run.frame)
                run.step({})
                recovered = cast_field(run, soldier)
                self.assertIsNot(recovered, field)
                if jump:
                    run.frame += 180
                    run.match.update(run.frame * combat.TICK, run.frame)
                else:
                    for _ in range(180):
                        run.step({})
                self.assertIn(recovered, fight.projectiles.shots)
                self.assertEqual([pool.total for pool in pools], initial)
                full_healing = sum(full - hurt for full, hurt in zip(initial, wounded, strict=True))
                self.assertAlmostEqual(soldier.stats.value(stats.HEALING), full_healing, places=5)
                self.assertAlmostEqual(charge.value(), full_healing, delta=.002)
                for _ in range(12):
                    run.step({})
                self.assertEqual([pool.total for pool in pools], initial)
                self.assertAlmostEqual(soldier.stats.value(stats.HEALING), full_healing, places=5)
                self.assertAlmostEqual(charge.value(), full_healing, delta=.002)

    def test_overlapping_fields_share_actual_ana_wounds_and_charge_across_world_gap(self):
        modes = ("stepped", "jump-across-anti", "jump-after-anti", "replace-caster", "kill-caster",
                 "staggered-stepped", "staggered-jump")
        for mode in modes:
            with self.subTest(mode=mode):
                staggered = mode.startswith("staggered")
                run, first, trooper = self.arena("1", (-1.5, 0, -2), team=0)
                first.mover.position = [0, 0, -3]
                first.mover.body = None
                actors = [trooper]
                for key, x in (("2", -.75), ("3", 0), ("4", .75), ("5", 1.5)):
                    actors.append(npc.spawn(run.match, self.catalog[key], (x, 0, -1.5), team=first.team))
                ana_hero = heroes()[0x02E000000000013B]
                added = []
                players = [
                    (2, first.hero, first.team, [.9, 0, -3.4]),
                    (3, ana_hero, 1, [0, 0, -7]),
                ]
                if mode == "kill-caster":
                    players.append((4, first.hero, 1, [-2.4, 0, -6.5]))
                for slot, hero, team, position in players:
                    player = run.match.add_player(slot, hero.name, hero.guid, team, False)
                    in_world(run.match, player)
                    player.client = NpcRecordingClient(player)
                    run.players.append(player)
                    self.assertTrue(run.match.switch_hero(player, hero))
                    player.mover.position = position
                    player.mover.body = None
                    added.append(player)
                second, ana, *attackers = added
                fight = run.match.combat
                casters = (first, second)
                for player in casters:
                    player.body_script.ult_seconds = 0
                recipients = (first.body, second.body, *(actor.entity for actor in actors))
                pools = [self.vitals(run, actor) for actor in (*casters, *actors)]
                full = [pool.total for pool in pools]
                if staggered:
                    run.step({first: (0, 0, 16)})
                    for _ in range(80):
                        run.step({})
                        if any(shot.field_tick is not None for shot in fight.projectiles.shots):
                            break
                    for _ in range(42):
                        run.step({})
                angles = aim(fight._eye(ana), tuple(first.mover.position))
                run.step({ana: (*angles, 16)})
                for _ in range(35):
                    if all(entity in fight.heal_modifiers for entity in recipients):
                        break
                    run.step({})
                deadline, = {fight.heal_modifiers[entity].anti_until for entity in recipients}
                wounded = [pool.total for pool in pools]
                self.assertTrue(all(after < before for after, before in zip(wounded, full, strict=True)))

                run.step({player: (0, 0, 16) for player in (casters[1:] if staggered else casters)})
                for _ in range(80):
                    run.step({})
                    fields = [shot for shot in fight.projectiles.shots if shot.field_tick is not None]
                    if len(fields) == 2:
                        break
                left, right = fields
                self.assertEqual((left.player, right.player), casters)
                self.assertEqual([pool.total for pool in pools], wounded)
                end = deadline + 90
                if staggered:
                    duration = math.ceil(5 / combat.TICK)
                    left_end = left.field_tick + duration
                    end = right.field_tick + duration - 1
                    self.assertLess(deadline, left_end)
                    self.assertLess(left_end, end)
                else:
                    self.assertEqual(left.field_tick, right.field_tick)
                if mode in ("jump-after-anti", "replace-caster", "kill-caster"):
                    run.frame = deadline + 1
                    run.match.update(run.frame * combat.TICK, run.frame)
                    self.assertFalse(any(entity in fight.heal_modifiers for entity in recipients))
                    self.assertTrue(all(pool.total < maximum
                                        for pool, maximum in zip(pools, full, strict=True)))
                if mode in ("replace-caster", "kill-caster"):
                    if attackers:
                        attacker, = attackers
                        pools[0].damage(pools[0].total - 1)
                        x, y, z = first.mover.position
                        angles = aim(fight._eye(attacker), (x, y + .8, z))
                        for _ in range(20):
                            run.step({attacker: (*angles, 1)})
                            if pools[0].dead:
                                break
                        self.assertTrue(pools[0].dead)
                        self.assertEqual(pools[0].killer, attacker.body)
                        self.assertIn(left, fight.projectiles.shots)
                    else:
                        self.assertTrue(run.match.switch_hero(first, first.hero))
                        self.assertNotEqual(first.body, recipients[0])
                        first.body_script.ult_seconds = 0
                        self.assertNotIn(left, fight.projectiles.shots)
                    before = [pool.total for pool in pools]
                    healing_before = [player.stats.value(stats.HEALING) for player in casters]
                    charges = [player.body_script.component.entity_var(342) for player in casters]
                    points_before = [charge.value() for charge in charges]
                    run.frame += 16
                    run.match.update(run.frame * combat.TICK, run.frame)
                    self.assertIn(right, fight.projectiles.shots)
                    per_field = 16 * 35 * combat.TICK
                    self.assertEqual(pools[0].total, before[0])
                    if not attackers:
                        self.assertEqual(self.vitals(run, first).total, full[0])
                    for pool, previous in zip(pools[1:], before[1:], strict=True):
                        self.assertAlmostEqual(pool.total - previous, per_field * (2 if attackers else 1), places=6)
                    credit = per_field * (len(pools) - 1)
                    self.assertAlmostEqual(first.stats.value(stats.HEALING) - healing_before[0],
                                           credit if attackers else 0, places=5)
                    self.assertAlmostEqual(second.stats.value(stats.HEALING) - healing_before[1], credit, places=5)
                    self.assertEqual(charges[0].value(), points_before[0])
                    self.assertAlmostEqual(charges[1].value() - points_before[1], credit, delta=.002)
                    continue
                if not mode.endswith("stepped"):
                    run.frame = end
                    run.match.update(end * combat.TICK, end)
                else:
                    while run.frame < end:
                        run.step({})
                self.assertEqual([pool.total for pool in pools], full)
                self.assertIn(right, fight.projectiles.shots)
                self.assertEqual(left in fight.projectiles.shots, not staggered)

                per_tick = 35 * combat.TICK
                first_credit = 0.0
                total_credit = 0.0
                for before, after in zip(full, wounded, strict=True):
                    wound = before - after
                    if staggered:
                        overlap = (left_end - deadline) * per_tick
                        self.assertGreater(wound, 2 * overlap)
                        first_credit += overlap
                    else:
                        pairs, tail = divmod(wound, 2 * per_tick)
                        first_credit += pairs * per_tick + min(tail, per_tick)
                    total_credit += wound
                expected = (first_credit, total_credit - first_credit)
                for player, credit in zip(casters, expected, strict=True):
                    self.assertAlmostEqual(player.stats.value(stats.HEALING), credit, places=5)
                    points = player.body_script.component.entity_var(342).value()
                    self.assertAlmostEqual(points, credit, delta=.002)

    def test_rifle_kills_each_real_body_and_retains_corpse_without_respawn(self):
        for key, definition in self.catalog.items():
            with self.subTest(npc=definition.name):
                run, soldier, enemy = self.arena(key)
                pool = self.vitals(run, enemy)
                yaw, pitch = aim(run.match.combat._eye(soldier), (0.0, 0.8, 1.0))
                for _ in range(5000):
                    if enemy.dead:
                        break
                    rounds = soldier.body_script.component.instances[11].vars[53].value()
                    self.input(run, soldier, 1 if rounds else 4, yaw, pitch)
                self.assertTrue(enemy.dead)
                self.assertEqual(pool.total, 0)
                self.assertTrue(enemy.body_script.component.owner_dead)
                self.assertTrue(any(entity == enemy.entity for entity, _ in soldier.client.bot_deaths))
                self.assertFalse(any(actor is enemy for _, actor in run.match.combat.respawns))
                for _ in range(200):
                    self.input(run, soldier)
                self.assertIn(enemy, run.match.bots)
                self.assertTrue(enemy.retain_corpse)
                self.assertFalse(any(target.owner is enemy for target in run.match.combat._enemies(soldier)))
                self.assertEqual(npc.remove_spawned(run.match), (enemy.entity,))

    def test_authored_trooper_sniper_heavy_damage_soldier_and_respect_team_aim_and_wall(self):
        # The remaining two bodies do not yet emit volleys from slot 0. They
        # stay open; do not replace their authored weapons with a Soldier rifle.
        for key, amount in (("1", 20), ("3", 100), ("5", 5)):
            for case in ("hit", "ally", "miss", "wall"):
                with self.subTest(npc=self.catalog[key].name, case=case):
                    run, soldier, actor = self.arena(key, (0.0, 0.0, -7.0), team=0 if case == "ally" else 1)
                    # Exercise the authored raw slot, independently of the manual
                    # controller that would suppress allied/occluded/no-target fire.
                    actor.spawner = {**actor.spawner, "manual_spawn": False}
                    if case == "miss":
                        actor.yaw = 16384
                    if case == "wall":
                        run.match.collision_world = World([
                            (-10, -10, -5, 10, -10, -5, 10, 10, -5),
                            (-10, -10, -5, 10, 10, -5, -10, 10, -5),
                        ])
                    actor.firing = True
                    script = actor.body_script
                    script.advance_live(script.frame + 1, actor.position, weapon_input=(0, True))
                    pool = self.vitals(run, soldier)
                    observed = []
                    shoot = run.match.combat._shoot

                    def record(shooter, command, state, *args, observed=observed, shoot=shoot, **kwargs):
                        direct = combat.hit_def(state, state.node.field("m_6396149F"))
                        if direct is not None:
                            observed.append(direct.amount)
                        return shoot(shooter, command, state, *args, **kwargs)

                    with patch.object(run.match.combat, "_shoot", side_effect=record):
                        for frame in range(run.frame + 1, run.frame + 201):
                            run.match.update(frame * 0.016, frame, frames=False)
                            if case == "hit" and pool.total < 200:
                                break
                    self.assertTrue(observed)
                    self.assertEqual(observed[0], amount)
                    self.assertEqual(pool.total, 200 - amount if case == "hit" else 200)

    def test_duplicate_or_invalid_npc_weapon_input_cannot_create_extra_press_edges(self):
        def damage(repeat):
            run, soldier, actor = self.arena("1", (0.0, 0.0, -7.0))
            actor.spawner = {**actor.spawner, "manual_spawn": False}
            actor.firing = True
            script = actor.body_script
            for frame in range(run.frame + 1, run.frame + 101):
                if frame == run.frame + 1 or repeat:
                    script.advance_live(frame, actor.position, weapon_input=(0, True))
                run.match.update(frame * 0.016, frame, frames=False)
            return self.vitals(run, soldier).total

        ordinary = damage(False)
        self.assertLess(ordinary, 200)
        self.assertEqual(damage(True), ordinary)
        _run, _, actor = self.arena("1")
        script = actor.body_script
        before = (script.frame, tuple(script.component.held))
        for value in ((True, True), (0, 1), (99, True), [0, True]):
            with self.subTest(value=value), self.assertRaises(ValueError):
                script.advance_live(script.frame + 1, actor.position, weapon_input=value)
            self.assertEqual((script.frame, tuple(script.component.held)), before)

    def test_four_npc_primaries_resume_after_target_range_or_team_changes(self):
        for key in ("1", "2", "3", "5"):
            for gate in ("range", "team"):
                with self.subTest(npc=self.catalog[key].name, gate=gate):
                    run, soldier, actor = self.arena(key, (0, 0, 5))
                    soldier.mover.position = [0, 0, -3]
                    soldier.mover.body = None
                    pool = self.vitals(run, soldier)
                    actor.attack_target, actor.firing = soldier.entity, True
                    for _ in range(180):
                        run.step({})
                        if pool.total < 200:
                            break
                    self.assertLess(pool.total, 200)
                    wounded = pool.total
                    if gate == "range":
                        soldier.mover.position = [0, 0, -120]
                        soldier.mover.body = None
                    else:
                        actor.team = soldier.team
                    for _ in range(135):
                        run.step({})
                        self.assertFalse(actor.attack_input)
                    self.assertEqual(pool.total, wounded)
                    self.assertTrue(actor.firing)
                    self.assertEqual(actor.attack_target, soldier.entity)
                    soldier.mover.position = [0, 0, -3]
                    soldier.mover.body = None
                    actor.team = 1 - soldier.team
                    for _ in range(180):
                        run.step({})
                        if pool.total < wounded:
                            break
                    self.assertLess(pool.total, wounded)

    def test_four_npc_primaries_resume_when_soldier_leaves_static_cover(self):
        for key in ("1", "2", "3", "5"):
            with self.subTest(npc=self.catalog[key].name):
                run, soldier, actor = self.arena(key, (0, 0, 5))
                # Keep the left lane open and put cover on the right.
                run.match.collision_world = World([
                    (-50, 0, -50, -50, 0, 50, 50, 0, 50),
                    (-50, 0, -50, 50, 0, 50, 50, 0, -50),
                    (0, -10, 1, 10, -10, 1, 10, 10, 1),
                    (0, -10, 1, 10, 10, 1, 0, 10, 1),
                    (0, -10, 1, 10, 10, 1, 10, -10, 1),
                    (0, -10, 1, 0, 10, 1, 10, 10, 1),
                ])
                exposed = [-3, 0, -3]
                soldier.mover.position = exposed.copy()
                soldier.mover.body = None
                pool = self.vitals(run, soldier)
                actor.attack_target, actor.firing = soldier.entity, True
                for _ in range(180):
                    run.step({})
                    if pool.total < 200:
                        break
                self.assertLess(pool.total, 200)
                self.assertTrue(actor.attack_input)
                soldier.mover.position = [3, 0, -3]
                soldier.mover.body = None
                wounded = pool.total
                for _ in range(135):
                    run.step({})
                    self.assertFalse(actor.attack_input)
                self.assertEqual(pool.total, wounded)
                self.assertTrue(actor.firing)
                soldier.mover.position = exposed.copy()
                soldier.mover.body = None
                for _ in range(180):
                    run.step({})
                    if pool.total < wounded:
                        break
                self.assertLess(pool.total, wounded)

    def test_manual_controller_drives_three_authored_weapons_and_rejects_ally_or_wall(self):
        for key, amount in (("1", 20), ("3", 100), ("5", 5)):
            for case in ("hit", "ally", "wall"):
                with self.subTest(npc=self.catalog[key].name, case=case):
                    run, soldier, actor = self.arena(key, (0.0, 0.0, -7.0), team=0 if case == "ally" else 1)
                    if case == "wall":
                        run.match.collision_world = World([
                            (-10, -10, -5, 10, -10, -5, 10, 10, -5),
                            (-10, -10, -5, 10, 10, -5, -10, 10, -5),
                        ])
                    self.assertTrue(actor.spawner["manual_spawn"])
                    actor.attack_target, actor.firing = soldier.entity, True
                    pool = self.vitals(run, soldier)
                    for frame in range(run.frame + 1, run.frame + 201):
                        run.match.update(frame * 0.016, frame, frames=False)
                        if pool.total < 200:
                            break
                    self.assertEqual(pool.total, 200 - amount if case == "hit" else 200)
                    self.assertEqual(actor.attack_input, case == "hit")

    def test_npc_self_health_query_tracks_explicit_pools_without_supporting_foreign_queries(self):
        run, soldier, actor = self.arena("2")
        script = actor.body_script
        instance = next(iter(script.component.instances.values()))
        cfg = {"$": "STU_9B7648C3", "m_entity": {"$": "STU_B5A0CAF0"}}
        self.assertEqual(script.component.world.native(cfg, instance), 350)
        target = next(target for target in run.match.combat._targets() if target.owner is actor)
        run.match.combat._damage(soldier, target, 40, False, None, run.frame)
        self.assertEqual(script.component.world.native(cfg, instance), 310)
        script.component.set(65000, expr.Entity(soldier.body))
        foreign = {"$": "STU_9B7648C3", "m_entity": {
            "$": "STUConfigVarDynamic", "m_identifier": 65000, "m_60DB8F99": 1,
        }}
        self.assertEqual(instance.evaluate(foreign.get("m_entity")), expr.Entity(soldier.body))
        self.assertNotEqual(script.component.world.native(foreign, instance), 310)

    def test_passive_and_field_charge_are_separate_after_each_pve_attack(self):
        for key, definition in self.catalog.items():
            with self.subTest(npc=definition.name):
                distance = 2 if key == "4" else 8
                run, soldier, actor = self.arena(key, (0, 0, -3 + distance))
                soldier.mover.position = list(soldier.mover.position)
                soldier.mover.body = None
                script = soldier.body_script
                self.assertIsNone(script.ult_seconds)
                wait = ultimate.instance_of(script).states[1]
                first_counter = wait.counter
                charge = script.component.entity_var(342)
                pool = self.vitals(run, soldier)
                angles = aim(run.match.combat._eye(soldier), (0, .8, -3 + distance))
                run.step({soldier: (*angles, 0)})
                self.assertEqual(npc_control.start(run.match, soldier), actor.entity)

                for _ in range(180):
                    run.step({})
                    self.assertEqual(charge.value(), 5 * (wait.counter - first_counter))
                    if pool.total < 200:
                        break
                self.assertGreater(pool.total, 0)
                self.assertLess(pool.total, 200)
                if key == "4":
                    pins = run.match.combat.pins
                    previous_charge = charge.value()
                    for _ in range(64):
                        self.assertEqual(pins.get(actor.entity), soldier.body)
                        if charge.value() > previous_charge:
                            break
                        run.step({})
                    self.assertEqual(pins.get(actor.entity), soldier.body)
                    self.assertEqual(charge.value(), previous_charge + 5)
                    self.assertEqual(charge.value(), 5 * (wait.counter - first_counter))
                self.assertEqual(npc_control.stop(run.match), 1)
                self.assertFalse(run.match.combat.pins)
                for _ in range(20):
                    run.step({})
                wound = 200 - pool.total
                self.assertEqual(pool.ult_healing_deficit, wound)
                self.assertEqual(charge.value(), 5 * (wait.counter - first_counter))

                run.step({soldier: (0, 0, 16)})
                for _ in range(250):
                    run.step({})
                    if pool.total == 200:
                        break
                self.assertEqual(pool.total, 200)
                self.assertAlmostEqual(soldier.stats.value(stats.HEALING), wound)
                self.assertEqual(pool.ult_healing_deficit, 0)
                passive = 5 * (wait.counter - first_counter)
                self.assertGreater(passive, 0)
                self.assertAlmostEqual(charge.value(), passive + wound, places=3)

    def test_heavy_damage_fills_authored_ultimate_cost_then_activation_spends_and_blocks_gain(self):
        run, soldier, enemy = self.arena("5", (3.0, 0.0, 3.0))
        script = soldier.body_script
        script.ult_seconds = 0
        cost = script.component.vars[338].value()
        self.assertGreater(cost, 0)
        pool = self.vitals(run, enemy)
        yaw, pitch = aim(run.match.combat._eye(soldier), (3.0, 0.8, 3.0))
        for _ in range(5000):
            if script.component.vars[342].value() == cost:
                break
            rounds = script.component.instances[11].vars[53].value()
            self.input(run, soldier, 1 if rounds else 4, yaw, pitch)
        self.assertEqual(script.component.vars[342].value(), cost)
        self.assertLess(pool.total, 4000)
        self.input(run, soldier, 0x20)
        self.assertEqual(script.component.vars[342].value(), 0)
        for _ in range(80):
            self.input(run, soldier)
        before = pool.total
        self.input(run, soldier, 1)  # off-axis acquisition from the active ultimate
        self.assertEqual(pool.total, before - 20)
        self.assertEqual(script.component.vars[342].value(), 0)
        for _ in range(400):
            self.input(run, soldier)
        self.input(run, soldier, 1, yaw, pitch)
        self.assertEqual(script.component.vars[342].value(), 20)

    def test_earned_visor_hits_each_pve_body_and_player_then_expires(self):
        run, soldier, heavy = self.arena("5", (3, 0, 3))
        soldier.mover.position = [0, 0, -3]
        soldier.mover.body = None
        actors = [npc.spawn(run.match, self.catalog[key], (x, 0, 3), team=heavy.team)
                  for key, x in (("1", -3), ("2", -1.5), ("3", 0), ("4", 1.5))]
        actors.append(heavy)
        fight = run.match.combat
        script = soldier.body_script
        script.ult_seconds = 0
        charge = script.component.entity_var(342)
        cost = script.component.entity_var(338).value()
        ammo = script.component.instances[11].vars[53]
        pools = [self.vitals(run, actor) for actor in actors]
        initial = [pool.total for pool in pools]
        run.step({soldier: (0, 0, 0x20)})
        self.assertEqual(charge.value(), 0)
        for actor, pool in zip(actors, pools, strict=True):
            health, points = pool.total, charge.value()
            x, y, z = actor.position
            angles = aim(fight._eye(soldier), (x, y + .8, z))
            for _ in range(15):
                run.step({soldier: (*angles, 1)})
                if pool.total < health:
                    break
            self.assertLess(pool.total, health)
            self.assertAlmostEqual(charge.value() - points, health - pool.total)
        x, y, z = heavy.position
        heavy_angles = aim(fight._eye(soldier), (x, y + .8, z))
        for _ in range(5000):
            if charge.value() == cost:
                break
            run.step({soldier: (*heavy_angles, 1)})
        self.assertEqual(charge.value(), cost)
        earned_damage = sum(before - pool.total for before, pool in zip(initial, pools, strict=True))
        self.assertGreaterEqual(earned_damage, cost)
        self.assertLess(earned_damage, cost + 20)

        run.step({soldier: (0, 0, 0x20)})
        self.assertEqual(charge.value(), 0)
        for _ in range(100):
            run.step({})
        self.assertEqual(ammo.value(), 30)
        for selected, pool in zip(actors, pools, strict=True):
            for actor in actors:
                actor.position = (2, 0, 3) if actor is selected else (0, 0, -10)
            health = pool.total
            for _ in range(15):
                run.step({soldier: (0, 0, 1)})
                if pool.total < health:
                    break
            self.assertEqual(pool.total, health - 20)
            self.assertEqual(charge.value(), 0)

        heavy.position = (0, 0, -10)
        enemy = run.match.add_player(2, "Enemy", soldier.hero.guid, 1 - soldier.team, False)
        in_world(run.match, enemy)
        enemy.client = NpcRecordingClient(enemy)
        run.players.append(enemy)
        self.assertTrue(run.match.switch_hero(enemy, soldier.hero))
        enemy.mover.position = [2, 0, 3]
        enemy.mover.body = None
        pool = self.vitals(run, enemy)
        for _ in range(15):
            run.step({soldier: (0, 0, 1)})
            if pool.total < 200:
                break
        self.assertEqual(pool.total, 180)
        self.assertEqual(charge.value(), 0)
        for _ in range(10):
            run.step({})
        self.assertEqual(ammo.value(), 24)

        for _ in range(400):
            run.step({})
        health, rounds = pool.total, ammo.value()
        run.step({soldier: (0, 0, 1)})
        self.assertEqual(ammo.value(), rounds - 1)
        self.assertEqual(pool.total, health)
        self.assertEqual(charge.value(), 0)
        for _ in range(10):
            run.step({})
        angles = aim(fight._eye(soldier), (2, .8, 3))
        run.step({soldier: (*angles, 1)})
        self.assertEqual(pool.total, health - 20)
        self.assertEqual(charge.value(), 20)
        run.step({soldier: (0, 0, 0x20)})
        self.assertEqual(charge.value(), 20)


    def test_actual_control_cancels_earned_visor_then_combat_rearms_it(self):
        for control, wound in (("flashbang", 25), ("sleep", 5), ("assassin", 25)):
            with self.subTest(control=control):
                self.check_earned_visor_recovery(control, wound)

    def check_earned_visor_recovery(self, control, wound):
        run, soldier, heavy = self.arena("5", (3, 0, 3))
        soldier.mover.position = [0, 0, -3]
        soldier.mover.body = None
        if control == "assassin":
            caster = npc.spawn(run.match, self.catalog["4"], (0, 0, -1), team=heavy.team)
        else:
            hero = heroes()[0x02E000000000013B if control == "sleep" else 0x02E0000000000042]
            caster = run.match.add_player(2, hero.name, hero.guid, heavy.team, False)
            in_world(run.match, caster)
            caster.client = NpcRecordingClient(caster)
            run.players.append(caster)
            self.assertTrue(run.match.switch_hero(caster, hero))
            caster.mover.position = [0, 0, -.5]
            caster.mover.body = None
        fight = run.match.combat
        script = soldier.body_script
        script.ult_seconds = 0
        component = script.component
        cost, charge = component.entity_var(338).value(), component.entity_var(342)
        visor = next(i for i in component.instances.values() if i.graph.index == 0x0259)

        def earn_full(target):
            x, y, z = target.position
            angles = aim(fight._eye(soldier), (x, y + .8, z))
            pool = self.vitals(run, target)
            before, points = pool.total, charge.value()
            for _ in range(5000):
                if charge.value() == cost:
                    break
                run.step({soldier: (*angles, 1)})
            self.assertEqual(charge.value(), cost)
            self.assertGreaterEqual(before - pool.total, cost - points)
            self.assertLess(before - pool.total, cost - points + 20)

        self.assertEqual(charge.value(), 0)
        earn_full(heavy)
        run.step({soldier: (0, 0, 0x20)})
        windup, countdown = visor.states[43], visor.state(6)
        self.assertTrue(windup.active)
        self.assertEqual(charge.value(), 0)
        for _ in range(45 if control == "sleep" else 55):
            run.step({})
        self.assertTrue(windup.active)
        if control == "assassin":
            angles = aim(fight._eye(soldier), (0, .8, -1))
            run.step({soldier: (*angles, 0)})
            self.assertEqual(npc_control.start(run.match, soldier), caster.entity)
            aim_commands = {}
        else:
            angles = aim(fight._eye(caster), (0, 1, -3))
            run.step({caster: (*angles, 8 if control == "sleep" else 16)})
            aim_commands = {caster: (*angles, 0)}
        for _ in range(35):
            if fight.is_controlled(soldier):
                break
            run.step(aim_commands)
        self.assertTrue(fight.is_controlled(soldier))
        own = self.vitals(run, soldier)
        self.assertEqual(own.total, 200 - wound)
        self.assertFalse(windup.active)
        self.assertEqual(countdown.counter, 0)
        self.assertEqual(charge.value(), 0)
        if control == "assassin":
            self.assertEqual(fight.pins.get(caster.entity), soldier.body)
            self.assertGreater(npc_control.stop(run.match), 0)
        elif control == "sleep":
            self.assertIn(soldier.body, fight.sleeps)
        else:
            self.assertIn(soldier.body, fight.stuns)
        for _ in range(350):
            if not fight.is_controlled(soldier):
                break
            run.step({})
        self.assertFalse(fight.is_controlled(soldier))
        run.step({soldier: (0, 0, 0x20)})
        self.assertFalse(windup.active)
        self.assertEqual(countdown.counter, 0)

        run.step({soldier: (0, 0, 16)})
        for _ in range(120):
            run.step({})
            if own.total == 200:
                break
        self.assertEqual(own.total, 200)
        self.assertAlmostEqual(charge.value(), wound, places=4)
        heavy.position = (0, 0, -10)
        if control == "assassin":
            caster.position = (-8, 0, -5)
        else:
            caster.mover.position = [-8, 0, -5]
            caster.mover.body = None
        second = npc.spawn(run.match, self.catalog["5"], (3, 0, 3), team=heavy.team)
        earn_full(second)
        run.step({soldier: (0, 0, 0x20)})
        self.assertTrue(windup.active)
        self.assertEqual(charge.value(), 0)
        for _ in range(100):
            run.step({})
        self.assertTrue(countdown.active)
        pool = self.vitals(run, second)
        before = pool.total
        run.step({soldier: (0, 0, 1)})
        self.assertEqual(pool.total, before - 20)
        self.assertEqual(charge.value(), 0)


if __name__ == "__main__":
    unittest.main()
