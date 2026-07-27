import asyncio
import json
import os
import logging
import fetcher
import s3_utils

logger = logging.getLogger()
logger.setLevel("INFO")

RESOURCES = [
    "patient",
    "allergyintolerance",
    "device",
    "episodeofcare",
    "immunization",
    "diagnosticreport",
    "documentreference",
    "encounter",
    "medicationdispense",
    "medicationrequest",
    "observation",
]

ATHENA_DATABASE = os.getenv("ATHENA_DATABASE", "")
ATHENA_OUTPUT_BUCKET = os.getenv("ATHENA_OUTPUT_BUCKET", "")


def lambda_handler(event, context):
    if ATHENA_DATABASE == "" or ATHENA_OUTPUT_BUCKET == "":
        logger.error(
            "ATHENA_DATABASE and ATHENA_OUTPUT_BUCKET must be set in environment variables"
        )
        return {"statusCode": 500, "body": "Lambda misconfigured. See logs."}
    records = event.get("Records", [])
    logger.info(f"Processing {len(records)} records")
    body = json.loads(event.get("Records", [])[0]["body"])
    cohort_id = body.get("cohort_id")
    if not cohort_id:
        return {"statusCode": 400, "body": "Missing cohort_id"}
    if fetcher.athena_table_exists(cohort_id, ATHENA_DATABASE):
        logger.info(f"Cohort {cohort_id} exists in athena. Dumping...")
    else:
        logger.warning(f"Cohort {cohort_id} dne in database {ATHENA_DATABASE}")
        return {"statusCode": 404, "body": "cohort not found"}
    s3_utils.clear_output_dir(ATHENA_OUTPUT_BUCKET, cohort_id)
    execution_results = asyncio.run(process_all_requests(cohort_id))
    logger.info(
        f"All Athena queries completed for cohort {cohort_id}. Results {execution_results}"
    )
    return {
        "statusCode": 200,
        "body": f"All Athena queries completed for cohort {cohort_id}. Results: {execution_results}",
    }


async def process_all_requests(cohort_id: str):
    # Fire all network requests concurrently
    tasks = [
        fetcher.dump_fhir_resource(
            resource, cohort_id, ATHENA_DATABASE, ATHENA_OUTPUT_BUCKET
        )
        for resource in RESOURCES
    ]
    results = await asyncio.gather(*tasks)
    return results
