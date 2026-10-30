import dataclasses
import json
import logging
import os
import pathlib
from urllib import parse

import boto3
import duckdb

logger = logging.getLogger()
logger.setLevel("INFO")

s3 = boto3.client("s3")


@dataclasses.dataclass
class ConversionBatch:
    src: str
    dest: str
    delete_prefix: str


def lambda_handler(event, context):
    batches, bucket_name = batch_into_fhir_types(event)
    for fhir_type, batch in batches.items():
        logger.info(
            f"Converting {fhir_type} jsons in {batch.src} to parquet in {batch.dest}"
        )
        if bucket_name:
            con = create_s3_based_db_con()
            logger.info("Credentials created. Converting...")
        else:
            con = duckdb.connect(":memory:")
            logger.info("No s3 detected in src/dest, using local db")
        convert_json(batch.src, batch.dest, con)
        move_matching(bucket_name, batch.delete_prefix)
    logger.info("Done :)")


def batch_into_fhir_types(event) -> tuple[dict[str, ConversionBatch], str | None]:
    ndjsons: dict[str, ConversionBatch] = {}
    bucket = None
    for sqs_record in event["Records"]:
        s3_event = json.loads(sqs_record["body"])
        for record in s3_event["Records"]:
            if "s3" in record:
                bucket = record["s3"]["bucket"]["name"]
                key = record["s3"]["object"]["key"]
                key = parse.unquote_plus(key)
                # key will always be study/fhir_type/filename.ndjson
                parts = pathlib.Path(key).parts
                # avoid recursion: ignore raw
                if parts[-2] == "raw":
                    logger.debug(f"Skipping raw file {key}")
                    continue
                resource = parts[1]
                ndjson_path = "s3://" + os.path.join(
                    *(bucket,) + parts[:-1] + ("*.ndjson",)
                )
                parquet_dest = "s3://" + os.path.join(
                    *(bucket,) + parts[:-1] + (f"{resource}_compacted.parquet",)
                )
                delete_prefix = os.path.join(*parts[:-1])
            else:
                local_path: str = record["local_path"]
                parts = pathlib.Path(local_path).parts
                if parts[-2] == "raw":
                    logger.debug(f"Skipping raw file {local_path}")
                    continue
                resource = parts[-1]
                if local_path.endswith("*.ndjson"):
                    ndjson_path = local_path
                    parquet_dest = ndjson_path.replace(
                        "*.ndjson", f"{resource}_compacted.parquet"
                    )
                else:
                    ndjson_path = os.path.join(*parts[:-1] + ("*.ndjson",))
                    parquet_dest = os.path.join(
                        *parts[:-1] + (f"{resource}_compacted.parquet",)
                    )
                delete_prefix = os.path.join(*parts[:-1])
            ndjsons[resource] = ConversionBatch(
                ndjson_path, parquet_dest, delete_prefix
            )
    return ndjsons, bucket


def move_matching(
    bucket_name: str | None, prefix: str, suffix=".ndjson", destination="raw"
):
    if bucket_name is None:
        destination_path = pathlib.Path(prefix) / destination
        destination_path.mkdir(parents=True, exist_ok=True)
        for item in pathlib.Path(prefix).iterdir():
            if item.is_file() and item.name.endswith(suffix):
                item.rename(destination_path / item.name)
    else:
        logger.info(f"Moving ndjsons from bucket with prefix {prefix}..")
        paginator = s3.get_paginator("list_objects_v2")
        moved = 0
        destination_prefix = f"{prefix.rstrip('/')}/{destination}/"

        for page in paginator.paginate(Bucket=bucket_name, Prefix=prefix):
            for obj in page.get("Contents", []):
                source_key = obj["Key"]
                if not source_key.endswith(suffix) or source_key.startswith(
                    destination_prefix
                ):
                    continue
                relative_key = source_key[len(prefix) :].lstrip("/")
                destination_key = f"{destination_prefix}{relative_key}"
                s3.copy_object(
                    Bucket=bucket_name,
                    CopySource={"Bucket": bucket_name, "Key": source_key},
                    Key=destination_key,
                )
                s3.delete_object(Bucket=bucket_name, Key=source_key)
                moved += 1
        logger.info(f"Moved {moved} files")


def convert_json(source: str, destination: str, con: duckdb.DuckDBPyConnection):
    logger.info(f"Converting json {source} to {destination}")
    query = """
        COPY (
            SELECT * REPLACE (id::VARCHAR AS id)
            FROM read_ndjson_auto(?, union_by_name=true)
        ) 
        TO ? 
        (
            FORMAT PARQUET, 
            COMPRESSION 'zstd'
        );
        """
    con.execute(query, [destination, source])


def create_s3_based_db_con() -> duckdb.DuckDBPyConnection:
    session = boto3.session.Session()  # type: ignore
    credentials = session.get_credentials().get_frozen_credentials()
    logger.info("Creating duckdb connection")
    con = duckdb.connect(":memory:")
    logger.info("Setting up s3 query config")
    con.execute("INSTALL httpfs;")
    con.execute("LOAD httpfs;")
    con.execute("SET s3_region='us-east-1';")
    con.execute(f"SET s3_access_key_id='{credentials.access_key}';")
    con.execute(f"SET s3_secret_access_key='{credentials.secret_key}';")
    con.execute(f"SET s3_session_token='{credentials.token}';")
    return con
