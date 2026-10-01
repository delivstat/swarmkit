# Multi-stage build — the final image is Next.js's `standalone` output on top of a slim Node
# runtime (~150 MB). See design/details/dot-app.md §Dockerfile shape.
#
# Build from the repo root so the pnpm workspace lockfile resolves correctly:
#   docker build -f reference/apps/dot/Dockerfile -t ghcr.io/delivstat/dot-app:dev .

FROM node:20-alpine AS deps
WORKDIR /app
RUN corepack enable && corepack prepare pnpm@9.12.0 --activate
# Workspace manifests first, so Docker caches the install layer whenever source changes.
COPY package.json pnpm-lock.yaml pnpm-workspace.yaml .npmrc* ./
COPY reference/apps/dot/package.json reference/apps/dot/package.json
RUN pnpm install --filter @swarmkit/dot --frozen-lockfile

FROM node:20-alpine AS build
WORKDIR /app
RUN corepack enable && corepack prepare pnpm@9.12.0 --activate
COPY --from=deps /app ./
COPY reference/apps/dot ./reference/apps/dot
RUN pnpm --filter @swarmkit/dot build

FROM node:20-alpine AS run
WORKDIR /app
ENV NODE_ENV=production PORT=3400 HOSTNAME=0.0.0.0
# Next.js standalone output: a tiny server.js plus the pre-traced node_modules.
COPY --from=build /app/reference/apps/dot/.next/standalone/reference/apps/dot ./
COPY --from=build /app/reference/apps/dot/.next/standalone/node_modules ./node_modules
COPY --from=build /app/reference/apps/dot/.next/static ./.next/static
EXPOSE 3400
USER node
CMD ["node", "server.js"]
