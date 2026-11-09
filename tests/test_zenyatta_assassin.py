"""Actual1157 manual input versus Zen; bridge policies remain reconstructed."""

import os
import unittest

from test_game_combat import aim

from ow174.game import combat, npc, npc_control
from ow174.game.barriers import PROJECTED, ZARYA
from ow174.game.collision import World
from ow174.game.commands import Command
from ow174.game.content import heroes
from ow174.game.mover import mover_data


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

    def test_actual_transcendence_rejects_pin_and_preserves_native_duration(self):
        run, zen, actor, vitals = self.arena()
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
        self.assertGreater(self.state(actor, 25).counter, 0)  # actual contact phase was attempted
        self.assertFalse(run.match.combat.is_stunned(zen))
        for _ in range(10):
            run.step({})
        self.assertFalse(run.match.combat.zenyatta.ultimate(zen))
