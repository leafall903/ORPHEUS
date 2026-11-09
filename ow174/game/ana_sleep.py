"""104319 normal 0BDE/224E sleep timing for the server control bridge."""

from dataclasses import dataclass

EFFECT = 0x04A000000000026F
DURATION = 5.0  # 0BDE/node51, override 215
DAMAGE_GRACE = 0.5  # 224E/node166
WAKE = 0.5  # 224E/node145


@dataclass
class Sleep:
    start: int
    end: int
    waking: bool = False

    def active_at(self, frame: int) -> bool:
        return self.start <= frame < self.end
