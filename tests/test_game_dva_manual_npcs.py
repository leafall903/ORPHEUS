"""Actual five-body manual combat contexts; autonomous AI/native controller OPEN."""

import unittest
from dataclasses import replace
from unittest.mock import patch

import test_game_dva_npc_combat as incoming
import test_game_dva_npc_control as control_tests
import test_server_snapshot as snapshot_tests
from test_game_combat import KANEZAKA_DM, FakeClient, Run, aim, dva_pilot_input
from test_game_mover import FLOOR, wall

from ow174.game import assassin_effect, combat, heroselect, npc, npc_control, reinhardt_barrier, sigma_grasp
from ow174.game.body_vm_snapshot import export_component_vm
from ow174.game.collision import World
from ow174.game.commands import Command
from ow174.game.content import find_hero
from ow174.game.mover import mover_data
from ow174.game.script import decode, expr, graph, message_receiver, message_state, raw_payloads, runtime
from ow174.game.server_snapshot import snapshot_digest


class AssassinLinkedTargetTests(unittest.TestCase):
    def test_actual_victim_node_requires_explicit_mover_presence(self):
        model = graph.graph(0x1177)
        self.assertIsNotNone(model)
        controller = 0xA0000101
        body = 0xA0000102

        for sample in (NotImplemented, None, (body, 0.25, None, None)):
            with self.subTest(sample=sample):
                world = runtime.World()
                with patch.object(runtime.World, "linked_target_sample", return_value=sample) as provider:
                    host = runtime.Component(controller, world=world, strict=True)
                    effect = host.create(model, init=False)
                    state = effect.node_state(model.node(9))
                    if sample is NotImplemented:
                        with self.assertRaises(runtime.UnsupportedScriptError) as error:
                            state.begin()
                        self.assertEqual(error.exception.diagnostic["feature"], "linked target")
                        self.assertFalse(host.state_registries)
                        continue

                    state.begin()
                    provider.assert_called_with(controller)
                    self.assertTrue(state.active)
                    payload = state.payload()
                    if sample is None:
                        self.assertIsNone(state.mover_angles)
                        self.assertNotIn(state, effect.ticking)
                        self.assertFalse(payload["has_entity"])
                    else:
                        self.assertEqual(state.mover_angles, (0.0,) * 6)
                        self.assertIn(state, effect.ticking)
                        self.assertEqual(payload["entity"], body)
                        provider.return_value = (body, 0.5, None, None)
                        state.tick()
                        self.assertEqual(state.mover_angles, (0.25, 0.0) * 3)
                    self.assertTrue(raw_payloads.encode_payload(
                        state.node.cls, payload, owner=True, full=True,
                    ))
                    active = export_component_vm(host)
                    host.destroy(effect)
                    self.assertFalse(host.state_registries)
                    self.assertNotEqual(export_component_vm(host), active)


    def test_actual_pin_request_mounts_and_retires_in_controller_namespace(self):
        source = 0xA0000103
        controller = 0xA0000101
        body = 0xA0000102
        attack = runtime.Component(source).create(graph.graph(0x1157), init=False)
        action = attack.graph.node(45)
        for sample in (None, (body, 0.25, None, None)):
            with self.subTest(sample=sample):
                vm = assassin_effect.VictimEffect(controller, 10, health_absent=True, mover_sample=sample)
                host = vm.component
                attack.write((expr.INSTANCE, 536), expr.Entity(body))
                self.assertFalse(vm.submit(attack, action))
                self.assertFalse(host.instances)
                attack.write((expr.INSTANCE, 536), expr.Entity(controller))
                self.assertTrue(vm.submit(attack, action))
                vm.advance(11)
                effect, = host.instances.values()
                self.assertEqual(effect.graph.index, 0x1177)
                self.assertEqual(effect.source_entity(), source)
                self.assertTrue(effect.auto_retire)
                self.assertIs(effect.node_state(effect.graph.node(21)).current, False)
                receiver = effect.states.get(effect.graph.node(17).state)
                self.assertTrue(receiver is None or not receiver.active)
                linked = effect.node_state(effect.graph.node(9))
                self.assertTrue(linked.active)
                self.assertEqual(linked.linked_entity, body if sample is not None else None)
                self.assertIs(host.entity_var(14695).value(), True)
                self.assertIsNone(attack.component.entity_var(14695, create=False))
                self.assertTrue(vm.submit(attack, action))
                self.assertEqual(tuple(host.instances.values()), (effect,))
                vm.retire(body)
                self.assertEqual(tuple(host.instances.values()), (effect,))
                vm.retire(source)
                self.assertFalse(host.instances)
                self.assertFalse(host.state_registries)
                self.assertFalse(host.world.scopes.states)
                self.assertFalse(host.world.messages.states)
                self.assertIsNone(host.entity_var(14695).value())


    def test_authored_victim_wait_requests_deferred_self_stop(self):
        source, controller = 0xA0000103, 0xA0000101
        attack = runtime.Component(source).create(graph.graph(0x1157), init=False)
        attack.write((expr.INSTANCE, 536), expr.Entity(controller))
        vm = assassin_effect.VictimEffect(controller, 10, health_absent=True, mover_sample=None)
        self.assertTrue(vm.submit(attack, attack.graph.node(45)))
        vm.advance(11)
        host = vm.component
        effect, = host.instances.values()
        wait = effect.node_state(effect.graph.node(45))
        wait.request(runtime.BEGIN)
        host.run_now()
        deadline = wait.start + 2000
        self.assertIs(host.entity_var(14695).value(), True)
        vm.advance(deadline // host.frame_ms - 1)
        self.assertFalse(effect.stopped)
        vm.advance(deadline // host.frame_ms)
        self.assertTrue(effect.stopped)
        self.assertIsNone(host.entity_var(14695).value())
        self.assertFalse(host.state_registries)
        self.assertFalse(host.world.scopes.states)
        self.assertFalse(host.world.messages.states)
        self.assertEqual(vm.owner_effect_wires(), ((), {}))


    def test_positive_health_routes_authored_persistent_release_message(self):
        source, controller = 0xA0000103, 0xA0000101
        attack_world = runtime.World()
        attack_vm = runtime.Component(source, world=attack_world, strict=True)
        attack = attack_vm.create(graph.graph(0x1157), init=False)
        attack.write((expr.INSTANCE, 536), expr.Entity(controller))
        vm = assassin_effect.VictimEffect(controller, 10, health_absent=True, mover_sample=None)
        host = vm.component
        world = host.world
        receiver_context = world.messages
        receiver_context.receiver_policy = lambda state: message_receiver.Policy(
            False, False, False, False, False, False, True, False, False, False,
        )
        receiver_context.receiver_generation = lambda state: 17
        sender_context = message_state.Context(
            attack_vm, receiver_context.host, policy=world.message_policy,
            frame=world.message_frame, priority=world.message_priority,
        )
        ordinary_native = world.native

        def positive_health(cfg, instance):
            if cfg.get("$") == "STU_9B7648C3":
                return 150.0
            return ordinary_native(cfg, instance)

        with (
            patch.object(world, "native", side_effect=positive_health),
            patch.object(attack_world, "message_state_context", return_value=sender_context),
        ):
            self.assertTrue(vm.submit(attack, attack.graph.node(45)))
            vm.advance(11)
            effect, = host.instances.values()
            receiver = effect.node_state(effect.graph.node(17))
            self.assertTrue(receiver.active)
            self.assertEqual(receiver.message_key, 0x08FE)
            self.assertIsNone(receiver.message_selected)
            self.assertIs(host.entity_var(14695).value(), True)
            sender = attack.node_state(attack.graph.node(18))
            sender.begin()
            message_state.refresh(sender)
            self.assertEqual(
                [(key.entity, key.message) for key in sender.message_keys], [(controller, 0x08FE)],
            )
            self.assertFalse(effect.stopped)
            with patch.object(effect, "run_node", wraps=effect.run_node) as trace:
                vm.advance(12)
            self.assertIs(receiver.message_selected, sender)
            self.assertIn(15, [call.args[0].pos for call in trace.call_args_list])
            self.assertTrue(effect.stopped)
            self.assertIsNone(host.entity_var(14695).value())
            self.assertFalse(receiver_context.states)
            self.assertFalse(host.state_registries)
            sender.end(False)
            self.assertFalse(receiver_context.host.manager.channels)


class ManualIncomingTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        incoming.NpcIncomingTests.setUpClass.__func__(cls)

    def scene(self, mech, name, distance=None, *, teammate=False):
        run = Run(KANEZAKA_DM, ('DVa', 'Rescuer') if teammate else ('DVa',))
        match = run.match
        match.bots.clear()
        match._pending_bot_scripts.clear()
        player = run.players[0]
        hero = find_hero('dva')
        if mech:
            hero = replace(hero, body=incoming.MECH)
        self.assertTrue(match.switch_hero(player, hero))
        dva_pilot_input(player)
        player.mover.position = [0, 1, distance if distance is not None else (2 if name == 'Assassin' else 6)]
        player.mover.body = None
        for _ in range(100):
            run.step({})
        bot = npc.spawn(match, self.by_name[name], (0, 1, 0), team=1)
        self.assertIsNotNone(bot.body_script)
        target = next(t for t in match.combat._targets() if t.owner is player)
        return run, player, bot, match.combat.vitals_of(target)

    def first_loss(self, run, health, maximum=300):
        before = health.total
        for frame in range(1, maximum+1):
            run.step({})
            if health.total < before:
                return before-health.total, frame
        return 0.0, maximum

    def command(self, run, player, *, buttons=0, forward=0, yaw=-32768, target=None):
        run.frame += 1
        run.match.update(run.frame*.016, run.frame)
        pitch = 0
        if target is not None:
            point = (target.position[0], target.position[1]+mover_data(target.body).stand_height*.5,
                     target.position[2])
            yaw, pitch = aim(run.match.combat._eye(player), point)
        player.take_commands([Command(run.frame, buttons=buttons, forward=forward, yaw=yaw, pitch=pitch)])

    def test_all_five_manual_inputs_damage_both_forms(self):
        expected = {'Trooper': (20, 15), 'Enforcer': (50, 18), 'Sniper': (100, 95),
                    'Assassin': (25, 20), 'Heavy Assault': (10, 5)}
        for mech in (False, True):
            for name, amounts in expected.items():
                with self.subTest(mech=mech, npc=name):
                    run, player, bot, health = self.scene(mech, name)
                    npc_control.engage(run.match, bot, player)
                    loss, frame = self.first_loss(run, health)
                    self.assertEqual(loss, amounts[int(mech)], (loss, frame))
                    self.assertIsNotNone(bot.body_script)
                    if name == 'Assassin':
                        self.assertEqual(bot.body_script.component.world.ai_weapon_button(0), 36)
                        self.assertEqual(run.match.combat.pins, {bot.entity: player.body})
                    if mech:
                        self.assertEqual(health.now[combat.HEALTH_POOL], 300)

    def test_enforcer_launches_authored_pellets_with_contact_falloff_in_both_forms(self):
        for mech in (False, True):
            for distance in (6, 11, 18):
                with self.subTest(mech=mech, distance=distance):
                    run, player, bot, health = self.scene(mech, "Enforcer", distance=distance)
                    fight = run.match.combat
                    npc_control.engage(run.match, bot, player)
                    for _ in range(90):
                        run.step({})
                        if fight.rockets:
                            break
                    volley = tuple(fight.rockets)
                    self.assertEqual(len(volley), 25)
                    self.assertEqual(len({rocket.direction for rocket in volley}), 25)
                    target = fight.player_target(player)
                    a, b = target.segment()
                    distances = [combat.ray_capsule(shot.position, shot.direction, a, b, target.radius)
                                 for shot in volley]
                    amounts = [2 * (1 - .8 * min(1, max(0, (d - 8) / 7)))
                               for d in distances if d is not None]
                    self.assertTrue(amounts)
                    npc_control.stop(run.match)
                    before = health.total
                    with patch.object(fight, "_damage", wraps=fight._damage) as damage:
                        for _ in range(18):
                            run.step({})
                    actual = [call.args[2] for call in damage.call_args_list if call.args[0] is bot]
                    self.assertEqual(len(actual), len(amounts))
                    for dealt, expected in zip(sorted(actual), sorted(amounts), strict=True):
                        self.assertAlmostEqual(dealt, expected, places=6)
                    self.assertAlmostEqual(before - health.total, sum(amounts) / (2 if mech else 1), places=5)

    def test_enforcer_in_flight_volley_replays_demech_and_pilot_exposure_across_gap(self):
        from test_game_dva_created_body import CreatedBodyTests

        results = []
        for catchup in (False, True):
            with self.subTest(catchup=catchup):
                run, pilot, other, _, mech = CreatedBodyTests().scenario()
                other.spawned = False
                match, fight = run.match, run.match.combat
                x, y, z = mech.position
                bot = npc.spawn(match, self.by_name["Enforcer"], (x, y, z - 6), team=1)
                mech_health = fight.vitals[mech.entity] = combat.Vitals(mech.entity, 1)
                npc_control.engage(match, bot, pilot)
                for _ in range(90):
                    run.step({})
                    if fight.rockets:
                        break
                self.assertEqual(len(fight.rockets), 25)
                npc_control.stop(match)
                self.assertFalse(bot.firing)
                self.assertTrue(all(shot.last_tick == run.frame for shot in fight.rockets))
                launched = run.frame
                for offset in ((8,) if catchup else range(1, 9)):
                    match.update((launched + offset) * combat.TICK, launched + offset, frames=False)
                self.assertIsNone(pilot.controlled_mech)
                self.assertTrue(mech_health.dead)
                self.assertIs(fight.player_target(pilot).owner, pilot)
                health = fight.vitals_of(fight.player_target(pilot))
                after = health.total
                match.update((launched + 8) * combat.TICK, launched + 8, frames=False)
                self.assertEqual(health.total, after)
                results.append((
                    mech_health.death_tick - launched,
                    fight.replacement_ticks[pilot.body] - launched,
                    after,
                ))
        self.assertEqual(results[0], (5, 5, 138))
        self.assertEqual(results[1], results[0])

    def test_assassin_live_pin_requests_effect_on_both_form_controllers(self):
        for mech in (False, True):
            with self.subTest(mech=mech):
                run, player, bot, health = self.scene(mech, "Assassin")
                npc_control.engage(run.match, bot, player)
                loss, _ = self.first_loss(run, health)
                self.assertEqual(loss, 20 if mech else 25)
                vm = run.match.combat.assassin_effects[player.entity]
                effect, = vm.component.instances.values()
                self.assertEqual(effect.graph.index, 0x1177)
                self.assertTrue(export_component_vm(vm.component))
                self.assertEqual(effect.source_entity(), bot.entity)
                self.assertEqual(vm.component.entity, player.entity)
                self.assertEqual(run.match.combat.pins, {bot.entity: player.body})
                self.assertIs(vm.component.entity_var(14695).value(), True)
                self.assertIsNone(player.body_script.component.entity_var(14695, create=False))
                stock = set(heroselect.controller_wire_ids(run.match.controller))

                def owner_frames(player):
                    return [update for update in player.client.entities
                            if update.stream is player.script and update.resend]

                active_update = owner_frames(player)[-1]
                active = decode.read_chunk(active_update.chunk.getvalue(), active_update.chunk.count)
                self.assertEqual(set(active.instances), stock | {effect.id})
                linked = effect.graph.node(9).state
                self.assertEqual(active.instances[effect.id].states[linked], (
                    True, {"has_entity": 0, "values": [{"set": 0}] * 7},
                ))
                flag, bindings = active.entity_vars[14695]
                self.assertIs(flag, True)
                binding, = bindings
                self.assertEqual((binding.instance, binding.state), (effect.id, effect.graph.node(46).state))
                run.step({})
                self.assertIs(owner_frames(player)[-1], active_update)

                npc_control.stop(run.match)
                self.assertFalse(run.match.combat.assassin_effects)
                self.assertFalse(vm.component.instances)
                self.assertIsNone(vm.component.entity_var(14695).value())
                run.step({})
                retired_update = owner_frames(player)[-1]
                self.assertGreater(retired_update.chunk_last, active_update.chunk_last)
                retired = decode.read_chunk(retired_update.chunk.getvalue(), retired_update.chunk.count)
                self.assertEqual(set(retired.instances), stock)
                self.assertNotIn(14695, retired.entity_vars)

    def test_all_five_manual_inputs_target_normally_selected_controlled_mech(self):
        from test_game_dva_created_body import CreatedBodyTests

        expected = {'Trooper': 15, 'Enforcer': 18, 'Sniper': 95, 'Assassin': 20, 'Heavy Assault': 5}
        for name, amount in expected.items():
            with self.subTest(npc=name):
                run, pilot, _, _, mech = CreatedBodyTests().scenario()
                match = run.match
                x, y, z = mech.position
                distance = 2 if name == 'Assassin' else 6
                bot = npc.spawn(match, self.by_name[name], (x, y, z - distance), team=1)
                target = match.combat.player_target(pilot)
                self.assertIs(target.owner, mech)
                npc_control.engage(match, bot, pilot)
                loss, frame = self.first_loss(run, match.combat.vitals_of(target))
                self.assertEqual(loss, amount, (loss, frame))
                self.assertEqual(pilot.body_script.component.entity_var(253).value(), 150)
                if name == 'Assassin':
                    self.assertEqual(match.combat.pins, {bot.entity: mech.entity})

    def test_all_five_actual_npcs_demech_and_reacquire_the_live_pilot(self):
        from test_game_dva_created_body import CreatedBodyTests

        expected = {'Trooper': 20, 'Enforcer': 12, 'Sniper': 100, 'Assassin': 25, 'Heavy Assault': 10}
        for name, pilot_damage in expected.items():
            with self.subTest(npc=name):
                run, pilot, _, _, mech = CreatedBodyTests().scenario()
                match = run.match
                fight = match.combat
                pilot_script = pilot.body_script
                x, y, z = mech.position
                distance = 2 if name == 'Assassin' else 6
                bot = npc.spawn(match, self.by_name[name], (x, y, z - distance), team=1)
                fight.vitals[mech.entity] = combat.Vitals(mech.entity, 1)
                npc_control.engage(match, bot, pilot)
                for _ in range(300):
                    run.step({})
                    if pilot.controlled_mech is None:
                        break
                self.assertTrue(mech.dead)
                self.assertIsNone(pilot.controlled_mech)
                self.assertIs(pilot.body_script, pilot_script)
                self.assertEqual(bot.attack_target, pilot.entity)
                self.assertNotIn(mech.entity, fight.pins.values())
                target = fight.player_target(pilot)
                self.assertIs(target.owner, pilot)
                health = fight.vitals_of(target)
                if name == 'Enforcer':
                    self.assertTrue(any(rocket.shooter is bot for rocket in fight.rockets))
                loss, frame = self.first_loss(run, health, maximum=460)
                self.assertEqual(loss, pilot_damage, (loss, frame))
                if name == 'Enforcer':
                    self.assertEqual(frame, 1)
                if name == 'Assassin':
                    self.assertEqual(fight.pins, {bot.entity: pilot.body})
                npc_control.stop(match)
                self.assertNotIn(pilot.body, fight.pins.values())
                npc_health = fight.vitals[bot.entity]
                before = npc_health.total
                for _ in range(20):
                    run.step({})
                self.command(run, pilot, buttons=1, target=bot)
                for _ in range(100):
                    run.step({})
                self.assertLess(npc_health.total, before)

    def test_all_five_actual_npcs_receive_attacks_and_credit_charge_in_both_forms(self):
        from test_game_dva_created_body import CreatedBodyTests

        from ow174.game import body_entities
        from ow174.game.script.driver import ULT_CHARGE

        self.enterContext(patch.object(body_entities, "ULT_SECONDS", 0))
        attacks = (
            (False, 1, 2, 22, 11), (False, 16, 2, 126, 36),
            (False, 0x800, 1.5, 30, 1), (False, 8, 4, 10, 1),
            (True, 1, 2, 14, 1), (True, 0x800, 1.5, 30, 1),
        )
        for name, definition in self.by_name.items():
            for pilot_form, button, distance, amount, notices in attacks:
                with self.subTest(npc=name, pilot_form=pilot_form, button=button):
                    run, pilot, other, _, mech = CreatedBodyTests().scenario()
                    other.spawned = False
                    match = run.match
                    fight = match.combat
                    pilot.body_script.ult_seconds = 0
                    if pilot_form:
                        dva_pilot_input(pilot)
                        pilot.mover.position[0] += 10
                        pilot.mover.body = None
                    target = fight.player_target(pilot)
                    actor = target.owner
                    x, y, z = target.feet
                    inactive = mech if pilot_form else pilot
                    inactive_charge = inactive.body_script.component.entity_var(ULT_CHARGE)
                    inactive_before = inactive_charge.value()
                    bot = npc.spawn(match, definition, (x, y, z + distance), team=1)
                    health = fight.vitals[bot.entity]
                    before = health.total
                    for _ in range(100):
                        run.step({})
                    point = (x, y + 1, z + distance)
                    if button == 1:
                        capsule = next(item for item in fight._targets() if item.owner is bot)
                        tx, ty, tz = capsule.feet
                        point = (tx, ty + .85, tz - capsule.radius)
                    yaw, pitch = aim(fight._eye(actor), point)
                    if button == 8:
                        pitch = 0
                    run.step({pilot: (yaw, pitch, button)})
                    for _ in range(155 if button == 16 else 32):
                        run.step({pilot: (yaw, pitch, button if button == 16 else 0)})
                    self.assertIs(pilot.controlled_mech, None if pilot_form else mech)
                    self.assertEqual(health.total, before - amount)
                    self.assertEqual(actor.body_script.component.entity_var(ULT_CHARGE).value(), amount)
                    self.assertEqual(inactive_charge.value(), inactive_before)
                    hits = pilot.client.messages(combat.HIT)
                    self.assertEqual(len(hits), notices)
                    self.assertEqual({hit["+0x78"]["+0x0"]["+0x0"] for hit in hits}, {bot.entity})
                    if button == 8:
                        self.assertGreater(bot.position[2], z + distance)

    def test_all_five_actual_npcs_acquire_the_replacement_after_call_mech(self):
        self.check_call_mech_reacquisition(autonomous=False)

    def test_all_five_autonomous_npcs_acquire_the_replacement_after_call_mech(self):
        self.check_call_mech_reacquisition(autonomous=True)

    def check_call_mech_reacquisition(self, *, autonomous):
        from test_game_dva_created_body import CreatedBodyTests
        from test_game_mover import box

        damage = {
            'Trooper': (15, 20), 'Enforcer': (18, 12), 'Sniper': (95, 100),
            'Assassin': (20, 25), 'Heavy Assault': (5, 10),
        }
        for name, (mech_damage, pilot_damage) in damage.items():
            with self.subTest(npc=name):
                run, pilot, other, _, old = CreatedBodyTests().ready_call_mech()
                other.spawned = False
                match = run.match
                fight = match.combat
                open_world = match.collision_world
                x, y, z = pilot.mover.position
                match.collision_world = World(
                    box(x - 20, x + 20, 0, y, z - 20, z + 20)
                    + wall(x - 10, z - 1, x + 10, z - 1)
                )
                bot = npc.spawn(match, self.by_name[name], (x, y, z - 6), team=1, autonomous=autonomous)
                if not autonomous:
                    npc_control.engage(match, bot, pilot)
                run.step({pilot: (0, 0, 32)})
                for _ in range(120):
                    run.step({})
                    if pilot.controlled_mech is not None:
                        break
                self.assertIsNotNone(pilot.controlled_mech)
                self.assertIsNot(pilot.controlled_mech, old)
                target = fight.player_target(pilot)
                self.assertIs(target.owner, pilot.controlled_mech)
                health = fight.vitals_of(target)
                self.assertEqual(health.total, 600)
                self.assertFalse(bot.attack_input)
                if autonomous:
                    self.assertIsNone(bot.attack_target)
                x, y, z = target.feet
                bot.position = (x, y, z - (2 if name == 'Assassin' else 6))
                match.collision_world = open_world
                loss, frame = self.first_loss(run, health)
                self.assertEqual(loss, mech_damage, (loss, frame))
                self.assertEqual(bot.attack_target, pilot.entity)
                if name == 'Assassin':
                    self.assertEqual(fight.pins, {bot.entity: target.entity})

                replacement = target.owner
                pilot_script = pilot.body_script
                fight.vitals[target.entity] = combat.Vitals(target.entity, 1)
                for _ in range(460):
                    run.step({})
                    if pilot.controlled_mech is None:
                        break
                self.assertTrue(replacement.dead)
                self.assertIsNone(pilot.controlled_mech)
                self.assertIs(pilot.body_script, pilot_script)
                self.assertNotIn(replacement.entity, fight.pins.values())
                returned = fight.player_target(pilot)
                self.assertIs(returned.owner, pilot)
                self.assertEqual(returned.entity, pilot.body)
                loss, frame = self.first_loss(run, fight.vitals_of(returned), maximum=460)
                self.assertEqual(loss, pilot_damage, (loss, frame))
                self.assertEqual(bot.attack_target, pilot.entity)
                if name == 'Assassin':
                    self.assertEqual(fight.pins, {bot.entity: pilot.body})
                npc_control.stop(match)
                self.assertNotIn(pilot.body, fight.pins.values())
                npc_health = fight.vitals[bot.entity]
                for _ in range(20):
                    run.step({})
                self.command(run, pilot, buttons=1, target=bot)
                loss, frame = self.first_loss(run, npc_health, maximum=100)
                self.assertEqual(loss, 14, (loss, frame))

    def test_call_mech_protection_and_handoff_against_all_five_talon_npcs(self):
        from test_game_dva_created_body import CreatedBodyTests

        from ow174.game import assassin, defenses
        from ow174.game.script.driver import ULT_CHARGE, ULT_COST

        engagement = {
            'Trooper': (45, 20), 'Enforcer': (3, 2), 'Sniper': (0, 100),
            'Assassin': (64, None), 'Heavy Assault': (42, 5),
        }
        for name, (engage_at, raw_damage) in engagement.items():
            with self.subTest(npc=name):
                run, pilot, other, lifecycle, old = CreatedBodyTests().scenario()
                other.spawned = False
                for _ in range(100):
                    run.step({})
                component = old.body_script.component
                component.set(ULT_CHARGE, component.entity_var(ULT_COST).value())
                run.step({pilot: (0, 0, 32)})
                for _ in range(220):
                    run.step({})
                self.assertIsNone(pilot.controlled_mech)
                self.assertNotIn(old, run.match.bots)
                charge = pilot.body_script.component
                self.assertEqual(charge.entity_var(ULT_CHARGE).value(), charge.entity_var(ULT_COST).value())

                match = run.match
                fight = match.combat
                x, y, z = pilot.mover.position
                distance = 2 if name == 'Assassin' else 6
                bot = npc.spawn(match, self.by_name[name], (x, y, z - distance), team=1)
                body = fight.player_target(pilot)
                health = fight.vitals_of(body)
                self.assertEqual(health.total, 150)
                pilot_packets = []
                replacement_hits = []
                visible_pin_samples = 0
                protected_handoff = False
                observed = fight._damage, bot, body, pilot, pilot_packets, replacement_hits

                def observe(shooter, target, amount, *args, _observed=observed, **kwargs):
                    damage, bot, body, pilot, pilot_packets, replacement_hits = _observed
                    protection = defenses.protection(target.owner)
                    applied = damage(shooter, target, amount, *args, **kwargs)
                    if shooter is bot and target.entity == body.entity:
                        pilot_packets.append((round(amount, 4), protection, applied))
                    if shooter is bot and applied and target.owner is pilot.controlled_mech:
                        replacement_hits.append((target.entity, protection))
                    return applied

                cast_at = 56 if name == 'Sniper' else 0
                with patch.object(fight, '_damage', observe):
                    for frame in range(425):
                        if frame == engage_at:
                            npc_control.engage(match, bot, pilot)
                        run.step({pilot: (0, 0, 32)} if frame == cast_at else {})
                        if name == 'Assassin' and 65 <= frame < 78:
                            target = fight.player_target(pilot)
                            self.assertIs(target.owner, pilot)
                            self.assertEqual(defenses.protection(pilot), 'immune')
                            sight = assassin.contact_sample(fight, bot, target)
                            if sight is not None and sight[2]:
                                visible_pin_samples += 1
                            self.assertFalse(bot.attack_input)
                            self.assertFalse(fight.pins)
                        if name == 'Assassin' and frame == 78:
                            bot.position = (x, y, z - 6)
                        replacement = pilot.controlled_mech
                        if replacement is not None and defenses.protection(pilot) == 'immune':
                            protected_handoff = True
                            self.assertIsNone(defenses.protection(replacement))
                        if frame == 200:
                            npc_control.stop(match)
                            target = fight.player_target(pilot)
                            self.assertIsNotNone(replacement)
                            self.assertIs(target.owner, replacement)
                            self.assertIsNot(replacement, old)
                            tx, ty, tz = target.feet
                            bot.position = (tx, ty, tz - distance)
                            self.assertFalse(bot.dead)
                        if frame == 203:
                            npc_control.engage(match, bot, pilot)

                self.assertTrue(protected_handoff)
                self.assertEqual(lifecycle.state(99).counter, 1)
                self.assertEqual(health.total, 150)
                if raw_damage is None:
                    self.assertFalse(pilot_packets)
                    self.assertGreater(visible_pin_samples, 0)
                    self.assertEqual(fight.pins.get(bot.entity), replacement.entity)
                else:
                    self.assertTrue(pilot_packets)
                    self.assertEqual(set(pilot_packets), {(raw_damage, 'immune', False)})
                    if name == 'Enforcer':
                        self.assertEqual(len(pilot_packets), 25)
                self.assertTrue(replacement_hits)
                self.assertEqual(set(replacement_hits), {(replacement.entity, None)})
                self.assertIsNone(bot.body_script.component.fault)

    def test_actual_trooper_and_heavy_melee_hits_both_dva_forms_through_matrix(self):
        from test_game_dva_created_body import CreatedBodyTests

        for name, amount in (("Trooper", 30), ("Heavy Assault", 80)):
            for pilot_form in (False, True):
                with self.subTest(npc=name, pilot_form=pilot_form):
                    run, pilot, other, _, mech = CreatedBodyTests().scenario()
                    other.spawned = False
                    fight = run.match.combat
                    if pilot_form:
                        dva_pilot_input(pilot)
                        pilot.mover.position[0] += 10
                        pilot.mover.body = None
                    for _ in range(100):
                        run.step({})
                    target = fight.player_target(pilot)
                    health = fight.vitals_of(target)
                    x, y, z = target.feet
                    bot = npc.spawn(run.match, self.by_name[name], (x, y, z + 1.15), team=1)
                    matrix = None
                    if not pilot_form:
                        instances = mech.body_script.component.instances.values()
                        matrix = next(i for i in instances if i.graph.index == 0x2CC)
                        for _ in range(5):
                            run.step({pilot: (0, 0, 2)})
                        self.assertTrue(matrix.state(3).active)
                    npc_control.engage(run.match, bot, pilot, melee=True)
                    before = health.total
                    for _ in range(80):
                        run.step({pilot: (0, 0, 0 if pilot_form else 2)})
                        if health.total < before:
                            break
                    self.assertEqual(health.total, before - amount + (0 if pilot_form else 5))
                    self.assertIn(npc_control.MELEE_BUTTON, bot.body_script.component.held)
                    if matrix is not None:
                        self.assertTrue(matrix.state(3).active)
                    self.assertFalse(any(rocket.shooter is bot for rocket in fight.rockets))
                    npc_control.stop(run.match)
                    after = health.total
                    for _ in range(15):
                        run.step({})
                    self.assertEqual(health.total, after)
                    self.assertNotIn(npc_control.MELEE_BUTTON, bot.body_script.component.held)

    def test_trooper_melee_loses_contact_when_cover_appears_after_windup(self):
        run, pilot, bot, health = self.scene(False, "Trooper", distance=1.15)
        match = run.match
        instance = next(i for i in bot.body_script.component.instances.values() if i.graph.index == 0x1422)
        npc_control.engage(match, bot, pilot, melee=True)
        before = health.total
        for _ in range(30):
            run.step({})
            if instance.state(4).counter:
                break
        self.assertGreater(instance.state(4).counter, 0)
        self.assertEqual(health.total, before)
        match.collision_world = World(FLOOR + wall(-10, 0.55, 10, 0.55))
        for _ in range(70):
            run.step({})
        self.assertEqual(health.total, before)
        self.assertNotIn(npc_control.MELEE_BUTTON, bot.body_script.component.held)

    def test_stop_during_trooper_melee_windup_prevents_late_damage(self):
        run, pilot, bot, health = self.scene(False, "Trooper", distance=1.15)
        instance = next(i for i in bot.body_script.component.instances.values() if i.graph.index == 0x1422)
        npc_control.engage(run.match, bot, pilot, melee=True)
        before = health.total
        for _ in range(30):
            run.step({})
            if instance.state(4).counter:
                break
        self.assertGreater(instance.state(4).counter, 0)
        npc_control.stop(run.match)
        for _ in range(70):
            run.step({})
        self.assertEqual(health.total, before)
        self.assertNotIn(npc_control.MELEE_BUTTON, bot.body_script.component.held)

    def test_controlled_mech_matrix_handles_manual_npc_guns_and_assassin_pin(self):
        from test_game_dva_created_body import CreatedBodyTests

        leads = {'Trooper': 18, 'Enforcer': 50, 'Sniper': 108, 'Assassin': 0, 'Heavy Assault': 20}
        for name, lead in leads.items():
            with self.subTest(npc=name):
                run, pilot, _, _, mech = CreatedBodyTests().scenario()
                match = run.match
                fight = match.combat
                x, y, z = mech.position
                distance = 2 if name == 'Assassin' else 6
                bot = npc.spawn(match, self.by_name[name], (x, y, z - distance), team=1)
                vitals = fight.vitals_of(fight.player_target(pilot))
                for _ in range(100):
                    run.step({})
                if name == 'Assassin':
                    run.step({pilot: (-32768, 0, 2)})
                npc_control.engage(match, bot, pilot)
                with patch.object(fight, "_shoot", wraps=fight._shoot) as shoot:
                    for frame in range(lead + 30):
                        run.step({pilot: (-32768, 0, 2 if frame >= lead else 0)})
                    if name == 'Assassin':
                        self.assertLess(vitals.total, 600)
                        self.assertEqual(fight.pins, {bot.entity: mech.entity})
                        host = fight.assassin_effects[pilot.entity].component
                        self.assertEqual(host.entity, pilot.entity)
                        self.assertIs(host.entity_var(14695).value(), True)
                        self.assertIsNone(mech.body_script.component.entity_var(14695, create=False))
                    else:
                        self.assertTrue(any(call.args[0] is bot for call in shoot.call_args_list))
                        self.assertEqual(vitals.total, 600)

    def test_manual_pitch_is_shared_by_combat_spatial_query_and_movement_record(self):
        run, player, bot, _ = self.scene(True, 'Enforcer')
        npc_control.engage(run.match, bot, player)
        run.step({})
        self.assertNotEqual(bot.pitch, 0)
        instance = next(iter(player.body_script.component.instances.values()))
        look = instance.evaluate({'$': 'STU_2EDEE064', 'm_entity': expr.Entity(bot.entity)})
        expected = combat.aim(round(bot.yaw), round(bot.pitch))
        for native, model in zip(look, expected, strict=True):
            self.assertAlmostEqual(native, model, places=6)
        self.assertEqual(bot.movement(run.frame).pitch, round(bot.pitch))

    def test_stop_releases_input_but_keeps_an_already_launched_sniper_projectile(self):
        run, player, bot, health = self.scene(True, 'Sniper')
        npc_control.engage(run.match, bot, player)
        fight = run.match.combat
        for _ in range(180):
            run.step({})
            if any(rocket.shooter is bot for rocket in fight.rockets):
                break
        self.assertTrue(any(rocket.shooter is bot for rocket in fight.rockets))
        self.assertEqual(health.total, 600)
        npc_control.stop(run.match)
        self.assertTrue(any(rocket.shooter is bot for rocket in fight.rockets))
        self.assertEqual(self.first_loss(run, health)[0], 95)
        for _ in range(40):
            run.step({})
        self.assertEqual(health.total, 505)

    def sniper_scene(self, pilot_form, *, defender=None, defender_offset=(0, 8), distance=18):
        from test_game_dva_created_body import CreatedBodyTests
        from test_game_mover import box

        run, pilot, ally, _, mech = CreatedBodyTests().scenario()
        match, fight = run.match, run.match.combat
        match.game_map = replace(match.game_map, free_for_all=False)
        if pilot_form:
            dva_pilot_input(pilot)
            pilot.mover.position[0] += 10
            pilot.mover.body = None
        target = fight.player_target(pilot)
        x, y, z = target.feet
        floor = box(x - 25, x + 25, y - 1, y, z - 25, z + distance + 15)
        match.collision_world = World(floor)
        if defender is not None:
            ally.team = pilot.team
            self.assertTrue(match.switch_hero(ally, defender))
            side, forward = defender_offset
            ally.mover.position[:] = (x + side, y, z + forward)
            ally.mover.body = None
        else:
            ally.spawned = False
        for _ in range(35):
            run.step({})
        self.assertIs(target.owner, pilot if pilot_form else mech)
        sniper = npc.spawn(match, self.by_name["Sniper"], (x, y, z + distance), team=1)
        return run, pilot, ally, target, sniper, floor

    def until_sniper_flight(self, run, sniper, commands=None):
        fight = run.match.combat
        for _ in range(170):
            run.step(commands or {})
            shots = [rocket for rocket in fight.rockets if rocket.shooter is sniper]
            if shots:
                rocket, = shots
                self.assertEqual(rocket.definition, 0x0400000000001E80)
                self.assertEqual(rocket.speed, 200)
                self.assertEqual(rocket.left, .5)
                self.assertEqual(rocket.direct.amount, 100)
                self.assertEqual(rocket.direct.critical, 1)
                return rocket
        self.fail("actual Sniper graph did not emit a round")

    def test_actual_sniper_deflect_preserves_flight_time_and_protects_either_dva_form(self):
        for pilot_form in (False, True):
            with self.subTest(pilot_form=pilot_form):
                run, pilot, genji, target, sniper, _ = self.sniper_scene(
                    pilot_form, defender=find_hero("genji"),
                )
                fight = run.match.combat
                health = fight.vitals_of(target)
                source_health = fight.vitals[sniper.entity]
                before, source_before = health.total, source_health.total
                npc_control.engage(run.match, sniper, pilot)
                for _ in range(100):
                    run.step({})
                guard = {genji: (0, 0, 16)}
                rocket = self.until_sniper_flight(run, sniper, guard)
                npc_control.stop(run.match)
                for _ in range(18):
                    run.step(guard)
                    if rocket.shooter is genji:
                        break
                self.assertIs(rocket.shooter, genji)
                self.assertIn(rocket, fight.rockets)
                self.assertAlmostEqual(rocket.left + rocket.flown, .5, places=8)
                deflect = next(i for i in genji.body_script.component.instances.values()
                               if i.graph.index == 0x03A3)
                self.assertEqual(deflect.vars[6584].value(), 100)
                self.assertEqual(deflect.vars[41].value(), expr.Entity(sniper.entity))
                for _ in range(12):
                    run.step({})
                self.assertNotIn(rocket, fight.rockets)
                self.assertEqual(health.total, before)
                self.assertEqual(source_before - source_health.total, 100)
                hits = genji.client.messages(combat.HIT)
                self.assertEqual([row["+0x78"]["+0x0"]["+0x0"] for row in hits], [sniper.entity])

    def test_actual_sniper_cover_blocks_input_and_then_intercepts_emitted_round(self):
        from test_game_mover import box

        for pilot_form in (False, True):
            with self.subTest(pilot_form=pilot_form):
                run, pilot, _, target, sniper, floor = self.sniper_scene(pilot_form)
                match, fight = run.match, run.match.combat
                health = fight.vitals_of(target)
                before = health.total
                x, y, z = target.feet
                open_world = match.collision_world
                cover = World(floor + box(x - 6, x + 6, y, y + 5, z + 9, z + 9.2))
                match.collision_world = cover
                npc_control.engage(match, sniper, pilot)
                with patch.object(fight, "_shoot", wraps=fight._shoot) as shoot:
                    for _ in range(145):
                        run.step({})
                self.assertFalse(any(call.args[0] is sniper for call in shoot.call_args_list))
                self.assertEqual(health.total, before)
                match.collision_world = open_world
                rocket = self.until_sniper_flight(run, sniper)
                match.collision_world = cover
                npc_control.stop(match)
                for _ in range(12):
                    run.step({})
                self.assertNotIn(rocket, fight.rockets)
                self.assertEqual(health.total, before)
                match.collision_world = open_world
                npc_control.engage(match, sniper, pilot)
                self.until_sniper_flight(run, sniper)
                npc_control.stop(match)
                for _ in range(12):
                    run.step({})
                self.assertEqual(before - health.total, 100 if pilot_form else 95)

    def test_actual_sniper_round_in_flight_can_be_intercepted_by_controlled_matrix(self):
        run, pilot, _, target, sniper, _ = self.sniper_scene(False)
        match, fight = run.match, run.match.combat
        health = fight.vitals_of(target)
        matrix = next(i for i in target.owner.body_script.component.instances.values()
                      if i.graph.index == 0x2CC)
        npc_control.engage(match, sniper, pilot)
        rocket = self.until_sniper_flight(run, sniper)
        sniper.team = pilot.team
        npc_control.stop(match)
        with patch.object(fight, "_landed", wraps=fight._landed) as landed:
            for _ in range(12):
                run.step({pilot: (0, 0, 2)})
        self.assertTrue(matrix.state(3).active)
        self.assertNotIn(rocket, fight.rockets)
        self.assertEqual(health.total, 600)
        landed.assert_not_called()

    def test_actual_sniper_launch_team_survives_self_destruct_handoff(self):
        from ow174.game.script.driver import ULT_CHARGE, ULT_COST

        run, pilot, _, target, sniper, _ = self.sniper_scene(False)
        match, fight = run.match, run.match.combat
        mech = target.owner
        mech_health = fight.vitals_of(target)
        npc_control.engage(match, sniper, pilot)
        rocket = self.until_sniper_flight(run, sniper)
        launch_team = rocket.team
        sniper.team = pilot.team
        self.assertNotEqual(launch_team, sniper.team)
        component = mech.body_script.component
        component.set(ULT_CHARGE, component.entity_var(ULT_COST).value())
        run.step({pilot: (0, 0, 32)})
        run.step({})
        self.assertTrue(mech.detached_bomb)
        self.assertIsNone(pilot.controlled_mech)
        self.assertIn(rocket, fight.rockets)
        body = fight.player_target(pilot)
        self.assertEqual(body.entity, pilot.body)
        health = fight.vitals_of(body)
        self.assertEqual(health.total, 150)
        for _ in range(15):
            run.step({})
        self.assertNotIn(rocket, fight.rockets)
        self.assertEqual(rocket.team, launch_team)
        self.assertEqual(health.total, 50)
        self.assertEqual(mech_health.total, 600)
        self.assertFalse(sniper.attack_input)
        self.assertEqual(sniper.attack_target, pilot.entity)
        self.assertFalse(any(shot.shooter is sniper for shot in fight.rockets))

    def test_actual_sniper_return_uses_genji_team_after_original_shooter_changes_team(self):
        run, pilot, genji, target, sniper, _ = self.sniper_scene(False, defender=find_hero("genji"))
        fight = run.match.combat
        health = fight.vitals_of(target)
        source_health = fight.vitals[sniper.entity]
        before = source_health.total
        npc_control.engage(run.match, sniper, pilot)
        for _ in range(100):
            run.step({})
        guard = {genji: (0, 0, 16)}
        rocket = self.until_sniper_flight(run, sniper, guard)
        sniper.team = pilot.team
        npc_control.stop(run.match)
        for _ in range(40):
            run.step(guard)
        self.assertIs(rocket.shooter, genji)
        self.assertEqual(rocket.team, genji.team)
        self.assertAlmostEqual(rocket.flown, .5, places=8)
        self.assertEqual(health.total, 600)
        self.assertEqual(source_health.total, before)
        self.assertNotIn(rocket, fight.rockets)

    def test_actual_projected_barrier_uses_sniper_launch_team_on_either_form(self):
        for pilot_form in (False, True):
            with self.subTest(pilot_form=pilot_form):
                run, pilot, projector, target, sniper, _ = self.sniper_scene(
                    pilot_form, defender=find_hero("zarya"), defender_offset=(3, 0),
                )
                match, fight = run.match, run.match.combat
                health = fight.vitals_of(target)
                before = health.total
                npc_control.engage(match, sniper, pilot)
                rocket = self.until_sniper_flight(run, sniper)
                sniper.team = pilot.team
                npc_control.stop(match)
                a, b = fight.player_target(pilot).segment()
                centre = tuple((left + right) * .5 for left, right in zip(a, b, strict=True))
                angles = aim(fight._eye(projector), centre)
                run.step({projector: (*angles, 16)})
                bubble = fight.zarya_bubbles.for_target(target.owner)
                self.assertIsNotNone(bubble)
                self.assertEqual(bubble.health, 200)
                for _ in range(12):
                    run.step({})
                self.assertIs(fight.zarya_bubbles.for_target(target.owner), bubble)
                self.assertEqual(bubble.health, 100)
                self.assertEqual(health.total, before)
                self.assertNotIn(rocket, fight.rockets)

    def test_actual_reinhardt_barrier_uses_sniper_launch_team(self):
        run, pilot, defender, target, sniper, _ = self.sniper_scene(
            False, defender=find_hero("reinhardt"),
        )
        match, fight = run.match, run.match.combat
        npc_control.engage(match, sniper, pilot)
        for _ in range(90):
            run.step({})
        guard = {defender: (0, 0, 2)}
        rocket = self.until_sniper_flight(run, sniper, guard)
        barrier = reinhardt_barrier.active(defender)
        self.assertIsNotNone(barrier)
        self.assertEqual(barrier.vars[586].value(), 1600)
        sniper.team = pilot.team
        npc_control.stop(match)
        for _ in range(15):
            run.step(guard)
        self.assertEqual(barrier.vars[586].value(), 1500)
        self.assertNotIn(rocket, fight.rockets)
        self.assertEqual(fight.vitals_of(target).total, 600)
        self.assertEqual(fight.vitals_of(fight.player_target(defender)).total, 500)

    def test_actual_sigma_grasp_uses_sniper_launch_team(self):
        run, pilot, sigma, target, sniper, _ = self.sniper_scene(
            True, defender=find_hero("sigma"),
        )
        match, fight = run.match, run.match.combat
        npc_control.engage(match, sniper, pilot)
        for _ in range(100):
            run.step({})
        guard = {sigma: (0, 0, 8)}
        rocket = self.until_sniper_flight(run, sniper, guard)
        grasp = next(i for i in sigma.body_script.component.instances.values()
                     if i.graph.index == sigma_grasp.GRASP)
        self.assertTrue(grasp.state(1).active)
        sniper.team = pilot.team
        npc_control.stop(match)
        for _ in range(145):
            run.step({})
        self.assertNotIn(rocket, fight.rockets)
        self.assertEqual(fight.vitals_of(target).total, 150)
        self.assertEqual(grasp.vars[21312].value(), 100)
        health = fight.vitals_of(fight.player_target(sigma))
        self.assertAlmostEqual(health.temporary_shields[sigma_grasp.SHIELD_TAG].now, 60, places=5)

    def test_actual_light_gun_launch_team_preserves_enemy_hit_and_charge(self):
        from ow174.game.script.driver import ULT_CHARGE

        run, pilot, _, _, sniper, _ = self.sniper_scene(True)
        fight = run.match.combat
        script = pilot.body_script
        script.ult_seconds = 0
        script.set_ult_charge(0)
        health = fight.vitals[sniper.entity]
        before = health.total
        x, y, z = sniper.position
        angles = aim(fight._eye(pilot), (x, y + .8, z))
        run.step({pilot: (*angles, 1)})
        rocket, = fight.rockets
        self.assertIs(rocket.shooter, pilot)
        pilot.team = sniper.team
        self.assertNotEqual(rocket.team, pilot.team)
        for _ in range(50):
            run.step({})
        self.assertNotIn(rocket, fight.rockets)
        self.assertEqual(before - health.total, 14)
        self.assertEqual(script.component.entity_var(ULT_CHARGE).value(), 14)

    def test_actual_flashbang_interrupts_sniper_without_cancelling_its_airborne_round(self):
        from ow174.game.content import heroes

        for pilot_form in (False, True):
            with self.subTest(pilot_form=pilot_form):
                run, pilot, ally, target, sniper, _ = self.sniper_scene(
                    pilot_form, defender=heroes()[0x02E0000000000042], defender_offset=(3, 85), distance=85,
                )
                fight = run.match.combat
                health = fight.vitals_of(target)
                before = health.total
                npc_control.engage(run.match, sniper, pilot)
                rocket = self.until_sniper_flight(run, sniper)
                x, y, z = sniper.position
                angles = aim(fight._eye(ally), (x, y + 1.2, z))
                run.step({ally: (*angles, 16)})
                for _ in range(24):
                    if fight.is_stunned(sniper):
                        break
                    run.step({})
                self.assertTrue(fight.is_stunned(sniper))
                self.assertIn(rocket, fight.rockets)
                self.assertEqual(health.total, before)
                deadline = fight.stuns[sniper.entity]
                with patch.object(fight, "_shoot", wraps=fight._shoot) as shoot:
                    while run.frame < deadline - 1:
                        run.step({})
                self.assertFalse(any(call.args[0] is sniper for call in shoot.call_args_list))
                self.assertNotIn(rocket, fight.rockets)
                self.assertEqual(before - health.total, 100 if pilot_form else 95)

    def test_actual_sniper_round_expires_at_authored_range_against_either_form(self):
        from test_game_mover import box

        for pilot_form in (False, True):
            for in_range in (False, True):
                with self.subTest(pilot_form=pilot_form, in_range=in_range):
                    run, pilot, _, target, sniper, _ = self.sniper_scene(pilot_form)
                    match, fight = run.match, run.match.combat
                    health = fight.vitals_of(target)
                    before = health.total
                    npc_control.engage(match, sniper, pilot)
                    rocket = self.until_sniper_flight(run, sniper)
                    npc_control.stop(match)
                    x, y, z = rocket.position
                    # Keep the full flight clear and move the fixture after launch
                    # so the manual input range cannot decide this case.
                    match.collision_world = World(box(x - 120, x + 120, y - 22, y - 20, z - 120, z + 120))
                    distance = 99 if in_range else 102
                    cx, cy, cz = (
                        origin + distance * direction
                        for origin, direction in zip(rocket.position, rocket.direction, strict=True)
                    )
                    position = (cx, cy - target.height * .5, cz)
                    mover = pilot.mover if pilot_form else target.owner.control_mover
                    mover.position[:] = position
                    if not pilot_form:
                        target.owner.position = position
                    for _ in range(35):
                        run.frame += 1
                        match.update(run.frame * combat.TICK, run.frame, frames=False)
                        if rocket not in fight.rockets:
                            break
                    self.assertNotIn(rocket, fight.rockets)
                    if in_range:
                        self.assertLess(rocket.flown, .5)
                        self.assertEqual(before - health.total, 100 if pilot_form else 95)
                    else:
                        self.assertAlmostEqual(rocket.flown, .5, places=8)
                        self.assertEqual(health.total, before)

    def test_pin_denies_both_form_control_and_stop_restores_real_weapon_input(self):
        for mech in (False, True):
            with self.subTest(mech=mech):
                run, player, bot, health = self.scene(mech, 'Assassin')
                npc_control.engage(run.match, bot, player)
                self.assertGreater(self.first_loss(run, health)[0], 0)
                fight = run.match.combat
                starting = tuple(player.mover.position)
                source_health = fight.vitals[bot.entity]
                source_before = source_health.total
                for _ in range(8):
                    self.command(run, player, buttons=1 | 2 | 8 | 16 | 0x800, forward=127)
                    self.assertTrue(fight.is_stunned(player))
                    self.assertEqual(tuple(player.mover.position[0::2]), starting[0::2])
                    self.assertFalse(player.body_script.component.held)
                    self.assertEqual(source_health.total, source_before)
                self.assertEqual(npc_control.stop(run.match), 1)
                self.assertFalse(fight.pins)
                before = health.total
                self.command(run, player)
                for _ in range(20):
                    self.command(run, player, buttons=1, forward=127, target=bot)
                self.assertFalse(fight.is_stunned(player))
                self.assertLess(source_health.total, source_before)
                self.assertEqual(health.total, before)
                self.assertNotEqual(tuple(player.mover.position[0::2]), starting[0::2])

    def test_delayed_assassin_input_stays_blocked_until_both_sources_release(self):
        from test_game_dva_created_body import CreatedBodyTests

        for pilot_form in (False, True):
            with self.subTest(pilot_form=pilot_form):
                run, pilot, other, _, _ = CreatedBodyTests().scenario()
                match, fight = run.match, run.match.combat
                other.spawned = False
                if pilot_form:
                    dva_pilot_input(pilot)
                    pilot.mover.position[0] += 10
                    pilot.mover.body = None
                for _ in range(100):
                    run.step({})
                target = fight.player_target(pilot)
                x, y, z = target.feet
                first, second = [
                    npc.spawn(match, self.by_name["Assassin"], (x + dx, y, z + 2), team=1)
                    for dx in (-.5, .5)
                ]
                npc_control.engage(match, first, pilot)
                run.step({})
                self.assertEqual(fight.pins, {first.entity: target.entity})
                lock, = fight.input_locks[target.entity]
                self.assertEqual(lock.effect, combat.ASSASSIN_PIN_LOCK)
                self.assertEqual(lock.start, run.frame)
                self.assertIsNone(lock.end)
                npc_control.engage(match, second, pilot)
                run.step({})
                self.assertEqual(fight.pins, {first.entity: target.entity, second.entity: target.entity})
                self.assertEqual(fight.input_locks[target.entity], [lock])
                for _ in range(3):
                    run.frame += 1
                    match.update(run.frame * combat.TICK, run.frame, frames=False)
                npc.remove_body(match, first)
                self.assertEqual(fight.pins, {second.entity: target.entity})
                self.assertTrue(fight.is_stunned(target.owner))
                self.assertIsNone(lock.end)
                for _ in range(3):
                    run.frame += 1
                    match.update(run.frame * combat.TICK, run.frame, frames=False)
                self.assertEqual(npc_control.stop(match), 1)
                self.assertFalse(fight.pins)
                self.assertFalse(fight.is_stunned(target.owner))
                self.assertEqual(lock.end, run.frame)
                mover = pilot.mover if pilot_form else target.owner.control_mover
                feet = tuple(mover.position)
                x, y, z = second.position
                yaw, pitch = aim(fight._eye(target.owner), (x, y + .8, z))
                with patch.object(fight, "_shoot", wraps=fight._shoot) as shoot:
                    delayed = Command(run.frame - 4, yaw=yaw, pitch=pitch, buttons=1, forward=127)
                    pilot.take_commands([delayed])
                    pilot.take_commands([Command(run.frame - 1, yaw=yaw, pitch=pitch, buttons=1)])
                    self.assertFalse(any(call.args[0] is target.owner for call in shoot.call_args_list))
                    self.assertEqual(tuple(mover.position[0::2]), feet[0::2])
                    pilot.take_commands([Command(run.frame, yaw=yaw, pitch=pitch, buttons=1)])
                    self.assertTrue(mover.last_command.buttons & 1)
                    for _ in range(20):
                        if any(call.args[0] is target.owner for call in shoot.call_args_list):
                            break
                        run.step({pilot: (yaw, pitch, 1)})
                    self.assertTrue(any(call.args[0] is target.owner for call in shoot.call_args_list))

    def test_dva_quick_melee_rescues_pinned_teammate_and_stops_damage_during_recovery(self):
        self.enterContext(patch.object(FakeClient, 'queue_bot_death', create=True))
        self.enterContext(patch.object(FakeClient, 'queue_bot_death_update', create=True))
        for mech in (False, True):
            for allied, lethal in ((False, False), (True, False), (True, True)):
                with self.subTest(mech=mech, allied=allied, lethal=lethal):
                    run, victim, bot, health = self.scene(mech, 'Assassin', teammate=True)
                    rescuer = run.players[1]
                    match = run.match
                    match.game_map = replace(match.game_map, free_for_all=False)
                    bot.team, victim.team, rescuer.team = 2, 0, 0 if allied else 1
                    for player, z in ((victim, 2), (rescuer, -1.5)):
                        self.assertTrue(match.switch_hero(player, find_hero('dva')))
                        if not mech:
                            dva_pilot_input(player)
                        if player.controlled_mech is not None:
                            player.controlled_mech.position = (0, 1, z)
                            player.controlled_mech.control_mover.position[:] = (0, 1, z)
                        else:
                            player.mover.position[:] = (0, 1, z)
                            player.mover.body = None
                    for _ in range(100):
                        run.step({})
                    fight = match.combat
                    victim_body = fight.player_target(victim)
                    rescuer_body = fight.player_target(rescuer)
                    self.assertEqual(victim_body.owner is victim.controlled_mech, mech)
                    self.assertEqual(rescuer_body.owner is rescuer.controlled_mech, mech)
                    health = fight.vitals_of(victim_body)
                    npc_control.engage(match, bot, victim)
                    self.assertGreater(self.first_loss(run, health)[0], 0)
                    self.assertEqual(fight.pins, {bot.entity: victim_body.entity})
                    if lethal:
                        fight.vitals[bot.entity] = combat.Vitals(bot.entity, 5)
                    source_health = fight.vitals[bot.entity]
                    before = source_health.total
                    yaw, pitch = aim(fight._eye(rescuer_body.owner), (0, 2, 0))
                    run.step({rescuer: (yaw, pitch, 0x800)})
                    for _ in range(35):
                        if source_health.total < before:
                            break
                        run.step({rescuer: (yaw, pitch, 0)})
                    self.assertEqual(before - source_health.total, 5 if lethal else 30)
                    if lethal:
                        self.check_lethal_assassin_rescue(run, victim, victim_body, bot, health)
                        continue
                    if not allied:
                        self.assertEqual(fight.pins, {bot.entity: victim_body.entity})
                        self.assertFalse(fight.is_stunned(bot))
                        continue
                    self.assertFalse(fight.pins)
                    self.assertFalse(fight.is_stunned(victim_body.owner))
                    deadline = fight.stuns[bot.entity]
                    self.assertEqual(deadline - fight.tick, 125)
                    pinned_health = health.total
                    yaw, pitch = aim(fight._eye(victim_body.owner), (0, 2, 0))
                    for _ in range(20):
                        run.step({victim: (yaw, pitch, 1)})
                    self.assertLess(source_health.total, before - 30)
                    while run.frame + 1 < deadline:
                        run.step({})
                    self.assertEqual(health.total, pinned_health)
                    self.assertFalse(fight.pins)
                    self.assertGreater(self.first_loss(run, health)[0], 0)
                    self.assertEqual(fight.pins, {bot.entity: victim_body.entity})

    def check_lethal_assassin_rescue(self, run, victim, victim_body, bot, health):
        fight = run.match.combat
        self.assertTrue(fight.vitals[bot.entity].dead)
        self.assertTrue(bot.dead)
        self.assertFalse(fight.pins)
        self.assertFalse(fight.is_stunned(victim_body.owner))
        replacement = npc.spawn(run.match, self.by_name["Trooper"], bot.position, team=1)
        replacement_health = fight.vitals[replacement.entity]
        pinned_health = health.total
        before = replacement_health.total
        x, y, z = replacement.position
        yaw, pitch = aim(fight._eye(victim_body.owner), (x, y + 1, z))
        run.step({victim: (yaw, pitch, 1)})
        for _ in range(25):
            run.step({})
        self.assertLess(replacement_health.total, before)
        self.assertEqual(health.total, pinned_health)
        self.assertFalse(fight.pins)
        self.assertIs(fight.player_target(victim).owner, victim_body.owner)

    def test_controlled_boosters_release_active_assassin_pin_on_impact(self):
        for mech in (False, True):
            with self.subTest(mech=mech):
                run, victim, bot, _ = self.scene(False, 'Assassin', teammate=True)
                rescuer = run.players[1]
                match = run.match
                match.game_map = replace(match.game_map, free_for_all=False)
                bot.team, victim.team, rescuer.team = 1, 0, 0
                self.assertTrue(match.switch_hero(rescuer, find_hero('dva')))
                if mech:
                    self.assertTrue(match.switch_hero(victim, find_hero('dva')))
                for player, z in ((rescuer, -2), (victim, 2)):
                    if player.controlled_mech is not None:
                        player.controlled_mech.position = (0, 1, z)
                        player.controlled_mech.control_mover.position[:] = (0, 1, z)
                for _ in range(35):
                    run.step({})
                fight = match.combat
                body = fight.player_target(victim)
                self.assertEqual(body.owner is victim.controlled_mech, mech)
                health = fight.vitals_of(body)
                npc_control.engage(match, bot, victim)
                self.assertGreater(self.first_loss(run, health)[0], 0)
                self.assertEqual(fight.pins, {bot.entity: body.entity})
                npc_health = fight.vitals[bot.entity]
                before = npc_health.total
                run.step({rescuer: (0, 0, 8)})
                for _ in range(55):
                    if npc_health.total < before:
                        break
                    run.step({rescuer: (0, 0, 8)})
                self.assertEqual(before - npc_health.total, 10)
                self.assertIsNotNone(bot.impulse_mover)
                self.assertFalse(fight.pins)
                self.assertFalse(fight.is_stunned(body.owner))
                z = bot.position[2]
                saved_health = health.total
                for _ in range(16):
                    x, y, distance = bot.position
                    yaw, pitch = aim(fight._eye(body.owner), (x, y + 1, distance))
                    run.step({victim: (yaw, pitch, 1)})
                self.assertGreater(bot.position[2], z)
                self.assertEqual(health.total, saved_health)
                self.assertLess(npc_health.total, before - 10)

    def test_pin_requires_contact_los_and_enemy_team_then_releases_on_team_change(self):
        for mech in (False, True):
            for mode in ('range', 'wall', 'ally', 'change_team'):
                with self.subTest(mech=mech, mode=mode):
                    run, player, bot, health = self.scene(mech, 'Assassin',
                                                        distance=6 if mode == 'range' else 2)
                    match = run.match
                    if mode in ('ally', 'change_team'):
                        match.game_map = replace(match.game_map, free_for_all=False)
                        if mode == 'ally':
                            bot.team = player.team
                    if mode == 'wall':
                        match.collision_world = World(FLOOR+wall(-10, 1, 10, 1))
                        player.mover.use(world=match.collision_world)
                    if mode == 'ally':
                        with self.assertRaises(ValueError):
                            npc_control.engage(match, bot, player)
                    else:
                        npc_control.engage(match, bot, player)
                    if mode == 'change_team':
                        self.assertGreater(self.first_loss(run, health)[0], 0)
                        bot.team = player.team
                    before = health.total
                    for _ in range(90):
                        run.step({})
                    self.assertEqual(health.total, before)
                    self.assertFalse(match.combat.pins)
                    self.assertFalse(match.combat.assassin_effects)

    def test_source_removal_death_failure_and_target_leave_clear_pin_without_late_damage(self):
        # Run's FakeClient lacks NPC death observer delivery. Keep this a combat/
        # graph lifetime test with explicit transport-result adapters.
        self.enterContext(patch.object(FakeClient, 'queue_bot_death', create=True))
        self.enterContext(patch.object(FakeClient, 'queue_bot_death_update', create=True))
        for mode in ('remove', 'death', 'script_failure', 'leave'):
            with self.subTest(mode=mode):
                run, player, bot, health = self.scene(True, 'Assassin')
                match = run.match
                npc_control.engage(match, bot, player)
                self.assertGreater(self.first_loss(run, health)[0], 0)
                self.assertTrue(match.combat.pins)
                host = match.combat.assassin_effects[player.entity]
                if mode == 'remove':
                    npc.remove_body(match, bot)
                elif mode == 'death':
                    target = next(t for t in match.combat._targets() if t.owner is bot)
                    match.combat._damage(player, target, 10000, False, None, run.frame)
                elif mode == 'leave':
                    match.leave(player)
                    run.players = []  # actual server sends no further commands to a gone client
                else:
                    with (
                        patch.object(bot.body_script, 'advance_live', side_effect=RuntimeError('pin fault')),
                        self.assertLogs('ow174.game', level='ERROR'),
                    ):
                        run.step({})
                    self.assertIsNone(bot.body_script)
                self.assertFalse(match.combat.pins)
                self.assertFalse(match.combat.assassin_effects)
                self.assertFalse(host.component.instances)
                before = health.total
                for _ in range(20):
                    run.step({})
                self.assertEqual(health.total, before)

    def test_body_replacement_retires_old_pin_and_binds_the_new_body(self):
        run, player, bot, old_health = self.scene(True, 'Assassin')
        match = run.match
        npc_control.engage(match, bot, player)
        self.assertGreater(self.first_loss(run, old_health)[0], 0)
        source = next(i for i in bot.body_script.component.instances.values() if i.graph.index == 0x1157)
        effect_target = source.graph.node(45).field("m_targets")
        self.assertNotEqual(player.entity, player.body)
        self.assertEqual(source.evaluate(effect_target), expr.Entity(player.entity))
        self.assertEqual(source.component.world.manual_target, player.body)
        old_body, old_total = player.body, old_health.total
        old_host = match.combat.assassin_effects[player.entity]
        old_effect_id, = old_host.component.instances
        self.assertTrue(match.switch_hero(player, find_hero('dva')))
        self.assertFalse(match.combat.pins)
        self.assertFalse(match.combat.assassin_effects)
        self.assertFalse(old_host.component.instances)
        dva_pilot_input(player)
        player.mover.position = [0, 1, 2]
        player.mover.body = None
        target = next(t for t in match.combat._targets() if t.owner is player)
        new_health = match.combat.vitals_of(target)
        self.assertGreater(self.first_loss(run, new_health)[0], 0)
        self.assertEqual(match.combat.pins, {bot.entity: player.body})
        self.assertNotIn(old_body, match.combat.pins.values())
        self.assertEqual(old_health.total, old_total)
        self.assertEqual(source.evaluate(effect_target), expr.Entity(player.entity))
        self.assertEqual(source.component.world.manual_target, target.entity)
        self.assertNotEqual(target.entity, old_body)
        new_host = match.combat.assassin_effects[player.entity]
        self.assertIsNot(new_host, old_host)
        self.assertEqual(new_host.component.entity, old_host.component.entity)
        new_effect_id, = new_host.component.instances
        self.assertGreater(new_effect_id, old_effect_id)

    def test_pending_old_input_cannot_charge_the_new_pin_target_twice(self):
        run, player, bot, health = self.scene(True, 'Assassin')
        # A previous native input has been queued but has no controller target.
        bot.body_script.component.edges([(36, True)])
        npc_control.engage(run.match, bot, player)
        self.assertEqual(self.first_loss(run, health)[0], 20)

    def test_matrix_blocks_enforcer_projectiles_but_pin_interrupts_matrix(self):
        for name in ('Enforcer', 'Assassin'):
            with self.subTest(npc=name):
                run, player, bot, health = self.scene(True, name)
                for _ in range(20):
                    self.command(run, player, buttons=2)
                matrix = next(i for i in player.body_script.component.instances.values()
                              if i.graph.index == 0x02CC)
                self.assertTrue(matrix.state(3).active)
                npc_control.engage(run.match, bot, player)
                if name == 'Assassin':
                    self.assertEqual(self.first_loss(run, health)[0], 20)
                    self.assertFalse(matrix.state(3).active)
                    self.assertTrue(run.match.combat.is_stunned(player))
                else:
                    distances = []
                    original = run.match.combat._matrix_hit
                    def intercept(*args, _original=original, _distances=distances):
                        result = _original(*args)
                        _distances.append(result)
                        return result
                    with patch.object(run.match.combat, '_matrix_hit', side_effect=intercept):
                        for _ in range(80):  # actual Enforcer windup first damages at frame68
                            self.command(run, player, buttons=2)
                    self.assertEqual(sum(distance is not None for distance in distances), 25)
                    self.assertEqual(health.total, 600)
                    self.command(run, player)
                    self.assertEqual(self.first_loss(run, health)[0], 18)

    def test_actual_flashbang_releases_pin_and_manual_context_recovers_after_expiry(self):
        fixture = control_tests.NpcControlTests()
        fixture.by_name = self.by_name
        run, caster, bot, health = fixture.scenario('Assassin')
        player = run.players[1]
        player.mover.position = [0, 1, 2]
        player.mover.body = None
        npc_control.engage(run.match, bot, player)
        self.assertGreater(self.first_loss(run, health)[0], 0)
        self.assertTrue(run.match.combat.pins)
        fixture.step(run, caster, bot, 16)
        for _ in range(60):
            if run.match.combat.is_stunned(bot):
                break
            fixture.step(run, caster, bot)
        self.assertTrue(run.match.combat.is_stunned(bot))
        self.assertFalse(run.match.combat.pins)
        before = health.total
        deadline = run.match.combat.stuns[bot.entity]
        while run.frame < deadline-1:
            fixture.step(run, caster, bot)
            self.assertEqual(health.total, before)
            self.assertFalse(run.match.combat.pins)
        self.assertGreater(self.first_loss(run, health)[0], 0)
        self.assertEqual(run.match.combat.pins, {bot.entity: player.body})

    def test_manual_gun_inputs_recover_automatically_after_actual_flashbang(self):
        fixture = control_tests.NpcControlTests()
        fixture.by_name = self.by_name
        for name in ('Trooper', 'Enforcer', 'Sniper', 'Heavy Assault'):
            with self.subTest(npc=name):
                run, caster, bot, health = fixture.scenario(name)
                npc_control.engage(run.match, bot, run.players[1])
                self.assertGreater(self.first_loss(run, health)[0], 0)
                fixture.step(run, caster, bot, 16)
                for _ in range(60):
                    if run.match.combat.is_stunned(bot):
                        break
                    fixture.step(run, caster, bot)
                fight = run.match.combat
                self.assertTrue(fight.is_stunned(bot))
                deadline = fight.stuns[bot.entity]
                with patch.object(fight, '_shoot', wraps=fight._shoot) as emitted:
                    while run.frame < deadline-1:
                        fixture.step(run, caster, bot)
                    self.assertFalse(any(call.args[0] is bot for call in emitted.call_args_list))
                    for _ in range(240):
                        fixture.step(run, caster, bot)
                        if any(call.args[0] is bot for call in emitted.call_args_list):
                            break
                    self.assertTrue(any(call.args[0] is bot for call in emitted.call_args_list))

    def test_authored_pin_damage_delay_clock_duplicate_and_bounded_catchup(self):
        for mech in (False, True):
            totals = []
            for catchup in (False, True):
                with self.subTest(mech=mech, catchup=catchup):
                    run, player, bot, health = self.scene(mech, 'Assassin')
                    npc_control.engage(run.match, bot, player)
                    self.assertEqual(self.first_loss(run, health)[0], 20 if mech else 25)
                    instance = next(i for i in bot.body_script.component.instances.values()
                                    if i.graph.index == 0x1157)
                    delay = instance.state(instance.graph.node(24).state)
                    self.assertEqual(delay.ms, 1000)
                    damage_start = delay.start+delay.ms
                    if catchup:
                        for gap in (31, 49):
                            run.frame += gap
                            run.match.update(run.frame*.016, run.frame)
                            player.take_commands([Command(run.frame)])
                    else:
                        for _ in range(80):
                            run.step({})
                    elapsed = bot.body_script.component.now-damage_start
                    # Authored1157 Wait24=1s, mode1 amount11984=100/s; separate
                    # instant node25 amount13230=25. Per-tick armour halves DoT.
                    ongoing = 100*max(0, elapsed)/1000*(.5 if mech else 1)
                    expected = (600-20 if mech else 150-25)-ongoing
                    totals.append(health.total)
                    self.assertAlmostEqual(health.total, expected, places=4)
                    before = health.total
                    run.match.combat.update(run.frame*.016, run.frame)
                    self.assertEqual(health.total, before)
            self.assertAlmostEqual(totals[0], totals[1], places=5)

    def test_pin_context_clock_and_input_are_causal_passive_server_storage(self):
        fixture = snapshot_tests.ServerSnapshotTests()
        session, _, players = fixture.scene()
        player = players[0]
        match = player.match
        session.apply("switch_hero", {"account": player.account_lo, "hero": find_hero("dva").guid})
        self.assertEqual(player.hero, find_hero("dva"))
        dva_pilot_input(player)
        x, y, z = player.mover.position
        bot = npc.spawn(match, self.by_name['Assassin'], (x, y, z+2), team=1-player.team)
        npc_control.engage(match, bot, player)
        start = match.tick
        for offset in range(1, 82):
            match.update((start+offset)*.016, start+offset)
        self.assertEqual(match.combat.pins, {bot.entity: player.body})
        world = bot.body_script.component.world
        instance = next(i for i in bot.body_script.component.instances.values() if i.graph.index == 0x1157)
        state = instance.state(instance.graph.node(15).state)
        self.assertTrue(state.active)
        with (
            patch.object(runtime.Var, 'value', side_effect=AssertionError('no live variable read')),
            patch.object(runtime.Component, 'run_now', side_effect=AssertionError('no VM execution')),
        ):
            first = snapshot_digest(fixture.capture(session))
            self.assertEqual(snapshot_digest(fixture.capture(session)), first)
            host = match.combat.assassin_effects[player.entity].component
            effect, = host.instances.values()
            linked = effect.node_state(effect.graph.node(9))
            linked.counter += 1
            self.assertNotEqual(snapshot_digest(fixture.capture(session)), first)
            linked.counter -= 1
            state.last_applied += 1
            self.assertNotEqual(snapshot_digest(fixture.capture(session)), first)
            state.last_applied -= 1
            world.manual_target = None
            self.assertNotEqual(snapshot_digest(fixture.capture(session)), first)
            world.manual_target = player.body
            bot.attack_input = not bot.attack_input
            self.assertNotEqual(snapshot_digest(fixture.capture(session)), first)
            bot.attack_input = not bot.attack_input
            match.combat.pins.clear()
            self.assertNotEqual(snapshot_digest(fixture.capture(session)), first)


if __name__ == '__main__':
    unittest.main()
