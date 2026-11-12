"""Ana's reconstructed rifle healing through actual body commands and combat geometry.

These checks do not accept native filters, delivery timing or original-client visuals.
"""

import math
import unittest
from dataclasses import replace


from ow174.game import combat, projectiles, stats
from ow174.game.collision import World
from ow174.game.commands import FIRE, SECONDARY
from ow174.game.content import PRACTICE_RANGE, heroes
from tests.runtime.test_game_combat import KANEZAKA_DM, Run, aim

ANA = 0x02E000000000013B
TEAM_MAP = replace(PRACTICE_RANGE, team_sizes=(6, 6))


def grenade_aim(run, actor, point, *, speed=30.0, gravity=9.8):
    """Low-arc input for a controlled stationary-target ballistic fixture."""
    origin = run.match.combat._eye(actor)
    distance = math.hypot(point[0] - origin[0], point[2] - origin[2])
    height = point[1] - origin[1]
    root = speed ** 4 - gravity * (gravity * distance ** 2 + 2 * height * speed ** 2)
    tangent = (speed ** 2 - math.sqrt(root)) / (gravity * distance)
    yaw, _ = aim(origin, point)
    return yaw, -round(math.atan(tangent) * 65536 / (2 * math.pi))


def set_static_wall(run, actor, distance):
    """Controlled floor plus an opaque wall between the two combat bodies."""
    x, y, z = actor.mover.position
    wall_z = z + distance
    run.match.collision_world = World([
        (x - 50, y, z - 50, x - 50, y, z + 50, x + 50, y, z + 50),
        (x - 50, y, z - 50, x + 50, y, z + 50, x + 50, y, z - 50),
        (x - 3, y, wall_z, x + 3, y, wall_z, x + 3, y + 4, wall_z),
        (x - 3, y, wall_z, x + 3, y + 4, wall_z, x - 3, y + 4, wall_z),
    ])


class AnaRifleTests(unittest.TestCase):
    def setup_fight(self, *, npc=False, enemy=False, ffa=False, distance=6.0):
        run = Run(KANEZAKA_DM if ffa else TEAM_MAP, ("Ana", "Target"))
        shooter, player = run.players
        match = run.match
        self.assertTrue(match.switch_hero(shooter, heroes()[ANA]))
        if npc:
            target = match.bots[0]
            match.bots[:] = [target]
            target.route = []
            target.firing = False
        else:
            target = player
            match.bots.clear()
        match._pending_bot_scripts.clear()
        shooter.team = 0
        target.team = 1 if enemy else 0
        x, y, z = shooter.mover.position
        if npc:
            target.position = (x, y, z + distance)
            player.mover.position = [x + 30, y, z]
        else:
            target.mover.position = [x, y, z + distance]
        for _ in range(25):
            run.step({})
        entry = next(t for t in match.combat._targets() if t.owner is target)
        return run, shooter, target, entry

    def shoot(self, run, shooter, entry, scoped):
        direction = aim(run.match.combat._eye(shooter),
                        (entry.feet[0], entry.feet[1] + 1.0, entry.feet[2]))
        if scoped:
            for _ in range(35):
                run.step({shooter: (*direction, SECONDARY)})
        run.step({shooter: (*direction, FIRE | (SECONDARY if scoped else 0))})
        for _ in range(55):
            run.step({shooter: (*direction, SECONDARY if scoped else 0)})

    def test_hip_and_scoped_heal_players_and_npcs_without_damage_notices(self):
        for npc in (False, True):
            for scoped in (False, True):
                with self.subTest(npc=npc, scoped=scoped):
                    run, shooter, _target, entry = self.setup_fight(npc=npc)
                    vitals = run.match.combat.vitals_of(entry)
                    vitals.damage(100)
                    before = vitals.total
                    self.shoot(run, shooter, entry, scoped)
                    self.assertEqual(vitals.total, before + 70)
                    self.assertFalse(shooter.client.messages(combat.HIT))
                    self.assertEqual(shooter.stats.value(stats.HEALING), 70)

    def test_enemies_take_damage_including_ffa_same_team(self):
        for npc, ffa in ((False, False), (True, False), (False, True)):
            for scoped in (False, True):
                with self.subTest(npc=npc, ffa=ffa, scoped=scoped):
                    run, shooter, _target, entry = self.setup_fight(npc=npc, enemy=not ffa, ffa=ffa)
                    vitals = run.match.combat.vitals_of(entry)
                    before = vitals.total
                    self.shoot(run, shooter, entry, scoped)
                    self.assertEqual(vitals.total, before - 70)
                    self.assertTrue(shooter.client.messages(combat.HIT))

    def test_healing_clamps_and_dead_targets_do_not_revive(self):
        run, shooter, _target, entry = self.setup_fight()
        fight = run.match.combat
        vitals = fight.vitals_of(entry)
        vitals.damage(10)
        self.shoot(run, shooter, entry, False)
        self.assertEqual(vitals.total, sum(vitals.most.values()))
        vitals.damage(1000)
        vitals.died_at = fight.now
        self.assertNotIn(entry, fight._shot_targets(shooter, combat.HitDef(70, ally_heal=70)))
        self.assertEqual(fight._heal(shooter, entry, 70), 0)
        self.assertEqual(vitals.total, 0)

    def test_full_health_ally_is_transparent_to_enemy_behind(self):
        run, shooter, _target, entry = self.setup_fight()
        fight = run.match.combat
        # Put an enemy NPC behind the healthy friendly player on the same ray.
        from ow174.game import bots

        npc = bots.Bot(0x9000, {"hero": "0x02E000000000013B", "team": 1,
                              "position": [entry.feet[0], entry.feet[1], entry.feet[2] + 4],
                              "rotation": [0, 0, 0, 1], "graphs": []},
                       {"name": "Enemy", "body": "0x0400000000001424", "health": 200}, {})
        run.match.bots.append(npc)
        for _ in range(2):
            run.step({})
        self.shoot(run, shooter, entry, False)
        enemy = next(t for t in fight._targets() if t.owner is npc)
        self.assertEqual(fight.vitals_of(enemy).total, 130)
        self.assertEqual(fight.vitals_of(entry).total, 200)

    def test_enemy_player_can_damage_ana_through_actual_weapon_commands(self):
        run, ana, attacker, _entry = self.setup_fight(enemy=True)
        fight = run.match.combat
        x, y, z = ana.mover.position
        direction = aim(fight._eye(attacker), (x, y + 1, z))
        target = next(t for t in fight._targets() if t.owner is ana)
        before = fight.vitals_of(target).total
        run.step({attacker: (*direction, FIRE)})
        for _ in range(5):
            run.step({})
        self.assertEqual(fight.vitals_of(target).total, before - 20)

    def test_ana_melee_damages_enemy_player_and_npc_but_not_ally(self):
        for npc, enemy in ((False, True), (True, True), (False, False)):
            with self.subTest(npc=npc, enemy=enemy):
                run, ana, _target, entry = self.setup_fight(npc=npc, enemy=enemy, distance=1.5)
                x, y, z = ana.mover.position
                fight = run.match.combat
                vitals = fight.vitals_of(entry)
                before = vitals.total
                direction = aim(fight._eye(ana), (x, y + 1, z + 1.5))
                run.step({ana: (*direction, 0x800)})
                for _ in range(35):
                    run.step({})
                self.assertEqual(vitals.total, before - (30 if enemy else 0))

    def test_ana_melee_cannot_hit_enemy_player_through_static_wall(self):
        run, ana, _enemy, entry = self.setup_fight(enemy=True, distance=1.5)
        vitals = run.match.combat.vitals_of(entry)
        before = vitals.total
        set_static_wall(run, ana, 0.75)
        direction = aim(run.match.combat._eye(ana),
                        (entry.feet[0], entry.feet[1] + 1, entry.feet[2]))
        run.step({ana: (*direction, 0x800)})
        for _ in range(35):
            run.step({})
        self.assertEqual(vitals.total, before)


def status_until(fight, vitals, *, allied):
    effects = fight.healing.amp if allied else fight.healing.anti
    effect = effects.get(vitals.entity)
    if effect is not None:
        return effect.end * combat.TICK
    return (vitals.healing_boost if allied else vitals.healing_block)[1]


class AnaGrenadeTests(unittest.TestCase):
    def setup_fight(self, **kwargs):
        return AnaRifleTests().setup_fight(**kwargs)

    def cast(self, run, ana, entry):
        direction = aim(run.match.combat._eye(ana),
                        (entry.feet[0], entry.feet[1] + 1.0, entry.feet[2]))
        run.step({ana: (*direction, 0x10)})
        for _ in range(60):
            run.step({})

    def test_grenade_heals_allies_and_amplifies_other_healing_then_expires(self):
        for npc in (False, True):
            with self.subTest(npc=npc):
                run, ana, _target, entry = self.setup_fight(npc=npc)
                fight = run.match.combat
                vitals = fight.vitals_of(entry)
                vitals.damage(150)
                before = vitals.total
                self.cast(run, ana, entry)
                self.assertEqual(vitals.total, before + 100)
                self.assertGreater(status_until(fight, vitals, allied=True), fight.now)
                fight.heal_area(ana, entry.feet, 1, 20)
                self.assertEqual(vitals.total, before + 130)
                for _ in range(220):
                    run.step({})
                vitals.damage(100)
                before = vitals.total
                fight.heal_area(ana, entry.feet, 1, 20)
                self.assertEqual(vitals.total, before + 20)

    def test_enemy_grenade_damages_and_blocks_healing_until_expiry(self):
        for npc in (False, True):
            with self.subTest(npc=npc):
                run, ana, target, entry = self.setup_fight(npc=npc, enemy=True)
                fight = run.match.combat
                vitals = fight.vitals_of(entry)
                before = vitals.total
                self.cast(run, ana, entry)
                self.assertEqual(vitals.total, before - fight.healing.spec.damage)
                self.assertGreater(status_until(fight, vitals, allied=False), fight.now)
                fight.heal_area(target, entry.feet, 1, 40)
                self.assertEqual(vitals.total, before - fight.healing.spec.damage)
                for _ in range(220):
                    run.step({})
                fight.heal_area(target, entry.feet, 1, 40)
                self.assertEqual(vitals.total, before - fight.healing.spec.damage + 40)

    def test_actual_grenade_modifies_soldier_biotic_field_healing(self):
        for allied in (False, True):
            with self.subTest(allied=allied):
                run, ana, soldier, entry = self.setup_fight(enemy=not allied)
                fight = run.match.combat
                vitals = fight.vitals_of(entry)
                vitals.damage(170 if allied else 100)
                point = (entry.feet[0], entry.feet[1] + 1, entry.feet[2])
                direction = grenade_aim(run, ana, point)
                run.step({ana: (*direction, 16)})
                for _ in range(70):
                    run.step({})
                    deadline = status_until(fight, vitals, allied=allied)
                    if deadline > fight.now:
                        break
                self.assertGreater(deadline, fight.now)
                before = 130 if allied else 100 - fight.healing.spec.damage
                self.assertEqual(vitals.total, before)

                run.step({soldier: (0, 0, 16)})
                for _ in range(90):
                    run.step({})
                    fields = [shot for shot in fight.projectiles.shots
                              if shot.player is soldier and shot.field_tick is not None]
                    if fields:
                        break
                field, = fields
                self.assertLess(fight.now, deadline)
                self.assertEqual(vitals.total, before)
                rate = projectiles.HEAL_PER_SECOND * combat.TICK
                if allied:
                    run.step({})
                    self.assertAlmostEqual(vitals.total, before + 1.5 * rate)
                else:
                    for _ in range(250):
                        run.step({})
                        if fight.now >= deadline:
                            break
                        self.assertEqual(vitals.total, before)
                    self.assertGreaterEqual(fight.now, deadline)
                    self.assertIn(field, fight.projectiles.shots)
                    self.assertAlmostEqual(vitals.total, before + rate)

    def test_grenade_uses_release_team_after_thrower_changes_team(self):
        for enemy in (False, True):
            with self.subTest(enemy=enemy):
                run, ana, _target, entry = self.setup_fight(enemy=enemy, distance=20)
                fight = run.match.combat
                vitals = fight.vitals_of(entry)
                if not enemy:
                    vitals.damage(110)
                before = vitals.total
                point = (entry.feet[0], entry.feet[1] + 1, entry.feet[2])
                run.step({ana: (*grenade_aim(run, ana, point), 16)})
                for _ in range(40):
                    if any(rocket.shooter is ana for rocket in fight.rockets):
                        break
                    run.step({})
                self.assertTrue(any(rocket.shooter is ana for rocket in fight.rockets))
                ana.team = 1
                for _ in range(90):
                    run.step({})
                self.assertEqual(vitals.total, before + (-fight.healing.spec.damage if enemy else 100))
                self.assertEqual(status_until(fight, vitals, allied=False) > fight.now, enemy)
                self.assertEqual(status_until(fight, vitals, allied=True) > fight.now, not enemy)

    def test_grenade_self_healing_and_full_health_ally_collision(self):
        run, ana, _target, entry = self.setup_fight(distance=2.5)
        fight = run.match.combat
        own = next(t for t in fight._targets() if t.owner is ana)
        vitals = fight.vitals_of(own)
        vitals.damage(150)
        self.cast(run, ana, entry)
        self.assertEqual(vitals.total, 150)
        self.assertGreater(status_until(fight, vitals, allied=True), fight.now)
        self.assertEqual(fight.vitals_of(entry).total, 200)
        self.assertGreater(status_until(fight, fight.vitals_of(entry), allied=True), fight.now)

    def test_grenade_miss_has_no_healing_or_status(self):
        run, ana, _target, entry = self.setup_fight()
        fight = run.match.combat
        vitals = fight.vitals_of(entry)
        vitals.damage(100)
        run.step({ana: (0, -16000, 0x10)})
        for _ in range(90):
            run.step({})
        self.assertEqual(vitals.total, 100)
        self.assertEqual(status_until(fight, vitals, allied=True), 0)
        self.assertEqual(status_until(fight, vitals, allied=False), 0)

    def test_wall_explosion_heals_source_side_and_does_not_hit_through_wall(self):
        run, ana, _target, entry = self.setup_fight(enemy=True)
        fight = run.match.combat
        x, y, z = ana.mover.position
        wall_z = z + 2.5
        floor = [(x - 50, y, z - 50, x - 50, y, z + 50, x + 50, y, z + 50),
                 (x - 50, y, z - 50, x + 50, y, z + 50, x + 50, y, z - 50)]
        wall = [(x - 3, y, wall_z, x + 3, y, wall_z, x + 3, y + 4, wall_z),
                (x - 3, y, wall_z, x + 3, y + 4, wall_z, x - 3, y + 4, wall_z)]
        run.match.collision_world = World([*floor, *wall])
        own = next(t for t in fight._targets() if t.owner is ana)
        own_vitals = fight.vitals_of(own)
        own_vitals.damage(150)
        self.cast(run, ana, entry)
        self.assertEqual(own_vitals.total, 150)
        self.assertEqual(fight.vitals_of(entry).total, 200)
        self.assertEqual(status_until(fight, fight.vitals_of(entry), allied=False), 0)


if __name__ == "__main__":
    unittest.main()
