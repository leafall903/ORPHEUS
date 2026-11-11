"""Actual 1259 Whip Shot input against Pharah on build 104319."""

import unittest
from dataclasses import replace
from unittest.mock import patch

import test_pharah_brigitte as brigitte
from test_game_combat import aim

from ow174.game.collision import World
from ow174.game.commands import CROUCH, FIRE
from ow174.game.content import heroes
from ow174.game.zarya_barrier import PROJECTED


class PharahWhipTests(unittest.TestCase):
    arena = brigitte.PharahBrigitteTests.arena
    health = brigitte.PharahBrigitteTests.health

    @staticmethod
    def aim(run, pharah, caster):
        fight = run.match.combat
        return aim(fight._eye(caster), fight._eye(pharah))

    @staticmethod
    def flights(run):
        return [shot for shot in run.match.combat.rockets if shot.whip_source is not None]

    def fire(self, run, pharah, caster, *, frames=45):
        yaw, pitch = self.aim(run, pharah, caster)
        for index in range(frames):
            run.step({caster: (yaw, pitch, 8 if index == 0 else 0)})

    def test_actual_outbound_sweep_deals_70_and_pushes_without_stun(self):
        run, pharah, caster = self.arena(distance=15)
        self.fire(run, pharah, caster, frames=13)
        flight, = self.flights(run)
        self.assertEqual((flight.speed, flight.left, flight.contact_extent), (80, 0.25, 0.5))
        self.assertEqual(self.health(run, pharah).total, 200)
        for _ in range(10):
            run.step({})
        self.assertEqual(self.health(run, pharah).total, 200)
        run.step({})
        self.assertEqual(self.health(run, pharah).total, 130)
        self.assertEqual(caster.body_script.component.entity_var(342).value(), 70)
        self.assertEqual(pharah.mover.velocity[1:], (3, -25))
        self.assertFalse(run.match.combat.is_stunned(pharah))
        self.assertFalse(self.flights(run))

    def test_whip_displaces_pharah_during_barrage_without_ending_the_volley(self):
        run, pharah, caster = self.arena(distance=15)
        script = pharah.body_script
        script.set_ult_charge(script.component.entity_var(338).value())
        run.step({pharah: (-32768, 0, 32)})
        yaw, pitch = self.aim(run, pharah, caster)
        for index in range(35):
            run.step({caster: (yaw, pitch, 8 if index == 0 else 0), pharah: (-32768, 0, 0)})
            if self.health(run, pharah).total < 200:
                break
        self.assertEqual(self.health(run, pharah).total, 130)
        self.assertLess(pharah.mover.position[2], -0.25)
        self.assertTrue(script.ultimate_running())
        volley = next(i for i in script.component.instances.values() if i.graph.index == 0x2B).states[1]
        reported = volley.reported
        for _ in range(10):
            run.step({pharah: (-32768, 0, 0)})
        self.assertGreater(volley.reported, reported)

    def test_range_team_and_static_cover(self):
        for scenario in ("range", "ally", "wall"):
            with self.subTest(scenario=scenario):
                run, pharah, caster = self.arena(distance=22 if scenario == "range" else 15)
                fight = run.match.combat
                if scenario == "ally":
                    run.match.game_map = replace(run.match.game_map, free_for_all=False)
                    pharah.team = caster.team = 0
                if scenario == "wall":
                    y = pharah.mover.position[1]
                    z = 7
                    wall = World([(-4, y, z, -4, y + 5, z, 4, y + 5, z),
                                  (-4, y, z, 4, y + 5, z, 4, y, z)])
                    with patch.object(fight, "_collision", return_value=wall):
                        self.fire(run, pharah, caster)
                else:
                    self.fire(run, pharah, caster)
                self.assertEqual(self.health(run, pharah).total, 200)
                self.assertEqual(caster.body_script.component.entity_var(342).value(), 0)
                self.assertFalse(self.flights(run))

    def test_winston_deployment_during_flight_intercepts_whip(self):
        run, pharah, caster = self.arena(distance=15)
        winston = run.players[2]
        self.assertTrue(run.match.switch_hero(winston, heroes()[0x02E0000000000009]))
        winston.mover.position = [1, pharah.mover.position[1], 0]
        winston.mover.body = None
        run.match.game_map = replace(run.match.game_map, free_for_all=False)
        pharah.team = winston.team = 0
        caster.team = 1
        self.fire(run, pharah, caster, frames=13)
        self.assertTrue(self.flights(run))
        run.step({winston: (0, 0, 16)})
        for _ in range(30):
            run.step({})
        bubble = run.match.combat.winston_bubbles[winston.body]
        self.assertEqual(bubble.health, 630)
        self.assertEqual(self.health(run, pharah).total, 200)
        self.assertEqual(caster.body_script.component.entity_var(342).value(), 0)
        self.assertFalse(self.flights(run))

    def test_pharah_rocket_kills_caster_during_windup_or_outbound(self):
        for distance, launched in ((5, False), (10, True)):
            with self.subTest(launched=launched):
                run, pharah, caster = self.arena(distance=distance)
                health = self.health(run, caster)
                health.damage(health.total - 1)
                fight = run.match.combat
                yaw, pitch = self.aim(run, pharah, caster)
                fire_yaw, fire_pitch = aim(fight._eye(pharah), fight._eye(caster))
                observed_flight = False
                for index in range(45):
                    run.step({caster: (yaw, pitch, 8 if index == 0 else 0),
                              pharah: (fire_yaw, fire_pitch, FIRE if index == 0 else 0)})
                    observed_flight |= bool(self.flights(run))
                self.assertEqual(observed_flight, launched)
                self.assertTrue(health.dead)
                self.assertEqual(self.health(run, pharah).total, 200)
                self.assertFalse(self.flights(run))

    def test_authored_range_clips_the_last_swept_chord(self):
        for distance, health in ((20.95, 130), (21.05, 200)):
            with self.subTest(distance=distance):
                run, pharah, caster = self.arena(distance)
                self.fire(run, pharah, caster)
                self.assertEqual(self.health(run, pharah).total, health)
                self.assertFalse(self.flights(run))

    def test_cooldown_rejects_second_press_and_allows_recast(self):
        run, pharah, caster = self.arena(15)
        self.fire(run, pharah, caster)
        self.assertEqual(self.health(run, pharah).total, 130)
        instance = next(i for i in caster.body_script.component.instances.values() if i.graph.index == 0x1259)
        cooldown = instance.vars[61]
        self.assertGreater(cooldown.value(), 3.7)
        self.fire(run, pharah, caster, frames=35)
        self.assertEqual(self.health(run, pharah).total, 130)
        for _ in range(255):
            if cooldown.value() <= 0:
                break
            run.step({})
        self.assertEqual(cooldown.value(), 0)
        pharah.mover.position = [0, caster.mover.position[1], 0]
        pharah.mover.velocity = (0, 0, 0)
        pharah.mover.body = None
        run.step({})
        self.fire(run, pharah, caster)
        self.assertEqual(self.health(run, pharah).total, 60)

    def test_genji_blocks_whip_before_it_reaches_pharah(self):
        run, pharah, caster = self.arena(15)
        genji = run.players[2]
        self.assertTrue(run.match.switch_hero(genji, heroes()[0x02E0000000000029]))
        genji.mover.position = [0, pharah.mover.position[1], 4]
        genji.mover.body = None
        self.fire(run, pharah, caster, frames=13)
        run.step({genji: (0, 0, 16)})
        for _ in range(25):
            run.step({})
        self.assertEqual(self.health(run, pharah).total, 200)
        self.assertEqual(self.health(run, genji).total, 200)
        self.assertEqual(self.health(run, caster).total, 200)
        self.assertEqual(caster.body_script.component.entity_var(342).value(), 0)
        self.assertFalse(self.flights(run))

    def test_projected_barrier_cast_during_flight_blocks_damage_and_impulse(self):
        run, pharah, caster = self.arena(15)
        zarya = run.players[2]
        self.assertTrue(run.match.switch_hero(zarya, heroes()[0x02E0000000000068]))
        zarya.mover.position = [4.5, pharah.mover.position[1], 0]
        zarya.mover.body = None
        run.match.game_map = replace(run.match.game_map, free_for_all=False)
        pharah.team = zarya.team = 0
        caster.team = 1
        self.fire(run, pharah, caster, frames=13)
        yaw, pitch = self.aim(run, pharah, zarya)
        run.step({zarya: (yaw, pitch, 16)})
        for _ in range(25):
            run.step({})
        bubble = run.match.combat.zarya_bubbles[zarya.body, PROJECTED]
        self.assertIs(bubble.target, pharah)
        self.assertEqual(bubble.health, 130)
        self.assertEqual(self.health(run, pharah).total, 200)
        self.assertEqual(pharah.mover.position[2], 0)
        self.assertEqual(caster.body_script.component.entity_var(342).value(), 0)

    def test_authored_head_radius_brackets_a_grazing_pharah(self):
        for lateral, health in ((0.98, 130), (1.02, 200)):
            with self.subTest(lateral=lateral):
                run, pharah, caster = self.arena(15)
                _, pitch = self.aim(run, pharah, caster)
                pharah.mover.position[0] = lateral
                pharah.mover.body = None
                for index in range(35):
                    run.step({caster: (-32768, pitch, 8 if index == 0 else 0)})
                self.assertEqual(self.health(run, pharah).total, health)

    def test_intangible_body_passes_whip_and_cryo_body_stops_it(self):
        for name, hero, damage in (("Wraith", 0x02E0000000000002, 70),
                                   ("Cryo", 0x02E00000000000DD, 0)):
            with self.subTest(name=name):
                run, pharah, caster = self.arena(15)
                cover = run.players[2]
                self.assertTrue(run.match.switch_hero(cover, heroes()[hero]))
                cover.mover.position = [0, pharah.mover.position[1], 5]
                cover.mover.body = None
                for _ in range(12):
                    run.step({})
                baseline = self.health(run, cover).total
                self.fire(run, pharah, caster, frames=13)
                run.step({cover: (0, 0, 8)})
                for _ in range(30):
                    run.step({})
                self.assertEqual(self.health(run, cover).total, baseline)
                self.assertEqual(self.health(run, pharah).total, 200 - damage)
                self.assertEqual(caster.body_script.component.entity_var(342).value(), damage)

    def test_passive_snapshot_stores_outbound_source_state(self):
        import test_server_snapshot as stored

        case = stored.ServerSnapshotTests()
        session, peers, players = case.scene()
        caster, = players
        self.assertTrue(session.apply("switch_hero", {
            "account": caster.account_lo, "hero": 0x02E0000000000195,
        }))
        peers[0].commands(caster.body_script.frame + 1, 13, 8)
        flight, = (shot for shot in caster.match.combat.rockets if shot.whip_source is not None)
        before = stored.snapshot_digest(case.capture(session))
        with patch.object(flight, "whip_source", None):
            self.assertNotEqual(stored.snapshot_digest(case.capture(session)), before)

    def test_authored_stance_eye_brackets_low_cover(self):
        for crouched, height, cover_height, blocked in (
            (False, 1.58, 1.565, False), (False, 1.58, 1.59, True),
            (True, 1.20, 1.175, False), (True, 1.20, 1.21, True),
        ):
            with self.subTest(crouched=crouched, blocked=blocked):
                run, pharah, caster = self.arena(15)
                fight = run.match.combat
                buttons = CROUCH if crouched else 0
                for _ in range(20):
                    run.step({caster: (-32768, 0, buttons)})
                self.assertEqual(caster.mover.crouched, crouched)
                y = caster.mover.position[1]
                self.assertAlmostEqual(fight._eye(caster)[1] - y, height)
                z, top = 7, y + cover_height
                wall = World([(-4, y, z, -4, top, z, 4, top, z),
                              (-4, y, z, 4, top, z, 4, y, z)])
                with patch.object(fight, "_collision", return_value=wall):
                    run.step({caster: (-32768, 0, 8 | buttons)})
                    for _ in range(44):
                        run.step({caster: (-32768, 0, buttons)})
                self.assertEqual(self.health(run, pharah).total, 200 if blocked else 130)
