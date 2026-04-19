# Everand Unlock Credit Tracker

Tracks your Everand monthly unlock credits (3/month, rollover up to 6 months).
Run weekly via launchd or cron — alerts you when credits are about to expire.

## Requirements

- Python 3.11+
- macOS (primary target; Linux/Windows may work with adjustments to notification methods)

## Installation

### 1. Clone the repo

```bash
git clone https://github.com/yourname/everand-tracker.git
cd everand-tracker
```

### 2. Install dependencies

```bash
# Required for auto-scraping
pip install playwright
playwright install chromium

# Required for Keychain credential storage
pip install keyring

# Optional: desktop notifications via plyer
pip install plyer pyobjus

# Optional: email notifications
# (no extra packages needed — uses Python's built-in smtplib)
```

### 3. Store credentials in Keychain

Never put passwords in `config.json`. Store them in macOS Keychain instead:

```bash
# Your Everand account password
python -c "import keyring; keyring.set_password('everand_tracker', 'everand', 'your-everand-password')"

# Your email app password (if using email notifications — see below)
python -c "import keyring; keyring.set_password('everand_tracker', 'smtp', 'your-app-password')"
```

To update a password later, just run the same command with the new value — it overwrites the existing entry.

To verify entries were stored correctly:

```bash
security find-generic-password -s "everand_tracker" -a "everand"
security find-generic-password -s "everand_tracker" -a "smtp"
```

### 4. Create `config.json`

Copy the example config and edit it:

```bash
cp config.example.json config.json
```

See the Configuration section below for all available options.

### 5. Run first-time setup

```bash
python everand_tracker.py --setup
```

This seeds your initial credit balance. On first run with the scraper enabled,
a browser window will open for login — including MFA if your account requires it.
After that, the session is saved and subsequent runs are fully headless.

---

## Usage

```bash
# Normal weekly run (auto-scrape if configured)
python everand_tracker.py

# Manual credit count override (skip scraping)
python everand_tracker.py --credits 9

# Show current status without updating anything
python everand_tracker.py --status

# Generate and install a launchd plist for weekly scheduled runs
python everand_tracker.py --generate-plist

# Show cron/launchd scheduling instructions
python everand_tracker.py --schedule
```

---

## Configuration (`config.json`)

```json
{
  "notify_method": ["alerter"],
  "everand_email": "you@example.com",
  "use_scraper": true,
  "smtp_host": "smtp.gmail.com",
  "smtp_port": "587",
  "smtp_user": "you@example.com",
  "email_to": "you@example.com",
  "email_from": "you@example.com"
}
```

### `notify_method`

Accepts a string or a list — multiple methods can be combined:

```json
"notify_method": ["alerter", "email"]
```

| Method | Description |
|--------|-------------|
| `console` | Prints alert to stdout. Only useful for manual runs — output goes to the log file when running via launchd. |
| `alerter` | macOS Notification Center banner via the [`alerter`](https://github.com/vjeantet/alerter) CLI tool. Recommended for scheduled runs. |
| `desktop` | macOS notification via `plyer` + `pyobjus`. Requires those packages to be installed. |
| `email` | Sends an email via SMTP. Requires email keys in `config.json` and SMTP password in Keychain. |

### Email / SMTP setup

For Gmail, use an [App Password](https://myaccount.google.com/apppasswords) —
not your regular Google account password. Generate one at
**myaccount.google.com/apppasswords**, then store it in Keychain:

```bash
python -c "import keyring; keyring.set_password('everand_tracker', 'smtp', 'your-16-char-app-password')"
```

Do **not** add `smtp_password` to `config.json`.

---

## Notification methods

### alerter (recommended)

[alerter](https://github.com/vjeantet/alerter) is a CLI tool for macOS
Notification Center banners. Install it via Homebrew:

```bash
brew install alerter
```

The script automatically checks both `/opt/homebrew/bin/alerter` (Apple Silicon)
and `/usr/local/bin/alerter` (Intel) so it works across machines without
any configuration change.

You can use a custom icon by passing a local image path to `--appIcon` in the
`send_alerter_notification()` function. A good source for the Everand icon:

```
https://www.everand.com/apple-touch-icon.png
```

### desktop (plyer)

Requires:

```bash
pip install plyer pyobjus
```

Note: the notification may not appear if Python/Terminal doesn't have notification
permissions. Check **System Settings → Notifications** and grant permission if needed.

---

## How credits are tracked

Each month's batch of 3 credits is stored separately in `credits.json` with its
own expiry date (6 months from when it was earned). When credits are used, the
oldest batches are consumed first (FIFO).

```json
{
  "batches": [
    {
      "earned": "2026-01-01",
      "expires": "2026-07-01",
      "total": 3,
      "remaining": 1
    },
    {
      "earned": "2026-02-01",
      "expires": "2026-08-01",
      "total": 3,
      "remaining": 3
    }
  ],
  "last_known_count": 4,
  "last_run": "2026-04-18"
}
```

By default, you're alerted when any credit expires within **14 days**.
Change `ALERT_DAYS_BEFORE` at the top of the script to adjust.

---

## Auto-scraping

The scraper logs into Everand using Playwright and reads the credit count from
your account page. Credentials are retrieved from Keychain — never from
`config.json`.

### Session persistence

On first run (no `session.json` present), the browser opens in visible mode so
you can complete any MFA challenge. The script pauses and prompts you for the
6-digit code in the terminal. After successful login, the session is saved to
`session.json` and all future runs are fully headless with no MFA needed.

If the session expires, the script detects it, deletes `session.json`, and
prompts you to re-run to log in fresh.

### Scraper troubleshooting

If the credit count can't be parsed from the page, a `scraper_debug.html` file
is saved in the script directory so you can inspect the page source. The regex
pattern is in `scrape_credit_count()` — update it if Everand changes their markup.

---

## Scheduling (macOS)

### launchd (recommended)

Generate and install a plist that runs the script every Monday at 9am:

```bash
python everand_tracker.py --generate-plist
launchctl load ~/Library/LaunchAgents/com.everand.tracker.plist
```

To unload/reload after making changes to the plist:

```bash
launchctl unload ~/Library/LaunchAgents/com.everand.tracker.plist
launchctl load ~/Library/LaunchAgents/com.everand.tracker.plist
```

Logs are written to `everand_tracker.log` in the script directory.

### cron (alternative)

```bash
crontab -e
# Add:
0 9 * * 1 /usr/bin/python3 /path/to/everand_tracker.py
```

---

## Files

| File | Description | In git? |
|------|-------------|---------|
| `everand_tracker.py` | Main script | ✅ |
| `config.example.json` | Example config with placeholder values | ✅ |
| `config.json` | Your local config | ❌ `.gitignore` |
| `credits.json` | Credit state (auto-generated) | ❌ `.gitignore` |
| `session.json` | Playwright auth session (auto-generated) | ❌ `.gitignore` |
| `everand_tracker.log` | launchd output log (auto-generated) | ❌ `.gitignore` |
| `scraper_debug.html` | Debug output if scraping fails | ❌ `.gitignore` |

---

## Testing notifications

To test a notification method without waiting for the scheduled run, use this
shell function (add to `.zshrc`):

```bash
test_everand_alert() {
    python -c "
from everand_tracker import send_alerter_notification
send_alerter_notification([{'remaining': 2, 'earned': '2026-04-14', 'expires': '2026-10-14', 'days_left': 7}])
"
}
```

Swap `send_alerter_notification` for `send_desktop_notification`,
`send_email_alert`, or `print_console_alert` to test other methods.
