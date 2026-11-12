"""Actual1157 manual input against Soldier; native AI/pin services remain open."""

import math
import unittest

import test_soldier_pve_static as fixture
from test_game_combat import aim, in_world

from ow174.game import assassin, combat, defenses, npc, npc_control, projectiles, stats, zarya_barriers as zarya_barrier
from ow174.game.body_vm_snapshot import export_component_vm, snapshot_digest
from ow174.game.collision import World
from ow174.game.commands import Command
from ow174.game.content import heroes
from ow174.game.script import expr, runtime
from ow174.game.script.bot import BotWorld
from ow174.game.script.driver import OWNER_HEALTH


class SoldierPveAssassinTests(unittest.TestCase):
    setUpClass = classmethod(fixture.SoldierPveStaticTests.setUpClass.__func__)
    tearDownClass = classmethod(fixture.SoldierPveStaticTests.tearDownClass.__func__)
    reset_catalogs = staticmethod(fixture.SoldierPveStaticTests.reset_catalogs)
    move = fixture.SoldierPveStaticTests.move
    sprint = fixture.SoldierPveStaticTests.sprint

    def arena(self, distance=2, *, helper=False):
        run, soldier, actor = fixture.SoldierPveStaticTests.arena(self, '4', (0, 0, -3 + distance))
        if helper:
            support = run.match.add_player(2, 'Support', soldier.hero.guid, soldier.team, False)
            in_world(run.match, support)
            support.client = fixture.NpcRecordingClient(support)
            run.players.append(support)
            run.match.switch_hero(support, soldier.hero)
            support.mover.position = [2, 0, -1]
        for player in run.players:
            player.mover.position = list(player.mover.position)
            player.mover.body = None
            player.body_script.ult_seconds = 0
        target = next(t for t in run.match.combat._targets() if t.owner is soldier)
        return run, soldier, actor, run.match.combat.vitals_of(target)

    def state(self, actor, position, graph_id=0x1157):
        instance = next(i for i in actor.body_script.component.instances.values() if i.graph.index == graph_id)
        return instance.states.get(instance.graph.node(position).state)

    def engage(self, run, soldier, actor):
        x, y, z = actor.position
        angles = aim(run.match.combat._eye(soldier), (x, y + .8, z))
        run.step({soldier: (*angles, 0)})
        self.assertEqual(npc_control.start(run.match, soldier), actor.entity)

    def start(self, run, soldier, actor):
        self.engage(run, soldier, actor)
        for _ in range(80):
            run.step({})
            if actor.entity in run.match.combat.pins:
                return
        self.fail('actual1157 logical36 did not produce a manual contact pin')

    def test_assassin_pin_interrupts_sprint_and_fresh_input_restarts_it(self):
        run, soldier, actor, pool = self.arena()
        fight = run.match.combat
        self.sprint(run, soldier)
        actor.position = (0, 0, soldier.mover.position[2] + 2.8)
        self.move(run, soldier, target=(0, .8, actor.position[2]))
        self.assertEqual(npc_control.start(run.match, soldier), actor.entity)
        for _ in range(45):
            speed = self.move(run, soldier)
            if fight.is_controlled(soldier):
                break
            self.assertAlmostEqual(speed, 8.25, delta=.01)
        self.assertEqual(fight.pins.get(actor.entity), soldier.body)
        self.assertEqual(pool.total, 175)
        pinned_position = tuple(soldier.mover.position)
        ammo = soldier.body_script.component.instances[11].vars[53]
        before = ammo.value()
        for _ in range(6):
            self.assertEqual(self.move(run, soldier, buttons=8 | 1), 0)
            self.assertTrue(fight.is_controlled(soldier))
            self.assertEqual(tuple(soldier.mover.position), pinned_position)
            self.assertEqual(ammo.value(), before)
        self.assertGreater(npc_control.stop(run.match), 0)
        for _ in range(25):
            speed = self.move(run, soldier)
        self.assertFalse(fight.is_controlled(soldier))
        self.assertAlmostEqual(speed, 5.5, delta=.01)
        self.move(run, soldier, buttons=8)
        for _ in range(10):
            speed = self.move(run, soldier)
        self.assertAlmostEqual(speed, 8.25, delta=.01)

    def test_projected_barrier_keeps_sprint_through_assassin_pin_contact(self):
        for breaking in (False, True):
            with self.subTest(breaking=breaking):
                run, soldier, actor, pool = self.arena(helper=True)
                ally = run.players[1]
                self.assertTrue(run.match.switch_hero(ally, heroes()[zarya_barrier.HERO]))
                fight = run.match.combat
                self.sprint(run, soldier)
                hp = 20 if breaking else 200
                projected = fixture.SoldierPveStaticTests.project_onto_sprinting_soldier(
                    self, run, soldier, ally, hp=hp if breaking else None,
                )
                actor.position = (0, 0, soldier.mover.position[2] + 2.8)
                self.move(run, soldier, target=(0, .8, actor.position[2]))
                self.assertEqual(npc_control.start(run.match, soldier), actor.entity)
                for _ in range(45):
                    speed = self.move(run, soldier)
                    self.assertFalse(fight.is_controlled(soldier))
                    self.assertAlmostEqual(speed, 8.25, delta=.01)
                    row = projected.get(ally.body)
                    if row is None or row[1] < hp:
                        break
                self.assertGreater(self.state(actor, 25).counter, 0)
                self.assertEqual(pool.total, 200)
                if breaking:
                    self.assertEqual(projected.get(ally.body, (None, 0))[1], 0)
                else:
                    self.assertEqual(projected[ally.body][1:], (175, soldier.body))
                self.assertGreater(npc_control.stop(run.match), 0)
                for _ in range(145):
                    self.assertAlmostEqual(self.move(run, soldier), 8.25, delta=.01)
                    self.assertFalse(fight.is_controlled(soldier))
                    if ally.body not in projected:
                        break
                self.assertNotIn(ally.body, projected)
                actor.position = (0, 0, soldier.mover.position[2] + 2.8)
                self.move(run, soldier, target=(0, .8, actor.position[2]))
                self.assertEqual(npc_control.start(run.match, soldier), actor.entity)
                for _ in range(45):
                    self.move(run, soldier)
                    if fight.is_controlled(soldier):
                        break
                self.assertEqual(fight.pins.get(actor.entity), soldier.body)
                self.assertEqual(pool.total, 175)

    def test_projected_barrier_blocks_assassin_contact_even_when_it_breaks(self):
        for breaking in (False, True):
            with self.subTest(breaking=breaking):
                run, soldier, actor, pool = self.arena(helper=True)
                zarya = run.players[1]
                self.assertTrue(run.match.switch_hero(zarya, heroes()[zarya_barrier.HERO]))
                zarya.mover.position = [2, 0, -1]
                if breaking:
                    enemy = run.match.add_player(3, "Enemy Soldier", soldier.hero.guid, actor.team, False)
                    in_world(run.match, enemy)
                    enemy.client = fixture.NpcRecordingClient(enemy)
                    run.players.append(enemy)
                    run.match.switch_hero(enemy, soldier.hero)
                    enemy.mover.position = [-4, 0, -4]
                run.step({})
                fight = run.match.combat
                yaw, pitch = aim(fight._eye(zarya), (0, .9, -3))
                run.step({zarya: (yaw, pitch, 16)})
                barriers = fight.zarya_barriers.projected
                self.assertEqual(barriers[zarya.body][1:], (200, soldier.body))
                if breaking:
                    yaw, pitch = aim(fight._eye(enemy), (0, .9, -3))
                    for _ in range(100):
                        run.step({enemy: (yaw, pitch, 1)})
                        if barriers[zarya.body][1] == 20:
                            break
                    self.assertEqual(barriers[zarya.body][1], 20)
                self.engage(run, soldier, actor)
                for _ in range(35):
                    run.step({})
                self.assertGreater(self.state(actor, 25).counter, 0)
                self.assertEqual(pool.total, 200)
                self.assertNotIn(actor.entity, fight.pins)
                energy = zarya.body_script.component.entity_var(87).value()
                self.assertAlmostEqual(energy, 40 if breaking else 5)
                if breaking:
                    self.assertNotIn(zarya.body, barriers)
                else:
                    self.assertEqual(barriers[zarya.body][1], 175)
                for _ in range(120):
                    run.step({})
                self.assertEqual(pool.total, 200)
                self.assertNotIn(actor.entity, fight.pins)
                npc_control.stop(run.match)
                self.start(run, soldier, actor)
                self.assertEqual(pool.total, 175)

    def test_projected_barrier_releases_live_pin_on_next_assassin_damage(self):
        run, soldier, actor, pool = self.arena(helper=True)
        zarya = run.players[1]
        self.assertTrue(run.match.switch_hero(zarya, heroes()[zarya_barrier.HERO]))
        zarya.mover.position = [2, 0, -1]
        self.start(run, soldier, actor)
        fight = run.match.combat
        for _ in range(80):
            run.step({})
            if pool.total < 175:
                break
        self.assertLess(pool.total, 175)
        self.assertIn(actor.entity, fight.pins)
        yaw, pitch = aim(fight._eye(zarya), (0, .9, -3))
        run.step({zarya: (yaw, pitch, 16)})
        before = pool.total
        self.assertEqual(fight.zarya_barriers.projected[zarya.body][1:], (200, soldier.body))
        for _ in range(6):
            run.step({})
            if actor.entity not in fight.pins:
                break
        self.assertNotIn(actor.entity, fight.pins)
        self.assertEqual(pool.total, before)
        self.assertLess(fight.zarya_barriers.projected[zarya.body][1], 200)
        weapon = soldier.body_script.component.instances[11]
        ammo = weapon.vars[53].value()
        run.step({soldier: (0, 0, 1)})
        self.assertEqual(weapon.vars[53].value(), ammo - 1)

    def test_actual_ana_dart_on_assassin_releases_pinned_soldier(self):
        run, soldier, actor, pool = self.arena(helper=True)
        ana = run.players[1]
        self.assertTrue(run.match.switch_hero(ana, heroes()[0x02E000000000013B]))
        ana.mover.position = [2, 0, -1]
        self.start(run, soldier, actor)
        fight = run.match.combat
        self.assertIn(actor.entity, fight.pins)
        x, y, z = actor.position
        yaw, pitch = aim(fight._eye(ana), (x, y + .8, z))
        run.step({ana: (yaw, pitch, 8)})
        for _ in range(45):
            if actor.entity in fight.sleeps:
                break
            run.step({ana: (yaw, pitch, 0)})
        self.assertIn(actor.entity, fight.sleeps)
        self.assertNotIn(actor.entity, fight.pins)
        self.assertFalse(fight.is_controlled(soldier))
        before = pool.total
        weapon = soldier.body_script.component.instances[11]
        ammo = weapon.vars[53].value()
        run.step({soldier: (0, 0, 1)})
        self.assertEqual(weapon.vars[53].value(), ammo - 1)
        for _ in range(45):
            run.step({})
        self.assertEqual(pool.total, before)
        self.assertNotIn(actor.entity, fight.pins)

    def test_actual_contact_delay_rate_and_soldier_weapon_suppression(self):
        run, soldier, actor, pool = self.arena()
        self.start(run, soldier, actor)
        self.assertEqual(pool.total, 175)
        self.assertGreater(self.state(actor, 25).counter, 0)
        self.assertEqual(run.match.combat.pins, {actor.entity: soldier.body})
        soldier.body_script.set_ult_charge(soldier.body_script.component.vars[338].value())
        rifle = soldier.body_script.component.instances[11]
        ammo = rifle.vars[53].value()
        for _ in range(10):
            run.step({soldier: (0, 0, 1 | 2 | 0x800 | 0x20)})
        self.assertEqual(pool.total, 175)
        self.assertEqual(rifle.vars[53].value(), ammo)
        self.assertFalse(run.match.combat.rockets)
        for _ in range(190):
            run.step({})
            if (state := self.state(actor, 15)) is not None and state.active:
                break
        self.assertTrue(self.state(actor, 15).active)
        before = pool.total
        for _ in range(10):
            run.step({})
        self.assertAlmostEqual(before - pool.total, 16, places=4)

    def test_manual_stop_and_explicit_restart_have_no_old_damage_or_pin(self):
        run, soldier, actor, pool = self.arena()
        self.start(run, soldier, actor)
        self.assertIsNone(self.state(actor, 112, 0x13C3))
        own = self.actor_health(run, actor)
        self.assertEqual(npc_control.stop(run.match), 1)
        self.assertFalse(run.match.combat.pins)
        before = pool.total
        run.step({})
        self.assertTrue(self.state(actor, 24, 0x13C3).active)
        for _ in range(100):
            run.step({})
        self.assertEqual(pool.total, before)
        self.assert_assassin_recovery(actor)
        self.assertEqual(self.actor_health(run, actor), own)
        self.start(run, soldier, actor)
        self.assertEqual(pool.total, before - 25)
        self.assertEqual(self.state(actor, 25).counter, 2)

    def test_helix_launched_before_assassin_pin_releases_soldier(self):
        for lethal in (False, True):
            with self.subTest(lethal=lethal):
                run, soldier, actor, pool = self.arena(distance=2.9)
                fight = run.match.combat
                target = next(t for t in fight._targets() if t.owner is actor)
                attacker = fight.vitals_of(target)
                if lethal:
                    attacker.damage(attacker.total - 110)  # Require both rocket components.
                before = attacker.total
                angles = aim(fight._eye(soldier), (0, 0.8, actor.position[2]))
                run.step({soldier: (*angles, 0)})
                run.step({soldier: (*angles, 2)})
                rocket, = fight.rockets
                self.assertEqual(rocket.source_entity, soldier.body)
                self.assertEqual(npc_control.start(run.match, soldier), actor.entity)
                run.step({})
                self.assertTrue(fight.is_controlled(soldier))
                self.assertIn(rocket, fight.rockets)
                self.assertEqual(pool.total, 175)
                for _ in range(10):
                    run.step({})
                    if rocket not in fight.rockets:
                        break
                self.assertNotIn(rocket, fight.rockets)
                self.assertEqual(attacker.total, max(0, before - 120))
                self.assertEqual(pool.total, 144)
                self.assertIs(actor.dead, lethal)
                if lethal:
                    self.assertEqual(attacker.killer, soldier.body)
                    self.assertFalse(fight.is_controlled(soldier))
                else:
                    self.assertTrue(fight.is_controlled(soldier))
                    for _ in range(12):
                        run.step({})
                        if not fight.is_controlled(soldier):
                            break
                    x, y, z = actor.position
                    self.assertGreater(math.dist(fight._eye(soldier), (x, y + combat.EYE, z)),
                                       assassin.PIN_RANGE)
                self.assertFalse(fight.is_controlled(soldier))
                for _ in range(40):
                    run.step({})
                    self.assertEqual(pool.total, 144)
                if not lethal:
                    self.assert_assassin_recovery(actor)

    @staticmethod
    def actor_health(run, actor):
        fight = run.match.combat
        target = next(t for t in fight._targets() if t.owner is actor)
        return fight.vitals_of(target).total

    def assert_assassin_recovery(self, actor):
        self.assertFalse(self.state(actor, 24, 0x13C3).active)
        health = self.state(actor, 112, 0x13C3)
        self.assertEqual(health.counter, 1)
        self.assertEqual(health.evaluate(health.node.field("m_FA3ECC43")), expr.Entity(actor.entity))
        self.assertEqual(health.evaluate(health.node.field("m_modifyHealth.m_amount")), 0)
        effect = self.state(actor, 158, 0x13C3)
        self.assertEqual(effect.counter, 1)
        self.assertFalse(effect.active)

    def test_pinned_movement_keeps_look_and_stop_restores_movement(self):
        run, soldier, actor, _ = self.arena()
        self.start(run, soldier, actor)
        before = tuple(soldier.mover.position)
        for _ in range(10):
            run.frame += 1
            run.match.update(run.frame * combat.TICK, run.frame)
            soldier.take_commands([Command(run.frame, forward=1, right=1, yaw=2048, pitch=1024,
                                           buttons=0x10)])
        self.assertEqual(tuple(soldier.mover.position), before)
        self.assertEqual(soldier.mover.yaw, 2048)
        npc_control.stop(run.match)
        for _ in range(10):
            run.frame += 1
            run.match.update(run.frame * combat.TICK, run.frame)
            soldier.take_commands([Command(run.frame, forward=1)])
        self.assertNotEqual(tuple(soldier.mover.position), before)

    def test_range_wall_team_and_target_loss_release_without_later_damage(self):
        for reason in ('range', 'wall', 'team', 'gone'):
            with self.subTest(reason=reason):
                run, soldier, actor, pool = self.arena()
                self.start(run, soldier, actor)
                before = pool.total
                if reason == 'range':
                    actor.position = (0, 0, 1)
                elif reason == 'wall':
                    run.match.collision_world = World([
                        (-5, -1, -2, 5, -1, -2, 5, 5, -2),
                        (-5, -1, -2, 5, 5, -2, -5, 5, -2)])
                elif reason == 'team':
                    actor.team = soldier.team
                else:
                    soldier.gone = True
                for _ in range(100):
                    run.step({})
                    self.assertFalse(run.match.combat.pins)
                self.assertEqual(pool.total, before)
                self.assertFalse(actor.attack_input)

    def test_manual_pin_reacquires_after_temporary_gate_without_new_order(self):
        for gate in ('range', 'team', 'wall'):
            with self.subTest(gate=gate):
                run, soldier, actor, pool = self.arena()
                fight = run.match.combat
                self.start(run, soldier, actor)
                self.assertEqual(pool.total, 175)
                position, team = actor.position, actor.team
                clear_world = run.match.collision_world
                if gate == 'range':
                    actor.position = (0, 0, 1)
                elif gate == 'team':
                    actor.team = soldier.team
                else:
                    run.match.collision_world = World([
                        (-50, 0, -50, -50, 0, 50, 50, 0, 50),
                        (-50, 0, -50, 50, 0, 50, 50, 0, -50),
                        (-5, -1, -2, 5, -1, -2, 5, 5, -2),
                        (-5, -1, -2, 5, 5, -2, -5, 5, -2),
                    ])
                for _ in range(100):
                    run.step({})
                    self.assertFalse(fight.pins)
                    self.assertFalse(actor.attack_input)
                    self.assertEqual(pool.total, 175)
                self.assertTrue(actor.firing)
                self.assertEqual(actor.attack_target, soldier.entity)
                actor.position, actor.team = position, team
                run.match.collision_world = clear_world
                for _ in range(100):
                    run.step({})
                    if actor.entity in fight.pins:
                        break
                self.assertEqual(fight.pins, {actor.entity: soldier.body})
                self.assertEqual(self.state(actor, 25).counter, 2)
                self.assertEqual(pool.total, 150)

    def test_ai_primary_button_is_observed_without_hosted_contact(self):
        run, soldier, actor, pool = self.arena()
        component = actor.body_script.component
        self.assertEqual(component.world.ai_weapon_button(0), 20)
        primary = next(i for i in component.instances.values() if i.graph.index == 0x13B8)
        button_node = primary.graph.node(7)
        button = primary.states[button_node.state]
        self.assertTrue(button.active)
        self.assertFalse(button.down)
        self.assertFalse(any(button_node.links(path) for path in button_node.plugs()))
        component.edges([(20, True)])
        for _ in range(45):
            run.step({})
        self.assertTrue(button.down)
        self.assertFalse(run.match.combat.pins)
        self.assertEqual(pool.total, 200)
        component.edges([(20, False)])
        run.step({})
        self.assertFalse(button.down)
        self.assertEqual(pool.total, 200)


    def test_manual_pin_contact_can_miss_after_controller_admits_attack(self):
        run, soldier, actor, pool = self.arena()
        fight = run.match.combat
        clear = run.match.collision_world
        target = next(t for t in fight._targets() if t.owner is soldier)
        _, upper = target.segment()
        eye_y = fight._eye(soldier)[1]
        self.assertLess(upper[1], eye_y)
        # Place the wall top between the controller's eye ray and the capsule ray.
        top = (2 * combat.EYE + upper[1] + eye_y) / 4
        self.engage(run, soldier, actor)
        run.match.collision_world = World([
            (-50, 0, -50, -50, 0, 50, 50, 0, 50),
            (-50, 0, -50, 50, 0, 50, 50, 0, -50),
            (-5, -1, -2, 5, -1, -2, 5, top, -2),
            (-5, -1, -2, 5, top, -2, -5, top, -2),
        ])
        for _ in range(80):
            run.step({})
            self.assertTrue(actor.attack_input)
            self.assertFalse(fight.pins)
            self.assertEqual(pool.total, 200)
        self.assertGreater(self.state(actor, 25).counter, 0)
        self.assertEqual(npc_control.stop(run.match), 1)
        run.match.collision_world = clear
        self.start(run, soldier, actor)
        self.assertEqual(pool.total, 175)


    def test_outside_manual_contact_policy_does_not_pin_or_damage(self):
        run, soldier, actor, pool = self.arena(distance=8)
        self.engage(run, soldier, actor)
        for _ in range(100):
            run.step({})
        self.assertEqual(pool.total, 200)
        self.assertFalse(actor.attack_input)
        self.assertFalse(run.match.combat.pins)

    def test_body_replacement_and_manual_removal_retire_control_immediately(self):
        for removal in (False, True):
            with self.subTest(removal=removal):
                run, soldier, actor, pool = self.arena()
                self.start(run, soldier, actor)
                if removal:
                    self.assertEqual(npc.remove_spawned(run.match), (actor.entity,))
                else:
                    old = soldier.body
                    run.match.switch_hero(soldier, soldier.hero)
                    soldier.mover.position[0] += 20
                    soldier.mover.body = None
                    self.assertNotEqual(soldier.body, old)
                self.assertFalse(run.match.combat.pins)
                before = pool.total
                for _ in range(100):
                    run.step({})
                self.assertEqual(pool.total, before)

    def test_actual_support_rifle_kills_wounded_source_and_releases_victim(self):
        run, soldier, actor, pool = self.arena(helper=True)
        self.start(run, soldier, actor)
        own = run.match.combat.vitals[actor.entity]
        own.damage(own.total - 1)  # wounded fixture; real input performs the kill
        support = run.players[1]
        angles = aim(run.match.combat._eye(support), (0, .8, -1))
        run.step({support: (*angles, 1)})
        for _ in range(30):
            run.step({})
            if actor.dead:
                break
        self.assertTrue(actor.dead)
        self.assertFalse(run.match.combat.pins)
        self.assertIsNone(actor.body_script.component.world.manual_target)
        before = pool.total
        for _ in range(100):
            run.step({})
        self.assertEqual(pool.total, before)
        self.assertIn(actor, run.match.bots)
        self.assertTrue(support.client.messages(combat.KILL_NOTICE))

    def test_context_and_rate_clock_are_causal_vm_snapshot_state(self):
        run, soldier, actor, _ = self.arena()
        self.start(run, soldier, actor)
        component = actor.body_script.component
        before = snapshot_digest(export_component_vm(component))
        component.world.manual_target += 1
        self.assertNotEqual(snapshot_digest(export_component_vm(component)), before)
        component.world.manual_target -= 1
        self.assertEqual(snapshot_digest(export_component_vm(component)), before)
        self.state(actor, 25).last_applied += 1
        self.assertNotEqual(snapshot_digest(export_component_vm(component)), before)

    def test_actual_flashbang_interrupts_source_without_stunning_victim(self):
        run, soldier, actor, pool = self.arena(helper=True)
        support = run.players[1]
        run.match.switch_hero(support, heroes()[0x02E0000000000042])
        support.mover.position = [2, 0, -1]
        support.mover.body = None
        self.start(run, soldier, actor)
        angles = aim(run.match.combat._eye(support), (0, .8, -1))
        run.step({support: (*angles, 0x10)})
        for _ in range(80):
            run.step({})
            if run.match.combat.is_stunned(actor):
                break
        fight = run.match.combat
        self.assertTrue(fight.is_stunned(actor))
        self.assertFalse(fight.is_stunned(soldier))
        self.assertFalse(fight.pins)
        before = pool.total
        deadline = fight.stuns[actor.entity]
        while run.frame < deadline - 1:
            run.step({})
            self.assertEqual(pool.total, before)
            self.assertFalse(fight.pins)

    def test_continuous_attack_kills_victim_and_releases_body_control(self):
        run, soldier, actor, pool = self.arena()
        self.start(run, soldier, actor)
        own = self.actor_health(run, actor)
        for _ in range(400):
            run.step({})
            if pool.dead:
                break
        self.assertTrue(pool.dead)
        self.assertEqual(pool.killer, actor.entity)
        self.assertFalse(run.match.combat.pins)
        self.assertTrue(soldier.client.messages(combat.DEATH_NOTICE))
        for _ in range(22):
            run.step({})
        self.assert_assassin_recovery(actor)
        self.assertEqual(self.actor_health(run, actor), own)

    def test_manual_special_preserves_original_ai_primary_slot_metadata(self):
        run, soldier, actor, _ = self.arena()
        world = actor.body_script.component.world
        self.assertEqual(world.ai_weapon_button(0), 20)
        self.assertEqual(npc_control.button(actor), 36)
        self.start(run, soldier, actor)
        self.assertEqual(world.ai_weapon_button(0), 20)
        self.assertIn(36, actor.body_script.component.held)
        self.assertNotIn(20, actor.body_script.component.held)
        # Stop must cancel queued raw-slot input as well as the manual special.
        actor.body_script.component.edges([(20, True)])
        npc_control.stop(run.match)
        run.step({})
        self.assertNotIn(20, actor.body_script.component.held)
        self.assertNotIn(36, actor.body_script.component.held)
        self.assertFalse(run.match.combat.pins)

    def test_pin_rejects_new_reload_until_fresh_press_after_release(self):
        run, soldier, actor, _ = self.arena()
        fight = run.match.combat
        rifle = soldier.body_script.component.instances[11]
        ammo = rifle.vars[53]
        run.step({soldier: (16384, 0, 1)})
        self.assertEqual(ammo.value(), 29)
        for _ in range(40):
            run.step({})
        self.start(run, soldier, actor)
        for _ in range(60):
            run.step({soldier: (0, 0, 0x400)})
            self.assertIn(actor.entity, fight.pins)
            self.assertFalse(rifle.vars[51].value())
            self.assertEqual(ammo.value(), 29)
        npc_control.stop(run.match)
        run.step({})
        self.assertNotIn(actor.entity, fight.pins)
        self.assertFalse(rifle.vars[51].value())
        run.step({soldier: (0, 0, 0x400)})
        self.assertTrue(rifle.vars[51].value())
        for _ in range(100):
            run.step({})
        self.assertFalse(rifle.vars[51].value())
        self.assertEqual(ammo.value(), 30)

    def test_pin_suppresses_fire_while_admitted_reload_refills(self):
        run, soldier, actor, _ = self.arena()
        fight = run.match.combat
        rifle = soldier.body_script.component.instances[11]
        ammo = rifle.vars[53]
        run.step({soldier: (16384, 0, 1)})
        self.assertEqual(ammo.value(), 29)
        for _ in range(40):
            run.step({})
        run.step({soldier: (0, 0, 0x400)})
        self.assertTrue(rifle.vars[51].value())
        self.start(run, soldier, actor)
        self.assertTrue(rifle.vars[51].value())
        target = next(target for target in fight._targets() if target.owner is actor)
        pool = fight.vitals_of(target)
        initial = pool.total
        for _ in range(60):
            run.step({soldier: (0, 0, 1 | 0x400)})
            self.assertIn(actor.entity, fight.pins)
        self.assertEqual(ammo.value(), 30)
        self.assertEqual(pool.total, initial)
        npc_control.stop(run.match)
        run.step({})
        self.assertNotIn(actor.entity, fight.pins)
        for _ in range(50):
            run.step({})
        self.assertFalse(rifle.vars[51].value())
        self.assertEqual(ammo.value(), 30)
        x, y, z = actor.position
        angles = aim(fight._eye(soldier), (x, y + 0.8, z))
        run.step({soldier: (*angles, 1)})
        self.assertEqual(ammo.value(), 29)
        self.assertEqual(pool.total, initial - 20)

    def test_pin_interrupts_field_windup_without_a_late_deployment(self):
        run, soldier, actor, _ = self.arena()
        run.step({soldier: (0, 0, 0x10)})
        self.start(run, soldier, actor)
        for _ in range(100):
            run.step({soldier: (0, 0, 0x10)})
        self.assertFalse([s for s in run.match.combat.projectiles.shots
                          if s.player is soldier and s.spawn == projectiles.FIELD_ENTITY])
        npc_control.stop(run.match)
        for _ in range(80):
            run.step({})
        self.assertFalse([s for s in run.match.combat.projectiles.shots
                          if s.player is soldier and s.spawn == projectiles.FIELD_ENTITY])

    def test_predeployed_field_heals_during_pin_and_restores_health_after_release(self):
        run, soldier, actor, pool = self.arena()
        run.step({soldier: (0, 0, 0x10)})
        for _ in range(80):
            run.step({})
        fields = [s for s in run.match.combat.projectiles.shots
                  if s.player is soldier and s.spawn == projectiles.FIELD_ENTITY and s.field_tick is not None]
        field, = fields
        fight = run.match.combat
        self.start(run, soldier, actor)
        before = pool.total
        self.assertLess(before, 200)
        for _ in range(20):
            run.step({})
            self.assertIn(actor.entity, run.match.combat.pins)
        self.assertGreater(pool.total, before)
        self.assertIn(field, fight.projectiles.shots)
        npc_control.stop(run.match)
        run.step({})
        self.assertNotIn(actor.entity, fight.pins)
        wounded = pool.total
        self.assertLess(wounded, 200)
        run.step({})
        self.assertAlmostEqual(pool.total, wounded + 35 * combat.TICK)
        for _ in range(100):
            run.step({})
        self.assertIn(field, fight.projectiles.shots)
        self.assertEqual(pool.total, 200)
        self.assertEqual(soldier.body_script.component.vars[OWNER_HEALTH].value(), 200)
        healing = soldier.stats.value(stats.HEALING)
        self.assertAlmostEqual(healing, 25)
        self.assertAlmostEqual(soldier.body_script.component.entity_var(342).value(), healing, delta=0.0001)
        self.assertEqual(pool.ult_healing_deficit, 0)

    def test_active_visor_retains_countdown_and_resumes_fire_after_manual_release(self):
        run, soldier, actor, _ = self.arena()
        script = soldier.body_script
        script.set_ult_charge(script.component.entity_var(338).value())
        run.step({soldier: (0, 0, 0x20)})
        for _ in range(80):
            run.step({})
        visor = next(i for i in script.component.instances.values() if i.graph.index == 0x0259)
        wait = visor.states[6]  # authored215 countdown from567=6; generic timed state
        self.assertTrue(wait.active)
        deadline = wait.start + 6000
        counter = wait.counter
        self.start(run, soldier, actor)
        ammo = script.component.instances[11].vars[53].value()
        for _ in range(10):
            run.step({soldier: (0, 0, 1)})
        self.assertEqual(script.component.instances[11].vars[53].value(), ammo)
        self.assertEqual((wait.start + 6000, wait.counter), (deadline, counter))
        npc_control.stop(run.match)
        before = run.match.combat.vitals[actor.entity].total
        run.step({soldier: (0, 0, 1)})
        for _ in range(12):
            run.step({})
        self.assertLess(run.match.combat.vitals[actor.entity].total, before)
        while script.component.now < deadline:
            run.step({})
        self.assertFalse(wait.active)

    def test_passive_completes_earned_visor_during_pin_and_fresh_input_activates_after_release(self):
        run, soldier, actor, _ = self.arena()
        script = soldier.body_script
        script.ult_seconds = None
        fight = run.match.combat
        charge = script.component.entity_var(342)
        cost = script.component.entity_var(338).value()
        heavy = npc.spawn(run.match, self.catalog["5"], (3, 0, 1), team=actor.team)
        heavy_angles = aim(fight._eye(soldier), (3, .8, 1))
        self.assertEqual(charge.value(), 0)
        for _ in range(5000):
            if charge.value() >= cost - 35:
                break
            run.step({soldier: (*heavy_angles, 1)})
        self.assertGreaterEqual(charge.value(), cost - 35)
        self.assertGreater(cost - charge.value(), 5)
        self.assertLess(self.actor_health(run, heavy), self.catalog["5"].health)
        damage = soldier.stats.value(stats.HERO_DAMAGE)
        for _ in range(450):
            if cost - charge.value() <= 5:
                break
            run.step({})
        self.assertGreater(cost - charge.value(), 0)
        self.assertLessEqual(cost - charge.value(), 5)

        visor = next(i for i in script.component.instances.values() if i.graph.index == 0x0259)
        windup, countdown = visor.state(43), visor.state(6)
        self.start(run, soldier, actor)
        self.assertLess(charge.value(), cost)
        for _ in range(64):
            self.assertEqual(fight.pins.get(actor.entity), soldier.body)
            run.step({soldier: (0, 0, 0x20)})
            if charge.value() == cost:
                break
        self.assertEqual(fight.pins.get(actor.entity), soldier.body)
        self.assertEqual(charge.value(), cost)
        self.assertEqual(soldier.stats.value(stats.HERO_DAMAGE), damage)
        run.step({})
        run.step({soldier: (0, 0, 0x20)})
        self.assertTrue(fight.is_controlled(soldier))
        self.assertFalse(windup.active)
        self.assertEqual(countdown.counter, 0)
        self.assertEqual(charge.value(), cost)

        self.assertEqual(npc_control.stop(run.match), 2)
        run.step({})
        self.assertFalse(fight.is_controlled(soldier))
        self.assertEqual(charge.value(), cost)
        run.step({soldier: (0, 0, 0x20)})
        self.assertTrue(windup.active)
        self.assertEqual(charge.value(), 0)
        for _ in range(80):
            run.step({})
        self.assertTrue(countdown.active)
        before = self.actor_health(run, actor)
        x, y, z = actor.position
        yaw, pitch = aim(fight._eye(soldier), (x, y + .8, z))
        run.step({soldier: (yaw - 4000, pitch, 1)})
        self.assertEqual(self.actor_health(run, actor), before - 20)
        self.assertEqual(charge.value(), 0)
        for _ in range(400):
            if not countdown.active:
                break
            run.step({})
            self.assertEqual(charge.value(), 0)
        self.assertFalse(countdown.active)

    def test_actual_pin_aborts_early_and_late_visor_windup_without_delayed_lock(self):
        for startup_frames in (0, 72):
            with self.subTest(startup_frames=startup_frames):
                run, soldier, actor, _ = self.arena()
                script = soldier.body_script
                component = script.component
                visor = next(i for i in component.instances.values() if i.graph.index == 0x0259)
                script.set_ult_charge(component.entity_var(338).value())
                run.step({soldier: (0, 0, 32)})
                windup, countdown = visor.states[43], visor.state(6)
                self.assertTrue(windup.active)
                for _ in range(startup_frames):
                    run.step({})
                self.assertTrue(windup.active)
                self.start(run, soldier, actor)
                self.assertFalse(windup.active)
                self.assertEqual(countdown.counter, 0)
                self.assertEqual(component.entity_var(342).value(), 0)
                for _ in range(10):
                    run.step({soldier: (0, 0, 32)})
                npc_control.stop(run.match)
                for _ in range(85):
                    run.step({soldier: (0, 0, 32)})
                run.step({})
                run.step({soldier: (0, 0, 32)})
                self.assertFalse(windup.active)
                self.assertEqual(countdown.counter, 0)
                self.assertFalse(run.match.combat.is_controlled(soldier))
                pool = run.match.combat.vitals[actor.entity]
                before = pool.total
                ammo = component.instances[11].vars[53]
                rounds = ammo.value()
                yaw, pitch = aim(run.match.combat._eye(soldier), (0, .8, actor.position[2]))
                run.step({soldier: (yaw + 4000, pitch, 1)})
                self.assertEqual(ammo.value(), rounds - 1)
                self.assertEqual(pool.total, before)
                self.assertEqual(component.entity_var(342).value(), 0)

    def test_actual_existing_defense_states_reject_scoped_manual_pin_policy(self):
        for hero, button in ((0x02E0000000000020, 0x20), (0x02E000000000013E, 8)):
            with self.subTest(hero=hero):
                run, soldier, actor, _ = self.arena()
                run.match.switch_hero(soldier, heroes()[hero])
                soldier.mover.position = [0, 0, -3]
                soldier.mover.body = None
                soldier.body_script.set_ult_charge(soldier.body_script.component.entity_var(338).value())
                run.step({soldier: (0, 0, button)})
                for _ in range(4):
                    run.step({})
                self.assertTrue(defenses.invulnerable(soldier) or defenses.fortify_scale(soldier) is not None)
                target = next(t for t in run.match.combat._targets() if t.owner is soldier)
                pool = run.match.combat.vitals_of(target)
                before = pool.total
                self.engage(run, soldier, actor)
                for _ in range(80):
                    run.step({})
                    self.assertFalse(run.match.combat.pins)
                    self.assertEqual(pool.total, before)
                self.assertGreater(self.state(actor, 25).counter, 0)


class AssassinContextBoundaryTests(unittest.TestCase):
    def test_map_is_explicit_manual_self_context_only_and_unknown_gates_stay_unknown(self):
        from test_game_graph_analysis import make_graph

        world = BotWorld(assassin.HERO, 0, {'0': 36}, manual_control=True)
        component = runtime.Component(0xC0001000, world=world)
        instance = component.create(make_graph(assassin.GRAPH), init=False)
        cfg = {'$': 'STU_DB742151', 'm_entity': {'$': 'STU_B5A0CAF0'}}
        self.assertEqual(assassin.native(world, cfg, instance), expr.Map(((1650, None),)))
        world.manual_target = 0xB0001000
        self.assertEqual(assassin.native(world, cfg, instance), expr.Map(((1650, expr.Entity(0xB0001000)),)))
        for query in ('STU_B96D3A98', 'STU_F7262B36'):
            self.assertIs(assassin.native(world, {**cfg, '$': query}, instance), NotImplemented)
        foreign = {**cfg, 'm_entity': {'$': 'STUConfigVarDynamic', 'm_identifier': 536}}
        self.assertIs(assassin.native(world, foreign, instance), NotImplemented)
        world.manual_control = False
        self.assertIs(assassin.native(world, cfg, instance), NotImplemented)
        world.manual_control = True
        world.hero = 0x02E00000000001CE
        self.assertIs(assassin.native(world, cfg, instance), NotImplemented)
