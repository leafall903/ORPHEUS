"""Selected Sigma1D02/1D03 host; pose/filter/pool/clock replication stays reconstructed.

Native D5C2ED..D5C376 selects only operation1 payloads for the amount event.
The actual authored accumulator, conversion/cap and played HealthPool expression
execute here. This is not the general native interception or pool service.
"""

import math

from ow174.game.script import expr, graph

SIGMA = 0x02E000000000023B
SHIELD_TAG = 0x5847
TICK = .016


def window(instance, at_tick):
    state, chase = instance.states.get(1), instance.states.get(20)
    if (state is None or not state.active or state.node.field('m_8B251D53') != 1
            or chase is None or not chase.active or chase.node.pos != 45):
        return None
    if chase.remaining is not None:
        remaining = max(0, chase.remaining) / 1000
    else:
        destination = expr.to_float(chase.evaluate(chase.node.field('m_destination'), watch=False))
        rate = expr.to_float(chase.evaluate(chase.node.field('m_9B0385B4'), watch=False))
        remaining = abs(chase.cur - destination) / rate if rate > 0 else math.inf
    now = at_tick * TICK
    if (now + (instance.component.frame_ms - 1) / 1000 < state.start / 1000
            or now >= chase.last / 1000 + remaining):
        return None
    return state


def amount(direct, blast):
    """Typed nominal payload; falloff/Matrix placement before native caller remains open."""
    selected = [hit.amount for hit in (direct, blast)
                if hit is not None and hit.health_operation == 1 and not hit.healing]
    return expr.f32(sum(selected)) if selected else None


class Grasp:
    def __init__(self, combat):
        self.combat = combat
        self.converted = {}
        self.last_tick = None

    def retire(self, entity):
        self.converted.pop(entity, None)

    def absorbed(self, state, direct, blast, source):
        value = amount(direct, blast)
        if value is None:
            return  # healing interception has no damage-amount row, as in the native selector
        instance = state.instance
        instance.write(expr.lvalue(state.node.field('m_F6CDC81C')), value)
        instance.write(expr.lvalue(state.node.field('m_0A2CABA3')), expr.Entity(source))
        instance.follow(state.node, 'm_160B5DBE', state)
        instance.component.run_now()

    def pool_amount(self, instance, cfg):
        if (instance.graph.index != 0x1D03 or cfg.get('$') != 'STU_783B4CF0'
                or instance.evaluate(cfg.get('m_entity')) != expr.Entity(instance.component.entity)
                or (cfg.get('m_7E8B666C') or {}).get('m_7E8B666C') != 2
                or expr.guid_of((cfg.get('m_7BE02F89') or {}).get('m_identifier')) & 0xffff != SHIELD_TAG):
            return NotImplemented
        vitals = self.combat.vitals.get(instance.component.entity)
        return expr.f32(sum(pool.now for pool in vitals.extra.values()
                            if pool.identifier == SHIELD_TAG)) if vitals is not None else 0.0

    def play(self, instance, node):
        if (instance.graph.index != 0x1D02 or node.pos != 34
                or instance.evaluate(node.field('m_script')) != expr.Asset(0x0580000000001D03)
                or instance.evaluate(node.field('m_targets')) != expr.Entity(instance.component.entity)):
            return False
        target = next((t for t in self.combat._targets() if t.entity == instance.component.entity), None)
        if target is None or self.combat.vitals_of(target).dead:
            return True
        absorption = instance.states.get(1)
        key = (instance.id, absorption.counter if absorption else 0)
        if self.converted.get(target.entity) == key:
            return True
        self.converted[target.entity] = key
        definition = graph.graph(0x1D03)
        if definition is None:
            raise ValueError('selected Sigma shield profile requires1D03')
        params = tuple((expr.guid_of(row['m_B5051BCE']), instance.evaluate(row['m_value']))
                       for row in node.field('m_params') or ())
        instance.component.play_loaded_graph(definition, source=target.entity, instigator=target.entity,
                                             params=params, policy=1)
        return True

    def update(self):
        fight = self.combat
        previous = fight.tick if self.last_tick is None else self.last_tick
        self.last_tick = fight.tick
        present = set()
        for target in fight._targets():
            owner = target.owner
            if getattr(getattr(owner, 'hero', None), 'guid', None) != SIGMA:
                continue
            script = getattr(owner, 'body_script', None)
            if script is None or script.retired:
                continue
            present.add(target.entity)
            vitals = fight.vitals_of(target)
            component = script.component
            active, retire = {}, []
            for instance in list(component.instances.values()):
                if instance.graph.index != 0x1D03:
                    continue
                states = [s for s in instance.states.values() if s.active
                          and s.node.cls == 'STUStatescriptStateHealthPool']
                if vitals.dead or not states:
                    retire.append(instance)
                    continue
                for state in states:
                    if (state.node.field('m_7E8B666C') == 2
                            and expr.guid_of((state.node.field('m_7BE02F89') or {}).get('m_identifier'))
                            & 0xffff == SHIELD_TAG):
                        active[(instance.id, state.index, state.counter)] = state
            changed = False
            for key, pool in list(vitals.extra.items()):
                if pool.identifier == SHIELD_TAG and key not in active and not pool.removed:
                    vitals.remove_temporary(key)
                    changed = True
            for key, state in active.items():
                if key not in vitals.extra:
                    value = expr.to_float(state.evaluate(state.node.field('m_amount'), watch=False))
                    vitals.add_temporary(key, SHIELD_TAG, value)
                    changed = value > 0 or changed
                pool = vitals.extra.get(key)
                if pool is None or pool.removed:
                    retire.append(state.instance)
            root = next((i for i in component.instances.values() if i.graph.index == 0x1D02), None)
            decay = (next((s for s in root.states.values() if s.node.pos == 70 and s.active), None)
                     if root else None)
            if decay is not None:
                seconds = max(0, fight.tick - max(previous, decay.start / script.frame_ms)) * TICK
                rate_cfg = (decay.node.field('m_modifyHealth') or {}).get('m_amount')
                rate = expr.to_float(decay.evaluate(rate_cfg, watch=False))
                left = max(0, rate * seconds)
                for key, pool in vitals.extra.items():
                    if pool.identifier != SHIELD_TAG or pool.removed:
                        continue
                    lost = min(left, pool.now)
                    pool.now -= lost
                    left -= lost
                    changed = lost > 0 or changed
                    if lost > 0:
                        vitals.changed.add(2)  # HealthPool kind2, including partial decay updates
                    if pool.now <= 1e-9:
                        vitals.remove_temporary(key)
                        if key in active:
                            retire.append(active[key].instance)
            for instance in set(retire):
                component.destroy(instance)
            if changed:
                fight._sync_body_health(owner, vitals)
        for entity in self.converted.keys() - present:
            del self.converted[entity]
