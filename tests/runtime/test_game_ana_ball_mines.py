"""Actual Minefield and Ana combat with the external 2578 graph bundle."""

import os
import unittest
from pathlib import Path
from unittest.mock import patch

import test_game_ana_baptiste_field as field_fixture
import test_game_ana_nano as nano_fixture
import test_game_ana_sombra_devices as sombra_fixture
from test_game_ana import set_static_wall
from test_game_combat import aim

from ow174.game import ball_mines, combat, stats
from ow174.game.content import find_hero
from ow174.game.script import graph
from ow174.game.server_snapshot import _Graph

BUNDLE = os.environ.get("ORPHEUS_BALL_MINE_BUNDLE")


@unittest.skipUnless(BUNDLE, "requires the external 2578 Minefield bundle")
class AnaBallMineTests(unittest.TestCase):
    @staticmethod
    def clear_graphs():
        for cache in (graph._data, graph._records, graph.graph, graph.mode_variables):
            cache.cache_clear()

    @classmethod
    def setUpClass(cls):
        cls.bundle = patch.object(graph, "GRAPHS_PATH", Path(BUNDLE))
        cls.bundle.start()
        cls.addClassCleanup(cls.clear_graphs)
        cls.addClassCleanup(cls.bundle.stop)
        cls.clear_graphs()
        if graph.entity_body(ball_mines.DEVICE) is None or graph.graph(ball_mines.LIFECYCLE) is None:
            raise ValueError("2578 and 151A are required")

    def deploy(self, *, ally_hero="Soldier: 76", landed=True):
        fixture = nano_fixture.AnaNanoTests()
        run, ana, _, ball = fixture.fight(ally_hero=ally_hero, enemy_hero="Wrecking Ball")
        fight = run.match.combat
        script = ball.body_script
        script.set_ult_charge(script.component.vars[338].value())
        run.step({ball: (0, 0, 32)})
        self.assertEqual(script.component.vars[342].value(), 0)
        for _ in range(70):
            run.step({})
            if len(fight.ball_mines.devices) == 15:
                break
        mines = tuple(fight.ball_mines.devices.values())
        self.assertEqual(len(mines), 15)
        self.assertTrue(all(mine.health == 50 and not mine.armed for mine in mines))
        if not landed:
            return run, ana, ball, mines
        for _ in range(100):
            run.step({})
            if all(mine.normal is not None for mine in mines):
                break
        self.assertTrue(all(mine.normal is not None and not mine.armed for mine in mines))
        return run, ana, ball, mines

    def arm(self, run, mines):
        for _ in range(110):
            if all(mine.armed for mine in mines):
                break
            run.step({})
        self.assertTrue(all(mine.armed for mine in mines))

    def test_minefield_arms_then_respects_team_and_cover_before_hitting_ana(self):
        run, ana, ball, mines = self.deploy()
        fight = run.match.combat
        mine = mines[0]
        x, y, z = mine.position
        place = field_fixture.AnaBaptisteFieldTests.place
        place(ana, (x, y, z - 1.05))
        target = next(item for item in fight._targets() if item.owner is ana)
        vitals = fight.vitals_of(target)
        run.step({})
        self.assertEqual(vitals.total, 200)
        ana.team = ball.team
        self.arm(run, mines)
        wait = mine.primary.node_state(mine.primary.graph.node(6))
        self.assertEqual(wait.ended_at - wait.start, 1500)
        self.assertEqual(vitals.total, 200)

        stored = _Graph({"match": run.match, "combat": fight, "owner": ball,
                         "minefield": fight.ball_mines}, None)
        stored.value(mine)
        stored.finish()
        collision = run.match.collision_world
        set_static_wall(run, ana, 0.5)
        ana.team = 1 - ball.team
        for _ in range(4):
            run.step({})
        self.assertEqual(vitals.total, 200)
        self.assertFalse(mine.triggered)

        run.match.collision_world = collision
        run.step({})
        self.assertTrue(mine.triggered)
        self.assertEqual(vitals.total, 70)
        self.assertEqual(ball.stats.value(stats.HERO_DAMAGE), 130)
        self.assertGreater(ana.mover.position[1], y)
        self.assertLess(ana.mover.position[2], z - 1.05)
        for _ in range(20):
            run.step({})
        self.assertNotIn(mine.body, fight.ball_mines.devices)
        self.assertEqual(vitals.total, 70)
        run.frame += 1600
        run.match.update(run.frame * combat.TICK, run.frame, frames=False)
        self.assertFalse(fight.ball_mines.devices)

    def test_ana_actual_weapons_damage_landed_mines(self):
        for button, scoped, expected in ((1, False, 0), (1, True, 0), (8, False, 45),
                                         (16, False, 0), (0x800, False, 20)):
            with self.subTest(button=button, scoped=scoped):
                run, ana, ball, mines = self.deploy()
                fight = run.match.combat
                mine = mines[0]
                x, y, z = mine.position
                field = field_fixture.AnaBaptisteFieldTests()
                field.place(ball, (x + 15, y, z))
                if button == 0x800:
                    field.place(ana, (x, y, z - 1.3))
                else:
                    field.place(ana, (x, y, z - 8))
                direction = aim(fight._eye(ana), mine.centre())
                held = 2 if scoped else 0
                for _ in range(35 if scoped else 0):
                    run.step({ana: (*direction, held)})
                run.step({ana: (*direction, button | held)})
                for _ in range(70):
                    if mine.health < 50:
                        break
                    run.step({ana: (*direction, held)})
                self.assertEqual(mine.health, expected)
                self.assertEqual(mine.body in fight.ball_mines.devices, expected > 0)
                self.assertNotIn(mine.body, fight.vitals)
                self.assertFalse(fight.is_asleep(ball))
                self.assertEqual(ana.stats.value(stats.HERO_DAMAGE), 0)

    def test_sleeping_ball_keeps_armed_mines_until_body_replacement(self):
        run, ana, ball, mines = self.deploy()
        fight = run.match.combat
        self.arm(run, mines)
        fixture = nano_fixture.AnaNanoTests()
        place = field_fixture.AnaBaptisteFieldTests.place
        x, y, z = ana.mover.position
        place(ball, (x + 6, y, z))
        run.step({})
        direction = fixture.direction(fight, ana, ball)
        run.step({ana: (*direction, 8)})
        for _ in range(60):
            if fight.is_asleep(ball):
                break
            run.step({ana: (*direction, 0)})
        self.assertTrue(fight.is_asleep(ball))
        self.assertTrue(all(mine.armed for mine in mines))
        x, y, z = mines[0].position
        place(ana, (x, y, z - 1.05))
        run.step({})
        self.assertEqual(fight.vitals_of(fixture.target(fight, ana)).total, 70)
        self.assertTrue(fight.is_asleep(ball))
        source = ball.body
        self.assertTrue(run.match.switch_hero(ball, ball.hero))
        self.assertNotEqual(ball.body, source)
        self.assertFalse(fight.ball_mines.devices)
        self.assertFalse(fight.ball_mines.volleys)

    def test_rifle_kills_ball_and_allied_nano_reduces_his_mines(self):
        run, ana, ball, mines = self.deploy()
        fight = run.match.combat
        helper = run.players[1]
        fixture = nano_fixture.AnaNanoTests()
        field = field_fixture.AnaBaptisteFieldTests()
        self.assertTrue(run.match.switch_hero(helper, ana.hero))
        x, y, z = ana.mover.position
        field.place(helper, (x - 5, y, z))
        field.place(ball, (x + 6, y, z))
        self.arm(run, mines)
        fixture.cast(run, helper, ana)
        vitals = fight.vitals_of(fixture.target(fight, ana))
        self.assertGreater(vitals.nano_until, fight.now)

        target = fixture.target(fight, ball)
        health = fight.vitals_of(target)
        health.damage(health.total - 40)
        x, y, z = target.feet
        field.fire(run, ana, (x, y + 1, z), 1)
        self.assertTrue(fight.is_dead(ball))
        self.assertEqual(health.killer, ana.body)
        self.assertTrue(all(mine.armed for mine in mines))

        x, y, z = mines[0].position
        field.place(ana, (x, y, z - 1.05))
        run.step({})
        self.assertTrue(mines[0].triggered)
        self.assertEqual(vitals.total, 135)
        self.assertEqual(ball.stats.value(stats.HERO_DAMAGE), 65)

    def test_dart_during_paid_minefield_cancels_only_unreleased_groups(self):
        for cast_frame, count, positions in ((19, 0, ()), (17, 1, (33,)), (12, 7, (33, 8))):
            with self.subTest(cast_frame=cast_frame):
                fixture = nano_fixture.AnaNanoTests()
                run, ana, _, ball = fixture.fight(enemy_hero="Wrecking Ball")
                fight = run.match.combat
                x, y, z = ana.mover.position
                field_fixture.AnaBaptisteFieldTests.place(ball, (x + 6, y, z))
                run.step({})
                direction = fixture.direction(fight, ana, ball)
                script = ball.body_script
                cost = script.component.vars[338].value()
                script.set_ult_charge(cost)
                for frame in range(50):
                    run.step({ana: (*direction, 8 if frame == 0 else 0),
                              ball: (0, 0, 32 if frame == cast_frame else 0)})
                    if fight.is_asleep(ball):
                        break
                self.assertTrue(fight.is_asleep(ball))
                self.assertLess(script.component.vars[342].value(), cost)
                deployed = tuple(fight.ball_mines.devices)
                self.assertEqual(len(deployed), count)
                self.assertEqual(tuple(pos for _, pos in fight.ball_mines.volleys), positions)
                for _ in range(45):
                    run.step({ball: (0, 0, 32)})
                self.assertTrue(fight.is_asleep(ball))
                self.assertEqual(tuple(fight.ball_mines.devices), deployed)

    def test_actual_projected_barrier_absorbs_mine_without_moving_ana(self):
        run, ana, _, mines = self.deploy()
        fight = run.match.combat
        helper = run.players[1]
        fixture = nano_fixture.AnaNanoTests()
        place = field_fixture.AnaBaptisteFieldTests.place
        self.assertTrue(run.match.switch_hero(helper, find_hero("Zarya")))
        x, y, z = ana.mover.position
        place(helper, (x - 5, y, z))
        self.arm(run, mines)
        direction = fixture.direction(fight, helper, ana)
        run.step({helper: (*direction, 16)})
        barrier = fight.body_barriers[ana.body]
        self.assertEqual(barrier.health, 200)

        x, y, z = mines[0].position
        place(ana, (x, y, z - 1.05))
        before = tuple(ana.mover.position)
        run.step({})
        self.assertTrue(mines[0].triggered)
        self.assertEqual(barrier.health, 70)
        self.assertEqual(fight.vitals_of(fixture.target(fight, ana)).total, 200)
        self.assertEqual(tuple(ana.mover.position), before)
        self.assertNotIn(ana.body, fight.external_impulses)

    def test_actual_emp_disarms_mines_until_its_deadline(self):
        run, ana, ball, mines = self.deploy(ally_hero="Sombra")
        fight = run.match.combat
        sombra = run.players[1]
        mine = mines[0]
        x, y, z = mine.position
        fixture = sombra_fixture.AnaSombraDeviceTests()
        fixture.place(ball, (x + 30, y, z))
        fixture.place(sombra, (x, y, z - 8))
        self.arm(run, mines)
        self.assertNotIn(mine.body, {target.entity for target in fight.sombra_controls.hackable(sombra)})
        direction = aim(fight._eye(sombra), mine.centre())
        for _ in range(55):
            run.step({sombra: (*direction, 2)})
        self.assertTrue(mine.armed)
        self.assertNotIn(mine.body, fight.sombra_controls.locks)
        run.step({})
        lock = fixture.cast(run, sombra, mine, 32)
        self.assertIs(lock.application.component, mine.component)
        self.assertEqual(lock.pulse.graph.index, 0x0B32)
        self.assertFalse(mine.armed)
        self.assertEqual(mine.health, 50)

        def stored():
            capture = _Graph({"fight": fight, "owner": ball, "minefield": fight.ball_mines}, None)
            capture.value(lock)
            return capture.finish()

        snapshot = stored()
        deadline = lock.until
        lock.until += 1
        self.assertNotEqual(stored(), snapshot)
        lock.until = deadline
        self.assertEqual(stored(), snapshot)

        fixture.place(ana, (x, y, z - 1.05))
        vitals = fixture.vitals(run, ana)
        deadline = round(lock.until / combat.TICK)
        run.frame = deadline - 1
        run.match.update(run.frame * combat.TICK, run.frame, frames=False)
        self.assertFalse(mine.triggered)
        self.assertFalse(mine.armed)
        self.assertEqual(vitals.total, 200)
        run.frame = deadline
        run.match.update(run.frame * combat.TICK, run.frame, frames=False)
        self.assertNotIn(mine.body, fight.sombra_controls.locks)
        self.assertTrue(mine.triggered)
        self.assertEqual(vitals.total, 70)

    def test_ana_destroys_a_mine_disabled_by_emp_during_flight(self):
        run, ana, ball, mines = self.deploy(ally_hero="Sombra", landed=False)
        fight = run.match.combat
        sombra = run.players[1]
        mine = mines[0]
        x, _, z = mine.position
        y = ana.mover.position[1]
        fixture = sombra_fixture.AnaSombraDeviceTests()
        fixture.place(ball, (x + 30, y, z))
        fixture.place(sombra, (x, y, z - 8))
        lock = fixture.cast(run, sombra, mine, 32)
        self.assertIsNone(mine.normal)
        for _ in range(160):
            run.step({})
        self.assertIsNotNone(mine.normal)
        self.assertFalse(mine.armed)
        self.assertEqual(mine.health, 50)

        fixture.place(ana, (x, y, z - 1.05))
        field_fixture.AnaBaptisteFieldTests().fire(run, ana, mine.centre(), 1, scoped=True)
        self.assertEqual(fixture.vitals(run, ana).total, 200)
        self.assertEqual(mine.health, 0)
        self.assertNotIn(mine.body, fight.ball_mines.devices)
        self.assertNotIn(mine.body, fight.sombra_controls.locks)
        self.assertNotIn(mine.body, fight.sombra_controls.device_receivers)
        self.assertTrue(lock.application.stopped)
        self.assertTrue(lock.pulse.stopped)

    def test_overlapping_emps_hold_mine_disabled_until_last_deadline(self):
        run, ana, ball, mines = self.deploy(ally_hero="Sombra")
        fight = run.match.combat
        sombra = run.players[1]
        mine = mines[0]
        x, y, z = mine.position
        fixture = sombra_fixture.AnaSombraDeviceTests()
        fixture.place(ball, (x + 30, y, z))
        fixture.place(sombra, (x, y, z - 8))
        self.arm(run, mines)
        first = fixture.cast(run, sombra, mine, 32)
        for _ in range(100):
            run.step({})
        second = fixture.cast(run, sombra, mine, 32)
        self.assertIsNot(first.application, second.application)
        self.assertGreater(second.until, first.until)
        fixture.place(ana, (x, y, z - 1.05))
        vitals = fixture.vitals(run, ana)

        run.frame = round(first.until / combat.TICK)
        run.match.update(run.frame * combat.TICK, run.frame, frames=False)
        self.assertTrue(first.application.stopped)
        self.assertFalse(second.application.stopped)
        self.assertEqual(fight.sombra_controls.locks[mine.body], [second])
        self.assertFalse(mine.armed)
        self.assertEqual(vitals.total, 200)

        run.frame = round(second.until / combat.TICK)
        run.match.update(run.frame * combat.TICK, run.frame, frames=False)
        self.assertTrue(second.application.stopped)
        self.assertNotIn(mine.body, fight.sombra_controls.device_receivers)
        self.assertTrue(mine.triggered)
        self.assertEqual(vitals.total, 70)

    def test_emp_disables_exposed_mine_but_leaves_a_covered_mine_armed(self):
        run, _, ball, mines = self.deploy(ally_hero="Sombra")
        fight = run.match.combat
        sombra = run.players[1]
        covered = mines[0]
        x, y, z = covered.position
        exposed = min(mines, key=lambda mine: mine.position[2])
        fixture = sombra_fixture.AnaSombraDeviceTests()
        fixture.place(ball, (x + 30, y, z))
        fixture.place(sombra, (x, y, z - 8))
        self.arm(run, mines)
        set_static_wall(run, sombra, 6)
        fixture.cast(run, sombra, exposed, 32)
        self.assertFalse(exposed.armed)
        self.assertTrue(covered.armed)
        self.assertNotIn(covered.body, fight.sombra_controls.device_receivers)
