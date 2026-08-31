#!/usr/bin/env bash
set -Eeuo pipefail

base_dir=/opt/retail-demand-forecasting
releases_dir="$base_dir/releases"
current_link="$base_dir/current"
production_project=retail-forecasting
canary_project=retail-forecasting-canary
production_port=8200
canary_port=8201
public_host=retail.nightstrike.cloud
release_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd -P)"
compose_file="$release_dir/deploy/compose.public.yml"
smoke_script="$release_dir/scripts/smoke_public_demo.py"

if [[ "${EUID}" -ne 0 ]]; then
  echo "This release script must run as root on the VPS." >&2
  exit 1
fi

install -d -m 0755 "$releases_dir"
exec 9>"$base_dir/.deploy.lock"
flock -n 9 || { echo "Another retail deployment is already running." >&2; exit 1; }

if [[ "$(dirname -- "$release_dir")" != "$releases_dir" ]]; then
  echo "Release must be an exact child of $releases_dir." >&2
  exit 1
fi

for required in "$compose_file" "$smoke_script" "$release_dir/pyproject.toml"; do
  test -f "$required" || { echo "Missing release file: $required" >&2; exit 1; }
done

if git -C "$release_dir" rev-parse --is-inside-work-tree >/dev/null 2>&1; then
  test -z "$(git -C "$release_dir" status --porcelain)" || {
    echo "Release checkout is not clean." >&2
    exit 1
  }
  commit="$(git -C "$release_dir" rev-parse HEAD)"
else
  commit="$(basename "$release_dir" | sed -n 's/.*-\([0-9a-f]\{40\}\)$/\1/p')"
fi
[[ "$commit" =~ ^[0-9a-f]{40}$ ]] || { echo "Cannot determine release commit." >&2; exit 1; }

version="$(python3 - "$release_dir/pyproject.toml" <<'PY'
import pathlib
import sys
import tomllib

payload = tomllib.loads(pathlib.Path(sys.argv[1]).read_text(encoding="utf-8"))
print(payload["project"]["version"])
PY
)"
[[ "$version" =~ ^[0-9]+\.[0-9]+\.[0-9]+$ ]] || { echo "Invalid project version." >&2; exit 1; }
image_tag="${version}-${commit:0:12}"

compose() {
  local project="$1"
  local port="$2"
  shift 2
  APP_VERSION="$version" \
  IMAGE_TAG="$image_tag" \
  RETAIL_PORT="$port" \
  ALLOWED_HOSTS="$public_host,localhost,127.0.0.1" \
    docker compose -p "$project" -f "$compose_file" "$@"
}

cleanup_canary() {
  compose "$canary_project" "$canary_port" down --remove-orphans >/dev/null 2>&1 || true
}
trap cleanup_canary EXIT

echo "Building and verifying canary for $version ($commit)."
compose "$canary_project" "$canary_port" build --pull
compose "$canary_project" "$canary_port" up \
  --detach --wait --wait-timeout 180 --no-build
python3 "$smoke_script" "http://127.0.0.1:$canary_port"

canary_id="$(compose "$canary_project" "$canary_port" ps -q api)"
test -n "$canary_id"
canary_image_id="$(docker inspect --format '{{.Image}}' "$canary_id")"
test "$(docker inspect --format '{{.Config.User}}' "$canary_id")" = "10001:10001"
test "$(docker inspect --format '{{.HostConfig.ReadonlyRootfs}}' "$canary_id")" = "true"
test "$(docker inspect --format '{{.State.Health.Status}}' "$canary_id")" = "healthy"

previous_release=""
previous_version=""
previous_commit=""
previous_image_tag=""
if test -L "$current_link"; then
  previous_release="$(readlink -f "$current_link")"
  if [[ "$(dirname -- "$previous_release")" != "$releases_dir" ]]; then
    echo "Current release is outside $releases_dir." >&2
    exit 1
  fi
  previous_version="$(python3 - "$previous_release/pyproject.toml" <<'PY'
import pathlib
import sys
import tomllib

payload = tomllib.loads(pathlib.Path(sys.argv[1]).read_text(encoding="utf-8"))
print(payload["project"]["version"])
PY
)"
  if git -C "$previous_release" rev-parse --is-inside-work-tree >/dev/null 2>&1; then
    previous_commit="$(git -C "$previous_release" rev-parse HEAD)"
  else
    previous_commit="$(basename "$previous_release" | sed -n 's/.*-\([0-9a-f]\{40\}\)$/\1/p')"
  fi
  [[ "$previous_commit" =~ ^[0-9a-f]{40}$ ]] || {
    echo "Cannot determine previous release commit." >&2
    exit 1
  }
  previous_image_tag="${previous_version}-${previous_commit:0:12}"

  mapfile -t production_containers < <(
    docker ps --all --quiet \
      --filter "label=com.docker.compose.project=$production_project" \
      --filter "label=com.docker.compose.service=api"
  )
  if [[ "${#production_containers[@]}" -ne 1 ]]; then
    echo "Expected one existing production container before promotion." >&2
    exit 1
  fi
  previous_image_id="$(docker inspect --format '{{.Image}}' "${production_containers[0]}")"
  docker image inspect "$previous_image_id" >/dev/null
  docker tag "$previous_image_id" "retail-demand-forecasting-public:$previous_image_tag"
fi

cleanup_canary
trap - EXIT

rollback() {
  echo "Production verification failed; attempting rollback." >&2
  compose "$production_project" "$production_port" down --remove-orphans >/dev/null 2>&1 || true
  if test -n "$previous_release" && test -f "$previous_release/deploy/compose.public.yml"; then
    docker image inspect "retail-demand-forecasting-public:$previous_image_tag" >/dev/null
    APP_VERSION="$previous_version" \
    IMAGE_TAG="$previous_image_tag" \
    RETAIL_PORT="$production_port" \
    ALLOWED_HOSTS="$public_host,localhost,127.0.0.1" \
      docker compose -p "$production_project" \
        -f "$previous_release/deploy/compose.public.yml" \
        up --detach --wait --wait-timeout 180 --no-build
    python3 "$previous_release/scripts/smoke_public_demo.py" \
      "http://127.0.0.1:$production_port"
  fi
}

trap rollback ERR
echo "Promoting verified image to 127.0.0.1:$production_port."
compose "$production_project" "$production_port" up \
  --detach --wait --wait-timeout 180 --no-build
python3 "$smoke_script" "http://127.0.0.1:$production_port"
production_id="$(compose "$production_project" "$production_port" ps -q api)"
test -n "$production_id"
test "$(docker inspect --format '{{.Image}}' "$production_id")" = "$canary_image_id"

temporary_link="$base_dir/.current.$$"
ln -s "$release_dir" "$temporary_link"
mv -Tf "$temporary_link" "$current_link"
trap - ERR

echo "Retail demo release active: $version ($commit)"
