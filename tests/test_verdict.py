from verify.verdict import Verdict, decide


def test_code_hit_is_confirmed_and_points_at_that_recall():
    assert decide([["model:AB123"], [], []], [0.5, 0.9, 0.4]) == (Verdict.CONFIRMED, 0)
    assert decide([[], ["upc:012345678905", "brand"], []], [0.9, 0.1, 0.1]) == (Verdict.CONFIRMED, 1)


def test_brand_alone_is_not_a_code():
    assert decide([["brand"], [], []], [0.1, 0.1, 0.1], image_sim=0.7) == (Verdict.NO_MATCH, None)


def test_similar_photo_is_possible_never_confirmed():
    assert decide([[], [], []], [0.5, 0.75, None], image_sim=0.7) == (Verdict.POSSIBLE, None)


def test_dissimilar_photo_is_no_match():
    assert decide([[], [], []], [0.5, 0.6, None], image_sim=0.7) == (Verdict.NO_MATCH, None)


def test_text_led_label_photo_shows_the_shortlist_when_enabled():
    assert decide([[], [], []], [None, None, None], text_led=True, if_text_led=True) == (Verdict.POSSIBLE, None)
    assert decide([[], [], []], [None, None, None], text_led=True, if_text_led=False) == (Verdict.NO_MATCH, None)


def test_empty_shortlist_is_no_match():
    assert decide([], [], text_led=True, if_text_led=True) == (Verdict.NO_MATCH, None)


def test_code_match_vetoed_by_a_contradicting_brand():
    assert decide([["model:100D"], []], [0.5, 0.4], image_sim=0.7, brand_conflict=[True, False]) == (
        Verdict.NO_MATCH, None)
    assert decide([["model:100D"], []], [0.9, 0.4], image_sim=0.7, brand_conflict=[True, False]) == (
        Verdict.POSSIBLE, None)
    assert decide([["model:4000"], ["model:AB1234"]], [0.5, 0.4], brand_conflict=[True, False]) == (
        Verdict.CONFIRMED, 1)


def test_closest_recalls_only_for_children_and_only_when_nothing_triggered():
    from verify.verdict import show_closest

    assert show_closest(Verdict.NO_MATCH, True, None)
    assert show_closest(Verdict.NO_MATCH, False, "stroller")
    assert not show_closest(Verdict.NO_MATCH, False, "kitchen")
    assert not show_closest(Verdict.POSSIBLE, True, "stroller")
