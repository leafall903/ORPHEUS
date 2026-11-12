"""Actual owned D.Va Self-Destruct and its detached fuse against Hanzo."""

import unittest
from unittest.mock import patch

import test_game_hanzo_owned_mech as mech_tests
from test_game_combat import aim, in_world
from test_server_journal import wall_world

import hero_delivery_arrow_deflection as arrow_deflection
from ow174.game import combat, dva as dva_owned, zarya_barriers as zarya_barrier
from ow174.game.body_vm_snapshot import export_component_vm, snapshot_digest
from ow174.game.content import heroes
from ow174.game.script.driver import ULT_CHARGE, ULT_COST


class HanzoSelfDestructTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        mech_tests.HanzoOwnedMechTests.setUpClass()

    def arena(self, distance=8, health=200):
        run, pilot, mech, hanzo, _, _ = mech_tests.HanzoOwnedMechTests().arena(distance)
        fight = run.match.combat
        vitals = fight.vitals[hanzo.body] = combat.Vitals(hanzo.body, health)
        return run, pilot, mech, hanzo, vitals

    def cast(self, run, pilot, mech, *, charge=1540):
        mech.body_script.component.set(ULT_CHARGE, charge)
        run.step({pilot: (0, 0, 32)})
        run.step({pilot: (0, 0, 0)})

    def test_authored_resource_gate_detaches_and_refills_once_at_detonation(self):
        run, pilot, mech, hanzo, vitals = self.arena()
        component = mech.body_script.component
        pilot_component = pilot.body_script.component
        self.assertEqual(component.entity_var(ULT_COST).value(), 1540)
        self.cast(run, pilot, mech, charge=1539)
        self.assertIs(pilot.controlled_mech, mech)
        self.assertFalse(dva_owned.fuse_active(mech))
        self.assertEqual(component.entity_var(ULT_CHARGE).value(), 1539)
        with patch.object(run.match, "queue_body_message", wraps=run.match.queue_body_message) as queued:
            self.cast(run, pilot, mech)
            self.assertTrue(mech.detached_bomb)
            self.assertIsNone(pilot.controlled_mech)
            self.assertEqual(component.entity_var(ULT_CHARGE).value(), 0)
            self.assertEqual(pilot_component.entity_var(ULT_CHARGE).value(), 0)
            self.assertEqual(len(snapshot_digest(export_component_vm(component))), 64)
            for _ in range(186):
                run.step({})
            self.assertEqual(vitals.total, 200)
            self.assertEqual(pilot_component.entity_var(ULT_CHARGE).value(), 0)
            run.step({})
            self.assertTrue(vitals.dead)
            self.assertEqual(vitals.killer, mech.entity)
            self.assertEqual(pilot_component.entity_var(ULT_CHARGE).value(),
                             pilot_component.entity_var(ULT_COST).value())
            self.assertIn(mech, run.match.bots)
            for _ in range(22):
                run.step({})
            self.assertNotIn(mech, run.match.bots)
            refills = [call for call in queued.call_args_list
                       if call.args[:2] == (pilot.body, dva_owned.BOMB_FINISHED)]
            self.assertEqual(len(refills), 1)
            self.assertEqual(refills[0].args[3], mech.entity)
        hit, = pilot.client.messages(combat.HIT)
        self.assertEqual(hit["+0x78"],
                         {"+0x0": {"+0x0": hanzo.body}, "+0x4": 200, "+0x8": combat.KILLING})

    def test_radius_falloff_wall_and_faction(self):
        for distance, case, damage in ((3, "clear", 1000), (8, "clear", 714.0625),
                                       (15, "clear", 342.1875), (19, "clear", 129.6875),
                                       (21, "clear", 0), (8, "wall", 0), (8, "ally", 0)):
            with self.subTest(distance=distance, case=case):
                run, pilot, mech, hanzo, vitals = self.arena(distance, health=1200)
                if case == "wall":
                    run.match.collision_world = wall_world()
                elif case == "ally":
                    pilot.team = mech.team = hanzo.team
                self.cast(run, pilot, mech)
                for _ in range(211):
                    run.step({})
                self.assertEqual(vitals.total, 1200 - damage)
                self.assertNotIn(mech, run.match.bots)

    def add_player(self, run, hanzo, hero, x, z):
        match = run.match
        player = match.add_player(3, "Defender", hero, hanzo.team, False)
        in_world(match, player)
        run.players.append(player)
        position = (x, hanzo.mover.position[1], z)
        player.spawn = position, 180
        self.assertTrue(match.switch_hero(player, heroes()[hero]))
        player.mover.position[:] = position
        player.mover.body = None
        for _ in range(80):
            run.step({})
        return player

    def test_projected_barrier_breaks_without_exposing_hanzo_to_same_blast(self):
        run, pilot, mech, hanzo, vitals = self.arena()
        fight = run.match.combat
        zarya = self.add_player(run, hanzo, zarya_barrier.HERO, 4, 10)
        x, y, z = hanzo.mover.position
        angles = aim(fight._eye(zarya), (x, y + 1, z))
        self.cast(run, pilot, mech)
        for _ in range(150):
            run.step({})
        run.step({zarya: (*angles, 16)})
        self.assertEqual(fight.zarya_barriers.projected[zarya.body][1:], (200, hanzo.body))
        for _ in range(65):
            run.step({})
        self.assertEqual(vitals.total, 200)
        self.assertNotIn(zarya.body, fight.zarya_barriers.projected)
        self.assertNotIn(mech, run.match.bots)

    def test_genji_deflect_does_not_turn_radial_detonation(self):
        run, pilot, mech, hanzo, vitals = self.arena()
        fight = run.match.combat
        genji = self.add_player(run, hanzo, arrow_deflection.GENJI, 0, 4)
        self.cast(run, pilot, mech)
        for _ in range(160):
            run.step({})
        run.step({genji: (-32768, 0, 16)})
        target = next(target for target in fight._targets() if target.owner is genji)
        self.assertIsNotNone(arrow_deflection.active_volume(target, 0x03A3, 10))
        for _ in range(60):
            run.step({genji: (-32768, 0, 16)})
        self.assertTrue(vitals.dead)
        self.assertTrue(fight.vitals[genji.body].dead)
        self.assertEqual(vitals.killer, mech.entity)
        self.assertEqual(fight.vitals[genji.body].killer, mech.entity)

    def test_world_tick_gap_keeps_original_detonation_tick_and_single_refill(self):
        run, pilot, mech, _, vitals = self.arena()
        self.cast(run, pilot, mech)
        cast_frame = run.frame - 1
        run.frame += 220
        run.step({})
        self.assertEqual(vitals.death_tick, cast_frame + 188)
        self.assertNotIn(mech, run.match.bots)
        component = pilot.body_script.component
        self.assertEqual(component.entity_var(ULT_CHARGE).value(), component.entity_var(ULT_COST).value())
        self.assertEqual(len(pilot.client.messages(combat.HIT)), 1)

    def test_surviving_hanzo_receives_outer_ring_impulse(self):
        run, pilot, mech, hanzo, vitals = self.arena(8, health=1200)
        self.cast(run, pilot, mech)
        for _ in range(187):
            run.step({})
        self.assertEqual(vitals.total, 485.9375)
        self.assertGreater(hanzo.mover.velocity[1], 0)
        self.assertGreater(hanzo.mover.velocity[2], 0)

    def test_hanzo_can_kill_ejected_pilot_while_detached_fuse_continues(self):
        run, pilot, mech, hanzo, vitals = self.arena(8, health=1200)
        fight = run.match.combat
        x, y, z = mech.position
        angles = aim(fight._eye(hanzo), (x, y + 1.1, z))
        for _ in range(70):
            run.step({hanzo: (*angles, 1)})
        mech.body_script.component.set(ULT_CHARGE, 1540)
        run.step({pilot: (0, 0, 32), hanzo: (*angles, 1)})
        run.step({hanzo: (*angles, 1)})
        self.assertIsNone(pilot.controlled_mech)
        pilot_vitals = fight.vitals[pilot.body] = combat.Vitals(pilot.body, 1)
        run.step({hanzo: (*angles, 0)})
        for _ in range(20):
            run.step({})
        self.assertTrue(pilot_vitals.dead)
        self.assertEqual(pilot_vitals.killer, hanzo.body)
        self.assertIn(mech, run.match.bots)
        self.assertEqual(vitals.total, 1200)
        for _ in range(195):
            run.step({})
        self.assertEqual(vitals.total, 485.9375)
        self.assertNotIn(mech, run.match.bots)

    def test_boosting_bomb_coasts_and_respects_world_collision(self):
        for wall, damage in ((False, 786.6424560546875), (True, 0)):
            with self.subTest(wall=wall):
                run, pilot, mech, _, vitals = self.arena(9, health=1200)
                if wall:
                    run.match.collision_world = wall_world()
                for _ in range(10):
                    run.step({pilot: (0, 0, 8)})
                self.assertGreater(mech.velocity[2], 10)
                launch = mech.position[2]
                self.cast(run, pilot, mech)
                for _ in range(10):
                    run.step({})
                self.assertGreater(mech.position[2], launch)
                self.assertEqual(mech.velocity, (0, 0, 0))
                rest = mech.position
                for _ in range(177):
                    run.step({})
                self.assertEqual(mech.position, rest)
                self.assertEqual(1200 - vitals.total, damage)
                for _ in range(25):
                    run.step({})
                self.assertNotIn(mech, run.match.bots)
