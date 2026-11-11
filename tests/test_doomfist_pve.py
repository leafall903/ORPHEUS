"""Actual five-body inputs; opt in to a read-only externally prepared NPC bundle.

This fixture does not claim native AI or original-client presentation.
"""

import os
import unittest
from dataclasses import replace
from itertools import pairwise
from pathlib import Path
from unittest.mock import patch

from test_doomfist_combat import DoomfistFixture
from test_doomfist_meteor import charge_by_neutral_commands, flat_meteor_damage
from test_game_combat import aim
from test_game_mover import box

from ow174.game import bots, combat, defenses, mover, npc, npc_control, npc_melee, sombra_hack, stats
from ow174.game.collision import World
from ow174.game.commands import FIRE, Command
from ow174.game.content import heroes
from ow174.game.script import graph, nodes

NPC_DATA = os.environ.get("ORPHEUS_NPC_DATA_DIR")


@unittest.skipUnless(NPC_DATA, "requires explicit external ORPHEUS_NPC_DATA_DIR")
class ActualNpcDamageTests(DoomfistFixture, unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.original_graphs = graph.GRAPHS_PATH
        folder = Path(NPC_DATA)
        graph.GRAPHS_PATH = folder / "game_graphs_174.json.gz"
        graph._data.cache_clear()
        graph._records.cache_clear()
        graph.graph.cache_clear()
        cls.catalog = npc.load_catalog(folder / "pve_npcs_174.json")
        if {d.name for d in cls.catalog.values()} != {
            "Trooper", "Enforcer", "Sniper", "Assassin", "Heavy Assault",
        }:
            raise AssertionError("fixture must exercise all five requested real definitions")
        if os.environ.get('ORPHEUS_REQUIRE_NPC_MOVERS') == '1':
            missing = [d.name for d in cls.catalog.values() if d.body not in mover._movers()]
            if missing:
                raise AssertionError(f'authored NPC mover inputs required; missing: {missing}')

    @classmethod
    def tearDownClass(cls):
        graph.GRAPHS_PATH = cls.original_graphs
        graph._data.cache_clear()
        graph._records.cache_clear()
        graph.graph.cache_clear()

    def create_enemy(self, definition, *, team=1, distance=1.5):
        self.prepare(distance)
        self.match.game_map = self.match.game_map.__class__(**{
            **self.match.game_map.__dict__, "free_for_all": False,
        })
        self.a.team = self.b.team = 0
        for player in self.match.players:
            player.client.queue_bot_death = lambda entity, event: None
            player.client.queue_bot_death_update = lambda entity, event: None
        x, y, z = self.a.mover.position
        bot = npc.spawn(self.match, definition, (x, y, z + distance), team=team)
        self.assertIsNotNone(bot.body_script)
        self.assertTrue(bot.retain_corpse)
        return bot, self.fight.vitals[bot.entity]

    def test_enemy_wraith_form_allows_hand_cannon_to_reach_each_npc(self):
        for definition in self.catalog.values():
            with self.subTest(enemy=definition.name):
                bot, vitals = self.create_enemy(definition, distance=4)
                before = vitals.total
                self.b.team = 1
                self.match.switch_hero(self.b, heroes()[defenses.REAPER])
                x, y, z = self.a.mover.position
                self.place_player(self.b, (x, y, z + 2))
                reaper = self.vitals()
                self.run.step({self.b: (0, 0, 8)})
                self.assertTrue(self.fight.protected(self.b.body))
                yaw, pitch = aim(self.fight._eye(self.a), (x, y + 1, z + 4))
                self.run.step({self.a: (yaw, pitch, 1)})
                self.settle(15)
                self.assertEqual(reaper.total, 250)
                self.assertLess(vitals.total, before)
                first = vitals.total
                self.assertTrue(all(hit['+0x78']['+0x0']['+0x0'] == bot.entity
                                    for hit in self.a.client.messages(combat.HIT)))
                for _ in range(240):
                    if not self.fight.protected(self.b.body):
                        break
                    self.run.step({})
                self.assertFalse(self.fight.protected(self.b.body))
                self.run.step({self.a: (yaw, pitch, 1)})
                self.settle(15)
                self.assertEqual(reaper.total, 184)
                self.assertEqual(vitals.total, first)

    @unittest.skipUnless(os.environ.get('ORPHEUS_REINHARDT_COLLISION'), 'requires audited barrier geometry')
    def test_enemy_barrier_protects_each_npc_until_released(self):
        for definition in self.catalog.values():
            with self.subTest(enemy=definition.name):
                bot, vitals = self.create_enemy(definition, distance=4)
                before = vitals.total
                self.b.team = 1
                self.match.switch_hero(self.b, heroes()[0x02E0000000000007])
                x, y, z = self.a.mover.position
                self.place_player(self.b, (x + 1.4, y, z + 2.5))
                hold = {self.b: (-32768, 0, 2)}
                host = self.fight.hand_cannon_barriers
                for _ in range(400):
                    if host.sync(self.b).active:
                        break
                    self.run.step(hold)
                pool = host.sync(self.b)
                self.assertTrue(pool.active)
                self.assertEqual(pool.current, 1600)
                yaw, pitch = aim(self.fight._eye(self.a), (x, y + 1, z + 4))
                self.run.step({self.a: (yaw, pitch, 1), **hold})
                self.assertEqual(len(self.fight.rockets), 11)
                for _ in range(15):
                    self.run.step(hold)
                self.assertEqual(vitals.total, before)
                self.assertEqual(pool.current, 1534)
                self.assertFalse(self.a.client.messages(combat.HIT))
                self.settle(75)
                self.assertFalse(host.sync(self.b).active)
                self.run.step({self.a: (yaw, pitch, 1)})
                self.settle(15)
                self.assertLess(vitals.total, before)
                self.assertTrue(all(hit['+0x78']['+0x0']['+0x0'] == bot.entity
                                    for hit in self.a.client.messages(combat.HIT)))

    def test_enemy_deflect_and_grasp_intercept_hand_cannon_before_each_npc(self):
        for definition in self.catalog.values():
            for hero, button in ((defenses.GENJI, 16), (defenses.SIGMA, 8)):
                with self.subTest(enemy=definition.name, defense=hero):
                    bot, vitals = self.create_enemy(definition, distance=4)
                    before = vitals.total
                    own = self.own_vitals()
                    self.b.team = 1
                    self.match.switch_hero(self.b, heroes()[hero])
                    x, y, z = self.a.mover.position
                    self.place_player(self.b, (x, y, z + 2))
                    self.run.step({self.b: (-32768, 0, button)})
                    defense = defenses.deflect(self.b) if hero == defenses.GENJI else defenses.grasp(self.b)
                    self.assertIsNotNone(defense)
                    yaw, pitch = aim(self.fight._eye(self.a), (x, y + 1, z + 4))
                    self.run.step({self.a: (yaw, pitch, 1), self.b: (-32768, 0, 0)})
                    self.assertEqual(len(self.fight.rockets), 11)
                    for _ in range(15):
                        self.run.step({self.b: (-32768, 0, 0)})
                    self.assertEqual(vitals.total, before)
                    self.assertFalse(self.a.client.messages(combat.HIT))
                    if hero == defenses.GENJI:
                        self.assertEqual(own.total, 184)
                        self.assertEqual(self.b.stats.value(stats.HERO_DAMAGE), 66)
                    else:
                        self.assertEqual(own.total, 250)
                        self.assertEqual(defense.vars[21312].value(), 66)
                    self.place_player(self.b, (x + 15, y, z))
                    self.settle(75)
                    self.run.step({self.a: (yaw, pitch, 1)})
                    self.settle(15)
                    self.assertLess(vitals.total, before)
                    self.assertTrue(all(hit['+0x78']['+0x0']['+0x0'] == bot.entity
                                        for hit in self.a.client.messages(combat.HIT)))

    def test_same_tick_hand_cannon_precedes_unreleased_npc_attacks(self):
        self.check_hand_cannon_direct_npc_attack_order(('Trooper', 'Heavy Assault', 'Assassin'))

    def test_same_tick_hand_cannon_precedes_unreleased_npc_melee_and_charge(self):
        self.check_hand_cannon_direct_npc_attack_order(('Trooper', 'Heavy Assault'), melee=True)
        self.check_hand_cannon_direct_npc_attack_order(('Heavy Assault',), charge=True)

    def check_hand_cannon_direct_npc_attack_order(self, enemies, *, melee=False, charge=False):
        def fire_at(bot):
            x, y, z = bot.position
            yaw, pitch = aim(self.fight._eye(self.a), (x, y + 1, z))
            self.run.step({self.a: (yaw, pitch, 1)})

        for name in enemies:
            with self.subTest(enemy=name):
                definition = next(d for d in self.catalog.values() if d.name == name)
                pools = replace(definition, health=1, armour=0, shields=0)
                offsets = []
                warmup = 20
                for incoming in (False, True):
                    bot, own = self.prepare_incoming(pools, distance=2)
                    enemy = self.fight.vitals[bot.entity]
                    target = next(t for t in self.fight._targets() if t.owner is self.a)
                    self.fight._damage(self.b, target, 249, False, None, self.run.frame)
                    self.settle(warmup)
                    start = self.run.frame
                    if incoming:
                        self.assertEqual(npc_control.start(self.match, self.a, melee=melee, charge=charge), bot.entity)
                    else:
                        fire_at(bot)
                    victim = own if incoming else enemy
                    for _ in range(100):
                        if victim.dead:
                            break
                        self.run.step({})
                    self.assertTrue(victim.dead)
                    offsets.append(victim.death_tick - start)

                flight, attack = offsets
                for arrival_offset in (-1, 0, 1):
                    with self.subTest(pellet_arrival_relative_to_attack=arrival_offset):
                        bot, own = self.prepare_incoming(pools, distance=2)
                        enemy = self.fight.vitals[bot.entity]
                        target = next(t for t in self.fight._targets() if t.owner is self.a)
                        self.fight._damage(self.b, target, 249, False, None, self.run.frame)
                        start = self.run.frame
                        begin = start + warmup
                        attack_tick = begin + attack
                        pellet_tick = attack_tick + arrival_offset
                        fire = pellet_tick - flight
                        self.assertGreaterEqual(fire, start)
                        self.assertLess(fire, attack_tick)
                        while self.run.frame < max(attack_tick, pellet_tick):
                            if self.run.frame == begin and not enemy.dead:
                                self.assertEqual(npc_control.start(self.match, self.a, melee=melee, charge=charge),
                                                 bot.entity)
                            if self.run.frame == fire:
                                fire_at(bot)
                                self.assertTrue(any(p.shooter is self.a for p in self.fight.rockets))
                            else:
                                self.run.step({})
                        self.assertTrue(enemy.dead)
                        self.assertEqual(enemy.death_tick, pellet_tick)
                        self.assertEqual(enemy.killer, self.a.body)
                        self.assertEqual(own.dead, arrival_offset > 0)
                        if own.dead:
                            self.assertEqual(own.death_tick, attack_tick)
                            self.assertEqual(own.killer, bot.entity)
                        else:
                            self.assertEqual(own.total, 1)
                        self.assertFalse(self.fight.pins)
                        self.settle(60)
                        self.assertEqual(own.total, 0 if arrival_offset > 0 else 1)
                        self.assertEqual(self.a.stats.value(stats.DEATHS), int(arrival_offset > 0))
                        self.assertEqual(self.a.stats.value(stats.ELIMINATIONS), 1)
                        self.assertIn(bot, self.match.bots)

    def test_same_tick_projectile_trade_with_sniper_and_enforcer(self):
        for name in ('Sniper', 'Enforcer'):
            with self.subTest(enemy=name):
                definition = next(d for d in self.catalog.values() if d.name == name)

                def scene(definition):
                    bot, own = self.prepare_incoming(
                        replace(definition, health=1, armour=0, shields=0), distance=12,
                    )
                    target = next(t for t in self.fight._targets() if t.owner is self.a)
                    self.fight._damage(self.b, target, 249, False, None, self.run.frame)
                    return bot, own, self.fight.vitals[bot.entity]

                def until_dead(pool):
                    for _ in range(180):
                        if pool.dead:
                            break
                        self.run.step({})
                    self.assertTrue(pool.dead)

                def fire_at(bot):
                    x, y, z = bot.position
                    yaw, pitch = aim(self.fight._eye(self.a), (x, y + 1, z))
                    self.run.step({self.a: (yaw, pitch, 1)})

                bot, _, enemy = scene(definition)
                start = self.run.frame
                fire_at(bot)
                until_dead(enemy)
                flight = enemy.death_tick - start
                bot, own, _ = scene(definition)
                start = self.run.frame
                npc_control.start(self.match, self.a)
                until_dead(own)
                incoming = own.death_tick - start
                self.assertGreater(incoming, flight)

                bot, own, enemy = scene(definition)
                body = self.a.body
                npc_control.start(self.match, self.a)
                self.settle(incoming - flight)
                fire_at(bot)
                until_dead(own)
                self.assertTrue(enemy.dead)
                self.assertEqual(own.death_tick, enemy.death_tick)
                self.assertEqual(own.killer, bot.entity)
                self.assertEqual(enemy.killer, body)
                self.assertEqual(self.a.stats.value(stats.DEATHS), 1)
                self.assertEqual(self.a.stats.value(stats.ELIMINATIONS), 1)
                self.assertIn(bot, self.match.bots)
                self.assertFalse(any(who is bot for _, who in self.fight.respawns))

    def test_retained_npc_corpse_does_not_intercept_living_target(self):
        for definition in self.catalog.values():
            with self.subTest(enemy=definition.name):
                corpse, dead = self.create_enemy(replace(definition, health=20, armour=0, shields=0))
                x, y, z = self.a.mover.position
                self.place_player(self.b, (x + 15, y, z))
                yaw, pitch = aim(self.fight._eye(self.a), (x, y + 1, z + 1.5))
                self.run.step({self.a: (yaw, pitch, 0x800)})
                self.assertTrue(corpse.dead)
                self.assertEqual(dead.killer, self.a.body)
                death_tick = dead.death_tick
                corpse_position = tuple(corpse.position)
                living = npc.spawn(self.match, definition, (x, y, z + 3), team=1)
                vitals = self.fight.vitals[living.entity]
                before = vitals.total
                yaw, pitch = aim(self.fight._eye(self.a), (x, y + 1.4, z + 3))
                self.settle(75)  # allow quick melee recovery before firing
                self.run.step({self.a: (yaw, pitch, 0)})
                self.assertIs(npc_control.selected(self.match, self.a), living)
                self.run.step({self.a: (yaw, pitch, 1)})
                self.assertEqual(len(self.fight.rockets), 11)
                target = next(t for t in self.fight._targets() if t.owner is living)
                a, b = target.segment()
                expected = sum(combat.ray_capsule(p.position, p.direction, a, b, target.radius) is not None
                               for p in self.fight.rockets)
                self.assertGreater(expected, 0)
                self.settle(45)
                self.assertEqual(before - vitals.total, 6 * expected)
                self.assertEqual(dead.total, 0)
                self.assertEqual(dead.death_tick, death_tick)
                self.assertEqual(tuple(corpse.position), corpse_position)
                self.assertIn(corpse, self.match.bots)
                self.assertFalse(any(who is corpse for _, who in self.fight.respawns))

    def test_hand_cannon_high_contacts_use_null_critical_fallback_for_all_five_npcs(self):
        for definition in self.catalog.values():
            with self.subTest(enemy=definition.name):
                bot, vitals = self.create_enemy(definition)
                before = vitals.total
                target = next(t for t in self.fight._targets() if t.owner is bot)
                weapon = next(i for i in self.a.body_script.component.instances.values()
                              if i.graph.index == 0xD0A)
                self.assertIsNone(weapon.state(2).node.field('m_6396149F.m_A872C70E'))
                x, y, z = target.feet
                yaw, pitch = aim(self.fight._eye(self.a), (x, y + target.eye - .08, z))
                self.run.step({self.a: (yaw, pitch, 1)})
                pellets = self.fight.rockets
                self.assertEqual(len(pellets), 11)
                self.assertTrue(all(pellet.direct.critical == -1 for pellet in pellets))
                a, b = target.segment()
                centre = pellets[0]
                distance = combat.ray_capsule(centre.position, centre.direction, a, b, target.radius)
                self.assertIsNotNone(distance)
                height = centre.position[1] + distance * centre.direction[1]
                self.assertGreaterEqual(height, y + target.eye - combat.HEAD_BELOW)
                expected = sum(combat.ray_capsule(p.position, p.direction, a, b, target.radius) is not None
                               for p in pellets)
                self.settle(12)
                notices = self.a.client.messages(combat.HIT)
                self.assertEqual(len(notices), expected)
                self.assertEqual(before - vitals.total, 6 * expected)
                self.assertTrue(all(notice['+0x78']['+0x8'] & combat.CRITICAL == 0 for notice in notices))

    def test_hand_cannon_owner_death_keeps_only_emitted_pellets_against_all_five_npcs(self):
        self.check_hand_cannon_owner_death(lethal_target=False)

    def test_hand_cannon_posthumous_kill_credits_owner_against_all_five_npcs(self):
        self.check_hand_cannon_owner_death(lethal_target=True)

    def check_hand_cannon_owner_death(self, *, lethal_target):
        for definition in self.catalog.values():
            with self.subTest(enemy=definition.name):
                target_definition = (replace(definition, health=1, armour=0, shields=0)
                                     if lethal_target else definition)
                bot, vitals = self.create_enemy(target_definition, distance=20)
                before = vitals.total
                self.b.team = 1
                self.match.switch_hero(self.b, heroes()[0x02E0000000000042])
                owner = next(t for t in self.fight._targets() if t.owner is self.a)
                own = self.fight.vitals_of(owner)
                self.fight._damage(self.b, owner, 235, False, None, self.run.frame)
                x, y, z = self.a.mover.position
                self.place_player(self.b, (x + 1.5, y, z))
                yaw, pitch = aim(self.fight._eye(self.b), self.fight._eye(self.a))
                bx, by, bz = bot.position
                fire_yaw, fire_pitch = aim(self.fight._eye(self.a), (bx, by + 1, bz))
                self.run.step({self.a: (fire_yaw, fire_pitch, 1), self.b: (yaw, pitch, 16)})
                pellets = tuple(p for p in self.fight.rockets if p.shooter is self.a)
                self.assertEqual(len(pellets), 11)
                target = next(t for t in self.fight._targets() if t.owner is bot)
                start, end = target.segment()
                contacts = [distance for pellet in pellets if (distance := combat.ray_capsule(
                    pellet.position, pellet.direction, start, end, target.radius,
                )) is not None]
                self.assertTrue(contacts)
                for _ in range(20):
                    if self.fight.is_dead(self.a):
                        break
                    self.run.step({})
                self.assertTrue(self.fight.is_dead(self.a))
                self.assertEqual(own.killer, self.b.body)
                self.assertEqual(vitals.total, before)
                charge_at_death = self.charge_checkpoint()
                emitted = {id(pellet) for pellet in pellets}
                for _ in range(60):
                    self.run.step({self.a: (fire_yaw, fire_pitch, 1)})
                    self.assertTrue({id(p) for p in self.fight.rockets if p.shooter is self.a} <= emitted)
                expected = sum(6 * (1 - .7 * max(0, min(1, (d - 15) / 15))) for d in contacts)
                self.assertAlmostEqual(before - vitals.total, min(before, expected), places=5)
                self.assertEqual(len(self.a.client.messages(combat.HIT)), 1 if lethal_target else len(contacts))
                if lethal_target:
                    self.assertTrue(vitals.dead)
                    self.assertEqual(vitals.killer, self.a.body)
                    self.assertGreater(vitals.death_tick, own.death_tick)
                    self.assertEqual(self.a.stats.value(stats.ELIMINATIONS), 1)
                    self.assertIn(bot, self.match.bots)
                    self.assertFalse(any(who is bot for _, who in self.fight.respawns))
                self.assert_damage_charge(charge_at_death, 0)

    def test_hand_cannon_lifetime_brackets_five_hundred_metres_for_all_five_npcs(self):
        for definition in self.catalog.values():
            for distance in (490, 510):
                with self.subTest(enemy=definition.name, distance=distance):
                    bot, vitals = self.create_enemy(definition, distance=distance)
                    x, y, z = self.a.mover.position
                    self.place_player(self.b, (x + 15, y, z))
                    before = vitals.total
                    charge = self.charge_checkpoint()
                    weapon = next(i for i in self.a.body_script.component.instances.values()
                                  if i.graph.index == 0xD0A)
                    ammo = weapon.vars[53]
                    self.assertEqual(ammo.value(), 4)
                    bx, by, bz = bot.position
                    yaw, pitch = aim(self.fight._eye(self.a), (bx, by + 1, bz))
                    self.run.step({self.a: (yaw, pitch, 1)})
                    self.assertEqual(ammo.value(), 3)
                    self.assertEqual(len(self.fight.rockets), 11)
                    # Align the stationary fixture to the emitted ray so
                    # centre-pellet jitter cannot mask the lifetime boundary.
                    centre = self.fight.rockets[0]
                    px, py, pz = centre.position
                    vx, vy, vz = centre.direction
                    bot.position = (px + distance * vx, py + distance * vy - 1,
                                    pz + distance * vz)
                    self.settle(410)
                    hit = distance < 500
                    self.assertAlmostEqual(before - vitals.total, 1.8 if hit else 0, places=5)
                    self.assert_damage_charge(charge, 1.8 if hit else 0)
                    self.assertEqual(len(self.a.client.messages(combat.HIT)), int(hit))
                    self.assertFalse(self.fight.rockets)
                    self.assertEqual(ammo.value(), 4)

    def test_hand_cannon_natural_volley_falloff_against_all_five_npcs(self):
        for definition in self.catalog.values():
            for distance in (22.5, 35):
                with self.subTest(enemy=definition.name, distance=distance):
                    bot, vitals = self.create_enemy(definition, distance=distance)
                    before = vitals.total
                    x, y, z = bot.position
                    self.place_player(self.b, (x + 15, y, z - distance))
                    target = next(t for t in self.fight._targets() if t.owner is bot)
                    yaw, pitch = aim(self.fight._eye(self.a), (x, y + 1, z))
                    charge = self.charge_checkpoint()
                    self.run.step({self.a: (yaw, pitch, 1)})
                    self.assertEqual(len(self.fight.rockets), 11)
                    start, end = target.segment()
                    hits = [d for pellet in self.fight.rockets if
                            (d := combat.ray_capsule(pellet.position, pellet.direction,
                                                    start, end, target.radius)) is not None]
                    self.assertTrue(hits)
                    expected = sum(6 * (1 - .7 * max(0, min(1, (d - 15) / 15))) for d in hits)
                    self.settle(70)
                    self.assertAlmostEqual(before - vitals.total, expected, places=5)
                    self.assert_damage_charge(charge, expected)
                    self.assertEqual(len(self.a.client.messages(combat.HIT)), len(hits))

    def test_hand_cannon_pellet_armour_and_lethal_corpses_against_all_five_npcs(self):
        for definition in self.catalog.values():
            for lethal in (False, True):
                with self.subTest(enemy=definition.name, lethal=lethal):
                    pools = replace(definition, health=20, armour=0, shields=0) if lethal else replace(
                        definition, armour=100, shields=0,
                    )
                    bot, vitals = self.create_enemy(pools)
                    charge = self.charge_checkpoint()
                    self.fire()
                    self.settle(45)
                    self.assert_damage_charge(charge, 20 if lethal else 33)
                    self.assertEqual(len(self.a.client.messages(combat.HIT)), 4 if lethal else 11)
                    if lethal:
                        self.assertTrue(bot.dead)
                        self.assertEqual(vitals.total, 0)
                        self.assertEqual(vitals.killer, self.a.body)
                        self.assertIn(bot, self.match.bots)
                        self.assertFalse(any(who is bot for _, who in self.fight.respawns))
                    else:
                        self.assertEqual(vitals.now[combat.ARMOUR_POOL], 67)
                        self.assertEqual(vitals.now[combat.HEALTH_POOL], definition.health)

    def test_hand_cannon_empty_magazine_and_recovery_against_all_five_npcs(self):
        for definition in self.catalog.values():
            with self.subTest(enemy=definition.name):
                _, vitals = self.create_enemy(replace(definition, health=1000, armour=0, shields=0))
                weapon = next(i for i in self.a.body_script.component.instances.values()
                              if i.graph.index == 0xD0A)
                ammo = weapon.vars[53]
                self.assertEqual(ammo.value(), 4)
                fired = []
                for _ in range(100):
                    previous = ammo.value()
                    self.fire()
                    if ammo.value() < previous:
                        self.assertEqual(ammo.value(), previous - 1)
                        fired.append(self.run.frame)
                    if ammo.value() == 0:
                        break
                self.assertEqual(len(fired), 4)
                for first, second in pairwise(fired):
                    elapsed = (second - first) * combat.TICK
                    self.assertAlmostEqual(elapsed, 1 / 3, delta=combat.TICK)
                self.assertEqual(ammo.value(), 0)
                self.run.step({})
                self.fire()
                self.settle(8)
                self.assertEqual(ammo.value(), 0)
                self.assertEqual(vitals.total, 1000 - 4 * 66)
                self.assertEqual(len(self.a.client.messages(combat.HIT)), 44)
                for _ in range(100):
                    if ammo.value() > 0:
                        break
                    self.run.step({})
                self.assertEqual(ammo.value(), 1)
                self.assertEqual(vitals.total, 1000 - 4 * 66)
                self.fire()
                self.settle(8)
                self.assertEqual(ammo.value(), 0)
                self.assertEqual(vitals.total, 1000 - 5 * 66)
                self.assertEqual(len(self.a.client.messages(combat.HIT)), 55)
                self.settle(240)
                self.assertEqual(ammo.value(), 4)
                self.assertEqual(len(self.a.client.messages(combat.HIT)), 55)

    def test_hand_cannon_spends_npc_shields_then_spills_into_health(self):
        for definition in self.catalog.values():
            with self.subTest(enemy=definition.name):
                pools = replace(definition, health=200, armour=0, shields=30)
                bot, vitals = self.create_enemy(pools)
                self.assertEqual(vitals.now[combat.SHIELDS_POOL], 30)
                charge = self.charge_checkpoint()
                self.fire()
                self.settle(8)
                self.assertEqual(vitals.now[combat.SHIELDS_POOL], 0)
                self.assertEqual(vitals.now[combat.HEALTH_POOL], 164)
                self.assert_damage_charge(charge, 66)
                hits = self.a.client.messages(combat.HIT)
                self.assertEqual(len(hits), 11)
                self.assertTrue(all(hit["+0x78"]["+0x0"]["+0x0"] == bot.entity for hit in hits))

    def test_hand_cannon_actual_damage_to_each_of_five_bodies(self):
        for definition in self.catalog.values():
            with self.subTest(enemy=definition.name):
                bot, vitals = self.create_enemy(definition)
                before = vitals.total
                charge = self.a.body_script.component.entity_var(342)
                charge_before = charge.value()
                self.fire()
                self.settle(8)
                self.assertAlmostEqual(before - vitals.total, 66)
                self.assertAlmostEqual(charge.value() - charge_before, 66 + 9 * .448, places=3)
                self.assertTrue(all(hit["+0x78"]["+0x0"]["+0x0"] == bot.entity
                                    for hit in self.a.client.messages(combat.HIT)))

    def test_authored_ring_scatter_grazes_each_npc_beyond_center_ray(self):
        for definition in self.catalog.values():
            for clearance in (.12, .65):
                with self.subTest(enemy=definition.name, clearance=clearance):
                    bot, _ = self.create_enemy(definition, distance=10)
                    hits = self.assert_scatter_graze(bot, clearance)
                    if clearance < .2:
                        self.assertGreater(hits, 0)
                        self.assertLess(hits, 11)
                    else:
                        self.assertEqual(hits, 0)
                    self.assertTrue(all(hit["+0x78"]["+0x0"]["+0x0"] == bot.entity
                                        for hit in self.a.client.messages(combat.HIT)))

    def install_wall(self, *, present=True):
        x, y, z = self.a.mover.position
        triangles = box(x - 30, x + 30, y - 1, y, z - 30, z + 30)
        if present:
            triangles += box(x - 5, x + 5, y, y + 6, z + 1.2, z + 1.4)
        world = World(triangles)
        self.match.collision_world = world
        for player in self.match.players:
            player.mover.use(world=world)

    def own_vitals(self):
        return self.fight.vitals_of(next(t for t in self.fight._targets() if t.owner is self.a))

    def test_heavy_damage_fills_meteor_before_neutral_deadline_then_normal_cast_spends_it(self):
        definition = next(d for d in self.catalog.values() if d.name == 'Heavy Assault')
        bot, vitals = self.create_enemy(definition)
        script = self.a.body_script
        charge = script.component.entity_var(342)
        before = self.run.frame
        self.run.step({self.a: (0, 0, 32)})
        self.assertNotIn(self.a.body, self.fight.meteors.casts)
        rejected_near_threshold = False
        for _ in range(3600):
            previous_charge, previous_health = charge.value(), vitals.total
            yaw, pitch = aim(self.fight._eye(self.a), (bot.position[0], bot.position[1] + 1, bot.position[2]))
            if not rejected_near_threshold and 1500 <= previous_charge < 1600:
                self.run.step({self.a: (yaw, pitch, 32)})
                self.assertNotIn(self.a.body, self.fight.meteors.casts)
                self.assertLess(charge.value(), 1680)
                rejected_near_threshold = True
            else:
                self.run.step({self.a: (yaw, pitch, 1)})
            if charge.value() == 1680:
                break
        self.assertTrue(rejected_near_threshold)
        self.assertEqual(charge.value(), 1680)
        self.assertLess(previous_charge + .448, 1680)
        self.assertGreater(previous_health - vitals.total, 0)
        self.assertNotIn(self.a.body, self.fight.meteors.casts)
        self.assertLess(self.run.frame - before, 3700)
        self.assertGreater(vitals.total, 300)
        self.assertLess(vitals.total, 4000)
        self.settle(12)
        self.assertEqual(charge.value(), 1680)
        self.run.step({self.a: (0, 0, 32)})
        self.assertTrue(self.fight.meteors.protected(self.a.body))
        self.settle(70)
        self.run.step({self.a: (0, 0, 1)})
        self.settle(110)
        cast = self.fight.meteors.casts[self.a.body]
        self.assertTrue(cast.finished)
        self.assertIn(bot.entity, cast.hit)
        self.assertLess(charge.value(), 30)

    def test_hand_cannon_ally_rejection_spends_one_round_for_each_actual_npc(self):
        for definition in self.catalog.values():
            with self.subTest(enemy=definition.name):
                _, vitals = self.create_enemy(definition, team=0)
                before = vitals.total
                weapon = next(i for i in self.a.body_script.component.instances.values()
                              if i.graph.index == 0xD0A)
                charge = self.charge_checkpoint()
                self.fire()
                self.settle(10)
                self.assertEqual(vitals.total, before)
                self.assert_damage_charge(charge, 0)
                self.assertEqual(weapon.vars[53].value(), 3)
                self.assertFalse(self.a.client.messages(combat.HIT))
                self.assertEqual(self.own_vitals().temporary_shields, 0)

    def test_hand_cannon_off_axis_miss_spends_one_round_for_each_actual_npc(self):
        for definition in self.catalog.values():
            with self.subTest(enemy=definition.name):
                _, vitals = self.create_enemy(definition, distance=3)
                before = vitals.total
                weapon = next(i for i in self.a.body_script.component.instances.values()
                              if i.graph.index == 0xD0A)
                charge = self.charge_checkpoint()
                self.run.step({self.a: (16384, 0, 1)})
                self.assertEqual(len(self.fight.rockets), 11)
                self.settle(10)
                self.assertEqual(vitals.total, before)
                self.assert_damage_charge(charge, 0)
                self.assertEqual(weapon.vars[53].value(), 3)
                self.assertFalse(self.a.client.messages(combat.HIT))
                self.assertEqual(self.own_vitals().temporary_shields, 0)

    def test_hand_cannon_wall_rejects_all_pellets_for_each_actual_npc(self):
        for definition in self.catalog.values():
            with self.subTest(enemy=definition.name):
                _, vitals = self.create_enemy(definition, distance=3)
                before = vitals.total
                self.install_wall()
                charge = self.charge_checkpoint()
                self.fire()
                self.settle(10)
                self.assertEqual(vitals.total, before)
                self.assert_damage_charge(charge, 0)
                self.assertFalse(self.fight.rockets)
                self.assertFalse(self.a.client.messages(combat.HIT))
                self.assertEqual(self.own_vitals().temporary_shields, 0)

    def test_duplicate_actual_fire_commands_do_not_repeat_volley_or_cost_for_five_npcs(self):
        for definition in self.catalog.values():
            with self.subTest(enemy=definition.name):
                bot, vitals = self.create_enemy(definition)
                before = vitals.total
                self.fire()
                x, y, z = bot.position
                yaw, pitch = aim(self.fight._eye(self.a), (x, y + 1, z))
                command = Command(self.run.frame, yaw=yaw, pitch=pitch, buttons=1)
                self.a.take_commands([command, command])
                self.settle(10)
                weapon = next(i for i in self.a.body_script.component.instances.values()
                              if i.graph.index == 0xD0A)
                self.assertEqual(before - vitals.total, 66)
                self.assertEqual(weapon.vars[53].value(), 3)
                self.assertEqual(len(self.a.client.messages(combat.HIT)), 11)

    def test_actual_quick_melee_ally_range_and_wall_rejection_for_all_five(self):
        for definition in self.catalog.values():
            cases = (('ally', 0, 1.5), ('range', 1, 6), ('wall', 1, 2.3), ('off_axis', 1, 1.5))
            for case, team, distance in cases:
                with self.subTest(enemy=definition.name, case=case):
                    bot, vitals = self.create_enemy(definition, team=team, distance=distance)
                    before = vitals.total
                    if case == 'wall':
                        self.install_wall()
                    instance = next(i for i in self.a.body_script.component.instances.values()
                                    if i.graph.index == 0x43)
                    effect = instance.state(4)
                    counter = effect.counter
                    x, y, z = bot.position
                    offset = 8 if case == "off_axis" else 0
                    yaw, pitch = aim(self.fight._eye(self.a), (x + offset, y + 1, z))
                    charge = self.charge_checkpoint()
                    self.run.step({self.a: (yaw, pitch, 0x800)})
                    self.settle(45)
                    self.assertEqual(effect.counter, counter + 1)
                    self.assertEqual(vitals.total, before)
                    self.assert_damage_charge(charge, 0)
                    self.assertFalse(self.a.client.messages(combat.HIT))
                    self.assertEqual(self.own_vitals().temporary_shields, 0)
                    if case != 'ally':
                        ability = instance.state(6)
                        for _ in range(120):
                            if not ability.flags & nodes.Ability.RUNNING and ability.remaining() == 0:
                                break
                            self.settle(1)
                        self.assertFalse(ability.flags & nodes.Ability.RUNNING)
                        self.assertEqual(ability.remaining(), 0)
                        if case == 'wall':
                            self.install_wall(present=False)
                        elif case == 'range':
                            ox, oy, oz = self.a.mover.position
                            bot.position = (ox, oy, oz + 1.5)
                        self.settle(5)  # retain fresh target poses for the command's view time
                        x, y, z = bot.position
                        yaw, pitch = aim(self.fight._eye(self.a), (x, y + 1, z))
                        charge = self.charge_checkpoint()
                        self.run.step({self.a: (yaw, pitch, 0x800)})
                        self.settle(45)
                        self.assertEqual(before - vitals.total, 30)
                        self.assert_damage_charge(charge, 30)

    def test_actual_uppercut_range_and_off_axis_miss_for_all_five_npcs(self):
        for definition in self.catalog.values():
            for case, distance, yaw in (('range', 7, 0), ('off_axis', 2, -16384)):
                with self.subTest(enemy=definition.name, case=case):
                    _, vitals = self.create_enemy(definition, distance=distance)
                    before = vitals.total
                    self.run.step({self.a: (yaw, 0, 8)})
                    self.settle(45)
                    instance = next(i for i in self.a.body_script.component.instances.values()
                                    if i.graph.index == 0xDD8)
                    self.assertGreater(instance.state(10).counter, 0)
                    self.assertEqual(vitals.total, before)
                    self.assertFalse(self.a.client.messages(combat.HIT))
                    self.assertEqual(self.own_vitals().temporary_shields, 0)
                    self.assertFalse(self.own_vitals().temporary_pool_used)

    def test_uppercut_wall_miss_starts_real_cooldown_rejects_then_fresh_hits_all_five(self):
        for definition in self.catalog.values():
            with self.subTest(enemy=definition.name):
                _, vitals = self.create_enemy(definition, distance=2.5)
                before = vitals.total
                self.install_wall()
                charge = self.charge_checkpoint()
                self.run.step({self.a: (0, 0, 8)})
                self.settle(60)  # actual recovery ends before graph starts cooldown
                instance = next(i for i in self.a.body_script.component.instances.values()
                                if i.graph.index == 0xDD8)
                ability = next(s for s in instance.states.values() if isinstance(s, nodes.Ability))
                effect = instance.state(10)
                counter = effect.counter
                self.assertGreater(counter, 0)
                self.assertGreater(ability.remaining(), 0)
                self.assertEqual(vitals.total, before)
                self.assert_damage_charge(charge, 0)
                self.assertEqual(self.own_vitals().temporary_shields, 0)
                self.run.step({self.a: (0, 0, 8)})
                self.settle(10)
                self.assertEqual(effect.counter, counter)
                while ability.remaining() > 0:
                    self.settle(1)
                self.settle(15)
                self.assert_damage_charge(charge, 0)
                self.assertEqual(effect.counter, counter)
                self.install_wall(present=False)
                charge = self.charge_checkpoint()
                self.run.step({self.a: (0, 0, 8)})
                self.assertEqual(effect.counter, counter + 1)
                self.assertEqual(before - vitals.total, 50)
                self.assert_damage_charge(charge, 50)
                self.assertEqual(self.own_vitals().temporary_shields, 30)

    @unittest.skipUnless(os.environ.get('ORPHEUS_REQUIRE_NPC_MOVERS') == '1',
                         'requires explicit authored five-NPC mover inputs')
    def test_authored_npc_mover_inputs_drive_capsules_speed_and_launch(self):
        expected = {'Trooper': (1.7, .5, 5.75), 'Enforcer': (1.9, .5, 5),
                    'Sniper': (1.75, .5, 4.5), 'Assassin': (1.5, .5, 14),
                    'Heavy Assault': (2.3, .7, 2.2)}
        for definition in self.catalog.values():
            with self.subTest(enemy=definition.name):
                bot, _ = self.create_enemy(definition)
                target = next(t for t in self.fight._targets() if t.owner is bot)
                height, radius, speed = expected[definition.name]
                self.assertEqual((target.height, target.radius, bot.speed), (height, radius, speed))
                self.run.step({self.a: (0, 0, 8)})
                self.assertIsNotNone(bot.motion)
                self.assertIs(bot.motion.data, mover.mover_data(definition.body))
                self.assertEqual((bot.motion.data.stand_height, bot.motion.data.stand_radius),
                                 (height, radius))

    @unittest.skipUnless(os.environ.get('ORPHEUS_REQUIRE_NPC_MOVERS') == '1',
                         'requires explicit authored five-NPC mover inputs')
    def test_actual_volley_graze_distinguishes_heavy_authored_radius(self):
        # At2m, natural spread cannot bridge a0.65m lateral offset to a0.5m
        # capsule. Heavy's authored0.7m capsule catches part of the same volley.
        for definition in self.catalog.values():
            with self.subTest(enemy=definition.name):
                bot, vitals = self.create_enemy(definition, distance=2)
                x, y, z = bot.position
                bot.position = (x + .65, y, z)
                before = vitals.total
                self.fire()
                self.settle(10)
                if definition.name == 'Heavy Assault':
                    self.assertLess(vitals.total, before)
                    self.assertTrue(self.a.client.messages(combat.HIT))
                else:
                    self.assertEqual(vitals.total, before)
                    self.assertFalse(self.a.client.messages(combat.HIT))

    def test_owner_health_query_tracks_actual_pellet_damage_for_all_five_bodies(self):
        query = {'$': 'STU_9B7648C3', 'm_entity': {'$': 'STU_B5A0CAF0'}}
        for definition in self.catalog.values():
            with self.subTest(enemy=definition.name):
                bot, vitals = self.create_enemy(definition)
                instance = next(iter(bot.body_script.component.instances.values()))
                before = vitals.total
                self.assertEqual(instance.evaluate(query), before)
                health, shields = vitals.now[combat.HEALTH_POOL], vitals.now[combat.SHIELDS_POOL]
                self.fire()
                self.settle(8)
                self.assertAlmostEqual(before - vitals.total, 66)
                self.assertEqual(instance.evaluate(query), vitals.total)
                self.assertEqual(vitals.now[combat.SHIELDS_POOL], max(0, shields - 66))
                self.assertEqual(vitals.now[combat.HEALTH_POOL], health - max(0, 66 - shields))

    def test_flashbang_blocks_uppercut_while_stunned_or_dead_against_all_five_npcs(self):
        for definition in self.catalog.values():
            for lethal in (False, True):
                with self.subTest(enemy=definition.name, lethal=lethal):
                    _, vitals = self.create_enemy(definition)
                    before = vitals.total
                    self.b.team = 1
                    self.match.switch_hero(self.b, heroes()[0x02E0000000000042])
                    own = self.own_vitals()
                    if lethal:
                        owner = next(t for t in self.fight._targets() if t.owner is self.a)
                        self.fight._damage(self.b, owner, 235, False, None, self.run.frame)
                    x, y, z = self.a.mover.position
                    self.place_player(self.b, (x + 1.5, y, z))
                    yaw, pitch = aim(self.fight._eye(self.b), self.fight._eye(self.a))
                    self.run.step({self.b: (yaw, pitch, 16)})
                    for _ in range(20):
                        if self.fight.is_stunned(self.a) or self.fight.is_dead(self.a):
                            break
                        self.run.step({})
                    self.assertEqual(self.fight.is_dead(self.a), lethal)
                    if lethal:
                        self.assertEqual(own.killer, self.b.body)
                    else:
                        self.assertTrue(self.fight.is_stunned(self.a))
                    self.place_player(self.b, (x + 15, y, z))
                    instance = next(i for i in self.a.body_script.component.instances.values()
                                    if i.graph.index == 0xDD8)
                    effect = instance.state(10)
                    counter = effect.counter
                    self.run.step({self.a: (0, 0, 8)})
                    self.settle(110)
                    self.assertEqual(effect.counter, counter)
                    self.assertEqual(vitals.total, before)
                    self.assertEqual(own.temporary_shields, 0)
                    if not lethal:
                        self.assertFalse(self.fight.is_stunned(self.a))
                        self.run.step({self.a: (0, 0, 8)})
                        self.assertEqual(effect.counter, counter + 1)
                        self.assertEqual(before - vitals.total, 50)
                        self.assertEqual(own.temporary_shields, 30)

    def test_flashbang_interrupts_committed_uppercut_without_repeating_npc_hit(self):
        self.check_committed_uppercut_after_enemy_attack(lethal=False)

    def test_lethal_enemy_fire_after_committed_uppercut_preserves_each_npc_hit(self):
        self.check_committed_uppercut_after_enemy_attack(lethal=True)

    def check_committed_uppercut_after_enemy_attack(self, *, lethal):
        for definition in self.catalog.values():
            with self.subTest(enemy=definition.name):
                bot, vitals = self.create_enemy(definition)
                before = vitals.total
                self.b.team = 1
                self.match.switch_hero(self.b, heroes()[0x02E0000000000042])
                x, y, z = self.a.mover.position
                self.place_player(self.b, (x + 2, y, z))
                own = self.own_vitals()
                if lethal:
                    own.now[combat.HEALTH_POOL] = 15
                ex, ey, ez = self.fight._eye(self.a)
                yaw, pitch = aim(self.fight._eye(self.b), (ex, ey if lethal else ey + .65, ez))
                charge = self.charge_checkpoint()
                self.run.step({self.a: (0, 0, 8), self.b: (yaw, pitch, 1 if lethal else 16)})
                self.assertEqual(before - vitals.total, 50)
                self.assert_damage_charge(charge, 50)
                instance = next(i for i in self.a.body_script.component.instances.values()
                                if i.graph.index == 0xDD8)
                ability = next(s for s in instance.states.values() if isinstance(s, nodes.Ability))
                effect = instance.state(10)
                counter = effect.counter
                if not lethal:
                    self.assertTrue(ability.flags & nodes.Ability.RUNNING)
                for _ in range(25):
                    if own.dead or self.fight.is_stunned(self.a):
                        break
                    self.run.step({})
                self.assertEqual(own.dead, lethal)
                if lethal:
                    self.assertEqual(own.killer, self.b.body)
                    self.assertEqual(own.temporary_shields, 0)
                else:
                    self.assertTrue(self.fight.is_stunned(self.a))
                    self.assertFalse(ability.flags & nodes.Ability.RUNNING)
                    self.assertGreater(ability.remaining(), 0)
                self.assertTrue(bot.motion.airborne)
                self.run.step({self.a: (0, 0, 8)})
                self.settle(140)
                self.assertFalse(self.fight.is_stunned(self.a))
                self.assertEqual(effect.counter, counter)
                self.assertEqual(before - vitals.total, 50)
                self.assert_damage_charge(charge, 50)
                self.assertEqual(len(self.a.client.messages(combat.HIT)), 1)
                self.assertAlmostEqual(bot.position[1], y, delta=.01)
                self.assertGreater(bot.position[2], z + 1.5)

    def test_uppercut_npc_airborne_input_and_landing_attack(self):
        airborne_damage = {'Trooper': 20, 'Enforcer': 2, 'Sniper': 0,
                           'Assassin': 25, 'Heavy Assault': 5}
        for definition in self.catalog.values():
            with self.subTest(enemy=definition.name):
                bot, own = self.prepare_incoming(definition, distance=1.5)
                before = self.fight.vitals[bot.entity].total
                floor = bot.position[1]
                self.run.step({self.a: (0, 0, 8)})
                self.assertEqual(before - self.fight.vitals[bot.entity].total, 50)
                npc_control.start(self.match, self.a)
                before_attack = own.total
                self.assertEqual(own.temporary_shields, 30)
                self.settle(12)
                self.assertIsNotNone(bot.motion)
                self.assertTrue(bot.motion.airborne)
                self.assertGreater(bot.position[1], floor + .5)
                self.assertFalse(bot.controlled)
                self.assertFalse(bot.body_script.component.input_suppressed)
                button = npc_control.button(bot)
                self.assertIn(button, bot.body_script.component.held)
                damage = 0
                for _ in range(150):
                    decay = 3 * max(0, self.fight.now - own.temporary_decay_at)
                    damage = before_attack - min(30, decay) - own.total
                    if not bot.motion.airborne or damage > .001:
                        break
                    self.run.step({})
                expected = airborne_damage[definition.name]
                self.assertAlmostEqual(damage, expected)
                self.assertEqual(bot.motion.airborne, expected > 0)
                npc_control.stop(self.match)
                self.settle(155)
                self.assertAlmostEqual(bot.position[1], floor, delta=.01)
                self.assertFalse(bot.motion is not None and bot.motion.airborne)
                x, y, z = bot.position
                self.place_player(self.a, (x, y, z - 2))
                yaw, pitch = aim(self.fight._eye(self.a), (x, y + 1.4, z))
                self.run.step({self.a: (yaw, pitch, 0)})
                initial = own.total
                self.assertEqual(npc_control.start(self.match, self.a), bot.entity)
                for _ in range(150):
                    self.run.step({})
                    if own.total < initial:
                        break
                self.assertLess(own.total, initial)
                npc_control.stop(self.match)

    def test_sniper_windup_can_finish_while_airborne_after_uppercut(self):
        definition = next(d for d in self.catalog.values() if d.name == 'Sniper')
        _, own = self.prepare_incoming(definition, distance=1.5)
        npc_control.start(self.match, self.a)
        for windup in range(1, 151):
            self.run.step({})
            if own.total < 250:
                break
        self.assertEqual(own.total, 150)
        self.assertGreater(windup, 12)

        bot, own = self.prepare_incoming(definition, distance=1.5)
        before = self.fight.vitals[bot.entity].total
        npc_control.start(self.match, self.a)
        self.settle(windup - 12)
        self.assertEqual(own.total, 250)
        self.run.step({self.a: (0, 0, 8)})
        self.assertEqual(self.fight.vitals[bot.entity].total, before - 50)
        self.assertTrue(bot.motion.airborne)
        self.assertEqual(own.total, 280)
        for _ in range(35):
            if own.total < 280:
                break
            self.run.step({})
        self.assertTrue(bot.motion.airborne)
        self.assertFalse(bot.body_script.component.input_suppressed)
        self.assertEqual(own.total, 180)
        self.assertEqual(own.temporary_shields, 0)
        npc_control.stop(self.match)

    def test_uppercut_hold_through_recovery_requires_release_against_all_five_npcs(self):
        self.check_uppercut_recovery_against_all_five_npcs(held=True)

    def test_uppercut_early_release_preserves_recovery_against_all_five_npcs(self):
        self.check_uppercut_recovery_against_all_five_npcs(held=False)

    def check_uppercut_recovery_against_all_five_npcs(self, *, held):
        recovery_command = {self.a: (0, 0, 8)} if held else {}
        for definition in self.catalog.values():
            with self.subTest(enemy=definition.name):
                bot, vitals = self.create_enemy(definition)
                initial = vitals.total
                x, y, z = self.a.mover.position
                self.place_player(self.b, (x + 15, y, z))
                self.run.step({self.a: (0, 0, 8)})
                instance = next(i for i in self.a.body_script.component.instances.values()
                                if i.graph.index == 0xDD8)
                ability = next(s for s in instance.states.values() if isinstance(s, nodes.Ability))
                effect = instance.state(10)
                hit_count = effect.counter
                self.assertTrue(ability.flags & nodes.Ability.RUNNING)
                self.assertEqual(initial - vitals.total, 50)
                self.assertEqual(ability.remaining(), 0)
                for _frames in range(1, 70):
                    self.run.step(recovery_command)
                    if not ability.flags & nodes.Ability.RUNNING:
                        break
                    self.assertEqual(ability.remaining(), 0)
                self.assertEqual(_frames, 37)
                self.assertFalse(ability.flags & nodes.Ability.RUNNING)
                self.assertAlmostEqual(ability.remaining(), 5.993, places=3)
                for _ in range(500):
                    if ability.remaining() == 0:
                        break
                    self.run.step(recovery_command)
                self.assertEqual(ability.remaining(), 0)
                bx, _, bz = bot.position
                self.place_player(self.a, (bx, y, bz - 1.5))
                for _ in range(12):
                    self.run.step(recovery_command)
                self.assertEqual(effect.counter, hit_count)
                self.assertEqual(initial - vitals.total, 50)
                self.run.step({})
                self.assertEqual(effect.counter, hit_count)
                self.run.step({self.a: (0, 0, 8)})
                self.assertEqual(effect.counter, hit_count + 1)
                self.assertEqual(initial - vitals.total, 100)

    def test_uppercut_armour_and_lethal_retained_corpses_against_all_five_npcs(self):
        for definition in self.catalog.values():
            for lethal in (False, True):
                with self.subTest(enemy=definition.name, lethal=lethal):
                    pools = replace(definition, health=20, armour=0, shields=0) if lethal else replace(
                        definition, armour=100, shields=0,
                    )
                    bot, vitals = self.create_enemy(pools)
                    initial = vitals.total
                    charge = self.charge_checkpoint()
                    self.run.step({self.a: (0, 0, 8)})
                    self.assertEqual(self.own_vitals().temporary_shields, 30)
                    self.settle(45)
                    self.assertEqual(len(self.a.client.messages(combat.HIT)), 1)
                    if lethal:
                        self.assertTrue(bot.dead)
                        self.assertEqual(vitals.total, 0)
                        self.assertEqual(vitals.killer, self.a.body)
                        self.assertIn(bot, self.match.bots)
                        self.assertFalse(any(who is bot for _, who in self.fight.respawns))
                    else:
                        self.assertFalse(bot.dead)
                        self.assertEqual(vitals.now[combat.ARMOUR_POOL], 55)
                        self.assertEqual(vitals.now[combat.HEALTH_POOL], definition.health)
                    self.assert_damage_charge(charge, initial - vitals.total)

    def test_uppercut_actual_damage_to_each_of_five_bodies(self):
        for definition in self.catalog.values():
            with self.subTest(enemy=definition.name):
                _, vitals = self.create_enemy(definition)
                before = vitals.total
                charge = self.charge_checkpoint()
                self.run.step({self.a: (0, 0, 8)})
                source = next(t for t in self.fight._targets() if t.owner is self.a)
                self.assertEqual(self.fight.vitals_of(source).temporary_shields, 30)
                self.settle(45)
                self.assertAlmostEqual(before - vitals.total, 50)
                self.assert_damage_charge(charge, 50)

    def test_flashbang_stops_quick_melee_recovery_against_all_five_npcs(self):
        for definition in self.catalog.values():
            for lethal in (False, True):
                with self.subTest(enemy=definition.name, lethal=lethal):
                    bot, vitals = self.create_enemy(definition)
                    before = vitals.total
                    self.b.team = 1
                    self.match.switch_hero(self.b, heroes()[0x02E0000000000042])
                    own = self.own_vitals()
                    if lethal:
                        owner = next(t for t in self.fight._targets() if t.owner is self.a)
                        self.fight._damage(self.b, owner, 235, False, None, self.run.frame)
                    x, y, z = self.a.mover.position
                    self.place_player(self.b, (x + 1.5, y, z))
                    instance = next(i for i in self.a.body_script.component.instances.values()
                                    if i.graph.index == 0x43)
                    effect, ability = instance.state(4), instance.state(6)
                    weapon = next(i for i in self.a.body_script.component.instances.values()
                                  if i.graph.index == 0xD0A)
                    ammo = weapon.vars[53].value()
                    charge = self.charge_checkpoint()
                    yaw, pitch = aim(self.fight._eye(self.b), self.fight._eye(self.a))
                    bx, by, bz = bot.position
                    melee_yaw, melee_pitch = aim(self.fight._eye(self.a), (bx, by + 1, bz))
                    self.run.step({self.a: (melee_yaw, melee_pitch, 0x800), self.b: (yaw, pitch, 16)})
                    self.assertTrue(ability.flags & nodes.Ability.RUNNING)
                    counter = effect.counter
                    self.assertEqual(before - vitals.total, 30)
                    self.assertEqual(weapon.vars[53].value(), ammo)
                    self.assert_damage_charge(charge, 30)
                    for _ in range(20):
                        if self.fight.is_stunned(self.a) or self.fight.is_dead(self.a):
                            break
                        self.run.step({})
                    self.assertEqual(self.fight.is_dead(self.a), lethal)
                    if lethal:
                        self.assertEqual(own.killer, self.b.body)
                    else:
                        self.assertTrue(self.fight.is_stunned(self.a))
                        self.assertFalse(ability.flags & nodes.Ability.RUNNING)
                    self.run.step({self.a: (melee_yaw, melee_pitch, 0x800)})
                    self.settle(140)
                    self.assertEqual(effect.counter, counter)
                    self.assertEqual(before - vitals.total, 30)
                    self.assertEqual(own.temporary_shields, 0)
                    self.assertFalse(own.temporary_pool_used)
                    self.assertEqual(weapon.vars[53].value(), ammo)
                    self.assert_damage_charge(charge, 30)
                    self.assertEqual(len(self.a.client.messages(combat.HIT)), 1)
                    if not lethal:
                        self.assertFalse(self.fight.is_stunned(self.a))
                        self.place_player(self.b, (x + 15, y, z))
                        self.run.step({self.a: (melee_yaw, melee_pitch, 0x800)})
                        self.settle(45)
                        self.assertEqual(effect.counter, counter + 1)
                        self.assertEqual(before - vitals.total, 60)
                        self.assertEqual(weapon.vars[53].value(), ammo)
                        self.assert_damage_charge(charge, 60)
                        self.assertEqual(len(self.a.client.messages(combat.HIT)), 2)
                        self.assertFalse(own.temporary_pool_used)

    def test_quick_melee_repeat_interval_against_all_five_npcs(self):
        for definition in self.catalog.values():
            with self.subTest(enemy=definition.name):
                bot, vitals = self.create_enemy(definition)
                before = vitals.total
                x, y, z = bot.position
                yaw, pitch = aim(self.fight._eye(self.a), (x, y + 1, z))
                instance = next(i for i in self.a.body_script.component.instances.values()
                                if i.graph.index == 0x43)
                ability, effect = instance.state(6), instance.state(4)
                self.run.step({self.a: (yaw, pitch, 0x800)})
                for _ in range(45):
                    if vitals.total < before:
                        break
                    self.run.step({})
                self.assertEqual(before - vitals.total, 30)
                self.assertTrue(ability.flags & nodes.Ability.RUNNING)
                first = effect.counter
                self.run.step({})
                self.run.step({self.a: (yaw, pitch, 0x800)})
                for _ in range(150):
                    if not ability.flags & nodes.Ability.RUNNING and ability.remaining() == 0:
                        break
                    self.run.step({})
                self.assertFalse(ability.flags & nodes.Ability.RUNNING)
                self.assertEqual(ability.remaining(), 0)
                self.settle(10)
                self.assertEqual(effect.counter, first)
                self.assertEqual(before - vitals.total, 30)
                self.run.step({self.a: (yaw, pitch, 0x800)})
                self.settle(45)
                self.assertEqual(effect.counter, first + 1)
                self.assertEqual(before - vitals.total, 60)
                self.assertEqual(self.own_vitals().temporary_shields, 0)

    def test_quick_melee_armour_and_retained_corpses_against_all_five_npcs(self):
        for definition in self.catalog.values():
            for lethal in (False, True):
                with self.subTest(enemy=definition.name, lethal=lethal):
                    pools = replace(definition, health=20, armour=0, shields=0) if lethal else replace(
                        definition, armour=100, shields=0,
                    )
                    bot, vitals = self.create_enemy(pools)
                    x, y, z = bot.position
                    yaw, pitch = aim(self.fight._eye(self.a), (x, y + 1, z))
                    self.run.step({self.a: (yaw, pitch, 0x800)})
                    self.settle(60)
                    self.assertEqual(len(self.a.client.messages(combat.HIT)), 1)
                    self.assertEqual(self.own_vitals().temporary_shields, 0)
                    if lethal:
                        self.assertTrue(bot.dead)
                        self.assertEqual(vitals.total, 0)
                        self.assertEqual(vitals.killer, self.a.body)
                        self.assertIn(bot, self.match.bots)
                        self.assertFalse(any(who is bot for _, who in self.fight.respawns))
                    else:
                        self.assertFalse(bot.dead)
                        self.assertEqual(vitals.now[combat.ARMOUR_POOL], 75)
                        self.assertEqual(vitals.now[combat.HEALTH_POOL], definition.health)

    def test_melee_actual_damage_to_each_of_five_bodies(self):
        for definition in self.catalog.values():
            with self.subTest(enemy=definition.name):
                bot, vitals = self.create_enemy(definition)
                before = vitals.total
                charge = self.charge_checkpoint()
                x, y, z = bot.position
                yaw, pitch = aim(self.fight._eye(self.a), (x, y + 1, z))
                self.run.step({self.a: (yaw, pitch, 0x800)})
                self.settle(45)
                self.assertAlmostEqual(before - vitals.total, 30)
                self.assert_damage_charge(charge, 30)

    def test_uppercut_does_not_damage_allied_versions_of_five_bodies(self):
        for definition in self.catalog.values():
            with self.subTest(ally=definition.name):
                _, vitals = self.create_enemy(definition, team=0)
                before = vitals.total
                self.run.step({self.a: (0, 0, 8)})
                self.settle(45)
                self.assertEqual(vitals.total, before)
                self.assertFalse(self.own_vitals().temporary_pool_used)

    def test_one_uppercut_hits_seven_actual_npcs_and_caps_aggregate_shields_at_150(self):
        first = next(iter(self.catalog.values()))
        self.create_enemy(first)
        x, y, z = self.a.mover.position
        for definition in (*tuple(self.catalog.values())[1:], first, first):
            npc.spawn(self.match, definition, (x, y, z + 1.5), team=1)
        before = {b.entity: self.fight.vitals[b.entity].total for b in self.match.bots}
        self.assertEqual(len(before), 7)
        self.run.step({self.a: (0, 0, 8)})
        for bot in self.match.bots:
            self.assertEqual(before[bot.entity] - self.fight.vitals[bot.entity].total, 50)
        source = next(t for t in self.fight._targets() if t.owner is self.a)
        self.assertEqual(self.fight.vitals_of(source).temporary_shields, 150)

    def test_each_real_body_death_retains_corpse_without_respawn(self):
        for definition in self.catalog.values():
            with self.subTest(enemy=definition.name):
                bot, vitals = self.create_enemy(definition)
                # A wounded real body exercises the actual lethal/terminal path.
                for kind in vitals.now:
                    vitals.now[kind] = 0
                vitals.now[combat.HEALTH_POOL] = 25
                bot.body_script.set_health(25, sum(vitals.most.values()))
                self.fire()
                self.settle(210)
                self.assertTrue(bot.dead)
                self.assertEqual(vitals.killer, self.a.body)
                self.assertIn(bot, self.match.bots)
                self.assertFalse(any(who is bot for _, who in self.fight.respawns))
                self.assertEqual(vitals.total, 0)

    def test_each_real_npc_is_launched_by_input_and_lands_on_actual_collision(self):
        for definition in self.catalog.values():
            with self.subTest(enemy=definition.name):
                bot, _ = self.create_enemy(definition)
                _, y, z = bot.position
                self.run.step({self.a: (0, 0, 8)})
                self.assertIsNotNone(bot.motion)
                self.settle(10)
                self.assertGreater(bot.position[1], y + 0.5)
                self.assertGreater(bot.position[2], z + 0.5)
                self.assertGreater(bot.movement(self.run.frame).air_ticks, 0)
                self.settle(150)
                self.assertIsNone(bot.motion)
                self.assertEqual(bot.position[1], y)

    def test_death_in_air_keeps_corpse_and_gravity_continues_to_collision(self):
        bot, vitals = self.create_enemy(next(iter(self.catalog.values())))
        y = bot.position[1]
        self.run.step({self.a: (0, 0, 8)})
        self.settle(5)
        self.assertGreater(bot.position[1], y)
        target = next(t for t in self.fight._targets() if t.owner is bot)
        self.fight._damage(self.a, target, 10000, False, None, self.run.frame)
        self.assertTrue(bot.dead)
        self.settle(210)
        self.assertIn(bot, self.match.bots)
        self.assertEqual(vitals.total, 0)
        self.assertIsNone(bot.motion)
        self.assertEqual(bot.position[1], y)

    def test_skipped_world_ticks_match_continuous_npc_launch_collision(self):
        positions = []
        for skipped in (False, True):
            bot, _ = self.create_enemy(next(iter(self.catalog.values())))
            self.run.step({self.a: (0, 0, 8)})
            if skipped:
                frame = self.run.frame + 40
                self.match.update(frame * combat.TICK, frame)
            else:
                self.settle(40)
            positions.append((bot.position, bot.velocity))
        self.assertEqual(*positions)

    def punch_hit(self, vitals, frames=1):
        initial = vitals.total
        for _ in range(frames):
            self.run.step({self.a: (0, 0, 2)})
        self.run.step({})
        for _ in range(90):
            if vitals.total < initial:
                return
            self.run.step({})
        self.fail("actual NPC was not hit by the released Punch")

    def test_owner_death_prevents_punch_contact_with_all_five_npcs(self):
        for definition in self.catalog.values():
            for phase in ('charge', 'dash'):
                with self.subTest(enemy=definition.name, phase=phase):
                    bot, vitals = self.create_enemy(definition, distance=10)
                    before = vitals.total
                    self.b.team = 1
                    self.match.switch_hero(self.b, heroes()[0x02E0000000000042])
                    own = self.own_vitals()
                    owner = next(t for t in self.fight._targets() if t.owner is self.a)
                    if phase == 'charge':
                        self.fight._damage(self.b, owner, 235, False, None, self.run.frame)
                    x, y, z = self.a.mover.position
                    self.place_player(self.b, (x + (15 if phase == 'dash' else 1.5), y, z))
                    charge = self.charge_checkpoint()
                    for _ in range(90 if phase == 'dash' else 20):
                        self.run.step({self.a: (0, 0, 2)})
                    ability = self.a.body_script.component.instances[5].state(0)
                    self.assertTrue(ability.flags & nodes.Ability.RUNNING)
                    self.assertNotIn(self.a.body, self.fight.punches.dashes)
                    if phase == 'dash':
                        self.run.step({})
                        dash = self.fight.punches.dashes[self.a.body]
                        self.assertFalse(dash.finished)
                        self.assertEqual(vitals.total, before)
                        self.fight._damage(self.b, owner, 1000, False, None, self.run.frame)
                    else:
                        yaw, pitch = aim(self.fight._eye(self.b), self.fight._eye(self.a))
                        self.run.step({self.a: (0, 0, 2), self.b: (yaw, pitch, 16)})
                        for _ in range(20):
                            if self.fight.is_dead(self.a):
                                break
                            self.run.step({self.a: (0, 0, 2)})
                    self.assertTrue(self.fight.is_dead(self.a))
                    self.assertEqual(own.killer, self.b.body)
                    self.assertNotIn(self.a.body, self.fight.punches.dashes)
                    self.settle(90)
                    self.assertEqual(vitals.total, before)
                    self.assertFalse(bot.controlled)
                    self.assertEqual(own.temporary_shields, 0)
                    self.assertFalse(own.temporary_pool_used)
                    self.assert_damage_charge(charge, 0)
                    self.assertFalse(self.a.client.messages(combat.HIT))
                    self.assertNotIn(self.a.body, self.fight.punches.dashes)
                    self.assertFalse(any(i.target == bot.entity for i in self.fight.punches.impacts))

    def test_punch_range_wall_and_team_gates_against_all_five_npcs(self):
        for definition in self.catalog.values():
            travel = {}
            gates = (('short', 1), ('long', 90), ('wall', 90), ('ally', 90), ('off_axis', 1))
            for gate, held in gates:
                with self.subTest(enemy=definition.name, gate=gate):
                    distance = 25 if gate in ('short', 'long') else 6
                    bot, vitals = self.create_enemy(
                        definition, distance=distance, team=0 if gate == 'ally' else 1,
                    )
                    x, y, z = self.a.mover.position
                    if gate == 'off_axis':
                        bot.position = (x + 3, y, z + 1.5)
                        self.settle(5)
                    before, bot_start = vitals.total, tuple(bot.position)
                    self.place_player(self.b, (x + 15, y, z))
                    if gate == 'wall':
                        self.install_wall()
                    charge = self.charge_checkpoint()
                    for _ in range(held):
                        self.run.step({self.a: (0, 0, 2)})
                    self.settle(75)
                    dash = self.fight.punches.dashes[self.a.body]
                    self.assertTrue(dash.finished)
                    self.assertEqual(vitals.total, before)
                    self.assert_damage_charge(charge, 0)
                    self.assertEqual(tuple(bot.position), bot_start)
                    self.assertFalse(bot.controlled)
                    self.assertEqual(self.own_vitals().temporary_shields, 0)
                    self.assertFalse(self.own_vitals().temporary_pool_used)
                    travel[gate] = self.a.mover.position[2] - z
                    if gate == 'wall':
                        self.assertLess(travel[gate], 1.2)
                    elif gate == 'ally':
                        self.assertGreater(travel[gate], 6)
                    elif gate == 'off_axis':
                        self.assertGreater(travel[gate], 4)
            self.assertGreater(travel['short'], 4)
            self.assertLessEqual(travel['short'], 37.5 * .15 + .01)
            self.assertGreater(travel['long'], travel['short'] + 5)
            self.assertLessEqual(travel['long'], 37.5 * .45 + .01)

    def test_punch_cancel_cooldown_and_fresh_release_against_all_five_npcs(self):
        for definition in self.catalog.values():
            with self.subTest(enemy=definition.name):
                bot, vitals = self.create_enemy(definition, distance=6)
                start = tuple(self.a.mover.position)
                x, y, z = start
                self.place_player(self.b, (x + 15, y, z))
                before = vitals.total
                ability = self.a.body_script.component.instances[5].state(0)
                charge = self.charge_checkpoint()
                for _ in range(20):
                    self.run.step({self.a: (0, 0, 2)})
                self.assertTrue(ability.flags & nodes.Ability.RUNNING)
                self.assertEqual(tuple(self.a.mover.position), start)
                self.assertEqual(vitals.total, before)
                self.run.step({self.a: (0, 0, 1)})
                self.settle(65)
                self.assert_damage_charge(charge, 0)
                self.assertFalse(ability.flags & nodes.Ability.RUNNING)
                self.assertGreater(ability.remaining(), 0)
                self.assertNotIn(self.a.body, self.fight.punches.dashes)
                self.assertEqual(vitals.total, before)
                self.run.step({self.a: (0, 0, 2)})
                for _ in range(360):
                    if ability.remaining() == 0:
                        break
                    self.run.step({})
                self.assertEqual(ability.remaining(), 0)
                self.settle(5)
                self.assert_damage_charge(charge, 0)
                self.assertNotIn(self.a.body, self.fight.punches.dashes)
                self.assertEqual(tuple(self.a.mover.position), start)
                self.assertEqual(vitals.total, before)
                self.punch_hit(vitals)
                self.assert_damage_charge(charge, 50)
                self.assertEqual(before - vitals.total, 50)
                self.assertTrue(bot.controlled)
                self.settle(75)
                self.assert_damage_charge(charge, 50)
                self.assertTrue(self.fight.punches.dashes[self.a.body].finished)
                self.assertFalse(bot.controlled)
                self.assertEqual(before - vitals.total, 50)

    def test_punch_contact_starts_cooldown_and_allows_fresh_hit_against_all_five_npcs(self):
        for definition in self.catalog.values():
            with self.subTest(enemy=definition.name):
                bot, vitals = self.create_enemy(definition, distance=6)
                x, y, z = self.a.mover.position
                self.place_player(self.b, (x + 15, y, z))
                before = vitals.total
                charge = self.charge_checkpoint()
                ability = self.a.body_script.component.instances[5].state(0)
                self.run.step({self.a: (0, 0, 2)})
                self.assertEqual(ability.remaining(), 0)
                self.run.step({})
                for _ in range(90):
                    if vitals.total < before:
                        break
                    self.assertEqual(ability.remaining(), 0)
                    self.run.step({})
                self.assertEqual(before - vitals.total, 50)
                dash = self.fight.punches.dashes[self.a.body]
                self.assertTrue(dash.finished)
                for _ in range(60):
                    if ability.remaining() > 0:
                        break
                    self.run.step({})
                self.assertGreater(ability.remaining(), 0)
                self.run.step({self.a: (0, 0, 2)})
                for _ in range(360):
                    if ability.remaining() == 0:
                        break
                    self.run.step({})
                self.assertEqual(ability.remaining(), 0)
                bx, by, bz = bot.position
                self.place_player(self.a, (bx, by, bz - 6))
                self.settle(10)
                self.assertIs(self.fight.punches.dashes[self.a.body], dash)
                self.assertEqual(before - vitals.total, 50)
                self.punch_hit(vitals)
                self.assertIsNot(self.fight.punches.dashes[self.a.body], dash)
                self.assertEqual(before - vitals.total, 100)
                self.assert_damage_charge(charge, 100)

    def test_minimum_and_maximum_punch_damage_and_push_on_each_real_npc(self):
        for definition in self.catalog.values():
            for held, damage in ((1, 50), (90, 100)):
                with self.subTest(enemy=definition.name, held=held):
                    bot, vitals = self.create_enemy(definition, distance=6)
                    before, start = vitals.total, bot.position
                    charge = self.charge_checkpoint()
                    self.punch_hit(vitals, held)
                    self.assertEqual(before - vitals.total, damage)
                    self.assert_damage_charge(charge, damage)
                    self.assertTrue(bot.controlled)
                    self.assertTrue(bot.body_script.component.input_suppressed)
                    self.settle(5)
                    self.assertGreater(bot.position[2], start[2])
                    self.settle(35)
                    self.assertFalse(bot.controlled)
                    self.assertFalse(bot.body_script.component.input_suppressed)

    def test_punch_displacement_and_control_end_on_all_five_npcs(self):
        for definition in self.catalog.values():
            travel = []
            for wall in (False, True):
                with self.subTest(enemy=definition.name, wall=wall):
                    pools = replace(definition, health=1000, armour=0, shields=0)
                    bot, vitals = self.create_enemy(pools, distance=6)
                    x, y, z = self.a.mover.position
                    self.place_player(self.b, (x + 15, y, z))
                    if wall:
                        world = World(box(x - 30, x + 30, y - 1, y, z - 30, z + 30)
                                      + box(x - 30, x + 30, y, y + 6, z + 6.8, z + 7.8))
                        self.match.collision_world = world
                        for player in self.match.players:
                            player.mover.use(world=world)
                    start_z = bot.position[2]
                    contact_charge = self.charge_checkpoint()
                    self.punch_hit(vitals, 90)
                    self.assertEqual(vitals.total, 900)
                    self.assert_damage_charge(contact_charge, 100)
                    wall_charge = self.charge_checkpoint()
                    self.assertTrue(bot.controlled)
                    self.assertTrue(bot.body_script.component.input_suppressed)
                    self.settle(65)
                    self.assertFalse(bot.controlled)
                    self.assertFalse(bot.body_script.component.input_suppressed)
                    self.assert_damage_charge(wall_charge, 150 if wall else 0)
                    self.assertEqual(vitals.total, 750 if wall else 900)
                    self.assertGreater(bot.position[2], start_z)
                    if wall:
                        self.assertLess(bot.position[2], z + 6.8)
                    travel.append(bot.position[2] - start_z)
                    final = tuple(bot.position)
                    settled_charge = self.charge_checkpoint()
                    self.settle(35)
                    self.assert_damage_charge(settled_charge, 0)
                    self.assertEqual(tuple(bot.position), final)
                    self.assertEqual(vitals.total, 750 if wall else 900)
            self.assertGreater(travel[0], travel[1] + .05, definition.name)

    def test_full_punch_wall_damage_and_retained_death_across_five_real_bodies(self):
        self.check_full_punch_wall_damage(owner_dies=False)

    def test_punch_wall_impact_survives_owner_death_against_all_five_npcs(self):
        self.check_full_punch_wall_damage(owner_dies=True)

    def test_punch_wall_impact_kills_each_low_health_npc(self):
        self.check_full_punch_wall_damage(owner_dies=False, lethal_target=True)

    def check_full_punch_wall_damage(self, *, owner_dies, lethal_target=False):
        for definition in self.catalog.values():
            with self.subTest(enemy=definition.name):
                pools = replace(definition, health=125, armour=0, shields=0) if lethal_target else definition
                bot, vitals = self.create_enemy(pools, distance=6)
                before = vitals.total
                charge = self.charge_checkpoint()
                x, y, z = self.a.mover.position
                collision = World(box(x - 30, x + 30, y - 1, y, z - 30, z + 30)
                                  + box(x - 30, x + 30, y, y + 6, z + 6.8, z + 7.8))
                self.match.collision_world = collision
                for player in self.match.players:
                    player.mover.use(world=collision)
                self.punch_hit(vitals, 90)
                if lethal_target:
                    self.assertEqual(vitals.total, 25)
                    self.assertFalse(vitals.dead)
                if owner_dies:
                    self.assertEqual(before - vitals.total, 100)
                    impact, = self.fight.punches.impacts
                    self.assertEqual(impact.target, bot.entity)
                    owner = next(t for t in self.fight._targets() if t.owner is self.a)
                    self.fight._damage(self.b, owner, 1000, False, None, self.run.frame)
                    self.assertTrue(self.fight.is_dead(self.a))
                    self.assertNotIn(self.a.body, self.fight.punches.dashes)
                    self.assertIn(impact, self.fight.punches.impacts)
                self.settle(45)
                self.assertEqual(before - vitals.total, min(before, 250))
                self.assertEqual([hit['+0x78']['+0x4'] for hit in self.a.client.messages(combat.HIT)],
                                 [100, min(before - 100, 150)])
                self.assertEqual(vitals.dead, before <= 250)
                if vitals.dead:
                    self.assertEqual(vitals.killer, self.a.body)
                    self.assertEqual(self.a.stats.value(stats.ELIMINATIONS), 1)
                    self.assertFalse(any(who is bot for _, who in self.fight.respawns))
                if owner_dies:
                    self.assertFalse(self.fight.punches.impacts)
                    self.assertFalse(bot.controlled)
                    self.assertEqual(self.own_vitals().temporary_shields, 0)
                    self.assert_damage_charge(charge, 100)
                else:
                    self.assert_damage_charge(charge, min(before, 250))
                self.assertIn(bot, self.match.bots)
                self.assertTrue(bot.retain_corpse)

    @unittest.skipUnless(os.environ.get('ORPHEUS_REQUIRE_NPC_MOVERS') == '1',
                         'requires explicit authored five-NPC mover inputs')
    def test_assassin_stopping_before_wall_does_not_get_proximity_bonus(self):
        definition = next(d for d in self.catalog.values() if d.name == 'Assassin')
        bot, vitals = self.create_enemy(definition, distance=6)
        before = vitals.total
        x, y, z = self.a.mover.position
        collision = World(box(x - 30, x + 30, y - 1, y, z - 30, z + 30)
                          + box(x - 30, x + 30, y, y + 6, z + 7, z + 8))
        self.match.collision_world = collision
        for player in self.match.players:
            player.mover.use(world=collision)
        self.punch_hit(vitals, 90)
        self.settle(45)
        target = next(t for t in self.fight._targets() if t.owner is bot)
        self.assertGreater(z + 7 - (bot.position[2] + target.radius), .01)
        self.assertEqual(before - vitals.total, 100)

    def test_removal_during_punch_control_clears_pending_impacts_and_controls(self):
        bot, vitals = self.create_enemy(next(iter(self.catalog.values())), distance=6)
        self.punch_hit(vitals)
        self.assertIn(bot.entity, self.fight.punches.controls)
        self.assertTrue(any(i.target == bot.entity for i in self.fight.punches.impacts))
        npc.remove_spawned(self.match)
        self.assertNotIn(bot.entity, self.fight.punches.controls)
        self.assertFalse(any(i.target == bot.entity for i in self.fight.punches.impacts))

    def test_ground_slam_wall_miss_and_cooldown_recovery_for_all_five_npcs(self):
        for definition in self.catalog.values():
            with self.subTest(enemy=definition.name):
                bot, vitals = self.create_enemy(definition, distance=10)
                before = vitals.total
                x, y, z = self.a.mover.position
                self.place_player(self.b, (x + 15, y, z))
                self.install_wall()
                script = self.a.body_script
                weapon = next(i for i in script.component.instances.values() if i.graph.index == 0xD0A)
                ammo = weapon.vars[53].value()
                ability = script.component.instances[8].state(0)
                charge = self.charge_checkpoint()
                self.run.step({self.a: (0, 0, 0x10)})
                self.settle(145)
                cast = self.fight.slams.casts[self.a.body]
                self.assertTrue(cast.finished)
                self.assertLess(self.a.mover.position[2], z + 1.2)
                self.assertEqual(vitals.total, before)
                self.assertFalse(cast.hit)
                self.assert_damage_charge(charge, 0)
                self.assertFalse(self.own_vitals().temporary_pool_used)
                self.assertGreater(ability.remaining(), 0)
                self.assertEqual(weapon.vars[53].value(), ammo)
                self.install_wall(present=False)
                self.run.step({self.a: (0, 0, 0x10)})
                for _ in range(500):
                    if ability.remaining() == 0:
                        break
                    self.run.step({})
                self.assertEqual(ability.remaining(), 0)
                self.assertIs(self.fight.slams.casts[self.a.body], cast)
                self.assert_damage_charge(charge, 0)
                self.assertEqual(vitals.total, before)
                self.run.step({self.a: (0, 0, 0x10)})
                for _ in range(130):
                    self.run.step({})
                    if vitals.total < before:
                        break
                new = self.fight.slams.casts[self.a.body]
                self.assertNotEqual(new.activation, cast.activation)
                self.assertEqual(new.hit, (bot.entity,))
                self.assertAlmostEqual(before - vitals.total, new.damage)
                self.assert_damage_charge(charge, new.damage)
                self.assertEqual(weapon.vars[53].value(), ammo)

    def test_ground_slam_damages_pulls_and_lifts_all_five_actual_npcs(self):
        for definition in self.catalog.values():
            with self.subTest(enemy=definition.name):
                bot, vitals = self.create_enemy(definition, distance=10)
                before = vitals.total
                charge = self.charge_checkpoint()
                origin = tuple(bot.position)
                self.run.step({self.a: (0, 0, 0x10)})
                for _ in range(120):
                    self.run.step({})
                    if vitals.total < before:
                        break
                cast = self.fight.slams.casts[self.a.body]
                self.assertGreater(cast.damage, 25)
                self.assertAlmostEqual(before - vitals.total, cast.damage)
                self.settle(8)
                self.assertLess(bot.position[2], origin[2])
                self.assertGreater(bot.position[1], origin[1] + .1)
                self.settle(150)
                self.assertAlmostEqual(bot.position[1], origin[1], delta=.01)
                self.assertAlmostEqual(before - vitals.total, cast.damage)
                self.assert_damage_charge(charge, before - vitals.total)

    def test_slam_wave_duration_is_bounded_for_both_casts_against_all_five_npcs(self):
        for definition in self.catalog.values():
            for aerial in (False, True):
                with self.subTest(enemy=definition.name, aerial=aerial):
                    bot, vitals = self.create_enemy(definition, distance=10)
                    before = vitals.total
                    x, y, z = self.a.mover.position
                    rear = npc.spawn(self.match, definition, (x, y, z - 3), team=1)
                    rear_vitals = self.fight.vitals[rear.entity]
                    rear_before = rear_vitals.total
                    if aerial:
                        self.place_player(self.a, (x, y + 8, z))
                    self.run.step({self.a: (0, 9000 if aerial else 0, 0x10)})
                    start = self.run.frame
                    instance = self.fight.slams.instance(self.a)
                    ability = instance.state(0)
                    cast = self.fight.slams.casts[self.a.body]
                    self.assertTrue(ability.flags & nodes.Ability.RUNNING)
                    self.assertIsNone(cast.wave_at)
                    self.assertEqual(vitals.total, before)
                    for _ in range(120):
                        self.run.step({})
                        if cast.finished:
                            break
                    self.assertTrue(cast.finished)
                    self.assertEqual(cast.kind, 'aerial' if aerial else 'ground')
                    self.assertIsNotNone(cast.origin)
                    self.assertEqual(cast.hit, (bot.entity,))
                    self.assertEqual(rear_vitals.total, rear_before)
                    self.assertAlmostEqual(before - vitals.total, cast.damage)
                    self.assertGreater(cast.wave_at, start * instance.component.frame_ms)
                    speed = float(instance.evaluate(instance.graph.node(95).field('m_speed')))
                    reach = float(instance.vars[422].value())
                    elapsed = instance.component.now - cast.wave_at
                    self.assertGreaterEqual(elapsed, 1000 * reach / speed)
                    self.assertLess(elapsed, 1000 * reach / speed + instance.component.frame_ms)
                    self.settle(80)
                    self.assertFalse(ability.flags & nodes.Ability.RUNNING)
                    self.assertGreater(ability.remaining(), 0)
                    self.assertAlmostEqual(before - vitals.total, cast.damage)

    def test_ground_slam_wave_reach_brackets_all_five_npc_capsules(self):
        for definition in self.catalog.values():
            with self.subTest(enemy=definition.name):
                probe, _ = self.create_enemy(definition, distance=30)
                target = next(t for t in self.fight._targets() if t.owner is probe)
                self.run.step({self.a: (0, 0, 0x10)})
                cast = self.fight.slams.casts[self.a.body]
                for _ in range(120):
                    if cast.wave_at is not None:
                        break
                    self.run.step({})
                self.assertIsNotNone(cast.wave_at)
                self.assertEqual(cast.kind, 'ground')
                self.assertFalse(cast.hit)
                x, y, z = cast.origin
                instance = self.fight.slams.instance(self.a)
                reach = float(instance.vars[422].value())
                edge = reach + target.radius
                inside = npc.spawn(self.match, definition, (x, y, z + edge - .1), team=1)
                outside = npc.spawn(self.match, definition, (x, y, z + edge + .1), team=1)
                inside_pool = self.fight.vitals[inside.entity]
                outside_pool = self.fight.vitals[outside.entity]
                inside_before, outside_before = inside_pool.total, outside_pool.total
                for _ in range(60):
                    self.run.step({})
                    if cast.finished:
                        break
                self.assertTrue(cast.finished)
                self.assertEqual(cast.hit, (inside.entity,))
                self.assertAlmostEqual(inside_before - inside_pool.total, cast.damage)
                self.assertEqual(outside_pool.total, outside_before)
                notices = self.a.client.messages(combat.HIT)
                self.assertEqual([hit['+0x78']['+0x0']['+0x0'] for hit in notices], [inside.entity])
                self.assertEqual(self.own_vitals().temporary_shields, 30)

    def test_one_ground_slam_hits_seven_actual_npcs_and_caps_passive_at_150(self):
        definition = next(iter(self.catalog.values()))
        bot, _ = self.create_enemy(definition, distance=10)
        x, y, z = bot.position
        for _ in range(6):
            npc.spawn(self.match, definition, (x, y, z), team=1)
        before = {b.entity: self.fight.vitals[b.entity].total for b in self.match.bots}
        self.run.step({self.a: (0, 0, 0x10)})
        for _ in range(120):
            self.run.step({})
            if all(self.fight.vitals[e].total < hp for e, hp in before.items()):
                break
        self.assertEqual(len(self.fight.slams.casts[self.a.body].hit), 7)
        own = next(t for t in self.fight._targets() if t.owner is self.a)
        self.assertEqual(self.fight.vitals_of(own).temporary_shields, 150)

    def test_lethal_ground_slam_retains_each_actual_npc_corpse(self):
        for definition in self.catalog.values():
            with self.subTest(enemy=definition.name):
                bot, vitals = self.create_enemy(definition, distance=10)
                for kind in vitals.now:
                    vitals.now[kind] = 0
                vitals.now[combat.HEALTH_POOL] = 20
                bot.body_script.set_health(20, sum(vitals.most.values()))
                self.run.step({self.a: (0, 0, 0x10)})
                self.settle(260)
                self.assertTrue(bot.dead)
                self.assertEqual(vitals.killer, self.a.body)
                self.assertIn(bot, self.match.bots)
                self.assertFalse(any(who is bot for _, who in self.fight.respawns))
                self.assertEqual(vitals.total, 0)

    def test_ground_slam_rejects_allied_versions_of_each_actual_npc(self):
        for definition in self.catalog.values():
            with self.subTest(enemy=definition.name):
                bot, vitals = self.create_enemy(definition, team=0, distance=10)
                before, position = vitals.total, tuple(bot.position)
                self.run.step({self.a: (0, 0, 0x10)})
                self.settle(140)
                self.assertEqual(vitals.total, before)
                self.assertEqual(tuple(bot.position), position)
                own = next(t for t in self.fight._targets() if t.owner is self.a)
                self.assertEqual(self.fight.vitals_of(own).temporary_shields, 0)

    def test_actual_flashbang_interrupts_slam_before_npc_wave(self):
        for definition in self.catalog.values():
            for kind, lethal in (('ground', False), ('ground', True), ('aerial', False), ('aerial', True)):
                with self.subTest(enemy=definition.name, kind=kind, lethal=lethal):
                    _, vitals = self.create_enemy(definition, distance=10)
                    before = vitals.total
                    own = self.own_vitals()
                    self.b.team = 1
                    self.match.switch_hero(self.b, heroes()[0x02E0000000000042])
                    if lethal:
                        # Set up a low-health owner. The actual projectile still
                        # has to hit and perform the lethal transition in flight.
                        owner = next(t for t in self.fight._targets() if t.owner is self.a)
                        self.fight._damage(self.b, owner, 235, False, None, self.run.frame)
                    x, y, z = self.a.mover.position
                    if kind == 'aerial':
                        y += 8
                        self.place_player(self.a, (x, y, z))
                    self.place_player(self.b, (x + 1.5, y, z))
                    yaw, pitch = aim(self.fight._eye(self.b), self.fight._eye(self.a))
                    charge = self.charge_checkpoint()
                    self.run.step({self.a: (0, 9000 if kind == 'aerial' else 0, 0x10),
                                   self.b: (yaw, pitch, 16)})
                    cast = self.fight.slams.casts[self.a.body]
                    self.assertEqual(cast.kind, kind)
                    self.assertIsNone(cast.wave_at)
                    for _ in range(20):
                        if self.fight.is_stunned(self.a) or self.fight.is_dead(self.a):
                            break
                        self.run.step({})
                    if lethal:
                        self.assertTrue(self.fight.is_dead(self.a))
                        self.assertEqual(own.killer, self.b.body)
                        self.settle(125)
                        self.assertTrue(cast.finished)
                        self.assertIsNone(cast.wave_at)
                        self.assertFalse(cast.hit)
                        self.assertEqual(vitals.total, before)
                        self.assert_damage_charge(charge, 0)
                        self.assertFalse(own.temporary_pool_used)
                        self.assertFalse(self.a.client.messages(combat.HIT))
                        continue
                    self.assertTrue(self.fight.is_stunned(self.a))
                    ability = self.a.body_script.component.instances[8].state(0)
                    self.assertFalse(ability.flags & nodes.Ability.RUNNING)
                    self.settle(125)
                    self.assertTrue(cast.finished)
                    self.assertIsNone(cast.wave_at)
                    self.assertFalse(cast.hit)
                    self.assertEqual(vitals.total, before)
                    self.assert_damage_charge(charge, 0)
                    self.assertFalse(own.temporary_pool_used)
                    self.assertEqual(own.temporary_shields, 0)

    def test_aerial_slam_range_cancel_and_committed_release_against_all_five_npcs(self):
        for definition in self.catalog.values():
            with self.subTest(enemy=definition.name):
                bot, vitals = self.create_enemy(definition, distance=10)
                before = vitals.total
                x, y, z = self.a.mover.position
                self.place_player(self.b, (x + 15, y, z))
                script = self.a.body_script
                slam = script.component.instances[8]
                ability = slam.state(0)
                weapon = next(i for i in script.component.instances.values() if i.graph.index == 0xD0A)
                ammo = weapon.vars[53].value()
                self.place_player(self.a, (x, y + 20, z))
                charge = self.charge_checkpoint()
                self.run.step({self.a: (0, 9000, 0x10)})
                self.assertFalse(slam.vars[1627].value())
                self.assertNotIn(self.a.body, self.fight.slams.casts)
                self.run.step({})
                self.assertEqual(ability.remaining(), 0)
                self.place_player(self.a, (x, y + 8, z))
                for _ in range(8):
                    self.run.step({self.a: (0, -9000, 0x10)})
                self.assertEqual(slam.vars[59].value(), 2)
                self.assertFalse(slam.vars[1627].value())
                self.run.step({})
                self.assertFalse(ability.flags & nodes.Ability.RUNNING)
                self.assertEqual(ability.remaining(), 0)
                self.assertNotIn(self.a.body, self.fight.slams.casts)
                self.assertEqual(vitals.total, before)
                self.assertFalse(self.own_vitals().temporary_pool_used)
                self.assert_damage_charge(charge, 0)
                self.run.step({self.a: (0, 9000, 0x10)})
                cast = self.fight.slams.casts[self.a.body]
                self.assertEqual(cast.kind, 'aerial')
                destination = cast.destination
                for _ in range(100):
                    self.run.step({self.a: (16000, -9000, 0)})
                self.assertTrue(cast.finished)
                self.assertEqual(cast.destination, destination)
                self.assertEqual(cast.hit, (bot.entity,))
                self.assertAlmostEqual(before - vitals.total, cast.damage)
                self.assert_damage_charge(charge, cast.damage)
                self.assertAlmostEqual(self.a.mover.position[2], destination[2], delta=.01)
                self.assertFalse(ability.flags & nodes.Ability.RUNNING)
                self.assertGreater(ability.remaining(), 0)
                self.assertEqual(weapon.vars[53].value(), ammo)

    def test_aerial_slam_cooldown_discards_early_pulse_against_all_five_npcs(self):
        for definition in self.catalog.values():
            with self.subTest(enemy=definition.name):
                bot, vitals = self.create_enemy(definition, distance=10)
                x, y, z = self.a.mover.position
                self.place_player(self.b, (x + 15, y, z))
                ability = self.a.body_script.component.instances[8].state(0)
                self.place_player(self.a, (x, y + 8, z))
                charge = self.charge_checkpoint()
                self.run.step({self.a: (0, 9000, 0x10)})
                cast = self.fight.slams.casts[self.a.body]
                self.settle(95)
                self.assertTrue(cast.finished)
                self.assertEqual(cast.hit, (bot.entity,))
                self.assert_damage_charge(charge, cast.damage)
                self.assertGreater(ability.remaining(), 1)
                after = vitals.total
                charge = self.charge_checkpoint()
                self.place_player(self.a, (x, y + 8, bot.position[2] - 10))
                self.run.step({self.a: (0, 9000, 0x10)})
                for _ in range(500):
                    if ability.remaining() == 0:
                        break
                    self.run.step({})
                self.assertEqual(ability.remaining(), 0)
                self.settle(5)
                self.assertIs(self.fight.slams.casts[self.a.body], cast)
                self.assertEqual(vitals.total, after)
                self.assert_damage_charge(charge, 0)
                self.place_player(self.a, (x, y + 8, bot.position[2] - 10))
                self.run.step({self.a: (0, 9000, 0x10)})
                fresh = self.fight.slams.casts[self.a.body]
                self.assertEqual(fresh.kind, 'aerial')
                self.assertNotEqual(fresh.activation, cast.activation)
                self.settle(95)
                self.assertTrue(fresh.finished)
                self.assertEqual(fresh.hit, (bot.entity,))
                self.assertAlmostEqual(after - vitals.total, fresh.damage)
                self.assert_damage_charge(charge, fresh.damage)

    def test_aerial_slam_damages_pulls_and_lands_all_five_actual_npcs(self):
        for definition in self.catalog.values():
            with self.subTest(enemy=definition.name):
                bot, vitals = self.create_enemy(definition, distance=10)
                before, position = vitals.total, tuple(bot.position)
                charge = self.charge_checkpoint()
                x, y, z = self.a.mover.position
                self.place_player(self.a, (x, y + 8, z))
                self.run.step({self.a: (0, 9000, 0x10)})
                for _ in range(100):
                    self.run.step({})
                    if vitals.total < before:
                        break
                cast = self.fight.slams.casts[self.a.body]
                self.assertEqual(cast.kind, 'aerial')
                self.assertGreater(cast.damage, 25)
                self.assertAlmostEqual(before - vitals.total, cast.damage)
                self.settle(8)
                self.assertLess(bot.position[2], position[2])
                self.assertGreater(bot.position[1], position[1] + .1)
                self.settle(140)
                self.assertAlmostEqual(bot.position[1], position[1], delta=.01)
                self.assertAlmostEqual(before - vitals.total, cast.damage)
                self.assert_damage_charge(charge, before - vitals.total)

    def test_aerial_slam_wave_wall_blocks_damage_after_landing_against_all_five_npcs(self):
        for definition in self.catalog.values():
            for blocked in (False, True):
                with self.subTest(enemy=definition.name, blocked=blocked):
                    bot, vitals = self.create_enemy(definition, distance=10)
                    before = vitals.total
                    charge = self.charge_checkpoint()
                    x, y, z = self.a.mover.position
                    self.place_player(self.b, (x + 15, y, z))
                    self.place_player(self.a, (x, y + 8, z))
                    self.run.step({self.a: (0, 9000, 0x10)})
                    cast = self.fight.slams.casts[self.a.body]
                    self.assertEqual(cast.kind, 'aerial')
                    dx, dy, dz = cast.destination
                    bot.position = (dx, dy, dz + 3)
                    if blocked:
                        world = World(box(x - 30, x + 30, y - 1, y, z - 30, z + 30)
                                      + box(x - 8, x + 8, y, y + 5, dz + 1.5, dz + 1.7))
                        self.match.collision_world = world
                        for player in self.match.players:
                            player.mover.use(world=world)
                    self.settle(160)
                    self.assertTrue(cast.finished)
                    self.assertIsNotNone(cast.wave_at)
                    self.assertAlmostEqual(cast.origin[2], dz, delta=.2)
                    self.assertEqual(cast.hit, () if blocked else (bot.entity,))
                    damage = 0 if blocked else cast.damage
                    self.assertAlmostEqual(before - vitals.total, damage)
                    self.assert_damage_charge(charge, damage)
                    self.assertEqual(self.own_vitals().temporary_pool_used, not blocked)

    def test_aerial_slam_ally_rejection_and_lethal_retained_corpses_for_each_actual_npc(self):
        for definition in self.catalog.values():
            for team in (0, 1):
                with self.subTest(enemy=definition.name, team=team):
                    bot, vitals = self.create_enemy(definition, team=team, distance=10)
                    for kind in vitals.now:
                        vitals.now[kind] = 0
                    vitals.now[combat.HEALTH_POOL] = 20
                    bot.body_script.set_health(20, sum(vitals.most.values()))
                    position = tuple(bot.position)
                    x, y, z = self.a.mover.position
                    self.place_player(self.a, (x, y + 8, z))
                    self.run.step({self.a: (0, 9000, 0x10)})
                    self.settle(260)
                    self.assertEqual(vitals.total, 20 if team == 0 else 0)
                    self.assertEqual(bot.dead, team != 0)
                    self.assertIn(bot, self.match.bots)
                    self.assertFalse(any(who is bot for _, who in self.fight.respawns))
                    if team == 0:
                        self.assertEqual(tuple(bot.position), position)
                        self.assertFalse(self.own_vitals().temporary_pool_used)

    def prepare_incoming(self, definition, distance=10):
        bot, _ = self.create_enemy(definition, distance=distance)
        x, y, z = self.a.mover.position
        self.place_player(self.b, (x + 20, y, z + 10))
        # Authoritative weapon/damage acceptance only. This adapter does not
        # claim NPC projectile observer transport or original-client visuals.
        for player in self.match.players:
            player.client.bot_stream = lambda entity: None
        own = next(t for t in self.fight._targets() if t.owner is self.a)
        return bot, self.fight.vitals_of(own)

    @unittest.skipUnless(os.environ.get('ORPHEUS_REINHARDT_COLLISION'), 'requires audited barrier geometry')
    def test_actual_npc_contact_with_active_allied_defenses(self):
        for name, mode, damage in (
                ('Trooper', 'melee', 30), ('Heavy Assault', 'melee', 80), ('Heavy Assault', 'charge', 50)):
            definition = next(d for d in self.catalog.values() if d.name == name)
            for hero, button, defense in (
                    (0x02E0000000000007, 2, 'barrier'),
                    (defenses.GENJI, 16, 'deflect'),
                    (defenses.SIGMA, 8, 'grasp')):
                with self.subTest(npc=name, mode=mode, defense=defense):
                    bot, own = self.prepare_incoming(definition, distance=2)
                    self.match.switch_hero(self.b, heroes()[hero])
                    command = {self.b: (0, 0, button)}
                    host = self.fight.hand_cannon_barriers
                    if defense == 'barrier':
                        for _ in range(400):
                            if host.sync(self.b).active:
                                break
                            self.run.step(command)
                        self.assertTrue(host.sync(self.b).active)
                    self.assertEqual(npc_control.start(self.match, self.a, **{mode: True}), bot.entity)
                    if mode == 'charge' and defense != 'barrier':
                        self.settle(38)
                    x, y, z = self.a.mover.position
                    self.place_player(self.b, (x + 1.4, y, z + .2) if defense == 'barrier' else (x, y, z + 1))
                    self.run.step(command)
                    origin = (x, bot.position[1] + combat.eye_height(bot.body), bot.position[2])
                    delta = tuple(a - b for a, b in zip(self.fight._eye(self.a), origin, strict=True))
                    length = sum(v * v for v in delta) ** .5
                    heading = tuple(v / length for v in delta)
                    if defense == 'barrier':
                        shield = host.ray_contact(bot, bot.team, origin, heading, length)
                        self.assertIsNotNone(shield)
                        pool = shield[2]
                        self.assertEqual(pool.current, 1600)
                    else:
                        self.assertIsNotNone(defenses.ray_interception(
                            self.fight, bot, bot.team, origin, heading, length, absorbing=defense == 'grasp',
                        ))
                    for _ in range(65):
                        self.run.step(command if defense == 'barrier' else {})
                        if own.total < 250:
                            break
                    self.assertEqual(own.total, 250 - damage)
                    if mode == 'charge':
                        self.assertEqual(self.fight.pins, {bot.entity: self.a.body})
                    if defense == 'barrier':
                        self.assertTrue(pool.active)
                        self.assertEqual(pool.current, 1600)
                    elif defense == 'grasp':
                        self.assertEqual(defenses.grasp(self.b).vars[21312].value(), 0)
                    else:
                        self.assertIsNotNone(defenses.deflect(self.b))
                        self.assertEqual(self.b.stats.value(stats.HERO_DAMAGE), 0)
                    npc_control.stop(self.match)

    def test_actual_heavy_charge_carries_doomfist_without_crossing_wall(self):
        definition = next(d for d in self.catalog.values() if d.name == 'Heavy Assault')
        bot, own = self.prepare_incoming(definition, distance=2)
        x, y, z = self.a.mover.position
        world = World(box(x - 30, x + 30, y - 1, y, z - 30, z + 30)
                      + box(x - 4, x + 4, y, y + 5, z - 3.2, z - 3))
        self.match.collision_world = world
        for player in self.match.players:
            player.mover.use(world=world)
        npc_control.start(self.match, self.a, charge=True)
        self.settle(61)
        self.assertEqual(self.fight.pins, {bot.entity: self.a.body})
        self.assertLess(self.a.mover.position[2], z - 2)
        self.assertGreater(self.a.mover.position[2], z - 3)
        self.assertGreater(bot.position[2], z - 3)
        self.assertEqual(own.total, 200)
        npc_control.stop(self.match)
        stopped = self.a.mover.position[2]
        self.settle(10)
        self.assertEqual(self.a.mover.position[2], stopped)
        self.assertFalse(self.fight.is_stunned(self.a))

    def test_actual_flashbang_cancels_heavy_charge_without_stale_resume(self):
        definition = next(d for d in self.catalog.values() if d.name == 'Heavy Assault')
        bot, own = self.prepare_incoming(definition, distance=2)
        self.match.switch_hero(self.b, heroes()[0x02E0000000000042])
        npc_control.start(self.match, self.a, charge=True)
        self.settle(55)
        self.assertEqual(self.fight.pins, {bot.entity: self.a.body})
        x, y, z = bot.position
        self.place_player(self.b, (x + 1.5, y, z))
        self.run.step({self.b: (*aim(self.fight._eye(self.b), (x, y + .7, z)), 16)})
        for _ in range(20):
            if self.fight.is_stunned(bot):
                break
            self.run.step({})
        self.assertTrue(self.fight.is_stunned(bot))
        self.assertFalse(self.fight.pins)
        self.assertIsNotNone(bot.cancelled_charge)
        self.settle(2)
        stopped = bot.position[2]
        self.settle(200)
        self.assertFalse(self.fight.is_stunned(bot))
        self.assertEqual(bot.position[2], stopped)
        self.assertEqual(own.total, 200)
        self.assertFalse(self.fight.pins)
        instance = next(i for i in bot.body_script.component.instances.values() if i.graph.index == 0x1408)
        ability = instance.state(instance.graph.node(82).state)
        self.assertGreater(ability.remaining(), 13)
        npc_control.stop(self.match)
        x, y, z = bot.position
        self.run.step({self.a: (*aim(self.fight._eye(self.a), (x, y + 1.4, z)), 0)})
        npc_control.start(self.match, self.a, charge=True)
        self.settle(70)
        self.assertEqual(bot.position[2], stopped)
        self.assertEqual(own.total, 200)
        npc_control.stop(self.match)
        self.settle(900)
        self.assertEqual(ability.remaining(), 0)
        x, y, z = bot.position
        self.run.step({self.a: (*aim(self.fight._eye(self.a), (x, y + 1.4, z)), 0)})
        npc_control.start(self.match, self.a, charge=True)
        self.settle(65)
        self.assertLess(bot.position[2], stopped)

    def test_meteor_protection_blocks_heavy_charge_and_wall_impact_until_landing(self):
        definition = next(d for d in self.catalog.values() if d.name == 'Heavy Assault')
        bot, own = self.prepare_incoming(definition, distance=2)
        x, y, z = self.a.mover.position
        world = World(box(x - 30, x + 30, y - 1, y, z - 30, z + 30)
                      + box(x - 4, x + 4, y, y + 5, z - 3.2, z - 3))
        self.match.collision_world = world
        for player in self.match.players:
            player.mover.use(world=world)
        charge_by_neutral_commands(self)
        self.run.step({self.a: (0, 0, 32)})
        self.assertEqual(npc_control.start(self.match, self.a, charge=True), bot.entity)
        charge = next(i for i in bot.body_script.component.instances.values() if i.graph.index == 0x1408)
        contact = charge.state(charge.graph.node(168).state)
        impact = charge.state(charge.graph.node(29).state)
        for _ in range(70):
            self.run.step({})
            self.assertTrue(self.fight.protected(self.a.body))
            self.assertEqual(own.total, 250)
            self.assertFalse(self.fight.pins)
        self.assertLess(bot.position[2], z + 1)
        self.assertEqual((contact.counter, impact.counter), (0, 0))

        self.run.step({self.a: (0, 0, 1)})
        cast = self.fight.meteors.casts[self.a.body]
        for _ in range(110):
            self.run.step({})
            if cast.impacted:
                break
            self.assertTrue(self.fight.protected(self.a.body))
            self.assertEqual(own.total, 250)
            self.assertFalse(self.fight.pins)
        self.assertTrue(cast.impacted)
        self.assertFalse(self.fight.protected(self.a.body))
        self.assertEqual(own.now[combat.HEALTH_POOL], 250)
        self.assertEqual((contact.counter, impact.counter), (0, 0))

    def test_meteor_descent_rejects_heavy_charge_contact(self):
        definition = next(d for d in self.catalog.values() if d.name == 'Heavy Assault')
        bot, own = self.prepare_incoming(definition, distance=2)
        x, y, z = self.a.mover.position
        world = World(box(x - 30, x + 30, y - 1, y, z - 30, z + 30)
                      + box(x - 4, x + 4, y, y + 5, z - 3.2, z - 3))
        self.match.collision_world = world
        for player in self.match.players:
            player.mover.use(world=world)
        charge_by_neutral_commands(self)
        self.run.step({self.a: (0, 0, 32)})
        self.settle(70)
        cast = self.fight.meteors.casts[self.a.body]
        self.assertFalse(cast.committed)
        self.assertEqual(npc_control.start(self.match, self.a, charge=True), bot.entity)
        charge = next(i for i in bot.body_script.component.instances.values() if i.graph.index == 0x1408)
        contact = charge.state(charge.graph.node(168).state)
        impact = charge.state(charge.graph.node(29).state)
        self.settle(38)
        self.assertEqual(bot.position[2], z + 2)
        self.run.step({self.a: (0, 0, 1)})
        self.assertTrue(cast.committed)
        self.assertFalse(cast.impacted)

        crossed = False
        for _ in range(110):
            self.run.step({})
            self.assertEqual(own.now[combat.HEALTH_POOL], 250)
            self.assertFalse(self.fight.pins)
            self.assertEqual((contact.counter, impact.counter), (0, 0))
            if cast.impacted:
                break
            self.assertTrue(self.fight.protected(self.a.body))
            self.assertEqual(self.a.mover.position[2], z)
            crossed |= bot.position[2] < z
        self.assertTrue(crossed)
        self.assertTrue(cast.impacted)
        self.assertFalse(self.fight.protected(self.a.body))

    def test_actual_heavy_charge_wall_impact_kills_and_releases(self):
        definition = next(d for d in self.catalog.values() if d.name == 'Heavy Assault')
        bot, own = self.prepare_incoming(definition, distance=2)
        x, y, z = self.a.mover.position
        world = World(box(x - 30, x + 30, y - 1, y, z - 30, z + 30)
                      + box(x - 4, x + 4, y, y + 5, z - 3.2, z - 3))
        self.match.collision_world = world
        for player in self.match.players:
            player.mover.use(world=world)
        npc_control.start(self.match, self.a, charge=True)
        self.settle(57)
        self.assertEqual(own.total, 200)
        self.assertEqual(self.fight.pins, {bot.entity: self.a.body})
        self.settle(24)
        self.assertTrue(self.fight.is_dead(self.a))
        self.assertEqual(own.killer, bot.entity)
        self.assertFalse(self.fight.pins)
        charge = next(i for i in bot.body_script.component.instances.values() if i.graph.index == 0x1408)
        impact = charge.state(charge.graph.node(29).state)
        self.assertEqual(impact.counter, 1)
        amount = impact.node.field('m_modifyHealth')['m_amount']
        self.assertEqual(impact.evaluate(amount, watch=False), 300)
        self.settle(175)
        self.assertEqual(impact.counter, 1)
        self.assertEqual(self.a.stats.value(stats.DEATHS), 1)
        self.assertGreater(charge.state(charge.graph.node(82).state).remaining(), 14)

    def test_heavy_charge_contact_and_wall_impact_consume_passive_shields(self):
        heavy = next(d for d in self.catalog.values() if d.name == 'Heavy Assault')
        bot, own = self.prepare_incoming(heavy, distance=2)
        x, y, z = self.a.mover.position
        targets = [npc.spawn(self.match, heavy, (x + 1.5, y, z), team=1) for _ in range(3)]
        before = {target.entity: self.fight.vitals[target.entity].total for target in targets}
        yaw, pitch = aim(self.fight._eye(self.a), (x + 1.5, y + 1, z))
        self.run.step({self.a: (yaw, pitch, 8)})
        for entity, health in before.items():
            self.assertEqual(health - self.fight.vitals[entity].total, 50)
        self.assertEqual(own.temporary_shields, 90)
        deadline = own.temporary_decay_at
        self.place_player(self.a, (x, y, z))
        self.a.mover.yaw, self.a.mover.pitch = aim(
            self.fight._eye(self.a), (bot.position[0], bot.position[1] + 1, bot.position[2]))
        world = World(box(x - 30, x + 30, y - 1, y, z - 30, z + 30)
                      + box(x - 4, x + 4, y, y + 5, z - 3.2, z - 3))
        self.match.collision_world = world
        for player in self.match.players:
            player.mover.use(world=world)
        self.assertEqual(npc_control.start(self.match, self.a, charge=True), bot.entity)
        self.settle(57)
        self.assertEqual(self.fight.pins, {bot.entity: self.a.body})
        self.assertEqual(sum(own.now.values()), 250)
        self.assertEqual(own.temporary_shields, 40)
        self.assertEqual(own.temporary_decay_at, deadline)

        for _ in range(24):
            shields_before_impact = own.temporary_shields
            self.run.step({})
            if own.dead:
                break
        self.assertTrue(own.dead)
        self.assertGreater(shields_before_impact, 0)
        self.assertEqual(own.temporary_shields, 0)
        self.assertEqual(sum(own.now.values()), 0)
        self.assertEqual(own.killer, bot.entity)
        self.assertFalse(self.fight.pins)

        old_body = self.a.body
        npc_control.stop(self.match)
        respawn_at = next(when for when, player in self.fight.respawns if player is self.a)
        self.assertAlmostEqual(respawn_at - own.died_at, 10)
        while (self.run.frame + 1) * combat.TICK < respawn_at:
            self.run.step({})
            self.assertEqual(self.a.body, old_body)
        for _ in range(2):
            self.run.step({})
            if self.a.body != old_body:
                break
        self.assertNotEqual(self.a.body, old_body)
        self.assertLessEqual(abs(self.fight.now - respawn_at), combat.TICK + 1e-8)
        self.assertNotIn(old_body, self.fight.vitals)
        fresh = self.own_vitals()
        self.assertEqual(fresh.total, 250)
        self.assertEqual(fresh.temporary_shields, 0)
        self.assertEqual(fresh.temporary_decay_at, 0)
        self.assertFalse(fresh.temporary_pool_used)
        self.assertFalse(self.fight.pins)

        self.place_player(self.a, (x - 15, y, z))
        target = npc.spawn(self.match, heavy, (x - 16.5, y, z), team=1)
        victim = self.fight.vitals[target.entity]
        health = victim.total
        yaw, pitch = aim(self.fight._eye(self.a), (x - 16.5, y + 1, z))
        self.run.step({self.a: (yaw, pitch, 8)})
        self.assertEqual(health - victim.total, 50)
        self.assertEqual(fresh.temporary_shields, 30)
        self.assertAlmostEqual(fresh.temporary_decay_at, self.fight.now + 1)
        self.assertEqual(own.total, 0)

    def test_actual_heavy_charge_pin_controls_and_stop_releases_doomfist(self):
        definition = next(d for d in self.catalog.values() if d.name == 'Heavy Assault')
        bot, own = self.prepare_incoming(definition, distance=2)
        npc_control.start(self.match, self.a, charge=True)
        for _ in range(70):
            self.run.step({})
            if self.fight.is_stunned(self.a):
                break
        self.assertEqual(self.fight.pins, {bot.entity: self.a.body})
        self.assertEqual(own.total, 200)
        before = self.fight.vitals[bot.entity].total
        self.fire()
        self.assertTrue(self.fight.is_stunned(self.a))
        self.assertEqual(self.fight.vitals[bot.entity].total, before)
        npc_control.stop(self.match)
        self.assertFalse(self.fight.is_stunned(self.a))
        self.assertFalse(self.fight.pins)
        x, y, z = bot.position
        self.run.step({self.a: (*aim(self.fight._eye(self.a), (x, y + 1.4, z)), 0)})
        npc_control.start(self.match, self.a, charge=True)
        self.settle(3)
        self.assertFalse(self.fight.pins)
        self.assertEqual(bot.position[2], z)

    def test_actual_heavy_charge_graph_observes_low_obstacle(self):
        definition = next(d for d in self.catalog.values() if d.name == 'Heavy Assault')
        for height in (.7, 3):
            with self.subTest(height=height):
                bot, _ = self.prepare_incoming(definition, distance=8)
                x, y, z = self.a.mover.position
                npc_control.start(self.match, self.a, charge=True)
                self.settle(10)
                world = World(box(x - 30, x + 30, y - 1, y, z - 30, z + 30)
                              + box(x - 4, x + 4, y - .1, y + height, z + 4, z + 4.2))
                self.match.collision_world = world
                for player in self.match.players:
                    player.mover.use(world=world)
                charge = next(i for i in bot.body_script.component.instances.values()
                              if i.graph.index == 0x1408)
                for _ in range(100):
                    self.run.step({})
                    short = charge.state(charge.graph.node(170).state)
                    high = charge.state(charge.graph.node(172).state)
                    if short.hit:
                        break
                self.assertTrue(short.hit)
                self.assertEqual(high.hit, height > 1)
                self.settle(3)
                self.assertEqual(charge.state(charge.graph.node(160).state).active, height < 1)

    def test_actual_heavy_charge_moves_with_graph_speed_and_stops_at_wall(self):
        definition = next(d for d in self.catalog.values() if d.name == 'Heavy Assault')
        for wall in (False, True):
            with self.subTest(wall=wall):
                bot, own = self.prepare_incoming(definition, distance=8)
                start_z = bot.position[2]
                npc_control.start(self.match, self.a, charge=True)
                self.settle(35)
                self.assertEqual(bot.position[2], start_z)
                if wall:
                    self.install_wall()
                self.settle(29)
                self.assertGreater(start_z - bot.position[2], 2)
                self.assertAlmostEqual(bot.velocity[2], -15, delta=.25)
                if wall:
                    self.settle(55)
                    self.assertGreater(bot.position[2], self.a.mover.position[2] + 1.4)
                    self.assertAlmostEqual(bot.velocity[2], 0, delta=.25)
                npc_control.stop(self.match)
                stopped = bot.position[2]
                self.settle(10)
                self.assertEqual(bot.position[2], stopped)
                self.assertEqual(own.total, 250)

    def test_actual_heavy_charge_health_branch_applies_contact_damage(self):
        definition = next(d for d in self.catalog.values() if d.name == 'Heavy Assault')
        bot, own = self.prepare_incoming(definition, distance=2)
        self.assertEqual(npc_control.start(self.match, self.a, charge=True), bot.entity)
        for _ in range(90):
            self.run.step({})
            if own.total < 250:
                break
        self.assertIn(35, bot.body_script.component.held)
        self.assertEqual(own.total, 200)
        charge = next(i for i in bot.body_script.component.instances.values() if i.graph.index == 0x1408)
        self.assertTrue(charge.state(charge.graph.node(168).state).active)
        self.assertFalse(charge.state(charge.graph.node(170).state).active)
        npc_control.stop(self.match)
        self.settle(90)
        self.assertEqual(own.total, 200)

    def test_actual_heavy_charge_contact_rejects_range_cover_and_stop(self):
        definition = next(d for d in self.catalog.values() if d.name == 'Heavy Assault')
        for gate in ('range', 'cover', 'stop'):
            with self.subTest(gate=gate):
                bot, own = self.prepare_incoming(definition, distance=24 if gate == 'range' else 2)
                npc_control.start(self.match, self.a, charge=True)
                self.settle(8)
                self.assertIn(35, bot.body_script.component.held)
                if gate == 'cover':
                    self.install_wall()
                elif gate == 'stop':
                    npc_control.stop(self.match)
                self.settle(80)
                self.assertEqual(own.total, 250)
                instance = next(i for i in bot.body_script.component.instances.values()
                                if i.graph.index == 0x1408)
                self.assertEqual(instance.state(instance.graph.node(35).state).counter, 1)
                self.assertFalse(instance.state(instance.graph.node(168).state).active)
                self.assertTrue(instance.state(instance.graph.node(170).state).active)

    def test_actual_heavy_charge_hits_during_movement_only_once(self):
        definition = next(d for d in self.catalog.values() if d.name == 'Heavy Assault')
        bot, own = self.prepare_incoming(definition, distance=8)
        start_z = bot.position[2]
        npc_control.start(self.match, self.a, charge=True)
        self.settle(54)
        self.assertEqual(own.total, 250)
        for _ in range(45):
            self.run.step({})
            if own.total < 250:
                break
        self.assertGreater(start_z - bot.position[2], 4)
        self.assertEqual(own.total, 200)
        self.settle(65)
        self.assertEqual(own.total, 200)

    def test_actual_npc_melee_graphs_damage_doomfist_and_stop(self):
        for name, amount in (('Trooper', 30), ('Heavy Assault', 80)):
            with self.subTest(enemy=name):
                definition = next(d for d in self.catalog.values() if d.name == name)
                bot, vitals = self.prepare_incoming(definition, distance=2)
                self.assertEqual(npc_control.start(self.match, self.a, melee=True), bot.entity)
                for _ in range(80):
                    self.run.step({})
                    if vitals.total < 250:
                        break
                self.assertEqual(250 - vitals.total, amount)
                self.assertIn(31, bot.body_script.component.held)
                self.assertNotIn(20, bot.body_script.component.held)
                npc_control.stop(self.match)
                self.settle(100)
                self.assertEqual(vitals.total, 250 - amount)
                self.assertNotIn(31, bot.body_script.component.held)

    def test_actual_npc_melee_cooldown_rejects_repress_then_reopens(self):
        for name, amount, graph_id, ability_position in (
                ('Trooper', 30, 0x1422, 8), ('Heavy Assault', 80, 0x140E, 9)):
            with self.subTest(enemy=name):
                definition = next(d for d in self.catalog.values() if d.name == name)
                bot, vitals = self.prepare_incoming(definition, distance=2)
                npc_control.start(self.match, self.a, melee=True)
                for _ in range(55):
                    self.run.step({})
                    if vitals.total < 250:
                        break
                self.assertEqual(vitals.total, 250 - amount)
                instance = next(i for i in bot.body_script.component.instances.values()
                                if i.graph.index == graph_id)
                ability = instance.states[instance.graph.node(ability_position).state]
                self.assertGreater(ability.remaining(), 0.25)
                npc_control.stop(self.match)
                self.settle(1)
                npc_control.start(self.match, self.a, melee=True)
                self.settle(2)
                self.assertIn(31, bot.body_script.component.held)
                self.assertGreater(ability.remaining(), 0.25)
                npc_control.stop(self.match)
                self.settle(100)
                self.assertEqual(vitals.total, 250 - amount)
                self.assertEqual(ability.remaining(), 0)
                npc_control.start(self.match, self.a, melee=True)
                for _ in range(55):
                    self.run.step({})
                    if vitals.total < 250 - amount:
                        break
                self.assertEqual(vitals.total, 250 - 2 * amount)

    def test_actual_npc_melee_misses_range_and_rejects_wall_and_ally(self):
        for name in ('Trooper', 'Heavy Assault'):
            definition = next(d for d in self.catalog.values() if d.name == name)
            for gate in ('range', 'wall', 'ally'):
                with self.subTest(enemy=name, gate=gate):
                    bot, vitals = self.prepare_incoming(definition, distance=8 if gate == 'range' else 2)
                    npc_control.start(self.match, self.a, melee=True)
                    self.settle(4)
                    self.assertIn(31, bot.body_script.component.held)
                    if gate == 'wall':
                        self.install_wall()
                    elif gate == 'ally':
                        bot.team = self.a.team
                    self.settle(90)
                    self.assertEqual(vitals.total, 250)
                    self.assertEqual(31 in bot.body_script.component.held, gate == 'range')

    def test_actual_npc_melee_spends_uppercut_shields_first(self):
        for name, mode, amount in (
                ('Trooper', 'melee', 30), ('Heavy Assault', 'melee', 80), ('Heavy Assault', 'charge', 50)):
            with self.subTest(enemy=name, mode=mode):
                definition = next(d for d in self.catalog.values() if d.name == name)
                bot, own = self.prepare_incoming(definition, distance=2)
                x, y, z = self.a.mover.position
                self.b.team = 1
                self.place_player(self.b, (x + 1.5, y, z))
                yaw, pitch = aim(self.fight._eye(self.a), (x + 1.5, y + 1, z))
                self.run.step({self.a: (yaw, pitch, 8)})
                self.assertEqual(own.temporary_shields, 30)
                self.place_player(self.a, (x, y, z))
                self.place_player(self.b, (x + 20, y, z))
                self.run.step({self.a: (*aim(self.fight._eye(self.a), (x, y + 1.4, z + 2)), 0)})
                self.assertEqual(npc_control.start(self.match, self.a, **{mode: True}), bot.entity)
                for _ in range(65):
                    self.run.step({})
                    if own.total < 280:
                        break
                self.assertEqual(own.total, 280 - amount)
                self.assertEqual(own.temporary_shields, 0)

    def test_actual_npc_melee_cannot_damage_airborne_meteor_doomfist(self):
        for name, mode, button in (
                ('Trooper', 'melee', 31), ('Heavy Assault', 'melee', 31), ('Heavy Assault', 'charge', 35)):
            with self.subTest(enemy=name, mode=mode):
                definition = next(d for d in self.catalog.values() if d.name == name)
                bot, own = self.prepare_incoming(definition, distance=2)
                charge_by_neutral_commands(self)
                self.run.step({self.a: (0, 0, 32)})
                self.assertTrue(self.fight.protected(self.a.body))
                self.assertEqual(npc_control.start(self.match, self.a, **{mode: True}), bot.entity)
                self.settle(65)
                self.assertTrue(self.fight.protected(self.a.body))
                self.assertIn(button, bot.body_script.component.held)
                self.assertEqual(own.total, 250)
                self.assertFalse(self.fight.pins)

    def test_meteor_descent_filters_in_range_npc_melee_queries(self):
        for name in ('Trooper', 'Heavy Assault'):
            with self.subTest(enemy=name):
                definition = next(d for d in self.catalog.values() if d.name == name)
                bot, own = self.prepare_incoming(definition, distance=2)
                graph_id, query_position, _ = npc_melee.GRAPHS[bot.body]
                charge_by_neutral_commands(self)
                self.run.step({self.a: (0, 0, 32)})
                self.settle(70)
                self.run.step({self.a: (0, 0, 1)})
                cast = self.fight.meteors.casts[self.a.body]
                self.assertTrue(cast.committed)
                self.assertFalse(cast.impacted)
                self.assertEqual(npc_control.start(self.match, self.a, melee=True), bot.entity)
                track = npc_melee.track
                contacts = []

                def observe(world, state):
                    result = track(world, state)
                    if (state.instance.component.entity == bot.entity
                            and state.instance.graph.index == graph_id
                            and state.node.pos == query_position):
                        target = next(t for t in self.fight._targets() if t.owner is self.a)
                        self.assertTrue(self.fight.protected(target.entity))
                        self.assertFalse(cast.impacted)
                        self.assertTrue(npc_melee.contact(self.fight, bot, target, state))
                        contacts.append(result)
                    return result

                with patch.object(npc_melee, 'track', new=observe):
                    for _ in range(90):
                        self.run.step({})
                        self.assertEqual(own.now[combat.HEALTH_POOL], 250)
                        self.assertFalse(self.fight.pins)
                        if cast.impacted:
                            break
                self.assertTrue(contacts)
                self.assertTrue(all(result == () for result in contacts))
                self.assertTrue(cast.impacted)
                self.assertFalse(self.fight.protected(self.a.body))
                npc_control.stop(self.match)

    def test_flashbang_blocks_and_releases_all_five_actual_npc_attacks(self):
        for definition in self.catalog.values():
            with self.subTest(enemy=definition.name):
                bot, own = self.prepare_incoming(definition, distance=2)
                self.match.switch_hero(self.b, heroes()[0x02E0000000000042])
                x, y, z = bot.position
                self.place_player(self.b, (x + 1.5, y, z))
                yaw, pitch = aim(self.fight._eye(self.b), (x, y + .7, z))
                self.run.step({self.b: (yaw, pitch, 16)})
                for _ in range(20):
                    if self.fight.is_stunned(bot):
                        break
                    self.run.step({})
                self.assertTrue(self.fight.is_stunned(bot))
                self.assertEqual(npc_control.start(self.match, self.a), bot.entity)
                button = npc_control.button(bot)
                until = self.fight.stuns[bot.entity]
                while self.run.frame + 1 < until:
                    self.run.step({})
                    self.assertNotIn(button, bot.body_script.component.held)
                    self.assertEqual(own.total, 250)
                for _ in range(160):
                    self.run.step({})
                    if own.total < 250:
                        break
                self.assertFalse(self.fight.is_stunned(bot))
                self.assertIn(button, bot.body_script.component.held)
                self.assertLess(own.total, 250)
                self.assertEqual(self.fight.is_stunned(self.a), definition.name == 'Assassin')
                npc_control.stop(self.match)

    def test_ana_sleep_dart_blocks_all_five_npc_attacks_until_expiry(self):
        for definition in self.catalog.values():
            with self.subTest(enemy=definition.name):
                bot, own = self.prepare_incoming(definition, distance=2)
                enemy = self.fight.vitals[bot.entity]
                before = enemy.total
                self.match.switch_hero(self.b, heroes()[0x02E000000000013B])
                x, y, z = bot.position
                self.place_player(self.b, (x + 3, y, z))
                yaw, pitch = aim(self.fight._eye(self.b), (x, y + .9, z))
                for frame in range(60):
                    self.run.step({self.b: (yaw, pitch, 8 if frame == 0 else 0)})
                    if self.fight.is_stunned(bot):
                        break
                self.assertTrue(self.fight.is_stunned(bot))
                self.assertEqual(enemy.total, before - 5)
                sleep = self.fight.ana_sleeps[bot.entity]
                self.assertEqual(npc_control.start(self.match, self.a), bot.entity)
                button = npc_control.button(bot)
                while self.run.frame + 1 < sleep["end"]:
                    self.run.step({})
                    self.assertNotIn(button, bot.body_script.component.held)
                    self.assertEqual(own.total, 250)
                self.assertTrue(self.fight.is_stunned(bot))
                self.run.step({})
                self.assertFalse(self.fight.is_stunned(bot))
                for _ in range(160):
                    if own.total < 250:
                        break
                    self.run.step({})
                self.assertLess(own.total, 250)
                npc_control.stop(self.match)

    def test_sombra_hack_npc_receiver_lifetime_and_weapon_scope(self):
        for definition in self.catalog.values():
            with self.subTest(enemy=definition.name):
                bot, own = self.prepare_incoming(definition, distance=2)
                self.match.switch_hero(self.b, heroes()[sombra_hack.SOMBRA])
                x, y, z = bot.position
                self.place_player(self.b, (x + 3, y, z))
                aim_at_bot = aim(self.fight._eye(self.b), (x, y + .9, z))
                component = bot.body_script.component
                if definition.name == 'Enforcer':
                    # Isolate the authored reject condition, then clear it for the positive case.
                    component.set(sombra_hack.HACK_REJECT, True)
                    for _ in range(65):
                        self.run.step({self.b: (*aim_at_bot, 2)})
                    self.assertFalse(sombra_hack.locked(bot))
                    self.run.step({})
                    component.set(sombra_hack.HACK_REJECT, False)
                if definition.name == 'Assassin':
                    self.assertEqual(npc_control.start(self.match, self.a), bot.entity)
                    self.run.step({})
                    self.assertEqual(self.fight.pins.get(bot.entity), self.a.body)
                for _ in range(65):
                    self.run.step({self.b: (*aim_at_bot, 2)})
                    if sombra_hack.locked(bot):
                        break
                self.assertTrue(sombra_hack.locked(bot))
                receiver = sombra_hack.instance_for(bot, 0x0B1F)
                self.assertEqual(receiver.source_entity(), self.b.body)
                self.assertEqual(receiver.states[58].ms, 5000)
                self.assertNotIn(bot.entity, self.fight.pins)
                after = own.total
                if definition.name != 'Assassin':
                    self.assertEqual(npc_control.start(self.match, self.a), bot.entity)
                    for _ in range(200):
                        self.run.step({})
                        if own.total < after:
                            break
                    self.assertTrue(sombra_hack.locked(bot))
                    self.assertLess(own.total, after)
                if definition.name == 'Heavy Assault':
                    self.assertEqual(npc_control.start(self.match, self.a, charge=True), bot.entity)
                    self.settle(15)
                    self.assertTrue(sombra_hack.locked(bot))
                    self.assertFalse(bot.attack_input)
                    self.assertNotIn(bot.attack_special, component.held)
                    self.assertNotIn(bot.entity, self.fight.pins)
                    npc_control.stop(self.match)
                    before_charge = own.total
                for _ in range(330):
                    if not sombra_hack.locked(bot):
                        break
                    self.run.step({})
                    if definition.name == 'Assassin' and sombra_hack.locked(bot):
                        self.assertNotIn(bot.entity, self.fight.pins)
                        self.assertEqual(own.total, after)
                self.assertFalse(sombra_hack.locked(bot))
                if definition.name == 'Heavy Assault':
                    self.assertEqual(own.total, before_charge)
                    self.assertFalse(self.fight.pins)
                    self.assertEqual(npc_control.start(self.match, self.a, charge=True), bot.entity)
                    for _ in range(90):
                        if self.fight.pins.get(bot.entity) == self.a.body:
                            break
                        self.run.step({})
                    self.assertEqual(self.fight.pins.get(bot.entity), self.a.body)
                    self.assertEqual(own.total, before_charge - 50)
                if definition.name == 'Assassin':
                    for _ in range(45):
                        if own.total < after:
                            break
                        self.run.step({})
                    self.assertEqual(self.fight.pins.get(bot.entity), self.a.body)
                    self.assertLess(own.total, after)
                npc_control.stop(self.match)

    def test_mei_spray_freezes_all_five_npc_attacks_until_expiry(self):
        for definition in self.catalog.values():
            with self.subTest(enemy=definition.name):
                bot, own = self.prepare_incoming(definition, distance=2)
                enemy = self.fight.vitals[bot.entity]
                before = enemy.total
                self.match.switch_hero(self.b, heroes()[0x02E00000000000DD])
                x, y, z = bot.position
                self.place_player(self.b, (x + 3, y, z))
                yaw, pitch = aim(self.fight._eye(self.b), (x, y + .9, z))
                for _ in range(180):
                    self.run.step({self.b: (yaw, pitch, 1)})
                    if self.fight.is_stunned(bot):
                        break
                self.assertTrue(self.fight.is_stunned(bot))
                self.assertGreater(enemy.total, 0)
                self.assertLess(enemy.total, before)
                frost = self.fight.frost[bot.entity]
                deadline = frost.until
                self.assertEqual(deadline - self.run.frame, 82)
                self.assertEqual(npc_control.start(self.match, self.a), bot.entity)
                button = npc_control.button(bot)
                position = tuple(bot.position)
                for _ in range(5):
                    self.run.step({self.b: (yaw, pitch, 1)})
                self.assertEqual(frost.until, deadline)
                while self.run.frame + 1 < deadline:
                    self.run.step({})
                    self.assertNotIn(button, bot.body_script.component.held)
                    self.assertEqual(tuple(bot.position), position)
                    self.assertEqual(own.total, 250)
                self.assertTrue(self.fight.is_stunned(bot))
                self.run.step({})
                self.assertFalse(self.fight.is_stunned(bot))
                self.assertNotIn(bot.entity, self.fight.frost)
                for _ in range(160):
                    if own.total < 250:
                        break
                    self.run.step({})
                self.assertLess(own.total, 250)
                npc_control.stop(self.match)

    def test_actual_flashbang_interrupts_npc_melee_windup(self):
        for name, amount, graph_id, position in (
                ('Trooper', 30, 0x1422, 8), ('Heavy Assault', 80, 0x140E, 9)):
            with self.subTest(enemy=name):
                definition = next(d for d in self.catalog.values() if d.name == name)
                bot, own = self.prepare_incoming(definition, distance=2)
                self.match.switch_hero(self.b, heroes()[0x02E0000000000042])
                x, y, z = bot.position
                self.place_player(self.b, (x + 1.5, y, z))
                npc_control.start(self.match, self.a, melee=True)
                self.settle(3)
                instance = next(i for i in bot.body_script.component.instances.values()
                                if i.graph.index == graph_id)
                ability = instance.states[instance.graph.node(position).state]
                for _ in range(9):
                    if ability.flags & nodes.Ability.RUNNING:
                        break
                    self.run.step({})
                self.assertTrue(ability.flags & nodes.Ability.RUNNING)
                self.assertEqual(own.total, 250)
                yaw, pitch = aim(self.fight._eye(self.b), (x, y + .7, z))
                self.run.step({self.b: (yaw, pitch, 16)})
                for _ in range(20):
                    if self.fight.is_stunned(bot):
                        break
                    self.run.step({})
                self.assertTrue(self.fight.is_stunned(bot))
                self.assertFalse(ability.flags & nodes.Ability.RUNNING)
                while self.run.frame + 1 < self.fight.stuns[bot.entity]:
                    self.run.step({})
                    self.assertEqual(own.total, 250)
                for _ in range(125):
                    self.run.step({})
                    if own.total < 250:
                        break
                self.assertEqual(own.total, 250 - amount)

    def test_three_actual_authored_npc_weapons_damage_doomfist_and_stop(self):
        expected = {'Trooper': 20, 'Sniper': 100, 'Heavy Assault': 5}
        for definition in self.catalog.values():
            if definition.name not in expected:
                continue
            with self.subTest(enemy=definition.name):
                bot, vitals = self.prepare_incoming(definition)
                before = vitals.total
                self.assertEqual(npc_control.start(self.match, self.a), bot.entity)
                for _ in range(200):
                    self.run.step({})
                    self.assertFalse(self.fight.is_stunned(self.a))
                    if vitals.total < before:
                        break
                self.assertEqual(before - vitals.total, expected[definition.name])
                npc_control.stop(self.match)
                after = vitals.total
                self.settle(80)
                self.assertEqual(vitals.total, after)
                self.assertIsNotNone(bot.body_script)
                self.assertFalse(bot.attack_input)

    def test_three_actual_npc_weapons_resume_after_los_and_faction_restore(self):
        expected = {'Trooper': 20, 'Sniper': 100, 'Heavy Assault': 5}
        for definition in self.catalog.values():
            if definition.name not in expected:
                continue
            with self.subTest(enemy=definition.name):
                bot, vitals = self.prepare_incoming(definition)
                bot.team = self.a.team
                with self.assertRaises(ValueError):
                    npc_control.start(self.match, self.a)
                bot.team = 1
                self.assertEqual(npc_control.start(self.match, self.a), bot.entity)
                self.install_wall()
                self.settle(220)
                self.assertEqual(vitals.total, 250)
                self.assertFalse(bot.attack_input)
                self.install_wall(present=False)
                for _ in range(300):
                    self.run.step({})
                    if vitals.total < 250:
                        break
                self.assertEqual(250 - vitals.total, expected[definition.name])

                bot.team = self.a.team
                after_hit = vitals.total
                self.settle(220)
                self.assertEqual(vitals.total, after_hit)
                self.assertFalse(bot.attack_input)
                bot.team = 1
                for _ in range(300):
                    self.run.step({})
                    if vitals.total < after_hit:
                        break
                self.assertEqual(after_hit - vitals.total, expected[definition.name])
                npc_control.stop(self.match)

    def test_actual_npc_melee_kills_and_doomfist_respawns_without_stale_damage(self):
        for name, amount in (('Trooper', 30), ('Heavy Assault', 80)):
            with self.subTest(enemy=name):
                definition = next(d for d in self.catalog.values() if d.name == name)
                bot, vitals = self.prepare_incoming(definition, distance=2)
                old_body = self.a.body
                swings = (250 + amount - 1) // amount
                for swing in range(1, swings + 1):
                    before = vitals.total
                    self.assertEqual(npc_control.start(self.match, self.a, melee=True), bot.entity)
                    for _ in range(55):
                        self.run.step({})
                        if vitals.total < before:
                            break
                    self.assertEqual(vitals.total, max(0, 250 - swing * amount))
                    self.assertEqual(self.fight.is_dead(self.a), swing == swings)
                    if swing < swings:
                        npc_control.stop(self.match)
                        self.settle(70)
                        self.assertEqual(vitals.total, 250 - swing * amount)
                self.assertEqual(vitals.killer, bot.entity)
                self.assertEqual(self.a.stats.value(stats.DEATHS), 1)
                self.assertFalse(bot.dead)
                self.settle(1)
                self.assertNotIn(31, bot.body_script.component.held)
                npc_control.stop(self.match)
                deadline = next(when for when, player in self.fight.respawns if player is self.a)
                self.assertAlmostEqual(deadline - vitals.died_at, 10)
                while (self.run.frame + 1) * combat.TICK < deadline:
                    self.run.step({})
                    self.assertEqual(self.a.body, old_body)
                self.run.step({})
                self.assertNotEqual(self.a.body, old_body)
                self.assertFalse(self.fight.is_dead(self.a))
                self.settle(45)
                self.assertEqual(self.own_vitals().total, 250)
                self.assertEqual(vitals.total, 0)

    def test_three_actual_npc_weapons_kill_then_doomfist_respawns_on_deadline(self):
        for definition in self.catalog.values():
            if definition.name not in ('Trooper', 'Sniper', 'Heavy Assault'):
                continue
            with self.subTest(enemy=definition.name):
                bot, vitals = self.prepare_incoming(definition)
                old_body = self.a.body
                npc_control.start(self.match, self.a)
                previous, changed = vitals.total, self.run.frame
                for frame in range(12000):
                    if self.fight.is_dead(self.a):
                        break
                    self.run.step({})
                    if vitals.total != previous:
                        previous, changed = vitals.total, self.run.frame
                    self.assertLess(self.run.frame - changed, 2001)
                    if frame % 256 == 0:
                        # Drop already observed FakeClient data during long sustained fire.
                        for player in self.run.players:
                            player.client.entities.clear()
                            player.client.reliable.clear()
                self.assertTrue(self.fight.is_dead(self.a))
                self.assertEqual(vitals.total, 0)
                self.assertEqual(vitals.killer, bot.entity)
                self.assertEqual(self.a.stats.value(stats.DEATHS), 1)
                self.assertFalse(bot.dead)
                npc_control.stop(self.match)
                deadline = next(when for when, player in self.fight.respawns if player is self.a)
                self.assertAlmostEqual(deadline - vitals.died_at, 10)
                while (self.run.frame + 1) * combat.TICK < deadline:
                    self.run.step({})
                    self.assertEqual(self.a.body, old_body)
                for _ in range(2):
                    self.run.step({})
                    if self.a.body != old_body:
                        break
                self.assertNotEqual(self.a.body, old_body)
                self.assertFalse(self.fight.is_dead(self.a))
                self.assertLessEqual(abs(self.fight.now - deadline), combat.TICK + 1e-8)
                self.assertNotIn(old_body, self.fight.vitals)
                self.assertEqual(self.own_vitals().total, 250)
                self.assertEqual(self.own_vitals().temporary_shields, 0)
                weapon = next(i for i in self.a.body_script.component.instances.values()
                              if i.graph.index == 0xD0A)
                self.assertEqual(weapon.vars[53].value(), 4)

    def test_actual_npc_weapon_damage_is_absorbed_by_real_slam_passive_shields(self):
        expected = {'Trooper': 20, 'Sniper': 100, 'Heavy Assault': 5}
        for definition in self.catalog.values():
            if definition.name not in expected:
                continue
            with self.subTest(enemy=definition.name):
                bot, vitals = self.prepare_incoming(definition)
                enemy = self.fight.vitals[bot.entity]
                before = enemy.total
                self.run.step({self.a: (0, 0, 0x10)})
                for _ in range(100):
                    self.run.step({})
                    if enemy.total < before:
                        break
                self.assertEqual(vitals.temporary_shields, 30)
                decay_deadline = vitals.temporary_decay_at
                self.settle(50)
                self.a.mover.yaw, self.a.mover.pitch = aim(
                    self.fight._eye(self.a), (bot.position[0], bot.position[1] + 1, bot.position[2]))
                npc_control.start(self.match, self.a)
                hit = False
                for _ in range(200):
                    total, shields = vitals.total, vitals.temporary_shields
                    normal = sum(vitals.now.values())
                    before_tick = self.fight.now
                    self.run.step({})
                    damage = total - vitals.total
                    if damage > 1:
                        self.assertGreater(shields, 0)
                        self.assertEqual(vitals.temporary_decay_at, decay_deadline)
                        decay = 3 * max(0, self.fight.now - max(before_tick, decay_deadline))
                        remaining = max(0, shields - decay)
                        shot = expected[definition.name]
                        self.assertAlmostEqual(damage, shot + decay)
                        self.assertAlmostEqual(vitals.temporary_shields, max(0, remaining - shot))
                        self.assertAlmostEqual(normal - sum(vitals.now.values()), max(0, shot - remaining))
                        hit = True
                        break
                self.assertTrue(hit, 'actual NPC attack must damage the real temporary pool')
                npc_control.stop(self.match)

    def test_punch_control_interrupts_three_actual_npc_attacks_then_damage_resumes(self):
        expected = {'Trooper': 20, 'Sniper': 100, 'Heavy Assault': 5}
        for definition in self.catalog.values():
            if definition.name not in expected:
                continue
            with self.subTest(enemy=definition.name):
                bot, vitals = self.prepare_incoming(definition, distance=6)
                npc_control.start(self.match, self.a)
                self.run.step({self.a: (0, 0, 2)})
                for _ in range(30):
                    self.run.step({})
                    if self.fight.punches.controlled(bot.entity):
                        break
                self.assertTrue(self.fight.punches.controlled(bot.entity))
                until = self.fight.punches.controls[bot.entity]
                before = vitals.total
                for _ in range(32):
                    self.run.step({})
                    if self.fight.now < until:
                        self.assertEqual(vitals.total, before)
                self.assertFalse(self.fight.punches.controlled(bot.entity))
                hit = False
                for _ in range(260):
                    before = vitals.total
                    self.run.step({})
                    damage = before - vitals.total
                    if damage > 1:
                        self.assertAlmostEqual(damage, expected[definition.name], delta=.06)
                        hit = True
                        break
                self.assertTrue(hit, 'actual weapon damage must resume after control expires')
                npc_control.stop(self.match)

    def meteor_scene(self, team, wall=False, distances=None, *, move_and_wait=False, lethal=False,
                     spacing=0., knockback_scale=None):
        self.prepare(1)
        self.match.game_map = self.match.game_map.__class__(**{
            **self.match.game_map.__dict__, 'free_for_all': False})
        self.a.team = self.b.team = 0
        script = self.a.body_script
        component = script.component
        charge, cost = component.entity_var(342), component.entity_var(338)
        instance = next(i for i in component.instances.values() if i.graph.index == 0xC51)
        ability = instance.state(25)
        if knockback_scale is not None:
            instance.write((1, 10957), knockback_scale)
        self.assertLess(charge.value(), cost.value())
        self.run.step({self.a: (0, 0, 32)})
        self.assertFalse(ability.flags & nodes.Ability.RUNNING)
        self.assertNotIn(self.a.body, self.fight.meteors.casts)
        charge_by_neutral_commands(self)
        self.assertEqual(charge.value(), cost.value())
        for player in self.match.players:
            player.client.queue_bot_death = lambda entity, event: None
            player.client.queue_bot_death_update = lambda entity, event: None
        x, y, z = self.a.mover.position
        # Keep the victim capsules wholly beyond the wall. A one-metre
        # placement can intersect the wall for these real NPC mover radii.
        distances = distances or (3 if wall else 1,)
        definitions = [replace(d, health=20, armour=0, shields=0) if lethal else d
                       for d in self.catalog.values()]
        enemies = [npc.spawn(self.match, definition, (x, y, z + distance + slot * spacing), team=team)
                   for distance in distances
                   for slot, definition in enumerate(definitions)]
        before = {bot.entity: self.fight.vitals[bot.entity].total for bot in enemies}
        if wall:
            world = World(box(x - 20, x + 20, y - 1, y, z - 20, z + 20)
                          + box(x - 10, x + 10, y, y + 6, z + .5, z + .6))
            self.match.collision_world = world
            for player in self.match.players:
                player.mover.use(world=world)
        self.run.step({self.a: (0, 0, 32)})
        cast = self.fight.meteors.casts[self.a.body]
        self.assertTrue(ability.flags & nodes.Ability.RUNNING)
        self.assertTrue(self.fight.meteors.protected(self.a.body))
        self.assertEqual(charge.value(), cost.value())
        self.settle(70)
        self.assertFalse(cast.impacted)
        self.assertEqual({bot.entity: self.fight.vitals[bot.entity].total for bot in enemies}, before)
        if move_and_wait:
            initial = cast.target
            for _ in range(30):
                self.run.frame += 1
                self.match.update(self.run.frame * combat.TICK, self.run.frame)
                self.a.take_commands([Command(self.run.frame, forward=127)])
                self.b.take_commands([Command(self.run.frame)])
            self.assertGreater(cast.target[2], initial[2] + 2)
            self.assertFalse(cast.committed)
            self.assertFalse(cast.impacted)
            waited_from = self.run.frame
            for _ in range(330):
                self.run.step({})
                if cast.committed:
                    break
            self.assertTrue(cast.committed)
            self.assertGreater(self.run.frame - waited_from, 60)
            self.assertFalse(cast.impacted)
            destination = cast.target
            self.settle(110)
            self.assertEqual(cast.target, destination)
            self.assertAlmostEqual(self.a.mover.position[2], destination[2], delta=.01)
        else:
            self.run.step({self.a: (0, 0, 1)})
            self.settle(110)
        self.assertTrue(cast.impacted)
        self.assertTrue(cast.finished)
        self.assertFalse(ability.flags & nodes.Ability.RUNNING)
        self.assertFalse(self.fight.meteors.protected(self.a.body))
        self.assertLess(charge.value(), 30)
        return enemies, before

    def test_meteor_descent_protection_ends_at_impact_and_death_does_not_replay(self):
        self.prepare(3)
        self.match.game_map = self.match.game_map.__class__(**{
            **self.match.game_map.__dict__, 'free_for_all': False,
        })
        self.a.team, self.b.team = 0, 1
        charge_by_neutral_commands(self)
        self.match.switch_hero(self.b, heroes()[0x02E0000000000042])
        x, y, z = self.a.mover.position
        self.place_player(self.b, (x + 1.5, y, z))
        enemies = [npc.spawn(self.match, d, (x, y, z + 9), team=1) for d in self.catalog.values()]
        before = {bot.entity: self.fight.vitals[bot.entity].total for bot in enemies}
        owner = next(t for t in self.fight._targets() if t.owner is self.a)
        own = self.fight.vitals_of(owner)
        self.fight._damage(self.b, owner, 235, False, None, self.run.frame)
        self.run.step({self.a: (0, 0, 32)})
        self.settle(70)
        cast = self.fight.meteors.casts[self.a.body]
        self.run.step({self.a: (0, 0, 1)})
        for _ in range(12):
            if cast.committed:
                break
            self.run.step({})
        self.assertTrue(cast.committed)
        self.assertFalse(cast.impacted)
        self.assertTrue(self.fight.protected(self.a.body))
        self.assertEqual(self.fight._damage(self.b, owner, 1000, False, None, self.run.frame), 0)
        self.assertEqual(own.total, 15)
        self.place_player(self.b, (x + 15, y, z))
        for _ in range(90):
            if cast.impacted:
                break
            self.run.step({})
        self.assertTrue(cast.impacted)
        self.assertFalse(cast.hit)
        self.assertFalse(self.fight.protected(self.a.body))
        self.assertEqual(own.temporary_shields, 0)
        self.place_player(self.b, (x + 1.5, y, z))
        yaw, pitch = aim(self.fight._eye(self.b), self.fight._eye(self.a))
        self.run.step({self.b: (yaw, pitch, 16)})
        for _ in range(25):
            if self.fight.is_dead(self.a):
                break
            self.run.step({})
        self.assertTrue(self.fight.is_dead(self.a))
        self.assertEqual(own.killer, self.b.body)
        for bot in enemies:
            bot.position = (x, y, z + 1)
        self.settle(150)
        self.assertFalse(cast.hit)
        self.assertEqual({bot.entity: self.fight.vitals[bot.entity].total for bot in enemies}, before)

    def test_meteor_flashbang_immunity_and_body_replacement_retires_npc_impact(self):
        for committed in (False, True):
            with self.subTest(committed=committed):
                self.prepare(3)
                self.match.game_map = self.match.game_map.__class__(**{
                    **self.match.game_map.__dict__, 'free_for_all': False,
                })
                self.a.team, self.b.team = 0, 1
                charge_by_neutral_commands(self)
                self.match.switch_hero(self.b, heroes()[0x02E0000000000042])
                x, y, z = self.a.mover.position
                self.place_player(self.b, (x + 1.5, y, z))
                enemies = [npc.spawn(self.match, d, (x, y, z + 1), team=1) for d in self.catalog.values()]
                before = {bot.entity: self.fight.vitals[bot.entity].total for bot in enemies}
                own = self.own_vitals()
                old_body = self.a.body
                self.run.step({self.a: (0, 0, 32)})
                self.settle(70)
                cast = self.fight.meteors.casts[old_body]
                yaw, pitch = aim(self.fight._eye(self.b), self.fight._eye(self.a))
                self.run.step({self.b: (yaw, pitch, 16)})
                self.settle(20)
                self.assertTrue(self.fight.protected(old_body))
                self.assertFalse(self.fight.is_stunned(self.a))
                self.assertEqual(own.total, 250)
                if committed:
                    self.run.step({self.a: (0, 0, 1)})
                    for _ in range(12):
                        if cast.committed:
                            break
                        self.run.step({})
                self.assertEqual(cast.committed, committed)
                self.assertFalse(cast.impacted)
                self.match.switch_hero(self.a, heroes()[0x02E000000000006E])
                self.assertNotEqual(self.a.body, old_body)
                self.settle(400)
                self.assertNotIn(old_body, self.fight.meteors.casts)
                self.assertFalse(self.fight.protected(old_body))
                self.assertFalse(cast.impacted)
                self.assertFalse(cast.hit)
                self.assertEqual({bot.entity: self.fight.vitals[bot.entity].total for bot in enemies}, before)

    def test_meteor_intro_primary_requires_fresh_selection_press_against_all_five_npcs(self):
        for held in (False, True):
            with self.subTest(held_through_intro=held):
                self.prepare(5)
                self.match.game_map = replace(self.match.game_map, free_for_all=False)
                self.a.team = self.b.team = 0
                charge_by_neutral_commands(self)
                component = self.a.body_script.component
                charge = component.entity_var(342)
                cost = component.entity_var(338).value()
                weapon = next(i for i in component.instances.values() if i.graph.index == 0xD0A)
                ammo = weapon.vars[53].value()
                x, y, z = self.a.mover.position
                enemies = [npc.spawn(self.match, definition, (x, y, z + 5 + slot * .04), team=1)
                           for slot, definition in enumerate(self.catalog.values())]
                before = {bot.entity: self.fight.vitals[bot.entity].total for bot in enemies}
                self.run.step({self.a: (0, 0, 32)})
                cast = self.fight.meteors.casts[self.a.body]
                instance = self.fight.meteors.instance(self.a)
                self.assertFalse(instance.state(18).active)
                primary = {self.a: (0, 0, FIRE)}
                self.run.step(primary)
                self.assertFalse(instance.state(18).active)
                for _ in range(70):
                    self.run.step(primary if held else {})
                    self.assertFalse(cast.committed or cast.impacted or cast.finished)
                    self.assertTrue(self.fight.protected(self.a.body))
                    self.assertFalse(self.fight.rockets)
                self.assertTrue(instance.state(18).active)
                self.assertEqual(charge.value(), cost)
                self.assertEqual(weapon.vars[53].value(), ammo)
                self.assertEqual({bot.entity: self.fight.vitals[bot.entity].total for bot in enemies}, before)
                self.assertFalse(self.a.client.messages(combat.HIT))

                self.run.step({})
                self.assertFalse(cast.committed)
                self.run.step(primary)
                self.settle(110)
                self.assertIs(self.fight.meteors.casts[self.a.body], cast)
                self.assertTrue(cast.committed and cast.impacted and cast.finished)
                self.assertFalse(self.fight.protected(self.a.body))
                self.assertEqual(set(cast.hit), set(before))
                self.assertEqual(len(self.a.client.messages(combat.HIT)), len(enemies))
                for slot, bot in enumerate(enemies):
                    damage = flat_meteor_damage(5 + slot * .04, mover.mover_data(bot.body))
                    self.assertAlmostEqual(before[bot.entity] - self.fight.vitals[bot.entity].total, damage, delta=.01)
                self.assertEqual(weapon.vars[53].value(), ammo)
                self.assertLess(charge.value(), 30)

    def test_meteor_moved_target_auto_lands_against_all_five_npcs(self):
        enemies, before = self.meteor_scene(1, distances=(10,), move_and_wait=True)
        cast = self.fight.meteors.casts[self.a.body]
        self.assertEqual(set(cast.hit), {bot.entity for bot in enemies})
        for bot in enemies:
            with self.subTest(enemy=bot.name):
                self.assertLess(self.fight.vitals[bot.entity].total, before[bot.entity])
        self.assertEqual(len(self.a.client.messages(combat.HIT)), len(enemies))

    def test_meteor_charge_lifecycle_outer_damage_and_range_against_all_five_npcs(self):
        distances = (5., 8.2, 9.)
        spacing = .04
        enemies, before = self.meteor_scene(1, distances=distances, spacing=spacing)
        cast = self.fight.meteors.casts[self.a.body]
        count = len(self.catalog)
        hit = set()
        for distance, start in zip(distances, range(0, len(enemies), count), strict=True):
            for slot, bot in enumerate(enemies[start:start + count]):
                damage = flat_meteor_damage(distance + slot * spacing, mover.mover_data(bot.body))
                with self.subTest(enemy=bot.name, distance=distance):
                    self.assertEqual(damage > 0., distance < 9.)
                    if damage:
                        hit.add(bot.entity)
                    self.assertAlmostEqual(before[bot.entity] - self.fight.vitals[bot.entity].total,
                                           damage, delta=.01)
        self.assertEqual(set(cast.hit), hit)
        self.run.step({self.a: (0, 0, 32)})
        self.settle(40)
        self.assertIs(self.fight.meteors.casts[self.a.body], cast)

    def test_zero_meteor_knockback_preserves_all_five_npc_movement_states(self):
        spacing = .25
        with patch.object(bots.Bot, 'launch', autospec=True, side_effect=bots.Bot.launch) as launch:
            enemies, before = self.meteor_scene(1, distances=(5.,), spacing=spacing, knockback_scale=0.)
        cast = self.fight.meteors.casts[self.a.body]
        self.assertEqual(cast.hit, tuple(bot.entity for bot in enemies))
        self.assertEqual(self.own_vitals().temporary_shields, 150)
        for index, bot in enumerate(enemies):
            with self.subTest(enemy=bot.name):
                expected = flat_meteor_damage(5. + index * spacing, mover.mover_data(bot.body))
                self.assertAlmostEqual(before[bot.entity] - self.fight.vitals[bot.entity].total,
                                       expected, delta=.01)
                self.assertIsNone(bot.motion)
                self.assertIsNone(bot.moved_tick)
        launch.assert_not_called()

    def test_other_ability_hits_count_current_intrinsic_shields_toward_passive_cap(self):
        heavy = next(d for d in self.catalog.values() if d.name == 'Heavy Assault')
        for ability, distance in (('punch', 2), ('ground', 10), ('aerial', 10), ('meteor', 1)):
            with self.subTest(ability=ability):
                bot, target = self.create_enemy(heavy, distance=distance)
                owner = combat.Vitals(self.a.body, 250, shields=200)
                owner.now[combat.SHIELDS_POOL] = 140
                self.fight.vitals[self.a.body] = owner
                before = target.total
                if ability == 'punch':
                    self.punch_hit(target)
                    self.assertEqual(before - target.total, 50)
                elif ability == 'meteor':
                    charge_by_neutral_commands(self)
                    self.run.step({self.a: (0, 0, 32)})
                    self.settle(70)
                    self.run.step({self.a: (0, 0, 1)})
                    cast = self.fight.meteors.casts[self.a.body]
                    for _ in range(110):
                        self.run.step({})
                        if cast.finished:
                            break
                    self.assertTrue(cast.finished)
                    self.assertEqual(set(cast.hit), {bot.entity})
                    self.assertEqual(before - target.total, 300)
                else:
                    pitch = 0
                    if ability == 'aerial':
                        x, y, z = self.a.mover.position
                        self.place_player(self.a, (x, y + 8, z))
                        pitch = 9000
                    self.run.step({self.a: (0, pitch, 0x10)})
                    for _ in range(120):
                        self.run.step({})
                        if target.total < before:
                            break
                    cast = self.fight.slams.casts[self.a.body]
                    self.assertEqual(cast.kind, ability)
                    self.assertIn(bot.entity, cast.hit)
                    self.assertGreater(cast.damage, 25)
                    self.assertAlmostEqual(before - target.total, cast.damage)
                self.assertEqual(owner.now[combat.SHIELDS_POOL], 140)
                self.assertEqual(owner.temporary_shields, 10)
                self.assertEqual(owner.temporary_cap, 150)
                self.assertGreater(owner.temporary_decay_at, self.fight.now)

    def test_meteor_and_uppercut_share_passive_shield_cap(self):
        heavy = next(d for d in self.catalog.values() if d.name == 'Heavy Assault')
        for meteor_targets, after_decay in ((1, False), (2, False), (2, True)):
            with self.subTest(meteor_targets=meteor_targets, after_decay=after_decay):
                first, _ = self.create_enemy(heavy, distance=1)
                targets = [first]
                if meteor_targets == 2:
                    targets.append(npc.spawn(self.match, heavy, first.position, team=1))
                initial_health = {bot.entity: self.fight.vitals[bot.entity].total for bot in targets}
                charge_by_neutral_commands(self)
                self.run.step({self.a: (0, 0, 32)})
                self.settle(70)
                self.run.step({self.a: (0, 0, 1)})
                cast = self.fight.meteors.casts[self.a.body]
                for _ in range(110):
                    self.run.step({})
                    if cast.finished:
                        break
                self.assertTrue(cast.finished)
                self.assertEqual(set(cast.hit), set(initial_health))
                for entity, health in initial_health.items():
                    self.assertEqual(health - self.fight.vitals[entity].total, 300)

                owner = self.own_vitals()
                self.assertEqual(owner.temporary_shields, 75 * meteor_targets)
                original_deadline = owner.temporary_decay_at
                if after_decay:
                    for _ in range(64):
                        if self.fight.now > original_deadline:
                            break
                        self.run.step({})
                    elapsed = self.fight.now - original_deadline
                    self.assertGreater(elapsed, 0)
                    self.assertGreater(owner.temporary_shields, 149)
                    self.assertLess(owner.temporary_shields, 150)
                    self.assertAlmostEqual(owner.temporary_shields, 150 - 3 * elapsed)
                opponent_count = 1 if after_decay else 3
                x, y, z = self.a.mover.position
                opponents = [npc.spawn(self.match, heavy, (x, y, z + 1.5), team=1) for _ in range(opponent_count)]
                before = {bot.entity: self.fight.vitals[bot.entity].total for bot in opponents}
                self.run.step({self.a: (0, 0, 8)})
                for entity, health in before.items():
                    self.assertEqual(health - self.fight.vitals[entity].total, 50)
                self.assertEqual(owner.temporary_shields, 150)
                if meteor_targets == 2 and not after_decay:
                    self.assertEqual(owner.temporary_decay_at, original_deadline)
                else:
                    self.assertAlmostEqual(owner.temporary_decay_at, self.fight.now + 1)
                for _ in range(64):
                    if self.fight.now > owner.temporary_decay_at:
                        break
                    self.run.step({})
                self.assertGreater(self.fight.now, owner.temporary_decay_at)
                elapsed = self.fight.now - owner.temporary_decay_at
                self.assertAlmostEqual(owner.temporary_shields, 150 - 3 * elapsed)

    def test_meteor_hits_all_five_actual_npcs_caps_shields_and_retains_lethal_corpses(self):
        enemies, before = self.meteor_scene(1)
        cast = self.fight.meteors.casts[self.a.body]
        self.assertEqual(len(cast.hit), 5)
        self.assertTrue(cast.finished)
        own = next(t for t in self.fight._targets() if t.owner is self.a)
        self.assertEqual(self.fight.vitals_of(own).temporary_shields, 150)
        self.settle(160)
        for bot in enemies:
            with self.subTest(enemy=bot.name):
                self.assertEqual(self.fight.vitals[bot.entity].total, max(0, before[bot.entity] - 300))
                self.assertEqual(bot.dead, before[bot.entity] <= 300)
                self.assertIn(bot, self.match.bots)
                self.assertFalse(any(who is bot for _, who in self.fight.respawns))

    def test_meteor_kills_all_five_low_health_npcs_without_launch_or_replay(self):
        enemies, _ = self.meteor_scene(1, lethal=True)
        cast = self.fight.meteors.casts[self.a.body]
        self.assertEqual(set(cast.hit), {bot.entity for bot in enemies})
        self.assertEqual(len(self.a.client.messages(combat.HIT)), len(enemies))
        x, y, z = cast.target
        for bot in enemies:
            with self.subTest(enemy=bot.name):
                victim = self.fight.vitals[bot.entity]
                self.assertTrue(victim.dead)
                self.assertEqual(victim.killer, self.a.body)
                self.assertEqual(victim.total, 0)
                self.assertEqual(tuple(bot.position), (x, y, z + 1))
                self.assertIn(bot, self.match.bots)
                self.assertFalse(any(who is bot for _, who in self.fight.respawns))

    def test_meteor_recovery_flashbang_interrupt_preserves_each_npc_hit(self):
        self.check_meteor_recovery_preserves_each_npc_hit(owner_dies=False)

    def test_meteor_recovery_owner_death_preserves_each_npc_hit(self):
        self.check_meteor_recovery_preserves_each_npc_hit(owner_dies=True)

    def test_meteor_hit_npcs_can_retaliate_after_landing(self):
        for definition in self.catalog.values():
            with self.subTest(enemy=definition.name):
                bot, own = self.prepare_incoming(definition, distance=5)
                enemy = self.fight.vitals[bot.entity]
                before = enemy.total
                charge_by_neutral_commands(self)
                owner = next(t for t in self.fight._targets() if t.owner is self.a)
                self.fight._damage(self.b, owner, 249, False, None, self.run.frame)
                self.run.step({self.a: (0, 0, 32)})
                self.settle(70)
                self.run.step({self.a: (0, 0, 1)})
                cast = self.fight.meteors.casts[self.a.body]
                for _ in range(90):
                    if cast.impacted:
                        break
                    self.run.step({})
                self.assertEqual(cast.hit, (bot.entity,))
                after_impact = enemy.total
                damage = flat_meteor_damage(5, mover.mover_data(bot.body))
                self.assertAlmostEqual(after_impact, before - damage, places=3)
                self.assertEqual(own.total, 76)
                self.assertFalse(self.fight.protected(self.a.body))
                # Let impact displacement resolve, then enter manual pin range.
                self.settle(60)
                x, y, z = bot.position
                self.place_player(self.a, (x, y, z - 2))
                yaw, pitch = aim(self.fight._eye(self.a), (x, y + 1, z))
                self.run.step({self.a: (yaw, pitch, 0)})
                self.assertEqual(npc_control.start(self.match, self.a), bot.entity)
                decay_at = own.temporary_decay_at
                retaliation = False
                for _ in range(900):
                    shields = own.temporary_shields
                    health = sum(own.now.values())
                    now = self.fight.now
                    self.run.step({})
                    decay = 3 * max(0, self.fight.now - max(now, decay_at))
                    retaliation |= own.total < health + max(0, shields - decay) - .001
                    if own.dead or (retaliation and definition.name == 'Enforcer'):
                        break
                self.assertTrue(retaliation)
                if definition.name == 'Enforcer':
                    npc_control.stop(self.match)
                    # Allow the remaining Meteor shields to decay on the normal clock.
                    for _ in range(1800):
                        if not own.temporary_shields:
                            break
                        self.run.step({})
                    self.assertEqual(own.total, 1)
                    self.assertTrue(cast.finished)
                    self.assertEqual(npc_control.start(self.match, self.a), bot.entity)
                    for _ in range(300):
                        if own.dead:
                            break
                        self.run.step({})
                self.assertTrue(own.dead)
                self.assertEqual(own.killer, bot.entity)
                self.assertEqual(own.temporary_shields, 0)
                npc_control.stop(self.match)
                self.settle(60)
                self.assertTrue(cast.finished)
                self.assertEqual(enemy.total, after_impact)
                self.assertEqual(len(self.a.client.messages(combat.HIT)), 1)

    def check_meteor_recovery_preserves_each_npc_hit(self, *, owner_dies):
        self.prepare(12)
        self.match.switch_hero(self.b, heroes()[0x02E0000000000042])
        x, y, z = self.a.mover.position
        self.place_player(self.b, (x + 12, y, z))
        charge_by_neutral_commands(self)
        enemies = [npc.spawn(self.match, d, (x, y, z + 5 + slot * .04), team=1)
                   for slot, d in enumerate(self.catalog.values())]
        before = {bot.entity: self.fight.vitals[bot.entity].total for bot in enemies}
        self.run.step({self.a: (0, 0, 32)})
        self.settle(70)
        self.run.step({self.a: (0, 0, 1)})
        cast = self.fight.meteors.casts[self.a.body]
        for _ in range(90):
            if cast.impacted:
                break
            self.run.step({})
        self.assertTrue(cast.impacted)
        self.assertFalse(self.fight.meteors.protected(self.a.body))
        ability = self.fight.meteors.instance(self.a).state(25)
        self.assertTrue(ability.flags & nodes.Ability.RUNNING)
        charge = self.a.body_script.component.entity_var(342)
        self.assertEqual(charge.value(), 1680)
        self.assertEqual(set(cast.hit), set(before))
        self.assertEqual(self.own_vitals().temporary_shields, 150)
        after_impact = {bot.entity: self.fight.vitals[bot.entity].total for bot in enemies}
        for slot, bot in enumerate(enemies):
            damage = flat_meteor_damage(5 + slot * .04, mover.mover_data(bot.body))
            self.assertAlmostEqual(after_impact[bot.entity], before[bot.entity] - damage, delta=.01)
        if owner_dies:
            owner = next(t for t in self.fight._targets() if t.owner is self.a)
            self.fight._damage(self.b, owner, 1000, False, None, self.run.frame)
            self.assertTrue(self.fight.is_dead(self.a))
            self.assertEqual(self.own_vitals().killer, self.b.body)
            self.assertEqual(self.own_vitals().temporary_shields, 0)
            self.assertFalse(self.fight.protected(self.a.body))
            self.assertTrue(cast.finished)
            self.settle(90)
            self.assertFalse(ability.flags & nodes.Ability.RUNNING)
            self.assertEqual(set(cast.hit), set(before))
            self.assertEqual(len(self.a.client.messages(combat.HIT)), len(enemies))
            self.assertEqual({entity: self.fight.vitals[entity].total for entity in before}, after_impact)
            return
        self.place_player(self.b, (x + 2, y, z))
        yaw, pitch = aim(self.fight._eye(self.b), self.fight._eye(self.a))
        self.run.step({self.b: (yaw, pitch, 16)})
        for _ in range(15):
            if self.fight.is_stunned(self.a):
                break
            self.run.step({})
        self.assertTrue(self.fight.is_stunned(self.a))
        self.assertFalse(ability.flags & nodes.Ability.RUNNING)
        self.assertLessEqual(charge.value(), .448001)
        resumed_charge = self.charge_checkpoint()
        self.settle(90)
        self.assertTrue(cast.finished)
        self.assertEqual(set(cast.hit), set(before))
        self.assertEqual(len(self.a.client.messages(combat.HIT)), len(enemies))
        self.assertEqual({entity: self.fight.vitals[entity].total for entity in before}, after_impact)
        self.assert_damage_charge(resumed_charge, 0)
        self.assertLess(charge.value(), 1680)
        self.assertFalse(self.fight.is_stunned(self.a))
        self.run.step({self.a: (0, 0, 32)})
        self.assertIs(self.fight.meteors.casts[self.a.body], cast)
        self.assertFalse(ability.flags & nodes.Ability.RUNNING)

    def test_meteor_ally_and_static_wall_rejection_for_all_five_actual_npcs(self):
        for team, wall in ((0, False), (1, True)):
            with self.subTest(team=team, wall=wall):
                enemies, before = self.meteor_scene(team, wall)
                for bot in enemies:
                    self.assertEqual(self.fight.vitals[bot.entity].total, before[bot.entity])
                cast = self.fight.meteors.casts[self.a.body]
                self.assertEqual(cast.hit, ())
                self.assertFalse(self.own_vitals().temporary_pool_used)
                self.assertTrue(cast.finished)
                self.assertLess(self.a.body_script.component.entity_var(342).value(), 30)

    def test_meteor_blocks_three_actual_npc_weapons_then_damage_resumes(self):
        for definition in self.catalog.values():
            if definition.name not in ('Trooper', 'Sniper', 'Heavy Assault'):
                continue
            with self.subTest(enemy=definition.name):
                self.prepare(10)
                self.match.game_map = self.match.game_map.__class__(**{
                    **self.match.game_map.__dict__, 'free_for_all': False})
                self.a.team = self.b.team = 0
                charge_by_neutral_commands(self)
                x, y, z = self.a.mover.position
                self.place_player(self.b, (x + 20, y, z + 10))
                for player in self.match.players:
                    player.client.bot_stream = lambda entity: None
                bot = npc.spawn(self.match, definition, (x, y, z + 10), team=1)
                own = next(t for t in self.fight._targets() if t.owner is self.a)
                vitals = self.fight.vitals_of(own)
                before = vitals.total
                self.run.step({self.a: (0, 0, 32)})
                npc_control.start(self.match, self.a)
                self.settle(150)
                self.assertTrue(self.fight.meteors.protected(self.a.body))
                self.assertEqual(vitals.total, before)
                self.assertIsNotNone(bot.body_script)
                self.run.step({self.a: (0, 0, 1)})
                self.settle(100)
                self.assertFalse(self.fight.meteors.protected(self.a.body))
                for _ in range(200):
                    if vitals.total < before:
                        break
                    self.run.step({})
                self.assertLess(vitals.total, before)
                npc_control.stop(self.match)

    def test_meteor_descent_rejects_four_authored_npc_weapons(self):
        # Confirm before each weapon's release so it fires during descent.
        for name, projectile, lead in (
                ('Trooper', False, 0), ('Enforcer', True, 30),
                ('Sniper', True, 100), ('Heavy Assault', False, 30)):
            with self.subTest(enemy=name):
                definition = next(d for d in self.catalog.values() if d.name == name)
                bot, own = self.prepare_incoming(definition, distance=10)
                z = self.a.mover.position[2]
                charge_by_neutral_commands(self)
                self.run.step({self.a: (0, 0, 32)})
                self.settle(70)
                self.assertEqual(npc_control.start(self.match, self.a), bot.entity)
                self.settle(lead)
                self.run.step({self.a: (0, 0, 1)})
                cast = self.fight.meteors.casts[self.a.body]
                self.assertTrue(cast.committed)
                self.assertFalse(cast.impacted)

                crossed = False
                with patch.object(self.fight, '_shoot', wraps=self.fight._shoot) as shots:
                    for _ in range(90):
                        if cast.impacted:
                            break
                        self.assertTrue(self.fight.protected(self.a.body))
                        self.assertEqual(own.total, 250)
                        self.assertEqual(self.a.mover.position[2], z)
                        crossed |= any(rocket.shooter is bot and rocket.position[2] < z
                                       for rocket in self.fight.rockets)
                        self.run.step({})
                    self.assertTrue(any(call.args[0] is bot for call in shots.call_args_list))
                if projectile:
                    self.assertTrue(crossed)
                self.assertTrue(cast.impacted)
                self.assertFalse(cast.hit)
                self.assertFalse(self.fight.protected(self.a.body))
                self.assertEqual(own.total, 250)
                for _ in range(200):
                    self.run.step({})
                    if own.total < 250:
                        break
                self.assertLess(own.total, 250)
                npc_control.stop(self.match)
