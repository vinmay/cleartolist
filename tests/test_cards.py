from demo.cards import hazard_tag, heading, what_to_do


def test_heading_is_brand_plus_product_in_cpsc_capitalization():
    assert heading("The Children's Place Recalls Infant Snowsuits Due to Choking Hazard") == \
        "The Children's Place Infant Snowsuits"
    assert heading("Graco Recalls Inclined Sleeper Accessory Included with Four Models of Playards to Prevent "
                   "Risk of Suffocation") == "Graco Inclined Sleeper Accessory"
    assert heading("Philips Lighting Expands Recall of Metal Halide Lamps Due to Fire Hazards") == \
        "Philips Lighting Metal Halide Lamps"


def test_heading_handles_recalled_by_and_missing_brand():
    assert heading("Women's Iron Complete Supplements Recalled by GNC Due to Failure to Meet Child Resistant "
                   "Packaging") == "GNC Women's Iron Complete Supplements"
    t = "Air Rifles Recalled by DIANA Can Unexpectedly Discharge; Risk of Serious Injury"
    assert heading(t) == "DIANA Air Rifles"
    assert heading("Pool and Patio Umbrellas Recalled Due to Injury Hazard (Recall Alert)", ["Umbrosa NV"]) == \
        "Umbrosa NV Pool and Patio Umbrellas"
    assert heading("Amazon Recalls Amazon Basics Dumbbells Due to Impact Hazard") == "Amazon Basics Dumbbells"


def test_heading_falls_back_to_a_short_title():
    h = heading("Something Odd Happened With Products That Nobody Expected At All And It Keeps Going For A Long "
                "While; more")
    assert len(h) <= 73 and "more" not in h


def test_hazard_tag_is_one_word_from_the_title_first():
    assert hazard_tag("X Recalls Y Due to Choking Hazard", ["fire"]) == "Choking"
    assert hazard_tag("X Recalls Y Due to Risk of Serious Injury or Death from Suffocation", []) == "Suffocation"
    assert hazard_tag("X Recalls Y", ["The heater can overheat, posing a fire hazard."]) == "Fire"
    assert hazard_tag("X Recalls Y", []) == "Injury"


def test_what_to_do_is_the_first_sentence_as_an_instruction():
    assert what_to_do(["Consumers should immediately stop using the snowsuits and return them for a full refund. "
                       "Online customers will get instructions."]) == \
        "Stop using the snowsuits and return them for a full refund."
    assert what_to_do([]) == ""
