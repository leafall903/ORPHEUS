"""Roadhog lane regressions; these do not establish complete hero/client acceptance."""

import unittest

from test_game_combat import KANEZAKA_DM, Run

from ow174.game import combat
from ow174.game.content import heroes

ROADHOG = 0x02E0000000000040


class RoadhogProjectileTests(unittest.TestCase):
    def pellet_hit(self, distance, *, critical=1.0, head=False, gap=False):
        run = Run(KANEZAKA_DM, ("Roadhog", "Enemy"))
        match = run.match
        match.bots.clear()
        match._pending_bot_scripts.clear()
        shooter, enemy = run.players
        match.switch_hero(shooter, heroes()[ROADHOG])
        x, y, z = shooter.mover.position
        enemy.mover.position = [x, y, z + distance]
        fight = match.combat
        target = next(t for t in fight._targets() if t.owner is enemy)
        vitals = fight.vitals_of(target)
        weapon = next(i for i in shooter.body_script.component.instances.values() if i.graph.index == 0x01C6)
        state = next(s for s in weapon.states.values() if s.node.pos == 26)
        direct = combat.hit_def(state, state.node.field("m_6396149F"))
        direct.critical = critical
        origin = (x, y + (combat.EYE if head else 1.0), z)
        rocket = combat.Rocket(shooter, shooter.team, origin, (0.0, 0.0, 1.0), 80.0, 1.25,
                               direct, None, last_tick=run.frame)
        fight.rockets.append(rocket)
        frames = (100,) if gap else range(1, 101)
        for advance in frames:
            frame = run.frame + advance
            match.update(frame * combat.TICK, frame)
        self.assertNotIn(rocket, fight.rockets)
        return 200.0 - vitals.total, direct, rocket, shooter.client.messages(combat.HIT)

    def test_authored_primary_pellet_falloff_reaches_actual_player_pools(self):
        for distance in (8.0, 22.0, 35.0):
            with self.subTest(distance=distance):
                damage, definition, rocket, _ = self.pellet_hit(distance)
                self.assertAlmostEqual(definition.amount, 6.6, places=5)
                expected = definition.amount * definition.scale(rocket.speed * rocket.flown)
                self.assertAlmostEqual(damage, expected)
                if distance > 15:
                    self.assertLess(damage, definition.amount)

    def test_skipped_ticks_preserve_total_distance_falloff(self):
        continuous, _, _, _ = self.pellet_hit(22.0)
        skipped, _, _, _ = self.pellet_hit(22.0, gap=True)
        self.assertAlmostEqual(continuous, skipped)

    def test_projectile_critical_multiplier_and_hit_notice_agree(self):
        # Explicit shared-mechanism fixture: Roadhog node26 has no authored critical
        # multiplier in this extracted input, so this does not invent a hero default.
        body_damage, _, _, body_hits = self.pellet_hit(22.0, critical=2.0)
        head_damage, definition, rocket, head_hits = self.pellet_hit(22.0, critical=2.0, head=True)
        self.assertGreater(head_damage, body_damage)
        self.assertAlmostEqual(
            head_damage, 2.0 * definition.amount * definition.scale(rocket.speed * rocket.flown),
        )
        self.assertEqual(body_hits[-1]["+0x78"]["+0x8"] & combat.CRITICAL, 0)
        self.assertEqual(head_hits[-1]["+0x78"]["+0x8"] & combat.CRITICAL, combat.CRITICAL)


if __name__ == "__main__":
    unittest.main()
