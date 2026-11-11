
"""Sombra's actual inputs against Sigma's Experimental Barrier."""

import os
import unittest
from dataclasses import replace
from pathlib import Path

import test_game_sombra_combat as scenes
import test_game_sombra_grenade as grenades
import test_game_sombra_npc as npc_fixture
from test_game_combat import aim
from test_game_sombra_orisa_halt import place
from test_game_sombra_reaper_blossom import charge
from test_game_sombra_sigma import cast, prepare

from ow174.game import combat, npc_control, props, sigma_barrier
from ow174.game.commands import SECONDARY, Command
from ow174.game.content import SOLDIER, heroes
from ow174.game.server_snapshot import SCHEMA, SCOPE, _Graph, snapshot_digest
from ow174.game.world import HEALTH_POOL, SHIELDS_POOL

MODEL = os.environ.get("ORPHEUS_SIGMA_BARRIER_MODEL")
FORWARD = (0, 1.55, 0)


@unittest.skipUnless(MODEL, "requires the verified external Sigma C015 model")
class SombraSigmaBarrierTests(unittest.TestCase):
    def arena(self, deploy=True):
        fixture = scenes.SombraCombatTests()
        fixture.setUp()
        prepare(fixture)
        self.fight = fixture.run.match.combat
        self.barrier, = self.fight.barriers.values()
        if deploy:
            self.hold(fixture, 10)
            cast(fixture, 0, FORWARD, 18)
            self.assertEqual(self.barrier.phase, sigma_barrier.PARKED)
            self.assertTrue(self.barrier.active())
        return fixture

    @staticmethod
    def hold(fixture, frames, point=FORWARD):
        for _ in range(frames):
            cast(fixture, SECONDARY, point)

    def test_original_mesh_and_actual_pistol_contact_without_ult_credit(self):
        f = self.arena()
        raw = Path(MODEL).read_bytes()
        self.assertEqual(props.model_shapes(raw, 0xFFFF), [])
        shape, = props.model_shapes(raw, 0xFFFF, include_dynamic=True)
        self.assertEqual((len(shape[2]), len(shape[3])), (62, 120))
        self.assertEqual(len(self.barrier.mesh), 120)
        self.assertEqual(self.barrier.vitals.total, 700)
        f.press(1, 20)
        self.assertEqual(self.barrier.vitals.total, 644)
        self.assertEqual(f.health(f.b), 400)
        self.assertEqual(charge(f.a), 0)

    def test_recall_and_original_recharge_delay_with_world_gap(self):
        f = self.arena()
        f.press(1, 20)
        damaged = self.barrier.vitals.total
        f.press(0, 140)
        self.assertEqual(self.barrier.vitals.total, damaged)
        cast(f, SECONDARY, FORWARD, 2)
        self.assertFalse(self.barrier.active())
        self.assertAlmostEqual(self.barrier.recharge_at - self.fight.now, 2)
        self.assertGreater(self.barrier.instance.states[111].remaining(), 1.9)
        run = f.run
        run.frame += 124
        run.match.update(run.frame * combat.TICK, run.frame, frames=False)
        self.assertEqual(self.barrier.vitals.total, damaged)
        run.frame += 26
        run.match.update(run.frame * combat.TICK, run.frame, frames=False)
        self.assertAlmostEqual(self.barrier.vitals.total, damaged + 32, places=4)
        run.frame += 50
        run.match.update(run.frame * combat.TICK, run.frame, frames=False)
        self.assertEqual(self.barrier.vitals.total, 700)

    def test_continuous_hold_survives_missing_input_and_release_parks_at_wall(self):
        f = self.arena(deploy=False)
        f.geometry((-5, 0, -7, 5, 0, -7, 5, 5, -7), (-5, 0, -7, 5, 5, -7, -5, 5, -7))
        self.hold(f, 12)
        start = self.barrier.position[2]
        run = f.run
        run.frame += 35
        run.match.update(run.frame * combat.TICK, run.frame, frames=False)
        self.assertAlmostEqual(self.barrier.position[2], start - 35 * combat.TICK * 16.5, places=4)
        f.b.take_commands([Command(run.frame, yaw=-32768, buttons=SECONDARY)])
        self.hold(f, 28)
        self.assertEqual(self.barrier.phase, sigma_barrier.TRAVELLING)
        self.assertAlmostEqual(self.barrier.position[2], -6.1, places=4)
        cast(f, 0, FORWARD, 22)
        self.assertEqual(self.barrier.phase, sigma_barrier.PARKED)
        self.assertAlmostEqual(self.barrier.position[2], -6.1, places=4)

    def test_toggle_releases_without_parking_and_later_presses_park_and_recall(self):
        f = self.arena(deploy=False)
        f.b.body_script.component.set(23619, True)
        self.hold(f, 8)
        start = self.barrier.position[2]
        cast(f, 0, FORWARD, 14)
        self.assertEqual(self.barrier.phase, sigma_barrier.TRAVELLING)
        self.assertAlmostEqual(self.barrier.position[2], start - 14 * combat.TICK * 16.5, places=4)
        cast(f, SECONDARY, FORWARD, 23)
        self.assertEqual(self.barrier.phase, sigma_barrier.PARKED)
        cast(f, SECONDARY, FORWARD, 2)
        self.assertEqual(self.barrier.phase, sigma_barrier.STORED)

    def test_rear_hack_parks_a_travelling_mesh_while_secondary_is_held(self):
        f = self.arena(deploy=False)
        place(f.a, (0, 0, 8))
        self.hold(f, 12)
        self.assertEqual(self.barrier.phase, sigma_barrier.TRAVELLING)
        attack = aim(self.fight._eye(f.a), (0, 1.2, 6))
        deploy = aim(self.fight._eye(f.b), FORWARD)
        for _ in range(85):
            f.run.step({f.a: (*attack, 2), f.b: (*deploy, SECONDARY)})
            if f.b.body in self.fight.hacks:
                break
        self.assertIn(f.b.body, self.fight.hacks)
        self.hold(f, 2)
        self.assertEqual(self.barrier.phase, sigma_barrier.PARKED)
        parked = self.barrier.position
        self.hold(f, 35)
        self.assertEqual(self.barrier.position, parked)

    def test_real_hack_is_blocked_from_front_but_rear_hack_leaves_parked_mesh(self):
        f = self.arena()
        f.press(2, 82)
        self.assertNotIn(f.b.body, self.fight.hacks)
        self.assertEqual(self.barrier.vitals.total, 700)
        place(f.a, (0, 0, 8))
        f.press(0, 8)
        f.press(2, 82)
        self.assertIn(f.b.body, self.fight.hacks)
        cast(f, SECONDARY, FORWARD, 8)
        self.assertTrue(self.barrier.active())
        self.assertEqual(self.barrier.phase, sigma_barrier.PARKED)

    def test_actual_emp_breaks_screen_and_drains_owner_without_refund_or_damage_credit(self):
        f = self.arena()
        f.a.body_script.set_ult_charge(1400)
        f.press(32, 48)
        self.assertEqual(self.barrier.vitals.total, 0)
        self.assertEqual(self.barrier.phase, sigma_barrier.STORED)
        self.assertFalse(self.barrier.active())
        self.assertIn(f.b.body, self.fight.hacks)
        self.assertEqual(f.health(f.b), 300)
        self.assertEqual(charge(f.a), 0)

    def test_emp_hits_owner_but_excludes_the_parked_mesh_outside_range(self):
        f = self.arena(deploy=False)
        self.hold(f, 105)
        cast(f, 0, FORWARD, 16)
        self.assertLess(self.barrier.position[2], -18)
        self.assertTrue(self.barrier.active())
        f.a.body_script.set_ult_charge(1400)
        f.press(32, 48)
        self.assertEqual(f.health(f.b), 300)
        self.assertIn(f.b.body, self.fight.hacks)
        self.assertEqual(self.barrier.vitals.total, 700)
        self.assertTrue(self.barrier.active())

    def test_breaking_pistol_shot_has_no_spill_and_next_round_reaches_body(self):
        f = self.arena()
        self.barrier.vitals.now[HEALTH_POOL] = 4
        f.press(1)
        self.assertEqual(self.barrier.vitals.total, 0)
        self.assertEqual(f.health(f.b), 400)
        self.assertEqual(charge(f.a), 0)
        f.press(0, 12)
        self.assertEqual(self.barrier.phase, sigma_barrier.STORED)
        self.assertGreater(self.barrier.instance.states[111].remaining(), 4.5)
        f.press(1)
        self.assertEqual(f.health(f.b), 392)
        self.assertEqual(charge(f.a), 8)

    def test_allied_sombra_round_passes_mesh_to_enemy(self):
        f = self.arena()
        match = f.run.match
        match.game_map = replace(match.game_map, free_for_all=False)
        f.a.team = f.b.team = 0
        third = grenades.SombraGrenadeTests().join_ally(f, SOLDIER, (0, 0, 8))
        third.team = 1
        f.press(1, target=third)
        self.assertEqual(self.barrier.vitals.total, 700)
        self.assertLess(f.health(third), 200)

    def test_actual_quick_melee_reaches_body_through_the_mesh(self):
        f = self.arena(deploy=False)
        place(f.b, (0, 0, 1.9))
        self.hold(f, 7)
        cast(f, 0, FORWARD, 15)
        self.assertTrue(self.barrier.active())
        f.press(0x800)
        f.press(0, 24)
        self.assertEqual(f.health(f.b), 370)
        self.assertEqual(self.barrier.vitals.total, 700)
        self.assertEqual(charge(f.a), 30)

    def test_actual_active_mesh_enters_snapshot_and_damage_changes_digest(self):
        f = self.arena()
        match = f.run.match

        def digest():
            stored = _Graph({"match": match, "sigma": f.b}, None)
            roots = {"shield": stored.value(self.barrier)}
            return snapshot_digest({"schema": SCHEMA, "scope": SCOPE, "restorable": False,
                                    "roots": roots, "nodes": stored.finish(), "assets": []})

        before = digest()
        self.assertEqual(digest(), before)
        f.press(1)
        self.assertLess(self.barrier.vitals.total, 700)
        self.assertNotEqual(digest(), before)

    def test_replacement_and_actual_lethal_pistol_retire_the_mesh(self):
        for death in (False, True):
            with self.subTest(death=death):
                f = self.arena()
                if death:
                    vitals = self.fight.vitals[f.b.body]
                    vitals.now[HEALTH_POOL], vitals.now[SHIELDS_POOL] = 8, 0
                    place(f.a, (0, 0, 8))
                    f.press(0, 6)
                    f.press(1)
                    self.assertTrue(self.fight.is_dead(f.b))
                else:
                    self.assertTrue(f.run.match.switch_hero(f.b, heroes()[SOLDIER]))
                self.assertNotIn(self.barrier.entity, self.fight.barriers)


@unittest.skipUnless(MODEL, "requires the verified external Sigma C015 model")
class NpcSigmaBarrierTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        npc_fixture.SombraActualNpcTests.setUpClass.__func__(cls)

    @classmethod
    def restore_graphs(cls, path):
        npc_fixture.SombraActualNpcTests.restore_graphs(path)

    def test_actual_npc_weapons_meet_the_screen_or_assassin_passes_it(self):
        for key, definition in self.catalog.items():
            with self.subTest(npc=definition.name):
                f, bot = npc_fixture.SombraActualNpcTests.make(self, key)
                prepare(f)
                fight = f.run.match.combat
                barrier, = fight.barriers.values()
                place(f.a, (8, 0, 0))
                bot.position = (0, 0, 0)
                if key == "4":
                    place(f.b, (0, 0, 2.5))  # inside the manual Assassin contact range
                SombraSigmaBarrierTests.hold(f, 7 if key == "4" else 10)
                cast(f, 0, FORWARD, 16)
                self.assertTrue(barrier.active())
                cast(f, 0, (0, .85, 0))
                self.assertEqual(npc_control.start(f.run.match, f.b), bot.entity)
                for _ in range(420):
                    f.press(0)
                    if barrier.vitals.total < 700 or f.health(f.b) < 400:
                        break
                if key == "4":
                    self.assertLess(f.health(f.b), 400)
                    self.assertEqual(barrier.vitals.total, 700)
                else:
                    self.assertLess(barrier.vitals.total, 700)
                    self.assertEqual(f.health(f.b), 400)
