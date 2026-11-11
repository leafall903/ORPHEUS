"""Sombra inputs against the actual 0407 Matrix body and mover."""

import os
import unittest
from contextlib import nullcontext
from dataclasses import replace
from pathlib import Path
from unittest.mock import patch

import test_game_sombra_combat as scenes
import test_server_snapshot as stored
from test_game_combat import KANEZAKA_DM, SOLDIER, Run, aim, in_world
from test_game_mover import wall as solid_wall

from ow174.game import (
    combat,
    dva,
    mover,
    npc,
    npc_control,
    reinhardt_barrier,
    shot_guards,
    sniper_special,
    stats,
    translocators,
    world,
)
from ow174.game.commands import FIRE, Command
from ow174.game.script import graph
from ow174.game.script.driver import ULT_CHARGE, ULT_COST
from ow174.game.script.runtime import send_instance_message
from ow174.game.server_snapshot import snapshot_digest
from ow174.game.world import ARMOUR_POOL, HEALTH_POOL


class SombraMatrixTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        mover.mover_data(shot_guards.MECH)
        table = mover.loaded_mover_table() or {}
        if graph.entity_body(shot_guards.MECH) is None or shot_guards.MECH not in table:
            raise unittest.SkipTest("requires the prepared 0407 graph and mover runtime")

    def arena(self, *, facing=32768, distance=4):
        f = scenes.SombraCombatTests()
        f.setUp()
        mech = replace(scenes.heroes()[shot_guards.DVA], body=shot_guards.MECH)
        self.assertTrue(f.run.match.switch_hero(f.b, mech))
        f.b.mover.position[:] = (0, 0, distance)
        f.b.mover.body = None
        f.a.body_script.ult_seconds = 0
        f.press(0, 100)
        instance = next(i for i in f.b.body_script.component.instances.values() if i.graph.index == 0x02CC)
        self.assertEqual(f.b.mover.data.stand_radius, 0.7)
        self.assertEqual(f.health(f.b), 600)
        self.assertEqual(instance.vars[971].value(), 100)
        for _ in range(5):
            f.run.step({f.b: (facing, 0, 2)})
        self.assertTrue(instance.state(3).active)
        self.assertLess(instance.vars[87].value(), 100)
        return f, instance

    @staticmethod
    def fire(f, facing=32768, matrix=2):
        yaw, pitch = aim(f.run.match.combat._eye(f.a), (0, 1, f.b.mover.position[2]))
        f.run.step({f.a: (yaw, pitch, FIRE), f.b: (facing, 0, matrix)})

    def test_front_consumes_round_and_rear_takes_armour_loss(self):
        for facing, loss in ((32768, 0), (0, 4)):
            with self.subTest(facing=facing):
                f, _ = self.arena(facing=facing)
                f.a.body_script.set_ult_charge(0)
                self.fire(f, facing)
                self.assertEqual(f.health(f.b), 600 - loss)
                self.assertEqual(f.weapon().vars[53].value(), 59)
                self.assertEqual(len(f.a.client.messages(combat.HIT)), int(bool(loss)))
                self.assertEqual(f.a.body_script.component.entity_var(342).value(), loss)

    def test_release_ends_matrix_and_restores_damage(self):
        f, instance = self.arena()
        for _ in range(55):
            f.run.step({f.b: (32768, 0, 0)})
        self.assertFalse(instance.state(3).active)
        self.fire(f, matrix=0)
        self.assertEqual(f.health(f.b), 596)

    def test_matrix_release_and_refill_replay_for_direct_and_owned_mechs(self):
        for owned in (False, True):
            traces = []
            for gap in (False, True):
                with self.subTest(owned=owned, gap=gap):
                    if owned:
                        f, _ = self.pilot_arena()
                        script = f.b.body_script
                        script.set_ult_charge(script.component.entity_var(338).value())
                        f.run.step({f.b: (0, 0, 32)})
                        f.press(0, 120)
                        mech = dva.controlled_mech(f.b)
                        self.assertIsNotNone(mech)
                        entity, controlled = mech.entity, mech.dva_mover
                        matrix = next(i for i in mech.body_script.component.instances.values()
                                      if i.graph.index == 0x02CC)
                        for _ in range(5):
                            f.run.step({f.b: (32768, 0, 2)})
                    else:
                        f, matrix = self.arena()
                        entity, controlled = f.b.body, f.b.mover
                    match = f.run.match
                    fight = match.combat
                    attacker = f.a.body_script
                    attacker.ult_seconds = 0
                    attacker.set_ult_charge(0)
                    self.assertTrue(matrix.state(3).active)
                    initial = matrix.vars[87].value()
                    start = f.run.frame
                    trace = []
                    for offset in (10, 55, 300):
                        at = start + offset
                        if gap:
                            match.update((at + 1) * combat.TICK, at + 1)
                        else:
                            while f.run.frame < at:
                                f.run.step({f.b: (32768, 0, 0)})
                        fuel = matrix.vars[87].value()
                        trace.append(fuel)
                        self.assertEqual(matrix.state(3).active, offset == 10)
                        if offset == 300:
                            self.assertEqual(fuel, 100)
                            continue
                        self.assertGreater(fuel, 0)
                        self.assertLess(fuel, initial)
                        x, y, z = controlled.position
                        yaw, pitch = aim(fight._eye(f.a), (x, y + 1, z))
                        if gap:
                            f.a.take_commands([Command(at + 1, yaw=yaw, pitch=pitch, buttons=FIRE)])
                            match.update((at + 2) * combat.TICK, at + 2)
                            f.a.take_commands([Command(at + 2, yaw=yaw, pitch=pitch)])
                        else:
                            f.run.step({f.a: (yaw, pitch, FIRE), f.b: (32768, 0, 0)})
                        loss = 0 if offset == 10 else 4
                        self.assertEqual(fight.vitals[entity].total, 600 - loss)
                        self.assertEqual(attacker.component.entity_var(ULT_CHARGE).value(), loss)
                    traces.append(trace)
                    last = controlled.last_command
                    f.b.take_commands([replace(last, buttons=2)])
                    self.assertIs(controlled.last_command, last)
                    self.assertFalse(matrix.state(3).active)
                    f.run.frame = start + 300
                    for _ in range(5):
                        f.run.step({f.b: (32768, 0, 2)})
                    self.assertTrue(matrix.state(3).active)
                    self.assertLess(matrix.vars[87].value(), 100)
            self.assertEqual(len(traces), 2)
            self.assertEqual(*traces)

    def test_reflected_zarya_projectiles_are_absorbed_by_the_new_enemy_team(self):
        for absorber_hero in (shot_guards.DVA, shot_guards.SIGMA):
            for button in (2, 32):
                with self.subTest(absorber=absorber_hero, button=button):
                    f = scenes.SombraCombatTests()
                    f.setUp()
                    f.run = Run(KANEZAKA_DM, ("Sombra", "Genji", "Zarya", "Absorber"))
                    f.a, f.b, zarya, absorber = f.run.players
                    match = f.run.match
                    fight = match.combat
                    match.bots.clear()
                    match.game_map = replace(match.game_map, free_for_all=False)
                    rows = (
                        (f.a, scenes.SOMBRA, 0, (0, 0, 4)),
                        (f.b, shot_guards.GENJI, 0, (0, 0, 2)),
                        (zarya, reinhardt_barrier.ZARYA, 1, (0, 0, -5)),
                        (absorber, absorber_hero, 1, (0.9, 0, -4)),
                    )
                    for player, guid, team, position in rows:
                        hero = scenes.heroes()[guid]
                        if guid == shot_guards.DVA:
                            hero = replace(hero, body=shot_guards.MECH)
                        self.assertTrue(match.switch_hero(player, hero))
                        player.team = team
                        player.mover.position[:] = position
                        player.mover.body = None
                    f.geometry()
                    f.press(0, 50)
                    hold = 2 if absorber_hero == shot_guards.DVA else 0
                    f.run.step({f.b: (-32768, 0, 16), absorber: (0, 0, hold or 8)})
                    script = zarya.body_script
                    if button == 32:
                        script.set_ult_charge(script.component.entity_var(338).value())
                    f.run.step({f.b: (-32768, 0, 0), absorber: (0, 0, hold), zarya: (0, 0, button)})
                    rocket, = fight.rockets
                    lease = rocket.shot
                    self.assertIs(rocket.shooter, zarya)
                    for _ in range(45):
                        f.run.step({f.b: (-32768, 0, 0), absorber: (0, 0, hold)})
                        if rocket not in fight.rockets:
                            break
                    self.assertIs(rocket.shooter, f.b)
                    self.assertEqual(rocket.source_body, f.b.body)
                    self.assertNotIn(rocket, fight.rockets)
                    self.assertNotIn(lease, fight.projectiles.shots)
                    self.assertFalse(fight.gravitons.fields)
                    self.assertEqual(f.health(f.a), 200)
                    self.assertEqual(f.health(f.b), 200)
                    self.assertEqual(f.health(zarya), 400)
                    if absorber_hero == shot_guards.SIGMA:
                        self.assertEqual(f.health(absorber), 400)
                        grasp = next(i for i in absorber.body_script.component.instances.values()
                                     if i.graph.index == 0x1D02)
                        self.assertEqual(grasp.vars[21312].value(), 47 if button == 2 else 5)
                    else:
                        self.assertEqual(f.health(absorber), 600)

    def test_actual_matrix_intercepts_zarya_projectiles_before_sombra(self):
        cases = (("front", 2), ("front", 32), ("unleased", 32), ("rear", 32), ("wall", 32), ("ally", 32))
        for case, button in cases:
            with self.subTest(case=case, button=button):
                facing = 0 if case == "rear" else -32768
                f, matrix = self.arena(facing=facing)
                match = f.run.match
                fight = match.combat
                hero = scenes.heroes()[reinhardt_barrier.ZARYA]
                zarya = match.add_player(3, "Zarya", hero.guid, 2, False)
                in_world(match, zarya)
                f.run.players.append(zarya)
                self.assertTrue(match.switch_hero(zarya, hero))
                match.game_map = replace(match.game_map, free_for_all=False)
                f.a.team, f.b.team, zarya.team = 0, int(case == "ally"), 1
                f.b.mover.position[:] = (0, 0, -2)
                zarya.mover.position[:] = (0, 0, -5)
                for player in (f.b, zarya):
                    player.mover.body = None
                if case == "wall":
                    f.a.mover.position[2] = 12
                    f.b.mover.position[2] = 10
                    f.a.mover.body = None
                    f.geometry((-4, 0, -4, 4, 0, -4, 4, 4, -4), (-4, 0, -4, 4, 4, -4, -4, 4, -4))
                for _ in range(35):
                    f.run.step({f.b: (facing, 0, 2)})
                self.assertTrue(matrix.state(3).active)
                script = zarya.body_script
                script.ult_seconds = 0
                if button == 32:
                    script.set_ult_charge(script.component.entity_var(338).value())
                delivery = (patch.object(fight.projectiles, "fired", return_value=None)
                            if case == "unleased" else nullcontext())
                with delivery:
                    f.run.step({f.b: (facing, 0, 2), zarya: (0, 0, button)})
                rocket, = fight.rockets
                self.assertIn(rocket.definition, shot_guards.ZARYA_GUARDED_PROJECTILES)
                if case == "unleased":
                    self.assertIsNone(rocket.shot)
                for _ in range(20):
                    f.run.step({f.b: (facing, 0, 2)})
                self.assertNotIn(rocket, fight.rockets)
                self.assertTrue(matrix.state(3).active)
                if button == 32:
                    self.assertEqual(script.component.entity_var(342).value(), 0)
                if case in ("front", "unleased"):
                    self.assertFalse(fight.gravitons.fields)
                    self.assertNotIn(rocket.shot, fight.projectiles.shots)
                    self.assertEqual(f.health(f.a), 200)
                    self.assertEqual(f.health(f.b), 600)
                else:
                    field, = fight.gravitons.fields
                    self.assertIs(field.shooter, zarya)
                    if case == "wall":
                        self.assertEqual(f.health(f.a), 200)
                        self.assertEqual(f.health(f.b), 600)
                    elif case == "ally":
                        self.assertLess(f.health(f.a), 195)
                        self.assertEqual(f.health(f.b), 600)
                    else:
                        self.assertLess(f.health(f.a), 200)
                        self.assertLess(f.health(f.b), 600)

    def test_held_matrix_ends_on_resource_exhaustion(self):
        f, instance = self.arena()
        for _ in range(160):
            f.run.step({f.b: (32768, 0, 2)})
            if not instance.state(3).active:
                break
        self.assertFalse(instance.state(3).active)
        self.assertLessEqual(instance.vars[87].value(), 1)
        self.fire(f)
        self.assertEqual(f.health(f.b), 596)

    def test_melee_reaches_armour_during_active_matrix(self):
        f, instance = self.arena(distance=1.5)
        f.run.step({f.a: (0, 0, scenes.MELEE), f.b: (32768, 0, 2)})
        for _ in range(30):
            f.run.step({f.b: (32768, 0, 2)})
        self.assertTrue(instance.state(3).active)
        self.assertEqual(f.health(f.b), 575)

    def test_lethal_melee_ends_matrix_and_exposes_rear_target(self):
        f, instance = self.arena(distance=1.5)
        match = f.run.match
        fight = match.combat
        rear = match.add_player(3, "Rear", SOLDIER, 2, False)
        in_world(match, rear)
        f.run.players.append(rear)
        self.assertTrue(match.switch_hero(rear, scenes.heroes()[SOLDIER]))
        rear.mover.position[:] = (0, 0, 4)
        rear.mover.body = None
        for _ in range(35):
            f.run.step({f.b: (32768, 0, 2)})
        self.assertTrue(instance.state(3).active)
        self.fire(f)
        self.assertEqual(f.health(rear), 200)
        self.assertFalse(f.a.client.messages(combat.HIT))

        vitals = fight.vitals[f.b.body]
        vitals.now[ARMOUR_POOL] = 0
        vitals.now[HEALTH_POOL] = 30
        f.run.step({f.a: (0, 0, scenes.MELEE), f.b: (32768, 0, 2)})
        for _ in range(30):
            f.run.step({f.b: (32768, 0, 2)})
            if fight.is_dead(f.b):
                break
        self.assertTrue(fight.is_dead(f.b))
        self.assertEqual(vitals.killer, f.a.body)
        self.assertIsNone(shot_guards.active_guard(f.b, fight.tick * combat.TICK * 1000))
        for _ in range(35):
            f.run.step({f.b: (32768, 0, 2)})
        f.press(FIRE, target=rear)
        self.assertEqual(f.health(rear), 192)
        self.assertEqual(len(f.a.client.messages(combat.HIT)), 2)

    def test_replacing_active_mech_with_pilot_retires_matrix_and_restores_damage(self):
        f, instance = self.arena()
        match = f.run.match
        fight = match.combat
        self.fire(f)
        self.assertEqual(f.health(f.b), 600)
        self.assertFalse(f.a.client.messages(combat.HIT))
        self.assertTrue(instance.state(3).active)

        old_body = f.b.body
        old_script = f.b.body_script
        pilot = scenes.heroes()[shot_guards.DVA]
        self.assertEqual(pilot.body, 0x040000000000040A)
        self.assertTrue(match.switch_hero(f.b, pilot))
        self.assertNotEqual(f.b.body, old_body)
        self.assertTrue(old_script.retired)
        f.b.mover.position[:] = (0, 0, 4)
        f.b.mover.body = None
        for _ in range(45):
            f.run.step({f.b: (32768, 0, 2)})
        self.assertIsNone(shot_guards.active_guard(f.b, fight.tick * combat.TICK * 1000))
        self.assertNotIn(old_body, {target.entity for target in fight._targets()})
        self.assertEqual(f.health(f.b), 150)
        self.fire(f)
        self.assertEqual(f.health(f.b), 142)
        hit, = f.a.client.messages(combat.HIT)
        self.assertEqual(hit["+0x78"]["+0x0"]["+0x0"], f.b.body)

        weapon = next(i for i in f.b.body_script.component.instances.values() if i.graph.index == 0x02B8)
        self.assertEqual(weapon.vars[53].value(), 20)
        f.press(FIRE, shooter=f.b, target=f.a)
        f.press(0, 25)
        self.assertEqual(weapon.vars[53].value(), 19)
        self.assertEqual(f.health(f.a), 186)

    def pilot_arena(self):
        f = scenes.SombraCombatTests()
        f.setUp()
        for player in (f.a, f.b):
            player.client.queue_bot_live = lambda *args: None
        self.assertTrue(f.run.match.switch_hero(f.b, scenes.heroes()[shot_guards.DVA]))
        f.b.mover.position[:] = (0, 0, 4)
        f.b.mover.body = None
        f.press(0, 50)
        weapon = next(i for i in f.b.body_script.component.instances.values() if i.graph.index == 0x02B8)
        return f, weapon

    def test_pilot_light_gun_interrupts_camo_only_on_enemy_contact(self):
        f, weapon = self.pilot_arena()
        match = f.run.match
        f.press(8)
        f.press(0, 60)
        camo = f.camo().state(13)
        self.assertTrue(camo.flags & camo.RUNNING)

        match.game_map = replace(match.game_map, free_for_all=False)
        f.a.team = 0
        for index, (wall, hostile) in enumerate(((False, False), (True, True), (False, True)), 1):
            with self.subTest(wall=wall, hostile=hostile):
                f.b.team = int(hostile)
                if wall:
                    f.wall()
                else:
                    f.geometry()
                f.press(FIRE, shooter=f.b, target=f.a)
                self.assertEqual(weapon.vars[53].value(), 20 - index)
                self.assertEqual(f.health(f.a), 200)
                f.press(0, 65)
                landed = hostile and not wall
                self.assertEqual(f.health(f.a), 186 if landed else 200)
                self.assertEqual(bool(camo.flags & camo.RUNNING), not landed)
        self.assertGreater(camo.remaining(), 0)

    def test_pilot_light_gun_interrupts_hack_and_fires_during_silence(self):
        for button in (2, 0x20):
            with self.subTest(button=button):
                f, weapon = self.pilot_arena()
                fight = f.run.match.combat
                f.press(2, 12)
                hack = f.weapon().state(53)
                self.assertTrue(hack.flags & hack.RUNNING)
                target = aim(fight._eye(f.a), (0, 1, 4))
                shot = aim(fight._eye(f.b), (0, 1, 0))
                f.run.step({f.a: (*target, 2), f.b: (*shot, FIRE)})
                self.assertEqual(weapon.vars[53].value(), 19)
                self.assertEqual(f.health(f.a), 200)
                f.press(2, 30)
                self.assertEqual(f.health(f.a), 186)
                self.assertFalse(hack.flags & hack.RUNNING)
                self.assertGreater(hack.remaining(), 0)
                self.assertNotIn(f.b.body, fight.hacks)

                f.press(0, 130)
                if button == 0x20:
                    f.a.body_script.set_ult_charge(1400)
                    f.press(button)
                f.press(2 if button == 2 else 0, 90)
                effect, = fight.hacks[f.b.body]
                self.assertEqual(effect.application.source_entity(), f.a.body)
                f.press(FIRE, shooter=f.b, target=f.a)
                f.press(0, 30)
                self.assertEqual(weapon.vars[53].value(), 18)
                self.assertEqual(f.health(f.a), 172)
                self.assertIs(fight.hacks[f.b.body][0], effect)

    def test_call_mech_placement_checks_sombra_body_at_landing_point(self):
        for blocked in (False, True):
            with self.subTest(blocked=blocked):
                f, _ = self.pilot_arena()
                f.a.mover.position[:] = (0 if blocked else 3, 0, 5.5)
                f.a.mover.body = None
                f.press(0, 5)
                script = f.b.body_script
                script.set_ult_charge(script.component.entity_var(338).value())
                results = []
                probe = dva.placement_probe

                def observe(world, state, probe=probe, results=results):
                    result = probe(world, state)
                    results.append(result)
                    return result

                with patch.object(dva, "placement_probe", side_effect=observe):
                    f.run.step({f.b: (0, 0, 0x20)})
                    f.press(0, 50)
                result, = results
                self.assertEqual(result[3], "blocked-body" if blocked else "clear")
                cast = next(i for i in script.component.instances.values() if i.graph.index == 0x026A)
                point = cast.vars[1626].value()
                if blocked:
                    self.assertIsNone(point)
                    self.assertFalse(f.run.match.bots)
                else:
                    self.assertAlmostEqual(point.x, 0)
                    self.assertAlmostEqual(point.y, 0)
                    self.assertAlmostEqual(point.z, 5.5)
                    mech, = f.run.match.bots
                    self.assertEqual(mech.body, shot_guards.MECH)
                    self.assertEqual(mech.spawner["dva_source_body"], f.b.body)
                    self.assertEqual(cast.vars[38].value().id, mech.entity)
                    fight = f.run.match.combat
                    self.assertEqual(fight.vitals[mech.entity].total, 600)
                    angles = aim(fight._eye(f.a), (point.x, point.y + 1, point.z))
                    f.run.step({f.a: (*angles, FIRE)})
                    self.assertEqual(fight.vitals[mech.entity].total, 596)

    def test_call_mech_runs_both_authored_entry_moves(self):
        f, _ = self.pilot_arena()
        script = f.b.body_script
        start = tuple(f.b.mover.position)
        script.set_ult_charge(script.component.entity_var(338).value())
        f.run.step({f.b: (0, 0, 0x20)})
        f.press(0, 120)
        cast = next(i for i in script.component.instances.values() if i.graph.index == 0x026A)
        moves = {state.node.pos: state for state in cast.states.values()}
        for pos in (85, 200):
            self.assertEqual(moves[pos].counter, 1)
            self.assertFalse(moves[pos].active)
        destination = moves[200].evaluate(moves[200].node.field("m_865F3D01"), watch=False)
        self.assertGreater(destination.z, start[2])
        for actual, expected in zip(f.b.mover.position, destination, strict=True):
            self.assertAlmostEqual(actual, expected, delta=0.002)
        mech, = f.run.match.bots
        self.assertIs(dva.controlled_mech(f.b), mech)
        fight = f.run.match.combat
        self.assertNotIn(f.b.body, {target.entity for target in fight._targets()})
        parked = tuple(f.b.mover.position)
        start_z = mech.position[2]
        for _ in range(30):
            f.run.frame += 1
            f.run.match.update(f.run.frame * combat.TICK, f.run.frame)
            f.b.take_commands([Command(f.run.frame, forward=127)])
        self.assertEqual(tuple(f.b.mover.position), parked)
        self.assertGreater(mech.position[2], start_z + 0.3)
        x, y, z = mech.position
        angles = aim(fight._eye(f.a), (x, y + 1, z))
        f.run.step({f.a: (*angles, FIRE)})
        self.assertEqual(fight.vitals[mech.entity].total, 596)

    def test_controlled_fusion_cannons_hit_sombra_and_stop_on_release(self):
        f, _ = self.pilot_arena()
        script = f.b.body_script
        script.set_ult_charge(script.component.entity_var(338).value())
        f.run.step({f.b: (0, 0, 0x20)})
        f.press(0, 120)
        mech = dva.controlled_mech(f.b)
        self.assertIsNotNone(mech)
        x, y, z = mech.position
        f.a.mover.position[:] = (x, y, z + 2)
        f.a.mover.body = None
        facing = aim((x, y + combat.EYE, z), (x, y + 1.05, z + 2))
        for _ in range(40):
            f.run.step({f.b: (*facing, 0)})
        for _ in range(10):
            f.run.step({f.b: (*facing, FIRE)})
        after_hit = f.health(f.a)
        self.assertLess(after_hit, 200)
        for _ in range(25):
            f.run.step({f.b: (*facing, 0)})
        self.assertEqual(f.health(f.a), after_hit)
        self.assertAlmostEqual(f.b.stats.value(stats.HERO_DAMAGE), 200 - after_hit)
        self.assertTrue(f.b.client.messages(combat.HIT))

        fight = f.run.match.combat
        vitals = fight.vitals[f.a.body]
        vitals.now[HEALTH_POOL] = 1
        for _ in range(20):
            f.run.step({f.b: (*facing, FIRE)})
            if fight.is_dead(f.a):
                break
        self.assertTrue(fight.is_dead(f.a))
        self.assertEqual(vitals.killer, mech.entity)
        self.assertEqual(f.b.stats.value(stats.ELIMINATIONS), 1)
        self.assertAlmostEqual(f.b.stats.value(stats.HERO_DAMAGE), 201 - after_hit)
        kill, = f.b.client.messages(combat.KILL_NOTICE)
        self.assertEqual(kill["+0x80"]["+0x0"], mech.entity)
        death, = f.a.client.messages(combat.DEATH_NOTICE)
        self.assertEqual(death["+0x7C"]["+0x0"], mech.entity)

    def test_controlled_fusion_cannons_interrupt_hack_only_if_damage_reaches_sombra(self):
        for covered in (False, True):
            with self.subTest(covered=covered):
                f, _ = self.pilot_arena()
                fight = f.run.match.combat
                script = f.b.body_script
                script.set_ult_charge(script.component.entity_var(338).value())
                f.run.step({f.b: (0, 0, 0x20)})
                f.press(0, 120)
                mech = dva.controlled_mech(f.b)
                self.assertIsNotNone(mech)
                x, y, z = mech.position
                f.a.mover.position[:] = (x, y, z + 2)
                f.a.mover.body = None
                f.press(0, 40)
                target = aim(fight._eye(f.a), (x, y + 1, z))
                facing = aim((x, y + combat.EYE, z), (x, y + 1.05, z + 2))
                for _ in range(12):
                    f.run.step({f.a: (*target, 2), f.b: (*facing, 0)})
                hack = f.weapon().state(53)
                self.assertTrue(hack.flags & hack.RUNNING)
                if covered:
                    f.geometry(*solid_wall(x - 2, z + 1, x + 2, z + 1))
                with patch.object(fight, "_shoot", wraps=fight._shoot) as shoot:
                    for _ in range(10):
                        f.run.step({f.a: (*target, 2), f.b: (*facing, FIRE)})
                        if shoot.call_count:
                            break
                self.assertTrue(shoot.call_count)
                self.assertEqual(f.health(f.a) == 200, covered)
                f.geometry()
                for _ in range(30):
                    f.run.step({f.a: (*target, 2), f.b: (*facing, 0)})
                self.assertEqual(bool(hack.flags & hack.RUNNING), covered)
                if not covered:
                    self.assertGreater(hack.remaining(), 0)
                self.assertNotIn(mech.entity, fight.hacks)

    def test_owned_matrix_absorbs_pistol_until_release_hack_or_emp(self):
        for button in (0, 2, 0x20):
            with self.subTest(button=button):
                f, _ = self.pilot_arena()
                fight = f.run.match.combat
                script = f.b.body_script
                script.set_ult_charge(script.component.entity_var(338).value())
                f.run.step({f.b: (0, 0, 0x20)})
                f.press(0, 120)
                mech = dva.controlled_mech(f.b)
                self.assertIsNotNone(mech)
                instances = mech.body_script.component.instances.values()
                matrix = next(i for i in instances if i.graph.index == 0x02CC)
                x, y, z = mech.position
                angles = aim(fight._eye(f.a), (x, y + 1, z))
                for _ in range(5):
                    f.run.step({f.b: (32768, 0, 2)})
                self.assertTrue(matrix.state(3).active)
                self.assertLess(matrix.vars[87].value(), 100)
                f.run.step({f.a: (*angles, FIRE), f.b: (32768, 0, 2)})
                self.assertEqual(f.weapon().vars[53].value(), 59)
                self.assertEqual(fight.vitals[mech.entity].total, 600)
                self.assertFalse(f.a.client.messages(combat.HIT))

                if button == 0x20:
                    f.a.body_script.set_ult_charge(1400)
                held = 2 if button else 0
                for frame in range(90 if button == 2 else 55):
                    attack = button if button == 2 or frame == 0 else 0
                    f.run.step({f.a: (*angles, attack), f.b: (32768, 0, held)})
                    if button == 2 and mech.entity in fight.hacks:
                        break
                self.assertEqual(mech.entity in fight.hacks, bool(button))
                self.assertFalse(matrix.state(3).active)
                f.press(0, 50)
                f.run.step({f.a: (*angles, FIRE), f.b: (32768, 0, held)})
                self.assertEqual(f.weapon().vars[53].value(), 58)
                self.assertEqual(fight.vitals[mech.entity].total, 596)
                if button:
                    effect, = fight.hacks[mech.entity]
                    self.assertEqual(effect.application.source_entity(), f.a.body)
                    f.a.mover.position[:] = (x, y, z - 2)
                    f.a.mover.body = None
                    facing = aim((x, y + combat.EYE, z), (x, y + 1.05, z - 2))
                    for _ in range(12):
                        f.run.step({f.b: (*facing, FIRE)})
                    self.assertLess(f.health(f.a), 200)
                    self.assertIs(fight.hacks[mech.entity][0], effect)

                    for _ in range(5):
                        f.run.step({f.b: (*facing, 2)})
                    self.assertFalse(matrix.state(3).active)
                    f.press(0, effect.until_tick - f.run.frame + 2)
                    self.assertNotIn(mech.entity, fight.hacks)
                    self.assertNotIn(effect.application.id, mech.body_script.component.instances)
                    self.assertFalse(mech.body_script.component.entity_var(888).value())
                    self.assertFalse(matrix.state(3).active)
                    for _ in range(5):
                        f.run.step({f.b: (*facing, 2)})
                    self.assertTrue(matrix.state(3).active)
                    angles = aim(fight._eye(f.a), (x, y + 1, z))
                    f.run.step({f.a: (*angles, FIRE), f.b: (*facing, 2)})
                    self.assertEqual(f.weapon().vars[53].value(), 57)
                    self.assertEqual(fight.vitals[mech.entity].total, 596)

    def test_boosters_contact_cancel_and_emp_order_for_actual_mechs(self):
        cases = (
            (False, "open"), (False, "gap"), (True, "open"), (True, "gap"),
            (True, "wall"), (True, "ally"), (True, "cancel"), (True, "early"), (True, "late"),
        )
        contiguous = {}
        for owned, mode in cases:
            with self.subTest(owned=owned, mode=mode):
                if owned:
                    f, _ = self.pilot_arena()
                    script = f.b.body_script
                    script.set_ult_charge(script.component.entity_var(ULT_COST).value())
                    f.run.step({f.b: (0, 0, 32)})
                    f.press(0, 120)
                    actor = dva.controlled_mech(f.b)
                    self.assertIsNotNone(actor)
                    drive, entity = actor.dva_mover, actor.entity
                else:
                    f, _ = self.arena()
                    f.press(0, 200)
                    actor = f.b
                    drive, entity = actor.mover, actor.body
                match = f.run.match
                fight = match.combat
                if mode == "wall":
                    f.wall()
                if mode == "ally":
                    match.game_map = replace(match.game_map, free_for_all=False)
                    f.a.team = actor.team
                if mode == "cancel":
                    f.a.mover.teleport_grounded((0., 0., -9.))
                x, y, z = drive.position
                reply = aim(fight._eye(f.a), (x, y + 1, z))
                if mode in ("early", "late"):
                    attacker = f.a.body_script
                    attacker.set_ult_charge(attacker.component.entity_var(ULT_COST).value())
                if mode == "early":
                    f.run.step({f.a: (*reply, 32)})
                    f.press(0, 17)
                flight = next(i for i in actor.body_script.component.instances.values()
                              if i.graph.index == dva.BOOSTERS)
                ability = flight.states[flight.graph.node(4).state]
                f.run.step({f.a: (*reply, 32 if mode == "late" else 0), f.b: (32768, 0, 8)})
                self.assertTrue(ability.flags & ability.RUNNING)
                if mode == "gap":
                    end = f.run.frame + 81
                    match.update(end * combat.TICK, end)
                else:
                    for frame in range(80):
                        f.run.step({f.b: (32768, 0, 8 if mode == "cancel" and frame == 29 else 0)})
                hit = mode in ("open", "gap", "late")
                self.assertEqual(f.health(f.a), 190 if hit else 200)
                self.assertEqual(f.a.mover.position[2] < -9, hit)
                self.assertEqual(entity in fight.hacks, mode in ("early", "late"))
                if mode == "open":
                    self.assertGreater(z - drive.position[2], 15)
                    contiguous[owned] = tuple(drive.position)
                if mode == "gap":
                    self.assertEqual(tuple(drive.position), contiguous[owned])
                if mode == "wall":
                    self.assertGreater(drive.position[2], 0.75)
                if mode in ("early", "late", "cancel"):
                    self.assertFalse(ability.flags & ability.RUNNING)
                    self.assertGreater(ability.remaining(), 0)
                    self.assertLess(drive.position[2], z)
                    stopped = tuple(drive.position)
                    f.run.step({f.b: (32768, 0, 8)})
                    self.assertFalse(ability.flags & ability.RUNNING)
                    self.assertEqual(tuple(drive.position), stopped)

    def test_owned_micro_missiles_resolve_cover_hack_and_paid_emp_order(self):
        cases = (
            ("open", 18, 74), ("wall", 18, 200), ("hack", 0, 200),
            ("emp", 2, 186), ("lethal", 18, 0),
        )
        for mode, rounds, health in cases:
            with self.subTest(mode=mode):
                f, _ = self.pilot_arena()
                fight = f.run.match.combat
                pilot = f.b.body_script
                pilot.set_ult_charge(pilot.component.entity_var(ULT_COST).value())
                f.run.step({f.b: (0, 0, 32)})
                f.press(0, 120)
                mech = dva.controlled_mech(f.b)
                self.assertIsNotNone(mech)
                x, y, z = mech.position
                tx, ty, tz = f.a.mover.position
                launch = aim((x, y + 1.3, z), (tx, ty + 1, tz))
                reply = aim(fight._eye(f.a), (x, y + 1, z))
                volley = next(i for i in mech.body_script.component.instances.values()
                              if i.graph.index == dva.MICRO_MISSILES)
                ability = volley.states[volley.graph.node(3).state]
                attacker = f.a.body_script
                attacker.ult_seconds = 0
                if mode == "wall":
                    f.wall()
                if mode == "hack":
                    for _ in range(100):
                        f.run.step({f.a: (*reply, 2)})
                        if mech.entity in fight.hacks:
                            break
                    self.assertIn(mech.entity, fight.hacks)
                if mode in ("emp", "lethal"):
                    attacker.set_ult_charge(attacker.component.entity_var(ULT_COST).value())
                if mode == "lethal":
                    fight.vitals[f.a.body].now[HEALTH_POOL] = 7
                initial = f.health(f.a)
                released = {}
                first_hit = first_silence = None
                for frame in range(150):
                    emp = 32 if (mode, frame) in (("emp", 0), ("lethal", 15)) else 0
                    f.run.step({f.a: (*reply, emp), f.b: (*launch, 16 if frame == 0 else 0)})
                    for rocket in fight.rockets:
                        self.assertIs(rocket.shooter, mech)
                        released[id(rocket)] = rocket
                    if first_hit is None and f.health(f.a) < initial:
                        first_hit = frame
                    if first_silence is None and mech.entity in fight.hacks:
                        first_silence = frame
                self.assertEqual(len(released), rounds)
                self.assertEqual(f.health(f.a), health)
                self.assertEqual(ability.remaining() > 0, mode != "hack")
                if mode == "emp":
                    self.assertIsNotNone(first_silence)
                    self.assertIsNotNone(first_hit)
                    self.assertLess(first_silence, first_hit)
                    self.assertEqual(fight.hacks[mech.entity][0].application.source_entity(), f.a.body)
                if mode == "lethal":
                    self.assertTrue(fight.is_dead(f.a))
                    self.assertIsNone(first_silence)
                if mode in ("emp", "lethal"):
                    self.assertEqual(attacker.component.entity_var(ULT_CHARGE).value(), 0)

    def test_micro_missiles_finish_and_cool_during_omitted_mech_inputs(self):
        for owned in (False, True):
            for emp in (False, True):
                trace = []
                for gap in (False, True):
                    with self.subTest(owned=owned, emp=emp, gap=gap):
                        if owned:
                            f, _ = self.pilot_arena()
                            pilot = f.b.body_script
                            pilot.set_ult_charge(pilot.component.entity_var(ULT_COST).value())
                            f.run.step({f.b: (0, 0, 32)})
                            f.press(0, 120)
                            mech = dva.controlled_mech(f.b)
                            self.assertIsNotNone(mech)
                            script, drive, entity = mech.body_script, mech.dva_mover, mech.entity
                        else:
                            f, _ = self.arena(distance=5.5)
                            f.press(0, 200)
                            script, drive, entity = f.b.body_script, f.b.mover, f.b.body
                        match = f.run.match
                        fight = match.combat
                        x, y, z = drive.position
                        launch = aim((x, y + 1.3, z), (0, 1, 0))
                        reply = aim(fight._eye(f.a), (x, y + 1, z))
                        volley = next(i for i in script.component.instances.values()
                                      if i.graph.index == dva.MICRO_MISSILES)
                        ability = volley.states[volley.graph.node(3).state]
                        if emp:
                            attacker = f.a.body_script
                            attacker.ult_seconds = 0
                            attacker.set_ult_charge(attacker.component.entity_var(ULT_COST).value())
                        f.run.step({f.a: (*reply, 32 if emp else 0), f.b: (*launch, 16)})
                        self.assertTrue(ability.flags & ability.RUNNING)
                        end = f.run.frame + (150 if emp else 650)
                        if gap:
                            match.update((end + 1) * combat.TICK, end + 1)
                        else:
                            while f.run.frame < end:
                                f.run.step({f.b: (*launch, 0)})
                        expected = (186 if owned else 193) if emp else 74
                        self.assertEqual(f.health(f.a), expected)
                        self.assertFalse(fight.rockets)
                        self.assertFalse(ability.flags & ability.RUNNING)
                        trace.append(ability.remaining())
                        if emp:
                            self.assertGreater(ability.remaining(), 0)
                            effect, = fight.hacks[entity]
                            self.assertEqual(effect.application.source_entity(), f.a.body)
                        else:
                            self.assertEqual(ability.remaining(), 0)
                            f.run.frame = end
                            f.run.step({f.b: (*launch, 16)})
                            self.assertTrue(ability.flags & ability.RUNNING)
                self.assertEqual(len(trace), 2)
                self.assertEqual(*trace)

    def test_launched_micro_missile_preserves_source_after_mech_death_or_direct_replacement(self):
        for mode in ("direct_death", "called_death", "direct_switch"):
            with self.subTest(mode=mode):
                self.check_launched_micro_missile_source(mode)

    def check_launched_micro_missile_source(self, mode):
        if mode == "called_death":
            f, _ = self.pilot_arena()
            for player in (f.a, f.b):
                player.client.queue_bot_death = lambda *args: None
                player.client.queue_bot_death_update = lambda *args: None
            pilot = f.b.body_script
            pilot.set_ult_charge(pilot.component.entity_var(ULT_COST).value())
            f.run.step({f.b: (0, 0, 32)})
            f.press(0, 120)
            mech = dva.controlled_mech(f.b)
            self.assertIsNotNone(mech)
            source, drive = mech.entity, mech.dva_mover
        else:
            f, _ = self.arena(distance=5.5)
            f.press(0, 200)
            source, drive = f.b.body, f.b.mover
        match = f.run.match
        fight = match.combat
        f.health(f.a)
        vitals = fight.vitals[f.a.body]
        vitals.now[HEALTH_POOL] = 7
        x, y, z = drive.position
        launch = aim((x, y + 1.3, z), (0, 1, 0))
        f.run.step({f.b: (*launch, 16)})
        cast = f.run.frame
        for _ in range(20):
            f.run.step({f.b: (*launch, 0)})
            if fight.rockets:
                break
        missile, = fight.rockets
        self.assertEqual(missile.definition, dva.MICRO_MISSILE_ENTITY)
        self.assertEqual(missile.source_body, source)
        if mode == "direct_switch":
            self.assertTrue(match.switch_hero(f.b, scenes.heroes()[scenes.SOMBRA]))
            self.assertNotEqual(f.b.body, source)
        else:
            source_target = next(target for target in fight._targets() if target.entity == source)
            source_vitals = fight.vitals_of(source_target)
            source_vitals.now[HEALTH_POOL], source_vitals.now[ARMOUR_POOL] = 8, 0
            reply = aim(fight._eye(f.a), (x, y + 1, z))
            f.run.step({f.a: (*reply, FIRE), f.b: (*launch, 0)})
            self.assertTrue(source_vitals.dead)
            self.assertEqual(source_vitals.killer, f.a.body)
            self.assertEqual(fight.rockets, [missile])
        end = cast + 81
        match.update(end * combat.TICK, end)
        self.assertEqual(vitals.total, 0)
        self.assertEqual(vitals.killer, source)
        self.assertEqual(vitals.death_tick, cast + 23)
        self.assertFalse(fight.rockets)

    def test_owned_matrix_intercepts_zarya_projectiles_before_sombra(self):
        for button in (2, 32):
            for matrix_held in (False, True):
                with self.subTest(button=button, matrix_held=matrix_held):
                    f, _ = self.pilot_arena()
                    match = f.run.match
                    fight = match.combat
                    match.game_map = replace(match.game_map, free_for_all=False)
                    f.a.team = f.b.team = 0
                    pilot = f.b.body_script
                    pilot.set_ult_charge(pilot.component.entity_var(338).value())
                    f.run.step({f.b: (0, 0, 0x20)})
                    f.press(0, 120)
                    mech = dva.controlled_mech(f.b)
                    self.assertIsNotNone(mech)
                    x, y, z = mech.position
                    hero = scenes.heroes()[reinhardt_barrier.ZARYA]
                    zarya = match.add_player(3, "Zarya", hero.guid, 2, False)
                    in_world(match, zarya)
                    f.run.players.append(zarya)
                    self.assertTrue(match.switch_hero(zarya, hero))
                    zarya.team = 1
                    zarya.mover.position[:] = (x, y, z - 3)
                    f.a.mover.position[:] = (x, y, z + 2)
                    for player in (f.a, zarya):
                        player.mover.body = None
                        player.client.queue_bot_live = lambda *args: None
                    held = 2 if matrix_held else 0
                    for _ in range(40):
                        f.run.step({f.b: (32768, 0, held)})
                    self.assertEqual(shot_guards.active_guard(mech, fight.tick * combat.TICK * 1000)
                                     is not None, matrix_held)
                    if button == 32:
                        script = zarya.body_script
                        script.set_ult_charge(script.component.entity_var(338).value())
                    f.run.step({f.b: (32768, 0, held), zarya: (0, 0, button)})
                    rocket, = fight.rockets
                    self.assertIn(rocket.definition, shot_guards.ZARYA_GUARDED_PROJECTILES)
                    for _ in range(20):
                        f.run.step({f.b: (32768, 0, held)})
                    self.assertNotIn(rocket, fight.rockets)
                    if matrix_held:
                        self.assertFalse(fight.gravitons.fields)
                        self.assertEqual(fight.vitals[mech.entity].total, 600)
                        self.assertEqual(f.health(f.a), 200)
                    else:
                        self.assertLess(fight.vitals[mech.entity].total, 600)
                        self.assertEqual(bool(fight.gravitons.fields), button == 32)
                        if button == 32:
                            self.check_owned_mech_graviton_pull(f, zarya, mech)

    def test_actual_npc_weapons_absorbed_by_owned_matrix_until_release_or_silence(self):
        runtime_dir = os.environ.get("ORPHEUS_NPC_RUNTIME_DIR")
        if not runtime_dir:
            self.skipTest("requires the actual NPC catalog")
        catalog = npc.load_catalog(Path(runtime_dir) / "data" / "pve_npcs_174.json")
        attacks = (("1", None, 15), ("2", 0x04000000000023F0, 1),
                   ("3", 0x0400000000001E80, 95), ("5", None, 2.5))
        for key, definition, loss in attacks:
            for interruption in (None, "release", "Hack", "EMP"):
                with self.subTest(npc=catalog[key].name, interruption=interruption):
                    silenced = interruption in ("Hack", "EMP")
                    blocked = interruption is None
                    f, _ = self.pilot_arena()
                    for player in f.run.players:
                        player.client.bot_stream = lambda body: None
                    match = f.run.match
                    fight = match.combat
                    match.game_map = replace(match.game_map, free_for_all=silenced)
                    f.a.team = f.b.team = 0
                    pilot = f.b.body_script
                    pilot.set_ult_charge(pilot.component.entity_var(ULT_COST).value())
                    f.run.step({f.b: (0, 0, 32)})
                    f.press(0, 120)
                    mech = dva.controlled_mech(f.b)
                    self.assertIsNotNone(mech)
                    x, y, z = mech.position
                    self.assertIn(catalog[key].body, mover.loaded_mover_table())
                    bot = npc.spawn(match, catalog[key], (x, y, z - 5), team=1)
                    bot.body_script.component.set(9948, 0)
                    f.a.mover.position[:] = (x + 2, y, z - 1)
                    f.a.mover.body = None
                    self.assertTrue(f.a.has_body)
                    angles = aim(fight._eye(f.a), (x, y + 1, z - 5))
                    for _ in range(75):
                        f.run.step({f.a: (*angles, 0), f.b: (32768, 0, 0)})
                    if key == "3":
                        special = next(i for i in bot.body_script.component.instances.values()
                                       if i.graph.index == 0x142C)
                        sent = send_instance_message(special, 0x0240000000000A45, {}, bot.entity)
                        self.assertEqual(sent, 1)
                    if interruption is not None:
                        f.run.step({f.a: (*angles, 0), f.b: (32768, 0, 2)})
                        self.assertIsNotNone(shot_guards.active_guard(mech, fight.tick * combat.TICK * 1000))
                        if silenced:
                            mech_angles = aim(fight._eye(f.a), (x, y + 1, z))
                            button = 2 if interruption == "Hack" else 32
                            if button == 32:
                                f.a.body_script.set_ult_charge(1400)
                            for frame in range(90):
                                attack = button if button == 2 or frame == 0 else 0
                                f.run.step({f.a: (*mech_angles, attack), f.b: (32768, 0, 2)})
                                if mech.entity in fight.hacks:
                                    break
                            effect, = fight.hacks[mech.entity]
                            self.assertEqual(effect.application.source_entity(), f.a.body)
                        else:
                            for _ in range(35):
                                f.run.step({f.a: (*angles, 0), f.b: (32768, 0, 0)})
                        self.assertIsNone(shot_guards.active_guard(mech, fight.tick * combat.TICK * 1000))
                    if silenced:
                        f.run.step({f.a: (*angles, 0), f.b: (32768, 0, 0)})
                    self.assertEqual(npc_control.start(match, f.a), bot.entity)
                    f.a.mover.position[:] = (x, y, z + 2)
                    f.a.mover.body = None

                    if key == "3":
                        # Sniper windup outlasts a full early Matrix hold.
                        f.press(0, 72)
                        self.assertFalse(fight.rockets)

                    held = 2 if blocked or silenced else 0
                    if blocked:
                        f.run.step({f.b: (32768, 0, held)})
                        self.assertIsNotNone(shot_guards.active_guard(mech, fight.tick * combat.TICK * 1000))
                    with patch.object(fight, "_shoot", wraps=fight._shoot) as shoot:
                        for _ in range(170):
                            f.run.step({f.b: (32768, 0, held)})
                            if shoot.call_count:
                                break
                    self.assertEqual(shoot.call_count, 1)
                    self.assertIs(shoot.call_args.args[0], bot)
                    self.assertEqual(shot_guards.active_guard(mech, fight.tick * combat.TICK * 1000)
                                     is not None, blocked)
                    if definition is None:
                        self.assertFalse(fight.rockets)
                    else:
                        rocket, = fight.rockets
                        self.assertEqual(rocket.definition, definition)
                        for _ in range(20):
                            f.run.step({f.b: (32768, 0, held)})
                            if rocket not in fight.rockets:
                                break
                        self.assertNotIn(rocket, fight.rockets)
                    if silenced:
                        self.assertIs(fight.hacks[mech.entity][0], effect)
                        self.assertIsNone(shot_guards.active_guard(mech, fight.tick * combat.TICK * 1000))
                    self.assertEqual(f.health(f.a), 200)
                    self.assertEqual(fight.vitals[mech.entity].total, 600 if blocked else 600 - loss)

    def test_autonomous_npc_weapons_track_dva_from_matrix_through_demech(self):
        runtime_dir = os.environ.get("ORPHEUS_NPC_RUNTIME_DIR")
        if not runtime_dir:
            self.skipTest("requires the actual NPC catalog")
        catalog = npc.load_catalog(Path(runtime_dir) / "data" / "pve_npcs_174.json")
        attacks = (("1", 20, 15, None), ("2", 2, 1, 0x04000000000023F0),
                   ("3", 100, 95, 0x0400000000001E80), ("5", 5, 2.5, None))
        for key, damage, armour_loss, projectile in attacks:
            definition = catalog[key]
            self.assertIn(definition.body, mover.loaded_mover_table())
            for button in (2, 32):
                with self.subTest(npc=definition.name, silence=button):
                    self.check_autonomous_dva_target(definition, damage, armour_loss, projectile, button)

    def check_autonomous_dva_target(self, definition, damage, armour_loss, projectile, button):
        f, _ = self.pilot_arena()
        match = f.run.match
        fight = match.combat
        match.game_map = replace(match.game_map, free_for_all=False)
        f.a.team, f.b.team = 0, 1
        for player in f.run.players:
            player.client.bot_stream = lambda body: None
            player.client.queue_bot_death = lambda *args: None
            player.client.queue_bot_death_update = lambda *args: None
        pilot = f.b.body_script
        pilot.set_ult_charge(pilot.component.entity_var(ULT_COST).value())
        f.run.step({f.b: (0, 0, 32)})
        f.press(0, 120)
        mech = dva.controlled_mech(f.b)
        self.assertIsNotNone(mech)
        parked_pilot = tuple(f.b.mover.position)
        for _ in range(48):
            f.run.frame += 1
            match.update(f.run.frame * combat.TICK, f.run.frame)
            f.b.take_commands([Command(f.run.frame, right=127)])
        f.press(0, 35)
        x, y, z = mech.position
        self.assertEqual(tuple(f.b.mover.position), parked_pilot)
        self.assertGreater(abs(x - parked_pilot[0]), 1.5)
        f.a.mover.position[:] = (x + 2, y, z - 1)
        f.a.mover.body = None
        bot = npc.spawn(match, definition, (x, y, z - 5), team=0, autonomous=True)
        component = bot.body_script.component
        component.set(9948, 0)
        if definition.hero == sniper_special.HERO:
            bot.firing = False
            f.press(0, 75)
            special = next(i for i in component.instances.values() if i.graph.index == 0x142C)
            self.assertEqual(send_instance_message(special, 0x0240000000000A45, {}, bot.entity), 1)
            bot.firing = True
            # Start the long weapon windup before engaging Matrix.
            f.press(0, 72)
            self.assertFalse(fight.rockets)
        f.run.step({f.b: (32768, 0, 2)})
        self.assertIsNotNone(shot_guards.active_guard(mech, fight.tick * combat.TICK * 1000))
        vitals = fight.vitals[mech.entity]
        with patch.object(fight, "_shoot", wraps=fight._shoot) as shoot:
            for _ in range(170):
                f.run.step({f.b: (32768, 0, 2)})
                if shoot.call_count:
                    break
        self.assertEqual(bot.attack_target, f.b.entity)
        self.assertTrue(bot.attack_input)
        self.assertEqual(shoot.call_count, 1)
        self.assertIs(shoot.call_args.args[0], bot)
        if projectile is not None:
            rocket, = fight.rockets
            self.assertEqual(rocket.definition, projectile)
            for _ in range(20):
                f.run.step({f.b: (32768, 0, 2)})
                if rocket not in fight.rockets:
                    break
            self.assertNotIn(rocket, fight.rockets)
        self.assertEqual(vitals.total, 600)
        self.assertIsNotNone(shot_guards.active_guard(mech, fight.tick * combat.TICK * 1000))

        angles = aim(fight._eye(f.a), (x, y + 1, z))
        if button == 32:
            f.a.body_script.set_ult_charge(1400)
        for frame in range(220):
            attack = button if button == 2 or frame == 0 else 0
            f.run.step({f.a: (*angles, attack), f.b: (32768, 0, 2)})
            if mech.entity in fight.hacks:
                break
        effect, = fight.hacks[mech.entity]
        self.assertEqual(effect.application.source_entity(), f.a.body)
        self.assertIsNone(shot_guards.active_guard(mech, fight.tick * combat.TICK * 1000))
        f.run.step({f.b: (32768, 0, 0)})
        for _ in range(170):
            f.run.step({f.b: (32768, 0, 2)})
            if vitals.total < 600:
                break
        self.assertEqual(vitals.total, 600 - armour_loss)
        self.assertIs(fight.hacks[mech.entity][0], effect)

        vitals.now[HEALTH_POOL], vitals.now[ARMOUR_POOL] = 1, 0
        for _ in range(220):
            f.press(0)
            if mech.dead and dva.controlled_mech(f.b) is None:
                break
        self.assertTrue(mech.dead)
        self.assertEqual(vitals.killer, bot.entity)
        self.assertNotIn(mech.entity, fight.hacks)
        self.assertIsNone(dva.controlled_mech(f.b))
        self.assertIs(f.b.body_script, pilot)
        for _ in range(220):
            if f.health(f.b) < 150:
                break
            f.press(0)
        self.assertEqual(bot.attack_target, f.b.entity)
        self.assertEqual(f.health(f.b), 150 - damage)
        self.assertEqual(f.health(f.a), 200)

    def test_actual_deadeye_volley_respects_called_matrix_until_sombra_silences_it(self):
        if graph.graph(0x01EE) is None:
            self.skipTest("requires the external Deadeye target effect graph")
        for button in (0, 2, 32):
            with self.subTest(silence=button):
                self.check_rifle_ultimate_owned_matrix(button, visor=False)

    def test_actual_visor_rifle_respects_called_matrix_until_sombra_silences_it(self):
        for button in (0, 2, 32):
            with self.subTest(silence=button):
                self.check_rifle_ultimate_owned_matrix(button, visor=True)

    def test_actual_grenade_is_absorbed_by_owned_matrix_until_sombra_silences_it(self):
        for button in (0, 2, 32):
            with self.subTest(silence=button):
                f, _ = self.pilot_arena()
                match = f.run.match
                fight = match.combat
                match.game_map = replace(match.game_map, free_for_all=False)
                f.a.team, f.b.team = 0, 1
                pilot = f.b.body_script
                pilot.set_ult_charge(pilot.component.entity_var(ULT_COST).value())
                f.run.step({f.b: (0, 0, 32)})
                f.press(0, 120)
                mech = dva.controlled_mech(f.b)
                self.assertIsNotNone(mech)
                x, y, z = mech.position
                hero = scenes.heroes()[0x02E000000000013B]
                ana = match.add_player(3, "Ana", hero.guid, 0, False)
                in_world(match, ana)
                f.run.players.append(ana)
                self.assertTrue(match.switch_hero(ana, hero))
                ana.team = 0
                ana.mover.position[:] = (x, y, z - 5)
                f.a.mover.position[:] = (x + 2, y, z - 1)
                for player in (ana, f.a):
                    player.mover.body = None
                    player.client.queue_bot_live = lambda *args: None
                f.press(0, 50)
                f.run.step({f.b: (32768, 0, 2)})
                self.assertIsNotNone(shot_guards.active_guard(mech, fight.tick * combat.TICK * 1000))
                if button:
                    angles = aim(fight._eye(f.a), (x, y + 1, z))
                    if button == 32:
                        f.a.body_script.set_ult_charge(1400)
                    for frame in range(90):
                        attack = button if button == 2 or frame == 0 else 0
                        f.run.step({f.a: (*angles, attack), f.b: (32768, 0, 2)})
                        if mech.entity in fight.hacks:
                            break
                    effect, = fight.hacks[mech.entity]
                    self.assertEqual(effect.application.source_entity(), f.a.body)
                    self.assertIsNone(shot_guards.active_guard(mech, fight.tick * combat.TICK * 1000))
                angles = aim(fight._eye(ana), (x, y + 1, z))
                f.run.step({ana: (*angles, 16), f.b: (32768, 0, 2)})
                grenade, = [r for r in fight.rockets if r.definition == shot_guards.BIOTIC_GRENADE]
                for _ in range(60):
                    if grenade not in fight.rockets:
                        break
                    f.run.step({ana: (*angles, 0), f.b: (32768, 0, 2)})
                self.assertNotIn(grenade, fight.rockets)
                vitals = fight.vitals[mech.entity]
                self.assertEqual(vitals.total, 555 if button else 600)
                self.assertEqual(vitals.healing_block_until > f.run.frame, bool(button))
                hits = ana.client.messages(combat.HIT)
                self.assertEqual(len(hits), int(bool(button)))
                if button:
                    self.assertEqual(hits[0]["+0x78"]["+0x0"]["+0x0"], mech.entity)
                self.assertEqual(f.health(f.a), 200)

    def check_rifle_ultimate_owned_matrix(self, button, *, visor):
        f, _ = self.pilot_arena()
        match = f.run.match
        fight = match.combat
        match.game_map = replace(match.game_map, free_for_all=False)
        f.a.team, f.b.team = 0, 1
        pilot = f.b.body_script
        pilot.set_ult_charge(pilot.component.entity_var(ULT_COST).value())
        f.run.step({f.b: (0, 0, 32)})
        f.press(0, 120)
        mech = dva.controlled_mech(f.b)
        self.assertIsNotNone(mech)
        x, y, z = mech.position
        hero = scenes.heroes()[SOLDIER if visor else 0x02E0000000000042]
        caster = match.add_player(3, "Rifle ultimate", hero.guid, 0, False)
        in_world(match, caster)
        f.run.players.append(caster)
        self.assertTrue(match.switch_hero(caster, hero))
        caster.team = 0
        caster.mover.position[:] = (x, y, z - 5)
        f.a.mover.position[:] = (x + 2, y, z - 1)
        for player in (caster, f.a):
            player.mover.body = None
            player.client.queue_bot_live = lambda *args: None
        script = caster.body_script
        script.ult_seconds = 0
        f.press(0, 50)
        script.set_ult_charge(script.component.entity_var(ULT_COST).value())
        graph_index, search = (0x0259, 11) if visor else (0x022F, 86)
        root = next(i for i in script.component.instances.values() if i.graph.index == graph_index)
        yaw = 2800 if visor else 0
        f.run.step({caster: (yaw, 0, 32)})
        for _ in range(83 if visor else 44):
            f.run.step({caster: (yaw, 0, 0)})
        visible = root.node_state(root.graph.node(search))
        self.assertEqual(visible.targets, (mech.entity,))
        f.run.step({f.b: (32768, 0, 2)})
        self.assertIsNotNone(shot_guards.active_guard(mech, fight.tick * combat.TICK * 1000))

        if button:
            angles = aim(fight._eye(f.a), (x, y + 1, z))
            if button == 32:
                f.a.body_script.set_ult_charge(1400)
            for frame in range(90):
                attack = button if button == 2 or frame == 0 else 0
                f.run.step({f.a: (*angles, attack), f.b: (32768, 0, 2), caster: (yaw, 0, 0)})
                if mech.entity in fight.hacks:
                    break
            effect, = fight.hacks[mech.entity]
            self.assertEqual(effect.application.source_entity(), f.a.body)
            self.assertIsNone(shot_guards.active_guard(mech, fight.tick * combat.TICK * 1000))

        with patch.object(fight, "_hitscan", wraps=fight._hitscan) as hitscan:
            f.run.step({caster: (yaw, 0, FIRE), f.b: (32768, 0, 2)})
            for _ in range(35):
                if hitscan.call_count:
                    break
                f.run.step({f.b: (32768, 0, 2)})
        self.assertEqual(hitscan.call_count, 1)
        self.assertIs(hitscan.call_args.args[0], caster)
        amount = hitscan.call_args.args[3].amount
        self.assertGreater(amount, 10)
        self.assertLess(amount, 600)
        if visor:
            self.assertEqual(amount, 20)
            rifle = next(i for i in script.component.instances.values() if i.graph.index == 0x0254)
            self.assertEqual(rifle.vars[53].value(), 29)
        expected = 600 - (amount - 5) if button else 600
        self.assertAlmostEqual(fight.vitals[mech.entity].total, expected, places=4)
        self.assertEqual(len(caster.client.messages(combat.HIT)), int(bool(button)))
        self.assertEqual(f.health(f.a), 200)

    def check_owned_mech_graviton_pull(self, f, zarya, mech):
        match = f.run.match
        fight = match.combat
        self.assertIn(mech.entity, fight.gravitons.npc_movers)
        for _ in range(10):
            f.run.frame += 1
            match.update(f.run.frame * combat.TICK, f.run.frame)
            pulled = mech.position
            f.b.take_commands([Command(f.run.frame, forward=127)])
            self.assertEqual(mech.position, pulled)
        match.leave(zarya)
        self.assertNotIn(mech.entity, fight.gravitons.npc_movers)
        before = mech.position[2]
        for _ in range(20):
            f.run.frame += 1
            match.update(f.run.frame * combat.TICK, f.run.frame)
            f.b.take_commands([Command(f.run.frame, forward=127)])
        self.assertGreater(mech.position[2], before + 0.3)

    def test_owned_self_destruct_admission_requires_charge_and_no_silence(self):
        for button in (2, 0x20):
            with self.subTest(button=button):
                f, _ = self.pilot_arena()
                fight = f.run.match.combat
                pilot = f.b.body_script
                pilot.set_ult_charge(pilot.component.entity_var(ULT_COST).value())
                f.run.step({f.b: (0, 0, 0x20)})
                f.press(0, 120)
                mech = dva.controlled_mech(f.b)
                self.assertIsNotNone(mech)
                component = mech.body_script.component
                bomb = next(i for i in component.instances.values() if i.graph.index == 0x0B2F)
                cast = bomb.state(0)
                cost = component.entity_var(ULT_COST).value()
                self.assertEqual(cost, 1540)
                component.set(ULT_CHARGE, cost - 1)
                f.run.step({f.b: (0, 0, 0x20)})
                f.press(0, 10)
                self.assertFalse(cast.flags & cast.RUNNING)
                self.assertEqual(component.entity_var(ULT_CHARGE).value(), cost - 1)

                x, y, z = mech.position
                angles = aim(fight._eye(f.a), (x, y + 1, z))
                f.a.body_script.set_ult_charge(1400)
                for frame in range(90):
                    attack = button if button == 2 or frame == 0 else 0
                    f.run.step({f.a: (*angles, attack)})
                    if mech.entity in fight.hacks:
                        break
                effect, = fight.hacks[mech.entity]
                component.set(ULT_CHARGE, cost)
                f.run.step({f.b: (0, 0, 0x20)})
                f.press(0, 10)
                self.assertFalse(cast.flags & cast.RUNNING)
                self.assertEqual(component.entity_var(ULT_CHARGE).value(), cost)
                self.assertIs(fight.hacks[mech.entity][0], effect)

                f.press(0, effect.until_tick - f.run.frame + 2)
                f.run.step({f.b: (0, 0, 0x20)})
                self.assertTrue(cast.flags & cast.RUNNING)
                self.assertEqual(component.entity_var(ULT_CHARGE).value(), 0)
                f.press(0, 20)
                self.assertTrue(mech.dva_shell)
                self.assertIsNone(dva.controlled_mech(f.b))
                self.assertIs(f.b.body_script, pilot)
                self.assertTrue(bomb.state(6).active)
                targets = {target.entity for target in fight._targets()}
                self.assertIn(f.b.body, targets)
                self.assertNotIn(mech.entity, targets)
                self.assertEqual(f.health(f.a), 200)
                detached_frame = mech.body_script.frame
                f.press(0, 30)
                self.assertGreater(mech.body_script.frame, detached_frame)
                self.assertTrue(bomb.state(6).active)
                self.assertEqual(f.health(f.a), 200)

    def test_paid_self_destruct_replays_pilot_return_and_fuse(self):
        for gap_at in (None, 0, 150):
            with self.subTest(gap_at=gap_at):
                f, _ = self.pilot_arena()
                match = f.run.match
                fight = match.combat
                pilot = f.b.body_script
                pilot.set_ult_charge(pilot.component.entity_var(ULT_COST).value())
                f.run.step({f.b: (0, 0, 32)})
                f.press(0, 120)
                mech = dva.controlled_mech(f.b)
                self.assertIsNotNone(mech)
                x, y, z = mech.position
                f.a.mover.teleport_grounded((x, y, z + 3))
                f.press(0, 5)
                self.assertEqual(f.health(f.a), 200)
                victim = fight.vitals[f.a.body]
                component = mech.body_script.component
                component.set(ULT_CHARGE, component.entity_var(ULT_COST).value())
                f.run.step({f.b: (0, 0, 32)})
                cast = f.run.frame
                if gap_at is None:
                    f.press(0, 186)
                    self.assertEqual(victim.total, 200)
                    self.assertIn(mech, match.bots)
                    f.press(0, 89)
                else:
                    f.press(0, gap_at)
                    if gap_at:
                        before_blast = cast + 186
                        match.update(before_blast * combat.TICK, before_blast)
                        self.assertEqual(victim.total, 200)
                        self.assertIn(mech, match.bots)
                    end = cast + 276
                    match.update(end * combat.TICK, end)
                self.assertEqual(victim.total, 0)
                self.assertEqual(victim.death_tick, cast + 187)
                self.assertEqual(victim.killer, mech.entity)
                self.assertNotIn(mech, match.bots)
                self.assertIsNone(dva.controlled_mech(f.b))
                self.assertEqual(pilot.component.entity_var(ULT_CHARGE).value(),
                                 pilot.component.entity_var(ULT_COST).value())

    def test_owned_self_destruct_damage_respects_cover_range_and_team(self):
        for case in ("open", "wall", "range", "ally", "pilot-death", "bubble", "outer", "outer-disabled"):
            with self.subTest(case=case):
                self.check_owned_self_destruct_damage(case)

    def test_owned_self_destruct_resolves_all_five_npcs_during_pilot_silence(self):
        runtime_dir = os.environ.get("ORPHEUS_NPC_RUNTIME_DIR")
        if not runtime_dir:
            self.skipTest("requires the actual NPC catalog")
        catalog = npc.load_catalog(Path(runtime_dir) / "data" / "pve_npcs_174.json")
        for case in ("hack", "emp", "wall", "range", "ally"):
            with self.subTest(case=case):
                f, _ = self.pilot_arena()
                match = f.run.match
                fight = match.combat
                match.game_map = replace(match.game_map, free_for_all=False)
                f.b.team = 0
                f.a.team = 1
                for player in f.run.players:
                    player.client.queue_bot_death = lambda *args: None
                    player.client.queue_bot_death_update = lambda *args: None
                pilot = f.b.body_script
                pilot.set_ult_charge(pilot.component.entity_var(ULT_COST).value())
                f.run.step({f.b: (0, 0, 32)})
                f.press(0, 120)
                mech = dva.controlled_mech(f.b)
                self.assertIsNotNone(mech)
                x, y, z = mech.position
                f.a.mover.position[:] = (x, y, z - 3)
                f.a.mover.body = None

                victims = []
                for offset, definition in enumerate(catalog.values(), -2):
                    self.assertIn(definition.body, mover.loaded_mover_table())
                    distance = 22 if case == "range" else 2.5
                    victim = npc.spawn(match, definition, (x + offset * 0.5, y, z + distance),
                                       team=0 if case == "ally" else 1)
                    victims.append((victim, fight.vitals[victim.entity].total))
                sombra_cover = solid_wall(x - 4, z - 1.2, x + 4, z - 1.2)
                if case == "wall":
                    f.geometry(*sombra_cover, *solid_wall(x - 4, z + 1.2, x + 4, z + 1.2))
                component = mech.body_script.component
                component.set(ULT_CHARGE, component.entity_var(ULT_COST).value())
                f.run.step({f.b: (0, 0, 32)})
                f.press(0, 110)
                self.assertTrue(mech.dva_shell)

                if case in ("hack", "emp"):
                    button = 2 if case == "hack" else 32
                    if button == 32:
                        f.a.body_script.set_ult_charge(1400)
                    for frame in range(85):
                        f.press(button if button == 2 or frame == 0 else 0)
                        if f.b.body in fight.hacks:
                            break
                    effect, = fight.hacks[f.b.body]
                    self.assertEqual(effect.application.source_entity(), f.a.body)
                    self.assertNotIn(mech.entity, fight.hacks)
                # Put Sombra behind cover after the cast and leave the NPC lane open.
                if case != "wall":
                    f.geometry(*sombra_cover)
                self.assertIn(mech, match.bots)
                for victim, total in victims:
                    self.assertEqual(fight.vitals[victim.entity].total, total)

                for _ in range(130):
                    f.press(0)
                    if mech not in match.bots:
                        break
                self.assertNotIn(mech, match.bots)
                self.assertNotIn(mech.entity, fight.vitals)
                f.press(0, 20)
                self.assertEqual(f.health(f.a), 200)
                self.assertEqual(f.health(f.b), 150)
                hit = case in ("hack", "emp")
                total_loss = 0
                killed = set()
                for victim, total in victims:
                    vitals = fight.vitals[victim.entity]
                    expected = max(0, total - 1000) if hit else total
                    self.assertEqual(vitals.total, expected, victim.name)
                    total_loss += total - expected
                    self.assertEqual(vitals.killer, mech.entity if expected == 0 else None)
                    self.assertEqual(victim.dead, expected == 0)
                    self.assertIn(victim, match.bots)
                    if expected == 0:
                        killed.add(victim.entity)
                kills = f.b.client.messages(combat.KILL_NOTICE)
                self.assertEqual({notice["+0x84"]["+0x0"] for notice in kills}, killed)
                self.assertEqual(len(kills), 4 if hit else 0)
                self.assertTrue(all(notice["+0x80"]["+0x0"] == mech.entity for notice in kills))
                self.assertEqual(f.b.stats.value(stats.ELIMINATIONS), 4 if hit else 0)
                self.assertEqual(f.b.stats.value(stats.HERO_DAMAGE), total_loss)
                if hit:
                    self.assertIs(fight.hacks[f.b.body][0], effect)

    def test_translocator_recall_escapes_self_destruct_behind_cover(self):
        if not translocators.DATA_PATH.is_file():
            self.skipTest("requires the Translocator entity definition")
        for case in ("recall", "stay"):
            with self.subTest(case=case):
                self.check_owned_self_destruct_damage(case)

    def test_silence_targets_returned_pilot_without_stopping_self_destruct(self):
        for case in ("hack-fuse", "emp-fuse"):
            with self.subTest(case=case):
                self.check_owned_self_destruct_damage(case)

    def test_replayed_self_destruct_respects_recall_bubble_and_paid_emp(self):
        if not translocators.DATA_PATH.is_file():
            self.skipTest("requires the Translocator entity definition")
        for case in ("recall", "bubble", "emp-fuse"):
            with self.subTest(case=case):
                self.check_owned_self_destruct_damage(case, world_gap=True)

    def check_owned_self_destruct_damage(self, case, *, world_gap=False):
        f, _ = self.pilot_arena()
        match = f.run.match
        fight = match.combat
        match.game_map = replace(match.game_map, free_for_all=False)
        f.b.team, f.a.team = 0, int(case != "ally")
        pilot = f.b.body_script
        pilot.set_ult_charge(pilot.component.entity_var(ULT_COST).value())
        f.run.step({f.b: (0, 0, 0x20)})
        f.press(0, 120)
        mech = dva.controlled_mech(f.b)
        self.assertIsNotNone(mech)
        x, y, z = mech.position
        distance = {"range": 22, "outer": 19.5, "outer-disabled": 19.5}.get(case, 3)
        if case == "outer-disabled":
            pilot.component.set(10640, 0.0)
        f.a.mover.position[:] = (x, y, z + distance)
        f.a.mover.body = None
        if case == "bubble":
            hero = scenes.heroes()[reinhardt_barrier.ZARYA]
            zarya = match.add_player(3, "Zarya", hero.guid, 2, False)
            in_world(match, zarya)
            f.run.players.append(zarya)
            self.assertTrue(match.switch_hero(zarya, hero))
            zarya.team = f.a.team
            zarya.mover.position[:] = (x, y, z + 24)
            zarya.mover.body = None
            zarya.client.queue_bot_live = lambda *args: None
        if case == "wall":
            f.geometry(*solid_wall(x - 4, z + 1.5, x + 4, z + 1.5))
        f.press(0, 5)
        if case in ("recall", "stay"):
            for _ in range(20):
                f.run.step({f.a: (0, 0, 16)})
            f.press(0, 180)
            beacon, = fight.translocators.entries.values()
            self.assertTrue(beacon.settled)
            self.assertGreater(beacon.position[2], z + 8)
            self.assertLess(beacon.position[2], z + 19)
            f.geometry(*solid_wall(x - 4, z + 8, x + 4, z + 8))
        component = mech.body_script.component
        component.set(ULT_CHARGE, component.entity_var(ULT_COST).value())
        f.run.step({f.b: (0, 0, 0x20)})
        cast = f.run.frame
        f.press(0, 110 if case == "hack-fuse" else 150)
        self.assertTrue(mech.dva_shell)
        self.assertEqual(f.health(f.a), 200)
        self.assertLess(pilot.component.entity_var(ULT_CHARGE).value(),
                        pilot.component.entity_var(ULT_COST).value())
        self.assertFalse(match.queue_body_message(f.b.body, dva.SELF_DESTRUCT_COMPLETE, {}, mech.entity))
        if case in ("hack-fuse", "emp-fuse"):
            button = 2 if case == "hack-fuse" else 0x20
            f.a.body_script.set_ult_charge(1400)
            for frame in range(70):
                f.press(button if button == 2 or frame == 0 else 0)
                if f.b.body in fight.hacks:
                    break
            effect, = fight.hacks[f.b.body]
            self.assertEqual(effect.application.source_entity(), f.a.body)
            self.assertNotIn(mech.entity, fight.hacks)
            self.assertIn(mech, match.bots)
            self.assertEqual(f.health(f.a), 200)
        if case == "recall":
            f.press(16)
            f.press(0, 25)
            self.assertIn(mech, match.bots)
            self.assertNotIn(beacon.entity, fight.translocators.entries)
            self.assertAlmostEqual(f.a.mover.position[2], beacon.position[2], places=2)
        if case == "bubble":
            angles = aim(fight._eye(zarya), (x, y + 1.3, z + 3))
            f.run.step({zarya: (*angles, 16)})
            f.press(0, 12)
            barrier, = fight.barriers.values()
            self.assertIs(barrier.protected, f.a)
            self.assertEqual(barrier.vitals.total, 200)
        if case == "pilot-death":
            f.health(f.b)
            fight.vitals[f.b.body].now[HEALTH_POOL] = 8
            f.press(FIRE)
            self.assertTrue(fight.is_dead(f.b))
            self.assertIn(mech, match.bots)
        if world_gap:
            end = cast + 220
            match.update(end * combat.TICK, end)
            f.run.frame = end
        else:
            for _ in range(100):
                f.press(0)
                if mech not in match.bots:
                    break
        self.assertNotIn(mech, match.bots)
        self.assertNotIn(mech.entity, fight.vitals)
        charge = pilot.component.entity_var(ULT_CHARGE).value()
        cost = pilot.component.entity_var(ULT_COST).value()
        if case == "pilot-death":
            self.assertLess(charge, cost)
        else:
            self.assertEqual(charge, cost)
        lethal = case in ("open", "pilot-death", "stay", "hack-fuse", "emp-fuse")
        if case in ("outer", "outer-disabled"):
            self.assertGreater(f.health(f.a), 0)
            self.assertLess(f.health(f.a), 200)
            f.press(0, 8)
            if case == "outer":
                self.assertGreater(f.a.mover.position[2], z + distance + 0.1)
            else:
                self.assertAlmostEqual(f.a.mover.position[2], z + distance, places=2)
        else:
            self.assertEqual(f.health(f.a), 0 if lethal else 200)
        if lethal:
            self.assertEqual(fight.vitals[f.a.body].killer, mech.entity)
            if world_gap:
                self.assertEqual(fight.vitals[f.a.body].death_tick, cast + 187)
            self.assertEqual(f.b.stats.value(stats.ELIMINATIONS), 1)
            self.assertEqual(f.b.stats.value(stats.HERO_DAMAGE), 200)
            kill, = f.b.client.messages(combat.KILL_NOTICE)
            death, = f.a.client.messages(combat.DEATH_NOTICE)
            self.assertEqual(kill["+0x80"]["+0x0"], mech.entity)
            self.assertEqual(death["+0x7C"]["+0x0"], mech.entity)
        else:
            self.assertEqual(f.b.stats.value(stats.ELIMINATIONS), 0)
            self.assertAlmostEqual(f.b.stats.value(stats.HERO_DAMAGE), 200 - f.health(f.a))
        if case == "bubble":
            self.assertEqual(barrier.vitals.total, 0)
            self.assertAlmostEqual(f.a.mover.position[2], z + distance, places=2)
        if case in ("hack-fuse", "emp-fuse"):
            self.assertIs(fight.hacks[f.b.body][0], effect)
            f.run.step({f.b: (0, 0, 0x20)})
            f.press(0, 20)
            self.assertFalse(match.bots)
            self.assertEqual(pilot.component.entity_var(ULT_CHARGE).value(), cost)
            f.press(0, effect.until_tick - f.run.frame + 2)
            self.assertNotIn(f.b.body, fight.hacks)
            self.assertFalse(match.bots)
        if case in ("range", "hack-fuse", "emp-fuse"):
            f.run.step({f.b: (0, 0, 0x20)})
            f.press(0, 120)
            fresh = dva.controlled_mech(f.b)
            self.assertIsNotNone(fresh)
            self.assertNotEqual(fresh.entity, mech.entity)
            self.assertEqual(match.bots, [fresh])
            self.assertEqual(fight.vitals[fresh.entity].total, 600)
            self.assertLess(pilot.component.entity_var(ULT_CHARGE).value(), cost)
            self.assertIs(f.b.body_script, pilot)

    def test_owned_mech_snapshot_captures_live_input_and_clock(self):
        storage = stored.ServerSnapshotTests()
        session, _, players = storage.scene()
        pilot = players[0]
        session.apply("switch_hero", {"account": pilot.account_lo, "hero": dva.HERO})
        script = pilot.body_script
        script.set_ult_charge(script.component.entity_var(338).value())
        match = pilot.match
        tick_start, frame_start = match.tick, script.frame
        for step in range(1, 121):
            tick = tick_start + step
            match.update(tick * combat.TICK, tick)
            pilot.take_commands([Command(frame_start + step, buttons=0x20 if step == 1 else 0)])
            if dva.controlled_mech(pilot) is not None:
                break
        mech = dva.controlled_mech(pilot)
        self.assertIsNotNone(mech)
        for offset in range(1, 15):
            tick = tick_start + step + offset
            match.update(tick * combat.TICK, tick)
            pilot.take_commands([Command(frame_start + step + offset, buttons=2)])
        self.assertEqual(mech.dva_buttons, 2)
        snapshot = storage.capture(session)
        self.assertFalse(snapshot["restorable"])
        before = snapshot_digest(snapshot)
        mech.dva_buttons = 0
        self.assertNotEqual(snapshot_digest(storage.capture(session)), before)
        mech.dva_buttons = 2
        mech.dva_frame_offset += 1
        self.assertNotEqual(snapshot_digest(storage.capture(session)), before)
        mech.dva_frame_offset -= 1
        self.assertEqual(snapshot_digest(storage.capture(session)), before)

    def test_sombra_lethal_melee_returns_owned_mech_to_pilot(self):
        for button in (0, 2, 0x20):
            with self.subTest(button=button):
                self.check_sombra_lethal_mech_return(button)

    def check_sombra_lethal_mech_return(self, button):
        f, weapon = self.pilot_arena()
        match = f.run.match
        fight = match.combat
        for player in (f.a, f.b):
            player.client.queue_bot_death = lambda *args: None
            player.client.queue_bot_death_update = lambda *args: None
        pilot = f.b.body_script
        pilot.set_ult_charge(pilot.component.entity_var(338).value())
        f.run.step({f.b: (0, 0, 0x20)})
        f.press(0, 120)
        mech = dva.controlled_mech(f.b)
        self.assertIsNotNone(mech)
        self.assertFalse(match.queue_body_message(f.b.body, dva.RETURN_TO_PILOT, {}, f.a.body))
        self.assertFalse(match.queue_body_message(f.b.body, dva.RETURN_TO_PILOT, {}, mech.entity))
        x, y, z = mech.position
        f.a.mover.position[:] = (x, y, z + 1.8)
        f.a.mover.body = None
        f.press(0, 5)
        vitals = fight.vitals[mech.entity]
        angles = aim(fight._eye(f.a), (x, y + 1, z))
        if not button:
            self.check_opportunist_tracks_owned_mech(f, mech, angles)
        vitals.now[HEALTH_POOL], vitals.now[ARMOUR_POOL] = 30, 0
        effect = None
        if button:
            if button == 0x20:
                f.a.body_script.set_ult_charge(1400)
                f.run.step({f.a: (*angles, button)})
            f.press(2 if button == 2 else 0, 90)
            effect, = fight.hacks[mech.entity]
            self.assertEqual(effect.application.source_entity(), f.a.body)
            f.press(0, 50)
        f.run.step({f.a: (*angles, scenes.MELEE)})
        f.press(0, 40)
        self.assertTrue(mech.dead)
        self.assertEqual(vitals.killer, f.a.body)
        self.assertIsNone(dva.controlled_mech(f.b))
        self.assertIs(f.b.body_script, pilot)
        self.assertEqual(f.health(f.b), 150)
        self.assertFalse(fight.opportunist_contacts(f.a))
        self.assertNotIn(mech.entity, fight.hacks)
        self.assertNotIn(f.b.body, fight.hacks)
        self.assertFalse(pilot.component.entity_var(888).value())
        if effect is not None:
            self.assertLess(f.run.frame, effect.until_tick)
            self.assertNotIn(effect.application.id, mech.body_script.component.instances)
            self.assertFalse(mech.body_script.component.entity_var(888).value())
        f.press(0, 100)
        f.press(FIRE, shooter=f.b, target=f.a)
        f.press(0, 20)
        self.assertEqual(weapon.vars[53].value(), 19)
        self.assertEqual(f.health(f.a), 186)

    def check_opportunist_tracks_owned_mech(self, f, mech, angles):
        fight = f.run.match.combat
        self.assertFalse(fight.opportunist_contacts(f.a))
        vitals = fight.vitals[mech.entity]
        vitals.now[ARMOUR_POOL] = 1
        self.assertEqual(fight.opportunist_contacts(f.a), (
            {"entity": mech.entity, "outline": False, "health_bar": True},
        ))
        f.run.step({f.a: (*angles, FIRE)})
        self.assertLess(vitals.total, 300)
        self.assertEqual(fight.opportunist_contacts(f.a), (
            {"entity": mech.entity, "outline": True, "health_bar": True},
        ))
        x, _, z = mech.position
        f.geometry(*solid_wall(x - 2, z + 0.9, x + 2, z + 0.9))
        self.assertEqual(fight.opportunist_contacts(f.a), (
            {"entity": mech.entity, "outline": True, "health_bar": False},
        ))
        f.geometry()
        f.press(0, 10)

    def test_wall_blocks_call_mech_entry_after_spawn(self):
        f, _ = self.pilot_arena()
        script = f.b.body_script
        start_z = f.b.mover.position[2]
        script.set_ult_charge(script.component.entity_var(338).value())
        f.run.step({f.b: (0, 0, 0x20)})
        f.press(0, 20)
        self.assertEqual(len(f.run.match.bots), 1)
        wall_z = start_z + f.b.mover.data.stand_radius + 0.08
        f.geometry(*solid_wall(-2, wall_z, 2, wall_z))
        f.press(0, 120)
        cast = next(i for i in script.component.instances.values() if i.graph.index == 0x026A)
        moves = {state.node.pos: state for state in cast.states.values()}
        self.assertEqual(moves[85].counter, 1)
        self.assertFalse(moves[85].active)
        self.assertNotIn(200, moves)
        self.assertGreater(f.b.mover.position[2], start_z)
        self.assertLess(f.b.mover.position[2], start_z + 0.14)

    def test_called_mech_retires_with_its_pilot(self):
        for ending in ("switch", "leave", "death"):
            with self.subTest(ending=ending):
                f, _ = self.pilot_arena()
                match = f.run.match
                fight = match.combat
                f.b.body_script.set_ult_charge(f.b.body_script.component.entity_var(338).value())
                f.run.step({f.b: (0, 0, 0x20)})
                f.press(0, 50)
                mech, = match.bots
                component = mech.body_script.component
                self.assertEqual(npc.remove_spawned(match), ())
                self.assertIn(mech.entity, fight.vitals)
                if ending == "switch":
                    self.assertTrue(match.switch_hero(f.b, scenes.heroes()[SOLDIER]))
                elif ending == "leave":
                    match.leave(f.b)
                else:
                    f.health(f.b)
                    fight.vitals[f.b.body].now[HEALTH_POOL] = 8
                    f.press(FIRE)
                    self.assertTrue(fight.is_dead(f.b))
                self.assertNotIn(mech, match.bots)
                self.assertNotIn(mech.entity, fight.vitals)
                self.assertFalse(component.instances)
                self.assertEqual(sum(u.entity == mech.entity and u.op == world.OP_DESTROY
                                     for u in f.a.client.entities), 1)

    def test_lethal_light_gun_hit_prevents_emp_pulse(self):
        for lethal in (False, True):
            with self.subTest(lethal=lethal):
                f, weapon = self.pilot_arena()
                fight = f.run.match.combat
                if lethal:
                    f.health(f.a)
                    fight.vitals[f.a.body].now[HEALTH_POOL] = 14
                component = f.a.body_script.component
                emp = next(i for i in component.instances.values() if i.graph.index == 0x0B31)
                pulse = emp.node_state(emp.graph.node(40))
                f.a.body_script.set_ult_charge(1400)
                f.press(0x20)
                self.assertTrue(emp.state(1).active)
                self.assertEqual(component.entity_var(342).value(), 0)
                f.press(FIRE, shooter=f.b, target=f.a)
                self.assertEqual(weapon.vars[53].value(), 19)
                self.assertEqual(f.health(f.a), 14 if lethal else 200)
                for _ in range(20):
                    f.press(0)
                    if f.health(f.a) < (14 if lethal else 200):
                        break
                self.assertEqual(f.health(f.a), 0 if lethal else 186)
                self.assertEqual(pulse.counter, 0)
                if lethal:
                    self.assertEqual(fight.vitals[f.a.body].killer, f.b.body)
                f.press(0, 45)
                self.assertEqual(pulse.counter, 0 if lethal else 1)
                self.assertEqual(f.b.body in fight.hacks, not lethal)
                self.assertEqual(component.entity_var(342).value(), 0)

    def test_hack_and_emp_allow_pistol_damage(self):
        for button, frames in ((2, 85), (0x20, 42)):
            with self.subTest(button=button):
                f, instance = self.arena()
                f.a.body_script.set_ult_charge(1400)
                fight = f.run.match.combat
                yaw, pitch = aim(fight._eye(f.a), (0, 1, 4))
                for frame in range(frames):
                    self.assertTrue(instance.state(3).active)
                    press = button if button == 2 or frame == 0 else 0
                    f.run.step({f.a: (yaw, pitch, press), f.b: (32768, 0, 2)})
                    if f.b.body in fight.hacks:
                        break
                self.assertTrue(f.hack_applications())
                self.assertFalse(instance.state(3).active)
                self.assertGreater(instance.vars[87].value(), 1)
                f.press(0, frames - frame - 1)
                self.fire(f)
                self.assertEqual(f.health(f.b), 596)
                effect, = fight.hacks[f.b.body]
                f.press(0, effect.until_tick - f.run.frame + 2)
                self.assertNotIn(f.b.body, fight.hacks)
                for _ in range(5):
                    f.run.step({f.b: (32768, 0, 2)})
                self.assertTrue(instance.state(3).active)
                self.fire(f)
                self.assertEqual(f.health(f.b), 596)
