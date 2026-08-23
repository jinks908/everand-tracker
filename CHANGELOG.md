# Changelog

All notable changes to this project are documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [1.0.1] - 2026-08-23

### Fixed

- **Alerter notifications auto-dismissed before they could be seen.** Both
  `send_alerter_notification()` and `send_arrival_alerter()` passed `--timeout 20`,
  which overrode the persistent Alert style and closed the notification after 20
  seconds. On an unattended scheduled run this meant the alert vanished before
  anyone saw it. The flag is now removed, so notifications stay on screen until
  dismissed.
- **Notification failures were reported as successes.** Both alerter functions
  printed `"🔔  Alerter notification sent."` unconditionally, without inspecting
  the `alerter` exit status. A failed notification was indistinguishable from a
  successful one in `everand_tracker.log`. Success is now reported only when
  `alerter` exits `0`; otherwise the exit code and `stderr` are printed.

### Removed

- Dead `except subprocess.CalledProcessError` handlers in both alerter functions.
  `subprocess.run()` cannot raise this without `check=True`, so the handlers were
  unreachable.

### Notes

- Because notifications now persist until dismissed, the tracker process stays
  alive while an alert is pending. This is inherent to the behavior: `alerter`
  holds the notification, so its parent must keep waiting. Expected to be a
  single idle process given the weekly + day-of-month schedule.
- `alerter` requires notification permission to be granted for its impersonated
  sender (`com.apple.Terminal` by default). Without it, notifications are never
  displayed. Grant this once via the system permission prompt or in
  System Settings → Notifications.

### Added

- `CHANGELOG.md` (this file).

## [1.0.0] - 2026-07-05

Initial tagged release.

[1.0.1]: https://github.com/jinks908/everand-tracker/compare/v1.0.0...v1.0.1
[1.0.0]: https://github.com/jinks908/everand-tracker/releases/tag/v1.0.0
