#!/bin/bash
set -e

# Automation script to configure Systemd service for the Benchmark Dashboard
SCRIPT_DIR="$( cd "$( dirname "${BASH_SOURCE[0]}" )" &> /dev/null && pwd )"
SERVICE_NAME="benchmark-dashboard"
SERVICE_FILE="/etc/systemd/system/${SERVICE_NAME}.service"
CURRENT_USER=$(whoami)

echo "Setting permissions on startup script..."
chmod +x "${SCRIPT_DIR}/start_server.sh"

# Detect and propagate active environment variables
ENV_LINES=""
if [ -n "$DASHBOARD_BUCKET" ]; then
    ENV_LINES="Environment=\"DASHBOARD_BUCKET=${DASHBOARD_BUCKET}\"
"
fi
if [ -n "$DASHBOARD_PASSWORD" ]; then
    ENV_LINES="${ENV_LINES}Environment=\"DASHBOARD_PASSWORD=${DASHBOARD_PASSWORD}\""
fi

echo "Creating systemd service file at ${SERVICE_FILE}..."
sudo bash -c "cat > ${SERVICE_FILE}" <<EOF
[Unit]
Description=GCSFuse Benchmark Dashboard
After=network.target

[Service]
Type=simple
User=${CURRENT_USER}
WorkingDirectory=${SCRIPT_DIR}
ExecStart=${SCRIPT_DIR}/start_server.sh
Restart=always
RestartSec=5
${ENV_LINES}
StandardOutput=append:${SCRIPT_DIR}/server.log
StandardError=append:${SCRIPT_DIR}/server.log

[Install]
WantedBy=multi-user.target
EOF

echo "Reloading systemd daemon..."
sudo systemctl daemon-reload

echo "Enabling and starting ${SERVICE_NAME} service..."
sudo systemctl enable ${SERVICE_NAME}
sudo systemctl restart ${SERVICE_NAME}

echo "--------------------------------------------------------"
echo "✓ Systemd service successfully installed and started!"
echo "Check status using: sudo systemctl status ${SERVICE_NAME}"
echo "View logs using: journalctl -u ${SERVICE_NAME} -n 50 -f"
echo "--------------------------------------------------------"
