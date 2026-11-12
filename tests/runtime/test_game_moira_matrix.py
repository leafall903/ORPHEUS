"""Actual Moira volleys and0407 Matrix input; geometry/filter are reconstructed."""

import unittest
from dataclasses import replace
from unittest.mock import patch

from test_game_combat import KANEZAKA_DM, Run, aim

from ow174.game import absorption, deflection, moira
from ow174.game.collision import World
from ow174.game.content import find_hero, heroes
from ow174.game.mover import defined_mover_data
from ow174.game.script import graph

MECH = 0x0400000000000407


class MoiraMatrixTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        if graph.entity_body(MECH) is None or defined_mover_data(MECH) is None:
            raise unittest.SkipTest('requires external0407 graph/mover inputs')
        if any(key not in deflection.tags() for key in (moira.HEAL_ORB, moira.DAMAGE_ORB)):
            raise unittest.SkipTest('requires external Moira003 target tags')

    def fight(self, *, active=True, ally=False, offset=0, yaw=-32768):
        run = Run(KANEZAKA_DM, names=('Moira', 'Matrix', 'Recipient'))
        match = run.match
        match.bots.clear()
        match._pending_bot_scripts.clear()
        source, mech, recipient = run.players
        self.assertTrue(match.switch_hero(source, find_hero('moira')))
        self.assertTrue(match.switch_hero(mech, replace(find_hero('dva'), body=MECH)))
        source.body_script.component.set(12918, 0)
        mech.team = source.team if ally else 1 - source.team
        recipient.team = source.team
        if ally:
            match.game_map = replace(match.game_map, free_for_all=False)
        for player, position in ((source, [0, 1, 0]), (mech, [offset, 1, 15]),
                                 (recipient, [0, 1, 19])):
            player.mover.position = position
            player.mover.body = None
        for _ in range(100):
            self.step(run, active=False, yaw=yaw)
        self.step(run, active=active, yaw=yaw)
        instance = next(i for i in mech.body_script.component.instances.values()
                        if i.graph.index == 0x2CC)
        self.assertEqual(instance.state(3).active, active)
        targets = {t.owner: t for t in match.combat._targets()}
        health = [match.combat.vitals_of(targets[p]) for p in run.players]
        return run, instance, health

    @staticmethod
    def step(run, buttons=0, *, active=True, yaw=-32768):
        source, mech, _ = run.players
        run.step({source: (0, 0, buttons), mech: (yaw, 0, 2 if active else 0)})

    def launch(self, run, *, healing=False, active=True, yaw=-32768):
        self.step(run, 16, active=active, yaw=yaw)
        for _ in range(40):
            self.step(run, 1 if healing else 2, active=active, yaw=yaw)
            if run.match.combat.moira_orbs:
                orb = run.match.combat.moira_orbs[-1]
                self.assertEqual(orb.healing, healing)
                return orb
        self.fail('actual volley did not emit a Moira orb')

    def test_both_orbs_are_consumed_once_before_health_or_charge(self):
        for healing in (False, True):
            with self.subTest(healing=healing):
                run, matrix, health = self.fight()
                health[2].damage(80)
                orb = self.launch(run, healing=healing)
                before = [v.total for v in health]
                shot = orb.shot
                with patch.object(run.match.combat.projectiles, 'absorbed',
                                  wraps=run.match.combat.projectiles.absorbed) as ended:
                    for _ in range(65):
                        self.step(run)
                    if shot is not None:
                        ended.assert_called_once_with(shot)
                    else:
                        ended.assert_not_called()
                self.assertNotIn(orb, run.match.combat.moira_orbs)
                self.assertGreater(orb.energy, 0)
                self.assertGreater(orb.left, 0)
                self.assertEqual([v.total for v in health], before)
                self.assertEqual(run.players[0].body_script.component.entity_var(342).value(), 0)
                self.assertLess(matrix.vars[87].value(), 100)

    def test_matrix_protects_moira_from_actual_control_projectiles_and_helix(self):
        for hero, button, definition, damage in (
            ('ana', 8, 0x04000000000015E8, 5),
            ('mccree', 16, 0x040000000000037D, 25),
            ('soldier', 2, 0x040000000000072C, 120),
        ):
            for scenario in ('unprotected', 'matrix', 'wall', 'body_first', 'allied_matrix'):
                with self.subTest(hero=hero, scenario=scenario):
                    active = scenario != 'unprotected'
                    run, matrix, _ = self.fight(active=False)
                    source, mech, recipient = run.players
                    match = run.match
                    hero_definition = heroes()[0x02E0000000000042] if hero == 'mccree' else find_hero(hero)
                    self.assertTrue(match.switch_hero(source, hero_definition))
                    self.assertTrue(match.switch_hero(recipient, find_hero('moira')))
                    match.game_map = replace(match.game_map, free_for_all=False)
                    source.team, recipient.team = 0, 1
                    mech.team = source.team if scenario == 'allied_matrix' else recipient.team
                    mech_z = 13 if scenario in ('wall', 'body_first') else 5
                    recipient_z = 2 if scenario == 'body_first' else 6.5
                    positions = ([0, 1, 0], [1.1, 1, mech_z], [0, 1, recipient_z])
                    for player, position in zip(run.players, positions, strict=True):
                        player.mover.position = position
                        player.mover.body = None
                    for _ in range(30):
                        self.step(run, active=False)
                    self.step(run, active=active)
                    self.assertEqual(matrix.state(3).active, active)
                    fight = match.combat
                    target = next(target for target in fight._targets() if target.owner is recipient)
                    health = fight.vitals_of(target)
                    before = health.total
                    world = fight._collision()
                    if scenario == 'wall':
                        world = World([(-10, -5, 1, 10, -5, 1, 10, 10, 1),
                                       (-10, -5, 1, 10, 10, 1, -10, 10, 1)])
                    rocket = None
                    control = set()
                    with (patch.object(fight, '_collision', return_value=world),
                          patch.object(fight.projectiles, 'absorbed',
                                       wraps=fight.projectiles.absorbed) as ended,
                          patch.object(fight, '_landed', wraps=fight._landed) as landed):
                        for frame in range(65):
                            self.step(run, button if frame == 0 else 0, active=active)
                            if fight.rockets:
                                rocket = fight.rockets[0]
                            if fight.is_asleep(recipient):
                                control.add('sleep')
                            if fight.stuns.get(recipient.body, 0) > fight.tick:
                                control.add('flash')
                        self.assertIsNotNone(rocket)
                        self.assertEqual(rocket.definition, definition)
                        self.assertNotIn(rocket, fight.rockets)
                        if scenario == 'matrix':
                            landed.assert_not_called()
                            if rocket.shot is not None:
                                ended.assert_called_once_with(rocket.shot)
                        else:
                            ended.assert_not_called()
                            landed.assert_called_once_with(rocket)
                    impacted = scenario not in ('matrix', 'wall')
                    self.assertEqual(before - health.total, damage if impacted else 0)
                    expected_control = {'ana': {'sleep'}, 'mccree': {'flash'}}.get(hero, set()) if impacted else set()
                    self.assertEqual(control, expected_control)
                    if 'sleep' in expected_control:
                        self.assertAlmostEqual(health.sleep_until - health.sleep_wake_after, 4.5)
                    else:
                        self.assertEqual((health.sleep_until, health.sleep_wake_after), (0.0, 0.0))

    def test_ally_off_axis_and_aim_away_do_not_consume_orbs(self):
        for kwargs in ({'ally': True}, {'offset': 3}, {'yaw': 0}):
            for healing in (False, True):
                with self.subTest(kwargs=kwargs, healing=healing):
                    run, _, _ = self.fight(**kwargs)
                    orb = self.launch(run, healing=healing, yaw=kwargs.get('yaw', -32768))
                    # Aim-away boxes start beyond the defender. Check the
                    # incoming flight before the orb can cross to that side.
                    for _ in range(30):
                        self.step(run, yaw=kwargs.get('yaw', -32768))
                    self.assertIn(orb, run.match.combat.moira_orbs)

    def test_release_and_exhaustion_disable_interception(self):
        for exhausted in (False, True):
            with self.subTest(exhausted=exhausted):
                run, matrix, _ = self.fight()
                for _ in range(155 if exhausted else 40):
                    self.step(run, active=exhausted)
                self.assertFalse(matrix.state(3).active)
                orb = self.launch(run, active=exhausted)
                for _ in range(50):
                    self.step(run, active=exhausted)
                self.assertIn(orb, run.match.combat.moira_orbs)

    def test_live_ice_wall_and_matrix_resolve_by_first_contact(self):
        for healing in (False, True):
            for wall_first in (False, True):
                with self.subTest(healing=healing, wall_first=wall_first):
                    run, matrix, _ = self.fight(active=False)
                    source, mech, mei = run.players
                    self.assertTrue(run.match.switch_hero(mei, find_hero("mei")))
                    mei.mover.position = [0, 1, 25]
                    mei.mover.body = None
                    for _ in range(30):
                        self.step(run, active=False)
                    fight = run.match.combat
                    wall_aim = aim(fight._eye(mei), (0, 1, 3 if wall_first else 12))
                    for button in (16, 0, 1, 0):
                        run.step({mei: (*wall_aim, button)})
                    self.assertEqual(len(fight.walls.pieces), 5)
                    self.step(run)
                    self.assertTrue(matrix.state(3).active)
                    orb = self.launch(run, healing=healing)
                    energy = orb.energy
                    for _ in range(60):
                        if orb.bounces or orb not in fight.moira_orbs:
                            break
                        self.step(run)
                    self.assertEqual(orb in fight.moira_orbs, wall_first)
                    self.assertEqual(orb.bounces, int(wall_first))
                    self.assertEqual(orb.energy, energy)
                    self.assertIs(orb.shooter, source)
                    self.assertTrue(all(piece.health == 400 for piece in fight.walls.pieces.values()))

    def test_nearer_static_wall_bounces_orb_before_matrix(self):
        run, _, _ = self.fight()
        orb = self.launch(run)
        wall = World([(-10, -5, 2, 10, -5, 2, 10, 10, 2),
                      (-10, -5, 2, 10, 10, 2, -10, 10, 2)])
        with patch.object(run.match.combat, '_collision', return_value=wall):
            for _ in range(60):
                self.step(run)
        self.assertIn(orb, run.match.combat.moira_orbs)
        self.assertGreater(orb.bounces, 0)

    def test_unknown_tag_input_does_not_consume(self):
        run, _, _ = self.fight()
        orb = self.launch(run)
        with patch.object(deflection, 'tags', return_value={}):
            for _ in range(60):
                self.step(run)
        self.assertIn(orb, run.match.combat.moira_orbs)

    def test_active_matrix_does_not_block_grasp(self):
        run, matrix, health = self.fight()
        before = health[1].total
        for _ in range(30):
            self.step(run, 2)
        self.assertTrue(matrix.state(3).active)
        self.assertLess(health[1].total, before)

    def test_dead_or_replaced_mech_does_not_consume(self):
        for replace_body in (False, True):
            with self.subTest(replace_body=replace_body):
                run, _matrix, _ = self.fight()
                source, mech, _ = run.players
                if replace_body:
                    self.assertTrue(run.match.switch_hero(mech, find_hero('soldier')))
                else:
                    target = next(t for t in run.match.combat._targets() if t.owner is mech)
                    run.match.combat._damage(source, target, 1000, False, None, run.frame)
                # Retired state objects may retain their stored active bit;
                # admission must use live body ownership and health instead.
                self.assertIsNone(run.match.combat._matrix_hit(
                    source, run.match.combat._eye(source), (0, 0, 1), 20, moira.DAMAGE_ORB,
                ))
                orb = self.launch(run)
                for _ in range(50):
                    self.step(run)
                self.assertIn(orb, run.match.combat.moira_orbs)

    def test_nearest_of_simultaneous_matrix_and_grasp_consumes_both_orbs(self):
        for sigma_distance in (3, 10):
            for healing in (False, True):
                with self.subTest(sigma_distance=sigma_distance, healing=healing):
                    run, matrix, _ = self.fight(active=False)
                    source, mech, sigma = run.players
                    self.assertTrue(run.match.switch_hero(sigma, find_hero('sigma')))
                    sigma.team = 1 - source.team
                    sigma.mover.position = [0, 1, sigma_distance]
                    sigma.mover.body = None
                    for _ in range(100):
                        self.step(run, active=False)
                    run.step({mech: (-32768, 0, 2), sigma: (-32768, 0, 8)})
                    grasp = next(i for i in sigma.body_script.component.instances.values()
                                 if i.graph.index == absorption.GRASP)
                    self.assertTrue(grasp.state(1).active)
                    self.assertTrue(matrix.state(3).active)
                    origin = run.match.combat._eye(source)
                    sigma_hit = absorption.grasp_intersection(
                        run.match.combat, source, origin, (0, 0, 1), 30,
                    )[0]
                    matrix_hit = run.match.combat._matrix_hit(
                        source, origin, (0, 0, 1), 30, moira.DAMAGE_ORB,
                    )
                    self.assertEqual(sigma_hit < matrix_hit, sigma_distance == 3)

                    def step(buttons=0, run=run, source=source, mech=mech, sigma=sigma):
                        run.step({source: (0, 0, buttons), mech: (-32768, 0, 2),
                                  sigma: (-32768, 0, 0)})

                    step(16)
                    for _ in range(40):
                        step(1 if healing else 2)
                        if run.match.combat.moira_orbs:
                            break
                    self.assertTrue(run.match.combat.moira_orbs)
                    orb = run.match.combat.moira_orbs[-1]
                    with patch.object(absorption, 'absorbed', wraps=absorption.absorbed) as consumed:
                        for _ in range(50):
                            step()
                        if sigma_distance == 3:
                            consumed.assert_called_once_with(grasp.state(1), 0 if healing else 100)
                        else:
                            consumed.assert_not_called()
                    self.assertNotIn(orb, run.match.combat.moira_orbs)
                    expected = 100 if sigma_distance == 3 and not healing else 0
                    self.assertEqual(grasp.vars[21312].value(), expected)
                    # Healing has zero nominal amount. Observe actual Grasp's
                    # output, rather than treating a zero accumulator as proof.
                    if sigma_distance == 3:
                        self.assertLess(orb.position[2], 3)
                    else:
                        self.assertAlmostEqual(orb.position[2], matrix_hit, places=4)

    def test_nearest_matrix_or_deflect_wins_before_owner_transfer(self):
        for genji_distance in (2, 8):
            for healing in (False, True):
                with self.subTest(genji_distance=genji_distance, healing=healing):
                    run, matrix, _ = self.fight(active=False)
                    source, mech, genji = run.players
                    self.assertTrue(run.match.switch_hero(genji, find_hero('genji')))
                    genji.team = 1 - source.team
                    genji.mover.position = [0, 1, genji_distance]
                    genji.mover.body = None
                    for _ in range(100):
                        self.step(run, active=False)
                    run.step({mech: (-32768, 0, 2), genji: (-32768, 0, 16)})
                    deflect = next(i for i in genji.body_script.component.instances.values()
                                   if i.graph.index == 0x3A3)
                    self.assertTrue(deflect.state(10).active)
                    self.assertTrue(matrix.state(3).active)
                    # Subsequent idle Genji inputs still need his facing aim.
                    def step(buttons=0, run=run, source=source, mech=mech, genji=genji):
                        run.step({source: (0, 0, buttons), mech: (-32768, 0, 2),
                                  genji: (-32768, 0, 0)})

                    step(16)
                    for _ in range(40):
                        step(1 if healing else 2)
                        if run.match.combat.moira_orbs:
                            break
                    self.assertTrue(run.match.combat.moira_orbs)
                    orb = run.match.combat.moira_orbs[-1]
                    for _ in range(40):
                        step()
                    if genji_distance == 2:
                        self.assertIn(orb, run.match.combat.moira_orbs)
                        self.assertIs(orb.shooter, genji)
                        self.assertEqual(orb.body, genji.body)
                        self.assertLess(orb.direction[2], 0)
                    else:
                        self.assertNotIn(orb, run.match.combat.moira_orbs)
                        self.assertIs(orb.shooter, source)

    def test_reflected_orb_rechecks_matrix_allegiance_in_the_contact_slice(self):
        for healing in (False, True):
            with self.subTest(healing=healing):
                run, matrix, _ = self.fight(active=False)
                source, mech, genji = run.players
                run.match.game_map = replace(run.match.game_map, free_for_all=False)
                mech.team = source.team
                self.assertTrue(run.match.switch_hero(genji, find_hero('genji')))
                genji.team = 1 - source.team
                for player, distance in ((mech, 2), (genji, 8)):
                    player.mover.position = [0, 1, distance]
                    player.mover.body = None
                for _ in range(100):
                    self.step(run, active=False, yaw=0)
                run.step({mech: (0, 0, 2), genji: (-32768, 0, 16)})
                self.assertTrue(matrix.state(3).active)

                def step(buttons=0, run=run, source=source, mech=mech, genji=genji):
                    run.step({source: (0, 0, buttons), mech: (0, 0, 2), genji: (-32768, 0, 0)})

                step(16)
                for _ in range(40):
                    step(1 if healing else 2)
                    if run.match.combat.moira_orbs:
                        break
                self.assertTrue(run.match.combat.moira_orbs)
                orb = run.match.combat.moira_orbs[-1]
                self.assertIs(orb.shooter, source)
                reflected = []
                original = run.match.combat._matrix_hit

                def observe(shooter, *args, original=original, genji=genji, reflected=reflected, run=run):
                    hit = original(shooter, *args)
                    if shooter is genji and hit is not None:
                        reflected.append((run.frame, hit))
                    return hit

                with patch.object(run.match.combat, '_matrix_hit', side_effect=observe):
                    for _ in range(60):
                        step()
                        if orb not in run.match.combat.moira_orbs:
                            break
                self.assertNotIn(orb, run.match.combat.moira_orbs)
                self.assertIs(orb.shooter, genji)
                self.assertEqual(orb.body, genji.body)
                self.assertTrue(reflected)
                self.assertAlmostEqual(reflected[0][1], 0, places=6)
                self.assertEqual(reflected[0][0], run.frame)


if __name__ == '__main__':
    unittest.main()
