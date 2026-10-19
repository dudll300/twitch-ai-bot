"""Exact text diagnosis. No generation, journal, memory or Twitch access."""
from safety import SafetyReview, local_review, review_candidate
from safety_settings import policy_scope
from privacy import contains_private_data, unsafe_question, PrivacyViolation, PrivacyAnalysisLimit


def diagnose(store, policy, text, kind, *, target='', ai=False, auth=None, answer_model='', cancelled=lambda: False):
    with policy_scope(store, policy):
        if cancelled():
            return SafetyReview('cancelled', '', ('cancelled',), stage='question' if kind == 'question' else 'answer')
        if not isinstance(text, str) or not text.strip() or len(text) > 100000:
            return SafetyReview('blocked', '', ('invalid_text',), stage=kind)
        if auth is not None and auth.api_key and auth.api_key in text:
            return SafetyReview('blocked', '', ('privacy_blocked',), stage=kind)
        if kind == 'question':
            try:
                private = contains_private_data(text)
                unsafe = private or unsafe_question(text)
            except PrivacyViolation:
                private, unsafe = True, True
            except PrivacyAnalysisLimit:
                return SafetyReview('error', '', ('privacy_analysis_limit',), stage='question')
            except Exception:
                return SafetyReview('error', '', ('privacy_check_error',), stage='question')
            return SafetyReview('blocked' if unsafe else 'local_allowed', '',
                ('privacy_blocked' if private else 'disclosure_request',) if unsafe else (), stage='question')
        if kind != 'answer':
            return SafetyReview('blocked', '', ('invalid_text',))
        local = local_review(text, target=target)
        if local.status != 'local_allowed' or not ai:
            # Results never carry the diagnosis input, even for allowed text.
            from dataclasses import replace
            return replace(local, text='')
        if auth is None or not policy.model_for(answer_model).strip():
            return SafetyReview('error', '', ('review_unavailable',))
        review = review_candidate(auth.config(), answer_model, text, kind='diagnostic', target=target, cancelled=cancelled, cancellation_reason='cancelled')
        from dataclasses import replace
        return replace(review, text='')
