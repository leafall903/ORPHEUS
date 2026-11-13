"""Explicit staged0D07/58 admission; no normal PlayScript or health hook.

One bridge belongs to one live recipient body and captured source body. The
caller owns its lifetime. Graph47/48 own finite modifiers; this backend queues
only the already formed authored enum4 request in the canonical force host.
Original C329C0 maps it to queue0/SET2, including a real zero reset. NPCs and
full-helper completion remain unqualified.
"""

import math

from ow174.game import doomfist, doomfist_helper
from ow174.game.script import expr


class StagedVelocityBridge:
    def __init__(self, world, source, recipient):
        self.world = world
        self.source = source
        self.source_body = source.body
        self.source_script = source.body_script
        self.recipient = recipient
        self.recipient_script = recipient.body_script
        self.component = recipient.body_script.component
        self.results = {}
        self.closed = False

    def close(self):
        self.closed = True
        self.results.clear()

    def __call__(self, instance, node):
        if (instance.graph.index, node.pos) != (0x0D07, 58):
            return NotImplemented
        world, source, component = self.world, self.source, self.component
        match = getattr(world, 'match', None)
        if (self.closed or match is None or instance.component is not component
                or instance.graph.node(58) is not node
                or doomfist_helper._recipient(world, instance) is not self.recipient
                or self.recipient not in match.players
                or self.recipient.body_script is not self.recipient_script
                or source not in match.players or source.hero.guid != doomfist.HERO
                or not source.has_body or source.gone or source.body != self.source_body
                or source.body_script is not self.source_script or self.source_script.retired
                or self.source_script.component.world.match is not match
                or instance.source_entity() != self.source_body
                or instance.evaluate(instance.graph.node(14).field('m_modifyHealth')['m_instigator'])
                != expr.Entity(self.source_body)):
            return False
        rows = node.field('m_BC5E91CF')
        if (not isinstance(rows, list) or len(rows) != 1
                or rows[0].get('$') != 'STU_95A10547' or rows[0].get('m_CFEC5F76') != 4):
            return False
        response_config = instance.graph.node(66).field('m_condition')['m_configVars'][0]
        response = doomfist_helper.response(world, response_config, instance)
        if type(response) is not float or not math.isfinite(response) or response <= 0:
            return False
        row = rows[0]
        vector = instance.evaluate(row.get('m_9B7A63EA'))
        priority = instance.evaluate(row.get('m_priority'))
        if (not isinstance(vector, expr.Vec3) or not all(map(math.isfinite, vector))
                or type(priority) not in (int, float)
                or not math.isfinite(priority) or priority != 1):
            return False
        target = next((t for t in match.combat._enemies(source)
                       if t.owner is self.recipient and t.entity == component.entity), None)
        if (target is None
                or match._script_body_mover(component.entity, component.entity) is not self.recipient.mover):
            return False
        key = instance
        if key in self.results:
            return self.results[key]
        # Reserve before canonical admission: an exception after enqueue cannot
        # replay a second force. No finite launch_modifiers are passed: original
        # action58 has one vector row; its out plug enters separate47/48 states.
        self.results[key] = False
        accepted = match.combat.knockbacks.apply_script_world_set(
            target, vector, recipient_component=component, source_entity=self.source_body,
            priority=expr.f32(priority))
        self.results[key] = bool(accepted)
        return self.results[key]
