"""Actual catalog NPC damage fixtures; AI/specials/client acceptance remain open.

Set ORPHEUS_NPC_TEST_DATA to an external directory containing the prepared
pve_npcs_174.json and extended game_graphs_174.json.gz. Normal runtime is unchanged.
"""

import math
import os
import unittest
from dataclasses import replace
from itertools import product
from pathlib import Path
from unittest.mock import patch

from test_game_combat import FakeClient, Run, aim, in_world
from test_game_mover import box, wall

from ow174.game import (
    barrier_mesh, bodytrace, combat, hanzo_control, heavy_assault, movelog, mover, npc, npc_control,
    projectiles, reinhardt_barrier, sniper_special,
)
from ow174.game.body_vm_snapshot import export_component_vm
from ow174.game.collision import World, flat_world
from ow174.game.commands import JUMP
from ow174.game.content import SOLDIER, heroes
from ow174.game.script import expr, graph

HANZO = 0x02E0000000000005
INPUTS = {"bow": (0, 125), "sonic": (8, 125), "storm": (16, 350), "melee": (0x800, 30)}
SNIPER_WEAPONS = ((1, 0x0400000000001E80, 100), (2, 0x04000000000023BB, 5))


def clear_graphs():
    graph._data.cache_clear()
    graph._records.cache_clear()
    graph.graph.cache_clear()


class NpcFixtureClient(FakeClient):
    """Record death stream callbacks without claiming real client delivery."""

    def __init__(self, player):
        super().__init__(player)
        self.npc_deaths = []
        self.npc_death_updates = []

    def queue_bot_death(self, entity, snapshot):
        self.npc_deaths.append((entity, snapshot))

    def queue_bot_death_update(self, entity, snapshot):
        self.npc_death_updates.append((entity, snapshot))

    def bot_stream(self, entity):
        return None  # no real NPC transport ACKs in this damage fixture


class HanzoCatalogNpcTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        folder = os.environ.get("ORPHEUS_NPC_TEST_DATA")
        if not folder:
            raise unittest.SkipTest("requires explicit prepared NPC data; no synthetic fallback")
        folder = Path(folder)
        cls.catalog = npc.load_catalog(folder / "pve_npcs_174.json")
        if set(cls.catalog) != set("12345"):
            raise ValueError("this matrix requires all five NPC definitions")
        table = mover._movers()
        if any(definition.body not in table for definition in cls.catalog.values()):
            raise ValueError("NPC matrix requires explicit104319 NPC mover rows;"
                             "use prepared NPC navigation runtime")
        cls.graph_patch = patch.object(graph, "GRAPHS_PATH", folder / "game_graphs_174.json.gz")
        cls.graph_patch.start()
        clear_graphs()

    @classmethod
    def tearDownClass(cls):
        cls.graph_patch.stop()
        clear_graphs()

    def setUp(self):
        for module in (bodytrace, movelog):
            self.enterContext(patch.object(module, "ENABLED", False))

    def arena(self, definition, action, situation, *, distance=None, lateral=0):
        run = Run()
        match = run.match
        # Explicit headless reconstruction: authored pellet counts, eye muzzles,
        # fixed ring phase / paired corners. Native spread and muzzle remain open.
        match.combat.configure_volley_geometry(reconstructed_patterns=True)
        match.bots.clear()
        match._pending_bot_scripts.clear()
        hero, = run.players
        hero.client = NpcFixtureClient(hero)
        match.switch_hero(hero, heroes()[HANZO])
        x, y, z = hero.mover.position
        if distance is None:
            distance = 2 if action == "melee" else 5
        bot = npc.spawn(match, definition, (x + lateral, y, z + distance),
                        team=hero.team if situation == "ally" else 1 - hero.team)
        self.assertEqual(bot.body, definition.body)
        self.assertIsNotNone(bot.body_script)
        self.assertTrue(bot.retain_corpse)
        if situation == "wall":
            w = z + (1 if action == "melee" else 2.5)
            match.collision_world = World([
                (x-20, y, z-20, x-20, y, z+20, x+20, y, z+20),
                (x-20, y, z-20, x+20, y, z+20, x+20, y, z-20),
                (x-10, y, w, x+10, y, w, x+10, y+5, w),
                (x-10, y, w, x+10, y+5, w, x-10, y+5, w),
                (x-10, y, w, x+10, y+5, w, x+10, y, w),
                (x-10, y, w, x-10, y+5, w, x+10, y+5, w),
            ], name="explicit floor and NPC occluder")
            hero.mover.use(world=match.collision_world)
        for _ in range(20):
            run.step({})
        return run, hero, bot

    def exercise(self, situation):
        for definition in self.catalog.values():
            for action, (button, amount) in INPUTS.items():
                with self.subTest(npc=definition.name, action=action, situation=situation):
                    run, hero, bot = self.arena(definition, action, situation)
                    script = hero.body_script
                    initial_time = script.passive_charge_ms
                    initial_frame = script.frame
                    initial_charge = script.component.entity_var(342).value()
                    yaw, pitch = self.target_aim(run, hero, bot)
                    if situation == "miss":
                        yaw, pitch = 16384, 0
                    if action == "melee":
                        run.step({hero: (yaw, pitch, button)})
                    else:
                        if button:
                            run.step({hero: (yaw, pitch, button)})
                            run.step({})
                        for _ in range(140 if action == "storm" else 70):
                            run.step({hero: (yaw, pitch, 1)})
                        if action != "storm":
                            run.step({hero: (yaw, pitch, 0)})
                    if action != "storm":
                        for _ in range(30):
                            run.step({})
                    # Releasing after Storm Arrows ends also releases the normal
                    # bow that is already drawing again. Measure the five-arrow
                    # skill before that distinct primary attack is released.
                    vitals = run.match.combat.vitals[bot.entity]
                    total = definition.health + definition.armour + definition.shields
                    self.assertEqual(definition.armour, 0)  # this explicit base-pool catalog
                    expected = max(0, total - amount) if situation == "hit" else total
                    self.assertEqual(vitals.total, expected)
                    shields = (max(0, definition.shields - amount)
                               if situation == "hit" else definition.shields)
                    self.assertEqual(vitals.now[combat.SHIELDS_POOL], shields)
                    self.assertEqual(vitals.now[combat.HEALTH_POOL], expected - shields)
                    hits = hero.client.messages(combat.HIT)
                    self.assertEqual(sum(h["+0x78"]["+0x4"] for h in hits), total - expected)
                    elapsed_ms = (script.frame - initial_frame) * script.frame_ms
                    passive_ticks = (initial_time + elapsed_ms) // 1000 - initial_time // 1000
                    self.assertEqual(
                        script.component.entity_var(342).value(),
                        initial_charge + passive_ticks * 5 + total - expected,
                    )
                    self.assertEqual(bot.dead, expected == 0)
                    self.assertFalse(any(who is bot for _, who in run.match.combat.respawns))
                    if bot.dead:
                        self.assertIsNotNone(bot.body_script)
                        self.assertTrue(bot.body_script.component.owner_dead)
                        self.assertEqual([entity for entity, _ in hero.client.npc_deaths], [bot.entity])
                        tick = run.frame + 1000
                        run.match.update(tick * combat.TICK, tick)
                        self.assertIn(bot, run.match.bots)
                        self.assertTrue(bot.dead)
                        self.assertFalse(any(who is bot for _, who in run.match.combat.respawns))
                    self.assertEqual(npc.remove_spawned(run.match), (bot.entity,))

    def test_storm_and_sonic_bank_shots_against_all_five_talon_bodies(self):
        from test_game_hanzo_sonic import profile

        sonic_profile = profile()
        for definition, mode, outcome in product(
            self.catalog.values(), ("storm", "sonic", "final_sonic"), ("hit", "wall", "lethal"),
        ):
            with self.subTest(npc=definition.name, mode=mode, outcome=outcome):
                combined = mode != "storm"
                run, hero, bot = self.arena(definition, "storm", "hit", distance=1, lateral=7)
                fight = run.match.combat
                x, y, z = hero.mover.position
                triangles = box(x - 25, x + 25, y - 1, y, z - 25, z + 25)
                triangles += wall(x - 10, z + 4, x + 15, z + 4)
                if outcome == "wall":
                    triangles += wall(x + 6, z - 5, x + 6, z + 8)
                run.match.collision_world = World(triangles, name="Talon bank shot")
                hero.mover.use(world=run.match.collision_world)
                if combined:
                    fight.configure_sonic(sonic_profile)
                vitals = fight.vitals[bot.entity]
                lethal = outcome == "lethal"
                if lethal:
                    vitals.damage(vitals.total - 70)
                before = vitals.total
                bank_height = y + (1.55 if mode == "final_sonic" else 1.0)
                angles = aim(fight._eye(hero), (x + 4, bank_height, z + 4))
                if mode == "final_sonic":
                    run.step({hero: (-32768, 0, 16)})
                    run.step({})
                    with patch.object(fight.projectiles, "fired", wraps=fight.projectiles.fired) as fired:
                        for _ in range(110):
                            run.step({hero: (-32768, 0, 1)})
                            if fired.call_count == 4:
                                break
                    self.assertEqual([call.args[3] for call in fired.call_args_list], [0x040000000000270F] * 4)
                    self.assertEqual(vitals.total, before)
                    run.step({hero: (*angles, 8)})
                else:
                    run.step({hero: (*angles, 24 if combined else 16)})
                run.step({hero: (*angles, 0)})
                prior = list(fight.rockets)
                for _ in range(40):
                    run.step({hero: (*angles, 1)})
                    new = [arrow for arrow in fight.rockets if arrow not in prior]
                    if new:
                        break
                rocket, = new
                definition_id = {"storm": 0x270F, "sonic": 0x0149, "final_sonic": 0x020D}[mode]
                self.assertEqual(rocket.shot.definition, 0x0400000000000000 | definition_id)
                if mode == "final_sonic":
                    instances = hero.body_script.component.instances.values()
                    storm = next(i for i in instances if i.graph.index == 0x146A)
                    sonic = next(i for i in instances if i.graph.index == 0x00C1)
                    storm_start = storm.vars[61].value()
                    sonic_start = sonic.vars[61].value()
                    release_frame = run.frame
                    self.assertGreater(storm_start, 9.9)
                    self.assertGreater(sonic_start, 11.9)
                    self.assertEqual(rocket.speed, 25)
                self.assertEqual((rocket.direct.amount, rocket.storm_ricochets), (70, 1))
                for _ in range(20 if mode == "final_sonic" else 8):
                    run.step({})
                    if rocket.storm_ricochets == 0:
                        break
                self.assertEqual(rocket.storm_ricochets, 0)
                self.assertIn(rocket, fight.rockets)
                self.assertLess(rocket.direction[2], 0)
                self.assertFalse(fight.sonars)
                for _ in range(20):
                    run.step({})
                self.assertNotIn(rocket, fight.rockets)
                self.assertEqual(rocket.source_entity, hero.body)
                if mode == "final_sonic":
                    elapsed = (run.frame - release_frame) * combat.TICK
                    self.assertAlmostEqual(storm.vars[61].value(), storm_start - elapsed, delta=0.02)
                    self.assertAlmostEqual(sonic.vars[61].value(), sonic_start - elapsed, delta=0.02)
                damage = 0 if outcome == "wall" else 70
                self.assertEqual(vitals.total, before - damage)
                hits = [hit["+0x78"] for hit in hero.client.messages(combat.HIT)]
                flags = combat.KILLING if lethal else 0
                expected = [{"+0x0": {"+0x0": bot.entity}, "+0x4": 70, "+0x8": flags}]
                self.assertEqual(hits, expected if damage else [])
                self.assertEqual(bot.dead, lethal)
                if lethal:
                    self.assertEqual(vitals.killer, hero.body)
                    self.assertEqual([entity for entity, _ in hero.client.npc_deaths], [bot.entity])
                if outcome == "wall":
                    self.assertAlmostEqual(rocket.position[0], x + 6, delta=0.01)
                if combined:
                    beacon, = fight.sonars
                    self.assertEqual(beacon.source_entity, hero.body)
                    self.assertEqual(beacon.attached, None if outcome == "wall" else bot.entity)
                    self.assertEqual(fight.revealed_to(hero, bot), not lethal)
                else:
                    self.assertFalse(fight.sonars)
                self.assertEqual(npc.remove_spawned(run.match), (bot.entity,))

    def test_secondary_and_reload_cancel_draw_against_all_five_talon_bodies(self):
        for definition in self.catalog.values():
            for cancel in (2, 0x400):
                with self.subTest(npc=definition.name, cancel=cancel):
                    run, hero, bot = self.arena(definition, "bow", "hit")
                    self.addCleanup(npc.remove_spawned, run.match)
                    fight = run.match.combat
                    yaw, pitch = self.target_aim(run, hero, bot)
                    target = next(t for t in fight._targets() if t.owner is bot)
                    vitals = fight.vitals_of(target)
                    before = vitals.total
                    with patch.object(fight.projectiles, "fired", wraps=fight.projectiles.fired) as fired:
                        for _ in range(20):
                            run.step({hero: (yaw, pitch, 1)})
                        for _ in range(15):
                            run.step({hero: (yaw, pitch, 1 | cancel)})
                        for _ in range(20):
                            run.step({})
                        self.assertFalse(fired.called)
                        self.assertFalse(fight.rockets)
                        self.assertEqual(vitals.total, before)
                        self.assertFalse(hero.client.messages(combat.HIT))

                        for _ in range(70):
                            run.step({hero: (yaw, pitch, 1)})
                        run.step({hero: (yaw, pitch, 0)})
                        for _ in range(10):
                            run.step({})
                    self.assertEqual(fired.call_count, 1)
                    self.assertEqual(vitals.total, before - 125)
                    hits = hero.client.messages(combat.HIT)
                    self.assertEqual([hit["+0x78"]["+0x4"] for hit in hits], [125])

    def test_direct_damage_and_death(self):
        self.exercise("hit")

    def test_actual_bow_upper_and_torso_impacts_on_all_talon_bodies(self):
        for key, upper in product(self.catalog, (False, True)):
            with self.subTest(npc=self.catalog[key].name, upper=upper):
                run, hero, bot = self.arena(self.catalog[key], "bow", "hit", distance=3)
                self.addCleanup(npc.remove_spawned, run.match)
                fight = run.match.combat
                target = next(t for t in fight._targets() if t.owner is bot)
                pool = fight.vitals_of(target)
                before = pool.total
                shields = pool.now[combat.SHIELDS_POOL]
                x, y, z = target.feet
                height = min(target.eye + 0.10, target.height - 0.08) if upper else target.height / 2
                angles = aim(fight._eye(hero), (x, y + height, z))
                for _ in range(70):
                    run.step({hero: (*angles, 1)})
                run.step({hero: (*angles, 0)})
                arrow, = fight.rockets
                self.assertEqual((arrow.direct.amount, arrow.direct.critical), (125, 2))
                for _ in range(12):
                    run.step({})
                    if arrow not in fight.rockets:
                        break
                self.assertNotIn(arrow, fight.rockets)
                self.assertEqual(arrow.position[1] >= y + target.eye - combat.HEAD_BELOW, upper)
                dealt = min(before, 250 if upper else 125)
                self.assertEqual(pool.total, before - dealt)
                self.assertEqual(pool.now[combat.SHIELDS_POOL], max(0, shields - dealt))
                hit, = hero.client.messages(combat.HIT)
                self.assertEqual(hit["+0x78"]["+0x4"], dealt)
                self.assertEqual(bool(hit["+0x78"]["+0x8"] & combat.CRITICAL), upper)
                self.assertEqual(bot.dead, dealt == before)
                if bot.dead:
                    self.assertEqual(pool.killer, hero.body)
                    self.assertTrue(hit["+0x78"]["+0x8"] & combat.KILLING)

    @staticmethod
    def target_aim(run, hero, bot):
        height = mover.mover_data(bot.body).stand_height
        return aim(run.match.combat._eye(hero),
                   (bot.position[0], bot.position[1] + height / 2, bot.position[2]))

    def aim_sniper_weapon(self, run, hero, bot, slot, *, hanzo_buttons=0):
        angles = self.target_aim(run, hero, bot)
        component = bot.body_script.component
        if component.entity_var(30).value() != slot:
            button = {1: 15, 2: 16}[slot]
            component.edges([(button, True)])
            run.step({hero: (*angles, hanzo_buttons)})
            component.edges([(button, False)])
        run.step({hero: (*angles, hanzo_buttons)})
        self.assertEqual(component.entity_var(30).value(), slot)

    def test_hanzo_actual_hits_spill_from_seeded_shields_through_armour_to_health(self):
        from test_game_hanzo_dragons import prepared_profile
        from test_game_hanzo_sonic import profile

        for action, amount in (("bow", 125), ("sonic", 125), ("storm", 70), ("melee", 30), ("dragon", 125)):
            with self.subTest(action=action):
                run, hero, bot = self.arena(self.catalog["5"], action, "hit")
                self.addCleanup(npc.remove_spawned, run.match)
                fight = run.match.combat
                health = self.catalog["5"].health
                pool = fight.vitals[bot.entity] = combat.Vitals(bot.entity, health, 6, 10)
                fight._sync_body_health(bot, pool)
                angles = self.target_aim(run, hero, bot)
                if action == "sonic":
                    fight.configure_sonic(profile())
                    run.step({hero: (*angles, 8)})
                    run.step({})
                if action in ("bow", "sonic"):
                    for _ in range(70):
                        run.step({hero: (*angles, 1)})
                    run.step({hero: (*angles, 0)})
                elif action == "storm":
                    run.step({hero: (*angles, 16)})
                    run.step({})
                elif action == "melee":
                    run.step({hero: (*angles, 0x800)})
                else:
                    fight.configure_dragonstrike(prepared_profile())
                    hero.body_script.ult_seconds = 0
                    hero.body_script.set_ult_charge(1680)
                    run.step({hero: (*angles, 32)})
                for _ in range(110):
                    if pool.total < health + 16:
                        break
                    run.step({hero: (*angles, int(action == "storm"))})
                self.assertEqual(pool.now[combat.SHIELDS_POOL], 0)
                self.assertEqual(pool.now[combat.ARMOUR_POOL], 0)
                self.assertEqual(pool.now[combat.HEALTH_POOL], health - (amount - 5 - 16))
                hit, = hero.client.messages(combat.HIT)
                self.assertEqual(hit["+0x78"]["+0x4"], amount - 5)
                self.assertEqual(tuple(pool.component(pool.ids)[51]), bot.body_script.component.world.health_pools)
                if action == "sonic":
                    beacon, = fight.sonars
                    self.assertEqual(beacon.attached, bot.entity)
                if action == "dragon":
                    initial, = fight.rockets
                    self.assertIn(bot.entity, initial.hit_entities)
                    self.assertFalse(fight.dragons)

    def test_friendly_bodies_are_not_damaged(self):
        self.exercise("ally")

    def test_off_axis_attacks_miss(self):
        self.exercise("miss")

    def test_static_wall_blocks_direct_attacks(self):
        self.exercise("wall")

    def test_sonic_as_final_storm_arrow_preserves_both_cooldown_boundaries(self):
        from test_game_hanzo_sonic import profile

        run, hero, bot = self.arena(self.catalog["5"], "storm", "hit")
        match = run.match
        self.addCleanup(npc.remove_spawned, match)
        fight = match.combat
        fight.configure_sonic(profile())
        component = hero.body_script.component
        storm = next(i for i in component.instances.values() if i.graph.index == 0x146A)
        sonic = next(i for i in component.instances.values() if i.graph.index == 0x00C1)
        duration = storm.find_var(0, 215)
        storm_cd, sonic_cd = storm.vars[61], sonic.vars[61]
        selected = component.entity_var(627)
        pool = fight.vitals[bot.entity]
        before = pool.total
        angles = self.target_aim(run, hero, bot)

        def step(buttons=0):
            run.step({hero: (*angles, buttons)})

        def shoot(expected):
            with patch.object(fight.projectiles, "fired", wraps=fight.projectiles.fired) as fired:
                for _ in range(35):
                    step(1)
                    if fired.called:
                        break
                fired.assert_called_once()
                self.assertEqual(fired.call_args.args[3], expected)
                arrow = fight.rockets[-1]
                self.assertIsNotNone(arrow.shot)
                self.assertEqual(arrow.shot.definition, expected)
                self.assertEqual((arrow.direct.amount, arrow.storm_ricochets), (70, 1))

        def wait_for_ready(cooldown, max_frames):
            self.assertGreater(cooldown.value(), 0)
            for _ in range(max_frames):
                step()
                if cooldown.value() == 0:
                    break
            self.assertEqual(cooldown.value(), 0)

        step(16)
        step()
        self.assertTrue(storm.states[0].flags & 1)
        for _ in range(4):
            shoot(0x040000000000270F)
            self.assertEqual(storm_cd.value(), 0)
        step(8)
        step()
        self.assertEqual(selected.value(), 1)
        self.assertEqual(sonic_cd.value(), 0)
        self.assertEqual(storm_cd.value(), 0)
        shoot(0x040000000000020D)
        self.assertEqual(duration.value(), 0)
        self.assertEqual(selected.value(), 0)
        self.assertGreater(storm_cd.value(), 9.9)
        self.assertGreater(sonic_cd.value(), 11.9)
        for _ in range(12):
            step()
        self.assertEqual(pool.total, before - 350)
        beacon, = fight.sonars
        self.assertEqual((beacon.source_entity, beacon.attached), (hero.body, bot.entity))
        self.assertTrue(fight.revealed_to(hero, bot))
        self.assertEqual([hit["+0x78"]["+0x4"] for hit in hero.client.messages(combat.HIT)], [70] * 5)

        step(8 | 16)
        step()
        self.assertEqual(duration.value(), 0)
        self.assertEqual(selected.value(), 0)
        wait_for_ready(storm_cd, 650)
        self.assertGreater(sonic_cd.value(), 1.8)
        self.assertFalse(storm.states[0].flags & 1)
        self.assertFalse(fight.sonars)

        step(8 | 16)
        step()
        self.assertTrue(storm.states[0].flags & 1)
        self.assertEqual(selected.value(), 0)
        shoot(0x040000000000270F)
        wait_for_ready(sonic_cd, 150)
        self.assertEqual(selected.value(), 0)
        self.assertGreater(duration.value(), 0)

        step(8)
        step()
        self.assertEqual(selected.value(), 1)
        shoot(0x0400000000000149)
        self.assertEqual(selected.value(), 0)
        self.assertEqual(storm_cd.value(), 0)
        self.assertGreater(sonic_cd.value(), 11.9)
        for _ in range(3):
            shoot(0x040000000000270F)
        self.assertEqual(duration.value(), 0)
        self.assertGreater(storm_cd.value(), 9.9)
        for _ in range(12):
            step()
        self.assertEqual(pool.total, before - 700)
        beacon, = fight.sonars
        self.assertEqual((beacon.source_entity, beacon.attached), (hero.body, bot.entity))
        self.assertEqual([hit["+0x78"]["+0x4"] for hit in hero.client.messages(combat.HIT)], [70] * 10)

    def test_short_arrows_check_talon_contact_wall_off_axis_and_faction(self):
        from test_game_hanzo_sonic import profile

        sonic_profile = profile()
        for definition, use_sonic, situation in product(
            self.catalog.values(), (False, True), ("hit", "wall", "miss", "ally"),
        ):
            with self.subTest(npc=definition.name, sonic=use_sonic, situation=situation):
                run, hero, bot = self.arena(definition, "bow", situation)
                match = run.match
                self.addCleanup(npc.remove_spawned, match)
                match.game_map = replace(match.game_map, free_for_all=False)
                fight = match.combat
                if use_sonic:
                    fight.configure_sonic(sonic_profile)
                angles = self.target_aim(run, hero, bot)
                x, y, z = hero.mover.position
                if situation == "miss":
                    angles = (16384, 0)
                    match.collision_world = World(
                        box(x - 20, x + 20, y - 1, y, z - 20, z + 20)
                        + wall(x + 12, z - 4, x + 12, z + 4, y0=y, y1=y + 5),
                    )
                downstream = None
                if situation == "ally":
                    downstream = match.add_player(2, "Downstream Soldier", SOLDIER, 1 - hero.team, False)
                    in_world(match, downstream)
                    run.players.append(downstream)
                    self.assertTrue(match.switch_hero(downstream, heroes()[SOLDIER]))
                    downstream.team = 1 - hero.team
                    downstream.mover.position[:] = (x, y, z + 8)
                    downstream.mover.body = None
                    angles = aim(fight._eye(hero), (x, y + 1, z + 8))
                    for _ in range(5):
                        run.step({})

                pool = fight.vitals[bot.entity]
                before = pool.total
                shields = pool.now[combat.SHIELDS_POOL]
                if use_sonic:
                    run.step({hero: (*angles, 8)})
                    run.step({})
                for _ in range(5):
                    run.step({hero: (*angles, 1)})
                run.step({hero: (*angles, 0)})
                arrow, = fight.rockets
                self.assertIsNotNone(arrow.shot)
                expected = 0x040000000000020D if use_sonic else 0x040000000000020B
                self.assertEqual(arrow.shot.definition, expected)
                amount = arrow.direct.amount
                self.assertAlmostEqual(amount, 25 + 100 * 1.375 * 0.079, delta=0.12)
                for _ in range(45):
                    run.step({})
                    if arrow not in fight.rockets:
                        break
                self.assertNotIn(arrow, fight.rockets)
                loss = amount if situation == "hit" else 0
                self.assertAlmostEqual(pool.total, before - loss, delta=0.01)
                self.assertAlmostEqual(pool.now[combat.SHIELDS_POOL], max(0, shields - loss), delta=0.01)
                self.assertFalse(bot.dead)
                hits = [hit["+0x78"] for hit in hero.client.messages(combat.HIT)]
                attached = bot.entity if situation == "hit" else None
                if downstream is not None:
                    attached = downstream.body
                    self.assertAlmostEqual(fight.vitals[attached].total, 200 - amount, delta=0.01)
                self.assertEqual(hits, [] if attached is None else [
                    {"+0x0": {"+0x0": attached}, "+0x4": round(amount), "+0x8": 0},
                ])

                if use_sonic:
                    beacon, = fight.sonars
                    self.assertEqual(beacon.source_entity, hero.body)
                    self.assertEqual(beacon.attached, attached)
                    self.assertEqual(fight.revealed_to(hero, bot), situation in ("hit", "wall"))
                    if situation == "wall":
                        self.assertAlmostEqual(beacon.position[2], z + 2.5, delta=0.01)
                    if situation == "miss":
                        self.assertAlmostEqual(beacon.position[0], x + 12, delta=0.01)
                    if downstream is not None:
                        self.assertTrue(fight.revealed_to(hero, downstream))
                else:
                    self.assertFalse(fight.sonars)

    def test_actual_arrows_pass_near_ally_only_in_team_mode(self):
        from test_game_hanzo_sonic import profile

        sonic_profile = profile()
        tank = next(hero for hero in heroes().values() if hero.name == "Roadhog")
        for definition in self.catalog.values():
            for action, free_for_all in product(("bow", "sonic", "storm"), (False, True)):
                with self.subTest(npc=definition.name, action=action, free_for_all=free_for_all):
                    run, hero, bot = self.arena(definition, action, "hit", distance=7)
                    match = run.match
                    self.addCleanup(npc.remove_spawned, match)
                    match.game_map = replace(match.game_map, free_for_all=free_for_all)
                    near = match.add_player(2, "Near body", tank.guid, hero.team, False)
                    in_world(match, near)
                    near.client = NpcFixtureClient(near)
                    run.players.append(near)
                    self.assertTrue(match.switch_hero(near, tank))
                    near.team = hero.team
                    x, y, z = hero.mover.position
                    near.mover.position[:] = (x, y, z + 2.5)
                    near.mover.body = None
                    fight = match.combat
                    if action == "sonic":
                        fight.configure_sonic(sonic_profile)
                    for _ in range(12):
                        run.step({})
                    near_pool = fight.vitals_of(next(t for t in fight._targets() if t.owner is near))
                    far_pool = fight.vitals[bot.entity]
                    near_before, far_before = near_pool.total, far_pool.total
                    button, amount = INPUTS[action]
                    angles = self.target_aim(run, hero, bot)
                    if button:
                        run.step({hero: (*angles, button)})
                        run.step({})
                    for _ in range(140 if action == "storm" else 70):
                        run.step({hero: (*angles, 1)})
                    if action != "storm":
                        run.step({hero: (*angles, 0)})
                        for _ in range(12):
                            run.step({})

                    near_loss = amount if free_for_all else 0
                    far_loss = 0 if free_for_all else min(amount, far_before)
                    self.assertEqual(near_pool.total, near_before - near_loss)
                    self.assertEqual(far_pool.total, far_before - far_loss)
                    hits = [hit["+0x78"] for hit in hero.client.messages(combat.HIT)]
                    target = near.body if free_for_all else bot.entity
                    self.assertTrue(hits)
                    self.assertEqual({hit["+0x0"]["+0x0"] for hit in hits}, {target})
                    self.assertEqual(sum(hit["+0x4"] for hit in hits), near_loss + far_loss)
                    if action == "sonic":
                        beacon, = fight.sonars
                        self.assertEqual(beacon.attached, target)
                        self.assertEqual(fight.revealed_to(hero, near), free_for_all)
                        self.assertTrue(fight.revealed_to(hero, bot))
                    else:
                        self.assertFalse(fight.sonars)

    def test_actual_npc_pool_queries_follow_hanzo_damage_and_expose_enforcer_controller_gate(self):
        for definition in self.catalog.values():
            with self.subTest(npc=definition.name):
                run, hero, bot = self.arena(definition, "bow", "hit")
                fight = run.match.combat
                instance = next(iter(bot.body_script.component.instances.values()))

                def query(cls, instance=instance):
                    return instance.evaluate({"$": cls, "m_entity": {"$": "STU_B5A0CAF0"}})

                total = definition.health + definition.armour + definition.shields
                for cls in ("STU_9B7648C3", "STU_32E6E4D8", "STU_7D7D2470"):
                    self.assertEqual(query(cls), total)
                yaw, pitch = self.target_aim(run, hero, bot)
                for _ in range(70):
                    run.step({hero: (yaw, pitch, 1)})
                run.step({hero: (yaw, pitch, 0)})
                for _ in range(30):
                    run.step({})
                self.assertEqual(query("STU_9B7648C3"), total - 125)
                self.assertEqual(query("STU_32E6E4D8"), total - 125)
                self.assertEqual(query("STU_7D7D2470"), total)
                self.assertEqual(tuple(fight.vitals[bot.entity].component(fight.vitals[bot.entity].ids)[51]),
                                 bot.body_script.component.world.health_pools)
                if definition.name == "Enforcer":
                    weapon = next(i for i in bot.body_script.component.instances.values()
                                  if i.graph.index == 0x13EB)
                    self.assertTrue(weapon.states[1].active)  # node1: now beyond node18's health gate
                if definition.name == "Trooper":
                    for _ in range(70):
                        run.step({hero: (yaw, pitch, 1)})
                    run.step({hero: (yaw, pitch, 0)})
                    for _ in range(30):
                        run.step({})
                    self.assertTrue(bot.dead)
                    self.assertEqual(query("STU_9B7648C3"), 0)
                    self.assertEqual(query("STU_7D7D2470"), total)
                npc.remove_spawned(run.match)

    def test_biotic_field_restores_bow_loss_only_to_allied_talon_bodies(self):
        for definition in self.catalog.values():
            for allied in (False, True):
                with self.subTest(npc=definition.name, allied=allied):
                    run, hero, bot = self.arena(definition, "bow", "hit")
                    self.addCleanup(npc.remove_spawned, run.match)
                    match = run.match
                    match.game_map = replace(match.game_map, free_for_all=False)
                    team = bot.team if allied else hero.team
                    medic = match.add_player(2, "Field owner", SOLDIER, team, False)
                    in_world(match, medic)
                    medic.client = NpcFixtureClient(medic)
                    run.players.append(medic)
                    self.assertTrue(match.switch_hero(medic, heroes()[SOLDIER]))
                    medic.team = team
                    x, y, z = bot.position
                    medic.mover.position[:] = (x + 2, y, z)
                    medic.mover.body = None
                    fight = match.combat
                    pool = fight.vitals[bot.entity]
                    total = pool.total
                    yaw, pitch = self.target_aim(run, hero, bot)
                    for _ in range(70):
                        run.step({hero: (yaw, pitch, 1)})
                    run.step({hero: (yaw, pitch, 0)})
                    for _ in range(12):
                        run.step({})
                    self.assertEqual(pool.total, total - 125)

                    run.step({medic: (0, 0, 16)})
                    for _ in range(100):
                        if any(shot.field_tick is not None for shot in fight.projectiles.shots):
                            break
                        run.step({})
                    field, = [shot for shot in fight.projectiles.shots if shot.field_tick is not None]
                    self.assertIs(field.player, medic)
                    self.assertEqual(pool.total, total - 125)
                    for _ in range(10):
                        run.step({})
                    given = 10 * projectiles.HEAL_PER_SECOND * combat.TICK if allied else 0
                    self.assertAlmostEqual(pool.total, total - 125 + given)
                    self.assertEqual(tuple(pool.component(pool.ids)[51]),
                                     bot.body_script.component.world.health_pools)
                    if not allied:
                        continue

                    for _ in range(70):
                        run.step({hero: (yaw, pitch, 1)})
                    pool.damage(max(0, pool.total - 90))  # Keep the next full draw lethal.
                    run.step({hero: (yaw, pitch, 0)})
                    for _ in range(15):
                        if pool.dead:
                            break
                        run.step({})
                    self.assertTrue(pool.dead)
                    self.assertEqual(pool.killer, hero.body)
                    for _ in range(25):
                        run.step({})
                        self.assertTrue(field in fight.projectiles.shots)
                        self.assertLess(run.frame - field.field_tick, projectiles.FIELD_TICKS)
                        self.assertEqual(pool.total, 0)
                        self.assertTrue(bot.body_script.component.owner_dead)
                    self.assertTrue(bot.dead)
                    self.assertEqual(tuple(pool.component(pool.ids)[51]),
                                     bot.body_script.component.world.health_pools)
                    self.assertEqual([entity for entity, _ in hero.client.npc_deaths], [bot.entity])

    def test_catalog_capsules_use_explicit_movers_in_combat_targets(self):
        expected = {"1": (1.7, 0.5, 5.75), "2": (1.9, 0.5, 5.0),
                    "3": (1.75, 0.5, 4.5), "4": (1.5, 0.5, 14.0), "5": (2.3, 0.7, 2.2)}
        for key, values in expected.items():
            with self.subTest(npc=self.catalog[key].name):
                definition = self.catalog[key]
                data = mover._movers()[definition.body]  # direct row access forbids fallback
                self.assertEqual((data.stand_height, data.stand_radius, data.run_forward), values)
                run, _hero, bot = self.arena(definition, "bow", "hit")
                target = next(t for t in run.match.combat._targets() if t.owner is bot)
                self.assertEqual((target.height, target.radius), values[:2])
                npc.remove_spawned(run.match)

    def test_actual_talon_capsules_straddle_bow_sonic_and_storm_terminal_paths(self):
        from test_game_hanzo_bow import HanzoBowTests
        from test_game_hanzo_sonic import profile

        sonic_profile = profile()

        def release(run, hero, attack, held, pitch, projectile):
            fight = run.match.combat
            if attack == "sonic":
                fight.configure_sonic(sonic_profile)
            with patch.object(fight.projectiles, "fired", wraps=fight.projectiles.fired) as fired:
                if attack != "bow":
                    run.step({hero: (0, pitch, INPUTS[attack][0])})
                    run.step({})
                if attack == "storm":
                    for _ in range(40):
                        run.step({hero: (0, pitch, 1)})
                        if fight.rockets:
                            break
                else:
                    for _ in range(held):
                        run.step({hero: (0, pitch, 1)})
                    run.step({hero: (0, pitch, 0)})
            fired.assert_called_once()
            self.assertEqual(fired.call_args.args[3], projectile)
            arrow, = fight.rockets
            self.assertIs(arrow.sonar, sonic_profile if attack == "sonic" else None)
            return arrow

        def advance(run, fight, frames):
            tick = run.frame + frames
            fight.match.tick = tick
            fight.update(tick * combat.TICK, tick, frames=False)
            run.frame = tick

        for attack, held, projectile, expected_amount in (
            ("bow", 70, 0x0400000000000144, 125),
            ("bow", 5, 0x040000000000020B, 25 + 100 * 1.375 * 0.079),
            ("sonic", 70, 0x0400000000000149, 125),
            ("sonic", 5, 0x040000000000020D, 25 + 100 * 1.375 * 0.079),
            ("storm", 0, 0x040000000000270F, 70),
        ):
            for pitch in (0, -16384):
                with self.subTest(attack=attack, held=held, pitch=pitch):
                    baseline = Run()
                    baseline.match.bots.clear()
                    baseline.match._pending_bot_scripts.clear()
                    hero, = baseline.players
                    self.assertTrue(baseline.match.switch_hero(hero, heroes()[HANZO]))
                    for _ in range(20):
                        baseline.step({})
                    fight = baseline.match.combat
                    fight.match.collision_world = World([], name="empty arrow terminal path")
                    arrow = release(baseline, hero, attack, held, pitch, projectile)
                    self.assertEqual((arrow.range_left, arrow.left), (1000, 20))
                    amount = arrow.direct.amount
                    self.assertAlmostEqual(amount, expected_amount, delta=0.12)
                    vx, vy, vz = (axis * arrow.speed for axis in arrow.direction)
                    launch_point = arrow.position
                    HanzoBowTests.finish(baseline, fight, arrow, skipped=True)
                    self.assertFalse(fight.rockets)
                    self.assertFalse(fight.sonars)
                    lifetime = pitch == -16384 and held != 5
                    if lifetime:
                        self.assertAlmostEqual(arrow.flown, 20)
                        self.assertLess(arrow.distance_flown, 1000)
                    else:
                        self.assertLess(arrow.flown, 20)
                        self.assertAlmostEqual(arrow.distance_flown, 1000)
                    terminal_time = arrow.flown
                    displacement = tuple(a - b for a, b in zip(arrow.position, launch_point, strict=True))
                    velocity = (vx, vy - arrow.gravity * terminal_time, vz)
                    speed = math.hypot(*velocity)
                    tangent = tuple(axis / speed for axis in velocity)

                    for definition in self.catalog.values():
                        for offset in (-2, 2):
                            with self.subTest(npc=definition.name, offset=offset):
                                run, hero, bot = self.arena(definition, attack, "hit", lateral=60)
                                try:
                                    fight = run.match.combat
                                    fight.match.collision_world = World([], name="Talon arrow terminal path")
                                    arrow = release(run, hero, attack, held, pitch, projectile)
                                    self.assertEqual(arrow.direct.amount, amount)
                                    endpoint = tuple(a + b for a, b in zip(arrow.position, displacement, strict=True))
                                    advance(run, fight, 1)
                                    advance(run, fight, math.floor((terminal_time - 0.5) / combat.TICK))
                                    self.assertIn(arrow, fight.rockets)
                                    target = next(t for t in fight._targets() if t.owner is bot)
                                    x, y, z = (p + offset * d for p, d in zip(endpoint, tangent, strict=True))
                                    bot.position = (x, y - target.height / 2, z)
                                    pool = fight.vitals_of(target)
                                    before = pool.total
                                    advance(run, fight, 90)
                                    self.assertFalse(fight.rockets)
                                    # Descending paths enter the upper hit region first. The steep
                                    # short-draw horizontal paths and tall Heavy do so as well.
                                    critical = offset < 0 and (
                                        pitch < 0 or held == 5 or definition is self.catalog["5"]
                                    )
                                    dealt = min(before, amount * (2 if critical else 1)) if offset < 0 else 0
                                    lethal = dealt == before
                                    self.assertAlmostEqual(pool.total, before - dealt)
                                    self.assertEqual(pool.dead, lethal)
                                    notices = hero.client.messages(combat.HIT)
                                    flags = (combat.CRITICAL if critical else 0) | (combat.KILLING if lethal else 0)
                                    self.assertEqual([hit["+0x78"] for hit in notices], [
                                        {"+0x0": {"+0x0": bot.entity}, "+0x4": round(dealt), "+0x8": flags},
                                    ] if dealt else [])
                                    if lethal:
                                        self.assertEqual(pool.killer, hero.body)
                                    deployed = bool(dealt and attack == "sonic")
                                    self.assertEqual(fight.revealed_to(hero, bot), deployed and not lethal)
                                    if deployed:
                                        beacon, = fight.sonars
                                        self.assertEqual((beacon.source_entity, beacon.attached),
                                                         (hero.body, bot.entity))
                                    else:
                                        self.assertFalse(fight.sonars)
                                    if not dealt:
                                        self.assertAlmostEqual(arrow.flown, terminal_time)
                                        for actual, expected in zip(arrow.position, endpoint, strict=True):
                                            self.assertAlmostEqual(actual, expected)
                                finally:
                                    npc.remove_spawned(run.match)

    def test_real_heavy_capsule_extends_melee_range_and_static_wall_still_blocks(self):
        for key, definition in self.catalog.items():
            for blocked in (False, True):
                with self.subTest(npc=definition.name, wall=blocked):
                    run, hero, bot = self.arena(definition, "melee", "wall" if blocked else "hit",
                                                distance=3.1)
                    run.step({hero: (0, 0, 0x800)})
                    for _ in range(10):
                        run.step({})
                    before = definition.health + definition.armour + definition.shields
                    damage = before - run.match.combat.vitals[bot.entity].total
                    self.assertEqual(damage, 30 if key == "5" and not blocked else 0)
                    npc.remove_spawned(run.match)

    def test_actual_quick_melee_straddles_each_talon_capsule_range(self):
        for definition in self.catalog.values():
            capsule = mover._movers()[definition.body]
            # The authored sphere is 1.5 m ahead of the eye with a 1 m radius.
            # Horizontal aim can pass above the top of a short body's segment.
            radius = capsule.stand_radius
            vertical_gap = max(0, 1.35 - (capsule.stand_height - radius))
            limit = 1.5 + math.sqrt((1 + radius) ** 2 - vertical_gap ** 2)
            for situation, offset, dealt in (("hit", -0.05, 30), ("hit", 0.05, 0),
                                             ("ally", -0.05, 0), ("wall", -0.05, 0)):
                with self.subTest(npc=definition.name, situation=situation, offset=offset):
                    run, hero, bot = self.arena(definition, "melee", situation, distance=limit + offset)
                    self.addCleanup(npc.remove_spawned, run.match)
                    fight = run.match.combat
                    self.assertAlmostEqual(fight._eye(hero)[1] - bot.position[1], 1.35)
                    pool = fight.vitals[bot.entity]
                    before = pool.total
                    run.step({hero: (0, 0, 0x800)})
                    for _ in range(10):
                        run.step({})
                    self.assertEqual(pool.total, before - dealt)
                    hits = hero.client.messages(combat.HIT)
                    self.assertEqual([hit["+0x78"]["+0x4"] for hit in hits], [30] if dealt else [])

    def test_one_actual_quick_melee_filters_mixed_player_and_talon_targets(self):
        for definition in self.catalog.values():
            for allied in (None, "player", "npc"):
                with self.subTest(npc=definition.name, allied=allied):
                    situation = "ally" if allied == "npc" else "hit"
                    run, hanzo, bot = self.arena(definition, "melee", situation, distance=2.4, lateral=0.6)
                    match = run.match
                    self.addCleanup(npc.remove_spawned, match)
                    match.game_map = replace(match.game_map, free_for_all=False)
                    team = hanzo.team if allied == "player" else 1 - hanzo.team
                    player = match.add_player(2, "Soldier", SOLDIER, team, False)
                    in_world(match, player)
                    player.client = NpcFixtureClient(player)
                    run.players.append(player)
                    self.assertTrue(match.switch_hero(player, heroes()[SOLDIER]))
                    player.team = team
                    x, y, z = hanzo.mover.position
                    player.mover.position[:] = (x - 0.6, y, z + 1.8)
                    player.mover.body = None
                    for _ in range(10):
                        run.step({})
                    fight = match.combat
                    pools = {target.entity: fight.vitals_of(target) for target in fight._targets()
                             if target.owner is player or target.owner is bot}
                    before = {entity: pool.total for entity, pool in pools.items()}
                    expected_hits = {player.body: 30, bot.entity: 30}
                    if allied is not None:
                        del expected_hits[player.body if allied == "player" else bot.entity]
                    script = hanzo.body_script
                    initial_passive_ms = script.passive_charge_ms
                    initial_frame = script.frame
                    charge = script.component.entity_var(combat.ULT_CHARGE)
                    initial_charge = charge.value()
                    run.step({hanzo: (0, 0, 0x800)})
                    for _ in range(10):
                        run.step({})
                    self.assertEqual({entity: before[entity] - pool.total for entity, pool in pools.items()},
                                     {entity: expected_hits.get(entity, 0) for entity in before})
                    hits = [notice["+0x78"] for notice in hanzo.client.messages(combat.HIT)]
                    self.assertCountEqual([(hit["+0x0"]["+0x0"], hit["+0x4"]) for hit in hits],
                                          expected_hits.items())
                    elapsed_ms = (script.frame - initial_frame) * script.frame_ms
                    passive_ticks = (initial_passive_ms + elapsed_ms) // 1000 - initial_passive_ms // 1000
                    self.assertEqual(charge.value(), initial_charge + passive_ticks * 5 + sum(expected_hits.values()))

    def test_authored_eye_hits_short_assassin_horizontally_and_capsule_aim_while_high_aim_misses(self):
        definition = self.catalog["4"]
        for mode in ('horizontal', 'capsule', 'above'):
            with self.subTest(aim=mode):
                run, hero, bot = self.arena(definition, "bow", "hit")
                if mode == 'capsule':
                    yaw, pitch = self.target_aim(run, hero, bot)
                elif mode == 'above':
                    yaw, pitch = aim(run.match.combat._eye(hero),
                                     (bot.position[0], bot.position[1] + 2.5, bot.position[2]))
                else:
                    yaw, pitch = 0, 0
                for _ in range(70):
                    run.step({hero: (yaw, pitch, 1)})
                run.step({hero: (yaw, pitch, 0)})
                for _ in range(30):
                    run.step({})
                total = definition.health + definition.armour + definition.shields
                self.assertEqual(run.match.combat.vitals[bot.entity].total,
                                 total if mode == 'above' else total - 125)
                npc.remove_spawned(run.match)

    def test_authored_npc_controller_hit_range_faction_and_wall(self):
        # The public reconstructed manual controller supplies actual slot input
        # and aim. Native autonomous acquisition/specials remain unestablished.
        for key, hit_health in (("1", 20), ("2", 100), ("3", 100), ("5", 0)):
            definition = self.catalog[key]
            for situation in ("hit", "range", "ally", "wall"):
                with self.subTest(npc=definition.name, situation=situation):
                    run, hero, bot = self.arena(
                        definition, "bow", "hit" if situation == "range" else situation,
                    )
                    bot.firing = True
                    bot.attack_target = hero.entity
                    if situation == "range":
                        hero.mover.position[:] = (bot.position[0], bot.position[1], bot.position[2] + 110)
                        hero.mover.body = None
                    fight = run.match.combat
                    target = next(t for t in fight._targets() if t.owner is hero)
                    vitals = fight.vitals_of(target)
                    with patch.object(fight, "_shoot", wraps=fight._shoot) as fire:
                        for _ in range(200):
                            run.step({})
                    self.assertEqual(fire.called, situation == "hit")
                    self.assertIsNotNone(bot.body_script)
                    expected = hit_health if situation == "hit" else 200
                    self.assertEqual(vitals.total, expected)
                    self.assertEqual(vitals.dead, expected == 0)
                    self.assertEqual(npc.remove_spawned(run.match), (bot.entity,))

    def test_manual_sniper_loses_windup_on_occlusion_and_hits_after_reacquiring(self):
        run, hero, bot = self.arena(self.catalog["3"], "bow", "hit")
        self.addCleanup(npc.remove_spawned, run.match)
        fight = run.match.combat
        clear = run.match.collision_world
        yaw, pitch = self.target_aim(run, hero, bot)
        run.step({hero: (yaw, pitch, 0)})
        self.assertEqual(npc_control.start(run.match, hero), bot.entity)
        self.assertEqual(npc_control.button(bot), 20)
        target = next(t for t in fight._targets() if t.owner is hero)
        pool = fight.vitals_of(target)
        x, y, z = hero.mover.position
        mid = (z + bot.position[2]) / 2
        blocked = World(box(x - 20, x + 20, y - 1, y, z - 20, z + 20)
                        + wall(x - 5, mid, x + 5, mid, y0=y, y1=y + 5))

        with patch.object(fight, "_shoot", wraps=fight._shoot) as shoot:
            for _ in range(50):
                run.step({})
            self.assertTrue(bot.attack_input)
            self.assertFalse(shoot.called)
            run.match.collision_world = blocked
            for _ in range(160):
                run.step({})
            self.assertFalse(bot.attack_input)
            self.assertFalse(shoot.called)
            self.assertEqual(pool.total, 200)

            run.match.collision_world = clear
            for _ in range(100):
                run.step({})
            self.assertTrue(bot.attack_input)
            self.assertFalse(shoot.called)
            for _ in range(60):
                if pool.total < 200:
                    break
                run.step({})
            self.assertEqual(pool.total, 100)
            self.assertEqual(shoot.call_count, 1)
            self.assertIs(shoot.call_args.args[0], bot)
            self.assertIs(shoot.call_args.args[2].instance.component, bot.body_script.component)
            self.assertEqual(npc_control.stop(run.match), 1)
            for _ in range(160):
                run.step({})
            self.assertEqual(shoot.call_count, 1)
            self.assertEqual(pool.total, 100)

    def movement_body_scene(self, key, distance):
        definition = self.catalog.get(key, self.catalog["1"])
        run, hero, actor = self.arena(definition, "bow", "hit", distance=distance)
        self.addCleanup(npc.remove_spawned, run.match)
        if key in ("empty", "player"):
            npc.remove_spawned(run.match)
            actor = None
        if key == "player":
            x, y, z = hero.mover.position
            actor = run.match.add_player(2, "Enemy", SOLDIER, 1 - hero.team, False)
            in_world(run.match, actor)
            run.players.append(actor)
            self.assertTrue(run.match.switch_hero(actor, heroes()[SOLDIER]))
            actor.mover.position[:] = (x, y, z + distance)
            actor.mover.body = None
        return run, hero, actor

    def test_lunge_trajectory_through_player_and_all_five_talon_bodies(self):
        from test_game_hanzo_movement import HanzoLungeTests

        movement = HanzoLungeTests()
        reference = None
        for key in ("empty", "player", *self.catalog):
            with self.subTest(body=key):
                run, hero, actor = self.movement_body_scene(key, distance=1.5)
                z = hero.mover.position[2]
                fight = run.match.combat
                if actor is not None:
                    target = next(t for t in fight._targets() if t.owner is actor)
                    pool = fight.vitals_of(target)
                    total = pool.total
                movement.arm(run, hero)
                movement.step(run, hero, buttons=JUMP)
                trajectory = []
                overlapped = False
                for _ in range(20):
                    movement.step(run, hero)
                    trajectory.append(tuple(hero.mover.position))
                    if actor is not None and not overlapped:
                        bodies = {t.owner: t for t in fight._targets()}
                        moving = bodies[hero]
                        stationary = bodies[actor]
                        centre, _ = moving.segment()
                        a, b = stationary.segment()
                        overlapped = combat.segment_distance(centre, a, b) < moving.radius + stationary.radius
                self.assertGreater(hero.mover.position[2], z + 2)
                if reference is None:
                    reference = trajectory
                else:
                    self.assertTrue(overlapped)
                    self.assertEqual(trajectory, reference)
                    self.assertEqual(pool.total, total)
                    self.assertFalse(hero.client.messages(combat.HIT))

    def test_wall_climb_requires_static_contact_even_with_a_body_in_reach(self):
        from test_game_hanzo_climb import HanzoClimbTests

        for solid_wall in (False, True):
            reference = None
            for key in ("empty", "player", *self.catalog):
                with self.subTest(wall=solid_wall, body=key):
                    run, hero, actor = self.movement_body_scene(key, distance=0.7)
                    fight = run.match.combat
                    x, y, z = hero.mover.position
                    geometry = box(x - 20, x + 20, y - 1, y, z - 20, z + 20)
                    if solid_wall:
                        geometry += wall(x - 5, z + 0.75, x + 5, z + 0.75, y0=y, y1=y + 20)
                    run.match.collision_world = World(geometry)
                    if actor is not None:
                        target = next(t for t in fight._targets() if t.owner is actor)
                        pool = fight.vitals_of(target)
                        total = pool.total
                        a, b = target.segment()
                        entry = combat.ray_capsule((x, y + 1, z), (0, 0, 1), a, b, target.radius)
                        self.assertIsNotNone(entry)
                        self.assertLess(entry, 0.8)
                    trajectory = []
                    for _ in range(24):
                        HanzoClimbTests.step(run, hero, buttons=JUMP)
                        trajectory.append(tuple(hero.mover.position))
                    progress = hero.body_script.world.hanzo_climb
                    self.assertEqual(progress.active, solid_wall)
                    self.assertEqual(progress.elapsed > 0, solid_wall)
                    if actor is None:
                        reference = trajectory
                    else:
                        self.assertEqual(trajectory, reference)
                        self.assertEqual(pool.total, total)
                        self.assertFalse(hero.client.messages(combat.HIT))

    def sniper_reposition_arena(self, distance=16):
        run, hero, bot = self.arena(self.catalog["3"], "bow", "hit", distance=distance)
        match = run.match
        self.addCleanup(npc.remove_spawned, match)
        match.game_map = replace(match.game_map, free_for_all=False)
        match.collision_world = flat_world(hero.mover.position[1])
        hero.mover.use(world=match.collision_world)
        for _ in range(145):
            run.step({})
        special = next(i for i in bot.body_script.component.instances.values()
                       if i.graph.index == sniper_special.GRAPH)
        return run, hero, bot, special

    def test_sniper_reposition_reaches_authored_destination_then_fires_at_hanzo(self):
        run, hero, bot, special = self.sniper_reposition_arena()
        fight = run.match.combat
        start = bot.position
        run.step({hero: (*self.target_aim(run, hero, bot), 0)})
        self.assertEqual(npc_control.start(run.match, hero, reposition=True), bot.entity)
        move = special.node_state(special.graph.node(16))
        for _ in range(100):
            run.step({})
            if move.move_request() is not None:
                break
        destination, speed = move.move_request()
        self.assertEqual(speed, 20)
        self.assertAlmostEqual(math.dist(start, destination), 2.5, places=4)
        for _ in range(40):
            run.step({})
            if not move.active:
                break
        self.assertFalse(move.active)
        self.assertLess(math.dist(bot.position, destination), 0.01)
        self.assertEqual(special.vars[59].value(), 2)
        for _ in range(240):
            run.step({})
            if fight.rockets:
                break
        shot, = fight.rockets
        self.assertIs(shot.shooter, bot)
        self.assertEqual(shot.source_entity, bot.entity)
        self.assertEqual(shot.direct.amount, 100)
        self.assertEqual(bot.velocity, (0, 0, 0))
        for _ in range(30):
            run.step({})
            if shot not in fight.rockets:
                break
        self.assertEqual(fight.vitals[hero.body].total, 100)

    def sonic_sniper_reposition_arena(self):
        from test_game_hanzo_sonic import profile

        run, hero, bot, special = self.sniper_reposition_arena()
        fight = run.match.combat
        fight.configure_sonic(profile())
        pool = fight.vitals[bot.entity]
        initial_health = pool.total
        angles = self.target_aim(run, hero, bot)
        run.step({hero: (*angles, 8)})
        run.step({hero: (*angles, 0)})
        for _ in range(70):
            run.step({hero: (*angles, 1)})
        run.step({hero: (*angles, 0)})
        for _ in range(24):
            if fight.sonars:
                break
            run.step({})

        beacon, = fight.sonars
        self.assertEqual(beacon.attached, bot.entity)
        self.assertEqual(pool.total, initial_health - 125)
        self.assertTrue(fight.revealed_to(hero, bot))
        self.assertFalse(fight.revealed_to(bot, hero))
        return run, hero, bot, special, beacon

    def test_attached_sonic_tracks_talon_sniper_through_reposition_and_counterfire(self):
        run, hero, bot, special, beacon = self.sonic_sniper_reposition_arena()
        fight = run.match.combat
        pool = fight.vitals[bot.entity]
        remaining_health = pool.total
        status = beacon.statuses[bot.entity]
        start = bot.position
        initial_beacon = beacon.position
        self.assertEqual(npc_control.start(run.match, hero, reposition=True), bot.entity)
        move = special.node_state(special.graph.node(16))
        for _ in range(100):
            run.step({})
            request = move.move_request()
            if request is not None:
                break
        self.assertIsNotNone(request)
        destination, speed = request
        self.assertEqual(speed, 20)
        self.assertAlmostEqual(math.dist(start, destination), 2.5, places=4)
        moving_samples = 0
        for _ in range(40):
            previous = bot.position
            run.step({})
            self.assertIs(beacon.statuses[bot.entity], status)
            expected_position = tuple(a + b for a, b in zip(bot.position, beacon.offset, strict=True))
            self.assertEqual(beacon.position, expected_position)
            if bot.position != previous:
                moving_samples += 1
            if not move.active:
                break
        self.assertGreater(moving_samples, 1)
        self.assertFalse(move.active)
        self.assertLess(math.dist(bot.position, destination), 0.01)
        self.assertAlmostEqual(math.dist(beacon.position, initial_beacon), 2.5, places=2)

        for _ in range(240):
            run.step({})
            if fight.rockets:
                break
        shot, = fight.rockets
        self.assertIs(shot.shooter, bot)
        self.assertEqual(shot.source_entity, bot.entity)
        self.assertEqual(shot.direct.amount, 100)
        for _ in range(30):
            run.step({})
            if shot not in fight.rockets:
                break
        self.assertEqual(fight.vitals[hero.body].total, 100)
        self.assertEqual(pool.total, remaining_health)
        self.assertEqual(beacon.attached, bot.entity)
        self.assertIs(beacon.statuses[bot.entity], status)
        self.assertTrue(fight.revealed_to(hero, bot))
        self.assertFalse(fight.revealed_to(bot, hero))

    def test_attached_sonic_rechecks_sniper_faction_during_reposition(self):
        run, hero, bot, special, beacon = self.sonic_sniper_reposition_arena()
        match = run.match
        fight = match.combat
        original_team = bot.team
        original_status = beacon.statuses[bot.entity]
        original_born = beacon.born
        remaining_health = fight.vitals[bot.entity].total

        self.assertEqual(npc_control.start(match, hero, reposition=True), bot.entity)
        move = special.node_state(special.graph.node(16))
        for _ in range(100):
            run.step({})
            if move.active and any(bot.velocity):
                break
        self.assertTrue(move.active)
        self.assertTrue(any(bot.velocity))
        stopped = bot.position
        bot.team = hero.team
        run.step({hero: (*self.target_aim(run, hero, bot), 0)})
        self.assertFalse(move.active)
        self.assertIsNone(bot.reposition_body)
        self.assertFalse(bot.attack_input)
        self.assertEqual(beacon.attached, bot.entity)
        self.assertNotIn(bot.entity, beacon.statuses)
        self.assertTrue(original_status.stopped)
        self.assertFalse(fight.revealed_to(hero, bot))
        with self.assertRaisesRegex(ValueError, "No living manual enemy"):
            npc_control.start(match, hero, reposition=True)
        for _ in range(140):
            run.step({})
        self.assertEqual(bot.position, stopped)
        self.assertFalse(fight.rockets)
        self.assertEqual(fight.vitals[hero.body].total, 200)

        bot.team = original_team
        run.step({})
        self.assertTrue(bot.attack_input)
        self.assertFalse(move.active)
        self.assertIsNone(bot.reposition_body)
        self.assertEqual(fight.sonars, [beacon])
        self.assertEqual(beacon.born, original_born)
        self.assertIsNot(beacon.statuses[bot.entity], original_status)
        self.assertTrue(fight.revealed_to(hero, bot))
        self.assertFalse(fight.revealed_to(bot, hero))
        self.assertEqual(fight.vitals[bot.entity].total, remaining_health)
        for _ in range(240):
            run.step({})
            if fight.rockets:
                break
        shot, = fight.rockets
        self.assertIs(shot.shooter, bot)
        self.assertEqual(bot.position, stopped)
        for _ in range(30):
            run.step({})
            if shot not in fight.rockets:
                break
        self.assertEqual(fight.vitals[hero.body].total, 100)

    def test_sniper_reposition_aborts_when_a_wall_blocks_the_committed_destination(self):
        run, hero, bot, special = self.sniper_reposition_arena()
        match = run.match
        run.step({hero: (*self.target_aim(run, hero, bot), 0)})
        self.assertEqual(npc_control.start(match, hero, reposition=True), bot.entity)
        move = special.node_state(special.graph.node(16))
        for _ in range(100):
            run.step({})
            if move.active and any(bot.velocity):
                break
        destination, _ = move.move_request()
        x, y, z = bot.position
        dx, dz = destination.x - x, destination.z - z
        remaining = math.hypot(dx, dz)
        nx, nz = dx / remaining, dz / remaining
        edge_x, edge_z = x + nx, z + nz
        match.collision_world = World(
            box(x - 50, x + 50, y - 1, y, z - 50, z + 50)
            + wall(edge_x - 5 * nz, edge_z + 5 * nx, edge_x + 5 * nz, edge_z - 5 * nx, y, y + 4)
        )
        hero.mover.use(world=match.collision_world)
        for _ in range(30):
            run.step({})
            if not move.active:
                break
        self.assertFalse(move.active)
        self.assertLess((bot.position[0] - x) * nx + (bot.position[2] - z) * nz, 1)
        self.assertGreater(math.dist(bot.position, destination), 1)
        for _ in range(70):
            run.step({})
        self.assertEqual(special.vars[59].value(), 0)
        self.assertEqual(bot.velocity, (0, 0, 0))

    def test_sniper_reposition_stops_on_manual_stop_faction_change_or_body_replacement(self):
        for cause in ("stop", "faction", "replacement"):
            with self.subTest(cause=cause):
                run, hero, bot, special = self.sniper_reposition_arena()
                fight = run.match.combat
                run.step({hero: (*self.target_aim(run, hero, bot), 0)})
                self.assertEqual(npc_control.start(run.match, hero, reposition=True), bot.entity)
                move = special.node_state(special.graph.node(16))
                for _ in range(100):
                    run.step({})
                    if move.active and any(bot.velocity):
                        break
                self.assertTrue(move.active)
                self.assertTrue(any(bot.velocity))
                stopped = bot.position
                if cause == "stop":
                    self.assertEqual(npc_control.stop(run.match), 1)
                elif cause == "faction":
                    bot.team = hero.team
                else:
                    old_body = hero.body
                    old_position = tuple(hero.mover.position)
                    self.assertTrue(run.match.switch_hero(hero, heroes()[HANZO]))
                    self.assertNotEqual(hero.body, old_body)
                    hero.mover.position[:] = old_position
                    hero.mover.body = None
                for _ in range(130):
                    run.step({})
                self.assertEqual(bot.position, stopped)
                self.assertEqual(bot.velocity, (0, 0, 0))
                self.assertFalse(move.active)
                self.assertIsNone(bot.reposition_body)
                self.assertEqual(special.vars[59].value(), 0)
                if cause != "replacement":
                    self.assertFalse(any(shot.shooter is bot for shot in fight.rockets))
                    target = next(t for t in fight._targets() if t.owner is hero)
                    self.assertEqual(fight.vitals_of(target).total, 200)

    def test_sniper_reposition_retries_blocked_search_after_geometry_opens(self):
        run, hero, bot, special = self.sniper_reposition_arena()
        match = run.match
        x, y, z = bot.position
        match.collision_world = World(
            box(x - 30, x + 30, y - 1, y, z - 35, z + 30)
            + wall(x - 1, z + 1, x + 1, z + 1, y, y + 4)
            + wall(x - 1, z - 1, x - 0.18, z - 1, y, y + 4)
            + wall(x + 0.18, z - 1, x + 1, z - 1, y, y + 4)
            + wall(x - 1, z - 1, x - 1, z + 1, y, y + 4)
            + wall(x + 1, z - 1, x + 1, z + 1, y, y + 4)
        )
        hero.mover.use(world=match.collision_world)
        run.step({hero: (*self.target_aim(run, hero, bot), 0)})
        self.assertEqual(npc_control.start(match, hero, reposition=True), bot.entity)
        search = special.node_state(special.graph.node(39))
        move = special.node_state(special.graph.node(16))
        for _ in range(120):
            run.step({})
            if search.counter:
                break
        self.assertGreater(search.counter, 0)
        for _ in range(10):
            run.step({})
            self.assertFalse(move.active)
            self.assertEqual(bot.position, (x, y, z))
        self.assertTrue(search.active)
        export_component_vm(special.component)
        self.assertIsNone(special.context().var(expr.INSTANCE, 1220))
        match.collision_world = flat_world(y)
        hero.mover.use(world=match.collision_world)
        for _ in range(245):
            run.step({})
            if move.active and any(bot.velocity):
                break
        self.assertTrue(move.active)
        self.assertNotEqual(bot.position, (x, y, z))

    def test_sniper_alternate_weapon_slot_uses_its_own_volley_against_hanzo(self):
        for situation in ("hit", "near_limit", "wall_after_release", "ally", "range", "off_axis"):
            with self.subTest(situation=situation):
                distance = {"near_limit": 99, "range": 110}.get(situation, 16)
                run, hero, bot, _ = self.sniper_reposition_arena(distance)
                match = run.match
                fight = match.combat
                target = next(t for t in fight._targets() if t.owner is hero)
                pool = fight.vitals_of(target)
                component = bot.body_script.component
                manager = next(i for i in component.instances.values() if i.graph.index == 0x0015)
                self.assertEqual(manager.context().var(expr.ENTITY, 30), 1)
                # Drive the authored NPC logical button directly, bypassing autonomous AI.
                component.edges([(16, True)])
                yaw, pitch = self.target_aim(run, hero, bot)
                if situation == "off_axis":
                    yaw += 16384
                run.step({hero: (yaw, pitch, 0)})
                component.edges([(16, False)])
                run.step({hero: (yaw, pitch, 0)})
                self.assertEqual(manager.context().var(expr.ENTITY, 30), 2)

                with patch.object(fight.projectiles, "fired", wraps=fight.projectiles.fired) as launch:
                    if situation in ("ally", "range", "off_axis"):
                        if situation == "ally":
                            bot.team = hero.team
                        with self.assertRaisesRegex(ValueError, "No living manual enemy"):
                            npc_control.start(match, hero)
                        for _ in range(160):
                            run.step({})
                        self.assertFalse(bot.attack_input)
                        self.assertFalse(launch.called)
                        self.assertEqual(pool.total, 200)
                        continue

                    self.assertEqual(npc_control.start(match, hero), bot.entity)
                    for _ in range(180):
                        run.step({})
                        if fight.rockets:
                            break
                    shot, = fight.rockets
                    launch.assert_called_once()
                    source, state, _, definition = launch.call_args.args[:4]
                    self.assertIs(source, bot)
                    self.assertEqual((state.instance.graph.index, state.node.pos), (0x1134, 6))
                    self.assertEqual(definition, 0x04000000000023BB)
                    self.assertEqual((shot.speed, shot.direct.amount), (200, 5))
                    self.assertAlmostEqual(shot.range_left, 100)
                    self.assertEqual(shot.source_entity, bot.entity)
                    if situation == "wall_after_release":
                        x, y, z = hero.mover.position
                        match.collision_world = World(
                            box(x - 30, x + 30, y - 1, y, z - 30, z + 30)
                            + wall(x - 5, z + 5, x + 5, z + 5, y, y + 5)
                        )
                        hero.mover.use(world=match.collision_world)
                    for _ in range(40):
                        run.step({})
                        if shot not in fight.rockets:
                            break
                    self.assertNotIn(shot, fight.rockets)
                    self.assertEqual(pool.total, 200 if situation == "wall_after_release" else 195)
                    self.assertEqual(launch.call_count, 2 if situation == "near_limit" else 1)
                    if situation == "near_limit":
                        following, = fight.rockets
                        self.assertIs(following.shooter, bot)
                        self.assertEqual(launch.call_args.args[3], definition)

    def test_sniper_weapon_switch_cancels_windup_but_preserves_committed_round(self):
        graphs = {1: 0x1122, 2: 0x1134}
        primary, alternate = SNIPER_WEAPONS
        for (old_weapon, new_weapon), committed in product(((primary, alternate), (alternate, primary)), (False, True)):
            old_slot, old_definition, old_damage = old_weapon
            new_slot, new_definition, new_damage = new_weapon
            with self.subTest(old_slot=old_slot, committed=committed):
                run, hero, bot, _ = self.sniper_reposition_arena(32)
                match = run.match
                fight = match.combat
                target = next(t for t in fight._targets() if t.owner is hero)
                pool = fight.vitals_of(target)
                self.aim_sniper_weapon(run, hero, bot, old_slot)
                self.assertEqual(npc_control.start(match, hero), bot.entity)
                with patch.object(fight.projectiles, "fired", wraps=fight.projectiles.fired) as launch:
                    for _ in range(180 if committed else 8):
                        run.step({})
                        if fight.rockets:
                            break
                    self.assertTrue(bot.attack_input)
                    self.assertEqual(pool.total, 200)
                    if committed:
                        old_shot, = fight.rockets
                        self.assertEqual((old_shot.source_entity, old_shot.direct.amount), (bot.entity, old_damage))
                        self.assertEqual(launch.call_args.args[3], old_definition)
                    else:
                        self.assertFalse(fight.rockets)
                        self.assertFalse(launch.called)
                        old_weapon = next(i for i in bot.body_script.component.instances.values()
                                          if i.graph.index == graphs[old_slot])
                        self.assertTrue(old_weapon.node_state(old_weapon.graph.node(4)).down)

                    self.aim_sniper_weapon(run, hero, bot, new_slot)
                    if committed:
                        self.assertIn(old_shot, fight.rockets)
                    for _ in range(180):
                        if launch.call_count > int(committed):
                            break
                        run.step({})
                    self.assertEqual(launch.call_count, 1 + int(committed))
                    definitions = [call.args[3] for call in launch.call_args_list]
                    self.assertEqual(definitions, ([old_definition] if committed else []) + [new_definition])
                    source, state = launch.call_args.args[:2]
                    self.assertIs(source, bot)
                    self.assertEqual((state.instance.graph.index, state.node.pos), (graphs[new_slot], 6))
                    new_shot = fight.rockets[-1]
                    self.assertEqual((new_shot.source_entity, new_shot.direct.amount), (bot.entity, new_damage))
                    bot.firing = False  # release further input without clearing the committed shots
                    for _ in range(25):
                        if not fight.rockets:
                            break
                        run.step({})
                    self.assertFalse(fight.rockets)
                    self.assertEqual(pool.total, 200 - new_damage - (old_damage if committed else 0))
                    self.assertEqual(launch.call_count, 1 + int(committed))

    def test_sniper_alternate_cooldown_survives_early_release_and_repress(self):
        for repress in (False, True):
            with self.subTest(repress=repress):
                run, hero, bot, _ = self.sniper_reposition_arena()
                fight = run.match.combat
                target = next(t for t in fight._targets() if t.owner is hero)
                pool = fight.vitals_of(target)
                self.aim_sniper_weapon(run, hero, bot, 2)
                component = bot.body_script.component
                weapon = next(i for i in component.instances.values() if i.graph.index == 0x1134)
                authored = weapon.graph.node(5).field("m_value")
                self.assertEqual((authored["m_min"], authored["m_max"]), (0.25, 0.35))
                self.assertEqual(npc_control.start(run.match, hero), bot.entity)
                fire_frames = []
                cooldowns = []
                with patch.object(fight.projectiles, "fired", wraps=fight.projectiles.fired) as launch:
                    for _ in range(250):
                        run.step({})
                        if launch.call_count == len(fire_frames):
                            continue
                        self.assertEqual(launch.call_count, len(fire_frames) + 1)
                        source, state, frame, definition = launch.call_args.args[:4]
                        self.assertIs(source, bot)
                        self.assertIs(state.instance, weapon)
                        self.assertEqual(definition, 0x04000000000023BB)
                        fire_frames.append(frame)
                        cooldowns.append(weapon.vars[173].value())
                        if repress and len(fire_frames) == 1:
                            bot.firing = False
                            run.step({})
                            self.assertFalse(bot.attack_input)
                            self.assertEqual(npc_control.start(run.match, hero), bot.entity)
                            run.step({})
                            self.assertTrue(bot.attack_input)
                            self.assertEqual(launch.call_count, 1)
                        if len(fire_frames) == 5:
                            break
                    self.assertEqual(len(fire_frames), 5)
                    self.assertTrue(all(authored["m_min"] <= value <= authored["m_max"] for value in cooldowns))
                    observed = [end - start for start, end in zip(fire_frames, fire_frames[1:])]
                    expected = [math.ceil(1000 * value / component.frame_ms) for value in cooldowns[:-1]]
                    self.assertEqual(observed, expected)
                    bot.firing = False
                    for _ in range(25):
                        if not fight.rockets:
                            break
                        run.step({})
                    self.assertFalse(fight.rockets)
                    self.assertEqual(launch.call_count, 5)
                    self.assertEqual(pool.total, 175)

    def test_lunge_can_evade_an_already_launched_talon_sniper_round(self):
        for reposition, evade in product((False, True), repeat=2):
            with self.subTest(reposition=reposition, evade=evade):
                if reposition:
                    run, hero, bot, special = self.sniper_reposition_arena(distance=22)
                else:
                    run, hero, bot = self.arena(self.catalog["3"], "bow", "hit", distance=20)
                    self.addCleanup(npc.remove_spawned, run.match)
                fight = run.match.combat
                yaw, pitch = self.target_aim(run, hero, bot)
                run.step({hero: (yaw, pitch, 0)})
                start = bot.position
                self.assertEqual(npc_control.start(run.match, hero, reposition=reposition), bot.entity)
                target = next(t for t in fight._targets() if t.owner is hero)
                pool = fight.vitals_of(target)
                with patch.object(fight, "_shoot", wraps=fight._shoot) as shoot:
                    # Both fixtures jump near the end of their measured rifle windup.
                    for _ in range(210 if reposition else 110):
                        run.step({})
                    if reposition:
                        self.assertEqual(special.vars[59].value(), 0)
                        self.assertAlmostEqual(math.dist(start, bot.position), 2.5, delta=0.01)
                    run.step({hero: (0, 0, JUMP)})
                    for _ in range(20):
                        run.step({})
                        if fight.rockets:
                            break
                    round_, = fight.rockets
                    state = shoot.call_args.args[2]
                    self.assertEqual((state.instance.graph.index, state.node.pos), (0x1122, 6))
                    self.assertIs(round_.shooter, bot)
                    self.assertEqual(round_.speed, 200)
                    self.assertEqual(pool.total, 200)
                    origin = tuple(hero.mover.position)
                    run.step({hero: (16384, 0, JUMP if evade else 0)})
                    if evade:
                        self.assertGreater(hero.mover.velocity[0], 14)
                    for _ in range(25):
                        if round_ not in fight.rockets:
                            break
                        run.step({})
                    self.assertEqual(shoot.call_count, 1)
                    self.assertEqual(pool.total, 200 if evade else 100)
                    if evade:
                        self.assertGreater(hero.mover.position[0] - origin[0], 0.7)
                    else:
                        self.assertNotIn(round_, fight.rockets)

    def test_sniper_reposition_stop_keeps_authored_cooldown_until_restart(self):
        run, hero, bot, special = self.sniper_reposition_arena()

        def engage():
            run.step({hero: (*self.target_aim(run, hero, bot), 0)})
            self.assertEqual(npc_control.start(run.match, hero, reposition=True), bot.entity)

        engage()
        move = special.node_state(special.graph.node(16))
        cooldown = special.node_state(special.graph.node(73))
        for _ in range(100):
            run.step({})
            if move.active and any(bot.velocity):
                break
        self.assertTrue(move.active)
        stopped = bot.position
        self.assertEqual(npc_control.stop(run.match), 1)
        for _ in range(130):
            run.step({})
            if cooldown.active:
                break
        self.assertTrue(cooldown.active)
        engage()
        for _ in range(35):
            run.step({})
            self.assertTrue(cooldown.active)
            self.assertFalse(move.active)
            self.assertEqual(bot.position, stopped)
        self.assertEqual(npc_control.stop(run.match), 1)
        for _ in range(750):
            if not cooldown.active:
                break
            run.step({})
            self.assertFalse(move.active)
            self.assertEqual(bot.position, stopped)
        self.assertFalse(cooldown.active)
        engage()
        for _ in range(125):
            run.step({})
            if move.active and bot.position != stopped:
                break
        self.assertTrue(move.active)
        self.assertNotEqual(bot.position, stopped)

    def test_lunge_can_evade_a_committed_talon_enforcer_volley(self):
        def armed_scene():
            run, hero, bot = self.arena(self.catalog["2"], "bow", "hit", distance=12)
            self.addCleanup(npc.remove_spawned, run.match)
            run.step({hero: (*self.target_aim(run, hero, bot), 0)})
            self.assertEqual(npc_control.start(run.match, hero), bot.entity)
            return run, hero, bot

        probe, _, _ = armed_scene()
        for delay in range(1, 151):
            probe.step({})
            if probe.match.combat.rockets:
                break
        self.assertEqual(len(probe.match.combat.rockets), 25)
        self.assertGreater(delay, 16)
        for evade in (False, True):
            with self.subTest(evade=evade):
                run, hero, bot = armed_scene()
                fight = run.match.combat
                pool = fight.vitals_of(next(t for t in fight._targets() if t.owner is hero))
                for frame in range(1, delay + 3):
                    run.step({hero: (0, 0, JUMP if frame == delay - 16 else 0)})
                    if fight.rockets:
                        break
                volley = list(fight.rockets)
                self.assertEqual(len(volley), 25)
                self.assertTrue(all(shot.shooter is bot and shot.direct.amount == 2 and shot.speed == 80
                                    for shot in volley))
                self.assertEqual(pool.total, 200)
                self.assertTrue(hero.mover.airborne)
                bot.firing = False
                x = hero.mover.position[0]
                run.step({hero: (16384, 0, JUMP if evade else 0)})
                if evade:
                    self.assertAlmostEqual(hero.mover.velocity[0], 15)
                for _ in range(85):
                    run.step({})
                    if not fight.rockets:
                        break
                self.assertFalse(fight.rockets)
                if evade:
                    self.assertGreater(hero.mover.position[0], x + 1)
                    self.assertEqual(pool.total, 200)
                else:
                    self.assertAlmostEqual(hero.mover.position[0], x)
                    self.assertLess(pool.total, 200)

    def test_wall_climb_can_evade_an_already_launched_talon_sniper_round(self):
        from test_game_hanzo_movement import HanzoLungeTests

        for obstacle in ("floor", "wall", "ledge"):
            with self.subTest(obstacle=obstacle):
                run, hero, bot = self.arena(self.catalog["3"], "bow", "hit", distance=75)
                self.addCleanup(npc.remove_spawned, run.match)
                fight = run.match.combat
                x, y, z = hero.mover.position
                triangles = box(x - 20, x + 20, y - 1, y, z - 20, z + 90)
                if obstacle == "wall":
                    triangles += wall(x - 5, z - 0.7, x + 5, z - 0.7, y0=y, y1=y + 20)
                elif obstacle == "ledge":
                    triangles += box(x - 5, x + 5, y, y + 3, z - 8, z - 0.7)
                run.match.collision_world = World(triangles)
                hero.mover.use(world=run.match.collision_world)
                yaw, pitch = self.target_aim(run, hero, bot)
                run.step({hero: (yaw, pitch, 0)})
                self.assertEqual(npc_control.start(run.match, hero), bot.entity)
                target = next(t for t in fight._targets() if t.owner is hero)
                pool = fight.vitals_of(target)
                for _ in range(100):
                    run.step({})
                for _ in range(40):
                    run.step({})
                    if fight.rockets:
                        break
                shot, = fight.rockets
                self.assertIs(shot.shooter, bot)
                self.assertEqual(shot.speed, 200)
                self.assertEqual(pool.total, 200)
                progress = hero.body_script.world.hanzo_climb
                climbed = False
                for _ in range(45):
                    if shot not in fight.rockets:
                        break
                    if obstacle == "ledge":
                        HanzoLungeTests.step(run, hero, buttons=JUMP, yaw=-32768, forward=127)
                    else:
                        run.step({hero: (-32768 if obstacle == "wall" else 0, 0, JUMP)})
                    climbed |= progress.active
                self.assertNotIn(shot, fight.rockets)
                self.assertEqual(climbed, obstacle != "floor")
                self.assertEqual(pool.total, 100 if obstacle == "floor" else 200)
                if obstacle == "wall":
                    self.assertTrue(progress.active)
                    self.assertGreater(hero.mover.position[1] - y, 1.3)
                    self.assertAlmostEqual(hero.mover.position[0], x, places=4)
                elif obstacle == "ledge":
                    self.assertIsNotNone(progress.mantle)
                    self.assertGreater(progress.elapsed, 0)
                    self.assertEqual(npc_control.stop(run.match), 1)
                    for _ in range(45):
                        HanzoLungeTests.step(run, hero, buttons=JUMP, yaw=-32768, forward=127)
                        if progress.mantle is None:
                            break
                    self.assertIsNone(progress.mantle)
                    self.assertFalse(hero.mover.airborne)
                    self.assertAlmostEqual(hero.mover.position[1], y + 3, delta=0.01)
                    self.assertLess(hero.mover.position[2], z - 1)
                    self.assertEqual(pool.total, 200)

    def test_tracking_talon_hitscan_hits_active_lunge_and_only_lethal_contact_stops_it(self):
        def scene(key):
            run, hero, bot = self.arena(self.catalog[key], "bow", "hit", distance=5)
            self.addCleanup(npc.remove_spawned, run.match)
            return run, hero, bot

        for key, damage in (("1", 20), ("5", 10)):
            probe, hero, bot = scene(key)
            probe.step({hero: (*self.target_aim(probe, hero, bot), 0)})
            self.assertEqual(npc_control.start(probe.match, hero), bot.entity)
            probe_fight = probe.match.combat
            with patch.object(probe_fight, "_hitscan", wraps=probe_fight._hitscan) as fired:
                for delay in range(1, 151):
                    probe.step({})
                    if fired.called:
                        break
            self.assertTrue(fired.called)
            engage = max(1, 24 - delay)
            impact = engage + delay
            lunge = impact - 7
            if lunge == engage:
                lunge += 1  # keep the target-selection frame aimed at the NPC
            for lethal in (False, True):
                with self.subTest(npc=self.catalog[key].name, lethal=lethal):
                    run, hero, bot = scene(key)
                    fight = run.match.combat
                    body = hero.body
                    pool = fight.vitals_of(next(t for t in fight._targets() if t.owner is hero))
                    if lethal:
                        pool.damage(199)
                    x = hero.mover.position[0]
                    movement = next(i for i in hero.body_script.component.instances.values()
                                    if i.graph.index == 0x010C)
                    with patch.object(fight, "_hitscan", wraps=fight._hitscan) as fired:
                        for frame in range(1, impact + 3):
                            before = tuple(hero.mover.position)
                            angles = (16384, 0) if frame == lunge else self.target_aim(run, hero, bot)
                            button = JUMP if frame in (impact - 23, lunge) else 0
                            run.step({hero: (*angles, button)})
                            if frame == engage:
                                self.assertEqual(npc_control.start(run.match, hero), bot.entity)
                            if frame == lunge:
                                self.assertAlmostEqual(hero.mover.velocity[0], 15)
                            if fired.called:
                                break
                    self.assertEqual(frame, impact)
                    self.assertEqual(fired.call_count, 1 if key == "1" else 2)
                    self.assertTrue(all(call.args[0] is bot for call in fired.call_args_list))
                    self.assertGreater(before[0], x + 1)
                    self.assertGreater(movement.vars[61].value(), 0)
                    bot.firing = False
                    if lethal:
                        self.assertTrue(pool.dead)
                        self.assertEqual(pool.killer, bot.entity)
                        self.assertEqual(tuple(hero.mover.position), before)
                        for _ in range(5):
                            run.step({hero: (16384, 0, JUMP)})
                            self.assertEqual(tuple(hero.mover.position), before)
                        self.assertEqual(hero.body, body)
                    else:
                        self.assertEqual(pool.total, 200 - damage)
                        self.assertFalse(fight.is_stunned(hero))
                        self.assertGreater(hero.mover.position[0], before[0])
                        for _ in range(5):
                            run.step({})
                        self.assertGreater(hero.mover.position[0], before[0] + 0.5)

    def test_talon_projectile_and_assassin_lethal_contact_retires_committed_lunge(self):
        from test_game_hanzo_movement import HanzoLungeTests

        lunge = HanzoLungeTests()
        for key in ("2", "3", "4"):
            with self.subTest(npc=self.catalog[key].name):
                assassin = key == "4"
                distance = 2.5 if assassin else 5
                if not assassin:
                    probe, actor, npc_bot = self.arena(self.catalog[key], "bow", "hit", distance=distance)
                    self.addCleanup(npc.remove_spawned, probe.match)
                    probe.step({actor: (*self.target_aim(probe, actor, npc_bot), 0)})
                    self.assertEqual(npc_control.start(probe.match, actor), npc_bot.entity)
                    for delay in range(1, 170):
                        probe.step({})
                        if probe.match.combat.rockets:
                            break
                    self.assertTrue(probe.match.combat.rockets)
                    self.assertGreater(delay, 20)

                run, hero, bot = self.arena(self.catalog[key], "bow", "hit", distance=distance)
                self.addCleanup(npc.remove_spawned, run.match)
                fight = run.match.combat
                old_body, script = hero.body, hero.body_script
                origin = tuple(hero.mover.position)
                pool = fight.vitals_of(next(t for t in fight._targets() if t.owner is hero))
                pool.damage(199)

                if assassin:
                    lunge.arm(run, hero)
                    run.step({hero: (*self.target_aim(run, hero, bot), JUMP)})
                    self.assertAlmostEqual(hero.mover.velocity[2], 15)
                    run.step({hero: (*self.target_aim(run, hero, bot), 0)})
                    self.assertEqual(npc_control.start(run.match, hero), bot.entity)
                else:
                    run.step({hero: (*self.target_aim(run, hero, bot), 0)})
                    self.assertEqual(npc_control.start(run.match, hero), bot.entity)
                    for frame in range(1, delay - 3):
                        run.step({hero: (0, 0, JUMP if frame in (delay - 20, delay - 4) else 0)})
                    self.assertFalse(pool.dead)
                    self.assertAlmostEqual(hero.mover.velocity[2], 15)
                    self.assertFalse(fight.rockets)

                shot = None
                for _ in range(30):
                    before = tuple(hero.mover.position)
                    velocity = hero.mover.velocity
                    run.step({hero: (0, 0, JUMP)})
                    if not assassin and shot is None and fight.rockets:
                        shot = fight.rockets[0]
                        self.assertIs(shot.shooter, bot)
                        self.assertEqual(shot.speed, 80 if key == "2" else 200)
                    if pool.dead:
                        break
                self.assertTrue(pool.dead)
                self.assertEqual(pool.killer, bot.entity)
                self.assertGreater(velocity[2], 12)
                self.assertGreater(before[2], origin[2])
                self.assertEqual(tuple(hero.mover.position), before)
                self.assertNotIn(old_body, fight.pins.values())
                if not assassin:
                    self.assertIsNotNone(shot)
                self.assertEqual(npc_control.stop(run.match), 1)
                for _ in range(5):
                    run.step({hero: (0, 0, JUMP)})
                    self.assertEqual(tuple(hero.mover.position), before)

                limit = round(combat.respawn_seconds(run.match) / combat.TICK) + 120
                for _ in range(limit):
                    if hero.body != old_body:
                        break
                    run.step({})
                self.assertNotEqual(hero.body, old_body)
                self.assertTrue(script.retired)
                hero.mover.position[:] = origin
                hero.mover.body = None
                run.step({})
                fresh = next(i for i in hero.body_script.component.instances.values() if i.graph.index == 0x010C)
                self.assertEqual(fresh.vars[61].value(), 0)
                self.assertEqual(hero.mover.velocity[0::2], (0, 0))
                lunge.arm(run, hero)
                run.step({hero: (16384, 0, JUMP)})
                self.assertAlmostEqual(hero.mover.velocity[0], 15)


    def test_actual_talon_primary_hits_do_not_interrupt_wall_climb(self):
        for key, expected in (("1", 20), ("2", 50), ("5", 10)):
            with self.subTest(npc=self.catalog[key].name):
                run, hero, bot = self.arena(self.catalog[key], "bow", "hit", distance=5)
                match = run.match
                self.addCleanup(npc.remove_spawned, match)
                fight = match.combat
                x, y, z = hero.mover.position
                match.collision_world = World(
                    box(x - 20, x + 20, y - 1, y, z - 20, z + 20)
                    + wall(x - 5, z - 0.7, x + 5, z - 0.7, y, y + 12)
                )
                hero.mover.use(world=match.collision_world)
                pool = fight.vitals_of(next(t for t in fight._targets() if t.owner is hero))
                run.step({hero: (*self.target_aim(run, hero, bot), 0)})
                self.assertEqual(npc_control.start(match, hero), bot.entity)
                with patch.object(fight, "_shoot", wraps=fight._shoot) as fired:
                    for _ in range(80):
                        run.step({hero: (-32768, 0, JUMP)})
                        if fired.called:
                            break
                call, = fired.call_args_list
                self.assertIs(call.args[0], bot)
                bot.firing = False  # retain an already committed Enforcer volley
                climb = hero.body_script.world.hanzo_climb
                self.assertTrue(climb.active)
                height = hero.mover.position[1]
                if key == "2":
                    self.assertEqual(len(fight.rockets), 25)
                    self.assertEqual(pool.total, 200)
                else:
                    self.assertFalse(fight.rockets)
                for _ in range(12):
                    run.step({hero: (-32768, 0, JUMP)})
                self.assertFalse(fight.rockets)
                self.assertEqual(pool.total, 200 - expected)
                self.assertFalse(fight.is_stunned(hero))
                self.assertTrue(climb.active)
                self.assertTrue(hero.body_script.component.entity_var(1204).value())
                self.assertGreater(hero.mover.position[1], height + 0.5)

    def test_all_five_talon_attacks_hit_an_active_mantle(self):
        from test_game_hanzo_movement import HanzoLungeTests

        # Start a few frames before the measured first contact in this arena.
        # Assassin has immediate pin entry, so start its controller during ascent.
        timings = {"1": (15, 20), "2": (60, 50), "3": (121, 100), "4": (0, 25), "5": (25, 10)}
        for key, lethal in product(self.catalog, (False, True)):
            definition = self.catalog[key]
            start, damage = timings[key]
            assassin = key == "4"
            with self.subTest(npc=definition.name, lethal=lethal):
                run, hero, bot = self.arena(definition, "bow", "hit", distance=2.5 if assassin else 5)
                match = run.match
                self.addCleanup(npc.remove_spawned, match)
                fight = match.combat
                x, y, z = hero.mover.position
                match.collision_world = World(
                    box(x - 20, x + 20, y - 1, y, z - 20, z + 20)
                    + box(x - 5, x + 5, y, y + 1.9, z - 8, z - 0.7),
                )
                hero.mover.use(world=match.collision_world)
                pool = fight.vitals_of(next(t for t in fight._targets() if t.owner is hero))
                if lethal:
                    pool.damage(199)
                before = pool.total
                progress = hero.body_script.world.hanzo_climb

                def mantle_step():
                    HanzoLungeTests.step(run, hero, buttons=JUMP, yaw=-32768, forward=127)

                if assassin:
                    mantle_step()
                    mantle_step()
                run.step({hero: (*self.target_aim(run, hero, bot), JUMP if assassin else 0)})
                self.assertEqual(npc_control.start(match, hero), bot.entity)
                for frame in range(160):
                    route_at_contact = progress.mantle
                    position_at_contact = tuple(hero.mover.position)
                    if frame >= start:
                        mantle_step()
                    else:
                        run.step({})
                    if pool.total < before:
                        break
                self.assertIsNotNone(route_at_contact)
                self.assertEqual(pool.total, 0 if lethal else 200 - damage)
                self.assertEqual(pool.dead, lethal)
                self.assertEqual(fight.is_stunned(hero), assassin and not lethal)
                if lethal:
                    self.assertEqual(pool.killer, bot.entity)
                    self.assertEqual(tuple(hero.mover.position), position_at_contact)
                    self.assertNotIn(hero.body, fight.pins.values())
                elif assassin:
                    self.assertEqual(fight.pins.get(bot.entity), hero.body)
                self.assertEqual(progress.mantle is None, lethal or assassin)
                self.assertEqual(npc_control.stop(match), 1)

                if lethal or assassin:
                    for _ in range(5):
                        run.step({hero: (-32768, 0, JUMP)})
                        self.assertIsNone(progress.mantle)
                        self.assertGreater(hero.mover.position[2], z - 0.7)
                else:
                    for _ in range(48):
                        mantle_step()
                    self.assertIsNone(progress.mantle)
                    self.assertFalse(hero.mover.airborne)
                    self.assertAlmostEqual(hero.mover.position[1], y + 1.9, delta=0.01)
                    self.assertLess(hero.mover.position[2], z - 1)
                    self.assertEqual(pool.total, 200 - damage)

    def test_hanzo_arrows_fire_during_mantle_against_all_five_talon_bodies(self):
        from test_game_hanzo_movement import HanzoLungeTests
        from test_game_hanzo_sonic import profile

        sonic_profile = profile()
        for height, definition, use_sonic, storm in product(
            (1.9, 3), self.catalog.values(), (False, True), (False, True),
        ):
            high = height == 3
            with self.subTest(height=height, npc=definition.name, sonic=use_sonic, storm=storm):
                run, hero, bot = self.arena(definition, "bow", "hit", distance=8 if high else 6)
                match = run.match
                self.addCleanup(npc.remove_spawned, match)
                fight = match.combat
                x, y, z = hero.mover.position
                match.collision_world = World(
                    box(x - 20, x + 20, y - 1, y, z - 20, z + 20)
                    + box(x - 5, x + 5, y, y + height, z + 0.7, z + 2.5),
                )
                hero.mover.use(world=match.collision_world)
                if use_sonic:
                    fight.configure_sonic(sonic_profile)
                ability = (8 if use_sonic else 0) | (16 if storm else 0)
                if ability:
                    run.step({hero: (0, 0, ability)})
                    run.step({})
                pool = fight.vitals[bot.entity]
                before = pool.total
                bx, by, bz = bot.position
                contact = bx, by + 0.7, bz
                if storm:
                    for _ in range(29 if high else 2):
                        HanzoLungeTests.step(run, hero, buttons=JUMP, forward=127)
                    for _ in range(25):
                        yaw, pitch = aim(fight._eye(hero), contact)
                        HanzoLungeTests.step(run, hero, buttons=JUMP | 1, forward=127, yaw=yaw, pitch=pitch)
                        if fight.rockets:
                            break
                else:
                    if high:
                        for _ in range(13):
                            HanzoLungeTests.step(run, hero, buttons=JUMP, forward=127)
                        self.assertIsNotNone(hero.body_script.world.hanzo_climb.mantle)
                        for _ in range(18):
                            HanzoLungeTests.step(run, hero, buttons=JUMP | 1, forward=127)
                    else:
                        for _ in range(70):
                            run.step({hero: (0, 0, 1)})
                        for _ in range(21):
                            HanzoLungeTests.step(run, hero, buttons=JUMP | 1, forward=127)
                    angles = aim(fight._eye(hero), contact)
                    run.step({hero: (*angles, JUMP)})
                progress = hero.body_script.world.hanzo_climb
                self.assertEqual(progress.elapsed > 0, high)
                arrow, = fight.rockets
                if use_sonic:
                    definition_id = 0x020D if high and not storm else 0x0149
                elif storm:
                    definition_id = 0x270F
                else:
                    definition_id = 0x020B if high else 0x0144
                self.assertIsNotNone(arrow.shot)
                self.assertEqual(arrow.shot.definition, 0x0400000000000000 | definition_id)
                expected_damage = 70 if storm else (64.484 if high else 125)
                damage = arrow.direct.amount
                self.assertAlmostEqual(damage, expected_damage, delta=0.01)
                self.assertEqual(arrow.storm_ricochets, int(storm))
                angles = aim(fight._eye(hero), contact)
                self.assertGreater(arrow.position[1], y + height + 1.1)
                self.assertIsNotNone(progress.mantle)
                for _ in range(35):
                    run.step({hero: (*angles, JUMP)})
                self.assertIsNone(progress.mantle)
                self.assertFalse(hero.mover.airborne)
                self.assertAlmostEqual(hero.mover.position[1], y + height, delta=0.01)
                self.assertAlmostEqual(pool.total, before - damage, delta=0.01)
                hit, = hero.client.messages(combat.HIT)
                self.assertEqual(hit["+0x78"], {"+0x0": {"+0x0": bot.entity}, "+0x4": round(damage), "+0x8": 0})
                if use_sonic:
                    beacon, = fight.sonars
                    self.assertEqual(beacon.attached, bot.entity)
                    self.assertTrue(fight.revealed_to(hero, bot))
                else:
                    self.assertFalse(fight.sonars)

    def test_mantle_melee_and_dragonstrike_contact_all_five_talon_bodies(self):
        from test_game_hanzo_dragons import prepared_profile
        from test_game_hanzo_movement import HanzoLungeTests
        from ow174.game import hanzo_control

        dragon_profile = prepared_profile()
        for definition, ultimate in product(self.catalog.values(), (False, True)):
            with self.subTest(npc=definition.name, ultimate=ultimate):
                distance = 4 if ultimate else 2.75
                run, hero, bot = self.arena(definition, "bow", "hit", distance=distance)
                match = run.match
                self.addCleanup(npc.remove_spawned, match)
                fight = match.combat
                x, y, z = hero.mover.position
                match.collision_world = World(
                    box(x - 20, x + 20, y - 1, y, z - 20, z + 20)
                    + box(x - 5, x + 5, y, y + 1.9, z + 0.7, z + 8),
                )
                hero.mover.use(world=match.collision_world)
                bot.position = (x, y + 1.9, z + distance)
                body = hero.body_script
                if ultimate:
                    fight.configure_dragonstrike(dragon_profile)
                    body.set_ult_charge(1680)
                pool = fight.vitals[bot.entity]
                before = pool.total
                for _ in range(8 if ultimate else 21):
                    HanzoLungeTests.step(run, hero, buttons=JUMP, forward=127)
                progress = body.world.hanzo_climb
                self.assertIsNotNone(progress.mantle)
                point = (x, y + 2.8, z + distance)
                angles = aim(fight._eye(hero), point)
                run.step({hero: (*angles, JUMP | (32 if ultimate else 0x800))})
                self.assertIsNotNone(progress.mantle)
                if ultimate:
                    self.assertTrue(hanzo_control.casting(body))
                    self.assertEqual(body.component.entity_var(combat.ULT_CHARGE).value(), 0)
                    for _ in range(90):
                        angles = aim(fight._eye(hero), point)
                        run.step({hero: (*angles, JUMP if progress.mantle is not None else 0)})
                        if fight.rockets:
                            break
                    arrow, = fight.rockets
                    self.assertGreater(arrow.position[2], z + 1)
                    self.assertEqual(arrow.source_entity, hero.body)
                    for _ in range(60):
                        run.step({})
                        if fight.dragons:
                            break
                    flight, = fight.dragons
                    self.assertEqual(flight.source_entity, hero.body)
                else:
                    self.assertEqual(pool.total, before - 30)
                    for _ in range(35):
                        run.step({hero: (*angles, JUMP | 0x800)})
                self.assertIsNone(progress.mantle)
                self.assertFalse(hero.mover.airborne)
                self.assertAlmostEqual(hero.mover.position[1], y + 1.9, delta=0.01)
                damage = 125 if ultimate else 30
                self.assertEqual(pool.total, before - damage)
                hit, = hero.client.messages(combat.HIT)
                self.assertEqual(hit["+0x78"], {"+0x0": {"+0x0": bot.entity}, "+0x4": damage, "+0x8": 0})

    def test_all_five_talon_lethal_attacks_retire_active_wall_climb(self):
        for key, definition in self.catalog.items():
            with self.subTest(npc=definition.name):
                assassin = key == "4"
                run, hero, bot = self.arena(definition, "bow", "hit", distance=2.5 if assassin else 5)
                match = run.match
                self.addCleanup(npc.remove_spawned, match)
                fight = match.combat
                old_body, script = hero.body, hero.body_script
                origin = tuple(hero.mover.position)
                x, y, z = origin
                # The low front wall gives Assassin an open eye line on the
                # same heading as the chest probe, before immediate pin entry.
                wall_z = z + 0.7 if assassin else z - 0.7
                wall_height = 1.27 if assassin else 12
                climb_yaw = 0 if assassin else -32768
                match.collision_world = World(
                    box(x - 20, x + 20, y - 1, y, z - 20, z + 20)
                    + wall(x - 5, wall_z, x + 5, wall_z, y, y + wall_height),
                )
                hero.mover.use(world=match.collision_world)
                pool = fight.vitals_of(next(t for t in fight._targets() if t.owner is hero))
                pool.damage(199)
                if assassin:
                    for _ in range(2):
                        angles = aim(fight._eye(hero), (x, y + 1.4, z + 2.5))
                        run.step({hero: (*angles, JUMP)})
                    self.assertTrue(script.world.hanzo_climb.active)
                else:
                    run.step({hero: (*self.target_aim(run, hero, bot), 0)})
                self.assertEqual(npc_control.start(match, hero), bot.entity)
                for frame in range(180):
                    climb = script.world.hanzo_climb
                    was_climbing = climb is not None and climb.active
                    before = tuple(hero.mover.position)
                    # Let the Sniper wind up while grounded, then climb while
                    # it still tracks so its live projectile can hit.
                    button = JUMP if key != "3" or frame >= 112 else 0
                    run.step({hero: (climb_yaw, 0, button)})
                    if pool.dead:
                        break

                self.assertTrue(pool.dead)
                self.assertEqual(pool.killer, bot.entity)
                self.assertTrue(was_climbing)
                self.assertFalse(climb.active)
                self.assertFalse(script.component.entity_var(1204).value())
                self.assertEqual(tuple(hero.mover.position), before)
                self.assertNotIn(old_body, fight.pins.values())
                self.assertEqual(npc_control.stop(match), 1)
                for _ in range(5):
                    run.step({hero: (climb_yaw, 0, JUMP)})
                    self.assertFalse(climb.active)
                    self.assertEqual(tuple(hero.mover.position), before)

                limit = round(combat.respawn_seconds(match) / combat.TICK) + 120
                for _ in range(limit):
                    if hero.body != old_body:
                        break
                    run.step({})
                self.assertNotEqual(hero.body, old_body)
                self.assertTrue(script.retired)
                hero.mover.position[:] = origin
                hero.mover.body = None
                run.step({hero: (climb_yaw, 0, 0)})
                fresh = hero.body_script.world.hanzo_climb
                self.assertIsNot(fresh, climb)
                self.assertFalse(fresh.active)
                self.assertEqual(fresh.elapsed, 0)
                run.step({hero: (climb_yaw, 0, JUMP)})
                run.step({hero: (climb_yaw, 0, JUMP)})
                self.assertTrue(fresh.active)
                self.assertGreater(hero.mover.position[1], y)


    def test_storm_arrows_kill_sniper_before_windup_can_fire(self):
        run, hero, bot = self.arena(self.catalog["3"], "bow", "hit")
        self.addCleanup(npc.remove_spawned, run.match)
        fight = run.match.combat
        yaw, pitch = self.target_aim(run, hero, bot)
        run.step({hero: (yaw, pitch, 0)})
        self.assertEqual(npc_control.start(run.match, hero), bot.entity)
        target = next(t for t in fight._targets() if t.owner is hero)
        pool = fight.vitals_of(target)
        started = run.frame
        with patch.object(fight, "_shoot", wraps=fight._shoot) as shoot:
            for _ in range(10):
                run.step({hero: (yaw, pitch, 0)})
            self.assertTrue(bot.attack_input)
            self.assertFalse(shoot.called)
            run.step({hero: (yaw, pitch, 16)})
            run.step({hero: (yaw, pitch, 0)})
            for _ in range(110):
                if bot.dead:
                    break
                run.step({hero: (yaw, pitch, 1)})
            self.assertTrue(bot.dead)
            self.assertLess(run.frame - started, 126)  # baseline first shot in this fixture
            self.assertTrue(bot.body_script.component.owner_dead)
            self.assertEqual(fight.vitals[bot.entity].killer, hero.body)
            hits = hero.client.messages(combat.HIT)
            self.assertEqual([hit["+0x78"]["+0x4"] for hit in hits], [70, 70, 70, 70, 20])
            self.assertTrue(hits[-1]["+0x78"]["+0x8"] & combat.KILLING)
            self.assertEqual([entity for entity, _ in hero.client.npc_deaths], [bot.entity])
            for _ in range(160):
                run.step({hero: (yaw, pitch, 0)})
            self.assertFalse(any(call.args[0] is bot for call in shoot.call_args_list))
            self.assertFalse(bot.attack_input)
            self.assertEqual(pool.total, 200)
            with self.assertRaises(ValueError):
                npc_control.start(run.match, hero)

    def test_authored_trooper_and_heavy_melee_cooldown_and_fresh_recovery(self):
        for key, damage, graph_index in (("1", 30, 0x1422), ("5", 80, 0x140E)):
            with self.subTest(npc=self.catalog[key].name):
                run, hero, bot = self.arena(self.catalog[key], "melee", "hit", distance=2)
                self.addCleanup(npc.remove_spawned, run.match)
                bot.yaw = -32768
                fight = run.match.combat
                target = next(t for t in fight._targets() if t.owner is hero)
                pool = fight.vitals_of(target)
                component = bot.body_script.component
                swing = next(i for i in component.instances.values() if i.graph.index == graph_index)
                component.edges([(31, True)])
                for _ in range(35):
                    run.step({})
                self.assertEqual(pool.total, 200 - damage)
                self.assertGreater(swing.vars[61].value(), 0)
                component.edges([(31, False)])
                run.step({})
                component.edges([(31, True)])
                run.step({})
                component.edges([(31, False)])
                run.step({})
                for _ in range(125):
                    self.assertEqual(pool.total, 200 - damage)
                    if swing.vars[61].value() == 0:
                        break
                    run.step({})
                self.assertEqual(swing.vars[61].value(), 0)
                for _ in range(8):
                    run.step({})
                self.assertEqual(pool.total, 200 - damage)
                component.edges([(31, True)])
                for _ in range(35):
                    run.step({})
                self.assertEqual(pool.total, 200 - 2 * damage)

    def test_authored_trooper_and_heavy_melee_geometry_and_faction(self):
        for key, case in product(("1", "5"), ("wall", "distance", "rear_close", "rear_far", "ally")):
            with self.subTest(npc=self.catalog[key].name, case=case):
                distance = {"distance": 6, "rear_far": 4}.get(case, 2)
                run, hero, bot = self.arena(self.catalog[key], "melee", case, distance=distance)
                self.addCleanup(npc.remove_spawned, run.match)
                if case == "ally":
                    run.match.game_map = replace(run.match.game_map, free_for_all=False)
                bot.yaw = 0 if case.startswith("rear") else -32768
                fight = run.match.combat
                target = next(t for t in fight._targets() if t.owner is hero)
                pool = fight.vitals_of(target)
                component = bot.body_script.component
                component.edges([(31, True)])
                for _ in range(35):
                    run.step({})
                self.assertIs(bot.body_script.component, component)
                # The authored 2.5m Heavy sphere reaches the close rear capsule.
                self.assertEqual(pool.total, 120 if key == "5" and case == "rear_close" else 200)

    def test_talon_melee_checks_deflect_for_each_target_in_same_swing(self):
        from ow174.game import arrow_deflection

        for key, damage in (("1", 30), ("5", 80)):
            for active, yaw in ((False, 0), (True, 0), (True, -32768)):
                with self.subTest(npc=self.catalog[key].name, active=active, yaw=yaw):
                    run, hero, bot = self.arena(self.catalog[key], "melee", "hit", distance=2)
                    match = run.match
                    self.addCleanup(npc.remove_spawned, match)
                    match.game_map = replace(match.game_map, free_for_all=False)
                    genji = match.add_player(2, "Genji", arrow_deflection.GENJI, hero.team, False)
                    in_world(match, genji)
                    genji.client = NpcFixtureClient(genji)
                    run.players.append(genji)
                    self.assertTrue(match.switch_hero(genji, heroes()[arrow_deflection.GENJI]))
                    genji.team = hero.team
                    x, y, z = hero.mover.position
                    genji.mover.position[:] = (x + 0.65, y, z + 0.2)
                    genji.mover.body = None
                    for _ in range(20):
                        run.step({})
                    bot.yaw = -32768
                    fight = match.combat
                    targets = {target.owner: target for target in fight._targets() if target.owner in (hero, genji)}
                    hanzo_pool = fight.vitals_of(targets[hero])
                    genji_pool = fight.vitals_of(targets[genji])
                    npc_pool = fight.vitals[bot.entity]
                    npc_before = npc_pool.total
                    run.step({genji: (yaw, 0, 16 if active else 0)})
                    volume = arrow_deflection.active_volume(targets[genji], 0x03A3, 10)
                    self.assertEqual(volume is not None, active)
                    bot.body_script.component.edges([(31, True)])
                    for _ in range(35):
                        run.step({genji: (yaw, 0, 0)})
                    self.assertEqual(hanzo_pool.total, 200 - damage)
                    self.assertEqual(genji_pool.total, 200 if active and yaw == 0 else 200 - damage)
                    self.assertEqual(npc_pool.total, npc_before)
                    self.assertFalse(genji.client.messages(combat.HIT))

    def test_authored_trooper_and_heavy_melee_spend_projected_cover_without_spill(self):
        for key, damage in (("1", 30), ("5", 80)):
            for shield_hp in (200, 15):
                with self.subTest(npc=self.catalog[key].name, shield=shield_hp):
                    run, hero, bot, caster, cast = self.talon_projected_arena(key, 2, start=False)
                    fight = run.match.combat
                    barriers = fight.zarya_barriers
                    bot.yaw = -32768
                    run.step({caster: (*cast, 16)})
                    self.assertEqual(barriers.projected[caster.body][1:], (200, hero.body))
                    if shield_hp < 200:
                        barriers.damage(hero, 200 - shield_hp, bot.entity)
                    bot.body_script.component.edges([(31, True)])
                    for _ in range(35):
                        run.step({})
                    self.assertEqual(fight.vitals[hero.body].total, 200)
                    if shield_hp > damage:
                        self.assertEqual(barriers.projected[caster.body][1:], (shield_hp - damage, hero.body))
                    else:
                        self.assertNotIn(caster.body, barriers.projected)

    def test_authored_trooper_and_heavy_melee_race_against_hanzo_lethal_bow(self):
        for key, bow_wins in product(("1", "5"), (False, True)):
            with self.subTest(npc=self.catalog[key].name, bow_wins=bow_wins):
                run, hero, bot = self.arena(self.catalog[key], "melee", "hit", distance=2)
                self.addCleanup(npc.remove_spawned, run.match)
                bot.yaw = -32768
                fight = run.match.combat
                victim = next(t for t in fight._targets() if t.owner is hero)
                hanzo_pool = fight.vitals_of(victim)
                bot_pool = fight.vitals[bot.entity]
                if bow_wins:
                    bot_pool.damage(bot_pool.total - 1)
                else:
                    hanzo_pool.damage(199)
                angles = self.target_aim(run, hero, bot)
                for _ in range(70):
                    run.step({hero: (*angles, 1)})
                bot.body_script.component.edges([(31, True)])
                with patch.object(fight.projectiles, "fired", wraps=fight.projectiles.fired) as fired:
                    if bow_wins:
                        run.step({hero: (*angles, 0)})
                    for _ in range(35):
                        run.step({hero: (*angles, 0 if bow_wins else 1)})
                    run.step({hero: (*angles, 0)})
                    self.assertEqual(fired.call_count, int(bow_wins))
                if bow_wins:
                    self.assertTrue(bot_pool.dead)
                    self.assertEqual(bot_pool.killer, hero.body)
                    self.assertEqual(hanzo_pool.total, 200)
                else:
                    self.assertTrue(hanzo_pool.dead)
                    self.assertEqual(hanzo_pool.killer, bot.entity)
                    self.assertFalse(bot_pool.dead)
                    self.assertFalse(fight.rockets)

    def heavy_capture_arena(self, distance=1.5):
        run, hero, bot = self.arena(self.catalog["5"], "bow", "hit", distance=distance)
        self.addCleanup(npc.remove_spawned, run.match)
        bot.yaw = -32768
        run.match.collision_world = flat_world(hero.mover.position[1])
        special = next(i for i in bot.body_script.component.instances.values()
                       if i.graph.index == heavy_assault.GRAPH)
        return run, hero, bot, special

    def test_heavy_capture_cancels_hanzo_draw_then_fresh_bow_recovers(self):
        run, hero, bot, special = self.heavy_capture_arena()
        fight = run.match.combat
        victim = hero.body_script.component
        start = hero.mover.position[2]

        def step(button=0):
            run.step({hero: (*self.target_aim(run, hero, bot), button)})

        for _ in range(70):
            step(1)
        special.component.edges([(35, True)])
        for _ in range(75):
            step(1)
            if fight.pins.get(bot.entity) == hero.body:
                break
        self.assertEqual(fight.pins.get(bot.entity), hero.body)
        child, = [i for i in victim.instances.values() if i.graph.index == heavy_assault.VICTIM_GRAPH]
        self.assertEqual(child.source_entity(), bot.entity)
        self.assertEqual(victim.entity_var(7539).value(), expr.Entity(bot.entity))
        step()
        self.assertFalse(fight.rockets)
        for _ in range(20):
            step(1)
        self.assertTrue(fight.is_stunned(hero))
        self.assertLess(hero.mover.position[2], start - 1)
        self.assertAlmostEqual(bot.position[2] - hero.mover.position[2], 1.5, delta=0.03)
        step()
        self.assertFalse(fight.rockets)
        for _ in range(250):
            step()
            if not fight.is_stunned(hero):
                break
        self.assertFalse(fight.is_stunned(hero))
        self.assertNotIn(child.id, victim.instances)
        self.assertIsNone(victim.entity_var(7539).value())
        before = fight.vitals[bot.entity].total
        for _ in range(70):
            step(1)
        step()
        for _ in range(10):
            step()
        self.assertEqual(fight.vitals[bot.entity].total, before - 125)

    def test_heavy_capture_wall_impact_kills_hanzo_and_releases_victim_graph(self):
        run, hero, bot, special = self.heavy_capture_arena()
        x, y, z = hero.mover.position
        wall_z = z - 0.9
        run.match.collision_world = World(
            box(x - 40, x + 40, y - 1, y, z - 40, z + 40)
            + wall(x - 5, wall_z, x + 5, wall_z, y, y + 5)
        )
        hero.mover.use(world=run.match.collision_world)
        fight = run.match.combat
        body = hero.body
        special.component.edges([(35, True)])
        for _ in range(75):
            run.step({})
            if fight.pins.get(bot.entity) == body:
                break
        self.assertEqual(fight.pins.get(bot.entity), body)
        for _ in range(40):
            run.step({})
            if fight.is_dead(hero):
                break
        self.assertTrue(fight.is_dead(hero))
        special.component.edges([(35, False)])
        self.assertEqual(fight.vitals[body].killer, bot.entity)
        self.assertGreater(hero.mover.position[2], wall_z)
        self.assertEqual(special.vars[59].value(), 2)
        self.assertNotIn(bot.entity, fight.pins)
        self.assertFalse(any(i.graph.index == heavy_assault.VICTIM_GRAPH
                             for i in hero.body_script.component.instances.values()))
        impact = special.states[special.graph.node(10).state]
        recovery_ms = 1000 * special.evaluate(special.graph.node(15).field("m_AA6E611E"))
        deadline = math.ceil((impact.start + recovery_ms) / special.component.frame_ms)
        while run.frame < deadline - 1:
            run.step({})
            self.assertEqual(special.vars[59].value(), 2)
        run.step({})
        self.assertEqual(special.vars[59].value(), 0)
        cooldown = special.vars[61].value()
        self.assertGreater(cooldown, 0)
        deadline = run.frame + math.ceil(cooldown * 1000 / special.component.frame_ms)
        while run.frame < deadline - 1:
            if run.frame == deadline - 40:
                special.component.edges([(35, True)])
            if run.frame == deadline - 39:
                special.component.edges([(35, False)])
            run.step({})
            self.assertEqual(special.vars[59].value(), 0)
            self.assertGreater(special.vars[61].value(), 0)
        run.step({})
        self.assertEqual(special.vars[61].value(), 0)
        self.assertEqual(special.vars[59].value(), 0)
        self.assertNotEqual(hero.body, body)
        self.assertFalse(fight.is_dead(hero))
        run.match.collision_world = flat_world(y)
        hero.mover.use(world=run.match.collision_world)
        hero.mover.position[:] = (x, y, z)
        hero.mover.body = None
        bot.position = (x, y, z + 1.5)
        special.component.edges([(35, True)])
        for _ in range(75):
            run.step({})
            if fight.pins.get(bot.entity) == hero.body:
                break
        self.assertEqual(fight.pins.get(bot.entity), hero.body)

    def test_heavy_capture_interrupts_storm_after_first_arrow_and_requires_ready_recast(self):
        run, hero, bot, special = self.heavy_capture_arena()
        fight = run.match.combat
        storm = next(i for i in hero.body_script.component.instances.values() if i.graph.index == 0x146A)
        ability = storm.states[0]
        projectile = 0x040000000000270F
        pool = fight.vitals[bot.entity]
        before = pool.total

        def step(button=0):
            run.step({hero: (*self.target_aim(run, hero, bot), button)})

        with patch.object(fight.projectiles, "fired", wraps=fight.projectiles.fired) as fired:
            step(16)
            step()
            for _ in range(50):
                step(1)
                if fired.called:
                    break
            self.assertEqual([call.args[3] for call in fired.call_args_list], [projectile])
            self.assertTrue(ability.flags & 1)
            step()
            special.component.edges([(35, True)])
            for _ in range(75):
                step()
                if fight.pins.get(bot.entity) == hero.body:
                    break
            self.assertEqual(fight.pins.get(bot.entity), hero.body)
            self.assertFalse(ability.flags & 1)
            self.assertGreater(storm.find_var(0, 61).value(), 9.95)
            self.assertEqual(storm.find_var(0, 215).value(), 0)
            for _ in range(12):
                step(1)
            self.assertEqual(fired.call_count, 1)
            self.assertEqual(pool.total, before - 70)
            for _ in range(250):
                step()
                if not fight.is_stunned(hero):
                    break
            self.assertFalse(fight.is_stunned(hero))
            step(16)
            step()
            self.assertFalse(ability.flags & 1)
            for _ in range(650):
                if storm.find_var(0, 61).value() == 0:
                    break
                step()
            self.assertEqual(storm.find_var(0, 61).value(), 0)
            self.assertFalse(ability.flags & 1)
            self.assertEqual(fired.call_count, 1)
            step(16)
            step()
            self.assertTrue(ability.flags & 1)
            for _ in range(50):
                step(1)
                if fired.call_count == 2:
                    break
            self.assertEqual([call.args[3] for call in fired.call_args_list], [projectile, projectile])
            for _ in range(10):
                step()
            self.assertEqual(pool.total, before - 140)

    def test_heavy_capture_cancels_paid_dragonstrike_before_initial_arrow(self):
        from test_game_hanzo_dragons import prepared_profile

        run, hero, bot, special = self.heavy_capture_arena()
        fight = run.match.combat
        fight.configure_dragonstrike(prepared_profile())
        script = hero.body_script
        script.ult_seconds = 0
        script.set_ult_charge(1680)
        charge = script.component.entity_var(combat.ULT_CHARGE)
        special.component.edges([(35, True)])
        with patch.object(fight.projectiles, "fired", wraps=fight.projectiles.fired) as fired:
            run.step({hero: (*self.target_aim(run, hero, bot), 32)})
            self.assertTrue(hanzo_control.casting(script))
            self.assertEqual(charge.value(), 0)
            for _ in range(60):
                run.step({})
                if fight.pins.get(bot.entity) == hero.body:
                    break
            self.assertEqual(fight.pins.get(bot.entity), hero.body)
            self.assertFalse(hanzo_control.casting(script))
            self.assertLess(charge.value(), 1680)
            for _ in range(250):
                run.step({})
                if not fight.is_stunned(hero):
                    break
            self.assertFalse(fight.is_stunned(hero))
            self.assertFalse(fired.called)
            self.assertFalse(fight.rockets)
            self.assertFalse(fight.dragons)
            self.assertLess(charge.value(), 1680)
            run.step({hero: (*self.target_aim(run, hero, bot), 32)})
            self.assertFalse(hanzo_control.casting(script))
            self.assertFalse(fired.called)

    def test_heavy_capture_preserves_prepared_sonic_but_cancels_its_draw(self):
        from test_game_hanzo_sonic import profile
        from ow174.game import sonic

        run, hero, bot, special = self.heavy_capture_arena()
        fight = run.match.combat
        fight.configure_sonic(profile())
        component = hero.body_script.component
        ability = next(i for i in component.instances.values() if i.graph.index == 0x00C1)
        selected = component.entity_var(627)
        pool = fight.vitals[bot.entity]
        before = pool.total

        def step(button=0):
            run.step({hero: (*self.target_aim(run, hero, bot), button)})

        with patch.object(fight.projectiles, "fired", wraps=fight.projectiles.fired) as fired:
            step(8)
            step()
            self.assertEqual(selected.value(), 1)
            for _ in range(70):
                step(1)
            special.component.edges([(35, True)])
            for _ in range(75):
                step(1)
                if fight.pins.get(bot.entity) == hero.body:
                    break
            self.assertEqual(fight.pins.get(bot.entity), hero.body)
            step()
            self.assertEqual((selected.value(), ability.vars[61].value(), fired.call_count), (1, 0, 0))
            for _ in range(250):
                step()
                if not fight.is_stunned(hero):
                    break
            self.assertFalse(fight.is_stunned(hero))
            self.assertEqual(selected.value(), 1)
            self.assertFalse(fired.called)
            for _ in range(70):
                step(1)
            step()
            self.assertEqual([call.args[3] for call in fired.call_args_list], [sonic.DEFINITIONS[0]])
            self.assertEqual(selected.value(), 0)
            self.assertGreater(ability.vars[61].value(), 11.9)
            for _ in range(10):
                step()
            self.assertEqual(pool.total, before - 125)
            beacon, = fight.sonars
            self.assertEqual(beacon.attached, bot.entity)
            self.assertTrue(fight.revealed_to(hero, bot))

    def test_heavy_capture_wall_kill_preserves_launched_dragonstrike_and_source(self):
        from test_game_hanzo_dragons import prepared_profile

        for transformed in (False, True):
            with self.subTest(transformed=transformed):
                run, hero, bot, special = self.heavy_capture_arena()
                match = run.match
                match.game_map = replace(match.game_map, free_for_all=False)
                fight = match.combat
                fight.configure_dragonstrike(prepared_profile())
                x, y, z = hero.mover.position
                match.collision_world = World(
                    box(x - 50, x + 70, y - 1, y, z - 40, z + 40)
                    + wall(x - 5, z - 0.9, x + 5, z - 0.9, y, y + 5)
                )
                hero.mover.use(world=match.collision_world)
                victim = match.add_player(3, "Downrange", SOLDIER, bot.team, False)
                in_world(match, victim)
                victim.client = NpcFixtureClient(victim)
                run.players.append(victim)
                self.assertTrue(match.switch_hero(victim, heroes()[SOLDIER]))
                victim.team = bot.team
                victim.mover.position[:] = (x + 40, y, z)
                victim.mover.body = None
                victim_pool = fight.vitals_of(next(t for t in fight._targets() if t.owner is victim))
                source_body = hero.body
                script = hero.body_script
                script.ult_seconds = 0
                script.set_ult_charge(1680)
                angles = aim(fight._eye(hero), (x + 40, y + 1.2, z))
                run.step({hero: (*angles, 32)})
                if not transformed:
                    for _ in range(18):
                        run.step({hero: (*angles, 0)})
                    special.component.edges([(35, True)])
                for _ in range(100):
                    run.step({hero: (*angles, 0)})
                    if fight.rockets:
                        break
                initial, = fight.rockets
                self.assertIsNotNone(initial.continuation)
                self.assertEqual(initial.source_entity, source_body)
                if transformed:
                    for _ in range(60):
                        run.step({})
                        if fight.dragons:
                            break
                    original, = fight.dragons
                    special.component.edges([(35, True)])
                for _ in range(90):
                    run.step({})
                    if fight.is_dead(hero):
                        break
                self.assertTrue(fight.is_dead(hero))
                self.assertEqual(fight.vitals[source_body].killer, bot.entity)
                self.assertEqual(victim_pool.total, 200)
                if transformed:
                    self.assertEqual(fight.dragons, [original])
                else:
                    self.assertIn(initial, fight.rockets)
                    self.assertFalse(fight.dragons)
                    for _ in range(60):
                        run.step({})
                        if fight.dragons:
                            break
                flight, = fight.dragons
                self.assertEqual(flight.source_entity, source_body)
                self.assertIs(flight.shooter, hero)
                for _ in range(230):
                    run.step({})
                    if victim_pool.total < 200:
                        break
                self.assertIn(round(200 - victim_pool.total, 3), (2.4, 4.8))

    def test_heavy_capture_discards_buffered_melee_until_a_fresh_press(self):
        run, hero, bot, special = self.heavy_capture_arena()
        fight = run.match.combat
        pool = fight.vitals[bot.entity]
        before = pool.total
        melee = 0x800

        def step(button=0):
            run.step({hero: (*self.target_aim(run, hero, bot), button)})

        step(melee)
        self.assertEqual(pool.total, before - 30)
        first = run.frame
        for _ in range(5):
            step()
        special.component.edges([(35, True)])
        while run.frame < first + 53:
            step()
        self.assertNotIn(bot.entity, fight.pins)
        step(melee)
        graph_instance = next(i for i in hero.body_script.component.instances.values() if i.graph.index == 0x0043)
        ability = next(state for state in graph_instance.states.values() if state.node.pos == 14)
        self.assertTrue(ability.presses)
        for _ in range(6):
            step(melee)
            if fight.pins.get(bot.entity) == hero.body:
                break
        self.assertEqual(fight.pins.get(bot.entity), hero.body)
        self.assertFalse(ability.presses)
        for _ in range(10):
            step(melee)
        self.assertEqual(fight.pins.get(bot.entity), hero.body)
        for _ in range(250):
            step()
            if not fight.is_stunned(hero):
                break
        self.assertFalse(fight.is_stunned(hero))
        step()
        self.assertEqual(pool.total, before - 30)
        step(melee)
        self.assertEqual(pool.total, before - 60)

    def test_heavy_capture_stops_lunge_and_fresh_jump_recovers_after_cooldown(self):
        run, hero, bot, special = self.heavy_capture_arena()
        fight = run.match.combat
        movement = next(i for i in hero.body_script.component.instances.values() if i.graph.index == 0x010C)
        world = hero.body_script.world

        def step(button=0):
            run.step({hero: (*self.target_aim(run, hero, bot), button)})

        special.component.edges([(35, True)])
        for _ in range(32):
            step()
        with patch.object(world, "velocity_action", wraps=world.velocity_action) as launch:
            step(JUMP)
            for _ in range(14):
                step()
            step(JUMP)
            self.assertEqual(launch.call_count, 1)
            self.assertGreater(abs(hero.mover.velocity[2]), 14)
            for _ in range(12):
                step(JUMP)
                if fight.pins.get(bot.entity) == hero.body:
                    break
            self.assertEqual(fight.pins.get(bot.entity), hero.body)
            self.assertEqual(hero.mover.velocity[0::2], (0, 0))
            for _ in range(12):
                step(JUMP)
                self.assertEqual(hero.mover.velocity[0::2], (0, 0))
            self.assertGreater(movement.vars[61].value(), 0)
            for _ in range(350):
                step()
                if not fight.is_stunned(hero) and movement.vars[61].value() == 0:
                    break
            self.assertFalse(fight.is_stunned(hero))
            self.assertEqual(movement.vars[61].value(), 0)
            self.assertEqual(launch.call_count, 1)
            step(JUMP)
            for _ in range(14):
                step()
            step(JUMP)
            self.assertEqual(launch.call_count, 2)
            self.assertGreater(abs(hero.mover.velocity[2]), 14)

    def test_heavy_capture_interrupts_active_wall_climb_at_contact(self):
        run, hero, bot, special = self.heavy_capture_arena()
        fight = run.match.combat
        x, y, z = hero.mover.position
        match = run.match
        match.collision_world = World(
            box(x - 30, x + 30, y - 1, y, z - 30, z + 30)
            + wall(x - 5, z - 0.7, x + 5, z - 0.7, y, y + 10)
        )
        hero.mover.use(world=match.collision_world)
        component = hero.body_script.component
        climb = hero.body_script.world.hanzo_climb
        interrupt = fight._interrupt
        active_at_pin = []

        def observe(target):
            if target.owner is hero:
                active_at_pin.append(climb.active)
            return interrupt(target)

        special.component.edges([(35, True)])
        with patch.object(fight, "_interrupt", side_effect=observe):
            for _ in range(45):
                run.step({})
            for _ in range(2):
                run.step({hero: (-32768, 0, JUMP)})
            self.assertTrue(climb.active)
            self.assertNotIn(bot.entity, fight.pins)
            for _ in range(12):
                run.step({hero: (-32768, 0, JUMP)})
                if fight.pins.get(bot.entity) == hero.body:
                    break
            self.assertEqual(fight.pins.get(bot.entity), hero.body)
            self.assertEqual(active_at_pin, [True])
            self.assertFalse(climb.active)
            self.assertFalse(component.entity_var(1204).value())

    def test_heavy_capture_pins_hanzo_after_climb_handoff_and_clears_the_route(self):
        from test_game_hanzo_movement import HanzoLungeTests

        run, hero, bot, special = self.heavy_capture_arena()
        match = run.match
        fight = match.combat
        x, y, z = hero.mover.position
        match.collision_world = World(
            box(x - 30, x + 30, y - 1, y, z - 30, z + 30)
            + box(x - 5, x + 5, y, y + 3, z - 8, z - 0.7),
        )
        hero.mover.use(world=match.collision_world)
        progress = hero.body_script.world.hanzo_climb
        component = hero.body_script.component
        special.component.edges([(35, True)])
        for frame in range(75):
            route = progress.mantle
            if frame < 30:
                run.step({})
            else:
                HanzoLungeTests.step(run, hero, buttons=JUMP, yaw=-32768, forward=127)
            if fight.pins.get(bot.entity) == hero.body:
                break
        self.assertEqual(frame, 50)
        self.assertIsNotNone(route)
        self.assertEqual(fight.pins.get(bot.entity), hero.body)
        self.assertTrue(fight.is_stunned(hero))
        victim, = [i for i in component.instances.values() if i.graph.index == heavy_assault.VICTIM_GRAPH]
        self.assertEqual(victim.source_entity(), bot.entity)
        self.assertFalse(progress.active)
        self.assertIsNone(progress.mantle)
        self.assertFalse(component.entity_var(1204).value())
        for _ in range(8):
            HanzoLungeTests.step(run, hero, buttons=JUMP, yaw=-32768, forward=127)
            self.assertIsNone(progress.mantle)
            self.assertGreater(hero.mover.position[2], z - 0.7)

    def test_heavy_capture_raised_wall_probe_tracks_and_retires_authored_hit(self):
        run, hero, bot, special = self.heavy_capture_arena(6)
        x, y, z = hero.mover.position
        hero.mover.position[0] += 8
        hero.mover.body = None
        floor = box(x - 40, x + 40, y - 1, y, z - 40, z + 40)

        def place_wall(wall_z):
            run.match.collision_world = World(floor + wall(x - 2, wall_z, x + 2, wall_z, y, y + 3))
            hero.mover.use(world=run.match.collision_world)

        place_wall(z - 5)
        special.component.edges([(35, True)])
        for _ in range(65):
            run.step({})
        hit = special.vars[13757]
        self.assertIs(hit.value(), False)
        place_wall(bot.position[2] - 1)
        for _ in range(2):
            run.step({})
        self.assertIs(hit.value(), True)
        export_component_vm(special.component)
        self.assertIs(hit.value(), True)
        place_wall(bot.position[2] + 1)
        for _ in range(2):
            run.step({})
        self.assertIs(hit.value(), False)
        for _ in range(240):
            run.step({})
            if special.vars[59].value() == 0:
                break
        self.assertEqual(special.vars[59].value(), 0)
        self.assertIsNone(hit.value())

    def test_heavy_capture_lower_wall_probe_drives_authored_exit(self):
        run, hero, bot, special = self.heavy_capture_arena(6)
        x, y, z = hero.mover.position
        hero.mover.position[0] += 8
        hero.mover.body = None
        floor = box(x - 40, x + 40, y - 1, y, z - 40, z + 40)

        def place_wall(wall_z, height):
            run.match.collision_world = World(floor + wall(x - 2, wall_z, x + 2, wall_z, y, y + height))
            hero.mover.use(world=run.match.collision_world)

        place_wall(z - 5, 3)
        special.component.edges([(35, True)])
        for _ in range(65):
            run.step({})
        lower, raised = special.vars[2824], special.vars[13757]
        self.assertIs(lower.value(), False)
        self.assertIs(raised.value(), False)
        wall_z = bot.position[2] - 0.9
        place_wall(wall_z, 3)
        for _ in range(2):
            run.step({})
        self.assertIs(lower.value(), True)
        self.assertIs(raised.value(), True)
        self.assertEqual(special.vars[59].value(), 1)
        place_wall(wall_z, 0.8)
        for _ in range(4):
            run.step({})
            if special.vars[59].value() == 0:
                break
        self.assertEqual(special.vars[59].value(), 0)
        self.assertGreater(special.vars[61].value(), 0)
        self.assertIsNone(lower.value())
        self.assertIsNone(raised.value())
        self.assertNotIn(bot.entity, run.match.combat.pins)

    def test_heavy_capture_allied_flashbang_releases_hanzo_and_stops_carry(self):
        run, hero, bot, special = self.heavy_capture_arena()
        match = run.match
        match.game_map = replace(match.game_map, free_for_all=False)
        fight = match.combat
        target = next(t for t in fight._targets() if t.owner is hero)
        pool = fight.vitals_of(target)
        special.component.edges([(35, True)])
        for _ in range(75):
            run.step({})
            if fight.pins.get(bot.entity) == hero.body:
                break
        self.assertEqual(fight.pins.get(bot.entity), hero.body)

        mccree = 0x02E0000000000042
        ally = match.add_player(4, "Rescuer", mccree, hero.team, False)
        in_world(match, ally)
        ally.client = NpcFixtureClient(ally)
        run.players.append(ally)
        self.assertTrue(match.switch_hero(ally, heroes()[mccree]))
        ally.team = hero.team
        x, y, z = bot.position
        ally.mover.position[:] = (x + 2, y, z - 3)
        ally.mover.body = None
        rescue = aim(fight._eye(ally), (x, y + 1, z - 1))
        before = fight.vitals[bot.entity].total
        run.step({ally: (*rescue, 16)})
        for _ in range(45):
            run.step({})
            if fight.is_stunned(bot):
                break
        self.assertTrue(fight.is_stunned(bot))
        self.assertEqual(fight.vitals[bot.entity].total, before - 25)
        self.assertFalse(fight.is_stunned(hero))
        self.assertNotIn(bot.entity, fight.pins)
        self.assertFalse(any(i.graph.index == heavy_assault.VICTIM_GRAPH
                             for i in hero.body_script.component.instances.values()))
        carrier = bot.position
        for _ in range(60):
            run.step({})
            self.assertNotIn(bot.entity, fight.pins)
        self.assertEqual(bot.position, carrier)
        self.assertEqual(pool.total, 200)

    def test_heavy_capture_search_respects_wall_distance_facing_and_team(self):
        for case, distance in (("front", 4), ("rear", 4), ("wall", 1.5), ("distance", 12), ("team", 1.5)):
            with self.subTest(case=case):
                run, hero, bot, special = self.heavy_capture_arena(distance)
                match = run.match
                if case == "wall":
                    x, y, z = hero.mover.position
                    match.collision_world = World(
                        box(x - 40, x + 40, y - 1, y, z - 40, z + 40)
                        + wall(x - 5, z + 0.75, x + 5, z + 0.75, y, y + 5)
                    )
                    hero.mover.use(world=match.collision_world)
                elif case == "rear":
                    bot.yaw = 0
                elif case == "team":
                    match.game_map = replace(match.game_map, free_for_all=False)
                    bot.team = hero.team
                fight = match.combat
                target = next(t for t in fight._targets() if t.owner is hero)
                pool = fight.vitals_of(target)
                special.component.edges([(35, True)])
                for _ in range(75):
                    run.step({})
                    if case != "front":
                        self.assertNotIn(bot.entity, fight.pins)
                expected = (expr.Entity(hero.body),) if case == "front" else ()
                self.assertEqual(special.vars[562].value(), expected)
                self.assertEqual(fight.pins.get(bot.entity), hero.body if expected else None)
                self.assertEqual(pool.total, 200)

    def test_heavy_capture_cooldown_requires_ready_input_or_live_buffer(self):
        for buffered in (False, True):
            with self.subTest(buffered=buffered):
                run, hero, bot, special = self.heavy_capture_arena()
                fight = run.match.combat
                start_hero, start_bot = tuple(hero.mover.position), bot.position
                component = special.component
                component.edges([(35, True)])
                for _ in range(75):
                    run.step({})
                    if fight.pins.get(bot.entity) == hero.body:
                        break
                self.assertEqual(fight.pins.get(bot.entity), hero.body)
                component.edges([(35, False)])
                for _ in range(250):
                    run.step({})
                    if special.vars[59].value() == 0:
                        break
                self.assertEqual(special.vars[61].value(), 15)
                self.assertNotIn(bot.entity, fight.pins)
                hero.mover.position[:] = start_hero
                hero.mover.body = None
                bot.position = start_bot
                deadline = run.frame + math.ceil(15000 / component.frame_ms)
                press_at = deadline - (4 if buffered else 40)
                while run.frame < deadline - 1:
                    if run.frame == press_at:
                        component.edges([(35, True)])
                    if run.frame == press_at + 1:
                        component.edges([(35, False)])
                    run.step({})
                    self.assertEqual(special.vars[59].value(), 0)
                    self.assertGreater(special.vars[61].value(), 0)
                run.step({})
                self.assertEqual(special.vars[61].value(), 0)
                self.assertEqual(special.vars[59].value(), int(buffered))
                if not buffered:
                    component.edges([(35, True)])
                    run.step({})
                    self.assertEqual(special.vars[59].value(), 1)
                for _ in range(75):
                    run.step({})
                    if fight.pins.get(bot.entity) == hero.body:
                        break
                self.assertEqual(fight.pins.get(bot.entity), hero.body)

    def test_heavy_capture_excludes_actual_zarya_projected_recipient(self):
        run, hero, bot, caster, cast = self.talon_projected_arena("5", 1.5, start=False)
        bot.yaw = -32768
        fight = run.match.combat
        target = next(t for t in fight._targets() if t.owner is hero)
        pool = fight.vitals_of(target)
        special = next(i for i in bot.body_script.component.instances.values()
                       if i.graph.index == heavy_assault.GRAPH)
        special.component.edges([(35, True)])
        for _ in range(40):
            run.step({})
        run.step({caster: (*cast, 16)})
        self.assertEqual(fight.zarya_barriers.projected[caster.body][1:], (200, hero.body))
        for _ in range(34):
            run.step({})
            self.assertNotIn(bot.entity, fight.pins)
        self.assertEqual(special.vars[562].value(), ())
        self.assertEqual(pool.total, 200)
        self.assertFalse(any(i.graph.index == heavy_assault.VICTIM_GRAPH
                             for i in hero.body_script.component.instances.values()))

    def heavy_secondary_contact_arena(self):
        run, hero, bot, special = self.heavy_capture_arena()
        match = run.match
        match.game_map = replace(match.game_map, free_for_all=False)
        x, y, z = hero.mover.position
        teammate = match.add_player(3, "Ally", SOLDIER, hero.team, False)
        in_world(match, teammate)
        teammate.client = NpcFixtureClient(teammate)
        run.players.append(teammate)
        self.assertTrue(match.switch_hero(teammate, heroes()[SOLDIER]))
        teammate.team = hero.team
        teammate.mover.position[:] = (x, y, z + 0.8)
        teammate.mover.body = None
        fight = match.combat
        target = next(t for t in fight._targets() if t.owner is hero)
        pool = fight.vitals_of(target)
        return run, hero, bot, special, teammate, pool

    def heavy_secondary_enemy(self, run, hero, bot, distance, guid=SOLDIER):
        match = run.match
        target = match.add_player(4, "Enemy", guid, bot.team, False)
        in_world(match, target)
        target.client = NpcFixtureClient(target)
        run.players.append(target)
        self.assertTrue(match.switch_hero(target, heroes()[guid]))
        target.team = bot.team
        x, y, z = hero.mover.position
        target.mover.position[:] = (x + distance, y, z)
        target.mover.body = None
        return target

    def test_heavy_capture_secondary_contact_damages_and_launches_hanzo_once(self):
        run, hero, bot, special, teammate, pool = self.heavy_secondary_contact_arena()
        fight = run.match.combat
        special.component.edges([(35, True)])
        for _ in range(75):
            run.step({})
            if pool.total < 200:
                break
        self.assertEqual(special.vars[562].value(), (expr.Entity(teammate.body),))
        self.assertEqual(special.vars[563].value(), (expr.Entity(hero.body),))
        self.assertEqual(pool.total, 150)
        self.assertGreater(hero.mover.velocity[1], 0)
        self.assertLess(hero.mover.velocity[2], 0)
        export_component_vm(special.component)
        for _ in range(120):
            run.step({})
            self.assertNotIn(hero.body, fight.pins.values())
        self.assertEqual(pool.total, 150)

    def test_heavy_secondary_launch_allows_fresh_airborne_lunge_without_pin(self):
        run, hero, bot, special, teammate, pool = self.heavy_secondary_contact_arena()
        fight = run.match.combat
        movement = next(i for i in hero.body_script.component.instances.values() if i.graph.index == 0x010C)
        world = hero.body_script.world
        with patch.object(world, "velocity_action", wraps=world.velocity_action) as launch:
            special.component.edges([(35, True)])
            for _ in range(75):
                run.step({})
                if pool.total < 200:
                    break
            self.assertEqual(pool.total, 150)
            self.assertEqual(fight.pins.get(bot.entity), teammate.body)
            self.assertFalse(fight.is_stunned(hero))
            self.assertGreater(hero.mover.velocity[1], 0)
            self.assertLess(hero.mover.velocity[2], 0)
            self.assertFalse(launch.called)
            for _ in range(14):
                run.step({})
            self.assertTrue(hero.mover.airborne)
            self.assertEqual(movement.vars[61].value(), 0)
            run.step({hero: (16384, 0, JUMP)})
            self.assertEqual(launch.call_count, 1)
            for actual, expected in zip(hero.mover.velocity, (15.0, 4.5, 0.0), strict=True):
                self.assertAlmostEqual(actual, expected, places=6)
            for _ in range(12):
                run.step({hero: (16384, 0, JUMP)})
            self.assertEqual(launch.call_count, 1)
            self.assertGreater(movement.vars[61].value(), 0)
            self.assertEqual(pool.total, 150)
            self.assertNotIn(hero.body, fight.pins.values())

    def test_heavy_secondary_launch_preserves_actual_bow_and_sonic_draw(self):
        from test_game_hanzo_sonic import profile
        from ow174.game import sonic

        for prepared in (False, True):
            with self.subTest(sonic=prepared):
                run, hero, bot, special, teammate, pool = self.heavy_secondary_contact_arena()
                match = run.match
                fight = match.combat
                if prepared:
                    fight.configure_sonic(profile())
                target = self.heavy_secondary_enemy(run, hero, bot, 8)
                x, y, z = target.mover.position
                component = hero.body_script.component
                sonic_ability = next(i for i in component.instances.values() if i.graph.index == 0x00C1)
                selected = component.entity_var(627)

                def step(button=0):
                    angles = aim(fight._eye(hero), (x, y + 1, z))
                    run.step({hero: (*angles, button)})

                if prepared:
                    step(8)
                    step()
                for _ in range(70):
                    step(1)
                start = fight._eye(hero)
                special.component.edges([(35, True)])
                for _ in range(75):
                    step(1)
                    if pool.total < 200:
                        break
                self.assertEqual(pool.total, 150)
                self.assertEqual(fight.pins.get(bot.entity), teammate.body)
                self.assertFalse(fight.is_stunned(hero))
                for _ in range(3):
                    step(1)
                self.assertTrue(hero.mover.airborne)
                self.assertGreater(math.dist(start, fight._eye(hero)), 0.1)
                self.assertFalse(fight.rockets)
                self.assertEqual(selected.value() or 0, int(prepared))
                self.assertEqual(sonic_ability.vars[61].value(), 0)
                step()
                arrow, = fight.rockets
                self.assertEqual(arrow.position, fight._eye(hero))
                self.assertEqual((arrow.source_entity, arrow.direct.amount), (hero.body, 125))
                self.assertEqual(selected.value() or 0, 0)
                if prepared:
                    self.assertEqual(arrow.shot.definition, sonic.DEFINITIONS[0])
                    self.assertGreater(sonic_ability.vars[61].value(), 11.9)
                for _ in range(15):
                    step()
                self.assertEqual(fight.vitals[target.body].total, 75)
                self.assertEqual(pool.total, 150)
                if prepared:
                    beacon, = fight.sonars
                    self.assertEqual((beacon.source_entity, beacon.attached), (hero.body, target.body))
                    self.assertTrue(fight.revealed_to(hero, target))
                else:
                    self.assertFalse(fight.sonars)

    def test_actual_heavy_bow_hits_earn_dragonstrike_from_zero_charge(self):
        from test_game_hanzo_dragons import prepared_profile

        run, hero, bot = self.arena(self.catalog["5"], "bow", "hit", distance=18)
        self.addCleanup(npc.remove_spawned, run.match)
        fight = run.match.combat
        profile = prepared_profile()
        fight.configure_dragonstrike(profile)
        script = hero.body_script
        charge = script.component.entity_var(combat.ULT_CHARGE)
        self.assertIsNone(script.ult_seconds)
        self.assertEqual(charge.value(), 0)
        initial_frame = script.frame
        initial_passive_ms = script.passive_charge_ms
        pool = fight.vitals[bot.entity]
        before = pool.total
        angles = self.target_aim(run, hero, bot)

        def step(button=0):
            run.step({hero: (*angles, button)})

        for shot in range(1, 14):
            for _ in range(70):
                step(1)
            step()
            arrow, = fight.rockets
            self.assertEqual(arrow.direct.amount, 125)
            for _ in range(20):
                step()
                if arrow not in fight.rockets:
                    break
            self.assertEqual(pool.total, before - 125 * shot)
            elapsed_ms = (script.frame - initial_frame) * script.frame_ms
            passive = (initial_passive_ms + elapsed_ms) // 1000 * 5
            self.assertEqual(charge.value(), min(1680, 125 * shot + passive))
            if shot == 12:
                self.assertLess(charge.value(), 1680)
                step(32)
                self.assertFalse(hanzo_control.casting(script))
                self.assertFalse(fight.rockets)
                self.assertFalse(fight.dragons)
                step()

        self.assertEqual(charge.value(), 1680)
        step(32)
        self.assertTrue(hanzo_control.casting(script))
        self.assertEqual(charge.value(), 0)
        self.assertFalse(fight.rockets)
        for _ in range(100):
            step()
            if fight.rockets:
                break
        initial, = fight.rockets
        self.assertIs(initial.continuation, profile)
        self.assertEqual((initial.source_entity, initial.direct.amount), (hero.body, 125))
        before_dragon = pool.total
        for _ in range(80):
            step()
            if pool.total < before_dragon:
                break
        flight, = fight.dragons
        self.assertEqual(flight.source_entity, hero.body)
        self.assertAlmostEqual(initial.distance_flown, 10)
        self.assertNotIn(bot.entity, initial.hit_entities)
        self.assertLess(pool.total, before_dragon)

    def test_heavy_secondary_launch_preserves_paid_dragonstrike_windup(self):
        from test_game_hanzo_dragons import prepared_profile

        run, hero, bot, special, teammate, pool = self.heavy_secondary_contact_arena()
        fight = run.match.combat
        profile = prepared_profile()
        fight.configure_dragonstrike(profile)
        target = self.heavy_secondary_enemy(run, hero, bot, 20)
        target_pool = fight.vitals_of(next(t for t in fight._targets() if t.owner is target))
        x, y, z = target.mover.position
        script = hero.body_script
        script.ult_seconds = 0
        script.set_ult_charge(1680)
        charge = script.component.entity_var(combat.ULT_CHARGE)

        def step(button=0):
            run.step({hero: (*aim(fight._eye(hero), (x, y + 1, z)), button)})

        start = fight._eye(hero)
        special.component.edges([(35, True)])
        step(32)
        self.assertTrue(hanzo_control.casting(script))
        self.assertEqual(charge.value(), 0)
        for _ in range(60):
            step()
            if pool.total < 200:
                break
        self.assertEqual(pool.total, 150)
        self.assertEqual(fight.pins.get(bot.entity), teammate.body)
        self.assertFalse(fight.is_stunned(hero))
        self.assertTrue(hanzo_control.casting(script))
        self.assertFalse(fight.rockets)
        self.assertFalse(fight.dragons)
        for _ in range(40):
            step()
            if fight.rockets:
                break
        initial, = fight.rockets
        self.assertEqual(initial.position, fight._eye(hero))
        self.assertGreater(math.dist(start, initial.position), 0.1)
        self.assertIs(initial.continuation, profile)
        self.assertEqual((initial.source_entity, initial.direct.amount), (hero.body, 125))
        for _ in range(80):
            step()
            if target_pool.total < 200:
                break
        flight, = fight.dragons
        self.assertNotIn(initial, fight.rockets)
        self.assertAlmostEqual(initial.distance_flown, 10)
        self.assertEqual((flight.origin, flight.source_entity), (initial.position, hero.body))
        self.assertLess(target_pool.total, 200)
        self.assertNotIn(target.body, initial.hit_entities)
        self.assertEqual(pool.total, 150)

    def test_heavy_secondary_launch_preserves_storm_volley_and_cooldown(self):
        from ow174.game.sigma_grasp import SIGMA

        run, hero, bot, special, teammate, pool = self.heavy_secondary_contact_arena()
        fight = run.match.combat
        target = self.heavy_secondary_enemy(run, hero, bot, 8, SIGMA)
        target_pool = fight.vitals_of(next(t for t in fight._targets() if t.owner is target))
        self.assertEqual(target_pool.total, 400)
        x, y, z = target.mover.position
        storm = next(i for i in hero.body_script.component.instances.values() if i.graph.index == 0x146A)
        cooldown = storm.find_var(0, 61)

        def step(button=0):
            run.step({hero: (*aim(fight._eye(hero), (x, y + 1, z)), button)})

        start = fight._eye(hero)
        special.component.edges([(35, True)])
        for _ in range(25):
            step()
        with patch.object(fight.projectiles, "fired", wraps=fight.projectiles.fired) as fired:
            step(16)
            step()
            for _ in range(60):
                step(1)
                if pool.total < 200:
                    break
            self.assertEqual(pool.total, 150)
            self.assertEqual(fight.pins.get(bot.entity), teammate.body)
            self.assertFalse(fight.is_stunned(hero))
            self.assertTrue(storm.states[0].flags & 1)
            self.assertEqual(cooldown.value(), 0)
            before = fired.call_count
            self.assertIn(before, range(1, 5))
            for _ in range(100):
                step(1)
                if fired.call_count == 5:
                    break
            self.assertEqual([call.args[3] for call in fired.call_args_list], [0x040000000000270F] * 5)
            self.assertGreater(math.dist(start, fired.call_args_list[before].args[5]), 0.1)
            for _ in range(15):
                step()
            self.assertFalse(storm.states[0].flags & 1)
            self.assertGreater(cooldown.value(), 9.5)
            self.assertEqual(storm.find_var(0, 215).value(), 0)
            step(16)
            step()
            self.assertFalse(storm.states[0].flags & 1)
            self.assertEqual(fired.call_count, 5)
            self.assertEqual(target_pool.total, 50)
            hits = hero.client.messages(combat.HIT)
            self.assertEqual([hit["+0x78"]["+0x4"] for hit in hits], [70] * 5)
            for _ in range(650):
                if cooldown.value() == 0:
                    break
                step()
            self.assertEqual(cooldown.value(), 0)
            self.assertFalse(storm.states[0].flags & 1)
            self.assertEqual(fired.call_count, 5)
            step(16)
            step()
            self.assertTrue(storm.states[0].flags & 1)
            for _ in range(50):
                step(1)
                if fired.call_count == 6:
                    break
            self.assertEqual([call.args[3] for call in fired.call_args_list], [0x040000000000270F] * 6)
            for _ in range(15):
                step()
            self.assertTrue(target_pool.dead)
            self.assertEqual(target_pool.killer, hero.body)
        self.assertEqual(pool.total, 150)

    def test_heavy_secondary_launch_interrupts_wall_climb_without_erasing_impulse(self):
        contact_step = None
        for release_at_contact in (False, True):
            with self.subTest(release_at_contact=release_at_contact):
                run, hero, bot, special, teammate, pool = self.heavy_secondary_contact_arena()
                match = run.match
                fight = match.combat
                x, y, z = hero.mover.position
                wall_x = x + 0.78
                match.collision_world = World(
                    box(x - 30, x + 30, y - 1, y, z - 30, z + 30)
                    + wall(wall_x, z - 10, wall_x, z + 10, y, y + 10)
                )
                hero.mover.use(world=match.collision_world)
                component = hero.body_script.component
                special.component.edges([(35, True)])
                impulse = heavy_assault.secondary_impulse
                contacts = []

                def observe(state, target, config):
                    was_climbing = hero.body_script.world.hanzo_climb.active
                    before = tuple(hero.mover.position)
                    impulse(state, target, config)
                    if target.owner is hero:
                        contacts.append((was_climbing, before, hero.mover.velocity))

                with patch.object(heavy_assault, "secondary_impulse", side_effect=observe):
                    for frame in range(75):
                        climbing = frame >= 40 and (not release_at_contact or frame < contact_step)
                        run.step({hero: (16384, 0, JUMP if climbing else 0)})
                        if pool.total < 200:
                            break
                if release_at_contact:
                    self.assertEqual(frame, contact_step)
                else:
                    contact_step = frame
                self.assertEqual(pool.total, 150)
                self.assertEqual(fight.pins.get(bot.entity), teammate.body)
                self.assertFalse(fight.is_stunned(hero))
                (was_climbing, before, impulse_velocity), = contacts
                self.assertTrue(was_climbing)
                self.assertLess(impulse_velocity[2], 0)
                self.assertGreater(impulse_velocity[1], 0)
                climb = hero.body_script.world.hanzo_climb
                self.assertFalse(climb.active)
                self.assertFalse(component.entity_var(1204).value())
                self.assertLess(hero.mover.position[2], before[2])
                self.assertLess(hero.mover.velocity[2], 0)
                if not release_at_contact:
                    for _ in range(6):
                        run.step({hero: (16384, 0, JUMP)})
                        self.assertFalse(climb.active)
                    self.assertTrue(climb.blocked)
                self.assertEqual(pool.total, 150)

    def test_heavy_secondary_cancels_mantle_and_preserves_launch(self):
        from test_game_hanzo_movement import HanzoLungeTests

        for release in (False, True):
            with self.subTest(release=release):
                run, hero, bot, special, teammate, pool = self.heavy_secondary_contact_arena()
                match = run.match
                fight = match.combat
                x, y, z = hero.mover.position
                match.collision_world = World(
                    box(x - 30, x + 30, y - 1, y, z - 30, z + 30)
                    + box(x + 0.78, x + 8, y, y + 3, z - 5, z + 5),
                )
                hero.mover.use(world=match.collision_world)
                progress = hero.body_script.world.hanzo_climb
                special.component.edges([(35, True)])
                impulse = heavy_assault.secondary_impulse
                contacts = []

                def observe(state, target, config):
                    if target.owner is hero:
                        contacts.append((progress.mantle, tuple(hero.mover.position)))
                    impulse(state, target, config)

                with patch.object(heavy_assault, "secondary_impulse", side_effect=observe):
                    for frame in range(75):
                        held = 30 <= frame and not (release and frame >= 50)
                        HanzoLungeTests.step(run, hero, buttons=JUMP if held else 0,
                                             forward=127 if held else 0, yaw=16384)
                        if pool.total < 200:
                            break
                self.assertEqual(frame, 50)
                (route, before), = contacts
                self.assertIsNotNone(route)
                self.assertEqual(pool.total, 150)
                self.assertEqual(fight.pins.get(bot.entity), teammate.body)
                self.assertFalse(fight.is_stunned(hero))
                self.assertIsNone(progress.mantle)
                self.assertFalse(progress.active)
                self.assertFalse(hero.mover.driven)
                self.assertLess(hero.mover.position[2], before[2])
                self.assertLess(hero.mover.velocity[2], 0)
                self.assertGreater(hero.mover.velocity[1], 0)
                for _ in range(6):
                    HanzoLungeTests.step(run, hero, buttons=JUMP if not release else 0,
                                         forward=127 if not release else 0, yaw=16384)
                    self.assertIsNone(progress.mantle)
                self.assertEqual(pool.total, 150)

    def test_heavy_secondary_projected_cover_preserves_committed_mantle(self):
        from test_game_hanzo_movement import HanzoLungeTests
        from ow174.game import zarya_barrier

        for shield_hp in (200, 15):
            with self.subTest(shield_hp=shield_hp):
                run, hero, bot, special, teammate, pool = self.heavy_secondary_contact_arena()
                match = run.match
                fight = match.combat
                x, y, z = hero.mover.position
                match.collision_world = World(
                    box(x - 30, x + 30, y - 1, y, z - 30, z + 30)
                    + box(x + 0.78, x + 8, y, y + 3, z - 5, z + 5),
                )
                hero.mover.use(world=match.collision_world)
                caster = match.add_player(4, "Zarya", zarya_barrier.HERO, hero.team, False)
                in_world(match, caster)
                caster.client = NpcFixtureClient(caster)
                run.players.append(caster)
                self.assertTrue(match.switch_hero(caster, heroes()[zarya_barrier.HERO]))
                caster.team = hero.team
                caster.mover.position[:] = (x - 4, y, z)
                caster.mover.body = None
                for _ in range(25):
                    run.step({})
                special.component.edges([(35, True)])
                for _ in range(30):
                    run.step({})
                for _ in range(10):
                    HanzoLungeTests.step(run, hero, buttons=JUMP, yaw=16384, forward=127)
                cast = aim(fight._eye(caster), fight._eye(hero))
                run.step({caster: (*cast, 16), hero: (16384, 0, JUMP)})
                barriers = fight.zarya_barriers
                self.assertEqual(barriers.projected[caster.body][1:], (200, hero.body))
                if shield_hp < 200:
                    barriers.damage(hero, 200 - shield_hp, bot.entity)
                progress = hero.body_script.world.hanzo_climb
                remaining = max(0, shield_hp - 50)
                for _ in range(20):
                    HanzoLungeTests.step(run, hero, buttons=JUMP, yaw=16384, forward=127)
                    if barriers.projected.get(caster.body, (None, 0))[1] == remaining:
                        break
                self.assertEqual(barriers.projected.get(caster.body, (None, 0))[1], remaining)
                self.assertEqual(fight.pins.get(bot.entity), teammate.body)
                self.assertEqual(pool.total, 200)
                self.assertIsNotNone(progress.mantle)
                self.assertFalse(fight.is_stunned(hero))
                self.assertAlmostEqual(hero.mover.position[2], z, delta=0.01)
                for _ in range(38):
                    HanzoLungeTests.step(run, hero, buttons=JUMP, yaw=16384, forward=127)
                    if progress.mantle is None:
                        break
                self.assertIsNone(progress.mantle)
                self.assertFalse(hero.mover.airborne)
                self.assertAlmostEqual(hero.mover.position[1], y + 3, delta=0.01)
                self.assertGreater(hero.mover.position[0], x + 1)
                self.assertEqual(pool.total, 200)

    def test_heavy_secondary_contact_spends_projected_cover_without_launch(self):
        from ow174.game import zarya_barrier

        for shield_hp in (200, 15):
            with self.subTest(shield_hp=shield_hp):
                run, hero, bot, special, teammate, pool = self.heavy_secondary_contact_arena()
                match = run.match
                fight = match.combat
                barriers = fight.zarya_barriers
                caster = match.add_player(4, "Zarya", zarya_barrier.HERO, hero.team, False)
                in_world(match, caster)
                caster.client = NpcFixtureClient(caster)
                run.players.append(caster)
                self.assertTrue(match.switch_hero(caster, heroes()[zarya_barrier.HERO]))
                caster.team = hero.team
                x, y, z = hero.mover.position
                caster.mover.position[:] = (x + 4, y, z)
                caster.mover.body = None
                for _ in range(25):
                    run.step({})
                special.component.edges([(35, True)])
                for _ in range(40):
                    run.step({})
                cast = aim(fight._eye(caster), (x, y + 1, z))
                run.step({caster: (*cast, 16)})
                self.assertEqual(barriers.projected[caster.body][1:], (200, hero.body))
                if shield_hp < 200:
                    barriers.damage(hero, 200 - shield_hp, bot.entity)
                remaining = max(0, shield_hp - 50)
                for _ in range(34):
                    run.step({})
                    self.assertEqual(pool.total, 200)
                    self.assertFalse(hero.mover.airborne)
                    if barriers.projected.get(caster.body, (None, 0))[1] == remaining:
                        break
                self.assertEqual(fight.pins.get(bot.entity), teammate.body)
                self.assertEqual(special.vars[563].value(), (expr.Entity(hero.body),))
                self.assertEqual(tuple(hero.mover.position), (x, y, z))
                self.assertEqual(hero.mover.velocity, (0, 0, 0))
                if remaining:
                    self.assertEqual(barriers.projected[caster.body][1:], (remaining, hero.body))
                else:
                    self.assertNotIn(caster.body, barriers.projected)
                for _ in range(12):
                    run.step({})
                    self.assertEqual(pool.total, 200)
                    self.assertFalse(hero.mover.airborne)

    def deploy_winston_cover(self, run, hero, bot, *, allied_to_npc=False, forward=0):
        from ow174.game import winston_barrier

        match = run.match
        self.addCleanup(npc.remove_spawned, match)
        x, y, z = hero.mover.position
        match.collision_world = flat_world(y)
        match.game_map = replace(match.game_map, free_for_all=False)
        team = bot.team if allied_to_npc else hero.team
        winston = match.add_player(4, "Winston", winston_barrier.HERO, team, False)
        in_world(match, winston)
        winston.client = NpcFixtureClient(winston)
        run.players.append(winston)
        self.assertTrue(match.switch_hero(winston, heroes()[winston_barrier.HERO]))
        winston.team = team
        winston.mover.position[:] = (x + 1.5, y, z + forward)
        winston.mover.body = None
        for _ in range(20):
            run.step({})
        service = match.combat.winston_barriers
        run.step({winston: (0, 0, 16)})
        for _ in range(8):
            run.step({})
            if winston.body in service.actors:
                break
        bubble = service.actors[winston.body]
        self.assertEqual(bubble.health, 700)
        return bubble

    def test_talon_first_weapon_volley_checks_winston_shell_faction(self):
        for key, damage, pellets in (("1", 20, 0), ("2", 50, 25), ("3", 100, 1), ("5", 10, 0)):
            for allied in (False, True):
                with self.subTest(npc=self.catalog[key].name, allied=allied):
                    run, hero, bot = self.arena(self.catalog[key], "bow", "hit", distance=7)
                    bubble = self.deploy_winston_cover(run, hero, bot, allied_to_npc=allied)
                    fight = run.match.combat
                    run.step({hero: (*self.target_aim(run, hero, bot), 0)})
                    self.assertEqual(npc_control.start(run.match, hero), bot.entity)
                    with patch.object(fight, "_shoot", wraps=fight._shoot) as fired:
                        for _ in range(150):
                            run.step({})
                            if fired.called:
                                break
                    call, = fired.call_args_list
                    self.assertIs(call.args[0], bot)
                    self.assertEqual(len(fight.rockets), pellets)
                    bot.firing = False  # leave committed shots in flight
                    for _ in range(90):
                        if not fight.rockets:
                            break
                        run.step({})
                    self.assertFalse(fight.rockets)
                    self.assertEqual(bubble.health, 700 if allied else 700 - damage)
                    pool = fight.vitals[hero.body]
                    if allied and key == "2":
                        self.assertGreaterEqual(pool.total, 200 - damage)
                        self.assertLess(pool.total, 200)
                    else:
                        self.assertEqual(pool.total, 200 - damage if allied else 200)

    def deploy_sigma_cover(self, run, hero, bot, *, allied_to_npc=False):
        from ow174.game import sigma_barrier

        match = run.match
        service = match.combat.sigma_barriers
        if service.geometry is None:
            self.skipTest("manifest-verified Sigma C015 model required")
        self.addCleanup(npc.remove_spawned, match)
        x, y, z = hero.mover.position
        match.collision_world = flat_world(y)
        match.game_map = replace(match.game_map, free_for_all=False)
        team = bot.team if allied_to_npc else hero.team
        sigma = match.add_player(4, "Sigma", sigma_barrier.HERO, team, False)
        in_world(match, sigma)
        sigma.client = NpcFixtureClient(sigma)
        run.players.append(sigma)
        self.assertTrue(match.switch_hero(sigma, heroes()[sigma_barrier.HERO]))
        sigma.team = team
        midpoint = (z + bot.position[2]) * .5
        sigma.mover.position[:] = (x + 1.2, y, midpoint - 2.5)
        sigma.mover.body = None
        for _ in range(20):
            run.step({})
        for _ in range(8):
            run.step({sigma: (0, 0, 2)})
        for _ in range(3):
            run.step({})
        actor, = service.actors.values()
        self.assertEqual((actor.phase, actor.vitals.total), (3, 700))
        fight = match.combat
        origin = (bot.position[0], bot.position[1] + combat.EYE, bot.position[2])
        eye = fight._eye(hero)
        reach = math.dist(origin, eye)
        ray = tuple((end - start) / reach for start, end in zip(origin, eye, strict=True))
        self.assertIsNotNone(service.intersect(actor, origin, ray, reach))
        return actor

    def test_talon_weapon_volley_checks_sigma_dynamic_barrier_faction(self):
        for key, damage, pellets in (("1", 20, 0), ("2", 50, 25), ("3", 100, 1), ("5", 10, 0)):
            for allied in (False, True):
                with self.subTest(npc=self.catalog[key].name, allied=allied):
                    run, hero, bot = self.arena(self.catalog[key], "bow", "hit", distance=7)
                    actor = self.deploy_sigma_cover(run, hero, bot, allied_to_npc=allied)
                    fight = run.match.combat
                    run.step({hero: (*self.target_aim(run, hero, bot), 0)})
                    self.assertEqual(npc_control.start(run.match, hero), bot.entity)
                    with patch.object(fight, "_shoot", wraps=fight._shoot) as fired:
                        for _ in range(150):
                            run.step({})
                            if fired.called:
                                break
                    call, = fired.call_args_list
                    self.assertIs(call.args[0], bot)
                    self.assertEqual(len(fight.rockets), pellets)
                    bot.firing = False
                    for _ in range(90):
                        if not fight.rockets:
                            break
                        run.step({})
                    self.assertFalse(fight.rockets)
                    self.assertEqual(actor.vitals.total, 700 if allied else 700 - damage)
                    pool = fight.vitals[hero.body]
                    if allied and key == "2":
                        self.assertGreaterEqual(pool.total, 200 - damage)
                        self.assertLess(pool.total, 200)
                    else:
                        self.assertEqual(pool.total, 200 - damage if allied else 200)

    def test_talon_assassin_crosses_sigma_dynamic_barrier_without_damage_to_it(self):
        run, hero, bot = self.arena(self.catalog["4"], "bow", "hit", distance=2.5)
        actor = self.deploy_sigma_cover(run, hero, bot)
        fight = run.match.combat
        run.step({hero: (*self.target_aim(run, hero, bot), 0)})
        self.assertEqual(npc_control.start(run.match, hero), bot.entity)
        for _ in range(120):
            run.step({})
            if fight.pins.get(bot.entity) == hero.body:
                break
        self.assertEqual(fight.pins.get(bot.entity), hero.body)
        self.assertEqual(fight.vitals[hero.body].total, 175)
        self.assertEqual(actor.vitals.total, 700)

    def test_actual_enforcer_projectiles_apply_authored_distance_falloff(self):
        for allied in (False, True):
            with self.subTest(allied=allied):
                run, hero, bot = self.arena(self.catalog["2"], "bow", "hit", distance=16)
                bubble = self.deploy_winston_cover(run, hero, bot, allied_to_npc=allied)
                fight = run.match.combat
                run.step({hero: (*self.target_aim(run, hero, bot), 0)})
                self.assertEqual(npc_control.start(run.match, hero), bot.entity)
                for _ in range(150):
                    run.step({})
                    if fight.rockets:
                        break
                pellets = list(fight.rockets)
                self.assertEqual(len(pellets), 25)
                for pellet in pellets:
                    self.assertEqual((pellet.direct.amount, pellet.direct.falloff_start,
                                      pellet.direct.falloff_end), (2, 8, 15))
                    self.assertAlmostEqual(pellet.direct.falloff_floor, 0.2)
                bot.firing = False
                with patch.object(fight, "_damage", wraps=fight._damage) as damage:
                    for _ in range(85):
                        run.step({})
                        if not fight.rockets:
                            break
                self.assertFalse(fight.rockets)
                pool = fight.vitals[hero.body]
                hits = [call.args[2] for call in damage.call_args_list if call.args[1].owner is hero]
                if allied:
                    self.assertEqual(len(hits), 10)
                    for amount in hits:
                        self.assertAlmostEqual(amount, 0.4)
                    self.assertAlmostEqual(pool.total, 196, places=5)
                    self.assertEqual(bubble.health, 700)
                else:
                    self.assertFalse(hits)
                    self.assertEqual(pool.total, 200)
                    self.assertTrue(all(8 < pellet.distance_flown < 15 for pellet in pellets))
                    expected = sum(2 * (1 - 0.8 * (pellet.distance_flown - 8) / 7) for pellet in pellets)
                    self.assertAlmostEqual(bubble.health, 700 - expected, places=5)

    def test_talon_assassin_contact_crosses_winston_sphere_without_draining_it(self):
        run, hero, bot = self.arena(self.catalog["4"], "bow", "hit", distance=2.5)
        bubble = self.deploy_winston_cover(run, hero, bot, forward=-4)
        fight = run.match.combat
        origin = (bot.position[0], bot.position[1] + combat.EYE, bot.position[2])
        eye = fight._eye(hero)
        distance = math.dist(origin, eye)
        ray = tuple((end - start) / distance for start, end in zip(origin, eye, strict=True))
        self.assertIsNotNone(fight.winston_barriers.trace(bot, origin, ray, distance))
        run.step({hero: (*self.target_aim(run, hero, bot), 0)})
        self.assertEqual(npc_control.start(run.match, hero), bot.entity)
        for _ in range(120):
            run.step({})
            if fight.pins.get(bot.entity) == hero.body:
                break
        self.assertEqual(fight.pins.get(bot.entity), hero.body)
        self.assertEqual(fight.vitals[hero.body].total, 175)
        self.assertEqual(bubble.health, 700)

    def deploy_orisa_cover(self, run, hero, bot, *, allied_to_npc):
        orisa_guid = 0x02E000000000013E
        match = run.match
        self.addCleanup(npc.remove_spawned, match)
        fight = match.combat
        service = fight.orisa_barriers
        if service.geometry is None:
            self.skipTest("prepared build 104319 Orisa collision mesh required")
        x, y, z = hero.mover.position
        match.collision_world = flat_world(y)
        match.game_map = replace(match.game_map, free_for_all=False)
        team = bot.team if allied_to_npc else hero.team
        orisa = match.add_player(4, "Orisa", orisa_guid, team, False)
        in_world(match, orisa)
        orisa.client = NpcFixtureClient(orisa)
        run.players.append(orisa)
        self.assertTrue(match.switch_hero(orisa, heroes()[orisa_guid]))
        orisa.team = team
        orisa.mover.position[:] = (x, y, z + 5)
        orisa.mover.body = None
        for _ in range(25):
            run.step({})
        run.step({orisa: (-32768, 8192, 16)})
        for _ in range(60):
            run.step({orisa: (-32768, 8192, 0)})
            if orisa.body in service.actors:
                break
        shield = service.actors[orisa.body]
        self.assertEqual(shield.health, 600)
        orisa.mover.position[0] += 8
        orisa.mover.body = None
        return shield

    def talon_projected_arena(self, key, distance, *, start=True):
        from ow174.game import zarya_barrier

        run, hero, bot = self.arena(self.catalog[key], "bow", "hit", distance=distance)
        match = run.match
        self.addCleanup(npc.remove_spawned, match)
        x, y, z = hero.mover.position
        match.collision_world = flat_world(y)
        match.game_map = replace(match.game_map, free_for_all=False)
        caster = match.add_player(3, "Zarya", zarya_barrier.HERO, hero.team, False)
        in_world(match, caster)
        caster.client = NpcFixtureClient(caster)
        run.players.append(caster)
        self.assertTrue(match.switch_hero(caster, heroes()[zarya_barrier.HERO]))
        caster.team = hero.team
        caster.mover.position[:] = (x + 4, y, z + 2)
        caster.mover.body = None
        for _ in range(25):
            run.step({})
        yaw, pitch = self.target_aim(run, hero, bot)
        run.step({hero: (yaw, pitch, 0)})
        if start:
            self.assertEqual(npc_control.start(match, hero), bot.entity)
        cast = aim(match.combat._eye(caster), (x, y + 1, z))
        return run, hero, bot, caster, cast

    def test_talon_sniper_round_is_stopped_by_zarya_projected_in_flight(self):
        for (slot, definition, damage), protected in product(SNIPER_WEAPONS, (False, True)):
            with self.subTest(slot=slot, protected=protected):
                run, hero, bot, caster, cast = self.talon_projected_arena("3", 20, start=False)
                fight = run.match.combat
                self.aim_sniper_weapon(run, hero, bot, slot)
                self.assertEqual(npc_control.start(run.match, hero), bot.entity)
                for _ in range(140):
                    run.step({})
                    if fight.rockets:
                        break
                shot, = fight.rockets
                self.assertIs(shot.shooter, bot)
                self.assertEqual((shot.shot.definition, shot.source_entity, shot.direct.amount),
                                 (definition, bot.entity, damage))
                self.assertNotIn(caster.body, fight.zarya_barriers.projected)
                run.step({caster: (*cast, 16 if protected else 0)})
                self.assertIn(shot, fight.rockets)
                if protected:
                    self.assertEqual(fight.zarya_barriers.projected[caster.body][1:], (200, hero.body))
                for _ in range(20):
                    run.step({})
                    if shot not in fight.rockets:
                        break
                self.assertNotIn(shot, fight.rockets)
                self.assertEqual(fight.vitals[hero.body].total, 200 if protected else 200 - damage)
                if protected:
                    self.assertEqual(fight.zarya_barriers.projected[caster.body][1:], (200 - damage, hero.body))
                    self.assertEqual(caster.body_script.component.entity_var(87).value(), damage / 5)

    def talon_reinhardt_arena(self, key, distance, *, allied=False):
        geometry = os.environ.get("ORPHEUS_REINHARDT_COLLISION")
        if not geometry:
            self.skipTest("prepared build 104319 Reinhardt mesh required")
        run, hero, bot = self.arena(self.catalog[key], "bow", "hit", distance=distance)
        match = run.match
        self.addCleanup(npc.remove_spawned, match)
        x, y, z = hero.mover.position
        match.collision_world = flat_world(y)
        match.game_map = replace(match.game_map, free_for_all=False)
        team = bot.team if allied else hero.team
        rein = match.add_player(3, "Reinhardt", reinhardt_barrier.HERO, team, False)
        in_world(match, rein)
        rein.client = NpcFixtureClient(rein)
        run.players.append(rein)
        self.assertTrue(match.switch_hero(rein, heroes()[reinhardt_barrier.HERO]))
        rein.team = team
        rein.mover.position[:] = (x + 1.5, y, z + 3)
        rein.mover.body = None
        fight = match.combat
        barriers = fight.barriers
        barriers.geometry = barrier_mesh.load_geometry(Path(geometry))
        for _ in range(25):
            run.step({})
        return run, hero, bot, rein, barriers

    def test_talon_hitscan_respects_reinhardt_cover_for_hanzo(self):
        for key, allied, initial, final_shield, final_hanzo, rays in (
            ("1", False, 1600, 1580, 200, 1),
            ("1", True, 1600, 1600, 180, 1),
            ("5", False, 4, 0, 195, 2),
        ):
            with self.subTest(npc=key, allied=allied):
                run, hero, bot, rein, barriers = self.talon_reinhardt_arena(key, 8, allied=allied)
                match = run.match
                fight = match.combat
                shield_input = {rein: (0, 0, 2)}
                for _ in range(5):
                    run.step(shield_input)
                shield = barriers.states[rein.body]
                self.assertTrue(shield.active)
                self.assertEqual(shield.current, 1600)
                if initial < 1600:
                    barriers.damage(rein, 1600 - initial)
                yaw, pitch = self.target_aim(run, hero, bot)
                run.step({**shield_input, hero: (yaw, pitch, 0)})
                self.assertEqual(npc_control.start(match, hero), bot.entity)
                with patch.object(fight, "_hitscan", wraps=fight._hitscan) as hitscan:
                    for _ in range(110):
                        run.step(shield_input)
                        if hitscan.called:
                            break
                self.assertEqual(hitscan.call_count, rays)
                shooter, _, origin, direction, _, reach, _ = hitscan.call_args.args
                self.assertIs(shooter, bot)
                self.assertIsNotNone(barriers.intersect(rein, origin, direction, reach))
                self.assertEqual(shield.current, final_shield)
                self.assertEqual(fight.vitals[hero.body].total, final_hanzo)
                self.assertEqual(fight.vitals[rein.body].total, 500)

    def test_talon_sniper_projectile_meets_reinhardt_cover_raised_in_flight(self):
        for (slot, definition, damage), allied in product(SNIPER_WEAPONS, (False, True)):
            with self.subTest(slot=slot, allied=allied):
                run, hero, bot, rein, barriers = self.talon_reinhardt_arena("3", 32, allied=allied)
                fight = run.match.combat
                self.aim_sniper_weapon(run, hero, bot, slot)
                self.assertEqual(npc_control.start(run.match, hero), bot.entity)
                for _ in range(140):
                    run.step({})
                    if fight.rockets:
                        break
                shot, = fight.rockets
                self.assertIs(shot.shooter, bot)
                self.assertEqual((shot.shot.definition, shot.source_entity, shot.direct.amount),
                                 (definition, bot.entity, damage))
                shield_input = {rein: (0, 0, 2)}
                shield = barriers.states[rein.body]
                self.assertFalse(shield.active)
                for _ in range(8):
                    run.step(shield_input)
                    if shield.active:
                        break
                self.assertTrue(shield.active)
                self.assertIn(shot, fight.rockets)
                self.assertIsNotNone(barriers.intersect(rein, shot.position, shot.direction, 32))
                for _ in range(20):
                    run.step(shield_input)
                    if shot not in fight.rockets:
                        break
                self.assertNotIn(shot, fight.rockets)
                self.assertEqual(shield.current, 1600 if allied else 1600 - damage)
                self.assertEqual(fight.vitals[hero.body].total, 200 - damage if allied else 200)
                self.assertEqual(fight.vitals[rein.body].total, 500)

    def test_talon_enforcer_projectiles_break_or_drain_reinhardt_cover(self):
        for initial, final_shield, final_hanzo in ((1600, 1550, 200), (25, 0, 176)):
            with self.subTest(initial=initial):
                run, hero, bot, rein, barriers = self.talon_reinhardt_arena("2", 5)
                fight = run.match.combat
                shield_input = {rein: (0, 0, 2)}
                for _ in range(5):
                    run.step(shield_input)
                shield = barriers.states[rein.body]
                self.assertTrue(shield.active)
                if initial < 1600:
                    barriers.damage(rein, 1600 - initial)
                yaw, pitch = self.target_aim(run, hero, bot)
                run.step({**shield_input, hero: (yaw, pitch, 0)})
                self.assertEqual(npc_control.start(run.match, hero), bot.entity)
                for _ in range(200):
                    run.step(shield_input)
                    if fight.rockets:
                        break
                pellets = tuple(fight.rockets)
                self.assertEqual(len(pellets), 25)
                self.assertTrue(all(shot.shooter is bot and shot.direct.amount == 2 for shot in pellets))
                for _ in range(20):
                    run.step(shield_input)
                    if not fight.rockets:
                        break
                self.assertFalse(fight.rockets)
                self.assertEqual(shield.current, final_shield)
                self.assertEqual(fight.vitals[hero.body].total, final_hanzo)
                self.assertEqual(fight.vitals[rein.body].total, 500)

    def test_talon_hitscan_drains_zarya_projected_cover_without_spill(self):
        for key, rays, amount, initial, shield_hp, body_hp in (
            ("1", 1, 20, 200, 180, 200), ("1", 1, 20, 15, 0, 200),
            ("5", 2, 5, 200, 190, 200), ("5", 2, 5, 4, 0, 195),
        ):
            with self.subTest(npc=key, initial=initial):
                run, hero, bot, caster, cast = self.talon_projected_arena(key, 8)
                fight = run.match.combat
                barriers = fight.zarya_barriers
                run.step({caster: (*cast, 16)})
                self.assertEqual(barriers.projected[caster.body][1:], (200, hero.body))
                if initial < 200:
                    barriers.damage(hero, 200 - initial, bot.entity)
                with patch.object(fight, "_hitscan", wraps=fight._hitscan) as hitscan:
                    for _ in range(110):
                        run.step({})
                        if hitscan.called:
                            break
                self.assertEqual(hitscan.call_count, rays)
                self.assertTrue(all(call.args[0] is bot and call.args[4].amount == amount
                                    for call in hitscan.call_args_list))
                self.assertEqual(barriers.projected.get(caster.body, (None, 0))[1], shield_hp)
                self.assertEqual(fight.vitals[hero.body].total, body_hp)
                self.assertFalse(fight.rockets)

    def test_talon_trooper_hitscan_drains_nearest_of_orisa_and_projected_cover(self):
        for offset, orisa_hp, zarya_hp in ((3, 580, 200), (0.2, 600, 180)):
            with self.subTest(orisa_offset=offset):
                run, hero, bot, caster, cast = self.talon_projected_arena("1", 8, start=False)
                shield = self.deploy_orisa_cover(run, hero, bot, allied_to_npc=False)
                x, y, _ = shield.position
                shield.position = (x, y, hero.mover.position[2] + offset)
                fight = run.match.combat
                barriers = fight.zarya_barriers
                run.step({caster: (*cast, 16)})
                self.assertEqual(barriers.projected[caster.body][1:], (200, hero.body))
                self.assertEqual(npc_control.start(run.match, hero), bot.entity)
                with patch.object(fight, "_hitscan", wraps=fight._hitscan) as hitscan:
                    for _ in range(110):
                        run.step({})
                        if hitscan.called:
                            break
                hitscan.assert_called_once()
                _, _, origin, direction, direct, reach, _ = hitscan.call_args.args
                self.assertEqual(direct.amount, 20)
                orisa_contact = fight.orisa_barriers.intersect(shield, origin, direction, reach)
                zarya_contact = barriers.trace(bot, origin, direction, reach)
                self.assertIsNotNone(orisa_contact)
                self.assertIsNotNone(zarya_contact)
                self.assertEqual(orisa_contact < zarya_contact[0], orisa_hp < 600)
                self.assertEqual(shield.health, orisa_hp)
                self.assertEqual(barriers.projected[caster.body][1:], (zarya_hp, hero.body))
                self.assertEqual(fight.vitals[hero.body].total, 200)

    def test_helix_direct_and_ground_blast_against_projected_cover(self):
        impacts = (
            (None, False, 0), (None, False, 200), (None, False, 100),
            (1.8, False, 200), (1.8, False, 30), (1.8, True, 200), (6, False, 200),
        )
        for visor_active, (offset, blocked, shield_hp) in product((False, True), impacts):
            with self.subTest(visor=visor_active, offset=offset, blocked=blocked, shield_hp=shield_hp):
                run, hero, bot, caster, cast = self.talon_projected_arena("1", 8, start=False)
                match = run.match
                fight = match.combat
                barriers = fight.zarya_barriers
                attacker = match.add_player(4, "Soldier", SOLDIER, bot.team, False)
                in_world(match, attacker)
                attacker.client = NpcFixtureClient(attacker)
                run.players.append(attacker)
                self.assertTrue(match.switch_hero(attacker, heroes()[SOLDIER]))
                x, y, z = hero.mover.position
                attacker.mover.position[:] = (x - 8, y, z)
                attacker.mover.body = None
                point = (x, y + 1, z) if offset is None else (x - offset, y, z)
                if blocked:
                    wall_x = x - 1.65
                    match.collision_world = World(
                        box(x - 20, x + 20, y - 1, y, z - 20, z + 20)
                        + wall(wall_x, z - 5, wall_x, z + 5, y0=y, y1=y + 5)
                    )
                angles = aim(fight._eye(attacker), point)
                if visor_active:
                    script = attacker.body_script
                    script.ult_seconds = 0
                    script.set_ult_charge(script.component.entity_var(combat.ULT_COST).value())
                    visor = next(i for i in script.component.instances.values() if i.graph.index == 0x0259)
                    run.step({attacker: (*angles, 32)})
                    self.assertTrue(visor.states[0].flags & 1)
                    self.assertEqual(script.component.entity_var(combat.ULT_CHARGE).value(), 0)
                    for _ in range(100):
                        run.step({})
                run.step({caster: (*cast, 16 if shield_hp else 0)})
                if shield_hp:
                    self.assertEqual(barriers.projected[caster.body][1:], (200, hero.body))
                    if shield_hp < 200:
                        barriers.damage(hero, 200 - shield_hp, attacker.body)
                    # Keep the caster beside the impact, outside the recipient's shell.
                    caster.mover.position[:] = (x - 1.5, y, z + 1.6)
                    caster.mover.body = None
                    caster_target = next(t for t in fight._targets() if t.owner is caster)
                    caster_pool = fight.vitals_of(caster_target)
                    caster_before = caster_pool.total
                run.step({attacker: (*angles, 2)})
                rocket, = fight.rockets
                definition = 0x0400000000001C86 if visor_active else 0x040000000000072C
                self.assertEqual(rocket.shot.definition, definition)
                self.assertEqual((rocket.direct.amount, rocket.blast.amount), (40, 80))
                if offset is None:
                    contact = barriers.intersect(rocket.position, rocket.direction, barriers.centre(hero))
                    self.assertIsNotNone(contact)
                    shell_x = rocket.position[0] + contact * rocket.direction[0]
                for _ in range(30):
                    if rocket not in fight.rockets:
                        break
                    run.step({})
                self.assertNotIn(rocket, fight.rockets)
                self.assertEqual(fight.vitals[hero.body].total, 200 if shield_hp else 80)
                remaining = barriers.projected.get(caster.body, (None, 0))[1]
                if offset is not None:
                    self.assertAlmostEqual(rocket.position[1], y, places=4)
                    self.assertAlmostEqual(rocket.position[0], point[0], places=2)
                    distance = max(0, math.dist(rocket.position, barriers.centre(hero)) - 1.5)
                    loss = 0 if blocked else 80 * rocket.blast.splash(distance)
                    self.assertEqual(loss > 0, not blocked and offset < 6)
                    self.assertAlmostEqual(remaining, max(0, shield_hp - loss), places=4)
                elif shield_hp:
                    self.assertAlmostEqual(rocket.position[0], shell_x, places=4)
                    self.assertEqual(remaining, max(0, shield_hp - 120))
                else:
                    self.assertGreater(rocket.position[0], shell_x)
                if shield_hp:
                    caster_target = next(t for t in fight._targets() if t.owner is caster)
                    a, b = caster_target.segment()
                    distance = max(0, combat.segment_distance(rocket.position, a, b) - caster_target.radius)
                    splash = 0 if blocked else 80 * rocket.blast.splash(distance)
                    self.assertEqual(splash > 0, not blocked and offset != 6)
                    self.assertAlmostEqual(caster_pool.total, caster_before - splash, places=4)
                    self.assertEqual(len(attacker.client.messages(combat.HIT)), int(splash > 0))

                body_before = fight.vitals[hero.body].total
                rifle_aim = aim(fight._eye(attacker), (x, y + 1, z))
                with patch.object(fight, "_hitscan", wraps=fight._hitscan) as rifle:
                    # Try fresh presses through the Helix recovery window.
                    for frame in range(40):
                        run.step({attacker: (*rifle_aim, int(frame % 2 == 0))})
                        if rifle.called:
                            break
                rifle.assert_called_once()
                self.assertIs(rifle.call_args.args[0], attacker)
                self.assertEqual(rifle.call_args.args[4].amount, 20)
                rifle_damage = 0 if blocked else 20
                expected_body = body_before if remaining else body_before - rifle_damage
                self.assertAlmostEqual(fight.vitals[hero.body].total, expected_body, places=4)
                self.assertAlmostEqual(
                    barriers.projected.get(caster.body, (None, 0))[1], max(0, remaining - rifle_damage),
                    places=4,
                )
                if visor_active:
                    self.assertTrue(visor.states[0].flags & 1)

    def test_enemy_flashbang_against_projected_cover(self):
        for shield_hp, final_body, final_shield in ((0, 155, 0), (200, 200, 155), (15, 180, 0)):
            with self.subTest(shield_hp=shield_hp):
                run, hero, bot, caster, cast = self.talon_projected_arena("1", 8, start=False)
                match = run.match
                fight = match.combat
                barriers = fight.zarya_barriers
                mccree = 0x02E0000000000042
                attacker = match.add_player(4, "McCree", mccree, bot.team, False)
                in_world(match, attacker)
                attacker.client = NpcFixtureClient(attacker)
                run.players.append(attacker)
                self.assertTrue(match.switch_hero(attacker, heroes()[mccree]))
                x, y, z = hero.mover.position
                attacker.mover.position[:] = (x - 3, y, z)
                attacker.mover.body = None
                flash = aim(fight._eye(attacker), (x, y + 1, z))
                yaw, pitch = self.target_aim(run, hero, bot)
                draw = {hero: (yaw, pitch, 1)}
                for _ in range(70):
                    run.step(draw)
                self.assertIn(20, hero.body_script.component.held)
                run.step({**draw, caster: (*cast, 16 if shield_hp else 0)})
                if shield_hp:
                    self.assertEqual(barriers.projected[caster.body][1:], (200, hero.body))
                    if shield_hp < 200:
                        barriers.damage(hero, 200 - shield_hp, bot.entity)
                run.step({**draw, attacker: (*flash, 16)})
                for _ in range(35):
                    if fight.rockets:
                        break
                    run.step(draw)
                grenade, = fight.rockets
                self.assertIs(grenade.shooter, attacker)
                self.assertEqual((grenade.blast.amount, grenade.blast.effect), (25, combat.FLASHBANG_EFFECT))
                for _ in range(25):
                    if grenade not in fight.rockets:
                        break
                    run.step(draw)
                self.assertNotIn(grenade, fight.rockets)
                self.assertEqual(fight.is_stunned(hero), shield_hp == 0)
                self.assertEqual(20 in hero.body_script.component.held, bool(shield_hp))
                self.assertEqual(fight.vitals[hero.body].total, 200 if shield_hp else 175)
                self.assertEqual(barriers.projected.get(caster.body, (None, 0))[1], max(0, shield_hp - 25))
                commands = draw if shield_hp else {hero: (yaw, pitch, 0)}
                run.step(commands)
                self.assertEqual(npc_control.start(match, hero), bot.entity)
                with patch.object(fight, "_hitscan", wraps=fight._hitscan) as hitscan:
                    for _ in range(110):
                        run.step(commands)
                        if hitscan.called:
                            break
                hitscan.assert_called_once()
                self.assertEqual(fight.vitals[hero.body].total, final_body)
                self.assertEqual(barriers.projected.get(caster.body, (None, 0))[1], final_shield)
                run.step({hero: (yaw, pitch, 0)})
                if shield_hp:
                    arrow, = fight.rockets
                    self.assertIs(arrow.shooter, hero)
                    self.assertEqual(arrow.direct.amount, 125)
                else:
                    self.assertFalse(fight.rockets)

    def test_talon_enforcer_volley_breaks_or_drains_zarya_projected_cover(self):
        for initial, expected_shield, expected_body in ((200, 150, 200), (25, 0, 176)):
            with self.subTest(initial_shield=initial):
                run, hero, bot, caster, cast = self.talon_projected_arena("2", 5)
                fight = run.match.combat
                barriers = fight.zarya_barriers
                for _ in range(200):
                    run.step({})
                    if fight.rockets:
                        break
                pellets = list(fight.rockets)
                self.assertEqual(len(pellets), 25)
                self.assertTrue(all(shot.shooter is bot and shot.source_entity == bot.entity
                                    and shot.direct.amount == 2 for shot in pellets))
                run.step({caster: (*cast, 16)})
                self.assertEqual(fight.rockets, pellets)
                self.assertEqual(barriers.projected[caster.body][1:], (200, hero.body))
                if initial < 200:
                    barriers.damage(hero, 200 - initial, bot.entity)
                for _ in range(20):
                    run.step({})
                    if not fight.rockets:
                        break
                self.assertFalse(fight.rockets)
                self.assertEqual(barriers.projected.get(caster.body, (None, 0))[1], expected_shield)
                self.assertEqual(fight.vitals[hero.body].total, expected_body)

    def test_talon_sniper_round_meets_actual_deflect_or_grasp_raised_in_flight(self):
        from ow174.game import arrow_deflection, matrix, sigma_grasp

        tags = os.environ.get("ORPHEUS_MATRIX_TAGS")
        if not tags:
            self.skipTest("prepared build 104319 projectile tags required")
        defenders = ((arrow_deflection.GENJI, 16), (sigma_grasp.SIGMA, 8))
        for slot, definition, damage in SNIPER_WEAPONS:
            for (defender_guid, button), active in product(defenders, (False, True)):
                with self.subTest(slot=slot, defender=defender_guid, active=active):
                    run, hero, bot = self.arena(self.catalog["3"], "bow", "hit", distance=32)
                    match = run.match
                    self.addCleanup(npc.remove_spawned, match)
                    x, y, z = hero.mover.position
                    match.collision_world = flat_world(y)
                    match.game_map = replace(match.game_map, free_for_all=False)
                    defender = match.add_player(3, "Defender", defender_guid, hero.team, False)
                    in_world(match, defender)
                    defender.client = NpcFixtureClient(defender)
                    run.players.append(defender)
                    self.assertTrue(match.switch_hero(defender, heroes()[defender_guid]))
                    defender.team = hero.team
                    defender.mover.position[:] = (x + 0.8, y, z + 5)
                    defender.mover.body = None
                    fight = match.combat
                    fight.matrix_definitions = matrix.load_admitted_definitions(Path(tags))
                    for _ in range(30):
                        run.step({})
                    self.aim_sniper_weapon(run, hero, bot, slot)
                    self.assertEqual(npc_control.start(match, hero), bot.entity)
                    for _ in range(150):
                        run.step({})
                        if fight.rockets:
                            break
                    shot, = fight.rockets
                    self.assertIs(shot.shooter, bot)
                    lease = shot.shot
                    self.assertEqual((lease.definition, shot.direct.amount), (definition, damage))
                    self.assertTrue(shot.direct.deflectable)
                    self.assertTrue(shot.matrix_absorbable)
                    bot.firing = False  # release the input while preserving the committed shot
                    before = fight.vitals[bot.entity].total
                    defender.mover.position[0] = x
                    defender.mover.body = None
                    run.step({defender: (0, 0, button if active else 0)})
                    self.assertIn(shot, fight.rockets)
                    target = next(t for t in fight._targets() if t.owner is defender)
                    defender_pool = fight.vitals_of(target)
                    defender_before = defender_pool.total
                    if defender_guid == arrow_deflection.GENJI:
                        contact = arrow_deflection.trace(target, shot.position, shot.direction, 32)
                    else:
                        contact = fight.grasp.trace(target, shot.position, shot.direction, 32)
                    self.assertEqual(contact is not None, active)
                    for _ in range(45):
                        run.step({})
                        if shot not in fight.rockets:
                            break
                    self.assertNotIn(shot, fight.rockets)
                    hanzo_target = next(t for t in fight._targets() if t.owner is hero)
                    self.assertEqual(fight.vitals_of(hanzo_target).total, 200)
                    self.assertEqual(defender_pool.total, defender_before if active else defender_before - damage)
                    if active:
                        self.assertIsNone(shot.shot)
                        self.assertNotIn(lease, fight.projectiles.shots)
                    if active and defender_guid == arrow_deflection.GENJI:
                        self.assertEqual(shot.source_entity, defender.body)
                        self.assertEqual(fight.vitals[bot.entity].total, before - damage)
                        hit, = defender.client.messages(combat.HIT)
                        self.assertEqual(hit["+0x78"]["+0x4"], damage)
                    elif active:
                        self.assertEqual(shot.source_entity, bot.entity)
                        self.assertEqual(fight.vitals[bot.entity].total, before)
                        instance = next(i for i in defender.body_script.component.instances.values()
                                        if i.graph.index == sigma_grasp.GRAPH)
                        self.assertEqual(instance.find_var(0, 21312).value(), damage)
                        for _ in range(160):
                            run.step({})
                            if defender_pool.bonus_shields:
                                break
                        self.assertAlmostEqual(defender_pool.bonus_shields, damage * 0.6, places=4)

    def test_talon_sniper_round_hits_orisa_mesh_only_when_hostile_to_shield(self):
        for (slot, definition, damage), shield_allied_to_sniper in product(SNIPER_WEAPONS, (False, True)):
            with self.subTest(slot=slot, shield_allied_to_sniper=shield_allied_to_sniper):
                run, hero, bot = self.arena(self.catalog["3"], "bow", "hit", distance=20)
                shield = self.deploy_orisa_cover(run, hero, bot, allied_to_npc=shield_allied_to_sniper)
                match = run.match
                fight = match.combat
                service = fight.orisa_barriers
                self.aim_sniper_weapon(run, hero, bot, slot)
                self.assertEqual(npc_control.start(match, hero), bot.entity)
                target = next(t for t in fight._targets() if t.owner is hero)
                pool = fight.vitals_of(target)
                for _ in range(140):
                    run.step({})
                    if fight.rockets:
                        break
                shot, = fight.rockets
                self.assertIs(shot.shooter, bot)
                self.assertEqual((shot.shot.definition, shot.source_entity, shot.direct.amount),
                                 (definition, bot.entity, damage))
                self.assertIsNotNone(service.intersect(shield, shot.position, shot.direction, 30))
                for _ in range(20):
                    run.step({})
                    if shot not in fight.rockets:
                        break
                self.assertNotIn(shot, fight.rockets)
                self.assertEqual(shield.health, 600 if shield_allied_to_sniper else 600 - damage)
                self.assertEqual(pool.total, 200 - damage if shield_allied_to_sniper else 200)

    def test_talon_enforcer_first_volley_respects_orisa_cover_faction(self):
        for allied_to_npc, initial_hp, final_hp, hanzo_hp in (
            (False, 600, 550, 200), (True, 600, 600, 150), (False, 25, 0, 176),
        ):
            with self.subTest(allied_to_npc=allied_to_npc, initial_hp=initial_hp):
                run, hero, bot = self.arena(self.catalog["2"], "bow", "hit")
                shield = self.deploy_orisa_cover(run, hero, bot, allied_to_npc=allied_to_npc)
                fight = run.match.combat
                if initial_hp < 600:
                    fight.orisa_barriers.damage(shield, 600 - initial_hp)
                yaw, pitch = self.target_aim(run, hero, bot)
                run.step({hero: (yaw, pitch, 0)})
                self.assertEqual(npc_control.start(run.match, hero), bot.entity)
                with patch.object(fight.projectiles, "fired", wraps=fight.projectiles.fired) as fired:
                    for _ in range(200):
                        run.step({})
                        if fight.rockets:
                            break
                    pellets = [shot for shot in fight.rockets if shot.shooter is bot]
                    self.assertEqual(len(pellets), 25)
                    self.assertTrue(all(shot.source_entity == bot.entity for shot in pellets))
                    self.assertEqual({shot.direct.amount for shot in pellets}, {2})
                    for _ in range(20):
                        run.step({})
                        if not fight.rockets:
                            break
                    self.assertFalse(fight.rockets)
                    self.assertEqual(fired.call_count, 25)
                    self.assertEqual({(call.args[1].instance.graph.index, call.args[1].node.pos)
                                      for call in fired.call_args_list}, {(0x13EB, 24)})
                self.assertEqual(shield.health, final_hp)
                self.assertEqual(fight.vitals[hero.body].total, hanzo_hp)

    def test_talon_trooper_hitscan_respects_orisa_cover_and_next_round_after_break(self):
        for allied_to_npc, hero_in_front, initial_hp, first_hp, second_hp, shield_hp in (
            (False, False, 600, 200, 200, 560),
            (True, False, 600, 180, 160, 600),
            (False, False, 15, 200, 180, 0),
            (False, True, 600, 180, 160, 600),
        ):
            with self.subTest(allied_to_npc=allied_to_npc, hero_in_front=hero_in_front,
                              initial_hp=initial_hp):
                run, hero, bot = self.arena(self.catalog["1"], "bow", "hit", distance=8)
                shield = self.deploy_orisa_cover(run, hero, bot, allied_to_npc=allied_to_npc)
                fight = run.match.combat
                if initial_hp < 600:
                    fight.orisa_barriers.damage(shield, 600 - initial_hp)
                if hero_in_front:
                    hero.mover.position[2] = shield.position[2] + 1.5
                    hero.mover.body = None
                yaw, pitch = self.target_aim(run, hero, bot)
                run.step({hero: (yaw, pitch, 0)})
                self.assertEqual(npc_control.start(run.match, hero), bot.entity)
                with (patch.object(fight, "_shoot", wraps=fight._shoot) as fire,
                      patch.object(fight, "_hitscan", wraps=fight._hitscan) as hitscan):
                    for count, expected_hp in enumerate((first_hp, second_hp), start=1):
                        for _ in range(200):
                            run.step({})
                            if hitscan.call_count >= count:
                                break
                        self.assertEqual(hitscan.call_count, count)
                        state = fire.call_args.args[2]
                        self.assertEqual((state.instance.graph.index, state.node.pos), (0x102E, 60))
                        shooter, _, origin, direction, direct, reach, _ = hitscan.call_args.args
                        self.assertIs(shooter, bot)
                        self.assertEqual(direct.amount, 20)
                        self.assertIsNotNone(fight.orisa_barriers.intersect(shield, origin, direction, reach))
                        self.assertEqual(fight.vitals[hero.body].total, expected_hp)
                self.assertFalse(fight.rockets)
                self.assertEqual(shield.health, shield_hp)

    def test_talon_heavy_first_hitscan_shot_respects_orisa_cover_faction(self):
        for allied_to_npc, initial_hp, final_hp, hanzo_hp in (
            (False, 600, 590, 200), (True, 600, 600, 190), (False, 4, 0, 195),
        ):
            with self.subTest(allied_to_npc=allied_to_npc, initial_hp=initial_hp):
                run, hero, bot = self.arena(self.catalog["5"], "bow", "hit", distance=8)
                shield = self.deploy_orisa_cover(run, hero, bot, allied_to_npc=allied_to_npc)
                fight = run.match.combat
                if initial_hp < 600:
                    fight.orisa_barriers.damage(shield, 600 - initial_hp)
                yaw, pitch = self.target_aim(run, hero, bot)
                run.step({hero: (yaw, pitch, 0)})
                self.assertEqual(npc_control.start(run.match, hero), bot.entity)
                with (patch.object(fight, "_shoot", wraps=fight._shoot) as fire,
                      patch.object(fight, "_hitscan", wraps=fight._hitscan) as hitscan):
                    for _ in range(200):
                        run.step({})
                        if hitscan.called:
                            break
                state = fire.call_args.args[2]
                self.assertEqual((state.instance.graph.index, state.node.pos), (0x114F, 47))
                self.assertEqual(hitscan.call_count, 2)
                for call in hitscan.call_args_list:
                    shooter, _, origin, direction, direct, reach, _ = call.args
                    self.assertIs(shooter, bot)
                    self.assertIsNotNone(fight.orisa_barriers.intersect(shield, origin, direction, reach))
                    self.assertEqual(direct.amount, 5)
                self.assertFalse(fight.rockets)
                self.assertEqual(shield.health, final_hp)
                self.assertEqual(fight.vitals[hero.body].total, hanzo_hp)

    def test_sniper_death_cancels_uncommitted_fire_and_preserves_a_launched_round(self):
        first_launch_frames = {1: 126, 2: 30}
        for (slot, definition, damage), committed in product(SNIPER_WEAPONS, (False, True)):
            with self.subTest(slot=slot, committed=committed):
                run, hero, bot = self.arena(self.catalog["3"], "bow", "hit", distance=20)
                self.addCleanup(npc.remove_spawned, run.match)
                fight = run.match.combat
                targets = {target.entity: target for target in fight._targets()}
                hero_pool = fight.vitals_of(targets[hero.body])
                sniper_pool = fight.vitals_of(targets[bot.entity])
                hero_pool.damage(200 - damage)
                sniper_pool.damage(sniper_pool.total - 125)
                self.assertEqual((hero_pool.total, sniper_pool.total), (damage, 125))
                angles = self.target_aim(run, hero, bot)
                for _ in range(70):
                    run.step({hero: (*angles, 1)})
                self.aim_sniper_weapon(run, hero, bot, slot, hanzo_buttons=1)
                self.assertEqual(npc_control.start(run.match, hero), bot.entity)
                old_hero = hero.body

                with patch.object(fight.projectiles, "fired", wraps=fight.projectiles.fired) as launch:
                    if committed:
                        for _ in range(first_launch_frames[slot] - 7):
                            run.step({hero: (*angles, 1)})
                    self.assertFalse(launch.called)
                    run.step({hero: (*angles, 0)})
                    arrow, = fight.rockets
                    self.assertIs(arrow.shooter, hero)
                    self.assertEqual(arrow.direct.amount, 125)
                    self.assertTrue(bot.attack_input)
                    for _ in range(18):
                        if bot.dead:
                            break
                        run.step({})
                    self.assertTrue(bot.dead)
                    self.assertEqual(sniper_pool.killer, old_hero)
                    self.assertEqual(hero_pool.total, damage)
                    if committed:
                        shot, = fight.rockets
                        self.assertEqual((shot.shot.definition, shot.source_entity, shot.direct.amount),
                                         (definition, bot.entity, damage))
                    else:
                        self.assertFalse(fight.rockets)
                    for _ in range(35):
                        run.step({})
                    self.assertEqual(hero_pool.dead, committed)
                    self.assertEqual(hero_pool.killer, bot.entity if committed else None)
                    self.assertFalse(fight.rockets)
                    self.assertFalse(bot.attack_input)
                    npc_shots = [call.args[3] for call in launch.call_args_list if call.args[0] is bot]
                    self.assertEqual(npc_shots, [definition] if committed else [])
                    self.assertEqual([entity for entity, _ in hero.client.npc_deaths], [bot.entity])

    def test_launched_sniper_round_hits_hanzos_replacement_body(self):
        for slot, projectile, damage in SNIPER_WEAPONS:
            with self.subTest(slot=slot):
                definition = self.catalog["3"]
                run, hero, far = self.arena(definition, "bow", "hit", distance=75)
                self.addCleanup(npc.remove_spawned, run.match)
                fight = run.match.combat
                x, y, z = origin = tuple(hero.mover.position)
                run.match.collision_world = World(box(x - 20, x + 20, y - 1, y, z - 20, z + 90))
                near = npc.spawn(run.match, definition, (x + 5, y, z), team=far.team)
                old = hero.body
                target = next(t for t in fight._targets() if t.owner is hero)
                old_pool = fight.vitals_of(target)
                old_pool.damage(200 - damage)  # Only the first life is vulnerable to one round.
                self.aim_sniper_weapon(run, hero, far, slot)
                self.assertEqual(npc_control.start(run.match, hero), far.entity)
                for _ in range(5):
                    run.step({})
                self.aim_sniper_weapon(run, hero, near, slot)
                self.assertEqual(npc_control.start(run.match, hero), near.entity)

                with patch.object(combat, "respawn_seconds", return_value=2 * combat.TICK):
                    for _ in range(150):
                        run.step({})
                        if fight.rockets:
                            break
                    round_, = fight.rockets
                    self.assertIs(round_.shooter, far)
                    self.assertEqual(round_.speed, 200)
                    self.assertEqual(round_.source_entity, far.entity)
                    self.assertEqual(old_pool.total, damage)
                    self.assertEqual((round_.shot.definition, round_.direct.amount), (projectile, damage))
                    far.firing = False  # preserve the distant round without another launch
                    for _ in range(14):
                        run.step({})
                        if old_pool.dead:
                            break
                    self.assertTrue(old_pool.dead)
                    self.assertEqual(old_pool.killer, near.entity)
                    near.firing = False
                    self.assertEqual(hero.body, old)
                    self.assertTrue(round_ in fight.rockets)
                    for _ in range(5):
                        run.step({})
                        if hero.body != old:
                            break

                self.assertNotEqual(hero.body, old)
                self.assertTrue(round_ in fight.rockets)
                target = next(t for t in fight._targets() if t.owner is hero)
                fresh_pool = fight.vitals_of(target)
                self.assertEqual(fresh_pool.total, 200)
                hero.mover.position[:] = origin
                hero.mover.body = None
                for _ in range(30):
                    run.step({})
                    if round_ not in fight.rockets:
                        break
                self.assertFalse(round_ in fight.rockets)
                self.assertEqual(fresh_pool.total, 200 - damage)
                self.assertEqual(old_pool.total, 0)
                self.assertEqual(old_pool.killer, near.entity)

    def test_enforcer_authored_shield_recovery_rearms_after_hanzo_melee_and_restores_only_shields(self):
        run, hero, bot = self.arena(self.catalog["2"], "melee", "hit", distance=2)
        self.addCleanup(npc.remove_spawned, run.match)
        fight = run.match.combat
        pool = fight.vitals[bot.entity]
        component = bot.body_script.component
        recovery = next(i for i in component.instances.values() if i.graph.index == 0x0E62)
        wait = recovery.node_state(recovery.graph.node(52))
        regen = recovery.node_state(recovery.graph.node(51))
        shield = combat.SHIELDS_POOL
        angles = self.target_aim(run, hero, bot)

        run.step({hero: (*angles, 0x800)})
        run.step({})
        self.assertEqual(pool.now[shield], 70)
        self.assertTrue(wait.active)
        self.assertEqual(wait.ms, 3000)
        first_deadline = wait.start + wait.ms
        for _ in range(80):
            run.step({})
        run.step({hero: (*angles, 0x800)})
        run.step({})
        self.assertEqual(pool.now[shield], 40)
        deadline = wait.start + wait.ms
        self.assertGreater(deadline, first_deadline)
        while component.now + component.frame_ms < deadline:
            run.step({})
            self.assertEqual(pool.now[shield], 40)
        for _ in range(3):
            run.step({})
            if pool.now[shield] > 40:
                break
        self.assertFalse(wait.active)
        self.assertTrue(regen.active)
        self.assertAlmostEqual(pool.now[shield], 40 + .03 * (component.now - deadline), places=5)
        script = hero.body_script
        charge = script.component.entity_var(combat.ULT_CHARGE).value()
        passive_ms = script.passive_charge_ms
        elapsed = run.frame
        for _ in range(145):
            run.step({})
        self.assertEqual(pool.now[shield], 100)
        self.assertEqual(pool.now[combat.HEALTH_POOL], 250)
        self.assertFalse(regen.active)
        awards, remainder = divmod(passive_ms + (run.frame - elapsed) * component.frame_ms, 1000)
        self.assertEqual(script.component.entity_var(combat.ULT_CHARGE).value() - charge, awards * 5)
        self.assertEqual(script.passive_charge_ms, remainder)
        for _ in range(70):
            run.step({hero: (*angles, 1)})
        run.step({hero: (*angles, 0)})
        for _ in range(8):
            run.step({})
        self.assertEqual(pool.now[shield], 0)
        self.assertEqual(pool.now[combat.HEALTH_POOL], 225)
        self.assertTrue(wait.active)
        for _ in range(440):
            run.step({})
            if pool.now[shield] == 100:
                break
        self.assertEqual(pool.now[shield], 100)
        self.assertEqual(pool.now[combat.HEALTH_POOL], 225)

    def test_projected_zarya_barrier_orders_hanzo_attacks_against_all_five_talon_bodies(self):
        from ow174.game import zarya_barrier
        from test_game_hanzo_dragons import prepared_profile
        from test_game_hanzo_sonic import profile

        for definition, action in product(self.catalog.values(), INPUTS):
            with self.subTest(npc=definition.name, action=action):
                run, hero, bot = self.arena(definition, action, "hit")
                match = run.match
                self.addCleanup(npc.remove_spawned, match)
                match.game_map = replace(match.game_map, free_for_all=False)
                fight = match.combat
                if action == "sonic":
                    fight.configure_sonic(profile())
                if action == "storm":
                    fight.configure_dragonstrike(prepared_profile())
                target = next(t for t in fight._targets() if t.owner is bot)
                pool = fight.vitals_of(target)
                before = pool.total
                caster = match.add_player(3, "Zarya", zarya_barrier.HERO, bot.team, False)
                in_world(match, caster)
                caster.client = NpcFixtureClient(caster)
                run.players.append(caster)
                self.assertTrue(match.switch_hero(caster, heroes()[zarya_barrier.HERO]))
                caster.team = bot.team
                x, y, z = target.feet
                caster.mover.position[:] = (x + 4, y, z)
                caster.mover.body = None
                for _ in range(30):
                    run.step({})
                angles = self.target_aim(run, hero, bot)
                cast = aim(fight._eye(caster), (x, y + target.height / 2, z))
                barriers = fight.zarya_barriers
                hold = 1 if action == "storm" else 0
                script = hero.body_script
                self.assertIsNone(script.ult_seconds)
                charge = script.component.entity_var(342)
                cost = script.component.entity_var(338).value()
                if action == "storm":
                    script.set_ult_charge(cost - 140)
                start_charge = charge.value()
                start_frame = script.frame
                start_clock = script.passive_charge_ms
                rejected_cast = False

                def step(button=0, project=0):
                    run.step({hero: (*angles, button), caster: (*cast, project)})

                with patch.object(fight.projectiles, "fired", wraps=fight.projectiles.fired) as fired:
                    if action in ("sonic", "storm"):
                        step(INPUTS[action][0])
                        step()
                    if action in ("bow", "sonic"):
                        for _ in range(70):
                            step(1)
                        step()
                    elif action == "storm":
                        for _ in range(100):
                            if fight.rockets:
                                break
                            step(1)
                    if action != "melee":
                        first, = fight.rockets
                        self.assertIs(first.shooter, hero)
                        self.assertNotIn(caster.body, barriers.projected)
                    step(hold, 16)
                    self.assertEqual(barriers.projected[caster.body][1:], (200, bot.entity))
                    self.assertEqual(pool.total, before)
                    if action == "melee":
                        step(0x800)
                    else:
                        self.assertIn(first, fight.rockets)

                    changes = [(200, before)]
                    expected_shots = 5 if action == "storm" else int(action != "melee")
                    for _ in range(140):
                        entry = barriers.projected.get(caster.body)
                        current = (entry[1] if entry else 0, pool.total)
                        passive = (start_clock + (script.frame - start_frame) * script.frame_ms) // 1000 * 5
                        self.assertEqual(charge.value(), min(cost, start_charge + passive + before - pool.total))
                        if current != changes[-1]:
                            changes.append(current)
                        if len(changes) > 1 and fired.call_count == expected_shots and not fight.rockets:
                            break
                        if action == "storm" and current == (0, before) and not rejected_cast:
                            self.assertLess(charge.value(), cost)
                            step(hold | 32)
                            self.assertFalse(hanzo_control.casting(script))
                            rejected_cast = True
                        else:
                            step(hold)
                    self.assertFalse(fight.rockets)
                    self.assertEqual(fired.call_count, expected_shots)
                    hits = [hit["+0x78"]["+0x4"] for hit in hero.client.messages(combat.HIT)]
                    energy = caster.body_script.component.entity_var(87).value()
                    if action == "storm":
                        self.assertEqual(changes, [(200, before), (130, before), (60, before),
                                                   (0, before), (0, before - 70), (0, before - 140)])
                        self.assertEqual([call.args[3] for call in fired.call_args_list], [0x040000000000270F] * 5)
                        self.assertEqual((hits, energy), ([70, 70], 40))
                        self.assertTrue(rejected_cast)
                        self.assertEqual(charge.value(), cost)
                    else:
                        amount = INPUTS[action][1]
                        self.assertEqual(changes, [(200, before), (200 - amount, before)])
                        self.assertEqual((hits, energy), ([], amount / 5))
                    if action == "sonic":
                        self.assertEqual(fired.call_args.args[3], 0x0400000000000149)
                        cooldown = next(i for i in hero.body_script.component.instances.values()
                                        if i.graph.index == 0x00C1).vars[61]
                        self.assertGreater(cooldown.value(), 11.5)
                        self.assertFalse(fight.sonars)
                        self.assertFalse(fight.revealed_to(hero, bot))
                if action == "storm":
                    step()
                    step(32)
                    self.assertTrue(hanzo_control.casting(script))
                    self.assertEqual(charge.value(), 0)
                    for _ in range(100):
                        if fight.rockets:
                            break
                        step()
                    initial, = fight.rockets
                    self.assertEqual(initial.source_entity, hero.body)
                    self.assertEqual(initial.direct.amount, 125)
                    self.assertIs(initial.continuation, fight.dragon_profile)
                    for _ in range(40):
                        if initial not in fight.rockets:
                            break
                        step()
                    self.assertNotIn(initial, fight.rockets)
                    self.assertIn(bot.entity, initial.hit_entities)
                    self.assertEqual(pool.total, max(0, before - 140 - 125))
                    flight, = fight.dragons
                    self.assertEqual(flight.source_entity, hero.body)

    def test_enforcer_authored_shield_wait_is_not_rearmed_by_an_intercepted_hanzo_bow(self):
        from ow174.game import zarya_barrier

        run, hero, bot = self.arena(self.catalog["2"], "melee", "hit", distance=2)
        self.addCleanup(npc.remove_spawned, run.match)
        match = run.match
        match.game_map = replace(match.game_map, free_for_all=False)
        fight = match.combat
        pool = fight.vitals[bot.entity]
        component = bot.body_script.component
        recovery = next(i for i in component.instances.values() if i.graph.index == 0x0E62)
        wait = recovery.node_state(recovery.graph.node(52))
        caster = match.add_player(3, "Zarya", zarya_barrier.HERO, bot.team, False)
        in_world(match, caster)
        caster.client = NpcFixtureClient(caster)
        run.players.append(caster)
        self.assertTrue(match.switch_hero(caster, heroes()[zarya_barrier.HERO]))
        caster.team = bot.team
        x, y, z = bot.position
        caster.mover.position[:] = (x + 4, y, z)
        caster.mover.body = None
        for _ in range(30):
            run.step({})
        angles = self.target_aim(run, hero, bot)
        run.step({hero: (*angles, 0x800)})
        run.step({})
        self.assertEqual(pool.now[combat.SHIELDS_POOL], 70)
        self.assertTrue(wait.active)
        start = wait.start
        for _ in range(40):
            run.step({})
        cast = aim(fight._eye(caster), (x, y + 1, z))
        run.step({caster: (*cast, 16)})
        barriers = fight.zarya_barriers
        self.assertEqual(barriers.projected[caster.body][1:], (200, bot.entity))
        for _ in range(70):
            run.step({hero: (*angles, 1)})
        run.step({hero: (*angles, 0)})
        for _ in range(8):
            run.step({})
        self.assertEqual(barriers.projected[caster.body][1:], (75, bot.entity))
        self.assertEqual(pool.now[combat.SHIELDS_POOL], 70)
        self.assertTrue(wait.active)
        self.assertEqual(wait.start, start)
        for _ in range(260):
            run.step({})
            if pool.now[combat.SHIELDS_POOL] == 100:
                break
        self.assertEqual(pool.total, 350)

    def test_enforcer_authored_shield_recovery_agrees_across_idle_scheduler_gap(self):
        observed = []
        for skip in (False, True):
            with self.subTest(skip=skip):
                run, hero, bot = self.arena(self.catalog["2"], "bow", "hit", distance=5)
                self.addCleanup(npc.remove_spawned, run.match)
                fight = run.match.combat
                pool = fight.vitals[bot.entity]
                component = bot.body_script.component
                recovery = next(i for i in component.instances.values() if i.graph.index == 0x0E62)
                wait = recovery.node_state(recovery.graph.node(52))
                angles = self.target_aim(run, hero, bot)
                for _ in range(70):
                    run.step({hero: (*angles, 1)})
                run.step({hero: (*angles, 0)})
                for _ in range(20):
                    run.step({})
                    if wait.active:
                        break
                self.assertTrue(wait.active)
                self.assertEqual(pool.total, 225)
                end = run.frame + 230
                if skip:
                    run.frame = end
                    run.match.update(end * combat.TICK, end)
                else:
                    while run.frame < end:
                        run.step({})
                self.assertEqual(pool.now[combat.HEALTH_POOL], 225)
                self.assertFalse(wait.active)
                self.assertAlmostEqual(pool.now[combat.SHIELDS_POOL],
                                       .03 * (component.now - wait.start - wait.ms), places=5)
                observed.append(pool.now[combat.SHIELDS_POOL])
        self.assertEqual(len(observed), 2)
        self.assertAlmostEqual(observed[0], observed[1], places=5)

    def test_enforcer_authored_shield_recovery_orders_inflight_bow_across_gap(self):
        observed = {}
        for after_deadline, skip, gap in product((False, True), (False, True), (50, 230)):
            with self.subTest(after_deadline=after_deadline, skip=skip, gap=gap):
                run, hero, bot = self.arena(self.catalog["2"], "bow", "hit", distance=5)
                self.addCleanup(npc.remove_spawned, run.match)
                fight = run.match.combat
                pool = fight.vitals[bot.entity]
                component = bot.body_script.component
                recovery = next(i for i in component.instances.values() if i.graph.index == 0x0E62)
                wait = recovery.node_state(recovery.graph.node(52))

                def fire():
                    angles = self.target_aim(run, hero, bot)
                    for _ in range(70):
                        run.step({hero: (*angles, 1)})
                    run.step({hero: (*angles, 0)})

                fire()
                for _ in range(20):
                    run.step({})
                    if wait.active:
                        break
                self.assertTrue(wait.active)
                self.assertEqual(pool.total, 225)
                deadline = wait.start + wait.ms
                x, y, z = hero.mover.position
                bot.position = (x, y, z + 30)
                while run.frame < deadline // component.frame_ms - (75 if after_deadline else 95):
                    run.step({})
                fire()
                run.step({})
                arrow, = fight.rockets
                self.assertIsNotNone(arrow.last_tick)
                self.assertLess(component.now, deadline)
                end = run.frame + gap
                if skip:
                    run.frame = end
                    run.match.update(end * combat.TICK, end)
                else:
                    while run.frame < end:
                        run.step({})
                self.assertNotIn(arrow, fight.rockets)
                self.assertEqual(wait.active, gap == 50)
                if after_deadline:
                    self.assertGreater(pool.now[combat.HEALTH_POOL], 100)
                else:
                    self.assertEqual(pool.now[combat.HEALTH_POOL], 100)
                observed[after_deadline, skip, gap] = (
                    round(pool.now[combat.HEALTH_POOL], 5), round(pool.now[combat.SHIELDS_POOL], 5), wait.start,
                )
        for after_deadline, gap in product((False, True), (50, 230)):
            self.assertEqual(observed[after_deadline, False, gap], observed[after_deadline, True, gap])

    def test_enforcer_authored_reload_gates_fire_across_two_empty_magazines(self):
        run, hero, bot = self.arena(self.catalog["2"], "bow", "hit", distance=5)
        self.addCleanup(npc.remove_spawned, run.match)
        fight = run.match.combat
        # Durable test body keeps the same real target through nine volleys.
        pool = fight.vitals[hero.body] = combat.Vitals(hero.body, 5000)
        component = bot.body_script.component
        weapon = next(i for i in component.instances.values() if i.graph.index == 0x13EB)
        ammo = weapon.vars[53]
        reload = weapon.node_state(weapon.graph.node(32))
        self.assertEqual(ammo.value(), 4)
        angles = self.target_aim(run, hero, bot)
        run.step({hero: (*angles, 0)})
        self.assertEqual(npc_control.start(run.match, hero), bot.entity)
        shots = []
        reloads = {}
        with patch.object(fight, "_shoot", wraps=fight._shoot) as fired:
            for _ in range(1500):
                before = fired.call_count
                was_reloading = reload.active
                run.step({})
                if reload.active:
                    self.assertEqual(ammo.value(), 0)
                    if was_reloading:
                        self.assertEqual(fired.call_count, before)
                    reloads.setdefault(reload.counter, reload.start + reload.ms)
                if fired.call_count != before:
                    self.assertEqual(fired.call_count, before + 1)
                    shooter, _, node, _, _ = fired.call_args.args
                    self.assertIs(shooter, bot)
                    self.assertEqual(node.node.pos, 24)
                    shots.append((component.now, ammo.value()))
                if len(shots) == 9:
                    break
        self.assertEqual([ammo_left for _, ammo_left in shots], [3, 2, 1, 0, 3, 2, 1, 0, 3])
        self.assertEqual(len(reloads), 2)
        for index, deadline in zip((4, 8), reloads.values(), strict=True):
            self.assertGreaterEqual(shots[index][0], deadline)
        for _ in range(35):
            run.step({})
            if not fight.rockets:
                break
        self.assertEqual(pool.total, 5000 - 9 * 25 * 2)

    def test_manual_enforcer_start_fires_authored_pellets_and_stop_retires_flight(self):
        run, hero, bot = self.arena(self.catalog["2"], "bow", "hit")
        self.addCleanup(npc.remove_spawned, run.match)
        fight = run.match.combat
        world = bot.body_script.component.world
        self.assertEqual(world.controller_components, (0x4B,))
        yaw, pitch = self.target_aim(run, hero, bot)
        run.step({hero: (yaw, pitch, 0)})
        self.assertEqual(npc_control.start(run.match, hero), bot.entity)
        target = next(t for t in fight._targets() if t.owner is hero)
        with patch.object(fight.projectiles, "fired", wraps=fight.projectiles.fired) as fired:
            for _ in range(200):
                run.step({})
        launches = [call for call in fired.call_args_list if call.args[0] is bot]
        self.assertEqual(len(launches), 50)
        self.assertEqual({(call.args[1].instance.graph.index, call.args[1].node.pos)
                          for call in launches}, {(0x13EB, 24)})
        self.assertEqual(fight.vitals_of(target).total, 100)
        for _ in range(200):
            run.step({})
            if any(rocket.shooter is bot for rocket in fight.rockets):
                break
        self.assertTrue(any(rocket.shooter is bot for rocket in fight.rockets))
        self.assertEqual(fight.vitals_of(target).total, 100)
        self.assertEqual(npc_control.stop(run.match), 1)
        self.assertFalse(any(rocket.shooter is bot for rocket in fight.rockets))
        self.assertFalse(any(shot.player is bot for shot in fight.projectiles.shots))
        with patch.object(fight, "_shoot", wraps=fight._shoot) as shoot:
            for _ in range(200):
                run.step({})
        self.assertFalse(shoot.called)
        self.assertEqual(fight.vitals_of(target).total, 100)

    def test_manual_enforcer_kill_records_source_and_stops_target_input(self):
        run, hero, bot = self.arena(self.catalog["2"], "bow", "hit")
        self.addCleanup(npc.remove_spawned, run.match)
        fight = run.match.combat
        yaw, pitch = self.target_aim(run, hero, bot)
        run.step({hero: (yaw, pitch, 0)})
        self.assertEqual(npc_control.start(run.match, hero), bot.entity)
        target = next(t for t in fight._targets() if t.owner is hero)
        pool = fight.vitals_of(target)
        for _ in range(600):
            run.step({})
            if pool.dead:
                break
        self.assertTrue(pool.dead)
        self.assertEqual(pool.killer, bot.entity)
        self.assertEqual(pool.total, 0)
        with patch.object(fight, "_shoot", wraps=fight._shoot) as shoot:
            for _ in range(50):
                run.step({})
        self.assertFalse(shoot.called)
        self.assertFalse(bot.attack_input)

    def test_all_five_talon_first_attacks_spend_seeded_hanzo_armour_per_hit(self):
        expected = {"1": 15, "2": 25, "3": 95, "4": 20, "5": 5}
        for key, definition in self.catalog.items():
            with self.subTest(npc=definition.name):
                run, hero, bot = self.arena(definition, "bow", "hit", distance=2.5 if key == "4" else 5)
                self.addCleanup(npc.remove_spawned, run.match)
                fight = run.match.combat
                pool = fight.vitals[hero.body] = combat.Vitals(hero.body, 200, 150)
                fight._sync_body_health(hero, pool)
                run.step({hero: (*self.target_aim(run, hero, bot), 0)})
                self.assertEqual(npc_control.start(run.match, hero), bot.entity)
                for _ in range(150):
                    run.step({})
                    if pool.total < 350 and not fight.rockets:
                        break
                self.assertEqual(pool.now[combat.ARMOUR_POOL], 150 - expected[key])
                self.assertEqual(pool.now[combat.HEALTH_POOL], 200)
                if key == "4":
                    self.assertEqual(fight.pins.get(bot.entity), hero.body)
                    pin = next(i for i in bot.body_script.component.instances.values() if i.graph.index == 0x1157)
                    periodic = pin.node_state(pin.graph.node(15))
                    for _ in range(80):
                        if periodic.active:
                            break
                        run.step({})
                    self.assertTrue(periodic.active)
                    before = pool.now[combat.ARMOUR_POOL]
                    for _ in range(10):
                        run.step({})
                    self.assertAlmostEqual(pool.now[combat.ARMOUR_POOL], before - 8, places=4)
                    self.assertEqual(pool.now[combat.HEALTH_POOL], 200)
                self.assertEqual(npc_control.stop(run.match), 1)

    def test_all_five_talon_lethal_attacks_retire_held_bow_before_respawn(self):
        for key, definition in self.catalog.items():
            with self.subTest(npc=definition.name):
                run, hero, bot = self.arena(
                    definition, "bow", "hit", distance=2.5 if key == "4" else 5,
                )
                self.addCleanup(npc.remove_spawned, run.match)
                fight = run.match.combat
                old_body, old_script = hero.body, hero.body_script
                origin = tuple(hero.mover.position)
                target = next(t for t in fight._targets() if t.owner is hero)
                pool = fight.vitals_of(target)
                pool.damage(199)  # Seed 1 HP for a lethal first contact.
                yaw, pitch = self.target_aim(run, hero, bot)
                for _ in range(70):
                    run.step({hero: (yaw, pitch, 1)})
                self.assertEqual(npc_control.start(run.match, hero), bot.entity)
                with patch.object(fight.projectiles, "fired", wraps=fight.projectiles.fired) as fired:
                    for _ in range(300):
                        run.step({hero: (yaw, pitch, 1)})
                        if pool.dead:
                            break
                    self.assertTrue(pool.dead)
                    self.assertEqual(pool.killer, bot.entity)
                    run.step({hero: (yaw, pitch, 0)})
                    self.assertFalse(bot.attack_input)
                    self.assertNotIn(old_body, fight.pins.values())
                    self.assertEqual(npc_control.stop(run.match), 1)

                    limit = round(combat.respawn_seconds(run.match) / combat.TICK) + 120
                    for _ in range(limit):
                        if hero.body != old_body:
                            break
                        run.step({})
                    self.assertNotEqual(hero.body, old_body)
                    self.assertTrue(old_script.retired)
                    self.assertFalse(fight.is_dead(hero))
                    self.assertFalse(any(call.args[0] is hero for call in fired.call_args_list))
                    self.assertFalse(hero.client.messages(combat.HIT))

                hero.mover.position[:] = origin
                hero.mover.body = None
                run.step({})
                yaw, pitch = self.target_aim(run, hero, bot)
                for _ in range(70):
                    run.step({hero: (yaw, pitch, 1)})
                run.step({hero: (yaw, pitch, 0)})
                for _ in range(30):
                    run.step({})
                hits = hero.client.messages(combat.HIT)
                self.assertEqual([hit["+0x78"]["+0x4"] for hit in hits], [125])

    def add_downrange_soldier(self, run, hero, distance):
        match = run.match
        target = match.add_player(2, "Downrange Soldier", SOLDIER, 1, False)
        in_world(match, target)
        target.client = NpcFixtureClient(target)
        run.players.append(target)
        self.assertTrue(match.switch_hero(target, heroes()[SOLDIER]))
        target.team = 1
        x, y, z = hero.mover.position
        target.mover.position[:] = (x + distance, y, z)
        target.mover.body = None
        return target

    def test_committed_sonic_survives_each_talon_first_lethal_contact(self):
        self._committed_arrow_survives_talon("sonic")

    def test_committed_storm_survives_each_talon_first_lethal_contact(self):
        self._committed_arrow_survives_talon("storm")

    def _committed_arrow_survives_talon(self, attack):
        from test_game_hanzo_sonic import profile

        sonic_profile = profile() if attack == "sonic" else None
        prepare, _ = INPUTS[attack]
        damage = 125 if attack == "sonic" else 70
        held_draw = int(attack == "sonic")
        for key, definition in self.catalog.items():
            with self.subTest(npc=definition.name):
                lethal_frame = None
                for release in (False, True):
                    run, hero, bot = self.arena(definition, "bow", "hit", distance=2.5 if key == "4" else 5)
                    self.addCleanup(npc.remove_spawned, run.match)
                    match = run.match
                    fight = match.combat
                    if sonic_profile is not None:
                        fight.configure_sonic(sonic_profile)
                    target = self.add_downrange_soldier(run, hero, 19)
                    hero_pool = fight.vitals_of(next(t for t in fight._targets() if t.owner is hero))
                    victim_pool = fight.vitals_of(next(t for t in fight._targets() if t.owner is target))
                    hero_pool.damage(199)
                    if attack == "storm":
                        victim_pool.damage(130)
                    victim_before_impact = victim_pool.total
                    original_body, original_script = hero.body, hero.body_script
                    enemy_aim = self.target_aim(run, hero, bot)
                    run.step({hero: (*enemy_aim, prepare)})
                    run.step({hero: (*enemy_aim, 0)})
                    for _ in range(70):
                        run.step({hero: (*enemy_aim, held_draw)})

                    def commit_arrow():
                        reply = aim(fight._eye(hero), fight._eye(target))
                        run.step({hero: (*reply, 1 - held_draw)})
                        arrow, = [r for r in fight.rockets if r.shooter is hero]
                        self.assertIs(arrow.sonar, sonic_profile)
                        self.assertEqual(arrow.direct.amount, damage)
                        if attack == "storm":
                            self.assertEqual(arrow.shot.definition, 0x040000000000270F)
                        self.assertEqual(arrow.source_entity, original_body)
                        return arrow

                    committed = None
                    if release and lethal_frame < 2:
                        committed = commit_arrow()
                    else:
                        run.step({hero: (*enemy_aim, held_draw)})
                    run.step({hero: (*enemy_aim, held_draw if committed is None else 0)})
                    self.assertEqual(npc_control.start(match, hero), bot.entity)
                    for frame in range(250):
                        if release and frame == lethal_frame - 2:
                            committed = commit_arrow()
                        else:
                            run.step({hero: (*enemy_aim, held_draw if committed is None else 0)})
                        if hero_pool.dead:
                            break
                    self.assertTrue(hero_pool.dead)
                    self.assertEqual(hero_pool.killer, bot.entity)
                    self.assertEqual(victim_pool.total, victim_before_impact)
                    if not release:
                        lethal_frame = frame
                        self.assertFalse(fight.sonars)
                        continue
                    self.assertEqual(frame, lethal_frame)
                    self.assertIn(committed, fight.rockets)
                    charge = original_script.component.entity_var(342).value()
                    for _ in range(45):
                        run.step({hero: (*enemy_aim, 1)})
                        self.assertFalse(any(r is not committed and r.shooter is hero for r in fight.rockets))
                    if sonic_profile is not None:
                        beacon, = fight.sonars
                        self.assertEqual((beacon.source_entity, beacon.attached), (original_body, target.body))
                    else:
                        self.assertFalse(fight.sonars)
                        self.assertTrue(victim_pool.dead)
                        self.assertEqual(victim_pool.killer, original_body)
                        self.assertGreater(victim_pool.death_tick, hero_pool.death_tick)
                    self.assertEqual(victim_pool.total, victim_before_impact - damage)
                    self.assertNotIn(committed, fight.rockets)
                    self.assertEqual(original_script.component.entity_var(342).value(), charge)

    def test_talon_lethal_contact_cancels_uncommitted_but_preserves_launched_dragonstrike(self):
        from test_game_hanzo_dragons import prepared_profile

        dragon_profile = prepared_profile()
        for key, definition in self.catalog.items():
            lethal_offset = None
            transform_offset = None
            for stage in ("baseline", "windup", "launched", "transformed"):
                with self.subTest(npc=definition.name, stage=stage):
                    run, hero, bot = self.arena(definition, "bow", "hit", distance=2.5 if key == "4" else 5)
                    self.addCleanup(npc.remove_spawned, run.match)
                    fight = run.match.combat
                    fight.configure_dragonstrike(dragon_profile)
                    target = self.add_downrange_soldier(run, hero, 28)
                    hero_pool = fight.vitals_of(next(t for t in fight._targets() if t.owner is hero))
                    target_pool = fight.vitals_of(next(t for t in fight._targets() if t.owner is target))
                    hero_pool.damage(199)
                    original_body, script = hero.body, hero.body_script
                    script.ult_seconds = 0
                    script.set_ult_charge(1680)
                    charge = script.component.entity_var(342)
                    enemy_aim = self.target_aim(run, hero, bot)
                    dragon_aim = aim(fight._eye(hero), fight._eye(target))
                    run.step({hero: (*enemy_aim, 0)})
                    if stage == "baseline":
                        self.assertEqual(npc_control.start(run.match, hero), bot.entity)
                        for frame in range(250):
                            run.step({hero: (*enemy_aim, 0)})
                            if hero_pool.dead:
                                break
                        self.assertTrue(hero_pool.dead)
                        self.assertEqual(hero_pool.killer, bot.entity)
                        lethal_offset = frame
                        continue

                    # Move the actual NPC start around the paid one-second cast.
                    # The control observes the attack's own authored input delay.
                    cast_to_contact = {"windup": 55, "launched": 67}.get(stage)
                    if stage == "transformed":
                        self.assertIsNotNone(transform_offset)
                        cast_to_contact = transform_offset + 2
                    cast_at = max(0, lethal_offset - cast_to_contact)
                    npc_at = max(0, cast_to_contact - lethal_offset)
                    initial = None
                    for frame in range(max(cast_at, npc_at) + 260):
                        if frame == npc_at:
                            self.assertEqual(npc_control.start(run.match, hero), bot.entity)
                        angles = enemy_aim if frame == npc_at - 1 else dragon_aim
                        run.step({hero: (*angles, 32 if frame == cast_at else 0)})
                        if frame == cast_at:
                            self.assertTrue(hanzo_control.casting(script))
                            self.assertEqual(charge.value(), 0)
                        if initial is None:
                            initial = next((r for r in fight.rockets if r.continuation is dragon_profile), None)
                        if hero_pool.dead:
                            break
                    self.assertTrue(hero_pool.dead)
                    self.assertEqual(hero_pool.killer, bot.entity)
                    self.assertEqual(frame, npc_at + lethal_offset)
                    self.assertEqual(target_pool.total, 200)
                    self.assertEqual(charge.value(), 0)
                    if stage == "windup":
                        self.assertIsNone(initial)
                        for _ in range(95):
                            run.step({})
                            self.assertFalse(any(r.continuation is dragon_profile for r in fight.rockets))
                            self.assertFalse(fight.dragons)
                        self.assertEqual(target_pool.total, 200)
                        self.assertFalse(hanzo_control.casting(script))
                    else:
                        self.assertIsNotNone(initial)
                        self.assertEqual(initial.source_entity, original_body)
                        if stage == "launched":
                            self.assertIn(initial, fight.rockets)
                            self.assertFalse(fight.dragons)
                            committed_flight = None
                        else:
                            self.assertNotIn(initial, fight.rockets)
                            committed_flight, = fight.dragons
                        for follow in range(150):
                            run.step({})
                            if stage == "launched" and transform_offset is None and fight.dragons:
                                transform_offset = frame + follow + 1 - cast_at
                            if target_pool.total < 200:
                                break
                        self.assertIsNotNone(transform_offset)
                        moving, = fight.dragons
                        if committed_flight is not None:
                            self.assertIs(moving, committed_flight)
                        self.assertEqual(moving.source_entity, original_body)
                        self.assertLess(target_pool.total, 200)
                        self.assertNotIn(initial, fight.rockets)
                    self.assertEqual(charge.value(), 0)

    def test_all_five_talon_lethal_attacks_discard_buffered_quick_melee(self):
        melee = 0x800
        for key, definition in self.catalog.items():
            contact_offset = None
            for buffered in (False, True):
                with self.subTest(npc=definition.name, buffered=buffered):
                    run, hero, bot = self.arena(
                        definition, "bow", "hit", distance=2.5 if key == "4" else 5,
                    )
                    self.addCleanup(npc.remove_spawned, run.match)
                    fight = run.match.combat
                    target = self.add_downrange_soldier(run, hero, 1.8)
                    hero_pool = fight.vitals_of(next(t for t in fight._targets() if t.owner is hero))
                    target_pool = fight.vitals_of(next(t for t in fight._targets() if t.owner is target))
                    hero_pool.damage(199)
                    old_body, script = hero.body, hero.body_script
                    origin = tuple(hero.mover.position)
                    npc_aim = self.target_aim(run, hero, bot)
                    melee_aim = aim(fight._eye(hero), fight._eye(target))
                    run.step({hero: (*npc_aim, 0)})
                    if not buffered:
                        self.assertEqual(npc_control.start(run.match, hero), bot.entity)
                        for frame in range(250):
                            run.step({hero: (*melee_aim, 0)})
                            if hero_pool.dead:
                                break
                        self.assertTrue(hero_pool.dead)
                        self.assertEqual(hero_pool.killer, bot.entity)
                        self.assertEqual(target_pool.total, 200)
                        contact_offset = frame
                        continue

                    first_press = max(0, contact_offset - 58)
                    npc_start = max(0, 58 - contact_offset)
                    for frame in range(npc_start + contact_offset + 1):
                        if frame == npc_start:
                            self.assertEqual(npc_control.start(run.match, hero), bot.entity)
                        angles = npc_aim if frame == npc_start - 1 else melee_aim
                        button = melee if frame in (first_press, first_press + 54) else 0
                        run.step({hero: (*angles, button)})
                        if frame == first_press:
                            self.assertEqual(target_pool.total, 170)
                        if frame == first_press + 54:
                            instance = next(i for i in script.component.instances.values()
                                            if i.graph.index == 0x0043)
                            ability = instance.node_state(instance.graph.node(14))
                            self.assertTrue(ability.presses)
                            self.assertEqual(target_pool.total, 170)
                        if hero_pool.dead:
                            break

                    self.assertTrue(hero_pool.dead)
                    self.assertEqual(hero_pool.killer, bot.entity)
                    self.assertEqual(frame - first_press, 58)
                    self.assertEqual(npc_control.stop(run.match), 1)
                    limit = round(combat.respawn_seconds(run.match) / combat.TICK) + 120
                    for _ in range(limit):
                        if hero.body != old_body:
                            break
                        run.step({hero: (*melee_aim, 0)})
                    self.assertNotEqual(hero.body, old_body)
                    self.assertTrue(script.retired)
                    self.assertNotIn(old_body, fight.melee_seen)

                    hero.mover.position[:] = origin
                    hero.mover.body = None
                    for _ in range(12):
                        run.step({hero: (*melee_aim, 0)})
                    self.assertEqual(target_pool.total, 170)
                    self.assertEqual([hit["+0x78"]["+0x4"] for hit in hero.client.messages(combat.HIT)], [30])
                    run.step({hero: (*melee_aim, melee)})
                    self.assertEqual(target_pool.total, 140)
                    self.assertEqual([hit["+0x78"]["+0x4"] for hit in hero.client.messages(combat.HIT)], [30, 30])


    def test_all_five_talon_manual_attacks_reacquire_replacement_body(self):
        for key, definition in self.catalog.items():
            with self.subTest(npc=definition.name):
                run, hero, bot = self.arena(
                    definition, "bow", "hit", distance=2.5 if key == "4" else 5,
                )
                self.addCleanup(npc.remove_spawned, run.match)
                fight = run.match.combat
                old_body, old_script = hero.body, hero.body_script
                origin = tuple(hero.mover.position)
                yaw, pitch = self.target_aim(run, hero, bot)
                for _ in range(70):
                    run.step({hero: (yaw, pitch, 1)})
                run.step({hero: (yaw, pitch, 0)})
                for _ in range(15):
                    run.step({})
                hits = hero.client.messages(combat.HIT)
                self.assertEqual([hit["+0x78"]["+0x4"] for hit in hits], [125])
                target = next(t for t in fight._targets() if t.owner is hero)
                old_pool = fight.vitals_of(target)
                old_pool.damage(199)
                self.assertEqual(npc_control.start(run.match, hero), bot.entity)
                for _ in range(300):
                    run.step({})
                    if old_pool.dead:
                        break
                self.assertTrue(old_pool.dead)
                self.assertEqual(old_pool.killer, bot.entity)
                earned = old_script.component.entity_var(342).value()
                self.assertGreaterEqual(earned, 125)

                limit = round(combat.respawn_seconds(run.match) / combat.TICK) + 120
                for _ in range(limit):
                    run.step({})
                    if hero.body != old_body:
                        break
                    self.assertFalse(bot.attack_input)
                    self.assertEqual(old_script.component.entity_var(342).value(), earned)
                self.assertNotEqual(hero.body, old_body)
                self.assertTrue(old_script.retired)
                self.assertEqual(hero.body_script.component.entity_var(342).value(), earned)
                target = next(t for t in fight._targets() if t.owner is hero)
                fresh_pool = fight.vitals_of(target)
                self.assertEqual(fresh_pool.total, 200)

                hero.mover.position[:] = origin
                hero.mover.body = None
                self.assertTrue(bot.firing)
                self.assertEqual(bot.attack_target, hero.entity)
                for _ in range(300):
                    run.step({})
                    if fresh_pool.total < 200:
                        break
                self.assertLess(fresh_pool.total, 200)
                self.assertGreater(fresh_pool.total, 0)
                self.assertEqual(old_pool.total, 0)
                if key == "4":
                    self.assertEqual(fight.pins.get(bot.entity), hero.body)

    def test_actual_heavy_damage_and_passive_clock_charge_a_real_dragonstrike(self):
        from test_game_hanzo_dragons import prepared_profile

        run, hero, bot = self.arena(self.catalog["5"], "bow", "hit")
        self.addCleanup(npc.remove_spawned, run.match)
        fight = run.match.combat
        fight.configure_dragonstrike(prepared_profile())
        script = hero.body_script
        self.assertIsNone(script.ult_seconds)
        self.assertEqual(script.component.entity_var(342).value(), 0)
        initial_time = script.passive_charge_ms
        initial_frame = script.frame
        initial_health = fight.vitals[bot.entity].total
        yaw, pitch = self.target_aim(run, hero, bot)
        for draw in range(16):
            for _ in range(70):
                run.step({hero: (yaw, pitch, 1)})
            run.step({hero: (yaw, pitch, 0)})
            for _ in range(12):
                run.step({})
            dealt = initial_health - fight.vitals[bot.entity].total
            passive = (initial_time + (script.frame - initial_frame) * script.frame_ms) // 1000 * 5
            charge = script.component.entity_var(342).value()
            self.assertEqual(charge, min(1680, dealt + passive), draw)
            if charge == 1680:
                break
        self.assertEqual(charge, 1680)
        x, y, z = hero.mover.position
        bot.position = (x, y, z + 2)
        yaw, pitch = self.target_aim(run, hero, bot)
        before = fight.vitals[bot.entity].total
        run.step({hero: (yaw, pitch, 32)})
        self.assertEqual(script.component.entity_var(342).value(), 0)
        for _ in range(74):
            run.step({})
            self.assertEqual(script.component.entity_var(342).value(), 0)
        self.assertLess(fight.vitals[bot.entity].total, before)

        # Put both actual Heavy bodies in the moving phase, beyond initial-arrow reach.
        bot.position = (x, y, z + 20)
        second = npc.spawn(run.match, self.catalog["5"], (x, y, z + 25), team=bot.team)
        targets = {target.entity: target for target in fight._targets()}
        pools = [fight.vitals_of(targets[actor.entity]) for actor in (bot, second)]
        base_health = sum(pool.total for pool in pools)
        run.step({})  # The original cast recovery ends here.
        self.assertEqual(script.component.entity_var(342).value(), 0)
        frame = script.frame
        clock = script.passive_charge_ms
        for _ in range(100):
            if fight.dragons:
                break
            run.step({})
        first_flight, = fight.dragons
        run.step({hero: (0, 0, 32)})
        self.assertFalse(hanzo_control.casting(script))
        self.assertEqual(fight.dragons, [first_flight])
        self.assertFalse(fight.rockets)

        for _ in range(550):
            run.step({})
            dealt = base_health - sum(pool.total for pool in pools)
            passive = (clock + (script.frame - frame) * script.frame_ms) // 1000 * 5
            charge = script.component.entity_var(342).value()
            self.assertAlmostEqual(charge, min(1680, dealt + passive), places=2)
            if charge == 1680:
                break
        self.assertEqual(charge, 1680)
        self.assertEqual(fight.dragons, [first_flight])
        run.step({hero: (0, 0, 32)})
        self.assertEqual(script.component.entity_var(342).value(), 0)
        for _ in range(160):
            run.step({})
            if len(fight.dragons) == 2:
                break
        first, second_flight = fight.dragons
        self.assertIs(first, first_flight)
        self.assertEqual(second_flight.source_entity, hero.body)

    def test_actual_heavy_volley_bakes_authored_ramp_and_samples_shot_time(self):
        from ow174.game.script.expr import f32

        run, hero, bot = self.arena(self.catalog['5'], 'bow', 'hit')
        self.addCleanup(npc.remove_spawned, run.match)
        bot.firing = True
        bot.attack_target = hero.entity
        fight = run.match.combat
        shoot = fight._shoot
        samples = []

        def observed(shooter, command, state, number, time, **kwargs):
            if shooter is bot:
                self.assertEqual((state.instance.graph.index, state.node.pos), (0x114F, 47))
                self.assertEqual(state.spread_ramp, 10)
                self.assertIs(state._out('m_3FFC9EB9'), state.instance.vars[231])
                # Reports can be consumed after the activation ended and a
                # ChaseVar already changed231. Sample the actual shot time.
                samples.append((kwargs['activation'], state.spread_at(time - state.start - state.offset)))
            return shoot(shooter, command, state, number, time, **kwargs)

        with patch.object(fight, '_shoot', side_effect=observed):
            for _ in range(200):
                run.step({})
        self.assertGreaterEqual(len(samples), 2)
        first_activation = [value for activation, value in samples if activation == samples[0][0]]
        self.assertEqual(first_activation[:2], [f32(0.1), f32(0.2)])
        self.assertEqual(first_activation, sorted(first_activation))
        self.assertTrue(fight.vitals[hero.body].dead)
