"""
SubscriptionManager — reference-counted symbol subscriptions per client.
Thread-safe under asyncio single-thread model (no locks needed).
"""
from __future__ import annotations

from typing import Dict, Literal, Set

ServiceKey = Literal["LEVELONE_EQUITIES", "LEVELONE_OPTIONS"]


class SubscriptionManager:
    def __init__(self) -> None:
        # symbol → refcount per service
        self._refcounts: Dict[ServiceKey, Dict[str, int]] = {
            "LEVELONE_EQUITIES": {},
            "LEVELONE_OPTIONS": {},
        }
        # per-client ownership: client_id → service → set of symbols
        self._ownership: Dict[str, Dict[ServiceKey, Set[str]]] = {}

    def _ensure_client(self, client_id: str) -> None:
        if client_id not in self._ownership:
            self._ownership[client_id] = {
                "LEVELONE_EQUITIES": set(),
                "LEVELONE_OPTIONS": set(),
            }

    def add(self, client_id: str, service: ServiceKey, symbols: Set[str]) -> Set[str]:
        """
        Add symbols for a client.
        Returns the set of symbols whose refcount went from 0 → 1
        (i.e., upstream must be told to subscribe).
        """
        self._ensure_client(client_id)
        new_upstream: Set[str] = set()
        svc_refs = self._refcounts[service]
        client_svc = self._ownership[client_id][service]

        for sym in symbols:
            if sym in client_svc:
                # already held by this client; no-op
                continue
            client_svc.add(sym)
            svc_refs[sym] = svc_refs.get(sym, 0) + 1
            if svc_refs[sym] == 1:
                new_upstream.add(sym)

        return new_upstream

    def remove(self, client_id: str, service: ServiceKey, symbols: Set[str]) -> Set[str]:
        """
        Remove symbols for a client.
        Returns the set of symbols whose refcount went from 1 → 0
        (i.e., upstream must be told to unsubscribe).
        """
        if client_id not in self._ownership:
            return set()

        dropped_upstream: Set[str] = set()
        svc_refs = self._refcounts[service]
        client_svc = self._ownership[client_id][service]

        for sym in list(symbols):
            if sym not in client_svc:
                continue
            client_svc.discard(sym)
            if sym in svc_refs:
                svc_refs[sym] -= 1
                if svc_refs[sym] <= 0:
                    del svc_refs[sym]
                    dropped_upstream.add(sym)

        return dropped_upstream

    def release_client(self, client_id: str) -> Dict[ServiceKey, Set[str]]:
        """
        Release all subscriptions held by client_id.
        Returns a dict of service → set of symbols that hit refcount 0
        (upstream must unsubscribe from these).
        """
        if client_id not in self._ownership:
            return {"LEVELONE_EQUITIES": set(), "LEVELONE_OPTIONS": set()}

        result: Dict[ServiceKey, Set[str]] = {}
        for service in ("LEVELONE_EQUITIES", "LEVELONE_OPTIONS"):
            owned = set(self._ownership[client_id].get(service, set()))
            result[service] = self.remove(client_id, service, owned)

        del self._ownership[client_id]
        return result

    def current_union(self, service: ServiceKey) -> Set[str]:
        """Return all currently subscribed symbols for a service (refcount > 0)."""
        return set(self._refcounts[service].keys())
