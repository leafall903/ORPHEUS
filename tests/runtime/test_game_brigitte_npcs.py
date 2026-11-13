"""Five authored NPC targets and four manual primary attacks, not autonomous AI."""

import gzip
import json
import math
import os
import unittest
from pathlib import Path
from unittest.mock import patch

import test_game_brigitte as brigitte_tests
import test_game_combat as combat_tests

from ow174.game import defenses, npc, npc_control, world
from ow174.game.content import find_hero, heroes
from ow174.game.script import graph, runtime
from ow174.paths import DATA_DIR

EXTERNAL = Path(os.environ.get("ORPHEUS_PVE_NPC_DATA", str(DATA_DIR)))


class NpcClient(combat_tests.FakeClient):
    """Immediate fixture transport, recording real BotScript terminal events."""

    def __init__(self, player):
        super().__init__(player)
        self.death_events = []

    def queue_bot_death(self, entity, event):
        self.death_events.append((entity, event))

    def queue_bot_death_update(self, entity, event):
        self.death_events.append((entity, event))

    def bot_stream(self, entity):
        return None  # fixture has no acknowledged native observer stream


class ActualNpcTests(unittest.TestCase):
    def test_actual_quick_melee_respects_all_five_npc_contacts_and_exclusions(self):
        from test_game_mover import box

        from ow174.game.collision import StaticCollisionProvider, World

        for definition in self.catalog.values():
            for contact in ("hit", "friendly", "range", "wall"):
                with self.subTest(npc=definition.name, contact=contact):
                    run, brig, bot, _ = self.fixture(definition, allied=contact == "friendly")
                    fight = run.match.combat
                    x, y, z = brig.mover.position
                    if contact == "range":
                        bot.position = (x, y, z + 7)
                    elif contact == "wall":
                        collision = World(box(x - 20, x + 20, y - 2, y, z - 20, z + 20)
                                          + box(x - 4, x + 4, y, y + 4, z + 1.5, z + 1.7))
                        run.match.collision_provider = StaticCollisionProvider(collision)
                        run.match.collision_world = collision
                        brig.mover.world, brig.mover.body = collision, None
                    target = next(t for t in fight._targets() if t.owner is bot)
                    vitals = fight.vitals_of(target)
                    before = vitals.total
                    run.step({brig: (0, 0, 0x800)})
                    for _ in range(39):
                        run.step({})
                    self.assertEqual(vitals.total, before - (35 if contact == "hit" else 0))
                    self.assertEqual(bool(fight.brigitte.inspire), contact == "hit")

    def test_actual_projected_barrier_protects_all_five_npc_bodies_from_brigitte(self):
        from test_game_brigitte_defenses import DefensiveControlTests
        from test_game_brigitte_shield import BarrierTests

        attacks = (("flail", 35), ("quick_melee", 35), ("whip", 70), ("bash", 5))
        for definition in self.catalog.values():
            for attack, damage in attacks:
                with self.subTest(npc=definition.name, attack=attack):
                    run, brig, bot, target = self.fixture(definition, names=("Brigitte", "Zarya"))
                    zarya = run.players[1]
                    self.assertTrue(run.match.switch_hero(zarya, find_hero("zarya")))
                    zarya.team = bot.team
                    x, y, z = bot.position
                    zarya.mover.position, zarya.mover.body = [x + 8, y, z], None
                    for _ in range(25):
                        run.step({})
                    fight = run.match.combat
                    vitals = fight.vitals_of(target)
                    before = vitals.total
                    direction = combat_tests.aim(fight._eye(zarya), (x, y + target.height / 2, z))
                    run.step({zarya: (*direction, 16)})
                    barrier = fight.projected_barriers[bot.entity]
                    self.assertEqual((barrier.source, barrier.health), (zarya.body, 200))
                    if attack == "bash":
                        BarrierTests().raise_shield(run, brig, 22)
                    DefensiveControlTests().attack(run, brig, attack)
                    self.assertEqual((vitals.total, barrier.health), (before, 200 - damage))
                    self.assertEqual(bot.position, (x, y, z))
                    self.assertFalse(fight.is_stunned(bot))
                    self.assertFalse(fight.brigitte.inspire)
                    energy = zarya.body_script.component.entity_var(87, create=False)
                    self.assertAlmostEqual(energy.value(), damage * 0.2)
                    for _ in range(125):
                        run.step({})
                    self.assertNotIn(bot.entity, fight.projected_barriers)
                    recovery_attack = "quick_melee" if attack == "quick_melee" else "flail"
                    DefensiveControlTests().attack(run, brig, recovery_attack)
                    self.assertEqual(vitals.total, before - 35)

    def test_all_five_bodies_reject_flail_whip_and_bash_at_range_wall_or_friendly(self):
        from test_game_mover import box

        from ow174.game.collision import StaticCollisionProvider, World

        for definition in self.catalog.values():
            for boundary in ("range", "wall", "friendly"):
                with self.subTest(npc=definition.name, boundary=boundary):
                    run, player, bot, target = self.fixture(definition, allied=boundary == "friendly")
                    fight = run.match.combat
                    x, y, z = player.mover.position
                    if boundary == "range":
                        bot.position = (x, y, z + 30)
                    elif boundary == "wall":
                        wall = World(box(x - 20, x + 20, y - 2, y, z - 20, z + 20)
                                     + box(x - 4, x + 4, y, y + 4, z + 1.5, z + 1.7))
                        run.match.collision_provider = StaticCollisionProvider(wall)
                        run.match.collision_world = wall
                        player.mover.world, player.mover.body = wall, None
                    before = fight.vitals_of(target).total
                    brigitte_tests.BrigitteTests().swing(run, player, 40)
                    flail = next(i for i in player.body_script.component.instances.values()
                                 if i.graph.index == 0x1248)
                    self.assertGreater(sum(s.counter for k, s in flail.states.items() if k in (7, 8)), 0)
                    for _ in range(45):
                        run.step({})
                    brigitte_tests.BrigitteTests().whip(run, player)
                    whip = next(i for i in player.body_script.component.instances.values()
                                if i.graph.index == 0x1259)
                    self.assertGreater(whip.states[6].counter, 0)
                    for _ in range(20):
                        run.step({player: (0, 0, 2)})
                    run.step({player: (0, 0, 3)})
                    for _ in range(35):
                        run.step({player: (0, 0, 2)})
                    barrier = fight.brigitte.barriers[player.body]
                    self.assertGreater(barrier.instance.states[14].counter, 0)
                    self.assertEqual(fight.vitals_of(target).total, before)
                    self.assertFalse(fight.is_stunned(bot))

    def test_flail_range_edge_uses_each_actual_body_capsule_radius_and_height(self):
        for definition in self.catalog.values():
            for offset in (-0.05, 0.05):
                with self.subTest(npc=definition.name, offset=offset):
                    run, player, bot, target = self.fixture(definition)
                    fight = run.match.combat
                    flail = next(i for i in player.body_script.component.instances.values()
                                 if i.graph.index == 0x1248)
                    reach = flail.vars[1648].value()
                    x, y, z = fight._eye(player)
                    a, b = target.segment()
                    dy = y - min(b[1], max(a[1], y))
                    distance = math.sqrt((reach + target.radius + offset) ** 2 - dy ** 2)
                    bot.position = (x, target.feet[1], z + distance)
                    before = fight.vitals_of(target).total
                    brigitte_tests.BrigitteTests().swing(run, player, 20)
                    expected = before - 35 if offset < 0 else before
                    self.assertEqual(fight.vitals_of(target).total, expected)

    def engage(self, run, player, bot, *, buttons=0):
        target = next(t for t in run.match.combat._targets() if t.owner is bot)
        a, b = target.segment()
        centre = tuple((x + y) / 2 for x, y in zip(a, b, strict=True))
        yaw, pitch = combat_tests.aim(run.match.combat._eye(player), centre)
        run.step({player: (yaw, pitch, buttons)})
        return npc_control.start(run.match, player)

    def test_actual_assassin_contact_drops_barrier_controls_and_stop_releases(self):
        definition = next(d for d in self.catalog.values() if d.name == "Assassin")
        run, player, bot, _target = self.fixture(definition)
        for _ in range(20):
            run.step({player: (0, 0, 2)})
        fight = run.match.combat
        barrier = fight.brigitte.barriers[player.body]
        self.assertTrue(fight.brigitte._barrier_active(player, barrier))
        self.engage(run, player, bot, buttons=2)
        run.step({player: (0, 0, 2)})
        self.assertEqual(fight.pins, {bot.entity: player.body})
        self.assertTrue(fight.is_stunned(player))
        self.assertFalse(fight.brigitte._barrier_active(player, barrier))
        owner = next(t for t in fight._targets() if t.owner is player)
        self.assertEqual(fight.vitals_of(owner).total, 180)
        npc_control.stop(run.match)
        self.assertFalse(fight.is_stunned(player))
        before = fight.vitals_of(owner).total
        for _ in range(100):
            run.step({})
        self.assertEqual(fight.vitals_of(owner).total, before)
        self.assertFalse(fight.pins)

    def test_assassin_cannot_pin_wraith_fade_or_cryo_until_the_phase_ends(self):
        definition = next(d for d in self.catalog.values() if d.name == "Assassin")
        for hero in ("Reaper", "Moira", "Mei"):
            with self.subTest(hero=hero):
                run, player, bot, _ = self.fixture(definition)
                position = list(player.mover.position)
                self.assertTrue(run.match.switch_hero(player, find_hero(hero)))
                player.mover.position, player.mover.body = position, None
                for _ in range(25):
                    run.step({})
                fight = run.match.combat
                hp = fight.vitals_of(brigitte_tests.BrigitteTests().target(run, player))
                before = hp.total
                run.step({player: (0, 0, 8)})
                self.assertTrue(fight._invulnerable(player))
                self.assertEqual(self.engage(run, player, bot), bot.entity)
                for _ in range(5):
                    run.step({})
                self.assertTrue(fight._invulnerable(player))
                self.assertFalse(fight.is_stunned(player))
                self.assertFalse(fight.pins)
                self.assertEqual(hp.total, before)
                npc_control.stop(run.match)
                for _ in range(260):
                    if not fight._invulnerable(player):
                        break
                    run.step({})
                self.assertFalse(fight._invulnerable(player))
                self.assertEqual(self.engage(run, player, bot), bot.entity)
                run.step({})
                self.assertEqual(fight.pins, {bot.entity: player.body})
                self.assertEqual(hp.total, before - 25)

    def test_assassin_contact_range_includes_recorded_player_eye_height(self):
        definition = next(d for d in self.catalog.values() if d.name == "Assassin")
        for distance, contact in ((2.99, True), (3.0, False)):
            with self.subTest(horizontal_distance=distance):
                run, player, bot, _target = self.fixture(definition)
                x, y, z = player.mover.position
                bot.position = (x, y, z + distance)
                self.engage(run, player, bot)
                run.step({})
                fight = run.match.combat
                self.assertEqual(fight.is_stunned(player), contact)
                self.assertEqual(bot.entity in fight.pins, contact)
                target = next(t for t in fight._targets() if t.owner is player)
                self.assertEqual(fight.vitals_of(target).total, 180 if contact else 200)

    def test_actual_allied_bash_frees_assassin_pin_victim_and_pauses_source(self):
        definition = next(d for d in self.catalog.values() if d.name == "Assassin")
        run, victim, bot, _target = self.fixture(definition, names=("Brigitte", "Rescuer"))
        ally = run.players[1]
        run.match.switch_hero(ally, heroes()[victim.hero.guid])
        ally.team = victim.team
        x, y, z = victim.mover.position
        ally.mover.position, ally.mover.body = [x + 0.5, y, z], None
        for _ in range(20):
            run.step({victim: (0, 0, 0), ally: (0, 0, 2)})
        self.engage(run, victim, bot)
        run.step({ally: (0, 0, 2)})
        self.assertTrue(run.match.combat.is_stunned(victim))
        run.step({ally: (0, 0, 3)})
        run.step({ally: (0, 0, 2)})
        self.assertTrue(run.match.combat.is_stunned(bot))
        self.assertFalse(run.match.combat.is_stunned(victim))
        self.assertFalse(run.match.combat.pins)
        fight = run.match.combat
        owner = next(t for t in fight._targets() if t.owner is victim)
        before = fight.vitals_of(owner).total
        for _ in range(30):
            run.step({ally: (0, 0, 2)})
        self.assertEqual(fight.vitals_of(owner).total, before)
        for _ in range(80):
            run.step({ally: (0, 0, 2)})
        self.assertFalse(fight.is_stunned(bot))
        self.assertFalse(fight.is_stunned(victim))
        self.assertEqual(fight.vitals_of(owner).total, before)
        x, y, z = bot.position
        victim.mover.position, victim.mover.body = [x, y, z - 2.9], None
        for _ in range(80):
            run.step({ally: (0, 0, 2)})
            if fight.is_stunned(victim):
                break
        self.assertTrue(fight.is_stunned(victim))
        self.assertLess(fight.vitals_of(owner).total, before)

    def test_actual_assassin_burst_then_authored_wait_precedes_continuous_damage(self):
        definition = next(d for d in self.catalog.values() if d.name == "Assassin")
        run, player, bot, _target = self.fixture(definition)
        run.step({player: (0, 0, 0)})
        self.engage(run, player, bot)
        fight = run.match.combat
        health = fight.vitals_of(next(t for t in fight._targets() if t.owner is player))
        run.step({})
        self.assertEqual(health.total, 180)
        for _ in range(60):
            run.step({})
        self.assertEqual(health.total, 180)
        instance = next(i for i in bot.body_script.component.instances.values() if i.graph.index == 0x1157)
        self.assertEqual(instance.vars[11984].value(), 100)
        for _ in range(10):
            run.step({})
        self.assertLess(health.total, 180)
        self.assertTrue(instance.states[instance.graph.node(15).state].active)
        self.assertTrue(fight.is_stunned(player))

    def test_rally_keeps_issuing_armor_during_actual_assassin_pin(self):
        definition = next(d for d in self.catalog.values() if d.name == "Assassin")
        run, player, bot, _target = self.fixture(definition)
        player.body_script.ult_seconds = 0
        player.body_script.set_ult_charge(player.body_script.component.entity_var(338).value())
        run.step({player: (0, 0, 32)})
        for _ in range(45):
            run.step({})
        fight = run.match.combat
        self.engage(run, player, bot)
        run.step({})
        self.assertTrue(fight.is_stunned(player))
        owner = next(t for t in fight._targets() if t.owner is player)
        vitals = fight.vitals_of(owner)
        before = vitals.temporary_armour
        for _ in range(35):
            run.step({})
        self.assertTrue(fight.is_stunned(player))
        self.assertTrue(fight.brigitte.rally_active(player.body_script.component))
        self.assertGreater(vitals.temporary_armour, before)

    def test_actual_assassin_source_death_or_removal_releases_control_immediately(self):
        definition = next(d for d in self.catalog.values() if d.name == "Assassin")
        for removed in (False, True):
            with self.subTest(removed=removed):
                run, player, bot, target = self.fixture(definition)
                run.step({player: (0, 0, 0)})
                self.engage(run, player, bot)
                run.step({})
                fight = run.match.combat
                self.assertTrue(fight.is_stunned(player))
                if removed:
                    npc.remove_spawned(run.match)
                else:
                    fight._damage(player, target, 1000, False, None, run.frame)
                    self.assertTrue(bot.dead)
                    self.assertIn(bot, run.match.bots)
                self.assertFalse(fight.is_stunned(player))
                self.assertFalse(fight.pins)
                owner = next(t for t in fight._targets() if t.owner is player)
                before = fight.vitals_of(owner).total
                for _ in range(100):
                    run.step({})
                self.assertEqual(fight.vitals_of(owner).total, before)

    @classmethod
    def setUpClass(cls):
        if not (EXTERNAL / "pve_npcs_174.json").is_file():
            raise unittest.SkipTest("actual NPC acceptance needs parent's external catalog")
        cls.catalog = npc.load_catalog(EXTERNAL / "pve_npcs_174.json")
        cls.bundle = json.loads(gzip.decompress((EXTERNAL / "game_graphs_174.json.gz").read_bytes()))

    def setUp(self):
        self.data_patch = patch.object(graph, "_data", return_value=self.bundle)
        self.data_patch.start()
        graph._records.cache_clear()
        graph.graph.cache_clear()
        self.addCleanup(self.restore)

    def restore(self):
        self.data_patch.stop()
        graph._records.cache_clear()
        graph.graph.cache_clear()

    def fixture(self, definition, *, allied=False, names=("Brigitte",)):
        run, player = brigitte_tests.BrigitteTests().fixture(names)
        player.client = NpcClient(player)
        run.match.game_map = run.match.game_map.__class__(**{
            **run.match.game_map.__dict__, "free_for_all": False,
        })
        x, y, z = player.mover.position
        # Contact cases need to be inside the three-dimensional pin range;
        # Brigitte's recorded eye is 0.03m above the NPC fallback origin.
        distance = 2.9 if definition.hero == 0x02E00000000001BB else 3
        bot = npc.spawn(run.match, definition, (x, y, z + distance),
                        team=player.team if allied else 1 - player.team)
        self.assertIsNotNone(bot.body_script)
        self.assertEqual(bot.body, definition.body)
        target = next(t for t in run.match.combat._targets() if t.owner is bot)
        return run, player, bot, target

    def test_flail_and_whip_damage_all_five_actual_bodies_and_shield_pools(self):
        for definition in self.catalog.values():
            with self.subTest(npc=definition.name):
                run, player, bot, target = self.fixture(definition)
                fight = run.match.combat
                vitals = fight.vitals_of(target)
                before = vitals.total
                brigitte_tests.BrigitteTests().swing(run, player, 20)
                self.assertEqual(vitals.total, before - 35)
                for _ in range(25):
                    run.step({})
                brigitte_tests.BrigitteTests().whip(run, player)
                self.assertEqual(vitals.total, before - 105)
                self.assertEqual(vitals.now[world.ARMOUR_POOL], 0)
                self.assertEqual(vitals.now[world.SHIELDS_POOL], max(0, definition.shields - 105))
                self.assertFalse(bot.dead)

    def test_actual_allied_npcs_receive_repair110_without_hostile_damage(self):
        for definition in self.catalog.values():
            with self.subTest(npc=definition.name):
                run, player, bot, target = self.fixture(definition, allied=True)
                fight = run.match.combat
                # Set up an injured living NPC through ordinary health pools.
                vitals = fight.vitals_of(target)
                vitals.damage(150)
                before = vitals.total
                a, b = target.segment()
                centre = tuple((x + y) / 2 for x, y in zip(a, b, strict=True))
                yaw, pitch = combat_tests.aim(fight._eye(player), centre)
                run.step({player: (yaw, pitch, 16)})
                for _ in range(190):
                    run.step({})
                self.assertAlmostEqual(vitals.total, before + 110, places=4)
                self.assertFalse(bot.dead)

    def test_repair_pack_rejects_invalid_npc_targets_and_drops_dead_recipient(self):
        from test_game_mover import box

        from ow174.game.collision import StaticCollisionProvider, World

        for definition in self.catalog.values():
            for boundary in ("enemy", "range", "aim", "wall", "death"):
                with self.subTest(npc=definition.name, boundary=boundary):
                    run, player, bot, target = self.fixture(
                        definition, allied=boundary != "enemy", names=("Brigitte", "Enemy"),
                    )
                    enemy = run.players[1]
                    enemy.team = 1 - player.team
                    enemy.client = NpcClient(enemy)
                    fight = run.match.combat
                    x, y, z = player.mover.position
                    if boundary in ("range", "death"):
                        bot.position = (x, y, z + (32 if boundary == "range" else 10))
                    elif boundary == "wall":
                        wall = World(box(x - 20, x + 20, y - 2, y, z - 20, z + 20)
                                     + box(x - 4, x + 4, y, y + 4, z + 1.5, z + 1.7))
                        run.match.collision_provider = StaticCollisionProvider(wall)
                        run.match.collision_world = wall
                    target = brigitte_tests.BrigitteTests().target(run, bot)
                    vitals = fight.vitals_of(target)
                    vitals.damage(150)
                    before = vitals.total
                    a, b = target.segment()
                    centre = tuple((low + high) / 2 for low, high in zip(a, b, strict=True))
                    yaw, pitch = combat_tests.aim(fight._eye(player), centre)
                    if boundary == "aim":
                        yaw = -32768
                    instance = next(i for i in player.body_script.component.instances.values()
                                    if i.graph.index == 0x0EDD)
                    run.step({player: (yaw, pitch, 16)})
                    for _ in range(19):
                        run.step({player: (yaw, pitch, 0)})
                    if boundary == "death":
                        self.assertEqual(instance.vars[11].value(), 2)
                        self.assertEqual(len(fight.brigitte.repair_deliveries), 1)
                        fight._damage(enemy, target, vitals.total + 1000, False, None, run.frame)
                    for _ in range(60):
                        run.step({})
                    self.assertFalse(fight.brigitte.repair_deliveries)
                    self.assertFalse(fight.brigitte.repairs)
                    if boundary == "death":
                        self.assertTrue(bot.dead)
                        self.assertEqual(vitals.total, 0)
                    else:
                        self.assertEqual(instance.vars[11].value(), 3)
                        self.assertEqual(vitals.total, before)

    def test_repair_pack_revalidates_npc_relation_and_retires_replaced_body(self):
        for definition in self.catalog.values():
            for boundary in ("team_change", "ffa", "replace_in_flight", "replace_during_heal"):
                with self.subTest(npc=definition.name, boundary=boundary):
                    run, player, bot, _ = self.fixture(definition, allied=True)
                    fight = run.match.combat
                    x, y, z = player.mover.position
                    bot.position = (x, y, z + 10)
                    target = brigitte_tests.BrigitteTests().target(run, bot)
                    vitals = fight.vitals_of(target)
                    vitals.damage(150)
                    before = vitals.total
                    a, b = target.segment()
                    centre = tuple((low + high) / 2 for low, high in zip(a, b, strict=True))
                    yaw, pitch = combat_tests.aim(fight._eye(player), centre)
                    instance = next(i for i in player.body_script.component.instances.values()
                                    if i.graph.index == 0x0EDD)
                    run.step({player: (yaw, pitch, 16)})
                    for _ in range(19):
                        run.step({player: (yaw, pitch, 0)})
                    self.assertEqual(instance.vars[11].value(), 2)
                    self.assertEqual(len(fight.brigitte.repair_deliveries), 1)
                    self.assertEqual(fight.brigitte.repair_deliveries[0].cast.target, bot.entity)
                    if boundary == "team_change":
                        bot.team = 1 - player.team
                    elif boundary == "ffa":
                        game_map = run.match.game_map
                        run.match.game_map = type(game_map)(**{**game_map.__dict__, "free_for_all": True})
                    else:
                        if boundary == "replace_during_heal":
                            for _ in range(45):
                                run.step({})
                            self.assertIn((player.body, bot.entity), fight.brigitte.repairs)
                            self.assertGreater(vitals.total, before)
                            before = vitals.total
                        self.assertEqual(npc.remove_spawned(run.match), (bot.entity,))
                        self.assertFalse(fight.brigitte.repair_deliveries)
                        self.assertFalse(fight.brigitte.repairs)
                        replacement = npc.spawn(run.match, definition, bot.position, team=player.team)
                        self.assertNotEqual(replacement.entity, bot.entity)
                        replacement_target = brigitte_tests.BrigitteTests().target(run, replacement)
                        replacement_hp = fight.vitals_of(replacement_target)
                        replacement_hp.damage(150)
                        replacement_before = replacement_hp.total
                    for _ in range(65):
                        run.step({})
                    self.assertFalse(fight.brigitte.repair_deliveries)
                    self.assertFalse(fight.brigitte.repairs)
                    self.assertEqual(instance.vars[11].value(), 2)
                    self.assertEqual(vitals.total, before)
                    if boundary.startswith("replace"):
                        self.assertEqual(replacement_hp.total, replacement_before)
                        self.assertNotIn(bot.entity, fight.vitals)

    def test_actual_ana_grenade_modifies_repair_and_inspire_on_all_five_npcs(self):
        from test_game_brigitte_healing_modifiers import HealingModifierTests

        grenade = HealingModifierTests()
        for definition in self.catalog.values():
            for friendly in (False, True):
                for ability in ("repair", "inspire"):
                    with self.subTest(npc=definition.name, friendly=friendly, ability=ability):
                        run, player, bot, _ = self.fixture(
                            definition, allied=True, names=("Brigitte", "Ana", "Enemy"),
                        )
                        ana, enemy = run.players[1:]
                        self.assertTrue(run.match.switch_hero(ana, find_hero("Ana")))
                        ana.team = player.team if friendly else 1 - player.team
                        enemy.team = 1 - player.team
                        x, y, z = player.mover.position
                        bot.position = (x, y, z + 10)
                        ana.mover.position, ana.mover.body = [x + 3, y, z + 10], None
                        enemy.mover.position, enemy.mover.body = [x, y, z + 3], None
                        fight = run.match.combat
                        target = brigitte_tests.BrigitteTests().target(run, bot)
                        vitals = fight.vitals_of(target)
                        vitals.damage(100)
                        for _ in range(6):
                            run.step({})
                        a, b = target.segment()
                        centre = tuple((low + high) / 2 for low, high in zip(a, b, strict=True))
                        direction = combat_tests.aim(fight._eye(ana), centre)
                        grenade.grenade(run, ana, vitals, friendly, direction)
                        if friendly:
                            # Leave enough missing health to observe the full multiplier even on a Trooper.
                            vitals.damage(180)
                        before = vitals.total
                        if ability == "repair":
                            yaw, pitch = combat_tests.aim(fight._eye(player), centre)
                            run.step({player: (yaw, pitch, 16)})
                            for _ in range(60):
                                run.step({})
                            self.assertIn((player.body, bot.entity), fight.brigitte.repairs)
                            for _ in range(110):
                                run.step({})
                            self.assertFalse(fight.brigitte.repairs)
                            expected = 165 if friendly else 0
                        else:
                            brigitte_tests.BrigitteTests().swing(run, player, 20)
                            heal = fight.brigitte.inspire[player.body, bot.entity]
                            status = vitals.healing_boost if friendly else vitals.healing_block
                            status_start, status_end = status
                            overlap = status_end - max(heal.until - 6, status_start)
                            self.assertGreater(overlap, 0)
                            self.assertLess(overlap, 6)
                            for _ in range(400):
                                run.step({})
                            self.assertNotIn((player.body, bot.entity), fight.brigitte.inspire)
                            expected = 90 + 15 * overlap * (0.5 if friendly else -1)
                        self.assertAlmostEqual(vitals.total - before, expected, places=5)

    def test_bash_input_damages_all_five_actual_bodies_once(self):
        for definition in self.catalog.values():
            with self.subTest(npc=definition.name):
                run, player, bot, target = self.fixture(definition)
                fight = run.match.combat
                before = fight.vitals_of(target).total
                for _ in range(20):
                    run.step({player: (0, 0, 2)})
                run.step({player: (0, 0, 3)})
                for _ in range(40):
                    run.step({player: (0, 0, 2)})
                self.assertEqual(fight.vitals_of(target).total, before - 5)
                self.assertFalse(bot.dead)

    def test_lethal_bash_delivers_terminal_event_for_each_actual_body(self):
        for definition in self.catalog.values():
            with self.subTest(npc=definition.name):
                run, player, bot, target = self.fixture(definition)
                fight = run.match.combat
                vitals = fight.vitals_of(target)
                start = bot.position
                fight._damage(player, target, vitals.total - 5, False, None, run.frame)
                for _ in range(20):
                    run.step({player: (0, 0, 2)})
                run.step({player: (0, 0, 3)})
                for _ in range(40):
                    run.step({player: (0, 0, 2)})
                self.assertTrue(bot.dead)
                self.assertEqual(bot.position, start)
                self.assertIsNone(bot.impulse_mover)
                self.assertFalse(fight.is_stunned(bot))
                self.assertEqual(vitals.total, 0)
                self.assertTrue(any(entity == bot.entity for entity, _ in player.client.death_events))
                self.assertFalse(any(who is bot for _, who in fight.respawns))

    def test_bash_hosts_authored_stun_on_all_five_actual_bodies_and_expires(self):
        for definition in self.catalog.values():
            with self.subTest(npc=definition.name):
                run, player, bot, _target = self.fixture(definition)
                initial_z = bot.position[2]
                for _ in range(20):
                    run.step({player: (0, 0, 2)})
                run.step({player: (0, 0, 3)})
                run.step({player: (0, 0, 2)})
                self.assertTrue(bot.body_script.component.entity_var(888).value())
                for _ in range(40):
                    run.step({})
                self.assertTrue(bot.body_script.component.entity_var(888).value())
                if definition.name == "Heavy Assault":
                    # The existing live property26 SET0 response admits no force.
                    self.assertEqual(defenses.knockback_scale(bot), 0)
                    self.assertEqual(bot.position[2], initial_z)
                else:
                    self.assertGreater(bot.position[2], initial_z)
                for _ in range(15):
                    run.step({})
                self.assertFalse(bot.body_script.component.entity_var(888).value())
                self.assertFalse(run.match.combat.brigitte.stuns)
                self.assertIsNotNone(bot.body_script)

    def test_bash_uses_live_knockback_scalar_without_changing_damage_or_stun(self):
        for definition in self.catalog.values():
            distances = []
            for scalar in (0, 1, 2):
                with self.subTest(npc=definition.name, scalar=scalar):
                    run, player, bot, target = self.fixture(definition)
                    fight = run.match.combat
                    vitals = fight.vitals_of(target)
                    before = vitals.total
                    start = bot.position
                    for _ in range(20):
                        run.step({player: (0, 0, 2)})
                    host = fight.knockbacks
                    with patch.object(host, "apply", wraps=host.apply) as submit:
                        run.step({player: (0, 0, 3)})
                        player.body_script.component.set(14130, scalar)
                        run.step({player: (0, 0, 2)})
                    self.assertEqual(vitals.total, before - 5)
                    self.assertTrue(fight.is_stunned(bot))
                    response = defenses.knockback_scale(bot)
                    self.assertEqual(response, 0 if definition.name == "Heavy Assault" else 1)
                    self.assertEqual(submit.call_count, 1)
                    self.assertEqual(tuple(submit.call_args.args[1]),
                                     (0, scalar * response, 3 * scalar * response))
                    if scalar == 0 or response == 0:
                        self.assertIsNone(bot.impulse_mover)
                    else:
                        self.assertIsNotNone(bot.impulse_mover)
                        # Canonical ADD is queued for the recipient's next physical
                        # frame; the formed vector above retains its original values.
                        self.assertEqual(len(bot.impulse_mover.velocity_changes), 1)
                    for _ in range(30):
                        run.step({})
                    self.assertTrue(fight.is_stunned(bot))
                    distances.append(bot.position[2] - start[2])
                    if scalar == 0 or response == 0:
                        self.assertEqual(bot.position, start)
                    for _ in range(25):
                        run.step({})
                    self.assertFalse(fight.is_stunned(bot))
            self.assertEqual(len(distances), 3)
            if definition.name == "Heavy Assault":
                self.assertEqual(distances, [0, 0, 0])
            else:
                self.assertGreater(distances[1], distances[0], definition.name)
                self.assertGreater(distances[2], distances[1], definition.name)

    def test_manual_removal_retires_each_actual_bash_recipient_immediately(self):
        for definition in self.catalog.values():
            with self.subTest(npc=definition.name):
                run, player, _bot, _target = self.fixture(definition)
                for _ in range(20):
                    run.step({player: (0, 0, 2)})
                run.step({player: (0, 0, 3)})
                run.step({player: (0, 0, 2)})
                bridge = run.match.combat.brigitte
                self.assertEqual(len(bridge.stuns), 1)
                lease = bridge.stuns[0]
                npc.remove_spawned(run.match)
                self.assertFalse(bridge.stuns)
                self.assertTrue(lease.instance.stopped)

    def test_bash_interrupts_four_actual_primaries_and_they_resume_after_expiry(self):
        warmups = {"Trooper": 0, "Enforcer": 65, "Sniper": 100, "Heavy Assault": 10}
        for definition in self.catalog.values():
            if definition.name not in warmups:
                continue
            with self.subTest(npc=definition.name):
                run, player, bot, _target = self.fixture(definition)
                start = tuple(player.mover.position)
                for _ in range(20):
                    run.step({player: (0, 0, 2)})
                self.assertEqual(self.engage(run, player, bot, buttons=2), bot.entity)
                for _ in range(warmups[definition.name]):
                    run.step({player: (0, 0, 2)})
                run.step({player: (0, 0, 3)})
                run.step({player: (0, 0, 2)})
                player.mover.position, player.mover.body = list(start), None
                player.mover.velocity = (0.0, 0.0, 0.0)
                barrier = run.match.combat.brigitte.barriers[player.body]
                before = barrier.health
                for _ in range(35):
                    run.step({player: (0, 0, 2)})
                self.assertEqual(barrier.health, before)
                for _ in range(150):
                    run.step({player: (0, 0, 2)})
                    if barrier.health < before:
                        break
                self.assertLess(barrier.health, before)
                npc_control.stop(run.match)

    def test_lethal_flail_quick_melee_and_whip_retain_each_actual_npc_without_respawn(self):
        for definition in self.catalog.values():
            for attack, damage in (("flail", 35), ("quick_melee", 35), ("whip", 70)):
                with self.subTest(npc=definition.name, attack=attack):
                    run, player, bot, target = self.fixture(definition)
                    fight = run.match.combat
                    vitals = fight.vitals_of(target)
                    fight._damage(player, target, vitals.total - damage, False, None, run.frame)
                    if attack == "flail":
                        brigitte_tests.BrigitteTests().swing(run, player, 20)
                    elif attack == "whip":
                        brigitte_tests.BrigitteTests().whip(run, player)
                    else:
                        run.step({player: (0, 0, 0x800)})
                        for _ in range(40):
                            run.step({})
                    self.assertEqual(vitals.total, 0)
                    self.assertTrue(bot.dead)
                    self.assertTrue(any(entity == bot.entity for entity, _ in player.client.death_events))
                    for _ in range(205):
                        run.step({})
                    self.assertIn(bot, run.match.bots)
                    self.assertFalse(any(who is bot for _, who in fight.respawns))

    def test_inspire_heals_all_five_actual_allied_bodies_from_real_hostile_contact(self):
        for definition in self.catalog.values():
            with self.subTest(npc=definition.name):
                run, player, bot, target = self.fixture(definition, allied=True, names=('Brigitte', 'Enemy'))
                enemy = run.players[1]
                enemy.team = 1 - player.team
                x, y, z = player.mover.position
                enemy.mover.position, enemy.mover.body = [x + 2, y, z + 3], None
                vitals = run.match.combat.vitals_of(target)
                vitals.damage(150)
                before = vitals.total
                brigitte_tests.BrigitteTests().swing(run, player, 20)
                for _ in range(420):
                    run.step({})
                self.assertAlmostEqual(vitals.total, before + 90, places=4)
                self.assertFalse(bot.dead)
                self.assertNotIn((player.body, bot.entity), run.match.combat.brigitte.inspire)

    def test_rally_grants_and_expires_extra_armor_on_all_five_allied_npcs(self):
        for definition in self.catalog.values():
            with self.subTest(npc=definition.name):
                run, player, _bot, target = self.fixture(definition, allied=True)
                player.body_script.ult_seconds = 0
                player.body_script.set_ult_charge(player.body_script.component.entity_var(338).value())
                run.step({player: (0, 0, 32)})
                for _ in range(220):
                    run.step({})
                vitals = run.match.combat.vitals_of(target)
                self.assertEqual(vitals.temporary_armour, 100)
                self.assertEqual(vitals.now[world.ARMOUR_POOL], 0)
                base_total = definition.health + definition.shields
                self.assertEqual(vitals.total, base_total + 100)
                deadlines = [pool.until for pool in vitals.bonus_armour]
                for _ in range(440):
                    run.step({})
                self.assertFalse(run.match.combat.brigitte.rally_active(player.body_script.component))
                self.assertEqual([pool.until for pool in vitals.bonus_armour], deadlines)
                run.frame = math.ceil(min(deadlines) / 0.016) + 1
                run.match.update(run.frame * 0.016, run.frame, frames=False)
                self.assertEqual(vitals.temporary_armour, 85)
                run.frame = math.ceil(max(deadlines) / 0.016) + 1
                run.match.update(run.frame * 0.016, run.frame, frames=False)
                self.assertEqual(vitals.temporary_armour, 0)
                self.assertEqual(vitals.total, base_total)

    def test_allied_npc_inspire_snapshot_and_rally_pulses_use_distinct_range_and_los(self):
        from test_game_mover import box

        from ow174.game.collision import StaticCollisionProvider, World

        for definition in self.catalog.values():
            for boundary, offset in (("outside_both", 25), ("inspire_only", 10), ("wall", 3)):
                with self.subTest(npc=definition.name, boundary=boundary):
                    run, player, bot, target = self.fixture(
                        definition, allied=True, names=("Brigitte", "Enemy"),
                    )
                    enemy = run.players[1]
                    enemy.team = 1 - player.team
                    x, y, z = player.mover.position
                    enemy.mover.position, enemy.mover.body = [x, y, z + 3], None
                    bot.position = (x - offset, y, z)
                    if boundary == "wall":
                        wall = World(box(x - 30, x + 30, y - 2, y, z - 30, z + 30)
                                     + box(x - 1.7, x - 1.5, y, y + 4, z - 2, z + 2))
                        run.match.collision_provider = StaticCollisionProvider(wall)
                        run.match.collision_world = wall
                    fight = run.match.combat
                    vitals = fight.vitals_of(target)
                    vitals.damage(150)
                    injured = vitals.total
                    brigitte_tests.BrigitteTests().swing(run, player, 20)
                    self.assertIn((player.body, player.body), fight.brigitte.inspire)
                    heal_key = (player.body, bot.entity)
                    expected_inspire = boundary == "inspire_only"
                    self.assertEqual(heal_key in fight.brigitte.inspire, expected_inspire)
                    player.body_script.ult_seconds = 0
                    player.body_script.set_ult_charge(player.body_script.component.entity_var(338).value())
                    run.step({player: (0, 0, 32)})
                    for _ in range(80):
                        run.step({})
                    player_target = brigitte_tests.BrigitteTests().target(run, player)
                    self.assertGreater(fight.vitals_of(player_target).temporary_armour, 0)
                    initial_armour = vitals.temporary_armour
                    self.assertEqual(initial_armour > 0, boundary == "wall")
                    self.assertEqual(vitals.total - initial_armour > injured, expected_inspire)
                    bot.position = (x - 3, y, z + 3)
                    for _ in range(40):
                        run.step({})
                    self.assertGreater(vitals.temporary_armour, initial_armour)
                    self.assertEqual(heal_key in fight.brigitte.inspire, expected_inspire)
                    if not expected_inspire:
                        self.assertEqual(vitals.total - vitals.temporary_armour, injured)

    def test_inspire_and_rally_exclude_all_five_hostile_npc_bodies(self):
        for definition in self.catalog.values():
            with self.subTest(npc=definition.name):
                run, player, bot, target = self.fixture(definition)
                fight = run.match.combat
                vitals = fight.vitals_of(target)
                before = vitals.total
                brigitte_tests.BrigitteTests().swing(run, player, 20)
                self.assertEqual(vitals.total, before - 35)
                self.assertIn((player.body, player.body), fight.brigitte.inspire)
                player.body_script.ult_seconds = 0
                player.body_script.set_ult_charge(player.body_script.component.entity_var(338).value())
                run.step({player: (0, 0, 32)})
                for _ in range(90):
                    run.step({})
                    self.assertNotIn((player.body, bot.entity), fight.brigitte.inspire)
                player_target = brigitte_tests.BrigitteTests().target(run, player)
                self.assertGreater(fight.vitals_of(player_target).temporary_armour, 0)
                self.assertEqual(vitals.temporary_armour, 0)
                self.assertEqual(vitals.total, before - 35)

    def test_actual_four_primaries_hurt_brigitte_and_stop_cleanly(self):
        expected = {"Trooper": 185, "Enforcer": 199, "Sniper": 105, "Heavy Assault": 197.5}
        for definition in self.catalog.values():
            if definition.name not in expected:
                continue
            with self.subTest(npc=definition.name):
                run, player, bot, _target = self.fixture(definition)
                run.step({player: (0, 0, 0)})
                fight = run.match.combat
                owner = next(t for t in fight._targets() if t.owner is player)
                vitals = fight.vitals_of(owner)
                self.assertEqual(self.engage(run, player, bot), bot.entity)
                for _ in range(200):
                    run.step({})
                    if vitals.total < 200:
                        break
                self.assertEqual(vitals.total, expected[definition.name])
                self.assertIsNotNone(bot.body_script)
                npc_control.stop(run.match)
                for _ in range(80):
                    run.step({})
                self.assertEqual(vitals.total, expected[definition.name])

    def test_actual_trooper_fire_is_intercepted_by_raised_shield(self):
        run, player, bot, _target = self.fixture(self.catalog["1"])
        run.step({player: (0, 0, 2)})
        self.assertEqual(self.engage(run, player, bot, buttons=2), bot.entity)
        bridge = run.match.combat.brigitte
        barrier = bridge.barriers[player.body]
        for _ in range(100):
            run.step({player: (0, 0, 2)})
            if barrier.health < 250:
                break
        self.assertEqual(barrier.health, 230)
        fight = run.match.combat
        owner = next(t for t in fight._targets() if t.owner is player)
        self.assertEqual(fight.vitals_of(owner).total, 200)
        npc_control.stop(run.match)

    def test_actual_enforcer_sniper_and_heavy_projectiles_are_intercepted_by_shield(self):
        for definition in self.catalog.values():
            if definition.name not in ("Enforcer", "Sniper", "Heavy Assault"):
                continue
            with self.subTest(npc=definition.name):
                run, player, bot, _target = self.fixture(definition)
                run.step({player: (0, 0, 2)})
                self.assertEqual(self.engage(run, player, bot, buttons=2), bot.entity)
                barrier = run.match.combat.brigitte.barriers[player.body]
                for _ in range(200):
                    run.step({player: (0, 0, 2)})
                    if barrier.health < 250:
                        break
                expected = {"Enforcer": 248, "Sniper": 150, "Heavy Assault": 245}[definition.name]
                self.assertEqual(barrier.health, expected)
                fight = run.match.combat
                owner = next(t for t in fight._targets() if t.owner is player)
                self.assertEqual(fight.vitals_of(owner).total, 200)
                npc_control.stop(run.match)

    def test_all_four_ranged_npc_primaries_hit_barrier_covering_an_off_axis_ally(self):
        expected = {"Trooper": 20, "Enforcer": 2, "Sniper": 100, "Heavy Assault": 5}
        for definition in self.catalog.values():
            if definition.name not in expected:
                continue
            with self.subTest(npc=definition.name):
                run, brig, bot, _ = self.fixture(definition, names=("Brigitte", "Ally"))
                ally = run.players[1]
                ally.client = NpcClient(ally)
                ally.team = brig.team
                x, y, z = brig.mover.position
                brig.mover.position, brig.mover.body = [x + 0.8, y, z], None
                ally.mover.position, ally.mover.body = [x, y, z - 2], None
                bot.position = (x, y, z + 6)
                fight = run.match.combat
                owner = brigitte_tests.BrigitteTests().target(run, brig)
                protected = brigitte_tests.BrigitteTests().target(run, ally)
                owner_hp, ally_hp = fight.vitals_of(owner), fight.vitals_of(protected)
                for _ in range(24):
                    run.step({brig: (0, 0, 2)})
                barrier = fight.brigitte.barriers[brig.body]
                self.assertEqual(self.engage(run, ally, bot), bot.entity)
                for _ in range(200):
                    run.step({brig: (0, 0, 2)})
                    if barrier.health < 250:
                        break
                self.assertEqual(barrier.health, 250 - expected[definition.name])
                self.assertEqual((owner_hp.total, ally_hp.total), (200, 200))
                npc_control.stop(run.match)
                for _ in range(12):
                    run.step({})
                self.assertEqual(self.engage(run, ally, bot), bot.entity)
                for _ in range(200):
                    run.step({})
                    if ally_hp.total < 200:
                        break
                self.assertEqual(ally_hp.total, 200 - expected[definition.name])
                self.assertEqual(owner_hp.total, 200)

    def test_assassin_pins_the_ally_behind_an_off_axis_barrier(self):
        definition = next(d for d in self.catalog.values() if d.name == "Assassin")
        run, brig, bot, _ = self.fixture(definition, names=("Brigitte", "Ally"))
        ally = run.players[1]
        ally.client = NpcClient(ally)
        ally.team = brig.team
        x, y, z = brig.mover.position
        brig.mover.position, brig.mover.body = [x + 0.8, y, z], None
        ally.mover.position, ally.mover.body = [x, y, z - 1], None
        bot.position = (x, y, z + 1.5)
        fight = run.match.combat
        owner_hp = fight.vitals_of(brigitte_tests.BrigitteTests().target(run, brig))
        ally_hp = fight.vitals_of(brigitte_tests.BrigitteTests().target(run, ally))
        for _ in range(24):
            run.step({brig: (0, 0, 2)})
        barrier = fight.brigitte.barriers[brig.body]
        self.assertEqual(self.engage(run, ally, bot), bot.entity)
        run.step({brig: (0, 0, 2)})
        self.assertEqual(fight.pins, {bot.entity: ally.body})
        self.assertTrue(fight.is_stunned(ally))
        self.assertEqual((barrier.health, owner_hp.total, ally_hp.total), (250, 200, 175))
        npc_control.stop(run.match)
        self.assertFalse(fight.is_stunned(ally))

    def test_npc_owner_health_service_tracks_damage_without_resolving_foreign_entities(self):
        run, player, bot, target = self.fixture(self.catalog["2"])
        component = bot.body_script.component
        instance = next(iter(component.instances.values()))
        query = {"$": "STU_9B7648C3", "m_entity": {"$": "STU_B5A0CAF0"}}
        self.assertEqual(component.world.native(query, instance), 350)
        run.match.combat._damage(player, target, 10, False, None, run.frame)
        self.assertEqual(component.world.native(query, instance), 340)
        foreign = {"$": "STU_9B7648C3", "m_entity": {"$": "STUConfigVarDynamic", "m_identifier": 999}}
        with patch.object(runtime.World, "native", return_value=NotImplemented) as fallback:
            self.assertIs(component.world.native(foreign, instance), NotImplemented)
            fallback.assert_called_once_with(foreign, instance)

    def test_all_four_primaries_pause_under_cover_and_resume_same_engagement(self):
        from test_game_mover import box

        from ow174.game.collision import StaticCollisionProvider, World

        first_hit = {"Trooper": 185, "Enforcer": 199, "Sniper": 105, "Heavy Assault": 197.5}
        for definition in self.catalog.values():
            if definition.name not in first_hit:
                continue
            with self.subTest(npc=definition.name):
                run, player, bot, _ = self.fixture(definition)
                fight = run.match.combat
                vitals = fight.vitals_of(brigitte_tests.BrigitteTests().target(run, player))
                x, y, z = player.mover.position
                clear = run.match.collision_world
                wall = World(box(x - 20, x + 20, y - 2, y, z - 20, z + 20)
                             + box(x - 4, x + 4, y, y + 4, z + 1.5, z + 1.7))

                def collision(world, *, run=run, player=player):
                    run.match.collision_provider = StaticCollisionProvider(world)
                    run.match.collision_world = world
                    player.mover.world, player.mover.body = world, None

                collision(wall)
                with self.assertRaisesRegex(ValueError, "No living manual enemy"):
                    self.engage(run, player, bot)
                self.assertFalse(bot.firing)
                collision(clear)
                self.assertEqual(self.engage(run, player, bot), bot.entity)
                for _ in range(200):
                    run.step({})
                    if vitals.total < 200:
                        break
                self.assertEqual(vitals.total, first_hit[definition.name])
                before_cover = vitals.total

                collision(wall)
                for _ in range(150):
                    run.step({})
                    self.assertFalse(bot.attack_input)
                self.assertTrue(bot.firing)
                self.assertEqual(bot.attack_target, player.entity)
                self.assertEqual(vitals.total, before_cover)
                collision(clear)
                for _ in range(200):
                    run.step({})
                    if vitals.total < before_cover:
                        break
                self.assertTrue(bot.attack_input)
                self.assertLess(vitals.total, before_cover)
                self.assertEqual(bot.attack_target, player.entity)
                npc_control.stop(run.match)

    def test_all_five_npcs_pause_on_range_and_team_and_resume_the_retained_target(self):
        from ow174.game import assassin

        for definition in self.catalog.values():
            for boundary in ("range", "friendly"):
                with self.subTest(npc=definition.name, boundary=boundary):
                    run, player, bot, _ = self.fixture(definition)
                    fight = run.match.combat
                    vitals = fight.vitals_of(brigitte_tests.BrigitteTests().target(run, player))
                    self.assertEqual(self.engage(run, player, bot), bot.entity)
                    for _ in range(200):
                        run.step({})
                        if vitals.total < 200:
                            break
                    self.assertLess(vitals.total, 200)
                    before = vitals.total
                    position, team = bot.position, bot.team
                    if boundary == "range":
                        reach = assassin.PIN_RANGE if bot.hero == assassin.HERO else npc_control.AIM_RANGE
                        x, y, z = player.mover.position
                        bot.position = (x, y, z + reach + 2)
                    else:
                        bot.team = player.team
                    for _ in range(80):
                        run.step({})
                        self.assertFalse(bot.attack_input)
                    self.assertFalse(fight.pins)
                    self.assertFalse(fight.is_stunned(player))
                    self.assertTrue(bot.firing)
                    self.assertEqual(bot.attack_target, player.entity)
                    self.assertEqual(vitals.total, before)
                    bot.position, bot.team = position, team
                    for _ in range(200):
                        run.step({})
                        if vitals.total < before:
                            break
                    self.assertLess(vitals.total, before)
                    self.assertEqual(bot.attack_target, player.entity)
                    if bot.hero == assassin.HERO:
                        self.assertEqual(fight.pins, {bot.entity: player.body})
                    npc_control.stop(run.match)

    def test_lethal_incoming_hits_from_all_five_npcs_release_attack_input(self):
        for definition in self.catalog.values():
            with self.subTest(npc=definition.name):
                run, player, bot, _ = self.fixture(definition)
                fight = run.match.combat
                target = brigitte_tests.BrigitteTests().target(run, player)
                vitals = fight.vitals_of(target)
                vitals.damage(vitals.total - 1)
                vitals.damage(vitals.total - 1)  # finish preparing the pool after armor reduction
                self.assertEqual(vitals.total, 1)
                self.assertEqual(self.engage(run, player, bot), bot.entity)
                for _ in range(200):
                    run.step({})
                    if vitals.dead:
                        break
                self.assertTrue(vitals.dead)
                self.assertEqual(vitals.total, 0)
                self.assertEqual(vitals.killer, bot.entity)
                self.assertFalse(fight.pins)
                death_tick = vitals.death_tick
                for _ in range(30):
                    run.step({})
                    self.assertFalse(bot.attack_input)
                    self.assertFalse(fight.pins)
                self.assertEqual(vitals.death_tick, death_tick)
                self.assertEqual(vitals.total, 0)
                self.assertEqual(sum(owner is player for _, owner in fight.respawns), 1)

    def test_assassin_loses_pin_under_cover_and_can_reengage_after_clearance(self):
        from test_game_mover import box

        from ow174.game.collision import StaticCollisionProvider, World

        definition = next(d for d in self.catalog.values() if d.name == "Assassin")
        run, player, bot, _ = self.fixture(definition)
        fight = run.match.combat
        vitals = fight.vitals_of(brigitte_tests.BrigitteTests().target(run, player))
        self.assertEqual(self.engage(run, player, bot), bot.entity)
        run.step({})
        self.assertEqual(fight.pins, {bot.entity: player.body})
        before = vitals.total
        x, y, z = player.mover.position
        clear = run.match.collision_world
        wall = World(box(x - 20, x + 20, y - 2, y, z - 20, z + 20)
                     + box(x - 4, x + 4, y, y + 4, z + 1.5, z + 1.7))
        run.match.collision_provider = StaticCollisionProvider(wall)
        run.match.collision_world = wall
        player.mover.world, player.mover.body = wall, None
        run.step({})
        self.assertFalse(fight.pins)
        self.assertFalse(fight.is_stunned(player))
        self.assertFalse(bot.attack_input)
        for _ in range(90):
            run.step({})
        self.assertEqual(vitals.total, before)
        npc_control.stop(run.match)
        run.match.collision_provider = StaticCollisionProvider(clear)
        run.match.collision_world = clear
        player.mover.world, player.mover.body = clear, None
        self.assertEqual(self.engage(run, player, bot), bot.entity)
        for _ in range(45):
            run.step({})
            if fight.pins:
                break
        self.assertEqual(fight.pins, {bot.entity: player.body})
        self.assertLess(vitals.total, before)
        npc_control.stop(run.match)

    def test_actual_enforcer_projectile_wall_occlusion_and_manual_restart(self):
        from test_game_mover import box

        from ow174.game.collision import StaticCollisionProvider, World

        run, player, bot, _target = self.fixture(self.catalog["2"])
        run.step({player: (0, 0, 0)})
        self.engage(run, player, bot)
        fight = run.match.combat
        vitals = fight.vitals_of(next(t for t in fight._targets() if t.owner is player))
        x, y, z = player.mover.position
        original = run.match.collision_world
        wall = World(box(x - 20, x + 20, y - 2, y, z - 20, z + 20)
                     + box(x - 4, x + 4, y, y + 4, z + 1.5, z + 1.7))
        fired = []
        shoot = fight._shoot

        def capture(who, command, state, number, time, **kwargs):
            if who is bot:
                fired.append((state.instance.graph.index, state.node.pos))
            result = shoot(who, command, state, number, time, **kwargs)
            if who is bot and len(fired) == 1:
                # Insert the wall after real launch, before flight. Placing it
                # before launch would only exercise the manual controller's LOS gate.
                self.assertTrue(fight.rockets)
                run.match.collision_provider = StaticCollisionProvider(wall)
                run.match.collision_world = wall
                player.mover.world, player.mover.body = wall, None
            return result

        with patch.object(fight, "_shoot", side_effect=capture):
            for _ in range(200):
                run.step({})
            self.assertTrue(fired)
            self.assertEqual(set(fired), {(0x13EB, 24)})
            self.assertEqual(vitals.total, 200)
            npc_control.stop(run.match)
            count = len(fired)
            for _ in range(80):
                run.step({})
            self.assertEqual(len(fired), count)
            run.match.collision_provider = StaticCollisionProvider(original)
            run.match.collision_world = original
            player.mover.world, player.mover.body = original, None
            self.engage(run, player, bot)
            for _ in range(200):
                run.step({})
                if vitals.total < 200:
                    break
            self.assertLess(vitals.total, 200)
            self.assertGreater(len(fired), count)


if __name__ == "__main__":
    unittest.main()
