# -*- coding: utf-8 -*-
"""
Stub for AnySearch provider.
TODO: Implement or remove from test_search_api.py if not used.
"""
from typing import List, Optional


class AnySearchSearchProvider:
    """Stub provider - not implemented."""

    def __init__(self, api_keys: List[str]):
        self._keys = api_keys

    @property
    def is_available(self) -> bool:
        return False

    def search(self, query: str, max_results: int = 5, days: int = 3):
        raise NotImplementedError("AnySearchSearchProvider is a stub - not implemented")
