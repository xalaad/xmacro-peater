"""MainWindow mixins - cohesive slices extracted verbatim from
ui.main_window. MainWindow composes them; each mixin is a plain
class whose methods use attributes created in
MainWindow.__init__."""
from .chrome import ChromeMixin
from .deck import DeckMixin
from .docking import DockTab, DockingMixin
from .live_feed import LiveFeedMixin
from .playback_ctl import PlaybackMixin
from .schemes import SchemesMixin

__all__ = [
    "ChromeMixin",
    "DeckMixin",
    "DockTab",
    "DockingMixin",
    "LiveFeedMixin",
    "PlaybackMixin",
    "SchemesMixin",
]
