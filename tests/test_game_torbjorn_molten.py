"""Actual Molten Core area damage/lifecycle under the documented contact host."""

import os
import unittest
from dataclasses import replace
from pathlib import Path
from unittest import mock

from test_game_combat import KANEZAKA_DM, Run

from ow174.game import molten_core, npc, stats, world
from ow174.game.collision import World
from ow174.game.commands import Command
from ow174.game.content import SOLDIER, heroes
from ow174.game.script import graph


@unittest.skipUnless(os.environ.get("ORPHEUS_TORBJORN_GRAPH_BUNDLE"), "needs prepared053 status bundle")
class TorbjornMoltenTests(unittest.TestCase):
    @staticmethod
    def clear_graph_cache():
        for function in (graph._data, graph._records, graph.graph, graph.mode_variables):
            function.cache_clear()

    @classmethod
    def setUpClass(cls):
        cls.old_path = graph.GRAPHS_PATH
        graph.GRAPHS_PATH = Path(os.environ["ORPHEUS_TORBJORN_GRAPH_BUNDLE"])
        cls.clear_graph_cache()
        if not (graph._data().get("deployables") or {}).get("molten_core"):
            raise ValueError("selected bundle lacks the actual030C.053/16DE dependency")

    @classmethod
    def tearDownClass(cls):
        graph.GRAPHS_PATH = cls.old_path
        cls.clear_graph_cache()

    def arena(self, hero=SOLDIER, *, overload=False):
        run = Run(KANEZAKA_DM, names=("Torbjorn", "Enemy"))
        owner, victim = run.players
        self.assertTrue(run.match.switch_hero(owner, heroes()[molten_core.HERO]))
        self.assertTrue(run.match.switch_hero(victim, heroes()[hero]))
        run.match.bots.clear()
        run.match._pending_bot_scripts.clear()
        x, y, z = owner.mover.position
        victim.mover.position = [x, y, z + 5.0]
        for viewer in run.match.players:
            viewer.client.queue_bot_death = lambda *args: None
            viewer.client.queue_bot_death_update = lambda *args: None
        self.advance(run, 35)
        if overload:
            run.step({victim: (0, 0, 16)})
        fight = run.match.combat
        target = next(t for t in fight._targets() if t.owner is victim)
        pool = molten_core.create_pool(fight, owner, tuple(victim.mover.position))
        return run, owner, victim, pool, fight.vitals_of(target)

    @staticmethod
    def advance(run, ticks):
        for _ in range(ticks):
            run.step({})

    def test_native_preparation_delays_periodic_damage_and_mounts_status(self):
        run, owner, victim, pool, vitals = self.arena()
        self.advance(run, 31)
        self.assertEqual(vitals.total, 200.0)
        self.assertEqual(run.match.combat.molten_burns, {})
        self.advance(run, 1)
        self.assertAlmostEqual(vitals.total, 198.08)
        burn = run.match.combat.molten_burns[owner.entity, victim.body]
        self.assertEqual(burn.script.component.instances[burn.instance].graph.index, 0x16DE)
        self.assertIn(victim.body, pool.targets)
        self.advance(run, 62)
        self.assertAlmostEqual(vitals.total, 39.36, places=5)
        self.assertAlmostEqual(owner.stats.value(stats.HERO_DAMAGE), 160.64, places=5)
        instance = burn.script.component.instances[burn.instance]
        self.assertTrue(instance.states[0].active)
        self.assertTrue(instance.states[1].active)
        self.assertTrue(burn.scope_entered)
        self.assertTrue(instance.states[2].active)

    def test_empty_ultimate_charge_rejects_actual_press(self):
        run, owner, _, pool, _ = self.arena()
        molten_core.end_pool(run.match.combat, pool)
        owner.body_script.set_ult_charge(0.0)
        run.step({owner: (0, 4000, 32)})
        self.advance(run, 45)
        run.step({owner: (0, 4000, 1)})
        self.advance(run, 120)
        self.assertEqual(run.match.combat.molten_pools, {})
        self.assertFalse(any(r.spawn == molten_core.POOL for r in run.match.combat.rockets))

    def test_pool_arrival_waits_for_create_ack_and_cannot_revive_retired_area(self):
        run, owner, _, pool, _ = self.arena()
        arrival = molten_core.Arrival(pool, owner, molten_core.Stream(pool.entity))
        with mock.patch.object(owner.client, "queue_entities") as send:
            arrival.send()
            send.assert_not_called()
            arrival.arrived(0)
            self.assertTrue(arrival.done)
            send.assert_called_once()
            molten_core.end_pool(run.match.combat, pool)
            send.reset_mock()
            # Late/repeated ACKs cannot recreate a destroyed script stream.
            arrival.arrived(0)
            arrival.send()
            send.assert_not_called()
        self.assertTrue(pool.ended)
        self.assertNotIn(pool.entity, run.match.combat.molten_pools)

    def test_pool_and_active_status_are_in_passive_snapshots(self):
        from test_server_snapshot import ServerSnapshotTests

        from ow174.game.server_snapshot import snapshot_digest

        case = ServerSnapshotTests()
        session, _, players = case.scene(two=True)
        owner, victim = players
        session.apply("switch_hero", {"account": owner.account_lo, "hero": molten_core.HERO})
        fight = owner.match.combat
        pool = molten_core.create_pool(fight, owner, tuple(victim.mover.position))
        target = next(t for t in fight._targets() if t.owner is victim)
        burn = molten_core._ensure_burn(fight, owner, target, pool.config)
        victim.body_script.component.run_now()
        self.assertTrue(molten_core._activate_burn_scope(burn))
        first = snapshot_digest(case.capture(session))
        pool.last_update += 0.01
        second = snapshot_digest(case.capture(session))
        self.assertNotEqual(first, second)
        burn.duration += 0.01
        self.assertNotEqual(second, snapshot_digest(case.capture(session)))

    def test_armour_bonus_stops_at_exact_pool_boundary(self):
        run, _, _, _, vitals = self.arena(molten_core.HERO)
        self.advance(run, 94)
        self.assertEqual(vitals.now[world.ARMOUR_POOL], 0.0)
        self.assertAlmostEqual(vitals.total, 71.36, places=5)

    def test_overload_temporary_armour_gets_bonus_without_per_tick_reduction(self):
        run, _, _, _, vitals = self.arena(molten_core.HERO, overload=True)
        self.assertEqual(vitals.total, 350.0)
        self.advance(run, 94)
        self.assertEqual(vitals.over_armour.amount, 0.0)
        self.assertEqual(vitals.now[world.ARMOUR_POOL], 0.0)
        self.assertAlmostEqual(vitals.total, 135.36, places=5)

    def test_same_owner_overlapping_pools_do_not_stack_damage_or_status_instances(self):
        run, owner, victim, first, vitals = self.arena()
        second = molten_core.create_pool(run.match.combat, owner, first.position)
        self.assertNotEqual(first.entity, second.entity)
        self.advance(run, 94)
        self.assertAlmostEqual(vitals.total, 39.36, places=5)
        self.assertEqual(len(run.match.combat.molten_burns), 1)
        instances = victim.body_script.component.instances.values()
        self.assertEqual(sum(i.graph.index == 0x16DE for i in instances), 1)

    def test_explicit_area_removal_removes_status_and_stops_damage(self):
        run, owner, victim, pool, vitals = self.arena()
        self.advance(run, 40)
        before = vitals.total
        # Retire the pool as an explicit removal, then verify queued status cleanup.
        molten_core.end_pool(run.match.combat, pool)
        self.advance(run, 20)
        self.assertEqual(vitals.total, before)
        self.assertNotIn((owner.entity, victim.body), run.match.combat.molten_burns)
        instances = victim.body_script.component.instances.values()
        self.assertFalse(any(i.graph.index == 0x16DE for i in instances))

    def test_actual_movement_out_of_area_stops_damage_and_retires_status(self):
        run, owner, victim, pool, vitals = self.arena()
        self.advance(run, 40)
        for _ in range(60):
            run.frame += 1
            run.match.update(run.frame * 0.016, run.frame)
            for player in run.players:
                player.client.queue_entities(run.match.remote_movements(player))
                player.take_commands([Command(run.frame, forward=127 if player is victim else 0)])
        self.assertGreater(abs(victim.mover.position[2] - pool.position[2]), 3.0)
        self.assertFalse(vitals.dead)
        before = vitals.total
        self.advance(run, 20)
        self.assertEqual(vitals.total, before)
        self.assertNotIn((owner.entity, victim.body), run.match.combat.molten_burns)

    def test_range_height_team_and_real_wall_filter_contacts(self):
        for case in ("range", "height", "ally", "wall"):
            with self.subTest(case=case):
                run, owner, victim, pool, vitals = self.arena()
                x, y, z = pool.position
                if case == "range":
                    pool.position = (x + 5.0, y, z)
                elif case == "height":
                    pool.position = (x, y - 2.0, z)
                elif case == "ally":
                    run.match.game_map = replace(run.match.game_map, free_for_all=False)
                    victim.team = owner.team
                else:
                    pool.position = (x, y, z - 1.5)
                    wall = z - 0.75
                    run.match.collision_world = World([
                        (x-3, y, wall, x+3, y, wall, x+3, y+4, wall),
                        (x-3, y, wall, x+3, y+4, wall, x-3, y+4, wall),
                    ], name="molten-wall")
                self.advance(run, 94)
                self.assertEqual(vitals.total, 200.0)
                self.assertEqual(run.match.combat.molten_burns, {})

    def test_owner_is_not_damaged_by_own_pool(self):
        run, owner, _, pool, _ = self.arena()
        pool.position = tuple(owner.mover.position)
        self.advance(run, 94)
        self.assertEqual(run.match.combat.vitals[owner.body].total, 250.0)

    @unittest.skipUnless(os.environ.get("ORPHEUS_NPC_CATALOG_DIR"), "needs five-NPC catalog")
    def test_actual_five_npc_bodies_take_periodic_damage_and_receive_status(self):
        catalog = npc.load_catalog(Path(os.environ["ORPHEUS_NPC_CATALOG_DIR"]) / "pve_npcs_174.json")
        for definition in catalog.values():
            with self.subTest(npc=definition.name):
                run, owner, _, pool, _ = self.arena()
                x, y, z = owner.mover.position
                pool.position = (x + 10.0, y, z)
                bot = npc.spawn(run.match, definition, pool.position, team=1)
                hp = run.match.combat.vitals[bot.entity]
                before = hp.total
                self.advance(run, 94)
                self.assertAlmostEqual(before - hp.total, 160.64, places=5)
                self.assertIn((owner.entity, bot.entity), run.match.combat.molten_burns)

    def test_actual_ultimate_input_consumes_charge_and_creates_ground_pools(self):
        run, owner, _, old_pool, _ = self.arena()
        molten_core.end_pool(run.match.combat, old_pool)
        owner.body_script.set_ult_charge(10000.0)
        run.step({owner: (0, 4000, 32)})
        self.advance(run, 45)
        run.step({owner: (0, 4000, 1)})
        self.advance(run, 120)
        self.assertGreater(len(run.match.combat.molten_pools), 0)
        self.assertLess(owner.body_script.component.vars[342].value(), 10000.0)
        self.assertTrue(all(p.owner is owner for p in run.match.combat.molten_pools.values()))

    def test_held_ultimate_fire_exhausts_ten_globules_then_ends_without_extra_launches(self):
        run, owner, _, pool, _ = self.arena()
        fight = run.match.combat
        molten_core.end_pool(fight, pool)
        owner.body_script.set_ult_charge(10000.0)
        run.step({owner: (0, 4000, 32)})
        self.advance(run, 45)
        with mock.patch.object(fight, "_shoot", wraps=fight._shoot) as shoot:
            for _ in range(450):
                run.step({owner: (0, 4000, 1)})
            launches = [call for call in shoot.call_args_list
                        if call.args[2].instance.graph.index == 0x16DA and call.args[2].index == 17]
            self.assertEqual(len(launches), 10)
            self.assertEqual(len(fight.molten_pools), 10)
            self.assertTrue(all(not call.args[2].active for call in launches))
            for _ in range(100):
                run.step({owner: (0, 4000, 1 | 32)})
            self.assertEqual(sum(call.args[2].instance.graph.index == 0x16DA
                                 and call.args[2].index == 17 for call in shoot.call_args_list), 10)

    @unittest.skipUnless(os.environ.get("ORPHEUS_NPC_CATALOG_DIR"), "needs actual Heavy Assault")
    def test_authored_lifetime_stops_damage_and_removes_entity_and_status(self):
        catalog = npc.load_catalog(Path(os.environ["ORPHEUS_NPC_CATALOG_DIR"]) / "pve_npcs_174.json")
        definition = next(row for row in catalog.values() if row.name == "Heavy Assault")
        run, owner, _, pool, _ = self.arena()
        x, y, z = owner.mover.position
        pool.position = (x + 10, y, z)
        bot = npc.spawn(run.match, definition, pool.position, team=1)
        hp = run.match.combat.vitals[bot.entity]
        self.advance(run, 660)
        # The current native-backed ChaseVar model reaches zero one grid tick
        # before the nominal cap, after9.996 active seconds on this16ms clock.
        self.assertAlmostEqual(hp.total, 2400.64, places=5)
        self.assertAlmostEqual(pool.last_update - pool.born - 0.5, 9.996, places=5)
        self.assertTrue(pool.ended)
        self.assertNotIn(pool.entity, run.match.combat.molten_pools)
        self.assertNotIn((owner.entity, bot.entity), run.match.combat.molten_burns)
        before = hp.total
        self.advance(run, 20)
        self.assertEqual(hp.total, before)

    def test_victim_death_retires_burn_and_records_caster(self):
        run, owner, victim, _, vitals = self.arena()
        self.advance(run, 120)
        self.assertTrue(vitals.dead)
        self.assertEqual(vitals.killer, owner.body)
        self.assertNotIn((owner.entity, victim.body), run.match.combat.molten_burns)

    def test_caster_death_does_not_cancel_existing_area(self):
        run, owner, victim, pool, vitals = self.arena()
        fight = run.match.combat
        own_target = next(t for t in fight._targets() if t.owner is owner)
        fight._damage(victim, own_target, 1000.0, False, None, run.frame)
        self.advance(run, 94)
        self.assertTrue(fight.is_dead(owner))
        self.assertIn(pool.entity, fight.molten_pools)
        self.assertAlmostEqual(vitals.total, 39.36, places=5)
