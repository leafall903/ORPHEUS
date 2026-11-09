"""Actual Ana grenade inputs versus Zen healing, shield recovery and Transcendence."""

import os
import unittest
from dataclasses import replace

from test_game_combat import Run, aim

from ow174.game import combat, npc, projectiles
from ow174.game.collision import World
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
                for _ in range(10):
                    helper.step(run, ana, bot)
                self.assertAlmostEqual(vitals.total, expected.total+4.8)

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
