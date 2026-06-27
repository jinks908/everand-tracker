# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

---

**IMPORTANT GUIDELINES**

## 1. Think Before Coding

**Don't assume. Don't hide confusion. Surface tradeoffs.**

Before implementing:
- State your assumptions explicitly. If uncertain, ask.
- If multiple interpretations exist, present them - don't pick silently.
- If a simpler approach exists, say so. Push back when warranted.
- If something is unclear, stop. Name what's confusing. Ask.

## 2. Simplicity First

**Minimum code that solves the problem. Nothing speculative.**

- No features beyond what was asked.
- No abstractions for single-use code.
- No "flexibility" or "configurability" that wasn't requested.
- No error handling for impossible scenarios.
- If you write 200 lines and it could be 50, rewrite it.

Ask yourself: "Would a senior engineer say this is overcomplicated?" If yes, simplify.

## 3. Surgical Changes

**Touch only what you must. Clean up only your own mess.**

When editing existing code:
- Don't "improve" adjacent code, comments, or formatting.
- Don't refactor things that aren't broken.
- Match existing style, even if you'd do it differently.
- If you notice unrelated dead code, mention it - don't delete it.

When your changes create orphans:
- Remove imports/variables/functions that YOUR changes made unused.
- Don't remove pre-existing dead code unless asked.

The test: Every changed line should trace directly to the user's request.

## 4. Goal-Driven Execution

**Define success criteria. Loop until verified.**

Transform tasks into verifiable goals:
- "Add validation" → "Write tests for invalid inputs, then make them pass"
- "Fix the bug" → "Write a test that reproduces it, then make it pass"
- "Refactor X" → "Ensure tests pass before and after"

For multi-step tasks, state a brief plan:
```
1. [Step] → verify: [check]
2. [Step] → verify: [check]
3. [Step] → verify: [check]
```

## 5. Git

**User will perform ALL git operations unless agent is explicitly asked to.**

- Agent can raise concerns, check for conflicts, suggest commit messages, propose branch names.
- Input/suggestions are always welcome, especially for obvious mistakes or oversights.
- Any read-only operations (like `git log`, `git diff`, `git status`, etc.) are permitted and encouraged to help the agent understand the codebase.

---

# Project: Everand Unlock Credit Tracker

A single-file Python CLI (`everand_tracker.py`, ~680 lines, Python 3.11+, macOS-targeted)
that tracks Everand monthly unlock credits (3/month, rolling over up to 6 months) and
alerts before credits expire. No build step, no test suite, no dependency manifest — deps
are installed ad-hoc (`playwright`, `keyring`, optionally `plyer`/`pyobjus`).

## Commands

```bash
python everand_tracker.py                 # normal run: scrape, reconcile, notify
python everand_tracker.py --credits N      # manual count, skip scraping
python everand_tracker.py --status         # read-only; print current state
python everand_tracker.py --setup          # first-time wizard, seeds initial balance
python everand_tracker.py --generate-plist # write + install launchd plist (weekly)
python everand_tracker.py --schedule       # print cron/launchd instructions
```

There is no automated test harness. To exercise a notification path in isolation, import
the relevant `send_*`/`print_*` function and call it with a fake warnings list (see the
"Testing notifications" section of README.md).

## Architecture

The flow in `main()` is: **acquire a credit count → `reconcile()` → `save_state()` →
`check_expiring()` → `notify()`**. Two data files (both gitignored, both `Path(__file__).parent`):

- `credits.json` (`STATE_FILE`) — the source of truth. A list of credit `batches`, each with
  `earned`/`expires`/`total`/`remaining`, plus `last_known_count`, `last_run`, and
  `next_batch_date`. This is the only persistent state the program reasons about.
- `config.json` (`CONFIG_FILE`) — user settings (notify methods, scraper toggle, SMTP/email).
  Contains no secrets.

**Batch accounting is the core logic** (`reconcile()`, `total_active_credits()`,
`check_expiring()`). The scraper only ever yields a single integer "current count"; the program
*infers* what happened by comparing that to `total_active_credits(state)`:
- delta > 0 → new batch(es) earned today, expiring `ROLLOVER_MONTHS` out
- delta < 0 → credits used; drain oldest batches first (FIFO)
- expired batches are zeroed out before computing the delta

This inference is lossy by design — the count is the only signal Everand exposes, so all batch
boundaries are reconstructed from count changes over time. The three domain constants live at
the top of the file: `CREDITS_PER_MONTH=3`, `ROLLOVER_MONTHS=6`, `ALERT_DAYS_BEFORE=14`.

**Scraping** (`scrape_data()` → `scrape_credit_count()`, `scrape_next_batch_date()`) uses
Playwright against Everand's Auth0 flow (auth.scribd.com). The auth `state` param is dynamic, so
the code navigates to the homepage and clicks sign-in rather than building the auth URL directly.
First run is headed for MFA; the session persists to `session.json` and later runs are headless.
The two scrape functions parse the page with regex — **if Everand changes their markup, update the
patterns there**; on a parse miss the full page HTML is dumped to `scraper_debug.html` for
inspection.

**Notifications** fan out through `notify()`, which dispatches to one or more of
`print_console_alert`, `send_alerter_notification` (macOS Notification Center via the `alerter`
CLI — checks both `/opt/homebrew/bin` and `/usr/local/bin`), `send_desktop_notification` (plyer),
and `send_email_alert` (SMTP). `config["notify_method"]` accepts a string or list.

## Secrets

Credentials are **never** stored in `config.json`. They live in the macOS Keychain under service
`everand_tracker`: account `everand` (Everand password) and `smtp` (email app password), read via
`keyring.get_password(...)`. Gmail requires an App Password, not the account password.

