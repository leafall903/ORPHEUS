"""Actual Zarya/Sigma input with external projectile tags; not native filter proof."""

import json
import os
import tempfile
import unittest
from pathlib import Path
from types import MappingProxyType, SimpleNamespace
from unittest.mock import patch

from test_game_combat import Run, aim

from ow174.game import combat, interception, world
from ow174.game.collision import World
from ow174.game.commands import Command
from ow174.game.content import heroes

SECONDARY, ULTIMATE = 0x04000000000003DB, 0x04000000000008CE


def queue_sleep_wake_and_defense(test, run, caster, defender, ana, *, wake_offset, defense_button):
    """Queue a rear Sleep Dart, a Zarya melee wake, and a later Particle Grenade."""
    match, fight = run.match, run.match.combat
    test.assertTrue(match.switch_hero(ana, heroes()[0x02E000000000013B]))
    caster.mover.position[:] = (0, 1, 3.5)
    defender.mover.position[:] = (0, 1, 5)
    ana.mover.position[:] = (0, 1, 7)
    caster.mover.body = defender.mover.body = ana.mover.body = None
    ana.team = caster.team
    angles = aim(fight._eye(ana), (0, 2, 5))
    for frame in range(45):
        run.step({ana: (*angles, 8 if frame == 0 else 0)})
        if fight.rockets:
            break
    dart, = fight.rockets
    test.assertEqual(dart.direct.effect, combat.SLEEP_DART_EFFECT)
    test.assertFalse(dart.flown)
    start = run.frame
    wake = start + wake_offset
    cast = start + 25
    grenade = start + 80
    inputs = (
        (wake, caster, Command(wake, buttons=0x800)),
        (cast, defender, Command(cast, yaw=-32768, buttons=defense_button)),
        (grenade, caster, Command(grenade, buttons=2)),
    )
    for at, owner, command in sorted(inputs, key=lambda event: event[0]):
        match.tick = at
        owner.take_commands([command])
    return start + 100


@unittest.skipUnless(os.environ.get("ORPHEUS_PROJECTILE_TAGS"), "requires external authored projectile tags")
class ZaryaAbsorptionTests(unittest.TestCase):
    def scene(self, *, facing=-32768, close=False, ally=False, nearer=False):
        run = Run(names=("Zarya", "Sigma", "Victim"))
        a, b, c = run.players
        run.match.bots.clear()
        run.match._pending_bot_scripts.clear()
        run.match.switch_hero(a, heroes()[0x02E0000000000068])
        run.match.switch_hero(b, heroes()[0x02E000000000023B])
        a.team, b.team, c.team = 0, 0 if ally else 1, 1
        a.mover.position[:] = [0, 1, 3.5 if close else 0]
        b.mover.position[:] = [0, 1, 5]
        c.mover.position[:] = [0, 1, 1 if nearer else 10]
        self.assertTrue(interception.eligible(SECONDARY))
        self.assertTrue(interception.eligible(ULTIMATE))
        for i in range(5):
            run.step({b: (facing, 0, 8 if i == 0 else 0)})
        self.assertTrue(self.instance(b).state(1).active)
        return run, a, b, c

    @staticmethod
    def instance(player):
        return next(i for i in player.body_script.component.instances.values() if i.graph.index == 0x1D02)

    @staticmethod
    def health(run, player):
        fight = run.match.combat
        return fight.vitals_of(next(t for t in fight._targets() if t.owner is player)).total

    @staticmethod
    def advance(run, frames, a=None, button=0, b=None, facing=-32768):
        for _ in range(frames):
            commands = {p: (p.mover.yaw, p.mover.pitch, 0) for p in run.players}
            if a is not None:
                commands[a] = (0, 0, button)
            if b is not None:
                commands[b] = (facing, 0, 0)
            run.step(commands)

    def shoot(self, run, a, *, ultimate=False):
        if ultimate:
            a.body_script.set_ult_charge(2100)
        commands = {p: (p.mover.yaw, p.mover.pitch, 0) for p in run.players}
        commands[a] = (0, 0, 0x20 if ultimate else 2)
        run.step(commands)

    def test_grasp_consumes_halt_before_it_can_pull_zarya_without_granting_shields(self):
        definition = 0x040000000000172C
        self.assertTrue(interception.eligible(definition))
        run, orisa, sigma, victim = self.scene()
        self.assertTrue(run.match.switch_hero(orisa, heroes()[0x02E000000000013E]))
        self.assertTrue(run.match.switch_hero(victim, heroes()[0x02E0000000000068]))
        orisa.mover.position[:] = [0, 1, 0]
        victim.mover.position[:] = [0, 1, 9]
        orisa.mover.body = victim.mover.body = None
        fight = run.match.combat
        launched = False
        for frame in range(26):
            run.step({orisa: (0, 0, 2 if frame in (0, 25) else 0), sigma: (-32768, 0, 0)})
            launched |= any(rocket.definition == definition for rocket in fight.rockets)
        self.assertTrue(launched)
        self.assertFalse(fight.rockets)
        instance = next(i for i in orisa.body_script.component.instances.values() if i.graph.index == 0x0CCB)
        self.assertGreater(instance.state(2).remaining(), 4)
        self.advance(run, 135, b=sigma)
        self.assertEqual(self.health(run, sigma), 400)
        self.assertEqual(self.health(run, victim), 400)
        self.assertEqual(victim.mover.position[2], 9)
        self.assertNotIn(victim.body, fight.halt_pulls)

    def test_grasp_consumes_ana_grenade_and_dart_without_status(self):
        for button, amount in ((8, 5), (16, 50)):
            with self.subTest(button=button):
                run, ana, sigma, zarya = self.scene()
                self.assertTrue(run.match.switch_hero(ana, heroes()[0x02E000000000013B]))
                self.assertTrue(run.match.switch_hero(zarya, heroes()[0x02E0000000000068]))
                ana.mover.position[:] = [0, 1, 0]
                zarya.mover.position[:] = [0, 1, 7]
                fight = run.match.combat
                angles = aim(fight._eye(ana), fight._eye(sigma))
                for frame in range(45):
                    run.step({ana: (*angles, button if frame == 0 else 0), sigma: (-32768, 0, 0)})
                self.assertEqual(self.instance(sigma).vars[21312].value(), amount)
                self.assertEqual(self.health(run, sigma), 400)
                self.assertEqual(self.health(run, zarya), 400)
                for player in (sigma, zarya):
                    self.assertNotIn(player.body, fight.sleeps)
                    self.assertEqual(fight.vitals[player.body].healing_block_until, 0)
                self.assertFalse(fight.rockets)

    def test_grasp_absorbs_both_hyperspheres_but_accretion_interrupts_it(self):
        for rock in (False, True):
            with self.subTest(rock=rock):
                run, attacker, receiver, _ = self.scene()
                self.assertTrue(run.match.switch_hero(attacker, heroes()[0x02E000000000023B]))
                attacker.mover.position[:] = [0, 1, 0]
                fight = run.match.combat
                angles = aim(fight._eye(attacker), fight._eye(receiver))
                instance = self.instance(receiver)
                for frame in range(65):
                    button = (16 if rock else 1) if frame == 0 else 0
                    run.step({attacker: (*angles, button), receiver: (-32768, 0, 0)})
                    if rock and fight.is_stunned(receiver):
                        break
                self.assertFalse(fight.rockets)
                self.assertEqual(self.health(run, receiver), 330 if rock else 400)
                self.assertEqual(instance.vars[21312].value(), 0 if rock else 110)
                self.assertEqual(fight.is_stunned(receiver), rock)
                self.assertEqual(instance.state(1).active, not rock)
                if not rock:
                    self.assertFalse(attacker.client.messages(combat.HIT))
                    self.advance(run, 75, b=receiver)
                    self.assertFalse(instance.state(1).active)
                    self.assertEqual(self.health(run, receiver), 466)
                else:
                    self.assertFalse(fight.vitals[receiver.body].temporary_shields)

    def test_secondary_is_consumed_and_authored_absorption_receives_47(self):
        run, a, b, c = self.scene()
        a.body_script.ult_seconds = 0
        a.body_script.set_ult_charge(0)
        self.shoot(run, a)
        self.advance(run, 60)
        self.assertEqual(self.health(run, b), 400)
        self.assertEqual(self.health(run, c), 200)
        self.assertEqual(self.instance(b).vars[21312].value(), 47)
        self.assertFalse(run.match.combat.rockets)
        self.assertFalse(a.client.messages(combat.HIT))
        self.assertEqual(a.body_script.component.entity_var(342).value(), 0)

    def test_absorbed_graviton_spends_charge_without_damage_or_field(self):
        run, a, b, c = self.scene()
        a.body_script.ult_seconds = 0
        self.shoot(run, a, ultimate=True)
        self.advance(run, 280)
        self.assertEqual(a.body_script.component.entity_var(342).value(), 0)
        self.assertEqual(self.health(run, b), 403)  # authored Grasp exit converts5 ->3 temporary shields
        self.assertEqual(self.health(run, c), 200)
        self.assertEqual(self.instance(b).vars[21312].value(), 5)
        self.assertFalse(run.match.combat.rockets)
        self.assertFalse(run.match.combat.zarya_gravitons.fields)

    def test_primary_beam_bypasses_grasp(self):
        run, a, b, _ = self.scene()
        self.advance(run, 51, a, 1)
        self.assertAlmostEqual(self.health(run, b), 340)
        self.assertEqual(self.instance(b).vars[21312].value(), 0)

    def test_absorbed_graviton_recharges_after_cast_wait_without_input(self):
        run, a, b, _ = self.scene()
        self.shoot(run, a, ultimate=True)
        launched = run.frame
        self.advance(run, 16)
        self.assertFalse(run.match.combat.rockets)
        self.assertFalse(run.match.combat.zarya_gravitons.fields)
        self.assertEqual(self.instance(b).vars[21312].value(), 5)
        for tick in range(run.frame + 1, launched + 32):
            run.match.update(tick * combat.TICK, tick)
            self.assertEqual(a.body_script.component.entity_var(342).value(), 0)
        tick = launched + 32
        run.match.update(tick * combat.TICK, tick)
        self.assertAlmostEqual(a.body_script.component.entity_var(342).value(), 0.08)

    def test_melee_bypasses_grasp(self):
        run, a, b, _ = self.scene(close=True)
        run.step({a: (0, 0, 0x800)})
        self.advance(run, 40)
        self.assertEqual(self.health(run, b), 370)
        self.assertEqual(self.instance(b).vars[21312].value(), 0)

    def test_aimed_away_grasp_does_not_absorb(self):
        run, a, b, _ = self.scene(facing=0)
        self.shoot(run, a)
        self.advance(run, 60, b=b, facing=0)
        self.assertEqual(self.health(run, b), 353)
        self.assertEqual(self.instance(b).vars[21312].value(), 0)

    def test_allied_grasp_does_not_intercept_friendly_projectile(self):
        run, a, b, c = self.scene(ally=True)
        self.shoot(run, a)
        self.advance(run, 60)
        self.assertEqual(self.health(run, b), 400)
        self.assertEqual(self.health(run, c), 153)
        self.assertEqual(self.instance(b).vars[21312].value(), 0)

    def test_nearer_enemy_body_is_hit_before_grasp_volume(self):
        run, a, b, c = self.scene(nearer=True)
        self.shoot(run, a)
        self.advance(run, 60)
        self.assertEqual(self.health(run, c), 153)
        self.assertEqual(self.instance(b).vars[21312].value(), 0)

    def test_wall_stops_projectile_before_grasp_volume(self):
        run, a, b, _ = self.scene()
        wall = World([(-10, 0, 1, 10, 0, 1, 10, 10, 1),
                      (-10, 0, 1, 10, 10, 1, -10, 10, 1)])
        with patch.object(run.match.combat, "_collision", return_value=wall):
            self.shoot(run, a)
            self.advance(run, 60)
        self.assertEqual(self.health(run, b), 400)
        self.assertEqual(self.instance(b).vars[21312].value(), 0)

    def test_expired_grasp_no_longer_intercepts(self):
        run, a, b, _ = self.scene()
        self.advance(run, 140)
        self.assertFalse(self.instance(b).state(1).active)
        self.shoot(run, a)
        self.advance(run, 60)
        self.assertEqual(self.health(run, b), 353)

    def test_dead_sigma_no_longer_protects_bystander(self):
        run, a, b, c = self.scene()
        fight = run.match.combat
        target = next(t for t in fight._targets() if t.owner is b)
        fight._damage(a, target, 500, False, None, run.frame)
        self.assertTrue(fight.is_dead(b))
        self.shoot(run, a)
        self.advance(run, 60)
        self.assertEqual(self.health(run, c), 153)
        self.assertEqual(self.instance(b).vars[21312].value(), 0)

    def test_replacing_grasp_body_during_flight_does_not_leave_a_volume(self):
        run, a, b, _ = self.scene()
        self.shoot(run, a)
        old = b.body
        run.match.switch_hero(b, heroes()[0x02E000000000006E])
        self.assertNotEqual(b.body, old)
        b.mover.position[:] = [0, 1, 5]
        self.advance(run, 60)
        self.assertAlmostEqual(self.health(run, b), 153)
        self.assertFalse(any(i.graph.index == 0x1D02 for i in b.body_script.component.instances.values()))

    def test_original_projectile_team_survives_owner_team_change(self):
        run, a, b, _ = self.scene()
        self.shoot(run, a)
        a.team = b.team
        self.advance(run, 60)
        self.assertEqual(self.health(run, b), 400)
        self.assertEqual(self.instance(b).vars[21312].value(), 47)

    def test_unknown_or_excluded_metadata_is_not_blanket_absorption(self):
        for tags in ({}, {SECONDARY: frozenset({interception.EXCLUDED_TAG})}):
            with self.subTest(tags=tags):
                run, a, b, _ = self.scene()
                with patch.object(interception, "_TAGS", MappingProxyType(tags)):
                    self.shoot(run, a)
                    self.advance(run, 60)
                self.assertEqual(self.health(run, b), 353)
                self.assertEqual(self.instance(b).vars[21312].value(), 0)

    def test_unleased_projectile_retains_definition_and_is_still_absorbed(self):
        run, a, b, _ = self.scene()
        with patch.object(run.match.combat.projectiles, "fired", return_value=None):
            self.shoot(run, a)
        rocket, = run.match.combat.rockets
        self.assertEqual(rocket.definition, SECONDARY)
        self.assertIsNone(rocket.shot)
        self.advance(run, 60)
        self.assertEqual(self.health(run, b), 400)
        self.assertEqual(self.instance(b).vars[21312].value(), 47)

    def test_confirmed_owner_copy_is_destroyed_without_explosion_release(self):
        run, a, _, _ = self.scene()
        self.shoot(run, a)
        rocket, = run.match.combat.rockets
        shot = rocket.shot
        self.assertIsNotNone(shot)
        entity = 0xA0012345
        shot.created[entity] = SimpleNamespace(received=True)
        self.advance(run, 60)
        self.assertNotIn(shot, run.match.combat.projectiles.shots)
        removed = [u for u in a.client.entities if u.entity == entity and u.op == world.OP_DESTROY]
        self.assertTrue(removed)
        self.assertTrue(all(u.ch2 is None for u in removed))


class ProjectileTagTests(unittest.TestCase):
    def test_loader_rejects_wrong_build_and_duplicate_or_wrong_type_tags(self):
        base = {"version": 1, "build": interception.BUILD, "entities": {
            "0x04000000000003DB": ["0x0D600000000002F9"]}}
        cases = [dict(base, build="other"), dict(base, version=True), dict(base, entities={"wrong": []}),
                 dict(base, entities={"0x04000000000003DB": ["0x0400000000000001"]}),
                 dict(base, entities={"0x04000000000003DB": ["0x0D600000000002F9"] * 2})]
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "tags.json"
            for case in cases:
                path.write_text(json.dumps(case))
                with self.assertRaises(ValueError):
                    interception.read_tags(path)


if __name__ == "__main__":
    unittest.main()
