"""104319 F7262B36: explicit component43 relation lookup, distinct from reverse possession.

Native CDBDE0/D15520 select one Entity, read component43 and convert the related
uint32 ID. Namespace allocation and subscriptions are supplied by the host.
"""

from dataclasses import dataclass

from ow174.game.script import expr, play

CLASS = "STU_F7262B36"


def normalize_relations(values):
    if values is None:
        return None
    if type(values) not in (tuple, list):
        raise ValueError("body owner baseline needs explicit pairs")
    rows = []
    for row in values:
        if (type(row) not in (tuple, list) or len(row) != 2
                or any(type(value) is not int or not 0 <= value <= 0xFFFFFFFF for value in row)):
            raise ValueError("body owner baseline needs uint32 pairs")
        rows.append(tuple(row))
    if len({row[0] for row in rows}) != len(rows):
        raise ValueError("body owner baseline has duplicate bodies")
    return tuple(rows)


def player_relations(match):
    players = getattr(match, "players", None)
    if players is None:
        return None
    return normalize_relations(tuple((player.body, player.entity) for player in players
                                     if not player.gone and player.has_body))


@dataclass(frozen=True)
class Target:
    entity: int
    current: int | None  # None: known component43 absence; zero: present with null relation.

    def __post_init__(self):
        if type(self.entity) is not int or not 0 <= self.entity <= 0xFFFFFFFF:
            raise ValueError("body owner target must be uint32")
        if self.current is not None and (type(self.current) is not int
                                         or not 0 <= self.current <= 0xFFFFFFFF):
            raise ValueError("body owner relation must be uint32")


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
            unsupported("body owner query retired or replaced context")
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
    if target is NotImplemented:
        return NotImplemented  # host has not declared this entity/component relation
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
        call(context.watch_owner, "body owner relation subscription", watcher, target)
    return expr.Entity(target.current)
