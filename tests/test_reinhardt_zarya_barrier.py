"""Actual Zarya barrier commands against Reinhardt attacks and incoming statuses."""

import unittest
from dataclasses import replace
from unittest.mock import patch

import test_game_reinhardt as fixtures
import test_reinhardt_ana_biotic as ana_fixture
from test_game_combat import aim, in_world
from test_reinhardt_damage_charge import isolate_damage_charge

from ow174.game import combat, defenses, deflection, hook, lucio_soundwave, stats, zarya_barrier
from ow174.game.content import SOLDIER, heroes
from ow174.game.script import expr, runtime


class ReinhardtZaryaBarrierTests(unittest.TestCase):
    place = staticmethod(fixtures.HammerTests.place)
    health = fixtures.HammerTests.health

    def arena(self, distance):
        fixtures.HammerTests.setUp(self)
        self.assertTrue(self.match.switch_hero(self.enemy, heroes()[zarya_barrier.HERO]))
        self.place(self.enemy, (0, 1, distance))
        self.place(self.other, (12, 1, 0))
        self.fight = self.match.combat
        self.step(20)
        self.step(zarya=8)
        return self.fight.zarya_bubbles[self.enemy.body]

    def step(self, frames=1, *, rein=0, zarya=0):
        for _ in range(frames):
            self.run.step({self.rein: (0, 0, rein), self.enemy: (-32768, 0, zarya)})

    def test_personal_barrier_blocks_actual_hammer_and_melee_alias(self):
        for button in (1, 0x800):
            with self.subTest(button=button):
                bubble = self.arena(3)
                self.assertAlmostEqual(bubble.until - self.fight.now, 2, delta=.017)
                self.step(rein=button)
                self.step(43)
                self.assertEqual(bubble.health, 115)
                self.assertEqual(self.health(self.enemy), 400)
                self.assertEqual(self.rein.stats.value(stats.HERO_DAMAGE), 0)
                energy = self.enemy.body_script.component.entity_var(87).value()
                self.assertAlmostEqual(energy, 17, places=4)

    def test_fire_strike_break_does_not_spill_and_next_hammer_hits(self):
        bubble = self.arena(3)
        bubble.health = 65
        self.step(rein=16)
        self.step(45)
        self.assertEqual(bubble.health, 0)
        self.assertEqual(self.health(self.enemy), 400)
        for _ in range(90):
            self.step(rein=1)
            if self.health(self.enemy) < 400:
                break
        self.assertEqual(self.health(self.enemy), 315)
        self.assertLess(self.fight.now, bubble.until)

    def test_fire_strike_credits_only_body_shield_loss_behind_active_bubble(self):
        bubble = self.arena(4)
        self.assertTrue(self.match.switch_hero(self.other, heroes()[defenses.ZENYATTA]))
        self.place(self.other, (0, 1, 8))
        target = self.fight.vitals_of(self.fight._target_entity(self.other.body))
        meter = self.rein.body_script.component.entity_var(342)
        before = meter.value()
        with patch.object(runtime, 'send_instance_message', wraps=runtime.send_instance_message) as sent:
            self.step(rein=16)
            for _ in range(80):
                self.step()
                if target.total < 200:
                    break
        events = [call.args[2] for call in sent.call_args_list if call.args[1] == 0x0240000000000012]
        self.assertEqual([(event[0x13], event[0x15]) for event in events],
                         [(100, expr.Entity(self.other.body))])
        self.assertEqual(bubble.health, 100)
        self.assertEqual(self.health(self.enemy), 400)
        self.assertEqual(target.now[combat.SHIELDS_POOL], 50)
        self.assertEqual(target.now[combat.HEALTH_POOL], 50)
        self.assertIn(meter.value() - before, (100, 105))

    def test_personal_barrier_denies_distant_shatter_control(self):
        bubble = self.arena(8)
        self.rein.body_script.set_ult_charge(1540)
        self.step(rein=32)
        self.step(55)
        self.assertEqual(bubble.health, 150)
        self.assertEqual(self.health(self.enemy), 400)
        self.assertFalse(self.fight.controlled(self.enemy.body))

    def test_projected_barrier_timing_on_live_earthshatter_wave(self):
        for cast_before_contact in (True, False):
            with self.subTest(cast_before_contact=cast_before_contact):
                self.check_projected_barrier_shatter_contact(cast_before_contact)

    def check_projected_barrier_shatter_contact(self, cast_before_contact):
        case = fixtures.HammerTests()
        case.setUp()
        fight = case.match.combat
        case.match.game_map = replace(case.match.game_map, free_for_all=False)
        case.rein.team, case.enemy.team, case.other.team = 0, 1, 1
        self.assertTrue(case.match.switch_hero(case.other, heroes()[zarya_barrier.HERO]))
        case.place(case.enemy, (0, 1, 14))
        case.place(case.other, (7, 1, 14))
        downstream = case.match.add_player(4, 'Downstream', SOLDIER, 1, False)
        in_world(case.match, downstream)
        self.assertTrue(case.match.switch_hero(downstream, heroes()[SOLDIER]))
        case.place(downstream, (0, 1, 18))
        case.run.players.append(downstream)
        case.advance(20, buttons=0)
        script = case.rein.body_script
        isolate_damage_charge(case.rein)
        script.set_ult_charge(script.component.entity_var(338).value())
        case.advance(1, buttons=32)
        for _ in range(50):
            case.advance(1, buttons=0)
            wave = fight.shatters.get(case.rein.body)
            if wave is not None and wave.travelled > 0:
                break
        self.assertIsNotNone(wave)
        self.assertGreater(wave.travelled, 0)
        self.assertLess(wave.travelled, 8)
        self.assertEqual(case.health(case.enemy), 200)
        self.assertNotIn(case.enemy.body, fight.zarya_bubbles)
        key = (case.enemy.body, fight.replacement_ticks.get(case.enemy.body))
        downstream_key = (downstream.body, fight.replacement_ticks.get(downstream.body))

        if not cast_before_contact:
            for _ in range(45):
                case.advance(1, buttons=0)
                if key in wave.hit:
                    break
            self.assertIn(key, wave.hit)
            self.assertEqual(case.health(case.enemy), 150)
            self.assertTrue(fight.controlled(case.enemy.body))
        self.assertNotIn(downstream_key, wave.hit)
        angles = aim(fight._eye(case.other), (0, 2.1, 14))
        case.run.step({case.other: (*angles, 16)})
        bubble = fight.zarya_bubbles[case.enemy.body]
        self.assertEqual(bubble.source, case.other.body)
        self.assertEqual(bubble.health, 200)

        case.advance(45, buttons=0)
        self.assertIn(key, wave.hit)
        self.assertIn(downstream_key, wave.hit)
        self.assertEqual(case.health(downstream), 150)
        self.assertTrue(fight.controlled(downstream.body))
        self.assertEqual(case.health(case.enemy), 200 if cast_before_contact else 150)
        self.assertEqual(fight.controlled(case.enemy.body), not cast_before_contact)
        self.assertEqual(bubble.health, 150 if cast_before_contact else 200)
        energy = case.other.body_script.component.entity_var(87).value()
        self.assertAlmostEqual(energy or 0, 10 if cast_before_contact else 0, places=4)
        self.assertEqual(script.component.entity_var(342).value(), 0)

    def test_near_shatter_breaks_bubble_before_separate_wave_hits_body(self):
        bubble = self.arena(3)
        self.rein.body_script.set_ult_charge(1540)
        self.step(rein=32)
        for _ in range(45):
            self.step()
            if bubble.health == 0:
                break
        self.assertEqual(bubble.health, 0)
        self.assertEqual(self.health(self.enemy), 400)
        self.assertFalse(self.fight.controlled(self.enemy.body))
        for _ in range(15):
            self.step()
            if self.fight.controlled(self.enemy.body):
                break
        self.assertEqual(self.health(self.enemy), 350)
        self.assertTrue(self.fight.controlled(self.enemy.body))

    def test_charge_capture_and_wall_hit_consume_bubble_without_body_damage(self):
        bubble = self.arena(3)
        fixtures.charge_wall(self, 6)
        self.step(rein=8)
        for _ in range(70):
            self.step()
            if self.fight.pinned(self.enemy.body):
                break
        self.assertTrue(self.fight.pinned(self.enemy.body))
        self.assertEqual(bubble.health, 200)
        for _ in range(40):
            self.step()
            if bubble.health == 0:
                break
        self.assertEqual(bubble.health, 0)
        self.assertFalse(self.fight.pinned(self.enemy.body))
        self.assertEqual(self.health(self.enemy), 400)
        self.assertAlmostEqual(self.enemy.body_script.component.entity_var(87).value(), 40, places=4)

    def projected_case(self):
        case = ana_fixture.ReinhardtAnaBioticTests()
        self.addCleanup(case.doCleanups)
        case.setup_teams()
        self.assertTrue(case.match.switch_hero(case.other, heroes()[zarya_barrier.HERO]))
        case.place(case.other, (3, 1, 1))
        case.advance(20)
        return case

    def cast_projected(self, case):
        yaw, pitch = aim(case.fight._eye(case.other), (0, 2.3, 0))
        case.run.step({case.other: (yaw, pitch, 16)})
        for _ in range(12):
            if case.rein.body in case.fight.zarya_bubbles:
                break
            case.run.step({case.other: (yaw, pitch, 16)})
        return case.fight.zarya_bubbles.get(case.rein.body)

    def test_projected_barrier_cleanses_existing_grenade_and_expires(self):
        case = self.projected_case()
        case.grenade()
        self.assertTrue(case.victim.healing_block)
        bubble = self.cast_projected(case)
        self.assertIsNotNone(bubble)
        self.assertEqual(bubble.source, case.other.body)
        self.assertEqual(bubble.health, 200)
        self.assertFalse(case.victim.healing_block)
        self.assertEqual(case.victim.total, 455)
        case.advance(145)
        self.assertNotIn(case.rein.body, case.fight.zarya_bubbles)
        self.assertEqual(case.rein.hero.guid, combat.REINHARDT)

    def test_projected_barrier_blocks_hook_impact_or_releases_an_existing_pull(self):
        for before_impact in (True, False):
            with self.subTest(before_impact=before_impact):
                case = self.projected_case()
                self.assertTrue(case.match.switch_hero(case.enemy, heroes()[hook.ROADHOG]))
                case.place(case.enemy, (0, 1, 8))
                fight = case.fight
                if before_impact:
                    bubble = self.cast_projected(case)
                    self.assertIsNotNone(bubble)
                case.run.step({case.enemy: (-32768, 0, 8)})
                launched = False
                for _ in range(60):
                    case.run.step({case.enemy: (-32768, 0, 0)})
                    launched |= bool(fight.hooks.flights)
                    if fight.hooks.pulling(case.rein.body) or launched and not fight.hooks.flights:
                        break
                self.assertTrue(launched)
                if before_impact:
                    self.assertEqual(case.victim.total, 500)
                    self.assertEqual(bubble.health, 170)
                    self.assertAlmostEqual(case.other.body_script.component.entity_var(87).value(), 6, places=4)
                else:
                    self.assertTrue(fight.hooks.pulling(case.rein.body))
                    self.assertEqual(case.victim.total, 475)
                    case.run.step({case.enemy: (-32768, 0, 0)})
                    for _ in range(12):
                        x, y, z = case.rein.mover.position
                        angles = aim(fight._eye(case.other), (x, y + 1.3, z))
                        case.run.step({case.other: (*angles, 16), case.enemy: (-32768, 0, 0)})
                        bubble = fight.zarya_bubbles.get(case.rein.body)
                        if bubble is not None:
                            break
                    self.assertIsNotNone(bubble)
                    case.advance(1)
                    self.assertEqual(bubble.health, 200)
                    self.assertEqual(case.victim.total, 475)
                self.assertFalse(fight.hooks.flights)
                self.assertFalse(fight.controlled(case.rein.body))
                position = tuple(case.rein.mover.position)
                case.advance(12)
                self.assertEqual(tuple(case.rein.mover.position), position)
                self.assertEqual(case.victim.total, 500 if before_impact else 475)

    def test_projected_barrier_absorbs_actual_shield_bash_without_late_control(self):
        for mode in ("full", "break", "expired"):
            with self.subTest(mode=mode):
                case = self.projected_case()
                self.assertTrue(case.match.switch_hero(case.enemy, heroes()[0x02E0000000000195]))
                case.place(case.enemy, (0, 1, 2.7))
                bubble = self.cast_projected(case)
                self.assertIsNotNone(bubble)
                self.assertEqual(bubble.health, 200)
                if mode == "break":
                    bubble.health = 2
                hold = {case.enemy: (-32768, 0, 2)}
                for _ in range(135 if mode == "expired" else 35):
                    case.run.step(hold)
                if mode == "expired":
                    self.assertNotIn(case.rein.body, case.fight.zarya_bubbles)
                start = tuple(case.rein.mover.position)
                energy_before = case.other.body_script.component.entity_var(87).value()
                case.run.step({case.enemy: (-32768, 0, 3)})
                dash = case.fight.brigitte_barriers.shields[case.enemy.body].instance.states[14]
                self.assertTrue(dash.active)
                self.assertEqual(case.victim.total, 500)
                case.run.step(hold)
                self.assertFalse(dash.active)
                energy = case.other.body_script.component.entity_var(87).value()
                if mode == "expired":
                    self.assertEqual(case.victim.total, 497.5)
                    self.assertTrue(case.fight.controlled(case.rein.body))
                    self.assertEqual(len(case.enemy.client.messages(combat.HIT)), 1)
                    self.assertEqual(energy, energy_before)
                else:
                    self.assertEqual(bubble.health, 195 if mode == "full" else 0)
                    self.assertAlmostEqual(energy, 1 if mode == "full" else .4, places=4)
                    for _ in range(12):
                        case.run.step(hold)
                    self.assertEqual(case.victim.total, 500)
                    self.assertFalse(case.fight.controlled(case.rein.body))
                    self.assertEqual(tuple(case.rein.mover.position), start)
                    self.assertEqual(case.enemy.client.messages(combat.HIT), [])

    def test_projected_barrier_cast_during_bash_protects_reinhardt_before_contact(self):
        case = self.projected_case()
        self.assertTrue(case.match.switch_hero(case.enemy, heroes()[0x02E0000000000195]))
        case.place(case.enemy, (0, 1, 4.5))
        hold = {case.enemy: (-32768, 0, 2)}
        for _ in range(35):
            case.run.step(hold)
        case.run.step({case.enemy: (-32768, 0, 3)})
        dash = case.fight.brigitte_barriers.shields[case.enemy.body].instance.states[14]
        self.assertTrue(dash.active)
        self.assertNotIn(case.rein.body, case.fight.zarya_bubbles)
        yaw, pitch = aim(case.fight._eye(case.other), (0, 2.3, 0))
        case.run.step({**hold, case.other: (yaw, pitch, 16)})
        self.assertTrue(dash.active)
        self.assertLess(case.enemy.mover.position[2], 4.5)
        bubble = case.fight.zarya_bubbles[case.rein.body]
        self.assertEqual(bubble.health, 200)
        for _ in range(8):
            case.run.step(hold)
            if not dash.active:
                break
        self.assertFalse(dash.active)
        self.assertEqual(bubble.health, 195)
        self.assertEqual(case.victim.total, 500)
        self.assertFalse(case.fight.controlled(case.rein.body))
        self.assertEqual(case.enemy.client.messages(combat.HIT), [])

    def test_projected_barrier_blocks_actual_soundwave_damage_and_impulse(self):
        for mode in ("full", "break", "expired"):
            with self.subTest(mode=mode):
                case = self.projected_case()
                self.assertTrue(case.match.switch_hero(case.enemy, heroes()[lucio_soundwave.HERO]))
                case.place(case.enemy, (0, 1, 4))
                bubble = self.cast_projected(case)
                self.assertIsNotNone(bubble)
                self.assertEqual(bubble.health, 200)
                if mode == "break":
                    bubble.health = 15
                if mode == "expired":
                    case.advance(135)
                    self.assertNotIn(case.rein.body, case.fight.zarya_bubbles)
                start = tuple(case.rein.mover.position)
                wave = next(i for i in case.enemy.body_script.component.instances.values()
                            if i.graph.index == lucio_soundwave.GRAPH)
                with patch.object(case.rein.mover, "add_impulse", wraps=case.rein.mover.add_impulse) as impulse:
                    case.run.step({case.enemy: (-32768, 0, 2)})
                    self.assertTrue(wave.states[11].active)
                    case.advance(10)
                if mode == "expired":
                    self.assertEqual(case.victim.total, 480)
                    impulse.assert_called_once()
                    self.assertGreater(case.rein.mover.position[1], start[1])
                else:
                    self.assertEqual(case.victim.total, 500)
                    self.assertEqual(tuple(case.rein.mover.position), start)
                    impulse.assert_not_called()
                    remaining, gained = (175, 5) if mode == "full" else (0, 3)
                    self.assertEqual(bubble.health, remaining)
                    energy = case.other.body_script.component.entity_var(87).value()
                    self.assertAlmostEqual(energy, gained, places=4)

    def test_projected_barrier_blocks_actual_dart_and_grenade_status(self):
        for button, remaining in ((8, 195), (16, 150)):
            with self.subTest(button=button):
                case = self.projected_case()
                bubble = self.cast_projected(case)
                self.assertIsNotNone(bubble)
                case.advance(ana=button)
                case.advance(65)
                self.assertEqual(bubble.health, remaining)
                self.assertEqual(case.victim.total, 500)
                self.assertFalse(case.victim.healing_block)
                self.assertNotIn(case.rein.body, case.fight.sleeps)

    @unittest.skipUnless(fixtures.BARRIER_DATA.is_file(), 'prepared Barrier Field required')
    def test_enemy_fire_strike_spends_field_and_projected_barrier_before_body(self):
        case = self.projected_case()
        self.assertTrue(case.match.switch_hero(case.enemy, heroes()[combat.REINHARDT]))
        case.place(case.enemy, (0, 1, 6))
        case.advance(20, rein=2)
        field = case.fight.barriers[case.rein.body]
        self.assertTrue(field.active)
        bubble = self.cast_projected(case)
        self.assertIsNotNone(bubble)
        case.advance(15, rein=2)
        self.assertTrue(field.active)
        case.run.step({case.enemy: (-32768, 0, 16), case.rein: (0, 0, 2)})
        for _ in range(65):
            case.run.step({case.enemy: (-32768, 0, 0), case.rein: (0, 0, 2)})
            if bubble.health < 200:
                break
        self.assertEqual(field.current, 1500)
        self.assertEqual(bubble.health, 100)
        self.assertEqual(case.victim.total, 500)
        self.assertEqual(case.enemy.stats.value(stats.HERO_DAMAGE), 0)
        self.assertAlmostEqual(case.other.body_script.component.entity_var(87).value(), 20, places=4)

    def test_projected_barrier_cleanses_an_actual_sleep(self):
        case = self.projected_case()
        case.advance(ana=8)
        for _ in range(65):
            case.advance()
            if case.rein.body in case.fight.sleeps:
                break
        self.assertTrue(case.fight.is_stunned(case.rein))
        bubble = self.cast_projected(case)
        self.assertIsNotNone(bubble)
        self.assertNotIn(case.rein.body, case.fight.sleeps)
        self.assertFalse(case.fight.is_stunned(case.rein))
        self.assertEqual(case.victim.total, 497.5)

    def late_projected_barrier(self):
        case = self.projected_case()
        self.assertTrue(case.match.switch_hero(case.enemy, heroes()[deflection.GENJI]))
        case.place(case.enemy, (0, 1, 22))
        isolate_damage_charge(case.rein)
        isolate_damage_charge(case.enemy)
        case.run.step({case.rein: (0, 0, 16), case.enemy: (-32768, 0, 16)})
        fight = case.fight
        for _ in range(108):
            case.run.step({case.enemy: (-32768, 0, 0)})
            if fight.rockets and fight.rockets[0].reflected:
                break
        [shot] = fight.rockets
        self.assertTrue(shot.reflected)
        self.assertIs(shot.shooter, case.enemy)
        self.assertEqual(shot.team, case.enemy.team)
        self.assertGreater(shot.position[2], 12)
        field = fight.barriers[case.rein.body]
        self.assertFalse(field.active)
        self.assertNotIn(case.rein.body, fight.zarya_bubbles)

        bubble = self.cast_projected(case)
        self.assertIsNotNone(bubble)
        self.assertIn(shot, fight.rockets)
        self.assertEqual(bubble.source, case.other.body)
        self.assertEqual(bubble.health, 200)
        return case, field, bubble

    def assert_reflected_barrier_contacts(self, case, field, bubble):
        for _ in range(85):
            case.run.step({case.rein: (0, 0, 2)})
            if bubble.health < 200:
                break
        self.assertTrue(field.active)
        self.assertEqual(field.current, 1500)
        self.assertEqual(bubble.health, 100)
        self.assertEqual(case.victim.total, 500)
        self.assertEqual(case.health(case.enemy), 200)
        for player in (case.rein, case.enemy):
            self.assertEqual(player.body_script.component.entity_var(342).value(), 0)
            self.assertFalse(player.client.messages(combat.HIT))
        for _ in range(20):
            case.run.step({case.rein: (0, 0, 2)})
        self.assertEqual(field.current, 1500)
        self.assertEqual(bubble.health, 100)
        self.assertEqual(case.victim.total, 500)

    @unittest.skipUnless(fixtures.BARRIER_DATA.is_file(), 'prepared Barrier Field required')
    def test_late_field_and_projected_barrier_protect_from_reflected_fire_strike(self):
        case, field, bubble = self.late_projected_barrier()
        self.assert_reflected_barrier_contacts(case, field, bubble)
        self.assertFalse(bubble.source_retired)
        self.assertAlmostEqual(case.other.body_script.component.entity_var(87).value(), 20, places=4)

    @unittest.skipUnless(fixtures.BARRIER_DATA.is_file(), 'prepared Barrier Field required')
    def test_reflected_hit_on_projected_barrier_cannot_credit_recycled_caster(self):
        case, field, bubble = self.late_projected_barrier()
        old_body = case.other.body
        original = case.other.body_script
        old_generation = case.fight.replacement_ticks[old_body]
        self.assertTrue(case.match.switch_hero(case.other, heroes()[SOLDIER]))
        with patch.object(case.other.client, 'body_id_available', side_effect=lambda body: body == old_body):
            self.assertTrue(case.match.switch_hero(case.other, heroes()[zarya_barrier.HERO]))
        self.assertEqual(case.other.body, old_body)
        self.assertGreater(case.fight.replacement_ticks[old_body], old_generation)
        self.assertIsNot(case.other.body_script, original)
        self.assertTrue(bubble.source_retired)
        case.place(case.other, (3, 1, 1))
        self.assert_reflected_barrier_contacts(case, field, bubble)
        self.assertIsNone(case.other.body_script.component.entity_var(87).value())

    def test_projected_barrier_denies_enemy_range_and_wall(self):
        for reason in ('team', 'range', 'wall'):
            with self.subTest(reason=reason):
                case = self.projected_case()
                if reason == 'team':
                    case.other.team = 1
                elif reason == 'range':
                    case.place(case.other, (0, 1, 32))
                    case.enemy.team = 1
                else:
                    fixtures.charge_wall(case, .5)
                self.assertIsNone(self.cast_projected(case))

    def test_projected_barrier_waits_for_actual_ice_wall_removal(self):
        case = self.projected_case()
        self.assertTrue(case.match.switch_hero(case.enemy, heroes()[defenses.MEI]))
        case.place(case.enemy, (0, 1, 7))
        case.place(case.other, (0, 1, 6))
        case.advance(15)
        angles = aim(case.fight._eye(case.enemy), (0, 1, 3))
        for button in (16, 0, 1, 0):
            case.run.step({case.enemy: (*angles, button)})
        self.assertEqual(len(case.fight.walls.pieces), 5)
        self.assertIsNone(self.cast_projected(case))
        case.run.step({case.enemy: (*angles, 16)})
        case.run.step({case.enemy: (*angles, 0)})
        self.assertFalse(case.fight.walls.pieces)
        self.assertIsNotNone(self.cast_projected(case))

    def test_projected_barrier_is_retired_with_recipient_body(self):
        from ow174.game.body_vm_snapshot import export_component_vm

        case = self.projected_case()
        self.assertIsNotNone(self.cast_projected(case))
        self.assertTrue(export_component_vm(case.other.body_script.component))
        old = case.rein.body
        self.assertTrue(case.match.switch_hero(case.rein, heroes()[combat.REINHARDT]))
        self.assertNotEqual(case.rein.body, old)
        self.assertNotIn(old, case.fight.zarya_bubbles)
        case.advance(10)
        self.assertNotIn(case.rein.body, case.fight.zarya_bubbles)
