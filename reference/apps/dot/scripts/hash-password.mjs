#!/usr/bin/env node
// Emit a scrypt hash for DOT_OWNER_PASSWORD_HASH. Read the password from stdin so it never
// enters the shell history.
//
//   printf '%s' 'your password' | node scripts/hash-password.mjs
//
// Prints the scrypt$... string on stdout; paste into .env.

import { randomBytes, scryptSync } from "node:crypto";

const N = 16384;
const R = 8;
const P = 1;
const KEYLEN = 64;

const chunks = [];
for await (const c of process.stdin) chunks.push(c);
const password = Buffer.concat(chunks).toString("utf8").replace(/\n$/, "");
if (!password) {
	process.stderr.write("no password on stdin\n");
	process.exit(1);
}
const salt = randomBytes(16);
const hash = scryptSync(password, salt, KEYLEN, { N, r: R, p: P });
const raw = `scrypt$${N}$${R}$${P}$${salt.toString("base64")}$${hash.toString("base64")}`;
// Next.js's env loader (@next/env) does $VAR expansion on .env files, so the
// unescaped '$' segments in our hash get eaten. Print the value pre-escaped and
// double-quoted so the user can paste it straight after `DOT_OWNER_PASSWORD_HASH=`.
const escaped = raw.replaceAll("$", "\\$");
process.stdout.write(`"${escaped}"\n`);
