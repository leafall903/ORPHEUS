"""Executed Mei/Genji authored-input cross-hero reflection evidence."""

import unittest
from dataclasses import replace

import test_mei_blizzard as blizzard
import test_mei_combat as fixtures
import test_mei_wall as walls

from ow174.game import combat, genji_deflect, mei
from ow174.game.content import heroes


class DeflectTests(unittest.TestCase):
    def scene(self, facing=True, distance=None):
        run, mei, genji, _ = fixtures.MeiCombatTests().duel()
        run.match.switch_hero(genji, heroes()[genji_deflect.HERO])
        if distance is not None:
            x, y, z = mei.mover.position
            run.match.combat.walls.place_player(genji, (x, y, z + distance))
        yaw, pitch = fixtures.aim(run.match.combat._eye(genji), run.match.combat._eye(mei))
        if not facing:
            yaw = (yaw + 32768) % 65536
        run.step({genji: (yaw, pitch, 16)})
        run.step({genji: (yaw, pitch, 0)})
        self.assertIsNotNone(genji_deflect.active(run.match.combat, genji))
        target = next(t for t in run.match.combat._targets() if t.owner is genji)
        return run, mei, genji, target

    def fire(self, run, mei, target, button, height):
        genji = target.owner
        facing = (genji.mover.yaw, genji.mover.pitch, 0)
        point = (target.feet[0], target.feet[1] + height, target.feet[2])
        angle = fixtures.aim(run.match.combat._eye(mei), point)
        for frame in range(45):
            run.step({mei: (*angle, button if frame < 20 else 0), genji: facing})

    def health(self, run, actor):
        target = next(t for t in run.match.combat._targets() if t.owner is actor)
        return run.match.combat.vitals_of(target).total

    def test_actual_icicle_returns_to_mei_with_genji_damage_credit(self):
        run, mei, genji, target = self.scene()
        self.fire(run, mei, target, 2, 0.9)
        self.assertEqual(self.health(run, genji), 200.)
        self.assertLess(self.health(run, mei), 250.)
        self.assertEqual(len(mei.client.messages(combat.HIT)), 0)
        self.assertEqual(len(genji.client.messages(combat.HIT)), 1)

    def test_cryo_started_after_actual_icicle_reflection_blocks_return_hit(self):
        for protected in (False, True):
            with self.subTest(protected=protected):
                run, owner, genji, target = self.scene(distance=18.)
                fight = run.match.combat
                facing = (genji.mover.yaw, genji.mover.pitch, 0)
                point = (target.feet[0], target.feet[1] + .9, target.feet[2])
                angle = fixtures.aim(fight._eye(owner), point)
                for frame in range(85):
                    run.step({owner: (*angle, 2 if frame < 20 else 0), genji: facing})
                    if any(rocket.shooter is genji for rocket in fight.rockets):
                        break
                self.assertEqual(len(fight.rockets), 1)
                self.assertIs(fight.rockets[0].shooter, genji)
                run.step({owner: (*angle, 8 if protected else 0), genji: facing})
                self.assertEqual(mei.protected(owner), protected)
                self.assertEqual(len(fight.rockets), 1)
                for _ in range(40):
                    run.step({genji: facing})
                self.assertFalse(fight.rockets)
                self.assertEqual(self.health(run, genji), 200.)
                self.assertEqual(self.health(run, owner), 250. if protected else 175.)
                self.assertEqual(len(genji.client.messages(combat.HIT)), 0 if protected else 1)

    def test_quick_melee_is_blocked_from_the_front_and_hits_from_behind(self):
        for facing in (True, False):
            with self.subTest(facing=facing):
                run, owner, genji, target = self.scene(facing=facing, distance=1.5)
                self.fire(run, owner, target, 0x800, .9)
                self.assertEqual(self.health(run, genji), 200. if facing else 170.)
                self.assertEqual(self.health(run, owner), 250.)
                self.assertEqual(len(owner.client.messages(combat.HIT)), 0 if facing else 1)
                self.assertFalse(genji.client.messages(combat.HIT))

    def test_spray_excluded_tag_reaches_health_and_frost(self):
        run, mei, genji, target = self.scene()
        self.fire(run, mei, target, 1, 0.9)
        self.assertLess(self.health(run, genji), 200.)
        self.assertIsNotNone(run.match.combat.frost(genji))
        self.assertEqual(self.health(run, mei), 250.)

    def test_rear_icicle_is_not_reflected(self):
        run, mei, genji, target = self.scene(False)
        self.fire(run, mei, target, 2, 0.9)
        self.assertEqual(self.health(run, genji), 125.)
        self.assertEqual(self.health(run, mei), 250.)

    def test_native_duration_and_cooldown_prevent_immediate_restart(self):
        run, mei, genji, target = self.scene()
        yaw, pitch = genji.mover.yaw, genji.mover.pitch
        for _ in range(128):
            run.step({})
        self.assertIsNone(genji_deflect.active(run.match.combat, genji))
        run.step({genji: (yaw, pitch, 16)})
        run.step({genji: (yaw, pitch, 0)})
        self.assertIsNone(genji_deflect.active(run.match.combat, genji))
        self.fire(run, mei, target, 2, 0.9)
        self.assertEqual(self.health(run, genji), 125.)

    def test_allied_icicle_passes_deflector_without_friendly_damage(self):
        run, mei, genji, target = self.scene()
        run.match.game_map = replace(run.match.game_map, free_for_all=False)
        genji.team = mei.team
        self.fire(run, mei, target, 2, 0.9)
        self.assertEqual(self.health(run, genji), 200.)
        self.assertEqual(self.health(run, mei), 250.)

    def test_static_wall_before_deflector_stops_icicle(self):
        run, mei, genji, target = self.scene()
        fixtures.static_wall(run, mei, 2.)
        self.fire(run, mei, target, 2, 0.9)
        self.assertEqual(self.health(run, genji), 200.)
        self.assertEqual(self.health(run, mei), 250.)
        self.assertEqual(len(genji.client.messages(combat.HIT)), 0)

    def test_native_cooldown_eventually_allows_actual_e_again(self):
        run, _mei, genji, _target = self.scene()
        facing = genji.mover.yaw, genji.mover.pitch
        for _ in range(650):
            run.step({genji: (*facing, 0)})
        run.step({genji: (*facing, 16)})
        self.assertIsNotNone(genji_deflect.active(run.match.combat, genji))

    def launch_blizzard(self, run, mei, genji):
        mei.body_script.set_ult_charge(1610.)
        facing = genji.mover.yaw, genji.mover.pitch, 0
        point = run.match.combat._eye(genji)
        angle = fixtures.aim(run.match.combat._eye(mei), point)
        for frame in range(32):
            run.step({mei: (*angle, 32 if frame == 0 else 0), genji: facing})
        manager = run.match.combat.blizzard
        self.assertEqual(len(manager.flights), 1)
        return manager.flights[0], facing

    def test_airborne_blizzard_transfers_area_damage_and_frost_to_genji(self):
        run, mei, genji, _target = self.scene(distance=5.)
        run.match.game_map = replace(run.match.game_map, free_for_all=False)
        mei.team, genji.team = 0, 1
        flight, facing = self.launch_blizzard(run, mei, genji)
        for _ in range(100):
            if run.match.combat.blizzard.areas:
                break
            run.step({genji: facing})
        area = next(iter(run.match.combat.blizzard.areas.values()))
        self.assertIs(flight.owner, genji)
        self.assertIs(area.owner, genji)
        self.assertEqual(area.body, genji.body)
        while run.frame * 16 < area.starts + 2800:
            run.step({genji: facing})
        self.assertTrue(run.match.combat.frozen(mei))
        self.assertIsNone(run.match.combat.frost(genji))
        blizzard.BlizzardTests().finish(run, area)
        self.assertAlmostEqual(self.health(run, mei), 165.)
        self.assertEqual(self.health(run, genji), 200.)
        self.assertTrue(genji.client.messages(combat.HIT))
        self.assertFalse(mei.client.messages(combat.HIT))

    def test_airborne_blizzard_from_rear_retains_mei_ownership(self):
        run, mei, genji, _target = self.scene(False, distance=5.)
        flight, facing = self.launch_blizzard(run, mei, genji)
        for _ in range(100):
            if run.match.combat.blizzard.areas:
                break
            run.step({genji: facing})
        area = next(iter(run.match.combat.blizzard.areas.values()))
        self.assertIs(flight.owner, mei)
        self.assertIs(area.owner, mei)

    def test_allied_blizzard_ignores_active_deflector(self):
        run, mei, genji, _target = self.scene(distance=5.)
        run.match.game_map = replace(run.match.game_map, free_for_all=False)
        genji.team = mei.team
        flight, facing = self.launch_blizzard(run, mei, genji)
        for _ in range(100):
            if run.match.combat.blizzard.areas:
                break
            run.step({genji: facing})
        area = next(iter(run.match.combat.blizzard.areas.values()))
        self.assertIs(flight.owner, mei)
        self.assertIs(area.owner, mei)
        blizzard.BlizzardTests().finish(run, area)
        self.assertEqual(self.health(run, mei), 250.)
        self.assertEqual(self.health(run, genji), 200.)

    def test_ice_wall_before_deflector_anchors_original_blizzard(self):
        run, mei, genji, _target = self.scene(distance=5.)
        facing = genji.mover.yaw, genji.mover.pitch, 0
        pieces, _ = walls.WallTests().place(run, mei, distance=3.)
        self.assertEqual(len(pieces), 5)
        run.step({genji: facing})
        flight, facing = self.launch_blizzard(run, mei, genji)
        for _ in range(100):
            if run.match.combat.blizzard.areas:
                break
            run.step({genji: facing})
        area = next(iter(run.match.combat.blizzard.areas.values()))
        self.assertIs(flight.owner, mei)
        self.assertIs(area.owner, mei)
        self.assertLess(area.position[2], pieces[2].feet[2])
        self.assertEqual(self.health(run, genji), 200.)

    def test_reflected_area_survives_reflector_death_and_ends_normally(self):
        run, mei, genji, target = self.scene(distance=5.)
        flight, facing = self.launch_blizzard(run, mei, genji)
        while flight.owner is mei and flight in run.match.combat.blizzard.flights:
            run.step({genji: facing})
        self.assertIs(flight.owner, genji)
        run.match.combat._damage(mei, target, 1000., False, None, run.frame)
        self.assertTrue(run.match.combat.is_dead(genji))
        area = blizzard.BlizzardTests().landed(run)
        self.assertIs(area.owner, genji)
        blizzard.BlizzardTests().finish(run, area)
        self.assertAlmostEqual(self.health(run, mei), 165.)
        self.assertNotIn(area.entity, run.match.combat.blizzard.areas)

    def test_original_body_switch_preserves_reflected_flight_but_reflector_switch_retires(self):
        for original in (True, False):
            with self.subTest(original=original):
                run, mei, genji, _target = self.scene(distance=5.)
                flight, facing = self.launch_blizzard(run, mei, genji)
                while flight.owner is mei and flight in run.match.combat.blizzard.flights:
                    run.step({genji: facing})
                self.assertIs(flight.owner, genji)
                actor = mei if original else genji
                run.match.switch_hero(actor, actor.hero)
                self.assertEqual(flight in run.match.combat.blizzard.flights, original)

    def test_actual_spray_finishes_freeze_and_deflect_does_not_resume_after_thaw(self):
        run, mei_actor, genji, target = self.scene()
        fight = run.match.combat
        # Explicit pre-existing slow isolates a freeze early in the2s Deflect
        # window. The final freeze-producing contacts use actual spray input.
        for _ in range(28):
            fight._damage(mei_actor, target, 2.75, False, mei.SPRAY_LOADOUT, run.frame)
        self.assertFalse(fight.frozen(genji))
        self.fire(run, mei_actor, target, 1, .9)
        self.assertTrue(fight.frozen(genji))
        sample = fight.frost(genji)
        facing = genji.mover.yaw, genji.mover.pitch, 0
        instance = next(i for i in genji.body_script.component.instances.values() if i.graph.index == 0x3A3)
        self.assertFalse(instance.state(10).active)
        while run.frame * 16 <= sample[2]:
            run.step({genji: facing})
        self.assertIsNone(genji_deflect.active(fight, genji))
        before = self.health(run, genji)
        self.fire(run, mei_actor, target, 2, .9)
        self.assertEqual(self.health(run, genji), before - 75.)

    def test_frost_interruption_keeps_native_cooldown_and_later_e_can_restart(self):
        run, mei_actor, genji, target = self.scene()
        fight = run.match.combat
        for _ in range(30):
            fight._damage(mei_actor, target, 2.75, False, mei.SPRAY_LOADOUT, run.frame)
        self.assertTrue(fight.frozen(genji))
        instance = next(i for i in genji.body_script.component.instances.values() if i.graph.index == 0x3A3)
        facing = genji.mover.yaw, genji.mover.pitch
        for _ in range(83):
            run.step({genji: (*facing, 0)})
        self.assertFalse(fight.frozen(genji))
        self.assertFalse(instance.state(10).active)
        self.assertGreater(instance.vars[61].value(), 6.)
        self.assertLess(instance.vars[61].value(), 8.)
        run.step({genji: (*facing, 16)})
        run.step({genji: (*facing, 0)})
        self.assertFalse(instance.state(10).active)
        for _ in range(500):
            run.step({genji: (*facing, 0)})
        run.step({genji: (*facing, 16)})
        self.assertTrue(instance.state(10).active)


if __name__ == '__main__':
    unittest.main()
