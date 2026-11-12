"""Mei combat regressions with the external 104319 graphs; no client acceptance."""

import sys
import unittest
from dataclasses import replace
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from test_game_combat import KANEZAKA_DM, Run, aim

from ow174.game import combat
from ow174.game import mei as mei_abilities
from ow174.game.collision import World
from ow174.game.combat import HitDef, Rocket, Vitals
from ow174.game.commands import FIRE
from ow174.game.content import heroes

MEI = 0x02E00000000000DD


def static_wall(run, owner, distance=1.0):
    """Actual double-sided triangle wall plus floor, not a mocked combat query."""
    x, floor, z = owner.mover.position
    at, lo, hi = z + distance, x - 5.0, x + 5.0
    top = floor + 4.0
    triangles = [
        (-200, floor, -200, -200, floor, 200, 200, floor, 200),
        (-200, floor, -200, 200, floor, 200, 200, floor, -200),
        (lo, floor, at, hi, floor, at, hi, top, at),
        (lo, floor, at, hi, top, at, lo, top, at),
        (lo, floor, at, hi, top, at, hi, floor, at),
        (lo, floor, at, lo, top, at, hi, top, at),
    ]
    collision = World(triangles, name='Mei melee static wall fixture')
    run.match.collision_world = collision
    for player in run.players:
        player.mover.use(world=collision)
    for _ in range(20):
        run.step({})


class MeiCombatTests(unittest.TestCase):
    def duel(self):
        run = Run(KANEZAKA_DM, ("Mei", "Enemy"))
        match = run.match
        match.bots.clear()
        match._pending_bot_scripts.clear()
        mei, enemy = run.players
        self.assertTrue(match.switch_hero(mei, heroes()[MEI]))
        x, y, z = mei.mover.position
        enemy.mover.position = [x, y, z + 5.0]
        for _ in range(30):
            run.step({})
        target = next(t for t in match.combat._targets() if t.owner is enemy)
        return run, mei, enemy, target

    def fire(self, run, player, target, button, height, frames=45):
        fight = run.match.combat
        yaw, pitch = aim(fight._eye(player), (
            target.feet[0], target.feet[1] + height, target.feet[2],
        ))
        for frame in range(frames):
            run.step({player: (yaw, pitch, button if frame < 20 else 0)})

    def test_icicle_body_damage_and_noncritical_notice(self):
        run, mei, _enemy, target = self.duel()
        self.fire(run, mei, target, 2, 0.9)
        self.assertEqual(run.match.combat.vitals_of(target).total, 125.0)
        hits = mei.client.messages(combat.HIT)
        self.assertEqual(len(hits), 1)
        self.assertEqual(hits[0]["+0x78"]["+0x4"], 75)
        self.assertEqual(hits[0]["+0x78"]["+0x8"], 0)

    def test_icicle_reconstructed_headshot_reaches_health_and_notice(self):
        run, mei, _enemy, target = self.duel()
        self.fire(run, mei, target, 2, 1.55)
        self.assertEqual(run.match.combat.vitals_of(target).total, 50.0)
        hits = mei.client.messages(combat.HIT)
        self.assertEqual(len(hits), 1)
        self.assertEqual(hits[0]["+0x78"]["+0x4"], 150)
        self.assertEqual(hits[0]["+0x78"]["+0x8"], combat.CRITICAL)

    def test_primary_spray_deals_damage_without_critical(self):
        run, mei, _enemy, target = self.duel()
        self.fire(run, mei, target, FIRE, 1.55)
        hits = mei.client.messages(combat.HIT)
        self.assertGreater(len(hits), 0)
        self.assertAlmostEqual(run.match.combat.vitals_of(target).total, 200 - len(hits) * 2.75)
        self.assertTrue(all(hit["+0x78"]["+0x8"] == 0 for hit in hits))

    def test_enemy_rifle_damages_mei(self):
        run, mei, enemy, _target = self.duel()
        target = next(t for t in run.match.combat._targets() if t.owner is mei)
        self.fire(run, enemy, target, FIRE, 0.9, frames=3)
        self.assertEqual(run.match.combat.vitals_of(target).total, 230.0)

    def test_icicle_respects_team_filter(self):
        run, mei, enemy, target = self.duel()
        # This map is FFA. Switch to a team rule without changing the geometry.
        run.match.game_map = replace(run.match.game_map, free_for_all=False)
        enemy.team = mei.team
        self.fire(run, mei, target, 2, 1.55)
        self.assertEqual(run.match.combat.vitals_of(target).total, 200.0)
        self.assertEqual(mei.client.messages(combat.HIT), [])

    def test_icicle_lethal_headshot_marks_death_once(self):
        run, mei, enemy, target = self.duel()
        vitals = run.match.combat.vitals_of(target)
        vitals.damage(100.0)
        self.fire(run, mei, target, 2, 1.55)
        self.assertTrue(vitals.dead)
        self.assertEqual(vitals.total, 0.0)
        self.assertEqual(len(mei.client.messages(combat.KILL_NOTICE)), 1)
        self.assertEqual(len(enemy.client.messages(combat.DEATH_NOTICE)), 1)
        self.assertEqual(mei.client.messages(combat.HIT)[0]["+0x78"]["+0x8"],
                         combat.CRITICAL | combat.KILLING)

    def test_icicle_drains_shields_then_armour_then_health(self):
        run, mei, _enemy, target = self.duel()
        fight = run.match.combat
        vitals = Vitals(target.entity, 200.0, armour=50.0, shields=50.0)
        fight.vitals[target.entity] = vitals
        self.fire(run, mei, target, 2, 1.55)
        self.assertEqual(vitals.total, 155.0)
        self.assertEqual(mei.client.messages(combat.HIT)[0]["+0x78"]["+0x4"], 145)

    def test_projectile_falloff_uses_entire_flight_distance(self):
        run, mei, _enemy, target = self.duel()
        fight = run.match.combat
        # Explicit synthetic hit definition isolates the shared projectile path.
        rocket = Rocket(mei, mei.team, fight._eye(mei), (0.0, 0.0, 1.0), 10.0, 2.0,
                        HitDef(100.0, falloff_start=0.0, falloff_end=10.0,
                               falloff_floor=0.0, critical=1.0), None)
        fight.rockets.append(rocket)
        for _ in range(40):
            run.step({})
        self.assertNotIn(rocket, fight.rockets)
        expected = 100.0 * (1.0 - rocket.flown * rocket.speed / 10.0)
        self.assertAlmostEqual(fight.vitals_of(target).total, 200.0 - expected)
        self.assertGreater(rocket.flown, 0.4)

    def test_quick_melee_damages_nearby_enemy(self):
        run, mei, enemy, _target = self.duel()
        x, y, z = mei.mover.position
        enemy.mover.position = [x, y, z + 1.5]
        target = next(t for t in run.match.combat._targets() if t.owner is enemy)
        self.fire(run, mei, target, 0x800, 0.9)
        self.assertEqual(run.match.combat.vitals_of(target).total, 170.0)

    def test_lethal_quick_melee_cannot_hit_or_credit_the_dead_body_again(self):
        run, owner, enemy, _ = self.duel()
        fight = run.match.combat
        x, y, z = owner.mover.position
        fight.walls.place_player(enemy, (x, y, z + 2.0))
        target = next(t for t in fight._targets() if t.owner is enemy)
        vitals = fight.vitals_of(target)
        vitals.damage(180.0)
        owner.body_script.ult_seconds = 0
        owner.body_script.set_ult_charge(0.0)
        charge = owner.body_script.component.entity_var(342)
        self.fire(run, owner, target, 0x800, 0.9)
        self.assertTrue(vitals.dead)
        self.assertEqual(vitals.total, 0.0)
        self.assertEqual(vitals.killer, owner.body)
        self.assertEqual(charge.value(), 20.0)
        for _ in range(20):
            run.step({})
        self.fire(run, owner, target, 0x800, 0.9)
        self.assertEqual(len(owner.client.messages(combat.HIT)), 1)
        self.assertEqual(len(owner.client.messages(combat.KILL_NOTICE)), 1)
        self.assertEqual(len(enemy.client.messages(combat.DEATH_NOTICE)), 1)
        self.assertEqual(charge.value(), 20.0)

    def test_quick_melee_repeated_input_cannot_bypass_authored_cooldown(self):
        cooldown_traces = []
        for repeated in (False, True):
            with self.subTest(repeated=repeated):
                run, owner, enemy, _ = self.duel()
                fight = run.match.combat
                x, y, z = owner.mover.position
                fight.walls.place_player(enemy, (x, y, z + 2.))
                target = next(t for t in fight._targets() if t.owner is enemy)
                instances = owner.body_script.component.instances.values()
                melee = next(i for i in instances if i.graph.index == 0x43)

                run.step({owner: (0, 0, 0x800)})
                hit = melee.states[melee.graph.node(10).state]
                cooldown = melee.vars[61]
                self.assertEqual(melee.vars[472].value(), 1.)
                self.assertEqual(fight.vitals_of(target).total, 170.)
                trace = []
                for elapsed in range(1, 62):
                    buttons = 0x800 if repeated and elapsed % 2 == 0 else 0
                    run.step({owner: (0, 0, buttons)})
                    trace.append(cooldown.value())
                    self.assertGreater(trace[-1], 0.)
                    self.assertEqual(hit.counter, 1)
                    self.assertEqual(fight.vitals_of(target).total, 170.)
                cooldown_traces.append(trace)
                self.assertEqual(len(owner.client.messages(combat.HIT)), 1)

                run.step({owner: (0, 0, 0x800)})
                self.assertEqual(hit.counter, 2)
                self.assertEqual(fight.vitals_of(target).total, 140.)
                self.assertEqual(len(owner.client.messages(combat.HIT)), 2)
        self.assertEqual(*cooldown_traces)

    def test_quick_melee_cannot_damage_enemy_through_actual_static_wall(self):
        run, owner, enemy, _target = self.duel()
        x, y, z = owner.mover.position
        run.match.combat.walls.place_player(enemy, (x, y, z + 2.0))
        static_wall(run, owner)
        target = next(t for t in run.match.combat._targets() if t.owner is enemy)
        self.fire(run, owner, target, 0x800, 0.9)
        self.assertEqual(run.match.combat.vitals_of(target).total, 200.0)
        self.assertEqual(owner.client.messages(combat.HIT), [])

    def test_quick_melee_filters_team_range_and_aim_and_hits_once_per_press(self):
        cases = (
            ("enemy", 2.5, False, False, 30.),
            ("ally", 2.5, True, False, 0.),
            ("range", 3.1, False, False, 0.),
            ("aim", 2.5, False, True, 0.),
        )
        for name, distance, allied, away, damage in cases:
            with self.subTest(name=name):
                run, owner, enemy, _ = self.duel()
                fight = run.match.combat
                run.match.game_map = replace(run.match.game_map, free_for_all=False)
                if allied:
                    enemy.team = owner.team
                x, y, z = owner.mover.position
                fight.walls.place_player(enemy, (x, y, z + distance))
                target = next(t for t in fight._targets() if t.owner is enemy)
                if away:
                    for frame in range(45):
                        run.step({owner: (-32768, 0, 0x800 if frame < 20 else 0)})
                else:
                    self.fire(run, owner, target, 0x800, 0.9)
                self.assertEqual(fight.vitals_of(target).total, 200. - damage)
                hits = owner.client.messages(combat.HIT)
                self.assertEqual(len(hits), int(damage > 0.))
                if damage:
                    self.assertEqual(hits[0]["+0x78"]["+0x4"], 30)
                    self.assertEqual(hits[0]["+0x78"]["+0x8"], 0)
                    for _ in range(25):
                        run.step({})
                    self.assertEqual(fight.vitals_of(target).total, 170.)
                    self.fire(run, owner, target, 0x800, 0.9)
                    self.assertEqual(fight.vitals_of(target).total, 140.)
                    self.assertEqual(len(owner.client.messages(combat.HIT)), 2)

    def test_icicle_cannot_damage_through_static_wall(self):
        run, mei, _enemy, target = self.duel()
        static_wall(run, mei)
        self.fire(run, mei, target, 2, 0.9)
        self.assertEqual(run.match.combat.vitals_of(target).total, 200.0)
        self.assertEqual(mei.client.messages(combat.HIT), [])

    def npc_duel(self):
        run, mei, _enemy, _target = self.duel()
        stock = Run().match.bots[0]
        stock.body_script = None
        stock.route = []
        stock.firing = False
        stock.team = mei.team + 1
        stock.retain_corpse = True
        x, y, z = mei.mover.position
        stock.position = (x, y, z + 5.0)
        run.match.bots.append(stock)
        other = run.players[1]
        other.spawn = ((x + 20.0, y, z + 5.0), other.spawn[1])
        self.assertTrue(run.match.switch_hero(other, other.hero))
        target = next(t for t in run.match.combat._targets() if t.owner is stock)
        return run, mei, stock, target

    def test_icicle_kills_retained_training_npc_without_respawn(self):
        run, mei, npc, target = self.npc_duel()
        vitals = run.match.combat.vitals_of(target)
        vitals.damage(vitals.total - 100.0)
        self.fire(run, mei, target, 2, 1.55)
        self.assertTrue(npc.dead)
        self.assertTrue(vitals.dead)
        self.assertEqual(vitals.total, 0.0)
        for _ in range(220):
            run.step({})
        self.assertIn(npc, run.match.bots)
        self.assertFalse(any(who is npc for _when, who in run.match.combat.respawns))
        self.assertFalse(any(t.owner is npc for t in run.match.combat._enemies(mei)))

    def test_npc_projectile_path_damages_mei(self):
        run, mei, npc, _target = self.npc_duel()
        fight = run.match.combat
        target = next(t for t in fight._targets() if t.owner is mei)
        x, y, z = npc.position
        # Explicitly inject a flight, not an AI decision or a native weapon volley.
        fight.rockets.append(Rocket(npc, npc.team, (x, y + 1.0, z), (0.0, 0.0, -1.0),
                                    40.0, 1.0, HitDef(19.0), None))
        for _ in range(20):
            run.step({})
        self.assertEqual(fight.vitals_of(target).total, 231.0)

    def test_primary_miss_spends_ammo_without_damaging_enemy(self):
        run, mei, _enemy, target = self.duel()
        weapon = next(i for i in mei.body_script.component.instances.values() if i.graph.index == 0x5E6)
        for _ in range(30):
            run.step({mei: (-32768, 0, FIRE)})
        for _ in range(30):
            run.step({})
        self.assertLess(weapon.vars[53].value(), 120)
        self.assertEqual(run.match.combat.vitals_of(target).total, 200.0)
        self.assertEqual(mei.client.messages(combat.HIT), [])

    def test_primary_range_rejects_enemy_beyond_ten_metres(self):
        for distance, damage in ((9.9, 2.75), (10.75, 0.)):
            with self.subTest(distance=distance):
                run, mei, enemy, _target = self.duel()
                fight = run.match.combat
                x, y, z = mei.mover.position
                enemy.spawn = ((x, y, z + distance), enemy.spawn[1])
                self.assertTrue(run.match.switch_hero(enemy, enemy.hero))
                target = next(t for t in fight._targets() if t.owner is enemy)
                weapon = next(i for i in mei.body_script.component.instances.values()
                              if i.graph.index == 0x5E6)
                yaw, pitch = aim(fight._eye(mei), (target.feet[0], target.feet[1] + .9, target.feet[2]))
                run.step({mei: (yaw, pitch, FIRE)})
                for _ in range(45):
                    run.step({})
                self.assertEqual(weapon.vars[53].value(), 119.)
                self.assertEqual(fight.vitals_of(target).total, 200. - damage)
                self.assertEqual(len(mei.client.messages(combat.HIT)), int(damage > 0.))
                self.assertFalse(any(shot.shooter is mei for shot in fight.rockets))

    def test_primary_team_and_static_los_gate_damage_and_frost_until_release(self):
        for admission in ("enemy", "ally", "wall"):
            with self.subTest(admission=admission):
                run, mei, enemy, target = self.duel()
                fight = run.match.combat
                if admission == "ally":
                    run.match.game_map = replace(run.match.game_map, free_for_all=False)
                    enemy.team = mei.team
                elif admission == "wall":
                    static_wall(run, mei)
                target = next(t for t in fight._targets() if t.owner is enemy)
                weapon = next(i for i in mei.body_script.component.instances.values()
                              if i.graph.index == 0x5E6)
                self.fire(run, mei, target, FIRE, 0.9)
                vitals = fight.vitals_of(target)
                hits = mei.client.messages(combat.HIT)
                ammo = weapon.vars[53].value()
                self.assertLess(ammo, 120)
                if admission == "enemy":
                    self.assertGreater(len(hits), 0)
                    self.assertEqual(vitals.total, 200 - 2.75 * len(hits))
                    self.assertGreater(fight.frost(enemy)[0], 0.)
                else:
                    self.assertEqual(vitals.total, 200.)
                    self.assertEqual(hits, [])
                    self.assertIsNone(fight.frost(enemy))
                settled_health = vitals.total
                for _ in range(45):
                    run.step({})
                self.assertEqual(weapon.vars[53].value(), ammo)
                self.assertEqual(vitals.total, settled_health)
                self.assertEqual(mei.client.messages(combat.HIT), hits)

    def test_icicle_miss_consumes_one_volley_without_a_hit(self):
        run, mei, _enemy, target = self.duel()
        weapon = next(i for i in mei.body_script.component.instances.values()
                      if i.graph.index == 0x5E6)
        for frame in range(100):
            run.step({mei: (-32768, 0, 2 if frame < 20 else 0)})
        self.assertEqual(weapon.vars[53].value(), 110)
        self.assertEqual(run.match.combat.vitals_of(target).total, 200.)
        self.assertEqual(mei.client.messages(combat.HIT), [])

    def test_icicle_hits_inside_authored_range_and_expires_beyond_it(self):
        for distance, damage in ((99., 75.), (101., 0.)):
            with self.subTest(distance=distance):
                run, mei, enemy, _target = self.duel()
                fight = run.match.combat
                x, y, z = mei.mover.position
                enemy.spawn = ((x, y, z + distance), enemy.spawn[1])
                self.assertTrue(run.match.switch_hero(enemy, enemy.hero))
                target = next(t for t in fight._targets() if t.owner is enemy)
                weapon = next(i for i in mei.body_script.component.instances.values()
                              if i.graph.index == 0x5E6)
                self.fire(run, mei, target, 2, 0.9, frames=120)
                self.assertEqual(weapon.vars[53].value(), 110)
                self.assertEqual(fight.vitals_of(target).total, 200. - damage)
                self.assertEqual(len(mei.client.messages(combat.HIT)), int(damage > 0.))
                self.assertFalse(any(shot.shooter is mei for shot in fight.rockets))

    def test_reload_refills_only_after_authored_delay(self):
        for repeat in (False, True):
            with self.subTest(repeat=repeat):
                run, mei, _enemy, target = self.duel()
                weapon = next(i for i in mei.body_script.component.instances.values()
                              if i.graph.index == 0x5E6)
                self.fire(run, mei, target, FIRE, 0.9)
                ammo = weapon.vars[53]
                spent = ammo.value()
                self.assertLess(spent, 120.)
                run.step({mei: (0, 0, 0x400)})
                refill = weapon.states[weapon.graph.node(22).state]
                self.assertTrue(refill.active)
                activation = refill.counter, refill.start
                self.assertEqual(refill.ms, 900)
                deadline = refill.start + refill.ms
                for frame in range(90):
                    run.step({mei: (0, 0, 0x400 if repeat and frame % 2 else 0)})
                    self.assertEqual((refill.counter, refill.start), activation)
                    if refill.now >= deadline:
                        break
                    self.assertEqual(ammo.value(), spent)
                self.assertGreaterEqual(refill.now, deadline)
                self.assertFalse(refill.active)
                self.assertEqual(ammo.value(), 120.)
                for frame in range(90):
                    run.step({mei: (0, 0, 0x400 if repeat and frame % 2 else 0)})
                    self.assertEqual(ammo.value(), 120.)
                    self.assertEqual(refill.counter, activation[0])

    def test_secondary_consumes_ten_ammo_and_death_refuses_fire(self):
        run, mei, enemy, target = self.duel()
        weapon = next(i for i in mei.body_script.component.instances.values() if i.graph.index == 0x5E6)
        self.fire(run, mei, target, 2, 0.9)
        self.assertEqual(weapon.vars[53].value(), 110)
        fight = run.match.combat
        hits = mei.client.messages(combat.HIT)
        self.assertEqual(len(hits), 1)
        owner_target = next(t for t in fight._targets() if t.owner is mei)
        fight._damage(enemy, owner_target, 1000.0, False, None, run.frame)
        self.assertTrue(fight.vitals_of(owner_target).dead)
        for button in (FIRE, 2):
            with self.subTest(button=button):
                self.fire(run, mei, target, button, 0.9)
                self.assertEqual(weapon.vars[53].value(), 110)
                self.assertEqual(fight.vitals_of(target).total, 125.0)
                self.assertEqual(mei.client.messages(combat.HIT), hits)
                self.assertFalse(any(shot.shooter is mei for shot in fight.rockets))

    def test_death_during_icicle_windup_retires_the_unreleased_projectile(self):
        run, mei, enemy, target = self.duel()
        fight = run.match.combat
        weapon = next(i for i in mei.body_script.component.instances.values() if i.graph.index == 0x5E6)
        yaw, pitch = aim(fight._eye(mei), (target.feet[0], target.feet[1] + .9, target.feet[2]))
        run.step({mei: (yaw, pitch, 2)})
        windup = weapon.states[weapon.graph.node(63).state]
        self.assertTrue(windup.active)
        self.assertFalse(fight.rockets)

        owner_target = next(t for t in fight._targets() if t.owner is mei)
        fight._damage(enemy, owner_target, 1000.0, False, None, run.frame)
        self.assertTrue(fight.vitals_of(owner_target).dead)
        for _ in range(75):
            run.step({mei: (yaw, pitch, 2)})
            self.assertFalse(any(shot.shooter is mei for shot in fight.rockets))
        self.assertFalse(windup.active)
        self.assertEqual(fight.vitals_of(target).total, 200.0)
        self.assertEqual(mei.client.messages(combat.HIT), [])

    def test_cryo_wall_preview_and_melee_cancel_admitted_icicle_windup(self):
        for action, button in (("cryo", 8), ("wall", 16), ("melee", 0x800)):
            with self.subTest(action=action):
                run, owner, _enemy, target = self.duel()
                fight = run.match.combat
                weapon = next(i for i in owner.body_script.component.instances.values()
                              if i.graph.index == 0x5E6)
                ammo = weapon.vars[53]
                yaw, pitch = aim(fight._eye(owner),
                                 (target.feet[0], target.feet[1] + .9, target.feet[2]))
                run.step({owner: (yaw, pitch, 2)})
                windup = weapon.states[weapon.graph.node(63).state]
                self.assertTrue(windup.active)
                self.assertFalse(fight.rockets)
                reserved_ammo = ammo.value()
                self.assertGreater(reserved_ammo, 110.)
                self.assertLess(reserved_ammo, 120.)

                run.step({owner: (yaw, pitch, button)})
                self.assertFalse(windup.active)
                self.assertEqual(mei_abilities.protected(owner), action == "cryo")
                self.assertEqual(fight.walls.inputs[owner.body].preview, action == "wall")
                for _ in range(45):
                    run.step({})
                    self.assertFalse(any(shot.shooter is owner for shot in fight.rockets))
                self.assertEqual(ammo.value(), 120. if action == "cryo" else reserved_ammo)

                if action in ("cryo", "wall"):
                    cancel = 2 if action == "cryo" else 16
                    run.step({owner: (yaw, pitch, cancel)})
                for _ in range(25):
                    run.step({})
                    self.assertFalse(any(shot.shooter is owner for shot in fight.rockets))
                self.assertFalse(mei_abilities.protected(owner))
                self.assertFalse(fight.walls.inputs[owner.body].preview)
                self.assertFalse(fight.walls.pieces)
                self.assertEqual(fight.vitals_of(target).total, 200.)
                self.assertEqual(owner.client.messages(combat.HIT), [])
                before = ammo.value()
                self.fire(run, owner, target, 2, .9)
                self.assertAlmostEqual(ammo.value(), before - 10., places=4)
                self.assertEqual(fight.vitals_of(target).total, 125.)
                self.assertEqual(len(owner.client.messages(combat.HIT)), 1)

    def test_empty_held_spray_reloads_and_can_deal_damage_again(self):
        run, mei, _enemy, target = self.duel()
        weapon = next(i for i in mei.body_script.component.instances.values() if i.graph.index == 0x5E6)
        for _ in range(650):
            run.step({mei: (-32768, 0, FIRE)})
        self.assertGreaterEqual(weapon.states[7].counter, 1)
        self.assertGreaterEqual(weapon.states[13].counter, 2)
        self.assertGreater(weapon.vars[53].value(), 0)
        self.assertLess(weapon.vars[53].value(), 120)
        fight = run.match.combat
        vitals = fight.vitals_of(target)
        self.assertEqual(vitals.total, 200.0)
        ammo = weapon.vars[53].value()
        yaw, pitch = aim(fight._eye(mei), (target.feet[0], target.feet[1] + .9, target.feet[2]))
        # Turn toward the target with primary still held after the empty reload.
        for _ in range(20):
            run.step({mei: (yaw, pitch, FIRE)})
        self.assertLess(weapon.vars[53].value(), ammo)
        self.assertLess(vitals.total, 200.0)
        for _ in range(30):
            run.step({mei: (yaw, pitch, 0)})
        settled = vitals.total
        self.fire(run, mei, target, 2, 0.9)
        self.assertEqual(vitals.total, settled - 75.0)

    def test_insufficient_icicle_ammo_waits_for_real_reload_before_damage(self):
        run, mei, _enemy, target = self.duel()
        weapon = next(i for i in mei.body_script.component.instances.values() if i.graph.index == 0x5E6)
        weapon.write((0, 53), 5)
        yaw, pitch = aim(run.match.combat._eye(mei),
                         (target.feet[0], target.feet[1] + 0.9, target.feet[2]))
        for _ in range(20):
            run.step({mei: (yaw, pitch, 2)})
        self.assertTrue(weapon.states[7].active)
        self.assertEqual(weapon.vars[53].value(), 5)
        self.assertEqual(run.match.combat.vitals_of(target).total, 200.0)
        for _ in range(80):
            run.step({mei: (yaw, pitch, 2)})
        for _ in range(60):
            run.step({mei: (yaw, pitch, 0)})
        self.assertEqual(weapon.vars[53].value(), 110)
        self.assertEqual(run.match.combat.vitals_of(target).total, 125.0)

    def test_last_ten_ammo_icicle_finishes_before_automatic_reload(self):
        run, mei, _enemy, target = self.duel()
        weapon = next(i for i in mei.body_script.component.instances.values() if i.graph.index == 0x5E6)
        weapon.write((0, 53), 10)
        yaw, pitch = aim(run.match.combat._eye(mei),
                         (target.feet[0], target.feet[1] + 0.9, target.feet[2]))
        run.step({mei: (yaw, pitch, 2)})
        for _ in range(64):
            run.step({mei: (yaw, pitch, 0)})
        self.assertEqual(run.match.combat.vitals_of(target).total, 125.0)
        self.assertEqual(len(mei.client.messages(combat.HIT)), 1)
        self.assertTrue(weapon.states[7].active)
        self.assertEqual(weapon.vars[53].value(), 0)
        for _ in range(140):
            run.step({})
        self.assertEqual(weapon.vars[53].value(), 120)

    def test_melee_cancels_reload_without_delayed_refill(self):
        run, mei, enemy, _target = self.duel()
        x, y, z = mei.mover.position
        enemy.spawn = ((x, y, z + 1.5), enemy.spawn[1])
        self.assertTrue(run.match.switch_hero(enemy, enemy.hero))
        target = next(t for t in run.match.combat._targets() if t.owner is enemy)
        self.fire(run, mei, target, FIRE, 0.9)
        weapon = next(i for i in mei.body_script.component.instances.values() if i.graph.index == 0x5E6)
        ammo = weapon.vars[53].value()
        health = run.match.combat.vitals_of(target).total
        run.step({mei: (0, 0, 0x400)})
        for _ in range(20):
            run.step({})
        self.assertTrue(weapon.states[7].active)
        self.fire(run, mei, target, 0x800, 0.9)
        for _ in range(150):
            run.step({})
        self.assertFalse(weapon.states[7].active)
        self.assertEqual(weapon.vars[53].value(), ammo)
        self.assertEqual(run.match.combat.vitals_of(target).total, health - 30)
        run.step({mei: (0, 0, 0x400)})
        for _ in range(140):
            run.step({})
        self.assertEqual(weapon.vars[53].value(), 120)


if __name__ == "__main__":
    unittest.main()
