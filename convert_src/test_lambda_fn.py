import json

import boto3
import duckdb
import moto
import pytest
from moto.moto_server import threaded_moto_server

from convert_src import lambda_fn

MOTO_SERVER_IP = "127.0.0.1"
MOTO_SERVER_PORT = 5000
server = threaded_moto_server.ThreadedMotoServer(
    ip_address=MOTO_SERVER_IP, port=MOTO_SERVER_PORT
)
server.start()


def with_key(key: str) -> dict:
    s3_object = '{"Records":[{"s3":{"bucket":{"name":"fake_bucket"},"object":{"key":"__KEY__"}}}]}'
    return {"body": s3_object.replace("__KEY__", key)}


def with_local_path(local_path: str) -> dict:
    return {"body": json.dumps({"Records": [{"local_path": local_path}]})}


def test_batch_into_fhir_types_with_no_records():
    batches, bucket_name = lambda_fn.batch_into_fhir_types({"Records": []})

    assert batches == {}
    assert bucket_name is None


def test_batch_into_fhir_types_groups_s3_files_by_resource():
    event = {
        "Records": [
            with_key("my_json_cohort/patient/Patient.000.ndjson"),
            with_key("my_json_cohort/patient/Patient.001.ndjson"),
            with_key("my_json_cohort/encounter/Encounter.000.ndjson"),
        ]
    }

    batches, bucket_name = lambda_fn.batch_into_fhir_types(event)

    assert bucket_name == "fake_bucket"
    assert batches == {
        "patient": lambda_fn.ConversionBatch(
            src="s3://fake_bucket/my_json_cohort/patient/*.ndjson",
            dest="s3://fake_bucket/my_json_cohort/patient/patient_compacted.parquet",
            delete_prefix="my_json_cohort/patient",
        ),
        "encounter": lambda_fn.ConversionBatch(
            src="s3://fake_bucket/my_json_cohort/encounter/*.ndjson",
            dest="s3://fake_bucket/my_json_cohort/encounter/encounter_compacted.parquet",
            delete_prefix="my_json_cohort/encounter",
        ),
    }


def test_batch_into_fhir_types_decodes_s3_keys():
    event = {
        "Records": [
            with_key("my_json_cohort/patient/Patient%20000.ndjson"),
        ]
    }

    batches, bucket_name = lambda_fn.batch_into_fhir_types(event)

    assert bucket_name == "fake_bucket"
    assert batches["patient"].src == "s3://fake_bucket/my_json_cohort/patient/*.ndjson"


def test_batch_into_fhir_types_with_local_file(tmp_path):
    local_path = str(tmp_path / "my_cohort" / "patient" / "Patient.000.ndjson")

    batches, bucket_name = lambda_fn.batch_into_fhir_types(
        {"Records": [with_local_path(local_path)]}
    )

    assert bucket_name is None
    assert batches == {
        "Patient.000.ndjson": lambda_fn.ConversionBatch(
            src=str(tmp_path / "my_cohort" / "patient" / "*.ndjson"),
            dest=str(
                tmp_path
                / "my_cohort"
                / "patient"
                / "Patient.000.ndjson_compacted.parquet"
            ),
            delete_prefix=str(tmp_path / "my_cohort" / "patient"),
        )
    }


def test_batch_into_fhir_types_ignores_files_in_raw():
    event = {
        "Records": [
            with_key("my_json_cohort/patient/raw/Patient.000.ndjson"),
        ]
    }

    batches, bucket_name = lambda_fn.batch_into_fhir_types(event)

    assert batches == {}
    assert bucket_name == "fake_bucket"


def test_batch_into_fhir_types_ignores_local_files_in_raw(tmp_path):
    local_path = str(tmp_path / "my_cohort" / "patient" / "raw" / "Patient.000.ndjson")

    batches, bucket_name = lambda_fn.batch_into_fhir_types(
        {"Records": [with_local_path(local_path)]}
    )

    assert batches == {}
    assert bucket_name is None


@moto.mock_aws
def test_convert_from_sqs_queue(tmp_path):
    ndjsons = [
        "test_data/my_json_cohort/encounter/Encounter.000.ndjson",
        "test_data/my_json_cohort/encounter/Encounter.001.ndjson",
        "test_data/my_json_cohort/patient/Patient.000.ndjson",
        "test_data/my_json_cohort/patient/Patient.001.ndjson",
    ]
    sqs_message = {
        "Records": [with_key(ndjson.replace("test_data/", "")) for ndjson in ndjsons]
    }
    bucket_name = "fake_bucket"
    s3 = boto3.client(
        "s3",
        region_name="us-east-1",
        endpoint_url=f"http://{MOTO_SERVER_IP}:{MOTO_SERVER_PORT}",
    )
    s3.create_bucket(Bucket=bucket_name)
    for ndjson in ndjsons:
        s3.upload_file(ndjson, bucket_name, ndjson.replace("test_data/", ""))

    lambda_fn.s3 = s3
    lambda_fn.create_s3_based_db_con = create_s3_based_db_con
    lambda_fn.lambda_handler(sqs_message, {})
    for ndjson in ndjsons:
        key = ndjson.replace("test_data/", "")
        prefix, filename = key.rsplit("/", 1)
        s3.head_object(
            Bucket=bucket_name,
            Key=f"{prefix}/raw/{filename}",
        )
        with pytest.raises(s3.exceptions.ClientError):
            s3.head_object(Bucket=bucket_name, Key=key)
    s3.download_file(
        bucket_name,
        "my_json_cohort/encounter/encounter_compacted.parquet",
        tmp_path / "encounter.parquet",
    )
    s3.download_file(
        bucket_name,
        "my_json_cohort/patient/patient_compacted.parquet",
        tmp_path / "patient.parquet",
    )
    encounter_count = duckdb.query(
        f"SELECT COUNT(*) FROM '{tmp_path}/encounter.parquet'"
    ).fetchone()[0]
    patient_count = duckdb.query(
        f"SELECT COUNT(*) FROM '{tmp_path}/patient.parquet'"
    ).fetchone()[0]
    assert encounter_count == 601
    assert patient_count == 11


def create_s3_based_db_con() -> duckdb.DuckDBPyConnection:
    session = boto3.session.Session()  # type: ignore
    credentials = session.get_credentials().get_frozen_credentials()
    con = duckdb.connect(":memory:")
    con.execute("INSTALL httpfs;")
    con.execute("LOAD httpfs;")
    con.execute("SET s3_region='us-east-1';")
    con.execute(f"SET s3_access_key_id='{credentials.access_key}';")
    con.execute(f"SET s3_secret_access_key='{credentials.secret_key}';")
    con.execute(f"SET s3_session_token='{credentials.token}';")

    con.execute("SET s3_endpoint='localhost:5000';")
    con.execute("SET s3_use_ssl=false;")
    con.execute("SET s3_url_style='path';")
    return con
