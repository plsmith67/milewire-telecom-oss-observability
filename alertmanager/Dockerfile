FROM prom/alertmanager:v0.27.0

COPY alertmanager.yml /etc/alertmanager/alertmanager.yml

HEALTHCHECK --interval=10s --timeout=5s --start-period=15s --retries=3 \
    CMD wget -q -O /dev/null http://127.0.0.1:9093/-/healthy || exit 1

EXPOSE 9093
