"""Real input/graph windup/cooldown, authoritative hook damage/control/movement.

Geometry and hook-engine phases are explicitly reconstructed, not client proof.
"""

import math
import unittest
from dataclasses import replace
from unittest.mock import patch

from test_game_combat import KANEZAKA_DM, Run, aim, flat_world, in_world

from ow174.game import bots, combat
from ow174.game.collision import World
from ow174.game.commands import Command
from ow174.game.content import heroes
from ow174.game.script.driver import ULT_CHARGE, ULT_COST
from ow174.game.script import expr

ROADHOG = 0x02E0000000000040


def assert_competing_hooks(test, run, casters, victim, entity):
    match = run.match
    fight = match.combat
    hooks = fight.hooks
    match.game_map = replace(match.game_map, free_for_all=False)
    victim.team = 1
    target = next(t for t in fight._targets() if t.entity == entity)
    x, y, z = target.feet
    match.collision_world = flat_world(y)
    for caster, dx in zip(casters, (-4, 4), strict=True):
        caster.team = 0
        caster.mover.position[:] = (x + dx, y, z - 8)
        caster.mover.body = None
    winner, loser = (actor for actor in run.players if actor in casters)
    for _ in range(30):
        run.step({})
    low, high = target.segment()
    centre = tuple((a + b) / 2 for a, b in zip(low, high, strict=True))
    looks = {actor: aim(fight._eye(actor), centre) for actor in casters}
    pool = fight.vitals_of(target)
    initial_health = pool.total
    states = {}
    for actor in casters:
        graph = next(i for i in actor.body_script.component.instances.values() if i.graph.index == 0x01C8)
        states[actor] = (graph.state(graph.graph.node(11).state), graph.state(graph.graph.node(12).state))
    initial_launches = {actor: launch.counter for actor, (_, launch) in states.items()}

    with patch.object(fight, "_damage", wraps=fight._damage) as damage:
        run.step({actor: (*look, 8) for actor, look in looks.items()})
        both_flying = False
        for _ in range(55):
            run.step({actor: (*look, 0) for actor, look in looks.items()})
            both_flying |= set(hooks.flights) == {actor.body for actor in casters}
            if hooks.controlled(entity):
                break
        test.assertTrue(both_flying)
        test.assertIs(hooks.flights[winner.body].target.owner, victim)
        test.assertIsNone(hooks.flights[loser.body].target)
        hit_health = pool.total
        test.assertGreater(hit_health, 0)
        test.assertGreater(initial_health, hit_health)

        cooldowns = {actor: ability.remaining() for actor, (ability, _) in states.items()}
        run.step({actor: (*look, 8) for actor, look in looks.items()})
        for _ in range(90):
            run.step({actor: (*look, 0) for actor, look in looks.items()})
        hits = [(call.args[0], call.args[1].entity, call.args[2]) for call in damage.call_args_list]
        test.assertEqual(hits, [(winner, entity, 30)])

    test.assertFalse(hooks.flights)
    test.assertEqual(pool.total, hit_health)
    position = victim.position if isinstance(victim, bots.Bot) else victim.mover.position
    test.assertLess(abs(position[0] - winner.mover.position[0]), 4)
    test.assertLess(position[2], z)
    for actor, (ability, launch) in states.items():
        test.assertGreater(ability.remaining(), 0)
        test.assertAlmostEqual(ability.remaining(), cooldowns[actor] - 91 * combat.TICK, places=5)
        test.assertEqual(launch.counter, initial_launches[actor] + 1)


class HookTests(unittest.TestCase):
    def test_hook_native_cosmetic_gets_world_positions_through_flight(self):
        run, hog, enemy, _, _ = self.session(distance=30)
        seen = []
        for frame in range(75):
            self.step(run, hog, enemy, shift=frame == 0)
            flight = run.match.combat.hooks.flights.get(hog.body)
            if flight is None:
                continue
            point = flight.instance.find_var(0, 431).value()
            self.assertIsInstance(point, expr.Vec3)
            self.assertIsInstance(flight.instance.find_var(0, 1179).value(), expr.Vec3)
            seen.append(tuple(point))
        self.assertGreater(len(seen), 2)
        self.assertGreater(math.dist(seen[0], seen[-1]), 5)
        self.assertFalse(run.match.combat.hooks.flights)

    def session(self, distance=10.0, *, enemy_hog=False):
        run = Run(KANEZAKA_DM, ("Roadhog", "Enemy"))
        match = run.match
        match.bots.clear()
        match._pending_bot_scripts.clear()
        hog, enemy = run.players
        match.switch_hero(hog, heroes()[ROADHOG])
        if enemy_hog:
            match.switch_hero(enemy, heroes()[ROADHOG])
        x, y, z = hog.mover.position
        enemy.mover.position = [x, y, z + distance]
        fight = match.combat
        targets = {t.owner: t for t in fight._targets()}
        return run, hog, enemy, targets, fight.vitals_of(targets[enemy])

    def step(self, run, hog, enemy, *, shift=False, enemy_buttons=0, forward=0):
        run.frame += 1
        match = run.match
        match.update(run.frame * combat.TICK, run.frame)
        for player in run.players:
            player.client.queue_entities(match.remote_movements(player))
        hog.take_commands([Command(run.frame, buttons=8 if shift else 0)])
        enemy.take_commands([Command(run.frame, yaw=-32768, buttons=enemy_buttons, forward=forward)])

    def advance(self, run, hog, enemy, frames, **options):
        for _ in range(frames):
            self.step(run, hog, enemy, **options)

    def wall(self, hog, distance=4):
        x, y, z = hog.mover.position
        return World([
            (x - 5, y, z + distance, x + 5, y, z + distance, x + 5, y + 5, z + distance),
            (x - 5, y, z + distance, x + 5, y + 5, z + distance, x - 5, y + 5, z + distance),
        ])

    def test_live_hook_releases_with_target_commands_but_no_owner_commands(self):
        for send_owner in (False, True):
            with self.subTest(send_owner=send_owner):
                run, hog, enemy, _, vitals = self.session()
                fight = run.match.combat
                self.step(run, hog, enemy, shift=True)
                for _ in range(40):
                    if hog.body in fight.hooks.flights:
                        break
                    self.step(run, hog, enemy)
                self.assertIn(hog.body, fight.hooks.flights)
                source_frame = hog.body_script.component.frame
                caught = False
                for _ in range(105):
                    run.frame += 1
                    run.match.update(run.frame * combat.TICK, run.frame)
                    if send_owner:
                        hog.take_commands([Command(run.frame, buttons=0)])
                    enemy.take_commands([Command(run.frame, yaw=-32768, buttons=0)])
                    caught |= fight.hooks.controlled(enemy.body)
                self.assertTrue(caught)
                self.assertNotIn(hog.body, fight.hooks.flights)
                self.assertFalse(fight.hooks.controlled(enemy.body))
                self.assertEqual(vitals.total, 170)
                if not send_owner:
                    self.assertEqual(hog.body_script.component.frame, source_frame)
                    self.step(run, hog, enemy, shift=True)
                    self.assertNotIn(hog.body, fight.hooks.flights)
                    self.assertEqual(vitals.total, 170)

    def test_player_pull_and_release_are_independent_of_target_input(self):
        traces = {}
        for cadence in (1, 7, None):
            with self.subTest(target_command_interval=cadence):
                run, hog, enemy, _, pool = self.session(enemy_hog=True)
                fight = run.match.combat
                self.step(run, hog, enemy, shift=True)
                for _ in range(60):
                    if fight.hooks.controlled(enemy.body):
                        break
                    self.step(run, hog, enemy)
                self.assertTrue(fight.hooks.controlled(enemy.body))
                self.assertEqual(pool.total, 570)
                anchor = tuple(enemy.mover.position)
                trace = []
                for step in range(1, 105):
                    run.frame += 1
                    run.match.update(run.frame * combat.TICK, run.frame)
                    active = fight.hooks.controlled(enemy.body)
                    if active and cadence is not None and step % cadence == 0:
                        enemy.take_commands([Command(run.frame, yaw=-32768, forward=127, buttons=8 | 16)])
                    pose = tuple(enemy.mover.position)
                    self.assertEqual(fight.history[enemy.body][-1][1], pose)
                    self.assertEqual(pool.total, 570)
                    self.assertNotIn(enemy.body, fight.hooks.flights)
                    trace.append((pose, active))
                    if not active:
                        break
                self.assertFalse(fight.hooks.controlled(enemy.body))
                self.assertGreater(math.dist(anchor, enemy.mover.position), 5)
                self.assertEqual(enemy.mover.velocity, (0, 0, 0))
                traces[cadence] = trace
                final_pose = tuple(enemy.mover.position)
                # The first fresh command must not replay movement or ability presses
                # from ticks already consumed by the world-clock pull.
                self.step(run, hog, enemy)
                self.assertEqual(tuple(enemy.mover.position), final_pose)
                for _ in range(12):
                    self.step(run, hog, enemy, forward=127)
                self.assertLess(enemy.mover.position[2], final_pose[2])
        self.assertEqual(traces[1], traces[7])
        self.assertEqual(traces[1], traces[None])

    def test_windup_one_damage_pull_and_control_recovery(self):
        run, hog, enemy, _, vitals = self.session()
        self.step(run, hog, enemy, shift=True)
        self.advance(run, hog, enemy, 8)
        self.assertEqual(vitals.total, 200)
        hooks = run.match.combat.hooks
        for _ in range(40):
            self.step(run, hog, enemy)
            if hooks.controlled(enemy.body):
                break
        self.assertTrue(hooks.controlled(enemy.body))
        self.assertEqual(vitals.total, 170)
        own = run.match.combat.vitals_of(next(t for t in run.match.combat._targets() if t.owner is hog))
        before = own.total
        self.advance(run, hog, enemy, 10, enemy_buttons=1, forward=-127)
        self.assertEqual(own.total, before)  # Forced victim input cannot shoot the hook owner.
        self.assertLess(math.dist(enemy.mover.position, hog.mover.position), 10)
        self.advance(run, hog, enemy, 60)
        self.assertFalse(hooks.controlled(enemy.body))
        self.assertAlmostEqual(math.dist(enemy.mover.position, hog.mover.position), 3, places=2)
        self.assertEqual(vitals.total, 170)
        self.assertEqual(len(hog.client.messages(combat.HIT)), 1)

    def test_miss_ally_and_wall_leave_target_untouched(self):
        for mode in ("miss", "ally", "wall"):
            with self.subTest(mode=mode):
                run, hog, enemy, _, vitals = self.session()
                if mode == "miss":
                    enemy.mover.position[0] += 8
                if mode == "ally":
                    run.match.game_map = replace(run.match.game_map, free_for_all=False)
                    enemy.team = hog.team
                start = tuple(enemy.mover.position)
                fight = run.match.combat
                collision = self.wall(hog) if mode == "wall" else None
                with patch.object(fight, "_collision", return_value=collision):
                    self.step(run, hog, enemy, shift=True)
                    self.advance(run, hog, enemy, 100)
                self.assertEqual(vitals.total, 200)
                self.assertEqual(tuple(enemy.mover.position), start)
                self.assertEqual(hog.client.messages(combat.HIT), [])
                self.assertEqual(fight.hooks.flights, {})

    def test_exact_sweep_front_range_boundary(self):
        for delta in (-0.01, 0.01):
            with self.subTest(delta=delta):
                run, hog, enemy, targets, vitals = self.session()
                # The authored20m limit applies to the sweep front, not feet distance.
                enemy.mover.position[2] = hog.mover.position[2] + 20 + targets[enemy].radius + 0.5 + delta
                self.step(run, hog, enemy, shift=True)
                fight = run.match.combat
                controlled = False
                for _ in range(100):
                    self.step(run, hog, enemy)
                    controlled |= fight.hooks.controlled(enemy.body)
                self.assertEqual(vitals.total, 170 if delta < 0 else 200)
                self.assertEqual(controlled, delta < 0)
                self.assertFalse(fight.hooks.flights)
                instance = next(i for i in hog.body_script.component.instances.values()
                                if i.graph.index == 0x01C8)
                self.assertGreater(instance.state(instance.graph.node(11).state).remaining(), 0)

    def test_simultaneous_hooks_use_first_resolved_contact_without_double_damage(self):
        for reverse in (False, True):
            with self.subTest(reverse=reverse):
                run, hog, enemy, _, _ = self.session(enemy_hog=True)
                fight = run.match.combat
                if reverse:
                    run.players.reverse()
                winner, loser = run.players
                vitals = {t.owner: fight.vitals_of(t) for t in fight._targets()}
                commands = {hog: (0, 0, 8), enemy: (-32768, 0, 8)}
                run.step(commands)
                both_flying = False
                for _ in range(55):
                    run.step({hog: (0, 0, 0), enemy: (-32768, 0, 0)})
                    both_flying |= set(fight.hooks.flights) == {hog.body, enemy.body}
                    if fight.hooks.controlled(loser.body):
                        break
                self.assertTrue(both_flying)
                self.assertTrue(fight.hooks.controlled(loser.body))
                self.assertFalse(fight.hooks.controlled(winner.body))
                self.assertNotIn(loser.body, fight.hooks.flights)
                self.assertEqual(vitals[winner].total, 600)
                self.assertEqual(vitals[loser].total, 570)
                for _ in range(90):
                    run.step({hog: (0, 0, 0), enemy: (-32768, 0, 0)})
                self.assertFalse(fight.hooks.flights)
                for actor in (hog, enemy):
                    instance = next(i for i in actor.body_script.component.instances.values()
                                    if i.graph.index == 0x01C8)
                    self.assertGreater(instance.state(instance.graph.node(11).state).remaining(), 0)
                self.assertEqual(vitals[winner].total, 600)
                self.assertEqual(vitals[loser].total, 570)

    def test_competing_hooks_on_one_enemy_player_keep_the_first_pull(self):
        for reverse in (False, True):
            with self.subTest(reverse=reverse):
                run = Run(KANEZAKA_DM, ("First Roadhog", "Second Roadhog", "Enemy"))
                first, second, victim = run.players
                for actor in (first, second):
                    self.assertTrue(run.match.switch_hero(actor, heroes()[ROADHOG]))
                if reverse:
                    run.players.reverse()
                assert_competing_hooks(self, run, (first, second), victim, victim.body)

    def test_cooldown_repress_rejection_then_reuse(self):
        run, hog, enemy, _, vitals = self.session()
        self.step(run, hog, enemy, shift=True)
        self.advance(run, hog, enemy, 100)
        self.assertEqual(vitals.total, 170)
        instance = next(i for i in hog.body_script.component.instances.values() if i.graph.index == 0x01C8)
        ability = next(s for s in instance.states.values() if s.node.pos == 11)
        self.assertEqual(instance.find_var(0, 61).base, 8.0)
        self.assertGreater(ability.remaining(), 0)
        for _ in range(4):
            self.step(run, hog, enemy, shift=True)
            self.step(run, hog, enemy)
        self.advance(run, hog, enemy, 40)
        self.assertEqual(vitals.total, 170)
        self.advance(run, hog, enemy, 450)
        self.assertEqual(ability.remaining(), 0)
        self.step(run, hog, enemy, shift=True)
        self.advance(run, hog, enemy, 100)
        self.assertEqual(vitals.total, 140)

    def test_whole_hog_interrupts_outgoing_hook_before_latch_but_keeps_existing_pull(self):
        for phase in ('windup', 'flight', 'pull', 'ultimate'):
            with self.subTest(phase=phase):
                self.check_whole_hog_hook_switch(phase)

    def check_whole_hog_hook_switch(self, phase):
        run, hog, enemy, _, pool = self.session()
        hooks = run.match.combat.hooks
        script = hog.body_script
        hook = next(i for i in script.component.instances.values() if i.graph.index == 0x1C8)
        ability = hook.state(hook.graph.node(11).state)
        launch = hook.state(hook.graph.node(12).state)
        ultimate, = script.ultimates
        if phase != 'ultimate':
            run.step({hog: (0, 0, 8)})
            for _ in range(45):
                if (phase == 'windup' or (phase == 'flight' and hog.body in hooks.flights)
                        or (phase == 'pull' and hooks.controlled(enemy.body))):
                    break
                run.step({})
            self.assertTrue(ability.flags & 1)
            self.assertGreater(ability.remaining(), 0)
            self.assertEqual(hog.body in hooks.flights, phase in ('flight', 'pull'))
            self.assertEqual(hooks.controlled(enemy.body), phase == 'pull')
        else:
            self.assertEqual(ability.remaining(), 0)
        cooldown = ability.remaining()
        launches = launch.counter
        script.set_ult_charge(script.component.entity_var(ULT_COST).value())
        run.step({hog: (16384, 0, 32)})
        self.assertTrue(ultimate.flags & 1)
        self.assertEqual(script.component.entity_var(ULT_CHARGE).value(), 0)
        if phase == 'ultimate':
            for _ in range(45):
                run.step({hog: (16384, 0, 0)})
            run.step({hog: (16384, 0, 8)})
            self.assertEqual(ability.remaining(), 0)
        else:
            self.assertFalse(ability.flags & 1)
            self.assertAlmostEqual(ability.remaining(), cooldown - combat.TICK, places=5)
        run.step({hog: (16384, 0, 0)})
        self.assertEqual(hog.body in hooks.flights, phase == 'pull')
        self.assertEqual(hooks.controlled(enemy.body), phase == 'pull')
        health = 170 if phase == 'pull' else 200
        for _ in range(530):
            run.step({hog: (16384, 0, 0)})
            self.assertEqual(pool.total, health)
            self.assertEqual(launch.counter, launches)
            if not ultimate.flags & 1 and not hooks.flights and ability.remaining() == 0:
                break
        self.assertFalse(ultimate.flags & 1)
        self.assertFalse(hooks.flights)
        self.assertEqual(ability.remaining(), 0)
        if phase == 'pull':
            self.assertAlmostEqual(math.dist(enemy.mover.position, hog.mover.position), 3, places=2)
        for _ in range(20):
            run.step({})
            self.assertEqual(launch.counter, launches)
        run.step({hog: (0, 0, 8)})
        for _ in range(80):
            run.step({})
        self.assertEqual(launch.counter, launches + 1)
        self.assertEqual(pool.total, health - 30)

    def test_actual_hook_rejected_short_press_keeps_timestamp_and_expires(self):
        run, hog, enemy, _, _ = self.session(distance=100)
        self.step(run, hog, enemy, shift=True)
        self.advance(run, hog, enemy, 100)
        instance = next(i for i in hog.body_script.component.instances.values() if i.graph.index == 0x1C8)
        launch = next(s for s in instance.states.values() if s.node.pos == 12)
        self.step(run, hog, enemy, shift=True)
        self.advance(run, hog, enemy, 600)
        self.assertEqual(launch.counter, 1)
        self.step(run, hog, enemy, shift=True)
        self.advance(run, hog, enemy, 25)
        self.assertEqual(launch.counter, 2)

    def test_actual_hook_short_press_inside_authored150ms_window_starts_after_expiry(self):
        run, hog, enemy, _, _ = self.session(distance=100)
        self.step(run, hog, enemy, shift=True)
        self.advance(run, hog, enemy, 100)
        instance = next(i for i in hog.body_script.component.instances.values() if i.graph.index == 0x1C8)
        ability = next(s for s in instance.states.values() if s.node.pos == 11)
        launch = next(s for s in instance.states.values() if s.node.pos == 12)
        for _ in range(600):
            if ability.remaining() <= .1:
                break
            self.step(run, hog, enemy)
        self.assertGreater(ability.remaining(), 0)
        self.step(run, hog, enemy, shift=True)
        self.assertEqual(launch.counter, 1)
        self.advance(run, hog, enemy, 30)
        self.assertEqual(launch.counter, 2)

    def test_actual_hook_held_button_does_not_bypass_cooldown_and_release_allows_new_cast(self):
        run, hog, enemy, _, _ = self.session(distance=100)
        self.step(run, hog, enemy, shift=True)
        self.advance(run, hog, enemy, 600, shift=True)
        instance = next(i for i in hog.body_script.component.instances.values() if i.graph.index == 0x1C8)
        launch = next(s for s in instance.states.values() if s.node.pos == 12)
        self.assertEqual(launch.counter, 1)
        self.step(run, hog, enemy)
        self.step(run, hog, enemy, shift=True)
        self.advance(run, hog, enemy, 25)
        self.assertEqual(launch.counter, 2)

    def test_in_flight_hook_retires_on_lethal_shot_or_owner_replacement(self):
        for retire in ("lethal shot", "hero change"):
            with self.subTest(retire=retire):
                run, hog, enemy, targets, victim = self.session(distance=16)
                match = run.match
                fight = match.combat
                start = tuple(hog.mover.position)
                enemy_start = tuple(enemy.mover.position)
                match.collision_world = flat_world(start[1])
                self.advance(run, hog, enemy, 10)
                old_body = hog.body
                self.step(run, hog, enemy, shift=True)
                for _ in range(40):
                    if old_body in fight.hooks.flights:
                        break
                    self.step(run, hog, enemy)
                self.assertIsNone(fight.hooks.flights[old_body].target)
                self.assertEqual(victim.total, 200)

                if retire == "lethal shot":
                    owner = fight.vitals_of(targets[hog])
                    owner.damage(owner.total - 1)
                    fight._sync_body_health(hog, owner)
                    x, y, z = hog.mover.position
                    look = aim(fight._eye(enemy), (x, y + 1.2, z))
                    run.step({enemy: (*look, 1)})
                    for _ in range(8):
                        if owner.dead:
                            break
                        run.step({})
                    self.assertTrue(owner.dead)
                    self.assertEqual(owner.killer, enemy.body)
                    self.step(run, hog, enemy)
                else:
                    self.assertTrue(match.switch_hero(hog, heroes()[0x02E000000000006E]))
                self.assertNotIn(old_body, fight.hooks.flights)
                self.advance(run, hog, enemy, 55)
                self.assertEqual(victim.total, 200)
                self.assertFalse(fight.hooks.controlled(enemy.body))
                self.assertFalse(hog.client.messages(combat.HIT))

                if retire == "lethal shot":
                    for _ in range(700):
                        if hog.body != old_body:
                            break
                        self.step(run, hog, enemy)
                    self.assertFalse(fight.is_dead(hog))
                else:
                    self.assertTrue(match.switch_hero(hog, heroes()[ROADHOG]))
                self.assertNotEqual(hog.body, old_body)
                self.assertNotIn(old_body, fight.hooks.flights)
                hog.mover.position[:] = start
                enemy.mover.position[:] = enemy_start
                hog.mover.body = enemy.mover.body = None
                self.advance(run, hog, enemy, 10)
                script = hog.body_script
                script.ult_seconds = 0
                script.set_ult_charge(0)
                charge = script.component.entity_var(ULT_CHARGE)
                self.step(run, hog, enemy, shift=True)
                for _ in range(60):
                    self.step(run, hog, enemy)
                    if fight.hooks.controlled(enemy.body):
                        break
                self.assertTrue(fight.hooks.controlled(enemy.body))
                self.assertEqual(victim.total, 170)
                self.assertEqual(charge.value(), 30)
                self.assertEqual(len(hog.client.messages(combat.HIT)), 1)

    def test_owner_death_releases_caught_target(self):
        run, hog, enemy, targets, vitals = self.session()
        self.step(run, hog, enemy, shift=True)
        hooks = run.match.combat.hooks
        for _ in range(50):
            self.step(run, hog, enemy)
            if hooks.controlled(enemy.body):
                break
        self.assertTrue(hooks.controlled(enemy.body))
        run.match.combat._damage(enemy, targets[hog], 2000, False, None, run.frame)
        self.step(run, hog, enemy)
        self.assertFalse(hooks.controlled(enemy.body))
        self.assertEqual(vitals.total, 170)

    def test_target_death_and_owner_switch_release_the_control(self):
        for mode in ("target_death", "owner_switch"):
            with self.subTest(mode=mode):
                run, hog, enemy, targets, vitals = self.session()
                self.step(run, hog, enemy, shift=True)
                hooks = run.match.combat.hooks
                for _ in range(50):
                    self.step(run, hog, enemy)
                    if hooks.controlled(enemy.body):
                        break
                self.assertTrue(hooks.controlled(enemy.body))
                if mode == "target_death":
                    run.match.combat._damage(hog, targets[enemy], 2000, False, None, run.frame)
                    self.step(run, hog, enemy)
                    self.assertTrue(vitals.dead)
                    self.assertEqual(vitals.total, 0)
                else:
                    run.match.switch_hero(hog, heroes()[0x02E000000000006E])
                self.assertFalse(hooks.controlled(enemy.body))
                self.assertEqual(hooks.flights, {})

    def test_leaving_retires_live_hook_and_same_account_reentry_casts_fresh(self):
        run, hog, enemy, _, pool = self.session()
        match = run.match
        fight = match.combat
        start = tuple(hog.mover.position)
        enemy_start = tuple(enemy.mover.position)
        self.step(run, hog, enemy, shift=True)
        for _ in range(50):
            self.step(run, hog, enemy)
            if fight.hooks.controlled(enemy.body):
                break
        self.assertTrue(fight.hooks.controlled(enemy.body))
        self.assertEqual(pool.total, 170)
        old_body = hog.body
        match.leave(hog, "test link retired")
        run.players.remove(hog)
        self.assertFalse(fight.hooks.controlled(enemy.body))
        self.assertNotIn(old_body, fight.hooks.flights)
        self.assertFalse(any(key[0] == old_body for key in fight.hooks.seen))
        for _ in range(25):
            run.step({})
        self.assertEqual(pool.total, 170)

        fresh = match.add_player(hog.account_lo, hog.name, ROADHOG, hog.team, False)
        in_world(match, fresh)
        run.players.append(fresh)
        match.switch_hero(fresh, heroes()[ROADHOG])
        self.assertNotEqual(fresh.body, old_body)
        fresh.mover.position[:] = start
        enemy.mover.position[:] = enemy_start
        fresh.mover.body = enemy.mover.body = None
        self.advance(run, fresh, enemy, 5)
        self.step(run, fresh, enemy, shift=True)
        for _ in range(50):
            self.step(run, fresh, enemy)
            if fight.hooks.controlled(enemy.body):
                break
        self.assertTrue(fight.hooks.controlled(enemy.body))
        self.assertIn(fresh.body, fight.hooks.flights)
        self.assertEqual(pool.total, 140)

    def test_new_wall_between_hook_owner_and_target_breaks_the_link(self):
        run, hog, enemy, _, vitals = self.session()
        self.step(run, hog, enemy, shift=True)
        hooks = run.match.combat.hooks
        for _ in range(50):
            self.step(run, hog, enemy)
            if hooks.controlled(enemy.body):
                break
        self.assertTrue(hooks.controlled(enemy.body))
        position = tuple(enemy.mover.position)
        with patch.object(run.match.combat, "_collision", return_value=self.wall(hog)):
            self.step(run, hog, enemy)
        self.assertFalse(hooks.controlled(enemy.body))
        self.assertEqual(tuple(enemy.mover.position), position)
        self.assertEqual(vitals.total, 170)

    def test_hook_interrupts_real_enemy_breather_healing(self):
        run, hog, enemy, _, vitals = self.session(enemy_hog=True)
        vitals.damage(400)
        run.match.combat._sync_body_health(enemy, vitals)
        self.step(run, hog, enemy, enemy_buttons=16)
        self.advance(run, hog, enemy, 20)
        self.step(run, hog, enemy, shift=True)
        hooks = run.match.combat.hooks
        for _ in range(45):
            before = vitals.total
            self.step(run, hog, enemy)
            if hooks.controlled(enemy.body):
                break
        self.assertTrue(hooks.controlled(enemy.body))
        stopped = vitals.total
        self.assertAlmostEqual(before - stopped, 15)
        self.assertEqual(run.match.combat.incoming_damage_scale(enemy), 1.0)
        self.advance(run, hog, enemy, 80)
        self.assertEqual(vitals.total, stopped)
        instance = next(i for i in enemy.body_script.component.instances.values() if i.graph.index == 0x0240)
        ability = next(s for s in instance.states.values() if s.node.pos == 1)
        self.assertFalse(ability.flags & 1)
        self.assertGreater(ability.remaining(), 0)

    def test_hook_interrupts_real_enemy_whole_hog(self):
        run, hog, enemy, _, _ = self.session(enemy_hog=True)
        component = enemy.body_script.component
        component.set(ULT_CHARGE, component.entity_var(ULT_COST).value())
        self.step(run, hog, enemy, enemy_buttons=32)
        ultimate, = enemy.body_script.ultimates
        self.assertTrue(ultimate.flags & 1)
        self.step(run, hog, enemy, shift=True)
        hooks = run.match.combat.hooks
        for _ in range(45):
            self.step(run, hog, enemy)
            if hooks.controlled(enemy.body):
                break
        self.assertTrue(hooks.controlled(enemy.body))
        self.assertFalse(ultimate.flags & 1)
        own = run.match.combat.vitals_of(next(t for t in run.match.combat._targets() if t.owner is hog))
        before = own.total
        self.advance(run, hog, enemy, 60)
        self.assertEqual(own.total, before)


if __name__ == "__main__":
    unittest.main()
