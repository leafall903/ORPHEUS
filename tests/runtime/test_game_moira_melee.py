"""Moira quick-melee recipient/range/occlusion acceptance via actual input."""

import unittest
from unittest.mock import patch

import test_game_moira as fixtures

from ow174.game import combat, stats
from ow174.game.collision import World
from ow174.game.commands import Command


class MoiraMeleeTests(unittest.TestCase):
    setup_fight = fixtures.MoiraSprayTests.setup_fight

    def test_actual_melee_applies_authored_horizontal_impulse_only_on_damage(self):
        for ally in (False, True):
            with self.subTest(ally=ally):
                run, source, target, health = self.setup_fight(ally=ally, distance=1.5)
                mover = target.owner.mover
                before = mover.velocity
                run.frame += 1
                run.match.update(run.frame * combat.TICK, run.frame)
                source.take_commands([Command(run.frame, buttons=0x800)])
                self.assertEqual(health.total, 100 if ally else 70)
                instance = next(i for i in source.body_script.component.instances.values()
                                if i.graph.index == 0x0043)
                hit = instance.state(4)
                modify = hit.node.field("m_modifyHealth")
                horizontal = hit.evaluate(modify["m_E33EFEE1"], watch=False)
                self.assertEqual(horizontal, 1)
                self.assertEqual(mover.velocity, (before[0], before[1], before[2] + (0 if ally else horizontal)))

    def test_full_melee_cooldown_rejects_hold_and_early_repress(self):
        melee = 0x800
        for held in (False, True):
            with self.subTest(held=held):
                run, source, _, health = self.setup_fight(ally=False, distance=1.5)
                instance = next(i for i in source.body_script.component.instances.values()
                                if i.graph.index == 0x0043)
                ability = instance.state(6)
                run.step({source: (0, 0, melee)})
                source.take_commands([source.mover.last_command])
                self.assertEqual(health.total, 70)
                self.assertGreater(ability.remaining(), 0.9)
                self.assertLessEqual(ability.remaining(), 1.0)
                first = instance.state(4).counter
                for frame in range(85):
                    remaining = ability.remaining()
                    if remaining == 0:
                        break
                    button = melee if held or (remaining > 0.25 and frame % 2) else 0
                    run.step({source: (0, 0, button)})
                    self.assertEqual(health.total, 70)
                    self.assertEqual(instance.state(4).counter, first)
                else:
                    self.fail("quick melee never became ready")
                for _ in range(8):
                    run.step({source: (0, 0, melee if held else 0)})
                    self.assertEqual(health.total, 70)
                    self.assertEqual(instance.state(4).counter, first)
                run.step({})
                run.step({source: (0, 0, melee)})
                source.take_commands([source.mover.last_command])
                self.assertEqual(instance.state(4).counter, first + 1)
                self.assertEqual(health.total, 40)
                self.assertEqual(source.stats.value(stats.HERO_DAMAGE), 60)

    def test_melee_rejects_ally_out_of_range_and_wall_occlusion(self):
        for ally, distance, wall in [(True, 1.5, False), (False, 4, False), (False, 1.5, True)]:
            with self.subTest(ally=ally, distance=distance, wall=wall):
                run, source, _target, vitals = self.setup_fight(ally=ally, distance=distance)
                fight = run.match.combat
                collision = fight._collision()
                if wall:
                    x, y, z = source.mover.position
                    z += 0.75
                    collision = World([
                        (x - 10, y - 5, z, x + 10, y - 5, z, x + 10, y + 5, z),
                        (x - 10, y - 5, z, x + 10, y + 5, z, x - 10, y + 5, z),
                    ])
                with patch.object(fight, "_collision", return_value=collision):
                    run.step({source: (0, 0, 0x800)})
                    for _ in range(40):
                        run.step({})
                self.assertEqual(vitals.total, 100)

    def test_out_of_range_swing_still_spends_the_full_melee_cooldown(self):
        run, source, target, health = self.setup_fight(ally=False, distance=4)
        instance = next(i for i in source.body_script.component.instances.values()
                        if i.graph.index == 0x0043)
        ability = instance.state(6)
        run.step({source: (0, 0, 0x800)})
        first = instance.state(4).counter
        self.assertEqual(health.total, 100)
        self.assertGreater(ability.remaining(), 0.9)
        x, y, z = source.mover.position
        target.owner.mover.position[:] = (x, y, z + 1.5)
        target.owner.mover.body = None
        run.step({})
        run.step({source: (0, 0, 0x800)})
        self.assertEqual(health.total, 100)
        self.assertEqual(instance.state(4).counter, first)
        for _ in range(70):
            run.step({})
            self.assertEqual(health.total, 100)
            self.assertEqual(instance.state(4).counter, first)
        self.assertEqual(ability.remaining(), 0)
        run.step({source: (0, 0, 0x800)})
        self.assertEqual(health.total, 70)
        self.assertEqual(instance.state(4).counter, first + 1)
