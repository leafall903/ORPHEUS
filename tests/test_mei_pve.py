"""Actual five-NPC identities/pools against Mei; AI acceptance is separate."""

import gzip
import json
import os
import sys
import unittest
from dataclasses import replace
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parent))

import test_mei_blizzard as blizzard_fixtures
import test_mei_combat as fixtures
import test_mei_frost as frost_fixtures
import test_mei_shields as shield_fixtures
import test_mei_wall as wall_fixtures
from test_game_combat import FakeClient

from ow174.game import (
    combat,
    heavy_charge,
    mei,
    npc,
    npc_context,
    npc_melee,
    sniper_reposition,
    zarya_shields,
)
from ow174.game.body_vm_snapshot import export_component_vm
from ow174.game.collision import World
from ow174.game.content import heroes
from ow174.game.script import entity_queries, expr, graph, play, runtime, scene
from ow174.game.server_snapshot import snapshot_digest


class NpcClient(FakeClient):
    def __init__(self, player):
        super().__init__(player)
        self.death_events = []

    def queue_bot_death(self, entity, event):
        self.death_events.append((entity, event))

    def queue_bot_death_update(self, entity, event):
        self.death_events.append((entity, event))

    def bot_stream(self, entity):
        return None  # This combat fixture omits NPC observer projectile transport.


@unittest.skipUnless(os.environ.get("ORPHEUS_NPC_DATA_DIR"), "explicit external NPC data required")
class MeiPveTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.data_dir = Path(os.environ["ORPHEUS_NPC_DATA_DIR"])
        cls.definitions = npc.load_catalog(cls.data_dir / "pve_npcs_174.json")
        cls.graph_data = json.loads(gzip.decompress((cls.data_dir / "game_graphs_174.json.gz").read_bytes()))

    def setUp(self):
        self.graph_patch = patch.object(graph, "_data", return_value=self.graph_data)
        self.graph_patch.start()
        graph.graph.cache_clear()
        graph._records.cache_clear()
        self.addCleanup(self.reset_graphs)

    def reset_graphs(self):
        self.graph_patch.stop()
        graph.graph.cache_clear()
        graph._records.cache_clear()

    def scene(self, definition, distance=5.0):
        run, owner, other, _target = fixtures.MeiCombatTests().duel()
        x, y, z = owner.mover.position
        other.spawn = ((x + 20.0, y, z + 5.0), other.spawn[1])
        self.assertTrue(run.match.switch_hero(other, other.hero))
        for player in run.players:
            player.client = NpcClient(player)
        bot = npc.spawn(run.match, definition, (x, y, z + distance), team=1, yaw=-32768)
        self.assertIsNotNone(bot.body_script)
        self.assertEqual(bot.body, definition.body)
        self.assertEqual(bot.hero, definition.hero)
        target = next(t for t in run.match.combat._targets() if t.owner is bot)
        return run, owner, bot, target

    def test_icicle_body_damage_uses_each_actual_npcs_base_pools(self):
        for definition in self.definitions.values():
            with self.subTest(npc=definition.name):
                run, owner, _bot, target = self.scene(definition)
                expected = combat.Vitals(0, definition.health, definition.armour, definition.shields)
                dealt = expected.damage(75.0)
                fixtures.MeiCombatTests().fire(run, owner, target, 2, 0.9)
                actual = run.match.combat.vitals_of(target)
                self.assertEqual(actual.now, expected.now)
                self.assertEqual(owner.client.messages(combat.HIT)[0]["+0x78"]["+0x4"], round(dealt))

    def test_difficulty_pools_preserve_five_npc_bidirectional_combat_and_death(self):
        from test_game_combat import KANEZAKA_DM

        # 104319 143A branches supply the amounts. The spawn policy adds them
        # to the simultaneously active base pools in the static server model.
        additions = {
            "Trooper": ((0, 0),) * 4,
            "Sniper": ((0, 0), (150, 0), (200, 100), (100, 400)),
            "Heavy Assault": ((2000, 0), (3000, 0), (5000, 0), (7000, 0)),
            "Enforcer": ((0, 0), (150, 50), (250, 100), (250, 200)),
            "Assassin": ((0, 0), (350, 0), (600, 0), (400, 300)),
        }
        mode = f"0x{KANEZAKA_DM.mode_guid:016X}"
        mode_data = self.graph_data.get("mode_variables", {})
        self.addCleanup(graph.mode_variables.cache_clear)
        weapon = fixtures.MeiCombatTests()

        for definition in self.definitions.values():
            for level, (health, shields) in enumerate(additions[definition.name]):
                with self.subTest(npc=definition.name, difficulty=level):
                    modes = {**mode_data, mode: {**mode_data.get(mode, {}), "9948": level}}
                    with patch.dict(self.graph_data, {"mode_variables": modes}):
                        graph.mode_variables.cache_clear()
                        run, mei, bot, target = self.scene(definition, 2.)
                        self.check_difficulty_exchange(
                            run, mei, bot, target, definition, health, shields, weapon,
                        )

    def check_difficulty_exchange(self, run, mei, bot, target, definition, health, shields, weapon):
        fight = run.match.combat
        vitals = fight.vitals_of(target)
        expected = combat.Vitals(bot.entity, definition.health + health, definition.armour,
                                 definition.shields + shields)
        self.assertEqual(vitals.most, expected.most)
        expected.damage(75.)
        weapon.fire(run, mei, target, 2, .9)
        self.assertEqual(vitals.now, expected.now)

        npc.engage(run.match, bot, mei)
        mei_body = next(body for body in fight._targets() if body.owner is mei)
        mei_vitals = fight.vitals_of(mei_body)
        for _ in range(220):
            run.step({})
            if mei_vitals.total < 250.:
                break
        self.assertGreater(mei_vitals.total, 0.)
        self.assertLess(mei_vitals.total, 250.)
        npc.engage(run.match, bot, None)
        for _ in range(65):
            run.step({})

        # Admit an actual killing icicle after a pool drain fixture.
        vitals.damage(vitals.total - 1.)
        notices = len(mei.client.messages(combat.KILL_NOTICE))
        weapon.fire(run, mei, target, 2, .9)
        self.assertTrue(vitals.dead and bot.dead)
        self.assertEqual(vitals.killer, mei.body)
        self.assertEqual(len(mei.client.messages(combat.KILL_NOTICE)), notices + 1)
        self.assertEqual(npc.remove_spawned(run.match), (bot.entity,))
        self.assertNotIn(bot.entity, fight.vitals)

    def test_actual_npc_damage_credits_mei_only_effective_health_loss(self):
        for definition in self.definitions.values():
            with self.subTest(npc=definition.name):
                run, owner, bot, target = self.scene(definition)
                owner.body_script.ult_seconds = 0
                owner.body_script.set_ult_charge(0.)
                before = run.match.combat.vitals_of(target).total
                fixtures.MeiCombatTests().fire(run, owner, target, 2, .9)
                effective = before - run.match.combat.vitals_of(target).total
                self.assertGreater(effective, 0.)
                charge = owner.body_script.component.entity_var(342)
                self.assertEqual(charge.value(), effective)

                vitals = run.match.combat.vitals_of(target)
                vitals.now.update({combat.HEALTH_POOL: 10., combat.ARMOUR_POOL: 0.,
                                   combat.SHIELDS_POOL: 0.})
                fixtures.MeiCombatTests().fire(run, owner, target, 2, .9)
                self.assertTrue(bot.dead)
                self.assertIn(bot, run.match.bots)
                self.assertEqual(charge.value(), effective + 10.)
                hits = owner.client.messages(combat.HIT)
                self.assertEqual(len(hits), 2)
                self.assertEqual(hits[-1]["+0x78"]["+0x4"], 10)
                self.assertEqual(len(owner.client.messages(combat.KILL_NOTICE)), 1)
                fixtures.MeiCombatTests().fire(run, owner, target, 2, .9)
                weapon = next(i for i in owner.body_script.component.instances.values()
                              if i.graph.index == 0x5E6)
                self.assertEqual(weapon.vars[53].value(), 90.)
                self.assertEqual(charge.value(), effective + 10.)
                self.assertEqual(owner.client.messages(combat.HIT), hits)
                self.assertEqual(len(owner.client.messages(combat.KILL_NOTICE)), 1)

    def test_explicit_npc_removal_clears_per_source_frost_storage(self):
        for definition in self.definitions.values():
            with self.subTest(npc=definition.name):
                run, owner, bot, target = self.scene(definition)
                run.match.combat._damage(owner, target, 2.75, False, mei.SPRAY_LOADOUT, run.frame)
                self.assertIn(bot.entity, run.match.combat.frost_sources)
                self.assertEqual(npc.remove_spawned(run.match), (bot.entity,))
                self.assertNotIn(bot.entity, run.match.combat.frost_sources)
                self.assertNotIn(bot.entity, run.match.combat.frost_hits)

    def test_spray_and_melee_damage_each_actual_npc(self):
        for definition in self.definitions.values():
            for button, damage in ((1, None), (0x800, 30.0)):
                with self.subTest(npc=definition.name, button=button):
                    run, owner, _bot, target = self.scene(definition, 1.5)
                    expected = combat.Vitals(0, definition.health, definition.armour, definition.shields)
                    fixtures.MeiCombatTests().fire(run, owner, target, button, 0.9)
                    hits = owner.client.messages(combat.HIT)
                    self.assertGreater(len(hits), 0)
                    for _hit in hits:
                        expected.damage(2.75 if damage is None else damage)
                    self.assertEqual(run.match.combat.vitals_of(target).now, expected.now)

    def test_each_actual_npc_death_retains_corpse_beyond_deadline(self):
        for definition in self.definitions.values():
            with self.subTest(npc=definition.name):
                run, owner, bot, target = self.scene(definition)
                vitals = run.match.combat.vitals_of(target)
                vitals.now.update({combat.HEALTH_POOL: 60.0, combat.ARMOUR_POOL: 0.0,
                                   combat.SHIELDS_POOL: 0.0})
                fixtures.MeiCombatTests().fire(run, owner, target, 2, 0.9)
                self.assertTrue(bot.dead)
                self.assertTrue(vitals.dead)
                self.assertTrue(owner.client.death_events)
                for _ in range(210):
                    run.step({})
                self.assertIn(bot, run.match.bots)
                self.assertNotIn(bot, [who for _when, who in run.match.combat.respawns])
                self.assertEqual(npc.remove_spawned(run.match), (bot.entity,))

    def test_each_actual_npc_source_damage_is_blocked_during_cryo(self):
        for definition in self.definitions.values():
            with self.subTest(npc=definition.name):
                run, owner, bot, _npc_target = self.scene(definition)
                fight = run.match.combat
                target = next(t for t in fight._targets() if t.owner is owner)
                vitals = fight.vitals_of(target)
                # Explicit fixture attack; this is not an authored AI decision.
                fight._damage(bot, target, 25.0, False, None, run.frame)
                self.assertEqual(vitals.total, 225.0)
                run.step({owner: (0, 0, 8)})
                before = vitals.total
                fight._damage(bot, target, 1000.0, False, None, run.frame)
                self.assertEqual(vitals.total, before)
                self.assertFalse(vitals.dead)

    def test_spray_penetrates_two_real_npcs_once_each_but_icicle_stops(self):
        definition = self.definitions["1"]
        for button, front_damage, rear_damage in ((1, 2.75, 2.75), (2, 75.0, 0.0)):
            with self.subTest(button=button):
                run, owner, _front, target = self.scene(definition)
                x, y, z = owner.mover.position
                rear = npc.spawn(run.match, definition, (x, y, z + 8.0), team=1)
                rear_target = next(t for t in run.match.combat._targets() if t.owner is rear)
                yaw, pitch = fixtures.aim(run.match.combat._eye(owner),
                                          (target.feet[0], target.feet[1] + 0.9, target.feet[2]))
                run.step({owner: (yaw, pitch, button)})
                for _ in range(45):
                    run.step({owner: (yaw, pitch, 0)})
                self.assertEqual(run.match.combat.vitals_of(target).total,
                                 definition.health - front_damage)
                self.assertEqual(run.match.combat.vitals_of(rear_target).total,
                                 definition.health - rear_damage)

    def test_spray_and_icicle_pass_each_allied_npc_to_hit_a_rear_enemy(self):
        for definition in self.definitions.values():
            for button, damage in ((1, 2.75), (2, 75.)):
                with self.subTest(npc=definition.name, button=button):
                    run, owner, ally, front = self.scene(definition, 4.)
                    fight = run.match.combat
                    run.match.game_map = replace(run.match.game_map, free_for_all=False)
                    ally.team = owner.team
                    enemy_team = next(team for team in (0, 1) if team != owner.team)
                    x, y, z = owner.mover.position
                    enemy = npc.spawn(run.match, self.definitions["1"], (x, y, z + 7.), team=enemy_team)
                    rear = next(t for t in fight._targets() if t.owner is enemy)
                    front_vitals, rear_vitals = fight.vitals_of(front), fight.vitals_of(rear)
                    front_health, rear_health = front_vitals.total, rear_vitals.total
                    yaw, pitch = fixtures.aim(fight._eye(owner), (x, y + .9, z + 7.))
                    run.step({owner: (yaw, pitch, button)})
                    for _ in range(45):
                        run.step({owner: (yaw, pitch, 0)})
                    self.assertEqual(front_vitals.total, front_health)
                    self.assertIsNone(fight.frost(ally))
                    self.assertEqual(rear_vitals.total, rear_health - damage)
                    self.assertEqual(len(owner.client.messages(combat.HIT)), 1)
                    self.assertEqual(fight.frost(enemy) is not None, button == 1)

    def test_actual_spray_freezes_each_npc_and_pauses_route_without_catchup(self):
        for definition in self.definitions.values():
            with self.subTest(npc=definition.name):
                run, owner, bot, target = self.scene(definition)
                frost_fixtures.FrostTests().freeze(run, owner, target)
                before = tuple(bot.position)
                bot.route = [before, (before[0], before[1], before[2] + 30.0)]
                bot.index, bot.wait = 0, 0.0
                for _ in range(20):
                    run.step({})
                self.assertEqual(bot.position, before)
                self.assertEqual(bot.held, (0, 0))
                self.assertEqual(bot.last_tick, run.frame)
                for _ in range(100):
                    run.step({})
                self.assertFalse(run.match.combat.frozen(bot))
                self.assertGreater(bot.position[2], before[2] + 0.1)
                self.assertLess(bot.position[2] - before[2], bot.speed * 100 * combat.TICK)
                self.assertEqual(npc.remove_spawned(run.match), (bot.entity,))
                self.assertNotIn(bot.entity, run.match.combat.frost_hits)

    def test_wall_blocks_each_actual_npc_route_and_npc_damage_opens_a_column(self):
        for definition in self.definitions.values():
            with self.subTest(npc=definition.name):
                run, owner, bot, _target = self.scene(definition)
                pieces, _aim = wall_fixtures.WallTests().place(run, owner)
                centre = pieces[2]
                start = tuple(bot.position)
                bot.route = [start, (start[0], start[1], start[2] - 15.0)]
                bot.index, bot.wait = 0, 0.0
                for _ in range(100):
                    run.step({})
                self.assertGreater(bot.position[2], centre.feet[2] + centre.depth)
                self.assertLess(bot.position[2], start[2])
                # Explicit NPC damage fixture; authored attack selection is still open.
                self.assertEqual(run.match.combat.walls.damage(centre, bot, 400.0, run.frame), 400.0)
                self.assertNotIn(centre.entity, run.match.combat.walls.pieces)
                for _ in range(100):
                    run.step({})
                self.assertLess(bot.position[2], centre.feet[2] - centre.depth)

    def test_blizzard_damages_and_freezes_each_actual_npc(self):
        for definition in self.definitions.values():
            with self.subTest(npc=definition.name):
                run, owner, bot, target = self.scene(definition)
                fight = run.match.combat
                run.match.game_map = replace(run.match.game_map, free_for_all=False)
                owner.team, bot.team = 0, 1
                x, y, z = bot.position
                ally = npc.spawn(run.match, definition, (x + 1.5, y, z), team=owner.team)
                ally_target = next(t for t in fight._targets() if t.owner is ally)
                ally_vitals = fight.vitals_of(ally_target)
                ally_pools = dict(ally_vitals.now)
                fixture = blizzard_fixtures.BlizzardTests()
                fixture.launch(run, owner)
                area = fixture.landed(run)
                while run.frame * 16 < area.starts + 2800:
                    run.step({})
                self.assertTrue(fight.frozen(bot))
                self.assertEqual(ally_vitals.now, ally_pools)
                self.assertIsNone(fight.frost(ally))
                fixture.finish(run, area)
                expected = definition.health + definition.armour + definition.shields - 85.0
                self.assertAlmostEqual(run.match.combat.vitals_of(target).total, expected)
                self.assertFalse(bot.dead)
                self.assertFalse(ally.dead)
                self.assertEqual(ally_vitals.now, ally_pools)
                self.assertIsNone(fight.frost(ally))
                self.assertTrue(owner.client.messages(combat.HIT))

    def test_landed_blizzard_checks_static_los_and_radius_for_each_npc(self):
        for definition in self.definitions.values():
            with self.subTest(npc=definition.name):
                run, owner, blocked, _target = self.scene(definition)
                fight = run.match.combat
                fixture = blizzard_fixtures.BlizzardTests()
                fixture.launch(run, owner)
                area = fixture.landed(run)
                x, y, z = area.position
                blocked.position = (x, y, z + 4.)
                exposed = npc.spawn(run.match, definition, (x, y, z - 3.), team=blocked.team)
                outside = npc.spawn(run.match, definition, (x, y, z - 12.), team=blocked.team)
                targets = {t.entity: t for t in fight._targets()}
                bodies = (blocked, exposed, outside)
                vitals = [fight.vitals_of(targets[bot.entity]) for bot in bodies]
                initial = [v.total for v in vitals]
                fixtures.static_wall(run, owner, distance=z - owner.mover.position[2] + 2.)
                while run.frame * 16 < area.starts + 2800:
                    run.step({})
                self.assertEqual([fight.frozen(bot) for bot in bodies], [False, True, False])
                self.assertIsNone(fight.frost(blocked))
                self.assertIsNone(fight.frost(outside))
                fixture.finish(run, area)
                for actual, before, damage in zip(vitals, initial, (0., 85., 0.), strict=True):
                    self.assertAlmostEqual(actual.total, before - damage)

    def test_blizzard_lethal_damage_retains_each_actual_npc_corpse(self):
        for definition in self.definitions.values():
            with self.subTest(npc=definition.name):
                run, owner, bot, target = self.scene(definition)
                fight = run.match.combat
                vitals = fight.vitals_of(target)
                vitals.now.update({combat.HEALTH_POOL: 10.0, combat.ARMOUR_POOL: 0.0,
                                   combat.SHIELDS_POOL: 0.0})
                fixture = blizzard_fixtures.BlizzardTests()
                fixture.launch(run, owner)
                area = fixture.landed(run)
                fixture.finish(run, area)
                self.assertTrue(bot.dead)
                self.assertEqual(vitals.total, 0.0)
                self.assertIn(bot, run.match.bots)
                self.assertNotIn(bot, [who for _when, who in fight.respawns])
                self.assertNotIn(bot.entity, fight.frost_hits)
                self.assertTrue(owner.client.death_events)
                self.assertEqual(len(owner.client.messages(combat.KILL_NOTICE)), 1)

    def test_static_wall_blocks_melee_against_each_actual_npc(self):
        for definition in self.definitions.values():
            with self.subTest(npc=definition.name):
                run, owner, _bot, target = self.scene(definition, 2.0)
                fixtures.static_wall(run, owner)
                before = run.match.combat.vitals_of(target).total
                fixtures.MeiCombatTests().fire(run, owner, target, 0x800, 0.9)
                self.assertEqual(run.match.combat.vitals_of(target).total, before)
                self.assertEqual(owner.client.messages(combat.HIT), [])

    def test_static_los_gates_spray_and_icicle_against_each_actual_npc(self):
        for definition in self.definitions.values():
            for button, damage in ((1, 2.75), (2, 75.)):
                with self.subTest(npc=definition.name, button=button):
                    run, owner, bot, target = self.scene(definition)
                    fight = run.match.combat
                    clear_world = run.match.collision_world
                    fixtures.static_wall(run, owner)
                    vitals = fight.vitals_of(target)
                    pools = dict(vitals.now)
                    weapon = next(i for i in owner.body_script.component.instances.values()
                                  if i.graph.index == 0x5E6)
                    ammo = weapon.vars[53].value()
                    fixtures.MeiCombatTests().fire(run, owner, target, button, .9)
                    self.assertLess(weapon.vars[53].value(), ammo)
                    self.assertEqual(vitals.now, pools)
                    self.assertEqual(owner.client.messages(combat.HIT), [])
                    self.assertIsNone(fight.frost(bot))

                    run.match.collision_world = clear_world
                    for player in run.players:
                        player.mover.use(world=clear_world)
                    yaw, pitch = fixtures.aim(fight._eye(owner),
                                              (target.feet[0], target.feet[1] + .9, target.feet[2]))
                    run.step({owner: (yaw, pitch, button)})
                    for _ in range(45):
                        run.step({owner: (yaw, pitch, 0)})
                    expected = combat.Vitals(0, definition.health, definition.armour, definition.shields)
                    expected.damage(damage)
                    self.assertEqual(vitals.now, expected.now)
                    self.assertEqual(len(owner.client.messages(combat.HIT)), 1)
                    self.assertEqual(fight.frost(bot) is not None, button == 1)

    def test_weapon_range_separates_spray_and_icicle_against_each_actual_npc(self):
        for definition in self.definitions.values():
            with self.subTest(npc=definition.name):
                run, owner, bot, target = self.scene(definition, 12.)
                fight = run.match.combat
                vitals = fight.vitals_of(target)
                pools = dict(vitals.now)
                weapon = next(i for i in owner.body_script.component.instances.values()
                              if i.graph.index == 0x5E6)
                shooter = fixtures.MeiCombatTests()
                shooter.fire(run, owner, target, 1, .9)
                ammo = weapon.vars[53].value()
                self.assertLess(ammo, 120.)
                self.assertEqual(vitals.now, pools)
                self.assertIsNone(fight.frost(bot))
                self.assertEqual(owner.client.messages(combat.HIT), [])

                shooter.fire(run, owner, target, 2, .9)
                expected = combat.Vitals(0, definition.health, definition.armour, definition.shields)
                expected.damage(75.)
                self.assertEqual(vitals.now, expected.now)
                self.assertEqual(weapon.vars[53].value(), ammo - 10.)
                hits = owner.client.messages(combat.HIT)
                self.assertEqual(len(hits), 1)

                x, y, z = owner.mover.position
                bot.position = (x, y, z + 101.)
                target = next(t for t in fight._targets() if t.owner is bot)
                shooter.fire(run, owner, target, 2, .9, frames=120)
                self.assertEqual(weapon.vars[53].value(), ammo - 20.)
                self.assertEqual(vitals.now, expected.now)
                self.assertEqual(owner.client.messages(combat.HIT), hits)
                self.assertFalse(any(shot.shooter is owner for shot in fight.rockets))

    def test_three_actual_npc_weapons_damage_mei_and_cryo_blocks_them(self):
        cases = {'Trooper': (120, 120.0), 'Sniper': (240, 100.0), 'Heavy Assault': (120, 110.0)}
        for definition in self.definitions.values():
            if definition.name not in cases:
                continue
            frames, damage = cases[definition.name]
            for cryo in (False, True):
                with self.subTest(npc=definition.name, cryo=cryo):
                    run, owner, bot, _target = self.scene(definition)
                    if cryo:
                        run.step({owner: (0, 0, 8)})
                    component = bot.body_script.component
                    bot.firing = True
                    # Manual extracted primary slot, not autonomous AI selection.
                    component.edges([(component.world.ai_weapon_button(0), True)])
                    for _ in range(frames):
                        run.step({})
                    target = next(t for t in run.match.combat._targets() if t.owner is owner)
                    self.assertEqual(run.match.combat.vitals_of(target).total,
                                     250.0 if cryo else 250.0 - damage)
                    volleys = [s for i in component.instances.values() for s in i.states.values()
                               if s.node.cls == 'STUStatescriptStateWeaponVolley' and s.counter]
                    self.assertTrue(volleys)
                    self.assertIsNotNone(bot.body_script)

    @unittest.skipUnless(os.environ.get("ORPHEUS_ASSASSIN_GRAPH_BUNDLE"),
                         "explicit 104319 Assassin controller graph bundle required")
    def test_same_mei_body_exchanges_damage_with_all_five_npcs_in_one_match(self):
        path = Path(os.environ["ORPHEUS_ASSASSIN_GRAPH_BUNDLE"])
        bundle = json.loads(gzip.decompress(path.read_bytes()))
        with patch.object(graph, "_data", return_value=bundle):
            graph.graph.cache_clear()
            graph._records.cache_clear()
            self.check_five_npc_exchange()

    def check_five_npc_exchange(self):
        run, owner, other, _target = fixtures.MeiCombatTests().duel()
        fight = run.match.combat
        run.match.game_map = replace(run.match.game_map, free_for_all=False)
        owner.team = other.team = 0
        x, y, z = owner.mover.position
        fight.walls.place_player(other, (x + 20., y, z))
        for player in run.players:
            player.client = NpcClient(player)
        cases = (("Trooper", 20.), ("Sniper", 100.), ("Heavy Assault", 5.),
                 ("Enforcer", 50.), ("Assassin", 25.))
        definitions = {definition.name: definition for definition in self.definitions.values()}
        units = [npc.spawn(run.match, definitions[name], (x + 18. + index * 2., y, z),
                           team=0, yaw=0) for index, (name, _) in enumerate(cases)]
        self.assertEqual(len({bot.entity for bot in units}), 5)
        self.assertTrue(all(bot.body_script is not None for bot in units))
        owner_body = owner.body
        owner_target = next(target for target in fight._targets() if target.owner is owner)
        owner_vitals = fight.vitals_of(owner_target)
        self.assertEqual(owner_vitals.total, 250.)
        shoot = fixtures.MeiCombatTests().fire
        damage = fight._damage
        landed = []

        def record_damage(shooter, target, *args, **kwargs):
            effective = damage(shooter, target, *args, **kwargs)
            if effective:
                landed.append((shooter, target.owner, effective))
            return effective

        with patch.object(fight, "_damage", side_effect=record_damage):
            for (name, incoming), bot in zip(cases, units, strict=True):
                with self.subTest(npc=name):
                    landed.clear()
                    before = owner_vitals.total
                    autonomous = name == "Assassin"
                    if autonomous:
                        # Other bodies remain present. Put them on the Assassin's
                        # team so Mei is its only eligible opponent.
                        other.team = 1
                        for unit in units:
                            unit.team = 1
                    bot.team = 1
                    bot.position = (x, y, z + (10. if autonomous else 2.))
                    target = next(target for target in fight._targets() if target.owner is bot)
                    vitals = fight.vitals_of(target)
                    initial_npc_health = vitals.total
                    if not autonomous:
                        shoot(run, owner, target, 2, .9)
                        npc.engage(run.match, bot, owner)

                    for _ in range(240):
                        if owner_vitals.total < before:
                            break
                        run.step({})
                    self.assertEqual(owner_vitals.total, before - incoming)
                    if autonomous:
                        self.assertIsNotNone(bot.body_script.controller)
                        self.assertFalse(bot.firing)
                        self.assertIsNone(bot.body_script.component.world.manual_target)
                        self.assertEqual(fight.pins.get(bot.entity), owner_body)
                        bot.position = (x, y, z + 8.)
                        for _ in range(3):
                            run.step({})
                        self.assertNotIn(bot.entity, fight.pins)
                        self.assertFalse(fight.is_stunned(owner))
                        target = next(target for target in fight._targets() if target.owner is bot)
                        shoot(run, owner, target, 2, .9)

                    self.assertEqual(initial_npc_health - vitals.total, 75.)
                    self.assertEqual(sum(amount for source, victim, amount in landed
                                         if source is owner and victim is bot), 75.)
                    self.assertEqual(sum(amount for source, victim, amount in landed
                                         if source is bot and victim is owner), incoming)
                    self.assertEqual(owner.body, owner_body)
                    self.assertEqual(owner_vitals.total, before - incoming)
                    self.assertTrue(all(unit in run.match.bots and not unit.dead for unit in units))
                    if not autonomous:
                        npc.engage(run.match, bot, None)
                    bot.team = 0
                    bot.position = (x + 25., y, z + 20.)
                    bot.yaw = 0.

        self.assertEqual(owner_vitals.total, 50.)

    def test_actual_npc_primary_fire_hits_wall_then_mei_after_cancel(self):
        cases = {'Trooper': 120, 'Sniper': 240, 'Heavy Assault': 120}
        for definition in self.definitions.values():
            if definition.name not in cases:
                continue
            with self.subTest(npc=definition.name):
                run, owner, bot, _target = self.scene(definition)
                fight = run.match.combat
                walls = wall_fixtures.WallTests()
                pieces, aim = walls.place(run, owner)
                self.assertEqual(len(pieces), 5)
                centre = pieces[2]
                target = next(t for t in fight._targets() if t.owner is owner)
                vitals = fight.vitals_of(target)
                npc.engage(run.match, bot, owner)
                for _ in range(cases[definition.name]):
                    run.step({})
                    if centre.health < 400.:
                        break
                self.assertLess(centre.health, 400.)
                self.assertIn(centre.entity, fight.walls.pieces)
                self.assertEqual(vitals.total, 250.)
                self.assertTrue(all(p.health == 400. for p in pieces if p is not centre))

                walls.tap(run, owner, 16, aim)
                self.assertFalse(fight.walls.pieces)
                for _ in range(cases[definition.name]):
                    run.step({})
                    if vitals.total < 250.:
                        break
                self.assertLess(vitals.total, 250.)

    def test_actual_heavy_charge_input_advances_without_primary_fire_and_cancels(self):
        definition = next(d for d in self.definitions.values() if d.name == 'Heavy Assault')
        run, owner, bot, _target = self.scene(definition, 8.)
        instance = heavy_charge.charge(bot)
        self.assertIsNotNone(instance)
        start = bot.position
        for _ in range(25):
            run.step({})
        npc.engage(run.match, bot, owner, action="charge")
        npc.engage(run.match, bot, None)
        run.step({})
        self.assertEqual(heavy_charge.phase(instance), 0)
        self.assertNotIn(heavy_charge.BUTTON, instance.component.held)
        npc.engage(run.match, bot, owner, action="charge")
        run.step({})
        self.assertFalse(bot.firing)
        self.assertEqual(heavy_charge.phase(instance), 1)
        self.assertTrue(instance.states[71].active)
        for _ in range(45):
            run.step({})
        self.assertEqual(bot.position, start)
        for _ in range(10):
            run.step({})
        drive = instance.states[instance.graph.node(71).state]
        self.assertTrue(drive.active)
        self.assertLess(bot.position[2], start[2])
        self.assertAlmostEqual(bot.velocity[2], -15.)

        npc.engage(run.match, bot, None)
        run.step({})
        self.assertEqual(heavy_charge.phase(instance), 0)
        self.assertNotIn(heavy_charge.BUTTON, instance.component.held)
        self.assertFalse(drive.active)
        self.assertEqual(bot.velocity, (0., 0., 0.))
        self.assertGreater(instance.vars[61].value(), 0.)
        npc.engage(run.match, bot, owner, action="charge")
        for _ in range(3):
            run.step({})
        self.assertEqual(heavy_charge.phase(instance), 0)

    def test_actual_heavy_charge_stops_at_static_and_ice_walls(self):
        definition = next(d for d in self.definitions.values() if d.name == 'Heavy Assault')
        for ice in (False, True):
            with self.subTest(ice=ice):
                run, owner, bot, _target = self.scene(definition, 8.)
                start = bot.position
                if ice:
                    pieces, _ = wall_fixtures.WallTests().place(run, owner)
                    centre = pieces[2]
                    wall_z = centre.feet[2] + centre.depth
                else:
                    fixtures.static_wall(run, owner, distance=3.)
                    wall_z = owner.mover.position[2] + 3.
                instance = heavy_charge.charge(bot)
                npc.engage(run.match, bot, owner, action="charge")
                for _ in range(110):
                    run.step({})
                self.assertGreater(bot.position[2], wall_z)
                self.assertLess(bot.position[2], start[2] - 0.5)
                self.assertEqual(bot.velocity, (0., 0., 0.))
                self.assertEqual(heavy_charge.phase(instance), 0)
                self.assertGreater(instance.vars[61].value(), 0.)

    def test_actual_heavy_charge_catches_up_drive_and_exits_on_open_ground(self):
        definition = next(d for d in self.definitions.values() if d.name == 'Heavy Assault')
        run, owner, bot, _target = self.scene(definition, 8.)
        fixtures.static_wall(run, owner, distance=100.)
        instance = heavy_charge.charge(bot)
        start = bot.position
        npc.engage(run.match, bot, owner, action="charge")
        run.step({})
        run.frame += 69
        run.step({})
        self.assertTrue(heavy_charge.driving(instance))
        self.assertLess(bot.position[2], start[2] - 3.)
        for _ in range(200):
            run.step({})
            if not heavy_charge.phase(instance):
                break
        end = bot.position
        self.assertLess(end[2], start[2] - 20.)
        self.assertEqual(heavy_charge.phase(instance), 0)
        self.assertGreater(instance.vars[61].value(), 0.)
        self.assertEqual(bot.velocity, (0., 0., 0.))
        self.assertFalse(heavy_charge.clocked(bot))
        npc.engage(run.match, bot, owner, action="charge")
        for _ in range(20):
            run.step({})
        self.assertEqual(bot.position, end)
        self.assertFalse(heavy_charge.clocked(bot))

        # A rejected press is retired. Only a new press after cooldown starts the drive.
        for _ in range(950):
            run.step({})
        self.assertEqual(heavy_charge.phase(instance), 0)
        self.assertEqual(bot.position, end)
        npc.engage(run.match, bot, owner, action="charge")
        run.step({})
        self.assertEqual(heavy_charge.phase(instance), 1)

    def test_actual_heavy_release_graph_selects_low_high_and_miss_positions(self):
        definition = next(d for d in self.definitions.values() if d.name == "Heavy Assault")
        cases = (("low", 103, .05), ("high", 110, .05), ("miss", 111, .35), ("ice", 103, .05))
        for barrier, expected_node, distance in cases:
            with self.subTest(barrier=barrier):
                run, owner, bot, _target = self.scene(definition)
                bot.yaw = 0.
                if barrier == "ice":
                    pieces, _ = wall_fixtures.WallTests().place(run, owner)
                    self.assertEqual(len(pieces), 5)
                    centre = pieces[2]
                    x, y, z = centre.feet
                    bot.position = (x, y, z - centre.depth - .3)
                instance = heavy_charge.charge(bot)
                origin = expr.Vec3(*bot.position)
                x, y, z = origin
                if barrier in ("low", "high"):
                    height = 1. if barrier == "low" else 2.
                    low, high, plane = y + height - .2, y + height + .2, z + .3
                    a, b = (x - 1, low, plane), (x + 1, low, plane)
                    c, d = (x + 1, high, plane), (x - 1, high, plane)
                    run.match.collision_world = World([a + b + c, a + c + d], name="Heavy release test panel")
                placements = []

                def place(caller_instance, node, _caller, *, placements=placements):
                    placements.append((node.pos, caller_instance.evaluate(node.field("m_target")),
                                       caller_instance.evaluate(node.field("m_AF774F7D"))))
                    return True

                # Trigger and placement are diagnostic. Queries and collision use live server state.
                instance.write((expr.INSTANCE, 536), expr.Entity(owner.body))
                with patch.dict(runtime.HANDLERS, {"STU_CFCE55DC": place}):
                    self.assertTrue(instance.run_node(instance.graph.node(109), None))
                self.assertEqual(instance.vars[633].value(), expr.f32(.35))
                self.assertEqual(instance.vars[2814].value(), barrier in ("low", "ice"))
                self.assertEqual(instance.vars[2823].value(), barrier in ("high", "ice"))
                self.assertEqual(len(placements), 1)
                node, target, position = placements[0]
                self.assertEqual(node, expected_node)
                self.assertEqual(target, expr.Entity(owner.body))
                self.assertAlmostEqual(position.x, origin.x, places=4)
                self.assertAlmostEqual(position.y, origin.y, places=4)
                self.assertAlmostEqual(position.z, origin.z + distance, places=4)

    def test_actual_heavy_release_double_hit_uses_native_scalar_distance(self):
        definition = next(d for d in self.definitions.values() if d.name == "Heavy Assault")
        _run, _owner, bot, _target = self.scene(definition)
        instance = heavy_charge.charge(bot)
        x, y, z = bot.position
        inputs = {431: expr.Vec3(x, y, z), 2814: True, 2823: True, 1188: expr.Vec3(x, y + 1., z + .3)}
        for identifier, value in inputs.items():
            instance.write((expr.INSTANCE, identifier), value)
        switch = instance.graph.node(108)
        low_case = switch.field("m_FDD34C25[0].m_value")
        selected = []

        def observe(_instance, node, _caller):
            selected.append(node.pos)
            return True

        # The authored bytecode compares a vector to a scalar before the outer distance.
        with patch.dict(runtime.HANDLERS, {"STU_CFCE55DC": observe}):
            for high_distance, result in ((.1, 1.), (0., 0.)):
                instance.write((expr.INSTANCE, 1189), expr.Vec3(x, y + 2., z + high_distance))
                self.assertEqual(instance.evaluate(low_case), result)
                self.assertTrue(instance.run_node(switch, None))
        self.assertEqual(selected, [103, 110])

    def test_actual_heavy_release_action_has_no_implicit_stun_target(self):
        definition = next(d for d in self.definitions.values() if d.name == 'Heavy Assault')
        _run, _owner, bot, _target = self.scene(definition)
        instance = heavy_charge.charge(bot)
        node = instance.graph.node(188)
        resource = play.resolve_resource(instance, node)
        self.assertEqual(resource.guid, 0x058000000000224E)
        self.assertAlmostEqual(dict(resource.overrides)[0x0D800000000000D7], .65)
        provider = self.fail
        self.assertIsNone(play.resolve_request(instance, node, resource, context_provider=provider))
        self.assertTrue(instance.run_node(node, None))

    def test_actual_heavy_charge_graze_damage_and_mei_defenses(self):
        definition = next(d for d in self.definitions.values() if d.name == 'Heavy Assault')
        for defense in ("none", "cryo", "projected", "ally", "frontal", "occluded", "out_of_range"):
            with self.subTest(defense=defense):
                run, owner, bot, _target = self.scene(definition, 8.)
                fight = run.match.combat
                owner_target = next(t for t in fight._targets() if t.owner is owner)
                vitals = fight.vitals_of(owner_target)
                x, y, z = owner.mover.position
                caster = run.players[1]
                if defense in ("projected", "ally"):
                    run.match.game_map = replace(run.match.game_map, free_for_all=False)
                    owner.team = caster.team = 0
                if defense == "projected":
                    self.assertTrue(run.match.switch_hero(caster, heroes()[zarya_shields.HERO]))
                    fight.walls.place_player(caster, (x + 5., y, z))
                npc.engage(run.match, bot, owner, action="charge")
                if defense == "ally":
                    owner.team = bot.team
                if defense != "frontal":
                    side = 4.5 if defense == "out_of_range" else 1.7
                    fight.walls.place_player(owner, (x + side, y, z))
                if defense == "occluded":
                    at, lo, hi, top = x + 0.9, z - 10., z + 10., y + 4.
                    triangles = [
                        (-200, y, -200, -200, y, 200, 200, y, 200),
                        (-200, y, -200, 200, y, 200, 200, y, -200),
                        (at, y, lo, at, y, hi, at, top, hi),
                        (at, y, lo, at, top, hi, at, top, lo),
                        (at, y, lo, at, top, hi, at, y, hi),
                        (at, y, lo, at, top, lo, at, top, hi),
                    ]
                    collision = World(triangles, name='Heavy charge side cover')
                    run.match.collision_world = collision
                    for player in run.players:
                        player.mover.use(world=collision)
                for _ in range(45):
                    run.step({})
                bubble = None
                if defense == "cryo":
                    run.step({owner: (0, 0, 8)})
                    self.assertTrue(mei.protected(owner))
                elif defense == "projected":
                    bubble = shield_fixtures.ShieldTests().cast(run, caster, owner)
                for _ in range(50):
                    run.step({})
                expected = 200. if defense == "none" else 250.
                self.assertEqual(vitals.total, expected)
                if defense in ("cryo", "projected"):
                    self.assertEqual(owner.mover.velocity, (0., 0., 0.))
                if bubble is not None:
                    self.assertEqual(bubble.health, 150.)
                if defense in ("none", "cryo", "projected"):
                    self.assertEqual(fight.heavy_charge_contacts[bot.entity], {owner.body})
                else:
                    self.assertNotIn(bot.entity, fight.heavy_charge_contacts)
                if defense == "occluded":
                    self.assertLess(bot.position[2], z)
                if defense in ("none", "projected"):
                    fight.walls.place_player(owner, (x + 1.7, y, bot.position[2] - 2.5))
                    for _ in range(25):
                        run.step({})
                    self.assertEqual(vitals.total, expected)
                    if bubble is not None:
                        self.assertEqual(bubble.health, 150.)
                npc.engage(run.match, bot, None)
                self.assertNotIn(bot.entity, fight.heavy_charge_contacts)

    def test_actual_heavy_graze_launches_mei_away_from_the_charge(self):
        definition = next(d for d in self.definitions.values() if d.name == 'Heavy Assault')
        run, owner, bot, _target = self.scene(definition, 8.)
        fight = run.match.combat
        target = next(t for t in fight._targets() if t.owner is owner)
        vitals = fight.vitals_of(target)
        x, y, z = owner.mover.position
        npc.engage(run.match, bot, owner, action="charge")
        fight.walls.place_player(owner, (x + 1.7, y, z))
        for _ in range(95):
            run.step({})
            if vitals.total < 250.:
                break
        self.assertEqual(vitals.total, 200.)
        self.assertGreater(owner.mover.velocity[0], 15.)
        self.assertGreater(owner.mover.velocity[1], 8.)
        start = tuple(owner.mover.position)
        for _ in range(3):
            run.step({})
        self.assertGreater(owner.mover.position[0], start[0] + .5)
        self.assertGreater(owner.mover.position[1], start[1] + .1)
        self.assertEqual(vitals.total, 200.)

    def test_actual_heavy_charge_attaches_transports_and_releases_mei(self):
        definition = next(d for d in self.definitions.values() if d.name == 'Heavy Assault')
        run, owner, bot, _target = self.scene(definition, 8.)
        fight = run.match.combat
        start = owner.mover.position[2]
        for _ in range(25):
            run.step({})
        npc.engage(run.match, bot, owner, action="charge")
        for _ in range(85):
            run.step({})
            if bot.entity in fight.heavy_charge_pins:
                break
        self.assertIn(bot.entity, fight.heavy_charge_pins)
        app = fight.heavy_charge_pins[bot.entity]
        self.assertEqual(app.graph.index, 0x1407)
        self.assertTrue(app.states[app.graph.node(1).state].active)
        self.assertTrue(fight.is_stunned(owner))
        for _ in range(25):
            run.step({owner: (0, 0, 8)})
        self.assertLess(owner.mover.position[2], start - 1.)
        self.assertFalse(mei.protected(owner))
        npc.engage(run.match, bot, None)
        self.assertNotIn(bot.entity, fight.heavy_charge_pins)
        self.assertNotIn(app.id, owner.body_script.component.instances)
        self.assertFalse(fight.is_stunned(owner))

    def test_actual_heavy_frontal_pin_respects_defenses_and_victim_replacement(self):
        definition = next(d for d in self.definitions.values() if d.name == 'Heavy Assault')
        for case in ("cryo", "projected", "shield_after_pin", "replace"):
            with self.subTest(case=case):
                run, owner, bot, _target = self.scene(definition, 8.)
                fight = run.match.combat
                caster = run.players[1]
                if case.startswith("shield") or case == "projected":
                    run.match.game_map = replace(run.match.game_map, free_for_all=False)
                    owner.team = caster.team = 0
                    self.assertTrue(run.match.switch_hero(caster, heroes()[zarya_shields.HERO]))
                    x, y, z = owner.mover.position
                    fight.walls.place_player(caster, (x + 5., y, z))
                npc.engage(run.match, bot, owner, action="charge")
                for _ in range(45):
                    run.step({})
                bubble = None
                if case == "cryo":
                    run.step({owner: (0, 0, 8)})
                    self.assertTrue(mei.protected(owner))
                elif case == "projected":
                    bubble = shield_fixtures.ShieldTests().cast(run, caster, owner)
                for _ in range(45):
                    run.step({})
                    if bot.entity in fight.heavy_charge_pins:
                        break
                if case in ("shield_after_pin", "replace"):
                    self.assertTrue(fight.is_stunned(owner))
                    if case == "shield_after_pin":
                        bubble = shield_fixtures.ShieldTests().cast(run, caster, owner)
                    else:
                        self.assertTrue(run.match.switch_hero(owner, heroes()[owner.hero.guid]))
                for _ in range(8):
                    run.step({})
                self.assertNotIn(bot.entity, fight.heavy_charge_pins)
                self.assertFalse(fight.is_stunned(owner))
                if bubble is not None:
                    self.assertEqual(bubble.health, 25. if case == "projected" else 200.)

    def test_actual_heavy_charge_crushes_bound_mei_against_static_and_ice_wall(self):
        definition = next(d for d in self.definitions.values() if d.name == 'Heavy Assault')
        for ice in (False, True):
            with self.subTest(ice=ice):
                run, owner, bot, _target = self.scene(definition, 8.)
                fight = run.match.combat
                target = next(t for t in fight._targets() if t.owner is owner)
                vitals = fight.vitals_of(target)
                x, y, z = owner.mover.position
                if ice:
                    walls = wall_fixtures.WallTests()
                    aim = fixtures.aim(fight._eye(owner), (x, y, z - 5.))
                    walls.tap(run, owner, 16, aim)
                    walls.tap(run, owner, 1, aim)
                    self.assertEqual(len(fight.walls.pieces), 5)
                else:
                    fixtures.static_wall(run, owner, distance=-5.)
                    # Increase only the fixture pool to measure the full 300-point hit.
                    vitals.most[combat.HEALTH_POOL] = vitals.now[combat.HEALTH_POOL] = 400.
                npc.engage(run.match, bot, owner, action="charge")
                attached = False
                for _ in range(140):
                    run.step({})
                    attached |= bot.entity in fight.heavy_charge_pins
                    if attached and bot.entity not in fight.heavy_charge_pins:
                        break
                self.assertTrue(attached)
                self.assertNotIn(bot.entity, fight.heavy_charge_pins)
                self.assertFalse(fight.is_stunned(owner))
                self.assertGreater(bot.position[2], z - 5.)
                self.assertEqual(vitals.total, 0. if ice else 100.)
                self.assertEqual(vitals.dead, ice)
                if ice:
                    self.assertEqual(vitals.killer, bot.entity)
                for _ in range(10):
                    run.step({})
                self.assertEqual(vitals.total, 0. if ice else 100.)

    def test_actual_heavy_close_impact_deals_damage_and_only_stuns_accepted_hit(self):
        definition = next(d for d in self.definitions.values() if d.name == 'Heavy Assault')
        for defense in ("none", "cryo", "projected"):
            with self.subTest(defense=defense):
                run, owner, bot, _target = self.scene(definition, 8.)
                fight = run.match.combat
                target = next(t for t in fight._targets() if t.owner is owner)
                vitals = fight.vitals_of(target)
                caster = run.players[1]
                if defense == "projected":
                    run.match.game_map = replace(run.match.game_map, free_for_all=False)
                    owner.team = caster.team = 0
                    self.assertTrue(run.match.switch_hero(caster, heroes()[zarya_shields.HERO]))
                    x, y, z = owner.mover.position
                    fight.walls.place_player(caster, (x + 5., y, z))
                npc.engage(run.match, bot, owner, action="charge")
                for _ in range(53):
                    run.step({})
                instance = heavy_charge.charge(bot)
                self.assertTrue(heavy_charge.driving(instance))
                bubble = None
                if defense == "projected":
                    bubble = shield_fixtures.ShieldTests().cast(run, caster, owner)
                elif defense == "cryo":
                    run.step({owner: (0, 0, 8)})
                    self.assertTrue(mei.protected(owner))
                x, y, z = bot.position
                fight.walls.place_player(owner, (x, y, z - .45))
                run.step({})
                expected = 75. if defense == "none" else 250.
                self.assertEqual(vitals.total, expected)
                self.assertEqual(heavy_charge.phase(instance), 0)
                self.assertNotIn(bot.entity, fight.heavy_charge_pins)
                if defense == "none":
                    remaining = (fight.stuns[owner.body] - fight.tick) * combat.TICK
                    self.assertAlmostEqual(remaining, 2., delta=.016)
                else:
                    self.assertFalse(fight.is_stunned(owner))
                if bubble is not None:
                    self.assertEqual(bubble.health, 25.)
                for _ in range(15):
                    run.step({})
                self.assertEqual(vitals.total, expected)
                if defense == "none":
                    deadline = fight.stuns[owner.body]
                    while fight.tick < deadline - 1:
                        run.step({})
                    self.assertTrue(fight.is_stunned(owner))
                    run.step({})
                    self.assertFalse(fight.is_stunned(owner))

    def test_actual_heavy_close_impact_distinguishes_bound_and_unbound_victims(self):
        definition = next(d for d in self.definitions.values() if d.name == 'Heavy Assault')
        run, owner, bot, _target = self.scene(definition, 8.)
        fight = run.match.combat
        other = run.players[1]
        vitals = {t.owner: fight.vitals_of(t) for t in fight._targets() if t.owner in run.players}
        npc.engage(run.match, bot, owner, action="charge")
        for _ in range(85):
            run.step({})
            if fight.pins.get(bot.entity) == owner.body:
                break
        self.assertEqual(fight.pins.get(bot.entity), owner.body)
        x, y, z = bot.position
        fight.walls.place_player(other, (x, y, z - .45))
        run.step({})
        self.assertTrue(vitals[owner].dead)
        self.assertEqual(vitals[owner].killer, bot.entity)
        self.assertEqual(vitals[other].total, 25.)
        self.assertTrue(fight.is_stunned(other))
        self.assertNotIn(bot.entity, fight.heavy_charge_pins)

    def test_actual_heavy_and_reinhardt_counter_charge_requires_opposing_active_charge(self):
        definition = next(d for d in self.definitions.values() if d.name == 'Heavy Assault')
        reinhardt = next(h for h in heroes().values() if h.name == "Reinhardt")
        for case in ("opposing", "same_direction", "idle"):
            with self.subTest(case=case):
                run, owner, bot, bot_target = self.scene(definition, 8.)
                other = run.players[1]
                self.assertTrue(run.match.switch_hero(other, reinhardt))
                fight = run.match.combat
                x, y, z = owner.mover.position
                fight.walls.place_player(other, (x + 1.5, y, z + 2.))
                target = next(t for t in fight._targets() if t.owner is other)
                health = fight.vitals_of(target).total
                bot_health = fight.vitals_of(bot_target).total
                npc.engage(run.match, bot, owner, action="charge")
                yaw = -32768 if case == "same_direction" else 14500
                for tick in range(85):
                    button = 8 if tick == 64 and case != "idle" else 0
                    run.step({other: (yaw, 0, button)})
                    if bot.entity in fight.stuns:
                        break
                if case == "opposing":
                    self.assertEqual(fight.stuns[bot.entity] - fight.tick, 125)
                    self.assertEqual(fight.stuns[other.body] - fight.tick, 125)
                    self.assertEqual(fight.vitals_of(target).total, health)
                    self.assertEqual(fight.vitals_of(bot_target).total, bot_health)
                    self.assertEqual(heavy_charge.phase(heavy_charge.charge(bot)), 0)
                else:
                    self.assertNotIn(bot.entity, fight.stuns)
                    self.assertNotIn(other.body, fight.stuns)

    def test_actual_fortify_stops_heavy_charge_without_pin_or_dual_stun(self):
        definition = next(d for d in self.definitions.values() if d.name == 'Heavy Assault')
        orisa = next(h for h in heroes().values() if h.name == "Orisa")
        run, owner, bot, bot_target = self.scene(definition, 8.)
        other = run.players[1]
        self.assertTrue(run.match.switch_hero(other, orisa))
        fight = run.match.combat
        x, y, z = owner.mover.position
        fight.walls.place_player(other, (x, y, z + 2.))
        target = next(t for t in fight._targets() if t.owner is other)
        health = fight.vitals_of(target).total
        bot_health = fight.vitals_of(bot_target).total
        instance = heavy_charge.charge(bot)
        npc.engage(run.match, bot, owner, action="charge")
        for _ in range(48):
            run.step({})
        run.step({other: (0, 0, 8)})
        self.assertTrue(heavy_charge.charge_immune(other))
        for _ in range(35):
            run.step({})
            self.assertNotIn(bot.entity, fight.heavy_charge_pins)
            if not heavy_charge.phase(instance):
                break
        self.assertEqual(heavy_charge.phase(instance), 0)
        run.step({})
        self.assertGreater(instance.vars[61].value(), 0.)
        self.assertTrue(heavy_charge.charge_immune(other))
        self.assertEqual(fight.vitals_of(target).total, health)
        self.assertEqual(fight.vitals_of(bot_target).total, bot_health)
        self.assertFalse(fight.is_stunned(other))
        self.assertFalse(fight.is_stunned(bot))
        self.assertAlmostEqual(other.mover.position[2], z + 2., places=3)

    def test_actual_fortify_releases_a_bound_mei_without_damage_or_stun(self):
        definition = next(d for d in self.definitions.values() if d.name == 'Heavy Assault')
        orisa_hero = next(h for h in heroes().values() if h.name == "Orisa")
        run, owner, orisa, other = shield_fixtures.ShieldTests().scene()
        fight = run.match.combat
        self.assertTrue(run.match.switch_hero(orisa, orisa_hero))
        x, y, z = owner.mover.position
        owner.team = orisa.team = 0
        fight.walls.place_player(orisa, (x, y, z - 6.))
        fight.walls.place_player(other, (x + 20., y, z + 5.))
        for player in run.players:
            player.client = NpcClient(player)
        bot = npc.spawn(run.match, definition, (x, y, z + 8.), team=1, yaw=-32768)
        npc.engage(run.match, bot, owner, action="charge")
        for _ in range(100):
            run.step({})
            if fight.pins.get(bot.entity) == owner.body:
                break
        self.assertEqual(fight.pins.get(bot.entity), owner.body)
        app = fight.heavy_charge_pins[bot.entity]
        charge = heavy_charge.charge(bot)
        placements = []
        apply_placement = runtime.HANDLERS["STU_CFCE55DC"]

        def observe_placement(instance, node, _caller):
            if node.pos == 111:
                placements.append((instance, node.pos, instance.evaluate(node.field("m_target")),
                                   instance.evaluate(node.field("m_AF774F7D"))))
            return apply_placement(instance, node, _caller)

        trace = patch.dict(runtime.HANDLERS, {"STU_CFCE55DC": observe_placement})
        trace.start()
        self.addCleanup(trace.stop)
        run.step({orisa: (0, 0, 8)})
        self.assertTrue(heavy_charge.charge_immune(orisa))
        for _ in range(50):
            run.step({})
            if bot.entity not in fight.heavy_charge_pins:
                break
        self.assertNotIn(bot.entity, fight.heavy_charge_pins)
        self.assertNotIn(app.id, owner.body_script.component.instances)
        self.assertFalse(fight.is_stunned(owner))
        self.assertFalse(fight.is_stunned(orisa))
        self.assertTrue(heavy_charge.charge_immune(orisa))
        target = next(t for t in fight._targets() if t.owner is owner)
        self.assertEqual(fight.vitals_of(target).total, 250.)
        self.assertGreater(owner.mover.position[2], orisa.mover.position[2])
        released = tuple(owner.mover.position)
        run.step({owner: (0, 0, 8)})
        self.assertTrue(mei.protected(owner))
        self.assertEqual(len(placements), 1)
        instance, node, victim, point = placements[0]
        self.assertIs(instance, charge)
        self.assertEqual(node, 111)
        self.assertEqual(victim, expr.Entity(owner.body))
        self.assertEqual(charge.vars[633].value(), expr.f32(.35))
        self.assertFalse(charge.vars[2814].value())
        self.assertFalse(charge.vars[2823].value())
        direction = combat.aim(round(bot.yaw), 0)
        for actual, expected in zip(point, combat._along(bot.position, direction, .35), strict=True):
            self.assertAlmostEqual(actual, expected, places=4)
        for actual, expected in zip(released, point, strict=True):
            self.assertAlmostEqual(actual, expected, delta=1 / 1024)
        wait = charge.states[charge.graph.node(105).state]
        self.assertTrue(wait.active)
        self.assertTrue(charge.vars[559].value())
        for _ in range(120):
            run.step({})
        self.assertTrue(wait.active)
        for _ in range(7):
            run.step({})
        self.assertFalse(wait.active)
        self.assertEqual(len(placements), 1)
        self.assertFalse(fight.pins)
        self.assertEqual(fight.vitals_of(target).total, 250.)

        fight.walls.place_player(orisa, (x + 20., y, z))
        run.step({owner: (0, 0, 2)})
        self.assertFalse(mei.protected(owner))
        for _ in range(20):
            run.step({})
        ox, oy, oz = owner.mover.position
        bot.position = (ox, oy, oz + 8.)
        ability = charge.states[charge.graph.node(82).state]
        self.assertGreater(ability.remaining(), 0.)
        npc.engage(run.match, bot, owner, action="charge")
        run.step({})
        self.assertEqual(heavy_charge.phase(charge), 0)
        self.assertFalse(fight.pins)
        for _ in range(1000):
            run.step({})
            if ability.remaining() <= 0.:
                break
        self.assertLessEqual(ability.remaining(), 0.)
        self.assertEqual(heavy_charge.phase(charge), 0)
        self.assertFalse(fight.pins)
        npc.engage(run.match, bot, owner, action="charge")
        for _ in range(85):
            run.step({})
            if fight.pins.get(bot.entity) == owner.body:
                break
        self.assertEqual(fight.pins.get(bot.entity), owner.body)
        self.assertFalse(charge.vars[559].value())
        self.assertEqual(len(placements), 1)

    def test_heavy_release_placement_rejects_obstruction_and_stale_binding(self):
        definition = next(d for d in self.definitions.values() if d.name == "Heavy Assault")
        run, owner, bot, _target = self.scene(definition, 8.)
        fight = run.match.combat
        npc.engage(run.match, bot, owner, action="charge")
        for _ in range(100):
            run.step({})
            if fight.pins.get(bot.entity) == owner.body:
                break
        self.assertEqual(fight.pins.get(bot.entity), owner.body)
        instance = heavy_charge.charge(bot)
        node = instance.graph.node(111)
        x, y, z = start = tuple(owner.mover.position)
        destination = expr.Vec3(x, y, z + 2.)
        instance.write((expr.INSTANCE, 431), destination)
        instance.write((expr.INSTANCE, 633), 0.)
        a, b = (x - 2., y, z + 1.), (x + 2., y, z + 1.)
        c, d = (x + 2., y + 3., z + 1.), (x - 2., y + 3., z + 1.)
        original = run.match.collision_world
        run.match.collision_world = World([a + b + c, a + c + d], name="Heavy victim placement blocker")
        heavy_charge.place_released_victim(instance, node)
        self.assertEqual(tuple(owner.mover.position), start)
        run.match.collision_world = original
        npc.engage(run.match, bot, None)
        self.assertFalse(fight.pins)
        heavy_charge.place_released_victim(instance, node)
        self.assertEqual(tuple(owner.mover.position), start)

    def test_actual_heavy_melee_uses_authored_hit_and_mei_defenses(self):
        definition = next(d for d in self.definitions.values() if d.name == 'Heavy Assault')
        for case in ("hit", "cryo", "range", "static", "ice", "projected", "ally", "cancel"):
            with self.subTest(case=case):
                distance = 8. if case == "range" else 5. if case == "ice" else 2.
                run, owner, bot, _target = self.scene(definition, distance)
                fight = run.match.combat
                target = next(t for t in fight._targets() if t.owner is owner)
                vitals = fight.vitals_of(target)
                bubble = None
                if case in ("projected", "ally"):
                    run.match.game_map = replace(run.match.game_map, free_for_all=False)
                    owner.team = 0
                if case == "projected":
                    caster = run.players[1]
                    caster.team = 0
                    self.assertTrue(run.match.switch_hero(caster, heroes()[zarya_shields.HERO]))
                    x, y, z = owner.mover.position
                    fight.walls.place_player(caster, (x + 5., y, z))
                    bubble = shield_fixtures.ShieldTests().cast(run, caster, owner)
                if case == "static":
                    fixtures.static_wall(run, owner)
                if case == "ice":
                    pieces, _ = wall_fixtures.WallTests().place(run, owner)
                    centre = pieces[2]
                if case == "cryo":
                    run.step({owner: (0, 0, 8)})
                    self.assertTrue(mei.protected(owner))
                npc.engage(run.match, bot, owner, action="melee")
                if case == "ally":
                    owner.team = bot.team
                if case == "cancel":
                    run.step({})
                    npc.engage(run.match, bot, None)
                for _ in range(85):
                    run.step({})
                self.assertIsNotNone(bot.body_script)
                self.assertFalse(bot.firing)
                self.assertEqual(vitals.total, 170. if case == "hit" else 250.)
                if case == "ice":
                    self.assertEqual(centre.health, 320.)
                if bubble is not None:
                    self.assertEqual(bubble.health, 120.)

    def test_actual_trooper_melee_waits_and_uses_the_1_5_metre_gate(self):
        definition = next(d for d in self.definitions.values() if d.name == "Trooper")
        for case in ("hit", "range", "cryo", "static", "ally", "cancel", "projected", "ice"):
            with self.subTest(case=case):
                distance = 1.6 if case == "range" else 4.4 if case == "ice" else 1.4
                run, owner, bot, _target = self.scene(definition, distance)
                fight = run.match.combat
                target = next(t for t in fight._targets() if t.owner is owner)
                vitals = fight.vitals_of(target)
                bubble = None
                if case == "projected":
                    run.match.game_map = replace(run.match.game_map, free_for_all=False)
                    caster = run.players[1]
                    owner.team = caster.team = 0
                    self.assertTrue(run.match.switch_hero(caster, heroes()[zarya_shields.HERO]))
                    x, y, z = owner.mover.position
                    fight.walls.place_player(caster, (x + 5., y, z))
                    bubble = shield_fixtures.ShieldTests().cast(run, caster, owner)
                if case == "ice":
                    pieces, _ = wall_fixtures.WallTests().place(run, owner)
                if case == "static":
                    fixtures.static_wall(run, owner, .7)
                if case == "cryo":
                    run.step({owner: (0, 0, 8)})
                    self.assertTrue(mei.protected(owner))
                if case == "ally":
                    run.match.game_map = replace(run.match.game_map, free_for_all=False)
                    owner.team = 0
                npc.engage(run.match, bot, owner, action="melee")
                if case == "ally":
                    owner.team = bot.team
                for _ in range(10):
                    run.step({})
                self.assertEqual(vitals.total, 250.)
                if case == "cancel":
                    npc.engage(run.match, bot, None)
                for _ in range(55):
                    run.step({})
                self.assertIsNotNone(bot.body_script)
                self.assertEqual(vitals.total, 220. if case == "hit" else 250.)
                if bubble is not None:
                    self.assertEqual(bubble.health, 170.)
                if case == "ice":
                    self.assertEqual(pieces[2].health, 370.)

    @unittest.skipUnless(
        os.environ.get("ORPHEUS_ENFORCER_CONTROLLER_DATA"), "explicit controller data required"
    )
    def test_actual_enforcer_controller_owns_weapon_readiness_and_retires(self):
        path = Path(os.environ["ORPHEUS_ENFORCER_CONTROLLER_DATA"])
        supplement = json.loads(path.read_text(encoding="utf-8-sig"))
        merged = {**self.graph_data, "entities": {**self.graph_data["entities"], **supplement["entities"]}}
        records = {item["index"]: item for item in self.graph_data["graphs"]}
        records.update({item["index"]: item for item in supplement["graphs"]})
        merged["graphs"] = list(records.values())
        with patch.object(graph, "_data", return_value=merged):
            graph.graph.cache_clear()
            graph._records.cache_clear()
            self.addCleanup(graph.graph.cache_clear)
            self.addCleanup(graph._records.cache_clear)
            definition = next(d for d in self.definitions.values() if d.name == "Enforcer")
            run, owner, bot, _target = self.scene(definition, 3.)
            script = bot.body_script
            controller = script.controller
            self.assertIsNotNone(controller)
            self.assertNotEqual(controller.entity, bot.entity)
            self.assertEqual({i.graph.index for i in controller.instances.values()},
                             {0x142A, 0xE5C, 0xC83, 0x13E9, 0x13EA})
            self.assertTrue(controller.entity_var(9134, create=False).value())
            self.assertIsNone(script.component.entity_var(9134, create=False))
            self.assertTrue(script.component.world.entity_var(controller.entity, 9134))
            export_component_vm(script.component)
            export_component_vm(controller)
            target = next(t for t in run.match.combat._targets() if t.owner is owner)
            vitals = run.match.combat.vitals_of(target)
            controller_graph = next(i for i in controller.instances.values() if i.graph.index == 0x13EA)
            query = controller_graph.graph.node(1).field("m_AAAC951E")[1]
            self.assertEqual(controller_graph.evaluate(query), expr.Entity(0))
            npc.engage(run.match, bot, owner)
            self.assertEqual(controller_graph.evaluate(query), expr.Entity(owner.body))
            for _ in range(65):
                run.step({})
                if vitals.total < 250.:
                    break
            self.assertLess(vitals.total, 250.)
            self.assertGreater(controller.frame, 1030)
            run.match.game_map = replace(run.match.game_map, free_for_all=False)
            owner.team = bot.team
            self.assertEqual(controller_graph.evaluate(query), expr.Entity(0))
            owner.team = 0
            old_body = owner.body
            self.assertTrue(run.match.switch_hero(owner, heroes()[owner.hero.guid]))
            self.assertNotEqual(owner.body, old_body)
            self.assertEqual(controller_graph.evaluate(query), expr.Entity(0))
            npc.engage(run.match, bot, owner)
            self.assertEqual(controller_graph.evaluate(query), expr.Entity(owner.body))
            self.assertEqual(npc.remove_spawned(run.match), (bot.entity,))
            self.assertTrue(controller.owner_dead)
            self.assertFalse(controller.instances)
            self.assertIsNone(script.component.world.entity_var(controller.entity, 9134))
            replacement = npc.spawn(run.match, definition, bot.position, team=bot.team)
            self.assertGreater(replacement.entity, controller.entity)
            active_controller = replacement.body_script.controller
            victim = next(t for t in run.match.combat._targets() if t.owner is replacement)
            pools = run.match.combat.vitals_of(victim)
            pools.now.update({combat.HEALTH_POOL: 40., combat.ARMOUR_POOL: 0., combat.SHIELDS_POOL: 0.})
            fixtures.MeiCombatTests().fire(run, owner, victim, 2, .9)
            self.assertTrue(replacement.dead)
            self.assertTrue(active_controller.owner_dead)
            self.assertFalse(active_controller.instances)

            run, owner, bot, _target = self.scene(definition, 16.)
            script = bot.body_script
            controller = script.controller
            provider = next(i for i in controller.instances.values() if i.graph.index == 0x13EA)
            npc.engage(run.match, bot, owner)
            for _ in range(12):
                run.step({})
            self.assertAlmostEqual(provider.vars[680].value(), 16.)
            self.assertEqual(npc_context.movement_scale(bot), 1.)
            x, y, z = owner.mover.position
            bot.position = (x, y, z + 14.)
            for _ in range(12):
                run.step({})
            self.assertEqual(controller.entity_var(12299, create=False).value(), 1)
            self.assertAlmostEqual(npc_context.movement_scale(bot), .4)
            animation = next(i for i in script.component.instances.values() if i.graph.index == 0x1506)
            gate = animation.graph.node(16).field("m_condition")
            native_ingress = animation.graph.node(12)
            self.assertTrue(animation.states[native_ingress.state].active)
            self.assertFalse(expr.truthy(animation.evaluate(gate)))
            script.component.set(14175, 2)
            self.assertFalse(expr.truthy(animation.evaluate(gate)))
            controller.set(14175, 2)
            for _ in range(3):
                run.step({})
            self.assertTrue(expr.truthy(animation.evaluate(gate)))
            self.assertFalse(animation.states[native_ingress.state].active)
            controller.set(14175, 0)
            for _ in range(3):
                run.step({})
            self.assertTrue(animation.states[native_ingress.state].active)
            self.assertFalse(animation.states.get(animation.graph.node(1).state))
            # Inject one authored 35AE candidate from 08A3.021 (clip 5316.006).
            # The native listener's event and candidate selection are unhosted.
            for number, value in ((13233, 0x0C80000000000581), (13234, 0x0280000000000009),
                                  (13235, 1.133333444595337)):
                animation.write((expr.INSTANCE, number), value)
            listener = animation.states[native_ingress.state]
            self.assertTrue(animation.follow(native_ingress, "m_806B068F", listener))
            script.component.run_now()
            for position in (1, 3):
                state = animation.states[animation.graph.node(position).state]
                self.assertEqual(state.counter, 1)
                self.assertFalse(state.active)
            rearm = animation.states[animation.graph.node(11).state]
            self.assertTrue(rearm.active)
            self.assertTrue(listener.active)
            for _ in range(120):
                run.step({})
            self.assertTrue(rearm.active)
            for _ in range(6):
                run.step({})
            self.assertFalse(rearm.active)
            self.assertTrue(listener.active)
            bot.route = [bot.position, (x, y, z + 5.)]
            bot.index, bot.wait = 0, 0.
            for _ in range(10):
                run.step({})
            self.assertLess(bot.position[2], z + 14.)
            self.assertAlmostEqual(bot.pace, bot.speed * .4)
            bot.firing = False  # Keep the selected controller input without shooting the wall.
            bot.route = []
            bot.position = (x, y, z + 16.)
            for _ in range(10):
                run.step({})
            self.assertAlmostEqual(npc_context.movement_scale(bot), .4)
            walls = wall_fixtures.WallTests()
            pieces, wall_aim = walls.place(run, owner)
            self.assertTrue(pieces)
            for _ in range(80):
                run.step({})
            self.assertEqual(npc_context.movement_scale(bot), 1.)
            walls.tap(run, owner, 16, wall_aim)
            bot.position = (x, y, z + 14.)
            for _ in range(12):
                run.step({})
            self.assertAlmostEqual(npc_context.movement_scale(bot), .4)
            npc.engage(run.match, bot, None)
            for _ in range(15):
                run.step({})
            self.assertEqual(npc_context.movement_scale(bot), 1.)

            from test_server_snapshot import ServerSnapshotTests

            fixture = ServerSnapshotTests()
            session, _, players = fixture.scene()
            live = npc.spawn(players[0].match, definition, (0., 1., 5.), team=1)
            self.assertIsNotNone(live.body_script.controller)
            live_controller = live.body_script.controller
            digest = snapshot_digest(fixture.capture(session))
            self.assertEqual(snapshot_digest(fixture.capture(session)), digest)
            live_controller.set(12299, True)
            self.assertNotEqual(snapshot_digest(fixture.capture(session)), digest)

    def test_actual_sniper_repositions_away_from_mei_and_rejects_early_repeat(self):
        definition = next(d for d in self.definitions.values() if d.name == "Sniper")
        run, owner, bot, _target = self.scene(definition, 2.)
        fixtures.static_wall(run, owner, distance=100.)
        fight = run.match.combat
        start = bot.position
        npc.engage(run.match, bot, owner, action="reposition")
        state = sniper_reposition.move_state(bot)
        destination, speed = state.move_request()
        self.assertEqual(speed, 20.)
        self.assertAlmostEqual(destination.z, start[2] + 4.)
        self.assertIn(bot.entity, fight.sniper_repositions)
        with self.assertRaises(ValueError):
            npc.engage(run.match, bot, owner, action="reposition")
        for _ in range(35):
            run.step({})
            if bot.entity not in fight.sniper_repositions:
                break
        self.assertNotIn(bot.entity, fight.sniper_repositions)
        self.assertFalse(state.active)
        self.assertAlmostEqual(bot.position[2], destination.z, delta=.2)
        self.assertEqual(bot.velocity, (0., 0., 0.))
        fight.walls.place_player(owner, (bot.position[0], bot.position[1], bot.position[2] - 2.))
        with self.assertRaises(ValueError):
            npc.engage(run.match, bot, owner, action="reposition")
        for _ in range(380):
            run.step({})
        npc.engage(run.match, bot, owner, action="reposition")
        self.assertTrue(state.active)

    def test_actual_sniper_reposition_rejects_range_and_mei_ice_wall(self):
        definition = next(d for d in self.definitions.values() if d.name == "Sniper")
        run, owner, bot, _target = self.scene(definition, 5.)
        fixtures.static_wall(run, owner, distance=100.)
        with self.assertRaises(ValueError):
            npc.engage(run.match, bot, owner, action="reposition")
        walls = wall_fixtures.WallTests()
        pieces, aim = walls.place(run, owner, distance=2.)
        self.assertTrue(pieces)
        x, y, z = owner.mover.position
        bot.position = (x, y, z + 2.8)
        with self.assertRaises(ValueError):
            npc.engage(run.match, bot, owner, action="reposition")
        walls.tap(run, owner, 16, aim)
        npc.engage(run.match, bot, owner, action="reposition")
        move = sniper_reposition.move_state(bot)
        self.assertTrue(move.active)
        run.step({})
        animation = (move.instance.id, 6)
        self.assertEqual(bot.body_script.latest_live.states[animation][:2], ("STUStatescriptStateAnim", True))
        with patch.object(owner.client, "queue_bot_live") as publish:
            npc.engage(run.match, bot, None)
        self.assertFalse(move.active)
        self.assertNotIn(animation, bot.body_script.latest_live.states)
        publish.assert_called_once_with(bot.entity, bot.body_script.latest_live)

    def test_actual_sniper_a45_closes_the_idle_search_scope(self):
        definition = next(d for d in self.definitions.values() if d.name == "Sniper")
        run, owner, bot, _target = self.scene(definition, 2.)
        instance = sniper_reposition.move_state(bot).instance
        npc.engage(run.match, bot, owner)
        for _ in range(90):
            run.step({})
        entry = instance.states[instance.graph.node(98).state]
        search = instance.states[instance.graph.node(56).state]
        self.assertTrue(entry.active)
        self.assertTrue(search.active)
        self.assertEqual(runtime.send_instance_message(instance, 0x0240000000000A45, {}, owner.body), 1)
        instance.component.run_now()
        self.assertTrue(entry.received)
        self.assertFalse(search.active)
        self.assertFalse(sniper_reposition.move_state(bot).active)

    def test_actual_mei_wall_created_mid_retreat_stops_sniper_before_crossing(self):
        definition = next(d for d in self.definitions.values() if d.name == "Sniper")
        run, owner, bot, _target = self.scene(definition, 2.)
        fixtures.static_wall(run, owner, distance=100.)
        fight = run.match.combat
        x, y, z = owner.mover.position
        aim = fixtures.aim(fight._eye(owner), (x, y, z + 4.7))
        walls = wall_fixtures.WallTests()
        walls.tap(run, owner, 16, aim)
        npc.engage(run.match, bot, owner, action="reposition")
        walls.tap(run, owner, 1, aim)
        centre = list(fight.walls.pieces.values())[2]
        self.assertTrue(sniper_reposition.move_state(bot).active)
        for _ in range(30):
            run.step({})
            if bot.entity not in fight.sniper_repositions:
                break
        self.assertNotIn(bot.entity, fight.sniper_repositions)
        self.assertFalse(sniper_reposition.move_state(bot).active)
        self.assertGreater(bot.position[2], z + 2.)
        self.assertLess(bot.position[2], centre.feet[2] - centre.depth)
        self.assertEqual(bot.velocity, (0., 0., 0.))

    def test_stationary_sniper_falls_when_ice_wall_support_ends(self):
        definition = next(d for d in self.definitions.values() if d.name == "Sniper")
        for cancel in (True, False):
            with self.subTest(cancel=cancel):
                run, owner, bot, _ = self.scene(definition, 2.)
                fixtures.static_wall(run, owner, distance=100.)
                floor = bot.position
                fight = run.match.combat
                walls = wall_fixtures.WallTests()
                pieces, aim = walls.place(run, owner, distance=2.)
                self.assertEqual(len(pieces), 5)
                raised = bot.position
                self.assertGreater(raised[1], floor[1] + 4.)
                for _ in range(10):
                    run.step({})
                    self.assertEqual(bot.position, raised)
                if cancel:
                    walls.tap(run, owner, 16, aim)
                else:
                    for _ in range(320):
                        if not fight.walls.pieces:
                            break
                        run.step({})
                        self.assertEqual(bot.position, raised)
                self.assertFalse(fight.walls.pieces)
                fell = False
                for _ in range(85):
                    run.step({})
                    if floor[1] < bot.position[1] < raised[1]:
                        self.assertLess(bot.velocity[1], 0.)
                        fell = True
                    if bot.position == floor:
                        break
                self.assertTrue(fell)
                self.assertEqual(bot.position, floor)
                self.assertEqual(bot.velocity, (0., 0., 0.))
                self.assertFalse(getattr(bot, "wall_lifted", False))
                npc.engage(run.match, bot, owner, action="reposition")
                self.assertTrue(sniper_reposition.move_state(bot).active)

    def test_active_sniper_reposition_changes_full_snapshot_and_retires_on_removal(self):
        from test_server_snapshot import ServerSnapshotTests

        fixture = ServerSnapshotTests()
        session, _, players = fixture.scene()
        owner = players[0]
        match = owner.match
        definition = next(d for d in self.definitions.values() if d.name == "Sniper")
        x, y, z = owner.mover.position
        bot = npc.spawn(match, definition, (x, y, z + 2.), team=1 - owner.team)
        npc.engage(match, bot, owner, action="reposition")
        move = sniper_reposition.move_state(bot)
        mover = match.combat.sniper_repositions[bot.entity]
        self.assertTrue(move.active)
        start = snapshot_digest(fixture.capture(session))
        self.assertEqual(snapshot_digest(fixture.capture(session)), start)
        velocity = mover.velocity
        mover.velocity = (1., 0., 0.)
        self.assertNotEqual(snapshot_digest(fixture.capture(session)), start)
        mover.velocity = velocity
        self.assertEqual(snapshot_digest(fixture.capture(session)), start)
        self.assertEqual(npc.remove_spawned(match), (bot.entity,))
        self.assertFalse(move.active)
        self.assertNotIn(bot.entity, match.combat.sniper_repositions)
        self.assertNotEqual(snapshot_digest(fixture.capture(session)), start)

    def test_actual_icicle_kills_sniper_during_reposition_and_retires_move(self):
        definition = next(d for d in self.definitions.values() if d.name == "Sniper")
        run, owner, bot, target = self.scene(definition, 2.)
        fixtures.static_wall(run, owner, distance=100.)
        fight = run.match.combat
        vitals = fight.vitals_of(target)
        vitals.now.update({combat.HEALTH_POOL: 40., combat.ARMOUR_POOL: 0., combat.SHIELDS_POOL: 0.})
        point = (target.feet[0], target.feet[1] + .9, target.feet[2])
        yaw, pitch = fixtures.aim(fight._eye(owner), point)
        for frame in range(35):
            run.step({owner: (yaw, pitch, 2 if frame < 20 else 0)})
            if fight.projectiles.shots:
                break
        self.assertTrue(fight.projectiles.shots)
        self.assertFalse(vitals.dead)
        npc.engage(run.match, bot, owner, action="reposition")
        move = sniper_reposition.move_state(bot)
        self.assertTrue(move.active)
        for _ in range(15):
            run.step({})
            if bot.dead:
                break
        self.assertTrue(bot.dead)
        self.assertEqual(vitals.killer, owner.body)
        self.assertFalse(move.active)
        self.assertNotIn(bot.entity, fight.sniper_repositions)
        self.assertEqual(bot.velocity, (0., 0., 0.))

    def test_actual_spray_freeze_aborts_sniper_reposition_without_catchup(self):
        definition = next(d for d in self.definitions.values() if d.name == "Sniper")
        run, owner, bot, target = self.scene(definition, 2.)
        fixtures.static_wall(run, owner, distance=100.)
        fight = run.match.combat
        point = (target.feet[0], target.feet[1] + .9, target.feet[2])
        yaw, pitch = fixtures.aim(fight._eye(owner), point)
        spray = {owner: (yaw, pitch, 1)}
        for _ in range(90):
            run.step(spray)
        self.assertFalse(fight.frozen(bot))
        npc.engage(run.match, bot, owner, action="reposition")
        for _ in range(8):
            run.step(spray)
            if fight.frozen(bot):
                break
        self.assertTrue(fight.frozen(bot))
        run.step({})
        self.assertNotIn(bot.entity, fight.sniper_repositions)
        self.assertFalse(sniper_reposition.move_state(bot).active)
        stopped = bot.position
        for _ in range(120):
            run.step({})
        self.assertFalse(fight.frozen(bot))
        self.assertEqual(bot.position, stopped)

    def test_actual_spray_freeze_interrupts_both_npc_melees_without_replay(self):
        for definition in self.definitions.values():
            if definition.body not in npc_melee.SPECS:
                continue
            with self.subTest(npc=definition.name):
                run, owner, bot, target = self.scene(definition, 1.4)
                fight = run.match.combat
                owner_target = next(t for t in fight._targets() if t.owner is owner)
                vitals = fight.vitals_of(owner_target)
                point = (target.feet[0], target.feet[1] + .9, target.feet[2])
                yaw, pitch = fixtures.aim(fight._eye(owner), point)
                spray = {owner: (yaw, pitch, 1)}
                for _ in range(81):
                    run.step(spray)
                self.assertFalse(fight.frozen(bot))
                npc.engage(run.match, bot, owner, action="melee")
                for _ in range(4):
                    run.step(spray)
                instance = npc_melee.instance_of(bot)
                ability_node = instance.graph.node(npc_melee.SPECS[bot.body][1])
                ability = instance.states[ability_node.state]
                self.assertTrue(ability.flags & ability.RUNNING)
                for _ in range(15):
                    run.step(spray)
                    if fight.frozen(bot):
                        break
                self.assertTrue(fight.frozen(bot))
                run.step({})
                self.assertFalse(ability.flags & ability.RUNNING)
                self.assertIsNone(bot.pending_ability)
                self.assertNotIn(npc_melee.BUTTON, instance.component.held)
                for _ in range(135):
                    run.step({})
                self.assertFalse(fight.frozen(bot))
                self.assertEqual(vitals.total, 250.)
                self.assertFalse(ability.flags & ability.RUNNING)

    def test_actual_spray_freeze_interrupts_heavy_charge_and_retires_input(self):
        definition = next(d for d in self.definitions.values() if d.name == 'Heavy Assault')
        run, owner, bot, target = self.scene(definition, 5.)
        fight = run.match.combat
        instance = heavy_charge.charge(bot)
        yaw, pitch = fixtures.aim(fight._eye(owner), (target.feet[0], target.feet[1] + 0.9, target.feet[2]))
        spray = {owner: (yaw, pitch, 1)}
        for _ in range(65):
            run.step(spray)
        self.assertFalse(fight.frozen(bot))
        npc.engage(run.match, bot, owner, action="charge")
        run.step(spray)
        self.assertEqual(heavy_charge.phase(instance), 1)
        for _ in range(45):
            run.step(spray)
            if fight.frozen(bot):
                break
        self.assertTrue(fight.frozen(bot))
        run.step({})
        self.assertEqual(heavy_charge.phase(instance), 0)
        self.assertGreater(instance.vars[61].value(), 0.)
        self.assertNotIn(heavy_charge.BUTTON, instance.component.held)
        self.assertIsNone(bot.attack_target)
        with self.assertRaises(ValueError):
            npc.engage(run.match, bot, owner, action="charge")
        for _ in range(120):
            run.step({})
        self.assertFalse(fight.frozen(bot))
        self.assertEqual(heavy_charge.phase(instance), 0)

    def test_teammate_blizzard_freeze_releases_heavy_charge_pin(self):
        definition = next(d for d in self.definitions.values() if d.name == "Heavy Assault")
        run, owner, bot, _target = self.scene(definition, 5.)
        fight = run.match.combat
        ally = run.players[1]
        self.assertTrue(run.match.switch_hero(ally, heroes()[mei.HERO]))
        run.match.game_map = replace(run.match.game_map, free_for_all=False)
        owner.team, ally.team, bot.team = 0, 0, 1
        x, y, z = owner.mover.position
        fight.walls.place_player(ally, (x + 2., y, z - 4.))
        for _ in range(10):
            run.step({})
        blizzard = blizzard_fixtures.BlizzardTests()
        blizzard.launch(run, ally)
        area = blizzard.landed(run)
        while run.frame * 16 < area.starts + 1100:
            run.step({})
        self.assertFalse(fight.frozen(bot))
        npc.engage(run.match, bot, owner, action="charge")
        for _ in range(100):
            run.step({})
            if fight.pins.get(bot.entity) == owner.body or fight.frozen(bot):
                break
        self.assertEqual(fight.pins.get(bot.entity), owner.body)
        self.assertFalse(fight.frozen(bot))
        instance = heavy_charge.charge(bot)
        self.assertTrue(heavy_charge.driving(instance))
        for _ in range(100):
            run.step({})
            if fight.frozen(bot):
                break
            self.assertEqual(fight.pins.get(bot.entity), owner.body)
        self.assertTrue(fight.frozen(bot))
        run.step({})
        self.assertFalse(fight.pins)
        self.assertFalse(fight.heavy_charge_pins)
        self.assertEqual(heavy_charge.phase(instance), 0)
        self.assertGreater(instance.vars[61].value(), 0.)
        blizzard.finish(run, area)
        for _ in range(150):
            run.step({})
        self.assertFalse(fight.frozen(bot))
        self.assertFalse(fight.pins)
        self.assertEqual(heavy_charge.phase(instance), 0)

    def test_trooper_lethal_icicle_retires_pending_reload(self):
        definition = next(d for d in self.definitions.values() if d.name == 'Trooper')
        run, owner, bot, target = self.scene(definition)
        fight = run.match.combat
        owner_target = next(t for t in fight._targets() if t.owner is owner)
        owner_vitals = fight.vitals_of(owner_target)
        npc.engage(run.match, bot, owner)
        for _ in range(10):
            run.step({})
        component = bot.body_script.component
        weapon = next(i for i in component.instances.values() if i.graph.index == 0x102E)
        self.assertEqual(weapon.vars[53].value(), 30)
        weapon.write((expr.INSTANCE, 53), 1)  # Explicit last-round fixture.
        reload_wait = weapon.state(18)
        for _ in range(120):
            run.step({})
            if reload_wait.active:
                break
        self.assertTrue(reload_wait.active)
        self.assertEqual(weapon.vars[53].value(), 0)
        self.assertLess(owner_vitals.total, 250.)
        deadline = reload_wait.start + reload_wait.ms
        fight.vitals_of(target).now.update({
            combat.HEALTH_POOL: 60.,
            combat.ARMOUR_POOL: 0.,
            combat.SHIELDS_POOL: 0.,
        })
        fixtures.MeiCombatTests().fire(run, owner, target, 2, .9, frames=35)
        self.assertLess(run.frame * 16, deadline)
        self.assertTrue(bot.dead)
        self.assertTrue(owner.client.death_events)
        self.assertFalse(reload_wait.active)
        settled_health = owner_vitals.total
        for _ in range(120):
            run.step({})
        self.assertGreater(run.frame * 16, deadline)
        self.assertIn(bot, run.match.bots)
        self.assertEqual(weapon.vars[53].value(), 0)
        self.assertEqual(owner_vitals.total, settled_health)

    def select_sniper_alternate(self, run, component):
        self.assertEqual(component.entity_var(31).value(), 1)
        component.edges([(16, True)])
        run.step({})
        component.edges([(16, False)])
        for _ in range(30):
            run.step({})
        self.assertEqual(component.entity_var(31).value(), 2)

    def test_sniper_alternate_weapon_switch_fires_and_damages_mei(self):
        definition = next(d for d in self.definitions.values() if d.name == 'Sniper')
        run, owner, bot, _target = self.scene(definition)
        bot.firing = True
        component = bot.body_script.component
        for _ in range(20):
            run.step({})
        self.select_sniper_alternate(run, component)
        primary = next(i for i in component.instances.values() if i.graph.index == 0x1122)
        alternate = next(i for i in component.instances.values() if i.graph.index == 0x1134)
        npc.engage(run.match, bot, owner)
        run.step({})
        component.edges([(component.world.ai_weapon_button(0), False)])
        self.assertEqual(alternate.state(4).reported, 1)
        self.assertEqual(primary.state(4).counter, 0)
        for _ in range(30):
            run.step({})
        fight = run.match.combat
        target = next(t for t in fight._targets() if t.owner is owner)
        self.assertEqual(fight.vitals_of(target).total, 250. - alternate.vars[476].value())
        self.assertLess(fight.vitals_of(target).total, 250.)
        self.assertEqual(alternate.state(4).counter, 1)

    def test_sniper_and_heavy_authored_refill_gates_and_death_abort(self):
        cases = (
            ('Sniper', 0x1122, 15, 20, 910),
            ('Sniper', 0x1134, 14, 20, 910),
            ('Heavy Assault', 0x114F, 15, 150, 1300),
        )
        for name, graph_id, wait_index, capacity, duration in cases:
            for lethal in (False, True):
                with self.subTest(npc=name, graph=hex(graph_id), lethal=lethal):
                    definition = next(d for d in self.definitions.values() if d.name == name)
                    run, owner, bot, target = self.scene(definition)
                    bot.firing = True
                    component = bot.body_script.component
                    for _ in range(20):
                        run.step({})
                    if graph_id == 0x1134:
                        self.select_sniper_alternate(run, component)
                    weapon = next(i for i in component.instances.values() if i.graph.index == graph_id)
                    ammo, reload_wait = weapon.vars[53], weapon.state(wait_index)
                    self.assertEqual(ammo.value(), capacity)
                    self.assertFalse(reload_wait.active)
                    weapon.write((expr.INSTANCE, 53), 0)
                    if name == 'Heavy Assault':
                        run.step({})
                        self.assertFalse(reload_wait.active)
                        weapon.write((expr.INSTANCE, 51), True)  # Explicit reload-request fixture.
                    run.step({})
                    self.assertTrue(reload_wait.active)
                    self.assertEqual(reload_wait.ms, duration)
                    deadline = reload_wait.start + duration
                    if lethal:
                        run.match.combat.vitals_of(target).now.update({
                            combat.HEALTH_POOL: 60.,
                            combat.ARMOUR_POOL: 0.,
                            combat.SHIELDS_POOL: 0.,
                        })
                        fixtures.MeiCombatTests().fire(run, owner, target, 2, .9, frames=35)
                        self.assertTrue(bot.dead)
                        self.assertTrue(owner.client.death_events)
                        self.assertLess(run.frame * 16, deadline)
                        self.assertFalse(reload_wait.active)
                    frame_ms = component.frame_ms
                    while (run.frame + 2) * frame_ms - 1 < deadline:
                        run.step({})
                        self.assertEqual(ammo.value(), 0)
                    run.step({})
                    self.assertGreaterEqual((run.frame + 1) * frame_ms - 1, deadline)
                    self.assertEqual(ammo.value(), 0 if lethal else capacity)

    def test_manual_primary_stop_clears_pending_and_committed_npc_bursts(self):
        cases = (
            ('Trooper', 0x102E, 33, 23, 20, 20.),
            ('Heavy Assault', 0x114F, 17, 25, 26, 5.),
        )
        for name, graph_id, windup_index, volley_index, wait_index, damage in cases:
            definition = next(row for row in self.definitions.values() if row.name == name)
            for phase, idle in (('windup', 120), ('volley', 0), ('volley', 120), ('volley', 650)):
                with self.subTest(npc=name, phase=phase, stopped_ticks=idle):
                    run, owner, bot, _ = self.scene(definition)
                    fight = run.match.combat
                    target = next(target for target in fight._targets() if target.owner is owner)
                    vitals = fight.vitals_of(target)
                    component = bot.body_script.component
                    weapon = next(i for i in component.instances.values() if i.graph.index == graph_id)
                    windup, volley, wait = (weapon.state(i) for i in (windup_index, volley_index, wait_index))
                    npc.engage(run.match, bot, owner)
                    for _ in range(150):
                        run.step({})
                        admitted = windup.active if phase == 'windup' else bool(volley.reported)
                        if admitted:
                            break
                    self.assertTrue(windup.active if phase == 'windup' else volley.active and wait.active)
                    health, counter, reports = vitals.total, volley.counter, volley.reported

                    npc.engage(run.match, bot, None)
                    npc.engage(run.match, bot, None)
                    self.assertFalse(windup.active or volley.active or wait.active)
                    self.assertNotIn(component.world.ai_weapon_button(0), component.held)
                    for _ in range(idle):
                        run.step({})
                    self.assertEqual(
                        (vitals.total, volley.counter, volley.reported), (health, counter, reports),
                    )

                    reengaged_at = run.match.tick
                    npc.engage(run.match, bot, owner)
                    run.step({})
                    self.assertEqual(vitals.total, health)
                    for _ in range(150):
                        run.step({})
                        if vitals.total != health:
                            break
                    self.assertEqual(vitals.total, health - damage)
                    self.assertEqual((volley.counter, volley.reported), (counter + 1, 1))
                    self.assertGreater(volley.start // component.frame_ms, reengaged_at)

    def test_heavy_release_preserves_admitted_burst_until_authored_timeout(self):
        definition = next(d for d in self.definitions.values() if d.name == 'Heavy Assault')
        run, owner, bot, _ = self.scene(definition)
        bot.firing = True
        component = bot.body_script.component
        button = component.world.ai_weapon_button(0)
        component.edges([(button, True)])
        for _ in range(120):
            run.step({})
        target = next(t for t in run.match.combat._targets() if t.owner is owner)
        self.assertLess(run.match.combat.vitals_of(target).total, 250.)
        weapon = next(i for i in component.instances.values() if i.graph.index == 0x114F)
        volley, timeout = weapon.state(25), weapon.state(26)
        self.assertEqual(weapon.vars[13657].value(), 10.)
        self.assertEqual(timeout.ms, 10000)
        self.assertTrue(volley.active)
        released_at = volley.reported
        component.edges([(button, False)])
        bot.yaw = 0  # Keep Mei alive while measuring the outgoing burst.
        remaining_frames = (timeout.start + timeout.ms - component.now - 1) // component.frame_ms
        for _ in range(remaining_frames):
            run.step({})
        self.assertTrue(volley.active)
        self.assertGreater(volley.reported, released_at)
        for _ in range(80):
            run.step({})
        self.assertFalse(timeout.active)
        self.assertFalse(volley.active)
        self.assertEqual(volley.counter, 1)

    def test_heavy_authored_volley_aborts_when_resolved_entity_query_changes(self):
        definition = next(d for d in self.definitions.values() if d.name == 'Heavy Assault')
        run, owner, bot, _ = self.scene(definition)
        bot.firing = True
        component = bot.body_script.component
        native = component.world.native
        controller = next(player.body for player in run.players if player is not owner)
        context_world = scene.SceneWorld()
        for entity in (bot.entity, owner.body, controller):
            context_world.bind(entity)
        context_world.set_possessions(possessors=(controller,), primary={bot.entity: controller})
        records = {}
        subscriptions = set()
        context_world.set_entity_query_context(bot.entity, entity_queries.Context(
            bot.entity, True, True, True,
            lookup_linked=lambda entity, selector: records.get((entity, selector)),
            watch_environment=lambda state, key: subscriptions.add((state, key)),
        ))

        def world_native(cfg, instance):
            if cfg.get('$') == 'STU_DB742151' and instance.graph.index == 0x114F:
                return context_world.native(cfg, instance)
            return native(cfg, instance)

        with (
            patch.object(component.world, 'native', side_effect=world_native),
            patch.object(component.world, 'entity_query_context',
                         side_effect=context_world.entity_query_context),
        ):
            button = component.world.ai_weapon_button(0)
            component.edges([(button, True)])
            for _ in range(120):
                run.step({})
            fight = run.match.combat
            target = next(t for t in fight._targets() if t.owner is owner)
            vitals = fight.vitals_of(target)
            self.assertLess(vitals.total, 250.)
            weapon = next(i for i in component.instances.values() if i.graph.index == 0x114F)
            volley, timeout, watch = (weapon.state(n) for n in (25, 26, 36))
            self.assertTrue(volley.active and timeout.active and watch.active)
            self.assertEqual(watch.seen, expr.Entity(0))
            self.assertIn((watch, 0x20), subscriptions)
            component.edges([(button, False)])
            reports = volley.reported
            records[(controller, 1)] = owner.body
            for _ in range(12):
                run.step({})
            self.assertTrue(volley.active)
            self.assertGreater(volley.reported, reports)
            self.assertLess(component.now, timeout.start + timeout.ms)

            ammo = weapon.vars[53]
            weapon.write((expr.INSTANCE, 53), 0)  # The primary volley has no ammo output.
            reload_wait = weapon.state(15)
            self.assertFalse(reload_wait.active)
            records[(controller, 0)] = owner.body
            health = vitals.total
            reports = volley.reported
            run.step({})
            self.assertFalse(volley.active)
            self.assertFalse(timeout.active)
            self.assertFalse(watch.active)
            self.assertEqual(weapon.vars[2172].value(), expr.Entity(owner.body))
            self.assertTrue(weapon.vars[51].value())
            self.assertFalse(reload_wait.active)
            for _ in range(125):
                run.step({})
                self.assertEqual(ammo.value(), 0)
                if reload_wait.active:
                    break
            self.assertTrue(reload_wait.active)
            self.assertEqual(reload_wait.ms, 1300)
            deadline = reload_wait.start + reload_wait.ms
            while component.now + component.frame_ms < deadline:
                run.step({})
                self.assertEqual(ammo.value(), 0)
            run.step({})
            self.assertEqual(ammo.value(), 150)
            self.assertFalse(reload_wait.active)
            self.assertEqual(volley.counter, 1)
            self.assertEqual(volley.reported, reports)
            self.assertEqual(vitals.total, health)

    def test_heavy_admitted_burst_is_suppressed_by_freeze_and_ends_on_death(self):
        definition = next(d for d in self.definitions.values() if d.name == 'Heavy Assault')
        run, owner, bot, target = self.scene(definition)
        fight = run.match.combat
        bot.firing = True
        component = bot.body_script.component
        component.edges([(component.world.ai_weapon_button(0), True)])
        for _ in range(120):
            run.step({})
        owner_target = next(t for t in fight._targets() if t.owner is owner)
        owner_vitals = fight.vitals_of(owner_target)
        self.assertLess(owner_vitals.total, 250.)
        weapon = next(i for i in component.instances.values() if i.graph.index == 0x114F)
        volley, timeout = weapon.state(25), weapon.state(26)
        self.assertTrue(volley.active)
        bot.yaw = 0  # Give Mei time to build frost with her spray.
        frost_fixtures.FrostTests().freeze(run, owner, target)
        bot.yaw = -32768
        frozen_health = owner_vitals.total
        frozen_reports = volley.reported
        for _ in range(40):
            run.step({})
        self.assertTrue(fight.frozen(bot))
        self.assertEqual(owner_vitals.total, frozen_health)
        self.assertGreater(volley.reported, frozen_reports)
        self.assertTrue(timeout.active)
        for _ in range(100):
            if owner_vitals.total < frozen_health:
                break
            run.step({})
        self.assertFalse(fight.frozen(bot))
        self.assertLess(owner_vitals.total, frozen_health)
        self.assertEqual(volley.counter, 1)

        self.assertTrue(timeout.active)
        self.assertTrue(volley.active)
        fight.vitals_of(target).now.update({
            combat.HEALTH_POOL: 60.,
            combat.ARMOUR_POOL: 0.,
            combat.SHIELDS_POOL: 0.,
        })
        bot.yaw = 0  # Keep Mei alive until her lethal icicle lands.
        fixtures.MeiCombatTests().fire(run, owner, target, 2, 0.9)
        self.assertTrue(bot.dead)
        self.assertTrue(owner.client.death_events)
        bot.yaw = -32768
        settled_health = owner_vitals.total
        settled_reports = volley.reported
        for _ in range(80):
            run.step({})
        self.assertEqual(owner_vitals.total, settled_health)
        self.assertEqual(volley.reported, settled_reports)

    def test_trooper_and_sniper_released_input_stops_later_volleys_and_damage(self):
        # Heavy has a separate timer-bounded burst covered above.
        for definition in self.definitions.values():
            if definition.name not in ('Trooper', 'Sniper'):
                continue
            with self.subTest(npc=definition.name):
                run, owner, bot, _target = self.scene(definition)
                component = bot.body_script.component
                bot.firing = True
                primary = component.world.ai_weapon_button(0)
                component.edges([(primary, True)])
                for _ in range(120 if definition.name == 'Trooper' else 240):
                    run.step({})
                component.edges([(primary, False)])
                for _ in range(80):
                    run.step({})  # Allow a released burst/in-flight projectile to finish.
                target = next(t for t in run.match.combat._targets() if t.owner is owner)
                settled = run.match.combat.vitals_of(target).total
                volleys = [s for i in component.instances.values() for s in i.states.values()
                           if s.node.cls == 'STUStatescriptStateWeaponVolley' and s.counter]
                counters = [state.counter for state in volleys]
                for _ in range(80):
                    run.step({})
                self.assertEqual(run.match.combat.vitals_of(target).total, settled)
                self.assertEqual([state.counter for state in volleys], counters)

    def shield_caster(self, run, owner, *, same_team=False):
        caster = run.players[1]
        self.assertTrue(run.match.switch_hero(caster, heroes()[zarya_shields.HERO]))
        run.match.game_map = replace(run.match.game_map, free_for_all=False)
        caster.team = owner.team if same_team else 1
        p = owner.mover.position
        run.match.combat.walls.place_player(caster, (p[0] + 3., p[1], p[2] + 3.))
        for _ in range(30):
            run.step({})
        return caster

    def test_projected_shield_blocks_actual_icicle_and_spray_for_each_npc(self):
        for definition in self.definitions.values():
            with self.subTest(npc=definition.name):
                run, owner, bot, target = self.scene(definition)
                caster = self.shield_caster(run, owner)
                before = run.match.combat.vitals_of(target).total
                bubble = shield_fixtures.ShieldTests().cast(run, caster, bot)
                fixtures.MeiCombatTests().fire(run, owner, target, 2, 0.9)
                self.assertEqual(bubble.health, 125.)
                fixtures.MeiCombatTests().fire(run, owner, target, 1, 0.9)
                self.assertLess(bubble.health, 125.)
                self.assertEqual(run.match.combat.vitals_of(target).total, before)
                self.assertIsNone(run.match.combat.frost(bot))
                self.assertEqual(npc.remove_spawned(run.match), (bot.entity,))
                self.assertFalse(run.match.combat.shields.bubbles)
                self.assertFalse(run.match.combat.shields.flags)

    def test_projected_shield_cleanses_each_npcs_actual_frost_and_resource_stun_fixture(self):
        for definition in self.definitions.values():
            with self.subTest(npc=definition.name):
                run, owner, bot, target = self.scene(definition)
                caster = self.shield_caster(run, owner)
                frost_fixtures.FrostTests().one_spray(run, owner, target)
                self.assertIsNotNone(run.match.combat.frost(bot))
                # Isolate the25D cleanse consumer from NPC Flashbang aiming/AI.
                run.match.combat._hit_effect(target, combat.FLASHBANG_EFFECT)
                self.assertTrue(run.match.combat.is_stunned(bot))
                shield_fixtures.ShieldTests().cast(run, caster, bot)
                self.assertFalse(run.match.combat.is_stunned(bot))
                self.assertIsNone(run.match.combat.frost(bot))
                self.assertTrue(bot.body_script.component.entity_var(8440).value())

    def test_three_actual_npc_weapons_hit_meis_projected_shield_instead_of_health(self):
        cases = {'Trooper': (120, 120.), 'Sniper': (240, 100.), 'Heavy Assault': (120, 110.)}
        for definition in self.definitions.values():
            if definition.name not in cases:
                continue
            with self.subTest(npc=definition.name):
                run, owner, bot, _target = self.scene(definition)
                caster = self.shield_caster(run, owner, same_team=True)
                component = bot.body_script.component
                bot.firing = True
                component.edges([(component.world.ai_weapon_button(0), True)])
                before = 110 if definition.name == 'Sniper' else 0
                for _ in range(before):
                    run.step({})
                bubble = shield_fixtures.ShieldTests().cast(run, caster, owner)
                frames, damage = cases[definition.name]
                for _ in range(frames - before - 2):
                    run.step({})
                own = next(t for t in run.match.combat._targets() if t.owner is owner)
                self.assertEqual(run.match.combat.vitals_of(own).total, 250.)
                self.assertEqual(bubble.health, 200. - damage)


if __name__ == "__main__":
    unittest.main()
