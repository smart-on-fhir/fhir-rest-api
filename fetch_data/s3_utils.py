import logging
import boto3

s3 = boto3.client("s3")
logger = logging.getLogger()
logger.setLevel("INFO")


def clear_output_dir(bucket: str, prefix: str):
    logger.info(f"Clearing output dir {prefix}")

    paginator = s3.get_paginator("list_objects_v2")
    delete_objects = []

    count = 0
    for page in paginator.paginate(Bucket=bucket, Prefix=prefix):
        for obj in page.get("Contents", []):
            count += 1
            delete_objects.append({"Key": obj["Key"]})
            if len(delete_objects) == 1000:
                s3.delete_objects(Bucket=bucket, Delete={"Objects": delete_objects})
                delete_objects = []

    if delete_objects:
        s3.delete_objects(Bucket=bucket, Delete={"Objects": delete_objects})
    logger.info(f"Deleted {count} objects")
