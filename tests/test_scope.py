from __future__ import annotations

import unittest

from src.scrapers.scope import SearchScope
from tests.helpers import make_profile


class SearchScopeTests(unittest.TestCase):
    def test_union_of_unpaused_profiles(self) -> None:
        scope = SearchScope.from_profiles(
            [
                make_profile(id=1, municipalities=("delft", "den-haag")),
                make_profile(id=2, municipalities=("leiden",)),
                make_profile(id=3, municipalities=("gouda",), paused=True),
                make_profile(id=4, municipalities=()),
            ]
        )
        self.assertEqual(scope.municipalities, frozenset({"delft", "den-haag", "leiden"}))

    def test_no_profiles(self) -> None:
        self.assertEqual(SearchScope.from_profiles([]).municipalities, frozenset())


if __name__ == "__main__":
    unittest.main()
