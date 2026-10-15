from ingest.cpsc import (
    extract_model_numbers,
    normalize,
    normalize_manufacturer,
    normalize_model,
    normalize_upc,
)


def test_normalize_model_strips_punctuation_and_case():
    assert normalize_model("ab-123.4 x") == "AB1234X"


def test_normalize_upc_drops_ean_leading_zero():
    assert normalize_upc("0 12345-67890 5") == "012345678905"
    assert normalize_upc("0012345678905") == "012345678905"
    assert normalize_upc("012345678905") == "012345678905"


def test_normalize_manufacturer_drops_corporate_suffixes():
    assert normalize_manufacturer("Graco Children's Products, Inc.") == "graco children s products"
    assert normalize_manufacturer("IKEA USA LLC") == "ikea"


def test_extract_model_numbers_from_description():
    text = "The recall involves strollers with model numbers 1234567, AB-200X and CD300. Sold in 2019."
    models = extract_model_numbers(text)
    assert {"1234567", "AB200X", "CD300"} <= models
    assert "2019" not in models


def test_normalize_record_collects_models_and_upcs():
    rec = {
        "RecallID": 1,
        "RecallNumber": "26001",
        "RecallDate": "2026-01-02T00:00:00",
        "Title": "Acme Recalls Cribs",
        "Description": "Item number XY-99 is affected.",
        "Products": [{"Name": "Crib", "Model": "AC-100, AC-200", "Description": ""}],
        "ProductUPCs": [{"UPC": "0 12345 67890 5"}],
        "Images": [{"URL": "https://example.com/a.jpg"}],
        "Manufacturers": [{"Name": "Acme Inc."}],
        "Hazards": [{"Name": "Entrapment"}],
        "Remedies": [{"Name": "Refund"}],
    }
    n = normalize(rec)
    assert n["recall_date"] == "2026-01-02"
    assert {"AC100", "AC200", "XY99"} <= set(n["models"])
    assert n["upcs"] == ["012345678905"]
    assert n["images"] == ["https://example.com/a.jpg"]
    assert n["manufacturers"] == ["Acme Inc."]
