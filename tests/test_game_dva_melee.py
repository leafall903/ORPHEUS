"""Authored0043 commands in both forms; target fixtures are not NPC AI acceptance."""

import json
import os
import unittest
from dataclasses import replace
from pathlib import Path
from unittest.mock import patch

from test_game_combat import KANEZAKA_DM, Run, aim, dva_pilot_input
from test_game_mover import FLOOR, box
from test_game_mover import wall as collision_wall

from ow174.game import bots, combat
from ow174.game.collision import World
from ow174.game.content import find_hero
from ow174.game.mover import defined_mover_data
from ow174.game.script import graph

MECH = 0x0400000000000407


class MeleeScenarios:
    @classmethod
    def setUpClass(cls):
        if graph.entity_body(MECH) is None or defined_mover_data(MECH) is None:
            raise unittest.SkipTest("requires external0407 graph/mover inputs; both-form melee OPEN")

    def scenario(self, mech, definition=None, case="hit", *, distance=None):
        run = Run(KANEZAKA_DM, ("DVa", "Target"))
        match = run.match
        match.bots.clear()
        match._pending_bot_scripts.clear()
        attacker, victim = run.players
        hero = find_hero("dva")
        if mech:
            hero = replace(hero, body=MECH)
        self.assertTrue(match.switch_hero(attacker, hero))
        dva_pilot_input(attacker)
        attacker.mover.position = [0, 1, 0]
        attacker.mover.body = None
        if distance is None:
            distance = 4 if case == "range" else 1.5
        victim.mover.position = [0, 1, distance if definition is None else 50]
        victim.mover.body = None
        owner = victim
        if definition is not None:
            owner = bots.Bot(bots.BOT_ENTITY, {
                "hero": definition["hero"], "team": 1, "position": (0, 1, distance),
                "rotation": (0, 0, 0, 1), "graphs": [],
            }, definition, {})
            owner.retain_corpse = True
            match.bots.append(owner)
            match.combat.vitals[owner.entity] = combat.Vitals(
                owner.entity, definition["health"], definition["armour"], definition["shields"],
            )
        if case == "ally":
            match.game_map = replace(match.game_map, free_for_all=False)
            owner.team = attacker.team
        if case == "wall":
            collision = World(FLOOR + collision_wall(-10, 0.7, 10, 0.7))
            match.collision_world = collision
            for player in run.players:
                player.mover.use(world=collision)
        for _ in range(100):
            run.step({})
        target = next(t for t in match.combat._targets() if t.owner is owner)
        self.assertAlmostEqual(target.feet[2], distance, delta=0.01)
        return run, attacker, owner, match.combat.vitals_of(target)

    def strike(self, run, attacker, owner, case="hit"):
        point = owner.position if isinstance(owner, bots.Bot) else owner.mover.position
        yaw, pitch = aim(run.match.combat._eye(attacker), (point[0], point[1] + 1, point[2]))
        if case == "miss":
            yaw, pitch = 16384, 0
        run.step({attacker: (yaw, pitch, 0x800)})
        for _ in range(40):
            run.step({attacker: (yaw, pitch, 0)})

    def wait_for_melee(self, run, attacker, threshold=0):
        instance = next(i for i in attacker.body_script.component.instances.values() if i.graph.index == 0x43)
        ability = instance.state(6)
        for _ in range(100):
            if ability.remaining() <= threshold and not ability.flags & ability.RUNNING:
                break
            run.step({})
        self.assertLessEqual(ability.remaining(), threshold)
        self.assertFalse(ability.flags & ability.RUNNING)
        return ability


class BothFormMeleeTests(MeleeScenarios, unittest.TestCase):
    def test_both_forms_use_authored_thirty_damage_once_and_allow_reuse(self):
        for mech in (False, True):
            with self.subTest(mech=mech):
                run, attacker, victim, vitals = self.scenario(mech)
                self.strike(run, attacker, victim)
                self.assertEqual(vitals.total, 170)
                self.assertEqual([h["+0x78"]["+0x4"] for h in attacker.client.messages(combat.HIT)], [30])
                self.wait_for_melee(run, attacker)
                self.strike(run, attacker, victim)
                self.assertEqual(vitals.total, 140)
                self.assertEqual(len(attacker.client.messages(combat.HIT)), 2)

    def test_both_forms_expire_too_early_melee_press_then_allow_a_fresh_press(self):
        for mech in (False, True):
            with self.subTest(mech=mech):
                run, attacker, victim, vitals = self.scenario(mech)
                self.strike(run, attacker, victim)
                instance = next(i for i in attacker.body_script.component.instances.values()
                                if i.graph.index == 0x43)
                self.assertGreater(instance.state(6).remaining(), 0.15)
                self.strike(run, attacker, victim)
                self.assertEqual(vitals.total, 170)
                self.assertEqual(len(attacker.client.messages(combat.HIT)), 1)
                self.wait_for_melee(run, attacker)
                self.strike(run, attacker, victim)
                self.assertEqual(vitals.total, 140)
                self.assertEqual(len(attacker.client.messages(combat.HIT)), 2)

    def test_both_forms_buffer_melee_press_inside_authored_window(self):
        for mech in (False, True):
            with self.subTest(mech=mech):
                run, attacker, victim, vitals = self.scenario(mech)
                self.strike(run, attacker, victim)
                ability = self.wait_for_melee(run, attacker, 0.08)
                self.assertGreater(ability.remaining(), 0)
                self.strike(run, attacker, victim)
                self.assertEqual(vitals.total, 140)
                self.assertEqual(len(attacker.client.messages(combat.HIT)), 2)

    def test_both_forms_miss_range_wall_and_allies_do_no_damage(self):
        for mech in (False, True):
            for case in ("miss", "range", "wall", "ally"):
                with self.subTest(mech=mech, case=case):
                    run, attacker, victim, vitals = self.scenario(mech, case=case)
                    self.strike(run, attacker, victim, case)
                    self.assertEqual(vitals.total, 200)
                    self.assertEqual(attacker.client.messages(combat.HIT), [])

    def test_dead_owner_script_cannot_begin_melee_in_either_form(self):
        for mech in (False, True):
            with self.subTest(mech=mech):
                run, attacker, victim, vitals = self.scenario(mech)
                attacker.body_script.set_health(0, 600 if mech else 150)
                self.strike(run, attacker, victim)
                self.assertEqual(vitals.total, 200)
                self.assertEqual(attacker.client.messages(combat.HIT), [])


class FiveNpcMeleeTests(MeleeScenarios, unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        path = os.environ.get("ORPHEUS_PVE_NPC_CATALOG")
        if not path:
            raise unittest.SkipTest("requires explicit actual five-NPC catalog/mover inputs; acceptance OPEN")
        data = json.loads(Path(path).read_text(encoding="utf-8"))
        cls.definitions = data["definitions"]
        if (data.get("format_version") != 1 or len(cls.definitions) != 5
                or {r["name"] for r in cls.definitions}
                != {"Trooper", "Enforcer", "Sniper", "Assassin", "Heavy Assault"}):
            raise ValueError("requires actual five-NPC catalog")
        if any(defined_mover_data(int(r["body"], 16)) is None for r in cls.definitions):
            raise unittest.SkipTest("requires all five authored NPC movers; no training-bot fallback")

    def test_all_five_actual_target_bodies_take_damage_but_respect_cover_range_miss_and_team(self):
        for mech in (False, True):
            for row in self.definitions:
                for case in ("hit", "wall", "range", "miss", "ally"):
                    with self.subTest(mech=mech, npc=row["name"], case=case):
                        run, attacker, target, vitals = self.scenario(mech, row, case)
                        before = vitals.total
                        self.strike(run, attacker, target, case)
                        self.assertEqual(vitals.total, before - (30 if case == "hit" else 0))
                        hits = attacker.client.messages(combat.HIT)
                        self.assertEqual([h["+0x78"]["+0x4"] for h in hits], [30] if case == "hit" else [])
                        if case == "hit" and row["shields"]:
                            self.assertEqual(vitals.now[combat.SHIELDS_POOL], row["shields"] - 30)
                            self.assertEqual(vitals.now[combat.HEALTH_POOL], row["health"])

    def test_both_forms_primary_fire_hits_all_five_authored_target_capsules_and_pools(self):
        for mech in (False, True):
            for row in self.definitions:
                with self.subTest(mech=mech, npc=row["name"]):
                    run, attacker, target, vitals = self.scenario(mech, row)
                    run.players[1].spawned = False
                    before = vitals.total
                    fight = run.match.combat
                    capsule = next(item for item in fight._targets() if item.owner is target)
                    x, y, z = capsule.feet
                    yaw, pitch = aim(fight._eye(attacker), (x, y + .85, z - capsule.radius))
                    run.step({attacker: (yaw, pitch, 1)})
                    for _ in range(30):
                        run.step({attacker: (yaw, pitch, 0)})
                    self.assertEqual(vitals.total, before - (22 if mech else 14))
                    hits = attacker.client.messages(combat.HIT)
                    self.assertEqual(len(hits), 11 if mech else 1)
                    self.assertEqual({h["+0x78"]["+0x0"]["+0x0"] for h in hits}, {target.entity})
                    if row["shields"]:
                        self.assertEqual(vitals.now[combat.SHIELDS_POOL],
                                         row["shields"] - (22 if mech else 14))

    def test_light_gun_hits_all_five_npcs_near_range_limit_unless_cover_intervenes(self):
        from ow174.game.script.driver import ULT_CHARGE

        for row in self.definitions:
            for covered in (False, True):
                with self.subTest(npc=row["name"], covered=covered):
                    run, pilot, target, vitals = self.scenario(False, row, distance=99)
                    run.players[1].spawned = False
                    fight = run.match.combat
                    triangles = box(-20, 20, -1, 1, -20, 110)
                    if covered:
                        triangles += collision_wall(-10, 90, 10, 90)
                    run.match.collision_world = World(triangles)
                    pilot.mover.use(world=run.match.collision_world)
                    pilot.mover.position[:] = (0.0, 1.0, 0.0)
                    script = pilot.body_script
                    script.ult_seconds = 0
                    script.set_ult_charge(0)
                    weapon = next(i for i in script.component.instances.values() if i.graph.index == 0x2B8)
                    before = vitals.total
                    capsule = next(item for item in fight._targets() if item.owner is target)
                    x, y, z = capsule.feet
                    yaw, pitch = aim(fight._eye(pilot), (x, y + .85, z - capsule.radius))
                    run.step({pilot: (yaw, pitch, 1)})
                    rocket, = fight.rockets
                    for _ in range(108):
                        run.step({})
                    self.assertIn(rocket, fight.rockets)
                    self.assertGreater(rocket.position[2], 85)
                    self.assertEqual(vitals.total, before)
                    self.assertEqual(pilot.client.messages(combat.HIT), [])
                    for _ in range(24):
                        run.step({})
                        if rocket not in fight.rockets:
                            break
                    self.assertNotIn(rocket, fight.rockets)
                    damage = 0 if covered else 14
                    self.assertEqual(vitals.total, before - damage)
                    hits = [h["+0x78"] for h in pilot.client.messages(combat.HIT)]
                    self.assertEqual([(h["+0x0"]["+0x0"], h["+0x4"]) for h in hits],
                                     [] if covered else [(target.entity, 14)])
                    self.assertEqual(weapon.vars[53].value(), 19)
                    self.assertEqual(script.component.entity_var(ULT_CHARGE).value(), damage)

    def test_both_form_primary_critical_hits_on_all_five_talon_capsules(self):
        for mech in (False, True):
            for row in self.definitions:
                with self.subTest(mech=mech, npc=row["name"]):
                    run, attacker, actor, vitals = self.scenario(mech, row, distance=2)
                    run.players[1].spawned = False
                    fight = run.match.combat
                    target = next(item for item in fight._targets() if item.owner is actor)
                    x, y, z = target.feet
                    point = (x, y + target.eye - combat.HEAD_BELOW + .06, z - target.radius)
                    yaw, pitch = aim(fight._eye(attacker), point)
                    before = vitals.total
                    run.step({attacker: (yaw, pitch, 1)})
                    for _ in range(8):
                        run.step({})
                    hits = [message["+0x78"] for message in attacker.client.messages(combat.HIT)]
                    critical = [hit for hit in hits if hit["+0x8"] & combat.CRITICAL]
                    self.assertTrue(critical)
                    self.assertEqual({hit["+0x0"]["+0x0"] for hit in hits}, {target.entity})
                    self.assertEqual(before - vitals.total, sum(hit["+0x4"] for hit in hits))

    def test_both_forms_primary_rejects_cover_range_miss_and_allies_for_all_five_npcs(self):
        for mech in (False, True):
            for row in self.definitions:
                for case in ("wall", "range", "miss", "ally"):
                    with self.subTest(mech=mech, npc=row["name"], case=case):
                        self.check_primary_exclusion(mech, row, case)

    def check_primary_exclusion(self, mech, row, case):
        distance = 125 if case == "range" else None
        run, attacker, target, vitals = self.scenario(mech, row, case, distance=distance)
        run.players[1].spawned = False
        fight = run.match.combat
        before = dict(vitals.now)
        x, y, z = target.position
        yaw, pitch = aim(fight._eye(attacker), (x, y + 1, z))
        if case == "miss":
            yaw, pitch = 16384, 0
        with patch.object(fight, "_shoot", wraps=fight._shoot) as shoot:
            run.step({attacker: (yaw, pitch, 1)})
            for _ in range(145):
                run.step({})
                if not fight.rockets:
                    break
            self.assertEqual(sum(call.args[0] is attacker for call in shoot.call_args_list), 1)
        self.assertEqual(vitals.now, before)
        self.assertFalse(attacker.client.messages(combat.HIT))
        self.assertFalse(fight.rockets)

    def test_micro_missile_burst_hits_all_five_authored_target_capsules_and_pools(self):
        for row in self.definitions:
            with self.subTest(npc=row["name"]):
                run, attacker, target, vitals = self.scenario(True, row)
                run.players[1].spawned = False
                before = vitals.total
                yaw, pitch = aim(run.match.combat._eye(attacker),
                                 (target.position[0], target.position[1] + 1, target.position[2]))
                for _ in range(160):
                    run.step({attacker: (yaw, pitch, 16)})
                self.assertEqual(vitals.total, before - 126)
                hits = attacker.client.messages(combat.HIT)
                self.assertEqual(len(hits), 36)
                self.assertEqual({h["+0x78"]["+0x0"]["+0x0"] for h in hits}, {target.entity})
                if row["shields"]:
                    self.assertEqual(vitals.now[combat.SHIELDS_POOL], 0)
                    self.assertEqual(vitals.now[combat.HEALTH_POOL], row["health"] - 26)


if __name__ == "__main__":
    unittest.main()
