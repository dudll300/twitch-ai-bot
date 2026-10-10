"""Legacy scenarios isolate their original invariants from paid semantic review.

The actual review transport, quotas, rejection, and publication gates are exercised
without these stubs in test_safety.py and test_moderation.py.
"""
from unittest.mock import patch
from safety import SafetyReview


def approved(cfg, model, text, **options):
    return SafetyReview("allowed", text)


def stub_reviews(test):
    for target in ("autonomous.review_candidate", "testing.review_candidate"):
        mock = patch(target, side_effect=approved)
        mock.start()
        test.addCleanup(mock.stop)
