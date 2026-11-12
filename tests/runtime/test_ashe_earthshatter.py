"""Live build 104319 Earthshatter against Ashe and B.O.B."""

import os
import unittest
from unittest.mock import patch

import test_ashe_bob as bob_fixtures
import test_ashe_controls as controls
import test_ashe_fire_strike as fixtures
from test_ashe_impulses import wall_scene
from test_game_combat import aim

from ow174.game import combat, earthshatter, sombra_hack
from ow174.game.content import HEROES_PATH, heroes
from ow174.game.script import graph
from ow174.game.script.driver import ULT_CHARGE, ULT_COST

WAVE_TAGS = (0x0D80000000000654, 0x0D800000000001FD, 0x0D80000000002D55, 0x0D80000000004D2B)
CLOSE_TAGS = (*WAVE_TAGS, 0x0D80000000006E31)


@unittest.skipUnless(HEROES_PATH.is_file() and graph.GRAPHS_PATH.is_file(), "requires external 104319 data")
class AsheEarthshatterTests(unittest.TestCase):
    def cast(self, run, rein, point, frames):
        angle = aim(run.match.combat._eye(rein), point)
        for frame in range(frames):
            run.step({rein: (*angle, 32 if frame == 0 else 0)})

    def test_ground_release_spends_charge_and_stuns_ashe_once(self):
        run, ashe, rein, other = fixtures.FireStrikeTests().arena()
        fight = run.match.combat
        other.mover.position[0] = 12
        other.mover.body = None
        script = rein.body_script
        component = script.component
        instance = sombra_hack.instance_for(rein, earthshatter.GRAPH)
        cost = component.entity_var(ULT_COST).value()
        self.assertEqual(cost, 1540)
        point = fight._eye(ashe)
        script.set_ult_charge(0)
        self.cast(run, rein, point, 35)
        self.assertFalse(fight.earthshatters)
        script.set_ult_charge(cost)
        self.cast(run, rein, point, 29)
        wave, = fight.earthshatters.values()
        self.assertEqual(component.entity_var(ULT_CHARGE).value(), 0)
        self.assertEqual((wave.speed, wave.reach, wave.cone, wave.height), (40, 20, 50, 1.75))
        self.assertEqual((wave.amount, wave.near_amount, wave.duration), (50, 250, 2.5))
        self.assertNotIn(ashe.body, fight.stuns)
        with patch.object(fight, "_damage", wraps=fight._damage) as damage:
            for _ in range(36):
                run.step({})
                if ashe.body in fight.stuns:
                    break
        self.assertEqual(damage.call_args.kwargs["damage_tags"], WAVE_TAGS)
        self.assertEqual(fight.vitals[ashe.body].total, 150)
        deadline = fight.stuns[ashe.body]
        self.assertEqual(deadline - run.frame, 157)
        for _ in range(45):
            run.step({})
        self.assertFalse(fight.earthshatters)
        self.assertFalse(instance.states[0].active)
        self.assertEqual(fight.vitals[ashe.body].total, 150)
        self.assertEqual(fight.stuns[ashe.body], deadline)
        for _ in range(160):
            if not fight.is_stunned(ashe):
                break
            run.step({})
        self.assertEqual(run.frame, deadline)
        self.assertFalse(fight.is_stunned(ashe))

    @unittest.skipUnless(fixtures.EXTENSION.is_file(), "requires explicit B.O.B. graph extension")
    def test_earthshatter_pauses_live_bob_gun_until_stun_expiry(self):
        run, ashe, rein, actor = bob_fixtures.BobTests().cast()
        fight = run.match.combat
        self.assertTrue(run.match.switch_hero(rein, heroes()[fixtures.REINHARDT]))
        rein.mover.position[:] = (0, 1, 10)
        rein.mover.body = None
        fight.vitals[rein.body] = combat.Vitals(rein.body, 10000)
        for _ in range(65):
            run.step({})
        self.assertGreater(actor.shots, 0)
        rein.body_script.set_ult_charge(1540)
        with patch.object(fight, "_damage", wraps=fight._damage) as damage:
            self.cast(run, rein, fight._eye(ashe), 58)
        hit, = (call for call in damage.call_args_list if call.args[1].entity == actor.bot.entity)
        self.assertEqual(hit.kwargs["damage_tags"], WAVE_TAGS)
        self.assertEqual(fight.vitals[actor.bot.entity].total, 1150)
        self.assertEqual(fight.vitals[ashe.body].total, 150)
        deadline = fight.stuns[actor.bot.entity]
        shots = actor.shots
        for _ in range(157):
            if run.frame >= deadline - 1:
                break
            run.step({})
        self.assertEqual(actor.shots, shots)
        for _ in range(20):
            run.step({})
        self.assertGreater(actor.shots, shots)

    def test_static_wall_blocks_wave_and_stun(self):
        run, ashe, rein, other = fixtures.FireStrikeTests().arena()
        fight = run.match.combat
        other.mover.position[0] = 12
        other.mover.body = None
        run.match.collision_world = wall_scene(3)
        target = next(item for item in fight._targets() if item.entity == ashe.body)
        vitals = fight.vitals_of(target)
        rein.body_script.set_ult_charge(1540)
        self.cast(run, rein, fight._eye(ashe), 70)
        self.assertEqual(vitals.total, 200)
        self.assertNotIn(ashe.body, fight.stuns)
        self.assertFalse(fight.earthshatters)

    def test_authored_close_hit_kills_ashe_at_release(self):
        run, ashe, rein, other = fixtures.FireStrikeTests().arena()
        fight = run.match.combat
        other.mover.position[0] = 12
        other.mover.body = None
        ashe.mover.position[:] = (0, 1, 4)
        ashe.mover.body = None
        rein.body_script.set_ult_charge(1540)
        with patch.object(fight, "_damage", wraps=fight._damage) as damage:
            self.cast(run, rein, fight._eye(ashe), 29)
        self.assertEqual(damage.call_args.kwargs["damage_tags"], CLOSE_TAGS)
        self.assertTrue(fight.vitals[ashe.body].dead)
        self.assertEqual(fight.vitals[ashe.body].killer, rein.body)
        self.assertEqual(len(rein.client.messages(combat.KILL_NOTICE)), 1)
        self.assertNotIn(ashe.body, fight.stuns)

    @unittest.skipUnless(os.environ.get("ORPHEUS_REINHARDT_COLLISION"), "requires 104319 Barrier Field mesh")
    def test_raised_field_blocks_wave_and_shields_ashe(self):
        run, ashe, rein, defender = fixtures.FireStrikeTests().arena()
        fight = run.match.combat
        self.assertTrue(run.match.switch_hero(defender, heroes()[fixtures.REINHARDT]))
        defender.mover.position[:] = (0, 1, 3)
        defender.mover.body = None
        for _ in range(35):
            run.step({defender: (0, 0, 2)})
        shield = fight.reinhardt_barriers.states[defender.body]
        self.assertTrue(shield.active)
        targets = {item.entity: fight.vitals_of(item) for item in fight._targets()}
        rein.body_script.set_ult_charge(1540)
        attack = aim(fight._eye(rein), fight._eye(ashe))
        for frame in range(70):
            run.step({rein: (*attack, 32 if frame == 0 else 0), defender: (0, 0, 2)})
        self.assertEqual(shield.current, 1550)
        self.assertEqual(targets[defender.body].total, 500)
        self.assertEqual(targets[ashe.body].total, 200)
        self.assertNotIn(defender.body, fight.stuns)
        self.assertNotIn(ashe.body, fight.stuns)
        self.assertFalse(fight.earthshatters)

    @unittest.skipUnless(os.environ.get("ORPHEUS_REINHARDT_COLLISION"), "requires 104319 Barrier Field mesh")
    def test_wave_retains_the_field_it_breaks_until_that_cast_ends(self):
        run, ashe, rein, defender = fixtures.FireStrikeTests().arena()
        fight = run.match.combat
        self.assertTrue(run.match.switch_hero(defender, heroes()[fixtures.REINHARDT]))
        for player, position in ((ashe, (2.31, 1, 0)), (rein, (0, 1, 6.35)), (defender, (0, 1, 3))):
            player.mover.position[:] = position
            player.mover.body = None
        for _ in range(35):
            run.step({defender: (0, 0, 2)})
        host = fight.reinhardt_barriers
        shield = host.states[defender.body]
        self.assertTrue(shield.active)
        # The middle rays reach the mesh a tick before the rays aimed near Ashe.
        shield.current = 50.0
        host.instance(defender).write((0, 586), 50.0)
        target = next(item for item in fight._targets() if item.entity == ashe.body)
        health = fight.vitals_of(target)
        rein.body_script.set_ult_charge(1540)
        for frame in range(70):
            run.step({rein: (-32768, 0, 32 if frame == 0 else 0), defender: (0, 0, 2)})
        self.assertFalse(shield.active)
        self.assertEqual(shield.current, 0)
        self.assertEqual(health.total, 200)
        self.assertNotIn(ashe.body, fight.stuns)
        self.assertFalse(fight.earthshatters)

        rein.body_script.set_ult_charge(1540)
        for frame in range(70):
            run.step({rein: (-32768, 0, 32 if frame == 0 else 0)})
        self.assertEqual(health.total, 150)
        self.assertTrue(fight.is_stunned(ashe))

    def test_emp_cancels_paid_windup_and_preserves_released_wave(self):
        for released in (False, True):
            with self.subTest(released=released):
                run, ashe, rein, sombra = fixtures.FireStrikeTests().sombra_arena()
                fight = run.match.combat
                target = next(item for item in fight._targets() if item.entity == ashe.body)
                health = fight.vitals_of(target)
                rein.mover.position[:] = (0, 1, 20)
                rein.mover.body = None
                sombra.mover.position[:] = (4, 1, 20)
                sombra.mover.body = None
                rein.body_script.set_ult_charge(1540)
                angle = aim(fight._eye(rein), fight._eye(ashe))
                if released:
                    self.cast(run, rein, fight._eye(ashe), 29)
                    wave, = fight.earthshatters.values()
                sombra.body_script.set_ult_charge(1400)
                run.step({rein: (*angle, 0 if released else 32), sombra: (0, 0, 32)})
                for frame in range(21):
                    run.step({rein: (*angle, 0)})
                    if frame == 18:
                        self.assertFalse(sombra_hack.locked(rein))
                        self.assertEqual(rein.body_script.component.entity_var(ULT_CHARGE).value(), 0)
                self.assertTrue(sombra_hack.locked(rein))
                self.assertFalse(sombra_hack.locked(ashe))
                if released:
                    self.assertIs(fight.earthshatters[rein.body], wave)
                for _ in range(45):
                    run.step({})
                    if not released:
                        self.assertFalse(fight.earthshatters)
                self.assertEqual(health.total, 150 if released else 200)
                self.assertEqual(fight.is_stunned(ashe), released)
                self.assertFalse(fight.earthshatters)

    def test_hack_rejects_fresh_earthshatter_until_expiry(self):
        run, ashe, rein, sombra = fixtures.FireStrikeTests().sombra_arena()
        fight = run.match.combat
        for _ in range(65):
            angle = aim(fight._eye(sombra), fight._eye(rein))
            run.step({sombra: (*angle, 2)})
            if sombra_hack.locked(rein):
                break
        self.assertTrue(sombra_hack.locked(rein))
        rein.body_script.set_ult_charge(1540)
        component = rein.body_script.component
        self.cast(run, rein, fight._eye(ashe), 60)
        self.assertEqual(component.entity_var(ULT_CHARGE).value(), 1540)
        self.assertFalse(fight.earthshatters)
        self.assertEqual(fight.vitals[ashe.body].total, 200)
        for _ in range(320):
            if not sombra_hack.locked(rein):
                break
            run.step({})
        self.assertFalse(sombra_hack.locked(rein))
        self.cast(run, rein, fight._eye(ashe), 60)
        self.assertEqual(fight.vitals[ashe.body].total, 150)

    def test_projected_barrier_absorbs_wave_before_ashe_can_be_stunned(self):
        fixture = controls.AsheBarrierTests()
        run, rein, zarya, ashe = fixture.projected_arena()
        fight = run.match.combat
        self.assertTrue(run.match.switch_hero(rein, heroes()[fixtures.REINHARDT]))
        self.assertTrue(run.match.switch_hero(ashe, heroes()[fixtures.ASHE]))
        zarya.team = ashe.team = 1 - rein.team
        for player, x, z in ((rein, 0, 7), (zarya, 4, 0), (ashe, 0, 0)):
            player.mover.position[:] = (x, 1, z)
            player.mover.body = None
        for _ in range(35):
            run.step({})
        direction, projected = fixture.projected_input(run, zarya, ashe)
        run.step({zarya: (*direction, 16)})
        phase = projected.states[10]
        self.assertTrue(phase.active)
        self.assertEqual(projected.vars[536].value().id, ashe.body)

        rein.body_script.set_ult_charge(1540)
        self.cast(run, rein, fight._eye(ashe), 60)
        self.assertEqual(fight.personal_barriers[ashe.body][2], 150)
        self.assertEqual(fight.vitals[ashe.body].total, 200)
        self.assertNotIn(ashe.body, fight.stuns)
        fixture.assert_energy_credit(zarya, 50, rein.body, 10)
        for _ in range(130):
            if not phase.active:
                break
            run.step({})
        self.assertFalse(phase.active)

        rein.body_script.set_ult_charge(1540)
        self.cast(run, rein, fight._eye(ashe), 60)
        self.assertEqual(fight.vitals[ashe.body].total, 150)
        self.assertTrue(fight.is_stunned(ashe))
