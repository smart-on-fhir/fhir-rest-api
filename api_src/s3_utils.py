import logging
import os
import shutil
import pathlib
from os import path


import boto3
import env

logger = logging.getLogger()
logger.setLevel("INFO")
s3 = boto3.client("s3")
paginator = s3.get_paginator("list_objects_v2")

NINE_GIGS_IN_BYTES = 9.5 * 1024 * 1024 * 1024


def prepare_local_data_dir(resource: str, bucket: str, cohort_id: str) -> bool:
    if bucket:
        # We were seeing significant performance issues when duckdb was reading from S3 directly, so we now download the data to the local /tmp directory first.
        local_dir = pathlib.Path(path.join("/tmp", bucket, cohort_id))
        # Ugly caching logic to avoid repeat downloads
        if local_dir.exists() and local_dir.is_dir():
            logger.info(
                f"Local directory {local_dir} already exists, skipping download"
            )
            return True
        else:
            shutil.rmtree(env.local_root, ignore_errors=True)

        logger.info("S3_ROOT is set, downloading data from S3")
        logger.info(
            f"Calculating size of S3 objects in bucket: {bucket} with prefix: {cohort_id}"
        )
        size_bytes = calculate_object_size_bytes(bucket, cohort_id)
        # Lambda storage limit 10G
        if size_bytes > NINE_GIGS_IN_BYTES:
            logger.error(f"Data size {size_bytes} bytes exceeds 9GB limit")
            return False
        logger.info(f"Data size is {size_bytes} bytes, proceeding to download")
        download_s3_parquets(bucket, cohort_id, local_dir)
        logger.info(f"Downloaded S3 objects to {local_dir}")
        return True
    return True


def calculate_object_size_bytes(bucket_name: str, prefix: str) -> int:
    total_bytes = 0
    page_iterator = paginator.paginate(Bucket=bucket_name, Prefix=prefix)
    for page in page_iterator:
        if "Contents" in page:
            for obj in page["Contents"]:
                total_bytes += obj["Size"]

    return total_bytes


def download_s3_parquets(bucket: str, prefix: str, local_dir: pathlib.Path) -> None:
    os.makedirs(local_dir, exist_ok=True)

    logger.info(
        f"Downloading S3 objects from bucket: {bucket} path: {prefix} to {local_dir}"
    )

    for page in paginator.paginate(Bucket=bucket, Prefix=prefix):
        for obj in page.get("Contents", []):
            key = pathlib.Path(obj["Key"])
            local_path = local_dir / key.parent.name / key.name
            local_path.parent.mkdir(parents=True, exist_ok=True)
            s3.download_file(bucket, str(key), str(local_path))


def list_s3_subdirectories(bucket: str, prefix: str) -> list[str]:
    if prefix and not prefix.endswith("/"):
        prefix += "/"

    subdirectories = []

    logger.info(f"Scanning S3 subdirectories under: s3://{bucket}/{prefix}")
    pages = paginator.paginate(
        Bucket=bucket,
        Prefix=prefix,
        Delimiter="/",  # Tells S3 to roll up everything past this slash into CommonPrefixes
    )
    for page in pages:
        if "CommonPrefixes" in page:
            for cp in page["CommonPrefixes"]:
                folder_path = cp["Prefix"]
                # Strip out the parent prefix to return just the relative directory name
                relative_dir = folder_path[len(prefix) :]
                subdirectories.append(relative_dir.replace("/", ""))
    return subdirectories


def get_fhir_resource_types(cohort_id: str) -> list[str]:
    if env.uses_s3():
        return list_s3_subdirectories(env.source_bucket, cohort_id)
    else:
        return os.listdir(env.local_root)
