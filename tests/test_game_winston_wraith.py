"""Winston attacks against Reaper's actual bounded Wraith state."""

import unittest

from test_game_combat import Run

from ow174.game import combat, protections, winston
from ow174.game.content import heroes

REAPER = 0x02E0000000000002


class WinstonWraithTests(unittest.TestCase):
    def setUp(self):
        self.run = Run(names=('Winston', 'Reaper'))
        self.match = self.run.match
        self.match.bots.clear()
        self.owner, self.reaper = self.run.players
        self.match.switch_hero(self.owner, heroes()[winston.HERO])
        self.match.switch_hero(self.reaper, heroes()[REAPER])
        self.owner.team, self.reaper.team = 0, 1
        self.move(self.owner, (0, 1, 0))
        self.move(self.reaper, (0, 1, 1.5))
        self.fight = self.match.combat
        self.owner.body_script.ult_seconds = 0

    @staticmethod
    def move(player, point):
        player.mover.position[:] = point
        player.mover.body = None
        player.mover.velocity = (0, 0, 0)

    def step(self, attack=0, defense=0, *, pitch=0):
        self.run.step({self.owner: (0, pitch, attack), self.reaper: (-32768, 0, defense)})

    def health(self, player):
        return self.fight.vitals_of(next(t for t in self.fight._targets()
                                           if t.owner is player)).total

    def phase(self):
        self.step(defense=8)
        for _ in range(4):
            self.step()
        self.assertTrue(protections.intangible(self.reaper))
        instance = next(i for i in self.reaper.body_script.component.instances.values()
                        if i.graph.index == 0x20)
        self.assertTrue(instance.state(3).active)
        return instance

    def rage(self):
        script = self.owner.body_script
        script.set_ult_charge(script.component.entity_var(338).value())
        self.step(32)
        for _ in range(35):
            self.step()
        self.assertTrue(winston.primal_active(script.component))

    def test_wraith_excludes_target_and_rejects_direct_damage_path(self):
        self.phase()
        self.assertNotIn(self.reaper, [t.owner for t in self.fight._enemies(self.owner)])
        target = next(t for t in self.fight._targets() if t.owner is self.reaper)
        self.fight._damage(self.owner, target, 100, False, None, self.run.frame)
        self.assertEqual(self.health(self.reaper), 250)

    def test_tesla_spends_ammo_but_cannot_hit_or_charge_from_wraith(self):
        self.phase()
        for _ in range(30):
            self.step(1)
        self.assertEqual(self.health(self.reaper), 250)
        self.assertEqual(self.owner.client.messages(combat.HIT), [])
        self.assertEqual(self.owner.body_script.component.entity_var(342).value(), 0)
        weapon = next(i for i in self.owner.body_script.component.instances.values()
                      if i.graph.index == 0xFE)
        self.assertLess(weapon.vars[53].value(), 100)

    def test_quick_melee_cannot_damage_wraith(self):
        self.phase()
        self.step(0x800)
        for _ in range(40):
            self.step()
        self.assertEqual(self.health(self.reaper), 250)

    def test_primal_cannot_damage_or_knock_back_wraith(self):
        self.rage()
        self.phase()
        for _ in range(80):
            self.step(1)
        self.assertEqual(self.health(self.reaper), 250)
        self.assertEqual(self.reaper.mover.position[2], 1.5)
        self.assertEqual(self.reaper.mover.velocity, (0, 0, 0))
        self.assertEqual(self.health(self.owner), 1000)

    def test_jump_takeoff_cannot_damage_or_knock_back_wraith(self):
        self.phase()
        self.step(8)
        self.assertEqual(self.health(self.reaper), 250)
        self.assertEqual(self.reaper.mover.velocity, (0, 0, 0))

    def test_actual_landing_ignores_wraith_at_impact(self):
        self.move(self.reaper, (0, 1, 30))
        self.step(8, pitch=-16384)
        for _ in range(250):
            self.step()
            if self.owner.mover.velocity[1] < 0 and self.owner.mover.position[1] < 3:
                break
        self.move(self.reaper, (0, 1, 0))
        self.phase()
        for _ in range(40):
            self.step()
            if self.owner.body not in self.fight.winston_jump.flights:
                break
        self.assertTrue(protections.intangible(self.reaper))
        self.assertNotIn(self.owner.body, self.fight.winston_jump.flights)
        self.assertEqual(self.health(self.reaper), 250)
        self.assertEqual(self.reaper.mover.velocity, (0, 0, 0))
        self.step()
        self.assertEqual(tuple(self.reaper.mover.position), (0, 1, 0))

    def test_expiry_restores_actual_tesla_damage(self):
        self.phase()
        for _ in range(190):
            self.step()
        self.assertFalse(protections.intangible(self.reaper))
        self.step(1)
        self.assertAlmostEqual(self.health(self.reaper), 249.04)

    def test_primary_cancel_restores_targeting_and_damage(self):
        self.phase()
        for _ in range(30):
            self.step()
        self.step(defense=1)
        self.assertFalse(protections.intangible(self.reaper))
        self.step(1)
        self.assertAlmostEqual(self.health(self.reaper), 249.04)

    def test_cooldown_repress_cannot_restore_immunity(self):
        instance = self.phase()
        for _ in range(190):
            self.step()
        self.assertFalse(protections.intangible(self.reaper))
        self.step(defense=8)
        self.step()
        self.assertFalse(protections.intangible(self.reaper))
        self.assertEqual(instance.state(1).counter, 1)
        self.step(1)
        self.assertLess(self.health(self.reaper), 250)


if __name__ == '__main__':
    unittest.main()
