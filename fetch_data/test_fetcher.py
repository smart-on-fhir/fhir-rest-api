from . import fetcher


def test_render_query_from_template_patient():
    actual = fetcher.render_query_from_template(
        "patient", "my_cohort", "s3://foo_bar_baz/my_cohort/"
    )
    expected = """UNLOAD (
    SELECT target.*
    FROM patient AS target
        INNER JOIN my_cohort AS my_patients ON 
                target.id = my_patients.patient_ref
)
TO 's3://foo_bar_baz/my_cohort/patient'
WITH (
    format = 'PARQUET',
    compression = 'SNAPPY'
)"""

    assert expected == actual


def test_render_query_from_template_allergy_intolerance():
    actual = fetcher.render_query_from_template(
        "allergyintolerance", "my_cohort", "s3://foo_bar_baz/my_cohort/"
    )
    expected = """UNLOAD (
    SELECT target.*
    FROM allergyintolerance AS target
        INNER JOIN my_cohort AS my_patients ON 
                replace(target.patient.reference, 'Patient/', '') = my_patients.patient_ref
)
TO 's3://foo_bar_baz/my_cohort/allergyintolerance'
WITH (
    format = 'PARQUET',
    compression = 'SNAPPY'
)"""

    assert expected == actual


def test_render_query_from_template_allergy_observation():
    actual = fetcher.render_query_from_template(
        "observation", "my_cohort", "s3://foo_bar_baz/my_cohort/"
    )
    expected = """UNLOAD (
    SELECT target.*
    FROM observation AS target
        INNER JOIN my_cohort AS my_patients ON 
                replace(target.subject.reference, 'Patient/', '') = my_patients.patient_ref
)
TO 's3://foo_bar_baz/my_cohort/observation'
WITH (
    format = 'PARQUET',
    compression = 'SNAPPY'
)"""

    assert expected == actual
