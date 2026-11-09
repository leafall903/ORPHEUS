"""Actual selected0407 commands and Zen volleys; reconstructed finite-projectile Matrix host."""

import hashlib
import json
import os
import tempfile
import unittest
from dataclasses import replace
from pathlib import Path
from unittest.mock import patch

import test_dva_body_forms as forms
from test_game_combat import Run, aim, in_world

from ow174.game import combat, dva, matrix, npc, projectiles
from ow174.game.collision import World, flat_world
from ow174.game.commands import FIRE, SECONDARY
from ow174.game.content import SOLDIER, heroes
from ow174.game.world import OP_DESTROY
from tools.extract_projectile_tags import entity_tags

ORB, FROST = 0x0400000000000236, 0x04000000000008BE


class MatrixInputTests(unittest.TestCase):
    def test_bad_catalog_and_unknown_definition_cannot_be_assumed_absorbable(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder)/'tags.json'
            for data in ({'version': 1, 'build': 'other', 'entities': {}},
                         {'version': 1, 'build': matrix.BUILD, 'entities': {'0236': []}},
                         {'version': 1, 'build': matrix.BUILD,
                          'entities': {'0x0400000000000236': ['0x0400000000000145']}}):
                path.write_text(json.dumps(data), encoding='utf-8')
                with self.assertRaises(ValueError):
                    matrix.read_tags(path)
        with patch.object(matrix, 'tags', return_value={ORB: frozenset(),
                                                       FROST: frozenset({matrix.EXCLUDED_TAG})}):
            self.assertTrue(matrix.absorbable(ORB))
            self.assertFalse(matrix.absorbable(FROST))
            self.assertFalse(matrix.absorbable(None))
            self.assertFalse(matrix.absorbable(0x040000000000FFFF))

    @unittest.skipUnless(os.environ.get('ORPHEUS_MATRIX_INPUTS'), 'requires manifest-verified matrix inputs')
    def test_exported_projectile_tags_match_verified_xml_and_profile_type_is097(self):
        root = Path(os.environ['ORPHEUS_MATRIX_INPUTS'])
        proof = json.loads((root.parent/'zen-matrix-input-provenance.json').read_text(encoding='utf-8'))
        for row in proof:
            data = (root/row['guid'].split('.')[1]/f"{row['guid']}.xml").read_bytes()
            self.assertTrue(row['verified'])
            self.assertEqual(row['build'], 104319)
            self.assertEqual(hashlib.sha256(data).hexdigest(), row['xml_sha256'])
        self.assertEqual(entity_tags(root/'003'/'000000000236.003.xml'), ['0x0D600000000002F9'])
        self.assertIn('0x0D60000000000145', entity_tags(root/'003'/'0000000008BE.003.xml'))
        self.assertTrue((root/'097'/'000000000002.097.xml').is_file())
        self.assertTrue(matrix.absorbable(ORB))
        self.assertFalse(matrix.absorbable(FROST))


@unittest.skipUnless(os.environ.get('ORPHEUS_DVA_RUNTIME') and os.environ.get('ORPHEUS_PROJECTILE_TAGS'),
                     'requires actual0407 metadata/mover and verified projectile tag catalog')
class ZenyattaMatrixTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        forms.DvaBodyFormTests.setUpClass()

    def arena(self, *, active=True, yaw=-32768, distance=8):
        run, mech, zen = forms.DvaBodyFormTests().arena()
        floor = mech.mover.position[1]
        mech.mover.position[:] = (0, floor, distance)
        zen.mover.position[:] = (0, floor, 0)
        collision = flat_world(floor)
        run.match.collision_world = collision
        for player in run.players:
            player.mover.use(world=collision)
        for _ in range(100):
            self.step(run, mech, zen, mb=0, yaw=yaw)
        if active:
            for _ in range(10):
                self.step(run, mech, zen, yaw=yaw)
            self.assertTrue(self.instance(mech).state(3).active)
        return run, mech, zen

    def instance(self, mech):
        return next(i for i in mech.body_script.component.instances.values() if i.graph.index == 0x2CC)

    def step(self, run, mech, zen, *, mb=SECONDARY, zb=0, yaw=-32768):
        run.step({mech: (yaw, 0, mb), zen: (0, 600, zb)})

    def health(self, run, actor):
        return run.match.combat.vitals_of(next(t for t in run.match.combat._targets() if t.owner is actor))

    def shoot(self, run, mech, zen, *, mb=SECONDARY, yaw=-32768):
        self.step(run, mech, zen, zb=FIRE, mb=mb, yaw=yaw)
        for _ in range(30):
            self.step(run, mech, zen, mb=mb, yaw=yaw)

    def test_friendly_matrix_only_consumes_the_orb_after_enemy_deflect(self):
        genji_hero = 0x02E0000000000029
        for active in (False, True):
            with self.subTest(matrix=active):
                run, mech, zen = self.arena(active=False, yaw=0, distance=3)
                match = run.match
                match.game_map = replace(match.game_map, free_for_all=False)
                zen.team = mech.team = 0
                genji = match.add_player(3, "Genji", genji_hero, 1, False)
                in_world(match, genji)
                self.assertTrue(match.switch_hero(genji, heroes()[genji_hero]))
                run.players.append(genji)
                floor = zen.mover.position[1]
                genji.mover.position = [0, floor, 13]
                genji.mover.body = None
                fight = match.combat
                guard = {
                    genji: (*aim(fight._eye(genji), (0, floor+1, 3)), 16),
                    mech: (0, 0, SECONDARY if active else 0),
                }
                for _ in range(20):
                    run.step(guard)
                self.assertEqual(self.instance(mech).state(3).active, active)
                direction = aim(fight._eye(zen), (0, floor+1, 13))
                run.step({**guard, zen: (*direction, FIRE)})
                orb, = fight.rockets
                for _ in range(35):
                    run.step(guard)
                    if orb not in fight.rockets:
                        break
                self.assertIs(orb.shooter, genji)
                self.assertNotIn(orb, fight.rockets)
                self.assertEqual(self.health(run, mech).total, 600 if active else 557)
                self.assertEqual(self.health(run, zen).total, 200)
                self.assertEqual(self.health(run, genji).total, 200)
                self.assertFalse(zen.client.messages(combat.HIT))
                self.assertEqual(len(genji.client.messages(combat.HIT)), 0 if active else 1)


    def test_front_consumes_real_primary_without_damage_hit_or_damage_ultimate_charge(self):
        run, mech, zen = self.arena()
        charge = zen.body_script.component.entity_var(342).value()
        frame = run.frame
        self.shoot(run, mech, zen)
        self.assertEqual(self.health(run, mech).total, 600)
        self.assertEqual(self.health(run, zen).total, 200)
        self.assertFalse(zen.client.messages(combat.HIT))
        self.assertFalse(mech.client.messages(combat.HIT))
        self.assertFalse(run.match.combat.rockets)
        self.assertFalse(run.match.combat.projectiles.shots)
        self.assertAlmostEqual(zen.body_script.component.entity_var(342).value()-charge,
                               (run.frame-frame)*.016*5, places=4)
        weapon = next(i for i in zen.body_script.component.instances.values() if i.graph.index == 0x164)
        self.assertEqual(weapon.vars[53].value(), 19)

    def test_inactive_matrix_query_does_not_materialize_a_vm_state(self):
        run, mech, zen = self.arena(active=False)
        instance = self.instance(mech)
        before = tuple(instance.states)
        self.assertNotIn(3, before)
        self.assertIsNone(run.match.combat._matrix_hit(
            zen, run.match.combat._eye(zen), (0, 0, 1), 30, ORB))
        self.assertEqual(tuple(instance.states), before)

    def test_actual_full_burst_consumes_five_rounds_without_hitting_active_matrix(self):
        run, mech, zen = self.arena(active=False)
        for _ in range(130):
            self.step(run, mech, zen, mb=0, zb=SECONDARY)
        for _ in range(60):
            self.step(run, mech, zen)
        self.assertEqual(self.health(run, mech).total, 600)
        self.assertFalse(zen.client.messages(combat.HIT))
        weapon = next(i for i in zen.body_script.component.instances.values() if i.graph.index == 0x164)
        self.assertEqual(weapon.vars[53].value(), 15)
        self.assertFalse(run.match.combat.rockets)

    def test_real_orb_absorption_retires_confirmed_owner_copy_without_explosion_release(self):
        run, mech, zen = self.arena()
        self.step(run, mech, zen, zb=FIRE)
        [rocket] = run.match.combat.rockets
        shot = rocket.shot
        self.assertIsNotNone(shot)
        # Explicit already-acknowledged transport fixture; the actual Volley and flight are unchanged.
        shot.created[0xA0001234] = projectiles._ProjectileArrival(received=True)
        zen.client.entities.clear()
        self.step(run, mech, zen)
        self.assertNotIn(shot, run.match.combat.projectiles.shots)
        [destroy] = [row for row in zen.client.entities if row.entity == 0xA0001234]
        self.assertEqual(destroy.op, OP_DESTROY)
        self.assertIsNone(destroy.ch2)
        self.assertEqual(self.health(run, mech).total, 600)

    def test_real_depletion_rejects_new_matrix_until_resource_recovers_then_orbs_are_consumed(self):
        run, mech, zen = self.arena()
        instance = self.instance(mech)
        for _ in range(160):
            self.step(run, mech, zen)
        self.assertEqual(instance.vars[87].value(), 0)
        self.assertFalse(instance.state(3).active)
        self.shoot(run, mech, zen)
        self.assertEqual(self.health(run, mech).total, 557)
        for _ in range(150):
            self.step(run, mech, zen, mb=0)
        self.assertGreater(instance.vars[87].value(), 10)
        for _ in range(10):
            self.step(run, mech, zen)
        self.assertTrue(instance.state(3).active)
        hits = len(zen.client.messages(combat.HIT))
        self.shoot(run, mech, zen)
        self.assertEqual(len(zen.client.messages(combat.HIT)), hits)
        self.assertEqual(self.health(run, mech).total, 557)

    def test_absorbed_orb_does_not_add_resource_cost_beyond_authored_elapsed_drain(self):
        absorbed, defender, zen = self.arena()
        control, other, idle = self.arena()
        self.shoot(absorbed, defender, zen)
        for _ in range(31):
            self.step(control, other, idle)
        self.assertFalse(absorbed.match.combat.rockets)
        self.assertEqual(self.health(absorbed, defender).total, 600)
        self.assertEqual(self.instance(defender).vars[87].value(), self.instance(other).vars[87].value())

    def test_nearer_live_body_receives_real_orb_before_the_matrix_volume(self):
        run, mech, zen = self.arena(distance=15)
        # A real Soldier definition is a collision/health fixture, not one of the five Talon NPCs.
        soldier = heroes()[SOLDIER]
        body = npc.spawn(run.match, npc.Definition(soldier.guid, soldier.body, 'Near body', 200),
                         (0, zen.mover.position[1], 2.5), team=mech.team)
        self.shoot(run, mech, zen)
        self.assertEqual(self.health(run, mech).total, 600)
        self.assertEqual(self.health(run, body).total, 104)
        self.assertEqual(zen.client.messages(combat.HIT)[0]['+0x78']['+0x4'], 96)

    def test_actual_harmony_and_transcendence_heal_friendly_mech_during_active_matrix(self):
        for button, rate in ((0x10, 30), (0x20, 300)):
            with self.subTest(button=button):
                run, mech, zen = self.arena()
                zen.team = mech.team
                vitals = self.health(run, mech)
                vitals.damage(100)
                before = vitals.total
                if button == 0x20:
                    script = zen.body_script
                    script.set_ult_charge(script.component.entity_var(338).value())
                self.step(run, mech, zen, zb=button)
                for _ in range(20):
                    self.step(run, mech, zen)
                self.assertTrue(self.instance(mech).state(3).active)
                self.assertAlmostEqual(vitals.total, min(600, before+rate*.016*20), delta=rate*.016)

    def test_actual_back_flashbang_interrupts_matrix_then_real_zen_orb_hits(self):
        run = Run(names=('Mech', 'Zenyatta', 'McCree'))
        mech, zen, caster = run.players
        run.match.bots.clear()
        self.assertTrue(run.match.switch_hero(mech, replace(heroes()[dva.HERO], body=dva.MECH)))
        self.assertTrue(run.match.switch_hero(zen, heroes()[0x02E0000000000020]))
        self.assertTrue(run.match.switch_hero(caster, heroes()[0x02E0000000000042]))
        mech.team, zen.team, caster.team = 1, 0, 0
        floor = mech.mover.position[1]
        collision = flat_world(floor)
        run.match.collision_world = collision
        for player, z in ((mech, 8), (zen, 0), (caster, 10)):
            player.mover.position[:] = (0, floor, z)
            player.mover.use(world=collision)
        for _ in range(100):
            self.step(run, mech, zen, mb=0)
        for _ in range(10):
            self.step(run, mech, zen)
        self.assertTrue(self.instance(mech).state(3).active)
        fight = run.match.combat
        angles = aim(fight._eye(caster), (0, floor+1, 8))
        run.step({mech: (-32768, 0, SECONDARY), caster: (*angles, 16)})
        for _ in range(50):
            if fight.is_stunned(mech):
                break
            self.step(run, mech, zen)
        self.assertTrue(fight.is_stunned(mech))
        self.assertFalse(self.instance(mech).state(3).active)
        before = self.health(run, mech).total
        self.shoot(run, mech, zen)
        self.assertEqual(self.health(run, mech).total, before-43)

    def test_off_back_and_released_matrix_allow_real_orb_damage(self):
        for mode in ('off', 'back', 'released'):
            with self.subTest(mode=mode):
                yaw = 0 if mode == 'back' else -32768
                run, mech, zen = self.arena(active=mode != 'off', yaw=yaw)
                if mode == 'released':
                    for _ in range(30):
                        self.step(run, mech, zen, mb=0)
                    self.assertFalse(self.instance(mech).state(3).active)
                self.shoot(run, mech, zen, mb=SECONDARY if mode == 'back' else 0, yaw=yaw)
                self.assertEqual(self.health(run, mech).total, 557)
                self.assertEqual(len(zen.client.messages(combat.HIT)), 1)

    def test_actual_melee_bypasses_active_matrix(self):
        run, mech, zen = self.arena(distance=2)
        self.step(run, mech, zen, zb=0x800)
        for _ in range(35):
            self.step(run, mech, zen)
        self.assertEqual(self.health(run, mech).total, 575)
        self.assertEqual(zen.client.messages(combat.HIT)[0]['+0x78']['+0x4'], 25)

    def test_discord_targeting_bypasses_matrix_but_consumed_orb_grants_no_damage(self):
        run, mech, zen = self.arena()
        self.step(run, mech, zen, zb=8)
        self.assertIsNotNone(run.match.combat.zenyatta.owners[zen.body].discord)
        self.shoot(run, mech, zen)
        self.assertEqual(self.health(run, mech).total, 600)
        self.assertFalse(zen.client.messages(combat.HIT))

    def test_authored_bounds_rotate_with_aim_and_do_not_expand_to_unknown_geometry(self):
        run, mech, _ = self.arena()
        eye = run.match.combat._eye(mech)
        forward = (0, 0, 1)
        self.assertAlmostEqual(matrix.matrix_distance(
            mech, (0, eye[1], 23), (0, 0, -1), 30, eye, forward), 4.75)
        for x, y in ((1.26, eye[1]), (0, eye[1]+1.01)):
            self.assertIsNone(matrix.matrix_distance(mech, (x, y, 23), (0, 0, -1), 30, eye, forward))
        self.assertAlmostEqual(matrix.matrix_distance(
            mech, (15, eye[1], 8), (-1, 0, 0), 30, eye, (1, 0, 0)), 4.75)

    def test_world_wall_precedes_matrix_and_no_damage_or_charge_is_reported(self):
        run, mech, zen = self.arena(distance=15)
        floor = zen.mover.position[1]
        run.match.collision_world = World([
            (-5, floor, 2, 5, floor, 2, 5, floor+5, 2),
            (-5, floor, 2, 5, floor+5, 2, -5, floor+5, 2),
        ])
        with patch.object(run.match.combat, '_matrix_hit', wraps=run.match.combat._matrix_hit) as query:
            self.shoot(run, mech, zen)
        self.assertTrue(query.called)
        self.assertEqual(self.health(run, mech).total, 600)
        self.assertFalse(zen.client.messages(combat.HIT))

    def test_dead_and_replaced_mech_cannot_leave_a_live_matrix(self):
        for death in (False, True):
            with self.subTest(death=death):
                run, mech, zen = self.arena()
                fight = run.match.combat
                if death:
                    fight._damage(zen, next(t for t in fight._targets() if t.owner is mech),
                                  10000, False, None, run.frame)
                else:
                    self.assertTrue(run.match.switch_hero(mech, heroes()[dva.HERO]))
                self.assertIsNone(fight._matrix_hit(zen, fight._eye(zen), (0, 0, 1), 30, ORB))

    def test_team_filter_and_ffa_use_actual_match_relationships(self):
        run, mech, zen = self.arena()
        fight = run.match.combat
        zen.team = mech.team
        run.match.game_map = replace(run.match.game_map, free_for_all=False)
        self.assertIsNone(fight._matrix_hit(zen, fight._eye(zen), (0, 0, 1), 30, ORB))
        run.match.game_map = replace(run.match.game_map, free_for_all=True)
        self.assertIsNotNone(fight._matrix_hit(zen, fight._eye(zen), (0, 0, 1), 30, ORB))
