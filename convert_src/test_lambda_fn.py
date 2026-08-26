import boto3
import duckdb
import moto
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
