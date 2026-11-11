"""Explicit external five-NPC combat acceptance. Missing behavior remains a failure.

Set ORPHEUS_NPC_FIXTURE_DIR to prepared NPC data and ORPHEUS_RUNTIME_DIR to
an external runtime containing all five authored mover rows; both are read only.
These are stand-in collision/transport scenarios, not authored AI/client acceptance.
"""

import os
import unittest
from dataclasses import replace
from pathlib import Path
from unittest.mock import patch

import test_game_ana_projected_barrier as projected
from test_game_ana import ANA, TEAM_MAP, set_static_wall
from test_game_ana_orisa_barrier import AnaOrisaBarrierTests
from test_game_combat import FakeClient, Run, aim

from ow174.game import assassin, assassin_controller, combat, enforcer_controller, matrix, mover, npc, sombra_control, stats
from ow174.game.body_vm_snapshot import export_component_vm, snapshot_digest
from ow174.game.content import find_hero, heroes
from ow174.game.script import expr, graph, nodes, runtime
from ow174.game.script.driver import ULT_COST
from ow174.game.server_snapshot import _Graph
from ow174.game.world import ARMOUR_POOL, HEALTH_POOL, SHIELDS_POOL

FIXTURE = os.environ.get("ORPHEUS_NPC_FIXTURE_DIR")


class NPCFixtureClient(FakeClient):
    def __init__(self, player):
        super().__init__(player)
        self.bot_deaths = []

    def bot_stream(self, _body):
        return None  # no observer-stream transport is modelled in this combat fixture

    def queue_bot_death(self, entity, snapshot):
        self.bot_deaths.append((entity, snapshot))

    def queue_bot_death_update(self, entity, snapshot):
        self.bot_deaths.append((entity, snapshot))


def clear_graph_caches():
    for function in (graph._data, graph._records, graph.graph, graph.mode_variables):
        function.cache_clear()


@unittest.skipUnless(FIXTURE, "explicit external ORPHEUS_NPC_FIXTURE_DIR required")
class AnaRealNPCTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.original_graph_path = graph.GRAPHS_PATH
        directory = Path(FIXTURE)
        cls.catalog = npc.load_catalog(directory / "pve_npcs_174.json")
        if {d.name for d in cls.catalog.values()} != {
            "Trooper", "Enforcer", "Sniper", "Assassin", "Heavy Assault"
        }:
            raise ValueError("the complete actual five-NPC catalog is required")
        # Collision acceptance must never silently use mover_data's generic
        # fallback for these real bodies. Load and inspect the actual table.
        mover.mover_data(next(iter(cls.catalog.values())).body)
        table = mover.loaded_mover_table()
        missing = [definition.name for definition in cls.catalog.values()
                   if table is None or definition.body not in table]
        if missing:
            raise ValueError('authored NPC mover rows required in ORPHEUS_RUNTIME_DIR: ' + ', '.join(missing))
        graph.GRAPHS_PATH = directory / "game_graphs_174.json.gz"
        clear_graph_caches()

    @classmethod
    def tearDownClass(cls):
        graph.GRAPHS_PATH = cls.original_graph_path
        clear_graph_caches()

    def fight(self, definition, *, ally=False, distance=6.0, with_zarya=False,
              acquire=False, pursue=False, specials=False):
        run = Run(TEAM_MAP, ('Ana', 'Zarya')) if with_zarya else Run(TEAM_MAP)
        ana = run.players[0]
        match = run.match
        match.bots.clear()
        match._pending_bot_scripts.clear()
        self.assertTrue(match.switch_hero(ana, heroes()[ANA]))
        ana.client = NPCFixtureClient(ana)
        ana.team = 0
        x, y, z = ana.mover.position
        bot = npc.spawn(match, definition, (x, y, z + distance), team=0 if ally else 1,
                        yaw=0 if acquire or pursue else -32768, acquire=acquire, pursue=pursue,
                        specials=specials)
        for _ in range(25):
            run.step({})
        self.assertIsNotNone(bot.body_script)
        self.assertIsNone(bot.body_script.component.fault)
        entry = next(t for t in match.combat._targets() if t.owner is bot)
        return run, ana, bot, entry

    def projected_fight(self, definition, *, distance=6.0):
        run, ana, bot, entry = self.fight(definition, distance=distance, with_zarya=True)
        caster = run.players[1]
        self.assertTrue(run.match.switch_hero(caster, find_hero('Zarya')))
        caster.client = NPCFixtureClient(caster)
        caster.team = bot.team
        x, y, z = ana.mover.position
        caster.mover.position = [x + 3, y, z + 1]
        ana.body_script.ult_seconds = 0
        ana.body_script.set_ult_charge(0)
        for _ in range(25):
            run.step({})
        return run, ana, bot, entry, caster

    def project(self, run, caster, entry):
        direction = aim(run.match.combat._eye(caster),
                        (entry.feet[0], entry.feet[1] + entry.height / 2, entry.feet[2]))
        run.step({caster: (*direction, 16)})
        barrier = run.match.combat.body_barriers[entry.entity]
        self.assertEqual((barrier.source, barrier.target, barrier.health),
                         (caster.body, entry.entity, 200))
        return barrier

    def test_zarya_primary_track_ray_resolves_all_five_authored_npc_capsules(self):
        for definition in self.catalog.values():
            with self.subTest(npc=definition.name):
                run, ana, bot, entry = self.fight(definition, with_zarya=True)
                caster = run.players[1]
                self.assertTrue(run.match.switch_hero(caster, find_hero('Zarya')))
                caster.team = ana.team
                x, y, z = ana.mover.position
                caster.mover.position = [x + 3, y, z]
                caster.mover.body = None
                for _ in range(25):
                    run.step({})
                fight = run.match.combat
                direction = aim(fight._eye(caster),
                                (entry.feet[0], entry.feet[1] + entry.height / 2, entry.feet[2]))
                vitals = fight.vitals_of(entry)
                before = vitals.total
                for _ in range(10):
                    run.step({caster: (*direction, 1)})
                weapon = next(i for i in caster.body_script.component.instances.values()
                              if i.graph.index == 0x241)
                self.assertEqual(weapon.vars[536].value().id, bot.entity)
                self.assertAlmostEqual(vitals.total, before - 12)
                self.assertIsNone(caster.body_script.component.fault)

    def test_projected_barrier_absorbs_ana_contacts_on_all_five_npc_bodies(self):
        for definition in self.catalog.values():
            for button, scoped, expected in ((1, False, 130), (1, True, 130), (8, False, 195),
                                              (16, False, 150), (0x800, False, 170)):
                with self.subTest(npc=definition.name, button=button, scoped=scoped):
                    run, ana, bot, entry, caster = self.projected_fight(
                        definition, distance=1.5 if button == 0x800 else 6)
                    fight = run.match.combat
                    before = fight.vitals_of(entry).total
                    barrier = self.project(run, caster, entry)
                    self.attack(run, ana, entry, button, scoped=scoped)
                    self.assertEqual(fight.vitals_of(entry).total, before)
                    self.assertEqual(barrier.health, expected)
                    self.assertFalse(fight.is_asleep(bot))
                    self.assertLessEqual(fight.vitals_of(entry).healing_block_until, fight.now)
                    self.assertFalse(any(hit.target == entry.entity for hit in fight.timed_hits))
                    self.assertEqual(ana.stats.value(stats.HERO_DAMAGE), 0)
                    self.assertEqual(ana.body_script.component.vars[342].value(), 0)

    def test_projected_barrier_allows_friendly_ana_healing_on_all_five_npcs(self):
        for definition in self.catalog.values():
            for button, healing in ((1, 70), (16, 100)):
                with self.subTest(npc=definition.name, button=button):
                    run, ana, bot, entry, caster = self.projected_fight(definition)
                    ana.team = bot.team
                    fight = run.match.combat
                    vitals = fight.vitals_of(entry)
                    vitals.damage(100)
                    before = vitals.total
                    barrier = self.project(run, caster, entry)
                    self.attack(run, ana, entry, button)
                    self.assertEqual(vitals.total, before + healing)
                    self.assertEqual(barrier.health, 200)

    def test_projected_barrier_on_all_five_npcs_credits_caster_energy_at_rifle_impact(self):
        for definition in self.catalog.values():
            for scoped in (False, True):
                with self.subTest(npc=definition.name, scoped=scoped):
                    run, ana, _bot, entry, caster = self.projected_fight(definition)
                    barrier = self.project(run, caster, entry)
                    direction = aim(run.match.combat._eye(ana),
                                    (entry.feet[0], entry.feet[1] + entry.height / 2, entry.feet[2]))
                    if scoped:
                        for _ in range(35):
                            run.step({ana: (*direction, 2)})
                    run.step({ana: (*direction, 3 if scoped else 1)})
                    for _ in range(60):
                        if barrier.health < 200:
                            break
                        run.step({ana: (*direction, 0)})
                    self.assertEqual(barrier.health, 130)
                    self.assertEqual(caster.body_script.component.entity_var(87, create=False).value(), 14)

    def test_projected_barrier_cleanses_existing_ana_effects_on_all_five_npcs(self):
        for definition in self.catalog.values():
            for sleep in (False, True):
                with self.subTest(npc=definition.name, sleep=sleep):
                    run, ana, bot, entry, caster = self.projected_fight(definition)
                    fight = run.match.combat
                    self.attack(run, ana, entry, 8 if sleep else 16)
                    if sleep:
                        self.assertTrue(fight.is_asleep(bot))
                    else:
                        self.assertGreater(fight.vitals_of(entry).healing_block_until, fight.now)
                        direction = aim(fight._eye(ana), (entry.feet[0], entry.feet[1] + 1, entry.feet[2]))
                        run.step({ana: (*direction, 1)})
                        for _ in range(10):
                            run.step({ana: (*direction, 0)})
                        self.assertTrue(any(hit.target == entry.entity for hit in fight.timed_hits))
                    self.project(run, caster, entry)
                    before = fight.vitals_of(entry).total
                    self.assertFalse(fight.is_asleep(bot))
                    self.assertEqual(fight.vitals_of(entry).healing_block_until, 0)
                    self.assertFalse(any(hit.target == entry.entity for hit in fight.timed_hits))
                    for _ in range(30):
                        run.step({})
                    self.assertEqual(fight.vitals_of(entry).total, before)

    def test_projected_npc_death_or_manual_removal_retires_pool_without_refill(self):
        for definition in self.catalog.values():
            for remove in (False, True):
                with self.subTest(npc=definition.name, remove=remove):
                    run, ana, bot, entry, caster = self.projected_fight(definition)
                    fight = run.match.combat
                    self.project(run, caster, entry)
                    if remove:
                        self.assertIn(bot.entity, npc.remove_spawned(run.match))
                    else:
                        fight._damage(ana, entry, 10000, False, None, run.frame)
                        fight._damage(ana, entry, 10000, False, None, run.frame)
                        self.assertTrue(fight.vitals_of(entry).dead)
                    self.assertNotIn(entry.entity, fight.body_barriers)
                    for _ in range(10):
                        run.step({})
                    self.assertNotIn(entry.entity, fight.body_barriers)

    def attack(self, run, ana, entry, button, *, scoped=False):
        direction = aim(run.match.combat._eye(ana),
                        (entry.feet[0], entry.feet[1] + 1, entry.feet[2]))
        if scoped:
            for _ in range(35):
                run.step({ana: (*direction, 2)})
        run.step({ana: (*direction, button | (2 if scoped else 0))})
        for _ in range(80):
            run.step({})

    def test_rifle_modes_damage_all_five_actual_npc_bodies(self):
        for definition in self.catalog.values():
            for scoped in (False, True):
                with self.subTest(npc=definition.name, scoped=scoped):
                    run, ana, _bot, entry = self.fight(definition)
                    vitals = run.match.combat.vitals_of(entry)
                    before = vitals.total
                    self.attack(run, ana, entry, 1, scoped=scoped)
                    self.assertEqual(vitals.total, before - 70)

    def test_authored_five_body_envelopes_distinguish_grazing_hits_and_misses(self):
        expected = {'Trooper': (1.7, .5), 'Enforcer': (1.9, .5), 'Sniper': (1.75, .5),
                    'Assassin': (1.5, .5), 'Heavy Assault': (2.3, .7)}
        for definition in self.catalog.values():
            height, radius = expected[definition.name]
            for scoped in (False, True):
                for hit in (False, True):
                    with self.subTest(npc=definition.name, scoped=scoped, hit=hit):
                        run, ana, _bot, entry = self.fight(definition)
                        fight = run.match.combat
                        self.assertEqual((entry.height, entry.radius), (height, radius))
                        vitals = fight.vitals_of(entry)
                        before = vitals.total
                        point = (entry.feet[0] + radius + (-.1 if hit else .15),
                                 entry.feet[1] + height / 2, entry.feet[2])
                        direction = aim(fight._eye(ana), point)
                        if scoped:
                            for _ in range(35):
                                run.step({ana: (*direction, 2)})
                        run.step({ana: (*direction, 1 | (2 if scoped else 0))})
                        for _ in range(80):
                            run.step({})
                        self.assertEqual(vitals.total, before - (70 if hit else 0))

    def test_both_rifle_modes_obey_authored100m_contact_range_for_all_five(self):
        for definition in self.catalog.values():
            for scoped in (False, True):
                for distance in (99, 101):
                    with self.subTest(npc=definition.name, scoped=scoped, distance=distance):
                        run, ana, _bot, entry = self.fight(definition, distance=distance)
                        fight = run.match.combat
                        vitals = fight.vitals_of(entry)
                        before = vitals.total
                        direction = aim(fight._eye(ana), (entry.feet[0], entry.feet[1] + 1, entry.feet[2]))
                        if scoped:
                            for _ in range(35):
                                run.step({ana: (*direction, 2)})
                        run.step({ana: (*direction, 1 | (2 if scoped else 0))})
                        for _ in range(125):
                            run.step({})
                        self.assertEqual(vitals.total, before - (70 if distance == 99 else 0))

    def test_npc_controller_records_remain_unknown_while_player_queries_work(self):
        from ow174.game.script import controller_queries as query
        from ow174.game.script import expr

        for definition in self.catalog.values():
            with self.subTest(npc=definition.name):
                _run, ana, bot, _entry = self.fight(definition)
                instance = next(iter(bot.body_script.component.instances.values()))
                for cls, field in ((query.RELATED, 'm_801B298F'), (query.PREDICATE, 'm_entity')):
                    cfg = {'$': cls, field: expr.Entity(bot.entity)}
                    self.assertIs(query.evaluate(instance.context(), cfg), NotImplemented)
                    cfg[field] = expr.Entity(ana.body)
                    expected = expr.Entity(ana.entity) if cls == query.RELATED else True
                    self.assertEqual(instance.evaluate(cfg), expected)

    def test_native_health_queries_read_each_real_npc_from_both_body_hosts(self):
        from test_game_ana_health_queries import read

        from ow174.game.script import expr, health_queries

        for definition in self.catalog.values():
            with self.subTest(npc=definition.name):
                run, ana, bot, entry = self.fight(definition)
                fight = run.match.combat
                vitals = fight.vitals_of(entry)
                maximum = vitals.total
                instance = next(iter(bot.body_script.component.instances.values()))

                def npc_read(maximum_query=False, *, instance=instance, entity=bot.entity):
                    return instance.evaluate({
                        "$": health_queries.MAXIMUM if maximum_query else health_queries.CURRENT,
                        "m_entity": expr.Entity(entity),
                    })

                self.assertEqual((read(ana, bot.entity), npc_read()), (maximum, maximum))
                self.assertEqual(npc_read(True), maximum)
                self.attack(run, ana, entry, 1)
                self.assertEqual((read(ana, bot.entity), npc_read()), (maximum - 70, maximum - 70))
                self.assertEqual((read(ana, bot.entity, maximum=True), npc_read(True)),
                                 (maximum, maximum))
                fight._damage(ana, entry, 100000, False, None, run.frame)
                self.assertEqual((read(ana, bot.entity), npc_read()), (0, 0))
                self.assertEqual(npc_read(True), maximum)

    def test_grenade_damages_and_blocks_healing_for_all_five_bodies(self):
        for definition in self.catalog.values():
            with self.subTest(npc=definition.name):
                run, ana, bot, entry = self.fight(definition)
                fight = run.match.combat
                vitals = fight.vitals_of(entry)
                before = vitals.total
                self.attack(run, ana, entry, 0x10)
                self.assertEqual(vitals.total, before - 50)
                self.assertGreater(vitals.healing_block_until, fight.now)
                fight.heal_area(bot, entry.feet, 1, 40)
                self.assertEqual(vitals.total, before - 50)

    def test_rifle_and_grenade_heal_allied_actual_npc_bodies(self):
        for definition in self.catalog.values():
            with self.subTest(npc=definition.name):
                run, ana, _bot, entry = self.fight(definition, ally=True)
                fight = run.match.combat
                vitals = fight.vitals_of(entry)
                vitals.damage(150)
                before = vitals.total
                self.attack(run, ana, entry, 1)
                self.assertEqual(vitals.total, before + 70)
                self.attack(run, ana, entry, 0x10)
                self.assertEqual(vitals.total, min(before + 170, sum(vitals.most.values())))
                self.assertGreater(vitals.healing_boost_until, fight.now)

    def test_manual_corpses_remain_and_cannot_be_healed(self):
        for definition in self.catalog.values():
            with self.subTest(npc=definition.name):
                run, ana, bot, entry = self.fight(definition)
                fight = run.match.combat
                fight._damage(ana, entry, 100000, False, None, run.frame)
                self.assertTrue(bot.dead)
                self.assertTrue(bot.retain_corpse)
                self.assertTrue(any(entity == bot.entity for entity, _snapshot in ana.client.bot_deaths))
                self.assertFalse(any(who is bot for _when, who in fight.respawns))
                run.frame += 1250
                run.match.update(run.frame * 0.016, run.frame, frames=False)
                self.assertIn(bot, run.match.bots)
                self.assertNotIn(entry, fight._enemies(ana))
                self.assertEqual(fight._heal(ana, entry, 1000), 0)
                self.assertEqual(fight.vitals_of(entry).total, 0)

    def test_melee_hits_all_five_enemy_bodies_and_spares_allies(self):
        for definition in self.catalog.values():
            for ally in (False, True):
                for distance in (1.5, 4.0):
                    with self.subTest(npc=definition.name, ally=ally, distance=distance):
                        run, ana, _bot, entry = self.fight(definition, ally=ally, distance=distance)
                        vitals = run.match.combat.vitals_of(entry)
                        before = vitals.total
                        self.attack(run, ana, entry, 0x800)
                        dealt = 30 if not ally and distance == 1.5 else 0
                        self.assertEqual(vitals.total, before - dealt)
                        self.assertEqual(ana.stats.value(stats.HERO_DAMAGE), dealt)

    def test_actual_rifle_fire_kills_full_health_five_bodies_and_retains_corpses(self):
        for definition in self.catalog.values():
            with self.subTest(npc=definition.name):
                run, ana, bot, entry = self.fight(definition)
                fight = run.match.combat
                vitals = fight.vitals_of(entry)
                initial = vitals.total
                direction = aim(fight._eye(ana),
                                (entry.feet[0], entry.feet[1] + 1, entry.feet[2]))
                # Hold actual primary input through magazine depletion/reload.
                # No direct pool changes or damage API shortcuts precede death.
                for _ in range(6500):
                    run.step({ana: (*direction, 1)})
                    if bot.dead:
                        break
                self.assertTrue(bot.dead, f"{definition.name}: {vitals.total}/{initial} remains")
                self.assertEqual(vitals.total, 0)
                self.assertEqual(vitals.killer, ana.body)
                self.assertEqual(ana.stats.value(stats.HERO_DAMAGE), initial)
                self.assertTrue(ana.client.messages(combat.KILL_NOTICE))
                self.assertTrue(any(entity == bot.entity for entity, _ in ana.client.bot_deaths))
                self.assertFalse(any(who is bot for _, who in fight.respawns))
                # Stop firing, let the old dose and ordinary corpse timer expire.
                for _ in range(60):
                    run.step({})
                run.frame += 1250
                run.match.update(run.frame * 0.016, run.frame, frames=False)
                self.assertIn(bot, run.match.bots)
                self.assertEqual(fight._heal(ana, entry, 1000), 0)
                self.assertFalse(any(hit.target == bot.entity for hit in fight.timed_hits))

    def test_melee_cannot_hit_five_enemy_bodies_through_static_wall(self):
        for definition in self.catalog.values():
            with self.subTest(npc=definition.name):
                run, ana, _bot, entry = self.fight(definition, distance=1.5)
                vitals = run.match.combat.vitals_of(entry)
                before = vitals.total
                set_static_wall(run, ana, 0.75)
                self.attack(run, ana, entry, 0x800)
                self.assertEqual(vitals.total, before)
                self.assertEqual(ana.stats.value(stats.HERO_DAMAGE), 0)

    def test_each_native_npc_weapon_can_damage_ana(self):
        # Manual inputs and explicit contact do not exercise autonomous AI.
        for definition in self.catalog.values():
            with self.subTest(npc=definition.name):
                is_assassin = definition.hero == assassin.HERO
                run, ana, bot, _entry = self.fight(definition, distance=2.0 if is_assassin else 6.0)
                fight = run.match.combat
                own = next(t for t in fight._targets() if t.owner is ana)
                vitals = fight.vitals_of(own)
                if is_assassin:
                    self.assertTrue(assassin.engage(run.match, bot, ana))
                else:
                    bot.firing = True
                    button = bot.body_script.component.world.ai_weapon_button(0)
                    self.assertIsNotNone(button)
                    bot.body_script.component.edges([(button, True)])
                for _ in range(180):
                    run.step({})
                self.assertLess(vitals.total, 200, f"{definition.name} attack produced no damage")

    def sombra_npc_fight(self, definition, *, distance=5):
        run, ana, bot, entry = self.fight(definition, distance=distance, with_zarya=True)
        sombra = run.players[1]
        self.assertTrue(run.match.switch_hero(sombra, heroes()[sombra_control.HERO]))
        sombra.client = NPCFixtureClient(sombra)
        sombra.team = ana.team
        x, y, z = ana.mover.position
        AnaOrisaBarrierTests.place(sombra, (x + 2, y, z + 1))
        for _ in range(30):
            run.step({})
        return run, ana, sombra, bot, entry

    def sombra_npc_cast(self, run, sombra, bot, button):
        fight = run.match.combat
        if button == 32:
            script = sombra.body_script
            script.set_ult_charge(script.component.entity_var(ULT_COST).value())
        for frame in range(100):
            x, y, z = bot.position
            direction = aim(fight._eye(sombra), (x, y + 1, z))
            run.step({sombra: (*direction, button if button == 2 or frame == 0 else 0)})
            if bot.entity in fight.sombra_controls.locks:
                return fight.sombra_controls.locks[bot.entity][-1]
        self.fail("NPC did not receive the authored Hack or EMP application")

    def test_actual_sombra_hack_and_emp_on_all_five_npcs(self):
        for definition in self.catalog.values():
            for button in (2, 32):
                with self.subTest(npc=definition.name, button=button):
                    run, ana, sombra, bot, entry = self.sombra_npc_fight(definition)
                    fight = run.match.combat
                    vitals = fight.vitals_of(entry)
                    before = dict(vitals.now)
                    lock = self.sombra_npc_cast(run, sombra, bot, button)
                    self.assertEqual(lock.application.source_entity(), sombra.body)
                    self.assertEqual(lock.application.graph.index, sombra_control.EFFECT)
                    self.assertEqual(lock.pulse is not None, button == 32)
                    self.assertAlmostEqual(lock.until - fight.now, 5)
                    drained = definition.shields if button == 32 else 0
                    self.assertEqual(vitals.total, sum(before.values()) - drained)
                    if definition.hero != assassin.HERO:
                        bot.spawner['acquire_targets'] = True
                        x, y, z = ana.mover.position
                        AnaOrisaBarrierTests.place(sombra, (x + 40, y, z))
                        for _ in range(185):
                            run.step({})
                            if fight.vitals[ana.body].total < 200:
                                break
                        self.assertLess(fight.vitals[ana.body].total, 200)
                        self.assertIn(bot.entity, fight.sombra_controls.locks)
                    while fight.now < lock.until:
                        run.step({})
                    self.assertNotIn(bot.entity, fight.sombra_controls.locks)
                    self.assertNotIn(lock.application.id, bot.body_script.component.instances)
                    self.assertIsNone(bot.body_script.component.fault)

    def test_sombra_npc_casts_reject_ally_cover_and_range(self):
        definition = next(item for item in self.catalog.values() if item.name == "Trooper")
        for button in (2, 32):
            for gate in ("ally", "wall", "range"):
                with self.subTest(button=button, gate=gate):
                    run, ana, sombra, bot, _ = self.sombra_npc_fight(
                        definition, distance=36 if gate == "range" else 5,
                    )
                    if gate == "ally":
                        bot.team = sombra.team
                    elif gate == "wall":
                        set_static_wall(run, ana, 3)
                    if button == 32:
                        script = sombra.body_script
                        script.set_ult_charge(script.component.entity_var(ULT_COST).value())
                    x, y, z = bot.position
                    direction = aim(run.match.combat._eye(sombra), (x, y + 1, z))
                    for frame in range(80):
                        run.step({sombra: (*direction, button if button == 2 or frame == 0 else 0)})
                    self.assertNotIn(bot.entity, run.match.combat.sombra_controls.locks)
                    self.assertFalse(any(instance.graph.index in (sombra_control.PULSE, sombra_control.EFFECT)
                                         for instance in bot.body_script.component.instances.values()))

    def test_sombra_npc_hack_preserves_shields_and_emp_drains_only_shields(self):
        definition = next(item for item in self.catalog.values() if item.name == "Heavy Assault")
        for button in (2, 32):
            with self.subTest(button=button):
                shielded = replace(definition, shields=75)
                run, _, sombra, bot, entry = self.sombra_npc_fight(shielded)
                vitals = run.match.combat.vitals_of(entry)
                self.sombra_npc_cast(run, sombra, bot, button)
                self.assertEqual(vitals.now[HEALTH_POOL], definition.health)
                self.assertEqual(vitals.now[ARMOUR_POOL], definition.armour)
                self.assertEqual(vitals.now[SHIELDS_POOL], 0 if button == 32 else shielded.shields)

    def test_sombra_emp_releases_assassin_pin_on_ana(self):
        definition = next(item for item in self.catalog.values() if item.hero == assassin.HERO)
        run, ana, sombra, bot, _ = self.sombra_npc_fight(definition, distance=2)
        fight = run.match.combat
        self.assertTrue(assassin.engage(run.match, bot, ana))
        for _ in range(12):
            run.step({})
            if fight.pins.get(bot.entity) == ana.body:
                break
        self.assertEqual(fight.pins, {bot.entity: ana.body})
        lock = self.sombra_npc_cast(run, sombra, bot, 32)
        self.assertFalse(fight.is_stunned(ana))
        self.assertNotIn(bot.entity, fight.pins)
        self.assertNotIn(assassin.BUTTON, bot.body_script.component.held)
        self.assertFalse(assassin.engage(run.match, bot, ana))
        before = fight.vitals[ana.body].total
        while fight.now < lock.until:
            run.step({})
        self.assertEqual(fight.vitals[ana.body].total, before)
        self.assertTrue(assassin.engage(run.match, bot, ana))

    def test_sombra_emp_interrupts_moving_heavy_charge_before_ana_contact(self):
        definition = next(item for item in self.catalog.values() if item.name == "Heavy Assault")
        run, ana, sombra, bot, _ = self.sombra_npc_fight(definition, distance=7.5)
        fight = run.match.combat
        component = bot.body_script.component
        charge = next(instance for instance in component.instances.values() if instance.graph.index == 0x1408)
        bot.spawner.update(acquire_targets=True, acquire_specials=True)
        start = bot.position
        for _ in range(90):
            run.step({})
            if bot.position != start:
                break
        self.assertLess(bot.position[2], start[2])
        self.assertTrue(charge.states[33].active)
        self.sombra_npc_cast(run, sombra, bot, 32)
        stop = bot.position
        self.assertFalse(charge.states[33].active)
        self.assertGreater(charge.states[41].remaining(), 0)
        self.assertGreater(stop[2], ana.mover.position[2] + 1)
        self.assertEqual(fight.vitals[ana.body].total, 200)
        for _ in range(20):
            run.step({})
        self.assertEqual(bot.position, stop)
        self.assertNotIn(bot.entity, fight.pins)
        self.assertNotIn(35, component.held)
        self.assertIsNone(component.fault)

    def test_sombra_emp_interrupts_sniper_retreat_from_ana(self):
        definition = next(item for item in self.catalog.values() if item.name == "Sniper")
        run, _, sombra, bot, _ = self.sombra_npc_fight(definition, distance=1.5)
        bot.spawner.update(acquire_targets=True, acquire_specials=True)
        component = bot.body_script.component
        root = next(instance for instance in component.instances.values() if instance.graph.index == 0x142C)
        start = bot.position
        for _ in range(150):
            run.step({})
            windup = root.states.get(root.graph.node(94).state)
            if windup is not None and windup.active:
                break
        self.assertIsNotNone(windup)
        self.assertTrue(windup.active)
        retry_at = component.world.sniper_retry_at
        for _ in range(10):
            run.step({})
        self.sombra_npc_cast(run, sombra, bot, 32)
        stop = bot.position
        self.assertGreater(stop[2], start[2])
        self.assertLess(stop[2], start[2] + 2.5)
        self.assertIsNone(self.sniper_move(root))
        self.assertEqual(component.world.sniper_retry_at, retry_at)
        for _ in range(45):
            run.step({})
        self.assertEqual(bot.position, stop)
        self.assertIn(bot.entity, run.match.combat.sombra_controls.locks)
        self.assertIsNone(component.fault)

    def test_sombra_npc_effects_retire_on_removal_or_lethal_ana_dart(self):
        definition = next(item for item in self.catalog.values() if item.name == "Trooper")
        for button, remove in ((2, True), (32, False)):
            with self.subTest(button=button, remove=remove):
                run, ana, sombra, bot, entry = self.sombra_npc_fight(definition)
                fight = run.match.combat
                lock = self.sombra_npc_cast(run, sombra, bot, button)
                component = bot.body_script.component
                if remove:
                    self.assertIn(bot.entity, npc.remove_spawned(run.match))
                else:
                    vitals = fight.vitals_of(entry)
                    vitals.damage(vitals.total - 5)
                    self.attack(run, ana, entry, 8)
                    self.assertTrue(bot.dead)
                    self.assertEqual(vitals.killer, ana.body)
                self.assertNotIn(bot.entity, fight.sombra_controls.locks)
                self.assertNotIn(lock.application.id, component.instances)
                if lock.pulse is not None:
                    self.assertNotIn(lock.pulse.id, component.instances)

    def test_ana_dart_and_sombra_emp_expire_independently_on_npc(self):
        definition = next(item for item in self.catalog.values() if item.name == "Trooper")
        run, ana, sombra, bot, entry = self.sombra_npc_fight(definition)
        fight = run.match.combat
        lock = self.sombra_npc_cast(run, sombra, bot, 32)
        vitals = fight.vitals_of(entry)
        before = vitals.total
        direction = aim(fight._eye(ana), (entry.feet[0], entry.feet[1] + 1, entry.feet[2]))
        run.step({ana: (*direction, 8)})
        for _ in range(65):
            run.step({})
            if fight.is_asleep(bot):
                break
        self.assertTrue(fight.is_asleep(bot))
        self.assertEqual(vitals.total, before - 5)
        self.assertGreater(vitals.sleep_until, lock.until)
        while fight.now < lock.until:
            run.step({})
        self.assertNotIn(bot.entity, fight.sombra_controls.locks)
        self.assertTrue(fight.is_asleep(bot))
        while fight.now < vitals.sleep_until:
            run.step({})
        self.assertFalse(fight.is_asleep(bot))
        self.assertEqual(vitals.total, before - 5)

    def sniper_retreat(self, *, distance=1.5, specials=True):
        definition = next(item for item in self.catalog.values() if item.name == "Sniper")
        run, ana, bot, entry = self.fight(definition, ally=True, distance=distance,
                                          acquire=True, specials=specials)
        root = next(instance for instance in bot.body_script.component.instances.values()
                    if instance.graph.index == 0x142C)
        return run, ana, bot, entry, root

    @staticmethod
    def sniper_move(root):
        state = root.states.get(root.graph.node(16).state)
        return state.move_request() if state is not None else None

    def test_sniper_autonomously_retreats_and_resumes_rifle_on_ana(self):
        run, ana, bot, _, root = self.sniper_retreat()
        start = bot.position
        fight = run.match.combat
        for _ in range(100):
            run.step({})
        self.assertEqual(bot.position, start)
        ana.team = 1
        for _ in range(150):
            run.step({})
            if self.sniper_move(root):
                break
        move = self.sniper_move(root)
        self.assertIsNotNone(move)
        point, speed = move
        self.assertEqual(speed, 20)
        vm = root.component
        active = snapshot_digest(export_component_vm(vm))
        for field, changed in (("sniper_target", None), ("sniper_retry_at", 0)):
            original = getattr(vm.world, field)
            setattr(vm.world, field, changed)
            self.assertNotEqual(snapshot_digest(export_component_vm(vm)), active)
            setattr(vm.world, field, original)
        self.assertEqual(snapshot_digest(export_component_vm(vm)), active)
        self.assertGreater(point.z, start[2] + 2)
        self.assertFalse(bot.firing)
        self.assertNotIn(root.component.world.ai_weapon_button(0), root.component.held)
        self.assertEqual(fight.vitals[ana.body].total, 200)
        for _ in range(20):
            run.step({})
            if self.sniper_move(root) is None:
                break
        self.assertIsNone(self.sniper_move(root))
        self.assertEqual(bot.position, tuple(point))
        for _ in range(180):
            run.step({})
            if fight.vitals[ana.body].total < 200:
                break
        self.assertEqual(fight.vitals[ana.body].total, 100)
        self.assertGreater(root.component.world.sniper_retry_at, root.component.now)
        self.assertIsNone(root.component.fault)

    def test_sniper_retreat_requires_opt_in_range_and_line_of_sight(self):
        for distance, specials, cover in ((1.5, False, False), (6, True, False), (1.5, True, True)):
            with self.subTest(distance=distance, specials=specials, cover=cover):
                run, ana, bot, _, root = self.sniper_retreat(distance=distance, specials=specials)
                if cover:
                    set_static_wall(run, ana, distance / 2)
                ana.team = 1
                start = bot.position
                for _ in range(135):
                    run.step({})
                    self.assertIsNone(self.sniper_move(root))
                self.assertEqual(bot.position, start)
                self.assertEqual(root.component.world.sniper_retry_at, 0)
                self.assertIsNone(root.component.fault)

    def test_sniper_cannot_retreat_through_a_rear_wall(self):
        run, ana, bot, _, root = self.sniper_retreat()
        set_static_wall(run, ana, 2)
        ana.team = 1
        start = bot.position
        fight = run.match.combat
        for _ in range(320):
            run.step({})
            self.assertIsNone(self.sniper_move(root))
            if fight.vitals[ana.body].total < 200 and root.component.world.sniper_retry_at:
                break
        self.assertEqual(bot.position, start)
        self.assertGreater(root.component.world.sniper_retry_at, root.component.now)
        self.assertEqual(fight.vitals[ana.body].total, 100)
        self.assertIsNone(root.component.fault)

    def test_ana_sleep_dart_interrupts_autonomous_sniper_retreat(self):
        run, ana, bot, entry, root = self.sniper_retreat()
        ana.team = 1
        start = bot.position
        for _ in range(150):
            run.step({})
            windup = root.states.get(root.graph.node(94).state)
            if windup is not None and windup.active:
                break
        self.assertIsNotNone(windup)
        self.assertTrue(windup.active)
        for _ in range(11):
            run.step({})
        fight = run.match.combat
        vitals = fight.vitals_of(entry)
        before = vitals.total
        retry_at = root.component.world.sniper_retry_at
        direction = aim(fight._eye(ana), (start[0], start[1] + 1, start[2]))
        run.step({ana: (*direction, 8)})
        moving = False
        for _ in range(42):
            run.step({})
            moving |= self.sniper_move(root) is not None
            if fight.is_asleep(bot):
                break
        self.assertTrue(moving)
        self.assertTrue(fight.is_asleep(bot))
        self.assertEqual(vitals.total, before - 5)
        self.assertEqual(root.component.world.sniper_retry_at, retry_at)
        self.assertIsNone(self.sniper_move(root))
        position = bot.position
        self.assertGreater(position[2], start[2])
        self.assertLess(position[2], start[2] + 2.5)
        for _ in range(45):
            run.step({})
        self.assertEqual(bot.position, position)
        self.assertFalse(bot.firing)
        self.assertEqual(fight.vitals[ana.body].total, 200)
        self.assertIsNone(root.component.fault)

    def test_opt_in_stationary_npcs_acquire_and_attack_ana(self):
        for definition in self.catalog.values():
            with self.subTest(npc=definition.name):
                distance = 2 if definition.hero == assassin.HERO else 6
                run, ana, bot, _ = self.fight(definition, ally=True, distance=distance, acquire=True)
                fight = run.match.combat
                own = next(target for target in fight._targets() if target.owner is ana)
                vitals = fight.vitals_of(own)
                for _ in range(35):
                    run.step({})
                self.assertEqual(vitals.total, 200)
                ana.team = 1
                open_world = run.match.collision_world
                set_static_wall(run, ana, distance / 2)
                for _ in range(35):
                    run.step({})
                self.assertEqual(vitals.total, 200)
                run.match.collision_world = open_world
                for _ in range(180):
                    run.step({})
                    if vitals.total < 200:
                        break
                self.assertLess(vitals.total, 200)
                self.assertIsNotNone(bot.body_script)
                self.assertIsNone(bot.body_script.component.fault)

    def test_stationary_npcs_kill_ana_and_acquire_her_respawned_body(self):
        for definition in self.catalog.values():
            with self.subTest(npc=definition.name):
                distance = 2 if definition.hero == assassin.HERO else 6
                run, ana, bot, _ = self.fight(definition, ally=True, distance=distance, acquire=True)
                fight = run.match.combat
                component = bot.body_script.component
                old_body = ana.body
                old = fight.vitals_of(next(t for t in fight._targets() if t.owner is ana))
                old.damage(old.total - 1)
                ana.team = 1
                for _ in range(200):
                    run.step({})
                    if old.dead:
                        break
                self.assertTrue(old.dead)
                self.assertEqual(old.killer, bot.entity)
                self.assertEqual(ana.body, old_body)
                deadline, = (when for when, who in fight.respawns if who is ana)
                for _ in range(1000):
                    run.step({})
                    if ana.body != old_body:
                        break
                    self.assertTrue(fight.is_dead(ana))
                    self.assertNotIn(old_body, fight.pins.values())
                self.assertNotEqual(ana.body, old_body)
                self.assertGreaterEqual(fight.now, deadline)
                self.assertNotIn(old_body, fight.vitals)
                current = fight.vitals_of(next(t for t in fight._targets() if t.owner is ana))
                self.assertEqual(current.total, 200)
                self.assertFalse(current.dead)
                x, y, z = ana.mover.position
                bot.position = (x, y, z + distance)
                open_world = run.match.collision_world
                set_static_wall(run, ana, distance / 2)
                for _ in range(40):
                    run.step({})
                self.assertEqual(current.total, 200)
                run.match.collision_world = open_world
                for _ in range(200):
                    run.step({})
                    if current.total < 200:
                        break
                self.assertLess(current.total, 200)
                self.assertIs(bot.body_script.component, component)
                self.assertIsNone(component.fault)

    def test_stationary_acquisition_resumes_after_both_ana_sleep_wake_routes(self):
        for definition in self.catalog.values():
            for wake in ('early rifle', 'late rifle', 'expiry'):
                with self.subTest(npc=definition.name, wake=wake):
                    distance = 2 if definition.hero == assassin.HERO else 6
                    run, ana, bot, entry = self.fight(definition, distance=distance)
                    fight = run.match.combat
                    own = next(target for target in fight._targets() if target.owner is ana)
                    vitals = fight.vitals_of(own)
                    pool = fight.vitals_of(entry)
                    direction = aim(fight._eye(ana), (entry.feet[0], entry.feet[1] + 1, entry.feet[2]))
                    run.step({ana: (*direction, 8)})
                    for _ in range(60):
                        if fight.is_asleep(bot):
                            break
                        run.step({ana: (*direction, 0)})
                    self.assertTrue(fight.is_asleep(bot))
                    bot.spawner['acquire_targets'] = True
                    deadline = pool.sleep_until
                    floor = pool.sleep_wake_floor
                    if wake != 'expiry':
                        if wake == 'late rifle':
                            while fight.now < floor:
                                run.step({})
                        before = pool.total
                        for frame in range(60):
                            if pool.total < before:
                                break
                            run.step({ana: (*direction, 1 if frame % 2 == 0 else 0)})
                        self.assertLess(pool.total, before)
                        deadline = max(floor, fight.now + .5)
                        self.assertEqual(deadline == floor, wake == 'early rifle')
                        self.assertAlmostEqual(pool.sleep_until, deadline)
                    while fight.now + combat.TICK < deadline:
                        run.step({})
                        self.assertTrue(fight.is_asleep(bot))
                        self.assertEqual(pool.sleep_until, deadline)
                        self.assertEqual(vitals.total, 200)
                    while fight.is_asleep(bot):
                        run.step({})
                    self.assertFalse(fight.is_asleep(bot))
                    for _ in range(180):
                        if vitals.total < 200:
                            break
                        run.step({})
                    self.assertLess(vitals.total, 200)
                    self.assertIsNotNone(bot.body_script)
                    self.assertIsNone(bot.body_script.component.fault)

    def test_sleep_interrupts_an_already_attacking_stationary_npc(self):
        for definition in self.catalog.values():
            with self.subTest(npc=definition.name):
                is_assassin = definition.hero == assassin.HERO
                distance = 2 if is_assassin else 6
                run, ana, bot, _ = self.fight(definition, ally=True, distance=distance,
                                                acquire=not is_assassin, with_zarya=True)
                helper = run.players[1]
                self.assertTrue(run.match.switch_hero(helper, heroes()[ANA]))
                helper.client = NPCFixtureClient(helper)
                helper.team = ana.team
                x, y, z = bot.position
                helper.spawn = (x + 8, y, z), helper.spawn[1]
                helper.mover = helper.new_mover()
                for _ in range(25):
                    run.step({})
                ana.team = helper.team = 1 - bot.team
                bot.spawner['acquire_targets'] = True
                fight = run.match.combat
                own = next(target for target in fight._targets() if target.owner is ana)
                vitals = fight.vitals_of(own)
                for _ in range(100):
                    run.step({})
                    if vitals.total < 200:
                        break
                self.assertLess(vitals.total, 200)
                direction = aim(fight._eye(helper), (x, y + 1, z))
                run.step({helper: (*direction, 8)})
                for _ in range(50):
                    run.step({helper: (*direction, 0)})
                    if fight.is_asleep(bot):
                        break
                for _ in range(30):
                    run.step({})
                self.assertTrue(fight.is_asleep(bot))
                self.assertNotIn(bot.entity, fight.pins)
                component = bot.body_script.component
                button = (assassin.BUTTON if definition.hero == assassin.HERO
                          else component.world.ai_weapon_button(0))
                self.assertNotIn(button, component.held)
                health = vitals.total
                for _ in range(60):
                    run.step({})
                self.assertTrue(fight.is_asleep(bot))
                self.assertEqual(vitals.total, health)

    def test_opt_in_pursuit_moves_around_wall_and_attacks_ana(self):
        for definition in self.catalog.values():
            with self.subTest(npc=definition.name):
                run, ana, bot, _ = self.fight(definition, ally=True, distance=8, pursue=True)
                fight = run.match.combat
                start = bot.position
                set_static_wall(run, ana, 4)
                ana.team = 1 - bot.team
                own = next(target for target in fight._targets() if target.owner is ana)
                vitals = fight.vitals_of(own)
                controller = None
                for _ in range(900):
                    run.step({})
                    if vitals.total < 200:
                        break
                    controller = getattr(bot, "assassin_controller", None)
                    if controller is not None:
                        slot = controller.entity_var(1650, create=False)
                        if slot is not None and slot.value() == expr.Entity(ana.body):
                            break
                if controller is not None:
                    self.assertEqual(controller.entity_var(1650).value(), expr.Entity(ana.body))
                    self.assertEqual(vitals.total, 200)
                    contact = bot.position
                    for _ in range(47):
                        run.step({})
                        self.assertEqual(bot.position, contact)
                        if fight.pins:
                            break
                    self.assertEqual(fight.pins, {bot.entity: ana.body})
                self.assertNotEqual(bot.position, start)
                self.assertLess(vitals.total, 200)
                self.assertEqual(bot.route, [])
                self.assertIsNotNone(bot.body_script)
                self.assertIsNone(bot.body_script.component.fault)

    def test_sleep_halts_pursuit_and_wake_replans_all_five_npcs(self):
        for definition in self.catalog.values():
            with self.subTest(npc=definition.name):
                run, ana, bot, _ = self.fight(definition, ally=True, distance=8,
                                               pursue=True, with_zarya=True)
                helper = run.players[1]
                self.assertTrue(run.match.switch_hero(helper, heroes()[ANA]))
                helper.client = NPCFixtureClient(helper)
                helper.team = bot.team
                x, y, z = bot.position
                helper.spawn = (x + 6, y, z + 2), helper.spawn[1]
                helper.mover = helper.new_mover()
                for _ in range(25):
                    run.step({})
                set_static_wall(run, ana, 4)
                ana.team = 1 - bot.team
                fight = run.match.combat
                start = bot.position
                for _ in range(130):
                    run.step({})
                    if bot.route and bot.position != start:
                        break
                self.assertTrue(bot.route)
                self.assertNotEqual(bot.position, start)
                helper.team = ana.team
                for frame in range(50):
                    x, y, z = bot.position
                    direction = aim(fight._eye(helper), (x, y + 1, z))
                    run.step({helper: (*direction, 8 if frame == 0 else 0)})
                    if fight.is_asleep(bot):
                        break
                self.assertTrue(fight.is_asleep(bot))
                helper.team = bot.team
                run.step({})
                slept_at = bot.position
                self.assertFalse(bot.route)
                self.assertEqual(bot.velocity, (0.0, 0.0, 0.0))
                for _ in range(90):
                    run.step({})
                self.assertTrue(fight.is_asleep(bot))
                self.assertEqual(bot.position, slept_at)
                own = next(target for target in fight._targets() if target.owner is ana)
                vitals = fight.vitals_of(own)
                self.assertEqual(vitals.total, 200)
                for _ in range(1150):
                    run.step({})
                    if vitals.total < 200:
                        break
                self.assertFalse(fight.is_asleep(bot))
                self.assertNotEqual(bot.position, slept_at)
                self.assertLess(vitals.total, 200)

    def test_pursuit_replans_when_mei_wall_closes_and_reopens_route(self):
        from ow174.game.collision import World

        definition = next(d for d in self.catalog.values() if d.hero == assassin.HERO)
        run, ana, bot, _ = self.fight(definition, ally=True, distance=8,
                                       pursue=True, with_zarya=True)
        mei = run.players[1]
        self.assertTrue(run.match.switch_hero(mei, find_hero('Mei')))
        mei.client = NPCFixtureClient(mei)
        mei.team = bot.team
        x, y, z = ana.mover.position
        mei.spawn = (x, y, z + 1), mei.spawn[1]
        mei.mover = mei.new_mover()
        low, high = z - 45, z + 45
        triangles = [
            (x - 3, y, low, x - 3, y, high, x + 3, y, high),
            (x - 3, y, low, x + 3, y, high, x + 3, y, low),
        ]
        for side in (x - 2.4, x + 2.4):
            triangles.extend([
                (side, y, low, side, y, high, side, y + 5, high),
                (side, y, low, side, y + 5, high, side, y + 5, low),
            ])
        run.match.collision_world = World(triangles)
        for _ in range(20):
            run.step({})
        fight = run.match.combat
        ana.team = 1 - bot.team
        for _ in range(100):
            run.step({})
            if bot.route:
                break
        self.assertTrue(bot.route)
        initial_search = bot.pursuit.search
        direction = aim(fight._eye(mei), (x, y, z + 4))
        for buttons in (16, 0, 1, 0):
            run.step({mei: (*direction, buttons)})
        self.assertEqual(len(fight.ice_wall.pieces), 3)
        self.assertIsNot(bot.pursuit.search, initial_search)
        own = next(target for target in fight._targets() if target.owner is ana)
        vitals = fight.vitals_of(own)
        for _ in range(180):
            run.step({})
            self.assertGreater(bot.position[2], z + 4.4)
        self.assertEqual(vitals.total, 200)
        run.step({mei: (*direction, 16)})
        self.assertFalse(fight.ice_wall.pieces)
        for _ in range(650):
            run.step({})
            if vitals.total < 200:
                break
        self.assertLess(bot.position[2], z + 4)
        self.assertLess(vitals.total, 200)

    def test_autonomous_trooper_and_heavy_select_authored_melee(self):
        for hero, damage in ((0x02E00000000001AC, 30), (0x02E00000000001BA, 80)):
            definition = next(d for d in self.catalog.values() if d.hero == hero)
            with self.subTest(npc=definition.name):
                run, ana, bot, _ = self.fight(definition, ally=True, distance=1.2,
                                               acquire=True, specials=True)
                fight = run.match.combat
                target = next(target for target in fight._targets() if target.owner is ana)
                vitals = fight.vitals_of(target)
                self.assertEqual(vitals.total, 200)
                ana.team = 1 - bot.team
                open_world = run.match.collision_world
                set_static_wall(run, ana, 0.6)
                for _ in range(35):
                    run.step({})
                self.assertEqual(vitals.total, 200)
                run.match.collision_world = open_world
                target_controller = getattr(bot, "trooper_controller", None)
                if target_controller is not None:
                    instance, = target_controller.instances.values()
                    gate = instance.node_state(instance.graph.node(20))
                    with patch.object(enforcer_controller.TargetWorld, "target_record_scalar", return_value=0.0):
                        for _ in range(20):
                            run.step({})
                        self.assertEqual(target_controller.world.selected, ana.body)
                        self.assertFalse(gate.current)
                        self.assertNotIn(npc.MELEE_BUTTON, bot.body_script.component.held)
                        self.assertEqual(vitals.total, 200)
                    gate.dependency_changed()
                for _ in range(90):
                    run.step({})
                    if vitals.total < 200:
                        break
                self.assertEqual(vitals.total, 200 - damage)
                component = bot.body_script.component
                self.assertIn(npc.MELEE_BUTTON, component.held)
                self.assertNotIn(component.world.ai_weapon_button(0), component.held)
                if target_controller is not None:
                    with patch.object(enforcer_controller.TargetWorld, "target_record_scalar", return_value=0.0):
                        gate.dependency_changed()
                        run.step({})
                        self.assertFalse(gate.current)
                        self.assertNotIn(npc.MELEE_BUTTON, component.held)
                self.assertIsNone(component.fault)

    def test_heavy_melee_lifts_and_pushes_ana_but_trooper_does_not(self):
        for hero, lifts in ((0x02E00000000001AC, False), (0x02E00000000001BA, True)):
            definition = next(d for d in self.catalog.values() if d.hero == hero)
            with self.subTest(npc=definition.name):
                run, ana, bot, _ = self.fight(definition, ally=True, distance=1.2,
                                               acquire=True, specials=True)
                fight = run.match.combat
                target = next(target for target in fight._targets() if target.owner is ana)
                vitals = fight.vitals_of(target)
                _, y, z = ana.mover.position
                ana.team = 1 - bot.team
                for _ in range(90):
                    run.step({})
                    if vitals.total < 200:
                        break
                self.assertEqual(vitals.total, 120 if lifts else 170)
                impulse = fight.external_impulses.get(ana.body)
                if lifts:
                    self.assertIsNotNone(impulse)
                    self.assertAlmostEqual(impulse[1], 8)
                    self.assertAlmostEqual(impulse[2], -8)
                else:
                    self.assertIsNone(impulse)
                ana.team = bot.team
                peak = y
                for _ in range(75):
                    run.step({})
                    peak = max(peak, ana.mover.position[1])
                if lifts:
                    self.assertGreater(peak, y + 0.2)
                    self.assertLess(ana.mover.position[2], z - 0.2)
                else:
                    self.assertAlmostEqual(peak, y)
                    self.assertAlmostEqual(ana.mover.position[2], z)
                self.assertNotIn(ana.body, fight.external_impulses)

    def test_mei_wall_clips_heavy_melee_push_without_removing_lift(self):
        definition = next(d for d in self.catalog.values() if d.hero == 0x02E00000000001BA)
        run, ana, bot, _ = self.fight(definition, ally=True, distance=1.2, with_zarya=True,
                                       acquire=True, specials=True)
        mei = run.players[1]
        self.assertTrue(run.match.switch_hero(mei, find_hero('Mei')))
        mei.client = NPCFixtureClient(mei)
        mei.team = bot.team
        x, y, z = ana.mover.position
        mei.spawn = (x, y, z - 5), mei.spawn[1]
        mei.mover = mei.new_mover()
        for _ in range(25):
            run.step({})
        fight = run.match.combat
        direction = aim(fight._eye(mei), (x, y, z - 1.2))
        for buttons in (16, 0, 1, 0):
            run.step({mei: (*direction, buttons)})
        self.assertEqual(len(fight.ice_wall.pieces), 5)
        centre = min(fight.ice_wall.pieces.values(), key=lambda piece: abs(piece.feet[0] - x))
        boundary = centre.feet[2] + centre.depth + ana.mover.data.stand_radius
        self.assertLess(boundary, z)
        self.assertGreater(boundary, z - 0.5)
        target = next(target for target in fight._targets() if target.owner is ana)
        vitals = fight.vitals_of(target)
        ana.team = 1 - bot.team
        for _ in range(90):
            run.step({})
            if vitals.total < 200:
                break
        self.assertEqual(vitals.total, 120)
        ana.team = bot.team
        for _ in range(12):
            run.step({})
            self.assertGreaterEqual(ana.mover.position[2], boundary - 0.002)
        self.assertGreater(ana.mover.position[1], y + 0.2)
        self.assertAlmostEqual(ana.mover.position[2], boundary, delta=0.02)
        self.assertAlmostEqual(fight.external_impulses[ana.body][2], 0)
        self.assertEqual(centre.health, 400)

    def test_npc_melee_rechecks_cover_and_team_after_windup(self):
        for hero, windup in ((0x02E00000000001AC, 14), (0x02E00000000001BA, 18)):
            definition = next(d for d in self.catalog.values() if d.hero == hero)
            for cover in (False, True):
                with self.subTest(npc=definition.name, cover=cover):
                    run, ana, bot, _ = self.fight(definition, ally=True, distance=1.2,
                                                   acquire=True, specials=True)
                    fight = run.match.combat
                    target = next(target for target in fight._targets() if target.owner is ana)
                    vitals = fight.vitals_of(target)
                    graph_id, _, _, _ = npc.MELEE_GRAPHS[hero]
                    component = bot.body_script.component
                    instance = next(i for i in component.instances.values() if i.graph.index == graph_id)
                    ana.team = 1 - bot.team
                    for _ in range(10):
                        run.step({})
                        if any(state.active and state.node.pos == windup
                               for state in instance.states.values()):
                            break
                    self.assertTrue(any(state.active and state.node.pos == windup
                                        for state in instance.states.values()))
                    self.assertEqual(vitals.total, 200)
                    if cover:
                        set_static_wall(run, ana, 0.6)
                    else:
                        ana.team = bot.team
                    for _ in range(60):
                        run.step({})
                    self.assertEqual(vitals.total, 200)
                    self.assertNotIn(npc.MELEE_BUTTON, component.held)
                    self.assertIsNone(component.fault)

    def test_heavy_charge_graph_resolves_motion_from_live_body_heading(self):
        definition = next(d for d in self.catalog.values() if d.hero == 0x02E00000000001BA)
        for yaw, expected in ((0, (0, 0, 15)), (-32768, (0, 0, -15)), (16384, (15, 0, 0))):
            with self.subTest(yaw=yaw):
                run, _, bot, _ = self.fight(definition, distance=8)
                bot.yaw = yaw
                component = bot.body_script.component
                instance = next(i for i in component.instances.values() if i.graph.index == 0x1408)
                bot.firing = True
                component.edges([(35, True)])
                for _ in range(65):
                    run.step({})
                    motion = instance.states.get(33)
                    if motion is not None and motion.active:
                        break
                self.assertIsNotNone(motion)
                self.assertTrue(motion.active)
                self.assertIsNone(component.fault)
                config = motion.node.field('m_BC5E91CF')[0]['m_9B7A63EA']
                vector = motion.evaluate(config, watch=False)
                for actual, wanted in zip((vector.x, vector.y, vector.z), expected, strict=True):
                    self.assertAlmostEqual(actual, wanted)

    def test_heavy_autonomously_charges_visible_ana_and_rejects_cover_or_ally(self):
        definition = next(d for d in self.catalog.values() if d.hero == 0x02E00000000001BA)
        for gate in (None, "ally", "cover"):
            with self.subTest(gate=gate):
                run, ana, bot, _ = self.fight(definition, ally=True, distance=6,
                                             acquire=True, specials=True)
                if gate != "ally":
                    ana.team = 1 - bot.team
                if gate == "cover":
                    set_static_wall(run, ana, 3)
                fight = run.match.combat
                target = next(target for target in fight._targets() if target.owner is ana)
                vitals = fight.vitals_of(target)
                component = bot.body_script.component
                instance = next(i for i in component.instances.values() if i.graph.index == 0x1408)
                start = bot.position
                for _ in range(100):
                    run.step({})
                    if vitals.total < 200:
                        break
                motion = instance.states.get(33)
                if gate is None:
                    self.assertIsNotNone(motion)
                    self.assertTrue(motion.active)
                    self.assertLess(bot.position[2], start[2] - 2)
                    self.assertEqual(vitals.total, 150)
                else:
                    self.assertTrue(motion is None or motion.counter == 0)
                    self.assertEqual(bot.position, start)
                    self.assertEqual(vitals.total, 200)
                self.assertIsNone(component.fault)

    @unittest.skipUnless(os.environ.get(enforcer_controller.SUPPLEMENT), "explicit controller supplement required")
    def test_heavy_body_gate_blocks_new_melee_and_charge_on_visible_ana(self):
        definition = next(d for d in self.catalog.values() if d.hero == enforcer_controller.HEAVY_ASSAULT)
        for distance, damage in ((1.2, 80), (6, 50)):
            with self.subTest(distance=distance):
                run, ana, bot, _ = self.fight(definition, ally=True, distance=distance,
                                             acquire=True, specials=True)
                fight = run.match.combat
                target = next(target for target in fight._targets() if target.owner is ana)
                vitals = fight.vitals_of(target)
                body = bot.body_script.component
                controller = bot.heavy_controller
                instance, = controller.instances.values()
                gate = instance.node_state(instance.graph.node(enforcer_controller.HEAVY_BODY_GATE))
                body.set(888, True)
                ana.team = 1 - bot.team
                start = bot.position
                for _ in range(35):
                    run.step({})
                self.assertEqual(controller.world.selected, ana.body)
                self.assertEqual(controller.entity_var(1650).value(), expr.Entity(ana.body))
                self.assertFalse(gate.current)
                self.assertFalse(bot.firing)
                self.assertNotIn(npc.MELEE_BUTTON, body.held)
                self.assertNotIn(npc.npc_navigation.CHARGE_BUTTON, body.held)
                self.assertEqual(bot.position, start)
                self.assertEqual(vitals.total, 200)
                body.set(888, False)
                for _ in range(100):
                    run.step({})
                    if vitals.total < 200:
                        break
                self.assertTrue(gate.current)
                self.assertEqual(vitals.total, 200 - damage)

    def test_heavy_charge_stops_on_static_wall_and_rearms_after_cooldown(self):
        definition = next(d for d in self.catalog.values() if d.hero == 0x02E00000000001BA)
        run, ana, bot, _ = self.fight(definition, distance=8)
        fight = run.match.combat
        component = bot.body_script.component
        instance = next(i for i in component.instances.values() if i.graph.index == 0x1408)
        start = bot.position
        wall_z = ana.mover.position[2] + 4
        set_static_wall(run, ana, 4)
        bot.firing = True
        component.edges([(35, True)])
        for _ in range(120):
            run.step({})
            self.assertGreater(bot.position[2], wall_z)
            ability = instance.states.get(41)
            if ability is not None and ability.remaining() > 0:
                break
        self.assertLess(bot.position[2], start[2] - 2)
        self.assertLess(bot.position[2], wall_z + 1.5)
        self.assertAlmostEqual(ability.remaining(), 15)
        self.assertFalse(instance.states[33].active)
        self.assertEqual(instance.vars[59].value(), 0)
        self.assertEqual(bot.velocity, (0.0, 0.0, 0.0))
        self.assertIsNone(component.fault)
        target = next(target for target in fight._targets() if target.owner is ana)
        self.assertEqual(fight.vitals_of(target).total, 200)
        stopped = bot.position
        component.edges([(35, True)])
        for _ in range(65):
            run.step({})
        self.assertEqual(bot.position, stopped)
        self.assertFalse(instance.states[33].active)
        self.assertGreater(ability.remaining(), 0)
        component.edges([(35, False)])
        for _ in range(950):
            run.step({})
            if ability.remaining() <= 0:
                break
        self.assertLessEqual(ability.remaining(), 0)
        self.assertEqual(bot.position, stopped)
        bot.yaw = 0
        component.edges([(35, True)])
        for _ in range(85):
            run.step({})
            if bot.position[2] > stopped[2] + 1:
                break
        self.assertGreater(bot.position[2], stopped[2] + 1)
        self.assertTrue(instance.states[33].active)
        self.assertIsNone(component.fault)

    def test_ana_sleep_dart_interrupts_a_moving_heavy_charge(self):
        definition = next(d for d in self.catalog.values() if d.hero == 0x02E00000000001BA)
        run, ana, bot, entry = self.fight(definition, distance=18)
        fight = run.match.combat
        component = bot.body_script.component
        instance = next(i for i in component.instances.values() if i.graph.index == 0x1408)
        start = bot.position
        bot.firing = True
        component.edges([(35, True)])
        for _ in range(65):
            run.step({})
            if bot.position != start:
                break
        self.assertLess(bot.position[2], start[2])
        direction = aim(fight._eye(ana), (entry.feet[0], entry.feet[1] + 1, entry.feet[2]))
        run.step({ana: (*direction, 8)})
        for _ in range(60):
            run.step({})
            if fight.is_asleep(bot):
                break
        self.assertTrue(fight.is_asleep(bot))
        run.step({})
        stopped = bot.position
        self.assertFalse(instance.states[33].active)
        self.assertGreater(instance.states[41].remaining(), 0)
        self.assertEqual(bot.velocity, (0.0, 0.0, 0.0))
        for _ in range(50):
            run.step({})
        self.assertEqual(bot.position, stopped)
        self.assertTrue(fight.is_asleep(bot))
        self.assertIsNone(component.fault)

    def test_heavy_charge_sweep_hits_ana_once_and_spares_ally_and_wide_miss(self):
        definition = next(d for d in self.catalog.values() if d.hero == 0x02E00000000001BA)
        for ally, lateral, expected in ((False, 0, 150), (True, 0, 200), (False, 4, 200)):
            with self.subTest(ally=ally, lateral=lateral):
                run, ana, bot, _ = self.fight(definition, ally=ally, distance=6)
                x, y, z = bot.position
                bot.position = (x + lateral, y, z)
                fight = run.match.combat
                component = bot.body_script.component
                instance = next(i for i in component.instances.values() if i.graph.index == 0x1408)
                bot.firing = True
                component.edges([(35, True)])
                for _ in range(105):
                    run.step({})
                target = next(target for target in fight._targets() if target.owner is ana)
                self.assertEqual(fight.vitals_of(target).total, expected)
                self.assertLess(bot.position[2], z - 6)
                self.assertTrue(instance.states[13].counter)
                self.assertIsNone(component.fault)

    def test_heavy_charge_flank_knockback_and_projected_barrier(self):
        definition = next(d for d in self.catalog.values() if d.hero == 0x02E00000000001BA)
        for protected in (False, True):
            with self.subTest(protected=protected):
                run, ana, bot, _, caster = self.projected_fight(definition)
                caster.team = ana.team
                x, y, z = bot.position
                bot.position = (x + 1.25, y, z)
                fight = run.match.combat
                target = next(target for target in fight._targets() if target.owner is ana)
                barrier = self.project(run, caster, target) if protected else None
                start = tuple(ana.mover.position)
                peak = start[1]
                bot.firing = True
                bot.body_script.component.edges([(35, True)])
                for _ in range(105):
                    run.step({})
                    peak = max(peak, ana.mover.position[1])
                if protected:
                    self.assertEqual(barrier.health, 150)
                    self.assertEqual(fight.vitals_of(target).total, 200)
                    self.assertEqual(peak, start[1])
                    self.assertEqual(tuple(ana.mover.position), start)
                else:
                    self.assertEqual(fight.vitals_of(target).total, 150)
                    self.assertGreater(peak, start[1] + 0.5)
                    self.assertLess(ana.mover.position[2], start[2] - 0.03)
                self.assertIsNone(bot.body_script.component.fault)

    def test_heavy_charge_direct_branch_requires_flag_and_obeys_cover_and_barrier(self):
        definition = next(d for d in self.catalog.values() if d.hero == 0x02E00000000001BA)
        for defense in (None, "cover", "barrier"):
            with self.subTest(defense=defense):
                run, ana, bot, _, caster = self.projected_fight(definition)
                caster.team = ana.team
                if defense == "cover":
                    set_static_wall(run, ana, 2)
                # Diagnostic receiver for the authored filter. Ana has no natural writer here.
                ana.body_script.component.set(961, True)
                fight = run.match.combat
                target = next(target for target in fight._targets() if target.owner is ana)
                barrier = self.project(run, caster, target) if defense == "barrier" else None
                component = bot.body_script.component
                instance = next(i for i in component.instances.values() if i.graph.index == 0x1408)
                start = tuple(ana.mover.position)
                bot.firing = True
                component.edges([(35, True)])
                for _ in range(110):
                    run.step({})
                self.assertEqual(fight.vitals_of(target).total, 25 if defense is None else 200)
                self.assertEqual(tuple(ana.mover.position), start)
                if defense == "cover":
                    self.assertNotIn(53, instance.states)
                else:
                    self.assertEqual(instance.states[53].counter, 1)
                if barrier is not None:
                    self.assertEqual(barrier.health, 25)
                self.assertFalse(instance.states[33].active)
                self.assertGreater(instance.states[41].remaining(), 0)
                self.assertNotIn(35, component.held)
                self.assertEqual(bot.velocity, (0.0, 0.0, 0.0))
                self.assertIsNone(component.fault)

    def test_heavy_charge_hits_hostile_ice_wall_and_stops_on_friendly_wall(self):
        definition = next(d for d in self.catalog.values() if d.hero == 0x02E00000000001BA)
        for hostile in (False, True):
            with self.subTest(hostile=hostile):
                run, ana, bot, _ = self.fight(definition, distance=8, with_zarya=True)
                mei = run.players[1]
                self.assertTrue(run.match.switch_hero(mei, find_hero('Mei')))
                mei.team = ana.team if hostile else bot.team
                x, y, z = ana.mover.position
                mei.mover.position[:] = (x, y, z - 3)
                for _ in range(25):
                    run.step({})
                fight = run.match.combat
                direction = aim(fight._eye(mei), (x, y, z + 4))
                for buttons in (16, 0, 1, 0):
                    run.step({mei: (*direction, buttons)})
                pieces = tuple(fight.ice_wall.pieces.values())
                self.assertEqual(len(pieces), 5)
                self.assertTrue(all(piece.charge_contact for piece in pieces))
                centre = min(pieces, key=lambda piece: abs(piece.feet[0] - x))
                component = bot.body_script.component
                instance = next(i for i in component.instances.values() if i.graph.index == 0x1408)
                bot.firing = True
                component.edges([(35, True)])
                for _ in range(115):
                    run.step({})
                self.assertEqual(centre.health, 225 if hostile else 400)
                self.assertTrue(all(piece.health == 400 for piece in pieces if piece is not centre))
                target = next(target for target in fight._targets() if target.owner is ana)
                self.assertEqual(fight.vitals_of(target).total, 200)
                self.assertGreater(bot.position[2], centre.feet[2] + centre.depth)
                self.assertFalse(instance.states[33].active)
                self.assertGreater(instance.states[41].remaining(), 0)
                self.assertIsNone(component.fault)

    def test_mei_wall_lifts_and_interrupts_active_heavy_charge(self):
        definition = next(d for d in self.catalog.values() if d.hero == enforcer_controller.HEAVY_ASSAULT)
        run, ana, bot, _ = self.fight(definition, distance=8, with_zarya=True)
        mei = run.players[1]
        self.assertTrue(run.match.switch_hero(mei, find_hero("Mei")))
        mei.client = NPCFixtureClient(mei)
        x, y, z = ana.mover.position
        mei.mover.position[:] = (x + 5, y, z + 5)
        for _ in range(25):
            run.step({})
        fight = run.match.combat
        start = bot.position
        direction = aim(fight._eye(mei), start)
        run.step({mei: (*direction, 16)})
        run.step({mei: (*direction, 0)})
        component = bot.body_script.component
        instance = next(i for i in component.instances.values() if i.graph.index == 0x1408)
        bot.firing = True
        component.edges([(npc.npc_navigation.CHARGE_BUTTON, True)])
        for _ in range(65):
            run.step({})
            if bot.position[2] < start[2] - .25:
                break
        self.assertLess(bot.position[2], start[2] - .25)
        self.assertTrue(instance.states[33].active)
        self.assertTrue(instance.states[36].active)
        target = next(target for target in fight._targets() if target.owner is ana)
        vitals = fight.vitals_of(target)
        self.assertEqual(vitals.total, 200)
        run.step({mei: (*aim(fight._eye(mei), bot.position), 1)})
        self.assertTrue(hasattr(bot, "wall_mover"))
        self.assertAlmostEqual(bot.position[1], y + 4.4, delta=.002)
        self.assertFalse(instance.states[33].active)
        self.assertFalse(instance.states[36].active)
        self.assertGreater(instance.states[41].remaining(), 0)
        self.assertNotIn(bot.entity, fight.pins)
        run.step({mei: (0, 0, 0)})
        self.assertNotIn(npc.npc_navigation.CHARGE_BUTTON, component.held)
        run.step({mei: (0, 0, 16)})
        self.assertFalse(fight.ice_wall.pieces)
        for _ in range(100):
            run.step({})
            if not hasattr(bot, "wall_mover"):
                break
        self.assertFalse(hasattr(bot, "wall_mover"))
        self.assertAlmostEqual(bot.position[1], y, delta=.002)
        self.assertFalse(instance.states[33].active)
        self.assertGreater(instance.states[41].remaining(), 0)
        self.assertEqual(vitals.total, 200)
        self.assertIsNone(component.fault)

    def test_heavy_charge_carries_flagged_receiver_until_wall_or_timeout(self):
        definition = next(d for d in self.catalog.values() if d.hero == 0x02E00000000001BA)
        for wall in (False, True):
            with self.subTest(wall=wall):
                run, ana, bot, _ = self.fight(definition, distance=6)
                if wall:
                    set_static_wall(run, ana, -5)
                # Diagnostic receiver for the authored frontal carry predicate.
                ana.body_script.component.set(6700, True)
                start = ana.mover.position[2]
                fight = run.match.combat
                target = next(target for target in fight._targets() if target.owner is ana)
                vitals = fight.vitals_of(target)
                component = bot.body_script.component
                instance = next(i for i in component.instances.values() if i.graph.index == 0x1408)
                bot.firing = True
                component.edges([(35, True)])
                travel = 0
                for _ in range(260):
                    run.step({ana: (0, 0, 1)})
                    if fight.is_stunned(ana):
                        travel = max(travel, start - ana.mover.position[2])
                        self.assertEqual(vitals.total, 200)
                        self.assertEqual(ana.body_script.buttons, 0)
                    elif travel:
                        break
                self.assertGreater(travel, 3 if wall else 20)
                self.assertFalse(fight.is_stunned(ana))
                self.assertNotIn(bot.entity, fight.pins)
                self.assertFalse(instance.states[33].active)
                self.assertGreater(instance.states[41].remaining(), 0)
                self.assertEqual(vitals.total, 0 if wall else 200)
                if wall:
                    self.assertEqual(instance.states[11].counter, 1)
                    self.assertEqual(vitals.killer, bot.entity)
                else:
                    self.assertNotIn(11, instance.states)
                self.assertIsNone(component.fault)

    def heavy_charge_mech(self, *, wall):
        if matrix.MECH not in mover.loaded_mover_table() or graph.entity_body(matrix.MECH) is None:
            self.skipTest("requires the combined authored NPC and 0407 runtime")
        definition = next(d for d in self.catalog.values() if d.hero == 0x02E00000000001BA)
        run, ana, bot, _ = self.fight(definition, with_zarya=True)
        mech = run.players[1]
        self.assertTrue(run.match.switch_hero(mech, replace(heroes()[matrix.HERO], body=matrix.MECH)))
        x, y, z = ana.mover.position
        mech.team = ana.team
        mech.mover.position[:] = x, y, z
        ana.mover.position[:] = x + 1.7, y, z + 1
        set_static_wall(run, mech, wall)
        for _ in range(100):
            run.step({})
        self.assertTrue(mech.body_script.component.entity_var(6700, create=False).value())
        fight = run.match.combat
        targets = {target.entity: target for target in fight._targets()}
        vitals = fight.vitals_of(targets[mech.body])
        self.assertEqual(vitals.total, 600)
        component = bot.body_script.component
        instance = next(i for i in component.instances.values() if i.graph.index == 0x1408)
        bot.firing = True
        component.edges([(35, True)])
        return run, ana, bot, mech, vitals, instance, z

    def test_heavy_charge_carries_authored_mech_while_side_sweeping_ana(self):
        run, ana, bot, mech, vitals, instance, z = self.heavy_charge_mech(wall=-5)
        fight = run.match.combat
        travel = 0
        for _ in range(120):
            run.step({})
            if fight.is_stunned(mech):
                travel = max(travel, z - mech.mover.position[2])
                self.assertEqual(vitals.total, 600)
        self.assertGreater(travel, 3)
        self.assertEqual(vitals.total, 305)
        self.assertEqual(fight.vitals[ana.body].total, 150)
        self.assertFalse(fight.is_stunned(mech))
        self.assertEqual(instance.states[11].counter, 1)
        self.assertNotIn(53, instance.states)
        self.assertIsNone(bot.body_script.component.fault)

    def wait_for_heavy_mech_carry(self, run, bot, mech, z):
        fight = run.match.combat
        for _ in range(110):
            run.step({})
            if fight.is_stunned(mech) and mech.mover.position[2] < z - 0.5:
                break
        self.assertEqual(fight.pins, {bot.entity: mech.body})

    def test_ana_sleep_dart_releases_mech_already_carried_by_heavy_charge(self):
        run, ana, bot, mech, vitals, instance, z = self.heavy_charge_mech(wall=-14)
        fight = run.match.combat
        self.wait_for_heavy_mech_carry(run, bot, mech, z)
        for tick in range(55):
            x, y, z = bot.position
            direction = aim(fight._eye(ana), (x, y + 1, z - 1.5))
            run.step({ana: (*direction, 8 if tick == 0 else 0)})
            if fight.is_asleep(bot):
                break
        self.assertTrue(fight.is_asleep(bot))
        run.step({})
        stopped = bot.position
        self.assertFalse(fight.is_stunned(mech))
        self.assertFalse(instance.states[33].active)
        self.assertGreater(instance.states[41].remaining(), 0)
        for _ in range(70):
            run.step({})
        self.assertEqual(bot.position, stopped)
        self.assertEqual(vitals.total, 600)
        self.assertNotIn(11, instance.states)
        self.assertIsNone(bot.body_script.component.fault)

    def test_ana_lethal_dart_releases_mech_carried_by_heavy_charge(self):
        run, ana, bot, mech, vitals, instance, z = self.heavy_charge_mech(wall=-14)
        fight = run.match.combat
        self.wait_for_heavy_mech_carry(run, bot, mech, z)
        mech.client = NPCFixtureClient(mech)
        carrier = fight.vitals_of(next(target for target in fight._targets() if target.owner is bot))
        carrier.damage(carrier.total - 1)
        for tick in range(55):
            x, y, z = bot.position
            direction = aim(fight._eye(ana), (x, y + 1, z - 1.5))
            run.step({ana: (*direction, 8 if tick == 0 else 0)})
            if carrier.dead:
                break
        self.assertTrue(carrier.dead)
        self.assertEqual(carrier.killer, ana.body)
        self.assertTrue(any(entity == bot.entity for entity, _ in mech.client.bot_deaths))
        self.assertFalse(fight.is_asleep(bot))
        self.assertFalse(fight.is_stunned(mech))
        self.assertNotIn(bot.entity, fight.pins)
        stopped = bot.position
        for _ in range(70):
            run.step({})
        self.assertEqual(bot.position, stopped)
        self.assertEqual(vitals.total, 600)
        self.assertNotIn(11, instance.states)
        self.assertIsNone(bot.body_script.component.fault)

    def test_heavy_charge_releases_when_the_carried_body_is_replaced(self):
        run, _ana, bot, mech, _, instance, z = self.heavy_charge_mech(wall=-14)
        fight = run.match.combat
        self.wait_for_heavy_mech_carry(run, bot, mech, z)
        old = mech.body
        self.assertTrue(run.match.switch_hero(mech, mech.hero))
        self.assertNotEqual(mech.body, old)
        self.assertNotIn(old, fight.pins.values())
        self.assertFalse(instance.states[33].active)
        run.step({})
        stopped = bot.position
        for _ in range(70):
            run.step({})
        self.assertEqual(bot.position, stopped)
        self.assertFalse(fight.is_stunned(mech))
        self.assertFalse(instance.states[33].active)
        self.assertGreater(instance.states[41].remaining(), 0)
        replacement = next(target for target in fight._targets() if target.owner is mech)
        self.assertEqual(fight.vitals_of(replacement).total, 600)
        self.assertNotIn(11, instance.states)
        self.assertIsNone(bot.body_script.component.fault)

    def start_assassin_pin(self, *, support=False):
        definition = next(d for d in self.catalog.values() if d.hero == assassin.HERO)
        run, ana, bot, entry = self.fight(definition, distance=2.0, with_zarya=support)
        if support:
            helper = run.players[1]
            self.assertTrue(run.match.switch_hero(helper, heroes()[ANA]))
            helper.team = ana.team
            x, y, z = bot.position
            helper.mover.position = [x + 4, y, z]
            for _ in range(25):
                run.step({})
        fight = run.match.combat
        target = next(t for t in fight._targets() if t.owner is ana)
        self.assertTrue(assassin.engage(run.match, bot, ana))
        for _ in range(30):
            run.step({})
            if fight.pins:
                break
        self.assertEqual(fight.pins, {bot.entity: ana.body})
        self.assertEqual(fight.vitals_of(target).total, 175)
        return run, ana, bot, entry, target

    def test_assassin_actual_pin_damage_clock_and_control(self):
        run, ana, bot, _entry, target = self.start_assassin_pin()
        fight = run.match.combat
        instance = next(i for i in bot.body_script.component.instances.values()
                        if i.graph.index == assassin.GRAPH)
        state_id = instance.graph.node(15).state
        for _ in range(180):
            run.step({ana: (0, 0, 1)})
            state = instance.states.get(state_id)
            if state is not None and state.active:
                break
        self.assertTrue(state is not None and state.active)
        self.assertEqual(fight.pins, {bot.entity: ana.body})
        before = fight.vitals_of(target).total
        for _ in range(10):
            run.step({ana: (0, 0, 1)})
        self.assertAlmostEqual(fight.vitals_of(target).total, before - 16, places=4)
        self.assertEqual(ana.body_script.buttons, 0)
        self.assertTrue(fight.is_stunned(ana))
        deadline = fight.tick + 10
        fight.stuns[ana.body] = deadline
        assassin.stop(bot)
        run.step({})
        self.assertFalse(fight.pins)
        self.assertEqual(fight.stuns[ana.body], deadline)
        self.assertNotIn(assassin.BUTTON, bot.body_script.component.held)

    @unittest.skipUnless(os.environ.get(enforcer_controller.SUPPLEMENT), "requires the controller supplement")
    def test_assassin_live_controller_publishes_target_and_delivers_attack(self):
        definition = next(d for d in self.catalog.values() if d.hero == assassin.HERO)
        run, ana, bot, _entry = self.fight(definition, distance=2)
        bot.spawner.update(acquire_targets=True, acquire_specials=True)
        fight = run.match.combat
        vitals = fight.vitals_of(next(target for target in fight._targets() if target.owner is ana))
        deliveries = []
        send = runtime.send_message

        def observe(component, message, params, sender):
            receivers = send(component, message, params, sender)
            if message == assassin_controller.ATTACK:
                deliveries.append((component.entity, sender, receivers))
            return receivers

        with patch.object(runtime, "send_message", side_effect=observe):
            run.step({})
            controller = bot.assassin_controller
            target = controller.entity_var(1650, create=False)
            self.assertEqual(expr.to_entity(target.value()), ana.body)
            instance, = controller.instances.values()
            windup = instance.node_state(instance.graph.node(6))
            self.assertTrue(windup.active)
            for _ in range(20):
                run.step({})
                self.assertFalse(fight.pins)
                self.assertEqual(vitals.total, 200)
            origin = bot.position
            x, y, z = origin
            bot.position = x, y, z + 4
            run.step({})
            self.assertEqual(target.value(), 0)
            self.assertFalse(windup.active)
            bot.position = origin
            run.step({})
            self.assertEqual(expr.to_entity(target.value()), ana.body)
            self.assertTrue(windup.active)
            for _ in range(47):
                self.assertFalse(deliveries)
                self.assertFalse(fight.pins)
                self.assertEqual(vitals.total, 200)
                run.step({})
                if deliveries:
                    break
            self.assertEqual(deliveries, [(bot.entity, controller.entity, 2)])
            self.assertEqual(windup.ended_at - windup.start, 750)
            self.assertEqual(fight.pins, {bot.entity: ana.body})
            self.assertEqual(vitals.total, 175)
            self.assertEqual(controller.world.attack_target, ana.body)
            stored = _Graph({"combat": fight, "match": run.match}, None)
            stored.value(controller)
            stored.finish()

        bot.position = x, y, z + 4
        run.step({})
        self.assertEqual(target.value(), 0)
        self.assertIsNone(controller.world.attack_target)
        bot.position = origin
        run.step({})
        self.assertEqual(expr.to_entity(target.value()), ana.body)
        self.assertFalse(fight.pins)
        self.assertIsNone(controller.fault)

    @unittest.skipUnless(os.environ.get(enforcer_controller.SUPPLEMENT), "requires the controller supplement")
    def test_assassin_preference_restarts_windup_and_clears_when_target_leaves(self):
        definition = next(d for d in self.catalog.values() if d.hero == assassin.HERO)
        run, ana, bot, _ = self.fight(definition, distance=2, with_zarya=True)
        other = run.players[1]
        other.client = NPCFixtureClient(other)
        other.team = bot.team
        x, y, z = bot.position
        other.spawn = (x + 1.8, y, z), other.spawn[1]
        other.mover = other.new_mover()
        bot.spawner.update(acquire_targets=True, acquire_specials=True)
        fight = run.match.combat
        run.step({})
        controller = bot.assassin_controller
        instance, = controller.instances.values()
        windup = instance.node_state(instance.graph.node(6))
        selected = controller.entity_var(1650)
        observed = self.enterContext(patch.object(runtime, "send_message", wraps=runtime.send_message))
        self.assertEqual(selected.value(), expr.Entity(ana.body))
        initial = windup.start

        def prefer(player):
            self.assertEqual(runtime.send_message(controller, assassin_controller.PREFERENCE,
                                                  {0x5C5: expr.Entity(player.body)}, 0xB0001000), 1)
            run.step({})

        prefer(other)
        self.assertEqual(controller.entity_var(assassin_controller.PREFERRED_BODY).value(), expr.Entity(0))
        self.assertEqual(windup.start, initial)
        other.team = ana.team
        for _ in range(20):
            run.step({})
        self.assertEqual(windup.start, initial)
        self.assertEqual(selected.value(), expr.Entity(ana.body))

        prefer(other)
        self.assertEqual(selected.value(), expr.Entity(other.body))
        self.assertGreater(windup.start, initial)
        switched = windup.start
        for _ in range(30):
            run.step({})
            self.assertFalse(fight.pins)
        prefer(ana)
        self.assertEqual(selected.value(), expr.Entity(ana.body))
        self.assertGreater(windup.start, switched)

        ana.team = bot.team
        run.step({})
        self.assertEqual(controller.entity_var(assassin_controller.PREFERRED_BODY).value(), expr.Entity(0))
        self.assertEqual(selected.value(), expr.Entity(other.body))
        self.assertEqual(controller.world.windup_target, other.body)
        self.assertIsNone(controller.world.attack_target)
        self.assertFalse(any(call.args[1] == assassin_controller.ATTACK for call in observed.call_args_list))
        stored = snapshot_digest(export_component_vm(controller))
        controller.world.windup_target = 0
        self.assertNotEqual(snapshot_digest(export_component_vm(controller)), stored)
        controller.world.windup_target = other.body
        self.assertEqual(snapshot_digest(export_component_vm(controller)), stored)
        for _ in range(47):
            self.assertFalse(fight.pins)
            run.step({})
            if fight.pins:
                break
        self.assertEqual(windup.ended_at - windup.start, 750)
        handoff, = [call for call in observed.call_args_list if call.args[1] == assassin_controller.ATTACK]
        self.assertIs(handoff.args[0], bot.body_script.component)
        self.assertEqual(fight.pins, {bot.entity: other.body})
        self.assertEqual(fight.vitals_of(next(t for t in fight._targets() if t.owner is ana)).total, 200)
        self.assertEqual(fight.vitals_of(next(t for t in fight._targets() if t.owner is other)).total, 175)

    @unittest.skipUnless(os.environ.get(enforcer_controller.SUPPLEMENT), "requires the controller supplement")
    def test_ana_sleep_dart_cancels_assassin_handoff_before_contact(self):
        definition = next(d for d in self.catalog.values() if d.hero == assassin.HERO)
        run, ana, bot, entry = self.fight(definition, distance=2)
        bot.spawner.update(acquire_targets=True, acquire_specials=True)
        fight = run.match.combat
        ana_health = fight.vitals_of(next(target for target in fight._targets() if target.owner is ana))
        run.step({})
        controller = bot.assassin_controller
        instance, = controller.instances.values()
        windup = instance.node_state(instance.graph.node(6))
        self.assertTrue(windup.active)
        direction = aim(fight._eye(ana), (entry.feet[0], entry.feet[1] + 1, entry.feet[2]))
        run.step({ana: (*direction, 8)})
        for _ in range(40):
            if fight.is_asleep(bot):
                break
            run.step({})
        self.assertTrue(fight.is_asleep(bot))
        run.step({})
        self.assertFalse(windup.active)
        self.assertIsNone(controller.world.attack_target)
        self.assertFalse(fight.pins)
        self.assertEqual(ana_health.total, 200)

        while fight.is_asleep(bot):
            run.step({})
        self.assertEqual(ana_health.total, 200)
        for _ in range(48):
            run.step({})
            if fight.pins:
                break
        self.assertEqual(windup.ended_at - windup.start, 750)
        self.assertEqual(fight.pins, {bot.entity: ana.body})
        self.assertEqual(ana_health.total, 175)

    def test_assassin_autonomous_recontact_waits_for_authored_release(self):
        definition = next(d for d in self.catalog.values() if d.hero == assassin.HERO)
        run, ana, bot, _entry = self.fight(definition, distance=2)
        bot.spawner.update(acquire_targets=True, acquire_specials=True)
        fight = run.match.combat
        body = bot.body_script.component
        instance = next(item for item in body.instances.values() if item.graph.index == assassin.GRAPH)
        target = next(item for item in fight._targets() if item.owner is ana)
        vitals = fight.vitals_of(target)

        for _ in range(48):
            run.step({})
            if fight.pins:
                break
        self.assertEqual(fight.pins, {bot.entity: ana.body})
        self.assertEqual(vitals.total, 175)
        origin = bot.position
        x, y, z = origin
        bot.position = x, y, z + 4
        run.step({})
        self.assertFalse(fight.pins)
        self.assertNotIn(assassin.BUTTON, body.held)
        release = instance.node_state(instance.graph.node(37))
        animation = instance.node_state(instance.graph.node(31))
        self.assertTrue(release.active)
        self.assertTrue(animation.active)
        recovery_start = release.start
        animation_start = animation.start

        bot.position = origin
        for _ in range(126):
            if not release.active:
                break
            run.step({})
            self.assertFalse(fight.pins)
            self.assertEqual(vitals.total, 175)
            self.assertNotIn(assassin.BUTTON, body.held)
        self.assertFalse(release.active)
        self.assertEqual(release.ended_at - recovery_start, 2000)
        self.assertFalse(animation.active)
        self.assertEqual(animation.ended_at - animation_start, 1467)

        run.step({})
        self.assertEqual(fight.pins, {bot.entity: ana.body})
        self.assertEqual(vitals.total, 150)
        self.assertIsNone(body.fault)

    def test_assassin_autonomous_recontact_waits_for_queued_release(self):
        run, ana, bot, _entry, target = self.start_assassin_pin()
        bot.spawner.update(acquire_targets=True, acquire_specials=True)
        fight = run.match.combat
        body = bot.body_script.component
        instance = next(item for item in body.instances.values() if item.graph.index == assassin.GRAPH)

        assassin.stop(bot)
        release = instance.node_state(instance.graph.node(37))
        self.assertFalse(release.active)
        run.step({})

        self.assertTrue(release.active)
        self.assertFalse(fight.pins)
        self.assertEqual(fight.vitals_of(target).total, 175)
        self.assertNotIn(assassin.BUTTON, body.held)

    def test_actual_ally_sleep_dart_releases_assassin_pin(self):
        run, ana, bot, entry, target = self.start_assassin_pin(support=True)
        helper = run.players[1]
        fight = run.match.combat
        direction = aim(fight._eye(helper), (entry.feet[0], entry.feet[1] + 1, entry.feet[2]))
        run.step({helper: (*direction, 8)})
        for _ in range(40):
            run.step({helper: (*direction, 0)})
            if fight.is_asleep(bot):
                run.step({})
                break
        self.assertTrue(fight.is_asleep(bot))
        self.assertFalse(fight.is_stunned(ana))
        self.assertFalse(fight.pins)
        self.assertIsNone(bot.body_script.component.world.manual_target)
        self.assertEqual(fight.vitals_of(target).total, 175)

    def test_assassin_releases_pin_on_body_replacement_and_removal(self):
        for replacement in (True, False):
            with self.subTest(replacement=replacement):
                run, ana, bot, _entry, _target = self.start_assassin_pin()
                fight = run.match.combat
                old = ana.body
                if replacement:
                    self.assertTrue(run.match.switch_hero(ana, find_hero('Zarya')))
                    self.assertNotEqual(old, ana.body)
                else:
                    self.assertIn(bot.entity, npc.remove_spawned(run.match))
                self.assertFalse(fight.pins)
                run.step({})
                self.assertFalse(fight.is_stunned(ana))
                if replacement:
                    self.assertIsNone(bot.body_script.component.world.manual_target)

    def test_assassin_releases_control_when_either_body_dies(self):
        for source_dies in (True, False):
            with self.subTest(source_dies=source_dies):
                run, ana, bot, entry, target = self.start_assassin_pin()
                fight = run.match.combat
                if source_dies:
                    # Isolate the existing authoritative source death boundary.
                    fight._damage(ana, entry, 1000, False, None, run.frame)
                    self.assertTrue(bot.dead)
                    self.assertIn(bot, run.match.bots)
                    self.assertIsNone(bot.body_script.component.world.manual_target)
                else:
                    # Keep the pin's next real continuous tick lethal.
                    vitals = fight.vitals_of(target)
                    vitals.damage(vitals.total - 1)
                    for _ in range(180):
                        run.step({})
                        if vitals.dead:
                            break
                    self.assertTrue(vitals.dead)
                    self.assertEqual(vitals.killer, bot.entity)
                self.assertFalse(fight.pins)
                self.assertFalse(fight.is_stunned(ana))

    def test_assassin_contact_breaks_on_range_wall_and_team_change(self):
        for reason in ('range', 'wall', 'team'):
            with self.subTest(reason=reason):
                run, ana, bot, _entry, target = self.start_assassin_pin()
                fight = run.match.combat
                if reason == 'range':
                    x, y, z = bot.position
                    bot.position = x, y, z + 2
                elif reason == 'wall':
                    set_static_wall(run, ana, 1)
                else:
                    bot.team = ana.team
                run.step({})
                self.assertFalse(fight.pins)
                self.assertFalse(assassin.engage(run.match, bot, ana))
                self.assertEqual(fight.vitals_of(target).total, 175)

    def test_actual_projected_barrier_blocks_or_cleanses_assassin_pin(self):
        definition = next(d for d in self.catalog.values() if d.hero == assassin.HERO)
        for protect_first in (True, False):
            with self.subTest(protect_first=protect_first):
                helper = projected.AnaProjectedBarrierTests()
                run, ana, caster, _other = helper.fight(ally_ana=True)
                x, y, z = ana.mover.position
                bot = npc.spawn(run.match, definition, (x, y, z + 2), team=1 - ana.team, yaw=-32768)
                fight = run.match.combat
                if protect_first:
                    barrier = helper.cast(run, caster, ana)
                self.assertTrue(assassin.engage(run.match, bot, ana))
                for _ in range(6):
                    run.step({})
                if not protect_first:
                    self.assertEqual(fight.pins, {bot.entity: ana.body})
                    barrier = helper.cast(run, caster, ana)
                    run.step({})
                self.assertFalse(fight.pins)
                self.assertFalse(fight.is_stunned(ana))
                self.assertEqual(barrier.health, 175 if protect_first else 200)
                self.assertEqual(fight.vitals[ana.body].total, 200 if protect_first else 175)

    def test_assassin_pin_interrupts_actual_nano_before_release(self):
        definition = next(d for d in self.catalog.values() if d.hero == assassin.HERO)
        run, ana, bot, _entry = self.fight(definition, distance=2, with_zarya=True)
        ally = run.players[1]
        ally.team = ana.team
        x, y, z = ana.mover.position
        ally.mover.position = [x + 5, y, z]
        fight = run.match.combat
        target = next(t for t in fight._targets() if t.owner is ally)
        vitals = fight.vitals_of(target)
        vitals.damage(100)
        before = vitals.total
        ana.body_script.set_ult_charge(ana.body_script.component.vars[338].value())
        direction = aim(fight._eye(ana), (target.feet[0], target.feet[1] + 1, target.feet[2]))
        run.step({ana: (*direction, 32)})
        ultimate = ana.body_script.ultimates[0]
        self.assertTrue(ultimate.flags & nodes.Ability.RUNNING)
        self.assertEqual(ana.body_script.component.vars[342].value(), 0)
        self.assertTrue(assassin.engage(run.match, bot, ana))
        for _ in range(20):
            run.step({})
        self.assertTrue(fight.is_stunned(ana))
        self.assertFalse(ultimate.flags & nodes.Ability.RUNNING)
        self.assertLess(ana.body_script.component.vars[342].value(),
                        ana.body_script.component.vars[338].value())
        self.assertEqual(vitals.total, before)
        self.assertEqual(vitals.nano_until, 0)

    @unittest.skipUnless(os.environ.get(enforcer_controller.SUPPLEMENT), "explicit controller supplement required")
    def test_enforcer_live_target_graph_gates_cannon_and_reacquires_after_cover(self):
        self.check_live_npc_target_graph(enforcer_controller.ENFORCER)

    @unittest.skipUnless(os.environ.get(enforcer_controller.SUPPLEMENT), "explicit controller supplement required")
    def test_enforcer_live_controller_switches_targets_and_retires_on_ana_dart(self):
        self.check_live_npc_controller_lifetime(enforcer_controller.ENFORCER)

    @unittest.skipUnless(os.environ.get(enforcer_controller.SUPPLEMENT), "explicit controller supplement required")
    def test_trooper_live_target_graph_gates_rifle_and_reacquires_after_cover(self):
        self.check_live_npc_target_graph(enforcer_controller.TROOPER)

    @unittest.skipUnless(os.environ.get(enforcer_controller.SUPPLEMENT), "explicit controller supplement required")
    def test_trooper_live_controller_switches_targets_and_retires_on_ana_dart(self):
        self.check_live_npc_controller_lifetime(enforcer_controller.TROOPER)

    @unittest.skipUnless(os.environ.get(enforcer_controller.SUPPLEMENT), "explicit controller supplement required")
    def test_match_runs_trooper_coordination_and_retires_it_on_ana_kill(self):
        definition=next(d for d in self.catalog.values() if d.hero==npc.TROOPER_HERO)
        run,ana,prior,_=self.fight(definition)
        match=run.match
        fight=match.combat
        x,y,z=prior.position
        npc.remove_spawned(match)
        a=npc.spawn(match,definition,(x,y,z),team=1,acquire=True,authored_trooper=True)
        b=npc.spawn(match,definition,(x+5,y,z+4),team=1,acquire=True,authored_trooper=True)
        for _ in range(35):
            run.step({})
        host=fight.trooper_coordination
        assert set(host.components)=={a.entity,b.entity} and not host.faults
        registrations={(key.entity,key.message,row.state.instance.component.entity) for key,channel in host.messages.manager.channels.items() for row in channel.senders}
        assert len(registrations)==2
        initiator_id=next(sender for target,message,sender in registrations if message==0x0861)
        victim=b if a.entity==initiator_id else a
        v=fight.vitals_of(next(t for t in fight._targets() if t.owner is victim))
        v.damage(v.total-1)
        x,y,z=victim.position
        run.step({ana:(*aim(fight._eye(ana),(x,y+1,z)),1)})
        for _ in range(35):
            run.step({})
            if victim.dead:
                break
        assert victim.dead and v.killer==ana.body and victim in match.bots
        for _ in range(5): run.step({})
        assert set(host.components)=={initiator_id} and not host.faults
        assert not any(c.senders for c in host.messages.manager.channels.values())
        instance,=host.components[initiator_id].instances.values()
        assert instance.context().var(expr.INSTANCE,14087)==()
        assert not instance.context().var(expr.ENTITY,11183)
        npc.remove_spawned(match)
        assert not host.components and not host.messages.manager.channels
        assert not hasattr(fight, 'trooper_coordination')

    def test_mei_wall_lifts_moving_assassin_and_pursuit_resumes_after_landing(self):
        import math
        from ow174.game import npc_navigation

        definition = next(d for d in self.catalog.values() if d.hero == assassin.HERO)
        run, ana, bot, _ = self.fight(definition, ally=True, distance=8, pursue=True, with_zarya=True)
        mei = run.players[1]
        self.assertTrue(run.match.switch_hero(mei, find_hero("Mei")))
        mei.client = NPCFixtureClient(mei)
        mei.team = bot.team
        x, y, z = ana.mover.position
        mei.mover.position[:] = (x + 5, y, z + 3)
        for _ in range(20):
            run.step({})
        fight = run.match.combat
        npc_navigation.stop(bot)
        bot.position = (x, y, z + 8)
        ana.team = 1 - bot.team
        for _ in range(90):
            run.step({})
            if math.hypot(bot.velocity[0], bot.velocity[2]) > 1:
                break
        self.assertGreater(math.hypot(bot.velocity[0], bot.velocity[2]), 1)
        for buttons in (16, 0, 1):
            run.step({mei: (*aim(fight._eye(mei), bot.position), buttons)})
        self.assertTrue(hasattr(bot, "wall_mover"))
        self.assertAlmostEqual(bot.position[1], y + 4.4, delta=.002)
        before = bot.position
        for _ in range(4):
            run.step({})
        self.assertLess(bot.position[2], before[2] - .002)
        self.assertFalse(bot.route)
        self.assertNotIn(bot.entity, fight.pins)
        run.step({mei: (0, 0, 16)})
        self.assertFalse(fight.ice_wall.pieces)
        for _ in range(100):
            run.step({})
            if not hasattr(bot, "wall_mover"):
                break
        self.assertFalse(hasattr(bot, "wall_mover"))
        self.assertAlmostEqual(bot.position[1], y, delta=.002)
        for _ in range(500):
            run.step({})
            if bot.entity in fight.pins:
                break
        self.assertEqual(fight.pins.get(bot.entity), ana.body)

    def test_mei_wall_lifts_and_lands_all_five_stationary_npcs(self):
        from test_game_ana_nano import AnaNanoTests

        for definition in self.catalog.values():
            for recall in (True, False):
                with self.subTest(npc=definition.name, recall=recall):
                    run, ana, mei, _=AnaNanoTests().fight(ally_hero="Mei",distance=8)
                    fight=run.match.combat
                    feet=tuple(ana.mover.position)
                    ana.mover.position[0]+=5
                    bot=npc.spawn(run.match,definition,feet,team=1)
                    direction=aim(fight._eye(mei),feet)
                    for buttons in (16,0,1,0):
                        run.step({mei:(*direction,buttons)})
                    pieces=fight.ice_wall.pieces.values()
                    assert len(pieces)==5,(definition.name,len(pieces))
                    top=max(p.feet[1]+p.height for p in pieces)
                    assert abs(bot.position[1]-top)<.002,(definition.name,bot.position,top)
                    for _ in range(20):
                        run.step({})
                    assert abs(bot.position[1]-top)<.002
                    if recall:
                        run.step({mei: (*direction, 16)})
                    else:
                        expiry = max(piece.expires for piece in pieces)
                        while run.match.tick < expiry:
                            run.step({})
                    assert not fight.ice_wall.pieces
                    for _ in range(14):
                        run.step({})
                    falling=bot.position[1]
                    assert feet[1]<falling<top,(definition.name,falling,top)
                    for _ in range(85):
                        run.step({})
                    assert abs(bot.position[1]-feet[1])<.002 and not hasattr(bot,"wall_mover"),(definition.name,bot.position)

    def test_mei_wall_omits_column_when_heavy_assault_cannot_fit(self):
        from ow174.game.collision import World
        from test_game_ana_nano import AnaNanoTests

        run, ana, mei, _ = AnaNanoTests().fight(ally_hero="Mei", distance=8)
        x, y, z = ana.mover.position
        ceiling = y + 4.8
        run.match.collision_world = World([
            (x - 30, y, z - 30, x - 30, y, z + 30, x + 30, y, z + 30),
            (x - 30, y, z - 30, x + 30, y, z + 30, x + 30, y, z - 30),
            (x - .7, ceiling, z - .7, x + .7, ceiling, z + .7, x - .7, ceiling, z + .7),
            (x - .7, ceiling, z - .7, x + .7, ceiling, z - .7, x + .7, ceiling, z + .7),
        ])
        fight = run.match.combat
        ana.mover.position[0] += 5
        definition = next(d for d in self.catalog.values() if d.name == "Heavy Assault")
        bot = npc.spawn(run.match, definition, (x, y, z), team=1)
        direction = aim(fight._eye(mei), (x, y, z))
        for buttons in (16, 0, 1, 0):
            run.step({mei: (*direction, buttons)})
        pieces = fight.ice_wall.pieces.values()
        self.assertEqual(len(pieces), 4)
        self.assertTrue(all(abs(piece.feet[0] - x) > 1 for piece in pieces))
        self.assertEqual(bot.position, (x, y, z))
        self.assertFalse(hasattr(bot, "wall_mover"))

    def test_ana_sleep_dart_hits_all_five_npcs_above_mei_wall(self):
        from test_game_ana_nano import AnaNanoTests

        for definition in self.catalog.values():
            with self.subTest(npc=definition.name):
                run, ana, mei, _ = AnaNanoTests().fight(ally_hero="Mei", distance=8)
                fight = run.match.combat
                x, y, z = ana.mover.position
                ana.mover.position[0] += 5
                bot = npc.spawn(run.match, definition, (x, y, z), team=1)
                direction = aim(fight._eye(mei), (x, y, z))
                for buttons in (16, 0, 1, 0):
                    run.step({mei: (*direction, buttons)})
                self.assertEqual(len(fight.ice_wall.pieces), 5)
                self.assertAlmostEqual(bot.position[1], y + 4.4, delta=.002)
                ana.mover.position[1] = bot.position[1] + .5
                target = next(target for target in fight._targets() if target.owner is bot)
                vitals = fight.vitals_of(target)
                previous = vitals.total
                direction = aim(fight._eye(ana), (x, bot.position[1] + target.height / 2, z))
                run.step({ana: (*direction, 8)})
                for _ in range(45):
                    if fight.is_asleep(bot):
                        break
                    run.step({})
                self.assertTrue(fight.is_asleep(bot))
                self.assertIn(previous - vitals.total, (2.5, 5))
                self.assertTrue(all(piece.health == 400 for piece in fight.ice_wall.pieces.values()))

    def test_trooper_coordination_tracks_live_tagged_bodies_and_ana_kills(self):
        definition = next(d for d in self.catalog.values() if d.hero == npc.TROOPER_HERO)
        other = next(d for d in self.catalog.values() if d.hero == enforcer_controller.ENFORCER)
        run, ana, source, _ = self.fight(definition, acquire=True)
        match = run.match
        fight = match.combat
        component = source.body_script.component
        instance, = (inst for inst in component.instances.values() if inst.graph.index == 0x1034)
        tracker = instance.states[instance.graph.node(16).state]
        slot = instance.find_var(expr.INSTANCE, 14087, create=False)
        self.assertTrue(tracker.active)
        self.assertEqual(slot.value(), ())
        x, y, z = source.position
        far = npc.spawn(match, definition, (x, y, z + 12), team=source.team)
        near = npc.spawn(match, definition, (x, y, z + 6), team=1 - source.team)
        distant = npc.spawn(match, definition, (x, y, z + 24), team=source.team)
        npc.spawn(match, other, (x + 2, y, z + 3), team=source.team)
        run.step({})
        expected = tuple(expr.Entity(bot.entity) for bot in (near, far))
        self.assertEqual(slot.value(), expected)
        self.assertNotIn(expr.Entity(distant.entity), slot.value())
        distant.position = x, y, z + 19
        run.step({})
        self.assertEqual(slot.value(), (*expected, expr.Entity(distant.entity)))
        distant.position = x, y, z + 24
        clear = match.collision_world
        set_static_wall(run, ana, z - ana.mover.position[2] + 3)
        run.step({})
        self.assertEqual(slot.value(), ())
        match.collision_world = clear
        near.position = x + 5, y, z + 6
        near.team = 1 - ana.team
        run.step({})
        self.assertEqual(slot.value(), expected)
        target = next(t for t in fight._targets() if t.owner is near)
        vitals = fight.vitals_of(target)
        vitals.damage(vitals.total - 1)
        direction = aim(fight._eye(ana), (x + 5, y + 1, z + 6))
        run.step({ana: (*direction, 1)})
        for _ in range(30):
            run.step({})
            if near.dead:
                break
        self.assertTrue(near.dead)
        self.assertEqual(vitals.killer, ana.body)
        self.assertIn(near, match.bots)
        run.step({})
        self.assertEqual(slot.value(), (expr.Entity(far.entity),))
        self.assertIsNone(component.fault)

    @unittest.skipUnless(os.environ.get(enforcer_controller.SUPPLEMENT), "explicit controller supplement required")
    def test_sniper_live_target_graph_gates_rifle_and_reacquires_after_cover(self):
        self.check_live_npc_target_graph(enforcer_controller.SNIPER)

    @unittest.skipUnless(os.environ.get(enforcer_controller.SUPPLEMENT), "explicit controller supplement required")
    def test_sniper_live_controller_switches_targets_and_retires_on_ana_dart(self):
        self.check_live_npc_controller_lifetime(enforcer_controller.SNIPER)

    @unittest.skipUnless(os.environ.get(enforcer_controller.SUPPLEMENT), "explicit controller supplement required")
    def test_heavy_live_target_graph_gates_weapon_and_reacquires_after_cover(self):
        self.check_live_npc_target_graph(enforcer_controller.HEAVY_ASSAULT)

    @unittest.skipUnless(os.environ.get(enforcer_controller.SUPPLEMENT), "explicit controller supplement required")
    def test_heavy_live_controller_switches_targets_and_retires_on_ana_dart(self):
        self.check_live_npc_controller_lifetime(enforcer_controller.HEAVY_ASSAULT)

    def check_live_npc_target_graph(self, hero):
        field, graph_id, _, gate_node, _ = enforcer_controller.HOSTS[hero]
        definition = next(item for item in self.catalog.values() if item.hero == hero)
        run, ana, bot, _ = self.fight(definition, ally=True, distance=6, acquire=True)
        fight = run.match.combat
        vitals = fight.vitals_of(next(target for target in fight._targets() if target.owner is ana))
        component = getattr(bot, field)
        self.assertNotEqual(component.entity, bot.entity)
        instance, = component.instances.values()
        self.assertEqual(instance.graph.index, graph_id)
        gate = instance.node_state(instance.graph.node(gate_node))
        heavy = hero == enforcer_controller.HEAVY_ASSAULT
        self.assertTrue(gate.active)
        self.assertEqual(gate.current, heavy)
        self.assertFalse(bot.firing)
        ana.team = 1
        for _ in range(155):
            run.step({})
            if vitals.total < 200:
                break
        self.assertLess(vitals.total, 200)
        self.assertTrue(gate.current)
        self.assertEqual(component.entity_var(1650).value(), expr.Entity(ana.body))
        self.assertEqual(instance.vars[536].value(), expr.Entity(ana.body))
        self.assertTrue(bot.firing)

        held = bot.body_script.component.world.ai_weapon_button(0)
        if not heavy:
            with patch.object(enforcer_controller.TargetWorld, "target_record_scalar", return_value=0.0):
                gate.dependency_changed()
                run.step({})
                self.assertEqual(component.world.selected, ana.body)
                self.assertFalse(gate.current)
                self.assertFalse(bot.firing)
                self.assertNotIn(held, bot.body_script.component.held)
            gate.dependency_changed()
            run.step({})
            self.assertTrue(gate.current)
            self.assertTrue(bot.firing)

        if graph_id in enforcer_controller.BODY_GATES:
            intro = instance.node_state(instance.graph.node(enforcer_controller.BODY_GATES[graph_id]))
            body = bot.body_script.component
            body.set(888, True)
            run.step({})
            self.assertEqual(component.world.selected, ana.body)
            self.assertFalse(intro.current)
            self.assertFalse(bot.firing)
            self.assertNotIn(held, body.held)
            body.set(888, False)
            run.step({})
            self.assertTrue(intro.current)
            self.assertTrue(bot.firing)

        world = run.match.collision_world
        set_static_wall(run, ana, 3)
        for _ in range(20):
            run.step({})
        self.assertEqual(gate.current, heavy)
        self.assertFalse(bot.firing)
        self.assertEqual(instance.vars[536].value(), expr.Entity(ana.body))
        self.assertEqual(component.entity_var(1650).value(), expr.Entity(ana.body))
        self.assertNotIn(held, bot.body_script.component.held)
        before = vitals.total
        for _ in range(35):
            run.step({})
        self.assertEqual(vitals.total, before)

        run.match.collision_world = world
        run.step({})
        self.assertTrue(gate.current)
        self.assertEqual(component.world.selected, ana.body)
        for _ in range(145):
            if vitals.total < before:
                break
            run.step({})
        self.assertLess(vitals.total, before)
        self.assertIsNone(component.fault)

    def check_live_npc_controller_lifetime(self, hero):
        field, _, _, gate_node, _ = enforcer_controller.HOSTS[hero]
        definition = next(item for item in self.catalog.values() if item.hero == hero)
        run, ana, bot, entry = self.fight(definition, ally=True, distance=6, with_zarya=True, acquire=True)
        fight = run.match.combat
        other = run.players[1]
        other.client = NPCFixtureClient(other)
        x, y, z = ana.mover.position
        AnaOrisaBarrierTests.place(other, (x + 5, y, z))
        other.team = bot.team
        ana.team = 1
        run.step({})
        component = getattr(bot, field)
        instance, = component.instances.values()
        self.assertEqual(instance.vars[536].value(), expr.Entity(ana.body))
        self.assertTrue(instance.node_state(instance.graph.node(gate_node)).current)

        ana.team = bot.team
        other.team = 1
        run.step({})
        self.assertEqual(instance.vars[536].value(), expr.Entity(other.body))
        self.assertEqual(component.entity_var(1650).value(), expr.Entity(other.body))
        self.assertTrue(instance.node_state(instance.graph.node(gate_node)).current)
        self.assertTrue(bot.firing)
        before = snapshot_digest(export_component_vm(component))
        selected = component.world.selected
        component.world.selected = None
        self.assertNotEqual(snapshot_digest(export_component_vm(component)), before)
        component.world.selected = selected
        self.assertEqual(snapshot_digest(export_component_vm(component)), before)
        stored = _Graph({"combat": fight, "match": run.match}, None)
        stored.value(component)
        stored.finish()

        other.team = bot.team
        ana.team = 1
        vitals = fight.vitals_of(entry)
        vitals.damage(vitals.total - 5)
        self.attack(run, ana, entry, 8)
        self.assertTrue(bot.dead)
        self.assertEqual(vitals.killer, ana.body)
        self.assertTrue(instance.stopped)
        self.assertFalse(hasattr(bot, field))

    def test_manual_enforcer_actual_primary_obeys_friend_wall_and_aim(self):
        definition = next(d for d in self.catalog.values() if d.name == 'Enforcer')
        for reason in ('contact', 'friend', 'wall', 'miss'):
            with self.subTest(reason=reason):
                run, ana, bot, _entry = self.fight(definition, ally=reason == 'friend')
                if reason == 'wall':
                    set_static_wall(run, ana, 3)
                if reason == 'miss':
                    bot.yaw = 0
                component = bot.body_script.component
                self.assertEqual(component.world.controller_components, (75,))
                own = next(t for t in run.match.combat._targets() if t.owner is ana)
                bot.firing = True
                component.edges([(component.world.ai_weapon_button(0), True)])
                for _ in range(80):
                    run.step({})
                self.assertEqual(run.match.combat.vitals_of(own).total < 200, reason == 'contact')
                self.assertIsNone(component.fault)

    def test_manual_enforcer_sleep_suppresses_attack_then_actual_rifle_wake_allows_damage(self):
        definition = next(d for d in self.catalog.values() if d.name == 'Enforcer')
        run, ana, bot, entry = self.fight(definition)
        self.attack(run, ana, entry, 8)
        fight = run.match.combat
        self.assertTrue(fight.is_asleep(bot))
        component = bot.body_script.component
        own = next(t for t in fight._targets() if t.owner is ana)
        bot.firing = True
        component.edges([(component.world.ai_weapon_button(0), True)])
        for _ in range(80):
            run.step({})
        self.assertEqual(fight.vitals_of(own).total, 200)
        self.attack(run, ana, entry, 1)
        self.assertFalse(fight.is_asleep(bot))
        for _ in range(180):
            run.step({})
        self.assertLess(fight.vitals_of(own).total, 200)

    def test_sleep_suppresses_five_npc_movement_and_damage_then_wakes(self):
        for definition in self.catalog.values():
            with self.subTest(npc=definition.name):
                run, ana, bot, entry = self.fight(definition)
                fight = run.match.combat
                vitals = fight.vitals_of(entry)
                before = vitals.total
                self.attack(run, ana, entry, 8)
                self.assertEqual(vitals.total, before - 5)
                self.assertTrue(fight.is_asleep(bot))
                # A controlled route/input fixture is not autonomous native AI.
                position = tuple(bot.position)
                destination = (position[0] + 15, position[1], position[2])
                bot.route, bot.target, bot.keys = [destination], destination, True
                bot.firing = True
                button = bot.body_script.component.world.ai_weapon_button(0)
                bot.body_script.component.edges([(button, True)])
                own = next(t for t in fight._targets() if t.owner is ana)
                own_vitals = fight.vitals_of(own)
                health = own_vitals.total
                for _ in range(80):
                    run.step({})
                self.assertEqual(tuple(bot.position), position)
                self.assertEqual(own_vitals.total, health)
                self.assertEqual(bot.velocity, (0, 0, 0))
                # Later real rifle damage wakes the sleeping body.
                bot.route, bot.target, bot.keys = [], None, False
                self.attack(run, ana, entry, 1)
                self.assertEqual(vitals.total, before - 75)
                self.assertFalse(fight.is_asleep(bot))

    def test_nano_heals_and_reduces_damage_on_each_allied_actual_npc(self):
        for definition in self.catalog.values():
            with self.subTest(npc=definition.name):
                run, ana, _bot, entry = self.fight(definition, ally=True)
                fight = run.match.combat
                vitals = fight.vitals_of(entry)
                vitals.damage(180)
                before = vitals.total
                ana.body_script.set_ult_charge(ana.body_script.component.vars[338].value())
                self.attack(run, ana, entry, 32)
                self.assertEqual(vitals.total, min(before + 250, sum(vitals.most.values())))
                self.assertGreater(vitals.nano_until, fight.now)
                # Explicit common damage API, independent of still-open NPC AI.
                health = vitals.total
                fight._damage(ana, entry, 40, False, None, run.frame)
                self.assertEqual(vitals.total, health - 20)


if __name__ == "__main__":
    unittest.main()
