"""Actual commands through static placement, pair ownership and relocation."""

import os
import unittest
from dataclasses import replace
from unittest.mock import patch

import test_symmetra_static_placement as placement_tests
from test_game_combat import aim, in_world
from test_symmetra_combat import health

from ow174.game import combat, cryo, projectile_tags, stats, symmetra, winston_barrier, zarya_barriers
from ow174.game.collision import World
from ow174.game.commands import CROUCH, Command
from ow174.game.content import SOLDIER, heroes
from ow174.game.script import expr
from ow174.game.symmetra_portals import BODIES


@unittest.skipUnless(os.environ.get('ORPHEUS_EXTRA_GRAPH_BUNDLES'), 'requires the prepared portal actors')
class TeleporterTests(unittest.TestCase):
    def deploy(self):
        run, owner = placement_tests.StaticPlacementTests().scene()
        instance, state = placement_tests.StaticPlacementTests().preview(run, owner, 16, 0x1571, 69)
        self.assertFalse(run.match.combat.portals.pairs)
        run.step({owner: (0, 0, 1)})
        self.assertFalse(state.active)
        self.assertFalse(instance.join_inputs)
        self.assertTrue(instance.node_state(instance.graph.node(2)).active)
        [pair] = run.match.combat.portals.pairs
        return run, owner, instance, pair

    @staticmethod
    def place(player, position):
        player.mover.position[:] = position
        if player.mover.body is not None:
            x, y, z = position
            player.mover.body.teleport((x, y + player.mover._lift(), z))

    @staticmethod
    def until(run, deadline):
        while run.match.combat.now < deadline:
            run.step({})

    def test_confirm_creates_two_authored_bodies_and_owner_can_use_both_ways(self):
        run, owner, instance, pair = self.deploy()
        fight = run.match.combat
        destination, entrance = pair.endpoints
        self.assertEqual(tuple(bot.body for bot in pair.endpoints), BODIES)
        self.assertEqual(entrance.position, (0, 0, 0))
        self.assertGreater(destination.position[2], 29)
        for var, bot in zip((1624, 1625), pair.endpoints, strict=True):
            self.assertEqual(instance.find_var(1, var).value(), expr.Entity(bot.entity))
            self.assertEqual(fight.vitals[bot.entity].now, {0: 50, 1: 0, 2: 250})
        run.step({owner: (0, 0, 4)})
        self.assertEqual(owner.mover.position[2], 0)
        self.until(run, pair.ready_at)
        run.step({owner: (0, 0, 4)})
        self.assertAlmostEqual(owner.mover.position[2], destination.position[2])
        run.step({})
        run.step({owner: (0, 0, 4)})
        self.assertAlmostEqual(owner.mover.position[2], destination.position[2])
        self.until(run, fight.now + 1.1)
        run.step({owner: (0, 0, 4)})
        self.assertEqual(owner.mover.position[2], 0)

    def test_enemy_rifle_uses_transit_discontinuity_on_actual_shot_command(self):
        for view_delay, exit_aim in ((1, False), (0, False), (0, True)):
            with self.subTest(view_delay=view_delay, exit_aim=exit_aim):
                self.check_enemy_rifle_transit_view(view_delay, exit_aim)

    def check_enemy_rifle_transit_view(self, view_delay, exit_aim):
        run, owner, _, pair = self.deploy()
        fight = run.match.combat
        enemy = run.players[1]
        self.place(owner, (.8, 0, 0))
        self.place(enemy, (6, 0, 16))
        self.until(run, pair.ready_at)
        pool = health(run, owner)
        entrance = tuple(owner.mover.position)
        run.frame += 1
        run.match.update(run.frame * combat.TICK, run.frame)
        owner.take_commands([Command(run.frame, buttons=4)])
        self.assertIn(owner.body, fight.portals.transits)
        destination = tuple(owner.mover.position)
        self.assertGreater(destination[2], 29)
        x, y, z = destination if exit_aim else entrance
        yaw, pitch = aim(fight._eye(enemy), (x, y + 1.7, z))
        with patch.object(fight, "_hitscan", wraps=fight._hitscan) as shots:
            enemy.take_commands([Command(run.frame, yaw=yaw, pitch=pitch, buttons=1, extra=view_delay)])
        self.assertEqual(shots.call_count, 1)
        self.assertIs(shots.call_args.args[0], enemy)
        self.assertEqual(pool.total, 185 if exit_aim else 225)
        self.assertEqual([fight.vitals[endpoint.entity].total for endpoint in pair.endpoints], [300, 300])

    def test_held_primary_retargets_from_teleporter_exit(self):
        self.check_primary_through_teleporter()

    def test_exit_cover_blocks_held_primary_on_teleporter_transit(self):
        self.check_primary_through_teleporter(cover="static")

    def test_exit_ice_wall_takes_held_primary_until_mei_cancels(self):
        self.check_primary_through_teleporter(cover="ice")

    def test_live_ice_wall_occupying_exit_blocks_use_until_removed(self):
        for ending in ("cancel", "break", "expire"):
            with self.subTest(ending=ending):
                self.check_ice_wall_at_destination(ending)

    def check_ice_wall_at_destination(self, ending):
        run, owner, _, pair = self.deploy()
        fight = run.match.combat
        mei = run.players[1]
        self.assertTrue(run.match.switch_hero(mei, heroes()[cryo.HERO]))
        exit_z = pair.endpoints[0].position[2]
        self.place(mei, (0, 0, exit_z + 6))
        self.until(run, pair.ready_at)
        angles = aim(fight._eye(mei), (0, 0, exit_z))
        for button in (16, 0, 1, 0):
            run.step({mei: (*angles, button)})
        self.assertEqual(len(fight.ice_wall.pieces), 5)
        center = min(fight.ice_wall.pieces.values(), key=lambda piece: abs(piece.feet[0]))
        self.assertAlmostEqual(center.feet[2], exit_z, places=2)
        start = tuple(owner.mover.position)
        run.step({owner: (0, 0, 4)})
        self.assertEqual(tuple(owner.mover.position), start)
        self.assertNotIn(owner.body, fight.portals.transits)
        self.assertFalse(any(i.graph.index == 0x1647 for i in owner.body_script.component.instances.values()))
        self.assertIn(pair, fight.portals.pairs)
        self.assertEqual([health(run, endpoint).total for endpoint in pair.endpoints], [300, 300])

        if ending == "cancel":
            run.step({owner: (0, 0, 4), mei: (*angles, 16)})
            self.assertFalse(fight.ice_wall.pieces)
        elif ending == "expire":
            while run.frame < center.expires:
                run.step({owner: (0, 0, 4)})
            self.assertFalse(fight.ice_wall.pieces)
        else:
            soldier = run.match.add_player(3, "Soldier", SOLDIER, owner.team, False)
            in_world(run.match, soldier)
            run.players.append(soldier)
            self.assertTrue(run.match.switch_hero(soldier, heroes()[SOLDIER]))
            self.place(soldier, (0, 0, exit_z - 6))
            rifle_aim = aim(fight._eye(soldier), (0, 1.2, exit_z))
            for _ in range(180):
                if center.entity not in fight.ice_wall.pieces:
                    break
                run.step({owner: (0, 0, 4), soldier: (*rifle_aim, 1)})
            self.assertNotIn(center.entity, fight.ice_wall.pieces)
            self.assertEqual(len(fight.ice_wall.pieces), 4)
            self.assertLess(run.frame, center.expires)
        run.step({owner: (0, 0, 4)})
        self.assertNotIn(owner.body, fight.portals.transits)
        run.step({})
        run.step({owner: (0, 0, 4)})
        self.assertIn(owner.body, fight.portals.transits)
        self.assertAlmostEqual(owner.mover.position[2], exit_z)
        self.assertEqual([health(run, endpoint).total for endpoint in pair.endpoints], [300, 300])

    def test_static_geometry_inside_exit_rejects_transit_without_lockout(self):
        for returning in (False, True):
            with self.subTest(returning=returning):
                self.check_static_blocked_destination(returning)

    def check_static_blocked_destination(self, returning):
        run, owner, _, pair = self.deploy()
        fight = run.match.combat
        self.until(run, pair.ready_at)
        if returning:
            run.step({owner: (0, 0, 4)})
            self.assertIn(owner.body, fight.portals.transits)
            self.until(run, fight.portals.transits[owner.body][1])
        destination_z = pair.endpoints[1 if returning else 0].position[2]
        source_z = owner.mover.position[2]
        original = run.match.collision_world
        self.block_destination(run, destination_z)
        run.step({owner: (0, 0, 4)})
        self.assertEqual(owner.mover.position[2], source_z)
        self.assertNotIn(owner.body, fight.portals.transits)
        self.assertIn(pair, fight.portals.pairs)
        run.match.collision_world = original
        run.step({})
        run.step({owner: (0, 0, 4)})
        self.assertIn(owner.body, fight.portals.transits)
        self.assertAlmostEqual(owner.mover.position[2], destination_z)

    @staticmethod
    def block_destination(run, z):
        floor = [(-100, 0, -100, -100, 0, 100, 100, 0, 100),
                 (-100, 0, -100, 100, 0, 100, 100, 0, -100)]
        wall = [(-4, 0, z, 4, 0, z, 4, 5, z), (-4, 0, z, 4, 5, z, -4, 5, z)]
        run.match.collision_world = World(floor + wall)

    def test_exit_under_overhang_uses_current_stance(self):
        run, owner, _, pair = self.deploy()
        fight = run.match.combat
        self.until(run, pair.ready_at)
        exit_z = pair.endpoints[0].position[2]
        floor = [(-100, 0, -100, -100, 0, 100, 100, 0, 100),
                 (-100, 0, -100, 100, 0, 100, 100, 0, -100)]
        y = 2.2
        a, b, c, d = ((-2, y, exit_z - 2), (2, y, exit_z - 2),
                      (2, y, exit_z + 2), (-2, y, exit_z + 2))
        run.match.collision_world = World([*floor, (*a, *b, *c), (*a, *c, *d)])
        run.step({owner: (0, 0, 4)})
        self.assertFalse(owner.mover.crouched)
        self.assertNotIn(owner.body, fight.portals.transits)
        self.assertEqual(owner.mover.position[2], 0)
        for _ in range(20):
            run.step({owner: (0, 0, CROUCH)})
        self.assertTrue(owner.mover.crouched)
        run.step({owner: (0, 0, CROUCH | 4)})
        self.assertTrue(owner.mover.crouched)
        self.assertIn(owner.body, fight.portals.transits)
        self.assertAlmostEqual(owner.mover.position[2], exit_z)

    def test_exit_ice_pillar_absorbs_breaking_beam_tick(self):
        self.check_primary_through_teleporter(cover="ice", break_pillar=True)

    def test_exit_cryo_blocks_held_primary_and_protects_rear_enemy(self):
        self.check_primary_through_teleporter(cover="cryo")

    @unittest.skipUnless(projectile_tags.has_tag(winston_barrier.DEFINITION, 0x0D6000000000003A),
                         "requires authored Winston shield tag")
    def test_exit_winston_barrier_absorbs_held_primary_until_break(self):
        self.check_primary_through_teleporter(cover="winston")

    def test_exit_zarya_barrier_absorbs_transit_beam_and_grants_energy(self):
        self.check_primary_through_teleporter(cover="zarya")

    def test_exit_projected_barrier_absorbs_transit_beam_and_credits_caster(self):
        self.check_primary_through_teleporter(cover="projected")

    def check_primary_through_teleporter(self, *, cover=None, break_pillar=False):
        run, owner, _, pair = self.deploy()
        fight = run.match.combat
        entrance_target = run.players[1]
        exit_target = run.match.add_player(3, "Exit target", SOLDIER, 1 - owner.team, False)
        in_world(run.match, exit_target)
        run.players.append(exit_target)
        exit_hero = {"ice": cryo.HERO, "cryo": cryo.HERO,
                     "winston": winston_barrier.HERO, "zarya": zarya_barriers.HERO}.get(cover, SOLDIER)
        self.assertTrue(run.match.switch_hero(exit_target, heroes()[exit_hero]))
        exit_z = pair.endpoints[0].position[2]
        if cover == "static":
            z = exit_z + 3
            floor = [(-100, 0, -100, -100, 0, 100, 100, 0, 100),
                     (-100, 0, -100, 100, 0, 100, 100, 0, -100)]
            wall = [(-4, 0, z, 4, 0, z, 4, 5, z), (-4, 0, z, 4, 5, z, -4, 5, z)]
            run.match.collision_world = World(floor + wall)
        self.place(entrance_target, (0, 0, 6))
        self.place(exit_target, (0, 0, exit_z + 6))
        barrier_caster = exit_target
        if cover == "projected":
            run.match.game_map = replace(run.match.game_map, free_for_all=False)
            entrance_target.team = exit_target.team = 1 - owner.team
            barrier_caster = run.match.add_player(4, "Zarya", SOLDIER, exit_target.team, False)
            in_world(run.match, barrier_caster)
            run.players.append(barrier_caster)
            self.assertTrue(run.match.switch_hero(barrier_caster, heroes()[zarya_barriers.HERO]))
            self.place(barrier_caster, (3, 0, exit_z + 6))
        rear_pools = None
        if cover == "cryo":
            rear = run.match.add_player(4, "Behind Mei", SOLDIER, exit_target.team, False)
            in_world(run.match, rear)
            run.players.append(rear)
            self.assertTrue(run.match.switch_hero(rear, heroes()[SOLDIER]))
            self.place(rear, (0, 0, exit_z + 8))
            rear_pools = health(run, rear)
        self.until(run, pair.ready_at)
        angles = aim(fight._eye(owner), (0, 1, 6))
        entrance_pools, exit_pools = health(run, entrance_target), health(run, exit_target)
        weapon = next(i for i in owner.body_script.component.instances.values() if i.graph.index == 0x157C)
        rate = weapon.find_var(0, 499)
        for _ in range(130):
            run.step({owner: (*angles, 1)})
            if rate.value() == 120:
                break
        self.assertEqual(rate.value(), 120)
        self.assertGreater(entrance_pools.total, 0)
        self.assertLess(entrance_pools.total, 200)
        exit_before = exit_pools.total
        if cover == "cryo":
            run.step({owner: (*angles, 1), exit_target: (0, 0, 8)})
            self.assertTrue(cryo.active(exit_target))
        if cover == "winston":
            run.step({owner: (*angles, 1), exit_target: (0, 0, 16)})
            for _ in range(3):
                run.step({owner: (*angles, 1)})
            sphere = fight.winston_barriers.actors[exit_target.body]
            self.assertEqual(sphere.health, 700)
        if cover in ("zarya", "projected"):
            defense = (-16384, 0, 16) if cover == "projected" else (0, 0, 8)
            run.step({owner: (*angles, 1), barrier_caster: defense})
            source, _, bubble = fight.zarya_barriers.contact(exit_target)
            self.assertIs(source, barrier_caster)
            self.assertEqual(bubble.remaining, 200)
            energy = barrier_caster.body_script.component.entity_var(87)
            caster_before = health(run, barrier_caster).total
            self.assertFalse(energy.value())
        if cover == "ice":
            wall_aim = aim(fight._eye(exit_target), (0, 0, exit_z + 3))
            for button in (16, 0, 1, 0):
                run.step({owner: (*angles, 1), exit_target: (*wall_aim, button)})
            pieces = list(fight.ice_wall.pieces.values())
            self.assertEqual(len(pieces), 5)
            self.assertTrue(all(piece.health == 400 for piece in pieces))
            center = min(pieces, key=lambda piece: abs(piece.feet[0]))
        self.assertEqual(exit_pools.total, exit_before)
        before, hits_before = entrance_pools.total, len(owner.client.messages(combat.HIT))

        run.step({owner: (*angles, 1 | 4)})
        self.assertAlmostEqual(owner.mover.position[2], exit_z)
        self.assertEqual(entrance_pools.total, before)
        self.assertEqual(rate.value(), 120)
        expected_damage = 0 if cover else 120 * combat.TICK
        self.assertAlmostEqual(exit_before - exit_pools.total, expected_damage, places=4)
        if cover == "ice":
            self.assertAlmostEqual(400 - center.health, 120 * combat.TICK, places=4)
        if cover == "cryo":
            self.assertTrue(cryo.active(exit_target))
            self.assertEqual(rear_pools.total, 200)
        if cover == "winston":
            self.assertAlmostEqual(700 - sphere.health, 120 * combat.TICK, places=4)
            ammo_after_transit = weapon.find_var(0, 53).value()
        if cover in ("zarya", "projected"):
            self.assertAlmostEqual(200 - bubble.remaining, 120 * combat.TICK, places=4)
            self.assertAlmostEqual(energy.value(), 120 * combat.TICK * .2, places=4)
        for _ in range(20 if cover == "cryo" else 8):
            run.step({owner: (*angles, 1)})
        self.assertEqual(entrance_pools.total, before)
        hits = owner.client.messages(combat.HIT)[hits_before:]
        if cover:
            self.assertEqual(exit_pools.total, exit_before)
            self.assertFalse(hits)
            if cover == "static":
                return
            if cover == "cryo":
                self.assertEqual(rear_pools.total, 200)
                run.step({owner: (*angles, 1), exit_target: (0, 0, 1)})
                self.assertFalse(cryo.active(exit_target))
            elif cover == "winston":
                self.assertGreater(weapon.find_var(0, 53).value(), ammo_after_transit)
                while fight.now + combat.TICK < sphere.expires and sphere.health > 0:
                    run.step({owner: (*angles, 1)})
                    self.assertEqual(exit_pools.total, exit_before)
                self.assertEqual(sphere.health, 0)
                self.assertLess(fight.now, sphere.expires)
                self.assertEqual(len(owner.client.messages(combat.HIT)), hits_before)
            elif cover in ("zarya", "projected"):
                for _ in range(120):
                    run.step({owner: (*angles, 1)})
                    self.assertEqual(exit_pools.total, exit_before)
                    if not bubble.remaining:
                        break
                self.assertEqual(bubble.remaining, 0)
                self.assertIsNone(fight.zarya_barriers.contact(exit_target))
                self.assertAlmostEqual(energy.value(), 40, places=4)
                self.assertEqual(len(owner.client.messages(combat.HIT)), hits_before)
            elif break_pillar:
                while run.frame < center.expires - 1:
                    run.step({owner: (*angles, 1)})
                    self.assertEqual(exit_pools.total, exit_before)
                    if center.entity not in fight.ice_wall.pieces:
                        break
                self.assertEqual(center.health, 0)
                self.assertLess(run.frame, center.expires)
                self.assertEqual(len(fight.ice_wall.pieces), 4)
                self.assertEqual(len(owner.client.messages(combat.HIT)), hits_before)
            else:
                run.step({owner: (*angles, 1), exit_target: (*wall_aim, 16)})
                self.assertFalse(fight.ice_wall.pieces)
            if cover == "ice":
                self.assertTrue(all(piece.health == 400 for piece in pieces if piece is not center))
            self.assertEqual(exit_pools.total, exit_before)
            run.step({owner: (*angles, 1)})
            hits = owner.client.messages(combat.HIT)[hits_before:]
        self.assertLess(exit_pools.total, exit_before)
        self.assertEqual(entrance_pools.total, before)
        if cover == "projected":
            self.assertEqual(health(run, barrier_caster).total, caster_before)
        if rear_pools is not None:
            self.assertEqual(rear_pools.total, 200)
        self.assertTrue(hits)
        self.assertTrue(all(hit["+0x78"]["+0x0"] == {"+0x0": exit_target.body} for hit in hits))

    def test_flashbang_blocks_teleporter_until_stun_deadline(self):
        for held in (False, True):
            with self.subTest(held=held):
                self.check_teleporter_after_flashbang(held)

    def check_teleporter_after_flashbang(self, held):
        run, owner, _, pair = self.deploy()
        enemy = run.players[1]
        self.assertTrue(run.match.switch_hero(enemy, heroes()[0x02E0000000000042]))
        self.place(enemy, (0, 0, 2))
        self.until(run, pair.ready_at)
        fight = run.match.combat
        angles = aim(fight._eye(enemy), (0, 1, 0))
        run.step({enemy: (*angles, 16)})
        for _ in range(25):
            if fight.is_stunned(owner):
                break
            run.step({})
        self.assertTrue(fight.is_stunned(owner))
        self.assertEqual(health(run, owner).total, 200)
        deadline = fight.stuns[owner.body]

        run.step({owner: (0, 0, 4)})
        while run.frame < deadline - 1:
            run.step({owner: (0, 0, 4 if held else 0)})
            self.assertEqual(owner.mover.position[2], 0)
            self.assertNotIn(owner.body, fight.portals.transits)
        run.step({owner: (0, 0, 4 if held else 0)})
        self.assertEqual(run.frame, deadline)
        self.assertFalse(fight.is_stunned(owner))
        exit_z = pair.endpoints[0].position[2]
        self.assertAlmostEqual(owner.mover.position[2], exit_z if held else 0)
        if not held:
            run.step({owner: (0, 0, 4)})
            self.assertAlmostEqual(owner.mover.position[2], exit_z)

    def test_non_owner_requires_same_team_and_endpoint_range(self):
        run, owner, _, pair = self.deploy()
        other = run.players[1]
        self.until(run, pair.ready_at)
        run.match.game_map = replace(run.match.game_map, free_for_all=False)
        other.team = 1 - owner.team
        self.place(other, (0, 0, 1))
        run.step({other: (0, 0, 4)})
        self.assertAlmostEqual(other.mover.position[2], 1)
        other.team = owner.team
        self.place(other, (0, 0, 2))
        run.step({})
        run.step({other: (0, 0, 4)})
        self.assertAlmostEqual(other.mover.position[2], 2)
        self.place(other, (0, 0, 1))
        run.step({})
        run.step({other: (0, 0, 4)})
        self.assertAlmostEqual(other.mover.position[2], pair.endpoints[0].position[2])

    def test_flashbang_cancels_charge_and_blocks_portal_without_late_orb(self):
        self.check_charged_portal_against_flashbang(protected=False)

    def test_projected_barrier_preserves_charge_for_transit_frame_release(self):
        for entrance_offset, level_aim in ((0, True), (0, False), (-.8, False)):
            with self.subTest(entrance_offset=entrance_offset, level_aim=level_aim):
                self.check_charged_portal_against_flashbang(
                    protected=True, entrance_offset=entrance_offset, level_aim=level_aim,
                )

    def check_charged_portal_against_flashbang(self, *, protected, entrance_offset=0, level_aim=False):
        run, owner, _, pair = self.deploy()
        fight = run.match.combat
        caster = run.players[1]
        self.assertTrue(run.match.switch_hero(caster, heroes()[0x02E0000000000042]))
        self.place(caster, (0, 0, 2))
        target = run.match.add_player(3, "Exit target", SOLDIER, 1 - owner.team, False)
        in_world(run.match, target)
        run.players.append(target)
        self.assertTrue(run.match.switch_hero(target, heroes()[SOLDIER]))
        exit_z = pair.endpoints[0].position[2]
        self.place(target, (0, 0, exit_z + 8))
        if protected:
            run.match.game_map = replace(run.match.game_map, free_for_all=False)
            caster.team = target.team = 1 - owner.team
            zarya = run.match.add_player(4, "Zarya", SOLDIER, owner.team, False)
            in_world(run.match, zarya)
            run.players.append(zarya)
            self.assertTrue(run.match.switch_hero(zarya, heroes()[zarya_barriers.HERO]))
            self.place(owner, (0, 0, entrance_offset))
            self.place(zarya, (3, 0, entrance_offset))
        self.until(run, pair.ready_at)
        aim_in = aim(fight._eye(owner), (0, 1, 2))
        flash = aim(fight._eye(caster), fight._eye(owner))
        if protected:
            shield_aim = (-16384, 0) if level_aim else aim(fight._eye(zarya), fight._eye(owner))
        weapon = next(i for i in owner.body_script.component.instances.values()
                      if i.graph.index == symmetra.WEAPON)
        ammo = weapon.find_var(0, 53)
        for _ in range(90 if protected else 20):
            commands = {owner: (*aim_in, 2)}
            if protected:
                commands[zarya] = (*shield_aim, 0)
            run.step(commands)
        self.assertLess(ammo.value(), weapon.find_var(0, 198).value())
        self.assertFalse(fight.rockets)
        commands = {owner: (*aim_in, 2), caster: (*flash, 16)}
        if protected:
            commands[zarya] = (*shield_aim, 16)
        run.step(commands)
        self.assertTrue(fight.rockets)
        if protected:
            projected = next(i for i in zarya.body_script.component.instances.values()
                             if i.graph.index == 0x025C)
            self.assertEqual(projected.find_var(0, 536).value(), expr.Entity(owner.body),
                             f"entrance actor is {pair.endpoints[1].entity:#x}")
            source, _, bubble = fight.zarya_barriers.contact(owner)
            self.assertIs(source, zarya)
            self.assertEqual(bubble.remaining, 200)
        for _ in range(25):
            if not fight.rockets:
                break
            run.step({owner: (*aim_in, 2)})
        self.assertFalse(fight.rockets)
        if protected:
            self.assertEqual(bubble.remaining, 175)
            self.assertAlmostEqual(zarya.body_script.component.entity_var(87).value(), 5)
            self.assertFalse(fight.is_stunned(owner))
            self.assertEqual(health(run, owner).total, 225)
            release_aim = aim(fight._eye(owner), (0, .9, owner.mover.position[2] + 8))
            run.step({owner: (*release_aim, 4)})
            self.assertAlmostEqual(owner.mover.position[2], exit_z)
            self.assertEqual(ammo.value(), weapon.find_var(0, 198).value() - 10)
            self.check_exit_orb_hit(run, owner, target, exit_z)
            self.assertEqual(health(run, caster).total, 225)
            return
        self.assertTrue(fight.is_stunned(owner))
        self.assertEqual(health(run, owner).total, 200)
        deadline = fight.stuns[owner.body]
        while run.frame < deadline:
            run.step({owner: (*aim_in, 4)})
            self.assertFalse(fight.rockets)
            self.assertFalse(owner.body_script.component.shots)
            if run.frame < deadline:
                self.assertEqual(owner.mover.position[2], 0)
                self.assertNotIn(owner.body, fight.portals.transits)
        self.assertAlmostEqual(owner.mover.position[2], exit_z)
        self.assertFalse(fight.is_stunned(owner))
        self.assertEqual(health(run, caster).total, 225)
        self.assertEqual(health(run, target).total, 200)
        self.assertFalse(owner.client.messages(combat.HIT))

        aim_out = aim(fight._eye(owner), (0, .9, exit_z + 8))
        for _ in range(90):
            run.step({owner: (*aim_out, 2)})
            self.assertFalse(fight.rockets)
        run.step({owner: (*aim_out, 0)})
        self.check_exit_orb_hit(run, owner, target, exit_z)

    def check_exit_orb_hit(self, run, owner, target, exit_z):
        fight = run.match.combat
        [orb] = fight.rockets
        self.assertIs(orb.shooter, owner)
        self.assertAlmostEqual(orb.position[2], exit_z)
        for _ in range(35):
            run.step({})
        self.assertFalse(fight.rockets)
        self.assertEqual(health(run, target).total, 80)

    def test_orb_keeps_flight_and_attribution_through_teleporter(self):
        for allied in (False, True):
            with self.subTest(allied=allied):
                self.check_orb_transit(allied)

    def test_charged_orb_releases_from_exit_on_or_after_transit(self):
        for release_on_transit in (True, False):
            with self.subTest(release_on_transit=release_on_transit):
                spent, damage = self.fire_orb_at_portal(
                    90, transit=True, release_on_transit=release_on_transit,
                )
                self.assertEqual(spent, 10)
                self.assertAlmostEqual(damage, 120)

    def test_partial_orb_preserves_ammo_cost_and_damage_across_transit(self):
        expected_ammo, expected_damage = self.fire_orb_at_portal(20)
        self.assertGreater(expected_ammo, 0)
        self.assertLess(expected_ammo, 10)
        self.assertGreater(expected_damage, 0)
        self.assertLess(expected_damage, 120)
        for release_on_transit in (True, False):
            with self.subTest(release_on_transit=release_on_transit):
                charge_before_transit = 20 if release_on_transit else 19
                spent, damage = self.fire_orb_at_portal(
                    charge_before_transit, transit=True, release_on_transit=release_on_transit,
                )
                self.assertAlmostEqual(spent, expected_ammo, places=4)
                self.assertAlmostEqual(damage, expected_damage, places=4)

    def test_blocked_destination_releases_partial_and_full_orbs_from_entrance(self):
        for charge_frames in (20, 90):
            with self.subTest(charge_frames=charge_frames):
                expected_ammo, expected_damage = self.fire_orb_at_portal(charge_frames)
                spent, damage = self.fire_orb_at_portal(charge_frames, blocked=True, release_on_transit=True)
                self.assertAlmostEqual(spent, expected_ammo)
                self.assertAlmostEqual(damage, expected_damage)

    def fire_orb_at_portal(self, charge_frames, *, transit=False, release_on_transit=False, blocked=False):
        run, owner, _, pair = self.deploy()
        fight = run.match.combat
        entrance_target = run.players[1]
        exit_target = run.match.add_player(3, "Exit target", SOLDIER, 1 - owner.team, False)
        in_world(run.match, exit_target)
        run.players.append(exit_target)
        self.assertTrue(run.match.switch_hero(exit_target, heroes()[SOLDIER]))
        exit_z = pair.endpoints[0].position[2]
        self.place(entrance_target, (0, 0, 8))
        self.place(exit_target, (0, 0, exit_z + 8))
        if blocked:
            self.block_destination(run, exit_z)
        self.until(run, pair.ready_at)
        angles = aim(fight._eye(owner), (0, .9, 8))
        weapon = next(i for i in owner.body_script.component.instances.values()
                      if i.graph.index == symmetra.WEAPON)
        ammo = weapon.find_var(0, 53)
        before_ammo = ammo.value()
        for _ in range(charge_frames):
            run.step({owner: (*angles, 2)})
        self.assertFalse(fight.rockets)
        if transit or blocked:
            run.step({owner: (*angles, 4 if release_on_transit else 2 | 4)})
            self.assertEqual(owner.body in fight.portals.transits, transit)
            self.assertAlmostEqual(owner.mover.position[2], exit_z if transit else 0)
            if not release_on_transit:
                self.assertFalse(fight.rockets)
        if not release_on_transit:
            run.step({owner: (*angles, 0)})

        [orb] = fight.rockets
        self.assertIs(orb.shooter, owner)
        self.assertAlmostEqual(orb.position[2], exit_z if transit else 0)
        spent = before_ammo - ammo.value()
        entrance_pools, exit_pools = health(run, entrance_target), health(run, exit_target)
        self.assertEqual((entrance_pools.total, exit_pools.total), (200, 200))
        for _ in range(35):
            run.step({})
            if orb not in fight.rockets:
                break
        self.assertNotIn(orb, fight.rockets)
        hit, untouched = (exit_pools, entrance_pools) if transit else (entrance_pools, exit_pools)
        damage = 200 - hit.total
        self.assertEqual(untouched.total, 200)
        self.assertAlmostEqual(owner.stats.value(stats.HERO_DAMAGE), damage)
        return spent, damage

    def check_orb_transit(self, allied):
        run, deployer, _, pair = self.deploy()
        fight = run.match.combat
        enemy = run.players[1]
        run.match.game_map = replace(run.match.game_map, free_for_all=False)
        enemy.team = 1 - deployer.team
        owner = deployer
        if allied:
            owner = run.match.add_player(3, "Orb shooter", SOLDIER, deployer.team, False)
            in_world(run.match, owner)
            run.players.append(owner)
            self.assertTrue(run.match.switch_hero(owner, heroes()[symmetra.HERO]))
        self.place(owner, (0, 0, 0))
        self.place(enemy, (0, 0, 8))
        self.until(run, pair.ready_at)
        angles = aim(fight._eye(owner), (0, .9, 8))
        before = health(run, enemy).total
        for _ in range(90):
            run.step({owner: (*angles, 2)})
        self.assertEqual(health(run, enemy).total, before)
        run.step({owner: (*angles, 0)})
        [orb] = fight.rockets
        self.assertIs(orb.shooter, owner)
        origin_z = orb.position[2]
        self.assertEqual(health(run, enemy).total, before)

        run.step({owner: (*angles, 4)})
        self.assertAlmostEqual(owner.mover.position[2], pair.endpoints[0].position[2])
        self.assertIn(orb, fight.rockets)
        self.assertGreater(orb.position[2], origin_z)
        self.assertLess(orb.position[2], enemy.mover.position[2])
        for _ in range(35):
            run.step({})
            if orb not in fight.rockets:
                break
        self.assertNotIn(orb, fight.rockets)
        self.assertEqual(health(run, enemy).total, before - 120)
        self.assertAlmostEqual(owner.stats.value(stats.HERO_DAMAGE), 120)
        hits = owner.client.messages(combat.HIT)
        self.assertTrue(hits)
        self.assertTrue(all(hit["+0x78"]["+0x0"] == {"+0x0": enemy.body} for hit in hits))
        self.assertEqual(sum(hit["+0x78"]["+0x4"] for hit in hits), 120)
        if allied:
            self.assertFalse(deployer.client.messages(combat.HIT))

        distant = aim(fight._eye(owner), (0, .9, 8))
        for _ in range(12):
            run.step({owner: (*distant, 1)})
        self.assertEqual(health(run, enemy).total, before - 120)

    def test_actual_enemy_damage_destroys_pair_and_starts_cooldown(self):
        run, owner, instance, pair = self.deploy()
        fight = run.match.combat
        enemy = run.players[1]
        target, _ = pair.endpoints
        self.until(run, pair.ready_at)
        yaw, pitch = aim(fight._eye(enemy), (0, .85, target.position[2]))
        shield_before = fight.vitals[target.entity].now[2]
        for _ in range(35):
            run.step({enemy: (yaw, pitch, 1)})
        self.assertLess(fight.vitals[target.entity].now[2], shield_before)
        self.assertEqual(fight.vitals[target.entity].now[0], 50)
        for _ in range(260):
            if not fight.portals.pairs:
                break
            run.step({enemy: (yaw, pitch, 1)})
        self.assertFalse(fight.portals.pairs)
        self.assertTrue(all(bot not in run.match.bots for bot in pair.endpoints))
        self.assertGreater(instance.find_var(0, 61).value(), 9)
        run.step({owner: (0, 0, 4)})
        self.assertEqual(owner.mover.position[2], 0)

    def test_static_cover_blocks_interaction_inside_radius(self):
        run, owner, _, pair = self.deploy()
        self.until(run, pair.ready_at)
        floor = [(-50, 0, -50, -50, 0, 50, 50, 0, 50), (-50, 0, -50, 50, 0, 50, 50, 0, -50)]
        cover = [(-5, 0, .5, 5, 0, .5, 5, 5, .5), (-5, 0, .5, 5, 5, .5, -5, 5, .5)]
        collision = World(floor + cover)
        run.match.collision_world = collision
        owner.mover.use(world=collision)
        self.place(owner, (0, 0, 1))
        run.step({owner: (0, 0, 4)})
        self.assertGreater(owner.mover.position[2], .5)
        self.assertLess(owner.mover.position[2], 1.5)
        self.assertNotIn(owner.body, run.match.combat.portals.transits)

    def test_enemy_portal_damage_does_not_credit_character_charge(self):
        run, owner, _, pair = self.deploy()
        fight = run.match.combat
        shooter = run.players[1]
        self.assertTrue(run.match.switch_hero(shooter, owner.hero))
        shooter.mover.use(world=run.match.collision_world)
        shooter.body_script.component.set(12918, 0)
        self.until(run, pair.ready_at)
        target = pair.endpoints[0]
        self.place(shooter, (0, 0, target.position[2] - 6))
        yaw, pitch = aim(fight._eye(shooter), (0, .85, target.position[2]))
        before = fight.vitals[target.entity].total
        for _ in range(20):
            run.step({shooter: (yaw, pitch, 1)})
        self.assertLess(fight.vitals[target.entity].total, before)
        self.assertEqual(shooter.body_script.component.entity_var(342).value(), 0)

    def test_pair_survives_actual_owner_death_and_dead_owner_cannot_use_it(self):
        run, owner, _, pair = self.deploy()
        fight = run.match.combat
        enemy = run.players[1]
        self.until(run, pair.ready_at)
        self.place(owner, (1.25, 0, 0))
        self.place(enemy, (1.25, 0, 8))
        yaw, pitch = aim(fight._eye(enemy), (1.25, 1.2, 0))
        for _ in range(180):
            run.step({enemy: (yaw, pitch, 1)})
            if fight.is_dead(owner):
                break
        self.assertTrue(fight.is_dead(owner))
        self.assertIn(pair, fight.portals.pairs)
        before = tuple(owner.mover.position)
        run.step({owner: (0, 0, 4)})
        self.assertEqual(tuple(owner.mover.position), before)

    def test_pair_persists_and_owner_dismissal_cleans_both_before_cooldown(self):
        run, owner, instance, pair = self.deploy()
        self.until(run, pair.ready_at + 12)
        self.assertIn(pair, run.match.combat.portals.pairs)
        run.step({owner: (0, 0, 16)})
        self.assertFalse(run.match.combat.portals.pairs)
        self.assertTrue(all(bot not in run.match.bots for bot in pair.endpoints))
        self.assertFalse(instance.node_state(instance.graph.node(69)).active)
        self.assertGreater(instance.find_var(0, 61).value(), 9)
        self.until(run, run.match.combat.now + 10.1)
        run.step({owner: (0, 0, 16)})
        self.assertTrue(instance.node_state(instance.graph.node(69)).active)

    def test_no_static_floor_does_not_create_pair(self):
        run, owner = placement_tests.StaticPlacementTests().scene(world=World([]))
        placement_tests.StaticPlacementTests().preview(run, owner, 16, 0x1571, 69)
        run.step({owner: (0, 0, 1)})
        for _ in range(60):
            run.step({})
        self.assertFalse(run.match.combat.portals.pairs)

    def test_cancel_preview_allows_fresh_placement_without_creating_pair(self):
        run, owner = placement_tests.StaticPlacementTests().scene()
        _, state = placement_tests.StaticPlacementTests().preview(run, owner, 16, 0x1571, 69)
        run.step({owner: (0, 0, 2)})
        self.until(run, run.match.combat.now + .8)
        self.assertFalse(state.active)
        self.assertFalse(run.match.combat.portals.pairs)
        run.step({owner: (0, 0, 16)})
        self.assertTrue(state.active)
        run.step({owner: (0, 0, 1)})
        self.assertEqual(len(run.match.combat.portals.pairs), 1)

    def test_owner_leave_and_hero_switch_remove_both_endpoints(self):
        for leave in (False, True):
            with self.subTest(leave=leave):
                run, owner, _, pair = self.deploy()
                if leave:
                    run.match.leave(owner)
                else:
                    self.assertTrue(run.match.switch_hero(owner, heroes()[SOLDIER]))
                self.assertFalse(run.match.combat.portals.pairs)
                self.assertTrue(all(bot not in run.match.bots for bot in pair.endpoints))
