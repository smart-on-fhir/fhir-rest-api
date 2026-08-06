import logging
import os

import duckdb
import env
from jinja2 import Environment, FileSystemLoader

logger = logging.getLogger()
logger.setLevel("INFO")

template_env = Environment(
    loader=FileSystemLoader(os.path.join(os.path.dirname(__file__), "templates")),
    trim_blocks=True,
    lstrip_blocks=True,
)

con = duckdb.connect(":memory:")


def get_fhir_data(
    resource: str,
    cohort_id: str,
    fields: list[str],
    patients: list[str],
    offset: int,
    limit: int,
):
    logger.info("Setting up query")
    parquet_pattern = os.path.join(env.local_root, cohort_id, resource, "*")
    if resource != "patient":
        patients = [f"Patient/{p}" for p in patients] if patients else []

    template = template_env.get_template("fhir_query.sql.jinja2")
    query = template.render(
        has_fields=bool(fields),
        has_patients=bool(patients),
        patient_path=get_patient_path(resource),
    )
    if fields and patients:
        logger.info("Fetching data, filtering fields and patients")
        params = [fields, parquet_pattern, patients]
    elif fields:
        logger.info("Fetching data, filtering fields")
        params = [fields, parquet_pattern, limit, offset]
    elif patients:
        logger.info("Fetching data, filtering patients")
        params = [parquet_pattern, patients, limit, offset]
    else:
        logger.info("Fetching data, no filtering")
        params = [parquet_pattern, limit, offset]
    result = con.execute(query, params).fetchall()
    column_names = [desc[0] for desc in con.description]
    ret = [to_dict(row, column_names) for row in result]
    return ret


def to_dict(row: tuple, column_names: list[str]):
    obj = {}
    for i, cell in enumerate(row):
        obj[column_names[i]] = cell
    return obj


def get_fhir_count(resource: str, cohort_id: str, patients: list[str]) -> int:
    parquet_pattern = os.path.join(env.local_root, cohort_id, resource, "*")

    template = template_env.get_template("fhir_count.sql.jinja2")
    query = template.render(
        has_patients=bool(patients),
        patient_path=get_patient_path(resource),
    )

    if patients:
        params = [parquet_pattern, patients]
    else:
        params = [parquet_pattern]
    result = con.execute(query, params).fetchall()
    return result[0][0]


def get_patient_path(resource: str) -> str:
    resource_map = {
        "allergyintolerance": "patient.reference",
        "device": "patient.reference",
        "diagnosticreport": "subject.reference",
        "documentreference": "subject.reference",
        "encounter": "subject.reference",
        "episodeofcare": "patient.reference",
        "immunization": "patient.reference",
        "medicationdispense": "patient.reference",
        "medicationrequest": "subject.reference",
        "patient": "id",
    }
    return resource_map.get(resource.lower(), "subject.reference")
