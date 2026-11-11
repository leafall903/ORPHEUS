"""Normal manual Enforcer spawn uses actual13EB;no controller/health query mocks.

25-pellet resources execute with declared deterministic tangent-ring projection.
This is server combat reconstruction,not native AI,phase or muzzle parity.
"""

import itertools
import math
import os
import unittest
from dataclasses import replace
from unittest.mock import patch

import test_game_reinhardt as fixtures
import test_reinhardt_npc_incoming as incoming
from test_game_combat import aim
from test_reinhardt_damage_charge import isolate_damage_charge

from ow174.game import defenses, deflection, npc, npc_control, sigma_grasp, zarya_barrier
from ow174.game.combat import REINHARDT
from ow174.game.content import SOLDIER, heroes


@unittest.skipUnless((fixtures.NPC_DATA / 'pve_npcs_174.json').is_file()
                     and fixtures.NPC_MOVERS.is_file() and fixtures.BARRIER_DATA.is_file(),
                     'authorized NPC/mover/barrier data required')
class EnforcerIncomingTests(unittest.TestCase):
    advance = fixtures.HammerTests.advance
    place = staticmethod(fixtures.HammerTests.place)
    scenario = fixtures.TalonInteractionTests.scenario
    mount_mech_inputs = incoming.ReinhardtNpcIncomingTests.mount_mech_inputs

    @classmethod
    def setUpClass(cls):
        fixtures.TalonInteractionTests.setUpClass()
        cls.catalog = fixtures.TalonInteractionTests.catalog

    @classmethod
    def tearDownClass(cls):
        fixtures.TalonInteractionTests.tearDownClass()

    def setUp(self):
        self.enterContext(patch.dict(os.environ, {
            'ORPHEUS_REINHARDT_COLLISION': str(fixtures.BARRIER_DATA)}))

    def engage(self, distance, shield=False):
        actor, target, _ = self.scenario(self.catalog['2'], (0, 1, distance))
        self.advance(20, buttons=2 if shield else 0)
        self.rein.mover.yaw, self.rein.mover.pitch = aim(
            self.match.combat._eye(self.rein), (0, 2, distance))
        self.assertEqual(npc_control.start(self.match, self.rein), actor.entity)
        health = self.match.combat.vitals_of(self.match.combat._target_entity(self.rein.body))
        return actor, target, health

    def launch(self, shield=False):
        for _ in range(220):
            self.advance(1, buttons=2 if shield else 0)
            if self.match.combat.rockets:
                break
        pellets = list(self.match.combat.rockets)
        self.assertEqual(len(pellets), 25)
        self.assertEqual(len({pellet.direction for pellet in pellets}), 25)
        for pellet in pellets:
            self.assertAlmostEqual(math.sqrt(sum(v * v for v in pellet.direction)), 1)
            self.assertEqual(pellet.direct.amount, 2)
            self.assertEqual(pellet.speed, 80)
        return pellets

    def test_authored_25_pellets_damage_armour_and_shield_at_three_distances(self):
        for distance, body_loss, shield_loss in ((2, 25, 50), (8, 20, 50), (20, 1.4, 10)):
            for shield in (False, True):
                with self.subTest(distance=distance, shield=shield):
                    actor, _, health = self.engage(distance, shield)
                    self.launch(shield)
                    # Release future input;already emitted projectiles still fly.
                    actor.firing = False
                    actor.attack_target = None
                    self.advance(100, buttons=2 if shield else 0)
                    self.assertIsNotNone(actor.body_script)
                    self.assertFalse(self.match.combat.rockets)
                    barrier = self.match.combat.barriers[self.rein.body]
                    self.assertAlmostEqual(health.total, 500 if shield else 500 - body_loss, places=5)
                    self.assertAlmostEqual(1600 - barrier.current, shield_loss if shield else 0, places=2)
                    self.assertEqual(health.now[0], 300)
                    self.assertFalse(actor.attack_input)

    def test_kinetic_grasp_consumes_all_25_enforcer_pellets_and_converts_damage(self):
        self.check_enforcer_interception(heroes()[sigma_grasp.SIGMA], sigma_grasp.GRAPH, 1, 8)

    def test_matrix_consumes_all_25_enforcer_pellets(self):
        self.mount_mech_inputs()
        mech = replace(heroes()[deflection.DVA], body=deflection.DVA_MECH)
        self.check_enforcer_interception(mech, 0x02CC, 3, 2)

    def test_genji_deflects_all_25_enforcer_pellets_back_to_the_source(self):
        self.check_enforcer_interception(heroes()[deflection.GENJI], 0x03A3, 10, 16)

    def check_enforcer_interception(self, hero, graph_index, state_index, button):
        actor, source, health = self.engage(8)
        fight = self.match.combat
        self.match.game_map = replace(self.match.game_map, free_for_all=False)
        self.assertTrue(self.match.switch_hero(self.enemy, hero))
        self.rein.team = self.enemy.team = 0
        self.advance(4, buttons=0)
        pellets = self.launch()
        self.assertTrue(all(pellet.definition == deflection.ENFORCER_PELLET for pellet in pellets))
        self.place(self.enemy, (0, 1, 3.5))
        reflects = hero.guid == deflection.GENJI
        angles = aim(fight._eye(self.enemy), (0, 2, 8)) if reflects else (0, 0)
        self.run.step({self.enemy: (*angles, button)})
        state = defenses.active_state(self.enemy, hero.guid, graph_index, state_index)
        self.assertIsNotNone(state)
        receiver = fight.vitals_of(fight._target_entity(self.enemy.body))
        source_health = fight.vitals_of(source)
        receiver_before, source_before = receiver.total, source_health.total
        with patch.object(fight, "_intercept_rocket", wraps=fight._intercept_rocket) as intercept:
            for _ in range(35):
                self.run.step({self.enemy: (*angles, 2 if button == 2 else 0)})
                if all(pellet not in fight.rockets for pellet in pellets):
                    break
        self.assertTrue(all(pellet not in fight.rockets for pellet in pellets))
        self.assertEqual(len(intercept.call_args_list), 25)
        self.assertEqual({id(call.args[0]) for call in intercept.call_args_list}, {id(pellet) for pellet in pellets})
        self.assertTrue(all(call.args[1].owner is self.enemy and call.args[2] == reflects
                            and call.args[3] is state for call in intercept.call_args_list))
        self.assertEqual(health.total, 500)
        self.assertEqual(receiver.total, receiver_before)
        self.assertEqual(source_health.total, source_before - 50 if reflects else source_before)
        self.assertEqual(npc_control.stop(self.match), 1)
        if hero.guid == sigma_grasp.SIGMA:
            self.assertEqual(state.instance.vars[21312].value(), 50)
            self.advance(140, buttons=0)
            self.assertFalse(state.active)
            self.assertAlmostEqual(receiver.grasp_shields, 30, places=4)

    def test_held_primary_drains_full_barrier_without_armour_spill(self):
        _, _, health = self.engage(8, shield=True)
        fight = self.match.combat
        barrier = fight.barriers[self.rein.body]
        self.assertEqual(barrier.current, 1600)

        for _ in range(5600):
            previous = barrier.current
            self.advance(1, buttons=2)
            self.assertLessEqual(barrier.current, previous)
            self.assertEqual(health.total, 500)
            if not barrier.active:
                break
        self.assertEqual(barrier.current, 0)
        self.assertFalse(barrier.active)
        self.assertGreater(barrier.broken_until, fight.now)
        self.assertEqual(npc_control.stop(self.match), 1)

    def test_barrier_break_consumes_one_pellet_then_same_volley_reaches_armour(self):
        actor, _, health = self.engage(2, shield=True)
        fight = self.match.combat
        barrier = fight.barriers[self.rein.body]
        barrier.current = 1
        self.launch(shield=True)
        self.assertEqual(health.total, 500)
        actor.firing = False
        actor.attack_target = None
        self.advance(20, buttons=2)
        self.assertEqual(barrier.current, 0)
        self.assertFalse(barrier.active)
        self.assertGreater(barrier.broken_until, fight.now)
        self.assertFalse(fight.rockets)
        self.assertEqual(health.total, 476)
        self.assertEqual(health.now[0], 300)

    def test_one_volley_resolves_field_projected_barrier_and_armour_in_order(self):
        for bubble_hp, absorbed, armour_loss in ((200, 48, 0), (1, 1, 23)):
            with self.subTest(bubble_hp=bubble_hp):
                actor, _, health = self.engage(2, shield=True)
                self.match.game_map = replace(self.match.game_map, free_for_all=False)
                self.rein.team, self.other.team, actor.team = 0, 0, 1
                self.assertTrue(self.match.switch_hero(self.other, heroes()[zarya_barrier.HERO]))
                self.place(self.other, (3, 1, 0))
                self.advance(12, buttons=2)
                fight = self.match.combat
                angles = aim(fight._eye(self.other), (0, 2.3, 0))
                self.run.step({self.other: (*angles, 16), self.rein: (0, 0, 2)})
                bubble = fight.zarya_bubbles[self.rein.body]
                self.assertEqual(bubble.health, 200)
                shield = fight.barriers[self.rein.body]
                self.assertTrue(shield.active)
                shield.current = 1
                bubble.health = bubble_hp

                self.launch(shield=True)
                self.assertEqual(shield.current, 1)
                self.assertEqual(bubble.health, bubble_hp)
                self.assertEqual(health.total, 500)
                for _ in range(20):
                    self.advance(1, buttons=2)
                    if not fight.rockets:
                        break
                self.assertFalse(fight.rockets)
                self.assertEqual(shield.current, 0)
                self.assertFalse(shield.active)
                self.assertGreater(shield.broken_until, fight.now)
                self.assertAlmostEqual(bubble.health, bubble_hp - absorbed, places=4)
                energy = self.other.body_script.component.entity_var(87).value()
                self.assertAlmostEqual(energy, absorbed / 5, places=4)
                self.assertEqual(health.now[0], 300)
                self.assertEqual(health.now[1], 200 - armour_loss)
                self.assertEqual(npc_control.stop(self.match), 1)
                self.advance(20, buttons=2)
                self.assertEqual(health.total, 500 - armour_loss)

    def test_actual_fatal_volley_cancels_reinhardt_skill_windups(self):
        for button, graph_index, state_index in ((16, 0xE2, 9), (8, 0x7D, 75), (32, None, None)):
            with self.subTest(button=button):
                actor, target, health = self.engage(8)
                fight = self.match.combat
                old = self.rein.body
                script = self.rein.body_script
                victim = fight.vitals_of(target)
                before = victim.total
                health.damage(499, bypass_armour=True)
                if button == 32:
                    script.set_ult_charge(script.component.entity_var(338).value())
                self.launch()
                self.advance(1, buttons=button)
                if button == 32:
                    self.assertEqual(script.component.entity_var(342).value(), 0)
                    self.assertTrue(any(state.flags & state.RUNNING for state in script.ultimates))
                else:
                    ability = next(i for i in script.component.instances.values()
                                   if i.graph.index == graph_index)
                    state = ability.states[state_index]
                    if button == 8:
                        self.assertTrue(state.active)
                    else:
                        self.assertTrue(state.flags & state.RUNNING)
                self.assertFalse(health.dead)
                for _ in range(20):
                    self.advance(1, buttons=0)
                    if health.dead:
                        break
                self.assertTrue(health.dead)
                self.assertEqual(health.killer, actor.entity)
                self.assertTrue(script.component.owner_dead)
                self.assertEqual(npc_control.stop(self.match), 1)
                for _ in range(90):
                    self.advance(1, buttons=0)
                    self.assertFalse(any(rocket.shooter is self.rein for rocket in fight.rockets))
                    self.assertNotIn(old, fight.charge_drives)
                    self.assertNotIn(old, fight.charge_contacts)
                    self.assertNotIn(old, fight.shatters)
                self.assertEqual(victim.total, before)
                self.assertFalse(fight.controlled(actor.entity))

    def test_actual_fatal_volley_leaves_released_fire_strike_and_shatter_live(self):
        self.engage(-8)
        for launch_delay in range(1, 250):
            self.advance(1, buttons=0)
            if launch_delay >= 40 and any(shot.flown == 0 for shot in self.match.combat.rockets):
                break
        else:
            self.fail('no Enforcer volley available after both skill windups')

        cases = ((16, 33, 100, False), (32, 29, 50, False), (16, 33, 100, True), (32, 29, 0, True))
        for button, release, damage, replace_owner in cases:
            with self.subTest(button=button, replace_owner=replace_owner):
                actor, _, health = self.engage(-8)
                self.place(self.other, (0, 1, 18))
                fight = self.match.combat
                old = self.rein.body
                script = self.rein.body_script
                isolate_damage_charge(self.rein)
                target = fight.vitals_of(fight._target_entity(self.other.body))
                before = target.total
                self.advance(launch_delay - release, buttons=0)
                if button == 32:
                    script.set_ult_charge(script.component.entity_var(338).value())
                self.advance(1, buttons=button)
                self.advance(release - 1, buttons=0)
                if button == 16:
                    [released] = [shot for shot in fight.rockets if shot.shooter is self.rein]
                else:
                    released = fight.shatters[old]
                self.assertTrue(any(shot.shooter is actor and shot.flown == 0 for shot in fight.rockets))
                health.damage(health.total - 1, bypass_armour=True)
                for _ in range(20):
                    self.advance(1, buttons=0)
                    if health.dead:
                        break
                self.assertTrue(health.dead)
                self.assertEqual(health.killer, actor.entity)
                self.assertEqual(target.total, before)
                if button == 16:
                    self.assertIn(released, fight.rockets)
                else:
                    self.assertIs(fight.shatters[old], released)
                self.assertEqual(npc_control.stop(self.match), 1)
                if replace_owner:
                    if button == 16:
                        self.assertIsNone(released.shot)
                    self.assertTrue(self.match.switch_hero(self.rein, self.rein.hero))
                    self.assertNotEqual(self.rein.body, old)
                    self.assertTrue(script.retired)
                    self.assertNotIn(old, fight.shatters)
                    isolate_damage_charge(self.rein)
                self.advance(65, buttons=0)
                self.assertEqual(target.total, before - damage)
                self.assertEqual(fight.controlled(self.other.body), button == 32 and not replace_owner)
                self.assertNotIn(old, fight.shatters)
                self.advance(195, buttons=0)
                self.assertEqual(self.rein.body != old, replace_owner)
                self.assertTrue(health.dead)
                self.assertEqual(target.total, before - damage)
                self.assertFalse(fight.controlled(self.other.body))
                self.assertFalse(any(shot.shooter is self.rein for shot in fight.rockets))
                expected_charge = 100 if button == 16 and not replace_owner else 0
                self.assertEqual(self.rein.body_script.component.entity_var(342).value(), expected_charge)
                if not replace_owner:
                    [deadline] = [when for when, owner in fight.respawns if owner is self.rein]
                    for _ in range(math.ceil((deadline - fight.now) / .016) + 2):
                        self.advance(1, buttons=0)
                        if self.rein.body != old:
                            break
                    self.assertNotEqual(self.rein.body, old)
                    self.assertEqual(self.rein.body_script.component.entity_var(342).value(), expected_charge)

    def test_actual_fatal_volley_releases_an_active_reinhardt_capture(self):
        actor, _, health = self.engage(4)
        self.assertEqual(npc_control.stop(self.match), 1)
        actor.position = (0, 1, -4)
        self.place(self.other, (0, 1, 3))
        fight = self.match.combat
        old = self.rein.body
        captured = self.other.body
        self.advance(1, buttons=8)
        for _ in range(65):
            self.advance(1, buttons=0)
            if fight.pinned(captured):
                break
        self.assertTrue(fight.pinned(captured))
        self.assertEqual(fight.charge_contacts[old].pinned, captured)
        victim = fight.vitals_of(fight._target_entity(captured))
        before = victim.total
        self.rein.mover.yaw, self.rein.mover.pitch = aim(fight._eye(self.rein), (0, 2, -4))
        self.assertEqual(npc_control.start(self.match, self.rein), actor.entity)
        health.damage(499, bypass_armour=True)
        for _ in range(130):
            self.advance(1, buttons=0)
            if health.dead:
                break
            self.assertTrue(fight.pinned(captured))
        self.assertTrue(health.dead)
        self.assertEqual(health.killer, actor.entity)
        self.assertFalse(fight.pinned(captured))
        self.assertNotIn(old, fight.charge_drives)
        self.assertNotIn(old, fight.charge_contacts)
        self.assertNotIn(captured, fight.knockbacks)
        self.assertEqual(npc_control.stop(self.match), 1)
        self.advance(45, buttons=0)
        self.assertEqual(victim.total, before)
        self.assertFalse(fight.controlled(captured))

    def test_explicit_stop_retires_all_25_inflight_pellets_before_far_body_hit(self):
        actor, _, health = self.engage(20)
        self.launch()
        self.assertEqual(health.total, 500)
        self.assertEqual(npc_control.stop(self.match), 1)
        self.assertFalse(self.match.combat.rockets)
        self.advance(100, buttons=0)
        self.assertEqual(health.total, 500)
        self.assertFalse(actor.attack_input)
        self.assertFalse(actor.body_script.component.held)

    def test_control_delays_weapon_and_actual_shatter_interrupts_without_new_volley(self):
        actor, target, health = self.engage(8)
        self.match.combat.control(target, self.rein.body, 'stun', .5)
        self.advance(25, buttons=0)
        self.assertFalse(actor.attack_input)
        self.assertFalse(self.match.combat.rockets)
        self.assertEqual(health.total, 500)
        self.launch()
        npc_control.stop(self.match)
        self.assertFalse(self.match.combat.rockets)
        actor.firing = True
        actor.attack_target = self.rein.entity
        # Actual Reinhardt ultimate acquisition fixture;release uses the real graph.
        self.rein.body_script.set_ult_charge(1540)
        self.advance(1, buttons=0x20)
        self.assertTrue(actor.attack_input)
        self.advance(60, buttons=0)
        self.assertTrue(self.match.combat.controlled(actor.entity))
        self.assertFalse(actor.attack_input)
        self.assertFalse(self.match.combat.rockets)
        frozen = health.total
        self.advance(70, buttons=0)
        self.assertTrue(self.match.combat.controlled(actor.entity))
        self.assertEqual(health.total, frozen)
        for _ in range(300):
            self.advance(1, buttons=0)
            if health.total < frozen:
                break
        self.assertFalse(self.match.combat.controlled(actor.entity))
        self.assertLess(health.total, frozen)
        self.assertIsNotNone(actor.body_script)

    def test_range_wall_and_allied_target_deny_weapon_input(self):
        for reason in ('range', 'wall', 'ally'):
            with self.subTest(reason=reason):
                actor, _, health = self.engage(8)
                if reason == 'range':
                    actor.position = (0, 1, 120)
                elif reason == 'wall':
                    fixtures.charge_wall(self, 4)
                else:
                    self.match.game_map = self.match.game_map.__class__(
                        **{**self.match.game_map.__dict__, 'free_for_all': False})
                    actor.team = self.rein.team = 0
                self.advance(100, buttons=0)
                self.assertEqual(health.total, 500)
                self.assertFalse(actor.attack_input)
                self.assertFalse(self.match.combat.rockets)
                self.assertFalse(actor.body_script.component.held)

    def test_retarget_and_departure_require_fresh_enforcer_windup(self):
        for departure in (False, True):
            with self.subTest(departure=departure):
                actor, _, old_health = self.engage(8)
                fight = self.match.combat
                weapon = next(i for i in actor.body_script.component.instances.values()
                              if i.graph.index == 0x13EB)
                windup, volley = (weapon.node_state(weapon.graph.node(pos)) for pos in (11, 24))
                for _ in range(150):
                    self.advance(1, buttons=0)
                    if windup.active and windup.cur >= .55:
                        break
                self.assertTrue(windup.active)
                self.assertGreaterEqual(windup.cur, .55)
                self.assertEqual(volley.counter, 0)
                self.assertEqual(old_health.total, 500)

                self.assertTrue(self.match.switch_hero(self.other, heroes()[REINHARDT]))
                self.place(self.other, (0, 1, 6))
                new_health = fight.vitals_of(fight._target_entity(self.other.body))
                if departure:
                    # The nearer candidate is behind static cover on the other side of Enforcer.
                    self.place(self.enemy, (0, 1, 9.5))
                    fixtures.charge_wall(self, z=9)
                    self.match.leave(self.rein)
                    self.run.players.remove(self.rein)
                else:
                    self.other.mover.yaw, self.other.mover.pitch = aim(
                        fight._eye(self.other), (0, 2, 8))
                    self.assertEqual(npc_control.start(self.match, self.other), actor.entity)
                self.run.step({})
                self.assertEqual(actor.attack_target, self.other.entity)
                for _ in range(140):
                    if windup.active:
                        break
                    self.assertEqual(volley.counter, 0)
                    self.assertEqual(new_health.total, 500)
                    self.run.step({})
                self.assertTrue(windup.active)
                self.assertLess(windup.cur, .1)
                for frame in range(130):
                    self.run.step({})
                    if volley.counter:
                        break
                self.assertGreaterEqual(frame, 55)
                self.assertEqual(volley.counter, 1)
                for _ in range(10):
                    self.run.step({})
                self.assertEqual(new_health.total, 475)
                self.assertEqual(old_health.total, 500)

    def test_recycled_target_body_id_requires_fresh_enforcer_windup(self):
        actor, _, old_health = self.engage(8)
        fight = self.match.combat
        weapon = next(i for i in actor.body_script.component.instances.values() if i.graph.index == 0x13EB)
        windup, volley = (weapon.node_state(weapon.graph.node(pos)) for pos in (11, 24))
        for _ in range(150):
            self.advance(1, buttons=0)
            if windup.active and windup.cur >= .55:
                break
        self.assertTrue(windup.active)
        self.assertGreaterEqual(windup.cur, .55)
        self.assertEqual(volley.counter, 0)

        old_body = self.rein.body
        old_generation = fight.replacement_ticks[old_body]
        self.assertTrue(self.match.switch_hero(self.rein, heroes()[SOLDIER]))
        self.assertFalse(windup.active)
        self.assertFalse(actor.attack_input)
        self.assertNotEqual(self.rein.body, old_body)
        with patch.object(self.rein.client, 'body_id_available', side_effect=lambda body: body == old_body):
            self.assertTrue(self.match.switch_hero(self.rein, heroes()[REINHARDT]))
        self.assertEqual(self.rein.body, old_body)
        self.assertGreater(fight.replacement_ticks[old_body], old_generation)
        self.assertEqual(actor.attack_target, self.rein.entity)
        self.place(self.rein, (0, 1, 6))
        new_health = fight.vitals_of(fight._target_entity(old_body))
        self.assertIsNot(new_health, old_health)

        self.advance(1, buttons=0)
        for _ in range(140):
            if windup.active:
                break
            self.assertEqual(volley.counter, 0)
            self.assertEqual(new_health.total, 500)
            self.advance(1, buttons=0)
        self.assertTrue(windup.active)
        self.assertLess(windup.cur, .1)
        for frame in range(130):
            self.advance(1, buttons=0)
            if volley.counter:
                break
        self.assertGreaterEqual(frame, 55)
        self.assertEqual(volley.counter, 1)
        self.advance(10, buttons=0)
        self.assertEqual(new_health.total, 475)
        self.assertEqual(old_health.total, 500)

    def test_target_body_retirement_preserves_enforcer_pellets_already_in_flight(self):
        actor, _, old_health = self.engage(20)
        fight = self.match.combat
        pellets = self.launch()
        old_body = self.rein.body
        self.assertEqual(old_health.total, 500)
        self.assertTrue(self.match.switch_hero(self.rein, self.rein.hero))
        self.assertNotEqual(self.rein.body, old_body)
        self.assertFalse(actor.attack_input)
        self.assertEqual([id(pellet) for pellet in fight.rockets], [id(pellet) for pellet in pellets])

        self.place(self.rein, (0, 1, 12))
        new_health = fight.vitals_of(fight._target_entity(self.rein.body))
        self.assertEqual(new_health.total, 500)
        self.advance(20, buttons=0)
        self.assertEqual(new_health.total, 475)
        self.assertEqual(old_health.total, 500)

    def test_authored_volley_clock_caps_rate_and_repeats_while_held(self):
        actor, _, health = self.engage(8, shield=True)
        fight = self.match.combat
        original, launches = fight._shoot, []

        def observe(*args, **kwargs):
            if args[0] is actor and args[2].instance.graph.index == 0x13EB:
                launches.append(args[4])
            return original(*args, **kwargs)

        with patch.object(fight, '_shoot', side_effect=observe):
            self.advance(600, buttons=2)
        self.assertGreaterEqual(len(launches), 3, launches)
        self.assertTrue(all(b - a >= 1000 for a, b in itertools.pairwise(launches)))
        self.assertEqual(health.total, 500)
        self.assertIsNotNone(actor.body_script)
        npc_control.stop(self.match)
        self.assertFalse(fight.rockets)

    def test_death_preserves_emitted_pellets_but_removal_retires_attacks_and_context(self):
        for removed in (False, True):
            with self.subTest(removed=removed):
                actor, target, health = self.engage(20)
                self.launch()
                script = actor.body_script
                context = script.component.world.manual_controller_context
                if removed:
                    self.assertEqual(npc.remove_spawned(self.match), (actor.entity,))
                    self.assertTrue(context.retired)
                    self.assertFalse(script.component.instances)
                    self.assertFalse(self.match.combat.rockets)
                else:
                    self.match.combat._damage(self.rein, target, 10000, False, None, self.run.frame)
                    self.assertTrue(actor.dead)
                    self.assertFalse(script.component.held)
                    self.assertEqual(len(self.match.combat.rockets), 25)
                self.advance(100, buttons=0)
                self.assertAlmostEqual(health.total, 500 if removed else 498.6, places=5)
                self.assertFalse(self.match.combat.rockets)


    def test_controller_facts_and_retirement_are_causal_vm_storage_without_callbacks(self):
        from ow174.game.body_vm_snapshot import BodyVMSnapshotError, export_component_vm, snapshot_digest
        from ow174.game.script import runtime

        actor, _, _ = self.engage(8)
        component = actor.body_script.component
        world = component.world
        with patch.object(runtime.Instance, 'evaluate', side_effect=AssertionError('no query execution')):
            before = snapshot_digest(export_component_vm(component))
            for obj, name in ((world.manual_controller_context, 'retired'),
                              (world.manual_controller_target, 'has_component43'),
                              (world.manual_controller_descriptor, 'has_component92')):
                old = getattr(obj, name)
                setattr(obj, name, not old)
                self.assertNotEqual(snapshot_digest(export_component_vm(component)), before)
                setattr(obj, name, old)
                self.assertEqual(snapshot_digest(export_component_vm(component)), before)
            context = world.manual_controller_context
            old = context.lookup
            context.lookup = lambda entity: world.manual_controller_target
            with self.assertRaises(BodyVMSnapshotError):
                export_component_vm(component)
            context.lookup = old

    def test_manual_controller_binding_is_in_full_server_snapshot(self):
        from test_server_snapshot import ServerSnapshotTests

        from ow174.game.script import runtime
        from ow174.game.server_snapshot import snapshot_digest

        case = ServerSnapshotTests()
        session, _, players = case.scene()
        player = players[0]
        x, y, z = player.mover.position
        actor = npc.spawn(player.match, self.catalog['2'], (x, y, z + 8), team=1)
        self.assertIsNotNone(actor.body_script)
        descriptor = actor.body_script.component.world.manual_controller_descriptor
        with patch.object(runtime.Instance, 'evaluate', side_effect=AssertionError('no query execution')):
            before = snapshot_digest(case.capture(session))
            descriptor.has_component92 = True
            self.assertNotEqual(snapshot_digest(case.capture(session)), before)
            descriptor.has_component92 = False
            self.assertEqual(snapshot_digest(case.capture(session)), before)


if __name__ == '__main__':
    unittest.main()
