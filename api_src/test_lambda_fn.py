import pytest

from api_src import lambda_fn


def test_should_route_count():
    event = {
        "path": "/my_test_cohort/fhir/patient/count",
        "pathParameters": {
            "fhir_resource": "patient",
            "cohort_id": "my_test_cohort",
        },
    }
    actual_fn = lambda_fn.determine_route(event)
    expected_fn = lambda_fn.run_count_query

    # This compares the bytecode of the function bodies
    assert actual_fn.__code__.co_code == expected_fn.__code__.co_code


@pytest.mark.parametrize(
    "event, expected_fn",
    [
        (
            {
                "path": "/my_test_cohort/fhir/patient",
                "pathParameters": {
                    "fhir_resource": "patient",
                    "cohort_id": "my_test_cohort",
                },
            },
            "fhir",
        ),
        (
            {
                "path": "/my_test_cohort/fhir/Patient",
                "pathParameters": {
                    "fhir_resource": "Patient",
                    "cohort_id": "my_test_cohort",
                },
            },
            "fhir",
        ),
        (
            {
                "path": "/my_test_cohort/fhir/patient/count",
                "pathParameters": {
                    "fhir_resource": "patient",
                    "cohort_id": "my_test_cohort",
                },
            },
            lambda_fn.run_count_query,
        ),
        (
            {
                "path": "/my_test_cohort/fhir/Patient/count",
                "pathParameters": {
                    "fhir_resource": "Patient",
                    "cohort_id": "my_test_cohort",
                },
            },
            lambda_fn.run_count_query,
        ),
    ],
)
def test_should_route_fhir(event, expected_fn):
    actual_fn = lambda_fn.determine_route(event)
    expected_fn = (
        lambda_fn.run_fhir_query if expected_fn == "fhir" else lambda_fn.run_count_query
    )

    # This compares the bytecode of the function bodies
    assert actual_fn.__code__.co_code == expected_fn.__code__.co_code


def test_should_route_404():
    event = {
        "path": "/my_test_cohort/narnia/patient",
        "pathParameters": {
            "fhir_resource": "patient",
            "cohort_id": "my_test_cohort",
        },
    }
    actual_body = lambda_fn.determine_route(event)(event)
    expected_body = {"statusCode": "404", "body": "Route not found"}

    # The 404 function is a lambda, so we compare function outputs instead
    assert actual_body == expected_body


@pytest.mark.parametrize(
    "data",
    [
        {"event": {}, "expected": True},
        {"event": {"queryStringParameters": {"offset": "1"}}, "expected": True},
        {"event": {"queryStringParameters": {"limit": "1"}}, "expected": True},
        {
            "event": {"queryStringParameters": {"offset": "1", "limit": "1"}},
            "expected": True,
        },
        {
            "event": {
                "queryStringParameters": {"offset": "1", "limit": "1", "blah": 0}
            },
            "expected": False,
        },
        {"event": {"queryStringParameters": {"blimit": "1"}}, "expected": False},
        {"event": {"queryStringParameters": {"boffset": "1"}}, "expected": False},
        {
            "event": {"queryStringParameters": {"offset": "1", "blimit": "1"}},
            "expected": False,
        },
    ],
)
def test_validate_query_params(data):
    actual = lambda_fn.validate_query_params(data["event"])
    assert data["expected"] == actual


@pytest.mark.parametrize(
    "event, cohort_id, resource, fields, patients, offset, limit",
    [
        (
            {"pathParameters": {"cohort_id": "foo", "fhir_resource": "patient"}},
            "foo",
            "patient",
            [],
            [],
            0,
            50,
        ),
        (
            {"pathParameters": {"cohort_id": "foo", "fhir_resource": "Patient"}},
            "foo",
            "patient",
            [],
            [],
            0,
            50,
        ),
        (
            {
                "pathParameters": {"cohort_id": "foo", "fhir_resource": "Encounter"},
                "body": '{"patients":["id_1", "id_2"]}',
            },
            "foo",
            "encounter",
            [],
            ["Patient/id_1", "Patient/id_2"],
            0,
            50,
        ),
        (
            {
                "pathParameters": {"cohort_id": "foo", "fhir_resource": "patient"},
                "body": '{"patients":["id_1", "id_2"]}',
            },
            "foo",
            "patient",
            [],
            ["id_1", "id_2"],
            0,
            50,
        ),
    ],
)
def test_extract_params(event, cohort_id, resource, fields, patients, offset, limit):
    a_cohort_id, a_resource, a_fields, a_patients, a_offset, a_limit = (
        lambda_fn.extract_params(event)
    )
    assert a_cohort_id == cohort_id
    assert a_resource == resource
    assert a_fields == fields
    assert a_patients == patients
    assert a_offset == offset
    assert a_limit == limit
