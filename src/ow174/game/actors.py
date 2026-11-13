"""Server-owned transient deployables, separate from manual PvE NPC corpses.

The initial consumer is reconstructed Baptiste deployment. Geometry, replication
and script fidelity still require original-client validation.
"""

from dataclasses import dataclass

from ow174.game.script.runtime import Component


@dataclass
class PillarScript:
    component: Component

    def set_health(self, health: float, maximum: float) -> None:
        from ow174.game.script.driver import OWNER_HEALTH

        self.component.set(OWNER_HEALTH, float(health))
        self.component.maximum_health = maximum


@dataclass
class Deployable:
    entity: int
    definition: int
    kind: str
    team: int
    position: tuple
    health: float
    radius: float
    height: float
    owner: object
    source_body: int
    created_tick: int
    expires_tick: int
    body_script: object = None
    filter_bits: int = 0
    rotation: tuple = (0.0, 0.0, 0.0, 1.0)
    capsule: tuple | None = None  # model-local endpoints for an authenticated bind-pose capsule
    pet_owner: int | None = None  # explicit evaluated Pet source, distinct from combat source_body

    @property
    def body(self):
        return self.definition

    @property
    def name(self):
        return self.kind
