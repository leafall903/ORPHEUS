"""Real five-class NPC inputs; run with ORPHEUS_NPC_INPUT_DIR pointing at prepared external data.

Flat collision, permissive NPC VM and adapter transport remain explicit. These are
combat health checks, not authored enemy AI or original-client acceptance.
"""
import math
import os
import unittest
from pathlib import Path
from unittest.mock import patch

import test_echo_combat as echo_tests
from test_echo_combat import ECHO
from test_game_combat import Run, aim, in_world
from test_game_mover import box, wall

from ow174.game import combat, echo_movement, immunity, npc, npc_control, weapon_spread
from ow174.game.collision import World
from ow174.game.commands import JUMP
from ow174.game.content import PRACTICE_RANGE, SOLDIER, heroes
from ow174.game.mover import Mover, loaded_mover_table, mover_data
from ow174.game.script import graph


class EchoActualNpcTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        directory = os.environ.get('ORPHEUS_NPC_INPUT_DIR')
        if not directory:
            raise unittest.SkipTest('set ORPHEUS_NPC_INPUT_DIR to the explicit five-NPC data directory')
        cls.directory = Path(directory)
        cls.catalog = npc.load_catalog(cls.directory / 'pve_npcs_174.json')
        cls.binding = patch.object(graph, 'GRAPHS_PATH', cls.directory / 'game_graphs_174.json.gz')
        cls.binding.start()
        cls.clear_graphs()
        if {d.name for d in cls.catalog.values()} != {
            'Trooper', 'Enforcer', 'Sniper', 'Assassin', 'Heavy Assault'
        }:
            raise AssertionError('all five actual enemy definitions are required')

    @staticmethod
    def clear_graphs():
        graph._data.cache_clear()
        graph._records.cache_clear()
        graph.graph.cache_clear()

    @classmethod
    def tearDownClass(cls):
        cls.binding.stop()
        cls.clear_graphs()

    def session(self, definition, distance=5):
        run = Run(PRACTICE_RANGE)
        run.match.bots.clear()
        run.match._pending_bot_scripts.clear()
        player = run.players[0]
        # The health fixture omits observer projectile replication explicitly.
        player.client.bot_stream = lambda _entity: None
        run.match.switch_hero(player, heroes()[ECHO])
        x, y, z = player.mover.position
        bot = npc.spawn(run.match, definition, (x, y, z + distance), team=1)
        self.assertIn(bot.body, loaded_mover_table() or {},
                      'NPC geometry requires an authored mover row; generic fallback is not acceptance')
        self.assertIsNotNone(bot.body_script)
        self.assertEqual(bot.body, definition.body)
        self.assertTrue(bot.retain_corpse)
        for _ in range(20):
            run.step({})
        fight = run.match.combat
        target = next(t for t in fight._targets() if t.owner is bot)
        angles = aim(fight._eye(player), (x, y + 1, z + distance))
        return run, player, bot, target, angles

    def test_copied_cassidy_roll_refill_recovers_primary_and_fan_on_all_five_npcs(self):
        for definition in self.catalog.values():
            for mode in ('partial-primary', 'empty-primary', 'empty-fan', 'ally-primary', 'ally-fan'):
                with self.subTest(npc=definition.name, mode=mode):
                    run, player, bot, target, _ = self.session(definition)
                    # Health/ammo fixture deliberately omits projectile/death transport.
                    player.client.queue_bot_death = lambda *_args, **_kwargs: None
                    fight = run.match.combat
                    cassidy = heroes()[0x02E0000000000042]
                    enemy = run.match.add_player(2, 'Cassidy copy target', cassidy.guid, 1, False)
                    in_world(run.match, enemy)
                    enemy.client.bot_stream = lambda _entity: None
                    enemy.client.queue_bot_death = lambda *_args, **_kwargs: None
                    run.match.switch_hero(enemy, cassidy)
                    run.players.append(enemy)
                    x, y, z = player.mover.position
                    enemy.mover = Mover((x+8, y, z+5), world=run.match.collision_world,
                                        data=mover_data(cassidy.body))
                    for _ in range(25):
                        run.step({})
                    player.body_script.set_ult_charge(player.body_script.component.entity_var(338).value())
                    toward = aim(fight._eye(player), (x+8, y+1, z+5))
                    run.step({player: (*toward, 32)})
                    self.assertEqual(player.hero.guid, cassidy.guid)
                    self.assertTrue(fight.echo_duplicates.active(player))
                    player.body_script.ult_seconds = 0
                    for _ in range(25):
                        run.step({})
                    weapon = next(i for i in player.body_script.component.instances.values()
                                  if i.graph.index == 0x1D2)
                    if mode.startswith('partial'):
                        run.step({player: (32768, 0, 1)})
                        for _ in range(40):
                            run.step({})
                        self.assertEqual(weapon.vars[53].value(), 5)
                    else:
                        for _ in range(240):
                            run.step({player: (32768, 0, 1)})
                            if weapon.vars[53].value() == 0:
                                break
                        self.assertEqual(weapon.vars[53].value(), 0)
                        for _ in range(5):
                            run.step({})
                    run.step({player: (0, 0, 8)})
                    for _ in range(35):
                        run.step({})
                    self.assertEqual(weapon.vars[53].value(), 6)
                    if mode.startswith('ally'):
                        bot.team = player.team
                    before = fight.vitals_of(target).total
                    low, high = target.segment()
                    centre = tuple((a+b)*.5 for a, b in zip(low, high, strict=True))
                    attack = aim(fight._eye(player), centre)
                    fan = mode.endswith('fan')
                    if fan:
                        for _ in range(90):
                            run.step({player: (*attack, 2)})
                            if weapon.vars[53].value() == 0:
                                break
                    else:
                        run.step({player: (*attack, 1)})
                        for _ in range(10):
                            run.step({})
                    self.assertEqual(weapon.vars[53].value(), 0 if fan else 5)
                    expected = 0 if mode.startswith('ally') else min(before, 300 if fan else 70)
                    self.assertEqual(before-fight.vitals_of(target).total, expected)

    def test_copied_fan_hits_all_five_npcs_with_aim_centre_outside_capsule(self):
        for definition in self.catalog.values():
            with self.subTest(npc=definition.name):
                results = []
                for side in (-1, 1):
                    run, player, _, target, _ = self.session(definition, distance=8)
                    fight = run.match.combat
                    cassidy = heroes()[0x02E0000000000042]
                    enemy = run.match.add_player(2, "Copy target", cassidy.guid, 1, False)
                    in_world(run.match, enemy)
                    enemy.client.bot_stream = lambda _entity: None
                    run.match.switch_hero(enemy, cassidy)
                    run.players.append(enemy)
                    x, y, z = player.mover.position
                    enemy.mover = Mover((x+9, y, z), world=run.match.collision_world,
                                        data=mover_data(cassidy.body))
                    for _ in range(25):
                        run.step({})
                    player.body_script.set_ult_charge(player.body_script.component.entity_var(338).value())
                    direction = aim(fight._eye(player), fight._eye(enemy))
                    run.step({player: (*direction, 32)})
                    self.assertTrue(fight.echo_duplicates.active(player))
                    for _ in range(25):
                        run.step({})
                    origin = fight._eye(player)
                    direction = aim(origin, (x+side*(target.radius+.05), y+target.height/2, z+8))
                    self.assertIsNone(combat.ray_capsule(origin, combat.aim(*direction),
                                                         *target.segment(), target.radius))
                    weapon = next(i for i in player.body_script.component.instances.values()
                                  if i.graph.index == 0x1D2)
                    self.assertEqual(weapon.vars[53].value(), 6)
                    vitals = fight.vitals_of(target)
                    before = vitals.total
                    samples = []
                    original = weapon_spread.fan_direction

                    def observe(shooter, state, forward, number, time, activation, *,
                                original=original, player=player, samples=samples):
                        vector = original(shooter, state, forward, number, time, activation)
                        fan = state.instance.graph.index == 0x01D2 and state.node.pos == 37
                        if shooter is player and fan:
                            samples.append(vector)
                        return vector

                    with patch.object(weapon_spread, "fan_direction", side_effect=observe):
                        for _ in range(65):
                            run.step({player: (*direction, 2)})
                            if weapon.vars[53].value() == 0:
                                break
                    self.assertEqual(weapon.vars[53].value(), 0)
                    self.assertEqual(len(set(samples)), 6)
                    results.append(before - vitals.total)
                self.assertGreater(max(results), 0)

    def test_copied_winston_world_bubble_preserves_all_five_npc_team_semantics(self):
        for definition in self.catalog.values():
            for protected in (True,False):
                with self.subTest(npc=definition.name,protected=protected):
                    run,caster,bot,target,_ = self.session(definition,distance=4)
                    fight = run.match.combat
                    winston = heroes()[0x02E0000000000009]
                    enemy = run.match.add_player(2,'Winston copy target',winston.guid,1,False)
                    in_world(run.match,enemy)
                    enemy.client.bot_stream = lambda _entity: None
                    run.match.switch_hero(enemy,winston)
                    run.players.append(enemy)
                    x,y,z = caster.mover.position
                    enemy.mover = Mover((x+8,y,z+5),world=run.match.collision_world,
                                        data=mover_data(winston.body))
                    for _ in range(25):
                        run.step({})
                    caster.body_script.set_ult_charge(caster.body_script.component.entity_var(338).value())
                    run.step({caster:(*aim(fight._eye(caster),(x+8,y+1,z+5)),32)})
                    self.assertEqual(caster.hero.guid,winston.guid)
                    run.match.switch_hero(enemy,heroes()[ECHO],preserve_pose=True)
                    enemy.team = 1 if protected else caster.team
                    bot.team = caster.team if protected else 1
                    enemy.mover = Mover((x,y,z+8),world=run.match.collision_world,
                                        data=mover_data(enemy.hero.body))
                    for _ in range(25):
                        run.step({})
                    run.step({caster:(0,0,16)})
                    for _ in range(10):
                        run.step({})
                    actor = next(iter(fight.barriers.world.actors.values()))
                    before = fight.vitals_of(target).total
                    a,b = target.segment()
                    attack = aim(fight._eye(enemy),tuple((p+q)*.5 for p,q in zip(a,b,strict=True)))
                    run.step({enemy:(*attack,1)})
                    for _ in range(25):
                        run.step({})
                    self.assertEqual(before-fight.vitals_of(target).total,0 if protected else 51)
                    self.assertEqual(actor.health,649 if protected else 700)

    def test_primary_melee_sticky_and_beam_damage_each_actual_npc_class(self):
        for definition in self.catalog.values():
            for button, frames, expected in ((1, 15, 51), (0x800, 25, 30), (2, 140, 180), (16, 20, None)):
                with self.subTest(npc=definition.name, button=button):
                    run, player, _, target, angles = self.session(definition, 2 if button == 0x800 else 5)
                    fight = run.match.combat
                    initial = fight.vitals_of(target).total
                    run.step({player: (*angles, button)})
                    for _ in range(frames):
                        run.step({player: (*angles, 0)})
                    loss = initial - fight.vitals_of(target).total
                    if expected is None:
                        self.assertGreater(loss, 0)
                        self.assertLess(loss, 20)
                        before = fight.vitals_of(target).total
                        run.step({player: (*angles, 0)})
                        self.assertAlmostEqual(before - fight.vitals_of(target).total, .8)
                    else:
                        self.assertAlmostEqual(loss, expected)

    def test_all_five_actual_npc_classes_keep_friendly_team_semantics(self):
        for definition in self.catalog.values():
            with self.subTest(npc=definition.name):
                run, player, bot, target, angles = self.session(definition, 2)
                bot.team = player.team
                initial = run.match.combat.vitals_of(target).total
                for button in (1, 0x800, 2, 16):
                    run.step({player: (*angles, button)})
                    for _ in range(145):
                        run.step({player: (*angles, 0)})
                self.assertEqual(run.match.combat.vitals_of(target).total, initial)

    def test_authored_trooper_heavy_and_sniper_fire_inputs_can_damage_echo(self):
        # Actual manual target selection, authored AI slot input and stop; no direct shot injection.
        for definition in self.catalog.values():
            expected = {'Trooper': 20, 'Heavy Assault': 5, 'Sniper': 100}
            if definition.name not in expected:
                continue
            with self.subTest(npc=definition.name):
                run, player, bot, _, angles = self.session(definition)
                fight = run.match.combat
                echo = next(t for t in fight._targets() if t.owner is player)
                initial = fight.vitals_of(echo).total
                player.mover.yaw, player.mover.pitch = angles
                self.assertEqual(npc_control.start(run.match, player), bot.entity)
                for _ in range(200):
                    run.step({})
                    if fight.vitals_of(echo).total < initial:
                        break
                self.assertAlmostEqual(initial - fight.vitals_of(echo).total,
                                       expected[definition.name])
                self.assertEqual(npc_control.stop(run.match), 1)
                stopped = fight.vitals_of(echo).total
                for _ in range(80):
                    run.step({})
                self.assertEqual(fight.vitals_of(echo).total, stopped)

    def test_echo_takes_a_hit_then_returns_tri_shot_against_each_talon_class(self):
        self.check_talon_counterfire(1, 5, 51)

    def test_echo_takes_a_hit_then_returns_melee_against_each_talon_class(self):
        self.check_talon_counterfire(0x800, 2, 30)

    def check_talon_counterfire(self, button, reach, reply_damage):
        damage = {'Trooper': 20, 'Enforcer': 2, 'Sniper': 100, 'Assassin': 25, 'Heavy Assault': 5}
        for definition in self.catalog.values():
            with self.subTest(npc=definition.name):
                distance = min(reach, 2.5) if definition.name == 'Assassin' else reach
                run, player, bot, target, angles = self.session(definition, distance)
                fight = run.match.combat
                own = next(t for t in fight._targets() if t.owner is player)
                vitals = fight.vitals_of(own)
                enemy_vitals = fight.vitals_of(target)
                before = enemy_vitals.total
                player.mover.yaw, player.mover.pitch = angles
                self.assertEqual(npc_control.start(run.match, player), bot.entity)
                for _ in range(300):
                    run.step({})
                    if vitals.total < 200:
                        break
                self.assertEqual(200 - vitals.total, damage[definition.name])
                self.assertEqual(npc_control.stop(run.match), 1)
                self.assertFalse(fight.is_stunned(player))

                target = next(t for t in fight._targets() if t.owner is bot)
                x, y, z = target.feet
                reply = aim(fight._eye(player), (x, y + 1, z))
                run.step({player: (*reply, button)})
                for _ in range(25):
                    run.step({player: (*reply, 0)})
                self.assertEqual(before - enemy_vitals.total, reply_damage)

    def test_all_five_talon_attacks_hit_echo_during_sticky_volley(self):
        # Lead-in frames let each authored attack reach Echo before the sixth bomb.
        timing = {'Trooper': (14, 20), 'Enforcer': (59, 2), 'Sniper': (120, 100),
                  'Assassin': (0, 25), 'Heavy Assault': (24, 5)}
        for definition in self.catalog.values():
            with self.subTest(npc=definition.name):
                assassin = definition.name == 'Assassin'
                run, player, bot, _, angles = self.session(definition, 2.5 if assassin else 5)
                fight = run.match.combat
                own = next(t for t in fight._targets() if t.owner is player)
                vitals = fight.vitals_of(own)
                lead, damage = timing[definition.name]
                if assassin:
                    run.step({player: (-32768, 0, 2)})
                    for _ in range(7):
                        run.step({player: (-32768, 0, 0)})
                player.mover.yaw, player.mover.pitch = angles
                self.assertEqual(npc_control.start(run.match, player), bot.entity)
                if not assassin:
                    for _ in range(lead):
                        run.step({})
                    self.assertEqual(vitals.total, 200)
                    run.step({player: (-32768, 0, 2)})
                for _ in range(20):
                    run.step({player: (-32768, 0, 0)})
                    if vitals.total < 200:
                        break
                self.assertEqual(200 - vitals.total, damage)
                launched = sum(r.shooter is player for r in fight.rockets)
                self.assertGreater(launched, 0)
                self.assertLess(launched, 6)
                if assassin:
                    self.assertEqual(fight.pins.get(bot.entity), player.body)
                self.assertEqual(npc_control.stop(run.match), 1)
                for _ in range(24):
                    run.step({player: (-32768, 0, 0)})
                self.assertEqual(sum(r.shooter is player for r in fight.rockets), launched if assassin else 6)

    def test_talon_attacks_hit_airborne_echo_and_assassin_pin_stops_flight(self):
        damage = {'Trooper': 20, 'Enforcer': 2, 'Sniper': 100, 'Assassin': 25, 'Heavy Assault': 5}
        for definition in self.catalog.values():
            for button in (8, JUMP):
                with self.subTest(npc=definition.name, button=button):
                    assassin = definition.name == 'Assassin'
                    pin_interrupts_flight = assassin and button == 8
                    distance = 2.5 if assassin and button == JUMP else 5
                    run, player, bot, target, angles = self.session(definition, distance)
                    fight = run.match.combat
                    own = next(t for t in fight._targets() if t.owner is player)
                    vitals = fight.vitals_of(own)
                    if pin_interrupts_flight:
                        # Select the Assassin after takeoff, once Echo is hovering in pin range.
                        for _ in range(20):
                            run.step({player: (0, 0, button)})
                        x, y, z = target.feet
                        angles = aim(fight._eye(player), (x, y + 1, z))
                    player.mover.yaw, player.mover.pitch = angles
                    self.assertEqual(npc_control.start(run.match, player), bot.entity)
                    if button == JUMP:
                        x, y, z = player.mover.position
                        player.mover = Mover((x, y + (2 if assassin else 10), z),
                                             world=run.match.collision_world,
                                             data=mover_data(player.hero.body))
                    if not pin_interrupts_flight:
                        run.step({player: (0, 0, button)})
                    for _ in range(180):
                        active = (echo_movement.flight(player) if button == 8
                                  else echo_movement.glide_properties(player, JUMP))
                        self.assertIsNotNone(active)
                        run.step({player: (0, 0, button)})
                        pinned = pin_interrupts_flight and fight.pins.get(bot.entity) == player.body
                        if vitals.total < 200 or pinned:
                            break
                    if pin_interrupts_flight:
                        self.assertEqual(fight.pins.get(bot.entity), player.body)
                        self.assertIsNone(echo_movement.flight(player))
                        for _ in range(30):
                            if vitals.total < 200:
                                break
                            run.step({})
                    self.assertEqual(200 - vitals.total, damage[definition.name])
                    self.assertEqual(npc_control.stop(run.match), 1)

    def test_flight_and_glide_proximity_does_not_damage_players_or_talon(self):
        for definition in (None, *self.catalog.values()):
            for button in (8, JUMP):
                with self.subTest(target=definition.name if definition else 'Soldier', button=button):
                    if definition is None:
                        run, player, _, target, _ = echo_tests.EchoPrimaryTests().session(distance=1.25)
                    else:
                        run, player, _, target, _ = self.session(definition, distance=1.25)
                    fight = run.match.combat
                    vitals = fight.vitals_of(target)
                    initial = vitals.total
                    x, y, z = target.feet
                    if button == JUMP:
                        player.mover = Mover((x, y + 1.4, z), world=run.match.collision_world,
                                             data=mover_data(player.hero.body))
                    closest = math.inf
                    for _ in range(30):
                        run.step({player: (0, 0, button)})
                        mode = (echo_movement.flight(player) if button == 8
                                else echo_movement.glide_properties(player, JUMP))
                        if mode is not None:
                            closest = min(closest, math.dist(player.mover.position, target.feet))
                    self.assertLess(closest, .8)
                    self.assertEqual(vitals.total, initial)
                    self.assertFalse(fight.rockets)
                    self.assertEqual(player.client.messages(combat.HIT), [])

    def test_enforcer_volleys_respect_wall_team_and_manual_stop(self):
        definition = next(item for item in self.catalog.values() if item.name == "Enforcer")
        run, player, bot, _, angles = self.session(definition)
        fight = run.match.combat
        echo = next(target for target in fight._targets() if target.owner is player)
        vitals = fight.vitals_of(echo)
        initial = vitals.total
        player.mover.yaw, player.mover.pitch = angles
        self.assertEqual(npc_control.start(run.match, player), bot.entity)
        collision = run.match.collision_world
        x, y, z = player.mover.position
        run.match.collision_world = World(box(x-20, x+20, y-1, y, z-20, z+20)
                                          + wall(x-10, z+2, x+10, z+2, y0=y, y1=y+6))
        for _ in range(300):
            run.step({})
        self.assertFalse(bot.attack_input)
        self.assertEqual(vitals.total, initial)
        run.match.collision_world = collision
        bot.team = player.team
        for _ in range(80):
            run.step({})
        self.assertFalse(bot.attack_input)
        self.assertEqual(vitals.total, initial)
        bot.team = 1 - player.team
        self.assertEqual(npc_control.start(run.match, player), bot.entity)
        for _ in range(300):
            run.step({})
            if vitals.total < initial:
                break
        self.assertEqual(initial - vitals.total, 2)
        self.assertEqual(npc_control.stop(run.match), 1)
        for _ in range(100):
            run.step({})
        self.assertEqual(initial - vitals.total, 2)

    def test_assassin_pin_hits_controls_and_releases_echo(self):
        definition = next(item for item in self.catalog.values() if item.name == "Assassin")
        run, player, bot, _, angles = self.session(definition, distance=2.5)
        fight = run.match.combat
        echo = next(target for target in fight._targets() if target.owner is player)
        vitals = fight.vitals_of(echo)
        initial = vitals.total
        player.mover.yaw, player.mover.pitch = angles
        self.assertEqual(npc_control.start(run.match, player), bot.entity)
        for _ in range(120):
            run.step({})
            if fight.is_stunned(player):
                break
        self.assertEqual(fight.pins, {bot.entity: player.body})
        self.assertEqual(initial - vitals.total, 25)
        weapon = next(i for i in player.body_script.component.instances.values() if i.graph.index == 0x19E5)
        ammo = weapon.vars[53].value()
        for _ in range(180):
            run.step({player: (*angles, 1)})
            if vitals.total < initial - 25:
                break
        self.assertLess(vitals.total, initial - 25)
        after_first_tick = vitals.total
        for _ in range(10):
            run.step({player: (*angles, 1)})
        self.assertAlmostEqual(after_first_tick - vitals.total, 16, places=4)
        self.assertEqual(weapon.vars[53].value(), ammo)
        self.assertEqual(npc_control.stop(run.match), 1)
        self.assertFalse(fight.is_stunned(player))
        stopped = vitals.total
        run.step({})
        for _ in range(40):
            run.step({player: (*angles, 1)})
        self.assertEqual(vitals.total, stopped)
        self.assertLess(weapon.vars[53].value(), ammo)

    def test_four_ranged_talon_attacks_damage_echo_during_active_beam(self):
        # Start the beam before the first shot of each NPC's actual input sequence.
        timing = {'Trooper': (2, 20), 'Enforcer': (47, 2), 'Sniper': (108, 100), 'Heavy Assault': (12, 5)}
        for definition in self.catalog.values():
            if definition.name not in timing:
                continue
            with self.subTest(npc=definition.name):
                run, player, bot, target, angles = self.session(definition)
                fight = run.match.combat
                own = next(t for t in fight._targets() if t.owner is player)
                vitals = fight.vitals_of(own)
                npc_vitals = fight.vitals_of(target)
                initial = npc_vitals.total
                lead, damage = timing[definition.name]
                player.mover.yaw, player.mover.pitch = angles
                self.assertEqual(npc_control.start(run.match, player), bot.entity)
                for _ in range(lead):
                    run.step({})
                self.assertEqual(vitals.total, 200)
                run.step({player: (*angles, 16)})
                for _ in range(40):
                    run.step({player: (*angles, 0)})
                    if vitals.total < 200:
                        break
                self.assertEqual(200 - vitals.total, damage)
                beam = next(i for i in player.body_script.component.instances.values()
                            if i.graph.index == 0x1FBD).states[17]
                self.assertTrue(beam.active)
                self.assertLess(npc_vitals.total, initial)
                before = npc_vitals.total
                run.step({player: (*angles, 0)})
                self.assertAlmostEqual(before - npc_vitals.total, .8)
                self.assertEqual(npc_control.stop(run.match), 1)

    def test_assassin_pin_interrupts_active_echo_beam(self):
        definition = next(item for item in self.catalog.values() if item.name == "Assassin")
        run, player, bot, target, angles = self.session(definition, distance=2.5)
        fight = run.match.combat
        vitals = fight.vitals_of(target)
        initial = vitals.total
        for _ in range(20):
            run.step({player: (*angles, 16)})
        beam = next(i for i in player.body_script.component.instances.values()
                    if i.graph.index == 0x1FBD).states[17]
        self.assertTrue(beam.active)
        self.assertLess(vitals.total, initial)
        self.assertEqual(npc_control.start(run.match, player), bot.entity)
        for _ in range(100):
            self.assertTrue(beam.active)
            run.step({player: (-32768, 0, 16)})
            if fight.pins.get(bot.entity) == player.body:
                break
        self.assertEqual(fight.pins.get(bot.entity), player.body)
        after_pin = vitals.total
        for _ in range(10):
            run.step({player: (*angles, 16)})
        self.assertFalse(beam.active)
        self.assertEqual(vitals.total, after_pin)

    def test_assassin_refuses_range_wall_ally_and_releases_on_occlusion(self):
        definition = next(item for item in self.catalog.values() if item.name == "Assassin")
        run, player, bot, _, angles = self.session(definition)
        fight = run.match.combat
        echo = next(target for target in fight._targets() if target.owner is player)
        vitals = fight.vitals_of(echo)
        initial = vitals.total
        player.mover.yaw, player.mover.pitch = angles
        self.assertEqual(npc_control.start(run.match, player), bot.entity)
        for _ in range(40):
            run.step({})
        self.assertFalse(bot.attack_input)
        self.assertEqual(vitals.total, initial)
        x, y, z = player.mover.position
        bot.position = (x, y, z+2.5)
        collision = run.match.collision_world
        obstruction = World(box(x-20, x+20, y-1, y, z-20, z+20)
                            + wall(x-10, z+1, x+10, z+1, y0=y, y1=y+6))
        run.match.collision_world = obstruction
        for _ in range(40):
            run.step({})
        self.assertFalse(bot.attack_input)
        self.assertEqual(vitals.total, initial)
        run.match.collision_world = collision
        bot.team = player.team
        for _ in range(40):
            run.step({})
        self.assertFalse(bot.attack_input)
        self.assertEqual(vitals.total, initial)
        bot.team = 1 - player.team
        for _ in range(120):
            run.step({})
            if fight.is_stunned(player):
                break
        self.assertEqual(fight.pins, {bot.entity: player.body})
        self.assertEqual(vitals.total, initial - 25)
        run.match.collision_world = obstruction
        for _ in range(100):
            run.step({})
        self.assertFalse(fight.is_stunned(player))
        self.assertIsNone(bot.body_script.component.world.manual_target)
        self.assertEqual(vitals.total, initial - 25)

    def test_copied_wraith_rejects_assassin_pin_until_phase_ends(self):
        definition = next(item for item in self.catalog.values() if item.name == "Assassin")
        run, player, bot, _, angles = self.session(definition, distance=2.5)
        fight = run.match.combat
        reaper = heroes()[0x02E0000000000002]
        enemy = run.match.add_player(2, 'Copy target', reaper.guid, 1, False)
        in_world(run.match, enemy)
        enemy.client.bot_stream = lambda _entity: None
        run.match.switch_hero(enemy, reaper)
        run.players.append(enemy)
        x, y, z = player.mover.position
        enemy.mover = Mover((x+8, y, z+5), world=run.match.collision_world, data=mover_data(reaper.body))
        player.body_script.set_ult_charge(player.body_script.component.entity_var(338).value())
        toward = aim(fight._eye(player), (x+8, y+1, z+5))
        run.step({player: (*toward, 32)})
        self.assertEqual(player.hero.guid, reaper.guid)
        run.step({})
        run.step({player: (*angles, 8)})
        self.assertTrue(immunity.wraith_active(player))
        own = next(t for t in fight._targets() if t.owner is player)
        vitals = fight.vitals_of(own)
        self.assertEqual(npc_control.start(run.match, player), bot.entity)
        for _ in range(300):
            run.step({})
            if not immunity.wraith_active(player):
                break
            self.assertEqual(vitals.total, 250)
            self.assertFalse(fight.pins)
            self.assertFalse(bot.attack_input)
        self.assertFalse(immunity.wraith_active(player))
        for _ in range(120):
            run.step({})
            if fight.is_stunned(player):
                break
        self.assertEqual(fight.pins, {bot.entity: player.body})
        self.assertEqual(vitals.total, 225)

    def test_assassin_pin_damage_kills_echo_and_releases_retired_body(self):
        definition = next(item for item in self.catalog.values() if item.name == "Assassin")
        run, player, bot, _, angles = self.session(definition, distance=2.5)
        fight = run.match.combat
        player.mover.yaw, player.mover.pitch = angles
        old_body = player.body
        self.assertEqual(npc_control.start(run.match, player), bot.entity)
        for _ in range(450):
            run.step({})
            if fight.is_dead(player):
                break
        self.assertTrue(fight.is_dead(player))
        self.assertEqual(fight.vitals[old_body].killer, bot.entity)
        self.assertFalse(fight.pins)
        npc_control.stop(run.match)
        for _ in range(700):
            run.step({})
            if player.body != old_body and not fight.is_dead(player):
                break
        self.assertNotEqual(player.body, old_body)
        self.assertFalse(fight.is_dead(player))
        self.assertFalse(fight.is_stunned(player))

    def test_flashbang_expiry_airburst_releases_assassin_pin(self):
        definition = next(item for item in self.catalog.values() if item.name == "Assassin")
        run, player, bot, target, angles = self.session(definition, distance=2.5)
        fight = run.match.combat
        cassidy = heroes()[0x02E0000000000042]
        ally = run.match.add_player(2, "Rescuer", cassidy.guid, player.team, False)
        in_world(run.match, ally)
        ally.client.bot_stream = lambda _entity: None
        run.match.switch_hero(ally, cassidy)
        run.players.append(ally)
        x, y, z = bot.position
        ally.mover = Mover((x+7, y, z+2), world=run.match.collision_world, data=mover_data(cassidy.body))
        for _ in range(25):
            run.step({})
        player.mover.yaw, player.mover.pitch = angles
        self.assertEqual(npc_control.start(run.match, player), bot.entity)
        for _ in range(120):
            run.step({})
            if fight.is_stunned(player):
                break
        self.assertEqual(fight.pins, {bot.entity: player.body})
        before = fight.vitals_of(target).total
        direction = aim(fight._eye(ally), (x, fight._eye(ally)[1], z+2))
        run.step({ally: (*direction, 16)})
        grenade = None
        for _ in range(65):
            run.step({})
            grenade = next((r for r in fight.rockets if r.shooter is ally), grenade)
            if fight.is_stunned(bot):
                break
        self.assertIsNotNone(grenade)
        self.assertTrue(grenade.detonate_on_expiry)
        self.assertAlmostEqual(grenade.flown, 7/30)
        self.assertNotIn(grenade, fight.rockets)
        self.assertEqual(fight.vitals_of(target).total, before - 25)
        self.assertTrue(fight.is_stunned(bot))
        self.assertFalse(fight.is_stunned(player))
        self.assertFalse(fight.pins)

    def test_copied_deadeye_fires_at_each_of_the_five_authored_enemies(self):
        definitions = list(self.catalog.values())
        run, player, first, _, _ = self.session(definitions[0], distance=8)
        fight = run.match.combat
        cassidy = heroes()[0x02E0000000000042]
        enemy = run.match.add_player(2, "Copy target", cassidy.guid, 1, False)
        in_world(run.match, enemy)
        run.match.switch_hero(enemy, cassidy)
        run.players.append(enemy)
        for observer in run.players:
            observer.client.bot_stream = lambda _entity: None
            observer.client.queue_bot_death = lambda *_args, **_kwargs: None
            observer.client.queue_bot_death_update = lambda *_args, **_kwargs: None
        x, y, z = player.mover.position
        enemy.mover = Mover((x+9, y, z), world=run.match.collision_world, data=mover_data(cassidy.body))
        first.position = (x-6, y, z+8)
        for index, definition in enumerate(definitions[1:], 1):
            npc.spawn(run.match, definition, (x+(index-2)*3, y, z+8), team=1)
        ally = npc.spawn(run.match, definitions[0], (x+1.5, y, z+7), team=player.team)
        for _ in range(25):
            run.step({})
        player.body_script.set_ult_charge(player.body_script.component.entity_var(338).value())
        direction = aim(fight._eye(player), fight._eye(enemy))
        run.step({player: (*direction, 32)})
        self.assertTrue(fight.echo_duplicates.active(player))
        self.assertEqual(player.hero.guid, cassidy.guid)
        for _ in range(25):
            run.step({})
        component = player.body_script.component
        player.body_script.ult_seconds = 0
        player.body_script.set_ult_charge(component.entity_var(338).value())
        run.step({player: (0, 0, 32)})
        for _ in range(125):
            run.step({})
        enemies = {actor.entity for actor in run.match.bots if actor is not ally}
        locked = {i.vars[536].value().id for i in component.instances.values() if i.graph.index == 0x01EE}
        self.assertEqual(locked, enemies)
        health = {target.entity: fight.vitals_of(target) for target in fight._targets()
                  if target.entity in enemies or target.owner is ally}
        initial = {entity: vitals.total for entity, vitals in health.items()}
        run.step({player: (0, 0, 1)})
        for _ in range(150):
            run.step({})
        for entity in enemies:
            self.assertLess(health[entity].total, initial[entity], hex(entity))
        self.assertEqual(health[ally.entity].total, initial[ally.entity])
        self.assertFalse(any(i.graph.index == 0x01EE for i in component.instances.values()))
        self.assertTrue(fight.echo_duplicates.active(player))

    def test_actual_five_npc_deaths_retain_corpse_after_real_primary_fire(self):
        for definition in self.catalog.values():
            with self.subTest(npc=definition.name):
                run, player, bot, target, angles = self.session(definition)
                terminal = []
                player.client.queue_bot_death = (
                    lambda entity, frames, sink=terminal: sink.append((entity, frames))
                )
                player.client.queue_bot_death_update = (
                    lambda entity, event, sink=terminal: sink.append((entity, event))
                )
                fight = run.match.combat
                weapon = next(i for i in player.body_script.component.instances.values()
                              if i.graph.index == 0x19E5)
                for _ in range(5000):
                    ammo = weapon.vars[53].value()
                    run.step({player: (*angles, 1 if ammo > 0 else 0x400)})
                    if bot.dead:
                        break
                self.assertTrue(bot.dead)
                self.assertEqual(fight.vitals_of(target).total, 0)
                self.assertTrue(bot.body_script.component.owner_dead)
                self.assertTrue(terminal)
                for _ in range(220):
                    run.step({})
                self.assertIn(bot, run.match.bots)
                self.assertFalse(any(who is bot for _, who in fight.respawns))
                self.assertNotIn(bot.entity, [t.entity for t in fight._enemies(player)])

    def test_duplicate_rejects_each_nonplayable_npc_without_spending_charge(self):
        for definition in self.catalog.values():
            with self.subTest(npc=definition.name):
                run, player, _, _, angles = self.session(definition)
                cost = player.body_script.component.entity_var(338).value()
                player.body_script.set_ult_charge(cost)
                body = player.body
                run.step({player: (*angles, 32)})
                self.assertEqual(player.hero.guid, ECHO)
                self.assertEqual(player.body, body)
                self.assertEqual(player.body_script.component.entity_var(342).value(), cost)
                self.assertFalse(run.match.combat.echo_duplicates.active(player))

    def test_all_five_actual_npcs_damage_echo_during_duplicate(self):
        damage = {'Trooper': 20, 'Enforcer': 2, 'Sniper': 100, 'Assassin': 25, 'Heavy Assault': 5}
        for definition in self.catalog.values():
            with self.subTest(npc=definition.name):
                distance = 2.5 if definition.name == 'Assassin' else 5
                run, player, bot, _, angles = self.session(definition, distance=distance)
                fight = run.match.combat
                enemy = run.match.add_player(2, 'Soldier copy target', SOLDIER, 1, False)
                in_world(run.match, enemy)
                enemy.client.bot_stream = lambda _entity: None
                run.match.switch_hero(enemy, heroes()[SOLDIER])
                run.players.append(enemy)
                x, y, z = player.mover.position
                enemy.mover = Mover((x+8, y, z+5), world=run.match.collision_world,
                                    data=mover_data(enemy.hero.body))
                player.body_script.set_ult_charge(player.body_script.component.entity_var(338).value())
                copy_angles = aim(fight._eye(player), (x+8, y+1, z+5))
                run.step({player: (*copy_angles, 32)})
                self.assertTrue(fight.echo_duplicates.active(player))
                self.assertEqual(player.hero.guid, SOLDIER)
                copied_body = player.body
                own = next(t for t in fight._targets() if t.owner is player)
                vitals = fight.vitals_of(own)
                self.assertEqual(vitals.total, 200)
                player.mover.yaw, player.mover.pitch = angles
                self.assertEqual(npc_control.start(run.match, player), bot.entity)
                for _ in range(300):
                    run.step({})
                    if vitals.total < 200:
                        break
                self.assertEqual(200 - vitals.total, damage[definition.name])
                self.assertEqual(player.body, copied_body)
                self.assertTrue(fight.echo_duplicates.active(player))
                if definition.name == 'Assassin':
                    self.assertEqual(fight.pins.get(bot.entity), copied_body)
                    for _ in range(450):
                        run.step({})
                        if player.body != copied_body:
                            break
                    self.assertEqual(player.hero.guid, ECHO)
                    self.assertFalse(fight.echo_duplicates.active(player))
                    self.assertFalse(fight.is_dead(player))
                    self.assertNotIn(copied_body, fight.pins.values())
                npc_control.stop(run.match)
                run.step({})  # Deliver the release before starting another manual attack.
                for _ in range(950):
                    if not fight.echo_duplicates.active(player):
                        break
                    run.step({})
                self.assertEqual(player.hero.guid, ECHO)
                self.assertNotEqual(player.body, copied_body)
                self.assertFalse(fight.echo_duplicates.active(player))
                restored = next(t for t in fight._targets() if t.owner is player)
                restored_vitals = fight.vitals_of(restored)
                self.assertEqual(restored_vitals.total, 200)
                target = next(t for t in fight._targets() if t.owner is bot)
                a, b = target.segment()
                centre = tuple((lo + hi) / 2 for lo, hi in zip(a, b, strict=True))
                player.mover.yaw, player.mover.pitch = aim(fight._eye(player), centre)
                self.assertEqual(npc_control.start(run.match, player), bot.entity)
                for _ in range(450):
                    run.step({})
                    if restored_vitals.total < 200:
                        break
                self.assertEqual(200 - restored_vitals.total, damage[definition.name])
                if definition.name == 'Assassin':
                    self.assertEqual(fight.pins.get(bot.entity), restored.entity)
                npc_control.stop(run.match)

    def test_actual_heavy_damage_fills_echo_meter_and_casts_copy_without_gm_fill(self):
        definition = next(d for d in self.catalog.values() if d.name == 'Heavy Assault')
        run, player, _, _, angles = self.session(definition)
        enemy = run.match.add_player(2, 'Copy target', SOLDIER, 1, False)
        in_world(run.match, enemy)
        run.match.switch_hero(enemy, heroes()[SOLDIER])
        run.players.append(enemy)
        x, y, z = player.mover.position
        enemy.mover = Mover((x + 6, y, z + 5), world=run.match.collision_world,
                             data=mover_data(enemy.hero.body))
        script = player.body_script
        script.ult_seconds = 0  # isolate actual health-event gain from the legacy passive clock
        script.set_ult_charge(0)
        cost = script.component.entity_var(338).value()
        weapon = next(i for i in script.component.instances.values() if i.graph.index == 0x19E5)
        for _ in range(2600):
            button = 1 if weapon.vars[53].value() > 0 else 0x400
            run.step({player: (*angles, button)})
            if script.component.entity_var(342).value() >= cost:
                break
        self.assertEqual(script.component.entity_var(342).value(), cost)
        # Drain old projectiles so this does not claim cross-body projectile charge ownership.
        for _ in range(15):
            run.step({})
        angle = aim(run.match.combat._eye(player), (x + 6, y + 1, z + 5))
        run.step({player: (*angle, 32)})
        self.assertEqual(player.hero.guid, SOLDIER)
        self.assertTrue(run.match.combat.echo_duplicates.active(player))
        self.assertEqual(player.body_script.component.entity_var(342).value(), 0)

    def test_copied_soldier_earns_and_activates_visor_from_heavy_assault_damage(self):
        definition = next(item for item in self.catalog.values() if item.name == 'Heavy Assault')
        run, player, _, target, _ = self.session(definition)
        fight = run.match.combat
        enemy = run.match.add_player(2, 'Soldier copy target', SOLDIER, 1, False)
        in_world(run.match, enemy)
        enemy.client.bot_stream = lambda _entity: None
        run.match.switch_hero(enemy, heroes()[SOLDIER])
        run.players.append(enemy)
        x, y, z = player.mover.position
        enemy.mover = Mover((x+8, y, z+5), world=run.match.collision_world,
                            data=mover_data(enemy.hero.body))
        player.body_script.set_ult_charge(player.body_script.component.entity_var(338).value())
        run.step({player: (*aim(fight._eye(player), (x+8, y+1, z+5)), 32)})
        self.assertTrue(fight.echo_duplicates.active(player))
        self.assertEqual(player.hero.guid, SOLDIER)
        script = player.body_script
        script.ult_seconds = 0
        meter = script.component.entity_var(342)
        cost = script.component.entity_var(338).value()
        self.assertEqual(meter.value(), 0)
        self.assertGreater(cost, 0)
        x, y, z = target.feet
        attack = aim(fight._eye(player), (x, y + 1, z))
        vitals = fight.vitals_of(target)
        before = vitals.total
        for _ in range(180):
            run.step({player: (*attack, 1)})
            if meter.value() >= cost:
                break
        self.assertEqual(meter.value(), cost)
        self.assertGreaterEqual((before - vitals.total) * 6.5, cost)
        self.assertLess((before - vitals.total - 20) * 6.5, cost)
        self.assertFalse(vitals.dead)
        run.step({player: (*attack, 32)})
        self.assertEqual(meter.value(), 0)
        visor = next(i for i in script.component.instances.values() if i.graph.index == 0x259)
        for _ in range(85):
            run.step({})
        self.assertTrue(visor.states[7].active)
        before = vitals.total
        for _ in range(26):
            run.step({player: (attack[0] + 3641, attack[1], 1)})
        self.assertLess(vitals.total, before)
        self.assertEqual(meter.value(), 0)

    def test_copied_visor_off_axis_fire_hits_all_five_actual_npc_classes(self):
        # Charge injection isolates copied ultimate execution from open acceleration.
        for definition in self.catalog.values():
            with self.subTest(npc=definition.name):
                run, player, _, target, angles = self.session(definition)
                fight = run.match.combat
                enemy = run.match.add_player(2, 'Copy target', SOLDIER, 1, False)
                in_world(run.match, enemy)
                run.match.switch_hero(enemy, heroes()[SOLDIER])
                run.players.append(enemy)
                x, y, z = player.mover.position
                enemy.mover = Mover((x + 8, y, z + 5), world=run.match.collision_world,
                                    data=mover_data(enemy.hero.body))
                player.body_script.set_ult_charge(player.body_script.component.entity_var(338).value())
                copy_angles = aim(fight._eye(player), (x + 8, y + 1, z + 5))
                run.step({player: (*copy_angles, 32)})
                self.assertTrue(fight.echo_duplicates.active(player))
                self.assertEqual(player.hero.guid, SOLDIER)
                run.step({})
                player.body_script.ult_seconds = 0
                player.body_script.set_ult_charge(player.body_script.component.entity_var(338).value())
                run.step({player: (*angles, 32)})
                for _ in range(85):
                    run.step({})
                initial = fight.vitals_of(target).total
                # Aim20 degrees away to the opposite side from the copy target.
                off_axis = (angles[0] - 3641, angles[1])
                for _ in range(26):
                    run.step({player: (*off_axis, 1)})
                self.assertEqual(initial - fight.vitals_of(target).total, 80)
                self.assertEqual(player.body_script.component.entity_var(342).value(), 0)

    def test_real_three_firing_npcs_cannot_damage_copied_wraith_until_phase_ends(self):
        for definition in self.catalog.values():
            damage = {'Trooper': 20, 'Sniper': 100, 'Heavy Assault': 5}.get(definition.name)
            if damage is None:
                continue  # Enforcer/Assassin incoming attacks are still unaccepted, not fake green cases.
            with self.subTest(npc=definition.name):
                run, player, bot, _, angles = self.session(definition)
                fight = run.match.combat
                reaper = heroes()[0x02E0000000000002]
                enemy = run.match.add_player(2, 'Reaper copy target', reaper.guid, 1, False)
                in_world(run.match, enemy)
                enemy.client.bot_stream = lambda _entity: None  # this observer also omits NPC transport
                run.match.switch_hero(enemy, reaper)
                run.players.append(enemy)
                x, y, z = player.mover.position
                enemy.mover = Mover((x+8, y, z+5), world=run.match.collision_world,
                                    data=mover_data(reaper.body))
                player.body_script.set_ult_charge(player.body_script.component.entity_var(338).value())
                copy_angles = aim(fight._eye(player), (x+8, y+1, z+5))
                run.step({player: (*copy_angles, 32)})
                self.assertTrue(fight.echo_duplicates.active(player))
                self.assertEqual(player.hero.guid, reaper.guid)
                run.step({})
                own = next(t for t in fight._targets() if t.owner is player)
                if definition.name == 'Sniper':
                    # Let the real charge-up begin before phasing; a fresh Sniper
                    # does not emit within the first100 input frames.
                    player.mover.yaw, player.mover.pitch = angles
                    npc_control.start(run.match, player)
                    for _ in range(40):
                        run.step({})
                    self.assertEqual(fight.vitals_of(own).total, 250)
                run.step({player: (*angles, 8)})
                self.assertTrue(immunity.wraith_active(player))
                player.mover.yaw, player.mover.pitch = angles
                self.assertEqual(npc_control.start(run.match, player), bot.entity)
                phase = next(i for i in player.body_script.component.instances.values()
                             if i.graph.index == 0x20).states[3]
                phase_end = phase.start + phase.evaluate(phase.node.field('m_timeout'), watch=False)*1000
                with patch.object(fight, '_shoot', wraps=fight._shoot) as emitted:
                    while player.body_script.component.now + player.body_script.frame_ms < phase_end:
                        run.step({})
                        self.assertTrue(immunity.wraith_active(player))
                        self.assertEqual(fight.vitals_of(own).total, 250)
                        self.assertIsNotNone(bot.body_script)
                    self.assertTrue(any(call.args[0] is bot for call in emitted.call_args_list))
                for _ in range(300):
                    run.step({})
                    if fight.vitals_of(own).total < 250:
                        break
                self.assertFalse(immunity.wraith_active(player))
                self.assertEqual(fight.vitals_of(own).total, 250-damage)
                self.assertIsNotNone(bot.body_script)
                self.assertTrue(fight.echo_duplicates.active(player))
                npc_control.stop(run.match)

    def test_copied_zarya_projected_barrier_preserves_all_five_npc_team_semantics(self):
        for definition in self.catalog.values():
            for friendly in (False, True):
                with self.subTest(npc=definition.name, friendly=friendly):
                    run, player, bot, target, _ = self.session(definition)
                    fight = run.match.combat
                    zarya = heroes()[0x02E0000000000068]
                    enemy = run.match.add_player(2, 'Copy and attack source', zarya.guid, 1, False)
                    in_world(run.match, enemy)
                    enemy.client.bot_stream = lambda _entity: None
                    run.match.switch_hero(enemy, zarya)
                    run.players.append(enemy)
                    x, y, z = player.mover.position
                    enemy.mover = Mover((x+8, y, z+5), world=run.match.collision_world,
                                        data=mover_data(enemy.hero.body))
                    player.body_script.set_ult_charge(player.body_script.component.entity_var(338).value())
                    copy_aim = aim(fight._eye(player), fight._eye(enemy))
                    run.step({player: (*copy_aim, 32)})
                    self.assertEqual(player.hero.guid, zarya.guid)
                    player.body_script.ult_seconds = 0
                    run.match.switch_hero(enemy, heroes()[ECHO], preserve_pose=True)
                    enemy.body_script.ult_seconds = 0
                    enemy.body_script.set_ult_charge(0)
                    bot.team = player.team if friendly else 1
                    for _ in range(25):
                        run.step({})
                    initial = fight.vitals_of(target).total
                    a, b = target.segment()
                    centre = tuple((p+q)*.5 for p, q in zip(a, b, strict=True))
                    cast = aim(fight._eye(player), centre)
                    run.step({player: (*cast, 16)})
                    instance = next(i for i in player.body_script.component.instances.values()
                                    if i.graph.index == 0x25C)
                    self.assertGreater(instance.states[7].counter, 0)
                    bubble = fight.barriers.for_target(bot)
                    if friendly:
                        self.assertIsNotNone(bubble)
                        self.assertEqual(instance.vars[536].value().id, bot.entity)
                    else:
                        self.assertIsNone(bubble)
                        self.assertEqual(instance.vars[61].value(), 0)
                    # The actual Echo attacker is always opposite the NPC's prepared team.
                    enemy.team = 1-bot.team
                    attack = aim(fight._eye(enemy), centre)
                    run.step({enemy: (*attack, 1)})
                    for _ in range(25):
                        run.step({enemy: (*attack, 0)})
                    self.assertEqual(fight.vitals_of(target).total, initial if friendly else initial-51)
                    if friendly:
                        self.assertEqual(bubble.health, 149)
                        self.assertEqual(enemy.body_script.component.entity_var(342).value(), 0)
                        self.assertGreater(player.body_script.component.entity_var(87).value(), 9)
                        for _ in range(150):
                            run.step({})
                        self.assertIsNone(fight.barriers.for_target(bot))
                        run.step({enemy: (*attack, 1)})
                        for _ in range(25):
                            run.step({enemy: (*attack, 0)})
                        self.assertEqual(fight.vitals_of(target).total, initial-51)

    def test_copied_zarya_barrier_takes_real_three_npc_damage_then_owner_recovers_damage(self):
        for definition in self.catalog.values():
            if definition.name not in ('Trooper', 'Sniper', 'Heavy Assault'):
                continue
            with self.subTest(npc=definition.name):
                run, player, bot, _, _ = self.session(definition)
                fight = run.match.combat
                zarya = heroes()[0x02E0000000000068]
                enemy = run.match.add_player(2, 'Zarya copy target', zarya.guid, 1, False)
                in_world(run.match, enemy)
                enemy.client.bot_stream = lambda _entity: None
                run.match.switch_hero(enemy, zarya)
                run.players.append(enemy)
                x, y, z = player.mover.position
                enemy.mover = Mover((x+8, y, z+5), world=run.match.collision_world,
                                    data=mover_data(zarya.body))
                player.body_script.set_ult_charge(player.body_script.component.entity_var(338).value())
                angle = aim(fight._eye(player), (x+8, y+1, z+5))
                run.step({player: (*angle, 32)})
                self.assertEqual(player.hero.guid, zarya.guid)
                player.body_script.ult_seconds = 0
                run.step({})
                own = next(t for t in fight._targets() if t.owner is player)
                if definition.name == 'Sniper':
                    npc_control.start(run.match, player)
                    for _ in range(100):
                        run.step({})
                    self.assertEqual(fight.vitals_of(own).total, 400)
                run.step({player: (0, 0, 8)})
                bubble = fight.barriers.sync(player)
                self.assertIsNotNone(bubble)
                npc_control.start(run.match, player)
                with patch.object(fight, '_shoot', wraps=fight._shoot) as emitted:
                    for _ in range(115):
                        run.step({})
                        self.assertEqual(fight.vitals_of(own).total, 400)
                        if bubble.health < bubble.maximum:
                            break
                    self.assertTrue(any(call.args[0] is bot for call in emitted.call_args_list))
                damage = {'Trooper': 20, 'Sniper': 100, 'Heavy Assault': 5}[definition.name]
                self.assertEqual(bubble.health, 200-damage)
                self.assertEqual(player.body_script.component.entity_var(342).value(), 0)
                npc_control.stop(run.match)
                for _ in range(150):
                    run.step({})
                self.assertIsNone(fight.barriers.sync(player))
                self.assertEqual(fight.vitals_of(own).total, 400)
                npc_control.start(run.match, player)
                for _ in range(350):
                    run.step({})
                    if fight.vitals_of(own).total < 400:
                        break
                self.assertEqual(fight.vitals_of(own).total, 400-damage)
                self.assertIsNotNone(bot.body_script)
                self.assertTrue(fight.echo_duplicates.active(player))
                npc_control.stop(run.match)

    def test_copied_sigma_absorbs_real_three_npc_volleys_and_converts_damage(self):
        for definition in self.catalog.values():
            if definition.name not in ('Trooper', 'Sniper', 'Heavy Assault'):
                continue  # The other two incoming paths are open.
            with self.subTest(npc=definition.name):
                run, player, bot, target, _ = self.session(definition)
                fight = run.match.combat
                sigma = heroes()[0x02E000000000023B]
                enemy = run.match.add_player(2, 'Sigma copy target', sigma.guid, 1, False)
                in_world(run.match, enemy)
                enemy.client.bot_stream = lambda _entity: None
                run.match.switch_hero(enemy, sigma)
                run.players.append(enemy)
                x, y, z = player.mover.position
                enemy.mover = Mover((x+8, y, z+5), world=run.match.collision_world,
                                    data=mover_data(sigma.body))
                player.body_script.set_ult_charge(player.body_script.component.entity_var(338).value())
                angle = aim(fight._eye(player), (x+8, y+1, z+5))
                run.step({player: (*angle, 32)})
                self.assertEqual(player.hero.guid, sigma.guid)
                run.step({})
                facing = aim(fight._eye(player), (x, y+combat.EYE, z+5))
                own = next(t for t in fight._targets() if t.owner is player)
                if definition.name == 'Sniper':
                    player.mover.yaw, player.mover.pitch = facing
                    npc_control.start(run.match, player)
                    for _ in range(100):
                        run.step({player: (*facing, 0)})
                    self.assertEqual(fight.vitals_of(own).total, 400)
                player.body_script.ult_seconds = 0
                player.body_script.set_ult_charge(0)
                run.step({player: (*facing, 8)})
                player.mover.yaw, player.mover.pitch = facing
                npc_control.start(run.match, player)
                instance = next(i for i in player.body_script.component.instances.values()
                                if i.graph.index == 0x1D02)
                self.assertTrue(instance.states[1].active)
                countdown = instance.states[20]
                end = countdown.start + instance.vars[567].value()*1000
                initial = fight.vitals_of(target).total
                with patch.object(fight, '_shoot', wraps=fight._shoot) as emitted:
                    while player.body_script.component.now + 2*player.body_script.frame_ms < end:
                        run.step({player: (*facing, 0)})
                        self.assertTrue(instance.states[1].active)
                        self.assertEqual(fight.vitals_of(own).total, 400)
                    self.assertTrue(any(call.args[0] is bot for call in emitted.call_args_list))
                captured = instance.vars[21312].value()
                self.assertGreater(captured, 0)
                npc_control.stop(run.match)
                for _ in range(10):
                    run.step({player: (*facing, 0)})
                self.assertFalse(instance.states[1].active)
                self.assertAlmostEqual(fight.vitals_of(own).temporary_shields,
                                       min(captured*.6, 400), places=3)
                self.assertEqual(fight.vitals_of(target).total, initial)
                self.assertEqual(player.body_script.component.entity_var(342).value(), 0)
