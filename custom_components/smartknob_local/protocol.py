"""Bounded HTTP client and entity translation, independent of Home Assistant.

Call the blocking client from an executor. It never follows redirects, uses a
proxy, or logs the device key. HTTP is intended for a trusted local network.
"""

from __future__ import annotations

import hashlib
import http.client
import json
import re
from typing import Any

MAX_BODY = 16384
MAX_COMMANDS = 10
MEDIA_PAUSE = 1
MEDIA_VOLUME_SET = 4
MEDIA_VOLUME_MUTE = 8
MEDIA_PREVIOUS_TRACK = 16
MEDIA_NEXT_TRACK = 32
MEDIA_TURN_ON = 128
MEDIA_TURN_OFF = 256
MEDIA_SELECT_SOURCE = 2048
MEDIA_PLAY = 16384


class ProtocolError(Exception):
    """Invalid or unsupported device response."""


class AuthenticationError(ProtocolError):
    """The device key was rejected."""


class SessionExpired(ProtocolError):
    """Reconnect without replaying old commands."""


class RestartRequired(ProtocolError):
    """Legacy UI requires a reboot before changing the app selection."""


def valid_host(host: str) -> str:
    """Accept a hostname or IPv4 address, never a URL or header content."""
    if not isinstance(host, str) or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9.-]{0,252}", host):
        raise ProtocolError("invalid_host")
    return host


class Client:
    def __init__(self, host: str, key: str, port: int = 80) -> None:
        self.host = valid_host(host)
        if not re.fullmatch(r"[0-9a-fA-F]{64}", key):
            raise AuthenticationError("invalid_key")
        if type(port) is not int or not 1 <= port <= 65535:
            raise ProtocolError("invalid_port")
        self.key = key.lower()
        self.port = port

    def request(self, path: str, data: dict | None = None) -> dict:
        conn = http.client.HTTPConnection(self.host, self.port, timeout=2)
        body = None if data is None else json.dumps(data, separators=(",", ":"), ensure_ascii=False, allow_nan=False).encode()
        if body is not None and len(body) > 4096:
            raise ProtocolError("request_too_large")
        try:
            conn.request("GET" if body is None else "POST", "/api/local/v1/" + path,
                         body=body, headers={"Authorization": "Bearer " + self.key, "Content-Type": "application/json"})
            response = conn.getresponse()
            raw = response.read(MAX_BODY + 1)
            if response.status == 401:
                raise AuthenticationError("invalid_key")
            if len(raw) > MAX_BODY:
                raise ProtocolError("response_too_large")
            try:
                result = json.loads(raw)
            except (ValueError, UnicodeError) as err:
                raise ProtocolError("invalid_json") from err
            if not isinstance(result, dict):
                raise ProtocolError("invalid_response")
            if response.status == 409:
                if result.get("error") == "restart_required":
                    raise RestartRequired("restart_required")
                raise SessionExpired("session_expired")
            if response.status != 200:
                raise ProtocolError("http_error")
            return result
        except (OSError, http.client.HTTPException) as err:
            raise ProtocolError("cannot_connect") from err
        finally:
            conn.close()

    def info(self) -> dict:
        result = self.request("info")
        if (type(result.get("protocol")) is not int or result["protocol"] != 1
                or not re.fullmatch(r"[0-9a-f]{12}", str(result.get("id", "")))
                or not re.fullmatch(r"[0-9a-f]{32}", str(result.get("boot", "")))):
            raise ProtocolError("unsupported_device")
        return result


def make_app(entity_id: str, name: str, dimmable: bool = False) -> dict:
    if not re.fullmatch(r"(?:light|scene|media_player)\.[a-z0-9_]+", entity_id) or len(entity_id.encode()) >= 64:
        raise ProtocolError("invalid_entity")
    domain = entity_id.split(".", 1)[0]
    return {
        "app_id": "ha_" + hashlib.sha256(entity_id.encode()).hexdigest()[:16],
        "entity_id": entity_id,
        "friendly_name": name.encode("utf-8")[:63].decode("utf-8", errors="ignore") or entity_id,
        "app_slug": ("scene" if domain == "scene" else
                     "media_player" if domain == "media_player" else
                     "light_dimmer" if dimmable else "light_switch"),
    }


def light_state(app: dict, state: str | None, attributes: dict) -> dict:
    if app["app_slug"] == "media_player":
        features = attributes.get("supported_features", 0)
        # Home Assistant may keep supported_features as an IntFlag instance.
        # IntFlag is an int subclass and must retain its capability bits.
        if not isinstance(features, int):
            features = 0
        volume = attributes.get("volume_level", 0)
        if type(volume) not in (int, float) or not 0 <= volume <= 1:
            volume = 0
        source = attributes.get("source", "")
        if not isinstance(source, str):
            source = ""
        source = source.encode("utf-8")[:63].decode("utf-8", errors="ignore")
        raw_sources = attributes.get("source_list", [])
        sources = []
        if isinstance(raw_sources, (list, tuple)):
            for item in raw_sources:
                if not isinstance(item, str):
                    continue
                item = item.encode("utf-8")[:63].decode("utf-8", errors="ignore")
                if item and item not in sources:
                    sources.append(item)
                if len(sources) == 15:
                    break
        if source and source not in sources:
            if len(sources) == 15:
                sources[-1] = source
            else:
                sources.append(source)
        title = attributes.get("media_title", "")
        artist = attributes.get("media_artist", "")
        title = title if isinstance(title, str) else ""
        artist = artist if isinstance(artist, str) else ""
        title = title.encode("utf-8")[:63].decode("utf-8", errors="ignore")
        artist = artist.encode("utf-8")[:63].decode("utf-8", errors="ignore")
        available = state not in (None, "unknown", "unavailable")
        powered = available and state != "off"
        # Several TV integrations under-report transport and power feature
        # flags although their standard media_player services work. Expose the
        # state-appropriate controls and let HA report a real service failure.
        can_play = bool(features & MEDIA_PLAY) or powered
        can_pause = bool(features & MEDIA_PAUSE) or powered
        can_turn_on = bool(features & MEDIA_TURN_ON) or (available and not powered)
        can_turn_off = bool(features & MEDIA_TURN_OFF) or powered
        return {"app_id": app["app_id"], "available": available,
                "playing": state == "playing", "volume": round(volume * 100),
                "muted": attributes.get("is_volume_muted") is True,
                "powered": powered,
                "can_volume": bool(features & MEDIA_VOLUME_SET),
                "can_play": can_play,
                "can_pause": can_pause,
                "can_mute": bool(features & MEDIA_VOLUME_MUTE),
                "can_previous": bool(features & MEDIA_PREVIOUS_TRACK),
                "can_next": bool(features & MEDIA_NEXT_TRACK),
                "can_turn_on": can_turn_on,
                "can_turn_off": can_turn_off,
                "can_select_source": bool(features & MEDIA_SELECT_SOURCE),
                "source": source, "sources": sources,
                "media_title": title, "media_artist": artist}
    if app["app_slug"] == "scene":
        return {"app_id": app["app_id"], "available": state not in (None, "unknown", "unavailable"),
                "on": False, "brightness": 0, "rgb_color": None, "color_temp": None}
    available = state in ("on", "off")
    brightness = attributes.get("brightness", 255)
    if type(brightness) is not int:
        brightness = 255
    rgb = attributes.get("rgb_color")
    valid_rgb = (isinstance(rgb, (list, tuple)) and len(rgb) == 3
                 and all(type(channel) is int and 0 <= channel <= 255 for channel in rgb))
    color_mode = attributes.get("color_mode")
    kelvin = attributes.get("color_temp_kelvin")
    mired = attributes.get("color_temp")
    if type(kelvin) is int and 1000 <= kelvin <= 40000:
        mired = round(1_000_000 / kelvin)
    valid_temp = type(mired) is int and 1 <= mired <= 1000
    # Home Assistant may retain converted/stale color attributes. Send only the
    # active mode so the firmware never applies RGB and temperature together.
    use_temp = color_mode == "color_temp" and valid_temp
    use_rgb = valid_rgb and not use_temp
    return {"app_id": app["app_id"], "available": available, "on": state == "on",
            "brightness": max(0, min(255, brightness)) if state == "on" else 0,
            "rgb_color": list(rgb) if use_rgb else None,
            "color_temp": mired if use_temp else None}


def commands(response: dict, apps: list[dict], boot: str, acknowledged: int) -> list[tuple[int, str, dict[str, Any]]]:
    """Validate the whole batch before any service call; only configured entities."""
    if response.get("boot") != boot:
        raise SessionExpired("device_rebooted")
    if type(response.get("ready")) is not bool:
        raise ProtocolError("invalid_ready")
    batch = response.get("commands")
    if not isinstance(batch, list) or len(batch) > MAX_COMMANDS or (not response["ready"] and batch):
        raise ProtocolError("invalid_commands")
    known = {app["app_id"]: app for app in apps}
    result = []
    seen_sequences: set[int] = set()
    seen_apps: set[str] = set()
    for item in batch:
        if not isinstance(item, dict) or not isinstance(item.get("app_id"), str):
            raise ProtocolError("invalid_command")
        app = known.get(item["app_id"])
        seq = item.get("seq")
        if (app is None or type(seq) is not int or not 0 < seq <= 2147483647
                or seq in seen_sequences or item["app_id"] in seen_apps):
            raise ProtocolError("invalid_command")
        seen_sequences.add(seq)
        seen_apps.add(item["app_id"])
        data = {"entity_id": app["entity_id"]}
        if app["app_slug"] == "media_player":
            action = item.get("action")
            if action == "volume_set" and type(item.get("volume")) is int and 0 <= item["volume"] <= 100:
                service = "volume_set"
                data["volume_level"] = item["volume"] / 100
            elif action == "play_pause" and "volume" not in item:
                service = "media_play_pause"
            elif action == "play" and "volume" not in item:
                service = "media_play"
            elif action == "pause" and "volume" not in item:
                service = "media_pause"
            elif action == "mute" and type(item.get("muted")) is bool and "volume" not in item:
                service = "volume_mute"
                data["is_volume_muted"] = item["muted"]
            elif action == "next" and "volume" not in item:
                service = "media_next_track"
            elif action == "previous" and "volume" not in item:
                service = "media_previous_track"
            elif action == "turn_on" and "volume" not in item:
                service = "turn_on"
            elif action == "turn_off" and "volume" not in item:
                service = "turn_off"
            elif (action == "select_source" and isinstance(item.get("source"), str)
                  and 0 < len(item["source"].encode("utf-8")) < 64 and "volume" not in item):
                service = "select_source"
                data["source"] = item["source"]
            else:
                raise ProtocolError("invalid_media_command")
            allowed = {"seq", "app_id", "action"}
            if action == "volume_set":
                allowed.add("volume")
            elif action == "select_source":
                allowed.add("source")
            elif action == "mute":
                allowed.add("muted")
            if any(field not in allowed for field in item):
                raise ProtocolError("invalid_media_command")
        elif type(item.get("on")) is not bool:
            raise ProtocolError("invalid_command")
        elif app["app_slug"] == "scene":
            if not item["on"] or any(field in item for field in ("brightness", "rgb_color", "color_temp")):
                raise ProtocolError("invalid_scene_command")
        elif app["app_slug"] == "light_dimmer":
            value = item.get("brightness")
            if type(value) is not int or not 0 <= value <= 255:
                raise ProtocolError("invalid_brightness")
            rgb = item.get("rgb_color")
            color_temp = item.get("color_temp")
            has_rgb = "rgb_color" in item
            has_temp = "color_temp" in item
            if has_rgb and (not isinstance(rgb, list) or len(rgb) != 3
                            or any(type(channel) is not int or not 0 <= channel <= 255 for channel in rgb)):
                raise ProtocolError("invalid_rgb_color")
            if has_temp and (type(color_temp) is not int or not 1 <= color_temp <= 1000):
                raise ProtocolError("invalid_color_temp")
            if has_rgb and has_temp:
                raise ProtocolError("conflicting_color_command")
            if item["on"]:
                data["brightness"] = value
                if has_rgb:
                    data["rgb_color"] = rgb
                elif has_temp:
                    data["color_temp_kelvin"] = round(1_000_000 / color_temp)
        elif any(field in item for field in ("brightness", "rgb_color", "color_temp")):
            raise ProtocolError("invalid_switch_command")
        if seq > acknowledged:
            result.append((seq, service if app["app_slug"] == "media_player" else
                           ("turn_on" if item["on"] else "turn_off"), data))
    return sorted(result, key=lambda command: command[0])
