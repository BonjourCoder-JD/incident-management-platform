# Operations — single-server public demo

How to check, back up and recover the production stack (`deploy/compose.prod.yaml`) on its one server. Everything here uses only the stack itself: no external monitoring or paid services. Run the commands from the repository root on the server, with this shorthand:

```sh
C="docker compose -f deploy/compose.prod.yaml --env-file deploy/.env.production --profile demo"
```

`deploy/.env.production` holds the only copy of the database passwords. Keep a copy of it **off the server** (for example in a password manager): backups do not contain it, and without it a restore cannot be switched in.

## 1. Is everything working?

```sh
$C ps
```

Every long-running service has a health check; the `STATUS` column shows `healthy` or `unhealthy`.

| Service | Unhealthy when | Check by hand |
|---|---|---|
| `backend` | `/health/ready` is not 200: database down, schema not at the code's migration head, or an unsafe database role | `curl -s https://$SITE_ADDRESS/api/health/ready` |
| `worker` | its latest SLA run failed, the database was not ready, the latest cleanup failed, or no run succeeded for 5 minutes | `$C exec worker python -m app.cli health-check worker --max-age 300` |
| `demo-reset` | the latest daily reset failed or was refused (stays unhealthy until a reset succeeds) | `$C exec demo-reset python -m app.cli health-check demo-reset --max-age 90000` |
| `backup` | the latest backup failed, or none succeeded for 26 hours | `$C exec backup backup.sh health` |

**These health checks are not alerts.** Docker records the state on the server; nobody is notified, and if the whole server is down nothing reports anything. Until external monitoring exists, look at `$C ps` regularly. *Phase F deployment task:* add a free external uptime check of `https://$SITE_ADDRESS/api/health/ready` (every 5 minutes, e-mail on failure) from outside the server; it covers the server, Caddy, the API and the database, but not the worker, reset or backups, which still need `$C ps`.

## 2. Logs

All services log one JSON object per line (`time`, `level`, `logger`, `message`, and in the API `request_id`). Docker keeps at most 5 × 10 MB per container.

```sh
$C logs --since 24h | grep '"level":"ERROR"'          # everything that failed today
$C logs backend | grep 0f6c2b1e...                     # one request, by its X-Request-ID
```

Every API response carries an `X-Request-ID` header, and a 500 response names it in its body, so a visitor's report can be matched to the log line. The request log records only the method, the route template (for example `/incidents/{incident_id}`), the status and the duration: never bodies, query strings, cookies, tokens, passwords or incident text. SQL parameters and PostgreSQL `DETAIL` lines are withheld from error logs.

## 3. Backups

The `backup` service dumps `POSTGRES_DB` every day at 03:30 UTC (`BACKUP_TIME_UTC`), and once at start-up if no dump is under 24 hours old, into the `backups` volume. Each dump is a PostgreSQL custom-format archive with a `.sha256` file, readable only by the backup user (directory 0700, files 0600); the newest 7 are kept (`BACKUP_KEEP`). Dumps contain password hashes: treat them like the database.

```sh
$C exec backup ls -l /backups                          # list
$C exec backup backup.sh once                          # back up now
```

**Backups currently live on the same server**, in a Docker volume on the same disk as the database. They protect against bad data and mistakes, not against losing the server or its disk. Copy them off the server (below).

**What the checksum proves.** The `.sha256` file detects accidental damage: a truncated copy, a bad disk, an interrupted transfer. It is stored next to the dump, so it does **not** prove that nobody changed the archive on purpose: whoever can change the dump can change its checksum too. The encrypted off-server copy below is tamper-evident; the copy on the server is only as trustworthy as the server.

### Encrypted off-server copy (weekly)

Copies are encrypted **on the server, before transfer**, with [age](https://age-encryption.org) to a public key. The server holds only the public key, so neither the server nor the transfer can read old copies, and no secret is stored on the server or in the repository.

Once, on your own machine (keep `imp-backup-key.txt` private, for example also in your password manager; never put it on the server or in the repository):

```sh
age-keygen -o imp-backup-key.txt          # prints the public key: age1...
```

Once, on the server (`sudo apt install age`), save that public key (it is not secret) outside the repository:

```sh
echo 'age1...' > ~/imp-backup-recipient.txt
```

Weekly, from your own machine: `backup.sh export` checks the newest dump against its checksum and streams it with its `.sha256` as a tar; the server encrypts the stream before it leaves:

```sh
ssh server 'cd incident-management-platform && docker compose -f deploy/compose.prod.yaml --env-file deploy/.env.production exec -T backup backup.sh export | age -R ~/imp-backup-recipient.txt' > imp-backup-20261116.tar.age
age -d -i imp-backup-key.txt imp-backup-20261116.tar.age | tar -tvf -     # check: one .dump and its .sha256
```

Keep the `.tar.age` files somewhere you control (your machine plus one other place). Decryption fails if a copy was changed in any way (age authenticates every chunk). Anyone with the public key could create a new encrypted file, so keep copies only where you put them yourself. To use one: `age -d -i imp-backup-key.txt imp-backup-20261116.tar.age > imp-backup.tar` (section 5 B).

## 4. Restore drill (harmless; monthly)

A restore always goes into a **new** database named `<database>_restore_<time>`. `restore.sh` refuses any other name and any database that already exists, so it can never write over the live, development or test databases. It checks the SHA-256 file first, restores in one transaction, and removes the database it created if anything fails.

```sh
$C run --rm --no-deps backup restore.sh incidents_demo-20261116T033000Z.dump
#   ... restored_database=incidents_demo_restore_20261116190000
$C run --rm --no-deps migrate python -m app.cli verify-restore --database incidents_demo_restore_20261116190000
#   verify-restore: consistent; users 5, incidents 16, ...
$C exec db sh -c 'dropdb -U "$POSTGRES_USER" incidents_demo_restore_20261116190000'   # drill only: remove the copy
```

`verify-restore` checks that the schema is at this code's migration head, that every append-only table still has its protecting trigger, that the stored SLA figures match the audit trail and that no rows refer to missing users or incidents, and prints row counts.

## 5. Recovery

**For the public demo, the quickest recovery is often no restore at all:** the demo data is recreated every day, so after any database problem `$C up -d` followed by `$C exec demo-reset python -m app.cli demo-reset` (and `create-admin` for the private administrator, if the database was lost) gives a working demo in a minute. Restore a backup when the administrator account or anything else outside the demo data must come back.

### A. Bad data, database still running

1. Restore the chosen backup into a new database and verify it (section 4, without the `dropdb`).
2. Switch it in. The services that connect to the database must be stopped, or the rename is refused:

   ```sh
   $C stop backend worker demo-reset backup
   $C exec db sh -c 'psql -U "$POSTGRES_USER" -d postgres -v ON_ERROR_STOP=1 \
     -c "ALTER DATABASE incidents_demo RENAME TO incidents_demo_replaced_20261116" \
     -c "ALTER DATABASE incidents_demo_restore_20261116190000 RENAME TO incidents_demo"'
   $C run --rm migrate          # migrations (none pending) and the runtime role's privileges on the restored copy
   $C up -d
   $C ps                        # backend healthy; sign in and check the data
   ```

3. Keep `incidents_demo_replaced_<date>` until you are sure, then `$C exec db sh -c 'dropdb -U "$POSTGRES_USER" incidents_demo_replaced_20261116'`.

### B. Database volume lost (or a new server)

1. Clone the repository at the deployed commit and put back `deploy/.env.production` (from your off-server copy).
2. Start only the database (it creates an empty `POSTGRES_DB`) and the backup tools, and put the dump back into the `backups` volume if that was lost too:

   ```sh
   $C up -d db
   $C run --rm --no-deps -T backup sh -c 'cd /backups && tar -xf -' < imp-backup.tar
   ```

3. Restore and verify (section 4), then switch in as in A, step 2 (the empty database is the one renamed to `_replaced_<date>`). `migrate` recreates the runtime role, so nothing else is needed.

**What a restore loses:** everything after the backup (at most a day), and all sessions (everyone signs in again). Rate-limit counters restart.
