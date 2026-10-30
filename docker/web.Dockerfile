FROM node:22-alpine AS build
WORKDIR /web
COPY web/package.json web/package-lock.json ./
RUN npm ci --no-audit --no-fund
COPY web/ ./
# Browser-side URL of the OpsLoop API (baked in at build time by Next.js).
ARG NEXT_PUBLIC_OPSLOOP_API=http://localhost:8000
ENV NEXT_PUBLIC_OPSLOOP_API=$NEXT_PUBLIC_OPSLOOP_API
RUN npm run build

FROM node:22-alpine
WORKDIR /app
ENV NODE_ENV=production PORT=3000 HOSTNAME=0.0.0.0
COPY --from=build /web/.next/standalone ./
COPY --from=build /web/.next/static ./.next/static
USER node
EXPOSE 3000
CMD ["node", "server.js"]
