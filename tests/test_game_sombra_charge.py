"""Actual0B8B/EMP combat charge; scoped body/tag/lifecycle bridges are reconstructed."""

import math
import os
import unittest
from unittest.mock import patch

import test_game_sombra_combat as fixture
import test_server_snapshot as stored
from test_game_combat import aim

from ow174.game import combat, npc, stats
from ow174.game.commands import FIRE, Command
from ow174.game.content import heroes
from ow174.game.script import runtime
from ow174.game.world import ARMOUR_POOL, HEALTH_POOL


class SombraChargeTests(unittest.TestCase):
    def setUp(self):
        if not os.environ.get("ORPHEUS_ULTIMATE_DATA"):
            self.skipTest("set ORPHEUS_ULTIMATE_DATA to the external direct-body tag context")

    def make(self, *, natural=False):
        f = fixture.SombraCombatTests()
        f.setUp()
        script = f.a.body_script
        self.assertTrue(f.run.match.combat.sombra_charge.enabled(script))
        if not natural:
            script.ult_seconds = 0
            f.press(0)
        script.set_ult_charge(0)
        return f

    @staticmethod
    def charge(f):
        return f.a.body_script.component.entity_var(342).value()

    def test_authored_natural_clock_is_five_per_second_and_stops_at_cost(self):
        f = self.make(natural=True)
        root = next(i for i in f.a.body_script.component.instances.values() if i.graph.index == 0x0B8B)
        wait = root.node_state(root.graph.node(3))
        self.assertTrue(wait.active)
        remaining = math.ceil((wait.start + 1000 - root.component.now) / 16)
        f.press(0, remaining - 1)
        self.assertEqual(self.charge(f), 0)
        f.press(0)
        self.assertEqual(self.charge(f), 5)
        f.press(0, 125)
        self.assertEqual(self.charge(f), 15)
        f.a.body_script.set_ult_charge(1399)
        f.press(0, 70)
        self.assertEqual(self.charge(f), 1400)
        f.press(0, 100)
        self.assertEqual(self.charge(f), 1400)

    def test_world_only_emp_recovery_keeps_natural_charge_deadlines(self):
        for world_only in (False, True):
            with self.subTest(world_only=world_only):
                f = self.make(natural=True)
                match = f.run.match
                f.a.body_script.set_ult_charge(1400)
                f.press(0x20)
                cast = f.run.frame
                self.assertEqual(self.charge(f), 0)
                previous = 0
                for elapsed, expected in ((353, 0), (414, 0), (415, 5), (477, 5), (478, 10)):
                    if world_only:
                        at = cast + elapsed + 1  # the update leaves the endpoint open for real input
                        match.update(at * combat.TICK, at)
                    else:
                        f.press(0, elapsed - previous)
                    self.assertEqual(self.charge(f), expected, elapsed)
                    previous = elapsed

                if world_only:
                    yaw, pitch = aim(match.combat._eye(f.a), (0, 1, 1.5))
                    f.a.take_commands([Command(at - 1, yaw=yaw, pitch=pitch, buttons=FIRE)])
                    self.assertEqual(f.health(f.b), 200)
                    self.assertEqual(self.charge(f), 10)
                    f.a.take_commands([Command(at, yaw=yaw, pitch=pitch, buttons=FIRE)])
                else:
                    f.press(FIRE)
                self.assertEqual(f.health(f.b), 192)
                self.assertEqual(self.charge(f), 18)

    def test_world_only_natural_charge_caps_and_admits_a_fresh_emp(self):
        f = self.make(natural=True)
        match = f.run.match
        script = f.a.body_script
        root = next(i for i in script.component.instances.values() if i.graph.index == 0x0B8B)
        wait = root.node_state(root.graph.node(3))
        first = (wait.start + wait.ms) // script.frame_ms
        third = (wait.start + wait.ms * 3) // script.frame_ms
        script.set_ult_charge(1389)
        match.update(first * combat.TICK, first)
        self.assertEqual(self.charge(f), 1389)
        match.update((third + 1) * combat.TICK, third + 1)
        self.assertEqual(self.charge(f), 1400)
        at = third + 150
        match.update(at * combat.TICK, at)
        self.assertEqual(self.charge(f), 1400)
        self.assertFalse(wait.active)

        f.a.take_commands([Command(at, buttons=0x20)])
        self.assertEqual(self.charge(f), 0)
        match.update((at + 60) * combat.TICK, at + 60)
        effect, = match.combat.sombra_controls.locks[f.b.body]
        self.assertEqual(effect.until_tick, at + 334)
        self.assertEqual(self.charge(f), 0)

    def test_actual_pistol_and_melee_credit_pool_loss_and_release_adds_no_charge(self):
        f = self.make()
        f.press(FIRE)
        f.press(0, 3)
        self.assertEqual(f.health(f.b), 192)
        self.assertEqual(self.charge(f), 8)
        f.press(fixture.MELEE)
        f.press(0, 45)
        self.assertEqual(f.health(f.b), 162)
        self.assertEqual(self.charge(f), 38)
        f.press(0, 80)
        self.assertEqual(self.charge(f), 38)

    def test_original_gain_multiplier_and_cap_apply_to_actual_damage(self):
        f = self.make()
        f.a.body_script.component.set(9532, 2)
        f.press(FIRE)
        f.press(0, 3)
        self.assertEqual(self.charge(f), 16)
        f.a.body_script.component.set(9532, 0)
        f.press(0, 5)
        before = f.health(f.b)
        f.press(FIRE, 10)
        self.assertLess(f.health(f.b), before)
        self.assertEqual(self.charge(f), 16)
        f.a.body_script.component.set(9532, 2)
        f.a.body_script.set_ult_charge(1399)
        f.press(0, 5)
        f.press(FIRE, 10)
        f.press(0, 3)
        self.assertEqual(self.charge(f), 1400)

    def test_actual_armor_shields_and_fatal_overkill_use_resolved_loss(self):
        for hero, expected in ((0x02E0000000000006, 4), (0x02E0000000000020, 8)):
            with self.subTest(hero=hero):
                f = self.make()
                f.run.match.switch_hero(f.b, heroes()[hero])
                f.b.mover.position[:] = (0, 0, 1.5)
                f.b.mover.body = None
                f.press(0, 50)
                before = f.health(f.b)
                f.press(FIRE)
                self.assertEqual(before - f.health(f.b), expected)
                self.assertEqual(self.charge(f), expected)
        f = self.make()
        target = next(t for t in f.run.match.combat._targets() if t.owner is f.b)
        pools = f.run.match.combat.vitals_of(target)
        pools.now[HEALTH_POOL] = 3  # isolate overkill from the normal200 pool
        pools.now[ARMOUR_POOL] = 0
        f.press(FIRE)
        self.assertEqual(f.health(f.b), 0)
        self.assertEqual(self.charge(f), 3)
        self.assertEqual(f.a.stats.value(stats.HERO_DAMAGE), 3)

    def test_actual_miss_wall_allied_body_and_received_damage_do_not_credit(self):
        for mode in ("miss", "wall", "ally", "received"):
            with self.subTest(mode=mode):
                f = self.make()
                if mode == "miss":
                    f.run.step({f.a: (16384, 0, FIRE)})
                elif mode == "wall":
                    f.wall()
                    f.press(FIRE)
                elif mode == "ally":
                    f.run.match.game_map = f.run.match.game_map.__class__(**{
                        **f.run.match.game_map.__dict__, "free_for_all": False,
                    })
                    f.b.team = f.a.team
                    f.press(FIRE)
                else:
                    f.press(FIRE, shooter=f.b, target=f.a)
                    self.assertLess(f.health(f.a), 200)
                self.assertEqual(self.charge(f), 0)

    def test_emp_shield_drain_is_excluded_by_original_tags_and_active_cast_blocks_gain(self):
        f = self.make()
        f.run.match.switch_hero(f.b, heroes()[0x02E0000000000020])
        f.b.mover.position[:] = (0, 0, 1.5)
        f.b.mover.body = None
        f.press(0, 50)
        f.a.body_script.set_ult_charge(1400)
        f.press(32)
        f.press(0, 30)
        self.assertEqual(f.health(f.b), 50)
        self.assertEqual(self.charge(f), 0)
        self.assertTrue(f.a.body_script.component.entity_var(842).value())
        # Use the real remaining shield/health target with original damage tags;
        # this is a damage-adapter fixture while the actual cast is still running.
        target = next(t for t in f.run.match.combat._targets() if t.owner is f.b)
        f.run.match.combat._damage(f.a, target, 1, False, None, f.run.frame)
        self.assertEqual(self.charge(f), 0)
        f.press(0, 20)
        before = f.health(f.b)
        f.press(FIRE, 10)
        self.assertLess(f.health(f.b), before)
        self.assertEqual(self.charge(f), 0)
        f.press(0, 5)
        # Original0B31/node15 adds5 seconds of charge lockout after the cast.
        for _ in range(400):
            if not f.a.body_script.component.entity_var(842).value():
                break
            f.press(0)
        self.assertFalse(f.a.body_script.component.entity_var(842).value())
        f.press(FIRE)
        self.assertEqual(self.charge(f), 8)

    def test_excluded_tags_still_refuse_charge_after_ultimate_has_ended(self):
        f = self.make()
        target = next(t for t in f.run.match.combat._targets() if t.owner is f.b)
        for tag in (0x0D8000000000025A, 0x0D800000000001FD):
            f.run.match.combat._damage(f.a, target, 8, False, None, f.run.frame, hit_tags=(tag,))
            self.assertEqual(self.charge(f), 0)
        f.press(FIRE)
        self.assertEqual(self.charge(f), 8)

    def test_actual_enemy_beacon_destruction_does_not_award_character_charge(self):
        f = self.make()
        f.run.match.switch_hero(f.b, heroes()[fixture.SOMBRA])
        f.b.mover.position[:] = (0, 0, 1.5)
        f.b.mover.body = None
        f.b.body_script.ult_seconds = 0
        f.press(0, 50)
        f.b.body_script.set_ult_charge(0)
        f.press(16, 20)
        f.press(0, 200)
        actor = next(iter(f.run.match.combat.translocators.entries.values()))
        yaw, pitch = fixture.aim(f.run.match.combat._eye(f.b), actor.position)
        f.run.step({f.b: (yaw, pitch, FIRE)})
        self.assertNotIn(actor.entity, f.run.match.combat.translocators.entries)
        self.assertEqual(f.b.body_script.component.entity_var(342).value(), 0)

    def test_natural_pause_restores_the_existing_profile_multiplier(self):
        for world_only in (False, True):
            with self.subTest(world_only=world_only):
                f = self.make(natural=True)
                script = f.a.body_script
                start = f.run.frame
                script.component.set(12918, 2)
                script.ult_seconds = 0
                if world_only:
                    f.run.match.update((start + 101) * combat.TICK, start + 101)
                else:
                    f.press(0, 100)
                self.assertEqual(self.charge(f), 0)
                script.ult_seconds = 60  # reenable the authored clock
                if world_only:
                    f.run.match.update((start + 171) * combat.TICK, start + 171)
                else:
                    f.press(0, 70)
                self.assertEqual(self.charge(f), 10)
                self.assertEqual(script.component.entity_var(12918).base, 2)

    def test_damage_fills_original_admission_and_actual_emp_spends_it(self):
        f = self.make()
        f.a.body_script.set_ult_charge(1392)
        f.press(32)
        self.assertEqual(self.charge(f), 1392)
        f.press(0)
        f.press(FIRE)
        self.assertEqual(self.charge(f), 1400)
        f.press(0, 3)
        f.press(32)
        self.assertEqual(self.charge(f), 0)
        f.press(0, 30)
        self.assertIn(f.b.body, f.run.match.combat.sombra_controls.locks)

    def test_same_hero_respawn_retains_charge_and_explicit_switch_resets(self):
        f = self.make()
        f.press(FIRE)
        self.assertEqual(self.charge(f), 8)
        f.press(0, 3)
        f.press(FIRE, 100, shooter=f.b, target=f.a)
        self.assertTrue(f.run.match.combat.is_dead(f.a))
        old_body = f.a.body
        f.press(0, math.ceil(combat.respawn_seconds(f.run.match) / combat.TICK) + 5)
        self.assertNotEqual(f.a.body, old_body)
        self.assertGreaterEqual(self.charge(f), 8)
        self.assertFalse(f.run.match.combat.sombra_charge.retained)
        self.assertTrue(f.run.match.switch_hero(f.a, f.a.hero))
        self.assertEqual(self.charge(f), 0)

    def test_lethal_rifle_before_natural_deadline_preserves_only_earned_charge(self):
        f = self.make(natural=True)
        match = f.run.match
        script = f.a.body_script
        root = next(i for i in script.component.instances.values() if i.graph.index == 0x0B8B)
        wait = root.node_state(root.graph.node(3))
        first = (wait.start + wait.ms) // script.frame_ms
        second = (wait.start + wait.ms * 2) // script.frame_ms
        match.update((first + 1) * combat.TICK, first + 1)
        self.assertEqual(self.charge(f), 5)
        self.assertEqual(f.health(f.a), 200)
        match.combat.vitals[f.a.body].now[HEALTH_POOL] = 1
        match.update(second * combat.TICK, second)
        yaw, pitch = aim(match.combat._eye(f.b), (0, 1, 0))
        f.b.take_commands([Command(second, yaw=yaw, pitch=pitch, buttons=FIRE)])
        self.assertTrue(match.combat.is_dead(f.a))

        at = second + math.ceil(combat.respawn_seconds(match) / combat.TICK) + 1
        match.update(at * combat.TICK, at)
        self.assertIsNot(f.a.body_script, script)
        self.assertEqual(script.component.entity_var(342).value(), 5)
        self.assertEqual(self.charge(f), 5)

    def test_full_snapshot_captures_context_pause_and_deferred_respawn_charge(self):
        case = stored.ServerSnapshotTests()
        session, _, players = case.scene()
        player = players[0]
        session.apply("switch_hero", {"account": player.account_lo, "hero": fixture.SOMBRA})
        manager = player.match.combat.sombra_charge
        self.assertTrue(manager.data)
        initial = stored.snapshot_digest(case.capture(session))
        manager.retained[player.entity] = (fixture.SOMBRA, 123.0)
        self.assertNotEqual(stored.snapshot_digest(case.capture(session)), initial)
        manager.retained.clear()
        self.assertEqual(stored.snapshot_digest(case.capture(session)), initial)
        manager.paused[player.body] = 2
        self.assertNotEqual(stored.snapshot_digest(case.capture(session)), initial)
        manager.paused.clear()
        self.assertEqual(stored.snapshot_digest(case.capture(session)), initial)
        with patch.object(runtime.Var, "value", side_effect=AssertionError("snapshot must not evaluate")):
            self.assertEqual(stored.snapshot_digest(case.capture(session)), initial)


class SombraChargeNpcTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        global actual_npcs
        import test_game_sombra_npc as actual_npcs
        if not os.environ.get("ORPHEUS_ULTIMATE_DATA"):
            raise unittest.SkipTest("set ORPHEUS_ULTIMATE_DATA to the direct-body tag context")
        actual_npcs.SombraActualNpcTests.setUpClass.__func__(cls)

    @classmethod
    def restore_graphs(cls, path):
        actual_npcs.SombraActualNpcTests.restore_graphs(path)

    def replace_target(self, f, bot, definition):
        match = f.run.match
        npc.retire_body(match, bot)
        fresh = npc.spawn(match, definition, (0, 0, 1.5), team=1)
        fresh.body_script.component.set(9948, 0)
        actual_npcs.SombraActualNpcTests.press(f, fresh, 0, 4)
        self.assertEqual(fresh.body, definition.body)
        self.assertEqual(match.combat.vitals[fresh.entity].total,
                         definition.health + definition.armour + definition.shields)
        return fresh

    def test_unseeded_combat_earns_spends_and_recovers_emp_on_every_actual_npc_class(self):
        press = actual_npcs.SombraActualNpcTests.press
        for key, definition in self.catalog.items():
            with self.subTest(npc=definition.name):
                f, bot = actual_npcs.SombraActualNpcTests.make(self, key)
                match = f.run.match
                fight = match.combat
                script = f.a.body_script
                script.ult_seconds = 0
                press(f, bot, 0)
                script.set_ult_charge(0)
                cost = script.component.entity_var(338).value()
                self.assertEqual(cost, 1400)
                self.assertEqual(SombraChargeTests.charge(f), 0)
                earned = 0
                tried_early = False
                defeated = set()

                for _ in range(100):
                    vitals = fight.vitals[bot.entity]
                    before = vitals.total
                    if f.weapon().vars[53].value() < 12:
                        press(f, bot, fixture.RELOAD)
                        press(f, bot, 0, 130)
                        self.assertEqual(f.weapon().vars[53].value(), 60)
                    press(f, bot, FIRE, 24)
                    press(f, bot, 0, 4)
                    lost = before - vitals.total
                    self.assertGreater(lost, 0)
                    earned += lost
                    self.assertAlmostEqual(SombraChargeTests.charge(f), min(cost, earned), places=4)
                    if vitals.dead:
                        self.assertEqual(vitals.killer, f.a.body)
                        self.assertNotIn(bot.entity, defeated)
                        defeated.add(bot.entity)
                        bot = self.replace_target(f, bot, definition)
                    if SombraChargeTests.charge(f) == cost:
                        break
                    if not tried_early and earned > 1000:
                        press(f, bot, 32)
                        press(f, bot, 0, 45)
                        self.assertNotIn(bot.entity, fight.sombra_controls.locks)
                        self.assertAlmostEqual(SombraChargeTests.charge(f), earned, places=4)
                        tried_early = True

                self.assertTrue(tried_early)
                self.assertEqual(SombraChargeTests.charge(f), cost)
                if fight.vitals[bot.entity].total < 40:
                    # Preserve the final victim through the lockout shot and the post-EMP shot.
                    bot = self.replace_target(f, bot, definition)
                press(f, bot, 32)
                self.assertEqual(SombraChargeTests.charge(f), 0)
                press(f, bot, 0, 50)
                application, = fight.sombra_controls.locks[bot.entity]
                self.assertEqual(application.application.source_entity(), f.a.body)
                locked = script.component.entity_var(842)
                self.assertTrue(locked.value())
                vitals = fight.vitals[bot.entity]
                before = vitals.total
                press(f, bot, FIRE)
                self.assertLess(vitals.total, before)
                self.assertEqual(SombraChargeTests.charge(f), 0)
                press(f, bot, 0, 400)
                self.assertFalse(locked.value())
                self.assertNotIn(bot.entity, fight.sombra_controls.locks)
                before = vitals.total
                press(f, bot, FIRE)
                self.assertGreater(before - vitals.total, 0)
                self.assertAlmostEqual(SombraChargeTests.charge(f), before - vitals.total, places=4)

    def test_actual_five_npc_pistol_and_melee_credit_resolved_damage(self):
        for key, definition in self.catalog.items():
            with self.subTest(npc=definition.name):
                f, bot = actual_npcs.SombraActualNpcTests.make(self, key)
                f.a.body_script.ult_seconds = 0
                f.press(0)
                f.a.body_script.set_ult_charge(0)
                actual_npcs.SombraActualNpcTests.press(f, bot, FIRE)
                actual_npcs.SombraActualNpcTests.press(f, bot, 0, 3)
                self.assertEqual(SombraChargeTests.charge(f), 8)
                actual_npcs.SombraActualNpcTests.press(f, bot, fixture.MELEE)
                actual_npcs.SombraActualNpcTests.press(f, bot, 0, 45)
                self.assertEqual(SombraChargeTests.charge(f), 38)

    def test_actual_pistol_hit_unlocks_emp_against_each_npc(self):
        press = actual_npcs.SombraActualNpcTests.press
        for key, definition in self.catalog.items():
            with self.subTest(npc=definition.name):
                f, bot = actual_npcs.SombraActualNpcTests.make(self, key)
                fight = f.run.match.combat
                script = f.a.body_script
                script.ult_seconds = 0
                f.press(0)
                script.set_ult_charge(1392)
                before = fight.vitals[bot.entity].total

                press(f, bot, 32)
                self.assertEqual(SombraChargeTests.charge(f), 1392)
                self.assertNotIn(bot.entity, fight.sombra_controls.locks)
                press(f, bot, 0)
                press(f, bot, FIRE)
                self.assertEqual(fight.vitals[bot.entity].total, before - 8)
                self.assertEqual(SombraChargeTests.charge(f), 1400)

                press(f, bot, 0, 3)
                press(f, bot, 32)
                self.assertEqual(SombraChargeTests.charge(f), 0)
                self.assertNotIn(bot.entity, fight.sombra_controls.locks)
                press(f, bot, 0, 35)
                effect, = fight.sombra_controls.locks[bot.entity]
                self.assertEqual(effect.application.source_entity(), f.a.body)

    def test_actual_npc_pistol_damage_resumes_charging_after_emp_lockout(self):
        press = actual_npcs.SombraActualNpcTests.press
        for key, definition in self.catalog.items():
            with self.subTest(npc=definition.name):
                f, bot = actual_npcs.SombraActualNpcTests.make(self, key)
                fight = f.run.match.combat
                vitals = fight.vitals[bot.entity]
                script = f.a.body_script
                script.ult_seconds = 0
                script.set_ult_charge(1400)
                press(f, bot, 32)
                press(f, bot, 0, 50)
                self.assertIn(bot.entity, fight.sombra_controls.locks)
                locked = script.component.entity_var(842)
                self.assertTrue(locked.value())
                before = vitals.total
                press(f, bot, FIRE, 10)
                self.assertLess(vitals.total, before)
                self.assertEqual(SombraChargeTests.charge(f), 0)

                for _ in range(400):
                    if not locked.value():
                        break
                    press(f, bot, 0)
                self.assertFalse(locked.value())
                self.assertEqual(SombraChargeTests.charge(f), 0)
                before = vitals.total
                press(f, bot, FIRE)
                self.assertEqual(vitals.total, before - 8)
                self.assertEqual(SombraChargeTests.charge(f), 8)
