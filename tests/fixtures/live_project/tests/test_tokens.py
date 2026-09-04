import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from calc.tokens import split_line


class TokensTests(unittest.TestCase):
    def test_plain_line(self):
        self.assertEqual(split_line("a,b,c"), ["a", "b", "c"])
