"""Actual Orisa and Zenyatta input against Echo."""

import unittest
from dataclasses import replace

import test_echo_combat as echo_tests
import test_echo_copied_cassidy as cassidy_tests
from test_game_combat import aim, in_world
from test_game_mover import box, wall

from ow174.game import combat, immunity
from ow174.game.collision import World
from ow174.game.content import SOLDIER, heroes
from ow174.game.mover import Mover, mover_data


class EchoLiveDefenseTests(unittest.TestCase):
    def session(self, hero, distance=5):
        run, echo, enemy, _, _ = echo_tests.EchoPrimaryTests().session(distance=distance)
        run.match.switch_hero(enemy, heroes()[hero], preserve_pose=True)
        for _ in range(25):
            run.step({})
        fight = run.match.combat
        target = next(t for t in fight._targets() if t.owner is enemy)
        x, y, z = target.feet
        toward = aim(fight._eye(echo), (x, y+1, z))
        echo.body_script.ult_seconds = 0
        echo.body_script.set_ult_charge(0)
        return run, echo, enemy, target, toward

    def test_fortify_reduces_tri_shot_then_expires(self):
        run, echo, orisa, target, toward = self.session(immunity.ORISA)
        fight = run.match.combat
        vitals = fight.vitals_of(target)
        initial = vitals.total
        run.step({orisa: (0, 0, 8)})
        self.assertTrue(immunity.control_immune(orisa))
        self.assertAlmostEqual(immunity.damage_scale(orisa, orisa.body), .6)
        echo_tests.EchoPrimaryTests().fire(run, echo, toward)
        self.assertAlmostEqual(initial - vitals.total, 15.6, places=4)
        for _ in range(270):
            if not immunity.control_immune(orisa):
                break
            run.step({})
        self.assertFalse(immunity.control_immune(orisa))
        before = vitals.total
        echo_tests.EchoPrimaryTests().fire(run, echo, toward)
        self.assertEqual(before - vitals.total, 36)

    def test_fortify_suppresses_tri_shot_head_multiplier_until_expiry(self):
        run, echo, orisa, target, _ = self.session(immunity.ORISA, distance=2)
        fight = run.match.combat
        vitals = fight.vitals_of(target)
        x, y, z = target.feet
        angles = aim(fight._eye(echo), (x, y + target.eye - .05, z))
        run.step({orisa: (0, 0, 8)})
        for fortified, expected in ((True, 15.6), (False, 87)):
            with self.subTest(fortified=fortified):
                if not fortified:
                    for _ in range(270):
                        if not immunity.fortified(orisa):
                            break
                        run.step({})
                self.assertEqual(immunity.fortified(orisa), fortified)
                before = vitals.total
                notices = len(echo.client.messages(combat.HIT))
                run.step({echo: (*angles, 1)})
                self.assertEqual(len(fight.rockets), 3)
                a, b = target.segment()
                for rocket in fight.rockets:
                    contact = combat.ray_capsule(rocket.position, rocket.direction, a, b, target.radius)
                    self.assertIsNotNone(contact)
                    height = rocket.position[1] + contact * rocket.direction[1]
                    self.assertGreaterEqual(height, y + target.eye - combat.HEAD_BELOW)
                for _ in range(25):
                    run.step({})
                self.assertAlmostEqual(before - vitals.total, expected, places=4)
                hits = echo.client.messages(combat.HIT)[notices:]
                self.assertEqual(len(hits), 3)
                self.assertTrue(all(bool(hit["+0x78"]["+0x8"] & combat.CRITICAL) != fortified
                                    for hit in hits))

    def test_transcendence_denies_echo_damage_and_hit_credit_until_expiry(self):
        run, echo, zen, target, toward = self.session(immunity.ZENYATTA, distance=2)
        fight = run.match.combat
        vitals = fight.vitals_of(target)
        initial = vitals.total
        script = zen.body_script
        script.set_ult_charge(script.component.entity_var(338).value())
        run.step({zen: (0, 0, 32)})
        self.assertTrue(immunity.control_immune(zen))
        echo_tests.EchoPrimaryTests().fire(run, echo, toward)
        echo_tests.EchoStickyTests().launch(run, echo, toward)
        self.assertEqual(len(fight.rockets), 6)
        self.assertTrue(all(rocket.attached_entity == target.entity for rocket in fight.rockets))
        for _ in range(90):
            run.step({})
        self.assertFalse(fight.rockets)
        run.step({echo: (*toward, 0x800)})
        melee = next(i for i in echo.body_script.component.instances.values() if i.graph.index == 0x43)
        self.assertEqual(melee.states[4].counter, 1)
        for _ in range(65):
            run.step({})
        echo_tests.EchoBeamTests().start_beam(run, echo, toward)
        self.assertTrue(immunity.control_immune(zen))
        self.assertEqual(vitals.total, initial)
        self.assertEqual(echo.client.messages(combat.HIT), [])
        self.assertEqual(echo.body_script.component.entity_var(342).value(), 0)
        for _ in range(400):
            if not immunity.control_immune(zen):
                break
            run.step({})
        self.assertFalse(immunity.control_immune(zen))
        echo_tests.EchoPrimaryTests().fire(run, echo, toward)
        self.assertEqual(vitals.total, initial - 51)

    def test_transcendence_actual_healing_obeys_team_range_and_wall(self):
        run, echo, zen, _, _ = self.session(immunity.ZENYATTA)
        match = run.match
        match.game_map = replace(match.game_map, free_for_all=False)
        echo.team, zen.team = 0, 1
        ally = match.add_player(3, "Ally", SOLDIER, zen.team, False)
        in_world(match, ally)
        match.switch_hero(ally, heroes()[SOLDIER])
        run.players.append(ally)
        x, y, z = zen.mover.position
        ally.mover = Mover((x+4, y, z), world=match.collision_world, data=mover_data(ally.hero.body))
        for _ in range(25):
            run.step({})
        fight = match.combat
        ally_target = next(t for t in fight._targets() if t.owner is ally)
        echo_target = next(t for t in fight._targets() if t.owner is echo)
        ally_health, echo_health = fight.vitals_of(ally_target), fight.vitals_of(echo_target)
        # Seed missing health independently of the ability's healing clock.
        fight._damage(echo, ally_target, 100, False, None, run.frame)
        fight._damage(zen, echo_target, 100, False, None, run.frame)
        injured = ally_health.total
        echo_injured = echo_health.total
        collision = match.collision_world
        match.collision_world = World(box(x-25, x+25, y-1, y, z-25, z+25)
                                      + wall(x+2, z-5, x+2, z+5, y0=y, y1=y+6))
        script = zen.body_script
        script.set_ult_charge(script.component.entity_var(338).value())
        run.step({zen: (0, 0, 32)})
        for _ in range(12):
            run.step({})
        self.assertEqual(ally_health.total, injured)
        match.collision_world = collision
        run.step({})  # Target query publishes membership for the next health tick.
        self.assertEqual(ally_health.total, injured)
        for _ in range(10):
            run.step({})
        self.assertAlmostEqual(ally_health.total - injured, 48, places=4)
        self.assertEqual(echo_health.total, echo_injured)
        ally.mover = Mover((x+18, y, z), world=collision, data=mover_data(ally.hero.body))
        for _ in range(2):
            run.step({})
        outside = ally_health.total
        for _ in range(12):
            run.step({})
        self.assertEqual(ally_health.total, outside)
        self.assertEqual(echo_health.total, echo_injured)

    def test_actual_copied_flashbang_cannot_stun_fortify_or_transcendence(self):
        for hero, expected in ((immunity.ORISA, 10), (immunity.ZENYATTA, 0)):
            with self.subTest(hero=hero):
                run, echo, enemy, _ = cassidy_tests.EchoCopiedCassidyTests().session()
                match = run.match
                fight = match.combat
                match.switch_hero(enemy, heroes()[hero], preserve_pose=True)
                for _ in range(25):
                    run.step({})
                if hero == immunity.ZENYATTA:
                    script = enemy.body_script
                    script.set_ult_charge(script.component.entity_var(338).value())
                run.step({enemy: (0, 0, 32 if hero == immunity.ZENYATTA else 8)})
                self.assertTrue(immunity.control_immune(enemy))
                target = next(t for t in fight._targets() if t.owner is enemy)
                vitals = fight.vitals_of(target)
                before = vitals.total
                x, y, z = target.feet
                direction = aim(fight._eye(echo), (x, y+1, z))
                run.step({echo: (*direction, 16)})
                launched = False
                for _ in range(65):
                    run.step({})
                    launched |= any(rocket.shooter is echo for rocket in fight.rockets)
                    self.assertFalse(fight.is_stunned(enemy))
                self.assertTrue(launched)
                self.assertAlmostEqual(before - vitals.total, expected, places=5)

    def test_copied_transcendence_heals_allies_and_stops_at_copy_expiry(self):
        run, echo, zen, _, toward = self.session(immunity.ZENYATTA)
        match = run.match
        match.game_map = replace(match.game_map, free_for_all=False)
        echo.team, zen.team = 0, 1
        fight = match.combat
        ally = match.add_player(3, "Ally", SOLDIER, echo.team, False)
        in_world(match, ally)
        match.switch_hero(ally, heroes()[SOLDIER])
        run.players.append(ally)
        x, y, z = echo.mover.position
        ally.mover = Mover((x+4, y, z), world=match.collision_world, data=mover_data(ally.hero.body))
        for _ in range(25):
            run.step({})
        echo.body_script.set_ult_charge(echo.body_script.component.entity_var(338).value())
        run.step({echo: (*toward, 32)})
        self.assertTrue(fight.echo_duplicates.active(echo))
        self.assertEqual(echo.hero.guid, immunity.ZENYATTA)
        until = fight.echo_duplicates.states[echo.entity].expires
        while until - fight.now > .25:
            run.step({})
        target = next(t for t in fight._targets() if t.owner is ally)
        health = fight.vitals_of(target)
        fight._damage(zen, target, 125, False, None, run.frame)
        before = health.total
        script = echo.body_script
        script.ult_seconds = 0
        script.set_ult_charge(script.component.entity_var(338).value())
        run.step({echo: (0, 0, 32)})
        self.assertTrue(immunity.control_immune(echo))
        while fight.echo_duplicates.active(echo):
            run.step({})
        healed = health.total
        self.assertGreater(healed, before)
        self.assertLess(healed, health.maximum)
        self.assertTrue(script.retired)
        self.assertFalse(immunity.control_immune(echo))
        for _ in range(30):
            run.step({})
        self.assertEqual(health.total, healed)
