"""Actual five-NPC body data in a Match; fixture collision/transport are not native acceptance."""

import math
import os
import unittest
from dataclasses import replace
from pathlib import Path
from unittest.mock import patch

import test_game_sombra_combat as sombra_fixture
from test_game_combat import aim, in_world

from ow174.game import (
    combat,
    fortify,
    mei,
    mover,
    npc,
    npc_control,
    phase_defenses,
    reinhardt_barrier,
    shot_guards,
    tracer_combat,
)
from ow174.game.body_vm_snapshot import export_component_vm
from ow174.game.commands import FIRE, Command
from ow174.game.content import SOLDIER, heroes
from ow174.game.script import graph
from ow174.game.script.driver import OWNER_HEALTH
from ow174.game.script.expr import INSTANCE, Entity, Vec3
from ow174.game.script.runtime import BEGIN, send_instance_message
from ow174.game.world import ARMOUR_POOL, HEALTH_POOL, SHIELDS_POOL


def clear_graphs():
    graph._data.cache_clear()
    graph._records.cache_clear()
    graph.graph.cache_clear()


class SombraActualNpcTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        path = os.environ.get("ORPHEUS_NPC_RUNTIME_DIR")
        if not path:
            raise unittest.SkipTest("set ORPHEUS_NPC_RUNTIME_DIR to the external actual-NPC runtime")
        folder = Path(path) / "data"
        cls.catalog = npc.load_catalog(folder / "pve_npcs_174.json")
        for definition in cls.catalog.values():
            mover.mover_data(definition.body)  # load the declared runtime's canonical table
            if definition.body not in (mover.loaded_mover_table() or {}):
                raise AssertionError(
                    f"Actual NPC acceptance requires explicit mover data for {definition.name}: "
                    f"{definition.body:016X}; generic fallback is not acceptance evidence"
                )
        cls.addClassCleanup(cls.restore_graphs, graph.GRAPHS_PATH)
        graph.GRAPHS_PATH = folder / "game_graphs_174.json.gz"
        clear_graphs()

    @classmethod
    def restore_graphs(cls, path):
        graph.GRAPHS_PATH = path
        clear_graphs()

    def make(self, key, *, distance=1.5, team=1):
        fixture = sombra_fixture.SombraCombatTests()
        fixture.setUp()
        fixture.b.spawned = False  # leave only the actual NPC as the enemy target
        for player in fixture.run.players:
            # The model does not claim NPC projectile observer-wire acceptance.
            player.client.bot_stream = lambda body: None
            player.client.npc_deaths = []
            player.client.queue_bot_death = (
                lambda body, event, log=player.client.npc_deaths: log.append((body, event))
            )
            player.client.queue_bot_death_update = (
                lambda body, event, log=player.client.npc_deaths: log.append((body, event))
            )
        bot = npc.spawn(fixture.run.match, self.catalog[key], (0, 0, distance), team=team)
        # Explicit first authored weapon profile, not retail difficulty.
        bot.body_script.component.set(9948, 0)
        fixture.press(0, 4)
        self.assertIsNotNone(bot.body_script)
        self.assertEqual(bot.body, self.catalog[key].body)
        return fixture, bot

    @staticmethod
    def press(fixture, bot, buttons, frames=1):
        x, y, z = bot.position
        yaw, pitch = aim(fixture.run.match.combat._eye(fixture.a), (x, y + 1, z))
        for _ in range(frames):
            fixture.run.step({fixture.a: (yaw, pitch, buttons)})

    def displaced_npc(self, key):
        f, bot = self.make(key, distance=4)
        match = f.run.match
        fight = match.combat
        match.game_map = replace(match.game_map, free_for_all=False)
        f.a.team = f.b.team = 0
        f.b.spawned = True
        self.assertTrue(match.switch_hero(f.b, heroes()[reinhardt_barrier.ZARYA]))
        f.b.mover.position[:] = (2, 0, 0)
        f.b.mover.body = None
        f.geometry((-5, 0, 6, 5, 0, 6, 5, 4, 6), (-5, 0, 6, 5, 4, 6, -5, 4, 6))
        f.press(0, 50)
        script = f.b.body_script
        script.set_ult_charge(script.component.entity_var(338).value())
        stale_aim = aim(fight._eye(f.a), (0, 1, 4))
        f.run.step({f.b: (0, 0, 32)})
        for _ in range(130):
            f.press(0)
            if bot.position[0] > 1.2:
                break
        self.assertGreater(bot.position[0], 1.2)
        self.assertIn(bot.entity, fight.gravitons.npc_movers)
        return f, bot, stale_aim

    def test_lethal_pistol_or_graviton_clears_displacement_for_all_five_npcs(self):
        for key, definition in self.catalog.items():
            for pistol in (False, True):
                with self.subTest(npc=definition.name, pistol=pistol):
                    f, bot, _ = self.displaced_npc(key)
                    fight = f.run.match.combat
                    field, = fight.gravitons.fields
                    vitals = fight.vitals[bot.entity]
                    vitals.now[ARMOUR_POOL] = vitals.now[SHIELDS_POOL] = 0
                    vitals.now[HEALTH_POOL] = 1 if pistol else 0.05
                    self.press(f, bot, FIRE if pistol else 0)
                    self.assertTrue(bot.dead)
                    self.assertEqual(vitals.total, 0)
                    self.assertEqual(vitals.killer, f.a.body if pistol else f.b.body)
                    self.assertNotIn(bot.entity, fight.gravitons.npc_movers)
                    self.assertEqual(bot.velocity, (0.0, 0.0, 0.0))
                    self.assertTrue(f.a.client.npc_deaths)
                    position = bot.position
                    f.press(0, 12)
                    self.assertIn(field, fight.gravitons.fields)
                    self.assertEqual(bot.position, position)
                    self.assertEqual(vitals.total, 0)

    def test_graviton_range_and_team_gate_for_all_five_npc_bodies(self):
        for key, definition in self.catalog.items():
            with self.subTest(npc=definition.name):
                f, bot, _ = self.displaced_npc(key)
                fight = f.run.match.combat
                field, = fight.gravitons.fields
                x, _, z = field.position
                target = next(t for t in fight._targets() if t.owner is bot)
                outside = (x + 6 + target.radius + 0.15, 0, z - 0.4)
                inside = (x + 6 + target.radius - 0.15, 0, z - 0.4)
                vitals = fight.vitals[bot.entity]
                before = vitals.total
                bot.position = outside
                f.press(0, 8)
                self.assertEqual(bot.position, outside)
                self.assertEqual(vitals.total, before)
                self.assertNotIn(bot.entity, fight.gravitons.npc_movers)

                bot.position = inside
                bot.team = field.team
                f.press(0, 8)
                self.assertEqual(bot.position, inside)
                self.assertEqual(vitals.total, before)
                self.assertNotIn(bot.entity, fight.gravitons.npc_movers)

                bot.team = 1 - field.team
                f.press(0, 8)
                self.assertIn(field, fight.gravitons.fields)
                self.assertIn(bot.entity, fight.gravitons.npc_movers)
                self.assertLess(bot.position[0], inside[0])
                self.assertGreater(before - vitals.total, 0)
                self.assertLess(before - vitals.total, 0.7)

    def test_graviton_reacquires_all_five_npcs_when_wall_cover_is_removed(self):
        for key, definition in self.catalog.items():
            with self.subTest(npc=definition.name):
                f, bot, _ = self.displaced_npc(key)
                fight = f.run.match.combat
                field, = fight.gravitons.fields
                bot.position = (0, 0, 3)
                f.geometry((-5, 0, 4.5, 5, 0, 4.5, 5, 4, 4.5),
                           (-5, 0, 4.5, 5, 4, 4.5, -5, 4, 4.5))
                before = fight.vitals[bot.entity].total
                f.press(0, 12)
                self.assertIn(field, fight.gravitons.fields)
                self.assertNotIn(bot.entity, fight.gravitons.npc_movers)
                self.assertEqual(bot.position, (0, 0, 3))
                self.assertEqual(fight.vitals[bot.entity].total, before)
                f.geometry()
                f.press(0, 12)
                self.assertIn(field, fight.gravitons.fields)
                self.assertIn(bot.entity, fight.gravitons.npc_movers)
                self.assertGreater(bot.position[2], 3)
                self.assertGreater(before - fight.vitals[bot.entity].total, 0)
                self.assertLess(before - fight.vitals[bot.entity].total, 1)

    def test_caster_departure_stops_graviton_displacement_for_all_five_npcs(self):
        for key, definition in self.catalog.items():
            with self.subTest(npc=definition.name):
                f, bot, _ = self.displaced_npc(key)
                match = f.run.match
                fight = match.combat
                self.assertGreater(math.hypot(*bot.velocity), 0)
                position = bot.position
                health = fight.vitals[bot.entity].total
                match.leave(f.b)
                f.run.players.remove(f.b)
                self.assertFalse(fight.gravitons.fields)
                self.assertNotIn(bot.entity, fight.gravitons.npc_movers)
                self.assertEqual(bot.velocity, (0.0, 0.0, 0.0))
                f.press(0, 12)
                self.assertEqual(bot.position, position)
                self.assertEqual(fight.vitals[bot.entity].total, health)

    def test_displaced_npcs_can_attack_sombra_before_graviton_expires(self):
        for key, definition in self.catalog.items():
            with self.subTest(npc=definition.name):
                f, bot, _ = self.displaced_npc(key)
                match = f.run.match
                fight = match.combat
                x, _, z = bot.position
                f.a.mover.position[:] = (x, 0, z - 1.5)
                f.a.mover.body = None
                self.press(f, bot, 0, 2)
                if key == "3":
                    special = next(i for i in bot.body_script.component.instances.values()
                                   if i.graph.index == 0x142C)
                    self.assertEqual(send_instance_message(special, 0x0240000000000A45, {}, bot.entity), 1)
                self.assertEqual(npc_control.start(match, f.a), bot.entity)
                field, = fight.gravitons.fields
                while f.run.frame + 1 < field.end_tick and f.health(f.a) == 200:
                    f.press(0)
                self.assertLess(f.health(f.a), 200)
                self.assertIn(field, fight.gravitons.fields)
                if key == "4":
                    self.assertEqual(fight.pins.get(bot.entity), f.a.body)
                else:
                    self.assertIn(bot.entity, fight.gravitons.npc_movers)
                before = f.health(f.a)
                npc_control.stop(match)
                f.press(0, 12)
                self.assertNotIn(bot.entity, fight.pins)
                self.assertEqual(f.health(f.a), before)

    def test_actual_pistol_tracks_all_five_npcs_displaced_by_graviton(self):
        for key, definition in self.catalog.items():
            with self.subTest(npc=definition.name):
                f, bot, stale_aim = self.displaced_npc(key)
                f.run.step({f.a: (*stale_aim, FIRE)})
                self.assertFalse(f.a.client.messages(combat.HIT))
                self.assertEqual(f.weapon().vars[53].value(), 59)
                f.press(0, 12)
                self.press(f, bot, FIRE)
                self.assertEqual(f.weapon().vars[53].value(), 58)
                notice, = f.a.client.messages(combat.HIT)
                self.assertEqual(notice["+0x78"]["+0x0"]["+0x0"], bot.entity)
                self.assertGreater(notice["+0x78"]["+0x4"], 0)

    def test_actual_melee_hits_displaced_npcs_only_in_reach(self):
        for key, definition in self.catalog.items():
            with self.subTest(npc=definition.name):
                f, bot, _ = self.displaced_npc(key)
                fight = f.run.match.combat
                x, _, z = bot.position
                f.a.mover.position[:] = (x, 0, z - 1.4)
                f.a.mover.body = None
                f.press(0, 2)
                before = fight.vitals[bot.entity].total
                position = bot.position
                self.press(f, bot, sombra_fixture.MELEE, 20)
                self.assertNotEqual(bot.position, position)
                hit, = f.a.client.messages(combat.HIT)
                self.assertEqual(hit["+0x78"]["+0x0"]["+0x0"], bot.entity)
                damage = hit["+0x78"]["+0x4"]
                self.assertGreaterEqual(damage, 25)
                self.assertLessEqual(damage, 30)
                self.assertGreaterEqual(before - fight.vitals[bot.entity].total, damage)
                f.press(0, 55)
                f.a.mover.position[:] = (0, 0, 0)
                f.a.mover.body = None
                f.press(0, 2)
                instances = f.a.body_script.component.instances.values()
                melee = next(i for i in instances if i.graph.index == 0x0043)
                contact = melee.state(4)
                counter = contact.counter
                self.press(f, bot, sombra_fixture.MELEE, 20)
                self.assertEqual(contact.counter, counter + 1)
                self.assertEqual(f.a.client.messages(combat.HIT), [hit])

    def silence_displaced_npc(self, key, button):
        f, bot, stale_aim = self.displaced_npc(key)
        fight = f.run.match.combat
        component = bot.body_script.component
        if key == "2" and button == 2:
            # Isolate moving-target selection from the Enforcer rejection flag.
            component.set(14679, False)
        if button == 2:
            for _ in range(8):
                f.run.step({f.a: (*stale_aim, button)})
            self.assertFalse(f.weapon().vars[375].value())
            self.assertEqual(f.weapon().state(53).remaining(), 0)
            f.press(0, 2)
        else:
            f.a.body_script.set_ult_charge(1400)
            f.run.step({f.a: (*stale_aim, button)})
        for _ in range(85):
            self.press(f, bot, 2 if button == 2 else 0)
            if bot.entity in fight.hacks:
                break
        effect, = fight.hacks[bot.entity]
        self.assertTrue(component.entity_var(888).value())
        self.assertIn(bot.entity, fight.gravitons.npc_movers)
        self.assertEqual(effect.application.source_entity(), f.a.body)
        return f, bot, effect

    def test_actual_hack_and_emp_apply_to_displaced_npcs_and_expire(self):
        for key, definition in self.catalog.items():
            for button in (2, 32):
                with self.subTest(npc=definition.name, button=button):
                    f, bot, effect = self.silence_displaced_npc(key, button)
                    fight = f.run.match.combat
                    component = bot.body_script.component
                    f.press(0, effect.until_tick - f.run.frame + 2)
                    self.assertNotIn(bot.entity, fight.hacks)
                    self.assertFalse(component.entity_var(888).value())

    def test_npc_hack_and_emp_expire_after_sombra_dies(self):
        for key, definition in self.catalog.items():
            for button in (2, 32):
                with self.subTest(npc=definition.name, button=button):
                    f, bot, effect = self.silence_displaced_npc(key, button)
                    match = f.run.match
                    fight = match.combat
                    component = bot.body_script.component
                    source = f.a.body
                    match.switch_hero(f.b, heroes()[sombra_fixture.SOMBRA])
                    f.b.team = 1 - f.a.team
                    f.b.mover.position[:] = [0.0, 0.0, -1.5]
                    f.b.mover.body = None
                    f.press(0, 50)
                    f.health(f.a)
                    fight.vitals[source].now[HEALTH_POOL] = 1
                    f.press(FIRE, shooter=f.b, target=f.a)
                    self.assertTrue(fight.is_dead(f.a))
                    self.assertEqual(fight.vitals[source].killer, f.b.body)
                    self.assertLess(f.run.frame, effect.until_tick)
                    self.assertIs(fight.hacks[bot.entity][0], effect)
                    self.assertEqual(effect.application.source_entity(), source)
                    self.assertTrue(component.entity_var(888).value())
                    f.press(0, effect.until_tick - f.run.frame + 2)
                    self.assertNotIn(bot.entity, fight.hacks)
                    self.assertNotIn(bot.entity, fight.hack_bases)
                    self.assertNotIn(effect.application.id, component.instances)
                    self.assertFalse(component.entity_var(888).value())

    def test_pistol_death_clears_hack_or_emp_from_displaced_npcs(self):
        for key, definition in self.catalog.items():
            for button in (2, 32):
                with self.subTest(npc=definition.name, button=button):
                    f, bot, effect = self.silence_displaced_npc(key, button)
                    fight = f.run.match.combat
                    component = bot.body_script.component
                    f.press(0, 50)
                    self.assertLess(f.run.frame, effect.until_tick)
                    self.assertIn(bot.entity, fight.hacks)
                    self.assertIn(bot.entity, fight.gravitons.npc_movers)
                    vitals = fight.vitals[bot.entity]
                    vitals.now[ARMOUR_POOL] = vitals.now[SHIELDS_POOL] = 0
                    vitals.now[HEALTH_POOL] = 1
                    self.press(f, bot, FIRE)
                    self.assertTrue(bot.dead)
                    self.assertEqual(vitals.killer, f.a.body)
                    self.assertNotIn(bot.entity, fight.hacks)
                    self.assertNotIn(bot.entity, fight.hack_bases)
                    self.assertNotIn(effect.application.id, component.instances)
                    self.assertFalse(component.entity_var(888).value())
                    self.assertNotIn(bot.entity, fight.gravitons.npc_movers)
                    self.assertTrue(f.a.client.npc_deaths)

    def test_actual_catalog_pistol_damage_and_initial_total_health_for_all_five(self):
        self.assertEqual([self.catalog[str(i)].name for i in range(1, 6)],
                         ["Trooper", "Enforcer", "Sniper", "Assassin", "Heavy Assault"])
        for key, definition in self.catalog.items():
            with self.subTest(npc=definition.name):
                fixture, bot = self.make(key)
                fight = fixture.run.match.combat
                before = definition.health + definition.armour + definition.shields
                self.assertEqual(bot.body_script.component.entity_var(OWNER_HEALTH).value(), before)
                self.press(fixture, bot, FIRE)
                self.press(fixture, bot, 0, 3)
                self.assertEqual(fight.vitals[bot.entity].total, before - 8)
                self.assertEqual(bot.body_script.component.entity_var(OWNER_HEALTH).value(), before - 8)
                self.assertEqual(fixture.weapon().vars[53].value(), 59)

    def test_actual_npc_melee_and_allied_exclusion_for_all_five(self):
        for key, definition in self.catalog.items():
            with self.subTest(npc=definition.name):
                fixture, bot = self.make(key)
                fight = fixture.run.match.combat
                before = fight.vitals[bot.entity].total
                self.press(fixture, bot, sombra_fixture.MELEE)
                self.press(fixture, bot, 0, 45)
                self.assertEqual(fight.vitals[bot.entity].total, before - 30)
                fixture.run.match.game_map = fixture.run.match.game_map.__class__(**{
                    **fixture.run.match.game_map.__dict__, "free_for_all": False,
                })
                bot.team = fixture.a.team
                self.press(fixture, bot, FIRE, 20)
                self.press(fixture, bot, 0, 3)
                self.press(fixture, bot, sombra_fixture.MELEE)
                self.press(fixture, bot, 0, 45)
                self.assertEqual(fight.vitals[bot.entity].total, before - 30)

    def test_actual_npc_hack_application_ownership_and_timed_status_for_all_five(self):
        for key, definition in self.catalog.items():
            with self.subTest(npc=definition.name):
                fixture, bot = self.make(key)
                if key == "2":
                    # The actual Enforcer fixture seeds the authored rejection
                    # flag14679. Preserve that rejection, then explicitly release
                    # it through the declared host variable for the second case.
                    self.assertTrue(bot.body_script.component.entity_var(14679).value())
                    self.press(fixture, bot, 2, 100)
                    self.assertFalse(bot.body_script.component.entity_var(888).value())
                    self.assertEqual(fixture.weapon().state(53).remaining(), 0)
                    bot.body_script.component.set(14679, False)
                    self.press(fixture, bot, 0, 30)
                self.press(fixture, bot, 2, 100)
                component = bot.body_script.component
                fight = fixture.run.match.combat
                self.assertTrue(component.entity_var(888).value())
                self.assertEqual(component.entity_var(6396).value().id, fixture.a.body)
                deadline = fight.hacks[bot.entity][0].until_tick
                self.press(fixture, bot, 0, deadline - fixture.run.frame)
                self.assertNotIn(bot.entity, fight.hacks)
                self.assertFalse(component.entity_var(888).value())
                self.assertFalse(component.entity_var(6396).value())
                self.assertEqual(fight.vitals[bot.entity].total,
                                 definition.health + definition.armour + definition.shields)

    def test_enforcer_authored_hack_rejection_survives_emp_and_expiry(self):
        f, bot = self.make("2")
        fight = f.run.match.combat
        component = bot.body_script.component

        self.assertTrue(component.entity_var(14679).value())
        self.press(f, bot, 2, 100)
        self.assertNotIn(bot.entity, fight.hacks)
        self.assertEqual(f.weapon().state(53).remaining(), 0)
        self.press(f, bot, 0, 2)

        f.a.body_script.set_ult_charge(1400)
        self.press(f, bot, 32)
        for _ in range(85):
            self.press(f, bot, 0)
            if bot.entity in fight.hacks:
                break
        effect, = fight.hacks[bot.entity]
        self.assertEqual(effect.application.source_entity(), f.a.body)
        self.assertTrue(component.entity_var(888).value())
        self.assertTrue(component.entity_var(14679).value())

        self.press(f, bot, 0, effect.until_tick - f.run.frame + 2)
        self.assertFalse(component.entity_var(888).value())
        self.assertTrue(component.entity_var(14679).value())
        self.press(f, bot, 2, 100)
        self.assertNotIn(bot.entity, fight.hacks)
        self.assertFalse(component.entity_var(888).value())
        self.assertEqual(f.weapon().state(53).remaining(), 0)

    def test_four_actual_weapon_npcs_deal_damage_through_authored_reports(self):
        # Assassin's separate1157 pin path has actual-data cases in test_game_sombra_assassin.
        for key in ("1", "2", "3", "5"):
            with self.subTest(npc=self.catalog[key].name):
                fixture, bot = self.make(key, distance=3)
                npc_control.start(fixture.run.match, fixture.a)
                for _ in range(380 if key == "3" else 300):
                    fixture.press(0)
                    if fixture.health(fixture.a) < 200:
                        break
                self.assertIsNotNone(bot.body_script)
                self.assertLess(fixture.health(fixture.a), 200)

    def test_actual_npc_engagement_replays_attacks_from_start_across_world_gap(self):
        cases = (
            ("1", 125, 80, False),
            ("2", 125, 198, False),
            ("3", 63, 200, False),  # the first 142C move starts before any rifle contact
            ("3", 235, 100, False),
            ("4", 125, 75.1, True),
            ("4", 235, 0, False),
            ("5", 125, 85, False),
        )
        for key, frames, expected_health, pinned in cases:
            samples = []
            for gap in (False, True):
                with self.subTest(npc=self.catalog[key].name, frames=frames, gap=gap):
                    f, bot = self.make(key, distance=1.5 if key == "4" else 6)
                    match = f.run.match
                    fight = match.combat
                    f.press(0, 150)
                    self.press(f, bot, 0)
                    origin = bot.position
                    self.assertEqual(npc_control.start(match, f.a), bot.entity)
                    start = f.run.frame
                    if gap:
                        match.update((start + frames) * combat.TICK, start + frames)
                    else:
                        f.press(0, frames)

                    vitals = fight.vitals[f.a.body]
                    self.assertAlmostEqual(f.health(f.a), expected_health, places=4)
                    self.assertEqual(fight.pins.get(bot.entity), f.a.body if pinned else None)
                    self.assertEqual(vitals.dead, expected_health == 0)
                    if vitals.dead:
                        self.assertEqual(vitals.killer, bot.entity)
                        self.assertTrue(start < vitals.death_tick < start + frames)
                    if key == "3":
                        self.assertNotEqual(bot.position, origin)
                        self.assertEqual(bot.velocity, (0, 0, 0))
                    samples.append((f.health(f.a), bot.position, vitals.death_tick))
            self.assertEqual(samples[:1], samples[1:])

    def test_actual_npc_engagement_releases_input_under_cover_and_reacquires(self):
        for key, definition in self.catalog.items():
            with self.subTest(npc=definition.name):
                f, bot = self.make(key, distance=2.5 if key == "4" else 3)
                match = f.run.match
                component = bot.body_script.component
                primary = npc_control.button(bot)
                if key == "3":
                    self.press(f, bot, 0, 70)
                    special = next(i for i in component.instances.values()
                                   if i.graph.index == 0x142C)
                    self.assertEqual(send_instance_message(special, 0x0240000000000A45, {}, bot.entity), 1)
                self.assertEqual(npc_control.start(match, f.a), bot.entity)
                f.press(0)
                self.assertTrue(bot.attack_input)
                self.assertIn(primary, component.held)
                before_cover = f.health(f.a)

                f.wall()
                f.press(0, 70)
                self.assertFalse(bot.attack_input)
                self.assertNotIn(primary, component.held)
                self.assertEqual(f.health(f.a), before_cover)
                self.assertEqual(bot.attack_target, f.a.entity)
                self.assertNotIn(bot.entity, match.combat.pins)

                f.geometry()
                for _ in range(380):
                    f.press(0)
                    if f.health(f.a) < before_cover:
                        break
                self.assertLess(f.health(f.a), before_cover)
                self.assertTrue(bot.attack_input)
                if key == "4":
                    self.assertEqual(match.combat.pins.get(bot.entity), f.a.body)
                npc_control.stop(match)

    def test_actual_npc_attack_damage_ends_camo_and_starts_cooldown(self):
        for key in self.catalog:
            with self.subTest(npc=self.catalog[key].name):
                fixture, bot = self.make(key, distance=2.5 if key == "4" else 3)
                self.press(fixture, bot, 8)
                self.press(fixture, bot, 0, 60)
                camo = fixture.camo().state(13)
                self.assertTrue(camo.flags & camo.RUNNING)
                self.assertEqual(fixture.health(fixture.a), 200)
                npc_control.start(fixture.run.match, fixture.a)
                for _ in range(380):
                    fixture.press(0)
                    if fixture.health(fixture.a) < 200:
                        break
                    self.assertTrue(camo.flags & camo.RUNNING)
                self.assertLess(fixture.health(fixture.a), 200)
                if key == "4":
                    self.assertEqual(fixture.health(fixture.a), 175)
                    self.assertEqual(fixture.run.match.combat.pins[bot.entity], fixture.a.body)
                npc_control.stop(fixture.run.match)
                fixture.press(0, 65)
                self.assertFalse(camo.flags & camo.RUNNING)
                self.assertGreater(camo.remaining(), 0)

    def test_actual_npc_damage_camo_reaction_during_absent_owner_input(self):
        for key, definition in self.catalog.items():
            with self.subTest(npc=definition.name):
                results = []
                for gap in (False, True):
                    f, bot = self.make(key, distance=2.5 if key == "4" else 3)
                    self.press(f, bot, 8)
                    self.press(f, bot, 0, 60)
                    camo = f.camo().state(13)
                    self.assertTrue(camo.flags & camo.RUNNING)
                    self.assertEqual(npc_control.start(f.run.match, f.a), bot.entity)
                    for _ in range(380):
                        if gap:
                            f.run.frame += 1
                            f.run.match.update(f.run.frame * combat.TICK, f.run.frame, frames=False)
                        else:
                            f.press(0)
                        if f.health(f.a) < 200:
                            break
                    self.assertLess(f.health(f.a), 200)
                    npc_control.stop(f.run.match)
                    if gap:
                        f.run.frame += 66
                        f.run.match.update(f.run.frame * combat.TICK, f.run.frame, frames=False)
                    else:
                        f.press(0, 65)
                    self.assertFalse(camo.flags & camo.RUNNING)
                    self.assertGreater(camo.remaining(), 0)
                    results.append((f.health(f.a), camo.remaining()))
                self.assertEqual(results[0], results[1])

    def test_authored_trooper_and_heavy_melee_contact_and_recovery(self):
        for key, damage, index in (("1", 30, 0x1422), ("5", 80, 0x140E)):
            with self.subTest(npc=self.catalog[key].name):
                fixture, bot = self.make(key, distance=2)
                bot.yaw = -32768
                component = bot.body_script.component
                component.edges([(31, True)])
                fixture.press(0, 35)
                self.assertEqual(fixture.health(fixture.a), 200 - damage)
                swing = next(i for i in component.instances.values() if i.graph.index == index)
                self.assertGreater(swing.vars[61].value(), 0)
                component.edges([(31, False)])
                fixture.press(0, 2)
                component.edges([(31, True)])
                fixture.press(0, 15)
                self.assertEqual(fixture.health(fixture.a), 200 - damage)
                component.edges([(31, False)])
                fixture.press(0, 60)
                component.edges([(31, True)])
                fixture.press(0, 35)
                self.assertEqual(fixture.health(fixture.a), 200 - 2 * damage)

    def test_authored_npc_melee_respects_genji_parry_and_sigma_grasp(self):
        scenarios = (
            (shot_guards.GENJI, 16, 0, 0),
            (shot_guards.GENJI, 16, -32768, 0),
            (shot_guards.GENJI, 16, 0, 2),
            (shot_guards.GENJI, 16, 0, 32),
            (shot_guards.SIGMA, 8, 0, 0),
        )
        for key, damage in (("1", 30), ("5", 80)):
            for hero, guard_button, facing, silence in scenarios:
                with self.subTest(npc=self.catalog[key].name, hero=hero, facing=facing, silence=silence):
                    f, bot = self.make(key, distance=2, team=0)
                    match = f.run.match
                    fight = match.combat
                    match.game_map = replace(match.game_map, free_for_all=False)
                    f.a.team, f.b.team = 0, 1
                    f.b.spawned = True
                    self.assertTrue(match.switch_hero(f.b, heroes()[hero]))
                    f.a.mover.position[:] = (3, 0, 0)
                    f.b.mover.position[:] = (0, 0, 0)
                    for player in (f.a, f.b):
                        player.mover.body = None
                    f.press(0, 50)
                    f.run.step({f.b: (facing, 0, guard_button)})
                    guard = shot_guards.active_guard(f.b, fight.tick * combat.TICK * 1000)
                    self.assertIsNotNone(guard)
                    if silence:
                        if silence == 32:
                            f.a.body_script.set_ult_charge(1400)
                        angles = aim(fight._eye(f.a), (0, 1, 0))
                        for frame in range(80):
                            buttons = silence if silence == 2 or frame == 0 else 0
                            f.run.step({f.a: (*angles, buttons), f.b: (facing, 0, 0)})
                            if f.b.body in fight.hacks:
                                break
                        effect, = fight.hacks[f.b.body]
                        self.assertEqual(effect.application.source_entity(), f.a.body)
                        self.assertNotIn(bot.entity, fight.hacks)
                        f.run.step({f.b: (facing, 0, guard_button)})
                        self.assertIsNone(shot_guards.active_guard(f.b, fight.tick * combat.TICK * 1000))

                    before = f.health(f.b)
                    npc_health = fight.vitals[bot.entity].total
                    bot.yaw = -32768
                    bot.body_script.component.edges([(31, True)])
                    for _ in range(35):
                        f.run.step({f.b: (facing, 0, 0)})
                    parried = hero == shot_guards.GENJI and facing == 0 and not silence
                    self.assertEqual(f.health(f.b), before if parried else before - damage)
                    self.assertEqual(f.health(f.a), 200)
                    self.assertEqual(fight.vitals[bot.entity].total, npc_health)
                    if hero == shot_guards.SIGMA:
                        self.assertEqual(guard.instance.vars[21312].value(), 0)

    def test_authored_npc_melee_passes_reinhardt_barrier_but_not_static_wall(self):
        if not reinhardt_barrier.barrier_geometry.load()[0]:
            self.skipTest("requires the extracted Reinhardt shield mesh")
        for key, damage in (("1", 30), ("5", 80)):
            for wall in (False, True):
                with self.subTest(npc=self.catalog[key].name, wall=wall):
                    f, bot = self.make(key, distance=2, team=1)
                    match = f.run.match
                    fight = match.combat
                    match.game_map = replace(match.game_map, free_for_all=False)
                    f.a.team = 0
                    self.assertTrue(match.switch_hero(f.a, heroes()[reinhardt_barrier.HERO]))
                    f.a.mover.position[:] = (0, 0, 0)
                    f.a.mover.body = None
                    f.press(0, 50)
                    self.press(f, bot, 2, 30)
                    barrier, = fight.barriers.values()
                    self.assertTrue(barrier.mesh)
                    self.assertEqual(barrier.vitals.total, 1600)
                    if wall:
                        f.geometry((-3, 0, 1, 3, 0, 1, 3, 4, 1),
                                   (-3, 0, 1, 3, 4, 1, -3, 4, 1))

                    bot.yaw = -32768
                    bot.body_script.component.edges([(31, True)])
                    self.press(f, bot, 2, 35)
                    self.assertIs(fight.barriers.get(barrier.entity), barrier)
                    self.assertEqual(barrier.vitals.total, 1600)
                    self.assertEqual(f.health(f.a), 500 if wall else 500 - (damage - 5))

    def test_authored_npc_melee_respects_phase_at_contact_and_recovers(self):
        def arena(key, hero):
            f, bot = self.make(key, distance=2)
            self.assertTrue(f.run.match.switch_hero(f.a, heroes()[hero]))
            f.a.mover.position[:] = (0, 0, 0)
            f.a.mover.body = None
            f.press(0, 50)
            bot.yaw = -32768
            return f, bot.body_script.component

        for key, damage in (("1", 30), ("5", 80)):
            for hero in phase_defenses.PHASES:
                with self.subTest(npc=self.catalog[key].name, hero=hero):
                    baseline, component = arena(key, hero)
                    before = baseline.health(baseline.a)
                    start_frame = baseline.run.frame
                    component.edges([(31, True)])
                    for _ in range(35):
                        baseline.press(0)
                        if baseline.health(baseline.a) < before:
                            break
                    self.assertEqual(baseline.health(baseline.a), before - damage)
                    contact_frame = baseline.run.frame - start_frame
                    self.assertGreater(contact_frame, 2)

                    f, component = arena(key, hero)
                    fight = f.run.match.combat
                    self.assertEqual(f.health(f.a), before)
                    component.edges([(31, True)])
                    f.press(0, contact_frame - 2)
                    self.assertFalse(phase_defenses.active(f.a))
                    self.assertEqual(f.health(f.a), before)
                    f.press(8)
                    self.assertTrue(phase_defenses.active(f.a))
                    f.press(0, 12)
                    self.assertTrue(phase_defenses.active(f.a))
                    self.assertEqual(f.health(f.a), before)
                    self.assertFalse(fight.is_stunned(f.a))

                    component.edges([(31, False)])
                    f.press(0, 210)
                    self.assertFalse(phase_defenses.active(f.a))
                    self.assertEqual(f.health(f.a), before)
                    component.edges([(31, True)])
                    f.press(0, 35)
                    self.assertEqual(f.health(f.a), before - damage)

    def test_authored_npc_melee_is_absorbed_by_projected_barrier(self):
        for key, damage in (("1", 30), ("5", 80)):
            with self.subTest(npc=self.catalog[key].name):
                f, bot = self.make(key, distance=2)
                match = f.run.match
                fight = match.combat
                match.game_map = replace(match.game_map, free_for_all=False)
                f.a.team = f.b.team = 0
                f.b.spawned = True
                self.assertTrue(match.switch_hero(f.b, heroes()[reinhardt_barrier.ZARYA]))
                f.b.mover.position[:] = (3, 0, 0)
                f.b.mover.body = None
                f.press(0, 50)
                yaw, pitch = aim(fight._eye(f.b), (0, 1, 0))
                f.run.step({f.b: (yaw, pitch, 16)})
                f.press(0, 12)
                bubble, = fight.barriers.values()
                self.assertIs(bubble.protected, f.a)
                self.assertEqual(bubble.vitals.total, 200)

                bot.yaw = -32768
                component = bot.body_script.component
                component.edges([(31, True)])
                f.press(0, 35)
                self.assertEqual(f.health(f.a), 200)
                self.assertEqual(bubble.vitals.total, 200 - damage)
                self.assertAlmostEqual(f.b.body_script.component.entity_var(87).value(), damage / 5, places=4)

                component.edges([(31, False)])
                f.press(0, 135)
                self.assertNotIn(bubble.entity, fight.barriers)
                component.edges([(31, True)])
                f.press(0, 35)
                self.assertEqual(f.health(f.a), 200 - damage)

    def test_authored_npc_melee_respects_wall_range_team_and_stun(self):
        for key in ("1", "5"):
            for block in ("wall", "range", "team", "stun"):
                with self.subTest(npc=self.catalog[key].name, block=block):
                    fixture, bot = self.make(key, distance=5 if block == "range" else 2)
                    match = fixture.run.match
                    bot.yaw = -32768
                    if block == "wall":
                        fixture.wall()
                    if block == "team":
                        match.game_map = replace(match.game_map, free_for_all=False)
                        bot.team = fixture.a.team
                    if block == "stun":
                        match.combat.stuns[bot.entity] = fixture.run.frame + 50
                    bot.body_script.component.edges([(31, True)])
                    fixture.press(0, 35)
                    self.assertEqual(fixture.health(fixture.a), 200)

    def test_authored_npc_melee_remains_available_during_silence(self):
        for key, damage in (("1", 30), ("5", 80)):
            for button in (2, 0x20):
                with self.subTest(npc=self.catalog[key].name, button=button):
                    fixture, bot = self.make(key, distance=2)
                    fight = fixture.run.match.combat
                    if button == 0x20:
                        fixture.a.body_script.set_ult_charge(1400)
                    self.press(fixture, bot, button, 82)
                    self.assertIn(bot.entity, fight.hacks)
                    bot.yaw = -32768
                    bot.body_script.component.edges([(31, True)])
                    fixture.press(0, 35)
                    self.assertEqual(fixture.health(fixture.a), 200 - damage)
                    self.assertIn(bot.entity, fight.hacks)

    def weapon_impact_arena(self, key, *, team=1):
        f, bot = self.make(key, distance=3, team=team)
        f.b.spawned = True
        f.b.mover.position[:] = (2, 0, 0)
        f.b.mover.body = None
        f.press(0, 75)
        if key == "3":
            special = next(i for i in bot.body_script.component.instances.values()
                           if i.graph.index == 0x142C)
            self.assertEqual(send_instance_message(special, 0x0240000000000A45, {}, bot.entity), 1)
        self.press(f, bot, 0)
        return f, bot

    def calibrate_weapon_impact(self, key):
        baseline, bot = self.weapon_impact_arena(key)
        self.assertEqual(npc_control.start(baseline.run.match, baseline.a), bot.entity)
        baseline_start = baseline.run.frame
        for _ in range(380):
            baseline.press(0)
            if baseline.health(baseline.a) < 200:
                break
        self.assertLess(baseline.health(baseline.a), 200)

        f, bot = self.weapon_impact_arena(key)
        self.assertEqual(npc_control.start(f.run.match, f.a), bot.entity)
        return f, bot, baseline.run.frame - baseline_start, baseline.health(baseline.a)

    def npc_reinhardt_barrier_engagement(self, key, *, facing=0, friendly=False):
        if not reinhardt_barrier.barrier_geometry.load()[0]:
            self.skipTest("requires the extracted Reinhardt shield mesh")
        f, bot = self.weapon_impact_arena(key)
        match = f.run.match
        fight = match.combat
        match.game_map = replace(match.game_map, free_for_all=False)
        f.a.team = 0
        f.b.team = bot.team if friendly else f.a.team
        self.assertTrue(match.switch_hero(f.b, heroes()[reinhardt_barrier.HERO]))
        f.b.mover.position[:] = (0, 0, 1.5)
        f.b.mover.body = None
        f.a.mover.position[:] = (3, 0, 0)
        f.a.mover.body = None
        yaw, pitch = aim(fight._eye(f.a), (0, 1, 3))
        for _ in range(45):
            f.run.step({f.a: (yaw, pitch, 0), f.b: (facing, 0, 2)})
        barrier, = fight.barriers.values()
        self.assertEqual(barrier.vitals.total, 1600)
        self.assertEqual(npc_control.start(match, f.a), bot.entity)
        f.a.mover.position[:] = (0, 0, 0)
        f.a.mover.body = None
        return f, barrier

    def test_actual_npc_weapons_hit_reinhardt_barrier_then_body_after_release(self):
        for key, damage in (("1", 20), ("2", 2), ("3", 100), ("5", 5)):
            with self.subTest(npc=self.catalog[key].name):
                f, barrier = self.npc_reinhardt_barrier_engagement(key)
                fight = f.run.match.combat

                if key in ("2", "3"):
                    for _ in range(170):
                        f.run.step({f.b: (0, 0, 2)})
                        if fight.rockets:
                            break
                    rocket, = fight.rockets
                    f.geometry((-3, 0, 2.8, 3, 0, 2.8, 3, 4, 2.8),
                               (-3, 0, 2.8, 3, 4, 2.8, -3, 4, 2.8))
                    f.run.step({f.b: (0, 0, 2)})
                    self.assertNotIn(rocket, fight.rockets)
                    self.assertEqual(barrier.vitals.total, 1600)
                    self.assertEqual(f.health(f.b), 500)
                    self.assertEqual(f.health(f.a), 200)
                    f.geometry()

                for _ in range(220):
                    f.run.step({f.b: (0, 0, 2)})
                    if barrier.vitals.total < 1600 or f.health(f.b) < 500:
                        break
                self.assertEqual(barrier.vitals.total, 1600 - damage)
                self.assertEqual(f.health(f.b), 500)
                self.assertEqual(f.health(f.a), 200)

                for _ in range(220):
                    f.run.step({f.b: (0, 0, 0)})
                    if f.health(f.b) < 500:
                        break
                self.assertNotIn(barrier.entity, fight.barriers)
                self.assertLess(f.health(f.b), 500)
                self.assertEqual(f.health(f.a), 200)

    def test_actual_npc_weapons_ignore_rear_and_friendly_reinhardt_barriers(self):
        for key, damage in (("1", 20), ("2", 2), ("3", 100), ("5", 5)):
            for friendly in (False, True):
                with self.subTest(npc=self.catalog[key].name, friendly=friendly):
                    facing = 0 if friendly else -32768
                    f, barrier = self.npc_reinhardt_barrier_engagement(key, facing=facing, friendly=friendly)
                    for _ in range(220):
                        f.run.step({f.b: (facing, 0, 2)})
                        if f.health(f.a) < 200 or f.health(f.b) < 500:
                            break
                    self.assertEqual(barrier.vitals.total, 1600)
                    self.assertIn(barrier.entity, f.run.match.combat.barriers)
                    self.assertEqual(f.health(f.a), 200 - damage if friendly else 200)
                    armour_loss = damage - min(damage / 2, 5)
                    self.assertEqual(f.health(f.b), 500 if friendly else 500 - armour_loss)

    def test_actual_npc_weapons_hit_reinhardt_after_sombra_lowers_barrier(self):
        for key, damage in (("1", 20), ("2", 2), ("3", 100), ("5", 5)):
            armour_loss = damage - min(damage / 2, 5)
            for silence, barrier_hp in ((2, 1600), (32, 0)):
                with self.subTest(npc=self.catalog[key].name, silence=silence):
                    if not reinhardt_barrier.barrier_geometry.load()[0]:
                        self.skipTest("requires the extracted Reinhardt shield mesh")
                    f, bot = self.weapon_impact_arena(key)
                    match = f.run.match
                    fight = match.combat
                    self.assertTrue(match.switch_hero(f.b, heroes()[reinhardt_barrier.HERO]))
                    f.b.mover.position[:] = (0, 0, 1.5)
                    f.a.mover.position[:] = (3, 0, 0)
                    for player in (f.a, f.b):
                        player.mover.body = None
                    angles = aim(fight._eye(f.a), (0, 1, 1.5))
                    for _ in range(45):
                        f.run.step({f.a: (*angles, 0), f.b: (0, 0, 2)})
                    barrier, = fight.barriers.values()
                    self.assertEqual(barrier.vitals.total, 1600)
                    if silence == 32:
                        f.a.body_script.set_ult_charge(1400)
                    for frame in range(85):
                        buttons = silence if silence == 2 or frame == 0 else 0
                        f.run.step({f.a: (*angles, buttons), f.b: (0, 0, 2)})
                        if f.b.body in fight.hacks:
                            break
                    effect, = fight.hacks[f.b.body]
                    self.assertEqual(effect.application.source_entity(), f.a.body)
                    self.assertEqual(barrier.vitals.total, barrier_hp)
                    self.assertFalse(fight.barriers)
                    self.assertFalse(barrier.component.instances)
                    self.assertEqual(f.health(f.b), 500)

                    angles = aim(fight._eye(f.a), (0, 1, 3))
                    f.run.step({f.a: (*angles, 0), f.b: (0, 0, 0)})
                    self.assertEqual(npc_control.start(match, f.a), bot.entity)
                    f.a.mover.position[:] = (0, 0, 0)
                    f.a.mover.body = None
                    for _ in range(220):
                        f.run.step({f.b: (0, 0, 2)})
                        if f.health(f.b) < 500:
                            break
                    self.assertIs(fight.hacks[f.b.body][0], effect)
                    self.assertFalse(fight.barriers)
                    self.assertEqual(barrier.vitals.total, barrier_hp)
                    self.assertEqual(f.health(f.b), 500 - armour_loss)
                    self.assertEqual(f.health(f.a), 200)

                    self.assertEqual(npc_control.stop(match), 1)
                    f.a.mover.position[:] = (3, 0, 0)
                    f.a.mover.body = None
                    angles = aim(fight._eye(f.a), (0, 1, 3))
                    for _ in range(480):
                        f.run.step({f.a: (*angles, 0)})
                        if f.b.body not in fight.hacks and not fight.rockets:
                            break
                    self.assertNotIn(f.b.body, fight.hacks)
                    for _ in range(180):
                        f.run.step({f.a: (*angles, 0), f.b: (0, 0, 2)})
                        if fight.barriers:
                            break
                    recovered, = fight.barriers.values()
                    self.assertNotEqual(recovered.entity, barrier.entity)
                    recovered_hp = recovered.vitals.total
                    self.assertGreater(recovered_hp, 100)

                    self.assertEqual(npc_control.start(match, f.a), bot.entity)
                    f.a.mover.position[:] = (0, 0, 0)
                    f.a.mover.body = None
                    for _ in range(220):
                        f.run.step({f.b: (0, 0, 2)})
                        if recovered.vitals.total < recovered_hp:
                            break
                    self.assertEqual(recovered.vitals.total, recovered_hp - damage)
                    self.assertEqual(f.health(f.b), 500 - armour_loss)
                    self.assertEqual(f.health(f.a), 200)

    def test_actual_npc_projectiles_resolve_nearer_reinhardt_or_sigma_defense(self):
        for key in ("2", "3"):
            for shield_first in (False, True):
                with self.subTest(npc=self.catalog[key].name, shield_first=shield_first):
                    _, _, hit_frame, hit_health = self.calibrate_weapon_impact(key)
                    f, barrier = self.npc_reinhardt_barrier_engagement(key)
                    match = f.run.match
                    fight = match.combat
                    bot = match.bots[0]
                    bot.position = (0, 0, 8)
                    f.b.mover.position[:] = (0, 0, 4 if shield_first else 1.5)
                    f.b.mover.body = None
                    sigma = match.add_player(3, "Sigma", shot_guards.SIGMA, f.a.team, False)
                    in_world(match, sigma)
                    f.run.players.append(sigma)
                    sigma.client.bot_stream = lambda body: None
                    self.assertTrue(match.switch_hero(sigma, heroes()[shot_guards.SIGMA]))
                    sigma.mover.position[:] = (0, 0, 1.5 if shield_first else 4)
                    sigma.mover.body = None

                    for _ in range(hit_frame - 24):
                        f.run.step({f.b: (0, 0, 2)})
                    f.run.step({f.b: (0, 0, 2), sigma: (0, 0, 8)})
                    guard = shot_guards.active_guard(sigma, fight.tick * combat.TICK * 1000)
                    self.assertIsNotNone(guard)
                    for _ in range(30):
                        f.run.step({f.b: (0, 0, 2)})
                        if fight.rockets:
                            break
                    rocket, = fight.rockets
                    self.assertIn(rocket.definition, shot_guards.NPC_GUARDED_PROJECTILES)
                    for _ in range(20):
                        f.run.step({f.b: (0, 0, 2)})
                        if rocket not in fight.rockets:
                            break
                    self.assertNotIn(rocket, fight.rockets)
                    npc_control.stop(match)
                    damage = 200 - hit_health
                    self.assertEqual(barrier.vitals.total, 1600 - damage if shield_first else 1600)
                    self.assertEqual(guard.instance.vars[21312].value(), 0 if shield_first else damage)
                    self.assertEqual((f.health(f.a), f.health(f.b), f.health(sigma)), (200, 500, 400))

    def npc_guard_engagement(self, key, hero, facing):
        _, _, hit_frame, hit_health = self.calibrate_weapon_impact(key)
        f, bot = self.weapon_impact_arena(key)
        match = f.run.match
        fight = match.combat
        match.game_map = replace(match.game_map, free_for_all=False)
        f.a.team = f.b.team = 0
        self.assertTrue(match.switch_hero(f.b, heroes()[hero]))
        f.press(0, 50)
        self.press(f, bot, 0)
        self.assertEqual(npc_control.start(match, f.a), bot.entity)
        f.b.mover.position[:] = (0, 0, 1.5)
        f.b.mover.body = None
        f.press(0, hit_frame - 18)
        f.run.step({f.b: (facing, 0, 16 if hero == shot_guards.GENJI else 8)})
        guard = shot_guards.active_guard(f.b, fight.tick * combat.TICK * 1000)
        self.assertIsNotNone(guard)
        self.assertEqual(fight.vitals[bot.entity].total, self.catalog[key].health
                         + self.catalog[key].armour + self.catalog[key].shields)
        return f, bot, guard, 200 - hit_health

    def test_actual_npc_hitscan_resolves_genji_and_sigma_guard_direction(self):
        for key in ("1", "5"):
            for hero in (shot_guards.GENJI, shot_guards.SIGMA):
                for facing in (0, -32768):
                    with self.subTest(npc=self.catalog[key].name, hero=hero, facing=facing):
                        f, bot, guard, damage = self.npc_guard_engagement(key, hero, facing)
                        fight = f.run.match.combat
                        vitals = fight.vitals[bot.entity]
                        max_health = f.health(f.b)
                        has_armour = vitals.now[ARMOUR_POOL] > 0
                        for _ in range(17):
                            f.run.step({f.b: (facing, 0, 0)})
                        npc_control.stop(f.run.match)
                        self.assertEqual(f.health(f.a), 200)
                        self.assertEqual(f.health(f.b), max_health if facing == 0 else max_health - damage)
                        if hero == shot_guards.GENJI and facing == 0:
                            resolved = damage - min(damage / 2, 5) if has_armour else damage
                            self.assertEqual(vitals.total, vitals.maximum - resolved)
                            self.assertEqual(len(f.b.client.messages(combat.HIT)), 1)
                        else:
                            self.assertEqual(vitals.total, vitals.maximum)
                        if hero == shot_guards.SIGMA:
                            absorbed = damage if facing == 0 else 0
                            self.assertEqual(guard.instance.vars[21312].value(), absorbed)
                            f.press(0, 145)
                            self.assertAlmostEqual(fight.vitals[f.b.body].temporary_shield_total,
                                                   absorbed * 0.6, places=5)

    def test_actual_npc_weapons_reach_defenders_after_sombra_disables_guards(self):
        for key, damage in (("1", 20), ("2", 2), ("3", 100), ("5", 5)):
            for hero, guard_button in ((shot_guards.GENJI, 16), (shot_guards.SIGMA, 8)):
                for silence in (2, 32):
                    with self.subTest(npc=self.catalog[key].name, hero=hero, silence=silence):
                        f, bot = self.weapon_impact_arena(key)
                        match = f.run.match
                        fight = match.combat
                        self.assertTrue(match.switch_hero(f.b, heroes()[hero]))
                        f.b.mover.position[:] = (2, 0, 0)
                        f.b.mover.body = None
                        f.press(0, 50)
                        f.run.step({f.b: (0, 0, guard_button)})
                        self.assertIsNotNone(shot_guards.active_guard(f.b, fight.tick * combat.TICK * 1000))
                        if silence == 32:
                            f.a.body_script.set_ult_charge(1400)
                        f.press(silence, 35 if silence == 32 else 75)
                        effect, = fight.hacks[f.b.body]
                        self.assertEqual(effect.application.source_entity(), f.a.body)
                        self.assertIsNone(shot_guards.active_guard(f.b, fight.tick * combat.TICK * 1000))
                        before = f.health(f.b)
                        source_health = fight.vitals[bot.entity].total

                        self.press(f, bot, 0, 2)
                        self.assertEqual(npc_control.start(match, f.a), bot.entity)
                        f.b.mover.position[:] = (0, 0, 1.5)
                        f.b.mover.body = None
                        for _ in range(170):
                            f.run.step({f.b: (0, 0, guard_button)})
                            if f.health(f.b) < before:
                                break
                        self.assertIn(f.b.body, fight.hacks)
                        self.assertIsNone(shot_guards.active_guard(f.b, fight.tick * combat.TICK * 1000))
                        self.assertEqual(f.health(f.b), before - damage)
                        self.assertEqual(f.health(f.a), 200)
                        self.assertEqual(fight.vitals[bot.entity].total, source_health)

    def npc_projectile_guard_contact(self, key, hero, facing):
        f, bot, guard, damage = self.npc_guard_engagement(key, hero, facing)
        match = f.run.match
        fight = match.combat
        for _ in range(18):
            f.run.step({f.b: (facing, 0, 0)})
            if fight.rockets:
                break
        rocket = next(shot for shot in fight.rockets if shot.shooter is bot)
        definition = 0x04000000000023F0 if key == "2" else 0x0400000000001E80
        self.assertEqual(rocket.definition, definition)
        for _ in range(20):
            f.run.step({f.b: (facing, 0, 0)})
            if rocket not in fight.rockets:
                break
        self.assertNotIn(rocket, fight.rockets)
        self.assertEqual(f.health(f.a), 200)
        npc_control.stop(match)
        return f, bot, guard, rocket, damage

    def test_actual_npc_projectiles_return_from_allied_genji_deflect(self):
        for key in ("2", "3"):
            for facing in (0, -32768):
                with self.subTest(npc=self.catalog[key].name, facing=facing):
                    f, bot, _, rocket, damage = self.npc_projectile_guard_contact(
                        key, shot_guards.GENJI, facing,
                    )
                    vitals = f.run.match.combat.vitals[bot.entity]
                    if facing == 0:
                        self.assertIs(rocket.shooter, f.b)
                        self.assertEqual(rocket.source_body, f.b.body)
                        self.assertEqual(rocket.team, f.a.team)
                        self.assertEqual(f.health(f.b), 200)
                        self.assertEqual(vitals.total, vitals.maximum - damage)
                    else:
                        self.assertIs(rocket.shooter, bot)
                        self.assertEqual(f.health(f.b), 200 - damage)
                        self.assertEqual(vitals.total, vitals.maximum)

    def test_actual_npc_projectiles_feed_allied_sigma_grasp(self):
        for key in ("2", "3"):
            for facing in (0, -32768):
                with self.subTest(npc=self.catalog[key].name, facing=facing):
                    f, bot, guard, rocket, damage = self.npc_projectile_guard_contact(
                        key, shot_guards.SIGMA, facing,
                    )
                    fight = f.run.match.combat
                    vitals = fight.vitals[bot.entity]
                    self.assertEqual(vitals.total, vitals.maximum)
                    self.assertIs(rocket.shooter, bot)
                    absorbed = damage if facing == 0 else 0
                    self.assertEqual(guard.instance.vars[21312].value(), absorbed)
                    self.assertEqual(f.health(f.b), 400 - (damage - absorbed))
                    f.press(0, 145)
                    self.assertAlmostEqual(fight.vitals[f.b.body].temporary_shield_total,
                                           absorbed * 0.6, places=5)
                    self.assertEqual(f.health(f.a), 200)

    def projected_bubble_engagement(self, key, *, camouflaged=False):
        f, bot = self.weapon_impact_arena(key)
        match = f.run.match
        fight = match.combat
        match.game_map = replace(match.game_map, free_for_all=False)
        f.a.team = f.b.team = 0
        self.assertTrue(match.switch_hero(f.b, heroes()[reinhardt_barrier.ZARYA]))
        f.b.mover.position[:] = (3, 0, 0)
        f.b.mover.body = None
        f.press(0, 50)
        if camouflaged:
            f.press(8)
            f.press(0, 60)
        if key == "3":
            # The calibrated first Sniper hit is 127 frames after engagement.
            self.press(f, bot, 0)
            self.assertEqual(npc_control.start(match, f.a), bot.entity)
            engaged = f.run.frame
            f.press(0, 48)
            self.assertEqual(f.health(f.a), 200)
        yaw, pitch = aim(fight._eye(f.b), (0, 1, 0))
        f.run.step({f.b: (yaw, pitch, 16)})
        self.press(f, bot, 0, 2)
        bubble, = fight.barriers.values()
        self.assertIs(bubble.protected, f.a)
        if key != "3":
            self.assertEqual(npc_control.start(match, f.a), bot.entity)
            engaged = f.run.frame
        return f, bot, bubble, engaged

    def test_actual_npc_weapons_resolve_projected_bubble_after_hack_or_emp(self):
        for key, damage in (("1", 20), ("2", 2), ("3", 100), ("5", 5)):
            _, _, hit_frame, _ = self.calibrate_weapon_impact(key)
            for ability in (2, 32):
                with self.subTest(npc=self.catalog[key].name, ability=ability):
                    f, bot = self.weapon_impact_arena(key, team=0)
                    match = f.run.match
                    fight = match.combat
                    match.game_map = replace(match.game_map, free_for_all=False)
                    f.a.team, f.b.team = 0, 1
                    zarya = match.add_player(3, "Zarya", reinhardt_barrier.ZARYA, f.b.team, False)
                    in_world(match, zarya)
                    f.run.players.append(zarya)
                    zarya.client.bot_stream = lambda body: None
                    self.assertTrue(match.switch_hero(zarya, heroes()[reinhardt_barrier.ZARYA]))
                    zarya.team = f.b.team
                    f.b.mover.position[:] = (0, 0, 1.5)
                    zarya.mover.position[:] = (0, 0, 25)
                    f.a.mover.position[:] = (3, 0, 0)
                    for player in (f.a, f.b, zarya):
                        player.mover.body = None
                    bot_angles = aim(fight._eye(f.b), (0, 1, 3))
                    for _ in range(50):
                        f.run.step({f.b: (*bot_angles, 0)})
                    # Start long windups before the short-lived projected bubble.
                    lead = max(0, hit_frame - 68) if ability == 2 else 0
                    if lead:
                        self.assertEqual(npc_control.start(match, f.b), bot.entity)
                        f.press(0, lead)
                        self.assertEqual(f.health(f.b), 200)

                    cast_angles = aim(fight._eye(zarya), (0, 1.3, 1.5))
                    f.run.step({zarya: (*cast_angles, 16)})
                    f.press(0, 2)
                    bubble, = fight.barriers.values()
                    self.assertIs(bubble.protected, f.b)
                    self.assertEqual(bubble.vitals.total, 200)
                    if ability == 32:
                        f.a.body_script.set_ult_charge(1400)
                    f.press(ability, 50 if ability == 2 else 35)
                    self.assertEqual(f.health(f.b), 200)
                    self.assertNotIn(zarya.body, fight.hacks)
                    self.assertNotIn(bot.entity, fight.hacks)

                    if ability == 2:
                        self.assertNotIn(f.b.body, fight.hacks)
                        self.assertEqual(f.weapon().state(53).remaining(), 0)
                        self.assertIn(bubble.entity, fight.barriers)
                        self.assertEqual(bubble.vitals.total, 200)
                    else:
                        effect, = fight.hacks[f.b.body]
                        self.assertEqual(effect.application.source_entity(), f.a.body)
                        self.assertNotIn(bubble.entity, fight.barriers)
                        self.assertEqual(bubble.vitals.total, 0)

                    if not lead:
                        f.run.step({f.b: (*bot_angles, 0)})
                        self.assertEqual(npc_control.start(match, f.b), bot.entity)
                    bubble_hp = bubble.vitals.total
                    for _ in range(220):
                        f.press(0)
                        if bubble.vitals.total < bubble_hp or f.health(f.b) < 200:
                            break
                    self.assertEqual(f.health(f.a), 200)
                    if ability == 2:
                        self.assertEqual(bubble.vitals.total, 200 - damage)
                        self.assertEqual(f.health(f.b), 200)
                        self.assertAlmostEqual(zarya.body_script.component.entity_var(87).value(),
                                               damage * 0.2, places=4)
                        self.assertEqual(npc_control.stop(match), 1)
                        for _ in range(130):
                            f.press(0)
                            if bubble.entity not in fight.barriers:
                                break
                        self.assertNotIn(bubble.entity, fight.barriers)
                        f.press(2, 75)
                        unshielded_effect, = fight.hacks[f.b.body]
                        self.assertEqual(unshielded_effect.application.source_entity(), f.a.body)
                        self.assertEqual(f.health(f.b), 200)
                    else:
                        self.assertIs(fight.hacks[f.b.body][0], effect)
                        self.assertEqual(f.health(f.b), 200 - damage)
                        self.assertFalse(fight.barriers)

    def test_actual_npc_weapon_absorption_preserves_held_hack(self):
        for key in ("1", "2", "3", "5"):
            with self.subTest(npc=self.catalog[key].name):
                _, _, hit_frame, hit_health = self.calibrate_weapon_impact(key)
                f, bot, bubble, engaged = self.projected_bubble_engagement(key)
                fight = f.run.match.combat
                if key == "2":
                    bot.body_script.component.set(14679, False)
                f.press(0, engaged + hit_frame - f.run.frame - 13)
                self.press(f, bot, 2, 12)
                hack = f.weapon().state(53)
                self.assertTrue(hack.flags & hack.RUNNING)
                self.assertEqual(bubble.vitals.total, 200)
                self.assertNotIn(bot.entity, fight.hacks)

                self.press(f, bot, 2)
                self.assertEqual(bubble.vitals.total, hit_health)
                self.assertEqual(f.health(f.a), 200)
                self.assertTrue(hack.flags & hack.RUNNING)
                npc_control.stop(f.run.match)
                self.press(f, bot, 2, 80)
                effect, = fight.hacks[bot.entity]
                self.assertEqual(effect.application.source_entity(), f.a.body)
                self.assertGreater(hack.remaining(), 6)
                self.assertEqual(f.health(f.a), 200)

    def test_actual_npc_weapons_hit_projected_bubble_then_interrupt_camo(self):
        for key in ("1", "2", "3", "5"):
            with self.subTest(npc=self.catalog[key].name):
                f, _, bubble, _ = self.projected_bubble_engagement(key, camouflaged=True)
                fight = f.run.match.combat
                camo = f.camo()
                ability = camo.state(13)
                tracker = camo.node_state(camo.graph.node(128))
                self.assertTrue(ability.flags & ability.RUNNING)
                self.assertTrue(tracker.active)
                for _ in range(380):
                    f.press(0)
                    if bubble.vitals.total < 200 or f.health(f.a) < 200:
                        break
                self.assertLess(bubble.vitals.total, 200)
                self.assertEqual(f.health(f.a), 200)
                self.assertIn(bubble.entity, fight.barriers)
                f.press(0)
                self.assertGreater(f.b.body_script.component.entity_var(87).value(), 0)
                for _ in range(380):
                    f.press(0)
                    if f.health(f.a) < 200:
                        break
                    self.assertTrue(ability.flags & ability.RUNNING)
                self.assertLess(f.health(f.a), 200)
                self.assertNotIn(bubble.entity, fight.barriers)
                npc_control.stop(f.run.match)
                f.press(0, 65)
                self.assertFalse(ability.flags & ability.RUNNING)
                self.assertFalse(tracker.active)
                self.assertGreater(ability.remaining(), 0)

    def test_actual_npc_weapon_hits_interrupt_running_hack_on_enemy_player(self):
        for key in ("1", "2", "3", "5"):
            with self.subTest(npc=self.catalog[key].name):
                f, _, hit_frame, hit_health = self.calibrate_weapon_impact(key)
                lead = min(12, hit_frame - 1)
                f.press(0, hit_frame - lead - 1)
                f.press(2, lead)
                hack = f.weapon().state(53)
                self.assertTrue(hack.flags & hack.RUNNING)
                self.assertEqual(f.health(f.a), 200)

                f.press(2)
                self.assertEqual(f.health(f.a), hit_health)
                npc_control.stop(f.run.match)
                impact_frame = f.run.frame
                for _ in range(40):
                    f.press(2)
                    if not hack.flags & hack.RUNNING:
                        break
                self.assertEqual(f.health(f.a), hit_health)
                self.assertFalse(hack.flags & hack.RUNNING)
                elapsed = (f.run.frame - impact_frame) * combat.TICK
                self.assertAlmostEqual(hack.remaining() + elapsed, 2, delta=0.04)
                self.assertFalse(f.hack_applications())
                self.assertNotIn(f.b.body, f.run.match.combat.hacks)

    def test_actual_npc_weapon_absorption_preserves_emp_at_one_health(self):
        for key in ("1", "2", "3", "5"):
            with self.subTest(npc=self.catalog[key].name):
                _, _, hit_frame, hit_health = self.calibrate_weapon_impact(key)
                f, bot, bubble, engaged = self.projected_bubble_engagement(key)
                fight = f.run.match.combat
                script = f.a.body_script
                script.ult_seconds = 0
                script.set_ult_charge(1400)
                emp = next(i for i in script.component.instances.values() if i.graph.index == 0x0B31)
                pulse = emp.node_state(emp.graph.node(40))
                f.press(0, engaged + hit_frame - f.run.frame - 2)
                f.press(32)
                cast = emp.state(1)
                self.assertTrue(cast.flags & cast.RUNNING)
                self.assertEqual(script.component.entity_var(342).value(), 0)
                self.assertEqual(pulse.counter, 0)
                self.assertEqual(bubble.vitals.total, 200)
                self.assertEqual(f.health(f.a), 200)
                fight.vitals[f.a.body].now[HEALTH_POOL] = 1

                f.press(0)
                self.assertEqual(bubble.vitals.total, hit_health)
                self.assertEqual(f.health(f.a), 1)
                self.assertEqual(pulse.counter, 0)
                npc_control.stop(f.run.match)
                f.press(0, 35)
                self.assertEqual(pulse.counter, 1)
                effect, = fight.hacks[bot.entity]
                self.assertEqual(effect.application.source_entity(), f.a.body)
                self.assertNotIn(f.b.body, fight.hacks)
                self.assertEqual(f.health(f.a), 1)

    def test_actual_npc_weapon_hits_gate_emp_pulse_on_caster_survival(self):
        for key in ("1", "2", "3", "5"):
            for lethal in (False, True):
                with self.subTest(npc=self.catalog[key].name, lethal=lethal):
                    f, bot, hit_frame, hit_health = self.calibrate_weapon_impact(key)
                    fight = f.run.match.combat
                    script = f.a.body_script
                    script.set_ult_charge(1400)
                    f.press(0, hit_frame - 2)
                    f.press(32)
                    emp = next(i for i in script.component.instances.values() if i.graph.index == 0x0B31)
                    cast = emp.state(1)
                    pulse = emp.node_state(emp.graph.node(40))
                    self.assertTrue(cast.flags & cast.RUNNING)
                    self.assertEqual(script.component.entity_var(342).value(), 0)
                    self.assertEqual(f.health(f.a), 200)
                    self.assertEqual(pulse.counter, 0)

                    body = f.a.body
                    vitals = fight.vitals[body]
                    if lethal:
                        vitals.now[HEALTH_POOL] = 1
                    f.press(0)
                    self.assertEqual(f.health(f.a), 0 if lethal else hit_health)
                    self.assertEqual(pulse.counter, 0)
                    if lethal:
                        self.assertEqual(vitals.killer, bot.entity)
                        self.assertEqual(vitals.death_tick, f.run.frame)
                    npc_control.stop(f.run.match)

                    f.press(0, 35)
                    self.assertEqual(pulse.counter, 0 if lethal else 1)
                    for entity in (f.b.body, bot.entity):
                        if lethal:
                            self.assertNotIn(entity, fight.hacks)
                        else:
                            effect, = fight.hacks[entity]
                            self.assertEqual(effect.application.source_entity(), body)
                    self.assertEqual(f.health(f.b), 200)
                    f.press(0, 60)
                    self.assertEqual(pulse.counter, 0 if lethal else 1)
                    if lethal:
                        self.assertEqual(script.component.entity_var(342).value(), 0)
                    else:
                        self.assertFalse(cast.flags & cast.RUNNING)

    def test_actual_npc_fire_still_kills_before_paid_emp_in_a_long_gap(self):
        for key in ("1", "2", "3", "5"):
            with self.subTest(npc=self.catalog[key].name):
                f, bot, hit_frame, _ = self.calibrate_weapon_impact(key)
                fight = f.run.match.combat
                script = f.a.body_script
                script.set_ult_charge(1400)
                f.press(0, hit_frame - 2)
                f.press(32)
                cast = f.run.frame
                self.assertEqual(script.component.entity_var(342).value(), 0)
                self.assertEqual(f.health(f.a), 200)
                vitals = fight.vitals[f.a.body]
                vitals.now[HEALTH_POOL] = 1
                at = cast + 100
                f.run.match.update(at * combat.TICK, at)
                self.assertEqual(vitals.killer, bot.entity)
                self.assertEqual(vitals.death_tick, cast + 1)
                emp = next(i for i in script.component.instances.values() if i.graph.index == 0x0B31)
                self.assertEqual(emp.node_state(emp.graph.node(40)).counter, 0)
                self.assertNotIn(bot.entity, fight.hacks)
                self.assertNotIn(f.b.body, fight.hacks)
                self.assertEqual(script.component.entity_var(342).value(), 0)

    def test_authored_npc_melee_interrupts_sombra_hack(self):
        for key, damage in (("1", 30), ("5", 80)):
            with self.subTest(npc=self.catalog[key].name):
                fixture, bot = self.make(key, distance=2)
                self.press(fixture, bot, 2, 8)
                self.assertTrue(fixture.weapon().vars[8909].value())
                bot.yaw = -32768
                bot.body_script.component.edges([(31, True)])
                self.press(fixture, bot, 2, 35)
                self.assertEqual(fixture.health(fixture.a), 200 - damage)
                self.assertNotIn(bot.entity, fixture.run.match.combat.hacks)
                cooldown = fixture.weapon().state(53).remaining()
                self.assertGreater(cooldown, 0)
                self.assertLess(cooldown, 2)

    def test_heavy_special_authored_start_and_recovery(self):
        fixture, bot = self.make("5", distance=6)
        bot.yaw = -32768
        component = bot.body_script.component
        special = next(instance for instance in component.instances.values()
                       if instance.graph.index == 0x1408)

        component.edges([(35, True)])
        fixture.press(0)
        component.edges([(35, False)])
        self.assertEqual(special.vars[59].value(), 1)
        self.assertAlmostEqual(special.vars[3192].value().z, -1)
        bot.yaw = 0
        fixture.press(0)
        self.assertAlmostEqual(special.vars[3192].value().z, -1)
        fixture.press(0, 300)
        self.assertEqual(special.vars[59].value(), 0)
        self.assertGreater(special.vars[61].value(), 0)

        component.edges([(35, True)])
        fixture.press(0, 4)
        self.assertEqual(special.vars[59].value(), 0)

    def test_heavy_special_running_phase_moves_and_respects_static_wall(self):
        for case in ("open", "wall", "contact"):
            with self.subTest(case=case):
                fixture, bot = self.make("5", distance=6)
                bot.yaw = -32768 if case == "contact" else 0
                if case == "wall":
                    fixture.geometry((-4, 0, 7, 4, 5, 7, 4, 0, 7),
                                     (-4, 0, 7, -4, 5, 7, 4, 5, 7))
                component = bot.body_script.component
                component.edges([(35, True)])
                fixture.press(0, 45)
                self.assertEqual(bot.position[2], 6)
                fixture.press(0, 28)
                if case == "wall":
                    self.assertGreater(bot.position[2], 6.1)
                    self.assertLess(bot.position[2], 7)
                elif case == "open":
                    self.assertGreater(bot.position[2], 8)
                else:
                    self.assertLess(bot.position[2], 3)
                    special = next(item for item in component.instances.values()
                                   if item.graph.index == 0x1408)
                    self.assertEqual(special.vars[562].value(), (Entity(fixture.a.body),))
                    self.assertLess(fixture.a.mover.position[2], 0)
                fixture.press(0, 260)
                self.assertEqual(bot.velocity, (0, 0, 0))

    def test_heavy_world_gap_replays_charge_carry_and_wall_impact(self):
        for wall in (False, True):
            with self.subTest(wall=wall):
                outcomes = []
                for world_only in (False, True):
                    f, bot = self.make("5", distance=6)
                    match = f.run.match
                    fight = match.combat
                    if wall:
                        f.geometry((-4, 0, -1.65, 4, 0, -1.65, 4, 4, -1.65),
                                   (-4, 0, -1.65, 4, 4, -1.65, -4, 4, -1.65))
                    bot.yaw = -32768
                    bot.body_script.component.edges([(35, True)])
                    start = f.run.frame
                    at = start + 90
                    if world_only:
                        match.update(at * combat.TICK, at)
                    else:
                        f.press(0, 90)
                    vitals = fight.vitals[f.a.body]
                    self.assertEqual(vitals.total, 0 if wall else 200)
                    self.assertEqual(fight.pins.get(bot.entity), None if wall else f.a.body)
                    self.assertLess(f.a.mover.position[2], -1)
                    death = None
                    if wall:
                        self.assertEqual(vitals.killer, bot.entity)
                        death = vitals.death_tick - start
                    outcomes.append((bot.position, tuple(f.a.mover.position), death))
                self.assertLess(math.dist(outcomes[0][0], outcomes[1][0]), 0.002)
                self.assertLess(math.dist(outcomes[0][1], outcomes[1][1]), 0.002)
                self.assertEqual(outcomes[0][2], outcomes[1][2])

    def test_heavy_world_gap_respects_four_protections_during_real_charge(self):
        cases = [(tracer_combat.HERO, 16), (mei.HERO, 8)]
        cases.extend((hero, 8) for hero in phase_defenses.PHASES)
        for hero, button in cases:
            with self.subTest(hero=f"{hero:016X}"):
                positions = []
                for world_only in (False, True):
                    f, bot = self.make("5", distance=6)
                    match = f.run.match
                    fight = match.combat
                    match.game_map = replace(match.game_map, free_for_all=False)
                    f.b.spawned = True
                    self.assertTrue(match.switch_hero(f.b, heroes()[hero]))
                    f.a.team = f.b.team = 0
                    bot.team = 1
                    f.a.mover.position[:] = (6, 0, 0)
                    f.b.mover.position[:] = (0, 0, 0)
                    f.a.mover.body = f.b.mover.body = None
                    f.press(0, 205 if hero == tracer_combat.HERO else 50)
                    before = f.health(f.b)
                    bot.yaw = -32768
                    bot.body_script.component.edges([(35, True)])
                    f.press(0, 44)
                    f.press(button, shooter=f.b, target=f.a)
                    protected = mei.protected if hero == mei.HERO else phase_defenses.active
                    self.assertTrue(protected(f.b))
                    if world_only:
                        at = f.run.frame + 28
                        match.update(at * combat.TICK, at)
                    else:
                        f.press(0, 28)
                    self.assertTrue(protected(f.b))
                    self.assertLess(bot.position[2], 1)
                    self.assertNotIn(bot.entity, fight.pins)
                    self.assertEqual(f.health(f.b), before)
                    instances = f.b.body_script.component.instances.values()
                    self.assertFalse(any(i.graph.index == 0x1407 for i in instances))
                    positions.append(bot.position)
                self.assertEqual(positions[0], positions[1])

    def test_heavy_special_carried_body_triggers_wall_impact_and_release(self):
        for blocked in (False, True):
            with self.subTest(blocked=blocked):
                fixture, bot = self.make("5", distance=1.5)
                bot.yaw = -32768
                if blocked:
                    fixture.geometry((-4, 0, -0.9, 4, 0, -0.9, 4, 5, -0.9),
                                     (-4, 0, -0.9, 4, 5, -0.9, -4, 5, -0.9))
                component = bot.body_script.component
                special = next(item for item in component.instances.values() if item.graph.index == 0x1408)
                component.edges([(35, True)])
                fixture.press(0, 75)
                fight = fixture.run.match.combat
                carried = fixture.a.mover.position[2]
                self.assertAlmostEqual(bot.position[2] - carried, 1.5, delta=0.02)
                if blocked:
                    self.assertGreater(carried, -0.9)
                    self.assertLess(carried, -0.1)
                    self.assertEqual(bot.velocity, (0, 0, 0))
                    self.assertEqual(special.vars[59].value(), 2)
                    self.assertGreater(special.vars[61].value(), 0)
                    self.assertEqual(fight.vitals[fixture.a.body].killer, bot.entity)
                    self.assertTrue(fight.vitals[fixture.a.body].dead)
                    self.assertNotIn(bot.entity, fight.pins)
                    self.assertIsNotNone(fixture.a.body_script)
                    self.assertFalse(any(item.graph.index == 0x1407
                                         for item in fixture.a.body_script.component.instances.values()))
                    fixture.press(0, 80)
                    self.assertEqual(special.vars[59].value(), 2)
                    fixture.press(0, 60)
                    self.assertEqual(special.vars[59].value(), 0)
                    self.assertGreater(special.vars[61].value(), 0)
                else:
                    self.assertLess(carried, -1)
                    self.assertEqual(fight.pins.get(bot.entity), fixture.a.body)
                    self.assertFalse(fight.vitals[fixture.a.body].dead)

    def test_heavy_special_capture_uses_actual_victim_graph_and_releases(self):
        fixture, bot = self.make("5", distance=1.5)
        bot.yaw = -32768
        fight = fixture.run.match.combat
        victim = fixture.a.body_script.component
        bot.body_script.component.edges([(35, True)])
        fixture.press(0, 75)
        child, = [item for item in victim.instances.values() if item.graph.index == 0x1407]
        self.assertEqual(child.source_entity(), bot.entity)
        self.assertEqual(victim.entity_var(7539).value(), Entity(bot.entity))
        self.assertEqual(fight.pins.get(bot.entity), fixture.a.body)

        before = fixture.run.match.combat.vitals[bot.entity].total
        self.press(fixture, bot, FIRE, 15)
        self.assertEqual(fight.vitals[bot.entity].total, before)
        fixture.press(0, 250)
        self.assertNotIn(bot.entity, fight.pins)
        self.assertNotIn(child.id, victim.instances)
        self.assertIsNone(victim.entity_var(7539).value())
        self.press(fixture, bot, FIRE, 15)
        self.assertLess(fight.vitals[bot.entity].total, before)

    def start_heavy_capture(self):
        f, bot = self.make("5")
        bot.yaw = -32768
        bot.body_script.component.edges([(35, True)])
        return f, bot

    def heavy_capture_contact_frame(self):
        f, bot = self.start_heavy_capture()
        fight = f.run.match.combat
        start = f.run.frame
        for _ in range(75):
            f.press(0)
            if bot.entity in fight.pins:
                break
        self.assertEqual(fight.pins[bot.entity], f.a.body)
        return f.run.frame - start

    def test_actual_npc_capture_blocks_pistol_reload_and_quick_melee_until_release(self):
        for key in ("4", "5"):
            with self.subTest(npc=self.catalog[key].name):
                f, bot = self.make(key, distance=2 if key == "4" else 1.5)
                match = f.run.match
                fight = match.combat
                vitals = fight.vitals[bot.entity]
                ammo = f.weapon().vars[53]
                full = vitals.total
                self.press(f, bot, FIRE)
                self.press(f, bot, 0, 12)
                self.assertEqual(ammo.value(), 59)
                before = vitals.total
                self.assertEqual(before, full - 8)
                if key == "4":
                    self.assertEqual(npc_control.start(match, f.a), bot.entity)
                    f.press(0, 8)
                else:
                    bot.yaw = -32768
                    bot.body_script.component.edges([(35, True)])
                    f.press(0, 75)
                    bot.body_script.component.edges([(35, False)])

                self.assertEqual(fight.pins[bot.entity], f.a.body)
                for button in (FIRE, sombra_fixture.RELOAD, sombra_fixture.MELEE):
                    self.press(f, bot, button, 12)
                    self.press(f, bot, 0)
                    self.assertEqual(fight.pins[bot.entity], f.a.body)
                    self.assertEqual(vitals.total, before)
                    self.assertEqual(ammo.value(), 59)
                if key == "4":
                    npc_control.stop(match)
                f.press(0, 250)
                self.assertNotIn(bot.entity, fight.pins)
                self.assertEqual(vitals.total, before)
                self.assertEqual(ammo.value(), 59)

                # Restore a common open melee lane after Heavy's carry moves both actors.
                f.a.mover.position[:] = (0, 0, 0)
                f.a.mover.body = None
                bot.position = (0, 0, 1.5)
                self.press(f, bot, 0, 10)
                self.press(f, bot, FIRE)
                self.assertEqual(ammo.value(), 58)
                self.assertEqual(vitals.total, before - 8)
                self.press(f, bot, 0, 10)
                self.press(f, bot, sombra_fixture.RELOAD)
                self.press(f, bot, 0, 130)
                self.assertEqual(ammo.value(), 60)
                self.assertEqual(vitals.total, before - 8)
                self.press(f, bot, sombra_fixture.MELEE)
                self.press(f, bot, 0, 45)
                self.assertEqual(vitals.total, before - 38)

    def test_heavy_special_capture_cancels_held_hack_with_short_cooldown(self):
        contact = self.heavy_capture_contact_frame()
        f, bot = self.start_heavy_capture()
        fight = f.run.match.combat
        f.press(0, contact - 13)
        for _ in range(12):
            self.press(f, bot, 2)
        hack = f.weapon().state(53)
        self.assertTrue(hack.flags & hack.RUNNING)
        self.assertEqual(f.weapon().vars[8909].value(), Entity(bot.entity))
        self.assertNotIn(bot.entity, fight.hacks)
        self.assertNotIn(bot.entity, fight.pins)
        self.assertEqual(f.health(f.a), 200)

        self.press(f, bot, 2)
        self.assertEqual(fight.pins[bot.entity], f.a.body)
        self.assertFalse(hack.flags & hack.RUNNING)
        self.assertEqual(f.health(f.a), 200)
        self.assertAlmostEqual(hack.remaining(), 2 - combat.TICK, places=5)
        self.press(f, bot, 2, 90)
        self.assertEqual(fight.pins[bot.entity], f.a.body)
        self.assertNotIn(bot.entity, fight.hacks)
        self.assertAlmostEqual(hack.remaining(), 2 - 91 * combat.TICK, places=5)

    def test_heavy_special_capture_ends_active_camo_and_clears_proximity(self):
        contact = self.heavy_capture_contact_frame()
        f, bot = self.make("5")
        fight = f.run.match.combat
        f.press(8)
        f.press(0, 60)
        camo = f.camo()
        ability = camo.state(13)
        tracker = camo.states[camo.graph.node(128).state]
        self.assertTrue(ability.flags & ability.RUNNING)
        self.assertTrue(tracker.active)
        self.assertEqual(tracker.targets, (bot.entity,))

        bot.yaw = -32768
        bot.body_script.component.edges([(35, True)])
        f.press(0, contact - 1)
        self.assertTrue(ability.flags & ability.RUNNING)
        self.assertNotIn(bot.entity, fight.pins)
        f.press(0)
        self.assertEqual(fight.pins[bot.entity], f.a.body)
        self.assertFalse(ability.flags & ability.RUNNING)
        self.assertEqual(f.health(f.a), 200)
        self.assertGreater(ability.remaining(), 0)

        f.press(8, 90)
        self.assertEqual(fight.pins[bot.entity], f.a.body)
        self.assertFalse(ability.flags & ability.RUNNING)
        self.assertFalse(tracker.active)
        self.assertEqual(tracker.targets, ())
        self.assertFalse(camo.vars[12382].value())

    def capture_sombra(self, key):
        if key == "4":
            f, bot = self.make(key, distance=2)
            self.press(f, bot, 0, 2)
            self.assertEqual(npc_control.start(f.run.match, f.a), bot.entity)
            f.press(0, 8)
        else:
            f, bot = self.start_heavy_capture()
            f.press(0, 75)
            bot.body_script.component.edges([(35, False)])
        self.assertEqual(f.run.match.combat.pins[bot.entity], f.a.body)
        return f, bot

    def test_actual_npc_capture_blocks_camo_admission_without_cooldown(self):
        for key in ("4", "5"):
            with self.subTest(npc=self.catalog[key].name):
                f, bot = self.capture_sombra(key)
                match = f.run.match
                fight = match.combat
                camo = f.camo()
                ability = camo.state(13)
                tracker = camo.node_state(camo.graph.node(128))
                f.press(8, 60)
                f.press(0)
                self.assertEqual(fight.pins[bot.entity], f.a.body)
                self.assertFalse(ability.flags & ability.RUNNING)
                self.assertEqual(ability.remaining(), 0)
                self.assertFalse(tracker.active)

                if key == "4":
                    npc_control.stop(match)
                f.press(0, 250)
                self.assertNotIn(bot.entity, fight.pins)
                self.assertFalse(ability.flags & ability.RUNNING)
                self.assertEqual(ability.remaining(), 0)
                self.assertFalse(tracker.active)

                f.press(8)
                f.press(0, 60)
                self.assertTrue(ability.flags & ability.RUNNING)
                self.assertTrue(tracker.active)

    def test_actual_npc_capture_blocks_hack_admission_without_cooldown(self):
        for key in ("4", "5"):
            with self.subTest(npc=self.catalog[key].name):
                f, bot = self.capture_sombra(key)
                match = f.run.match
                fight = match.combat
                hack = f.weapon().state(53)
                before = fight.vitals[bot.entity].total
                self.press(f, bot, 2, 75)
                self.press(f, bot, 0)
                self.assertEqual(fight.pins[bot.entity], f.a.body)
                self.assertFalse(hack.flags & hack.RUNNING)
                self.assertEqual(hack.remaining(), 0)
                self.assertNotIn(bot.entity, fight.hacks)

                if key == "4":
                    npc_control.stop(match)
                f.press(0, 250)
                self.assertNotIn(bot.entity, fight.pins)
                self.assertNotIn(bot.entity, fight.hacks)
                self.assertEqual(hack.remaining(), 0)

                f.a.mover.position[:] = (0, 0, 0)
                f.a.mover.body = None
                bot.position = (0, 0, 2)
                self.press(f, bot, 0, 10)
                self.press(f, bot, 2, 12)
                self.assertTrue(hack.flags & hack.RUNNING)
                self.assertNotIn(bot.entity, fight.hacks)
                self.press(f, bot, 2, 88)
                effect, = fight.hacks[bot.entity]
                self.assertEqual(effect.application.source_entity(), f.a.body)
                self.assertGreater(hack.remaining(), 6)
                self.assertEqual(fight.vitals[bot.entity].total, before)

    def test_actual_npc_capture_preserves_full_emp_charge_until_fresh_press(self):
        for key in ("4", "5"):
            with self.subTest(npc=self.catalog[key].name):
                f, bot = self.capture_sombra(key)
                match = f.run.match
                fight = match.combat
                script = f.a.body_script
                script.ult_seconds = 0
                script.set_ult_charge(1400)
                emp = next(i for i in script.component.instances.values() if i.graph.index == 0x0B31)
                cast = emp.state(1)
                pulse = emp.node_state(emp.graph.node(40))
                charge = script.component.entity_var(342)
                f.press(32, 35)
                f.press(0)
                self.assertEqual(fight.pins[bot.entity], f.a.body)
                self.assertFalse(cast.flags & cast.RUNNING)
                self.assertEqual(charge.value(), 1400)
                self.assertEqual(pulse.counter, 0)

                if key == "4":
                    npc_control.stop(match)
                f.press(0, 250)
                self.assertNotIn(bot.entity, fight.pins)
                self.assertEqual(charge.value(), 1400)
                self.assertEqual(pulse.counter, 0)
                self.assertNotIn(bot.entity, fight.hacks)

                # Restore a common open pulse range after Heavy's carry moves both actors.
                f.a.mover.position[:] = (0, 0, 0)
                f.a.mover.body = None
                bot.position = (0, 0, 2)
                f.press(0, 10)
                f.press(32)
                self.assertEqual(charge.value(), 0)
                self.assertTrue(cast.flags & cast.RUNNING)
                f.press(0, 35)
                self.assertEqual(pulse.counter, 1)
                effect, = fight.hacks[bot.entity]
                self.assertEqual(effect.application.source_entity(), f.a.body)

    def test_heavy_special_capture_cancels_paid_emp_before_pulse(self):
        contact = self.heavy_capture_contact_frame()
        f, bot = self.start_heavy_capture()
        fight = f.run.match.combat
        script = f.a.body_script
        script.set_ult_charge(1400)
        emp = next(i for i in script.component.instances.values() if i.graph.index == 0x0B31)
        cast = emp.state(1)
        pulse = emp.node_state(emp.graph.node(40))
        f.press(0, contact - 2)
        f.press(32)
        self.assertTrue(cast.flags & cast.RUNNING)
        self.assertEqual(script.component.entity_var(342).value(), 0)
        self.assertEqual(pulse.counter, 0)
        self.assertNotIn(bot.entity, fight.pins)

        f.press(0)
        self.assertEqual(fight.pins[bot.entity], f.a.body)
        self.assertFalse(cast.flags & cast.RUNNING)
        f.press(0, 300)
        self.assertNotIn(bot.entity, fight.pins)
        self.assertEqual(pulse.counter, 0)
        self.assertNotIn(bot.entity, fight.hacks)
        self.assertLess(script.component.entity_var(342).value(), 1400)

    def test_heavy_special_capture_cancels_paid_emp_during_world_only_update(self):
        contact = self.heavy_capture_contact_frame()
        f, bot = self.start_heavy_capture()
        match = f.run.match
        script = f.a.body_script
        script.set_ult_charge(1400)
        emp = next(i for i in script.component.instances.values() if i.graph.index == 0x0B31)
        ability = emp.state(1)
        pulse = emp.node_state(emp.graph.node(40))
        f.press(0, contact - 2)
        f.press(32)
        cast = f.run.frame
        self.assertEqual(script.component.entity_var(342).value(), 0)
        self.assertNotIn(bot.entity, match.combat.pins)
        at = cast + 100
        match.update(at * combat.TICK, at)
        self.assertEqual(match.combat.pins[bot.entity], f.a.body)
        self.assertFalse(ability.flags & ability.RUNNING)
        self.assertEqual(pulse.counter, 0)
        self.assertNotIn(bot.entity, match.combat.hacks)

    def test_heavy_capture_excludes_actual_phase_and_accepts_after_expiry(self):
        contact = self.heavy_capture_contact_frame()
        for hero in (0x02E0000000000002, 0x02E00000000001A2):
            for phased in (False, True):
                with self.subTest(hero=hero, phased=phased):
                    f, bot = self.make("5")
                    match = f.run.match
                    fight = match.combat
                    self.assertTrue(match.switch_hero(f.a, heroes()[hero]))
                    f.a.mover.position[:] = (0, 0, 0)
                    f.a.mover.body = None
                    f.press(0, 50)
                    if not phased:
                        f.press(8)
                        self.assertTrue(phase_defenses.active(f.a))
                        f.press(0, 210)
                        self.assertFalse(phase_defenses.active(f.a))

                    bot.yaw = -32768
                    bot.body_script.component.edges([(35, True)])
                    f.press(0, contact - 2)
                    self.assertNotIn(bot.entity, fight.pins)
                    if phased:
                        f.press(8)
                        self.assertTrue(phase_defenses.active(f.a))
                    else:
                        f.press(0)
                    before = f.health(f.a)
                    f.press(0, 16)
                    victim = f.a.body_script.component
                    children = [i for i in victim.instances.values() if i.graph.index == 0x1407]
                    self.assertEqual(fight.pins.get(bot.entity), None if phased else f.a.body)
                    self.assertEqual(len(children), 0 if phased else 1)
                    self.assertEqual(f.health(f.a), before)
                    if phased:
                        self.assertTrue(phase_defenses.active(f.a))
                    else:
                        self.assertEqual(children[0].source_entity(), bot.entity)

    def test_heavy_capture_excludes_actual_projected_barrier_until_expiry(self):
        for protected in (False, True):
            with self.subTest(protected=protected):
                f, bot = self.make("5")
                match = f.run.match
                fight = match.combat
                match.game_map = replace(match.game_map, free_for_all=False)
                f.a.team = f.b.team = 0
                f.b.spawned = True
                self.assertTrue(match.switch_hero(f.b, heroes()[reinhardt_barrier.ZARYA]))
                f.b.mover.position[:] = (3, 0, 0)
                f.b.mover.body = None
                f.press(0, 50)
                yaw, pitch = aim(fight._eye(f.b), (0, 1, 0))
                f.run.step({f.b: (yaw, pitch, 16)})
                f.press(0, 12)
                bubble, = fight.barriers.values()
                self.assertIs(bubble.protected, f.a)
                if not protected:
                    f.press(0, 150)
                    self.assertNotIn(bubble.entity, fight.barriers)

                bot.yaw = -32768
                bot.body_script.component.edges([(35, True)])
                f.press(0, 75)
                instances = f.a.body_script.component.instances.values()
                children = [i for i in instances if i.graph.index == 0x1407]
                self.assertEqual(fight.pins.get(bot.entity), None if protected else f.a.body)
                self.assertEqual(len(children), 0 if protected else 1)
                self.assertEqual(f.health(f.a), 200)
                if protected:
                    self.assertIn(bubble.entity, fight.barriers)
                    self.assertEqual(bubble.vitals.total, 150)
                    self.assertAlmostEqual(f.b.body_script.component.entity_var(87).value(), 10, places=4)
                    self.assertAlmostEqual(f.a.mover.position[2], 0, places=4)
                else:
                    self.assertEqual(children[0].source_entity(), bot.entity)

    def test_heavy_capture_releases_when_allied_pistol_kills_source(self):
        f, bot = self.make("5")
        match = f.run.match
        fight = match.combat
        match.game_map = replace(match.game_map, free_for_all=False)
        f.a.team = f.b.team = 0
        f.b.spawned = True
        self.assertTrue(match.switch_hero(f.b, heroes()[sombra_fixture.SOMBRA]))
        f.b.mover.position[:] = (3, 0, -4)
        f.b.mover.body = None
        f.press(0, 50)

        bot.yaw = -32768
        bot.body_script.component.edges([(35, True)])
        f.press(0, 62)
        victim = f.a.body_script.component
        child, = [i for i in victim.instances.values() if i.graph.index == 0x1407]
        self.assertEqual(child.source_entity(), bot.entity)
        self.assertEqual(fight.pins[bot.entity], f.a.body)
        self.assertLess(bot.velocity[2], 0)
        vitals = fight.vitals[bot.entity]
        vitals.damage(vitals.total - 1)
        x, y, z = bot.position
        yaw, pitch = aim(fight._eye(f.b), (x, y + 1, z))
        f.run.step({f.b: (yaw, pitch, FIRE)})
        self.assertTrue(vitals.dead)
        self.assertEqual(vitals.killer, f.b.body)
        self.assertNotIn(bot.entity, fight.pins)
        self.assertIn(bot, match.bots)

        f.press(0)
        self.assertNotIn(child.id, victim.instances)
        self.assertFalse(victim.entity_var(7539).value())
        f.press(8)
        f.press(0, 60)
        self.assertTrue(f.camo().state(13).active)
        self.assertEqual(f.health(f.a), 200)
        self.assertIn(bot, match.bots)

    def test_heavy_special_support_silence_stops_carry_before_wall_damage(self):
        for button in (2, 0x20):
            with self.subTest(button=button):
                fixture, bot = self.make("5", distance=1.5)
                match = fixture.run.match
                fight = match.combat
                match.game_map = replace(match.game_map, free_for_all=False)
                bot.team = 1 - fixture.a.team
                fixture.b.team = fixture.a.team
                fixture.b.spawned = True
                match.switch_hero(fixture.b, heroes()[sombra_fixture.SOMBRA])
                fixture.b.mover.position[:] = [3, 0, -4]
                fixture.b.mover.body = None
                fixture.press(0, 50)
                if button == 0x20:
                    fixture.b.body_script.set_ult_charge(1400)

                bot.yaw = -32768
                component = bot.body_script.component
                special = next(item for item in component.instances.values() if item.graph.index == 0x1408)
                component.edges([(35, True)])
                fixture.press(0, 62)
                self.assertEqual(fight.pins.get(bot.entity), fixture.a.body)
                self.assertLess(bot.velocity[2], 0)
                for frame in range(85):
                    x, y, z = bot.position
                    yaw, pitch = aim(fight._eye(fixture.b), (x, y + 1, z))
                    pressed = button if button == 2 or frame == 0 else 0
                    fixture.run.step({fixture.b: (yaw, pitch, pressed)})
                    if bot.entity in fight.hacks:
                        break

                self.assertIn(bot.entity, fight.hacks)
                self.assertEqual(special.vars[59].value(), 0)
                z = fixture.a.mover.position[2] - 0.9
                fixture.geometry((-4, 0, z, 4, 0, z, 4, 5, z), (-4, 0, z, 4, 5, z, -4, 5, z))
                stop = bot.position
                fixture.press(0, 20)
                self.assertNotIn(bot.entity, fight.pins)
                self.assertFalse(any(item.graph.index == 0x1407
                                     for item in fixture.a.body_script.component.instances.values()))
                self.assertGreater(special.vars[61].value(), 0)
                self.assertEqual(bot.position, stop)
                self.assertEqual(bot.velocity, (0, 0, 0))
                self.assertEqual(fixture.health(fixture.a), 200)

    def test_heavy_special_raised_ray_tracks_static_wall_in_authored_range(self):
        fixture, bot = self.make("5", distance=6)
        bot.yaw = -32768
        fixture.a.mover.position[:] = [8, 0, 0]
        component = bot.body_script.component
        special = next(instance for instance in component.instances.values()
                       if instance.graph.index == 0x1408)

        def wall(z):
            fixture.geometry((-2, 0, z, 2, 0, z, 2, 3, z), (-2, 0, z, 2, 3, z, -2, 3, z))

        wall(-5)
        component.edges([(35, True)])
        fixture.press(0, 65)
        self.assertIs(special.vars[13757].value(), False)

        wall(bot.position[2] - 1)
        fixture.press(0, 2)
        self.assertIs(special.vars[13757].value(), True)
        export_component_vm(component)
        self.assertIs(special.vars[13757].value(), True)
        wall(bot.position[2] + 1)
        fixture.press(0, 2)
        self.assertIs(special.vars[13757].value(), False)
        fixture.press(0, 300)
        self.assertIsNone(special.vars[13757].value())

    def test_heavy_special_authored_early_exit_requires_low_hit_and_raised_miss(self):
        fixture, bot = self.make("5", distance=6)
        bot.yaw = -32768
        fixture.a.mover.position[:] = [8, 0, 0]
        component = bot.body_script.component
        special = next(instance for instance in component.instances.values()
                       if instance.graph.index == 0x1408)
        cast = component.world.body_raycast
        low_hit = False

        def with_known_low_result(state):
            if state.instance is special and state.node.pos in (168, 170):
                return low_hit
            return cast(state)

        with patch.object(component.world, "body_raycast", with_known_low_result):
            component.edges([(35, True)])
            fixture.press(0, 65)
            self.assertIs(special.vars[2824].value(), False)
            self.assertIs(special.vars[13757].value(), False)
            self.assertEqual(special.vars[59].value(), 1)

            z = bot.position[2] - 1
            fixture.geometry((-2, 0, z, 2, 0, z, 2, 3, z), (-2, 0, z, 2, 3, z, -2, 3, z))
            fixture.press(0, 2)
            low_hit = True
            fixture.press(0, 2)
            self.assertIs(special.vars[2824].value(), True)
            self.assertIs(special.vars[13757].value(), True)
            self.assertEqual(special.vars[59].value(), 1)

            fixture.geometry()
            fixture.press(0, 2)
            self.assertEqual(special.vars[59].value(), 0)
            self.assertGreater(special.vars[61].value(), 0)

    def test_heavy_special_primary_contact_search_respects_static_combat_gates(self):
        for case in ("contact", "wall", "distance", "facing", "team", "target_flag"):
            with self.subTest(case=case):
                fixture, bot = self.make("5", distance=12 if case == "distance" else 1.5)
                bot.yaw = 0 if case == "facing" else -32768
                match = fixture.run.match
                if case == "wall":
                    fixture.wall()
                elif case == "team":
                    match.game_map = replace(match.game_map, free_for_all=False)
                    bot.team = fixture.a.team
                elif case == "target_flag":
                    fixture.a.body_script.component.set(6601, True)
                component = bot.body_script.component
                special = next(instance for instance in component.instances.values()
                               if instance.graph.index == 0x1408)
                component.edges([(35, True)])
                for _ in range(75):
                    fixture.press(0)
                    selected = special.vars.get(562)
                    if selected is not None and selected.value() is not None:
                        break
                expected = (Entity(fixture.a.body),) if case == "contact" else ()
                self.assertEqual(special.vars[562].value(), expected)

    def test_heavy_special_secondary_contact_damages_sombra_once(self):
        fixture, bot = self.make("5", distance=1.5)
        bot.yaw = -32768
        fixture.b.spawned = True
        fixture.b.mover.position[:] = [0, 0, 0.8]
        component = bot.body_script.component
        special = next(instance for instance in component.instances.values()
                       if instance.graph.index == 0x1408)
        component.edges([(35, True)])
        fixture.press(0, 36)
        fixture.press(2, 8)
        self.assertTrue(fixture.weapon().vars[8909].value())
        for _ in range(31):
            fixture.press(2)
            if fixture.health(fixture.a) < 200:
                break
        self.assertEqual(special.vars[562].value(), (Entity(fixture.b.body),))
        self.assertEqual(special.vars[563].value(), (Entity(fixture.a.body),))
        self.assertEqual(fixture.health(fixture.a), 150)
        self.assertGreater(fixture.a.mover.velocity[1], 0)
        self.assertLess(fixture.a.mover.velocity[2], 0)
        self.assertNotIn(fixture.b.body, fixture.run.match.combat.hacks)
        export_component_vm(component)
        fixture.press(0, 8)
        self.assertGreater(fixture.weapon().state(53).remaining(), 0)
        self.assertGreater(fixture.a.mover.position[1], 0.1)
        self.assertLess(fixture.a.mover.position[2], -0.7)
        fixture.press(0, 185)
        self.assertEqual(fixture.health(fixture.a), 150)

    def test_heavy_special_secondary_launch_respects_static_wall(self):
        fixture, bot = self.make("5", distance=1.5)
        bot.yaw = -32768
        fixture.b.spawned = True
        fixture.b.mover.position[:] = [0, 0, 0.8]
        fixture.geometry((-4, 0, -0.7, 4, 0, -0.7, 4, 5, -0.7),
                         (-4, 0, -0.7, 4, 5, -0.7, -4, 5, -0.7))
        bot.body_script.component.edges([(35, True)])
        fixture.press(0, 75)
        self.assertEqual(fixture.health(fixture.a), 150)
        self.assertLess(fixture.a.mover.position[2], -0.1)
        self.assertGreater(fixture.a.mover.position[2], -0.7)

    def test_heavy_secondary_launch_matches_missing_player_poses(self):
        for wall in (False, True):
            with self.subTest(wall=wall):
                samples = []
                for schedule in ("commands", "jump", "world_ticks"):
                    f, bot = self.make("5", distance=1.5)
                    match = f.run.match
                    bot.yaw = -32768
                    f.b.spawned = True
                    f.b.mover.position[:] = (0, 0, 0.8)
                    f.b.mover.body = None
                    f.floor = [(-40, 0, -40, -40, 0, 100, 40, 0, 100),
                               (-40, 0, -40, 40, 0, 100, 40, 0, -40)]
                    if wall:
                        f.geometry((-4, 0, -0.7, 4, 0, -0.7, 4, 5, -0.7),
                                   (-4, 0, -0.7, 4, 5, -0.7, -4, 5, -0.7))
                    else:
                        f.geometry()
                    bot.body_script.component.edges([(35, True)])
                    start = f.run.frame
                    if schedule == "commands":
                        f.press(0, 75)
                    else:
                        frames = (start + 76,) if schedule == "jump" else range(start + 1, start + 77)
                        for at in frames:
                            match.update(at * combat.TICK, at)
                    mover = f.a.mover
                    self.assertEqual(f.health(f.a), 150)
                    self.assertGreater(mover.position[1], 2.5)
                    self.assertIn(f.a.body, match.combat.impulses.launches)
                    if wall:
                        self.assertGreater(mover.position[2], -0.7)
                        self.assertEqual(mover.velocity[2], 0)
                    else:
                        self.assertLess(mover.position[2], -8)
                    samples.append((tuple(mover.position), mover.velocity))
                    at = start + 155
                    match.update(at * combat.TICK, at)
                    self.assertNotIn(f.a.body, match.combat.impulses.launches)
                    self.assertEqual(f.health(f.a), 150)
                self.assertEqual(samples, [samples[0]] * 3)

    def test_heavy_secondary_launch_rejects_late_soldier_pose_during_and_after_flight(self):
        f, bot = self.make("5", distance=1.5)
        match = f.run.match
        self.assertTrue(match.switch_hero(f.a, heroes()[SOLDIER]))
        f.a.mover.position[:] = (0, 0, 0)
        f.a.mover.body = None
        f.b.spawned = True
        f.b.mover.position[:] = (0, 0, 0.8)
        f.b.mover.body = None
        f.floor = [(-40, 0, -40, -40, 0, 100, 40, 0, 100),
                   (-40, 0, -40, 40, 0, 100, 40, 0, -40)]
        f.geometry()
        f.press(0, 50)
        bot.yaw = -32768
        bot.body_script.component.edges([(35, True)])
        start = f.run.frame
        mover = f.a.mover
        for elapsed in (76, 155):
            at = start + elapsed
            match.update(at * combat.TICK, at)
            self.assertEqual(f.health(f.a), 150)
            self.assertEqual(f.a.body in match.combat.impulses.launches, elapsed == 76)
            before = (tuple(mover.position), mover.velocity, mover.last_command)
            f.a.take_commands([Command(mover.last_command.frame - 1, forward=127)])
            self.assertEqual((tuple(mover.position), mover.velocity, mover.last_command), before)
        f.a.take_commands([Command(at, forward=127)])
        self.assertEqual(mover.last_command.frame, at)
        self.assertEqual(f.a.body_script.frame, at)

    def test_heavy_secondary_contact_respects_projected_barrier_without_launching_sombra(self):
        from test_game_sombra_grenade import SombraGrenadeTests

        for shield in (200, 10):
            with self.subTest(shield=shield):
                f, bot = self.make("5", distance=1.5)
                match = f.run.match
                fight = match.combat
                match.game_map = replace(match.game_map, free_for_all=False)
                f.a.team = f.b.team = 0
                bot.team = 1
                f.b.spawned = True
                f.b.mover.position[:] = (0, 0, 0.8)
                f.b.mover.body = None
                zarya = SombraGrenadeTests().join_ally(f, reinhardt_barrier.ZARYA, (3, 0, -3))
                f.press(16, shooter=zarya, target=f.a)
                f.press(0, 12)
                bubble, = fight.barriers.values()
                self.assertIs(bubble.protected, f.a)
                bubble.vitals.damage(bubble.vitals.total - shield)
                start = tuple(f.a.mover.position)
                bot.yaw = -32768
                bot.body_script.component.edges([(35, True)])
                impact_sample = f.run.frame + 52
                match.update(impact_sample * combat.TICK, impact_sample)
                instances = bot.body_script.component.instances.values()
                special = next(i for i in instances if i.graph.index == 0x1408)
                self.assertIn(Entity(f.a.body), special.vars[563].value())
                at = f.run.frame + 76
                match.update(at * combat.TICK, at)
                self.assertEqual(f.health(f.a), 200)
                self.assertEqual(tuple(f.a.mover.position), start)
                self.assertNotIn(f.a.body, fight.impulses.launches)
                self.assertEqual(bubble.vitals.total, max(0, shield - 50))
                self.assertEqual(bubble.entity in fight.barriers, shield > 50)

    def test_heavy_secondary_contact_does_not_outlast_cryo_or_fortify_protection(self):
        for hero, damage in ((mei.HERO, 50), (fortify.HERO, 45)):
            for protected in (False, True):
                with self.subTest(hero=f"{hero:016X}", protected=protected):
                    f, bot = self.make("5", distance=1.5)
                    match = f.run.match
                    fight = match.combat
                    self.assertTrue(match.switch_hero(f.a, heroes()[hero]))
                    f.a.mover.position[:] = (0, 0, 0)
                    f.b.spawned = True
                    f.b.mover.position[:] = (0, 0, 0.8)
                    f.a.mover.body = f.b.mover.body = None
                    f.press(0, 50)
                    before = f.health(f.a)
                    bot.yaw = -32768
                    bot.body_script.component.edges([(35, True)])
                    start = f.run.frame
                    f.press(0, 44)
                    f.press(8 if protected else 0)
                    if hero == mei.HERO:
                        self.assertEqual(mei.protected(f.a), protected)
                    else:
                        self.assertEqual(fortify.active(fight, f.a), protected)
                    at = start + 76
                    match.update(at * combat.TICK, at)
                    self.assertEqual(f.health(f.a), before if protected else before - damage)
                    self.assertEqual(f.a.body in fight.impulses.launches, not protected)
                    if protected:
                        self.assertEqual(tuple(f.a.mover.position), (0, 0, 0))
                        at = start + 360
                        match.update(at * combat.TICK, at)
                        self.assertFalse(mei.protected(f.a) or fortify.active(fight, f.a))
                        self.assertEqual(f.health(f.a), before)
                        self.assertEqual(tuple(f.a.mover.position), (0, 0, 0))
                        self.assertNotIn(f.a.body, fight.impulses.launches)

    def test_fortify_after_heavy_secondary_launch_preserves_existing_flight(self):
        samples = []
        for world_only in (False, True):
            f, bot = self.make("5", distance=1.5)
            match = f.run.match
            fight = match.combat
            self.assertTrue(match.switch_hero(f.a, heroes()[fortify.HERO]))
            f.a.mover.position[:] = (0, 0, 0)
            f.b.spawned = True
            f.b.mover.position[:] = (0, 0, 0.8)
            f.a.mover.body = f.b.mover.body = None
            f.floor = [(-40, 0, -40, -40, 0, 100, 40, 0, 100),
                       (-40, 0, -40, 40, 0, 100, 40, 0, -40)]
            f.geometry()
            f.press(0, 50)
            before = f.health(f.a)
            bot.yaw = -32768
            bot.body_script.component.edges([(35, True)])
            f.press(0, 55)
            self.assertEqual(f.health(f.a), before - 45)
            self.assertIn(f.a.body, fight.impulses.launches)
            f.press(8)
            self.assertTrue(fortify.active(fight, f.a))
            origin = tuple(f.a.mover.position)
            if world_only:
                at = f.run.frame + 21
                match.update(at * combat.TICK, at)
            else:
                f.press(0, 20)
            self.assertTrue(fortify.active(fight, f.a))
            self.assertEqual(f.health(f.a), before - 45)
            self.assertGreater(f.a.mover.position[1], origin[1])
            samples.append((tuple(f.a.mover.position), f.a.mover.velocity))
        self.assertEqual(samples[0], samples[1])

    def test_heavy_special_primary_damage_node_uses_acquired_sombra(self):
        for allied_after_contact in (False, True):
            with self.subTest(allied_after_contact=allied_after_contact):
                fixture, bot = self.make("5", distance=1.5)
                bot.yaw = -32768
                component = bot.body_script.component
                special = next(instance for instance in component.instances.values()
                               if instance.graph.index == 0x1408)
                component.edges([(35, True)])
                fixture.press(0, 75)
                self.assertEqual(special.vars[536].value(), Entity(fixture.a.body))
                self.assertEqual(fixture.health(fixture.a), 200)

                match = fixture.run.match
                if allied_after_contact:
                    match.game_map = replace(match.game_map, free_for_all=False)
                    bot.team = fixture.a.team
                # Supply the authored branch until its native transition input is hosted.
                special.follow(special.graph.node(11), "m_truePlug")
                component.run_now()
                self.assertEqual(special.vars[59].value(), 2)
                self.assertTrue(special.node_state(special.graph.node(29)).active)
                self.assertEqual(special.vars[476].value(), 300)
                vitals = match.combat.vitals[fixture.a.body]
                self.assertEqual(vitals.total, 200 if allied_after_contact else 0)
                if not allied_after_contact:
                    self.assertEqual(vitals.killer, bot.entity)

    def test_heavy_special_flagged_contact_enters_second_phase_and_deals_damage(self):
        for case in ("contact", "unflagged", "range"):
            with self.subTest(case=case):
                fixture, bot = self.make("5", distance=10 if case == "range" else 0.65)
                bot.yaw = -32768
                if case != "unflagged":
                    fixture.a.body_script.component.set(961, True)
                component = bot.body_script.component
                special = next(instance for instance in component.instances.values()
                               if instance.graph.index == 0x1408)
                component.edges([(35, True)])
                fixture.press(0, 75)
                if case == "contact":
                    self.assertEqual(special.vars[59].value(), 2)
                    self.assertEqual(special.vars[9908].value(), 175)
                    self.assertEqual(fixture.health(fixture.a), 25)
                else:
                    self.assertEqual(special.vars[59].value(), 1)
                    self.assertEqual(fixture.health(fixture.a), 200)

    def test_heavy_special_cannot_start_during_hack_or_emp(self):
        for button in (2, 0x20):
            with self.subTest(button=button):
                fixture, bot = self.make("5", distance=6)
                fight = fixture.run.match.combat
                component = bot.body_script.component
                special = next(instance for instance in component.instances.values()
                               if instance.graph.index == 0x1408)
                if button == 0x20:
                    fixture.a.body_script.set_ult_charge(1400)
                self.press(fixture, bot, button, 82)
                self.assertIn(bot.entity, fight.hacks)

                component.edges([(35, True)])
                fixture.press(0, 4)
                component.edges([(35, False)])
                self.assertIsNone(special.vars[59].value())
                self.assertEqual(special.vars[61].value(), 0)

                fixture.press(0, 425)
                self.assertNotIn(bot.entity, fight.hacks)
                self.assertIsNone(special.vars[59].value())
                component.edges([(35, True)])
                fixture.press(0)
                self.assertEqual(special.vars[59].value(), 1)

    def test_sniper_special_world_gap_preserves_landing_and_paid_emp_interruption(self):
        for emp in (False, True):
            samples = []
            for gap in (False, True):
                with self.subTest(emp=emp, gap=gap):
                    f, bot = self.make("3", distance=6)
                    match = f.run.match
                    fight = match.combat
                    f.press(0, 150)
                    self.press(f, bot, 0)
                    self.assertEqual(npc_control.start(match, f.a), bot.entity)
                    component = bot.body_script.component
                    special = next(i for i in component.instances.values() if i.graph.index == 0x142C)
                    move = special.node_state(special.graph.node(16))
                    for _ in range(170):
                        f.press(0)
                        if special.context().var(INSTANCE, 59) == 1:
                            break
                    self.assertEqual(special.context().var(INSTANCE, 59), 1)
                    origin = bot.position
                    destination = tuple(special.vars[1220].value())
                    if emp:
                        # Time the real 21-frame windup to pulse during the short authored move.
                        f.press(0, 27)
                        self.assertEqual(bot.position, origin)
                        f.a.body_script.set_ult_charge(1400)
                        f.press(32)
                        self.assertNotIn(bot.entity, fight.hacks)
                        frames = 38
                    else:
                        for _ in range(60):
                            f.press(0)
                            if move.active and any(bot.velocity):
                                break
                        self.assertTrue(move.active)
                        self.assertNotEqual(bot.position, origin)
                        frames = 24
                    start = f.run.frame
                    if gap:
                        match.update((start + frames) * combat.TICK, start + frames)
                    else:
                        f.press(0, frames)

                    self.assertFalse(move.active)
                    self.assertEqual(special.context().var(INSTANCE, 59), 2)
                    self.assertEqual(bot.velocity, (0, 0, 0))
                    self.assertEqual(f.health(f.a), 200)
                    self.assertEqual(len(fight.hacks.get(bot.entity, ())), int(emp))
                    if emp:
                        self.assertGreater(math.dist(origin, bot.position), 0)
                        self.assertLess(math.dist(origin, bot.position), math.dist(origin, destination))
                        self.assertEqual(fight.hacks[bot.entity][0].until_tick, start + 334)
                    else:
                        self.assertLess(math.dist(bot.position, destination), 0.002)
                    samples.append(bot.position)
            self.assertEqual(samples[0], samples[1])

    def test_sniper_special_invalid_target_cancels_selected_destination(self):
        for moving in (False, True):
            with self.subTest(moving=moving):
                fixture, bot = self.make("3", distance=6)
                match = fixture.run.match
                self.press(fixture, bot, 0)
                self.assertEqual(npc_control.start(match, fixture.a), bot.entity)
                component = bot.body_script.component
                special = next(item for item in component.instances.values() if item.graph.index == 0x142C)
                move = special.node_state(special.graph.node(16))
                for _ in range(175):
                    fixture.press(0)
                    if special.context().var(INSTANCE, 59) == 1 and (not moving or any(bot.velocity)):
                        break
                self.assertEqual(special.vars[59].value(), 1)
                self.assertIsInstance(special.vars[1220].value(), Vec3)
                self.assertEqual(move.active, moving)
                stop = bot.position
                if moving:
                    match.game_map = replace(match.game_map, free_for_all=False)
                    bot.team = fixture.a.team
                else:
                    fixture.a.spawned = False
                fixture.press(0, 10)
                self.assertFalse(move.active)
                self.assertEqual(special.vars[59].value(), 2 if moving else 0)
                self.assertEqual(bot.position, stop)
                self.assertEqual(bot.velocity, (0, 0, 0))
                fixture.press(0, 75)
                self.assertEqual(special.vars[59].value(), 0)
                self.assertEqual(move.counter, int(moving))
                self.assertEqual(bot.position, stop)

    def test_sniper_special_manual_engagement_can_stop_mid_move_and_restart(self):
        fixture, bot = self.make("3", distance=6)
        match = fixture.run.match
        fixture.press(0, 150)
        self.press(fixture, bot, 0)
        self.assertEqual(npc_control.start(match, fixture.a), bot.entity)
        component = bot.body_script.component
        special = next(item for item in component.instances.values() if item.graph.index == 0x142C)
        move = special.node_state(special.graph.node(16))
        for _ in range(170):
            fixture.press(0)
            if move.active and any(bot.velocity):
                break
        self.assertTrue(move.active)
        self.assertNotEqual(bot.position, (0, 0, 6))
        self.assertGreater(math.dist(bot.position, special.vars[1220].value()), 0.1)
        stop = bot.position
        health = fixture.health(fixture.a)

        self.assertEqual(npc_control.stop(match), 1)
        fixture.press(0, 170)
        self.assertFalse(move.active)
        self.assertEqual(special.vars[59].value(), 0)
        self.assertEqual(bot.position, stop)
        self.assertEqual(bot.velocity, (0, 0, 0))
        self.assertEqual(fixture.health(fixture.a), health)

        cooldown = special.node_state(special.graph.node(73))
        self.assertTrue(cooldown.active)
        for _ in range(660):
            if not cooldown.active:
                break
            fixture.press(0)
        self.assertFalse(cooldown.active)
        self.assertEqual(bot.position, stop)

        self.press(fixture, bot, 0)
        self.assertEqual(npc_control.start(match, fixture.a), bot.entity)
        for _ in range(200):
            fixture.press(0)
            if move.active and bot.position != stop:
                break
        self.assertTrue(move.active)
        self.assertNotEqual(bot.position, stop)

    def test_sniper_special_idle_search_respects_manual_enemy_and_inhibit_message(self):
        for case in ("enemy", "unbound", "ally", "inhibited", "hacked"):
            with self.subTest(case=case):
                fixture, bot = self.make("3", distance=6)
                if case != "unbound":
                    bot.attack_target = fixture.a.entity
                if case == "ally":
                    match = fixture.run.match
                    match.game_map = replace(match.game_map, free_for_all=False)
                    bot.team = fixture.a.team
                if case == "hacked":
                    self.press(fixture, bot, 2, 82)
                    self.assertIn(bot.entity, fixture.run.match.combat.hacks)
                component = bot.body_script.component
                special = next(item for item in component.instances.values() if item.graph.index == 0x142C)
                if case == "inhibited":
                    fixture.press(0, 70)
                    self.assertEqual(send_instance_message(special, 0x0240000000000A45, {}, bot.entity), 1)
                for _ in range(145):
                    fixture.press(0)
                    if case == "enemy" and special.context().var(INSTANCE, 1220) is not None:
                        break
                if case == "enemy":
                    direction = special.vars[2083].value()
                    destination = special.vars[1220].value()
                    self.assertGreater(direction.y, 0)
                    horizontal = math.hypot(direction.x, direction.z)
                    self.assertAlmostEqual(destination.x, 2.5 * direction.x / horizontal, places=5)
                    self.assertAlmostEqual(destination.z - 6, 2.5 * direction.z / horizontal, places=5)
                    self.assertNotAlmostEqual(destination.x, 0, places=2)
                    self.assertEqual(special.vars[59].value(), 1)
                    fixture.press(0, 80)
                    self.assertLess(math.dist(bot.position, destination), 0.01)
                else:
                    self.assertIsNone(special.context().var(INSTANCE, 1220))
                    self.assertEqual(special.vars[59].value(), 0)
                    self.assertEqual(bot.position, (0, 0, 6))

    def test_sniper_special_blocked_search_retries_and_revalidates_engagement(self):
        for case in ("retry", "lost", "ally", "hack", "emp"):
            with self.subTest(case=case):
                fixture, bot = self.make("3", distance=6)
                bot.attack_target = fixture.a.entity
                fixture.geometry(
                    (-1, 0, 7, 1, 4, 7, 1, 0, 7), (-1, 0, 7, -1, 4, 7, 1, 4, 7),
                    (-1, 0, 5, -0.18, 0, 5, -0.18, 4, 5), (-1, 0, 5, -0.18, 4, 5, -1, 4, 5),
                    (0.18, 0, 5, 1, 0, 5, 1, 4, 5), (0.18, 0, 5, 1, 4, 5, 0.18, 4, 5),
                    (-1, 0, 5, -1, 4, 5, -1, 4, 7), (-1, 0, 5, -1, 4, 7, -1, 0, 7),
                    (1, 0, 5, 1, 4, 7, 1, 4, 5), (1, 0, 5, 1, 0, 7, 1, 4, 7),
                )
                component = bot.body_script.component
                special = next(item for item in component.instances.values() if item.graph.index == 0x142C)
                search = special.node_state(special.graph.node(39))
                for _ in range(120):
                    fixture.press(0)
                    if search.active:
                        break
                self.assertTrue(search.active)
                first_direction = special.vars[2083].value()
                self.assertIsNone(special.context().var(INSTANCE, 1220))
                export_component_vm(component)

                if case in ("hack", "emp"):
                    button = 0x20 if case == "emp" else 2
                    if case == "emp":
                        fixture.a.body_script.set_ult_charge(1400)
                    self.press(fixture, bot, button, 82)
                    self.assertIn(bot.entity, fixture.run.match.combat.hacks)
                    self.assertFalse(search.active)
                    self.assertFalse(special.node_state(special.graph.node(41)).active)
                    self.assertIsNone(special.context().var(INSTANCE, 1220))
                    self.assertEqual(bot.position, (0, 0, 6))
                else:
                    if case == "lost":
                        fixture.a.spawned = False
                    elif case == "ally":
                        match = fixture.run.match
                        match.game_map = replace(match.game_map, free_for_all=False)
                        bot.team = fixture.a.team
                    fixture.geometry()
                    fixture.press(0, 20)
                    if case != "retry":
                        self.assertFalse(search.active)
                        self.assertFalse(special.node_state(special.graph.node(41)).active)
                        self.assertIsNone(special.context().var(INSTANCE, 1220))
                        self.assertEqual(bot.position, (0, 0, 6))
                        if case == "lost":
                            fixture.a.spawned = True
                        else:
                            match.game_map = replace(match.game_map, free_for_all=True)
                        fixture.press(0, 5)
                    self.assertNotEqual(special.vars[2083].value(), first_direction)
                    self.assertFalse(search.active)
                    destination = special.vars[1220].value()
                    fixture.press(0, 75)
                    self.assertLess(math.dist(bot.position, destination), 0.01)

    def test_sniper_special_search_publishes_reachable_static_destination(self):
        for case in ("clear", "wall", "gap", "hacked"):
            with self.subTest(case=case):
                fixture, bot = self.make("3", distance=6)
                fixture.press(0, 70)
                if case == "wall":
                    fixture.geometry((-30, 0, 7, 30, 40, 7, 30, 0, 7),
                                     (-30, 0, 7, -30, 40, 7, 30, 40, 7))
                elif case == "gap":
                    fixture.geometry((-30, 0, 7, -0.18, 40, 7, -0.18, 0, 7),
                                     (-30, 0, 7, -30, 40, 7, -0.18, 40, 7),
                                     (0.18, 0, 7, 30, 40, 7, 30, 0, 7),
                                     (0.18, 0, 7, 0.18, 40, 7, 30, 40, 7))
                elif case == "hacked":
                    self.press(fixture, bot, 2, 82)
                    self.assertIn(bot.entity, fixture.run.match.combat.hacks)
                component = bot.body_script.component
                special = next(item for item in component.instances.values() if item.graph.index == 0x142C)
                special.node_state(special.graph.node(39)).request(BEGIN)
                component.run_now()
                if case == "clear":
                    self.assertEqual(special.vars[1220].value(), Vec3(0, 0, 8.5))
                    self.assertEqual(special.vars[59].value(), 1)
                    fixture.press(0, 72)
                    self.assertAlmostEqual(bot.position[2], 8.5, places=2)
                else:
                    self.assertIsNone(special.context().var(INSTANCE, 1220))
                    self.assertEqual(special.vars[59].value(), 0)
                    fixture.press(0, 72)
                    self.assertEqual(bot.position, (0, 0, 6))

    def test_sniper_special_moves_to_resolved_destination_or_stops_at_wall(self):
        for blocked in (False, True):
            with self.subTest(blocked=blocked):
                fixture, bot = self.make("3", distance=6)
                fixture.press(0, 70)
                component = bot.body_script.component
                special = next(item for item in component.instances.values() if item.graph.index == 0x142C)
                if blocked:
                    fixture.geometry((-4, 0, 8, 4, 5, 8, 4, 0, 8),
                                     (-4, 0, 8, -4, 5, 8, 4, 5, 8))
                destination = Vec3(0, 0, 10)
                special.write((INSTANCE, 1220), destination)
                special.write((INSTANCE, 431), destination)
                special.follow(special.graph.node(39), "m_onFinishedPlug")
                fixture.press(0, 35)
                self.assertEqual(special.vars[59].value(), 1)
                self.assertEqual(bot.position, (0, 0, 6))
                fixture.press(0, 38)
                self.assertFalse(special.node_state(special.graph.node(16)).active)
                self.assertGreater(bot.position[2], 7)
                if blocked:
                    self.assertLess(bot.position[2], 8)
                else:
                    self.assertAlmostEqual(bot.position[2], 10, places=2)
                self.assertEqual(bot.velocity, (0, 0, 0))
                fixture.press(0, 70)
                self.assertEqual(special.vars[59].value(), 0)

    def test_sniper_special_rejects_resolved_move_during_hack_and_emp(self):
        for button in (2, 0x20):
            with self.subTest(button=button):
                fixture, bot = self.make("3", distance=6)
                fight = fixture.run.match.combat
                if button == 0x20:
                    fixture.a.body_script.set_ult_charge(1400)
                self.press(fixture, bot, button, 82)
                self.assertIn(bot.entity, fight.hacks)
                component = bot.body_script.component
                special = next(item for item in component.instances.values() if item.graph.index == 0x142C)
                special.write((INSTANCE, 1220), Vec3(0, 0, 10))
                special.follow(special.graph.node(39), "m_onFinishedPlug")
                self.assertEqual(special.vars[59].value(), 1)
                fixture.press(0, 65)
                self.assertEqual(special.vars[59].value(), 0)
                self.assertFalse(special.node_state(special.graph.node(16)).active)
                self.assertEqual(bot.position, (0, 0, 6))

    def test_sniper_special_hack_and_emp_stop_active_movement(self):
        for button in (2, 0x20):
            with self.subTest(button=button):
                fixture, bot = self.make("3", distance=6)
                fixture.a.mover.position[:] = [3, 0, 14]
                fixture.a.mover.body = None
                fixture.press(0, 70)
                if button == 0x20:
                    fixture.a.body_script.set_ult_charge(1400)
                fight = fixture.run.match.combat
                component = bot.body_script.component
                special = next(item for item in component.instances.values() if item.graph.index == 0x142C)
                destination = Vec3(0, 0, 30)
                special.write((INSTANCE, 1220), destination)
                special.write((INSTANCE, 431), destination)
                special.follow(special.graph.node(39), "m_onFinishedPlug")
                fixture.press(0, 54)
                self.assertGreater(bot.position[2], 6)
                self.assertTrue(special.node_state(special.graph.node(16)).active)
                for frame in range(65):
                    self.press(fixture, bot, button if button == 2 or frame == 0 else 0)
                    if bot.entity in fight.hacks:
                        break
                self.assertIn(bot.entity, fight.hacks)
                stop = bot.position
                self.assertLess(stop[2], 30)
                fixture.press(0, 10)
                self.assertEqual(bot.position, stop)
                self.assertEqual(bot.velocity, (0, 0, 0))
                self.assertEqual(special.vars[59].value(), 2)
                self.assertFalse(special.node_state(special.graph.node(16)).active)
                fixture.press(0, 60)
                self.assertEqual(special.vars[59].value(), 0)
                self.assertEqual(bot.position, stop)

    def test_ordinary_npc_weapons_can_fire_during_hack_and_emp(self):
        for key in ("1", "2", "3", "5"):
            for button in (2, 0x20):
                with self.subTest(npc=self.catalog[key].name, button=button):
                    fixture, bot = self.make(key, distance=3)
                    match = fixture.run.match
                    if key == "2" and button == 2:
                        bot.body_script.component.set(14679, False)
                    if button == 0x20:
                        fixture.a.body_script.set_ult_charge(1400)
                    for _ in range(100):
                        self.press(fixture, bot, button)
                        if bot.entity in match.combat.hacks:
                            break
                    effect, = match.combat.hacks[bot.entity]
                    npc_control.start(match, fixture.a)
                    before = fixture.health(fixture.a)
                    while fixture.run.frame + 1 < effect.until_tick and fixture.health(fixture.a) == before:
                        fixture.press(0)
                    self.assertLess(fixture.health(fixture.a), before)
                    self.assertIn(bot.entity, match.combat.hacks)
                    self.assertTrue(bot.body_script.component.entity_var(888).value())
                    npc_control.stop(match)

    def test_support_hack_and_emp_leave_ongoing_npc_primary_fire_available(self):
        for key in ("1", "2", "3", "5"):
            for button in (2, 0x20):
                with self.subTest(npc=self.catalog[key].name, button=button):
                    fixture, bot = self.make(key, distance=3)
                    match = fixture.run.match
                    supporter = fixture.b
                    supporter.spawned = True
                    self.assertTrue(match.switch_hero(supporter, fixture.a.hero))
                    supporter.mover.position[:] = (3, 0, 3)
                    supporter.mover.body = None
                    self.press(fixture, bot, 0, 50)
                    if key == "2" and button == 2:
                        bot.body_script.component.set(14679, False)
                    npc_control.start(match, fixture.a)
                    component = bot.body_script.component
                    logical = npc_control.button(bot)
                    # Sniper can reposition before settling into primary fire.
                    for _ in range(380 if key == "3" else 180):
                        fixture.press(0)
                        if fixture.health(fixture.a) < 200:
                            break
                    self.assertLess(fixture.health(fixture.a), 200)
                    self.assertIn(logical, component.held)
                    if button == 0x20:
                        supporter.body_script.set_ult_charge(1400)
                    x, y, z = bot.position
                    angles = aim(match.combat._eye(supporter), (x, y + 1, z))
                    for _ in range(100):
                        fixture.run.step({supporter: (*angles, button)})
                        self.assertIn(logical, component.held)
                        if bot.entity in match.combat.hacks:
                            break
                    effect, = match.combat.hacks[bot.entity]
                    before = fixture.health(fixture.a)
                    self.assertGreater(before, 0)
                    while fixture.run.frame + 1 < effect.until_tick and fixture.health(fixture.a) == before:
                        fixture.press(0)
                    self.assertLess(fixture.health(fixture.a), before)
                    self.assertIn(bot.entity, match.combat.hacks)

    def test_enforcer_explicit_weapon_profiles_change_actual_pool_loss(self):
        for profile, damage in ((0, 2), (1, 4)):
            with self.subTest(profile=profile):
                fixture, bot = self.make("2", distance=3)
                bot.body_script.component.set(9948, profile)
                npc_control.start(fixture.run.match, fixture.a)
                for _ in range(250):
                    fixture.press(0)
                    if fixture.health(fixture.a) < 200:
                        break
                self.assertEqual(fixture.health(fixture.a), 200 - damage)

    def test_four_actual_weapon_npcs_stop_releases_input_and_prevents_further_damage(self):
        for key in ("1", "2", "3", "5"):
            with self.subTest(npc=self.catalog[key].name):
                fixture, bot = self.make(key, distance=3)
                match = fixture.run.match
                npc_control.start(match, fixture.a)
                fixture.press(0, 10)
                npc_control.stop(match)
                before = fixture.health(fixture.a)
                fixture.press(0, 180)
                self.assertEqual(fixture.health(fixture.a), before)
                self.assertFalse(bot.firing)
                self.assertNotIn(20, bot.body_script.component.held)
                self.assertFalse(any(r.shooter is bot for r in match.combat.rockets))

    def test_actual_npc_static_wall_blocks_pistol_and_hack_for_all_five(self):
        for key, definition in self.catalog.items():
            with self.subTest(npc=definition.name):
                fixture, bot = self.make(key)
                bot.body_script.component.set(14679, False)  # isolate LOS from authored rejection flags
                fixture.wall()
                before = fixture.run.match.combat.vitals[bot.entity].total
                self.press(fixture, bot, FIRE, 30)
                self.press(fixture, bot, 0, 3)
                self.press(fixture, bot, 2, 100)
                self.assertEqual(fixture.run.match.combat.vitals[bot.entity].total, before)
                self.assertFalse(bot.body_script.component.entity_var(888).value())
                self.assertEqual(fixture.weapon().state(53).remaining(), 0)

    def test_emp_pulse_applies_to_five_simultaneous_actual_npcs_and_drains_only_shields(self):
        fixture, first = self.make("1", distance=3)
        match = fixture.run.match
        actors = [first]
        for key in ("2", "3", "4", "5"):
            actors.append(npc.spawn(match, self.catalog[key], (int(key) - 3, 0, 3), team=1))
        fixture.press(0, 4)
        before = {bot.entity: dict(match.combat.vitals[bot.entity].now) for bot in actors}
        fixture.a.body_script.set_ult_charge(1400)
        fixture.press(0x20, 30)
        for bot in actors:
            with self.subTest(npc=bot.name):
                self.assertIsNotNone(bot.body_script)
                component = bot.body_script.component
                self.assertIn(bot.entity, match.combat.hacks)
                self.assertEqual(component.entity_var(6396).value().id, fixture.a.body)
                vitals = match.combat.vitals[bot.entity]
                self.assertEqual(vitals.now[SHIELDS_POOL], 0)
                self.assertEqual(vitals.now[HEALTH_POOL], before[bot.entity][HEALTH_POOL])
                self.assertEqual(vitals.now[ARMOUR_POOL], before[bot.entity][ARMOUR_POOL])
                self.assertEqual(component.entity_var(OWNER_HEALTH).value(), vitals.total)
                self.assertEqual(sum(i.graph.index == 0x0B32 for i in component.instances.values()), 1)
        fixture.press(0, 400)
        for bot in actors:
            self.assertNotIn(bot.entity, match.combat.hacks)
            self.assertFalse(bot.body_script.component.entity_var(888).value())
            self.assertFalse(bot.body_script.component.entity_var(6396).value())
            self.assertFalse(any(i.graph.index in (0x0B32, 0x0B1F)
                                 for i in bot.body_script.component.instances.values()))

    def test_paid_emp_pulses_and_expires_during_world_only_updates_for_all_five(self):
        fixture, first = self.make("1", distance=3)
        match = fixture.run.match
        fight = match.combat
        actors = [first]
        for key in ("2", "3", "4", "5"):
            actors.append(npc.spawn(match, self.catalog[key], (int(key) - 3, 0, 3), team=1))
        fixture.press(0, 4)
        before = {bot.entity: dict(fight.vitals[bot.entity].now) for bot in actors}
        owner = fixture.a.body_script
        owner.set_ult_charge(1400)
        fixture.press(0x20)
        cast = fixture.run.frame
        self.assertEqual(owner.component.entity_var(342).value(), 0)
        at = cast + 90
        match.update(at * combat.TICK, at)
        for bot in actors:
            with self.subTest(npc=bot.name):
                component = bot.body_script.component
                effect, = fight.hacks[bot.entity]
                self.assertEqual(effect.until_tick, cast + 334)
                self.assertEqual(component.entity_var(6396).value(), Entity(fixture.a.body))
                pools = fight.vitals[bot.entity].now
                self.assertEqual(pools[SHIELDS_POOL], 0)
                self.assertEqual(pools[HEALTH_POOL], before[bot.entity][HEALTH_POOL])
                self.assertEqual(pools[ARMOUR_POOL], before[bot.entity][ARMOUR_POOL])
        at = cast + 333
        match.update(at * combat.TICK, at)
        self.assertTrue(all(bot.entity in fight.hacks for bot in actors))
        at = cast + 334
        match.update(at * combat.TICK, at)
        for bot in actors:
            with self.subTest(npc=bot.name):
                self.assertNotIn(bot.entity, fight.hacks)
                self.assertFalse(bot.body_script.component.entity_var(888).value())
                self.assertFalse(bot.body_script.component.entity_var(6396).value())

    def test_emp_source_retirement_releases_all_five_npcs_during_world_only_updates(self):
        from test_game_combat import SOLDIER

        for gap in (False, True):
            with self.subTest(gap=gap):
                f, first = self.make("1", distance=3)
                match = f.run.match
                fight = match.combat
                actors = [first]
                for key in ("2", "3", "4", "5"):
                    actors.append(npc.spawn(match, self.catalog[key], (int(key) - 3, 0, 3), team=1))
                f.press(0, 4)
                f.a.body_script.set_ult_charge(1400)
                f.press(32)
                cast = f.run.frame
                f.press(0, 50)
                effects = [fight.hacks[bot.entity][0] for bot in actors]
                self.assertTrue(all(effect.until_tick == cast + 334 for effect in effects))
                self.assertTrue(match.switch_hero(f.a, heroes()[SOLDIER]))
                for offset in (239, 240):
                    at = cast + offset
                    if gap:
                        match.update(at * combat.TICK, at)
                    else:
                        f.press(0, at - f.run.frame)
                    for bot, effect in zip(actors, effects, strict=True):
                        self.assertTrue(effect.application.stopped)
                        self.assertEqual(bot.entity in fight.hacks, offset == 239)
                        if offset == 240:
                            component = bot.body_script.component
                            self.assertFalse(component.entity_var(888).value())
                            self.assertFalse(component.entity_var(6396).value())

    def test_actual_npcs_killed_by_pistol_keep_corpses_past_respawn_for_all_five(self):
        for key, definition in self.catalog.items():
            with self.subTest(npc=definition.name):
                fixture, bot = self.make(key)
                fight = fixture.run.match.combat
                for _ in range(4000):
                    if bot.dead:
                        break
                    if fixture.weapon().vars[53].value() <= 0:
                        self.press(fixture, bot, sombra_fixture.RELOAD)
                        self.press(fixture, bot, 0, 100)
                    else:
                        self.press(fixture, bot, FIRE)
                self.assertTrue(bot.dead)
                self.assertEqual(fight.vitals[bot.entity].total, 0)
                self.assertTrue(bot.retain_corpse)
                self.assertTrue(bot.body_script.component.owner_dead)
                self.assertTrue(fixture.a.client.npc_deaths)
                self.assertFalse(any(who is bot for _, who in fight.respawns))
                self.press(fixture, bot, 0, round(combat.BOT_RESPAWN / combat.TICK) + 10)
                self.assertIn(bot, fixture.run.match.bots)
                self.assertEqual(fight.vitals[bot.entity].total, 0)
                self.assertFalse(any(t.entity == bot.entity for t in fight._enemies(fixture.a)))


if __name__ == "__main__":
    unittest.main()
