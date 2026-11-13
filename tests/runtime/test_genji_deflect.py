"""Reconstructed Deflect authority through real player commands and volleys."""

import json
import unittest
from dataclasses import replace
from pathlib import Path

from test_game_combat import aim
from test_genji_combat import GENJI, GenjiFixture
from test_game_mover import FLOOR, wall

from ow174.game import combat
from ow174.game.collision import World
from ow174.game.content import heroes
from ow174.game.script.expr import INSTANCE


class GenjiDeflectTests(GenjiFixture):
    def test_projectile_admission_contract(self):
        path = Path(__file__).resolve().parents[2] / "fixtures/behavior/genji-deflect-admission-v1.json"
        contract = json.loads(path.read_text(encoding="utf-8"))
        inputs = contract["input"]
        for case in contract["cases"]:
            with self.subTest(case=case["name"]):
                run, genji, source = self.start()
                direct = (combat.HitDef(case["direct"], None, deflectable=case["deflectable"],
                                       deflect_override=case["override"]) if case["direct"] else None)
                blast = (combat.HitDef(case["blast"], None, rings=((3, 1),))
                         if case["blast"] else None)
                fight = run.match.combat
                fight.rockets.append(combat.Rocket(
                    source, source.team, tuple(inputs["projectile_position"]),
                    tuple(inputs["projectile_direction"]), inputs["projectile_speed"],
                    inputs["projectile_lifetime"], direct, blast,
                ))
                for _ in range(inputs["flight_frames"]):
                    run.step({})
                expected = case["expected"]
                self.assertEqual(self.health(run, genji), expected["genji_health"])
                self.assertEqual(self.health(run, source), expected["attacker_health"])
                attacker, victim = ((genji, source) if expected["damage_owner"] == "genji"
                                    else (source, genji))
                hits = [notice["+0x78"] for notice in attacker.client.messages(combat.HIT)]
                self.assertEqual([(hit["+0x0"]["+0x0"], hit["+0x4"]) for hit in hits],
                                 [(victim.body, case["direct"] or case["blast"])])
                self.assertFalse(victim.client.messages(combat.HIT))
                self.assertFalse(fight.rockets)

    def start(self, distance=4, facing=0, projectile=False, lateral=0):
        run, genji = self.spawn(distance)
        source = run.players[1]
        if projectile:
            run.match.switch_hero(source, heroes()[GENJI])
            source.mover.position[:] = [0, 1, distance]
        source.mover.position[0] = lateral
        for frame in range(5):
            run.step({genji: (facing, 0, 16 if frame == 0 else 0)})
        return run, genji, source

    def incoming(self, run, genji, source, *, facing=0, button=1, frames=24):
        for frame in range(frames):
            run.step({genji: (facing, 0, 0), source: (-32768, 0, button if frame < 2 else 0)})

    def state(self, genji):
        instance = next(i for i in genji.body_script.component.instances.values() if i.graph.index == 0x03A3)
        return instance.state(10), instance.state(0)

    def test_frontal_soldier_hit_is_reflected_and_credited_to_genji(self):
        run, genji, source = self.start()
        self.incoming(run, genji, source)
        self.assertEqual(self.health(run, genji), 200)
        self.assertEqual(self.health(run, source), 160)  # reflected eye-height rifle hit
        self.assertEqual(len(genji.client.messages(combat.HIT)), 1)
        hit = genji.client.messages(combat.HIT)[0]["+0x78"]
        self.assertEqual(hit["+0x4"], 40)
        self.assertEqual(hit["+0x8"] & combat.CRITICAL, combat.CRITICAL)
        self.assertEqual(source.client.messages(combat.HIT), [])

    def test_mei_spray_exclusion_keeps_damage_owner_but_icicle_reflects_from_front(self):
        for button, facing, genji_hp, mei_hp, genji_hits, mei_hits, charge in (
            (1, 0, 180.75, 250, [], [3] * 7, 0),
            (2, 0, 200, 175, [75], [], 75),
            (2, -32768, 125, 250, [], [75], 0),
        ):
            with self.subTest(button=button, facing=facing):
                run, genji = self.spawn(4)
                mei = run.players[1]
                run.match.switch_hero(mei, heroes()[0x02E00000000000DD])
                mei.mover.position[:] = [0, 1, 4]
                yaw, pitch = aim(run.match.combat._eye(mei), (0, 1.75, 0))
                for frame in range(60):
                    run.step({genji: (facing, 0, 16 if frame == 0 else 0),
                              mei: (yaw, pitch, button if 5 <= frame < 25 else 0)})
                self.assertTrue(self.state(genji)[0].active)
                self.assertEqual(self.health(run, genji), genji_hp)
                self.assertEqual(self.health(run, mei), mei_hp)
                self.assertEqual(genji.body_script.component.entity_var(342).value(), charge)
                for attacker, victim, amounts in ((genji, mei, genji_hits), (mei, genji, mei_hits)):
                    hits = [notice["+0x78"] for notice in attacker.client.messages(combat.HIT)]
                    self.assertEqual(hits, [
                        {"+0x0": {"+0x0": victim.body}, "+0x4": amount, "+0x8": 0} for amount in amounts
                    ])

    def test_mei_spray_pierces_deflect_once_per_body_but_stops_at_wall_or_bubble(self):
        for blocker in (None, "wall", "bubble"):
            with self.subTest(blocker=blocker):
                run, genji = self.spawn(0, ("Genji", "Mei", "Rear", "Far", "Protector"))
                mei, rear, far, protector = run.players[1:]
                run.match.switch_hero(mei, heroes()[0x02E00000000000DD])
                run.match.switch_hero(protector, heroes()[0x02E0000000000068])
                run.match.game_map = replace(run.match.game_map, free_for_all=False)
                mei.team = 1
                far_position = (0, 1, 4.02) if blocker is None else (-0.6, 1, 4.1)
                for player, position in ((genji, (0, 1, 2)), (rear, (0, 1, 4)), (far, far_position),
                                         (protector, (2, 1, 5.5))):
                    player.team = 0
                    player.mover.position[:] = position
                mei.mover.position[:] = [0, 1, 0]
                if blocker == "wall":
                    run.match.collision_world = World(FLOOR + wall(-5, 3, 5, 3))
                for _ in range(30):
                    run.step({})
                fight = run.match.combat
                if blocker == "bubble":
                    recipient = next(target for target in fight._targets() if target.owner is rear)
                    x, y, z = recipient.feet
                    yaw, pitch = aim(fight._eye(protector), (x, y + recipient.height / 2, z))
                    run.step({protector: (yaw, pitch, 16)})
                    for _ in range(12):
                        run.step({protector: (yaw, pitch, 0)})
                    bubble, = fight.barriers.scripted.values()
                    self.assertIs(bubble.recipient, rear)
                run.step({genji: (-32768, 0, 16)})
                yaw, pitch = aim(fight._eye(mei), (0, 2, 2))
                run.step({genji: (-32768, 0, 0), mei: (yaw, pitch, 1)})
                spray, = fight.rockets
                self.assertEqual(spray.target_limit, 99)
                for _ in range(24):
                    run.step({genji: (-32768, 0, 0)})
                self.assertFalse(fight.rockets)
                self.assertTrue(self.state(genji)[0].active)
                self.assertEqual(self.health(run, mei), 250)
                self.assertFalse(genji.client.messages(combat.HIT))
                contacted = (genji, rear, far) if blocker is None else (genji,)
                for target in (genji, rear, far):
                    self.assertEqual(self.health(run, target), 197.25 if target in contacted else 200)
                    self.assertAlmostEqual(fight.frost_progress(target, tick=run.frame),
                                           0.2 if target in contacted else 0.0)
                hits = [notice["+0x78"]["+0x0"]["+0x0"] for notice in mei.client.messages(combat.HIT)]
                self.assertEqual(hits, [target.body for target in contacted])
                if blocker is None:
                    self.assertEqual(fight.frost_hits[rear.body].sources[mei.body, 0][0][1],
                                     fight.frost_hits[far.body].sources[mei.body, 0][0][1])
                if blocker == "bubble":
                    self.assertEqual(bubble.vitals.total, 197.25)
                    self.assertAlmostEqual(protector.body_script.component.entity_var(87).value(), 0.55)

    def test_hitscan_can_clip_deflect_boxes_outside_the_body(self):
        for offset, expected in ((0.5, 160), (0.8, 200)):
            with self.subTest(offset=offset):
                run, genji, source = self.start(lateral=offset)
                self.incoming(run, genji, source)
                self.assertEqual(self.health(run, genji), 200)
                self.assertEqual(self.health(run, source), expected)
                self.assertEqual(len(genji.client.messages(combat.HIT)), int(expected < 200))

    def test_projectiles_can_clip_deflect_boxes_outside_the_body(self):
        for offset, expected in ((0.8, 26), (0.9, 200)):
            with self.subTest(offset=offset):
                run, genji, source = self.start(projectile=True, lateral=offset)
                self.incoming(run, genji, source, frames=60)
                self.assertEqual(self.health(run, genji), 200)
                self.assertEqual(self.health(run, source), expected)

    def test_static_wall_precedes_deflect_boxes(self):
        for projectile in (False, True):
            with self.subTest(projectile=projectile):
                run, genji, source = self.start(projectile=projectile, lateral=0.5)
                run.match.collision_world = World(FLOOR + wall(-5, 2, 5, 2))
                self.incoming(run, genji, source, frames=60)
                self.assertEqual(self.health(run, genji), 200)
                self.assertEqual(self.health(run, source), 200)
                self.assertFalse(genji.client.messages(combat.HIT))
                self.assertFalse(run.match.combat.rockets)

    def test_nearer_enemy_precedes_deflect_boxes(self):
        run, genji = self.spawn(4, ("Genji", "Shooter", "Nearer"))
        source, nearer = run.players[1:]
        nearer.mover.position[:] = [0, 1, 2]
        for frame in range(5):
            run.step({genji: (0, 0, 16 if frame == 0 else 0)})
        self.incoming(run, genji, source)
        self.assertEqual(self.health(run, nearer), 160)
        self.assertEqual(self.health(run, source), 200)
        self.assertEqual(self.health(run, genji), 200)
        self.assertFalse(genji.client.messages(combat.HIT))

    def test_friendly_deflect_boxes_do_not_intercept(self):
        run, genji = self.spawn(4, ("Genji", "Shooter", "Enemy"))
        source, enemy = run.players[1:]
        run.match.game_map = replace(run.match.game_map, free_for_all=False)
        source.team = genji.team = 0
        enemy.team = 1
        enemy.mover.position[:] = [0, 1, -2]
        for frame in range(5):
            run.step({genji: (0, 0, 16 if frame == 0 else 0)})
        self.incoming(run, genji, source)
        self.assertEqual(self.health(run, genji), 200)
        self.assertEqual(self.health(run, source), 200)
        self.assertEqual(self.health(run, enemy), 160)
        self.assertFalse(genji.client.messages(combat.HIT))

    def test_reflected_kill_belongs_to_genji(self):
        run, genji, source = self.start()
        wounded = combat.Vitals(source.body, 200)
        wounded.damage(180)
        run.match.combat.vitals[source.body] = wounded
        self.incoming(run, genji, source)
        self.assertTrue(run.match.combat.is_dead(source))
        self.assertEqual(wounded.killer, genji.body)
        self.assertEqual(len(genji.client.messages(combat.KILL_NOTICE)), 1)
        self.assertEqual(self.health(run, genji), 200)

    def test_reflected_final_blow_resets_strike_and_allows_deflect_cancel(self):
        for lethal in (False, True):
            with self.subTest(lethal=lethal):
                run, genji = self.spawn(4)
                source = run.players[1]
                if lethal:
                    yaw, pitch = aim(run.match.combat._eye(genji), (0, 2.55, 4))
                    for frame in range(60):
                        run.step({genji: (yaw, pitch, 1 if frame == 0 else 0)})
                self.assertEqual(self.health(run, source), 26 if lethal else 200)
                previous_hits = len(genji.client.messages(combat.HIT))

                # Dash away so the initial dash neither wounds nor kills the shooter.
                for frame in range(35):
                    run.step({genji: (-32768, 0, 8 if frame == 0 else 0)})
                self.assertEqual(genji.mover.position, [0, 1, -15])
                dash = next(i for i in genji.body_script.component.instances.values() if i.graph.index == 0x01DB)
                self.assertGreater(dash.state(0).var.value(), 7)

                for frame in range(5):
                    run.step({genji: (0, 0, 16 if frame == 0 else 0)})
                self.incoming(run, genji, source)
                hits = genji.client.messages(combat.HIT)[previous_hits:]
                self.assertEqual([hit["+0x78"]["+0x4"] for hit in hits], [26 if lethal else 32])
                self.assertEqual(len(genji.client.messages(combat.KILL_NOTICE)), int(lethal))
                self.assertFalse(source.client.messages(combat.HIT))
                self.assertEqual(self.health(run, genji), 200)
                self.assertEqual(run.match.combat.is_dead(source), lethal)
                self.assertTrue(self.state(genji)[0].active)
                if lethal:
                    self.assertEqual(dash.state(0).var.value(), 0)
                else:
                    self.assertGreater(dash.state(0).var.value(), 7)

                for frame in range(35):
                    run.step({genji: (-32768, 0, 8 if frame == 0 else 0)})
                self.assertEqual(genji.mover.position, [0, 1, -30 if lethal else -15])
                self.assertEqual(self.state(genji)[0].active, not lethal)

    def test_rear_hitscan_damages_genji(self):
        run, genji, source = self.start(facing=-32768)
        self.incoming(run, genji, source, facing=-32768)
        self.assertEqual(self.health(run, genji), 160)
        self.assertEqual(self.health(run, source), 200)

    def test_shuriken_projectiles_transfer_owner_and_team(self):
        run, genji, source = self.start(projectile=True)
        self.incoming(run, genji, source, frames=60)
        self.assertEqual(self.health(run, genji), 200)
        self.assertEqual(self.health(run, source), 26)
        self.assertEqual(len(genji.client.messages(combat.HIT)), 3)

    def test_reflection_preserves_per_launch_damage_and_loadout(self):
        for button, distance, loadout, damage in (
            (1, 4.0, 0x0B900000000000DB, [17, 31, 31]),
            (2, 1.5, 0x0B900000000000DC, [17, 17, 17]),
        ):
            with self.subTest(button=button):
                run, genji, source = self.start(distance=distance, projectile=True)
                source_weapon, deflector_weapon = (
                    next(i for i in player.body_script.component.instances.values() if i.graph.index == 0x0304)
                    for player in (source, genji)
                )
                source_weapon.write((INSTANCE, 476), 17)
                deflector_weapon.write((INSTANCE, 476), 63)
                source_weapon.component.run_now()
                deflector_weapon.component.run_now()

                run.step({source: (-32768, 0, button)})
                flights = {id(rocket): rocket for rocket in run.match.combat.rockets}
                source_weapon.write((INSTANCE, 476), 31)
                source_weapon.component.run_now()
                for _ in range(59):
                    run.step({source: (-32768, 0, 0)})
                    flights.update((id(rocket), rocket) for rocket in run.match.combat.rockets)

                self.assertEqual(
                    [(rocket.direct.amount, rocket.direct.loadout) for rocket in flights.values()],
                    [(amount, loadout) for amount in damage],
                )
                for rocket in flights.values():
                    self.assertIs(rocket.shooter, genji)
                    self.assertEqual(rocket.team, genji.team)
                expected_hits = [amount * 2 for amount in damage]
                hits = [notice["+0x78"] for notice in genji.client.messages(combat.HIT)]
                self.assertEqual([hit["+0x4"] for hit in hits], expected_hits)
                self.assertTrue(all(hit["+0x8"] & combat.CRITICAL for hit in hits))
                self.assertEqual(self.health(run, source), 200 - sum(expected_hits))
                self.assertEqual(self.health(run, genji), 200)
                self.assertEqual(genji.body_script.component.entity_var(342).value(), sum(expected_hits))
                self.assertFalse(source.client.messages(combat.HIT))

    def test_second_deflect_transfers_the_same_projectile_back(self):
        run, first = self.spawn(16)
        second = run.players[1]
        run.match.switch_hero(second, heroes()[GENJI])
        second.mover.position[:] = [0, 1, 16]
        run.step({first: (0, 0, 2), second: (-32768, 0, 16)})
        self.assertEqual(len(run.match.combat.rockets), 3)
        centre = max(run.match.combat.rockets, key=lambda rocket: rocket.direction[2])
        self.assertIs(centre.shooter, first)
        transfers = []
        owner = first
        remaining = centre.left
        for frame in range(1, 75):
            run.step({
                first: (0, 0, 16 if frame == 8 else 0),
                second: (-32768, 0, 16 if frame == 36 else 0),
            })
            if centre.shooter is not owner:
                owner = centre.shooter
                transfers.append((owner, centre.team))
                self.assertLess(centre.left, remaining)
                remaining = centre.left
        self.assertEqual(transfers, [(second, second.team), (first, first.team)])
        self.assertFalse(self.state(second)[0].active)
        self.assertEqual(self.health(run, first), 200)
        self.assertEqual(self.health(run, second), 142)
        hits = first.client.messages(combat.HIT)
        self.assertEqual([hit["+0x78"]["+0x4"] for hit in hits], [58])
        self.assertEqual(first.body_script.component.entity_var(342).value(), 58)
        self.assertFalse(second.client.messages(combat.HIT))

    def test_reflected_shuriken_across_death_and_body_replacement(self):
        for event in ("shooter_death", "deflector_death", "deflector_replacement"):
            with self.subTest(event=event):
                run, genji = self.spawn(16, ("Deflector", "Shooter", "Recipient", "Soldier"))
                source, recipient, soldier = run.players[1:]
                run.match.game_map = replace(run.match.game_map, free_for_all=False)
                genji.team = 0
                source.team = recipient.team = 1
                victim = source if event == "shooter_death" else genji
                soldier.team = 1 - victim.team
                run.match.switch_hero(source, heroes()[GENJI])
                source.mover.position[:] = [0, 1, 16]
                recipient.mover.position[:] = [4, 1, 8]
                victim_z = victim.mover.position[2]
                soldier.mover.position[:] = [-3, 1, victim_z]
                for _ in range(30):
                    run.step({})
                rifle_yaw, rifle_pitch = aim(run.match.combat._eye(soldier), (0, 2.2, victim_z))
                if event != "deflector_replacement":
                    for _ in range(40):
                        run.step({soldier: (rifle_yaw, rifle_pitch, 1)})
                        if self.health(run, victim) == 40:
                            break
                    self.assertEqual(self.health(run, victim), 40)
                    for _ in range(20):
                        run.step({})

                # The reflected trajectory begins at the forward box face.
                yaw, pitch = aim((0, 2.2, 1.15), (4, 2.2, 8))
                run.step({genji: (yaw, pitch, 16), source: (-32768, 0, 2)})
                reflected = None
                for _ in range(30):
                    run.step({genji: (yaw, pitch, 0)})
                    reflected = next((r for r in run.match.combat.rockets if r.shooter is genji), None)
                    if reflected is not None:
                        break
                self.assertIsNotNone(reflected)
                old_script = genji.body_script
                if event == "deflector_replacement":
                    self.assertTrue(run.match.switch_hero(genji, genji.hero))
                    self.assertTrue(old_script.retired)
                    self.assertFalse(any(rocket is reflected for rocket in run.match.combat.rockets))
                else:
                    run.step({genji: (yaw, pitch, 0), soldier: (rifle_yaw, rifle_pitch, 1)})
                    self.assertTrue(run.match.combat.is_dead(victim))
                    self.assertTrue(any(rocket is reflected for rocket in run.match.combat.rockets))
                self.assertEqual(self.health(run, recipient), 200)
                for _ in range(40):
                    run.step({})
                damage = 0 if event == "deflector_replacement" else 29
                self.assertEqual(self.health(run, genji), 0 if event == "deflector_death" else 200)
                self.assertEqual(self.health(run, recipient), 200 - damage)
                hits = genji.client.messages(combat.HIT)
                self.assertEqual([hit["+0x78"]["+0x4"] for hit in hits], [damage] if damage else [])
                charge = genji.body_script.component.entity_var(342).value()
                self.assertEqual(charge, damage)
                self.assertFalse(source.client.messages(combat.HIT))

    def test_projectiles_from_rear_are_not_reflected(self):
        run, genji, source = self.start(facing=-32768, projectile=True)
        self.incoming(run, genji, source, facing=-32768, frames=60)
        self.assertEqual(self.health(run, genji), 26)
        self.assertEqual(self.health(run, source), 200)

    def test_reflected_projectile_uses_deflectors_enemy_team(self):
        run, genji = self.spawn(4, ("Genji", "Shooter", "Shooters ally"))
        source, ally = run.players[1:]
        run.match.game_map = run.match.game_map.__class__(
            **{**run.match.game_map.__dict__, "free_for_all": False}
        )
        source.team = ally.team = 1
        genji.team = 0
        run.match.switch_hero(source, heroes()[GENJI])
        source.mover.position[:] = [0, 1, 4]
        ally.mover.position[:] = [0, 1, 2]
        for frame in range(5):
            run.step({genji: (0, 0, 16 if frame == 0 else 0)})
        self.incoming(run, genji, source, frames=60)
        self.assertEqual(self.health(run, genji), 200)
        self.assertEqual(self.health(run, source), 200)
        # Actual Genji1.2m view yields level body hits on the Soldier ally.
        self.assertEqual(self.health(run, ally), 113)
        self.assertTrue(all(not hit["+0x78"]["+0x8"] & combat.CRITICAL
                            for hit in genji.client.messages(combat.HIT)))

    def test_quick_melee_is_blocked_without_return_damage(self):
        run, genji, source = self.start(distance=1)
        self.incoming(run, genji, source, button=0x800, frames=60)
        self.assertEqual(self.health(run, genji), 200)
        self.assertEqual(self.health(run, source), 200)

    def test_rear_quick_melee_is_not_blocked(self):
        run, genji, source = self.start(distance=1, facing=-32768)
        self.incoming(run, genji, source, facing=-32768, button=0x800, frames=60)
        self.assertEqual(self.health(run, genji), 170)

    def test_expiry_removes_protection_and_starts_cooldown(self):
        run, genji, source = self.start()
        for _ in range(140):
            run.step({})
        state, ability = self.state(genji)
        self.assertFalse(state.active)
        self.assertGreater(ability.var.value(), 7)
        self.incoming(run, genji, source)
        self.assertEqual(self.health(run, genji), 160)

    def test_cancel_removes_protection_and_cooldown_rejects_reactivation(self):
        run, genji, source = self.start()
        for frame in range(60):
            run.step({genji: (0, 0, 16 if frame in (25, 45) else 0)})
        state, ability = self.state(genji)
        self.assertFalse(state.active)
        self.assertEqual(state.counter, 1)
        self.assertGreater(ability.var.value(), 7)
        self.incoming(run, genji, source)
        self.assertEqual(self.health(run, genji), 160)

    def test_after_cooldown_new_press_can_deflect_again(self):
        run, genji, source = self.start()
        for _ in range(650):
            run.step({})
        state, ability = self.state(genji)
        self.assertEqual(ability.var.value(), 0)
        run.step({genji: (0, 0, 16)})
        self.assertTrue(state.active)
        self.assertEqual(state.counter, 2)
        self.incoming(run, genji, source)
        self.assertEqual(self.health(run, genji), 200)
        self.assertEqual(self.health(run, source), 160)

    def test_death_removes_protection(self):
        run, genji, source = self.start()
        target = next(t for t in run.match.combat._targets() if t.owner is genji)
        run.match.combat._damage(source, target, 200, False, None, run.frame)
        run.step({})
        self.assertFalse(run.match.combat._genji_deflects(target, (0, 0, -1)))

    def test_noneligible_damage_is_not_blanket_blocked(self):
        run, genji, source = self.start()
        # This tests the explicit eligibility contract, not a restored beam hero.
        run.match.combat.rockets.append(combat.Rocket(
            source, source.team, (0, 2, 2), (0, 0, -1), 60, 1, combat.HitDef(10, None), None,
        ))
        for _ in range(10):
            run.step({})
        self.assertEqual(self.health(run, genji), 190)
        self.assertEqual(self.health(run, source), 200)

    def test_splash_damage_is_not_blanket_blocked(self):
        run, genji, source = self.start()
        blast = combat.HitDef(80, None, rings=((3, 1),))
        run.match.combat.rockets.append(combat.Rocket(
            source, source.team, (0, 2, 2), (0, 0, -1), 60, 1, None, blast,
        ))
        for _ in range(10):
            run.step({})
        self.assertEqual(self.health(run, genji), 120)


if __name__ == "__main__":
    unittest.main()
