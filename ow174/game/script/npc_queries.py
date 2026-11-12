"""Typed host calls for two authored NPC controller queries."""

from collections.abc import Callable
from dataclasses import dataclass

from ow174.game.script import expr, play

ENTITY_QUERY = 'STU_A37A910D'
PAIR_QUERY = 'STU_D379F515'


@dataclass(frozen=True)
class Context:
    entity: Callable[[int, int], int]
    pair: Callable[[int, int], bool]

    def __post_init__(self):
        if not callable(self.entity) or not callable(self.pair):
            raise ValueError('NPC query callbacks must be callable')


def evaluate(evaluation, cfg):
    cls = cfg.get('$')
    if cls not in (ENTITY_QUERY, PAIR_QUERY):
        return NotImplemented
    instance, watcher = evaluation.instance, evaluation.watcher
    component = instance.component
    world = component.world
    context = world.npc_query_context(component.entity)
    if context is NotImplemented:
        return NotImplemented

    def scalar(field):
        return play.evaluate_scalar(instance, cfg.get(field), watcher=watcher)

    def check_context():
        component.ensure_valid()
        if (world.npc_query_context(component.entity) is not context
                or component.instances.get(instance.id) is not instance
                or instance.stopped or instance.teardown):
            component.unsupported(instance, 'NPC query callback lifetime', cls)

    if cls == ENTITY_QUERY:
        owner = expr.to_entity(scalar('m_152B6961')) & 0xFFFFFFFF
        selector = expr.to_int(scalar('m_A60150E2'))
        check_context()
        result = context.entity(owner, selector)
        valid = type(result) is int and 0 <= result <= 0xFFFFFFFF
    else:
        target = expr.to_entity(scalar('m_target')) & 0xFFFFFFFF
        owner = expr.to_entity(scalar('m_7826B991')) & 0xFFFFFFFF
        check_context()
        result = context.pair(owner, target)
        valid = type(result) is bool
    check_context()
    if not valid:
        component.unsupported(instance, 'NPC query callback result', cls)
    return expr.Entity(result) if cls == ENTITY_QUERY else result
