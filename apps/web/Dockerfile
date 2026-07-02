# Trax Inventory Optimizer (apps/web) — Vite build served by nginx, which reverse-proxies /v1 to the BFF.
FROM node:20-bookworm-slim AS build
WORKDIR /app
COPY package.json package-lock.json ./
RUN npm ci
COPY . .
# Empty base → the client calls same-origin /v1/... which nginx proxies to the BFF (no CORS).
ENV VITE_BFF_URL=""
RUN npm run build

FROM nginx:1.27-alpine
COPY nginx.conf /etc/nginx/conf.d/default.conf
COPY --from=build /app/dist /usr/share/nginx/html
EXPOSE 80
