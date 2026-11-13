"""Real orb input: Pet source on self, allied and hostile observer creates."""

import unittest
from unittest.mock import patch

from test_game_combat import in_world
from test_game_moira_orbs import MoiraOrbTests

from ow174.game import world


class MoiraOrbPetSourceTests(unittest.TestCase):
    def test_both_original_pet_orbs_keep_cast_body_for_every_viewer_and_delayed_baseline(self):
        for healing in (False, True):
            with self.subTest(healing=healing):
                fixture = MoiraOrbTests()
                run, caster, ally_target, _ = fixture.setup_fight(ally=True, distance=1000)
                match = run.match
                ally = ally_target.owner
                enemy = match.add_player(3, 'Enemy observer', ally.hero.guid, 1 - caster.team, False)
                in_world(match, enemy)
                run.players.append(enemy)
                enemy.mover.position = [caster.mover.position[0] + 20, *caster.mover.position[1:]]
                for _ in range(4):
                    run.step({})
                publish = match.combat.projectiles._show_others

                def delay_owner_baseline(*args, caster=caster, publish=publish, **kwargs):
                    stream = caster.body_script.stream
                    saved, stream.base = stream.base, None
                    try:
                        return publish(*args, **kwargs)
                    finally:
                        stream.base = saved

                with patch.object(match.combat.projectiles, '_show_others', side_effect=delay_owner_baseline):
                    orb = fixture.launch(run, caster, healing=healing)
                shot = orb.shot
                source = caster.body
                self.assertEqual({d.viewer for d in shot.observers}, {caster, ally, enemy})
                delayed = next(d for d in shot.observers if d.viewer is caster)
                self.assertIsNotNone(delayed.pending_create)
                run.step({})
                self.assertIsNone(delayed.pending_create)
                creates = []
                for delivery in shot.observers:
                    create, = [u for u in delivery.client.entities
                               if u.entity == delivery.record.ids[0] and u.op == world.OP_CREATE]
                    creates.append(create)
                    with patch.object(world, 'create', wraps=world.create) as build:
                        self.assertTrue(create.build(0).getvalue())
                    self.assertEqual(build.call_args.args[2], {72: [source]})
                    self.assertEqual(delivery.launch.entity, source)
                self.assertEqual(caster.team, ally.team)
                self.assertNotEqual(caster.team, enemy.team)
                # Body-attached effects retain the caster's actual team for
                # existing observers and the normal late-join body path.
                late = match.add_player(4, 'Late observer', ally.hero.guid, enemy.team, False)
                in_world(match, late)
                for viewer in (caster, ally, enemy, late):
                    create = (match.remote_body_create(viewer, caster) if viewer is not caster
                              else world.EntityUpdate(caster.body, world.OP_CREATE,
                                                      match._body_builder(caster, None, own=True)))
                    with patch.object(world, 'create', wraps=world.create) as build:
                        create.build(0)
                    self.assertEqual(build.call_args.args[2][26], [caster.team_bit])
                self.assertTrue(match.switch_hero(caster, caster.hero, preserve_pose=True, start_frame=18000))
                self.assertNotEqual(caster.body, source)
                for create in creates:
                    with patch.object(world, 'create', wraps=world.create) as build:
                        create.build(0)
                    self.assertEqual(build.call_args.args[2], {72: [source]})


if __name__ == '__main__':
    unittest.main()
