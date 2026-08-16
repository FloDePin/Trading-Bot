#!/bin/bash
# ─────────────────────────────────────────────────────────────
#  Trading Platform v1 – Universelles Raspberry Pi / Linux Setup
#  Ausfuehren als:  bash setup.sh      (fragt bei Bedarf nach sudo)
#            oder:  sudo bash setup.sh
#
#  Erkennt den echten Benutzer automatisch (auch unter sudo) und
#  richtet den systemd-Dienst dafuer ein – egal wie der Nutzer heisst.
# ─────────────────────────────────────────────────────────────

set -e  # Bei Fehler sofort abbrechen

# --- Echten Ziel-Benutzer + Home ermitteln --------------------
# Unter "sudo bash setup.sh" ist $USER=root; SUDO_USER haelt dann den
# echten Anmeldenamen. Ohne sudo greift $USER.
TARGET_USER="${SUDO_USER:-$USER}"
TARGET_HOME="$(getent passwd "$TARGET_USER" | cut -d: -f6)"
[ -z "$TARGET_HOME" ] && TARGET_HOME="$HOME"

INSTALL_DIR="$TARGET_HOME/trading"
SERVICE_NAME="trading-platform"
SERVICE_FILE="/etc/systemd/system/${SERVICE_NAME}.service"
PYTHON="$(command -v python3)"

# Hilfsfunktion: privilegierte Befehle mit sudo, falls nicht schon root
SUDO=""
[ "$(id -u)" -ne 0 ] && SUDO="sudo"

echo ""
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
echo "  Trading Platform v1 – Setup"
echo "  Benutzer: $TARGET_USER   Ziel: $INSTALL_DIR"
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
echo ""

# 1. Verzeichnis anlegen
echo "[1/5] Installationsverzeichnis anlegen..."
mkdir -p "$INSTALL_DIR"
echo "      -> $INSTALL_DIR"

# 2. Python-Abhaengigkeiten
echo "[2/5] Python-Pakete pruefen/installieren..."
if python3 -c "import requests" 2>/dev/null; then
    echo "      -> requests bereits vorhanden"
else
    $SUDO apt-get update -qq || true
    $SUDO apt-get install -y -qq python3-pip || true
    # Neuere Pi-OS (PEP 668) brauchen --break-system-packages; aeltere kennen es nicht.
    pip3 install requests --break-system-packages -q 2>/dev/null \
        || pip3 install requests -q \
        || $SUDO apt-get install -y -qq python3-requests
    echo "      -> requests installiert"
fi

# 3. platform.py kopieren (robust gegen Quelle==Ziel)
echo "[3/5] Plattform-Datei pruefen..."
if [ -f "./platform.py" ]; then
    SRC="$(readlink -f ./platform.py)"
    DST="$(readlink -f "$INSTALL_DIR/platform.py" 2>/dev/null || echo "$INSTALL_DIR/platform.py")"
    if [ "$SRC" = "$DST" ]; then
        echo "      -> platform.py liegt bereits am Ziel (kein Kopieren noetig)"
    else
        cp ./platform.py "$INSTALL_DIR/platform.py"
        echo "      -> platform.py kopiert nach $INSTALL_DIR"
    fi
else
    echo "      WARN: platform.py nicht im aktuellen Verzeichnis gefunden."
    echo "      Bitte platform.py manuell nach $INSTALL_DIR kopieren."
fi

# 4. systemd-Service fuer den echten Benutzer schreiben
echo "[4/5] systemd-Service einrichten (User=$TARGET_USER)..."
$SUDO bash -c "cat > '$SERVICE_FILE'" << EOF
[Unit]
Description=Trading Platform v1 - Multi Bot (Signal, Grid, Funding, DCA)
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
User=${TARGET_USER}
WorkingDirectory=${INSTALL_DIR}
ExecStart=${PYTHON} ${INSTALL_DIR}/platform.py
Restart=on-failure
RestartSec=15
StandardOutput=append:${INSTALL_DIR}/platform.log
StandardError=append:${INSTALL_DIR}/platform.log
Environment=PYTHONUNBUFFERED=1

[Install]
WantedBy=multi-user.target
EOF

$SUDO systemctl daemon-reload
$SUDO systemctl enable "$SERVICE_NAME" >/dev/null 2>&1 || true
# Rechte dem echten Benutzer geben (falls Dateien unter root angelegt wurden)
$SUDO chown -R "$TARGET_USER":"$TARGET_USER" "$INSTALL_DIR"
echo "      -> Service registriert, Autostart aktiv, Rechte gesetzt"

# 5. Netzwerk-Info
echo "[5/5] Netzwerk-Info..."
LOCAL_IP="$(hostname -I | awk '{print $1}')"
echo "      -> Pi-IP im Netzwerk: $LOCAL_IP"

echo ""
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
echo "  Setup abgeschlossen!"
echo ""
echo "  Plattform starten:  sudo systemctl start $SERVICE_NAME"
echo "  Status pruefen:     sudo systemctl status $SERVICE_NAME"
echo "  Live-Log:           tail -f ${INSTALL_DIR}/platform.log"
echo ""
echo "  Dann im Browser oeffnen:"
echo "  -> http://${LOCAL_IP}:5000"
echo "  -> http://$(hostname).local:5000  (falls mDNS aktiv)"
echo ""
echo "  ERSTER START: Das Dashboard zeigt einen Setup-Assistenten."
echo "  Dort legst du Benutzername + Passwort SELBST fest."
echo "  (Kein Passwort mehr im Log - das ist gewollt.)"
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
