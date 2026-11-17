from __future__ import annotations

import importlib.resources
import subprocess
from threading import Thread

import cairo
from gi.repository import Gdk, GdkPixbuf, GLib, Gtk, Pango

from lyrik.config import Settings, save_settings
from lyrik.lyrics import Lyrics, fetch_lyrics
from lyrik.mpris import MprisClient, load_bytes

try:
    from gi.repository import Gtk4LayerShell as LayerShell

    LAYER_SHELL = LayerShell.is_supported()
except Exception:
    LayerShell = None
    LAYER_SHELL = False


def _css_data() -> bytes:
    return importlib.resources.files("lyrik.resources").joinpath("style.css").read_bytes()


def format_time(seconds: float) -> str:
    seconds = max(0, int(seconds))
    return f"{seconds // 60}:{seconds % 60:02d}"


class OverlayWindow(Gtk.Window):
    def __init__(self, application: Gtk.Application, settings: Settings, player: MprisClient) -> None:
        super().__init__(application=application, title="Lyrik")
        self.settings = settings
        self.player = player
        self._lyrics = Lyrics()
        self._lyrics_key = ""
        self._fetch_gen = 0
        self._current_line = -1
        self._line_widgets: list[Gtk.Label] = []
        self._art_url = ""

        self.add_css_class("lyrik-window")
        self.set_decorated(False)
        self.set_resizable(True)
        self.set_default_size(settings.width, 72 if settings.compact else settings.height)
        self.set_hide_on_close(False)

        self._install_css()
        self._maybe_layer_shell()
        self._build()
        self._bind_keys()
        self.set_opacity(settings.opacity)

        self.player.connect("changed", self._on_player_changed)
        self.connect("close-request", self._on_close)
        self.connect("realize", self._on_realize)
        GLib.timeout_add_seconds(4, self._persist_geometry)
        self._on_player_changed(self.player)

    def _install_css(self) -> None:
        provider = Gtk.CssProvider()
        provider.load_from_data(_css_data())
        Gtk.StyleContext.add_provider_for_display(
            Gdk.Display.get_default(),
            provider,
            Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION,
        )

    def _maybe_layer_shell(self) -> None:
        if not LAYER_SHELL or LayerShell is None:
            return
        LayerShell.init_for_window(self)
        LayerShell.set_layer(self, LayerShell.Layer.OVERLAY)
        LayerShell.set_namespace(self, "lyrik")
        LayerShell.set_anchor(self, LayerShell.Edge.TOP, True)
        LayerShell.set_anchor(self, LayerShell.Edge.RIGHT, True)
        LayerShell.set_margin(self, LayerShell.Edge.TOP, 20)
        LayerShell.set_margin(self, LayerShell.Edge.RIGHT, 20)
        LayerShell.set_exclusive_zone(self, 0)
        LayerShell.set_keyboard_mode(self, LayerShell.KeyboardMode.ON_DEMAND)

    def _build(self) -> None:
        self.root = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=0)
        self.root.add_css_class("overlay-root")
        if self.settings.compact:
            self.root.add_css_class("compact")

        outer = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=0)
        outer.set_hexpand(True)

        handle = Gtk.WindowHandle()
        header = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        header.add_css_class("overlay-header")
        self.header_bar = header
        brand = Gtk.Label(label="LYRIK", xalign=0)
        brand.add_css_class("brand")
        self.player_pill = Gtk.Label(label="SPOTIFY")
        self.player_pill.add_css_class("status-pill")
        header.append(brand)
        header.append(self.player_pill)
        spacer = Gtk.Box()
        spacer.set_hexpand(True)
        header.append(spacer)
        header.append(self._icon_button("view-restore-symbolic", "Compact / expand", self.toggle_compact))
        self.lock_button = self._icon_button(
            "system-lock-screen-symbolic",
            "Click-through so you can keep working",
            self._toggle_click_through,
        )
        header.append(self.lock_button)
        header.append(self._menu_button())
        handle.set_child(header)
        outer.append(handle)

        now = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=12)
        now.set_margin_start(14)
        now.set_margin_end(14)
        now.set_margin_top(6)
        self.art = Gtk.Picture()
        self.art.add_css_class("art")
        self.art.set_size_request(64, 64)
        self.art.set_content_fit(Gtk.ContentFit.COVER)
        meta = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=2)
        meta.set_valign(Gtk.Align.CENTER)
        meta.set_hexpand(True)
        self.title_label = Gtk.Label(xalign=0, ellipsize=Pango.EllipsizeMode.END)
        self.title_label.add_css_class("title")
        self.title_label.set_margin_start(6)
        self.subtitle_label = Gtk.Label(xalign=0, ellipsize=Pango.EllipsizeMode.END)
        self.subtitle_label.add_css_class("subtitle")
        meta.append(self.title_label)
        meta.append(self.subtitle_label)
        now.append(self.art)
        now.append(meta)
        self.compact_controls = self._transport(compact=True)
        now.append(self.compact_controls)
        outer.append(now)

        self.progress = Gtk.Scale.new_with_range(Gtk.Orientation.HORIZONTAL, 0, 1, 0.001)
        self.progress.set_draw_value(False)
        self.progress.add_css_class("progress")
        self.progress.connect("change-value", self._on_seek)
        outer.append(self.progress)

        times = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL)
        times.add_css_class("times")
        self.elapsed = Gtk.Label(label="0:00", xalign=0)
        self.remaining = Gtk.Label(label="0:00", xalign=1, hexpand=True)
        times.append(self.elapsed)
        times.append(self.remaining)
        outer.append(times)

        self.full_controls = self._transport(compact=False)
        outer.append(self.full_controls)

        self.scroller = Gtk.ScrolledWindow()
        self.scroller.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
        self.scroller.set_vexpand(True)
        self.scroller.set_min_content_height(220)
        self.lyrics_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=0)
        self.lyrics_box.add_css_class("lyrics")
        self.placeholder = Gtk.Label(wrap=True, justify=Gtk.Justification.CENTER)
        self.placeholder.add_css_class("placeholder")
        self.lyrics_box.append(self.placeholder)
        self.scroller.set_child(self.lyrics_box)
        outer.append(self.scroller)

        self.root.append(outer)
        self.set_child(self.root)
        header.connect("notify::width", lambda *_: self._apply_click_through())
        header.connect("notify::height", lambda *_: self._apply_click_through())
        self._apply_compact()
        self._update_lock_button()

    def _transport(self, compact: bool) -> Gtk.Box:
        box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        box.add_css_class("transport")
        box.set_halign(Gtk.Align.CENTER if not compact else Gtk.Align.END)
        box.set_valign(Gtk.Align.CENTER)
        prev_btn = self._icon_button("media-skip-backward-symbolic", "Previous", lambda *_: self.player.previous())
        play_btn = self._icon_button(
            "media-playback-start-symbolic",
            "Play / pause",
            lambda *_: self.player.play_pause(),
            accent=not compact,
        )
        next_btn = self._icon_button("media-skip-forward-symbolic", "Next", lambda *_: self.player.next())
        if compact:
            setattr(self, "play_compact", play_btn)
        else:
            setattr(self, "play_full", play_btn)
        box.append(prev_btn)
        box.append(play_btn)
        box.append(next_btn)
        return box

    def _icon_button(self, icon: str, tooltip: str, callback, accent: bool = False) -> Gtk.Button:
        button = Gtk.Button()
        button.set_icon_name(icon)
        button.set_tooltip_text(tooltip)
        button.add_css_class("icon-button")
        if accent:
            button.add_css_class("accent")
        button.connect("clicked", callback)
        return button

    def _menu_button(self) -> Gtk.MenuButton:
        button = Gtk.MenuButton()
        button.set_icon_name("open-menu-symbolic")
        button.add_css_class("icon-button")
        button.set_tooltip_text("Settings")
        pop = Gtk.Popover()
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=10)
        box.set_margin_top(12)
        box.set_margin_bottom(12)
        box.set_margin_start(12)
        box.set_margin_end(12)

        opacity_label = Gtk.Label(label="Opacity", xalign=0)
        opacity = Gtk.Scale.new_with_range(Gtk.Orientation.HORIZONTAL, 0.35, 1.0, 0.01)
        opacity.set_value(self.settings.opacity)
        opacity.set_draw_value(False)
        opacity.set_size_request(180, -1)
        opacity.connect("value-changed", self._on_opacity)

        art = Gtk.CheckButton(label="Show album art")
        art.set_active(self.settings.show_album_art)
        art.connect("toggled", lambda btn: self._set_flag("show_album_art", btn.get_active()))

        follow = Gtk.CheckButton(label="Keep lyrics in sync with playback")
        follow.set_active(self.settings.follow_playback)
        follow.connect("toggled", lambda btn: self._set_flag("follow_playback", btn.get_active()))

        spotify = Gtk.CheckButton(label="Prefer Spotify over other players")
        spotify.set_active(self.settings.prefer_spotify)
        spotify.connect("toggled", self._on_prefer_spotify)

        quit_btn = Gtk.Button(label="Quit Lyrik")
        quit_btn.connect("clicked", lambda *_: self.get_application().quit())

        for widget in (opacity_label, opacity, art, follow, spotify, quit_btn):
            box.append(widget)
        pop.set_child(box)
        button.set_popover(pop)
        return button

    def _bind_keys(self) -> None:
        controller = Gtk.EventControllerKey()
        controller.connect("key-pressed", self._on_key)
        self.add_controller(controller)

    def _on_key(self, _controller, keyval: int, _keycode: int, state: Gdk.ModifierType) -> bool:
        ctrl = bool(state & Gdk.ModifierType.CONTROL_MASK)
        if keyval in (Gdk.KEY_space, Gdk.KEY_k):
            self.player.play_pause()
            return True
        if keyval in (Gdk.KEY_Right, Gdk.KEY_n):
            self.player.next()
            return True
        if keyval in (Gdk.KEY_Left, Gdk.KEY_p):
            self.player.previous()
            return True
        if keyval == Gdk.KEY_c:
            self.toggle_compact()
            return True
        if keyval == Gdk.KEY_l and ctrl:
            self.set_click_through(not self.settings.click_through)
            return True
        if keyval == Gdk.KEY_q and ctrl:
            self.get_application().quit()
            return True
        if keyval == Gdk.KEY_Escape:
            if not self.settings.compact:
                self.toggle_compact()
            return True
        return False

    def _on_realize(self, *_args) -> None:
        self._raise_window()
        self._apply_click_through()

    def _raise_window(self) -> None:
        surface = self.get_surface()
        if surface is None:
            return
        if hasattr(surface, "set_startup_id"):
            try:
                surface.set_startup_id("lyrik")
            except Exception:
                pass
        if LAYER_SHELL:
            return
        xid = getattr(surface, "get_xid", lambda: None)()
        if not xid:
            return
        try:
            subprocess.run(
                ["wmctrl", "-i", "-r", str(xid), "-b", "add,above,sticky,skip_taskbar"],
                check=False,
                timeout=1,
                capture_output=True,
            )
        except (FileNotFoundError, subprocess.TimeoutExpired, OSError):
            pass

    def _on_close(self, *_args) -> bool:
        save_settings(self.settings)
        return False

    def _persist_geometry(self) -> bool:
        width, height = self.get_width(), self.get_height()
        if width > 50:
            self.settings.width = width
            if not self.settings.compact:
                self.settings.height = max(height, 180)
            save_settings(self.settings)
        return True

    def _on_opacity(self, scale: Gtk.Scale) -> None:
        self.settings.opacity = scale.get_value()
        self.set_opacity(self.settings.opacity)
        save_settings(self.settings)

    def _set_flag(self, name: str, value: bool) -> None:
        setattr(self.settings, name, value)
        save_settings(self.settings)
        if name == "show_album_art":
            self.art.set_visible(value)
        if name == "follow_playback":
            self._update_lyrics_highlight()

    def _on_prefer_spotify(self, button: Gtk.CheckButton) -> None:
        self.settings.prefer_spotify = button.get_active()
        self.player.prefer_spotify = button.get_active()
        self.player.reconnect()
        save_settings(self.settings)

    def toggle_compact(self, *_args) -> None:
        self.settings.compact = not self.settings.compact
        save_settings(self.settings)
        self._apply_compact()

    def _apply_compact(self) -> None:
        compact = self.settings.compact
        self.root.remove_css_class("compact")
        if compact:
            self.root.add_css_class("compact")
        self.scroller.set_visible(not compact)
        self.full_controls.set_visible(not compact)
        self.compact_controls.set_visible(compact)
        self.set_default_size(self.settings.width, 88 if compact else self.settings.height)
        if compact:
            self.set_size_request(320, 80)
        else:
            self.set_size_request(320, 280)
        self._apply_click_through()

    def _toggle_click_through(self, *_args) -> None:
        self.set_click_through(not self.settings.click_through)

    def set_click_through(self, enabled: bool) -> None:
        self.settings.click_through = enabled
        save_settings(self.settings)
        self._update_lock_button()
        GLib.idle_add(self._apply_click_through)

    def _update_lock_button(self) -> None:
        if self.settings.click_through:
            self.lock_button.set_icon_name("changes-allow-symbolic")
            self.lock_button.set_tooltip_text("Unlock — click this button again (lyrics ignore the mouse)")
            self.lock_button.add_css_class("locked")
            self.player_pill.set_text("LOCKED")
        else:
            self.lock_button.set_icon_name("system-lock-screen-symbolic")
            self.lock_button.set_tooltip_text("Click-through so you can keep working")
            self.lock_button.remove_css_class("locked")

    def _apply_click_through(self) -> bool:
        surface = self.get_surface()
        if surface is None:
            return False
        if not self.settings.click_through:
            surface.set_input_region(None)
            return False

        width = self.get_width()
        header_height = self.header_bar.get_height()
        if width < 8 or header_height < 8 or not self.header_bar.get_mapped():
            # Never leave the user with a fully dead window before layout.
            surface.set_input_region(None)
            return False

        region = cairo.Region(
            cairo.RectangleInt(0, 0, width, header_height + 6)
        )
        surface.set_input_region(region)
        return False

    def _on_seek(self, _scale: Gtk.Scale, _scroll: Gtk.ScrollType, value: float) -> bool:
        length = self.player.state.track.length_s
        if length <= 0:
            return True
        self.player.seek_to(value * length)
        return False

    def _on_player_changed(self, player: MprisClient) -> None:
        state = player.state
        if not state.connected:
            self.player_pill.set_label("NO PLAYER")
            self.title_label.set_text("Spotify is not running")
            self.subtitle_label.set_text("Open Spotify — Premium is not required")
            self._set_placeholder("Start Spotify, then play a track.\nLyrik reads playback through MPRIS, so lyrics and controls work while you stay in another app.")
            self._set_play_icons(False)
            return

        self.player_pill.set_label(state.identity.upper()[:18])
        track = state.track
        self.title_label.set_text(track.title or "Unknown title")
        parts = [part for part in (track.artist, track.album) if part]
        self.subtitle_label.set_text(" · ".join(parts) or " ")
        self._set_play_icons(state.playing)
        self.art.set_visible(self.settings.show_album_art)

        length = track.length_s
        position = state.position_s
        self.progress.set_sensitive(length > 0)
        if length > 0:
            self.progress.set_value(min(1.0, position / length))
        self.elapsed.set_text(format_time(position))
        self.remaining.set_text(format_time(length))

        if track.art_url and track.art_url != self._art_url:
            self._art_url = track.art_url
            load_bytes(track.art_url, self._on_art)

        key = track.identity
        if key != self._lyrics_key and not track.is_empty:
            self._lyrics_key = key
            self._load_lyrics(track.artist, track.title, track.album, int(length) if length else None)

        self._update_lyrics_highlight()
        if self.settings.click_through:
            self._apply_click_through()

    def _set_play_icons(self, playing: bool) -> None:
        icon = "media-playback-pause-symbolic" if playing else "media-playback-start-symbolic"
        for attr in ("play_compact", "play_full"):
            button = getattr(self, attr, None)
            if button is not None:
                button.set_icon_name(icon)

    def _on_art(self, data: bytes | None) -> None:
        if not data:
            return
        try:
            loader = GdkPixbuf.PixbufLoader()
            loader.write(data)
            loader.close()
            pixbuf = loader.get_pixbuf()
        except GLib.Error:
            return
        if pixbuf is None:
            return
        scaled = pixbuf.scale_simple(128, 128, GdkPixbuf.InterpType.BILINEAR)
        texture = Gdk.Texture.new_for_pixbuf(scaled)
        self.art.set_paintable(texture)

    def _load_lyrics(self, artist: str, title: str, album: str, duration: int | None) -> None:
        self._fetch_gen += 1
        gen = self._fetch_gen
        self._set_placeholder("Looking up lyrics…")

        def work() -> None:
            try:
                lyrics = fetch_lyrics(artist, title, album, duration)
            except Exception:
                lyrics = Lyrics()
            GLib.idle_add(self._apply_lyrics, gen, lyrics)

        Thread(target=work, daemon=True).start()

    def _apply_lyrics(self, gen: int, lyrics: Lyrics) -> bool:
        if gen != self._fetch_gen:
            return False
        self._lyrics = lyrics
        self._current_line = -1
        self._rebuild_lyrics()
        return False

    def _clear_lyrics_box(self) -> None:
        child = self.lyrics_box.get_first_child()
        while child is not None:
            nxt = child.get_next_sibling()
            self.lyrics_box.remove(child)
            child = nxt
        self._line_widgets = []

    def _set_placeholder(self, text: str) -> None:
        self._clear_lyrics_box()
        self.placeholder = Gtk.Label(label=text, wrap=True, justify=Gtk.Justification.CENTER)
        self.placeholder.add_css_class("placeholder")
        self.lyrics_box.append(self.placeholder)

    def _rebuild_lyrics(self) -> None:
        self._clear_lyrics_box()
        if self._lyrics.instrumental:
            self._set_placeholder("This track is instrumental.")
            return
        if self._lyrics.synced:
            for line in self._lyrics.synced:
                label = Gtk.Label(label=line.text, wrap=True, xalign=0.5, justify=Gtk.Justification.CENTER)
                label.add_css_class("lyric-line")
                label.set_hexpand(True)
                self.lyrics_box.append(label)
                self._line_widgets.append(label)
            self._update_lyrics_highlight()
            return
        if self._lyrics.plain:
            label = Gtk.Label(label=self._lyrics.plain, wrap=True, xalign=0.5, justify=Gtk.Justification.CENTER)
            label.add_css_class("lyric-line")
            label.add_css_class("near")
            self.lyrics_box.append(label)
            return
        self._set_placeholder("No lyrics found for this track.")

    def _update_lyrics_highlight(self) -> None:
        if not self._line_widgets or not self.settings.follow_playback:
            return
        index = self._lyrics.line_index_at(self.player.state.position_s)
        if index == self._current_line:
            return
        self._current_line = index
        for i, label in enumerate(self._line_widgets):
            label.remove_css_class("current")
            label.remove_css_class("near")
            if i == index:
                label.add_css_class("current")
            elif abs(i - index) == 1:
                label.add_css_class("near")
        if 0 <= index < len(self._line_widgets):
            self._scroll_to(self._line_widgets[index])

    def _scroll_to(self, widget: Gtk.Widget) -> None:
        def later() -> bool:
            adj = self.scroller.get_vadjustment()
            coords = widget.translate_coordinates(self.lyrics_box, 0, 0)
            if not coords:
                return False
            _x, y = coords
            height = widget.get_allocated_height()
            view = adj.get_page_size()
            target = y - (view / 2) + (height / 2)
            adj.set_value(max(adj.get_lower(), min(target, adj.get_upper() - view)))
            return False

        GLib.idle_add(later)
