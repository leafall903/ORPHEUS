"""Actual Sigma Shift and Zen inputs, with explicit reconstructed grasp hosting."""

import struct
import unittest
from dataclasses import replace
from itertools import product

import test_zenyatta_combat as weapon_tests
from test_game_combat import aim, in_world

from ow174.game import combat
from ow174.game.collision import World
from ow174.game.commands import FIRE, SECONDARY
from ow174.game.content import PRACTICE_RANGE, heroes
from ow174.game.script import expr, graph
from ow174.game.world import POOLS, SHIELDS_POOL


class ZenyattaAbsorbTests(unittest.TestCase):
    def arena(self, yaw=-32768, start=True):
        run, zen, sigma, _ = weapon_tests.ZenyattaCombatTests().arena()
        hero = int(graph.bodies()[0x0400000000002AB1]['hero'], 16)
        run.match.switch_hero(sigma, heroes()[hero])
        x, y, z = zen.mover.position
        sigma.mover.position = [x, y, z+8]
        sigma.mover.body = None
        if start:
            for _ in range(10):
                run.step({sigma: (yaw, 0, 0x08)})
            self.assertTrue(self.active(sigma))
        return run, zen, sigma

    def instance(self, sigma):
        return next(i for i in sigma.body_script.component.instances.values() if i.graph.index == 0x1D02)

    def active(self, sigma):
        return any(s.active and s.node.cls == 'STUStatescriptStateDeflectProjectiles'
                   for s in self.instance(sigma).states.values())

    def vitals(self, run, player):
        target = next(t for t in run.match.combat._targets() if t.owner is player)
        return run.match.combat.vitals_of(target)

    def test_front_absorbs_actual_orb_and_resource_sum_without_hit_or_reflection(self):
        run, zen, sigma = self.arena()
        charge = zen.body_script.component.entity_var(342).value()
        frame = run.frame
        run.step({zen: (0, 600, FIRE), sigma: (-32768, 0, 0)})
        for _ in range(30):
            run.step({sigma: (-32768, 0, 0)})
        self.assertEqual(self.vitals(run, sigma).total, 400)
        self.assertEqual(self.vitals(run, zen).total, 200)
        self.assertEqual(self.instance(sigma).find_var(0, 21312).value(), 48)
        self.assertFalse(zen.client.messages(combat.HIT))
        self.assertFalse(sigma.client.messages(combat.HIT))
        self.assertFalse(run.match.combat.rockets)
        self.assertFalse(run.match.combat.projectiles.shots)
        self.assertAlmostEqual(zen.body_script.component.entity_var(342).value()-charge,
                               (run.frame-frame)*combat.TICK*5, places=4)

    def test_actual_grasp_absorbs_enemy_deflected_orb_and_grants_shields(self):
        genji_hero = 0x02E0000000000029
        for active in (False, True):
            with self.subTest(grasp=active):
                run, zen, sigma = self.arena(start=False)
                match = run.match
                match.game_map = replace(match.game_map, free_for_all=False)
                zen.team = sigma.team = 0
                genji = match.add_player(3, "Genji", genji_hero, 1, False)
                in_world(match, genji)
                self.assertTrue(match.switch_hero(genji, heroes()[genji_hero]))
                run.players.append(genji)
                x, y, z = zen.mover.position
                sigma.mover.position = [x, y, z+3]
                genji.mover.position = [x, y, z+13]
                sigma.mover.body = genji.mover.body = None
                fight = match.combat
                guard = {
                    genji: (*aim(fight._eye(genji), (x, y+1, z+3)), 16),
                    sigma: (0, 0, 8 if active else 0),
                }
                for _ in range(20):
                    run.step(guard)
                self.assertEqual(self.active(sigma), active)
                direction = aim(fight._eye(zen), (x, y+1, z+13))
                run.step({**guard, zen: (*direction, FIRE)})
                orb, = fight.rockets
                for _ in range(35):
                    run.step(guard)
                    if orb not in fight.rockets:
                        break
                self.assertIs(orb.shooter, genji)
                self.assertNotIn(orb, fight.rockets)
                self.assertEqual(self.vitals(run, sigma).total, 400 if active else 352)
                self.assertEqual(self.vitals(run, zen).total, 200)
                self.assertEqual(self.vitals(run, genji).total, 200)
                self.assertEqual(self.instance(sigma).find_var(0, 21312).value(), 48 if active else None)
                self.assertEqual(len(genji.client.messages(combat.HIT)), 0 if active else 1)
                self.assertFalse(zen.client.messages(combat.HIT))
                for _ in range(100):
                    run.step({})
                self.assertAlmostEqual(self.vitals(run, sigma).bonus_shields, 28.8 if active else 0, places=4)

    def test_volley_crosses_deflect_then_grasp_expiry_in_one_flight(self):
        run, zen, sigma = self.arena(start=False)
        match = run.match
        match.game_map = replace(match.game_map, free_for_all=False)
        zen.team = sigma.team = 0
        genji_hero = heroes()[0x02E0000000000029]
        genji = match.add_player(3, "Genji", genji_hero.guid, 1, False)
        in_world(match, genji)
        self.assertTrue(match.switch_hero(genji, genji_hero))
        genji.team = 1
        run.players.append(genji)
        x, y, z = zen.mover.position
        sigma.mover.position = [x, y, z + 4]
        genji.mover.position = [x, y, z + 24]
        sigma.mover.body = genji.mover.body = None
        fight = match.combat
        target = next(target for target in fight._targets() if target.owner is genji)
        mark_aim = aim(fight._eye(zen), fight._eye(genji))
        body_aim = aim(fight._eye(zen), (x, y + target.height * .5, z + 24))
        return_aim = aim(fight._eye(genji), (x, y + 1, z + 4))

        run.step({zen: (*mark_aim, 8)})
        owner = fight.zenyatta.owner(zen)
        orb = owner.discord
        self.assertIsNotNone(orb)
        self.assertEqual(orb.target, genji.body)
        # Stagger the two authored durations to bracket the first two returned orbs.
        for frame in range(130):
            run.step({zen: (*body_aim, SECONDARY), genji: (*return_aim, 16 if frame == 31 else 0),
                      sigma: (0, 0, 8 if frame == 37 else 0)})
        self.assertTrue(self.active(sigma))
        genji_instance = next(i for i in genji.body_script.component.instances.values() if i.graph.index == 0x03A3)
        deflect = genji_instance.states[genji_instance.graph.node(18).state]
        self.assertTrue(deflect.active)

        sigma_instance = self.instance(sigma)
        absorbed = sigma_instance.find_var(0, 21312)
        shield = self.vitals(run, sigma)
        charge = zen.body_script.component.entity_var(342)
        before_charge, before_frame = charge.value(), run.frame
        shots = {}
        deflect_expired = grasp_expired = False
        for _ in range(100):
            bonus_before = shield.bonus_shields
            hits_before = len(genji.client.messages(combat.HIT))
            run.step({zen: (*body_aim, 0), genji: (*return_aim, 0), sigma: (0, 0, 0)})
            for rocket in fight.rockets:
                shots[id(rocket)] = rocket
            if not deflect_expired and not deflect.active:
                self.assertEqual(sum(rocket.shooter is genji for rocket in fight.rockets), 2)
                deflect_expired = True
            if not grasp_expired and not self.active(sigma):
                self.assertTrue(deflect_expired)
                self.assertEqual(absorbed.value(), 48)
                self.assertEqual(sum(rocket.shooter is genji for rocket in fight.rockets), 1)
                self.assertFalse(genji.client.messages(combat.HIT))
                grasp_expired = True
            if len(genji.client.messages(combat.HIT)) > hits_before:
                self.assertTrue(grasp_expired)
                self.assertAlmostEqual(bonus_before, 28.8, places=4)
                self.assertEqual(shield.bonus_shields, 0)
            if len(shots) == 5 and not fight.rockets:
                break

        self.assertTrue(grasp_expired)
        self.assertEqual([rocket.shooter for rocket in shots.values()], [genji] * 2 + [zen] * 3)
        self.assertFalse(fight.rockets)
        self.assertIs(owner.discord, orb)
        self.assertEqual(absorbed.value(), 48)
        self.assertEqual(sigma_instance.find_var(0, 41).value(), expr.Entity(genji.body))
        self.assertAlmostEqual(shield.bonus_shields_max, 28.8, places=4)
        self.assertEqual(shield.bonus_shields, 0)
        self.assertAlmostEqual(shield.total, 380.8, places=4)
        for shooter, victim, amount, count in ((genji, sigma, 48, 1), (zen, genji, 60, 3)):
            hits = [notice["+0x78"] for notice in shooter.client.messages(combat.HIT)]
            self.assertEqual([(hit["+0x0"]["+0x0"], hit["+0x4"], hit["+0x8"]) for hit in hits],
                             [(victim.body, amount, 0)] * count)
        self.assertEqual(self.vitals(run, genji).total, 20)
        self.assertEqual(self.vitals(run, zen).total, 200)
        weapon = next(i for i in zen.body_script.component.instances.values() if i.graph.index == 0x164)
        self.assertEqual(weapon.vars[53].value(), 15)
        self.assertAlmostEqual(charge.value() - before_charge,
                               180 + (run.frame - before_frame) * combat.TICK * 5, places=3)

    def test_back_is_not_protected_and_grants_no_extra_shields(self):
        run, zen, sigma = self.arena(yaw=0)
        run.step({zen: (0, 600, FIRE)})
        for _ in range(160):
            run.step({})
        self.assertFalse(self.active(sigma))
        self.assertEqual(self.vitals(run, sigma).total, 352)
        self.assertEqual(self.vitals(run, sigma).bonus_shields, 0)
        self.assertEqual(self.instance(sigma).find_var(0, 21312).value(), 0)

    def test_full_charged_burst_is_consumed_and_converts_to_separate_shields_at_native_end(self):
        run, zen, sigma = self.grant_burst()
        self.assertFalse(zen.client.messages(combat.HIT))
        parts = self.vitals(run, sigma).component((SHIELDS_POOL,))[51]
        pool = parts[SHIELDS_POOL*POOLS+1]
        self.assertEqual(struct.unpack('<IffI', pool[:16]), (4, 144, 144, SHIELDS_POOL))
        self.assertEqual(pool[17], 0)  # explicit reconstructed over-health classification

    def test_critical_volley_straddles_grasp_start_and_natural_expiry(self):
        for expires_during_flight in (False, True):
            with self.subTest(expires_during_flight=expires_during_flight):
                run, zen, sigma = self.arena(start=False)
                sigma.mover.position[2] += 16
                sigma.mover.body = None
                fight = run.match.combat
                vitals = self.vitals(run, sigma)
                instance = self.instance(sigma)
                angles = aim(fight._eye(zen), fight._eye(sigma))
                charge = zen.body_script.component.entity_var(342)
                weapon = next(i for i in zen.body_script.component.instances.values() if i.graph.index == 0x164)

                def step(*, fire=0, grasp=0):
                    run.step({zen: (*angles, fire), sigma: (-32768, 0, grasp)})

                step(fire=8)
                owner = fight.zenyatta.owner(zen)
                orb = owner.discord
                self.assertEqual(orb.target, sigma.body)
                # At this distance, frame 31 puts the two-second expiry between impacts two and three.
                for frame in range(130):
                    step(fire=SECONDARY, grasp=8 if expires_during_flight and frame == 31 else 0)
                self.assertEqual(self.active(sigma), expires_during_flight)
                before_charge, before_frame = charge.value(), run.frame
                absorbed = instance.find_var(0, 21312)
                shots = {}
                outcomes = []
                transitioned = False
                for _ in range(90):
                    was_active = self.active(sigma)
                    start_grasp = not expires_during_flight and len(outcomes) == 2 and not transitioned
                    hits_before = len(zen.client.messages(combat.HIT))
                    absorbed_before = absorbed.value() or 0
                    bonus_before = vitals.bonus_shields
                    step(grasp=8 if start_grasp else 0)
                    live = {id(rocket): rocket for rocket in fight.rockets if rocket.shooter is zen}
                    shots.update(live)
                    damage_events = len(zen.client.messages(combat.HIT)) - hits_before
                    absorbed_damage = (absorbed.value() or 0) - absorbed_before
                    if damage_events or absorbed_damage:
                        self.assertIn((damage_events, absorbed_damage), ((1, 0), (0, 48)))
                        if expires_during_flight and damage_events and not hits_before:
                            self.assertAlmostEqual(bonus_before, 57.6, places=4)
                            self.assertEqual(vitals.bonus_shields, 0)
                            self.assertAlmostEqual(vitals.total, 337.6, places=4)
                        outcomes.append("hit" if damage_events else "absorbed")
                    if self.active(sigma) != was_active:
                        self.assertTrue(live, "Grasp boundary must occur with an orb still in flight")
                        self.assertEqual(len(outcomes), 2, outcomes)
                        transitioned = True
                    if len(outcomes) == 5:
                        break
                expected = (["absorbed"] * 2 + ["hit"] * 3 if expires_during_flight
                            else ["hit"] * 2 + ["absorbed"] * 3)
                self.assertTrue(transitioned)
                self.assertEqual(outcomes, expected)
                self.assertEqual(len(shots), 5)
                self.assertFalse(fight.rockets)
                self.assertIs(owner.discord, orb)
                self.assertEqual(weapon.vars[53].value(), 15)
                hit_count = expected.count("hit")
                hits = [notice["+0x78"] for notice in zen.client.messages(combat.HIT)]
                self.assertEqual([(hit["+0x0"]["+0x0"], hit["+0x4"], hit["+0x8"]) for hit in hits],
                                 [(sigma.body, 120, combat.CRITICAL)] * hit_count)
                self.assertAlmostEqual(charge.value() - before_charge,
                                       120 * hit_count + (run.frame - before_frame) * combat.TICK * 5,
                                       places=3)
                self.assertEqual(absorbed.value(), 48 * expected.count("absorbed"))

                if not expires_during_flight:
                    self.assertTrue(self.active(sigma))
                    self.assertEqual(vitals.bonus_shields, 0)
                    for _ in range(130):
                        step()
                        if not self.active(sigma) and vitals.bonus_shields:
                            break
                self.assertFalse(self.active(sigma))
                timer = instance.states[instance.graph.node(45).state]
                self.assertAlmostEqual(timer.ended_at - timer.start, 2000, delta=16)
                self.assertAlmostEqual(vitals.bonus_shields_max, 57.6 if expires_during_flight else 86.4,
                                       places=4)
                self.assertAlmostEqual(vitals.bonus_shields, 0 if expires_during_flight else 86.4,
                                       places=4)
                self.assertAlmostEqual(vitals.total, 97.6 if expires_during_flight else 246.4, places=4)

    def grant_burst(self):
        run, zen, sigma = self.arena(start=False)
        for _ in range(130):
            run.step({zen: (0, 600, SECONDARY)})
        for _ in range(10):
            run.step({zen: (0, 600, SECONDARY), sigma: (-32768, 0, 0x08)})
        self.assertTrue(self.active(sigma))
        for _ in range(60):
            run.step({sigma: (-32768, 0, 0)})
        self.assertEqual(self.instance(sigma).find_var(0, 21312).value(), 240)
        self.assertEqual(self.vitals(run, sigma).bonus_shields, 0)
        for _ in range(80):
            run.step({sigma: (-32768, 0, 0)})
        self.assertFalse(self.active(sigma))
        self.assertAlmostEqual(self.vitals(run, sigma).bonus_shields, 144, places=4)
        self.assertAlmostEqual(self.vitals(run, sigma).total, 544, places=4)
        return run, zen, sigma

    def test_extra_shields_take_orb_damage_first_are_unhealable_and_decay_after_authored_wait(self):
        run, zen, sigma = self.grant_burst()
        vitals = self.vitals(run, sigma)
        normal = dict(vitals.now)
        run.step({zen: (0, 600, FIRE), sigma: (-32768, 0, 0)})
        for _ in range(20):
            run.step({sigma: (-32768, 0, 0)})
        self.assertAlmostEqual(vitals.bonus_shields, 96, places=4)
        self.assertEqual(vitals.now, normal)
        run.match.combat.heal_area(sigma, run.match.combat._eye(sigma), 10, 1000)
        self.assertAlmostEqual(vitals.bonus_shields, 96, places=4)
        decay = None
        for _ in range(250):
            decay = next((s for s in self.instance(sigma).states.values()
                          if s.node.pos == 70 and s.active), None)
            if decay is not None:
                break
            self.assertAlmostEqual(vitals.bonus_shields, 96, places=4)
            run.step({sigma: (-32768, 0, 0)})
        self.assertIsNotNone(decay)
        run.step({sigma: (-32768, 0, 0)})  # account for the native sub-frame boundary first
        before = vitals.bonus_shields
        for _ in range(60):
            run.step({sigma: (-32768, 0, 0)})
        self.assertAlmostEqual(before-vitals.bonus_shields, 4.8, places=3)
        self.assertEqual(vitals.now, normal)

    def test_three_actual_zen_bursts_reach_authored_four_hundred_extra_shield_cap(self):
        run = weapon_tests.Run(PRACTICE_RANGE, ('Zen1', 'Zen2', 'Zen3', 'Sigma'))
        zens, sigma = run.players[:3], run.players[3]
        run.match.bots.clear()
        for zen in zens:
            run.match.switch_hero(zen, heroes()[weapon_tests.ZENYATTA])
            zen.team = 0
        hero = int(graph.bodies()[0x0400000000002AB1]['hero'], 16)
        run.match.switch_hero(sigma, heroes()[hero])
        sigma.team = 1
        x, y, z = zens[0].mover.position
        for zen in zens:
            zen.mover.position = [x, y, z]
            zen.mover.body = None
        sigma.mover.position = [x, y, z+8]
        sigma.mover.body = None
        commands = dict.fromkeys(zens, (0, 600, SECONDARY))
        for _ in range(130):
            run.step(commands)
        for _ in range(10):
            run.step({**commands, sigma: (-32768, 0, 0x08)})
        for _ in range(150):
            run.step({sigma: (-32768, 0, 0)})
        self.assertEqual(self.instance(sigma).find_var(0, 21312).value(), 720)
        self.assertEqual(self.vitals(run, sigma).bonus_shields, 400)
        self.assertEqual(self.vitals(run, sigma).total, 800)
        for zen in zens:
            self.assertFalse(zen.client.messages(combat.HIT))

    def test_static_wall_intercepts_before_grasp_and_grants_no_shields(self):
        run, zen, sigma = self.arena()
        x, y, z = zen.mover.position
        run.match.collision_world = World([
            (x-50, y, z-50, x-50, y, z+50, x+50, y, z+50),
            (x-50, y, z-50, x+50, y, z+50, x+50, y, z-50),
            (x-5, y-1, z+4, x+5, y-1, z+4, x+5, y+5, z+4),
            (x-5, y-1, z+4, x+5, y+5, z+4, x-5, y+5, z+4),
        ])
        run.step({zen: (0, 600, FIRE), sigma: (-32768, 0, 0)})
        for _ in range(150):
            run.step({sigma: (-32768, 0, 0)})
        self.assertEqual(self.instance(sigma).find_var(0, 21312).value(), 0)
        self.assertEqual(self.vitals(run, sigma).total, 400)
        self.assertEqual(self.vitals(run, sigma).bonus_shields, 0)
        self.assertFalse(run.match.combat.rockets)

    def test_absorbed_cast_death_or_body_replacement_cannot_grant_shields_later(self):
        for replacement in (False, True):
            with self.subTest(replacement=replacement):
                run, zen, sigma = self.arena()
                run.step({zen: (0, 600, FIRE), sigma: (-32768, 0, 0)})
                for _ in range(20):
                    run.step({sigma: (-32768, 0, 0)})
                self.assertEqual(self.instance(sigma).find_var(0, 21312).value(), 48)
                old = sigma.body
                if replacement:
                    self.assertTrue(run.match.switch_hero(sigma, sigma.hero))
                    self.assertNotEqual(sigma.body, old)
                else:
                    target = next(t for t in run.match.combat._targets() if t.owner is sigma)
                    run.match.combat._damage(zen, target, 10000, False, None, run.frame)
                    self.assertTrue(run.match.combat.is_dead(sigma))
                for _ in range(200):
                    run.step({sigma: (-32768, 0, 0)})
                self.assertEqual(self.vitals(run, sigma).bonus_shields, 0)
                if replacement:
                    self.assertNotIn(old, run.match.combat.vitals)
                    self.assertFalse(any(k[0] == old for k in run.match.combat.sigma.conversions))

    def test_flashbang_front_is_consumed_without_splash_or_stun_but_back_interrupts_grasp(self):
        for front in (True, False):
            with self.subTest(front=front):
                run, caster, sigma = self.arena(start=False)
                run.match.switch_hero(caster, heroes()[0x02E0000000000042])
                x, y, z = sigma.mover.position
                caster.mover.position = [x, y, z-3]
                caster.mover.body = None
                yaw = -32768 if front else 0
                for _ in range(10):
                    run.step({sigma: (yaw, 0, 0x08)})
                self.assertTrue(self.active(sigma))
                run.step({caster: (0, 600, 0x10), sigma: (yaw, 0, 0)})
                was_stunned = False
                for _ in range(160):
                    run.step({sigma: (yaw, 0, 0)})
                    was_stunned |= run.match.combat.is_stunned(sigma)
                self.assertEqual(was_stunned, not front)
                self.assertEqual(self.instance(sigma).find_var(0, 21312).value(), 25 if front else 0)
                self.assertAlmostEqual(self.vitals(run, sigma).bonus_shields, 15 if front else 0, places=4)
                self.assertEqual(sum(self.vitals(run, sigma).now.values()), 400 if front else 375)

    def test_discord_targeting_bypasses_grasp_but_absorbed_orb_has_no_victim_damage_boost(self):
        run, zen, sigma = self.arena()
        run.step({zen: (0, 0, 0x08), sigma: (-32768, 0, 0)})
        owner = run.match.combat.zenyatta.owners[zen.body]
        self.assertEqual(owner.discord.target, sigma.body)
        run.step({zen: (0, 600, FIRE), sigma: (-32768, 0, 0)})
        for _ in range(25):
            run.step({sigma: (-32768, 0, 0)})
        self.assertEqual(self.instance(sigma).find_var(0, 21312).value(), 48)
        self.assertEqual(self.vitals(run, sigma).total, 400)
        self.assertEqual(owner.discord.target, sigma.body)
        self.assertFalse(zen.client.messages(combat.HIT))

    def test_skipped_world_frames_still_consume_projectile_once(self):
        results = []
        for advances in (range(1, 21), (20,)):
            run, zen, sigma = self.arena()
            run.step({zen: (0, 600, FIRE), sigma: (-32768, 0, 0)})
            run.step({sigma: (-32768, 0, 0)})
            frame = run.frame
            for advance in advances:
                tick = frame+advance
                run.match.update(tick*combat.TICK, tick)
            results.append((self.vitals(run, sigma).total,
                            self.instance(sigma).find_var(0, 21312).value(),
                            len(run.match.combat.rockets), len(run.match.combat.projectiles.shots)))
        self.assertEqual(results, [(400, 48, 0, 0), (400, 48, 0, 0)])

    def test_grasp_expiry_during_catchup_matches_continuous_projectile_impacts(self):
        def start():
            run, zen, sigma = self.arena(start=False)
            x, y, z = zen.mover.position
            sigma.mover.position = [x, y, z+16]
            sigma.mover.body = None
            fight = run.match.combat
            angles = aim(fight._eye(zen), (x, y+1, z+16))
            run.step({sigma: (-32768, 0, 8)})
            self.assertTrue(self.active(sigma))
            return run, zen, sigma, angles

        run, zen, sigma, angles = start()
        absorbed = self.instance(sigma).find_var(0, 21312)
        run.step({zen: (*angles, FIRE), sigma: (-32768, 0, 0)})
        launch = run.frame
        for _ in range(30):
            run.step({sigma: (-32768, 0, 0)})
            if absorbed.value() == 48:
                break
        self.assertEqual(absorbed.value(), 48)
        travel = run.frame-launch
        self.assertGreater(travel, 5)

        for impact, skipped in product((124, 125), (False, True)):
            with self.subTest(impact=impact, skipped=skipped):
                run, zen, sigma, angles = start()
                fight = run.match.combat
                cast = run.frame
                absorbed = self.instance(sigma).find_var(0, 21312)
                for relative in range(1, 122):
                    run.step({sigma: (-32768, 0, 0),
                              zen: (*angles, FIRE if relative == impact-travel else 0)})
                self.assertTrue(self.active(sigma))
                self.assertTrue(any(rocket.shooter is zen for rocket in fight.rockets))
                if skipped:
                    run.frame += 4
                    run.step({sigma: (-32768, 0, 0)})
                else:
                    for _ in range(5):
                        run.step({sigma: (-32768, 0, 0)})
                self.assertEqual(run.frame, cast+126)
                for _ in range(20):
                    run.step({sigma: (-32768, 0, 0)})
                self.assertFalse(self.active(sigma))
                self.assertFalse(fight.rockets)
                health = self.vitals(run, sigma)
                protected = impact == 124
                self.assertEqual(absorbed.value(), 48 if protected else 0)
                self.assertAlmostEqual(health.bonus_shields, 28.8 if protected else 0, places=4)
                self.assertAlmostEqual(health.total, 428.8 if protected else 352, places=4)
                self.assertEqual(len(zen.client.messages(combat.HIT)), 0 if protected else 1)

    def test_actual_zen_melee_bypasses_grasp_without_becoming_extra_shields(self):
        run, zen, sigma = self.arena()
        x, y, z = zen.mover.position
        sigma.mover.position = [x, y, z+1.5]
        sigma.mover.body = None
        run.step({zen: (0, 0, 0x800), sigma: (-32768, 0, 0)})
        for _ in range(150):
            run.step({sigma: (-32768, 0, 0)})
        self.assertEqual(self.vitals(run, sigma).total, 370)
        self.assertEqual(self.instance(sigma).find_var(0, 21312).value(), 0)
        self.assertEqual(self.vitals(run, sigma).bonus_shields, 0)

    def test_extra_shields_fully_decay_and_next_actual_cast_uses_only_new_absorbed_damage(self):
        run, zen, sigma = self.grant_burst()
        vitals = self.vitals(run, sigma)
        normal = dict(vitals.now)
        for _ in range(2400):
            run.step({sigma: (-32768, 0, 0)})
        self.assertEqual(vitals.bonus_shields, 0)
        self.assertEqual(vitals.total, 400)
        self.assertEqual(vitals.now, normal)
        for _ in range(10):
            run.step({sigma: (-32768, 0, 0x08)})
        self.assertTrue(self.active(sigma))
        self.assertEqual(self.instance(sigma).find_var(0, 21312).value(), 0)
        run.step({zen: (0, 600, FIRE), sigma: (-32768, 0, 0)})
        for _ in range(150):
            run.step({sigma: (-32768, 0, 0)})
        self.assertEqual(self.instance(sigma).find_var(0, 21312).value(), 48)
        self.assertAlmostEqual(vitals.bonus_shields, 28.8, places=4)
        self.assertEqual(vitals.now, normal)


if __name__ == '__main__':
    unittest.main()
