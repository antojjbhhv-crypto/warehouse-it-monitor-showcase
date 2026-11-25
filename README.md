# Warehouse IT Monitor

[Deutsch](README.md) · [English](README.en.md)

**Version:** 0.7.0  
**Zweck:** Lokales Lernprojekt zur Überwachung ausdrücklich konfigurierter HTTP/HTTPS- und TCP-Dienste mit Prüfverlauf, Analysen und SQLite.

## Funktionen

- Lädt ausschließlich HTTP/HTTPS-URLs, die ausdrücklich in einer JSON-Datei angegeben sind. Es gibt weder eine automatische Netzwerksuche noch eine Gerätesuche.
- Prüft die Erreichbarkeit mit HTTP-GET-Anfragen und speichert den Status `UP`/`DOWN`, den Antwortcode, die Antwortzeit und etwaige Fehler.
- Prüft einen TCP-Port mit genau einer Verbindung zu einem ausdrücklich angegebenen `host:port`. Es werden weder Ports noch Portbereiche gescannt.
- Speichert Prüfergebnisse und Statusänderungen in SQLite.
- Sendet optionale Telegram-Benachrichtigungen bei Statuswechseln `UP → DOWN` und `DOWN → UP`. Die erste Statusfeststellung und wiederholte identische Statuswerte lösen keine Benachrichtigung aus.
- Verwendet eine persistente SQLite-Outbox mit erneuten Zustellversuchen. Die Telegram-API blockiert die Prüfungen nicht.
- Führt automatische Prüfungen in einem konfigurierbaren Intervall aus. Eine manuelle Prüfung setzt den Zeitplan nicht zurück.
- Verhindert parallele Prüfläufe und überspringt ausgefallene Zeitpunkte, statt mehrere nachträgliche Prüfungen unmittelbar hintereinander auszuführen.
- Stellt ein Dashboard, den Dienststatus, Ereignisse, den Countdown bis zur nächsten Prüfung, SVG-Diagramme und einen filterbaren Verlauf bereit.
- Berechnet eine stichprobenbasierte Verfügbarkeit: `UP / (UP + DOWN) × 100`. Dies ist weder eine kontinuierlich gemessene Verfügbarkeit noch ein SLA. Zeiträume ohne Messwerte bleiben `null`; die durchschnittliche Antwortzeit berücksichtigt nur `UP`-Ergebnisse.
- Erstellt und prüft SQLite-Sicherungen und stellt sie nach einer Prüfung wieder her. Vorhandene Datenbanken werden dabei durch eine Sicherheitskopie geschützt.
- Bietet einen optionalen Start über Docker Compose mit einer separaten Konfiguration für den Demo-Dienst.
- Enthält einen GitHub-Actions-Workflow für Tests unter Ubuntu und Windows mit Python 3.13 und 3.15.1.

## Sicherheit und Einschränkungen

Diese Anwendung ist ein lokales Lernprojekt und kein produktionsreifes Überwachungssystem. Sie prüft ausschließlich URLs aus der konfigurierten JSON-Datei. Füge nur eigene Dienste oder Adressen hinzu, zu deren Prüfung du berechtigt bist. Speichere keine Passwörter oder Tokens in URLs. Das Dashboard wird standardmäßig nur an der Loopback-Schnittstelle bereitgestellt; veröffentliche es nicht direkt im Internet.

Der Scheduler läuft innerhalb des FastAPI-Prozesses. Starte Uvicorn in dieser Version mit genau einem Worker. Starte nicht gleichzeitig den nativen Monitor und den Docker-Monitor mit derselben SQLite-Datenbank, da dadurch zwei unabhängige Scheduler entstehen.

## Voraussetzungen

- Python 3.11 oder neuer für den nativen Start.
- Windows 10/11, macOS oder Linux.
- Für Docker Compose: installierte Docker Engine mit Compose v2.

## Nativer Start unter Windows

Öffne zwei PowerShell-Fenster im Projektverzeichnis.

### 1. Virtuelle Umgebung erstellen und Abhängigkeiten installieren

```powershell
py -3 -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install --upgrade pip
pip install -r requirements.txt
```

Wenn `.venv` bereits vorhanden ist, musst du sie nicht erneut erstellen. Aktiviere die Umgebung und aktualisiere bei Bedarf die Abhängigkeiten.

### 2. Demo-Dienst starten

Im ersten Fenster:

```powershell
.\.venv\Scripts\Activate.ps1
uvicorn demo_service.main:app --host 127.0.0.1 --port 8001
```

### 3. Monitor starten

Im zweiten Fenster:

```powershell
.\.venv\Scripts\Activate.ps1
uvicorn app.main:app --host 127.0.0.1 --port 8000
```

Öffne `http://127.0.0.1:8000`. Die erste automatische Prüfung erfolgt nach Ablauf eines vollständigen Intervalls (standardmäßig 60 Sekunden). Für eine sofortige Prüfung verwende **Prüfungen jetzt ausführen**.

Beende den Demo-Dienst mit `Ctrl+C` und starte eine manuelle Prüfung – der Status wechselt zu `DOWN`. Nach dem erneuten Start des Demo-Dienstes wechselt der Status bei der nächsten Prüfung zurück zu `UP`.

## Konfiguration

Die Konfigurationsdatei für den nativen Start ist `config/services.json`:

```json
{
  "scheduler": {
    "enabled": true,
    "interval_seconds": 60
  },
  "services": [
    {
      "name": "Warehouse Demo Service",
      "url": "http://127.0.0.1:8001/healthz",
      "timeout_seconds": 2.0
    }
  ]
}
```

`interval_seconds` ist eine Ganzzahl zwischen 5 und 86400. Fehlt der Abschnitt `scheduler`, gelten die Standardwerte: Scheduler aktiviert, Intervall 60 Sekunden. Die JSON-Datei wird als UTF-8 oder UTF-8 mit BOM (`utf-8-sig`) gelesen. Starte den Monitor nach Änderungen an der Datei neu.

Für jeden Dienst kann eine Prüfungsrichtlinie festgelegt werden (alle Felder sind optional):

```json
{
  "name": "Warehouse Demo Service",
  "url": "http://127.0.0.1:8001/healthz",
  "timeout_seconds": 2.0,
  "method": "GET",
  "expected_status_codes": [200, 204, 301, 302],
  "slow_threshold_ms": 750
}
```

- `method` – `GET` oder `HEAD`; Standardwert ist `GET`.
- `expected_status_codes` – nicht leere Liste eindeutiger Statuscodes von 100 bis 599; standardmäßig gelten alle Codes von 200 bis 399 als erfolgreich.
- `slow_threshold_ms` – Schwellenwert für langsame Antworten von 1 bis 60000 ms oder `null`. Eine langsame, aber erfolgreiche Antwort bleibt `UP` und erhält die Kategorie `SLOW_RESPONSE` / `WARNING`; die stichprobenbasierte Verfügbarkeit ändert sich dadurch nicht.

Ergebniskategorien: `OK`, `SLOW_RESPONSE`, `HTTP_STATUS`, `TIMEOUT`, `DNS_ERROR`, `CONNECTION_REFUSED`, `CONNECTION_RESET`, `TLS_ERROR`, `NETWORK_ERROR`, `UNKNOWN_ERROR`. Einträge aus der Zeit vor v0.5.0 werden als `UNCLASSIFIED` gelesen.

### TCP-Prüfungen

Ein Dienst mit `check_type: "tcp"` prüft genau eine ausdrücklich konfigurierte TCP-Verbindung:

```json
{
  "name": "Local Demo TCP Port",
  "check_type": "tcp",
  "host": "127.0.0.1",
  "port": 8001,
  "timeout_seconds": 2.0
}
```

- `host` – Hostname oder IP-Adresse, keine URL; `port` – Ganzzahl von 1 bis 65535; `timeout_seconds` – Wert von 0.2 bis 15.
- Ein erfolgreicher TCP-Verbindungsaufbau ergibt `UP` und die Kategorie `TCP_CONNECTED`. Timeout, DNS-Fehler, Verbindungsablehnung und andere Netzwerkfehler ergeben `DOWN` mit der entsprechenden Kategorie.
- Es werden keine Ports gescannt, keine Portbereiche durchlaufen und keine Hosts gesucht. Geprüft wird ausschließlich der eine konfigurierte `host:port`.
- HTTP- und TCP-Dienste verwenden denselben Scheduler, Verlauf und Backup-/Restore-Mechanismus. Ältere Einträge werden als `http` gelesen.

## Telegram-Benachrichtigungen

Telegram-Benachrichtigungen sind standardmäßig deaktiviert. Erstelle zum Aktivieren im Stammverzeichnis des Projekts eine lokale Datei `.env` auf Grundlage von `.env.example`. Die Datei wird von Git ausgeschlossen:

```dotenv
WITMON_TELEGRAM_ENABLED=true
WITMON_TELEGRAM_BOT_TOKEN=your-bot-token
WITMON_TELEGRAM_CHAT_ID=your-chat-id
WITMON_TELEGRAM_TIMEOUT=10
```

Erstelle einen Bot über den offiziellen Dienst `@BotFather`, öffne einen Chat mit ihm und sende die erste Nachricht. Trage das Bot-Token und die Chat-ID in die lokale Datei `.env` ein. In einem privaten Chat kann der Bot keine Unterhaltung selbst beginnen. Füge das Token niemals zu Git hinzu und sende es nicht in Nachrichten.

Nachrichten werden ausschließlich bei tatsächlichen Statuswechseln `UP → DOWN` und `DOWN → UP` erstellt. Der erste Status dient als Ausgangswert und löst keine Benachrichtigung aus. Wiederholte identische Statuswerte erzeugen keine Nachrichten. Die Warteschlange wird in SQLite gespeichert. Bei einem Fehler der Telegram-API wird die Nachricht nach 60 Sekunden, 5 Minuten, 15 Minuten und 1 Stunde erneut gesendet; es gibt höchstens fünf Zustellversuche. Nach Ausschöpfen der Versuche bleibt der Eintrag zur Fehleranalyse im Status `failed`. Prüfungen und Scheduler warten nicht auf den Nachrichtenversand.

Öffne für den nativen Start PowerShell im Projektstammverzeichnis und führe vor dem Start von Uvicorn im selben Fenster Folgendes aus:

```powershell
Get-Content .env | ForEach-Object {
  $line = $_.Trim()
  if ($line -and -not $line.StartsWith('#')) {
    $parts = $line -split '=', 2
    if ($parts.Count -eq 2) {
      [Environment]::SetEnvironmentVariable($parts[0].Trim(), $parts[1].Trim(), 'Process')
    }
  }
}
uvicorn app.main:app --host 127.0.0.1 --port 8000
```

Für Docker Compose werden die Variablen automatisch aus der Datei `.env` im Projektstammverzeichnis übernommen. Erstelle den Container nach Änderungen an dieser Datei neu: `docker compose up -d`.

## API

- `GET /healthz` – Anwendungsstatus und Version.
- `GET /api/services` – Liste der aktivierten Dienste und ihrer letzten Prüfung.
- `GET /api/events` – Protokoll der Statusänderungen.
- `GET /api/scheduler` – Scheduler-Status und Zeitpunkt der nächsten Prüfung.
- `GET /api/analytics?period=7d&service_id=1` – Analysen für `24h`, `7d`, `30d` oder `90d`; `service_id` ist optional.
- `GET /api/history?period=7d&service_id=1&status=DOWN&category=TIMEOUT&check_type=tcp&limit=50&offset=0` – Gefilterter Verlauf mit Seitennavigation; `category` und `check_type` (`http`/`tcp`) sind optional.
- `POST /api/checks/run` – Manuelles Starten der Prüfungen.

## SQLite-Sicherung und Wiederherstellung

Verwendet wird die integrierte SQLite Online Backup API. Die Sicherung enthält daher einen konsistenten Datenbankschnappschuss und ist keine einfache Dateikopie einer laufenden SQLite-Datenbank. Nach der Erstellung wird die Sicherung mit `PRAGMA integrity_check`, `PRAGMA foreign_key_check` und einer Prüfung der erforderlichen Projekttabellen kontrolliert.

Führe in PowerShell im Projektstammverzeichnis Folgendes aus:

```powershell
$stamp = Get-Date -Format 'yyyyMMdd-HHmmss'
python -m app.backup backup --db data/monitor.sqlite3 --output "backups/monitor-$stamp.sqlite3"
python -m app.backup verify --db "backups/monitor-$stamp.sqlite3"
```

Eine Sicherung kann erstellt werden, während die Anwendung läuft. Die Wiederherstellung ist nur im Offline-Betrieb zulässig: Beende zuerst Uvicorn (`Ctrl+C`) und führe anschließend Folgendes aus:

```powershell
python -m app.backup restore --db data/monitor.sqlite3 --input "backups/monitor-YYYYMMDD-HHMMSS.sqlite3"
```

Vor dem Ersetzen wird die ursprüngliche Datenbank standardmäßig unter dem Namen `monitor.pre-restore-YYYYMMDDTHHMMSSZ.sqlite3` im selben Verzeichnis gesichert. Der Restore prüft zunächst die Quelldatei und ersetzt anschließend die Datenbankdatei atomar. Wenn die Datenbank belegt ist oder nicht leere `-wal`-/`-shm`-Begleitdateien erkannt werden, wird die Wiederherstellung abgebrochen. Lösche diese Dateien nicht manuell – beende den Prozess ordnungsgemäß und versuche es erneut.

Mit `backup --overwrite` kann eine vorhandene Sicherungsdatei überschrieben werden. `restore --no-safety-backup` deaktiviert die Sicherheitskopie der aktuellen Datenbank. Verwende diese Option nur, wenn eine andere geprüfte Sicherung vorhanden ist.

## Alternativer Start mit Docker Compose

Docker Compose ist eine alternative Startmöglichkeit und ersetzt nicht den nativen Windows-Prozess. Der Anwendungscontainer verwendet Python 3.15.1. Führe vor dem Start in PowerShell Folgendes aus:

```powershell
New-Item -ItemType Directory -Force data, backups | Out-Null
docker compose up --build -d
```

Das Dashboard ist unter `http://127.0.0.1:8000` erreichbar. Zum Stoppen der Container:

```powershell
docker compose down
```

Compose verwendet die separate Konfigurationsdatei `config/services.docker.json`, in der der lokale Demo-Container unter `http://demo:8001/healthz` eingerichtet ist. Der Demo-Port wird nicht nach außen veröffentlicht; das Dashboard ist nur über Loopback erreichbar. Ändere für den Docker-Modus die Datei `config/services.docker.json`, für den nativen Modus dagegen `config/services.json`. Beide Konfigurationen sollten bewusst und ausschließlich für zulässige Endpunkte geändert werden.

Compose bindet `./data` und `./backups` vom Host ein. Dadurch bleiben SQLite-Datenbank und Sicherungen in den Projektverzeichnissen erhalten und sind nach dem Stoppen der Container über die CLI zugänglich. Um Berechtigungskonflikte bei eingebundenen Verzeichnissen unter Windows zu vermeiden, läuft der Monitor-Dienst in Compose innerhalb des Containers als root. Diese Einstellung ist ausschließlich für den lokalen Lernbetrieb vorgesehen und nicht für eine öffentliche Produktivumgebung.

**Docker lokal geprüft:** Docker Desktop 29.9.3 und Docker Compose v5.7.1 unter Windows. Die Images werden aus `python:3.15.1-slim` erstellt; der Demo-Dienst wird `healthy`, und Dashboard sowie Prüfungen funktionieren in den Containern. Ein separater Docker-Test auf einem Linux-Runner wurde nicht durchgeführt.

## GitHub Actions (CI)

Die Datei `.github/workflows/tests.yml` führt `python -m pytest -v` bei `push`, `pull_request` und manuell über GitHub Actions aus. Die Testmatrix umfasst `ubuntu-latest` und `windows-latest` mit Python 3.13 und 3.15.1. Zusätzlich wird in jedem Job der Ablauf `backup → verify → restore` geprüft.

**Letzter bestätigter CI-Lauf:** Alle vier Jobs waren erfolgreich. Insgesamt bestanden 100 Tests; auch der Smoke-Test `backup → verify → restore` wurde erfolgreich abgeschlossen.

## Tests

```powershell
python -m pytest -v
```

Die Tests verwenden temporäre SQLite-Dateien und Testfunktionen. Sie greifen nicht auf externe Systeme zu und senden keine echten Anfragen an beliebige Dienste.

## Projektstruktur

```text
warehouse-it-monitor/
├── .github/workflows/tests.yml
├── app/
│   ├── migrations/
│   │   └── v070.py
│   ├── backup.py
│   ├── database.py
│   ├── main.py
│   ├── monitor.py
│   ├── notifications.py
│   └── scheduler.py
├── config/
│   ├── services.json
│   └── services.docker.json
├── demo_service/main.py
├── tests/
├── Dockerfile
├── docker-compose.yml
├── requirements.txt
└── README.md
```

## Portfolio

Version 0.7.0 umfasst Python, FastAPI, SQLite, HTTP-Health-Checks für ausdrücklich konfigurierte Endpunkte, TCP-Prüfungen einzelner konfigurierter Ports, einen Hintergrund-Scheduler, stichprobenbasierte Analysen, ein HTML-/CSS-/SVG-Dashboard, pytest, überprüfbare SQLite-Sicherungen und -Wiederherstellungen, Dockerfile, Docker Compose, GitHub Actions CI sowie optionale Telegram-Benachrichtigungen mit persistenter Outbox und Wiederholungsversuchen.

Die CI wurde im GitHub-Repository tatsächlich ausgeführt: Der letzte bestätigte Workflow-Lauf endete erfolgreich mit vier grünen Jobs, 100 bestandenen Tests und einem erfolgreichen Smoke-Test für `backup → verify → restore`. Docker Compose wurde lokal unter Windows geprüft; ein separater Docker-Test auf einem Linux-Runner wurde nicht durchgeführt.
