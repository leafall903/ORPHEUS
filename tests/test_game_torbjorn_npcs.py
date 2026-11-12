"""External five-body NPC fixtures; permissive VM/transport adapter, no AI acceptance.

Set ORPHEUS_NPC_CATALOG_DIR to the prepared read-only directory containing
pve_npcs_174.json and game_graphs_174.json.gz. Normal runtime inputs are unchanged.
"""

import math
import os
import unittest
from dataclasses import replace
from pathlib import Path
from unittest.mock import patch

import test_game_torbjorn as torbjorn
from test_game_combat import aim
from test_game_mover import box, wall

from ow174.game import assassin, combat, hero_torbjorn, npc, npc_control, stats
from ow174.game.collision import World
from ow174.game.content import SOLDIER, heroes
from ow174.game.script import graph


@unittest.skipUnless(os.environ.get("ORPHEUS_NPC_CATALOG_DIR"), "needs external five-NPC catalog")
class TorbjornNpcTests(unittest.TestCase):
    @staticmethod
    def clear_graph_cache():
        for function in (graph._data, graph._records, graph.graph, graph.mode_variables):
            function.cache_clear()

    @classmethod
    def setUpClass(cls):
        cls.old_path = graph.GRAPHS_PATH
        directory = Path(os.environ["ORPHEUS_NPC_CATALOG_DIR"])
        graph.GRAPHS_PATH = directory / "game_graphs_174.json.gz"
        cls.clear_graph_cache()
        cls.catalog = npc.load_catalog(directory / "pve_npcs_174.json")
        if len(cls.catalog) != 5:
            raise ValueError("the acceptance fixture requires all five NPC definitions")

    @classmethod
    def tearDownClass(cls):
        graph.GRAPHS_PATH = cls.old_path
        cls.clear_graph_cache()

    def arena(self, key, distance=5.0, *, friendly=False):
        run, owner, _ = torbjorn.TorbjornProjectileTests().arena(100.0)
        match = run.match
        match.game_map = replace(match.game_map, free_for_all=False)
        for viewer in match.players:
            viewer.client.queue_bot_death = lambda *args: None
            viewer.client.queue_bot_death_update = lambda *args: None
        x, y, z = owner.mover.position
        bot = npc.spawn(match, self.catalog[key], (x, y, z + distance),
                        team=owner.team if friendly else 1 - owner.team, yaw=-32768)
        self.assertIsNotNone(bot.body_script)
        self.assertGreater(match._bot_create_update(bot).build(0).count, 0)
        return run, owner, bot, match.combat.vitals[bot.entity]

    @staticmethod
    def body_aim(run, owner, bot):
        target = next(t for t in run.match.combat._targets() if t.owner is bot)
        return aim(run.match.combat._eye(owner),
                   (target.feet[0], target.feet[1] + target.height * 0.5, target.feet[2]))

    def test_primary_secondary_quick_melee_and_hammer_damage_all_five(self):
        for key, definition in self.catalog.items():
            for label, button, expected in (("primary", 1, 70.0), ("secondary", 2, 125.0),
                                             ("quick-melee", 0x800, 30.0), ("hammer", 1, 55.0)):
                with self.subTest(npc=definition.name, action=label):
                    run, owner, bot, vitals = self.arena(key, 1.5)
                    before = vitals.total
                    if label == "hammer":
                        from ow174.game.commands import Command

                        run.frame += 1
                        owner.take_commands([Command(run.frame, action=2)])
                        for _ in range(45):
                            run.step({})
                    yaw, pitch = (self.body_aim(run, owner, bot)
                                  if label in ("primary", "secondary") else (0, 0))
                    run.step({owner: (yaw, pitch, button)})
                    for _ in range(40):
                        run.step({owner: (0, 0, button if label == "hammer" else 0)})
                    self.assertAlmostEqual(before - vitals.total, expected)

    def test_friendly_npc_takes_no_primary_secondary_or_melee_damage(self):
        for key in self.catalog:
            for button in (1, 2, 0x800):
                with self.subTest(npc=key, button=button):
                    run, owner, bot, vitals = self.arena(key, 1.5, friendly=True)
                    before = vitals.total
                    yaw, pitch = self.body_aim(run, owner, bot)
                    run.step({owner: (yaw, pitch, button)})
                    for _ in range(40):
                        run.step({})
                    self.assertEqual(vitals.total, before)

    def test_last_round_secondary_delivers_full_volley_to_each_actual_npc(self):
        for key, definition in self.catalog.items():
            with self.subTest(npc=definition.name):
                run, owner, bot, hp = self.arena(key, 1.5)
                weapon = next(i for i in owner.body_script.component.instances.values()
                              if i.graph.index == 0x1683)
                for _ in range(630):
                    run.step({owner: (16384, 0, 1)})
                for _ in range(50):
                    run.step({})
                self.assertEqual(weapon.vars[53].value(), 1)
                before = hp.total
                yaw, pitch = self.body_aim(run, owner, bot)
                run.step({owner: (yaw, pitch, 2)})
                for _ in range(25):
                    run.step({})
                self.assertAlmostEqual(before - hp.total, 125.0)
                self.assertEqual(weapon.vars[53].value(), 0)

    def test_actual_heavy_assault_weapon_damages_torbjorn_armour(self):
        key = next(key for key, row in self.catalog.items() if row.name == "Heavy Assault")
        run, owner, bot, _ = self.arena(key)
        fight = run.match.combat
        while run.frame < bot.records_from:
            run.step({})
        instance = next(i for i in bot.body_script.component.instances.values() if i.graph.index == 0x114F)
        state = instance.state(25)
        self.assertEqual(combat.hit_def(state, state.node.field("m_6396149F")).amount, 5.0)
        target = next(t for t in fight._targets() if t.owner is owner)
        vitals = fight.vitals_of(target)
        npc.aim_at(run.match, bot,
                   (target.feet[0], target.feet[1] + target.height * 0.5, target.feet[2]))
        self.assertNotEqual(bot.pitch, 0)
        self.assertEqual(bot.movement(run.frame).pitch, bot.pitch)
        self.assertTrue(bot.record_due(run.frame))
        fight._shoot(bot, None, state, 0, run.frame * 16, activation=(1, run.frame * 16))
        for _ in range(20):
            run.step({})
        self.assertAlmostEqual(vitals.total, 247.5)

    def test_trooper_and_sniper_actual_primary_input_hits_and_respects_team(self):
        for name, damage in (("Trooper", 15.0), ("Sniper", 95.0)):
            key = next(key for key, row in self.catalog.items() if row.name == name)
            for friendly in (False, True):
                with self.subTest(npc=name, friendly=friendly):
                    run, owner, bot, _ = self.arena(key, friendly=friendly)
                    # This adapter omits observer projectile transport; authoritative
                    # input, authored weapon delays and health damage still execute.
                    for viewer in run.match.players:
                        viewer.client.bot_stream = lambda *args: None
                    while run.frame < bot.records_from:
                        run.step({})
                    fight = run.match.combat
                    target = next(t for t in fight._targets() if t.owner is owner)
                    hp = fight.vitals_of(target)
                    npc.aim_at(run.match, bot, (target.feet[0], target.feet[1] + target.height * 0.5,
                                              target.feet[2]))
                    button = bot.body_script.component.world.weapon_buttons["0"]
                    bot.body_script.component.edges([(button, True)])
                    bot.attack_target = owner.entity
                    bot.firing = True
                    for _ in range(200):
                        run.step({})
                        if hp.total < 250.0:
                            break
                    self.assertIsNotNone(bot.body_script)
                    self.assertAlmostEqual(250.0 - hp.total, 0.0 if friendly else damage)

    def test_each_real_npc_identity_can_damage_and_kill_at_combat_boundary(self):
        # This exercises attribution and pools, not authored AI or attack execution.
        for key in self.catalog:
            with self.subTest(npc=key):
                run, owner, bot, _ = self.arena(key)
                fight = run.match.combat
                target = next(t for t in fight._targets() if t.owner is owner)
                vitals = fight.vitals_of(target)
                fight._damage(bot, target, 20.0, False, None, run.frame)
                self.assertEqual(vitals.total, 235.0)
                fight._damage(bot, target, 1000.0, False, None, run.frame)
                self.assertTrue(vitals.dead)
                self.assertEqual(vitals.killer, bot.entity)
                self.assertTrue(owner.body_script.component.owner_dead)

    def test_actual_manual_engagement_stop_and_restart_for_four_working_attackers(self):
        for name in ("Trooper", "Sniper", "Heavy Assault", "Enforcer"):
            with self.subTest(npc=name):
                key = next(key for key, row in self.catalog.items() if row.name == name)
                run, owner, bot, _ = self.arena(key)
                for viewer in run.match.players:
                    viewer.client.bot_stream = lambda *args: None
                while run.frame < bot.records_from:
                    run.step({})
                hp = run.match.combat.vitals[owner.body]
                self.assertEqual(npc_control.start(run.match, owner), bot.entity)
                for _ in range(200):
                    run.step({})
                    if hp.total < 250.0:
                        break
                self.assertLess(hp.total, 250.0)
                npc_control.stop(run.match)
                before = hp.total
                for _ in range(80):
                    run.step({})
                self.assertEqual(hp.total, before)
                self.assertFalse(bot.firing)
                self.assertIsNone(bot.attack_target)
                self.assertEqual(npc_control.start(run.match, owner), bot.entity)
                for _ in range(200):
                    run.step({})
                    if hp.total < before:
                        break
                self.assertLess(hp.total, before)

    def test_live_ranged_engagement_releases_on_team_change_and_resumes_when_hostile(self):
        for name, damage in (("Trooper", 15), ("Enforcer", 25), ("Sniper", 95), ("Heavy Assault", 2.5)):
            with self.subTest(npc=name):
                key = next(key for key, row in self.catalog.items() if row.name == name)
                run, owner, bot, _ = self.arena(key, friendly=True)
                match = run.match
                for viewer in match.players:
                    viewer.client.bot_stream = lambda *args: None
                while run.frame < bot.records_from:
                    run.step({})
                hp = match.combat.vitals[owner.body]
                with self.assertRaisesRegex(ValueError, "No living manual enemy"):
                    npc_control.start(match, owner)
                bot.team = 1 - owner.team
                self.assertEqual(npc_control.start(match, owner), bot.entity)
                run.step({})
                self.assertTrue(bot.attack_input)
                bot.team = owner.team
                for _ in range(150):
                    run.step({})
                    self.assertFalse(bot.attack_input)
                    self.assertEqual(hp.total, 250)
                bot.team = 1 - owner.team
                for _ in range(200):
                    run.step({})
                    if hp.total < 250:
                        break
                self.assertTrue(bot.attack_input)
                self.assertEqual(hp.total, 250 - damage)
                bot.team = owner.team
                before = hp.total
                for _ in range(160):
                    run.step({})
                    self.assertFalse(bot.attack_input)
                    self.assertEqual(hp.total, before)

    def test_actual_npc_primary_hits_spend_overload_before_base_armour(self):
        for name, expected in (("Trooper", 15), ("Sniper", 95), ("Heavy Assault", 2.5), ("Enforcer", 25)):
            with self.subTest(npc=name):
                key = next(key for key, row in self.catalog.items() if row.name == name)
                run, owner, bot, _ = self.arena(key)
                for viewer in run.match.players:
                    viewer.client.bot_stream = lambda *args: None
                while run.frame < bot.records_from:
                    run.step({})
                hp = run.match.combat.vitals[owner.body]
                run.step({owner: (0, 0, 16)})
                self.assertEqual(hp.total, 350)
                source = hp.over_armour.source
                x, y, z = owner.mover.position
                match = run.match
                open_world = match.collision_world
                match.collision_world = World(
                    box(x - 25, x + 25, y - 1, y, z - 25, z + 25)
                    + wall(x - 4, z + 2.5, x + 4, z + 2.5, y0=y, y1=y + 5)
                )
                with self.assertRaisesRegex(ValueError, "No living manual enemy"):
                    npc_control.start(match, owner)
                for _ in range(12):
                    run.step({})
                self.assertEqual(hp.over_armour.amount, 100)
                self.assertFalse(bot.attack_input)
                match.collision_world = open_world
                self.assertEqual(npc_control.start(run.match, owner), bot.entity)
                for _ in range(200):
                    run.step({})
                    if hp.total < 350:
                        break
                self.assertEqual(hp.over_armour.source, source)
                self.assertEqual(hp.over_armour.amount, 100 - expected)
                self.assertEqual(hp.now[combat.ARMOUR_POOL], 50)
                self.assertEqual(hp.now[combat.HEALTH_POOL], 200)
                npc_control.stop(run.match)
                for _ in range(325):
                    run.step({})
                self.assertIsNone(hp.over_armour.source)
                self.assertEqual(hp.total, 250)

    def supercharger_arena(self, key, distance, boost_npc):
        run, torb, bot, _ = self.arena(key, distance)
        torb.body_script.ult_seconds = 0
        torb.body_script.set_ult_charge(0)
        match = run.match
        service = match.combat.superchargers
        orisa = run.players[1]
        self.assertTrue(match.switch_hero(orisa, heroes()[0x02E000000000013E]))
        orisa.team = bot.team if boost_npc else torb.team
        x, y, z = torb.mover.position
        orisa.mover.position = [x - 5, y, z]
        orisa.mover.body = None
        for viewer in run.players:
            viewer.client.bot_stream = lambda *args: None
        for _ in range(35):
            run.step({})
        script = orisa.body_script
        script.set_ult_charge(script.component.entity_var(338).value())
        run.step({orisa: (0, 0, 32)})
        for _ in range(75):
            if service.active:
                break
            run.step({})
        device, = service.active.values()
        return run, torb, bot, device


    @unittest.skipUnless(os.environ.get("ORPHEUS_ORISA_SUPERCHARGER"), "needs authored Supercharger data")
    def test_supercharger_applies_only_to_allied_actual_npc_attacks_against_torbjorn(self):
        expected = {
            "Trooper": (15, 25), "Sniper": (95, 145), "Heavy Assault": (2.5, 3.75),
            "Enforcer": (25, 37.5), "Assassin": (20, 32.5),
        }
        for key, definition in self.catalog.items():
            for boost_npc in (False, True):
                with self.subTest(npc=definition.name, boost_npc=boost_npc):
                    assassin_pin = definition.name == "Assassin"
                    run, torb, bot, device = self.supercharger_arena(key, 2 if assassin_pin else 5, boost_npc)
                    match = run.match
                    fight = match.combat
                    service = fight.superchargers
                    orisa = device.owner
                    self.assertEqual(service.scale(torb), 1 if boost_npc else 1.5)
                    self.assertEqual(service.scale(bot), 1.5 if boost_npc else 1)
                    self.assertEqual((device.entity, bot.entity) in service.buffs, boost_npc)

                    hp = fight.vitals[torb.body]
                    before = hp.total
                    self.assertEqual(npc_control.start(match, torb), bot.entity)
                    for _ in range(300):
                        run.step({})
                        if hp.total < before:
                            break
                    self.assertAlmostEqual(before - hp.total, expected[definition.name][boost_npc], places=4)
                    if assassin_pin:
                        self.assertEqual(fight.pins[bot.entity], torb.body)
                        self.assertTrue(fight.is_stunned(torb))
                        pin = next(i for i in bot.body_script.component.instances.values()
                                   if i.graph.index == 0x1157)
                        pulse = pin.state(pin.graph.node(15).state)
                        for _ in range(100):
                            if pulse.active:
                                break
                            run.step({})
                        self.assertTrue(pulse.active)
                        before = hp.total
                        for _ in range(10):
                            run.step({})
                        self.assertAlmostEqual(before - hp.total, 12 if boost_npc else 8, places=4)
                    npc_control.stop(match)
                    self.assertNotIn(bot.entity, fight.pins)
                    self.assertEqual(torb.stats.value(stats.HERO_DAMAGE), 0)
                    self.assertEqual(orisa.stats.value(stats.HERO_DAMAGE), 0)

                    if boost_npc:
                        aura_key = device.entity, bot.entity
                        status = service.buffs[aura_key]
                        component = status.component
                        self.assertNotIn(status.id, bot.body_script.latest_live.instances)
                        if assassin_pin:
                            self.assertEqual(npc.remove_spawned(match, entities={bot.entity}), (bot.entity,))
                        else:
                            bot_hp = fight.vitals[bot.entity]
                            bot_hp.damage(bot_hp.total - 1, armour_reduction=False)
                            run.step({torb: (*self.body_aim(run, torb, bot), 1)})
                            for _ in range(30):
                                if bot.dead:
                                    break
                                run.step({})
                            self.assertTrue(bot.dead)
                            self.assertIsNotNone(bot.body_script.death_frame)
                        self.assertNotIn(aura_key, service.buffs)
                        self.assertNotIn(status.id, component.instances)
                        self.assertEqual(service.scale(bot), 1)
                        self.assertIn(device.entity, service.active)

    @unittest.skipUnless(os.environ.get("ORPHEUS_ORISA_SUPERCHARGER"), "needs authored Supercharger data")
    def test_destroying_supercharger_retires_all_five_npc_auras_before_return_fire(self):
        ordinary = {"Trooper": 15, "Sniper": 95, "Heavy Assault": 2.5, "Enforcer": 25, "Assassin": 20}
        for key, definition in self.catalog.items():
            with self.subTest(npc=definition.name):
                assassin_pin = definition.name == "Assassin"
                run, torb, bot, device = self.supercharger_arena(key, 2 if assassin_pin else 5, True)
                match = run.match
                fight = match.combat
                service = fight.superchargers
                status = service.buffs[(device.entity, bot.entity)]
                self.assertEqual(service.scale(bot), 1.5)
                self.assertEqual(device.health, 200)
                hp = fight.vitals[torb.body]
                x, y, z = device.position
                direction = aim(fight._eye(torb), (x, y + .5, z))
                for capacity in (130, 60, 0):
                    run.step({torb: (*direction, 1)})
                    for _ in range(45):
                        run.step({})
                    self.assertEqual(device.health, capacity)
                    self.assertEqual(service.scale(bot), 1.5 if capacity else 1)
                self.assertFalse(service.active)
                self.assertFalse(service.buffs)
                self.assertNotIn(status.id, bot.body_script.component.instances)
                self.assertFalse(bot.dead)
                self.assertEqual(torb.stats.value(stats.HERO_DAMAGE), 0)
                self.assertEqual(torb.body_script.component.entity_var(342).value(), 0)

                before = hp.total
                self.assertEqual(npc_control.start(match, torb), bot.entity)
                for _ in range(300):
                    run.step({})
                    if hp.total < before:
                        break
                self.assertAlmostEqual(before - hp.total, ordinary[definition.name], places=4)
                if assassin_pin:
                    self.assertEqual(fight.pins[bot.entity], torb.body)
                npc_control.stop(match)

    @unittest.skipUnless(os.environ.get("ORPHEUS_ORISA_SUPERCHARGER"), "needs authored Supercharger data")
    def test_enforcer_airborne_pellets_lose_boost_when_rivet_destroys_supercharger(self):
        key = next(key for key, row in self.catalog.items() if row.name == "Enforcer")
        run, torb, bot, device = self.supercharger_arena(key, 18, True)
        match = run.match
        fight = match.combat
        service = fight.superchargers
        hp = fight.vitals[torb.body]
        before = hp.total
        x, y, z = device.position
        direction = aim(fight._eye(torb), (x, y + .5, z))
        for capacity in (130, 60):
            run.step({torb: (*direction, 1)})
            for _ in range(45):
                run.step({})
            self.assertEqual(device.health, capacity)
        self.assertEqual(service.scale(bot), 1.5)
        self.assertEqual(npc_control.start(match, torb), bot.entity)
        for _ in range(180):
            run.step({})
            if any(rocket.shooter is bot for rocket in fight.rockets):
                break
        volley = [rocket for rocket in fight.rockets if rocket.shooter is bot]
        self.assertEqual(len(volley), 25)
        self.assertEqual(hp.total, before)
        with patch.object(fight, "_damage", wraps=fight._damage) as hits:
            run.step({torb: (*direction, 1)})
            for _ in range(12):
                if not service.active:
                    break
                run.step({})
            self.assertEqual(device.health, 0)
            self.assertEqual(service.scale(bot), 1)
            self.assertEqual(hp.total, before)
            self.assertTrue(any(rocket.shooter is bot for rocket in fight.rockets))
            for _ in range(35):
                run.step({})
        npc_control.stop(match)
        amounts = [
            call.args[2] for call in hits.call_args_list
            if call.args[0] is bot and call.args[1].owner is torb
        ]
        self.assertTrue(amounts)
        for amount in amounts:
            self.assertAlmostEqual(amount, .4, places=5)
        self.assertAlmostEqual(before - hp.total, sum(amounts) / 2, places=5)
        self.assertEqual(torb.stats.value(stats.HERO_DAMAGE), 0)
        self.assertEqual(torb.body_script.component.entity_var(342).value(), 0)


    def test_enforcer_death_before_release_or_with_airborne_pellets(self):
        key = next(key for key, row in self.catalog.items() if row.name == "Enforcer")
        for fire_frame, death_frame in ((45, 61), (47, 63)):
            with self.subTest(death_frame=death_frame):
                run, torb, bot, enemy_hp = self.arena(key, 18)
                fight = run.match.combat
                torb.body_script.ult_seconds = 0
                torb.body_script.set_ult_charge(0)
                for viewer in run.match.players:
                    viewer.client.bot_stream = lambda *args: None
                while run.frame < bot.records_from:
                    run.step({})
                hp = fight.vitals[torb.body]
                base = dict(hp.now)
                direction = self.body_aim(run, torb, bot)
                run.step({torb: (*direction, 16)})
                source = hp.over_armour.source
                self.assertIsNotNone(source)
                self.assertEqual(npc_control.start(run.match, torb), bot.entity)
                enemy_hp.damage(enemy_hp.total - 10)
                volley = set()
                for frame in range(180):
                    run.step({torb: (*direction, int(frame == fire_frame))})
                    pellets = {shot for shot in fight.rockets if shot.shooter is bot}
                    self.assertEqual(bot.dead, frame >= death_frame)
                    if frame == 62 and death_frame == 63:
                        volley = pellets
                        self.assertEqual(len(volley), 25)
                    if frame <= death_frame:
                        self.assertEqual(pellets, volley)
                        self.assertEqual(hp.over_armour.amount, 100)
                    else:
                        self.assertLessEqual(pellets, volley)
                        self.assertFalse(bot.attack_input)
                self.assertEqual(enemy_hp.killer, torb.body)
                self.assertEqual(torb.stats.value(stats.HERO_DAMAGE), 10)
                self.assertEqual(torb.body_script.component.entity_var(342).value(), 10)
                self.assertEqual(hp.over_armour.source, source)
                self.assertAlmostEqual(hp.over_armour.amount, 98.6 if volley else 100, places=5)
                self.assertEqual(hp.now, base)
                self.assertFalse(fight.rockets)

    def test_ranged_npc_death_on_attack_frame_prevents_release(self):
        cases = (("Trooper", 8, 7, 21, 0, 15), ("Heavy Assault", 8, 7, 31, 0, 2.5),
                 ("Sniper", 18, 16, 125, 6, 95))
        for name, distance, rivet_ticks, release, npc_ticks, damage in cases:
            key = next(key for key, row in self.catalog.items() if row.name == name)
            for death_offset in (-1, 0, 1):
                with self.subTest(npc=name, death_offset=death_offset):
                    run, torb, bot, enemy_hp = self.arena(key, distance)
                    fight = run.match.combat
                    torb.body_script.ult_seconds = 0
                    torb.body_script.set_ult_charge(0)
                    for viewer in run.match.players:
                        viewer.client.bot_stream = lambda *args: None
                    while run.frame < bot.records_from:
                        run.step({})
                    hp = fight.vitals[torb.body]
                    base = dict(hp.now)
                    direction = self.body_aim(run, torb, bot)
                    run.step({torb: (*direction, 16)})
                    source = hp.over_armour.source
                    self.assertIsNotNone(source)
                    if name != "Sniper":
                        for _ in range(45):
                            run.step({torb: (*direction, 0)})
                    self.assertEqual(npc_control.start(run.match, torb), bot.entity)
                    enemy_hp.damage(enemy_hp.total - 10)
                    death = release + death_offset
                    fire = death - rivet_ticks
                    impact = release + npc_ticks
                    attack_releases = death_offset > 0
                    airborne = None
                    for frame in range(impact + 26):
                        run.step({torb: (*direction, int(frame == fire))})
                        self.assertEqual(bot.dead, frame >= death)
                        shots = [shot for shot in fight.rockets if shot.shooter is bot]
                        if npc_ticks and attack_releases and release <= frame < impact:
                            shot, = shots
                            if frame == release:
                                airborne = shot
                            self.assertIs(shot, airborne)
                        else:
                            self.assertFalse(shots)
                        if frame > death:
                            self.assertFalse(bot.attack_input)
                        loss = damage if attack_releases and frame >= impact else 0
                        self.assertEqual(hp.over_armour.amount, 100 - loss)

                    self.assertEqual(hp.over_armour.source, source)
                    self.assertEqual(hp.now, base)
                    self.assertEqual(enemy_hp.killer, torb.body)
                    self.assertEqual(torb.stats.value(stats.HERO_DAMAGE), 10)
                    self.assertEqual(torb.body_script.component.entity_var(342).value(), 10)
                    self.assertFalse(fight.rockets)

    def test_airborne_rivet_kills_assassin_before_or_after_pin_admission(self):
        key = next(key for key, row in self.catalog.items() if row.name == "Assassin")
        for pins_first in (False, True):
            with self.subTest(pins_first=pins_first):
                run, torb, bot, enemy_hp = self.arena(key, 2)
                match = run.match
                fight = match.combat
                torb.body_script.ult_seconds = 0
                torb.body_script.set_ult_charge(0)
                for viewer in match.players:
                    viewer.client.bot_stream = lambda *args: None
                while run.frame < bot.records_from:
                    run.step({})
                hp = fight.vitals[torb.body]
                base = dict(hp.now)
                direction = self.body_aim(run, torb, bot)
                run.step({torb: (*direction, 16)})
                for _ in range(45):
                    run.step({torb: (*direction, 0)})
                source = hp.over_armour.source
                self.assertIsNotNone(source)
                enemy_hp.damage(enemy_hp.total - 10)
                run.step({torb: (*direction, 1)})
                rivet, = fight.rockets
                self.assertIs(rivet.shooter, torb)
                self.assertEqual(rivet.distance, 0)
                if not pins_first:
                    run.step({torb: (*direction, 0)})
                self.assertFalse(bot.dead)
                self.assertIn(rivet, fight.rockets)
                self.assertEqual(npc_control.start(match, torb), bot.entity)
                run.step({})

                if pins_first:
                    self.assertFalse(bot.dead)
                    self.assertIs(fight.rockets[0], rivet)
                    self.assertEqual(fight.pins.get(bot.entity), torb.body)
                    self.assertTrue(fight.is_stunned(torb))
                    self.assertEqual(hp.over_armour.amount, 80)
                    run.step({})
                self.assertTrue(bot.dead)
                self.assertEqual(enemy_hp.killer, torb.body)
                self.assertFalse(fight.rockets)
                self.assertNotIn(bot.entity, fight.pins)
                self.assertFalse(fight.is_stunned(torb))
                self.assertEqual(hp.over_armour.amount, 80 if pins_first else 100)
                for _ in range(45):
                    run.step({})
                    self.assertFalse(bot.attack_input)
                    self.assertFalse(fight.is_stunned(torb))
                self.assertIsNone(bot.body_script.component.world.manual_target)
                self.assertEqual(hp.over_armour.source, source)
                self.assertEqual(hp.over_armour.amount, 80 if pins_first else 100)
                self.assertEqual(hp.now, base)
                self.assertEqual(torb.stats.value(stats.HERO_DAMAGE), 10)
                self.assertEqual(torb.body_script.component.entity_var(342).value(), 10)

    def test_actual_npc_lethal_hits_clear_overload_and_respawn_with_fresh_armour(self):
        for key, definition in self.catalog.items():
            with self.subTest(npc=definition.name):
                distance = 1.5 if definition.name == "Assassin" else 5
                run, owner, bot, _ = self.arena(key, distance)
                match = run.match
                fight = match.combat
                for viewer in match.players:
                    viewer.client.bot_stream = lambda *args: None
                while run.frame < bot.records_from:
                    run.step({})
                old_body = owner.body
                hp = fight.vitals[old_body]
                run.step({owner: (0, 0, 16)})
                self.assertEqual(hp.total, 350)
                source = hp.over_armour.source
                self.assertIsNotNone(source)
                # Keep a live Overload source on the lethal tick for every attack.
                hp.over_armour.amount = 1
                hp.now[combat.ARMOUR_POOL] = 0
                hp.now[combat.HEALTH_POOL] = 1
                self.assertEqual(npc_control.start(match, owner), bot.entity)
                for _ in range(200):
                    self.assertEqual(hp.over_armour.source, source)
                    self.assertEqual(hp.over_armour.amount, 1)
                    run.step({})
                    if hp.dead:
                        break
                self.assertTrue(hp.dead)
                self.assertEqual(hp.killer, bot.entity)
                self.assertEqual(hp.total, 0)
                self.assertIsNone(hp.over_armour.source)
                npc_control.stop(match)
                for _ in range(750):
                    if owner.body != old_body:
                        break
                    run.step({})
                self.assertNotEqual(owner.body, old_body)
                fresh = fight.vitals[owner.body]
                self.assertEqual(fresh.total, 250)
                self.assertEqual(fresh.now[combat.ARMOUR_POOL], 50)
                self.assertEqual(fresh.over_armour.amount, 0)
                self.assertIsNone(fresh.over_armour.source)
                run.step({owner: (0, 0, 16)})
                self.assertEqual(fresh.total, 350)

    def test_all_five_npc_kills_isolate_pending_background_reload_from_respawn(self):
        from ow174.game.commands import Command

        for key, definition in self.catalog.items():
            with self.subTest(npc=definition.name):
                sniper = definition.name == "Sniper"
                distance = 1.5 if definition.name == "Assassin" else 5
                run, owner, bot, enemy_hp = self.arena(key, distance)
                match = run.match
                fight = match.combat
                for viewer in match.players:
                    viewer.client.bot_stream = lambda *args: None
                while run.frame < bot.records_from:
                    run.step({})
                old_body, old_script = owner.body, owner.body_script
                rifle = next(instance for instance in old_script.component.instances.values()
                             if instance.graph.index == 0x1683)
                ammo = rifle.vars[53]
                run.step({owner: (16384, 0, 1)})
                for _ in range(50):
                    run.step({})
                origin = tuple(owner.mover.position)
                yaw, pitch = self.body_aim(run, owner, bot)
                run.step({owner: (yaw, pitch, 0)})
                if sniper:
                    self.assertEqual(npc_control.start(match, owner), bot.entity)
                    for _ in range(16):
                        run.step({})
                run.frame += 1
                match.update(run.frame * combat.TICK, run.frame)
                owner.take_commands([Command(run.frame, yaw=yaw, pitch=pitch, action=2)])
                selected = run.frame
                background = rifle.state(25)
                self.assertTrue(background.active)
                self.assertEqual(ammo.value(), 17)

                hp = fight.vitals[old_body]
                hp.damage(hp.total - 1, armour_reduction=False)
                if not sniper:
                    self.assertEqual(npc_control.start(match, owner), bot.entity)
                while not hp.dead and run.frame < selected + 124:
                    run.step({})
                self.assertTrue(hp.dead)
                self.assertEqual(hp.killer, bot.entity)
                self.assertTrue(old_script.component.owner_dead)
                self.assertTrue(background.active)
                self.assertEqual(ammo.value(), 17)
                npc_control.stop(match)
                enemy_before = enemy_hp.total

                with patch.object(fight, "_shoot", wraps=fight._shoot) as shots:
                    while run.frame < selected + 126:
                        run.frame += 1
                        match.update(run.frame * combat.TICK, run.frame)
                        owner.take_commands([Command(run.frame, yaw=yaw, pitch=pitch,
                                                     buttons=1 | 2 | 16 | 0x400, action=1)])
                        self.assertEqual(owner.body, old_body)
                        self.assertEqual(ammo.value(), 17 if run.frame < selected + 125 else 18)
                    self.assertFalse(any(event.args[0] is owner for event in shots.call_args_list))
                self.assertEqual(enemy_hp.total, enemy_before)
                self.assertFalse(rifle.state(8).active)
                self.assertEqual(ammo.value(), 18)
                self.assertFalse(background.active)
                self.assertIsNone(hp.over_armour.source)

                for _ in range(750):
                    if owner.body != old_body:
                        break
                    run.step({})
                self.assertNotEqual(owner.body, old_body)
                self.assertTrue(old_script.retired)
                self.assertNotIn(old_body, fight.vitals)
                self.assertEqual(fight.vitals[owner.body].total, 250)
                fresh_rifle = next(instance for instance in owner.body_script.component.instances.values()
                                   if instance.graph.index == 0x1683)
                self.assertIsNot(fresh_rifle, rifle)
                self.assertEqual(fresh_rifle.vars[53].value(), 18)
                self.assertFalse(fresh_rifle.state(25).active)
                owner.mover.position = list(origin)
                owner.mover.body = None
                for _ in range(35):
                    run.step({})
                yaw, pitch = self.body_aim(run, owner, bot)
                run.step({owner: (yaw, pitch, 1)})
                for _ in range(20):
                    run.step({})
                self.assertEqual(enemy_before - enemy_hp.total, 70)
                self.assertEqual(fresh_rifle.vars[53].value(), 17)
                self.assertEqual(ammo.value(), 18)

    def test_continuous_npc_input_crosses_overload_expiry_and_exhaustion(self):
        cases = (("Trooper", 270, 15), ("Enforcer", 240, 25), ("Sniper", 175, 95),
                 ("Heavy Assault", 270, 2.5), ("Trooper", 0, 15))
        for name, delay, hit in cases:
            with self.subTest(npc=name, delay=delay):
                key = next(key for key, row in self.catalog.items() if row.name == name)
                run, owner, bot, _ = self.arena(key)
                for viewer in run.match.players:
                    viewer.client.bot_stream = lambda *args: None
                while run.frame < bot.records_from:
                    run.step({})
                hp = run.match.combat.vitals[owner.body]
                run.step({owner: (0, 0, 16)})
                source = hp.over_armour.source
                self.assertIsNotNone(source)
                for _ in range(delay):
                    run.step({})
                self.assertEqual(npc_control.start(run.match, owner), bot.entity)
                for _ in range(350):
                    previous = hp.over_armour.amount
                    run.step({})
                    if hp.over_armour.source is None or hp.over_armour.amount == 0:
                        break
                self.assertTrue(bot.attack_input)
                self.assertGreater(previous, 0)
                if delay:
                    self.assertLess(previous, 100)
                    self.assertIsNone(hp.over_armour.source)
                    self.assertEqual(hp.total, 250)
                    next_totals = (250 - hit,)
                else:
                    self.assertEqual(hp.over_armour.source, source)
                    self.assertEqual(previous, 10)
                    self.assertEqual(hp.now[combat.ARMOUR_POOL], 45)
                    next_totals = (230, 215, 200, 180)
                for next_total in next_totals:
                    before = hp.total
                    for _ in range(300):
                        run.step({})
                        if hp.total < before:
                            break
                    self.assertTrue(bot.attack_input)
                    self.assertEqual(hp.total, next_total)
                    self.assertEqual(hp.over_armour.amount, 0)
                    self.assertEqual(hp.now[combat.ARMOUR_POOL], max(0, next_total - 200))
                    self.assertEqual(hp.now[combat.HEALTH_POOL], min(200, next_total))

    def test_airborne_npc_attacks_settle_before_or_after_overload_expiry(self):
        cases = (("Enforcer", 235, 14, 25, 7, .4, 1.4), ("Sniper", 180, 6, 1, 1, 100, 95))
        expiry = 312
        for name, aligned_delay, travel, launches, contacts, raw_damage, armor_loss in cases:
            key = next(key for key, row in self.catalog.items() if row.name == name)
            for impact_offset in (-1, 0, 1):
                with self.subTest(npc=name, impact_offset=impact_offset):
                    run, torb, bot, _ = self.arena(key, 18)
                    fight = run.match.combat
                    for viewer in run.match.players:
                        viewer.client.bot_stream = lambda *args: None
                    while run.frame < bot.records_from:
                        run.step({})
                    hp = fight.vitals[torb.body]
                    direction = self.body_aim(run, torb, bot)
                    run.step({torb: (*direction, 16)})
                    source = hp.over_armour.source
                    self.assertIsNotNone(source)
                    delay = aligned_delay + impact_offset
                    for _ in range(delay):
                        run.step({torb: (*direction, 0)})
                    self.assertEqual(npc_control.start(run.match, torb), bot.entity)

                    impact = expiry + impact_offset
                    launch = impact - travel
                    volley = set()
                    with patch.object(fight, "_damage", wraps=fight._damage) as hits:
                        for frame in range(delay + 1, impact + 8):
                            run.step({})
                            shots = {shot for shot in fight.rockets if shot.shooter is bot}
                            if frame == launch:
                                volley = shots
                                self.assertEqual(len(volley), launches)
                                self.assertTrue(all(shot.distance == 0 for shot in volley))
                            self.assertLessEqual(shots, volley)
                            if launch <= frame < impact:
                                self.assertTrue(shots)
                            self.assertTrue(bot.attack_input)
                            self.assertEqual(hits.call_count, contacts if frame >= impact else 0)
                            self.assertEqual(hp.over_armour.source, source if frame < expiry else None)
                            loss = armor_loss if frame >= impact else 0
                            temporary = 100 - loss if frame < expiry else 0
                            self.assertAlmostEqual(hp.over_armour.amount, temporary, places=5)
                            base_loss = loss if impact > expiry else 0
                            armor = max(0, 50 - base_loss)
                            health = min(200, 250 - base_loss)
                            self.assertAlmostEqual(hp.now[combat.ARMOUR_POOL], armor, places=5)
                            self.assertAlmostEqual(hp.now[combat.HEALTH_POOL], health, places=5)
                    for call in hits.call_args_list:
                        self.assertIs(call.args[0], bot)
                        self.assertIs(call.args[1].owner, torb)
                        self.assertAlmostEqual(call.args[2], raw_damage, places=5)

    def test_immediate_npc_hits_and_pin_cross_overload_expiry(self):
        cases = (("Trooper", 8, 290, 3, 20, 15), ("Heavy Assault", 8, 280, 4, 5, 2.5),
                 ("Assassin", 2, 311, 0, 25, 20))
        expiry, last_frame = 312, 315
        for name, distance, aligned_delay, cadence, raw_damage, armor_loss in cases:
            key = next(key for key, row in self.catalog.items() if row.name == name)
            for offset in (-1, 0, 1):
                with self.subTest(npc=name, impact_offset=offset):
                    run, torb, bot, _ = self.arena(key, distance)
                    fight = run.match.combat
                    for viewer in run.match.players:
                        viewer.client.bot_stream = lambda *args: None
                    while run.frame < bot.records_from:
                        run.step({})
                    hp = fight.vitals[torb.body]
                    direction = self.body_aim(run, torb, bot)
                    run.step({torb: (*direction, 16)})
                    source = hp.over_armour.source
                    self.assertIsNotNone(source)
                    delay = aligned_delay + offset
                    for _ in range(delay):
                        run.step({torb: (*direction, 0)})
                    self.assertEqual(hp.over_armour.source, source if delay < expiry else None)
                    self.assertEqual(npc_control.start(run.match, torb), bot.entity)

                    impact = expiry + offset
                    attack_frames = [impact]
                    if cadence and impact + cadence <= last_frame:
                        attack_frames.append(impact + cadence)
                    with patch.object(fight, "_damage", wraps=fight._damage) as hits:
                        for frame in range(delay + 1, last_frame + 1):
                            run.step({})
                            landed = [tick for tick in attack_frames if tick <= frame]
                            self.assertEqual(hits.call_count, len(landed))
                            self.assertTrue(bot.attack_input)
                            self.assertFalse(fight.rockets)
                            self.assertEqual(hp.over_armour.source, source if frame < expiry else None)
                            temporary = 100 - armor_loss * len(landed) if frame < expiry else 0
                            self.assertEqual(hp.over_armour.amount, temporary)
                            base_hits = sum(tick > expiry for tick in landed)
                            self.assertEqual(hp.now[combat.ARMOUR_POOL], 50 - base_hits * armor_loss)
                            self.assertEqual(hp.now[combat.HEALTH_POOL], 200)
                            pinned = name == "Assassin" and frame >= impact
                            self.assertEqual(fight.pins.get(bot.entity), torb.body if pinned else None)
                            self.assertEqual(fight.is_stunned(torb), pinned)
                    for call in hits.call_args_list:
                        self.assertIs(call.args[0], bot)
                        self.assertIs(call.args[1].owner, torb)
                        self.assertEqual(call.args[2], raw_damage)
                    npc_control.stop(run.match)
                    run.step({})
                    self.assertFalse(bot.attack_input)
                    self.assertFalse(fight.is_stunned(torb))

    def test_empty_secondary_reload_survives_overload_expiry_under_heavy_assault_fire(self):
        key = next(key for key, row in self.catalog.items() if row.name == "Heavy Assault")
        run, owner, bot, enemy_hp = self.arena(key)
        match = run.match
        fight = match.combat
        for viewer in match.players:
            viewer.client.bot_stream = lambda *args: None
        rifle = next(instance for instance in owner.body_script.component.instances.values()
                     if instance.graph.index == 0x1683)
        ammo = rifle.vars[53]
        before = enemy_hp.total
        for _ in range(8):
            run.step({owner: (16384, 0, 2)})
            for _ in range(58):
                run.step({})
        self.assertEqual(ammo.value(), 2)
        self.assertEqual(enemy_hp.total, before)

        run.step({owner: (0, 0, 16)})
        activated = run.frame
        hp = fight.vitals[owner.body]
        source = hp.over_armour.source
        self.assertIsNotNone(source)
        for _ in range(236):
            run.step({})
        self.assertEqual(npc_control.start(match, owner), bot.entity)
        for _ in range(13):
            run.step({})
        self.assertEqual(run.frame, activated + 249)
        yaw, pitch = self.body_aim(run, owner, bot)

        with patch.object(fight, "_shoot", wraps=fight._shoot) as shots:
            def advance_to(age):
                while run.frame < activated + age:
                    run.step({owner: (yaw, pitch, 2)})

            def launches():
                return [event.args[1].frame - activated for event in shots.call_args_list
                        if event.args[0] is owner and event.args[2].instance is rifle]

            run.step({owner: (16384, 0, 2)})
            self.assertEqual(ammo.value(), 0)
            advance_to(284)
            self.assertEqual(launches(), [250])
            self.assertFalse(rifle.state(8).active)
            self.assertTrue(bot.attack_input)
            self.assertLess(hp.over_armour.amount, 100)
            self.assertEqual(hp.over_armour.source, source)

            advance_to(285)
            reload = rifle.state(8)
            self.assertTrue(reload.active)
            counter = reload.counter
            self.assertAlmostEqual(rifle.vars[581].value(), 1.4)
            advance_to(311)
            self.assertEqual(hp.over_armour.source, source)
            advance_to(312)
            self.assertIsNone(hp.over_armour.source)
            self.assertTrue(reload.active)
            self.assertEqual(reload.counter, counter)
            self.assertAlmostEqual(rifle.vars[581].value(), 1.4)
            advance_to(328)
            self.assertEqual(ammo.value(), 0)
            self.assertLess(hp.now[combat.ARMOUR_POOL], 50)
            advance_to(329)
            self.assertEqual(ammo.value(), 18)
            self.assertTrue(reload.active)
            advance_to(372)
            self.assertEqual(launches(), [250])
            self.assertEqual(ammo.value(), 18)
            self.assertEqual(enemy_hp.total, before)
            self.assertTrue(bot.attack_input)
            advance_to(373)
            self.assertFalse(reload.active)
            self.assertEqual(launches(), [250, 373])
            self.assertEqual(ammo.value(), 16)
        for _ in range(18):
            run.step({})
        self.assertFalse(fight.is_dead(owner))
        self.assertEqual(before - enemy_hp.total, 125)

    def test_authored_npc_weapon_reach_after_manual_acquisition(self):
        for name in ("Trooper", "Sniper", "Heavy Assault", "Enforcer"):
            key = next(key for key, row in self.catalog.items() if row.name == name)
            for distance in (99, 101):
                with self.subTest(npc=name, distance=distance):
                    run, owner, bot, _ = self.arena(key, distance)
                    match = run.match
                    other = next(player for player in match.players if player is not owner)
                    x, y, z = owner.mover.position
                    other.mover.position = [x + 100, y, z]
                    other.mover.body = None
                    for viewer in match.players:
                        viewer.client.bot_stream = lambda *args: None
                    while run.frame < bot.records_from:
                        run.step({})
                    run.step({owner: (*self.body_aim(run, owner, bot), 0)})
                    if distance == 101:
                        with self.assertRaisesRegex(ValueError, "No living manual enemy"):
                            npc_control.start(match, owner)
                    fight = match.combat
                    hp = fight.vitals[owner.body]
                    # Allow real weapon input beyond the sandbox controller cap.
                    with (patch.object(npc_control, "AIM_RANGE", 110),
                          patch.object(fight, "_shoot", wraps=fight._shoot) as shots):
                        self.assertEqual(npc_control.start(match, owner), bot.entity)
                        for _ in range(180):
                            run.step({})
                            if shots.called:
                                break
                        self.assertTrue(shots.called)
                        state = shots.call_args_list[0].args[2]
                        self.assertEqual(state.evaluate(state.node.field("m_FEC435C6"), watch=False), 100)
                        for _ in range(95):
                            run.step({})
                            if hp.total < 250:
                                break
                    if distance == 99:
                        self.assertLess(hp.total, 250)
                    else:
                        self.assertEqual(hp.total, 250)

    def test_actual_enforcer_pellet_damage_falls_to_authored_floor(self):
        key = next(key for key, row in self.catalog.items() if row.name == "Enforcer")
        for distance, damage in ((6, 2), (12, 1.2), (18, .4)):
            with self.subTest(distance=distance):
                run, owner, bot, _ = self.arena(key, distance)
                for viewer in run.match.players:
                    viewer.client.bot_stream = lambda *args: None
                while run.frame < bot.records_from:
                    run.step({})
                fight = run.match.combat
                hp = fight.vitals[owner.body]
                self.assertEqual(npc_control.start(run.match, owner), bot.entity)
                with patch.object(fight, "_damage", wraps=fight._damage) as damage_events:
                    for _ in range(180):
                        run.step({})
                        if hp.total < 250:
                            break
                amounts = [call.args[2] for call in damage_events.call_args_list
                           if call.args[0] is bot and call.args[1].owner is owner]
                self.assertTrue(amounts)
                for amount in amounts:
                    self.assertAlmostEqual(amount, damage, delta=.12 if distance == 12 else 1e-6)
                self.assertAlmostEqual(250 - hp.total, sum(amounts) / 2)

    def test_all_five_manual_npc_corpses_survive_the_respawn_deadline(self):
        for key in self.catalog:
            with self.subTest(npc=key):
                run, owner, bot, vitals = self.arena(key)
                fight = run.match.combat
                target = next(t for t in fight._targets() if t.owner is bot)
                fight._damage(owner, target, vitals.total + 500.0, False, None, run.frame)
                for _ in range(220):
                    run.step({})
                self.assertTrue(bot.dead)
                self.assertTrue(bot.retain_corpse)
                self.assertIn(bot, run.match.bots)
                self.assertEqual(vitals.total, 0.0)
                self.assertFalse(any(actor is bot for _, actor in fight.respawns))
                self.assertIsNotNone(bot.body_script)
                self.assertTrue(bot.body_script.component.owner_dead)

    def test_actual_assassin_pin_input_damages_torbjorn_and_stop_releases_control(self):
        key = next(key for key, row in self.catalog.items() if row.name == "Assassin")
        run, owner, bot, _ = self.arena(key, 2.0)
        for viewer in run.match.players:
            viewer.client.bot_stream = lambda *args: None
        while run.frame < bot.records_from:
            run.step({})
        fight = run.match.combat
        hp = fight.vitals[owner.body]
        self.assertEqual(npc_control.start(run.match, owner), bot.entity)
        for _ in range(300):
            run.step({})
            if hp.total < 250:
                break
        self.assertLess(hp.total, 250)
        self.assertTrue(fight.is_stunned(owner))
        self.assertEqual(fight.pins[bot.entity], owner.body)
        self.assertEqual(bot.body_script.component.world.manual_target, owner.body)
        npc_control.stop(run.match)
        self.assertFalse(fight.is_stunned(owner))
        self.assertNotIn(bot.entity, fight.pins)
        before = hp.total
        for _ in range(80):
            run.step({})
        self.assertEqual(hp.total, before)
        self.assertEqual(npc_control.start(run.match, owner), bot.entity)
        for _ in range(300):
            run.step({})
            if hp.total < before:
                break
        self.assertLess(hp.total, before)

    def test_allied_flashbang_releases_live_assassin_pin_until_stun_expires(self):
        self.assert_attack_during_flashbang_pin_release(button=1, distance=2, damage=70, ammo=17)

    def test_quick_melee_hits_assassin_between_live_pin_and_reacquisition(self):
        self.assert_attack_during_flashbang_pin_release(button=0x800, distance=1.5, damage=30, ammo=18)

    def assert_attack_during_flashbang_pin_release(self, *, button, distance, damage, ammo):
        key = next(key for key, row in self.catalog.items() if row.name == "Assassin")
        run, torb, bot, bot_hp = self.arena(key, distance)
        match = run.match
        fight = match.combat
        cassidy = run.players[1]
        self.assertTrue(match.switch_hero(cassidy, heroes()[0x02E0000000000042]))
        cassidy.team = torb.team
        x, y, z = torb.mover.position
        cassidy.mover.position = [x + 3, y, z + 2]
        cassidy.mover.body = None
        for viewer in run.players:
            viewer.client.bot_stream = lambda *args: None
        while run.frame < bot.records_from:
            run.step({})
        torb_aim = self.body_aim(run, torb, bot)
        attack_aim = (0, 0) if button == 0x800 else torb_aim
        torb.body_script.ult_seconds = 0
        torb.body_script.set_ult_charge(0)
        run.step({torb: (*torb_aim, 16)})
        hp = fight.vitals[torb.body]
        source = hp.over_armour.source
        base = dict(hp.now)
        self.assertEqual(npc_control.start(match, torb), bot.entity)
        for _ in range(150):
            if fight.pins.get(bot.entity) == torb.body:
                break
            run.step({})
        self.assertTrue(fight.is_stunned(torb))
        self.assertEqual(hp.over_armour.amount, 80)
        rifle = next(i for i in torb.body_script.component.instances.values()
                     if i.graph.index == 0x1683)
        before = bot_hp.total
        run.step({torb: (*attack_aim, button)})
        self.assertFalse(torb.body_script.component.held)
        self.assertEqual(rifle.vars[53].value(), 18)
        self.assertEqual(bot_hp.total, before)
        self.assertEqual(torb.body_script.component.entity_var(342).value(), 0)
        run.step({cassidy: (*self.body_aim(run, cassidy, bot), 16)})
        for _ in range(55):
            if bot.entity in fight.stuns:
                break
            run.step({})
        self.assertEqual(fight.stuns.get(bot.entity), fight.tick + 50)
        self.assertEqual(bot_hp.total, before - 25)
        self.assertEqual(cassidy.stats.value(stats.HERO_DAMAGE), 25)
        self.assertNotIn(bot.entity, fight.pins)
        self.assertFalse(fight.is_stunned(torb))
        released_armour = hp.over_armour.amount
        end = fight.stuns[bot.entity]
        while run.frame < end - 1:
            pending = bot_hp.total == before - 25 and rifle.vars[53].value() == 18
            run.step({torb: (*attack_aim, button if pending else 0)})
            self.assertFalse(bot.attack_input)
            self.assertNotIn(bot.entity, fight.pins)
            self.assertEqual(hp.over_armour.amount, released_armour)
        self.assertEqual(rifle.vars[53].value(), ammo)
        self.assertEqual(bot_hp.total, before - 25 - damage)
        self.assertEqual(torb.stats.value(stats.HERO_DAMAGE), damage)
        self.assertEqual(torb.body_script.component.entity_var(342).value(), damage)
        run.step({})
        self.assertEqual(fight.tick, end)
        self.assertNotIn(bot.entity, fight.stuns)
        self.assertTrue(bot.attack_input)
        for _ in range(150):
            if fight.pins.get(bot.entity) == torb.body:
                break
            run.step({})
        self.assertEqual(fight.pins.get(bot.entity), torb.body)
        self.assertTrue(fight.is_stunned(torb))
        self.assertEqual(hp.over_armour.source, source)
        self.assertEqual(hp.over_armour.amount, released_armour - 20)
        self.assertEqual(hp.now, base)
        run.step({torb: (*attack_aim, button)})
        self.assertFalse(torb.body_script.component.held)
        self.assertEqual(bot_hp.total, before - 25 - damage)
        self.assertEqual(rifle.vars[53].value(), ammo)

    def test_live_assassin_pin_releases_and_reacquires_after_team_or_cover_change(self):
        for blocked in ("team", "wall"):
            with self.subTest(blocked=blocked):
                key = next(key for key, row in self.catalog.items() if row.name == "Assassin")
                run, owner, bot, _ = self.arena(key, 2.0, friendly=blocked == "team")
                match = run.match
                fight = match.combat
                for viewer in match.players:
                    viewer.client.bot_stream = lambda *args: None
                while run.frame < bot.records_from:
                    run.step({})
                hp = fight.vitals[owner.body]
                x, y, z = owner.mover.position
                clear = match.collision_world
                cover = World(
                    box(x - 25, x + 25, y - 1, y, z - 25, z + 25)
                    + wall(x - 3, z + 1, x + 3, z + 1, y0=y, y1=y + 4)
                )
                if blocked == "wall":
                    match.collision_world = cover
                with self.assertRaisesRegex(ValueError, "No living manual enemy"):
                    npc_control.start(match, owner)
                bot.team = 1 - owner.team
                match.collision_world = clear
                self.assertEqual(npc_control.start(match, owner), bot.entity)
                for _ in range(150):
                    run.step({})
                    if bot.entity in fight.pins:
                        break
                self.assertEqual(fight.pins.get(bot.entity), owner.body)
                self.assertLess(hp.total, 250)
                before = hp.total
                if blocked == "team":
                    bot.team = owner.team
                else:
                    match.collision_world = cover
                for _ in range(150):
                    run.step({})
                    self.assertNotIn(bot.entity, fight.pins)
                    self.assertFalse(fight.is_stunned(owner))
                    self.assertFalse(bot.attack_input)
                    self.assertEqual(hp.total, before)
                self.assertIsNone(bot.body_script.component.world.manual_target)
                bot.team = 1 - owner.team
                match.collision_world = clear
                for _ in range(150):
                    run.step({})
                    if bot.entity in fight.pins:
                        break
                self.assertEqual(fight.pins.get(bot.entity), owner.body)
                self.assertTrue(fight.is_stunned(owner))
                self.assertLess(hp.total, before)

    def test_live_assassin_pin_checks_contact_range_on_entry_and_during_pin(self):
        key = next(key for key, row in self.catalog.items() if row.name == "Assassin")
        run, owner, bot, _ = self.arena(key, 2.0)
        match = run.match
        fight = match.combat
        for viewer in match.players:
            viewer.client.bot_stream = lambda *args: None
        while run.frame < bot.records_from:
            run.step({})
        x, y, z = owner.mover.position
        eye = bot.combat_geometry[2] if bot.combat_geometry else combat.EYE
        height = fight._eye(owner)[1] - (y + eye)
        outside = math.sqrt((assassin.PIN_RANGE + .02) ** 2 - height ** 2)
        inside = math.sqrt((assassin.PIN_RANGE - .02) ** 2 - height ** 2)
        bot.position = (x, y, z + outside)
        hp = fight.vitals[owner.body]
        self.assertEqual(npc_control.start(match, owner), bot.entity)
        for _ in range(150):
            run.step({})
            self.assertFalse(bot.attack_input)
        self.assertNotIn(bot.entity, fight.pins)
        self.assertEqual(hp.total, 250)
        for attempt in range(2):
            with self.subTest(attempt=attempt):
                before = hp.total
                bot.position = (x, y, z + inside)
                for _ in range(150):
                    run.step({})
                    if bot.entity in fight.pins:
                        break
                self.assertEqual(fight.pins.get(bot.entity), owner.body)
                self.assertLess(hp.total, before)
                before = hp.total
                bot.position = (x, y, z + outside)
                for _ in range(20):
                    run.step({})
                    self.assertFalse(bot.attack_input)
                    self.assertFalse(fight.is_stunned(owner))
                    self.assertEqual(hp.total, before)
                self.assertNotIn(bot.entity, fight.pins)

    def test_lethal_assassin_pin_releases_control_before_torbjorn_respawns(self):
        key = next(key for key, row in self.catalog.items() if row.name == "Assassin")
        run, owner, bot, _ = self.arena(key, 2.0)
        match = run.match
        fight = match.combat
        for viewer in match.players:
            viewer.client.bot_stream = lambda *args: None
        while run.frame < bot.records_from:
            run.step({})
        body = owner.body
        hp = fight.vitals[body]
        self.assertEqual(npc_control.start(match, owner), bot.entity)
        for _ in range(150):
            run.step({})
            if fight.pins.get(bot.entity) == body:
                break
        self.assertEqual(fight.pins.get(bot.entity), body)
        self.assertLess(hp.total, 250)
        hp.damage(hp.total - 1)
        for _ in range(150):
            self.assertEqual(fight.pins.get(bot.entity), body)
            run.step({})
            if hp.dead:
                break
        self.assertTrue(hp.dead)
        self.assertEqual(hp.killer, bot.entity)
        self.assertNotIn(bot.entity, fight.pins)
        run.step({})
        self.assertFalse(bot.attack_input)
        self.assertIsNone(bot.body_script.component.world.manual_target)
        npc_control.stop(match)
        for _ in range(750):
            if owner.body != body:
                break
            run.step({})
        self.assertNotEqual(owner.body, body)
        self.assertFalse(fight.is_stunned(owner))
        self.assertNotIn(bot.entity, fight.pins)
        fresh = fight.vitals_of(next(target for target in fight._targets() if target.owner is owner))
        self.assertEqual(fresh.total, 250)

    def test_actual_assassin_pin_rebinds_after_same_tick_hero_switch(self):
        key = next(key for key, row in self.catalog.items() if row.name == "Assassin")
        run, owner, bot, _ = self.arena(key, 2.0)
        match = run.match
        fight = match.combat
        for viewer in match.players:
            viewer.client.bot_stream = lambda *args: None
        while run.frame < bot.records_from:
            run.step({})
        old_body = owner.body
        old_hp = fight.vitals[old_body]
        self.assertEqual(npc_control.start(match, owner), bot.entity)
        for _ in range(150):
            run.step({})
            if fight.pins.get(bot.entity) == old_body:
                break
        self.assertEqual(fight.pins.get(bot.entity), old_body)
        self.assertLess(old_hp.total, 250)
        before = old_hp.total
        position = list(owner.mover.position)
        torbjorn_hero = owner.hero
        self.assertTrue(match.switch_hero(owner, heroes()[SOLDIER]))
        self.assertNotIn(bot.entity, fight.pins)
        self.assertTrue(match.switch_hero(owner, torbjorn_hero))
        owner.mover.position = position
        owner.mover.body = None
        self.assertNotEqual(owner.body, old_body)
        self.assertFalse(fight.is_stunned(owner))
        fresh = fight.vitals_of(next(target for target in fight._targets() if target.owner is owner))
        self.assertEqual(fresh.total, 250)
        run.step({})
        self.assertIsNone(bot.body_script.component.world.manual_target)
        self.assertFalse(bot.attack_input)
        self.assertNotIn(old_body, fight.pins.values())
        self.assertEqual(fresh.total, 250)
        for _ in range(150):
            run.step({})
            if fight.pins.get(bot.entity) == owner.body:
                break
        self.assertEqual(fight.pins.get(bot.entity), owner.body)
        self.assertEqual(bot.body_script.component.world.manual_target, owner.body)
        self.assertLess(fresh.total, 250)
        self.assertEqual(old_hp.total, before)

    def test_assassin_pin_blocks_overload_after_cooldown_until_release(self):
        key = next(key for key, row in self.catalog.items() if row.name == "Assassin")
        for held in (False, True):
            with self.subTest(held=held):
                run, owner, bot, _ = self.arena(key, 1.5)
                match = run.match
                fight = match.combat
                for viewer in match.players:
                    viewer.client.bot_stream = lambda *args: None
                while run.frame < bot.records_from:
                    run.step({})
                hp = fight.vitals[owner.body]
                run.step({owner: (0, 0, 16)})
                source = hp.over_armour.source
                self.assertEqual(hp.over_armour.amount, 100)
                for _ in range(1000):
                    if hero_torbjorn.overload_cooldown_remaining(owner) <= 1.4:
                        break
                    run.step({})
                self.assertIsNone(hp.over_armour.source)
                self.assertEqual(npc_control.start(match, owner), bot.entity)
                for _ in range(85):
                    run.step({})
                    if fight.pins.get(bot.entity) == owner.body:
                        break
                self.assertEqual(fight.pins.get(bot.entity), owner.body)
                self.assertLess(hp.total, 250)
                self.assertGreater(hero_torbjorn.overload_cooldown_remaining(owner), 0)

                for _ in range(100):
                    run.step({owner: (0, 0, 16)})
                    self.assertTrue(fight.is_stunned(owner))
                    self.assertFalse(owner.body_script.component.held)
                    self.assertIsNone(hp.over_armour.source)
                    if hero_torbjorn.overload_cooldown_remaining(owner) == 0:
                        break
                self.assertEqual(hero_torbjorn.overload_cooldown_remaining(owner), 0)
                run.step({owner: (0, 0, 16 if held else 0)})
                self.assertEqual(fight.pins.get(bot.entity), owner.body)
                self.assertIsNone(hp.over_armour.source)

                npc_control.stop(match)
                self.assertFalse(fight.is_stunned(owner))
                before = hp.total
                if not held:
                    for _ in range(12):
                        run.step({})
                        self.assertIsNone(hp.over_armour.source)
                        self.assertEqual(hp.total, before)
                run.step({owner: (0, 0, 16)})
                self.assertEqual(hp.over_armour.amount, 100)
                self.assertEqual(hp.over_armour.source, (source[0], source[1] + 1))
                self.assertAlmostEqual(hp.total, before + 100)

    def test_assassin_pin_preserves_active_reload_but_blocks_fire_until_release(self):
        key = next(key for key, row in self.catalog.items() if row.name == "Assassin")
        for release in (40, 130):
            with self.subTest(release=release):
                run, owner, bot, bot_hp = self.arena(key, 1.5)
                match = run.match
                fight = match.combat
                for viewer in match.players:
                    viewer.client.bot_stream = lambda *args: None
                while run.frame < bot.records_from:
                    run.step({})
                rifle = next(instance for instance in owner.body_script.component.instances.values()
                             if instance.graph.index == 0x1683)
                ammo = rifle.vars[53]
                run.step({owner: (16384, 0, 1)})
                for _ in range(52):
                    run.step({})
                self.assertEqual(ammo.value(), 17)
                run.step({owner: (0, 0, 0x400)})
                started = run.frame
                reload = rifle.state(8)
                self.assertTrue(reload.active)
                counter = reload.counter
                owner_hp = fight.vitals[owner.body]
                bot_before = bot_hp.total
                self.assertEqual(npc_control.start(match, owner), bot.entity)
                run.step({})
                self.assertEqual(fight.pins.get(bot.entity), owner.body)
                self.assertLess(owner_hp.total, 250)
                yaw, pitch = self.body_aim(run, owner, bot)

                with patch.object(fight, "_shoot", wraps=fight._shoot) as shots:
                    while run.frame < started + release:
                        run.step({owner: (yaw, pitch, 1 | 2 | 0x400)})
                        age = run.frame - started
                        self.assertTrue(fight.is_stunned(owner))
                        self.assertFalse(owner.body_script.component.held)
                        self.assertEqual(reload.counter, counter)
                        self.assertEqual(reload.active, age < 125)
                        self.assertEqual(ammo.value(), 17 if age < 62 else 18)
                    self.assertFalse(any(event.args[0] is owner for event in shots.call_args_list))
                    self.assertEqual(bot_hp.total, bot_before)
                    npc_control.stop(match)
                    self.assertFalse(fight.is_stunned(owner))

                    first_fire = max(125, release + 1)
                    while run.frame < started + first_fire - 1:
                        run.step({owner: (yaw, pitch, 1)})
                    self.assertFalse(any(event.args[0] is owner for event in shots.call_args_list))
                    run.step({owner: (yaw, pitch, 1)})
                    launches = [event.args[1].frame - started for event in shots.call_args_list
                                if event.args[0] is owner and event.args[2].instance is rifle]
                    self.assertEqual(launches, [first_fire])
                    self.assertEqual(ammo.value(), 17)
                for _ in range(18):
                    run.step({})
                self.assertEqual(bot_before - bot_hp.total, 70)

    def test_assassin_pin_gates_weapon_switch_during_background_reload(self):
        from ow174.game.commands import Command

        key = next(key for key, row in self.catalog.items() if row.name == "Assassin")
        for release in (40, 130):
            with self.subTest(release=release):
                run, owner, bot, hp = self.arena(key, 1.5)
                match = run.match
                fight = match.combat
                for viewer in match.players:
                    viewer.client.bot_stream = lambda *args: None
                while run.frame < bot.records_from:
                    run.step({})
                rifle = next(instance for instance in owner.body_script.component.instances.values()
                             if instance.graph.index == 0x1683)
                ammo = rifle.vars[53]
                run.step({owner: (16384, 0, 1)})
                for _ in range(50):
                    run.step({})
                yaw, pitch = self.body_aim(run, owner, bot)
                run.frame += 1
                match.update(run.frame * combat.TICK, run.frame)
                owner.take_commands([Command(run.frame, yaw=yaw, pitch=pitch, action=2)])
                selected = run.frame
                background = rifle.state(25)
                self.assertTrue(background.active)
                self.assertEqual(ammo.value(), 17)
                counter = background.counter
                self.assertEqual(npc_control.start(match, owner), bot.entity)
                run.step({})
                self.assertEqual(fight.pins.get(bot.entity), owner.body)
                before = hp.total

                with patch.object(fight, "_shoot", wraps=fight._shoot) as shots:
                    while run.frame < selected + release:
                        run.frame += 1
                        match.update(run.frame * combat.TICK, run.frame)
                        owner.take_commands([Command(run.frame, yaw=yaw, pitch=pitch, buttons=1, action=1)])
                        self.assertTrue(fight.is_stunned(owner))
                        self.assertFalse(owner.body_script.component.held)
                        self.assertEqual(background.counter, counter)
                        age = run.frame - selected
                        self.assertEqual(ammo.value(), 17 if age < 125 else 18)
                    self.assertFalse(any(event.args[0] is owner for event in shots.call_args_list))
                    self.assertEqual(hp.total, before)
                    npc_control.stop(match)
                    self.assertFalse(fight.is_stunned(owner))
                    ammo_after_pin = 17 if release == 40 else 18
                    self.assertEqual(ammo.value(), ammo_after_pin)

                    run.frame += 1
                    match.update(run.frame * combat.TICK, run.frame)
                    owner.take_commands([Command(run.frame, yaw=yaw, pitch=pitch, action=1)])
                    for _ in range(130):
                        run.step({})
                    self.assertFalse(background.active)
                    self.assertEqual(ammo.value(), ammo_after_pin)
                    self.assertFalse(any(event.args[0] is owner for event in shots.call_args_list))

                    run.step({owner: (yaw, pitch, 1)})
                    for _ in range(20):
                        run.step({})
                    self.assertEqual(ammo.value(), ammo_after_pin - 1)
                    self.assertEqual(sum(event.args[0] is owner for event in shots.call_args_list), 1)
                self.assertEqual(before - hp.total, 70)

    def test_actual_assassin_pin_blocks_actions_and_preserves_torbjorn_buff_windows(self):
        from unittest import mock

        from ow174.game.commands import Command

        key = next(key for key, row in self.catalog.items() if row.name == "Assassin")
        for active in ("hammer", "overload", "ultimate"):
            with self.subTest(active=active):
                run, owner, bot, bot_hp = self.arena(key, 1.5)
                for viewer in run.match.players:
                    viewer.client.bot_stream = lambda *args: None
                while run.frame < bot.records_from:
                    run.step({})
                fight = run.match.combat
                hp = fight.vitals[owner.body]
                if active == "hammer":
                    run.frame += 1
                    owner.take_commands([Command(run.frame, action=2)])
                    for _ in range(45):
                        run.step({})
                    run.step({owner: (0, 0, 1)})
                elif active == "overload":
                    run.step({owner: (0, 0, 16)})
                    self.assertEqual(hp.over_armour.amount, 100)
                    source = hp.over_armour.source
                else:
                    owner.body_script.set_ult_charge(10000)
                    run.step({owner: (0, 0, 32)})
                    for _ in range(45):
                        run.step({})
                npc_control.start(run.match, owner)
                for _ in range(150):
                    run.step({})
                    if bot.entity in fight.pins:
                        break
                self.assertEqual(fight.pins.get(bot.entity), owner.body)
                position, before = tuple(owner.mover.position), bot_hp.total
                with mock.patch.object(fight, "_shoot", wraps=fight._shoot) as shots:
                    for _ in range(20):
                        run.frame += 1
                        run.match.update(run.frame * 0.016, run.frame)
                        owner.take_commands([Command(run.frame, forward=127,
                            buttons=1 | 2 | 8 | 16 | 32 | 0x800)])
                        self.assertEqual(tuple(owner.mover.position), position)
                        self.assertFalse(owner.body_script.component.held)
                    self.assertFalse(any(call.args[0] is owner for call in shots.call_args_list))
                    self.assertEqual(bot_hp.total, before)

                    if active == "overload":
                        self.assertEqual(hp.over_armour.source, source)
                        self.assertGreater(hp.over_armour.amount, 0)
                        self.assertLess(hp.over_armour.amount, 100)
                    npc_control.stop(run.match)
                    self.assertFalse(fight.is_stunned(owner))
                    for _ in range(10):
                        run.step({})
                    for _ in range(90):
                        run.step({owner: (0, 0, 1)})
                    if active == "ultimate":
                        self.assertTrue(any(call.args[0] is owner and
                            call.args[2].instance.graph.index == 0x16DA
                            for call in shots.call_args_list))
                    else:
                        self.assertLess(bot_hp.total, before)
