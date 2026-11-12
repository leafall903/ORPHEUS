"""Executed Ice Wall reconstruction checks; native placement/client parity stays open."""

import sys
import unittest
from dataclasses import replace
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import test_delivered_mei_combat as fixtures
import test_mei_frost as inputs

from ow174.game import combat, mei, world
from ow174.game.collision_fingerprint import world_fingerprint
from ow174.game.commands import FIRE, JUMP, Command
from ow174.game.content import heroes


class WallTests(unittest.TestCase):
    def scene(self):
        return fixtures.MeiCombatTests().duel()

    def tap(self, run, owner, button, aim):
        run.step({owner: (*aim, button)})
        run.step({owner: (*aim, 0)})

    def place(self, run, owner, distance=3.0, rotated=False):
        x, y, z = owner.mover.position
        aim = fixtures.aim(run.match.combat._eye(owner), (x, y, z + distance))
        self.tap(run, owner, 16, aim)
        if rotated:
            self.tap(run, owner, 2, aim)
        self.tap(run, owner, 1, aim)
        return list(run.match.combat.walls.pieces.values()), aim

    def test_preview_rotation_five_real_creates_and_cooldown(self):
        for rotated in (False, True):
            with self.subTest(rotated=rotated):
                run, owner, _enemy, _target = self.scene()
                collision = world_fingerprint(run.match.collision_world)
                pieces, _aim = self.place(run, owner, rotated=rotated)
                self.assertEqual(len(pieces), 5)
                self.assertTrue(all(piece.health == 400 for piece in pieces))
                self.assertFalse(run.match.combat.walls.inputs[owner.body].preview)
                self.assertGreater(run.match.combat.walls.instance(owner).vars[61].value(), 11.9)
                self.assertAlmostEqual(pieces[-1].feet[2] - pieces[0].feet[2], 6.2 if rotated else 0.0)
                self.assertAlmostEqual(pieces[-1].feet[0] - pieces[0].feet[0], 0.0 if rotated else 6.2)
                ids = {piece.entity for piece in pieces}
                creates = [u for u in owner.client.entities if u.entity in ids and u.op == world.OP_CREATE]
                self.assertEqual(len(creates), 5)
                for update in creates:
                    out = update.build(0)
                    self.assertGreater(out.count, 100)
                self.assertEqual(world_fingerprint(run.match.collision_world), collision)
                weapon = next(i for i in owner.body_script.component.instances.values()
                              if i.graph.index == 0x5E6)
                self.assertEqual(weapon.vars[53].value(), 120)

    def test_invalid_sky_confirmation_and_preview_cancel_spend_no_cooldown(self):
        run, owner, _enemy, _target = self.scene()
        sky = (0, -10000)
        self.tap(run, owner, 16, sky)
        self.tap(run, owner, 1, sky)
        self.assertFalse(run.match.combat.walls.pieces)
        self.assertTrue(run.match.combat.walls.inputs[owner.body].preview)
        self.assertEqual(run.match.combat.walls.instance(owner).vars[61].value(), 0.0)
        self.tap(run, owner, 16, sky)
        self.assertFalse(run.match.combat.walls.inputs[owner.body].preview)
        self.assertEqual(run.match.combat.walls.instance(owner).vars[61].value(), 0.0)

    def test_projectiles_stop_damage_one_pillar_and_do_not_hit_body_behind(self):
        run, owner, enemy, target = self.scene()
        pieces, _aim = self.place(run, owner)
        centre = pieces[2]
        self.assertLess(owner.mover.position[2], centre.feet[2])
        fixtures.MeiCombatTests().fire(run, owner, target, 2, 0.9)
        self.assertEqual(run.match.combat.vitals_of(target).total, 200.0)
        self.assertEqual(centre.health, 400.0)  # own wall blocks the shot but takes no friendly damage
        point = (centre.feet[0], centre.feet[1] + 1.0, centre.feet[2])
        yaw, pitch = fixtures.aim(run.match.combat._eye(enemy), point)
        for frame in range(12):
            run.step({enemy: (yaw, pitch, FIRE if frame == 0 else 0)})
        self.assertEqual(centre.health, 380.0)
        self.assertTrue(all(piece.health == 400 for piece in pieces if piece is not centre))

    def opposing_mei_wall(self):
        run, attacker, defender, _ = self.scene()
        fight = run.match.combat
        self.assertTrue(run.match.switch_hero(defender, heroes()[mei.HERO]))
        run.match.game_map = replace(run.match.game_map, free_for_all=False)
        defender.team = 1 - attacker.team
        x, y, z = attacker.mover.position
        fight.walls.place_player(defender, (x, y, z + 5.0))
        wall_aim = fixtures.aim(fight._eye(defender), (x, y, z + 2.5))
        self.tap(run, defender, 16, wall_aim)
        self.tap(run, defender, 1, wall_aim)
        pieces = tuple(fight.walls.pieces.values())
        self.assertEqual(len(pieces), 5)
        target = next(t for t in fight._targets() if t.owner is defender)
        return run, attacker, target, pieces

    def test_held_spray_breaks_enemy_column_before_reaching_enemy_body(self):
        run, attacker, target, pieces = self.opposing_mei_wall()
        fight = run.match.combat
        defender = target.owner
        centre = pieces[2]
        centre.health = 5.5  # Two original 2.75-damage spray contacts remain.
        vitals = fight.vitals_of(target)
        attacker.body_script.ult_seconds = 0
        attacker.body_script.set_ult_charge(0.0)
        charge = attacker.body_script.component.entity_var(342)
        weapon = next(i for i in attacker.body_script.component.instances.values() if i.graph.index == 0x5E6)
        ammo_before = weapon.vars[53].value()
        x, y, z = target.feet
        spray_aim = fixtures.aim(fight._eye(attacker), (x, y + 0.9, z))
        centre_health = {centre.health}
        for _ in range(30):
            run.step({attacker: (*spray_aim, FIRE)})
            centre_health.add(centre.health)
            self.assertEqual(vitals.total, 250.0)
            self.assertIsNone(fight.frost(defender))
            self.assertEqual(charge.value(), 0.0)
            if centre.entity not in fight.walls.pieces:
                break
        self.assertNotIn(centre.entity, fight.walls.pieces)
        self.assertEqual(centre_health, {5.5, 2.75, 0.0})
        self.assertLess(weapon.vars[53].value(), ammo_before)
        self.assertTrue(all(piece.health == 400.0 for piece in pieces if piece is not centre))
        for _ in range(20):
            run.step({attacker: (*spray_aim, FIRE)})
            if vitals.total < 250.0:
                break
        self.assertEqual(vitals.total, 247.25)
        self.assertAlmostEqual(fight.frost(defender)[0], 0.2, places=6)
        self.assertEqual(charge.value(), 2.75)

    def test_breaking_icicle_stops_at_column_and_next_shot_reaches_body(self):
        run, attacker, target, pieces = self.opposing_mei_wall()
        fight = run.match.combat
        centre = pieces[2]
        centre.health = 75.0
        vitals = fight.vitals_of(target)
        script = attacker.body_script
        script.ult_seconds = 0
        script.set_ult_charge(0.0)
        charge = script.component.entity_var(342)
        weapon = next(i for i in script.component.instances.values() if i.graph.index == 0x5E6)
        ammo = weapon.vars[53]
        before = ammo.value()
        attack = fixtures.MeiCombatTests().fire

        attack(run, attacker, target, 2, 0.9)
        self.assertNotIn(centre.entity, fight.walls.pieces)
        self.assertEqual(centre.health, 0.0)
        self.assertTrue(all(piece.health == 400.0 for piece in pieces if piece is not centre))
        self.assertEqual(vitals.total, 250.0)
        self.assertEqual(charge.value(), 0.0)
        self.assertEqual(ammo.value(), before - 10)

        for _ in range(20):
            run.step({})
        attack(run, attacker, target, 2, 0.9)
        self.assertEqual(vitals.total, 175.0)
        self.assertEqual(charge.value(), 75.0)
        self.assertEqual(ammo.value(), before - 20)
        self.assertIsNone(fight.frost(target.owner))

    def test_enemy_hitscan_damages_pillar_and_opening_allows_later_damage(self):
        run, owner, enemy, _target = self.scene()
        pieces, _aim = self.place(run, owner)
        centre = pieces[2]
        owner_target = next(t for t in run.match.combat._targets() if t.owner is owner)
        point = (owner_target.feet[0], owner_target.feet[1] + 0.9, owner_target.feet[2])
        for frame in range(12):
            yaw, pitch = fixtures.aim(run.match.combat._eye(enemy), point)
            run.step({enemy: (yaw, pitch, FIRE if frame == 0 else 0)})
        self.assertEqual(run.match.combat.vitals_of(owner_target).total, 250.0)
        self.assertEqual(centre.health, 380.0)
        run.match.combat.walls.damage(centre, enemy, 1000.0, run.frame)
        self.assertNotIn(centre.entity, run.match.combat.walls.pieces)
        for frame in range(12):
            run.step({enemy: (yaw, pitch, FIRE if frame == 0 else 0)})
        self.assertEqual(run.match.combat.vitals_of(owner_target).total, 230.0)

    def test_cancel_does_not_reset_cooldown_or_leave_weapon_locked(self):
        run, owner, _enemy, target = self.scene()
        _pieces, aim = self.place(run, owner)
        self.tap(run, owner, 16, aim)
        self.assertFalse(run.match.combat.walls.pieces)
        self.assertGreater(run.match.combat.walls.instance(owner).vars[61].value(), 11.8)
        for _ in range(5):
            self.tap(run, owner, 16, aim)
            self.tap(run, owner, 1, aim)
        self.assertFalse(run.match.combat.walls.pieces)
        for _ in range(750):
            run.step({})
        pieces, _aim = self.place(run, owner)
        self.assertEqual(len(pieces), 5)
        self.tap(run, owner, 16, aim)
        before = run.match.combat.vitals_of(target).total
        fixtures.MeiCombatTests().fire(run, owner, target, 2, 0.9)
        self.assertEqual(run.match.combat.vitals_of(target).total, before - 75.0)

    def test_wall_cooldown_requires_fresh_preview_press_after_a_held_attempt(self):
        run, owner, _enemy, _target = self.scene()
        fight = run.match.combat
        pieces, aim = self.place(run, owner)
        first_ids = {piece.entity for piece in pieces}
        self.tap(run, owner, 16, aim)
        self.assertFalse(fight.walls.pieces)
        cooldown = fight.walls.instance(owner).vars[61]
        for _ in range(750):
            if cooldown.value() < .1:
                break
            run.step({})
        self.assertGreater(cooldown.value(), .016)
        self.assertLess(cooldown.value(), .1)

        for _ in range(20):
            run.step({owner: (*aim, 16)})
            self.assertFalse(fight.walls.inputs[owner.body].preview)
            self.assertFalse(fight.walls.pieces)
        self.assertEqual(cooldown.value(), 0.)
        run.step({owner: (*aim, 0)})
        self.assertFalse(fight.walls.inputs[owner.body].preview)
        self.tap(run, owner, 16, aim)
        self.assertTrue(fight.walls.inputs[owner.body].preview)
        self.assertEqual(cooldown.value(), 0.)
        self.tap(run, owner, 1, aim)
        self.assertEqual(len(fight.walls.pieces), 5)
        self.assertTrue(first_ids.isdisjoint(fight.walls.pieces))
        self.assertGreater(cooldown.value(), 11.9)

    def test_scheduled_respawn_restarts_body_local_wall_cooldown(self):
        run, owner, enemy, _target = self.scene()
        fight = run.match.combat
        _pieces, aim = self.place(run, owner)
        self.tap(run, owner, 16, aim)
        self.assertFalse(fight.walls.pieces)
        old_body = owner.body
        old_deadline = run.frame * 16 + round(fight.walls.instance(owner).vars[61].value() * 1000)
        target = next(t for t in fight._targets() if t.owner is owner)
        fight.vitals_of(target).damage(240.)  # Low-health fixture; death uses actual rifle input.
        yaw, pitch = fixtures.aim(fight._eye(enemy), fight._eye(owner))
        run.step({enemy: (yaw, pitch, FIRE)})
        self.assertTrue(fight.is_dead(owner))
        self.tap(run, owner, 16, aim)
        self.assertFalse(fight.walls.pieces)

        for _ in range(680):
            if owner.body != old_body:
                break
            run.step({})
        self.assertNotEqual(owner.body, old_body)
        self.assertFalse(fight.is_dead(owner))
        for _ in range(30):
            run.step({})
        self.assertLess(run.frame * 16, old_deadline)
        self.assertNotIn(old_body, fight.walls.inputs)
        cooldown = fight.walls.instance(owner).vars[61]
        self.assertEqual(cooldown.value(), 0.)
        x, y, z = owner.mover.position
        yaw, pitch = fixtures.aim(fight._eye(owner), (x, y, z + 3.))
        # The new body has its own command epoch after scheduled respawn.
        epoch_tick, epoch_frame = owner.body_script.world_frame_anchor
        for buttons in (16, 0, FIRE, 0):
            run.step({})
            frame = epoch_frame + run.frame - epoch_tick
            owner.take_commands([Command(frame, yaw=yaw, pitch=pitch, buttons=buttons)])
        pieces = list(fight.walls.pieces.values())
        self.assertEqual(len(pieces), 5)
        self.assertTrue(all(piece.owner == owner.body for piece in pieces))
        self.assertGreater(cooldown.value(), 11.9)

    def test_expiry_and_body_retirement_remove_entities(self):
        run, owner, _enemy, _target = self.scene()
        pieces, _aim = self.place(run, owner)
        until = pieces[0].expires
        while run.frame < until - 1:
            run.step({})
        self.assertEqual(len(run.match.combat.walls.pieces), 5)
        run.step({})
        self.assertFalse(run.match.combat.walls.pieces)
        ids = {p.entity for p in pieces}
        destroyed = {u.entity for u in owner.client.entities if u.op == world.OP_DESTROY and u.entity in ids}
        self.assertEqual(destroyed, ids)
        run, owner, _enemy, _target = self.scene()
        self.place(run, owner)
        retired = owner.body
        self.assertTrue(run.match.switch_hero(owner, owner.hero))
        self.assertFalse(run.match.combat.walls.pieces)
        self.assertNotIn(retired, run.match.combat.walls.inputs)

    def test_player_is_blocked_by_wall_then_can_cross_destroyed_column(self):
        run, owner, enemy, _target = self.scene()
        pieces, _aim = self.place(run, owner)
        centre = pieces[2]
        inputs.FrostTests().commands(run, owner, 55, forward=127)
        self.assertLess(owner.mover.position[2], centre.feet[2] - centre.depth)
        self.assertGreater(owner.mover.position[2], centre.feet[2] - 1.1)
        run.match.combat.walls.damage(centre, enemy, 1000.0, run.frame)
        inputs.FrostTests().commands(run, owner, 55, forward=127)
        self.assertGreater(owner.mover.position[2], centre.feet[2] + centre.depth)

    def test_reinhardt_capsule_must_center_on_destroyed_column_gap(self):
        run, owner, enemy, _target = self.scene()
        self.assertTrue(run.match.switch_hero(enemy, heroes()[0x02E0000000000007]))
        fight = run.match.combat
        x, y, z = owner.mover.position
        fight.walls.place_player(enemy, (x, y, z + 5.))
        for _ in range(30):
            run.step({})
        self.assertEqual(enemy.mover.data.stand_radius, .7)
        pieces, _aim = self.place(run, owner)
        self.assertEqual(len(pieces), 5)
        centre = pieces[2]
        near_face = centre.feet[2] + centre.depth + enemy.mover.data.stand_radius
        walk = inputs.FrostTests().commands
        walk(run, enemy, 40, forward=127, yaw=-32768)
        self.assertAlmostEqual(enemy.mover.position[2], near_face, delta=.02)
        self.assertEqual(fight.walls.damage(centre, enemy, 400., run.frame), 400.)
        self.assertNotIn(centre.entity, fight.walls.pieces)

        fight.walls.place_player(enemy, (centre.feet[0] + .2, y, z + 5.))
        walk(run, enemy, 40, forward=127, yaw=-32768)
        self.assertAlmostEqual(enemy.mover.position[2], near_face, delta=.02)
        fight.walls.place_player(enemy, (centre.feet[0], y, z + 5.))
        walk(run, enemy, 55, forward=127, yaw=-32768)
        self.assertLess(enemy.mover.position[2], centre.feet[2] - centre.depth)

        # Approach the neighboring column at 45 degrees, then slide toward the gap.
        gap_half_width = centre.width / 2 - enemy.mover.data.stand_radius
        for side in (-1, 1):
            with self.subTest(side=side):
                fight.walls.place_player(enemy, (centre.feet[0] + side * 1.8, y, z + 5.))
                walk(run, enemy, 20)
                path = []
                for _ in range(65):
                    walk(run, enemy, 1, forward=127, yaw=-side * 24576)
                    path.append(tuple(enemy.mover.position))
                along_face = [p[0] for p in path if abs(p[2] - near_face) < .02]
                self.assertGreater(len(along_face), 2)
                self.assertGreater(side * (along_face[0] - along_face[-1]), .2)
                within_wall_depth = [p for p in path if abs(p[2] - centre.feet[2]) < centre.depth]
                self.assertTrue(within_wall_depth)
                self.assertTrue(all(abs(p[0] - centre.feet[0]) <= gap_half_width + .01
                                    for p in within_wall_depth))
                self.assertLess(enemy.mover.position[2], centre.feet[2] - centre.depth)

    def test_creation_lifts_player_stands_and_jumps_and_removal_restores_fall(self):
        run, owner, _enemy, _target = self.scene()
        initial = owner.mover.position[1]
        _pieces, aim = self.place(run, owner, distance=0.25)
        self.assertAlmostEqual(owner.mover.position[1], initial + 4.4, places=3)
        for _ in range(20):
            run.step({})
        standing = owner.mover.position[1]
        self.assertFalse(owner.mover.airborne)
        inputs.FrostTests().commands(run, owner, 5, buttons=JUMP)
        self.assertGreater(owner.mover.position[1], standing + 0.2)
        self.assertTrue(owner.mover.airborne)
        self.tap(run, owner, 16, aim)
        for _ in range(100):
            run.step({})
        self.assertLess(owner.mover.position[1], initial + 0.1)

    def test_allied_wall_takes_no_damage_but_still_blocks(self):
        run, owner, ally, _target = self.scene()
        run.match.game_map = replace(run.match.game_map, free_for_all=False)
        ally.team = owner.team
        pieces, _aim = self.place(run, owner)
        self.assertEqual(run.match.combat.walls.damage(pieces[2], ally, 1000.0, run.frame), 0.0)
        self.assertEqual(pieces[2].health, 400.0)
        self.assertIsNotNone(run.match.combat.walls.cast(run.match.combat._eye(ally), (0.0, 0.0, -1.0), 10.0))

    def test_actual_melee_damages_enemy_column_but_not_allied_column(self):
        for allied in (False, True):
            with self.subTest(allied=allied):
                run, owner, attacker, _ = self.scene()
                run.match.game_map = replace(run.match.game_map, free_for_all=False)
                if allied:
                    attacker.team = owner.team
                pieces, _ = self.place(run, owner)
                centre = pieces[2]
                x, y, z = centre.feet
                yaw, pitch = fixtures.aim(run.match.combat._eye(attacker), (x, y + 1., z))
                self.tap(run, attacker, 0x800, (yaw, pitch))
                for _ in range(45):
                    run.step({})
                self.assertEqual(centre.health, 400. if allied else 370.)
                self.assertEqual((pieces[0].health, pieces[-1].health), (400., 400.))
                hits = attacker.client.messages(combat.HIT)
                centre_hits = [hit for hit in hits if hit["+0x78"]["+0x0"]["+0x0"] == centre.entity]
                self.assertEqual(len(centre_hits), 0 if allied else 1)

    def test_quick_melee_cannot_reach_enemy_through_own_ice_wall(self):
        run, owner, enemy, _ = self.scene()
        fight = run.match.combat
        run.match.game_map = replace(run.match.game_map, free_for_all=False)
        x, y, z = owner.mover.position
        fight.walls.place_player(enemy, (x, y, z + 2.5))
        pieces, aim = self.place(run, owner, distance=1.25)
        self.assertEqual(len(pieces), 5)
        target = next(t for t in fight._targets() if t.owner is enemy)
        attack = fixtures.MeiCombatTests().fire

        attack(run, owner, target, 0x800, 0.9)
        self.assertEqual(fight.vitals_of(target).total, 200.0)
        self.assertEqual(owner.client.messages(combat.HIT), [])
        self.assertTrue(all(piece.health == 400.0 for piece in pieces))

        self.tap(run, owner, 16, aim)
        self.assertFalse(fight.walls.pieces)
        for _ in range(20):
            run.step({})
        attack(run, owner, target, 0x800, 0.9)
        self.assertEqual(fight.vitals_of(target).total, 170.0)
        self.assertEqual(len(owner.client.messages(combat.HIT)), 1)

    def test_actual_helix_damages_wall_without_blasting_owner_behind_it(self):
        run, owner, enemy, _target = self.scene()
        pieces, _aim = self.place(run, owner)
        centre = pieces[2]
        point = (centre.feet[0], centre.feet[1] + 1.0, centre.feet[2])
        yaw, pitch = fixtures.aim(run.match.combat._eye(enemy), point)
        for frame in range(45):
            run.step({enemy: (yaw, pitch, 2 if frame == 0 else 0)})
        self.assertEqual(centre.health, 280.0)  # graph direct40 + blast80
        self.assertTrue(all(320.0 <= pieces[i].health < 400.0 for i in (1, 3)))
        owner_target = next(t for t in run.match.combat._targets() if t.owner is owner)
        self.assertEqual(run.match.combat.vitals_of(owner_target).total, 250.0)

    def test_blast_uses_surface_distance_and_destruction_does_not_open_same_blast(self):
        run, owner, enemy, _target = self.scene()
        pieces, _aim = self.place(run, owner)
        fight, centre = run.match.combat, pieces[2]
        # Explicit large explosion isolates surface-distance/occlusion policy.
        centre.health = 10.0
        point = (centre.feet[0], centre.feet[1] + 1.0, centre.feet[2] + centre.depth + 0.1)
        blast = combat.HitDef(80.0, rings=((1.0, 1.0), (10.0, 1.0)))
        rocket = combat.Rocket(enemy, enemy.team, point, (0.0, 0.0, -1.0), 1.0, 1.0, None, blast)
        fight._splash(rocket, point)
        self.assertNotIn(centre.entity, fight.walls.pieces)
        owner_target = next(t for t in fight._targets() if t.owner is owner)
        self.assertEqual(fight.vitals_of(owner_target).total, 250.0)
        self.assertTrue(all(piece.health == 320.0 for piece in pieces if piece is not centre))
        fight._splash(rocket, point)
        self.assertEqual(fight.vitals_of(owner_target).total, 170.0)

    def test_dead_and_frozen_cancel_preview_without_creating_or_spending_cooldown(self):
        for reason in ('dead', 'frozen', 'cryo'):
            with self.subTest(reason=reason):
                run, owner, enemy, _target = self.scene()
                aim = fixtures.aim(run.match.combat._eye(owner),
                                   (owner.mover.position[0], owner.mover.position[1],
                                    owner.mover.position[2] + 3.0))
                self.tap(run, owner, 16, aim)
                self.assertTrue(run.match.combat.walls.inputs[owner.body].preview)
                if reason == 'dead':
                    target = next(t for t in run.match.combat._targets() if t.owner is owner)
                    run.match.combat._damage(enemy, target, 1000.0, False, None, run.frame)
                elif reason == 'frozen':
                    now = round(run.frame * combat.TICK * 1000)
                    run.match.combat.frost_hits[owner.body] = (1.0, now, now + 1300, 0, 0)
                else:
                    self.tap(run, owner, 8, aim)
                self.tap(run, owner, 1, aim)
                self.assertFalse(run.match.combat.walls.inputs[owner.body].preview)
                self.assertFalse(run.match.combat.walls.pieces)
                self.assertEqual(run.match.combat.walls.instance(owner).vars[61].value(), 0.0)

    def test_cryo_entry_wins_over_wall_confirmation_on_the_same_frame(self):
        run, owner, _enemy, _target = self.scene()
        fight = run.match.combat
        x, y, z = owner.mover.position
        aim = fixtures.aim(fight._eye(owner), (x, y, z + 3.))
        wall = fight.walls.instance(owner)
        weapon = next(i for i in owner.body_script.component.instances.values() if i.graph.index == 0x5E6)
        ammo = weapon.vars[53].value()
        self.tap(run, owner, 16, aim)
        self.assertTrue(fight.walls.inputs[owner.body].preview)
        self.tap(run, owner, 8 | FIRE, aim)
        self.assertTrue(mei.protected(owner))
        self.assertFalse(fight.walls.inputs[owner.body].preview)
        self.assertFalse(fight.walls.pieces)
        self.assertFalse(fight.rockets)
        self.assertEqual(wall.vars[61].value(), 0.)
        self.assertEqual(weapon.vars[53].value(), ammo)

        for _ in range(20):
            run.step({})
        self.tap(run, owner, 2, aim)
        self.assertFalse(mei.protected(owner))
        self.assertFalse(fight.walls.pieces)
        cryo = mei.cryo_instance(owner)
        exit_wait = cryo.states[cryo.graph.node(34).state]
        self.assertTrue(exit_wait.active)
        self.tap(run, owner, 16, aim)
        self.assertFalse(fight.walls.inputs[owner.body].preview)
        self.assertEqual(wall.vars[61].value(), 0.)
        for _ in range(65):
            run.step({})
            if not exit_wait.active:
                break
        self.assertFalse(exit_wait.active)
        self.assertFalse(fight.walls.inputs[owner.body].preview)
        self.tap(run, owner, 16, aim)
        self.assertTrue(fight.walls.inputs[owner.body].preview)
        self.tap(run, owner, FIRE, aim)
        self.assertEqual(len(fight.walls.pieces), 5)
        self.assertGreater(wall.vars[61].value(), 11.9)

    def test_cryo_keeps_an_existing_wall_and_allows_manual_removal(self):
        run, owner, _enemy, _target = self.scene()
        fight = run.match.combat
        pieces, aim = self.place(run, owner)
        self.assertEqual(len(pieces), 5)
        ids = set(fight.walls.pieces)
        self.tap(run, owner, 8, aim)
        self.assertTrue(mei.protected(owner))
        self.assertEqual(set(fight.walls.pieces), ids)

        cooldown = fight.walls.instance(owner).vars[61]
        before = cooldown.value()
        self.tap(run, owner, 16, aim)
        self.assertTrue(mei.protected(owner))
        self.assertFalse(fight.walls.pieces)
        self.assertFalse(fight.walls.inputs[owner.body].preview)
        self.assertAlmostEqual(cooldown.value(), before - 2 * combat.TICK, places=4)

    def test_death_keeps_existing_wall_until_its_original_expiry(self):
        run, owner, enemy, _target = self.scene()
        pieces, _aim = self.place(run, owner)
        target = next(t for t in run.match.combat._targets() if t.owner is owner)
        run.match.combat._damage(enemy, target, 1000.0, False, None, run.frame)
        self.tap(run, owner, 16, (0, 0))
        self.assertEqual(len(run.match.combat.walls.pieces), 5)
        self.assertTrue(all(p.expires == pieces[0].expires for p in pieces))
        while run.frame < pieces[0].expires:
            run.step({})
        self.assertFalse(run.match.combat.walls.pieces)

    def test_hero_switch_removes_live_wall_and_restores_enemy_line_of_fire(self):
        run, owner, enemy, _target = self.scene()
        fight = run.match.combat
        pieces, _aim = self.place(run, owner)
        ids = {piece.entity for piece in pieces}
        self.assertEqual(len(ids), 5)
        self.assertTrue(ids.issubset(enemy.seen))

        old_body = owner.body
        old_pose = tuple(owner.mover.position)
        old_target = next(t for t in fight._targets() if t.owner is owner)
        fixtures.MeiCombatTests().fire(run, enemy, old_target, FIRE, 0.9, frames=3)
        self.assertEqual(fight.vitals_of(old_target).total, 250.)
        self.assertEqual(pieces[2].health, 380.)
        for _ in range(15):
            run.step({})

        self.assertTrue(run.match.switch_hero(owner, enemy.hero))
        self.assertNotEqual(owner.body, old_body)
        self.assertNotIn(old_body, fight.walls.inputs)
        self.assertFalse(fight.walls.pieces)
        self.assertFalse(ids.intersection(enemy.seen))
        destroys = [update.entity for update in enemy.client.entities
                    if update.entity in ids and update.op == world.OP_DESTROY]
        self.assertCountEqual(destroys, ids)

        fight.walls.place_player(owner, old_pose)
        target = next(t for t in fight._targets() if t.owner is owner)
        health = fight.vitals_of(target).total
        fixtures.MeiCombatTests().fire(run, enemy, target, FIRE, 0.9, frames=3)
        self.assertEqual(fight.vitals_of(target).total, health - 20.)

    def test_late_viewer_gets_current_pillars_once_and_receives_removal(self):
        run, owner, viewer, _target = self.scene()
        viewer.spawned = False
        pieces, _aim = self.place(run, owner)
        ids = {p.entity for p in pieces}
        self.assertFalse(ids.intersection(viewer.seen))
        viewer.spawned = True
        run.step({})
        run.step({})
        creates = [u for u in viewer.client.entities if u.entity in ids and u.op == world.OP_CREATE]
        self.assertEqual(len(creates), 5)
        run.match.combat.walls.remove(pieces[2])
        self.assertNotIn(pieces[2].entity, viewer.seen)
        self.assertTrue(any(u.entity == pieces[2].entity and u.op == world.OP_DESTROY
                            for u in viewer.client.entities))

    def test_passive_storage_observes_preview_health_geometry_and_expiry(self):
        import test_server_snapshot as snapshots

        from ow174.game.mei_wall import Input
        from ow174.game.server_snapshot import snapshot_digest

        fixture = snapshots.ServerSnapshotTests()
        session, _peers, players = fixture.scene()
        owner = players[0]
        walls = owner.match.combat.walls
        before = snapshot_digest(fixture.capture(session))
        walls.inputs[owner.body] = Input(16, True, False)
        preview = snapshot_digest(fixture.capture(session))
        self.assertNotEqual(before, preview)
        walls.create(owner, [(0.0, 0.0, 3.0)], (1.0, 0.0, 0.0))
        created = snapshot_digest(fixture.capture(session))
        self.assertNotEqual(preview, created)
        piece = next(iter(walls.pieces.values()))
        piece.health -= 1.0
        health = snapshot_digest(fixture.capture(session))
        self.assertNotEqual(created, health)
        piece.expires += 1
        expiry = snapshot_digest(fixture.capture(session))
        self.assertNotEqual(health, expiry)
        piece.axis = (0.0, 0.0, 1.0)
        self.assertNotEqual(expiry, snapshot_digest(fixture.capture(session)))


if __name__ == "__main__":
    unittest.main()
