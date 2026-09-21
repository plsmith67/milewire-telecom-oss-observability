FROM grafana/grafana:11.4.0

USER root
RUN if command -v apk >/dev/null 2>&1; then apk add --no-cache curl wget; \
    elif command -v apt-get >/dev/null 2>&1; then apt-get update && apt-get install -y --no-install-recommends curl wget && rm -rf /var/lib/apt/lists/*; \
    fi
USER grafana

COPY provisioning /etc/grafana/provisioning
COPY dashboards /var/lib/grafana/dashboards

ENV GF_SECURITY_ADMIN_USER=admin \
    GF_SECURITY_ADMIN_PASSWORD=admin \
    GF_USERS_ALLOW_SIGN_UP=false \
    GF_AUTH_ANONYMOUS_ENABLED=false \
    GF_DASHBOARDS_DEFAULT_HOME_DASHBOARD_PATH=/var/lib/grafana/dashboards/private-lte-oss.json

HEALTHCHECK --interval=10s --timeout=5s --start-period=30s --retries=5 \
    CMD curl -fsS http://127.0.0.1:3000/api/health || wget -q -O /dev/null http://127.0.0.1:3000/api/health || exit 1

EXPOSE 3000
