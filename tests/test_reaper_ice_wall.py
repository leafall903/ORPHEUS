"""Actual melee and Death Blossom against Mei's placed Ice Wall."""

import unittest
from unittest.mock import patch

import test_game_reaper as reaper_tests
from test_game_combat import aim

from ow174.game import ana, combat, hook, mei, reaper, stats, zarya_barriers
from ow174.game.collision import World
from ow174.game.commands import Command
from ow174.game.content import SOLDIER, heroes
from ow174.game.script.nodes import Ability


class ReaperIceWallTests(unittest.TestCase):
    def scene(self, *, target_distance=3.5, wall_distance=1.5, names=("Reaper", "Mei"), place_wall=True):
        run, owner, caster, _ = reaper_tests.ReaperTests().scenario(names=names)
        self.assertTrue(run.match.switch_hero(caster, heroes()[mei.HERO]))
        x, y, z = owner.mover.position
        run.match.collision_world = World([
            (x - 20, y, z - 20, x - 20, y, z + 20, x + 20, y, z + 20),
            (x - 20, y, z - 20, x + 20, y, z + 20, x + 20, y, z - 20),
        ], name="Reaper Ice Wall flat arena")
        for player in run.players:
            player.mover.use(world=run.match.collision_world)
        caster.mover.position[:] = (x, y, z + wall_distance + 2.5)
        caster.mover.body = None
        for _ in range(25):
            run.step({})
        fight = run.match.combat
        angles = aim(fight._eye(caster), (x, y, z + wall_distance))
        piece = None
        if place_wall:
            for button in (16, 0, 1, 0):
                run.step({caster: (*angles, button)})
            self.assertEqual(len(fight.walls.pieces), 5)
            piece = min(fight.walls.pieces.values(), key=lambda p: abs(p.feet[0] - x))
        caster.mover.position[:] = (x, y, z + target_distance)
        caster.mover.body = None
        pool = combat.Vitals(caster.body, 500)
        fight.vitals[caster.body] = pool
        own = fight.vitals[owner.body]
        own.damage(100)
        return run, owner, caster, piece, angles, pool, own

    def test_actual_melee_spends_pillar_then_hits_body_after_cancel(self):
        run, owner, caster, piece, angles, pool, own = self.scene(target_distance=2.1)
        run.step({owner: (0, 0, 0x800)})
        for _ in range(30):
            run.step({})
        self.assertEqual(piece.health, 370)
        self.assertEqual(pool.total, 500)
        self.assertEqual(own.total, 150)
        run.step({caster: (*angles, 16)})
        self.assertFalse(run.match.combat.walls.pieces)
        for _ in range(40):
            run.step({})
        run.step({owner: (0, 0, 0x800)})
        for _ in range(30):
            run.step({})
        self.assertEqual(pool.total, 470)
        self.assertAlmostEqual(own.total, 160.5, places=4)

    def test_actual_blossom_breaking_pulse_never_hits_body_until_next_pulse(self):
        run, owner, _, piece, _, pool, own = self.scene()
        fight = run.match.combat
        owner.body_script.set_ult_charge(2100)
        run.step({owner: (0, 0, 32)})
        self.assertEqual(piece.health, 366)
        self.assertEqual(pool.total, 500)
        for _ in range(185):
            if piece.entity not in fight.walls.pieces:
                break
            run.step({})
        self.assertNotIn(piece.entity, fight.walls.pieces)
        self.assertEqual(pool.total, 500)
        self.assertEqual(own.total, 150)
        for _ in range(70):
            run.step({})
        self.assertEqual(pool.total, 398)
        self.assertAlmostEqual(own.total, 185.7, places=4)

    def test_actual_blossom_ice_wall_and_personal_bubble_order(self):
        for bubble_first in (True, False):
            with self.subTest(bubble_first=bubble_first):
                run, owner, caster, piece, angles, pool, own = self.scene(
                    target_distance=7, wall_distance=4, names=("Reaper", "Mei", "Zarya"),
                )
                zarya = run.players[2]
                self.assertTrue(run.match.switch_hero(zarya, heroes()[zarya_barriers.HERO]))
                x, y, z = owner.mover.position
                zarya.mover.position[:] = (x, y, z + (2.25 if bubble_first else 6))
                zarya.mover.body = None
                for _ in range(25):
                    run.step({})
                fight = run.match.combat
                run.step({zarya: (-32768, 0, 8)})
                bubble = fight.zarya_bubbles.for_target(zarya)
                self.assertIsNotNone(bubble)
                target = next(t for t in fight._targets() if t.owner is zarya)
                zarya_pool = fight.vitals_of(target)
                before = zarya_pool.total
                owner.body_script.set_ult_charge(2100)
                run.step({owner: (0, 0, 32)})
                cast_tick = fight.tick
                self.assertEqual(piece.health, 400 if bubble_first else 366)
                self.assertEqual(bubble.health, 166 if bubble_first else 200)
                self.assertEqual((zarya_pool.total, pool.total, own.total), (before, 500, 150))
                if bubble_first:
                    for _ in range(70):
                        if bubble.health == 0:
                            break
                        run.step({})
                    self.assertEqual(bubble.health, 0)
                    self.assertEqual(piece.health, 400)
                    self.assertEqual(zarya_pool.total, before)
                    break_tick = fight.tick
                    for _ in range(15):
                        run.step({})
                        if piece.health < 400:
                            break
                    self.assertEqual(piece.health, 366)
                    self.assertGreaterEqual(fight.tick - break_tick, 12)
                else:
                    run.step({caster: (*angles, 16)})
                    self.assertFalse(fight.walls.pieces)
                    self.assertEqual(bubble.health, 200)
                    for _ in range(15):
                        run.step({})
                        if bubble.health < 200:
                            break
                    self.assertEqual(bubble.health, 166)
                    self.assertEqual(zarya_pool.total, before)
                    self.assertGreaterEqual(fight.tick - cast_tick, 12)

    def projectile_scene(self, hero):
        run, owner, caster, piece, angles, _, own = self.scene(
            target_distance=8, names=("Reaper", "Mei", "Shooter"),
        )
        shooter = run.players[2]
        self.assertTrue(run.match.switch_hero(shooter, heroes()[hero]))
        x, y, z = owner.mover.position
        shooter.mover.position[:] = (x, y, z + 6)
        shooter.mover.body = None
        for _ in range(25):
            run.step({})
        return run, owner, caster, piece, angles, own, shooter

    @staticmethod
    def fire_projectile(run, shooter, target, button):
        fight = run.match.combat
        x, y, z = target.mover.position
        angles = aim(fight._eye(shooter), (x, y + 1, z))
        run.step({shooter: (*angles, button)})
        for _ in range(40):
            run.step({shooter: (*angles, 0)})

    def test_actual_helix_hits_pillar_without_splash_leaking_on_break(self):
        for breaks in (False, True):
            with self.subTest(breaks=breaks):
                run, owner, caster, piece, angles, own, shooter = self.projectile_scene(SOLDIER)
                fight = run.match.combat
                if breaks:
                    piece.health = 60  # fixture enters the same impact at a low pool value
                self.fire_projectile(run, shooter, owner, 2)
                self.assertEqual(own.total, 150)
                self.assertFalse(fight.rockets)
                self.assertEqual(piece.health, 0 if breaks else 280)
                self.assertEqual(piece.entity in fight.walls.pieces, not breaks)
                run.step({caster: (*angles, 16)})
                self.assertFalse(fight.walls.pieces)
                for _ in range(390):
                    run.step({})
                self.fire_projectile(run, shooter, owner, 2)
                self.assertEqual(own.total, 30)

    def test_actual_projectiles_resolve_ice_wall_expiry_across_world_gap(self):
        def advance(run, tick):
            run.frame = tick
            run.match.update(tick * combat.TICK, tick, frames=False)

        def launch(run, shooter, owner, button):
            fight = run.match.combat
            x, y, z = owner.mover.position
            heading = aim(fight._eye(shooter), (x, y + 1, z))
            run.step({shooter: (*heading, button)})
            for _ in range(30):
                if fight.rockets:
                    break
                run.step({shooter: (*heading, 0)})
            rocket, = fight.rockets
            self.assertIs(rocket.shooter, shooter)
            return rocket

        for hero, button, amount in ((SOLDIER, 2, 120), (ana.HERO, 8, 5)):
            run, owner, _, piece, _, own, shooter = self.projectile_scene(hero)
            fight = run.match.combat
            before = run.frame
            launch(run, shooter, owner, button)
            for _ in range(25):
                advance(run, run.frame + 1)
                if not fight.rockets:
                    break
            self.assertFalse(fight.rockets)
            self.assertEqual(piece.health, 400 - amount)
            self.assertEqual(own.total, 150)
            travel = run.frame - before

            for intercepted in (True, False):
                outcomes = []
                for skipped in (False, True):
                    with self.subTest(hero=hero, intercepted=intercepted, skipped=skipped):
                        run, owner, _, piece, _, own, shooter = self.projectile_scene(hero)
                        fight = run.match.combat
                        surface_tick = piece.expires - int(intercepted)
                        advance(run, surface_tick - travel)
                        rocket = launch(run, shooter, owner, button)
                        advance(run, run.frame + 1)
                        self.assertIn(rocket, fight.rockets)
                        self.assertEqual(rocket.last_tick, run.frame)
                        self.assertIn(piece.entity, fight.walls.pieces)
                        contacts = []

                        def observe(missile, *, world=fight, pillar=piece, hits=contacts,
                                    landed=fight._landed):
                            hits.append((world.tick, pillar.entity in world.walls.pieces))
                            landed(missile)

                        end = piece.expires + 35
                        with patch.object(fight, "_landed", side_effect=observe):
                            while run.frame < end:
                                advance(run, end if skipped else run.frame + 1)
                        self.assertFalse(fight.rockets)
                        self.assertFalse(fight.walls.pieces)
                        self.assertEqual(len(contacts), 1)
                        tick, wall_present = contacts[0]
                        if intercepted:
                            self.assertEqual(tick, surface_tick)
                        else:
                            self.assertGreater(tick, surface_tick)
                        self.assertEqual(wall_present, intercepted)
                        self.assertEqual(piece.health, 400 - amount if intercepted else 400)
                        self.assertEqual(own.total, 150 if intercepted else 150 - amount)
                        self.assertEqual(shooter.stats.value(stats.HERO_DAMAGE), 0 if intercepted else amount)
                        sleep = fight.ana_sleeps.get(owner.body)
                        if hero == ana.HERO and not intercepted:
                            self.assertIsNotNone(sleep)
                            self.assertEqual(sleep["start"], tick)
                            self.assertEqual(sleep["end"] - tick, 313)
                        else:
                            self.assertIsNone(sleep)
                        outcomes.append((contacts, sleep))
                if len(outcomes) == 2:
                    self.assertEqual(outcomes[0], outcomes[1])

    def test_actual_sleep_dart_hits_pillar_and_applies_sleep_only_after_cancel(self):
        run, owner, caster, piece, angles, own, shooter = self.projectile_scene(ana.HERO)
        fight = run.match.combat
        self.fire_projectile(run, shooter, owner, 8)
        self.assertEqual(piece.health, 395)
        self.assertEqual(own.total, 150)
        self.assertNotIn(owner.body, fight.ana_sleeps)
        run.step({caster: (*angles, 16)})
        self.assertFalse(fight.walls.pieces)
        instance = next(i for i in shooter.body_script.component.instances.values()
                        if i.graph.index == 0x0BDD)
        ability = next(state for state in instance.states.values()
                       if isinstance(state, Ability) and 35 in state.buttons())
        for _ in range(1600):
            if not ability.flags & Ability.RUNNING and ability.remaining() == 0:
                break
            run.step({})
        self.assertFalse(ability.flags & Ability.RUNNING)
        self.assertEqual(ability.remaining(), 0)
        self.fire_projectile(run, shooter, owner, 8)
        self.assertEqual(own.total, 145)
        self.assertIn(owner.body, fight.ana_sleeps)

    @staticmethod
    def walk(run, owner, *, buttons=0):
        run.frame += 1
        run.match.update(run.frame * combat.TICK, run.frame)
        for player in run.players:
            player.client.queue_entities(run.match.remote_movements(player))
            player.take_commands([Command(
                run.frame, forward=127 if player is owner else 0,
                buttons=buttons if player is owner else 0,
            )])

    def test_actual_walk_and_wraith_stop_at_ice_wall_then_cross_after_cancel(self):
        for wraith in (False, True):
            with self.subTest(wraith=wraith):
                run, owner, caster, piece, angles, _, _ = self.scene()
                start = owner.mover.position[2]
                self.walk(run, owner, buttons=8 if wraith else 0)
                for _ in range(40):
                    self.walk(run, owner)
                self.assertEqual(reaper.intangible(owner), wraith)
                self.assertGreater(owner.mover.position[2], start + 0.3)
                self.assertLess(owner.mover.position[2], piece.feet[2] - piece.depth)
                self.assertAlmostEqual(owner.mover.velocity[2], 0, places=4)
                run.step({caster: (*angles, 16)})
                self.assertFalse(run.match.combat.walls.pieces)
                for _ in range(40):
                    self.walk(run, owner)
                self.assertGreater(owner.mover.position[2], piece.feet[2] + piece.depth)

    def test_actual_shadow_rejects_ice_wall_face_then_accepts_clear_floor(self):
        run, owner, caster, piece, angles, _, _ = self.scene()
        fight = run.match.combat
        x, y, z = owner.mover.position
        target = (x, y, z + 5)
        facing = aim(fight._eye(owner), target)
        run.step({owner: (*facing, 16)})
        run.step({owner: (*facing, 0)})
        record = fight.reaper_shadows[owner.body]
        self.assertIsNone(record["target"])
        run.step({owner: (*facing, 1)})
        self.assertIsNone(record["cast"])
        run.step({caster: (*angles, 16), owner: (*facing, 0)})
        self.assertNotIn(piece.entity, fight.walls.pieces)
        run.step({owner: (*facing, 0)})
        self.assertIsNotNone(record["target"])
        run.step({owner: (*facing, 1)})
        for _ in range(110):
            run.step({})
        self.assertTrue(record["moved"])
        self.assertAlmostEqual(owner.mover.position[2], target[2], delta=0.1)

    def test_actual_ice_wall_lifts_supports_and_releases_reaper(self):
        run, owner, caster, piece, angles, _, _ = self.scene(wall_distance=0)
        top = piece.feet[1] + piece.height
        self.assertAlmostEqual(owner.mover.position[1], top, delta=0.03)
        for _ in range(25):
            run.step({})
        self.assertAlmostEqual(owner.mover.position[1], top, delta=0.05)
        run.step({caster: (*angles, 16)})
        for _ in range(85):
            run.step({})
        self.assertAlmostEqual(owner.mover.position[1], piece.feet[1], delta=0.06)

    def test_actual_shadow_revalidates_landing_when_wall_rises_during_cast(self):
        run, owner, caster, _, angles, _, _ = self.scene(
            target_distance=7.5, wall_distance=5, place_wall=False,
        )
        fight = run.match.combat
        x, y, z = owner.mover.position
        facing = aim(fight._eye(owner), (x, y, z + 5))
        for button in (16, 0, 1):
            run.step({owner: (*facing, button)})
        record = fight.reaper_shadows[owner.body]
        self.assertIsNotNone(record["cast"])
        for button in (16, 0, 1, 0):
            run.step({caster: (*angles, button)})
        self.assertEqual(len(fight.walls.pieces), 5)
        for _ in range(110):
            run.step({})
        self.assertTrue(record["moved"])  # Terminal cast consumed, though landing was blocked.
        self.assertAlmostEqual(owner.mover.position[2], z, delta=0.02)

    def test_actual_shadow_selects_supported_pillar_top(self):
        run, owner, _, piece, _, _, _ = self.scene(wall_distance=0)
        x, y, z = piece.feet
        destination = (x + piece.width, y + piece.height, z)
        fight = run.match.combat
        angles = aim(fight._eye(owner), destination)
        for button in (16, 0, 1):
            run.step({owner: (*angles, button)})
        self.assertIsNotNone(fight.reaper_shadows[owner.body]["cast"])
        for _ in range(110):
            run.step({})
        self.assertAlmostEqual(owner.mover.position[0], destination[0], delta=0.1)
        self.assertAlmostEqual(owner.mover.position[1], destination[1], delta=0.08)

    def shadow_wall_expiry_trace(self, travel=None, *, lands=True, skipped=False):
        run, owner, _, piece, _, _, own = self.scene(wall_distance=0)
        fight = run.match.combat
        origin = tuple(owner.mover.position)
        x, y, z = piece.feet
        destination = (x + piece.width, y + piece.height, z)
        angles = aim(fight._eye(owner), destination)
        if travel is not None:
            run.frame = piece.expires - travel - int(lands) - 3
            run.match.update(run.frame * combat.TICK, run.frame, frames=False)
        for button in (16, 0, 1):
            run.step({owner: (*angles, button)})
        record = fight.reaper_shadows[owner.body]
        self.assertIsNotNone(record["cast"])
        self.assertFalse(record["moved"])
        start = run.frame
        end = start + 165
        old_input = owner.commands.last_frame
        resolutions = []
        landing = fight._shadow_landing

        def observe(player, point):
            result = landing(player, point)
            resolutions.append((fight.tick, result))
            return result

        with patch.object(fight, "_shadow_landing", side_effect=observe):
            for tick in (end,) if skipped else range(start + 1, end + 1):
                run.match.update(tick * combat.TICK, tick, frames=False)
        self.assertEqual(owner.commands.last_frame, old_input)
        self.assertTrue(record["moved"])
        self.assertFalse(reaper.intangible(owner))
        self.assertEqual(own.total, 150)
        instance = next(i for i in owner.body_script.component.instances.values()
                        if i.graph.index == reaper.SHADOW_GRAPH)
        cooldown = instance.vars[61].value()
        self.assertGreater(cooldown, 0)
        if lands:
            self.assertAlmostEqual(owner.mover.position[0], destination[0], delta=0.1)
        else:
            self.assertAlmostEqual(owner.mover.position[0], origin[0], delta=0.02)
        if travel is None:
            self.assertEqual(len(resolutions), 1)
            self.assertTrue(resolutions[0][1])
            return resolutions[0][0] - start
        self.assertFalse(fight.walls.pieces)
        self.assertEqual(resolutions, [(piece.expires - int(lands), lands)])
        return tuple(owner.mover.position), cooldown, tuple(resolutions)

    def test_actual_shadow_pillar_top_resolves_on_ice_wall_expiry_tick(self):
        travel = self.shadow_wall_expiry_trace()
        for lands in (True, False):
            outcomes = []
            for skipped in (False, True):
                with self.subTest(lands=lands, skipped=skipped):
                    outcomes.append(self.shadow_wall_expiry_trace(travel, lands=lands, skipped=skipped))
            if len(outcomes) == 2:
                self.assertEqual(outcomes[0], outcomes[1])

    def test_actual_dragonblade_hits_reaper_only_after_ice_wall_cancel(self):
        run, owner, mei_caster, piece, angles, _, own = self.scene(names=("Reaper", "Mei", "Genji"))
        fight = run.match.combat
        genji = run.players[2]
        self.assertTrue(run.match.switch_hero(genji, heroes()[0x02E0000000000029]))
        x, y, z = owner.mover.position
        genji.mover.position[:] = (x, y, z + 3.5)
        mei_caster.mover.position[0] += 4
        genji.mover.body = mei_caster.mover.body = None
        for _ in range(30):
            run.step({})
        heading = aim(fight._eye(genji), (x, y + 1, z))
        genji.body_script.set_ult_charge(1932)
        run.step({genji: (*heading, 32)})
        for _ in range(84):
            run.step({genji: (*heading, 0)})
        run.step({genji: (*heading, 1)})
        for _ in range(20):
            run.step({genji: (*heading, 0)})
        self.assertIn(piece.entity, fight.walls.pieces)
        self.assertEqual(piece.health, 400)
        self.assertEqual(own.total, 150)
        run.step({mei_caster: (*angles, 16), genji: (*heading, 0)})
        self.assertFalse(fight.walls.pieces)
        for _ in range(38):
            run.step({genji: (*heading, 0)})
        run.step({genji: (*heading, 1)})
        for _ in range(24):
            run.step({genji: (*heading, 0)})
        self.assertEqual(own.total, 40)
        self.assertEqual(tuple(owner.mover.position), (x, y, z))

    def test_actual_swift_strike_cannot_hit_through_ice_wall(self):
        run, owner, mei_caster, piece, angles, _, own = self.scene(
            wall_distance=1.2, names=("Reaper", "Mei", "Genji")
        )
        fight = run.match.combat
        genji = run.players[2]
        self.assertTrue(run.match.switch_hero(genji, heroes()[0x02E0000000000029]))
        x, y, z = owner.mover.position
        z = piece.feet[2] - 0.8
        owner.mover.position[:] = (x, y, z)
        owner.mover.body = None
        start = (x, y, z + 4.5)
        genji.mover.position[:] = start
        genji.mover.body = None
        mei_caster.mover.position[0] += 4
        mei_caster.mover.body = None
        for _ in range(30):
            run.step({})
        run.step({genji: (-32768, 0, 8)})
        for _ in range(26):
            run.step({})
        self.assertLess(genji.mover.position[2], start[2] - 1)
        self.assertGreater(genji.mover.position[2], piece.feet[2])
        self.assertEqual(own.total, 150)
        self.assertEqual(piece.health, 400)
        run.step({mei_caster: (*angles, 16)})
        self.assertFalse(fight.walls.pieces)
        for _ in range(510):
            run.step({})
        genji.mover.position[:] = start
        genji.mover.body = None
        run.step({genji: (-32768, 0, 8)})
        for _ in range(26):
            run.step({})
        self.assertLess(genji.mover.position[2], z)
        self.assertEqual(own.total, 100, (start, tuple(owner.mover.position), tuple(genji.mover.position)))

    def test_actual_hook_stops_at_ice_wall_then_catches_reaper_after_cancel(self):
        run, owner, caster, piece, angles, own, hog = self.projectile_scene(hook.ROADHOG)
        fight = run.match.combat
        start = tuple(owner.mover.position)
        heading = aim(fight._eye(hog), (start[0], start[1] + 1, start[2]))
        run.step({hog: (*heading, 8)})
        launched = hog.body in fight.hooks.flights
        for _ in range(40):
            run.step({hog: (*heading, 0)})
            launched |= hog.body in fight.hooks.flights
            self.assertFalse(fight.hooks.controlled(owner.body))
        self.assertTrue(launched)
        self.assertFalse(fight.hooks.flights)
        self.assertEqual(piece.health, 400)
        self.assertEqual(own.total, 150)
        self.assertEqual(tuple(owner.mover.position), start)
        run.step({caster: (*angles, 16)})
        self.assertFalse(fight.walls.pieces)
        for _ in range(530):
            run.step({})
        self.fire_projectile(run, hog, owner, 8)
        self.assertEqual(own.total, 120)
        self.assertGreater(owner.mover.position[2], start[2] + 1)

    def test_actual_ice_wall_severs_established_hook_pull(self):
        run, owner, caster, _, angles, _, own = self.scene(
            target_distance=6.5, wall_distance=4, names=("Reaper", "Mei", "Roadhog"), place_wall=False,
        )
        hog = run.players[2]
        self.assertTrue(run.match.switch_hero(hog, heroes()[hook.ROADHOG]))
        x, y, z = owner.mover.position
        hog.mover.position[:] = (x, y, z + 10)
        hog.mover.body = None
        caster.mover.position[0] += 1.6  # Keep Mei out of the hook's line.
        caster.mover.body = None
        for _ in range(25):
            run.step({})
        fight = run.match.combat
        angles = aim(fight._eye(caster), (x, y, z + 4))
        run.step({caster: (*angles, 16)})
        run.step({caster: (*angles, 0)})
        heading = aim(fight._eye(hog), (x, y + 1, z))
        run.step({hog: (*heading, 8)})
        for _ in range(40):
            run.step({hog: (*heading, 0)})
            if fight.hooks.controlled(owner.body):
                break
        self.assertTrue(fight.hooks.controlled(owner.body))
        self.assertEqual(own.total, 120)
        run.step({caster: (*angles, 1)})
        self.assertTrue(fight.walls.pieces)
        run.step({})
        self.assertFalse(fight.hooks.controlled(owner.body))
        stopped = tuple(owner.mover.position)
        for _ in range(20):
            run.step({})
        self.assertLess(stopped[2], z + 3)
        self.assertAlmostEqual(owner.mover.position[2], stopped[2], delta=0.03)
        self.assertEqual(own.total, 120)
