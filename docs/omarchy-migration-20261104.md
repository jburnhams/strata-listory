# Move Strata Listory to the Omarchy server — 4 November 2026

## Shutdown and handoff

The old machine's **`strata-listory.service` is stopped and disabled**. Its only
backend listened on `127.0.0.1:8364`; do not start a second server on port 8360.
The existing Caddy route was `/strata-listory/`, behind the machine's separate
authentication service. Caddy itself and the other applications were left alone.

The transfer bundle is on the data mount:

```
/home/jburnhams/data/strata-listory/migration-20261104/
```

That mount is `//server/Omarchy`. On the destination server, use the actual local
directory backing that share; it need not have the same mount path. The bundle
contains private catalogue information and provider credentials. Keep it private.

| File | Purpose |
| --- | --- |
| `catalogue.db` | Consistent SQLite backup, including all catalogue entries, settings stored in the database, ingest evidence/decisions, shortlists, policy, jobs and read models |
| `support-data.tar.gz` | Files under repository `data/ingest/` and `data/evidence/`, offline matching experiments/reports without their virtual environment, bulk identification artifacts without the old database snapshot, and the Lords of Finance repair record |
| `private-config.tar.gz` | App settings, provider credential files, captured app environment, and reference copies of the old service/drop-ins and Caddy snippet |
| `source.bundle` | Git repository bundle containing the feature branch and this migration guide; usable without GitHub |
| `snapshot.json` | Snapshot time, schema, table counts, queue states and evidence-file audit |
| `SHA256SUMS` | Checksums for the transfer files, checked again after copying to the mount |
| `README.md` | Copy of this guide, readable without restoring the repository |

Snapshot: schema **12**, **5,706 books**, **1,745 ingest runs**, **85,395 events**,
**3,021 read models**. All **15,563** distinct archived provider-file paths exist.
The copied database passes SQLite's full integrity check. Job states are 549
done, five failed and one queued; no jobs remain running. The queued job is an
ingest batch selecting 113 entries. Shutdown can mark interrupted work failed;
use explicit Retry for failures after the new installation is ready.

The database includes five proposed processing plans, one done plan, and one
processed result (book #171, *Great Expectations*). It retains that history even
though generated outputs are deliberately omitted from the transfer.

## What matters beyond the database

**Keep archived provider evidence.** Saved approvals and plans verify hashes of
files in `data/ingest/`; these are historical evidence, not interchangeable HTTP
cache entries. Downloading current metadata again may produce different bytes
and require refreshed reviews. The support archive preserves those files at
their original relative paths, along with saved fingerprints/analysis artifacts.

**Recreate the audio copy at the same absolute path.** The user confirmed that
`audio-local` was copied from the audio mount and can be copied again. All
catalogued source paths live below `/home/jburnhams/audio-local`. The simplest
restore uses the same username/home, repository path and source path:

```
/home/jburnhams/code/github/strata-listory
/home/jburnhams/audio-local
/home/jburnhams/audiobooks                       # generated outputs
```

The database contains absolute paths inside inventories, member signatures,
source groups, provider manifests and processing plans. `./app use-library`
changes catalogue book paths only; it does not rewrite all those signed records.
If the home or repository location changes, provide compatible old paths or
perform a deliberate migration and fresh ingest/review. Do not mass-replace
strings in the database and assume existing consent is still valid.

Preserve source filenames, directory layout, audio bytes and metadata sidecars.
Preserving timestamps while copying avoids unnecessary changes; saved source
hashes remain the authority. On the server, after substituting its audio-mount
path, for example:

```sh
mkdir -p "$HOME/audio-local"
rsync -rt --info=progress2 /actual/path/to/Audio/ "$HOME/audio-local/"
```

**Recreate these items rather than copying them:** `.venv`, `web/node_modules`,
the model experiment's virtual environment, Open Library dumps/index,
downloaded Diamond/AP-BWE code/model/cache environments, covers, listening
previews, and generated audiobook outputs. The Open Library archive was about
22 GB and the old accelerator environment about 16 GB; neither is included.
Old generated-output read models can refer to missing files until outputs are
rebuilt. Saved review cards and trained-model reports are retained.

## Code and dependencies

All application changes are in [PR #13](https://github.com/jburnhams/strata-listory/pull/13),
branch `codex/book-match-review-and-grouping`. Use that branch, or main after it
contains the PR, rather than the older main commit. The feature code was at
`bc8394838686c8ccff48f26efc4057559fe840dc` before the migration documentation.

The changes include offline model training from trusted approvals, candidate
audit features, automatic-approval and shortlist review pages, firm Approve and
Reject decisions, ten-card batches with next-page preloading, bounded additional
Open Library edition evidence, and narrator-aware chapter grouping. Review
fixes bind cards to each shortlist-import revision and exclude revoked/rejected
approvals from training. Models rank shortlists; they do not write approvals.

Lords of Finance is now one current work with 30 chapter files, by Liaquat Ahamed,
narrated by Stephen Hoye. Historical fragment runs and the human decision remain
available; no old recording/quality approvals were transferred to the merged run.

The final local backend suite passed **833 tests at 95.58% coverage**. The latest
code also passed all five cross-platform Python unit checks, both web checks,
and Linux/Windows integration checks. The remaining macOS integration check
encountered GitHub runner-capacity problems; consult the PR for its latest state.

Install Python 3.11+ (CI covers 3.11–3.13), Node/npm for the web build, and the
native audio tools `ffmpeg`, `ffprobe`, `mp3gain` and `fpcalc` (Chromaprint).
The old machine's `mp3gain` and `fpcalc` were user-local installations; those
binaries are not part of this transfer. See [audio-tools.md](audio-tools.md).

From a fresh checkout:

```sh
scripts/setup_env.sh
.venv/bin/python -m pip install -r requirements-audio.txt
# Only needed to retrain the offline models:
# .venv/bin/python -m pip install -r requirements-model.txt
```

The setup script builds the frontend when npm is available. Otherwise run
`npm ci && npm run build` in `web/` before serving the app. Compiled frontend
assets and Python environments are regenerated rather than transferred.

## Restore files and configuration before starting the server

Set `LISTORY_MIGRATION` to the bundle's actual path on the destination server:

```sh
export LISTORY_MIGRATION=/actual/local/path/to/strata-listory/migration-20261104
(cd "$LISTORY_MIGRATION" && sha256sum -c SHA256SUMS)

mkdir -p "$HOME/code/github"
git clone -b codex/book-match-review-and-grouping \
  "$LISTORY_MIGRATION/source.bundle" "$HOME/code/github/strata-listory"
cd "$HOME/code/github/strata-listory"
git remote set-url origin https://github.com/jburnhams/strata-listory.git

# In this fresh checkout, with no existing database to overwrite:
mkdir -p data
cp "$LISTORY_MIGRATION/catalogue.db" data/catalogue.db
tar -xzf "$LISTORY_MIGRATION/support-data.tar.gz" -C "$PWD"

LISTORY_CONFIG_RESTORE=$(mktemp -d)
chmod 700 "$LISTORY_CONFIG_RESTORE"
tar -xzf "$LISTORY_MIGRATION/private-config.tar.gz" -C "$LISTORY_CONFIG_RESTORE"
install -d -m 700 "$HOME/.config/strata-listory/app-home"
install -m 600 "$LISTORY_CONFIG_RESTORE/home/.app/settings.json" \
  "$HOME/.config/strata-listory/app-home/settings.json"
cp -a "$LISTORY_CONFIG_RESTORE/home/.config/strata-listory/." \
  "$HOME/.config/strata-listory/"
```

Use a dedicated `APP_HOME` so this app does not overwrite another project's
generic `~/.app/settings.json`. Merge any existing target configuration instead
of replacing it blindly. Remove the temporary extracted configuration once the
keys have been installed. Credential contents must never go into Git or logs.

The captured persistent settings were:

| Setting | Old value |
| --- | --- |
| `library_dir` | `/home/jburnhams/audio-local` |
| `backup_dir` | `/home/jburnhams/data/strata-listory/backups` |
| `data_dir` | Unset: repository `data/` |
| `final_dir` | Unset: `~/audiobooks` |
| `audible_region` | Unset: `uk` |
| `match_providers` | Unset: adapter defaults; ingest's provider policy is separately saved in SQLite |

Provider files are `~/.config/strata-listory/audiobookdb.key` and `hardcover.key`.
An existing `goodreads.cred` is also retained, although no Goodreads adapter is
implemented. No application key was set in the service/manager environment.
Optional overrides are `APP_ADB_KEY`, `APP_HARDCOVER_KEY` and
`APP_GOOGLE_BOOKS_KEY`; Google Books is optional and was not configured here.
There is no required dotenv file or additional application database password.

The service's app environment was exactly `APP_LIBRARY`, `APP_WORKERS=1`, and
`APP_JOB_TIMEOUT_SECONDS=1800`; its captured values are in `runtime.env` inside
the private archive. The example below makes data/output/backup locations
explicit. The old backup setting can be replaced later with the appropriate
server backup location. **Do not run the SQLite database from an SMB share.**

## Service on the destination server

Create `~/.config/systemd/user/strata-listory.service` after installing the audio
tools, copying the sources and restoring the support archive:

```ini
[Unit]
Description=Strata Listory audiobook catalogue
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
WorkingDirectory=%h/code/github/strata-listory
Environment=PATH=%h/.local/bin:/usr/local/bin:/usr/bin
Environment=APP_HOME=%h/.config/strata-listory/app-home
Environment=APP_DATA=%h/code/github/strata-listory/data
Environment=APP_LIBRARY=%h/audio-local
Environment=APP_BACKUP=%h/code/github/strata-listory/data/backups
Environment=APP_FINAL=%h/audiobooks
Environment=APP_WORKERS=1
Environment=APP_JOB_TIMEOUT_SECONDS=1800
ExecStart=%h/code/github/strata-listory/app serve --host 127.0.0.1 --port 8364
Restart=on-failure
RestartSec=30
MemoryHigh=3G
MemoryMax=4G
MemorySwapMax=0
KillMode=control-group

[Install]
WantedBy=default.target
```

The old `OnFailure=notify-failure@%n.service` integration depends on another
machine-local unit; its reference copy is retained, but it is intentionally not
required by this standalone template. Adjust resource limits to the new server
if needed. Keep one service; development `npm run dev` is unnecessary in normal
operation. For startup without an interactive login, check user lingering with
`loginctl show-user "$USER" -p Linger` and enable it through the server's normal
administration process if required.

Before starting, export the same APP_HOME/APP_DATA/APP_LIBRARY/APP_BACKUP/APP_FINAL
values in the shell when using the CLI, then run `./app doctor --audio`,
`./app providers`, and `./app list --json`. Compare the table counts with
`snapshot.json`, and verify the source paths exist. Saved decisions still perform
their normal byte/hash checks; do not force past a failed source check.

When all files and tools are ready:

```sh
systemctl --user daemon-reload
systemctl --user enable --now strata-listory.service
systemctl --user status strata-listory.service
curl --fail 'http://127.0.0.1:8364/api/match-reviews?mode=automatic'
```

Starting the service starts its durable worker and may claim the retained
113-entry ingest job immediately. Automatic book approval is enabled in the
saved policy; automatic quality approval and automatic output processing are
disabled. Leave the old machine's service stopped and disabled.

Restore the Caddy path handler from the private archive's `reference/` directory
into the server's existing proxy configuration. It strips `/strata-listory/`
before forwarding to `127.0.0.1:8364`. The frontend uses that relative base.
The old host's URL/IP is not portable. Preserve the server's authenticated outer
route: the application has no independent login, and its loopback listener is
intended to sit behind the existing authentication proxy. Migrating that separate
authentication service is outside the application database/configuration bundle.

Verify the new host's `/strata-listory/#/automatic-approvals` and
`/strata-listory/#/match-shortlist` routes,
their ten-card batches, and approval/rejection jobs. Confirm Lords of Finance
still appears as one current 30-file work. Missing covers/previews can be rebuilt.

## Rebuild optional data and outputs

Re-download Open Library's authors/works/ratings gzip dumps and run
`./app openlibrary-import`; see [openlibrary-offline.md](openlibrary-offline.md).
Until its local index is rebuilt, the adapter can fall back to the online API.
It is still one source family. Re-downloading cannot substitute for the archived
provider responses already preserved with approvals.

Diamond enhancement is optional and needs a separate runtime/model setup.
The old ROCm environment is not portable to different hardware. Configure
`STRATA_DIAMOND_CODE`, `STRATA_DIAMOND_MODEL`, `STRATA_DIAMOND_PYTHON`,
`STRATA_DIAMOND_DEVICE` and `STRATA_DIAMOND_THREADS` if enhancement is needed;
see [ingest-pipeline.md](ingest-pipeline.md#confirmed-medium-quality-enhancement).
Basic catalogue/review functionality does not need those downloads.

Generated outputs were intentionally left behind. The completed Great
Expectations row will not automatically reprocess merely because its files are
absent. Start a fresh run with `./app ingest-run 171 --limit 1`, then follow its
book, recording and quality review gates to obtain a new eligible plan. The old
run's `processed` state cannot be used directly as an eligible ingest plan.
After those gates, name the book explicitly to inspect the plan
(`./app process 171 --plan`) and, when appropriate, regenerate it
(`./app process 171`). Keep normal ingest consent checks; explicitly naming a
book is a direct processing action.
Five other plans remain proposed and require normal review/approval. Rebuild
missing listening previews through the app; retained ready-state records alone
do not recreate their audio assets.

The immutable handoff snapshot can be restored again if needed. Nothing in this
move deletes the old local database, audio copy, environments or output files.
