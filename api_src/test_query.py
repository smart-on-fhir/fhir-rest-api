from api_src import query


def test_should_get_all_patients():
    result = query.get_fhir_data("patient", "my_parquet_cohort", [], [], 0, 1000)

    assert len(result) == 5


def test_should_paginate_patients():
    result = query.get_fhir_data("patient", "my_parquet_cohort", [], [], 0, 2)

    assert len(result) == 2


def test_should_get_patients_with_fields():
    result = query.get_fhir_data("patient", "my_parquet_cohort", ["id"], [], 0, 1000)
    assert [{"id": "3"}, {"id": "5"}, {"id": "2"}, {"id": "4"}, {"id": "1"}] == result


def test_should_get_patients_with_fields_and_filters():
    result = query.get_fhir_data(
        "patient", "my_parquet_cohort", ["id"], ["1", "2", "3"], 0, 1000
    )
    assert [{"id": "3"}, {"id": "2"}, {"id": "1"}] == result


def test_should_get_episode_of_care():
    result = query.get_fhir_data(
        "episodeofcare", "my_parquet_cohort", ["id"], [], 0, 1000
    )
    assert len(result) == 2


def test_should_get_medication_request():
    result = query.get_fhir_data(
        "medicationrequest", "my_parquet_cohort", ["id"], [], 0, 1000
    )
    assert len(result) == 2
