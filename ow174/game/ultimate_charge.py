"""Scoped Sombra and Roadhog host for authored 0B8B charge.

Amounts, intervals, exclusions and caps stay in the original graph. Character
classification, lifecycle notification and active-ultimate flag are explicit
server bridges; full native filters/dynamic tags/damage breakdown remain open.
"""

import hashlib
import json
import os
from pathlib import Path

from ow174.game import bots, genji_combat, mei, moira, pharah, reaper_blossom, reinhardt_offense, sigma_combat
from ow174.game.script import expr, nodes, runtime
from ow174.paths import DATA_DIR

SOMBRA = 0x02E000000000012E
ANA = 0x02E000000000013B
ROADHOG = 0x02E0000000000040
RETAINED_HEROES = (ANA, ROADHOG, genji_combat.HERO, mei.HERO, moira.HERO, pharah.HERO, reaper_blossom.HERO,
                   reinhardt_offense.HERO, sigma_combat.HERO)
GRAPH = 0x0B8B
CONFIG_CHANGED = 0x024000000000002D
DAMAGE_DEALT = 0x0240000000000012
DATA_PATH = Path(os.environ.get("ORPHEUS_ULTIMATE_DATA", DATA_DIR / "ultimate_context_174.json"))


class ChargeAmount(runtime.InstanceContext):
    def __init__(self, instance, identifier, amount):
        super().__init__(instance, None)
        self.identifier = identifier
        self.amount = amount

    def var(self, scope, var):
        if (scope, var) == (0, self.identifier):
            return self.amount
        return super().var(scope, var)


def roadhog_instance(script):
    if getattr(script, "hero", None) != ROADHOG:
        return None
    return next((i for i in script.component.instances.values() if i.graph.index == GRAPH), None)


def roadhog_credit(script, amount, *, healing=False):
    """Feed eligible pool changes into the graph's original combat expression."""
    instance = roadhog_instance(script)
    if instance is None or amount <= 0:
        return
    position, identifier = (32, 499) if healing else (23, 336)
    before = expr.to_float(script.component.entity_var(342).value())
    value = expr.evaluate(instance.graph.node(position).field("m_value"),
                          ChargeAmount(instance, identifier, expr.f32(amount)))
    script.gain_ult_charge(expr.to_float(value) - before)


def roadhog_victim_multiplier(script):
    if getattr(script, "hero", None) == ROADHOG:
        for instance in script.component.instances.values():
            if instance.graph.index != 0x01C7:
                continue
            node = instance.graph.node(111)
            if node is not None and (node.field("m_out_Var") or {}).get("m_identifier") == 15586:
                value = instance.evaluate(node.field("m_value"))
                if type(value) in (int, float):
                    return value
            break
    return 1.0


class UltimateCharge:
    def __init__(self, combat):
        self.combat = combat
        self.data_path = str(DATA_PATH)
        self.data = None
        self.data_digest = None
        self.hosted_records = None
        self.paused = {}  # body -> original natural-charge multiplier base
        self.retained = {}  # player controller -> (hero, charge), pending same-hero respawn

    def hosted_catalog(self):
        from ow174.game import hero_charge

        if self.hosted_records is None:
            self.hosted_records = hero_charge.catalog() or {}
        return self.hosted_records or None

    def enabled(self, script):
        if script.hero != SOMBRA:
            return False
        if self.data is None:
            path = Path(self.data_path)
            if path.is_file():
                raw = path.read_bytes()
                self.data = json.loads(raw)
                self.data_digest = hashlib.sha256(raw).hexdigest()
            else:
                self.data = {}  # capture absence once; do not hide later filesystem changes
        return bool(self.data.get("bodies"))

    def _owner(self, body):
        return next((p for p in self.combat.match.players
                     if p.has_body and p.body == body and not p.gone), None)

    def initialize(self, script):
        authored = self.enabled(script)
        if not authored and script.hero not in RETAINED_HEROES:
            return
        component = script.component
        if authored:
            # 01C7/entry37 supplies 2426 through its authored mode expression.
            runtime.send_message(component, CONFIG_CHANGED, {}, component.entity)
        owner = self._owner(component.entity)
        saved = self.retained.pop(owner.entity, None) if owner is not None else None
        if saved is not None and saved[0] == script.hero:
            cost = expr.to_float(component.entity_var(338).value())
            component.set(342, expr.f32(max(0.0, min(cost, saved[1]))))
        if authored:
            self.sync(script)

    def sync(self, script):
        if not self.enabled(script):
            return
        component = script.component
        #842 is the graph's charge-suppression input; derive this declared bridge
        #from the body's actual ultimate Ability lifetime, rather than a timer.
        running = any(s.flags & nodes.Ability.RUNNING for s in script.ultimates)
        component.set(842, running)
        natural = component.entity_var(12918)
        if script.ult_seconds <= 0:  # retain the existing explicit test/debug pause
            if component.entity not in self.paused or natural.base not in (None, 0):
                self.paused[component.entity] = natural.base
            component.set(12918, 0)
        elif component.entity in self.paused:
            component.set(12918, self.paused.pop(component.entity))
        component.run_now()

    def next_passive_frame(self, script):
        """Return the original charge graph's next deadline on the owner clock."""
        if not self.enabled(script):
            return None
        for instance in script.component.instances.values():
            if instance.graph.index != GRAPH or not instance.queue:
                continue
            return max(script.frame + 1, instance.queue[0].time // script.frame_ms)
        return None

    def prepare_retirement(self, player, preserve):
        self.retained.pop(player.entity, None)
        script = player.body_script
        if (preserve and script is not None
                and (script.hero in RETAINED_HEROES or self.enabled(script))):
            amount = expr.to_float(script.component.entity_var(342).value())
            self.retained[player.entity] = (script.hero, amount)

    def retire_body(self, body):
        self.paused.pop(body, None)

    @staticmethod
    def roadhog_passive(script):
        instance = roadhog_instance(script)
        if instance is None:
            return False
        timer = instance.graph.node(3)
        state = instance.states.get(timer.state)
        if (script.ult_seconds <= 0 or script.component.owner_dead or script.retired
                or any(s.flags & nodes.Ability.RUNNING for s in script.ultimates)
                or (state is not None and state.active)):
            script.passive_charge_ms = 0
            return True
        interval = round(expr.to_float(instance.evaluate(timer.field("m_timeout"))) * 1000)
        if interval <= 0:
            return False
        script.passive_charge_ms += script.frame_ms
        while script.passive_charge_ms >= interval:
            script.passive_charge_ms -= interval
            before = expr.to_float(script.component.entity_var(342).value())
            value = instance.evaluate(instance.graph.node(4).field("m_value"))
            script.gain_ult_charge(expr.to_float(value) - before)
        return True

    def _target(self, entity):
        return next((t for t in self.combat._targets() if t.entity == entity), None)

    def _tags(self, target):
        if target is None:
            return None
        owner = target.owner
        if isinstance(owner, bots.Bot):
            body = owner.body
        elif hasattr(getattr(owner, "hero", None), "body"):
            body = owner.hero.body
        else:
            return None  # persistent devices are not character damage credit
        row = self.data.get("bodies", {}).get(f"0x{body:016X}")
        return row["direct_target_tags"] if row is not None else None

    def native(self, world, cfg, instance):
        if (instance is None or instance.graph.index != GRAPH or world.hero != SOMBRA
                or not self.data or not self.data.get("bodies")):
            return NotImplemented
        cls = cfg.get("$")
        owner = self._owner(instance.component.entity)
        if cls == "STU_F7262B36":
            body = expr.to_entity(instance.evaluate(cfg.get("m_801B298F")))
            if owner is not None and body == owner.body:
                return expr.Entity(owner.entity)  # actual owning player, never an aliased/fake body
        elif cls == "STU_AC829876":
            target = self._target(expr.to_entity(instance.evaluate(cfg.get("m_entity"))))
            target_filter = cfg.get("m_filter") or {}
            nonzero = {k: v for k, v in target_filter.items() if v not in (None, 0) and k != "$"}
            if nonzero == {"m_84065FD1": 1}:
                return self._tags(target) is not None  # explicit character-only host classification
        elif cls == "STU_A58C1E63":
            source = expr.to_entity(instance.evaluate(cfg.get("m_source")))
            target = self._target(expr.to_entity(instance.evaluate(cfg.get("m_target"))))
            if owner is not None and source == owner.body and self._tags(target) is not None:
                return (target.owner is not owner
                        and (self.combat.match.game_map.free_for_all or target.team != owner.team))
        elif cls == "STU_25ECB55F":
            tag = expr.guid_of(cfg.get("m_88B0050A")) & 0xFFFFFFFFFFFF
            target = self._target(expr.to_entity(instance.evaluate(cfg.get("m_entity"))))
            tags = self._tags(target)
            if tag in (0x2ED, 0x3C3, 0x11E) and tags is not None:
                return tag in tags  # static body tags only; dynamic tag production is unaccepted
        return NotImplemented

    def damage(self, shooter, target, dealt, tags):
        script = getattr(shooter, "body_script", None)
        if (isinstance(shooter, bots.Bot) or script is None or script.hero != SOMBRA
                or not self.enabled(script)
                or script.entity != shooter.body or script.retired or self._tags(target) is None):
            return
        self.sync(script)
        runtime.send_message(script.component, DAMAGE_DEALT, {
            0x13: expr.f32(dealt), 0x15: expr.Entity(target.entity),
            0x3D: tuple(expr.Asset(tag) for tag in tags), 0x624: (),
        }, shooter.body)
        script.component.run_now()
