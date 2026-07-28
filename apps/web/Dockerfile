# Trax Inventory Optimizer (apps/web) — Vite build served by nginx, which reverse-proxies /v1 to the BFF.
FROM node:20-bookworm-slim AS build
WORKDIR /app
# Copy packages/ for tailwind-preset and other shared dependencies
COPY packages/ ./packages/
# Copy apps/web with its own package.json and package-lock.json
COPY apps/web/ ./apps/web/
WORKDIR /app/apps/web
RUN npm ci
# Empty base → the client calls same-origin /v1/... which nginx proxies to the BFF (no CORS).
ENV VITE_BFF_URL=""
RUN npm run build

FROM nginx:1.27-alpine
COPY apps/web/nginx.conf /etc/nginx/conf.d/default.conf
COPY --from=build /app/apps/web/dist /usr/share/nginx/html
EXPOSE 80
