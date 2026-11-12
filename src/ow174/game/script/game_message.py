"""104319 905F80 outgoing action construction and 1BA7C60 array parameters."""

from dataclasses import dataclass

from ow174.game.script import expr, play
from ow174.game.script.graph import guid as parse_guid


@dataclass(frozen=True)
class Request:
    message: int
    owner: int
    instigator: int
    targets: tuple[int, ...]  # External targets, preserving duplicates and order.
    self_target: bool
    params: tuple  # Ordered (full parameter GUID, collected value tuple) pairs.


def prepare(instance, node, *, routed=False):
    component = instance.component

    def unsupported(feature):
        component.unsupported(instance, feature, node.cls, node)

    def identity(value):
        if type(value) is not int or not 0 <= value <= 0xFFFFFFFF:
            unsupported('game message context identity')
        component.ensure_valid()
        return value

    def check():
        component.world.validate_game_message_source(instance, node)

    with component.operation():
        cfg = node.field('m_gameMessage')
        if cfg is None:
            return None
        value, success = play.evaluate_scalar_result(instance, cfg)
        message = play._asset(value) if success else 0
        if not message:
            return None
        owner = identity(component.world.game_message_owner(instance, node))
        targets, self_target = [], False
        if routed:
            candidates = component.world.routed_game_message_targets(instance, node, message)
            if type(candidates) is not tuple:
                unsupported('routed game message targets')
        else:
            candidates = play._iter_values(instance, node.field('m_targets'))
        for value in candidates:
            if routed:
                component.consume_work('routed game message target collection')
            check()
            target = expr.to_entity(value) & 0xFFFFFFFF
            if not expr.valid_entity(target):
                continue
            if target == owner:
                self_target = True
            else:
                targets.append(target)
        check()
        if not targets and not self_target:
            return None
        candidate = identity(component.world.game_message_source(instance, node))
        cfg = node.field('m_8B34E9C3')
        if cfg is not None:
            value, success = play.evaluate_scalar_result(instance, cfg)
            check()
            if success:
                candidate = expr.to_entity(value) & 0xFFFFFFFF
        instigator = candidate if expr.valid_entity(candidate) else owner
        params = []
        for row in node.field('m_params') or ():
            component.consume_work('game message parameter collection')
            if row is None:
                continue
            if not isinstance(row, dict):
                unsupported('game message parameter row')
            identifier = row.get('m_B5051BCE')
            if identifier is None:
                continue
            key = parse_guid(identifier)
            if not key:
                unsupported('game message parameter identifier')
            values = []
            for value in play._iter_values(instance, row.get('m_value')):
                check()
                values.append(value)
            check()
            params.append((key, tuple(values)))
        check()
        return Request(message, owner, instigator, tuple(targets), self_target, tuple(params))


def execute(instance, node, *, routed=False):
    with instance.component.operation():
        request = prepare(instance, node, routed=routed)
        if request is not None:
            instance.component.world.submit_game_message(instance, node, request)
            instance.component.world.validate_game_message_source(instance, node)
