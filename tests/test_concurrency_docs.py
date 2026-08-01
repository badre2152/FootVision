# tests/test_concurrency_docs.py
#
# Regression test for 21_concurrency_docs.md — a documentation-only spec,
# so this is deliberately light: it just proves the documentation exists
# and is actually cross-referenced from where the spec's Acceptance
# Criteria say a contributor would look, so a future edit can't silently
# delete docs/architecture.md or the README pointer to it without a test
# noticing (same "fixes keep disappearing" concern as everything else in
# CHANGELOG.md).

import os
import sys
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

PROJECT_ROOT = Path(__file__).resolve().parent.parent


class TestConcurrencyDocumentationExists:

    def test_architecture_doc_exists_and_documents_the_limitation(self):
        doc_path = PROJECT_ROOT / "docs" / "architecture.md"
        assert doc_path.exists(), "docs/architecture.md is missing"
        text = doc_path.read_text(encoding="utf-8")
        assert "no locking or transactional guarantees" in text
        assert "last write wins" in text.lower()

    def test_architecture_doc_states_a_trigger_condition(self):
        text = (PROJECT_ROOT / "docs" / "architecture.md").read_text(encoding="utf-8")
        assert "Trigger condition" in text

    def test_readme_cross_references_architecture_doc(self):
        readme = (PROJECT_ROOT / "README.md").read_text(encoding="utf-8")
        assert "docs/architecture.md" in readme
        assert "concurrency" in readme.lower()

    def test_notes_store_cross_references_architecture_doc(self):
        notes_store_src = (PROJECT_ROOT / "dashboard" / "notes_store.py").read_text(encoding="utf-8")
        assert "docs/architecture.md" in notes_store_src
