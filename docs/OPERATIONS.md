# Operations — single-server public demo

How to check, back up, deploy, roll back and recover the production stack (`deploy/compose.prod.yaml`) on its one server. Everything here uses only the stack itself and free GitHub features: no paid services. Run the commands from the repository root on the server, with this shorthand (`deploy/.deploy/images.env` holds the digest-pinned images of the deployed version; `deploy/deploy.sh` writes it):

```sh
C="docker compose -p imp-prod -f deploy/compose.prod.yaml --env-file deploy/.env.production --env-file deploy/.deploy/images.env --profile demo"
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
ssh server 'cd incident-management-platform && docker compose -p imp-prod -f deploy/compose.prod.yaml --env-file deploy/.env.production --env-file deploy/.deploy/images.env exec -T backup backup.sh export | age -R ~/imp-backup-recipient.txt' > imp-backup-20261116.tar.age
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

1. Prepare the server (section 6, Deployment setup) with `deploy/.env.production` from your off-server copy.
2. Deploy the version the backup came from (section 6), which starts the stack on an empty database, then put the dump back into the `backups` volume if that was lost too:

   ```sh
   git checkout --detach <commit> && bash deploy/deploy.sh <commit>
   $C run --rm --no-deps -T backup sh -c 'cd /backups && tar -xf -' < imp-backup.tar
   ```

3. Restore and verify (section 4), then switch in as in A, step 2 (the empty database is the one renamed to `_replaced_<date>`). `migrate` recreates the runtime role, so nothing else is needed.

**What a restore loses:** everything after the backup (at most a day), and all sessions (everyone signs in again). Rate-limit counters restart.

## 6. Releases and deployment

**How a version becomes deployable.** Every push to `main` runs *CI* and *Security scans*. The *Release images* workflow publishes a commit only when both succeeded for exactly that commit (`deploy/release_gate.py`; a failed, cancelled or skipped run refuses). It then builds the three production images natively on a GitHub **linux/arm64** runner (the server's architecture), scans them (Trivy), deploys them on that runner with `deploy/deploy.sh`, runs `deploy/smoke.sh` and the rollback regression test (`deploy/tests/rollback_regression.sh`), and only then publishes them to GitHub Container Registry; a commit's published images are never replaced:

```
ghcr.io/bonjourcoder-jd/incident-management-platform/backend:sha-<commit>
ghcr.io/bonjourcoder-jd/incident-management-platform/web:sha-<commit>
ghcr.io/bonjourcoder-jd/incident-management-platform/backup:sha-<commit>
```

The workflow summary lists each image's digest. A version is always a full 40-character commit SHA on `main`.

**Nothing deploys automatically.** The *Deploy* workflow runs only when started by hand (Actions > Deploy > Run workflow, with the version); no other workflow can start it (the release has no permission to). Its first job refuses unless the `production` environment exists with a required reviewer and a deployment-branch restriction, because GitHub would otherwise create a missing environment on first use without protection; then a required reviewer must approve the run. It checks that the version is on `main` and published, connects over SSH with a pinned host key, checks out the version on the server, runs `deploy/deploy.sh <version>`, and then runs the smoke test again from GitHub against the public address.

### Deployment setup (Phase F; once)

On GitHub (repository settings; nothing here is stored in the repository):

1. **Packages:** after the first release, set the three packages (`backend`, `web`, `backup`) to **public** (Package settings > Change visibility), so the server and the Deploy workflow can pull them without a credential.
2. **Environment `production`:** Settings > Environments > New environment. Add yourself as a **required reviewer**, and limit deployment branches to `main`.
3. **Environment secrets** (in `production`): `DEPLOY_HOST` (the server's address), `DEPLOY_USER` (an unprivileged user in the `docker` group), `DEPLOY_SSH_KEY` (a private key used only for deployments), `DEPLOY_KNOWN_HOSTS` (the server's host key line, from `ssh-keyscan -t ed25519 <host>`, checked against the server's own `/etc/ssh/ssh_host_ed25519_key.pub`).
4. **Environment variables:** `DEPLOY_PATH` (the repository's absolute path on the server) and `DEPLOY_SITE_ADDRESS` (the public hostname).

On the server: install Docker and git, `git clone https://github.com/BonjourCoder-JD/incident-management-platform.git` (public; no credentials), create `deploy/.env.production` from the example (`chmod 600`), and add the deployment key's public half to the deploy user's `~/.ssh/authorized_keys`.

### Deploying by hand (the same steps the workflow runs)

```sh
git fetch origin && git checkout --detach <commit> && bash deploy/deploy.sh <commit>
bash deploy/deploy.sh --status        # deployed version, previous version, database migration
```

`deploy.sh` pulls the version's images and pins them by digest, refuses if the database is at a migration the version does not know, backs up the database, starts the version (the `migrate` service applies new migrations), and runs the smoke test: readiness, the web app and its security headers, the dashboard page, a refused wrong password, sign-in, the session, the dashboard summary, the incident list, sign-out. In the public demo it uses the published demo responder; otherwise set `SMOKE_EMAIL` and `SMOKE_PASSWORD` in the environment. Exit status 0 deployed, 1 failed, 2 refused (nothing changed). Every version's pinned images are kept in `deploy/.deploy/images-<commit>.env`.

## 7. Rollback

**Rolling back an image never undoes a database migration.** Each version's code accepts only the migrations it knows; the backend reports 503 `unknown_database_revision` on a newer database. `deploy.sh` therefore checks before switching.

**A failed deployment without a migration** rolls back by itself: `deploy.sh` checks out the previous commit (so the compose file and scripts match), restarts the previous images, smoke-tests them, and exits 1 saying so. It runs from a private copy of itself, so the checkout cannot change the running script.

**Rolling back by choice** is a deployment of an earlier version (`deploy.sh --status` shows the previous one): run the Deploy workflow with that commit, or the commands in section 6. If no migration was added in between, that is all.

**After a migration** (a failed deployment that migrated, or rolling back past a migration), `deploy.sh` refuses or stops and names the **pre-deploy backup** it took. Either deploy a fixed version (roll forward), or restore that backup and then deploy the earlier version:

```sh
P=<previous commit>
CP="docker compose -p imp-prod -f deploy/compose.prod.yaml --env-file deploy/.env.production --env-file deploy/.deploy/images-$P.env --profile demo"
$C run --rm --no-deps backup restore.sh <pre-deploy backup>.dump       # -> restored_database=NAME
$CP run --rm --no-deps migrate python -m app.cli verify-restore --database NAME   # checked by the earlier version's code
$C stop backend worker demo-reset backup
$C exec db sh -c 'psql -U "$POSTGRES_USER" -d postgres -v ON_ERROR_STOP=1 \
  -c "ALTER DATABASE incidents_demo RENAME TO incidents_demo_replaced_<date>" \
  -c "ALTER DATABASE NAME RENAME TO incidents_demo"'
git checkout --detach $P && bash deploy/deploy.sh $P
```

Do **not** run the current version's `migrate` after switching (it would apply the newer migration again); `deploy.sh $P` runs the earlier version's. Changes made after the pre-deploy backup are lost.

## 8. Updating base images and actions

All base images are pinned to multi-architecture digests (`backend/Dockerfile`, `deploy/web.Dockerfile`, `deploy/backup.Dockerfile`, the `db` image in `deploy/compose.prod.yaml`), and GitHub Actions to commit SHAs. **Dependabot** proposes updates weekly as pull requests, which must pass CI and the *Security scans* workflow before merging. By hand:

```sh
docker buildx imagetools inspect python:3.13-slim --format '{{json .Manifest.Digest}}'   # new digest
# replace the @sha256:... after the tag, then confirm both architectures are present:
docker buildx imagetools inspect python:3.13-slim@sha256:<new digest> | grep -E 'linux/(amd64|arm64)'
```

**Vulnerability scanning.** The *Security scans* workflow (every change and weekly) runs `pip-audit`, `npm audit` and Trivy on the production images; HIGH and CRITICAL findings that have a fix fail it. An accepted finding goes into `.trivyignore.yaml` with a reason and an expiry date within about a month, after which the scan fails again.
