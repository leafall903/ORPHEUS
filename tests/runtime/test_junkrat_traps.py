"""Actual Steel Trap input, hit/control/lifetime and destruction in a real Match."""

import math
import unittest
from dataclasses import replace
from unittest.mock import patch

import test_junkrat_projectile_combat as helpers
from test_game_combat import KANEZAKA_DM, Run, aim, in_world

from ow174.game import combat, immunity, junkrat, world
from ow174.game.collision import World
from ow174.game.commands import FIRE, JUMP, Command
from ow174.game.content import SOLDIER, find_hero


class JunkratTrapTests(unittest.TestCase):
    health = helpers.JunkratProjectileTests.health
    place = helpers.JunkratProjectileTests.place

    def setUp(self):
        self.run, self.player, self.enemy = helpers.JunkratProjectileTests().setup_fight()
        self.fight = self.run.match.combat

    def step(self, *, buttons=0, forward=0, yaw=0, pitch=0, player_buttons=0):
        run = self.run
        run.frame += 1
        run.match.update(run.frame * combat.TICK, run.frame)
        self.player.take_commands([Command(run.frame, buttons=player_buttons)])
        self.enemy.take_commands([Command(run.frame, buttons=buttons, forward=forward, yaw=yaw, pitch=pitch)])

    def throw(self):
        self.step(player_buttons=0x10)
        for _ in range(60):
            self.step()
        trap, = self.fight.junkrat.traps.values()
        return trap

    def capture(self, trap):
        x, _, z = trap.flight.position
        self.place(self.run, self.enemy, (x, self.enemy.mover.position[1], z))
        self.step()
        self.assertTrue(self.fight.junkrat.rooted(self.enemy.body, self.run.frame))

    def test_mine_and_trap_reach_authored_airborne_range_before_lifetime(self):
        for button, lifetime in ((8, 999), (16, 5)):
            with self.subTest(button=button):
                self.setUp()
                match = self.run.match
                deployables = self.fight.junkrat.mines if button == 8 else self.fight.junkrat.traps
                self.step(player_buttons=button)
                for _ in range(40):
                    if deployables:
                        break
                    self.step()
                deployable, = deployables.values()
                rocket = deployable.flight
                self.assertFalse(deployable.parked)
                self.assertEqual(rocket.max_distance, 100)
                self.assertAlmostEqual(rocket.left, lifetime)
                start = rocket.position
                match.collision_world = World([])
                first = self.run.frame
                with patch.object(self.fight, "_explode", wraps=self.fight._explode) as explode:
                    for offset in range(1, 251):
                        at = first + offset
                        match.update(at * combat.TICK, at, frames=False)
                    self.assertNotIn(rocket, self.fight.rockets)
                    self.assertFalse(deployables)
                    if button == 8:
                        explode.assert_called_once_with(rocket, rocket.position, protected_out=set())
                        self.assertEqual((rocket.blast.amount, rocket.blast.rings), (120, ((3.0, 1.0),)))
                    else:
                        explode.assert_not_called()
                self.assertAlmostEqual(rocket.distance_flown, 100)
                self.assertLess(math.dist(start, rocket.position), 100)
                self.assertLess(rocket.flown, lifetime)
                self.assertFalse(deployable.parked)

    def test_deployable_launch_tick_matches_first_batched_update(self):
        def fly(button, stride, empty):
            self.setUp()
            match = self.run.match
            deployables = self.fight.junkrat.mines if button == 8 else self.fight.junkrat.traps
            self.step(player_buttons=button)
            for _ in range(40):
                if deployables:
                    break
                self.step()
            deployable, = deployables.values()
            rocket = deployable.flight
            self.assertEqual(rocket.flown, 0)
            if empty:
                match.collision_world = World([])
            first = self.run.frame
            retirements = []
            landed = self.fight._landed

            def retire(flight):
                if flight is rocket:
                    retirements.append(self.fight.tick - first)
                return landed(flight)

            with patch.object(self.fight, "_landed", side_effect=retire):
                for offset in range(stride, 257, stride):
                    at = first + offset
                    match.update(at * combat.TICK, at, frames=False)

            self.assertEqual(deployable.parked, not empty)
            self.assertEqual(len(retirements), int(empty))
            parked_tick = None if empty else round(deployable.parked_at / combat.TICK) - first
            return rocket.position, rocket.distance_flown, rocket.flown, parked_tick, retirements

        for button in (8, 16):
            for empty in (False, True):
                with self.subTest(button=button, empty=empty):
                    contiguous = fly(button, 1, empty)
                    for stride in (8, 256):
                        self.assertEqual(fly(button, stride, empty), contiguous)

    def test_trap_lands_arms_and_deals_80_once(self):
        trap = self.throw()
        self.assertTrue(trap.parked)
        self.assertEqual(trap.vitals.total, 100)
        self.assertEqual(self.health(self.run, self.enemy).total, 200)
        self.assertIsNone(trap.victim)
        self.capture(trap)
        self.assertEqual(self.health(self.run, self.enemy).total, 120)
        self.assertEqual(trap.release_tick - self.run.frame, math.ceil(3 / combat.TICK))
        for _ in range(20):
            self.step()
        self.assertEqual(self.health(self.run, self.enemy).total, 120)

    def test_native_trap_entity_is_created_once_and_destroyed_after_capture(self):
        shots = []
        original = self.fight.projectiles.fired

        def fired(*args, **kwargs):
            shot = original(*args, **kwargs)
            shots.append(shot)
            return shot

        with patch.object(self.fight.projectiles, "fired", side_effect=fired):
            trap = self.throw()
        shot, = shots
        self.assertEqual(shot.definition, junkrat.TRAP_PROJECTILE)
        self.assertTrue(shot.authoritative)
        self.assertEqual({delivery.viewer for delivery in shot.observers}, {self.player, self.enemy})
        for delivery in shot.observers:
            self.assertTrue(delivery.launch_queued and delivery.launch_arrival.received)
            self.assertTrue(delivery.finished)
            self.assertTrue(any(item.entity == delivery.record.ids[0] and item.op == world.OP_DESTROY
                                and item.ch2 is not None for item in delivery.client.entities))
        self.assertIsNone(trap.flight.shot)
        clients = (self.player.client, self.enemy.client)
        for client in clients:
            creates = [item for item in client.entities
                       if item.entity == trap.entity and item.op == world.OP_CREATE]
            self.assertEqual(len(creates), 1)
            with patch.object(world, "create", wraps=world.create) as create:
                creates[0].build(0)
            self.assertEqual(create.call_args.args[1], junkrat.TRAP_BODY)
            self.assertEqual(create.call_args.kwargs["position"], trap.position)
        replay, = self.fight.junkrat.trap_creates()
        self.assertEqual((replay.entity, replay.op), (trap.entity, world.OP_CREATE))
        self.capture(trap)
        self.assertEqual(self.health(self.run, self.enemy).total, 120)
        self.assertTrue(self.player.client.messages(combat.HIT))
        while self.run.frame <= trap.release_tick:
            self.step()
        self.assertNotIn(trap.entity, self.fight.junkrat.traps)
        self.assertFalse(self.fight.junkrat.trap_creates())
        self.assertFalse(self.fight.junkrat.rooted(self.enemy.body, self.run.frame))
        for client in clients:
            self.assertEqual(sum(item.entity == trap.entity and item.op == world.OP_CREATE
                                 for item in client.entities), 1)
            self.assertEqual(sum(item.entity == trap.entity and item.op == world.OP_DESTROY
                                 for item in client.entities), 1)

    def test_active_grasp_does_not_absorb_armed_trap_damage_or_root(self):
        trap = self.throw()
        x, _, z = trap.flight.position
        self.run.match.switch_hero(self.enemy, find_hero("Sigma"))
        self.place(self.run, self.enemy, (x, self.enemy.mover.position[1], z - 1.8))
        before = self.health(self.run, self.enemy).total
        self.step(buttons=8)
        for _ in range(60):
            if trap.victim is self.enemy:
                break
            self.step(forward=127)
        instance = next(i for i in self.enemy.body_script.component.instances.values()
                        if i.graph.index == 0x1D02)
        self.assertTrue(instance.states[1].active)
        self.assertEqual(instance.vars[21312].value(), 0)
        self.assertIs(trap.victim, self.enemy)
        self.assertTrue(self.fight.junkrat.rooted(self.enemy.body, self.run.frame))
        self.assertEqual(self.health(self.run, self.enemy).total, before - 80)

    def test_active_deflect_does_not_reflect_armed_trap_damage_or_root(self):
        trap = self.throw()
        x, _, z = trap.flight.position
        self.run.match.switch_hero(self.enemy, find_hero("Genji"))
        self.place(self.run, self.enemy, (x, self.enemy.mover.position[1], z - 1.5))
        before = self.health(self.run, self.enemy).total
        owner_before = self.health(self.run, self.player).total
        self.step(buttons=16)
        for _ in range(45):
            if trap.victim is self.enemy:
                break
            self.step(forward=127)
        instance = next(i for i in self.enemy.body_script.component.instances.values()
                        if i.graph.index == 0x03A3)
        self.assertTrue(instance.states[10].active)
        self.assertIs(trap.victim, self.enemy)
        self.assertTrue(self.fight.junkrat.rooted(self.enemy.body, self.run.frame))
        self.assertEqual(self.health(self.run, self.enemy).total, before - 80)
        self.assertEqual(self.health(self.run, self.player).total, owner_before)
        self.assertFalse(self.enemy.client.messages(combat.HIT))

    def test_personal_barrier_absorbs_trap_contact_without_body_damage_or_root(self):
        trap = self.throw()
        x, _, z = trap.flight.position
        self.run.match.switch_hero(self.enemy, find_hero("Zarya"))
        self.place(self.run, self.enemy, (x, self.enemy.mover.position[1], z - 1.8))
        for _ in range(25):
            self.step()
        self.step(buttons=8)
        bubble = self.fight.barriers.sync(self.enemy)
        self.assertIsNotNone(bubble)
        self.assertEqual(bubble.health, 200)
        for _ in range(60):
            if trap.entity not in self.fight.junkrat.traps:
                break
            self.step(forward=127)
        self.assertNotIn(trap.entity, self.fight.junkrat.traps)
        self.assertTrue(bubble.phase.active)
        self.assertEqual(bubble.health, 120)
        self.assertEqual(self.health(self.run, self.enemy).total, 400)
        self.assertFalse(self.fight.junkrat.rooted(self.enemy.body, self.run.frame))
        self.assertFalse(self.player.client.messages(combat.HIT))

    def test_projected_barrier_absorbs_trap_contact_even_when_it_breaks(self):
        for pool in (200, 50):
            with self.subTest(pool=pool):
                self.setUp()
                match = self.run.match
                match.game_map = replace(match.game_map, free_for_all=False)
                trap = self.throw()
                x, _, z = trap.flight.position
                y = self.enemy.mover.position[1]
                self.place(self.run, self.enemy, (x, y, z - 1.8))
                zarya = match.add_player(3, "Zarya", SOLDIER, self.enemy.team, False)
                in_world(match, zarya)
                zarya.spawn = ((x + 5, y, z - 1.8), zarya.spawn[1])
                match.switch_hero(zarya, find_hero("Zarya"))
                self.run.players.append(zarya)
                for _ in range(25):
                    self.run.step({})
                target = next(t for t in self.fight._targets() if t.owner is self.enemy)
                a, b = target.segment()
                centre = tuple((lo + hi) * .5 for lo, hi in zip(a, b, strict=True))
                self.run.step({zarya: (*aim(self.fight._eye(zarya), centre), 16)})
                self.fight.barriers.sync(zarya)
                bubble, = self.fight.barriers.live.values()
                self.assertEqual(bubble.body, self.enemy.body)
                self.assertEqual(bubble.health, 200)
                if pool < 200:
                    self.fight.barriers.damage(bubble, 200 - pool, self.player)
                for _ in range(60):
                    if trap.entity not in self.fight.junkrat.traps:
                        break
                    self.step(forward=127)
                self.assertNotIn(trap.entity, self.fight.junkrat.traps)
                self.assertEqual(bubble.health, max(0, pool - 80))
                if pool > 80:
                    self.assertTrue(bubble.phase.active)
                else:
                    # Child Pet coverage ends at the hit. The idle caster's
                    # owner-frame phase completes at the mapped neutral gap.
                    self.assertIsNone(self.fight.barriers.authored_for(zarya.body, bubble.phase))
                    self.assertFalse(self.fight.barriers.live_source(bubble))
                    self.step()
                    self.step()
                    self.assertFalse(bubble.phase.active)
                self.assertEqual(self.health(self.run, self.enemy).total, 200)
                self.assertFalse(self.fight.junkrat.rooted(self.enemy.body, self.run.frame))
                self.assertFalse(self.player.client.messages(combat.HIT))

    def test_projected_barrier_does_not_retroactively_change_trap_capture(self):
        match = self.run.match
        match.game_map = replace(match.game_map, free_for_all=False)
        trap = self.throw()
        self.capture(trap)
        deadline = trap.release_tick
        x, y, z = self.enemy.mover.position
        zarya = match.add_player(3, "Zarya", SOLDIER, self.enemy.team, False)
        in_world(match, zarya)
        zarya.spawn = ((x + 5, y, z), zarya.spawn[1])
        match.switch_hero(zarya, find_hero("Zarya"))
        self.run.players.append(zarya)
        for _ in range(25):
            self.run.step({})
        target = next(t for t in self.fight._targets() if t.owner is self.enemy)
        a, b = target.segment()
        centre = tuple((lo + hi) * .5 for lo, hi in zip(a, b, strict=True))
        self.run.step({zarya: (*aim(self.fight._eye(zarya), centre), 16)})
        self.fight.barriers.sync(zarya)
        bubble, = self.fight.barriers.live.values()
        self.assertEqual(bubble.body, self.enemy.body)
        self.assertEqual(bubble.health, 200)
        self.assertIs(trap.victim, self.enemy)
        self.assertEqual(trap.release_tick, deadline)
        while self.run.frame < deadline - 1:
            self.run.step({})
            self.assertTrue(self.fight.junkrat.rooted(self.enemy.body, self.run.frame))
            self.assertEqual(self.health(self.run, self.enemy).total, 120)
        self.run.step({})
        self.assertFalse(self.fight.junkrat.rooted(self.enemy.body, self.run.frame))
        self.assertNotIn(trap.entity, self.fight.junkrat.traps)
        self.assertEqual(bubble.health, 200)

    def test_wraith_does_not_spend_trap_but_expiry_allows_capture(self):
        trap = self.throw()
        x, _, z = trap.flight.position
        self.run.match.switch_hero(self.enemy, find_hero("Reaper"))
        self.place(self.run, self.enemy, (x, self.enemy.mover.position[1], z - 1.8))
        self.step(buttons=8)
        self.assertTrue(immunity.wraith_active(self.enemy, self.enemy.body))
        for _ in range(40):
            if self.enemy.mover.position[2] >= z - .2:
                break
            self.step(forward=127)
        self.assertAlmostEqual(self.enemy.mover.position[2], z, delta=.3)
        self.assertEqual(self.health(self.run, self.enemy).total, 250)
        self.assertIsNone(trap.victim)
        for _ in range(230):
            self.step()
            if trap.victim is self.enemy:
                break
        self.assertIs(trap.victim, self.enemy)
        self.assertEqual(self.health(self.run, self.enemy).total, 170)
        self.assertTrue(self.fight.junkrat.rooted(self.enemy.body, self.run.frame))

    def test_armed_trap_excludes_fade_until_phase_ends(self):
        trap = self.throw()
        self.run.match.switch_hero(self.enemy, find_hero("Moira"))
        x, _, z = trap.flight.position
        self.place(self.run, self.enemy, (x, self.enemy.mover.position[1], z - 1.8))
        before = self.health(self.run, self.enemy).total
        self.step(buttons=8)
        self.assertTrue(immunity.phased_out(self.enemy, self.enemy.body))
        for _ in range(65):
            self.step(forward=127 if self.enemy.mover.position[2] < z - .25 else 0)
            if not immunity.phased_out(self.enemy, self.enemy.body):
                break
            self.assertIsNone(trap.victim)
            self.assertEqual(self.health(self.run, self.enemy).total, before)
        self.assertFalse(immunity.phased_out(self.enemy, self.enemy.body))
        self.step()
        self.assertIs(trap.victim, self.enemy)
        self.assertEqual(self.health(self.run, self.enemy).total, before - 80)
        self.assertTrue(self.fight.junkrat.rooted(self.enemy.body, self.run.frame))

    def test_authored_release_flag_ends_capture_on_falling_edge(self):
        trap = self.throw()
        self.capture(trap)
        component = self.enemy.body_script.component
        for flag in (False, True, True):
            component.set(junkrat.TRAP_RELEASE_VAR, flag)
            self.step()
            self.assertTrue(self.fight.junkrat.rooted(self.enemy.body, self.run.frame))
        component.set(junkrat.TRAP_RELEASE_VAR, False)
        if trap.victim_status is not None:
            self.step()
            self.assertTrue(self.fight.junkrat.rooted(self.enemy.body, self.run.frame))
            status = trap.victim_status
            junkrat.runtime.send_instance_message(status, 0x0240000000000FC1, {}, trap.entity)
            component.run_now()
            self.assertTrue(status.stopped)
            self.assertIs(component.instances[status.id], status)
        self.step()
        self.assertIsNone(trap.victim_status)
        self.assertLess(self.run.frame, trap.release_tick)
        self.assertNotIn(trap.entity, self.fight.junkrat.traps)
        self.assertFalse(self.fight.junkrat.rooted(self.enemy.body, self.run.frame))
        self.assertEqual(self.health(self.run, self.enemy).total, 120)

    def test_wraith_and_cryo_after_capture_leave_the_existing_root_until_release(self):
        for hero, active in (("Reaper", immunity.wraith_active), ("Mei", immunity.cryo_active)):
            with self.subTest(hero=hero):
                self.setUp()
                self.run.match.switch_hero(self.enemy, find_hero(hero))
                trap = self.throw()
                self.capture(trap)
                release = trap.release_tick
                feet = tuple(self.enemy.mover.position)
                health = self.health(self.run, self.enemy)
                self.assertEqual(health.total, 170)
                self.step(buttons=8, forward=127)
                for _ in range(10):
                    if active(self.enemy, self.enemy.body):
                        break
                    self.step(forward=127)
                self.assertTrue(active(self.enemy, self.enemy.body))
                while self.run.frame < release - 1:
                    self.step(forward=127)
                    self.assertTrue(self.fight.junkrat.rooted(self.enemy.body, self.run.frame))
                self.assertEqual(tuple(self.enemy.mover.position), feet)
                self.assertEqual(trap.release_tick, release)
                # Original05FE supplies200 healing over4 seconds. Its existing
                # host keeps healing while the separate trap root remains.
                self.assertEqual(health.total, 250 if hero == "Mei" else 170)
                self.step()
                self.assertEqual(self.run.frame, release)
                self.assertFalse(self.fight.junkrat.rooted(self.enemy.body, self.run.frame))
                self.assertNotIn(trap.entity, self.fight.junkrat.traps)

    def test_root_stops_walk_and_jump_but_preserves_aim_and_attack(self):
        trap = self.throw()
        self.capture(trap)
        position = tuple(self.enemy.mover.position)
        yaw, pitch = aim(self.fight._eye(self.enemy), self.fight._eye(self.player))
        for _ in range(20):
            self.step(buttons=FIRE | JUMP, forward=127, yaw=yaw, pitch=pitch)
        self.assertEqual(tuple(self.enemy.mover.position), position)
        self.assertLess(self.health(self.run, self.player).total, 200)
        self.assertEqual(self.enemy.mover.yaw, yaw)

    def test_cryo_freeze_authored_exclusion_preserves_trap_until_expiry(self):
        self.run.match.switch_hero(self.enemy, find_hero("Mei"))
        self.step(player_buttons=0x10)
        trap, = self.fight.junkrat.traps.values()
        for _ in range(60):
            if trap.parked:
                break
            self.step()
        self.assertTrue(trap.parked)
        x, _, z = trap.flight.position
        self.place(self.run, self.enemy, (x, self.enemy.mover.position[1], z))
        self.step(buttons=8)
        component = self.enemy.body_script.component
        for _ in range(35):
            self.step()
        slot = component.entity_var(junkrat.TRAP_EXCLUDE_VAR, create=False)
        self.assertIsNotNone(slot)
        self.assertTrue(slot.value())
        self.assertGreaterEqual(self.fight.now, trap.armed_at)
        self.assertEqual(self.health(self.run, self.enemy).total, 250)
        self.assertIsNone(trap.victim)
        for _ in range(280):
            self.step()
            if trap.victim is self.enemy:
                break
        self.assertFalse(slot.value())
        self.assertIs(trap.victim, self.enemy)
        self.assertEqual(self.health(self.run, self.enemy).total, 170)
        self.assertTrue(self.fight.junkrat.rooted(self.enemy.body, self.run.frame))

    def test_fortify_takes_reduced_trap_damage_and_uses_authored_short_root(self):
        trap = self.throw()
        x, _, z = trap.flight.position
        self.run.match.switch_hero(self.enemy, find_hero("Orisa"))
        self.place(self.run, self.enemy, (x, self.enemy.mover.position[1], z - 1.8))
        before = self.health(self.run, self.enemy).total
        self.step(buttons=8)
        scale = immunity.damage_scale(self.enemy, self.enemy.body)
        self.assertLess(scale, 1)
        slot = self.enemy.body_script.component.entity_var(junkrat.TRAP_SHORT_VAR, create=False)
        self.assertTrue(slot.value())
        for _ in range(45):
            self.step(forward=127)
            if trap.victim is self.enemy:
                break
        self.assertIs(trap.victim, self.enemy)
        self.assertEqual(trap.release_tick - self.run.frame, 1)
        self.assertTrue(self.fight.junkrat.rooted(self.enemy.body, self.run.frame))
        expected = max(80 * scale - 5, 80 * scale / 2)
        # Component51 totals are float32; at this pool size their rounding
        # exceeds the old default seven-decimal comparison by a few micro-HP.
        quantum = math.ldexp(1.0, math.frexp(before)[1] - 24)
        self.assertAlmostEqual(before - self.health(self.run, self.enemy).total, expected, delta=quantum)
        self.step(forward=127)
        self.assertFalse(self.fight.junkrat.rooted(self.enemy.body, self.run.frame))
        self.assertNotIn(trap.entity, self.fight.junkrat.traps)

    def test_duration_releases_then_walk_can_move(self):
        trap = self.throw()
        self.capture(trap)
        position = tuple(self.enemy.mover.position)
        while self.run.frame < trap.release_tick - 1:
            self.step(forward=127)
        self.assertEqual(tuple(self.enemy.mover.position), position)
        self.step(forward=127)
        self.assertFalse(self.fight.junkrat.rooted(self.enemy.body, self.run.frame))
        self.assertNotIn(trap.entity, self.fight.junkrat.traps)
        for _ in range(20):
            self.step(forward=127)
        self.assertGreater(self.enemy.mover.position[2], position[2] + .1)

    def test_friendly_and_out_of_range_targets_do_not_trigger(self):
        self.run.match.game_map = replace(self.run.match.game_map, free_for_all=False)
        self.enemy.team = self.player.team
        trap = self.throw()
        x, _, z = trap.flight.position
        self.place(self.run, self.enemy, (x, self.enemy.mover.position[1], z))
        for _ in range(40):
            self.step()
        self.assertEqual(self.health(self.run, self.enemy).total, 200)
        self.assertIsNone(trap.victim)
        self.assertFalse(self.fight.junkrat.roots)

    def test_enemy_weapon_destroys_an_untriggered_trap(self):
        trap = self.throw()
        x, y, z = trap.flight.position
        yaw, pitch = aim(self.fight._eye(self.enemy), (x, y + .25, z))
        for _ in range(60):
            self.step(buttons=FIRE, yaw=yaw, pitch=pitch)
        self.assertNotIn(trap.entity, self.fight.junkrat.traps)
        self.assertTrue(trap.vitals.dead)
        self.assertEqual(self.health(self.run, self.player).total, 200)

    def test_captured_enemy_can_destroy_trap_and_escape(self):
        trap = self.throw()
        self.capture(trap)
        x, y, z = trap.flight.position
        yaw, pitch = aim(self.fight._eye(self.enemy), (x, y + .25, z))
        for _ in range(60):
            self.step(buttons=FIRE, yaw=yaw, pitch=pitch)
        self.assertTrue(trap.vitals.dead)
        self.assertFalse(self.fight.junkrat.rooted(self.enemy.body, self.run.frame))
        position = tuple(self.enemy.mover.position)
        for _ in range(20):
            self.step(forward=127)
        self.assertGreater(self.enemy.mover.position[2], position[2])

    def test_teammate_can_shoot_captured_enemy_free_but_trap_owners_ally_cannot(self):
        match = self.run.match
        match.game_map = replace(match.game_map, free_for_all=False)
        trap = self.throw()
        self.capture(trap)
        x, y, z = trap.flight.position
        rescuer = match.add_player(3, "Rescuer", SOLDIER, self.player.team, False)
        in_world(match, rescuer)
        rescuer.spawn = ((x + 3, self.enemy.mover.position[1], z), rescuer.spawn[1])
        match.switch_hero(rescuer, find_hero("Soldier: 76"))
        self.run.players.append(rescuer)
        for _ in range(20):
            self.run.step({})
        direction = aim(self.fight._eye(rescuer), (x, y + .25, z))
        for _ in range(8):
            self.run.step({rescuer: (*direction, FIRE)})
        self.assertEqual(trap.vitals.total, 100)
        self.assertTrue(self.fight.junkrat.rooted(self.enemy.body, self.run.frame))
        before_rescue = self.health(self.run, self.enemy).total
        rescuer.team = self.enemy.team
        for _ in range(65):
            self.run.step({rescuer: (*direction, FIRE)})
            if trap.vitals.dead:
                break
        self.assertTrue(trap.vitals.dead)
        self.assertEqual(trap.vitals.killer, rescuer.body)
        self.assertNotIn(trap.entity, self.fight.junkrat.traps)
        self.assertFalse(self.fight.junkrat.rooted(self.enemy.body, self.run.frame))
        self.assertLess(self.run.frame, trap.release_tick)
        self.assertEqual(self.health(self.run, self.enemy).total, before_rescue)

    def test_authored_cooldown_rejects_recast_then_replacement_releases_victim(self):
        trap = self.throw()
        with patch.object(self.fight.junkrat, "cast_trap", wraps=self.fight.junkrat.cast_trap) as cast:
            self.step(player_buttons=0x10)
            for _ in range(10):
                self.step()
            self.assertEqual(cast.call_count, 0)
            for _ in range(650):
                self.step()
            self.assertEqual(cast.call_count, 0)  # a released early press must expire, not queue for10s
            self.capture(trap)
            self.step(player_buttons=0x10)
            for _ in range(10):
                self.step()
            self.assertEqual(cast.call_count, 1)
        self.assertNotIn(trap.entity, self.fight.junkrat.traps)
        self.assertFalse(self.fight.junkrat.rooted(self.enemy.body, self.run.frame))

    def test_frag_kill_of_captured_enemy_releases_root_before_respawn(self):
        trap = self.throw()
        self.capture(trap)
        old_body = self.enemy.body
        health = self.health(self.run, self.enemy)
        self.assertEqual(health.total, 120)
        self.step(player_buttons=FIRE)
        for _ in range(40):
            self.step()
            if health.dead:
                break
        self.assertTrue(health.dead)
        self.assertEqual(health.killer, self.player.body)
        self.assertLess(self.run.frame, trap.release_tick)
        self.step()
        self.assertFalse(self.fight.junkrat.rooted(old_body, self.run.frame))
        self.assertNotIn(trap.entity, self.fight.junkrat.traps)
        for _ in range(round(combat.respawn_seconds(self.run.match) / combat.TICK) + 5):
            self.step()
            if self.enemy.body != old_body:
                break
        self.assertNotEqual(self.enemy.body, old_body)
        self.assertFalse(self.fight.junkrat.rooted(self.enemy.body, self.run.frame))
        self.assertNotIn(old_body, self.fight.junkrat.roots)
        self.assertEqual(self.health(self.run, self.enemy).total, 200)

    def test_victim_death_or_new_body_does_not_leave_a_root(self):
        trap = self.throw()
        self.capture(trap)
        old_body = self.enemy.body
        self.run.match.switch_hero(self.enemy, self.enemy.hero)
        self.step()
        self.assertFalse(self.fight.junkrat.rooted(old_body, self.run.frame))
        self.assertFalse(self.fight.junkrat.rooted(self.enemy.body, self.run.frame))
        self.assertNotIn(trap.entity, self.fight.junkrat.traps)

    def test_released_press_inside_authored_150ms_buffer_starts_at_cooldown_end(self):
        first = self.throw()
        instance = next(i for i in self.player.body_script.component.instances.values()
                        if i.graph.index == 0x9BA)
        ability = next(state for state in instance.states.values() if state.node.pos == 3)
        while ability.remaining() > .08:
            self.step()
        with patch.object(self.fight.junkrat, 'cast_trap', wraps=self.fight.junkrat.cast_trap) as cast:
            self.step(player_buttons=0x10)
            self.assertEqual(cast.call_count, 0)
            self.assertGreater(ability.remaining(), 0)
            press = next(entry for entry in ability.presses if entry[1] == 36)
            deadline_frames = math.ceil(ability.remaining() / combat.TICK) + 3
            self.step()  # release: the original short press,not held input,must admit the cast
            self.assertIn(press, ability.presses)
            for _ in range(deadline_frames):
                self.step()
            self.assertEqual(cast.call_count, 1)
        self.assertNotIn(first.entity, self.fight.junkrat.traps)

    def test_continuously_held_early_press_starts_when_cooldown_finishes(self):
        first = self.throw()
        instance = next(i for i in self.player.body_script.component.instances.values()
                        if i.graph.index == 0x9BA)
        ability = next(state for state in instance.states.values() if state.node.pos == 3)
        with patch.object(self.fight.junkrat, 'cast_trap', wraps=self.fight.junkrat.cast_trap) as cast:
            self.step(player_buttons=0x10)
            self.assertEqual(cast.call_count, 0)
            for _ in range(math.ceil(ability.remaining() / combat.TICK) + 3):
                self.step(player_buttons=0x10)
            self.assertEqual(cast.call_count, 1)
        self.assertNotIn(first.entity, self.fight.junkrat.traps)

    def test_actual_primary_then_trap_kills_without_rooting_a_corpse(self):
        self.step(player_buttons=FIRE)
        for _ in range(40):
            self.step()
        self.assertEqual(self.health(self.run, self.enemy).total, 80)
        trap = self.throw()
        for _ in range(100):
            self.step(forward=127, yaw=-32768)
            if self.fight.is_dead(self.enemy):
                break
        self.assertTrue(self.fight.is_dead(self.enemy))
        self.assertNotIn(trap.entity, self.fight.junkrat.traps)
        self.assertFalse(self.fight.junkrat.roots)
        self.assertEqual(self.health(self.run, self.enemy).killer, self.player.body)

    def test_authored_filter_excludes_flagged_targets_until_flag_is_removed(self):
        trap = self.throw()
        x, _, z = trap.flight.position
        self.place(self.run, self.enemy, (x, self.enemy.mover.position[1], z))
        self.enemy.body_script.component.set(junkrat.TRAP_EXCLUDE_VAR, True)
        for _ in range(30):
            self.step()
        self.assertEqual(self.health(self.run, self.enemy).total, 200)
        self.assertIsNone(trap.victim)
        self.enemy.body_script.component.set(junkrat.TRAP_EXCLUDE_VAR, False)
        self.step()
        self.assertEqual(self.health(self.run, self.enemy).total, 120)
        self.assertTrue(self.fight.junkrat.rooted(self.enemy.body, self.run.frame))

    def test_authored_short_control_flag_uses_the_001_second_branch(self):
        trap = self.throw()
        x, _, z = trap.flight.position
        self.place(self.run, self.enemy, (x, self.enemy.mover.position[1], z))
        self.enemy.body_script.component.set(junkrat.TRAP_SHORT_VAR, True)
        self.step()
        self.assertEqual(self.health(self.run, self.enemy).total, 120)
        self.assertEqual(trap.release_tick - self.run.frame, 1)
        self.step()
        self.assertFalse(self.fight.junkrat.rooted(self.enemy.body, self.run.frame))
        self.assertNotIn(trap.entity, self.fight.junkrat.traps)

    def test_arming_delay_refuses_capture_then_enables_it(self):
        self.step(player_buttons=0x10)
        trap, = self.fight.junkrat.traps.values()
        while not trap.parked:
            self.step()
        x, _, z = trap.flight.position
        self.place(self.run, self.enemy, (x, self.enemy.mover.position[1], z))
        for _ in range(30):  # .48s, earlier than the authored .5s arm delay
            self.step()
        self.assertEqual(self.health(self.run, self.enemy).total, 200)
        for _ in range(3):
            self.step()
        self.assertEqual(self.health(self.run, self.enemy).total, 120)

    def test_arming_and_capture_match_a_skipped_world_update(self):
        outcomes = []
        for skipped in (False, True):
            self.setUp()
            self.step(player_buttons=0x10)
            trap, = self.fight.junkrat.traps.values()
            while not trap.parked:
                self.step()
            x, _, z = trap.flight.position
            self.place(self.run, self.enemy, (x, self.enemy.mover.position[1], z))
            first = self.run.frame
            for offset in ((40,) if skipped else range(1, 41)):
                at = first + offset
                self.run.match.update(at * combat.TICK, at, frames=False)
            outcomes.append((self.health(self.run, self.enemy).total, trap.release_tick - first,
                             [hit["+0x84"] - first for hit in self.player.client.messages(combat.HIT)]))
        self.assertEqual(*outcomes)
        self.assertEqual(outcomes[0][0], 120)

    def test_long_gap_keeps_trap_capture_and_release_at_their_deadlines(self):
        def capture(stride):
            self.setUp()
            self.step(player_buttons=16)
            trap, = self.fight.junkrat.traps.values()
            x, y, z = self.player.mover.position
            self.enemy.mover.position = [x, y, z + 3.5]
            first = self.run.frame
            retirement = []
            retire = self.fight.junkrat.retire

            def observe(deployable):
                if deployable is trap:
                    retirement.append(self.fight.tick - first)
                return retire(deployable)

            with patch.object(self.fight.junkrat, "retire", side_effect=observe):
                for offset in range(stride, 257, stride):
                    at = first + offset
                    self.run.match.update(at * combat.TICK, at, frames=False)

            hits = [hit["+0x84"] - first for hit in self.player.client.messages(combat.HIT)]
            self.assertEqual(self.health(self.run, self.enemy).total, 120)
            self.assertFalse(self.fight.junkrat.rooted(self.enemy.body, self.fight.tick))
            self.assertNotIn(trap.entity, self.fight.junkrat.traps)
            return hits, retirement

        contiguous = capture(1)
        self.assertEqual(contiguous, ([55], [243]))
        self.assertEqual(capture(256), contiguous)

    def test_static_wall_blocks_capture_inside_search_radius(self):
        trap = self.throw()
        x, _, z = trap.flight.position
        self.place(self.run, self.enemy, (x, self.enemy.mover.position[1], z + 1.1))
        helpers.JunkratProjectileTests.install_wall(self, self.run, self.player)
        for _ in range(40):
            self.step()
        self.assertIsNone(trap.victim)
        self.assertEqual(self.health(self.run, self.enemy).total, 200)

    def test_mine_damage_does_not_displace_a_living_trapped_enemy(self):
        self.run.match.switch_hero(self.enemy, find_hero("Roadhog"))
        trap = self.throw()
        self.capture(trap)
        before = self.health(self.run, self.enemy).total
        position = tuple(self.enemy.mover.position)
        for offset in range(50):
            self.run.step({self.player: (0, 4096, 8 if offset == 0 else 0)})
        self.run.step({self.player: (0, 0, 2)})
        for _ in range(20):
            self.run.step({})
        self.assertEqual(self.health(self.run, self.enemy).total, before - 120)
        self.assertEqual(tuple(self.enemy.mover.position), position)
        self.assertTrue(self.fight.junkrat.rooted(self.enemy.body, self.run.frame))

    def test_owner_body_replacement_releases_its_capture(self):
        trap = self.throw()
        self.capture(trap)
        self.run.match.switch_hero(self.player, self.enemy.hero)
        self.step()
        self.assertNotIn(trap.entity, self.fight.junkrat.traps)
        self.assertFalse(self.fight.junkrat.rooted(self.enemy.body, self.run.frame))

    def test_owner_or_victim_leaving_releases_capture_before_next_tick(self):
        for owner_leaves in (False, True):
            with self.subTest(owner_leaves=owner_leaves):
                self.setUp()
                trap = self.throw()
                self.capture(trap)
                body = self.enemy.body
                status = trap.victim_status
                frame = self.run.frame
                feet = tuple(self.enemy.mover.position)
                match = self.run.match
                match.leave(self.player if owner_leaves else self.enemy)
                self.assertEqual(self.fight.tick, frame)
                self.assertNotIn(trap.entity, self.fight.junkrat.traps)
                self.assertNotIn(body, self.fight.junkrat.roots)
                self.assertNotIn(trap.flight, self.fight.rockets)
                self.assertIsNone(trap.victim_status)
                if status is not None:
                    self.assertNotIn(status.id, status.component.instances)
                    flag = status.component.entity_var(junkrat.TRAP_RELEASE_VAR, create=False)
                    self.assertFalse(flag is not None and flag.value())
                if owner_leaves:
                    self.enemy.take_commands([Command(frame + 1, forward=127)])
                    for offset in range(2, 10):
                        at = frame + offset
                        match.update(at * combat.TICK, at, frames=False)
                        self.enemy.take_commands([Command(at, forward=127)])
                    self.assertGreater(self.enemy.mover.position[2], feet[2] + .1)
                    self.assertEqual(self.health(self.run, self.enemy).total, 120)

    def test_owner_death_does_not_silently_remove_a_placed_trap(self):
        trap = self.throw()
        owner = next(t for t in self.fight._targets() if t.owner is self.player and t.construct is None)
        self.fight._damage(self.enemy, owner, 200, False, None, self.run.frame)
        self.capture(trap)
        self.assertTrue(self.fight.is_dead(self.player))
        self.assertEqual(self.health(self.run, self.enemy).total, 120)
        self.assertTrue(self.fight.junkrat.rooted(self.enemy.body, self.run.frame))

    def test_destroying_one_of_two_owners_traps_keeps_the_other_root(self):
        run = Run(KANEZAKA_DM, ("Junkrat A", "Junkrat B", "Target"))
        run.match.bots.clear()
        run.match._pending_bot_scripts.clear()
        first, second, target = run.players
        x, y, z = first.mover.position
        for creator in (first, second):
            creator.spawn = ((x, y, z), creator.spawn[1])
            run.match.switch_hero(creator, find_hero("Junkrat"))
        self.place(run, target, (x, y, z + 6))
        for _ in range(30):
            run.step({})
        run.step({first: (0, 0, 0x10), second: (0, 0, 0x10)})
        for _ in range(60):
            run.step({})
        fight = run.match.combat
        traps = list(fight.junkrat.traps.values())
        self.assertEqual(len(traps), 2)
        px, py, pz = traps[0].flight.position
        self.place(run, target, (px, y, pz))
        run.step({})
        self.assertEqual(self.health(run, target).total, 40)
        self.assertEqual(len(fight.junkrat.roots[target.body]), 2)
        statuses = [trap.victim_status for trap in traps]
        if junkrat.graph.graph(0x09BC) is not None:
            self.assertTrue(all(status is not None for status in statuses))
            self.assertNotEqual(statuses[0].id, statuses[1].id)
            component = target.body_script.component
            flag = component.entity_var(junkrat.TRAP_RELEASE_VAR, create=False)
            self.assertTrue(flag.value())
            self.assertEqual(len(flag.links), 2)
        yaw, pitch = aim(fight._eye(target), (px, py + .25, pz))
        for _ in range(60):
            run.step({target: (yaw, pitch, FIRE)})
        self.assertEqual(sum(trap.vitals.dead for trap in traps), 1)
        self.assertEqual(len(fight.junkrat.roots[target.body]), 1)
        if all(statuses):
            self.assertTrue(flag.value())
            self.assertEqual(len(flag.links), 1)
            for trap, status in zip(traps, statuses, strict=True):
                self.assertEqual(status.id in component.instances, not trap.vitals.dead)
        self.assertTrue(fight.junkrat.rooted(target.body, run.frame))
        for _ in range(20):
            run.step({target: (yaw, pitch, FIRE)})
        self.assertTrue(all(trap.vitals.dead for trap in traps))
        if all(statuses):
            self.assertFalse(flag.value())
            self.assertFalse(flag.links)
            self.assertTrue(all(status.id not in component.instances for status in statuses))
        self.assertFalse(fight.junkrat.rooted(target.body, run.frame))


if __name__ == "__main__":
    unittest.main()
