"""Actual00FA/Tracer inputs under reconstructed sphere interception, not native filter parity."""

import math
import unittest
from dataclasses import replace

import test_game_tracer_pulse_deflect as pulse_deflect_tests
from test_game_combat import Run, aim
from test_game_mover import FLOOR, wall

from ow174.game import combat, deflection
from ow174.game.collision import StaticCollisionProvider, World
from ow174.game.commands import Command
from ow174.game.content import heroes
from ow174.game.script.driver import ULT_COST


class TracerWinstonBarrierTests(unittest.TestCase):
    def arena(self):
        run = Run(names=('Tracer', 'Winston', 'Other'))
        match = run.match
        match.bots.clear()
        match._pending_bot_scripts.clear()
        player, owner, other = run.players
        match.switch_hero(player, heroes()[0x02E0000000000003])
        match.switch_hero(owner, heroes()[0x02E0000000000009])
        match.game_map = replace(match.game_map, free_for_all=False)
        player.team, owner.team, other.team = 0, 1, 1
        self.move(player, (0, 1, 10))
        self.move(owner, (0, 1, 0))
        self.move(other, (30, 1, 30))
        player.body_script.ult_seconds = 0
        return run, player, owner, other

    @staticmethod
    def move(player, point):
        # Explicit fixture reposition also discards the cached collision pose.
        player.mover.position[:] = point
        player.mover.velocity = (0, 0, 0)
        player.mover.body = None

    def cast(self, run, owner):
        run.step({owner: (0, 0, 16)})
        self.assertNotIn(owner.body, run.match.combat.winston_barriers.actors)
        for _ in range(3):
            run.step({})
        return run.match.combat.winston_barriers.actors[owner.body]

    @staticmethod
    def health(run, player):
        target = next(t for t in run.match.combat._targets() if t.owner is player)
        return run.match.combat.vitals_of(target)

    def test_actual_deployment_blocks_both_pistol_bullets_without_body_credit(self):
        run, player, owner, _ = self.arena()
        bubble = self.cast(run, owner)
        self.assertEqual((bubble.health, bubble.radius), (700, 5))
        self.assertAlmostEqual(bubble.expires-run.match.combat.now, 9)
        run.step({player: (-32768, 0, 1)})
        self.assertEqual(bubble.health, 688)
        self.assertEqual(self.health(run, owner).total, 500)
        self.assertFalse(player.client.messages(combat.HIT))
        self.assertEqual(player.body_script.component.entity_var(342).value(), 0)

    def test_fire_strike_damages_bubble_once_and_reaches_tracer(self):
        run, player, owner, caster = self.arena()
        self.assertTrue(run.match.switch_hero(caster, heroes()[0x02E0000000000007]))
        player.team = owner.team
        caster.team = 0
        self.move(caster, (0, 1, -8))
        bubble = self.cast(run, owner)
        for frame in range(34):
            run.step({caster: (0, 0, 16 if frame == 0 else 0)})
        strike, = run.match.combat.rockets
        self.assertEqual(strike.definition, combat.FIRE_STRIKE)
        for _ in range(60):
            run.step({})
        self.assertEqual(bubble.health, 600)
        self.assertEqual(strike.hit_barriers, {bubble.body})
        self.assertEqual(self.health(run, owner).total, 405)
        self.assertEqual(self.health(run, player).total, 50)

    @unittest.skipUnless(deflection.TAGS_PATH, "requires verified projectile definition tags")
    def test_fire_strike_resolves_bubble_and_active_defense_in_travel_order(self):
        for hero, button in ((0x02E0000000000029, 16), (0x02E000000000023B, 8)):
            for shield_first in (False, True):
                with self.subTest(hero=hero, shield_first=shield_first):
                    run = Run(names=("Tracer", "Winston", "Defender", "Reinhardt"))
                    match = run.match
                    match.bots.clear()
                    match._pending_bot_scripts.clear()
                    match.game_map = replace(match.game_map, free_for_all=False)
                    player, owner, defender, caster = run.players
                    for actor, hero_id in zip(
                        run.players, (0x02E0000000000003, 0x02E0000000000009, hero, 0x02E0000000000007),
                        strict=True,
                    ):
                        self.assertTrue(match.switch_hero(actor, heroes()[hero_id]))
                    player.team = owner.team = defender.team = 1
                    caster.team = 0
                    self.move(player, (0, 1, 7))
                    self.move(owner, (0, 1, 0))
                    self.move(defender, (0, 1, -2 if shield_first else -9))
                    self.move(caster, (0, 1, -14))
                    for _ in range(25):
                        run.step({})
                    bubble = self.cast(run, owner)
                    for frame in range(34):
                        run.step({caster: (0, 0, 16 if frame == 0 else 0)})
                    strike, = match.combat.rockets
                    self.assertEqual(strike.definition, combat.FIRE_STRIKE)
                    before = [self.health(run, actor).total for actor in (player, owner, defender)]
                    run.step({defender: (-32768, 0, button)})
                    for _ in range(65):
                        run.step({defender: (-32768, 0, 0)})
                    self.assertEqual(bubble.health, 600 if shield_first else 700)
                    self.assertEqual(strike.hit_barriers, {bubble.body} if shield_first else set())
                    after = [self.health(run, actor).total for actor in (player, owner, defender)]
                    self.assertEqual(after, before)
                    if button == 16:
                        self.assertIs(strike.shooter, defender)
                        self.assertEqual(strike.hit_entities, {caster.body})
                        self.assertEqual(self.health(run, caster).total, 405)
                    else:
                        self.assertNotIn(strike, match.combat.rockets)
                        self.assertFalse(strike.hit_entities)
                        grasp = next(i for i in defender.body_script.component.instances.values()
                                     if i.graph.index == 0x1D02)
                        self.assertEqual(grasp.vars[21312].value(), 100)

    def test_earthshatter_stops_at_winston_bubble_and_protects_tracer(self):
        run, player, owner, caster = self.arena()
        self.assertTrue(run.match.switch_hero(caster, heroes()[0x02E0000000000007]))
        player.team = owner.team
        caster.team = 0
        self.move(caster, (0, 1, -8))
        bubble = self.cast(run, owner)
        script = caster.body_script
        script.set_ult_charge(script.component.entity_var(ULT_COST).value())
        for frame in range(72):
            run.step({caster: (0, 0, 32 if frame == 0 else 0)})
        self.assertEqual(bubble.health, 650)
        self.assertEqual(self.health(run, owner).total, 500)
        self.assertEqual(self.health(run, player).total, 150)
        self.assertFalse(run.match.combat.is_stunned(player))

    def test_ray_without_body_still_hits_stationary_deployed_bubble(self):
        run, player, owner, _ = self.arena()
        bubble = self.cast(run, owner)
        self.move(owner, (20, 1, 0))
        run.step({player: (-32768, 0, 1)})
        self.assertEqual(bubble.health, 688)
        self.assertEqual(bubble.centre, (0, 1.1, 0))

    def test_breaking_first_bullet_has_no_spill_but_second_reaches_owner(self):
        run, player, owner, _ = self.arena()
        bubble = self.cast(run, owner)
        bubble.health = 3  # explicit near-break fixture, not native initial health
        run.step({player: (-32768, 0, 1)})
        self.assertEqual(bubble.health, 0)
        self.assertEqual(self.health(run, owner).total, 497)  # second6 against armour
        self.assertEqual(len(player.client.messages(combat.HIT)), 1)

    def test_friendly_barrier_allows_tracer_ray_to_enemy_inside(self):
        run, player, owner, other = self.arena()
        owner.team = player.team
        self.move(other, (0, 1, -1.5))
        bubble = self.cast(run, owner)
        run.step({player: (-32768, 0, 1)})
        self.assertEqual(bubble.health, 700)
        self.assertEqual(self.health(run, other).total, 188)

    def test_inside_enemy_can_hit_inside_but_cannot_shoot_through_exit(self):
        run, player, owner, other = self.arena()
        bubble = self.cast(run, owner)
        self.move(player, (0, 1, 3))
        run.step({player: (-32768, 0, 1)})
        self.assertEqual(self.health(run, owner).total, 494)
        self.assertEqual(bubble.health, 700)
        for _ in range(4):
            run.step({})
        self.move(player, (0, 1, 0))
        self.move(owner, (2, 1, 0))
        self.move(other, (0, 1, 10))
        for _ in range(20):
            run.step({player: (0, 0, 1)})
            if bubble.health < 700:
                break
        self.assertEqual(bubble.health, 688)
        self.assertEqual(self.health(run, other).total, 200)

    def test_static_wall_before_bubble_prevents_shield_and_body_damage(self):
        run, player, owner, _ = self.arena()
        bubble = self.cast(run, owner)
        triangles = [(-30, 1, -30, -30, 1, 30, 30, 1, 30),
                     (-30, 1, -30, 30, 1, 30, 30, 1, -30),
                     (-20, 1, 7, 20, 1, 7, 20, 6, 7),
                     (-20, 1, 7, 20, 6, 7, -20, 6, 7),
                     (-20, 1, 7, 20, 6, 7, 20, 1, 7),
                     (-20, 1, 7, -20, 6, 7, 20, 6, 7)]
        run.match.collision_world = World(triangles, name='wall before Winston bubble')
        for actor in run.players:
            actor.mover.use(world=run.match.collision_world)
        run.step({player: (-32768, 0, 1)})
        self.assertEqual(bubble.health, 700)
        self.assertEqual(self.health(run, owner).total, 500)

    def test_quick_melee_touches_shield_once_without_distant_body_damage(self):
        run, player, owner, _ = self.arena()
        bubble = self.cast(run, owner)
        self.move(player, (0, 1, 6))
        for _ in range(8):
            run.step({player: (-32768, 0, 0x800)})
        self.assertEqual(bubble.health, 670)
        self.assertEqual(self.health(run, owner).total, 500)

    def test_deployed_bubble_survives_owner_death_and_dead_input_cannot_refill(self):
        run, player, owner, _ = self.arena()
        bubble = self.cast(run, owner)
        self.move(player, (0, 1, 3))
        self.health(run, owner).now.update({combat.HEALTH_POOL: 1, combat.ARMOUR_POOL: 0})
        run.step({player: (-32768, 0, 1)})
        self.assertTrue(run.match.combat.is_dead(owner))
        self.assertIs(run.match.combat.winston_barriers.actors[owner.body], bubble)
        # First bullet kills the wounded owner; the second continues to the
        # far shield exit because no living body remains in front of it.
        self.assertEqual(bubble.health, 694)
        self.move(player, (0, 1, 10))
        for _ in range(20):
            run.step({owner: (0, 0, 16)})
        run.step({player: (-32768, 0, 1), owner: (0, 0, 16)})
        self.assertEqual(bubble.health, 682)

    def test_world_expiry_and_body_replacement_remove_actor_and_guard(self):
        run, _, owner, _ = self.arena()
        bubble = self.cast(run, owner)
        run.frame += math.ceil(9/combat.TICK)
        run.match.update(run.frame*combat.TICK, run.frame)
        self.assertNotIn(owner.body, run.match.combat.winston_barriers.actors)
        self.assertLessEqual(bubble.expires, run.match.combat.now)
        old_body = owner.body
        self.assertIn(old_body, run.match.combat.winston_barriers.seen)
        run.match.switch_hero(owner, owner.hero)
        self.assertNotIn(old_body, run.match.combat.winston_barriers.seen)
        self.assertNotEqual(owner.body, old_body)

    def test_melee_volume_can_touch_barrier_and_body_independently(self):
        run, player, owner, _ = self.arena()
        bubble = self.cast(run, owner)
        self.move(owner, (0, 1, 4.4))
        self.move(player, (0, 1, 6))
        for _ in range(8):
            run.step({player: (-32768, 0, 0x800)})
        self.assertEqual(bubble.health, 670)
        self.assertEqual(self.health(run, owner).total, 475)
        self.assertEqual(len(player.client.messages(combat.HIT)), 1)

    def test_cooldown_rejects_refill_and_expired_early_press_then_fresh_cast_reuses(self):
        run, player, owner, _ = self.arena()
        bubble = self.cast(run, owner)
        run.step({player: (-32768, 0, 1)})
        self.assertEqual(bubble.health, 688)
        for _ in range(20):
            run.step({owner: (0, 0, 16)})
        self.assertIs(run.match.combat.winston_barriers.actors[owner.body], bubble)
        self.assertEqual(bubble.health, 688)
        instance = next(i for i in owner.body_script.component.instances.values() if i.graph.index == 0xFA)
        ability = instance.states[2]
        for _ in range(1000):
            if ability.remaining() <= 1:
                break
            run.step({})
        self.assertGreater(ability.remaining(), 0.9)
        run.step({owner: (0, 0, 16)})  # too early for the authored short buffer
        run.step({})
        for _ in range(100):
            run.step({})
        self.assertEqual(ability.remaining(), 0)
        self.assertNotIn(owner.body, run.match.combat.winston_barriers.actors)
        fresh = self.cast(run, owner)
        self.assertIsNot(fresh, bubble)
        self.assertEqual(fresh.health, 700)

    def launch_pulse(self, run, player, yaw):
        script = player.body_script
        script.set_ult_charge(script.component.entity_var(ULT_COST).value())
        bombs = run.match.combat.pulse_bombs.bombs
        for index in range(60):
            run.step({player: (yaw, 0, 32 if index == 0 else 0)})
            if bombs and bombs[0].stuck:
                return bombs[0]
        self.fail("actual Pulse did not reach a surface")

    def launch_flying_pulse(self, run, player):
        script = player.body_script
        script.set_ult_charge(script.component.entity_var(ULT_COST).value())
        for index in range(20):
            run.step({player: (-32768, 0, 32 if index == 0 else 0)})
            if run.match.combat.pulse_bombs.bombs:
                break
        bomb, = run.match.combat.pulse_bombs.bombs
        for _ in range(3):
            run.step({})
        self.assertFalse(bomb.stuck)
        return bomb

    def run_winston_commands(self, run, owner, *, cast=None, batched):
        commands = [Command(run.frame + offset, buttons=16 if offset == cast else 0)
                    for offset in range(1, 31)]
        if batched:
            owner.take_commands(commands)
            run.frame += len(commands)
            run.match.update(run.frame * combat.TICK, run.frame)
        else:
            for command in commands:
                run.step({owner: (command.yaw, command.pitch, command.buttons)})

    def test_batched_winston_deployment_preserves_pulse_contact_order(self):
        for early in (False, True):
            expiry = []
            for batched in (False, True):
                with self.subTest(early=early, batched=batched):
                    run, player, owner, target = self.arena()
                    self.move(player, (0, 1, 7.5 if early else 6.5))
                    self.move(target, (0, 1, 3))
                    bomb = self.launch_flying_pulse(run, player)
                    manager = run.match.combat.winston_barriers
                    self.assertNotIn(owner.body, manager.actors)
                    self.run_winston_commands(run, owner, cast=1 if early else 8, batched=batched)
                    bubble = manager.actors[owner.body]
                    self.assertEqual(bubble.health, 695 if early else 700)
                    self.assertEqual(self.health(run, target).total, 200 if early else 195)
                    self.assertIs(bomb.attached_bubble, bubble if early else None)
                    self.assertEqual(bomb.attached, None if early else target.body)
                    expiry.append(bubble.expires)
            self.assertEqual(len(expiry), 2)
            self.assertAlmostEqual(*expiry, places=6)

    def run_flashbang_and_winston_cast(self, *, cast, batched):
        run, player, owner, target = self.arena()
        self.move(player, (0, 1, 7.5))
        self.move(target, (0, 1, 3))
        cassidy, _ = pulse_deflect_tests.PulseDeflectTests().support(run, owner, 0x02E0000000000042)
        cassidy.team = player.team
        self.move(cassidy, (0, 1, -3))
        fight = run.match.combat
        facing = aim(fight._eye(cassidy), (0, 2, 0))
        bomb = self.launch_flying_pulse(run, player)
        run.step({cassidy: (*facing, 16)})
        for _ in range(2):
            run.step({})
        start = run.frame
        rocket, = fight.rockets
        self.assertIsNotNone(rocket.last_tick)
        self.assertFalse(fight.is_stunned(owner))
        self.run_winston_commands(run, owner, cast=cast, batched=batched)
        self.assertTrue(fight.is_stunned(owner))
        self.assertEqual(fight.stuns[owner.body], start + 3 + 50)
        return run, owner, target, bomb, start

    def test_flashbang_after_cast_does_not_delay_committed_winston_deployment(self):
        for batched in (False, True):
            with self.subTest(batched=batched):
                run, owner, target, bomb, start = self.run_flashbang_and_winston_cast(cast=1, batched=batched)
                manager = run.match.combat.winston_barriers
                bubble = manager.actors[owner.body]
                self.assertAlmostEqual(bubble.expires, (start + 4) * combat.TICK + 9, places=6)
                self.assertEqual(bubble.health, 695)
                self.assertEqual(self.health(run, target).total, 200)
                self.assertIs(bomb.attached_bubble, bubble)
                self.assertIsNone(bomb.attached)
                for _ in range(55):
                    run.step({})
                self.assertIs(manager.actors[owner.body], bubble)
                self.assertEqual(bubble.health, 345)

    def test_flashbang_before_cast_rejects_winston_deployment_without_spending_cooldown(self):
        for batched in (False, True):
            with self.subTest(batched=batched):
                run, owner, target, bomb, _ = self.run_flashbang_and_winston_cast(cast=4, batched=batched)
                manager = run.match.combat.winston_barriers
                self.assertNotIn(owner.body, manager.actors)
                self.assertNotIn(owner.body, manager.pending)
                self.assertIsNone(bomb.attached_bubble)
                self.assertEqual(bomb.attached, target.body)
                self.assertEqual(self.health(run, target).total, 195)
                instances = owner.body_script.component.instances.values()
                instance = next(i for i in instances if i.graph.index == 0xFA)
                self.assertEqual(instance.states[2].remaining(), 0)
                for _ in range(55):
                    run.step({})
                self.assertNotIn(owner.body, manager.actors)
                self.assertEqual(self.cast(run, owner).health, 700)

    def test_batched_winston_expiry_keeps_earlier_pulse_interception(self):
        for batched in (False, True):
            with self.subTest(batched=batched):
                run, player, owner, target = self.arena()
                bubble = self.cast(run, owner)
                self.move(player, (0, 1, 6.5))
                self.move(target, (0, 1, 3))
                run.frame = math.ceil(bubble.expires / combat.TICK) - 25
                run.match.update(run.frame * combat.TICK, run.frame)
                bomb = self.launch_flying_pulse(run, player)
                manager = run.match.combat.winston_barriers
                self.assertIs(manager.actors[owner.body], bubble)
                self.run_winston_commands(run, owner, batched=batched)
                self.assertNotIn(owner.body, manager.actors)
                self.assertEqual(bubble.health, 695)
                self.assertEqual(self.health(run, target).total, 200)
                self.assertTrue(bomb.stuck)
                self.assertIsNone(bomb.attached_bubble)
                self.assertIsNone(bomb.attached)

    def test_external_pulse_sticks_to_bubble_and_blast_damages_surface_once(self):
        run, player, owner, other = self.arena()
        bubble = self.cast(run, owner)
        self.move(player, (0, 1, 6.5))
        self.move(owner, (0, 1, 4))
        self.move(other, (0, 1, 3.8))
        bomb = self.launch_pulse(run, player, -32768)
        self.assertIs(bomb.attached_bubble, bubble)
        self.assertIsNone(bomb.attached)
        self.assertEqual(bubble.health, 695)
        for _ in range(70):
            run.step({})
        self.assertFalse(run.match.combat.pulse_bombs.bombs)
        self.assertEqual(bubble.health, 345)
        self.assertEqual(self.health(run, owner).total, 500)
        self.assertEqual(self.health(run, other).total, 200)

    def test_pulse_crossing_from_inside_sticks_to_inner_surface(self):
        run, player, owner, other = self.arena()
        bubble = self.cast(run, owner)
        self.move(player, (0, 1, 3))
        self.move(other, (0, 1, 6))
        bomb = self.launch_pulse(run, player, 0)
        self.assertIs(bomb.attached_bubble, bubble)
        self.assertLess(math.dist(bomb.position, bubble.centre), bubble.radius)
        for _ in range(70):
            run.step({})
        self.assertEqual(bubble.health, 345)
        self.assertEqual(self.health(run, other).total, 200)

    def test_bubble_retirement_detaches_pulse_and_preserves_the_fuse(self):
        run, player, owner, other = self.arena()
        bubble = self.cast(run, owner)
        self.move(player, (0, 1, 6.5))
        self.move(other, (0, 1, 4))
        bomb = self.launch_pulse(run, player, -32768)
        self.assertIs(bomb.attached_bubble, bubble)
        position, fuse = bomb.position, bomb.fuse_left
        bubble.expires = run.match.combat.now
        run.step({})
        self.assertIsNone(bomb.attached_bubble)
        self.assertEqual(bomb.position, position)
        self.assertAlmostEqual(bomb.fuse_left, fuse - combat.TICK)
        for _ in range(70):
            run.step({})
        self.assertTrue(self.health(run, other).dead)
        self.assertEqual(bubble.health, 695)

    def test_pulse_inside_and_through_allied_bubble_keeps_body_contact(self):
        for allied in (False, True):
            with self.subTest(allied=allied):
                run, player, owner, other = self.arena()
                if allied:
                    owner.team = player.team
                bubble = self.cast(run, owner)
                victim = other if allied else owner
                self.move(player, (0, 1, 6.5 if allied else 3))
                self.move(victim, (0, 1, 3 if allied else 0))
                if allied:
                    self.move(owner, (1.5, 1, 0))
                bomb = self.launch_pulse(run, player, -32768)
                self.assertIsNone(bomb.attached_bubble)
                self.assertEqual(bomb.attached, victim.body)
                self.assertEqual(bubble.health, 700)

    def test_pulse_break_has_no_blast_spill_through_bubble(self):
        run, player, owner, _ = self.arena()
        bubble = self.cast(run, owner)
        bubble.health = 100
        self.move(player, (0, 1, 6.5))
        self.move(owner, (0, 1, 4))
        bomb = self.launch_pulse(run, player, -32768)
        self.assertIs(bomb.attached_bubble, bubble)
        self.assertEqual(bubble.health, 95)
        for _ in range(70):
            run.step({})
        self.assertEqual(bubble.health, 0)
        self.assertNotIn(owner.body, run.match.combat.winston_barriers.actors)
        self.assertEqual(self.health(run, owner).total, 500)

    def test_static_wall_wins_before_bubble_contact_and_occludes_pulse_blast(self):
        run, player, owner, _ = self.arena()
        bubble = self.cast(run, owner)
        self.move(player, (0, 1, 7))
        self.move(owner, (0, 1, 4))
        mesh = World(FLOOR + wall(-20, 6, 20, 6))
        run.match.collision_provider = StaticCollisionProvider(mesh)
        run.match.collision_world = mesh
        for actor in run.players:
            actor.mover.use(world=mesh)
        bomb = self.launch_pulse(run, player, -32768)
        self.assertIsNone(bomb.attached_bubble)
        self.assertIsNone(bomb.attached)
        for _ in range(70):
            run.step({})
        self.assertEqual(bubble.health, 700)
        self.assertEqual(self.health(run, owner).total, 500)


if __name__ == '__main__':
    unittest.main()
