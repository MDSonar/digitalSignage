#!/usr/bin/env python3
"""
Recurrence Engine
=================
RRULE-like recurrence rule parser and date evaluator.

Supported rules:
  FREQ=ONCE
  FREQ=DAILY
  FREQ=WEEKLY;BYDAY=MO,TU,WE
  FREQ=MONTHLY;BYMONTHDAY=15
  FREQ=MONTHLY;BYDAY=2TU          (2nd Tuesday)
  FREQ=MONTHLY;BYDAY=-1FR         (last Friday)
  FREQ=YEARLY;BYMONTH=12;BYMONTHDAY=25
  FREQ=YEARLY;BYMONTH=10;BYDAY=2MO (2nd Monday of October)

No external dependencies. Pure standard library.
"""

import re
from datetime import date
from typing import List, Optional, Tuple


class RecurrenceRule:
    """Parse and evaluate an RRULE-like recurrence string."""

    _WD_MAP = {'MO': 0, 'TU': 1, 'WE': 2, 'TH': 3, 'FR': 4, 'SA': 5, 'SU': 6}
    _WD_REV = {v: k for k, v in _WD_MAP.items()}

    # Mapping from UI day chip names to RRULE weekday codes
    _DAY_CHIP = {'mon': 'MO', 'tue': 'TU', 'wed': 'WE',
                 'thu': 'TH', 'fri': 'FR', 'sat': 'SA', 'sun': 'SU'}

    def __init__(self, rule: str = ''):
        self.freq: Optional[str] = None
        self.byday: List[Tuple[Optional[int], int]] = []
        if rule:
            self._parse(rule.strip())

    # ── Parsing ───────────────────────────────────────────────────────────────

    def _parse(self, rule: str) -> None:
        for part in rule.upper().split(';'):
            if '=' not in part:
                continue
            key, val = part.split('=', 1)
            if key == 'FREQ':
                self.freq = val
            elif key == 'BYDAY':
                self.byday = [t for t in
                               (self._parse_byday_token(tok) for tok in val.split(','))
                               if t is not None]

    def _parse_byday_token(self, tok: str) -> Optional[Tuple[Optional[int], int]]:
        m = re.match(r'^(-?\d+)?([A-Z]{2})$', tok.strip())
        if not m:
            return None
        wd = self._WD_MAP.get(m.group(2))
        if wd is None:
            return None
        n = int(m.group(1)) if m.group(1) else None
        return (n, wd)

    # ── Evaluation ────────────────────────────────────────────────────────────

    def matches(self, check_date: date, start_date: date) -> bool:
        """Return True if check_date is an occurrence of this rule (bounded by start_date)."""
        if not self.freq:
            return False
        if self.freq == 'ONCE':
            return check_date == start_date
        if self.freq == 'DAILY':
            return True
        if self.freq == 'WEEKLY':
            return self._weekly(check_date)
        return False

    def _weekly(self, d: date) -> bool:
        if not self.byday:
            return False
        return any(d.weekday() == wd for _, wd in self.byday)

    # ── Serialization ─────────────────────────────────────────────────────────

    def to_string(self) -> str:
        if not self.freq:
            return ''
        parts = [f'FREQ={self.freq}']
        if self.byday:
            tokens = [
                (f'{n}{self._WD_REV[wd]}' if n is not None else self._WD_REV[wd])
                for n, wd in self.byday
            ]
            parts.append(f'BYDAY={",".join(tokens)}')
        return ';'.join(parts)

    # ── Factory ───────────────────────────────────────────────────────────────

    @classmethod
    def from_ui(cls, repeat_type: str, days: list, start_date_str: str) -> 'RecurrenceRule':
        """Build a rule from the UI form fields (repeat_type, day chips, start_date)."""
        return cls(fields_to_rrule(repeat_type, days, start_date_str))


# ── Module-level helper ───────────────────────────────────────────────────────

def fields_to_rrule(repeat_type: str, days: list, start_date_str: str) -> str:
    """Convert UI form fields to an RRULE string."""
    _DAY = RecurrenceRule._DAY_CHIP
    rt = (repeat_type or '').lower()

    if rt == 'once':
        return 'FREQ=ONCE'
    if rt == 'daily':
        return 'FREQ=DAILY'
    if rt == 'weekly':
        byday = ','.join(_DAY[d] for d in (days or []) if d in _DAY)
        return f'FREQ=WEEKLY;BYDAY={byday}' if byday else 'FREQ=WEEKLY'
    return 'FREQ=WEEKLY'
