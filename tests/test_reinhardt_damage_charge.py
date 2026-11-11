"""Actual Combat-to-0B8B events in Match and with declared engine services.

Damage, inputs, resource tags, native VM filters/folds and charge entries execute.
Classification bit1/component97, component50 absence, tags and optional registries
are fixture inputs. This does not establish native Match component construction.
Passive wait0 is stopped to isolate damage gain from elapsed-time gain.
"""

import os
import unittest
from contextlib import nullcontext
from dataclasses import replace
from unittest.mock import patch

import test_game_reinhardt as hero_tests
from test_game_combat import aim, in_world

from ow174.game import combat, defenses
from ow174.game.content import SOLDIER, heroes
from ow174.game.script import entity_filter, entity_hostility, entity_tags, expr, runtime
from ow174.game.script.entity_team import FilterBits


def isolate_damage_charge(player):
    script = player.body_script
    common = next(i for i in script.component.instances.values() if i.graph.index == 0xB8B)
    common.states[0].end(False)
    script.set_ult_charge(0)
    return common


def install_backend(case, *, tags=(), admit=True):
    player, fight = case.rein, case.match.combat
    script, body = player.body_script, player.body
    actors = {t.entity: entity_hostility.Target(t.entity, 4 if case.match.game_map.free_for_all else t.team,
                                               False, None) for t in fight._targets()}
    groups = {key: entity_tags.Tags(base=tuple(tags) if key != body else ()) for key in actors}
    tag_targets = {key: entity_tags.Target(key, value) for key, value in groups.items()}
    filter_targets = {key: entity_filter.Target(key, FilterBits(1 if admit or key == body else 0),
                                                True, value, None, None) for key, value in groups.items()}
    hostility = entity_hostility.Context(body, True, lookup=actors.get, watch_environment=None,
                                         same_group=lambda source, target: False)
    tagged = entity_tags.Context(body, True, lookup=tag_targets.get, watch_tags=lambda *args: None)

    def relation(spectator, target, source):
        if spectator:
            return NotImplemented
        return entity_hostility.compare(actors.get(target.entity), actors.get(source.entity), False,
                                         lookup=actors.get, fallback_owner=lambda actor: None,
                                         same_group=lambda a, b: False)

    filtered = entity_filter.Context(body, True, lookup=filter_targets.get, source=filter_targets[body],
                                      relation_enemy=relation, watch_environment=None,
                                      watch_filter=lambda *args: None, watch_tags=lambda *args: None)
    for name, context in (('entity_hostility_context', hostility), ('entity_tag_context', tagged),
                           ('entity_filter_context', filtered)):
        case.enterContext(patch.object(script.world, name, lambda *args, context=context, **kwargs: context))
    isolate_damage_charge(player)
    return filter_targets, tag_targets, actors


class ReinhardtDamageChargeTests(unittest.TestCase):
    health = hero_tests.HammerTests.health
    place = staticmethod(hero_tests.HammerTests.place)

    def setUp(self):
        if hero_tests.BARRIER_DATA.is_file():
            self.enterContext(patch.dict(os.environ, {
                'ORPHEUS_REINHARDT_COLLISION': str(hero_tests.BARRIER_DATA),
            }))
        hero_tests.HammerTests.setUp(self)
        self.place(self.other, (20, 1, 0))

    def advance(self, count, buttons=0):
        for _ in range(count):
            self.run.step({self.rein: (0, 0, buttons)})

    def charge(self):
        return self.rein.body_script.component.entity_var(342).value()

    def test_actual_hammer_primary_and_alias_emit_net_damage_once_per_victim(self):
        for buttons in (1, 0x800):
            with self.subTest(buttons=buttons):
                self.setUp()
                install_backend(self)
                self.advance(45, buttons)
                self.assertEqual(self.health(self.enemy), 115)
                self.assertEqual(self.charge(), 85)

    def test_fire_strike_charge_uses_the_same_launch_team_as_damage(self):
        self.match.game_map = replace(self.match.game_map, free_for_all=False)
        self.rein.team, self.enemy.team, self.other.team = 0, 0, 1
        self.place(self.enemy, (0, 1, 6))
        self.place(self.other, (0, 1, 10))
        isolate_damage_charge(self.rein)
        self.advance(1, 16)
        self.advance(31)
        [shot] = self.match.combat.rockets
        self.assertEqual(shot.team, 0)
        self.assertFalse(shot.hit_targets)
        self.rein.team = 1
        self.advance(65)
        self.assertEqual(self.health(self.enemy), 200)
        self.assertEqual(self.health(self.other), 100)
        self.assertEqual({entity for entity, _ in shot.hit_targets}, {self.other.body})
        self.assertEqual(self.charge(), 100)

    def test_actual_fire_strike_lifetime_credit_does_not_repeat_for_one_victim(self):
        install_backend(self)
        self.place(self.enemy, (0, 1, 10))
        self.advance(1, 16)
        self.advance(95)
        self.assertEqual(self.health(self.enemy), 100)
        self.assertEqual(self.charge(), 100)
        self.advance(80)
        self.assertEqual(self.charge(), 100)

    def test_one_fire_strike_credits_old_and_replacement_bodies_separately(self):
        isolate_damage_charge(self.rein)
        fight = self.match.combat
        old_body = self.enemy.body
        old = fight.vitals_of(fight._target_entity(old_body))
        old.damage(old.total - 40)
        self.advance(1, 16)
        for _ in range(60):
            self.advance(1)
            if old.dead:
                break
        self.assertTrue(old.dead)
        [shot] = fight.rockets
        self.assertEqual(self.charge(), 40)
        self.advance(5)
        self.assertEqual(self.charge(), 40)

        self.assertTrue(self.match.switch_hero(self.enemy, self.enemy.hero))
        new_body = self.enemy.body
        self.assertNotEqual(new_body, old_body)
        self.place(self.enemy, (0, 1, 12))
        self.advance(60)
        self.assertEqual(self.health(self.enemy), 100)
        self.assertEqual(self.charge(), 140)
        self.assertEqual({entity for entity, _ in shot.hit_targets}, {old_body, new_body})
        hits = [message["+0x78"] for message in self.rein.client.messages(combat.HIT)]
        self.assertEqual(
            [(hit["+0x0"]["+0x0"], hit["+0x4"], bool(hit["+0x8"] & combat.KILLING)) for hit in hits],
            [(old_body, 40, True), (new_body, 100, False)],
        )

    def test_actual_charge_side_bump_credits_its_native_50_damage(self):
        self.place(self.enemy, (1.6, 1, 3))
        meter = self.charge()
        with patch.object(runtime, 'send_instance_message', wraps=runtime.send_instance_message) as sent:
            self.advance(1, 8)
            self.advance(80)
        self.assertEqual(self.health(self.enemy), 150)
        events = [call.args[2] for call in sent.call_args_list if call.args[1] == 0x0240000000000012]
        self.assertEqual([(event[0x13], event[0x15]) for event in events],
                         [(50, expr.Entity(self.enemy.body))])
        self.assertIn(self.charge() - meter, (50, 55, 60))

    def test_actual_pin_wall_damage_credits_resolved_armour_loss(self):
        self.match.switch_hero(self.enemy, heroes()[combat.REINHARDT])
        self.place(self.enemy, (0, 1, 3))
        hero_tests.charge_wall(self)
        fight = self.match.combat
        target = fight.vitals_of(fight._target_entity(self.enemy.body))
        meter = self.charge()
        with patch.object(runtime, 'send_instance_message', wraps=runtime.send_instance_message) as sent:
            self.advance(1, 8)
            self.advance(100)
        self.assertEqual(self.health(self.enemy), 205)
        self.assertEqual(target.now[combat.ARMOUR_POOL], 0)
        self.assertEqual(target.now[combat.HEALTH_POOL], 205)
        events = [call.args[2] for call in sent.call_args_list if call.args[1] == 0x0240000000000012]
        self.assertEqual([(event[0x13], event[0x15]) for event in events],
                         [(295, expr.Entity(self.enemy.body))])
        self.assertIn(self.charge() - meter, (295, 300, 305))

    def test_actual_damage_credit_clamps_at_the_effective_ultimate_cost(self):
        isolate_damage_charge(self.rein)
        self.rein.body_script.set_ult_charge(1530)
        self.advance(45, 1)
        self.assertEqual(self.health(self.enemy), 115)
        self.assertEqual(self.charge(), 1540)

    def fire_strike_below_ultimate_cost(self):
        self.place(self.enemy, (0, 1, 17))
        script = self.rein.body_script
        script.set_ult_charge(1500)
        fight = self.match.combat
        self.advance(1, 16)
        self.advance(31)
        [shot] = fight.rockets
        self.assertFalse(shot.hit_targets)
        self.assertLess(self.charge(), 1540)
        return shot

    def test_expired_earthshatter_press_does_not_cast_when_fire_strike_fills_meter(self):
        shot = self.fire_strike_below_ultimate_cost()
        script = self.rein.body_script
        fight = self.match.combat

        press_frame = self.run.frame + 1
        self.advance(1, 32)
        self.assertLess(self.charge(), 1540)
        self.assertFalse(any(state.flags & state.RUNNING for state in script.ultimates))
        for _ in range(80):
            self.advance(1)
            if shot.hit_targets:
                break
        self.assertGreater(self.run.frame - press_frame, 16)
        self.assertEqual(self.health(self.enemy), 100)
        self.assertEqual(self.charge(), 1540)

        self.advance(32)
        self.assertEqual(self.charge(), 1540)
        self.assertFalse(fight.shatters)
        self.assertEqual(self.health(self.enemy), 100)

        self.advance(1, 32)
        self.assertEqual(self.charge(), 0)
        self.advance(65)
        self.assertEqual(self.health(self.enemy), 50)

    def test_buffered_earthshatter_press_casts_when_fire_strike_fills_meter(self):
        shot = self.fire_strike_below_ultimate_cost()
        fight = self.match.combat
        for _ in range(60):
            if shot.position[2] >= 13.5:
                break
            self.advance(1)
        self.assertGreaterEqual(shot.position[2], 13.5)
        self.assertFalse(shot.hit_targets)
        self.assertLess(self.charge(), 1540)

        self.advance(1, 32)
        self.assertFalse(shot.hit_targets)
        self.assertLess(self.charge(), 1540)
        for release_frames in range(1, 10):
            self.advance(1)
            if shot.hit_targets:
                break
        self.assertIn(self.enemy.body, {entity for entity, _ in shot.hit_targets})
        self.assertEqual(self.health(self.enemy), 100)
        self.assertLessEqual(release_frames * .016, .15)

        for _ in range(65):
            self.advance(1)
            if self.rein.body in fight.shatters:
                break
        self.assertIn(self.rein.body, fight.shatters)
        self.assertEqual(self.charge(), 0)
        self.advance(40)
        self.assertEqual(self.health(self.enemy), 50)

    def test_held_earthshatter_casts_once_when_fire_strike_fills_meter(self):
        shot = self.fire_strike_below_ultimate_cost()
        script = self.rein.body_script
        fight = self.match.combat
        for held_frames in range(1, 81):
            self.advance(1, 32)
            if shot.hit_targets:
                break
            self.assertFalse(any(state.flags & state.RUNNING for state in script.ultimates))
            self.assertFalse(fight.shatters)
        self.assertGreater(held_frames, 16)
        self.assertEqual(self.health(self.enemy), 100)
        self.assertIn(self.enemy.body, {entity for entity, _ in shot.hit_targets})

        first_wave = None
        for _ in range(90):
            self.advance(1, 32)
            wave = fight.shatters.get(self.rein.body)
            if wave is not None:
                if first_wave is None:
                    first_wave = wave
                self.assertIs(wave, first_wave)
        self.assertIsNotNone(first_wave)
        self.assertFalse(fight.shatters)
        self.assertEqual(self.health(self.enemy), 50)
        self.assertLess(self.charge(), 10)

    def test_terminal_overkill_credits_only_remaining_pool_loss_and_dead_body_gives_no_more(self):
        isolate_damage_charge(self.rein)
        target = next(t for t in self.match.combat._targets() if t.owner is self.enemy)
        self.match.combat.vitals_of(target).damage(190)
        self.advance(45, 1)
        self.assertEqual(self.health(self.enemy), 0)
        self.assertEqual(self.charge(), 10)
        self.advance(50, 1)
        self.assertEqual(self.charge(), 10)

    def test_actual_friendly_rejection_does_not_gain_charge(self):
        self.match.game_map = replace(self.match.game_map, free_for_all=False)
        self.enemy.team = self.rein.team
        install_backend(self)
        self.advance(45, 1)
        self.assertEqual(self.health(self.enemy), 200)
        self.assertEqual(self.charge(), 0)

    def test_actual_range_miss_gives_no_charge(self):
        install_backend(self)
        self.place(self.enemy, (0, 1, 10))
        self.advance(45, 1)
        self.assertEqual(self.health(self.enemy), 200)
        self.assertEqual(self.charge(), 0)

    def test_actual_armour_loss_is_credited_after_damage_mitigation(self):
        self.match.switch_hero(self.enemy, heroes()[combat.REINHARDT])
        self.place(self.enemy, (0, 1, 3))
        isolate_damage_charge(self.rein)
        self.advance(45, 1)
        self.assertEqual(self.health(self.enemy), 420)
        self.assertEqual(self.charge(), 80)

    def test_normal_hammer_credits_base_shields_health_and_natural_terminal_loss(self):
        self.assertTrue(self.match.switch_hero(self.enemy, heroes()[defenses.ZENYATTA]))
        self.place(self.enemy, (0, 1, 3))
        fight = self.match.combat
        target = fight.vitals_of(fight._target_entity(self.enemy.body))
        self.assertEqual(target.now[combat.SHIELDS_POOL], 150)
        self.assertEqual(target.now[combat.HEALTH_POOL], 50)
        for button, loss, shields, health in ((1, 85, 65, 50), (0x800, 85, 0, 30), (1, 30, 0, 0)):
            with self.subTest(button=button, remaining_health=health):
                before = self.charge()
                with patch.object(runtime, 'send_instance_message', wraps=runtime.send_instance_message) as sent:
                    self.advance(45, button)
                events = [call.args[2] for call in sent.call_args_list if call.args[1] == 0x0240000000000012]
                self.assertEqual([(event[0x13], event[0x15]) for event in events],
                                 [(loss, expr.Entity(self.enemy.body))])
                self.assertIn(self.charge() - before, (loss, loss + 5))
                self.assertEqual(target.now[combat.SHIELDS_POOL], shields)
                self.assertEqual(target.now[combat.HEALTH_POOL], health)
                self.advance(25)
        self.assertTrue(target.dead)
        before = self.charge()
        with patch.object(runtime, 'send_instance_message', wraps=runtime.send_instance_message) as sent:
            self.advance(45, 0x800)
        self.assertFalse(any(call.args[1] == 0x0240000000000012 for call in sent.call_args_list))
        self.assertIn(self.charge() - before, (0, 5))

    def test_normal_match_awards_charge_from_actual_hammer(self):
        common = isolate_damage_charge(self.rein)
        self.advance(45, 1)
        self.assertEqual(self.health(self.enemy), 115)
        self.assertEqual(self.charge(), 85)
        self.advance(25)
        self.assertEqual(self.charge(), 85)
        self.advance(45, 1)
        self.assertEqual(self.health(self.enemy), 30)
        self.assertEqual(self.charge(), 170)

        # The fallback is restricted to the combat bridge's active event.
        params = {0x13: 50.0, 0x15: expr.Entity(self.enemy.body), 0x3D: (), 0x624: ()}
        runtime.send_instance_message(common, 0x0240000000000012, params, self.rein.body)
        common.component.run_now()
        self.assertEqual(self.charge(), 170)

        runtime.send_instance_message(common, 0x0240000000000012, params, self.rein.body)
        fight = self.match.combat
        fight._damage(self.rein, fight._target_entity(self.enemy.body), 10, False, None, self.run.frame)
        self.assertEqual(self.health(self.enemy), 20)
        self.assertEqual(self.charge(), 180)

    def test_unleased_fire_strike_cannot_charge_a_replacement_body(self):
        for recycle_body in (False, True):
            with self.subTest(recycle_body=recycle_body):
                self.check_fire_strike_source_retirement(recycle_body)

    def test_released_fire_strike_after_disconnect_deals_damage_without_charge_event(self):
        fight = self.match.combat
        self.advance(45, 1)
        self.assertEqual(self.health(self.enemy), 115)
        self.assertGreaterEqual(self.charge(), 85)
        self.place(self.enemy, (0, 1, 14))
        self.advance(1, 16)
        for _ in range(60):
            if fight.rockets:
                break
            self.advance(1)
        [shot] = fight.rockets
        self.assertIsNone(shot.shot)
        self.assertEqual(shot.charge_origin[0], self.rein.body)
        old_script = self.rein.body_script

        self.match.leave(self.rein)
        self.run.players.remove(self.rein)
        self.assertTrue(old_script.retired)
        self.assertIsNone(self.rein.retained_ult_charge)
        self.assertIn(shot, fight.rockets)
        with patch.object(runtime, 'send_instance_message', wraps=runtime.send_instance_message) as sent:
            for _ in range(80):
                self.run.step({})
                if self.health(self.enemy) < 115:
                    break
        self.assertEqual(self.health(self.enemy), 15)
        self.assertFalse(any(call.args[1] == 0x0240000000000012 for call in sent.call_args_list))
        self.assertIsNone(self.rein.retained_ult_charge)

    def check_fire_strike_source_retirement(self, recycle_body):
        self.setUp()
        self.place(self.enemy, (0, 1, 10))
        isolate_damage_charge(self.rein)
        fight = self.match.combat
        self.advance(1, 16)
        for _ in range(60):
            if fight.rockets:
                break
            self.advance(1)
        [rocket] = fight.rockets
        old = self.rein.body
        generation = fight.replacement_ticks[old]
        self.assertEqual(rocket.charge_origin, (old, generation))
        self.assertIsNone(rocket.shot)
        self.assertTrue(self.match.switch_hero(self.rein, heroes()[SOLDIER]))
        self.assertNotEqual(self.rein.body, old)
        allocation = (patch.object(self.rein.client, 'body_id_available', side_effect=lambda body: body == old)
                      if recycle_body else nullcontext())
        with allocation:
            self.assertTrue(self.match.switch_hero(self.rein, heroes()[combat.REINHARDT]))
        self.assertEqual(self.rein.body == old, recycle_body)
        self.assertGreater(fight.replacement_ticks[self.rein.body], generation)
        self.assertIn(rocket, fight.rockets)
        isolate_damage_charge(self.rein)
        self.advance(90)
        self.assertEqual(self.health(self.enemy), 100)
        self.assertEqual(self.charge(), 0)

        self.place(self.rein, (0, 1, 0))
        self.place(self.enemy, (0, 1, 3))
        self.advance(45, 1)
        self.assertEqual(self.health(self.enemy), 15)
        self.assertEqual(self.charge(), 85)

    def test_posthumous_fire_strike_credit_follows_reinhardt_through_respawn(self):
        fight = self.match.combat
        with patch.object(runtime, 'send_instance_message', wraps=runtime.send_instance_message) as sent:
            self.advance(45, 1)
        self.assertEqual(self.health(self.enemy), 115)
        self.assertEqual([call.args[2][0x13] for call in sent.call_args_list
                          if call.args[1] == 0x0240000000000012], [85.0])
        earned = self.charge()
        self.assertGreaterEqual(earned, 85)
        self.place(self.enemy, (0, 1, 17))
        self.place(self.other, (5, 1, 0))
        old_body = self.rein.body
        old_script = self.rein.body_script

        self.advance(1, 16)
        for _ in range(60):
            if fight.rockets:
                break
            self.advance(1)
        [shot] = fight.rockets
        self.assertEqual(shot.charge_origin[0], old_body)
        self.assertEqual(self.health(self.enemy), 115)
        victim = fight.vitals_of(fight._target_entity(old_body))
        victim.damage(victim.total - 1)
        yaw, pitch = aim(fight._eye(self.other), fight._eye(self.rein))
        self.run.step({self.other: (yaw, pitch, 1)})
        self.assertTrue(victim.dead)
        self.assertEqual(victim.killer, self.other.body)
        self.assertIs(self.rein.body_script, old_script)
        self.assertIn(shot, fight.rockets)

        for _ in range(80):
            before_hit = self.charge()
            self.advance(1)
            if self.health(self.enemy) != 115:
                break
        self.assertEqual(self.health(self.enemy), 15)
        self.assertEqual(self.rein.body, old_body)
        self.assertTrue(fight.is_dead(self.rein))
        self.assertEqual(self.charge() - before_hit, 100)
        self.assertEqual({entity for entity, _ in shot.hit_targets}, {self.enemy.body})

        for _ in range(800):
            before_respawn = self.charge()
            self.advance(1)
            if self.rein.body != old_body:
                break
        self.assertNotEqual(self.rein.body, old_body)
        self.assertTrue(old_script.retired)
        self.assertEqual(self.health(self.rein), 500)
        self.assertEqual(self.charge(), before_respawn)
        self.assertGreaterEqual(before_respawn, earned + 100)
        self.assertNotIn(shot, fight.rockets)
        self.assertTrue(self.match.switch_hero(self.rein, self.other.hero))
        self.assertTrue(self.match.switch_hero(self.rein, heroes()[combat.REINHARDT]))
        self.assertEqual(self.charge(), 0)

    def test_released_fire_strike_and_earthshatter_resolve_after_same_death(self):
        self.place(self.enemy, (0, 1, 17))
        self.place(self.other, (64, 1, 0))
        fight = self.match.combat
        old = self.rein.body
        script = self.rein.body_script
        isolate_damage_charge(self.rein)
        script.set_ult_charge(script.component.entity_var(338).value())

        self.run.step({self.rein: (16384, 0, 16)})
        for _ in range(65):
            if fight.rockets:
                break
            self.run.step({self.rein: (16384, 0, 0)})
        [shot] = fight.rockets
        self.assertEqual(shot.charge_origin[0], old)
        self.advance(45)
        self.advance(1, 32)
        for _ in range(50):
            wave = fight.shatters.get(old)
            if wave is not None and wave.travelled > 0:
                break
            self.advance(1)
        self.assertIsNotNone(wave)
        self.assertGreater(wave.travelled, 0)
        self.assertEqual(self.charge(), 0)
        self.assertIn(shot, fight.rockets)
        self.assertEqual(self.health(self.enemy), 200)
        self.assertEqual(self.health(self.other), 200)

        killer = self.match.add_player(4, 'Rifle', SOLDIER, 1, False)
        in_world(self.match, killer)
        self.assertTrue(self.match.switch_hero(killer, heroes()[SOLDIER]))
        self.place(killer, (-5, 1, 0))
        self.run.players.append(killer)
        victim = fight.vitals_of(fight._target_entity(old))
        victim.damage(victim.total - 1)
        angles = aim(fight._eye(killer), fight._eye(self.rein))
        self.run.step({killer: (*angles, 1)})
        self.assertTrue(victim.dead)
        self.assertEqual(victim.killer, killer.body)
        self.assertEqual(self.health(self.enemy), 200)
        self.assertEqual(self.health(self.other), 200)

        lock = script.component.entity_var(842)
        with patch.object(runtime, 'send_instance_message', wraps=runtime.send_instance_message) as sent:
            for _ in range(50):
                self.advance(1)
                if self.health(self.enemy) < 200:
                    break
            self.assertEqual(self.health(self.enemy), 150)
            self.assertEqual(self.health(self.other), 200)
            self.assertTrue(fight.is_dead(self.rein))
            self.assertIs(lock.value(), True)
            self.assertEqual(self.charge(), 0)
            for _ in range(125):
                self.advance(1)
                if self.health(self.other) < 200:
                    break
        self.assertIs(self.rein.body_script, script)
        self.assertTrue(fight.is_dead(self.rein))
        self.assertEqual(self.health(self.enemy), 150)
        self.assertEqual(self.health(self.other), 100)
        self.assertTrue(fight.controlled(self.enemy.body))
        events = {call.args[2][0x15].id: call.args[2] for call in sent.call_args_list
                  if call.args[1] == 0x0240000000000012}
        self.assertEqual(set(events), {self.enemy.body, self.other.body})
        self.assertEqual(events[self.enemy.body][0x13], 50)
        self.assertEqual(events[self.other.body][0x13], 100)
        self.assertIn(expr.Asset(0x0D800000000001FD), events[self.enemy.body][0x3D])
        self.assertNotIn(expr.Asset(0x0D800000000001FD), events[self.other.body][0x3D])
        self.assertIs(lock.value(), True)
        self.assertEqual(self.charge(), 0)

        for _ in range(800):
            self.advance(1)
            if self.rein.body != old:
                break
        self.assertNotEqual(self.rein.body, old)
        self.assertTrue(script.retired)
        self.assertEqual(self.health(self.rein), 500)
        self.assertEqual(self.charge(), 0)
        self.assertFalse(self.rein.body_script.component.entity_var(842).value())

        self.place(self.rein, (0, 1, 0))
        self.place(self.enemy, (0, 1, 3))
        self.advance(63)
        self.assertEqual(self.charge(), 5)
        isolate_damage_charge(self.rein)
        self.advance(45, 1)
        self.assertEqual(self.health(self.enemy), 65)
        self.assertEqual(self.charge(), 85)

    def test_normal_match_rejects_friendly_pool_loss_and_credits_terminal_enemy_loss(self):
        self.match.game_map = replace(self.match.game_map, free_for_all=False)
        self.enemy.team = self.rein.team
        isolate_damage_charge(self.rein)
        fight = self.match.combat
        target = fight._target_entity(self.enemy.body)
        fight._damage(self.rein, target, 190, False, None, self.run.frame)
        self.assertEqual(self.health(self.enemy), 10)
        self.assertEqual(self.charge(), 0)
        self.enemy.team = 1 - self.rein.team
        self.advance(45, 1)
        self.assertEqual(self.health(self.enemy), 0)
        self.assertEqual(self.charge(), 10)

    @unittest.skipUnless(hero_tests.BARRIER_DATA.is_file(), 'prepared barrier geometry required')
    def test_hammer_and_melee_alias_credit_armour_but_not_barrier_only_hits(self):
        for button in (1, 0x800):
            with self.subTest(button=button):
                self.setUp()
                self.assertTrue(self.match.switch_hero(self.enemy, heroes()[combat.REINHARDT]))
                self.place(self.enemy, (0, 1, 5))
                fight = self.match.combat

                def swing(frames, attack=0):
                    for _ in range(frames):
                        self.run.step({self.rein: (0, 0, attack), self.enemy: (-32768, 0, 2)})

                swing(35)
                shield = fight.barriers[self.enemy.body]
                target = fight.vitals_of(fight._target_entity(self.enemy.body))
                armour = target.now[combat.ARMOUR_POOL]
                self.assertTrue(shield.active)
                before = self.charge()
                with patch.object(runtime, 'send_instance_message', wraps=runtime.send_instance_message) as sent:
                    swing(45, button)
                self.assertEqual(shield.current, 1515)
                self.assertEqual(self.health(self.enemy), 500)
                self.assertFalse(any(call.args[1] == 0x0240000000000012 for call in sent.call_args_list))
                self.assertIn(self.charge() - before, (0, 5))

                swing(24)
                self.place(self.enemy, (0, 1, 3))
                before = self.charge()
                with patch.object(runtime, 'send_instance_message', wraps=runtime.send_instance_message) as sent:
                    swing(45, button)
                self.assertEqual(shield.current, 1430)
                self.assertEqual(self.health(self.enemy), 420)
                self.assertEqual(armour - target.now[combat.ARMOUR_POOL], 80)
                self.assertIn(self.charge() - before, (80, 85))
                events = [call.args[2] for call in sent.call_args_list if call.args[1] == 0x0240000000000012]
                self.assertEqual([(event[0x13], event[0x15]) for event in events],
                                 [(80.0, expr.Entity(self.enemy.body))])

    @unittest.skipUnless(hero_tests.BARRIER_DATA.is_file(), 'prepared barrier geometry required')
    def test_actual_fire_strike_credits_body_loss_without_adding_shield_damage(self):
        self.match.switch_hero(self.enemy, heroes()[combat.REINHARDT])
        self.place(self.enemy, (0, 1, 8))
        isolate_damage_charge(self.rein)
        for _ in range(35):
            self.run.step({self.enemy: (-32768, 0, 2)})
        shield = self.match.combat.barriers[self.enemy.body]
        self.assertTrue(shield.active)
        self.run.step({self.rein: (0, 0, 16), self.enemy: (-32768, 0, 2)})
        for _ in range(95):
            self.run.step({self.enemy: (-32768, 0, 2)})
        self.assertEqual(shield.current, 1500)
        self.assertEqual(self.health(self.enemy), 405)
        self.assertEqual(self.charge(), 95)

    def test_actual_damage_uses_native_multiplier_and_target_tag_reduction(self):
        install_backend(self, tags=(0x3C3, 0x11E))
        script = self.rein.body_script
        script.component.set(9532, 2.0)
        script.component.set(13022, 3.0)
        self.advance(45, 1)
        self.assertEqual(self.charge(), 255)  # 85 *2 *3 *0.5; half-tag takes priority

    def test_actual_filter_tag_admits_class_bit_rejected_target(self):
        install_backend(self, tags=(0x121,), admit=False)
        self.advance(45, 1)
        self.assertEqual(self.health(self.enemy), 115)
        self.assertEqual(self.charge(), 85)

    def test_real_hammer_damage_resumes_charge_only_after_earthshatter_lock(self):
        roadhog = next(hero for hero in heroes().values() if hero.name == 'Roadhog')
        self.assertTrue(self.match.switch_hero(self.enemy, roadhog))
        self.place(self.enemy, (0, 1, 3))
        self.place(self.other, (0, 1, 10))
        self.advance(2)
        isolate_damage_charge(self.rein)
        script = self.rein.body_script
        script.set_ult_charge(1540)
        self.advance(1, 32)
        self.advance(90)
        self.assertFalse(any(state.flags & state.RUNNING for state in script.ultimates))
        locked = script.component.entity_var(842)
        self.assertIs(locked.value(), True)
        self.assertEqual(self.health(self.enemy), 300)
        self.assertEqual(self.health(self.other), 150)
        self.assertEqual(self.charge(), 0)

        self.advance(1, 1)
        self.advance(45)
        self.assertEqual(self.health(self.enemy), 215)
        self.assertIs(locked.value(), True)
        self.assertEqual(self.charge(), 0)

        for _ in range(200):
            if not locked.value():
                break
            self.advance(1)
        self.assertFalse(locked.value())
        self.assertEqual(self.charge(), 0)
        self.place(self.enemy, (0, 1, 3))
        self.advance(1, 1)
        self.advance(45)
        self.assertEqual(self.health(self.enemy), 130)
        self.assertEqual(self.health(self.other), 150)
        self.assertEqual(self.charge(), 85)

    def test_one_fire_strike_can_hit_on_both_sides_of_earthshatter_charge_unlock(self):
        self.place(self.enemy, (20, 1, 3))
        isolate_damage_charge(self.rein)
        script = self.rein.body_script
        script.set_ult_charge(1540)
        self.advance(1, 32)
        self.advance(90)
        instance = next(i for i in script.component.instances.values() if i.graph.index == 0xBE)
        lock = instance.states[27]
        for _ in range(160):
            if lock.start + lock.ms - script.component.now <= 900:
                break
            self.advance(1)
        self.assertTrue(lock.active)
        self.assertFalse(self.match.combat.shatters)
        self.place(self.enemy, (0, 1, 5))
        self.place(self.other, (0, 1, 17))
        self.advance(1, 16)
        for _ in range(80):
            self.advance(1)
            if self.health(self.enemy) < 200:
                break
        self.assertTrue(lock.active)
        self.assertEqual(self.health(self.enemy), 100)
        self.assertEqual(self.health(self.other), 200)
        self.assertEqual(self.charge(), 0)
        [shot] = self.match.combat.rockets
        self.assertEqual({entity for entity, _ in shot.hit_targets}, {self.enemy.body})

        for _ in range(65):
            self.advance(1)
            if self.health(self.other) < 200:
                break
        self.assertFalse(lock.active)
        self.assertEqual(self.health(self.enemy), 100)
        self.assertEqual(self.health(self.other), 100)
        self.assertEqual({entity for entity, _ in shot.hit_targets}, {self.enemy.body, self.other.body})
        self.assertEqual(self.charge(), 100)

    def test_released_fire_strike_earns_charge_while_caster_is_knocked_down(self):
        self.assertTrue(self.match.switch_hero(self.other, heroes()[combat.REINHARDT]))
        self.place(self.other, (6, 1, 0))
        self.place(self.enemy, (0, 1, 17))
        self.advance(5)
        isolate_damage_charge(self.rein)
        self.other.body_script.set_ult_charge(1540)
        fight = self.match.combat
        self.advance(1, 16)
        self.advance(10)
        self.run.step({self.other: (-16384, 0, 32)})
        for _ in range(60):
            self.run.step({self.other: (-16384, 0, 0)})
            if fight.controlled(self.rein.body):
                break
        self.assertTrue(fight.controlled(self.rein.body))
        self.assertEqual(self.health(self.rein), 455)
        self.assertEqual(self.health(self.enemy), 200)
        self.assertEqual(self.charge(), 0)
        [shot] = fight.rockets
        self.assertIs(shot.shooter, self.rein)

        self.advance(45)
        self.assertTrue(fight.controlled(self.rein.body))
        self.assertEqual(self.health(self.enemy), 100)
        self.assertEqual({entity for entity, _ in shot.hit_targets}, {self.enemy.body})
        self.assertEqual(self.charge(), 100)

    def test_earthshatter_authored_multi_tags_reject_credit_even_without_the_busy_lock(self):
        install_backend(self)
        script = self.rein.body_script
        instance = next(i for i in script.component.instances.values() if i.graph.index == 0xBE)
        for index in (1, 41):
            state = instance.state(index)
            tags = combat.damage_tags(state, state.node.field('m_modifyHealth'))
            self.assertIn(0x0D800000000001FD, tags)
            target = next(t for t in self.match.combat._targets() if t.owner is self.enemy)
            self.match.combat._damage(self.rein, target, 50, False, None, self.run.frame, tags=tags)
        self.assertEqual(self.health(self.enemy), 100)
        self.assertEqual(self.charge(), 0)
        self.match.combat._damage(self.rein, target, 20, False, None, self.run.frame)
        self.assertEqual(self.charge(), 20)

    def test_actual_earthshatter_near_and_front_preserve_exclusion_tags_in_damage_events(self):
        self.match.switch_hero(self.enemy, next(h for h in heroes().values() if h.name == 'Roadhog'))
        self.place(self.enemy, (0, 1, 3))
        isolate_damage_charge(self.rein)
        self.rein.body_script.set_ult_charge(1540)
        with patch.object(runtime, 'send_instance_message', wraps=runtime.send_instance_message) as sent:
            self.advance(1, 32)
            self.advance(90)
        events = [call.args[2] for call in sent.call_args_list if call.args[1] == 0x0240000000000012]
        self.assertEqual([event[0x13] for event in events], [250, 50])
        self.assertTrue(all(0x0D800000000001FD in tuple(tag.guid for tag in event[0x3D]) for event in events))
        self.assertEqual(self.health(self.enemy), 300)
        self.assertEqual(self.charge(), 0)


@unittest.skipUnless((hero_tests.NPC_DATA / 'pve_npcs_174.json').is_file()
                     and hero_tests.NPC_MOVERS.is_file(), 'external NPC catalog and mover inputs required')
class ReinhardtNPCDamageChargeTests(unittest.TestCase):
    setUpClass = classmethod(hero_tests.TalonInteractionTests.setUpClass.__func__)
    tearDownClass = classmethod(hero_tests.TalonInteractionTests.tearDownClass.__func__)
    clear_graphs = staticmethod(hero_tests.TalonInteractionTests.clear_graphs)
    scenario = hero_tests.TalonInteractionTests.scenario
    health = hero_tests.HammerTests.health
    place = staticmethod(hero_tests.HammerTests.place)
    advance = hero_tests.HammerTests.advance
    charge = ReinhardtDamageChargeTests.charge

    def test_earthshatter_npc_and_player_damage_excludes_ultimate_credit(self):
        for definition in self.catalog.values():
            for near in (False, True):
                with self.subTest(npc=definition.name, near=near):
                    self.check_shatter_excludes_charge(definition, near=near)

    def check_shatter_excludes_charge(self, definition, *, near):
        bot, _, vitals = self.scenario(definition, (0, 1, 3 if near else 10))
        before = vitals.total
        self.place(self.enemy, (0, 1, 14))
        self.advance(2, buttons=0)
        isolate_damage_charge(self.rein)
        self.rein.body_script.set_ult_charge(1540)
        with patch.object(runtime, 'send_instance_message', wraps=runtime.send_instance_message) as sent:
            self.advance(1, buttons=32)
            self.advance(90, buttons=0)

        expected = []
        remaining = before
        for amount in ((250, 50) if near else (50,)):
            if remaining > 0:
                damage = min(remaining, amount)
                expected.append((bot.entity, damage))
                remaining -= damage
        expected.append((self.enemy.body, 50))
        hits = [message["+0x78"] for message in self.rein.client.messages(combat.HIT)]
        self.assertEqual([(hit["+0x0"]["+0x0"], hit["+0x4"]) for hit in hits], expected)
        events = [call.args[2] for call in sent.call_args_list if call.args[1] == 0x0240000000000012]
        self.assertEqual([(event[0x15].id, event[0x13]) for event in events], expected)
        for event in events:
            self.assertIn(0x0D800000000001FD, [tag.guid for tag in event[0x3D]])
        self.assertEqual(vitals.total, remaining)
        self.assertEqual(bot.dead, remaining == 0)
        self.assertEqual(self.health(self.enemy), 150)
        self.assertTrue(self.match.combat.controlled(self.enemy.body))
        self.assertEqual(self.charge(), 0)

        self.advance(200, buttons=0)
        self.assertFalse(self.match.combat.controlled(self.enemy.body))
        self.assertEqual(self.health(self.enemy), 150)
        self.assertEqual(self.charge(), 0)

    def test_normal_match_earns_then_spends_full_charge_with_actual_commands(self):
        heavy = next(definition for definition in self.catalog.values() if definition.name == 'Heavy Assault')
        bot, _, vitals = self.scenario(heavy)
        self.place(self.enemy, (0, 1, 10))
        before = vitals.total
        for _ in range(2500):
            meter, health = self.charge(), vitals.total
            self.advance(1, buttons=1)
            if self.charge() == 1540:
                break
        self.assertEqual(self.charge(), 1540)
        self.assertGreater(before - vitals.total, 1200)
        self.assertGreater(meter + health - vitals.total, 1540)
        self.advance(90, buttons=0)
        before_ultimate = vitals.total
        self.assertEqual(self.charge(), 1540)
        with patch.object(runtime, 'send_instance_message', wraps=runtime.send_instance_message) as sent:
            self.advance(1, buttons=32)
            self.assertEqual(self.charge(), 0)
            self.advance(90, buttons=0)
        events = [call.args[2] for call in sent.call_args_list if call.args[1] == 0x0240000000000012]
        self.assertEqual([(event[0x15].id, event[0x13]) for event in events],
                         [(bot.entity, 250), (bot.entity, 50), (self.enemy.body, 50)])
        self.assertTrue(all(0x0D800000000001FD in [tag.guid for tag in event[0x3D]] for event in events))
        self.assertEqual(before_ultimate - vitals.total, 300)
        self.assertEqual(self.health(self.enemy), 150)
        self.assertEqual(self.charge(), 0)
        script = self.rein.body_script
        shatter = next(instance for instance in script.component.instances.values() if instance.graph.index == 0xBE)
        lock = shatter.states[27]
        self.assertTrue(lock.active)
        for _ in range(200):
            if not lock.active:
                break
            self.assertEqual(self.charge(), 0)
            self.advance(1, buttons=0)
        self.assertFalse(lock.active)
        self.advance(63, buttons=0)
        self.assertEqual(self.charge(), 5)

        fight = self.match.combat
        old_body = self.rein.body
        self.place(self.enemy, (5, 1, 0))
        yaw, pitch = aim(fight._eye(self.enemy), fight._eye(self.rein))
        for _ in range(400):
            self.run.step({self.enemy: (yaw, pitch, 1)})
            if fight.is_dead(self.rein):
                break
        self.assertTrue(fight.is_dead(self.rein))
        dead = fight.vitals[self.rein.body]
        self.assertEqual(dead.last_damage_source, self.enemy.body)
        self.assertEqual(dead.killer, self.enemy.body)
        self.assertGreaterEqual(self.charge(), 5)
        for _ in range(800):
            before_respawn = self.charge()
            self.advance(1, buttons=0)
            if self.rein.body != old_body:
                break
        self.assertNotEqual(self.rein.body, old_body)
        self.assertEqual(self.health(self.rein), 500)
        self.assertEqual(self.charge(), before_respawn)
        self.assertLess(self.charge(), 1540)

    def test_actual_five_npc_primary_damage_events_credit_resolved_pool_loss(self):
        for key, definition in self.catalog.items():
            for button in (1, 0x800):
                with self.subTest(key=key, button=button):
                    bot, _, vitals = self.scenario(definition)
                    before, meter = vitals.total, self.charge()
                    with patch.object(runtime, 'send_instance_message', wraps=runtime.send_instance_message) as sent:
                        self.advance(45, buttons=button)
                    self.assertEqual(vitals.total, before - 85)
                    self.assertIn(self.charge() - meter, (85, 90))
                    events = [call.args[2] for call in sent.call_args_list if call.args[1] == 0x0240000000000012]
                    self.assertEqual([(event[0x13], event[0x15]) for event in events],
                                     [(85, expr.Entity(bot.entity))])

    def test_actual_five_npc_terminal_damage_events_clamp_to_remaining_life(self):
        for key, definition in self.catalog.items():
            with self.subTest(key=key):
                bot, _, vitals = self.scenario(definition)
                vitals.damage(vitals.total - 20)
                isolate_damage_charge(self.rein)
                self.advance(45, buttons=1)
                self.assertTrue(bot.dead)
                self.assertEqual(self.charge(), 20)
                self.advance(45, buttons=1)
                self.assertEqual(self.charge(), 20)

    def test_actual_five_npc_fire_strike_damage_events_credit_once_per_lifetime(self):
        for key, definition in self.catalog.items():
            with self.subTest(key=key):
                bot, _, vitals = self.scenario(definition, (0, 1, 10))
                before, meter = vitals.total, self.charge()
                with patch.object(runtime, 'send_instance_message', wraps=runtime.send_instance_message) as sent:
                    self.advance(1, buttons=16)
                    self.advance(95, buttons=0)
                self.assertEqual(vitals.total, before - 100)
                self.assertIn(self.charge() - meter, (100, 105, 110))
                events = [call.args[2] for call in sent.call_args_list if call.args[1] == 0x0240000000000012]
                self.assertEqual([(event[0x13], event[0x15]) for event in events],
                                 [(100, expr.Entity(bot.entity))])

    def test_fire_strike_kills_each_npc_and_still_hits_the_player_behind_it(self):
        for definition in self.catalog.values():
            with self.subTest(npc=definition.name):
                bot, _, vitals = self.scenario(definition, (0, 1, 3))
                self.place(self.enemy, (0, 1, 7))
                self.advance(2, buttons=0)
                vitals.damage(vitals.total - 40, bypass_armour=True)
                isolate_damage_charge(self.rein)
                self.advance(1, buttons=16)
                self.advance(95, buttons=0)

                self.assertTrue(bot.dead)
                self.assertEqual(vitals.total, 0)
                self.assertEqual(vitals.killer, self.rein.body)
                self.assertTrue(bot.body_script.component.owner_dead)
                self.assertEqual([entity for entity, _ in self.rein.client.deaths], [bot.entity])
                self.assertEqual(self.health(self.enemy), 100)
                self.assertEqual(self.charge(), 140)
                hits = [message["+0x78"] for message in self.rein.client.messages(combat.HIT)]
                self.assertEqual(
                    [(hit["+0x0"]["+0x0"], hit["+0x4"], bool(hit["+0x8"] & combat.KILLING)) for hit in hits],
                    [(bot.entity, 40, True), (self.enemy.body, 100, False)],
                )


if __name__ == '__main__':
    unittest.main()
