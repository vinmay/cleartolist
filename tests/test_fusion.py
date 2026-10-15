from retrieve.exact import model_candidates, upc_candidates
from retrieve.fusion import RRF_K, boost_exact, rrf


def test_rrf_rewards_agreement_across_lists():
    out = rrf({"image": [1, 2, 3], "text": [3, 4, 1]})
    ids = [c.recall_id for c in out]
    assert ids[:2] == [1, 3]  # in both lists
    assert out[0].sources == {"image": 1, "text": 3}
    assert abs(out[0].score - (1 / (RRF_K + 1) + 1 / (RRF_K + 3))) < 1e-12


def test_rrf_weights_and_duplicates():
    out = rrf({"image": [1, 1, 2], "brand": [2]}, weights={"brand": 0.5})
    by = {c.recall_id: c for c in out}
    assert by[1].sources == {"image": 1}
    assert by[2].score == 1 / (RRF_K + 3) + 0.5 / (RRF_K + 1)


def test_boost_exact_moves_model_hits_first_but_not_brand_only():
    fused = rrf({"image": [1, 2, 3]})
    out = boost_exact(fused, {3: ["model:AB123"], 2: ["brand"], 9: ["upc:012345678905"]})
    ids = [c.recall_id for c in out]
    assert ids[:2] == [3, 9]  # exact hits first (fused score breaks the tie; 9 had none)
    assert ids[2:] == [1, 2]
    assert out[0].exact == ["model:AB123"]


def test_model_candidates_filters_noise():
    got = model_candidates("none", "MODEL: GR-1234 Made in 2019 SN 55 abc")
    assert "GR1234" in got
    assert "2019" not in got
    assert "NONE" not in got
    assert model_candidates("", "") == set()


def test_upc_candidates():
    assert upc_candidates("", "barcode 0 12345 67890 5 here") == {"012345678905"}
