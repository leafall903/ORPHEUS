"""Actual Mercy boost credits on the five authored NPC bodies."""

import unittest
from dataclasses import replace
from unittest.mock import Mock, patch

import test_game_mercy_boost as boost
import test_game_mercy_chains as chains
import test_game_mercy_npc as npc_tests
import test_game_mercy_npc_geometry as geometry
from test_game_combat import aim
from test_game_mercy_heavy_charge import FLOOR, World, wall

from ow174.game import combat, defense_matrix, heavy_charge, npc, npc_control, sniper_reposition, stats, winston_barriers
from ow174.game.commands import FIRE, SECONDARY
from ow174.game.content import find_hero, heroes
from ow174.game.script import expr, graph, projectile_defense
from ow174.game.script.driver import ULT_CHARGE
from ow174.game.script.projectile_defense import SHIELD_TAG

AMPLIFIED = 0x086000000000048A


class MercyNpcBoostTests(unittest.TestCase):
    def fixture(self):
        geo = geometry.MercyNpcGeometryTests()
        geo.setUp()
        self.addCleanup(geo.doCleanups)
        f = boost.MercyBoostTests()
        with patch.object(boost, "EFFECT_BUNDLE", chains.NPC_EFFECT_BUNDLE):
            f.setUp()
        self.addCleanup(f.doCleanups)
        for player in f.run.players:
            player.mover.body = None
            player.client.bot_stream = Mock(return_value=None)
        catalog = npc.load_catalog(npc_tests.NPC_DATA / "pve_npcs_174.json")
        return f, catalog

    def encounter(self, name):
        f, catalog = self.fixture()
        f.ally.mover.position[:] = (20, 1, 20)
        f.enemy.mover.position[:] = (5, 1, 6)
        definition = next(d for d in catalog.values() if d.name == name)
        bot = npc.spawn(f.match, definition, (5, 1, 4), team=0)
        component = f.mercy.body_script.component
        component.set(12918, 0.0)
        charge = component.entity_var(ULT_CHARGE)
        for _ in range(10):
            self.step(f, bot)
        self.assertAlmostEqual(f.fight.damage_scale(bot), 1.3, places=5)
        self.step(f, bot)
        self.assertEqual(npc_control.start(f.match, f.enemy), bot.entity)
        return f, bot, charge

    @staticmethod
    def step(f, bot, beam=SECONDARY, *, defense=0):
        x, y, z = bot.position
        staff = aim(f.fight._eye(f.mercy), (x, y + 1, z))
        target = aim(f.fight._eye(f.enemy), (x, y + 1, z))
        f.run.step({f.mercy: (*staff, beam), f.enemy: (*target, defense)})

    def hitscan_defense(self, name, hero):
        f, bot, charge = self.encounter(name)
        npc_control.stop(f.match)
        self.assertTrue(f.match.switch_hero(f.enemy, hero))
        f.enemy.mover.position[:] = (5, 1, 12)
        f.enemy.mover.body = None
        self.step(f, bot)
        return f, bot, charge

    def fire_hitscan(self, f, bot, graph_id, definition, *, defense=0):
        with patch.object(f.fight, "_shoot", wraps=f.fight._shoot) as shoot:
            for _ in range(100):
                self.step(f, bot, defense=defense)
                if shoot.called:
                    break
        self.assertEqual(shoot.call_count, 1)
        state = shoot.call_args.args[2]
        self.assertEqual(state.instance.graph.index, graph_id)
        self.assertIsNone(state.node.field("m_projectileSpeed"))
        self.assertEqual(state.evaluate(state.node.field("m_projectileEntity.m_entityDef")),
                         expr.Asset(definition))
        self.assertFalse(f.fight.rockets)
        bot.firing = False

    def test_npc_death_retires_boost_before_replacement_attack(self):
        attacks = {"Trooper": 20, "Enforcer": 50, "Sniper": 100,
                   "Assassin": 25, "Heavy Assault": 5}
        for name, base in attacks.items():
            with self.subTest(npc=name):
                f, catalog = self.fixture()
                fight = f.fight
                f.ally.mover.position[:] = (20, 1, 20)
                f.enemy.mover.position[:] = (5, 1, 6)
                for player in f.run.players:
                    player.client.queue_bot_death = Mock()
                    player.client.queue_bot_death_update = Mock()
                component = f.mercy.body_script.component
                component.set(12918, 0.0)
                charge = component.entity_var(ULT_CHARGE)
                definition = next(d for d in catalog.values() if d.name == name)
                bot = npc.spawn(f.match, definition, (5, 1, 4), team=0)
                target = next(t for t in fight._targets() if t.owner is bot)
                health = fight.vitals_of(target)
                health.damage(health.total - 10)
                fight._sync_body_health(bot, health)
                self.assertEqual(health.total, 10)
                for _ in range(12):
                    self.step(f, bot)
                self.assertAlmostEqual(fight.damage_scale(bot), 1.3, places=5)
                before = charge.value()
                self.step(f, bot, defense=FIRE)
                for _ in range(10):
                    if health.dead:
                        break
                    self.step(f, bot)
                self.assertTrue(health.dead)
                self.assertEqual(health.killer, f.enemy.body)
                self.assertEqual(fight.damage_scale(bot), 1)
                effects = fight.support_effects.attachments
                self.assertFalse(any(effect.target == bot.entity for effect in effects.values()))
                for _ in range(20):
                    self.step(f, bot)
                self.assertEqual(charge.value(), before)
                self.assertEqual(f.mercy.stats.value(AMPLIFIED), 0)

                replacement = npc.spawn(f.match, definition, bot.position, team=0)
                self.assertNotEqual(replacement.entity, bot.entity)
                self.assertEqual(fight.damage_scale(replacement), 1)
                for _ in range(12):
                    self.step(f, replacement)
                self.assertAlmostEqual(fight.damage_scale(replacement), 1.3, places=5)
                self.assertTrue(any(effect.target == replacement.entity for effect in effects.values()))
                self.assertEqual(npc_control.start(f.match, f.enemy), replacement.entity)
                for _ in range(180):
                    self.step(f, replacement)
                    if f.enemy_health() < 200:
                        break
                if name == "Enforcer":
                    for _ in range(3):
                        self.step(f, replacement)
                self.assertAlmostEqual(200 - f.enemy_health(), base * 1.3, places=4)
                self.assertAlmostEqual(charge.value() - before, base * 0.3, places=4)
                self.assertAlmostEqual(f.mercy.stats.value(AMPLIFIED), base * 0.3, places=4)
                self.assertEqual(f.mercy.stats.value(stats.HERO_DAMAGE), 0)
                self.assertEqual(health.total, 0)

    def test_genji_returns_boosted_trooper_hitscan_to_its_source(self):
        f, bot, charge = self.hitscan_defense("Trooper", heroes()[0x02E0000000000029])
        self.step(f, bot, defense=0x10)
        self.assertEqual(npc_control.start(f.match, f.enemy), bot.entity)
        target = next(t for t in f.fight._targets() if t.owner is bot)
        health = f.fight.vitals_of(target)
        before = health.total
        damage = f.enemy.stats.value(stats.HERO_DAMAGE)
        self.fire_hitscan(f, bot, 0x102E, 0x0400000000001FE6)
        self.assertEqual(f.enemy_health(), 200)
        self.assertEqual(health.total, before - 20)
        self.assertEqual(f.enemy.stats.value(stats.HERO_DAMAGE) - damage, 20)
        self.assertEqual(f.mercy.stats.value(AMPLIFIED), 0)
        self.assertEqual(charge.value(), 0)

    def test_genji_blocks_frontal_mercy_boosted_npc_melee_without_boost_credit(self):
        for name, base, behind in (
            ("Trooper", 30, False),
            ("Trooper", 30, True),
            ("Heavy Assault", 80, False),
            ("Heavy Assault", 80, True),
        ):
            with self.subTest(npc=name, behind=behind):
                f, bot, charge = self.hitscan_defense(name, heroes()[0x02E0000000000029])
                f.enemy.mover.position[:] = (5, 1, 6)
                f.enemy.mover.body = None
                self.step(f, bot)
                self.assertEqual(npc_control.start(f.match, f.enemy, melee=True), bot.entity)
                staff = aim(f.fight._eye(f.mercy), (5, 2, 4))
                facing = 0 if behind else -32768
                f.run.step({f.mercy: (*staff, SECONDARY), f.enemy: (facing, 0, 0x10)})
                self.assertAlmostEqual(f.fight.damage_scale(bot), 1.3)
                before = charge.value()
                with patch.object(f.fight, "_damage", wraps=f.fight._damage) as damage:
                    for _ in range(62):
                        f.run.step({f.mercy: (*staff, SECONDARY), f.enemy: (facing, 0, 0)})
                hit, = [call for call in damage.call_args_list
                        if call.args[0] is bot and call.args[1].owner is f.enemy]
                self.assertAlmostEqual(hit.args[2], base * 1.3, places=4)
                self.assertIn(expr.Asset(0x0D80000000000302), hit.kwargs["melee_tags"])
                defense = next(i for i in f.enemy.body_script.component.instances.values() if i.graph.index == 0x03A3)
                self.assertTrue(defense.node_state(defense.graph.node(107)).active)
                self.assertAlmostEqual(f.enemy_health(), 200 - (base * 1.3 if behind else 0), places=4)
                extra = base * 0.3 if behind else 0
                self.assertAlmostEqual(f.mercy.stats.value(AMPLIFIED), extra, places=4)
                self.assertAlmostEqual(charge.value() - before, extra, places=4)

    def test_sigma_absorbs_boosted_heavy_hitscan_and_keeps_source(self):
        f, bot, charge = self.hitscan_defense("Heavy Assault", heroes()[0x02E000000000023B])
        self.step(f, bot, defense=0x08)
        self.assertEqual(npc_control.start(f.match, f.enemy), bot.entity)
        defense = next(i for i in f.enemy.body_script.component.instances.values() if i.graph.index == 0x1D02)
        self.fire_hitscan(f, bot, 0x114F, 0x040000000000206A)
        self.assertEqual(f.enemy_health(), 400)
        self.assertAlmostEqual(defense.find_var(0, 21312).value(), 6.5, places=4)
        self.assertEqual(defense.find_var(0, 41).value(), expr.Entity(bot.entity))
        for _ in range(130):
            self.step(f, bot, beam=0)
        self.assertAlmostEqual(f.enemy_health(), 403.9, places=4)
        self.assertEqual(f.mercy.stats.value(AMPLIFIED), 0)
        self.assertEqual(charge.value(), 0)

    def trooper_with_sphere_and_grasp(self, centre_z):
        f, bot, charge = self.hitscan_defense("Trooper", heroes()[0x02E000000000023B])
        self.assertTrue(f.match.switch_hero(f.ally, heroes()[winston_barriers.HERO]))
        f.ally.team = f.enemy.team
        f.ally.mover.position[:] = (5, 1, centre_z)
        f.ally.mover.body = None
        f.run.step({f.ally: (0, 0, 16)})
        for _ in range(12):
            f.run.step({})
            if f.ally.body in f.fight.winston_barriers.actors:
                break
        sphere = f.fight.winston_barriers.actors[f.ally.body]
        self.assertEqual(sphere.health, 700)
        f.ally.mover.position[:] = (12, 1, centre_z)
        f.ally.mover.body = None
        for _ in range(10):
            self.step(f, bot)
        self.assertAlmostEqual(f.fight.damage_scale(bot), 1.3, places=5)

        self.step(f, bot, defense=0x08)
        grasp = next(i for i in f.enemy.body_script.component.instances.values() if i.graph.index == 0x1D02)
        self.assertTrue(grasp.state(1).active)
        return f, bot, charge, sphere, grasp

    def test_winston_sphere_break_stops_trooper_shot_before_sigma_grasp(self):
        f, bot, charge, sphere, grasp = self.trooper_with_sphere_and_grasp(10)
        sphere.health = 10
        self.assertEqual(npc_control.start(f.match, f.enemy), bot.entity)
        self.fire_hitscan(f, bot, 0x102E, 0x0400000000001FE6)
        self.assertEqual(sphere.health, 0)
        self.assertNotIn(f.ally.body, f.fight.winston_barriers.actors)
        self.assertEqual(grasp.find_var(0, 21312).value(), 0)
        self.assertEqual(f.enemy_health(), 400)

        self.step(f, bot)
        self.assertEqual(npc_control.start(f.match, f.enemy), bot.entity)
        self.fire_hitscan(f, bot, 0x102E, 0x0400000000001FE6)
        self.assertTrue(grasp.state(1).active)
        self.assertAlmostEqual(grasp.find_var(0, 21312).value(), 26, places=4)
        self.assertEqual(grasp.find_var(0, 41).value(), expr.Entity(bot.entity))
        self.assertEqual(f.enemy_health(), 400)
        self.assertEqual(f.mercy.stats.value(AMPLIFIED), 0)
        self.assertEqual(charge.value(), 0)

    def test_nearer_sigma_grasp_absorbs_trooper_shot_before_winston_sphere(self):
        f, bot, charge, sphere, grasp = self.trooper_with_sphere_and_grasp(16)
        self.assertEqual(npc_control.start(f.match, f.enemy), bot.entity)
        self.fire_hitscan(f, bot, 0x102E, 0x0400000000001FE6)
        self.assertEqual(sphere.health, 700)
        self.assertAlmostEqual(grasp.find_var(0, 21312).value(), 26, places=4)
        for _ in range(130):
            self.step(f, bot)
        self.assertFalse(grasp.state(1).active)
        self.assertAlmostEqual(f.enemy_health(), 415.6, places=4)

        self.assertEqual(npc_control.start(f.match, f.enemy), bot.entity)
        self.fire_hitscan(f, bot, 0x102E, 0x0400000000001FE6)
        self.assertAlmostEqual(sphere.health, 674, places=4)
        self.assertAlmostEqual(f.enemy_health(), 415.6, places=4)
        self.assertEqual(f.mercy.stats.value(AMPLIFIED), 0)
        self.assertEqual(charge.value(), 0)

    def test_two_active_genji_boxes_bound_repeated_trooper_reflection(self):
        genji = heroes()[0x02E0000000000029]
        f, bot, charge = self.hitscan_defense("Trooper", genji)
        self.assertTrue(f.match.switch_hero(f.ally, genji))
        f.ally.mover.position[:] = (9, 1, 7)
        f.ally.mover.body = None
        target = aim(f.fight._eye(f.enemy), (5, 2, 4))
        staff = aim(f.fight._eye(f.mercy), (5, 2, 4))
        f.run.step({f.mercy: (*staff, SECONDARY), f.enemy: (*target, 0x10), f.ally: (0, 0, 0x10)})
        for player in (f.enemy, f.ally):
            defense = next(i for i in player.body_script.component.instances.values()
                           if i.graph.index == 0x03A3)
            self.assertTrue(defense.node_state(defense.graph.node(18)).active)
        self.assertEqual(npc_control.start(f.match, f.enemy), bot.entity)
        f.ally.mover.position[:] = (5, 1, 7)
        f.ally.mover.body = None

        contacts = []
        intersect = projectile_defense.intersection

        def observe(*args):
            contact = intersect(*args)
            if contact is not None:
                contacts.append(contact[1].entity)
            return contact

        before = {actor: f.fight.vitals_of(next(t for t in f.fight._targets() if t.owner is actor)).total
                  for actor in (bot, f.enemy, f.ally)}
        with patch.object(projectile_defense, "intersection", side_effect=observe):
            self.fire_hitscan(f, bot, 0x102E, 0x0400000000001FE6)
        self.assertEqual(contacts, [f.enemy.body, f.ally.body, f.enemy.body])
        for target in f.fight._targets():
            if target.owner in before:
                self.assertEqual(f.fight.vitals_of(target).total, before[target.owner])
        self.assertEqual(f.mercy.stats.value(AMPLIFIED), 0)
        self.assertEqual(charge.value(), 0)

    def test_genji_returns_boosted_sniper_sidearm_without_casters_bonus(self):
        f, bot, charge = self.encounter("Sniper")
        self.assertTrue(f.match.switch_hero(f.enemy, heroes()[0x02E0000000000029]))
        f.enemy.mover.position[:] = (5, 1, 12)
        f.enemy.mover.body = None
        self.step(f, bot)
        self.assertEqual(npc_control.start(f.match, f.enemy, alternate=True), bot.entity)
        self.step(f, bot, defense=0x10)
        for _ in range(85):
            if f.fight.rockets:
                break
            self.step(f, bot)
        [rocket] = f.fight.rockets
        self.assertEqual(rocket.definition, 0x04000000000023BB)
        self.assertAlmostEqual(rocket.direct.amount, 6.5, places=5)
        self.assertIs(rocket.shooter, bot)
        target = next(t for t in f.fight._targets() if t.owner is bot)
        health = f.fight.vitals_of(target)
        before = health.total
        defender_damage = f.enemy.stats.value(stats.HERO_DAMAGE)
        bot.firing = False
        self.step(f, bot, beam=0)
        for _ in range(20):
            self.step(f, bot, beam=0)
            if health.total < before:
                break
        self.assertIs(rocket.shooter, f.enemy)
        self.assertIsNone(rocket.boost_credit)
        self.assertEqual(rocket.direct.amount, 5)
        self.assertEqual(health.total, before - 5)
        self.assertEqual(f.enemy_health(), 200)
        self.assertEqual(f.enemy.stats.value(stats.HERO_DAMAGE) - defender_damage, 5)
        self.assertEqual(f.mercy.stats.value(AMPLIFIED), 0)
        self.assertEqual(charge.value(), 0)
        self.assertFalse(f.fight.rockets)

    def test_sigma_absorbs_boosted_sniper_primary_and_reports_source(self):
        f, bot, charge = self.encounter("Sniper")
        self.assertTrue(f.match.switch_hero(f.enemy, heroes()[0x02E000000000023B]))
        f.enemy.mover.position[:] = (5, 1, 12)
        f.enemy.mover.body = None
        self.step(f, bot)
        self.assertEqual(npc_control.start(f.match, f.enemy), bot.entity)
        defense = next(i for i in f.enemy.body_script.component.instances.values()
                       if i.graph.index == 0x1D02)
        for _ in range(200):
            if f.fight.rockets:
                break
            self.step(f, bot)
        [rocket] = f.fight.rockets
        self.assertEqual(rocket.definition, 0x0400000000001E80)
        self.assertAlmostEqual(rocket.direct.amount, 130, places=4)
        bot.firing = False
        self.step(f, bot, beam=0, defense=0x08)
        for _ in range(12):
            self.step(f, bot, beam=0)
        self.assertFalse(f.fight.rockets)
        self.assertAlmostEqual(defense.find_var(0, 21312).value(), 130, places=4)
        self.assertEqual(defense.find_var(0, 41).value(), expr.Entity(bot.entity))
        target = next(t for t in f.fight._targets() if t.owner is f.enemy)
        health = f.fight.vitals_of(target)
        self.assertEqual(health.total, 400)
        for _ in range(130):
            self.step(f, bot, beam=0)
        self.assertAlmostEqual(health.temporary_shields[SHIELD_TAG].now, 78, places=4)
        self.assertAlmostEqual(health.total, 478, places=4)
        self.assertEqual(f.mercy.stats.value(AMPLIFIED), 0)
        self.assertEqual(charge.value(), 0)

    def test_matrix_consumes_boosted_enforcer_volley_before_damage_or_credit(self):
        mech = defense_matrix.MECH_BODY
        projectile = 0x04000000000023F0
        if graph.entity_body(mech) is None or not defense_matrix.absorbable(projectile):
            self.skipTest("prepared mech body and Enforcer projectile tags required")
        f, bot, charge = self.encounter("Enforcer")
        dva = find_hero("dva")
        self.assertTrue(f.match.switch_hero(f.enemy, replace(dva, body=mech)))
        f.enemy.mover.position[:] = (5, 1, 8)
        f.enemy.mover.body = None
        matrix = next(i for i in f.enemy.body_script.component.instances.values() if i.graph.index == 0x02CC)
        for _ in range(85):
            self.step(f, bot, defense=SECONDARY)
            if f.fight.rockets:
                break
        self.assertTrue(matrix.state(3).active)
        self.assertEqual(len(f.fight.rockets), 25)
        self.assertEqual({r.definition for r in f.fight.rockets}, {projectile})
        bot.firing = False
        for _ in range(12):
            self.step(f, bot, defense=SECONDARY)
        self.assertFalse(f.fight.rockets)
        self.assertFalse(f.fight.projectiles.shots)
        self.assertEqual(f.enemy_health(), 600)
        self.assertLess(matrix.vars[87].value(), 100)
        self.assertEqual(f.mercy.stats.value(AMPLIFIED), 0)
        self.assertEqual(charge.value(), 0)

        for _ in range(45):
            self.step(f, bot)
            if not matrix.state(3).active:
                break
        self.assertFalse(matrix.state(3).active)
        self.assertEqual(npc_control.start(f.match, f.enemy), bot.entity)
        for _ in range(160):
            self.step(f, bot)
            if f.fight.rockets:
                break
        self.assertEqual(len(f.fight.rockets), 25)
        bot.firing = False
        for _ in range(12):
            self.step(f, bot)
        self.assertFalse(f.fight.rockets)
        target = next(t for t in f.fight._targets() if t.owner is f.enemy)
        health = f.fight.vitals_of(target)
        self.assertAlmostEqual(health.total, 567.5, places=4)
        self.assertAlmostEqual(health.most[combat.ARMOUR_POOL] - health.now[combat.ARMOUR_POOL], 32.5, places=4)
        self.assertAlmostEqual(f.mercy.stats.value(AMPLIFIED), 7.5, places=4)
        self.assertAlmostEqual(charge.value(), 7.5, places=4)

    def test_actual_enforcer_volley_keeps_launch_boost_and_credits_all_pellets_after_release(self):
        f, bot, charge = self.encounter("Enforcer")
        before = charge.value()
        before_stat = f.mercy.stats.value(AMPLIFIED)
        for _ in range(180):
            self.step(f, bot)
            if f.fight.rockets:
                break
        self.assertEqual(len(f.fight.rockets), 25)
        bot.firing = False
        for _ in range(24):
            self.step(f, bot, beam=0)
        self.assertAlmostEqual(f.enemy_health(), 135, places=4)
        self.assertAlmostEqual(charge.value() - before, 15, places=4)
        self.assertAlmostEqual(f.mercy.stats.value(AMPLIFIED) - before_stat, 15, places=4)

    def test_actual_trooper_sniper_and_heavy_hits_credit_charge_and_statistics(self):
        for name, base in (("Trooper", 20), ("Sniper", 100), ("Heavy Assault", 5)):
            with self.subTest(npc=name):
                f, bot, charge = self.encounter(name)
                before = charge.value()
                before_stat = f.mercy.stats.value(AMPLIFIED)
                for _ in range(200):
                    self.step(f, bot)
                    if f.enemy_health() < 200:
                        break
                npc_control.stop(f.match)
                self.step(f, bot, beam=0)
                self.assertAlmostEqual(f.enemy_health(), 200 - base * 1.3, places=4)
                self.assertAlmostEqual(charge.value() - before, base * 0.3, places=4)
                self.assertAlmostEqual(f.mercy.stats.value(AMPLIFIED) - before_stat, base * 0.3, places=4)

    def test_actual_heavy_charge_graze_and_close_impact_credit_applied_bonus(self):
        for graze, remaining, bonus in ((True, 135, 15), (False, 0, 25)):
            with self.subTest(graze=graze):
                f, bot, charge = self.encounter("Heavy Assault")
                self.assertEqual(npc_control.start(f.match, f.enemy, charge=True), bot.entity)
                instance = heavy_charge.charge(bot)
                x, y, z = bot.position
                if graze:
                    for _ in range(80):
                        self.step(f, bot)
                        if heavy_charge.travelling(instance):
                            break
                    self.assertTrue(heavy_charge.travelling(instance))
                    f.enemy.mover.position[:] = (x + 2, y, z + 2)
                else:
                    f.enemy.spawn = ((x, y, z + 0.7), 0.0)
                    f.enemy.mover = f.enemy.new_mover()
                    f.enemy.mover.body = None
                before = charge.value()
                before_stat = f.mercy.stats.value(AMPLIFIED)
                for _ in range(90):
                    self.step(f, bot)
                    if f.enemy_health() < 200:
                        break
                self.assertAlmostEqual(f.enemy_health(), remaining, places=4)
                if graze:
                    self.assertEqual(f.fight.heavy_grazes[bot.entity], {f.enemy.body})
                else:
                    self.assertNotIn(f.enemy.body, f.fight.stuns)
                for _ in range(10):
                    self.step(f, bot, beam=0)
                self.assertAlmostEqual(f.enemy_health(), remaining, places=4)
                self.assertAlmostEqual(charge.value() - before, bonus, places=4)
                self.assertAlmostEqual(f.mercy.stats.value(AMPLIFIED) - before_stat, bonus, places=4)

    def test_actual_heavy_charge_close_impact_boosts_full_damage_and_stuns_survivor(self):
        f, bot, charge = self.encounter("Heavy Assault")
        roadhog = next(hero for hero in heroes().values() if hero.name == "Roadhog")
        self.assertTrue(f.match.switch_hero(f.enemy, roadhog))
        self.step(f, bot)
        self.assertEqual(npc_control.start(f.match, f.enemy, charge=True), bot.entity)
        x, y, z = bot.position
        f.enemy.spawn = ((x, y, z + 0.7), 0.0)
        f.enemy.mover = f.enemy.new_mover()
        f.enemy.mover.body = None
        self.assertEqual(f.enemy_health(), 600)
        before = charge.value()
        before_stat = f.mercy.stats.value(AMPLIFIED)
        for _ in range(90):
            self.step(f, bot)
            if f.enemy_health() < 600:
                break
        self.assertAlmostEqual(f.enemy_health(), 372.5, places=4)
        self.assertTrue(f.fight.is_stunned(f.enemy))
        for _ in range(10):
            self.step(f, bot, beam=0)
        self.assertAlmostEqual(f.enemy_health(), 372.5, places=4)
        self.assertAlmostEqual(charge.value() - before, 52.5, places=4)
        self.assertAlmostEqual(f.mercy.stats.value(AMPLIFIED) - before_stat, 52.5, places=4)

    def test_enemy_heavy_impact_retires_boost_but_preserves_in_flight_credit(self):
        f, catalog = self.fixture()
        roadhog = next(hero for hero in heroes().values() if hero.name == "Roadhog")
        self.assertTrue(f.match.switch_hero(f.enemy, roadhog))
        f.enemy.mover.position[:] = (26, 1, 5)
        f.enemy.mover.body = None
        definition = next(d for d in catalog.values() if d.name == "Heavy Assault")
        bot = npc.spawn(f.match, definition, (0.7, 1, 0), team=f.enemy.team)
        yaw, pitch = aim(f.fight._eye(f.mercy), (0.7, 2, 0))
        f.run.step({f.mercy: (yaw, pitch, 0)})
        self.assertEqual(npc_control.start(f.match, f.mercy, charge=True), bot.entity)

        component = f.mercy.body_script.component
        component.set(12918, 0.0)
        charge = component.entity_var(ULT_CHARGE)
        before_charge = charge.value()
        f.step(35)
        self.assertAlmostEqual(f.fight.damage_scale(f.ally), 1.3)
        f.step(1, attack=SECONDARY)
        rocket = next(rocket for rocket in f.fight.rockets if rocket.shooter is f.ally)
        self.assertAlmostEqual(rocket.direct.amount, 52, places=4)
        self.assertAlmostEqual(rocket.blast.amount, 104, places=4)

        for _ in range(40):
            f.step(1)
            if f.fight.is_stunned(f.mercy):
                break
        self.assertTrue(f.fight.is_stunned(f.mercy))
        self.assertIn(rocket, f.fight.rockets)
        self.assertEqual(f.enemy_health(), 600)
        npc_control.stop(f.match)
        f.step(35)
        self.assertTrue(f.fight.is_stunned(f.mercy))
        self.assertEqual(f.fight.damage_scale(f.ally), 1)
        self.assertAlmostEqual(f.enemy_health(), 444, places=4)
        self.assertAlmostEqual(f.mercy.stats.value(AMPLIFIED), 36, places=4)
        self.assertAlmostEqual(charge.value() - before_charge, 36, places=4)

        f.step(1, attack=FIRE)
        f.step(10)
        self.assertAlmostEqual(f.enemy_health(), 424, places=4)
        self.assertAlmostEqual(f.mercy.stats.value(AMPLIFIED), 36, places=4)
        f.step(125, beam=0)
        self.assertFalse(f.fight.is_stunned(f.mercy))
        f.step(10)
        self.assertAlmostEqual(f.fight.damage_scale(f.ally), 1.3)
        f.step(1, attack=FIRE)
        f.step(10)
        self.assertAlmostEqual(f.enemy_health(), 398, places=4)
        self.assertAlmostEqual(f.ally.stats.value(stats.HERO_DAMAGE), 202, places=4)
        self.assertAlmostEqual(f.mercy.stats.value(AMPLIFIED), 42, places=4)
        self.assertAlmostEqual(charge.value() - before_charge, 42, places=4)

    def test_sniper_reposition_resumes_damage_with_the_current_mercy_boost(self):
        for alternate, held, weapon_graph, base in (
            (False, True, 0x1122, 100), (False, False, 0x1122, 100),
            (True, True, 0x1134, 5), (True, False, 0x1134, 5),
        ):
            with self.subTest(alternate=alternate, held=held):
                f, bot, charge = self.encounter("Sniper")
                if alternate:
                    self.assertEqual(npc_control.start(f.match, f.enemy, alternate=True), bot.entity)
                self.assertEqual(npc_control.reposition(f.match, f.enemy), bot.entity)
                move = sniper_reposition.move_state(bot)
                self.assertTrue(move.active)
                before_charge = charge.value()
                before_stat = f.mercy.stats.value(AMPLIFIED)
                beam = SECONDARY if held else 0
                shoot = f.fight._shoot
                shots = []

                def observe(shooter, command, state, number, time, **kwargs):
                    if shooter is bot:
                        shots.append((move.active, state.instance.graph.index))
                    return shoot(shooter, command, state, number, time, **kwargs)

                with patch.object(f.fight, "_shoot", side_effect=observe):
                    self.step(f, bot, beam)
                    self.assertTrue(move.active)
                    self.assertFalse(bot.attack_input)
                    self.assertEqual(f.enemy_health(), 200)
                    self.assertFalse(shots)
                    for _ in range(200):
                        self.step(f, bot, beam)
                        if f.enemy_health() < 200:
                            break
                self.assertFalse(move.active)
                self.assertEqual(bot.position, (5, 1, 0))
                self.assertEqual(shots, [(False, weapon_graph)])
                bonus = base * 0.3 if held else 0
                damage = base + bonus
                self.assertAlmostEqual(f.enemy_health(), 200 - damage, places=4)
                self.assertAlmostEqual(f.fight.damage_scale(bot), 1.3 if held else 1)
                npc_control.stop(f.match)
                for _ in range(10):
                    self.step(f, bot, beam)
                self.assertAlmostEqual(f.mercy.stats.value(AMPLIFIED) - before_stat, bonus, places=4)
                self.assertAlmostEqual(charge.value() - before_charge, bonus, places=4)

    def test_actual_heavy_charge_wall_crush_credits_damage_with_beam(self):
        f, bot, charge = self.encounter("Heavy Assault")
        roadhog = next(hero for hero in heroes().values() if hero.name == "Roadhog")
        self.assertTrue(f.match.switch_hero(f.enemy, roadhog))
        f.enemy.spawn = ((5, 1, 6), 0.0)
        f.enemy.mover = f.enemy.new_mover()
        f.enemy.mover.body = None
        f.match.collision_world = World(FLOOR + wall(1, 9, 10, 9))
        self.step(f, bot)
        self.assertEqual(npc_control.start(f.match, f.enemy, charge=True), bot.entity)
        self.assertEqual(f.enemy_health(), 600)
        before = charge.value()
        before_stat = f.mercy.stats.value(AMPLIFIED)
        for _ in range(90):
            self.step(f, bot)
            if bot.entity in f.fight.heavy_pins:
                break
        self.assertIn(bot.entity, f.fight.heavy_pins)
        self.assertEqual(f.enemy_health(), 600)
        for _ in range(40):
            self.step(f, bot)
            if f.enemy_health() < 600:
                break
        self.assertAlmostEqual(f.enemy_health(), 210, places=4)
        for _ in range(10):
            self.step(f, bot, beam=0)
        self.assertNotIn(bot.entity, f.fight.heavy_pins)
        self.assertAlmostEqual(f.enemy_health(), 210, places=4)
        self.assertAlmostEqual(charge.value() - before, 90, places=4)
        self.assertAlmostEqual(f.mercy.stats.value(AMPLIFIED) - before_stat, 90, places=4)

    def test_actual_assassin_boost_follows_contact_and_periodic_damage(self):
        for release in (False, True):
            with self.subTest(release=release):
                f, bot, charge = self.encounter("Assassin")
                before = charge.value()
                before_stat = f.mercy.stats.value(AMPLIFIED)
                for _ in range(180):
                    self.step(f, bot)
                    if f.fight.pins:
                        break
                self.assertEqual(f.fight.pins.get(bot.entity), f.enemy.body)
                self.assertAlmostEqual(f.enemy_health(), 167.5, places=4)
                self.step(f, bot, beam=0 if release else SECONDARY)
                self.assertAlmostEqual(charge.value() - before, 7.5, places=4)
                for _ in range(80):
                    old_health, old_charge = f.enemy_health(), charge.value()
                    self.step(f, bot, beam=0 if release else SECONDARY)
                    if old_health - f.enemy_health() > 1:
                        break
                self.assertAlmostEqual(old_health - f.enemy_health(), 1.6 if release else 2.08, places=4)
                self.step(f, bot, beam=0 if release else SECONDARY)
                self.assertAlmostEqual(old_health - f.enemy_health(), 3.2 if release else 4.16, places=4)
                self.assertAlmostEqual(charge.value() - old_charge, 0 if release else 0.96, places=4)
                self.assertAlmostEqual(f.mercy.stats.value(AMPLIFIED) - before_stat, charge.value() - before,
                                       places=4)
