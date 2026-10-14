# immich-amazon-sync

One-way sync from one or more [Immich](https://immich.app) albums into an **album** in [Amazon Photos](https://www.amazon.de/photos), with amazon.de as the default region.

This is a fork of [vanessa/immich-amazon-sync](https://github.com/vanessa/immich-amazon-sync) (MIT, based on commit `6d95d8c`). It was built for the same purpose: show self-hosted Immich photos on an **Amazon Echo Show**, which only accepts Amazon Photos as a photo source. Changes compared to upstream:

- Works with **Immich ≥ 3.2**: album contents come from `POST /api/search/metadata` with cursor pagination. Upstream relies on `GET /api/albums/{id}` → `assets`, which Immich 3.x no longer returns.
- Configurable region (default **Germany / amazon.de**), with the upload host and cookie names derived from it.
- Targets an Amazon Photos **album** (created if missing), not just the library root.
- Safety: a deletion brake, dry-run mode, file name sanitising, secrets from files, no secrets in logs, non-root container.
- Robustness: streaming downloads and uploads, atomic and versioned state, clear handling of expired cookies, 409 conflict resolution by MD5.

> [!WARNING]
> **Amazon Photos has no public API.** This tool uses the internal Amazon Drive v1 endpoints the web app uses, authenticated with your browser cookies. Amazon can change or block this at any time, and using it may conflict with Amazon's terms of use. Use at your own risk, and always start with a dry run.

## How it works

```
Immich albums ──(search/metadata, download original)──▶ Amazon Drive v1 upload (root folder)
                                                         └─▶ add node to Amazon Photos album
```

- Every run collects all assets of the configured Immich albums (non-trashed, visibility `timeline` or `archive`, i.e. exactly what the album shows).
- New assets are downloaded, streamed to the Amazon Photos root folder and then added to the album `AP_ALBUM_NAME` in batches.
- Assets that are no longer in **any** of the configured albums are removed from the Amazon album and, with `AP_DELETE_MODE=trash` (the default), moved to the Amazon trash. Only nodes this tool uploaded or adopted (those listed in the state file) are ever touched.
- The state file maps Immich asset IDs to Amazon node IDs and is saved after every upload.

## Requirements

- Immich **3.2 or newer** (cursor-based search). Older versions are detected, and the run aborts with an error.
- An Amazon account with Amazon Photos (Prime), tested for amazon.de.
- Docker.

## Setup

### 1. Build the image locally

The image is built from this repository. Do not pull it from a third-party registry.

```bash
git clone <your fork of this repo> immich-amazon-sync
cd immich-amazon-sync
docker build --pull -t immich-amazon-sync:local .
```

The base image is pinned by digest in the `Dockerfile`. To update it, change both the tag and the digest.

### 2. Create a restricted Immich API key

In Immich, go to **Account Settings → API Keys → New API Key**. Do not grant all permissions; select only:

| Permission       | Used for                                                  |
|------------------|-----------------------------------------------------------|
| `album.read`     | `GET /api/albums`, `GET /api/albums/{id}` (`assetCount`)  |
| `asset.read`     | `POST /api/search/metadata`                               |
| `asset.download` | `GET /api/assets/{id}/original`                           |

`GET /api/server/version` (the version check) is public.

### 3. Get the Amazon Photos cookies (amazon.de)

1. Log in to <https://www.amazon.de/photos> in your browser.
2. Open DevTools (F12) → **Application** (Chrome) / **Storage** (Firefox) → **Cookies** → `https://www.amazon.de`.
3. Copy these three values:

   | Cookie       | Variable        |
   |--------------|-----------------|
   | `session-id` | `AP_SESSION_ID` |
   | `ubid-acbde` | `AP_UBID_VALUE` |
   | `at-acbde`   | `AP_AT_VALUE`   |

4. Optionally copy your browser's User-Agent (DevTools → Network → any request → `User-Agent`) into `AP_USER_AGENT`.

> **Note:** `at-acbde` is `HttpOnly` and may not appear in the cookie list. In that case open **Network** → any request to `amazon.de` → **Request Headers** → `Cookie`, and copy the value after `at-acbde=`.

The cookies expire after a few weeks or months. When they do, every run stops at the first Amazon request with `cookies expired or invalid` (exit code 3). Copy fresh values and restart.

Other regions: set `AP_TLD` (for example `com`, `co.uk`, `fr`). Cookie names follow the pattern `ubid-acb<country>` / `at-acb<country>`, and `ubid-main` / `at-main` for amazon.com. The upload host is known for North America (`com`, `ca`) and EU marketplaces. For anything else, set `AP_CONTENT_URL`.

### 4. Configure

```bash
cp .env.example .env
chmod 600 .env
# edit .env
```

Prefer Docker secrets (`*_FILE` variables) for the cookies and API key, so they don't show up in `docker inspect`. See the Compose example below.

### 5. Prepare the data directory

The container runs as UID/GID `10001`. The data directory must be writable by that user:

```bash
mkdir -p data
sudo chown 10001:10001 data
chmod 700 data
```

### 6. Dry run first

```bash
docker run --rm --env-file .env -e DRY_RUN=true -e RUN_ONCE=true \
  -v ./data:/data immich-amazon-sync:local
```

The dry run checks the Immich version, the album lookup, the cookies (read-only Amazon requests) and the album lookup on Amazon. It then logs every planned upload and deletion. It writes nothing to Amazon and nothing to the state file, and it does not download any photos.

### 7. Run

```bash
docker run -d \
  --name immich-sync \
  --restart unless-stopped \
  --env-file .env \
  -e DRY_RUN=false \
  -v ./data:/data \
  immich-amazon-sync:local
```

View logs:

```bash
docker logs -f immich-sync
```

Force a sync now:

```bash
docker restart immich-sync
```

<details>
<summary>Docker Compose with secrets</summary>

```yaml
services:
  immich-sync:
    build: .
    image: immich-amazon-sync:local
    container_name: immich-sync
    restart: unless-stopped
    env_file: .env          # without the secret values
    environment:
      DRY_RUN: "false"
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

secrets:
  immich_api_key: { file: ./secrets/immich_api_key }
  ap_session_id: { file: ./secrets/ap_session_id }
  ap_ubid_value: { file: ./secrets/ap_ubid_value }
  ap_at_value: { file: ./secrets/ap_at_value }
```

Without Swarm, Compose bind-mounts the secret files, so they must be readable by UID 10001 (`sudo chown 10001 secrets/*; sudo chmod 400 secrets/*`). If both are set, a `*_FILE` variable takes precedence over the plain variable.

`/tmp` is used for the downloaded original while it is being uploaded. For videos, make sure the tmpfs is larger than `MAX_FILE_MB`.

</details>

## Configuration

| Variable | Description | Default |
|----------|-------------|---------|
| `IMMICH_URL` | Immich server URL (without `/api`) | - |
| `IMMICH_API_KEY` | Immich API key (`album.read`, `asset.read`, `asset.download`) | - |
| `IMMICH_ALBUMS` | Comma-separated Immich album names (exact match, must be unique) | - |
| `AP_ALBUM_NAME` | Target album in Amazon Photos (created if missing) | - |
| `AP_TLD` | Amazon marketplace TLD (`de`, `com`, `co.uk`, …) | `de` |
| `AP_SESSION_ID` | `session-id` cookie | - |
| `AP_UBID_VALUE` | `ubid-acbde` cookie (or the regional equivalent) | - |
| `AP_AT_VALUE` | `at-acbde` cookie (or the regional equivalent) | - |
| `AP_UBID_KEY` / `AP_AT_KEY` | Override the cookie names derived from `AP_TLD` | derived |
| `AP_CONTENT_URL` | Override the upload host, e.g. `https://content-eu.drive.amazonaws.com/cdproxy` | derived |
| `AP_USER_AGENT` | User-Agent sent to Amazon | recent Chrome |
| `AP_DELETE_MODE` | `trash` (remove from album and move to trash) or `album_only` | `trash` |
| `DRY_RUN` | Log the plan only; write nothing to Amazon or the state file | `false` |
| `MAX_DELETE_FRACTION` | Deletion brake: maximum share of tracked assets deleted per run | `0.2` |
| `MAX_DELETE_COUNT` | Deletion brake: maximum number of assets deleted per run | `50` |
| `SYNC_TYPES` | `IMAGE` or `IMAGE,VIDEO` | `IMAGE` |
| `MAX_FILE_MB` | Larger files are skipped (logged, retried next run) | `200` |
| `HEALTHCHECK_URL` | `GET` after every fully successful run (never logged) | - |
| `SYNC_INTERVAL_HOURS` | Hours between runs | `6` |
| `RUN_ONCE` | Run a single sync and exit (container entrypoint) | `false` |
| `STATE_DIR` | Directory for the state and log files | `/data` |

Every variable can also be read from a file via `<NAME>_FILE`.

### Exit codes

| Code | Meaning |
|------|---------|
| 0 | Success (the healthcheck is pinged) |
| 1 | Errors: config problem, Immich unreachable or too old, album not found, individual uploads failed, `assetCount` mismatch |
| 2 | Planned deletions were **not** executed (deletion brake or incomplete Immich data) |
| 3 | Amazon answered 401/403 or redirected to the sign-in page, so the cookies have expired. The run was aborted. |

## Deletion safety

Deletions only happen when **all** of the following are true:

- Every album in `IMMICH_ALBUMS` was found, exactly once.
- Every Immich query succeeded.
- For every album, the number of assets found by the search equals `assetCount` from `GET /api/albums/{id}`.
- The run deletes at most `MAX_DELETE_COUNT` assets and at most `MAX_DELETE_FRACTION` of the assets tracked before the run (photos uploaded in the same run don't count).

Otherwise nothing is deleted, an error is logged and the exit code is 2. An empty or broken Immich response therefore never causes a mass deletion.

With small albums the fraction limit trips quickly (for example, removing 1 of 4 photos is 25%). To deliberately delete more, raise `MAX_DELETE_FRACTION` / `MAX_DELETE_COUNT` for one run. Ideally do a dry run first.

Changing `SYNC_TYPES` does not delete anything: only assets that left all Immich albums are removed.

## State

`data/state.json` (mode `0600`, written atomically):

```json
{
  "version": 2,
  "album_node_id": "amazon-album-node-id",
  "synced": { "immich-asset-uuid": "amazon-node-id" },
  "pending_album_add": [],
  "adopted_nodes": []
}
```

- `pending_album_add` holds uploaded nodes that are not yet linked to the album. They are linked on the next run, so an interrupted run loses nothing.
- `adopted_nodes` holds nodes that already existed on Amazon (same MD5, found via a 409 conflict), for example a copy from the phone's own Amazon Photos backup. They are linked to the album but, when the photo leaves Immich, they are **only removed from the album and never trashed**.
- The upstream format (`{"synced": {...}}`) is migrated automatically without re-uploading. On the first run, all previously synced photos are added to the album.
- If the album is deleted or renamed in Amazon Photos, it is looked up by name or recreated, and all tracked photos are linked again.
- If the state file is corrupt, the run aborts instead of silently starting from scratch.
- To start over, delete `data/state.json`. Existing uploads are matched again via the 409 conflict and MD5 comparison, not duplicated.
- Logs go to stdout and `data/immich-sync.log` (rotated at 5 MB, 3 backups, mode `0600`).

## Limitations

- **Cookie auth is fragile.** Cookies expire and must be refreshed by hand. There is no OAuth or API key flow.
- **Undocumented API.** Endpoints and request shapes come from the browser app and the [`amazon-photos`](https://github.com/trevorhobenshield/amazon_photos) library, not from official documentation.
- **Files land in the root folder.** They are then added to the album. An album is not a folder, so there is no way to upload into it directly.
- **Deduplication is by Immich asset ID.** On a 409 conflict, the existing Amazon node is adopted only if its MD5 matches. Otherwise the file is uploaded again under `<name>_<assetid8>.<ext>`.
- **Deleted photos go to the trash**, not permanent deletion. Empty the Amazon trash yourself if needed.

## Implementation notes and verification

- **Immich search shape.** Immich 3.2 deprecated the flat fields of `MetadataSearchDto` (`albumIds`, `visibility`, `page`, …) in favour of `filter` / `orderBy` / `cursor`. A request that uses only flat fields takes the legacy path, which returns `nextPage` but **never `nextCursor`**. Combining flat fields with `cursor` is rejected with HTTP 400. This tool therefore sends:

  ```json
  {"filter": {"albumIds": {"any": ["<id>"]},
              "visibility": {"in": ["archive", "timeline"]},
              "trashedAt": {"eq": null}},
   "size": 1000, "cursor": "<nextCursor>"}
  ```

  Verified against `open-api/immich-openapi-specs.json` and the server source at tag `v3.3.1` (`search.service.ts`, `utils/database.ts`, `album.repository.ts`).
- **Visibility.** In the structured path, only `locked` is excluded by default. `hidden` (for example the video part of live photos) and trashed assets would be included. `assetCount` counts `archive` + `timeline`, without trashed assets. The filter above mirrors exactly that, so archived photos in the album are synced.
- **Amazon (from `amazon-photos`, `_api.py`).**
  - EU upload host: `https://content-eu.drive.amazonaws.com/cdproxy/nodes`. North America: `content-na`.
  - Album: `POST /drive/v1/nodes` `{"kind": "VISUAL_COLLECTION", "name": …}`.
  - Add/remove: `PATCH /drive/v1/nodes/{albumId}/children` `{"op": "add" | "remove", "value": [nodeIds]}`.
  - Trash: `PATCH /drive/v1/trash` `{"op": "add", "value": [nodeIds], …}`, batched 50 at a time.
  - The library uploads to a folder via `parentNodeId`, with no way to upload into an album. Albums are collections, not parents.

## License

MIT, see [LICENSE](LICENSE) (unchanged from upstream [vanessa/immich-amazon-sync](https://github.com/vanessa/immich-amazon-sync)).
