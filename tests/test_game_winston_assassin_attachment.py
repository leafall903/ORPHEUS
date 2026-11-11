"""Graph-owned Assassin attachment with declared possessed-body transform routing."""

import unittest

import test_game_winston_assassin_movement as fixtures
import test_game_winston_npc as npc_fixtures
import test_server_snapshot as snapshot_fixtures
from test_game_combat import aim

from ow174.game import bots, combat, hacks, npc, protections, winston
from ow174.game.commands import Command
from ow174.game.content import heroes
from ow174.game.server_snapshot import snapshot_digest


class AssassinAttachmentTests(unittest.TestCase):
    setUpClass = classmethod(fixtures.AssassinMovementTests.setUpClass.__func__)
    clear_graphs = classmethod(fixtures.AssassinMovementTests.clear_graphs.__func__)
    restore_graphs = classmethod(fixtures.AssassinMovementTests.restore_graphs.__func__)
    scene = fixtures.AssassinMovementTests.scene
    step = fixtures.AssassinMovementTests.step

    def acquire(self, *, jump=False, yaw=0, pitch=0):
        run, match, player, bot, script, _root, _behavior = self.scene()
        player.mover.yaw, player.mover.pitch = yaw, pitch
        if jump:
            bot.position = (20, 1, 20)  # no launch hit before the source approaches
            run.step({player: (yaw, pitch, 8)})
            distance = player.mover.data.stand_radius + bot.movement_data.stand_radius - .05
            bot.position = (player.mover.position[0], player.mover.position[1],
                            player.mover.position[2] + distance)
        script.set_manual_target(player.entity)
        self.step(run, bot, script)
        self.assertTrue(match.combat.assassins.controlled(player))
        instance = next(i for i in script.component.instances.values() if i.graph.index == 0x1157)
        self.assertTrue(instance.node_state(instance.graph.node(28)).active)
        self.assertTrue(instance.node_state(instance.graph.node(30)).active)
        return run, match, player, bot, script, instance

    def pose(self, run, player, *, yaw=0, pitch=0, buttons=0):
        run.frame += 1
        run.match.update(run.frame * .016, run.frame)
        player.take_commands([Command(run.frame, yaw=yaw, pitch=pitch, buttons=buttons)])

    def test_actual_constraint_snaps_zero_local_offset_and_replicates_opposite_facing(self):
        _run, _match, player, bot, _script, _instance = self.acquire(yaw=12000, pitch=8000)
        self.assertEqual(tuple(bot.position), tuple(player.mover.position))
        self.assertEqual(bot.velocity, (0, 0, 0))
        self.assertAlmostEqual(bot.yaw, bots.s16(player.mover.yaw + 32768), delta=1)
        self.assertAlmostEqual(bot.pitch, -player.mover.pitch, delta=1)
        movement = bot.movement(1002)
        self.assertEqual(movement.pitch, round(bot.pitch))
        self.assertEqual(movement.position, tuple(player.mover.position))

    def test_existing_jump_flight_carries_source_without_relaunch_or_losing_control(self):
        run, match, player, bot, _script, _instance = self.acquire(jump=True)
        jump = next(i for i in player.body_script.component.instances.values() if i.graph.index == 0x10A)
        marker = jump.state(19).last
        first_y = player.mover.position[1]
        for _ in range(20):
            self.pose(run, player, yaw=7000, pitch=3000, buttons=8 | 1 | 32)
            self.assertTrue(match.combat.assassins.controlled(player))
            self.assertEqual(tuple(bot.position), tuple(player.mover.position))
            self.assertEqual(jump.state(19).last, marker)
            self.assertAlmostEqual(bot.yaw, bots.s16(player.mover.yaw + 32768), delta=1)
            self.assertAlmostEqual(bot.pitch, -player.mover.pitch, delta=1)
        self.assertGreater(player.mover.position[1], first_y)
        self.assertEqual(match.combat.vitals[player.body].total, 480)

    def test_source_impulse_immediately_releases_scope_before_force_motion(self):
        run, match, player, bot, _script, instance = self.acquire()
        target = next(t for t in match.combat._targets() if t.owner is bot)
        health = match.combat.vitals[player.body].total
        match.combat.impulses.apply(target, (5, 7, 0))
        self.assertFalse(match.combat.assassins.controlled(player))
        self.assertFalse(match.combat.assassins.bindings)
        self.assertFalse(player.controller_component.instances)
        self.assertTrue(instance.node_state(instance.graph.node(31)).active)
        self.assertIn(bot.entity, match.combat.impulses.npcs)
        for _ in range(70):
            run.step({})
        self.assertEqual(match.combat.vitals[player.body].total, health)
        self.assertNotEqual(tuple(bot.position), tuple(player.mover.position))

    def test_authored_constraint_end_retires_binding_and_never_reattaches_on_pose(self):
        run, match, player, bot, script, instance = self.acquire()
        instance.node_state(instance.graph.node(28)).end(False)
        script.component.run_now()
        match.combat.assassins.update()
        self.assertFalse(match.combat.assassins.controlled(player))
        self.assertFalse(player.controller_component.instances)
        self.assertFalse(match.combat.assassins.bindings)
        source = tuple(bot.position)
        self.pose(run, player, yaw=16000, pitch=5000)
        self.assertEqual(tuple(bot.position), source)

    def test_victim_replacement_immediately_cancels_source_without_world_poll(self):
        _run, match, player, bot, script, instance = self.acquire()
        old = player.body
        self.assertTrue(match.switch_hero(player, player.hero))
        self.assertNotEqual(player.body, old)
        self.assertFalse(match.combat.assassins.bindings)
        self.assertFalse(player.controller_component.instances)
        self.assertNotIn(36, script.component.held)
        self.assertTrue(instance.node_state(instance.graph.node(31)).active)
        self.assertFalse(match.combat.assassins.controlled(player))
        self.assertNotIn(bot.entity, match.combat.impulses.npcs)

    def rescue_scene(self, *, melee):
        run, match, player, bot = npc_fixtures.WinstonNpcTests.create(
            self, self.definition, control_caster=True)
        caster = run.players[1]
        if melee:
            self.assertTrue(match.switch_hero(caster, heroes()[winston.HERO]))
        caster.mover.position[:] = (0, 1, -1.5)
        caster.mover.body = None
        distance = player.mover.data.stand_radius + bot.movement_data.stand_radius - .05
        bot.position = (0, 1, distance)
        script = bot.body_script
        script.set_manual_target(player.entity)
        self.step(run, bot, script)
        self.assertTrue(match.combat.assassins.controlled(player))
        return run, match, player, bot, script, caster

    def test_assassin_contact_across_winston_barrier_surface_still_pins(self):
        run, match, player, bot, script, _root, _behavior = self.scene()
        run.step({player: (0, 0, 16)})
        for _ in range(3):
            run.step({})
        fight = match.combat
        bubble = fight.barriers.actors[player.body]
        player.mover.position[:] = (0, 1, bubble.centre[2] + bubble.radius - .2)
        player.mover.body = None
        distance = player.mover.data.stand_radius + bot.movement_data.stand_radius - .05
        bot.position = (0, 1, player.mover.position[2] + distance)
        self.assertGreater(bot.position[2], bubble.centre[2] + bubble.radius)
        script.set_manual_target(player.entity)
        self.step(run, bot, script)
        self.assertTrue(fight.assassins.controlled(player))
        self.assertEqual(fight.vitals[player.body].total, 480)
        self.assertEqual(tuple(bot.position), tuple(player.mover.position))
        self.assertIs(fight.barriers.actors[player.body], bubble)
        self.assertEqual(bubble.health, 700)

    def test_actual_projected_barrier_absorbs_entry_and_prevents_pin(self):
        for initial in (200, 1):
            with self.subTest(barrier_health=initial):
                run, match, player, bot = npc_fixtures.WinstonNpcTests.create(
                    self, self.definition, control_caster=True)
                fight = match.combat
                caster = run.players[1]
                self.assertTrue(match.switch_hero(caster, heroes()[0x02E0000000000068]))
                caster.mover.position[:] = (0, 1, -3)
                run.step({caster: (0, 0, 16)})
                for _ in range(4):
                    run.step({})
                self.assertTrue(fight.zarya_barriers.protects(player))
                barrier = fight.zarya_barriers.projected[caster.body]
                self.assertEqual(barrier.remaining, 200)
                barrier.remaining = initial
                distance = player.mover.data.stand_radius + bot.movement_data.stand_radius - .05
                bot.position = (0, 1, distance)
                script = bot.body_script
                script.set_manual_target(player.entity)
                self.step(run, bot, script)
                self.assertEqual(fight.vitals[player.body].total, 500)
                self.assertEqual(barrier.remaining, max(0, initial - 25))
                self.assertFalse(player.controller_component.instances)
                self.assertFalse(fight.assassins.bindings)
                self.assertFalse(fight.assassins.admissions)
                self.assertNotIn(36, script.component.held)
                run.step({})
                self.assertAlmostEqual(caster.body_script.component.entity_var(87).value(),
                                       min(initial, 25) / 5, places=4)
                if initial == 1:
                    self.assertFalse(fight.zarya_barriers.protects(player))
                for _ in range(150):
                    run.step({})
                self.assertEqual(fight.vitals[player.body].total, 500)
                self.assertFalse(fight.assassins.controlled(player))
                script.set_manual_target(player.entity)
                self.step(run, bot, script)
                self.assertEqual(fight.vitals[player.body].total, 480)
                self.assertTrue(fight.assassins.controlled(player))
                self.assertFalse(fight.assassins.admissions)

    def test_actual_ice_wall_lift_releases_active_pin_on_confirmation(self):
        run, match, player, bot = npc_fixtures.WinstonNpcTests.create(
            self, self.definition, control_caster=True)
        fight = match.combat
        mei = run.players[1]
        self.assertTrue(match.switch_hero(mei, heroes()[protections.MEI]))
        mei.mover.position[:] = (0, 1, 2)
        mei.mover.body = None
        player.mover.position[:] = (0, 1, 5)
        player.mover.body = None
        distance = player.mover.data.stand_radius + bot.movement_data.stand_radius - .05
        bot.position = (0, 1, 5 + distance)
        script = bot.body_script
        script.set_manual_target(player.entity)
        self.step(run, bot, script)
        self.assertTrue(fight.assassins.controlled(player))
        for _ in range(75):
            run.step({})
        self.assertLess(fight.vitals[player.body].total, 480)
        self.assertTrue(fight.assassins.controlled(player))
        angle = aim(fight._eye(mei), (0, 1, 5))
        for button in (16, 0, 1):
            run.step({mei: (*angle, button)})
        self.assertEqual(len(fight.walls.pieces), 5)
        self.assertGreater(player.mover.position[1], bot.position[1] + 1)
        self.assertFalse(fight.assassins.bindings)
        self.assertFalse(fight.assassins.health_clock)
        self.assertFalse(player.controller_component.instances)
        self.assertNotIn(36, script.component.held)
        frame = player.body_script.frame + 1
        self.assertFalse(any(mod.acts(frame) for key, mod in player.mover.mods.items() if len(key) == 4))
        health = fight.vitals[player.body].total
        for button in (0, 16):
            run.step({mei: (*angle, button)})
        self.assertFalse(fight.walls.pieces)
        for _ in range(90):
            run.step({})
        self.assertEqual(fight.vitals[player.body].total, health)
        self.assertFalse(fight.assassins.controlled(player))

    def test_actual_ally_winston_quick_melee_rescues_without_health_or_event_injection(self):
        run, match, player, bot, script, caster = self.rescue_scene(melee=True)
        health = match.combat.vitals[bot.entity].total
        for frame in range(60):
            run.step({caster: (0, 0, 0x800 if frame == 0 else 0)})
            if not match.combat.assassins.controlled(player):
                break
        self.assertLess(frame, 59)
        self.assertEqual(health - match.combat.vitals[bot.entity].total, 30)
        self.assertFalse(player.controller_component.instances)
        self.assertFalse(match.combat.assassins.bindings)
        self.assertNotIn(36, script.component.held)
        health = match.combat.vitals[player.body].total
        for _ in range(70):
            run.step({})
        self.assertEqual(match.combat.vitals[player.body].total, health)

    def test_actual_pin_interrupts_pending_primal_punch_and_rescue_allows_fresh_hit(self):
        run, match, player, bot = npc_fixtures.WinstonNpcTests.create(
            self, self.definition, control_caster=True)
        caster = run.players[1]
        self.assertTrue(match.switch_hero(caster, heroes()[winston.HERO]))
        caster.mover.position[:] = (0, 1, -1.5)
        caster.mover.body = None
        bot.position = (0, 1, 20)
        self.rage(run, player)
        weapon = next(i for i in player.body_script.component.instances.values() if i.graph.index == 0xFE)
        pending = False
        for _ in range(80):
            run.step({player: (0, 0, 1)})
            pending = any(weapon.states.get(i) is not None and weapon.states[i].active for i in (20, 21))
            if pending:
                break
        self.assertTrue(pending)
        radius = player.mover.data.stand_radius + bot.movement_data.stand_radius
        bot.position = (0, 1, radius - .05)
        script = bot.body_script
        script.set_manual_target(player.entity)
        self.step(run, bot, script)
        fight = match.combat
        self.assertTrue(fight.assassins.controlled(player))
        vitals = fight.vitals[bot.entity]
        before = vitals.total
        for _ in range(25):
            run.step({player: (0, 0, 1)})
            self.assertTrue(fight.assassins.controlled(player))
            self.assertEqual(vitals.total, before)
        for frame in range(45):
            run.step({caster: (0, 0, 0x800 if frame == 0 else 0)})
            if not fight.assassins.controlled(player):
                break
        self.assertFalse(fight.assassins.controlled(player))
        self.assertEqual(vitals.total, before - 30)
        self.assertTrue(winston.primal_active(player.body_script.component))
        for _ in range(20):
            run.step({})
            self.assertEqual(vitals.total, before - 30)
        self.assertNotIn(bot.entity, fight.impulses.npcs)
        bot.position = (0, 1, 1.5)
        for _ in range(50):
            run.step({player: (0, 0, 1)})
            if vitals.total < before - 30:
                break
        self.assertEqual(vitals.total, before - 70)
        self.assertEqual(player.body_script.component.entity_var(342).value(), 0)

    def test_short_assassin_pin_clears_buffered_quick_melee(self):
        run, match, player, bot = npc_fixtures.WinstonNpcTests.create(
            self, self.definition, control_caster=True)
        caster = run.players[1]
        self.assertTrue(match.switch_hero(caster, heroes()[winston.HERO]))
        caster.mover.position[:] = (0, 1, -1.5)
        caster.mover.body = None
        bot.position = (0, 1, 20)
        run.step({player: (0, 0, 0x800)})
        for _ in range(54):
            run.step({})
        run.step({player: (0, 0, 0x800)})
        melee = next(i for i in player.body_script.component.instances.values() if i.graph.index == 0x0043)
        ability = melee.state(6)
        self.assertGreater(ability.remaining(), 0)
        self.assertTrue(ability.presses)
        self.assertEqual(melee.state(4).counter, 1)
        radius = player.mover.data.stand_radius + bot.movement_data.stand_radius
        bot.position = (0, 1, radius - .05)
        script = bot.body_script
        script.set_manual_target(player.entity)
        self.step(run, bot, script)
        fight = match.combat
        self.assertTrue(fight.assassins.controlled(player))
        self.assertFalse(ability.presses)
        vitals = fight.vitals[bot.entity]
        before = vitals.total
        run.step({caster: (0, 0, 0x800)})
        self.assertFalse(fight.assassins.controlled(player))
        self.assertEqual(vitals.total, before - 30)
        self.assertGreater(ability.remaining(), 0)
        for _ in range(65):
            run.step({})
            self.assertEqual(vitals.total, before - 30)
        self.assertEqual(melee.state(4).counter, 1)
        self.assertNotIn(bot.entity, fight.impulses.npcs)
        bot.position = (0, 1, 1.5)
        run.step({player: (0, 0, 0x800)})
        for _ in range(25):
            run.step({})
        self.assertEqual(vitals.total, before - 60)
        self.assertEqual(melee.state(4).counter, 2)

    def test_short_assassin_pin_clears_buffered_jump_and_barrier_recasts(self):
        for button, graph_index, ability_index in ((8, 0x010A, 19), (16, 0x00FA, 2)):
            with self.subTest(button=button):
                self.check_interrupted_ability_buffer(button, graph_index, ability_index)

    def check_interrupted_ability_buffer(self, button, graph_index, ability_index):
        run, match, player, bot = npc_fixtures.WinstonNpcTests.create(
            self, self.definition, control_caster=True)
        caster = run.players[1]
        self.assertTrue(match.switch_hero(caster, heroes()[winston.HERO]))
        caster.mover.position[:] = (0, 1, -1.5)
        caster.mover.body = None
        bot.position = (0, 1, 20)
        fight = match.combat
        jump = button == 8
        cast = {player: (0, -16384 if jump else 0, button)}
        effects = fight.winston_jump.flights if jump else fight.barriers.actors
        run.step(cast)
        for _ in range(4):
            run.step({})
        first = effects[player.body]
        instance = next(i for i in player.body_script.component.instances.values()
                        if i.graph.index == graph_index)
        ability = instance.state(ability_index)
        first_cast = ability.last
        for _ in range(850):
            if ability.remaining() <= .1:
                break
            run.step({})
        self.assertNotIn(player.body, effects)
        run.step(cast)
        self.assertGreater(ability.remaining(), 0)
        self.assertTrue(ability.presses)
        radius = player.mover.data.stand_radius + bot.movement_data.stand_radius
        bot.position = (0, 1, radius - .05)
        script = bot.body_script
        script.set_manual_target(player.entity)
        self.step(run, bot, script)
        self.assertTrue(fight.assassins.controlled(player))
        self.assertFalse(ability.presses)
        run.step({caster: (0, 0, 0x800)})
        self.assertFalse(fight.assassins.controlled(player))
        self.assertGreater(ability.remaining(), 0)
        for _ in range(20):
            run.step({})
            self.assertNotIn(player.body, effects)
        self.assertEqual(ability.remaining(), 0)
        self.assertEqual(ability.last, first_cast)
        run.step(cast)
        for _ in range(4):
            run.step({})
        self.assertGreater(ability.last, first_cast)
        self.assertIsNot(effects[player.body], first)
        if not jump:
            self.assertEqual(effects[player.body].health, 700)

    def test_actual_ally_flashbang_releases_pin_in_same_effect_without_late_damage(self):
        run, match, player, bot, script, caster = self.rescue_scene(melee=False)
        for frame in range(80):
            run.step({caster: (0, 0, 16 if frame == 0 else 0)})
            if match.combat.is_stunned(bot):
                break
        self.assertLess(frame, 79)
        self.assertFalse(match.combat.assassins.controlled(player))
        self.assertFalse(player.controller_component.instances)
        self.assertFalse(match.combat.assassins.bindings)
        self.assertNotIn(36, script.component.held)
        health = match.combat.vitals[player.body].total
        for _ in range(70):
            run.step({})
        self.assertEqual(match.combat.vitals[player.body].total, health)

    def test_allied_peacekeeper_kill_releases_assassin_pin_and_restores_jump(self):
        run, match, player, bot, _script, caster = self.rescue_scene(melee=False)
        fight = match.combat
        vitals = fight.vitals[bot.entity]
        vitals.damage(vitals.total - 1)  # injured source, actual shot owns the kill
        x, y, z = bot.position
        yaw, pitch = aim(fight._eye(caster), (x, y + 1, z))
        for _ in range(25):
            run.step({player: (0, 0, 8), caster: (yaw, pitch, 1)})
            if bot.dead:
                break
        self.assertTrue(bot.dead)
        self.assertEqual(vitals.killer, caster.body)
        self.assertFalse(fight.assassins.bindings)
        self.assertFalse(player.controller_component.instances)
        self.assertFalse(fight.actions_blocked(player))
        self.assertNotIn(player.body, fight.winston_jump.flights)
        run.step({})
        run.step({player: (0, 0, 8)})
        self.assertIn(player.body, fight.winston_jump.flights)
        self.assertEqual(fight.vitals[player.body].total, 480)
        self.assertIn(bot, match.bots)

    def test_allied_emp_releases_assassin_pin_and_blocks_reacquisition_until_expiry(self):
        run, match, player, bot, script, caster = self.rescue_scene(melee=False)
        self.assertTrue(match.switch_hero(caster, heroes()[hacks.SOMBRA]))
        caster.mover.position[:] = (0, 1, -3)
        caster.mover.body = None
        ultimate = caster.body_script
        ultimate.set_ult_charge(ultimate.component.entity_var(338).value())
        fight = match.combat
        before = fight.vitals[player.body].total
        run.step({caster: (0, 0, 32)})
        for _ in range(55):
            if fight.hacks.active(bot.entity):
                break
            run.step({})
        [(application, deadline)] = fight.hacks.effects[bot.entity]
        self.assertEqual(application.source_entity(), caster.body)
        self.assertFalse(fight.hacks.active(player.body))
        self.assertFalse(fight.assassins.controlled(player))
        self.assertFalse(player.controller_component.instances)
        self.assertEqual(fight.vitals[bot.entity].now[combat.SHIELDS_POOL], 0)
        self.assertEqual(fight.vitals[player.body].total, before)

        run.frame = deadline - 2
        match.update(run.frame * combat.TICK, run.frame, frames=False)
        script.set_manual_target(player.entity)
        self.step(run, bot, script)
        self.assertTrue(fight.hacks.active(bot.entity))
        self.assertFalse(fight.assassins.controlled(player))
        self.assertEqual(fight.vitals[player.body].total, before)

        self.step(run, bot, script, pressed=False)
        self.assertFalse(fight.hacks.active(bot.entity))
        self.assertNotIn(application.id, application.component.instances)
        for _ in range(100):
            run.step({})
        script.set_manual_target(player.entity)
        self.step(run, bot, script)
        self.assertTrue(fight.assassins.controlled(player))
        self.assertEqual(fight.vitals[player.body].total, before - 20)

    def test_actual_ally_sleep_dart_releases_pin_without_late_damage(self):
        run, match, player, bot, script, caster = self.rescue_scene(melee=False)
        self.assertTrue(match.switch_hero(caster, heroes()[0x02E000000000013B]))
        caster.mover.position[:] = (0, 1, -3)
        caster.mover.body = None
        source_health = match.combat.vitals[bot.entity].total
        x, y, z = bot.position
        yaw, pitch = aim(match.combat._eye(caster), (x, y + 1, z))
        for frame in range(60):
            run.step({caster: (yaw, pitch, 8 if frame == 0 else 0)})
            if match.combat.is_asleep(bot):
                break
        self.assertTrue(match.combat.is_asleep(bot))
        self.assertEqual(source_health - match.combat.vitals[bot.entity].total, 5)
        self.assertFalse(player.controller_component.instances)
        self.assertFalse(match.combat.assassins.bindings)
        self.assertNotIn(36, script.component.held)
        health = match.combat.vitals[player.body].total
        for _ in range(70):
            run.step({})
        self.assertEqual(match.combat.vitals[player.body].total, health)
        self.assertTrue(match.combat.is_asleep(bot))
        self.assertEqual(npc.remove_spawned(match), (bot.entity,))
        self.assertNotIn(bot.entity, match.combat.sleeps)

    def test_real_npc_pitch_is_causal_in_full_server_snapshot(self):
        snapshot_case = snapshot_fixtures.ServerSnapshotTests()
        session, _peers, players = snapshot_case.scene()
        bot = npc.spawn(players[0].match, self.definition, (0, 1, 1), team=1)
        old = bot.pitch
        initial = snapshot_digest(snapshot_case.capture(session))
        bot.pitch = 8192
        self.assertNotEqual(snapshot_digest(snapshot_case.capture(session)), initial)
        bot.pitch = old
        self.assertEqual(snapshot_digest(snapshot_case.capture(session)), initial)

    def rage(self, run, caster):
        caster.body_script.set_ult_charge(caster.body_script.component.entity_var(338).value())
        run.step({caster: (0, 0, 32)})
        self.assertTrue(winston.primal_active(caster.body_script.component))

    def test_actual_ally_primal_punch_rescues_then_applies_source_knockback(self):
        run, match, player, bot, script, caster = self.rescue_scene(melee=True)
        self.rage(run, caster)
        health = match.combat.vitals[bot.entity].total
        for _frame in range(60):
            run.step({caster: (0, 0, 1)})
            if not match.combat.assassins.controlled(player):
                break
        self.assertLess(_frame, 59)
        self.assertEqual(health - match.combat.vitals[bot.entity].total, 40)
        self.assertFalse(match.combat.assassins.bindings)
        self.assertNotIn(36, script.component.held)
        self.assertIn(bot.entity, match.combat.impulses.npcs)
        before = tuple(bot.position)
        run.step({})
        self.assertNotEqual(tuple(bot.position), before)
        self.assertTrue(winston.primal_active(caster.body_script.component))

    def test_actual_enemy_primal_force_on_victim_carries_attached_source(self):
        run, match, player, bot, _script, caster = self.rescue_scene(melee=True)
        caster.team = bot.team  # punch victim; the source is this player's ally
        self.rage(run, caster)
        victim_health = match.combat.vitals[player.body].total
        source_health = match.combat.vitals[bot.entity].total
        for _frame in range(60):
            run.step({caster: (0, 0, 1)})
            if match.combat.vitals[player.body].total < victim_health:
                break
        self.assertLess(_frame, 59)
        self.assertEqual(match.combat.vitals[bot.entity].total, source_health)
        self.assertTrue(match.combat.assassins.controlled(player))
        height = player.mover.position[1]
        for _ in range(20):
            run.step({})
            self.assertEqual(tuple(bot.position), tuple(player.mover.position))
            self.assertTrue(match.combat.assassins.controlled(player))
        self.assertGreater(player.mover.position[1], height)


if __name__ == '__main__':
    unittest.main()
