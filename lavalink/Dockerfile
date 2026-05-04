# Lavalink v4 — extend the official image and bake in our config.
# SPEC §3.1 / §7.8.5.

FROM ghcr.io/lavalink-devs/lavalink:4

# Bake the application.yml (sources / plugins / JVM tuning hints).
# Plugins (LavaSrc, youtube-source) listed under `lavalink.plugins:` are auto-
# downloaded by Lavalink itself at first startup — no need to fetch in the build.
COPY --chown=lavalink:lavalink application.yml /opt/Lavalink/application.yml

# Memory tuning per SPEC §7.8.5: small heap, SerialGC for low RSS.
# Override LAVALINK_MAX_HEAP_MB at deploy time to size the heap.
ENV LAVALINK_MAX_HEAP_MB=192

# Railway routes healthcheck traffic to $PORT. Pin it to 2333 so it matches
# the port Lavalink binds to (from application.yml `server.port: 2333`).
ENV PORT=2333

# Override the base ENTRYPOINT so we can apply -Xmx / -Xms / GC choice. exec
# keeps Java as PID 1 so SIGTERM from Railway/Compose shuts it down cleanly.
ENTRYPOINT ["/bin/sh", "-c", "exec java -Xms64m -Xmx${LAVALINK_MAX_HEAP_MB}m -XX:+UseSerialGC -jar Lavalink.jar"]
