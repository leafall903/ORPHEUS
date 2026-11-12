"""Actual Echo Q -> copied body -> weapons/health -> return, with explicit reconstruction limits."""
import json
import unittest
from dataclasses import replace

import test_echo_combat as echo_tests
import test_server_snapshot as snapshot_tests
from test_game_combat import aim, in_world
from test_game_mover import box, wall

from ow174.game import combat, stats, world
from ow174.game.collision import World
from ow174.game.commands import Command
from ow174.game.content import SOLDIER, heroes
from ow174.game.mover import Mover, mover_data
from ow174.game.script.driver import MAX_GAP

ECHO = 0x02E0000000000206


class EchoDuplicateTests(unittest.TestCase):
    def session(self, distance=5):
        return echo_tests.EchoPrimaryTests().session(distance=distance)

    def charged(self, player):
        cost = player.body_script.component.entity_var(338).value()
        player.body_script.set_ult_charge(cost)
        return cost

    def start(self, run, player, angles):
        self.charged(player)
        run.step({player: (*angles, 32)})
        self.assertEqual(player.hero.guid, SOLDIER)
        self.assertTrue(run.match.combat.echo_duplicates.active(player))

    def test_charged_q_copies_enemy_weapon_health_team_and_pose(self):
        run, player, enemy, target, _ = self.session()
        for _ in range(25):
            run.frame += 1
            run.match.update(run.frame * combat.TICK, run.frame)
            player.take_commands([Command(run.frame, right=127)])
        fight = run.match.combat
        angles = aim(fight._eye(player), (target.feet[0], target.feet[1] + 1, target.feet[2]))
        old_body, position, team = player.body, tuple(player.mover.position), player.team
        self.start(run, player, angles)
        self.assertNotEqual(player.body, old_body)
        cast_position = tuple(player.mover.position)
        self.assertLess(sum(abs(a-b) for a, b in zip(cast_position, position, strict=True)), .15)
        self.assertEqual(player.team, team)
        self.assertNotEqual(player.team, enemy.team)
        self.assertEqual(player.body_script.component.entity_var(342).value(), 0)
        creates = [u for u in player.client.entities if u.entity == player.body and u.op == world.OP_CREATE]
        self.assertTrue(creates)
        self.assertEqual(tuple(creates[-1].movement.position), cast_position)
        before = fight.vitals_of(target).total
        for _ in range(20):
            run.step({player: (*angles, 1)})
        self.assertEqual(before - fight.vitals_of(target).total, 60)

    def test_enemy_sticky_fuses_keep_their_position_when_the_copy_expires(self):
        run, player, enemy, _, angles = self.session()
        fight = run.match.combat
        self.start(run, player, angles)
        copied_body = player.body
        run.match.switch_hero(enemy, heroes()[ECHO], preserve_pose=True)
        expires = fight.echo_duplicates.states[player.entity].expires
        while expires - fight.now > 44 * combat.TICK:
            run.step({})
        x, y, z = player.mover.position
        incoming = aim(fight._eye(enemy), (x, y + 1, z))
        echo_tests.EchoStickyTests().launch(run, enemy, incoming)
        bombs = tuple(fight.rockets)
        self.assertEqual(len(bombs), 6)
        self.assertTrue(all(bomb.attached_entity == copied_body for bomb in bombs))
        target = next(t for t in fight._targets() if t.owner is player)
        self.assertEqual(fight.vitals_of(target).total, 170)

        for _ in range(8):
            run.step({})
        self.assertEqual(player.hero.guid, ECHO)
        self.assertNotEqual(player.body, copied_body)
        target = next(t for t in fight._targets() if t.owner is player)
        self.assertEqual(fight.vitals_of(target).total, 200)
        points = [bomb.position for bomb in bombs]
        self.assertEqual(tuple(fight.rockets), bombs)
        for _ in range(85):
            run.step({})
        self.assertFalse(fight.rockets)
        self.assertEqual([bomb.position for bomb in bombs], points)
        self.assertTrue(all(bomb.attached_entity == copied_body for bomb in bombs))
        self.assertEqual(fight.vitals_of(target).total, 50)

    def test_killing_the_source_hero_does_not_end_the_copy(self):
        run, player, enemy, target, angles = self.session()
        fight = run.match.combat
        instance = next(i for i in player.body_script.component.instances.values()
                        if i.graph.index == 0x19EB)
        duration = instance.vars[567].value()
        self.assertEqual(duration, 15)
        self.start(run, player, angles)
        started = fight.now
        body = player.body
        script = player.body_script
        copy = fight.echo_duplicates.states[player.entity]
        expires = copy.expires
        self.assertAlmostEqual(expires - started, duration)
        for _ in range(120):
            run.step({player: (*angles, 1)})
            if fight.is_dead(enemy):
                break
        self.assertTrue(fight.is_dead(enemy))
        self.assertEqual(fight.vitals_of(target).total, 0)
        self.assertTrue(player.client.messages(combat.KILL_NOTICE))
        self.assertEqual(player.hero.guid, SOLDIER)
        self.assertEqual(player.body, body)
        self.assertIs(player.body_script, script)
        self.assertIs(fight.echo_duplicates.states[player.entity], copy)
        self.assertEqual(copy.expires, expires)

        for _ in range(950):
            self.assertTrue(fight.echo_duplicates.active(player))
            self.assertLess(fight.now, expires)
            run.step({})
            if not fight.echo_duplicates.active(player):
                break
        self.assertGreaterEqual(fight.now, expires)
        self.assertAlmostEqual(fight.now - started, duration, delta=combat.TICK)
        self.assertFalse(fight.echo_duplicates.active(player))
        self.assertEqual(player.hero.guid, ECHO)
        self.assertFalse(fight.is_dead(player))
        self.assertEqual(player.client.messages(combat.DEATH_NOTICE), [])

    def test_insufficient_charge_miss_and_beyond_radius_do_not_consume_charge(self):
        for distance, charge, angles_override in ((5, 100, None), (5, None, (-32768, 0)), (41, None, None)):
            with self.subTest(distance=distance, charge=charge):
                run, player, _, _, angles = self.session(distance)
                original = player.body
                if charge is None:
                    charge = self.charged(player)
                else:
                    player.body_script.set_ult_charge(charge)
                run.step({player: (*(angles_override or angles), 32)})
                self.assertEqual(player.hero.guid, ECHO)
                self.assertEqual(player.body, original)
                self.assertGreaterEqual(player.body_script.component.entity_var(342).value(), charge)
                self.assertFalse(run.match.combat.echo_duplicates.active(player))

    def test_copy_uses_authored_radius_even_when_capsule_edge_is_in_reach(self):
        for distance in (40.125, 39.875):
            with self.subTest(distance=distance):
                run, player, enemy, _, _ = self.session(distance)
                fight = run.match.combat
                instance = next(i for i in player.body_script.component.instances.values()
                                if i.graph.index == 0x19EB)
                query = instance.graph.states[19]
                radius = float(instance.evaluate(query.field("m_radius")))
                self.assertEqual(radius, 40)
                target = next(t for t in fight._targets() if t.owner is enemy)
                a, b = target.segment()
                centre = tuple((x + y) / 2 for x, y in zip(a, b, strict=True))
                origin = fight._eye(player)
                angles = aim(origin, centre)
                contact = combat.ray_capsule(origin, combat.aim(*angles), a, b, target.radius)
                self.assertIsNotNone(contact)
                self.assertLess(contact, radius)

                cost = self.charged(player)
                body = player.body
                run.step({player: (*angles, 32)})
                if distance > radius:
                    self.assertEqual(player.body, body)
                    self.assertEqual(player.hero.guid, ECHO)
                    self.assertFalse(fight.echo_duplicates.active(player))
                    self.assertEqual(player.body_script.component.entity_var(342).value(), cost)
                else:
                    self.assertNotEqual(player.body, body)
                    self.assertEqual(player.hero.guid, SOLDIER)
                    self.assertTrue(fight.echo_duplicates.active(player))
                    self.assertEqual(player.body_script.component.entity_var(342).value(), 0)

    def test_ally_echo_and_static_wall_are_rejected(self):
        for kind in ('ally', 'echo', 'wall'):
            with self.subTest(kind=kind):
                run, player, enemy, _, angles = self.session()
                if kind == 'ally':
                    run.match.game_map = replace(run.match.game_map, free_for_all=False)
                    enemy.team = player.team
                elif kind == 'echo':
                    run.match.switch_hero(enemy, heroes()[ECHO], preserve_pose=True)
                else:
                    run.match.collision_world = World(
                        box(20, 80, 0, 1, 20, 80) + wall(50, 44.5, 60, 44.5, y1=8)
                    )
                cost = self.charged(player)
                run.step({player: (*angles, 32)})
                self.assertEqual(player.hero.guid, ECHO)
                self.assertEqual(player.body_script.component.entity_var(342).value(), cost)

    def test_actual_copy_selects_visible_cone_target_when_near_target_is_blocked(self):
        run, player, near, _, _ = self.session()
        match = run.match
        fight = match.combat
        far = match.add_player(3, "Visible target", SOLDIER, near.team, False)
        in_world(match, far)
        match.switch_hero(far, heroes()[SOLDIER])
        run.players.append(far)
        x, y, z = player.mover.position
        far.mover = Mover((x+1.15, y, z+8), world=match.collision_world, data=mover_data(far.hero.body))
        match.collision_world = World(box(x-20, x+20, y-1, y, z-20, z+20)
                                      + wall(x-.2, z+2.5, x+.2, z+2.5, y0=y, y1=y+6))
        for _ in range(25):
            run.step({})
        target = next(t for t in fight._targets() if t.owner is far)
        origin = fight._eye(player)
        self.assertIsNone(combat.ray_capsule(origin, (0, 0, 1), *target.segment(), target.radius))
        self.charged(player)
        run.step({player: (0, 0, 32)})
        self.assertEqual(fight.echo_duplicates.states[player.entity].target_body, far.body)

    def test_actual_copy_obeys_authored_cone_sensitivity(self):
        for offset, sensitivity, copies in ((1.5, None, False), (.65, .5, False), (.65, 1.0, True)):
            with self.subTest(offset=offset, sensitivity=sensitivity):
                run, player, enemy, _, _ = self.session()
                x, y, z = player.mover.position
                enemy.mover = Mover((x+offset, y, z+5), world=run.match.collision_world,
                                    data=mover_data(enemy.hero.body))
                for _ in range(25):
                    run.step({})
                if sensitivity is not None:
                    player.body_script.component.set(29053, sensitivity)
                fight = run.match.combat
                target = next(t for t in fight._targets() if t.owner is enemy)
                angles = aim(fight._eye(player), (x, target.feet[1] + target.height * .5, target.feet[2]))
                cost = self.charged(player)
                run.step({player: (*angles, 32)})
                self.assertEqual(player.hero.guid, SOLDIER if copies else ECHO)
                self.assertEqual(player.body_script.component.entity_var(342).value(), 0 if copies else cost)

    def test_timer_return_preserves_original_health_above_half(self):
        self.check_timer_return(50, 150)

    def test_timer_return_raises_original_health_to_half(self):
        self.check_timer_return(170, 100)

    def check_timer_return(self, original_damage, expected_health):
        run, player, enemy, _, angles = self.session()
        fight = run.match.combat
        original = next(t for t in fight._targets() if t.owner is player)
        fight._damage(enemy, original, original_damage, False, None, run.frame)
        self.start(run, player, angles)
        copied_body = player.body
        copy_target = next(t for t in fight._targets() if t.owner is player)
        self.assertEqual(fight.vitals_of(copy_target).total, 200)
        fight._damage(enemy, copy_target, 35, False, None, run.frame)
        position = tuple(player.mover.position)
        expires = fight.echo_duplicates.states[player.entity].expires
        tick = run.frame + 940
        run.match.update(expires + .001, tick)
        self.assertEqual(player.hero.guid, ECHO)
        self.assertNotEqual(player.body, copied_body)
        self.assertEqual(tuple(player.mover.position), position)
        returned = next(t for t in fight._targets() if t.owner is player)
        self.assertEqual(fight.vitals_of(returned).total, expected_health)
        self.assertEqual(player.body_script.component.entity_var(342).value(), 0)
        self.assertEqual(player.client.messages(combat.DEATH_NOTICE), [])
        self.assertFalse(any(who is player for _, who in fight.respawns))

    def test_destroying_copy_returns_echo_without_death_or_elimination_credit(self):
        run, player, enemy, _, angles = self.session()
        fight = run.match.combat
        original = next(t for t in fight._targets() if t.owner is player)
        fight._damage(enemy, original, 170, False, None, run.frame)
        self.start(run, player, angles)
        copied_body = player.body
        player.body_script.ult_seconds = 0
        run.step({player: (*angles, 1)})
        self.assertEqual(player.body_script.component.entity_var(342).value(), 130)
        target = next(t for t in fight._targets() if t.owner is player)
        x, y, z = target.feet
        incoming = aim(fight._eye(enemy), (x, y + 1, z))
        for _ in range(110):
            run.step({enemy: (*incoming, 1)})
            if player.hero.guid == ECHO:
                break
        self.assertEqual(player.hero.guid, ECHO)
        returned = next(t for t in fight._targets() if t.owner is player)
        self.assertEqual(fight.vitals_of(returned).total, 100)
        self.assertFalse(fight.echo_duplicates.active(player))
        self.assertFalse(fight.is_dead(player))
        self.assertEqual(player.stats.value(stats.DEATHS), 0)
        self.assertEqual(player.client.messages(combat.DEATH_NOTICE), [])
        self.assertEqual(enemy.client.messages(combat.KILL_NOTICE), [])
        self.assertFalse(enemy.client.messages(combat.HIT)[-1]['+0x78']['+0x8'] & combat.KILLING)

        restored_body = player.body
        self.assertNotEqual(restored_body, copied_body)
        script = player.body_script
        charge = script.component.entity_var(342)
        cost = script.component.entity_var(338).value()
        self.assertEqual(charge.value(), 0)
        run.step({player: (*angles, 32)})
        self.assertEqual(player.body, restored_body)
        self.assertFalse(fight.echo_duplicates.active(player))
        # Earn the full meter on the restored body using normal passive accrual.
        for _ in range(16):
            if charge.value() == cost:
                break
            run.frame += MAX_GAP
            run.match.update(run.frame * combat.TICK, run.frame)
            player.take_commands([Command(run.frame, yaw=angles[0], pitch=angles[1])])
        self.assertEqual(charge.value(), cost)
        run.step({player: (*angles, 32)})
        self.assertTrue(fight.echo_duplicates.active(player))
        self.assertEqual(player.hero.guid, SOLDIER)
        self.assertNotIn(player.body, (copied_body, restored_body))
        self.assertEqual(player.body_script.component.entity_var(342).value(), 0)

    def test_dead_echo_cannot_activate_and_external_hero_switch_cleans_copy(self):
        run, player, enemy, _, angles = self.session()
        self.charged(player)
        fight = run.match.combat
        target = next(t for t in fight._targets() if t.owner is player)
        fight._damage(enemy, target, 1000, False, None, run.frame)
        run.step({player: (*angles, 32)})
        self.assertEqual(player.hero.guid, ECHO)
        self.assertFalse(fight.echo_duplicates.active(player))
        run, player, _, _, angles = self.session()
        self.start(run, player, angles)
        fight = run.match.combat
        run.match.switch_hero(player, heroes()[0x02E0000000000002])
        self.assertNotIn(player.entity, fight.echo_duplicates.states)
        fight.echo_duplicates.update(100000)
        self.assertEqual(player.hero.guid, 0x02E0000000000002)

    def test_copied_biotic_field_heals_original_team_without_healing_enemy(self):
        run, player, enemy, _, angles = self.session()
        run.match.game_map = replace(run.match.game_map, free_for_all=False)
        self.start(run, player, angles)
        player.body_script.ult_seconds = 0
        player.body_script.set_ult_charge(0)
        ally = run.match.add_player(3, 'Ally', SOLDIER, player.team, False)
        in_world(run.match, ally)
        run.match.switch_hero(ally, heroes()[SOLDIER])
        run.players.append(ally)
        x, y, z = player.mover.position
        for owner, offset in ((ally, -.7), (enemy, .7)):
            owner.mover = Mover((x + offset, y, z), world=run.match.collision_world,
                                data=mover_data(owner.hero.body))
        fight = run.match.combat
        targets = {t.owner: t for t in fight._targets()}
        for owner in (player, ally, enemy):
            fight._damage(enemy, targets[owner], 100, False, None, run.frame)
        until = fight.echo_duplicates.states[player.entity].expires
        while until - fight.now > 2.6:
            run.step({})
        run.step({player: (0, 16000, 16)})
        for _ in range(150):
            run.step({})
        self.assertGreater(fight.vitals_of(targets[player]).total, 100)
        self.assertGreater(fight.vitals_of(targets[ally]).total, 100)
        self.assertEqual(fight.vitals_of(targets[enemy]).total, 100)
        given = fight.vitals_of(targets[player]).total + fight.vitals_of(targets[ally]).total - 200
        self.assertAlmostEqual(player.body_script.component.entity_var(342).value(), given * 6.5, delta=.002)
        self.assertTrue(fight.echo_duplicates.active(player))
        self.assertTrue(any(shot.player is player for shot in fight.projectiles.shots))
        while fight.echo_duplicates.active(player):
            run.step({})
        player.body_script.ult_seconds = 0
        returned_charge = player.body_script.component.entity_var(342).value()
        healed = fight.vitals_of(targets[ally]).total
        self.assertLess(healed, 200)
        for _ in range(40):
            run.step({})
        self.assertFalse(any(shot.player is player for shot in fight.projectiles.shots))
        self.assertEqual(fight.vitals_of(targets[ally]).total, healed)
        self.assertEqual(player.body_script.component.entity_var(342).value(), returned_charge)

    def test_allied_biotic_field_keeps_healing_after_copy_return(self):
        self.check_allied_biotic_field_after_return(break_copy=False)

    def test_allied_biotic_field_keeps_healing_after_copy_break(self):
        self.check_allied_biotic_field_after_return(break_copy=True)

    def check_allied_biotic_field_after_return(self, *, break_copy):
        run, player, enemy, _, angles = self.session()
        run.match.game_map = replace(run.match.game_map, free_for_all=False)
        fight = run.match.combat
        original = next(t for t in fight._targets() if t.owner is player)
        fight._damage(enemy, original, 50, False, None, run.frame)
        self.start(run, player, angles)
        player.body_script.ult_seconds = 0
        healer = run.match.add_player(3, 'Ally', SOLDIER, player.team, False)
        in_world(run.match, healer)
        run.match.switch_hero(healer, heroes()[SOLDIER])
        run.players.append(healer)
        x, y, z = player.mover.position
        for owner, offset in ((healer, -.7), (enemy, .7)):
            owner.mover = Mover((x + offset, y, z), world=run.match.collision_world,
                                data=mover_data(owner.hero.body))
        targets = {t.owner: t for t in fight._targets()}
        fight._damage(enemy, targets[player], 100, False, None, run.frame)
        fight._damage(player, targets[enemy], 100, False, None, run.frame)
        player.body_script.set_ult_charge(0)
        expires = fight.echo_duplicates.states[player.entity].expires
        while expires - fight.now > (3.0 if break_copy else 1.5):
            run.step({})
        run.step({healer: (0, 16000, 16)})
        for _ in range(40):
            run.step({})
        self.assertTrue(fight.echo_duplicates.active(player))
        self.assertGreater(fight.vitals_of(targets[player]).total, 100)
        self.assertEqual(player.body_script.component.entity_var(342).value(), 0)
        if break_copy:
            incoming = aim(fight._eye(enemy), (x, y + 1, z))
            while fight.echo_duplicates.active(player) and fight.now < expires:
                run.step({enemy: (*incoming, 1)})
            self.assertLess(fight.now, expires)
            self.assertFalse(fight.echo_duplicates.active(player))
            self.assertEqual(enemy.client.messages(combat.KILL_NOTICE), [])
        else:
            while fight.echo_duplicates.active(player):
                run.step({})
        player.body_script.ult_seconds = 0
        restored = next(t for t in fight._targets() if t.owner is player)
        before = fight.vitals_of(restored).total
        charge = player.body_script.component.entity_var(342).value()
        for _ in range(20):
            run.step({})
        self.assertEqual(player.hero.guid, ECHO)
        self.assertGreater(fight.vitals_of(restored).total, before)
        self.assertEqual(fight.vitals_of(targets[enemy]).total, 100)
        self.assertEqual(player.body_script.component.entity_var(342).value(), charge)

    def test_original_echo_charge_cannot_be_reused_after_copy_break(self):
        run, player, enemy, _, angles = self.session()
        self.start(run, player, angles)
        fight = run.match.combat
        target = next(t for t in fight._targets() if t.owner is player)
        fight._damage(enemy, target, 1000, False, None, run.frame)
        body = player.body
        run.step({player: (*angles, 0)})
        run.step({player: (*angles, 32)})
        self.assertEqual(player.body, body)
        self.assertEqual(player.hero.guid, ECHO)
        # Ordinary passive accrual may begin, but the spent full charge must not return.
        self.assertLess(player.body_script.component.entity_var(342).value(),
                        player.body_script.component.entity_var(338).value())

    def test_timed_return_requires_new_charge_and_release_before_recast(self):
        run, player, _, _, angles = self.session()
        self.start(run, player, angles)
        duplicates = run.match.combat.echo_duplicates
        copied_body = player.body
        expires = duplicates.states[player.entity].expires
        run.frame = int(expires / combat.TICK) + 1
        run.match.update(run.frame * combat.TICK, run.frame)
        self.assertEqual(player.hero.guid, ECHO)
        self.assertNotEqual(player.body, copied_body)
        echo_body = player.body
        script = player.body_script
        charge = script.component.entity_var(342)
        cost = script.component.entity_var(338).value()
        self.assertEqual(charge.value(), 0)

        # Advance the normal passive meter through bounded command gaps while Q stays held.
        for _ in range(16):
            if charge.value() >= cost:
                break
            run.frame += MAX_GAP
            run.match.update(run.frame * combat.TICK, run.frame)
            player.take_commands([Command(run.frame, yaw=angles[0], pitch=angles[1], buttons=32)])
            self.assertEqual(player.body, echo_body)
        self.assertEqual(charge.value(), cost)
        run.step({player: (*angles, 32)})
        self.assertEqual(player.body, echo_body)
        self.assertEqual(charge.value(), cost)

        run.step({player: (*angles, 0)})
        run.step({player: (*angles, 32)})
        self.assertTrue(duplicates.active(player))
        self.assertEqual(player.hero.guid, SOLDIER)
        self.assertNotEqual(player.body, copied_body)
        self.assertEqual(player.body_script.component.entity_var(342).value(), 0)

    def test_disabled_ultimate_input_does_not_bypass_the_control_gate(self):
        run, player, _, _, angles = self.session()
        cost = self.charged(player)
        player.body_script.component.disabled[37] = 1
        run.step({player: (*angles, 32)})
        self.assertEqual(player.hero.guid, ECHO)
        self.assertEqual(player.body_script.component.entity_var(342).value(), cost)
        self.assertFalse(run.match.combat.echo_duplicates.active(player))

    def test_batched_copy_and_fire_uses_the_actual_activation_frame(self):
        run, player, _, target, angles = self.session()
        self.charged(player)
        first = run.frame + 1
        run.match.update(first * combat.TICK, first)
        commands = [Command(first, yaw=angles[0], pitch=angles[1], buttons=32)]
        commands.extend(Command(first + index, yaw=angles[0], pitch=angles[1], buttons=1)
                        for index in range(1, 13))
        player.take_commands(commands)
        self.assertEqual(player.hero.guid, SOLDIER)
        self.assertEqual(player.body_script.frame, first + 12)
        weapon = next(i for i in player.body_script.component.instances.values() if i.graph.index == 0x254)
        self.assertEqual(weapon.vars[53].value(), 28)
        self.assertEqual(run.match.combat.vitals_of(target).total, 160)

    def test_active_copy_has_registered_passive_state(self):
        # Explicit charge injection in a wire-only fixture; this is a passive export,
        # not a claim that the injected charge belongs to a controlled replay root.
        observer = snapshot_tests.ServerSnapshotTests()
        session, peers, players = observer.scene(two=True)
        player = players[0]
        session.apply('switch_hero', {'account': player.account_lo, 'hero': ECHO})
        self.charged(player)
        peers[0].commands(player.body_script.frame + 1, 1, 32)
        self.assertTrue(player.match.combat.echo_duplicates.active(player))
        document = observer.capture(session)
        text = json.dumps(document)
        self.assertIn('echo_duplicate.Duplicates', text)
        self.assertIn('echo_duplicate.Copy', text)
        self.assertIn('ult_gain_bonus', text)

    def test_an_echo_copy_does_not_become_a_recursively_copyable_enemy_hero(self):
        run, player, other, _, _ = self.session()
        run.match.game_map = replace(run.match.game_map, free_for_all=False)
        run.match.switch_hero(other, heroes()[ECHO], preserve_pose=True)
        soldier = run.match.add_player(3, 'Soldier target', SOLDIER, player.team, False)
        in_world(run.match, soldier)
        run.match.switch_hero(soldier, heroes()[SOLDIER])
        run.players.append(soldier)
        x, y, z = player.mover.position
        soldier.mover = Mover((x, y, z + 10), world=run.match.collision_world,
                              data=mover_data(soldier.hero.body))
        fight = run.match.combat
        self.charged(other)
        angle = aim(fight._eye(other), (x, y + 1, z + 10))
        run.step({other: (*angle, 32)})
        self.assertTrue(fight.echo_duplicates.active(other))
        self.charged(player)
        angle = aim(fight._eye(player), (other.mover.position[0], y + 1, other.mover.position[2]))
        run.step({player: (*angle, 32)})
        self.assertEqual(player.hero.guid, ECHO)
        self.assertFalse(fight.echo_duplicates.active(player))
