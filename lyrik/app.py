from __future__ import annotations

import sys

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Gdk", "4.0")
gi.require_version("GdkPixbuf", "2.0")
try:
    gi.require_version("Adw", "1")
    from gi.repository import Adw
except ValueError:
    Adw = None
try:
    gi.require_version("Gtk4LayerShell", "1.0")
except ValueError:
    pass

from gi.repository import Gio, Gtk

from lyrik.config import load_settings
from lyrik.mpris import MprisClient
from lyrik.overlay import OverlayWindow


class LyrikApplication(Gtk.Application):
    def __init__(self) -> None:
        super().__init__(application_id="org.lyrik.Overlay", flags=Gio.ApplicationFlags.FLAGS_NONE)
        self.set_resource_base_path(None)
        if Adw is not None:
            Adw.init()
            Adw.StyleManager.get_default().set_color_scheme(Adw.ColorScheme.FORCE_DARK)

    def do_activate(self) -> None:
        window = self.props.active_window
        if window is None:
            settings = load_settings()
            player = MprisClient(prefer_spotify=settings.prefer_spotify)
            window = OverlayWindow(self, settings, player)
        window.present()


def main(argv: list[str] | None = None) -> int:
    app = LyrikApplication()
    return app.run(argv if argv is not None else sys.argv)
