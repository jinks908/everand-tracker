#!/usr/bin/env python3
"""
Everand Unlock Credit Tracker
------------------------------
Tracks your monthly unlock credits (3/month, rollover up to 6 months).
Run weekly via cron or Task Scheduler.

Usage:
  python everand_tracker.py              # Auto-scrape (requires Playwright setup)
  python everand_tracker.py --credits 9  # Manual credit count override
  python everand_tracker.py --status     # Print current state without updating
  python everand_tracker.py --setup      # Interactive first-time setup
"""

import argparse
import json
import smtplib
import sys
import keyring
from datetime import date, timedelta
from email.mime.text import MIMEText
from pathlib import Path


#:#  Configuration
#;# ─────────────────────────────────────────────────────────────── #

STATE_FILE = Path(__file__).parent / "credits.json"
CONFIG_FILE = Path(__file__).parent / "config.json"

CREDITS_PER_MONTH = 3
ROLLOVER_MONTHS = 6
ALERT_DAYS_BEFORE = 14  # Warn when a credit expires within this many days


#:#  State Management
#;# ─────────────────────────────────────────────────────────────── #
def load_state() -> dict:
    if STATE_FILE.exists():
        with open(STATE_FILE) as f:
            return json.load(f)
    return {"batches": [], "last_known_count": 0, "last_run": None}


def save_state(state: dict):
    with open(STATE_FILE, "w") as f:
        json.dump(state, f, indent=2, default=str)


def load_config() -> dict:
    if CONFIG_FILE.exists():
        with open(CONFIG_FILE) as f:
            return json.load(f)
    return {}


def save_config(config: dict):
    with open(CONFIG_FILE, "w") as f:
        json.dump(config, f, indent=2)


#:#  Credit Count Logic
#;# ─────────────────────────────────────────────────────────────── #
def total_active_credits(state: dict, today: date) -> int:
    """Sum of credits in all non-expired batches."""
    return sum(
        b["remaining"]
        for b in state["batches"]
        if date.fromisoformat(b["expires"]) >= today and b["remaining"] > 0
    )


## Update Credit Count (Reconcile)
def reconcile(state: dict, new_count: int, today: date) -> list[str]:
    """
    Compare new_count to the tracked state and update batches.
    Returns a list of human-readable log messages.
    """
    logs = []
    old_count = total_active_credits(state, today)
    delta = new_count - old_count

    # Expire old batches
    for b in state["batches"]:
        if date.fromisoformat(b["expires"]) < today and b["remaining"] > 0:
            logs.append(f"⚠️  Batch from {b['earned']} expired with {b['remaining']} unused credit(s).")
            b["remaining"] = 0

    if delta > 0:
        # Credits increased — new batch(es) arrived
        new_batches = delta // CREDITS_PER_MONTH
        leftover = delta % CREDITS_PER_MONTH
        for i in range(new_batches):
            expiry = today + timedelta(days=30 * ROLLOVER_MONTHS)
            state["batches"].append({
                "earned": today.isoformat(),
                "expires": expiry.isoformat(),
                "total": CREDITS_PER_MONTH,
                "remaining": CREDITS_PER_MONTH,
            })
            logs.append(f"✅  Added batch of {CREDITS_PER_MONTH} credits earned {today}, expires {expiry}.")
        if leftover:
            # Partial batch — unusual but handle it gracefully
            expiry = today + timedelta(days=30 * ROLLOVER_MONTHS)
            state["batches"].append({
                "earned": today.isoformat(),
                "expires": expiry.isoformat(),
                "total": leftover,
                "remaining": leftover,
            })
            logs.append(f"✅  Added partial batch of {leftover} credit(s) earned {today}, expires {expiry}.")

    elif delta < 0:
        # Credits were used — drain oldest batches first (FIFO)
        to_drain = abs(delta)
        for b in sorted(state["batches"], key=lambda x: x["earned"]):
            if to_drain == 0:
                break
            if b["remaining"] == 0:
                continue
            used = min(b["remaining"], to_drain)
            b["remaining"] -= used
            to_drain -= used
            logs.append(f"📖  Used {used} credit(s) from batch earned {b['earned']}.")

    state["last_known_count"] = new_count
    state["last_run"] = today.isoformat()
    return logs


## Check Expiration(s)
def check_expiring(state: dict, today: date) -> list[dict]:
    """Return batches with credits expiring within ALERT_DAYS_BEFORE days."""
    warnings = []
    for b in state["batches"]:
        exp = date.fromisoformat(b["expires"])
        days_left = (exp - today).days
        if 0 <= days_left <= ALERT_DAYS_BEFORE and b["remaining"] > 0:
            warnings.append({**b, "days_left": days_left})
    return sorted(warnings, key=lambda x: x["days_left"])


#:#  Display
#;# ─────────────────────────────────────────────────────────────── #
def print_status(state: dict, today: date):
    active = [
        b for b in state["batches"]
        if date.fromisoformat(b["expires"]) >= today and b["remaining"] > 0
    ]
    total = sum(b["remaining"] for b in active)

    print(f"\n{'═' * 52}")
    print(f"  Everand Unlock Credits — {today}")
    print(f"{'═' * 52}")
    print(f"  Total available: {total} credit(s)\n")

    if active:
        print(f"  {'Earned':<14} {'Expires':<14} {'Remaining':<10} {'Status'}")
        print(f"  {'─'*14} {'─'*14} {'─'*10} {'─'*16}")
        for b in sorted(active, key=lambda x: x["expires"]):
            exp = date.fromisoformat(b["expires"])
            days_left = (exp - today).days
            if days_left <= 7:
                status = f"🔴 {days_left}d left"
            elif days_left <= ALERT_DAYS_BEFORE:
                status = f"🟡 {days_left}d left"
            else:
                status = f"🟢 {days_left}d left"
            print(f"  {b['earned']:<14} {b['expires']:<14} {b['remaining']:<10} {status}")
    else:
        print("  No active credits.")

    print(f"{'═' * 52}\n")


#:#  Notifications
#;# ─────────────────────────────────────────────────────────────── #
## Email Notification
def send_email_alert(warnings: list[dict], config: dict):
    if not config.get("email_to"):
        print("⚠️  Email not configured. Printing alert to console instead.")
        print_console_alert(warnings)
        return

    lines = ["Your Everand unlock credits are expiring soon:\n"]
    for w in warnings:
        lines.append(f"  • {w['remaining']} credit(s) from {w['earned']} — expires {w['expires']} ({w['days_left']} days left)")
    lines.append("\nLog in to https://www.everand.com and use them before they're gone!")
    body = "\n".join(lines)

    msg = MIMEText(body)
    msg["Subject"] = f"⏰ Everand credits expiring soon ({warnings[0]['days_left']}d)"
    msg["From"] = config.get("email_from", config["email_to"])
    msg["To"] = config["email_to"]

    try:
        host = config.get("smtp_host", "localhost")
        port = int(config.get("smtp_port", 25))

        with smtplib.SMTP(host, port) as server:
            if config.get("smtp_user"):
                server.starttls()
                server.login(config["smtp_user"], keyring.get_password("everand_tracker", "smtp"))
            server.send_message(msg)
        print(f"📧  Alert email sent to {config['email_to']}.")
    except Exception as e:
        print(f"❌  Failed to send email: {e}")
        print_console_alert(warnings)


## Desktop Notification
def send_desktop_notification(warnings: list[dict]):
    try:
        from plyer import notification
        summary = f"{sum(w['remaining'] for w in warnings)} credit(s) expiring soon"
        detail = "; ".join(f"{w['remaining']} expire {w['expires']}" for w in warnings)
        notification.notify(
            title="Everand Credits Expiring",
            message=f"{summary}\n{detail}",
            app_name="Everand Tracker",
            timeout=10,
        )
        print("🔔  Desktop notification sent.")
    except ImportError:
        print("ℹ️   Install 'plyer' for desktop notifications: pip install plyer")
        print_console_alert(warnings)
    except Exception as e:
        print(f"❌  Desktop notification failed: {e}")
        print_console_alert(warnings)


## Console Alert
def print_console_alert(warnings: list[dict]):
    print("\n" + "!" * 52)
    print("  ⏰  EXPIRING CREDITS ALERT")
    print("!" * 52)
    for w in warnings:
        print(f"  {w['remaining']} credit(s) from {w['earned']} expire in {w['days_left']} day(s) ({w['expires']})")
    print("  → Go to https://www.everand.com and use them!\n")


## Alerter Notification (macOS)
def send_alerter_notification(warnings: list[dict]):
    import subprocess

    alerter_path = None
    for candidate in ["/opt/homebrew/bin/alerter", "/usr/local/bin/alerter", "alerter"]:
        if Path(candidate).is_file() or candidate == "alerter":
            alerter_path = candidate
            break

    icon = Path(__file__).parent / "everand_icon.png"
    summary = f"{sum(w['remaining'] for w in warnings)} unlock credit(s) expiring soon"
    detail = "; ".join(f"{w['remaining']} expire {w['expires']} ({w['days_left']}d)" for w in warnings)
    try:
        subprocess.run(
            [alerter_path, "--title", "Everand Credits", "--message", detail, "--subtitle", summary, "--app-icon", str(icon)],
            check=True
        )
        print("🔔  Alerter notification sent.")
    except FileNotFoundError:
        print("❌  alerter not found in /opt/homebrew/bin or /usr/local/bin.")
    except subprocess.CalledProcessError as e:
        print(f"❌  alerter failed: {e}")


## Notification Dispatcher
def notify(warnings: list[dict], config: dict):
    if not warnings:
        return
    methods = config.get("notify_method", "console")
    if isinstance(methods, str):
        methods = [methods]
    for method in methods:
        if method == "email":
            send_email_alert(warnings, config)
        elif method == "desktop":
            send_desktop_notification(warnings)
        elif method == "alerter":
            send_alerter_notification(warnings)
        else:
            print_console_alert(warnings)


#:#  Playwright Scraper
#;# ─────────────────────────────────────────────────────────────── #
def scrape_credit_count(config: dict) -> int | None:
    """
    Log into Everand and scrape the current credit count.
    Requires: pip install playwright && playwright install chromium
    Also requires everand_email and everand_password in config.json.

    Everand uses Auth0 via auth.scribd.com with a dynamic `state` param,
    so we navigate to the homepage first and click the sign-in button to
    let their redirect flow generate the correct auth URL naturally.
    """
    try:
        from playwright.sync_api import sync_playwright, TimeoutError as PWTimeout
    except ImportError:
        print("ℹ️   Playwright not installed. Run: pip install playwright && playwright install chromium")
        return None

    email = config.get("everand_email")
    password = keyring.get_password('everand_tracker', 'everand')
    if not email or not password:
        print("ℹ️   Everand credentials not in config.json (everand_email / everand_password).")
        return None

    print("🌐  Logging into Everand to fetch credit count...")

    # Create a session file path for Playwright to save auth state (cookies/localStorage)
    SESSION_FILE = Path(__file__).parent / "session.json"
    try:

        with sync_playwright() as p:
            has_session = SESSION_FILE.exists()
            browser = p.chromium.launch(headless=has_session)
            context = (
                browser.new_context(storage_state=str(SESSION_FILE))
                if has_session
                else browser.new_context(
                    user_agent=(
                        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                        "AppleWebKit/537.36 (KHTML, like Gecko) "
                        "Chrome/124.0.0.0 Safari/537.36"
                    )
                )
            )
            page = context.new_page()

            if has_session:
                # Saved session — go straight to account page
                print("  → Using saved session...")
                page.goto(
                    "https://www.everand.com/your-account",
                    wait_until="domcontentloaded",
                    timeout=30000,
                )
                # If session expired, we'll land back on a login page
                if "everand.com" not in page.url or "login" in page.url or "auth.scribd" in page.url:
                    print("  → Session expired, deleting and re-run to log in fresh.")
                    SESSION_FILE.unlink(missing_ok=True)
                    context.close()
                    browser.close()
                    return None
            else:
                # First run — full login flow with MFA handling
                print("  → No saved session. Opening browser for first-time login...")
                page.goto("https://www.everand.com", wait_until="domcontentloaded", timeout=45000)

                for selector in [
                    'a[href*="login"]',
                    'button:has-text("Sign in")',
                    'a:has-text("Sign in")',
                    'a:has-text("Log in")',
                ]:
                    try:
                        page.wait_for_selector(selector, timeout=5000)
                        page.click(selector)
                        break
                    except PWTimeout:
                        continue

                print("  → Waiting for auth page...")
                page.wait_for_url("**/auth.scribd.com/**", timeout=20000)
                page.wait_for_load_state("domcontentloaded")

                page.wait_for_selector('#username', timeout=10000)
                page.fill('#username', email)
                page.wait_for_selector('#password', timeout=8000)
                page.fill('#password', password)

                with page.expect_navigation(url="**everand.com**", wait_until="commit", timeout=30000):
                    page.click('button[type="submit"]')

                # Check if MFA challenge appeared instead of redirect
                if "mfa" in page.url or "auth.scribd.com" in page.url:
                    print("  → MFA challenge detected.")
                    code = input("  → Enter the 6-digit code from your email: ").strip()
                    page.wait_for_selector('input[name="code"], input[autocomplete="one-time-code"]', timeout=10000)
                    page.fill('input[name="code"], input[autocomplete="one-time-code"]', code)
                    with page.expect_navigation(url="**everand.com**", wait_until="commit", timeout=30000):
                        page.click('button[type="submit"]')

                print("  → Logged in. Navigating to account page...")
                page.goto(
                    "https://www.everand.com/your-account",
                    wait_until="domcontentloaded",
                    timeout=30000,
                )

                # Save session for future runs
                context.storage_state(path=str(SESSION_FILE))
                print(f"  → Session saved to {SESSION_FILE.name}. Future runs won't need MFA.")

            page.wait_for_timeout(3000)
            content = page.content()
            context.close()
            browser.close()

            # Parse credit count from page source
            import re
            # Unlock credit count pattern
            patterns = [
                r'(\d+)\s+unlocks?\s+available',
            ]

            for pattern in patterns:
                match = re.search(pattern, content, re.IGNORECASE)
                if match:
                    count = int(match.group(1))
                    print(f"✅  Scraped credit count: {count}")
                    return count

            print("⚠️  Could not parse credit count from page. Selector may need updating.")
            print("    Save the page HTML to debug: check scraper_debug.html")
            with open(Path(__file__).parent / "scraper_debug.html", "w") as f:
                f.write(content)
            return None

    except Exception as e:
        print(f"❌  Scraper error: {e}")
        return None


#:#  Setup Wizard
#;# ─────────────────────────────────────────────────────────────── #
def run_setup():
    print("\n🔧  Everand Tracker — First-time setup\n")
    config = load_config()

    config["notify_method"] = input("Notification method [console / email / desktop] (default: console): ").strip() or "console"

    if config["notify_method"] == "email":
        config["email_to"] = input("Send alerts to email: ").strip()
        config["email_from"] = input(f"From address (default: {config['email_to']}): ").strip() or config["email_to"]
        config["smtp_host"] = input("SMTP host (default: localhost): ").strip() or "localhost"
        config["smtp_port"] = input("SMTP port (default: 25, use 587 for Gmail): ").strip() or "25"
        use_auth = input("SMTP requires login? [y/N]: ").strip().lower() == "y"
        if use_auth:
            config["smtp_user"] = input("SMTP username: ").strip()
            config["smtp_password"] = input("SMTP password: ").strip()

    use_scraper = input("\nEnable auto-scraping from Everand? [y/N]: ").strip().lower() == "y"
    if use_scraper:
        config["everand_email"] = input("Everand email: ").strip()
        config["everand_password"] = input("Everand password: ").strip()
        config["use_scraper"] = True
        print("  → Run: pip install playwright && playwright install chromium")
    else:
        config["use_scraper"] = False

    initial = input("\nHow many credits do you have right now? (enter a number): ").strip()
    if initial.isdigit():
        today = date.today()
        state = load_state()
        logs = reconcile(state, int(initial), today)
        save_state(state)
        for log in logs:
            print(" ", log)

    save_config(config)
    print("\n✅  Setup complete! Run the script weekly to keep your credits tracked.")
    print(f"    State saved to: {STATE_FILE}")
    print(f"    Config saved to: {CONFIG_FILE}\n")


#:#  Print Scheduling Instructions
#;# ─────────────────────────────────────────────────────────────── #
def print_schedule_help():
    script = Path(__file__).resolve()
    python = sys.executable
    print(f"""
📅  Scheduling this script to run weekly:

  macOS / Linux (crontab):
    Run: crontab -e
    Add: 0 9 * * 1 {python} {script}
    (Every Monday at 9:00 AM)

  macOS (launchd plist):
    Create ~/Library/LaunchAgents/com.everand.tracker.plist
    See: https://launchd.info

  Windows (Task Scheduler):
    schtasks /create /sc weekly /d MON /tn "EverandTracker" ^
      /tr "{python} {script}" /st 09:00

""")


#:#  Generate Plist File
#;# ─────────────────────────────────────────────────────────────── #
def generate_plist():
    script = Path(__file__).resolve()
    python = sys.executable
    plist_path = Path.home() / "Library" / "LaunchAgents" / "com.everand.tracker.plist"

    plist_content = f"""<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
    <key>Label</key>
    <string>com.everand.tracker</string>
    <key>ProgramArguments</key>
    <array>
        <string>{python}</string>
        <string>{script}</string>
    </array>
    <key>StartCalendarInterval</key>
    <dict>
        <key>Weekday</key>
        <integer>1</integer>
        <key>Hour</key>
        <integer>9</integer>
        <key>Minute</key>
        <integer>0</integer>
    </dict>
    <key>StandardOutPath</key>
    <string>{script.parent}/everand_tracker.log</string>
    <key>StandardErrorPath</key>
    <string>{script.parent}/everand_tracker.log</string>
    <key>RunAtLoad</key>
    <false/>
</dict>
</plist>"""

    plist_path.write_text(plist_content)
    print(f"✅  Plist written to {plist_path}")
    print(f"\nTo activate it, run:")
    print(f"  launchctl load {plist_path}")
    print(f"\nTo deactivate it:")
    print(f"  launchctl unload {plist_path}")
    print(f"\nLogs will be written to: {script.parent}/everand_tracker.log")


#:#  Main
#;# ─────────────────────────────────────────────────────────────── #
def main():
    parser = argparse.ArgumentParser(description="Everand Unlock Credit Tracker")
    parser.add_argument("--credits", type=int, help="Manually specify current credit count")
    parser.add_argument("--status", action="store_true", help="Show status without updating")
    parser.add_argument("--setup", action="store_true", help="Run first-time setup wizard")
    parser.add_argument("--schedule", action="store_true", help="Show scheduling instructions")
    parser.add_argument("--generate-plist", action="store_true", help="Generate and install a launchd plist for weekly runs")
    args = parser.parse_args()

    if args.schedule:
        print_schedule_help()
        return

    if args.generate_plist:
        generate_plist()
        return

    if args.setup:
        run_setup()
        return

    today = date.today()
    state = load_state()
    config = load_config()

    if args.status:
        print_status(state, today)
        return

    # Determine credit count
    new_count = None

    if args.credits is not None:
        new_count = args.credits
        print(f"📝  Using manual credit count: {new_count}")
    elif config.get("use_scraper"):
        new_count = scrape_credit_count(config)

    if new_count is None:
        print("ℹ️   No credit count provided. Use --credits N or enable scraping in config.")
        print("    Showing current status only.\n")
        print_status(state, today)
        return

    # Reconcile and save
    logs = reconcile(state, new_count, today)
    save_state(state)

    for log in logs:
        print(log)

    print_status(state, today)

    # Check for expiring credits and notify
    warnings = check_expiring(state, today)
    if warnings:
        notify(warnings, config)
    else:
        print("✅  No credits expiring within the next 14 days.")


if __name__ == "__main__":
    main()
