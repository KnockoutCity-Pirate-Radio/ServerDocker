#!/bin/sh
set -eu

KOC_SERVER_FILE_ROOT_PATH=/data
KOC_SERVER_FILE_PATH=$KOC_SERVER_FILE_ROOT_PATH/KnockoutCityServer
KOC_SERVER_OUT_PATH=$KOC_SERVER_FILE_PATH/out
KOC_SERVER_DATA_PATH=$KOC_SERVER_OUT_PATH/data

# The stamp lives beside out/data, never inside it: everything inside is
# deleted when the patch changes.
KOC_CONTENT_PATCH_STAMP=$KOC_SERVER_OUT_PATH/.content_patch_sha256

log() {
  echo "[entrypoint] $*"
}

die() {
  echo "[entrypoint] ERROR: $*" >&2
  exit 1
}

# True when `$1` is itself a mount point. Used to tell a host who bind-mounted
# their own content folder from one who handed us a zip to manage.
is_mount_point() {
  awk -v target="$1" '$2 == target { found = 1 } END { exit !found }' \
    /proc/self/mounts
}

# --- content patch -----------------------------------------------------------
#
# Two modes, deliberately exclusive:
#
#   KOC_CONTENT_PATCH set    the container owns out/data and replaces it
#                            whenever the zip changes.
#   KOC_CONTENT_PATCH unset  out/data belongs to the host; we never touch it.
#
# Mixing them would delete a host's own folder on first boot, so that
# combination is refused rather than resolved.
install_content_patch() {
  source=$1
  archive=/tmp/content-patch.zip

  case "$source" in
    http://*|https://*)
      log "downloading content patch: $source"
      wget -q -O "$archive" "$source" \
        || die "could not download the content patch from $source"
      ;;
    *)
      [ -f "$source" ] || die "content patch not found: $source"
      cp "$source" "$archive"
      ;;
  esac

  checksum=$(sha256sum "$archive" | cut -d' ' -f1)

  if [ -f "$KOC_CONTENT_PATCH_STAMP" ] \
     && [ "$(cat "$KOC_CONTENT_PATCH_STAMP")" = "$checksum" ]; then
    log "content patch unchanged ($checksum), keeping the installed one"
    rm -f "$archive"
    return
  fi

  log "installing content patch ($checksum)"
  # Scoped to out/data on purpose. Its siblings under out/ are live state:
  # out/postgresql, out/redis, out/vonline_postgresql, out/cache.
  rm -rf "$KOC_SERVER_DATA_PATH"
  mkdir -p "$KOC_SERVER_DATA_PATH"

  # Extracted through python rather than unzip so that every entry is checked
  # to stay inside out/data first. The archive may have come from a URL, and
  # this runs as root.
  python3 - "$archive" "$KOC_SERVER_DATA_PATH" <<'PY' \
    || die "could not unpack the content patch"
import pathlib
import sys
import zipfile

archive, target = sys.argv[1], pathlib.Path(sys.argv[2]).resolve()
with zipfile.ZipFile(archive) as bundle:
    for name in bundle.namelist():
        if not str((target / name).resolve()).startswith(str(target)):
            sys.exit("refusing {}: it would write outside out/data".format(name))
    bundle.extractall(target)
PY
  printf '%s' "$checksum" > "$KOC_CONTENT_PATCH_STAMP"
  rm -f "$archive"

  # The engine only reads loose files under a root carrying this marker. A
  # shipped install has one; a hand-assembled folder may not.
  [ -f "$KOC_SERVER_FILE_PATH/.viper_root" ] \
    || touch "$KOC_SERVER_FILE_PATH/.viper_root"

  log "content patch installed into out/data"
}

# --- registration ------------------------------------------------------------
#
# Advertises the *client* half of the patch to clients that log in. Runs in the
# background, because on a first-ever boot the manifest tables do not exist
# yet: the backend creates and seeds its schema during its own startup, so
# there is nothing to write to until the server we are about to launch has run.
register_content_patch() {
  manifest=$1

  if [ -z "${KOC_BACKEND_DB:-}" ]; then
    log "no KOC_BACKEND_DB, skipping content-patch registration"
    return
  fi

  (
    waited=0
    # to_regclass returns NULL rather than erroring for a table that does not
    # exist, so one query covers both "database not up yet" and "schema not
    # created yet".
    until psql "$KOC_BACKEND_DB" -tAc \
            "SELECT to_regclass('data_manifest_packages') IS NOT NULL" \
            2>/dev/null | grep -qx t; do
      waited=$((waited + 5))
      if [ "$waited" -ge 600 ]; then
        log "manifest tables did not appear within 10 minutes;"
        log "the content patch is NOT advertised to clients"
        exit 0
      fi
      sleep 5
    done

    log "registering the content patch for download"
    if python3 /register_content_patch.py "$manifest" "${KOC_CONTENT_PATCH_URL:-}" \
         | psql "$KOC_BACKEND_DB" -v ON_ERROR_STOP=1 -q; then
      log "content patch registered; clients will fetch it at login"
    else
      log "registration FAILED -- clients will run unpatched against a"
      log "patched server. Fix the manifest or KOC_CONTENT_PATCH_URL."
    fi
  ) &
}

# --- server executable patches -----------------------------------------------
apply_exe_patches() {
  exe=$KOC_SERVER_FILE_PATH/KnockoutCityServer.exe
  [ -f "$exe" ] || die "server executable not found: $exe"

  log "applying server patches (commerce offers + initial inventory)"
  python3 /patch_server.py "$exe" \
    || die "could not patch the server executable"
}

# --- boot --------------------------------------------------------------------

[ -d "$KOC_SERVER_FILE_PATH" ] \
  || die "no server install at $KOC_SERVER_FILE_PATH -- see the README on downloading the game files with SteamCMD"

if [ -n "${KOC_CONTENT_PATCH:-}" ]; then
  if is_mount_point "$KOC_SERVER_DATA_PATH"; then
    die "out/data is a mount point and KOC_CONTENT_PATCH is set. Installing the patch would delete the contents of the folder you mounted. Either drop KOC_CONTENT_PATCH and manage out/data yourself, or unmount it and let the container own it."
  fi
  install_content_patch "$KOC_CONTENT_PATCH"
else
  log "KOC_CONTENT_PATCH unset -- leaving out/data as it is"
fi

if [ "${KOC_APPLY_EXE_PATCHES:-true}" = "true" ]; then
  apply_exe_patches
else
  log "KOC_APPLY_EXE_PATCHES is not true -- running a stock server executable"
fi

if [ -n "${KOC_CONTENT_PATCH_MANIFEST:-}" ]; then
  [ -f "$KOC_CONTENT_PATCH_MANIFEST" ] \
    || die "manifest not found: $KOC_CONTENT_PATCH_MANIFEST"
  register_content_patch "$KOC_CONTENT_PATCH_MANIFEST"
else
  log "no KOC_CONTENT_PATCH_MANIFEST -- nothing advertised to clients"
fi

echo ""
echo "--------------------------------------------------------"
echo "KnockoutCity Server Max Player Connections: ${KOC_BACKEND_MAX_PLAYER_CONNECTIONS:-10}"
echo "KnockoutCity Server Backend Port: ${KOC_BACKEND_PORT:-23600}"
echo "KnockoutCity Server Port Range: ${KOC_SERVER_MIN_PORT:-23600} - ${KOC_SERVER_MAX_PORT:-23699}"
echo ""
echo "KnockoutCity Server Backend DB: ${KOC_BACKEND_DB:-}"
echo "KnockoutCity Server Backend Redis Host: ${KOC_BACKEND_REDIS_DB_HOST:-}"
echo "KnockoutCity Server Backend Redis Port: ${KOC_BACKEND_REDIS_DB_PORT:-6379}"
echo "--------------------------------------------------------"
echo ""

cd "$KOC_SERVER_FILE_PATH" || exit 1

log "starting server"
# KOC_EXTRA_ARGS is split on whitespace on purpose: it exists so a host can
# pass flags this image does not know about. "$@" carries anything appended
# to `docker run`.
# shellcheck disable=SC2086
exec wine64 KnockoutCityServer.exe \
  -backend_port="${KOC_BACKEND_PORT:-23600}" \
  -server_min_port="${KOC_SERVER_MIN_PORT:-23600}" \
  -server_max_port="${KOC_SERVER_MAX_PORT:-23699}" \
  -secret="${KOC_SECRET:-}" \
  -backend_db="${KOC_BACKEND_DB:-}" \
  -backend_redis_db_host="${KOC_BACKEND_REDIS_DB_HOST:-}" \
  -backend_redis_db_port="${KOC_BACKEND_REDIS_DB_PORT:-6379}" \
  -backend_tunable_user_connections_max_per_backend="${KOC_BACKEND_MAX_PLAYER_CONNECTIONS:-10}" \
  ${KOC_EXTRA_ARGS:-} "$@"
