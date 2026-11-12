"""Real five-archetype recipient/source health paths with external authored data.

Use a prepared runtime with the NPC catalog, or set ORPHEUS_MOIRA_PVE_DATA and
ORPHEUS_MOIRA_PVE_MOVERS to supplemental resources. The tests include stationary autonomous acquisition.
Full native NPC navigation, specials and client presentation remain outside this fixture.
"""

import json
import os
import unittest
from dataclasses import fields, replace
from pathlib import Path
from unittest.mock import patch

import test_game_moira as moira_tests
import test_game_moira_absorption as absorption_tests
import test_game_moira_barriers as barrier_tests
import test_game_moira_beam as beam_tests
import test_game_moira_biotic as biotic_tests
import test_game_moira_control as control_tests
import test_game_moira_deflect as deflect_tests
import test_game_moira_graviton as graviton_tests
import test_game_moira_matrix as matrix_tests
import test_game_moira_orbs as orb_tests
import test_game_moira_photon as photon_tests
from test_game_combat import FakeClient, Run, aim, in_world

from ow174.game import combat, deflection, graviton, heavy_charge, npc, npc_control, photon_barrier, stats
from ow174.game import mover as mover_module
from ow174.game.collision import World
from ow174.game.commands import Command
from ow174.game.content import find_hero, heroes
from ow174.game.script import expr, graph, nodes
from ow174.game.script.driver import ULT_CHARGE, ULT_COST, ULT_GAIN_ENABLED
from ow174.game.world import HEALTH_POOL, OP_CREATE, OP_DESTROY

RUNTIME_CATALOG = graph.GRAPHS_PATH.with_name("pve_npcs_174.json")
EXTERNAL = os.environ.get("ORPHEUS_MOIRA_PVE_DATA") or (
    RUNTIME_CATALOG.parent if RUNTIME_CATALOG.is_file() else None
)
MOVERS = os.environ.get("ORPHEUS_MOIRA_PVE_MOVERS") or (
    mover_module.MOVERS_PATH
    if EXTERNAL is not None and Path(EXTERNAL).resolve() == RUNTIME_CATALOG.parent.resolve()
    and mover_module.MOVERS_PATH.is_file() else None
)


class NpcEventClient(FakeClient):
    """Declared in-memory event collector, not encrypted transport acceptance."""

    def __init__(self, player):
        super().__init__(player)
        self.bot_deaths = []
        self.bot_death_updates = []

    def queue_bot_death(self, entity, event):
        self.bot_deaths.append((entity, event))

    def queue_bot_death_update(self, entity, event):
        self.bot_death_updates.append((entity, event))

    def bot_stream(self, entity):
        # This health/event fixture has no encoded NPC observer stream. Return
        # the normal absent-stream value; projectile presentation stays OPEN.
        return None


@unittest.skipUnless(EXTERNAL and MOVERS, "external five-NPC graphs/catalog and mover supplement required")
class MoiraFiveNpcTests(unittest.TestCase):
    start_ult = moira_tests.MoiraOpenAcceptanceTests.start_ult
    launch_orb = orb_tests.MoiraOrbTests.launch
    step = staticmethod(deflect_tests.MoiraDeflectTests.step)
    launch_deflect_orb = deflect_tests.MoiraDeflectTests.launch
    reflect_orb = deflect_tests.MoiraDeflectTests.reflect

    def supported_weapon_rows(self):
        # Explicit-button release evidence for four declared manual bodies.
        # Assassin has a separate pin/ModifyHealth path, not a Volley20 outlet.
        return [row for row in self.rows if row['name'] in ('Trooper', 'Enforcer', 'Sniper', 'Heavy Assault')]

    @staticmethod
    def arm_weapon(bot):
        # Fixed pose and explicit NPC input, not autonomous target acquisition.
        bot.yaw = -32768
        bot.firing = True
        bot.body_script.component.edges([(20, True)])
        bot.body_script.component.run_now()

    def controlled_npc(self, row, *, ally=False, armed=False):
        run, source, target, health, _direction = self.fight(row, ally=ally)
        match = run.match
        x, y, z = source.mover.position
        target.owner.position = (x, y, z + 7)
        caster = match.add_player(2, 'Control', control_tests.MCCREE, 1, False)
        in_world(match, caster)
        caster.client = NpcEventClient(caster)
        run.players.append(caster)
        match.switch_hero(caster, heroes()[control_tests.MCCREE])
        caster.team = 1 - target.team
        caster.mover.position = [x + 3, y, z + 7]
        direction = aim(match.combat._eye(caster), (x, y + target.height / 2, z + 7))
        for _ in range(20):
            run.step({})
        if armed:
            self.arm_weapon(target.owner)
        run.step({caster: (*direction, 16)})
        for _ in range(50):
            if match.combat.is_stunned(target.owner):
                return run, source, target, health
            run.step({})
        self.fail('real Flashbang applied no NPC control')

    @classmethod
    def setUpClass(cls):
        directory = Path(EXTERNAL)
        cls.catalog = npc.load_catalog(directory / 'pve_npcs_174.json')
        cls.rows = json.loads((directory / "pve_npcs_174.json").read_text(encoding="utf-8"))["definitions"]
        if len(cls.rows) != 5:
            raise ValueError("five actual NPC definitions required")
        if ((directory / "game_graphs_174.json.gz").resolve() == graph.GRAPHS_PATH.resolve()
                and Path(MOVERS).resolve() == mover_module.MOVERS_PATH.resolve()):
            for row in cls.rows:
                body = int(row["body"], 16)
                if graph.entity_body(body) is None or mover_module.defined_mover_data(body) is None:
                    raise ValueError(f"missing runtime body or mover for {row['name']}")
            return
        # Explicit fixture-only supplemental asset input. Preserve canonical
        # hero records/identity and restore the original table after this class.
        supplemental = json.loads(Path(MOVERS).read_text(encoding="utf-8"))
        names = {field.name for field in fields(mover_module.MoverData)}
        table = dict(mover_module._movers())
        for row in cls.rows:
            entry = supplemental[row["body"]]
            table[int(row["body"], 16)] = mover_module.MoverData(
                **{key: value for key, value in entry.items() if key in names})
        cls.edit = patch.object(graph, "GRAPHS_PATH", directory / "game_graphs_174.json.gz")
        cls.edit.start()
        cls.addClassCleanup(cls.edit.stop)
        cls.addClassCleanup(cls.clear_graphs)
        cls.clear_graphs()
        cls.mover_edit = patch.object(mover_module, "_mover_table", table)
        cls.mover_edit.start()
        cls.addClassCleanup(cls.mover_edit.stop)

    @staticmethod
    def clear_graphs():
        graph.graph.cache_clear()
        graph._records.cache_clear()
        graph._data.cache_clear()
        graph.mode_variables.cache_clear()

    def fight(self, row, *, ally=False):
        run = Run()
        match = run.match
        match.bots.clear()
        match._pending_bot_scripts.clear()
        source = run.players[0]
        source.client = NpcEventClient(source)
        match.switch_hero(source, heroes()[moira_tests.MOIRA])
        x, y, z = source.mover.position
        bot = npc.spawn(match, self.catalog[row['key']], (x, y, z + 3),
                        team=source.team if ally else 1 - source.team)
        self.assertIsNotNone(bot.body_script)
        vitals = match.combat.vitals[bot.entity]
        for _ in range(20):
            run.step({})
        target = next(t for t in match.combat._targets() if t.owner is bot)
        direction = aim(match.combat._eye(source), (x, y + target.height / 2, z + 3))
        return run, source, target, vitals, direction

    def test_grasp_damage_and_spray_heal_actual_five_bodies(self):
        for row in self.rows:
            for ally in (False, True):
                with self.subTest(npc=row["name"], ally=ally):
                    run, source, _target, vitals, direction = self.fight(row, ally=ally)
                    vitals.damage(100)
                    before = vitals.total
                    for _ in range(30):
                        run.step({source: (*direction, 1 if ally else 2)})
                    if ally:
                        self.assertGreater(vitals.total, before)
                        self.assertLessEqual(vitals.total, sum(vitals.most.values()))
                    else:
                        self.assertAlmostEqual(before - vitals.total, 24, places=4)

    def test_same_encounter_grasp_fade_and_return_damage_from_each_manual_npc(self):
        from ow174.game import moira

        for row in self.rows:
            with self.subTest(npc=row["name"]):
                run, source, target, enemy_health, direction = self.fight(row)
                fight = run.match.combat
                bot = target.owner
                own = next(t for t in fight._targets() if t.owner is source)
                own_health = fight.vitals_of(own)
                if row["name"] == "Assassin":
                    x, y, z = source.mover.position
                    bot.position = (x, y, z + 2)
                    direction = aim(fight._eye(source), (x, y + target.height / 2, z + 2))
                source.body_script.component.set(12918, 0)

                before = enemy_health.total
                for _ in range(20):
                    run.step({source: (*direction, 2)})
                    if enemy_health.total < before:
                        break
                self.assertLess(enemy_health.total, before)
                self.assertEqual(own_health.total, 200)
                run.step({source: (*direction, 0)})
                run.step({source: (*direction, 8)})
                self.assertTrue(moira.fading(source))
                self.assertEqual(npc_control.start(run.match, source), bot.entity)

                faded_frames = 0
                with patch.object(fight, "_damage", wraps=fight._damage) as damage:
                    for _ in range(100):
                        if not moira.fading(source):
                            break
                        self.assertEqual(own_health.total, 200)
                        self.assertNotIn(source.body, fight.pins.values())
                        faded_frames += 1
                        run.step({source: (*direction, 0)})
                    self.assertGreater(faded_frames, 0)
                    self.assertFalse(moira.fading(source))
                    damage.reset_mock()
                    for _ in range(330):
                        if own_health.total < 200:
                            break
                        run.step({source: (*direction, 0)})

                self.assertLess(own_health.total, 200)
                self.assertTrue(any(call.args[0] is bot and call.args[1].entity == source.body
                                    for call in damage.call_args_list))
                self.assertEqual(bot.attack_target, source.entity)
                self.assertIsNone(bot.body_script.component.fault)
                if row["name"] == "Assassin":
                    self.assertEqual(fight.pins.get(bot.entity), source.body)
                self.assertEqual(npc_control.stop(run.match), 1)
                self.assertIsNone(bot.attack_target)
                self.assertNotIn(source.body, fight.pins.values())

    def test_all_five_npcs_acquire_and_return_damage_after_moira_fade(self):
        from ow174.game import moira

        for row in self.rows:
            with self.subTest(npc=row['name']):
                run, source, target, enemy_health, direction = self.fight(row)
                fight = run.match.combat
                bot = target.owner
                own = next(t for t in fight._targets() if t.owner is source)
                own_health = fight.vitals_of(own)
                if row['name'] == 'Assassin':
                    x, y, z = source.mover.position
                    bot.position = (x, y, z + 2)
                    direction = aim(fight._eye(source), (x, y + target.height / 2, z + 2))
                source.body_script.component.set(12918, 0)

                before = enemy_health.total
                for _ in range(20):
                    run.step({source: (*direction, 2)})
                    if enemy_health.total < before:
                        break
                self.assertLess(enemy_health.total, before)
                run.step({source: (*direction, 0)})
                run.step({source: (*direction, 8)})
                self.assertTrue(moira.fading(source))
                self.assertIsNone(bot.attack_target)
                bot.spawner['autonomous_targeting'] = True

                for _ in range(100):
                    if not moira.fading(source):
                        break
                    self.assertEqual(own_health.total, 200)
                    run.step({source: (*direction, 0)})
                self.assertFalse(moira.fading(source))

                for _ in range(330):
                    if own_health.total < 200:
                        break
                    run.step({source: (*direction, 0)})
                self.assertEqual(bot.attack_target, source.entity)
                self.assertLess(own_health.total, 200)
                self.assertIsNone(bot.body_script.component.fault)
                self.assertEqual(npc_control.stop(run.match), 1)
                run.step({source: (*direction, 0)})
                self.assertIsNone(bot.attack_target)

    def test_autonomous_npc_releases_an_ally_and_reacquires_when_hostile(self):
        row = next(row for row in self.rows if row['name'] == 'Trooper')
        run, source, target, _, _ = self.fight(row)
        match = run.match
        match.game_map = replace(match.game_map, free_for_all=False)
        bot = target.owner
        primary = npc_control.button(bot)
        bot.spawner['autonomous_targeting'] = True

        run.step({})
        self.assertEqual(bot.attack_target, source.entity)
        self.assertIn(primary, bot.body_script.component.held)
        enemy_team = source.team
        source.team = bot.team
        run.step({})
        self.assertIsNone(bot.attack_target)
        self.assertNotIn(primary, bot.body_script.component.held)

        source.team = enemy_team
        run.step({})
        self.assertEqual(bot.attack_target, source.entity)
        self.assertIn(primary, bot.body_script.component.held)

    def test_autonomous_npcs_retarget_after_live_cover_and_target_death(self):
        for row in self.rows:
            with self.subTest(npc=row['name']):
                self.check_autonomous_retarget(row)

    def check_autonomous_retarget(self, row):
        run, source, target, _, _ = self.fight(row)
        match, bot = run.match, target.owner
        match.game_map = replace(match.game_map, free_for_all=False)
        fight = match.combat
        x, y, z = source.mover.position

        def player(number, name, hero, team, position):
            actor = match.add_player(number, name, hero.guid, team, False)
            in_world(match, actor)
            actor.client = NpcEventClient(actor)
            run.players.append(actor)
            self.assertTrue(match.switch_hero(actor, hero))
            actor.team = team
            actor.mover.position = list(position)
            actor.mover.body = None
            return actor

        assassin = row['name'] == 'Assassin'
        depth = 2 if assassin else 3
        offset = 2.5 if assassin else 7
        old_entity, team = bot.entity, bot.team
        alternate = player(2, 'Other target', find_hero('soldier: 76'), source.team,
                           (x + offset, y, z + depth))
        caster = player(3, 'Mei', find_hero('mei'), bot.team, (x, y, z - 6))
        wall_aim = aim(fight._eye(caster), (x, y, z + depth / 2))
        for _ in range(25):
            run.step({})

        own_health = fight.vitals_of(next(t for t in fight._targets() if t.owner is source))
        alternate_health = fight.vitals_of(next(t for t in fight._targets() if t.owner is alternate))
        self.assertEqual(npc.remove_spawned(match), (old_entity,))
        self.assertNotIn(old_entity, fight.vitals)
        bot = npc.spawn(match, self.catalog[row['key']], (x, y, z + depth), team=team,
                        autonomous=True)
        self.assertGreater(bot.entity, old_entity)
        self.assertTrue(npc_control.autonomous(bot))
        self.assertTrue(any(update.entity == old_entity and update.op == OP_DESTROY
                            for update in source.client.entities))
        self.assertTrue(any(update.entity == bot.entity and update.op == OP_CREATE
                            for update in source.client.entities))
        primary = npc_control.button(bot)
        run.step({})
        self.assertEqual(bot.attack_target, source.entity)
        self.assertIn(primary, bot.body_script.component.held)

        for button in (16, 0, 1):
            run.step({caster: (*wall_aim, button)})
        self.assertEqual(len(fight.walls.pieces), 5)
        before = own_health.total
        run.step({caster: (*wall_aim, 0)})
        self.assertIsNone(bot.attack_target)
        self.assertNotIn(primary, bot.body_script.component.held)
        run.step({})
        self.assertEqual(bot.attack_target, alternate.entity)

        alternate_health.damage(alternate_health.total - 1)
        fight._sync_body_health(alternate, alternate_health)
        for _ in range(250):
            run.step({})
            if alternate_health.dead:
                break
        self.assertTrue(alternate_health.dead)
        self.assertEqual(alternate_health.killer, bot.entity)
        self.assertEqual(own_health.total, before)
        run.step({})
        self.assertIsNone(bot.attack_target)
        self.assertNotIn(primary, bot.body_script.component.held)

        run.step({caster: (*wall_aim, 16)})
        self.assertFalse(fight.walls.pieces)
        for _ in range(250):
            run.step({})
            if own_health.total < before:
                break
        self.assertEqual(bot.attack_target, source.entity)
        self.assertLess(own_health.total, before)
        self.assertIsNone(bot.body_script.component.fault)


    def test_remove_spawned_autonomous_assassin_releases_active_moira_pin(self):
        row = next(row for row in self.rows if row['name'] == 'Assassin')
        run, source, target, _, _ = self.fight(row)
        match, fight = run.match, run.match.combat
        x, y, z = source.mover.position
        self.assertEqual(npc.remove_spawned(match), (target.entity,))
        bot = npc.spawn(match, self.catalog[row['key']], (x, y, z + 2),
                        team=1 - source.team, autonomous=True)
        health = fight.vitals_of(next(t for t in fight._targets() if t.owner is source))
        for _ in range(250):
            run.step({})
            if fight.pins.get(bot.entity) == source.body:
                break
        self.assertEqual(fight.pins.get(bot.entity), source.body)
        self.assertTrue(fight.is_stunned(source))
        self.assertLess(health.total, 200)

        before = health.total
        self.assertEqual(npc.remove_spawned(match), (bot.entity,))
        self.assertFalse(fight.is_stunned(source))
        self.assertNotIn(bot.entity, fight.pins)
        self.assertNotIn(bot.entity, fight.vitals)
        self.assertTrue(any(update.entity == bot.entity and update.op == OP_DESTROY
                            for update in source.client.entities))
        for _ in range(180):
            run.step({})
        self.assertEqual(health.total, before)

    def test_autonomous_spawn_rejects_missing_weapon_without_publishing_an_entity(self):
        row = next(row for row in self.rows if row['name'] == 'Trooper')
        run, source, _, _, _ = self.fight(row)
        match, fight = run.match, run.match.combat
        definition = replace(self.catalog[row['key']], body=0x0400FFFFFFFFFFFF)
        entity = fight.next_bot
        before = list(match.bots)
        with self.assertRaisesRegex(ValueError, 'body script is unavailable'):
            npc.spawn(match, definition, source.mover.position, team=1, autonomous=True)
        self.assertEqual(match.bots, before)
        self.assertEqual(fight.next_bot, entity)
        self.assertNotIn(entity, fight.vitals)
        self.assertFalse(any(update.entity == entity for update in source.client.entities))



    def project_barrier(self, run, recipient):
        match, fight = run.match, run.match.combat
        match.game_map = replace(match.game_map, free_for_all=False)
        hero = find_hero('zarya')
        caster = match.add_player(2, 'Zarya', hero.guid, recipient.team, False)
        in_world(match, caster)
        caster.client = NpcEventClient(caster)
        run.players.append(caster)
        self.assertTrue(match.switch_hero(caster, hero))
        caster.team = recipient.team
        target = next(t for t in fight._targets() if t.owner is recipient)
        x, y, z = target.feet
        caster.mover.position[:] = (x - 4, y, z)
        caster.mover.body = None
        direction = aim(fight._eye(caster), (x, y + target.height / 2, z))
        run.step({caster: (*direction, 16)})
        bubble = fight.zarya_bubbles.for_target(recipient)
        self.assertIsNotNone(bubble)
        self.assertEqual(bubble.body, target.entity)
        self.assertEqual(bubble.definition & 0xFFFF, 0x3DA)
        self.assertEqual(bubble.health, 200)
        return bubble

    def test_projected_barrier_protects_moira_from_actual_four_npc_weapons(self):
        for row in self.supported_weapon_rows():
            with self.subTest(npc=row['name']):
                run, source, target, _, _ = self.fight(row)
                fight, actor = run.match.combat, target.owner
                health = fight.vitals_of(next(t for t in fight._targets() if t.owner is source))
                before = health.total
                actor.spawner['autonomous_targeting'] = True
                if row['name'] == 'Sniper':
                    # Start the authored windup before the shorter shield phase.
                    for _ in range(50):
                        run.step({})
                    self.assertEqual(health.total, before)
                bubble = self.project_barrier(run, source)
                for _ in range(130):
                    run.step({})
                    self.assertEqual(health.total, before)
                    if bubble.health < 200:
                        break
                self.assertLess(bubble.health, 200)
                self.assertEqual(actor.attack_target, source.entity)
                for _ in range(350):
                    run.step({})
                    if health.total < before:
                        break
                self.assertIsNone(fight.zarya_bubbles.for_target(source))
                self.assertLess(health.total, before)


    def test_actual_npc_shot_hits_projected_barrier_outside_moira_capsule(self):
        row = next(row for row in self.rows if row['name'] == 'Trooper')
        for shielded in (False, True):
            with self.subTest(shielded=shielded):
                run, source, target, _, _ = self.fight(row)
                fight = run.match.combat
                source.mover.position[0] += 1.0
                source.mover.body = None
                bubble = self.project_barrier(run, source) if shielded else None
                own = next(t for t in fight._targets() if t.owner is source)
                health = fight.vitals_of(own)
                before = health.total
                self.arm_weapon(target.owner)
                with patch.object(fight, '_shoot', wraps=fight._shoot) as shots:
                    for _ in range(45):
                        run.step({})
                self.assertGreater(shots.call_count, 0)
                self.assertEqual(health.total, before)
                if bubble is not None:
                    self.assertLess(bubble.health, 200)


    def test_projected_barrier_releases_and_blocks_autonomous_assassin_pin(self):
        row = next(row for row in self.rows if row['name'] == 'Assassin')
        run, source, target, _, _ = self.fight(row)
        fight, actor = run.match.combat, target.owner
        x, y, z = source.mover.position
        actor.position = (x, y, z + 2)
        actor.spawner['autonomous_targeting'] = True
        for _ in range(130):
            run.step({})
            if fight.pins.get(actor.entity) == source.body:
                break
        self.assertEqual(fight.pins.get(actor.entity), source.body)

        bubble = self.project_barrier(run, source)
        self.assertNotIn(actor.entity, fight.pins)
        self.assertFalse(fight.is_stunned(source))
        component = actor.body_script.component
        self.assertIsNone(component.world.manual_target)
        before = fight.vitals[source.body].total
        for _ in range(90):
            run.step({})
            self.assertIs(fight.zarya_bubbles.for_target(source), bubble)
            self.assertNotIn(actor.entity, fight.pins)
            self.assertIsNone(actor.attack_target)
            self.assertEqual(fight.vitals[source.body].total, before)
        self.assertEqual(bubble.health, 200)

        for _ in range(250):
            run.step({})
            if fight.pins.get(actor.entity) == source.body:
                break
        self.assertIsNone(fight.zarya_bubbles.for_target(source))
        self.assertEqual(fight.pins.get(actor.entity), source.body)
        self.assertAlmostEqual(before - fight.vitals[source.body].total, 25)


    def test_projected_barrier_prevents_or_releases_heavy_charge_binding(self):
        row = next(row for row in self.rows if row['name'] == 'Heavy Assault')
        for barrier_first in (False, True):
            with self.subTest(barrier_first=barrier_first):
                run, source, target, _, _ = self.fight(row)
                fight, actor = run.match.combat, target.owner
                x, y, z = source.mover.position
                actor.position = (x, y, z + 5)
                actor.yaw = -32768
                actor.firing = True
                for _ in range(12):
                    run.step({})
                if barrier_first:
                    self.project_barrier(run, source)
                component = actor.body_script.component
                component.edges([(35, True)])
                component.run_now()
                component.edges([(35, False)])
                component.run_now()
                actor.firing = False
                for _ in range(80):
                    run.step({})
                    if barrier_first:
                        self.assertNotIn(actor.entity, fight.heavy_charge_pins)
                    elif actor.entity in fight.heavy_charge_pins:
                        break
                self.assertLess(actor.position[2], z + 5)
                if not barrier_first:
                    self.assertEqual(fight.pins.get(actor.entity), source.body)
                    application = fight.heavy_charge_pins[actor.entity]
                    self.project_barrier(run, source)
                    self.assertNotIn(application.id, application.component.instances)
                self.assertIsNotNone(fight.zarya_bubbles.for_target(source))
                self.assertNotIn(actor.entity, fight.heavy_charge_pins)
                self.assertFalse(fight.is_stunned(source))
                self.assertIsNone(component.fault)

    def test_moira_grasp_spends_projected_barriers_on_all_five_npcs(self):
        for row in self.rows:
            with self.subTest(npc=row['name']):
                run, source, target, health, direction = self.fight(row)
                fight = run.match.combat
                bubble = self.project_barrier(run, target.owner)
                before = health.total
                for _ in range(20):
                    run.step({source: (*direction, 2)})
                    if bubble.health < 200:
                        break
                self.assertAlmostEqual(200 - bubble.health, 0.8, places=4)
                self.assertEqual(health.total, before)
                self.assertEqual(source.stats.value(stats.HERO_DAMAGE), 0)


    def test_grasp_authored_volume_margin_with_range_and_cover(self):
        for row in self.rows:
            run, source, target, vitals, _direction = self.fight(row)
            fight = run.match.combat
            x, y, z = fight._eye(source)
            direction = aim((x, y, z), (x, y, z + 3))
            feet_y = y - target.height / 2
            margin = target.radius + 0.2
            wall_z = z + 1.5
            cover = World([
                (x - 4, y - 4, wall_z, x + 4, y - 4, wall_z, x + 4, y + 4, wall_z),
                (x - 4, y - 4, wall_z, x + 4, y + 4, wall_z, x - 4, y + 4, wall_z),
            ])
            for offset, depth, collision, expected_damage in (
                (margin, 3, World([]), 0.8),
                (target.radius + 0.7, 3, World([]), 0),
                (0, 19, World([]), 0.8),
                (0, 19.5 + target.radius + 0.1, World([]), 0),
                (margin, 3, cover, 0),
            ):
                with self.subTest(npc=row['name'], offset=offset, depth=depth, covered=collision is cover):
                    target.owner.position = (x + offset, feet_y, z + depth)
                    with patch.object(fight, '_collision', return_value=collision):
                        for _ in range(20):
                            run.step({source: (*direction, 0)})
                        before = vitals.total
                        run.step({source: (*direction, 2)})
                    self.assertAlmostEqual(before - vitals.total, expected_damage, places=4)

    def test_primary_and_secondary_reject_wrong_allegiance_for_five_bodies(self):
        for row in self.rows:
            with self.subTest(npc=row['name'], attack='spray'):
                run, source, target, vitals, direction = self.fight(row)
                fight = run.match.combat
                vitals.damage(100)
                before = vitals.total
                with patch.object(fight, '_shoot', wraps=fight._shoot) as shots:
                    for _ in range(30):
                        run.step({source: (*direction, 1)})
                    for _ in range(30):
                        run.step({})
                self.assertGreater(shots.call_count, 0)
                self.assertEqual(vitals.total, before)
                self.assertNotIn(target.entity, fight.moira_hots)

            fuel_by_team = []
            for ally in (True, False):
                with self.subTest(npc=row['name'], attack='grasp', ally=ally):
                    run, source, _target, vitals, direction = self.fight(row, ally=ally)
                    weapon = next(i for i in source.body_script.component.instances.values()
                                  if i.graph.index == 0x0F76)
                    weapon.write((expr.INSTANCE, 87), 10.0)
                    before = vitals.total
                    run.step({source: (*direction, 2)})
                    self.assertTrue(weapon.states[106].active)
                    fuel_by_team.append(weapon.find_var(expr.INSTANCE, 87).value())
                    if ally:
                        self.assertEqual(vitals.total, before)
                    else:
                        self.assertLess(vitals.total, before)
            self.assertAlmostEqual(fuel_by_team[1] - fuel_by_team[0], 3, places=4)

    def test_grasp_candidate_member_reads_five_current_npc_scripts(self):
        for row in self.rows:
            with self.subTest(npc=row["name"]):
                run, source, target, _, _ = self.fight(row)
                moira = next(instance for instance in source.body_script.component.instances.values()
                             if instance.graph.index == 0xF76)
                component = target.owner.body_script.component
                entity = target.owner.entity
                self.assertIsNone(moira.context().resolved_member(entity, 7982))
                first = component.create(graph.graph(0x73), init=False).state(6)
                second = component.create(graph.graph(0xD39), init=False).state(1)
                first.begin()
                self.assertIs(moira.context().resolved_member(entity, 7982), True)
                second.begin()
                first.end(False)
                self.assertIs(moira.context().resolved_member(entity, 7982), True)
                second.end(False)
                self.assertIsNone(moira.context().resolved_member(entity, 7982))
                first.begin()
                run.match.bots.remove(target.owner)
                self.assertIsNone(moira.context().resolved_member(entity, 7982))

    def test_grasp_authored_target_output_follows_supplied_candidates(self):
        for row in self.rows:
            with self.subTest(npc=row["name"]):
                run, source, target, _, direction = self.fight(row)
                match = run.match
                enemy = match.add_player(2, "Target", moira_tests.MOIRA, target.team, False)
                in_world(match, enemy)
                enemy.client = NpcEventClient(enemy)
                run.players.append(enemy)
                self.assertTrue(match.switch_hero(enemy, heroes()[moira_tests.MOIRA]))
                enemy.team = target.team
                script = source.body_script
                instance = next(item for item in script.component.instances.values()
                                if item.graph.index == 0xF76)
                search = instance.state(106)
                damage_target = instance.state(22).node.field("m_FA3ECC43")
                original = script.world.tracked_targets
                selected = (expr.Entity(target.owner.entity),)

                def track(state):
                    return selected if state is search else original(state)

                with patch.object(script.world, "tracked_targets", side_effect=track):
                    run.step({source: (*direction, 2)})
                    self.assertTrue(search.active)
                    self.assertEqual(expr.to_entity(instance.evaluate(damage_target)), target.owner.entity)
                    self.assertEqual(search.payload()["targets"], ((target.owner.entity, 0),))
                    selected = (expr.Entity(enemy.body),)
                    run.step({source: (*direction, 2)})
                    self.assertEqual(search.payload()["targets"], ((enemy.body, 0),))
                    self.assertEqual(expr.to_entity(instance.evaluate(damage_target)), enemy.body)
                    selected = ()
                    run.step({source: (*direction, 2)})
                    self.assertEqual(search.payload()["targets"], ())
                    self.assertEqual(expr.to_entity(instance.evaluate(damage_target)), 0)
                    selected = (expr.Entity(enemy.body),)
                    run.step({source: (*direction, 2)})
                    self.assertEqual(expr.to_entity(instance.evaluate(damage_target)), enemy.body)
                    run.step({source: (*direction, 0)})
                    self.assertFalse(search.active)
                    self.assertEqual(expr.to_entity(instance.evaluate(damage_target)), 0)

    def test_grasp_ranks_npc_and_player_by_projection_then_distance(self):
        for row in self.rows:
            run, source, npc, npc_health, _ = self.fight(row)
            match = run.match
            fight = match.combat
            enemy = match.add_player(2, 'Enemy', moira_tests.MOIRA, npc.team, False)
            in_world(match, enemy)
            enemy.client = NpcEventClient(enemy)
            run.players.append(enemy)
            self.assertTrue(match.switch_hero(enemy, heroes()[moira_tests.MOIRA]))
            enemy.team = npc.team
            x, y, z = source.mover.position
            enemy.mover.position = [x, y, z + 5]
            enemy.mover.body = None
            eye = fight._eye(source)
            direction = aim(eye, (eye[0], eye[1], eye[2] + 3))
            weapon = next(instance for instance in source.body_script.component.instances.values()
                          if instance.graph.index == 0xF76)
            search = weapon.state(106)
            damage_target = weapon.state(22).node.field("m_FA3ECC43")
            empty = World([])

            def wall(depth):
                return World([
                    (x - 4, y - 4, z + depth, x + 4, y - 4, z + depth, x + 4, y + 4, z + depth),
                    (x - 4, y - 4, z + depth, x + 4, y + 4, z + depth, x - 4, y + 4, z + depth),
                ])

            margin = npc.radius + 0.2
            player_target = next(t for t in fight._targets() if t.owner is enemy)
            lower_cover = beam_tests.capsule_point_cover(eye, player_target, (0.3,))
            for offset, world, winner in ((margin, empty, enemy.body), (0, empty, npc.entity),
                                          (margin, lower_cover, enemy.body),
                                          (margin, wall(4), npc.entity), (0, wall(2), 0)):
                with (self.subTest(npc=row['name'], offset=offset, winner=winner),
                      patch.object(fight, '_collision', return_value=world)):
                    npc.owner.position = (x + offset, eye[1] - npc.height / 2, z + 3)
                    for _ in range(20):
                        run.step({source: (*direction, 0)})
                    player_health = fight.vitals_of(player_target)
                    before_npc, before_player = npc_health.total, player_health.total
                    run.step({source: (*direction, 2)})
                    self.assertTrue(search.active)
                    self.assertEqual(search.payload()["targets"], ((winner, 0),) if winner else ())
                    self.assertEqual(expr.to_entity(weapon.evaluate(damage_target)), winner)
                    self.assertAlmostEqual(before_npc - npc_health.total, 0.8 if winner == npc.entity else 0,
                                           places=4)
                    self.assertAlmostEqual(before_player - player_health.total, 0.8 if winner == enemy.body else 0,
                                           places=4)
            with patch.object(fight, '_collision', return_value=empty):
                run.step({source: (*direction, 2)})
                self.assertEqual(expr.to_entity(weapon.evaluate(damage_target)), npc.entity)
                run.step({source: (*direction, 0)})
                self.assertFalse(search.active)
                self.assertEqual(expr.to_entity(weapon.evaluate(damage_target)), 0)

    @unittest.skipUnless(deflection.TAGS_PATH is not None, "requires the build104319 entity tag catalog")
    def test_grasp_rejects_authored_body_tags_before_ranking(self):
        excluded = (0x1FDA, 0x17B4, 0x187E, 0x22D7, 0x2D91)
        for row, definition in zip(self.rows, excluded, strict=True):
            with self.subTest(npc=row["name"], definition=hex(definition)):
                run, source, npc, npc_health, _ = self.fight(row)
                fight = run.match.combat
                x, y, z = source.mover.position
                enemy = run.match.add_player(2, "Behind", moira_tests.MOIRA, npc.team, False)
                in_world(run.match, enemy)
                enemy.client = NpcEventClient(enemy)
                run.players.append(enemy)
                self.assertTrue(run.match.switch_hero(enemy, heroes()[moira_tests.MOIRA]))
                enemy.team = npc.team
                enemy.mover.position = [x, y, z + 5]
                enemy.mover.body = None
                npc.owner.position = (x, fight._eye(source)[1] - npc.height / 2, z + 3)
                weapon = next(i for i in source.body_script.component.instances.values() if i.graph.index == 0xF76)
                search = weapon.state(106)
                enemy_health = fight.vitals_of(next(t for t in fight._targets() if t.owner is enemy))
                before_npc, before_enemy = npc_health.total, enemy_health.total
                excluded_body = 0x0400000000000000 | definition
                with patch.object(fight, "_collision", return_value=World([])):
                    # Substitute authored metadata for the foreground actor. Its original
                    # NPC controller and the normal match input path remain in use.
                    with patch.object(npc.owner, "body", excluded_body):
                        for _ in range(20):
                            run.step({})
                        run.step({source: (0, 0, 2)})
                        self.assertEqual(search.payload()["targets"], ((enemy.body, 0),))
                        self.assertAlmostEqual(before_enemy - enemy_health.total, 0.8, places=4)
                        with patch.object(enemy, "hero", replace(enemy.hero, body=excluded_body)):
                            run.step({source: (0, 0, 2)})
                        self.assertEqual(search.payload()["targets"], ())
                        self.assertAlmostEqual(before_enemy - enemy_health.total, 0.8, places=4)
                    self.assertEqual(npc_health.total, before_npc)
                    run.step({source: (0, 0, 2)})
                    self.assertEqual(search.payload()["targets"], ((npc.entity, 0),))
                    self.assertAlmostEqual(before_npc - npc_health.total, 0.8, places=4)

    def test_grasp_switches_from_lethal_npc_hit_to_player_behind(self):
        for row in self.rows:
            with self.subTest(npc=row['name']):
                run, source, front, front_health, direction = self.fight(row)
                match = run.match
                behind = match.add_player(2, 'Behind', moira_tests.MOIRA, front.team, False)
                in_world(match, behind)
                behind.client = NpcEventClient(behind)
                run.players.append(behind)
                self.assertTrue(match.switch_hero(behind, heroes()[moira_tests.MOIRA]))
                behind.team = front.team
                x, y, z = source.mover.position
                behind.mover.position = [x, y, z + 4]
                behind.mover.body = None
                for _ in range(20):
                    run.step({})
                fight = match.combat
                rear_health = fight.vitals_of(next(t for t in fight._targets() if t.owner is behind))
                rear_before = rear_health.total
                weapon = next(instance for instance in source.body_script.component.instances.values()
                              if instance.graph.index == 0xF76)
                search = weapon.state(106)
                for kind in front_health.now:
                    front_health.now[kind] = 0.5 if kind == HEALTH_POOL else 0
                run.step({source: (*direction, 2)})
                self.assertTrue(front_health.dead)
                self.assertEqual(search.payload()["targets"], ((front.entity, 0),))
                self.assertEqual(rear_health.total, rear_before)
                run.step({source: (*direction, 2)})
                self.assertAlmostEqual(rear_before - rear_health.total, 0.8, places=4)
                self.assertEqual(search.payload()["targets"], ((behind.body, 0),))
                self.assertIn(front.owner, match.bots)

    def test_spray_reach_and_fuel_for_actual_five_bodies(self):
        for row in self.rows:
            fuel_spent = []
            for distance in (14, 17):
                with self.subTest(npc=row['name'], distance=distance):
                    run, source, target, vitals, _direction = self.fight(row, ally=True)
                    fight = run.match.combat
                    x, y, z = source.mover.position
                    target.owner.position = (x, y, z + distance)
                    direction = aim(fight._eye(source), (x, y + target.height / 2, z + distance))
                    vitals.damage(100)
                    before = vitals.total
                    weapon = next(i for i in source.body_script.component.instances.values()
                                  if i.graph.index == 0x0F76)
                    fuel = weapon.find_var(expr.INSTANCE, 87)
                    initial_fuel = fuel.value()
                    with (patch.object(fight, '_shoot', wraps=fight._shoot) as shots,
                          patch.object(fight, '_collision', return_value=World([]))):
                        for _ in range(40):
                            run.step({source: (*direction, 1)})
                        fuel_spent.append(initial_fuel - fuel.value())
                        for _ in range(60):
                            run.step({})
                    self.assertGreater(shots.call_count, 0)
                    state = shots.call_args_list[0].args[2]
                    self.assertEqual(state.evaluate(state.node.field('m_FEC435C6'), watch=False), 15)
                    if distance == 14:
                        self.assertGreater(vitals.total, before)
                    else:
                        self.assertEqual(vitals.total, before)
                    self.assertFalse(fight.rockets)
            self.assertEqual(fuel_spent[0], fuel_spent[1])
            self.assertGreater(fuel_spent[0], 0)

    def test_grasp_reach_for_actual_five_bodies(self):
        for row in self.rows:
            for distance in (19, 21):
                with self.subTest(npc=row['name'], distance=distance):
                    run, source, target, vitals, _direction = self.fight(row)
                    fight = run.match.combat
                    x, y, z = source.mover.position
                    target.owner.position = (x, y, z + distance)
                    direction = aim(fight._eye(source), (x, y + target.height / 2, z + distance))
                    before = vitals.total
                    with patch.object(fight, '_collision', return_value=World([])):
                        for _ in range(30):
                            run.step({source: (*direction, 2)})
                    grasp = next(i for i in source.body_script.component.instances.values()
                                 if i.graph.index == 0x0F76)
                    search = grasp.states[106]
                    self.assertTrue(search.active)
                    self.assertEqual(search.evaluate(search.node.field('m_radius'), watch=False), 19.5)
                    self.assertAlmostEqual(before - vitals.total, 24 if distance == 19 else 0, places=4)

    def test_coalescence_enemy_and_ally_health_paths_for_actual_five_bodies(self):
        for row in self.rows:
            for ally in (False, True):
                with self.subTest(npc=row["name"], ally=ally):
                    run, source, _target, vitals, direction = self.fight(row, ally=ally)
                    vitals.damage(100)
                    self.start_ult(run, source)
                    before = vitals.total
                    for _ in range(20):
                        run.step({source: (*direction, 0)})
                    self.assertAlmostEqual(vitals.total - before, 44.8 if ally else -22.4, places=4)

    def test_coalescence_reach_for_actual_five_bodies(self):
        for row in self.rows:
            for ally in (False, True):
                for distance in (29, 31):
                    with self.subTest(npc=row['name'], ally=ally, distance=distance):
                        run, source, target, vitals, _direction = self.fight(row, ally=ally)
                        fight = run.match.combat
                        x, y, z = source.mover.position
                        target.owner.position = (x, y, z + distance)
                        direction = aim(fight._eye(source), (x, y + target.height / 2, z + distance))
                        vitals.damage(100)
                        with patch.object(fight, '_collision', return_value=World([])):
                            self.start_ult(run, source)
                            before = vitals.total
                            for _ in range(20):
                                run.step({source: (*direction, 0)})
                        beam = next(i for i in source.body_script.component.instances.values()
                                    if i.graph.index == 0x1007)
                        self.assertTrue(beam.states[20].active)
                        search = beam.state(17 if ally else 19)
                        self.assertEqual(search.evaluate(search.node.field('m_radius'), watch=False), 30)
                        expected = (44.8 if ally else -22.4) if distance == 29 else 0
                        self.assertAlmostEqual(vitals.total - before, expected, places=4)

    def test_active_npc_tag_halves_charge_for_actual_damage_paths(self):
        for row in self.rows:
            for attack in ("grasp", "orb", "melee"):
                with self.subTest(npc=row["name"], attack=attack):
                    run, source, target, vitals, _direction = self.fight(row)
                    x, y, z = source.mover.position
                    target.owner.position = (x, y, z + 1.8)
                    for _ in range(20):
                        run.step({})
                    direction = aim(run.match.combat._eye(source), (x, y + target.height / 2, z + 1.8))
                    target_component = target.owner.body_script.component
                    tag_instance = next(
                        i for i in target_component.instances.values() if i.graph.index == 0xE62)
                    tag_state = tag_instance.states[7]
                    self.assertTrue(tag_state.active)
                    script = source.body_script
                    script.component.set(12918, 0)
                    script.set_ult_charge(0)
                    script.component.set(ULT_GAIN_ENABLED, True)
                    charge = script.component.entity_var(ULT_CHARGE)
                    before = vitals.total
                    if attack == "orb":
                        self.launch_orb(run, source)
                    for frame in range(20):
                        button = 2 if attack == "grasp" else 0x800 if attack == "melee" and frame == 0 else 0
                        run.step({source: (*direction, button)})
                    damage = before - vitals.total
                    self.assertGreater(damage, 0)
                    self.assertAlmostEqual(charge.value(), damage * 0.5, places=4)
                    if attack == "grasp":
                        tag_state.request_finish()
                        target_component.run_now()
                        before, previous_charge = vitals.total, charge.value()
                        for _ in range(20):
                            run.step({source: (*direction, 2)})
                        self.assertFalse(tag_state.active)
                        damage = before - vitals.total
                        self.assertGreater(damage, 0)
                        self.assertAlmostEqual(charge.value() - previous_charge, damage, places=4)

    def test_coalescence_visibility_fallback_for_actual_five_bodies(self):
        for row in self.rows:
            for ally in (False, True):
                with self.subTest(npc=row["name"], ally=ally):
                    run, source, target, vitals, direction = self.fight(row, ally=ally)
                    if ally:
                        vitals.damage(100)
                    self.start_ult(run, source)
                    fight = run.match.combat
                    for blocked_points in ((0.3,), (0.3, 0.6), (0.6,)):
                        with self.subTest(blocked_points=blocked_points):
                            collision = beam_tests.capsule_point_cover(
                                fight._eye(source), target, blocked_points)
                            before = vitals.total
                            with patch.object(fight, "_collision", return_value=collision):
                                for _ in range(20):
                                    run.step({source: (*direction, 0)})
                            expected = (44.8 if ally else -22.4) if len(blocked_points) == 1 else 0
                            self.assertAlmostEqual(vitals.total - before, expected, places=4)

    def test_actual_five_npc_sources_damage_moira_and_respect_fade(self):
        for row in self.rows:
            with self.subTest(npc=row["name"]):
                run, source, target, _vitals, _direction = self.fight(row)
                fight = run.match.combat
                own = next(t for t in fight._targets() if t.owner is source)
                health = fight.vitals_of(own)
                fight._damage(target.owner, own, 20, False, None, run.frame)
                self.assertEqual(health.total, 180)
                run.step({source: (0, 0, 8)})
                fight._damage(target.owner, own, 20, False, None, run.frame)
                self.assertEqual(health.total, 180)

    def test_both_orbs_affect_actual_five_bodies_with_correct_allegiance(self):
        for row in self.rows:
            for ally in (False, True):
                with self.subTest(npc=row["name"], ally=ally):
                    run, source, _target, vitals, _direction = self.fight(row, ally=ally)
                    vitals.damage(100)
                    orb = self.launch_orb(run, source, healing=ally)
                    before = vitals.total
                    for _ in range(20):
                        run.step({})
                    self.assertAlmostEqual(vitals.total - before, 20.8 if ally else -16, places=4)
                    self.assertAlmostEqual((300 if ally else 200) - orb.energy,
                                           20.8 if ally else 16, places=4)

    def test_both_actual_orbs_obey_aura_reach_for_five_bodies(self):
        for row in self.rows:
            for healing in (False, True):
                radius = 5 if healing else 4
                for inside in (False, True):
                    with self.subTest(npc=row['name'], healing=healing, inside=inside):
                        run, source, target, vitals, _direction = self.fight(row, ally=healing)
                        fight = run.match.combat
                        x, y, z = source.mover.position
                        lateral = radius + target.radius + (-0.75 if inside else 0.75)
                        target.owner.position = (x + lateral, y, z + 3)
                        vitals.damage(100)
                        before = vitals.total
                        with patch.object(fight, '_collision', return_value=World([])):
                            orb = self.launch_orb(run, source, healing=healing)
                            for _ in range(25):
                                run.step({})
                        initial_energy = 300 if healing else 200
                        if inside:
                            delivered = vitals.total - before if healing else before - vitals.total
                            self.assertGreater(delivered, 0)
                            self.assertAlmostEqual(initial_energy - orb.energy, delivered, places=4)
                        else:
                            self.assertEqual(vitals.total, before)
                            self.assertEqual(orb.energy, initial_energy)

    def test_both_actual_orbs_keep_moving_but_cannot_affect_five_bodies_through_cover(self):
        for row in self.rows:
            for healing in (False, True):
                with self.subTest(npc=row['name'], healing=healing):
                    run, source, target, vitals, _direction = self.fight(row, ally=healing)
                    fight = run.match.combat
                    x, y, z = source.mover.position
                    target.owner.position = (x + 2.5, y, z + 3)
                    vitals.damage(100)
                    before = vitals.total
                    wall = x + 1
                    covered = World([
                        (wall, y - 5, z - 10, wall, y + 5, z - 10, wall, y + 5, z + 10),
                        (wall, y - 5, z - 10, wall, y + 5, z + 10, wall, y - 5, z + 10),
                    ])
                    with patch.object(fight, '_collision', return_value=covered):
                        orb = self.launch_orb(run, source, healing=healing)
                        initial_position = orb.position
                        initial_energy = 300 if healing else 200
                        for _ in range(8):
                            run.step({})
                        self.assertEqual(vitals.total, before)
                        self.assertEqual(orb.energy, initial_energy)
                        self.assertGreater(orb.position[2], initial_position[2])
                    with patch.object(fight, '_collision', return_value=World([])):
                        for _ in range(8):
                            run.step({})
                    delivered = vitals.total - before if healing else before - vitals.total
                    self.assertGreater(delivered, 0)
                    self.assertAlmostEqual(initial_energy - orb.energy, delivered, places=4)

    def test_quick_melee_against_actual_five_bodies_obeys_allegiance(self):
        for row in self.rows:
            for ally in (False, True):
                with self.subTest(npc=row["name"], ally=ally):
                    run, source, target, vitals, _direction = self.fight(row, ally=ally)
                    x, y, z = source.mover.position
                    target.owner.position = (x, y, z + 1.5)
                    direction = aim(run.match.combat._eye(source), (x, y + target.height / 2, z + 1.5))
                    before = vitals.total
                    run.step({source: (*direction, 0x800)})
                    for _ in range(40):
                        run.step({source: (*direction, 0)})
                    self.assertEqual(before - vitals.total, 0 if ally else 30)

    def test_quick_melee_releases_but_misses_five_bodies_beyond_reach_or_cover(self):
        for row in self.rows:
            for blocked in (False, True):
                distance = 1.5 if blocked else 4
                with self.subTest(npc=row['name'], blocked=blocked):
                    run, source, target, vitals, _direction = self.fight(row)
                    fight = run.match.combat
                    x, y, z = source.mover.position
                    target.owner.position = (x, y, z + distance)
                    direction = aim(fight._eye(source), (x, y + target.height / 2, z + distance))
                    triangles = []
                    if blocked:
                        wall = z + 0.75
                        triangles = [
                            (x - 10, y - 5, wall, x + 10, y - 5, wall, x + 10, y + 5, wall),
                            (x - 10, y - 5, wall, x + 10, y + 5, wall, x - 10, y + 5, wall),
                        ]
                    before = vitals.total
                    with patch.object(fight, '_collision', return_value=World(triangles)):
                        run.step({source: (*direction, 0x800)})
                        for _ in range(40):
                            run.step({source: (*direction, 0)})
                    melee = next(i for i in source.body_script.component.instances.values()
                                 if i.graph.index == 0x0043)
                    self.assertGreater(fight.melee_seen[source.body][(melee.id, 4)], 0)
                    self.assertEqual(vitals.total, before)

    def test_grasp_kills_actual_five_bodies_and_manual_corpses_remain(self):
        for row in self.rows:
            with self.subTest(npc=row["name"]):
                run, source, target, vitals, direction = self.fight(row)
                bot = target.owner
                entity = bot.entity
                # Explicit near-death suffix of each real authored body/pool set.
                for kind in vitals.now:
                    vitals.now[kind] = 10.0 if kind == HEALTH_POOL else 0.0
                component = source.body_script.component
                component.set(12918, 0)
                source.body_script.set_ult_charge(0)
                self.assertIs(component.entity_var(ULT_GAIN_ENABLED).value(), True)
                charge = component.entity_var(ULT_CHARGE)
                for _ in range(20):
                    run.step({source: (*direction, 2)})
                self.assertTrue(vitals.dead)
                self.assertTrue(bot.dead)
                self.assertEqual(vitals.total, 0)
                self.assertAlmostEqual(charge.value(), 5, places=4)
                charge_after_kill = charge.value()
                self.assertAlmostEqual(source.stats.value(stats.HERO_DAMAGE), 10)
                self.assertEqual([entity for entity, _event in source.client.bot_deaths], [entity])
                self.assertNotIn(bot, [owner for _when, owner in run.match.combat.respawns])
                for _ in range(250):
                    run.step({})
                self.assertIn(bot, run.match.bots)
                self.assertEqual(bot.entity, entity)
                self.assertNotIn(entity, [t.entity for t in run.match.combat._enemies(source)])
                self.assertIsNotNone(bot.body_script)
                self.assertEqual(charge.value(), charge_after_kill)

    def test_surviving_orb_keeps_launch_body_in_actual_five_npc_death_events(self):
        for row in self.rows:
            with self.subTest(npc=row['name']):
                run, source, target, health, _direction = self.fight(row)
                orb = self.launch_orb(run, source)
                old = source.body
                self.assertTrue(run.match.switch_hero(source, heroes()[moira_tests.MOIRA]))
                self.assertNotEqual(source.body, old)
                for kind in health.now:
                    health.now[kind] = 0.4 if kind == HEALTH_POOL else 0.0
                orb.position = (target.feet[0], target.feet[1] + target.height / 2, target.feet[2])
                orb.speed = 0
                script = target.owner.body_script
                with patch.object(script, 'die', wraps=script.die) as die:
                    run.step({})
                self.assertTrue(health.dead)
                self.assertEqual(health.killer, old)
                die.assert_called_once()
                self.assertEqual(die.call_args.args[1][combat.KILLER], expr.Entity(old))
                self.assertEqual(die.call_args.args[2], old)
                notice = source.client.messages(combat.KILL_NOTICE)[-1]
                self.assertEqual(notice['+0x80']['+0x0'], old)

    def test_reflected_damage_orb_changes_npc_allegiance_and_death_credit(self):
        for row in self.rows:
            with self.subTest(npc=row['name']):
                run, source, target, health, _direction = self.fight(row, ally=True)
                match = run.match
                genji = match.add_player(2, 'Deflector', deflect_tests.GENJI, 1, False)
                in_world(match, genji)
                genji.client = NpcEventClient(genji)
                run.players.append(genji)
                match.switch_hero(genji, heroes()[deflect_tests.GENJI])
                genji.team = 1 - source.team
                x, y, z = source.mover.position
                genji.mover.position = [x, y, z + 2]
                for _ in range(20):
                    self.step(run, genji)
                for frame in range(5):
                    self.step(run, genji, deflect=frame == 0)
                for kind in health.now:
                    health.now[kind] = 0.4 if kind == HEALTH_POOL else 0.0
                orb = self.launch_deflect_orb(run, source, genji)
                self.assertFalse(health.dead)  # original ally receives no damage
                script = target.owner.body_script
                with patch.object(script, 'die', wraps=script.die) as die:
                    self.reflect_orb(run, source, genji, orb)
                self.assertTrue(health.dead)
                self.assertEqual(health.killer, genji.body)
                self.assertIs(orb.shooter, genji)
                self.assertEqual(die.call_args.args[1][combat.KILLER], expr.Entity(genji.body))
                self.assertEqual(die.call_args.args[2], genji.body)

    @unittest.skipUnless(barrier_tests.BARRIER, 'prepared external barrier descriptor required')
    def test_enemy_barrier_blocks_grasp_damage_and_spray_for_actual_five_bodies(self):
        for row in self.rows:
            for ally in (False, True):
                with self.subTest(npc=row['name'], ally=ally):
                    run, source, _target, health, direction = self.fight(row, ally=ally)
                    match = run.match
                    rein = match.add_player(2, 'Barrier', barrier_tests.REINHARDT, 1, False)
                    in_world(match, rein)
                    rein.client = NpcEventClient(rein)
                    run.players.append(rein)
                    match.switch_hero(rein, heroes()[barrier_tests.REINHARDT])
                    rein.team = 1 - source.team
                    x, y, z = source.mover.position
                    # Owner stays outside the grasp ray while its real mesh
                    # covers the NPC, proving interception of the NPC path.
                    rein.mover.position = [x + 1.5, y, z + 2]
                    for _ in range(30):
                        barrier_tests.MoiraBarrierTests.step(run, rein)
                    shield = match.combat.barriers[rein.body]
                    self.assertTrue(shield.active)
                    health.damage(100)
                    before = health.total
                    for _ in range(30):
                        run.step({source: (*direction, 1 if ally else 2), rein: (-32768, 0, 2)})
                    self.assertEqual(health.total, before)
                    if ally:
                        self.assertEqual(shield.current, 1600)
                    else:
                        self.assertLess(shield.current, 1600)

    def test_actual_trooper_and_heavy_melee_respect_range_team_and_fade(self):
        for name, graph_index, amount in (("Trooper", 0x1422, 30), ("Heavy Assault", 0x140E, 80)):
            row = next(row for row in self.rows if row["name"] == name)
            for case in ("hit", "ally", "far", "wall", "fade"):
                with self.subTest(npc=name, case=case):
                    run, source, target, _vitals, _direction = self.fight(row, ally=case == "ally")
                    fight = run.match.combat
                    actor = target.owner
                    actor.yaw = -32768
                    x, y, z = source.mover.position
                    actor.position = (x, y, z + (12 if case == "far" else 2))
                    own = next(item for item in fight._targets() if item.owner is source)
                    health = fight.vitals_of(own)
                    script = actor.body_script
                    actor.firing = True
                    for _ in range(12):
                        run.step({})
                    script.component.edges([(31, True)])
                    script.component.run_now()
                    collision = fight._collision()
                    if case == "wall":
                        wall_z = z + 1
                        collision = World([
                            (x - 5, y - 5, wall_z, x + 5, y - 5, wall_z, x + 5, y + 5, wall_z),
                            (x - 5, y - 5, wall_z, x + 5, y + 5, wall_z, x - 5, y + 5, wall_z),
                        ])
                    with patch.object(fight, "_collision", return_value=collision):
                        for frame in range(43):
                            run.step({source: (0, 0, 8 if case == "fade" and frame == 0 else 0)})
                    self.assertIs(actor.body_script, script)
                    self.assertIsNone(script.component.fault)
                    self.assertTrue(any(item.graph.index == graph_index
                                        for item in script.component.instances.values()))
                    self.assertEqual(health.total, 200 - amount if case == "hit" else 200)

    def test_actual_npc_melee_cooldown_rejects_hold_and_early_repress(self):
        for name, graph_index, ability_node, damage in (
            ("Trooper", 0x1422, 8, 30), ("Heavy Assault", 0x140E, 9, 80),
        ):
            row = next(row for row in self.rows if row["name"] == name)
            for held in (True, False):
                with self.subTest(npc=name, held=held):
                    run, source, target, _vitals, _direction = self.fight(row)
                    fight = run.match.combat
                    actor = target.owner
                    actor.yaw = -32768
                    x, y, z = source.mover.position
                    actor.position = (x, y, z + 2)
                    own = next(item for item in fight._targets() if item.owner is source)
                    health = fight.vitals_of(own)
                    component = actor.body_script.component
                    instance = next(item for item in component.instances.values()
                                    if item.graph.index == graph_index)
                    ability = instance.node_state(instance.graph.node(ability_node))
                    actor.firing = True
                    for _ in range(12):
                        run.step({})

                    def input_edge(down):
                        component.edges([(31, down)])
                        component.run_now()

                    input_edge(True)
                    self.assertEqual(ability.cur, 1)
                    if not held:
                        input_edge(False)
                    for _ in range(43):
                        run.step({})
                    self.assertEqual(health.total, 200 - damage)
                    self.assertGreater(ability.remaining(), 0.2)
                    source.mover.position = [x, y, z]
                    source.mover.velocity = (0, 0, 0)
                    source.mover.body = None
                    if not held:
                        input_edge(True)
                        run.step({})
                        input_edge(False)
                    for _ in range(40):
                        run.step({})
                    self.assertEqual(ability.remaining(), 0)
                    self.assertEqual(health.total, 200 - damage)
                    if held:
                        input_edge(False)
                        run.step({})
                    input_edge(True)
                    input_edge(False)
                    for _ in range(43):
                        run.step({})
                    self.assertEqual(health.total, 200 - 2 * damage)
                    self.assertIsNone(component.fault)

    def test_actual_npc_melee_impulse_reaches_moira_mover(self):
        for name, horizontal, vertical in (("Trooper", 1, 0), ("Heavy Assault", 20, 8)):
            row = next(row for row in self.rows if row["name"] == name)
            with self.subTest(npc=name):
                run, source, target, _vitals, _direction = self.fight(row)
                fight = run.match.combat
                actor = target.owner
                actor.yaw = -32768
                x, y, z = source.mover.position
                actor.position = (x, y, z + 2)
                own = next(item for item in fight._targets() if item.owner is source)
                health = fight.vitals_of(own)
                component = actor.body_script.component
                actor.firing = True
                for _ in range(12):
                    run.step({})
                component.edges([(31, True)])
                component.run_now()
                # The server update processes NPC combat before player poses.
                for _ in range(43):
                    run.frame += 1
                    run.match.update(run.frame * combat.TICK, run.frame)
                    if health.total < 200:
                        break
                    source.take_commands([Command(run.frame)])
                self.assertLess(health.total, 200)
                self.assertAlmostEqual(source.mover.velocity[2], -horizontal)
                self.assertAlmostEqual(source.mover.velocity[1], vertical)
                source.take_commands([Command(run.frame)])
                if vertical:
                    self.assertLess(source.mover.position[2], z)
                    self.assertGreater(source.mover.position[1], y)
                else:
                    self.assertEqual(source.mover.position[2], z)

    def test_actual_heavy_charge_travel_wall_and_cooldown(self):
        row = next(row for row in self.rows if row["name"] == "Heavy Assault")
        run, source, target, _vitals, _direction = self.fight(row)
        fight = run.match.combat
        actor = target.owner
        x, y, z = source.mover.position
        start = (x, y, z + 9)
        actor.position = start
        actor.yaw = -32768
        actor.firing = True
        for _ in range(12):
            run.step({})
        script = actor.body_script
        component = script.component
        instance = next(item for item in component.instances.values() if item.graph.index == 0x1408)
        ability = instance.node_state(instance.graph.node(82))

        def press():
            component.edges([(35, True)])
            component.run_now()
            component.edges([(35, False)])
            component.run_now()

        wall_z = z + 2
        wall = World([
            (x - 5, y - 5, wall_z, x + 5, y - 5, wall_z, x + 5, y + 5, wall_z),
            (x - 5, y - 5, wall_z, x + 5, y + 5, wall_z, x - 5, y + 5, wall_z),
        ])
        with patch.object(fight, "_collision", return_value=wall):
            press()
            actor.firing = False  # charge owns its clock without a firing route
            for _ in range(49):
                run.step({})
                self.assertEqual(actor.position, start)
            for _ in range(4):
                run.step({})
                if actor.position != start:
                    break
            self.assertAlmostEqual(actor.position[2], start[2] - 15 * combat.TICK)
            self.assertAlmostEqual(actor.velocity[2], -15)
            for _ in range(45):
                run.step({})
                if instance.vars[59].value() == 0:
                    break
            self.assertIs(actor.body_script, script)
            self.assertIsNone(component.fault)
            self.assertEqual(instance.vars[59].value(), 0)
            self.assertEqual(actor.velocity, (0, 0, 0))
            self.assertGreater(actor.position[2], wall_z)
            self.assertLessEqual(actor.position[2], wall_z + 2)
            self.assertGreater(ability.remaining(), 14)
            stop = actor.position
            for _ in range(950):
                if ability.remaining() <= 0.25:
                    break
                press()
                run.step({})
                self.assertEqual(actor.position, stop)
                self.assertEqual(instance.vars[59].value(), 0)
            self.assertGreater(ability.remaining(), 0)
            for _ in range(20):
                run.step({})
                self.assertEqual(actor.position, stop)
            self.assertEqual(ability.remaining(), 0)
            self.assertEqual(instance.vars[59].value(), 0)
            press()
            self.assertEqual(instance.vars[59].value(), 1)

    def test_actual_heavy_charge_side_graze_and_timeout(self):
        row = next(row for row in self.rows if row["name"] == "Heavy Assault")
        for case in ("side", "ally", "far", "wall", "frontal", "fade"):
            with self.subTest(case=case):
                run, source, target, _vitals, _direction = self.fight(row, ally=case == "ally")
                fight = run.match.combat
                actor = target.owner
                x, y, z = source.mover.position
                actor.position = (x, y, z + 9)
                actor.yaw = -32768
                lateral = {"far": 5, "frontal": 0}.get(case, 2)
                source.mover.position = [x + lateral, y, z + 7]
                source.mover.body = None
                health = fight.vitals_of(next(item for item in fight._targets() if item.owner is source))
                component = actor.body_script.component
                instance = next(item for item in component.instances.values() if item.graph.index == 0x1408)
                collision = fight._collision()
                if case == "wall":
                    wall_x = x + 1
                    collision = World([
                        (wall_x, y - 5, z + 4, wall_x, y - 5, z + 11, wall_x, y + 5, z + 11),
                        (wall_x, y - 5, z + 4, wall_x, y + 5, z + 11, wall_x, y + 5, z + 4),
                    ])
                actor.firing = True
                for _ in range(12):
                    run.step({})
                actor.firing = False
                self.assertEqual(actor.body_script.frame, run.frame)
                component.edges([(35, True)])
                component.run_now()
                component.edges([(35, False)])
                component.run_now()
                with patch.object(fight, "_collision", return_value=collision):
                    for frame in range(80):
                        run.frame += 1
                        run.match.update(run.frame * combat.TICK, run.frame)
                        if health.total < 200:
                            break
                        fade = case == "fade" and frame == 43
                        source.take_commands([Command(run.frame, buttons=8 if fade else 0)])
                        if fade:
                            self.assertTrue(fight.damage_immune(source))
                    self.assertIsNone(component.fault)
                    self.assertEqual(health.total, 150 if case == "side" else 200)
                    if case == "side":
                        self.assertEqual(fight.heavy_charge_contacts[actor.entity], {source.body})
                        vx, vy, vz = source.mover.velocity
                        self.assertAlmostEqual((vx * vx + vz * vz) ** 0.5, 30)
                        self.assertAlmostEqual(vy, 10)
                        source.take_commands([Command(run.frame)])
                        for _ in range(5):
                            bx, by, bz = actor.position
                            source.mover.position = [bx + 2, by, bz - 2]
                            source.mover.body = None
                            source.mover.velocity = (0, 0, 0)
                            run.step({})
                            self.assertEqual(health.total, 150)
                        for _ in range(250):
                            run.step({})
                        self.assertEqual(health.total, 150)
                        self.assertEqual(instance.vars[59].value(), 0)
                        self.assertEqual(actor.velocity, (0, 0, 0))
                        self.assertNotIn(actor.entity, fight.heavy_charge_contacts)

    def test_actual_heavy_charge_binds_and_releases_moira(self):
        row = next(row for row in self.rows if row["name"] == "Heavy Assault")
        for fade in (False, True):
            with self.subTest(fade=fade):
                run, source, target, _vitals, _direction = self.fight(row)
                fight = run.match.combat
                actor = target.owner
                x, y, z = source.mover.position
                actor.position = (x, y, z + 5)
                actor.yaw = -32768
                actor.firing = True
                for _ in range(12):
                    run.step({})
                component = actor.body_script.component
                component.edges([(35, True)])
                component.run_now()
                component.edges([(35, False)])
                component.run_now()
                actor.firing = False
                for frame in range(80):
                    run.step({source: (0, 0, 8 if fade and frame == 43 else 0)})
                    if actor.entity in fight.heavy_charge_pins:
                        break
                self.assertIsNone(component.fault)
                if fade:
                    self.assertNotIn(actor.entity, fight.heavy_charge_pins)
                    self.assertFalse(fight.is_stunned(source))
                    continue
                application = fight.heavy_charge_pins[actor.entity]
                self.assertEqual(application.graph.index, 0x1407)
                self.assertEqual(application.source_entity(), actor.entity)
                self.assertEqual(fight.pins[actor.entity], source.body)
                self.assertTrue(fight.is_stunned(source))
                for _ in range(10):
                    run.step({source: (12000, 0, 2 | 8 | 16 | 32)})
                    self.assertTrue(fight.is_stunned(source))
                    self.assertFalse(source.body_script.component.held)
                    self.assertLess(source.mover.position[2], actor.position[2])
                for _ in range(250):
                    run.step({})
                self.assertNotIn(actor.entity, fight.heavy_charge_pins)
                self.assertFalse(fight.is_stunned(source))
                self.assertNotIn(application.id, application.component.instances)

    def test_actual_heavy_charge_wall_crush_and_replacement(self):
        row = next(row for row in self.rows if row["name"] == "Heavy Assault")
        for replace_body in (False, True):
            with self.subTest(replace_body=replace_body):
                run, source, target, _vitals, _direction = self.fight(row)
                fight = run.match.combat
                actor = target.owner
                x, y, z = source.mover.position
                actor.position = (x, y, z + 5)
                actor.yaw = -32768
                actor.firing = True
                for _ in range(12):
                    run.step({})
                component = actor.body_script.component
                instance = next(item for item in component.instances.values() if item.graph.index == 0x1408)
                victim = next(item for item in fight._targets() if item.owner is source)
                health = fight.vitals_of(victim)
                wall_z = z - 1.5
                wall = World([
                    (x - 5, y - 5, wall_z, x + 5, y - 5, wall_z, x + 5, y + 5, wall_z),
                    (x - 5, y - 5, wall_z, x + 5, y + 5, wall_z, x - 5, y + 5, wall_z),
                ])
                with patch.object(fight, "_collision", return_value=wall):
                    component.edges([(35, True)])
                    component.run_now()
                    component.edges([(35, False)])
                    component.run_now()
                    for _ in range(80):
                        run.step({})
                        if actor.entity in fight.heavy_charge_pins:
                            break
                    application = fight.heavy_charge_pins[actor.entity]
                    self.assertEqual(health.total, 200)
                    if replace_body:
                        self.assertTrue(run.match.switch_hero(source, heroes()[moira_tests.MOIRA]))
                        self.assertNotEqual(source.body, victim.entity)
                    for _ in range(40):
                        run.step({})
                self.assertIsNone(component.fault)
                self.assertNotIn(actor.entity, fight.heavy_charge_pins)
                self.assertNotIn(application.id, application.component.instances)
                self.assertEqual(instance.vars[59].value(), 0)
                self.assertFalse(fight.is_stunned(source))
                if replace_body:
                    self.assertEqual(health.total, 200)
                    current = next(item for item in fight._targets() if item.owner is source)
                    self.assertEqual(fight.vitals_of(current).total, 200)
                else:
                    self.assertTrue(health.dead)
                    self.assertEqual(health.killer, actor.entity)
                    self.assertEqual(instance.node_state(instance.graph.node(29)).counter, 1)
                    self.assertEqual(source.client.messages(combat.DEATH_NOTICE)[-1]["+0x7C"]["+0x0"], actor.entity)

    def test_actual_control_and_death_stop_bound_heavy_charge(self):
        row = next(row for row in self.rows if row["name"] == "Heavy Assault")
        for hero, button in (
            (find_hero("ana"), 8), (heroes()[control_tests.MCCREE], 16), (find_hero("moira"), 2),
        ):
            with self.subTest(hero=hero.guid):
                run, source, target, heavy_health, _direction = self.fight(row)
                match, actor = run.match, target.owner
                fight = match.combat
                x, y, z = source.mover.position
                actor.position = (x, y, z + 5)
                actor.yaw = -32768
                caster = match.add_player(2, "Control", hero.guid, source.team, False)
                in_world(match, caster)
                caster.client = NpcEventClient(caster)
                run.players.append(caster)
                self.assertTrue(match.switch_hero(caster, hero))
                caster.team = source.team
                caster.mover.position = [x + 10, y, z + 8]
                actor.firing = True
                for _ in range(40):
                    run.step({})
                component = actor.body_script.component
                instance = next(item for item in component.instances.values() if item.graph.index == 0x1408)
                component.edges([(35, True)])
                component.run_now()
                component.edges([(35, False)])
                component.run_now()
                for _ in range(80):
                    run.step({})
                    if actor.entity in fight.heavy_charge_pins:
                        break
                application = fight.heavy_charge_pins[actor.entity]
                self.assertTrue(fight.is_stunned(source))
                bx, by, bz = actor.position
                caster.mover.position = [bx, by, bz + 1.25]
                caster.mover.body = None
                lethal = button == 2
                if lethal:
                    heavy_health.damage(heavy_health.total - 0.3)
                health_before = heavy_health.total
                run.step({caster: (-32768, 0, button)})
                for _ in range(60):
                    if heavy_health.dead or fight.is_stunned(actor):
                        break
                    run.step({caster: (-32768, 0, button if lethal else 0)})
                if lethal:
                    self.assertTrue(heavy_health.dead)
                    self.assertEqual(heavy_health.killer, caster.body)
                else:
                    self.assertTrue(fight.is_stunned(actor))
                    self.assertEqual(instance.vars[59].value(), 0)
                self.assertLess(heavy_health.total, health_before)
                self.assertFalse(fight.is_stunned(source))
                self.assertNotIn(actor.entity, fight.heavy_charge_pins)
                self.assertNotIn(application.id, application.component.instances)
                self.assertEqual(actor.velocity, (0, 0, 0))
                stopped = actor.position
                for _ in range(20):
                    run.step({})
                self.assertEqual(actor.position, stopped)

    def test_actual_heavy_charge_requires_marker_for_body_impact(self):
        row = next(row for row in self.rows if row["name"] == "Heavy Assault")
        for marked in (False, True):
            with self.subTest(marked=marked):
                run, source, target, _vitals, _direction = self.fight(row)
                fight = run.match.combat
                actor = target.owner
                x, y, z = source.mover.position
                actor.position = (x, y, z + 9)
                actor.yaw = -32768
                source.mover.position = [x, y, z + 8.3]
                source.mover.body = None
                if marked:
                    component = source.body_script.component
                    pillar = component.create(graph.graph(0x0636), init=False)
                    pillar.run_node(pillar.graph.node(27), None)
                    component.run_now()
                    self.assertTrue(component.entity_var(961).value())
                own = next(item for item in fight._targets() if item.owner is source)
                health = fight.vitals_of(own)
                actor.firing = True
                for _ in range(12):
                    run.step({})
                component = actor.body_script.component
                instance = next(item for item in component.instances.values() if item.graph.index == 0x1408)
                component.edges([(35, True)])
                component.run_now()
                component.edges([(35, False)])
                component.run_now()
                for _ in range(80):
                    run.step({})
                    if source.body in fight.stuns:
                        break
                impact = instance.states.get(instance.graph.node(102).state)
                self.assertIsNone(component.fault)
                if marked:
                    self.assertEqual(health.total, 25)
                    self.assertEqual(fight.stuns[source.body] - run.frame, 125)
                    self.assertEqual(impact.counter, 1)
                    self.assertEqual(instance.vars[59].value(), 0)
                    self.assertNotIn(actor.entity, fight.heavy_charge_impacts)
                    for _ in range(126):
                        run.step({})
                    self.assertEqual(health.total, 25)
                    self.assertFalse(fight.is_stunned(source))
                else:
                    self.assertEqual(health.total, 200)
                    self.assertTrue(impact is None or impact.counter == 0)
                    self.assertNotIn(source.body, fight.stuns)

    def test_heavy_marked_impact_finishes_bound_moira(self):
        row = next(row for row in self.rows if row["name"] == "Heavy Assault")
        run, source, target, _vitals, _direction = self.fight(row)
        fight, actor = run.match.combat, target.owner
        x, y, z = source.mover.position
        actor.position = (x, y, z + 5)
        actor.yaw = -32768
        actor.firing = True
        second = run.match.add_player(2, "Marked", moira_tests.MOIRA, source.team, False)
        in_world(run.match, second)
        second.client = NpcEventClient(second)
        run.players.append(second)
        self.assertTrue(run.match.switch_hero(second, heroes()[moira_tests.MOIRA]))
        second.team = source.team
        second.mover.position = [x + 10, y, z + 8]
        for _ in range(20):
            run.step({})
        component = actor.body_script.component
        component.edges([(35, True)])
        component.run_now()
        component.edges([(35, False)])
        component.run_now()
        for _ in range(80):
            run.step({})
            if actor.entity in fight.heavy_charge_pins:
                break
        application = fight.heavy_charge_pins[actor.entity]
        victims = {item.owner: fight.vitals_of(item) for item in fight._targets()}
        marker_component = second.body_script.component
        pillar = marker_component.create(graph.graph(0x0636), init=False)
        pillar.run_node(pillar.graph.node(27), None)
        marker_component.run_now()
        bx, by, bz = actor.position
        second.mover.position = [bx + 0.4, by, bz - 0.3]
        second.mover.body = None
        for _ in range(6):
            run.step({})
            if fight.is_stunned(second):
                break
        self.assertTrue(victims[source].dead)
        self.assertEqual(victims[source].killer, actor.entity)
        self.assertEqual(victims[second].total, 25)
        self.assertTrue(fight.is_stunned(second))
        self.assertNotIn(actor.entity, fight.heavy_charge_pins)
        self.assertNotIn(application.id, application.component.instances)

    def test_heavy_hits_actual_mei_wall_and_crushes_bound_moira(self):
        row = next(row for row in self.rows if row["name"] == "Heavy Assault")
        run, source, target, _vitals, _direction = self.fight(row)
        match, actor = run.match, target.owner
        fight = match.combat
        x, y, z = source.mover.position
        actor.position = (x, y, z + 9)
        actor.yaw = -32768
        actor.firing = True
        source.mover.position = [x, y, z + 7]
        source.mover.body = None
        mei = find_hero("mei")
        caster = match.add_player(2, "Mei", mei.guid, source.team, False)
        in_world(match, caster)
        caster.client = NpcEventClient(caster)
        run.players.append(caster)
        self.assertTrue(match.switch_hero(caster, mei))
        caster.team = source.team
        caster.mover.position = [x, y, z + 2]
        caster.mover.body = None
        for _ in range(40):
            run.step({})
        direction = aim(fight._eye(caster), (x, y, z + 5))
        for button in (16, 0, 1, 0):
            run.step({caster: (*direction, button)})
        self.assertEqual(len(fight.walls.pieces), 5)
        centre = min(fight.walls.pieces.values(), key=lambda piece: abs(piece.feet[0] - x))
        self.assertTrue(centre.impact_marked)
        self.assertEqual(centre.health, 400)
        health = fight.vitals_of(next(item for item in fight._targets() if item.owner is source))
        component = actor.body_script.component
        component.edges([(35, True)])
        component.run_now()
        component.edges([(35, False)])
        component.run_now()
        bound = False
        for _ in range(100):
            run.step({})
            bound |= actor.entity in fight.heavy_charge_pins
            if centre.health < 400:
                break
        self.assertTrue(bound)
        self.assertEqual(centre.health, 225)
        self.assertTrue(health.dead)
        self.assertEqual(health.killer, actor.entity)
        self.assertNotIn(actor.entity, fight.heavy_charge_pins)
        self.assertFalse(fight.is_stunned(caster))
        self.assertIsNone(component.fault)
        wall_graph = next(item for item in caster.body_script.component.instances.values()
                          if item.graph.index == 0x0622)
        self.assertGreater(wall_graph.state(33).remaining(), 0)
        run.step({caster: (*direction, 16)})
        self.assertFalse(fight.walls.pieces)
        for button in (0, 16, 0, 1, 0):
            run.step({caster: (*direction, button)})
        self.assertFalse(fight.walls.pieces)
        self.assertFalse(fight.walls.inputs[caster.body].preview)

    def test_four_actual_npc_weapon_releases_damage_moira_and_heavy_kills(self):
        expected = {'Trooper': (80, 6), 'Enforcer': (196, 2), 'Sniper': (100, 1),
                    'Heavy Assault': (0, 41)}
        for row in self.supported_weapon_rows():
            with self.subTest(npc=row['name']):
                run, source, target, _health, _direction = self.fight(row)
                fight = run.match.combat
                own = next(t for t in fight._targets() if t.owner is source)
                health = fight.vitals_of(own)
                self.arm_weapon(target.owner)
                with patch.object(fight, '_shoot', wraps=fight._shoot) as shots:
                    for _ in range(180):
                        run.step({})
                self.assertEqual(health.total, expected[row['name']][0])
                self.assertEqual(shots.call_count, expected[row['name']][1])
                self.assertTrue(all(call.args[0] is target.owner for call in shots.call_args_list))
                self.assertIsNotNone(target.owner.body_script)
                self.assertIsNone(target.owner.body_script.component.fault)
                if row['name'] == 'Heavy Assault':
                    self.assertTrue(health.dead)
                    self.assertEqual(health.killer, target.entity)
                    notice = source.client.messages(combat.DEATH_NOTICE)[-1]
                    self.assertEqual(notice['+0x7C']['+0x0'], target.entity)

    def test_actual_heavy_volley_cancels_orb_selection_before_respawn(self):
        row = next(row for row in self.rows if row['name'] == 'Heavy Assault')
        for healing in (False, True):
            with self.subTest(healing=healing):
                run, source, target, _health, direction = self.fight(row)
                fight = run.match.combat
                own = next(t for t in fight._targets() if t.owner is source)
                health = fight.vitals_of(own)
                old_body = source.body
                run.step({source: (*direction, 16)})
                selection = next(i.states[0] for i in source.body_script.component.instances.values()
                                 if i.graph.index == 0x10E4)
                self.assertTrue(selection.flags & nodes.Ability.RUNNING)
                stale = source.mover.last_command
                health.damage(health.total - 1)
                self.arm_weapon(target.owner)
                for _ in range(45):
                    run.step({})
                    if fight.is_dead(source):
                        break
                self.assertTrue(fight.is_dead(source))
                self.assertEqual(health.killer, target.entity)
                self.assertFalse(fight.moira_orbs)
                self.assertEqual(npc_control.stop(run.match), 1)
                for _ in range(230):
                    run.step({source: (*direction, 1 if healing else 2)})
                    if source.body != old_body:
                        break
                self.assertNotEqual(source.body, old_body)
                source.take_commands([stale])
                run.step({})
                self.assertFalse(fight.moira_orbs)
                orb = self.launch_orb(run, source, healing=healing)
                self.assertEqual(orb.body, source.body)
                self.assertEqual(orb.healing, healing)

    def test_actual_heavy_volley_ends_active_coalescence_without_refund(self):
        row = next(row for row in self.rows if row['name'] == 'Heavy Assault')
        run, source, target, target_health, direction = self.fight(row)
        fight = run.match.combat
        own = next(t for t in fight._targets() if t.owner is source)
        health = fight.vitals_of(own)
        component = source.body_script.component
        self.start_ult(run, source)
        beam = next(i for i in component.instances.values() if i.graph.index == 0x1007)
        self.assertTrue(beam.states[20].active)
        before = target_health.total
        for _ in range(5):
            run.step({source: (*direction, 0)})
        self.assertAlmostEqual(before - target_health.total, 5.6, places=4)

        health.damage(health.total - 1)
        self.arm_weapon(target.owner)
        for _ in range(45):
            run.step({source: (*direction, 0)})
            if fight.is_dead(source):
                break
        self.assertTrue(fight.is_dead(source))
        self.assertEqual(health.killer, target.entity)
        self.assertFalse(beam.states[20].active)
        self.assertFalse(beam.states[0].flags & nodes.Ability.RUNNING)
        self.assertEqual(component.entity_var(ULT_CHARGE).value(), 0)
        after = target_health.total
        for _ in range(20):
            run.step({source: (*direction, 32)})
        self.assertEqual(target_health.total, after)

    def test_actual_four_npc_weapon_releases_respect_already_active_fade(self):
        for row in self.supported_weapon_rows():
            with self.subTest(npc=row['name']):
                run, source, target, _health, _direction = self.fight(row)
                fight = run.match.combat
                own = next(t for t in fight._targets() if t.owner is source)
                health = fight.vitals_of(own)
                self.arm_weapon(target.owner)
                # Sniper's first authored release occurs at offset105; the
                # other weapons release within45 ticks. Use real Fade input
                # before those windows, not a synthetic immunity flag.
                fade_at, until = (80, 115) if row['name'] == 'Sniper' else (1, 45)
                with patch.object(fight, '_shoot', wraps=fight._shoot) as shots:
                    for frame in range(1, until + 1):
                        run.step({source: (0, 0, 8 if frame == fade_at else 0)})
                self.assertGreater(shots.call_count, 0)
                self.assertEqual(health.total, 200)
                self.assertIsNotNone(target.owner.body_script)

    def test_four_actual_npc_weapons_reject_allies_and_static_wall_occlusion(self):
        for row in self.supported_weapon_rows():
            for ally in (False, True):
                with self.subTest(npc=row['name'], ally=ally):
                    run, source, target, _health, _direction = self.fight(row, ally=ally)
                    fight = run.match.combat
                    own = next(t for t in fight._targets() if t.owner is source)
                    health = fight.vitals_of(own)
                    self.arm_weapon(target.owner)
                    collision = fight._collision()
                    if not ally:
                        x, y, z = source.mover.position
                        z += 1.5
                        collision = World([
                            (x - 10, y - 5, z, x + 10, y - 5, z, x + 10, y + 5, z),
                            (x - 10, y - 5, z, x + 10, y + 5, z, x - 10, y + 5, z),
                        ])
                    with (patch.object(fight, '_shoot', wraps=fight._shoot) as shots,
                          patch.object(fight, '_collision', return_value=collision)):
                        for _ in range(180):
                            run.step({})
                    self.assertGreater(shots.call_count, 0)
                    self.assertEqual(health.total, 200)
                    self.assertIsNotNone(target.owner.body_script)

    def test_four_actual_npc_weapon_releases_miss_when_aimed_away(self):
        for row in self.supported_weapon_rows():
            with self.subTest(npc=row['name']):
                run, source, target, _health, _direction = self.fight(row)
                fight = run.match.combat
                own = next(t for t in fight._targets() if t.owner is source)
                health = fight.vitals_of(own)
                self.arm_weapon(target.owner)
                target.owner.yaw = 0  # Face away after queuing the authored volley.
                with patch.object(fight, '_shoot', wraps=fight._shoot) as shots:
                    for _ in range(180):
                        run.step({})
                self.assertGreater(shots.call_count, 0)
                self.assertTrue(all(call.args[0] is target.owner for call in shots.call_args_list))
                self.assertEqual(health.total, 200)
                self.assertFalse(health.dead)
                self.assertIsNotNone(target.owner.body_script)
                self.assertIsNone(target.owner.body_script.component.fault)

    def test_four_actual_npc_weapons_obey_authored_hundred_meter_reach(self):
        for row in self.supported_weapon_rows():
            for distance in (99, 101):
                with self.subTest(npc=row['name'], distance=distance):
                    run, source, target, _health, _direction = self.fight(row)
                    fight = run.match.combat
                    own = next(t for t in fight._targets() if t.owner is source)
                    health = fight.vitals_of(own)
                    x, y, z = source.mover.position
                    target.owner.position = (x, y, z + distance)
                    self.arm_weapon(target.owner)
                    with (patch.object(fight, '_shoot', wraps=fight._shoot) as shots,
                          patch.object(fight, '_collision', return_value=World([]))):
                        for _ in range(220):
                            run.step({})
                    self.assertGreater(shots.call_count, 0)
                    state = shots.call_args_list[0].args[2]
                    reach = state.evaluate(state.node.field('m_FEC435C6'), watch=False)
                    self.assertEqual(reach, 100)
                    if distance < reach:
                        self.assertLess(health.total, 200)
                    else:
                        self.assertEqual(health.total, 200)
                    self.assertIsNotNone(target.owner.body_script)
                    self.assertIsNone(target.owner.body_script.component.fault)

    def test_controlled_actual_five_npcs_can_receive_moira_health_effects_and_clean_up(self):
        for row in self.rows:
            for ally in (False, True):
                with self.subTest(npc=row['name'], ally=ally):
                    run, source, target, health = self.controlled_npc(row, ally=ally)
                    fight = run.match.combat
                    self.assertFalse(fight.is_stunned(source))
                    health.damage(100)
                    before = health.total
                    direction = aim(fight._eye(source), (target.owner.position[0],
                                    target.owner.position[1] + target.height / 2, target.owner.position[2]))
                    for _ in range(20):
                        run.step({source: (*direction, 1 if ally else 2)})
                    self.assertTrue(fight.is_stunned(target.owner))
                    if ally:
                        self.assertGreater(health.total, before)
                        end = fight.stuns[target.entity]
                        while run.frame <= end:
                            run.step({})
                        self.assertNotIn(target.entity, fight.stuns)
                    else:
                        self.assertLess(health.total, before)
                        for kind in health.now:
                            health.now[kind] = 3 if kind == HEALTH_POOL else 0
                        for _ in range(5):
                            run.step({source: (*direction, 2)})
                        self.assertTrue(health.dead)
                        self.assertNotIn(target.entity, fight.stuns)
                        self.assertTrue(target.owner.retain_corpse)

    def test_four_actual_npc_weapons_pause_during_stun_and_rearm_after_expiry(self):
        for row in self.supported_weapon_rows():
            with self.subTest(npc=row['name']):
                run, source, target, _health = self.controlled_npc(row, armed=True)
                fight = run.match.combat
                own = next(t for t in fight._targets() if t.owner is source)
                health = fight.vitals_of(own)
                before = health.total
                end = fight.stuns[target.entity]
                while run.frame < end - 1:
                    run.step({})
                    self.assertEqual(health.total, before)
                while run.frame <= end:
                    run.step({})
                self.assertNotIn(target.entity, fight.stuns)
                # Explicit input rearm; this does not claim autonomous AI or
                # console-control desired-input restoration is available here.
                self.arm_weapon(target.owner)
                for _ in range(180):
                    run.step({})
                self.assertLess(health.total, before)
                self.assertIsNotNone(target.owner.body_script)

    def test_grasp_absorption_prevents_both_orb_operations_reaching_actual_five_npcs(self):
        for row in self.rows:
            for ally in (False, True):
                for active in (False, True):
                    with self.subTest(npc=row['name'], ally=ally, active=active):
                        run, source, target, health, _direction = self.fight(row, ally=ally)
                        match = run.match
                        x, y, z = source.mover.position
                        target.owner.position = (x, y, z + 12)
                        sigma = match.add_player(2, 'Absorber', absorption_tests.absorption.HERO, 1, False)
                        in_world(match, sigma)
                        sigma.client = NpcEventClient(sigma)
                        run.players.append(sigma)
                        match.switch_hero(sigma, heroes()[absorption_tests.absorption.HERO])
                        sigma.team = 1 - source.team
                        sigma.mover.position = [x, y, z + 4]
                        for _ in range(20):
                            absorption_tests.MoiraAbsorptionTests.step(run, source, sigma)
                        absorption_tests.MoiraAbsorptionTests.step(run, source, sigma, grasp=True)
                        instance = next(i for i in sigma.body_script.component.instances.values()
                                        if i.graph.index == absorption_tests.absorption.GRASP)
                        self.assertTrue(instance.states[1].active)
                        if not active:
                            for _ in range(150):
                                absorption_tests.MoiraAbsorptionTests.step(run, source, sigma)
                            self.assertFalse(instance.states[1].active)
                        health.damage(100)
                        before = health.total
                        absorption_tests.MoiraAbsorptionTests.step(run, source, sigma, 16)
                        for _ in range(40):
                            absorption_tests.MoiraAbsorptionTests.step(run, source, sigma, 1 if ally else 2)
                            if match.combat.moira_orbs:
                                break
                        orb = match.combat.moira_orbs[-1]
                        for _ in range(140):
                            absorption_tests.MoiraAbsorptionTests.step(run, source, sigma)
                        if active:
                            self.assertEqual(health.total, before)
                            self.assertNotIn(orb, match.combat.moira_orbs)
                            self.assertEqual(instance.vars[21312].value(), 0 if ally else 100)
                        elif ally:
                            self.assertGreater(health.total, before)
                        else:
                            self.assertLess(health.total, before)

    def test_matrix_prevents_both_orb_operations_reaching_actual_five_npcs(self):
        matrix_tests.MoiraMatrixTests.setUpClass()
        for row in self.rows:
            for ally in (False, True):
                for active in (False, True):
                    with self.subTest(npc=row['name'], ally=ally, active=active):
                        run, source, target, health, _direction = self.fight(row, ally=ally)
                        match = run.match
                        source.body_script.component.set(12918, 0)
                        x, y, z = source.mover.position
                        target.owner.position = (x, y, z + 22)
                        mech = match.add_player(2, 'Matrix', find_hero('dva').guid, 1, False)
                        in_world(match, mech)
                        mech.client = NpcEventClient(mech)
                        run.players.append(mech)
                        self.assertTrue(match.switch_hero(
                            mech, replace(find_hero('dva'), body=matrix_tests.MECH),
                        ))
                        mech.team = 1 - source.team
                        mech.mover.position = [x, y, z + 15]
                        mech.mover.body = None

                        def step(buttons=0, enabled=active, run=run, source=source, mech=mech):
                            run.step({source: (0, 0, buttons), mech: (-32768, 0, 2 if enabled else 0)})

                        for _ in range(100):
                            step(enabled=False)
                        step()
                        matrix = next(i for i in mech.body_script.component.instances.values()
                                      if i.graph.index == 0x2CC)
                        self.assertEqual(matrix.state(3).active, active)
                        health.damage(100)
                        before = health.total
                        step(16)
                        charge_before = source.body_script.component.entity_var(342).value()
                        for _ in range(40):
                            step(1 if ally else 2)
                            if match.combat.moira_orbs:
                                break
                        self.assertTrue(match.combat.moira_orbs)
                        orb = match.combat.moira_orbs[-1]
                        self.assertEqual(orb.healing, ally)
                        for _ in range(140):
                            step()
                        if active:
                            self.assertNotIn(orb, match.combat.moira_orbs)
                            self.assertEqual(health.total, before)
                            self.assertGreater(orb.energy, 0)
                            self.assertEqual(source.body_script.component.entity_var(342).value(),
                                             charge_before)
                        elif ally:
                            self.assertGreater(health.total, before)
                        else:
                            self.assertLess(health.total, before)

    def test_actual_grenade_modifies_all_four_moira_ally_heals_on_all_five_npcs(self):
        for row in self.rows:
            for attack in ('spray', 'tail', 'orb', 'ultimate'):
                results = {}
                for status in ('none', 'block', 'boost'):
                    with self.subTest(npc=row['name'], attack=attack, status=status):
                        run, source, target, health, direction = self.fight(row, ally=True)
                        hero = find_hero('ana')
                        ana = run.match.add_player(2, 'Ana', hero.guid, 1, False)
                        in_world(run.match, ana)
                        ana.client = NpcEventClient(ana)
                        run.players.append(ana)
                        self.assertTrue(run.match.switch_hero(ana, hero))
                        ana.team = 1 - source.team if status == 'block' else source.team
                        x, y, z = target.feet
                        ana.mover.position = [x + 3, y, z]
                        ana.mover.body = None
                        for _ in range(20):
                            run.step({})
                        if status == 'none':
                            for _ in range(21):
                                run.step({})
                        else:
                            biotic_tests.MoiraBioticTests.cast(self, run, ana, target)
                            deadline = (health.healing_block_until if status == 'block'
                                        else health.healing_boost_until)
                            self.assertGreater(deadline, run.match.combat.now)
                        health.damage(health.total - 100)
                        run.match.combat._sync_body_health(target.owner, health)
                        results[status] = biotic_tests.MoiraBioticTests.sample(
                            self, run, source, target, health, attack, direction,
                        )
                self.assertGreater(results['none'], 0)
                self.assertEqual(results['block'], 0)
                self.assertAlmostEqual(results['boost'], results['none'] * 1.5, places=4)

    def test_boosted_actual_healing_orb_keeps_declared_actual_health_budget(self):
        row = next(r for r in self.rows if r['name'] == 'Heavy Assault')
        run, source, target, health, _ = self.fight(row, ally=True)
        hero = find_hero('ana')
        ana = run.match.add_player(2, 'Boost', hero.guid, 1, False)
        in_world(run.match, ana)
        ana.client = NpcEventClient(ana)
        run.players.append(ana)
        self.assertTrue(run.match.switch_hero(ana, hero))
        ana.team = source.team
        x, y, z = target.feet
        ana.mover.position = [x + 3, y, z]
        ana.mover.body = None
        for _ in range(20):
            run.step({})
        biotic_tests.MoiraBioticTests.cast(self, run, ana, target)
        self.assertGreater(health.healing_boost_until, run.match.combat.now)
        health.damage(600)  # sufficient real ordinary-pool deficit for the budget
        before = health.total
        source.body_script.component.set(12918, 0)
        source.body_script.set_ult_charge(0)
        orb = self.launch_orb(run, source, healing=True)
        # Fix the emitted orb's pose to isolate aura accounting from flight.
        # Native movement is covered separately; no health/state/status is forced.
        orb.position = (x, y + target.height / 2, z)
        orb.speed = 0
        for _ in range(400):
            run.step({})
            if orb not in run.match.combat.moira_orbs:
                break
        self.assertNotIn(orb, run.match.combat.moira_orbs)
        self.assertAlmostEqual(health.total - before, 300, places=4)
        self.assertEqual(orb.energy, 0)
        self.assertGreater(orb.left, 0)
        self.assertAlmostEqual(source.body_script.component.entity_var(342).value(), 300, places=3)

    def test_actual_nano_modifies_four_moira_attacks_against_all_five_npcs(self):
        import test_game_moira_nano as nano_tests

        case = nano_tests.MoiraNanoTests()
        for row in self.rows:
            for attack in ('grasp', 'orb', 'melee', 'ultimate'):
                results = {}
                for mode in ('base', 'outgoing', 'incoming'):
                    with self.subTest(npc=row['name'], attack=attack, mode=mode):
                        run, source, target, health, _ = self.fight(row)
                        fight = run.match.combat
                        x, y, z = source.mover.position
                        target.owner.position = (x, y, z + 1.5)
                        if mode != 'base':
                            recipient = source if mode == 'outgoing' else target.owner
                            caster = case.add_ana(run, recipient)
                            caster.client = NpcEventClient(caster)
                            case.cast(run, caster, recipient)
                            deadline = fight.vitals_of(case.target(fight, recipient)).nano_until
                            self.assertGreater(deadline, fight.now)
                            caster.mover.position[0] += 30
                            caster.mover.body = None
                        direction = aim(fight._eye(source), (x, y + target.height / 2, z + 1.5))
                        orb = None
                        if attack == 'orb':
                            orb = self.launch_orb(run, source)
                        elif attack == 'ultimate':
                            self.start_ult(run, source)
                        before = health.total
                        energy = orb.energy if orb else None
                        for i in range(20):
                            button = 2 if attack == 'grasp' else 0x800 if attack == 'melee' and i == 0 else 0
                            run.step({source: (*direction, button)})
                        results[mode] = before - health.total
                        if orb:
                            self.assertAlmostEqual(energy - orb.energy, results[mode], places=4)
                self.assertGreater(results['base'], 0)
                self.assertGreater(results['outgoing'], results['base'])
                self.assertLess(results['incoming'], results['base'])
                self.assertGreater(results['incoming'], 0)
                # Armour is applied after Nano; its flat subtraction prevents a
                # universal HP ratio. Unarmoured bodies retain the exact ratio.
                if row['armour'] == 0:
                    self.assertAlmostEqual(results['outgoing'], results['base'] * 1.5, places=4)
                    self.assertAlmostEqual(results['incoming'], results['base'] * .5, places=4)

    def test_actual_nano_modifies_four_npc_weapon_releases_against_moira(self):
        import test_game_moira_nano as nano_tests

        case = nano_tests.MoiraNanoTests()
        for row in self.supported_weapon_rows():
            results = {}
            for mode in ('base', 'outgoing', 'incoming'):
                with self.subTest(npc=row['name'], mode=mode):
                    run, source, target, _, _ = self.fight(row)
                    fight = run.match.combat
                    if mode != 'base':
                        recipient = target.owner if mode == 'outgoing' else source
                        caster = case.add_ana(run, recipient)
                        caster.client = NpcEventClient(caster)
                        case.cast(run, caster, recipient)
                        deadline = fight.vitals_of(case.target(fight, recipient)).nano_until
                        self.assertGreater(deadline, fight.now)
                        caster.mover.position[0] += 30
                        caster.mover.body = None
                    health = fight.vitals_of(case.target(fight, source))
                    before = health.total
                    self.arm_weapon(target.owner)
                    # Observe the first real health-changing release. Sniper's
                    # windup exceeds60 frames; a fixed180-frame burst would kill
                    # Moira under boosted Heavy fire and hide the scalar in a cap.
                    for _ in range(180):
                        run.step({})
                        if health.total < before:
                            break
                    results[mode] = before - health.total
            self.assertGreater(results['base'], 0)
            self.assertAlmostEqual(results['outgoing'], results['base'] * 1.5, places=4)
            self.assertAlmostEqual(results['incoming'], results['base'] * .5, places=4)

    def test_all_five_sleeping_npcs_wake_on_four_moira_attacks_but_not_four_ally_heals(self):
        import test_game_moira_nano as nano_tests
        import test_game_moira_sleep as sleep_tests

        case = nano_tests.MoiraNanoTests()
        for row in self.rows:
            for attack in ('grasp', 'orb_damage', 'melee', 'ultimate_damage', 'spray', 'tail',
                           'orb', 'ultimate'):
                ally = attack in ('spray', 'tail', 'orb', 'ultimate')
                with self.subTest(npc=row['name'], attack=attack):
                    run, source, target, health, _ = self.fight(row, ally=ally)
                    fight = run.match.combat
                    x, y, z = source.mover.position
                    target.owner.position = (x, y, z + 1.5)
                    caster = case.add_ana(run, target.owner)
                    caster.client = NpcEventClient(caster)
                    caster.team = 1 - target.team
                    deadline = sleep_tests.MoiraSleepTests.cast_at(run, caster, target.owner)
                    caster.mover.position[0] += 30
                    caster.mover.body = None
                    direction = aim(fight._eye(source), (x, y + target.height / 2, z + 1.5))
                    if ally:
                        health.damage(100)  # explicit wounded setup; does not execute damage/wake service
                        fight._sync_body_health(target.owner, health)
                        given = biotic_tests.MoiraBioticTests.sample(self, run, source, target, health,
                                                                   attack, direction)
                        self.assertGreater(given, 0)
                        self.assertEqual(health.sleep_until, deadline)
                        self.assertTrue(fight.is_asleep(target.owner))
                    else:
                        for _ in range(33):
                            run.step({})  #224E rejects early wake damage for0.5s
                        before = health.total
                        if attack == 'orb_damage':
                            self.launch_orb(run, source)
                        elif attack == 'ultimate_damage':
                            self.start_ult(run, source)
                        for i in range(30):
                            button = 2 if attack == 'grasp' else 0x800 if attack == 'melee' and i == 0 else 0
                            run.step({source: (*direction, button)})
                            if health.total < before:
                                break
                        self.assertLess(health.total, before)
                        self.assertTrue(fight.is_asleep(target.owner))
                        self.assertLessEqual(health.sleep_until - fight.now, .5 + 1e-6)
                        recovery = health.sleep_until
                        while fight.now < recovery:
                            run.step({})
                        self.assertFalse(fight.is_asleep(target.owner))

    def test_four_actual_npc_weapons_pause_for_sleep_and_can_explicitly_rearm(self):
        import test_game_moira_nano as nano_tests
        import test_game_moira_sleep as sleep_tests

        case = nano_tests.MoiraNanoTests()
        for row in self.supported_weapon_rows():
            with self.subTest(npc=row['name']):
                run, source, target, _, _ = self.fight(row)
                fight = run.match.combat
                caster = case.add_ana(run, source)
                caster.client = NpcEventClient(caster)
                self.arm_weapon(target.owner)
                deadline = sleep_tests.MoiraSleepTests.cast_at(run, caster, target.owner)
                caster.mover.position[0] += 30
                caster.mover.body = None
                own = fight.vitals_of(case.target(fight, source))
                # A projectile released before sleep remains in flight. Gate
                # new authored volleys, rather than freezing those old bullets.
                with patch.object(fight, '_shoot', wraps=fight._shoot) as shots:
                    while fight.now + combat.TICK < deadline:
                        run.step({})
                    self.assertEqual(shots.call_count, 0)
                while fight.now < deadline:
                    run.step({})
                self.assertFalse(fight.is_asleep(target.owner))
                before = own.total
                self.arm_weapon(target.owner)
                for _ in range(180):
                    run.step({})
                    if own.total < before:
                        break
                self.assertLess(own.total, before)
                self.assertIsNotNone(target.owner.body_script)

    def graviton_at_npc(self, run, source, target):
        match, fight = run.match, run.match.combat
        match.game_map = replace(match.game_map, free_for_all=False)
        x, y, z = target.owner.position
        zarya = find_hero("zarya")
        caster = match.add_player(len(run.players) + 1, "Zarya", zarya.guid, source.team, False)
        in_world(match, caster)
        caster.client = NpcEventClient(caster)
        run.players.append(caster)
        self.assertTrue(match.switch_hero(caster, zarya))
        caster.team = source.team
        caster.mover.position[:] = (x, y, z + 8)
        caster.mover.body = None
        for _ in range(30):
            run.step({})
        direction = aim(fight._eye(caster), (x + 2.5, y + 0.85, z))
        script = caster.body_script
        script.set_ult_charge(script.component.entity_var(ULT_COST).value())
        run.step({caster: (*direction, 32)})
        rocket, = fight.rockets
        self.assertEqual(rocket.spawn, graviton.FIELD_ENTITY)
        return graviton_tests.MoiraGravitonTests.until_landed(self, run, rocket)

    def test_actual_graviton_pulls_all_five_npcs_and_grasp_still_hits(self):
        for row in self.rows:
            with self.subTest(npc=row["name"]):
                run, source, target, vitals, _ = self.fight(row)
                fight, bot = run.match.combat, target.owner
                field = self.graviton_at_npc(run, source, target)
                before = bot.position
                health_before = vitals.total
                for _ in range(12):
                    run.step({})
                    self.assertIn(bot.entity, fight.zarya_gravitons.pulled)
                self.assertGreater(bot.position[0], before[0] + 0.3)
                self.assertLess(vitals.total, health_before)

                x, y, z = bot.position
                goal = (x, y, z + 30)
                bot.route, bot.index, bot.target = [bot.position, goal], 1, goal
                bot.keys, bot.wait, bot.last_tick = True, 0, run.frame
                for _ in range(16):
                    x, y, z = bot.position
                    direction = aim(fight._eye(source), (x, y + target.height / 2, z))
                    run.step({source: (*direction, 2)})
                self.assertGreater(source.stats.value(stats.HERO_DAMAGE), 0)
                while run.frame < field.end_tick:
                    run.step({})
                    self.assertEqual(bot.target, goal)
                    self.assertEqual(bot.held, (0, 0))
                self.assertNotIn(bot.entity, fight.zarya_gravitons.pulled)
                self.assertNotIn(bot.entity, fight.zarya_gravitons.npc_movers)
                self.assertEqual(bot.velocity, (0, 0, 0))
                before = bot.position
                for _ in range(10):
                    run.step({})
                self.assertGreater(bot.position[2], before[2])
                self.assertLessEqual(bot.position[2] - before[2], bot.speed * 10 * combat.TICK)

    def test_actual_graviton_releases_dead_and_removed_npcs(self):
        row = next(row for row in self.rows if row["name"] == "Trooper")
        for removed in (False, True):
            with self.subTest(removed=removed):
                run, source, target, vitals, _ = self.fight(row)
                fight, bot = run.match.combat, target.owner
                field = self.graviton_at_npc(run, source, target)
                run.step({})
                self.assertIn(bot.entity, fight.zarya_gravitons.npc_movers)
                if removed:
                    self.assertEqual(npc.remove_spawned(run.match), (bot.entity,))
                else:
                    vitals.damage(vitals.total - 0.04)
                    run.step({})
                    self.assertTrue(vitals.dead)
                    self.assertTrue(bot.dead)
                self.assertNotIn(bot.entity, fight.zarya_gravitons.pulled)
                self.assertNotIn(bot.entity, fight.zarya_gravitons.npc_movers)
                before = bot.position
                run.step({})
                self.assertIn(field, fight.zarya_gravitons.fields)
                self.assertNotIn(bot.entity, fight.zarya_gravitons.pulled)
                self.assertEqual(bot.position, before)

    def test_two_actual_gravitons_apply_lethal_damage_before_npc_pull(self):
        row = next(row for row in self.rows if row["name"] == "Trooper")
        run, source, target, vitals, _ = self.fight(row)
        fight, bot = run.match.combat, target.owner
        first = self.graviton_at_npc(run, source, target)
        second = self.graviton_at_npc(run, source, target)
        self.assertNotEqual(first.source_body, second.source_body)
        self.assertIn(bot.entity, fight.zarya_gravitons.pulled)
        vitals.damage(vitals.total - 0.12)
        before = bot.position
        run.step({})
        self.assertTrue(bot.dead)
        self.assertEqual(vitals.killer, second.source_body)
        self.assertNotIn(bot.entity, fight.zarya_gravitons.pulled)
        self.assertNotIn(bot.entity, fight.zarya_gravitons.npc_movers)
        self.assertEqual(bot.position, before)
        self.assertEqual(bot.velocity, (0, 0, 0))

    def test_actual_graviton_interrupts_heavy_charge(self):
        row = next(row for row in self.rows if row["name"] == "Heavy Assault")
        run, source, target, _, _ = self.fight(row)
        fight, bot = run.match.combat, target.owner
        bot.yaw = 16384
        component = bot.body_script.component
        component.edges([(35, True)])
        component.run_now()
        component.edges([(35, False)])
        component.run_now()
        charge = heavy_charge.active(bot)
        self.assertIsNotNone(charge)
        field = self.graviton_at_npc(run, source, target)
        self.assertIs(heavy_charge.active(bot), charge)
        run.step({})
        self.assertIn(bot.entity, fight.zarya_gravitons.pulled)
        self.assertFalse(heavy_charge.travelling(charge))
        self.assertNotIn(bot.entity, fight.heavy_charge_pins)
        for _ in range(12):
            run.step({})
        self.assertIn(field, fight.zarya_gravitons.fields)
        self.assertIn(bot.entity, fight.zarya_gravitons.pulled)
        self.assertIsNone(heavy_charge.active(bot))

    def test_all_five_sleeping_npc_routes_pause_without_catchup_then_resume(self):
        import test_game_moira_nano as nano_tests
        import test_game_moira_sleep as sleep_tests

        case = nano_tests.MoiraNanoTests()
        for row in self.rows:
            with self.subTest(npc=row['name']):
                run, source, target, _, _ = self.fight(row)
                fight = run.match.combat
                bot = target.owner
                caster = case.add_ana(run, source)
                caster.client = NpcEventClient(caster)
                deadline = sleep_tests.MoiraSleepTests.cast_at(run, caster, bot)
                x, y, z = bot.position
                goal = (x, y, z + 30)
                bot.route, bot.index, bot.target = [bot.position, goal], 1, goal
                bot.keys, bot.wait, bot.last_tick = True, 0, run.frame
                before = bot.position
                run.frame += 100  # one scheduler gap entirely inside sleep
                run.match.update(run.frame * combat.TICK, run.frame)
                self.assertEqual(bot.position, before)
                self.assertEqual(bot.target, goal)
                while fight.now + combat.TICK < deadline:
                    run.step({})
                    self.assertEqual(bot.position, before)
                for _ in range(10):
                    run.step({})
                self.assertFalse(fight.is_asleep(bot))
                self.assertGreater(bot.position[2], before[2])
                self.assertLessEqual(bot.position[2] - before[2], bot.speed * 11 * combat.TICK)


    def test_four_actual_npc_weapons_stop_at_ice_wall(self):
        for row in self.supported_weapon_rows():
            for npc_ally in (False, True):
                with self.subTest(npc=row["name"], npc_ally=npc_ally):
                    run, source, target, _, _ = self.fight(row)
                    match, actor = run.match, target.owner
                    fight = match.combat
                    team = actor.team if npc_ally else source.team
                    _, _, pillar = self.deploy_npc_cover(run, source, team, 1.5)
                    health = fight.vitals_of(next(t for t in fight._targets() if t.owner is source))
                    self.arm_weapon(actor)
                    with patch.object(fight, "_shoot", wraps=fight._shoot) as shots:
                        for _ in range(130 if row["name"] == "Sniper" else 42):
                            run.step({})
                        self.assertGreater(shots.call_count, 0)
                    self.assertEqual(health.total, 200)
                    self.assertIn(pillar.entity, fight.walls.pieces)
                    if npc_ally:
                        self.assertEqual(pillar.health, 400)
                    else:
                        self.assertLess(pillar.health, 400)
                    self.assertIsNone(actor.body_script.component.fault)

    @unittest.skipUnless(os.environ.get("ORPHEUS_SYMMETRA_COLLISION"),
                         "verified build 104319 Photon Barrier mesh required")
    def test_photon_barrier_routes_moira_grasp_and_autonomous_npc_weapons_by_team(self):
        for row in self.supported_weapon_rows():
            for npc_ally in (False, True):
                with self.subTest(npc=row["name"], npc_ally=npc_ally):
                    run, source, target, npc_health, _ = self.fight(row)
                    match, actor = run.match, target.owner
                    fight = match.combat
                    x, y, z = source.mover.position
                    actor.position = (x, y, z + 8)
                    hero = find_hero("symmetra")
                    team = actor.team if npc_ally else source.team
                    caster = match.add_player(2, "Symmetra", hero.guid, team, False)
                    in_world(match, caster)
                    caster.client = NpcEventClient(caster)
                    run.players.append(caster)
                    self.assertTrue(match.switch_hero(caster, hero))
                    caster.team = team
                    caster.mover.position[:] = (x, y, z + 6)
                    for _ in range(25):
                        run.step({})
                    instance = next(i for i in caster.body_script.component.instances.values()
                                    if i.graph.index == photon_barrier.GRAPH)
                    wall = photon_tests.MoiraPhotonTests().deploy(run, caster, instance)
                    caster.mover.position[:] = (x + 6, y, z - 4)
                    caster.mover.body = None

                    npc_before = npc_health.total
                    direction = aim(fight._eye(source), (x, y + target.height / 2, z + 8))
                    for _ in range(12):
                        run.step({source: (*direction, 2)})
                        if wall.health < 4000 or npc_health.total < npc_before:
                            break
                    if npc_ally:
                        self.assertAlmostEqual(4000 - wall.health, 0.8, places=3)
                        self.assertEqual(npc_health.total, npc_before)
                    else:
                        self.assertEqual(wall.health, 4000)
                        self.assertLess(npc_health.total, npc_before)

                    health = fight.vitals_of(next(t for t in fight._targets() if t.owner is source))
                    before, shield_before = health.total, wall.health
                    actor.spawner["autonomous_targeting"] = True
                    for _ in range(150):
                        run.step({})
                        if health.total < before or wall.health < shield_before:
                            break
                    self.assertEqual(actor.attack_target, source.entity)
                    if npc_ally:
                        self.assertLess(health.total, before)
                        self.assertEqual(wall.health, shield_before)
                    else:
                        self.assertEqual(health.total, before)
                        self.assertLess(wall.health, shield_before)
                        wall.health = 1
                        for _ in range(320):
                            run.step({})
                            if health.total < before:
                                break
                        self.assertEqual(wall.health, 0)
                        self.assertFalse(fight.photon_barriers.actors)
                        self.assertLess(health.total, before)
                    self.assertIsNone(actor.body_script.component.fault)


    def deploy_npc_cover(self, run, source, team, distance):
        match = run.match
        fight = match.combat
        x, y, z = source.mover.position
        mei = find_hero("mei")
        caster = match.add_player(2, "Mei", mei.guid, team, False)
        in_world(match, caster)
        caster.client = NpcEventClient(caster)
        run.players.append(caster)
        self.assertTrue(match.switch_hero(caster, mei))
        caster.team = team
        caster.mover.position = [x, y, z - 6]
        caster.mover.body = None
        for _ in range(25):
            run.step({})
        direction = aim(fight._eye(caster), (x, y, z + distance))
        for button in (16, 0, 1, 0):
            run.step({caster: (*direction, button)})
        self.assertEqual(len(fight.walls.pieces), 5)
        pillar = min(fight.walls.pieces.values(), key=lambda piece: abs(piece.feet[0] - x))
        return caster, direction, pillar

    def test_ice_wall_pauses_manual_npc_fire_until_cancel(self):
        from ow174.game import npc_control

        for row in self.supported_weapon_rows():
            with self.subTest(npc=row["name"]):
                run, source, target, _, direction = self.fight(row)
                fight, actor = run.match.combat, target.owner
                run.step({source: (*direction, 0)})
                self.assertEqual(npc_control.start(run.match, source), actor.entity)
                run.step({})
                self.assertTrue(actor.attack_input)
                caster, wall_aim, _ = self.deploy_npc_cover(run, source, source.team, 1.5)
                run.step({source: (*direction, 0)})
                self.assertIsNone(npc_control.selected(run.match, source))
                self.assertFalse(actor.attack_input)
                self.assertEqual(actor.attack_target, source.entity)
                health = fight.vitals_of(next(t for t in fight._targets() if t.owner is source))
                before = health.total
                for _ in range(30):
                    run.step({})
                    self.assertFalse(actor.attack_input)
                self.assertEqual(health.total, before)
                run.step({caster: (*wall_aim, 16)})
                self.assertFalse(fight.walls.pieces)
                for _ in range(180):
                    run.step({})
                    if health.total < before:
                        break
                self.assertTrue(actor.attack_input)
                self.assertLess(health.total, before)
                self.assertIsNone(actor.body_script.component.fault)

    def test_actual_npc_melee_hits_ice_wall_before_moira(self):
        for name, graph_index, ability_node, amount in (
            ("Trooper", 0x1422, 8, 30), ("Heavy Assault", 0x140E, 9, 80),
        ):
            row = next(row for row in self.rows if row["name"] == name)
            for npc_ally in (False, True):
                with self.subTest(npc=name, npc_ally=npc_ally):
                    run, source, target, _, _ = self.fight(row)
                    fight, actor = run.match.combat, target.owner
                    x, y, z = source.mover.position
                    actor.position = (x, y, z + 2)
                    actor.yaw = -32768
                    team = actor.team if npc_ally else source.team
                    caster, direction, pillar = self.deploy_npc_cover(run, source, team, 1.05)
                    own = next(item for item in fight._targets() if item.owner is source)
                    health = fight.vitals_of(own)
                    component = actor.body_script.component
                    instance = next(i for i in component.instances.values() if i.graph.index == graph_index)
                    ability = instance.node_state(instance.graph.node(ability_node))
                    actor.firing = True
                    for _ in range(12):
                        run.step({})

                    def melee():
                        for down in (True, False):
                            component.edges([(31, down)])
                            component.run_now()
                        for _ in range(43):
                            run.step({})

                    before = tuple(source.mover.position)
                    melee()
                    self.assertEqual(health.total, 200)
                    self.assertEqual(tuple(source.mover.position), before)
                    self.assertEqual(pillar.health, 400 if npc_ally else 400 - amount)
                    self.assertGreater(ability.remaining(), 0)
                    run.step({caster: (*direction, 16)})
                    self.assertFalse(fight.walls.pieces)
                    for _ in range(75):
                        if ability.remaining() <= 0:
                            break
                        run.step({})
                    self.assertEqual(ability.remaining(), 0)
                    melee()
                    self.assertEqual(health.total, 200 - amount)
                    self.assertIsNone(component.fault)
