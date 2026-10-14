# SmartKnob Local für Home Assistant

Diese benutzerdefinierte Home-Assistant-Integration verbindet den SmartKnob direkt über das lokale WLAN. Sie benötigt weder MQTT noch einen WebSocket-Server. Spotify läuft eigenständig auf dem SmartKnob; Home Assistant ergänzt Leuchten, Szenen und Media Player.

## Funktionen

- automatische Erkennung per mDNS
- manuelle Verbindung über die IPv4-Adresse als Alternative
- lokale, mit dem Geräteschlüssel geschützte Kommunikation
- Leuchten: Ein/Aus, Helligkeit, RGB und Farbtemperatur
- Szenen: aktivieren
- Media Player: Lautstärke, Wiedergabe, Stumm, Titelwechsel, Quellen und Ein/Aus
- Anzeige von Titel und Künstler
- Auswahl von bis zu acht Home-Assistant-Entitäten
- klare Verbindungsanzeige und kein späteres Ausführen alter Befehle

## Installation über HACS

[![Repository in HACS öffnen](https://my.home-assistant.io/badges/hacs_repository.svg)](https://my.home-assistant.io/redirect/hacs_repository/?owner=Delein22&repository=smartknob-home-assistant-integration&category=integration)

Alternativ in HACS unter **Integrationen → Benutzerdefinierte Repositories** dieses Repository als Kategorie **Integration** hinzufügen:

`https://github.com/Delein22/smartknob-home-assistant-integration`

Danach **SmartKnob Local** herunterladen und Home Assistant neu starten.

## Einrichtung

1. Unter **Einstellungen → Geräte & Dienste** auf **Integration hinzufügen** klicken.
2. **SmartKnob Local** auswählen. Ein per mDNS gefundener Knob erscheint normalerweise automatisch.
3. Falls die Erkennung nicht funktioniert, die IPv4-Adresse des Knobs eingeben.
4. Den 64-stelligen Geräteschlüssel eingeben.
5. Bis zu acht Leuchten, Szenen oder Media Player auswählen.

Nur eine Home-Assistant-Instanz sollte den Knob gleichzeitig steuern. Eine bisher verwendete Testintegration daher vor der Einrichtung deaktivieren.

## Voraussetzungen

- Home Assistant 2026.10.1 oder neuer
- SmartKnob mit der passenden lokalen Firmware
- SmartKnob und Home Assistant müssen sich gegenseitig im lokalen Netzwerk erreichen können
