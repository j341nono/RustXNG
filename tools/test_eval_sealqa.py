"""Protect retrieval scores from URL-prefix and normalization false positives."""
import unittest

from eval_sealqa import rank_of, summarise


class SealQaScoringTests(unittest.TestCase):
    def test_same_page_ignores_scheme_fragment_and_trailing_slash(self):
        self.assertEqual(rank_of(["http://www.example.org/docs/"], ["https://example.org/docs#:~:text=evidence"]), 1)

    def test_path_prefix_and_different_hosts_are_not_hits(self):
        for candidate in ["https://example.org/docs-extra", "https://example.org/docs/child", "https://example.org.evil/docs"]:
            self.assertIsNone(rank_of([candidate], ["https://example.org/docs"]))

    def test_query_and_path_case_are_significant(self):
        self.assertIsNone(rank_of(["https://example.org/Docs"], ["https://example.org/docs"]))
        self.assertIsNone(rank_of(["https://example.org/docs?q=2"], ["https://example.org/docs?q=1"]))

    def test_percent_encoded_page_matches_unicode_reference(self):
        self.assertEqual(rank_of(["https://example.org/%E6%9D%B1%E4%BA%AC"], ["https://example.org/東京"]), 1)

    def test_invalid_urls_never_match_each_other(self):
        self.assertIsNone(rank_of(["javascript:alert(1)", "https://example.org:bad/"], ["not a URL"]))

    def test_alternative_reference_and_rank(self):
        self.assertEqual(rank_of(["https://example.org/other", "https://example.org/b"], ["https://example.org/a", "https://example.org/b"]), 2)

    def test_summary_uses_all_questions_including_errors(self):
        rows = [
            {"rank": 1, "urls": ["a"], "elapsed_ms": 100},
            {"rank": 5, "urls": ["b"], "elapsed_ms": 200},
            {"rank": None, "urls": [], "elapsed_ms": 300, "error": "timeout"},
        ]
        result = summarise(rows)
        self.assertEqual(result["reference_hit@1"], 1)
        self.assertEqual(result["reference_hit@5"], 2)
        self.assertEqual(result["reference_hit@10_pct"], 66.67)
        self.assertEqual(result["reference_mrr@10"], 0.4)
        self.assertEqual(result["errors"], 1)
        self.assertEqual(result["median_ms"], 200)


if __name__ == "__main__":
    unittest.main()
