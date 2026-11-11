"""Executed Ball interactions with externally prepared 104319 NPC definitions.

Set ORPHEUS_PVE_DATA_DIR to the read-only directory containing pve_npcs_174.json
and the reviewed graph bundle, and ORPHEUS_PVE_MOVERS to the extracted mover file.
The collision and transport are explicit fixtures; capsules use authored body data,
not animated retail hitboxes.
"""

import json
import os
import unittest
from dataclasses import replace
from pathlib import Path
from unittest.mock import patch

from test_game_combat import KANEZAKA_DM, PRACTICE_RANGE, FakeClient, Run, aim
from test_wrecking_ball_combat import BALL
from test_wrecking_ball_control import ANA
from test_wrecking_ball_fireball import add_side_wall, first_hit, prepare_swing, relocate
from test_wrecking_ball_grapple import hold
from test_wrecking_ball_piledriver import cast, lift

from ow174.game import combat, mover, npc, npc_control, world
from ow174.game.ball import instance as ball_instance
from ow174.game.collision import StaticCollisionProvider, World
from ow174.game.commands import Command
from ow174.game.content import heroes
from ow174.game.script import graph
from ow174.game.script.driver import ULT_CHARGE, ULT_COST


class PveClient(FakeClient):
    """Explicit in-memory transport; no claim that a retail client decoded these events."""

    def __init__(self, player):
        super().__init__(player)
        self.deaths = []

    def queue_bot_death(self, entity, snapshot):
        self.deaths.append((entity, snapshot))

    def queue_bot_death_update(self, entity, snapshot):
        self.deaths.append((entity, snapshot))

    def bot_stream(self, body):
        return None  # no projectile observer transport in this combat-only adapter


@unittest.skipUnless(os.environ.get('ORPHEUS_PVE_DATA_DIR') and os.environ.get('ORPHEUS_PVE_MOVERS'),
                     'external NPC graph/catalog and mover inputs not selected')
class BallPveTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        directory = Path(os.environ['ORPHEUS_PVE_DATA_DIR'])
        cls.catalog = npc.load_catalog(directory / 'pve_npcs_174.json')
        if {d.name for d in cls.catalog.values()} != {
            'Trooper', 'Enforcer', 'Sniper', 'Assassin', 'Heavy Assault',
        }:
            raise ValueError('acceptance requires all five actual NPC definitions')
        document = json.loads(Path(os.environ['ORPHEUS_PVE_MOVERS']).read_text(encoding='utf-8'))
        if document.get('format_version') != 1:
            raise ValueError('unsupported NPC mover table')
        cls.movers = {int(key, 16): mover.MoverData(**{k: v for k, v in row.items() if k != 'name'})
                      for key, row in document['movers'].items()}
        if set(cls.movers) != {definition.body for definition in cls.catalog.values()}:
            raise ValueError('all actual NPC bodies need authored movers; fallback is not acceptance')
        cls.mover_table = patch.object(mover, '_mover_table', {**mover._movers(), **cls.movers})
        cls.mover_table.start()
        cls.bundle = patch.object(graph, 'GRAPHS_PATH', directory / 'game_graphs_174.json.gz')
        cls.bundle.start()
        cls.clear_graphs()

    @classmethod
    def tearDownClass(cls):
        cls.bundle.stop()
        cls.mover_table.stop()
        cls.clear_graphs()

    @staticmethod
    def clear_graphs():
        graph._data.cache_clear()
        graph._records.cache_clear()
        graph.graph.cache_clear()

    def scenario(self, definition, *, distance=6, ally=False, names=('A',), game_map=KANEZAKA_DM):
        run = Run(game_map, names=names)
        ball = run.players[0]
        for player in run.players:
            player.client = PveClient(player)
        run.match.bots.clear()
        run.match._pending_bot_scripts.clear()
        run.match.switch_hero(ball, heroes()[BALL])
        if ally:
            run.match.game_map = run.match.game_map.__class__(
                **{**run.match.game_map.__dict__, 'free_for_all': False},
            )
        for _ in range(40):
            run.step({})
        x, y, z = ball.mover.position
        bot = npc.spawn(run.match, definition, (x, y, z + distance),
                        team=ball.team if ally else 1 - ball.team, yaw=-32768)
        fight = run.match.combat
        target = next(t for t in fight._targets() if t.owner is bot)
        self.assertEqual(target.height, self.movers[definition.body].stand_height)
        self.assertEqual(target.radius, self.movers[definition.body].stand_radius)
        own = next(t for t in fight._targets() if t.owner is ball)
        return run, ball, bot, target, fight.vitals_of(own)

    def nano_scenario(self, definition, *, distance):
        run = Run(replace(PRACTICE_RANGE, team_sizes=(6, 6)), ('Ball', 'Ana'))
        player, ana = run.players
        match = run.match
        match.bots.clear()
        match._pending_bot_scripts.clear()
        player.team = ana.team = 0
        for actor, hero in ((player, BALL), (ana, ANA)):
            actor.client = PveClient(actor)
            self.assertTrue(match.switch_hero(actor, heroes()[hero]))
            actor.body_script.ult_seconds = 0
            actor.body_script.set_ult_charge(0)
        x, y, z = player.mover.position
        relocate(run, ana, (x + 6, y, z))
        for _ in range(40):
            run.step({})
        fight = match.combat
        script = ana.body_script
        script.set_ult_charge(script.component.entity_var(ULT_COST).value())
        angles = aim(fight._eye(ana), (x, y + 0.8, z))
        run.step({ana: (*angles, 32)})
        for _ in range(13):
            run.step({})
        own = fight.vitals[player.body]
        self.assertGreater(own.nano_until, run.frame)
        self.assertEqual(script.component.entity_var(ULT_CHARGE).value(), 0)
        bot = npc.spawn(match, definition, (x, y, z + distance), team=1, yaw=-32768)
        target = next(t for t in fight._targets() if t.owner is bot)
        return run, player, bot, target, own

    def test_actual_nano_cannon_and_melee_damage_all_five_npcs_then_cannon_recovers(self):
        for definition in self.catalog.values():
            with self.subTest(npc=definition.name):
                run, player, _, target, own = self.nano_scenario(definition, distance=1.5)
                fight = run.match.combat
                vitals = fight.vitals_of(target)
                before = vitals.total
                charge = player.body_script.component.entity_var(ULT_CHARGE)
                ammo = ball_instance(player, 0x1514).vars[53]
                x, y, z = target.feet
                cannon = aim(fight._eye(player), (x, y + 0.5, z))
                melee = aim(fight._eye(player), (x, y + 1, z))
                run.step({player: (*cannon, 1)})
                self.assertEqual(vitals.total, before - 7.5)
                self.assertEqual(charge.value(), 7.5)
                self.assertEqual(ammo.value(), 79)
                run.step({player: (*melee, 0x800)})
                for _ in range(40):
                    run.step({})
                self.assertEqual(vitals.total, before - 52.5)
                self.assertEqual(charge.value(), 52.5)
                run.frame = own.nano_until - 1
                run.match.update(run.frame * combat.TICK, run.frame, frames=False)
                run.step({player: (*cannon, 1)})
                self.assertEqual(vitals.total, before - 57.5)
                self.assertEqual(charge.value(), 57.5)
                self.assertEqual(ammo.value(), 78)

    def test_actual_nano_piledriver_and_fireball_damage_and_launch_all_five_npcs(self):
        for definition in self.catalog.values():
            for attack, distance, damage in (('Piledriver', 1.5, 150), ('Fireball', 6, 75)):
                with self.subTest(npc=definition.name, attack=attack):
                    run, player, bot, target, own = self.nano_scenario(definition, distance=distance)
                    fight = run.match.combat
                    vitals = fight.vitals_of(target)
                    before = vitals.total
                    ground = bot.position[1]
                    if attack == 'Piledriver':
                        lift(run, player)
                        cast(run, player)
                    else:
                        prepare_swing(run, player)
                        first_hit(run, player, bot)
                    self.assertLess(run.frame, own.nano_until)
                    self.assertEqual(vitals.total, before - damage)
                    charge = player.body_script.component.entity_var(ULT_CHARGE)
                    self.assertEqual(charge.value(), damage)
                    self.assertTrue(bot.airborne)
                    self.assertIn(bot.entity, fight.npc_impulses)
                    if attack == 'Fireball':
                        self.assertGreater(bot.velocity[2], 20)
                        self.assertEqual(bot.velocity[1], 5)
                    else:
                        self.assertGreater(bot.position[1], ground + 0.2)

    def test_nano_piledriver_kills_each_npc_at_120_hp_and_expiry_restores_survival(self):
        for definition in self.catalog.values():
            for expired in (False, True):
                with self.subTest(npc=definition.name, expired=expired):
                    run, player, bot, target, own = self.nano_scenario(definition, distance=1.5)
                    fight = run.match.combat
                    vitals = fight.vitals_of(target)
                    vitals.damage(vitals.total - 120)
                    position = bot.position
                    if expired:
                        run.frame = own.nano_until
                        run.match.update(run.frame * combat.TICK, run.frame, frames=False)
                    lift(run, player)
                    cast(run, player)
                    charge = player.body_script.component.entity_var(ULT_CHARGE)
                    self.assertEqual(vitals.total, 20 if expired else 0)
                    self.assertEqual(charge.value(), 100 if expired else 120)
                    self.assertEqual(bot.dead, not expired)
                    self.assertEqual(bot.airborne, expired)
                    self.assertEqual(bot.entity in fight.npc_impulses, expired)
                    if expired:
                        self.assertFalse(player.client.deaths)
                    else:
                        self.assertEqual(bot.position, position)
                        self.assertEqual(player.client.deaths[0][0], bot.entity)
                        self.assertEqual(vitals.killer, player.body)

    def test_actual_five_bodies_take_quad_cannon_and_melee_damage(self):
        for definition in self.catalog.values():
            with self.subTest(npc=definition.name):
                run, ball, bot, target, _ = self.scenario(definition, distance=1.5)
                self.assertIsNotNone(bot.body_script)
                self.assertEqual(bot.body, definition.body)
                ball.body_script.ult_seconds = 0
                ball.body_script.set_ult_charge(0)
                fight = run.match.combat
                vitals = fight.vitals_of(target)
                before = definition.health + definition.armour + definition.shields
                self.assertEqual(vitals.total, before)
                angles = aim(fight._eye(ball), (target.feet[0], target.feet[1] + 1, target.feet[2]))
                # Aim below the near capsule's head even from Ball's authored1.83m eye.
                body_angles = aim(fight._eye(ball), (target.feet[0], target.feet[1] + 0.5, target.feet[2]))
                run.step({ball: (*body_angles, 1)})
                run.step({})
                self.assertEqual(vitals.total, before - 5)
                self.assertEqual(ball.body_script.component.entity_var(ULT_CHARGE).value(), 5)
                run.step({ball: (*angles, 0x800)})
                for _ in range(40):
                    run.step({ball: (*angles, 0)})
                self.assertEqual(vitals.total, before - 35)
                self.assertEqual(ball.body_script.component.entity_var(ULT_CHARGE).value(), 35)

    def test_actual_five_cannon_misses_spend_ammo_and_clear_shot_recovers(self):
        for definition in self.catalog.values():
            with self.subTest(npc=definition.name):
                run, ball, _, target, _ = self.scenario(definition, distance=6)
                fight = run.match.combat
                vitals = fight.vitals_of(target)
                before = vitals.total
                ball.body_script.ult_seconds = 0
                ball.body_script.set_ult_charge(0)
                charge = ball.body_script.component.entity_var(ULT_CHARGE)
                ammo = ball_instance(ball, 0x1514).vars[53]
                eye = fight._eye(ball)
                clear = aim(eye, (target.feet[0], target.feet[1] + 0.65, target.feet[2]))
                away = aim(eye, (eye[0], eye[1], eye[2] - 1))
                world_before = run.match.collision_world
                provider_before = run.match.collision_provider
                x, y, z = ball.mover.position
                wall_z = z + 3
                wall = World([
                    (x - 5, y, wall_z, x + 5, y, wall_z, x + 5, y + 5, wall_z),
                    (x - 5, y, wall_z, x + 5, y + 5, wall_z, x - 5, y + 5, wall_z),
                ])
                run.step({ball: (*away, 1)})
                self.assertEqual(ammo.value(), 79)
                self.assertEqual(vitals.total, before)
                self.assertEqual(charge.value(), 0)
                self.assertFalse(ball.client.messages(combat.HIT))
                for _ in range(12):
                    run.step({})
                run.match.collision_world = wall
                run.match.collision_provider = StaticCollisionProvider(wall)
                run.step({ball: (*clear, 1)})
                self.assertEqual(ammo.value(), 78)
                self.assertEqual(vitals.total, before)
                self.assertEqual(charge.value(), 0)
                self.assertFalse(ball.client.messages(combat.HIT))
                run.match.collision_world = world_before
                run.match.collision_provider = provider_before
                for _ in range(12):
                    run.step({})
                run.step({ball: (*clear, 1)})
                self.assertEqual(ammo.value(), 77)
                self.assertEqual(vitals.total, before - 5)
                self.assertEqual(charge.value(), 5)
                self.assertEqual(len(ball.client.messages(combat.HIT)), 1)

    def test_actual_five_enemies_increase_adaptive_shield_once(self):
        for definition in self.catalog.values():
            with self.subTest(npc=definition.name):
                run, ball, _, _, vitals = self.scenario(definition)
                run.step({ball: (0, 0, 16)})
                self.assertEqual(vitals.temporary_shields, 175)
                vitals.damage(50)
                for _ in range(5):
                    run.step({ball: (0, 0, 16)})
                self.assertEqual(vitals.temporary_shields, 125)

    def test_actual_five_adaptive_shield_counts_through_cover_within_radius(self):
        for definition in self.catalog.values():
            for distance, expected in ((7, 175), (10, 100)):
                with self.subTest(npc=definition.name, distance=distance):
                    run, ball, _, target, own = self.scenario(definition, distance=distance)
                    fight = run.match.combat
                    vitals = fight.vitals_of(target)
                    before = vitals.total
                    x, y, z = ball.mover.position
                    wall_z = z + 3
                    wall = World([
                        (x - 5, y, wall_z, x + 5, y, wall_z, x + 5, y + 5, wall_z),
                        (x - 5, y, wall_z, x + 5, y + 5, wall_z, x - 5, y + 5, wall_z),
                    ])
                    run.match.collision_world = wall
                    run.match.collision_provider = StaticCollisionProvider(wall)
                    angles = aim(fight._eye(ball), (target.feet[0], target.feet[1] + 0.65, target.feet[2]))
                    run.step({ball: (*angles, 1)})
                    self.assertEqual(ball_instance(ball, 0x1514).vars[53].value(), 79)
                    self.assertEqual(vitals.total, before)
                    self.assertFalse(ball.client.messages(combat.HIT))
                    run.step({ball: (*angles, 16)})
                    self.assertEqual(own.temporary_shields, expected)

    def test_actual_five_allies_reject_cannon_damage_and_enemy_shield_count(self):
        for definition in self.catalog.values():
            with self.subTest(npc=definition.name):
                run, ball, _, target, own = self.scenario(definition, ally=True)
                fight = run.match.combat
                vitals = fight.vitals_of(target)
                before = vitals.total
                angles = aim(fight._eye(ball), (target.feet[0], target.feet[1] + 1, target.feet[2]))
                run.step({ball: (*angles, 16 | 1)})
                for _ in range(10):
                    run.step({ball: (*angles, 1)})
                self.assertEqual(vitals.total, before)
                self.assertEqual(own.temporary_shields, 100)
                self.assertFalse(ball.client.messages(combat.HIT))

    def test_actual_five_dead_bodies_remain_corpses_and_leave_target_search(self):
        for definition in self.catalog.values():
            with self.subTest(npc=definition.name):
                run, ball, bot, target, own = self.scenario(definition)
                fight = run.match.combat
                ball.body_script.ult_seconds = 0
                ball.body_script.set_ult_charge(0)
                initial_total = fight.vitals_of(target).total
                fight._damage(ball, target, 10000, False, None, run.frame)
                self.assertEqual(ball.body_script.component.entity_var(ULT_CHARGE).value(),
                                 min(initial_total, ball.body_script.component.entity_var(ULT_COST).value()))
                self.assertTrue(bot.dead)
                self.assertIsNotNone(bot.body_script)
                self.assertTrue(bot.body_script.component.owner_dead)
                self.assertEqual(ball.client.deaths[0][0], bot.entity)
                self.assertTrue(fight.vitals_of(target).dead)
                self.assertFalse(any(who is bot for _, who in fight.respawns))
                self.assertNotIn(bot, [t.owner for t in fight._enemies(ball)])
                run.step({ball: (0, 0, 16)})
                self.assertEqual(own.temporary_shields, 100)
                for _ in range(200):
                    run.step({})
                self.assertIn(bot, run.match.bots)
                self.assertIsNotNone(bot.body_script)
                self.assertEqual(fight.vitals_of(target).total, 0)
                self.assertEqual(npc.remove_spawned(run.match), (bot.entity,))

    def test_manual_trooper_sniper_and_heavy_damage_ball_shield_and_stop(self):
        # Native acquisition/navigation and Enforcer/Assassin remain open rows.
        for definition in self.catalog.values():
            if definition.name not in {'Trooper', 'Sniper', 'Heavy Assault'}:
                continue
            with self.subTest(npc=definition.name):
                run, ball, bot, target, own = self.scenario(definition)
                run.step({ball: (0, 0, 16)})
                angles = aim(run.match.combat._eye(ball),
                             (target.feet[0], target.feet[1] + target.height / 2, target.feet[2]))
                run.step({ball: (*angles, 0)})
                self.assertEqual(npc_control.start(run.match, ball), bot.entity)
                for _ in range(200):
                    run.step({})
                    if own.temporary_shields < 175:
                        break
                damage = {'Trooper': 20, 'Sniper': 100, 'Heavy Assault': 5}[definition.name]
                self.assertEqual(own.temporary_shields, 175 - damage)
                self.assertEqual(own.now[world.ARMOUR_POOL], 100)
                self.assertEqual(own.now[world.HEALTH_POOL], 500)
                self.assertIsNotNone(bot.body_script)
                before = own.total
                self.assertEqual(npc_control.stop(run.match), 1)
                for _ in range(80):
                    run.step({})
                self.assertEqual(own.total, before)

    def test_actual_five_melee_hits_require_static_visibility(self):
        for definition in self.catalog.values():
            for blocked in (False, True):
                with self.subTest(npc=definition.name, blocked=blocked):
                    run, ball, _, target, _ = self.scenario(definition, distance=1.5)
                    fight = run.match.combat
                    before = fight.vitals_of(target).total
                    if blocked:
                        x, y, z = ball.mover.position
                        wall_z = z + 0.75
                        triangles = [
                            (x - 20, y, z - 20, x - 20, y, z + 20, x + 20, y, z + 20),
                            (x - 20, y, z - 20, x + 20, y, z + 20, x + 20, y, z - 20),
                            (x - 5, y, wall_z, x + 5, y, wall_z, x + 5, y + 5, wall_z),
                            (x - 5, y, wall_z, x + 5, y + 5, wall_z, x - 5, y + 5, wall_z),
                        ]
                        collision = World(triangles)
                        run.match.collision_world = collision
                        run.match.collision_provider = StaticCollisionProvider(collision)
                        ball.mover.use(world=collision)
                    angles = aim(fight._eye(ball), (target.feet[0], target.feet[1] + 1, target.feet[2]))
                    run.step({ball: (*angles, 0x800)})
                    for _ in range(40):
                        run.step({ball: (*angles, 0)})
                    self.assertEqual(fight.vitals_of(target).total, before - (0 if blocked else 30))
                    self.assertEqual(len(ball.client.messages(combat.HIT)), 0 if blocked else 1)

    def test_actual_five_melee_range_team_rejection_and_reuse(self):
        for definition in self.catalog.values():
            for ally in (False, True):
                with self.subTest(npc=definition.name, ally=ally):
                    run, ball, bot, target, _ = self.scenario(
                        definition, distance=1.5 if ally else 6, ally=ally,
                    )
                    fight = run.match.combat
                    vitals = fight.vitals_of(target)
                    before = vitals.total
                    script = ball.body_script
                    script.ult_seconds = 0

                    x, y, z = bot.position
                    angles = aim(fight._eye(ball), (x, y + 1, z))
                    for _ in range(81):
                        run.step({ball: (*angles, 0x800)})
                    self.assertEqual(vitals.total, before)
                    self.assertEqual(script.component.entity_var(ULT_CHARGE).value(), 0)
                    self.assertFalse(ball.client.messages(combat.HIT))
                    if ally:
                        continue

                    x, y, z = ball.mover.position
                    bot.position = (x, y, z + 1.5)
                    for _ in range(8):
                        run.step({})
                    angles = aim(fight._eye(ball), (x, y + 1, z + 1.5))
                    run.step({ball: (*angles, 0x800)})
                    for _ in range(40):
                        run.step({ball: (*angles, 0)})
                        if vitals.total < before:
                            break
                    self.assertEqual(vitals.total, before - 30)
                    run.step({ball: (*angles, 0x800)})
                    for _ in range(80):
                        run.step({ball: (*angles, 0)})
                    self.assertEqual(vitals.total, before - 30)
                    self.assertEqual(len(ball.client.messages(combat.HIT)), 1)
                    run.step({ball: (*angles, 0x800)})
                    for _ in range(40):
                        run.step({ball: (*angles, 0)})
                    self.assertEqual(vitals.total, before - 60)
                    self.assertEqual(script.component.entity_var(ULT_CHARGE).value(), 60)
                    self.assertEqual(len(ball.client.messages(combat.HIT)), 2)

    def test_piledriver_damages_and_physically_lifts_each_actual_npc_then_lands(self):
        for definition in self.catalog.values():
            with self.subTest(npc=definition.name):
                run, ball, bot, target, _ = self.scenario(definition, distance=1.5)
                fight = run.match.combat
                before = fight.vitals_of(target).total
                ground = bot.position[1]
                lift(run, ball)
                cast(run, ball)
                self.assertEqual(fight.vitals_of(target).total, before - 100)
                self.assertGreater(bot.position[1], ground + 0.2)
                self.assertTrue(bot.airborne)
                self.assertTrue(bot.movement(run.frame).flags & 8)
                self.assertIn(bot.entity, fight.npc_impulses)
                for _ in range(200):
                    run.step({})
                self.assertFalse(bot.airborne)
                self.assertNotIn(bot.entity, fight.npc_impulses)
                self.assertAlmostEqual(bot.position[1], ground, delta=0.01)
                self.assertEqual(fight.vitals_of(target).total, before - 100)

    def test_piledriver_pulls_all_five_actual_npcs_towards_landing_centre(self):
        for definition in self.catalog.values():
            with self.subTest(npc=definition.name):
                run, player, bot, target, _ = self.scenario(definition, distance=4)
                fight = run.match.combat
                vitals = fight.vitals_of(target)
                before = vitals.total
                start = bot.position
                lift(run, player)
                cast(run, player, 1)
                for _ in range(100):
                    run.step({})
                    if vitals.total < before:
                        break
                self.assertLess(vitals.total, before)
                self.assertGreater(bot.velocity[1], 10)
                self.assertLess(bot.velocity[2], -1)
                for _ in range(25):
                    run.step({})
                self.assertLess(bot.position[2], start[2])

    def test_all_five_npc_routes_yield_to_piledriver_then_resume_on_landing(self):
        for definition in self.catalog.values():
            with self.subTest(npc=definition.name):
                run, player, bot, target, _ = self.scenario(definition, distance=1.5)
                fight = run.match.combat
                vitals = fight.vitals_of(target)
                before = vitals.total
                lift(run, player)
                cast(run, player)
                self.assertEqual(vitals.total, before - 100)
                self.assertTrue(bot.airborne)
                x, _, z = bot.position
                ground = target.feet[1]
                bot.route = ((x, ground, z), (x + 4, ground, z))
                bot.index = 0
                bot.wait = 0
                for _ in range(150):
                    run.step({})
                    if not bot.airborne:
                        break
                    self.assertEqual(bot.index, 0)
                    self.assertAlmostEqual(bot.position[0], x)
                    self.assertIn(bot.entity, fight.npc_impulses)
                self.assertFalse(bot.airborne)
                self.assertNotIn(bot.entity, fight.npc_impulses)
                for _ in range(45):
                    run.step({})
                self.assertGreater(bot.position[0], x)
                self.assertEqual(vitals.total, before - 100)

    def test_fireball_hits_each_actual_npc_for_fifty_with_real_motion_then_landing(self):
        for definition in self.catalog.values():
            with self.subTest(npc=definition.name):
                run, player, bot, target, _ = self.scenario(definition)
                prepare_swing(run, player)
                before = run.match.combat.vitals_of(target).total
                start = bot.position
                for _ in range(100):
                    hold(run, player, 1)
                    if run.match.combat.vitals_of(target).total < before:
                        break
                self.assertEqual(run.match.combat.vitals_of(target).total, before - 50)
                self.assertGreater(bot.velocity[2], 20)
                self.assertEqual(bot.velocity[1], 5)
                self.assertTrue(bot.airborne)
                for _ in range(100):
                    run.step({})
                self.assertGreater(bot.position[2], start[2] + 1)
                self.assertAlmostEqual(bot.position[1], start[1], delta=0.02)
                self.assertFalse(bot.airborne)
                self.assertEqual(run.match.combat.vitals_of(target).total, before - 50)

    def test_fireball_side_wall_and_lateral_miss_reject_all_five_actual_npcs(self):
        for definition in self.catalog.values():
            for case, offset in (('visible', 1.5), ('wall', 1.5), ('miss', 6)):
                with self.subTest(npc=definition.name, case=case):
                    run, player, bot, target, _ = self.scenario(definition)
                    prepare_swing(run, player)
                    fight = run.match.combat
                    script = player.body_script
                    script.ult_seconds = 0
                    script.set_ult_charge(0)
                    vitals = fight.vitals_of(target)
                    before = vitals.total
                    x, y, z = bot.position
                    bot.position = (x + offset, y, z)
                    start = bot.position
                    if case == 'wall':
                        add_side_wall(run, player, x + 0.75, y, z)
                    search = ball_instance(player, 0x1203).state(35)
                    entered = False
                    for _ in range(100):
                        hold(run, player, 1)
                        entered |= search.active
                    damage = 50 if case == 'visible' else 0
                    self.assertTrue(entered)
                    self.assertEqual(vitals.total, before - damage)
                    self.assertEqual(script.component.entity_var(ULT_CHARGE).value(), damage)
                    self.assertEqual(len(player.client.messages(combat.HIT)), int(bool(damage)))
                    if not damage:
                        self.assertEqual(bot.position, start)
                        self.assertNotIn(bot.entity, fight.npc_impulses)

    def test_fireball_rehits_each_actual_npc_only_after_outer_exit(self):
        for definition in self.catalog.values():
            with self.subTest(npc=definition.name):
                run, player, bot, target, _ = self.scenario(definition)
                prepare_swing(run, player)
                fight = run.match.combat
                script = player.body_script
                script.ult_seconds = 0
                script.set_ult_charge(0)
                vitals = fight.vitals_of(target)
                before = vitals.total
                first_hit(run, player, bot)
                contacts = fight.ball_contacts[player.body]
                self.assertIn(bot.entity, contacts)
                self.assertEqual(vitals.total, before - 50)
                player.take_commands([Command(run.frame, buttons=2)])
                self.assertEqual(vitals.total, before - 50)

                # Stage the same NPC at successive points while the actual swing continues.
                # Clear the previous impulse so it cannot overwrite the test placements.
                fight.npc_impulses.pop(bot.entity)
                bot.airborne = False
                bot.velocity = (0, 0, 0)
                ground = target.feet[1]
                x, _, z = player.mover.position
                bot.position = (x, ground, z + 2)
                hold(run, player, 1)
                self.assertIn(bot.entity, contacts)
                self.assertEqual(vitals.total, before - 50)
                bot.position = (x, ground, z + 30)
                hold(run, player, 1)
                self.assertNotIn(bot.entity, contacts)
                x, _, z = player.mover.position
                bot.position = (x, ground, z + 2)
                hold(run, player, 1)
                self.assertIn(bot.entity, contacts)
                self.assertEqual(vitals.total, before - 100)
                self.assertEqual(script.component.entity_var(ULT_CHARGE).value(), 100)
                self.assertEqual(len(player.client.messages(combat.HIT)), 2)

    def test_lethal_fireball_and_piledriver_do_not_launch_actual_npc_corpses(self):
        for definition in self.catalog.values():
            for attack in ('fireball', 'piledriver'):
                with self.subTest(npc=definition.name, attack=attack):
                    distance = 6 if attack == 'fireball' else 1.5
                    run, player, bot, target, _ = self.scenario(definition, distance=distance)
                    fight = run.match.combat
                    vitals = fight.vitals_of(target)
                    vitals.damage(vitals.total - 25)
                    remaining = vitals.total
                    self.assertGreater(remaining, 0)
                    self.assertLess(remaining, 50)
                    start = bot.position
                    player.body_script.ult_seconds = 0
                    player.body_script.set_ult_charge(0)
                    charge = player.body_script.component.entity_var(ULT_CHARGE)
                    if attack == 'fireball':
                        prepare_swing(run, player)
                        for _ in range(100):
                            hold(run, player, 1)
                            if bot.dead:
                                break
                    else:
                        lift(run, player)
                        cast(run, player)
                    self.assertTrue(bot.dead)
                    self.assertEqual(vitals.total, 0)
                    self.assertEqual(player.client.deaths[0][0], bot.entity)
                    for _ in range(10):
                        self.assertEqual(charge.value(), remaining)
                        self.assertFalse(bot.airborne)
                        self.assertNotIn(bot.entity, fight.npc_impulses)
                        self.assertEqual(bot.position, start)
                        run.step({})

    def test_rifle_kill_retires_pending_piledriver_motion_before_next_tick(self):
        for definition in self.catalog.values():
            with self.subTest(npc=definition.name):
                run, player, bot, target, _ = self.scenario(definition, distance=1.5, names=('Ball', 'Rifle'))
                support = run.players[1]
                x, y, z = player.mover.position
                support.mover.position[:] = (x + 2, y, z)
                fight = run.match.combat
                vitals = fight.vitals_of(target)
                vitals.damage(vitals.total - 110)
                lift(run, player)
                cast(run, player)
                self.assertGreater(vitals.total, 0)
                self.assertLess(vitals.total, 20)
                self.assertTrue(bot.airborne)
                self.assertIn(bot.entity, fight.npc_impulses)
                bx, by, bz = bot.position
                angles = aim(fight._eye(support), (bx, by + 0.65, bz))
                run.step({support: (*angles, 1)})
                self.assertTrue(bot.dead)
                self.assertFalse(bot.airborne)
                self.assertNotIn(bot.entity, fight.npc_impulses)
                self.assertEqual(support.client.deaths[0][0], bot.entity)
                stopped = bot.position
                for _ in range(10):
                    run.step({})
                self.assertEqual(bot.position, stopped)

    def test_fireball_rejects_each_actual_allied_npc(self):
        for definition in self.catalog.values():
            with self.subTest(npc=definition.name):
                run, player, bot, target, _ = self.scenario(definition, ally=True)
                prepare_swing(run, player)
                before = run.match.combat.vitals_of(target).total
                start = bot.position
                hold(run, player, 100)
                self.assertEqual(run.match.combat.vitals_of(target).total, before)
                self.assertEqual(bot.position, start)
                self.assertFalse(bot.airborne)

    def test_removing_actual_npc_clears_fireball_latch_and_motion(self):
        definition = next(d for d in self.catalog.values() if d.name == 'Trooper')
        run, player, bot, target, _ = self.scenario(definition)
        prepare_swing(run, player)
        fight = run.match.combat
        before = fight.vitals_of(target).total
        for _ in range(100):
            hold(run, player, 1)
            if fight.vitals_of(target).total < before:
                break
        self.assertIn(bot.entity, fight.ball_contacts[player.body])
        self.assertIn(bot.entity, fight.npc_impulses)
        self.assertEqual(npc.remove_spawned(run.match), (bot.entity,))
        self.assertNotIn(bot.entity, fight.ball_contacts[player.body])
        self.assertNotIn(bot.entity, fight.npc_impulses)

    def test_piledriver_rejects_allied_npcs_and_explicit_removal_clears_airborne_motion(self):
        for definition in self.catalog.values():
            with self.subTest(npc=definition.name):
                run, ball, bot, target, _ = self.scenario(definition, distance=1.5, ally=True)
                fight = run.match.combat
                before = fight.vitals_of(target).total
                lift(run, ball)
                cast(run, ball)
                self.assertEqual(fight.vitals_of(target).total, before)
                self.assertFalse(bot.airborne)
                # Exercise the actual physics lifetime/removal boundary separately from ally damage.
                fight._knock_up(target, 11)
                self.assertIn(bot.entity, fight.npc_impulses)
                self.assertEqual(npc.remove_spawned(run.match), (bot.entity,))
                self.assertNotIn(bot.entity, fight.npc_impulses)


if __name__ == '__main__':
    unittest.main()
