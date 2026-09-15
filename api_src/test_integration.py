import json
import os
import shutil

import pytest

from api_src import lambda_fn as api_lambda_fn
from convert_src import lambda_fn as convert_lambda_fn


def validate_cors(response):
    expected = {
        "Access-Control-Allow-Headers": "Content-Type",
        "Access-Control-Allow-Origin": "*",
        "Access-Control-Allow-Methods": "POST, GET",
    }
    for k, v in expected.items():
        headers = response.get("headers", {})
        assert headers.get(k, "") == v


@pytest.mark.parametrize(
    "fhir_type, expected_count",
    [
        ("allergyintolerance", 10),
        ("condition", 10),
        ("encounter", 10),
        ("medication", 10),
        ("organization", 10),
        ("procedure", 10),
        ("appointment", 1),
        ("device", 6),
        ("imagingstudy", 2),
        ("medicationadministration", 4),
        ("patient", 1),
        ("careplan", 5),
        ("diagnosticreport", 10),
        ("immunization", 10),
        ("medicationrequest", 10),
        ("practitioner", 10),
        ("careteam", 5),
        ("documentreference", 10),
        ("location", 10),
        ("observation", 10),
        ("practitionerrole", 10),
    ],
)
def test_convert_data_and_query(fhir_type, expected_count, tmp_path, monkeypatch):
    full_data_path = f"{tmp_path}/my_cohort"
    shutil.copytree("./test_data/all_fhir_types", full_data_path)

    # for each ndjson file in the copied directory, convert it to parquet using the conversion lambda function
    for filename in os.listdir(f"{tmp_path}/my_cohort/{fhir_type}"):
        record = {
            "Records": [{"local_path": f"{tmp_path}/my_cohort/{fhir_type}/{filename}"}]
        }
        convert_event = {"Records": [{"body": json.dumps(record)}]}
        convert_lambda_fn.lambda_handler(convert_event, None)

    event = {
        "pathParameters": {"fhir_resource": fhir_type, "cohort_id": "my_cohort"},
        "queryStringParameters": {},
        "body": '{"patients": ["Alden-Chong-Murphy"]}',
        "path": f"/fhir/my_cohort/{fhir_type}/count",
    }
    monkeypatch.setattr(api_lambda_fn.env, "local_root", tmp_path)
    response = api_lambda_fn.lambda_handler(event, None)
    assert response["body"] == expected_count
    validate_cors(response)
    event = {
        "pathParameters": {"fhir_resource": fhir_type, "cohort_id": "my_cohort"},
        "queryStringParameters": {},
        "body": '{"patients": ["Alden-Chong-Murphy"]}',
        "path": f"/fhir/my_cohort/{fhir_type}",
    }
    response = api_lambda_fn.lambda_handler(event, None)
    assert response["statusCode"] == 200
    validate_cors(response)


def test_convert_data_and_query_patient(tmp_path, monkeypatch):
    full_data_path = f"{tmp_path}/my_cohort"
    shutil.copytree("./test_data/all_fhir_types", full_data_path)

    # for each ndjson file in the copied directory, convert it to parquet using the conversion lambda function
    for fhir_type in os.listdir(f"{tmp_path}/my_cohort/"):
        records = []
        for filename in os.listdir(f"{tmp_path}/my_cohort/{fhir_type}"):
            records.append(
                {"local_path": f"{tmp_path}/my_cohort/{fhir_type}/{filename}"}
            )
        convert_event = {"Records": [{"body": json.dumps({"Records": records})}]}
        convert_lambda_fn.lambda_handler(convert_event, None)
    fhir_type = "patient"

    event = {
        "pathParameters": {
            "cohort_id": "my_cohort",
            "patient_id": "Alden-Chong-Murphy",
        },
        "queryStringParameters": {},
        "path": "/fhir/my_cohort/patient/Alden-Chong-Murphy",
    }
    monkeypatch.setattr(api_lambda_fn.env, "local_root", tmp_path)
    response = api_lambda_fn.lambda_handler(event, None)
    assert response["statusCode"] == 200
    validate_cors(response)
