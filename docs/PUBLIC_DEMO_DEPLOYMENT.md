# Public demo deployment

This procedure publishes the reviewed historical replay at
<https://retail.nightstrike.cloud>. Public availability does not approve the model for purchasing,
inventory or other operational decisions.

## Runtime decision

The public service intentionally uses `demo/demo_snapshot.json`, not PostgreSQL. The repository and
CI still demonstrate PostgreSQL migration, idempotent seeding and persistence, while the Internet
runtime has no database, credentials, mutable volume or write route.

The reviewed snapshot contains six historical forecast runs, 1,680 forecasts and 1,680 monitoring
rows. It contains dates, SKU codes and derived forecast evidence; it contains no customer,
invoice, address, email or other private fields. Startup fails closed unless its SHA-256 is:

```text
6a1e418049eb2a2c5094be44c6cf722452a2d9c471ba2a230c5c9f0488f4caad
```

## Architecture and isolation

```text
Internet :443
  -> Nginx TLS, method/rate/connection limits and security headers
  -> 127.0.0.1:8200
  -> one non-root, read-only Docker container
  -> immutable snapshot loaded once in memory
```

The public Compose manifest enforces:

- UID/GID `10001:10001`;
- read-only root filesystem and a small, non-executable `/tmp`;
- all Linux capabilities dropped and `no-new-privileges` enabled;
- 0.75 CPU, 512 MiB memory, 64 PIDs and bounded log rotation;
- a dedicated Docker bridge network and loopback-only host publication;
- Swagger/OpenAPI disabled and an explicit trusted-host list;
- no PostgreSQL dependency or password.

## Local validation

```bash
APP_VERSION=1.0.1 IMAGE_TAG=1.0.1-local RETAIL_PORT=18081 \
ALLOWED_HOSTS=127.0.0.1,localhost \
docker compose -f deploy/compose.public.yml \
  up --build --detach --wait --wait-timeout 180

python3 scripts/smoke_public_demo.py http://127.0.0.1:18081
docker compose -f deploy/compose.public.yml down --remove-orphans
```

The smoke test verifies the no-go copy, strict first-party assets, hidden API documentation, exact
`1680/1680` evidence counts, one coherent 14-horizon slice, read-only methods and security headers.

## DNS and TLS bootstrap

Create one DNS A record:

```text
retail.nightstrike.cloud -> 72.60.61.126
```

Use a short TTL during the initial publication. On the VPS, install the bootstrap virtual host as
`/etc/nginx/sites-available/retail.nightstrike.cloud.conf`, enable that exact file and validate
before reloading Nginx:

```bash
install -d -m 0755 /var/www/letsencrypt
install -m 0644 deploy/nginx/retail.nightstrike.cloud.bootstrap.conf \
  /etc/nginx/sites-available/retail.nightstrike.cloud.conf
enabled=/etc/nginx/sites-enabled/retail.nightstrike.cloud.conf
target=/etc/nginx/sites-available/retail.nightstrike.cloud.conf
if test -L "$enabled"; then
  test "$(readlink -f "$enabled")" = "$target"
else
  test ! -e "$enabled"
  ln -s "$target" "$enabled"
fi
nginx -t
systemctl reload nginx
```

After public DNS resolves, issue a webroot certificate without allowing Certbot to rewrite Nginx:

```bash
certbot certonly \
  --webroot \
  --webroot-path /var/www/letsencrypt \
  --domain retail.nightstrike.cloud \
  --email cvarvergara@gmail.com \
  --agree-tos \
  --non-interactive
```

Replace the bootstrap file with `deploy/nginx/retail.nightstrike.cloud.conf`, run `nginx -t` again
and reload only after it passes. Existing virtual hosts and containers must not be modified.

## Immutable release

Create an exact checkout beneath `/opt/retail-demand-forecasting/releases`:

```bash
release=/opt/retail-demand-forecasting/releases/1.0.1-<full-commit>
git clone --filter=blob:none \
  https://github.com/xSkyLiN3/retail-demand-forecasting.git "$release"
git -C "$release" checkout --detach <full-commit>
"$release/deploy/vps-release.sh"
```

`vps-release.sh` builds a canary on loopback port `8201`, runs the full smoke test, verifies the
container user, health and read-only filesystem, removes the canary, promotes the same image on
`8200`, repeats the smoke test and updates `current` atomically. If production verification fails,
it attempts to restore the previously active release and leaves `current` unchanged.

## Public verification

```bash
python3 scripts/smoke_public_demo.py https://retail.nightstrike.cloud
curl -fsS https://retail.nightstrike.cloud/health
```

Also verify that:

- HTTP redirects to HTTPS;
- `POST /api/forecasts` is rejected;
- `/docs` and `/openapi.json` return `404`;
- only ports 80 and 443 are externally reachable;
- the dashboard shows `NO-GO`, `77.02%` coverage and the frozen `85%` minimum;
- Predictive, RutaCuadrilla, the portfolio and unrelated VPS services remain healthy.

## Rollback

Resolve the exact previous release path before acting. Never select it through a glob or delete
release directories as part of a rollback.

```bash
previous=/opt/retail-demand-forecasting/releases/<exact-previous-release>
previous_version="$(python3 -c \
  'import pathlib,tomllib; print(tomllib.loads(pathlib.Path("'$previous'/pyproject.toml").read_text())["project"]["version"])')"
previous_commit="$(git -C "$previous" rev-parse HEAD)"
previous_image_tag="${previous_version}-${previous_commit:0:12}"

APP_VERSION="$previous_version" IMAGE_TAG="$previous_image_tag" RETAIL_PORT=8200 \
ALLOWED_HOSTS=retail.nightstrike.cloud,localhost,127.0.0.1 \
docker compose -p retail-forecasting \
  -f "$previous/deploy/compose.public.yml" \
  up --detach --wait --wait-timeout 180 --no-build

python3 "$previous/scripts/smoke_public_demo.py" http://127.0.0.1:8200
```

Only after the rollback smoke passes should `current` be moved atomically to that exact release.
