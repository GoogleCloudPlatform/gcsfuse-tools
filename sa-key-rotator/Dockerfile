FROM google/cloud-sdk:alpine
RUN apk add --no-cache bash jq util-linux
WORKDIR /workspace
COPY rotate_sa_keys.sh .
RUN chmod +x rotate_sa_keys.sh
ENTRYPOINT ["/workspace/rotate_sa_keys.sh"]
