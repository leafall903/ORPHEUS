"""Real Symmetra attacks into authored personal and projected particle barriers."""

import os
import unittest
from unittest.mock import patch

from test_game_combat import Run, aim, in_world
from test_symmetra_combat import health
from test_symmetra_turrets import wall

from ow174.game import combat, projectile_tags, stats, symmetra, zarya_barriers
from ow174.game.collision import World
from ow174.game.commands import Command
from ow174.game.content import SOLDIER, heroes
from ow174.game.script import expr
from ow174.game.script.driver import ULT_COST

PHARAH = 0x02E0000000000008
BARRAGE_ROCKET = 0x0400000000000052


class SymmetraZaryaTests(unittest.TestCase):
    def scene(self, projected, *, close=False):
        run = Run(names=("Symmetra", "Zarya", "Ally"))
        owner, caster, ally = run.players
        run.match.bots.clear()
        run.match._pending_bot_scripts.clear()
        self.assertTrue(run.match.switch_hero(owner, heroes()[symmetra.HERO]))
        self.assertTrue(run.match.switch_hero(caster, heroes()[zarya_barriers.HERO]))
        caster.team = ally.team = 1
        owner.team = 0
        owner.mover.position[:] = (0, 1, 3.5 if close else 0)
        caster.mover.position[:] = (3 if projected else 0, 1, 5)
        ally.mover.position[:] = (0, 1, 5 if projected else 20)
        victim = ally if projected else caster
        owner.body_script.component.set(12918, 0)
        fight = run.match.combat

        def step(attack=0, defense=0, count=1):
            for _ in range(count):
                x, y, z = victim.mover.position
                angles = aim(fight._eye(owner), (x, y + 1, z))
                run.step({owner: (*angles, attack), caster: (-16384 if projected else 0, 0, defense)})

        index, pet = (0x025C, 10) if projected else (0x0244, 2)
        instance = next(i for i in caster.body_script.component.instances.values() if i.graph.index == index)
        return run, owner, caster, victim, step, instance, pet

    def test_actual_beam_orb_and_melee_feed_personal_and_projected_energy(self):
        for projected in (False, True):
            for button, amount in ((1, 9.6), (2, 120), (0x800, 30)):
                with self.subTest(projected=projected, button=button):
                    run, owner, caster, victim, step, instance, pet = self.scene(
                        projected, close=button == 0x800,
                    )
                    before = health(run, victim).total
                    if button == 2:
                        step(2, count=90)
                    step(2 if button == 2 else 0, 16 if projected else 8)
                    self.assertTrue(instance.state(pet).active)
                    step(button if button != 2 else 0, count=10 if button == 1 else 1)
                    step(count=22 if button == 2 else 2)
                    fight = run.match.combat
                    _, _, bubble = fight.zarya_barriers.contact(victim)
                    self.assertAlmostEqual(bubble.remaining, 200 - amount, places=4)
                    energy = caster.body_script.component.entity_var(87).value()
                    self.assertAlmostEqual(energy, amount * .2, places=4)
                    self.assertEqual(health(run, victim).total, before)
                    self.assertFalse(owner.client.messages(combat.HIT))
                    self.assertEqual(owner.stats.value(stats.HERO_DAMAGE), 0)
                    self.assertEqual(owner.body_script.component.entity_var(342).value(), 0)

    @unittest.skipIf(projectile_tags.CATALOG_SHA256 is None, "requires authored shield tags")
    def test_real_beam_recovers_ammo_on_both_bubbles_and_ends_on_expiry(self):
        for projected in (False, True):
            with self.subTest(projected=projected):
                run, owner, _, victim, step, instance, pet = self.scene(projected)
                weapon = next(i for i in owner.body_script.component.instances.values()
                              if i.graph.index == symmetra.WEAPON)
                ammo = weapon.find_var(0, 53)
                step(2, count=90)
                step(count=25)
                step(defense=16 if projected else 8)
                before_health = health(run, victim).total
                step(1, count=5)
                self.assertTrue(weapon.state(95).active)
                before = ammo.value()
                step(1, count=25)
                self.assertAlmostEqual(ammo.value() - before, 7 * 25 * .016, places=3)
                self.assertEqual(health(run, victim).total, before_health)
                step(count=125)
                self.assertFalse(instance.state(pet).active)
                self.assertFalse(weapon.state(95).active)
                before = ammo.value()
                step(1, count=15)
                self.assertLess(ammo.value(), before)
                self.assertLess(health(run, victim).total, before_health)

    def test_actual_orb_then_beam_exhausts_each_bubble_without_spill(self):
        for projected in (False, True):
            with self.subTest(projected=projected):
                run, owner, caster, victim, step, instance, pet = self.scene(projected)
                fight = run.match.combat
                health_before = health(run, victim).total
                step(2, count=90)
                step(2, 16 if projected else 8)
                step()
                for _ in range(20):
                    if not fight.rockets:
                        break
                    step()
                self.assertFalse(fight.rockets)
                _, _, bubble = fight.zarya_barriers.contact(victim)
                self.assertAlmostEqual(bubble.remaining, 80, places=4)
                for _ in range(100):
                    if bubble.remaining <= 0:
                        break
                    step(1)
                self.assertEqual(bubble.remaining, 0)
                self.assertIsNone(fight.zarya_barriers.contact(victim))
                self.assertEqual(health(run, victim).total, health_before)
                self.assertFalse(owner.client.messages(combat.HIT))
                step(1, count=3)
                self.assertFalse(instance.state(pet).active)
                self.assertAlmostEqual(caster.body_script.component.entity_var(87).value(), 40, places=4)
                self.assertLess(health(run, victim).total, health_before)

    def test_actual_orb_respects_particle_barrier_expiry_across_retained_flight(self):
        for projected in (False, True):
            for before_expiry in (True, False):
                with self.subTest(projected=projected, before_expiry=before_expiry):
                    stepped = self.particle_expiry_orb_impact(projected, before_expiry, gap=False)
                    retained = self.particle_expiry_orb_impact(projected, before_expiry, gap=True)
                    self.assertEqual(retained, stepped)

    def particle_expiry_orb_impact(self, projected, before_expiry, *, gap):
        run, owner, caster, victim, step, instance, pet = self.scene(projected)
        fight = run.match.combat
        vitals = health(run, victim)
        before = vitals.total
        step(defense=16 if projected else 8)
        cast_frame = run.frame
        _, _, bubble = fight.zarya_barriers.contact(victim)
        release = 112 if before_expiry else 118
        step(count=release - 91)
        step(2, count=90)
        step()
        step()
        [orb] = fight.rockets
        self.assertIs(orb.shooter, owner)
        self.assertEqual(orb.last_tick, run.frame)
        self.assertTrue(instance.state(pet).active)

        impacts = []
        resolve = fight._damage

        def trace(shooter, target, amount, *args, **kwargs):
            applied = resolve(shooter, target, amount, *args, **kwargs)
            if target.owner is victim:
                impacts.append((fight.tick - cast_frame, amount, applied))
            return applied

        end_frame = cast_frame + 143
        with patch.object(fight, "_damage", side_effect=trace):
            if gap:
                run.frame = end_frame
                run.match.update(run.frame * combat.TICK, run.frame)
            else:
                step(count=end_frame - run.frame)
        self.assertFalse(fight.rockets)
        self.assertFalse(instance.state(pet).active)
        self.assertIsNone(fight.zarya_barriers.contact(victim))
        impact_tick = 124 if before_expiry else 130
        self.assertEqual(impacts, [(impact_tick, 60, not before_expiry)] * 2)
        self.assertEqual(vitals.total, before if before_expiry else before - 120)
        self.assertEqual(bubble.remaining, 80 if before_expiry else 200)
        energy = caster.body_script.component.entity_var(87).value() or 0
        self.assertEqual(energy > 0, before_expiry)
        return impacts

    def test_helix_direct_and_splash_resolve_against_one_particle_barrier(self):
        for projected in (False, True):
            for remaining in (30, 40, 60, 120, 200):
                with self.subTest(projected=projected, remaining=remaining):
                    self.check_helix_against_particle_barrier(projected, remaining)

    def check_helix_against_particle_barrier(self, projected, remaining):
        run, soldier, caster, victim, step, instance, pet = self.scene(projected)
        self.assertTrue(run.match.switch_hero(soldier, heroes()[SOLDIER]))
        soldier.mover.position[:] = (0, 1, 0)
        soldier.team = 0
        fight = run.match.combat
        if projected:
            caster.mover.position[:] = (4, 1, 5)
            self.assertTrue(run.match.switch_hero(victim, heroes()[symmetra.HERO]))
            victim.mover.position[:] = (0, 1, 5)
            victim.team = caster.team
        step(defense=16 if projected else 8)
        self.assertTrue(instance.state(pet).active)
        source, _, bubble = fight.zarya_barriers.contact(victim)
        self.assertIs(source, caster)
        self.assertEqual(bubble.remaining, 200)
        bubble.remaining = remaining
        if projected:
            bystander = run.match.add_player(4, "Splash target", SOLDIER, 0, False)
            in_world(run.match, bystander)
            run.players.append(bystander)
            self.assertTrue(run.match.switch_hero(bystander, heroes()[SOLDIER]))
        else:
            bystander = run.players[2]
        bystander.team = victim.team
        bystander.mover.position[:] = (.9, 1, 5)
        bystander.mover.body = None
        before = health(run, victim).total
        exposed_before = health(run, bystander).total
        step(2)
        self.assertTrue(any(r.shooter is soldier for r in fight.rockets))
        for _ in range(20):
            step()
        self.assertFalse(any(r.shooter is soldier for r in fight.rockets))
        self.assertEqual(health(run, victim).total, before)
        self.assertAlmostEqual(exposed_before - health(run, bystander).total, 80)
        self.assertAlmostEqual(soldier.stats.value(stats.HERO_DAMAGE), 80)
        self.assertEqual(bubble.remaining, max(0, remaining - 120))
        self.assertEqual(instance.state(pet).active, remaining > 120)
        self.assertAlmostEqual(caster.body_script.component.entity_var(87).value(),
                               min(120, remaining) * .2)
        if remaining <= 120:
            step(1, count=50)
            self.assertLess(health(run, victim).total, before)

    def pharah_particle_scene(self, projected):
        run, pharah, caster, victim, step, instance, pet = self.scene(projected)
        self.assertTrue(run.match.switch_hero(pharah, heroes()[PHARAH]))
        pharah.mover.position[:] = (0, 1, 0)
        if projected:
            caster.mover.position[:] = (4, 1, 5)
            self.assertTrue(run.match.switch_hero(victim, heroes()[symmetra.HERO]))
            victim.mover.position[:] = (0, 1, 5)
            victim.team = caster.team
        step(count=40)
        step(defense=16 if projected else 8)
        self.assertTrue(instance.state(pet).active)
        fight = run.match.combat
        source, _, bubble = fight.zarya_barriers.contact(victim)
        self.assertIs(source, caster)
        self.assertEqual(bubble.remaining, 200)
        return run, pharah, caster, victim, bubble, step, instance, pet

    def launch_barrage(self, run, pharah):
        script = pharah.body_script
        script.ult_seconds = 0
        script.set_ult_charge(script.component.entity_var(ULT_COST).value())
        fight = run.match.combat
        angles = aim(fight._eye(pharah), (0, 1.65, 5))
        run.step({pharah: (*angles, 32)})
        [rocket] = fight.rockets
        self.assertIs(rocket.shooter, pharah)
        self.assertEqual(rocket.definition, BARRAGE_ROCKET)
        self.assertEqual(len(rocket.motion), 2)
        return rocket

    def resolve_barrage_round(self, run, caster, victim, rocket, *, gap):
        fight = run.match.combat
        resolve = fight._damage
        impacts = []

        def observe(shooter, target, amount, *args, **kwargs):
            applied = resolve(shooter, target, amount, *args, **kwargs)
            if target.owner is victim:
                impacts.append((fight.tick, amount, applied))
            return applied

        first, end = run.frame + 1, run.frame + 13
        with patch.object(fight, "_damage", side_effect=observe):
            run.match.update(first * combat.TICK, first)
            self.assertEqual(rocket.last_tick, first)
            for tick in (end,) if gap else range(first + 1, end + 1):
                run.match.update(tick * combat.TICK, tick)
        self.assertFalse(fight.rockets)
        # Deliver the caster's authored energy and barrier-finish work.
        caster.take_commands([Command(end + 1)])
        return impacts

    def test_actual_curved_barrage_direct_and_splash_resolve_against_one_particle_barrier(self):
        for projected in (False, True):
            for remaining in (5, 25, 40, 200):
                for gap in (False, True):
                    with self.subTest(projected=projected, remaining=remaining, gap=gap):
                        scene = self.pharah_particle_scene(projected)
                        run, pharah, caster, victim, bubble, _, instance, pet = scene
                        bubble.remaining = remaining
                        before = health(run, victim).total
                        rocket = self.launch_barrage(run, pharah)
                        impacts = self.resolve_barrage_round(run, caster, victim, rocket, gap=gap)
                        amounts = [10] if remaining <= 10 else [10, 30]
                        self.assertEqual(impacts, [(run.frame + 7, amount, False) for amount in amounts])
                        self.assertEqual(health(run, victim).total, before)
                        self.assertEqual(bubble.remaining, max(0, remaining - 40))
                        self.assertEqual(instance.state(pet).active, remaining > 40)
                        self.assertAlmostEqual(caster.body_script.component.entity_var(87).value(),
                                               min(remaining, 40) * .2)
                        self.assertEqual(pharah.stats.value(stats.HERO_DAMAGE), 0)

    def test_actual_curved_barrage_respects_particle_barrier_expiry_in_retained_flight(self):
        for projected in (False, True):
            for impact_offset in (124, 130):
                for gap in (False, True):
                    with self.subTest(projected=projected, impact_offset=impact_offset, gap=gap):
                        scene = self.pharah_particle_scene(projected)
                        run, pharah, caster, victim, bubble, step, instance, pet = scene
                        cast = run.frame
                        before = health(run, victim).total
                        step(count=impact_offset - 8)
                        rocket = self.launch_barrage(run, pharah)
                        impacts = self.resolve_barrage_round(run, caster, victim, rocket, gap=gap)
                        protected = impact_offset == 124
                        self.assertEqual(impacts, [(cast + impact_offset, amount, not protected)
                                                   for amount in (10, 30)])
                        self.assertEqual(health(run, victim).total, before if protected else before - 40)
                        self.assertEqual(bubble.remaining, 160 if protected else 200)
                        self.assertFalse(instance.state(pet).active)

    def test_actual_curved_barrage_cannot_spend_two_overlapping_particle_barriers(self):
        for remaining in (5, 25):
            with self.subTest(remaining=remaining):
                run, pharah, victim, _, personal, _, instance, pet = self.pharah_particle_scene(False)
                fight = run.match.combat
                caster = run.players[2]
                self.assertTrue(run.match.switch_hero(caster, heroes()[zarya_barriers.HERO]))
                caster.team = victim.team
                caster.mover.position[:] = (4, 1, 5)
                angle = aim(fight._eye(caster), fight._eye(victim))
                run.step({caster: (*angle, 16)})
                projected_graph = next(i for i in caster.body_script.component.instances.values()
                                       if i.graph.index == 0x025C)
                self.assertTrue(projected_graph.state(10).active)
                self.assertEqual(projected_graph.find_var(0, 536).value(), expr.Entity(victim.body))
                projected = fight.zarya_barriers._active(caster, projected=True, entity=victim.body)[2]
                personal.remaining = remaining
                before = health(run, victim).total

                rocket = self.launch_barrage(run, pharah)
                impacts = self.resolve_barrage_round(run, victim, victim, rocket, gap=True)
                amounts = [10] if remaining == 5 else [10, 30]
                self.assertEqual(impacts, [(run.frame + 7, amount, False) for amount in amounts])
                self.assertEqual(health(run, victim).total, before)
                self.assertEqual(personal.remaining, 0)
                self.assertFalse(instance.state(pet).active)
                self.assertEqual(projected.remaining, 200)
                self.assertIs(fight.zarya_barriers.contact(victim)[0], caster)
                self.assertEqual(caster.body_script.component.entity_var(87).value() or 0, 0)

    def test_actual_projected_barrier_blocks_concussion_without_spending_pool(self):
        for protected in (False, True):
            with self.subTest(protected=protected):
                run, pharah, caster, victim, bubble, step, instance, pet = self.pharah_particle_scene(True)
                if not protected:
                    step(count=130)
                self.assertEqual(instance.state(pet).active, protected)
                fight = run.match.combat
                before = tuple(victim.mover.position)
                angles = aim(fight._eye(pharah), (0, 2, 5))
                for _ in range(12):
                    run.step({pharah: (*angles, 16)})
                    if fight.rockets:
                        break
                [rocket] = fight.rockets
                self.assertEqual(rocket.definition, 0x040000000000010F)
                for _ in range(35):
                    run.step({})
                self.assertNotIn(rocket, fight.rockets)
                self.assertEqual((health(run, victim).total, bubble.remaining), (225, 200))
                self.assertEqual(caster.body_script.component.entity_var(87).value() or 0, 0)
                if protected:
                    self.assertEqual(tuple(victim.mover.position), before)
                else:
                    self.assertGreater(victim.mover.position[2], before[2] + 2)

    def test_projected_cast_requires_ally_and_clear_static_sight(self):
        for cover in (False, True):
            with self.subTest(cover=cover):
                run, owner, caster, victim, step, instance, pet = self.scene(True)
                if cover:
                    divider = World([(1.5, 0, 3, 1.5, 5, 3, 1.5, 5, 7),
                                     (1.5, 0, 3, 1.5, 5, 7, 1.5, 0, 7)])
                    with patch.object(run.match.combat, "_collision", return_value=divider):
                        step(defense=16)
                else:
                    victim.team = owner.team
                    step(defense=16)
                self.assertFalse(instance.state(pet).active)
                self.assertIsNone(run.match.combat.zarya_barriers.contact(victim))
                self.assertFalse(caster.body_script.component.entity_var(87).value())

    def test_overlapping_particle_barriers_resolve_one_layer_per_attack(self):
        for helix in (False, True):
            for projected_first in (False, True):
                with self.subTest(helix=helix, projected_first=projected_first):
                    self.check_overlapping_particle_barriers(helix, projected_first)

    def check_overlapping_particle_barriers(self, helix, projected_first):
        run, attacker, victim, _, _, personal, _ = self.scene(False)
        caster = run.players[2]
        if helix:
            self.assertTrue(run.match.switch_hero(attacker, heroes()[SOLDIER]))
        self.assertTrue(run.match.switch_hero(caster, heroes()[zarya_barriers.HERO]))
        attacker.team, caster.team = 0, victim.team
        attacker.mover.position[:] = (0, 1, 0)
        caster.mover.position[:] = (4, 1, 5)
        fight = run.match.combat
        attack_aim = aim(fight._eye(attacker), (0, 2, 5))
        projected_aim = aim(fight._eye(caster), fight._eye(victim))
        projected = next(i for i in caster.body_script.component.instances.values()
                         if i.graph.index == 0x025C)
        own_energy = victim.body_script.component.entity_var(87)
        ally_energy = caster.body_script.component.entity_var(87)

        def step(attack=0, own_cast=0, ally_cast=0):
            run.step({attacker: (*attack_aim, attack), victim: (0, 0, own_cast),
                      caster: (*projected_aim, ally_cast)})

        held = 0 if helix else 2
        for _ in range(90 if held else 1):
            step(held)
        if projected_first:
            step(held, ally_cast=16)
            step(held, own_cast=8)
        else:
            step(held, own_cast=8)
            step(held, ally_cast=16)
        self.assertTrue(personal.state(2).active)
        self.assertTrue(projected.state(10).active)
        self.assertEqual(projected.find_var(0, 536).value(), expr.Entity(victim.body))
        source, _, bubble = fight.zarya_barriers.contact(victim)
        self.assertIs(source, victim)
        self.assertEqual(bubble.remaining, 200)
        bubble.remaining = 30
        before = health(run, victim).total
        step(2 if helix else 0)
        self.assertTrue(any(r.shooter is attacker for r in fight.rockets))
        for _ in range(20):
            step()
        self.assertFalse(any(r.shooter is attacker for r in fight.rockets))
        self.assertEqual(health(run, victim).total, before)
        self.assertFalse(personal.state(2).active)
        self.assertEqual(bubble.remaining, 0)
        self.assertAlmostEqual(own_energy.value(), 6)
        self.assertAlmostEqual(ally_energy.value() or 0, 0)
        source, _, bubble = fight.zarya_barriers.contact(victim)
        self.assertIs(source, caster)
        self.assertEqual(bubble.remaining, 200)

        # Shorten the remaining pool to isolate a breaking hit from expiry.
        bubble.remaining = 80
        for _ in range(120):
            step(1)
            self.assertEqual(health(run, victim).total, before)
            if not bubble.remaining:
                break
        self.assertEqual(bubble.remaining, 0)
        self.assertIsNone(fight.zarya_barriers.contact(victim))
        for _ in range(12):
            step(1)
        self.assertFalse(projected.state(10).active)
        self.assertAlmostEqual(ally_energy.value(), 16, places=4)
        self.assertLess(health(run, victim).total, before)

    def test_two_projectors_handoff_after_break_expiry_or_caster_loss(self):
        for ending, reverse_cast in (("break", False), ("break", True),
                                     ("expiry", False), ("switch", False), ("death", False)):
            with self.subTest(ending=ending, reverse_cast=reverse_cast):
                self.check_two_projectors_handoff(ending, reverse_cast)

    def check_two_projectors_handoff(self, ending, reverse_cast):
        run = Run(names=("Symmetra", "First Zarya", "Second Zarya", "Ally"))
        attacker, first, second, victim = run.players
        run.match.bots.clear()
        run.match._pending_bot_scripts.clear()
        self.assertTrue(run.match.switch_hero(attacker, heroes()[symmetra.HERO]))
        for caster in (first, second):
            self.assertTrue(run.match.switch_hero(caster, heroes()[zarya_barriers.HERO]))
        attacker.team = 0
        first.team = second.team = victim.team = 1
        attacker.mover.position[:] = (0, 1, 3.5)
        first.mover.position[:] = (4, 1, 5)
        second.mover.position[:] = (-4, 1, 5)
        victim.mover.position[:] = (0, 1, 5)
        attacker.body_script.component.set(12918, 0)
        fight = run.match.combat
        attack_aim = aim(fight._eye(attacker), (0, 2, 5))
        cast_aims = {caster: aim(fight._eye(caster), fight._eye(victim))
                     for caster in (first, second)}
        instances = [next(i for i in caster.body_script.component.instances.values()
                          if i.graph.index == 0x025C) for caster in (first, second)]
        first_energy, second_energy = (caster.body_script.component.entity_var(87)
                                       for caster in (first, second))

        def step(attack=0, cast=None, angles=attack_aim):
            commands = {caster: (*cast_aim, 16 if caster is cast else 0)
                        for caster, cast_aim in cast_aims.items()}
            run.step({**commands, attacker: (*angles, attack)})

        for _ in range(40 if ending == "expiry" else 90):
            step(2)
        early, late = (second, first) if reverse_cast else (first, second)
        step(2, cast=early)
        if ending == "expiry":
            for _ in range(50):
                step(2)
        step(2, cast=late)
        for instance in instances:
            self.assertTrue(instance.state(10).active)
            self.assertEqual(instance.find_var(0, 536).value(), expr.Entity(victim.body))
        source, _, first_barrier = fight.zarya_barriers.contact(victim)
        self.assertIs(source, first)
        self.assertEqual(first_barrier.remaining, 200)
        if ending == "break":
            first_barrier.remaining = 30

        body_pools = health(run, victim)
        before = body_pools.total
        step()
        self.assertTrue(any(rocket.shooter is attacker for rocket in fight.rockets))
        for _ in range(10):
            step()
        self.assertFalse(any(rocket.shooter is attacker for rocket in fight.rockets))
        self.assertEqual(body_pools.total, before)
        self.assertEqual(first_barrier.remaining, 0 if ending == "break" else 80)
        self.assertAlmostEqual(first_energy.value(), 6 if ending == "break" else 24)
        self.assertEqual(second_energy.value() or 0, 0)
        self.assertEqual(attacker.stats.value(stats.HERO_DAMAGE), 0)
        if ending == "expiry":
            for _ in range(125):
                if not instances[0].state(10).active:
                    break
                step()
            self.assertFalse(instances[0].state(10).active)
            self.assertEqual(first_barrier.remaining, 80)
        elif ending == "switch":
            self.assertTrue(run.match.switch_hero(first, heroes()[SOLDIER]))
        elif ending == "death":
            source_pools = health(run, first)
            source_pools.now[combat.HEALTH_POOL] = 5
            source_pools.now[combat.SHIELDS_POOL] = 0
            angles = aim(fight._eye(attacker), (4, 2, 5))
            for _ in range(20):
                step(1, angles=angles)
                if source_pools.dead:
                    break
            self.assertTrue(source_pools.dead)
            self.assertEqual(source_pools.killer, attacker.body)

        source, _, second_barrier = fight.zarya_barriers.contact(victim)
        self.assertIs(source, second)
        self.assertEqual(second_barrier.remaining, 200)
        damage_before = attacker.stats.value(stats.HERO_DAMAGE)
        for _ in range(14):
            step(0x800)
        self.assertEqual(body_pools.total, before)
        self.assertEqual(second_barrier.remaining, 170)
        self.assertAlmostEqual(second_energy.value(), 6)
        self.assertEqual(attacker.stats.value(stats.HERO_DAMAGE), damage_before)

    @unittest.skipUnless(os.environ.get("ORPHEUS_EXTRA_GRAPH_BUNDLES"), "requires the summon graph shard")
    def test_projected_barrier_on_allied_sentry_absorbs_hostile_primary(self):
        run, owner, caster, enemy, _, instance, pet = self.scene(True)
        fight = run.match.combat
        owner.team = caster.team
        enemy.team = 1 - caster.team
        enemy.mover.position[:] = (0, 1, 6)
        with patch.object(fight, "_collision", return_value=wall()):
            for _ in range(40):
                run.step({owner: (0, 0, 8)})
        [sentry] = fight.sentries.sentries
        self.assertIsNotNone(sentry.attached_at)
        x, y, z = sentry.bot.position
        caster.mover.position[:] = (x + 3, 1, z)
        caster.mover.body = None
        projected_aim = aim(fight._eye(caster), (x, y + combat.EYE, z))
        run.step({caster: (*projected_aim, 0)})
        run.step({caster: (*projected_aim, 16)})
        self.assertTrue(instance.state(pet).active)
        self.assertEqual(instance.find_var(0, 536).value(), expr.Entity(sentry.bot.entity))
        source, _, bubble = fight.zarya_barriers.contact(sentry.bot)
        self.assertIs(source, caster)
        self.assertEqual(bubble.remaining, 200)
        pools = health(run, sentry.bot)
        self.assertEqual(pools.total, 30)
        shot_aim = aim(fight._eye(enemy), (x, y + .5, z))
        for _ in range(120):
            run.step({enemy: (*shot_aim, 1)})
            self.assertEqual(pools.total, 30)
            if not bubble.remaining:
                break
        self.assertEqual(bubble.remaining, 0)
        self.assertIn(sentry, fight.sentries.sentries)
        run.step({})
        self.assertAlmostEqual(caster.body_script.component.entity_var(87).value(), 40, places=4)
        for _ in range(25):
            run.step({enemy: (*shot_aim, 1)})
        self.assertNotIn(sentry, fight.sentries.sentries)

    @unittest.skipUnless(os.environ.get("ORPHEUS_EXTRA_GRAPH_BUNDLES"), "requires the summon graph shard")
    def test_airborne_sentry_attaches_and_boots_across_retained_particle_barrier_expiry(self):
        for projected in (False, True):
            for boot_tick in (120, 125, 126):
                with self.subTest(projected=projected, boot_tick=boot_tick):
                    stepped = self.airborne_sentry_expiry_trace(projected, boot_tick, gap=False)
                    retained = self.airborne_sentry_expiry_trace(projected, boot_tick, gap=True)
                    self.assertEqual(retained, stepped)

    def airborne_sentry_expiry_trace(self, projected, boot_tick, *, gap):
        run, owner, caster, victim, step, instance, pet = self.scene(projected)
        fight = run.match.combat
        victim.mover.position[:] = (2, 1, 1)
        victim.mover.body = None
        if projected:
            caster.mover.position[:] = (5, 1, 1)
            caster.mover.body = None
        vitals = health(run, victim)
        initial_total = vitals.total
        step(defense=16 if projected else 8)
        cast_frame = run.frame
        _, _, bubble = fight.zarya_barriers.contact(victim)
        step(count=boot_tick - 60)
        with patch.object(fight, "_collision", return_value=wall()):
            run.step({owner: (0, 0, 8)})
            for _ in range(15):
                run.step({})
            [sentry] = fight.sentries.sentries
            self.assertIsNone(sentry.bot.moved_tick)
            run.step({})
            self.assertEqual(sentry.bot.moved_tick, cast_frame + boot_tick - 43)
            self.assertIsNone(sentry.attached_at)
            self.assertIsNone(sentry.target)
            self.assertEqual(bubble.remaining, 200)

            hits = []
            resolve = fight._damage

            def trace(shooter, target, amount, *args, **kwargs):
                applied = resolve(shooter, target, amount, *args, **kwargs)
                if shooter is owner and target.owner is victim:
                    hits.append((fight.tick - cast_frame, round(amount, 6), applied, sentry.slow is not None))
                return applied

            remaining = cast_frame + 132 - run.frame
            with patch.object(fight, "_damage", side_effect=trace):
                if gap:
                    run.frame += remaining
                    run.match.update(run.frame * combat.TICK, run.frame)
                else:
                    step(count=remaining)
        self.assertAlmostEqual(sentry.attached_at, (cast_frame + boot_tick - 31.5) * combat.TICK)
        self.assertAlmostEqual(sentry.bot.position[2], 2.99)
        self.assertIsNone(sentry.support_pillar)
        expected = [
            (tick, .16 if tick == boot_tick else .64, tick >= 125, tick >= 125)
            for tick in range(boot_tick, 133)
        ]
        self.assertEqual(hits, expected)
        self.assertAlmostEqual(bubble.remaining, 200 - sum(amount for _, amount, applied, _ in expected
                                                           if not applied))
        self.assertAlmostEqual(vitals.total, initial_total - sum(amount for _, amount, applied, _ in expected
                                                               if applied))
        self.assertFalse(instance.state(pet).active)
        self.assertIsNotNone(sentry.slow)
        return hits

    @unittest.skipUnless(os.environ.get("ORPHEUS_EXTRA_GRAPH_BUNDLES"), "requires the summon graph shard")
    def test_new_sentry_starts_at_endpoint_during_older_flight_and_particle_cover(self):
        run, owner, _, victim, step, _, _ = self.scene(False)
        other = run.match.add_player(4, "Second Symmetra", SOLDIER, owner.team, False)
        in_world(run.match, other)
        run.players.append(other)
        self.assertTrue(run.match.switch_hero(other, heroes()[symmetra.HERO]))
        other.mover.position[:] = (-1, 1, 0)
        fight = run.match.combat
        step(defense=8)
        with patch.object(fight, "_collision", return_value=wall()):
            for frame in range(40):
                run.step({owner: (0, 0, 8), other: (0, 0, 8 if frame >= 2 else 0)})
                if len(fight.sentries.sentries) == 2:
                    break
            old, new = fight.sentries.sentries
            self.assertIs(old.owner, owner)
            self.assertIs(new.owner, other)
            self.assertIsNone(old.attached_at)
            self.assertIsNotNone(old.bot.moved_tick)
            self.assertIsNone(new.bot.moved_tick)
            before = new.bot.position[2]
            run.frame += 16
            run.match.update(run.frame * combat.TICK, run.frame)
        self.assertAlmostEqual(old.bot.position[2], 2.99)
        self.assertIsNotNone(old.attached_at)
        self.assertEqual(new.bot.moved_tick, run.frame)
        self.assertAlmostEqual(new.bot.position[2] - before, new.speed * combat.TICK)
        self.assertIsNone(new.attached_at)
        self.assertEqual(fight.zarya_barriers.contact(victim)[2].remaining, 200)

    @unittest.skipUnless(os.environ.get("ORPHEUS_EXTRA_GRAPH_BUNDLES"), "requires the summon graph shard")
    def test_deployed_sentry_switches_from_barrier_to_body_on_retained_expiry_tick(self):
        for projected in (False, True):
            with self.subTest(projected=projected):
                stepped = self.particle_expiry_sentry_trace(projected, gap=False)
                self.assertEqual(self.particle_expiry_sentry_trace(projected, gap=True), stepped)

    def particle_expiry_sentry_trace(self, projected, *, gap):
        run, owner, caster, victim, step, instance, pet = self.scene(projected)
        fight = run.match.combat
        victim.mover.position[:] = (2, 1, 1)
        victim.mover.body = None
        if projected:
            caster.mover.position[:] = (5, 1, 1)
            caster.mover.body = None
        vitals = health(run, victim)
        initial_total = vitals.total
        with patch.object(fight, "_collision", return_value=wall()):
            for _ in range(40):
                run.step({owner: (0, 0, 8)})
            [sentry] = fight.sentries.sentries
            self.assertIsNotNone(sentry.attached_at)
            step(defense=16 if projected else 8)
            _, _, bubble = fight.zarya_barriers.contact(victim)
            step(count=110)
            self.assertGreater(bubble.remaining, 30 * .64)
            self.assertLess(bubble.remaining, 200)
            self.assertEqual(vitals.total, initial_total)
            self.assertIsNone(sentry.slow)
            shield_at_gap = bubble.remaining
            resolve = fight._damage
            hits = []

            def trace(shooter, target, amount, *args, **kwargs):
                applied = resolve(shooter, target, amount, *args, **kwargs)
                if shooter is owner and target.owner is victim:
                    hits.append((fight.tick, round(amount, 6), applied, sentry.slow is not None))
                return applied

            with patch.object(fight, "_damage", side_effect=trace):
                if gap:
                    run.frame += 30
                    run.match.update(run.frame * combat.TICK, run.frame)
                else:
                    step(count=30)
            self.assertFalse(instance.state(pet).active)
            self.assertIsNone(fight.zarya_barriers.contact(victim))
            self.assertIsNotNone(sentry.slow)
            self.assertEqual(len(hits), 30)
            self.assertTrue(all(amount == .64 and applied == slow for _, amount, applied, slow in hits))
            blocked = sum(not applied for _, _, applied, _ in hits)
            self.assertEqual(blocked, 14)
            self.assertEqual([hit[2] for hit in hits], [False] * blocked + [True] * (30 - blocked))
            self.assertAlmostEqual(bubble.remaining, shield_at_gap - .64 * blocked)
            self.assertAlmostEqual(vitals.total, initial_total - .64 * (30 - blocked))
            return hits

    @unittest.skipUnless(os.environ.get("ORPHEUS_EXTRA_GRAPH_BUNDLES"), "requires the summon graph shard")
    def test_sentry_feeds_energy_without_slow_then_applies_slow_after_expiry(self):
        run, owner, caster, victim, step, instance, pet = self.scene(False)
        fight = run.match.combat
        caster.mover.position[:] = (2, 1, 1)
        caster.mover.body = None
        before = health(run, victim).total
        with patch.object(fight, "_collision", return_value=wall()):
            for _ in range(40):
                run.step({owner: (0, 0, 8)})
            [sentry] = fight.sentries.sentries
            self.assertIsNotNone(sentry.attached_at)
            step(defense=8)
            self.assertTrue(instance.state(pet).active)
            step(count=60)
            self.assertEqual(sentry.target, caster.body)
            self.assertIsNone(sentry.slow)
            self.assertEqual(health(run, victim).total, before)
            self.assertGreater(caster.body_script.component.entity_var(87).value(), 0)
            step(count=80)
            self.assertFalse(instance.state(pet).active)
            self.assertIsNotNone(sentry.slow)
            self.assertLess(health(run, victim).total, before)
