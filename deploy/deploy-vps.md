# VPS Deployment

The repository is small. The large data is under `data/` and should be transferred separately.

## What to Upload

Do not zip and upload the whole project.

Use this split:

- Code: `backend/`, `frontend/`, `requirements.txt`, `Dockerfile.prod`, `docker-compose.prod.yml`, `.dockerignore`
- Database: `pg_dump` / `pg_restore`
- Large assets: `data/images`, `data/images_jp`, and optionally `data/deck_json_exports` via `rsync`

Current local data shape:

- `data/images`: Chinese card images
- `data/images_jp`: Japanese card images
- `data/deck_json_exports`: generated exports
- `data/pgdata`: local Postgres data directory, do not copy directly unless containers are stopped and Postgres versions match

## Prepare VPS

```bash
sudo apt update
sudo apt install -y docker.io docker-compose-plugin rsync
sudo systemctl enable --now docker
sudo docker network create proxy
```

## Upload Code

From the local project root, prefer `rsync`:

```bash
rsync -avz --delete \
  --exclude data \
  --exclude .env \
  --exclude .git \
  --exclude "__pycache__" \
  ./ user@YOUR_VPS:/opt/ptcg/
```

For Windows, you can use `deploy/sync-code-to-vps.bat`. It syncs code only, excludes `data/` and local secrets, then rebuilds `ptcg-web` on the VPS.

On the VPS:

```bash
cd /opt/ptcg
cp .env.production.example .env.production
nano .env.production
```

Generate secrets:

```bash
python3 - <<'PY'
import secrets
print("SECRET_KEY=" + secrets.token_urlsafe(64))
print("SECURITY_PASSWORD_SALT=" + secrets.token_urlsafe(32))
print("POSTGRES_PASSWORD=" + secrets.token_urlsafe(32))
PY
```

## Transfer Images and Exports

These are the big folders. Use resumable `rsync`, not FTP zip upload:

```bash
rsync -avz --partial --append-verify --info=progress2 data/images/ user@YOUR_VPS:/opt/ptcg/data/images/
rsync -avz --partial --append-verify --info=progress2 data/images_jp/ user@YOUR_VPS:/opt/ptcg/data/images_jp/
rsync -avz --partial --append-verify --info=progress2 data/deck_json_exports/ user@YOUR_VPS:/opt/ptcg/data/deck_json_exports/
```

If the VPS storage is limited, `data/deck_json_exports` can be skipped and regenerated.

## Transfer Postgres Data

Create a dump from the local Docker database:

```powershell
docker compose exec -T db pg_dump -U ptcg -d ptcg_db -Fc > data/ptcg_db.dump
```

Upload it:

```bash
rsync -avz --partial --info=progress2 data/ptcg_db.dump user@YOUR_VPS:/opt/ptcg/data/
```

Start production containers on the VPS. Compose variable interpolation needs `--env-file`:

```bash
cd /opt/ptcg
docker compose --env-file .env.production -f docker-compose.prod.yml up -d --build db
docker compose --env-file .env.production -f docker-compose.prod.yml up -d --build ptcg-web
```

Restore into the VPS database:

```bash
docker compose --env-file .env.production -f docker-compose.prod.yml exec -T db pg_restore \
  -U "$POSTGRES_USER" \
  -d "$POSTGRES_DB" \
  --clean --if-exists \
  /dev/stdin < data/ptcg_db.dump
```

If the shell does not expand `.env.production`, run:

```bash
source .env.production
```

## Run

Local VPS-only test without public proxy:

```bash
docker compose --env-file .env.production -f docker-compose.prod.yml -f docker-compose.prod.local.yml up -d --build
curl http://127.0.0.1:5000/api/decks/japanese/list?page=1
```

For HTTP-only local tests, set `SESSION_COOKIE_SECURE=false` in `.env.production`. Use `true` again behind HTTPS.

To validate the compose file locally without creating `.env.production`, run:

```bash
ENV_FILE=.env.production.example docker compose --env-file .env.production.example -f docker-compose.prod.yml -f docker-compose.prod.local.yml config
```

Public proxy setup depends on your reverse proxy. With Caddy, put `deploy/Caddyfile.example` into your Caddy config and replace `example.com`.

## Checks

```bash
docker compose --env-file .env.production -f docker-compose.prod.yml ps
docker compose --env-file .env.production -f docker-compose.prod.yml logs --tail=100 ptcg-web
curl -I https://YOUR_DOMAIN/
curl https://YOUR_DOMAIN/api/decks/japanese/list?page=1
```

## Notes

- Rotate the local `.env` secrets before deployment if they were ever shared or committed.
- Do not expose Postgres to the public internet.
- Do not expose Flask port `5000` publicly unless it is bound to `127.0.0.1`.
- Back up Postgres with `pg_dump -Fc`; do not rely only on copying `data/pgdata`.
- `ENABLE_JP_DECK_AUTO_UPDATE` and `ENABLE_LIMITLESS_AUTO_UPDATE` default to `false` in production compose to avoid duplicate crawler threads. Turn them on only if this web container should run the daily deck-data jobs.
- If upload time is tight, deploy code + database first, then sync `data/images` and `data/images_jp` in the background. Missing local images will still fall back where the app has remote image URLs.
