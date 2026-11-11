"""Actual 104319 inputs across Sigma and Reinhardt combat graphs."""

import os
import unittest
from unittest.mock import patch

import test_sigma_combat as fixtures
from test_game_combat import aim

from ow174.game import combat, reinhardt_barrier, sigma
from ow174.game.commands import FIRE, SECONDARY
from ow174.game.content import SOLDIER, heroes
from ow174.game.script import nodes


class SigmaFireStrikeTests(unittest.TestCase):
    @unittest.skipUnless(os.environ.get("ORPHEUS_SIGMA_BARRIER_MODEL"), "requires the external C015 mesh")
    def test_fire_strike_crosses_experimental_barrier_until_grasp_or_world_cover(self):
        for case in ("clear", "grasp", "wall"):
            with self.subTest(case=case):
                fixture = fixtures.SigmaCombatTests()
                run, player, reinhardt, _ = fixture.arena(offset=0, distance=8)
                position = list(reinhardt.mover.position)
                run.match.switch_hero(reinhardt, heroes()[0x02E0000000000007])
                reinhardt.mover.position = position
                if case == "wall":
                    fixture.wall(run, sideways=False, offset=5)
                for frame in range(40):
                    run.step({player: (0, 0, SECONDARY if frame < 10 else 0)})
                fight = run.match.combat
                barrier, = fight.barriers.values()
                self.assertTrue(barrier.active)
                target = next(target for target in fight._targets() if target.owner is player)
                health = fight.vitals_of(target)
                if case == "grasp":
                    run.step({player: (0, 0, 0x08)})
                    grasp = fixture.ability(player, sigma.GRASP).instance
                    self.assertTrue(grasp.states[1].active)
                x, y, z = player.mover.position
                yaw, pitch = aim(fight._eye(reinhardt), (x, y + 1, z))
                rocket = None
                with patch.object(fight, "_shoot", wraps=fight._shoot) as shoots:
                    for frame in range(150):
                        run.step({reinhardt: (yaw, pitch, 0x10 if frame in (0, 80) else 0)})
                        if rocket is None and fight.rockets:
                            rocket = fight.rockets[0]
                fired_graphs = [call.args[2].instance.graph.index for call in shoots.call_args_list]
                self.assertEqual(fired_graphs, [0x00E2])
                self.assertEqual(barrier.vitals.total, 700 if case == "wall" else 600)
                self.assertEqual(health.now[combat.SHIELDS_POOL], 0 if case == "clear" else 100)
                self.assertEqual(health.now[combat.HEALTH_POOL], 300)
                self.assertEqual(len(reinhardt.client.messages(combat.HIT)), int(case == "clear"))
                if case == "grasp":
                    self.assertEqual(grasp.vars[21312].value(), 100)
                    self.assertAlmostEqual(health.temporary_shields[sigma.SHIELD_TAG].now, 60, places=4)
                if case == "clear":
                    self.assertIsNotNone(rocket)
                    self.assertIn((player.body, fight.replacement_ticks.get(player.body)), rocket.penetrated)
                    self.assertIn((barrier.entity, None), rocket.penetrated)
                self.assertFalse(fight.rockets)

    def test_accretion_interrupts_fire_strike_windup_then_fresh_input_recovers(self):
        fixture = fixtures.SigmaCombatTests()
        run, player, reinhardt, _ = fixture.arena(offset=0, distance=8)
        position = list(reinhardt.mover.position)
        run.match.switch_hero(reinhardt, heroes()[0x02E0000000000007])
        reinhardt.mover.position = position
        fight = run.match.combat
        health = {target.owner: fight.vitals_of(target) for target in fight._targets()}
        x, y, z = reinhardt.mover.position
        rock_aim = aim(fight._eye(player), (x, y + 1, z))
        x, y, z = player.mover.position
        strike_aim = aim(fight._eye(reinhardt), (x, y + 1, z))
        run.step({player: (*rock_aim, 0x10)})
        for _ in range(75):
            if fight.rockets:
                break
            run.step({player: (*rock_aim, 0)})
        rock, = fight.rockets
        self.assertIs(rock.shooter, player)
        run.step({reinhardt: (*strike_aim, 0x10)})
        instances = reinhardt.body_script.component.instances.values()
        instance = next(i for i in instances if i.graph.index == 0x00E2)
        ability = instance.states[9]
        self.assertTrue(ability.flags & nodes.Ability.RUNNING)
        with patch.object(fight, "_shoot", wraps=fight._shoot) as shoots:
            for _ in range(30):
                if fight.is_stunned(reinhardt):
                    break
                run.step({reinhardt: (*strike_aim, 0)})
            self.assertTrue(fight.is_stunned(reinhardt))
            self.assertFalse(ability.flags & nodes.Ability.RUNNING)
            self.assertEqual(health[reinhardt].total, 440)
            for _ in range(100):
                run.step({reinhardt: (*strike_aim, 0)})
            self.assertFalse(shoots.called)
            self.assertEqual(health[player].total, 400)
            run.step({reinhardt: (*strike_aim, 0x10)})
            for _ in range(70):
                run.step({reinhardt: (*strike_aim, 0)})
            self.assertEqual(len(shoots.call_args_list), 1)
        self.assertEqual(health[player].total, 300)

    def test_body_replacement_retires_fire_strike_before_it_reaches_sigma(self):
        run, player, reinhardt, _ = fixtures.SigmaCombatTests().arena(offset=0, distance=14)
        position = list(reinhardt.mover.position)
        run.match.switch_hero(reinhardt, heroes()[0x02E0000000000007])
        reinhardt.mover.position = position
        fight = run.match.combat
        target = next(target for target in fight._targets() if target.owner is player)
        health = fight.vitals_of(target)
        x, y, z = player.mover.position
        yaw, pitch = aim(fight._eye(reinhardt), (x, y + 1, z))
        run.step({reinhardt: (yaw, pitch, 0x10)})
        for _ in range(60):
            if fight.rockets:
                break
            run.step({reinhardt: (yaw, pitch, 0)})
        rocket, = fight.rockets
        self.assertEqual(rocket.definition, 0x04000000000001EA)
        self.assertEqual(rocket.owner_body, reinhardt.body)
        self.assertFalse(rocket.penetrated)
        self.assertTrue(run.match.switch_hero(reinhardt, heroes()[SOLDIER]))
        self.assertFalse(fight.rockets)
        for _ in range(60):
            run.step({})
        self.assertEqual(health.total, 400)
        self.assertFalse(reinhardt.client.messages(combat.HIT))


@unittest.skipUnless(os.environ.get("ORPHEUS_REINHARDT_COLLISION"), "requires the external A3C mesh")
class SigmaReinhardtBarrierTests(unittest.TestCase):
    def raised_field(self, distance=8):
        run, player, reinhardt, _ = fixtures.SigmaCombatTests().arena(offset=0, distance=distance)
        position = list(reinhardt.mover.position)
        self.assertTrue(run.match.switch_hero(reinhardt, heroes()[reinhardt_barrier.HERO]))
        reinhardt.mover.position = position
        player.body_script.ult_seconds = 0
        player.body_script.set_ult_charge(0)
        fight = run.match.combat
        held = {reinhardt: (-32768, 0, SECONDARY)}
        for _ in range(45):
            run.step(held)
        field, = (barrier for barrier in fight.barriers.values() if barrier.owner is reinhardt)
        self.assertTrue(field.active)
        self.assertEqual(field.vitals.total, 1600)
        self.assertTrue(field.instance.states[17].active)
        health = next(fight.vitals_of(t) for t in fight._targets() if t.owner is reinhardt)
        return run, player, reinhardt, held, field, health

    def test_hyperspheres_stop_until_a_prior_sphere_breaks_the_field(self):
        for breaking in (False, True):
            with self.subTest(breaking=breaking):
                run, player, reinhardt, held, field, health = self.raised_field()
                fight = run.match.combat
                if breaking:
                    field.vitals.damage(1560)
                yaw, pitch = aim(fight._eye(player), fight._eye(reinhardt))
                for frame in range(105):
                    run.step({**held, player: (yaw, pitch, FIRE if frame == 0 else 0)})
                self.assertEqual(field.vitals.total, 0 if breaking else 1490)
                self.assertEqual(field.active, not breaking)
                self.assertEqual(health.total, 455 if breaking else 500)
                self.assertEqual(player.body_script.component.entity_var(342).value(), 45 if breaking else 0)
                self.assertEqual(len(player.client.messages(combat.HIT)), 2 if breaking else 0)
                if breaking:
                    self.assertFalse(field.instance.states[17].active)
                    self.assertLess(fight.now, field.recharge_at)
                    self.assertAlmostEqual(field.broken_until - field.recharge_at, 3.0)
                    while fight.now + 0.016 < field.broken_until:
                        run.step(held)
                        self.assertFalse(field.active)
                    self.assertGreater(field.vitals.total, 590)
                    for _ in range(45):
                        run.step(held)
                    self.assertTrue(field.active)

    def test_accretion_hits_field_without_owner_damage_or_stun(self):
        run, player, reinhardt, held, field, health = self.raised_field()
        fight = run.match.combat
        x, y, z = reinhardt.mover.position
        yaw, pitch = aim(fight._eye(player), (x, y + 1, z))
        for frame in range(105):
            run.step({**held, player: (yaw, pitch, 0x10 if frame == 0 else 0)})
            self.assertFalse(fight.is_stunned(reinhardt))
        self.assertEqual(field.vitals.total, 1530)
        self.assertEqual(field.instance.find_var(0, 586).value(), 1530)
        self.assertEqual(health.total, 500)
        self.assertEqual(player.body_script.component.entity_var(342).value(), 0)
        self.assertFalse(player.client.messages(combat.HIT))
        self.assertTrue(run.match.switch_hero(reinhardt, heroes()[SOLDIER]))
        self.assertNotIn(field.entity, fight.barriers)

    def test_quick_melee_hits_the_mesh_once_within_reach_and_line_of_sight(self):
        for case, distance in (("mesh", 3.5), ("close", 2.4), ("wall", 3.5), ("range", 5.0)):
            with self.subTest(case=case):
                run, player, reinhardt, held, field, health = self.raised_field(distance)
                if case == "wall":
                    fixtures.SigmaCombatTests().wall(run, sideways=False, offset=1.7)
                fight = run.match.combat
                yaw, pitch = aim(fight._eye(player), fight._eye(reinhardt))
                for frame in range(50):
                    run.step({**held, player: (yaw, pitch, 0x800 if frame == 0 else 0)})
                self.assertEqual(field.vitals.total, 1570 if case in ("mesh", "close") else 1600)
                body_damage = 25 if case == "close" else 0
                self.assertEqual(health.total, 500 - body_damage)
                self.assertEqual(player.body_script.component.entity_var(342).value(), body_damage)
                self.assertEqual(len(player.client.messages(combat.HIT)), int(case == "close"))

    def test_raised_field_does_not_intercept_flux_lift_or_slam(self):
        run, player, reinhardt, held, field, health = self.raised_field()
        fight = run.match.combat
        script = player.body_script
        script.set_ult_charge(script.component.entity_var(338).value())
        point = tuple(reinhardt.mover.position)
        for frame in range(81):
            yaw, pitch = aim(fight._eye(player), point)
            buttons = 0x20 if frame == 0 else FIRE if frame == 80 else 0
            run.step({**held, player: (yaw, pitch, buttons)})
        for _ in range(150):
            run.step(held)
            self.assertTrue(field.active)
        self.assertGreater(reinhardt.mover.position[1], point[1])
        self.assertEqual(health.total, 455)
        for _ in range(170):
            run.step(held)
            self.assertTrue(field.active)
        self.assertAlmostEqual(reinhardt.mover.position[1], point[1], places=3)
        self.assertEqual(health.total, 210)
        self.assertEqual(field.vitals.total, 1600)
        self.assertEqual(script.component.entity_var(342).value(), 0)


if __name__ == "__main__":
    unittest.main()
