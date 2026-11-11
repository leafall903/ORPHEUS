"""Actual Roadhog inputs and Zarya casts; finite sphere/target/credit policy reconstructed."""

import os
import unittest
from dataclasses import replace
from unittest.mock import patch

import test_game_roadhog_defenses as phases
from test_game_combat import aim

from ow174.game import combat, defenses, zarya_barriers
from ow174.game.collision import World
from ow174.game.content import heroes
from ow174.game.script import expr, nodes

ZARYA = 0x02E0000000000068


class RoadhogZaryaTests(unittest.TestCase):
    health = phases.RoadhogDefenseTests.health

    def step(self, run, hog, caster, *, fire=0, cast=0, cast_aim=(0, 0), attack_aim=(0, 0)):
        run.step({hog: (*attack_aim, fire), caster: (*cast_aim, cast)})

    def session(self, *, projected=False, distance=4):
        run, hog, caster, rear = phases.RoadhogDefenseTests().session("Zarya", distance=distance)
        y = hog.mover.position[1]
        if projected:
            run.match.game_map = replace(run.match.game_map, free_for_all=False)
            hog.team, caster.team, rear.team = 0, 1, 1
            caster.mover.position[:] = (-4, y, distance)
            rear.mover.position[:] = (0, y, distance)
            caster.mover.body = rear.mover.body = None
        for _ in range(30):
            self.step(run, hog, caster)
        target = rear if projected else caster
        point = (target.mover.position[0], y + 1, target.mover.position[2])
        cast_aim = aim(run.match.combat._eye(caster), point)
        self.step(run, hog, caster, cast_aim=cast_aim)  # actual aim precedes the cast edge
        return run, hog, caster, target, cast_aim

    def cast(self, run, hog, caster, target, cast_aim, *, projected=False):
        self.step(run, hog, caster, cast=16 if projected else 8, cast_aim=cast_aim)
        bubble = run.match.combat.zarya_bubbles.for_target(target)
        self.assertIsNotNone(bubble)
        self.assertEqual(bubble.health, 200)
        self.assertIs(bubble.caster, caster)
        self.assertIs(bubble.owner, target)
        return bubble

    def test_primary_slug_fragments_and_outer_sphere_use_separate_pool_and_fund_caster_energy(self):
        for projected in (False, True):
            for button, distance in ((1, 4), (2, 4), (2, 15)):
                with self.subTest(projected=projected, button=button, distance=distance):
                    run, hog, caster, target, angles = self.session(projected=projected, distance=distance)
                    bubble = self.cast(run, hog, caster, target, angles, projected=projected)
                    weapon = next(i for i in hog.body_script.component.instances.values()
                                  if i.graph.index == 0x1C6)
                    before = weapon.vars[53].value()
                    self.step(run, hog, caster, fire=button)
                    for _ in range(35):
                        self.step(run, hog, caster)
                    self.assertAlmostEqual(
                        bubble.health, 150 if button == 2 and distance == 4 else 35, places=3)
                    self.assertEqual(self.health(run, target), 20000)
                    self.assertEqual(weapon.vars[53].value(), before - 1)
                    self.assertEqual(hog.body_script.component.entity_var(342).value(), 0)
                    self.assertFalse(hog.client.messages(combat.HIT))
                    self.assertGreater(caster.body_script.component.entity_var(87).value(), 5)
                    if projected:
                        self.assertIsNone(target.body_script.component.entity_var(87, create=False))
                    self.assertFalse(run.match.combat.rockets)

    def test_projected_bubble_protects_active_grasp_from_hook(self):
        run, hog, zarya, sigma, _ = self.session(projected=True, distance=5)
        match = run.match
        fight = match.combat
        match.switch_hero(sigma, heroes()[0x02E000000000023B])
        y = hog.mover.position[1]
        sigma.mover.position[:] = (0, y, 5)
        sigma.mover.body = None
        fight.vitals[sigma.body] = combat.Vitals(sigma.body, 20000)
        point = (0, y + 1, 5)
        zarya_aim = aim(fight._eye(zarya), point)
        hog_aim = aim(fight._eye(hog), point)
        for _ in range(35):
            run.step({zarya: (*zarya_aim, 0), sigma: (-32768, 0, 0)})
        run.step({zarya: (*zarya_aim, 16), sigma: (-32768, 0, 8)})
        for _ in range(6):
            run.step({sigma: (-32768, 0, 0)})
        bubble = fight.zarya_bubbles.for_target(sigma)
        self.assertIsNotNone(bubble)
        self.assertIs(bubble.caster, zarya)
        grasp = next(i for i in sigma.body_script.component.instances.values() if i.graph.index == 0x1D02)
        self.assertTrue(grasp.states[1].active)

        origin, direction = fight._eye(hog), (0, 0, 1)
        cover = fight._weapon_cover(hog, origin, direction, 10)
        gate = defenses.projectile_gate(origin, direction, 10, fight._enemies(hog))
        self.assertIs(cover[1], bubble)
        self.assertIs(gate[1].owner, sigma)
        self.assertLess(gate[0], cover[0])

        run.step({hog: (*hog_aim, 2), sigma: (-32768, 0, 0)})
        for _ in range(45):
            run.step({sigma: (-32768, 0, 0)})
        self.assertEqual(grasp.vars[21312].value(), 50)
        self.assertEqual(bubble.health, 200)
        self.assertEqual(self.health(run, sigma), 20000)
        self.assertEqual(hog.body_script.component.entity_var(342).value(), 0)

        run.step({hog: (*hog_aim, 8), sigma: (-32768, 0, 0)})
        for _ in range(35):
            run.step({sigma: (-32768, 0, 0)})
            self.assertFalse(fight.hooks.controlled(sigma.body))
        self.assertEqual(bubble.health, 170)
        self.assertTrue(grasp.states[1].active)
        self.assertEqual(self.health(run, sigma), 20000)
        self.assertEqual(grasp.vars[21312].value(), 50)
        self.assertEqual(hog.body_script.component.entity_var(342).value(), 0)

        for _ in range(65):
            run.step({sigma: (-32768, 0, 0)})
        self.assertFalse(grasp.states[1].active)
        vitals = fight.vitals[sigma.body]
        self.assertAlmostEqual(vitals.temporary_shields[defenses.SHIELD_TAG].now, 30, places=5)
        self.assertEqual(sum(vitals.now.values()), 20000)

    def test_hook_damages_finite_pool_without_control_or_interrupt_and_keeps_cooldown(self):
        for projected in (False, True):
            with self.subTest(projected=projected):
                run, hog, caster, target, angles = self.session(projected=projected)
                bubble = self.cast(run, hog, caster, target, angles, projected=projected)
                self.step(run, hog, caster, fire=8)
                for _ in range(40):
                    self.step(run, hog, caster)
                    self.assertFalse(run.match.combat.hooks.controlled(target.body))
                self.assertEqual(bubble.health, 170)
                self.assertTrue(bubble.phase.active)
                self.assertEqual(self.health(run, target), 20000)
                ability = next(s for i in hog.body_script.component.instances.values()
                               if i.graph.index == 0x1C8
                               for s in i.states.values() if s.node.pos == 11)
                self.assertGreater(ability.remaining(), 0)
                self.assertEqual(hog.body_script.component.entity_var(342).value(), 0)

    def test_melee_final_whole_hit_is_absorbed_then_next_shot_hits_body(self):
        run, hog, caster, target, angles = self.session(distance=2.5)
        bubble = self.cast(run, hog, caster, target, angles)
        self.step(run, hog, caster, fire=1)
        for _ in range(30):
            self.step(run, hog, caster)
        self.assertAlmostEqual(bubble.health, 35, places=4)
        self.step(run, hog, caster, fire=8)
        for _ in range(35):
            self.step(run, hog, caster)
        self.assertAlmostEqual(bubble.health, 5, places=4)
        self.step(run, hog, caster, fire=0x800)
        for _ in range(25):
            self.step(run, hog, caster)
        self.assertEqual(bubble.health, 0)
        self.assertFalse(bubble.phase.active)
        self.assertIsNone(run.match.combat.zarya_bubbles.for_target(target))
        self.assertEqual(self.health(run, target), 20000)
        for _ in range(45):
            self.step(run, hog, caster)  # finish the authored melee window before fresh primary input
        self.step(run, hog, caster, fire=1)
        for _ in range(35):
            self.step(run, hog, caster)
        self.assertLess(self.health(run, target), 20000)
        self.assertGreater(hog.body_script.component.entity_var(342).value(), 0)

    def test_whole_hog_spends_charge_breaks_pool_and_continues_into_body(self):
        run, hog, caster, target, angles = self.session()
        bubble = self.cast(run, hog, caster, target, angles)
        hog.body_script.set_ult_charge(2240)
        self.step(run, hog, caster, fire=32)
        for _ in range(80):
            self.step(run, hog, caster)
        self.assertEqual(bubble.health, 0)
        self.assertLess(self.health(run, target), 20000)
        self.assertEqual(hog.body_script.component.entity_var(342).value(), 0)
        self.assertTrue(any(s.flags & nodes.Ability.RUNNING for s in hog.body_script.ultimates))

    def test_projected_barrier_clears_live_whole_hog_force_until_break_or_expiry(self):
        for depleted in (False, True):
            with self.subTest(depleted=depleted):
                self.check_projected_whole_hog_overlap(depleted)

    def check_projected_whole_hog_overlap(self, depleted):
        run, hog, caster, target, angles = self.session(projected=True)
        fight = run.match.combat
        script = hog.body_script
        ultimate, = script.ultimates
        script.set_ult_charge(script.component.entity_var(338).value())
        self.step(run, hog, caster, fire=32)
        for _ in range(55):
            if target.body in fight.impulses.live:
                break
            self.step(run, hog, caster, cast_aim=angles)
        old_motion = fight.impulses.live.get(target.body)
        self.assertIsNotNone(old_motion)
        self.assertTrue(any(old_motion.velocity))
        self.assertLess(self.health(run, target), 20000)

        bubble = self.cast(run, hog, caster, target, angles, projected=True)
        self.assertTrue(ultimate.flags & nodes.Ability.RUNNING)
        energy = caster.body_script.component.entity_var(87)
        starting_energy = expr.to_float(energy.value())
        before = self.health(run, target)
        hits = len(hog.client.messages(combat.HIT))
        x, _, z = target.mover.position
        for _ in range(25):
            self.step(run, hog, caster)
            self.assertNotIn(target.body, fight.impulses.live)
            self.assertEqual(self.health(run, target), before)
            self.assertEqual((target.mover.position[0], target.mover.position[2]), (x, z))
            if bubble.health < 200:
                break
        self.assertLess(bubble.health, 200)
        self.assertGreater(bubble.health, 0)
        self.assertEqual(len(hog.client.messages(combat.HIT)), hits)
        self.assertAlmostEqual(energy.value() - starting_energy, (200 - bubble.health) * 0.2, places=3)

        if depleted:
            breaking_rays = []
            shoot = fight._shoot_pellet

            def observe(*args, **kwargs):
                pool_before = bubble.health
                body_before = self.health(run, target)
                result = shoot(*args, **kwargs)
                if pool_before > 0 and bubble.health == 0:
                    breaking_rays.append((body_before, self.health(run, target),
                                          target.body in fight.impulses.live))
                return result

            with patch.object(fight, "_shoot_pellet", side_effect=observe):
                for _ in range(25):
                    self.step(run, hog, caster)
                    if bubble.health == 0:
                        break
            self.assertEqual(bubble.health, 0)
            self.assertEqual(breaking_rays, [(before, before, False)])
        else:
            remaining = bubble.health
            for _ in range(145):
                self.step(run, hog, caster, attack_aim=(16384, 0))
                self.assertEqual(bubble.health, remaining)
                self.assertNotIn(target.body, fight.impulses.live)
                self.assertEqual(self.health(run, target), before)
                self.assertEqual((target.mover.position[0], target.mover.position[2]), (x, z))
                if not bubble.phase.active:
                    break
        self.assertFalse(bubble.phase.active)
        self.assertIsNone(fight.zarya_bubbles.for_target(target))
        self.assertTrue(ultimate.flags & nodes.Ability.RUNNING)

        for _ in range(25):
            if target.body in fight.impulses.live:
                break
            self.step(run, hog, caster)
        new_motion = fight.impulses.live.get(target.body)
        self.assertIsNotNone(new_motion)
        self.assertIsNot(new_motion, old_motion)
        self.assertLess(self.health(run, target), before)
        self.assertEqual(script.component.entity_var(342).value(), 0)
        self.step(run, hog, caster)
        self.assertGreater(target.mover.position[2], z)

    def test_melee_can_hit_outer_bubble_without_body_contact(self):
        run, hog, caster, target, angles = self.session(distance=3.2)
        bubble = self.cast(run, hog, caster, target, angles)
        self.step(run, hog, caster, fire=0x800)
        for _ in range(25):
            self.step(run, hog, caster)
        self.assertEqual(bubble.health, 170)
        self.assertEqual(self.health(run, target), 20000)

    def test_wall_ally_miss_and_rear_sphere_policy(self):
        for mode in ("wall", "ally", "miss", "rear", "outer"):
            with self.subTest(mode=mode):
                run, hog, caster, target, angles = self.session()
                if mode == "ally":
                    run.match.game_map = replace(run.match.game_map, free_for_all=False)
                    caster.team = hog.team
                if mode == "wall":
                    y = hog.mover.position[1]
                    run.match.collision_world = World([
                        (-10, y - 10, 1, 10, y - 10, 1, 10, y + 10, 1),
                        (-10, y - 10, 1, 10, y + 10, 1, -10, y + 10, 1),
                    ])
                bubble = self.cast(run, hog, caster, target, angles)
                if mode == "rear":
                    hog.mover.position[2] = 8
                    hog.mover.body = None
                    rear = run.players[2]
                    rear.mover.position[0] = 20
                    rear.mover.body = None
                attack = (-32768, 0) if mode == "rear" else ((16384, 0) if mode == "miss" else (0, 0))
                if mode == "outer":
                    attack = aim(run.match.combat._eye(hog), (1.1, hog.mover.position[1] + 1, 4))
                self.step(run, hog, caster, fire=2, attack_aim=attack)
                for _ in range(35):
                    self.step(run, hog, caster, attack_aim=attack)
                self.assertEqual(bubble.health, 150 if mode in ("rear", "outer") else 200)
                self.assertEqual(self.health(run, target), 20000)

    def test_expiry_repress_refusal_cooldown_expiry_and_fresh_capacity(self):
        for projected in (False, True):
            with self.subTest(projected=projected):
                run, hog, caster, target, angles = self.session(projected=projected)
                first = self.cast(run, hog, caster, target, angles, projected=projected)
                self.step(run, hog, caster)
                self.step(run, hog, caster, cast=16 if projected else 8, cast_aim=angles)
                self.assertIs(run.match.combat.zarya_bubbles.for_target(target), first)
                for _ in range(145):
                    self.step(run, hog, caster, cast_aim=angles)
                self.assertFalse(first.phase.active)
                self.assertIsNone(run.match.combat.zarya_bubbles.for_target(target))
                self.step(run, hog, caster, cast=16 if projected else 8, cast_aim=angles)
                self.assertIsNone(run.match.combat.zarya_bubbles.for_target(target))
                instance = next(i for i in caster.body_script.component.instances.values()
                                if i.graph.index == (0x25C if projected else 0x244))
                self.assertGreater(instance.vars[61].value(), 0)
                for _ in range(710):
                    self.step(run, hog, caster, cast_aim=angles)
                second = self.cast(run, hog, caster, target, angles, projected=projected)
                self.assertNotEqual(first.entity, second.entity)
                self.assertGreater(second.counter, first.counter)

    def test_projected_target_query_rejects_enemy_self_wall_range_dead_and_ffa(self):
        for mode in ("enemy", "self", "wall", "range", "dead", "ffa", "miss"):
            with self.subTest(mode=mode):
                run, hog, caster, target, angles = self.session(projected=True)
                if mode in ("enemy", "self"):
                    target.team = hog.team
                    if mode == "self":
                        angles = aim(run.match.combat._eye(caster), caster.mover.position)
                elif mode == "range":
                    target.mover.position[0] += 35
                    target.mover.body = None
                    angles = aim(run.match.combat._eye(caster), run.match.combat._eye(target))
                elif mode == "dead":
                    run.match.combat.vitals[target.body].died_at = run.match.combat.now
                elif mode == "ffa":
                    run.match.game_map = replace(run.match.game_map, free_for_all=True)
                elif mode == "miss":
                    angles = (angles[0] - 32768, angles[1])
                else:
                    y = caster.mover.position[1]
                    run.match.collision_world = World([
                        (-2, y - 10, -10, -2, y + 10, -10, -2, y + 10, 20),
                        (-2, y - 10, -10, -2, y + 10, 20, -2, y - 10, 20),
                    ])
                for _ in range(4):
                    self.step(run, hog, caster, cast_aim=angles)
                self.step(run, hog, caster, cast=16, cast_aim=angles)
                self.assertIsNone(run.match.combat.zarya_bubbles.for_target(target))
                instance = next(i for i in caster.body_script.component.instances.values()
                                if i.graph.index == 0x25C)
                self.assertEqual(instance.vars[61].value(), 0)

    def test_projected_target_replacement_and_personal_owner_replacement_retire_old_pool(self):
        for projected in (False, True):
            with self.subTest(projected=projected):
                run, hog, caster, target, angles = self.session(projected=projected)
                bubble = self.cast(run, hog, caster, target, angles, projected=projected)
                old = target.body
                run.match.switch_hero(target, heroes()[0x02E0000000000040])
                self.assertIsNone(run.match.combat.zarya_bubbles.for_target(target))
                self.assertFalse(any(b.body == old for b in run.match.combat.zarya_bubbles.live.values()))
                self.assertFalse(run.match.combat.zarya_bubbles.live_source(bubble))

    def test_projected_rear_target_keeps_self_bubble_outgoing_attacks_usable(self):
        run, hog, caster, target, angles = self.session(projected=True)
        bubble = self.cast(run, hog, caster, target, angles, projected=True)
        target.mover.yaw = -32768
        before = self.health(run, hog)
        for _ in range(20):
            run.step({target: (-32768, 0, 1)})
        self.assertLess(self.health(run, hog), before)
        self.assertEqual(bubble.health, 200)

    def test_projected_bubble_breaks_active_hook_link_without_contact_damage(self):
        run, hog, caster, target, _ = self.session(projected=True, distance=10)
        self.step(run, hog, caster, fire=8)
        for _ in range(70):
            self.step(run, hog, caster)
            if run.match.combat.hooks.controlled(target.body):
                break
        self.assertTrue(run.match.combat.hooks.controlled(target.body))
        point = tuple(target.mover.position[k] + (1 if k == 1 else 0) for k in range(3))
        angles = aim(run.match.combat._eye(caster), point)
        bubble = self.cast(run, hog, caster, target, angles, projected=True)
        for _ in range(15):
            self.step(run, hog, caster)
        self.assertFalse(run.match.combat.hooks.controlled(target.body))
        self.assertEqual(bubble.health, 200)
        self.assertEqual(self.health(run, target), 19970)

    @unittest.skipUnless(os.environ.get("ORPHEUS_NPC_GRAPHS"), "actual NPC graph bundle required")
    def test_projected_bubble_breaks_each_five_npc_hook_link(self):
        import test_game_roadhog_npcs as actual
        from ow174.game import npc

        actual.RoadhogFiveNpcTests.setUpClass()
        self.addCleanup(actual.RoadhogFiveNpcTests.tearDownClass)
        for definition in actual.RoadhogFiveNpcTests.definitions.values():
            with self.subTest(npc=definition.name):
                run, hog, caster, rear, _ = self.session(projected=True, distance=12)
                fight = run.match.combat
                # Leave enough lateral distance to keep the moving capsule in the aim cone.
                caster.mover.position[0] = -8
                caster.mover.body = None
                rear.mover.position[0] = 20
                rear.mover.body = None
                for player in run.players:
                    player.client = actual.NpcClient(player)
                bot = npc.spawn(run.match, definition, (0, hog.mover.position[1], 14), team=caster.team)
                target = next(t for t in fight._targets() if t.owner is bot)
                pool = fight.vitals_of(target)
                before = pool.total
                low, high = target.segment()
                point = tuple((a + b) / 2 for a, b in zip(low, high, strict=True))
                look = aim(fight._eye(hog), point)
                self.step(run, hog, caster, fire=8, attack_aim=look)
                for _ in range(60):
                    self.step(run, hog, caster, attack_aim=look)
                    if fight.hooks.controlled(bot.entity):
                        break
                self.assertTrue(fight.hooks.controlled(bot.entity))
                self.assertEqual(pool.total, before - 30)
                self.assertIsNone(fight.zarya_bubbles.for_target(bot))
                point = (bot.position[0], bot.position[1] + target.height / 2, bot.position[2])
                angles = aim(fight._eye(caster), point)
                self.step(run, hog, caster, cast_aim=angles)
                self.assertTrue(fight.hooks.controlled(bot.entity))
                point = (bot.position[0], bot.position[1] + target.height / 2, bot.position[2])
                angles = aim(fight._eye(caster), point)
                bubble = self.cast(run, hog, caster, bot, angles, projected=True)
                for _ in range(6):
                    self.step(run, hog, caster)
                self.assertFalse(fight.hooks.controlled(bot.entity))
                self.assertNotIn(hog.body, fight.hooks.flights)
                position = bot.position
                for _ in range(6):
                    self.step(run, hog, caster)
                self.assertEqual(bot.position, position)
                self.assertEqual(bot.velocity, (0, 0, 0))
                self.assertEqual(bubble.health, 200)
                self.assertEqual(pool.total, before - 30)

    def test_breather_does_not_reduce_projected_shield_damage_and_healing_continues(self):
        from ow174.game.content import SOLDIER

        run, shooter, caster, target, _ = self.session(projected=True)
        run.match.switch_hero(shooter, heroes()[SOLDIER])
        run.match.switch_hero(target, heroes()[0x02E0000000000040])
        y = caster.mover.position[1]
        shooter.mover.position[:] = (0, y, 0)
        target.mover.position[:] = (0, y, 4)
        shooter.mover.body = target.mover.body = None
        own = combat.Vitals(target.body, 600)
        own.damage(300)
        run.match.combat.vitals[target.body] = own
        for _ in range(30):
            self.step(run, shooter, caster)
        run.step({target: (0, 0, 16)})
        for _ in range(25):
            self.step(run, shooter, caster)
        self.assertEqual(run.match.combat.incoming_damage_scale(target), .5)
        angles = aim(run.match.combat._eye(caster), run.match.combat._eye(target))
        bubble = self.cast(run, shooter, caster, target, angles, projected=True)
        before = own.total
        self.step(run, shooter, caster, fire=1)
        for _ in range(15):
            self.step(run, shooter, caster)
        self.assertEqual(bubble.health, 180)
        self.assertGreater(own.total, before)

    def test_primary_breaks_projected_shield_then_breather_reduces_remaining_pellets(self):
        run, shooter, caster, target, _ = self.session(projected=True, distance=2)
        fight = run.match.combat
        run.match.switch_hero(target, shooter.hero)
        y = shooter.mover.position[1]
        target.mover.position[:] = (0, y, 2)
        target.mover.body = None
        for _ in range(30):
            self.step(run, shooter, caster)
        run.step({target: (0, 0, 16)})
        self.assertEqual(fight.incoming_damage_scale(target), 0.5)
        angles = aim(fight._eye(caster), fight._eye(target))
        bubble = self.cast(run, shooter, caster, target, angles, projected=True)
        bubble.health = 10
        energy = caster.body_script.component.entity_var(87)
        before_energy = expr.to_float(energy.value())
        charge = shooter.body_script.component.entity_var(342)
        before_charge = charge.value()
        attack_aim = aim(fight._eye(shooter), (0, y + 0.7, 2))
        self.step(run, shooter, caster, fire=1, attack_aim=attack_aim)
        for _ in range(20):
            self.step(run, shooter, caster)

        self.assertEqual(bubble.health, 0)
        self.assertIsNone(fight.zarya_bubbles.for_target(target))
        self.assertAlmostEqual(energy.value() - before_energy, 2, places=3)
        damage = 23 * 3.3
        self.assertAlmostEqual(self.health(run, target), 600 - damage, places=3)
        # Roadhog's victim passive scales the attacker's credit after mitigation.
        self.assertAlmostEqual(charge.value() - before_charge, damage * .75, places=3)
        self.assertEqual(fight.incoming_damage_scale(target), 0.5)

    def test_shared_flashbang_breaking_pool_does_not_spill_stun_or_body_damage(self):
        run, hog, caster, target, angles = self.session(distance=2.5)
        # Warm the helper before the finite shield phase; deplete it with actual Roadhog input.
        from test_game_combat import in_world

        source = run.match.add_player(8, "Flashbang", 0x02E0000000000042, hog.team, False)
        in_world(run.match, source)
        run.match.switch_hero(source, heroes()[0x02E0000000000042])
        source.mover.position[:] = (-2, hog.mover.position[1], 0)
        source.mover.body = None
        run.players.append(source)
        for _ in range(30):
            self.step(run, hog, caster)
        bubble = self.cast(run, hog, caster, target, angles)
        self.step(run, hog, caster, fire=1)
        for _ in range(30):
            self.step(run, hog, caster)
        self.step(run, hog, caster, fire=8)
        for _ in range(35):
            self.step(run, hog, caster)
        self.assertAlmostEqual(bubble.health, 5, places=4)
        look = aim(run.match.combat._eye(source), run.match.combat._eye(target))
        run.step({source: (*look, 16)})
        for _ in range(35):
            run.step({})
        self.assertEqual(bubble.health, 0)
        self.assertEqual(self.health(run, target), 20000)
        self.assertFalse(run.match.combat.is_stunned(target))

    def test_active_bubble_and_target_search_storage_export_without_simulation(self):
        from unittest.mock import patch

        import test_server_snapshot as stored

        from ow174.game.script import runtime
        from ow174.game.server_snapshot import snapshot_digest

        case = stored.ServerSnapshotTests()
        session, _, players = case.scene(two=True)
        caster = players[0]
        session.apply("switch_hero", {"account": caster.account_lo, "hero": ZARYA})
        fight = caster.match.combat
        for offset in range(50):
            frame = caster.body_script.frame + 1
            caster.match.tick = fight.tick = frame
            fight.now = frame * combat.TICK
            caster.body_script.command(frame, 8 if offset == 35 else 0)
            fight.zarya_bubbles.sync(caster)
        bubble = fight.zarya_bubbles.for_target(caster)
        self.assertIsNotNone(bubble)
        recipient = players[1]
        caster.match.game_map = replace(caster.match.game_map, free_for_all=False)
        recipient.team = caster.team
        y = caster.mover.position[1]
        caster.mover.position[:] = (0, y, 0)
        recipient.mover.position[:] = (0, y, 4)
        caster.mover.body = recipient.mover.body = None
        caster.mover.yaw, caster.mover.pitch = aim(fight._eye(caster), fight._eye(recipient))
        frame = caster.body_script.frame + 1
        caster.match.tick = fight.tick = frame
        caster.body_script.command(frame, 16)
        self.assertIsNotNone(fight.zarya_bubbles.for_target(recipient))
        with (
            patch.object(runtime.Var, "value", side_effect=AssertionError("no evaluation")),
            patch.object(zarya_barriers.Barriers, "sync", side_effect=AssertionError("no synchronization")),
        ):
            first = snapshot_digest(case.capture(session))
            self.assertEqual(snapshot_digest(case.capture(session)), first)
            bubble.health -= 1
            self.assertNotEqual(snapshot_digest(case.capture(session)), first)
            bubble.health += 1
            fight.zarya_bubbles.next_entity += 1
            self.assertNotEqual(snapshot_digest(case.capture(session)), first)
            fight.zarya_bubbles.next_entity -= 1
            search = next(s for i in caster.body_script.component.instances.values()
                          for s in i.states.values() if isinstance(s, nodes.TrackTargets))
            original = search.targets
            search.targets = (expr.Entity(0xA0000321),)
            self.assertNotEqual(snapshot_digest(case.capture(session)), first)
            search.targets = original

    @unittest.skipUnless(os.environ.get("ORPHEUS_NPC_GRAPHS"), "actual NPC graph bundle required")
    def test_projected_shield_protects_all_five_actual_npc_bodies(self):
        import test_game_roadhog_npcs as actual

        from ow174.game import npc

        actual.RoadhogFiveNpcTests.setUpClass()
        try:
            for definition in actual.RoadhogFiveNpcTests.definitions.values():
                for button, distance in ((1, 6), (2, 6), (2, 15), (8, 6), (0x800, 2.5), (32, 6)):
                    with self.subTest(npc=definition.name, button=button, distance=distance):
                        run, hog, caster, rear, _ = self.session(projected=True, distance=distance)
                        for player in run.players:
                            player.client = actual.NpcClient(player)
                        rear.team = hog.team
                        rear.mover.position[0] = 20
                        rear.mover.body = None
                        actor = npc.spawn(run.match, definition,
                                          (0, hog.mover.position[1], distance), team=caster.team)
                        target = next(t for t in run.match.combat._targets() if t.owner is actor)
                        own = run.match.combat.vitals_of(target)
                        before = own.total
                        point = (0, actor.position[1] + target.height * .5, distance)
                        angles = aim(run.match.combat._eye(caster), point)
                        self.step(run, hog, caster, cast_aim=angles)
                        bubble = self.cast(run, hog, caster, actor, angles, projected=True)
                        look = aim(run.match.combat._eye(hog), point)
                        if button == 32:
                            hog.body_script.set_ult_charge(2240)
                        self.step(run, hog, caster, fire=button, attack_aim=look)
                        admitted = False
                        for _ in range(70 if button == 32 else 35):
                            self.step(run, hog, caster, attack_aim=look)
                            self.assertFalse(run.match.combat.hooks.controlled(actor.entity))
                            if 0 < bubble.health < 200:
                                admitted = True
                                self.assertEqual(own.total, before)
                        self.assertTrue(admitted)
                        self.assertLess(bubble.health, 200)
                        if button == 32:
                            self.assertEqual(bubble.health, 0)
                            self.assertLess(own.total, before)  # later pellets after break reach the body
                        else:
                            self.assertEqual(own.total, before)
                            self.assertFalse(actor.dead)
                        self.assertEqual(hog.body_script.component.entity_var(342).value(), 0)
                        npc.remove_spawned(run.match)
                        self.assertIsNone(run.match.combat.zarya_bubbles.for_target(actor))
        finally:
            actual.RoadhogFiveNpcTests.tearDownClass()

    @unittest.skipUnless(os.environ.get("ORPHEUS_NPC_GRAPHS"), "actual NPC graph bundle required")
    def test_projected_bubble_exact_break_and_overkill_do_not_spill_on_any_npc(self):
        import test_game_roadhog_npcs as actual

        actual.RoadhogFiveNpcTests.setUpClass()
        self.addCleanup(actual.RoadhogFiveNpcTests.tearDownClass)
        attacks = (("slug", 2, 4, 50), ("melee", 0x800, 1.7, 30), ("hook", 8, 6, 30))
        for definition in actual.RoadhogFiveNpcTests.definitions.values():
            for attack, button, distance, damage in attacks:
                for remaining in (damage, damage - 1):
                    with self.subTest(npc=definition.name, attack=attack, remaining=remaining):
                        self.check_projected_npc_break(definition, button, distance, remaining)

    def check_projected_npc_break(self, definition, button, distance, remaining):
        from test_game_roadhog_npcs import NpcClient

        from ow174.game import npc

        run, hog, caster, rear, _ = self.session(projected=True, distance=distance)
        fight = run.match.combat
        for player in run.players:
            player.client = NpcClient(player)
        rear.team = hog.team
        rear.mover.position[0] = 20
        rear.mover.body = None
        actor = npc.spawn(run.match, definition, (0, hog.mover.position[1], distance), team=caster.team)
        target = next(t for t in fight._targets() if t.owner is actor)
        pool = fight.vitals_of(target)
        before = pool.total
        position = actor.position
        point = (0, position[1] + target.height * .5, distance)
        angles = aim(fight._eye(caster), point)
        self.step(run, hog, caster, cast_aim=angles)
        bubble = self.cast(run, hog, caster, actor, angles, projected=True)
        bubble.health = remaining
        look = aim(fight._eye(hog), point)
        charge = hog.body_script.component.entity_var(342)
        energy = caster.body_script.component.entity_var(87)
        weapon = next(i for i in hog.body_script.component.instances.values() if i.graph.index == 0x01C6)
        ammo = weapon.vars[53].value()

        self.step(run, hog, caster, fire=button, attack_aim=look)
        for _ in range(45):
            if bubble.health <= 0:
                break
            self.step(run, hog, caster, attack_aim=look)
            self.assertFalse(fight.hooks.controlled(actor.entity))
        self.assertEqual(bubble.health, 0)
        self.assertFalse(bubble.phase.active)
        self.assertIsNone(fight.zarya_bubbles.for_target(actor))
        self.assertEqual(pool.total, before)
        self.assertFalse(hog.client.messages(combat.HIT))
        self.assertEqual(charge.value(), 0)
        self.assertAlmostEqual(energy.value(), remaining * .2, places=3)
        self.assertEqual(weapon.vars[53].value(), ammo - (button == 2))
        self.assertFalse(fight.hooks.controlled(actor.entity))
        if button == 8:
            hook = next(i for i in hog.body_script.component.instances.values() if i.graph.index == 0x01C8)
            self.assertGreater(hook.state(hook.graph.node(11).state).remaining(), 0)
            self.assertNotIn(hog.body, fight.hooks.flights)

        for _ in range(65):
            self.step(run, hog, caster, attack_aim=look)
        self.assertEqual(actor.position, position)
        self.assertEqual(pool.total, before)
        self.assertFalse(hog.client.messages(combat.HIT))
        self.step(run, hog, caster, fire=1, attack_aim=look)
        for _ in range(30):
            self.step(run, hog, caster, attack_aim=look)
            if pool.total < before:
                break
        self.assertLess(pool.total, before)
        self.assertAlmostEqual(charge.value(), before - pool.total, places=3)

    @unittest.skipUnless(os.environ.get("ORPHEUS_NPC_GRAPHS"), "actual NPC graph bundle required")
    def test_projected_barrier_breaks_without_spilling_manual_npc_melee(self):
        import test_game_roadhog_npcs as actual

        case = actual.RoadhogFiveNpcTests()
        case.setUpClass()
        self.addCleanup(case.tearDownClass)
        definitions = {d.name: d for d in case.definitions.values()}
        for name, damage in (("Trooper", 30), ("Heavy Assault", 80)):
            for remaining in (damage, damage - 1):
                with self.subTest(npc=name, remaining=remaining):
                    self.check_manual_melee_break(case, definitions[name], damage, remaining)

    @unittest.skipUnless(os.environ.get("ORPHEUS_NPC_GRAPHS"), "actual NPC graph bundle required")
    def test_npc_melee_breaks_projected_barrier_before_breather_mitigates_a_fresh_hit(self):
        import test_game_roadhog_npcs as actual

        case = actual.RoadhogFiveNpcTests()
        case.setUpClass()
        self.addCleanup(case.tearDownClass)
        definitions = {definition.name: definition for definition in case.definitions.values()}
        for name, damage in (("Trooper", 30), ("Heavy Assault", 80)):
            with self.subTest(npc=name):
                self.check_manual_melee_break(case, definitions[name], damage, damage - 1, breather=True)

    def check_manual_melee_break(self, case, definition, damage, remaining, *, breather=False):
        from ow174.game import npc_control, stats

        run, hog, bot, _, look = case.session(definition, distance=1.55, caster=True)
        caster = run.players[1]
        match = run.match
        fight = match.combat
        match.game_map = replace(match.game_map, free_for_all=False)
        hog.team = caster.team = 0
        self.assertTrue(match.switch_hero(caster, heroes()[ZARYA]))
        x, y, z = hog.mover.position
        caster.mover.position[:] = (x + 4, y, z)
        caster.mover.body = None
        cast_aim = aim(fight._eye(caster), fight._eye(hog))
        for _ in range(35):
            self.step(run, hog, caster, cast_aim=cast_aim, attack_aim=look)
        bubble = self.cast(run, hog, caster, hog, cast_aim, projected=True)
        bubble.health = remaining
        run.step({hog: (*look, 16 if breather else 0), caster: (*cast_aim, 0)})
        if breather:
            self.assertEqual(fight.incoming_damage_scale(hog), .5)
        self.assertEqual(npc_control.start(match, hog, action="melee"), bot.entity)
        for _ in range(45):
            self.step(run, hog, caster, attack_aim=look)
            if bubble.health <= 0:
                break
        self.assertEqual(bubble.health, 0)
        self.assertIsNone(fight.zarya_bubbles.for_target(hog))
        self.assertAlmostEqual(caster.body_script.component.entity_var(87).value(), remaining * .2, places=5)
        self.assertEqual(self.health(run, hog), 600)
        self.assertEqual(tuple(hog.mover.position), (x, y, z))
        self.assertEqual(hog.mover.velocity, (0, 0, 0))

        if breather:
            self.assertEqual(fight.incoming_damage_scale(hog), .5)
        npc_control.stop(match)
        for _ in range(1 if breather else 90):
            self.step(run, hog, caster, cast_aim=cast_aim, attack_aim=look)
        self.assertEqual(self.health(run, hog), 600)

        healing_before = hog.stats.value(stats.HEALING)
        self.assertEqual(npc_control.start(match, hog, action="melee"), bot.entity)
        for _ in range(100 if breather else 55):
            self.step(run, hog, caster, attack_aim=look)
            if self.health(run, hog) < 600:
                break
        body_damage = damage * .5 if breather else damage
        healing = hog.stats.value(stats.HEALING) - healing_before
        self.assertAlmostEqual(healing, 4.8 if breather else 0, places=5)
        self.assertAlmostEqual(self.health(run, hog), 600 - body_damage + healing, places=5)
        if definition.name == "Heavy Assault":
            self.assertGreater(hog.mover.velocity[1], 0)
        npc_control.stop(match)
        if breather:
            self.assertEqual(fight.incoming_damage_scale(hog), .5)
            for _ in range(20):
                self.step(run, hog, caster, attack_aim=look)
            self.assertEqual(self.health(run, hog), 600)
            self.assertAlmostEqual(hog.stats.value(stats.HEALING) - healing_before, body_damage, places=5)

    @unittest.skipUnless(os.environ.get("ORPHEUS_NPC_GRAPHS"), "actual NPC graph bundle required")
    def test_actual_four_manual_npc_guns_hit_projected_roadhog_bubble_and_stop(self):
        import test_game_roadhog_npcs as actual

        from ow174.game import npc_control

        actual.RoadhogFiveNpcTests.setUpClass()
        case = actual.RoadhogFiveNpcTests()
        try:
            for definition in actual.RoadhogFiveNpcTests.definitions.values():
                if definition.name == "Assassin":
                    continue
                with self.subTest(npc=definition.name):
                    run, hog, actor, _, step = self.manual_gun_barrier_scene(
                        case, definition, actual.NpcClient,
                    )
                    step(cast=16)
                    bubble = run.match.combat.zarya_bubbles.for_target(hog)
                    self.assertIsNotNone(bubble)
                    if definition.name != "Sniper":
                        self.assertEqual(npc_control.start(run.match, hog), actor.entity)
                    for _ in range(120):
                        step()
                        if bubble.health < 200:
                            break
                    self.assertLess(bubble.health, 200)
                    self.assertEqual(self.health(run, hog), 600)
                    npc_control.stop(run.match)
                    before = bubble.health
                    for _ in range(100):
                        step()
                    self.assertEqual(bubble.health, before)
                    self.assertEqual(self.health(run, hog), 600)
                    self.assertFalse(actor.dead)
        finally:
            actual.RoadhogFiveNpcTests.tearDownClass()

    @unittest.skipUnless(os.environ.get("ORPHEUS_NPC_GRAPHS"), "actual NPC graph bundle required")
    def test_breather_mitigates_four_manual_guns_only_after_barrier_admission(self):
        import test_game_roadhog_npcs as actual

        case = actual.RoadhogFiveNpcTests()
        case.setUpClass()
        self.addCleanup(case.tearDownClass)
        for definition in case.definitions.values():
            if definition.name == "Assassin":
                continue
            with self.subTest(npc=definition.name):
                self.check_manual_gun_breather_contact(case, definition, actual.NpcClient)
                raw = self.check_manual_gun_breather_contact(case, definition, actual.NpcClient, remaining=200)
                self.check_manual_gun_breather_contact(
                    case, definition, actual.NpcClient, remaining=raw * .75, first_barrier_amount=raw,
                )

    def manual_gun_barrier_scene(self, case, definition, client):
        from ow174.game import npc_control

        run, hog, shooter, _, angles = case.session(definition, distance=10, caster=True)
        match = run.match
        fight = match.combat
        for player in run.players:
            player.client = client(player)
        caster = run.players[1]
        self.assertTrue(match.switch_hero(caster, heroes()[ZARYA]))
        x, y, z = hog.mover.position
        caster.mover.position[:] = (x + 4, y, z)
        caster.mover.body = None
        match.game_map = replace(match.game_map, free_for_all=False)
        hog.team = caster.team = 0
        cast_aim = aim(fight._eye(caster), fight._eye(hog))

        def step(breathe=0, cast=0):
            run.step({hog: (*angles, breathe), caster: (*cast_aim, cast)})

        for _ in range(35):
            step()
        if definition.name == "Sniper":
            # Time the defenses to the real in-flight shot, after the long charge.
            self.assertEqual(npc_control.start(match, hog), shooter.entity)
            for _ in range(250):
                step()
                if any(rocket.shooter is shooter for rocket in fight.rockets):
                    break
            self.assertTrue(any(rocket.shooter is shooter for rocket in fight.rockets))
            self.assertEqual(self.health(run, hog), 600)

        return run, hog, shooter, caster, step

    def check_manual_gun_breather_contact(self, case, definition, client, *, remaining=None, first_barrier_amount=None):
        from ow174.game import npc_control, stats

        run, hog, shooter, caster, step = self.manual_gun_barrier_scene(case, definition, client)
        match = run.match
        fight = match.combat
        pool = fight.vitals_of(next(t for t in fight._targets() if t.owner is hog))

        step(breathe=16, cast=16 if remaining is not None else 0)
        self.assertEqual(fight.incoming_damage_scale(hog), .5)
        bubble = fight.zarya_bubbles.for_target(hog)
        if remaining is not None:
            self.assertIsNotNone(bubble)
            self.assertIs(bubble.caster, caster)
            self.assertEqual(bubble.health, 200)
            bubble.health = remaining
        if definition.name != "Sniper":
            self.assertEqual(npc_control.start(match, hog), shooter.entity)

        impacts = []
        apply_body = fight._damage
        apply_barrier = fight.zarya_bubbles.damage

        def observe_body(source, target, amount, *args, **kwargs):
            before = pool.total
            result = apply_body(source, target, amount, *args, **kwargs)
            if source is shooter and target.owner is hog and pool.total < before:
                impacts.append(("body", amount, before - pool.total, fight.incoming_damage_scale(hog)))
            return result

        def observe_barrier(target, amount, source):
            before = pool.total
            loss = apply_barrier(target, amount, source)
            if source is shooter and target is bubble and loss:
                self.assertEqual(pool.total, before)
                impacts.append(("barrier", amount, loss, fight.incoming_damage_scale(hog)))
            return loss

        healing_before = hog.stats.value(stats.HEALING)
        with (patch.object(fight, "_damage", side_effect=observe_body),
              patch.object(fight.zarya_bubbles, "damage", side_effect=observe_barrier),
              patch.object(fight, "_shoot", wraps=fight._shoot) as shots):
            for _ in range(110):
                step()
                if impacts:
                    break
            if definition.name == "Enforcer":
                # Let the rest of the already released volley finish travelling.
                for _ in range(12):
                    step()
                self.assertEqual(sum(call.args[0] is shooter for call in shots.call_args_list), 1)
        npc_control.stop(match)
        self.assertTrue(impacts)
        barrier_loss = 0
        body_loss = 0
        for kind, raw, loss, scale in impacts:
            self.assertEqual(scale, .5)
            if kind == "barrier":
                self.assertEqual(body_loss, 0, impacts)
                self.assertAlmostEqual(loss, min(remaining - barrier_loss, raw), places=5)
                barrier_loss += loss
            else:
                self.assertAlmostEqual(loss, raw * .5, places=5)
                body_loss += loss

        first_kind, first_raw, _, _ = impacts[0]
        if remaining is None:
            self.assertEqual(first_kind, "body")
            self.assertGreater(body_loss, 0)
        else:
            self.assertEqual(first_kind, "barrier")
            self.assertAlmostEqual(bubble.health, remaining - barrier_loss, places=5)
            self.assertAlmostEqual(caster.body_script.component.entity_var(87).value(),
                                   barrier_loss * .2, places=5)
            if first_barrier_amount is None:
                self.assertGreater(bubble.health, 0)
                self.assertIs(fight.zarya_bubbles.for_target(hog), bubble)
                self.assertEqual(body_loss, 0)
            else:
                self.assertAlmostEqual(first_raw, first_barrier_amount, places=5)
                self.assertEqual(bubble.health, 0)
                self.assertIsNone(fight.zarya_bubbles.for_target(hog))
                if definition.name == "Enforcer":
                    self.assertGreater(body_loss, 0)  # later pellets in the same volley
                else:
                    self.assertEqual(body_loss, 0)

        for _ in range(85):
            step()
        self.assertAlmostEqual(pool.total, 600, places=5)
        self.assertAlmostEqual(hog.stats.value(stats.HEALING) - healing_before, body_loss, places=5)
        return first_raw

    @unittest.skipUnless(os.environ.get("ORPHEUS_NPC_GRAPHS"), "actual NPC graph bundle required")
    def test_projected_roadhog_bubble_rejects_actual_assassin_contact_and_stale_attempt(self):
        self.assassin_case(already_pinned=False)

    @unittest.skipUnless(os.environ.get("ORPHEUS_NPC_GRAPHS"), "actual NPC graph bundle required")
    def test_actual_projected_cast_during_assassin_pin_releases_and_stops_future_damage(self):
        self.assassin_case(already_pinned=True)

    def assassin_case(self, *, already_pinned):
        import test_game_roadhog_assassin as actual

        from ow174.game import npc_control

        actual.RoadhogAssassinTests.setUpClass()
        try:
            case = actual.RoadhogAssassinTests()
            run, hog, actor, _, angles, own = case.encounter(support=True)
            caster = run.players[1]
            run.match.switch_hero(caster, heroes()[ZARYA])
            caster.mover.position[:] = (hog.mover.position[0] + 4, hog.mover.position[1],
                                       hog.mover.position[2])
            caster.mover.body = None
            run.match.game_map = replace(run.match.game_map, free_for_all=False)
            caster.team = hog.team
            for _ in range(30):
                run.step({})
            if already_pinned:
                case.engage(run, hog, actor, angles)
                self.assertEqual(own.total, 575)
            look = aim(run.match.combat._eye(caster), run.match.combat._eye(hog))
            run.step({caster: (*look, 16)})
            bubble = run.match.combat.zarya_bubbles.for_target(hog)
            self.assertIsNotNone(bubble)
            if not already_pinned:
                hog.mover.yaw, hog.mover.pitch = angles
                npc_control.start(run.match, hog)
            before = own.total
            for _ in range(60):
                run.step({})
            self.assertEqual(own.total, before)
            self.assertNotIn(actor.entity, run.match.combat.pins)
            self.assertEqual(bubble.health, 200 if already_pinned else 175)
            self.assertIsNotNone(actor.body_script.component.world.rejected_pin_attempt)
            for _ in range(130):
                run.step({})
            self.assertIsNone(run.match.combat.zarya_bubbles.for_target(hog))
            self.assertEqual(own.total, before)
            self.assertNotIn(actor.entity, run.match.combat.pins)
        finally:
            actual.RoadhogAssassinTests.tearDownClass()


if __name__ == "__main__":
    unittest.main()
