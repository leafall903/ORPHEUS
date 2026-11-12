"""Actual13C2 filter stages over explicit normal-Match player inputs.

Native stage order/masks/condition/empty-rule path are shared with filter_game.
The live Match namespace and static replicated category bits are reconstructed
host services. This does not grant general native component/status projection,
controller effect-variable routing, acquisition or target pin/damage consumers.
"""

import math

from ow174.game import defenses, winston
from ow174.game.script import expr, filter_callbacks, filter_game, graph, play


def supported(config):
    if not isinstance(config, dict) or config.get('$') != 'STUConfigVarFilter':
        return False
    if tuple(config.get(k, 0) for k in filter_game.MASKS) not in (
            (0x40000, 0, 0x130, 0, 0, 0), (0x40000, 0, 0x138, 0, 0, 1)):
        return False
    # Only the exact two authored13C2 filters. Nonempty rules, extension and
    # script-filter services are not inferred from their absence in this tuple.
    record = graph.graph(0x13C2)
    expected = (record.node(4).field('m_filter'), record.node(10).field('m_condition')['m_filter'])
    return config in expected and filter_callbacks.empty_result(config) == 2


class Services:
    def __init__(self, match, source_actor=None):
        self.match = match
        self.source_actor = source_actor

    def lookup(self, entity):
        for player in self.match.players:
            if (player.entity == entity and not player.gone and player.has_body and player.spawned
                    and player.hero.guid == winston.HERO and player.body_script is not None
                    and not player.body_script.retired and not player.body_script.component.owner_dead
                    and not self.match.combat.is_dead(player)):
                component = player.body_script.component
                flag = component.entity_var(14695, create=False)
                #000E.003 declares an initially empty controller script component.
                # A body flag cannot silently become that controller's flag. Such
                # effect-routing/status combinations remain an unsupported gate.
                if ((flag is not None and flag.value() is not None)
                        or defenses.intangible(player) or self.match.combat.is_stunned(player)):
                    return None
                return player
        return None

    def has_component(self, candidate, kind):
        if kind != 97:
            raise ValueError('unsupported Assassin filter component query')
        return True  #000E.003 STUStatescriptComponent, also present in captured controller masks

    def bits(self, candidate):
        from ow174.game.match import PLAYER_FILTER, TEAM_BIT

        if candidate is self.source_actor:
            return TEAM_BIT << candidate.team  # actual NPC body create's component26
        return PLAYER_FILTER | candidate.team_bit

    def source(self):
        # AC829876/node10 has no relation masks. TrackTargets/node4 supplies
        # the actual possessed NPC body for its excluded-relation mask1.
        return self.source_actor

    def observe(self, *_):
        # Evaluation already registered AC829876 in its native per-frame poll.
        pass

    def missing_target(self, *_):
        pass

    def relationship(self, source, target, spectator):
        if spectator or source is not self.source_actor or source is None:
            raise ValueError('Assassin spectator/unknown relationship service unavailable')
        # Explicit ordinary Match enemy relation reconstruction. This is not a
        # native C1CF60/C1C690 implementation or a guessed spectator policy.
        return self.match.game_map.free_for_all or source.team != target.team

    def observe_script_filters(self, *_):
        raise ValueError('Assassin scripted filter service unavailable')

    def script_filters(self, *_):
        raise ValueError('Assassin scripted filter service unavailable')

    def engine_filter(self, *_):
        raise ValueError('Assassin nonempty callback rules unavailable')

    def extension_filter(self, *_):
        raise ValueError('Assassin extension filter service unavailable')


def evaluate(world, instance, config):
    if (world.match is None or instance.graph.index != 0x13C2
            or config.get('$') != 'STU_AC829876' or not supported(config.get('m_filter'))):
        return NotImplemented
    target = expr.to_entity(play.evaluate_scalar(instance, config.get('m_entity'), initial=expr.Entity(0)))
    return filter_game.evaluate(instance, config['m_filter'], target, services=Services(world.match))


def track_targets(world, state):
    if world.match is None or state.instance.graph.index != 0x13C2 or state.node.pos != 4:
        return NotImplemented
    config = state.node.field('m_filter')
    if not supported(config):
        return NotImplemented
    origin = expr.to_entity(state.evaluate(state.node.field('m_8DCA77B6'), watch=False))
    source = next((b for b in world.match.bots if b.entity == origin and not b.dead), None)
    if source is None:
        return ()
    services = Services(world.match, source)
    radius = expr.to_float(state.evaluate(state.node.field('m_radius'), watch=False))
    return tuple(p.entity for p in world.match.players
                 if math.dist(source.position, p.mover.position) <= radius
                 and filter_game.evaluate(state.instance, config, p.entity, services=services))
