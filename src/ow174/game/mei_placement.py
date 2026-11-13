"""Original4C96470A byte lifecycle for Mei0622/node3; geometry host is unchanged."""

from ow174.game.script.nodes import PlacementPreview


class MeiPlacementPreview(PlacementPreview):
    """104319 constructor/begin0, typed A1AAB8C4 confirmation1, rebegin0.

    The instance factory restricts this adapter to permissive Mei0622/3.
    Reuse PlacementPreview's existing entry, confirmation and geometry services.
    The strict codec still requires this explicit uint8 from its producer.
    """

    def __init__(self, *args):
        super().__init__(*args)
        self.confirmation_byte = 0

    def on_begin(self):
        self.confirmation_byte = 0
        return super().on_begin()

    def plug_enter(self, input_path):
        if input_path == 'm_6647ED22' and self.active and not self.ending:
            self.confirmation_byte = 1
        return super().plug_enter(input_path)

    def payload(self):
        return {'m_B4802CE3': self.confirmation_byte}
