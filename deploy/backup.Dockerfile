# Backup and restore tools (Milestone 6 Phase D): the PostgreSQL 17 client tools (the server's major
# version) and two shell scripts, as an unprivileged user. Build from the repository root:
#   docker build -f deploy/backup.Dockerfile -t imp-backup:prod .
FROM alpine:3.22
RUN apk add --no-cache postgresql17-client \
    && adduser -D -H -u 10003 backup \
    && install -d -m 700 -o backup -g backup /backups
COPY --chmod=755 deploy/backup.sh deploy/restore.sh /usr/local/bin/
USER backup
CMD ["backup.sh", "daily"]
