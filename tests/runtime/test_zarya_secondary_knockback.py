"""Native feedback: Zarya secondary damages targets but did not move them."""

import unittest

import test_zarya_reported_presentation as presentation

from ow174.game import bots, combat
from ow174.game.collision import flat_world


class ZaryaSecondaryKnockbackTests(unittest.TestCase):
    def test_authored_secondary_moves_the_damaged_player_and_training_bot(self):
        for kind in ("player", "training_bot"):
            with self.subTest(kind=kind):
                run, caster, enemy, _ = presentation.ZaryaPresentationTests().scene()
                fight = run.match.combat
                run.match.collision_world = flat_world(1)
                for player in run.players:
                    player.mover.use(world=run.match.collision_world)
                victim = enemy
                if kind == "training_bot":
                    enemy.mover.position[:] = [10, 1, 5]
                    hero, data = next(iter(bots.table()["heroes"].items()))
                    victim = bots.Bot(bots.BOT_ENTITY, {
                        "hero": hero, "team": 1, "position": [0, 1, 5],
                        "rotation": [0, 0, 0, 1], "graphs": [],
                    }, data, {})
                    run.match.bots.append(victim)
                entity = victim.entity if kind == "training_bot" else victim.body
                position = tuple(victim.position if kind == "training_bot" else victim.mover.position)
                run.step({caster: (0, 0, 2)})
                rocket, = fight.rockets
                self.assertTrue(rocket.blast.impulse_rings)
                self.assertGreater(rocket.blast.horizontal_impulse, 0)
                for _ in range(30):
                    run.step({})
                    if rocket not in fight.rockets:
                        break
                self.assertNotIn(rocket, fight.rockets)
                self.assertLess(fight.vitals[entity].total, 200)
                self.assertTrue(caster.client.messages(combat.HIT))
                for _ in range(6):
                    run.step({})
                moved = victim.position if kind == "training_bot" else victim.mover.position
                self.assertGreater(moved[2], position[2] + 0.05)
                for _ in range(150):
                    run.step({})
                settled = victim.position if kind == "training_bot" else victim.mover.position
                velocity = victim.velocity if kind == "training_bot" else victim.mover.velocity
                self.assertAlmostEqual(settled[1], 1, places=4)
                self.assertLess(sum(value * value for value in velocity), 0.0001)
                if kind == "training_bot":
                    self.assertIsNone(victim.impulse_mover)
