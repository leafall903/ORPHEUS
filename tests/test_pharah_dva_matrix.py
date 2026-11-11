"""Actual Pharah volleys against a selected 0407 mech and its authored Matrix."""

import gzip
import json
import os
import tempfile
import unittest
from dataclasses import replace
from pathlib import Path
from unittest.mock import patch

import test_pharah_defenses as defense
import test_pharah_pve as pve

from ow174.game import dva_matrix, mover, npc, npc_control, pools
from ow174.game.collision import World
from ow174.game.content import heroes
from ow174.game.script import graph


@unittest.skipUnless(os.environ.get("ORPHEUS_DVA_RUNTIME"), "prepared 104319 mech data required")
class PharahMatrixTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        data = Path(os.environ["ORPHEUS_DVA_RUNTIME"]) / "data"
        for module, attr, name in ((graph, "GRAPHS_PATH", "game_graphs_174.json.gz"),
                                   (mover, "MOVERS_PATH", "game_movers_174.json")):
            path = data / name
            if not path.is_file():
                raise AssertionError(f"missing mech input {path}")
            mount = patch.object(module, attr, path)
            mount.start()
            cls.addClassCleanup(mount.stop)
        cls.clear_inputs()
        cls.addClassCleanup(cls.clear_inputs)

    @staticmethod
    def clear_inputs():
        pve.PharahPveTests.clear_graphs()
        mover._clear_movers()
        pools.hero_pools.cache_clear()

    def arena(self, yaw=-32768):
        fixture = defense.PharahDefenseTests()
        run, shooter, mech, behind = fixture.encounter("D.Va")
        position = list(mech.mover.position)
        self.assertTrue(run.match.switch_hero(mech, replace(mech.hero, body=dva_matrix.MECH)))
        self.assertEqual(mech.mover.data.stand_radius, .7)
        mech.mover.position = position
        mech.mover.body = None
        self.assertEqual(fixture.health(run, mech).total, 600)
        instance = next(i for i in mech.body_script.component.instances.values() if i.graph.index == 0x02CC)
        self.assertEqual(instance.vars[971].value(), 100)
        for _ in range(100):
            run.step({mech: (yaw, 0, 0)})
        return fixture, run, shooter, mech, behind, instance

    def test_primary_concussion_and_barrage_are_consumed(self):
        for button in (1, 16, 32):
            with self.subTest(button=button):
                fixture, run, shooter, mech, behind, instance = self.arena()
                facing = {mech: (-32768, 0, 2)}
                for _ in range(10):
                    run.step(facing)
                self.assertTrue(instance.states[3].active)
                self.assertLess(instance.vars[87].value(), 100)
                position = tuple(mech.mover.position)
                script = shooter.body_script
                if button == 32:
                    script.set_ult_charge(script.component.entity_var(338).value())
                run.step({**facing, shooter: (0, 0, button)})
                for _ in range(28):
                    run.step(facing)
                self.assertEqual(fixture.health(run, mech).total, 600)
                self.assertEqual(fixture.health(run, behind).total, 200)
                self.assertEqual(tuple(mech.mover.position), position)
                self.assertEqual(script.component.entity_var(342).value(), 0)
                self.assertFalse(shooter.client.messages(25001))

    def test_quick_melee_hits_through_actual_defense_matrix(self):
        fixture, run, player, mech, _, instance = self.arena()
        mech.mover.position[2] = 1.8
        mech.mover.body = None
        for _ in range(10):
            run.step({mech: (-32768, 0, 2)})
        self.assertTrue(instance.states[3].active)
        before = fixture.health(run, mech).total
        run.step({player: (0, 0, 0x800), mech: (-32768, 0, 2)})
        self.assertTrue(instance.states[3].active)
        self.assertEqual(fixture.health(run, mech).total, before - 25)
        self.assertEqual(player.body_script.component.entity_var(342).value(), 25)

    def mount_npc_inputs(self):
        npc_bundle = Path(os.environ.get("ORPHEUS_ASSOCIATED_GRAPH_BUNDLE", ""))
        catalog = Path(os.environ.get("ORPHEUS_NPC_DATA_DIR", "")) / "pve_npcs_174.json"
        if not npc_bundle.is_file() or not catalog.is_file():
            self.skipTest("prepared 104319 associated NPC graphs and catalog required")
        with gzip.open(graph.GRAPHS_PATH, "rt", encoding="utf-8") as source:
            combined = json.load(source)
        with gzip.open(npc_bundle, "rt", encoding="utf-8") as source:
            npc_data = json.load(source)
        graphs = {row["index"]: row for row in npc_data["graphs"]}
        graphs.update({row["index"]: row for row in combined["graphs"]})
        combined["graphs"] = list(graphs.values())
        combined["entities"] = {**combined["entities"], **npc_data["entities"]}
        bundle = Path(self.enterContext(tempfile.TemporaryDirectory())) / "matrix-and-npcs.json.gz"
        with gzip.open(bundle, "wt", encoding="utf-8", compresslevel=1) as output:
            json.dump(combined, output)
        self.addCleanup(self.clear_inputs)
        self.enterContext(patch.object(graph, "GRAPHS_PATH", bundle))
        self.clear_inputs()
        return {row.name: row for row in npc.load_catalog(catalog).values()}

    def test_allied_matrix_protects_pharah_from_trooper_hitscan_until_release(self):
        trooper = self.mount_npc_inputs()["Trooper"]
        fixture, run, pharah, mech, spare, instance = self.arena(yaw=0)
        match = run.match
        match.game_map = replace(match.game_map, free_for_all=False)
        pharah.team = mech.team = 0
        for player in run.players:
            player.client = pve.NpcClient(player)
        y = pharah.mover.position[1]
        spare.mover.position, mech.mover.position = [30, y, 0], [1, y, 4]
        spare.mover.body = mech.mover.body = None
        attacker = npc.spawn(match, trooper, (0, y, 8), team=1)
        for _ in range(10):
            run.step({mech: (0, 0, 2)})
        self.assertTrue(instance.states[3].active)
        pharah.mover.yaw, pharah.mover.pitch = pve.PharahPveTests.aim_at_body(run, pharah, attacker)
        self.assertEqual(npc_control.start(match, pharah), attacker.entity)
        weapon = next(i for i in attacker.body_script.component.instances.values() if i.graph.index == 0x102E)
        for _ in range(80):
            run.step({mech: (0, 0, 2)})
            if weapon.vars[53].value() < 30:
                break
        self.assertEqual(weapon.vars[53].value(), 29)
        self.assertEqual(fixture.health(run, pharah).total, 200)
        for _ in range(80):
            run.step({})
            if fixture.health(run, pharah).total < 200:
                break
        self.assertFalse(instance.states[3].active)
        self.assertEqual(fixture.health(run, pharah).total, 180)
        self.assertEqual(fixture.health(run, mech).total, 600)

    def test_allied_matrix_protects_pharah_from_sniper_and_enforcer_projectiles(self):
        definitions = self.mount_npc_inputs()
        cases = (
            ("Sniper", True, 25, 200, 0x23BB, 1, 5),
            ("Sniper", False, 25, 200, 0x1E80, 1, 100),
            ("Enforcer", False, 8, 80, 0x23F0, 25, 38),
        )
        for name, alternate, distance, speed, projectile, count, damage in cases:
            for matrix in (False, True):
                with self.subTest(name=name, alternate=alternate, matrix=matrix):
                    fixture, run, pharah, mech, spare, instance = self.arena(yaw=0)
                    match = run.match
                    match.game_map = replace(match.game_map, free_for_all=False)
                    pharah.team = mech.team = 0
                    for player in run.players:
                        player.client = pve.NpcClient(player)
                    y = pharah.mover.position[1]
                    spare.mover.position, mech.mover.position = [30, y, 0], [10, y, distance / 2]
                    spare.mover.body = mech.mover.body = None
                    attacker = npc.spawn(match, definitions[name], (0, y, distance), team=1)
                    pharah.mover.yaw, pharah.mover.pitch = pve.PharahPveTests.aim_at_body(run, pharah, attacker)
                    self.assertEqual(npc_control.start(match, pharah, alternate=alternate), attacker.entity)
                    mech.mover.position = [1, y, distance / 2]
                    mech.mover.body = None
                    fight = match.combat
                    with patch.object(fight.projectiles, "fired", wraps=fight.projectiles.fired) as fired:
                        for _ in range(160):
                            if fight.rockets:
                                break
                            run.step({})
                    volley = [rocket for rocket in fight.rockets if rocket.shooter is attacker]
                    self.assertEqual(len(volley), count)
                    self.assertEqual({rocket.speed for rocket in volley}, {speed})
                    self.assertEqual([call.args[3] for call in fired.call_args_list],
                                     [0x0400000000000000 | projectile])
                    for _ in range(15):
                        run.step({mech: (0, 0, 2 if matrix else 0)})
                    state = instance.states.get(3)
                    self.assertEqual(state is not None and state.active, matrix)
                    self.assertEqual(fixture.health(run, mech).total, 600)
                    self.assertEqual(fixture.health(run, pharah).total, 200 if matrix else 200 - damage)

    def test_releasing_matrix_mid_barrage_allows_later_rounds_to_hit(self):
        fixture, run, pharah, mech, behind, instance = self.arena()
        held = {mech: (-32768, 0, 2)}
        released = {mech: (-32768, 0, 0)}
        for _ in range(10):
            run.step(held)
        self.assertTrue(instance.states[3].active)
        script = pharah.body_script
        script.set_ult_charge(script.component.entity_var(338).value())
        fight = run.match.combat
        health = fixture.health(run, mech)
        run.step({**held, pharah: (0, 0, 32)})
        first, = fight.rockets
        for _ in range(30):
            if all(rocket is not first for rocket in fight.rockets):
                break
            run.step(held)
        self.assertTrue(all(rocket is not first for rocket in fight.rockets))
        self.assertTrue(instance.states[3].active)
        self.assertEqual(health.total, 600)
        self.assertFalse(pharah.client.messages(25001))
        for _ in range(40):
            run.step(released)
            if health.total < 600:
                break
        self.assertFalse(instance.states[3].active)
        self.assertLess(health.total, 600)
        self.assertTrue(script.ultimate_running())
        self.assertEqual(fixture.health(run, behind).total, 200)
        self.assertEqual(script.component.entity_var(342).value(), 0)

    def test_matrix_consumes_enemy_rocket_only_after_genji_reflects_it(self):
        for button in (1, 16):
            with self.subTest(button=button):
                self.assert_matrix_consumes_reflected_rocket(button)

    def assert_matrix_consumes_reflected_rocket(self, button):
        fixture, run, pharah, mech, genji, instance = self.arena(yaw=0)
        self.assertTrue(run.match.switch_hero(genji, heroes()[0x02E0000000000029]))
        run.match.game_map = replace(run.match.game_map, free_for_all=False)
        pharah.team = mech.team = 0
        genji.team = 1
        y = pharah.mover.position[1]
        mech.mover.position = [0, y, -4]
        genji.mover.position = [0, y, 8]
        mech.mover.body = genji.mover.body = None
        for player in (pharah, genji, mech):
            player.body_script.ult_seconds = 0
            player.body_script.set_ult_charge(0)
        run.step({genji: (-32768, 0, 16), mech: (0, 0, 2)})
        for _ in range(7):
            run.step({genji: (-32768, 0, 0), mech: (0, 0, 2)})
        self.assertTrue(instance.states[3].active)
        fight = run.match.combat
        before = [tuple(player.mover.position) for player in (pharah, genji, mech)]
        facing = {genji: (-32768, 0, 0), mech: (0, 0, 2)}
        run.step({**facing, pharah: (0, 0, button)})
        launched = False
        reflected = False
        for _ in range(45):
            launched |= any(rocket.shooter is pharah for rocket in fight.rockets)
            reflected |= any(rocket.shooter is genji for rocket in fight.rockets)
            run.step(facing)
        self.assertTrue(launched)
        self.assertTrue(reflected)
        self.assertFalse(fight.rockets)
        self.assertEqual([tuple(player.mover.position) for player in (pharah, genji, mech)], before)
        self.assertEqual(fixture.health(run, pharah).total, 200)
        self.assertEqual(fixture.health(run, genji).total, 200)
        self.assertEqual(fixture.health(run, mech).total, 600)
        for player in (pharah, genji, mech):
            self.assertEqual(player.body_script.component.entity_var(342).value(), 0)
            self.assertFalse(player.client.messages(25001))

    def test_airborne_blizzard_is_consumed_before_reaching_pharah(self):
        fixture, run, pharah, mech, mei, instance = self.arena(yaw=0)
        self.assertTrue(run.match.switch_hero(mei, heroes()[0x02E00000000000DD]))
        run.match.game_map = replace(run.match.game_map, free_for_all=False)
        pharah.team = mech.team = 0
        mei.team = 1
        floor = pharah.mover.position[1]
        mech.mover.position = [0, floor, 5.5]
        mei.mover.position = [0, floor, 8]
        mech.mover.body = mei.mover.body = None
        script = mei.body_script
        script.ult_seconds = 0
        script.set_ult_charge(script.component.entity_var(338).value())
        fight = run.match.combat
        released = False
        for frame in range(90):
            run.step({mech: (0, 0, 2), mei: (-32768, 3800, 32 if frame == 10 else 0)})
            released |= bool(fight.blizzard_flights)
            self.assertFalse(fight.blizzards)
        self.assertTrue(released)
        self.assertTrue(instance.states[3].active)
        self.assertEqual(script.component.entity_var(342).value(), 0)
        self.assertFalse(fight.blizzard_flights)
        self.assertEqual(fixture.health(run, pharah).total, 200)
        self.assertNotIn(pharah.body, fight.frost)

    def test_back_exposure_and_resource_depletion_allow_primary(self):
        for yaw, hold in ((0, 10), (-32768, 160)):
            with self.subTest(yaw=yaw):
                fixture, run, shooter, mech, _, instance = self.arena(yaw)
                facing = {mech: (yaw, 0, 2)}
                for _ in range(hold):
                    run.step(facing)
                self.assertEqual(instance.states[3].active, yaw == 0)
                run.step({**facing, shooter: (0, 0, 1)})
                for _ in range(35):
                    run.step(facing)
                self.assertLess(fixture.health(run, mech).total, 600)

    def test_friendly_matrix_and_wall_precedence(self):
        fixture, run, shooter, mech, behind, _ = self.arena()
        run.match.game_map = replace(run.match.game_map, free_for_all=False)
        shooter.team = mech.team = 0
        behind.team = 1
        facing = {mech: (-32768, 0, 2)}
        for _ in range(10):
            run.step(facing)
        run.step({**facing, shooter: (0, 0, 1)})
        for _ in range(35):
            run.step(facing)
        self.assertEqual(fixture.health(run, behind).total, 80)
        self.assertEqual(fixture.health(run, mech).total, 600)

        fixture, run, shooter, mech, _, instance = self.arena()
        mech.mover.position[2] = 14
        mech.mover.body = None
        floor = shooter.mover.position[1]
        wall = World([(-5, floor, 2, -5, floor + 5, 2, 5, floor + 5, 2),
                      (-5, floor, 2, 5, floor + 5, 2, 5, floor, 2)])
        for _ in range(10):
            run.step({mech: (-32768, 0, 2)})
        self.assertTrue(instance.states[3].active)
        with patch.object(run.match.combat, "_collision", return_value=wall):
            run.step({mech: (-32768, 0, 2), shooter: (0, 0, 1)})
            for _ in range(35):
                run.step({mech: (-32768, 0, 2)})
        self.assertLess(fixture.health(run, shooter).total, 200)
        self.assertEqual(fixture.health(run, mech).total, 600)

    def test_release_recovers_resource_and_fresh_press_reopens_matrix(self):
        _, run, _, mech, _, instance = self.arena()
        for _ in range(60):
            run.step({mech: (-32768, 0, 2)})
        spent = instance.vars[87].value()
        self.assertAlmostEqual(spent, 52, delta=.1)
        run.step({mech: (-32768, 0, 0)})
        self.assertFalse(instance.states[3].active)
        for _ in range(100):
            run.step({mech: (-32768, 0, 0)})
        self.assertGreater(instance.vars[87].value(), spent)
        run.step({mech: (-32768, 0, 2)})
        self.assertTrue(instance.states[3].active)
