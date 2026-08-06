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


def test_should_route_fhir():
    event = {
        "path": "/my_test_cohort/fhir/patient",
        "pathParameters": {
            "fhir_resource": "patient",
            "cohort_id": "my_test_cohort",
        },
    }
    actual_fn = lambda_fn.determine_route(event)
    expected_fn = lambda_fn.run_fhir_query

    # This compares the bytecode of the function bodies
    assert actual_fn.__code__.co_code == expected_fn.__code__.co_code


def test_should_route_404():
    event = {
        "resource": "/my_test_cohort/narnia/patient",
        "pathParameters": {
            "fhir_resource": "patient",
            "cohort_id": "my_test_cohort",
        },
    }
    actual_body = lambda_fn.determine_route(event)(event)
    expected_body = {"statusCode": "404", "body": "Route not found"}

    # The 404 function is a lambda, so we compare function outputs instead
    assert actual_body == expected_body
