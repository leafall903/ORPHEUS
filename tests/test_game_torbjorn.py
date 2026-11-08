"""104319 Torbjorn combat evidence; these are server tests, not client acceptance.

The secondary checks deliberately exercise one pellet. Pellet count/spread, gravity,
turret, Overload, hammer repair, Molten Core and protection interactions remain open.
"""

import unittest

from test_game_combat import KANEZAKA_DM, Run

from ow174.game import combat, world
from ow174.game.commands import Command
from ow174.game.content import heroes

TORBJORN = 0x02E0000000000006


class TorbjornProjectileTests(unittest.TestCase):
    def arena(self, distance=5.0):
        run = Run(KANEZAKA_DM, names=("Torbjorn", "Enemy"))
        shooter, victim = run.players
        self.assertTrue(run.match.switch_hero(shooter, heroes()[TORBJORN]))
        run.match.bots.clear()
        run.match._pending_bot_scripts.clear()
        x, y, z = shooter.mover.position
        victim.mover.position = [x, y, z + distance]
        for _ in range(35):
            run.step({})
        return run, shooter, victim

    def fire_pellet(self, distance, *, critical=None, height=0.8):
        run, shooter, victim = self.arena(distance)
        fight = run.match.combat
        x, y, z = shooter.mover.position
        instance = next(i for i in shooter.body_script.component.instances.values()
                        if i.graph.index == 0x1683)
        state = instance.states[13]
        hit = combat.hit_def(state, state.node.field("m_6396149F"))
        self.assertEqual(hit.amount, 12.5)
        if critical is not None:
            hit.critical = critical
        target = next(t for t in fight._targets() if t.owner is victim)
        vitals = fight.vitals_of(target)
        before = vitals.total
        rocket = combat.Rocket(shooter, shooter.team, (x, y + height, z),
                               (0.0, 0.0, 1.0), 120.0, 2.0, hit, None)
        fight.rockets.append(rocket)
        for _ in range(20):
            run.step({})
        self.assertNotIn(rocket, fight.rockets)
        return before - vitals.total, shooter.client.messages(combat.HIT), vitals

    def test_secondary_pellet_falloff_changes_actual_health(self):
        for distance, expected in ((5.0, 12.5), (15.0, None), (25.0, 3.75)):
            with self.subTest(distance=distance):
                dealt, hits, _ = self.fire_pellet(distance)
                if expected is None:
                    self.assertGreater(dealt, 3.75)
                    self.assertLess(dealt, 12.5)
                else:
                    self.assertAlmostEqual(dealt, expected, places=5)
                self.assertEqual(len(hits), 1)

    def test_explicit_projectile_critical_multiplier_and_hit_flag(self):
        body, _, _ = self.fire_pellet(5.0, critical=2.0, height=0.8)
        head, hits, _ = self.fire_pellet(5.0, critical=2.0, height=1.55)
        self.assertAlmostEqual(head, 2.0 * body)
        self.assertEqual(hits[0]["+0x78"]["+0x8"] & combat.CRITICAL, combat.CRITICAL)

    def test_actual_primary_volley_damages_player_once(self):
        run, shooter, victim = self.arena()
        fight = run.match.combat
        target = next(t for t in fight._targets() if t.owner is victim)
        vitals = fight.vitals_of(target)
        state = next(i for i in shooter.body_script.component.instances.values()
                     if i.graph.index == 0x1683).states[10]
        fight._shoot(shooter, Command(run.frame, yaw=0, pitch=0), state, 0,
                     run.frame * 16, activation=(1, run.frame * 16))
        for _ in range(20):
            run.step({})
        self.assertAlmostEqual(vitals.total, 130.0)
        self.assertEqual(len(shooter.client.messages(combat.HIT)), 1)

    def test_torbjorn_takes_armour_damage_and_dies_to_enemy_player(self):
        run, victim, shooter = self.arena()
        fight = run.match.combat
        target = next(t for t in fight._targets() if t.owner is victim)
        vitals = fight.vitals_of(target)
        self.assertEqual(vitals.now[world.ARMOUR_POOL], 50.0)
        fight._damage(shooter, target, 20.0, False, None, run.frame)
        self.assertEqual(vitals.total, 235.0)
        fight._damage(shooter, target, 500.0, False, None, run.frame)
        self.assertTrue(fight.is_dead(victim))
        self.assertTrue(victim.body_script.component.owner_dead)
        self.assertEqual(vitals.killer, shooter.body)
