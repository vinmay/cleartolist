from verify.verdict import Choice, Verdict, decide


def test_code_hit_on_chosen_candidate_is_confirmed():
    assert decide(Choice(0, "medium"), [["model:AB123"], [], []]) == (Verdict.CONFIRMED, 0)
    assert decide(Choice(1, "low"), [[], ["upc:012345678905", "brand"], []]) == (Verdict.CONFIRMED, 1)


def test_code_hit_but_verifier_picks_nothing_is_unsure():
    assert decide(Choice(None), [[], ["model:AB123"], []]) == (Verdict.UNSURE, 1)


def test_code_hit_and_verifier_picks_another_is_unsure_on_the_code_hit():
    assert decide(Choice(0, "high"), [[], ["model:AB123"]]) == (Verdict.UNSURE, 1)


def test_brand_alone_is_not_a_code():
    assert decide(Choice(None), [["brand"]]) == (Verdict.NO_MATCH, None)


def test_confident_pick_is_likely():
    assert decide(Choice(2, "high"), [[], [], []]) == (Verdict.LIKELY, 2)
    assert decide(Choice(2, "high", same_brand="yes"), [[], [], []]) == (Verdict.LIKELY, 2)


def test_weaker_or_conflicting_pick_is_unsure():
    assert decide(Choice(0, "medium"), [[]]) == (Verdict.UNSURE, 0)
    assert decide(Choice(0, "high", same_brand="no"), [[]]) == (Verdict.UNSURE, 0)


def test_contradicting_model_number_is_no_match():
    assert decide(Choice(0, "high", model_consistent="no"), [[]]) == (Verdict.NO_MATCH, None)


def test_no_pick_no_code_is_no_match():
    assert decide(Choice(None, "high"), [[], []]) == (Verdict.NO_MATCH, None)


def test_no_pick_but_very_similar_photo_is_unsure():
    assert decide(Choice(None), [[], []], top_image_score=0.85) == (Verdict.UNSURE, 0)
    assert decide(Choice(None), [[], []], top_image_score=0.7) == (Verdict.NO_MATCH, None)


def test_similarity_never_overrides_a_contradicting_model_number():
    assert decide(Choice(0, "high", model_consistent="no"), [[]], top_image_score=0.95) == (Verdict.NO_MATCH, None)
