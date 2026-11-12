"""Original0068 Fortify/Graviton assertions using the existing single field host."""
import unittest
from test_game_combat import Run, aim
from ow174.game import combat, defenses
from ow174.game.collision import flat_world
from ow174.game.content import heroes

class ZaryaGravitonFortifyTests(unittest.TestCase):
    def scene(self, hero, *, distance=5, activate_defense=True):
        self.run = Run(names=("Zarya", "Defender", "Behind"))
        self.a, self.b, self.behind = self.run.players
        self.fight = self.run.match.combat
        self.run.match.bots.clear()
        self.run.match._pending_bot_scripts.clear()
        self.run.match.collision_world = flat_world(0)
        self.run.match.switch_hero(self.a, heroes()[0x02E0000000000068])
        self.run.match.switch_hero(self.b, heroes()[hero])
        self.b.team = self.behind.team = 1 - self.a.team
        for player, pos in ((self.a, (0, 0, 0)), (self.b, (0, 0, distance)),
                            (self.behind, (0, 0, distance + 5))):
            player.mover.position[:] = pos
            player.mover.body = None
            player.body_script.ult_seconds = 0
        self.step(20)
        if activate_defense and hero == defenses.ZENYATTA:
            self.b.body_script.set_ult_charge(self.b.body_script.component.entity_var(338).value())
            self.step(defense=0x20)
            self.assertTrue(defenses.invulnerable(self.b))
        elif activate_defense and hero == defenses.ORISA:
            self.step(defense=8)
            self.assertAlmostEqual(defenses.fortify_scale(self.b), 0.6, places=6)

    def step(self, frames=1, attack=0, defense=0):
        for _ in range(frames):
            self.run.step({self.a: (0, 0, attack), self.b: (-32768, 0, defense)})

    def health(self, player=None):
        player = self.b if player is None else player
        target = next(t for t in self.fight._targets() if t.owner is player)
        return self.fight.vitals_of(target).total

    def charge(self):
        return self.a.body_script.component.entity_var(342).value()

    def field(self):
        self.a.body_script.set_ult_charge(2100)
        self.step(attack=0x20)
        for _ in range(100):
            if self.fight.zarya_gravitons.fields:
                break
            self.step()
        field, = self.fight.zarya_gravitons.fields
        return field

    def test_fortify_scales_field_damage_and_blocks_pull_until_expiry(self):
        self.scene(defenses.ORISA)
        self.step(180)
        before = self.health()
        self.field()
        point = tuple(self.b.mover.position)
        self.assertAlmostEqual(before - self.health(), 1.5, places=5)
        self.step(10)
        self.assertEqual(tuple(self.b.mover.position), point)
        self.assertAlmostEqual(before - self.health(), 1.98, places=5)
        deadline = self.run.frame + 100
        while defenses.fortify_scale(self.b) is not None:
            self.assertLess(self.run.frame, deadline)
            self.step()
        self.step(10)
        self.assertNotEqual(tuple(self.b.mover.position), point)
        self.assertEqual(self.charge(), 0)

    def test_fortify_releases_active_pull_on_next_world_tick_without_input(self):
        self.scene(defenses.ORISA, activate_defense=False)
        field = self.field()
        self.step(2)
        self.assertLess(self.b.mover.velocity[2], 0)
        self.step(defense=8)
        self.assertIsNotNone(defenses.fortify_state(self.b))
        position = tuple(self.b.mover.position)
        before = self.health()

        self.run.frame += 1
        self.run.match.update(self.run.frame * combat.TICK, self.run.frame)
        self.assertIn(field, self.fight.zarya_gravitons.fields)
        self.assertNotIn(self.b.body, self.fight.zarya_gravitons.pulled)
        self.assertEqual(tuple(self.b.mover.position), position)
        self.assertEqual(self.b.mover.velocity, (0.0, 0.0, 0.0))
        self.assertAlmostEqual(before - self.health(), 0.048, places=5)

