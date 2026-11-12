"""104319 CE5020/C1CF60 hostility query with explicit engine relation inputs."""

from dataclasses import dataclass

from ow174.game.script import expr, play

CLASS = 'STU_A58C1E63'


def _identity(value):
    if type(value) is not int or not 0 <= value <= 0xFFFFFFFF:
        raise ValueError('expected native uint32 identity')


@dataclass(frozen=True)
class Relation:
    identity: int  # D6B260 result, including dynamic identity overrides
    fallback: int
    base: int
    fallback_guard: int

    def __post_init__(self):
        for value in (self.identity, self.fallback, self.base, self.fallback_guard):
            _identity(value)


@dataclass(frozen=True)
class Target:
    entity: int
    team: int  # C1B570 result, not a guessed entity/header field
    spectator: bool  # C1F000: component25 bit28
    relation: object = NotImplemented  # None explicitly declares component50 absence

    def __post_init__(self):
        _identity(self.entity)
        if type(self.team) is not int or not -(1 << 31) <= self.team < (1 << 31):
            raise ValueError('expected signed native team result')
        if type(self.spectator) is not bool:
            raise ValueError('spectator status must be known')
        if (self.relation is not NotImplemented and self.relation is not None
                and type(self.relation) is not Relation):
            raise ValueError('expected component50 relation or known absence')


@dataclass
class Context:
    owner: int
    present: bool
    lookup: object = NotImplemented
    fallback_owner: object = NotImplemented  # DA7ED0, after the fallback entity lookup
    same_group: object = NotImplemented  # C6C0A0, only the neutral/default branch
    watch_environment: object = NotImplemented  # None declares no dependency hub
    retired: bool = False

    def __post_init__(self):
        _identity(self.owner)
        if not expr.valid_entity(self.owner) or type(self.present) is not bool:
            raise ValueError('expected known hostility owner/context availability')

    def retire(self):
        self.retired = True


def compare(target, source, option, *, lookup, fallback_owner, same_group):
    """C1CF60 decision after explicit C1F000/C1B570 service results.

    Native component lookup/identity, team and group services are separate inputs.
    Unknown component50 presence cannot silently mean absence.
    """
    target_team = target.team if target is not None else -1
    source_team = source.team if source is not None else -1
    if option:
        return target_team == 1
    if target is None or source is None:
        return target is not source

    def resolve(actor):
        relation = actor.relation
        if relation is NotImplemented:
            raise ValueError('component50 availability unknown')
        if relation is None:
            return actor.entity, False, actor
        found = lookup(relation.identity)
        if found is not None:
            return relation.identity, False, found
        if (expr.valid_entity(relation.fallback)
                and (not expr.valid_entity(relation.fallback_guard)
                     or relation.fallback_guard == relation.base)):
            found = fallback_owner(lookup(relation.fallback))
            if found is not None:
                return relation.identity, False, found
        return relation.identity, True, actor

    target_id, target_unresolved, target_group = resolve(target)
    source_id, source_unresolved, source_group = resolve(source)
    if target_id == source_id:
        return False
    if (not target_unresolved and not source_unresolved
            and source_team == target_team and source_team in (-1, 4)
            and same_group(source_group, target_group)):
        return False
    if target_team not in (-1, 4) and source_team not in (-1, 4) and source_team == target_team:
        return False
    return not target.spectator and not source.spectator


def evaluate(evaluation, cfg):
    if cfg.get('$') != CLASS:
        return NotImplemented
    instance, watcher = evaluation.instance, evaluation.watcher
    component = instance.component
    context = component.world.entity_hostility_context(component.entity, component=component)
    if context is NotImplemented:
        return NotImplemented

    def unsupported(feature):
        component.unsupported(instance, feature, CLASS)

    def check():
        component.ensure_valid()
        bound = component.world.entity_hostility_context(component.entity, component=component)
        if (type(context) is not Context or context.owner != component.entity or context.retired
                or type(context.present) is not bool or instance.stopped or instance.teardown
                or component.instances.get(instance.id) is not instance
                or bound is not context):
            unsupported('hostility owner/context retired during callback')

    def call(callback, feature, *args):
        if not callable(callback):
            unsupported(feature)
        result = callback(*args)
        check()
        return result

    def target_result(value, feature):
        if value is not None and type(value) is not Target:
            unsupported(feature)
        return value

    def lookup(identifier):
        component.consume_work('hostility entity lookup')
        result = target_result(call(context.lookup, 'hostility lookup', identifier),
                               'hostility lookup result')
        if result is not None and result.entity != identifier:
            unsupported('hostility lookup identity')
        return result

    def fallback(actor):
        return target_result(call(context.fallback_owner, 'hostility fallback owner', actor),
                             'hostility fallback owner result')

    def same_group(source, target):
        result = call(context.same_group, 'hostility neutral group', source, target)
        if type(result) is not bool:
            unsupported('hostility neutral group result')
        return result

    with component.operation():
        check()
        if not context.present:
            return expr.EVALUATION_FAILED
        if watcher is not None and context.watch_environment is not None:
            call(context.watch_environment, 'hostility environment subscription', watcher, 2)
        target = play.evaluate_scalar(instance, cfg.get('m_target'), initial=expr.Entity(0), watcher=watcher)
        check()
        source = play.evaluate_scalar(instance, cfg.get('m_source'), initial=expr.Entity(0), watcher=watcher)
        check()
        option = expr.truthy(play.evaluate_scalar(instance, cfg.get('m_AFBBE9C3'),
                                                  initial=False, watcher=watcher))
        check()
        # CE5020 looks up source first, then target, after evaluating all configs.
        source_actor = lookup(expr.to_entity(source) & 0xFFFFFFFF)
        target_actor = lookup(expr.to_entity(target) & 0xFFFFFFFF)
        try:
            return compare(target_actor, source_actor, option, lookup=lookup,
                           fallback_owner=fallback, same_group=same_group)
        except ValueError as error:
            unsupported(str(error))
