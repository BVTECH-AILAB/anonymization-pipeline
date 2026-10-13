# Web UI: build with Node, static files served by nginx (proxy /api -> API_UPSTREAM).
FROM node:24-alpine AS build
WORKDIR /web
COPY package.json package-lock.json ./
RUN npm ci --no-audit --no-fund
COPY . .
RUN npm run build

FROM nginx:1.29-alpine
COPY nginx.conf.template /etc/nginx/templates/default.conf.template
# optional extra locations (docker compose mounts nginx.validation.conf here)
RUN mkdir -p /etc/nginx/extra
# base URL of the API, without the trailing slash
ENV API_UPSTREAM=http://app:8000
COPY --from=build /web/dist /usr/share/nginx/html
EXPOSE 80
HEALTHCHECK --interval=30s --timeout=5s CMD wget -qO- http://localhost/ >/dev/null || exit 1
