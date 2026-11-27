"""Bounded extraction and literal evidence binding reject fabricated answers."""

import unittest

from utils.retrieval_evidence import evidence_answer, evidence_cards, query_excerpt, ranked_source_urls


class RetrievalEvidenceTests(unittest.TestCase):
    def test_site_restriction_and_stable_ties_keep_explicit_search_scope(self):
        sources = [{'url': 'https://other.example/topic', 'title': 'HTMLParser'},
                   {'url': 'https://docs.python.org/one', 'title': 'HTMLParser'},
                   {'url': 'https://docs.python.org/two', 'title': 'HTMLParser'}]
        self.assertEqual(ranked_source_urls(sources, 'site:docs.python.org HTMLParser'),
                         ['https://docs.python.org/one', 'https://docs.python.org/two'])

    def test_evidence_ids_cannot_introduce_free_text_duplicate_or_invalid_excerpts(self):
        cards = [{'id': 1, 'source': 1, 'text': 'HTMLParser does not match end tags.'}]
        for ids in ([True], [-1], [2], [1, 1], ['HTMLParser uses Expat'], [{}], None):
            with self.subTest(ids=ids):
                self.assertIsNone(evidence_answer(cards, ids))
        self.assertIn('does not match', evidence_answer(cards, [1]))
        self.assertIn("don't provide enough evidence", evidence_answer(cards, []))

    def test_failed_pages_and_irrelevant_text_do_not_become_evidence(self):
        data = {'kind': 'web_sources', 'sources': [
            {'status': 'failed', 'text': 'HTMLParser uses Expat.'},
            {'status': 'fetched', 'text': 'A recipe for bread.'}]}
        self.assertEqual(evidence_cards(data, 'HTMLParser limitations'), [])

    def test_many_irrelevant_paragraphs_cannot_hide_late_relevant_evidence(self):
        text = 'Unrelated introduction.\n' * 10000 + 'HTMLParser does not check matching end tags.'
        excerpt = query_excerpt(text, 'HTMLParser limitations', 600)
        self.assertIn('does not check', excerpt)
        self.assertLessEqual(len(excerpt), 600)
