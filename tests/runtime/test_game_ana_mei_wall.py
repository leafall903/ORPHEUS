"""Ana's actual fire against reconstructed Mei Ice Wall pillars."""
import unittest

from test_game_ana_nano import AnaNanoTests as NanoFixture
from test_game_combat import aim
from ow174.game.collision import World
from ow174.game.commands import Command
from ow174.game.content import find_hero
from ow174.game.mover import JUMP


class AnaMeiWallTests(unittest.TestCase):
    @staticmethod
    def move_ana(run, ana, *, forward=0, buttons=0):
        run.frame += 1
        run.match.update(run.frame * 0.016, run.frame)
        for player in run.players:
            command = Command(run.frame, forward=forward, buttons=buttons) if player is ana else Command(run.frame)
            player.take_commands([command])

    def deploy(self, *, ally=False):
        run, ana, mei, _ = NanoFixture().fight(ally_hero='Mei', distance=8)
        mei.team = 0 if ally else 1
        fight = run.match.combat
        x, y, z = mei.mover.position
        placement = aim(fight._eye(mei), (x, y, z - 4))
        for buttons in (16, 0, 1, 0):
            run.step({mei: (*placement, buttons)})
        pieces = list(fight.ice_wall.pieces.values())
        self.assertEqual(len(pieces), 5)
        self.assertTrue(all(piece.health == 400 for piece in pieces))
        centre = min(pieces, key=lambda piece: abs(piece.feet[0] - x))
        target = next(target for target in fight._targets() if target.owner is mei)
        vitals = fight.vitals_of(target)
        direction = aim(fight._eye(ana), (x, y + 1, z))
        return run, ana, mei, fight, pieces, centre, vitals, direction

    def test_scoped_fire_breaks_only_the_hit_pillar_then_reaches_mei(self):
        run, ana, _, fight, pieces, centre, vitals, direction = self.deploy()
        for _ in range(35):
            run.step({ana: (*direction, 2)})
        for expected, target_health in ((330, 250), (0, 250), (0, 180)):
            if centre.health == 330:
                centre.health = 60
            run.step({ana: (*direction, 3)})
            for _ in range(65):
                run.step({ana: (*direction, 2)})
            self.assertEqual(centre.health, expected)
            self.assertTrue(all(piece.health == 400 for piece in pieces if piece is not centre))
            self.assertEqual(vitals.total, target_health)
        self.assertEqual(vitals.total, 180)
        self.assertNotIn(centre.entity, fight.ice_wall.pieces)

    def test_hip_rifle_and_sleep_dart_hit_pillar_without_body_status(self):
        for button, damage in ((1, 70), (8, 5)):
            with self.subTest(button=button):
                run, ana, mei, fight, pieces, centre, vitals, direction = self.deploy()
                run.step({ana: (*direction, button)})
                for _ in range(70):
                    run.step({})
                self.assertEqual(centre.health, 400 - damage)
                self.assertTrue(all(piece.health == 400 for piece in pieces if piece is not centre))
                self.assertEqual(vitals.total, 250)
                self.assertFalse(fight.is_asleep(mei))

    def test_ana_cannot_walk_through_wall_and_crosses_after_recall(self):
        run, ana, mei, fight, _, centre, _, _ = self.deploy()
        for _ in range(90):
            self.move_ana(run, ana, forward=127)
        self.assertLess(ana.mover.position[2], centre.feet[2] - centre.depth)
        self.assertGreater(ana.mover.position[2], centre.feet[2] - 2)
        self.assertAlmostEqual(ana.mover.velocity[2], 0, places=3)
        run.step({mei: (0, 0, 16)})
        self.assertFalse(fight.ice_wall.pieces)
        for _ in range(45):
            self.move_ana(run, ana, forward=127)
        self.assertGreater(ana.mover.position[2], centre.feet[2] + 1)

    def lift_ana(self):
        run, ana, mei, _ = NanoFixture().fight(ally_hero='Mei', distance=8)
        fight = run.match.combat
        feet = tuple(ana.mover.position)
        placement = aim(fight._eye(mei), feet)
        for buttons in (16, 0, 1, 0):
            run.step({mei: (*placement, buttons)})
        piece = min(fight.ice_wall.pieces.values(), key=lambda p: abs(p.feet[0] - feet[0]))
        top = piece.feet[1] + piece.height
        self.assertAlmostEqual(ana.mover.position[1], top, delta=.002)
        return run, ana, mei, fight, piece, feet, top

    def test_wall_lifts_ana_and_supports_jump_landing_and_walk_off(self):
        run, ana, _, fight, piece, feet, top = self.lift_ana()
        for _ in range(20):
            self.move_ana(run, ana)
            self.assertAlmostEqual(ana.mover.position[1], top, delta=.002)
        self.move_ana(run, ana, buttons=JUMP)
        self.assertGreater(ana.mover.position[1], top + .05)
        for _ in range(65):
            self.move_ana(run, ana)
        self.assertAlmostEqual(ana.mover.position[1], top, delta=.002)
        for _ in range(90):
            self.move_ana(run, ana, forward=127)
        self.assertGreater(ana.mover.position[2], feet[2] + 2)
        self.assertAlmostEqual(ana.mover.position[1], feet[1], places=3)
        self.assertIn(piece.entity, fight.ice_wall.pieces)

    def test_wall_owner_replacement_removes_support_and_ana_lands(self):
        run, ana, mei, fight, _, feet, top = self.lift_ana()
        self.assertTrue(run.match.switch_hero(mei, find_hero("Soldier")))
        self.assertFalse(fight.ice_wall.pieces)
        for _ in range(16):
            self.move_ana(run, ana)
        self.assertLess(ana.mover.position[1], top)
        self.assertGreater(ana.mover.position[1], feet[1])
        for _ in range(80):
            self.move_ana(run, ana)
        self.assertAlmostEqual(ana.mover.position[1], feet[1], places=3)

    def test_occupied_column_is_omitted_when_ana_cannot_fit_above_it(self):
        run, ana, mei, _ = NanoFixture().fight(ally_hero='Mei', distance=8)
        x, y, z = ana.mover.position
        c = y + 4.8
        run.match.collision_world = World([
            (x - 30, y, z - 30, x - 30, y, z + 30, x + 30, y, z + 30),
            (x - 30, y, z - 30, x + 30, y, z + 30, x + 30, y, z - 30),
            (x - .7, c, z - .7, x + .7, c, z + .7, x - .7, c, z + .7),
            (x - .7, c, z - .7, x + .7, c, z - .7, x + .7, c, z + .7),
        ])
        fight = run.match.combat
        placement = aim(fight._eye(mei), (x, y, z))
        for buttons in (16, 0, 1, 0):
            run.step({mei: (*placement, buttons)})
        pieces = fight.ice_wall.pieces.values()
        self.assertEqual(len(pieces), 4)
        self.assertTrue(all(abs(piece.feet[0] - x) > 1 for piece in pieces))
        self.assertEqual(tuple(ana.mover.position), (x, y, z))

    def test_breaking_grenade_keeps_pillar_cover_for_its_splash(self):
        run, ana, mei, fight, _, centre, vitals, direction = self.deploy()
        x, y, z = centre.feet
        mei.spawn = (x, y, z + 1.5), mei.spawn[1]
        mei.mover = mei.new_mover()
        for _ in range(5):
            run.step({})
        centre.health = 40
        run.step({ana: (*direction, 16)})
        for _ in range(70):
            run.step({})
        self.assertEqual(centre.health, 0)
        self.assertNotIn(centre.entity, fight.ice_wall.pieces)
        self.assertEqual(vitals.total, 250)
        self.assertEqual(vitals.healing_block_until, 0)

    def test_allied_ice_wall_blocks_nano_without_spending_charge(self):
        run, ana, mei, fight, pieces, _, vitals, _ = self.deploy(ally=True)
        helper = NanoFixture()
        vitals.damage(100)
        cost = ana.body_script.component.vars[338].value()
        helper.cast(run, ana, mei)
        self.assertEqual(ana.body_script.component.vars[342].value(), cost)
        self.assertEqual(vitals.total, 150)
        self.assertEqual(vitals.nano_until, 0)
        self.assertTrue(all(piece.health == 400 for piece in pieces))
        x, y, z = mei.mover.position
        placement = aim(fight._eye(mei), (x, y, z - 4))
        run.step({mei: (*placement, 16)})
        self.assertFalse(fight.ice_wall.pieces)
        helper.cast(run, ana, mei, charged=False)
        self.assertEqual(ana.body_script.component.vars[342].value(), 0)
        self.assertEqual(vitals.total, 250)
        self.assertGreater(vitals.nano_until, fight.now)

    def test_allied_ice_wall_blocks_rifle_healing_and_occludes_grenade_splash(self):
        run, ana, mei, fight, pieces, centre, far_pool, direction = self.deploy(ally=True)
        near = run.players[2]
        near.team = ana.team
        x, y, z = centre.feet
        near.spawn = (x + 1.8, y, z - 1.5), near.spawn[1]
        near.mover = near.new_mover()
        far_pool.damage(150)
        near_pool = fight.vitals_of(NanoFixture.target(fight, near))
        near_pool.damage(150)

        run.step({ana: (*direction, 1)})
        for _ in range(65):
            run.step({ana: (*direction, 2)})
        run.step({ana: (*direction, 3)})
        for _ in range(65):
            run.step({ana: (*direction, 2)})
        self.assertEqual(far_pool.total, 100)
        self.assertEqual(near_pool.total, 50)
        self.assertTrue(all(piece.health == 400 for piece in pieces))

        run.step({ana: (*direction, 16)})
        for _ in range(70):
            run.step({})
        self.assertEqual(far_pool.total, 100)
        self.assertEqual(far_pool.healing_boost_until, 0)
        self.assertEqual(near_pool.total, 150)
        self.assertGreater(near_pool.healing_boost_until, fight.now)
        self.assertTrue(all(piece.health == 400 for piece in pieces))

        run.step({mei: (0, 0, 16)})
        self.assertFalse(fight.ice_wall.pieces)
        NanoFixture().single_shot(run, ana, mei)
        self.assertEqual(far_pool.total, 170)

    def test_melee_breaking_pillar_blocks_body_until_next_swing(self):
        run, ana, mei, fight, pieces, centre, vitals, _ = self.deploy()
        x, y, z = centre.feet
        for actor, offset in ((ana, -1.45), (mei, 0.7)):
            actor.spawn = (x, y, z + offset), actor.spawn[1]
            actor.mover = actor.new_mover()
        for _ in range(5):
            run.step({})
        direction = aim(fight._eye(ana), (x, y + 1.3, z + 0.7))
        for pillar_health, body_health in ((370, 250), (0, 250), (0, 220)):
            if centre.health == 370:
                centre.health = 25
            run.step({ana: (*direction, 0x800)})
            for _ in range(65):
                run.step({})
            self.assertEqual(centre.health, pillar_health)
            self.assertEqual(vitals.total, body_health)
        self.assertNotIn(centre.entity, fight.ice_wall.pieces)
        outer = [piece for piece in pieces if abs(piece.feet[0] - x) > 2]
        self.assertEqual(len(outer), 2)
        self.assertTrue(all(piece.health == 400 for piece in outer))

    def test_wall_expires_at_deadline_and_redeployment_waits_for_cooldown(self):
        run, ana, mei, fight, pieces, centre, vitals, direction = self.deploy()
        manager = fight.ice_wall
        ability = manager.instance(mei).state(33)
        deadline = centre.expires
        self.assertEqual({piece.expires for piece in pieces}, {deadline})
        self.assertEqual(deadline - run.frame, 312)
        while run.frame < deadline - 1:
            run.step({})
        self.assertEqual(len(manager.pieces), 5)
        self.assertGreater(ability.remaining(), 0)
        run.step({})
        self.assertEqual(run.frame, deadline)
        self.assertFalse(manager.pieces)
        x, y, z = mei.mover.position
        placement = aim(fight._eye(mei), (x, y, z - 4))
        for buttons in (16, 0, 1, 0):
            run.step({mei: (*placement, buttons)})
        self.assertFalse(manager.pieces)
        run.step({ana: (*direction, 1)})
        for _ in range(70):
            run.step({})
        self.assertEqual(vitals.total, 180)
        for _ in range(800):
            if ability.remaining() <= 0:
                break
            run.step({})
        self.assertLessEqual(ability.remaining(), 0)
        for buttons in (16, 0, 1, 0):
            run.step({mei: (*placement, buttons)})
        self.assertEqual(len(manager.pieces), 5)
        self.assertTrue(set(manager.pieces).isdisjoint(piece.entity for piece in pieces))

    def test_wall_preview_geometry_health_and_expiry_are_stored(self):
        import test_server_snapshot as stored

        from ow174.game.mei_wall import Input

        case = stored.ServerSnapshotTests()
        session, _, players = case.scene()
        player = players[0]
        fight = player.match.combat
        manager = fight.ice_wall

        def digest():
            return stored.snapshot_digest(case.capture(session))

        empty = digest()
        manager.inputs[player.body] = Input(buttons=16, preview=True)
        preview = digest()
        self.assertNotEqual(preview, empty)
        manager.inputs[player.body].rotated = True
        rotated = digest()
        self.assertNotEqual(rotated, preview)
        manager.create(player, [(0, 1, 4)], (1, 0, 0))
        [pillar] = manager.pieces.values()
        deployed = digest()
        self.assertNotEqual(deployed, rotated)
        self.assertEqual(digest(), deployed)
        manager.damage(pillar, 70)
        damaged = digest()
        self.assertNotEqual(damaged, deployed)
        pillar.expires += 1
        self.assertNotEqual(digest(), damaged)
        manager.update(pillar.expires)
        self.assertFalse(manager.pieces)
        self.assertNotEqual(digest(), deployed)

    def test_invalid_wall_confirmation_preserves_preview_then_rotates_and_deploys(self):
        run, _, mei, _ = NanoFixture().fight(ally_hero='Mei', distance=8)
        fight = run.match.combat
        manager = fight.ice_wall
        x, y, z = mei.mover.position
        placement = aim(fight._eye(mei), (x, y, z - 4))
        invalid = (placement[0], -12000)
        for buttons in (16, 0, 1, 0):
            run.step({mei: (*invalid, buttons)})
        self.assertFalse(manager.pieces)
        self.assertTrue(manager.inputs[mei.body].preview)
        ability = manager.instance(mei).state(33)
        self.assertEqual(ability.remaining(), 0)
        run.step({mei: (*placement, 2)})
        self.assertTrue(manager.inputs[mei.body].rotated)
        self.assertFalse(manager.pieces)
        run.step({mei: (*placement, 0)})
        run.step({mei: (*placement, 1)})
        self.assertFalse(manager.inputs[mei.body].preview)
        self.assertGreater(ability.remaining(), 0)
        pillars = list(manager.pieces.values())
        self.assertEqual(len(pillars), 5)
        self.assertTrue(all(abs(piece.feet[0] - x) < 0.02 for piece in pillars))
        depths = [piece.feet[2] for piece in pillars]
        self.assertAlmostEqual(max(depths) - min(depths), 6.2)
