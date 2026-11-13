"""Actual Molten Core area damage/lifecycle under the documented contact host."""

import os
import unittest
from contextlib import ExitStack
from dataclasses import replace
from math import hypot, radians, tan
from pathlib import Path
from unittest import mock

from test_game_combat import KANEZAKA_DM, Run, aim, in_world
from test_game_mover import box, ramp, wall

from ow174.game import combat, molten_core, npc, npc_control, projectiles, stats, world
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

    def test_pool_effect_snapshot_preserves_the_launch_source(self):
        from ow174.game import observers
        from ow174.game.script import expr

        run, owner, _, pool, _ = self.arena()
        instance = pool.script.component.instances[pool.instance]
        self.assertEqual(instance.source_entity(), owner.body)
        snapshot = observers.snapshot(pool.script)
        self.assertEqual(snapshot.vars[pool.instance, 27][0], expr.Entity(owner.body))
        self.assertEqual(snapshot.sources[pool.instance], owner.body)
        self.assertGreater(observers.full_frame(pool.script, snapshot).count, 0)
        source_body = owner.body
        self.assertTrue(run.match.switch_hero(owner, heroes()[SOLDIER]))
        self.assertTrue(run.match.switch_hero(owner, heroes()[molten_core.HERO]))
        self.assertNotEqual(owner.body, source_body)
        replacement = molten_core.create_pool(run.match.combat, owner, pool.position,
                                              source_body=source_body)
        replacement_instance = replacement.script.component.instances[replacement.instance]
        self.assertEqual(replacement_instance.source_entity(), source_body)
        self.assertEqual(observers.snapshot(replacement.script).sources[replacement.instance], source_body)

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
        captured_bits = pool.filter_bits
        pool.filter_bits ^= 1
        self.assertNotEqual(first, snapshot_digest(case.capture(session)))
        pool.filter_bits = captured_bits
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

    def launched_pool_against_torbjorn(self):
        run, owner, victim, fixture_pool, hp = self.arena(molten_core.HERO)
        fight = run.match.combat
        molten_core.end_pool(fight, fixture_pool)
        x, y, z = owner.mover.position
        victim.mover.position = [x + 30, y, z]
        victim.mover.body = None
        owner.body_script.set_ult_charge(owner.body_script.component.entity_var(338).value())
        run.step({owner: (0, 4000, 32)})
        self.advance(run, 45)
        run.step({owner: (0, 4000, 1)})
        for _ in range(60):
            if fight.molten_pools:
                break
            run.step({})
        pool, = fight.molten_pools.values()
        self.assertIs(pool.owner, owner)
        self.assertNotEqual(pool.entity, fixture_pool.entity)
        self.assertNotEqual(pool.phase, 1)
        victim.mover.position = list(pool.position)
        victim.mover.body = None
        return run, owner, victim, pool, hp

    def test_launched_pool_splits_damage_at_each_unseeded_overload_armor_boundary(self):
        run, owner, victim, _, hp = self.launched_pool_against_torbjorn()
        fight = run.match.combat
        run.step({victim: (0, 0, 16)})
        self.assertEqual(hp.total, 350)
        source = hp.over_armour.source
        credit = owner.stats.value(stats.HERO_DAMAGE)
        key = owner.entity, victim.body
        for _ in range(40):
            if key in fight.molten_burns:
                break
            run.step({})
        burn = fight.molten_burns[key]
        self.assertAlmostEqual(hp.over_armour.amount, 97)
        self.assertEqual(hp.now[world.ARMOUR_POOL], 50)
        self.assertEqual(hp.now[world.HEALTH_POOL], 200)
        self.advance(run, 24)
        self.assertAlmostEqual(hp.over_armour.amount, 1)
        self.assertEqual(hp.now[world.ARMOUR_POOL], 50)
        run.step({})
        self.assertEqual(hp.over_armour.amount, 0)
        self.assertAlmostEqual(hp.now[world.ARMOUR_POOL], 47)
        self.assertEqual(hp.now[world.HEALTH_POOL], 200)
        self.assertAlmostEqual(owner.stats.value(stats.HERO_DAMAGE) - credit, 103)

        self.advance(run, 11)
        self.assertAlmostEqual(hp.now[world.ARMOUR_POOL], 3)
        self.assertEqual(hp.now[world.HEALTH_POOL], 200)
        run.step({})
        self.assertEqual(hp.now[world.ARMOUR_POOL], 0)
        self.assertAlmostEqual(hp.now[world.HEALTH_POOL], 199.36)
        self.assertAlmostEqual(owner.stats.value(stats.HERO_DAMAGE) - credit, 150.64)
        run.step({})
        self.assertAlmostEqual(hp.now[world.HEALTH_POOL], 196.8)
        self.assertAlmostEqual(owner.stats.value(stats.HERO_DAMAGE) - credit, 153.2)
        self.assertEqual(hp.over_armour.source, source)
        self.assertIs(fight.molten_burns[key], burn)

    def test_overload_during_live_health_burn_redirects_the_next_frame_to_new_armor(self):
        run, owner, victim, pool, hp = self.launched_pool_against_torbjorn()
        fight = run.match.combat
        key = owner.entity, victim.body
        for _ in range(60):
            if hp.now[world.ARMOUR_POOL] == 0:
                break
            run.step({})
        self.assertEqual(hp.now[world.ARMOUR_POOL], 0)
        self.assertIsNone(hp.over_armour.source)
        burn = fight.molten_burns[key]
        before = hp.now[world.HEALTH_POOL]
        credit = owner.stats.value(stats.HERO_DAMAGE)
        run.step({victim: (0, 0, 16)})
        self.assertAlmostEqual(hp.now[world.HEALTH_POOL], before - 2.56)
        self.assertEqual(hp.over_armour.amount, 100)
        self.assertAlmostEqual(owner.stats.value(stats.HERO_DAMAGE) - credit, 2.56)
        source = hp.over_armour.source
        health = hp.now[world.HEALTH_POOL]

        run.step({})
        self.assertAlmostEqual(hp.over_armour.amount, 96)
        self.assertEqual(hp.now[world.HEALTH_POOL], health)
        for _ in range(12):
            run.step({victim: (0, 0, 16)})
            run.step({})
        self.assertAlmostEqual(hp.over_armour.amount, 0)
        self.assertEqual(hp.now[world.ARMOUR_POOL], 0)
        self.assertEqual(hp.now[world.HEALTH_POOL], health)
        self.assertAlmostEqual(owner.stats.value(stats.HERO_DAMAGE) - credit, 102.56)
        run.step({victim: (0, 0, 16)})
        self.assertAlmostEqual(hp.now[world.HEALTH_POOL], health - 2.56)
        self.assertAlmostEqual(owner.stats.value(stats.HERO_DAMAGE) - credit, 105.12)
        self.assertEqual(hp.over_armour.amount, 0)
        self.assertEqual(hp.over_armour.source, source)
        self.assertIs(fight.molten_burns[key], burn)
        self.assertEqual(list(fight.molten_pools.values()), [pool])
        for _ in range(90):
            if hp.dead:
                break
            run.step({})
        self.assertTrue(hp.dead)
        self.assertEqual(hp.killer, owner.body)
        self.assertAlmostEqual(owner.stats.value(stats.HERO_DAMAGE) - credit, before + 100)
        self.assertEqual(owner.stats.value(stats.ELIMINATIONS), 1)
        self.assertIsNone(hp.over_armour.source)
        self.assertNotIn(key, fight.molten_burns)
        self.assertNotIn(burn.instance, burn.script.component.instances)
        self.assertIs(fight.molten_pools[pool.entity], pool)

    def test_same_owner_overlapping_pools_do_not_stack_damage_or_status_instances(self):
        run, owner, victim, first, vitals = self.arena()
        second = molten_core.create_pool(run.match.combat, owner, first.position)
        self.assertNotEqual(first.entity, second.entity)
        self.advance(run, 94)
        self.assertAlmostEqual(vitals.total, 39.36, places=5)
        self.assertEqual(len(run.match.combat.molten_burns), 1)
        instances = victim.body_script.component.instances.values()
        self.assertEqual(sum(i.graph.index == 0x16DE for i in instances), 1)

    def test_two_launched_pools_keep_one_burn_through_the_first_natural_expiry(self):
        run, owner, victim, fixture_pool, hp = self.arena()
        fight = run.match.combat
        molten_core.end_pool(fight, fixture_pool)
        x, y, z = owner.mover.position
        victim.mover.position = [x + 30, y, z]
        victim.mover.body = None
        owner.body_script.set_ult_charge(owner.body_script.component.entity_var(338).value())
        run.step({owner: (0, 4000, 32)})
        self.advance(run, 45)
        run.step({owner: (0, 4000, 1)})
        self.advance(run, 32)
        run.step({owner: (0, 4000, 1)})
        for _ in range(100):
            if len(fight.molten_pools) == 2 and all(pool.phase == 1 for pool in fight.molten_pools.values()):
                break
            run.step({})
        first, second = sorted(fight.molten_pools.values(), key=lambda pool: pool.born)
        self.assertIs(first.owner, owner)
        self.assertIs(second.owner, owner)
        self.assertGreater(second.born, first.born)
        self.assertEqual(first.position, second.position)
        expires = first.born + first.config["arming_seconds"] + first.config["lifetime_seconds"]
        for _ in range(680):
            if fight.now >= expires - 8 * combat.TICK:
                break
            run.step({})
        self.assertFalse(first.ended or second.ended)
        self.assertEqual(hp.total, 200)
        self.assertFalse(fight.molten_burns)
        victim.mover.position = list(first.position)
        victim.mover.body = None
        key = owner.entity, victim.body
        run.step({})
        burn = fight.molten_burns[key]
        self.assertAlmostEqual(hp.total, 197.44)
        self.assertIn(victim.body, first.targets)
        self.assertIn(victim.body, second.targets)

        for _ in range(12):
            if first.ended:
                break
            before = hp.total
            run.step({})
            self.assertAlmostEqual(before - hp.total, 2.56)
            self.assertEqual(fight.molten_burns, {key: burn})
        self.assertTrue(first.ended)
        self.assertEqual(list(fight.molten_pools.values()), [second])
        before = hp.total
        run.step({})
        self.assertAlmostEqual(before - hp.total, 2.56)
        self.assertIs(fight.molten_burns[key], burn)

        for _ in range(45):
            if second.ended:
                break
            before = hp.total
            run.step({})
            if second.ended:
                self.assertGreater(before - hp.total, 0)
                self.assertLessEqual(before - hp.total, 2.56 + 1e-6)
            else:
                self.assertAlmostEqual(before - hp.total, 2.56)
                self.assertIs(fight.molten_burns[key], burn)
        self.assertTrue(second.ended)
        self.assertFalse(fight.molten_pools)
        self.assertFalse(fight.molten_burns)
        self.assertNotIn(burn.instance, burn.script.component.instances)
        before = hp.total
        self.assertGreater(before, 0)
        self.assertAlmostEqual(owner.stats.value(stats.HERO_DAMAGE), 200 - before)
        self.advance(run, 8)
        self.assertEqual(hp.total, before)

    def overlapping_casts(self, *, launch_gap=0, victim_hero=SOLDIER):
        run = Run(KANEZAKA_DM, names=("First", "Second", "Victim"))
        first, second, victim = run.players
        match = run.match
        match.bots.clear()
        match._pending_bot_scripts.clear()
        match.game_map = replace(match.game_map, free_for_all=False)
        first.team = second.team = 0
        victim.team = 1
        if victim.hero.guid != victim_hero:
            self.assertTrue(match.switch_hero(victim, heroes()[victim_hero]))
        x, y, z = first.mover.position
        for owner, offset in ((first, -.25), (second, .25)):
            self.assertTrue(match.switch_hero(owner, heroes()[molten_core.HERO]))
            owner.mover.position = [x + offset, y, z]
            owner.mover.body = None
        victim.mover.position = [x + 30, y, z]
        victim.mover.body = None
        self.advance(run, 35)
        for owner in (first, second):
            owner.body_script.set_ult_charge(10000)
        run.step(dict.fromkeys((first, second), (0, 4000, 32)))
        self.advance(run, 45)
        if launch_gap:
            run.step({first: (0, 4000, 1)})
            self.advance(run, launch_gap - 1)
            run.step({second: (0, 4000, 1)})
        else:
            run.step(dict.fromkeys((first, second), (0, 4000, 1)))
        fight = match.combat
        for _ in range(155):
            if len(fight.molten_pools) == 2 and all(pool.phase == 1 for pool in fight.molten_pools.values()):
                break
            run.step({})
        pools = {pool.owner: pool for pool in fight.molten_pools.values()}
        self.assertEqual(set(pools), {first, second})
        self.assertTrue(all(pool.phase == 1 for pool in pools.values()))
        victim.mover.position = [
            (a + b) / 2 for a, b in zip(pools[first].position, pools[second].position, strict=True)
        ]
        victim.mover.body = None
        return run, first, second, victim, pools

    def test_two_actual_casters_settle_separate_burns_and_retire_independently(self):
        run, first, second, victim, pools = self.overlapping_casts()
        match = run.match
        fight = match.combat
        hp = fight.vitals[victim.body]
        self.advance(run, 8)
        self.assertAlmostEqual(hp.total, 159.04)
        keys = [(owner.entity, victim.body) for owner in (first, second)]
        burns = [fight.molten_burns[key] for key in keys]
        self.assertNotEqual(burns[0].instance, burns[1].instance)
        for owner, burn in zip((first, second), burns, strict=True):
            self.assertAlmostEqual(owner.stats.value(stats.HERO_DAMAGE), 20.48)
            self.assertEqual(burn.script.component.instances[burn.instance].graph.index, 0x16DE)
        self.assertTrue(match.switch_hero(first, heroes()[SOLDIER]))
        self.advance(run, 8)
        self.assertTrue(pools[first].ended)
        self.assertFalse(pools[second].ended)
        self.assertNotIn(keys[0], fight.molten_burns)
        self.assertNotIn(burns[0].instance, burns[0].script.component.instances)
        self.assertIs(fight.molten_burns[keys[1]], burns[1])
        self.assertAlmostEqual(hp.total, 138.56)
        self.assertAlmostEqual(first.stats.value(stats.HERO_DAMAGE), 20.48)
        self.assertAlmostEqual(second.stats.value(stats.HERO_DAMAGE), 40.96)

    @unittest.skipUnless(os.environ.get("ORPHEUS_NPC_CATALOG_DIR"), "needs five-NPC catalog")
    def test_opposing_molten_pools_handoff_npc_burn_when_allegiance_changes(self):
        catalog = npc.load_catalog(Path(os.environ["ORPHEUS_NPC_CATALOG_DIR"]) / "pve_npcs_174.json")
        self.assertEqual(len(catalog), 5)
        for definition in catalog.values():
            with self.subTest(npc=definition.name):
                run, first, second, victim, pools = self.overlapping_casts()
                match = run.match
                fight = match.combat
                second.team = 1 - first.team
                position = tuple(victim.mover.position)
                x, y, z = position
                for player, offset in ((first, -30), (second, 30), (victim, 60)):
                    player.mover.position = [x + offset, y, z]
                    player.mover.body = None
                bot = npc.spawn(match, definition, position, team=second.team)
                hp = fight.vitals[bot.entity]
                before = hp.total
                previous = None
                for step, owner, other, first_ticks, second_ticks in (
                    (1, first, second, 1, 0),
                    (2, second, first, 1, 1),
                    (3, first, second, 2, 1),
                ):
                    bot.team = other.team
                    run.step({})
                    key = owner.entity, bot.entity
                    burn = fight.molten_burns[key]
                    self.assertEqual(fight.molten_burns, {key: burn})
                    self.assertIn(bot.entity, pools[owner].targets)
                    self.assertNotIn(bot.entity, pools[other].targets)
                    self.assertEqual(burn.script.component.instances[burn.instance].graph.index, 0x16DE)
                    if previous is not None:
                        self.assertNotEqual(burn.instance, previous.instance)
                        self.assertNotIn(previous.instance, previous.script.component.instances)
                    self.assertAlmostEqual(before - hp.total, 2.56 * step)
                    self.assertAlmostEqual(first.stats.value(stats.HERO_DAMAGE), 2.56 * first_ticks)
                    self.assertAlmostEqual(second.stats.value(stats.HERO_DAMAGE), 2.56 * second_ticks)
                    previous = burn
                for pool in pools.values():
                    self.assertIs(fight.molten_pools[pool.entity], pool)
                    self.assertFalse(pool.ended)

    def test_two_actual_casters_settle_staggered_natural_expiries(self):
        run, first, second, victim, pools = self.overlapping_casts(launch_gap=24)
        fight = run.match.combat
        hp = fight.vitals[victim.body]
        owners = first, second
        expiry = {owner: pool.born + pool.config["arming_seconds"] + pool.config["lifetime_seconds"]
                  for owner, pool in pools.items()}
        self.assertAlmostEqual(expiry[second] - expiry[first], 24 * combat.TICK)
        overlap = tuple(victim.mover.position)
        victim.mover.position = [overlap[0] + 30, overlap[1], overlap[2]]
        victim.mover.body = None
        for _ in range(680):
            if fight.now >= expiry[first] - 8 * combat.TICK:
                break
            run.step({})
        self.assertFalse(any(pool.ended for pool in pools.values()))
        self.assertEqual(hp.total, 200)
        self.assertFalse(fight.molten_burns)
        victim.mover.position = list(overlap)
        victim.mover.body = None
        run.step({})
        keys = {owner: (owner.entity, victim.body) for owner in owners}
        burns = {owner: fight.molten_burns[keys[owner]] for owner in owners}
        self.assertNotEqual(burns[first].instance, burns[second].instance)
        self.assertAlmostEqual(hp.total, 194.88)
        for owner in owners:
            self.assertIn(victim.body, pools[owner].targets)
            self.assertAlmostEqual(owner.stats.value(stats.HERO_DAMAGE), 2.56)

        for _ in range(40):
            if pools[second].ended:
                break
            start, before = fight.now, hp.total
            credit = {owner: owner.stats.value(stats.HERO_DAMAGE) for owner in owners}
            run.step({})
            dealt = 0
            for owner in owners:
                interval = max(0, min(fight.now, expiry[owner]) - start)
                expected = 160 * interval
                self.assertAlmostEqual(owner.stats.value(stats.HERO_DAMAGE) - credit[owner], expected,
                                       msg=f"owner {owner.entity} at {fight.now:.6f}")
                dealt += expected
                burn = burns[owner]
                if pools[owner].ended:
                    self.assertNotIn(keys[owner], fight.molten_burns)
                    self.assertNotIn(burn.instance, burn.script.component.instances)
                else:
                    self.assertIs(fight.molten_burns[keys[owner]], burn)
            self.assertAlmostEqual(before - hp.total, dealt)
            if pools[first].ended and not pools[second].ended:
                self.assertEqual(list(fight.molten_pools.values()), [pools[second]])
        self.assertTrue(all(pool.ended for pool in pools.values()))
        self.assertFalse(fight.molten_pools)
        self.assertFalse(fight.molten_burns)
        self.assertGreater(hp.total, 0)
        before = hp.total
        self.advance(run, 8)
        self.assertEqual(hp.total, before)
        self.assertAlmostEqual(sum(owner.stats.value(stats.HERO_DAMAGE) for owner in owners), 200 - before)

    def test_expiring_pool_splits_overload_armor_and_health_with_surviving_pool(self):
        cases = ((.25, .25, .82, 2.56), (.75, 1.25, 1.0, 2.92))
        for temporary, base, first_damage, second_damage in cases:
            with self.subTest(temporary=temporary, base=base):
                run, first, second, victim, pools = self.overlapping_casts(
                    launch_gap=24, victim_hero=molten_core.HERO,
                )
                fight = run.match.combat
                hp = fight.vitals[victim.body]
                overlap = tuple(victim.mover.position)
                victim.mover.position = [overlap[0] + 30, overlap[1], overlap[2]]
                victim.mover.body = None
                pool = pools[first]
                expiry = pool.born + pool.config["arming_seconds"] + pool.config["lifetime_seconds"]
                for _ in range(680):
                    if fight.now >= expiry - 3 * combat.TICK:
                        break
                    run.step({})
                self.assertEqual(hp.total, 250)
                run.step({victim: (0, 0, 16)})
                self.assertEqual(hp.total, 350)
                source = hp.over_armour.source
                self.assertIsNotNone(source)
                victim.mover.position = list(overlap)
                victim.mover.body = None
                run.step({})
                self.assertAlmostEqual(expiry - fight.now, .004)
                self.assertAlmostEqual(hp.over_armour.amount, 92)
                self.assertEqual(hp.now[world.ARMOUR_POOL], 50)
                self.assertEqual(hp.now[world.HEALTH_POOL], 200)
                first_key = first.entity, victim.body
                second_key = second.entity, victim.body
                first_burn = fight.molten_burns[first_key]
                second_burn = fight.molten_burns[second_key]
                hp.damage(hp.over_armour.amount - temporary, armour_reduction=False)
                self.assertEqual(hp.over_armour.amount, temporary)
                hp.now[world.ARMOUR_POOL] = base
                first_before = first.stats.value(stats.HERO_DAMAGE)
                second_before = second.stats.value(stats.HERO_DAMAGE)

                run.step({})
                self.assertTrue(pool.ended)
                self.assertEqual(list(fight.molten_pools.values()), [pools[second]])
                self.assertEqual(hp.over_armour.amount, 0)
                self.assertIs(hp.over_armour.source, source)
                self.assertEqual(hp.now[world.ARMOUR_POOL], 0)
                health = 200 + temporary + base - first_damage - second_damage
                self.assertAlmostEqual(hp.now[world.HEALTH_POOL], health)
                self.assertAlmostEqual(first.stats.value(stats.HERO_DAMAGE) - first_before, first_damage)
                self.assertAlmostEqual(second.stats.value(stats.HERO_DAMAGE) - second_before, second_damage)
                self.assertNotIn(first_key, fight.molten_burns)
                self.assertNotIn(first_burn.instance, first_burn.script.component.instances)
                self.assertIs(fight.molten_burns[second_key], second_burn)

                run.step({})
                self.assertAlmostEqual(hp.now[world.HEALTH_POOL], health - 2.56)
                self.assertAlmostEqual(first.stats.value(stats.HERO_DAMAGE) - first_before, first_damage)
                self.assertAlmostEqual(
                    second.stats.value(stats.HERO_DAMAGE) - second_before, second_damage + 2.56,
                )

    def test_two_actual_casters_credit_lethal_overlap_without_restarting_dead_burns(self):
        for remaining in (1, 4):
            with self.subTest(remaining=remaining):
                run, first, second, victim, _ = self.overlapping_casts()
                fight = run.match.combat
                if remaining == 4:
                    self.advance(run, 8)
                    self.assertEqual(len(fight.molten_burns), 2)
                hp = fight.vitals[victim.body]
                hp.damage(hp.total - remaining)
                owners = first, second
                before = [owner.stats.value(stats.HERO_DAMAGE) for owner in owners]
                winner = first if remaining == 1 else second
                with mock.patch.object(molten_core, "_ensure_burn", wraps=molten_core._ensure_burn) as burns:
                    run.step({})
                self.assertEqual(hp.total, 0)
                self.assertEqual(hp.killer, winner.body)
                self.assertEqual(hp.death_tick, run.frame)
                self.assertEqual(victim.stats.value(stats.DEATHS), 1)
                self.assertEqual([call.args[1] for call in burns.call_args_list], list(
                    owners[:1] if remaining == 1 else owners
                ))
                first_dealt = min(remaining, 2.56)
                expected_damage = (first_dealt, remaining - first_dealt)
                for owner, prior, dealt in zip(owners, before, expected_damage, strict=True):
                    self.assertAlmostEqual(owner.stats.value(stats.HERO_DAMAGE) - prior, dealt)
                self.assertFalse(fight.molten_burns)
                self.assertFalse(any(instance.graph.index == 0x16DE
                                     for instance in victim.body_script.component.instances.values()))
                self.advance(run, 2)
                self.assertEqual(victim.stats.value(stats.DEATHS), 1)
                self.assertFalse(fight.molten_burns)

    def test_projected_barrier_break_arbitrates_two_live_molten_burns(self):
        for remaining, same_frame_damage in ((1, 2.56), (4, 0)):
            with self.subTest(capacity=remaining):
                self.check_two_burns_at_projected_barrier_break(remaining, same_frame_damage)

    def check_two_burns_at_projected_barrier_break(self, remaining, same_frame_damage):
        run, first, second, victim, pools = self.overlapping_casts()
        match = run.match
        fight = match.combat
        zarya_hero = next(hero for hero in heroes().values() if hero.name == "Zarya")
        zarya = match.add_player(8, "Zarya", zarya_hero.guid, victim.team, False)
        in_world(match, zarya)
        run.players.append(zarya)
        self.assertTrue(match.switch_hero(zarya, zarya_hero))
        x, y, z = victim.mover.position
        zarya.mover.position = [x - 5, y, z]
        zarya.mover.body = None
        self.advance(run, 8)
        keys = [(owner.entity, victim.body) for owner in (first, second)]
        old_burns = [fight.molten_burns[key] for key in keys]
        hp = fight.vitals[victim.body]
        run.step({zarya: (*aim(fight._eye(zarya), (x, y + .8, z)), 16)})
        bubble = fight.bubbles.for_target(victim)
        self.assertIsNotNone(bubble)
        self.assertIs(bubble.caster, zarya)
        self.assertEqual(bubble.health, 200)
        before = hp.total
        credits = [owner.stats.value(stats.HERO_DAMAGE) for owner in (first, second)]
        run.step({})
        self.assertAlmostEqual(bubble.health, 194.88)
        self.assertEqual(hp.total, before)
        self.assertEqual([owner.stats.value(stats.HERO_DAMAGE) for owner in (first, second)], credits)
        self.assertFalse(fight.molten_burns)
        for burn in old_burns:
            self.assertNotIn(burn.instance, burn.script.component.instances)

        bubble.health = remaining
        run.step({})
        self.assertEqual(bubble.health, 0)
        self.assertIsNone(fight.bubbles.for_target(victim))
        self.assertAlmostEqual(hp.total, before - same_frame_damage)
        self.assertAlmostEqual(first.stats.value(stats.HERO_DAMAGE), credits[0])
        self.assertAlmostEqual(second.stats.value(stats.HERO_DAMAGE), credits[1] + same_frame_damage)
        self.assertNotIn(keys[0], fight.molten_burns)
        self.assertEqual(keys[1] in fight.molten_burns, remaining == 1)

        run.step({})
        self.assertEqual(set(fight.molten_burns), set(keys))
        self.assertAlmostEqual(hp.total, before - same_frame_damage - 5.12)
        survivor = fight.molten_burns[keys[0]]
        retired = fight.molten_burns[keys[1]]
        self.assertTrue(match.switch_hero(second, heroes()[SOLDIER]))
        run.step({})
        self.assertTrue(pools[second].ended)
        self.assertFalse(pools[first].ended)
        self.assertIs(fight.molten_burns[keys[0]], survivor)
        self.assertNotIn(keys[1], fight.molten_burns)
        self.assertNotIn(retired.instance, retired.script.component.instances)
        self.assertAlmostEqual(hp.total, before - same_frame_damage - 7.68)
        self.assertAlmostEqual(first.stats.value(stats.HERO_DAMAGE), credits[0] + 5.12)
        self.assertAlmostEqual(second.stats.value(stats.HERO_DAMAGE), credits[1] + same_frame_damage + 2.56)

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
    def test_expiring_pool_credits_lethal_overlap_for_all_five_npcs(self):
        catalog = npc.load_catalog(Path(os.environ["ORPHEUS_NPC_CATALOG_DIR"]) / "pve_npcs_174.json")
        self.assertEqual(len(catalog), 5)
        for remaining in (.5, 1.0):
            with self.subTest(remaining=remaining):
                run, first, second, victim, pools = self.overlapping_casts(launch_gap=24)
                fight = run.match.combat
                x, y, z = victim.mover.position
                victim.mover.position = [x + 30, y, z]
                victim.mover.body = None
                for viewer in run.players:
                    viewer.client.queue_bot_death = lambda *args: None
                    viewer.client.queue_bot_death_update = lambda *args: None
                deaths = {}
                first.client.queue_bot_death = deaths.__setitem__
                pool = pools[first]
                expiry = pool.born + pool.config["arming_seconds"] + pool.config["lifetime_seconds"]
                for _ in range(680):
                    if fight.now >= expiry - 2 * combat.TICK:
                        break
                    run.step({})
                bots = [
                    npc.spawn(run.match, definition, (x + (index - 2) * .4, y, z), team=1)
                    for index, definition in enumerate(catalog.values())
                ]
                run.step({})
                self.assertAlmostEqual(expiry - fight.now, .004)
                self.assertFalse(any(pool.ended for pool in pools.values()))
                burns = dict(fight.molten_burns)
                self.assertEqual(set(burns), {(owner.entity, bot.entity) for owner in pools for bot in bots})
                for bot in bots:
                    hp = fight.vitals[bot.entity]
                    hp.damage(hp.total - remaining, armour_reduction=False)
                    self.assertAlmostEqual(hp.total, remaining)
                initial = {owner: owner.stats.value(stats.HERO_DAMAGE) for owner in pools}

                run.step({})
                winner = first if remaining == .5 else second
                self.assertEqual(set(deaths), {bot.entity for bot in bots})
                for bot in bots:
                    hp = fight.vitals[bot.entity]
                    self.assertTrue(bot.dead)
                    self.assertEqual(hp.death_tick, run.frame)
                    self.assertEqual(hp.killer, winner.body)
                    self.assertEqual(bot.body_script.death_frame, run.frame)
                first_credit = min(remaining, .64) * len(bots)
                second_credit = remaining * len(bots) - first_credit
                self.assertAlmostEqual(first.stats.value(stats.HERO_DAMAGE) - initial[first], first_credit)
                self.assertAlmostEqual(second.stats.value(stats.HERO_DAMAGE) - initial[second], second_credit)
                self.assertEqual(winner.stats.value(stats.ELIMINATIONS), 5)
                self.assertEqual((second if winner is first else first).stats.value(stats.ELIMINATIONS), 0)
                self.assertTrue(pool.ended)
                self.assertEqual(list(fight.molten_pools.values()), [pools[second]])
                self.assertFalse(fight.molten_burns)
                for burn in burns.values():
                    self.assertNotIn(burn.instance, burn.script.component.instances)
                self.advance(run, 3)
                self.assertFalse(pools[second].targets)
                self.assertFalse(fight.molten_burns)
                self.assertEqual(winner.stats.value(stats.ELIMINATIONS), 5)

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

    @unittest.skipUnless(os.environ.get("ORPHEUS_NPC_CATALOG_DIR"), "needs five-NPC catalog")
    def test_launched_molten_pool_filters_and_remounts_burn_for_all_five_npcs(self):
        catalog = npc.load_catalog(Path(os.environ["ORPHEUS_NPC_CATALOG_DIR"]) / "pve_npcs_174.json")
        for definition in catalog.values():
            with self.subTest(npc=definition.name):
                run, owner, other, old_pool, _ = self.arena()
                match = run.match
                fight = match.combat
                match.game_map = replace(match.game_map, free_for_all=False)
                x, y, z = owner.mover.position
                other.mover.position = [x + 30, y, z]
                other.mover.body = None
                molten_core.end_pool(fight, old_pool)
                owner.body_script.set_ult_charge(10000)
                run.step({owner: (0, 4000, 32)})
                self.advance(run, 45)
                run.step({owner: (0, 4000, 1)})
                for _ in range(120):
                    if fight.molten_pools:
                        break
                    run.step({})
                pool, = fight.molten_pools.values()
                self.assertIs(pool.owner, owner)
                x, y, z = pool.position
                inside = (x + 1.5, y, z)
                outside = (x + pool.config["radius"] + 2, y, z)
                bot = npc.spawn(match, definition, outside, team=1 - owner.team)
                hp = fight.vitals[bot.entity]
                before = hp.total
                key = owner.entity, bot.entity
                self.advance(run, 38)
                self.assertEqual(pool.phase, 1)
                self.assertEqual(hp.total, before)
                self.assertNotIn(bot.entity, pool.targets)
                bot.position = inside
                bot.team = owner.team
                self.advance(run, 8)
                self.assertEqual(hp.total, before)
                self.assertNotIn(key, fight.molten_burns)
                bot.team = 1 - owner.team
                clear = match.collision_world
                match.collision_world = World(
                    box(x - 25, x + 25, y - 1, y, z - 25, z + 25)
                    + wall(x + .75, z - 3, x + .75, z + 3, y0=y, y1=y + 4)
                )
                self.advance(run, 8)
                self.assertEqual(hp.total, before)
                self.assertNotIn(key, fight.molten_burns)
                match.collision_world = clear
                self.advance(run, 8)
                self.assertAlmostEqual(before - hp.total, 20.48)
                burn = fight.molten_burns[key]
                self.assertIn(bot.entity, pool.targets)
                self.assertEqual(burn.script.component.instances[burn.instance].graph.index, 0x16DE)
                bot.position = outside
                self.advance(run, 8)
                self.assertAlmostEqual(before - hp.total, 20.48)
                self.assertNotIn(key, fight.molten_burns)
                self.assertNotIn(burn.instance, burn.script.component.instances)
                bot.position = inside
                self.advance(run, 8)
                self.assertAlmostEqual(before - hp.total, 40.96)
                remounted = fight.molten_burns[key]
                self.assertNotEqual(remounted.instance, burn.instance)
                credited = owner.stats.value(stats.HERO_DAMAGE)
                bot.team = owner.team
                run.step({})
                self.assertAlmostEqual(before - hp.total, 40.96)
                self.assertEqual(owner.stats.value(stats.HERO_DAMAGE), credited)
                self.assertNotIn(bot.entity, pool.targets)
                self.assertNotIn(key, fight.molten_burns)
                self.assertNotIn(remounted.instance, remounted.script.component.instances)
                bot.team = 1 - owner.team
                run.step({})
                self.assertAlmostEqual(before - hp.total, 43.52)
                self.assertAlmostEqual(owner.stats.value(stats.HERO_DAMAGE) - credited, 2.56)
                self.assertIn(bot.entity, pool.targets)
                self.assertNotEqual(fight.molten_burns[key].instance, remounted.instance)
                if definition.name == "Heavy Assault":
                    hp.now[combat.HEALTH_POOL] = 5  # 4000 HP exceeds one pool's lifetime damage.
                remaining = hp.total
                credited = owner.stats.value(stats.HERO_DAMAGE)
                lethal_burn = fight.molten_burns[key]
                for _ in range(300):
                    run.step({})
                    if bot.dead:
                        break
                self.assertTrue(bot.dead)
                self.assertEqual(hp.killer, owner.body)
                self.assertAlmostEqual(owner.stats.value(stats.HERO_DAMAGE) - credited, remaining)
                self.assertNotIn(key, fight.molten_burns)
                self.assertNotIn(lethal_burn.instance, lethal_burn.script.component.instances)
                run.step({})
                self.assertNotIn(bot.entity, pool.targets)

    @unittest.skipUnless(os.environ.get("ORPHEUS_NPC_CATALOG_DIR"), "needs five-NPC catalog")
    def test_launched_globule_deals_direct_damage_only_to_enemy_npc_bodies(self):
        catalog = npc.load_catalog(Path(os.environ["ORPHEUS_NPC_CATALOG_DIR"]) / "pve_npcs_174.json")
        for definition in catalog.values():
            for case in ("enemy", "ally", "wall"):
                with self.subTest(npc=definition.name, case=case):
                    run, owner, other, old_pool, _ = self.arena()
                    match = run.match
                    fight = match.combat
                    match.game_map = replace(match.game_map, free_for_all=False)
                    x, y, z = owner.mover.position
                    other.mover.position = [x + 30, y, z]
                    other.mover.body = None
                    molten_core.end_pool(fight, old_pool)
                    if case == "wall":
                        match.collision_world = World(
                            box(x - 25, x + 25, y - 1, y, z - 25, z + 25)
                            + wall(x - 3, z + 2.5, x + 3, z + 2.5, y0=y, y1=y + 4)
                        )
                    bot = npc.spawn(match, definition, (x, y, z + 5),
                                    team=owner.team if case == "ally" else 1 - owner.team)
                    target = next(target for target in fight._targets() if target.owner is bot)
                    point = (x, y + target.height * .5, z + 5)
                    yaw, pitch = aim(fight._eye(owner), point)
                    hp = fight.vitals[bot.entity]
                    before = hp.total
                    owner.body_script.set_ult_charge(10000)
                    run.step({owner: (yaw, pitch, 32)})
                    self.advance(run, 45)
                    with (mock.patch.object(fight, "_shoot", wraps=fight._shoot) as shots,
                          mock.patch.object(molten_core, "projectile_impact",
                                            wraps=molten_core.projectile_impact) as impacts):
                        run.step({owner: (yaw, pitch, 1)})
                        self.advance(run, 22)
                    self.assertEqual(sum(call.args[2].instance.graph.index == 0x16DA
                                         for call in shots.call_args_list), 1)
                    self.assertEqual(before - hp.total, 25 if case == "enemy" else 0)
                    self.assertNotIn((owner.entity, bot.entity), fight.molten_burns)
                    if case == "wall":
                        self.advance(run, 30)
                        pool, = fight.molten_pools.values()
                        self.assertLess(pool.position[2], z + 2.5)
                        self.assertAlmostEqual(pool.position[1], y)
                        self.assertEqual(hp.total, before)
                        self.assertNotIn((owner.entity, bot.entity), fight.molten_burns)
                        self.assertFalse(any(rocket.spawn == molten_core.POOL for rocket in fight.rockets))
                    elif case == "enemy":
                        self.assertFalse(fight.molten_pools)
                        self.assertFalse(any(rocket.spawn == molten_core.POOL for rocket in fight.rockets))
                        impacts.assert_called_once()
                        _, rocket, _, struck = impacts.call_args.args
                        self.assertEqual(rocket.spawn, molten_core.POOL)
                        self.assertIs(struck.owner, bot)

    def test_actual_globule_rebounds_from_ceiling_and_lands_on_floor(self):
        self.check_ceiling_rebound()

    def test_actual_globule_rebounds_twice_without_resetting_flight_or_creating_extra_pools(self):
        self.check_ceiling_rebound(with_wall=True)

    def terminal_rebounded_globule(self):
        run, owner, victim, fixture_pool, hp = self.arena()
        match = run.match
        fight = match.combat
        molten_core.end_pool(fight, fixture_pool)
        x, y, z = owner.mover.position
        victim.mover.position = [x + 30, y, z]
        victim.mover.body = None
        terrain = (box(x - 25, x + 25, y - 1, y, z - 25, z + 1)
                   + box(x - 8, x + 8, y + 2.5, y + 3, z - 4, z + 12))
        match.collision_world = World(terrain)
        owner.body_script.set_ult_charge(owner.body_script.component.entity_var(338).value())
        run.step({owner: (0, -7000, 32)})
        self.advance(run, 45)
        run.step({owner: (0, -7000, 1)})
        flight, = (rocket for rocket in fight.rockets if rocket.spawn == molten_core.POOL)
        lifetime = flight.left + flight.flown
        for _ in range(800):
            if flight.left <= combat.TICK + combat.EPSILON:
                break
            run.step({})
            self.assertIn(flight, fight.rockets)
            self.assertFalse(fight.molten_pools)
        self.assertGreater(flight.segment_start, 0)
        self.assertGreater(flight.left, combat.EPSILON)
        self.assertLessEqual(flight.left, combat.TICK + combat.EPSILON)
        self.assertAlmostEqual(flight.left + flight.flown, lifetime)
        self.assertLess(flight.position[1], y)
        self.assertGreater(flight.position[2], z + 1)
        return run, owner, hp, flight, terrain

    def test_rebounded_globule_sweeps_only_the_remaining_flight_at_expiry(self):
        for reachable in (False, True):
            with self.subTest(reachable=reachable):
                run, owner, hp, flight, terrain = self.terminal_rebounded_globule()
                match = run.match
                fight = match.combat
                lifetime = flight.left + flight.flown
                start_x, start_y, start_z = flight.position
                vertical_speed = flight.speed * flight.direction[1] - flight.gravity * (
                    flight.flown - flight.segment_start
                )
                duration = flight.left
                drop = -(vertical_speed - .5 * flight.gravity * duration) * duration
                self.assertGreater(drop, 0)
                surface = start_y - drop * (.5 if reachable else 1.5)
                catch_floor = box(start_x - 5, start_x + 5, surface - 1, surface, start_z - 5, start_z + 5)
                match.collision_world = World(terrain + catch_floor)
                run.step({})
                self.assertNotIn(flight, fight.rockets)
                if reachable:
                    pool, = fight.molten_pools.values()
                    self.assertIs(pool.owner, owner)
                    self.assertAlmostEqual(pool.position[1], surface)
                    self.assertLess(flight.flown, lifetime)
                else:
                    self.assertFalse(fight.molten_pools)
                    self.assertAlmostEqual(flight.flown, lifetime)
                    self.assertGreater(flight.position[1], surface)
                self.advance(run, 8)
                self.assertEqual(len(fight.molten_pools), int(reachable))
                self.assertFalse(fight.molten_burns)
                self.assertEqual(hp.total, 200)
                self.assertEqual(owner.stats.value(stats.HERO_DAMAGE), 0)

    @unittest.skipUnless(os.environ.get("ORPHEUS_NPC_CATALOG_DIR"), "needs five-NPC catalog")
    def test_final_globule_sweep_resolves_npc_and_floor_in_contact_order(self):
        catalog = npc.load_catalog(Path(os.environ["ORPHEUS_NPC_CATALOG_DIR"]) / "pve_npcs_174.json")
        self.assertEqual(len(catalog), 5)
        for definition in catalog.values():
            for body_first in (False, True):
                with self.subTest(npc=definition.name, body_first=body_first):
                    run, owner, _, flight, terrain = self.terminal_rebounded_globule()
                    match = run.match
                    fight = match.combat
                    origin = flight.position
                    elapsed = flight.flown
                    duration = flight.left
                    dx, dy, dz = (axis * flight.speed * duration for axis in flight.direction)
                    dy -= flight.gravity * (elapsed - flight.segment_start + .5 * duration) * duration
                    length = hypot(dx, dy, dz)
                    ray = tuple(value / length for value in (dx, dy, dz))
                    body_fraction, floor_fraction = (.25, .75) if body_first else (.75, .25)
                    bot = npc.spawn(match, definition, origin, team=1)
                    target = next(target for target in fight._targets() if target.owner is bot)
                    height, radius = target.height, target.radius
                    travel = body_fraction * length + radius + flight.body_radius
                    centre = tuple(value + axis * travel for value, axis in zip(origin, ray, strict=True))
                    bot.position = (centre[0], centre[1] - max(radius, height - radius), centre[2])
                    a, b = target.segment(bot.position)
                    contact = combat.ray_capsule(origin, ray, a, b, radius + flight.body_radius)
                    self.assertAlmostEqual(contact, body_fraction * length)
                    surface = origin[1] + dy * floor_fraction - flight.wall_radius
                    x, _, z = origin
                    match.collision_world = World(
                        terrain + box(x - 5, x + 5, surface - 1, surface, z - 5, z + 5)
                    )
                    hp = fight.vitals[bot.entity]
                    before = hp.total

                    run.step({})
                    self.assertNotIn(flight, fight.rockets)
                    self.assertGreater(flight.flown, elapsed)
                    self.assertLess(flight.flown, elapsed + duration)
                    damage = 25 if body_first else 0
                    self.assertEqual(hp.total, before - damage)
                    self.assertEqual(owner.stats.value(stats.HERO_DAMAGE), damage)
                    self.assertEqual(len(fight.molten_pools), int(not body_first))
                    if not body_first:
                        pool, = fight.molten_pools.values()
                        self.assertIs(pool.owner, owner)
                        self.assertAlmostEqual(pool.position[1], surface)
                    self.assertFalse(fight.molten_burns)

    @unittest.skipUnless(os.environ.get("ORPHEUS_NPC_CATALOG_DIR"), "needs five-NPC catalog")
    def test_ceiling_rebound_hits_enemy_npcs_and_passes_allies(self):
        catalog = npc.load_catalog(Path(os.environ["ORPHEUS_NPC_CATALOG_DIR"]) / "pve_npcs_174.json")
        self.assertEqual(len(catalog), 5)
        for definition in catalog.values():
            for friendly in (False, True):
                with self.subTest(npc=definition.name, friendly=friendly):
                    self.check_ceiling_rebound(target_definition=definition, friendly=friendly)

    def check_ceiling_rebound(self, *, with_wall=False, target_definition=None, friendly=False):
        run, owner, victim, old_pool, hp = self.arena()
        match = run.match
        fight = match.combat
        molten_core.end_pool(fight, old_pool)
        x, y, z = owner.mover.position
        victim.mover.position = [x + 30, y, z]
        victim.mover.body = None
        triangles = (
            box(x - 25, x + 25, y - 1, y, z - 25, z + 25)
            + box(x - 8, x + 8, y + 2.5, y + 3, z - 4, z + 12)
        )
        if with_wall:
            triangles += wall(x - 8, z + 4, x + 8, z + 4, y0=y, y1=y + 3)
        match.collision_world = World(triangles)
        if target_definition is not None:
            match.game_map = replace(match.game_map, free_for_all=False)
            team = owner.team if friendly else 1 - owner.team
            target = npc.spawn(match, target_definition, (x, y, z + 6.5), team=team)
            hp = fight.vitals[target.entity]
        before = hp.total
        owner.body_script.set_ult_charge(10000)
        run.step({owner: (0, -7000, 32)})
        self.advance(run, 45)
        run.step({owner: (0, -7000, 1)})
        flight, = (rocket for rocket in fight.rockets if rocket.spawn == molten_core.POOL)
        lifetime = flight.left
        self.assertGreater(flight.direction[1], 0)
        for _ in range(20):
            if flight.segment_start:
                break
            run.step({})
        self.assertIn(flight, fight.rockets)
        self.assertGreater(flight.segment_start, 0)
        self.assertLess(flight.direction[1], 0)
        self.assertFalse(fight.molten_pools)
        self.assertEqual(hp.total, before)
        self.assertAlmostEqual(flight.left + flight.flown, lifetime)
        if with_wall:
            first = flight.segment_start
            distance = flight.distance
            forward_speed = flight.speed * flight.direction[2]
            for _ in range(20):
                if flight.segment_start > first:
                    break
                run.step({})
            self.assertIn(flight, fight.rockets)
            self.assertGreater(flight.segment_start, first)
            self.assertGreater(flight.distance, distance)
            self.assertAlmostEqual(flight.speed * flight.direction[2], -.5 * forward_speed)
            self.assertAlmostEqual(flight.left + flight.flown, lifetime)
            self.assertFalse(fight.molten_pools)
        self.advance(run, 35)
        self.assertNotIn(flight, fight.rockets)
        if target_definition is not None and not friendly:
            self.assertEqual(hp.total, before - 25)
            self.assertEqual(owner.stats.value(stats.HERO_DAMAGE), 25)
            self.assertFalse(fight.molten_pools)
            self.assertFalse(fight.molten_burns)
            return
        pool, = fight.molten_pools.values()
        self.assertAlmostEqual(pool.position[1], y)
        self.assertGreater(pool.position[2], z)
        if with_wall:
            self.assertLess(pool.position[2], z + 4)
        self.assertEqual(hp.total, before)
        self.assertEqual(owner.stats.value(stats.HERO_DAMAGE), 0)

    def test_globule_lands_on_the_floor_it_hits_under_a_thin_overhang(self):
        run, owner, victim, old_pool, _ = self.arena()
        fight = run.match.combat
        molten_core.end_pool(fight, old_pool)
        x, y, z = owner.mover.position
        victim.mover.position = [x + 30, y, z]
        victim.mover.body = None
        run.match.collision_world = World(
            box(x - 25, x + 25, y - 1, y, z - 25, z + 25)
            + box(x - 1, x + 1, y + .025, y + .035, z + 2.32, z + 2.35)
        )
        owner.body_script.set_ult_charge(10000)
        run.step({owner: (0, 4000, 32)})
        self.advance(run, 45)
        run.step({owner: (0, 4000, 1)})
        self.advance(run, 25)
        pool, = fight.molten_pools.values()
        self.assertGreater(pool.position[2], z + 2.32)
        self.assertLess(pool.position[2], z + 2.35)
        self.assertAlmostEqual(pool.position[1], y)

    def test_globule_lands_on_shallow_ramp_and_rebounds_from_steep_ramp(self):
        for degrees, rebounds in ((30, False), (60, True)):
            with self.subTest(degrees=degrees):
                run, owner, victim, old_pool, hp = self.arena()
                fight = run.match.combat
                molten_core.end_pool(fight, old_pool)
                x, y, z = owner.mover.position
                ramp_start = z + 1
                victim.mover.position = [x + 30, y, z]
                victim.mover.body = None
                run.match.collision_world = World(
                    box(x - 35, x + 35, y - 1, y, z - 35, z + 35)
                    + ramp(ramp_start, z + 5, y, degrees, x - 8, x + 8)
                )
                owner.body_script.set_ult_charge(10000)
                run.step({owner: (0, 4000, 32)})
                self.advance(run, 45)
                run.step({owner: (0, 4000, 1)})
                flight, = (rocket for rocket in fight.rockets if rocket.spawn == molten_core.POOL)
                for _ in range(20):
                    if flight.segment_start or flight not in fight.rockets:
                        break
                    run.step({})
                if rebounds:
                    self.assertIn(flight, fight.rockets)
                    self.assertGreater(flight.segment_start, 0)
                    self.assertLess(flight.direction[2], 0)
                    self.assertFalse(fight.molten_pools)
                    for _ in range(100):
                        if flight not in fight.rockets:
                            break
                        run.step({})
                    pool, = fight.molten_pools.values()
                    self.assertAlmostEqual(pool.position[1], y)
                    self.assertLess(pool.position[2], ramp_start)
                else:
                    self.assertEqual(flight.segment_start, 0)
                    pool, = fight.molten_pools.values()
                    self.assertGreater(pool.position[2], ramp_start)
                    ramp_height = y + (pool.position[2] - ramp_start) * tan(radians(degrees))
                    self.assertAlmostEqual(pool.position[1], ramp_height)
                self.assertNotIn(flight, fight.rockets)
                victim.mover.position = list(pool.position)
                victim.mover.body = None
                for _ in range(35):
                    if (owner.entity, victim.body) in fight.molten_burns:
                        break
                    run.step({})
                self.assertIn((owner.entity, victim.body), fight.molten_burns)
                self.assertAlmostEqual(hp.total, 198.08)
                self.advance(run, 8)
                self.assertAlmostEqual(hp.total, 177.6)

    def test_landed_ramp_pool_burns_uphill_and_downhill_on_the_same_surface(self):
        run, owner, victim, old_pool, hp = self.arena()
        fight = run.match.combat
        molten_core.end_pool(fight, old_pool)
        x, y, z = owner.mover.position
        ramp_start = z + 1
        victim.mover.position = [x + 30, y, z]
        victim.mover.body = None
        run.match.collision_world = World(
            box(x - 35, x + 35, y - 1, y, z - 35, z + 35)
            + ramp(ramp_start, z + 6, y, 30, x - 8, x + 8)
        )
        owner.body_script.set_ult_charge(10000)
        run.step({owner: (0, 4000, 32)})
        self.advance(run, 45)
        run.step({owner: (0, 4000, 1)})
        self.advance(run, 45)
        pool, = fight.molten_pools.values()
        px, py, pz = pool.position
        self.assertEqual(pool.phase, 1)
        key = (owner.entity, victim.body)
        for offset in (-.6, 2.1, 3.5):
            with self.subTest(offset=offset):
                feet_y = py + offset * tan(radians(30))
                victim.mover.position = [px, feet_y, pz + offset]
                victim.mover.body = None
                before = hp.total
                self.advance(run, 8)
                expected = 20.48 if offset != 3.5 else 0
                self.assertAlmostEqual(before - hp.total, expected)
                self.assertEqual(key in fight.molten_burns, expected > 0)

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

    def test_same_tick_hero_switch_retires_pool_burn_and_old_globule_spawn(self):
        for airborne, predicted in ((False, False), (True, False), (True, True)):
            with self.subTest(airborne=airborne, predicted=predicted):
                run, owner, victim, old_pool, hp = self.arena()
                match = run.match
                fight = match.combat
                position = list(owner.mover.position)
                x, y, z = position
                victim.mover.position = [x + 30, y, z]
                victim.mover.body = None
                molten_core.end_pool(fight, old_pool)
                owner.body_script.set_ult_charge(10000)
                run.step({owner: (0, 4000, 32)})
                self.advance(run, 45)
                with ExitStack() as stack:
                    if predicted:
                        # This bundle launches without a lease. Exercise the host's leased path too.
                        stack.enter_context(
                            mock.patch.object(projectiles, "_sync", return_value=projectiles.PREDICTED))
                        stack.enter_context(
                            mock.patch.object(fight.projectiles, "lease_id", return_value=0xA0000001))
                    run.step({owner: (0, 4000, 1)})
                    for _ in range(15):
                        if any(rocket.spawn == molten_core.POOL for rocket in fight.rockets):
                            break
                        run.step({})
                rocket, = (rocket for rocket in fight.rockets if rocket.spawn == molten_core.POOL)
                shot = rocket.shot
                self.assertEqual(shot is not None, predicted)
                if not airborne:
                    for _ in range(100):
                        if fight.molten_pools:
                            break
                        run.step({})
                    pool, = fight.molten_pools.values()
                    victim.mover.position = list(pool.position)
                    victim.mover.body = None
                    self.advance(run, 40)
                    burn = fight.molten_burns[owner.entity, victim.body]
                self.assertTrue(match.switch_hero(owner, heroes()[SOLDIER]))
                self.assertFalse(fight.molten_pools)
                self.assertFalse(fight.molten_burns)
                if predicted:
                    self.assertNotIn(rocket, fight.rockets)
                    self.assertIn(shot, fight.projectiles.shots)
                    self.assertEqual(shot.spawn, molten_core.POOL)
                elif airborne:
                    self.assertIn(rocket, fight.rockets)
                    self.assertIsNone(rocket.spawn)
                else:
                    self.assertTrue(pool.ended)
                    self.assertNotIn(burn.instance, burn.script.component.instances)
                self.assertTrue(match.switch_hero(owner, heroes()[molten_core.HERO]))
                owner.mover.position = position
                owner.mover.body = None
                before = hp.total
                self.advance(run, 65)
                self.assertFalse(fight.molten_pools)
                self.assertFalse(fight.molten_burns)
                if shot is not None:
                    self.assertNotIn(shot, fight.projectiles.shots)
                self.assertEqual(hp.total, before)
                victim.mover.position = [x + 30, y, z]
                victim.mover.body = None
                owner.body_script.set_ult_charge(10000)
                run.step({owner: (0, 4000, 32)})
                self.advance(run, 45)
                run.step({owner: (0, 4000, 1)})
                self.advance(run, 65)
                replacement, = fight.molten_pools.values()
                self.assertIs(replacement.owner, owner)

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
        fight = run.match.combat
        burn_key = owner.entity, bot.entity
        self.advance(run, 656)
        self.assertAlmostEqual(hp.total, 2400.64, places=5)
        self.assertAlmostEqual(pool.last_update - pool.born - 0.5, 9.996, places=5)
        self.assertIn(pool.entity, fight.molten_pools)
        self.assertIn(burn_key, fight.molten_burns)
        # The next grid tick settles the remaining 4 ms and retires the pool.
        self.advance(run, 1)
        self.assertAlmostEqual(hp.total, 2400, places=5)
        self.assertAlmostEqual(owner.stats.value(stats.HERO_DAMAGE), 1600, places=5)
        self.assertAlmostEqual(pool.last_update - pool.born - 0.5, 10.012, places=5)
        self.assertTrue(pool.ended)
        self.assertNotIn(pool.entity, fight.molten_pools)
        self.assertNotIn(burn_key, fight.molten_burns)
        before = hp.total
        self.advance(run, 20)
        self.assertEqual(hp.total, before)

    def test_victim_death_retires_burn_and_records_caster(self):
        run, owner, victim, _, vitals = self.arena()
        self.advance(run, 120)
        self.assertTrue(vitals.dead)
        self.assertEqual(vitals.killer, owner.body)
        self.assertNotIn((owner.entity, victim.body), run.match.combat.molten_burns)

    def test_airborne_globule_lands_and_burns_after_caster_is_killed(self):
        run, owner, victim, old_pool, victim_hp = self.arena()
        fight = run.match.combat
        molten_core.end_pool(fight, old_pool)
        x, y, z = owner.mover.position
        victim.mover.position = [x + 4, y, z + 8]
        victim.mover.body = None
        target = next(target for target in fight._targets() if target.owner is owner)
        hp = fight.vitals_of(target)
        hp.damage(hp.total - 1)
        body = owner.body
        owner.body_script.set_ult_charge(10000)
        run.step({owner: (0, 4000, 32)})
        self.advance(run, 45)
        run.step({owner: (0, 4000, 1)})
        for _ in range(15):
            if any(rocket.spawn == molten_core.POOL for rocket in fight.rockets):
                break
            run.step({})
        flight, = (rocket for rocket in fight.rockets if rocket.spawn == molten_core.POOL)
        self.assertFalse(fight.molten_pools)
        direction = aim(fight._eye(victim), (x, y + .6, z))
        run.step({victim: (*direction, 1)})
        self.assertTrue(hp.dead)
        self.assertEqual(hp.killer, victim.body)
        self.assertIn(flight, fight.rockets)
        for _ in range(100):
            if any(pool.phase == 1 for pool in fight.molten_pools.values()):
                break
            run.step({owner: (0, 4000, 1)})
        pool, = fight.molten_pools.values()
        self.assertIs(pool.owner, owner)
        self.assertEqual(pool.phase, 1)
        self.assertNotIn(flight, fight.rockets)
        victim.mover.position = list(pool.position)
        victim.mover.body = None
        for _ in range(8):
            run.step({owner: (0, 4000, 1)})
        self.assertEqual(owner.body, body)
        self.assertTrue(fight.is_dead(owner))
        self.assertAlmostEqual(victim_hp.total, 179.52)
        self.assertAlmostEqual(owner.stats.value(stats.HERO_DAMAGE), 20.48)
        self.assertIn((owner.entity, victim.body), fight.molten_burns)
        self.assertEqual(list(fight.molten_pools.values()), [pool])
        self.assertFalse(any(rocket.spawn == molten_core.POOL for rocket in fight.rockets))

    @unittest.skipUnless(os.environ.get("ORPHEUS_NPC_CATALOG_DIR"), "needs five-NPC catalog")
    def test_actual_molten_pool_kills_assassin_and_releases_pin(self):
        run, owner, other, old_pool, _ = self.arena()
        match = run.match
        fight = match.combat
        molten_core.end_pool(fight, old_pool)
        match.game_map = replace(match.game_map, free_for_all=False)
        other.team = owner.team
        for viewer in match.players:
            viewer.client.bot_stream = lambda *args: None

        owner.body_script.set_ult_charge(10000)
        run.step({owner: (0, 4000, 32)})
        self.advance(run, 45)
        run.step({owner: (0, 4000, 1)})
        for _ in range(100):
            if any(pool.phase == 1 for pool in fight.molten_pools.values()):
                break
            run.step({})
        pool, = fight.molten_pools.values()
        self.assertEqual(pool.phase, 1)
        self.assertIs(pool.owner, owner)

        x, y, z = pool.position
        owner.mover.position = [x, y, z - 1.5]
        owner.mover.body = None
        directory = Path(os.environ["ORPHEUS_NPC_CATALOG_DIR"])
        catalog = npc.load_catalog(directory / "pve_npcs_174.json")
        definition = next(row for row in catalog.values() if row.name == "Assassin")
        bot = npc.spawn(match, definition, (x, y, z), team=1 - owner.team)
        hp = fight.vitals[bot.entity]
        self.assertEqual(hp.total, hp.maximum)
        self.advance(run, 2)
        self.assertEqual(npc_control.start(match, owner), bot.entity)
        for _ in range(150):
            run.step({})
            if bot.entity in fight.pins:
                break
        self.assertEqual(fight.pins.get(bot.entity), owner.body)
        self.assertTrue(fight.is_stunned(owner))
        owner_hp = fight.vitals[owner.body]
        self.assertLess(owner_hp.total, 250.0)

        before = hp.total
        self.assertGreater(before, 0.0)
        damaged_during_pin = False
        for _ in range(300):
            self.assertEqual(fight.pins.get(bot.entity), owner.body, (hp.total, owner_hp.total))
            run.step({})
            if bot.dead:
                break
            damaged_during_pin |= hp.total < before
        self.assertTrue(damaged_during_pin)
        self.assertTrue(bot.dead)
        self.assertEqual(hp.killer, owner.body)
        self.assertIn(pool.entity, fight.molten_pools)
        self.assertNotIn(bot.entity, fight.pins)
        self.assertFalse(fight.is_dead(owner))
        self.assertFalse(fight.is_stunned(owner))

    @unittest.skipUnless(os.environ.get("ORPHEUS_NPC_CATALOG_DIR"), "needs five-NPC catalog")
    def test_actual_npc_kills_cancel_new_molten_shots_but_preserve_launched_ones(self):
        directory = Path(os.environ["ORPHEUS_NPC_CATALOG_DIR"])
        catalog = npc.load_catalog(directory / "pve_npcs_174.json")
        for definition in catalog.values():
            for stage in ("before_release", "airborne", "landed"):
                with self.subTest(npc=definition.name, stage=stage):
                    run, owner, other, old_pool, _ = self.arena()
                    match = run.match
                    fight = match.combat
                    match.game_map = replace(match.game_map, free_for_all=False)
                    x, y, z = owner.mover.position
                    other.mover.position = [x + 30, y, z]
                    other.mover.body = None
                    for viewer in match.players:
                        viewer.client.bot_stream = lambda *args: None
                    molten_core.end_pool(fight, old_pool)
                    distance = 1.5 if definition.name == "Assassin" else 5
                    bot = npc.spawn(match, definition, (x + distance, y, z), team=1 - owner.team)
                    while run.frame < bot.records_from:
                        run.step({})
                    owner.body_script.set_ult_charge(10000)
                    run.step({owner: (0, 4000, 32)})
                    self.advance(run, 45)
                    self.assertLess(owner.body_script.component.entity_var(342).value(), 10)
                    if stage == "airborne":
                        # Keep the globule aloft long enough for the NPC's attack windup.
                        run.step({owner: (0, -12000, 1)})
                        for _ in range(15):
                            if any(r.spawn == molten_core.POOL for r in fight.rockets):
                                break
                            run.step({})
                        flight, = (r for r in fight.rockets if r.spawn == molten_core.POOL)
                    elif stage == "landed":
                        run.step({owner: (0, 4000, 1)})
                        for _ in range(100):
                            if any(pool.phase == 1 for pool in fight.molten_pools.values()):
                                break
                            run.step({})
                        pool, = fight.molten_pools.values()
                        self.assertEqual(pool.phase, 1)
                    body = owner.body
                    hp = fight.vitals[body]
                    hp.damage(hp.total - 1)
                    target = next(t for t in fight._targets() if t.owner is bot)
                    bx, by, bz = target.feet
                    direction = aim(fight._eye(owner), (bx, by + target.height * .5, bz))
                    run.step({owner: (*direction, 0)})
                    self.assertEqual(npc_control.start(match, owner), bot.entity)
                    for _ in range(200):
                        run.step({})
                        if hp.dead:
                            break
                    self.assertTrue(hp.dead)
                    self.assertEqual(hp.killer, bot.entity)
                    npc_control.stop(match)
                    if stage == "airborne":
                        self.assertIn(flight, fight.rockets)
                        self.assertFalse(fight.molten_pools)
                        for _ in range(400):
                            if any(pool.phase == 1 for pool in fight.molten_pools.values()):
                                break
                            run.step({owner: (0, 4000, 1)})
                        self.assertNotIn(flight, fight.rockets)
                        pool, = fight.molten_pools.values()
                        self.assertIs(pool.owner, owner)
                        self.assertEqual(pool.phase, 1)
                    launched = stage != "before_release"
                    bot_hp = fight.vitals[bot.entity]
                    before = bot_hp.total
                    credited = owner.stats.value(stats.HERO_DAMAGE)
                    if launched:
                        bot.position = pool.position
                    for _ in range(8):
                        run.step({owner: (0, 4000, 1)})
                    self.assertEqual(owner.body, body)
                    self.assertFalse(any(r.spawn == molten_core.POOL for r in fight.rockets))
                    self.assertEqual(list(fight.molten_pools.values()), [pool] if launched else [])
                    self.assertAlmostEqual(before - bot_hp.total, 20.48 if launched else 0)
                    self.assertAlmostEqual(
                        owner.stats.value(stats.HERO_DAMAGE) - credited, before - bot_hp.total)
                    self.assertEqual((owner.entity, bot.entity) in fight.molten_burns, launched)

    def test_caster_death_does_not_cancel_existing_area(self):
        run, owner, victim, pool, vitals = self.arena()
        fight = run.match.combat
        own_target = next(t for t in fight._targets() if t.owner is owner)
        fight._damage(victim, own_target, 1000.0, False, None, run.frame)
        self.advance(run, 94)
        self.assertTrue(fight.is_dead(owner))
        self.assertIn(pool.entity, fight.molten_pools)
        self.assertAlmostEqual(vitals.total, 39.36, places=5)
