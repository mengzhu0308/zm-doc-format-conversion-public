from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from publish_support import discover_local_publish_roots, find_project_root


class PublishRepoDiscoveryTests(unittest.TestCase):
    def test_find_project_root_uses_explicit_repo_path(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / 'repo'
            (root / '.git').mkdir(parents=True)
            (root / 'skills').mkdir()
            (root / 'project-publish').mkdir()
            (root / 'VERSION.yaml').write_text('project_info:\n  name: demo\n  version: 0.1.0\n', encoding='utf-8')
            nested = root / 'skills' / 'demo'
            nested.mkdir()
            self.assertEqual(find_project_root(nested), root)

    def test_discover_local_publish_roots_filters_valid_projects(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            valid = base / 'valid-repo'
            (valid / '.git').mkdir(parents=True)
            (valid / 'skills').mkdir()
            (valid / 'project-publish').mkdir()
            (valid / 'VERSION.yaml').write_text('project_info:\n  name: demo\n  version: 0.1.0\n', encoding='utf-8')

            missing_git = base / 'missing-git'
            (missing_git / 'skills').mkdir(parents=True)
            (missing_git / 'project-publish').mkdir()
            (missing_git / 'VERSION.yaml').write_text('project_info:\n  name: demo\n  version: 0.1.0\n', encoding='utf-8')

            missing_publish = base / 'missing-publish'
            (missing_publish / '.git').mkdir(parents=True)
            (missing_publish / 'skills').mkdir()
            (missing_publish / 'VERSION.yaml').write_text('project_info:\n  name: demo\n  version: 0.1.0\n', encoding='utf-8')

            results = discover_local_publish_roots((base,))
            self.assertEqual(results, [valid])


if __name__ == '__main__':
    unittest.main()
