FROM prom/prometheus:v2.55.1

COPY prometheus.yml /etc/prometheus/prometheus.yml
COPY alerts /etc/prometheus/alerts

HEALTHCHECK --interval=10s --timeout=5s --start-period=15s --retries=3 \
    CMD wget -q -O /dev/null http://127.0.0.1:9090/-/healthy || exit 1

EXPOSE 9090
