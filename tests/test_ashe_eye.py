"""Build104319 FirstPerson heights drive scoped shooter/target ray inputs."""

import unittest
from unittest.mock import patch

import test_ashe_combat as fixtures

from ow174.game import combat
from ow174.game.collision import World
from ow174.game.commands import CROUCH
from ow174.game.content import HEROES_PATH, heroes
from ow174.game.script import graph

ASHE, MCCREE = 0x02E0000000000200, 0x02E0000000000042


@unittest.skipUnless(HEROES_PATH.is_file() and graph.GRAPHS_PATH.is_file(), "requires external 104319 data")
class AsheEyeTests(unittest.TestCase):
    fight = fixtures.AsheCombatTests.fight

    def scene(self, hero, crouched, cover=None):
        run, ashe, enemy = self.fight(distance=8)
        if hero == MCCREE:
            run.match.switch_hero(enemy, heroes()[MCCREE])
            enemy.mover.position[:] = (0, 0, 8)
        actor = ashe if hero == ASHE else enemy
        if cover is not None:
            run.match.collision_world = World([
                (-100, 0, -100, -100, 0, 100, 100, 0, 100),
                (-100, 0, -100, 100, 0, 100, 100, 0, -100),
                (-10, -2, 4, 10, cover, 4, 10, -2, 4),
                (-10, -2, 4, -10, cover, 4, 10, cover, 4),
                (-10, -2, 4, 10, -2, 4, 10, cover, 4),
                (-10, -2, 4, 10, cover, 4, -10, cover, 4),
            ])
        for _ in range(60):
            run.step({actor: (0, 0, CROUCH if crouched else 0)})
        self.assertEqual(actor.mover.crouched, crouched)
        return run, actor, enemy if hero == ASHE else ashe

    def test_shooter_target_and_history_use_actual_standing_crouched_heights(self):
        for hero, heights in ((ASHE, (1.445, 1.0115)), (MCCREE, (1.425, 0.9975))):
            for crouched in (False, True):
                with self.subTest(hero=hero, crouched=crouched):
                    run, actor, _other = self.scene(hero, crouched)
                    height = heights[int(crouched)]
                    self.assertAlmostEqual(run.match.combat._eye(actor)[1] - actor.mover.position[1], height)
                    target = next(t for t in run.match.combat._targets() if t.owner is actor)
                    self.assertEqual(target.eye, height)
                    self.assertEqual(run.match.combat.history[actor.body][-1][3], height)

    def test_actual_rifle_origins_are_blocked_or_clear_at_native_height_low_cover(self):
        for hero, heights in ((ASHE, (1.445, 1.0115)), (MCCREE, (1.425, 0.9975))):
            for crouched in (False, True):
                for blocked in (True, False):
                    with self.subTest(hero=hero, crouched=crouched, blocked=blocked):
                        top = heights[int(crouched)] + (0.03 if blocked else -0.03)
                        run, actor, target = self.scene(hero, crouched, cover=top)
                        self.assertAlmostEqual(actor.mover.position[1], 0, delta=0.01)
                        victim = next(t for t in run.match.combat._targets() if t.owner is target)
                        before = run.match.combat.vitals_of(victim).total
                        yaw = 0 if hero == ASHE else 32768
                        held = CROUCH if crouched else 0
                        with patch.object(run.match.combat, "_shoot", wraps=run.match.combat._shoot) as shoot:
                            for frame in range(45):
                                run.step({actor: (yaw, 0, held | (1 if frame < 5 else 0))})
                            self.assertEqual(shoot.call_count, 1)
                            state = shoot.call_args.args[2]
                            self.assertEqual(state._out("m_24E351A4").value(), state.ammo0 - state.per_shot)
                        after = run.match.combat.vitals_of(victim).total
                        if blocked:
                            self.assertEqual(after, before)
                        else:
                            self.assertLess(after, before)

    def test_shared_height_lookup_preserves_existing_fallbacks(self):
        self.assertEqual(combat.eye_height(0x04000000000003CF), 1.55)
        self.assertEqual(combat.eye_height(0x04000000000003CF, True), 1.085)
        self.assertEqual(combat.eye_height(0), 1.55)
        self.assertEqual(combat.eye_height(0, True), 1.15)


if __name__ == "__main__":
    unittest.main()
