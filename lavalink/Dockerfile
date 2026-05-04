# Lavalink v4 with LavaSrc + youtube-source. SPEC §7.8.5.

FROM eclipse-temurin:17-jre-jammy

ARG LAVALINK_VERSION=4.0.8
ARG LAVASRC_VERSION=4.4.0
ARG YOUTUBE_SOURCE_VERSION=1.11.4

RUN apt-get update && apt-get install -y --no-install-recommends curl ca-certificates \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /opt/lavalink

# Download Lavalink JAR
RUN curl -fsSL -o Lavalink.jar \
    "https://github.com/lavalink-devs/Lavalink/releases/download/${LAVALINK_VERSION}/Lavalink.jar"

# Plugins are auto-downloaded by Lavalink at startup based on application.yml,
# but we pre-create the plugins dir so the volume mount has a target.
RUN mkdir -p plugins logs

COPY application.yml /opt/lavalink/application.yml

EXPOSE 2333

ENV LAVALINK_MAX_HEAP_MB=192

# JVM tuning per SPEC §7.8.5: small heap, SerialGC for low RSS.
CMD java \
    -Xms64m \
    -Xmx${LAVALINK_MAX_HEAP_MB}m \
    -XX:+UseSerialGC \
    -jar Lavalink.jar
