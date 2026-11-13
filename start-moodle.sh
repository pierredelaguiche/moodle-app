#!/usr/bin/env bash
# start-moodle.sh — initialise and run the Moodle backend (moodle-docker) and the Moodle app frontend.
#
# Expected layout (override with env vars if yours differs):
#   <root>/moodle         Moodle core        (MOODLE_DIR)
#   <root>/moodle-docker  moodle-docker      (DOCKER_DIR)
#   <root>/moodleapp      Moodle app         (APP_DIR)
#
# Usage:
#   ./start-moodle.sh            start backend + frontend (default)
#   ./start-moodle.sh install    first run: start backend, install DB, enable mobile services
#   ./start-moodle.sh down       stop and remove backend containers
#
# Windows: run from Git Bash or WSL with Docker Desktop running.

set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
MOODLE_DIR="${MOODLE_DIR:-$ROOT/moodle}"
DOCKER_DIR="${DOCKER_DIR:-$ROOT/moodle-docker}"
APP_DIR="${APP_DIR:-$ROOT/moodleapp}"

export MOODLE_DOCKER_WWWROOT="$MOODLE_DIR"
export MOODLE_DOCKER_DB="${MOODLE_DOCKER_DB:-pgsql}"
export MOODLE_DOCKER_WEB_PORT="${MOODLE_DOCKER_WEB_PORT:-8000}"

ADMIN_PASS="${ADMIN_PASS:-test}"

dc() { "$DOCKER_DIR/bin/moodle-docker-compose" "$@"; }

die() { echo "Error: $*" >&2; exit 1; }

check_prereqs() {
  command -v docker >/dev/null || die "docker not found (start Docker Desktop)."
  command -v npm    >/dev/null || die "npm not found (install Node.js)."
  [ -d "$MOODLE_DIR" ] || die "Moodle core not found at $MOODLE_DIR"
  [ -d "$DOCKER_DIR" ] || die "moodle-docker not found at $DOCKER_DIR"
  [ -d "$APP_DIR" ]    || die "moodleapp not found at $APP_DIR"
}

init_config() {
  if [ ! -f "$MOODLE_DIR/config.php" ]; then
    echo "→ Creating config.php from moodle-docker template"
    cp "$DOCKER_DIR/config.docker-template.php" "$MOODLE_DIR/config.php"
  fi
}

start_backend() {
  echo "→ Starting backend (db: $MOODLE_DOCKER_DB, port: $MOODLE_DOCKER_WEB_PORT)"
  dc up -d
  "$DOCKER_DIR/bin/moodle-docker-wait-for-db"
}

install_site() {
  echo "→ Installing Moodle database"
  dc exec webserver php admin/cli/install_database.php \
    --agree-license \
    --fullname="Docker moodle" \
    --shortname="docker_moodle" \
    --summary="Docker moodle site" \
    --adminpass="$ADMIN_PASS" \
    --adminemail="admin@example.com"

  echo "→ Enabling web services for the mobile app"
  dc exec webserver php admin/cli/cfg.php --name=enablewebservices --set=1
  dc exec webserver php admin/cli/cfg.php --name=enablemobilewebservice --set=1
}

start_frontend() {
  cd "$APP_DIR"
  if [ ! -d node_modules ]; then
    echo "→ Installing app dependencies"
    npm install
  fi
  echo "→ Starting app on http://localhost:8100 (site: http://localhost:$MOODLE_DOCKER_WEB_PORT)"
  npm start
}

case "${1:-up}" in
  up)
    check_prereqs
    init_config
    start_backend
    start_frontend
    ;;
  install)
    check_prereqs
    init_config
    start_backend
    install_site
    start_frontend
    ;;
  down)
    dc down
    ;;
  *)
    die "Unknown command '$1'. Use: up | install | down"
    ;;
esac
