"""Actual Winston Tesla input against Zarya's barriers and five NPC bodies."""

import unittest

import test_game_zarya_npcs as fixtures
from test_game_combat import Run, aim
from test_game_mover import FLOOR, wall

from ow174.game import combat, npc, zarya
from ow174.game.collision import World
from ow174.game.commands import CROUCH, FIRE, Command
from ow174.game.content import heroes

WINSTON = 0x02E0000000000009


def setup(run):
    winston, caster, receiver = run.players
    match = run.match
    match.bots.clear()
    match._pending_bot_scripts.clear()
    match.switch_hero(winston, heroes()[WINSTON])
    match.switch_hero(caster, heroes()[zarya.HERO])
    winston.team, caster.team, receiver.team = 0, 1, 1
    for player, position in ((winston, (0, 1, 0)), (caster, (0, 1, 3)), (receiver, (1, 1, 5))):
        player.mover.position[:] = position
        player.mover.body = None
    return winston, caster, receiver


def health(fight, owner):
    return fight.vitals_of(next(target for target in fight._targets() if target.owner is owner)).total


def weapon(winston):
    return next(instance for instance in winston.body_script.component.instances.values()
                if instance.graph.index == 0x00FE)


class ZaryaTeslaTests(unittest.TestCase):
    def test_actual_winston_body_uses_authored_standing_and_crouched_eyes(self):
        run = Run(names=("Winston", "Caster", "Receiver"))
        winston, _, _ = setup(run)
        fight = run.match.combat
        self.assertEqual(winston.hero.body, 0x04000000000001C0)
        for buttons, height in ((0, 1.5), (CROUCH, 1.05)):
            run.step({winston: (0, 0, buttons)})
            body = next(target for target in fight._targets() if target.owner is winston)
            self.assertEqual(winston.mover.crouched, bool(buttons))
            self.assertAlmostEqual(body.eye, height)
            self.assertAlmostEqual(fight._eye(winston)[1] - body.feet[1], height)

    def test_actual_tesla_slices_stop_at_personal_or_projected_barrier_until_next_pose(self):
        for projected in (False, True):
            with self.subTest(projected=projected):
                run = Run(names=("Winston", "Caster", "Receiver"))
                winston, caster, receiver = setup(run)
                fight = run.match.combat
                protected, exposed = (receiver, caster) if projected else (caster, receiver)
                cast = aim(fight._eye(caster), fight._eye(receiver)) if projected else (0, 0)
                run.step({caster: (*cast, 16 if projected else 8)})
                self.assertTrue(fight.zarya_barriers.protects(protected))
                pools = fight.zarya_barriers.projected if projected else fight.zarya_barriers.active
                pool = pools[caster.body]
                before, other = health(fight, protected), health(fight, exposed)
                fire = {winston: (0, 0, FIRE)}
                for _ in range(10):
                    run.step(fire)
                self.assertTrue(weapon(winston).state(10).active)
                self.assertTrue(weapon(winston).state(13).active)
                self.assertAlmostEqual(pool.remaining, 190.4)
                self.assertAlmostEqual(caster.body_script.component.entity_var(87).value(), 1.92, places=4)
                self.assertEqual(health(fight, protected), before)
                self.assertAlmostEqual(other - health(fight, exposed), 9.6)

                pool.remaining = 0.5
                run.step(fire)
                self.assertEqual(pool.remaining, 0)
                self.assertEqual(health(fight, protected), before)
                self.assertAlmostEqual(caster.body_script.component.entity_var(87).value(), 2.02, places=4)
                for frame in (run.frame, run.frame - 1):
                    fight.command(winston, Command(frame, buttons=FIRE))
                self.assertEqual(health(fight, protected), before)
                run.step(fire)
                self.assertAlmostEqual(before - health(fight, protected), 0.96)
                after = health(fight, protected)
                for _ in range(5):
                    run.step({})
                self.assertEqual(health(fight, protected), after)

    def test_same_tesla_slice_can_reach_both_active_barriers(self):
        run = Run(names=("Winston", "Caster", "Receiver"))
        winston, caster, receiver = setup(run)
        fight = run.match.combat
        run.step({caster: (0, 0, 8)})
        angles = aim(fight._eye(caster), fight._eye(receiver))
        run.step({caster: (*angles, 16)})
        self.assertTrue(fight.zarya_barriers.protects(caster))
        self.assertTrue(fight.zarya_barriers.protects(receiver))
        personal = fight.zarya_barriers.active[caster.body]
        projected = fight.zarya_barriers.projected[caster.body]
        personal.remaining = 0.5
        bodies = health(fight, caster), health(fight, receiver)
        run.step({winston: (0, 0, FIRE)})
        self.assertEqual(personal.remaining, 0)
        self.assertAlmostEqual(projected.remaining, 199.04)
        self.assertEqual((health(fight, caster), health(fight, receiver)), bodies)
        self.assertAlmostEqual(caster.body_script.component.entity_var(87).value(), 0.292, places=4)
        self.assertFalse(winston.client.messages(combat.HIT))

    def test_tesla_empty_magazine_blocks_damage_until_authored_reload(self):
        run = Run(names=("Winston", "Caster", "Receiver"))
        winston, caster, receiver = setup(run)
        fight = run.match.combat
        caster.mover.position[2] = 12
        receiver.team = winston.team
        fire = {winston: (0, 0, FIRE)}
        graph = weapon(winston)
        for _ in range(400):
            run.step(fire)
            if graph.vars[53].value() == 0 and not graph.state(13).active:
                break
        self.assertEqual(graph.vars[53].value(), 0)
        self.assertFalse(graph.state(13).active)
        caster.mover.position[2] = 3
        caster.mover.body = None
        before = health(fight, caster)
        for _ in range(20):
            run.step(fire)
            self.assertEqual(health(fight, caster), before)
            self.assertEqual(graph.vars[53].value(), 0)
        for _ in range(120):
            run.step(fire)
            if health(fight, caster) < before:
                break
        self.assertGreater(graph.vars[53].value(), 0)
        self.assertTrue(graph.state(13).active)
        self.assertAlmostEqual(before - health(fight, caster), 0.96)

    def test_actual_tesla_excludes_cover_range_and_allies(self):
        for case in ("wall", "range", "rear", "ally"):
            with self.subTest(case=case):
                run = Run(names=("Winston", "Caster", "Receiver"))
                winston, caster, receiver = setup(run)
                fight = run.match.combat
                receiver.team = winston.team
                if case == "wall":
                    run.match.collision_world = World(FLOOR + wall(-8, 2, 8, 2))
                elif case == "range":
                    caster.mover.position[2] = 10
                elif case == "rear":
                    caster.mover.position[2] = -3
                else:
                    caster.team = winston.team
                caster.mover.body = None
                run.step({caster: (0, 0, 8)})
                self.assertTrue(fight.zarya_barriers.protects(caster))
                before = health(fight, caster)
                for _ in range(6):
                    run.step({winston: (0, 0, FIRE)})
                pool = fight.zarya_barriers.active[caster.body]
                self.assertEqual(pool.remaining, 200)
                self.assertEqual(health(fight, caster), before)
                self.assertFalse(winston.client.messages(combat.HIT))


@unittest.skipUnless(fixtures.NPC_DATA, "requires actual five-NPC graphs and movers")
class ZaryaTeslaNpcTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        fixtures.ZaryaFiveNpcTests.setUpClass()

    @classmethod
    def tearDownClass(cls):
        fixtures.ZaryaFiveNpcTests.tearDownClass()

    def test_projected_barrier_intercepts_tesla_for_each_actual_npc(self):
        for definition in fixtures.ZaryaFiveNpcTests.catalog.values():
            with self.subTest(npc=definition.name):
                run = Run(names=("Winston", "Caster", "Receiver"))
                winston, caster, receiver = setup(run)
                match = run.match
                fight = match.combat
                caster.mover.position[:] = (4, 1, 4)
                caster.mover.body = None
                receiver.team = winston.team
                bot = npc.spawn(match, definition, (0, 1, 4), team=caster.team)
                target = next(t for t in fight._targets() if t.owner is bot)
                angles = aim(fight._eye(caster), (0, 1 + target.eye, 4))
                run.step({caster: (*angles, 16)})
                self.assertTrue(fight.zarya_barriers.protects(bot))
                pool = fight.zarya_barriers.projected[caster.body]
                self.assertEqual(pool.recipient, bot.entity)
                pool.remaining = 0.5
                before = health(fight, bot)
                armour = fight.vitals[bot.entity].now[combat.ARMOUR_POOL] > 0
                fire = {winston: (0, 0, FIRE)}
                run.step(fire)
                self.assertEqual(pool.remaining, 0)
                self.assertAlmostEqual(caster.body_script.component.entity_var(87).value(), 0.1)
                self.assertEqual(health(fight, bot), before)
                self.assertFalse(winston.client.messages(combat.HIT))
                run.step(fire)
                self.assertAlmostEqual(before - health(fight, bot), 0.768 if armour else 0.96)
