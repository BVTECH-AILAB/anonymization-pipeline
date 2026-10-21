# Demo page of the anonymization service, served by nginx with a reverse proxy
# to the API. Build context: apps/web (docker build apps/web).
#   API_UPSTREAM  base URL of the API (in compose: http://app:8000)
#   WEB_PORT      port nginx listens on
FROM nginxinc/nginx-unprivileged:1.28.2-alpine

ENV API_UPSTREAM=http://anonmyzation-pipeline:8000 \
    WEB_PORT=8080
COPY nginx/default.conf.template /etc/nginx/templates/default.conf.template
COPY index.html /usr/share/nginx/html/index.html
EXPOSE 8080
HEALTHCHECK --interval=30s --timeout=5s --start-period=10s \
    CMD wget -qO /dev/null "http://127.0.0.1:${WEB_PORT}/healthz" || exit 1
