"""104319 A58C1E63/CE5020 front end with explicit relationship services.

The C1CF60 backend includes associated objects and special teams. It is required
from the host; this module never substitutes a team comparison for that backend.
"""

from dataclasses import dataclass

from ow174.game.script import expr, play

CLASS = "STU_A58C1E63"


@dataclass(frozen=True)
class Target:
    entity: int
    payload: object = None

    def __post_init__(self):
        if type(self.entity) is not int or not 0 <= self.entity <= 0xFFFFFFFF:
            raise ValueError("relationship target must have a uint32 identity")


@dataclass
class Context:
    owner: int
    context_present: bool
    lookup: object = NotImplemented
    relate: object = NotImplemented
    watch_context: object = NotImplemented  # None declares no dependency hub.
    retired: bool = False

    def __post_init__(self):
        if (type(self.owner) is not int or not 0 <= self.owner <= 0xFFFFFFFF
                or not expr.valid_entity(self.owner)):
            raise ValueError("invalid relationship query owner")
        if type(self.context_present) is not bool:
            raise ValueError("relationship context availability must be known")

    def retire(self):
        self.retired = True


def evaluate(evaluation, cfg):
    if cfg.get("$") != CLASS:
        return NotImplemented
    instance, watcher = evaluation.instance, evaluation.watcher
    component = instance.component
    context = component.world.entity_relation_context(component.entity, component=component)
    if context is NotImplemented:
        return NotImplemented

    def unsupported(feature):
        component.unsupported(instance, feature, CLASS)

    def check():
        component.ensure_valid()
        bound = component.world.entity_relation_context(component.entity, component=component)
        if (type(context) is not Context or context.owner != component.entity or context.retired
                or instance.stopped or instance.teardown
                or component.instances.get(instance.id) is not instance or bound is not context):
            unsupported("entity relationship owner retired during callback")
        if type(context.context_present) is not bool:
            unsupported("entity relationship context availability")

    def call(callback, feature, *args):
        component.consume_work(feature)
        if not callable(callback):
            unsupported(feature)
        value = callback(*args)
        check()
        return value

    def selector(name, initial):
        value = play.evaluate_scalar(instance, cfg.get(name), initial=initial, watcher=watcher)
        check()
        return value

    def lookup(identifier):
        result = call(context.lookup, "entity relationship lookup", identifier)
        if result is not None and (type(result) is not Target or result.entity != identifier):
            unsupported("entity relationship lookup result")
        return result

    with component.operation():
        check()
        if not context.context_present:
            return expr.EVALUATION_FAILED
        # CE5086 subscribes kind2 before any child config. No hub is distinct
        # from an unknown dependency service.
        if watcher is not None and context.watch_context is not None:
            call(context.watch_context, "entity relationship dependency", watcher, 2)
        target_id = expr.to_entity(selector("m_target", expr.Entity(0))) & 0xFFFFFFFF
        source_id = expr.to_entity(selector("m_source", expr.Entity(0))) & 0xFFFFFFFF
        flag = expr.truthy(selector("m_AFBBE9C3", False))
        # CE51E4 walks source first, then CE5270 target. Even invalid/zero
        # identities go through lookup; missing objects still enter C1CF60.
        source = lookup(source_id)
        target = lookup(target_id)
        result = call(context.relate, "entity relationship backend", target, source, flag, watcher)
        if type(result) is not bool:
            unsupported("entity relationship backend result")
        return result
