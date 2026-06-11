from __future__ import annotations

import sys
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from publish_cli import parse_args


class PublishCliTests(unittest.TestCase):
    def test_accepts_private_as_positional_visibility_alias(self) -> None:
        with patch.object(sys, "argv", ["main.py", "private", "--repo-path", "/tmp/project"]):
            options = parse_args()

        self.assertEqual(options.repo_visibility, "private")
        self.assertEqual(options.release_scope, "private")
        self.assertEqual(options.repo_path, "/tmp/project")

    def test_rejects_conflicting_positional_and_flag_visibility(self) -> None:
        with (
            patch.object(sys, "argv", ["main.py", "private", "--repo-visibility", "public"]),
            self.assertRaises(SystemExit),
        ):
            parse_args()


if __name__ == "__main__":
    unittest.main()
