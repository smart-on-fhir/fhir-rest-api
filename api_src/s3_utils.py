import asyncio
import logging
import os
import pathlib
import shutil
import time
from os import path

import boto3
import env
from botocore import exceptions

logger = logging.getLogger()
logger.setLevel("INFO")
s3 = boto3.client("s3")
paginator = s3.get_paginator("list_objects_v2")

NINE_GIGS_IN_BYTES = 9.5 * 1024 * 1024 * 1024
STAGING_SUFFIX = ".staging"
# Requests timeout after 30 secs, so this is more documenting the existing constraint
WARMING_TIMEOUT = 30
POLLING_INTERVAL: float = 0.1


def prepare_local_data_dir(bucket: str, cohort_id: str) -> bool:
    if bucket:
        # We were seeing significant performance issues when duckdb was reading from S3 directly, so we now download the data to the local /tmp directory first.
        local_dir = pathlib.Path(path.join(env.local_root, cohort_id))
        staging_dir = local_dir.with_suffix(STAGING_SUFFIX)

        # If the cache is already present, just return
        if local_dir.exists() and local_dir.is_dir():
            logger.info(
                f"Local directory {local_dir} already exists, skipping download"
            )
            return True
        # Attempt to make the cache staging dir. If it already exists, wait for the cache to finish being staged
        staging_dir.parent.mkdir(parents=True, exist_ok=True)
        while True:
            try:
                staging_dir.mkdir()
                break
            except FileExistsError:
                if not wait_for_dir_removal(staging_dir, WARMING_TIMEOUT):
                    logger.info(
                        f"Detected staging dir {staging_dir}, indicating that an other invocation is warming cache"
                    )
                    logger.info(
                        f"The dir existed for {WARMING_TIMEOUT} seconds, so this lambda will exit."
                    )
                    logger.info("If these errors persist, redeploy the lambda.")
                    return False
                if local_dir.exists() and local_dir.is_dir():
                    logger.info(
                        f"Local directory {local_dir} already exists, skipping download"
                    )
                    return True

        # At this point, we know that this invocation is responsible for warming the cache
        # So clear it, and start downloading data
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
        asyncio.run(download_s3_parquets(bucket, cohort_id, staging_dir, local_dir))
        logger.info(f"Listing objects in {local_dir}")
        for p in local_dir.rglob("*"):
            if p.is_file():
                logger.info(p)
        return True
    return True


def wait_for_dir_removal(path: pathlib.Path, timeout: float) -> bool:
    p = pathlib.Path(path)
    deadline = time.monotonic() + timeout

    while p.is_dir():
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            return False
        time.sleep(min(POLLING_INTERVAL, remaining))

    return True


def calculate_object_size_bytes(bucket_name: str, prefix: str) -> int:
    total_bytes = 0
    page_iterator = paginator.paginate(Bucket=bucket_name, Prefix=prefix)
    for page in page_iterator:
        if "Contents" in page:
            for obj in page["Contents"]:
                total_bytes += obj["Size"]

    return total_bytes


async def download_s3_parquets(
    bucket: str, prefix: str, staging_dir: pathlib.Path, cache_dir: pathlib.Path
):
    os.makedirs(staging_dir, exist_ok=True)

    logger.info(
        f"Downloading S3 objects from bucket: {bucket} path: {prefix} to {staging_dir}"
    )

    async def download(s3_key: str, local_path: pathlib.Path) -> bool:
        local_path.parent.mkdir(parents=True, exist_ok=True)
        try:
            s3.download_file(bucket, s3_key, local_path)
            return True
        except exceptions.ClientError as e:
            logger.info(f" {bucket=} {s3_key=} {local_path=} Error: {e}")
            return False

    keys = []
    tasks = []
    for page in paginator.paginate(Bucket=bucket, Prefix=prefix):
        for obj in page.get("Contents", []):
            keys.append(pathlib.Path(obj["Key"]))
    async with asyncio.TaskGroup() as tg:
        for key in keys:
            local_path = staging_dir / key.parent.name / key.name
            tasks.append(tg.create_task(download(str(key), local_path)))
    results = [task.result() for task in tasks]
    logger.info(f"All {len(results)} finished. Moving to {cache_dir} and returning")
    logger.info(f"Results:\n{[f'\t{r}\n' for r in results]}")
    shutil.move(staging_dir, cache_dir)


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
        return os.listdir(env.local_root / cohort_id)
