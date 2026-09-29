#!/usr/bin/env bash
# Rebuild the dashboard's self-hosted static assets (no third-party scripts are loaded at runtime).
#   app/web/static/app.css      <- Tailwind CSS built from app/web/templates
#   app/web/static/htmx.min.js  <- htmx, vendored from npm
# Requires Node.js + npm. Run after changing classes in the templates.
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
TAILWIND_VERSION="3.4.17"
HTMX_VERSION="1.9.12"
TMP="$(mktemp -d)"
trap 'rm -rf "$TMP"' EXIT
( cd "$TMP" && npm init -y >/dev/null && npm install --silent --no-audit --no-fund \
    "tailwindcss@${TAILWIND_VERSION}" "htmx.org@${HTMX_VERSION}" )
node "$TMP/node_modules/tailwindcss/lib/cli.js" \
  -c "$ROOT/app/web/tailwind.config.js" -i "$ROOT/app/web/tailwind.input.css" \
  -o "$ROOT/app/web/static/app.css" --minify
cp "$TMP/node_modules/htmx.org/dist/htmx.min.js" "$ROOT/app/web/static/htmx.min.js"
cp "$TMP/node_modules/htmx.org/LICENSE" "$ROOT/app/web/static/htmx.LICENSE"
echo "assets built: $(wc -c < "$ROOT/app/web/static/app.css") bytes css, $(wc -c < "$ROOT/app/web/static/htmx.min.js") bytes js"
