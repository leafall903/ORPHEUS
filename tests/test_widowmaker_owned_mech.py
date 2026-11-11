"""Widowmaker combat through normal D.Va selection and an owned 0407 body."""

import unittest
from unittest.mock import Mock

from test_game_combat import aim
from test_widowmaker_combat import FIRE, MELEE, SCOPE, _WidowmakerMatch

from ow174.game import dva_body
from ow174.game.content import heroes
from ow174.game.script import expr


class WidowmakerOwnedMechTests(_WidowmakerMatch, unittest.TestCase):
    def test_normal_pick_creates_authored_mech_and_widow_hits_its_pools(self):
        self.assertTrue(self.run.match.switch_hero(self.b, heroes()[dva_body.DVA]))
        mech, = (bot for bot in self.run.match.bots if bot.owner == self.b.entity)
        self.assertEqual((mech.body, mech.source_body, mech.team), (dva_body.MECH, self.b.body, self.b.team))
        instance = next(i for i in self.b.body_script.component.instances.values() if i.graph.index == 0x026A)
        self.assertEqual(instance.vars[38].value(), expr.Entity(mech.entity))
        self.assertEqual(self.health(mech), 600)
        self.assertIsNotNone(mech.body_script)
        self.assertIs(self.b.controlled_mech, mech)

        mech.position = (0, 0, 5)
        mech.control_mover.position[:] = mech.position
        mech.control_mover.body = None
        self.b.mover.position[:] = (10, 0, 5)
        self.b.mover.body = None
        fight = self.run.match.combat
        yaw, pitch = aim(fight._eye(self.a), (0, 1, 5))
        for _ in range(100):
            self.run.step({self.a: (yaw, pitch, SCOPE)})
        self.a.body_script.ult_seconds = 0
        self.a.body_script.set_ult_charge(0)
        self.run.step({self.a: (yaw, pitch, SCOPE | FIRE)})
        dealt = 600 - self.health(mech)
        self.assertGreater(dealt, 0)
        self.assertAlmostEqual(self.a.body_script.component.entity_var(342).value(), dealt, places=4)
        self.assertEqual(self.b.body_script.component.entity_var(253).value(), 150)
        yaw, pitch = aim((0, 1.5, 5), (0, 1, 0))
        before = self.health(self.a)
        for _ in range(24):
            self.run.step({self.b: (yaw, pitch, FIRE)})
        self.assertLess(self.health(self.a), before)
        self.assertTrue(self.run.match.switch_hero(self.b, self.a.hero))
        self.assertNotIn(mech, self.run.match.bots)


    def test_widow_headshots_demech_and_returns_control_to_live_pilot(self):
        match = self.run.match
        self.assertTrue(match.switch_hero(self.b, heroes()[dva_body.DVA]))
        mech = self.b.controlled_mech
        mech.position = (0, 0, 5)
        mech.control_mover.position[:] = mech.position
        mech.control_mover.body = None
        self.b.mover.position[:] = (10, 0, 5)
        self.b.mover.body = None
        self.assertFalse(match.queue_body_message(self.b.body, dva_body.RETURN_TO_PILOT, {}, self.a.body))
        self.assertFalse(match.queue_body_message(self.b.body, dva_body.RETURN_TO_PILOT, {}, mech.entity))

        for pilot in (self.a, self.b):
            pilot.client.queue_bot_death = Mock()
            pilot.client.queue_bot_death_update = Mock()
        target = self.target(mech)
        head = (target.feet[0], target.feet[1] + target.eye, target.feet[2])
        yaw, pitch = aim(match.combat._eye(self.a), head)
        for _ in range(8):
            for _ in range(110):
                self.run.step({self.a: (yaw, pitch, SCOPE)})
            self.run.step({self.a: (yaw, pitch, SCOPE | FIRE)})
            if mech.dead:
                break
        self.assertTrue(mech.dead)
        self.a.client.queue_bot_death.assert_called_once()
        self.assertEqual(self.a.client.queue_bot_death.call_args.args[0], mech.entity)
        self.assertEqual(match.combat.vitals[mech.entity].killer, self.a.body)
        self.assertTrue(any(source is mech.body_script and recipient is self.b
                            and message == dva_body.RETURN_TO_PILOT
                            for source, recipient, _, message, _ in match._body_messages))
        self.run.step({})
        self.assertIsNone(self.b.controlled_mech)
        self.assertIsNone(mech.control_mover)
        self.assertEqual(self.health(self.b), 150)
        self.assertAlmostEqual(self.b.mover.position[2], mech.position[2], places=2)
        before = self.health(self.a)
        self.press(FIRE, attacker=self.b, defender=self.a)
        self.assertFalse(match.combat.rockets)
        for _ in range(100):
            self.run.step({})
        self.assertEqual(self.health(self.a), before)
        self.press(FIRE, attacker=self.b, defender=self.a)
        self.assertEqual(len(match.combat.rockets), 1)
        for _ in range(25):
            self.run.step({})
        self.assertEqual(self.health(self.a), before - 14)
        self.assertFalse(match.combat.rockets)
        self.press(SCOPE, 110)
        self.press(SCOPE | FIRE)
        self.assertLess(self.health(self.b), 150)
        self.assertIn(mech, match.bots)
        self.assertIsNotNone(mech.body_script)
        self.b.body_script.set_ult_charge(self.b.body_script.component.entity_var(338).value())
        self.run.step({self.b: (0, 0, 32)})
        for _ in range(120):
            self.run.step({})
        replacement, = match.bots
        self.assertIsNot(replacement, mech)
        self.assertIs(self.b.controlled_mech, replacement)
        self.assertIsNone(mech.body_script)
        self.assertNotIn(mech.entity, match.combat.vitals)

    def test_widow_kills_ejected_pilot_before_detached_blast_and_cleanup(self):
        from ow174.game.script.driver import ULT_CHARGE, ULT_COST
        from ow174.game.world import HEALTH_POOL

        match = self.run.match
        self.assertTrue(match.switch_hero(self.b, heroes()[dva_body.DVA]))
        mech = self.b.controlled_mech
        mech.position = (0, 0, 5)
        mech.control_mover.position[:] = mech.position
        mech.control_mover.body = None
        vitals = match.combat.vitals_of(self.target(self.a))
        vitals.now[HEALTH_POOL] = vitals.most[HEALTH_POOL] = 1500
        match.combat._sync_body_health(self.a, vitals)
        yaw, pitch = aim(match.combat._eye(self.a), (0, 1.2, 5))
        for _ in range(110):
            self.run.step({self.a: (yaw, pitch, SCOPE)})
        component = mech.body_script.component
        component.set(ULT_CHARGE, component.entity_var(ULT_COST).value())
        self.run.step({self.b: (0, 0, 32), self.a: (yaw, pitch, SCOPE)})
        self.assertTrue(any(source is mech.body_script and recipient is self.b
                            and message == dva_body.RETURN_TO_PILOT
                            for source, recipient, _, message, _ in match._body_messages))
        self.run.step({self.a: (yaw, pitch, SCOPE)})
        self.assertTrue(mech.detached_bomb)
        self.assertIsNone(self.b.controlled_mech)
        self.assertEqual(component.entity_var(ULT_CHARGE).value(), 0)
        self.press(SCOPE | FIRE, height=1.5)
        self.assertTrue(match.combat.is_dead(self.b))
        self.assertIn(mech, match.bots)
        self.assertEqual(vitals.total, 1500)
        for _ in range(184):
            self.run.step({})
        self.assertEqual(vitals.total, 1500)
        self.run.step({})
        expected_damage = 50 + 850 * (20 - 4.5) / 16
        self.assertAlmostEqual(vitals.total, 1500 - expected_damage, places=3)
        for _ in range(33):
            self.run.step({})
        self.assertNotIn(mech, match.bots)
        self.assertIsNone(mech.body_script)
        self.assertNotIn(mech.entity, match.combat.vitals)

    def test_detached_fuse_funds_blocked_then_successful_call_mech(self):
        from ow174.game.script.driver import ULT_CHARGE, ULT_COST
        from ow174.game.world import HEALTH_POOL

        match = self.run.match
        self.assertTrue(match.switch_hero(self.b, heroes()[dva_body.DVA]))
        mech = self.b.controlled_mech
        mech.position = (0, 0, 30)
        mech.control_mover.position[:] = mech.position
        mech.control_mover.body = None
        pilot = self.b.body_script.component
        for _ in range(100):
            self.run.step({})
        mech_vm = mech.body_script.component
        mech_vm.set(ULT_CHARGE, mech_vm.entity_var(ULT_COST).value())
        self.assertFalse(match.queue_body_message(self.b.body, dva_body.CALL_MECH_CHARGE, {}, self.a.body))
        self.run.step({self.b: (0, 0, 32)})
        self.run.step({})
        self.assertTrue(mech.detached_bomb)
        self.assertFalse(match.queue_body_message(self.b.body, dva_body.CALL_MECH_CHARGE, {}, mech.entity))
        for _ in range(185):
            self.run.step({})
        self.assertLess(pilot.entity_var(ULT_CHARGE).value(), pilot.entity_var(ULT_COST).value())
        self.run.step({})
        self.assertEqual(pilot.entity_var(ULT_CHARGE).value(), pilot.entity_var(ULT_COST).value())
        self.assertFalse(match.combat.is_dead(self.b))
        self.a.mover.position[:] = (0, 0, 31.5)
        self.a.mover.body = None
        self.run.step({self.b: (0, 0, 32)})
        for _ in range(80):
            self.run.step({})
        self.assertFalse(match.bots)
        self.assertIsNone(self.b.controlled_mech)
        self.assertEqual(pilot.entity_var(ULT_CHARGE).value(), pilot.entity_var(ULT_COST).value())
        self.a.mover.position[:] = (0, 0, 0)
        self.a.mover.body = None
        self.run.step({self.b: (0, 0, 32)})
        for _ in range(30):
            self.run.step({})
            if match.bots:
                break
        replacement, = match.bots
        x, y, z = replacement.position
        self.a.mover.position[:] = (x, y, z + 1)
        self.a.mover.body = None
        vitals = match.combat.vitals_of(self.target(self.a))
        vitals.now[HEALTH_POOL] = vitals.most[HEALTH_POOL] = 500
        match.combat._sync_body_health(self.a, vitals)
        for _ in range(65):
            self.run.step({})
            if vitals.total != 500:
                break
        self.assertEqual(vitals.total, 250)
        self.assertGreater(self.a.mover.velocity[2], 0)
        self.assertGreater(self.a.mover.velocity[1], 0)
        for _ in range(65):
            self.run.step({})
        self.assertEqual(vitals.total, 250)
        self.assertIsNot(replacement, mech)
        self.assertIs(self.b.controlled_mech, replacement)
        self.assertEqual(self.health(replacement), 600)

    def test_driven_mech_primary_uses_click_aim_and_rewinds_widow(self):
        from ow174.game import combat
        from ow174.game.commands import Command

        for delay_ms, use_click, offset, hits in (
            (0, True, 0, False), (96, False, 0, False), (96, True, 0, True), (96, True, 12, True),
        ):
            with self.subTest(delay_ms=delay_ms, use_click=use_click, offset=offset):
                self.setUp()
                match = self.run.match
                self.assertTrue(match.switch_hero(self.b, heroes()[dva_body.DVA]))
                mech = self.b.controlled_mech
                mech.position = (0, 0, 5)
                mech.control_mover.position[:] = mech.position
                mech.control_mover.body = None
                for _ in range(100):
                    self.run.step({})
                self.a.mover.position[:] = (7, 0, 0)
                self.a.mover.body = None
                for _ in range(3):
                    self.run.step({})
                tick = self.run.frame + 1
                match.update(tick * combat.TICK, tick)
                frame = tick + offset
                eye = combat.eye_height(mech.body, mech.control_mover.crouched)
                yaw, pitch = aim((0, eye, 5), (0, 1, 0))
                before = self.health(self.a)
                self.b.take_commands([Command(frame, yaw=0, pitch=0, buttons=FIRE, extra=delay_ms,
                                              click_yaw=yaw if use_click else None,
                                              click_pitch=pitch if use_click else None)])
                self.assertEqual(self.health(self.a) < before, hits)

    def test_boosted_self_destruct_keeps_moving_and_respects_wall_cover(self):
        from test_game_mover import box, wall

        from ow174.game.collision import World
        from ow174.game.script.driver import ULT_CHARGE, ULT_COST
        from ow174.game.world import HEALTH_POOL

        wall_z = 2.5
        open_end = None
        for cover in (False, True):
            with self.subTest(cover=cover):
                self.setUp()
                match = self.run.match
                self.assertTrue(match.switch_hero(self.b, heroes()[dva_body.DVA]))
                mech = self.b.controlled_mech
                mech.position = (0, 0, 0)
                mech.control_mover.position[:] = mech.position
                mech.control_mover.body = None
                self.a.mover.position[:] = (0, 0, 8)
                self.a.mover.body = None
                if cover:
                    floor = box(-40, 40, -1, 0, -40, 40)
                    match.collision_world = World(floor + wall(-10, wall_z, 10, wall_z))
                for _ in range(100):
                    self.run.step({})
                vitals = match.combat.vitals_of(self.target(self.a))
                vitals.now[HEALTH_POOL] = vitals.most[HEALTH_POOL] = 1500
                match.combat._sync_body_health(self.a, vitals)
                component = mech.body_script.component
                component.set(ULT_CHARGE, component.entity_var(ULT_COST).value())
                for _ in range(8):
                    self.run.step({self.b: (0, 0, 8)})
                self.assertGreater(mech.velocity[2], 10)
                self.run.step({self.b: (0, 0, 32)})
                launch_z = mech.position[2]
                self.run.step({})
                self.assertTrue(mech.detached_bomb)
                self.assertIsNone(self.b.controlled_mech)
                self.run.step({})
                self.assertGreater(mech.position[2], launch_z)
                for _ in range(184):
                    self.run.step({})
                self.assertEqual(vitals.total, 1500)
                self.run.step({})
                if cover:
                    self.assertLess(mech.position[2], open_end - 0.05)
                    self.assertLess(mech.position[2], wall_z)
                    self.assertEqual(vitals.total, 1500)
                    self.assertEqual(self.a.mover.velocity, (0.0, 0.0, 0.0))
                else:
                    open_end = mech.position[2]
                    self.assertLess(vitals.total, 1500)
                for _ in range(25):
                    self.run.step({})
                self.assertNotIn(mech, match.bots)

    def test_call_mech_landing_rejects_widow_out_of_range_behind_cover_or_on_same_team(self):
        from test_game_mover import box, wall

        from ow174.game.collision import World
        from ow174.game.script.driver import ULT_CHARGE, ULT_COST

        for boundary in ("range", "cover", "team"):
            with self.subTest(boundary=boundary):
                self.setUp()
                match = self.run.match
                self.assertTrue(match.switch_hero(self.b, heroes()[dva_body.DVA]))
                mech = self.b.controlled_mech
                mech.position = (0, 0, 30)
                mech.control_mover.position[:] = mech.position
                mech.control_mover.body = None
                for _ in range(100):
                    self.run.step({})
                component = mech.body_script.component
                component.set(ULT_CHARGE, component.entity_var(ULT_COST).value())
                self.run.step({self.b: (0, 0, 32)})
                for _ in range(220):
                    self.run.step({})
                self.assertNotIn(mech, match.bots)
                self.run.step({self.b: (0, 0, 32)})
                for _ in range(30):
                    self.run.step({})
                    if match.bots:
                        break
                replacement, = match.bots
                x, y, z = replacement.position
                self.a.mover.position[:] = (x, y, z + (3.5 if boundary == "range" else 2.0))
                self.a.mover.body = None
                if boundary == "cover":
                    match.collision_world = World(box(-50, 50, -1, 0, -50, 50) + wall(-6, z + 1, 6, z + 1))
                elif boundary == "team":
                    self.a.team = self.b.team
                for _ in range(100):
                    self.run.step({})
                self.assertIs(self.b.controlled_mech, replacement)
                recall = next(i for i in self.b.body_script.component.instances.values()
                              if i.graph.index == 0x026A)
                self.assertGreater(recall.states[19].start, 0)
                self.assertEqual(self.health(self.a), 175)
                self.assertEqual(self.a.mover.velocity, (0.0, 0.0, 0.0))

    def test_owned_matrix_intercepts_widow_fire_and_mine_until_release(self):
        from unittest.mock import patch

        match = self.run.match
        self.assertTrue(match.switch_hero(self.b, heroes()[dva_body.DVA]))
        mech = self.b.controlled_mech
        mech.position = (0, 0, 7)
        mech.control_mover.position[:] = mech.position
        mech.control_mover.body = None
        for _ in range(100):
            self.run.step({})
        yaw, pitch = aim(match.combat._eye(self.a), (0, 1, 7))
        back, _ = aim((0, 1, 7), (0, 1, 0))
        for _ in range(2):
            self.run.step({self.b: (back, 0, SCOPE)})
        matrix = next(i for i in mech.body_script.component.instances.values() if i.graph.index == 0x02CC)
        self.assertTrue(matrix.state(3).active)
        for _ in range(12):
            self.run.step({self.a: (yaw, pitch, FIRE), self.b: (back, 0, SCOPE)})
        self.assertEqual(self.health(mech), 600)
        mines = match.combat.venom
        with patch.object(mines, "throw", wraps=mines.throw) as thrown:
            self.run.step({self.a: (yaw, pitch, 16), self.b: (back, 0, SCOPE)})
            for _ in range(65):
                self.run.step({self.b: (back, 0, SCOPE)})
            thrown.assert_called_once()
        self.assertEqual((mines.flights, mines.mines, mines.poison), ([], [], []))
        self.assertEqual(self.health(mech), 600)
        self.run.step({})
        self.assertFalse(matrix.state(3).active)
        self.run.step({self.a: (yaw, pitch, FIRE)})
        self.assertLess(self.health(mech), 600)

        with patch.object(mines, "throw", wraps=mines.throw) as thrown:
            self.run.step({self.a: (yaw, pitch, 16)})
            for _ in range(65):
                self.run.step({})
            thrown.assert_not_called()
            self.assertEqual((mines.flights, mines.mines, mines.poison), ([], [], []))

            for _ in range(1000):
                self.run.step({})
            self.run.step({self.a: (yaw, pitch, 16)})
            for _ in range(65):
                self.run.step({})
            thrown.assert_called_once()
            self.assertEqual(len(mines.mines), 1)
            self.assertFalse(matrix.state(3).active)

    def test_owned_matrix_exhaustion_ends_interception_while_input_is_held(self):
        match = self.run.match
        self.assertTrue(match.switch_hero(self.b, heroes()[dva_body.DVA]))
        mech = self.b.controlled_mech
        mech.position = (0, 0, 7)
        mech.control_mover.position[:] = mech.position
        mech.control_mover.body = None
        for _ in range(100):
            self.run.step({})
        yaw, pitch = aim(match.combat._eye(self.a), (0, 1, 7))
        back, _ = aim((0, 1, 7), (0, 1, 0))
        command = {self.b: (back, 0, SCOPE)}
        self.run.step(command)
        matrix = next(i for i in mech.body_script.component.instances.values() if i.graph.index == 0x02CC)
        state = matrix.state(3)
        self.assertTrue(state.active)
        self.assertEqual(matrix.vars[971].value(), 100)
        for _ in range(60):
            self.run.step(command)
        self.assertTrue(state.active)
        self.assertGreater(matrix.vars[87].value(), 0)
        self.assertLess(matrix.vars[87].value(), 100)
        for _ in range(160):
            self.run.step(command)
            if not state.active:
                break
        self.assertFalse(state.active)
        self.assertEqual(matrix.vars[87].value(), 0)
        self.assertTrue(mech.control_buttons & SCOPE)
        self.run.step({**command, self.a: (yaw, pitch, FIRE)})
        self.assertLess(self.health(mech), 600)
        self.run.step({})
        self.run.step(command)
        self.assertFalse(state.active)
        for _ in range(175):
            self.run.step({})
        self.assertGreater(matrix.vars[87].value(), 0)
        self.run.step(command)
        self.assertTrue(state.active)

    def test_owned_micro_missiles_deliver_all_eighteen_hits_to_widow(self):
        from unittest.mock import patch

        from test_game_mover import box, wall

        from ow174.game import combat
        from ow174.game.collision import World
        from ow174.game.world import HEALTH_POOL

        for boundary in ("hit", "cover", "team"):
            with self.subTest(boundary=boundary):
                self.setUp()
                match = self.run.match
                self.assertTrue(match.switch_hero(self.b, heroes()[dva_body.DVA]))
                mech = self.b.controlled_mech
                mech.position = (0, 0, -2)
                mech.control_mover.position[:] = mech.position
                mech.control_mover.body = None
                if boundary == "cover":
                    match.collision_world = World(box(-20, 20, -1, 0, -20, 20) + wall(-5, -1, 5, -1))
                elif boundary == "team":
                    self.a.team = self.b.team
                for _ in range(100):
                    self.run.step({})
                vitals = match.combat.vitals_of(self.target(self.a))
                vitals.now[HEALTH_POOL] = vitals.most[HEALTH_POOL] = 1000
                match.combat._sync_body_health(self.a, vitals)
                eye = combat.eye_height(mech.body, mech.control_mover.crouched)
                yaw, pitch = aim((0, eye, -2), (0, 1.1, 0))
                fire = {self.b: (yaw, pitch, 16)}
                with patch.object(match.combat, "_shoot", wraps=match.combat._shoot) as shoot:
                    for _ in range(145):
                        self.run.step(fire)
                    self.assertEqual(sum(call.args[0] is mech for call in shoot.call_args_list), 18)
                    self.assertEqual(vitals.total, 874 if boundary == 'hit' else 1000)
                    self.run.step({})
                    for _ in range(35):
                        self.run.step(fire)
                    self.assertEqual(sum(call.args[0] is mech for call in shoot.call_args_list), 18)
                    self.assertEqual(vitals.total, 874 if boundary == 'hit' else 1000)
                self.assertFalse(match.combat.rockets)

    def test_owned_quick_melee_uses_mech_pose_range_cover_and_team(self):
        from test_game_mover import box, wall

        from ow174.game.collision import World

        for boundary in ("hit", "range", "cover", "team"):
            with self.subTest(boundary=boundary):
                self.setUp()
                match = self.run.match
                self.assertTrue(match.switch_hero(self.b, heroes()[dva_body.DVA]))
                mech = self.b.controlled_mech
                mech.position = (0, 0, -4 if boundary == "range" else -1.5)
                mech.control_mover.position[:] = mech.position
                mech.control_mover.body = None
                if boundary == "cover":
                    match.collision_world = World(box(-20, 20, -1, 0, -20, 20) + wall(-5, -0.7, 5, -0.7))
                elif boundary == "team":
                    self.a.team = self.b.team
                for _ in range(100):
                    self.run.step({})
                yaw, pitch = aim(match.combat._eye(mech), (0, 1, 0))
                self.run.step({self.b: (yaw, pitch, MELEE)})
                for _ in range(35):
                    self.run.step({self.b: (yaw, pitch, 0)})
                self.assertTrue(any(match.combat.melee_seen[mech.entity].values()))
                self.assertEqual(self.health(self.a), 145 if boundary == "hit" else 175)

    def test_owned_booster_contact_damages_and_pushes_widow_once(self):
        from test_game_mover import box, wall

        from ow174.game.collision import World

        for boundary in ("hit", "cover", "team", "zero_impulse"):
            with self.subTest(boundary=boundary):
                self.setUp()
                match = self.run.match
                self.assertTrue(match.switch_hero(self.b, heroes()[dva_body.DVA]))
                mech = self.b.controlled_mech
                mech.position = (0, 0, -4.5)
                mech.control_mover.position[:] = mech.position
                mech.control_mover.body = None
                if boundary == "cover":
                    match.collision_world = World(box(-20, 20, -1, 0, -20, 20) + wall(-5, -1, 5, -1))
                elif boundary == "team":
                    self.a.team = self.b.team
                elif boundary == "zero_impulse":
                    self.b.body_script.component.set(10611, 0)
                for _ in range(100):
                    self.run.step({})
                self.run.step({self.b: (0, 0, 8)})
                for _ in range(35):
                    self.run.step({})
                if boundary == "hit":
                    self.assertEqual(self.health(self.a), 165)
                    self.assertGreater(self.a.mover.position[2], 1)
                else:
                    self.assertEqual(self.health(self.a), 165 if boundary == "zero_impulse" else 175)
                    self.assertEqual(tuple(self.a.mover.position), (0, 0, 0))

    def test_owned_mech_active_matrix_and_booster_contact_are_stored(self):
        from test_server_snapshot import ServerSnapshotTests

        from ow174.game.server_snapshot import snapshot_digest

        fixture = ServerSnapshotTests()
        session, (_, peer), (widow, pilot) = fixture.scene(two=True)
        match = pilot.match
        session.apply('switch_hero', {'account': widow.account_lo, 'hero': 0x02E000000000000A})
        session.apply('switch_hero', {'account': pilot.account_lo, 'hero': dva_body.DVA})
        mech = pilot.controlled_mech
        self.assertIsNotNone(mech)
        match.bots[:] = [mech]
        mech.position = (0, 0, -4.5)
        mech.control_mover.position[:] = mech.position
        mech.control_mover.body = None
        widow.mover.position[:] = (0, 0, 0)
        widow.mover.body = None

        now = 100.928

        def step(buttons=0):
            nonlocal now
            now += 0.016
            session.tick(now)
            peer.commands(mech.body_script.frame + 1, 1, buttons, now=now)

        for _ in range(100):
            step()
        for _ in range(20):
            step(SCOPE)
        matrix = next(i for i in mech.body_script.component.instances.values() if i.graph.index == 0x02CC)
        self.assertTrue(matrix.state(3).active)
        active = snapshot_digest(fixture.capture(session))
        self.assertEqual(snapshot_digest(fixture.capture(session)), active)
        for _ in range(20):
            step()
        self.assertFalse(matrix.state(3).active)
        self.assertNotEqual(snapshot_digest(fixture.capture(session)), active)

        step(8)
        for _ in range(35):
            step()
            if mech.booster_contact is not None and widow.body in mech.booster_contact[2]:
                break
        self.assertIn(widow.body, mech.booster_contact[2])
        contact = snapshot_digest(fixture.capture(session))
        mech.booster_contact[2].remove(widow.body)
        self.assertNotEqual(snapshot_digest(fixture.capture(session)), contact)
        mech.booster_contact[2].add(widow.body)
        self.assertEqual(snapshot_digest(fixture.capture(session)), contact)

    def test_venom_mine_poisons_and_reveals_owned_mech_until_expiry(self):
        match = self.run.match
        self.assertTrue(match.switch_hero(self.b, heroes()[dva_body.DVA]))
        mech = self.b.controlled_mech
        mech.position = (0, 0, 6.5)
        mech.control_mover.position[:] = mech.position
        mech.control_mover.body = None
        for _ in range(100):
            self.run.step({})
        self.a.body_script.ult_seconds = 0
        self.a.body_script.set_ult_charge(0)
        self.run.step({self.a: (0, 0, 16)})
        for _ in range(40):
            self.run.step({})
        fight = match.combat
        self.assertEqual(len(fight.venom.mines), 1)
        self.assertEqual(self.health(mech), 600)
        self.assertFalse(fight.venom.poison)
        for _ in range(100):
            self.run.step({})
        self.assertTrue(fight.venom.poisoned_by(self.a, mech.entity))
        self.assertTrue(fight.revealed_to(self.a, mech.entity))
        self.assertFalse(fight.revealed_to(self.b, mech.entity))
        self.assertEqual(self.b.body_script.component.entity_var(253).value(), 150)
        poisoned_health = self.health(mech)
        for _ in range(20):
            self.run.step({self.b: (-32768, 0, SCOPE)})
        matrix = next(i for i in mech.body_script.component.instances.values() if i.graph.index == 0x02CC)
        self.assertTrue(matrix.state(3).active)
        self.assertLess(self.health(mech), poisoned_health)
        for _ in range(380):
            self.run.step({})
        self.assertAlmostEqual(self.health(mech), 562.5, places=4)
        self.assertAlmostEqual(self.a.body_script.component.entity_var(342).value(), 37.5, places=4)
        self.assertFalse(fight.venom.poison)
        self.assertFalse(fight.revealed_to(self.a, mech.entity))

    def test_infra_sight_reveals_owned_mech_through_cover_until_world_expiry(self):
        from test_game_mover import box, wall

        from ow174.game import combat
        from ow174.game.collision import World

        match = self.run.match
        self.assertTrue(match.switch_hero(self.b, heroes()[dva_body.DVA]))
        mech = self.b.controlled_mech
        mech.position = (0, 0, 7)
        mech.control_mover.position[:] = mech.position
        mech.control_mover.body = None
        match.collision_world = World(box(-20, 20, -1, 0, -20, 20) + wall(-5, 3, 5, 3))
        for _ in range(100):
            self.run.step({})
        script = self.a.body_script
        script.ult_seconds = 0
        script.set_ult_charge(script.component.entity_var(338).value())
        self.run.step({self.a: (0, 0, 32)})
        for _ in range(45):
            self.run.step({})
        self.assertEqual(script.component.entity_var(342).value(), 0)
        self.assertTrue(match.combat.revealed_to(self.a, mech.entity))
        self.assertFalse(match.combat.revealed_to(self.a, self.b.body))
        self.assertEqual(self.health(mech), 600)
        _, _, end_ms = script.infra_window()
        end_tick = (end_ms + script.frame_ms - 1) // script.frame_ms
        match.update(end_tick * combat.TICK, end_tick)
        self.assertFalse(match.combat.revealed_to(self.a, mech.entity))

    def test_replacing_poisoned_owned_mech_does_not_transfer_poison_or_reveal(self):
        match = self.run.match
        dva = heroes()[dva_body.DVA]
        self.assertTrue(match.switch_hero(self.b, dva))
        old = self.b.controlled_mech
        old.position = (0, 0, 6.5)
        old.control_mover.position[:] = old.position
        old.control_mover.body = None
        for _ in range(100):
            self.run.step({})
        self.run.step({self.a: (0, 0, 16)})
        for _ in range(140):
            self.run.step({})
        fight = match.combat
        self.assertLess(self.health(old), 600)
        self.assertTrue(fight.venom.poisoned_by(self.a, old.entity))
        self.assertTrue(fight.revealed_to(self.a, old.entity))

        self.assertTrue(match.switch_hero(self.b, self.a.hero))
        self.assertTrue(match.switch_hero(self.b, dva))
        replacement = self.b.controlled_mech
        self.assertNotEqual(replacement.entity, old.entity)
        replacement.position = old.position
        replacement.control_mover.position[:] = old.position
        replacement.control_mover.body = None
        for _ in range(60):
            self.run.step({})
        self.assertNotIn(old, match.bots)
        self.assertFalse(fight.venom.poison)
        self.assertFalse(fight.revealed_to(self.a, old.entity))
        self.assertFalse(fight.revealed_to(self.a, replacement.entity))
        self.assertEqual(self.health(replacement), 600)
