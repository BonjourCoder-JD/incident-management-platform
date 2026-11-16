# Backup and restore tools (Milestone 6 Phase D): the PostgreSQL 17 client tools (the server's major
# version) and two shell scripts, as an unprivileged user. Build from the repository root:
#   docker build -f deploy/backup.Dockerfile -t imp-backup:prod .
# Base images are pinned to multi-architecture index digests (amd64 and arm64); Dependabot proposes
# updates weekly, and docs/OPERATIONS.md (Updating base images) describes the manual procedure.
# The PostgreSQL client package comes from the Alpine 3.22 repository at build time (Alpine removes old
# package versions, so pinning one would break future builds); deployments use the published digest.
FROM alpine:3.24@sha256:f59abe656b692d901e184be3445e084786602108599d2a51d229c0f5a48c71bf
RUN apk add --no-cache postgresql17-client \
    && adduser -D -H -u 10003 backup \
    && install -d -m 700 -o backup -g backup /backups
COPY --chmod=755 deploy/backup.sh deploy/restore.sh /usr/local/bin/
USER backup
CMD ["backup.sh", "daily"]
