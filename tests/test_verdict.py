from verify.verdict import Judgment, Verdict, candidate_verdict, overall


def J(t=True, brand="cannot_tell", model="cannot_tell", vis="strong"):
    return Judgment(t, brand, model, vis)


def test_exact_code_plus_same_type_is_confirmed():
    assert candidate_verdict(J(vis="partial"), ["model:AB123"]) == Verdict.CONFIRMED
    assert candidate_verdict(J(), ["upc:012345678905", "brand"]) == Verdict.CONFIRMED


def test_exact_code_but_different_product_is_no_match():
    assert candidate_verdict(J(t=False), ["model:AB123"]) == Verdict.NO_MATCH


def test_exact_code_with_conflicting_brand_is_unsure():
    assert candidate_verdict(J(brand="no"), ["model:AB123"]) == Verdict.UNSURE


def test_brand_only_is_not_an_exact_code():
    assert candidate_verdict(J(vis="partial"), ["brand"]) == Verdict.UNSURE


def test_strong_visual_without_code_is_likely():
    assert candidate_verdict(J(brand="yes"), []) == Verdict.LIKELY
    assert candidate_verdict(J(), []) == Verdict.LIKELY


def test_contradicting_model_number_is_no_match():
    assert candidate_verdict(J(model="no"), []) == Verdict.NO_MATCH


def test_weak_or_wrong_brand_is_no_match_partial_is_unsure():
    assert candidate_verdict(J(vis="weak"), []) == Verdict.NO_MATCH
    assert candidate_verdict(J(brand="no"), []) == Verdict.NO_MATCH
    assert candidate_verdict(J(vis="partial"), []) == Verdict.UNSURE


def test_overall_takes_most_severe():
    assert overall([Verdict.NO_MATCH, Verdict.UNSURE, Verdict.LIKELY]) == Verdict.LIKELY
    assert overall([]) == Verdict.NO_MATCH
