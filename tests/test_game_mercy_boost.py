"""Outgoing source attribution and projectile launch-time boost on real attacks."""

import unittest
from pathlib import Path
from unittest.mock import patch

import test_game_mercy_staff as staff_tests
from test_game_combat import aim, in_world
from test_game_mercy_npc import clear_graphs

from ow174.game import stats
from ow174.game.commands import FIRE, SECONDARY
from ow174.game.content import heroes
from ow174.game.script import graph
from ow174.game.script.regeneration import MERCY

EFFECT_BUNDLE = (
    Path("D:/overwatch 1.7.4/verification/hero-lanes/0004/research") / "game_graphs_effects_174.json.gz"
)


@unittest.skipUnless(EFFECT_BUNDLE.is_file(), "external extracted effect pack required")
class MercyBoostTests(unittest.TestCase):
    def setUp(self):
        self.enterContext(patch.object(graph, "GRAPHS_PATH", EFFECT_BUNDLE))
        clear_graphs()
        self.addCleanup(clear_graphs)
        fixture = staff_tests.MercyStaffTests()
        fixture.setUp()
        self.addCleanup(fixture.doCleanups)
        self.fixture = fixture
        self.run, self.match = fixture.run, fixture.match
        self.mercy, self.ally, self.enemy = fixture.mercy, fixture.ally, fixture.enemy
        self.fight = fixture.fight

    def step(self, count, *, beam=SECONDARY, attack=0):
        for _ in range(count):
            x, y, z = self.ally.mover.position
            beam_yaw, beam_pitch = aim(self.fight._eye(self.mercy), (x, y + 1, z))
            x, y, z = self.enemy.mover.position
            shot_yaw, shot_pitch = aim(self.fight._eye(self.ally), (x, y + 0.8, z))
            self.run.step({self.mercy: (beam_yaw, beam_pitch, beam),
                           self.ally: (shot_yaw, shot_pitch, attack)})

    def enemy_health(self):
        target = next(t for t in self.fight._targets() if t.owner is self.enemy)
        return self.fight.vitals_of(target).total

    def test_secondary_amplifies_actual_rifle_damage_and_keeps_damage_on_ally(self):
        self.step(10)
        self.step(1, attack=FIRE)
        self.step(10)
        self.assertAlmostEqual(self.enemy_health(), 174, places=4)
        self.assertAlmostEqual(self.ally.stats.value(stats.HERO_DAMAGE), 26, places=4)
        self.assertEqual(self.mercy.stats.value(stats.HERO_DAMAGE), 0)
        self.assertEqual(self.mercy.stats.value(stats.HEALING), 0)
        self.assertEqual(self.fixture.vitals.total, 100)  # secondary does not heal

    def test_release_removes_bonus_before_the_next_attack(self):
        self.step(10)
        self.step(1, beam=0)
        self.step(1, beam=0, attack=FIRE)
        self.step(10, beam=0)
        self.assertAlmostEqual(self.enemy_health(), 180, places=4)

    def test_projectile_keeps_launch_bonus_after_beam_release(self):
        self.step(10)
        self.step(1, attack=SECONDARY)  # Soldier Helix:40 direct plus80 blast
        self.assertTrue(self.fight.rockets)
        rocket = next(r for r in self.fight.rockets if r.shooter is self.ally)
        self.assertAlmostEqual(rocket.direct.amount, 52, places=4)
        self.assertAlmostEqual(rocket.blast.amount, 104, places=4)
        self.step(20, beam=0)
        self.assertAlmostEqual(self.enemy_health(), 44, places=3)

    def test_projectile_does_not_gain_bonus_when_beam_attaches_after_launch(self):
        self.step(10, beam=0)
        self.step(1, beam=0, attack=SECONDARY)
        rocket = next(r for r in self.fight.rockets if r.shooter is self.ally)
        self.assertAlmostEqual(rocket.direct.amount, 40, places=4)
        self.step(20)
        self.assertAlmostEqual(self.enemy_health(), 80, places=3)

    def test_missing_effect_pack_does_not_invent_damage_bonus(self):
        self.step(10)
        with patch.object(graph, "effect_script", return_value=None):
            self.assertEqual(self.fight.damage_scale(self.ally), 1)
            self.step(1, attack=FIRE)
        self.assertAlmostEqual(self.enemy_health(), 180, places=4)

    def test_blocked_beam_does_not_amplify_a_real_attack(self):
        from test_game_mover import FLOOR, wall

        from ow174.game.collision import World

        self.step(10)
        self.match.collision_world = World(FLOOR + wall(-2, 2.5, 2, 2.5))
        self.step(1, attack=FIRE)
        self.assertAlmostEqual(self.enemy_health(), 180, places=4)

    def test_dead_caster_does_not_amplify_a_real_attack(self):
        self.step(10)
        caster = next(t for t in self.fight._targets() if t.owner is self.mercy)
        self.fight._damage(self.enemy, caster, 500, False, None, self.run.frame)
        self.step(1, attack=FIRE)
        self.assertAlmostEqual(self.enemy_health(), 180, places=4)

    def test_two_mercy_sources_do_not_compound_the_same_attack(self):
        other = self.match.add_player(4, "Second Mercy", MERCY, 0, False)
        in_world(self.match, other)
        self.match.switch_hero(other, heroes()[MERCY])
        self.run.players.append(other)
        for frame in range(11):
            commands = {}
            for healer in (self.mercy, other):
                x, y, z = self.ally.mover.position
                yaw, pitch = aim(self.fight._eye(healer), (x, y + 1, z))
                commands[healer] = (yaw, pitch, SECONDARY)
            x, y, z = self.enemy.mover.position
            yaw, pitch = aim(self.fight._eye(self.ally), (x, y + 0.8, z))
            commands[self.ally] = (yaw, pitch, FIRE if frame == 10 else 0)
            self.run.step(commands)
        self.assertAlmostEqual(self.enemy_health(), 174, places=4)

    def test_team_change_removes_bonus_before_the_next_attack(self):
        self.step(10)
        self.mercy.team = 2
        self.step(1, attack=FIRE)
        self.assertAlmostEqual(self.enemy_health(), 180, places=4)
