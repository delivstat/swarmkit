#!/usr/bin/env bash
# Reset the dots-app owner password. Updates DOTS_OWNER_PASSWORD_HASH in every env file
# that exists (.env, .env.docker, .env.local) with a scrypt hash of the new password, then
# restarts the dots-app container if docker compose is running the stack.
#
# Usage:
#   ./scripts/reset-password.sh                     # prompts for the password (hidden input)
#   ./scripts/reset-password.sh 'my-new-password'   # one-liner
#
# Options:
#   --no-restart   skip the docker-compose restart step (useful on hosts without docker)
#
# Env files it understands:
#   .env         — dev shell default (DOTS_OWNER_PASSWORD_HASH with $$ escaped for @next/env)
#   .env.local   — dev shell override (same $$ escaping)
#   .env.docker  — docker-compose env_file (same $$ escaping; compose collapses $$ → $)

set -euo pipefail
cd "$(dirname "$0")/.."

restart=1
password=""
for arg in "$@"; do
	case "$arg" in
		--no-restart) restart=0 ;;
		-h|--help)
			sed -n '2,/^set/p' "$0" | sed 's/^# \{0,1\}//; /^set/d'
			exit 0
			;;
		-*)
			echo "unknown option: $arg" >&2
			exit 1
			;;
		*) password="$arg" ;;
	esac
done

if [[ -z "$password" ]]; then
	echo -n "New password for owner: "
	read -r -s password
	echo
	if [[ -z "$password" ]]; then
		echo "empty password; aborting" >&2
		exit 1
	fi
	echo -n "Confirm: "
	read -r -s confirm
	echo
	if [[ "$password" != "$confirm" ]]; then
		echo "passwords did not match; aborting" >&2
		exit 1
	fi
fi

if ! command -v node >/dev/null; then
	echo "node is required for scripts/hash-password.mjs" >&2
	exit 1
fi

# hash-password.mjs emits a JSON-quoted string with every `$` written as `\$`
# (so the raw output looks like `"scrypt\$16384\$8\$1\$salt\$hash"`).
# Strip the outer quotes, then unescape `\$` → `$` to get the real hash.
raw=$(printf '%s' "$password" | node scripts/hash-password.mjs)
raw=${raw#\"}; raw=${raw%\"}
raw=${raw//\\\$/\$}

# BOTH @next/env (loading .env / .env.local) and docker-compose (loading .env.docker
# via env_file) interpolate `$VAR` references in values and collapse `$$` → `$`.
# So a hash containing a literal `$` MUST be stored with the `$` escaped as `$$` in
# every env file we write — otherwise `$B...` / `$Ca...` chunks of the hash get
# consumed as empty variable expansions and the container sees a mangled hash.
hash=${raw//\$/\$\$}

# scripts/hash-password.mjs already emits $$ escaping; use the output verbatim in each env
# file. Compose collapses $$ → $ via env_file interpolation; @next/env does the same for
# .env / .env.local.
updated=()
for f in .env .env.docker .env.local; do
	if [[ -f "$f" ]]; then
		if grep -q '^DOTS_OWNER_PASSWORD_HASH=' "$f"; then
			# Use a sed delimiter unlikely to collide with any hash character.
			sed -i "s|^DOTS_OWNER_PASSWORD_HASH=.*|DOTS_OWNER_PASSWORD_HASH=${hash}|" "$f"
		else
			printf 'DOTS_OWNER_PASSWORD_HASH=%s\n' "$hash" >> "$f"
		fi
		updated+=("$f")
	fi
done

if [[ ${#updated[@]} -eq 0 ]]; then
	echo "no env files found (expected one of .env, .env.docker, .env.local)" >&2
	exit 1
fi

echo "Updated DOTS_OWNER_PASSWORD_HASH in: ${updated[*]}"

if (( restart )); then
	if command -v docker >/dev/null && docker compose ps --format json dots-app 2>/dev/null | grep -q '"State"'; then
		echo "Restarting dots-app container..."
		docker compose up -d --force-recreate dots-app >/dev/null
		echo "Done. Log in as owner with your new password."
	else
		echo "docker compose dots-app is not running; skipping restart."
		echo "Start it with: docker compose up -d"
	fi
else
	echo "--no-restart passed; restart the stack yourself when ready."
fi
