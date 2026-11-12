"""Static combat against the reconstructed host for Zarya's authored personal Pet."""

import unittest
from itertools import product

import test_zenyatta_combat as weapons
from test_game_combat import aim, in_world

from ow174.game import combat
from ow174.game.barriers import PROJECTED, ZARYA
from ow174.game.collision import World
from ow174.game.commands import FIRE, SECONDARY
from ow174.game.content import PRACTICE_RANGE, SOLDIER, heroes


class ZenyattaBubbleTests(unittest.TestCase):
    def test_zarya_energy_combines_with_discord_once_when_two_orbs_target_zen(self):
        run = weapons.Run(PRACTICE_RANGE, ('Zen', 'Zarya', 'Discord1', 'Discord2'))
        zen, zarya, *discord_casters = run.players
        run.match.bots.clear()
        x, y, z = zen.mover.position
        for player, hero, team, offset in (
            (zen, weapons.ZENYATTA, 0, (0, 0)),
            (zarya, ZARYA, 1, (0, 8)),
            (discord_casters[0], weapons.ZENYATTA, 1, (3, 7)),
            (discord_casters[1], weapons.ZENYATTA, 1, (-3, 7)),
        ):
            run.match.switch_hero(player, heroes()[hero])
            player.team = team
            player.mover.position = [x+offset[0], y, z+offset[1]]
            player.mover.body = None
        for _ in range(20):
            run.step({})
        fight = run.match.combat
        run.step({zarya: (0, 0, 8)})
        run.step({zen: (0, 600, FIRE)})
        for _ in range(25):
            run.step({})
        self.assertAlmostEqual(self.energy(zarya), 9.6, places=5)
        run.step({
            caster: (*aim(fight._eye(caster), (x, y+1.5, z)), 8)
            for caster in discord_casters
        })
        for caster in discord_casters:
            self.assertEqual(fight.zenyatta.owner(caster).discord.target, zen.body)

        angles = aim(fight._eye(zarya), (x, y+1, z))
        for shot, scale in enumerate((1.25, 1.25, 1.0)):
            before = self.health(run, zen)
            run.step({zarya: (*angles, SECONDARY)})
            run.step({zarya: (*angles, 0)})
            projectile, = fight.rockets
            if shot == 0:
                self.assertAlmostEqual(projectile.blast.amount, 51.608, places=4)
            if shot == 2:
                run.match.switch_hero(discord_casters[1], heroes()[SOLDIER])
            for _ in range(75):
                run.step({zarya: (*angles, 0)})
            self.assertFalse(fight.rockets)
            self.assertAlmostEqual(before-self.health(run, zen), projectile.blast.amount*scale, places=4)
            if shot == 0:
                run.match.switch_hero(discord_casters[0], heroes()[SOLDIER])
                self.assertEqual(fight.zenyatta.owner(discord_casters[1]).discord.target, zen.body)

    def arena(self, distance=8, projected=False):
        names = ('Zen', 'Zarya', 'Ally') if projected else ('Zen', 'Zarya')
        run = weapons.Run(PRACTICE_RANGE, names)
        zen, zarya = run.players[:2]
        run.match.bots.clear()
        run.match.switch_hero(zen, heroes()[weapons.ZENYATTA])
        run.match.switch_hero(zarya, heroes()[ZARYA])
        zen.team, zarya.team = 0, 1
        x, y, z = zen.mover.position
        zarya.mover.position = [x, y, z + distance]
        zarya.mover.body = None
        if projected:
            ally = run.players[2]
            ally.team = zarya.team
            ally.mover.position = [x, y, z + distance]
            ally.mover.body = None
            zarya.mover.position[0] += 4
        for _ in range(20):
            run.step({})
        return run, zen, zarya

    def projection(self, run, zarya, ally):
        x, y, z = ally.mover.position
        yaw, pitch = aim(run.match.combat._eye(zarya), (x, y + 1.5, z))
        run.step({zarya: (yaw, pitch, 16)})

    def test_projected_bubble_catches_reflected_orb(self):
        genji_hero = 0x02E0000000000029
        for active in (False, True):
            with self.subTest(projection=active):
                run, zen, zarya = self.arena()
                match = run.match
                zen.team = zarya.team = 0
                genji = match.add_player(3, "Genji", genji_hero, 1, False)
                in_world(match, genji)
                self.assertTrue(match.switch_hero(genji, heroes()[genji_hero]))
                run.players.append(genji)
                x, y, z = zen.mover.position
                zarya.mover.position = [x+4, y, z+3]
                genji.mover.position = [x, y, z+13]
                zarya.mover.body = genji.mover.body = None
                fight = match.combat
                guard = {
                    genji: (*aim(fight._eye(genji), (x, y+1, z)), 16),
                }
                for _ in range(20):
                    run.step(guard)
                energy = self.energy(zarya) or 0
                if active:
                    direction = aim(fight._eye(zarya), (x, y+1.5, z))
                    run.step({**guard, zarya: (*direction, 16)})
                    bubble = fight.barriers.bubbles[zarya.body, PROJECTED]
                    self.assertEqual(bubble.target, zen.body)
                    self.assertEqual(bubble.hp, 200)
                direction = aim(fight._eye(zen), (x, y+1, z+13))
                run.step({**guard, zen: (*direction, FIRE)})
                orb, = fight.rockets
                for _ in range(35):
                    run.step(guard)
                    if orb not in fight.rockets:
                        break
                self.assertIs(orb.shooter, genji)
                self.assertNotIn(orb, fight.rockets)
                self.assertEqual(self.health(run, zen), 200 if active else 152)
                self.assertEqual(self.health(run, genji), 200)
                if active:
                    self.assertEqual(bubble.hp, 152)
                self.assertAlmostEqual((self.energy(zarya) or 0)-energy, 9.6 if active else 0, places=4)
                self.assertEqual(len(genji.client.messages(combat.HIT)), 0 if active else 1)
                self.assertFalse(zen.client.messages(combat.HIT))

    def test_projected_pet_protects_actual_selected_ally_and_cleanses_discord(self):
        run, zen, zarya = self.arena(projected=True)
        ally = run.players[2]
        fight = run.match.combat
        before = self.health(run, ally)
        run.step({zen: (0, 0, 8)})
        self.assertEqual(fight.zenyatta.owner(zen).discord.target, ally.body)
        charge = zen.body_script.component.entity_var(342)
        charge_before, frame_before = charge.value(), run.frame
        run.step({zen: (0, 600, FIRE)})
        orb, = fight.rockets
        self.assertIs(orb.shooter, zen)
        self.assertEqual(self.health(run, ally), before)
        self.projection(run, zarya, ally)
        bubble = fight.barriers.bubbles[zarya.body, 0x025C]
        self.assertEqual((bubble.target, bubble.hp), (ally.body, 200))
        self.assertIsNone(fight.zenyatta.owner(zen).discord)
        self.assertIn(orb, fight.rockets)
        for _ in range(25):
            run.step({})
        self.assertNotIn(orb, fight.rockets)
        self.assertEqual(bubble.hp, 152)
        self.assertAlmostEqual(charge.value()-charge_before, (run.frame-frame_before)*combat.TICK*5, places=4)
        self.assertAlmostEqual(self.energy(zarya), 9.6, places=5)
        self.assertIsNone(ally.body_script.component.entity_var(87, create=False))
        self.assertEqual(self.health(run, ally), before)
        self.assertFalse(zen.client.messages(combat.HIT))
        run.match.switch_hero(zarya, heroes()[weapons.ZENYATTA])
        self.assertFalse(fight.barriers.bubbles)
        zarya.mover.position[0] += 12
        for _ in range(15):
            run.step({})
        run.step({zen: (0, 600, FIRE)})
        for _ in range(25):
            run.step({})
        self.assertEqual(self.health(run, ally), before - 48)

    def test_projection_cleanses_both_discords_without_clearing_other_target(self):
        run, zen, zarya = self.arena(projected=True)
        ally = run.players[2]
        fight = run.match.combat
        x, y, z = zen.mover.position
        for name, offset in (("Second Zen", -3), ("Other Zen", 6)):
            player = run.match.add_player(len(run.players) + 1, name, weapons.ZENYATTA, 0, False)
            in_world(run.match, player)
            self.assertTrue(run.match.switch_hero(player, heroes()[weapons.ZENYATTA]))
            player.team = zen.team
            player.mover.position = [x + offset, y, z]
            player.mover.body = None
            run.players.append(player)
        second, other = run.players[-2:]
        run.step({})

        def mark(caster, target):
            x, y, z = target.mover.position
            return (*aim(fight._eye(caster), (x, y + 1.5, z)), 8)

        owners = [fight.zenyatta.owner(caster) for caster in (zen, second, other)]
        marks = {zen: mark(zen, ally), second: mark(second, ally), other: mark(other, zarya)}
        run.step(marks)
        old = [owner.discord for owner in owners]
        self.assertTrue(all(orb is not None for orb in old))
        self.assertEqual([orb.target for orb in old], [ally.body, ally.body, zarya.body])

        before = self.health(run, ally)
        for _ in range(130):
            run.step({zen: (0, 600, SECONDARY)})
        self.projection(run, zarya, ally)
        bubble = fight.barriers.bubbles[zarya.body, PROJECTED]
        self.assertEqual(bubble.target, ally.body)
        self.assertIsNone(owners[0].discord)
        self.assertIsNone(owners[1].discord)
        self.assertIs(owners[2].discord, old[2])
        recovery = [owner.ready["discord"] for owner in owners[:2]]
        run.step({caster: marks[caster] for caster in (zen, second)})
        self.assertIsNone(owners[0].discord)
        self.assertIsNone(owners[1].discord)
        self.assertEqual([owner.ready["discord"] for owner in owners[:2]], recovery)

        for _ in range(60):
            run.step({})
        self.assertEqual(bubble.hp, 0)
        self.assertNotIn((zarya.body, PROJECTED), fight.barriers.bubbles)
        self.assertEqual(self.health(run, ally), before)
        self.assertFalse(zen.client.messages(combat.HIT))
        self.assertIsNone(owners[0].discord)
        self.assertIsNone(owners[1].discord)
        self.assertIs(owners[2].discord, old[2])

        run.step({caster: marks[caster] for caster in (zen, second)})
        for owner, retired in zip(owners[:2], old[:2], strict=True):
            self.assertIsNot(owner.discord, retired)
            self.assertEqual(owner.discord.target, ally.body)
        target = next(target for target in fight._targets() if target.owner is ally)
        x, y, z = target.feet
        body_aim = aim(fight._eye(zen), (x, y + target.height * .5, z))
        run.step({zen: (*body_aim, FIRE)})
        shots = {}
        for _ in range(25):
            for shot in fight.rockets:
                if shot.shooter is zen:
                    shots[id(shot)] = shot
            run.step({})
        shot, = shots.values()
        self.assertEqual(shot.direct.amount, 48)
        hit, = zen.client.messages(combat.HIT)
        self.assertEqual(hit["+0x78"]["+0x0"]["+0x0"], ally.body)
        critical = bool(hit["+0x78"]["+0x8"] & combat.CRITICAL)
        expected_damage = 120 if critical else 60
        self.assertEqual(hit["+0x78"]["+0x4"], expected_damage)
        self.assertEqual(self.health(run, ally), before - expected_damage)
        self.assertIs(owners[2].discord, old[2])

    def test_projection_rejects_wrong_team_out_of_range_and_occluded_targets(self):
        for rejection in ('team', 'range', 'wall'):
            with self.subTest(rejection=rejection):
                run, _, zarya = self.arena(projected=True)
                ally = run.players[2]
                if rejection == 'team':
                    ally.team = 0
                elif rejection == 'range':
                    ally.mover.position[2] += 40
                else:
                    x, y, z = ally.mover.position
                    wall = x + 2
                    run.match.collision_world = World([
                        (wall, y, z-3, wall, y+4, z-3, wall, y+4, z+3),
                        (wall, y, z-3, wall, y+4, z+3, wall, y, z+3),
                    ])
                self.projection(run, zarya, ally)
                for _ in range(15):
                    run.step({})
                self.assertFalse(run.match.combat.barriers.bubbles)

    def test_bubble_expiry_samples_the_orb_crossing_across_skipped_updates(self):
        def start(projected):
            run, zen, zarya = self.arena(distance=16, projected=projected)
            target = run.players[2] if projected else zarya
            if projected:
                self.projection(run, zarya, target)
            else:
                run.step({zarya: (0, 0, 8)})
            fight = run.match.combat
            bubble, = fight.barriers.bubbles.values()
            self.assertEqual((bubble.target, bubble.hp), (target.body, 200))
            x, y, z = target.mover.position
            return run, zen, zarya, target, bubble, aim(fight._eye(zen), (x, y+0.75, z))

        for projected in (False, True):
            run, zen, _, _, bubble, angles = start(projected)
            run.step({zen: (*angles, FIRE)})
            launch = run.frame
            for _ in range(30):
                run.step({})
                if bubble.hp < 200:
                    break
            self.assertEqual(bubble.hp, 152)
            travel = run.frame-launch
            self.assertGreater(travel, 4)
            for before_expiry, skipped in product((False, True), repeat=2):
                with self.subTest(projected=projected, before_expiry=before_expiry, skipped=skipped):
                    run, zen, zarya, target, bubble, angles = start(projected)
                    fight = run.match.combat
                    health = self.health(run, target)
                    expiry = round(bubble.expires/combat.TICK)
                    crossing = expiry-int(before_expiry)
                    while run.frame < expiry-3:
                        run.step({zen: (*angles, FIRE)} if run.frame+1 == crossing-travel else {})
                    self.assertTrue(any(rocket.shooter is zen for rocket in fight.rockets))
                    self.assertEqual(bubble.hp, 200)
                    if skipped:
                        run.frame += 4
                        run.step({})
                    else:
                        for _ in range(5):
                            run.step({})
                    self.assertEqual(run.frame, expiry+2)
                    for _ in range(25):
                        run.step({})
                    self.assertFalse(fight.rockets)
                    self.assertFalse(fight.barriers.bubbles)
                    self.assertEqual(bubble.hp, 152 if before_expiry else 200)
                    self.assertEqual(self.health(run, target), health if before_expiry else health-48)
                    self.assertAlmostEqual(self.energy(zarya) or 0, 9.6 if before_expiry else 0, places=4)
                    hits = zen.client.messages(combat.HIT)
                    self.assertEqual(len(hits), 0 if before_expiry else 1)
                    if hits:
                        self.assertEqual(hits[0]["+0x78"]["+0x0"]["+0x0"], target.body)

    def test_burst_breaks_projected_pet_and_starts_cooldown(self):
        run, zen, zarya = self.arena(projected=True)
        ally = run.players[2]
        fight = run.match.combat
        before = self.health(run, ally)
        for _ in range(130):
            run.step({zen: (0, 600, SECONDARY)})
        self.projection(run, zarya, ally)
        bubble = fight.barriers.bubbles[zarya.body, 0x025C]
        for _ in range(60):
            run.step({})
        self.assertEqual(bubble.hp, 0)
        self.assertFalse(fight.barriers.bubbles)
        self.assertEqual(self.health(run, ally), before)
        self.assertFalse(zen.client.messages(combat.HIT))
        instance = zarya.body_script.component.instances[bubble.instance]
        self.assertGreater(instance.vars[61].value(), 7)
        self.assertAlmostEqual(self.energy(zarya), 40, places=5)

    def health(self, run, zarya):
        fight = run.match.combat
        return fight.vitals_of(next(t for t in fight._targets() if t.owner is zarya)).total

    def energy(self, zarya):
        component = zarya.body_script.component
        owner = next(instance for instance in component.instances.values() if instance.graph.index == 0x0242)
        return owner.find_var(1, 87, create=False).value()

    def test_personal_bubble_cleanses_discord_and_blocks_primary_until_expiry(self):
        run, zen, zarya = self.arena()
        fight = run.match.combat
        before = self.health(run, zarya)
        run.step({zen: (0, 0, 8)})
        self.assertEqual(fight.zenyatta.owner(zen).discord.target, zarya.body)
        run.step({zarya: (0, 0, 8)})
        run.step({zen: (0, 600, FIRE)})
        for _ in range(65):
            run.step({})
        self.assertIsNone(fight.zenyatta.owner(zen).discord)
        self.assertEqual(fight.barriers.bubbles[zarya.body, 0x0244].hp, 152)
        self.assertAlmostEqual(self.energy(zarya), 9.6, places=5)
        self.assertEqual(self.health(run, zarya), before)
        self.assertFalse(zen.client.messages(combat.HIT))
        run.step({zen: (0, 0, 8)})
        self.assertIsNone(fight.zenyatta.owner(zen).discord)
        for _ in range(70):
            run.step({})
        run.step({zen: (0, 600, FIRE)})
        for _ in range(20):
            run.step({})
        self.assertEqual(self.health(run, zarya), before - 48)
        self.assertLess(self.energy(zarya), 9.6)
        self.assertGreater(self.energy(zarya), 0)

    def test_burst_breaks_personal_bubble_and_excess_hit_is_consumed(self):
        run, zen, zarya = self.arena()
        before = self.health(run, zarya)
        for _ in range(130):
            run.step({zen: (0, 600, SECONDARY)})
        run.step({zarya: (0, 0, 8)})
        bubble = run.match.combat.barriers.bubbles[zarya.body, 0x0244]
        for _ in range(60):
            run.step({})
        self.assertEqual(bubble.hp, 0)
        self.assertFalse(run.match.combat.barriers.bubbles)
        instance = zarya.body_script.component.instances[bubble.instance]
        self.assertGreater(instance.vars[61].value(), 9)
        self.assertAlmostEqual(self.energy(zarya), 40, places=5)
        self.assertEqual(self.health(run, zarya), before)
        self.assertFalse(zen.client.messages(combat.HIT))

    def test_melee_consumes_personal_bubble_without_a_body_hit(self):
        run, zen, zarya = self.arena(distance=2)
        before = self.health(run, zarya)
        run.step({zarya: (0, 0, 8)})
        run.step({zen: (0, 600, 0x800)})
        for _ in range(35):
            run.step({})
        self.assertEqual(run.match.combat.barriers.bubbles[zarya.body, 0x0244].hp, 170)
        self.assertAlmostEqual(self.energy(zarya), 6, places=5)
        self.assertEqual(self.health(run, zarya), before)
        self.assertFalse(zen.client.messages(combat.HIT))

    def test_zarya_energy_boosts_actual_secondary_damage_and_transcendence_blocks_it(self):
        for charged, immune, expected_damage in ((False, False, 47), (True, False, 51.608), (True, True, 0)):
            with self.subTest(charged=charged, immune=immune):
                run, zen, zarya = self.arena()
                fight = run.match.combat
                if charged:
                    run.step({zarya: (0, 0, 8)})
                    run.step({zen: (0, 600, FIRE)})
                    for _ in range(25):
                        run.step({})
                    self.assertAlmostEqual(self.energy(zarya), 9.6, places=5)
                if immune:
                    zen.body_script.set_ult_charge(zen.body_script.component.entity_var(338).value())
                    run.step({zen: (0, 0, 0x20)})
                    self.assertTrue(fight.zenyatta.ultimate(zen))
                x, y, z = zen.mover.position
                angles = aim(fight._eye(zarya), (x, y + 1, z))
                before = self.health(run, zen)
                run.step({zarya: (*angles, SECONDARY)})
                run.step({zarya: (*angles, 0)})
                projectile, = fight.rockets
                self.assertIs(projectile.shooter, zarya)
                self.assertAlmostEqual(projectile.blast.amount, 51.608 if charged else 47, places=4)
                for _ in range(25):
                    run.step({zarya: (*angles, 0)})
                self.assertFalse(fight.rockets)
                self.assertAlmostEqual(before - self.health(run, zen), expected_damage, places=4)
