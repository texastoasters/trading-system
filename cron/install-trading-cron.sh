# Installing Trading System Cron Jobs (System Cron)
# =================================================
# Host: openboog (Vultr VPS, Ubuntu 24.04)
# User: linuxuser

# 1. VERIFY CRONIE IS INSTALLED (required for CRON_TZ support)
#    The cron file uses CRON_TZ=America/New_York so all times are ET.
#    This requires cronie; Vixie cron does not support CRON_TZ.

sudo systemctl status crond 2>/dev/null || sudo systemctl status cron
# Should show "active (running)". If Vixie cron is running instead:
#   sudo apt install cronie
#   sudo systemctl disable cron && sudo systemctl enable --now crond

# 2. COPY THE CRON FILE INTO PLACE
#    Files in /etc/cron.d/ must:
#    - Be owned by root
#    - Have permissions 0644 (not executable!)
#    - Not contain dots in the filename (e.g., "trading.system" won't run)
#    - Have a newline at the end of the file

sudo cp trading-system-cron /etc/cron.d/trading-system
sudo chown root:root /etc/cron.d/trading-system
sudo chmod 0644 /etc/cron.d/trading-system

# 3. VALIDATE THE FILE
#    Check for syntax issues. Cron is silent about bad files — it just
#    ignores them. These checks catch the common mistakes:

# Verify ownership and permissions
ls -la /etc/cron.d/trading-system
# Expected: -rw-r--r-- 1 root root ... /etc/cron.d/trading-system

# Verify the file ends with a newline (cron silently drops the last
# line if it doesn't):
tail -c 1 /etc/cron.d/trading-system | xxd | head -1
# Should contain "0a" (newline). If not: echo "" | sudo tee -a /etc/cron.d/trading-system

# Verify no BOM or weird encoding:
file /etc/cron.d/trading-system
# Expected: ASCII text (or UTF-8 Unicode text)

# 4. VERIFY CRON DAEMON IS RUNNING

sudo systemctl status crond
# Should show "active (running)"
# If not: sudo systemctl enable --now crond

# 5. VERIFY .trading_env IS SOURCEABLE BY LINUXUSER
#    The cron jobs run as linuxuser and source this file. Make sure
#    it exists, is readable, and exports the required vars:

sudo -u linuxuser bash -c '. /home/linuxuser/.trading_env && echo "TELEGRAM_BOT_TOKEN=${TELEGRAM_BOT_TOKEN:0:10}..." && echo "TELEGRAM_CHAT_ID=$TELEGRAM_CHAT_ID" && echo "ALPACA_API_KEY=${ALPACA_API_KEY:0:10}..."'
# All three should print values, not blanks

# 6. TEST A JOB MANUALLY (as linuxuser, simulating what cron will do)
#    This runs the health check exactly as cron would:

sudo -u linuxuser bash -c '. /home/linuxuser/.trading_env && cd /home/linuxuser/trading-system && PYTHONPATH=/home/linuxuser/trading-system/scripts python3 skills/supervisor/supervisor.py --health'
# Should produce health check output with no import errors

# 7. REMOVE THE CORRESPONDING OPENCLAW CRON JOBS
#    Now that system cron handles these, remove them from OpenClaw
#    to avoid double-execution:

openclaw cron remove --name "trading-daily-reset"
openclaw cron remove --name "trading-screener-scan"
openclaw cron remove --name "trading-watcher-cycle"
openclaw cron remove --name "trading-health-check"
openclaw cron remove --name "trading-discovery"

# Verify only the AI jobs remain:
openclaw cron list
# Should show only:
#   trading-eod-review      (30 16 * * 1-5 ET)
#   trading-revalidation    (0 6 1 * * ET)

# 8. MONITOR THE FIRST RUN
#    Watch syslog for the tagged output when the next job fires:

# Live tail (the logger -t tags make filtering easy):
sudo journalctl -t trading-health -f
# or for all trading tags:
sudo journalctl -t trading-reset -t trading-screener -t trading-watcher -t trading-health -t trading-discovery --since "today" -f

# 9. DST CHANGEOVER
#    No action needed. The cron file uses CRON_TZ=America/New_York and
#    cronie handles DST transitions automatically. All times in the file
#    are ET and remain correct year-round.
