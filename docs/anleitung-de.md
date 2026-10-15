# Anleitung: Immich-Album auf dem Echo Show anzeigen

Ziel: Fotos aus einem Immich-Album landen automatisch in einem Amazon-Photos-Album, das der Echo Show als Diashow anzeigt.

```
Immich-Album „Echo Show“ ──▶ immich-amazon-sync (Docker) ──▶ Amazon Photos Album „Echo Show“ ──▶ Echo Show
```

Zeitaufwand beim ersten Mal: etwa 30 Minuten.

## 0. Voraussetzungen

- **Immich 3.2 oder neuer.** Version prüfen:
  ```bash
  curl -s http://<immich-host>:2283/api/server/version
  ```
  Ausgabe z. B. `{"major":3,"minor":3,"patch":1,...}`. Bei älteren Versionen bricht das Tool mit einer Fehlermeldung ab.
- **Ein Docker-Host**, idealerweise derselbe Rechner, auf dem Immich läuft.
- **Echo Show und Amazon Photos im selben Amazon-Konto (amazon.de).** Der Echo zeigt nur Alben des Kontos, mit dem er registriert ist.
- Amazon Photos ist bei Prime unbegrenzt für Fotos. Videos zählen auf das Speicherkontingent, deshalb ist der Default `SYNC_TYPES=IMAGE`.

## Schnellstart (Copy & Paste)

Für einen Linux-Docker-Host mit `bash`, auf dem Immich per Docker Compose läuft. Die Blöcke nacheinander ins Terminal kopieren. Annahmen:
- Das Immich-Album und das Amazon-Album heißen beide **„Echo Show“**.
- Installiert wird nach `~/immich-amazon-sync`.

Vorher erledigen:
- Album und API-Key in Immich anlegen (Abschnitt 2).
- Cookies und User-Agent aus dem Browser kopieren (Abschnitt 3).

**1. Klonen und Image bauen**

```bash
cd ~ && git clone https://github.com/airbone42/immich-amazon-sync.git && cd immich-amazon-sync && docker build --pull -t immich-amazon-sync:local .
```

**2. Immich-Netz finden und Verbindung testen.** Ausgabe muss `"major":3` und `"minor"` ≥ 2 enthalten.

```bash
cd ~/immich-amazon-sync && NET=$(docker network ls --format '{{.Name}}' | grep -m1 immich) && echo "Immich-Netz: $NET" && docker run --rm --network "$NET" --entrypoint python3 immich-amazon-sync:local -c "import urllib.request; print(urllib.request.urlopen('http://immich-server:2283/api/server/version').read().decode())"
```

**3. Secrets eingeben.** Die Eingabe ist verdeckt; einfügen und Enter drücken.
- `immich_api_key`: der Immich-API-Key
- `ap_session_id`: Cookie `session-id`
- `ap_ubid_value`: Cookie `ubid-acbde`
- `ap_at_value`: Cookie `at-acbde`

```bash
cd ~/immich-amazon-sync && mkdir -p data secrets && chmod 700 secrets && for s in immich_api_key ap_session_id ap_ubid_value ap_at_value; do read -rsp "$s: " v; echo; printf '%s' "$v" > "secrets/$s"; done; unset v
```

**4. Rechte für den Container-User (UID 10001)**

```bash
cd ~/immich-amazon-sync && sudo chown 10001:10001 data secrets/* && sudo chmod 700 data && sudo chmod 400 secrets/*
```

**5. `.env` anlegen.** Fragt nach dem User-Agent deines Browsers.

```bash
cd ~/immich-amazon-sync && read -rp "User-Agent aus dem Browser: " UA && cat > .env <<EOF
IMMICH_URL=http://immich-server:2283
IMMICH_ALBUMS=Echo Show
AP_TLD=de
AP_ALBUM_NAME=Echo Show
AP_DELETE_MODE=trash
AP_USER_AGENT=$UA
SYNC_TYPES=IMAGE
MAX_FILE_MB=200
SYNC_INTERVAL_HOURS=6
MAX_DELETE_FRACTION=0.2
MAX_DELETE_COUNT=50
EOF
chmod 600 .env
```

**6. `compose.yml` anlegen.** Das Immich-Netz wird automatisch eingetragen.

```bash
cd ~/immich-amazon-sync && NET=$(docker network ls --format '{{.Name}}' | grep -m1 immich) && cat > compose.yml <<EOF
services:
  immich-sync:
    image: immich-amazon-sync:local
    container_name: immich-sync
    restart: unless-stopped
    env_file: .env
    environment:
      IMMICH_API_KEY_FILE: /run/secrets/immich_api_key
      AP_SESSION_ID_FILE: /run/secrets/ap_session_id
      AP_UBID_VALUE_FILE: /run/secrets/ap_ubid_value
      AP_AT_VALUE_FILE: /run/secrets/ap_at_value
    secrets: [immich_api_key, ap_session_id, ap_ubid_value, ap_at_value]
    volumes:
      - ./data:/data
    read_only: true
    tmpfs: [/tmp]
    cap_drop: [ALL]
    security_opt: ["no-new-privileges:true"]
    networks: [immich]

secrets:
  immich_api_key: { file: ./secrets/immich_api_key }
  ap_session_id:  { file: ./secrets/ap_session_id }
  ap_ubid_value:  { file: ./secrets/ap_ubid_value }
  ap_at_value:    { file: ./secrets/ap_at_value }

networks:
  immich:
    external: true
    name: $NET
EOF
```

**7. Dry-Run.** Schreibt nichts; zeigt, was hochgeladen würde.

```bash
cd ~/immich-amazon-sync && docker compose run --rm -e DRY_RUN=true -e RUN_ONCE=true immich-sync
```

**8. Erster echter Lauf.** Danach das Album unter amazon.de/photos prüfen.

```bash
cd ~/immich-amazon-sync && docker compose run --rm -e RUN_ONCE=true immich-sync
```

**9. Dauerbetrieb starten.** Synchronisiert alle 6 Stunden.

```bash
cd ~/immich-amazon-sync && docker compose up -d && docker logs -f immich-sync
```

Zum Schluss den Echo Show auf das Album stellen (Abschnitt 9). Die folgenden Abschnitte erklären die einzelnen Schritte ausführlich.

## 1. Code holen und Image bauen

Auf dem Docker-Host:

```bash
git clone https://github.com/airbone42/immich-amazon-sync.git
cd immich-amazon-sync
docker build --pull -t immich-amazon-sync:local .
```

## 2. Immich vorbereiten

### 2.1 Album anlegen

In Immich ein Album anlegen, z. B. **„Echo Show“**, und die Fotos hinzufügen, die auf dem Echo erscheinen sollen. Der Name muss in Immich eindeutig sein. Mehrere Alben gehen auch (kommagetrennt, siehe unten).

### 2.2 Eingeschränkten API-Key erstellen

Immich → Profilbild → **Kontoeinstellungen → API-Schlüssel → Neuer API-Schlüssel**:

- Name: `amazon-sync`
- Berechtigungen: **nur** diese drei anhaken
  - `album.read`
  - `asset.read`
  - `asset.download`

Den Schlüssel kopieren, er wird nur einmal angezeigt.

## 3. Amazon-Cookies besorgen

Das Tool meldet sich mit den Cookies deines Browsers bei Amazon an, ein offizielles API gibt es nicht.

1. Am Desktop-Browser (Chrome/Edge/Firefox) auf <https://www.amazon.de/photos> einloggen. Dabei **„Angemeldet bleiben“** anhaken.
2. **F12** → Reiter **Netzwerk** (Network) → Seite neu laden (F5).
3. Eine beliebige Anfrage an `www.amazon.de` anklicken → **Anforderungsheader** (Request Headers) → Zeile `Cookie`.
4. Aus dieser Zeile drei Werte heraussuchen, jeweils der Teil nach `=` bis zum nächsten `;`:

   | Cookie | Datei (Schritt 4) |
   |---|---|
   | `session-id` | `secrets/ap_session_id` |
   | `ubid-acbde` | `secrets/ap_ubid_value` |
   | `at-acbde` | `secrets/ap_at_value` (beginnt meist mit `Atza|`) |

5. Unter **Netzwerk** steht in derselben Anfrage auch `User-Agent`. Den kopierst du für `AP_USER_AGENT`.

Wichtig:
- **Dich danach in diesem Browser nicht bei Amazon abmelden.** Abmelden macht die Cookies ungültig.
- Die Cookies sind Zugangsdaten zu deinem Amazon-Konto. Behandle sie wie ein Passwort.
- Sie laufen nach einigen Wochen bis Monaten ab. Dann diesen Schritt wiederholen (siehe Abschnitt 9).

## 4. Konfiguration anlegen

Im Ordner `immich-amazon-sync`:

```bash
mkdir -p data secrets
chmod 700 secrets

# Secrets je in eine eigene Datei, ohne Anführungszeichen
printf '%s' 'DEIN_IMMICH_API_KEY'  > secrets/immich_api_key
printf '%s' 'DEINE_SESSION_ID'     > secrets/ap_session_id
printf '%s' 'DEIN_UBID_ACBDE_WERT' > secrets/ap_ubid_value
printf '%s' 'DEIN_AT_ACBDE_WERT'   > secrets/ap_at_value

# Der Container läuft als UID 10001 und muss Daten und Secrets lesen können
sudo chown 10001:10001 data secrets/*
sudo chmod 700 data
sudo chmod 400 secrets/*
```

> Tipp: Ein Leerzeichen vor dem Befehl verhindert bei den meisten Shells, dass die Secrets in der Bash-History landen. Alternativ die Dateien mit einem Editor anlegen.

Dann die Datei `.env` (ohne Secrets):

```bash
cat > .env <<'EOF'
IMMICH_URL=http://immich-server:2283
IMMICH_ALBUMS=Echo Show

AP_TLD=de
AP_ALBUM_NAME=Echo Show
AP_DELETE_MODE=trash
AP_USER_AGENT=HIER_DEN_USER_AGENT_AUS_SCHRITT_3_EINFUEGEN

SYNC_TYPES=IMAGE
MAX_FILE_MB=200
SYNC_INTERVAL_HOURS=6
MAX_DELETE_FRACTION=0.2
MAX_DELETE_COUNT=50
# HEALTHCHECK_URL=https://hc-ping.com/deine-uuid
EOF
chmod 600 .env
```

### Welche `IMMICH_URL`?

- **Immich läuft per Docker Compose auf demselben Host** (Normalfall): Der Sync-Container wird ins Docker-Netz von Immich gehängt (siehe Schritt 5). Dann ist die URL `http://immich-server:2283`. Den Netzwerknamen findest du mit:
  ```bash
  docker network ls | grep immich
  ```
  Bei der offiziellen Immich-Compose-Datei heißt das Netz meist `immich_default`.
- **Immich läuft auf einem anderen Rechner:** `http://<ip-des-immich-hosts>:2283`. Dann in Schritt 5 den Block `networks` weglassen.
- Die URL ohne `/api` angeben.

## 5. Docker Compose anlegen

`compose.yml` im selben Ordner:

```yaml
services:
  immich-sync:
    image: immich-amazon-sync:local
    container_name: immich-sync
    restart: unless-stopped
    env_file: .env
    environment:
      IMMICH_API_KEY_FILE: /run/secrets/immich_api_key
      AP_SESSION_ID_FILE: /run/secrets/ap_session_id
      AP_UBID_VALUE_FILE: /run/secrets/ap_ubid_value
      AP_AT_VALUE_FILE: /run/secrets/ap_at_value
    secrets: [immich_api_key, ap_session_id, ap_ubid_value, ap_at_value]
    volumes:
      - ./data:/data
    read_only: true
    tmpfs: [/tmp]
    cap_drop: [ALL]
    security_opt: ["no-new-privileges:true"]
    networks: [immich]

secrets:
  immich_api_key: { file: ./secrets/immich_api_key }
  ap_session_id:  { file: ./secrets/ap_session_id }
  ap_ubid_value:  { file: ./secrets/ap_ubid_value }
  ap_at_value:    { file: ./secrets/ap_at_value }

networks:
  immich:
    external: true
    name: immich_default   # Name aus `docker network ls`
```

## 6. Erster Test: Dry-Run

Der Dry-Run schreibt nichts nach Amazon und nichts in den State. Er prüft aber alles: Immich-Version, Album, API-Key, Cookies und das Amazon-Album.

```bash
docker compose run --rm -e DRY_RUN=true -e RUN_ONCE=true immich-sync
```

Erwartete Ausgabe (gekürzt):

```
Starting Immich → Amazon Photos sync [DRY RUN]
Immich server version 3.3.1
Amazon Photos root node: ...
[dry-run] Would create Amazon Photos album 'Echo Show'
Album 'Echo Show': 42 assets, 42 of type IMAGE
42 assets to mirror, 42 new
  [dry-run] Would upload IMG_1234.jpg (...)
Done. Uploaded: 0, Deleted: 0, Skipped: 0, Errors: 0, Tracked: 0 [DRY RUN - nothing written]
```

Typische Fehler:

| Meldung | Ursache / Lösung |
|---|---|
| `Immich ... is too old` | Immich auf ≥ 3.2 aktualisieren |
| `Album not found in Immich: 'Echo Show'` | Name in `IMMICH_ALBUMS` exakt wie in Immich schreiben, Groß-/Kleinschreibung beachten |
| `HTTP Error 401` bei Immich | API-Key falsch oder ohne die drei Berechtigungen |
| `HTTP Error 403` bei Immich | API-Key fehlt eine der drei Berechtigungen |
| `Name or service not known` / `Connection refused` | `IMMICH_URL` oder Netzwerkname in `compose.yml` prüfen |
| `cookies expired or invalid` (Exit-Code 3) | Cookies falsch kopiert oder abgelaufen → Schritt 3 wiederholen |
| `Permission denied` auf `/data` oder `/run/secrets/...` | `chown 10001:10001` aus Schritt 4 vergessen |

## 7. Erster echter Lauf

```bash
docker compose run --rm -e RUN_ONCE=true immich-sync
```

Danach unter <https://www.amazon.de/photos> → **Alben** prüfen: Das Album **„Echo Show“** sollte existieren und alle Fotos enthalten. Die Dateien liegen zusätzlich in der Bibliothek; das ist normal, denn Amazon-Alben verweisen nur auf Dateien.

Ein zweiter Aufruf desselben Befehls sollte `Uploaded: 0, Deleted: 0` melden.

## 8. Dauerbetrieb starten

```bash
docker compose up -d
docker logs -f immich-sync
```

Das Tool synchronisiert jetzt alle 6 Stunden (`SYNC_INTERVAL_HOURS`). Einen Sync sofort anstoßen:

```bash
docker restart immich-sync
```

## 9. Echo Show einrichten

Am Echo Show:

1. Vom oberen Bildschirmrand nach unten wischen → **Einstellungen**.
2. **Startbildschirm & Uhr** → **Uhr und Fotoanzeige** (bei manchen Firmware-Versionen heißt es **Hintergrund** oder **Fotoanzeige**).
3. **Amazon Photos** wählen → Album **„Echo Show“** auswählen.

Alternativ geht es in der **Alexa-App**: **Geräte** → **Echo & Alexa** → dein Echo Show → **Fotoanzeige** / **Startbildschirm**.

Die Menübezeichnungen ändern sich gelegentlich mit Firmware-Updates. Gesucht ist die Einstellung für die Fotoquelle der Uhr bzw. des Startbildschirms.

Neue Fotos erscheinen auf dem Echo, nachdem der nächste Sync gelaufen ist und der Echo das Album aktualisiert hat. Das kann ein paar Stunden dauern.

## 10. Alltag

- **Foto hinzufügen:** In Immich ins Album legen. Es erscheint nach dem nächsten Sync.
- **Foto entfernen:** In Immich aus dem Album nehmen. Beim nächsten Sync wird es aus dem Amazon-Album entfernt und in den Amazon-Papierkorb verschoben.
  - Mit `AP_DELETE_MODE=album_only` bleibt es stattdessen in der Amazon-Bibliothek.
  - Fotos, die schon vorher in Amazon Photos lagen (z. B. aus der Handy-Sicherung), werden nie in den Papierkorb verschoben, nur aus dem Album genommen.
- **Löschbremse:** Würden in einem Lauf mehr als 20 % oder mehr als 50 der Fotos gelöscht, löscht das Tool gar nichts und meldet `Deletion brake` (Exit-Code 2). Bei kleinen Alben passiert das schnell: 1 von 4 Fotos sind schon 25 %. Wenn das Absicht war, einmalig mit höherer Grenze laufen lassen:
  ```bash
  docker compose run --rm -e RUN_ONCE=true -e MAX_DELETE_FRACTION=1 immich-sync
  ```
- **Cookies abgelaufen:** Im Log steht `cookies expired or invalid`, Exit-Code 3. Dann:
  1. Schritt 3 wiederholen.
  2. Die drei Dateien in `secrets/` überschreiben. Sie gehören jetzt UID 10001, also mit `sudo`, z. B.:
     ```bash
     sudo sh -c "printf '%s' 'NEUER_AT_WERT' > secrets/ap_at_value"
     ```
  3. `docker compose up -d --force-recreate` ausführen.
- **Überwachung (empfohlen):** Bei <https://healthchecks.io> einen Check mit Periode 6 h anlegen und `HEALTHCHECK_URL` setzen. Bleibt der Ping aus (Cookies abgelaufen, Immich nicht erreichbar, Löschbremse), bekommst du eine Mail.
- **Updates des Tools:**
  ```bash
  git pull
  docker build --pull -t immich-amazon-sync:local .
  docker compose up -d --force-recreate
  ```
- **Logs:** `docker logs immich-sync` oder `data/immich-sync.log`.
- **Alles neu synchronisieren:** `data/state.json` löschen. Vorhandene Fotos werden über die MD5-Prüfung wiedererkannt und nicht doppelt hochgeladen.

## Hinweis

Das Tool nutzt die inoffiziellen Schnittstellen der Amazon-Photos-Webseite. Amazon kann sie jederzeit ändern. Die Nutzung erfolgt auf eigenes Risiko und kann gegen die Amazon-Nutzungsbedingungen verstoßen.
