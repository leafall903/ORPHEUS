"""Declared 1:1 combat-charge reconstruction; native weights/cadence stay open."""

import unittest
from unittest.mock import patch

import test_game_moira as fixtures
import test_game_moira_orbs as orb_fixtures
from test_game_combat import KANEZAKA_DM, Run

from ow174.game import absorption, combat, cryo, moira, stats
from ow174.game.body_vm_snapshot import export_component_vm
from ow174.game.commands import CROUCH, Command
from ow174.game.content import heroes
from ow174.game.script import expr, runtime
from ow174.game.script.driver import ULT_CHARGE, ULT_COST, ULT_GAIN_ENABLED
from ow174.game.server_snapshot import _Graph


class MoiraCombatChargeTests(unittest.TestCase):
    setup_fight = fixtures.MoiraSprayTests.setup_fight
    launch_orb = orb_fixtures.MoiraOrbTests.launch
    start_ult = fixtures.MoiraOpenAcceptanceTests.start_ult

    @staticmethod
    def reset(source):
        source.body_script.component.set(12918, 0)  # isolate combat gain from the authored passive pulse
        source.body_script.ult_seconds = 0
        source.body_script.set_ult_charge(0)

    @staticmethod
    def charge(source):
        return source.body_script.component.entity_var(ULT_CHARGE).value()

    def test_normal_match_starts_passive_charge_after_assembly(self):
        run = Run(KANEZAKA_DM)
        source, = run.players
        match = run.match
        match.assembled = False
        match.assemble_ends = (run.frame + 70) * 0.016
        self.assertTrue(match.switch_hero(source, heroes()[moira.MOIRA]))
        component = source.body_script.component
        for _ in range(69):
            run.step({})
        self.assertTrue(match.assembling())
        self.assertIsNone(component.entity_var(ULT_GAIN_ENABLED).value())
        self.assertEqual(self.charge(source), 0)
        run.step({})
        self.assertFalse(match.assembling())
        self.assertIs(component.entity_var(ULT_GAIN_ENABLED).value(), True)
        for _ in range(61):
            run.step({})
        self.assertEqual(self.charge(source), 0)
        for _ in range(3):
            run.step({})
        self.assertEqual(self.charge(source), 5)
        self.assertIsNone(component.fault)

    def test_passive_charge_uses_the_same_clock_during_long_command_gaps(self):
        expected = []
        run, source, _, _ = self.setup_fight(ally=False)
        for elapsed in range(1, 421):
            run.step({})
            if elapsed in (120, 420):
                expected.append(self.charge(source))
        self.assertEqual(expected, [10, 35])

        for jumps in (False, True):
            with self.subTest(jumps=jumps):
                run, source, _, _ = self.setup_fight(ally=False)
                self.assertIs(source.body_script.component.entity_var(ULT_GAIN_ENABLED).value(), True)
                start = run.frame
                actual = []
                for elapsed in (120, 420) if jumps else range(1, 421):
                    run.frame = start + elapsed
                    run.match.update(run.frame * combat.TICK, run.frame)
                    if elapsed in (120, 420):
                        actual.append(self.charge(source))
                self.assertEqual(actual, expected)
                command = Command(run.frame)
                source.take_commands([command, command])
                self.assertEqual(self.charge(source), expected[-1])

    def test_silent_passive_charge_stops_during_coalescence_and_caps_afterward(self):
        run, source, _, _ = self.setup_fight(ally=False)
        script = source.body_script
        component = script.component
        self.start_ult(run, source)
        casting = component.entity_var(842)
        self.assertTrue(casting.value())

        def advance():
            run.frame += 1
            run.match.update(run.frame * combat.TICK, run.frame)

        for _ in range(600):
            advance()
            self.assertEqual(self.charge(source), 0)
            if not casting.value():
                break
        else:
            self.fail("Coalescence never released the passive charge gate")
        for _ in range(60):
            advance()
            self.assertEqual(self.charge(source), 0)
        for _ in range(5):
            advance()
        self.assertEqual(self.charge(source), 5)
        cost = component.entity_var(ULT_COST).value()
        script.set_ult_charge(cost - 2)
        for _ in range(70):
            advance()
        self.assertEqual(self.charge(source), cost)

    def test_enabled_passive_graph_owns_charge_and_resumes_after_actual_ultimate(self):
        run, source, _target, _vitals = self.setup_fight(ally=False)
        script = source.body_script
        component = script.component

        def advance(frames):
            for _ in range(frames):
                run.step({})
            self.assertIsNone(component.fault)

        self.assertIs(component.entity_var(ULT_GAIN_ENABLED).value(), True)
        self.assertEqual(self.charge(source), 0)
        advance(45)
        self.assertEqual(self.charge(source), 5)
        script.set_ult_charge(0)
        gate = next(i for i in component.instances.values() if i.graph.index == 0x01C7)
        gate.node_state(gate.graph.node(77)).request_finish()
        component.run_now()
        component.set(ULT_GAIN_ENABLED, False)
        self.assertIs(component.entity_var(ULT_GAIN_ENABLED).value(), False)
        advance(80)
        self.assertEqual(self.charge(source), 0)
        self.assertEqual(runtime.send_instance_message(gate, 0x0240000000000700, {}, source.body), 1)
        component.run_now()
        self.assertIs(component.entity_var(ULT_GAIN_ENABLED).value(), True)
        advance(61)
        self.assertEqual(self.charge(source), 0)
        advance(3)
        self.assertEqual(self.charge(source), 5)
        component.set(11942, True)
        advance(80)
        self.assertEqual(self.charge(source), 5)
        component.set(11942, False)
        advance(64)
        self.assertEqual(self.charge(source), 10)
        self.start_ult(run, source)
        self.assertIs(component.entity_var(842).value(), True)
        advance(80)
        self.assertEqual(self.charge(source), 0)
        for _ in range(600):
            if not component.entity_var(842).value():
                break
            run.step({})
        self.assertFalse(component.entity_var(842).value())
        advance(61)
        self.assertEqual(self.charge(source), 0)
        advance(3)
        self.assertEqual(self.charge(source), 5)

    def test_authored_damage_charge_reduces_adjustment_records(self):
        _run, source, target, _vitals = self.setup_fight(ally=False)
        self.reset(source)
        component = source.body_script.component
        instance = next(i for i in component.instances.values() if i.graph.index == 0x0B8B)
        component.set(ULT_GAIN_ENABLED, True)

        def record(identifier, amount):
            return expr.Map(((16056, expr.Asset(0x0D80000000000000 | identifier)), (499, amount)))

        first, second, ignored = record(0x3EBB, 7), record(0x3EBB, 3), record(0x3EBC, 29)
        for adjustments, expected in (((), 40), ((ignored,), 40),
                                      ((first, (ignored, second)), 30), ((record(0x3EBB, 45),), 0)):
            with self.subTest(adjustments=adjustments):
                source.body_script.set_ult_charge(0)
                params = {0x13: 40, 0x15: expr.Entity(target.entity), 0x3D: (), 0x624: adjustments}
                self.assertEqual(
                    runtime.send_instance_message(instance, 0x0240000000000012, params, source.body), 1)
                component.run_now()
                self.assertIsNone(component.fault)
                self.assertEqual(self.charge(source), expected)
                self.assertFalse(component.parameter_scopes)

    def test_damage_notification_148_requires_tag_and_retains_graph_adjustment(self):
        _run, source, target, _vitals = self.setup_fight(ally=False)
        self.reset(source)
        component = source.body_script.component
        component.set(ULT_GAIN_ENABLED, True)
        instance = next(item for item in component.instances.values() if item.graph.index == 0x0B8B)
        tag = expr.Asset(0x0D8000000000090D)
        adjustment = expr.Map(((16056, expr.Asset(0x0D80000000003EBB)), (499, 20)))

        def deliver(message, expected, *, tags=(), adjustments=None):
            params = {0x13: 30, 0x15: expr.Entity(target.entity), 0x3D: tags}
            if adjustments is not None:
                params[0x624] = adjustments
            previous = self.charge(source)
            self.assertEqual(runtime.send_instance_message(
                instance, 0x0240000000000000 | message, params, source.body,
            ), 1)
            component.run_now()
            self.assertIsNone(component.fault)
            self.assertFalse(component.parameter_scopes)
            self.assertEqual(self.charge(source) - previous, expected)

        deliver(0x148, 0)
        deliver(0x148, 30, tags=(tag,))
        deliver(0x12, 10, adjustments=(adjustment,))
        deliver(0x148, 10, tags=(tag,))
        deliver(0x12, 30, adjustments=())
        deliver(0x148, 30, tags=(tag,))

    def test_actual_damage_paths_respect_enemy_protection_windows(self):
        for guid, graph, slot, press in (
            (0x02E0000000000002, 0x0020, 5, 8),
            (0x02E00000000000DD, 0x05FE, 35, 8),
            (0x02E0000000000020, 0x017B, 31, 32),
        ):
            for attack in ('grasp', 'orb', 'melee', 'coalescence'):
                with self.subTest(hero=guid, attack=attack):
                    run = Run(KANEZAKA_DM, names=('Moira', 'Protected'))
                    fight = run.match.combat
                    source, enemy = run.players
                    for player, hero, position in (
                        (source, fixtures.MOIRA, [0, 1, 0]),
                        (enemy, guid, [0, 1, 1.8]),
                    ):
                        self.assertTrue(run.match.switch_hero(player, heroes()[hero]))
                        player.mover.position = position
                        player.mover.body = None
                    for _ in range(30):
                        run.step({})
                    self.reset(source)
                    if attack == 'coalescence':
                        self.start_ult(run, source)
                    if press == 32:
                        script = enemy.body_script
                        script.set_ult_charge(script.component.entity_var(ULT_COST).value())
                    run.step({enemy: (-32768, 0, press)})
                    instance = next(i for i in enemy.body_script.component.instances.values()
                                    if i.graph.index == graph)
                    self.assertTrue(instance.state(slot).active)
                    self.assertTrue(fight.damage_immune(enemy))
                    targets = {target.owner: target for target in fight._targets()}
                    own, victim = (fight.vitals_of(targets[player]) for player in run.players)
                    own.damage(30)
                    before = victim.total
                    charge = self.charge(source)
                    damage_done = source.stats.value(stats.HERO_DAMAGE)
                    orb = self.launch_orb(run, source) if attack == 'orb' else None
                    own_before = own.total
                    for frame in range(20):
                        button = 2 if attack == 'grasp' else 0x800 if attack == 'melee' and frame == 0 else 0
                        run.step({source: (0, 0, button)})
                    self.assertTrue(instance.state(slot).active)
                    self.assertGreaterEqual(victim.total, before)
                    self.assertEqual(source.stats.value(stats.HERO_DAMAGE), damage_done)
                    self.assertEqual(self.charge(source), charge)
                    if attack == 'grasp':
                        self.assertEqual(own.total, own_before)
                        for _ in range(420):
                            if not fight.damage_immune(enemy):
                                break
                            run.step({})
                        self.assertFalse(fight.damage_immune(enemy))
                        for _ in range(20):
                            run.step({source: (0, 0, 2)})
                        self.assertLess(victim.total, before)
                    if orb is not None:
                        self.assertEqual(orb.energy, 200)

    def test_grasp_passes_wraith_but_retains_cryo_and_transcendence_contact(self):
        for guid, press, bypass in (
            (0x02E0000000000002, 8, True),
            (0x02E00000000000DD, 8, False),
            (0x02E0000000000020, 32, False),
        ):
            with self.subTest(hero=guid):
                run = Run(KANEZAKA_DM, names=('Moira', 'Protected', 'Behind'))
                match = run.match
                source, protected, behind = run.players
                self.assertTrue(match.switch_hero(source, heroes()[fixtures.MOIRA]))
                self.assertTrue(match.switch_hero(protected, heroes()[guid]))
                for actor, position in zip(run.players, ([0, 1, 0], [0, 1, 3], [0, 1, 6]), strict=True):
                    actor.mover.position = position
                    actor.mover.body = None
                for _ in range(30):
                    run.step({})
                if press == 32:
                    script = protected.body_script
                    script.set_ult_charge(script.component.entity_var(ULT_COST).value())
                run.step({protected: (-32768, 0, press)})
                self.assertTrue(match.combat.damage_immune(protected))
                targets = {target.owner: target for target in match.combat._targets()}
                front = match.combat.vitals_of(targets[protected])
                back = match.combat.vitals_of(targets[behind])
                before = front.total, back.total
                for _ in range(20):
                    run.step({source: (0, 0, 2)})
                self.assertEqual(front.total, before[0])
                self.assertAlmostEqual(before[1] - back.total, 16 if bypass else 0)

    def test_cryo_pet_intercepts_actual_helix_and_blocks_splash(self):
        for cancel in (False, True):
            with self.subTest(cancel=cancel):
                run = Run(KANEZAKA_DM, names=("Soldier", "Mei", "Moira"))
                match = run.match
                fight = match.combat
                source, mei, recipient = run.players
                self.assertTrue(match.switch_hero(mei, heroes()[cryo.HERO]))
                self.assertTrue(match.switch_hero(recipient, heroes()[fixtures.MOIRA]))
                for actor, position in zip(run.players, ([0, 1, 0], [.2, 1, 2.5], [0, 1, 4.6]), strict=True):
                    actor.mover.position = position
                    actor.mover.body = None
                crouch = {mei: (-32768, 0, CROUCH)}
                for _ in range(30):
                    run.step(crouch)
                run.step({mei: (-32768, 0, CROUCH | 8)})
                self.assertTrue(fight.damage_immune(mei))
                for _ in range(20):
                    run.step(crouch)
                if cancel:
                    run.step({mei: (-32768, 0, CROUCH | 2)})
                self.assertEqual(bool(fight.cryo_actors), not cancel)
                health = fight.vitals_of(next(t for t in fight._targets() if t.owner is recipient))
                with patch.object(fight, "_explode", wraps=fight._explode) as blast:
                    run.step({**crouch, source: (0, 0, 2)})
                    for _ in range(28):
                        run.step(crouch)
                    blast.assert_called_once()
                    self.assertEqual(blast.call_args.args[1][2] < 2.5, not cancel)
                self.assertEqual(health.total, 80 if cancel else 200)

    def test_cryo_pet_blocks_grasp_above_crouched_body_until_retirement(self):
        for end in ('active', 'cancel', 'expire', 'replace', 'leave'):
            with self.subTest(end=end):
                run = Run(KANEZAKA_DM, names=('Moira', 'Mei', 'Behind'))
                match = run.match
                source, mei, behind = run.players
                self.assertTrue(match.switch_hero(source, heroes()[fixtures.MOIRA]))
                self.assertTrue(match.switch_hero(mei, heroes()[combat.CRYO_FREEZE[0]]))
                for actor, position in zip(run.players, ([0, 1, 0], [.2, 1, 2.5], [0, 1, 6.5]), strict=True):
                    actor.mover.position = position
                    actor.mover.body = None
                crouch = {mei: (-32768, 0, CROUCH)}
                for _ in range(30):
                    run.step(crouch)
                run.step({mei: (-32768, 0, CROUCH | 8)})
                fight = match.combat
                self.assertTrue(fight.damage_immune(mei))
                ice, = fight.cryo_actors.values()
                child, = ice.component.instances.values()
                self.assertEqual(child.graph.index, cryo.EFFECT)
                self.assertEqual(child.source_entity(), mei.body)
                self.assertTrue(child.initialized)
                self.assertEqual(ice.pet.pet_entity, ice.entity)
                self.assertTrue(export_component_vm(ice.component))
                storage = _Graph({"owner": mei, "match": match}, {})
                storage.value(ice)
                records = storage.finish()
                self.assertTrue(any(record["type"].endswith(".Ice") for record in records))
                if end == 'replace':
                    old_body = mei.body
                    self.assertTrue(match.switch_hero(mei, heroes()[fixtures.MOIRA]))
                    self.assertNotEqual(mei.body, old_body)
                    self.assertFalse(fight.cryo_actors)
                    self.assertFalse(ice.component.instances)
                    continue
                if end == 'leave':
                    match.leave(mei)
                    self.assertFalse(fight.cryo_actors)
                    self.assertFalse(ice.component.instances)
                    continue
                if end == 'cancel':
                    for _ in range(20):
                        run.step(crouch)
                    run.step({mei: (-32768, 0, CROUCH | 2)})
                    for _ in range(20):
                        run.step(crouch)
                    run.step({mei: (-32768, 0, CROUCH | 8)})
                if end == 'expire':
                    for _ in range(280):
                        if not fight.damage_immune(mei):
                            break
                        run.step(crouch)
                self.assertEqual(fight.damage_immune(mei), end == 'active')
                self.assertEqual(bool(fight.cryo_actors), end == 'active')
                self.assertEqual(bool(ice.component.instances), end == 'active')
                if end == 'expire':
                    for _ in range(20):
                        run.step(crouch)
                targets = {target.owner: target for target in fight._targets()}
                origin, direction = fight._eye(source), combat.aim(0, 0)
                body = targets[mei]
                self.assertIsNone(combat.ray_capsule(origin, direction, *body.segment(), body.radius))
                ice = fight._cryo_contact(source, origin, direction, 8)
                self.assertEqual(ice is not None, end == 'active')
                front, back = fight.vitals_of(body), fight.vitals_of(targets[behind])
                before = front.total, back.total
                for _ in range(20):
                    run.step({source: (0, 0, 2), **crouch})
                self.assertEqual(front.total, before[0])
                self.assertAlmostEqual(before[1] - back.total, 0 if end == 'active' else 16)

    def test_actual_cryo_restores_grasp_damage_and_stops_healing_on_cancel(self):
        for cancel in (False, True):
            with self.subTest(cancel=cancel):
                run = Run(KANEZAKA_DM, names=('Moira', 'Mei'))
                fight = run.match.combat
                source, mei = run.players
                self.assertTrue(run.match.switch_hero(source, heroes()[fixtures.MOIRA]))
                self.assertTrue(run.match.switch_hero(mei, heroes()[cryo.HERO]))
                for actor, position in zip(run.players, ([0, 1, 0], [0, 1, 3]), strict=True):
                    actor.mover.position = position
                    actor.mover.body = None
                for _ in range(30):
                    run.step({})
                target = next(target for target in fight._targets() if target.owner is mei)
                vitals = fight.vitals_of(target)
                for _ in range(260):
                    run.step({source: (0, 0, 2)})
                wounded = vitals.total
                self.assertGreater(wounded, 0)
                self.assertLessEqual(wounded + 200, vitals.maximum)
                run.step({mei: (-32768, 0, 8)})
                self.assertTrue(fight.damage_immune(mei))
                for _ in range(35):
                    run.step({})
                partial = vitals.total
                self.assertGreater(partial, wounded)
                self.assertLess(partial, wounded + 200)
                if cancel:
                    run.step({mei: (-32768, 0, 2)})
                    self.assertFalse(fight.damage_immune(mei))
                after_cancel = vitals.total
                for _ in range(260):
                    run.step({})
                self.assertAlmostEqual(vitals.total, after_cancel if cancel else wounded + 200, places=4)

    def test_actual_lethal_grasp_charges_only_health_lost(self):
        run, source, target, vitals = self.setup_fight(ally=False)
        self.reset(source)
        self.assertIs(source.body_script.component.entity_var(ULT_GAIN_ENABLED).value(), True)
        vitals.damage(vitals.total - 0.3)
        for _ in range(20):
            run.step({source: (0, 0, 2)})
        self.assertTrue(vitals.dead)
        self.assertEqual(vitals.killer, source.body)
        self.assertEqual(vitals.total, 0)
        self.assertAlmostEqual(source.stats.value(stats.HERO_DAMAGE), 0.3)
        self.assertAlmostEqual(self.charge(source), 0.3)
        self.assertIsNotNone(target.owner.body_script)

    def test_grasp_counts_actual_damage_but_not_self_healing_or_misses(self):
        run, source, _target, vitals = self.setup_fight(ally=False)
        self.reset(source)
        fight = run.match.combat
        own = next(t for t in fight._targets() if t.owner is source)
        health = fight.vitals_of(own)
        health.damage(100)
        for _ in range(20):
            run.step({source: (0, 0, 2)})
        self.assertAlmostEqual(100 - vitals.total, 16, places=4)
        self.assertGreater(health.total, 100)
        self.assertAlmostEqual(self.charge(source), 16, places=4)
        before = self.charge(source)
        for _ in range(10):
            run.step({source: (16384, 0, 2)})
        self.assertEqual(self.charge(source), before)

    def test_actual_grasp_and_spray_obey_authored_charge_gate_and_rates(self):
        for ally in (False, True):
            with self.subTest(ally=ally):
                run, source, _target, vitals = self.setup_fight(ally=ally)
                self.reset(source)
                component = source.body_script.component
                component.set(9532, 2)
                component.set(13022, 0.25)
                gate = next(i for i in component.instances.values() if i.graph.index == 0x01C7)
                gate.node_state(gate.graph.node(77)).request_finish()
                component.run_now()
                for enabled, paused, rate in ((False, False, 0), (True, True, 0), (True, False, 0.5)):
                    with self.subTest(enabled=enabled, paused=paused):
                        component.set(ULT_GAIN_ENABLED, enabled)
                        component.set(842, paused)
                        before = vitals.total
                        charge = self.charge(source)
                        for _ in range(20):
                            run.step({source: (0, 0, 1 if ally else 2)})
                        delta = abs(vitals.total - before)
                        self.assertGreater(delta, 0)
                        self.assertAlmostEqual(self.charge(source) - charge, delta * rate, places=3)

    def test_spray_and_tail_count_actual_ally_healing(self):
        run, source, target, vitals = self.setup_fight()
        self.reset(source)
        source.body_script.component.set(ULT_GAIN_ENABLED, True)
        for _ in range(20):
            run.step({source: (0, 0, 1)})
        for _ in range(160):
            run.step({})
        self.assertGreater(vitals.total, 135)
        component = source.body_script.component
        instance = next(item for item in component.instances.values() if item.graph.index == 0x0B8B)
        self.assertEqual(instance.vars[536].value(), expr.Entity(target.entity))
        self.assertEqual(instance.vars[659].value(), (expr.Asset(0x0D80000000002F9F),))
        self.assertFalse(component.parameter_scopes)
        self.assertAlmostEqual(self.charge(source), vitals.total - 100, places=3)

    def test_scaled_grasp_earns_the_last_charge_for_actual_ultimate(self):
        run, source, _target, vitals = self.setup_fight(ally=False)
        self.reset(source)
        script = source.body_script
        component = script.component
        component.set(ULT_GAIN_ENABLED, True)
        component.set(11942, True)  # pause passive only
        component.set(13022, 0.5)
        cost = component.entity_var(ULT_COST).value()
        script.set_ult_charge(cost - 1)
        ultimate = next(i for i in component.instances.values() if i.graph.index == 0x1007)
        run.step({source: (0, 0, 32)})
        for _ in range(30):
            run.step({})
        self.assertEqual(self.charge(source), cost - 1)
        self.assertFalse(ultimate.states.get(20) and ultimate.states[20].active)

        before = vitals.total
        for _ in range(2):
            run.step({source: (0, 0, 2)})
        self.assertAlmostEqual(before - vitals.total, 1.6, places=4)
        self.assertAlmostEqual(self.charge(source), cost - 0.2, places=3)
        run.step({source: (0, 0, 2)})
        self.assertEqual(self.charge(source), cost)

        run.step({source: (0, 0, 32)})
        for _ in range(60):
            if ultimate.states.get(20) and ultimate.states[20].active:
                break
            run.step({})
        self.assertTrue(ultimate.states.get(20) and ultimate.states[20].active)
        self.assertEqual(self.charge(source), 0)

    def test_authored_roadhog_body_reduces_actual_damage_charge(self):
        for attack in ("grasp", "orb", "melee"):
            with self.subTest(attack=attack):
                run, source, target, _vitals = self.setup_fight(ally=False, distance=1.8)
                match, enemy = run.match, target.owner
                self.assertTrue(match.switch_hero(enemy, heroes()[0x02E0000000000040]))
                self.assertEqual(enemy.hero.body, 0x040000000000028F)
                x, y, z = source.mover.position
                enemy.mover.position = [x, y, z + 1.8]
                enemy.mover.body = None
                for _ in range(20):
                    run.step({})
                self.reset(source)
                source.body_script.component.set(ULT_GAIN_ENABLED, True)
                target = next(t for t in match.combat._targets() if t.owner is enemy)
                vitals = match.combat.vitals_of(target)
                before = vitals.total
                if attack == "orb":
                    self.launch_orb(run, source)
                for frame in range(20):
                    button = 2 if attack == "grasp" else 0x800 if attack == "melee" and frame == 0 else 0
                    run.step({source: (0, 0, button)})
                damage = before - vitals.total
                self.assertGreater(damage, 0)
                self.assertAlmostEqual(self.charge(source), damage * 0.75, places=4)

    def test_actual_adaptive_shield_grant_absorbs_melee_then_expires(self):
        run, source, target, _vitals = self.setup_fight(ally=False, distance=1.8)
        match, ball = run.match, target.owner
        self.assertTrue(match.switch_hero(ball, heroes()[0x02E00000000001CA]))
        x, y, z = source.mover.position
        ball.mover.position = [x, y, z + 1.8]
        ball.mover.body = None
        for _ in range(40):
            run.step({})
        self.reset(source)
        source.body_script.component.set(ULT_GAIN_ENABLED, True)
        target = next(target for target in match.combat._targets() if target.owner is ball)
        vitals = match.combat.vitals_of(target)
        base = vitals.total
        run.step({ball: (0, 0, 16)})
        instance = next(item for item in ball.body_script.component.instances.values()
                        if item.graph.index == 0x1566)
        for _ in range(5):
            if instance.states.get(6) is not None and instance.states[6].active:
                break
            run.step({})
        state = instance.states.get(6)
        self.assertTrue(state is not None and state.active)
        shield = vitals.temporary_shields[moira.ADAPTIVE_SHIELD_TAG]
        self.assertEqual(shield.now, 175)

        run.step({source: (0, 0, 0x800)})
        for _ in range(19):
            run.step({ball: (0, 0, 16)})
        self.assertEqual(state.counter, 1)
        self.assertEqual(shield.now, 145)
        self.assertEqual(vitals.total, base + 145)
        self.assertEqual(source.stats.value(stats.HERO_DAMAGE), 30)
        self.assertEqual(self.charge(source), 0)
        component = source.body_script.component
        charge_graph = next(item for item in component.instances.values() if item.graph.index == 0x0B8B)
        self.assertEqual(charge_graph.vars[393].value(), expr.Entity(target.entity))
        self.assertEqual(charge_graph.vars[16102].value(), 30)
        self.assertFalse(component.parameter_scopes)

        for _ in range(20):  # let the melee weapon lock finish
            run.step({})
        own = next(item for item in match.combat._targets() if item.owner is source)
        own_vitals = match.combat.vitals_of(own)
        own_vitals.damage(100)
        health_before = own_vitals.total
        weapon = next(item for item in component.instances.values() if item.graph.index == 0xF76)
        weapon.write((expr.INSTANCE, 87), 20.0)
        for _ in range(20):
            run.step({source: (0, 0, 2)})
        self.assertAlmostEqual(shield.now, 129, places=4)
        self.assertAlmostEqual(vitals.total, base + 129, places=4)
        self.assertAlmostEqual(source.stats.value(stats.HERO_DAMAGE), 46, places=4)
        self.assertAlmostEqual(own_vitals.total - health_before, 7.68, places=4)
        self.assertGreaterEqual(weapon.find_var(expr.INSTANCE, 87).value(), 80)
        self.assertLess(weapon.find_var(expr.INSTANCE, 87).value(), 81)
        self.assertEqual(self.charge(source), 0)

        for _ in range(565):
            run.step({})
        self.assertFalse(state.active)
        self.assertEqual(shield.now, 0)
        self.assertEqual(vitals.total, base)
        charge_before = self.charge(source)
        run.step({source: (0, 0, 0x800), ball: (0, 0, 16)})
        for _ in range(19):
            run.step({})
        self.assertEqual(state.counter, 1)
        self.assertEqual(shield.now, 0)
        self.assertAlmostEqual(base - vitals.total, 25)
        self.assertAlmostEqual(self.charge(source) - charge_before, 25)

    def test_adaptive_shield_loss_is_excluded_from_actual_melee_charge(self):
        for tag, shield, expected in (
            (moira.ADAPTIVE_SHIELD_TAG, 40, 0),
            (moira.ADAPTIVE_SHIELD_TAG, 10, 20),
            (absorption.SHIELD_TAG, 40, 30),
        ):
            with self.subTest(tag=tag, shield=shield):
                run, source, _target, vitals = self.setup_fight(ally=False, distance=1.8)
                self.reset(source)
                source.body_script.component.set(ULT_GAIN_ENABLED, True)
                vitals.grant_temporary_shields(tag, shield, shield)
                before = vitals.total
                damage_before = source.stats.value(stats.HERO_DAMAGE)
                run.step({source: (0, 0, 0x800)})
                for _ in range(19):
                    run.step({})
                self.assertAlmostEqual(before - vitals.total, 30)
                self.assertAlmostEqual(source.stats.value(stats.HERO_DAMAGE) - damage_before, 30)
                self.assertAlmostEqual(vitals.temporary_shields[tag].now, max(0, shield - 30))
                self.assertAlmostEqual(self.charge(source), expected)

    def test_healing_orb_counts_ally_restore_but_not_caster_restore(self):
        for ally in (False, True):
            with self.subTest(ally=ally):
                run, source, _target, _vitals = self.setup_fight(ally=ally)
                self.reset(source)
                source.body_script.component.set(ULT_GAIN_ENABLED, True)
                if not ally:
                    own = next(t for t in run.match.combat._targets() if t.owner is source)
                    run.match.combat.vitals_of(own).damage(100)
                self.launch_orb(run, source, healing=True)
                for _ in range(20):
                    run.step({})
                self.assertAlmostEqual(self.charge(source), 20.8 if ally else 0, places=4)

    def test_ultimate_effects_cannot_generate_charge(self):
        run, source, _target, vitals = self.setup_fight(ally=False)
        self.reset(source)
        self.start_ult(run, source)
        before = vitals.total
        for _ in range(20):
            run.step({})
        self.assertLess(vitals.total, before)
        self.assertEqual(self.charge(source), 0)

    def test_damage_gain_caps_at_actual_cost(self):
        run, source, target, _vitals = self.setup_fight(ally=False)
        self.reset(source)
        cost = source.body_script.component.entity_var(ULT_COST).value()
        source.body_script.set_ult_charge(cost - 0.2)
        run.match.combat._damage(source, target, 1, False, None, run.frame)
        self.assertEqual(self.charge(source), cost)

    def test_old_orb_damage_cannot_charge_a_respawned_body(self):
        run, source, target, _vitals = self.setup_fight(ally=False)
        self.reset(source)
        orb = self.launch_orb(run, source)
        fight = run.match.combat
        own = next(t for t in fight._targets() if t.owner is source)
        old = source.body
        fight._damage(target.owner, own, 1000, False, None, run.frame)
        for _ in range(210):
            run.step({})
        self.assertNotEqual(source.body, old)
        self.assertIn(orb, fight.moira_orbs)
        self.reset(source)
        fresh = next(t for t in fight._targets() if t.owner is target.owner)
        health = fight.vitals_of(fresh)
        # Controlled surviving-flight suffix: force proximity without changing
        # attribution, energy, damage operation or the new body's charge state.
        orb.position = (fresh.feet[0], fresh.feet[1] + fresh.height / 2, fresh.feet[2])
        orb.speed = 0
        before = health.total
        run.step({})
        self.assertLess(health.total, before)
        self.assertEqual(self.charge(source), 0)
