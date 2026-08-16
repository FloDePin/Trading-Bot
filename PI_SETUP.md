# 🍓 Raspberry Pi – Installation Guide

> 🇩🇪 Deutsche Version: [PI_SETUP.de.md](PI_SETUP.de.md)

This installs the **Trading Platform v1** as a permanent background service on a
Raspberry Pi (or any other Linux machine). Works with **any username** – the
setup detects your user automatically.

You only need two files: `platform.py` and `setup.sh`.

---

## Step 1 – Copy the files to the Pi

Open a terminal **on your PC** (CMD/PowerShell/Terminal) and change into the
folder that contains `platform.py` and `setup.sh`. Then:

```bash
# Replace YOUR_USER and PI_IP, e.g.  pihole@192.168.178.28
scp platform.py setup.sh YOUR_USER@PI_IP:~
```

> Find your Pi's IP on the Pi with `hostname -I`.

---

## Step 2 – SSH in and run the setup

```bash
ssh YOUR_USER@PI_IP
```

Then on the Pi:

```bash
# 1) Strip invisible Windows line endings (important if copied from Windows!)
sed -i 's/\r$//' setup.sh platform.py

# 2) Make the setup executable and run it
chmod +x setup.sh
sudo bash setup.sh
```

The script:
- auto-detects your username (even under `sudo`),
- installs the Python dependency `requests`,
- creates the `trading-platform` systemd service for **your** user,
- fixes file permissions.

It ends with **"Setup abgeschlossen!"** and prints your Pi's IP address.

---

## Step 3 – Start the platform

```bash
sudo systemctl start trading-platform
```

Check status (should read `active (running)`):

```bash
sudo systemctl status trading-platform
```

---

## Step 4 – Open the dashboard & choose your own login

Open in a browser (PC or phone):

```
http://PI_IP:5000
```

> ⚠️ **New in v1.2:** No password is generated in the log anymore.
> Instead, the very first visit shows a **setup wizard**.

There you set **yourself**:
- **Username** (your choice, default `admin`)
- **Password** (at least 8 characters, entered twice for safety)

Click **Save & log in**. The browser then asks once for exactly these
credentials (HTTP Basic Auth) – done, you're in the dashboard.

> 💡 Tip: Set up the login **right after** starting. Until you do, the
> dashboard is unprotected on your local network (first visitor sets the
> password). Harmless on a home network, but don't leave it open for long.

---

## Step 5 – First settings

In the dashboard:

1. Open the **SETTINGS** tab.
2. Confirm the **trading mode** is set to **DEMO** (paper trading).
3. Enter your **Bitget API keys** (and optionally Telegram/Discord).
4. Click **SAVE SETTINGS** at the bottom.
5. Go to a bot tab (e.g. **GRID** or **SIGNAL**) and click **START**.

Happy paper trading! 🎉

---

## 🛠️ Handy commands (cheat sheet)

| Action              | Command                                       |
|---------------------|-----------------------------------------------|
| Start               | `sudo systemctl start trading-platform`       |
| Stop                | `sudo systemctl stop trading-platform`        |
| Restart             | `sudo systemctl restart trading-platform`     |
| Status              | `sudo systemctl status trading-platform`      |
| Live log            | `tail -f ~/trading/platform.log`              |
| Last 100 lines      | `tail -100 ~/trading/platform.log`            |

**Updating the bot:** copy the new `platform.py` into `~/trading/`, then
`sudo systemctl restart trading-platform`. Your settings
(`platform_config.json`) are **preserved**.

---

## ❓ Troubleshooting

| Terminal error | Cause & fix |
|----------------|-------------|
| `$'\r': command not found` | Windows line endings. Fix: `sed -i 's/\r$//' setup.sh platform.py` and re-run. |
| `pip3: command not found` | Rare – the new `setup.sh` installs `python3-pip` automatically. Otherwise: `sudo apt install -y python3-pip`. |
| `Permission denied` on the log | Files owned by root. Fix: `sudo chown -R $USER:$USER ~/trading` and restart. |
| Service starts but **dashboard unreachable** | Usually the wrong user in the service. The new `setup.sh` fixes this automatically (uses your real user). Check with `sudo systemctl status trading-platform`. |
| Forgot password / reset it | Stop the service, set `"dashboard_password"` to `""` in `~/trading/platform_config.json`, start the service → the setup wizard appears again. |

**Static IP recommended:** assign your Pi a fixed IP in your router so the
dashboard address never changes.
