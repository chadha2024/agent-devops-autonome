"""
Tests unitaires pour dedup.py — déduplication des alertes (cooldown).
"""

import time
from dedup import Deduplicator


class TestShouldAlert:

    def test_first_occurrence_always_alerts(self):
        dedup = Deduplicator(cooldown_seconds=300)
        assert dedup.should_alert("node-1+cpu_percent") is True

    def test_immediate_repeat_is_suppressed(self):
        dedup = Deduplicator(cooldown_seconds=300)
        dedup.should_alert("node-1+cpu_percent")
        assert dedup.should_alert("node-1+cpu_percent") is False

    def test_alert_repeats_after_cooldown_expires(self):
        dedup = Deduplicator(cooldown_seconds=0.05)
        dedup.should_alert("node-1+cpu_percent")
        time.sleep(0.1)
        assert dedup.should_alert("node-1+cpu_percent") is True

    def test_different_incident_keys_are_independent(self):
        dedup = Deduplicator(cooldown_seconds=300)
        dedup.should_alert("node-1+cpu_percent")
        assert dedup.should_alert("node-2+ram_percent") is True


class TestCleanupExpired:

    def test_old_entries_are_removed_after_long_expiry(self):
        dedup = Deduplicator(cooldown_seconds=0.05)
        dedup.should_alert("node-1+cpu_percent")
        time.sleep(0.25)  # dépasse cooldown_seconds * 4
        dedup.cleanup_expired()
        assert "node-1+cpu_percent" not in dedup._last_seen