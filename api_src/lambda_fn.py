import argparse
import datetime
import json
import logging
import math
import uuid

import env
import query
import s3_utils

logger = logging.getLogger()
logger.setLevel("INFO")
ALLOWED_PARAMS = {"offset", "limit"}


def lambda_handler(event, context):
    return determine_route(event)(event)


def run_count_query(event) -> dict:
    cohort_id, resource, _, patients, _, _ = extract_params(event)
    resources = s3_utils.get_fhir_resource_types(cohort_id)
    logger.info(f"Found the following resources: {resources}")

    success = s3_utils.prepare_local_data_dir(env.source_bucket, cohort_id)
    if not success:
        logger.error("Failed to prepare local data directory due to size constraints.")
        return {
            "statusCode": 500,
            "body": "Data size exceeds 9GB lambda storage constraint.",
        }
    try:
        # We'll get the exact case of the S3 path for file fetching
        index = [x.lower() for x in resources].index(resource)
        s3_resource = resources[index]
        logger.info("Fetching counts")
        count = query.get_fhir_count(s3_resource, cohort_id, patients)
        logger.info(f"Count for resource {resource} is {count}")
        return {"statusCode": 200, "body": count}
    except ValueError:
        return {
            "statusCode": "404",
            "body": f"Resource {resource} not found",
        }


def _json_type_check(obj):
    if isinstance(obj, datetime.datetime):
        return obj.isoformat()
    if isinstance(obj, uuid.UUID):
        return str(obj)


def run_fhir_query(event) -> dict:
    cohort_id, resource, fields, patients, offset, limit = extract_params(event)
    resources = s3_utils.get_fhir_resource_types(cohort_id)
    logger.info(f"Found the following resources: {resources}")

    success = s3_utils.prepare_local_data_dir(env.source_bucket, cohort_id)
    if not success:
        logger.error("Failed to prepare local data directory due to size constraints.")
        return {
            "statusCode": 500,
            "body": "Data size exceeds 9GB lambda storage constraint.",
        }
    try:
        # We'll get the exact case of the S3 path for file fetching
        index = [x.lower() for x in resources].index(resource)
        s3_resource = resources[index]
        logger.info("Fetching counts")
        count = query.get_fhir_count(s3_resource, cohort_id, patients)
        logger.info(f"Count for resource {resource} is {count}")
        logger.info("Fetching data")
        data = query.get_fhir_data(
            s3_resource, cohort_id, fields, patients, offset, limit
        )
        logger.info("Processing data")
        resources.remove(s3_resource)
        body = {
            "fhir": data,
            "pagination": {
                "count": math.ceil(count / limit),
                "first": f"/fhir/{resource}/?offset=0&limit={limit}",
                "last": f"/fhir/{resource}/?offset={math.floor(count / limit) * limit}&limit={limit}",
                "limit": limit,
                "next": f"/fhir/{resource}/?offset={offset + limit}&limit={limit}",
                "offset": offset,
                "previous": f"/fhir/{resource}/?offset={max(offset - limit, 0)}&limit={limit}",
                "total": count,
            },
            "otherResources": resources,
        }
        logger.info("Done")
        return {"statusCode": 200, "body": json.dumps(body, default=_json_type_check)}
    except ValueError:
        return {
            "statusCode": "404",
            "body": f"Resource {resource} not found",
        }


def determine_route(event):
    if not validate_query_params:
        return lambda e: {"statusCode": "400", "body": "Invalid query params."}
    cohort_id, resource, _, _, _, _ = extract_params(event)
    uncased_resource = event.get("pathParameters").get("fhir_resource")
    route = event.get("path").replace(uncased_resource, resource)
    base_route = f"/{cohort_id}/fhir/{resource}"
    if route in [f"{base_route}/count", f"{base_route}/count/"]:
        return run_count_query
    if route in [f"{base_route}", f"{base_route}/"]:
        return run_fhir_query
    return lambda e: {"statusCode": "404", "body": "Route not found"}


def validate_query_params(event) -> bool:
    return event.get("queryStringParameters", {}).keys() <= ALLOWED_PARAMS


def extract_params(event) -> tuple[str, str, list[str], list[str], int, int]:
    resource = event.get("pathParameters").get("fhir_resource").lower()
    cohort_id = event.get("pathParameters").get("cohort_id")
    fields = []
    patients = []
    offset = 0
    limit = 50
    if event.get("body"):
        body = json.loads(event.get("body"))
        fields = body.get("fields", [])
        patients = body.get("patients", [])
    if event.get("queryStringParameters"):
        offset = int(event.get("queryStringParameters").get("offset", "0"))
        limit = int(event.get("queryStringParameters").get("limit", "50"))
    if resource != "patient":
        patients = [f"Patient/{p}" for p in patients] if patients else []
    return cohort_id, resource, fields, patients, offset, limit


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Run FHIR Lambda handler from CLI")
    parser.add_argument("--fhir_resource", required=True, help="FHIR resource type")
    parser.add_argument("--count", default=False, help="Is this a count query?")
    parser.add_argument("--cohort_id", required=True, help="Cohort dir name in s3")
    parser.add_argument("--body", required=True, help="JSON string body")
    parser.add_argument("--limit", default="50", help="Pagination limit")
    parser.add_argument("--offset", default="0", help="Pagination offset")
    args = parser.parse_args()
    if args.count:
        path = f"/{args.cohort_id}/fhir/{args.fhir_resource}/count"
    else:
        path = f"/{args.cohort_id}/fhir/{args.fhir_resource}"

    event = {
        "path": path,
        "pathParameters": {
            "fhir_resource": args.fhir_resource,
            "cohort_id": args.cohort_id,
        },
        "queryStringParameters": {"offset": args.offset, "limit": args.limit},
        "body": args.body,
    }
    lambda_handler(event, {})
