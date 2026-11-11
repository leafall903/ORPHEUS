"""104319 CF4340/D0DA20 ordered game filter with explicit engine inputs."""

from dataclasses import dataclass

from ow174.game.script import expr, filter_condition, filter_masks, filter_tags, play
from ow174.game.script.entity_tags import Tags
from ow174.game.script.entity_team import FilterBits, index_from_filters

CLASS = 'STU_AC829876'
MASK_FIELDS = ('m_84065FD1', 'm_1E14CCA8', 'm_4EA686DB',
               'm_27CF71D5', 'm_66747E7D', 'm_89831602')
TAG_FIELDS = ('m_9C7BD8B8', 'm_B486603E', 'm_8B2A4DF3')
NO_KEY = -(1 << 31)


@dataclass(frozen=True)
class Groups:
    counts: tuple  # signed16 counts returned by1AC6E80
    matches: object = NotImplemented  #1AC6C60(index,key)

    def __post_init__(self):
        if type(self.counts) is not tuple or any(
                type(value) is not int or not -(1 << 15) <= value < (1 << 15) for value in self.counts):
            raise ValueError('expected explicit signed16 optional-group counts')


@dataclass
class Target:
    entity: int
    bits: object = NotImplemented  # FilterBits or known component25 absence None
    has_component97: object = NotImplemented
    tags: object = NotImplemented  # Tags or known component76 absence None
    entity_result: object = NotImplemented  # None:known missing registry; otherwise callback(cfg,watcher)
    groups: object = NotImplemented  # None:known absent component4/list; otherwise Groups
    retired: bool = False

    def __post_init__(self):
        if type(self.entity) is not int or not 0 <= self.entity <= 0xFFFFFFFF:
            raise ValueError('filter entity must be a native uint32 identity')


@dataclass
class Context:
    owner: int
    environment_present: bool
    lookup: object = NotImplemented
    source: object = NotImplemented  # explicit native owner entity pointer:Target or None
    relation_enemy: object = NotImplemented  # callback(spectator,target,source)
    watch_environment: object = NotImplemented  # None declares no dependency hub
    watch_filter: object = NotImplemented
    watch_tags: object = NotImplemented
    retired: bool = False

    def __post_init__(self):
        if (type(self.owner) is not int or not 0 <= self.owner <= 0xFFFFFFFF
                or not expr.valid_entity(self.owner) or type(self.environment_present) is not bool):
            raise ValueError('expected known filter owner/environment availability')

    def retire(self):
        self.retired = True


def evaluate(evaluation, cfg):
    if cfg.get('$') != CLASS:
        return NotImplemented
    instance, watcher = evaluation.instance, evaluation.watcher
    component = instance.component
    # CF4340 evaluates the candidate before dispatching the VM filter method.
    candidate = play.evaluate_scalar(instance, cfg.get('m_entity'),
                                     initial=expr.Entity(0), watcher=watcher)
    identifier = expr.to_entity(candidate) & 0xFFFFFFFF
    definition = cfg.get('m_filter')
    if definition is None:
        return True  # D0DA20 null filter pointer; still after candidate evaluation
    context = component.world.entity_filter_context(component.entity, component=component)
    if context is NotImplemented:
        return NotImplemented
    target_ref = None
    source_ref = None

    def unsupported(feature):
        component.unsupported(instance, feature, CLASS)

    def check():
        component.ensure_valid()
        bound = component.world.entity_filter_context(component.entity, component=component)
        if (type(context) is not Context or context.owner != component.entity or context.retired
                or type(context.environment_present) is not bool or instance.stopped or instance.teardown
                or component.instances.get(instance.id) is not instance or bound is not context):
            unsupported('filter owner/context retired during callback')
        if target_ref is not None and (target_ref.retired or target_ref.entity != identifier):
            unsupported('filter target retired during callback')
        if source_ref is not None and source_ref.retired:
            unsupported('filter native owner retired during callback')

    def call(callback, feature, *args):
        if not callable(callback):
            unsupported(feature)
        result = callback(*args)
        check()
        return result

    def bits_of(actor):
        if (type(actor) is not Target or type(actor.entity) is not int
                or not 0 <= actor.entity <= 0xFFFFFFFF or actor.retired):
            unsupported('filter actor descriptor')
        bits = actor.bits
        if bits is not None and type(bits) is not FilterBits:
            unsupported('filter component25 availability')
        if bits is not None:
            try:
                index_from_filters(bits.bits)  # validate the full uint64, not just team bits
            except ValueError:
                unsupported('filter component25 bits')
        return bits

    def tags_of(actor):
        tags = actor.tags
        if tags is not None and type(tags) is not Tags:
            unsupported('filter component76 availability')
        return tags

    with component.operation():
        check()
        if not isinstance(definition, dict) or definition.get('$') != 'STUConfigVarFilter':
            unsupported('filter definition')
        target = call(context.lookup, 'filter entity lookup', identifier)
        if target is None:
            if watcher is not None and context.watch_environment is not None:
                call(context.watch_environment, 'filter environment subscription',
                     watcher, 0 if context.environment_present else 46)
            return False
        if type(target) is not Target or target.entity != identifier:
            unsupported('filter lookup identity/result')
        target_ref = target
        check()
        try:
            masks = filter_masks.Masks(*(0 if definition.get(field) is None else definition[field]
                                        for field in MASK_FIELDS))
        except ValueError as error:
            unsupported(str(error))
        # The component97 gate precedes both filter-component observation passes.
        if masks.any_bits != filter_masks.ALL:
            if type(target.has_component97) is not bool:
                unsupported('filter component97 presence')
            if not target.has_component97 and masks.combined:
                return False
        target_bits = bits_of(target)
        if watcher is not None and target_bits is not None:
            call(context.watch_filter, 'filter target subscription', watcher, target, target_bits)
        source = context.source
        if source is not None and type(source) is not Target:
            unsupported('filter native owner entity')
        if source is not None:
            source_ref = source
            source_bits = bits_of(source)
            if watcher is not None and source_bits is not None:
                call(context.watch_filter, 'filter owner subscription', watcher, source, source_bits)

        def enemy(spectator):
            result = call(context.relation_enemy, 'filter relationship service', spectator, target, source)
            if type(result) is not bool:
                unsupported('filter relationship result')
            return result

        if masks.combined:
            try:
                admitted = filter_masks.matches(target_bits.bits if target_bits is not None else None,
                                                masks, owner_present=source is not None,
                                                same_entity=source is target, relation_enemy=enemy)
            except ValueError as error:
                unsupported(str(error))
            check()
            if not admitted:
                return False
        if not filter_condition.evaluate(instance, definition, identifier,
                                         context_present=True, watcher=watcher):
            check()
            return False
        check()
        has_tag_configs = any(definition.get(field) is not None for field in TAG_FIELDS)
        if has_tag_configs:
            first_tags = tags_of(target)
            if watcher is not None and first_tags is not None:
                call(context.watch_tags, 'filter tag subscription', watcher, target)
            clauses = filter_tags.collect(instance, definition, watcher=watcher, check=check)
            check()
            if not clauses.matches(tags_of(target)):
                return False
        if target.entity_result is None:
            result = 2  # DA8BD0 known absent entity registry
        else:
            result = call(target.entity_result, 'filter entity registry', definition, watcher)
            if type(result) is not int or not -(1 << 31) <= result < (1 << 31):
                unsupported('filter entity registry result')
        if result == 1:
            return False
        extra = play.evaluate_scalar(instance, definition.get('m_F61726FE'),
                                     initial=False, watcher=watcher)
        check()
        if not expr.truthy(extra):
            return True
        groups = target.groups
        if groups is None:
            return True  # no optional component4/list
        if type(groups) is not Groups:
            unsupported('filter optional component4/list')
        value, success = play.evaluate_scalar_result(instance, definition.get('m_B40362FC'),
                                                     initial=NO_KEY, watcher=watcher)
        check()
        key = expr.i32(expr.to_int(value, NO_KEY)) if success else NO_KEY
        if key == NO_KEY:
            return False
        for index, count in enumerate(groups.counts):
            component.consume_work('filter optional group scan')
            if count > 0:
                matched = call(groups.matches, 'filter optional group match', index, key)
                if type(matched) is not bool:
                    unsupported('filter optional group match result')
                if matched:
                    return True
        return False
