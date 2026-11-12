"""Actual 104319 commands against the authored external mech and Matrix rows."""

import gzip
import json
import os
import unittest
from dataclasses import fields, replace
from pathlib import Path
from unittest.mock import patch

import test_game_brigitte as brigitte_tests
from test_game_mover import box

from ow174.game import mover, pools, projectile_tags
from ow174.game.collision import StaticCollisionProvider, World
from ow174.game.combat import HIT
from ow174.game.commands import FIRE, SECONDARY
from ow174.game.content import find_hero
from ow174.game.script import graph

MECH = 0x0400000000000407
RIFLE = 0x04000000000003D4
HELIX = 0x040000000000072C
FROST = 0x04000000000008BE


class BrigitteMatrixTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        directory = os.environ.get("ORPHEUS_TEST_MATRIX_INPUTS")
        if not directory or not all(projectile_tags.absorbable(guid) for guid in (RIFLE, HELIX)):
            raise unittest.SkipTest("requires prepared 0407 rows and the external 104319 projectile tags")
        path = Path(directory)
        extra = json.loads(gzip.decompress((path / "matrix-candidate-graphs_174.json.gz").read_bytes()))
        base = graph._data()
        key = f"0x{MECH:016X}"
        hero = find_hero("dva")
        mech = {**extra["entities"][key], "hero": f"0x{hero.guid:016X}"}
        known = {row["index"] for row in base["graphs"]}
        data = {
            **base,
            "bodies": {**base["bodies"], key: mech},
            "graphs": base["graphs"] + [row for row in extra["graphs"] if row["index"] not in known],
        }
        # Driver selects its body record by hero. The fixture requests the mech explicitly.
        data["bodies"].pop(f"0x{hero.body:016X}", None)
        raw = json.loads((path / "matrix-candidate-movers_174.json").read_text(encoding="utf-8"))[key]
        values = mover.MoverData(**{field.name: raw[field.name] for field in fields(mover.MoverData)
                                    if field.name in raw})
        for cache in (graph.graph, graph._records, pools.hero_pools):
            cache.cache_clear()
            cls.addClassCleanup(cache.cache_clear)
        cls.enterClassContext(patch.object(graph, "_data", return_value=data))
        cls.enterClassContext(patch.object(mover, "_mover_table", {**mover._movers(), MECH: values}))

    def scene(self, distance=3):
        run, brig = brigitte_tests.BrigitteTests().fixture(("Brigitte", "D.Va", "Soldier"))
        mech, soldier = run.players[1:]
        self.assertTrue(run.match.switch_hero(mech, replace(find_hero("dva"), body=MECH)))
        x, y, z = brig.mover.position
        mech.mover.position, mech.mover.body = [x, y, z + distance], None
        soldier.mover.position, soldier.mover.body = [x, y, z - 5], None
        for _ in range(100):
            run.step({})
        instance = next(i for i in mech.body_script.component.instances.values() if i.graph.index == 0x02CC)
        fight = run.match.combat
        hp = fight.vitals_of(next(t for t in fight._targets() if t.owner is mech))
        self.assertGreater(hp.total, 0)
        self.assertEqual(instance.vars[87].value(), 100)
        return run, brig, mech, soldier, instance, hp

    def test_front_flail_quick_melee_whip_and_bash_bypass_matrix(self):
        for attack, damage in ((FIRE, 30), (0x800, 30), (8, 65), (SECONDARY | FIRE, 2.5)):
            with self.subTest(attack=attack):
                run, brig, mech, _, instance, hp = self.scene()
                initial = hp.total
                start = tuple(mech.mover.position)
                bash = attack == SECONDARY | FIRE
                for _ in range(22 if bash else 3):
                    run.step({brig: (0, 0, SECONDARY if bash else 0), mech: (-32768, 0, SECONDARY)})
                self.assertTrue(instance.state(3).active)
                for frame in range(37):
                    run.step({brig: (0, 0, attack if frame == 0 else SECONDARY if bash else 0),
                              mech: (-32768, 0, SECONDARY)})
                    if hp.total < initial:
                        break
                self.assertEqual(initial - hp.total, damage)
                if bash:
                    self.assertTrue(run.match.combat.is_stunned(mech))
                else:
                    self.assertTrue(instance.state(3).active)
                if attack == 8:
                    run.step({mech: (-32768, 0, SECONDARY)})
                    self.assertNotEqual(tuple(mech.mover.position), start)

    def test_rifle_absorption_keeps_brigitte_shield_intact_until_release(self):
        run, brig, mech, soldier, instance, hp = self.scene(8)
        run.match.game_map = replace(run.match.game_map, free_for_all=False)
        brig.team, soldier.team = mech.team, 1 - mech.team
        # Matrix covers the nearer volume. The raised shield is behind it.
        for _ in range(22):
            run.step({brig: (-32768, 0, SECONDARY), mech: (-32768, 0, SECONDARY)})
        barrier = run.match.combat.brigitte.barriers[brig.body]
        initial = hp.total
        run.step({brig: (-32768, 0, SECONDARY), mech: (-32768, 0, SECONDARY), soldier: (0, 0, FIRE)})
        self.assertTrue(instance.state(3).active)
        self.assertEqual((hp.total, barrier.health), (initial, 250))
        self.assertFalse(soldier.client.messages(HIT))
        for _ in range(25):
            run.step({brig: (-32768, 0, SECONDARY), soldier: (0, 0, FIRE)})
        self.assertFalse(instance.state(3).active)
        self.assertLess(barrier.health, 250)
        self.assertEqual(hp.total, initial)

    def test_helix_absorbs_without_hit_or_blast_and_off_angle_hits(self):
        for facing, wall in ((-32768, False), (0, False), (-32768, True)):
            with self.subTest(facing=facing, wall=wall):
                run, brig, mech, soldier, instance, hp = self.scene(10)
                x, y, z = brig.mover.position
                brig.mover.position, brig.mover.body = [x + 7, y, z], None
                if wall:
                    collision = World(box(x - 20, x + 20, y - 2, y, z - 20, z + 25)
                                      + box(x - 1, x + 1, y, y + 4, z - 2, z - 1.9))
                    run.match.collision_provider = StaticCollisionProvider(collision)
                    run.match.collision_world = collision
                fight = run.match.combat
                initial = hp.total
                for _ in range(3):
                    run.step({mech: (facing, 0, SECONDARY)})
                self.assertTrue(instance.state(3).active)
                with patch.object(
                    fight.projectiles, "absorbed", wraps=fight.projectiles.absorbed,
                ) as absorbed:
                    for frame in range(45):
                        run.step({mech: (facing, 0, SECONDARY),
                                  soldier: (0, 0, SECONDARY if frame == 0 else 0)})
                    self.assertEqual(absorbed.call_count, int(facing == -32768 and not wall))
                self.assertFalse(fight.rockets)
                if facing == -32768:
                    self.assertEqual(hp.total, initial)
                    self.assertFalse(soldier.client.messages(HIT))
                else:
                    self.assertLess(hp.total, initial)

    def test_excluded_mei_frost_stream_bypasses_the_live_matrix(self):
        run, brig, mech, mei, instance, hp = self.scene(4)
        self.assertTrue(run.match.switch_hero(mei, find_hero("mei")))
        x, y, z = brig.mover.position
        brig.mover.position, brig.mover.body = [x + 7, y, z], None
        mei.mover.position, mei.mover.body = [x, y, z], None
        self.assertFalse(projectile_tags.absorbable(FROST))
        for _ in range(15):
            run.step({mech: (-32768, 0, SECONDARY)})
        initial = hp.total
        fight = run.match.combat
        with patch.object(fight, "_trace_absorption", wraps=fight._trace_absorption) as trace:
            for _ in range(30):
                run.step({mech: (-32768, 0, SECONDARY), mei: (0, 0, FIRE)})
        self.assertTrue(instance.state(3).active)
        self.assertIn(FROST, [call.args[1] for call in trace.call_args_list])
        self.assertLess(hp.total, initial)
