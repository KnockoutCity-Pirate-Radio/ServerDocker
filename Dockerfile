FROM --platform=linux/amd64 alpine:3.18

LABEL org.opencontainers.image.source "https://github.com/KnockoutCity-Pirate-Radio/ServerDocker"
LABEL org.opencontainers.image.description "A Docker Container Image that runs a Knockout City Private Server"

# python3        runs the two helper scripts below
# postgresql-client  registers the content patch in the backend's own tables
RUN apk add --update wine python3 postgresql-client

COPY --chmod=0755 entrypoint.sh entrypoint.sh
COPY --chmod=0755 patch_server.py /patch_server.py
COPY --chmod=0755 register_content_patch.py /register_content_patch.py

ENTRYPOINT ["sh", "entrypoint.sh"]
