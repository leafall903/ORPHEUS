"""Execute Coalescence through mixed bodies and world geometry."""

import unittest
from contextlib import ExitStack
from unittest.mock import patch

import test_game_moira as fixtures
from test_game_combat import Run

from ow174.game import combat, cryo
from ow174.game.collision import World
from ow174.game.commands import CROUCH, Command
from ow174.game.content import heroes
from ow174.game.script import expr
from ow174.game.script.runtime import ABORT, INSTANCE


def capsule_point_cover(origin, target, blocked_points):
    """Narrow static bands crossing the requested rays at their midpoints."""
    x, eye, z = origin
    target_x, feet, target_z = target.feet
    center = (x + target_x) * 0.5
    plane = (z + target_z) * 0.5
    triangles = []
    for fraction in blocked_points:
        midpoint = (eye + feet + target.height * fraction) * 0.5
        low, high = midpoint - 0.08, midpoint + 0.08
        left, right = center - 2, center + 2
        triangles.extend([
            (left, low, plane, right, low, plane, right, high, plane),
            (left, low, plane, right, high, plane, left, high, plane),
        ])
    return World(triangles)


class MoiraBeamGeometryTests(unittest.TestCase):
    start_ult = fixtures.MoiraOpenAcceptanceTests.start_ult
    setup_fight = fixtures.MoiraSprayTests.setup_fight

    def test_late_movement_poses_do_not_repeat_beam_samples_on_resume(self):
        for ally in (False, True):
            with self.subTest(ally=ally):
                run, source, _, recipient = self.setup_fight(ally=ally)
                fight = run.match.combat
                own = fight.vitals_of(next(t for t in fight._targets() if t.owner is source))
                self.start_ult(run, source)
                own.damage(50)
                before = recipient.total, own.total
                for _ in range(12):
                    run.frame += 1
                    run.match.update(run.frame * combat.TICK, run.frame)
                self.assertEqual((recipient.total, own.total), before)
                command = Command(run.frame)
                source.take_commands([command, command])
                dt = source.quanta / 1e6
                self.assertAlmostEqual(recipient.total - before[0], (140 if ally else -70) * dt, places=4)
                self.assertAlmostEqual(own.total - before[1], 50 * dt, places=4)

    def test_one_beam_pierces_enemy_ally_enemy_with_independent_health_operations(self):
        run = Run(names=("Moira", "NearEnemy", "Ally", "FarEnemy"))
        match = run.match
        match.bots.clear()
        match._pending_bot_scripts.clear()
        source = run.players[0]
        match.switch_hero(source, heroes()[fixtures.MOIRA])
        x, y, z = source.mover.position
        for index, player in enumerate(run.players[1:], 1):
            player.team = source.team if index == 2 else 1 - source.team
            player.mover.position = [x, y, z + index * 3]
        for _ in range(20):
            run.step({})
        targets = {target.owner: target for target in match.combat._targets()}
        health = [match.combat.vitals_of(targets[player]) for player in run.players[1:]]
        health[1].damage(100)
        instance = self.start_ult(run, source)
        before = [vitals.total for vitals in health]
        for _ in range(20):
            run.step({})
        changes = [vitals.total - old for vitals, old in zip(health, before, strict=True)]
        for actual, expected in zip(changes, (-22.4, 44.8, -22.4), strict=True):
            self.assertAlmostEqual(actual, expected, places=4)
        ally = instance.find_var(INSTANCE, 1184, create=False)
        enemies = instance.find_var(INSTANCE, 1182, create=False)
        self.assertIsNotNone(ally)
        self.assertIsNotNone(enemies)
        near, friendly, far = (expr.Entity(targets[player].entity) for player in run.players[1:])
        self.assertEqual(ally.value(), (friendly,))
        self.assertEqual(enemies.value(), (near, far))

        before = [vitals.total for vitals in health]
        run.step({source: (32768, 0, 0)})
        self.assertEqual([vitals.total for vitals in health], before)
        self.assertEqual(ally.value(), ())
        self.assertEqual(enemies.value(), ())

        run.step({source: (0, 0, 0)})
        self.assertEqual(ally.value(), (friendly,))
        self.assertEqual(enemies.value(), (near, far))

        own = match.combat.vitals_of(targets[source])
        own.damage(50)
        before = [own.total, *(vitals.total for vitals in health)]
        missing_target = {"$": "STUConfigVarDynamic", "m_identifier": 65534, "m_60DB8F99": INSTANCE}
        with ExitStack() as scope:
            for index in (12, 18, 20):
                scope.enter_context(patch.dict(instance.states[index].node.fields, m_FA3ECC43=missing_target))
            run.step({})
        self.assertEqual([own.total, *(vitals.total for vitals in health)], before)
        self.assertEqual(ally.value(), (friendly,))
        self.assertEqual(enemies.value(), (near, far))

        run.step({})
        dt = source.quanta / 1e6
        for vitals, previous, rate in zip((own, *health), before, (50, -70, 140, -70), strict=True):
            self.assertAlmostEqual(vitals.total - previous, rate * dt, places=4)

        run.frame += 550
        match.update(run.frame * combat.TICK, run.frame)
        for index, variable in ((17, ally), (19, enemies)):
            search = instance.states[index]
            self.assertFalse(search.active)
            self.assertIsNone(search.target_var)
            self.assertFalse(any(link.state is search for link in variable.links))
        self.assertEqual(ally.value(), (friendly,))
        self.assertEqual(enemies.value(), (near, far))

    def test_beam_health_routes_follow_their_own_state_lifetimes(self):
        for ally, stopped, expected in ((True, 18, 0), (True, 20, 140), (False, 18, -70), (False, 20, 0)):
            with self.subTest(ally=ally, stopped=stopped):
                run, source, _, recipient = self.setup_fight(ally=ally)
                if ally:
                    recipient.damage(100)
                instance = self.start_ult(run, source)
                self.assertTrue(instance.states[18].active)
                self.assertTrue(instance.states[20].active)
                instance.states[stopped].request(ABORT)
                instance.component.run_now()
                self.assertFalse(instance.states[stopped].active)
                self.assertTrue(instance.states[20 if stopped == 18 else 18].active)
                fight = run.match.combat
                own = fight.vitals_of(next(target for target in fight._targets() if target.owner is source))
                own.damage(50)
                before = recipient.total
                own_before = own.total
                run.step({})
                dt = source.quanta / 1e6
                self.assertAlmostEqual(recipient.total - before, expected * dt, places=4)
                self.assertAlmostEqual(own.total - own_before, (50 if stopped == 20 else 0) * dt, places=4)
                search = instance.states[17 if ally else 19]
                self.assertEqual(search.target_var.value(), (expr.Entity(recipient.entity),))

    def test_beam_reaches_allies_and_enemies_behind_live_cryo_without_crossing_world_cover(self):
        for wall in (False, True):
            with self.subTest(wall=wall):
                run = Run(names=("Moira", "Mei", "Ally", "Enemy"))
                match = run.match
                match.bots.clear()
                match._pending_bot_scripts.clear()
                source, mei, ally, enemy = run.players
                self.assertTrue(match.switch_hero(source, heroes()[fixtures.MOIRA]))
                self.assertTrue(match.switch_hero(mei, heroes()[cryo.HERO]))
                x, y, z = source.mover.position
                for index, actor in enumerate(run.players[1:], 1):
                    actor.team = source.team if actor is ally else 1 - source.team
                    actor.mover.position = [x, y, z + index * 3]
                    actor.mover.body = None
                for _ in range(20):
                    run.step({})
                fight = match.combat
                targets = {target.owner: target for target in fight._targets()}
                front, friendly, hostile = (fight.vitals_of(targets[actor]) for actor in (mei, ally, enemy))
                friendly.damage(100)
                self.start_ult(run, source)
                crouch = {mei: (-32768, 0, CROUCH)}
                for _ in range(20):
                    run.step(crouch)
                run.step({mei: (-32768, 0, CROUCH | 8)})
                self.assertTrue(fight.damage_immune(mei))
                origin, direction = fight._eye(source), combat.aim(0, 0)
                self.assertIsNotNone(fight._cryo_contact(source, origin, direction, 30))
                before = front.total, friendly.total, hostile.total
                collision = fight._collision()
                if wall:
                    plane = z + 4.5
                    collision = World([
                        (x-10, y-10, plane, x+10, y-10, plane, x+10, y+10, plane),
                        (x-10, y-10, plane, x+10, y+10, plane, x-10, y+10, plane),
                    ])
                with patch.object(fight, "_collision", return_value=collision):
                    for _ in range(20):
                        run.step(crouch)
                self.assertTrue(cryo.active(mei))
                self.assertGreaterEqual(front.total, before[0])
                self.assertAlmostEqual(friendly.total - before[1], 0 if wall else 44.8, places=4)
                self.assertAlmostEqual(hostile.total - before[2], 0 if wall else -22.4, places=4)

    def test_healing_and_damage_use_their_own_target_search_radius(self):
        for restricted_state in (17, 19):
            with self.subTest(restricted_state=restricted_state):
                run = Run(names=("Moira", "Ally", "Enemy"))
                match = run.match
                match.bots.clear()
                match._pending_bot_scripts.clear()
                source, ally, enemy = run.players
                self.assertTrue(match.switch_hero(source, heroes()[fixtures.MOIRA]))
                ally.team, enemy.team = source.team, 1 - source.team
                x, y, z = source.mover.position
                for actor, distance in ((ally, 6), (enemy, 9)):
                    actor.mover.position = [x, y, z + distance]
                for _ in range(20):
                    run.step({})
                fight = match.combat
                targets = {target.owner: target for target in fight._targets()}
                friendly, hostile = (fight.vitals_of(targets[actor]) for actor in (ally, enemy))
                friendly.damage(100)
                self.start_ult(run, source)
                instance = next(i for i in source.body_script.component.instances.values()
                                if i.graph.index == 0x1007)
                search_radius = instance.state(restricted_state).node.field("m_radius")
                self.assertEqual(search_radius["m_value"], 30)
                before_friendly, before_hostile = friendly.total, hostile.total
                with patch.dict(search_radius, m_value=3):
                    for _ in range(20):
                        run.step({})
                self.assertAlmostEqual(
                    friendly.total - before_friendly, 0 if restricted_state == 17 else 44.8, places=4)
                self.assertAlmostEqual(
                    hostile.total - before_hostile, 0 if restricted_state == 19 else -22.4, places=4)

    def test_beam_stops_at_static_wall_and_cannot_reach_beyond_thirty_metres(self):
        for distance, wall in ((3, True), (31, False), (29, False)):
            with self.subTest(distance=distance, wall=wall):
                run, source, _target, health = self.setup_fight(ally=False, distance=distance)
                fight = run.match.combat
                collision = fight._collision()
                if wall:
                    x, y, z = source.mover.position
                    z += 1.5
                    collision = World([
                        (x - 10, y - 10, z, x + 10, y - 10, z, x + 10, y + 10, z),
                        (x - 10, y - 10, z, x + 10, y + 10, z, x - 10, y + 10, z),
                    ])
                with patch.object(fight, "_collision", return_value=collision):
                    self.start_ult(run, source)
                    before = health.total
                    for _ in range(20):
                        run.step({})
                self.assertAlmostEqual(before - health.total, 22.4 if distance == 29 else 0, places=4)

    def test_free_for_all_beam_damages_same_numbered_team_instead_of_healing(self):
        run, source, _target, health = self.setup_fight(free_for_all=True)
        self.start_ult(run, source)
        before = health.total
        for _ in range(20):
            run.step({})
        self.assertAlmostEqual(before - health.total, 22.4, places=4)

    def test_off_axis_volume_and_target_specific_cover(self):
        for ally, offset, covered in ((True, 1, False), (False, 1, False),
                                      (False, 1, True), (False, 1.7, False)):
            with self.subTest(ally=ally, offset=offset, covered=covered):
                run, source, _target, health = self.setup_fight(ally=ally, offset=offset)
                fight = run.match.combat
                collision = fight._collision()
                if covered:
                    x, y, z = source.mover.position
                    left, right, plane = x + 0.2, x + 1.5, z + 1.5
                    collision = World([
                        (left, y-10, plane, right, y-10, plane, right, y+10, plane),
                        (left, y-10, plane, right, y+10, plane, left, y+10, plane),
                    ])
                    self.assertIsNone(combat.ray_world(collision, fight._eye(source), combat.aim(0, 0), 3))
                with patch.object(fight, "_collision", return_value=collision):
                    self.start_ult(run, source)
                    before = health.total
                    for _ in range(20):
                        run.step({})
                expected = (44.8 if ally else -22.4) if offset == 1 and not covered else 0
                self.assertAlmostEqual(health.total - before, expected, places=4)

    def test_active_beam_follows_command_yaw_and_pitch(self):
        run, source, _target, health = self.setup_fight(ally=False, distance=1, offset=4)
        with patch.object(run.match.combat, "_collision", return_value=World([])):
            self.start_ult(run, source)
            for yaw, pitch, damage in ((0, 0, 0), (16384, 0, 22.4), (16384, 8192, 0),
                                       (16384, 0, 22.4)):
                with self.subTest(yaw=yaw, pitch=pitch):
                    before = health.total
                    for _ in range(20):
                        run.step({source: (yaw, pitch, 0)})
                    self.assertAlmostEqual(before - health.total, damage, places=4)

    def test_candidate_visibility_tries_lower_and_upper_capsule_points(self):
        for ally, blocked_points in ((False, (0.6,)), (True, (0.3,)), (False, (0.3, 0.6))):
            with self.subTest(ally=ally, blocked_points=blocked_points):
                run, source, target, health = self.setup_fight(ally=ally)
                fight = run.match.combat
                self.start_ult(run, source)
                shown = next(candidate for candidate in fight._targets() if candidate.owner is target.owner)
                collision = capsule_point_cover(fight._eye(source), shown, blocked_points)
                before = health.total
                with patch.object(fight, "_collision", return_value=collision):
                    for _ in range(20):
                        run.step({})
                expected = (44.8 if ally else -22.4) if len(blocked_points) == 1 else 0
                self.assertAlmostEqual(health.total - before, expected, places=4)
