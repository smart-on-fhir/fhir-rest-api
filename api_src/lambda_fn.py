import argparse
import dataclasses
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
    request_params = extract_params(event)
    resources = s3_utils.get_fhir_resource_types(request_params.cohort_id)
    logger.info(f"Found the following resources: {resources}")

    success = s3_utils.prepare_local_data_dir(
        env.source_bucket, request_params.cohort_id
    )
    if not success:
        logger.error("Failed to prepare local data directory due to size constraints.")
        return {
            "statusCode": 500,
            "body": "Data size exceeds 9GB lambda storage constraint.",
        }
    try:
        # We'll get the exact case of the S3 path for file fetching
        index = [x.lower() for x in resources].index(request_params.resource)
        s3_resource = resources[index]
        logger.info("Fetching counts")
        count = query.get_fhir_count(
            s3_resource, request_params.cohort_id, request_params.patients
        )
        logger.info(f"Count for resource {request_params.resource} is {count}")
        return {"statusCode": 200, "body": count}
    except ValueError:
        return {
            "statusCode": "404",
            "body": f"Resource {request_params.resource} not found",
        }


def _json_type_check(obj):
    if isinstance(obj, datetime.datetime):
        return obj.isoformat()
    if isinstance(obj, uuid.UUID):
        return str(obj)


def run_fhir_query(event) -> dict:
    request_params = extract_params(event)
    offset = request_params.offset
    limit = request_params.limit
    resource = request_params.resource
    resources = s3_utils.get_fhir_resource_types(request_params.cohort_id)
    logger.info(f"Found the following resources: {resources}")

    success = s3_utils.prepare_local_data_dir(
        env.source_bucket, request_params.cohort_id
    )
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
        count = query.get_fhir_count(
            s3_resource, request_params.cohort_id, request_params.patients
        )
        logger.info(f"Count for resource {resource} is {count}")
        logger.info("Fetching data")
        data = query.get_fhir_data(
            s3_resource,
            request_params.cohort_id,
            request_params.fields,
            request_params.patients,
            offset,
            limit,
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


def run_resource_query(event):
    request_params = extract_params(event)
    resources = s3_utils.get_fhir_resource_types(request_params.cohort_id)
    return {"statusCode": 200, "body": json.dumps(resources, default=_json_type_check)}


def run_patient_query(event):
    request_params = extract_params(event)
    if not request_params.patient_id:
        return {"statusCode": 404, "body": json.dumps({"reason": "Patient not found"})}
    success = s3_utils.prepare_local_data_dir(
        env.source_bucket, request_params.cohort_id
    )
    if not success:
        logger.error("Failed to prepare local data directory due to size constraints.")
        return {
            "statusCode": 500,
            "body": "Data size exceeds 9GB lambda storage constraint.",
        }
    patients = [request_params.patient_id]
    resources = s3_utils.get_fhir_resource_types(request_params.cohort_id)
    patient_dict = run_async_patient_query(
        request_params.cohort_id, patients, resources, request_params.fields
    )
    return {
        "statusCode": 200,
        "body": json.dumps({"fhir": patient_dict}, default=_json_type_check),
    }


def run_async_patient_query(
    cohort_id: str, patients: list[str], resources: list[str], fields: list[str]
) -> dict:
    resource_fn = lambda resource: (
        resource,
        query.get_fhir_data(resource, cohort_id, fields, patients, 0, 10000),
    )
    results = [resource_fn(resource) for resource in resources]
    return dict(results)


def determine_route(event):
    if not validate_query_params:
        return lambda e: {"statusCode": "400", "body": "Invalid query params."}
    request_params = extract_params(event)
    if request_params.resource:
        uncased_resource = event.get("pathParameters").get("fhir_resource")
        route = event.get("path").replace(uncased_resource, request_params.resource)
    else:
        route = event.get("path")
    base_route = f"/{request_params.cohort_id}/fhir/{request_params.resource}"
    if request_params.patient_id:
        return run_patient_query
    if route == f"/{request_params.cohort_id}/fhir/resources":
        return run_resource_query
    if route in [f"{base_route}/count", f"{base_route}/count/"]:
        return run_count_query
    if route in [f"{base_route}", f"{base_route}/"]:
        return run_fhir_query
    return lambda e: {"statusCode": "404", "body": "Route not found"}


def validate_query_params(event) -> bool:
    return event.get("queryStringParameters", {}).keys() <= ALLOWED_PARAMS


@dataclasses.dataclass
class RequestParams:
    cohort_id: str | None
    resource: str
    fields: list[str]
    patients: list[str]
    offset: int
    limit: int
    patient_id: str | None


def extract_params(event) -> RequestParams:
    resource = event.get("pathParameters").get("fhir_resource", "").lower()
    patient_id = event.get("pathParameters").get("patient_id", None)
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
    return RequestParams(
        cohort_id=cohort_id,
        resource=resource,
        fields=fields,
        patients=patients,
        offset=offset,
        limit=limit,
        patient_id=patient_id,
    )


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
