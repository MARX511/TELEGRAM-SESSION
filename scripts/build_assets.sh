#!/usr/bin/env bash
# Rebuild the dashboard's self-hosted static assets. Nothing is loaded from a CDN at runtime.
#   app/web/static/app.css                 <- Tailwind CSS built from app/web/templates + design tokens
#   app/web/static/htmx.min.js             <- htmx, vendored from npm
#   app/web/static/vendor/three.module.min.js  <- three.js (login 3D scene), vendored from npm
#   app/web/static/fonts/*.woff2           <- IBM Plex Sans Arabic + IBM Plex Mono (OFL)
#   app/web/templates/partials/icons.html  <- Lucide icon macro (ISC), generated
# Requires Node.js + npm. Run after changing classes in the templates or the icon list.
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
TAILWIND_VERSION="3.4.17"
HTMX_VERSION="1.9.12"
THREE_VERSION="0.170.0"
FONTSOURCE_VERSION="5"
LUCIDE_VERSION="0.460.0"
TMP="$(mktemp -d)"
trap 'rm -rf "$TMP"' EXIT
( cd "$TMP" && npm init -y >/dev/null && npm install --silent --no-audit --no-fund \
    "tailwindcss@${TAILWIND_VERSION}" "htmx.org@${HTMX_VERSION}" "three@${THREE_VERSION}" \
    "@fontsource/ibm-plex-sans-arabic@${FONTSOURCE_VERSION}" "@fontsource/ibm-plex-mono@${FONTSOURCE_VERSION}" \
    "lucide-static@${LUCIDE_VERSION}" )
NM="$TMP/node_modules"
STATIC="$ROOT/app/web/static"
mkdir -p "$STATIC/vendor" "$STATIC/fonts"

# icons first: the Tailwind scan must see the generated template
node "$ROOT/scripts/gen_icons.mjs" "$NM" "$ROOT/app/web/templates/partials/icons.html"

node "$NM/tailwindcss/lib/cli.js" \
  -c "$ROOT/app/web/tailwind.config.js" -i "$ROOT/app/web/tailwind.input.css" \
  -o "$STATIC/app.css" --minify

cp "$NM/htmx.org/dist/htmx.min.js" "$STATIC/htmx.min.js"
cp "$NM/htmx.org/LICENSE" "$STATIC/htmx.LICENSE"
cp "$NM/three/build/three.module.min.js" "$STATIC/vendor/three.module.min.js"
cp "$NM/three/LICENSE" "$STATIC/vendor/three.LICENSE"

for w in 400 500 600 700; do
  for s in arabic latin; do
    cp "$NM/@fontsource/ibm-plex-sans-arabic/files/ibm-plex-sans-arabic-$s-$w-normal.woff2" "$STATIC/fonts/"
  done
done
for w in 400 500; do
  cp "$NM/@fontsource/ibm-plex-mono/files/ibm-plex-mono-latin-$w-normal.woff2" "$STATIC/fonts/"
done
cp "$NM/@fontsource/ibm-plex-sans-arabic/LICENSE" "$STATIC/fonts/OFL-IBM-Plex.txt"
cp "$NM/lucide-static/LICENSE" "$STATIC/vendor/lucide.LICENSE"

echo "assets built: $(wc -c < "$STATIC/app.css") bytes css, $(wc -c < "$STATIC/vendor/three.module.min.js") bytes three, $(ls "$STATIC/fonts"/*.woff2 | wc -l) font files"
