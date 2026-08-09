FROM node:24.7.0-alpine AS dependencies
WORKDIR /app
COPY miniapp/package.json miniapp/package-lock.json ./
RUN npm ci

FROM dependencies AS builder
COPY miniapp/ ./
RUN npm run build

FROM dependencies AS production-dependencies
RUN npm prune --omit=dev

FROM node:24.7.0-alpine AS runtime
ENV NODE_ENV=production \
    HOSTNAME=0.0.0.0 \
    PORT=3000
WORKDIR /app
COPY --from=production-dependencies --chown=node:node /app/node_modules ./node_modules
COPY --from=builder --chown=node:node /app/.next ./.next
COPY --from=builder --chown=node:node /app/package.json /app/package-lock.json ./
COPY --from=builder --chown=node:node /app/next.config.ts /app/tsconfig.json ./
COPY --from=builder --chown=node:node /app/worker ./worker
COPY --from=builder --chown=node:node /app/scripts ./scripts
COPY --from=builder --chown=node:node /app/drizzle ./drizzle
USER node
EXPOSE 3000
CMD ["npm", "run", "start"]
