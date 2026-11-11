"""104319 F7262B36/B96D3A98/D3EB8D87 through explicit entity/component records.

D15520 returns component43's related ID. DA6910/DA5FA0 resolve that ID's
component44, or use the input's44 only when43 is absent. CC9F61 requires75
and excludes92 on the selected44's owning entity. CDB700 checks92 on the
source entity. Engine crypto, native
allocation and observation callbacks are outside this service.
"""
from dataclasses import dataclass

from ow174.game.script import expr, play

RELATED = 'STU_F7262B36'
PREDICATE = 'STU_B96D3A98'
SOURCE_HAS_92 = 'STU_D3EB8D87'


@dataclass(frozen=True)
class Record:
    entity: int
    components: frozenset[int]  # Explicit relevant membership, never inferred from owner equality.
    related: int | None = None

    def __post_init__(self):
        if (type(self.entity) is not int or not 0 <= self.entity <= 0xFFFFFFFF
                or not expr.valid_entity(self.entity)):
            raise ValueError('controller record needs a valid uint32 entity')
        if (type(self.components) is not frozenset
                or any(type(bit) is not int or not 0 <= bit < 256 for bit in self.components)):
            raise ValueError('controller record needs declared component membership')
        if 43 in self.components:
            if type(self.related) is not int or not 0 <= self.related <= 0xFFFFFFFF:
                raise ValueError('component43 needs a uint32 related identity, including zero')
        elif self.related is not None:
            raise ValueError('related identity requires component43')


def evaluate(evaluation, cfg):
    cls = cfg.get('$')
    if cls not in (RELATED, PREDICATE, SOURCE_HAS_92):
        return NotImplemented
    instance, watcher = evaluation.instance, evaluation.watcher
    component = instance.component
    field = 'm_801B298F' if cls == RELATED else 'm_entity'
    value = play.evaluate_scalar(instance, cfg.get(field), initial=expr.Entity(0), watcher=watcher)
    identifier = expr.to_entity(value) & 0xFFFFFFFF

    def lookup(entity):
        result = component.world.controller_record(entity)
        component.ensure_valid()
        if (instance.stopped or instance.teardown
                or component.instances.get(instance.id) is not instance):
            component.unsupported(instance, 'controller query owner retired during lookup', cls)
        if (result is not None and result is not NotImplemented
                and (type(result) is not Record or result.entity != entity)):
            component.unsupported(instance, 'controller query record', cls)
        return result

    source = lookup(identifier)
    if source is NotImplemented:
        return NotImplemented
    if cls == RELATED:
        # CDBDE0 calls D15520 even for null input. Missing entity is no-output
        # failure; known entity without43 writes typed invalid Entity.
        if source is None:
            return expr.EVALUATION_FAILED
        return expr.Entity(source.related if 43 in source.components else 0)
    if source is None:
        return False
    if cls == SOURCE_HAS_92:
        return 92 in source.components
    if 43 in source.components:
        selected = lookup(source.related)
        if selected is NotImplemented:
            return NotImplemented
    else:
        selected = source
    # Present43 takes precedence even if dangling: no fallback to source44,
    # and no recursive traversal of another component43.
    return (selected is not None and 44 in selected.components
            and 75 in selected.components and 92 not in selected.components)
