"""104319 CDBDE0/D15520 direct component43 owner query.

This differs from DB742151's primary-possessor registry query. The host supplies
live entity lookup and component43 state; missing objects preserve output.
"""

from dataclasses import dataclass

from ow174.game.script import expr, play

CLASS = "STU_F7262B36"


@dataclass(frozen=True)
class Target:
    entity: int
    current: int | None  # None: known missing component43; zero: present with null owner.

    def __post_init__(self):
        if type(self.entity) is not int or not 0 <= self.entity <= 0xFFFFFFFF:
            raise ValueError("body owner target must be uint32")
        if self.current is not None and (type(self.current) is not int
                                         or not 0 <= self.current <= 0xFFFFFFFF):
            raise ValueError("body owner identity must be uint32")


@dataclass
class Context:
    owner: int
    instance_present: bool
    environment_present: bool
    lookup: object = NotImplemented
    watch_environment: object = NotImplemented
    watch_owner: object = NotImplemented
    retired: bool = False


def evaluate(evaluation, cfg):
    if cfg.get("$") != CLASS:
        return NotImplemented
    instance, watcher = evaluation.instance, evaluation.watcher
    component = instance.component
    context = component.world.body_owner_context(component.entity)
    if context is NotImplemented:
        return NotImplemented

    def unsupported(feature):
        component.unsupported(instance, feature, CLASS)

    def check():
        component.ensure_valid()
        if (type(context) is not Context or context.owner != component.entity or context.retired
                or instance.stopped or instance.teardown
                or component.instances.get(instance.id) is not instance
                or component.world.body_owner_context(component.entity) is not context):
            unsupported("body owner query retired context")
        if type(context.instance_present) is not bool or type(context.environment_present) is not bool:
            unsupported("body owner query availability")

    def call(callback, feature, *args):
        if not callable(callback):
            unsupported(feature)
        result = callback(*args)
        check()
        return result

    check()
    if not context.instance_present:
        return expr.EVALUATION_FAILED
    value = play.evaluate_scalar(instance, cfg.get("m_801B298F"), initial=expr.Entity(0), watcher=watcher)
    check()
    identifier = expr.to_entity(value) & 0xFFFFFFFF
    target = call(context.lookup, "body owner query lookup", identifier)
    if target is None:
        if watcher is not None and context.watch_environment is not None:
            call(context.watch_environment, "body owner environment subscription", watcher,
                 0 if context.environment_present else 46)
        return expr.EVALUATION_FAILED
    if type(target) is not Target or target.entity != identifier:
        unsupported("body owner query target")
    if target.current is None:
        return expr.Entity(0)
    if watcher is not None:
        call(context.watch_owner, "body owner subscription", watcher, target)
    return expr.Entity(target.current)
