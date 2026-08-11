FROM python:3.12-slim-bookworm

RUN apt-get update \
    && apt-get install -y --no-install-recommends git ripgrep ca-certificates \
    && rm -rf /var/lib/apt/lists/* \
    && groupadd --gid 65532 wenmo \
    && useradd --uid 65532 --gid 65532 --no-create-home --shell /usr/sbin/nologin wenmo

WORKDIR /workspace
USER 65532:65532
ENTRYPOINT []
