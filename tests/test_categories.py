from ingest.categories import categorize, is_child


def test_children_subcategories():
    assert categorize("Graco Recalls Strollers Due to Fall Hazard") == "stroller"
    assert categorize("Gimars Playpens Recalled Due to Entrapment") == "play_yard"
    assert categorize("Acme Recalls Cribs Due to Entrapment") == "crib_bassinet_sleeper"
    assert categorize("Toy Co Recalls Plush Toys Due to Choking Hazard") == "toy"
    assert is_child("stroller") and not is_child("car_seat")


def test_hazard_clause_does_not_make_it_a_childrens_product():
    title = "KICIMPRO Kitchen Faucets Recalled Due to Lead Exposure for Young Children"
    assert categorize(title) == "appliance_fixture"


def test_vehicles_win_over_youth():
    assert categorize("Polaris Recalls Youth ATVs Due to Crash Hazard") == "vehicle"


def test_car_seats_flagged():
    assert categorize("Brand Recalls Infant Car Seats Due to Fall Hazard") == "car_seat"
