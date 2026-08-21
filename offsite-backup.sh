set -euo pipefail
cd /root/freedom-v2

# 21/8: il nome era hardcoded su freedom_episodic mentre la viva e' v3.
coll=$(grep -oP 'collection:[[:space:]]*\K\S+' config/config.yaml)
test -n "$coll"
ts=$(date +%Y%m%d_%H%M)
mkdir -p offsite-staging

docker compose exec -T postgres pg_dump -U freedom freedom | gzip > offsite-staging/pg_$ts.sql.gz

json=$(curl -s -X POST http://127.0.0.1:6333/collections/$coll/snapshots)
name=$(echo "$json" | jq -r .result.name)
attesa=$(echo "$json" | jq -r .result.size)
test "$name" != null
curl -s http://127.0.0.1:6333/collections/$coll/snapshots/$name -o offsite-staging/qdrant_${coll}_$ts.snapshot

test -s offsite-staging/pg_$ts.sql.gz
reale=$(stat -c%s offsite-staging/qdrant_${coll}_$ts.snapshot)
test "$reale" -eq "$attesa"

rclone copy offsite-staging drive:freedom-backups/$ts
find offsite-staging -type f -mtime +7 -delete

echo "backup ok: $ts coll=$coll pg=$(stat -c%s offsite-staging/pg_$ts.sql.gz)B qdrant=${reale}B"
