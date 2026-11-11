"""Actual 0407 player inputs and Soldier attacks against Defense Matrix."""

import os
import unittest
from dataclasses import replace
from pathlib import Path
from unittest.mock import patch

import test_soldier_combat_lane as lane
from test_game_combat import Run, aim, in_world
from test_soldier_pve_static import NpcRecordingClient

from ow174.game import combat, defenses, matrix, mover, npc, npc_control, projectile_tags, projectiles, stats, ultimate
from ow174.game.collision import World, flat_world
from ow174.game.commands import Command
from ow174.game.content import heroes
from ow174.game.script import graph
from ow174.game.script.driver import OWNER_HEALTH


class SoldierMatrixTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        for key, module, attr in (
            ("ORPHEUS_MATRIX_GRAPHS", graph, "GRAPHS_PATH"),
            ("ORPHEUS_MATRIX_MOVERS", mover, "MOVERS_PATH"),
        ):
            path = os.environ.get(key)
            if not path:
                raise unittest.SkipTest("prepared 0407 graph and mover inputs required")
            patcher = patch.object(module, attr, Path(path))
            patcher.start()
            cls.addClassCleanup(patcher.stop)
        cls.reset_catalogs()
        cls.addClassCleanup(cls.reset_catalogs)

    @staticmethod
    def reset_catalogs():
        graph._data.cache_clear()
        graph._records.cache_clear()
        graph.graph.cache_clear()
        mover._movers.cache_clear()

    def arena(self):
        run = Run(names=("Soldier", "Mech"))
        soldier, mech = run.players
        run.match.bots.clear()
        run.match._pending_bot_scripts.clear()
        run.match.collision_world = flat_world(0)
        self.assertTrue(run.match.switch_hero(mech, replace(heroes()[matrix.DVA], body=matrix.MECH)))
        self.assertEqual(mech.mover.data.stand_radius, .7)
        self.assertEqual(mech.body_script.component.vars[OWNER_HEALTH].value(), 600)
        for player, position in ((soldier, [0, 0, 12]), (mech, [0, 0, 0])):
            player.mover.position = position
            player.mover.body = None
            player.body_script.ult_seconds = 0
        # 0276 node 57 owns the authored one-second startup lock.
        for _ in range(75):
            self.step(run, soldier, mech)
        self.assertEqual(self.pool(run, mech).total, 600)
        return run, soldier, mech

    def pool(self, run, actor):
        target = next(t for t in run.match.combat._targets() if t.owner is actor)
        return run.match.combat.vitals_of(target)

    def step(self, run, soldier, mech, buttons=0, held=0, facing=0):
        angles = aim(run.match.combat._eye(soldier), (0, .8, 0))
        run.step({soldier: (*angles, buttons), mech: (facing, 0, held)})

    def instance(self, mech):
        return next(i for i in mech.body_script.component.instances.values() if i.graph.index == 0x02CC)

    def state(self, mech):
        return self.instance(mech).states.get(3)

    def test_rifle_and_visor_check_matrix_at_the_viewed_pose(self):
        for visor in (False, True):
            for viewed, current in ((0, 30), (30, 0)):
                with self.subTest(visor=visor, viewed=viewed, current=current):
                    run, soldier, mech = self.arena()
                    rear = lane.SoldierCombatLaneTests.add_bot(self, run, mech.team, (0, 0, -3))
                    if visor:
                        soldier.body_script.set_ult_charge(soldier.body_script.component.vars[338].value())
                        self.step(run, soldier, mech, 0x20)
                        for _ in range(80):
                            self.step(run, soldier, mech)
                    mech.mover.position[0] = viewed
                    mech.mover.body = None
                    for _ in range(15):
                        self.step(run, soldier, mech, held=2)
                    self.assertTrue(self.state(mech).active)
                    mech.mover.position[0] = current
                    mech.mover.body = None
                    run.frame += 1
                    run.match.update(run.frame * combat.TICK, run.frame)
                    yaw, pitch = aim(run.match.combat._eye(soldier), (0, .8, -3))
                    soldier.take_commands([Command(run.frame, yaw=yaw, pitch=pitch, buttons=1, extra=128)])
                    self.assertEqual(self.pool(run, mech).total, 600)
                    self.assertEqual(self.pool(run, rear).total, 200 if viewed == 0 else 180)

    def test_actual_rifle_and_helix_stop_at_matrix_then_damage_mech_when_released(self):
        for weapon, damage in ((1, 15), (2, 110)):
            with self.subTest(weapon=weapon):
                run, soldier, mech = self.arena()
                for _ in range(4):
                    self.step(run, soldier, mech, held=2)
                self.assertTrue(self.state(mech).active)
                self.step(run, soldier, mech, weapon, held=2)
                for _ in range(25):
                    self.step(run, soldier, mech, held=2)
                self.assertEqual(self.pool(run, mech).total, 600)
                self.assertFalse(run.match.combat.rockets)
                self.assertFalse(soldier.client.messages(combat.HIT))
                self.assertEqual(soldier.body_script.component.vars[342].value(), 0)
                for _ in range(650 if weapon == 2 else 40):
                    self.step(run, soldier, mech)
                self.assertFalse(self.state(mech).active)
                self.step(run, soldier, mech, weapon)
                for _ in range(25):
                    self.step(run, soldier, mech)
                self.assertEqual(self.pool(run, mech).total, 600 - damage)
                self.assertEqual(soldier.body_script.component.vars[342].value(), damage)
                tags = projectile_tags.tags_of(matrix.MECH)
                if tags is not None:
                    self.assertIn(ultimate.DVA_TARGET_TAG, tags)
                    charge = ultimate.instance_of(soldier.body_script)
                    self.assertEqual(charge.find_var(0, 393).value().id, mech.body)

    def test_held_matrix_exhausts_and_rifle_hits(self):
        run, soldier, mech = self.arena()
        self.step(run, soldier, mech, held=2)
        self.assertTrue(self.state(mech).active)
        for _ in range(90):
            self.step(run, soldier, mech, buttons=1, held=2)
        self.assertEqual(self.pool(run, mech).total, 600)
        for _ in range(55):
            self.step(run, soldier, mech, buttons=1, held=2)
        self.assertFalse(self.state(mech).active)
        self.assertGreater(soldier.stats.value(stats.HERO_DAMAGE), 0)

    def test_matrix_release_orders_helix_contact_and_recharges_without_commands(self):
        for held_at_launch in (False, True):
            for jump in (False, True):
                with self.subTest(held_at_launch=held_at_launch, jump=jump):
                    run, soldier, mech = self.arena()
                    soldier.mover.position[2] = 6
                    soldier.mover.body = None
                    for _ in range(54):
                        self.step(run, soldier, mech, held=2)
                    self.assertTrue(self.state(mech).active)
                    resource = self.instance(mech).find_var(0, 87)
                    cap = self.instance(mech).find_var(0, 971)
                    launch = run.frame + 1
                    run.match.update(launch * combat.TICK, launch)
                    if held_at_launch:
                        mech.take_commands([Command(launch, buttons=2)])
                    yaw, pitch = aim(run.match.combat._eye(soldier), (0, .8, 0))
                    soldier.take_commands([Command(launch, yaw=yaw, pitch=pitch, buttons=2)])
                    self.assertEqual(len(run.match.combat.rockets), 1)

                    end = launch + 25
                    for tick in ((end,) if jump else range(launch + 1, end + 1)):
                        run.match.update(tick * combat.TICK, tick)
                    self.assertFalse(self.state(mech).active)
                    self.assertFalse(run.match.combat.rockets)
                    damage = 0 if held_at_launch else 110
                    self.assertEqual(self.pool(run, mech).total, 600 - damage)
                    self.assertEqual(soldier.body_script.component.vars[342].value(), damage)
                    self.assertLess(resource.value(), cap.value())

                    mech.take_commands([Command(end, buttons=2)])
                    self.assertFalse(self.state(mech).active)
                    recharged = end + 250
                    run.match.update(recharged * combat.TICK, recharged)
                    self.assertEqual(resource.value(), cap.value())
                    mech.take_commands([Command(recharged, buttons=2)])
                    self.assertTrue(self.state(mech).active)

    def test_actual_field_canister_is_excluded_by_active_matrix(self):
        if not os.environ.get("ORPHEUS_PROJECTILE_TAGS"):
            self.skipTest("prepared original003 projectile tags required")
        run, soldier, mech = self.arena()
        soldier.mover.position[:] = [0, 0, -2]
        soldier.mover.body = None
        fight = run.match.combat
        pool = self.pool(run, soldier)
        pool.damage(50)
        before = pool.total
        self.step(run, soldier, mech, held=2, facing=-32768)
        state = self.state(mech)
        self.assertTrue(state.active)
        self.assertEqual(int(state.node.field("m_filter.m_27CF71D5")), 2)
        tag = graph.guid(state.node.field("m_filter.m_8B2A4DF3.m_7D7C86A1"))
        self.assertEqual(tag, projectile_tags.EXCLUDED_TAG)
        self.step(run, soldier, mech, buttons=16, held=2, facing=-32768)
        for _ in range(45):
            self.step(run, soldier, mech, held=2, facing=-32768)
            canister = next((rocket for rocket in fight.rockets if rocket.shot is not None
                             and rocket.shot.spawn == projectiles.FIELD_ENTITY), None)
            if canister is not None:
                break
        else:
            self.fail("Field canister did not enter the server projectile path")
        field = canister.shot
        self.assertEqual(canister.definition, 0x040000000000072D)
        self.assertEqual(projectile_tags.tags_of(canister.definition), frozenset({tag}))
        ray = (canister.position, canister.direction, 20)
        contact = matrix.trace(fight, soldier, 0x040000000000072C, *ray)
        self.assertIsNotNone(contact)
        self.assertIs(contact[1].owner, mech)
        self.assertIsNone(matrix.trace(fight, soldier, canister.definition, *ray))
        for _ in range(55):
            self.step(run, soldier, mech, held=2, facing=-32768)
            if field.field_tick is not None and run.frame > field.field_tick:
                break
        else:
            self.fail("Field did not open and begin healing")
        self.assertTrue(self.state(mech).active)
        self.assertAlmostEqual(pool.total, before + 35 * combat.TICK)
        self.assertEqual(self.pool(run, mech).total, 600)
        self.assertFalse(soldier.client.messages(combat.HIT))

    def test_actual_matrix_intercepts_ana_grenade_before_soldier_field(self):
        for held in (0, 2):
            with self.subTest(matrix=bool(held)):
                run, soldier, mech = self.arena()
                fight = run.match.combat
                mech.team = soldier.team
                ana = run.match.add_player(3, "Ana", soldier.hero.guid, 1 - soldier.team, False)
                in_world(run.match, ana)
                run.players.append(ana)
                self.assertTrue(run.match.switch_hero(ana, soldier.hero))
                for player, position in ((ana, [-5, 0, -2]), (soldier, [0, 0, -2])):
                    player.mover.position = position
                    player.mover.body = None
                run.step({})
                rifle_angles = aim(fight._eye(ana), (0, 1, -2))
                for _ in range(45):
                    run.step({ana: (*rifle_angles, 1)})
                    if self.pool(run, soldier).total <= 120:
                        break
                self.assertEqual(self.pool(run, soldier).total, 120)
                self.assertTrue(run.match.switch_hero(ana, heroes()[0x02E000000000013B]))
                ana.mover.position = [0, 0, 12]
                ana.mover.body = None
                run.step({soldier: (0, 0, 16)})
                for _ in range(80):
                    run.step({})
                    if any(s.spawn == projectiles.FIELD_ENTITY and s.field_tick is not None
                           for s in fight.projectiles.shots):
                        break
                else:
                    self.fail("Actual Biotic Field did not deploy")
                for _ in range(4):
                    run.step({mech: (0, 0, held)})
                self.assertEqual(bool(self.state(mech) and self.state(mech).active), bool(held))
                yaw, pitch = aim(fight._eye(ana), (0, .8, -2))
                before, start = self.pool(run, soldier).total, run.frame
                run.step({ana: (yaw, pitch, 16), mech: (0, 0, held)})
                for _ in range(38):
                    run.step({ana: (yaw, pitch, 0), mech: (0, 0, held)})
                self.assertFalse(fight.rockets)
                modifier = fight.heal_modifiers.get(soldier.body)
                if held:
                    self.assertIsNone(modifier)
                    expected = before + (run.frame - start) * 35 * combat.TICK
                else:
                    self.assertIsNotNone(modifier)
                    expected = before + (modifier.anti_from - start - 1) * 35 * combat.TICK - 60
                self.assertAlmostEqual(self.pool(run, soldier).total, expected)
                self.assertEqual(self.pool(run, mech).total == 600, bool(held))

    @unittest.skipUnless(projectile_tags.CATALOG_SHA256, "prepared original003 target tags required")
    def test_matrix_and_grasp_intercept_sleep_dart_before_soldier_field(self):
        for defense in ("idle", "matrix", "release", "grasp"):
            with self.subTest(defense=defense):
                run, soldier, protector = self.arena()
                fight = run.match.combat
                protector.team = soldier.team
                if defense == "grasp":
                    self.assertTrue(run.match.switch_hero(protector, heroes()[defenses.SIGMA]))
                ana = run.match.add_player(3, "Ana", soldier.hero.guid, 1 - soldier.team, False)
                in_world(run.match, ana)
                run.players.append(ana)
                self.assertTrue(run.match.switch_hero(ana, heroes()[defenses.ANA]))
                ana_z = 18 if defense == "release" else 8
                for player, position in (
                    (protector, [0, 0, 0]), (ana, [1, 0, ana_z]), (soldier, [1, 0, -3]),
                ):
                    player.mover.position = position
                    player.mover.body = None
                    player.body_script.ult_seconds = 0
                held = 2 if defense in ("matrix", "release") else 0
                run.step({protector: (0, 0, 8 if defense == "grasp" else held)})
                # The release fixture must cross Matrix's minimum hold first.
                for _ in range(36 if defense == "release" else 4):
                    run.step({protector: (0, 0, held)})
                grasp = defenses.grasp_state(protector)
                self.assertEqual(defenses.matrix_state(protector) is not None, bool(held))
                self.assertEqual(grasp is not None, defense == "grasp")
                protector_hp = self.pool(run, protector).total
                angles = aim(fight._eye(ana), (1, 1.1, -3))
                run.step({ana: (*angles, 8), protector: (0, 0, held)})
                for _ in range(45):
                    if fight.rockets:
                        break
                    run.step({ana: (*angles, 0), protector: (0, 0, held)})
                dart, = fight.rockets
                self.assertEqual(dart.direct.effect, combat.SLEEP_EFFECT)
                self.assertFalse(fight.sleeps)
                if defense == "release":
                    held = 0
                run.step({ana: (*angles, 0), protector: (0, 0, held), soldier: (0, 0, 16)})
                self.assertIsNotNone(defenses.soldier_field_windup(soldier))
                self.assertEqual(defenses.matrix_state(protector) is not None, bool(held))
                if defense == "release":
                    self.assertIn(dart, fight.rockets)
                for _ in range(25):
                    if dart not in fight.rockets:
                        break
                    run.step({ana: (*angles, 0), protector: (0, 0, held)})
                self.assertNotIn(dart, fight.rockets)
                self.assertEqual(self.pool(run, protector).total, protector_hp)
                if grasp is not None:
                    self.assertEqual(grasp.instance.find_var(0, 21312).value(), 5)
                protected = defense in ("matrix", "grasp")
                self.assertEqual(self.pool(run, soldier).total, 200 if protected else 195)
                self.assertEqual(set(fight.sleeps), set() if protected else {soldier.body})
                self.assertEqual(defenses.soldier_field_windup(soldier) is not None, protected)
                deployed = False
                for _ in range(90):
                    run.step({protector: (0, 0, held)})
                    deployed = any(shot.spawn == projectiles.FIELD_ENTITY and shot.field_tick is not None
                                   for shot in fight.projectiles.shots)
                    if deployed:
                        break
                self.assertEqual(deployed, protected)
                if grasp is not None:
                    for _ in range(160):
                        if not grasp.active:
                            break
                        run.step({})
                    self.assertFalse(grasp.active)
                    self.assertAlmostEqual(self.pool(run, protector).bonus_shields, 3)

    def test_release_blocks_early_retry_then_recharges_to_authored_cap(self):
        run, soldier, mech = self.arena()
        instance = self.instance(mech)
        resource = instance.find_var(0, 87)
        cap = instance.find_var(0, 971)
        self.assertEqual(resource.value(), cap.value())
        self.assertEqual(cap.value(), 100)
        for _ in range(54):
            self.step(run, soldier, mech, held=2)
        self.assertTrue(self.state(mech).active)
        drained = resource.value()
        self.assertGreater(drained, 0)
        self.assertLess(drained, cap.value())
        self.step(run, soldier, mech)
        for _ in range(20):
            self.step(run, soldier, mech, held=2)
        self.assertFalse(self.state(mech).active)
        self.assertEqual(resource.value(), drained)
        for _ in range(130):
            self.step(run, soldier, mech)
        self.assertGreater(resource.value(), drained)
        self.assertLess(resource.value(), cap.value())
        for _ in range(80):
            self.step(run, soldier, mech)
        self.assertEqual(resource.value(), cap.value())
        self.step(run, soldier, mech, held=2)
        self.assertTrue(self.state(mech).active)

    def test_wall_precedes_matrix_and_away_matrix_allows_helix_damage(self):
        for wall in (False, True):
            with self.subTest(wall=wall):
                run, soldier, mech = self.arena()
                if wall:
                    run.match.collision_world = World([
                        (-4, -1, 11, 4, -1, 11, 4, 5, 11),
                        (-4, -1, 11, 4, 5, 11, -4, 5, 11),
                    ])
                facing = 0 if wall else -32768
                self.step(run, soldier, mech, held=2, facing=facing)
                self.assertTrue(self.state(mech).active)
                self.step(run, soldier, mech, buttons=2, held=2, facing=facing)
                for _ in range(25):
                    self.step(run, soldier, mech, held=2, facing=facing)
                self.assertEqual(self.pool(run, mech).total, 600 if wall else 490)

    def npc_catalog(self):
        data_dir = os.environ.get("ORPHEUS_PVE_DATA_DIR")
        if not data_dir:
            self.skipTest("prepared five-NPC catalog required")
        catalog = npc.load_catalog(Path(data_dir) / "pve_npcs_174.json")
        self.assertEqual(
            {row.name for row in catalog.values()},
            {"Trooper", "Enforcer", "Sniper", "Assassin", "Heavy Assault"},
        )
        return catalog

    def npc_arena(self, definition, *, incoming=False):
        run, soldier, mech = self.arena()
        mech_position = [1, 0, 3.5] if incoming else [.9, 0, 1]
        for player, position in ((soldier, [0, 0, -3]), (mech, mech_position)):
            player.client = NpcRecordingClient(player)
            player.mover.position = position
            player.mover.body = None
        if incoming:
            mech.team = soldier.team
        actor = npc.spawn(run.match, definition, (0, 0, 6), team=1)
        self.assertIsNotNone(actor.body_script)
        self.assertIn(actor.body, mover._movers())
        return run, soldier, mech, actor

    def test_rifle_helix_and_visor_protect_all_five_npcs_only_with_active_enemy_matrix(self):
        for definition in self.npc_catalog().values():
            for weapon, damage in (("rifle", 20), ("helix", 120), ("visor", 20)):
                for mode in ("active_enemy", "inactive_enemy", "active_ally"):
                    with self.subTest(npc=definition.name, weapon=weapon, mode=mode):
                        run, soldier, mech, actor = self.npc_arena(definition)
                        if mode == "active_ally":
                            mech.team = soldier.team
                        angles = aim(run.match.combat._eye(soldier), (0, .8, 6))
                        if weapon == "visor":
                            script = soldier.body_script
                            script.set_ult_charge(script.component.vars[338].value())
                            run.step({soldier: (*angles, 0x20), mech: (-32768, 0, 0)})
                            for _ in range(80):
                                run.step({soldier: (*angles, 0), mech: (-32768, 0, 0)})
                        held = 0 if mode == "inactive_enemy" else 2
                        run.step({soldier: (*angles, 0), mech: (-32768, 0, held)})
                        if held:
                            self.assertTrue(self.state(mech).active)
                        target = next(t for t in run.match.combat._targets() if t.owner is mech)
                        a, b = target.segment()
                        self.assertIsNone(combat.ray_capsule(
                            run.match.combat._eye(soldier), combat.aim(*angles), a, b, target.radius,
                        ))
                        pool = self.pool(run, actor)
                        before = pool.total
                        run.step({soldier: (*angles, 2 if weapon == "helix" else 1), mech: (-32768, 0, held)})
                        for _ in range(25):
                            run.step({soldier: (*angles, 0), mech: (-32768, 0, held)})
                        protected = mode == "active_enemy"
                        self.assertEqual(before - pool.total, 0 if protected else damage)
                        if protected:
                            self.assertEqual(soldier.stats.value(stats.HERO_DAMAGE), 0)
                            self.assertEqual(self.pool(run, mech).total, 600)

    def test_visor_hits_all_five_npcs_after_matrix_release(self):
        for definition in self.npc_catalog().values():
            with self.subTest(npc=definition.name):
                run, soldier, mech, actor = self.npc_arena(definition)
                angles = aim(run.match.combat._eye(soldier), (0, .8, 6))
                script = soldier.body_script
                script.set_ult_charge(script.component.vars[338].value())
                run.step({soldier: (*angles, 0x20), mech: (-32768, 0, 0)})
                for _ in range(80):
                    run.step({soldier: (*angles, 0), mech: (-32768, 0, 0)})
                run.step({soldier: (*angles, 0), mech: (-32768, 0, 2)})
                self.assertTrue(self.state(mech).active)
                pool = self.pool(run, actor)
                before = pool.total
                ammo = script.component.instances[11].vars[53]
                rounds = ammo.value()
                run.step({soldier: (*angles, 1), mech: (-32768, 0, 2)})
                self.assertEqual(ammo.value(), rounds - 1)
                self.assertEqual(pool.total, before)
                self.assertEqual(soldier.stats.value(stats.HERO_DAMAGE), 0)
                for _ in range(100):
                    run.step({soldier: (*angles, 0), mech: (-32768, 0, 0)})
                    if not self.state(mech).active:
                        break
                self.assertFalse(self.state(mech).active)
                run.step({soldier: (*angles, 1), mech: (-32768, 0, 0)})
                self.assertEqual(ammo.value(), rounds - 2)
                self.assertEqual(pool.total, before - 20)
                self.assertEqual(self.pool(run, mech).total, 600)
                self.assertEqual(soldier.stats.value(stats.HERO_DAMAGE), 20)
                self.assertEqual(script.component.vars[342].value(), 0)

    def test_four_actual_npc_weapons_hit_soldier_only_when_matrix_is_off(self):
        catalog = self.npc_catalog()
        for key, damage in (("1", 20), ("2", 28), ("3", 100), ("3", 5), ("5", 5)):
            for active in (True, False):
                with self.subTest(npc=catalog[key].name, active=active, damage=damage):
                    run, soldier, mech, actor = self.npc_arena(catalog[key], incoming=True)
                    if key == "3" and damage == 5:
                        actor.body_script.component.edges([(16, True), (16, False)])
                    actor.attack_target, actor.firing = soldier.entity, True
                    if key == "3" and damage == 100:
                        for _ in range(20):
                            run.step({soldier: (0, 0, 0), mech: (0, 0, 0)})
                    intercepted = []
                    trace = matrix.trace

                    def observe(fight, shooter, *args, view_tick=None, trace=trace, actor=actor,
                                intercepted=intercepted):
                        found = trace(fight, shooter, *args, view_tick=view_tick)
                        if shooter is actor and found is not None:
                            intercepted.append(found[1].owner)
                        return found

                    with patch.object(matrix, "trace", side_effect=observe):
                        for _ in range(140):
                            run.step({soldier: (0, 0, 0), mech: (0, 0, 2 if active else 0)})
                            if ((intercepted or self.pool(run, soldier).total < 200)
                                    and not any(r.shooter is actor for r in run.match.combat.rockets)):
                                break
                    self.assertEqual(intercepted, [mech] * (25 if key == "2" else 1) if active else [])
                    self.assertEqual(self.pool(run, soldier).total, 200 if active else 200 - damage)
                    self.assertEqual(self.pool(run, mech).total, 600)

    def test_same_npc_attack_order_hits_soldier_after_matrix_resource_exhaustion(self):
        catalog = self.npc_catalog()
        for key, damage in (("1", 20), ("2", 28), ("3", 100), ("3", 5), ("5", 5)):
            with self.subTest(npc=catalog[key].name, damage=damage):
                run, soldier, mech, actor = self.npc_arena(catalog[key], incoming=True)
                charged_sniper = key == "3" and damage == 100
                if key == "3" and not charged_sniper:
                    actor.body_script.component.edges([(16, True), (16, False)])
                actor.attack_target, actor.firing = soldier.entity, True
                if charged_sniper:
                    for _ in range(20):
                        run.step({mech: (0, 0, 0)})
                run.step({mech: (0, 0, 2)})
                state = self.state(mech)
                self.assertTrue(state.active)
                resource = self.instance(mech).find_var(0, 87)
                soldier_pool = self.pool(run, soldier)
                self.assertEqual(soldier_pool.total, 200)
                trace = matrix.trace
                interceptions = []

                def observe(fight, shooter, *args, view_tick=None):
                    hit = trace(fight, shooter, *args, view_tick=view_tick)
                    if shooter is actor and hit is not None:
                        interceptions.append(hit[1].owner)
                    return hit

                depleted = None
                with patch.object(matrix, "trace", side_effect=observe):
                    for _ in range(350):
                        run.step({mech: (0, 0, 2)})
                        if state.active:
                            self.assertEqual(soldier_pool.total, 200)
                        else:
                            if depleted is None:
                                depleted = resource.value()
                            if soldier_pool.total < 200:
                                break
                self.assertFalse(state.active)
                self.assertEqual(depleted, 0)
                self.assertEqual(set(interceptions), {mech})
                self.assertEqual(200 - soldier_pool.total, damage)
                self.assertEqual(self.pool(run, mech).total, 600)
                self.assertTrue(actor.firing)
                self.assertEqual(actor.attack_target, soldier.entity)

    def test_npc_capture_ignores_live_matrix_and_releases_soldier(self):
        catalog = self.npc_catalog()
        for key, contact_damage in (("4", 25), ("5", 0)):
            with self.subTest(npc=catalog[key].name):
                run, soldier, mech, actor = self.npc_arena(catalog[key], incoming=True)
                fight = run.match.combat
                mech.mover.position = [1, 0, -2.4]
                mech.mover.body = None
                actor.position = (0, 0, -1 if key == "4" else 0)
                run.step({mech: (0, 0, 2)})
                self.assertTrue(self.state(mech).active)
                if key == "4":
                    x, y, z = actor.position
                    angles = aim(fight._eye(soldier), (x, y + .8, z))
                    run.step({soldier: (*angles, 0), mech: (0, 0, 2)})
                    self.assertEqual(npc_control.start(run.match, soldier), actor.entity)
                else:
                    actor.yaw = -32768
                    actor.body_script.component.edges([(35, True)])
                for _ in range(65):
                    run.step({mech: (0, 0, 2)})
                    if fight.is_controlled(soldier):
                        break
                self.assertTrue(self.state(mech).active)
                self.assertEqual(fight.pins.get(actor.entity), soldier.body)
                self.assertEqual(self.pool(run, soldier).total, 200 - contact_damage)
                self.assertEqual(self.pool(run, mech).total, 600 - (45 if key == "5" else 0))
                self.assertGreater(npc_control.stop(run.match), 0)
                run.step({mech: (0, 0, 2)})
                self.assertFalse(fight.is_controlled(soldier))
                ammo = soldier.body_script.component.instances[11].vars[53]
                rounds = ammo.value()
                npc_pool = self.pool(run, actor)
                health = npc_pool.total
                x, y, z = actor.position
                angles = aim(fight._eye(soldier), (x, y + .8, z))
                run.step({soldier: (*angles, 1), mech: (0, 0, 2)})
                self.assertEqual(ammo.value(), rounds - 1)
                self.assertEqual(npc_pool.total, health - 20)
