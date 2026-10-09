# dots-app image. Uses pnpm against the repo's pnpm-workspace.yaml so the lockfile that
# builds inside the container matches the one every contributor runs locally — using plain
# `npm install` here picks different sub-dep versions and the production build fails on
# version drift between @ag-ui/client and @copilotkit (type error on HttpAgent).
#
# Build context must be the REPO ROOT (see docker-compose.yml → services.dots-app.build),
# the same way Dockerfile.swarmkit does.
#
# `output: standalone` is deliberately NOT used here. In a pnpm workspace the standalone
# tree carries empty symlink-based node_modules; `node server.js` fails with "Cannot find
# module 'next'" in production. Shipping the built `.next` tree and running `next start`
# with the real node_modules keeps the image uncomplicated at the cost of a bit of size.

FROM node:22-slim AS deps
RUN corepack enable
WORKDIR /repo
COPY package.json pnpm-lock.yaml pnpm-workspace.yaml ./
COPY reference/apps/dots/package.json reference/apps/dots/package.json
RUN pnpm --filter @swarmkit/dots... install --frozen-lockfile

FROM node:22-slim AS build
RUN corepack enable
WORKDIR /repo
COPY --from=deps /repo/node_modules ./node_modules
COPY --from=deps /repo/reference/apps/dots/node_modules ./reference/apps/dots/node_modules
COPY pnpm-workspace.yaml package.json ./
COPY reference/apps/dots ./reference/apps/dots
RUN pnpm --filter @swarmkit/dots build

FROM node:22-slim AS run
RUN corepack enable
WORKDIR /app
ENV NODE_ENV=production
ENV PORT=3500
# Disable pnpm's `verifyDepsBeforeRun` so it doesn't re-run `install` on every container
# start — that re-install swaps the baked node_modules for a fresh one that can disagree
# with the compiled .next tree in subtle ways (we burned hours on login silently 401ing
# because of exactly this). Script-time install stays available in dev.
ENV NPM_CONFIG_VERIFY_DEPS_BEFORE_RUN=false
COPY --from=build /repo/node_modules /repo/node_modules
COPY --from=build /repo/package.json /repo/pnpm-workspace.yaml /repo/
COPY --from=build /repo/reference/apps/dots /app/
# `.dockerignore` only filters the ROOT build context. This Dockerfile's context is the
# repo root (compose: dots-app.build.context=../../..), so a `.dockerignore` placed in
# reference/apps/dots is NOT consulted and .env/.env.local/.env.docker get baked in.
# Baked env files are loaded by @next/env on startup and SHADOW the vars Compose passes
# via env_file — causing silent login 401s. Strip them explicitly.
RUN rm -f /app/.env /app/.env.local /app/.env.production /app/.env.production.local /app/.env.docker
# pnpm start → `next start --port 3500`; keeps workspace symlinks intact (invoking
# ./node_modules/.bin/next directly fails because its symlinks point into /repo).
EXPOSE 3500
CMD ["pnpm", "start"]
