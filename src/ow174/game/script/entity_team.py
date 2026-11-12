"""104319 CEEDC0/D25400 entity team query and component25 filter decoding."""

from dataclasses import dataclass

from ow174.game.script import expr, play

CLASS = 'STU_65ABFCCC'


def index_from_filters(bits):
    """5D9570: first set team bit wins; unrelated and spectator bits are ignored."""
    if type(bits) is not int or not 0 <= bits <= 0xFFFFFFFFFFFFFFFF:
        raise ValueError('filter bits must be a native uint64 value')
    teams = (bits >> 23) & 31
    return (teams & -teams).bit_length() - 1 if teams else -1


@dataclass
class FilterBits:
    bits: int

    def __post_init__(self):
        index_from_filters(self.bits)


@dataclass(frozen=True)
class Target:
    entity: int
    filters: object = NotImplemented  # None declares component25 absence.

    def __post_init__(self):
        if type(self.entity) is not int or not 0 <= self.entity <= 0xFFFFFFFF:
            raise ValueError('team lookup target must have a native uint32 identity')
        if (self.filters is not NotImplemented and self.filters is not None
                and type(self.filters) is not FilterBits):
            raise ValueError('expected filter component or known absence')


@dataclass
class Context:
    owner: int
    context_present: bool
    namespace_present: bool
    environment_present: bool
    lookup: object = NotImplemented
    watch_filter: object = NotImplemented
    watch_environment: object = NotImplemented  # None declares a missing dependency hub.
    retired: bool = False

    def __post_init__(self):
        if (type(self.owner) is not int or not 0 <= self.owner <= 0xFFFFFFFF
                or not expr.valid_entity(self.owner)):
            raise ValueError('invalid entity-team owner')
        if any(type(value) is not bool for value in (
                self.context_present, self.namespace_present, self.environment_present)):
            raise ValueError('entity-team context availability must be known')

    def retire(self):
        self.retired = True


def evaluate(evaluation, cfg):
    if cfg.get('$') != CLASS:
        return NotImplemented
    instance, watcher = evaluation.instance, evaluation.watcher
    component = instance.component
    context = component.world.entity_team_context(component.entity, component=component)
    if context is NotImplemented:
        return NotImplemented

    def unsupported(feature):
        component.unsupported(instance, feature, CLASS)

    def check():
        component.ensure_valid()
        bound = component.world.entity_team_context(component.entity, component=component)
        if (type(context) is not Context or context.owner != component.entity or context.retired
                or instance.stopped or instance.teardown
                or component.instances.get(instance.id) is not instance or bound is not context):
            unsupported('entity team owner retired during callback')
        if any(type(value) is not bool for value in (
                context.context_present, context.namespace_present, context.environment_present)):
            unsupported('entity team context availability')

    def call(callback, feature, *args):
        if not callable(callback):
            unsupported(feature)
        result = callback(*args)
        check()
        return result

    def lookup(identifier):
        component.consume_work('entity team lookup')
        target = call(context.lookup, 'entity team lookup', identifier)
        if target is None:
            return None
        if type(target) is not Target or target.entity != identifier:
            unsupported('entity team lookup result')
        if target.filters is not None and type(target.filters) is not FilterBits:
            unsupported('entity team filter component')
        return target

    with component.operation():
        check()
        if not context.context_present:
            return expr.EVALUATION_FAILED
        value = play.evaluate_scalar(instance, cfg.get('m_target'), initial=expr.Entity(0), watcher=watcher)
        check()
        identifier = expr.to_entity(value) & 0xFFFFFFFF
        if not expr.valid_entity(identifier):
            return -1
        if not context.namespace_present:
            unsupported('entity team namespace')
        first = lookup(identifier)
        if first is None:
            if watcher is not None and context.watch_environment is not None:
                call(context.watch_environment, 'entity team environment subscription',
                     watcher, 0 if context.environment_present else 46)
        elif first.filters is not None and watcher is not None:
            call(context.watch_filter, 'entity team filter subscription', watcher, first)
        # D25400 performs a fresh lookup through +58 after the dependency pass.
        # A callback can change both object presence and filter bits in between.
        second = lookup(identifier)
        if second is None or second.filters is None:
            return -1
        try:
            return index_from_filters(second.filters.bits)
        except ValueError:
            unsupported('entity team filter bits')
