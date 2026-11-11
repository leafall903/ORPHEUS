"""104319 CDBDE0/D15520 component43 relation query with explicit engine inputs."""

from dataclasses import dataclass

from ow174.game.script import expr, play

CLASS = 'STU_F7262B36'


@dataclass
class Relation:
    entity: int

    def __post_init__(self):
        if type(self.entity) is not int or not 0 <= self.entity <= 0xFFFFFFFF:
            raise ValueError('component43 relation must be a native uint32 identity')


@dataclass
class Target:
    entity: int
    relation: object = NotImplemented  # None declares known component43 absence.
    retired: bool = False

    def __post_init__(self):
        if type(self.entity) is not int or not 0 <= self.entity <= 0xFFFFFFFF:
            raise ValueError('relation lookup target must be a native uint32 identity')


@dataclass
class Context:
    owner: int
    context_present: bool
    namespace_present: bool
    environment_present: bool
    lookup: object = NotImplemented
    watch_environment: object = NotImplemented  # None declares no dependency hub.
    watch_relation: object = NotImplemented
    retired: bool = False

    def __post_init__(self):
        if (type(self.owner) is not int or not 0 <= self.owner <= 0xFFFFFFFF
                or not expr.valid_entity(self.owner)):
            raise ValueError('invalid relation-query owner')
        if any(type(value) is not bool for value in (
                self.context_present, self.namespace_present, self.environment_present)):
            raise ValueError('relation-query context availability must be known')

    def retire(self):
        self.retired = True


def evaluate(evaluation, cfg):
    if cfg.get('$') != CLASS:
        return NotImplemented
    instance, watcher = evaluation.instance, evaluation.watcher
    component = instance.component
    context = component.world.entity_relation_context(component.entity, component=component)
    if context is NotImplemented:
        return NotImplemented
    target_ref = None

    def unsupported(feature):
        component.unsupported(instance, feature, CLASS)

    def check():
        component.ensure_valid()
        bound = component.world.entity_relation_context(component.entity, component=component)
        if (type(context) is not Context or context.owner != component.entity or context.retired
                or instance.stopped or instance.teardown
                or component.instances.get(instance.id) is not instance
                or bound is not context):
            unsupported('component43 relation owner/context retired during callback')
        if any(type(value) is not bool for value in (
                context.context_present, context.namespace_present, context.environment_present)):
            unsupported('component43 relation context availability')
        if target_ref is not None and (target_ref.retired or target_ref.entity != identifier):
            unsupported('component43 relation target retired during callback')

    def call(callback, feature, *args):
        if not callable(callback):
            unsupported(feature)
        result = callback(*args)
        check()
        return result

    with component.operation():
        check()
        # CDBDE0 obtains context+30 before evaluating its +10 selector.
        if not context.context_present or not context.namespace_present:
            return expr.EVALUATION_FAILED
        value = play.evaluate_scalar(instance, cfg.get('m_801B298F'),
                                     initial=expr.Entity(0), watcher=watcher)
        check()
        identifier = expr.to_entity(value) & 0xFFFFFFFF
        target = call(context.lookup, 'component43 relation entity lookup', identifier)
        if target is None:
            if watcher is not None and context.watch_environment is not None:
                call(context.watch_environment, 'component43 relation environment subscription',
                     watcher, 0 if context.environment_present else 46)
            return expr.EVALUATION_FAILED
        if type(target) is not Target or target.entity != identifier:
            unsupported('component43 relation lookup identity/result')
        target_ref = target
        check()
        relation = target.relation  # capture before observation, as D15520 keeps component pointer
        if relation is None:
            return expr.Entity(0)
        if type(relation) is not Relation:
            unsupported('component43 relation availability')
        if watcher is not None:
            call(context.watch_relation, 'component43 relation subscription', watcher, target, relation)
        if type(relation.entity) is not int or not 0 <= relation.entity <= 0xFFFFFFFF:
            unsupported('component43 relation identity')
        return expr.Entity(relation.entity)
