#!/usr/bin/env bash
set -euo pipefail
cd /root/freedom-v2
coll=
ts=$(date +%Y%m%d_%H%M)
mkdir -p offsite-staging

# Postgres dump
docker compose exec -T postgres pg_dump -U freedom freedom | gzip > offsite-staging/pg_$ts.sql.gz

# Qdrant snapshot + download
name=$(curl -s -X POST http://127.0.0.1:6333/collections/$coll/snapshots | jq -r .result.name)
curl -s http://127.0.0.1:6333/collections/$coll/snapshots/$name -o offsite-staging/qdrant_${coll}_$ts.snapshot

# sanity: file non vuoti
test -s offsite-staging/pg_$ts.sql.gz
test -s offsite-staging/qdrant_${coll}_$ts.snapshot

# ship su Drive
rclone copy offsite-staging drive:freedom-backups/$ts

# prune: locale > 7 giorni
find offsite-staging -type f -mtime +7 -delete

echo "backup ok: $ts"
