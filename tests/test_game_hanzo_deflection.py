"""Actual bow and Genji Deflect commands through the ballistic sweep."""

import unittest
from dataclasses import replace
from unittest.mock import patch

import test_game_hanzo as hanzo_tests
import test_game_hanzo_dragons as dragon_tests
import test_game_hanzo_sonic as sonic_tests
from test_game_combat import aim, in_world

from ow174.game import arrow_deflection, bodytrace, combat, movelog, transcendence
from ow174.game.collision import World
from ow174.game.content import SOLDIER, heroes


class HanzoDeflectionTests(unittest.TestCase):
    def setUp(self):
        for module in (bodytrace, movelog):
            self.enterContext(patch.object(module, "ENABLED", False))

    def encounter(self, facing, *, expired=False, skipped=False, lateral=0.0, wall=False, attack="bow",
                  team_targets=False, deflect_in_flight=False, height=1, draw_frames=70):
        run, hanzo, genji = hanzo_tests.HanzoCombatTests().arena(distance=20 if deflect_in_flight and draw_frames == 70 else 5)
        position = tuple(genji.mover.position)
        self.assertTrue(run.match.switch_hero(genji, heroes()[arrow_deflection.GENJI]))
        genji.mover.position[:] = position
        genji.mover.body = None
        zen = None
        if team_targets:
            run.match.game_map = replace(run.match.game_map, free_for_all=False)
            hanzo.team, genji.team = 0, 1
            x, y, z = hanzo.mover.position
            lateral_target, behind = (1.5, 2) if attack == "sonic" else (0.5, 12)
            for number, team in ((3, hanzo.team), (4, genji.team)):
                player = run.match.add_player(number, f"Downrange {team}", SOLDIER, team, False)
                in_world(run.match, player)
                run.players.append(player)
                self.assertTrue(run.match.switch_hero(player, heroes()[SOLDIER]))
                player.team = team
                player.mover.position[:] = (x + (lateral_target if team else -lateral_target), y, z - behind)
                player.mover.body = None
            if attack == "dragon":
                zen = run.match.add_player(5, "Immune enemy", transcendence.HERO, hanzo.team, False)
                in_world(run.match, zen)
                run.players.append(zen)
                self.assertTrue(run.match.switch_hero(zen, heroes()[transcendence.HERO]))
                zen.team = hanzo.team
                zen.mover.position[:] = (x, y, z - 12)
                zen.mover.body = None
        fight = run.match.combat
        instance = next(i for i in genji.body_script.component.instances.values() if i.graph.index == 0x03A3)

        def step(arrow=0, defense=0, zen_button=0):
            x, y, z = genji.mover.position
            angles = aim(fight._eye(hanzo), (x + lateral, y + height, z))
            commands = {hanzo: (*angles, arrow), genji: (facing, 0, defense)}
            if zen is not None:
                commands[zen] = (0, 0, zen_button)
            run.step(commands)

        for _ in range(20):
            step()
        if expired:
            step(defense=16)
            self.assertTrue(instance.states[10].active)
            for _ in range(125):
                step()
        if attack == "sonic":
            fight.configure_sonic(sonic_tests.profile())
            step(8)
            step()
        if attack == "dragon":
            fight.configure_dragonstrike(dragon_tests.prepared_profile())
            hanzo.body_script.ult_seconds = 0
            hanzo.body_script.set_ult_charge(1680)
            step(32, 16)
        elif attack == "storm":
            step(16, 16)
            step()
        else:
            for _ in range(draw_frames - 1):
                step(1)
            step(1, 0 if expired or deflect_in_flight else 16)
            step()
        if attack in ("storm", "dragon"):
            for _ in range(100):
                step(1 if attack == "storm" else 0)
                if fight.rockets:
                    break
        state = instance.states.get(10)
        self.assertEqual(bool(state and state.active), not (expired or deflect_in_flight))
        arrow, = fight.rockets
        self.assertTrue(arrow.direct.deflectable)
        lease = arrow.shot
        if attack != "dragon":
            self.assertIsNotNone(lease)
        if deflect_in_flight:
            step()
            self.assertTrue(arrow in fight.rockets)
            self.assertIs(arrow.shooter, hanzo)
            step(defense=16)
            self.assertTrue(instance.states[10].active)
            self.assertTrue(arrow in fight.rockets)
            self.assertIs(arrow.shooter, hanzo)
        if wall:
            x, y, z = hanzo.mover.position
            w = z + 2
            run.match.collision_world = World([
                (x-5, y, w, x+5, y, w, x+5, y+5, w),
                (x-5, y, w, x+5, y+5, w, x-5, y+5, w),
                (x+5, y+5, w, x+5, y, w, x-5, y, w),
                (x-5, y+5, w, x+5, y+5, w, x-5, y, w),
            ])
        if skipped:
            step()
            run.frame += 7
            step()
        for _ in range(40 if attack == "dragon" or deflect_in_flight else 15):
            step()
        if zen is not None:
            zen.body_script.set_ult_charge(zen.body_script.component.entity_var(combat.ULT_COST).value())
            step(zen_button=32)
            self.assertTrue(transcendence.invulnerable(zen))
            with patch.object(fight, "_damage", wraps=fight._damage) as damage:
                for _ in range(170):
                    step()
            zen_attempts = [call for call in damage.call_args_list if call.args[1].owner is zen]
            self.assertTrue(zen_attempts)
            self.assertTrue(all(call.args[0] is genji and call.kwargs["source_entity"] == genji.body
                                for call in zen_attempts))
            self.assertTrue(transcendence.invulnerable(zen))
        for target in fight._targets():
            fight.vitals_of(target)
        return fight, hanzo, genji, arrow, lease

    def test_active_front_reflects_and_credits_genji_across_contiguous_or_skipped_ticks(self):
        for skipped in (False, True):
            with self.subTest(skipped=skipped):
                fight, hanzo, genji, arrow, lease = self.encounter(-32768, skipped=skipped)
                self.assertEqual(fight.vitals[genji.body].total, 200)
                self.assertEqual(fight.vitals[hanzo.body].total, 75)
                self.assertIs(arrow.shooter, genji)
                self.assertEqual(arrow.source_entity, genji.body)
                self.assertIsNone(arrow.shot)
                self.assertNotIn(lease, fight.projectiles.shots)
                self.assertNotIn(arrow, fight.rockets)
                self.assertAlmostEqual(arrow.gravity, 9.8, places=5)
                self.assertAlmostEqual(arrow.range_left + arrow.distance_flown, 1000)
                self.assertFalse(hanzo.client.messages(combat.HIT))
                self.assertEqual(len(genji.client.messages(combat.HIT)), 1)

    def test_reflected_bow_can_critically_eliminate_hanzo(self):
        fight, hanzo, genji, arrow, _ = self.encounter(-32768, height=1.48)
        self.assertEqual(fight.vitals[genji.body].total, 200)
        self.assertTrue(fight.vitals[hanzo.body].dead)
        self.assertEqual(fight.vitals[hanzo.body].killer, genji.body)
        self.assertEqual(arrow.direct.critical, 2)
        notice, = genji.client.messages(combat.HIT)
        self.assertEqual(notice["+0x78"],
                         {"+0x0": {"+0x0": hanzo.body}, "+0x4": 200,
                          "+0x8": combat.CRITICAL | combat.KILLING})
        self.assertFalse(hanzo.client.messages(combat.HIT))

    def test_deflect_activated_in_flight_redirects_actual_bow_and_sonic(self):
        for attack, draw_frames in (("bow", 70), ("sonic", 70), ("bow", 5), ("sonic", 5)):
            with self.subTest(attack=attack, draw_frames=draw_frames):
                fight, hanzo, genji, arrow, lease = self.encounter(
                    -32768, attack=attack, deflect_in_flight=True, draw_frames=draw_frames,
                )
                self.assertEqual(fight.vitals[genji.body].total, 200)
                amount = 25 + 100 * 1.375 * 0.079 if draw_frames == 5 else 125
                self.assertAlmostEqual(fight.vitals[hanzo.body].total, 200 - amount, delta=0.12)
                self.assertIs(arrow.shooter, genji)
                self.assertEqual(arrow.source_entity, genji.body)
                self.assertIsNone(arrow.shot)
                self.assertNotIn(lease, fight.projectiles.shots)
                if draw_frames == 5:
                    definition = 0x020D if attack == "sonic" else 0x020B
                    self.assertEqual(lease.definition, 0x0400000000000000 | definition)
                self.assertFalse(arrow in fight.rockets)
                self.assertFalse(hanzo.client.messages(combat.HIT))
                hits = genji.client.messages(combat.HIT)
                self.assertEqual([hit["+0x78"]["+0x4"] for hit in hits], [round(amount)])
                if attack == "sonic":
                    beacon, = fight.sonars
                    self.assertEqual(beacon.source_entity, genji.body)
                    self.assertEqual(beacon.attached, hanzo.body)
                    self.assertTrue(fight.revealed_to(genji, hanzo))
                else:
                    self.assertFalse(fight.sonars)

    def test_rear_and_expired_deflect_take_arrow_damage(self):
        for facing, expired in ((0, False), (-32768, True)):
            with self.subTest(facing=facing, expired=expired):
                fight, hanzo, genji, arrow, _ = self.encounter(facing, expired=expired)
                self.assertEqual(fight.vitals[genji.body].total, 75)
                self.assertEqual(fight.vitals[hanzo.body].total, 200)
                self.assertIs(arrow.shooter, hanzo)

    def test_authored_box_can_intercept_outside_body_capsule(self):
        fight, hanzo, genji, arrow, _ = self.encounter(-32768, lateral=0.65)
        self.assertIs(arrow.shooter, genji)
        self.assertEqual(fight.vitals[genji.body].total, 200)
        self.assertEqual(fight.vitals[hanzo.body].total, 200)

    def test_nearer_wall_ends_flight_before_deflect_box(self):
        fight, hanzo, genji, arrow, _ = self.encounter(-32768, wall=True)
        self.assertIs(arrow.shooter, hanzo)
        self.assertNotIn(arrow, fight.rockets)
        self.assertAlmostEqual(arrow.position[2], hanzo.mover.position[2] + 2)
        self.assertEqual(fight.vitals[hanzo.body].total, 200)
        self.assertEqual(fight.vitals[genji.body].total, 200)

    def test_sonic_attachment_follows_reflected_owner_and_victim(self):
        fight, hanzo, genji, arrow, _ = self.encounter(-32768, attack="sonic")
        self.assertEqual(fight.vitals[hanzo.body].total, 75)
        beacon, = fight.sonars
        self.assertIs(beacon.source, genji)
        self.assertEqual(beacon.source_entity, genji.body)
        self.assertEqual(beacon.attached, hanzo.body)
        self.assertIs(arrow.shooter, genji)

    def test_reflected_sonic_reveals_only_to_genjis_team(self):
        fight, hanzo, genji, arrow, _ = self.encounter(-32768, attack="sonic", team_targets=True)
        hanzo_ally, genji_ally = fight.match.players[2:]
        beacon, = fight.sonars
        self.assertEqual((arrow.source_entity, arrow.team), (genji.body, genji.team))
        self.assertEqual((beacon.source_entity, beacon.team, beacon.attached), (genji.body, genji.team, hanzo.body))
        self.assertEqual(set(beacon.statuses), {hanzo.body, hanzo_ally.body})
        for viewer in (genji, genji_ally):
            self.assertTrue(fight.revealed_to(viewer, hanzo))
            self.assertTrue(fight.revealed_to(viewer, hanzo_ally))
            self.assertFalse(fight.revealed_to(viewer, genji_ally))
        for viewer in (hanzo, hanzo_ally):
            self.assertFalse(fight.revealed_to(viewer, hanzo))
            self.assertFalse(fight.revealed_to(viewer, hanzo_ally))
        self.assertEqual(fight.vitals[hanzo.body].total, 75)
        self.assertEqual([fight.vitals[actor.body].total for actor in (genji, hanzo_ally, genji_ally)],
                         [200, 200, 200])

    def test_lethal_reflected_sonic_keeps_genji_ownership_across_hanzo_respawn(self):
        run, hanzo, genji = hanzo_tests.HanzoCombatTests().arena()
        position = tuple(genji.mover.position)
        self.assertTrue(run.match.switch_hero(genji, heroes()[arrow_deflection.GENJI]))
        genji.mover.position[:] = position
        genji.mover.body = None
        fight = run.match.combat
        fight.configure_sonic(sonic_tests.profile())
        old_body = hanzo.body
        origin = tuple(hanzo.mover.position)
        target = next(t for t in fight._targets() if t.owner is hanzo)
        old_pool = fight.vitals_of(target)
        old_pool.damage(75)  # Seed a lethal reflected full draw.

        def step(arrow=0, defense=0):
            x, y, z = genji.mover.position
            angles = aim(fight._eye(hanzo), (x, y + 1, z))
            run.step({hanzo: (*angles, arrow), genji: (-32768, 0, defense)})

        step(8)
        step()
        for _ in range(69):
            step(1)
        step(1, 16)
        step()

        with patch.object(combat, "respawn_seconds", return_value=2 * combat.TICK):
            for _ in range(20):
                step()
                if old_pool.dead:
                    break
            self.assertTrue(old_pool.dead)
            self.assertEqual(old_pool.killer, genji.body)
            self.assertEqual(hanzo.body, old_body)
            beacon, = fight.sonars
            self.assertEqual(beacon.attached, old_body)
            self.assertIs(beacon.source, genji)
            self.assertEqual(beacon.source_entity, genji.body)
            for _ in range(5):
                step()
                if hanzo.body != old_body:
                    break

        self.assertNotEqual(hanzo.body, old_body)
        fixed_point = beacon.position
        x, y, z = origin
        hanzo.mover.position[:] = (x + 20, y, z)
        hanzo.mover.body = None
        step()
        self.assertIsNone(beacon.attached)
        self.assertEqual(beacon.position, fixed_point)
        self.assertFalse(beacon.statuses)
        self.assertFalse(fight.revealed_to(genji, hanzo))
        hanzo.mover.position[:] = origin
        hanzo.mover.body = None
        step()
        self.assertEqual(set(beacon.statuses), {hanzo.body})
        self.assertTrue(fight.revealed_to(genji, hanzo))
        self.assertEqual(fight.vitals[hanzo.body].total, 200)
        self.assertFalse(hanzo.client.messages(combat.HIT))

    def test_reflected_bow_keeps_old_genji_source_after_genji_respawns(self):
        run, hanzo, genji = hanzo_tests.HanzoCombatTests().arena(distance=20)
        position = tuple(genji.mover.position)
        self.assertTrue(run.match.switch_hero(genji, heroes()[arrow_deflection.GENJI]))
        genji.mover.position[:] = position
        genji.mover.body = None
        flanker = run.match.add_player(3, "Flanker", SOLDIER, hanzo.team, False)
        in_world(run.match, flanker)
        run.players.append(flanker)
        self.assertTrue(run.match.switch_hero(flanker, heroes()[SOLDIER]))
        x, y, z = position
        flanker.mover.position[:] = (x, y, z + 2)
        flanker.mover.body = None
        fight = run.match.combat
        old_genji = genji.body
        targets = {t.owner: t for t in fight._targets()}
        genji_pool = fight.vitals_of(targets[genji])
        hanzo_pool = fight.vitals_of(targets[hanzo])
        genji_pool.damage(199)
        hanzo_pool.damage(75)
        for _ in range(20):
            run.step({})
        arrow_aim = aim(fight._eye(hanzo), (x, y + 1, z))
        for _ in range(69):
            run.step({hanzo: (*arrow_aim, 1)})
        run.step({hanzo: (*arrow_aim, 1), genji: (-32768, 0, 16)})
        run.step({hanzo: (*arrow_aim, 0), genji: (-32768, 0, 0)})
        arrow, = fight.rockets
        for _ in range(20):
            run.step({genji: (-32768, 0, 0)})
            if arrow.shooter is genji:
                break
        self.assertIs(arrow.shooter, genji)
        self.assertEqual(arrow.source_entity, old_genji)
        self.assertIsNone(arrow.shot)
        self.assertTrue(arrow in fight.rockets)

        melee_aim = aim(fight._eye(flanker), (x, y + 1, z))
        with patch.object(combat, "respawn_seconds", return_value=2 * combat.TICK):
            run.step({flanker: (*melee_aim, 0x800), genji: (-32768, 0, 0)})
            for _ in range(6):
                if genji.body != old_genji:
                    break
                run.step({})
        self.assertTrue(genji_pool.dead)
        self.assertEqual(genji_pool.killer, flanker.body)
        self.assertNotEqual(genji.body, old_genji)
        self.assertTrue(arrow in fight.rockets)
        self.assertEqual(hanzo_pool.total, 125)
        for _ in range(20):
            run.step({})
            if hanzo_pool.dead:
                break
        self.assertTrue(hanzo_pool.dead)
        self.assertEqual(hanzo_pool.killer, old_genji)
        self.assertFalse(hanzo.client.messages(combat.HIT))

    def test_storm_arrow_reflects_with_its_own_damage(self):
        fight, hanzo, genji, arrow, _ = self.encounter(-32768, attack="storm")
        self.assertEqual(arrow.direct.amount, 70)
        self.assertIs(arrow.shooter, genji)
        self.assertEqual(fight.vitals[hanzo.body].total, 130)
        self.assertEqual(fight.vitals[genji.body].total, 200)

    def test_initial_dragon_transforms_with_reflected_owner(self):
        fight, hanzo, genji, arrow, _ = self.encounter(-32768, attack="dragon", skipped=True)
        self.assertIs(arrow.shooter, genji)
        self.assertEqual(fight.vitals[genji.body].total, 200)
        self.assertLessEqual(fight.vitals[hanzo.body].total, 75)
        flight, = fight.dragons
        self.assertIs(flight.shooter, genji)
        self.assertEqual(flight.source_entity, genji.body)


    def test_reflected_dragon_uses_genji_team_and_kill_source_downrange(self):
        fight, hanzo, genji, arrow, _ = self.encounter(-32768, attack="dragon", team_targets=True)
        enemy, ally, zen = fight.match.players[2:]
        flight, = fight.dragons
        self.assertEqual(flight.team, genji.team)
        self.assertEqual(flight.source_entity, genji.body)
        self.assertIs(flight.shooter, genji)
        self.assertNotIn(enemy.body, arrow.hit_entities)
        self.assertNotIn(ally.body, arrow.hit_entities)
        self.assertNotIn(zen.body, arrow.hit_entities)
        self.assertEqual(fight.vitals[zen.body].total, 200)
        self.assertEqual(fight.vitals[ally.body].total, 200)
        self.assertTrue(fight.vitals[enemy.body].dead)
        self.assertEqual(fight.vitals[enemy.body].killer, genji.body)
        self.assertFalse(hanzo.client.messages(combat.HIT))
