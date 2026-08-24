#!/usr/bin/env bash
# Is this node's docker actually able to build 150+ Tmax task images?
#
#   MIN_IMAGE_GB=80 bash scripts/tmax/docker_preflight.sh
#
# Checks, in the order that actually catches real failures:
#   1. daemon reachable
#   2. free space on the filesystem holding the IMAGE STORE — which on docker 29
#      with the containerd image store is /var/lib/containerd, NOT the path
#      `docker info` prints as DockerRootDir. Checking DockerRootDir alone
#      reports 421G free on a node where every build dies instantly with
#      "no space left on device" writing into
#      /var/lib/containerd/io.containerd.snapshotter.v1.overlayfs/... because
#      that path lives on a full root volume.
#   3. a real one-layer build, with the FULL error printed and diagnosed —
#      buildx missing, registry unreachable and ENOSPC all surface here.

set -uo pipefail
MIN_IMAGE_GB="${MIN_IMAGE_GB:-80}"

fail() { echo "ERROR: $*" >&2; exit 2; }

docker info >/dev/null 2>&1 || fail "docker daemon unreachable on $(hostname)"

root_dir="$(docker info --format '{{.DockerRootDir}}' 2>/dev/null || echo /var/lib/docker)"
driver="$(docker info --format '{{.Driver}}' 2>/dev/null || echo unknown)"

# Candidate stores, most specific first. The containerd snapshotter wins when
# the driver is the containerd-backed one.
declare -a cands=()
[[ -d /var/lib/containerd ]] && cands+=(/var/lib/containerd)
[[ -d "$root_dir" ]] && cands+=("$root_dir")
[[ -d /var/lib/docker ]] && cands+=(/var/lib/docker)
[[ ${#cands[@]} -gt 0 ]] || cands+=(/)

avail_gb() { df -BG --output=avail "$1" 2>/dev/null | tail -1 | tr -dc '0-9'; }
fs_of()    { df --output=source,target "$1" 2>/dev/null | tail -1; }

echo "docker  : $(docker version --format '{{.Server.Version}}' 2>/dev/null) driver=$driver"
echo "stores  :"
store=""
store_gb=0
for c in "${cands[@]}"; do
  g="$(avail_gb "$c")"; g="${g:-0}"
  printf '  %-28s %5sG free   [%s]\n' "$c" "$g" "$(fs_of "$c")"
  # The image store is what must be big. Prefer /var/lib/containerd when the
  # driver is containerd-backed, else DockerRootDir.
  if [[ -z "$store" ]]; then
    case "$driver:$c" in
      overlayfs:/var/lib/containerd|containerd*:/var/lib/containerd) store="$c"; store_gb="$g" ;;
    esac
  fi
done
if [[ -z "$store" ]]; then
  store="$root_dir"; store_gb="$(avail_gb "$root_dir")"; store_gb="${store_gb:-0}"
fi
echo "image store in use: $store (${store_gb}G free, need >= ${MIN_IMAGE_GB}G)"

space_hint() {
  if (( MIN_IMAGE_GB < 40 )); then
    cat >&2 <<HINT

Need >= ${MIN_IMAGE_GB}G. With SHARED_BASE=1 the 152 task images share one
python3/pip/pytest layer, so they cost ~15G instead of ~60G — that is the sizing
this threshold assumes.
HINT
  else
    cat >&2 <<HINT

Need >= ${MIN_IMAGE_GB}G: each of the 152 taxonomy images repeats the same
ubuntu:22.04 + apt python3/pip + pytest preamble in its own layer (~0.4-0.6G
each, nothing shared past the 80MB base). To cut that to ~15G instead of buying
more disk:
  SHARED_BASE=1 JOBS=12 bash scripts/tmax/prebuild_tmax_images.sh
HINT
  fi
  cat >&2 <<HINT

Free space, cheapest first:
  docker system df                      # what docker is holding
  docker system prune -a -f             # drop images no RUNNING container uses
  docker builder prune -a -f            # drop build cache
  sudo du -xh --max-depth=1 $store 2>/dev/null | sort -h | tail -12

If pruning is not enough, move the image store onto the big volume. With the
containerd image store the relevant root is containerd's, not docker's:

  # option A - stop using the containerd store, so docker's data-root applies
  sudo tee /etc/docker/daemon.json >/dev/null <<'JSON'
  {"data-root": "$root_dir", "features": {"containerd-snapshotter": false}}
JSON
  sudo systemctl restart docker

  # option B - move containerd's root itself
  sudo systemctl stop docker containerd
  sudo mkdir -p $root_dir/../containerd && sudo mv /var/lib/containerd/* $root_dir/../containerd/
  sudo mkdir -p /etc/containerd && sudo containerd config default | \
    sudo sed "s|root = \"/var/lib/containerd\"|root = \"$root_dir/../containerd\"|" \
    | sudo tee /etc/containerd/config.toml >/dev/null
  sudo systemctl start containerd docker

Both restart the daemon, which kills containers already running on this node —
check with \`docker ps\` first if another job of yours is using them.
HINT
}

if (( store_gb < MIN_IMAGE_GB )); then
  echo "ERROR: only ${store_gb}G free on the image store ($store)" >&2
  space_hint
  exit 2
fi

# Real build. Catches ENOSPC on a store we mis-identified, a missing buildx,
# and an unreachable registry — none of which a df check can see.
tmpd="$(mktemp -d)"
trap 'rm -rf "$tmpd"' EXIT
printf 'FROM ubuntu:22.04\nRUN true\n' >"$tmpd/Dockerfile"
if out="$(docker build --network=host -t hx-docker-preflight "$tmpd" 2>&1)"; then
  echo "smoke build: OK"
  docker image rm -f hx-docker-preflight >/dev/null 2>&1 || true
  exit 0
fi

echo "ERROR: smoke build failed. Full output:" >&2
echo "$out" | tail -30 >&2
if grep -qi "no space left on device" <<<"$out"; then
  bad_path="$(grep -oE '/[^ "]*' <<<"$out" | grep -m1 -E 'containerd|docker' || true)"
  echo >&2
  echo "diagnosis: ENOSPC while writing ${bad_path:-the image store}" >&2
  [[ -n "$bad_path" ]] && df -h "$bad_path" >&2 2>/dev/null
  space_hint
elif grep -qiE "legacy builder|classic builder|not supported with the containerd" <<<"$out"; then
  echo >&2 "diagnosis: DOCKER_BUILDKIT=0 does not work here — the classic builder is"
  echo >&2 "           unsupported with the containerd image store. Unset it and rely on"
  echo >&2 "           SHARED_BASE=1 + periodic 'docker builder prune -f' instead."
elif grep -qiE "buildx|buildkit" <<<"$out"; then
  echo >&2 "diagnosis: buildx/BuildKit problem — check the docker-buildx-plugin install"
elif grep -qiE "pull access denied|unauthorized|failed to resolve|dial tcp|timeout" <<<"$out"; then
  echo >&2 "diagnosis: cannot reach the registry for ubuntu:22.04 — check egress/proxy, or pre-pull it"
fi
exit 2
