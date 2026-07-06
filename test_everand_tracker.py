#!/usr/bin/env python3
"""
Tests for the core batch-accounting logic in everand_tracker.

Run with:  python -m unittest test_everand_tracker

The tracker imports `keyring` (and uses `plyer`/`playwright` at runtime), none
of which are needed for the pure accounting logic. We stub `keyring` before the
import so these tests run with no dependencies installed.
"""

import sys
import types
import unittest
from datetime import date, timedelta

# Stub keyring so the module imports without the real dependency.
_keyring_stub = types.ModuleType("keyring")
_keyring_stub.get_password = lambda *a, **k: None
sys.modules.setdefault("keyring", _keyring_stub)

import everand_tracker as et  # noqa: E402


def fresh_state() -> dict:
    return {"batches": [], "last_known_count": 0, "last_run": None, "next_batch_date": None}


class TestTotalActiveCredits(unittest.TestCase):
    def test_empty(self):
        self.assertEqual(et.total_active_credits(fresh_state(), date(2026, 7, 5)), 0)

    def test_sums_only_non_expired_with_remaining(self):
        today = date(2026, 7, 5)
        state = {"batches": [
            {"earned": "2026-01-01", "expires": "2026-07-01", "total": 3, "remaining": 3},  # expired
            {"earned": "2026-06-01", "expires": "2026-12-01", "total": 3, "remaining": 2},  # active
            {"earned": "2026-06-15", "expires": "2026-12-15", "total": 3, "remaining": 0},  # drained
        ]}
        self.assertEqual(et.total_active_credits(state, today), 2)

    def test_expiring_today_still_counts(self):
        today = date(2026, 7, 5)
        state = {"batches": [
            {"earned": "2026-01-05", "expires": "2026-07-05", "total": 3, "remaining": 3},
        ]}
        self.assertEqual(et.total_active_credits(state, today), 3)


class TestReconcileArrival(unittest.TestCase):
    def test_first_batch_arrives(self):
        today = date(2026, 7, 5)
        state = fresh_state()
        logs, arrived = et.reconcile(state, 3, today)
        self.assertEqual(arrived, 3)
        self.assertEqual(len(state["batches"]), 1)
        self.assertEqual(et.total_active_credits(state, today), 3)
        self.assertEqual(state["last_known_count"], 3)

    def test_arrival_sets_expiry_rollover_months_out(self):
        today = date(2026, 7, 5)
        state = fresh_state()
        et.reconcile(state, 3, today)
        expected = (today + timedelta(days=30 * et.ROLLOVER_MONTHS)).isoformat()
        self.assertEqual(state["batches"][0]["expires"], expected)

    def test_multiple_batches_from_large_delta(self):
        today = date(2026, 7, 5)
        state = fresh_state()
        logs, arrived = et.reconcile(state, 6, today)
        self.assertEqual(arrived, 6)
        self.assertEqual(len(state["batches"]), 2)
        self.assertTrue(all(b["total"] == et.CREDITS_PER_MONTH for b in state["batches"]))

    def test_partial_batch(self):
        today = date(2026, 7, 5)
        state = fresh_state()
        logs, arrived = et.reconcile(state, 4, today)
        self.assertEqual(arrived, 4)
        # one full batch of 3 + a partial batch of 1
        totals = sorted(b["total"] for b in state["batches"])
        self.assertEqual(totals, [1, 3])

    def test_no_change_reports_zero_arrival(self):
        today = date(2026, 7, 5)
        state = fresh_state()
        et.reconcile(state, 3, today)
        logs, arrived = et.reconcile(state, 3, today)
        self.assertEqual(arrived, 0)
        self.assertEqual(logs, [])


class TestReconcileUsage(unittest.TestCase):
    def test_usage_reports_zero_arrival(self):
        today = date(2026, 7, 5)
        state = fresh_state()
        et.reconcile(state, 3, today)
        logs, arrived = et.reconcile(state, 1, today)
        self.assertEqual(arrived, 0)

    def test_usage_drains_oldest_first_fifo(self):
        today = date(2026, 7, 5)
        state = fresh_state()
        # First batch earned earlier
        et.reconcile(state, 3, date(2026, 6, 5))
        # Second batch earned later (total 6)
        et.reconcile(state, 6, today)
        # Use 3 — should drain the June batch fully, leave July batch untouched
        et.reconcile(state, 3, today)
        by_earned = {b["earned"]: b["remaining"] for b in state["batches"]}
        self.assertEqual(by_earned["2026-06-05"], 0)
        self.assertEqual(by_earned["2026-07-05"], 3)

    def test_usage_spanning_two_batches(self):
        today = date(2026, 7, 5)
        state = fresh_state()
        et.reconcile(state, 3, date(2026, 6, 5))
        et.reconcile(state, 6, today)
        # Use 4 — drains June (3) then 1 from July
        et.reconcile(state, 2, today)
        by_earned = {b["earned"]: b["remaining"] for b in state["batches"]}
        self.assertEqual(by_earned["2026-06-05"], 0)
        self.assertEqual(by_earned["2026-07-05"], 2)


class TestReconcileExpiry(unittest.TestCase):
    def test_expired_batch_zeroed_before_delta(self):
        # A batch that has expired should be zeroed, and its credits should not
        # count toward old_count when computing the arrival delta.
        state = {"batches": [
            {"earned": "2026-01-01", "expires": "2026-07-01", "total": 3, "remaining": 3},
        ], "last_known_count": 3, "last_run": None, "next_batch_date": None}
        today = date(2026, 7, 5)  # past the expiry
        logs, arrived = et.reconcile(state, 3, today)
        # Expired batch zeroed; count of 3 now reads as a fresh arrival of 3
        self.assertEqual(state["batches"][0]["remaining"], 0)
        self.assertEqual(arrived, 3)
        self.assertTrue(any("expired" in log for log in logs))


class TestCheckExpiring(unittest.TestCase):
    def test_within_window_flagged(self):
        today = date(2026, 7, 5)
        exp = (today + timedelta(days=10)).isoformat()
        state = {"batches": [
            {"earned": "2026-01-05", "expires": exp, "total": 3, "remaining": 2},
        ]}
        warnings = et.check_expiring(state, today)
        self.assertEqual(len(warnings), 1)
        self.assertEqual(warnings[0]["days_left"], 10)

    def test_outside_window_not_flagged(self):
        today = date(2026, 7, 5)
        exp = (today + timedelta(days=et.ALERT_DAYS_BEFORE + 1)).isoformat()
        state = {"batches": [
            {"earned": "2026-01-05", "expires": exp, "total": 3, "remaining": 2},
        ]}
        self.assertEqual(et.check_expiring(state, today), [])

    def test_drained_batch_not_flagged(self):
        today = date(2026, 7, 5)
        exp = (today + timedelta(days=5)).isoformat()
        state = {"batches": [
            {"earned": "2026-01-05", "expires": exp, "total": 3, "remaining": 0},
        ]}
        self.assertEqual(et.check_expiring(state, today), [])

    def test_already_expired_not_flagged(self):
        today = date(2026, 7, 5)
        exp = (today - timedelta(days=1)).isoformat()
        state = {"batches": [
            {"earned": "2026-01-05", "expires": exp, "total": 3, "remaining": 2},
        ]}
        self.assertEqual(et.check_expiring(state, today), [])

    def test_sorted_by_days_left(self):
        today = date(2026, 7, 5)
        state = {"batches": [
            {"earned": "a", "expires": (today + timedelta(days=12)).isoformat(), "total": 3, "remaining": 1},
            {"earned": "b", "expires": (today + timedelta(days=3)).isoformat(), "total": 3, "remaining": 1},
        ]}
        warnings = et.check_expiring(state, today)
        self.assertEqual([w["days_left"] for w in warnings], [3, 12])


if __name__ == "__main__":
    unittest.main()
