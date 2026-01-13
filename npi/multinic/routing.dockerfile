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

CMD ["/bin/bash", "-c", "IP_ETH1=$(ip -4 addr show eth1 | grep -oP '(?<=inet\\s)\\d+(\\.\\d+){3}') && GW_ETH1=$(echo $IP_ETH1 | awk -F. '{print $1\".\"$2\".\"$3\".1\"}') && ip route add default via $GW_ETH1 dev eth1 table 100 && ip rule add from $IP_ETH1 lookup 100"]
