"""Actual Orisa deployable and Bastion 104319 weapon interception."""

import os
import unittest
from dataclasses import replace

import test_bastion_combat as kit
from test_game_combat import aim

from ow174.game import combat, defensive_casts


@unittest.skipUnless(os.environ.get("ORPHEUS_ORISA_GEOMETRY"), "104319 Orisa mesh required")
class BastionOrisaBarrierTests(unittest.TestCase):
    def arena(self, mode="recon"):
        case = kit.BastionCombatTests()
        case.fixture(enemy_position=(0, 1, 16), bystander_position=(0, 1, 19),
                     enemy_hero=defensive_casts.ORISA)
        if mode == "sentry":
            case.configure()
        elif mode == "tank":
            case.tank()
        return case

    def land(self, case):
        barriers = case.fight.orisa_barriers
        active_before = tuple(barriers.active)
        for frame in range(24):
            case.run.step({case.enemy: (-32768, 5000, 16 if frame == 0 else 0)})
            if barriers.flights:
                break
        self.assertEqual(len(barriers.flights), 1)
        flight, = barriers.flights
        self.assertAlmostEqual(flight.gravity, 9.8, places=5)
        self.assertEqual(tuple(barriers.active), active_before)
        for _ in range(100):
            case.step()
            if barriers.pending:
                break
        self.assertFalse(barriers.flights)
        (due, landed), = barriers.pending
        self.assertIs(landed, flight)
        self.assertGreater(due, case.fight.tick)
        self.assertEqual(tuple(barriers.active), active_before)
        return due, landed

    def deploy(self, case):
        due, _ = self.land(case)
        case.step(0, due - case.fight.tick)
        shield, = case.fight.orisa_barriers.active.values()
        self.assertEqual(shield.health, 600)
        self.assertEqual(shield.until - due, 1250)
        self.assertAlmostEqual(shield.position[1], 1, places=5)
        self.assertLess(shield.position[2], 16)
        self.assertTrue(shield.blocking())
        return shield

    def test_actual_mesh_stops_each_bastion_gun_before_orisa(self):
        for mode, frames, loss in (("recon", 10, 40), ("sentry", 10, 75), ("tank", 1, 205)):
            with self.subTest(mode=mode):
                case = self.arena(mode)
                shield = self.deploy(case)
                health = case.health(case.enemy)
                before = health.total
                case.step(1, frames)
                case.step(0, 30)
                self.assertAlmostEqual(600 - shield.health, loss, places=4)
                self.assertEqual(health.total, before)
                self.assertFalse(case.player.client.messages(combat.HIT))
                self.assertFalse(case.fight.rockets)

    def test_tank_and_deployment_keep_contact_order_across_world_gaps(self):
        for close in (True, False):
            for gap in (0, 30, 100):
                with self.subTest(close=close, gap=gap):
                    case = self.arena("tank")
                    due, landed = self.land(case)
                    if close:
                        x, y, z = landed.position
                        case.player.mover.position[:] = (x, y, z - 1.5)
                        case.player.mover.body = None
                        case.command_aim = aim(case.fight._eye(case.player), (0, 2, 16))
                    health = case.health(case.enemy)
                    before = health.total
                    case.step(1)
                    case.step()
                    rocket, = case.fight.rockets
                    self.assertIsNotNone(rocket.last_tick)
                    self.assertLess(case.fight.tick, due)
                    if gap:
                        case.run.frame += gap
                        case.run.match.update(case.run.frame * combat.TICK, case.run.frame)
                    else:
                        case.step(0, 30)
                    shield, = case.fight.orisa_barriers.active.values()
                    self.assertEqual(shield.until, due + 1250)
                    self.assertAlmostEqual(600 - shield.health, 0 if close else 205, places=5)
                    self.assertAlmostEqual(before - health.total, 195 if close else 0, places=5)
                    self.assertFalse(case.fight.rockets)

    def test_tank_body_impact_can_splash_mesh_unless_a_wall_blocks(self):
        for scenario in ("clear", "wall", "break"):
            with self.subTest(scenario=scenario):
                case = self.arena("tank")
                shield = self.deploy(case)
                x, y, z = shield.position
                if scenario == "break":
                    case.fight.orisa_barriers.damage(shield, 599)
                    case.enemy.mover.position[:] = (x, y, z + 0.6)
                    case.enemy.mover.body = None
                target = case.bystander
                target.mover.position[:] = (x, y, z - 1.4)
                target.mover.body = None
                case.command_aim = aim(case.fight._eye(case.player), (x, y + 1, z - 1.4))
                if scenario == "wall":
                    case.wall(z - 0.65)
                case.step(0, 5)
                front = case.health(target)
                owner = case.health(case.enemy)
                before = owner.total
                case.step(1)
                case.step(0, 30)
                self.assertTrue(front.dead)
                self.assertEqual(owner.total, before)
                if scenario == "wall":
                    self.assertEqual(shield.health, 600)
                elif scenario == "break":
                    self.assertEqual(shield.health, 0)
                    self.assertNotIn(shield.entity, case.fight.orisa_barriers.active)
                else:
                    self.assertGreater(600 - shield.health, 0)
                    self.assertLessEqual(600 - shield.health, 140)

    def test_tank_breaks_deployed_mesh_without_damage_spill(self):
        case = self.arena("tank")
        shield = self.deploy(case)
        health = case.health(case.enemy)
        before = health.total
        case.step(1, 140)
        case.step(0, 25)
        self.assertEqual(shield.health, 0)
        self.assertNotIn(shield.entity, case.fight.orisa_barriers.active)
        self.assertEqual(health.total, before)
        self.assertFalse(case.player.client.messages(combat.HIT))
        case.step(0, 70)
        case.step(1)
        case.step(0, 25)
        self.assertAlmostEqual(before - health.total, 195, places=4)

    def test_barrier_recast_preserves_old_mesh_until_new_deployment_then_expires(self):
        case = self.arena()
        barriers = case.fight.orisa_barriers
        old = self.deploy(case)
        orisa = case.enemy
        instance = next(i for i in orisa.body_script.component.instances.values() if i.graph.index == 0x0BD7)
        ability = instance.states[3]
        self.assertGreater(ability.remaining(), 0)
        case.run.step({orisa: (-32768, 5000, 16)})
        case.step(0, 25)
        self.assertFalse(barriers.flights)
        self.assertFalse(barriers.pending)
        self.assertEqual(tuple(barriers.active.values()), (old,))
        for _ in range(800):
            if ability.remaining() == 0:
                break
            case.step()
        self.assertEqual(ability.remaining(), 0)
        due, _ = self.land(case)
        case.step(0, due - case.fight.tick - 1)
        self.assertEqual(tuple(barriers.active.values()), (old,))
        case.step()
        new, = barriers.active.values()
        self.assertIsNot(new, old)
        self.assertEqual(new.health, 600)
        self.assertNotIn(old.entity, barriers.active)
        self.assertNotIn(old.entity, barriers.retiring)
        owner_input = orisa.commands.last_frame
        case.run.frame = new.until - 1
        case.run.match.update(case.run.frame * combat.TICK, case.run.frame)
        self.assertIn(new.entity, barriers.active)
        case.run.frame += 1
        case.run.match.update(case.run.frame * combat.TICK, case.run.frame)
        self.assertFalse(barriers.active)
        self.assertEqual(orisa.commands.last_frame, owner_input)
        before = case.health(orisa).total
        case.step(1, 10)
        self.assertEqual(before - case.health(orisa).total, 30)

    def test_friendly_mesh_and_world_occlusion_preserve_durability(self):
        for friendly in (False, True):
            with self.subTest(friendly=friendly):
                case = self.arena()
                if friendly:
                    case.run.match.game_map = replace(case.run.match.game_map, free_for_all=False)
                    case.player.team = case.enemy.team = 0
                    case.bystander.team = 1
                shield = self.deploy(case)
                if not friendly:
                    case.wall(5)
                behind = case.health(case.bystander)
                before = behind.total
                case.step(1, 10)
                self.assertEqual(shield.health, 600)
                self.assertEqual(before - behind.total, 40 if friendly else 0)


if __name__ == "__main__":
    unittest.main()
