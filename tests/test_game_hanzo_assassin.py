"""Actual manual Assassin1157 contact, control and authored damage against Hanzo."""

import math
import unittest
from dataclasses import replace
from unittest.mock import patch

import test_game_hanzo_dragons as dragon_tests
import test_game_hanzo_npc as fixture
import test_game_hanzo_sonic as sonic_tests
from test_game_combat import aim, in_world
from test_game_mover import box, wall

from ow174.game import assassin, combat, hanzo_control, npc, npc_control, sonic
from ow174.game.body_vm_snapshot import export_component_vm, snapshot_digest
from ow174.game.collision import World
from ow174.game.commands import Command, JUMP
from ow174.game.content import SOLDIER, heroes
from ow174.game.script import expr


class HanzoAssassinTests(unittest.TestCase):
    setUpClass = classmethod(fixture.HanzoCatalogNpcTests.setUpClass.__func__)
    tearDownClass = classmethod(fixture.HanzoCatalogNpcTests.tearDownClass.__func__)
    setUp = fixture.HanzoCatalogNpcTests.setUp
    arena = fixture.HanzoCatalogNpcTests.arena
    deploy_orisa_cover = fixture.HanzoCatalogNpcTests.deploy_orisa_cover
    talon_projected_arena = fixture.HanzoCatalogNpcTests.talon_projected_arena
    talon_reinhardt_arena = fixture.HanzoCatalogNpcTests.talon_reinhardt_arena
    target_aim = staticmethod(fixture.HanzoCatalogNpcTests.target_aim)

    def scene(self, distance=2.5, situation='hit'):
        run, hero, actor = self.arena(self.catalog['4'], 'bow', situation, distance=distance)
        self.addCleanup(npc.remove_spawned, run.match)
        target = next(t for t in run.match.combat._targets() if t.owner is hero)
        return run, hero, actor, run.match.combat.vitals_of(target)

    def step(self, run, hero, actor, button=0):
        yaw, pitch = self.target_aim(run, hero, actor)
        run.step({hero: (yaw, pitch, button)})

    def begin(self, run, hero, actor, held=0):
        self.assertEqual(npc_control.start(run.match, hero), actor.entity)
        fight = run.match.combat
        for _ in range(80):
            self.step(run, hero, actor, held)
            if fight.pins.get(actor.entity) == hero.body:
                return
        self.fail('1157 did not enter manual contact')

    @staticmethod
    def pin_instance(actor):
        return next(i for i in actor.body_script.component.instances.values()
                    if i.graph.index == assassin.GRAPH)

    def test_fortified_orisa_takes_contact_damage_without_assassin_pin(self):
        run, orisa, actor, _ = self.scene()
        match = run.match
        fight = match.combat
        position = tuple(orisa.mover.position)
        self.assertTrue(match.switch_hero(orisa, heroes()[0x02E000000000013E]))
        orisa.mover.position[:] = position
        orisa.mover.body = None
        for _ in range(25):
            self.step(run, orisa, actor)
        target = next(t for t in fight._targets() if t.owner is orisa)
        pool = fight.vitals_of(target)
        self.step(run, orisa, actor, 8)
        self.assertIsNotNone(combat._active_fortify(orisa))
        self.assertEqual(npc_control.start(match, orisa), actor.entity)
        for _ in range(80):
            self.step(run, orisa, actor)
            if pool.total < 450:
                break
        self.assertAlmostEqual(pool.total, 440, places=4)
        self.assertNotIn(actor.entity, fight.pins)
        self.assertFalse(fight.is_stunned(orisa))
        for _ in range(120):
            self.step(run, orisa, actor)
            self.assertNotIn(actor.entity, fight.pins)
        self.assertIsNotNone(combat._active_fortify(orisa))
        self.assertAlmostEqual(pool.total, 440, places=4)
        self.assertEqual(npc_control.stop(match), 1)
        for _ in range(260):
            if combat._active_fortify(orisa) is None:
                break
            self.step(run, orisa, actor)
        self.assertIsNone(combat._active_fortify(orisa))
        self.begin(run, orisa, actor)
        self.assertAlmostEqual(pool.total, 420, places=4)

    def test_actual_contact_and_rate_then_stop_preserve_raw_ai_slot(self):
        run, hero, actor, pool = self.scene()
        self.step(run, hero, actor)
        self.begin(run, hero, actor)
        component = actor.body_script.component
        self.assertEqual(component.world.ai_weapon_button(0), 20)
        self.assertEqual(npc_control.button(actor), 36)
        instance = self.pin_instance(actor)
        self.assertEqual(instance.vars[536].value(), expr.Entity(hero.body))
        self.assertEqual(pool.total, 175)
        phase = None
        delayed = 0
        for _ in range(200):
            self.step(run, hero, actor)
            phase = instance.states.get(instance.graph.node(15).state)
            if phase is not None and phase.active:
                break
            delayed += 1
            self.assertEqual(pool.total, 175)
        self.assertGreater(delayed, 0)
        self.assertIsNotNone(phase)
        self.assertTrue(phase.active)
        before = pool.total
        for _ in range(10):
            self.step(run, hero, actor)
        self.assertAlmostEqual(before - pool.total, 16, places=4)
        saved = snapshot_digest(export_component_vm(component))
        last = phase.last_applied
        phase.last_applied -= 1
        self.assertNotEqual(snapshot_digest(export_component_vm(component)), saved)
        phase.last_applied = last
        self.assertEqual(npc_control.stop(run.match), 1)
        self.assertFalse(run.match.combat.pins)
        self.assertFalse(run.match.combat.is_stunned(hero))
        before = pool.total
        for _ in range(50):
            self.step(run, hero, actor)
        self.assertEqual(pool.total, before)

    def test_projected_barrier_absorbs_assassin_entry_and_preserves_hanzo_draw(self):
        for shield_hp in (200, 15):
            with self.subTest(shield=shield_hp):
                run, hero, actor, caster, cast = self.talon_projected_arena("4", 2.5, start=False)
                fight = run.match.combat
                barriers = fight.zarya_barriers
                target = next(t for t in fight._targets() if t.owner is hero)
                pool = fight.vitals_of(target)
                actor_pool = fight.vitals[actor.entity]
                before = actor_pool.total
                for _ in range(70):
                    self.step(run, hero, actor, 1)
                angles = self.target_aim(run, hero, actor)
                run.step({hero: (*angles, 1), caster: (*cast, 16)})
                self.assertEqual(barriers.projected[caster.body][1:], (200, hero.body))
                if shield_hp < 200:
                    barriers.damage(hero, 200 - shield_hp, actor.entity)
                self.assertEqual(npc_control.start(run.match, hero), actor.entity)
                for _ in range(60):
                    self.step(run, hero, actor, 1)
                    self.assertFalse(fight.is_stunned(hero))
                    entry = barriers.projected.get(caster.body)
                    if entry is None or entry[1] < shield_hp:
                        break
                if shield_hp == 200:
                    self.assertEqual(barriers.projected[caster.body][1:], (175, hero.body))
                else:
                    self.assertNotIn(caster.body, barriers.projected)
                self.assertEqual(pool.total, 200)
                self.assertEqual(npc_control.stop(run.match), 1)
                self.step(run, hero, actor)
                arrow, = fight.rockets
                self.assertEqual(arrow.direct.amount, 125)
                for _ in range(10):
                    self.step(run, hero, actor)
                self.assertEqual(actor_pool.total, before - 125)
                for _ in range(130):
                    if caster.body not in barriers.projected:
                        break
                    self.step(run, hero, actor)
                self.assertNotIn(caster.body, barriers.projected)
                self.begin(run, hero, actor)
                self.assertEqual(pool.total, 175)

    def test_projected_barrier_absorbs_assassin_periodic_damage_until_break_or_expiry(self):
        for breaks in (False, True):
            with self.subTest(breaks=breaks):
                run, hero, actor, caster, cast = self.talon_projected_arena("4", 2.5, start=False)
                fight = run.match.combat
                barriers = fight.zarya_barriers
                self.begin(run, hero, actor)
                pool = fight.vitals[hero.body]
                self.assertEqual(pool.total, 175)
                run.step({caster: (*cast, 16)})
                self.assertEqual(barriers.projected[caster.body][1:], (200, hero.body))
                instance = self.pin_instance(actor)
                periodic = instance.node_state(instance.graph.node(15))
                for _ in range(120):
                    if periodic.active:
                        break
                    self.step(run, hero, actor)
                self.assertTrue(periodic.active)
                shield_before = barriers.projected[caster.body][1]
                for _ in range(10):
                    self.step(run, hero, actor)
                self.assertAlmostEqual(shield_before - barriers.projected[caster.body][1], 16, places=4)
                self.assertEqual(pool.total, 175)
                if breaks:
                    barriers.damage(hero, barriers.projected[caster.body][1] - 0.8, actor.entity)
                    self.step(run, hero, actor)
                    self.assertEqual(pool.total, 175)
                else:
                    for _ in range(130):
                        if caster.body not in barriers.projected:
                            break
                        self.assertGreater(barriers.projected[caster.body][1], 1.6)
                        self.assertEqual(pool.total, 175)
                        self.step(run, hero, actor)
                self.assertNotIn(caster.body, barriers.projected)
                self.assertEqual(fight.pins.get(actor.entity), hero.body)
                before = pool.total
                self.step(run, hero, actor)
                self.assertAlmostEqual(pool.total, before - 1.6, places=4)
                self.assertEqual(fight.pins.get(actor.entity), hero.body)

    def test_projected_barrier_orders_active_assassin_doses_across_scheduler_gap(self):
        for shield_hp in (200, 15):
            for gap in (90, 180):
                outcomes = []
                for skip in (False, True):
                    with self.subTest(shield=shield_hp, gap=gap, skip=skip):
                        run, hero, actor, caster, cast = self.talon_projected_arena("4", 2.5, start=False)
                        fight = run.match.combat
                        barriers = fight.zarya_barriers
                        self.begin(run, hero, actor)
                        body = hero.body
                        pool = fight.vitals[body]
                        run.step({caster: (*cast, 16)})
                        self.assertEqual(barriers.projected[caster.body][1:], (200, body))
                        instance = self.pin_instance(actor)
                        periodic = instance.node_state(instance.graph.node(15))
                        for _ in range(120):
                            if periodic.active:
                                break
                            self.step(run, hero, actor)
                        self.assertTrue(periodic.active)
                        self.assertEqual(pool.total, 175)
                        before = barriers.projected[caster.body][1]
                        if shield_hp < before:
                            barriers.damage(hero, before - shield_hp, actor.entity)
                        end = run.frame + gap
                        if skip:
                            run.frame = end
                            run.match.update(end * combat.TICK, end)
                        else:
                            while run.frame < end:
                                self.step(run, hero, actor)
                        self.assertNotIn(caster.body, barriers.projected)
                        self.assertLess(pool.total, 175)
                        outcomes.append((round(pool.total, 4), pool.death_tick, pool.killer,
                                         fight.pins.get(actor.entity)))
                self.assertEqual(outcomes[0], outcomes[1], (shield_hp, gap))

    def test_fresh_projected_barrier_does_not_absorb_earlier_assassin_doses(self):
        run, hero, actor, caster, cast = self.talon_projected_arena("4", 2.5, start=False)
        fight = run.match.combat
        barriers = fight.zarya_barriers
        self.begin(run, hero, actor)
        pool = fight.vitals[hero.body]
        instance = self.pin_instance(actor)
        periodic = instance.node_state(instance.graph.node(15))
        for _ in range(120):
            if periodic.active:
                break
            self.step(run, hero, actor)
        self.assertTrue(periodic.active)
        run.frame += 1
        run.match.update(run.frame * combat.TICK, run.frame)
        before = pool.total
        caster.take_commands([Command(run.frame, yaw=cast[0], pitch=cast[1], buttons=16)])
        self.assertEqual(barriers.projected[caster.body][1:], (200, hero.body))
        run.frame += 90
        run.match.update(run.frame * combat.TICK, run.frame)
        self.assertAlmostEqual(pool.total, before - 89 * 1.6, places=4)
        self.assertAlmostEqual(barriers.projected[caster.body][1], 198.4, places=4)
        self.assertEqual(fight.pins.get(actor.entity), hero.body)

    def test_orisa_mesh_does_not_stop_manual_assassin_contact(self):
        for allied_to_npc in (False, True):
            with self.subTest(allied_to_npc=allied_to_npc):
                run, hero, actor = self.arena(self.catalog["4"], "bow", "hit", distance=8)
                shield = self.deploy_orisa_cover(run, hero, actor, allied_to_npc=allied_to_npc)
                fight = run.match.combat
                x, y, z = shield.position
                hero.mover.position[:] = (x, y, z - 1.25)
                hero.mover.body = None
                actor.position = (x, y, z + 1.25)
                origin = (x, y + combat.EYE, z + 1.25)
                eye = fight._eye(hero)
                distance = math.dist(origin, eye)
                direction = tuple((b - a) / distance for a, b in zip(origin, eye, strict=True))
                self.assertLess(distance, assassin.PIN_RANGE)
                self.assertIsNotNone(fight.orisa_barriers.intersect(shield, origin, direction, distance))
                for _ in range(70):
                    self.step(run, hero, actor, 1)
                self.begin(run, hero, actor, held=1)
                self.assertTrue(fight.is_stunned(hero))
                self.assertEqual(fight.vitals[hero.body].total, 175)
                self.assertEqual(shield.health, 600)
                self.step(run, hero, actor)
                self.assertFalse(fight.rockets)
                self.assertEqual(npc_control.stop(run.match), 1)
                self.assertFalse(fight.is_stunned(hero))
                for _ in range(50):
                    self.step(run, hero, actor)
                self.assertEqual(fight.vitals[hero.body].total, 175)
                self.assertEqual(shield.health, 600)
                actor_pool = fight.vitals[actor.entity]
                initial_hp = actor_pool.total
                for _ in range(70):
                    self.step(run, hero, actor, 1)
                self.step(run, hero, actor)
                self.assertEqual(len(fight.rockets), 1)
                for _ in range(12):
                    self.step(run, hero, actor)
                self.assertFalse(fight.rockets)
                self.assertEqual(shield.health, 475 if allied_to_npc else 600)
                self.assertEqual(actor_pool.total, initial_hp if allied_to_npc else initial_hp - 125)

    def test_reinhardt_mesh_blocks_recovery_bow_by_faction_but_not_assassin_contact(self):
        for allied in (False, True):
            with self.subTest(allied_to_assassin=allied):
                run, hero, actor, rein, barriers = self.talon_reinhardt_arena("4", 2.5, allied=allied)
                fight = run.match.combat
                rein.mover.position[2] = hero.mover.position[2] + 0.25
                rein.mover.body = None
                angles = self.target_aim(run, hero, actor)

                def step(button=0):
                    run.step({hero: (*angles, button), rein: (0, 0, 2)})

                for _ in range(70):
                    step(1)
                shield = barriers.states[rein.body]
                self.assertTrue(shield.active)
                origin = (actor.position[0], actor.position[1] + combat.EYE, actor.position[2])
                eye = fight._eye(hero)
                distance = math.dist(origin, eye)
                direction = tuple((end - start) / distance for start, end in zip(origin, eye, strict=True))
                self.assertIsNotNone(barriers.intersect(rein, origin, direction, distance))
                self.assertEqual(npc_control.start(run.match, hero), actor.entity)
                for _ in range(80):
                    step(1)
                    if fight.pins.get(actor.entity) == hero.body:
                        break
                self.assertEqual(fight.pins.get(actor.entity), hero.body)
                self.assertEqual(fight.vitals[hero.body].total, 175)
                self.assertEqual(shield.current, 1600)
                step()
                self.assertFalse(fight.rockets)
                self.assertEqual(npc_control.stop(run.match), 1)
                self.assertFalse(fight.is_stunned(hero))
                for _ in range(50):
                    step()
                self.assertEqual(fight.vitals[hero.body].total, 175)
                actor_pool = fight.vitals[actor.entity]
                before = actor_pool.total
                for _ in range(70):
                    step(1)
                step()
                arrow, = fight.rockets
                self.assertEqual(arrow.direct.amount, 125)
                for _ in range(10):
                    step()
                self.assertFalse(fight.rockets)
                self.assertEqual(shield.current, 1475 if allied else 1600)
                self.assertEqual(actor_pool.total, before if allied else before - 125)

    def test_pin_cancels_draw_and_initial_ultimate_then_allows_fresh_bow(self):
        for cast in (False, True):
            with self.subTest(cast=cast):
                run, hero, actor, pool = self.scene()
                fight = run.match.combat
                script = hero.body_script
                script.ult_seconds = 0
                held = 0 if cast else 1
                if cast:
                    script.set_ult_charge(1680)  # explicit test setup; not charge acquisition proof
                    self.step(run, hero, actor, 32)
                    self.assertTrue(hanzo_control.casting(script))
                else:
                    for _ in range(20):
                        self.step(run, hero, actor, held)
                    self.assertTrue(script.component.held)
                with patch.object(fight.projectiles, 'fired', wraps=fight.projectiles.fired) as fire:
                    self.begin(run, hero, actor, held)
                    self.assertEqual(pool.total, 175)
                    self.assertFalse(script.component.held)
                    self.assertFalse(hanzo_control.casting(script))
                    npc_control.stop(run.match)
                    for _ in range(90):
                        self.step(run, hero, actor)
                    self.assertFalse(fire.called)
                    for _ in range(70):
                        self.step(run, hero, actor, 1)
                    self.step(run, hero, actor)
                    for _ in range(10):
                        self.step(run, hero, actor)
                    self.assertEqual(fire.call_count, 1)
                definition = self.catalog['4']
                initial = definition.health + definition.armour + definition.shields
                self.assertEqual(fight.vitals[actor.entity].total, initial - 125)

    def test_pin_preserves_selected_sonic_but_cancels_storm_volley(self):
        for button, retained in ((8, True), (16, False)):
            with self.subTest(sonic_selected=retained):
                run, hero, actor, pool = self.scene()
                fight = run.match.combat
                if retained:
                    fight.configure_sonic(sonic_tests.profile())
                self.step(run, hero, actor, button)
                self.step(run, hero, actor)
                storm = next(i for i in hero.body_script.component.instances.values()
                             if i.graph.index == 0x146A)
                if not retained:
                    self.assertTrue(storm.states[0].flags & 1)
                with patch.object(fight.projectiles, "fired", wraps=fight.projectiles.fired) as fired:
                    self.begin(run, hero, actor)
                    self.assertEqual(pool.total, 175)
                    if not retained:
                        self.assertFalse(storm.states[0].flags & 1)
                        self.assertEqual(storm.find_var(0, 61).value(), 0)
                    for _ in range(12):
                        self.step(run, hero, actor, 1)
                    self.assertFalse(fired.called)
                    if not retained:
                        self.assertEqual(storm.find_var(0, 61).value(), 0)
                    self.assertEqual(npc_control.stop(run.match), 1)
                    for _ in range(8):
                        self.step(run, hero, actor)
                    for _ in range(70):
                        self.step(run, hero, actor, 1)
                    self.step(run, hero, actor)
                    for _ in range(10):
                        self.step(run, hero, actor)

                definitions = [call.args[3] for call in fired.call_args_list]
                self.assertEqual(len(definitions), 1)
                self.assertEqual(definitions[0] in sonic.DEFINITIONS, retained)
                self.assertNotIn(0x040000000000270F, definitions)
                definition = self.catalog["4"]
                initial = definition.health + definition.armour + definition.shields
                self.assertEqual(fight.vitals[actor.entity].total, initial - 125)
                self.assertEqual(len(fight.sonars), int(retained))
                self.assertEqual(fight.revealed_to(hero, actor), retained)
                if retained:
                    self.assertEqual(fight.sonars[0].attached, actor.entity)

    def test_pin_at_sonic_cooldown_boundary_requires_fresh_press_after_release(self):
        run, hero, actor, pool = self.scene()
        fight = run.match.combat
        actor_pool = fight.vitals_of(next(t for t in fight._targets() if t.owner is actor))
        actor_hp = actor_pool.total
        fight.configure_sonic(sonic_tests.profile())
        instance = next(i for i in hero.body_script.component.instances.values() if i.graph.index == 0xC1)
        selected = instance.component.entity_var(627)
        self.assertEqual(instance.vars[472].value(), 12)
        with patch.object(fight.projectiles, "fired", wraps=fight.projectiles.fired) as fired:
            self.step(run, hero, actor, 8)
            self.step(run, hero, actor)
            for _ in range(70):
                self.step(run, hero, actor, 1)
            self.step(run, hero, actor)
            released = run.frame
            while run.frame < released + 720:
                self.step(run, hero, actor)
            self.assertEqual(actor_pool.total, actor_hp - 125)
            self.assertGreater(instance.vars[61].value(), 0)
            self.assertEqual(selected.value(), 0)
            self.begin(run, hero, actor)
            self.assertLess(run.frame, released + 745)
            while run.frame < released + 744:
                self.step(run, hero, actor)
            self.step(run, hero, actor, 8)
            self.assertTrue(fight.is_stunned(hero))
            self.assertGreater(instance.vars[61].value(), 0)
            while run.frame < released + 752:
                self.step(run, hero, actor, 8)
            self.assertEqual(instance.vars[61].value(), 0)
            self.assertEqual(selected.value(), 0)
            self.assertEqual(fired.call_count, 1)
            self.assertEqual(npc_control.stop(run.match), 1)
            self.assertFalse(fight.is_stunned(hero))
            self.step(run, hero, actor)
            self.assertEqual(selected.value(), 0)
            self.step(run, hero, actor, 8)
            self.step(run, hero, actor)
            self.assertEqual(selected.value(), 1)
            for _ in range(70):
                self.step(run, hero, actor, 1)
            self.step(run, hero, actor)
            for _ in range(12):
                self.step(run, hero, actor)
            self.assertEqual([call.args[3] for call in fired.call_args_list], [sonic.DEFINITIONS[0]] * 2)
        self.assertFalse(pool.dead)
        self.assertEqual(actor_pool.total, actor_hp - 250)
        self.assertTrue(fight.revealed_to(hero, actor))
        self.assertGreater(instance.vars[61].value(), 0)

    def test_pin_after_first_storm_arrow_enters_cooldown_and_gates_retry(self):
        run, hero, actor, pool = self.scene()
        fight = run.match.combat
        storm = next(i for i in hero.body_script.component.instances.values() if i.graph.index == 0x146A)
        ability = storm.states[0]
        projectile = 0x040000000000270F
        with patch.object(fight.projectiles, "fired", wraps=fight.projectiles.fired) as fired:
            self.step(run, hero, actor, 16)
            self.step(run, hero, actor)
            for _ in range(50):
                self.step(run, hero, actor, 1)
                if fired.called:
                    break
            self.assertEqual([call.args[3] for call in fired.call_args_list], [projectile])
            self.assertTrue(ability.flags & 1)
            self.begin(run, hero, actor, held=1)
            self.assertEqual(pool.total, 175)
            self.assertFalse(ability.flags & 1)
            for _ in range(12):
                self.step(run, hero, actor, 1)
            self.assertEqual(fired.call_count, 1)
            self.assertGreater(storm.find_var(0, 61).value(), 9.7)
            self.assertEqual(storm.find_var(0, 215).value(), 0)
            vitals = fight.vitals[actor.entity]
            definition = self.catalog["4"]
            initial = definition.health + definition.armour + definition.shields
            self.assertEqual(vitals.total, initial - 70)
            self.assertEqual(npc_control.stop(run.match), 1)

            self.assertFalse(ability.flags & 1)
            self.step(run, hero, actor, 16)
            self.step(run, hero, actor)
            self.assertFalse(ability.flags & 1)
            for _ in range(700):
                if storm.find_var(0, 61).value() == 0:
                    break
                self.step(run, hero, actor)
            self.assertEqual(storm.find_var(0, 61).value(), 0)
            self.assertFalse(ability.flags & 1)
            self.assertEqual(fired.call_count, 1)

            self.step(run, hero, actor, 16)
            self.step(run, hero, actor)
            self.assertTrue(ability.flags & 1)
            for _ in range(50):
                self.step(run, hero, actor, 1)
                if fired.call_count == 2:
                    break
            self.assertEqual([call.args[3] for call in fired.call_args_list], [projectile, projectile])
            for _ in range(10):
                self.step(run, hero, actor)
            self.assertEqual(vitals.total, max(0, initial - 140))

    def assassin_and_downrange_enemy(self):
        run, hero, actor, pool = self.scene()
        fight = run.match.combat
        x, y, z = hero.mover.position
        run.match.collision_world = World(box(x - 20, x + 20, y - 1, y, z - 20, z + 90))
        actor.position = (x + 2.5, y, z)
        victim = run.match.add_player(3, "Downrange", SOLDIER, actor.team, False)
        in_world(run.match, victim)
        victim.client = fixture.NpcFixtureClient(victim)
        run.players.append(victim)
        self.assertTrue(run.match.switch_hero(victim, heroes()[SOLDIER]))
        victim.mover.position[:] = (x, y, z + 24)
        victim.mover.body = None
        angles = aim(fight._eye(hero), (x, y + 1.2, z + 24))
        victim_target = next(t for t in fight._targets() if t.owner is victim)
        victim_pool = fight.vitals_of(victim_target)
        return run, hero, actor, pool, victim, victim_pool, angles

    def test_released_arrows_hit_enemy_player_after_lethal_assassin_contact(self):
        for attack, amount in ((0, 125), (8, 125), (16, 70)):
            with self.subTest(attack=attack):
                run, hero, actor, pool, victim, victim_pool, angles = self.assassin_and_downrange_enemy()
                fight = run.match.combat
                if attack == 8:
                    fight.configure_sonic(sonic_tests.profile())
                old_body = hero.body
                charge = hero.body_script.component.entity_var(combat.ULT_CHARGE)
                pool.damage(199)
                if attack:
                    run.step({hero: (*angles, attack)})
                    run.step({})
                if attack == 16:
                    for _ in range(50):
                        run.step({hero: (*angles, 1)})
                        if fight.rockets:
                            break
                else:
                    for _ in range(70):
                        run.step({hero: (*angles, 1)})
                    run.step({hero: (*angles, 0)})
                arrow, = fight.rockets
                self.assertEqual((arrow.source_entity, arrow.direct.amount), (old_body, amount))

                held = int(attack == 16)
                self.step(run, hero, actor, held)
                self.assertEqual(npc_control.start(run.match, hero), actor.entity)
                for _ in range(40):
                    if pool.dead:
                        break
                    self.step(run, hero, actor, held)
                    self.assertTrue(all(shot is arrow for shot in fight.rockets))
                self.assertTrue(pool.dead)
                self.assertEqual(pool.killer, actor.entity)
                self.assertNotIn(old_body, fight.pins.values())
                self.assertIn(arrow, fight.rockets)
                self.assertEqual(victim_pool.total, 200)
                frozen = charge.value()
                for _ in range(75):
                    if arrow not in fight.rockets:
                        break
                    self.step(run, hero, actor, held)
                self.assertFalse(fight.rockets)
                self.assertEqual(victim_pool.total, 200 - amount)
                self.assertEqual(charge.value(), frozen)
                self.assertEqual(len(fight.sonars), int(attack == 8))
                if attack == 8:
                    beacon, = fight.sonars
                    self.assertEqual((beacon.source_entity, beacon.attached), (old_body, victim.body))

    def test_dragonstrike_survives_lethal_assassin_contact_at_flight_boundary(self):
        for transformed in (False, True):
            with self.subTest(transformed=transformed):
                run, hero, actor, pool, _, victim_pool, angles = self.assassin_and_downrange_enemy()
                fight = run.match.combat
                fight.configure_dragonstrike(dragon_tests.prepared_profile())
                old_body = hero.body
                script = hero.body_script
                script.ult_seconds = 0
                script.set_ult_charge(1680)
                pool.damage(199)
                run.step({hero: (*angles, 32)})
                for _ in range(100):
                    if fight.rockets:
                        break
                    run.step({})
                arrow, = fight.rockets
                self.assertIsNotNone(arrow.continuation)
                self.assertEqual(arrow.source_entity, old_body)
                if transformed:
                    for _ in range(80):
                        if fight.dragons:
                            break
                        run.step({})
                    original, = fight.dragons
                self.step(run, hero, actor)
                self.assertEqual(npc_control.start(run.match, hero), actor.entity)
                for _ in range(40):
                    if pool.dead:
                        break
                    run.step({})
                self.assertTrue(pool.dead)
                self.assertEqual(pool.killer, actor.entity)
                self.assertNotIn(old_body, fight.pins.values())
                self.assertEqual(victim_pool.total, 200)
                if transformed:
                    self.assertEqual(fight.dragons, [original])
                else:
                    self.assertIn(arrow, fight.rockets)
                    self.assertFalse(fight.dragons)
                    for _ in range(80):
                        if fight.dragons:
                            break
                        run.step({})
                flight, = fight.dragons
                self.assertFalse(fight.rockets)
                self.assertIs(flight.shooter, hero)
                self.assertEqual(flight.source_entity, old_body)
                for _ in range(100):
                    if victim_pool.total < 200:
                        break
                    run.step({})
                self.assertIn(round(200 - victim_pool.total, 3), (2.4, 4.8))
                self.assertTrue(pool.dead)
                self.assertEqual(script.component.entity_var(combat.ULT_CHARGE).value(), 0)

    def test_pin_discards_buffered_melee_then_fresh_press_recovers(self):
        run, hero, actor, pool = self.scene(distance=1.8)
        fight = run.match.combat
        self.step(run, hero, actor, 0x800)
        first = run.frame
        vitals = fight.vitals[actor.entity]
        after_first_hit = vitals.total
        for _ in range(53):
            self.step(run, hero, actor)
        self.step(run, hero, actor, 0x800)
        instance = next(i for i in hero.body_script.component.instances.values() if i.graph.index == 0x0043)
        ability = next(state for state in instance.states.values() if state.node.pos == 14)
        self.assertTrue(ability.presses)
        self.begin(run, hero, actor)
        self.assertLess(run.frame - first, 62)
        self.assertEqual(pool.total, 175)
        self.assertFalse(ability.presses)
        for _ in range(10):
            self.step(run, hero, actor, 0x800)
        self.assertEqual(fight.pins.get(actor.entity), hero.body)
        self.assertEqual(npc_control.stop(run.match), 1)
        for _ in range(12):
            self.step(run, hero, actor)
        self.assertEqual(vitals.total, after_first_hit)
        self.step(run, hero, actor, 0x800)
        self.assertEqual(vitals.total, after_first_hit - 30)
        hits = hero.client.messages(combat.HIT)
        self.assertEqual([hit["+0x78"]["+0x4"] for hit in hits], [30, 30])

    def test_pin_stops_active_lunge_and_fresh_jump_recovers_after_cooldown(self):
        run, hero, actor, pool = self.scene()
        fight = run.match.combat
        movement = next(i for i in hero.body_script.component.instances.values() if i.graph.index == 0x010C)
        world = hero.body_script.world
        with patch.object(world, "velocity_action", wraps=world.velocity_action) as launch:
            self.step(run, hero, actor, JUMP)
            for _ in range(14):
                self.step(run, hero, actor)
            self.step(run, hero, actor, JUMP)
            self.assertEqual(launch.call_count, 1)
            self.assertGreater(abs(hero.mover.velocity[2]), 14)
            self.begin(run, hero, actor)
            self.assertEqual(pool.total, 175)
            for _ in range(10):
                self.step(run, hero, actor, JUMP)
                self.assertEqual(hero.mover.velocity[0::2], (0, 0))
            self.assertEqual(launch.call_count, 1)
            self.assertEqual(fight.pins.get(actor.entity), hero.body)
            self.assertEqual(npc_control.stop(run.match), 1)
            self.assertGreater(movement.vars[61].value(), 0)
            for _ in range(400):
                if movement.vars[61].value() == 0:
                    break
                self.step(run, hero, actor)
            self.assertEqual(movement.vars[61].value(), 0)
            self.assertEqual(launch.call_count, 1)
            self.step(run, hero, actor, JUMP)
            for _ in range(14):
                self.step(run, hero, actor)
            self.step(run, hero, actor, JUMP)
            self.assertEqual(launch.call_count, 2)
            self.assertGreater(abs(hero.mover.velocity[2]), 14)

    def test_pin_interrupts_wall_climb_while_hanzo_still_faces_wall(self):
        run, hero, actor, pool = self.scene(distance=5)
        fight = run.match.combat
        x, y, z = hero.mover.position
        self.step(run, hero, actor)
        self.assertEqual(npc_control.start(run.match, hero), actor.entity)
        fight.match.collision_world = World(
            box(x - 20, x + 20, y - 1, y, z - 20, z + 20)
            + wall(x - 5, z - 0.7, x + 5, z - 0.7, y0=y, y1=y + 20),
        )

        def wall_step(run, hero, actor, button=0):
            run.step({hero: (-32768, 0, button)})

        active_at_pin = []
        interrupt = fight._interrupt

        def observe(target):
            if target.owner is hero:
                active_at_pin.append(hero.body_script.world.hanzo_climb.active)
            return interrupt(target)

        with patch.object(self, "step", wall_step), patch.object(fight, "_interrupt", observe):
            self.step(run, hero, actor, JUMP)
            self.step(run, hero, actor, JUMP)
            self.assertTrue(hero.body_script.world.hanzo_climb.active)
            self.assertFalse(fight.pins)
            actor.position = (x + 2, y, z)
            for _ in range(15):
                self.step(run, hero, actor, JUMP)
                if fight.pins.get(actor.entity) == hero.body:
                    break
            self.assertEqual(fight.pins.get(actor.entity), hero.body)
            self.assertEqual(active_at_pin, [True])
            self.assertEqual(pool.total, 175)
            pinned_height = hero.mover.position[1]
            for _ in range(8):
                self.step(run, hero, actor, JUMP)
                self.assertFalse(hero.body_script.world.hanzo_climb.active)
                self.assertFalse(hero.body_script.component.entity_var(1204).value())
            self.assertLess(hero.mover.position[1], pinned_height)
            self.assertEqual(npc_control.stop(run.match), 1)
            for _ in range(40):
                self.step(run, hero, actor)
            self.step(run, hero, actor, JUMP)
            self.step(run, hero, actor, JUMP)
            self.assertTrue(hero.body_script.world.hanzo_climb.active)

    def test_range_wall_and_faction_prevent_contact(self):
        for scenario in ('range', 'wall', 'ally'):
            with self.subTest(scenario=scenario):
                run, hero, actor, pool = self.scene(5 if scenario in ('range', 'wall') else 2.5,
                                                     'wall' if scenario == 'wall' else
                                                     'ally' if scenario == 'ally' else 'hit')
                self.step(run, hero, actor)
                if scenario == 'range':
                    self.assertEqual(npc_control.start(run.match, hero), actor.entity)
                else:
                    with self.assertRaises(ValueError):
                        npc_control.start(run.match, hero)
                for _ in range(100):
                    self.step(run, hero, actor)
                self.assertEqual(pool.total, 200)
                self.assertFalse(run.match.combat.pins)

    def test_new_occluder_releases_pin_without_delayed_damage(self):
        run, hero, actor, pool = self.scene()
        self.step(run, hero, actor)
        self.begin(run, hero, actor)
        x, y, z = hero.mover.position
        w = (z + actor.position[2]) / 2
        collision = World([
            (x-20, y, z-20, x-20, y, z+20, x+20, y, z+20),
            (x-20, y, z-20, x+20, y, z+20, x+20, y, z-20),
            (x-5, y, w, x+5, y, w, x+5, y+5, w),
            (x-5, y, w, x+5, y+5, w, x-5, y+5, w),
        ])
        run.match.collision_world = collision
        hero.mover.use(world=collision)
        before = pool.total
        for _ in range(60):
            self.step(run, hero, actor)
            self.assertFalse(run.match.combat.pins)
        self.assertEqual(pool.total, before)

    def test_reengagement_and_removal_retire_old_contact(self):
        run, hero, actor, pool = self.scene()
        self.step(run, hero, actor)
        self.begin(run, hero, actor)
        self.assertEqual(pool.total, 175)
        self.assertEqual(npc_control.stop(run.match), 1)
        for _ in range(70):
            self.step(run, hero, actor)
        self.assertEqual(pool.total, 175)
        self.begin(run, hero, actor)
        self.assertEqual(pool.total, 150)
        component = actor.body_script.component
        self.assertEqual(npc.remove_spawned(run.match), (actor.entity,))
        self.assertIsNone(component.world.manual_target)
        self.assertIsNone(component.world.match)
        self.assertFalse(run.match.combat.is_stunned(hero))
        for _ in range(70):
            self.step(run, hero, actor)
        self.assertEqual(pool.total, 150)

    def test_allied_bow_kill_ends_active_assassin_damage_and_hanzo_can_fire(self):
        run, hero, actor, pool, _, victim_pool, downrange = self.assassin_and_downrange_enemy()
        match = run.match
        match.game_map = replace(match.game_map, free_for_all=False)
        self.assertNotEqual(hero.team, actor.team)
        fight = match.combat
        ally = match.add_player(4, "Rescuer", fixture.HANZO, hero.team, False)
        in_world(match, ally)
        ally.client = fixture.NpcFixtureClient(ally)
        run.players.append(ally)
        self.assertTrue(match.switch_hero(ally, heroes()[fixture.HANZO]))
        self.assertEqual(ally.team, hero.team)
        x, y, z = actor.position
        ally.mover.position[:] = (x + 5, y, z)
        ally.mover.body = None
        rescue = aim(fight._eye(ally), (x, y + 1, z))
        target = next(t for t in fight._targets() if t.owner is actor)
        actor_pool = fight.vitals_of(target)
        actor_pool.damage(actor_pool.total - 100)  # Seed one full bow hit to be lethal.
        for _ in range(70):
            run.step({ally: (*rescue, 1)})
        select = self.target_aim(run, hero, actor)
        run.step({hero: (*select, 0), ally: (*rescue, 1)})
        self.assertEqual(npc_control.start(match, hero), actor.entity)
        instance = self.pin_instance(actor)
        for _ in range(150):
            run.step({ally: (*rescue, 1)})
            if pool.total < 175:
                break
        phase = instance.states.get(instance.graph.node(15).state)
        self.assertIsNotNone(phase)
        self.assertTrue(phase.active)
        self.assertLess(pool.total, 175)
        self.assertEqual(fight.pins.get(actor.entity), hero.body)

        run.step({ally: (*rescue, 0)})
        arrow, = fight.rockets
        self.assertEqual((arrow.source_entity, arrow.direct.amount), (ally.body, 125))
        for _ in range(12):
            if actor.dead:
                break
            run.step({})
        self.assertTrue(actor.dead)
        self.assertEqual(actor_pool.killer, ally.body)
        self.assertNotIn(actor.entity, fight.pins)
        self.assertFalse(fight.is_stunned(hero))
        before = pool.total
        for _ in range(70):
            run.step({hero: (*downrange, 1)})
        run.step({hero: (*downrange, 0)})
        recovered, = fight.rockets
        self.assertEqual((recovered.source_entity, recovered.direct.amount), (hero.body, 125))
        for _ in range(40):
            if recovered not in fight.rockets:
                break
            run.step({})
        self.assertFalse(fight.rockets)
        self.assertEqual(victim_pool.total, 75)
        self.assertEqual(pool.total, before)

    def test_allied_flashbang_releases_hanzo_until_fresh_assassin_contact(self):
        run, hero, actor, pool, _, _, _ = self.assassin_and_downrange_enemy()
        match = run.match
        match.game_map = replace(match.game_map, free_for_all=False)
        fight = match.combat
        mccree = 0x02E0000000000042
        ally = match.add_player(4, "Rescuer", mccree, hero.team, False)
        in_world(match, ally)
        ally.client = fixture.NpcFixtureClient(ally)
        run.players.append(ally)
        self.assertTrue(match.switch_hero(ally, heroes()[mccree]))
        x, y, z = actor.position
        ally.mover.position[:] = (x + 3, y, z)
        ally.mover.body = None
        rescue = aim(fight._eye(ally), (x, y + 1, z))
        target = next(t for t in fight._targets() if t.owner is actor)
        actor_pool = fight.vitals_of(target)
        initial = actor_pool.total
        self.step(run, hero, actor)
        self.begin(run, hero, actor)
        for _ in range(150):
            run.step({})
            if pool.total < 175:
                break
        self.assertLess(pool.total, 175)
        self.assertTrue(fight.is_stunned(hero))
        run.step({ally: (*rescue, 16)})
        for _ in range(50):
            if fight.is_stunned(actor):
                break
            run.step({})
        self.assertTrue(fight.is_stunned(actor))
        self.assertFalse(actor.dead)
        self.assertEqual(actor_pool.total, initial - 25)
        self.assertNotIn(actor.entity, fight.pins)
        self.assertFalse(fight.is_stunned(hero))
        instance = self.pin_instance(actor)
        self.assertFalse(instance.states[instance.graph.node(15).state].active)
        before = pool.total
        deadline = fight.stuns[actor.entity]
        while fight.tick < deadline - 1:
            run.step({})
            self.assertFalse(fight.is_stunned(hero))
            self.assertEqual(pool.total, before)
        for _ in range(100):
            run.step({})
            if fight.pins.get(actor.entity) == hero.body:
                break
        self.assertNotIn(actor.entity, fight.stuns)
        self.assertEqual(fight.pins.get(actor.entity), hero.body)
        self.assertAlmostEqual(pool.total, before - 25, places=4)

    def test_actual_continuous_phase_kills_and_releases_victim(self):
        run, hero, actor, pool = self.scene()
        self.step(run, hero, actor)
        self.begin(run, hero, actor)
        body = hero.body
        for _ in range(300):
            self.step(run, hero, actor)
            if pool.dead:
                break
        self.assertTrue(pool.dead)
        self.assertEqual(pool.total, 0)
        self.assertEqual(pool.killer, actor.entity)
        self.assertNotIn(body, run.match.combat.pins.values())
        self.step(run, hero, actor)
        self.assertIsNone(actor.body_script.component.world.manual_target)
