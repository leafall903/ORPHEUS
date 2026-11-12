"""Actual Ana grenade inputs versus Zen healing, shield recovery and Transcendence."""

import os
import unittest
from dataclasses import replace
from itertools import product

from test_game_combat import Run, aim, in_world

from ow174.game import combat, mei_frost, npc, npc_control, npc_shields, projectiles
from ow174.game.barriers import PERSONAL, PROJECTED, ZARYA
from ow174.game.collision import World
from ow174.game.commands import FIRE
from ow174.game.content import PRACTICE_RANGE, heroes
from ow174.game.mover import mover_data
from ow174.game.script import graph
from ow174.game.zenyatta import HERO

ANA = 0x02E000000000013B


class ZenyattaHealingEffectsTests(unittest.TestCase):
    def arena(self, *, enemy=True, second=False, ffa=False):
        names = ('Zen', 'Ally', 'Ana', 'Ana2') if second else ('Zen', 'Ally', 'Ana')
        game_map = replace(PRACTICE_RANGE, free_for_all=True, team_sizes=(8,)) if ffa else PRACTICE_RANGE
        run = Run(game_map, names)
        run.match.bots.clear()
        zen, ally, ana = run.players[:3]
        run.match.switch_hero(zen, heroes()[HERO])
        run.match.switch_hero(ana, heroes()[ANA])
        zen.team = ally.team = 0
        ana.team = 1 if enemy else 0
        x, y, z = zen.mover.position
        zen.mover.position = [x+6, y, z]
        ally.mover.position = [x, y, z]
        ana.mover.position = [x, y, z-8]
        if second:
            other = run.players[3]
            run.match.switch_hero(other, heroes()[ANA])
            other.team = ana.team
            other.mover.position = [x+1, y, z-8]
        for player in run.players:
            player.mover.body = None
        for _ in range(5):
            self.step(run, ana, ally)
        return run, zen, ally, ana

    def target(self, run, player):
        return next(t for t in run.match.combat._targets() if t.owner is player)

    def vitals(self, run, player):
        return run.match.combat.vitals_of(self.target(run, player))

    def step(self, run, ana, target, *, cast=0, zen_buttons=0):
        zen = run.players[0]
        position = target.mover.position if hasattr(target, 'mover') else target.position
        point = (position[0], position[1]+1, position[2])
        aa = aim(run.match.combat._eye(ana), point)
        za = aim(run.match.combat._eye(zen), (point[0], point[1]+.55, point[2]))
        run.step({ana: (*aa, cast), zen: (*za, zen_buttons)})

    def grenade(self, run, ana, target, *, allied=False):
        entity = self.target(run, target).entity
        table = run.match.combat.healing.amp if allied else run.match.combat.healing.anti
        previous = table.get(entity)
        self.step(run, ana, target, cast=0x10)
        for _ in range(100):
            if entity in table and table[entity] is not previous:
                return table[entity]
            self.step(run, ana, target)
        self.fail('actual grenade did not execute its healing-status branch')

    def test_actual_enemy_grenade_uses_sixty_branch_and_blocks_harmony_until_exclusive_expiry(self):
        run, zen, ally, ana = self.arena()
        effect = self.grenade(run, ana, ally)
        self.assertEqual(self.vitals(run, ally).total, 140)
        self.assertEqual(effect.end-effect.start, 250)
        self.step(run, ana, ally, zen_buttons=0x10)
        self.assertIsNotNone(run.match.combat.zenyatta.owners[zen.body].harmony)
        charge = zen.body_script.component.entity_var(342).value()
        frame = run.frame
        while run.frame < effect.end:
            self.step(run, ana, ally)
            self.assertEqual(self.vitals(run, ally).total, 140)
        self.assertNotIn(ally.body, run.match.combat.healing.anti)
        self.assertAlmostEqual(zen.body_script.component.entity_var(342).value()-charge,
                               (run.frame-frame)*combat.TICK*5, places=3)
        for _ in range(10):
            self.step(run, ana, ally)
        self.assertAlmostEqual(self.vitals(run, ally).total, 144.8)

    def test_actual_friendly_grenade_heals_one_hundred_then_amplifies_harmony(self):
        run, _, ally, ana = self.arena(enemy=False)
        vitals = self.vitals(run, ally)
        vitals.damage(150)
        effect = self.grenade(run, ana, ally, allied=True)
        self.assertEqual(vitals.total, 150)
        while run.frame <= effect.start:
            self.step(run, ana, ally)
        self.step(run, ana, ally, zen_buttons=0x10)
        for _ in range(30):
            self.step(run, ana, ally)
        self.assertAlmostEqual(vitals.total, 171.6)

    def test_transcendence_cleanses_existing_anti_via_actual_immunity_flag(self):
        run, zen, ally, ana = self.arena()
        zen.mover.position = list(ally.mover.position)
        ally.mover.position[0] += 20
        zen.mover.body = ally.mover.body = None
        self.grenade(run, ana, zen)
        self.assertEqual(self.vitals(run, zen).total, 140)
        zen.body_script.set_ult_charge(zen.body_script.component.entity_var(338).value())
        self.step(run, ana, zen, zen_buttons=0x20)
        self.assertTrue(run.match.combat.zenyatta.ultimate(zen))
        self.assertTrue(zen.body_script.component.entity_var(8440).value())
        self.assertNotIn(zen.body, run.match.combat.healing.anti)
        self.assertEqual(self.vitals(run, zen).total, 200)

    def test_transcendence_cleans_self_anti_and_frost_but_ally_needs_barrier(self):
        run, zen, ally, ana = self.arena(second=True)
        fight = run.match.combat
        mei = run.players[3]
        self.assertTrue(run.match.switch_hero(ally, heroes()[ZARYA]))
        self.assertTrue(run.match.switch_hero(mei, heroes()[mei_frost.HERO]))
        ally.team, mei.team = zen.team, ana.team
        x, y, z = zen.mover.position
        ally.mover.position = [x + 2.5, y, z]
        mei.mover.position = [x, y, z + 3]
        ally.mover.body = mei.mover.body = None
        for _ in range(20):
            run.step({})
        self.grenade(run, ana, zen)
        ally_anti = fight.healing.anti[ally.body]
        self.assertIn(zen.body, fight.healing.anti)
        hp = self.vitals(run, ally)
        self.assertEqual(hp.total, 340)
        self.step(run, ana, ally, zen_buttons=0x10)
        owner = fight.zenyatta.owner(zen)
        harmony = owner.harmony
        self.assertEqual(harmony.target, ally.body)
        spray = {mei: (*aim(fight._eye(mei), (x, y + 1, z)), FIRE)}
        for _ in range(30):
            run.step(spray)
        self.assertGreater(fight.frost.samples[zen.body][0], 0)
        self.assertFalse(fight.is_stunned(zen))
        self.assertIn(zen.body, fight.healing.anti)
        self.assertLess(self.vitals(run, zen).total, 140)
        self.assertEqual(hp.total, 340)

        script = zen.body_script
        charge = script.component.entity_var(342)
        script.set_ult_charge(script.component.entity_var(338).value())
        run.step({**spray, zen: (0, 0, 0x20)})
        for _ in range(10):
            run.step(spray)
        self.assertTrue(fight.zenyatta.ultimate(zen))
        self.assertEqual(self.vitals(run, zen).total, 200)
        self.assertNotIn(zen.body, fight.healing.anti)
        self.assertNotIn(zen.body, fight.frost.samples)
        self.assertEqual(script.component.entity_var(2427).value(), 0)
        self.assertIs(fight.healing.anti[ally.body], ally_anti)
        self.assertLess(run.frame, ally_anti.end)
        self.assertEqual(hp.total, 340)
        self.assertIs(owner.harmony, harmony)
        self.assertEqual(charge.value(), 0)

        run.step({**spray, ally: (0, 0, 0x08)})
        bubble = fight.barriers.bubbles[ally.body, PERSONAL]
        self.assertEqual(bubble.target, ally.body)
        self.assertNotIn(ally.body, fight.healing.anti)
        before = hp.total
        for _ in range(5):
            run.step(spray)
        self.assertAlmostEqual(hp.total - before, 5 * combat.TICK * (300 + 30))
        self.assertEqual(bubble.hp, 200)
        self.assertIs(owner.harmony, harmony)
        self.assertTrue(fight.zenyatta.ultimate(zen))
        self.assertEqual(charge.value(), 0)

    def test_anti_blocks_transcendence_on_ally_until_expiry_then_healing_resumes(self):
        run, zen, ally, ana = self.arena()
        effect = self.grenade(run, ana, ally)
        zen.body_script.set_ult_charge(zen.body_script.component.entity_var(338).value())
        self.step(run, ana, ally, zen_buttons=0x20)
        for _ in range(100):
            self.step(run, ana, ally)
            self.assertEqual(self.vitals(run, ally).total, 140)
        while run.frame <= effect.end+20:
            self.step(run, ana, ally)
        self.assertTrue(run.match.combat.zenyatta.ultimate(zen))
        self.assertEqual(self.vitals(run, ally).total, 200)

    def test_personal_barrier_clears_anti_and_preserves_boost_for_zen_support(self):
        run, zen, ally, ana = self.arena(second=True)
        self.assertTrue(run.match.switch_hero(ally, heroes()[ZARYA]))
        friend = run.players[3]
        friend.team = zen.team
        anti = self.grenade(run, ana, ally)
        boost = self.grenade(run, friend, ally, allied=True)
        while run.frame < boost.start:
            self.step(run, ana, ally)
        fight = run.match.combat
        vitals = self.vitals(run, ally)
        before = vitals.total
        self.step(run, ana, ally, zen_buttons=0x10)
        owner = fight.zenyatta.owner(zen)
        orb = owner.harmony
        self.assertEqual(orb.target, ally.body)
        script = zen.body_script
        script.set_ult_charge(script.component.entity_var(338).value())
        self.step(run, ana, ally, zen_buttons=0x20)
        self.assertTrue(fight.zenyatta.ultimate(zen))
        self.assertEqual(vitals.total, before)

        run.step({ally: (0, 0, 8)})
        bubble = fight.barriers.bubbles[ally.body, PERSONAL]
        self.assertEqual(bubble.target, ally.body)
        self.assertLess(run.frame, anti.end)
        self.assertNotIn(ally.body, fight.healing.anti)
        self.assertIs(fight.healing.amp[ally.body], boost)
        component = ally.body_script.component
        self.assertFalse(component.entity_var(8202).value())
        self.assertTrue(component.entity_var(8203).value())
        self.assertEqual(vitals.total, before)
        run.step({})
        self.assertIs(owner.harmony, orb)
        self.assertAlmostEqual(vitals.total - before, 7.92, places=4)
        self.assertEqual(bubble.hp, 200)
        self.assertEqual(script.component.entity_var(342).value(), 0)

    def test_boost_amplifies_transcendence_and_caps_effective_healing(self):
        run, zen, ally, ana = self.arena(enemy=False)
        self.vitals(run, ally).damage(150)
        effect = self.grenade(run, ana, ally, allied=True)
        while run.frame <= effect.start:
            self.step(run, ana, ally)
        zen.body_script.set_ult_charge(zen.body_script.component.entity_var(338).value())
        self.step(run, ana, ally, zen_buttons=0x20)
        for _ in range(5):
            self.step(run, ana, ally)
        self.assertAlmostEqual(self.vitals(run, ally).total, 186)  #150 +450/s *0.08
        for _ in range(20):
            self.step(run, ana, ally)
        self.assertEqual(self.vitals(run, ally).total, 200)

    def test_anti_blocks_shield_regeneration_then_releases_at_expiry(self):
        run, zen, ally, ana = self.arena()
        zen.mover.position = list(ally.mover.position)
        ally.mover.position[0] += 20
        zen.mover.body = ally.mover.body = None
        effect = self.grenade(run, ana, zen)
        while run.frame < effect.end:
            self.step(run, ana, zen)
            self.assertEqual(self.vitals(run, zen).total, 140)
        for _ in range(10):
            self.step(run, ana, zen)
        self.assertAlmostEqual(self.vitals(run, zen).total, 144.8)

    def test_incoming_orb_samples_only_post_anti_shield_recovery_across_a_gap(self):
        for offset, skipped in product((0, 4), (0, 5)):
            with self.subTest(offset=offset, skipped=skipped):
                run, zen, ally, ana = self.arena(second=True)
                attacker = run.players[3]
                self.assertTrue(run.match.switch_hero(attacker, heroes()[HERO]))
                x, y, z = ally.mover.position
                zen.mover.position = [x, y, z]
                ally.mover.position = [x+20, y, z]
                attacker.mover.position = [x, y, z+12]
                zen.mover.body = ally.mover.body = attacker.mover.body = None
                for _ in range(20):
                    run.step({})
                fight = run.match.combat
                health = self.vitals(run, zen)
                angles = aim(fight._eye(attacker), (x, y+0.75, z))
                run.step({attacker: (*angles, FIRE)})
                first_shot = run.frame
                for _ in range(35):
                    run.step({})
                    if health.total < 200:
                        break
                self.assertEqual(health.total, 152)
                travel = run.frame-first_shot
                self.assertGreater(travel, skipped+1)

                anti = self.grenade(run, ana, zen)
                self.assertEqual(health.now[combat.SHIELDS_POOL], 42)
                self.assertEqual(health.now[combat.HEALTH_POOL], 50)
                flag = zen.body_script.component.entity_var(8202)
                owner = fight.zenyatta.owners[zen.body]
                grenade_damage = owner.last_damage
                impact = anti.end+offset
                recovered = .48*max(0, offset-1)
                for _ in range(impact-travel-run.frame-1):
                    run.step({})
                self.assertTrue(flag.value())
                self.assertEqual(health.now[combat.SHIELDS_POOL], 42)
                run.step({attacker: (*angles, FIRE)})
                self.assertEqual(run.frame+travel, impact)
                for _ in range(travel-skipped-1):
                    run.step({})
                self.assertTrue(any(rocket.shooter is attacker for rocket in fight.rockets))
                self.assertEqual(owner.last_damage, grenade_damage)
                if run.frame < anti.end:
                    self.assertTrue(flag.value())
                    self.assertEqual(health.now[combat.SHIELDS_POOL], 42)
                else:
                    self.assertFalse(flag.value())
                    self.assertAlmostEqual(health.now[combat.SHIELDS_POOL], 42+recovered)
                run.frame += skipped
                run.step({})
                self.assertEqual(run.frame, impact)
                self.assertFalse(flag.value())
                self.assertNotIn(zen.body, fight.healing.anti)
                self.assertEqual(owner.last_damage, impact*combat.TICK)
                self.assertEqual(health.now[combat.SHIELDS_POOL], 0)
                self.assertAlmostEqual(health.now[combat.HEALTH_POOL], 44+recovered)
                hits = attacker.client.messages(combat.HIT)
                self.assertEqual([hit["+0x78"]["+0x4"] for hit in hits], [48, 48])

    def test_amplified_shield_recovery_after_anti_survives_projectile_interception(self):
        for protected, skipped in product((False, True), repeat=2):
            with self.subTest(protected=protected, skipped=skipped):
                run, zen, zarya, ana = self.arena(second=True)
                match = run.match
                attacker = run.players[3]
                self.assertTrue(match.switch_hero(attacker, heroes()[HERO]))
                self.assertTrue(match.switch_hero(zarya, heroes()[ZARYA]))
                friendly = match.add_player(5, "Friendly Ana", ANA, zen.team, False)
                in_world(match, friendly)
                self.assertTrue(match.switch_hero(friendly, heroes()[ANA]))
                friendly.team = zarya.team = zen.team
                run.players.append(friendly)
                x, y, z = zarya.mover.position
                for actor, offset in ((zen, (0, 0)), (attacker, (0, 12)),
                                      (zarya, (5, 0)), (friendly, (-6, -4))):
                    actor.mover.position = [x + offset[0], y, z + offset[1]]
                    actor.mover.body = None
                for _ in range(20):
                    run.step({})
                fight = match.combat
                hp = self.vitals(run, zen)
                owner = fight.zenyatta.owner(zen)
                shot = aim(fight._eye(attacker), (x, y + .75, z))
                run.step({attacker: (*shot, FIRE)})
                launched = run.frame
                for _ in range(20):
                    run.step({})
                    if hp.total < 200:
                        break
                self.assertEqual(hp.total, 152)
                travel = run.frame - launched
                anti = self.grenade(run, ana, zen)
                self.assertEqual(hp.now[combat.SHIELDS_POOL], 42)
                damage_clock = owner.last_damage
                while run.frame < anti.end - 30:
                    run.step({})
                amp = self.grenade(run, friendly, zen, allied=True)
                self.assertIs(fight.healing.anti[zen.body], anti)
                self.assertEqual(hp.total, 92)
                impact = anti.end + 5
                self.assertGreater(amp.end, impact)
                while run.frame < impact - travel - 1:
                    run.step({})
                run.step({attacker: (*shot, FIRE)})
                self.assertEqual(run.frame + travel, impact)
                while run.frame < anti.end + 1:
                    run.step({})
                self.assertNotIn(zen.body, fight.healing.anti)
                self.assertIs(fight.healing.amp[zen.body], amp)
                self.assertAlmostEqual(hp.now[combat.SHIELDS_POOL], 42.72)
                self.assertEqual(owner.last_damage, damage_clock)
                bubble = None
                if protected:
                    projection = aim(fight._eye(zarya), fight._eye(zen))
                    run.step({zarya: (*projection, 0x10)})
                    bubble = fight.barriers.bubbles[zarya.body, PROJECTED]
                    self.assertEqual((bubble.target, bubble.hp), (zen.body, 200))
                if skipped:
                    run.frame = impact - 1
                while run.frame < impact:
                    run.step({})
                self.assertEqual(run.frame, impact)
                hits = [notice["+0x78"]["+0x4"] for notice in attacker.client.messages(combat.HIT)]
                if protected:
                    self.assertEqual(bubble.hp, 152)
                    self.assertEqual(hits, [48])
                    self.assertAlmostEqual(hp.now[combat.SHIELDS_POOL], 45.6)
                    self.assertEqual(hp.now[combat.HEALTH_POOL], 50)
                    self.assertEqual(owner.last_damage, damage_clock)
                else:
                    self.assertEqual(hits, [48, 48])
                    self.assertEqual(hp.now[combat.SHIELDS_POOL], 0)
                    self.assertAlmostEqual(hp.now[combat.HEALTH_POOL], 46.88)
                    self.assertAlmostEqual(owner.last_damage, impact * combat.TICK)

    def test_actual_anti_target_death_and_body_replacement_retire_status_flags(self):
        for death in (True, False):
            with self.subTest(death=death):
                run, _, ally, ana = self.arena()
                self.grenade(run, ana, ally)
                component = ally.body_script.component
                old = ally.body
                if death:
                    run.match.combat._damage(ana, self.target(run, ally), 1000, False, None, run.frame)
                    self.assertTrue(run.match.combat.is_dead(ally))
                    self.assertFalse(component.entity_var(8202).value())
                else:
                    run.match.switch_hero(ally, ally.hero)
                    self.assertNotEqual(ally.body, old)
                self.assertNotIn(old, run.match.combat.healing.anti)

    def test_actual_cooldown_press_cannot_refresh_status_or_recast_later_by_itself(self):
        run, _, ally, ana = self.arena()
        effect = self.grenade(run, ana, ally)
        self.step(run, ana, ally, cast=0x10)
        for _ in range(800):
            self.step(run, ana, ally)
        self.assertEqual(self.vitals(run, ally).total, 140)
        self.assertNotIn(ally.body, run.match.combat.healing.anti)
        self.assertLess(effect.end, run.frame)
        self.grenade(run, ana, ally)
        self.assertEqual(self.vitals(run, ally).total, 80)

    def test_two_actual_grenades_refresh_anti_without_a_healing_gap(self):
        run, _, ally, ana = self.arena(second=True)
        first = self.grenade(run, ana, ally)
        self.step(run, ana, ally, zen_buttons=0x10)
        for _ in range(100):
            self.step(run, ana, ally)
        second = self.grenade(run, run.players[3], ally)
        self.assertEqual(second.start, first.start)
        self.assertGreater(second.end, first.end)
        self.assertEqual(self.vitals(run, ally).total, 80)
        while run.frame < second.end:
            self.step(run, ana, ally)
            self.assertEqual(self.vitals(run, ally).total, 80)
        for _ in range(10):
            self.step(run, ana, ally)
        self.assertAlmostEqual(self.vitals(run, ally).total, 84.8)

    def test_two_actual_friendly_grenades_refresh_but_do_not_stack_amplification(self):
        run, _, ally, ana = self.arena(enemy=False, second=True)
        self.vitals(run, ally).damage(150)
        first = self.grenade(run, ana, ally, allied=True)
        for _ in range(100):
            self.step(run, ana, ally)
        second = self.grenade(run, run.players[3], ally, allied=True)
        self.assertEqual(second.start, first.start)
        self.assertGreater(second.end, first.end)
        vitals = self.vitals(run, ally)
        vitals.damage(150)
        before = vitals.total
        self.step(run, ana, ally, zen_buttons=0x10)
        for _ in range(20):
            self.step(run, ana, ally)
        self.assertAlmostEqual(vitals.total, before+14.4)

    def test_friendly_grenade_does_not_cleanse_anti_and_boost_resumes_after_anti_expires(self):
        run, _, ally, ana = self.arena(second=True)
        anti = self.grenade(run, ana, ally)
        self.step(run, ana, ally, zen_buttons=0x10)
        for _ in range(100):
            self.step(run, ana, ally)
        other = run.players[3]
        other.team = ally.team
        amp = self.grenade(run, other, ally, allied=True)
        self.assertEqual(self.vitals(run, ally).total, 140)
        self.assertGreater(amp.end, anti.end)
        while run.frame < anti.end:
            self.step(run, ana, ally)
            self.assertEqual(self.vitals(run, ally).total, 140)
            if amp.start <= run.frame < anti.end:
                self.assertFalse(ally.body_script.component.entity_var(8203).value())
        for _ in range(10):
            self.step(run, ana, ally)
        self.assertAlmostEqual(self.vitals(run, ally).total, 147.2)

    def test_actual_ffa_same_team_is_hostile_but_self_grenade_still_heals(self):
        run, _, ally, ana = self.arena(enemy=False, ffa=True)
        self.grenade(run, ana, ally)
        self.assertEqual(self.vitals(run, ally).total, 140)
        run, _, _, ana = self.arena(ffa=True)
        vitals = self.vitals(run, ana)
        vitals.damage(150)
        self.grenade(run, ana, ana, allied=True)
        self.assertEqual(vitals.total, 150)
        self.assertNotIn(ana.body, run.match.combat.healing.anti)

    def test_actual_wall_blocks_grenade_and_status_splash_within_radius(self):
        run, _, ally, ana = self.arena()
        x, y, z = ally.mover.position
        run.match.collision_world = World([
            (x-50, y, z-50, x-50, y, z+50, x+50, y, z+50),
            (x-50, y, z-50, x+50, y, z+50, x+50, y, z-50),
            (x-5, y-1, z-2, x+5, y-1, z-2, x+5, y+5, z-2),
            (x-5, y-1, z-2, x+5, y+5, z-2, x-5, y+5, z-2),
        ])
        self.step(run, ana, ally, cast=0x10)
        for _ in range(100):
            self.step(run, ana, ally)
        self.assertEqual(self.vitals(run, ally).total, 200)
        self.assertNotIn(ally.body, run.match.combat.healing.anti)
        self.assertFalse(run.match.combat.rockets)

    def test_grenade_at_transcendence_expiry_reads_the_world_immunity_boundary(self):
        for impact in (374, 375):
            with self.subTest(impact=impact):
                run, zen, ally, ana = self.arena(second=True)
                attacker = run.players[3]
                zen.mover.position = list(ally.mover.position)
                ally.mover.position[0] += 20
                attacker.mover.position[0] += 20
                zen.mover.body = ally.mover.body = attacker.mover.body = None
                cast_frame = run.frame+1
                first = self.grenade(run, ana, zen)
                travel = first.start-cast_frame
                self.assertGreater(travel, 5)
                self.assertEqual(self.vitals(run, zen).total, 140)
                attacker.mover.position = list(ana.mover.position)
                ana.mover.position[0] -= 20
                attacker.mover.body = ana.mover.body = None
                for _ in range(15):
                    run.step({})
                zen.body_script.set_ult_charge(zen.body_script.component.entity_var(338).value())
                self.step(run, attacker, zen, zen_buttons=0x20)
                start = run.frame
                fight = run.match.combat
                health = self.vitals(run, zen)
                self.assertEqual(health.total, 200)
                self.assertNotIn(zen.body, fight.healing.anti)
                for relative in range(1, 371):
                    self.step(run, attacker, zen, cast=0x10 if relative == impact-travel else 0)
                self.assertTrue(any(rocket.shooter is attacker for rocket in fight.rockets))
                run.frame += 4
                run.step({})
                self.assertEqual(run.frame, start+375)
                self.assertFalse(fight.zenyatta.ultimate(zen))
                if impact == 374:
                    self.assertEqual(health.total, 200)
                    self.assertNotIn(zen.body, fight.healing.anti)
                else:
                    self.assertEqual(health.total, 140)
                    effect = fight.healing.anti[zen.body]
                    self.assertEqual((effect.source, effect.start), (attacker.body, start+375))

    def test_actual_second_grenade_cannot_apply_anti_to_transcendent_zen(self):
        run, zen, ally, ana = self.arena(second=True)
        zen.mover.position = list(ally.mover.position)
        ally.mover.position[0] += 20
        zen.mover.body = ally.mover.body = None
        self.grenade(run, ana, zen)
        zen.body_script.set_ult_charge(zen.body_script.component.entity_var(338).value())
        self.step(run, ana, zen, zen_buttons=0x20)
        other = run.players[3]
        self.step(run, other, zen, cast=0x10)
        for _ in range(60):
            self.step(run, other, zen)
            self.assertNotIn(zen.body, run.match.combat.healing.anti)
            self.assertEqual(self.vitals(run, zen).total, 200)
        self.assertFalse(run.match.combat.rockets)

    def defended_grenade(self, hero, button, *, enemy=True):
        run, _, ally, ana = self.arena(enemy=enemy)
        run.match.switch_hero(ally, heroes()[hero])
        for _ in range(30):
            run.step({ally: (-32768, 0, button)})
        point = ally.mover.position
        angles = aim(run.match.combat._eye(ana), (point[0], point[1]+1, point[2]))
        run.step({ana: (*angles, 0x10), ally: (-32768, 0, button)})
        for _ in range(60):
            run.step({ally: (-32768, 0, button)})
        return run, ally, ana

    def test_actual_deflect_transfers_grenade_status_source_and_enemy_faction(self):
        run, genji, ana = self.defended_grenade(0x02E0000000000029, 0x10)
        self.assertEqual(self.vitals(run, genji).total, 200)
        self.assertNotIn(genji.body, run.match.combat.healing.anti)
        self.assertEqual(self.vitals(run, ana).total, 140)
        self.assertEqual(run.match.combat.healing.anti[ana.body].source, genji.body)
        self.assertFalse(run.match.combat.rockets)

    def test_actual_grasp_consumes_grenade_without_damage_or_status_explosion(self):
        hero = int(graph.bodies()[0x0400000000002AB1]['hero'], 16)
        run, sigma, _ = self.defended_grenade(hero, 0x08)
        instance = next(i for i in sigma.body_script.component.instances.values() if i.graph.index == 0x1D02)
        self.assertEqual(instance.find_var(0, 21312).value(), 50)  # carrier before branch routing
        self.assertEqual(self.vitals(run, sigma).total, 400)
        self.assertNotIn(sigma.body, run.match.combat.healing.anti)
        self.assertFalse(run.match.combat.rockets)

    def test_skipped_update_integrates_actual_anti_expiry_and_amp_activation(self):
        for allied in (False, True):
            results = []
            for skipped in (False, True):
                with self.subTest(allied=allied, skipped=skipped):
                    run, _, ally, ana = self.arena(enemy=not allied)
                    if allied:
                        self.vitals(run, ally).damage(150)
                    effect = self.grenade(run, ana, ally, allied=allied)
                    self.step(run, ana, ally, zen_buttons=0x10)
                    stop = effect.start+10 if allied else effect.end+10
                    if skipped:
                        run.match.update(stop*combat.TICK, stop)
                    else:
                        while run.frame < stop:
                            self.step(run, ana, ally)
                    results.append(self.vitals(run, ally).total)
            self.assertAlmostEqual(results[0], results[1])
            self.assertAlmostEqual(results[0], 158.64 if allied else 144.8)

    def test_harmony_catchup_intersects_anti_expiry_with_its_own_los_deadline(self):
        for skipped in (False, True):
            with self.subTest(skipped=skipped):
                run, zen, ally, ana = self.arena()
                fight = run.match.combat
                anti = self.grenade(run, ana, ally)
                vitals = self.vitals(run, ally)
                self.assertEqual(vitals.total, 140)
                cast_frame = anti.end-184
                for _ in range(cast_frame-run.frame-1):
                    run.step({})
                self.step(run, ana, ally, zen_buttons=0x10)
                self.assertEqual(run.frame, cast_frame)
                owner = fight.zenyatta.owners[zen.body]
                orb = owner.harmony
                self.assertIsNotNone(orb)
                self.assertEqual(orb.target, ally.body)
                ally.mover.position[0] -= 40
                ally.mover.body = None
                for _ in range(180):
                    run.step({})
                self.assertIs(owner.harmony, orb)
                self.assertIs(fight.healing.anti[ally.body], anti)
                self.assertEqual(vitals.total, 140)
                if skipped:
                    run.frame += 11
                    run.step({})
                else:
                    for _ in range(12):
                        run.step({})
                self.assertIsNone(owner.harmony)
                self.assertNotIn(ally.body, fight.healing.anti)
                self.assertAlmostEqual(vitals.total, 141.44)
                for _ in range(5):
                    run.step({})
                self.assertAlmostEqual(vitals.total, 141.44)

    def test_actual_biotic_field_skipped_interval_does_not_heal_before_anti_expiry(self):
        results = []
        for skipped in (False, True):
            with self.subTest(skipped=skipped):
                run, _, ally, ana = self.arena()
                anti = self.grenade(run, ana, ally)
                self.step(run, ana, ally)
                run.step({ally: (0, 0, 0x10)})  # actual Soldier Biotic Field input
                while run.frame < anti.end-10:
                    self.step(run, ana, ally)
                self.assertEqual(self.vitals(run, ally).total, 140)
                self.assertTrue(any(s.spawn == projectiles.FIELD_ENTITY and s.field_tick is not None
                                    for s in run.match.combat.projectiles.shots))
                stop = anti.end+10
                if skipped:
                    run.match.update(stop*combat.TICK, stop)
                else:
                    while run.frame < stop:
                        self.step(run, ana, ally)
                results.append(self.vitals(run, ally).total)
        self.assertAlmostEqual(results[0], 140+projectiles.HEAL_PER_SECOND*.16)
        self.assertAlmostEqual(results[1], results[0])

    def test_actual_biotic_field_integrates_amp_expiry_without_stacking_old_multiplier(self):
        results = []
        for skipped in (False, True):
            with self.subTest(skipped=skipped):
                run, _, ally, ana = self.arena(enemy=False)
                amp = self.grenade(run, ana, ally, allied=True)
                run.step({ally: (0, 0, 0x10)})
                while run.frame < amp.end-10:
                    self.step(run, ana, ally)
                self.vitals(run, ally).damage(150)  # wounded fixture isolates expiry-rate accounting
                stop = amp.end+10
                if skipped:
                    run.match.update(stop*combat.TICK, stop)
                else:
                    while run.frame < stop:
                        self.step(run, ana, ally)
                results.append(self.vitals(run, ally).total)
        self.assertAlmostEqual(results[0], 50+projectiles.HEAL_PER_SECOND*.16*2.5)
        self.assertAlmostEqual(results[1], results[0])

    def test_expired_actual_field_uses_its_last_healing_tick_not_later_status_time(self):
        results = []
        for skipped in (False, True):
            with self.subTest(skipped=skipped):
                run, _, ally, ana = self.arena()
                run.step({ally: (0, 0, 0x10)})
                for _ in range(100):
                    self.step(run, ana, ally)
                field = next(s for s in run.match.combat.projectiles.shots
                             if s.spawn == projectiles.FIELD_ENTITY)
                expires = field.field_due+projectiles.FIELD_TICKS
                anti = self.grenade(run, ana, ally)
                self.assertLess(expires, anti.end)
                impact_health = self.vitals(run, ally).total
                while run.frame < expires-10:
                    self.step(run, ana, ally)
                stop = anti.end+10
                if skipped:
                    run.match.update(stop*combat.TICK, stop)
                else:
                    while run.frame < stop:
                        self.step(run, ana, ally)
                results.append(self.vitals(run, ally).total)
                self.assertEqual(results[-1], impact_health)
                self.assertFalse(any(s.spawn == projectiles.FIELD_ENTITY
                                     for s in run.match.combat.projectiles.shots))
        self.assertEqual(results[0], results[1])

    @unittest.skipUnless(os.environ.get('ORPHEUS_BARRIER_DATA'), 'requires verified external mesh')
    def test_actual_barrier_intercepts_grenade_and_blocks_anti_splash(self):
        record = next(v for v in graph.bodies().values() if v.get('name') == 'Reinhardt')
        run, rein, _ = self.defended_grenade(int(record['hero'], 16), 0x02)
        self.assertEqual(run.match.combat.barriers.owners[rein.body].hp, 1550)
        self.assertEqual(self.vitals(run, rein).total, 500)
        self.assertNotIn(rein.body, run.match.combat.healing.anti)
        self.assertFalse(run.match.combat.rockets)


@unittest.skipUnless(os.environ.get('ORPHEUS_NPC_DATA'), 'needs explicit external prepared NPC data')
class ZenyattaNpcHealingEffectsTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        from test_zenyatta_npcs import ZenyattaNpcTests
        cls.fixture_class = ZenyattaNpcTests
        cls.fixture_class.setUpClass()

    @classmethod
    def tearDownClass(cls):
        cls.fixture_class.tearDownClass()

    def arena(self, definition, *, enemy):
        fixture = self.fixture_class()
        run, zen, bot, vitals = fixture.arena(definition, team=0, names=('Zen', 'Ana'))
        ana = run.players[1]
        run.match.switch_hero(ana, heroes()[ANA])
        ana.team = 1 if enemy else 0
        x, y, z = bot.position
        ana.mover.position = [x, y, z+8]
        ana.mover.body = None
        return run, zen, bot, ana, vitals

    def test_actual_shielded_npc_anti_amp_harmony_expiry_and_rearm_across_gap(self):
        helper = ZenyattaHealingEffectsTests()
        for key, skipped in product(("2", "3", "4"), (False, True)):
            definition = self.fixture_class.catalog[key]
            with self.subTest(npc=definition.name, skipped=skipped):
                fixture = self.fixture_class()
                run, zen, bot, vitals = fixture.arena(
                    definition, team=0, names=("Zen", "Enemy Ana", "Friendly Ana"), difficulty_index=3,
                )
                enemy, friendly = run.players[1:]
                x, y, z = bot.position
                for ana, team, dx, dz in ((enemy, 1, 0, 8), (friendly, 0, 4, 0)):
                    self.assertTrue(run.match.switch_hero(ana, heroes()[ANA]))
                    ana.team = team
                    ana.mover.position = [x + dx, y, z + dz]
                    ana.mover.body = None
                fight = run.match.combat
                shields = combat.SHIELDS_POOL
                starting_shields = vitals.pool_total(shields)
                starting_health = vitals.pool_total(combat.HEALTH_POOL)
                self.assertGreaterEqual(starting_shields, 100)

                anti = helper.grenade(run, enemy, bot)
                wounded = starting_shields - 60
                self.assertEqual(vitals.pool_total(shields), wounded)
                helper.step(run, enemy, bot, zen_buttons=0x10)
                owner = fight.zenyatta.owners[zen.body]
                orb = owner.harmony
                self.assertEqual(orb.target, bot.entity)
                recovery = next(i for i in bot.body_script.component.instances.values() if i.graph.index == 0x0E62)
                wait = recovery.states[recovery.graph.node(52).state]
                first_wait = wait.start
                while run.frame < anti.end - 32:
                    run.step({})
                amp = helper.grenade(run, friendly, bot, allied=True)
                while run.frame < anti.end - 6:
                    run.step({})
                regen = recovery.states[recovery.graph.node(51).state]
                self.assertTrue(regen.active)
                self.assertLessEqual(amp.start, run.frame)
                self.assertGreater(amp.end, anti.end + 8)
                self.assertEqual(vitals.pool_total(shields), wounded)
                self.assertIs(fight.healing.anti[bot.entity], anti)

                frames = anti.end + 8 - run.frame
                charge = zen.body_script.component.entity_var(342)
                charge_before = charge.value()
                if skipped:
                    run.frame += frames - 1
                    run.step({})
                else:
                    for _ in range(frames):
                        run.step({})
                self.assertIs(owner.harmony, orb)
                self.assertNotIn(bot.entity, fight.healing.anti)
                self.assertIs(fight.healing.amp[bot.entity], amp)
                self.assertAlmostEqual(vitals.pool_total(shields), wounded + 11.52, places=4)
                self.assertEqual(vitals.pool_total(combat.HEALTH_POOL), starting_health)
                self.assertAlmostEqual(charge.value() - charge_before, frames * .08 + 5.76, places=3)

                zen.team = 1
                run.step({})
                self.assertIsNone(owner.harmony)
                direction = aim(fight._eye(zen), (x, y + 1, z))
                run.step({zen: (*direction, FIRE)})
                for _ in range(20):
                    before = vitals.pool_total(shields)
                    run.step({})
                    if zen.client.messages(combat.HIT):
                        break
                hits = zen.client.messages(combat.HIT)
                self.assertEqual(len(hits), 1)
                self.assertEqual(hits[0]["+0x78"]["+0x0"]["+0x0"], bot.entity)
                self.assertEqual(hits[0]["+0x78"]["+0x4"], 48)
                self.assertAlmostEqual(vitals.pool_total(shields), before - 48, places=4)
                self.assertEqual(vitals.pool_total(combat.HEALTH_POOL), starting_health)
                self.assertTrue(wait.active)
                self.assertGreater(wait.start, first_wait)
                self.assertFalse(regen.active)
                restarted_at = wait.start
                remaining_shields = vitals.pool_total(shields)
                if key == "4":
                    zx, zy, zz = zen.mover.position
                    bot.position = (zx, zy, zz + 2)
                    centre = (zx, zy + mover_data(bot.body).stand_height * .5, zz + 2)
                    run.step({zen: (*aim(fight._eye(zen), centre), 0)})
                self.assertEqual(npc_control.start(run.match, zen), bot.entity)
                zen_vitals = helper.vitals(run, zen)
                for _ in range(180):
                    run.step({})
                    if zen_vitals.total < 200:
                        break
                if key == "2":
                    for _ in range(8):
                        run.step({})
                if key == "4":
                    self.assertEqual(fight.pins[bot.entity], zen.body)
                self.assertEqual(zen_vitals.total, 200 - {"2": 112, "3": 100, "4": 25}[key])
                self.assertEqual(wait.start, restarted_at)
                self.assertTrue(wait.active)
                self.assertAlmostEqual(vitals.pool_total(shields), remaining_shields, places=4)

    def test_actual_anti_blocks_harmony_on_all_five_npc_bodies_then_expires(self):
        helper = ZenyattaHealingEffectsTests()
        for definition in self.fixture_class.catalog.values():
            with self.subTest(npc=definition.name):
                run, _, bot, ana, vitals = self.arena(definition, enemy=True)
                expected = combat.Vitals(0, definition.health, definition.armour, definition.shields)
                expected.damage(60)
                effect = helper.grenade(run, ana, bot)
                self.assertEqual(vitals.total, expected.total)
                helper.step(run, ana, bot, zen_buttons=0x10)
                while run.frame < effect.end:
                    helper.step(run, ana, bot)
                    self.assertEqual(vitals.total, expected.total)
                recovers_shields = definition.hero in npc_shields.SHIELDED_HEROES and definition.shields > 0
                if recovers_shields:
                    component = bot.body_script.component
                    recovery, = (i for i in component.instances.values() if i.graph.index == 0x0E62)
                    self.assertTrue(recovery.states[recovery.graph.node(51).state].active)
                    self.assertEqual(vitals.now[combat.SHIELDS_POOL], 40)
                for _ in range(10):
                    helper.step(run, ana, bot)
                self.assertAlmostEqual(vitals.total, expected.total+(9.6 if recovers_shields else 4.8))

    def test_projection_clears_anti_and_restores_zen_support_on_each_npc(self):
        helper = ZenyattaHealingEffectsTests()
        for definition in self.fixture_class.catalog.values():
            with self.subTest(npc=definition.name):
                run, zen, bot, ana, vitals = self.arena(definition, enemy=True)
                zarya = run.match.add_player(3, "Zarya", ZARYA, zen.team, False)
                in_world(run.match, zarya)
                self.assertTrue(run.match.switch_hero(zarya, heroes()[ZARYA]))
                zarya.team = zen.team
                run.players.append(zarya)
                x, y, z = bot.position
                zarya.mover.position = [x + 4, y, z]
                zarya.mover.body = None
                anti = helper.grenade(run, ana, bot)
                before = vitals.total
                fight = run.match.combat
                other_anti = fight.healing.anti[zarya.body]
                helper.step(run, ana, bot, zen_buttons=0x10)
                owner = fight.zenyatta.owner(zen)
                orb = owner.harmony
                self.assertEqual(orb.target, bot.entity)
                script = zen.body_script
                script.set_ult_charge(script.component.entity_var(338).value())
                helper.step(run, ana, bot, zen_buttons=0x20)
                self.assertTrue(fight.zenyatta.ultimate(zen))
                for _ in range(5):
                    run.step({})
                    self.assertEqual(vitals.total, before)

                target = helper.target(run, bot)
                direction = aim(fight._eye(zarya), (x, y + target.eye, z))
                run.step({zarya: (*direction, 16)})
                bubble = fight.barriers.bubbles[zarya.body, PROJECTED]
                self.assertEqual(bubble.target, bot.entity)
                self.assertLess(run.frame, anti.end)
                self.assertNotIn(bot.entity, fight.healing.anti)
                self.assertIs(fight.healing.anti[zarya.body], other_anti)
                self.assertFalse(bot.body_script.component.entity_var(8202).value())
                self.assertEqual(vitals.total, before)
                for _ in range(4):
                    run.step({})
                self.assertIs(owner.harmony, orb)
                self.assertTrue(fight.zenyatta.ultimate(zen))
                self.assertAlmostEqual(vitals.total - before, 21.12, places=4)
                self.assertEqual(bubble.hp, 200)
                self.assertEqual(script.component.entity_var(342).value(), 0)

    def test_actual_friendly_grenade_amplifies_harmony_on_all_five_npc_bodies(self):
        helper = ZenyattaHealingEffectsTests()
        for definition in self.fixture_class.catalog.values():
            with self.subTest(npc=definition.name):
                run, _, bot, ana, vitals = self.arena(definition, enemy=False)
                vitals.damage(150)
                damaged = vitals.total
                effect = helper.grenade(run, ana, bot, allied=True)
                self.assertAlmostEqual(vitals.total, damaged+100)
                while run.frame <= effect.start:
                    helper.step(run, ana, bot)
                helper.step(run, ana, bot, zen_buttons=0x10)
                for _ in range(20):
                    helper.step(run, ana, bot)
                self.assertAlmostEqual(vitals.total, damaged+114.4)

    def test_actual_grenade_statuses_modify_transcendence_on_all_five_npc_bodies(self):
        helper = ZenyattaHealingEffectsTests()
        for definition in self.fixture_class.catalog.values():
            for enemy in (True, False):
                with self.subTest(npc=definition.name, enemy=enemy):
                    run, zen, bot, ana, vitals = self.arena(definition, enemy=enemy)
                    fight = run.match.combat
                    maximum = vitals.total
                    if not enemy:
                        vitals.damage(150)
                    effect = helper.grenade(run, ana, bot, allied=not enemy)
                    boundary = effect.end-12 if enemy else effect.start+1
                    while run.frame < boundary:
                        helper.step(run, ana, bot)
                    before = vitals.total
                    zen.body_script.set_ult_charge(zen.body_script.component.entity_var(338).value())
                    helper.step(run, ana, bot, zen_buttons=0x20)
                    self.assertTrue(fight.zenyatta.ultimate(zen))
                    if enemy:
                        while run.frame < effect.end:
                            helper.step(run, ana, bot)
                            self.assertEqual(vitals.total, before)
                        self.assertNotIn(bot.entity, fight.healing.anti)
                    for _ in range(5):
                        helper.step(run, ana, bot)
                    rate = 300 if enemy else 450
                    if enemy and definition.hero in npc_shields.SHIELDED_HEROES and definition.shields > 0:
                        rate += 30  # both the authored shield clock and Q resume after anti
                    self.assertAlmostEqual(vitals.total, before+5*combat.TICK*rate, places=4)
                    for _ in range(15):
                        helper.step(run, ana, bot)
                    self.assertEqual(vitals.total, maximum)

    def test_actual_orb_kill_retires_anti_but_keeps_each_manual_npc_corpse(self):
        helper = ZenyattaHealingEffectsTests()
        for definition in self.fixture_class.catalog.values():
            with self.subTest(npc=definition.name):
                run, _, bot, ana, vitals = self.arena(definition, enemy=True)
                helper.grenade(run, ana, bot)
                component = bot.body_script.component
                vitals.damage(vitals.total-1)  # wounded fixture; do not claim full-health time-to-kill
                bot.team = 1
                zen = run.players[0]
                x, y, z = bot.position
                angles = aim(run.match.combat._eye(zen), (x, y+mover_data(bot.body).stand_height*.5, z))
                for _ in range(50):
                    run.step({zen: (*angles, 0x01)})
                    if bot.dead:
                        break
                self.assertTrue(bot.dead)
                self.assertNotIn(bot.entity, run.match.combat.healing.anti)
                self.assertFalse(component.entity_var(8202).value())
                for _ in range(220):
                    run.step({})
                self.assertIn(bot, run.match.bots)
                self.assertIs(run.match.combat.vitals[bot.entity], vitals)
                self.assertFalse(any(who is bot for _, who in run.match.combat.respawns))

    def test_manual_removal_retires_each_npc_amplification_on_next_combat_update(self):
        helper = ZenyattaHealingEffectsTests()
        for definition in self.fixture_class.catalog.values():
            with self.subTest(npc=definition.name):
                run, _, bot, ana, _ = self.arena(definition, enemy=False)
                helper.grenade(run, ana, bot, allied=True)
                self.assertEqual(npc.remove_spawned(run.match), (bot.entity,))
                run.step({})
                self.assertNotIn(bot.entity, run.match.combat.healing.amp)
                self.assertNotIn(bot, run.match.bots)


if __name__ == '__main__':
    unittest.main()
