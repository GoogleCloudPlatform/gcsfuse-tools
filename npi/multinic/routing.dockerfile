# Dockerfile for a privileged init-container to configure routing.
# It routes Private Google Access VIPs through eth1.

FROM alpine:latest

# Install necessary packages:
# - iproute2: for 'ip' command
# - grep: for 'grep -P' (PCRE support)
# - gawk: for 'awk'
RUN apk add --no-cache \
    bash \
    iproute2 \
    grep \
    gawk

# 1. Get Gateway IP from eth1
# 2. Route Private Google Access VIPs through eth1
CMD ["/bin/bash", "-c", "GW_IP=$(ip -4 addr show eth1 | grep -oP '(?<=inet\\s)\\d+(\\.\\d+){3}' | awk -F. '{print $1\".\"$2\".\"$3\".1\"}') && ip route add 199.36.153.8/30 via $GW_IP dev eth1"]
