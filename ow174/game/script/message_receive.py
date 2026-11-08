"""104319 1B91D70 entry selection, 1B955F0 bindings and 1B932F0 scope."""

from dataclasses import dataclass

from ow174.game.script import expr, play
from ow174.game.script.graph import guid as parse_guid

PARAMETER_TYPE = 0x0A40000000000000
ENTRY_CLASS = 'STUStatescriptEntryGameMessage'


@dataclass(frozen=True)
class Packet:
    message: int
    caller: int
    instigator: int
    params: tuple = ()

    def __post_init__(self):
        if type(self.message) is not int or not 0 <= self.message <= 0xFFFFFFFFFFFFFFFF:
            raise ValueError('game message must have a uint64 identity')
        if any(type(value) is not int or not 0 <= value <= 0xFFFFFFFF
               for value in (self.caller, self.instigator)):
            raise ValueError('game message context must have uint32 identities')
        if type(self.params) is not tuple or any(
                type(row) is not tuple or len(row) != 2 or type(row[0]) is not int
                or not 0 <= row[0] <= 0xFFFFFFFFFFFFFFFF for row in self.params):
            raise ValueError('expected ordered full-key game message parameters')


def check(instance, node):
    instance.component.world.validate_game_message_source(instance, node)


def subject(instance, node, packet):
    value = instance.component.world.game_message_subject(instance, node, packet.instigator)
    check(instance, node)
    if type(value) is not int or not 0 <= value <= 0xFFFFFFFF:
        instance.component.unsupported(instance, 'game message subject identity', node.cls, node)
    return expr.Entity(value)


@dataclass(frozen=True)
class ParamScope:
    instance: object
    node: object
    packet: Packet

    def _named(self, identifier):
        for key, value in self.packet.params:
            self.instance.component.consume_work('game message scoped parameter lookup')
            if key == PARAMETER_TYPE | identifier:
                return True, value
        return False, None

    def __contains__(self, identifier):
        if type(identifier) is not int or not 0 <= identifier <= 0xFFFF:
            return False
        return identifier in (9, 10, 11) or self._named(identifier)[0]

    def get(self, identifier, default=None):
        if type(identifier) is not int or not 0 <= identifier <= 0xFFFF:
            return default
        if identifier == 9:
            return expr.Entity(self.packet.caller)
        if identifier == 10:
            return expr.Entity(self.packet.instigator)
        if identifier == 11:
            return subject(self.instance, self.node, self.packet)
        found, value = self._named(identifier)
        return value if found else default


def _target(instance, node, config):
    if config is None:
        return None
    target = expr.lvalue(config)
    if target is None:
        instance.component.unsupported(instance, 'game message binding target', node.cls, node)
    return target


def _context_target(instance, node, config):
    if config is None:
        return None
    target = expr.dynamic(config)
    if target is not None:
        return target
    cls = config.get('$') if isinstance(config, dict) else None
    if cls in expr.EXPRESSIONS or cls in expr.CONSTANTS or cls in expr.RESOURCES:
        return None  # 1B955F0 casts context outputs specifically to Dynamic.
    instance.component.unsupported(instance, 'game message context binding type', node.cls, node)


def queue_entry(instance, entry_index, node, packet):
    """Queue ordered assignments before the Entry; absent inputs do not clear variables."""
    from ow174.game.script.runtime import ASSIGN, ENTRY, Event

    component = instance.component
    with component.operation():
        check(instance, node)
        rows = node.field('m_params') or ()
        for position, (key, value) in enumerate(packet.params):
            for row_position, row in enumerate(rows):
                component.consume_work('game message parameter binding')
                if row is None:
                    continue
                if not isinstance(row, dict):
                    component.unsupported(instance, 'game message binding row', node.cls, node)
                identifier = row.get('m_B5051BCE')
                declared = parse_guid(identifier) if identifier is not None else 0
                if declared is None:
                    component.unsupported(instance, 'game message binding identifier', node.cls, node)
                if (declared != key if key else row_position != position):
                    continue
                target = _target(instance, node, row.get('m_out_Var'))
                if target is None:
                    continue
                if target[1]:
                    instance.post(Event(component.now, 0, ASSIGN, assignment=(*target, value)))
                break
        for field, value in (('m_B60ABDA4', expr.Entity(packet.caller)),
                             ('m_FF703F20', expr.Entity(packet.instigator))):
            target = _context_target(instance, node, node.field(field))
            if target is not None:
                instance.post(Event(component.now, 0, ASSIGN, assignment=(*target, value)))
        target = _context_target(instance, node, node.field('m_44D0D060'))
        if target is not None:
            value = subject(instance, node, packet)
            instance.post(Event(component.now, 0, ASSIGN, assignment=(*target, value)))
        check(instance, node)
        if node.field('m_FDBDCB70'):
            instance.disabled_message_entries.add(entry_index)
        instance.post(Event(component.now, 0, ENTRY, node=node))


def receive(instance, packet):
    component = instance.component
    count = 0
    with component.operation():
        if instance.stopped or instance.teardown:
            return 0
        for index, node in enumerate(instance.graph.entries):
            component.consume_work('game message entry selection')
            if (node is None or node.cls != ENTRY_CLASS or node.client_only
                    or node.field('m_5AEA0A51')
                    or index in instance.disabled_message_entries):
                continue
            check(instance, node)
            scope = ParamScope(instance, node, packet)
            instance.params.append(scope)
            try:
                value, success = play.evaluate_scalar_result(instance, node.field('m_gameMessage'))
                check(instance, node)
                selected = play._asset(value) if success else 0
                if not selected or selected != packet.message:
                    continue
                config = node.field('m_filter')
                if config is not None:
                    answer = component.world.game_message_filter(instance, node, config, packet.caller)
                    check(instance, node)
                    if answer is not None and type(answer) is not bool:
                        component.unsupported(instance, 'game message filter', node.cls, node)
                    if answer is False and packet.caller != 0:
                        continue
                queue_entry(instance, index, node, packet)
                count += 1
            finally:
                instance.params.pop()
    return count


def deliver(component, packet):
    if type(packet) is not Packet:
        raise ValueError('expected a native game message packet')
    with component.operation():
        return sum(receive(instance, packet) for instance in list(component.instances.values()))
