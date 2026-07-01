#!/usr/bin/env python3
"""
Scheduler Engine
================
Evaluates which schedule block is active at a given datetime.

Pure logic module: no Flask, no file I/O, no side effects.
O(N) per query — never generates future instances.

Usage:
    from scheduler_engine import SchedulerEngine
    engine = SchedulerEngine()
    block = engine.get_current_block(all_blocks, datetime.now())
"""

from datetime import date, datetime, timedelta
from typing import Optional, List, Dict, Any

from recurrence_engine import RecurrenceRule, fields_to_rrule


class SchedulerEngine:

    # ── Public API ────────────────────────────────────────────────────────────

    def is_block_active(self, block: dict, now: datetime) -> bool:
        """
        Return True if this block should be playing at `now`.

        Handles:
          - enabled flag
          - start_date / end_date / no_end_date bounds
          - overnight schedules (e.g., 22:00 – 06:00)
          - all recurrence types via RecurrenceRule
          - legacy blocks (no start_date, only days[])
        """
        if not block.get('enabled', True):
            return False

        start_date_str = block.get('start_date')
        if not start_date_str:
            return self._legacy_check(block, now)

        try:
            start_date = date.fromisoformat(start_date_str)
        except ValueError:
            return False

        today = now.date()

        # ── Date range bounds ────────────────────────────────────────────────
        if today < start_date:
            return False

        if not block.get('no_end_date', False):
            end_date_str = block.get('end_date')
            if end_date_str:
                try:
                    if today > date.fromisoformat(end_date_str):
                        return False
                except ValueError:
                    pass

        # ── Resolve recurrence rule ──────────────────────────────────────────
        rule_str = block.get('recurrence_rule') or fields_to_rrule(
            block.get('repeat_type', block.get('recurrence', 'weekly')),
            block.get('days', []),
            start_date_str,
        )
        rule = RecurrenceRule(rule_str)

        # ── Time window check ────────────────────────────────────────────────
        start_min, end_min = self._parse_times(
            block.get('start_time', '00:00'),
            block.get('end_time', '23:59'),
        )
        if start_min is None:
            return False

        current_min = now.hour * 60 + now.minute
        overnight = end_min <= start_min

        if overnight:
            if current_min >= start_min:
                return rule.matches(today, start_date)
            if current_min < end_min:
                yesterday = today - timedelta(days=1)
                if yesterday < start_date:
                    return False
                if not block.get('no_end_date', False):
                    end_date_str = block.get('end_date')
                    if end_date_str:
                        try:
                            if yesterday > date.fromisoformat(end_date_str):
                                return False
                        except ValueError:
                            pass
                return rule.matches(yesterday, start_date)
            return False
        else:
            if not (start_min <= current_min < end_min):
                return False
            return rule.matches(today, start_date)

    def get_current_block(self, blocks: List[dict], now: datetime) -> Optional[dict]:
        """Return the highest-priority active block, or None."""
        active = [b for b in blocks if self.is_block_active(b, now)]
        if not active:
            return None
        return max(active, key=lambda b: b.get('priority', 0))

    def get_next_block_today(self, blocks: List[dict], now: datetime) -> Optional[Dict[str, Any]]:
        """
        Return the next upcoming block later today as a summary dict:
          {'title': str, 'start_time': str}
        or None if nothing is scheduled after current time today.
        """
        current_min = now.hour * 60 + now.minute
        today = now.date()
        candidates = []

        for block in blocks:
            if not block.get('enabled', True):
                continue

            start_date_str = block.get('start_date')
            if start_date_str:
                try:
                    start_date = date.fromisoformat(start_date_str)
                except ValueError:
                    continue
                if today < start_date:
                    continue
                if not block.get('no_end_date', False):
                    end_str = block.get('end_date')
                    if end_str:
                        try:
                            if today > date.fromisoformat(end_str):
                                continue
                        except ValueError:
                            pass
                rule_str = block.get('recurrence_rule') or fields_to_rrule(
                    block.get('repeat_type', block.get('recurrence', 'weekly')),
                    block.get('days', []),
                    start_date_str,
                )
                if not RecurrenceRule(rule_str).matches(today, start_date):
                    continue
            else:
                _DAY = {0: 'mon', 1: 'tue', 2: 'wed', 3: 'thu', 4: 'fri', 5: 'sat', 6: 'sun'}
                if _DAY[today.weekday()] not in block.get('days', []):
                    continue

            start_min, _ = self._parse_times(
                block.get('start_time', '00:00'),
                block.get('end_time', '23:59'),
            )
            if start_min is None or start_min <= current_min:
                continue

            label = (block.get('title') or block.get('playlist_name') or
                     block.get('media_name') or 'Block')
            candidates.append({
                'title': label,
                'start_time': block.get('start_time'),
                '_min': start_min,
            })

        if not candidates:
            return None
        best = min(candidates, key=lambda c: c['_min'])
        return {'title': best['title'], 'start_time': best['start_time']}

    # ── Internals ─────────────────────────────────────────────────────────────

    @staticmethod
    def _parse_times(start_str: str, end_str: str):
        """Return (start_minutes, end_minutes) or (None, None) on error."""
        try:
            sh, sm = map(int, start_str.split(':'))
            eh, em = map(int, end_str.split(':'))
            return sh * 60 + sm, eh * 60 + em
        except (ValueError, AttributeError):
            return None, None

    @staticmethod
    def _legacy_check(block: dict, now: datetime) -> bool:
        """Backward-compatible check for old blocks that only carry days[]."""
        _DAY = {0: 'mon', 1: 'tue', 2: 'wed', 3: 'thu', 4: 'fri', 5: 'sat', 6: 'sun'}
        if _DAY[now.weekday()] not in block.get('days', []):
            return False
        try:
            sh, sm = map(int, block.get('start_time', '00:00').split(':'))
            eh, em = map(int, block.get('end_time', '23:59').split(':'))
        except (ValueError, AttributeError):
            return False
        start_min = sh * 60 + sm
        end_min = eh * 60 + em
        current_min = now.hour * 60 + now.minute
        if end_min <= start_min:
            return current_min >= start_min or current_min < end_min
        return start_min <= current_min < end_min
