#!/bin/sh
set -eu

# The bind-mounted directory starts empty on first boot. Seed CUPS' package
# defaults once, then keep queues and PPD files persistent in ./data/cups/etc.
if [ ! -f /etc/cups/mime.types ]; then
  cp -a /opt/cups-default/. /etc/cups/
fi
cp /opt/pdf-manager-cupsd.conf /etc/cups/cupsd.conf

mkdir -p /run/cups /var/spool/cups /var/cache/cups /run/dbus
rm -f /run/cups/cups.sock /run/dbus/pid
dbus-daemon --system --fork
avahi-daemon --daemonize --no-drop-root || true

# Refresh driverless IPP/Bonjour discoveries for Django. This runs in the
# host network namespace, while the web container only reads the shared file.
(
  while true; do
    : > /run/cups/discovered-printers.tmp
    while IFS= read -r discovered_uri; do
      [ -n "$discovered_uri" ] || continue
      scheme=${discovered_uri%%://*}
      address=${discovered_uri#*://}
      authority=${address%%/*}
      suffix=${address#"$authority"}
      host=${authority%%:*}
      port=${authority#"$host"}
      if [ "$host" != "$authority" ] && [ "$host" != "${host%.local}" ]; then
        resolution=$(avahi-resolve-host-name -4 "$host" 2>/dev/null || true)
        resolved_ip=$(printf '%s\n' "$resolution" | awk 'NR == 1 { print $2 }')
        case "$resolved_ip" in
          *.*.*.*) discovered_uri="$scheme://$resolved_ip$port$suffix" ;;
        esac
      fi
      printf '%s\n' "$discovered_uri" >> /run/cups/discovered-printers.tmp
    done <<EOF
$(ippfind -T 4 --print 2>/dev/null || true)
EOF
    mv /run/cups/discovered-printers.tmp /run/cups/discovered-printers.txt
    sleep 10
  done
) &

exec cupsd -f
