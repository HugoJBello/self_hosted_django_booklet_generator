FROM debian:trixie-slim

ENV DEBIAN_FRONTEND=noninteractive

RUN apt-get update && apt-get install -y --no-install-recommends \
    cups-daemon \
    cups-client \
    cups-server-common \
    cups-core-drivers \
    cups-filters \
    avahi-daemon \
    avahi-utils \
    dbus \
    ca-certificates \
 && rm -rf /var/lib/apt/lists/* \
 && cp -a /etc/cups /opt/cups-default

COPY docker/cups/cupsd.conf /opt/pdf-manager-cupsd.conf
COPY docker/cups/entrypoint.sh /entrypoint.sh
RUN chmod +x /entrypoint.sh

ENTRYPOINT ["/entrypoint.sh"]
