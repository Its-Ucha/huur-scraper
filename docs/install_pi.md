# Raspberry Pi setup

## 1) Copy project to Pi
Place this repository anywhere accessible to the user that will run the scraper.
The examples below use `/home/pi/huur_scraper`; substitute your checkout path.

## 2) Create environment and install systemd units
```bash
cd /home/pi/huur_scraper
chmod +x scripts/setup_pi.sh
./scripts/setup_pi.sh
```

Run the script as your normal user, **not** with `sudo`. It prompts for sudo
access to install the units; the virtual environment remains owned by your user.
The script:
- Creates `.venv` and installs dependencies.
- Renders the service with the actual checkout's absolute path and your user/group.
- Installs both units into `/etc/systemd/system/` and reloads systemd.

The service in `deploy/systemd/` is a template, not a unit to copy directly.
Re-run setup if you move the checkout or want to update the installed units.
The timer is not enabled automatically, so you can configure and test first.

## 3) Configure env
```bash
cp .env.example .env
nano .env
```
Set at least your matching profile and (optionally) Telegram.

Recommended logging values in `.env`:
- `LOG_LEVEL=INFO`
- `LOG_FILE_PATH=logs/huur_scraper.log`
- `LOG_TO_CONSOLE=true`

## 4) Test once manually
```bash
/home/pi/huur_scraper/.venv/bin/python -m src.main --once
```

## 5) Enable the timer
```bash
sudo systemctl enable --now huur-scraper.timer
```

## 6) Observe runtime logs
```bash
journalctl -u huur-scraper.service -f
tail -f /home/pi/huur_scraper/logs/huur_scraper.log
```

## Useful checks
```bash
systemctl status huur-scraper.timer
systemctl list-timers | grep huur-scraper
```
