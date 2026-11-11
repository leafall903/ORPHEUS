"""Actual Zenyatta 0164 volleys against the hosted 104319 Tracer."""

import math
import unittest
from dataclasses import replace

import test_game_tracer as tracer_tests
import test_game_tracer_pulse_deflect as support_tests
import test_game_tracer_winston_barrier as bubble_tests
from test_game_combat import aim
from test_game_mover import box, wall

from ow174.game import combat, deflection, sigma_grasp, tracer, zenyatta_orbs
from ow174.game.collision import World
from ow174.game.commands import CROUCH, FIRE, SECONDARY, Command
from ow174.game.content import heroes
from ow174.game.script import expr
from ow174.game.script.driver import ULT_CHARGE, ULT_COST

ZENYATTA = 0x02E0000000000020


class TracerZenyattaTests(unittest.TestCase):
    def setUp(self):
        self.run, self.tracer, self.zen, _ = tracer_tests.TracerPistolTests().fight()
        self.match = self.run.match
        self.match.game_map = replace(self.match.game_map, free_for_all=False)
        self.tracer.team, self.zen.team = 0, 1
        self.assertTrue(self.match.switch_hero(self.zen, heroes()[ZENYATTA]))
        x, y, z = self.tracer.mover.position
        self.zen.mover.position[:] = (x, y, z - 8)
        self.zen.mover.body = None
        self.fight = self.match.combat
        self.health = self.fight.vitals_of(self.target(self.tracer))
        self.zen_health = self.fight.vitals_of(self.target(self.zen))
        instances = self.zen.body_script.component.instances.values()
        self.weapon = next(i for i in instances if i.graph.index == 0x0164)
        self.step()

    def target(self, actor):
        return next(t for t in self.fight._targets() if t.owner is actor)

    def step(self, count=1, *, zen=0, tracer_buttons=0, head=False, fixed_yaw=None, support=None):
        for _ in range(count):
            target = self.target(self.tracer)
            point = combat._along(target.feet, target.head_offset, 1) if head else (
                target.feet[0], target.feet[1] + 0.8, target.feet[2],
            )
            aim_zen = aim(self.fight._eye(self.zen), point)
            x, y, z = self.zen.mover.position
            aim_tracer = aim(self.fight._eye(self.tracer), (x, y + 1, z))
            if fixed_yaw is not None:
                aim_tracer = (fixed_yaw, 0)
            self.run.step({**(support or {}), self.zen: (*aim_zen, zen),
                           self.tracer: (*aim_tracer, tracer_buttons)})

    def hits(self):
        return [(notice["+0x78"]["+0x4"], notice["+0x78"]["+0x8"])
                for notice in self.zen.client.messages(combat.HIT)]

    def begin_transcendence(self, *, earned=False):
        script = self.zen.body_script
        script.ult_seconds = 0
        meter = script.component.entity_var(ULT_CHARGE)
        cost = script.component.entity_var(ULT_COST).value()
        if not earned:
            script.set_ult_charge(cost)
        self.assertEqual(meter.value(), cost)
        self.step(zen=32)
        self.assertEqual(meter.value(), 0)
        self.assertTrue(self.fight.zenyatta_orbs.protects(self.zen))
        return self.fight.zenyatta_orbs.transcendence[self.zen.body]

    def test_transcendence_protects_zen_from_tracer_pistols_melee_and_pulse_until_expiry(self):
        script = self.zen.body_script
        script.ult_seconds = 0
        self.step(zen=32)
        self.assertFalse(self.fight.zenyatta_orbs.protects(self.zen))
        self.zen_health.damage(100)
        x, y, z = self.tracer.mover.position
        self.zen.mover.position[:] = (x, y, z - 2.4)
        self.zen.mover.body = None
        self.step()
        effect = self.begin_transcendence()
        self.assertEqual(effect.last - effect.start, 374)
        self.assertEqual(self.zen_health.total, 200)
        self.step(zen=FIRE, tracer_buttons=FIRE)
        self.assertFalse(self.fight.rockets)
        self.step(tracer_buttons=0x800)
        self.assertEqual(self.zen_health.total, 200)
        self.assertFalse(self.tracer.client.messages(combat.HIT))
        self.step(zen=32)
        self.assertIs(self.fight.zenyatta_orbs.transcendence[self.zen.body], effect)

        script = self.tracer.body_script
        script.set_ult_charge(script.component.entity_var(ULT_COST).value())
        self.step(tracer_buttons=32, fixed_yaw=-32768)
        for _ in range(55):
            self.step()
            if self.fight.pulse_bombs.bombs and self.fight.pulse_bombs.bombs[0].stuck:
                break
        bomb, = self.fight.pulse_bombs.bombs
        self.assertEqual(bomb.attached, self.zen.body)
        self.step(tracer_buttons=8, fixed_yaw=0)
        self.step(70)
        self.assertFalse(self.fight.pulse_bombs.bombs)
        self.assertEqual(self.zen_health.total, 200)
        self.assertEqual(self.health.total, 150)
        self.assertFalse(self.tracer.client.messages(combat.HIT))

        zen_frame = self.zen.body_script.frame
        yaw, pitch = aim(self.fight._eye(self.tracer), (x, y + 1, z - 2.4))
        for frame in range(self.run.frame + 1, effect.last + 2):
            self.match.update(frame * combat.TICK, frame)
            self.tracer.client.queue_entities(self.match.remote_movements(self.tracer))
            buttons = FIRE if frame == effect.last + 1 else 0
            self.tracer.take_commands([Command(frame, yaw=yaw, pitch=pitch, buttons=buttons)])
        self.run.frame = effect.last + 1
        self.assertEqual(self.zen.body_script.frame, zen_frame)
        self.assertFalse(self.fight.zenyatta_orbs.protects(self.zen))
        self.assertEqual(self.zen_health.total, 188)

    def test_transcendence_expiry_orders_pulse_blasts_before_retained_frames(self):
        blast_frames = {}
        for early, in_flight in ((True, False), (False, False), (True, True), (False, True)):
            with self.subTest(early=early, in_flight=in_flight):
                self.setUp()
                x, y, z = self.tracer.mover.position
                self.zen.mover.position[:] = (x, y, z - 3.5)
                self.zen.mover.body = None
                effect = self.begin_transcendence()
                self.step(275 if early else 315)
                script = self.tracer.body_script
                script.set_ult_charge(script.component.entity_var(ULT_COST).value())
                self.step(tracer_buttons=32, fixed_yaw=-32768)
                for _ in range(55):
                    self.step(fixed_yaw=-32768)
                    if self.fight.pulse_bombs.bombs and (in_flight or self.fight.pulse_bombs.bombs[0].stuck):
                        break
                bomb, = self.fight.pulse_bombs.bombs
                self.assertEqual(bomb.attached, None if in_flight else self.zen.body)
                self.assertEqual(self.zen_health.total, 200)
                self.step(tracer_buttons=8, fixed_yaw=0)
                self.assertTrue(self.fight.zenyatta_orbs.protects(self.zen))

                if in_flight:
                    self.assertFalse(bomb.stuck)
                else:
                    blast_frames[early] = self.run.frame + math.ceil(bomb.fuse_left / combat.TICK)
                blast = blast_frames[early]
                end = effect.last + combat.COMBAT_CATCH_UP + 50
                self.assertEqual(blast <= effect.last, early)
                self.assertLess(blast, end - combat.COMBAT_CATCH_UP + 1)
                self.match.update(end * combat.TICK, end)

                self.assertFalse(self.fight.pulse_bombs.bombs)
                self.assertEqual(bomb.attached, self.zen.body)
                self.assertFalse(self.fight.zenyatta_orbs.protects(self.zen))
                self.assertEqual(self.health.total, 150)
                self.assertEqual(self.zen_health.total, 200 if early else 0)
                self.assertEqual(len(self.tracer.client.messages(combat.HIT)), 0 if early else 1)
                if not early:
                    self.assertEqual(self.zen_health.death_tick, blast)
                    self.assertAlmostEqual(self.zen_health.died_at, blast * combat.TICK)

    def test_transcendence_is_earned_with_an_orb_hit_and_harmony_healing(self):
        script = self.zen.body_script
        script.ult_seconds = 0
        meter = script.component.entity_var(ULT_CHARGE)
        cost = script.component.entity_var(ULT_COST).value()
        script.set_ult_charge(cost - 50)
        self.step(zen=FIRE)
        self.assertEqual(meter.value(), cost - 50)
        self.step(18)
        self.assertEqual(self.health.total, 102)
        self.assertEqual(meter.value(), cost - 2)
        self.tracer.team = self.zen.team
        self.step(zen=16)
        self.step(5)
        self.assertEqual(meter.value(), cost)
        self.begin_transcendence(earned=True)
        before = self.health.total
        self.step(6)
        self.assertGreater(self.health.total, before)
        self.assertEqual(meter.value(), 0)

    def test_transcendence_heals_allied_tracer_and_respects_cover_and_blink_range(self):
        enemy, _ = support_tests.PulseDeflectTests().support(self.run, self.tracer, 0x02E000000000006E)
        enemy.team = self.tracer.team
        self.tracer.team = self.zen.team
        x, y, z = self.tracer.mover.position
        enemy.mover.position[:] = (x - 3, y, z)
        enemy.mover.body = None
        self.step(20)
        enemy_health = self.fight.vitals_of(self.target(enemy))
        enemy_health.damage(100)
        self.health.damage(100)
        effect = self.begin_transcendence()
        self.step(10)
        self.assertAlmostEqual(self.health.total, 98)
        self.assertEqual(enemy_health.total, 100)

        clear = self.match.collision_world
        floor = box(x - 12, x + 12, y - 1, y, z - 15, z + 12)
        self.match.collision_world = World(floor + wall(x - 5, z - 4, x + 5, z - 4, y, y + 5))
        self.step(10)
        self.assertAlmostEqual(self.health.total, 98)
        self.match.collision_world = clear
        self.step(tracer_buttons=8, fixed_yaw=0)
        outside = self.health.total
        self.step(10)
        self.assertEqual(self.health.total, outside)
        self.step(tracer_buttons=8, fixed_yaw=-32768)
        self.step(5)
        self.assertAlmostEqual(self.health.total, outside + 24)
        self.step(effect.last - self.run.frame - 1)
        self.health.damage(100)
        self.step()
        self.assertAlmostEqual(self.health.total, 54.8)
        self.step()
        self.assertAlmostEqual(self.health.total, 54.8)

    def test_transcendence_blocks_actual_flashbang_damage_and_control(self):
        for active in (False, True):
            with self.subTest(active=active):
                self.setUp()
                support = support_tests.PulseDeflectTests()
                mccree, _ = support.support(self.run, self.tracer, 0x02E0000000000042)
                mccree.team = self.tracer.team
                x, y, z = self.zen.mover.position
                mccree.mover.position[:] = (x - 3, y, z)
                mccree.mover.body = None
                self.step(25)
                if active:
                    self.begin_transcendence()
                yaw, pitch = aim(self.fight._eye(mccree), (x, y + 1, z))
                self.step(support={mccree: (yaw, pitch, 16)})
                self.step(18)
                self.assertEqual(self.zen_health.total, 200 if active else 175)
                self.assertEqual(self.fight.is_stunned(self.zen), not active)

    def test_transcendence_across_long_gaps_respects_actual_antigrenade_and_expiry(self):
        for sparse in (False, True):
            with self.subTest(sparse=sparse):
                self.setUp()
                ana, facing = support_tests.PulseDeflectTests().support(
                    self.run, self.tracer, hero=0x02E000000000013B,
                )
                ana.team = self.tracer.team
                self.tracer.team = self.zen.team
                effect = self.begin_transcendence()
                self.step(96)
                self.health.damage(90)
                self.step(support={ana: (*facing, 16)})
                for _ in range(50):
                    if self.health.healing_block_until > self.fight.tick:
                        break
                    self.step()
                deadline = self.health.healing_block_until
                self.assertLess(self.fight.tick + combat.COMBAT_CATCH_UP, deadline)
                self.assertLess(deadline, effect.last)
                wounded = self.health.total
                self.assertLess(wounded, 50)
                expected = min(self.health.maximum, wounded + (effect.last - deadline + 1) * 4.8)
                zen_frame = self.zen.body_script.frame
                end = effect.last + combat.COMBAT_CATCH_UP + 2

                first = deadline - 1 if sparse else self.run.frame + 1
                for frame in range(first, deadline):
                    self.match.update(frame * combat.TICK, frame)
                self.assertEqual(self.health.total, wounded)

                first = end if sparse else deadline
                for frame in range(first, end + 1):
                    self.match.update(frame * combat.TICK, frame)
                self.assertAlmostEqual(self.health.total, expected, places=4)
                self.assertEqual(self.zen.body_script.frame, zen_frame)
                self.assertNotIn(self.zen.body, self.fight.zenyatta_orbs.transcendence)

                self.tracer.team = ana.team
                x, y, z = self.zen.mover.position
                yaw, pitch = aim(self.fight._eye(self.tracer), (x, y + 1, z))
                self.tracer.client.queue_entities(self.match.remote_movements(self.tracer))
                self.tracer.take_commands([Command(end, yaw=yaw, pitch=pitch, buttons=FIRE)])
                self.assertEqual(self.zen_health.total, 188)

    def test_transcendence_resumes_after_actual_winston_dome_expires_in_discarded_frames(self):
        winston, _ = support_tests.PulseDeflectTests().support(self.run, self.tracer, hero=0x02E0000000000009)
        winston.team = self.tracer.team
        self.tracer.team = self.zen.team
        bubble = bubble_tests.TracerWinstonBarrierTests().cast(self.run, winston)
        expiry = self.fight.tick
        while expiry * combat.TICK < bubble.expires:
            expiry += 1
        self.step(expiry - self.fight.tick - 355)
        self.health.damage(120)
        effect = self.begin_transcendence()
        self.step()
        self.assertEqual(self.health.total, 30)
        self.assertIn(winston.body, self.fight.winston_barriers.actors)
        self.assertEqual(effect.last - expiry, 20)

        end = effect.last + combat.COMBAT_CATCH_UP + 2
        self.match.update(end * combat.TICK, end)

        self.assertNotIn(winston.body, self.fight.winston_barriers.actors)
        self.assertNotIn(self.zen.body, self.fight.zenyatta_orbs.transcendence)
        self.assertAlmostEqual(self.health.total, 30 + 21 * 4.8, places=4)

    def test_transcendence_resumes_after_actual_ice_wall_expires_in_discarded_frames(self):
        mei, _ = support_tests.PulseDeflectTests().support(self.run, self.tracer, hero=0x02E00000000000DD)
        mei.team = self.tracer.team
        self.tracer.team = self.zen.team
        x, y, z = self.tracer.mover.position
        mei.mover.position[:] = (x, y, z - 2.5)
        mei.mover.body = None
        effect = self.begin_transcendence()
        self.step(30)
        yaw, pitch = aim(self.fight._eye(mei), (x, y, z - 4))
        self.step(support={mei: (yaw, pitch, 16)})
        self.assertTrue(self.fight.walls.inputs[mei.body].preview)
        self.step()
        self.step(support={mei: (yaw, pitch, FIRE)})
        self.step()
        pieces = self.fight.walls.pieces.values()
        self.assertEqual(len(pieces), 5)
        expires, = {piece.expires for piece in pieces}
        self.assertLess(expires, effect.last)
        self.health.damage(120)
        self.step()
        self.assertEqual(self.health.total, 30)

        end = effect.last + combat.COMBAT_CATCH_UP + 2
        self.match.update(end * combat.TICK, end)

        self.assertFalse(self.fight.walls.pieces)
        self.assertAlmostEqual(self.health.total, min(150, 30 + (effect.last - expires) * 4.8), places=4)

    def test_primary_orb_body_and_head_damage_then_tracer_returns_fire(self):
        for head, damage, flags in ((False, 48, 0), (True, 96, combat.CRITICAL)):
            with self.subTest(head=head):
                self.setUp()
                self.step(zen=FIRE, head=head)
                rocket, = self.fight.rockets
                self.assertEqual(rocket.definition, combat.ZENYATTA_ORBS[0])
                self.assertAlmostEqual(rocket.body_radius, 0.15)
                self.assertEqual(self.health.total, 150)
                self.step(18)
                self.assertEqual(self.health.total, 150 - damage)
                self.assertEqual(self.hits(), [(damage, flags)])
                self.assertEqual(self.weapon.vars[53].value(), 19)
                self.step(tracer_buttons=FIRE)
                self.assertEqual(self.zen_health.total, 188)

    def test_primary_orb_resolves_crouched_tracer_body_and_critical_height(self):
        for height, damage, flags in ((0.45, 48, 0), (0.875, 96, combat.CRITICAL), (1.567, 0, 0)):
            with self.subTest(height=height):
                self.setUp()
                self.step(8, tracer_buttons=CROUCH)
                self.assertTrue(self.tracer.mover.crouched)
                x, y, z = self.tracer.mover.position
                direction = aim(self.fight._eye(self.zen), (x, y + height, z))
                self.run.step({self.zen: (*direction, FIRE), self.tracer: (0, 0, CROUCH)})
                rocket, = self.fight.rockets
                self.assertEqual(rocket.definition, combat.ZENYATTA_ORBS[0])
                self.step(20, tracer_buttons=CROUCH)
                self.assertEqual(self.health.total, 150 - damage)
                self.assertEqual(self.hits(), [(damage, flags)] if damage else [])

    def test_secondary_releases_only_charged_and_available_rounds(self):
        for frames, ammo, expected, head in ((1, 20, 1, False), (130, 20, 5, False),
                                               (130, 1, 1, False), (130, 3, 3, True)):
            with self.subTest(frames=frames, ammo=ammo, head=head):
                self.setUp()
                health = self.fight.vitals[self.tracer.body] = combat.Vitals(self.tracer.body, 1000)
                self.weapon.write((expr.INSTANCE, 53), ammo)
                self.step(frames, zen=SECONDARY, head=head)
                self.assertEqual(health.total, 1000)
                self.assertFalse(self.fight.rockets)
                self.step(60, head=head)
                damage, flags = (96, combat.CRITICAL) if head else (48, 0)
                self.assertEqual(self.hits(), [(damage, flags)] * expected)
                self.assertEqual(health.total, 1000 - damage * expected)
                self.assertEqual(self.weapon.vars[53].value(), ammo - expected)

    def test_blink_evades_launched_orb_and_recall_excludes_target(self):
        self.step(200)
        self.step(zen=FIRE)
        self.assertTrue(self.fight.rockets)
        self.step(tracer_buttons=8, fixed_yaw=16384)
        self.step(24)
        self.assertEqual(self.health.total, 150)
        self.step(tracer_buttons=8, fixed_yaw=-16384)
        self.step(10)
        self.step(tracer_buttons=16)
        self.step(25)
        self.assertTrue(tracer.recalling(self.tracer))
        self.step(zen=FIRE)
        self.step(18)
        self.assertTrue(tracer.recalling(self.tracer))
        self.assertEqual(self.health.total, 150)
        self.step(100)
        self.assertFalse(tracer.recalling(self.tracer))
        self.step(zen=FIRE)
        self.step(18)
        self.assertEqual(self.health.total, 102)

    def test_orbs_respect_static_cover_team_and_range(self):
        for reject in ("wall", "team", "range"):
            with self.subTest(reject=reject):
                self.setUp()
                x, y, z = self.tracer.mover.position
                if reject == "wall":
                    floor = box(x - 12, x + 12, y - 1, y, z - 15, z + 12)
                    self.match.collision_world = World(floor + wall(x - 5, z - 4, x + 5, z - 4, y, y + 5))
                elif reject == "team":
                    self.zen.team = self.tracer.team
                else:
                    self.tracer.mover.position[2] += 180
                    self.tracer.mover.body = None
                self.step(zen=FIRE)
                self.step(185)
                self.assertEqual(self.health.total, 150)
                self.assertFalse(self.hits())
                self.assertFalse(self.fight.rockets)

    def test_actual_deflect_and_grasp_protect_tracer_from_both_orb_types(self):
        if not all(map(deflection.absorbable, combat.ZENYATTA_ORBS)):
            self.skipTest("requires extracted Zenyatta projectile tags")
        for hero, button, attack in ((0x02E0000000000029, 16, FIRE), (sigma_grasp.HERO, 8, SECONDARY)):
            with self.subTest(hero=hero, attack=attack):
                self.setUp()
                defender, _ = support_tests.PulseDeflectTests().support(self.run, self.tracer, hero)
                defender.team = self.tracer.team
                x, y, z = self.tracer.mover.position
                defender.mover.position[:] = (x, y, z - 3)
                defender.mover.body = None
                self.step(20)
                self.step(support={defender: (-32768, 0, button)})
                guard = {defender: (-32768, 0, 0)}
                self.step(8, support=guard)
                self.step(zen=attack, support=guard)
                if attack == SECONDARY:
                    self.step(support=guard)
                rocket, = self.fight.rockets
                self.assertEqual(rocket.definition, combat.ZENYATTA_ORBS[attack == SECONDARY])
                self.step(25, support=guard)
                self.assertFalse(self.fight.rockets)
                self.assertEqual(self.health.total, 150)
                self.assertFalse(self.hits())
                if hero == sigma_grasp.HERO:
                    grasp = next(i for i in defender.body_script.component.instances.values()
                                 if i.graph.index == sigma_grasp.GRASP)
                    self.assertEqual(grasp.vars[21312].value(), 48)
                    self.assertEqual(self.zen_health.total, 200)
                else:
                    self.assertIs(rocket.shooter, defender)
                    self.assertLess(self.zen_health.total, 200)

    def test_winston_dome_spends_one_hit_from_each_orb_type(self):
        winston, _ = support_tests.PulseDeflectTests().support(self.run, self.tracer, 0x02E0000000000009)
        winston.team = self.tracer.team
        x, y, z = self.tracer.mover.position
        winston.mover.position[:] = (x, y, z + 2)
        winston.mover.body = None
        self.step(20)
        self.step(support={winston: (0, 0, 16)})
        self.step(8)
        dome = self.fight.winston_barriers.actors[winston.body]
        self.assertEqual(dome.health, 700)
        for attack, expected in ((FIRE, 652), (SECONDARY, 604)):
            self.step(zen=attack)
            self.step(35)
            self.assertEqual(dome.health, expected)
            self.assertEqual(self.health.total, 150)
            self.assertFalse(self.hits())

    def test_lethal_tracer_pistols_cancel_unreleased_charge(self):
        self.step(90, zen=SECONDARY)
        self.assertGreater(self.weapon.vars[1392].value(), 0)
        self.assertLess(self.weapon.vars[53].value(), 20)
        self.assertFalse(self.fight.rockets)
        self.zen_health.damage(195)
        self.step(zen=SECONDARY, tracer_buttons=FIRE)
        self.assertTrue(self.zen_health.dead)
        self.assertEqual(self.zen_health.killer, self.tracer.body)
        self.step(75)
        self.assertFalse(self.fight.rockets)
        self.assertEqual(self.health.total, 150)

    def test_harmony_heals_tracer_through_short_cover_then_expires(self):
        self.tracer.team = self.zen.team
        self.health.damage(100)
        self.step(8)
        self.assertEqual(self.health.total, 50)
        self.step(zen=16)
        key = self.zen.body, zenyatta_orbs.HARMONY
        orbs = self.fight.zenyatta_orbs
        self.assertEqual(orbs.links[key].target, self.tracer.body)
        harmony = orbs._instance(self.zen, zenyatta_orbs.HARMONY)
        slot = harmony.vars[zenyatta_orbs.ORB_TARGET]
        self.assertEqual(slot.value(), expr.Entity(self.tracer.body))
        self.step(10)
        self.assertAlmostEqual(self.health.total, 50 + 30 * combat.TICK * 10)
        x, y, z = self.tracer.mover.position
        floor = box(x - 12, x + 12, y - 1, y, z - 12, z + 12)
        self.match.collision_world = World(floor + wall(x - 5, z - 4, x + 5, z - 4, y, y + 5))
        before = self.health.total
        self.step(30)
        self.assertAlmostEqual(self.health.total - before, 30 * combat.TICK * 30)
        link = orbs.links[key]
        self.step(link.last_visible + link.grace - self.run.frame - 1)
        self.assertIn(key, orbs.links)
        before = self.health.total
        self.step()
        self.assertNotIn(key, orbs.links)
        self.assertEqual(slot.value(), 0)
        self.step(20, zen=16)
        self.assertNotIn(key, orbs.links)
        self.assertEqual(self.health.total, before)

    def test_harmony_keeps_pre_expiry_healing_and_charge_across_long_occlusion(self):
        self.tracer.team = self.zen.team
        self.health.damage(100)
        self.step(zen=16)
        orbs = self.fight.zenyatta_orbs
        key = self.zen.body, zenyatta_orbs.HARMONY
        link = orbs.links[key]
        meter = self.zen.body_script.component.entity_var(ULT_CHARGE)
        self.zen.body_script.set_ult_charge(0)
        x, y, z = self.tracer.mover.position
        floor = box(x - 12, x + 12, y - 1, y, z - 12, z + 12)
        self.match.collision_world = World(floor + wall(x - 5, z - 4, x + 5, z - 4, y, y + 5))
        end = link.last_visible + link.grace + combat.COMBAT_CATCH_UP
        self.match.update(end * combat.TICK, end)
        expected = (link.grace - 1) * 0.48
        self.assertAlmostEqual(self.health.total, 50 + expected, places=4)
        self.assertAlmostEqual(meter.value(), expected, places=3)
        self.assertNotIn(key, orbs.links)
        harmony = orbs._instance(self.zen, zenyatta_orbs.HARMONY)
        self.assertEqual(harmony.vars[zenyatta_orbs.ORB_TARGET].value(), 0)

    def test_full_health_harmony_keeps_visibility_through_a_long_open_gap(self):
        self.tracer.team = self.zen.team
        self.step(zen=16)
        orbs = self.fight.zenyatta_orbs
        key = self.zen.body, zenyatta_orbs.HARMONY
        link = orbs.links[key]
        end = self.run.frame + 10 * link.grace
        self.match.update(end * combat.TICK, end)
        self.assertEqual(link.last_visible, end)
        self.assertEqual(self.health.total, 150)
        self.tracer.mover.position[2] += 45
        self.health.damage(100)
        last = end + link.grace - 1
        self.match.update(last * combat.TICK, last)
        self.assertIs(orbs.links[key], link)
        self.assertAlmostEqual(self.health.total, 50 + (link.grace - 1) * 0.48, places=4)
        self.match.update((last + 1) * combat.TICK, last + 1)
        self.assertNotIn(key, orbs.links)

    def test_harmony_resumes_at_actual_antigrenade_expiry_inside_a_long_gap(self):
        ana, facing = support_tests.PulseDeflectTests().support(
            self.run, self.tracer, hero=0x02E000000000013B,
        )
        ana.team = self.tracer.team
        self.tracer.team = self.zen.team
        self.step(support={ana: (*facing, 16)})
        for _ in range(50):
            if self.health.healing_block_until > self.fight.tick:
                break
            self.step()
        deadline = self.health.healing_block_until
        self.assertEqual(self.health.total, 100)
        self.step(zen=16)
        key = self.zen.body, zenyatta_orbs.HARMONY
        self.assertEqual(self.fight.zenyatta_orbs.links[key].target, self.tracer.body)
        self.zen.body_script.set_ult_charge(0)
        meter = self.zen.body_script.component.entity_var(ULT_CHARGE)
        end = deadline + combat.COMBAT_CATCH_UP + 1
        self.match.update(end * combat.TICK, end)
        expected = (end - deadline + 1) * 0.48
        self.assertAlmostEqual(self.health.total, 100 + expected, places=4)
        self.assertAlmostEqual(meter.value(), expected, places=3)
        self.assertEqual(self.fight.zenyatta_orbs.links[key].last_visible, end)

    def test_harmony_rejects_enemy_long_range_and_off_axis_presses(self):
        orbs = self.fight.zenyatta_orbs
        key = self.zen.body, zenyatta_orbs.HARMONY
        self.step(zen=16)
        self.assertNotIn(key, orbs.links)
        self.tracer.team = self.zen.team
        origin = tuple(self.tracer.mover.position)
        self.tracer.mover.position[2] += 40
        self.tracer.mover.body = None
        self.step(3)
        self.step(zen=16)
        self.assertNotIn(key, orbs.links)
        self.tracer.mover.position[:] = origin
        self.tracer.mover.body = None
        self.step()
        self.run.step({self.zen: (16384, 0, 16)})
        self.assertNotIn(key, orbs.links)
        self.step()
        self.step(zen=16)
        self.assertEqual(orbs.links[key].target, self.tracer.body)

    def test_discord_amplifies_actual_orb_damage_then_recall_cleanses_and_allows_recast(self):
        self.step(200)
        key = self.zen.body, zenyatta_orbs.DISCORD
        orbs = self.fight.zenyatta_orbs
        self.step(zen=8)
        self.assertEqual(orbs.links[key].target, self.tracer.body)
        self.step(zen=FIRE)
        self.step(18)
        self.assertEqual(self.health.total, 90)
        self.step(tracer_buttons=16)
        self.step(30)
        self.assertTrue(tracer.recalling(self.tracer))
        self.assertNotIn(key, orbs.links)
        self.step(zen=8)
        self.assertNotIn(key, orbs.links)
        self.step(90)
        self.assertEqual(self.health.total, 150)
        self.step(zen=FIRE)
        self.step(18)
        self.assertEqual(self.health.total, 102)
        self.step(zen=8)
        self.assertEqual(orbs.links[key].target, self.tracer.body)
        self.step(zen=FIRE)
        self.step(18)
        self.assertEqual(self.health.total, 42)

    def test_discord_expiry_orders_actual_pulse_blasts_during_sparse_updates(self):
        cases = ((True, False, False), (False, False, False), (True, True, False),
                 (False, True, False), (False, True, True))
        for early, discarded, in_flight in cases:
            with self.subTest(early=early, discarded=discarded, in_flight=in_flight):
                self.setUp()
                enemy, _ = support_tests.PulseDeflectTests().support(
                    self.run, self.tracer, hero=0x02E0000000000040,
                )
                enemy.team = self.zen.team
                self.zen.team = self.tracer.team
                x, y, z = enemy.mover.position
                self.zen.mover.position[:] = (x - 8, y, z)
                self.zen.mover.body = None
                yaw, pitch = aim(self.fight._eye(self.zen), (x, y + self.target(enemy).eye, z))
                self.run.step({self.zen: (yaw, pitch, 8)})
                orbs = self.fight.zenyatta_orbs
                key = self.zen.body, zenyatta_orbs.DISCORD
                link = orbs.links[key]
                self.assertEqual(link.target, enemy.body)
                deadline = link.last_visible + link.grace
                self.zen.mover.position[0] -= 50
                self.zen.mover.body = None
                self.step(95 if early else 125)

                script = self.tracer.body_script
                script.set_ult_charge(script.component.entity_var(ULT_COST).value())
                self.step(tracer_buttons=32, fixed_yaw=0)
                for _ in range(45):
                    self.step(fixed_yaw=0)
                    if self.fight.pulse_bombs.bombs and (in_flight or self.fight.pulse_bombs.bombs[0].stuck):
                        break
                bomb, = self.fight.pulse_bombs.bombs
                health = self.fight.vitals_of(self.target(enemy))
                self.assertEqual(bomb.attached, None if in_flight else enemy.body)
                self.assertEqual(health.total, health.maximum - (0 if in_flight else 6.25))
                self.step(tracer_buttons=8, fixed_yaw=-32768)

                end = deadline + 24 + (combat.COMBAT_CATCH_UP if discarded else 0)
                if in_flight:
                    self.assertFalse(bomb.stuck)
                else:
                    blast = self.run.frame + math.ceil(bomb.fuse_left / combat.TICK)
                    self.assertEqual(blast < deadline, early)
                    self.assertEqual(blast < end - combat.COMBAT_CATCH_UP + 1, discarded)
                    self.assertLessEqual(blast, end)
                self.match.update(end * combat.TICK, end)

                self.assertNotIn(key, orbs.links)
                self.assertFalse(self.fight.pulse_bombs.bombs)
                self.assertEqual(bomb.attached, enemy.body)
                self.assertEqual(health.total, health.maximum - 6.25 - (437.5 if early else 350))

    def test_discord_cooldown_rejects_retarget_until_a_fresh_press(self):
        orbs = self.fight.zenyatta_orbs
        self.step(zen=8)
        key = self.zen.body, zenyatta_orbs.DISCORD
        original = orbs.links[key]
        self.step()
        self.step(zen=8)
        self.assertIs(orbs.links[key], original)
        self.step(55, zen=8)
        self.assertIs(orbs.links[key], original)
        self.step()
        self.step(zen=8)
        self.assertIsNot(orbs.links[key], original)
        self.zen_health.damage(195)
        self.step(tracer_buttons=FIRE)
        self.assertTrue(self.zen_health.dead)
        self.assertNotIn(key, orbs.links)

    def test_discord_blocks_acquisition_through_enemy_dome_and_amplifies_allied_rifle(self):
        support = support_tests.PulseDeflectTests()
        winston, _ = support.support(self.run, self.tracer, 0x02E0000000000009)
        winston.team = self.tracer.team
        x, y, z = self.tracer.mover.position
        winston.mover.position[:] = (x, y, z + 2)
        winston.mover.body = None
        self.step(20)
        self.step(support={winston: (0, 0, 16)})
        self.step(8)
        dome = self.fight.winston_barriers.actors[winston.body]
        self.step(zen=8)
        orbs = self.fight.zenyatta_orbs
        key = self.zen.body, zenyatta_orbs.DISCORD
        self.assertNotIn(key, orbs.links)
        self.assertEqual(dome.health, 700)

        # Use the same third player for the other side after the dome retires.
        self.assertTrue(self.match.switch_hero(winston, heroes()[0x02E000000000006E]))
        winston.team = self.zen.team
        winston.mover.position[:] = (x - 3, y, z - 6)
        winston.mover.body = None
        self.step(20)
        self.step(zen=8)
        self.assertEqual(orbs.links[key].target, self.tracer.body)
        yaw, pitch = aim(self.fight._eye(winston), (x, y + 1, z))
        self.step(support={winston: (yaw, pitch, FIRE)})
        self.assertEqual(self.health.total, 125)
