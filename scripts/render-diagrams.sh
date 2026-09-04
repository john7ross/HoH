#!/usr/bin/env bash
# Render the architecture diagrams and record what was rendered.
#
# GitHub does not render PlantUML inline, so the PNGs are committed next to their
# sources. A committed render is a copy, and a copy goes stale the moment someone
# edits the source and forgets this step -- so the digests of the sources are
# recorded here and the readiness audit fails when they no longer match.
#
# PlantUML itself is not vendored. Point PLANTUML_JAR at a jar, or pass one:
#   PLANTUML_JAR=~/plantuml.jar scripts/render-diagrams.sh
#   scripts/render-diagrams.sh /path/to/plantuml.jar
set -euo pipefail

project="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd -P)"
jar="${1:-${PLANTUML_JAR:-}}"

if [[ -z "$jar" || ! -f "$jar" ]]; then
  echo "PlantUML jar not found. Set PLANTUML_JAR or pass the path as an argument." >&2
  echo "Downloads: https://github.com/plantuml/plantuml/releases" >&2
  exit 1
fi
command -v java >/dev/null 2>&1 || { echo "java is required to render the diagrams." >&2; exit 1; }

# The component view is wider than PlantUML's default 4096 px ceiling, and hitting
# that ceiling silently truncates the right-hand side rather than failing.
java -DPLANTUML_LIMIT_SIZE=16384 -jar "$jar" -tpng -o . "$project/docs"/*.puml

digests="$project/docs/diagrams.sha256"
: > "$digests"
for source in "$project/docs"/*.puml; do
  # Hash with line endings normalised. .gitattributes makes these files CRLF in a
  # Windows working tree and LF everywhere else, so hashing the bytes on disk would
  # mark a current render stale on every machine that did not produce it.
  digest="$(tr -d '\r' < "$source" | sha256sum | awk '{print $1}')"
  printf '%s  %s\n' "$digest" "$(basename "$source")" >> "$digests"
done

echo "Rendered:"
for image in "$project/docs"/*.png; do
  printf '  %s (%s bytes)\n' "$(basename "$image")" "$(stat -c%s "$image" 2>/dev/null || stat -f%z "$image")"
done
echo "Source digests recorded in docs/diagrams.sha256"
