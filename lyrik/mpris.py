from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Callable

from gi.repository import Gio, GLib, GObject

SPOTIFY_HINTS = ("spotify",)
IGNORE_HINTS = ("firefox", "chromium", "chrome", "brave", "edge", "plasma-browser")
MPRIS_PREFIX = "org.mpris.MediaPlayer2."
PLAYER_PATH = "/org/mpris/MediaPlayer2"
PLAYER_IFACE = "org.mpris.MediaPlayer2.Player"
PROPS_IFACE = "org.freedesktop.DBus.Properties"


def unwrap(value: Any) -> Any:
    if isinstance(value, GLib.Variant):
        return unwrap(value.unpack())
    if isinstance(value, dict):
        return {key: unwrap(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [unwrap(item) for item in value]
    return value


@dataclass
class Track:
    id: str = ""
    title: str = ""
    artist: str = ""
    album: str = ""
    length_us: int = 0
    art_url: str = ""
    url: str = ""

    @property
    def length_s(self) -> float:
        return self.length_us / 1_000_000 if self.length_us else 0.0

    @property
    def identity(self) -> str:
        return f"{self.artist}|{self.title}|{self.album}|{self.length_us}"

    @property
    def is_empty(self) -> bool:
        return not self.title


@dataclass
class PlayerState:
    connected: bool = False
    bus_name: str = ""
    identity: str = "Spotify"
    status: str = "Stopped"
    position_us: int = 0
    volume: float = 1.0
    can_go_next: bool = True
    can_go_previous: bool = True
    can_seek: bool = True
    track: Track = field(default_factory=Track)

    @property
    def playing(self) -> bool:
        return self.status == "Playing"

    @property
    def position_s(self) -> float:
        return self.position_us / 1_000_000


class MprisClient(GObject.GObject):
    __gsignals__ = {"changed": (GObject.SignalFlags.RUN_FIRST, None, ())}

    def __init__(self, prefer_spotify: bool = True) -> None:
        super().__init__()
        self.prefer_spotify = prefer_spotify
        self.state = PlayerState()
        self._bus = Gio.bus_get_sync(Gio.BusType.SESSION, None)
        self._proxy: Gio.DBusProxy | None = None
        self._props: Gio.DBusProxy | None = None
        self._owner_id = 0
        self._tick_id = 0
        self._listen_for_players()
        self.reconnect()

    def _listen_for_players(self) -> None:
        self._owner_id = self._bus.signal_subscribe(
            "org.freedesktop.DBus",
            "org.freedesktop.DBus",
            "NameOwnerChanged",
            "/org/freedesktop/DBus",
            None,
            Gio.DBusSignalFlags.NONE,
            self._on_name_owner_changed,
        )

    def _on_name_owner_changed(
        self,
        _connection: Gio.DBusConnection,
        _sender: str,
        _path: str,
        _iface: str,
        _signal: str,
        params: GLib.Variant,
    ) -> None:
        name, old, new = params.unpack()
        if not str(name).startswith(MPRIS_PREFIX):
            return
        if self.state.bus_name == name and not new:
            self.reconnect()
            return
        if new and not self.state.connected:
            self.reconnect()

    def available_players(self) -> list[str]:
        try:
            result = self._bus.call_sync(
                "org.freedesktop.DBus",
                "/org/freedesktop/DBus",
                "org.freedesktop.DBus",
                "ListNames",
                None,
                GLib.VariantType.new("(as)"),
                Gio.DBusCallFlags.NONE,
                1500,
                None,
            )
        except GLib.Error:
            return []
        names = [name for name in result.unpack()[0] if name.startswith(MPRIS_PREFIX)]
        names.sort(key=self._rank)
        return names

    def _rank(self, bus_name: str) -> tuple[int, str]:
        short = bus_name.removeprefix(MPRIS_PREFIX).lower()
        if any(hint in short for hint in SPOTIFY_HINTS):
            return (0, short)
        if any(hint in short for hint in IGNORE_HINTS):
            return (2, short)
        return (1, short)

    def reconnect(self) -> None:
        names = self.available_players()
        chosen = ""
        if self.prefer_spotify:
            chosen = next((name for name in names if self._rank(name)[0] == 0), "")
        if not chosen and names:
            chosen = names[0]
        if chosen == self.state.bus_name and self._proxy is not None:
            return
        self._detach()
        if not chosen:
            self.state = PlayerState()
            self.emit("changed")
            return
        self._attach(chosen)

    def _detach(self) -> None:
        if self._tick_id:
            GLib.source_remove(self._tick_id)
            self._tick_id = 0
        self._proxy = None
        self._props = None

    def _attach(self, bus_name: str) -> None:
        flags = Gio.DBusProxyFlags.NONE
        try:
            self._proxy = Gio.DBusProxy.new_sync(
                self._bus,
                flags,
                None,
                bus_name,
                PLAYER_PATH,
                PLAYER_IFACE,
                None,
            )
            self._props = Gio.DBusProxy.new_sync(
                self._bus,
                flags,
                None,
                bus_name,
                PLAYER_PATH,
                PROPS_IFACE,
                None,
            )
        except GLib.Error:
            self.state = PlayerState()
            self.emit("changed")
            return
        self._proxy.connect("g-properties-changed", self._on_properties_changed)
        self.state.bus_name = bus_name
        self.state.connected = True
        self.state.identity = self._read_identity(bus_name)
        self.refresh()
        self._tick_id = GLib.timeout_add(250, self._tick)

    def _read_identity(self, bus_name: str) -> str:
        try:
            result = self._bus.call_sync(
                bus_name,
                PLAYER_PATH,
                PROPS_IFACE,
                "Get",
                GLib.Variant("(ss)", ("org.mpris.MediaPlayer2", "Identity")),
                GLib.VariantType.new("(v)"),
                Gio.DBusCallFlags.NONE,
                800,
                None,
            )
            value = unwrap(result.unpack()[0])
            return str(value) if value else bus_name.removeprefix(MPRIS_PREFIX)
        except GLib.Error:
            return bus_name.removeprefix(MPRIS_PREFIX)

    def _on_properties_changed(self, _proxy: Gio.DBusProxy, changed: GLib.Variant, _invalidated: Any) -> None:
        data = unwrap(changed)
        self._apply_props(data if isinstance(data, dict) else {})
        self.emit("changed")

    def _prop(self, name: str) -> Any:
        if not self._proxy:
            return None
        value = self._proxy.get_cached_property(name)
        return unwrap(value) if value is not None else None

    def _apply_props(self, changed: dict[str, Any] | None = None) -> None:
        changed = changed or {}
        status = changed.get("PlaybackStatus", self._prop("PlaybackStatus"))
        if status:
            self.state.status = str(status)
        volume = changed.get("Volume", self._prop("Volume"))
        if volume is not None:
            self.state.volume = float(volume)
        can_next = changed.get("CanGoNext", self._prop("CanGoNext"))
        if can_next is not None:
            self.state.can_go_next = bool(can_next)
        can_prev = changed.get("CanGoPrevious", self._prop("CanGoPrevious"))
        if can_prev is not None:
            self.state.can_go_previous = bool(can_prev)
        can_seek = changed.get("CanSeek", self._prop("CanSeek"))
        if can_seek is not None:
            self.state.can_seek = bool(can_seek)
        metadata = changed.get("Metadata", self._prop("Metadata")) or {}
        if metadata:
            self.state.track = Track(
                id=str(metadata.get("mpris:trackid") or ""),
                title=str(metadata.get("xesam:title") or "Unknown title"),
                artist=_artist(metadata.get("xesam:artist")),
                album=str(metadata.get("xesam:album") or ""),
                length_us=int(metadata.get("mpris:length") or 0),
                art_url=str(metadata.get("mpris:artUrl") or ""),
                url=str(metadata.get("xesam:url") or ""),
            )

    def refresh(self) -> None:
        if not self._proxy:
            return
        self._apply_props()
        self._read_position()
        self.emit("changed")

    def _read_position(self) -> None:
        if not self.state.bus_name:
            return
        try:
            result = self._bus.call_sync(
                self.state.bus_name,
                PLAYER_PATH,
                PROPS_IFACE,
                "Get",
                GLib.Variant("(ss)", (PLAYER_IFACE, "Position")),
                GLib.VariantType.new("(v)"),
                Gio.DBusCallFlags.NONE,
                400,
                None,
            )
            self.state.position_us = int(unwrap(result.unpack()[0]) or 0)
        except GLib.Error:
            cached = self._prop("Position")
            if cached is not None:
                self.state.position_us = int(cached)

    def _tick(self) -> bool:
        if not self.state.connected:
            return False
        if self.state.playing:
            self._read_position()
            self.emit("changed")
        return True

    def _call(self, method: str, args: GLib.Variant | None = None) -> None:
        if not self._proxy:
            return
        try:
            self._proxy.call_sync(method, args, Gio.DBusCallFlags.NONE, 800, None)
        except GLib.Error:
            pass

    def play_pause(self) -> None:
        self._call("PlayPause")

    def next(self) -> None:
        self._call("Next")

    def previous(self) -> None:
        self._call("Previous")

    def stop(self) -> None:
        self._call("Stop")

    def seek_to(self, seconds: float) -> None:
        track_id = self.state.track.id
        position = int(max(0, seconds) * 1_000_000)
        if track_id and self._proxy:
            try:
                self._proxy.call_sync(
                    "SetPosition",
                    GLib.Variant("(ox)", (track_id, position)),
                    Gio.DBusCallFlags.NONE,
                    800,
                    None,
                )
                return
            except GLib.Error:
                pass
        delta = position - self.state.position_us
        self._call("Seek", GLib.Variant("(x)", (delta,)))


def _artist(value: Any) -> str:
    if isinstance(value, list):
        return ", ".join(str(item) for item in value if item)
    return str(value or "")


def load_bytes(url: str, on_done: Callable[[bytes | None], None]) -> None:
    if not url:
        on_done(None)
        return

    def work() -> None:
        data: bytes | None = None
        try:
            if url.startswith("file://"):
                from pathlib import Path
                from urllib.parse import unquote, urlparse

                path = Path(unquote(urlparse(url).path))
                data = path.read_bytes() if path.exists() else None
            else:
                request = urllib_request(url)
                data = request
        except OSError:
            data = None
        def done() -> bool:
            on_done(data)
            return False

        GLib.idle_add(done)

    from threading import Thread

    Thread(target=work, daemon=True).start()


def urllib_request(url: str) -> bytes:
    import urllib.request

    request = urllib.request.Request(url, headers={"User-Agent": "Lyrik/0.1"})
    with urllib.request.urlopen(request, timeout=10) as response:
        return response.read()
