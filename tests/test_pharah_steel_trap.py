"""Steel Trap responses through actual Junkrat and Pharah inputs."""

import gzip
import json
import os
from pathlib import Path
import tempfile
import unittest
from dataclasses import replace
from unittest.mock import patch

import test_pharah_dva_matrix as matrix
import test_pharah_nano as nano
from test_game_combat import aim

from ow174.game import combat, steel_trap
from ow174.game.content import heroes
from ow174.game.script import graph


def relocate(player, x, z):
    player.mover.position[0], player.mover.position[2] = x, z
    player.mover.body = None


class PharahSteelTrapTests(unittest.TestCase):
    def mount_trap(self):
        self.addCleanup(nano.PharahNanoTests.doClassCleanups)
        nano.PharahNanoTests.setUpClass()
        if graph.graph(steel_trap.VICTIM) is None:
            self.skipTest("Supply the extracted Steel Trap graph 09BC")
    def deploy(self):
        fixture, run, pharah, ana, junkrat = nano.PharahNanoTests().arena()
        self.assertTrue(run.match.switch_hero(junkrat, heroes()[steel_trap.HERO]))
        relocate(junkrat, 0, 2)
        relocate(pharah, 20, 8)
        relocate(ana, 30, 0)
        for _ in range(10):
            run.step({})
        run.step({junkrat: (0, 0, 16)})
        return fixture, run, pharah, junkrat

    def test_batch_crossing_landing_and_arming_preserves_trigger_deadline(self):
        self.mount_trap()
        _, run, _, junkrat = self.deploy()
        fight = run.match.combat
        for _ in range(70):
            run.step({})
            trap = fight.steel_traps.get(junkrat.body)
            if trap is not None and trap.armed_tick is not None:
                break
        else:
            self.fail("Trap failed to land")
        landing, armed = run.frame, trap.armed_tick
        x, _, z = trap.flight.position

        for catch_up in (3, combat.COMBAT_CATCH_UP + 1):
            with self.subTest(catch_up=catch_up):
                fixture, run, pharah, junkrat = self.deploy()
                fight = run.match.combat
                relocate(pharah, x, z)
                while run.frame < landing - 2:
                    run.step({})
                run.frame = landing + catch_up
                run.match.update(run.frame * .016, run.frame)
                trap = fight.steel_traps[junkrat.body]
                self.assertEqual(trap.armed_tick, armed)
                if run.frame < armed:
                    self.assertEqual(fixture.health(run, pharah).total, 200)
                    while run.frame < armed - 2:
                        run.step({})
                    run.frame = armed + 3
                    run.match.update(run.frame * .016, run.frame)
                self.assertEqual(fixture.health(run, pharah).total, 120)
                self.assertEqual(trap.victim, pharah.body)
                self.assertEqual(trap.end_tick, armed + 188)
                while run.frame < trap.end_tick - 2:
                    run.step({})
                run.frame = trap.end_tick + 2
                run.match.update(run.frame * .016, run.frame)
                self.assertNotIn(junkrat.body, fight.steel_traps)
                self.assertTrue(trap.status.stopped)
                run.step({pharah: (0, 0, 8)})
                self.assertGreater(pharah.mover.velocity[1], 0)

    def test_pharah_melee_breaks_a_hostile_trap_with_no_ultimate_credit(self):
        self.mount_trap()
        fixture, run, pharah, junkrat = self.deploy()
        for _ in range(70):
            run.step({})
        fight = run.match.combat
        trap, = fight.steel_traps.values()
        self.assertIsNotNone(trap.armed_tick)
        x, y, z = trap.flight.position
        relocate(pharah, x, z + 1.9)
        script = pharah.body_script
        script.ult_seconds = 0
        script.set_ult_charge(0)
        yaw, pitch = aim(fight._eye(pharah), (x, y + .1, z))
        for health in (70, 40, 10, 0):
            run.step({pharah: (yaw, pitch, 0x800)})
            for _ in range(70):
                run.step({})
            self.assertEqual(trap.health, health)
        self.assertNotIn(junkrat.body, fight.steel_traps)
        self.assertEqual(fixture.health(run, junkrat).total, 200)
        self.assertEqual(fixture.health(run, pharah).total, 200)
        self.assertEqual(script.component.entity_var(342).value(), 0)

    @unittest.skipUnless(os.environ.get("ORPHEUS_DVA_RUNTIME"), "prepared 104319 mech data required")
    def test_allied_matrix_intercepts_airborne_trap(self):
        trap_bundle = Path(os.environ.get("ORPHEUS_NANO_GRAPH_BUNDLE", ""))
        if not trap_bundle.is_file():
            self.skipTest("Supply the extracted Steel Trap graph 09BC")
        self.addCleanup(matrix.PharahMatrixTests.doClassCleanups)
        matrix.PharahMatrixTests.setUpClass()
        with gzip.open(graph.GRAPHS_PATH, "rt", encoding="utf-8") as source:
            combined = json.load(source)
        with gzip.open(trap_bundle, "rt", encoding="utf-8") as source:
            trap_data = json.load(source)
        graphs = {row["index"]: row for row in combined["graphs"]}
        for row in trap_data["graphs"]:
            if row["index"] in (steel_trap.CAST, steel_trap.EFFECT, steel_trap.VICTIM):
                graphs[row["index"]] = row
        combined["graphs"] = list(graphs.values())
        with tempfile.TemporaryDirectory() as directory:
            bundle = Path(directory) / "matrix-and-steel-trap.json.gz"
            with gzip.open(bundle, "wt", encoding="utf-8", compresslevel=1) as target:
                json.dump(combined, target)
            with patch.object(graph, "GRAPHS_PATH", bundle):
                self.addCleanup(matrix.PharahMatrixTests.clear_inputs)
                matrix.PharahMatrixTests.clear_inputs()
                for active in (False, True):
                    with self.subTest(matrix=active):
                        self.throw_toward_matrix(active)

    def throw_toward_matrix(self, active):
        fixture, run, pharah, mech, junkrat, instance = matrix.PharahMatrixTests().arena(yaw=0)
        self.assertTrue(run.match.switch_hero(junkrat, heroes()[steel_trap.HERO]))
        run.match.game_map = replace(run.match.game_map, free_for_all=False)
        pharah.team = mech.team = 0
        junkrat.team = 1
        relocate(mech, 0, 5.5)
        relocate(junkrat, 0, 8)
        fight = run.match.combat
        next_entity = fight.next_bot
        for frame in range(48):
            run.step({mech: (0, 0, 2 if active else 0),
                      junkrat: (-32768, 0, 16 if frame == 10 else 0)})
        self.assertEqual(fight.next_bot, next_entity + 1)
        state = instance.states.get(3)
        self.assertEqual(state is not None and state.active, active)
        if active:
            self.assertNotIn(junkrat.body, fight.steel_traps)
        else:
            self.assertIsNotNone(fight.steel_traps[junkrat.body].armed_tick)
        self.assertEqual(fixture.health(run, pharah).total, 200)
