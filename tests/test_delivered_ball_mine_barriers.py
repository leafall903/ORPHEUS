"""Original Mine barrier checks through the canonical world/static actor APIs."""
import math
import unittest
from dataclasses import replace
from test_delivered_ball_mine_factory import MineTests
from test_delivered_wrecking_ball_fireball import relocate
from test_game_combat import Run, KANEZAKA_DM
from ow174.game import ball, barriers, combat
from ow174.game.content import heroes
from ow174.game.script.driver import ULT_CHARGE, ULT_COST

class MineBarrierTests(unittest.TestCase):
    setUpClass = classmethod(MineTests.setUpClass.__func__)
    scenario = MineTests.scenario
    deploy = MineTests.deploy
    isolated = MineTests.isolated
    target = MineTests.target

    def test_mine_triggers_across_winston_shell_without_damaging_dome(self):
        for mine_inside in (False, True):
            with self.subTest(mine_inside=mine_inside):
                self.check_mine_across_winston_shell(mine_inside)

    def check_mine_across_winston_shell(self, mine_inside):
        run = Run(KANEZAKA_DM, ('Ball', 'Target', 'Winston'))
        player, target, winston = run.players
        run.match.game_map = replace(run.match.game_map, free_for_all=False)
        player.team, target.team, winston.team = 0, 1, 1
        run.match.bots.clear()
        run.match._pending_bot_scripts.clear()
        run.match.switch_hero(player, heroes()[ball.HERO])
        run.match.switch_hero(winston, heroes()[barriers.WINSTON])
        x, y, z = player.mover.position
        relocate(run, target, (x + 100, y, z))
        relocate(run, winston, (x + 110, y, z))
        for _ in range(40):
            run.step({})
        player.body_script.ult_seconds = 0
        mine = self.isolated(run, player)
        self.assertTrue(mine.body_script.primary().states[28].active)
        mx, my, mz = mine.position
        relocate(run, winston, (mx, y, mz + (4.75 if mine_inside else 5.25)))
        run.step({winston: (0, 0, 16)})
        fight = run.match.combat
        for _ in range(45):
            if fight.barriers.world.actors:
                break
            run.step({})
        dome, = fight.barriers.world.actors.values()
        self.assertIs(dome.owner, winston)
        self.assertEqual(dome.health, 700)
        self.assertEqual(math.dist(mine.position, dome.centre()) < dome.radius, mine_inside)
        direction = -1 if mine_inside else 1
        relocate(run, target, (mx, my, mz + 0.5 * direction))
        target_inside = math.dist(target.mover.position, dome.centre()) < dome.radius
        self.assertEqual(target_inside, not mine_inside)
        run.step({})
        self.assertTrue(mine.triggered)
        self.assertEqual(fight.vitals[target.body].total, 70)
        self.assertGreater(target.mover.velocity[1], 4)
        self.assertGreater(direction * target.mover.velocity[2], 4)
        self.assertEqual(dome.health, 700)
        self.assertEqual(player.body_script.component.entity_var(ULT_CHARGE).value(), 0)

    def test_zarya_bubble_consumes_mine_explosion_without_body_impulse(self):
        run, player, zarya = self.scenario()
        run.match.switch_hero(zarya, heroes()[barriers.ZARYA])
        x, y, z = player.mover.position
        relocate(run, zarya, (x + 100, y, z))
        mine = self.isolated(run, player)
        fight = run.match.combat
        run.step({zarya: (0, 0, 8)})
        bubble = next(iter(fight.barriers.scripted.values()))
        self.assertTrue(mine.body_script.primary().states[28].active)
        x, y, z = mine.position
        relocate(run, zarya, (x, y, z + 0.5))
        run.step({})
        self.assertTrue(mine.triggered)
        self.assertEqual(bubble.vitals.total, 70)
        self.assertAlmostEqual(zarya.body_script.component.entity_var(87).value(), 26)
        self.assertEqual(fight.vitals_of(self.target(run, zarya)).total, 400)
        self.assertEqual(tuple(zarya.mover.velocity), (0, 0, 0))
        self.assertFalse(zarya.mover.airborne)
        self.assertEqual(player.body_script.component.entity_var(ULT_CHARGE).value(), 0)

    def projected_mine_scene(self, *, recipient_hero=None):
        run = Run(KANEZAKA_DM, ('Ball', 'Protected ally', 'Zarya'))
        player, ally, zarya = run.players
        match = run.match
        match.game_map = replace(match.game_map, free_for_all=False)
        player.team, ally.team, zarya.team = 0, 1, 1
        match.bots.clear()
        match._pending_bot_scripts.clear()
        match.switch_hero(player, heroes()[ball.HERO])
        match.switch_hero(zarya, heroes()[barriers.ZARYA])
        if recipient_hero is not None:
            self.assertTrue(match.switch_hero(ally, heroes()[recipient_hero]))
        x, y, z = player.mover.position
        relocate(run, ally, (x + 100, y, z))
        relocate(run, zarya, (x + 100, y, z + 5))
        for _ in range(90):
            run.step({})
        player.body_script.ult_seconds = 0
        mine = self.isolated(run, player)
        x, y, z = mine.position
        relocate(run, ally, (x, y, z + 6))
        relocate(run, zarya, (x, y, z + 10))
        for _ in range(40):
            run.step({})
        run.step({zarya: (-32768, 0, 16)})
        for _ in range(12):
            run.step({zarya: (-32768, 0, 0)})
        fight = match.combat
        bubble, = fight.barriers.scripted.values()
        self.assertIs(bubble.recipient, ally)
        self.assertEqual(bubble.vitals.total, 200)
        self.assertFalse(mine.triggered)
        self.assertTrue(mine.body_script.primary().states[28].active)
        return run, player, ally, zarya, mine, bubble

    def test_projected_zarya_bubble_consumes_armed_mine_and_credits_caster(self):
        run, player, ally, zarya, mine, bubble = self.projected_mine_scene()
        fight = run.match.combat
        x, y, z = mine.position
        relocate(run, ally, (x, y, z + 0.5))
        run.step({})
        self.assertTrue(mine.triggered)
        self.assertEqual(bubble.vitals.total, 70)
        self.assertAlmostEqual(zarya.body_script.component.entity_var(87).value(), 26)
        self.assertEqual(fight.vitals_of(self.target(run, ally)).total, 200)
        self.assertEqual(fight.vitals_of(self.target(run, zarya)).total, 400)
        self.assertEqual(tuple(ally.mover.velocity), (0, 0, 0))
        self.assertFalse(ally.mover.airborne)
        self.assertEqual(player.body_script.component.entity_var(ULT_CHARGE).value(), 0)
