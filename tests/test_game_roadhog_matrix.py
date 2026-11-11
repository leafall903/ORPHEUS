"""Roadhog commands against authored mech0407 Matrix; host filters reconstructed."""

import hashlib
import json
import os
import tempfile
import unittest
from dataclasses import fields, replace
from pathlib import Path
from unittest.mock import patch

import test_game_roadhog_defenses as phases
import test_game_roadhog_winston as domes
from test_game_combat import aim

from ow174.game import combat, defenses, deflection, mover, npc, npc_control, projectiles
from ow174.game.collision import World
from ow174.game.content import heroes
from ow174.game.script import graph, nodes

MECH = 0x0400000000000407
DVA = 0x02E000000000007A


class RoadhogMatrixTests(unittest.TestCase):
    health = phases.RoadhogDefenseTests.health

    def setUp(self):
        path = os.environ.get("ORPHEUS_TEST_MECH_MOVERS")
        if not path or graph.entity_body(MECH) is None or not deflection.tags():
            self.skipTest("requires actual0407 graph/mover and projectile tags; Matrix remains OPEN")
        names = {f.name for f in fields(mover.MoverData)}
        rows = json.loads(Path(path).read_text(encoding="utf-8"))
        table = dict(mover._movers())
        table.update({int(k, 16): mover.MoverData(**{n: v for n, v in row.items() if n in names})
                      for k, row in rows.items()})
        table_patch = patch.object(mover, "_mover_table", table)
        table_patch.start()
        self.addCleanup(table_patch.stop)

    def step(self, run, hog, mech, *, fire=0, cast=2, facing=-32768):
        x, y, z = mech.mover.position
        angles = aim(run.match.combat._eye(hog), (x, y + 1, z))
        run.step({hog: (*angles, fire), mech: (facing, 0, cast)})

    def session(self, distance=15, *, activate=True):
        helper = phases.RoadhogDefenseTests()
        run, hog, mech, rear = helper.session("Reaper", distance=distance)
        self.assertTrue(run.match.switch_hero(mech, replace(heroes()[DVA], body=MECH)))
        mech.mover.position[:] = (0, hog.mover.position[1], distance)
        mech.mover.body = None
        run.match.combat.vitals[mech.body] = combat.Vitals(mech.body, 20000)
        for _ in range(100):
            self.step(run, hog, mech, cast=0)
        matrix = next(i for i in mech.body_script.component.instances.values() if i.graph.index == 0x2CC)
        self.assertEqual(matrix.vars[87].value(), 100)
        self.assertEqual(matrix.vars[971].value(), 100)
        if activate:
            for _ in range(5):
                self.step(run, hog, mech)
            self.assertTrue(matrix.state(3).active)
        return run, hog, mech, rear, matrix

    def test_tagged_airborne_mines_cross_live_projectile_defenses_and_hit_roadhog(self):
        for name, button, distance in (("D.Va", 2, 10), ("Genji", 16, 3), ("Sigma", 8, 3)):
            with self.subTest(defense=name):
                self.check_airborne_mine_defense(name, button, distance)

    def check_airborne_mine_defense(self, name, button, distance):
        import math

        import test_game_roadhog_minefield as mine_tests

        from ow174.game import ball_mines

        if graph.entity_body(ball_mines.BODY) is None:
            self.skipTest("prepared 2578/151A Minefield graph bundle required")
        fixture = mine_tests.RoadhogMinefieldTests()
        run, hog, ball, defender = fixture.scene()
        match = run.match
        fight = match.combat
        match.game_map = replace(match.game_map, free_for_all=False)
        hog.team = defender.team = 0
        ball.team = 1
        matrix = name == "D.Va"
        hero = replace(heroes()[DVA], body=MECH) if matrix else next(
            hero for hero in heroes().values() if hero.name == name
        )
        self.assertTrue(match.switch_hero(defender, hero))
        x, y, z = ball.mover.position
        fixture.place(defender, (x, y, z + distance))
        for _ in range(100):
            run.step({defender: (-32768, 0, 0)})
        run.step({defender: (-32768, 0, button)})
        for _ in range(4):
            run.step({defender: (-32768, 0, button if matrix else 0)})
        gate_state = next(
            state for instance in defender.body_script.component.instances.values()
            for state in instance.states.values()
            if state.active and state.node.cls == "STUStatescriptStateDeflectProjectiles"
        )
        authored_filter = gate_state.node.field("m_filter")
        self.assertEqual(authored_filter["m_27CF71D5"], 2)
        tag = graph.guid(authored_filter["m_8B2A4DF3"]["m_7D7C86A1"])
        self.assertIn(tag, deflection.tags()[ball_mines.BODY])
        self.assertTrue(deflection.absorbable(0x04000000000003D4))

        fixture.cast(run, ball)
        crossed = set()
        field = fight.mines
        for _ in range(50):
            before = {mine.entity: mine.position for mine in field.entities.values() if not mine.landed}
            run.step({defender: (-32768, 0, button if matrix else 0)})
            target = next(target for target in fight._enemies(ball) if target.owner is defender)
            for entity, start in before.items():
                mine = field.entities.get(entity)
                if mine is None or mine.landed:
                    continue
                delta = tuple(end - begin for begin, end in zip(start, mine.position, strict=True))
                length = math.hypot(*delta)
                if not length:
                    continue
                direction = tuple(axis / length for axis in delta)
                control = defenses.projectile_gate(start, direction, length, (target,), 0x04000000000003D4)
                if control is None:
                    continue
                if matrix:
                    excluded = defenses.projectile_gate(start, direction, length, (target,), ball_mines.BODY)
                    self.assertIsNone(excluded)
                crossed.add(entity)
        self.assertTrue(crossed, f"no actual airborne mine chord intersected live {name}")
        self.assertEqual(len(field.entities), 15)
        self.assertTrue(crossed <= field.entities.keys())
        fixture.place(defender, (x + 30, y, z))
        for _ in range(130):
            run.step({})
        self.assertEqual(len(field.entities), 15)
        for mine in field.entities.values():
            self.assertTrue(mine.armed)
            self.assertEqual(
                (mine.owner, mine.source, mine.team, mine.health), (ball, ball.body, ball.team, 50),
            )
        self.assertFalse(fixture.pool(run, defender).temporary_shields)
        mine = field.entities[min(crossed)]
        for other in tuple(field.entities.values()):
            if other is not mine:
                field.remove(other)
        x, y, z = mine.position
        fixture.place(hog, (x + .55, y, z))
        run.step({})
        self.assertTrue(mine.triggered)
        self.assertEqual(fixture.pool(run, hog).total, 470)

    def test_primary_slug_and_fragments_absorbed_without_damage_charge_or_extra_ammo(self):
        for attack, distance in ((1, 15), (2, 3), (2, 15), (2, 23)):
            with self.subTest(attack=attack, distance=distance):
                run, hog, mech, rear, matrix = self.session(distance)
                weapon = next(i for i in hog.body_script.component.instances.values()
                              if i.graph.index == 0x1C6)
                ammo = weapon.vars[53].value()
                self.step(run, hog, mech, fire=attack)
                for _ in range(55):
                    self.step(run, hog, mech)
                self.assertEqual(self.health(run, mech), 20000)
                self.assertEqual(self.health(run, rear), 20000)
                self.assertEqual(weapon.vars[53].value(), ammo - 1)
                self.assertEqual(hog.body_script.component.entity_var(342).value(), 0)
                self.assertFalse(hog.client.messages(combat.HIT))
                self.assertFalse(run.match.combat.rockets)
                self.assertTrue(matrix.state(3).active)

    def test_actual_hand_cannon_fan_is_absorbed_before_it_can_reach_roadhog(self):
        from ow174.game import hand_cannon

        run, hog, mech, doom, matrix = self.session(3, activate=False)
        fight = run.match.combat
        self.assertTrue(run.match.switch_hero(doom, heroes()[hand_cannon.HERO]))
        run.match.game_map = replace(run.match.game_map, free_for_all=False)
        hog.team = mech.team = 0
        doom.team = 1
        x, y, z = hog.mover.position
        doom.mover.position[:] = (x, y, z + 6)
        doom.mover.body = None
        doom.body_script.ult_seconds = 0
        doom.body_script.set_ult_charge(0)
        for _ in range(35):
            run.step({})
        face = aim(fight._eye(mech), fight._eye(doom))
        for _ in range(5):
            run.step({mech: (*face, 2)})
        self.assertTrue(matrix.state(3).active)
        look = aim(fight._eye(doom), fight._eye(mech))
        run.step({doom: (*look, 1), mech: (*face, 2)})
        self.assertEqual(sum(p.shooter is doom for p in fight.rockets), 11)
        for _ in range(20):
            run.step({mech: (*face, 2)})
        self.assertEqual(self.health(run, hog), 600)
        self.assertEqual(self.health(run, mech), 20000)
        self.assertFalse(doom.client.messages(combat.HIT))
        self.assertEqual(doom.body_script.component.entity_var(342).value(), 0)
        self.assertFalse(fight.rockets)

    def test_quad_cannons_are_absorbed_before_reaching_roadhog(self):
        import test_game_roadhog_quad_cannons as quad_tests

        from ow174.game import quad_cannons

        run, hog, mech, ball, matrix = self.session(4, activate=False)
        fight = run.match.combat
        fixture = quad_tests.RoadhogQuadCannonsTests()
        self.assertTrue(run.match.switch_hero(ball, heroes()[quad_cannons.HERO]))
        run.match.game_map = replace(run.match.game_map, free_for_all=False)
        hog.team = mech.team = 0
        ball.team = 1
        x, y, z = hog.mover.position
        fixture.place(ball, (x, y, z + 8))
        ball.body_script.ult_seconds = 0
        ball.body_script.set_ult_charge(0)
        for _ in range(40):
            run.step({})
        face = aim(fight._eye(mech), fight._eye(ball))
        for _ in range(5):
            run.step({mech: (*face, 2)})
        self.assertTrue(matrix.state(3).active)
        before = matrix.vars[87].value()
        run.step({ball: (*fixture.look(run, ball, mech, 1.2), 1), mech: (*face, 2)})
        self.assertTrue(matrix.state(3).active)
        self.assertLess(matrix.vars[87].value(), before)
        self.assertEqual(fixture.pool(run, hog).total, 600)
        self.assertEqual(fixture.pool(run, mech).total, 20000)
        self.assertEqual(fixture.weapon(ball).vars[53].value(), 79)
        self.assertEqual(fixture.charge(ball), 0)
        self.assertFalse(ball.client.messages(combat.HIT))

    def test_whole_hog_hitscan_absorbed_while_resource_drains(self):
        run, hog, mech, rear, matrix = self.session()
        hog.body_script.set_ult_charge(2240)
        self.step(run, hog, mech, fire=32)
        for _ in range(65):
            self.step(run, hog, mech)
        self.assertEqual(self.health(run, mech), 20000)
        self.assertEqual(self.health(run, rear), 20000)
        self.assertEqual(hog.body_script.component.entity_var(342).value(), 0)
        self.assertFalse(hog.client.messages(combat.HIT))
        self.assertLess(matrix.vars[87].value(), 60)

    def test_release_reopens_damage_and_cooldown_repress_does_not_absorb(self):
        run, hog, mech, _, matrix = self.session()
        for _ in range(30):
            self.step(run, hog, mech)
        self.step(run, hog, mech, cast=0)
        self.assertFalse(matrix.state(3).active)
        self.assertGreater(matrix.vars[61].value(), 0)
        self.step(run, hog, mech, fire=1)
        for _ in range(25):
            self.step(run, hog, mech)
        self.assertFalse(matrix.state(3).active)
        self.assertLess(self.health(run, mech), 20000)

    def test_matrix_press_and_release_apply_to_secondary_fragments_already_in_flight(self):
        for release in (False, True):
            with self.subTest(release=release):
                run, hog, mech, rear, matrix = self.session(distance=22, activate=release)
                fight = run.match.combat
                rear.mover.position[0] += 30
                rear.mover.body = None
                weapon = next(i for i in hog.body_script.component.instances.values()
                              if i.graph.index == 0x01C6)
                if release:
                    for _ in range(20):
                        self.step(run, hog, mech)
                self.step(run, hog, mech, fire=2, cast=2 if release else 0)
                slug, = fight.rockets
                self.assertIsNotNone(slug.burst)
                for _ in range(12):
                    self.step(run, hog, mech, cast=2 if release else 0)
                    if slug not in fight.rockets:
                        break
                self.assertEqual(len(fight.rockets), 25)
                self.assertTrue(all(rocket.burst is None for rocket in fight.rockets))
                self.assertEqual(matrix.state(3).active, release)
                self.assertEqual(self.health(run, mech), 20000)
                self.step(run, hog, mech, cast=0 if release else 2)
                self.assertEqual(matrix.state(3).active, not release)
                for _ in range(90):
                    self.step(run, hog, mech, cast=0 if release else 2)
                self.assertEqual(weapon.vars[53].value(), 4)
                self.assertEqual(self.health(run, rear), 20000)
                self.assertFalse(fight.rockets)
                if release:
                    self.assertLess(self.health(run, mech), 20000)
                    self.assertTrue(hog.client.messages(combat.HIT))
                else:
                    self.assertEqual(self.health(run, mech), 20000)
                    self.assertFalse(hog.client.messages(combat.HIT))

    def test_hook_bypasses_matrix_and_interrupts_its_actual_graph(self):
        run, hog, mech, _, matrix = self.session()
        self.step(run, hog, mech, fire=8)
        caught = False
        for _ in range(100):
            self.step(run, hog, mech, cast=0 if caught else 2)
            if run.match.combat.hooks.controlled(mech.body):
                caught = True
                self.assertFalse(matrix.state(3).active)
        self.assertTrue(caught)
        self.assertEqual(self.health(run, mech), 19970)
        self.assertFalse(matrix.state(3).active)


    def test_hook_interruption_starts_matrix_cooldown_and_refuses_repress(self):
        run, hog, mech, _, matrix = self.session(3)
        fight = run.match.combat
        self.step(run, hog, mech, fire=8)
        for _ in range(40):
            self.step(run, hog, mech)
            if fight.hooks.controlled(mech.body):
                break
        self.assertTrue(fight.hooks.controlled(mech.body))
        self.assertFalse(matrix.state(3).active)
        self.assertEqual(matrix.state(0).cur, matrix.vars[472].value())
        self.assertAlmostEqual(matrix.vars[61].value(), matrix.vars[472].value() - combat.TICK)
        self.assertGreater(matrix.vars[61].value(), 0)

        for _ in range(40):
            self.step(run, hog, mech)
            if not fight.hooks.controlled(mech.body):
                break
        self.assertFalse(fight.hooks.controlled(mech.body))
        self.assertFalse(matrix.state(3).active)
        self.assertGreater(matrix.vars[61].value(), 0)
        self.step(run, hog, mech, cast=0)
        self.step(run, hog, mech)
        self.assertFalse(matrix.state(3).active)
        before_hog = self.health(run, hog)
        before_mech = self.health(run, mech)
        self.step(run, hog, mech, fire=1, cast=1)
        for _ in range(70):
            fire = 1 if self.health(run, mech) == before_mech else 0
            self.step(run, hog, mech, fire=fire, cast=0)
            if matrix.vars[61].value() == 0:
                break
        self.assertLess(self.health(run, hog), before_hog)
        self.assertLess(self.health(run, mech), before_mech)
        self.assertEqual(matrix.vars[61].value(), 0)

        self.step(run, hog, mech)
        self.assertTrue(matrix.state(3).active)
        protected = self.health(run, mech)
        with patch.object(fight.projectiles, "absorbed", wraps=fight.projectiles.absorbed) as absorbed:
            for _ in range(65):
                self.step(run, hog, mech, fire=2)
                if absorbed.called:
                    break
        self.assertTrue(absorbed.called)
        self.assertEqual(self.health(run, mech), protected)

    def test_melee_bypasses_matrix_and_does_not_end_it(self):
        run, hog, mech, rear, matrix = self.session(1.5)
        rear.mover.position[0] = 20
        self.step(run, hog, mech, fire=0x800)
        for _ in range(20):
            self.step(run, hog, mech)
        self.assertEqual(self.health(run, mech), 19970)
        self.assertTrue(matrix.state(3).active)

    def test_away_facing_does_not_absorb_and_exhaustion_reopens_damage(self):
        for exhausted in (False, True):
            with self.subTest(exhausted=exhausted):
                run, hog, mech, _, matrix = self.session()
                if exhausted:
                    for _ in range(135):
                        self.step(run, hog, mech)
                    self.assertFalse(matrix.state(3).active)
                    self.assertEqual(matrix.vars[87].value(), 0)
                self.step(run, hog, mech, fire=1, facing=-32768 if exhausted else 0)
                for _ in range(25):
                    self.step(run, hog, mech, facing=-32768 if exhausted else 0)
                self.assertLess(self.health(run, mech), 20000)

    def test_allied_matrix_does_not_shield_an_enemy_behind_it(self):
        run, hog, mech, rear, matrix = self.session()
        run.match.game_map = replace(run.match.game_map, free_for_all=False)
        mech.team = hog.team
        rear.team = 1 - hog.team
        self.step(run, hog, mech, fire=1)
        for _ in range(25):
            self.step(run, hog, mech)
        self.assertEqual(self.health(run, mech), 20000)
        self.assertLess(self.health(run, rear), 20000)
        self.assertTrue(matrix.state(3).active)

    def test_static_wall_precedes_matrix_and_rejects_damage(self):
        run, hog, mech, rear, _ = self.session()
        run.match.collision_world = World([
            (-10, -10, 2, 10, -10, 2, 10, 10, 2),
            (-10, -10, 2, 10, 10, 2, -10, 10, 2),
        ])
        with patch.object(run.match.combat.projectiles, "absorbed",
                          wraps=run.match.combat.projectiles.absorbed) as absorbed:
            self.step(run, hog, mech, fire=2)
            for _ in range(25):
                self.step(run, hog, mech)
            self.assertEqual(absorbed.call_count, 0)
        self.assertEqual(self.health(run, mech), 20000)
        self.assertEqual(self.health(run, rear), 20000)

    def test_live_matrix_and_grasp_each_win_when_nearer(self):
        for button in (2, 32):
            with self.subTest(button=button):
                matrix_near = self.check_matrix_grasp_order(13, False, button)
                grasp_near = self.check_matrix_grasp_order(18, True, button)
                self.assertGreater(grasp_near, matrix_near)

    def check_matrix_grasp_order(self, distance, grasp_first, button):
        run, hog, mech, sigma, matrix = self.session(distance, activate=False)
        match = run.match
        fight = match.combat
        self.assertTrue(match.switch_hero(sigma, heroes()[0x02E000000000023B]))
        y = hog.mover.position[1]
        sigma.mover.position[:] = (0, y, 6)
        sigma.mover.body = None
        fight.vitals[sigma.body] = combat.Vitals(sigma.body, 20000)
        for _ in range(30):
            run.step({mech: (-32768, 0, 0), sigma: (-32768, 0, 0)})
        run.step({mech: (-32768, 0, 2), sigma: (-32768, 0, 8)})
        hold_matrix = {mech: (-32768, 0, 2), sigma: (-32768, 0, 0)}
        for _ in range(6):
            run.step(hold_matrix)
        grasp = next(i for i in sigma.body_script.component.instances.values() if i.graph.index == 0x1D02)
        self.assertTrue(matrix.state(3).active)
        self.assertTrue(grasp.states[1].active)
        angles = aim(fight._eye(hog), (0, y + 1, 6))
        if button == 32:
            script = hog.body_script
            script.set_ult_charge(script.component.entity_var(338).value())
        with patch.object(defenses, "grasp_absorbed", wraps=defenses.grasp_absorbed) as contacts:
            run.step({hog: (*angles, button), **hold_matrix})
            for _ in range(45):
                run.step({hog: (*angles, 0), **hold_matrix})
        absorbed = grasp.vars[21312].value()
        owners = [call.args[0] for call in contacts.call_args_list]
        if button == 32:
            # Some off-axis rays can miss Matrix even when its centre is nearer.
            self.assertIn(sigma, owners)
            if not grasp_first:
                self.assertIn(mech, owners)
            self.assertAlmostEqual(absorbed, sum(call.args[1] for call in contacts.call_args_list
                                                 if call.args[0] is sigma), places=4)
        else:
            self.assertEqual(owners, [sigma if grasp_first else mech])
            self.assertEqual(absorbed, 50 if grasp_first else 0)
        self.assertEqual(self.health(run, sigma), 20000)
        self.assertEqual(self.health(run, mech), 20000)
        self.assertFalse(hog.client.messages(combat.HIT))
        self.assertEqual(hog.body_script.component.entity_var(342).value(), 0)
        for _ in range(120):
            run.step({hog: (16384, 0, 0), sigma: (-32768, 0, 0)})
        shields = fight.vitals[sigma.body].temporary_shields
        if absorbed:
            self.assertAlmostEqual(shields[defenses.SHIELD_TAG].now, min(400, absorbed * 0.6), delta=0.0001)
        else:
            self.assertFalse(shields)
        return absorbed

    def test_live_matrix_and_deflect_resolve_outbound_and_reflected_slug(self):
        for layout, distance, genji_depth, allied, damage, hits in (
            ("matrix first", 13, 6, False, 0, 0),
            ("deflect first close", 18, 4, False, 50, 1),
            ("deflect then burst", 18, 6, False, 165, 25),
            ("matrix on return", 2, 6, True, 0, 0),
        ):
            with self.subTest(layout=layout):
                run, hog, mech, genji, matrix = self.session(distance, activate=False)
                match = run.match
                fight = match.combat
                self.assertTrue(match.switch_hero(genji, heroes()[0x02E0000000000029]))
                match.game_map = replace(match.game_map, free_for_all=False)
                hog.team, mech.team, genji.team = 0, int(not allied), 1
                y = hog.mover.position[1]
                genji.mover.position[:] = (0, y, genji_depth)
                genji.mover.body = None
                fight.vitals[genji.body] = combat.Vitals(genji.body, 20000)
                facing = 0 if allied else -32768
                for _ in range(15):
                    run.step({mech: (facing, 0, 0), genji: (-32768, 0, 0)})
                run.step({mech: (facing, 0, 2), genji: (-32768, 0, 16)})
                hold = {mech: (facing, 0, 2), genji: (-32768, 0, 0)}
                for _ in range(6):
                    run.step(hold)
                self.assertTrue(matrix.state(3).active)
                deflect = next(i for i in genji.body_script.component.instances.values() if i.graph.index == 0x03A3)
                self.assertTrue(any(s.active and s.node.cls == "STUStatescriptStateDeflectProjectiles"
                                    for s in deflect.states.values()))

                look = aim(fight._eye(hog), (0, y + 1, genji_depth))
                gate = defenses.projectile_gate
                contacts = []

                def observe(*args, **kwargs):
                    result = gate(*args, **kwargs)
                    if result is not None:
                        contacts.append((result[1].owner, result[2]))
                    return result

                with patch.object(defenses, "projectile_gate", side_effect=observe):
                    run.step({hog: (*look, 2), **hold})
                    self.assertEqual(len(fight.rockets), 1)
                    for _ in range(40):
                        run.step(hold)
                        if not fight.rockets:
                            break
                self.assertFalse(fight.rockets)
                expected = {"matrix first": [(mech, 1)], "deflect first close": [(genji, 0)],
                            "deflect then burst": [(genji, 0)],
                            "matrix on return": [(genji, 0), (mech, 1)]}
                self.assertEqual(contacts, expected[layout])
                self.assertAlmostEqual(self.health(run, hog), 600 - damage, places=4)
                self.assertEqual(self.health(run, genji), 20000)
                self.assertEqual(self.health(run, mech), 20000)
                self.assertEqual(len(genji.client.messages(combat.HIT)), hits)
                self.assertFalse(hog.client.messages(combat.HIT))
                self.assertEqual(hog.body_script.component.entity_var(342).value(), 0)

    def test_allied_matrix_blocks_reflected_whole_hog_force_until_release(self):
        run, hog, mech, genji, matrix = self.session(2, activate=False)
        match = run.match
        fight = match.combat
        self.assertTrue(match.switch_hero(genji, heroes()[0x02E0000000000029]))
        match.game_map = replace(match.game_map, free_for_all=False)
        hog.team, mech.team, genji.team = 0, 0, 1
        y = hog.mover.position[1]
        mech.mover.position[:] = (2, y, 1)
        genji.mover.position[:] = (0, y, 4)
        mech.mover.body = genji.mover.body = None
        fight.vitals[genji.body] = combat.Vitals(genji.body, 20000)
        for _ in range(15):
            run.step({})
        look = aim(fight._eye(hog), (0, y + 1, 4))
        cover = aim(fight._eye(mech), (0, y + 1, 4))
        run.step({mech: (*cover, 2), genji: (-32768, 0, 16)})
        held = {mech: (*cover, 2), genji: (-32768, 0, 0)}
        for _ in range(6):
            run.step(held)
        self.assertTrue(matrix.state(3).active)
        deflect = next(i for i in genji.body_script.component.instances.values() if i.graph.index == 0x03A3)
        reflection = next(s for s in deflect.states.values()
                          if s.active and s.node.cls == "STUStatescriptStateDeflectProjectiles")
        script = hog.body_script
        ultimate, = script.ultimates
        script.set_ult_charge(script.component.entity_var(338).value())
        energy = matrix.vars[87].value()
        with patch.object(defenses, "grasp_absorbed", wraps=defenses.grasp_absorbed) as absorbed:
            run.step({hog: (*look, 32), **held})
            for _ in range(65):
                run.step({hog: (*look, 0), **held})
                if absorbed.called:
                    break
            for _ in range(10):
                run.step({hog: (*look, 0), **held})
                self.assertEqual(self.health(run, hog), 600)
                self.assertFalse(fight.impulses.live)
        absorbed.assert_called()
        self.assertTrue(all(call.args[0] is mech and call.args[1] > 0 for call in absorbed.call_args_list))
        self.assertTrue(matrix.state(3).active)
        self.assertTrue(reflection.active)
        self.assertTrue(ultimate.flags & nodes.Ability.RUNNING)
        self.assertLess(matrix.vars[87].value(), energy)
        self.assertEqual(self.health(run, hog), 600)
        self.assertFalse(fight.impulses.live)
        self.assertFalse(hog.client.messages(combat.HIT))
        self.assertFalse(genji.client.messages(combat.HIT))

        for _ in range(25):
            run.step({hog: (*look, 0), mech: (*cover, 0), genji: (-32768, 0, 0)})
            if hog.body in fight.impulses.live:
                break
        self.assertFalse(matrix.state(3).active)
        self.assertTrue(reflection.active)
        self.assertTrue(ultimate.flags & nodes.Ability.RUNNING)
        self.assertLess(self.health(run, hog), 600)
        self.assertLess(fight.impulses.velocity(hog.body)[2], 0)
        self.assertEqual(self.health(run, mech), 20000)
        self.assertEqual(self.health(run, genji), 20000)
        self.assertNotIn(mech.body, fight.impulses.live)
        self.assertNotIn(genji.body, fight.impulses.live)
        hits = genji.client.messages(combat.HIT)
        self.assertTrue(hits)
        self.assertTrue(all(hit["+0x78"]["+0x0"]["+0x0"] == hog.body for hit in hits))
        self.assertAlmostEqual(sum(hit["+0x78"]["+0x4"] for hit in hits), 600 - self.health(run, hog))
        self.assertFalse(hog.client.messages(combat.HIT))
        self.assertEqual(script.component.entity_var(342).value(), 0)

    def test_live_matrix_and_dome_resolve_nearest_interception(self):
        for distance, matrix_first in ((10, True), (18, False)):
            with self.subTest(matrix_first=matrix_first):
                run, hog, mech, winston, matrix = self.session(distance, activate=False)
                match = run.match
                fight = match.combat
                match.switch_hero(winston, next(hero for hero in heroes().values() if hero.name == "Winston"))
                y = hog.mover.position[1]
                winston.mover.position[:] = (0, y, 8)
                winston.mover.body = None
                dome = domes.RoadhogWinstonTests().cast(run, hog, winston)
                winston.mover.position[0] = 20
                winston.mover.body = None
                for _ in range(5):
                    self.step(run, hog, mech)
                self.assertTrue(matrix.state(3).active)
                origin, direction = fight._eye(hog), (0, 0, 1)
                contact = fight._barrier_contact(hog, origin, direction, distance)
                gate = deflection.matrix_distance(
                    mech, origin, direction, distance, fight._eye(mech), (0, 0, -1),
                )
                self.assertIs(contact[1], dome)
                self.assertIsNotNone(gate)
                self.assertEqual(gate < contact[0], matrix_first)
                with patch.object(fight.projectiles, "absorbed",
                                  wraps=fight.projectiles.absorbed) as absorbed:
                    self.step(run, hog, mech, fire=2)
                    for _ in range(40):
                        self.step(run, hog, mech)
                    self.assertEqual(absorbed.called, matrix_first)
                self.assertEqual(dome.health == 700, matrix_first)
                self.assertEqual(self.health(run, mech), 20000)
                self.assertTrue(matrix.state(3).active)

    @unittest.skipUnless(os.environ.get("ORPHEUS_NPC_CATALOG") and os.environ.get("ORPHEUS_NPC_GRAPHS"),
                         "actual manual NPC inputs required")
    def test_allied_matrix_absorbs_four_actual_npc_guns_before_roadhog(self):
        import test_game_roadhog_npcs as actual

        for definition in npc.load_catalog(Path(os.environ["ORPHEUS_NPC_CATALOG"])).values():
            if definition.name == "Assassin":
                continue
            with self.subTest(npc=definition.name):
                run, hog, mech, rear, matrix = self.session(5, activate=False)
                actual.RoadhogFiveNpcTests.setUpClass()
                try:
                    self.check_incoming_matrix(run, hog, mech, rear, matrix, definition, actual.NpcClient)
                finally:
                    actual.RoadhogFiveNpcTests.tearDownClass()

    def check_incoming_matrix(self, run, hog, mech, rear, matrix, definition, client):
        match = run.match
        match.game_map = replace(match.game_map, free_for_all=False)
        hog.team = mech.team = 0
        mech.mover.position[0] = 1
        rear.mover.position[0] = 20
        mech.mover.body = rear.mover.body = None
        for player in run.players:
            player.client = client(player)
        bot = npc.spawn(match, definition, (0, hog.mover.position[1], 12), team=1)
        for _ in range(15):
            run.step({})
        self.assertEqual(npc_control.start(match, hog), bot.entity)
        if definition.name == "Sniper":
            for _ in range(65):
                run.step({})
        own = self.health(run, hog)
        captured = []
        gate = defenses.projectile_gate

        def observe(origin, direction, reach, targets, projectile=None):
            result = gate(origin, direction, reach, targets, projectile)
            if result is not None and result[1].owner is mech:
                captured.append(projectile)
            return result

        with patch.object(defenses, "projectile_gate", side_effect=observe):
            for _ in range(105):
                run.step({mech: (0, 0, 2)})
                if captured:
                    break
        self.assertTrue(captured)
        self.assertTrue(all(deflection.absorbable(projectile) for projectile in captured))
        self.assertTrue(matrix.state(3).active)
        self.assertEqual(self.health(run, hog), own)
        self.assertEqual(self.health(run, mech), 20000)
        for _ in range(30):
            run.step({})
            if not matrix.state(3).active:
                break
        self.assertFalse(matrix.state(3).active)
        for _ in range(240):
            run.step({})
            if self.health(run, hog) < own:
                break
        self.assertLess(self.health(run, hog), own)
        npc_control.stop(match)

    @unittest.skipUnless(os.environ.get("ORPHEUS_NPC_CATALOG") and os.environ.get("ORPHEUS_NPC_GRAPHS"),
                         "actual manual NPC inputs required")
    def test_actual_assassin_contact_pins_roadhog_inside_allied_matrix(self):
        import test_game_roadhog_npcs as actual

        run, hog, mech, rear, matrix = self.session(5, activate=False)
        actual.RoadhogFiveNpcTests.setUpClass()
        self.addCleanup(actual.RoadhogFiveNpcTests.tearDownClass)
        definition = next(d for d in actual.RoadhogFiveNpcTests.definitions.values() if d.name == "Assassin")
        match = run.match
        fight = match.combat
        match.game_map = replace(match.game_map, free_for_all=False)
        hog.team = mech.team = 0
        y = hog.mover.position[1]
        mech.mover.position[:] = (1, y, -2)
        rear.mover.position[0] = 20
        mech.mover.body = rear.mover.body = None
        for player in run.players:
            player.client = actual.NpcClient(player)
        bot = npc.spawn(match, definition, (0, y, 2), team=1)
        for _ in range(10):
            run.step({})
        for _ in range(5):
            run.step({mech: (0, 0, 2)})
        self.assertTrue(matrix.state(3).active)
        x, eye_y, z = fight._eye(hog)
        contact = deflection.matrix_distance(
            mech, (x, eye_y, z + 2), (0, 0, -1), 2, fight._eye(mech), (0, 0, 1),
        )
        self.assertEqual(contact, 0)
        target = next(t for t in fight._targets() if t.owner is bot)
        low, high = target.segment()
        centre = tuple((a + b) / 2 for a, b in zip(low, high, strict=True))
        hog.mover.yaw, hog.mover.pitch = aim(fight._eye(hog), centre)
        self.assertEqual(npc_control.start(match, hog), bot.entity)
        for _ in range(100):
            run.step({mech: (0, 0, 2)})
            if fight.pins.get(bot.entity) == hog.body:
                break
        self.assertEqual(fight.pins.get(bot.entity), hog.body)
        self.assertTrue(matrix.state(3).active)
        self.assertEqual(self.health(run, hog), 575)
        self.assertEqual(self.health(run, mech), 20000)
        for _ in range(80):
            run.step({mech: (0, 0, 2)})
            if self.health(run, hog) < 575:
                break
        self.assertLess(self.health(run, hog), 575)
        self.assertTrue(matrix.state(3).active)
        npc_control.stop(match)
        self.assertNotIn(bot.entity, fight.pins)

    @unittest.skipUnless(os.environ.get("ORPHEUS_NPC_CATALOG") and os.environ.get("ORPHEUS_NPC_GRAPHS"),
                         "actual manual NPC inputs required")
    def test_enemy_matrix_covers_all_five_npcs_from_roadhog_projectiles_but_not_contact(self):
        import test_game_roadhog_npcs as actual

        for definition in npc.load_catalog(Path(os.environ["ORPHEUS_NPC_CATALOG"])).values():
            for button, distance in ((1, 2), (2, 2), (2, 16), (32, 2), (8, 2), (0x800, 1)):
                with self.subTest(npc=definition.name, button=button, distance=distance):
                    scene = self.session(20 if distance == 16 else 9, activate=False)
                    actual.RoadhogFiveNpcTests.setUpClass()
                    try:
                        self.check_outgoing_matrix(scene, definition, button, distance, actual.NpcClient)
                    finally:
                        actual.RoadhogFiveNpcTests.tearDownClass()

    def check_outgoing_matrix(self, scene, definition, button, distance, client):
        run, hog, mech, rear, matrix = scene
        match = run.match
        fight = match.combat
        match.game_map = replace(match.game_map, free_for_all=False)
        hog.team, mech.team = 0, 1
        rear.mover.position[0] = 20
        rear.mover.body = None
        for player in run.players:
            player.client = client(player)
        bot = npc.spawn(match, definition, (0, hog.mover.position[1], distance), team=1)
        for _ in range(10):
            run.step({mech: (-32768, 0, 2)})
        self.assertTrue(matrix.state(3).active)
        target = next(t for t in fight._targets() if t.owner is bot)
        pool = fight.vitals_of(target)
        before = pool.total
        low, high = target.segment()
        centre = tuple((a + b) / 2 for a, b in zip(low, high, strict=True))
        angles = aim(fight._eye(hog), centre)
        if button == 32:
            hog.body_script.set_ult_charge(2240)
        caught = False
        captured = []
        gate = defenses.projectile_gate

        def observe(origin, direction, reach, targets, projectile=None):
            result = gate(origin, direction, reach, targets, projectile)
            if result is not None and result[1].owner is mech:
                captured.append(projectile)
            return result

        with patch.object(defenses, "projectile_gate", side_effect=observe):
            run.step({hog: (*angles, button), mech: (-32768, 0, 2)})
            for _ in range(55):
                run.step({hog: (*angles, 0), mech: (-32768, 0, 2)})
                caught |= fight.hooks.controlled(bot.entity)
        self.assertTrue(matrix.state(3).active)
        self.assertEqual(self.health(run, mech), 20000)
        if button in (1, 2, 32):
            self.assertTrue(captured)
            self.assertTrue(all(deflection.absorbable(projectile) for projectile in captured))
            self.assertEqual(pool.total, before)
            self.assertNotIn(bot.entity, fight.impulses.live)
        else:
            self.assertFalse(captured)
            self.assertLess(pool.total, before)
        self.assertEqual(caught, button == 8)

    def test_snapshot_hashes_ruleset_cache_without_running_query_callbacks(self):
        from ow174.game import native_replay
        from ow174.game.body_vm_snapshot import export_body_vm
        from ow174.game.script import runtime

        _, _, mech, _, matrix = self.session()
        script = mech.body_script
        self.assertEqual(matrix.ruleset_target_entity, mech.body)
        with (
            patch.object(runtime.Var, "value", side_effect=AssertionError("no evaluation")),
            patch.object(deflection, "matrix_distance", side_effect=AssertionError("no gate query")),
        ):
            before = native_replay.snapshot_hash(script)
            first = export_body_vm(script)
            self.assertEqual(export_body_vm(script), first)
            matrix.ruleset_target_entity = 0
            self.assertNotEqual(native_replay.snapshot_hash(script), before)
            self.assertNotEqual(export_body_vm(script), first)


    def test_intercepted_real_primary_ends_confirmed_copy_without_explosion(self):
        run, hog, mech, _, _ = self.session()
        self.step(run, hog, mech, fire=1)
        fight = run.match.combat
        rocket = next(r for r in fight.rockets if r.shooter is hog)
        # Explicit already-confirmed owner-copy fixture; flight/003 came from actual input.
        shot = projectiles.Shot(hog, hog.body, rocket.definition, run.frame, 1, 1, (1, 1))
        shot.created[0xA0001234] = projectiles._ProjectileArrival(received=True)
        fight.projectiles.shots.append(shot)
        rocket.shot = shot
        hog.client.entities.clear()
        fight._fly(rocket, seconds=0.1)
        self.assertNotIn(rocket, fight.rockets)
        self.assertNotIn(shot, fight.projectiles.shots)
        destroyed = [r for r in hog.client.entities if r.entity == 0xA0001234]
        self.assertEqual(len(destroyed), 1)
        self.assertIsNone(destroyed[0].ch2)
        self.assertEqual(self.health(run, mech), 20000)


class ProjectileCatalogTests(unittest.TestCase):
    def test_unknown_and_excluded_metadata_are_not_assumed_absorbable(self):
        with patch.object(deflection, "tags", return_value={
            0x04000000000003D4: frozenset(),
            0x04000000000008BE: frozenset({deflection.EXCLUDED_TAG}),
        }):
            self.assertTrue(deflection.absorbable(0x04000000000003D4))
            self.assertFalse(deflection.absorbable(0x04000000000008BE))
            self.assertFalse(deflection.absorbable(None))
            self.assertFalse(deflection.absorbable(123))

    def test_catalog_peek_never_reads_and_loaded_file_identity_cannot_change(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "tags.json"
            raw = json.dumps({"version": 1, "build": deflection.BUILD,
                              "entities": {"0x04000000000003D4": []}}).encode()
            path.write_bytes(raw)
            with (
                patch.object(deflection, "TAGS_PATH", path),
                patch.object(deflection, "TAGS_SHA256", hashlib.sha256(raw).hexdigest()),
                patch.object(deflection, "_loaded_tags", None),
            ):
                with patch.object(Path, "read_bytes", side_effect=AssertionError("peek must not read")):
                    self.assertIsNone(deflection.loaded_tags())
                table = deflection.tags()
                self.assertIs(deflection.loaded_tags(), table)
                path.write_bytes(b"changed")
                self.assertIs(deflection.tags(), table)
                with (
                    patch.object(deflection, "_loaded_tags", None),
                    self.assertRaisesRegex(ValueError, "changed after model"),
                ):
                    deflection.tags()

    def test_runtime_configuration_pins_external_catalog_content_identity(self):
        from ow174.game.replay_manifest import runtime_configuration

        key = "ow174.game.deflection.TAGS_SHA256"
        with patch.object(deflection, "TAGS_SHA256", "0" * 64):
            first = runtime_configuration(quanta_us=16000, run_options={})
        with patch.object(deflection, "TAGS_SHA256", "1" * 64):
            second = runtime_configuration(quanta_us=16000, run_options={})
        self.assertEqual(first[key], "0" * 64)
        self.assertEqual(second[key], "1" * 64)
        self.assertNotEqual(first, second)

    def test_server_export_observes_loaded_catalog_without_calling_loader(self):
        import test_server_snapshot as stored

        from ow174.game.server_snapshot import snapshot_digest

        case = stored.ServerSnapshotTests()
        session, _, _ = case.scene()
        table = {0x04000000000003D4: frozenset()}
        with (
            patch.object(deflection, "_loaded_tags", table),
            patch.object(deflection, "tags", side_effect=AssertionError("no loader")),
        ):
            first = snapshot_digest(case.capture(session))
            self.assertEqual(snapshot_digest(case.capture(session)), first)
            table[0x04000000000003D4] = frozenset({deflection.EXCLUDED_TAG})
            self.assertNotEqual(snapshot_digest(case.capture(session)), first)


if __name__ == "__main__":
    unittest.main()
