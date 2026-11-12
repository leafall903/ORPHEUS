"""104319 CC9BF0/B96D3A98 with explicit controller-resolution services.

DA6910 resolution and native component allocation are host inputs. This query
does not manufacture an NPC controller or infer component92 absence.
"""

from dataclasses import dataclass

from ow174.game.script import expr, play

CLASS = 'STU_B96D3A98'


def identity(value):
    if type(value) is not int or not 0 <= value <= 0xFFFFFFFF:
        raise ValueError('expected native uint32 controller-query identity')


@dataclass
class Target:
    entity: int
    has_component43: object = NotImplemented
    retired: bool = False

    def __post_init__(self):
        identity(self.entity)


@dataclass
class Controller:
    entity: int
    has_component75: object = NotImplemented
    has_component92: object = NotImplemented
    retired: bool = False

    def __post_init__(self):
        identity(self.entity)


@dataclass
class Context:
    owner: int
    namespace_present: bool
    lookup: object = NotImplemented
    resolve_controller: object = NotImplemented
    watch_component43: object = NotImplemented
    retired: bool = False

    def __post_init__(self):
        identity(self.owner)
        if not expr.valid_entity(self.owner) or type(self.namespace_present) is not bool:
            raise ValueError('expected known controller-query owner and namespace')


def evaluate(evaluation, cfg):
    if cfg.get('$') != CLASS:
        return NotImplemented
    instance, watcher = evaluation.instance, evaluation.watcher
    component = instance.component
    context = component.world.entity_controller_context(component.entity)
    if context is NotImplemented:
        return NotImplemented  # an unmounted service preserves the world's fallback
    # Selection/conversion precedes the namespace lookup even on a missing ID.
    value = play.evaluate_scalar(instance, cfg.get('m_entity'),
                                 initial=expr.Entity(0), watcher=watcher)
    identifier = expr.to_entity(value) & 0xFFFFFFFF
    target = None

    def unsupported(feature):
        component.unsupported(instance, feature, CLASS)

    def check():
        component.ensure_valid()
        bound = component.world.entity_controller_context(component.entity)
        component.ensure_valid()
        if (type(context) is not Context or context.owner != component.entity or context.retired
                or type(context.namespace_present) is not bool or instance.stopped or instance.teardown
                or component.instances.get(instance.id) is not instance
                or bound is not context):
            unsupported('controller query owner/context retired')
        if target is not None and (target.retired or target.entity != identifier):
            unsupported('controller query target retired')

    def call(callback, feature, *args):
        if not callable(callback):
            unsupported(feature)
        result = callback(*args)
        check()
        return result

    with component.operation():
        check()
        if not context.namespace_present:
            unsupported('controller query namespace absent')
        found = call(context.lookup, 'controller query entity lookup', identifier)
        if found is None:
            return False
        if type(found) is not Target or found.entity != identifier:
            unsupported('controller query lookup identity/result')
        target = found
        check()
        if type(target.has_component43) is not bool:
            unsupported('controller query component43 availability')
        if target.has_component43 and watcher is not None:
            call(context.watch_component43, 'controller query component43 subscription', watcher, target)
        # DA6910 performs a separate resolution after the observation pass.
        controller = call(context.resolve_controller, 'controller query controller resolution', target)
        if controller is None:
            return False
        if type(controller) is not Controller or controller.retired:
            unsupported('controller query controller descriptor')
        if type(controller.has_component75) is not bool:
            unsupported('controller query component75 availability')
        if not controller.has_component75:
            return False
        if type(controller.has_component92) is not bool:
            unsupported('controller query component92 availability')
        return not controller.has_component92


def matches(components):
    """None declares no selected entity; an unknown selection must not use None."""
    if components is None:
        return False
    if (type(components) is not frozenset
            or any(type(index) is not int or not 0 <= index < 256 for index in components)):
        raise ValueError("controller query needs an explicit native component set")
    return 75 in components and 92 not in components
