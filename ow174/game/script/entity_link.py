"""104319 F76C486C: CE2CF0/D1BEE0's component-linked uint32 identity.

The component's producer and gameplay meaning are separate from this query.
Its +D8 field and +20 dependency must not be substituted with component43.
"""

from dataclasses import dataclass

from ow174.game.script import expr, play

CLASS = "STU_F76C486C"


@dataclass(frozen=True)
class Target:
    entity: int
    linked: int | None  # None: known missing component; zero: a present null identity.

    def __post_init__(self):
        if type(self.entity) is not int or not 0 <= self.entity <= 0xFFFFFFFF:
            raise ValueError("linked entity target must be uint32")
        if self.linked is not None and (type(self.linked) is not int or not 0 <= self.linked <= 0xFFFFFFFF):
            raise ValueError("linked identity must be uint32")


@dataclass
class Context:
    owner: int
    instance_present: bool
    environment_present: bool
    lookup: object = NotImplemented
    watch_environment: object = NotImplemented
    watch_link: object = NotImplemented
    retired: bool = False


def evaluate(evaluation, cfg):
    if cfg.get("$") != CLASS:
        return NotImplemented
    instance, watcher = evaluation.instance, evaluation.watcher
    component = instance.component
    context = component.world.entity_link_context(component.entity)
    if context is NotImplemented:
        return NotImplemented

    def unsupported(feature):
        component.unsupported(instance, feature, CLASS)

    def check():
        component.ensure_valid()
        if (type(context) is not Context or context.owner != component.entity or context.retired
                or instance.stopped or instance.teardown
                or component.instances.get(instance.id) is not instance
                or component.world.entity_link_context(component.entity) is not context):
            unsupported("entity link retired context")
        if type(context.instance_present) is not bool or type(context.environment_present) is not bool:
            unsupported("entity link availability")

    def call(callback, feature, *args):
        if not callable(callback):
            unsupported(feature)
        result = callback(*args)
        check()
        return result

    check()
    if not context.instance_present:
        return expr.EVALUATION_FAILED
    value = play.evaluate_scalar(instance, cfg.get("m_D63CBEC2"), initial=expr.Entity(0), watcher=watcher)
    check()
    identifier = expr.to_entity(value) & 0xFFFFFFFF
    target = call(context.lookup, "entity link lookup", identifier)
    if target is None:
        if watcher is not None and context.watch_environment is not None:
            call(context.watch_environment, "entity link environment subscription", watcher,
                 0 if context.environment_present else 46)
        # D1BEE0 writes the fallback word (3241288 == 0), unlike F726's
        # missing-entity failure that preserves the caller's destination.
        return expr.Entity(0)
    if type(target) is not Target or target.entity != identifier:
        unsupported("entity link target")
    if target.linked is None:
        return expr.Entity(0)
    if watcher is not None:
        call(context.watch_link, "entity link subscription", watcher, target)
    return expr.Entity(target.linked)
