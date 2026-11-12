"""Actual Roadhog Scrap Gun against the hosted Tracer."""

import unittest
from dataclasses import replace

import test_game_tracer as tracer_tests
import test_game_tracer_pulse_deflect as support_tests
import test_game_tracer_winston_barrier as bubble_tests
from test_game_combat import aim
from test_game_mover import box, wall

from ow174.game import combat, deflection, dva, sigma_grasp, stats, tracer
from ow174.game.collision import World
from ow174.game.commands import FIRE, SECONDARY, Command
from ow174.game.content import heroes
from ow174.game.script import nodes
from ow174.game.script.driver import ULT_CHARGE, ULT_COST


class TracerRoadhogTests(unittest.TestCase):
    def test_whole_hog_spends_charge_pushes_tracer_and_ends_without_spending_ammo(self):
        run, player, hog, angles, ammo = self.scene(5)
        fight = run.match.combat
        target = next(t for t in fight._targets() if t.owner is player)
        health = fight.vitals_of(target)
        script = hog.body_script
        script.ult_seconds = 0
        cost = script.component.entity_var(ULT_COST).value()
        meter = script.component.entity_var(ULT_CHARGE)
        ability, = script.ultimates
        script.set_ult_charge(cost - 1)
        run.step({hog: (*angles, 32)})
        for _ in range(40):
            run.step({})
        self.assertFalse(ability.flags & nodes.Ability.RUNNING)
        self.assertEqual(meter.value(), cost - 1)
        self.assertEqual(health.total, 150)

        script.set_ult_charge(cost)
        run.step({hog: (*angles, 32)})
        self.assertTrue(ability.flags & nodes.Ability.RUNNING)
        self.assertEqual(meter.value(), 0)
        start = player.mover.position[2]
        for _ in range(90):
            run.step({hog: (*angles, 0)})
            if health.total < 150:
                break
        self.assertGreater(health.total, 0)
        self.assertLess(health.total, 150)
        self.assertTrue(player.mover.external_impulse)
        self.assertAlmostEqual(player.mover.velocity[2], 10, delta=0.25)
        self.assertAlmostEqual(player.mover.velocity[1], 1.8, places=4)
        hits = hog.client.messages(combat.HIT)
        self.assertGreater(len(hits), 0)
        self.assertLess(len(hits), 16)
        self.assertEqual(150 - health.total, 7 * len(hits))
        for _ in range(480):
            run.step({hog: (-32768, 0, 0)})
            if not ability.flags & nodes.Ability.RUNNING:
                break
        self.assertGreater(player.mover.position[2], start + 0.5)
        self.assertFalse(ability.flags & nodes.Ability.RUNNING)
        self.assertEqual(ammo.value(), 5)
        after = health.total
        run.step({hog: (*angles, 32)})
        for _ in range(75):
            run.step({})
        self.assertFalse(ability.flags & nodes.Ability.RUNNING)
        self.assertEqual(meter.value(), 0)
        self.assertEqual(health.total, after)

    def test_actual_scrap_gun_damage_funds_whole_hog_without_credit_from_its_volley(self):
        run, player, hog, angles, _ = self.scene(5)
        fight = run.match.combat
        health = fight.vitals_of(next(t for t in fight._targets() if t.owner is player))
        script = hog.body_script
        script.ult_seconds = 0
        cost = script.component.entity_var(ULT_COST).value()
        meter = script.component.entity_var(ULT_CHARGE)
        script.set_ult_charge(cost - 50)
        run.step({hog: (*angles, SECONDARY)})
        for _ in range(62):
            run.step({})
        self.assertEqual(health.total, 100)
        self.assertEqual(meter.value(), cost)
        run.step({hog: (*angles, 32)})
        self.assertEqual(meter.value(), 0)
        for _ in range(90):
            run.step({hog: (*angles, 0)})
            if health.total < 100:
                break
        self.assertLess(health.total, 100)
        self.assertEqual(meter.value(), 0)

    def test_whole_hog_undercharge_press_is_buffered_only_until_its_window_expires(self):
        for delay, buffered in ((0, True), (5, False)):
            with self.subTest(delay=delay):
                run, player, hog, angles, _ = self.scene(5)
                fight = run.match.combat
                health = fight.vitals_of(next(t for t in fight._targets() if t.owner is player))
                script = hog.body_script
                script.ult_seconds = 0
                meter = script.component.entity_var(ULT_CHARGE)
                cost = script.component.entity_var(ULT_COST).value()
                script.set_ult_charge(cost - 1)
                ability, = script.ultimates
                self.assertAlmostEqual(ability.evaluate(ability.node.field("m_246438AD"), watch=False), 0.15)

                run.step({hog: (*angles, 32)})
                self.assertFalse(ability.flags & nodes.Ability.RUNNING)
                self.assertEqual(meter.value(), cost - 1)
                for _ in range(delay):
                    run.step({hog: (*angles, 0)})
                run.step({hog: (*angles, SECONDARY)})
                for _ in range(25):
                    run.step({hog: (*angles, 0)})
                    if health.total < 150:
                        break
                run.step({hog: (*angles, 0)})

                self.assertEqual(health.total, 100)
                self.assertEqual(bool(ability.flags & nodes.Ability.RUNNING), buffered)
                self.assertEqual(meter.value(), 0 if buffered else cost)
                if not buffered:
                    run.step({hog: (*angles, 32)})
                    self.assertTrue(ability.flags & nodes.Ability.RUNNING)
                    self.assertEqual(meter.value(), 0)

    def test_actual_whole_hog_damage_falloff_and_capsule_contact_at_range_limit(self):
        for distance, damage in ((25.0, 7), (35.0, 4.55), (99.99, 2.1), (100.01, 0)):
            with self.subTest(distance=distance):
                run, player, hog, angles, _ = self.scene(distance)
                fight = run.match.combat
                target = next(t for t in fight._targets() if t.owner is player)
                health = fight.vitals_of(target)
                direction = combat.aim(*angles)
                contact = combat.ray_capsule(fight._eye(hog), direction, *target.segment(), target.radius)
                self.assertIsNotNone(contact)
                # Shift the muzzle along the centre ray to set the capsule contact distance.
                hog.mover.position[:] = combat._along(hog.mover.position, direction, contact - distance)
                hog.mover.body = None
                run.step({})
                final_contact = combat.ray_capsule(
                    fight._eye(hog), direction, *target.segment(), target.radius,
                )
                self.assertAlmostEqual(final_contact, distance, delta=0.005)

                script = hog.body_script
                script.set_ult_charge(script.component.entity_var(ULT_COST).value())
                run.step({hog: (*angles, 32)})
                for _ in range(90):
                    run.step({hog: (*angles, 0)})
                    if health.total < 150:
                        break
                self.assertAlmostEqual(health.total, 150 - damage, places=3)
                self.assertEqual(len(hog.client.messages(combat.HIT)), int(damage > 0))
                if damage == 0:
                    self.assertFalse(player.mover.external_impulse)

    def test_actual_whole_hog_head_contact_uses_normal_pellet_damage(self):
        run, player, hog, _, _ = self.scene(25)
        fight = run.match.combat
        target = next(t for t in fight._targets() if t.owner is player)
        health = fight.vitals_of(target)
        self.assertIsNotNone(target.head_offset)
        centre = combat._along(target.feet, target.head_offset, 1)
        origin = fight._eye(hog)
        angles = aim(origin, centre)
        self.assertIsNotNone(combat.ray_capsule(
            origin, combat.aim(*angles), centre, centre, combat.TRACER_HEAD_RADIUS,
        ))

        script = hog.body_script
        script.set_ult_charge(script.component.entity_var(ULT_COST).value())
        run.step({hog: (*angles, 32)})
        for _ in range(90):
            run.step({hog: (*angles, 0)})
            if health.total < 150:
                break
        self.assertEqual(health.total, 143)
        self.assertEqual(len(hog.client.messages(combat.HIT)), 1)

    def test_actual_flashbang_and_lethal_pistols_interrupt_whole_hog(self):
        for lethal in (False, True):
            with self.subTest(lethal=lethal):
                run, player, hog, angles, _ = self.scene(5)
                fight = run.match.combat
                player_health = fight.vitals_of(next(t for t in fight._targets() if t.owner is player))
                hog_health = fight.vitals_of(next(t for t in fight._targets() if t.owner is hog))
                x, y, z = hog.mover.position
                if lethal:
                    hog_health.damage(hog_health.total - 6)
                    fight._sync_body_health(hog, hog_health)
                    attacker, buttons = player, FIRE
                else:
                    attacker, _ = support_tests.PulseDeflectTests().support(
                        run, player, hero=0x02E0000000000042,
                    )
                    attacker.team = player.team
                    bubble_tests.TracerWinstonBarrierTests.move(attacker, (x - 3, y, z))
                    for _ in range(20):
                        run.step({})
                    buttons = 16

                script = hog.body_script
                script.ult_seconds = 0
                script.set_ult_charge(script.component.entity_var(ULT_COST).value())
                ability, = script.ultimates
                run.step({hog: (*angles, 32)})
                for _ in range(90):
                    run.step({hog: (*angles, 0)})
                    if player_health.total < 150:
                        break
                self.assertGreater(player_health.total, 0)
                self.assertLess(player_health.total, 150)
                self.assertTrue(ability.flags & nodes.Ability.RUNNING)

                shot = aim(fight._eye(attacker), (x, y + 1, z))
                run.step({hog: (-32768, 0, 0), attacker: (*shot, buttons)})
                if lethal:
                    self.assertEqual(hog_health.total, 0)
                else:
                    for _ in range(35):
                        run.step({hog: (-32768, 0, 0)})
                        if fight.is_stunned(hog):
                            break
                    self.assertTrue(fight.is_stunned(hog))
                    self.assertGreater(hog_health.total, 0)
                self.assertFalse(ability.flags & nodes.Ability.RUNNING)

                before = player_health.total
                hits = len(hog.client.messages(combat.HIT))
                for _ in range(100):
                    px, py, pz = player.mover.position
                    retry = aim(fight._eye(hog), (px, py + 0.75, pz))
                    run.step({hog: (*retry, 0)})
                self.assertFalse(ability.flags & nodes.Ability.RUNNING)
                self.assertEqual(player_health.total, before)
                self.assertEqual(len(hog.client.messages(combat.HIT)), hits)

    def test_actual_whole_hog_respects_wall_team_and_winston_dome(self):
        for gate in ("wall", "ally", "dome"):
            with self.subTest(gate=gate):
                run, player, hog, angles, _ = self.scene(5 if gate != "dome" else 10)
                fight = run.match.combat
                health = fight.vitals_of(next(t for t in fight._targets() if t.owner is player))
                clear = run.match.collision_world
                if gate == "wall":
                    self.cover(run, player, player.mover.position[2] - 2)
                elif gate == "ally":
                    player.team = hog.team
                else:
                    winston, _ = support_tests.PulseDeflectTests().support(
                        run, player, hero=0x02E0000000000009,
                    )
                    winston.team = player.team
                    for _ in range(20):
                        run.step({})
                    dome = bubble_tests.TracerWinstonBarrierTests().cast(run, winston)
                script = hog.body_script
                script.set_ult_charge(script.component.entity_var(ULT_COST).value())
                run.step({hog: (*angles, 32)})
                for _ in range(90):
                    run.step({hog: (*angles, 0)})
                    if gate == "dome" and dome.health < 700:
                        break
                self.assertEqual(health.total, 150)
                self.assertFalse(player.mover.external_impulse)
                if gate == "dome":
                    self.assertLess(dome.health, 700)
                else:
                    run.match.collision_world = clear
                    player.team = 1 - hog.team
                    for _ in range(30):
                        run.step({hog: (*angles, 0)})
                        if health.total < 150:
                            break
                    self.assertLess(health.total, 150)
                    self.assertTrue(player.mover.external_impulse)

    def test_actual_whole_hog_cannot_damage_or_push_tracer_in_either_recall_phase(self):
        first_hit = None
        for recall_age in (None, 3, 34):
            with self.subTest(recall_age=recall_age):
                run, player, hog, angles, _ = self.scene(8)
                fight = run.match.combat
                health = fight.vitals_of(next(t for t in fight._targets() if t.owner is player))
                for _ in range(40):
                    run.step({player: (0, 0, 0)})
                script = hog.body_script
                script.set_ult_charge(script.component.entity_var(ULT_COST).value())
                run.step({hog: (-32768, 0, 32), player: (0, 0, 0)})
                for frame in range(1, 135):
                    face = (-32768, 0) if frame < 45 else angles
                    recall = 16 if recall_age is not None and frame == first_hit - recall_age else 0
                    run.step({hog: (*face, 0), player: (0, 0, recall)})
                    if recall_age is not None and frame == first_hit:
                        self.assertTrue(tracer.recalling(player))
                        phase = player.body_script.world.recall_session.state.node.pos
                        self.assertEqual(phase, 4 if recall_age == 3 else 1)
                    if tracer.recalling(player):
                        self.assertEqual(health.total, 150)
                        self.assertFalse(player.mover.external_impulse)
                        self.assertFalse(hog.client.messages(combat.HIT))
                    if health.total < 150:
                        if recall_age is None:
                            first_hit = frame
                        break
                self.assertIsNotNone(first_hit)
                self.assertLess(health.total, 150)
                self.assertTrue(player.mover.external_impulse)
                if recall_age is not None:
                    self.assertGreater(frame, first_hit)
                    self.assertFalse(tracer.recalling(player))

    def test_actual_blink_escapes_whole_hog_aim_until_roadhog_reaims(self):
        run, player, hog, angles, _ = self.scene(8)
        fight = run.match.combat
        health = fight.vitals_of(next(t for t in fight._targets() if t.owner is player))
        before = player.mover.position[0]
        script = hog.body_script
        script.set_ult_charge(script.component.entity_var(ULT_COST).value())
        ability, = script.ultimates
        run.step({hog: (*angles, 32)})
        run.step({hog: (*angles, 0), player: (16384, 0, 8)})
        self.assertGreater(abs(player.mover.position[0] - before), 5)
        for _ in range(75):
            run.step({hog: (*angles, 0)})
        self.assertTrue(ability.flags & nodes.Ability.RUNNING)
        self.assertEqual(health.total, 150)
        self.assertFalse(player.mover.external_impulse)
        self.assertFalse(hog.client.messages(combat.HIT))

        x, y, z = player.mover.position
        reaim = aim(fight._eye(hog), (x, y + 0.75, z))
        for _ in range(35):
            run.step({hog: (*reaim, 0)})
            if health.total < 150:
                break
        self.assertLess(health.total, 150)
        self.assertTrue(player.mover.external_impulse)
        self.assertTrue(hog.client.messages(combat.HIT))

    def test_actual_whole_hog_matrix_and_grasp_prevent_damage_and_force(self):
        if not deflection.absorbable(0x0400000000000405):
            self.skipTest("requires extracted Whole Hog projectile tags")
        for hero, button in ((dva.HERO, SECONDARY), (sigma_grasp.HERO, 8)):
            with self.subTest(hero=hero):
                run, player, hog, angles, _ = self.scene(8)
                fight = run.match.combat
                defender, _ = support_tests.PulseDeflectTests().support(run, player, hero=hero)
                if hero == dva.HERO:
                    mech = replace(defender.hero, body=dva.MECH)
                    self.assertTrue(run.match.switch_hero(defender, mech))
                defender.team = player.team
                x, y, z = player.mover.position
                bubble_tests.TracerWinstonBarrierTests.move(defender, (x, y, z - 3))
                for _ in range(100 if hero == dva.HERO else 20):
                    run.step({})
                targets = {target.owner: target for target in fight._targets()}
                guarded = fight.vitals_of(targets[defender])
                before = guarded.total
                script = hog.body_script
                script.set_ult_charge(script.component.entity_var(ULT_COST).value())
                run.step({hog: (*angles, 32), defender: (-32768, 0, button)})
                held = button if hero == dva.HERO else 0
                for _ in range(65):
                    run.step({hog: (*angles, 0), defender: (-32768, 0, held)})
                self.assertEqual(guarded.total, before)
                self.assertEqual(fight.vitals_of(targets[player]).total, 150)
                self.assertFalse(defender.mover.external_impulse)
                self.assertFalse(player.mover.external_impulse)
                if hero == sigma_grasp.HERO:
                    instances = defender.body_script.component.instances.values()
                    grasp = next(
                        instance for instance in instances if instance.graph.index == sigma_grasp.GRASP
                    )
                    self.assertGreater(grasp.vars[21312].value(), 0)
                else:
                    for _ in range(25):
                        run.step({hog: (*angles, 0), defender: (-32768, 0, 0)})
                        if guarded.total < before:
                            break
                    self.assertLess(guarded.total, before)
                    self.assertTrue(defender.mover.external_impulse)

    def test_actual_genji_deflect_returns_whole_hog_damage_and_force(self):
        run, player, hog, angles, _ = self.scene(8)
        fight = run.match.combat
        genji, _ = support_tests.PulseDeflectTests().support(run, player, hero=0x02E0000000000029)
        genji.team = player.team
        x, y, z = player.mover.position
        bubble_tests.TracerWinstonBarrierTests.move(genji, (x, y, z - 3))
        for _ in range(20):
            run.step({})

        targets = {target.owner: target for target in fight._targets()}
        genji_health = fight.vitals_of(targets[genji])
        hog_health = fight.vitals_of(targets[hog])
        before_genji, before_hog = genji_health.total, hog_health.total
        script = hog.body_script
        script.set_ult_charge(script.component.entity_var(ULT_COST).value())
        guard = aim(fight._eye(genji), (x, y + 1, z - 8))
        run.step({hog: (*angles, 32), genji: (*guard, 16)})
        for _ in range(90):
            run.step({hog: (*angles, 0), genji: (*guard, 0)})
            if hog_health.total < before_hog:
                break

        self.assertEqual(genji_health.total, before_genji)
        self.assertFalse(genji.mover.external_impulse)
        self.assertEqual(fight.vitals_of(targets[player]).total, 150)
        self.assertFalse(player.mover.external_impulse)
        self.assertLess(hog_health.total, before_hog)
        self.assertTrue(hog.mover.external_impulse)
        self.assertLess(hog.mover.velocity[2], 0)
        self.assertTrue(genji.client.messages(combat.HIT))
        self.assertFalse(hog.client.messages(combat.HIT))

    def test_actual_breather_heals_only_roadhog_after_windup(self):
        run, player, hog, angles, _ = self.scene(5)
        fight = run.match.combat
        targets = {target.owner: target for target in fight._targets()}
        health = fight.vitals_of(targets[hog])
        other = fight.vitals_of(targets[player])
        health.damage(400)
        other.damage(50)
        fight._sync_body_health(hog, health)
        run.step({hog: (*angles, 16)})
        for _ in range(25):
            run.step({})
        self.assertEqual(health.total, 200)
        for _ in range(160):
            run.step({})
        self.assertAlmostEqual(health.total, 500)
        self.assertEqual(other.total, 100)
        self.assertAlmostEqual(hog.stats.value(stats.HEALING), 300)

    def test_actual_breather_effective_healing_funds_whole_hog(self):
        for missing, shortfall in ((40, 0), (300, 1)):
            with self.subTest(missing=missing):
                run, _, hog, angles, _ = self.scene(5)
                fight = run.match.combat
                target = next(t for t in fight._targets() if t.owner is hog)
                health = fight.vitals_of(target)
                health.damage(missing)
                fight._sync_body_health(hog, health)
                script = hog.body_script
                script.ult_seconds = 0
                meter = script.component.entity_var(ULT_CHARGE)
                cost = script.component.entity_var(ULT_COST).value()
                script.set_ult_charge(cost - missing - shortfall)
                run.step({hog: (*angles, 16)})
                for _ in range(25):
                    run.step({})
                self.assertEqual(meter.value(), cost - missing - shortfall)
                for _ in range(140):
                    run.step({})
                self.assertAlmostEqual(health.total, 600)
                self.assertAlmostEqual(hog.stats.value(stats.HEALING), missing)
                self.assertAlmostEqual(meter.value(), cost - shortfall, places=2)
                ability, = script.ultimates
                run.step({hog: (*angles, 32)})
                if shortfall:
                    for _ in range(80):
                        run.step({})
                    self.assertFalse(ability.flags & nodes.Ability.RUNNING)
                    self.assertAlmostEqual(meter.value(), cost - shortfall, places=2)
                else:
                    self.assertTrue(ability.flags & nodes.Ability.RUNNING)
                    self.assertEqual(meter.value(), 0)

    def test_breather_reduces_actual_pistols_pulse_and_expires_without_hog_input(self):
        for sparse in (False, True):
            with self.subTest(sparse=sparse):
                self.breather_damage_clock(sparse)

    def breather_damage_clock(self, sparse):
        run, player, hog, angles, _ = self.scene(5)
        fight = run.match.combat
        x, y, z = hog.mover.position
        reverse = aim(fight._eye(player), (x, y + 1, z))
        run.step({hog: (*angles, 16)})
        expires = fight.breather_deadlines[hog.body]
        self.assertEqual(expires - run.frame, 125)
        run.step({player: (*reverse, FIRE)})
        self.assertEqual(player.stats.value(stats.HERO_DAMAGE), 6)
        script = player.body_script
        script.set_ult_charge(script.component.entity_var(ULT_COST).value())
        run.step({player: (-32768, 0, 32)})
        for _ in range(45):
            run.step({player: (-32768, 0, 0)})
            if fight.pulse_bombs.bombs and fight.pulse_bombs.bombs[0].attached == hog.body:
                break
        bomb, = fight.pulse_bombs.bombs
        self.assertEqual(bomb.attached, hog.body)
        self.assertEqual(player.stats.value(stats.HERO_DAMAGE), 8.5)
        run.step({player: (0, 0, 8)})
        if sparse:
            self.assertLess(run.frame + bomb.fuse_left / combat.TICK, expires)
            end = expires + combat.COMBAT_CATCH_UP + 1
        else:
            for _ in range(80):
                if not fight.pulse_bombs.bombs:
                    break
                run.step({})
            self.assertLess(run.frame, expires)
            end = expires + 1
        script = hog.body_script
        frame = script.frame
        run.match.update(end * combat.TICK, end)
        self.assertFalse(fight.pulse_bombs.bombs)
        self.assertEqual(player.stats.value(stats.HERO_DAMAGE), 183.5)
        self.assertEqual(script.frame, frame)
        self.assertEqual(script.component.entity_var(8243).value(), 0.5)
        reverse = aim(fight._eye(player), (x, y + 1, z))
        player.client.queue_entities(run.match.remote_movements(player))
        player.take_commands([Command(end, yaw=reverse[0], pitch=reverse[1], buttons=FIRE)])
        self.assertEqual(player.stats.value(stats.HERO_DAMAGE), 195.5)

    def test_actual_flashbang_interrupts_breather_and_cooldown_allows_fresh_reuse(self):
        run, player, hog, angles, _ = self.scene(5)
        fight = run.match.combat
        health = fight.vitals_of(next(t for t in fight._targets() if t.owner is hog))
        health.damage(400)
        fight._sync_body_health(hog, health)
        mccree, _ = support_tests.PulseDeflectTests().support(run, player, hero=0x02E0000000000042)
        mccree.team = player.team
        x, y, z = hog.mover.position
        bubble_tests.TracerWinstonBarrierTests.move(mccree, (x - 3, y, z))
        run.step({hog: (*angles, 16)})
        for _ in range(45):
            run.step({})
        self.assertGreater(health.total, 200)
        self.assertLess(health.total, 500)
        throw = aim(fight._eye(mccree), (x, y + 1, z))
        run.step({mccree: (*throw, 16)})
        for _ in range(35):
            run.step({})
            if fight.is_stunned(hog):
                break
        self.assertTrue(fight.is_stunned(hog))
        self.assertNotIn(hog.body, fight.breather_deadlines)
        healed = hog.stats.value(stats.HEALING)
        before = health.total
        run.step({player: (*aim(fight._eye(player), (x, y + 1, z)), FIRE)})
        self.assertAlmostEqual(health.total, before - 12)
        for _ in range(80):
            run.step({})
        run.step({hog: (*angles, 16)})
        for _ in range(50):
            run.step({})
        self.assertEqual(hog.stats.value(stats.HEALING), healed)
        instances = hog.body_script.component.instances.values()
        ability = next(instance.state(0) for instance in instances if instance.graph.index == 0x0240)
        self.assertGreater(ability.remaining(), 0)
        for _ in range(650):
            if ability.remaining() == 0:
                break
            run.step({})
        self.assertEqual(ability.remaining(), 0)
        run.step({hog: (*angles, 16)})
        for _ in range(130):
            run.step({})
        self.assertAlmostEqual(hog.stats.value(stats.HEALING), healed + 300)

    def test_breather_heals_remaining_portion_once_when_commands_resume(self):
        run, _, hog, angles, _ = self.scene(5)
        fight = run.match.combat
        health = fight.vitals_of(next(t for t in fight._targets() if t.owner is hog))
        health.damage(400)
        run.step({hog: (*angles, 16)})
        for _ in range(60):
            run.step({})
        before = health.total
        self.assertGreater(before, 200)
        self.assertLess(before, 500)
        run.frame += 160
        run.match.update(run.frame * combat.TICK, run.frame)
        self.assertEqual(health.total, before)
        for _ in range(12):
            run.step({})
        self.assertAlmostEqual(health.total, 500)
        self.assertAlmostEqual(hog.stats.value(stats.HEALING), 300)

    def test_actual_anti_grenade_denies_breather_healing(self):
        run, player, hog, angles, _ = self.scene(5)
        fight = run.match.combat
        health = fight.vitals_of(next(t for t in fight._targets() if t.owner is hog))
        ana, _ = support_tests.PulseDeflectTests().support(run, player, hero=0x02E000000000013B)
        ana.team = player.team
        x, y, z = hog.mover.position
        bubble_tests.TracerWinstonBarrierTests.move(ana, (x - 3, y, z))
        run.step({ana: (*aim(fight._eye(ana), (x, y + 1, z)), 16)})
        for _ in range(70):
            run.step({})
            if health.healing_block_until > fight.tick:
                break
        self.assertLess(health.total, 600)
        blocked_health = health.total
        deadline = health.healing_block_until
        self.assertGreater(deadline, fight.tick)
        hog.body_script.ult_seconds = 0
        hog.body_script.set_ult_charge(0)
        meter = hog.body_script.component.entity_var(ULT_CHARGE)
        run.step({hog: (*angles, 16)})
        self.assertIn(hog.body, fight.breather_deadlines)
        for _ in range(160):
            run.step({})
        self.assertLess(fight.tick, deadline)
        self.assertEqual(health.total, blocked_health)
        self.assertEqual(hog.stats.value(stats.HEALING), 0)
        self.assertEqual(meter.value(), 0)

    def test_actual_pulse_can_kill_during_breather_windup_without_late_healing(self):
        run, player, hog, angles, _ = self.scene(5)
        fight = run.match.combat
        health = fight.vitals_of(next(t for t in fight._targets() if t.owner is hog))
        health.damage(500)
        script = player.body_script
        script.set_ult_charge(script.component.entity_var(ULT_COST).value())
        run.step({player: (-32768, 0, 32)})
        for _ in range(45):
            run.step({player: (-32768, 0, 0)})
            if fight.pulse_bombs.bombs and fight.pulse_bombs.bombs[0].attached == hog.body:
                break
        bomb, = fight.pulse_bombs.bombs
        self.assertEqual(bomb.attached, hog.body)
        self.assertEqual(health.total, 95)
        run.step({player: (0, 0, 8)})
        for _ in range(65):
            if bomb.fuse_left < 0.22:
                break
            run.step({})
        self.assertIn(bomb, fight.pulse_bombs.bombs)
        run.step({hog: (*angles, 16)})
        self.assertIn(hog.body, fight.breather_deadlines)
        for _ in range(24):
            run.step({})
            if health.dead:
                break
        self.assertTrue(health.dead)
        self.assertEqual(health.killer, player.body)
        self.assertNotIn(hog.body, fight.breather_deadlines)
        for _ in range(90):
            run.step({})
        self.assertEqual(health.total, 0)
        self.assertEqual(hog.stats.value(stats.HEALING), 0)

    def test_actual_hook_hits_pulls_and_releases_tracer(self):
        run, player, hog, angles, _ = self.scene(10)
        fight = run.match.combat
        health = fight.vitals_of(next(t for t in fight._targets() if t.owner is player))
        hog_health = fight.vitals_of(next(t for t in fight._targets() if t.owner is hog))
        start = player.mover.position[2]
        run.step({hog: (*angles, 8)})
        for _ in range(8):
            run.step({})
        self.assertEqual(health.total, 150)
        for _ in range(40):
            run.step({})
            if health.total < 150:
                break
        self.assertEqual(health.total, 120)
        self.assertTrue(fight.is_stunned(player))
        script = player.body_script
        cost = script.component.entity_var(ULT_COST).value()
        script.set_ult_charge(cost)
        for _ in range(8):
            run.step({player: (-32768, 0, FIRE | 8 | 16 | 32)})
            self.assertEqual(hog_health.total, 600)
        self.assertEqual(script.component.entity_var(ULT_CHARGE).value(), cost)
        self.assertFalse(fight.pulse_bombs.bombs)
        self.assertLess(player.mover.position[2], start)
        for _ in range(60):
            run.step({})
        self.assertFalse(fight.is_stunned(player))
        self.assertAlmostEqual(player.mover.position[2] - hog.mover.position[2], 3, places=2)
        self.assertEqual(health.total, 120)
        self.assertEqual(len(hog.client.messages(combat.HIT)), 1)
        x, y, z = hog.mover.position
        reverse = aim(fight._eye(player), (x, y + 1, z))
        run.step({player: (*reverse, FIRE)})
        self.assertEqual(hog_health.total, 588)

    def test_actual_hook_miss_team_cover_range_and_tracer_evasions(self):
        for gate in ("miss", "ally", "wall", "range", "blink", "recall"):
            with self.subTest(gate=gate):
                run, player, hog, angles, _ = self.scene(24 if gate == "range" else 10)
                fight = run.match.combat
                if gate == "miss":
                    angles = (-32768, 0)
                elif gate == "ally":
                    player.team = hog.team
                elif gate == "wall":
                    self.cover(run, player, player.mover.position[2] - 4)
                start = tuple(player.mover.position)
                health = fight.vitals_of(next(t for t in fight._targets() if t.owner is player))
                run.step({hog: (*angles, 8)})
                for _ in range(45):
                    run.step({hog: (*angles, 0)})
                    if hog.body in fight.roadhog_hooks.flights:
                        break
                self.assertIn(hog.body, fight.roadhog_hooks.flights)
                if gate == "blink":
                    run.step({player: (16384, 0, 8)})
                    self.assertGreater(abs(player.mover.position[0] - start[0]), 5)
                elif gate == "recall":
                    run.step({player: (0, 0, 16)})
                    self.assertTrue(tracer.recalling(player))
                for _ in range(100):
                    run.step({})
                    self.assertFalse(fight.roadhog_hooks.controlled(player.body))
                self.assertEqual(health.total, 150)
                self.assertFalse(hog.client.messages(combat.HIT))
                self.assertFalse(fight.roadhog_hooks.flights)
                if gate not in ("blink", "recall"):
                    self.assertEqual(tuple(player.mover.position), start)

    def test_hook_rejects_early_repress_then_can_hit_again(self):
        run, player, hog, angles, _ = self.scene(10)
        fight = run.match.combat
        target = next(t for t in fight._targets() if t.owner is player)
        health = fight.vitals_of(target)
        instance = next(i for i in hog.body_script.component.instances.values() if i.graph.index == 0x01C8)
        run.step({hog: (*angles, 8)})
        for _ in range(100):
            run.step({})
        self.assertEqual(health.total, 120)
        ability = next(state for state in instance.states.values()
                       if isinstance(state, nodes.Ability) and state.node.pos == 11)
        self.assertGreater(ability.remaining(), 0)
        original = fight.roadhog_hooks.seen[hog.body]
        run.step({hog: (*angles, 8)})
        for _ in range(40):
            run.step({})
        self.assertEqual(fight.roadhog_hooks.seen[hog.body], original)
        self.assertEqual(health.total, 120)
        for _ in range(700):
            if ability.remaining() == 0:
                break
            run.step({})
        self.assertEqual(ability.remaining(), 0)
        run.step({hog: (*angles, 8)})
        for _ in range(70):
            run.step({})
        self.assertNotEqual(fight.roadhog_hooks.seen[hog.body], original)
        self.assertEqual(health.total, 90)
        self.assertEqual(len(hog.client.messages(combat.HIT)), 2)

    def test_actual_hook_spends_winston_dome_instead_of_pulling_tracer(self):
        run, player, hog, angles, _ = self.scene(10)
        fight = run.match.combat
        winston, _ = support_tests.PulseDeflectTests().support(run, player, hero=0x02E0000000000009)
        winston.team = player.team
        for _ in range(25):
            run.step({})
        dome = bubble_tests.TracerWinstonBarrierTests().cast(run, winston)
        health = fight.vitals_of(next(t for t in fight._targets() if t.owner is player))
        start = tuple(player.mover.position)
        run.step({hog: (*angles, 8)})
        for _ in range(90):
            run.step({hog: (*angles, 0)})
        self.assertEqual(dome.health, 670)
        self.assertEqual(health.total, 150)
        self.assertEqual(tuple(player.mover.position), start)
        self.assertFalse(fight.roadhog_hooks.flights)
        self.assertFalse(hog.client.messages(combat.HIT))

    def test_hook_pull_finishes_on_world_clock_without_target_or_owner_input(self):
        run, player, hog, angles, _ = self.scene(10)
        fight = run.match.combat
        run.step({hog: (*angles, 8)})
        for _ in range(40):
            run.step({hog: (*angles, 0)})
            if fight.roadhog_hooks.controlled(player.body):
                break
        self.assertTrue(fight.roadhog_hooks.controlled(player.body))
        held_frames = (player.body_script.frame, hog.body_script.frame)
        for frame in range(run.frame + 1, run.frame + 81):
            run.match.update(frame * combat.TICK, frame)
        self.assertEqual((player.body_script.frame, hog.body_script.frame), held_frames)
        self.assertFalse(fight.roadhog_hooks.controlled(player.body))
        self.assertAlmostEqual(player.mover.position[2] - hog.mover.position[2], 3, places=2)

    def scene(self, distance):
        run, player, hog, _ = tracer_tests.TracerPistolTests().fight()
        run.match.game_map = replace(run.match.game_map, free_for_all=False)
        player.team, hog.team = 0, 1
        self.assertTrue(run.match.switch_hero(hog, heroes()[0x02E0000000000040]))
        x, y, z = player.mover.position
        hog.mover.position[:] = (x, y, z - distance)
        hog.mover.body = None
        run.step({})
        fight = run.match.combat
        angles = aim(fight._eye(hog), (x, y + 0.75, z))
        instances = hog.body_script.component.instances.values()
        weapon = next(instance for instance in instances if instance.graph.index == 0x01C6)
        return run, player, hog, angles, weapon.vars[53]

    def test_chain_hook_interrupts_unreleased_pulse_but_keeps_launched_bomb(self):
        for released in (False, True):
            with self.subTest(released=released):
                run, player, hog, angles, _ = self.scene(5 if released else 3.5)
                fight = run.match.combat
                script = player.body_script
                script.ult_seconds = 0
                meter = script.component.entity_var(ULT_CHARGE)
                script.set_ult_charge(script.component.entity_var(ULT_COST).value())
                hog_health = fight.vitals_of(next(t for t in fight._targets() if t.owner is hog))
                hog_health.damage(300)
                if released:
                    run.step({player: (-32768, 0, 32)})
                    for _ in range(40):
                        if fight.pulse_bombs.bombs:
                            break
                        run.step({player: (-32768, 0, 0)})
                    bomb, = fight.pulse_bombs.bombs
                run.step({hog: (*angles, 8)})
                for _ in range(45):
                    run.step({hog: (*angles, 0)})
                    if hog.body in fight.roadhog_hooks.flights:
                        break
                self.assertIn(hog.body, fight.roadhog_hooks.flights)
                if not released:
                    run.step({hog: (*angles, 0), player: (-32768, 0, 32)})
                    self.assertFalse(fight.is_stunned(player))
                    self.assertFalse(fight.pulse_bombs.bombs)
                self.assertEqual(meter.value(), 0)
                for _ in range(30):
                    run.step({})
                    if fight.roadhog_hooks.controlled(player.body):
                        break
                self.assertTrue(fight.roadhog_hooks.controlled(player.body))
                if released:
                    self.assertIn(bomb, fight.pulse_bombs.bombs)
                else:
                    self.assertFalse(fight.pulse_bombs.bombs)
                    ability = next(s for i in script.component.instances.values() if i.graph.index == 0x34
                                   for s in i.states.values() if isinstance(s, nodes.Ability))
                    self.assertFalse(ability.flags & nodes.Ability.RUNNING)
                for _ in range(60):
                    if not fight.roadhog_hooks.controlled(player.body):
                        break
                    run.step({})
                self.assertFalse(fight.roadhog_hooks.controlled(player.body))
                run.step({player: (0, 0, 8)})
                for _ in range(80):
                    run.step({})
                self.assertFalse(fight.pulse_bombs.bombs)
                self.assertEqual(meter.value(), 0)
                self.assertEqual(hog_health.total, 0 if released else 300)

    def test_hook_contact_one_centimetre_either_side_of_range(self):
        for delta in (-0.01, 0.01):
            with self.subTest(delta=delta):
                run, player, hog, _, _ = self.scene(10)
                fight = run.match.combat
                run.step({hog: (0, 0, 8)})
                for _ in range(45):
                    run.step({})
                    if hog.body in fight.roadhog_hooks.flights:
                        break
                flight = fight.roadhog_hooks.flights[hog.body]
                target = next(t for t in fight._targets() if t.owner is player)
                hit = combat.ray_capsule(
                    flight.origin, flight.direction, *target.segment(), target.radius + flight.radius,
                )
                self.assertIsNotNone(hit)
                shift = flight.reach + delta - hit
                player.mover.position[:] = combat._along(target.feet, flight.direction, shift)
                player.mover.body = None
                for _ in range(90):
                    run.step({})
                self.assertFalse(fight.roadhog_hooks.flights)
                self.assertEqual(fight.vitals_of(target).total, 120 if delta < 0 else 150)

    def test_latched_hook_releases_on_lethal_fire_departure_and_new_cover(self):
        cases = ("owner dies", "target dies", "owner leaves", "target leaves", "wall")
        for cause in cases:
            with self.subTest(cause=cause):
                run, player, hog, angles, _ = self.scene(10)
                fight = run.match.combat
                if cause.endswith("dies"):
                    soldier, _ = support_tests.PulseDeflectTests().support(run, player)
                    soldier.team = player.team if cause.startswith("owner") else hog.team
                    for _ in range(20):
                        run.step({})
                run.step({hog: (*angles, 8)})
                for _ in range(45):
                    run.step({hog: (*angles, 0)})
                    if fight.roadhog_hooks.controlled(player.body):
                        break
                self.assertTrue(fight.roadhog_hooks.controlled(player.body))
                self.assertTrue(player.mover.driven)
                removed = hog if cause.startswith("owner") else player
                if cause.endswith("dies"):
                    target = next(t for t in fight._targets() if t.owner is removed)
                    health = fight.vitals_of(target)
                    health.damage(health.total - 6)
                    x, y, z = target.feet
                    run.step({soldier: (*aim(fight._eye(soldier), (x, y + 1, z)), FIRE)})
                    self.assertTrue(health.dead)
                    self.assertEqual(health.killer, soldier.body)
                elif cause.endswith("leaves"):
                    run.match.leave(removed)
                    run.players.remove(removed)
                    self.assertTrue(removed.gone)
                else:
                    midpoint = (hog.mover.position[2] + player.mover.position[2]) / 2
                    self.cover(run, player, midpoint)
                    run.step({})
                self.assertFalse(fight.roadhog_hooks.flights)
                self.assertFalse(fight.roadhog_hooks.controlled(player.body))
                self.assertFalse(player.mover.driven)
                if removed is hog or cause == "wall":
                    x = player.mover.position[0]
                    run.step({player: (16384, 0, 8)})
                    self.assertGreater(abs(player.mover.position[0] - x), 5)

    def test_hook_rejects_late_inputs_from_the_pull_window_after_release(self):
        run, player, hog, angles, _ = self.scene(10)
        fight = run.match.combat
        hog_health = fight.vitals_of(next(t for t in fight._targets() if t.owner is hog))
        run.step({hog: (*angles, 8)})
        for _ in range(45):
            run.step({hog: (*angles, 0)})
            if fight.roadhog_hooks.controlled(player.body):
                break
        self.assertTrue(fight.roadhog_hooks.controlled(player.body))
        delayed = player.body_script.frame + 4
        end = run.frame + 80
        for frame in range(run.frame + 1, end + 1):
            run.match.update(frame * combat.TICK, frame)
        self.assertFalse(fight.roadhog_hooks.controlled(player.body))
        released = tuple(player.mover.position)
        x, y, z = hog.mover.position
        yaw, pitch = aim(fight._eye(player), (x, y + 1, z))
        player.client.queue_entities(run.match.remote_movements(player))
        player.take_commands([Command(delayed, yaw=yaw, pitch=pitch, buttons=FIRE),
                              Command(delayed + 1, yaw=16384, buttons=8)])
        self.assertEqual(hog_health.total, 600)
        self.assertEqual(tuple(player.mover.position), released)
        player.take_commands([Command(end, yaw=yaw, pitch=pitch, buttons=FIRE)])
        self.assertEqual(hog_health.total, 588)

    @staticmethod
    def cover(run, player, wall_z):
        x, y, z = player.mover.position
        floor = box(x - 15, x + 15, y - 1, y, z - 15, z + 15)
        run.match.collision_world = World(floor + wall(x - 5, wall_z, x + 5, wall_z, y, y + 5))

    def test_shared_magazine_empty_rejection_and_held_secondary_after_auto_reload(self):
        run, player, hog, angles, ammo = self.scene(5)
        fight = run.match.combat
        health = fight.vitals_of(next(t for t in fight._targets() if t.owner is player))
        away = -32768, 0
        for index, button in enumerate((SECONDARY, FIRE, SECONDARY, FIRE, SECONDARY)):
            start = run.frame
            run.step({hog: (*(angles if index == 0 else away), button)})
            self.assertEqual(ammo.value(), 4 - index)
            admitted = [rocket for rocket in fight.rockets if rocket.last_tick is None]
            self.assertEqual(len(admitted), 25 if button == FIRE else 1)
            if index < 4:
                while run.frame < start + 59:
                    run.step({})
        self.assertEqual(health.total, 100)
        run.step({hog: (*angles, FIRE)})
        self.assertEqual(ammo.value(), 0)
        run.step({hog: (*angles, SECONDARY)})
        for _ in range(220):
            self.assertEqual(ammo.value(), 0)
            self.assertEqual(health.total, 100)
            run.step({hog: (*angles, SECONDARY)})
            if ammo.value():
                break
        self.assertEqual(ammo.value(), 5)
        self.assertFalse(fight.rockets)
        for _ in range(120):
            run.step({hog: (*angles, SECONDARY)})
            if health.total < 100:
                break
        self.assertEqual(ammo.value(), 4)
        self.assertEqual(health.total, 50)
        self.assertEqual(len(hog.client.messages(combat.HIT)), 2)

    def test_manual_reload_rejects_primary_until_refill(self):
        run, player, hog, angles, ammo = self.scene(5)
        fight = run.match.combat
        health = fight.vitals_of(next(t for t in fight._targets() if t.owner is player))
        run.step({hog: (*angles, SECONDARY)})
        for _ in range(60):
            run.step({})
        self.assertEqual(health.total, 100)
        self.assertEqual(ammo.value(), 4)
        run.step({hog: (*angles, 0x400)})
        for _ in range(180):
            run.step({hog: (-32768, 0, FIRE)})
            if ammo.value() != 4:
                break
            self.assertFalse(fight.rockets)
        self.assertEqual(ammo.value(), 5)
        self.assertEqual(health.total, 100)
        for _ in range(120):
            run.step({hog: (-32768, 0, FIRE)})
            if fight.rockets:
                break
        self.assertEqual(len(fight.rockets), 25)
        self.assertEqual(ammo.value(), 4)

    def test_actual_winston_dome_intercepts_pellets_slug_and_fragments(self):
        cases = ((FIRE, -6, 165), (SECONDARY, -6, 50), (SECONDARY, 3, 165))
        for button, offset, expected in cases:
            with self.subTest(button=button, offset=offset):
                run, player, hog, angles, ammo = self.scene(16)
                fight = run.match.combat
                winston, _ = support_tests.PulseDeflectTests().support(run, player, hero=0x02E0000000000009)
                winston.team = player.team
                x, y, z = player.mover.position
                bubble_tests.TracerWinstonBarrierTests.move(winston, (x, y, z + offset))
                for _ in range(20):
                    run.step({})
                dome = bubble_tests.TracerWinstonBarrierTests().cast(run, winston)
                run.step({hog: (*angles, button)})
                for _ in range(35):
                    run.step({})
                self.assertAlmostEqual(dome.health, 700 - expected, places=4)
                self.assertEqual(fight.vitals[player.body].total, 150)
                winston_target = next(t for t in fight._targets() if t.owner is winston)
                self.assertEqual(fight.vitals_of(winston_target).total, 500)
                self.assertFalse(hog.client.messages(combat.HIT))
                self.assertEqual(ammo.value(), 4)
                self.assertFalse(fight.rockets)

    def test_actual_projectile_defenses_protect_tracer_from_primary_and_secondary(self):
        if not all(map(deflection.absorbable, combat.SCRAP_GUN_PROJECTILES)):
            self.skipTest("requires extracted Scrap Gun projectile tags")
        cases = ((0x02E0000000000029, 16), (0x02E000000000007A, SECONDARY), (sigma_grasp.HERO, 8))
        for hero, guard in cases:
            for button, expected in ((FIRE, 165), (SECONDARY, 50)):
                with self.subTest(hero=hero, button=button):
                    run, player, hog, angles, ammo = self.scene(8)
                    fight = run.match.combat
                    defender, _ = support_tests.PulseDeflectTests().support(run, player, hero=hero)
                    if hero == dva.HERO:
                        mech = replace(defender.hero, body=dva.MECH)
                        self.assertTrue(run.match.switch_hero(defender, mech))
                    defender.team = player.team
                    x, y, z = player.mover.position
                    bubble_tests.TracerWinstonBarrierTests.move(defender, (x, y, z - 3))
                    for _ in range(100 if hero == dva.HERO else 20):
                        run.step({})
                    hog_health = fight.vitals_of(next(t for t in fight._targets() if t.owner is hog))
                    run.step({defender: (-32768, 0, guard)})
                    held = guard if hero == dva.HERO else 0
                    for _ in range(8):
                        run.step({defender: (-32768, 0, held)})
                    if hero == dva.HERO:
                        instances = defender.body_script.component.instances.values()
                        matrix = next(instance for instance in instances if instance.graph.index == 0x02CC)
                        self.assertTrue(matrix.state(3).active)
                    run.step({hog: (*angles, button), defender: (-32768, 0, held)})
                    admitted = tuple(fight.rockets)
                    self.assertEqual(len(admitted), 25 if button == FIRE else 1)
                    for _ in range(35):
                        run.step({defender: (-32768, 0, held)})
                    self.assertEqual(fight.vitals[player.body].total, 150)
                    self.assertFalse(hog.client.messages(combat.HIT))
                    self.assertEqual(ammo.value(), 4)
                    self.assertFalse(fight.rockets)
                    if hero == 0x02E0000000000029:
                        self.assertTrue(all(rocket.shooter is defender for rocket in admitted))
                        self.assertLess(hog_health.total, 600)
                    else:
                        self.assertEqual(hog_health.total, 600)
                    if hero == sigma_grasp.HERO:
                        grasp = next(i for i in defender.body_script.component.instances.values()
                                     if i.graph.index == sigma_grasp.GRASP)
                        self.assertAlmostEqual(grasp.vars[21312].value(), expected, places=4)
    def test_actual_secondary_distinguishes_slug_and_fragments_at_range(self):
        cases = ((5, False, None, 50, 1), (10, False, None, 150, 23), (10, True, None, 0, 0),
                 (10, False, -0.01, 50, 1), (10, False, 0.01, 150, 23))
        for distance, blocked, offset, loss, hits in cases:
            with self.subTest(distance=distance, blocked=blocked, offset=offset):
                run, player, hog, _ = tracer_tests.TracerPistolTests().fight()
                fight = run.match.combat
                run.match.game_map = replace(run.match.game_map, free_for_all=False)
                player.team, hog.team = 0, 1
                self.assertTrue(run.match.switch_hero(hog, heroes()[0x02E0000000000040]))
                x, y, z = player.mover.position
                hog.mover.position[:] = (x, y, z - distance)
                hog.mover.body = None
                if blocked:
                    self.cover(run, player, z - 6)
                run.step({})
                angles = aim(fight._eye(hog), (x, y + 0.75, z))
                instances = hog.body_script.component.instances.values()
                weapon = next(instance for instance in instances if instance.graph.index == 0x01C6)
                target = next(t for t in fight._targets() if t.owner is player)
                health = fight.vitals_of(target)
                run.step({hog: (*angles, SECONDARY)})
                self.assertEqual(weapon.vars[53].value(), 4)
                slug, = fight.rockets
                self.assertEqual(len(slug.fragment_burst.rays), 25)
                if offset is not None:
                    contact = combat.ray_capsule(
                        slug.position, slug.direction, *target.segment(), target.radius,
                    )
                    self.assertIsNotNone(contact)
                    shift = slug.speed * slug.left + offset - contact
                    player.mover.position[:] = combat._along(target.feet, slug.direction, shift)
                    player.mover.body = None
                peak = 1
                for _ in range(90):
                    run.step({})
                    peak = max(peak, len(fight.rockets))
                self.assertEqual(health.total, 150 - loss)
                self.assertEqual(len(hog.client.messages(combat.HIT)), hits)
                # The remaining fraction of the split frame resolves 23 fragments immediately.
                self.assertEqual(peak, 2 if hits == 23 else 1)
                self.assertEqual(weapon.vars[53].value(), 4)
                self.assertFalse(fight.rockets)

    def test_actual_primary_death_miss_team_cover_and_tracer_return_fire(self):
        for gate in ("hit", "miss", "ally", "wall"):
            with self.subTest(gate=gate):
                run, player, hog, _ = tracer_tests.TracerPistolTests().fight()
                fight = run.match.combat
                run.match.game_map = replace(run.match.game_map, free_for_all=False)
                player.team, hog.team = 0, 0 if gate == "ally" else 1
                self.assertTrue(run.match.switch_hero(hog, heroes()[0x02E0000000000040]))
                x, y, z = player.mover.position
                hog.mover.position[:] = (x, y, z - 2)
                hog.mover.body = None
                if gate == "wall":
                    self.cover(run, player, z - 1)
                run.step({})
                hog_health = fight.vitals_of(next(t for t in fight._targets() if t.owner is hog))
                angles = aim(fight._eye(hog), (x, y + 0.75, z))
                reverse = aim(fight._eye(player), (x, y + 1, z - 2))
                if gate == "miss":
                    angles = -32768, 0
                instances = hog.body_script.component.instances.values()
                weapon = next(instance for instance in instances if instance.graph.index == 0x01C6)
                ammo = weapon.vars[53]
                self.assertEqual(ammo.value(), 5)
                run.step({hog: (*angles, FIRE), player: (*reverse, FIRE)})
                self.assertEqual(ammo.value(), 4)
                self.assertEqual(len(fight.rockets), 25)
                self.assertEqual(len({pellet.direction for pellet in fight.rockets}), 25)
                for _ in range(24):
                    run.step({})
                self.assertEqual(fight.vitals[player.body].total, 0 if gate == "hit" else 150)
                self.assertEqual(len(hog.client.messages(combat.HIT)), 23 if gate == "hit" else 0)
                self.assertEqual(hog_health.total, 588 if gate in ("hit", "miss") else 600)
                if gate == "hit":
                    self.assertEqual(fight.vitals[player.body].killer, hog.body)
