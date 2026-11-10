"""104319 controller hero iterator (1AAB4B4F), with declared engine services."""

from contextlib import closing
from dataclasses import dataclass

from ow174.game.script import expr

CLASS = 'STU_1AAB4B4F'


@dataclass(frozen=True)
class Controller:
    entity: int

    def __post_init__(self):
        if (type(self.entity) is not int or not 0 <= self.entity <= 0xFFFFFFFF
                or not expr.valid_entity(self.entity)):
            raise ValueError('invalid hero-list controller identity')


@dataclass
class Context:
    owner: int
    namespace_present: bool
    environment_present: bool
    lookup: object = NotImplemented
    catalog: object = NotImplemented
    available: object = NotImplemented
    watch_environment: object = NotImplemented  # None declares a missing dependency hub.
    retired: bool = False

    def __post_init__(self):
        if (type(self.owner) is not int or not 0 <= self.owner <= 0xFFFFFFFF
                or not expr.valid_entity(self.owner)):
            raise ValueError('invalid controller-hero owner')
        if type(self.namespace_present) is not bool or type(self.environment_present) is not bool:
            raise ValueError('controller-hero context availability must be known')

    def retire(self):
        self.retired = True


def values(evaluation, cfg):
    """An owned iterator, or NotImplemented when no service has been installed."""
    if not isinstance(cfg, dict) or cfg.get('$') != CLASS:
        return NotImplemented
    instance, watcher = evaluation.instance, evaluation.watcher
    component = instance.component
    context = component.world.controller_hero_context(component.entity, component=component)
    if context is NotImplemented:
        return NotImplemented

    def unsupported(feature):
        component.unsupported(instance, feature, CLASS)

    def check():
        component.ensure_valid()
        if (type(context) is not Context or context.owner != component.entity or context.retired
                or instance.stopped or instance.teardown
                or component.instances.get(instance.id) is not instance
                or component.world.controller_hero_context(
                    component.entity, component=component) is not context):
            unsupported('controller hero owner retired during callback')
        if type(context.namespace_present) is not bool or type(context.environment_present) is not bool:
            unsupported('controller hero context availability')

    def call(callback, feature, *args):
        if not callable(callback):
            unsupported(feature)
        result = callback(*args)
        check()
        return result

    def watch(kind):
        if watcher is not None and context.watch_environment is not None:
            call(context.watch_environment, 'controller hero environment subscription', watcher, kind)

    def iterate():
        from ow174.game.script import play

        with component.operation():
            check()
            evaluation.poll_native(cfg)
            if not context.namespace_present:
                unsupported('controller hero namespace')
            if not context.environment_present:
                watch(46)
                # The native start cannot establish iterator storage on this path.
                # Its following next-call is not evidence of an empty collection.
                unsupported('controller hero active environment')
            watch(3)
            watch(37)
            value = play.evaluate_scalar(instance, cfg.get('m_controller'),
                                         initial=expr.Entity(0), watcher=watcher)
            check()
            identifier = expr.to_entity(value) & 0xFFFFFFFF
            controller = None
            if expr.valid_entity(identifier):
                controller = call(context.lookup, 'controller hero lookup', identifier)
                if (controller is not None
                        and (type(controller) is not Controller or controller.entity != identifier)):
                    unsupported('controller hero lookup result')
            # Native constructs the catalog even for an invalid/missing controller.
            catalog = call(context.catalog, 'controller hero catalog')
            if type(catalog) is not tuple:
                unsupported('controller hero catalog result')
            if controller is None:
                return
            for guid in catalog:
                component.consume_work('controller hero catalog iteration')
                check()
                if type(guid) is not int or not 0 <= guid <= 0xFFFFFFFFFFFFFFFF:
                    unsupported('controller hero catalog identity')
                if not guid:
                    continue
                accepted = call(context.available, 'controller hero availability', guid, controller)
                if type(accepted) is not bool:
                    unsupported('controller hero availability result')
                if accepted:
                    yield expr.Asset(guid)

    return iterate()


def scalar_result(evaluation, cfg):
    """Return (destination, AL), preserving a written int0 on empty failure."""
    iterator = values(evaluation, cfg)
    if iterator is NotImplemented:
        return NotImplemented
    with closing(iterator):
        missing = object()
        value = next(iterator, missing)
        return (0, False) if value is missing else (value, True)


def scalar(evaluation, cfg):
    result = scalar_result(evaluation, cfg)
    return result if result is NotImplemented else result[0]


def collect(evaluation, cfg):
    iterator = values(evaluation, cfg)
    if iterator is NotImplemented:
        return NotImplemented
    with closing(iterator):
        return tuple(iterator)
