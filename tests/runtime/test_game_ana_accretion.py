"""Actual Sigma Accretion against Ana on build 104319."""

import unittest

import test_game_ana as fixture
from test_game_combat import Run, aim

from ow174.game import accretion, combat
from ow174.game.content import find_hero
from ow174.game.script import nodes


class AnaAccretionTests(unittest.TestCase):
    @staticmethod
    def place(player, position):
        player.spawn = position, player.spawn[1]
        player.mover = player.new_mover()

    def arena(self, *, distance=6, third=False):
        run = Run(fixture.TEAM_MAP, ("Ana", "Sigma", "Ally") if third else ("Ana", "Sigma"))
        ana, sigma = run.players[:2]
        match = run.match
        match.bots.clear()
        match._pending_bot_scripts.clear()
        self.assertTrue(match.switch_hero(ana, find_hero('Ana')))
        self.assertTrue(match.switch_hero(sigma, find_hero('Sigma')))
        ana.team, sigma.team = 0, 1
        x, y, z = ana.mover.position
        self.place(sigma, (x, y, z + distance))
        if third:
            ally = run.players[2]
            ally.team = ana.team
            self.place(ally, (x + 5, y, z))
        for _ in range(30):
            run.step({})
        fight = match.combat
        for target in fight._targets():
            fight.vitals_of(target)
        return run, ana, sigma

    def cast(self, run, sigma, point):
        fight = run.match.combat
        direction = aim(fight._eye(sigma), point)
        run.step({sigma: (*direction, 16)})
        for _ in range(90):
            if fight.rockets:
                break
            run.step({sigma: (*direction, 0)})
        self.assertEqual(len(fight.rockets), 1)
        rock, = fight.rockets
        self.assertIsNotNone(rock.accretion_instance)
        self.assertEqual(rock.gravity, 18)
        self.assertAlmostEqual(rock.speed, 37)
        return rock

    def finish_flight(self, run, rock):
        for _ in range(60):
            run.step({})
            if rock not in run.match.combat.rockets:
                return
        self.fail("Accretion did not finish its flight")

    def test_direct_rock_applies_authored_damage_stun_and_impulse(self):
        run, ana, sigma = self.arena()
        fight = run.match.combat
        start = tuple(ana.mover.position)
        rock = self.cast(run, sigma, fight._eye(ana))
        aim_y = combat.aim(*aim(fight._eye(sigma), fight._eye(ana)))[1]
        self.assertAlmostEqual(rock.direction[1] - aim_y, 0.1, delta=0.002)
        self.assertEqual(rock.direct.knockback, (6, 3, ((3, 1, 1),)))
        self.finish_flight(run, rock)
        self.assertEqual(fight.vitals[ana.body].total, 130)
        self.assertTrue(fight.is_stunned(ana))
        self.assertEqual(fight.stuns[ana.body] - fight.tick, 50)
        self.assertGreater(ana.mover.position[1], start[1])
        self.assertLess(ana.mover.position[2], start[2])
        for _ in range(50):
            run.step({ana: (0, 0, 1)})
        self.assertFalse(fight.is_stunned(ana))

    def test_wall_impact_applies_splash_without_direct_stun(self):
        run, ana, sigma = self.arena()
        x, y, z = ana.mover.position
        self.place(ana, (x + 1.6, y, z))
        fixture.set_static_wall(run, ana, -1)
        for _ in range(20):
            run.step({})
        rock = self.cast(run, sigma, (x, y + 1, z - 1))
        self.finish_flight(run, rock)
        fight = run.match.combat
        self.assertEqual(fight.vitals[ana.body].total, 160)
        self.assertFalse(fight.is_stunned(ana))

    def test_ally_and_ana_behind_static_cover_reject_rock_damage(self):
        for gate in ("ally", "cover"):
            with self.subTest(gate=gate):
                run, ana, sigma = self.arena()
                if gate == "ally":
                    sigma.team = ana.team
                else:
                    fixture.set_static_wall(run, ana, 2.5)
                fight = run.match.combat
                rock = self.cast(run, sigma, fight._eye(ana))
                self.finish_flight(run, rock)
                self.assertEqual(fight.vitals[ana.body].total, 200)
                self.assertFalse(fight.is_stunned(ana))

    def test_rock_interrupts_nano_before_release(self):
        run, ana, sigma = self.arena(third=True)
        ally = run.players[2]
        fight = run.match.combat
        vitals = fight.vitals[ally.body]
        vitals.damage(100)
        rock = self.cast(run, sigma, fight._eye(ana))
        for _ in range(5):
            run.step({})
        self.assertIn(rock, fight.rockets)
        ana.body_script.set_ult_charge(ana.body_script.component.vars[338].value())
        direction = aim(fight._eye(ana), fight._eye(ally))
        run.step({ana: (*direction, 32)})
        ultimate = ana.body_script.ultimates[0]
        self.assertTrue(ultimate.flags & nodes.Ability.RUNNING)
        self.finish_flight(run, rock)
        self.assertTrue(fight.is_stunned(ana))
        self.assertFalse(ultimate.flags & nodes.Ability.RUNNING)
        for _ in range(20):
            run.step({})
        self.assertEqual(vitals.total, 100)
        self.assertEqual(vitals.nano_until, 0)

    def test_sleep_dart_cancels_rock_during_windup(self):
        run, ana, sigma = self.arena()
        fight = run.match.combat
        forward = aim(fight._eye(sigma), fight._eye(ana))
        reply = aim(fight._eye(ana), fight._eye(sigma))
        run.step({sigma: (*forward, 16), ana: (*reply, 8)})
        instances = sigma.body_script.component.instances.values()
        instance = next(i for i in instances if i.graph.index == accretion.GRAPH)
        ability = next(state for state in instance.states.values() if isinstance(state, nodes.Ability))
        self.assertTrue(ability.flags & nodes.Ability.RUNNING)
        for _ in range(105):
            run.step({})
            self.assertTrue(all(rock.accretion_instance is None for rock in fight.rockets))
        self.assertTrue(fight.is_asleep(sigma))
        self.assertFalse(ability.flags & nodes.Ability.RUNNING)
        self.assertEqual(fight.vitals[ana.body].total, 200)

    def test_projected_barrier_stops_both_damage_components_and_control(self):
        for pool in (45, 200):
            with self.subTest(pool=pool):
                run, ana, sigma = self.arena(third=True)
                caster = run.players[2]
                self.assertTrue(run.match.switch_hero(caster, find_hero('Zarya')))
                for _ in range(25):
                    run.step({})
                fight = run.match.combat
                rock = self.cast(run, sigma, fight._eye(ana))
                direction = aim(fight._eye(caster), fight._eye(ana))
                run.step({caster: (*direction, 16)})
                barrier = fight.body_barriers[ana.body]
                barrier.health = pool
                start = tuple(ana.mover.position)
                self.finish_flight(run, rock)
                self.assertEqual(barrier.health, max(0, pool - 70))
                self.assertEqual(fight.vitals[ana.body].total, 200)
                self.assertFalse(fight.is_stunned(ana))
                self.assertEqual(tuple(ana.mover.position), start)

    def test_launched_rock_survives_caster_death_but_retires_on_replacement(self):
        for ending in ("death", "replacement"):
            with self.subTest(ending=ending):
                run, ana, sigma = self.arena(distance=8)
                fight = run.match.combat
                vitals = fight.vitals[sigma.body]
                vitals.damage(vitals.total - 1)
                direction = aim(fight._eye(ana), fight._eye(sigma))
                rock = self.cast(run, sigma, fight._eye(ana))
                if ending == "death":
                    run.step({ana: (*direction, 1)})
                    for _ in range(8):
                        if vitals.dead:
                            break
                        run.step({})
                    self.assertTrue(vitals.dead)
                    self.assertIn(rock, fight.rockets)
                    self.finish_flight(run, rock)
                    self.assertEqual(fight.vitals[ana.body].total, 130)
                    self.assertTrue(fight.is_stunned(ana))
                else:
                    self.assertTrue(run.match.switch_hero(sigma, find_hero('Soldier')))
                    self.assertNotIn(rock, fight.rockets)
                    self.assertEqual(fight.vitals[ana.body].total, 200)
