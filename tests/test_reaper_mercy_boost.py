"""Actual Mercy staff input, Reaper damage and post-mitigation Reaping."""

import unittest
from unittest.mock import patch

import test_game_reaper as reaper_tests
import test_reaper_ana as ana_tests
import test_reaper_barriers as barrier_tests
from test_game_combat import aim

from ow174.game import ana, combat, mercy_beam, observers, reaper, sigma_grasp, stats
from ow174.game.collision import World, flat_world
from ow174.game.commands import Command
from ow174.game.content import find_hero, heroes
from ow174.game.script import expr, runtime
from ow174.game.script.decode import read_chunk
from ow174.game.script.driver import ULT_CHARGE, ULT_COST, BodyStream
from ow174.game.statescript import chunk


class ReaperMercyBoostTests(unittest.TestCase):
    def scene(self, *, names=("Reaper", "Enemy", "Mercy")):
        run, owner, victim, _ = reaper_tests.ReaperTests().scenario(
            team_mode=True, names=names,
        )
        mercy = run.players[2]
        self.assertTrue(run.match.switch_hero(mercy, heroes()[mercy_beam.HERO]))
        owner.team = mercy.team = 0
        victim.team = 1
        x, y, z = owner.mover.position
        mercy.mover.position[:] = (x - 3, y, z)
        mercy.mover.body = None
        for _ in range(30):
            run.step({})
        return run, owner, victim, mercy

    def step(self, run, owner, victim, mercy, *, beam=2, action=0):
        fight = run.match.combat
        x, y, z = owner.mover.position
        support_aim = aim(fight._eye(mercy), (x, y + 1, z))
        x, y, z = victim.mover.position
        shot_aim = aim(fight._eye(owner), (x, y + 1, z))
        run.step({mercy: (*support_aim, beam), owner: (*shot_aim, action)})

    def beam(self, run, owner, victim, mercy):
        for _ in range(15):
            self.step(run, owner, victim, mercy)
        self.assertEqual(mercy_beam.factor(run.match.combat, owner), 1.3)

    def chain_scene(self):
        run, owner, victim, mercy = self.scene(names=("Reaper", "Enemy", "Mercy", "Anchor"))
        anchor = run.players[3]
        anchor.team = owner.team
        x, y, z = owner.mover.position
        run.match.collision_world = flat_world(y)
        anchor.mover.position[:] = (x - 8, y, z)
        mercy.mover.position[:] = (x - 8, y, z - 10)
        anchor.mover.body = mercy.mover.body = None
        return run, owner, victim, mercy, anchor

    def cast_valkyrie(self, run, mercy):
        script = mercy.body_script
        self.assertEqual(script.component.entity_var(ULT_COST).value(), 1820)
        script.set_ult_charge(1820)
        run.step({mercy: (0, 0, 32)})
        self.assertEqual(script.component.entity_var(ULT_CHARGE).value(), 0)
        active = script.component.entity_var(842)
        self.assertTrue(active.value())
        for _ in range(35):
            run.step({})
        return active

    def chain_step(self, run, owner, victim, mercy, anchor, *, action=0, beam=2):
        fight = run.match.combat
        x, y, z = anchor.mover.position
        staff_aim = aim(fight._eye(mercy), (x, y + 1, z))
        x, y, z = victim.mover.position
        shot_aim = aim(fight._eye(owner), (x, y + 1, z))
        run.step({mercy: (*staff_aim, beam), owner: (*shot_aim, action)})

    def test_actual_valkyrie_chain_boosts_lethal_blossom_and_reaps_effective_loss(self):
        run, owner, victim, mercy, anchor = self.chain_scene()
        fight = run.match.combat
        charge = self.isolate_boost_charge(mercy)
        self.cast_valkyrie(run, mercy)
        for _ in range(50):
            self.chain_step(run, owner, victim, mercy, anchor)
        self.assertEqual(mercy_beam.factor(fight, anchor), 1.3)
        self.assertEqual(mercy_beam.factor(fight, owner), 1.3)
        self.assertEqual(mercy_beam.factor(fight, victim), 1)
        pool = fight.vitals[victim.body]
        pool.damage(pool.total - 130)
        own = fight.vitals[owner.body]
        own.damage(100)
        owner.body_script.set_ult_charge(2100)
        self.chain_step(run, owner, victim, mercy, anchor, action=32)
        self.assertAlmostEqual(pool.total, 85.8)
        self.assertAlmostEqual(own.total, 150 + 44.2 * 0.35, places=4)
        for _ in range(40):
            self.chain_step(run, owner, victim, mercy, anchor)
            if pool.dead:
                break
        self.assertTrue(pool.dead)
        self.assertEqual(pool.killer, owner.body)
        self.assertAlmostEqual(own.total, 150 + 130 * 0.35, places=4)
        self.assertAlmostEqual(owner.stats.value(stats.HERO_DAMAGE), 130, places=4)
        self.assertEqual(mercy.stats.value(stats.HERO_DAMAGE), 0)
        self.assertAlmostEqual(mercy.stats.value(stats.MERCY_DAMAGE_AMPLIFIED), 28, places=4)
        self.assertEqual(charge.value(), 0)
        self.assertEqual(owner.body_script.component.entity_var(ULT_CHARGE).value(), 0)
        feed = next(i for i in victim.body_script.component.instances.values() if i.graph.index == 0x01CF)
        self.assertEqual(feed.vars[9296].value(), (expr.Entity(mercy.body),))

    def test_actual_valkyrie_expiry_removes_chain_during_blossom(self):
        run, owner, victim, mercy, anchor = self.chain_scene()
        fight = run.match.combat
        active = self.cast_valkyrie(run, mercy)
        run.frame += 875
        run.match.update(run.frame * combat.TICK, run.frame, frames=False)
        self.assertTrue(active.value())
        for _ in range(15):
            self.chain_step(run, owner, victim, mercy, anchor)
        self.assertEqual(mercy_beam.factor(fight, owner), 1.3)
        pool = fight.vitals[victim.body] = combat.Vitals(victim.body, 1000)
        owner.body_script.set_ult_charge(2100)
        before = pool.total
        boosted, base = 0, 0
        for tick in range(200):
            self.chain_step(run, owner, victim, mercy, anchor, action=32 if tick == 0 else 0)
            if pool.total < before:
                if active.value():
                    self.assertAlmostEqual(before - pool.total, 44.2)
                    boosted += 1
                else:
                    self.assertAlmostEqual(before - pool.total, 34)
                    base += 1
                before = pool.total
        self.assertGreater(boosted, 0)
        self.assertGreater(base, 0)
        self.assertEqual(boosted + base, 15)
        self.assertFalse(active.value())
        self.assertEqual(mercy_beam.factor(fight, anchor), 1.3)
        self.assertEqual(mercy_beam.factor(fight, owner), 1)

    def test_actual_valkyrie_chain_range_cover_and_release_gate_reaper_shells(self):
        run, owner, victim, mercy, anchor = self.chain_scene()
        fight = run.match.combat
        pool = fight.vitals[victim.body] = combat.Vitals(victim.body, 1000)
        self.cast_valkyrie(run, mercy)
        for _ in range(50):
            self.chain_step(run, owner, victim, mercy, anchor)
        world = run.match.collision_world
        x, y, z = owner.mover.position
        wall = x - 4
        cover = World([
            (x-30,y,z-30,x-30,y,z+30,x+30,y,z+30),
            (x-30,y,z-30,x+30,y,z+30,x+30,y,z-30),
            (wall,y,z-8,wall,y+5,z-8,wall,y+5,z+8),
            (wall,y,z-8,wall,y+5,z+8,wall,y,z+8),
        ], name="Valkyrie chain cover")
        positions = tuple(mercy.mover.position), tuple(anchor.mover.position)
        for distance_shift, blocked, beam, amount in (
            (0, False, 2, 156), (4, False, 2, 120), (0, True, 2, 120),
            (0, False, 2, 156), (0, False, 0, 120),
        ):
            with self.subTest(distance_shift=distance_shift, blocked=blocked, beam=beam):
                run.match.collision_world = cover if blocked else world
                for actor, (sx, sy, sz) in zip((mercy, anchor), positions, strict=True):
                    actor.mover.position[:] = (sx - distance_shift, sy, sz)
                    actor.mover.body = None
                for _ in range(40):
                    self.chain_step(run, owner, victim, mercy, anchor, beam=beam)
                self.assertEqual(mercy_beam.factor(fight, anchor), 1.3 if beam else 1)
                before = pool.total
                self.chain_step(run, owner, victim, mercy, anchor, beam=beam, action=1)
                self.assertAlmostEqual(before - pool.total, amount)

    def test_actual_yellow_beam_heals_wraith_and_stops_across_idle_gap(self):
        outcomes = []
        for skipped in (False, True):
            run, owner, victim, mercy = self.scene()
            fight = run.match.combat
            own = fight.vitals[owner.body]
            own.damage(100)
            self.step(run, owner, victim, mercy, beam=1, action=8)
            for _ in range(15):
                self.step(run, owner, victim, mercy, beam=1)
            self.assertTrue(reaper.intangible(owner))
            before = own.total
            self.assertGreater(before, 150)
            self.step(run, owner, victim, mercy, beam=1)
            self.assertAlmostEqual(own.total - before, 55 * combat.TICK)
            for count in (230,) if skipped else (1,) * 230:
                run.frame += count
                run.match.update(run.frame * combat.TICK, run.frame, frames=False)
            self.assertFalse(reaper.intangible(owner))
            health = own.total
            run.frame += 20
            run.match.update(run.frame * combat.TICK, run.frame, frames=False)
            self.assertEqual(own.total, health)
            self.assertAlmostEqual(mercy.stats.value(stats.HEALING), health - 150)
            outcomes.append(health)
        self.assertAlmostEqual(*outcomes)

    def test_actual_reaper_healing_awards_effective_charge_and_can_fund_valkyrie(self):
        run, owner, victim, mercy = self.scene()
        script = mercy.body_script
        script.ult_seconds = 0
        component = script.component
        component.set(12918, 0)
        script.set_ult_charge(0)
        own = run.match.combat.vitals[owner.body]
        own.damage(0.25)
        charge = component.entity_var(ULT_CHARGE)
        for _ in range(25):
            self.step(run, owner, victim, mercy, beam=1)
        self.assertEqual(own.total, 250)
        self.assertEqual(charge.value(), 0.25)
        self.assertEqual(mercy.stats.value(stats.HEALING), 0.25)
        award = next(i for i in component.instances.values() if i.graph.index == 0x0B8B)
        self.assertEqual(award.vars[536].value(), expr.Entity(owner.body))
        self.assertEqual(award.vars[659].value(), (expr.Asset(0x0D800000000034BF),))
        script.set_ult_charge(1819.75)
        own.damage(10)
        self.step(run, owner, victim, mercy, beam=1)
        self.assertEqual(charge.value(), 1820)
        self.step(run, owner, victim, mercy, beam=32)
        self.assertTrue(component.entity_var(842).value())
        self.assertEqual(charge.value(), 0)

    def test_actual_yellow_beam_respects_recipient_and_mode_charge_gates(self):
        run, owner, victim, mercy = self.scene()
        script = mercy.body_script
        script.ult_seconds = 0
        component = script.component
        component.set(12918, 0)
        script.set_ult_charge(0)
        own = run.match.combat.vitals[owner.body]
        own.damage(100)
        charge = component.entity_var(ULT_CHARGE)
        owner.body_script.component.set(17625, True)
        for _ in range(20):
            self.step(run, owner, victim, mercy, beam=1)
        self.assertGreater(own.total, 150)
        self.assertEqual(charge.value(), 0)
        owner.body_script.component.set(17625, False)
        runtime.send_message(component, 0x024000000000002D,
                             {0x29: expr.Asset(0x0D80000000000016)}, mercy.body)
        component.run_now()
        for _ in range(5):
            self.step(run, owner, victim, mercy, beam=1)
        self.assertEqual(charge.value(), 0)
        runtime.send_message(component, 0x024000000000002D, {0x29: expr.Asset(0)}, mercy.body)
        component.run_now()
        self.step(run, owner, victim, mercy, beam=1)
        component.set(9532, 2.0)
        component.set(13022, 0.25)
        before, points = own.total, charge.value()
        for _ in range(10):
            self.step(run, owner, victim, mercy, beam=1)
        self.assertAlmostEqual(own.total - before, 8.8, places=4)
        self.assertAlmostEqual(charge.value() - points, 4.4, places=4)

    def test_actual_ana_antiheal_blocks_mercy_charge_until_healing_resumes(self):
        run, owner, victim, mercy = self.scene()
        self.assertTrue(run.match.switch_hero(victim, heroes()[ana.HERO], preserve_pose=True))
        fight = run.match.combat
        script = mercy.body_script
        script.ult_seconds = 0
        script.component.set(12918, 0)
        script.set_ult_charge(0)
        own = fight.vitals[owner.body]
        own.damage(100)
        ana_tests.ReaperAnaTests().grenade(run, victim, owner)
        end = fight.heal_modifiers[owner.body]["anti"]["end"]
        charge = script.component.entity_var(ULT_CHARGE)
        while end - fight.now > 0.2:
            self.step(run, owner, victim, mercy, beam=1)
        self.assertEqual(own.total, 90)
        self.assertEqual(charge.value(), 0)
        for _ in range(30):
            self.step(run, owner, victim, mercy, beam=1)
        self.assertGreater(own.total, 90)
        self.assertAlmostEqual(charge.value(), own.total - 90, places=4)

    def test_future_yellow_beam_input_heals_once_on_the_mapped_world_clock(self):
        run, owner, _, mercy = self.scene()
        fight = run.match.combat
        own = fight.vitals[owner.body]
        own.damage(100)
        mercy.body_script.ult_seconds = 0
        mercy.body_script.component.set(12918, 0)
        mercy.body_script.set_ult_charge(0)
        charge = mercy.body_script.component.entity_var(ULT_CHARGE)
        x, y, z = owner.mover.position
        yaw, pitch = aim(fight._eye(mercy), (x, y + 1, z))
        future = run.frame + 5
        mercy.take_commands([Command(future, yaw=yaw, pitch=pitch, buttons=1)])
        self.assertIsNotNone(mercy_beam.healing_state(mercy))
        self.assertEqual(mercy.body_script.world_frame_anchor, (run.frame, future))
        run.match.update(run.frame * combat.TICK, run.frame, frames=False)
        self.assertEqual(own.total, 150)
        self.assertEqual(charge.value(), 0)
        run.frame += 1
        run.match.update(run.frame * combat.TICK, run.frame, frames=False)
        self.assertAlmostEqual(own.total, 150 + 55 * combat.TICK)
        run.match.update(run.frame * combat.TICK, run.frame, frames=False)
        self.assertAlmostEqual(own.total, 150 + 55 * combat.TICK)
        run.frame += 20
        run.match.update(run.frame * combat.TICK, run.frame, frames=False)
        self.assertAlmostEqual(own.total, 150 + 55 * combat.TICK)
        self.assertAlmostEqual(charge.value(), 55 * combat.TICK, places=5)

    def test_actual_valkyrie_yellow_chain_and_reaping_recover_after_antiheal(self):
        run, owner, victim, mercy, anchor = self.chain_scene()
        self.assertTrue(run.match.switch_hero(victim, heroes()[ana.HERO], preserve_pose=True))
        fight = run.match.combat
        own = fight.vitals[owner.body]
        own.damage(100)
        self.cast_valkyrie(run, mercy)
        ana_tests.ReaperAnaTests().grenade(run, victim, owner)
        self.assertEqual(own.total, 90)
        end = fight.heal_modifiers[owner.body]["anti"]["end"]
        anchor_pool = fight.vitals[anchor.body]
        anchor_pool.damage(100)
        enemy = fight.vitals[victim.body] = combat.Vitals(victim.body, 1000)
        while end - fight.now > 0.85:
            self.chain_step(run, owner, victim, mercy, anchor, beam=1)
        self.assertEqual(own.total, 90)
        self.assertGreater(anchor_pool.total, 100)
        owner.body_script.set_ult_charge(2100)
        self.chain_step(run, owner, victim, mercy, anchor, beam=1, action=32)
        self.assertEqual(enemy.total, 966)
        self.assertEqual(own.total, 90)
        recovered = 0
        for _ in range(80):
            previous = own.total
            previous_enemy = enemy.total
            mercy_before = mercy.stats.value(stats.HEALING)
            self.chain_step(run, owner, victim, mercy, anchor, beam=1)
            if "anti" in fight.heal_modifiers.get(owner.body, {}):
                self.assertEqual(own.total, 90)
                continue
            healing = 60 * combat.TICK
            reaping = (previous_enemy - enemy.total) * 0.35
            self.assertAlmostEqual(mercy.stats.value(stats.HEALING) - mercy_before, healing, places=4)
            self.assertAlmostEqual(own.total - previous, healing + reaping, places=4)
            recovered += 1
            if recovered == 15:
                break
        self.assertEqual(recovered, 15)
        self.assertNotIn("anti", fight.heal_modifiers.get(owner.body, {}))
        self.assertGreater(owner.stats.value(stats.HEALING), 0)
        self.assertEqual(owner.body_script.component.entity_var(ULT_CHARGE).value(), 0)

    def test_actual_valkyrie_yellow_expiry_restores_only_normal_primary_healing(self):
        run, owner, victim, mercy, anchor = self.chain_scene()
        fight = run.match.combat
        mercy.body_script.ult_seconds = 0
        mercy.body_script.component.set(12918, 0)
        charge = mercy.body_script.component.entity_var(ULT_CHARGE)
        active = self.cast_valkyrie(run, mercy)
        run.frame += 875
        run.match.update(run.frame * combat.TICK, run.frame, frames=False)
        for _ in range(15):
            self.chain_step(run, owner, victim, mercy, anchor, beam=1)
        self.assertTrue(active.value())
        primary = fight.vitals[anchor.body]
        secondary = fight.vitals[owner.body]
        primary.damage(100)
        secondary.damage(100)
        normal = chained = 0
        for _ in range(35):
            p, s = primary.total, secondary.total
            was_active = active.value()
            self.chain_step(run, owner, victim, mercy, anchor, beam=1)
            if was_active:
                self.assertAlmostEqual(primary.total - p, 60 * combat.TICK)
                self.assertAlmostEqual(secondary.total - s, 60 * combat.TICK)
                chained += 1
                self.assertEqual(charge.value(), 0)
            else:
                self.assertAlmostEqual(primary.total - p, 55 * combat.TICK)
                self.assertEqual(secondary.total, s)
                normal += 1
        self.assertGreater(chained, 0)
        self.assertGreater(normal, 0)
        self.assertAlmostEqual(charge.value(), normal * 55 * combat.TICK, places=4)

    def test_actual_blue_beam_boosts_shell_and_reaping_then_release_restores_base(self):
        run, owner, victim, mercy = self.scene()
        fight = run.match.combat
        own = fight.vitals[owner.body]
        own.damage(100)
        pool = combat.Vitals(victim.body, 600)
        fight.vitals[victim.body] = pool
        self.beam(run, owner, victim, mercy)
        self.step(run, owner, victim, mercy, action=1)
        self.assertAlmostEqual(pool.total, 444)
        self.assertAlmostEqual(own.total, 204.6, places=4)
        self.assertAlmostEqual(owner.stats.value(stats.HERO_DAMAGE), 156, places=4)
        self.assertEqual(mercy.stats.value(stats.HERO_DAMAGE), 0)
        for _ in range(40):
            self.step(run, owner, victim, mercy, beam=0)
        self.assertEqual(mercy_beam.factor(fight, owner), 1)
        self.step(run, owner, victim, mercy, beam=0, action=1)
        self.assertAlmostEqual(pool.total, 324)
        self.assertAlmostEqual(own.total, 246.6, places=4)

    def test_actual_boosted_shell_credits_only_bonus_to_mercy(self):
        run, owner, victim, mercy = self.scene()
        fight = run.match.combat
        pool = fight.vitals[victim.body] = combat.Vitals(victim.body, 600)
        charge = self.isolate_boost_charge(mercy)
        owner_charge = self.isolate_boost_charge(owner)
        self.beam(run, owner, victim, mercy)
        self.step(run, owner, victim, mercy, action=1)
        self.assertAlmostEqual(pool.total, 444)
        self.assertAlmostEqual(owner.stats.value(stats.HERO_DAMAGE), 156, places=4)
        self.assertEqual(mercy.stats.value(stats.HERO_DAMAGE), 0)
        self.assertAlmostEqual(charge.value(), 36, places=4)
        self.assertAlmostEqual(owner_charge.value(), 120, places=4)
        self.assertAlmostEqual(mercy.stats.value(stats.MERCY_DAMAGE_AMPLIFIED), 36, places=4)
        self.assertAlmostEqual(mercy.stats.value(stats.MERCY_DAMAGE_AMPLIFIED_TOTAL), 36, places=4)
        staff = next(i for i in mercy.body_script.component.instances.values()
                     if i.graph.index == mercy_beam.STAFF)
        self.assertEqual(staff.vars[536].value(), expr.Entity(victim.body))
        self.assertGreater(staff.vars[476].value(), 0)

    def test_actual_boosted_shell_respects_staff_victim_charge_gate(self):
        run, owner, victim, mercy = self.scene()
        fight = run.match.combat
        pool = fight.vitals[victim.body] = combat.Vitals(victim.body, 600)
        victim.body_script.component.set(6442, True)
        charge = self.isolate_boost_charge(mercy)
        self.beam(run, owner, victim, mercy)
        self.step(run, owner, victim, mercy, action=1)
        self.assertAlmostEqual(pool.total, 444)
        self.assertAlmostEqual(mercy.stats.value(stats.MERCY_DAMAGE_AMPLIFIED), 36, places=4)
        self.assertEqual(charge.value(), 0)

    def test_actual_lethal_shell_carries_only_contributing_mercy_in_kill_feed(self):
        for health, bonus in ((13, 1.8), (5, 0)):
            with self.subTest(health=health):
                run, owner, victim, mercy = self.scene()
                fight = run.match.combat
                pool = fight.vitals[victim.body] = combat.Vitals(victim.body, health)
                self.beam(run, owner, victim, mercy)
                script = victim.body_script
                stream = BodyStream(victim.body)
                stream.base = observers.snapshot(script)
                known = {index: value[0] for index, value in stream.base.instances.items()}
                feed_id = next(index for index, graph_id in known.items() if graph_id == 0x01CF)

                self.step(run, owner, victim, mercy, action=1)

                self.assertTrue(pool.dead)
                self.assertEqual(pool.killer, owner.body)
                self.assertAlmostEqual(mercy.stats.value(stats.MERCY_DAMAGE_AMPLIFIED), bonus, places=4)
                self.assertEqual(owner.stats.value(stats.ELIMINATIONS), 1)
                self.assertEqual(mercy.stats.value(stats.ELIMINATIONS), 0)
                delta, _ = observers.delta(script, stream, observers.snapshot(script))
                wire = chunk(delta, script.frame, 0)
                feed = read_chunk(wire.getvalue(), wire.count, known).instances[feed_id]
                assistants = (expr.Entity(mercy.body),) if bonus else ()
                self.assertEqual(feed.vars[392], (expr.Entity(owner.body), ()))
                self.assertEqual(feed.vars[9296], (assistants, ()))

    def test_actual_reflected_reaper_shell_uses_only_deflect_side_blue_beam(self):
        cases = ((True, False, 250, 120, 0), (False, True, 250, 156, 36),
                 (True, True, 250, 156, 36), (True, True, 13, 13, 1.8))
        for launch_beam, return_beam, health, loss, bonus in cases:
            with self.subTest(launch_beam=launch_beam, return_beam=return_beam, health=health):
                run, owner, genji, launch_mercy = self.scene(
                    names=("Reaper", "Genji", "Launch Mercy", "Return Mercy"),
                )
                return_mercy = run.players[3]
                genji_position = tuple(genji.mover.position)
                self.assertTrue(run.match.switch_hero(genji, heroes()[0x02E0000000000029]))
                self.assertTrue(run.match.switch_hero(return_mercy, heroes()[mercy_beam.HERO]))
                genji.team = return_mercy.team = 1
                genji.mover.position[:] = genji_position
                x, y, z = genji_position
                return_mercy.mover.position[:] = (x + 3, y, z)
                genji.mover.body = return_mercy.mover.body = None
                fight = run.match.combat
                pool = fight.vitals[owner.body] = combat.Vitals(owner.body, health)
                charges = {actor: self.isolate_boost_charge(actor)
                           for actor in (owner, genji, launch_mercy, return_mercy)}
                beams = {}
                for caster, target, active in ((launch_mercy, owner, launch_beam),
                                               (return_mercy, genji, return_beam)):
                    x, y, z = target.mover.position
                    beams[caster] = (*aim(fight._eye(caster), (x, y + 1, z)), 2 if active else 0)
                for _ in range(18):
                    run.step(beams)
                forward = (-32768, 2200)
                for frame in range(12):
                    run.step({**beams, genji: (*forward, 16 if frame == 0 else 0)})
                self.assertEqual(mercy_beam.factor(fight, owner), 1.3 if launch_beam else 1)
                self.assertEqual(mercy_beam.factor(fight, genji), 1.3 if return_beam else 1)
                x, y, z = genji.mover.position
                shot = (*aim(fight._eye(owner), (x, y + 1, z)), 1)
                run.step({**beams, owner: shot, genji: (*forward, 0)})

                self.assertAlmostEqual(health - pool.total, loss, places=4)
                self.assertAlmostEqual(genji.stats.value(stats.HERO_DAMAGE), loss, places=4)
                self.assertEqual(owner.stats.value(stats.HERO_DAMAGE), 0)
                self.assertEqual(charges[owner].value(), 0)
                self.assertAlmostEqual(charges[genji].value(), loss - bonus, places=4)
                self.assertEqual(charges[launch_mercy].value(), 0)
                self.assertEqual(launch_mercy.stats.value(stats.MERCY_DAMAGE_AMPLIFIED), 0)
                self.assertAlmostEqual(charges[return_mercy].value(), bonus, places=4)
                amplified = return_mercy.stats.value(stats.MERCY_DAMAGE_AMPLIFIED)
                self.assertAlmostEqual(amplified, bonus, places=4)
                if pool.dead:
                    self.assertEqual(pool.killer, genji.body)
                    feed = next(i for i in owner.body_script.component.instances.values()
                                if i.graph.index == 0x01CF)
                    self.assertEqual(feed.vars[9296].value(), (expr.Entity(return_mercy.body),))

    def test_actual_grasp_absorbs_blue_boosted_deflect_then_body_contact_funds_charge(self):
        run, owner, genji, mercy = self.scene(names=("Reaper", "Genji", "Mercy", "Sigma"))
        sigma = run.players[3]
        self.assertTrue(run.match.switch_hero(genji, heroes()[0x02E0000000000029]))
        self.assertTrue(run.match.switch_hero(sigma, heroes()[sigma_grasp.HERO]))
        genji.team = mercy.team = 1
        sigma.team = owner.team
        x, y, z = owner.mover.position
        genji.mover.position[:] = (x, y, z + 3)
        sigma.mover.position[:] = (x, y, z + 1.5)
        mercy.mover.position[:] = (x + 3, y, z + 3)
        genji.mover.body = sigma.mover.body = mercy.mover.body = None
        fight = run.match.combat
        genji_charge = self.isolate_boost_charge(genji)
        mercy_charge = self.isolate_boost_charge(mercy)
        owner_charge = self.isolate_boost_charge(owner)
        beam = (*aim(fight._eye(mercy), (x, y + 1, z + 3)), 2)
        forward = (-32768, 5200)
        grasp_aim = (0, 0)
        for frame in range(32):
            run.step({mercy: beam, sigma: (*grasp_aim, 8 if frame == 0 else 0)})
        for frame in range(12):
            run.step({mercy: beam, genji: (*forward, 16 if frame == 0 else 0)})
        grasp = next(i for i in sigma.body_script.component.instances.values()
                     if i.graph.index == sigma_grasp.GRASP)
        deflect = next(i for i in genji.body_script.component.instances.values() if i.graph.index == 0x03A3)
        self.assertTrue(grasp.states[1].active)
        self.assertTrue(deflect.states[10].active)
        self.assertEqual(mercy_beam.factor(fight, genji), 1.3)
        shot = (*aim(fight._eye(owner), (x, y + 1, z + 3)), 1)
        own = fight.vitals[owner.body]
        defended = fight.vitals_of(next(t for t in fight._targets() if t.owner is sigma))
        inputs = {mercy: beam, genji: (*forward, 0), sigma: (*grasp_aim, 0)}

        run.step({**inputs, owner: shot})

        self.assertAlmostEqual(grasp.vars[21312].value(), 156, places=3)
        self.assertEqual((own.total, defended.total), (250, 400))
        self.assertEqual(genji.stats.value(stats.HERO_DAMAGE), 0)
        self.assertEqual(mercy.stats.value(stats.MERCY_DAMAGE_AMPLIFIED), 0)
        self.assertEqual((owner_charge.value(), genji_charge.value(), mercy_charge.value()), (0, 0, 0))
        for _ in range(150):
            if sigma.body in fight.grasp_shields:
                break
            run.step(inputs)
        self.assertFalse(grasp.states[1].active)
        self.assertTrue(deflect.states[10].active)
        self.assertAlmostEqual(defended.temporary_shields[sigma_grasp.SHIELD_TAG].now, 93.6, places=3)
        before = defended.total

        run.step({**inputs, owner: shot})

        self.assertEqual(own.total, 250)
        self.assertAlmostEqual(before - defended.total, 156, places=3)
        self.assertAlmostEqual(genji.stats.value(stats.HERO_DAMAGE), 156, places=3)
        self.assertAlmostEqual(genji_charge.value(), 120, places=3)
        self.assertAlmostEqual(mercy_charge.value(), 36, places=3)
        self.assertEqual(owner_charge.value(), 0)

    @staticmethod
    def isolate_boost_charge(player):
        script = player.body_script
        script.ult_seconds = 0
        script.component.set(12918, 0)
        script.set_ult_charge(0)
        return script.component.entity_var(ULT_CHARGE)

    def test_actual_boosted_melee_award_respects_overkill_and_armour(self):
        cases = ((29, 0, 29, 0), (32, 0, 32, 2), (200, 100, 34, 9))
        for health, armour, expected_loss, expected_bonus in cases:
            with self.subTest(health=health, armour=armour):
                run, owner, victim, mercy = self.scene()
                fight = run.match.combat
                pool = fight.vitals[victim.body] = combat.Vitals(victim.body, health, armour=armour)
                charge = self.isolate_boost_charge(mercy)
                self.beam(run, owner, victim, mercy)
                self.step(run, owner, victim, mercy, action=0x800)
                for _ in range(25):
                    self.step(run, owner, victim, mercy)
                self.assertAlmostEqual(health + armour - pool.total, expected_loss)
                self.assertAlmostEqual(charge.value(), expected_bonus, places=4)
                if pool.dead:
                    feed = next(i for i in victim.body_script.component.instances.values()
                                if i.graph.index == 0x01CF)
                    assistants = (expr.Entity(mercy.body),) if expected_bonus else ()
                    self.assertEqual(feed.vars[9296].value(), assistants)

    def test_actual_boosted_blossom_funds_mercy_and_keeps_reaper_charge_zero(self):
        run, owner, victim, mercy = self.scene()
        fight = run.match.combat
        pool = fight.vitals[victim.body] = combat.Vitals(victim.body, 1000)
        charge = self.isolate_boost_charge(mercy)
        self.beam(run, owner, victim, mercy)
        owner.body_script.set_ult_charge(2100)
        self.step(run, owner, victim, mercy, action=32)
        self.assertAlmostEqual(pool.total, 955.8)
        self.assertAlmostEqual(charge.value(), 10.2, places=4)
        self.assertEqual(owner.body_script.component.entity_var(ULT_CHARGE).value(), 0)
        for _ in range(200):
            self.step(run, owner, victim, mercy)
        self.assertAlmostEqual(pool.total, 337)
        self.assertAlmostEqual(charge.value(), 153, places=3)

    def test_actual_boosted_shell_reaps_only_available_health(self):
        run, owner, victim, mercy = self.scene()
        fight = run.match.combat
        own = fight.vitals[owner.body]
        own.damage(100)
        pool = fight.vitals[victim.body]
        pool.damage(pool.total - 130)
        self.beam(run, owner, victim, mercy)
        self.step(run, owner, victim, mercy, action=1)
        self.assertTrue(pool.dead)
        self.assertEqual(pool.killer, owner.body)
        self.assertAlmostEqual(own.total, 150 + 130 * 0.35, places=4)

    def fortified_blossom_scene(self):
        run, owner, orisa, mercy = self.scene(names=("Reaper", "Orisa", "Mercy"))
        self.assertTrue(run.match.switch_hero(orisa, find_hero("Orisa")))
        run.step({orisa: (0, 0, 8)})
        fight = run.match.combat
        target = next(target for target in fight._targets() if target.owner is orisa)
        self.assertAlmostEqual(fight._fortify_scale(target), 0.6)
        return run, owner, orisa, mercy, target

    def blossom_beam_fortify_trace(self, duration, *, skipped):
        run, owner, orisa, mercy, _ = self.fortified_blossom_scene()
        fight = run.match.combat
        expiry = run.frame + duration
        while run.frame < expiry - 81:
            run.step({})
        self.beam(run, owner, orisa, mercy)
        own = fight.vitals[owner.body]
        own.damage(240)
        pool = fight.vitals[orisa.body] = combat.Vitals(orisa.body, 1000)
        boost_charge = self.isolate_boost_charge(mercy)
        script = owner.body_script
        script.ult_seconds = 0
        script.set_ult_charge(script.component.entity_var(ULT_COST).value())
        pulses = []
        damage = fight._damage

        def observe(shooter, victim, amount, *args, **kwargs):
            before = pool.total
            result = damage(shooter, victim, amount, *args, **kwargs)
            if shooter is owner and victim.owner is orisa:
                pulses.append((fight.tick, before - pool.total))
            return result

        with patch.object(fight, "_damage", side_effect=observe):
            self.step(run, owner, orisa, mercy, action=32)
            self.assertEqual(run.frame, expiry - 65)
            end = run.frame + 200
            for _ in range(26):
                self.step(run, owner, orisa, mercy)
            self.assertEqual(len(pulses), 3)
            self.assertAlmostEqual(boost_charge.value(), 18.36, places=4)
            frame = mercy.commands.last_frame
            while run.frame < end:
                run.frame = end if skipped else run.frame + 1
                run.match.update(run.frame * combat.TICK, run.frame, frames=False)
        self.assertEqual(mercy.commands.last_frame, frame)
        expected = [26.52] * 3 + [20.4] * 3 + [34] * 9
        self.assertEqual(len(pulses), len(expected))
        for (tick, lost), amount in zip(pulses, expected, strict=True):
            self.assertAlmostEqual(lost, amount, places=4)
            self.assertEqual(tick < expiry, amount != 34)
        self.assertAlmostEqual(1000 - pool.total, 446.76, places=4)
        self.assertAlmostEqual(own.total, 10 + 446.76 * reaper.REAPING_RATIO, places=4)
        self.assertAlmostEqual(boost_charge.value(), 18.36, places=4)
        self.assertEqual(script.component.entity_var(ULT_CHARGE).value(), 0)
        self.assertIsNone(reaper.blossom_state(script))
        return pulses, own.total, boost_charge.value()

    def test_actual_blossom_credits_mitigated_beam_bonus_across_fortify_expiry(self):
        run, _, _, _, target = self.fortified_blossom_scene()
        start = run.frame
        for _ in range(270):
            run.frame += 1
            run.match.update(run.frame * combat.TICK, run.frame, frames=False)
            if run.match.combat._fortify_scale(target) is None:
                break
        self.assertIsNone(run.match.combat._fortify_scale(target))
        duration = run.frame - start
        stepped = self.blossom_beam_fortify_trace(duration, skipped=False)
        self.assertEqual(stepped, self.blossom_beam_fortify_trace(duration, skipped=True))

    def test_actual_full_boosted_blossom_spends_663_without_damage_charge(self):
        run, owner, victim, mercy = self.scene()
        fight = run.match.combat
        pool = combat.Vitals(victim.body, 1000)
        fight.vitals[victim.body] = pool
        self.beam(run, owner, victim, mercy)
        fight.vitals[owner.body].damage(100)
        owner.body_script.set_ult_charge(2100)
        self.step(run, owner, victim, mercy, action=32)
        self.assertAlmostEqual(pool.total, 955.8, places=4)
        self.assertAlmostEqual(fight.vitals[owner.body].total, 165.47, places=4)
        for _ in range(200):
            self.step(run, owner, victim, mercy)
        self.assertTrue(fight.reaper_blossoms[owner.body]["ended"])
        self.assertAlmostEqual(pool.total, 337, places=4)
        self.assertLess(owner.body_script.component.vars[342].value(), 30)

    def test_actual_boosted_pellets_reap_post_armour_loss(self):
        run, owner, victim, mercy = self.scene()
        fight = run.match.combat
        own = fight.vitals[owner.body]
        own.damage(100)
        pool = combat.Vitals(victim.body, 200, armour=100)
        fight.vitals[victim.body] = pool
        self.beam(run, owner, victim, mercy)
        self.step(run, owner, victim, mercy, action=1)
        self.assertAlmostEqual(pool.total, 300 - 78)
        self.assertAlmostEqual(own.total, 150 + 78 * 0.35, places=4)

    def test_actual_boosted_quick_melee_deals_39_and_reaps_actual_damage(self):
        run, owner, victim, mercy = self.scene()
        fight = run.match.combat
        own = fight.vitals[owner.body]
        own.damage(100)
        pool = fight.vitals[victim.body]
        before = pool.total
        self.beam(run, owner, victim, mercy)
        self.step(run, owner, victim, mercy, action=0x800)
        for _ in range(25):
            self.step(run, owner, victim, mercy)
        self.assertAlmostEqual(pool.total, before - 39)
        self.assertAlmostEqual(own.total, 150 + 39 * 0.35, places=4)

    def test_actual_boosted_shell_spends_156_from_shield_without_reaping(self):
        run, owner, victim, target, shield = barrier_tests.ReaperWinstonBarrierTests().dome(allied=True)
        mercy = run.players[2]
        self.assertTrue(run.match.switch_hero(mercy, heroes()[mercy_beam.HERO]))
        owner.team = mercy.team = 1
        x, y, z = owner.mover.position
        mercy.mover.position[:] = (x - 3, y, z)
        mercy.mover.body = None
        fight = run.match.combat
        own = fight.vitals_of(next(t for t in fight._targets() if t.owner is owner))
        own.damage(100)
        charge = self.isolate_boost_charge(mercy)
        self.beam(run, owner, victim, mercy)
        pool = fight.vitals_of(target)
        before = pool.total
        self.step(run, owner, victim, mercy, action=1)
        self.assertAlmostEqual(shield.health, 544, places=3)
        self.assertEqual(pool.total, before)
        self.assertEqual(own.total, 150)
        self.assertEqual(owner.client.messages(combat.HIT), [])
        self.assertEqual(charge.value(), 0)
        self.assertEqual(mercy.stats.value(stats.MERCY_DAMAGE_AMPLIFIED), 0)

    def test_actual_wall_breaks_held_beam_without_blocking_reaper_shell(self):
        run, owner, victim, mercy = self.scene()
        fight = run.match.combat
        pool = combat.Vitals(victim.body, 600)
        fight.vitals[victim.body] = pool
        self.beam(run, owner, victim, mercy)
        x, y, z = owner.mover.position
        wall = x - 1.5
        run.match.collision_world = World([
            (x-30,y,z-30,x-30,y,z+30,x+30,y,z+30),
            (x-30,y,z-30,x+30,y,z+30,x+30,y,z-30),
            (wall,y,z-8,wall,y+5,z-8,wall,y+5,z+8),
            (wall,y,z-8,wall,y+5,z+8,wall,y,z+8),
        ], name="Mercy beam cover")
        for _ in range(30):
            self.step(run, owner, victim, mercy)
        self.assertEqual(mercy_beam.factor(fight, owner), 1)
        self.step(run, owner, victim, mercy, action=1)
        self.assertAlmostEqual(pool.total, 480)

    def test_actual_beam_release_during_blossom_restores_later_pulse_damage(self):
        run, owner, victim, mercy = self.scene()
        fight = run.match.combat
        pool = combat.Vitals(victim.body, 1000)
        fight.vitals[victim.body] = pool
        self.beam(run, owner, victim, mercy)
        owner.body_script.set_ult_charge(2100)
        self.step(run, owner, victim, mercy, action=32)
        self.assertAlmostEqual(pool.total, 955.8)
        for _ in range(200):
            self.step(run, owner, victim, mercy, beam=0)
        self.assertAlmostEqual(pool.total, 1000 - (44.2 + 14 * 34))

    def test_two_actual_blue_beams_do_not_stack_on_reaper_shell(self):
        run, owner, victim, mercy = self.scene(names=("Reaper", "Enemy", "Mercy", "Second Mercy"))
        second = run.players[3]
        self.assertTrue(run.match.switch_hero(second, heroes()[mercy_beam.HERO]))
        second.team = owner.team
        charge = self.isolate_boost_charge(mercy)
        second_charge = self.isolate_boost_charge(second)
        x, y, z = owner.mover.position
        second.mover.position[:] = (x + 3, y, z)
        second.mover.body = None
        fight = run.match.combat
        pool = combat.Vitals(victim.body, 600)
        fight.vitals[victim.body] = pool
        beams = {p: (*aim(fight._eye(p), (x, y + 1, z)), 2) for p in (mercy, second)}
        for _ in range(30):
            run.step(beams)
        for caster in (mercy, second):
            instance = next(i for i in caster.body_script.component.instances.values()
                            if i.graph.index == mercy_beam.STAFF)
            state = instance.states[35]
            self.assertTrue(state.active)
            self.assertEqual(state.evaluate(state.node.field("m_targets")), expr.Entity(owner.body))
        x, y, z = victim.mover.position
        run.step({**beams, owner: (*aim(fight._eye(owner), (x, y + 1, z)), 1)})
        self.assertAlmostEqual(pool.total, 444)
        self.assertAlmostEqual(charge.value(), 36, places=4)
        self.assertEqual(second_charge.value(), 0)

    def test_actual_held_beam_releases_on_world_time_without_caster_input(self):
        run, owner, victim, mercy = self.scene()
        fight = run.match.combat
        self.beam(run, owner, victim, mercy)
        pool = combat.Vitals(victim.body, 600)
        fight.vitals[victim.body] = pool
        for _ in range(30):
            run.frame += 1
            run.match.update(run.frame * combat.TICK, run.frame)
        self.assertEqual(mercy_beam.factor(fight, owner), 1)
        self.step(run, owner, victim, mercy, beam=0, action=1)
        self.assertAlmostEqual(pool.total, 480)
