"""Actual Trooper, Sniper and Heavy weapon graphs against Reinhardt."""

import gzip
import json
import os
import unittest
from dataclasses import replace
from unittest.mock import patch

import test_game_reinhardt as fixtures
import test_reinhardt_mei_wall as ice_wall
from test_game_combat import aim
from test_reinhardt_deflection import MECH_INPUTS

from ow174.game import combat, defenses, deflection, mover, npc_control, pools, sigma_grasp, zarya_barrier
from ow174.game.content import heroes
from ow174.game.script import expr, graph


@unittest.skipUnless((fixtures.NPC_DATA / 'pve_npcs_174.json').is_file()
                     and fixtures.NPC_MOVERS.is_file() and fixtures.BARRIER_DATA.is_file(),
                     'authorized NPC data, authored movers and barrier descriptor required')
class ReinhardtNpcIncomingTests(unittest.TestCase):
    advance = fixtures.HammerTests.advance
    place = staticmethod(fixtures.HammerTests.place)
    scenario = fixtures.TalonInteractionTests.scenario
    tap = ice_wall.ReinhardtIceWallTests.tap

    @classmethod
    def setUpClass(cls):
        fixtures.TalonInteractionTests.setUpClass()
        cls.catalog = fixtures.TalonInteractionTests.catalog

    @classmethod
    def tearDownClass(cls):
        fixtures.TalonInteractionTests.tearDownClass()

    def setUp(self):
        self.enterContext(patch.dict(os.environ, {
            'ORPHEUS_REINHARDT_COLLISION': str(fixtures.BARRIER_DATA)}))

    def samples(self):
        expected = {'Trooper': (15, 20), 'Sniper': (95, 100), 'Heavy Assault': (2.5, 5)}
        rows = [(item, *expected[item.name]) for item in self.catalog.values() if item.name in expected]
        self.assertEqual({item.name for item, _, _ in rows}, set(expected))
        self.assertEqual(len(rows), 3)
        return rows

    def arm(self, actor, action='primary'):
        self.rein.mover.yaw, self.rein.mover.pitch = aim(
            self.match.combat._eye(self.rein), (actor.position[0], actor.position[1] + 1, actor.position[2]))
        self.assertEqual(npc_control.start(self.match, self.rein, action=action), actor.entity)

    def victim(self):
        target = next(t for t in self.match.combat._targets() if t.owner is self.rein)
        return self.match.combat.vitals_of(target)

    def test_talon_rays_deflect_from_the_front_and_hit_genji_from_behind(self):
        self.check_talon_ray_defense(heroes()[deflection.GENJI], 0x03A3, 10, 16, 0)

    def test_talon_rays_are_consumed_by_defense_matrix(self):
        self.mount_mech_inputs()
        mech = replace(heroes()[deflection.DVA], body=deflection.DVA_MECH)
        self.check_talon_ray_defense(mech, 0x02CC, 3, 2, 1)

    def test_talon_rays_are_converted_by_kinetic_grasp(self):
        self.check_talon_ray_defense(heroes()[sigma_grasp.SIGMA], sigma_grasp.GRAPH, 1, 8, .75)

    def test_deflected_talon_rays_can_be_consumed_by_the_original_shooters_ally(self):
        for definition, _, damage in self.samples():
            if definition.name == 'Sniper':
                continue
            with self.subTest(weapon=definition.name):
                actor, _, source_health = self.scenario(definition, (0, 1, 8))
                fight = self.match.combat
                self.match.game_map = replace(self.match.game_map, free_for_all=False)
                self.assertTrue(self.match.switch_hero(self.enemy, heroes()[deflection.GENJI]))
                self.assertTrue(self.match.switch_hero(self.other, heroes()[sigma_grasp.SIGMA]))
                self.rein.team = self.enemy.team = 0
                self.other.team = actor.team
                self.advance(100, buttons=0)
                self.arm(actor)
                self.place(self.enemy, (0, 1, 3.5))
                self.place(self.other, (.75, 1, 6))
                angles = aim(fight._eye(self.enemy), (0, 2, 8))
                self.run.step({self.enemy: (*angles, 16), self.other: (-32768, 0, 8)})
                grasp = defenses.active_state(self.other, sigma_grasp.SIGMA, sigma_grasp.GRAPH, 1)
                self.assertIsNotNone(grasp)
                source_before = source_health.total
                with patch.object(fight, '_shoot', wraps=fight._shoot) as shoot:
                    for _ in range(95):
                        self.run.step({self.enemy: (*angles, 0), self.other: (-32768, 0, 0)})
                        if shoot.called:
                            break
                self.assertEqual(shoot.call_count, 1)
                self.assertEqual(grasp.instance.vars[21312].value(), damage)
                self.assertEqual(source_health.total, source_before)
                self.assertEqual(self.victim().total, 500)
                self.assertFalse(self.enemy.client.messages(combat.HIT))
                self.assertEqual(npc_control.stop(self.match), 1)

    def test_talon_rays_stop_at_wall_or_barrier_before_active_grasp(self):
        for definition, _, damage in self.samples():
            if definition.name == 'Sniper':
                continue
            for wall in (False, True):
                with self.subTest(weapon=definition.name, wall=wall):
                    actor, _, _ = self.scenario(definition, (0, 1, 8))
                    fight = self.match.combat
                    self.match.game_map = replace(self.match.game_map, free_for_all=False)
                    self.assertTrue(self.match.switch_hero(self.enemy, heroes()[sigma_grasp.SIGMA]))
                    self.assertTrue(self.match.switch_hero(self.other, self.rein.hero))
                    self.rein.team = self.enemy.team = self.other.team = 0
                    self.advance(100, buttons=0)
                    self.arm(actor)
                    self.place(self.enemy, (.75, 1, 3))
                    if not wall:
                        self.place(self.other, (0, 1, 6))
                    self.run.step({self.enemy: (0, 0, 8), self.other: (0, 0, 2)})
                    state = defenses.active_state(self.enemy, sigma_grasp.SIGMA, sigma_grasp.GRAPH, 1)
                    self.assertIsNotNone(state)
                    resolve = fight._shoot

                    def release(*args, **kwargs):
                        # Introduce collision after the NPC's line-of-sight admission.
                        if wall:
                            fixtures.charge_wall(self, 6)
                        return resolve(*args, **kwargs)

                    with patch.object(fight, '_shoot', side_effect=release) as shoot:
                        for _ in range(95):
                            self.run.step({self.other: (0, 0, 2)})
                            if shoot.called:
                                break
                    self.assertEqual(shoot.call_count, 1)
                    self.assertTrue(state.active)
                    self.assertEqual(state.instance.vars[21312].value(), 0)
                    self.assertEqual(self.victim().total, 500)
                    self.assertEqual(fight.barriers[self.other.body].current, 1600 if wall else 1600 - damage)
                    self.assertEqual(npc_control.stop(self.match), 1)

    def check_talon_ray_defense(self, hero, graph_index, state_index, button, lateral):
        reflects = hero.guid == deflection.GENJI
        rays = {
            'Trooper': (0x102E, 60, deflection.TROOPER_RIFLE),
            'Heavy Assault': (0x114F, 47, deflection.HEAVY_CANNON),
        }
        for definition, armour_loss, damage in self.samples():
            if definition.name not in rays:
                continue
            weapon, node, entity = rays[definition.name]
            for intercepts in (False, True):
                with self.subTest(weapon=definition.name, intercepts=intercepts):
                    actor, source, source_health = self.scenario(definition, (0, 1, 8))
                    fight = self.match.combat
                    self.match.game_map = replace(self.match.game_map, free_for_all=False)
                    self.assertTrue(self.match.switch_hero(self.enemy, hero))
                    self.rein.team = self.enemy.team = 0
                    self.advance(100, buttons=0)
                    self.arm(actor)
                    self.place(self.enemy, (lateral, 1, 4))
                    angles = (0, 0)
                    if reflects:
                        angles = aim(fight._eye(self.enemy), (0, 2, 8)) if intercepts else (-32768, 0)
                    self.run.step({self.enemy: (*angles, button if intercepts or reflects else 0)})
                    receiver = fight.vitals_of(fight._target_entity(self.enemy.body))
                    receiver_before, source_before = receiver.total, source_health.total
                    state = defenses.active_state(self.enemy, hero.guid, graph_index, state_index)
                    self.assertEqual(state is not None, intercepts or reflects)
                    with patch.object(fight, '_shoot', wraps=fight._shoot) as shoot:
                        for _ in range(95):
                            self.run.step({self.enemy: (*angles, 2 if intercepts and button == 2 else 0)})
                            if shoot.called:
                                break
                    self.assertEqual(shoot.call_count, 1)
                    shooter, _, shot, _, _ = shoot.call_args.args
                    self.assertIs(shooter, actor)
                    self.assertEqual((shot.instance.graph.index, shot.node.pos), (weapon, node))
                    projectile = shot.node.field("m_projectileEntity")["m_entityDef"]
                    self.assertEqual(shot.evaluate(projectile, watch=False).guid, entity)
                    self.assertFalse(fight.rockets)
                    self.assertEqual(self.victim().total, 500 if intercepts or reflects else 500 - armour_loss)
                    self.assertEqual(receiver.total, receiver_before - damage if reflects and not intercepts
                                     else receiver_before)
                    self.assertEqual(npc_control.stop(self.match), 1)
                    if reflects and intercepts:
                        self.assertLess(source_health.total, source_before)
                        [hit] = self.enemy.client.messages(combat.HIT)
                        self.assertEqual(hit["+0x78"]["+0x0"]["+0x0"], source.entity)
                    else:
                        self.assertEqual(source_health.total, source_before)
                    if hero.guid == sigma_grasp.SIGMA and intercepts:
                        self.assertEqual(state.instance.vars[21312].value(), damage)
                        self.advance(140, buttons=0)
                        self.assertFalse(state.active)
                        self.assertAlmostEqual(receiver.grasp_shields, damage * .6, places=4)

    def test_sniper_alternate_hits_then_switches_back_to_primary(self):
        definition = next(item for item in self.catalog.values() if item.name == 'Sniper')
        actor, _, _ = self.scenario(definition, (0, 1, 8))
        self.advance(20, buttons=0)
        component = actor.body_script.component
        primary, alternate = (next(i for i in component.instances.values() if i.graph.index == graph)
                              for graph in (0x1122, 0x1134))
        self.assertEqual(component.entity_var(31).value(), 1)
        self.assertEqual(component.world.ai_weapon_button(0), 20)
        health = self.victim()
        self.arm(actor, 'alternate')
        for _ in range(220):
            self.advance(1, buttons=0)
            if health.total < 500:
                break
        self.assertEqual(component.entity_var(31).value(), 2)
        self.assertEqual(primary.state(4).counter, 0)
        self.assertEqual(alternate.state(4).counter, 1)
        self.assertEqual(health.total, 497.5)
        self.assertEqual((primary.vars[53].value(), alternate.vars[53].value()), (20, 19))
        self.arm(actor)
        for _ in range(220):
            self.advance(1, buttons=0)
            if health.total < 497.5:
                break
        self.assertEqual(component.entity_var(31).value(), 1)
        self.assertEqual(primary.state(4).counter, 1)
        self.assertEqual(alternate.state(4).counter, 1)
        self.assertEqual(health.total, 402.5)
        self.assertEqual((primary.vars[53].value(), alternate.vars[53].value()), (19, 19))

    def test_sniper_primary_restarts_aim_after_target_departure(self):
        self.check_sniper_primary_target_change(departure=True)

    def test_sniper_primary_restarts_aim_after_manual_retarget(self):
        self.check_sniper_primary_target_change(departure=False)

    def check_sniper_primary_target_change(self, *, departure):
        definition = next(item for item in self.catalog.values() if item.name == 'Sniper')
        actor, _, _ = self.scenario(definition, (0, 1, 8))
        self.advance(20, buttons=0)
        self.arm(actor)
        old_health = self.victim()
        fight = self.match.combat
        rifle = next(i for i in actor.body_script.component.instances.values() if i.graph.index == 0x1122)
        scope, charge, volley = (rifle.node_state(rifle.graph.node(pos)) for pos in (34, 53, 6))
        for _ in range(150):
            self.advance(1, buttons=0)
            if charge.active and charge.cur >= .35:
                break
        self.assertTrue(scope.active and charge.active)
        self.assertEqual(volley.counter, 0)
        self.assertTrue(self.match.switch_hero(self.other, self.rein.hero))
        self.place(self.other, (0, 1, 2))
        new_health = fight.vitals_of(fight._target_entity(self.other.body))
        if departure:
            self.match.leave(self.rein)
            self.run.players.remove(self.rein)
        else:
            self.other.mover.yaw, self.other.mover.pitch = aim(fight._eye(self.other), (0, 2, 8))
            self.assertEqual(npc_control.start(self.match, self.other), actor.entity)
        self.assertFalse(scope.active)
        self.assertFalse(charge.active)
        self.assertFalse(actor.attack_input)
        self.run.step({})
        self.assertEqual(actor.attack_target, self.other.entity)
        for _ in range(150):
            if charge.active:
                break
            self.assertEqual(volley.counter, 0)
            self.run.step({})
        self.assertTrue(charge.active)
        self.assertLess(charge.cur, .1)
        for _ in range(170):
            self.run.step({})
            if new_health.total < 500:
                break
        self.assertEqual(volley.counter, 1)
        self.assertEqual(new_health.total, 405)
        self.assertEqual(old_health.total, 500)

    def test_sniper_alternate_releases_on_target_body_replacement(self):
        definition = next(item for item in self.catalog.values() if item.name == 'Sniper')
        actor, _, _ = self.scenario(definition, (0, 1, 8))
        self.advance(20, buttons=0)
        self.arm(actor, 'alternate')
        old_health = self.victim()
        component = actor.body_script.component
        alternate = next(i for i in component.instances.values() if i.graph.index == 0x1134)
        fire, volley = (alternate.node_state(alternate.graph.node(pos)) for pos in (35, 6))
        for _ in range(150):
            self.advance(1, buttons=0)
            if old_health.total < 500:
                break
        self.assertEqual(old_health.total, 497.5)
        self.assertTrue(actor.attack_input)
        self.assertIn(20, component.held)
        self.assertEqual(volley.counter, 1)
        ammo = alternate.vars[53].value()
        self.assertTrue(self.match.switch_hero(self.rein, self.rein.hero))
        self.assertFalse(fire.active)
        self.assertFalse(actor.attack_input)
        self.assertEqual(alternate.vars[53].value(), ammo)
        self.place(self.rein, (0, 1, 2))
        new_health = self.victim()
        self.assertIsNot(old_health, new_health)
        for _ in range(150):
            self.advance(1, buttons=0)
            if new_health.total < 500:
                break
        self.assertEqual(component.entity_var(31).value(), 2)
        self.assertEqual(volley.counter, 2)
        self.assertEqual(new_health.total, 497.5)
        self.assertEqual(old_health.total, 497.5)

    def test_sniper_rifle_resolves_defences_raised_after_launch(self):
        for keep_field in (True, False):
            with self.subTest(keep_field=keep_field):
                definition = next(item for item in self.catalog.values() if item.name == 'Sniper')
                actor, _, _ = self.scenario(definition, (0, 1, 36))
                fight = self.match.combat
                self.match.game_map = replace(self.match.game_map, free_for_all=False)
                self.rein.team, self.other.team, actor.team = 0, 0, 1
                self.assertTrue(self.match.switch_hero(self.other, heroes()[zarya_barrier.HERO]))
                self.place(self.other, (3, 1, 0))
                self.advance(20, buttons=0)
                self.arm(actor)
                rifle = next(i for i in actor.body_script.component.instances.values() if i.graph.index == 0x1122)
                charge, volley = (rifle.node_state(rifle.graph.node(pos)) for pos in (53, 6))
                for _ in range(150):
                    self.advance(1, buttons=0)
                    if charge.active and charge.cur >= .35:
                        break
                self.assertTrue(charge.active)
                self.assertGreaterEqual(charge.cur, .35)
                self.assertEqual(volley.counter, 0)
                shield = fight.barriers[self.rein.body]
                health = self.victim()
                self.assertFalse(shield.active)
                self.assertNotIn(self.rein.body, fight.zarya_bubbles)

                for _ in range(120):
                    self.advance(1, buttons=0)
                    if fight.rockets:
                        break
                [shot] = fight.rockets
                self.assertIs(shot.shooter, actor)
                self.assertEqual(volley.counter, 1)
                self.assertEqual(health.total, 500)
                self.assertFalse(shield.active)

                angles = aim(fight._eye(self.other), (0, 2.3, 0))
                buttons = 2 if keep_field else 0
                self.run.step({self.other: (*angles, 16), self.rein: (0, 0, buttons)})
                self.assertIn(shot, fight.rockets)
                bubble = fight.zarya_bubbles[self.rein.body]
                self.assertEqual(bubble.health, 200)
                for _ in range(70):
                    self.advance(1, buttons=buttons)
                    if shot not in fight.rockets:
                        break
                self.assertNotIn(shot, fight.rockets)
                self.assertEqual(volley.counter, 1)
                self.assertEqual(shield.active, keep_field)
                self.assertEqual(shield.current, 1500 if keep_field else 1600)
                self.assertEqual(bubble.health, 200 if keep_field else 100)
                energy = self.other.body_script.component.entity_var(87).value() or 0
                self.assertAlmostEqual(energy, 0 if keep_field else 20, places=4)
                self.assertEqual(health.total, 500)
                self.assertEqual(npc_control.stop(self.match), 1)

    def test_sniper_rifle_resolves_ice_wall_placed_after_launch(self):
        definition = next(item for item in self.catalog.values() if item.name == 'Sniper')
        for cancel_wall in (False, True):
            with self.subTest(cancel_wall=cancel_wall):
                actor, _, _ = self.scenario(definition, (0, 1, 36))
                fight = self.match.combat
                self.assertTrue(self.match.switch_hero(self.enemy, heroes()[defenses.MEI]))
                self.enemy.team = self.rein.team
                self.place(self.enemy, (0, 1, -4))
                self.advance(20, buttons=2)
                field = fight.barriers[self.rein.body]
                health = self.victim()
                self.arm(actor)
                rifle = next(i for i in actor.body_script.component.instances.values()
                             if i.graph.index == 0x1122)
                volley = rifle.node_state(rifle.graph.node(6))
                for _ in range(270):
                    self.advance(1, buttons=2)
                    if fight.rockets:
                        break
                [shot] = fight.rockets
                self.assertIs(shot.shooter, actor)
                self.assertEqual(volley.counter, 1)
                self.assertFalse(fight.walls.pieces)
                self.assertTrue(field.active)
                self.assertEqual(field.current, 1600)

                angles = aim(fight._eye(self.enemy), (0, 1, 4))
                for mei_buttons in (16, 0, 1, 0):
                    self.run.step({self.enemy: (*angles, mei_buttons), self.rein: (0, 0, 2)})
                    self.assertTrue(field.active)
                pieces = list(fight.walls.pieces.values())
                self.assertEqual(len(pieces), 5)
                centre = pieces[2]
                self.assertEqual(centre.health, 400)
                self.assertIn(shot, fight.rockets)
                if cancel_wall:
                    self.run.step({self.enemy: (*angles, 16), self.rein: (0, 0, 2)})
                    self.assertFalse(fight.walls.pieces)
                    self.assertIn(shot, fight.rockets)

                for _ in range(70):
                    self.advance(1, buttons=2)
                    if shot not in fight.rockets:
                        break
                self.assertNotIn(shot, fight.rockets)
                self.assertEqual(volley.counter, 1)
                self.assertTrue(field.active)
                self.assertEqual(field.current, 1500 if cancel_wall else 1600)
                self.assertEqual(centre.health, 400 if cancel_wall else 300)
                self.assertEqual(health.total, 500)
                self.assertEqual(npc_control.stop(self.match), 1)

    def test_genji_deflects_actual_sniper_rifle_only_from_the_front(self):
        self.check_genji_sniper_deflect("primary", deflection.SNIPER_RIFLE, 100)

    def test_genji_deflects_actual_sniper_sidearm_only_from_the_front(self):
        self.check_genji_sniper_deflect("alternate", deflection.SNIPER_SIDEARM, 5)

    def launch_sniper_round(self, actor, action, projectile):
        fight = self.match.combat
        self.arm(actor, action)
        for _ in range(270):
            self.advance(1, buttons=0)
            if fight.rockets:
                break
        [shot] = fight.rockets
        self.assertIs(shot.shooter, actor)
        self.assertEqual(shot.definition, projectile)
        return shot

    def check_genji_sniper_deflect(self, action, projectile, damage):
        definition = next(item for item in self.catalog.values() if item.name == 'Sniper')
        for frontal in (True, False):
            with self.subTest(frontal=frontal):
                actor, target, sniper_health = self.scenario(definition, (0, 1, 36))
                fight = self.match.combat
                self.match.game_map = replace(self.match.game_map, free_for_all=False)
                self.assertTrue(self.match.switch_hero(self.enemy, heroes()[deflection.GENJI]))
                self.rein.team = self.enemy.team = 0
                self.advance(20, buttons=0)
                shot = self.launch_sniper_round(actor, action, projectile)

                self.place(self.enemy, (0, 1, 12))
                angles = aim(fight._eye(self.enemy), (0, 2, 36)) if frontal else (-32768, 0)
                self.run.step({self.enemy: (*angles, 16)})
                state = defenses.active_state(self.enemy, deflection.GENJI, 0x03A3, 10)
                self.assertIsNotNone(state)
                genji = fight.vitals_of(fight._target_entity(self.enemy.body))
                initial_genji = genji.total
                initial_sniper = sniper_health.total
                for _ in range(60):
                    self.run.step({self.enemy: (*angles, 0)})
                    if shot not in fight.rockets:
                        break
                self.assertNotIn(shot, fight.rockets)
                self.assertEqual(self.victim().total, 500)
                self.assertEqual(shot.reflected, frontal)
                self.assertEqual(genji.total, initial_genji if frontal else initial_genji - damage)
                self.assertEqual(sniper_health.total, initial_sniper - damage if frontal else initial_sniper)
                if frontal:
                    self.assertIs(shot.shooter, self.enemy)
                    self.assertEqual(shot.team, self.enemy.team)
                    [hit] = self.enemy.client.messages(combat.HIT)
                    self.assertEqual(hit["+0x78"]["+0x0"]["+0x0"], target.entity)
                self.assertEqual(npc_control.stop(self.match), 1)

    def mount_mech_inputs(self):
        bundle = MECH_INPUTS / "mech-supplement.json.gz"
        movers = MECH_INPUTS / "mech-movers.json"
        if not bundle.is_file() or not movers.is_file():
            self.skipTest("authored D.Va mech graph and mover supplements required")
        base = graph._data()
        supplement = json.loads(gzip.decompress(bundle.read_bytes()))
        records = {row["index"]: row for row in supplement["graphs"]}
        records.update((row["index"], row) for row in base["graphs"])
        entities = {**base["entities"], **supplement["entities"]}
        self.enterContext(patch.object(graph, "_data", return_value={
            **base, "graphs": list(records.values()), "entities": entities}))
        mech = json.loads(movers.read_text(encoding="utf-8"))[f"0x{deflection.DVA_MECH:016X}"]
        self.enterContext(patch.object(
            mover, "_mover_table", {**mover._movers(), deflection.DVA_MECH: mover.MoverData(**mech)}))
        for cached in (graph.graph, graph._records, pools.hero_pools):
            cached.cache_clear()
            self.addCleanup(cached.cache_clear)

    def test_actual_sniper_rifle_is_absorbed_by_kinetic_grasp(self):
        self.check_sniper_absorber(heroes()[sigma_grasp.SIGMA], sigma_grasp.GRAPH, 1, 8,
                                   "primary", deflection.SNIPER_RIFLE, 100, 95)

    def test_actual_sniper_rifle_is_absorbed_by_defense_matrix(self):
        self.mount_mech_inputs()
        mech = replace(heroes()[deflection.DVA], body=deflection.DVA_MECH)
        self.check_sniper_absorber(mech, 0x02CC, 3, 2, "primary", deflection.SNIPER_RIFLE, 100, 95)

    def test_actual_sniper_sidearm_is_absorbed_by_kinetic_grasp(self):
        self.check_sniper_absorber(heroes()[sigma_grasp.SIGMA], sigma_grasp.GRAPH, 1, 8,
                                   "alternate", deflection.SNIPER_SIDEARM, 5, 2.5)

    def test_actual_sniper_sidearm_is_absorbed_by_defense_matrix(self):
        self.mount_mech_inputs()
        mech = replace(heroes()[deflection.DVA], body=deflection.DVA_MECH)
        self.check_sniper_absorber(mech, 0x02CC, 3, 2, "alternate", deflection.SNIPER_SIDEARM, 5, 2.5)

    def check_sniper_absorber(self, hero, graph_index, state_index, button, action, projectile, damage, armour_loss):
        definition = next(item for item in self.catalog.values() if item.name == 'Sniper')
        for active in (False, True):
            with self.subTest(active=active):
                actor, _, _ = self.scenario(definition, (0, 1, 36))
                fight = self.match.combat
                self.match.game_map = replace(self.match.game_map, free_for_all=False)
                self.assertTrue(self.match.switch_hero(self.enemy, hero))
                self.rein.team = self.enemy.team = 0
                self.advance(100, buttons=0)
                shot = self.launch_sniper_round(actor, action, projectile)

                lateral = .75 if hero.guid == sigma_grasp.SIGMA else 1
                self.place(self.enemy, (lateral, 1, 12))
                self.run.step({self.enemy: (0, 0, button if active else 0)})
                defender = fight.vitals_of(fight._target_entity(self.enemy.body))
                initial_health = defender.total
                state = defenses.active_state(self.enemy, hero.guid, graph_index, state_index)
                self.assertEqual(state is not None, active)
                for _ in range(60):
                    self.run.step({self.enemy: (0, 0, 2 if active and button == 2 else 0)})
                    if shot not in fight.rockets:
                        break
                self.assertNotIn(shot, fight.rockets)
                self.assertFalse(shot.reflected)
                self.assertEqual(defender.total, initial_health)
                self.assertEqual(self.victim().total, 500 if active else 500 - armour_loss)
                self.assertEqual(npc_control.stop(self.match), 1)
                if active and hero.guid == sigma_grasp.SIGMA:
                    self.assertEqual(state.instance.vars[21312].value(), damage)
                    self.advance(140, buttons=0)
                    self.assertFalse(state.active)
                    self.assertAlmostEqual(defender.grasp_shields, damage * .6, places=4)

    def test_sniper_alternate_barrier_and_occlusion(self):
        definition = next(item for item in self.catalog.values() if item.name == 'Sniper')
        for blocked in (False, True):
            with self.subTest(blocked=blocked):
                actor, _, _ = self.scenario(definition, (0, 1, 8))
                self.advance(20, buttons=2)
                self.arm(actor, 'alternate')
                if blocked:
                    fixtures.charge_wall(self, 4)
                barrier = self.match.combat.barriers[self.rein.body]
                for _ in range(150):
                    self.advance(1, buttons=2)
                    if barrier.current < 1600:
                        break
                self.assertEqual(barrier.current, 1600 if blocked else 1595)
                self.assertEqual(self.victim().total, 500)
                npc_control.stop(self.match)
                self.advance(45, buttons=2)
                self.assertEqual(barrier.current, 1600 if blocked else 1595)
                self.assertFalse(actor.body_script.component.held)

    def test_sniper_alternate_breaks_barrier_then_hits_armour(self):
        definition = next(item for item in self.catalog.values() if item.name == 'Sniper')
        actor, _, _ = self.scenario(definition, (0, 1, 8))
        self.advance(20, buttons=2)
        barrier = self.match.combat.barriers[self.rein.body]
        barrier.current = 5  # one alternate hit from breaking
        self.arm(actor, 'alternate')
        health = self.victim()
        for _ in range(150):
            self.advance(1, buttons=2)
            if not barrier.active:
                break
        self.assertEqual(barrier.current, 0)
        self.assertFalse(barrier.active)
        self.assertGreater(barrier.broken_until, self.match.combat.now)
        self.assertEqual(health.total, 500)
        for _ in range(150):
            self.advance(1, buttons=2)
            if health.total < 500:
                break
        self.assertEqual(health.total, 497.5)
        self.assertFalse(barrier.active)

    def test_actual_shatter_stops_sniper_alternate_then_fire_recovers(self):
        definition = next(item for item in self.catalog.values() if item.name == 'Sniper')
        actor, _, _ = self.scenario(definition, (0, 1, 8))
        self.advance(20, buttons=0)
        self.arm(actor, 'alternate')
        health = self.victim()
        for _ in range(150):
            self.advance(1, buttons=0)
            if health.total < 500:
                break
        self.assertEqual(health.total, 497.5)
        self.rein.body_script.set_ult_charge(1540)
        self.advance(1, buttons=32)
        self.advance(60, buttons=0)
        self.assertTrue(self.match.combat.controlled(actor.entity))
        self.assertFalse(actor.attack_input)
        after_shatter = health.total
        self.advance(70, buttons=0)
        self.assertTrue(self.match.combat.controlled(actor.entity))
        self.assertEqual(health.total, after_shatter)
        for _ in range(180):
            self.advance(1, buttons=0)
            if health.total < after_shatter:
                break
        self.assertFalse(self.match.combat.controlled(actor.entity))
        self.assertEqual(health.total, after_shatter - 2.5)
        self.assertEqual(actor.body_script.component.entity_var(31).value(), 2)

    def test_sniper_alternate_allied_and_out_of_range_targets_never_fire(self):
        definition = next(item for item in self.catalog.values() if item.name == 'Sniper')
        for reason in ('allied', 'range'):
            with self.subTest(reason=reason):
                actor, _, _ = self.scenario(definition, (0, 1, 8))
                self.advance(20, buttons=0)
                self.arm(actor, 'alternate')
                if reason == 'allied':
                    self.match.game_map = self.match.game_map.__class__(
                        **{**self.match.game_map.__dict__, 'free_for_all': False})
                    self.rein.team = actor.team
                else:
                    self.place(self.rein, (0, 1, -101))
                self.advance(100, buttons=0)
                component = actor.body_script.component
                self.assertEqual(self.victim().total, 500)
                self.assertEqual(component.entity_var(31).value(), 1)
                self.assertFalse(actor.attack_input)
                self.assertFalse(component.held)
                if reason == 'allied':
                    self.rein.team = 1 - actor.team
                else:
                    self.place(self.rein, (0, 1, 0))
                for _ in range(150):
                    self.advance(1, buttons=0)
                    if self.victim().total < 500:
                        break
                self.assertEqual(component.entity_var(31).value(), 2)
                self.assertEqual(self.victim().total, 497.5)

    def test_trooper_reloads_until_barrier_breaks_and_recovered_barrier_blocks_again(self):
        definition = next(item for item in self.catalog.values() if item.name == 'Trooper')
        actor, _, _ = self.scenario(definition, (0, 1, 8))
        self.advance(20, buttons=2)
        barrier = self.match.combat.barriers[self.rein.body]
        self.arm(actor)
        component = actor.body_script.component
        weapon = next(i for i in component.instances.values() if i.graph.index == 0x102E)
        ammo, wait = weapon.vars[53], weapon.state(18)
        self.assertEqual(ammo.value(), 30)

        for _ in range(1500):
            self.advance(1, buttons=2)
            if ammo.value() == 0:
                break
        self.assertEqual(ammo.value(), 0)
        self.assertEqual(barrier.current, 1000)
        self.assertEqual(self.victim().total, 500)
        for _ in range(3):
            if wait.active:
                break
            self.advance(1, buttons=2)
        self.assertTrue(wait.active)
        self.assertEqual(wait.ms, 910)
        deadline = wait.start + wait.ms
        while (self.run.frame + 2) * component.frame_ms - 1 < deadline:
            self.advance(1, buttons=2)
            self.assertEqual(ammo.value(), 0)
            self.assertEqual(barrier.current, 1000)
        self.advance(1, buttons=2)
        self.assertEqual(ammo.value(), 30)
        self.assertEqual(barrier.current, 1000)

        for _ in range(220):
            self.advance(1, buttons=2)
            if barrier.current < 1000:
                break
        self.assertEqual(barrier.current, 980)
        self.assertEqual(ammo.value(), 29)
        self.assertEqual(self.victim().total, 500)

        for _ in range(2500):
            self.advance(1, buttons=2)
            if not barrier.active:
                break
        self.assertFalse(barrier.active)
        self.assertEqual(barrier.current, 0)
        self.assertEqual(self.victim().total, 500)
        for _ in range(220):
            self.advance(1, buttons=2)
            if self.victim().total < 500:
                break
        self.assertFalse(barrier.active)
        self.assertEqual(self.victim().total, 485)
        self.assertEqual(npc_control.stop(self.match), 1)
        for _ in range(400):
            self.advance(1, buttons=2)
            if barrier.active:
                break
        self.assertTrue(barrier.active)
        restored = barrier.current
        self.arm(actor)
        for _ in range(220):
            self.advance(1, buttons=2)
            if barrier.current < restored:
                break
        self.assertAlmostEqual(barrier.current, restored - 20)
        self.assertEqual(self.victim().total, 485)

    def test_talon_refill_deadlines_and_hammer_abort(self):
        cases = (('Trooper', 'primary', 0x102E, 18, 30, 910),
                 ('Sniper', 'primary', 0x1122, 15, 20, 910),
                 ('Sniper', 'alternate', 0x1134, 14, 20, 910),
                 ('Heavy Assault', 'primary', 0x114F, 15, 150, 1300))
        for name, action, graph, index, capacity, duration in cases:
            for lethal in (False, True):
                with self.subTest(name=name, action=action, lethal=lethal):
                    definition = next(item for item in self.catalog.values() if item.name == name)
                    actor, target, vitals = self.scenario(definition)
                    self.advance(20, buttons=0)
                    component = actor.body_script.component
                    if action == 'alternate':
                        self.arm(actor, action)
                        for _ in range(40):
                            self.advance(1, buttons=0)
                            if component.entity_var(31).value() == 2:
                                break
                        self.assertEqual(component.entity_var(31).value(), 2)
                        npc_control.stop(self.match)
                        self.advance(30, buttons=0)
                    weapon = next(i for i in component.instances.values() if i.graph.index == graph)
                    ammo, wait = weapon.vars[53], weapon.state(index)
                    self.assertEqual(ammo.value(), capacity)
                    weapon.write((expr.INSTANCE, 53), 0)  # depleted magazine fixture
                    if name == 'Heavy Assault':
                        self.advance(1, buttons=0)
                        self.assertFalse(wait.active)
                        weapon.write((expr.INSTANCE, 51), True)  # authored request fixture
                    self.advance(1, buttons=0)
                    self.assertTrue(wait.active)
                    self.assertEqual(wait.ms, duration)
                    deadline = wait.start + duration
                    if lethal:
                        self.match.combat._damage(self.rein, target, vitals.total - 60, False,
                                                  None, self.run.frame)
                        for _ in range(40):
                            self.advance(1)
                            if actor.dead:
                                break
                        self.assertTrue(actor.dead)
                        self.assertLess(component.now, deadline)
                        self.assertFalse(wait.active)
                    while (self.run.frame + 2) * component.frame_ms - 1 < deadline:
                        self.advance(1, buttons=0)
                        self.assertEqual(ammo.value(), 0)
                    self.advance(1, buttons=0)
                    self.assertEqual(ammo.value(), 0 if lethal else capacity)

    def test_sniper_slots_spend_emitted_rounds_then_refill_at_the_authored_deadline(self):
        definition = next(item for item in self.catalog.values() if item.name == 'Sniper')
        cases = (('primary', 0x1122, 15), ('alternate', 0x1134, 14))
        for action, graph_index, wait_index in cases:
            with self.subTest(action=action):
                actor, _, _ = self.scenario(definition, (0, 1, 8))
                self.advance(20, buttons=2)
                self.arm(actor, action)
                component = actor.body_script.component
                weapon = next(i for i in component.instances.values() if i.graph.index == graph_index)
                ammo, wait = weapon.vars[53], weapon.state(wait_index)
                self.assertEqual(ammo.value(), 20)
                fight = self.match.combat
                with patch.object(fight, '_shoot', wraps=fight._shoot) as shoot:
                    for _ in range(5000):
                        self.advance(1, buttons=2)
                        self.assertEqual(ammo.value(), 20 - shoot.call_count)
                        if wait.active:
                            break
                    self.assertTrue(wait.active)
                    self.assertEqual(shoot.call_count, 20)
                    self.assertEqual(ammo.value(), 0)
                    self.assertEqual(wait.ms, 910)
                    deadline = wait.start + wait.ms
                    while (self.run.frame + 2) * component.frame_ms - 1 < deadline:
                        self.advance(1, buttons=2)
                        self.assertEqual(ammo.value(), 0)
                        self.assertEqual(shoot.call_count, 20)
                    self.advance(1, buttons=2)
                    self.assertEqual(ammo.value(), 20)
                    self.assertEqual(shoot.call_count, 20)
                    for _ in range(240):
                        self.advance(1, buttons=2)
                        if shoot.call_count > 20:
                            break
                    self.assertEqual(shoot.call_count, 21)
                    self.assertEqual(ammo.value(), 19)

    def test_talon_rifles_release_at_range_and_after_target_departure(self):
        for name, remaining in (('Trooper', 485), ('Sniper', 405), ('Heavy Assault', 497.5)):
            with self.subTest(name=name):
                definition = next(item for item in self.catalog.values() if item.name == name)
                actor, _, _ = self.scenario(definition, (0, 1, 8))
                self.match.game_map = replace(self.match.game_map, free_for_all=False)
                self.enemy.team = self.other.team = actor.team
                self.rein.team = 1 - actor.team
                self.advance(20, buttons=0)
                self.arm(actor)
                self.advance(5, buttons=0)
                self.assertTrue(actor.attack_input)
                self.place(self.rein, (0, 1, -101))
                self.advance(90, buttons=0)
                component = actor.body_script.component
                self.assertFalse(actor.attack_input)
                self.assertFalse(component.held)
                health = self.victim()
                self.assertEqual(health.total, 500)

                self.place(self.rein, (0, 1, 0))
                for _ in range(220):
                    self.advance(1, buttons=0)
                    if health.total < 500:
                        break
                self.assertEqual(health.total, remaining)
                self.match.leave(self.rein)
                self.run.players.remove(self.rein)
                for _ in range(90):
                    self.run.step({})
                self.assertFalse(actor.attack_input)
                self.assertFalse(component.held)
                self.assertEqual(health.total, remaining)

    def test_actual_three_weapons_consume_armour_and_stop_without_late_damage(self):
        for definition, damage, _ in self.samples():
            with self.subTest(name=definition.name):
                actor, _, _ = self.scenario(definition, (0, 1, 8))
                self.advance(20, buttons=0)
                self.arm(actor)
                health = self.victim()
                for _ in range(220):
                    self.advance(1, buttons=0)
                    if health.total < 500:
                        break
                self.assertIsNotNone(actor.body_script)
                self.assertEqual(health.total, 500 - damage)
                self.assertEqual(health.now[0], 300)
                self.assertEqual(health.now[1], 200 - damage)
                npc_control.stop(self.match)
                self.advance(90, buttons=0)
                self.assertEqual(health.total, 500 - damage)
                self.assertFalse(actor.attack_input)
                self.assertFalse(actor.body_script.component.held)

    def test_active_trooper_enforcer_and_sniper_weapons_do_not_counter_charge(self):
        for name, action in (
            ('Trooper', 'primary'), ('Enforcer', 'primary'),
            ('Sniper', 'primary'), ('Sniper', 'alternate'),
        ):
            with self.subTest(name=name, action=action):
                definition = next(item for item in self.catalog.values() if item.name == name)
                actor, _, actor_health = self.scenario(definition, (0, 1, 8))
                self.advance(20, buttons=0)
                self.arm(actor, action)
                health = self.victim()
                for _ in range(220):
                    self.advance(1, buttons=0)
                    if health.total < 500:
                        break
                self.assertLess(health.total, 500)
                self.assertTrue(actor.attack_input)
                before = actor_health.total
                fight = self.match.combat
                self.advance(1, buttons=8)
                for _ in range(115):
                    self.advance(1, buttons=0)
                    contact = fight.charge_contacts.get(self.rein.body)
                    if contact is not None and contact.pinned == actor.entity:
                        break
                self.assertIsNotNone(contact)
                self.assertEqual(contact.pinned, actor.entity)
                self.assertTrue(fight.controlled(actor.entity))
                self.assertFalse(fight.controlled(self.rein.body))
                self.assertEqual(actor_health.total, before)

    def test_heavy_primary_breaks_full_barrier_then_recovered_field_blocks_again(self):
        self.check_full_barrier_break('Heavy Assault', 'primary', 5, 2.5)

    def test_both_sniper_weapons_break_full_barrier_then_recovered_field_blocks_again(self):
        for action, shield_damage, armour_damage in (('primary', 100, 95), ('alternate', 5, 2.5)):
            with self.subTest(action=action):
                self.check_full_barrier_break('Sniper', action, shield_damage, armour_damage)

    def check_full_barrier_break(self, name, action, shield_damage, armour_damage):
        definition = next(item for item in self.catalog.values() if item.name == name)
        actor, _, _ = self.scenario(definition, (0, 1, 8))
        self.advance(20, buttons=2)
        shield = self.match.combat.barriers[self.rein.body]
        health = self.victim()
        self.assertTrue(shield.active)
        self.assertEqual(shield.current, 1600)
        self.arm(actor, action)
        for _ in range(8000):
            self.advance(1, buttons=2)
            if not shield.active:
                break
        self.assertEqual(shield.current, 0)
        self.assertFalse(shield.active)
        self.assertEqual(health.total, 500)
        for _ in range(150):
            self.advance(1, buttons=2)
            if health.total < 500:
                break
        self.assertEqual(health.total, 500 - armour_damage)
        self.assertEqual(npc_control.stop(self.match), 1)
        for _ in range(400):
            self.advance(1, buttons=2)
            if shield.active:
                break
        self.assertTrue(shield.active)
        restored = shield.current
        self.arm(actor, action)
        for _ in range(220):
            self.advance(1, buttons=2)
            if shield.current < restored:
                break
        self.assertAlmostEqual(shield.current, restored - shield_damage)
        self.assertEqual(health.total, 500 - armour_damage)

    def test_front_barrier_absorbs_each_actual_weapon_without_armour_damage(self):
        for definition, _, shield_damage in self.samples():
            with self.subTest(name=definition.name):
                actor, _, _ = self.scenario(definition, (0, 1, 8))
                self.advance(20, buttons=2)
                barrier = self.match.combat.barriers[self.rein.body]
                self.assertTrue(barrier.active)
                self.arm(actor)
                health = self.victim()
                for _ in range(220):
                    self.advance(1, buttons=2)
                    if barrier.current < 1600:
                        break
                self.assertIsNotNone(actor.body_script)
                self.assertEqual(barrier.current, 1600 - shield_damage)
                self.assertEqual(health.total, 500)
                npc_control.stop(self.match)
                self.advance(60, buttons=2)
                self.assertEqual(barrier.current, 1600 - shield_damage)
                self.assertEqual(health.total, 500)

    def test_talon_ranged_weapons_hit_ice_wall_then_barrier_then_armor(self):
        cases = (
            ('Trooper', 'primary', 20, 15),
            ('Enforcer', 'primary', 50, 20),
            ('Sniper', 'primary', 100, 95),
            ('Sniper', 'alternate', 5, 2.5),
            ('Heavy Assault', 'primary', 5, 2.5),
        )
        for name, action, shield_damage, armor_damage in cases:
            with self.subTest(name=name, action=action):
                definition = next(item for item in self.catalog.values() if item.name == name)
                actor, _, _ = self.scenario(definition, (0, 1, 8))
                fight = self.match.combat
                self.assertTrue(self.match.switch_hero(self.enemy, heroes()[defenses.MEI]))
                self.enemy.team = self.rein.team
                self.place(self.enemy, (0, 1, -4))
                self.advance(20, buttons=2)
                barrier = fight.barriers[self.rein.body]
                health = self.victim()

                centre = ice_wall.ReinhardtIceWallTests.wall(self, 4)
                self.advance(20, buttons=2)
                self.assertTrue(barrier.active)
                self.arm(actor, action)
                for _ in range(220):
                    self.advance(1, buttons=2)
                    if centre.health < 400:
                        break
                self.assertLess(centre.health, 400)
                self.assertTrue(actor.attack_input)
                self.assertEqual(barrier.current, 1600)
                self.assertEqual(health.total, 500)

                angles = aim(fight._eye(self.enemy), (0, 1, 4))
                self.run.step({self.enemy: (*angles, 16), self.rein: (0, 0, 2)})
                self.assertFalse(fight.walls.pieces)
                for _ in range(220):
                    self.advance(1, buttons=2)
                    if barrier.current <= 1600 - shield_damage:
                        break
                self.assertTrue(barrier.active)
                self.assertAlmostEqual(barrier.current, 1600 - shield_damage, places=2)
                self.assertEqual(health.total, 500)
                self.assertEqual(health.now[1], 200)
                self.advance(1, buttons=0)
                self.assertFalse(barrier.active)
                for _ in range(220):
                    self.advance(1, buttons=0)
                    if health.total <= 500 - armor_damage + 1e-5:
                        break
                self.assertAlmostEqual(health.now[1], 200 - armor_damage, places=5)
                self.assertEqual(health.now[0], 300)
                self.assertEqual(npc_control.stop(self.match), 1)
                self.advance(20, buttons=0)
                self.assertFalse(actor.attack_input)
                self.assertAlmostEqual(health.total, 500 - armor_damage, places=5)

    def test_primary_breaks_barrier_then_next_actual_hit_reaches_armour(self):
        for definition, damage, shield_damage in self.samples():
            with self.subTest(name=definition.name):
                actor, _, _ = self.scenario(definition, (0, 1, 8))
                self.advance(20, buttons=2)
                fight = self.match.combat
                barrier = fight.barriers[self.rein.body]
                barrier.current = shield_damage - .5
                health = self.victim()
                self.arm(actor)
                for _ in range(220):
                    self.advance(1, buttons=2)
                    if not barrier.active:
                        break
                self.assertEqual(barrier.current, 0)
                self.assertGreater(barrier.broken_until, fight.now)
                self.assertEqual(health.total, 500)
                for _ in range(220):
                    self.advance(1, buttons=2)
                    if health.total < 500:
                        break
                self.assertEqual(health.total, 500 - damage)
                self.assertEqual(health.now[0], 300)
                self.assertFalse(barrier.active)

    def test_held_barrier_recovers_and_absorbs_reengaged_talon_primary(self):
        enforcer = next(item for item in self.catalog.values() if item.name == 'Enforcer')
        weapons = [(definition, amount) for definition, _, amount in self.samples()] + [(enforcer, 50)]
        for definition, amount in weapons:
            with self.subTest(name=definition.name):
                actor, _, _ = self.scenario(definition, (0, 1, 8))
                self.advance(20, buttons=2)
                fight = self.match.combat
                barrier = fight.barriers[self.rein.body]
                barrier.current = 1
                self.arm(actor)
                for _ in range(220):
                    self.advance(1, buttons=2)
                    if not barrier.active:
                        break
                self.assertEqual(barrier.current, 0)
                self.assertEqual(npc_control.stop(self.match), 1)
                hp = self.victim().total
                while fight.now + .016 < barrier.regen_after:
                    self.advance(1, buttons=2)
                    self.assertEqual(barrier.current, 0)
                    self.assertFalse(barrier.active)
                while fight.now + .016 < barrier.broken_until:
                    self.advance(1, buttons=2)
                    self.assertFalse(barrier.active)
                self.assertGreater(barrier.current, 0)
                self.assertEqual(self.victim().total, hp)
                self.advance(15, buttons=2)
                self.assertTrue(barrier.active)
                before = barrier.current
                self.arm(actor)
                for _ in range(220):
                    self.advance(1, buttons=2)
                    if before - barrier.current >= amount:
                        break
                self.assertAlmostEqual(before - barrier.current, amount, places=3)
                self.assertTrue(barrier.active)
                self.assertEqual(self.victim().total, hp)

    def test_actual_talon_weapon_death_retires_barrier_and_respawns(self):
        enforcer = next(item for item in self.catalog.values() if item.name == 'Enforcer')
        sniper = next(item for item in self.catalog.values() if item.name == 'Sniper')
        cases = [(row[0], 'primary') for row in self.samples()] + [(enforcer, 'primary'), (sniper, 'alternate')]
        for definition, action in cases:
            with self.subTest(name=definition.name, action=action):
                actor, _, _ = self.scenario(definition, (0, 1, 8))
                self.advance(20, buttons=2)
                fight = self.match.combat
                old_body = self.rein.body
                barrier = fight.barriers[old_body]
                self.assertTrue(barrier.active)
                barrier.current = 37
                self.advance(1, buttons=0)
                self.assertFalse(barrier.active)
                health = self.victim()
                health.damage(499, bypass_armour=True)
                self.arm(actor, action)
                for _ in range(220):
                    self.advance(1, buttons=0)
                    if health.dead:
                        break
                self.assertTrue(health.dead)
                self.assertEqual(health.total, 0)
                self.assertEqual(health.killer, actor.entity)
                self.assertTrue(self.rein.body_script.component.owner_dead)
                self.assertEqual(npc_control.stop(self.match), 1)
                fixtures.TalonInteractionTests.assert_reinhardt_respawns_with_fresh_barrier(self, old_body)
                self.place(self.rein, (0, 1, 0))
                shield = fight.barriers[self.rein.body]
                self.arm(actor, action)
                for _ in range(220):
                    self.advance(1, buttons=2)
                    if shield.current < 1600:
                        break
                self.assertLess(shield.current, 1600)
                self.assertEqual(self.victim().total, 500)

    def test_source_control_suppresses_then_releases_actual_weapon_input(self):
        for definition, damage, _ in self.samples():
            with self.subTest(name=definition.name):
                actor, target, _ = self.scenario(definition, (0, 1, 8))
                self.advance(20, buttons=0)
                self.arm(actor)
                self.match.combat.control(target, self.rein.body, 'stun', .5)
                health = self.victim()
                self.advance(25, buttons=0)
                self.assertEqual(health.total, 500)
                self.assertFalse(actor.attack_input)
                for _ in range(220):
                    self.advance(1, buttons=0)
                    if health.total < 500:
                        break
                self.assertEqual(health.total, 500 - damage)
                self.assertIsNotNone(actor.body_script)

    def test_static_occlusion_and_allied_team_deny_actual_weapon_input(self):
        for definition, _, _ in self.samples():
            for reason in ('wall', 'ally'):
                with self.subTest(name=definition.name, reason=reason):
                    actor, _, _ = self.scenario(definition, (0, 1, 8))
                    self.advance(20, buttons=0)
                    if reason == 'wall':
                        fixtures.charge_wall(self, 4)
                    else:
                        self.match.game_map = self.match.game_map.__class__(
                            **{**self.match.game_map.__dict__, 'free_for_all': False})
                        self.rein.team = actor.team = 0
                    # Exercise the engagement gate even when UI selection would
                    # refuse the ally/occluded actor before this request.
                    actor.attack_target = self.rein.entity
                    actor.firing = True
                    self.advance(100, buttons=0)
                    self.assertEqual(self.victim().total, 500)
                    self.assertFalse(actor.attack_input)
                    self.assertFalse(actor.body_script.component.held)

    def test_actual_shatter_interrupts_three_attackers_then_native_duration_expires(self):
        for definition, _, _ in self.samples():
            with self.subTest(name=definition.name):
                actor, _, _ = self.scenario(definition, (0, 1, 8))
                self.advance(20, buttons=0)
                self.arm(actor)
                self.rein.body_script.set_ult_charge(1540)
                self.advance(1, buttons=32)
                self.advance(60, buttons=0)
                self.assertTrue(self.match.combat.controlled(actor.entity))
                self.assertFalse(actor.attack_input)
                health = self.victim()
                frozen = health.total
                self.assertGreater(frozen, 0)
                self.advance(70, buttons=0)
                self.assertTrue(self.match.combat.controlled(actor.entity))
                self.assertEqual(health.total, frozen)
                for _ in range(240):
                    self.advance(1, buttons=0)
                    if health.total < frozen:
                        break
                self.assertFalse(self.match.combat.controlled(actor.entity))
                self.assertLess(health.total, frozen)


if __name__ == '__main__':
    unittest.main()
