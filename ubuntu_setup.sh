#!/bin/bash

# ==========================================
# Configuration Variables (Modify these)
# ==========================================
PROJECT_DIR="/path/to/your/django_project"
PYTHON_BIN="/path/to/your/venv/bin/python" # Or /usr/bin/python3
PORT="8000"
SERVICE_NAME="django_webui"
SHORTCUT_NAME="Django Web UI"

# ==========================================
# Pre-flight Checks
# ==========================================
if [ "$EUID" -ne 0 ]; then
  echo "Error: Please run as root (use sudo)"
  exit 1
fi

# Determine actual user to create desktop shortcut in the correct home directory
if [ -n "$SUDO_USER" ]; then
    ACTUAL_USER=$SUDO_USER
    USER_HOME=$(getent passwd "$SUDO_USER" | cut -d: -f6)
else
    ACTUAL_USER=$(whoami)
    USER_HOME=$HOME
fi

echo "Deploying $SERVICE_NAME..."

# ==========================================
# 1. Systemd Service Setup (Idempotent)
# ==========================================
SERVICE_FILE="/etc/systemd/system/${SERVICE_NAME}.service"

cat << EOF > "$SERVICE_FILE"
[Unit]
Description=Django Web UI Development Server
After=network.target

[Service]
User=$ACTUAL_USER
WorkingDirectory=$PROJECT_DIR
ExecStart=$PYTHON_BIN manage.py runserver 0.0.0.0:$PORT
Restart=always
RestartSec=3

[Install]
WantedBy=multi-user.target
EOF

echo "Reloading systemd daemon and starting service..."
systemctl daemon-reload
systemctl enable "$SERVICE_NAME"
systemctl restart "$SERVICE_NAME"

# ==========================================
# 2. Desktop Shortcut Setup (Idempotent)
# ==========================================
DESKTOP_DIR="$USER_HOME/Desktop"
mkdir -p "$DESKTOP_DIR"
SHORTCUT_FILE="$DESKTOP_DIR/$SHORTCUT_NAME.desktop"

cat << EOF > "$SHORTCUT_FILE"
[Desktop Entry]
Version=1.0
Name=$SHORTCUT_NAME
Comment=Open Django Web UI in Browser
Exec=xdg-open http://localhost:$PORT
Icon=web-browser
Terminal=false
Type=Application
Categories=Network;WebBrowser;
EOF

# Ensure proper permissions for the shortcut
chown "$ACTUAL_USER:$ACTUAL_USER" "$SHORTCUT_FILE"
chmod +x "$SHORTCUT_FILE"

echo "Setup complete! The service is running on port $PORT."
echo "You can check logs via: sudo journalctl -u $SERVICE_NAME -f"