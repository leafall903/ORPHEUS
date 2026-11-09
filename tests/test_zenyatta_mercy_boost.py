"""Actual Mercy beam and launch-time boost on Zen projectiles."""
import unittest
from pathlib import Path
from unittest.mock import patch

from test_game_combat import Run, aim

from ow174.game import combat, mercy_boost
from ow174.game.commands import FIRE, SECONDARY
from ow174.game.content import heroes
from ow174.game.script import graph
from ow174.game.zenyatta import HERO

EFFECTS = Path(r"D:\overwatch 1.7.4\verification\hero-lanes\0004\research\game_graphs_effects_174.json.gz")


def clear_graphs():
    for name in ("_data", "_records", "graph"):
        getattr(graph, name).cache_clear()


class MercyZen(unittest.TestCase):
    def setUp(self):
        self.addCleanup(clear_graphs)
        self.enterContext(patch.object(graph, "GRAPHS_PATH", EFFECTS))
        clear_graphs()
        self.run = Run(names=("Mercy", "Zen", "Enemy"))
        self.run.match.bots.clear()
        self.mercy, self.zen, self.enemy = self.run.players
        self.assertTrue(self.run.match.switch_hero(self.mercy, heroes()[mercy_boost.HERO]))
        self.assertTrue(self.run.match.switch_hero(self.zen, heroes()[HERO]))
        self.mercy.team = self.zen.team = 0
        self.enemy.team = 1
        x, y, z = self.mercy.mover.position
        for actor, distance in ((self.zen, 4), (self.enemy, 27)):
            actor.mover.position = [x, y, z+distance]
            actor.mover.body = None
        self.fight = self.run.match.combat
        self.staff = aim(self.fight._eye(self.mercy), (x, y+1, z+4))
        self.shot = aim(self.fight._eye(self.zen), (x, y+1, z+27))
        for _ in range(30):
            self.step()

    def step(self, beam=0, weapon=0):
        self.run.step({self.mercy: (*self.staff, beam), self.zen: (*self.shot, weapon)})

    def test_beam_and_discord_combine_on_orb_after_release(self):
        for _ in range(28):
            self.step(SECONDARY)
        self.assertAlmostEqual(mercy_boost.bonus(self.fight, self.zen), .3, places=5)
        self.step(SECONDARY, 8)
        self.assertEqual(self.fight.zenyatta.owners[self.zen.body].discord.target, self.enemy.body)
        self.step(SECONDARY, FIRE)
        rocket, = self.fight.rockets
        self.assertAlmostEqual(rocket.direct.amount, 62.4, places=4)
        for _ in range(50):
            self.step()
        self.assertEqual(mercy_boost.bonus(self.fight, self.zen), 0)
        self.assertAlmostEqual(self.fight.vitals[self.enemy.body].total, 122, places=4)

    def test_beam_attached_during_flight_does_not_rescale_existing_orb(self):
        self.step(weapon=FIRE)
        rocket, = self.fight.rockets
        self.assertEqual(rocket.direct.amount, 48)
        for _ in range(25):
            self.step(SECONDARY)
            if mercy_boost.bonus(self.fight, self.zen):
                break
        self.assertIn(rocket, self.fight.rockets)
        self.assertAlmostEqual(mercy_boost.bonus(self.fight, self.zen), .3, places=5)
        for _ in range(40):
            self.step(SECONDARY)
        self.assertEqual(self.fight.vitals[self.enemy.body].total, 152)

    def test_full_charged_volley_samples_beam_for_each_orb(self):
        health = self.fight.vitals[self.enemy.body] = combat.Vitals(self.enemy.body, 1000)
        self.step(SECONDARY, 8)
        for _ in range(130):
            self.step(SECONDARY, SECONDARY)
        self.assertEqual(health.total, 1000)
        for _ in range(75):
            self.step(SECONDARY)
        self.assertAlmostEqual(health.total, 610, places=3)
        hits = self.zen.client.messages(combat.HIT)
        self.assertEqual([hit["+0x78"]["+0x4"] for hit in hits], [78]*5)

    def test_actual_melee_combines_beam_and_discord_without_critical(self):
        x, y, z = self.zen.mover.position
        self.enemy.mover.position = [x, y, z+1.5]
        self.enemy.mover.body = None
        self.shot = (0, 0)
        for _ in range(28):
            self.step(SECONDARY)
        self.step(SECONDARY, 8)
        for index in range(35):
            self.step(SECONDARY, 0x800 if index == 0 else 0)
        hp = self.fight.vitals[self.enemy.body]
        self.assertAlmostEqual(hp.total, 151.25, places=4)
        hit, = self.zen.client.messages(combat.HIT)
        self.assertEqual(hit["+0x78"]["+0x8"], 0)

    def test_cover_breaks_actual_beam_before_next_orb(self):
        from test_game_mover import box, wall

        from ow174.game.collision import World

        for _ in range(28):
            self.step(SECONDARY)
        self.assertAlmostEqual(mercy_boost.bonus(self.fight, self.zen), .3, places=5)
        x, y, z = self.mercy.mover.position
        self.run.match.collision_world = World(
            box(x-35, x+35, y-2, y, z-35, z+35) + wall(x-6, z+2, x+6, z+2, y, y+5)
        )
        for _ in range(12):
            self.step(SECONDARY)
        self.assertEqual(mercy_boost.bonus(self.fight, self.zen), 0)
        self.step(SECONDARY, FIRE)
        for _ in range(40):
            self.step(SECONDARY)
        self.assertEqual(self.fight.vitals[self.enemy.body].total, 152)

    def test_actual_boost_awards_only_extra_damage_to_launch_caster(self):
        for actor in (self.zen, self.mercy):
            actor.body_script.ult_seconds = 0
        mercy_charge = self.mercy.body_script.component.entity_var(342)
        zen_charge = self.zen.body_script.component.entity_var(342)
        self.mercy.body_script.component.set(12918, 0.0)
        for _ in range(28):
            self.step(SECONDARY)
        self.step(SECONDARY, 8)
        self.step(SECONDARY, FIRE)
        projectile, = self.fight.rockets
        self.assertIs(projectile.direct.boost_credit[0], self.mercy.body_script)
        before = mercy_charge.value(), zen_charge.value()
        for _ in range(60):
            self.step()
        self.assertAlmostEqual(mercy_charge.value()-before[0], 18, places=4)
        self.assertAlmostEqual(zen_charge.value()-before[1], 60, places=4)

    def test_overkill_and_replaced_booster_cannot_claim_extra_charge(self):
        for mode, remaining, expected in (("overkill", 40, 0), ("partial", 55, 7), ("replace", 200, 0)):
            with self.subTest(mode=mode):
                self.setUp()
                health = self.fight.vitals[self.enemy.body]
                health.damage(200-remaining)
                original = self.mercy.body_script
                original.ult_seconds = 0
                original.component.set(12918, 0.0)
                for _ in range(28):
                    self.step(SECONDARY)
                self.step(SECONDARY, FIRE)
                if mode == "replace":
                    self.assertTrue(self.run.match.switch_hero(self.mercy, self.mercy.hero))
                    self.assertTrue(original.retired)
                    self.mercy.body_script.ult_seconds = 0
                    self.mercy.body_script.component.set(12918, 0.0)
                slot = self.mercy.body_script.component.entity_var(342)
                before = slot.value()
                for _ in range(65):
                    self.step()
                self.assertAlmostEqual(slot.value()-before, expected, places=4)
                self.assertAlmostEqual(health.total, 137.6 if mode == "replace" else 0, places=4)
