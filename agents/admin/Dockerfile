# Sysadmin observer: reads the host (mounted read-only at /host), proposes
# commands, never executes anything on the host. No sudo, no setuid binaries.
FROM agent-sandbox/agent-base:local

USER root
RUN apt-get update \
 && apt-get install -y --no-install-recommends systemd iproute2 lsof pciutils usbutils pacman-package-manager \
 && rm -rf /var/lib/apt/lists/* \
 && useradd -u 1500 -U -m -d /home/admin -s /bin/bash admin \
 && find / -xdev -type f -perm /6000 -exec chmod ug-s {} +

COPY pacman-host /usr/local/bin/pacman-host
COPY pacman-host.conf /etc/pacman-host.conf
RUN chmod 0755 /usr/local/bin/pacman-host

ENV HOME=/home/admin \
    PATH=/home/admin/.local/bin:/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin \
    NPM_CONFIG_PREFIX=/home/admin/.local
USER admin
WORKDIR /proposals
