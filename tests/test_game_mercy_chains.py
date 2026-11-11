"""Actual four-player support effects through000F and extracted chain graphs.

Neutral healing inputs, independent effect VMs and sphere/static-LOS selection
are explicit reconstruction policies; full native filters/status/transport stay open.
"""

import unittest
from dataclasses import replace
from pathlib import Path
from unittest.mock import patch

import test_game_mercy_npc as npc_tests
from test_game_combat import Run, aim
from test_game_mover import FLOOR, wall

from ow174.game import bodytrace, movelog, npc, npc_control, stats
from ow174.game.body_vm_snapshot import export_component_vm, snapshot_digest
from ow174.game.collision import World
from ow174.game.commands import FIRE, SECONDARY, Command
from ow174.game.content import PRACTICE_RANGE, Spawn, heroes
from ow174.game.script import expr, graph, support_effects
from ow174.game.script.driver import ULT_CHARGE
from ow174.game.script.regeneration import MERCY

def clear_graphs():
    graph._data.cache_clear()
    graph._records.cache_clear()
    graph.graph.cache_clear()

SCENE = replace(PRACTICE_RANGE, spawns=(
    Spawn(0, (0.0, 1.0, 0.0), 0.0), Spawn(1, (0.0, 1.0, 5.0), 0.0),
    Spawn(2, (5.0, 1.0, 5.0), 0.0), Spawn(3, (5.0, 1.0, 15.0), 0.0),
))


@unittest.skipUnless(graph.effect_script(support_effects.BOOST), "prepared Mercy effect graphs required")
class MercyChainTests(unittest.TestCase):
    def setUp(self):
        for diagnostic in (bodytrace, movelog):
            self.enterContext(patch.object(diagnostic, "ENABLED", False))

        clear_graphs()
        self.addCleanup(clear_graphs)
        self.run = Run(SCENE, ("Mercy", "Anchor", "Chain", "Enemy"))
        self.mercy, self.anchor, self.chain, self.enemy = self.run.players
        self.anchor.team = self.chain.team = self.mercy.team = 0
        self.enemy.team = 1
        self.match = self.run.match
        self.match.bots.clear()
        self.match._pending_bot_scripts.clear()
        self.match.switch_hero(self.mercy, heroes()[MERCY])
        self.run.step({})
        self.fight = self.match.combat
        self.target = next(t for t in self.fight._targets() if t.owner is self.chain)
        for player in (self.anchor, self.chain):
            target = next(t for t in self.fight._targets() if t.owner is player)
            self.fight._damage(self.enemy, target, 100, False, None, self.run.frame)

    def step(self, count, *, beam=FIRE, attack=0):
        for _ in range(count):
            point = self.anchor.mover.position
            yaw, pitch = aim(self.fight._eye(self.mercy), (point[0], point[1] + 1, point[2]))
            point = self.enemy.mover.position
            ayaw, apitch = aim(self.fight._eye(self.chain), (point[0], point[1] + 0.8, point[2]))
            self.run.step({self.mercy: (yaw, pitch, beam), self.chain: (ayaw, apitch, attack)})

    def start(self):
        self.step(35, beam=0)
        self.mercy.body_script.set_ult_charge(1820)
        self.step(1, beam=0x20)
        self.step(35, beam=0)

    def health(self, player):
        target = next(t for t in self.fight._targets() if t.owner is player)
        return self.fight.vitals_of(target).total

    def test_idle_world_expiry_removes_secondary_boost_before_the_next_ally_attack(self):
        self.start()
        self.step(10, beam=SECONDARY)
        self.step(1, beam=SECONDARY, attack=FIRE)
        self.step(10, beam=SECONDARY)
        self.assertAlmostEqual(self.health(self.enemy), 174, places=4)
        owner_frame = self.mercy.body_script.frame
        self.run.frame += 900
        self.match.update(self.run.frame * 0.016, self.run.frame)
        self.assertEqual(self.mercy.body_script.frame, owner_frame)
        items = self.fight.support_effects.attachments.values()
        self.assertEqual({item.definition for item in items}, {support_effects.BOOST})
        x, y, z = self.enemy.mover.position
        for shooter, expected in ((self.chain, 154), (self.anchor, 128)):
            yaw, pitch = aim(self.fight._eye(shooter), (x, y + 0.8, z))
            shooter.take_commands([Command(self.run.frame, yaw=yaw, pitch=pitch, buttons=FIRE)])
            self.assertAlmostEqual(self.health(self.enemy), expected, places=4)
        self.assertEqual(self.mercy.body_script.frame, owner_frame)

    def test_idle_world_expiry_retires_secondary_healing_without_ticking_the_staff(self):
        self.start()
        self.step(20)
        attachments = self.fight.support_effects.attachments
        self.assertEqual({item.definition for item in attachments.values()},
                         {support_effects.HEAL, support_effects.CHAIN_HEAL})
        before = self.health(self.anchor), self.health(self.chain)
        owner_frame = self.mercy.body_script.frame
        self.run.frame += 900
        self.match.update(self.run.frame * 0.016, self.run.frame)
        self.assertEqual(self.mercy.body_script.frame, owner_frame)
        self.assertEqual((self.health(self.anchor), self.health(self.chain)), before)
        self.assertEqual({item.definition for item in attachments.values()}, {support_effects.HEAL})

    def healing_across_expiry(self, *, late):
        self.start()
        script = self.mercy.body_script
        ultimate = next(i for i in script.component.instances.values() if i.graph.index == 0x10DA)
        start = ultimate.state(2).start // script.frame_ms
        self.step(start + 925 - self.run.frame, beam=0)
        charge = script.component.entity_var(ULT_CHARGE)
        if late:
            self.run.frame = start + 958
            self.match.update(self.run.frame * 0.016, self.run.frame)
            self.assertFalse(script.component.entity_var(842).value())
        x, y, z = self.anchor.mover.position
        yaw, pitch = aim(self.fight._eye(self.mercy), (x, y + 1, z))
        trace = []
        for frame in range(start + 926, start + 948):
            if not late:
                self.match.update(frame * 0.016, frame)
            self.mercy.take_commands([Command(frame, yaw=yaw, pitch=pitch, buttons=FIRE)])
            trace.append((self.health(self.anchor), self.health(self.chain), charge.value()))
            if late:
                self.assertFalse(script.component.entity_var(842).value())
                items = self.fight.support_effects.attachments.values()
                self.assertEqual({item.definition for item in items}, {support_effects.HEAL})
        return trace

    def test_late_staff_matches_timely_healing_and_charge_across_world_expiry(self):
        timely = self.healing_across_expiry(late=False)
        other = MercyChainTests()
        self.addCleanup(other.doCleanups)
        other.setUp()
        late = other.healing_across_expiry(late=True)
        self.assertAlmostEqual(timely[0][0], 100.96, places=4)
        self.assertAlmostEqual(timely[0][1], 100.96, places=4)
        self.assertEqual(timely[0][2], 0)
        self.assertGreater(timely[-1][2], 0)
        self.assertEqual(timely[-1][1], timely[-2][1])
        for timely_frame, late_frame in zip(timely, late, strict=True):
            for expected, actual in zip(timely_frame, late_frame, strict=True):
                self.assertAlmostEqual(actual, expected, places=4)

    def boost_across_expiry(self, *, late, projectile=False, shot_first=False, timeout=False):
        self.start()
        script = self.mercy.body_script
        ultimate = next(i for i in script.component.instances.values() if i.graph.index == 0x10DA)
        start = ultimate.state(2).start // script.frame_ms
        self.step(start + 925 - self.run.frame, beam=0)
        x, y, z = self.anchor.mover.position
        yaw, pitch = aim(self.fight._eye(self.mercy), (x, y + 1, z))
        x, y, z = self.enemy.mover.position
        shot_yaw, shot_pitch = aim(self.fight._eye(self.chain), (x, y + 0.8, z))
        staff = Command(start + 926, yaw=yaw, pitch=pitch, buttons=SECONDARY)
        release = Command(start + 928)
        shot = Command(start + 927, yaw=shot_yaw, pitch=shot_pitch, buttons=SECONDARY if projectile else FIRE)
        world_frame = start + (958 if late else 927)
        self.match.update(world_frame * 0.016, world_frame)
        if shot_first:
            self.chain.take_commands([shot])
            self.chain.take_commands([shot])
            self.assertEqual(self.health(self.enemy), 200)
            self.assertFalse(self.fight.rockets)
            self.assertEqual(len(self.fight.pending_attacks), 1)
            if timeout:
                deadline = self.fight.pending_attacks[0].deadline
                self.match.update((deadline - 1) * 0.016, deadline - 1)
                self.assertEqual(self.health(self.enemy), 200)
                self.match.update(deadline * 0.016, deadline)
                self.assertFalse(self.fight.pending_attacks)
                self.assertEqual(self.health(self.enemy), 180)
        self.mercy.take_commands([staff, replace(staff, frame=shot.frame)])
        if late:
            self.assertFalse(script.component.entity_var(842).value())
            self.assertFalse(any(item.definition == support_effects.CHAIN_BOOST
                                 for item in self.fight.support_effects.attachments.values()))
            self.mercy.take_commands([release])
        if not shot_first:
            self.chain.take_commands([shot])
        self.assertFalse(self.fight.pending_attacks)
        if projectile:
            rocket = next(rocket for rocket in self.fight.rockets if rocket.shooter is self.chain)
            self.assertAlmostEqual(rocket.direct.amount, 52, places=4)
            self.assertAlmostEqual(rocket.blast.amount, 104, places=4)
            for frame in range(world_frame + 1, world_frame + 31):
                self.match.update(frame * 0.016, frame)
                if rocket not in self.fight.rockets:
                    break
            self.assertFalse(script.component.entity_var(842).value())
        self.match.deliver_body_messages()
        self.match.deliver_body_messages()
        return (self.health(self.enemy), self.mercy.stats.value(0x086000000000048A),
                script.component.entity_var(ULT_CHARGE).value())

    def test_late_rifle_uses_recorded_secondary_bonus_even_after_staff_release(self):
        timely = self.boost_across_expiry(late=False)
        other = MercyChainTests()
        self.addCleanup(other.doCleanups)
        other.setUp()
        late = other.boost_across_expiry(late=True)
        self.assertAlmostEqual(timely[0], 174, places=4)
        self.assertAlmostEqual(timely[1], 6, places=4)
        self.assertEqual(timely[2], 0)
        for actual, expected in zip(late, timely, strict=True):
            self.assertAlmostEqual(actual, expected, places=4)

    def test_late_helix_keeps_secondary_launch_bonus_and_awards_at_impact(self):
        timely = self.boost_across_expiry(late=False, projectile=True)
        other = MercyChainTests()
        self.addCleanup(other.doCleanups)
        other.setUp()
        late = other.boost_across_expiry(late=True, projectile=True)
        for trace in (timely, late):
            for actual, expected in zip(trace, (44, 36, 36), strict=True):
                self.assertAlmostEqual(actual, expected, places=4)

    def test_attack_first_waits_for_staff_then_applies_actual_secondary_rifle_bonus(self):
        trace = self.boost_across_expiry(late=True, shot_first=True)
        for actual, expected in zip(trace, (174, 6, 0), strict=True):
            self.assertAlmostEqual(actual, expected, places=4)

    def test_attack_first_helix_waits_for_launch_bonus_and_keeps_impact_award(self):
        trace = self.boost_across_expiry(late=True, projectile=True, shot_first=True)
        for actual, expected in zip(trace, (44, 36, 36), strict=True):
            self.assertAlmostEqual(actual, expected, places=4)

    def test_attack_first_timeout_commits_base_damage_once_without_the_missing_staff_frame(self):
        self.assertEqual(self.boost_across_expiry(late=True, shot_first=True, timeout=True), (180, 0, 0))

    def test_waiting_attack_does_not_pick_up_another_casters_new_live_beam(self):
        self.match.switch_hero(self.anchor, heroes()[MERCY])
        self.start()
        script = self.mercy.body_script
        ultimate = next(i for i in script.component.instances.values() if i.graph.index == 0x10DA)
        start = ultimate.state(2).start // script.frame_ms
        self.step(start + 925 - self.run.frame, beam=0)
        world_frame = start + 958
        self.match.update(world_frame * 0.016, world_frame)
        x, y, z = self.enemy.mover.position
        yaw, pitch = aim(self.fight._eye(self.chain), (x, y + 0.8, z))
        shot = Command(start + 927, yaw=yaw, pitch=pitch, buttons=FIRE)
        self.chain.take_commands([shot])
        self.assertEqual(len(self.fight.pending_attacks), 1)
        x, y, z = self.chain.mover.position
        yaw, pitch = aim(self.fight._eye(self.anchor), (x, y + 1, z))
        self.anchor.take_commands([Command(world_frame, yaw=yaw, pitch=pitch, buttons=SECONDARY)])
        self.assertAlmostEqual(self.fight.damage_scale(self.chain), 1.3, places=4)
        self.mercy.take_commands([Command(shot.frame)])
        self.assertFalse(self.fight.pending_attacks)
        self.assertEqual(self.health(self.enemy), 180)

    def test_attack_first_melee_uses_original_swing_and_secondary_bonus(self):
        self.assert_attack_first_melee_resolves()

    def test_late_valkyrie_boost_survives_melee_attackers_later_death(self):
        self.assertTrue(self.match.switch_hero(self.chain, heroes()[MERCY]))
        self.chain.body_script.component.set(12918, 0.0)
        self.assert_attack_first_melee_resolves(lethal_target=self.chain)

    def test_late_valkyrie_boost_survives_primary_targets_later_death(self):
        self.assert_attack_first_melee_resolves(lethal_target=self.anchor)

    def assert_attack_first_melee_resolves(self, *, lethal_target=None):
        self.start()
        script = self.mercy.body_script
        ultimate = next(i for i in script.component.instances.values() if i.graph.index == 0x10DA)
        start = ultimate.state(2).start // script.frame_ms
        x, y, z = self.chain.mover.position
        self.enemy.mover.position[:] = (x, y, z + 1.4)
        self.enemy.mover.body = None
        self.step(start + 911 - self.run.frame, beam=0)
        if lethal_target is not None:
            target = next(t for t in self.fight._targets() if t.owner is lethal_target)
            victim = self.fight.vitals_of(target)
            self.fight._damage(self.enemy, target, victim.total - 15, False, None, self.run.frame)
        if lethal_target is self.chain:
            attacker_charge = self.chain.body_script.component.entity_var(ULT_CHARGE)
            charge_before = attacker_charge.value()
        world_frame = start + 958
        self.match.update(world_frame * 0.016, world_frame)
        x, y, z = self.enemy.mover.position
        yaw, pitch = aim(self.fight._eye(self.chain), (x, y + 1, z))
        self.chain.take_commands([
            Command(frame, yaw=yaw, pitch=pitch, buttons=0x800 if frame == start + 912 else 0)
            for frame in range(start + 912, start + 934)
        ])
        self.assertEqual(self.health(self.enemy), 200)
        attack, = self.fight.pending_attacks
        if lethal_target is not None:
            x, y, z = lethal_target.mover.position
            yaw, pitch = aim(self.fight._eye(self.enemy), (x, y + 1, z))
            self.enemy.take_commands([Command(world_frame, yaw=yaw, pitch=pitch, buttons=FIRE)])
            self.assertTrue(victim.dead)
            self.assertEqual(victim.death_tick, world_frame)
            self.assertEqual(victim.killer, self.enemy.body)
            self.assertLess(attack.frame, victim.death_tick)
            self.assertEqual(self.fight.pending_attacks, [attack])
            self.assertEqual(self.health(self.enemy), 200)
        x, y, z = self.anchor.mover.position
        yaw, pitch = aim(self.fight._eye(self.mercy), (x, y + 1, z))
        self.mercy.take_commands([
            Command(frame, yaw=yaw, pitch=pitch, buttons=SECONDARY)
            for frame in range(start + 912, attack.frame + 1)
        ])
        self.assertFalse(self.fight.pending_attacks)
        self.assertAlmostEqual(self.health(self.enemy), 161, places=4)
        if lethal_target is not None:
            self.assertAlmostEqual(self.chain.stats.value(stats.HERO_DAMAGE), 39, places=4)
            if lethal_target is self.chain:
                self.assertEqual(attacker_charge.value() - charge_before, 30)
            self.assertFalse(any(lethal_target.body in (item.target, item.anchor)
                                 for item in self.fight.support_effects.attachments.values()))
            self.match.update((world_frame + 1) * 0.016, world_frame + 1)
            self.assertAlmostEqual(self.health(self.enemy), 161, places=4)

    def test_late_melee_keeps_committed_boost_after_casters_later_death(self):
        self.assert_committed_boost_at_caster_death(attack_offset=0, death_offset=30, expected=39)

    def test_late_melee_at_casters_death_frame_does_not_use_the_old_boost(self):
        self.assert_committed_boost_at_caster_death(attack_offset=0, death_offset=0, expected=30)

    def test_late_melee_cannot_use_an_unprocessed_caster_frame_after_death(self):
        self.assert_committed_boost_at_caster_death(attack_offset=5, death_offset=30, expected=30)

    def test_waiting_melee_discards_unprocessed_boost_when_the_caster_dies(self):
        self.assert_committed_boost_at_caster_death(
            attack_offset=5, death_offset=30, expected=30, attack_first=True,
        )

    def assert_committed_boost_at_caster_death(self, *, attack_offset, death_offset, expected, attack_first=False):
        self.start()
        x, y, z = self.chain.mover.position
        self.enemy.mover.position[:] = (x, y, z + 1.4)
        self.enemy.mover.body = None
        self.step(15, beam=SECONDARY)
        beam_frame = self.run.frame + 1
        attack_frame = beam_frame + attack_offset
        death_frame = beam_frame + death_offset
        x, y, z = self.anchor.mover.position
        yaw, pitch = aim(self.fight._eye(self.mercy), (x, y + 1, z))
        self.mercy.take_commands([Command(beam_frame, yaw=yaw, pitch=pitch, buttons=SECONDARY)])
        self.assertAlmostEqual(self.fight.damage_scale(self.chain), 1.3, places=4)
        target = next(t for t in self.fight._targets() if t.owner is self.mercy)
        caster = self.fight.vitals_of(target)
        self.fight._damage(self.enemy, target, caster.total - 15, False, None, beam_frame)
        x, y, z = self.enemy.mover.position
        yaw, pitch = aim(self.fight._eye(self.chain), (x, y + 1, z))
        attack_commands = [
            Command(frame, yaw=yaw, pitch=pitch, buttons=0x800 if frame == attack_frame else 0)
            for frame in range(attack_frame, attack_frame + 22)
        ]
        charge = self.mercy.body_script.component.entity_var(ULT_CHARGE)
        charge_before = charge.value()
        if attack_first:
            self.match.update((death_frame - 1) * 0.016, death_frame - 1)
            self.chain.take_commands(attack_commands)
            pending, = self.fight.pending_attacks
            self.assertEqual(pending.frame, attack_frame)
            self.assertIn(self.mercy.body_script, pending.waiting)
            self.assertEqual(self.health(self.enemy), 200)
        self.match.update(death_frame * 0.016, death_frame)
        x, y, z = self.mercy.mover.position
        yaw, pitch = aim(self.fight._eye(self.enemy), (x, y + 1, z))
        self.enemy.take_commands([Command(death_frame, yaw=yaw, pitch=pitch, buttons=FIRE)])
        self.assertEqual(caster.death_tick, death_frame)
        self.assertEqual(caster.killer, self.enemy.body)
        self.assertEqual(self.fight.damage_scale(self.chain), 1)
        self.assertFalse(any(item.source == self.mercy.body
                             for item in self.fight.support_effects.attachments.values()))
        world_frame = max(death_frame, attack_frame + 30)
        self.match.update(world_frame * 0.016, world_frame)
        if not attack_first:
            self.chain.take_commands(attack_commands)
        self.match.deliver_body_messages()
        self.assertFalse(self.fight.pending_attacks)
        self.assertAlmostEqual(self.health(self.enemy), 200 - expected, places=4)
        self.assertAlmostEqual(self.chain.stats.value(stats.HERO_DAMAGE), expected, places=4)
        self.assertAlmostEqual(self.mercy.stats.value(0x086000000000048A), expected - 30, places=4)
        self.assertEqual(charge.value(), charge_before)

    def test_actual_primary_and_chain_restore_sixty_per_second_from_real_graphs_and_source(self):
        self.start()
        self.step(50)
        self.assertAlmostEqual(self.health(self.anchor), 148, places=4)
        self.assertAlmostEqual(self.health(self.chain), 148, places=4)
        self.assertAlmostEqual(self.mercy.stats.value(stats.HEALING), 96, places=4)
        items = list(self.fight.support_effects.attachments.values())
        self.assertEqual({a.instance.graph.index for a in items}, {0x0079, 0x10DC})
        child = next(a for a in items if a.definition == support_effects.CHAIN_HEAL)
        self.assertTrue(child.instance.state(0).active)
        self.assertEqual(child.instance.find_var(0, 40).value(), expr.Entity(self.anchor.body))
        self.assertEqual(child.instance.find_var(0, 42).value(), expr.Entity(self.mercy.body))
        self.assertEqual(child.instance.find_var(0, 499).value(), 60)

    def test_without_valkyrie_only_direct_target_heals_and_no_chain_is_mounted(self):
        self.step(50)
        self.assertAlmostEqual(self.health(self.anchor), 144, places=4)
        self.assertEqual(self.health(self.chain), 100)
        self.assertEqual({a.instance.graph.index for a in self.fight.support_effects.attachments.values()},
                         {0x0079})

    def test_chained_damage_boost_uses_original_filter_and_actual_rifle_damage(self):
        self.start()
        self.step(10, beam=SECONDARY)
        self.step(1, beam=SECONDARY, attack=FIRE)
        self.step(10, beam=SECONDARY)
        self.assertAlmostEqual(self.health(self.enemy), 174, places=4)
        self.assertAlmostEqual(self.chain.stats.value(stats.HERO_DAMAGE), 26, places=4)
        child = next(a for a in self.fight.support_effects.attachments.values()
                     if a.definition == support_effects.CHAIN_BOOST)
        self.assertTrue(child.instance.state(4).active)
        self.assertEqual(child.instance.find_var(0, 42).value(), expr.Entity(self.mercy.body))
        self.assertEqual(self.mercy.stats.value(stats.HERO_DAMAGE), 0)
        self.assertEqual(self.health(self.chain), 100)

    def test_chained_projectile_keeps_launch_bonus_after_beam_release(self):
        self.start()
        self.step(10, beam=SECONDARY)
        self.step(1, beam=SECONDARY, attack=SECONDARY)
        rocket = next(r for r in self.fight.rockets if r.shooter is self.chain)
        self.assertAlmostEqual(rocket.direct.amount, 52, places=4)
        self.assertAlmostEqual(rocket.blast.amount, 104, places=4)
        self.step(20, beam=0)
        self.assertAlmostEqual(self.health(self.enemy), 44, places=3)
        self.assertFalse(self.fight.support_effects.attachments)

    def test_chain_range_loss_and_reacquisition_use_live_target_body(self):
        self.start()
        self.step(10)
        before = self.health(self.chain)
        self.chain.spawn = ((20.0, 1.0, 5.0), 0.0)
        self.chain.mover = self.chain.new_mover()
        self.step(10)
        self.assertEqual(self.health(self.chain), before)
        self.assertEqual(len(self.fight.support_effects.attachments), 1)
        self.chain.spawn = ((5.0, 1.0, 5.0), 0.0)
        self.chain.mover = self.chain.new_mover()
        self.step(10)
        self.assertAlmostEqual(self.health(self.chain) - before, 9.6, places=4)
        self.assertEqual(len(self.fight.support_effects.attachments), 2)

    def test_static_wall_stops_chained_healing_but_not_the_visible_primary(self):
        self.match.collision_world = World(FLOOR + wall(2.5, 0, 2.5, 10))
        for player in self.run.players:
            player.mover.use(world=self.match.collision_world)
        self.start()
        self.step(50)
        self.assertAlmostEqual(self.health(self.anchor), 148, places=4)
        self.assertEqual(self.health(self.chain), 100)

    def test_chain_does_not_repeat_from_the_secondary_target_or_heal_caster(self):
        self.enemy.team = 0
        self.enemy.mover.position[:] = (14, 1, 5)
        target = next(t for t in self.fight._targets() if t.owner is self.enemy)
        self.fight.vitals_of(target).damage(100)
        self.fight._sync_body_health(self.enemy, self.fight.vitals_of(target))
        self.start()
        self.step(50)
        self.assertAlmostEqual(self.health(self.chain), 148, places=4)
        self.assertEqual(self.health(self.enemy), 100)
        self.assertFalse(any(a.target == self.mercy.body
                             for a in self.fight.support_effects.attachments.values()))

    def test_anchor_death_immediately_retires_root_and_child_effects(self):
        self.start()
        self.step(10)
        before = self.health(self.chain)
        target = next(t for t in self.fight._targets() if t.owner is self.anchor)
        self.fight._damage(self.enemy, target, 500, False, None, self.run.frame)
        self.assertFalse(self.fight.support_effects.attachments)
        self.step(20)
        self.assertEqual(self.health(self.chain), before)

    def test_attached_effect_vm_exports_real_graph_state_and_causal_parameter_changes(self):
        self.start()
        self.step(10)
        child = next(a for a in self.fight.support_effects.attachments.values()
                     if a.definition == support_effects.CHAIN_HEAL)
        before = snapshot_digest(export_component_vm(child.component))
        child.instance.write((0, 499), 59.0)
        self.assertNotEqual(snapshot_digest(export_component_vm(child.component)), before)

    def test_missing_effect_pack_keeps_direct_heal_but_does_not_invent_chains(self):
        self.start()
        with patch.object(graph, "effect_script", return_value=None):
            self.step(50)
        self.assertAlmostEqual(self.health(self.anchor), 148, places=4)
        self.assertEqual(self.health(self.chain), 100)
        self.assertFalse(self.fight.support_effects.attachments)

    def test_release_and_repress_retire_and_recreate_actual_effect_graphs(self):
        self.start()
        self.step(10)
        old = [a.component for a in self.fight.support_effects.attachments.values()]
        before = self.health(self.chain)
        self.step(10, beam=0)
        self.assertEqual(self.health(self.chain), before)
        self.assertFalse(self.fight.support_effects.attachments)
        self.assertTrue(all(not component.instances for component in old))
        self.step(10)
        self.assertAlmostEqual(self.health(self.chain) - before, 9.6, places=4)
        self.assertTrue(all(a.component not in old for a in self.fight.support_effects.attachments.values()))

    def test_hostile_secondary_is_not_healed_or_given_a_boost(self):
        self.chain.team = 1
        self.start()
        self.step(10)
        self.assertEqual(self.health(self.chain), 100)
        self.step(10, beam=SECONDARY)
        self.assertEqual(self.fight.damage_scale(self.chain), 1)

    def test_valkyrie_end_retires_chains_and_refreshes_primary_amount_without_restarting_it(self):
        self.start()
        self.step(10)
        root = next(a for a in self.fight.support_effects.attachments.values()
                    if a.definition == support_effects.HEAL)
        counter = root.instance.state(0).counter
        self.step(950)
        self.assertFalse(self.mercy.body_script.component.entity_var(842).value())
        self.assertEqual(len(self.fight.support_effects.attachments), 1)
        self.assertIs(next(iter(self.fight.support_effects.attachments.values())), root)
        self.assertEqual(root.instance.state(0).counter, counter)
        self.assertEqual(root.instance.find_var(0, 499).value(), 55)
        target = next(t for t in self.fight._targets() if t.owner is self.anchor)
        self.fight._damage(self.enemy, target, 100, False, None, self.run.frame)
        self.step(10)
        self.assertAlmostEqual(self.health(self.anchor), 108.8, places=4)

    def test_source_death_retires_all_attachments_without_later_healing(self):
        shooter = self.enemy
        self.assertTrue(self.match.switch_hero(shooter, heroes()[0x02E0000000000042]))
        shooter.mover.position[:] = (4, 1, 0)
        shooter.mover.body = self.mercy.mover.body = None
        self.start()
        self.step(10)
        self.assertEqual(self.mercy.mover.flight_scale, 1)
        self.assertEqual(len(self.fight.support_effects.attachments), 2)
        old_body, old_script = self.mercy.body, self.mercy.body_script
        target = next(t for t in self.fight._targets() if t.owner is self.mercy)
        health = self.fight.vitals_of(target)
        yaw, pitch = aim(self.fight._eye(shooter), (0, 1.8, 0))
        beam_yaw, beam_pitch = aim(self.fight._eye(self.mercy), (0, 2, 5))
        for _ in range(105):
            self.run.step({self.mercy: (beam_yaw, beam_pitch, FIRE), shooter: (yaw, pitch, FIRE)})
            if health.dead:
                break
        self.assertTrue(health.dead)
        self.assertEqual(health.killer, shooter.body)
        self.assertFalse(self.fight.support_effects.attachments)
        before = self.health(self.chain)
        self.step(20)
        self.assertFalse(old_script.component.entity_var(842).value())
        self.assertIsNone(self.mercy.mover.flight_scale)
        self.assertEqual(self.health(self.chain), before)
        for _ in range(205):
            self.run.step({})
            if self.mercy.body != old_body:
                break
        self.assertNotEqual(self.mercy.body, old_body)
        self.assertTrue(old_script.retired)
        self.assertIsNone(self.mercy.mover.flight_scale)
        self.assertFalse(self.fight.support_effects.attachments)
        self.assertEqual(self.health(self.chain), before)

    def test_full_primary_keeps_the_original_chain_healing_injured_secondary(self):
        self.start()
        self.fight.heal_target(self.mercy, self.anchor.body, 100, self.run.frame)
        before = self.mercy.stats.value(stats.HEALING)
        self.step(50)
        self.assertEqual(self.health(self.anchor), 200)
        self.assertAlmostEqual(self.health(self.chain), 148, places=4)
        self.assertAlmostEqual(self.mercy.stats.value(stats.HEALING) - before, 48, places=4)
        self.assertEqual({a.instance.graph.index for a in self.fight.support_effects.attachments.values()},
                         {0x0079, 0x10DC})

    def test_secondary_replacement_retires_old_vm_and_acquires_new_body(self):
        self.start()
        self.step(10)
        old_body = self.chain.body
        old = next(a for a in self.fight.support_effects.attachments.values()
                   if a.definition == support_effects.CHAIN_HEAL)
        self.match.switch_hero(self.chain, self.chain.hero)
        self.assertFalse(old.component.instances)
        self.assertNotEqual(self.chain.body, old_body)
        self.assertFalse(any(a.target == old_body for a in self.fight.support_effects.attachments.values()))
        target = next(t for t in self.fight._targets() if t.owner is self.chain)
        self.fight._damage(self.enemy, target, 100, False, None, self.run.frame)
        self.step(50)
        self.assertAlmostEqual(self.health(self.chain), 148, places=4)
        child = next(a for a in self.fight.support_effects.attachments.values()
                     if a.definition == support_effects.CHAIN_HEAL)
        self.assertEqual(child.target, self.chain.body)
        self.assertIsNot(child.component, old.component)

    def test_caster_replacement_destroys_old_effect_tree_and_does_not_keep_valkyrie(self):
        self.start()
        self.step(10)
        old = [a.component for a in self.fight.support_effects.attachments.values()]
        before = self.health(self.chain)
        self.match.switch_hero(self.mercy, self.mercy.hero)
        self.assertFalse(self.fight.support_effects.attachments)
        self.assertTrue(all(not component.instances for component in old))
        self.step(50)
        self.assertEqual(self.health(self.chain), before)
        self.assertFalse(self.mercy.body_script.component.entity_var(842).value())
        self.assertTrue(all(a.definition == support_effects.HEAL
                            for a in self.fight.support_effects.attachments.values()))

    def test_duplicate_source_command_does_not_tick_or_credit_chain_twice(self):
        self.start()
        self.step(10)
        before = self.health(self.chain), self.mercy.stats.value(stats.HEALING)
        clocks = {key: a.last_frame for key, a in self.fight.support_effects.attachments.items()}
        self.mercy.take_commands([Command(self.run.frame, buttons=FIRE)])
        self.assertEqual((self.health(self.chain), self.mercy.stats.value(stats.HEALING)), before)
        self.assertEqual({key: a.last_frame for key, a in self.fight.support_effects.attachments.items()},
                         clocks)


NPC_EFFECT_BUNDLE = Path("D:/overwatch 1.7.4/verification/hero-lanes/0004/research") / (
    "game_graphs_support_npcs_174.json.gz"
)


@unittest.skipUnless(NPC_EFFECT_BUNDLE.is_file(), "explicit NPC+support graph extension required")
class MercyChainNpcTests(unittest.TestCase):
    def test_each_npc_can_anchor_and_receive_actual_chain_healing_then_removal_retires_effects(self):
        fixture = npc_tests.MercyNpcTests()
        fixture.setUp()
        self.addCleanup(fixture.doCleanups)
        self.enterContext(patch.object(graph, "GRAPHS_PATH", NPC_EFFECT_BUNDLE))
        clear_graphs()
        self.addCleanup(clear_graphs)
        for definition in fixture.catalog.values():
            with self.subTest(npc=definition.name):
                run, player, primary, target = fixture.spawn(definition, 0)
                other = npc.spawn(run.match, definition, (5, 1, 5), team=0)
                second = next(t for t in run.match.combat._targets() if t.owner is other)
                for body in (target, second):
                    vitals = run.match.combat.vitals_of(body)
                    vitals.damage(100)
                    run.match.combat._sync_body_health(body.owner, vitals)
                initial = run.match.combat.vitals_of(second).total
                fixture.tick(run, player, primary, 35, buttons=0)
                player.body_script.set_ult_charge(1820)
                fixture.tick(run, player, primary, 1, buttons=0x20)
                fixture.tick(run, player, primary, 35, buttons=0)
                fixture.tick(run, player, primary, 50)
                self.assertAlmostEqual(run.match.combat.vitals_of(second).total - initial, 48, places=3)
                self.assertAlmostEqual(player.stats.value(stats.HEALING), 96, places=3)
                effects = run.match.combat.support_effects
                self.assertEqual({a.instance.graph.index for a in effects.attachments.values()},
                                 {0x79, 0x10DC})
                old = [a.component for a in effects.attachments.values()]
                npc.remove_spawned(run.match)
                self.assertFalse(effects.attachments)
                self.assertTrue(all(not component.instances for component in old))

    def assassin_chain_encounter(self):
        fixture = MercyChainTests()
        with patch.object(boost_tests, "EFFECT_BUNDLE", NPC_EFFECT_BUNDLE):
            fixture.setUp()
        self.addCleanup(fixture.doCleanups)
        fixture.start()
        catalog = npc.load_catalog(npc_tests.NPC_DATA / "pve_npcs_174.json")
        definition = next(d for d in catalog.values() if d.name == "Assassin")
        bot = npc.spawn(fixture.match, definition, (4, 1, 0), team=fixture.enemy.team)
        mercy, fight = fixture.mercy, fixture.fight
        yaw, pitch = aim(fight._eye(mercy), (4, 2, 0))
        fixture.run.step({mercy: (yaw, pitch, 0)})
        self.assertEqual(npc_control.start(fixture.match, mercy), bot.entity)
        return fixture, bot

    def test_actual_assassin_pounce_retires_valkyrie_healing_chain_and_release_rebuilds_it(self):
        fixture, bot = self.assassin_chain_encounter()
        mercy, fight = fixture.mercy, fixture.fight
        fixture.step(10)
        effects = fight.support_effects
        self.assertEqual({a.definition for a in effects.attachments.values()},
                         {support_effects.HEAL, support_effects.CHAIN_HEAL})
        old_components = {a.component for a in effects.attachments.values()}

        bot.position = (2.5, 1, 0)
        for _ in range(30):
            fixture.step(1)
            if fight.is_stunned(mercy):
                break
        self.assertEqual(fight.pins, {bot.entity: mercy.body})
        self.assertFalse(effects.attachments)
        self.assertTrue(all(not component.instances for component in old_components))
        before = fixture.health(fixture.anchor), fixture.health(fixture.chain)
        fixture.step(10)
        self.assertEqual((fixture.health(fixture.anchor), fixture.health(fixture.chain)), before)
        self.assertTrue(mercy.body_script.component.entity_var(842).value())

        npc_control.stop(fixture.match)
        fixture.step(1, beam=0)
        fixture.step(10)
        self.assertEqual({a.definition for a in effects.attachments.values()},
                         {support_effects.HEAL, support_effects.CHAIN_HEAL})
        self.assertTrue(all(a.component not in old_components for a in effects.attachments.values()))
        self.assertAlmostEqual(fixture.health(fixture.anchor) - before[0], 9.6, places=4)
        self.assertAlmostEqual(fixture.health(fixture.chain) - before[1], 9.6, places=4)

    def test_actual_pounce_retires_valkyrie_boost_chain_but_keeps_launch_award(self):
        fixture, bot = self.assassin_chain_encounter()
        mercy, fight = fixture.mercy, fixture.fight
        fixture.enemy.mover.position[:] = (5, 1, 30)
        fixture.enemy.mover.body = None
        amplified = 0x086000000000048A
        charge = mercy.body_script.component.entity_var(ULT_CHARGE)
        fixture.step(10, beam=SECONDARY)
        effects = fight.support_effects
        self.assertEqual({a.definition for a in effects.attachments.values()},
                         {support_effects.BOOST, support_effects.CHAIN_BOOST})
        old_components = {a.component for a in effects.attachments.values()}
        fixture.step(1, beam=SECONDARY, attack=SECONDARY)
        rocket = next(r for r in fight.rockets if r.shooter is fixture.chain)
        self.assertAlmostEqual(rocket.direct.amount, 52, places=4)
        self.assertAlmostEqual(rocket.blast.amount, 104, places=4)

        bot.position = (2.5, 1, 0)
        for _ in range(30):
            fixture.step(1, beam=SECONDARY)
            if fight.is_stunned(mercy):
                break
        self.assertEqual(fight.pins, {bot.entity: mercy.body})
        self.assertIn(rocket, fight.rockets)
        self.assertEqual(fixture.health(fixture.enemy), 200)
        self.assertFalse(effects.attachments)
        self.assertTrue(all(not component.instances for component in old_components))
        fixture.step(30, beam=SECONDARY)
        self.assertAlmostEqual(fixture.health(fixture.enemy), 44, places=3)
        self.assertAlmostEqual(mercy.stats.value(amplified), 36, places=3)
        self.assertEqual(charge.value(), 0)
        self.assertTrue(fight.is_stunned(mercy))
        self.assertFalse(effects.attachments)

        fixture.step(1, beam=SECONDARY, attack=FIRE)
        fixture.step(10, beam=SECONDARY)
        self.assertAlmostEqual(fixture.health(fixture.enemy), 24, places=3)
        self.assertAlmostEqual(mercy.stats.value(amplified), 36, places=3)
        npc_control.stop(fixture.match)
        fixture.step(1, beam=0)
        fixture.step(10, beam=SECONDARY)
        self.assertEqual({a.definition for a in effects.attachments.values()},
                         {support_effects.BOOST, support_effects.CHAIN_BOOST})
        self.assertTrue(all(a.component not in old_components for a in effects.attachments.values()))
        fixture.step(1, beam=SECONDARY, attack=FIRE)
        fixture.step(10, beam=SECONDARY)
        target = next(t for t in fight._targets() if t.owner is fixture.enemy)
        self.assertTrue(fight.vitals_of(target).dead)
        self.assertEqual(fight.vitals_of(target).killer, fixture.chain.body)
        self.assertAlmostEqual(mercy.stats.value(amplified), 40, places=3)
        self.assertEqual(charge.value(), 0)

    def test_all_five_friendly_npcs_apply_actual_chain_damage_bonus(self):
        expected = {"Trooper": 26, "Enforcer": 65, "Sniper": 130, "Assassin": 32.5, "Heavy Assault": 6.5}
        catalog = npc.load_catalog(npc_tests.NPC_DATA / "pve_npcs_174.json")
        for definition in catalog.values():
            with self.subTest(npc=definition.name):
                fixture = MercyChainTests()
                with patch.object(boost_tests, "EFFECT_BUNDLE", NPC_EFFECT_BUNDLE):
                    fixture.setUp()
                try:
                    fixture.chain.spawn = ((20.0, 1.0, 5.0), 0.0)
                    fixture.chain.mover = fixture.chain.new_mover()
                    bot = npc.spawn(fixture.match, definition, (5, 1, 5), team=0)
                    for player in fixture.run.players:
                        player.client.bot_stream = lambda entity: None  # explicit observer transport adapter
                    fixture.start()
                    fixture.step(10, beam=SECONDARY)
                    self.assertAlmostEqual(fixture.fight.damage_scale(bot), 1.3, places=5)
                    if definition.name in ("Enforcer", "Assassin"):
                        fixture.enemy.mover.position[:] = (5, 1, 7)
                        fixture.enemy.mover.body = None
                    origin = fixture.fight._eye(fixture.enemy)
                    fixture.enemy.mover.yaw, fixture.enemy.mover.pitch = aim(origin, (5, 2, 5))
                    self.assertEqual(npc_control.start(fixture.match, fixture.enemy), bot.entity)
                    for _ in range(200):
                        fixture.step(1, beam=SECONDARY)
                        if fixture.health(fixture.enemy) < 200:
                            break
                    self.assertAlmostEqual(200 - fixture.health(fixture.enemy),
                                           expected[definition.name], places=3)
                    npc_control.stop(fixture.match)
                    self.assertIsNotNone(bot.body_script)
                finally:
                    fixture.doCleanups()


if __name__ == "__main__":
    unittest.main()
