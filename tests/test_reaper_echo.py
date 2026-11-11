"""Actual Echo Duplicate input against Reaper and the copied Reaper's combat."""

import unittest
from contextlib import nullcontext
from dataclasses import replace
from unittest.mock import patch

import test_game_reaper as reaper_tests
import test_mccree_flashbang as flashbang_tests
from test_game_combat import aim
from test_game_mover import box, wall

from ow174.game import combat, dva_matrix, echo_duplicate, pools, reaper, sigma_grasp, stats
from ow174.game.collision import World, flat_world
from ow174.game.commands import Command
from ow174.game.content import SOLDIER, find_hero, heroes
from ow174.game.script.driver import ULT_CHARGE, ULT_COST


class ReaperEchoTests(unittest.TestCase):
    def scene(self, *, extra=()):
        run, owner, echo, _ = reaper_tests.ReaperTests().scenario(names=("Reaper", "Echo", *extra))
        self.assertTrue(run.match.switch_hero(echo, find_hero("Echo")))
        run.match.collision_world = flat_world(1)
        for actor, position in ((echo, (0, 1, 0)), (owner, (0, 1, 1.5))):
            actor.mover.position[:] = position
            actor.mover.body = None
            actor.body_script.ult_seconds = 0
            actor.body_script.set_ult_charge(0)
        for _ in range(15):
            run.step({})
        return run, owner, echo, run.match.combat

    @staticmethod
    def pool(fight, player):
        return fight.vitals_of(next(t for t in fight._targets() if t.owner is player))

    def copy(self, run, echo):
        script = echo.body_script
        script.set_ult_charge(script.component.entity_var(ULT_COST).value())
        run.step({echo: (0, 0, 32)})
        self.assertEqual(echo.hero.guid, reaper.HERO)
        return run.match.combat.echo_duplicates.copies[echo.entity]

    def copied_soldier(self, *, distance=8, extra=()):
        run, owner, echo, fight = self.scene(extra=("Soldier", *extra))
        soldier = run.players[2]
        for actor, position in ((owner, (2, 1, 0)), (soldier, (0, 1, 3))):
            actor.mover.position[:] = position
            actor.mover.body = None
        for _ in range(12):
            run.step({})
        script = echo.body_script
        script.set_ult_charge(script.component.entity_var(ULT_COST).value())
        run.step({echo: (0, 0, 32)})
        self.assertEqual(echo.hero.guid, SOLDIER)
        self.assertEqual(fight.echo_duplicates.copies[echo.entity].target, soldier.body)
        echo.body_script.ult_seconds = 0
        owner.mover.position[:] = (0, 1, distance)
        soldier.mover.position[:] = (8, 1, 0)
        owner.mover.body = soldier.mover.body = None
        for _ in range(12):
            run.step({})
        return run, owner, echo, fight

    def copied_helix_defender(self, name, distance, *, offset=0, body=None):
        run, owner, echo, fight = self.copied_soldier(distance=12, extra=(name,))
        defender = run.players[3]
        hero = find_hero(name)
        self.assertTrue(run.match.switch_hero(defender, hero if body is None else replace(hero, body=body)))
        run.match.game_map = replace(run.match.game_map, free_for_all=False)
        owner.team = defender.team = 0
        echo.team = run.players[2].team = 1
        defender.mover.position[:] = (offset, 1, distance)
        defender.mover.body = None
        for _ in range(12):
            run.step({})
        return run, owner, echo, defender, fight

    def test_actual_deflect_returns_copied_helix_to_echo_after_duplicate_ends(self):
        run, owner, echo, genji, fight = self.copied_helix_defender("Genji", 8)
        copy = fight.echo_duplicates.copies[echo.entity]
        run.frame = copy.until - 26
        run.match.update(run.frame * combat.TICK, run.frame, frames=False)
        heading = (-32768, 0)
        run.step({genji: (*heading, 16)})
        deflect = next(i for i in genji.body_script.component.instances.values() if i.graph.index == 0x03A3)
        for _ in range(11):
            run.step({genji: (*heading, 0)})
        self.assertTrue(deflect.states[10].active)
        x, y, z = genji.mover.position
        incoming = aim(fight._eye(echo), (x, y + 1.35, z))
        run.step({echo: (*incoming, 2), genji: (*heading, 0)})
        original, = fight.rockets
        self.assertEqual(original.duplicate_body, copy.body)
        for _ in range(12):
            run.step({genji: (*heading, 0)})
            if fight.rockets and fight.rockets[0].reflected:
                break
        returned, = fight.rockets
        self.assertTrue(returned.reflected)
        self.assertIs(returned.shooter, genji)
        self.assertIsNone(returned.duplicate_body)
        self.assertEqual(echo.body_script.component.entity_var(ULT_CHARGE).value(), 0)
        self.assertEqual(echo.body, copy.body)
        while run.frame < copy.until:
            run.step({})
        self.assertEqual(echo.hero.guid, echo_duplicate.HERO)
        self.assertEqual(self.pool(fight, echo).total, 200)
        self.assertIn(returned, fight.rockets)
        for _ in range(20):
            run.step({})
        self.assertEqual(echo.hero.guid, echo_duplicate.HERO)
        self.assertEqual(self.pool(fight, owner).total, 250)
        self.assertEqual(self.pool(fight, genji).total, 200)
        self.assertEqual(self.pool(fight, echo).total, 80)
        self.assertEqual(genji.stats.value(stats.HERO_DAMAGE), 120)
        self.assertFalse(fight.rockets)

    def test_actual_matrix_absorbs_copied_helix_then_reaper_is_exposed_after_release(self):
        from ow174.game.mover import DVA_MOVER_SHA256
        from ow174.game.script.graph import DVA_GRAPH_SHA256

        if not (DVA_MOVER_SHA256 and DVA_GRAPH_SHA256 and dva_matrix.TAG_CATALOG_SHA256):
            self.skipTest("requires the pinned 0407 mover, graph and projectile tag supplements")
        run, owner, echo, mech, fight = self.copied_helix_defender("Dva", 5, offset=1.6, body=pools.DVA_MECH)
        matrix = next(i for i in mech.body_script.component.instances.values()
                      if i.graph.index == dva_matrix.GRAPH)
        for _ in range(100):
            run.step({})
        facing = aim(fight._eye(mech), fight._eye(echo))
        for _ in range(30):
            run.step({mech: (*facing, 2)})
        self.assertTrue(matrix.states[dva_matrix.STATE].active)
        x, y, z = owner.mover.position
        incoming = aim(fight._eye(echo), (x, y + 1, z))
        with patch.object(fight.projectiles, "absorbed", wraps=fight.projectiles.absorbed) as absorbed:
            run.step({echo: (*incoming, 2), mech: (*facing, 2)})
            rocket, = fight.rockets
            self.assertEqual(rocket.definition, 0x040000000000072C)
            self.assertEqual(rocket.duplicate_body, echo.body)
            self.assertIsNotNone(rocket.shot)
            for _ in range(20):
                run.step({mech: (*facing, 2)})
            absorbed.assert_called_once_with(rocket.shot)
        self.assertFalse(fight.rockets)
        self.assertNotIn(rocket.shot, fight.projectiles.shots)
        self.assertEqual(self.pool(fight, mech).total, 600)
        self.assertEqual(self.pool(fight, owner).total, 250)
        charge = echo.body_script.component.entity_var(ULT_CHARGE)
        self.assertEqual(charge.value(), 0)
        run.step({mech: (*facing, 0)})
        run.frame += 390
        run.match.update(run.frame * combat.TICK, run.frame, frames=False)
        self.assertFalse(matrix.states[dva_matrix.STATE].active)
        run.step({echo: (*incoming, 2)})
        for _ in range(25):
            run.step({})
        self.assertEqual(self.pool(fight, mech).total, 600)
        self.assertEqual(self.pool(fight, owner).total, 130)
        self.assertEqual(charge.value(), 780)

    def test_actual_reaper_shell_breaks_soldier_copy_and_retires_its_helix(self):
        run, owner, echo, fight = self.copied_soldier(distance=2)
        copy = fight.echo_duplicates.copies[echo.entity]
        reaper_tests.ReaperTests().fire(run, owner, echo)
        self.assertEqual(self.pool(fight, echo).total, 80)
        for _ in range(35):
            run.step({})
        run.step({echo: (16384, 0, 2)})
        rocket, = fight.rockets
        self.assertEqual(rocket.duplicate_body, copy.body)
        self.assertIsNotNone(rocket.shot)
        reaper_tests.ReaperTests().fire(run, owner, echo)
        self.assertLess(fight.tick, copy.until)
        self.assertEqual(echo.hero.guid, echo_duplicate.HERO)
        self.assertNotEqual(echo.body, copy.body)
        self.assertFalse(fight.rockets)
        run.step({})
        self.assertNotIn(rocket.shot, fight.projectiles.shots)
        self.assertTrue(rocket.shot.observers)
        self.assertTrue(all(delivery.finished and not delivery.explode for delivery in rocket.shot.observers))
        self.assertEqual(owner.stats.value(stats.ELIMINATIONS), 0)
        self.assertEqual(self.pool(fight, owner).total, 250)
        self.assertEqual(self.pool(fight, echo).total, 200)

    def test_actual_grasp_absorbs_copied_helix_without_damage_or_copy_charge(self):
        run, owner, echo, sigma, fight = self.copied_helix_defender("Sigma", 5)
        heading = (-32768, 0)
        for frame in range(12):
            run.step({sigma: (*heading, 8 if frame == 0 else 0)})
        grasp = next(i for i in sigma.body_script.component.instances.values()
                     if i.graph.index == sigma_grasp.GRASP)
        self.assertTrue(grasp.states[1].active)
        x, y, z = sigma.mover.position
        incoming = aim(fight._eye(echo), (x, y + 1, z))
        run.step({echo: (*incoming, 2), sigma: (*heading, 0)})
        self.assertTrue(fight.rockets)
        for _ in range(20):
            run.step({sigma: (*heading, 0)})
        self.assertFalse(fight.rockets)
        self.assertEqual(grasp.vars[21312].value(), 120)
        self.assertEqual(self.pool(fight, sigma).total, 400)
        self.assertEqual(self.pool(fight, owner).total, 250)
        self.assertEqual(echo.body_script.component.entity_var(ULT_CHARGE).value(), 0)

    def test_actual_copied_soldier_rifle_and_helix_damage_fund_copy_charge(self):
        run, owner, echo, fight = self.copied_soldier()
        health = self.pool(fight, owner)
        charge = echo.body_script.component.entity_var(ULT_CHARGE)
        reaper_tests.ReaperTests().fire(run, echo, owner)
        self.assertEqual(health.total, 230)
        self.assertEqual(charge.value(), 130)
        run.step({})
        x, y, z = owner.mover.position
        angles = aim(fight._eye(echo), (x, y + 1, z))
        run.step({echo: (*angles, 2)})
        for _ in range(25):
            run.step({})
        self.assertEqual(health.total, 110)
        self.assertEqual(charge.value(), 910)

    def test_actual_copied_soldier_return_retires_in_flight_helix(self):
        for leased in (True, False):
            with self.subTest(leased=leased):
                run, owner, echo, fight = self.copied_soldier(distance=24)
                copy = fight.echo_duplicates.copies[echo.entity]
                run.frame = copy.until - 4
                run.match.update(run.frame * combat.TICK, run.frame, frames=False)
                x, y, z = owner.mover.position
                lease = (
                    nullcontext() if leased else patch.object(fight.projectiles, "fired", return_value=None)
                )
                with lease:
                    run.step({echo: (*aim(fight._eye(echo), (x, y + 1, z)), 2)})
                rocket, = fight.rockets
                self.assertEqual(rocket.shot is not None, leased)
                self.assertEqual(rocket.duplicate_body, echo.body)
                self.assertEqual(self.pool(fight, owner).total, 250)
                run.frame = copy.until + 45
                run.match.update(run.frame * combat.TICK, run.frame, frames=False)
                self.assertEqual(echo.hero.guid, echo_duplicate.HERO)
                self.assertFalse(fight.rockets)
                self.assertEqual(self.pool(fight, owner).total, 250)
                self.assertEqual(echo.body_script.component.entity_var(ULT_CHARGE).value(), 0)

    def test_actual_copied_soldier_helix_cooldown_expires_after_wraith_miss(self):
        run, owner, echo, fight = self.copied_soldier()
        script = echo.body_script
        charge = script.component.entity_var(ULT_CHARGE)
        weapon = next(i for i in script.component.instances.values() if i.graph.index == 0x0254)
        x, y, z = owner.mover.position
        angles = aim(fight._eye(echo), (x, y + 1, z))
        run.step({owner: (0, 0, 8), echo: (*angles, 2)})
        self.assertTrue(reaper.intangible(owner))
        self.assertGreater(weapon.vars[61].value(), 5)
        for _ in range(25):
            run.step({})
        self.assertEqual(self.pool(fight, owner).total, 250)
        self.assertEqual(charge.value(), 0)
        in_flight = tuple(fight.rockets)
        run.step({echo: (*angles, 2)})
        self.assertEqual(tuple(fight.rockets), in_flight)
        run.frame += 390
        run.match.update(run.frame * combat.TICK, run.frame, frames=False)
        self.assertFalse(reaper.intangible(owner))
        self.assertEqual(weapon.vars[61].value(), 0)
        run.step({})
        run.step({echo: (*angles, 2)})
        for _ in range(25):
            run.step({})
        self.assertEqual(self.pool(fight, owner).total, 130)
        self.assertEqual(charge.value(), 780)

    def test_actual_copied_visor_aims_rifle_at_reaper_but_keeps_helix_manual(self):
        run, owner, echo, fight = self.copied_soldier()
        health = self.pool(fight, owner)
        script = echo.body_script
        charge = script.component.entity_var(ULT_CHARGE)
        x, y, z = owner.mover.position
        yaw, pitch = aim(fight._eye(echo), (x, y + 1, z))
        offset = yaw + 3641, pitch
        run.step({echo: (*offset, 1)})
        self.assertEqual(health.total, 250)
        for _ in range(12):
            run.step({})
        script.set_ult_charge(script.component.entity_var(ULT_COST).value())
        run.step({echo: (yaw, pitch, 32)})
        visor = next(i for i in script.component.instances.values() if i.graph.index == 0x0259)
        for _ in range(85):
            state = visor.states.get(7)
            if state is not None and state.active:
                break
            run.step({})
        self.assertTrue(visor.states[7].active)
        self.assertEqual(charge.value(), 0)
        run.step({echo: (*offset, 1)})
        self.assertEqual(health.total, 230)
        self.assertEqual(charge.value(), 0)
        run.step({})
        run.step({echo: (*offset, 2)})
        for _ in range(35):
            run.step({})
        self.assertEqual(health.total, 230)
        self.assertEqual(charge.value(), 0)
        run.step({owner: (0, 0, 8), echo: (*offset, 1)})
        self.assertTrue(reaper.intangible(owner))
        self.assertEqual(health.total, 230)
        self.assertEqual(charge.value(), 0)
        run.frame += 450
        run.match.update(run.frame * combat.TICK, run.frame, frames=False)
        self.assertEqual(echo.hero.guid, SOLDIER)
        self.assertFalse(visor.states[7].active)
        self.assertFalse(reaper.intangible(owner))
        run.step({echo: (*offset, 1)})
        self.assertEqual(health.total, 230)
        for _ in range(12):
            run.step({})
        run.step({echo: (yaw, pitch, 1)})
        self.assertEqual(health.total, 210)
        self.assertEqual(charge.value(), 130)

    def test_actual_echo_q_copies_reaper_body_and_enhances_damage_charge(self):
        run, owner, echo, fight = self.scene()
        old = echo.body_script
        body, team, pose = echo.body, echo.team, tuple(echo.mover.position)
        cost = old.component.entity_var(ULT_COST).value()
        self.assertEqual(cost, 2254)
        old.set_ult_charge(cost - 1)
        run.step({echo: (0, 0, 32)})
        self.assertEqual(echo.hero.guid, echo_duplicate.HERO)
        old.set_ult_charge(cost)
        run.step({echo: (0, 0, 32)})
        self.assertEqual(echo.body, body)
        run.step({})
        copy = self.copy(run, echo)
        self.assertTrue(old.retired)
        self.assertNotEqual(echo.body, body)
        self.assertEqual(copy.target, owner.body)
        self.assertEqual(copy.until - run.frame, 938)
        self.assertEqual((echo.team, tuple(echo.mover.position)), (team, pose))
        script = echo.body_script
        self.assertEqual(script.frame, run.frame)
        self.assertTrue(script.component.entity_var(28985).value())
        self.assertEqual(script.ult_gain_bonus, 5.5)
        self.assertEqual(script.component.entity_var(ULT_CHARGE).value(), 0)
        script.ult_seconds = 0
        own = self.pool(fight, echo)
        own.damage(100)
        self.pool(fight, owner)
        reaper_tests.ReaperTests().fire(run, echo, owner)
        self.assertEqual(self.pool(fight, owner).total, 130)
        self.assertAlmostEqual(own.total, 150 + 120 * reaper.REAPING_RATIO)
        self.assertEqual(script.component.entity_var(ULT_CHARGE).value(),
                         120 * (1 + script.ult_gain_bonus))

    def test_actual_reaper_shell_breaks_echo_copy_without_elimination(self):
        run, owner, echo, fight = self.scene()
        self.pool(fight, echo).damage(150)
        original_skin = (0x123, True)
        echo.skin = original_skin
        self.copy(run, echo)
        self.assertEqual(echo.skin, (0, False))
        copy_body = echo.body
        self.pool(fight, echo).damage(245)
        own = self.pool(fight, owner)
        own.damage(100)
        x, y, z = echo.mover.position
        run.step({owner: (*aim(fight._eye(owner), (x, y + 1, z)), 1)})
        self.assertEqual(echo.hero.guid, echo_duplicate.HERO)
        self.assertEqual(echo.skin, original_skin)
        self.assertNotEqual(echo.body, copy_body)
        self.assertNotIn(echo.entity, fight.echo_duplicates.copies)
        self.assertNotIn(copy_body, fight.vitals)
        self.assertEqual(self.pool(fight, echo).total, 100)
        self.assertAlmostEqual(own.total, 150 + 5 * reaper.REAPING_RATIO)
        self.assertEqual(owner.body_script.component.entity_var(ULT_CHARGE).value(), 5)
        self.assertFalse(owner.client.messages(combat.KILL_NOTICE))
        for _ in range(35):
            run.step({})
        reaper_tests.ReaperTests().fire(run, owner, echo)
        self.assertTrue(fight.is_dead(echo))
        self.assertEqual(owner.body_script.component.entity_var(ULT_CHARGE).value(), 105)
        self.assertEqual(len(owner.client.messages(combat.KILL_NOTICE)), 1)

    def test_actual_echo_q_and_copied_shell_share_one_input_batch(self):
        run, owner, echo, fight = self.scene()
        script = echo.body_script
        script.set_ult_charge(script.component.entity_var(ULT_COST).value())
        run.frame += 2
        run.match.update(run.frame * combat.TICK, run.frame, frames=False)
        yaw, pitch = aim(fight._eye(echo), (0, 2, 1.5))
        echo.take_commands([Command(run.frame - 1, buttons=32),
                            Command(run.frame, yaw=yaw, pitch=pitch, buttons=1)])
        self.assertEqual(echo.hero.guid, reaper.HERO)
        self.assertEqual(echo.body_script.frame, run.frame)
        self.assertTrue(script.retired)
        self.assertEqual(self.pool(fight, owner).total, 130)

    def test_actual_echo_refuses_wraith_and_copied_wraith_rejects_a_shell(self):
        run, owner, echo, fight = self.scene()
        script = echo.body_script
        cost = script.component.entity_var(ULT_COST).value()
        script.set_ult_charge(cost)
        run.step({owner: (0, 0, 8), echo: (0, 0, 32)})
        self.assertTrue(reaper.intangible(owner))
        self.assertEqual(echo.hero.guid, echo_duplicate.HERO)
        self.assertEqual(script.component.entity_var(ULT_CHARGE).value(), cost)
        for _ in range(220):
            if not reaper.intangible(owner):
                break
            run.step({})
        self.assertFalse(reaper.intangible(owner))
        self.copy(run, echo)
        run.step({echo: (0, 0, 8)})
        self.assertTrue(reaper.intangible(echo))
        reaper_tests.ReaperTests().fire(run, owner, echo)
        self.assertEqual(self.pool(fight, echo).total, 250)
        self.assertEqual(owner.body_script.component.entity_var(ULT_CHARGE).value(), 0)

    def test_actual_echo_q_rejects_rear_range_cover_and_friendly_reaper(self):
        for case in ("rear", "range", "cover", "friendly"):
            with self.subTest(case=case):
                run, owner, echo, fight = self.scene()
                if case == "range":
                    owner.mover.position[2] = 42
                    owner.mover.body = None
                    run.match.collision_world = World(box(-50, 50, -1, 1, -50, 50))
                elif case == "cover":
                    run.match.collision_world = World(box(-30, 30, -1, 1, -30, 30)
                                                      + wall(-3, 0.75, 3, 0.75))
                elif case == "friendly":
                    run.match.game_map = replace(run.match.game_map, free_for_all=False)
                    owner.team = echo.team = 0
                for _ in range(12):
                    run.step({})
                script = echo.body_script
                cost = script.component.entity_var(ULT_COST).value()
                script.set_ult_charge(cost)
                run.step({echo: (-32768 if case == "rear" else 0, 0, 32)})
                self.assertEqual(echo.hero.guid, echo_duplicate.HERO)
                self.assertFalse(fight.echo_duplicates.copies)
                self.assertEqual(script.component.entity_var(ULT_CHARGE).value(), cost)

    def test_actual_echo_q_rejects_protected_shadow_then_copies_after_landing(self):
        run, owner, echo, fight = self.scene()
        for button in (16, 0, 1):
            run.step({owner: (16384, 7400, button)})
        record = fight.reaper_shadows[owner.body]
        self.assertIsNotNone(record["cast"])
        for _ in range(100):
            if reaper.intangible(owner):
                break
            run.step({})
        self.assertTrue(reaper.intangible(owner))
        script = echo.body_script
        cost = script.component.entity_var(ULT_COST).value()
        script.set_ult_charge(cost)
        run.step({echo: (0, 0, 32)})
        self.assertEqual(echo.hero.guid, echo_duplicate.HERO)
        self.assertEqual(script.component.entity_var(ULT_CHARGE).value(), cost)
        for _ in range(100):
            if record["moved"] and not reaper.intangible(owner):
                break
            run.step({})
        self.assertTrue(record["moved"])
        self.assertFalse(reaper.intangible(owner))
        x, y, z = owner.mover.position
        run.step({echo: (*aim(fight._eye(echo), (x, y + 1, z)), 32)})
        self.assertEqual(echo.hero.guid, reaper.HERO)

    def test_actual_flashbang_blocks_echo_copy_until_fresh_q_after_stun(self):
        run, owner, echo, fight = self.scene(extra=("McCree",))
        caster = run.players[2]
        self.assertTrue(run.match.switch_hero(caster, heroes()[0x02E0000000000042]))
        run.match.game_map = replace(run.match.game_map, free_for_all=False)
        owner.team, caster.team, echo.team = 0, 0, 1
        caster.mover.position[:] = (1.6, 1, 0)
        caster.mover.body = None
        flashbang_tests.FlashbangTests().cast(run, caster, echo)
        self.assertTrue(fight.is_stunned(echo))
        script = echo.body_script
        cost = script.component.entity_var(ULT_COST).value()
        script.set_ult_charge(cost)
        for _ in range(55):
            run.step({echo: (0, 0, 32)})
        self.assertFalse(fight.is_stunned(echo))
        self.assertEqual(echo.hero.guid, echo_duplicate.HERO)
        self.assertEqual(script.component.entity_var(ULT_CHARGE).value(), cost)
        run.step({})
        copy = self.copy(run, echo)
        self.assertEqual(copy.target, owner.body)

    def test_actual_copied_reaper_earns_blossom_then_expires_during_channel(self):
        outcomes = []
        for skip in (False, True):
            with self.subTest(skip=skip):
                run, owner, echo, fight = self.scene(extra=("Second target",))
                victim = run.players[2]
                victim.mover.position[:] = (1.5, 1, 0)
                victim.mover.body = None
                self.pool(fight, echo).damage(120)
                copy = self.copy(run, echo)
                body = echo.body
                script = echo.body_script
                script.ult_seconds = 0
                charge = script.component.entity_var(ULT_CHARGE)
                for _ in range(3):
                    reaper_tests.ReaperTests().fire(run, echo, owner)
                    for _ in range(35):
                        run.step({})
                self.assertTrue(fight.is_dead(owner))
                self.assertEqual(echo.body, body)
                self.assertEqual(charge.value(), 250 * (1 + script.ult_gain_bonus))
                reaper_tests.ReaperTests().fire(run, echo, victim)
                self.assertEqual(charge.value(), 2100)
                run.frame = copy.until - 21
                run.match.update(run.frame * combat.TICK, run.frame, frames=False)
                self.assertEqual(echo.body, body)
                run.step({echo: (0, 0, 32)})
                self.assertIsNotNone(reaper.blossom_state(script))
                self.assertEqual(charge.value(), 0)
                health = self.pool(fight, victim)
                self.assertEqual(health.total, 46)
                end = copy.until + 35
                for tick in ((end,) if skip else range(run.frame + 1, end + 1)):
                    run.frame = tick
                    run.match.update(tick * combat.TICK, tick, frames=False)
                self.assertEqual(echo.hero.guid, echo_duplicate.HERO)
                self.assertNotEqual(echo.body, body)
                self.assertNotIn(body, fight.reaper_blossoms)
                self.assertIsNone(echo.body_script.component.entity_var(28985, create=False))
                self.assertEqual(self.pool(fight, echo).total, 100)
                self.assertEqual(health.total, 12)
                self.assertEqual(fight.replacement_ticks[echo.body], copy.until)
                outcomes.append((health.total, tuple(echo.mover.position), self.pool(fight, echo).total))
        self.assertEqual(len(outcomes), 2)
        self.assertEqual(outcomes[0], outcomes[1])
