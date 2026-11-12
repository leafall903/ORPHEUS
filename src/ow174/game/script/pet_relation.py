"""104319 CE2CF0/D1BEE0: component72+D8 entity with explicit engine services."""

from dataclasses import dataclass

from ow174.game.script import expr, play

CLASS = 'STU_F76C486C'


@dataclass
class Pet:
    entity: int

    def __post_init__(self):
        if type(self.entity) is not int or not 0 <= self.entity <= 0xFFFFFFFF:
            raise ValueError('component72 entity must be a native uint32 identity')


@dataclass
class Target:
    entity: int
    pet: object = NotImplemented  # None declares known component72 absence.
    retired: bool = False


@dataclass
class Context:
    owner: int
    context_present: bool
    namespace_present: bool
    environment_present: bool
    lookup: object = NotImplemented
    watch_environment: object = NotImplemented  # None declares no dependency hub.
    watch_pet: object = NotImplemented
    retired: bool = False

    def __post_init__(self):
        if (type(self.owner) is not int or not 0 <= self.owner <= 0xFFFFFFFF
                or not expr.valid_entity(self.owner)):
            raise ValueError('invalid component72 query owner')
        if any(type(value) is not bool for value in (
                self.context_present, self.namespace_present, self.environment_present)):
            raise ValueError('component72 query context availability must be known')

    def retire(self):
        self.retired = True


def evaluate(evaluation, cfg):
    if cfg.get('$') != CLASS:
        return NotImplemented
    instance, watcher = evaluation.instance, evaluation.watcher
    component = instance.component
    context = component.world.pet_relation_context(component.entity, component=component)
    if context is NotImplemented:
        return NotImplemented
    target_ref = None

    def unsupported(feature):
        component.unsupported(instance, feature, CLASS)

    def check():
        component.ensure_valid()
        bound = component.world.pet_relation_context(component.entity, component=component)
        if (type(context) is not Context or context.owner != component.entity or context.retired
                or instance.stopped or instance.teardown
                or component.instances.get(instance.id) is not instance or bound is not context):
            unsupported('component72 query owner/context retired during callback')
        if any(type(value) is not bool for value in (
                context.context_present, context.namespace_present, context.environment_present)):
            unsupported('component72 query context availability')
        if target_ref is not None and (target_ref.retired or target_ref.entity != identifier):
            unsupported('component72 query target retired during callback')

    def call(callback, feature, *args):
        if not callable(callback):
            unsupported(feature)
        result = callback(*args)
        check()
        return result

    with component.operation():
        check()
        # CE2CF0 obtains context+30 before the selector. Both missing guards
        # fail without touching the output;all D1BEE0 return paths succeed.
        if not context.context_present or not context.namespace_present:
            return expr.EVALUATION_FAILED
        value = play.evaluate_scalar(instance, cfg.get('m_D63CBEC2'),
                                     initial=expr.Entity(0), watcher=watcher)
        check()
        identifier = expr.to_entity(value) & 0xFFFFFFFF
        target = call(context.lookup, 'component72 entity lookup', identifier)
        if target is None:
            if watcher is not None and context.watch_environment is not None:
                call(context.watch_environment, 'component72 environment subscription',
                     watcher, 0 if context.environment_present else 46)
            return expr.Entity(0)
        if type(target) is not Target or target.entity != identifier:
            unsupported('component72 lookup identity/result')
        target_ref = target
        check()
        pet = target.pet  # Native keeps this pointer across observer registration.
        if pet is None:
            return expr.Entity(0)
        if type(pet) is not Pet:
            unsupported('component72 data availability')
        if watcher is not None:
            call(context.watch_pet, 'component72 subscription', watcher, target, pet)
        if type(pet.entity) is not int or not 0 <= pet.entity <= 0xFFFFFFFF:
            unsupported('component72 entity identity')
        return expr.Entity(pet.entity)
