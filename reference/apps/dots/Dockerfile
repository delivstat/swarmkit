FROM node:22-slim AS deps
WORKDIR /app
COPY package.json ./
RUN npm install --omit=dev=false --no-audit --no-fund --loglevel=error

FROM node:22-slim AS build
WORKDIR /app
COPY --from=deps /app/node_modules ./node_modules
COPY . .
RUN npm run build

FROM node:22-slim AS run
WORKDIR /app
ENV NODE_ENV=production
ENV PORT=3500
COPY --from=build /app/.next/standalone ./
COPY --from=build /app/.next/static ./.next/static
COPY --from=build /app/public ./public
EXPOSE 3500
CMD ["node", "server.js"]
