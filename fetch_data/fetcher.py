import os
from jinja2 import Environment, FileSystemLoader
import boto3
import time
import logging
from botocore.exceptions import ClientError

logger = logging.getLogger()
logger.setLevel("INFO")
athena = boto3.client("athena")


TEMPLATE_DIR = os.path.join(os.path.dirname(__file__), "templates")

jinja_env = Environment(
    loader=FileSystemLoader(TEMPLATE_DIR),
    trim_blocks=True,
    lstrip_blocks=True,
)


def render_query_from_template(fhir_resource: str, cohort_id: str, s3_path: str) -> str:
    template = jinja_env.get_template("dump_fhir.jinja2")
    return template.render(
        fhir_resource=fhir_resource, cohort_id=cohort_id, s3_path=s3_path
    )


async def dump_fhir_resource(
    table: str, cohort_id: str, database: str, output_bucket: str
) -> str:
    logger.info(f"Running Athena query for table {table} and cohort {cohort_id}")
    output_location = f"s3://{output_bucket}/{cohort_id}/"
    query_string = render_query_from_template(table, cohort_id, output_location)
    print(query_string)

    query_execution = athena.start_query_execution(
        QueryString=query_string,
        QueryExecutionContext={"Database": database},
        ResultConfiguration={"OutputLocation": output_location},
    )

    execution_id = query_execution["QueryExecutionId"]
    logger.info(f"Started Athena query {execution_id} for table {table}")

    state = "RUNNING"
    while state in {"QUEUED", "RUNNING"}:
        response = athena.get_query_execution(QueryExecutionId=execution_id)
        state = response["QueryExecution"]["Status"]["State"]
        if state in {"QUEUED", "RUNNING"}:
            time.sleep(1)
        if state == "FAILED":
            logger.info(f"Query failed {response}")
    logger.info(
        f"Athena query {execution_id} for table {table} finished with state {state}"
    )
    return state


def athena_table_exists(table: str, database: str) -> bool:
    client = boto3.client("athena")
    try:
        response = client.get_table_metadata(
            CatalogName="AwsDataCatalog",
            DatabaseName=database,
            TableName=table,
        )
        return response.get("TableMetadata") is not None
    except ClientError as exc:
        error_code = exc.response.get("Error", {}).get("Code")
        logger.error(
            f"Error checking if table {table} exists in database {database}: {error_code}"
        )
        return False
