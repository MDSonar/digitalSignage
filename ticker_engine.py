#!/usr/bin/env python3
"""
Ticker Engine
=============
Provider-based architecture for news ticker messages.

Current providers:
  ManualMessagesProvider  — messages stored in ~/signage/news.json

Pluggable: future providers (RSS, MQTT, Weather, Emergency Alerts, KPIs)
can be added by subclassing TickerProvider and registering with TickerEngine.
"""

from abc import ABC, abstractmethod
from datetime import date as _date
import json
from pathlib import Path
from typing import List

NEWS_FILE = Path.home() / 'signage' / 'news.json'


class TickerMessage:
    """Represents a single message in the ticker."""

    __slots__ = ('id', 'title', 'message', 'priority', 'source')

    def __init__(self, *, id: str, title: str, message: str,
                 priority: int = 0, source: str = 'manual'):
        self.id = id
        self.title = title
        self.message = message
        self.priority = priority
        self.source = source

    def to_dict(self) -> dict:
        return {
            'id': self.id,
            'title': self.title,
            'message': self.message,
            'priority': self.priority,
            'source': self.source,
        }


class TickerProvider(ABC):
    """Abstract base class — subclass to add new message sources."""

    @property
    def name(self) -> str:
        return self.__class__.__name__

    @abstractmethod
    def get_messages(self) -> List[TickerMessage]:
        """Return currently active messages for this provider."""
        raise NotImplementedError


class ManualMessagesProvider(TickerProvider):
    """Returns enabled messages from ~/signage/news.json, filtered by date range."""

    def get_messages(self) -> List[TickerMessage]:
        try:
            if not NEWS_FILE.exists():
                return []
            data = json.loads(NEWS_FILE.read_text(encoding='utf-8'))
            today = _date.today().isoformat()
            out: List[TickerMessage] = []
            for raw in data.get('messages', []):
                if not raw.get('enabled', True):
                    continue
                start = raw.get('start_date') or ''
                end = raw.get('end_date') or ''
                if start and today < start:
                    continue
                if end and today > end:
                    continue
                out.append(TickerMessage(
                    id=raw.get('id', ''),
                    title=raw.get('title', ''),
                    message=raw.get('message', ''),
                    priority=int(raw.get('priority', 0)),
                    source='manual',
                ))
            out.sort(key=lambda m: m.priority, reverse=True)
            return out
        except Exception:
            return []


class TickerEngine:
    """Aggregates messages from all registered providers, sorted by priority."""

    def __init__(self):
        self._providers: List[TickerProvider] = []
        self.register(ManualMessagesProvider())

    def register(self, provider: TickerProvider) -> 'TickerEngine':
        """Register a new provider. Returns self for chaining."""
        self._providers.append(provider)
        return self

    def get_all_messages(self) -> List[dict]:
        """Return merged, priority-sorted messages from all providers."""
        messages: List[dict] = []
        for provider in self._providers:
            try:
                messages.extend(m.to_dict() for m in provider.get_messages())
            except Exception:
                pass
        messages.sort(key=lambda m: m.get('priority', 0), reverse=True)
        return messages
