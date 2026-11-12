"""Actual1157 manual input versus Zen; bridge policies remain reconstructed."""

import math
import os
import unittest
from unittest.mock import patch

from test_game_combat import aim, in_world

from ow174.game import assassin, combat, mei_frost, npc, npc_control
from ow174.game.barriers import PROJECTED, ZARYA
from ow174.game.body_vm_snapshot import export_component_vm, snapshot_digest
from ow174.game.collision import World
from ow174.game.commands import FIRE, SECONDARY, Command
from ow174.game.content import heroes
from ow174.game.mover import mover_data
from ow174.game.script import expr, message_receive, modifiers, runtime


@unittest.skipUnless(os.environ.get('ORPHEUS_NPC_DATA'), 'requires prepared external NPC data')
class ZenyattaAssassinTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        from test_zenyatta_npcs import ZenyattaNpcTests

        cls.fixture_class = ZenyattaNpcTests
        cls.fixture_class.setUpClass()

    @classmethod
    def tearDownClass(cls):
        cls.fixture_class.tearDownClass()

    def arena(self, distance=2, *, helper=False):
        fixture = self.fixture_class()
        names = ('Zen', 'Helper') if helper else ('Zen',)
        run, zen, actor, _ = fixture.arena(self.fixture_class.catalog['4'], names=names)
        x, y, z = zen.mover.position
        actor.position = (x, y, z+distance)
        if helper:
            support = run.players[1]
            support.team = zen.team
            support.mover.position = [x+2, y, z+2]
            support.mover.body = None
        target = next(t for t in run.match.combat._targets() if t.owner is zen)
        return run, zen, actor, run.match.combat.vitals_of(target)

    def state(self, actor, position):
        instance = next(i for i in actor.body_script.component.instances.values() if i.graph.index == 0x1157)
        return instance.states.get(instance.graph.node(position).state)

    def test_actual_09c6_receiver_hosts_direction_record_and_cleanup(self):
        for payload in ("authored_forward", "missing", "wrong_type"):
            with self.subTest(payload=payload):
                run, zen, actor, _ = self.arena()
                self.engage(run, zen, actor)
                yaw, pitch = 8192, -4096
                run.step({zen: (yaw, pitch, 0)})
                component = actor.body_script.component
                receiver = next(i for i in component.instances.values() if i.graph.index == 0x13C3)
                component.strict = True
                vector = expr.Vec3(0, 0, 0)
                if payload == "authored_forward":
                    receiver.write((expr.ENTITY, 13372), expr.Entity(zen.body))
                    receiver.run_node(receiver.graph.node(110), None)
                    receiver.drain()
                    vector = expr.Vec3(*(expr.f32(axis) for axis in combat.aim(yaw, pitch)))
                    self.assertEqual(receiver.find_var(expr.INSTANCE, 14567).value(), vector)
                else:
                    receiver.write((expr.INSTANCE, 14567), None if payload == "missing" else 1.0)
                sender = 0xC0001F01
                packet = message_receive.Packet(0x02400000000009C6, sender, sender)
                self.assertEqual(message_receive.deliver(component, packet), 2)
                component.strict = False
                receiver.drain()
                record = receiver.states[receiver.graph.node(154).state]
                self.assertTrue(record.active)
                self.assertEqual(record.counter, 1)
                key = (receiver.id, record.index)
                records = component.world.assassin_direction_records
                self.assertEqual(records, {key: (3, 30, 0, vector)})
                if payload == "authored_forward":
                    digest = snapshot_digest(export_component_vm(component))
                    records[key] = (3, 30, 0, expr.Vec3(0, 0, 0))
                    self.assertNotEqual(snapshot_digest(export_component_vm(component)), digest)
                    records[key] = (3, 30, 0, vector)
                if payload == "missing":
                    component.destroy(receiver)
                else:
                    record.end(False)
                    record.end(False)
                self.assertFalse(records)

    def test_strict_09c6_hosts_authored_106_modifier_windows(self):
        run, zen, actor, _ = self.arena()
        self.engage(run, zen, actor)
        run.step({})
        component = actor.body_script.component
        receiver = next(i for i in component.instances.values() if i.graph.index == 0x13C3)
        component.strict = True
        sender = 0xC0001F01
        self.assertEqual(message_receive.deliver(
            component, message_receive.Packet(0x02400000000009C6, sender, sender),
        ), 2)
        receiver.drain()
        state = receiver.states[receiver.graph.node(106).state]
        direction = receiver.states[receiver.graph.node(154).state]
        self.assertTrue(state.active)
        self.assertTrue(direction.active)
        self.assertIsNone(component.fault)
        backend = component.world.assassin_numeric_modifiers
        source_frame = component.frame
        start = source_frame + 1
        self.assertEqual(backend.source_frame, source_frame)
        self.assertEqual(len(backend.records), 8)
        self.assertEqual({row.field: (row.operation, row.value, row.priority) for row in backend.records}, {
            17: (1, 0, 99), 18: (1, 0, 99), 19: (1, 0, 99), 21: (1, 0, 99),
            20: (2, 0, 99), 26: (2, 0, 0), 7: (0, 9999, 0), 11: (2, 0, 0),
        })
        self.assertTrue(all(row.start == start and row.end == modifiers.UINT32_MAX for row in backend.records))
        key = modifiers.state_key(receiver.id, state.index, state.counter, 7, 0)
        zero = expr.Vec3(0, 0, 0)
        self.assertEqual(backend.velocity_sets, {key: (start, modifiers.UINT32_MAX, zero)})
        self.assertEqual(backend.resolve(19, 4, frame=source_frame), (False, 4))
        self.assertEqual(backend.resolve(19, 4, frame=start), (True, 0))
        digest = snapshot_digest(export_component_vm(component))
        backend.velocity_sets[key] = (start, modifiers.UINT32_MAX, expr.Vec3(0, 0, 1))
        self.assertNotEqual(snapshot_digest(export_component_vm(component)), digest)
        backend.velocity_sets[key] = (start, modifiers.UINT32_MAX, zero)

        component.strict = False
        run.step({})
        self.assertEqual(component.frame, start)
        self.assertTrue(state.active)
        state.end(False)
        self.assertTrue(all(row.end == start+1 for row in backend.records))
        self.assertEqual(backend.velocity_sets[key], (start, start+1, zero))
        self.assertEqual(backend.resolve(19, 4, frame=start), (True, 0))
        self.assertEqual(backend.resolve(19, 4, frame=start+1), (False, 4))
        with self.assertRaises(runtime.UnsupportedScriptError):
            component.unsupported(receiver, "fixture fault", state.node.cls, state.node)
        self.assertFalse(backend.records)
        self.assertFalse(backend.velocity_sets)
        self.assertFalse(component.world.assassin_direction_records)

    def test_authored_106_zero_velocity_holds_and_releases_routed_assassin(self):
        for omitted in (0, 3):
            with self.subTest(omitted=omitted):
                run, zen, actor, _ = self.arena()
                self.engage(run, zen, actor)
                x, y, z = actor.position
                actor.route = [(x+1.5, y, z)]
                actor.wait = 0
                run.step({})
                self.assertGreater(actor.position[0], x)
                self.assertGreater(actor.velocity[0], 0)
                before = actor.position
                component = actor.body_script.component
                receiver = next(i for i in component.instances.values() if i.graph.index == 0x13C3)
                sender = 0xC0001F01
                self.assertEqual(message_receive.deliver(
                    component, message_receive.Packet(0x02400000000009C6, sender, sender),
                ), 2)
                receiver.drain()
                state = receiver.states[receiver.graph.node(106).state]
                self.assertTrue(state.active)
                self.assertEqual(actor.position, before)
                run.frame += omitted
                run.step({})
                self.assertTrue(state.active)
                self.assertEqual(actor.position, before)
                self.assertEqual(actor.velocity, (0, 0, 0))
                self.assertEqual(actor.held, (0, 0))
                state.end(False)
                run.step({})
                self.assertGreater(actor.position[0], before[0])
                self.assertGreater(actor.velocity[0], 0)
                self.assertNotEqual(actor.held, (0, 0))

    def test_105_natural_release_resumes_route_across_skipped_updates(self):
        endpoints = []
        for omitted in (0, 3):
            with self.subTest(omitted=omitted):
                run, zen, actor, _ = self.arena()
                self.engage(run, zen, actor)
                x, y, z = actor.position
                actor.route = [(x+1.5, y, z)]
                actor.wait = 0
                run.step({})
                held = actor.position
                component = actor.body_script.component
                receiver = next(i for i in component.instances.values() if i.graph.index == 0x13C3)
                sender = 0xC0001F01
                self.assertEqual(message_receive.deliver(
                    component, message_receive.Packet(0x02400000000009C6, sender, sender),
                ), 2)
                receiver.drain()
                start = run.frame
                anim, lock, direction = (receiver.states[receiver.graph.node(pos).state] for pos in (105, 106, 154))
                for _ in range(12):
                    run.step({})
                self.assertEqual(actor.position, held)
                self.assertTrue(lock.active)
                run.frame += omitted
                while run.frame - start < 16:
                    run.step({})
                self.assertFalse(anim.active)
                self.assertFalse(lock.active)
                self.assertEqual(anim.ended_at - anim.start, 200)
                self.assertEqual(lock.ended_at, anim.ended_at)
                self.assertGreater(actor.position[0], held[0])
                endpoints.append(actor.position)
                for _ in range(27):
                    run.step({})
                self.assertTrue(direction.active)
                run.step({})
                self.assertFalse(direction.active)
                self.assertEqual(direction.ended_at - anim.ended_at, 500)
                self.assertFalse(component.world.assassin_direction_records)
        self.assertEqual(len(endpoints), 2)
        self.assertEqual(endpoints[0], endpoints[1])

    def test_authored_13c3_sends_09c6_to_its_live_body(self):
        for mode in ("strict_sender", "wait"):
            with self.subTest(mode=mode):
                run, zen, actor, _ = self.arena()
                self.engage(run, zen, actor)
                run.step({})
                component = actor.body_script.component
                receiver = next(i for i in component.instances.values() if i.graph.index == 0x13C3)
                with patch.object(message_receive, "deliver", wraps=message_receive.deliver) as delivered:
                    if mode == "strict_sender":
                        component.strict = True
                        receiver.run_node(receiver.graph.node(84), None)
                        receiver.drain()
                    else:
                        receiver.write((expr.INSTANCE, 13756), expr.f32(4))
                        receiver.write((expr.ENTITY, 13605), expr.f32(2))
                        wait = receiver.state(receiver.graph.node(10).state)
                        wait.request(runtime.BEGIN)
                        receiver.drain()
                        self.assertTrue(wait.active)
                        self.assertEqual(wait.ms, 2000)
                        deadline_frame = (wait.start + wait.ms) // component.frame_ms
                        while run.frame < deadline_frame - 1:
                            run.step({})
                        delivered.assert_not_called()
                        run.step({})
                        self.assertFalse(wait.active)
                        self.assertEqual(wait.ended_at - wait.start, 2000)
                    delivered.assert_called_once_with(
                        component, message_receive.Packet(0x02400000000009C6, actor.entity, actor.entity),
                    )
                self.assertIsNone(component.fault)
                for pos in (105, 106, 154):
                    self.assertTrue(receiver.states[receiver.graph.node(pos).state].active)

    def engage(self, run, zen, actor, buttons=0):
        x, y, z = actor.position
        angles = aim(run.match.combat._eye(zen), (x, y+mover_data(actor.body).stand_height*.5, z))
        run.step({zen: (*angles, buttons)})
        self.assertEqual(npc_control.start(run.match, zen), actor.entity)

    def start(self, run, zen, actor, buttons=0):
        self.engage(run, zen, actor, buttons)
        for _ in range(80):
            run.step({zen: (0, 0, buttons)})
            if actor.entity in run.match.combat.pins:
                return
        self.fail('actual1157 logical36 did not enter its contact pin')

    def test_actual_contact_damage_delay_continuous_rate_and_pinned_weapon_suppression(self):
        run, zen, actor, vitals = self.arena()
        self.start(run, zen, actor)
        self.assertEqual(run.match.combat.pins[actor.entity], zen.body)
        self.assertEqual(vitals.total, 175)
        self.assertGreater(self.state(actor, 25).counter, 0)
        weapon = next(i for i in zen.body_script.component.instances.values() if i.graph.index == 0x0164)
        zen.body_script.set_ult_charge(zen.body_script.component.entity_var(338).value())
        for _ in range(10):
            run.step({zen: (0, 0, 0x01 | 0x02 | 0x800 | 0x20)})
        self.assertEqual(weapon.vars[53].value(), 20)
        self.assertFalse(run.match.combat.rockets)
        self.assertFalse(run.match.combat.zenyatta.ultimate(zen))
        self.assertEqual(vitals.total, 175)
        for _ in range(80):
            run.step({})
            if (continuous := self.state(actor, 15)) is not None and continuous.active:
                break
        self.assertIsNotNone(self.state(actor, 15))
        self.assertTrue(self.state(actor, 15).active)
        before = vitals.total
        for _ in range(10):
            run.step({})
        self.assertAlmostEqual(before-vitals.total, 16, places=4)

    def test_actual_stop_releases_pin_and_explicit_restart_uses_new_contact_phase(self):
        run, zen, actor, vitals = self.arena()
        self.start(run, zen, actor)
        self.assertEqual(npc_control.stop(run.match), 1)
        self.assertFalse(run.match.combat.pins)
        before = vitals.total
        for _ in range(100):
            run.step({})
        self.assertEqual(vitals.total, before)
        self.start(run, zen, actor)
        self.assertEqual(vitals.total, before-25)
        self.assertEqual(self.state(actor, 25).counter, 2)

    def test_skipped_updates_preserve_authored_assassin_damage_onset(self):
        def trace(*, end=None, skip_after=None):
            run, zen, actor, vitals = self.arena()
            self.start(run, zen, actor)
            contact = run.frame
            self.assertEqual(vitals.total, 175)
            onset = None
            while run.frame - contact < (end if end is not None else 80):
                if run.frame - contact == skip_after:
                    run.frame += 4
                run.step({zen: (0, 0, 0x01)})
                if (state := self.state(actor, 15)) is not None and state.active and onset is None:
                    onset = run.frame - contact
                    if end is None:
                        end = onset + 5
            self.assertIsNotNone(onset)
            state = self.state(actor, 15)
            self.assertTrue(state.active)
            self.assertEqual(state.counter, 1)
            fight = run.match.combat
            self.assertEqual(fight.pins, {actor.entity: zen.body})
            self.assertFalse(fight.rockets)
            self.assertLess(vitals.total, 175)
            sample = (state.start, state.last_applied, vitals.total)
            self.assertEqual(npc_control.stop(run.match), 1)
            self.assertFalse(fight.pins)
            for _ in range(10):
                run.step({})
            self.assertEqual(vitals.total, sample[2])
            return onset, sample

        onset, continuous = trace()
        self.assertEqual(onset, 62)
        skipped_onset, skipped = trace(end=onset+5, skip_after=onset-2)
        self.assertEqual(skipped_onset, 65)
        self.assertEqual(skipped[:2], continuous[:2])
        self.assertEqual(continuous[1]-continuous[0], 87)
        self.assertAlmostEqual(continuous[2], 166.3, places=4)
        self.assertAlmostEqual(skipped[2], continuous[2], places=4)


    def test_manual_reengagement_does_not_wait_for_assassin_release_timer(self):
        for idle_frames in (0, 1, 91, 92, 124, 125, 126):
            with self.subTest(idle_frames=idle_frames):
                run, zen, actor, vitals = self.arena()
                self.start(run, zen, actor)
                fight = run.match.combat
                self.assertEqual(npc_control.stop(run.match), 1)
                self.assertFalse(fight.pins)
                stopped = run.frame
                timer, animation = self.state(actor, 37), self.state(actor, 31)
                self.assertFalse(timer.active)
                self.assertFalse(animation.active)
                for step in range(1, idle_frames+1):
                    run.step({})
                    self.assertEqual(timer.active, step <= 125)
                    self.assertEqual(animation.active, step <= 91)
                    self.assertFalse(fight.pins)
                    self.assertEqual(vitals.total, 175)

                self.start(run, zen, actor)
                self.assertEqual(run.frame, stopped+idle_frames+2)
                self.assertEqual(vitals.total, 150)
                self.assertEqual(self.state(actor, 25).counter, 2)
                self.assertEqual(fight.pins[actor.entity], zen.body)
                self.assertEqual(timer.active, run.frame < stopped+126)
                self.assertFalse(animation.active)

    def test_release_completion_clears_only_the_old_assassin_target(self):
        from unittest.mock import patch

        from ow174.game.script import expr, runtime

        for mode in ("natural", "same", "other"):
            with self.subTest(mode=mode):
                run, zen, actor, old_health = self.arena(helper=True)
                other = run.players[1]
                location = zen.mover.position[:]
                self.assertTrue(run.match.switch_hero(other, zen.hero))
                other.mover.position = [location[0]+4, location[1], location[2]]
                other.mover.body = None
                self.start(run, zen, actor)
                fight = run.match.combat
                component = actor.body_script.component
                instance = self.state(actor, 25).instance
                clear_times = []
                original = runtime.Instance.run_node

                def observe(current, node, *args, **kwargs):
                    if current is instance and node.pos == 50:
                        clear_times.append(component.now)
                    return original(current, node, *args, **kwargs)

                with patch.object(runtime.Instance, "run_node", observe):
                    self.assertEqual(npc_control.stop(run.match), 1)
                    run.step({})
                    animation = self.state(actor, 31)
                    self.assertTrue(animation.active)
                    old_deadline = animation.start + 1467
                    target = zen
                    if mode == "other":
                        target = other
                        zen.mover.position = [location[0]+4, location[1], location[2]]
                        other.mover.position = location[:]
                        zen.mover.body = other.mover.body = None
                    if mode != "natural":
                        self.start(run, target, actor)
                        self.assertEqual(fight.pins[actor.entity], target.body)
                        self.assertFalse(animation.active)

                    for _ in range(105):
                        if component.now > old_deadline+32:
                            break
                        run.step({})
                    self.assertGreater(component.now, old_deadline+32)

                self.assertFalse(animation.active)
                self.assertTrue(self.state(actor, 37).active)
                if mode == "natural":
                    self.assertEqual(clear_times, [old_deadline, old_deadline])
                    self.assertEqual(instance.vars[536].value(), 0)
                    self.assertFalse(fight.pins)
                    self.assertEqual(old_health.total, 175)
                else:
                    clear, = clear_times
                    self.assertLess(clear, old_deadline)
                    self.assertEqual(instance.vars[536].value(), expr.Entity(target.body))
                    self.assertEqual(fight.pins[actor.entity], target.body)
                    self.assertTrue(fight.is_stunned(target))
                    self.assertLess(fight.vitals[target.body].total, 150 if mode == "same" else 175)
                    if mode == "other":
                        self.assertEqual(old_health.total, 175)

    def test_actual_projected_barrier_absorbs_assassin_damage_during_manual_pin(self):
        for cast_before_contact in (True, False):
            with self.subTest(cast_before_contact=cast_before_contact):
                run, zen, actor, vitals = self.arena(helper=True)
                zarya = run.players[1]
                position = list(zarya.mover.position)
                run.match.switch_hero(zarya, heroes()[ZARYA])
                zarya.mover.position = position
                zarya.mover.body = None
                fight = run.match.combat
                x, y, z = zen.mover.position
                angles = aim(fight._eye(zarya), (x, y+1.5, z))
                if cast_before_contact:
                    run.step({zarya: (*angles, 16)})
                self.start(run, zen, actor)
                if not cast_before_contact:
                    run.step({zarya: (*angles, 16)})
                bubble = fight.barriers.bubbles[zarya.body, PROJECTED]
                self.assertEqual(vitals.total, 200 if cast_before_contact else 175)
                self.assertEqual(bubble.hp, 175 if cast_before_contact else 200)
                self.assertEqual(fight.pins[actor.entity], zen.body)
                for _ in range(80):
                    run.step({})
                    continuous = self.state(actor, 15)
                    if continuous is not None and continuous.active:
                        break
                self.assertIsNotNone(continuous)
                self.assertTrue(continuous.active)
                previous_hp, previous_body = bubble.hp, vitals.total
                for _ in range(10):
                    run.step({})
                self.assertAlmostEqual(previous_hp-bubble.hp, 16, places=4)
                self.assertEqual(vitals.total, previous_body)
                self.assertEqual(fight.pins[actor.entity], zen.body)
                for _ in range(130):
                    if (zarya.body, PROJECTED) not in fight.barriers.bubbles:
                        break
                    run.step({})
                self.assertNotIn((zarya.body, PROJECTED), fight.barriers.bubbles)
                self.assertGreater(bubble.hp, 0)
                previous_body = vitals.total
                for _ in range(10):
                    run.step({})
                self.assertAlmostEqual(previous_body-vitals.total, 16, places=4)
                self.assertEqual(fight.pins[actor.entity], zen.body)

    def test_assassin_continuous_damage_breaks_bubble_without_body_overflow(self):
        run, zen, actor, vitals = self.arena(helper=True)
        zarya = run.players[1]
        position = list(zarya.mover.position)
        self.assertTrue(run.match.switch_hero(zarya, heroes()[ZARYA]))
        zarya.mover.position = position
        zarya.mover.body = None
        fight = run.match.combat
        self.start(run, zen, actor)
        self.assertEqual(vitals.total, 175)
        owner = fight.zenyatta.owners[zen.body]
        contact_time = owner.last_damage
        x, y, z = zen.mover.position
        direction = aim(fight._eye(zarya), (x, y+1.5, z))
        run.step({zarya: (*direction, 16)})
        key = (zarya.body, PROJECTED)
        bubble = fight.barriers.bubbles[key]
        self.assertEqual((bubble.target, bubble.hp), (zen.body, 200))
        for _ in range(80):
            run.step({})
            continuous = self.state(actor, 15)
            if continuous is not None and continuous.active:
                break
        else:
            self.fail("Assassin did not reach its continuous damage phase")
        self.assertEqual(owner.last_damage, contact_time)

        bubble.hp = .8  # Isolate a break by one authored 1.6-damage tick.
        run.step({})
        self.assertEqual(bubble.hp, 0)
        self.assertNotIn(key, fight.barriers.bubbles)
        self.assertEqual(vitals.total, 175)
        self.assertEqual(owner.last_damage, contact_time)
        self.assertEqual(fight.pins[actor.entity], zen.body)
        self.assertTrue(fight.is_stunned(zen))

        run.step({})
        self.assertAlmostEqual(vitals.total, 173.4, places=4)
        self.assertEqual(owner.last_damage, fight.now)
        self.assertGreater(owner.last_damage, contact_time)
        self.assertEqual(fight.pins[actor.entity], zen.body)

    def test_actual_zen_melee_releases_a_live_assassin_pin(self):
        run, zen, actor, vitals = self.arena(helper=True)
        rescuer = run.players[1]
        position = list(rescuer.mover.position)
        run.match.switch_hero(rescuer, zen.hero)
        rescuer.mover.position = position
        rescuer.mover.body = None
        self.start(run, zen, actor)
        fight = run.match.combat
        attacker = fight.vitals[actor.entity]
        initial = attacker.total
        x, y, z = actor.position
        angles = aim(fight._eye(rescuer), (x, y+mover_data(actor.body).stand_height*.5, z))
        run.step({rescuer: (*angles, 1)})
        for _ in range(20):
            run.step({rescuer: (*angles, 0)})
        self.assertEqual(attacker.total, initial-48)
        self.assertEqual(fight.pins[actor.entity], zen.body)
        run.step({rescuer: (*angles, 0x800)})
        for _ in range(45):
            if actor.entity not in fight.pins:
                break
            run.step({rescuer: (*angles, 0)})
        self.assertNotIn(actor.entity, fight.pins)
        self.assertFalse(actor.dead)
        self.assertEqual(attacker.total, initial-78)
        self.assertFalse(self.state(actor, 23).active)
        self.assertTrue(self.state(actor, 31).active)
        self.assertTrue(self.state(actor, 37).active)
        before = vitals.total
        freed_aim = aim(fight._eye(zen), (x, y+mover_data(actor.body).stand_height*.5, z))
        run.step({zen: (*freed_aim, 1)})
        for _ in range(25):
            run.step({zen: (*freed_aim, 0)})
        self.assertEqual(attacker.total, initial-126)
        self.assertEqual(len(zen.client.messages(combat.HIT)), 1)
        for _ in range(105):
            run.step({})
        self.assertEqual(vitals.total, before)
        self.assertFalse(self.state(actor, 37).active)

    def test_projected_barrier_blocks_assassin_melee_rescue_until_expiry(self):
        fixture = self.fixture_class()
        run, zen, actor, attacker = fixture.arena(
            fixture.catalog["4"], names=("Zen", "Rescuer", "Zarya"),
        )
        _, rescuer, zarya = run.players
        rescuer.team = zen.team
        zarya.team = actor.team
        self.assertNotEqual(zen.team, actor.team)
        self.assertTrue(run.match.switch_hero(rescuer, zen.hero))
        self.assertTrue(run.match.switch_hero(zarya, heroes()[ZARYA]))
        x, y, z = zen.mover.position
        actor.position = (x, y, z+2)
        rescuer.mover.position = [x+2, y, z+2]
        zarya.mover.position = [x, y, z+7]
        rescuer.mover.body = zarya.mover.body = None
        fight = run.match.combat
        target = (x, y+mover_data(actor.body).stand_height*.5, z+2)
        projection = aim(fight._eye(zarya), target)
        melee = aim(fight._eye(rescuer), target)
        run.step({zarya: (*projection, 0x10)})
        key = (zarya.body, PROJECTED)
        bubble = fight.barriers.bubbles[key]
        self.assertEqual((bubble.target, bubble.hp), (actor.entity, 200))

        self.start(run, zen, actor)
        victim = fight.vitals[zen.body]
        self.assertEqual(victim.total, 175)
        initial = attacker.total
        run.step({rescuer: (*melee, 0x800)})
        for _ in range(45):
            if bubble.hp < 200:
                break
            run.step({rescuer: (*melee, 0)})
        self.assertEqual(bubble.hp, 170)
        self.assertEqual(attacker.total, initial)
        self.assertFalse(rescuer.client.messages(combat.HIT))
        self.assertEqual(fight.pins[actor.entity], zen.body)
        self.assertTrue(fight.is_stunned(zen))

        for _ in range(130):
            if key not in fight.barriers.bubbles:
                break
            run.step({})
        self.assertNotIn(key, fight.barriers.bubbles)
        self.assertEqual(bubble.hp, 170)
        self.assertEqual(fight.pins[actor.entity], zen.body)
        self.assertGreater(victim.total, 0)
        self.assertLess(victim.total, 175)
        run.step({rescuer: (*melee, 0x800)})
        for _ in range(45):
            if actor.entity not in fight.pins:
                break
            run.step({rescuer: (*melee, 0)})
        self.assertNotIn(actor.entity, fight.pins)
        self.assertFalse(fight.is_stunned(zen))
        self.assertFalse(actor.dead)
        self.assertEqual(attacker.total, initial-30)
        hits = rescuer.client.messages(combat.HIT)
        self.assertEqual(len(hits), 1)
        self.assertEqual(hits[0]["+0x78"]["+0x0"]["+0x0"], actor.entity)
        self.assertTrue(self.state(actor, 31).active)
        self.assertTrue(self.state(actor, 37).active)
        remaining = victim.total
        for _ in range(15):
            run.step({})
        self.assertEqual(victim.total, remaining)

    def test_actual_pin_cancels_charged_volley_without_late_orbs_after_release(self):
        run, zen, actor, _ = self.arena(distance=8)
        for _ in range(100):
            run.step({zen: (0, 0, 0x02)})
        weapon = next(i for i in zen.body_script.component.instances.values() if i.graph.index == 0x0164)
        self.assertGreater(weapon.vars[1392].value(), 0)
        ammunition = weapon.vars[53].value()
        x, y, z = zen.mover.position
        actor.position = (x, y, z+2)
        source_health = run.match.combat.vitals[actor.entity].total
        self.start(run, zen, actor, buttons=0x02)
        npc_control.stop(run.match)
        for _ in range(100):
            run.step({})
        self.assertFalse(run.match.combat.rockets)
        self.assertFalse(zen.client.messages(combat.HIT))
        self.assertEqual(run.match.combat.vitals[actor.entity].total, source_health)
        self.assertEqual(weapon.vars[1392].value(), 0)
        self.assertEqual(weapon.vars[53].value(), ammunition)  # canceled charge is not refunded
        x, y, z = actor.position
        angles = aim(run.match.combat._eye(zen), (x, y+mover_data(actor.body).stand_height*.5, z))
        run.step({zen: (*angles, 0x01)})
        for _ in range(20):
            run.step({})
        self.assertLess(run.match.combat.vitals[actor.entity].total, source_health)
        self.assertEqual(weapon.vars[53].value(), ammunition-1)

    def test_actual_pinned_commands_block_movement_but_allow_look_and_release_restores_movement(self):
        run, zen, actor, _ = self.arena()
        self.start(run, zen, actor)
        before = tuple(zen.mover.position)

        def walk():
            run.frame += 1
            run.match.update(run.frame*combat.TICK, run.frame)
            zen.take_commands([Command(run.frame, forward=127, right=127, yaw=1234, pitch=345)])

        for _ in range(20):
            walk()
        self.assertEqual(tuple(zen.mover.position), before)
        self.assertEqual((zen.mover.yaw, zen.mover.pitch), (1234, 345))
        npc_control.stop(run.match)
        for _ in range(20):
            walk()
        self.assertGreater(abs(zen.mover.position[0]-before[0])+abs(zen.mover.position[2]-before[2]), .1)

    def test_actual_allied_harmony_or_transcendence_heals_pinned_zen_without_cleansing_pin(self):
        for ultimate in (False, True):
            with self.subTest(ultimate=ultimate):
                run, zen, actor, vitals = self.arena(helper=True)
                support = run.players[1]
                position = list(support.mover.position)
                run.match.switch_hero(support, zen.hero)
                support.mover.position = position
                support.mover.body = None
                x, y, z = zen.mover.position
                angles = aim(run.match.combat._eye(support), (x, y+combat.EYE, z))
                if ultimate:
                    self.start(run, zen, actor)
                    support.body_script.set_ult_charge(support.body_script.component.entity_var(338).value())
                    run.step({support: (*angles, 0x20)})
                else:
                    run.step({support: (*angles, 0x10)})
                    self.assertEqual(run.match.combat.zenyatta.owners[support.body].harmony.target, zen.body)
                    self.start(run, zen, actor)
                for _ in range(80):
                    run.step({})
                    if (phase := self.state(actor, 15)) is not None and phase.active:
                        break
                self.assertIsNotNone(self.state(actor, 15))
                before = vitals.total
                for _ in range(10):
                    run.step({})
                self.assertAlmostEqual(vitals.total, 200 if ultimate else before-11.2, places=4)
                self.assertTrue(run.match.combat.is_stunned(zen))
                self.assertEqual(run.match.combat.pins[actor.entity], zen.body)

    def test_active_pin_clock_and_target_context_are_causal_vm_state(self):
        from ow174.game.body_vm_snapshot import export_component_vm, snapshot_digest

        run, zen, actor, _ = self.arena()
        self.start(run, zen, actor)
        component = actor.body_script.component
        phase = self.state(actor, 25)
        before = snapshot_digest(export_component_vm(component))
        phase.last_applied += 1
        self.assertNotEqual(before, snapshot_digest(export_component_vm(component)))
        phase.last_applied -= 1
        world = component.world
        world.manual_target += 1
        self.assertNotEqual(before, snapshot_digest(export_component_vm(component)))

    def test_actual_pin_releases_on_range_wall_and_team_loss_without_late_damage(self):
        for reason in ('range', 'wall', 'team'):
            with self.subTest(reason=reason):
                run, zen, actor, vitals = self.arena()
                self.start(run, zen, actor)
                before = vitals.total
                x, y, z = zen.mover.position
                if reason == 'range':
                    actor.position = (x, y, z+4)
                elif reason == 'wall':
                    run.match.collision_world = World([
                        (x-5, y-1, z+1, x+5, y-1, z+1, x+5, y+5, z+1),
                        (x-5, y-1, z+1, x+5, y+5, z+1, x-5, y+5, z+1),
                    ])
                else:
                    actor.team = zen.team
                for _ in range(100):
                    run.step({})
                    self.assertFalse(run.match.combat.pins)
                self.assertEqual(vitals.total, before)
                self.assertFalse(actor.attack_input)

    def test_out_of_contact_manual_engagement_cannot_damage_or_pin_zen(self):
        run, zen, actor, vitals = self.arena(distance=8)
        self.engage(run, zen, actor)
        for _ in range(100):
            run.step({})
        self.assertEqual(vitals.total, 200)
        self.assertFalse(run.match.combat.pins)
        self.assertFalse(actor.attack_input)

    def test_actual_source_flashbang_releases_victim_and_stun_retains_its_own_deadline(self):
        run, zen, actor, vitals = self.arena(helper=True)
        support = run.players[1]
        position = list(support.mover.position)
        run.match.switch_hero(support, heroes()[0x02E0000000000042])
        support.mover.position = position
        support.mover.body = None
        self.start(run, zen, actor)
        x, y, z = actor.position
        angles = aim(run.match.combat._eye(support), (x, y+1, z))
        run.step({support: (*angles, 0x10)})
        for _ in range(80):
            run.step({support: (*angles, 0)})
            if run.match.combat.stuns.get(actor.entity, 0) > run.frame:
                break
        deadline = run.match.combat.stuns.get(actor.entity, 0)
        self.assertGreater(deadline, run.frame)
        self.assertFalse(run.match.combat.pins)
        self.assertFalse(run.match.combat.is_stunned(zen))
        before = vitals.total
        while run.frame < deadline-1:
            run.step({})
            self.assertFalse(run.match.combat.pins)
            self.assertEqual(vitals.total, before)

    def test_actual_sleep_dart_releases_assassin_pin_until_natural_or_damage_wake(self):
        contacts = {}
        for wake_with_orb, omitted in ((False, 0), (False, 3), (True, 0), (True, 3)):
            with self.subTest(wake_with_orb=wake_with_orb, omitted=omitted):
                run, zen, actor, zen_vitals = self.arena(helper=True)
                ana = run.players[1]
                position = list(ana.mover.position)
                self.assertTrue(run.match.switch_hero(ana, heroes()[0x02E000000000013B]))
                ana.mover.position = position
                ana.mover.body = None
                self.start(run, zen, actor)
                fight = run.match.combat
                for _ in range(80):
                    run.step({})
                    continuous = self.state(actor, 15)
                    if continuous is not None and continuous.active:
                        break
                self.assertIsNotNone(continuous)
                self.assertTrue(continuous.active)
                x, y, z = actor.position
                angles = aim(fight._eye(ana), (x, y+1.15, z))
                actor_vitals = fight.vitals[actor.entity]
                initial = actor_vitals.total
                for frame in range(55):
                    run.step({ana: (*angles, 8 if frame == 0 else 0)})
                    if actor.entity in fight.sleeps:
                        break
                start, end, waking = fight.sleeps[actor.entity]
                self.assertEqual(end-start, 313)
                self.assertFalse(waking)
                self.assertEqual(actor_vitals.total, initial-5)
                self.assertFalse(fight.pins)
                self.assertFalse(fight.is_stunned(zen))
                self.assertFalse(continuous.active)
                after_dart = zen_vitals.total

                if wake_with_orb:
                    angles = aim(fight._eye(zen), (x, y+1, z))
                    run.step({zen: (*angles, 1)})
                    for _ in range(16):
                        if actor_vitals.total == initial-53:
                            break
                        run.step({})
                    self.assertEqual(actor_vitals.total, initial-53)
                    self.assertEqual(fight.sleeps[actor.entity], (start, end, False))
                    while run.frame < start+35:
                        run.step({})
                    self.assertEqual(fight.sleeps[actor.entity], (start, end, False))
                    angles = aim(fight._eye(zen), (x, y+1, z))
                    run.step({zen: (*angles, 1)})
                    for _ in range(16):
                        run.step({})
                        if fight.sleeps[actor.entity][2]:
                            break
                    self.assertEqual(actor_vitals.total, initial-101)
                    _, wake_end, waking = fight.sleeps[actor.entity]
                    self.assertTrue(waking)
                    self.assertEqual(wake_end-run.frame, 32)
                    self.assertLess(wake_end, end)
                    end = wake_end
                previous_health = zen_vitals.total
                stop = end-2 if omitted else end-1
                while run.frame < stop:
                    run.step({})
                    self.assertNotIn(actor.entity, fight.pins)
                    self.assertTrue(fight.is_stunned(actor))
                    self.assertGreaterEqual(zen_vitals.total, previous_health)
                    previous_health = zen_vitals.total
                before_repin = after_dart if wake_with_orb else 200
                self.assertEqual(zen_vitals.total, before_repin)
                self.assertNotIn(36, actor.body_script.component.held)
                self.assertFalse(actor.attack_input)
                run.frame += omitted
                run.step({})
                self.assertNotIn(actor.entity, fight.sleeps)
                for _ in range(24):
                    if actor.entity in fight.pins:
                        break
                    run.step({})
                self.assertEqual(fight.pins, {actor.entity: zen.body})
                self.assertAlmostEqual(zen_vitals.total, before_repin-25, places=4)
                self.assertEqual(self.state(actor, 25).counter, 2)
                contacts[wake_with_orb, omitted] = self.state(actor, 25).start
        for wake_with_orb in (False, True):
            self.assertIn((wake_with_orb, 0), contacts)
            self.assertEqual(contacts[wake_with_orb, 0], contacts.get((wake_with_orb, 3)))

    def test_actual_mei_freeze_releases_assassin_pin_until_thaw(self):
        contacts = []
        for omitted in (0, 3):
            with self.subTest(omitted=omitted):
                run, zen, actor, zen_vitals = self.arena(helper=True)
                mei = run.players[1]
                position = list(mei.mover.position)
                self.assertTrue(run.match.switch_hero(mei, heroes()[mei_frost.HERO]))
                mei.mover.position = position
                mei.mover.body = None
                self.start(run, zen, actor)
                fight = run.match.combat
                x, y, z = actor.position
                spray = aim(fight._eye(mei), (x, y+1, z))
                saw_partial = False
                for _ in range(150):
                    run.step({mei: (*spray, 1)})
                    sample = fight.frost.samples.get(actor.entity)
                    if sample is None:
                        continue
                    if sample[2]:
                        break
                    saw_partial = True
                    self.assertEqual(fight.pins.get(actor.entity), zen.body)
                self.assertTrue(saw_partial)
                self.assertIsNotNone(sample)
                self.assertEqual(sample[0], 1)
                self.assertEqual(sample[2]-sample[1], 1300)
                self.assertLess(zen_vitals.total, 175)
                self.assertFalse(self.state(actor, 15).active)
                self.assertFalse(fight.pins)
                self.assertFalse(fight.is_stunned(zen))
                self.assertTrue(fight.is_stunned(actor))
                health = zen_vitals.total
                end = math.ceil(sample[2] / 16)
                angles = aim(fight._eye(zen), (x, y+1, z))
                run.step({zen: (*angles, 1)})
                stop = end-2 if omitted else end-1
                while run.frame < stop:
                    run.step({})
                    self.assertEqual(fight.frost.samples[actor.entity], sample)
                    self.assertFalse(fight.pins)
                    self.assertEqual(zen_vitals.total, health)
                hits = zen.client.messages(combat.HIT)
                self.assertTrue(any(hit["+0x78"]["+0x0"]["+0x0"] == actor.entity
                                    and hit["+0x78"]["+0x4"] == 48 for hit in hits))
                self.assertNotIn(36, actor.body_script.component.held)
                run.frame += omitted
                run.step({})
                self.assertNotIn(actor.entity, fight.frost.samples)
                for _ in range(24):
                    if actor.entity in fight.pins:
                        break
                    run.step({})
                self.assertEqual(fight.pins, {actor.entity: zen.body})
                self.assertAlmostEqual(zen_vitals.total, health-25, places=4)
                self.assertEqual(self.state(actor, 25).counter, 2)
                contacts.append(self.state(actor, 25).start)
        self.assertEqual(len(contacts), 2)
        self.assertEqual(contacts[0], contacts[1])

    def test_actual_blizzard_freezes_assassin_and_releases_allied_zen(self):
        from test_zenyatta_blizzard import ZenyattaBlizzardTests

        run, zen, actor, zen_vitals = self.arena(helper=True)
        mei = run.players[1]
        position = list(mei.mover.position)
        self.assertTrue(run.match.switch_hero(mei, heroes()[mei_frost.HERO]))
        mei.mover.position = position
        mei.mover.body = None
        fight = run.match.combat
        area = ZenyattaBlizzardTests().land(run, mei, aim(fight._eye(mei), actor.position))
        for _ in range(160):
            run.step({})
            sample = fight.frost.samples.get(actor.entity)
            if sample is not None and sample[0] >= .58:
                break
        self.assertIsNotNone(sample)
        self.assertGreaterEqual(sample[0], .58)
        self.assertFalse(sample[2])
        self.assertEqual(zen_vitals.total, 200)
        self.start(run, zen, actor)
        for _ in range(160):
            run.step({})
            sample = fight.frost.samples[actor.entity]
            if sample[2]:
                break
            self.assertEqual(fight.pins.get(actor.entity), zen.body)
        self.assertGreater(sample[2], run.frame*16, (sample, area.starts, area.ends, zen_vitals.total))
        self.assertEqual(sample[2]-sample[1], 1300)
        self.assertIn(area.entity, fight.blizzard.areas)
        self.assertFalse(fight.pins)
        self.assertFalse(fight.is_stunned(zen))
        self.assertTrue(fight.is_stunned(actor))
        self.assertNotIn(zen.body, fight.frost.samples)
        health = zen_vitals.total
        self.assertGreater(health, 0)
        self.assertLess(health, 175)
        while (run.frame+1)*16 < sample[2]:
            run.step({})
            self.assertEqual(fight.frost.samples[actor.entity], sample)
            self.assertFalse(fight.pins)
            self.assertEqual(zen_vitals.total, health)

    def test_actual_soundwave_displacement_releases_assassin_pin_on_range_exit(self):
        from ow174.game.lucio import HERO as LUCIO

        for distance, releases in ((2.75, False), (2.9, True)):
            with self.subTest(distance=distance):
                run, zen, actor, zen_vitals = self.arena(distance, helper=True)
                lucio = run.players[1]
                self.assertTrue(run.match.switch_hero(lucio, heroes()[LUCIO]))
                x, y, z = zen.mover.position
                lucio.mover.position = [x+1, y, z+1]
                lucio.mover.body = None
                fight = run.match.combat
                self.start(run, zen, actor)
                for _ in range(80):
                    if (continuous := self.state(actor, 15)) is not None and continuous.active:
                        break
                    run.step({})
                self.assertTrue(self.state(actor, 15).active)
                self.assertEqual(fight.pins, {actor.entity: zen.body})
                assassin_vitals = fight.vitals[actor.entity]
                before = assassin_vitals.total
                ax, ay, az = actor.position
                angles = aim(fight._eye(lucio), (ax, ay+1, az))
                run.step({lucio: (*angles, SECONDARY)})
                self.assertEqual(assassin_vitals.total, before-25)
                self.assertEqual(fight.pins, {actor.entity: zen.body})
                self.assertFalse(fight.is_stunned(actor))
                before_position = actor.position
                for _ in range(8):
                    run.step({})
                    ax, ay, az = actor.position
                    separation = math.dist((ax, ay+combat.EYE, az), fight._eye(zen))
                    if separation > assassin.PIN_RANGE:
                        self.assertFalse(fight.pins)
                        break
                    self.assertEqual(fight.pins, {actor.entity: zen.body})
                self.assertEqual(separation > assassin.PIN_RANGE, releases)
                self.assertNotEqual(actor.position, before_position)
                self.assertEqual(fight.is_stunned(zen), not releases)
                if releases:
                    health = zen_vitals.total
                    for _ in range(20):
                        run.step({})
                        self.assertEqual(zen_vitals.total, health)

    def test_actual_allied_chain_hook_interrupts_pin_and_pulls_assassin_unless_projected(self):
        roadhog = 0x02E0000000000040
        for projected in (False, True):
            with self.subTest(projected=projected):
                run, zen, actor, zen_vitals = self.arena(helper=True)
                hog = run.players[1]
                self.assertTrue(run.match.switch_hero(hog, heroes()[roadhog]))
                x, y, z = actor.position
                hog.mover.position = [x + 8, y, z]
                hog.mover.body = None
                if projected:
                    zarya = run.match.add_player(3, "Zarya", ZARYA, actor.team, False)
                    in_world(run.match, zarya)
                    self.assertTrue(run.match.switch_hero(zarya, heroes()[ZARYA]))
                    zarya.team = actor.team
                    zarya.mover.position = [x, y, z + 4]
                    zarya.mover.body = None
                    run.players.append(zarya)
                for _ in range(25):
                    run.step({})
                fight = run.match.combat
                self.start(run, zen, actor)
                self.assertEqual(fight.pins, {actor.entity: zen.body})
                actor_vitals = fight.vitals[actor.entity]
                start_hp = actor_vitals.total
                if projected:
                    projection = aim(fight._eye(zarya), (x, y + 1.25, z))
                    run.step({zarya: (*projection, 0x10)})
                    bubble = fight.barriers.bubbles[zarya.body, PROJECTED]
                    self.assertEqual(bubble.target, actor.entity)
                hook_aim = aim(fight._eye(hog), (x, y + 1, z))
                run.step({hog: (*hook_aim, 0x08)})
                for _ in range(30):
                    run.step({hog: (*hook_aim, 0)})
                hook = fight.chain_hooks[hog.body]
                self.assertEqual(hook.remaining, 0)
                self.assertIsNone(hook.victim)
                if projected:
                    self.assertEqual(actor_vitals.total, start_hp)
                    self.assertEqual(actor.position, (x, y, z))
                    self.assertEqual(bubble.hp, 200)
                    self.assertEqual(fight.pins, {actor.entity: zen.body})
                    self.assertFalse(hog.client.messages(combat.HIT))
                    continue

                self.assertFalse(fight.pins)
                self.assertFalse(fight.is_stunned(zen))
                self.assertAlmostEqual(actor.position[0], x + 5, delta=0.2)
                self.assertEqual(actor_vitals.total, start_hp - 30)
                hits = hog.client.messages(combat.HIT)
                self.assertEqual(len(hits), 1)
                self.assertEqual(hits[0]["+0x78"]["+0x0"]["+0x0"], actor.entity)
                self.assertEqual(hits[0]["+0x78"]["+0x4"], 30)
                health = zen_vitals.total
                ax, ay, az = actor.position
                direction = aim(fight._eye(zen), (ax, ay + .75, az))
                run.step({zen: (*direction, FIRE)})
                for _ in range(20):
                    run.step({})
                self.assertEqual(zen_vitals.total, health)
                self.assertEqual(actor_vitals.total, start_hp - 30 - 48)
                self.assertFalse(fight.pins)

    def test_orb_launched_before_pin_can_hit_or_return_lethally_during_pin(self):
        for reflected in (False, True):
            with self.subTest(reflected=reflected):
                run, zen, actor, hp = self.arena(helper=True)
                fight = run.match.combat
                genji = run.players[1]
                self.assertTrue(run.match.switch_hero(genji, heroes()[0x02E0000000000029]))
                genji.team = actor.team
                x, y, z = zen.mover.position
                genji.mover.position = [x + 8, y, z + 18]
                genji.mover.body = None
                facing = aim(fight._eye(genji), (x, y + 1, z))
                guard = {genji: (*facing, 0x10 if reflected else 0)}
                for _ in range(20):
                    run.step(guard)
                hp.damage(140)  # Keep the contact nonlethal and make the returning orb lethal.
                zen.body_script.ult_seconds = 0
                charge = zen.body_script.component.entity_var(342)
                before_charge = charge.value()
                fire = aim(fight._eye(zen), (x + 8, y + 1, z + 18))
                run.step({**guard, zen: (*fire, FIRE)})
                orb, = fight.rockets
                self.assertIs(orb.shooter, zen)
                self.assertEqual(orb.direct.amount, 48)
                ax, ay, az = actor.position
                engage = aim(fight._eye(zen), (ax, ay + 1, az))
                run.step({**guard, zen: (*engage, 0)})
                self.assertEqual(npc_control.start(run.match, zen), actor.entity)
                for _ in range(6):
                    run.step(guard)
                    if actor.entity in fight.pins:
                        break
                self.assertEqual(fight.pins.get(actor.entity), zen.body)
                self.assertEqual(hp.total, 35)
                self.assertIn(orb, fight.rockets)
                self.assertFalse(zen.client.messages(combat.HIT))
                self.assertTrue(fight.is_stunned(zen))
                contact = self.state(actor, 25)
                for _ in range(50):
                    run.step(guard)
                    if orb not in fight.rockets:
                        break
                self.assertNotIn(orb, fight.rockets)
                self.assertEqual(contact.counter, 1)
                continuous = self.state(actor, 15)
                self.assertFalse(continuous and continuous.counter)
                if reflected:
                    self.assertIs(orb.shooter, genji)
                    self.assertEqual((hp.total, hp.killer), (0, genji.body))
                    self.assertFalse(fight.pins)
                    self.assertFalse(zen.client.messages(combat.HIT))
                    self.assertEqual(charge.value(), before_charge)
                    hit, = genji.client.messages(combat.HIT)
                    self.assertEqual(hit["+0x78"]["+0x0"]["+0x0"], zen.body)
                    self.assertEqual(hit["+0x78"]["+0x4"], 35)
                    self.assertEqual(hit["+0x78"]["+0x8"], combat.KILLING)
                    for _ in range(75):
                        run.step({})
                        self.assertFalse(fight.pins)
                    self.assertFalse(continuous and continuous.active)
                else:
                    self.assertIs(orb.shooter, zen)
                    self.assertEqual(fight.pins.get(actor.entity), zen.body)
                    self.assertEqual(hp.total, 35)
                    self.assertEqual(fight.vitals[genji.body].total, 152)
                    hit, = zen.client.messages(combat.HIT)
                    self.assertEqual(hit["+0x78"]["+0x0"]["+0x0"], genji.body)
                    self.assertEqual(hit["+0x78"]["+0x4"], 48)
                    self.assertAlmostEqual(charge.value() - before_charge, 48, places=4)

    def test_actual_support_bullet_kills_wounded_pin_source_and_retains_corpse(self):
        run, zen, actor, vitals = self.arena(helper=True)
        self.start(run, zen, actor)
        own = run.match.combat.vitals[actor.entity]
        own.damage(own.total-1)  # wounded source fixture; actual Soldier input performs the kill
        support = run.players[1]
        x, y, z = actor.position
        angles = aim(run.match.combat._eye(support), (x, y+1, z))
        run.step({support: (*angles, 0x01)})
        for _ in range(30):
            run.step({})
            if actor.dead:
                break
        self.assertTrue(actor.dead)
        self.assertFalse(run.match.combat.pins)
        before = vitals.total
        last_damage = run.match.combat.zenyatta.owners[zen.body].last_damage
        for _ in range(100):
            run.step({})
            self.assertEqual(vitals.total, before)
        for _ in range(120):
            run.step({})
        self.assertGreaterEqual(vitals.total, before)  # ordinary shield regeneration may now run
        self.assertEqual(run.match.combat.zenyatta.owners[zen.body].last_damage, last_damage)
        self.assertIn(actor, run.match.bots)
        self.assertFalse(any(who is actor for _, who in run.match.combat.respawns))
        self.assertTrue(support.client.messages(combat.KILL_NOTICE))

    def test_victim_body_replacement_and_manual_removal_retire_pin_identity(self):
        for removal in (False, True):
            with self.subTest(removal=removal):
                run, zen, actor, vitals = self.arena()
                self.start(run, zen, actor)
                old = zen.body
                if removal:
                    self.assertEqual(npc.remove_spawned(run.match), (actor.entity,))
                else:
                    run.match.switch_hero(zen, zen.hero)
                    zen.mover.position[0] += 20
                    zen.mover.body = None
                    self.assertNotEqual(zen.body, old)
                self.assertFalse(run.match.combat.pins)
                before = vitals.total
                for _ in range(100):
                    run.step({})
                self.assertEqual(vitals.total, before)
                self.assertFalse(run.match.combat.is_stunned(zen))

    def test_assassin_contact_tick_precedes_same_tick_ultimate_command(self):
        for before_contact in (False, True):
            with self.subTest(before_contact=before_contact):
                run, zen, actor, vitals = self.arena()
                fight = run.match.combat
                script = zen.body_script
                cost = script.component.entity_var(338).value()
                charge = script.component.entity_var(342)
                script.set_ult_charge(cost)
                self.engage(run, zen, actor, buttons=0x20 if before_contact else 0)
                self.assertTrue(actor.firing)
                self.assertEqual(fight.zenyatta.ultimate(zen), before_contact)
                self.assertEqual(vitals.total, 200)
                self.assertFalse(fight.pins)
                phase = self.state(actor, 25)
                prior_contacts = phase.counter if phase is not None else 0

                run.step({zen: (0, 0, 0 if before_contact else 0x20)})
                self.assertGreater(self.state(actor, 25).counter, prior_contacts)
                self.assertEqual(fight.zenyatta.ultimate(zen), before_contact)
                self.assertEqual(fight.is_stunned(zen), not before_contact)
                self.assertEqual(fight.pins.get(actor.entity), None if before_contact else zen.body)
                self.assertEqual(vitals.total, 200 if before_contact else 175)
                self.assertEqual(charge.value(), 0 if before_contact else cost)

    def test_actual_transcendence_rejects_pin_and_preserves_native_duration(self):
        run, zen, actor, vitals = self.arena()
        self.start(run, zen, actor)
        self.assertEqual(vitals.total, 175)
        fight = run.match.combat
        owner = fight.zenyatta.owners[zen.body]
        damage_clock = owner.last_damage
        npc_control.stop(run.match)
        for _ in range(16):
            run.step({})
        self.assertFalse(fight.pins)
        self.assertEqual(owner.last_damage, damage_clock)
        prior_contacts = self.state(actor, 25).counter

        zen.body_script.set_ult_charge(zen.body_script.component.entity_var(338).value())
        run.step({zen: (0, 0, 0x20)})
        self.assertTrue(run.match.combat.zenyatta.ultimate(zen))
        self.engage(run, zen, actor)
        start = run.frame
        while run.frame < start+370:
            run.step({})
            self.assertTrue(run.match.combat.zenyatta.ultimate(zen))
            self.assertFalse(run.match.combat.pins)
            self.assertEqual(vitals.total, 200)
        self.assertGreater(self.state(actor, 25).counter, prior_contacts)
        self.assertEqual(owner.last_damage, damage_clock)
        self.assertFalse(run.match.combat.is_stunned(zen))
        for _ in range(10):
            if not run.match.combat.zenyatta.ultimate(zen):
                break
            run.step({})
        self.assertFalse(run.match.combat.zenyatta.ultimate(zen))
        npc_control.stop(run.match)
        x, y, z = zen.mover.position
        actor.position = (x, y, z+2)
        self.start(run, zen, actor)
        self.assertEqual(run.match.combat.pins[actor.entity], zen.body)
        self.assertEqual(vitals.total, 175)
        self.assertGreater(owner.last_damage, damage_clock)
