# fhir-rest-api — Agent Guide

Written for an AI coding agent working *in this repo*. If this file and the code disagree,
the code wins; fix this file.

## What this is

A small AWS SAM application that serves FHIR R4 resources out of parquet files through a
custom, FHIR-ish REST protocol. FHIR NDJSON lands in an S3 bucket, a converter Lambda compacts
each resource type to one parquet file, and an API Lambda answers queries with DuckDB. It is
**not a FHIR server**: no search params, no Bundles, no `GET /Patient/{id}`.

```
POST /fhir/{cohort_id}/{fhir_resource}?offset=&limit=   body {patients?, fields?}  -> {fhir, pagination, otherResources}
POST /fhir/{cohort_id}/{fhir_resource}/count            body {patients?}           -> bare number
POST /fhir/{cohort_id}/patient/{patient_id}             body {fields?}             -> {fhir: {Type: [...]}}
GET  /fhir/{cohort_id}/resources                                                   -> ["Condition", ...]
```

Who uses it: the IHL hackathon apps read the synthetic `sim-ibd-patients` cohort through it.
The public endpoint (`https://www.smartcumulus.org/synthetic/fhir/sim-ibd-patients`) is the
sds-cache proxy from https://github.com/smart-on-fhir/smart-data-services in front of a
deployment of this repo. API *consumers* should read the `cohort-fhir-api` skill, not this file.

## Layout

| Path | What it is |
|---|---|
| `api_src/lambda_fn.py` | API Lambda: `lambda_handler`, routing (`determine_route`), param parsing (`extract_params`), response and `pagination` shape |
| `api_src/query.py` | DuckDB queries: patient filtering, `NON_PATIENT_RESOURCES`, `get_patient_path` (which reference each type is filtered on) |
| `api_src/templates/*.sql.jinja2` | The SQL for list (`fhir_query`) and count (`fhir_count`) |
| `api_src/s3_utils.py` | Downloads a cohort from S3 to the Lambda's `/tmp` once per container; lists resource types (S3 "directories") |
| `api_src/env.py` | `SOURCE_BUCKET`, `LOCAL_ROOT` |
| `convert_src/lambda_fn.py` | Converter Lambda: SQS batch of S3 `.ndjson` events -> `{type}_compacted.parquet`, then deletes the NDJSON |
| `api_src/test_*.py`, `convert_src/test_*.py` | pytest suites, next to the code they test |
| `test_data/` | `all_fhir_types/` (NDJSON, one patient), `my_parquet_cohort/`, `my_json_cohort/` |
| `template.yaml` | SAM stack: data bucket, SQS queue + DLQ, API Gateway (routes, CORS), both Lambdas |
| `tempate.waf.yaml` (sic) | Separate stack that attaches an existing WAF web ACL to the API stage |
| `example_samconfig.toml` | Example `sam deploy` config (copy to `samconfig.toml`, which is git-ignored) |

## Setup, test, lint

Python 3.14+ (`requires-python` in `pyproject.toml`; the Lambdas run `python3.14`). The repo uses
[uv](https://docs.astral.sh/uv/). From the repo root:

```bash
uv sync                          # creates .venv with runtime + dev deps
uv run pytest -q                 # 53 tests, ~7 s, offline (moto mocks S3)
uv run ruff check .              # lint (CI: .github/workflows/lint.yaml)
uv run ruff format --check .     # format check (CI too)
```

Run pytest from the repo root: `pyproject.toml` sets `LOCAL_ROOT=./test_data/` and puts
`api_src/` on `pythonpath`, and tests import both `api_src.lambda_fn` and the bare `env`/`query`.

There is no working local server. The `__main__` CLI in `api_src/lambda_fn.py` and the README's
"Run query" command don't produce output (see Known issues). To exercise a request end to end,
follow `api_src/test_integration.py`: copy `test_data/all_fhir_types` into `tmp_path`, convert it
with `convert_src.lambda_fn.lambda_handler`, monkeypatch `env.local_root` to a `pathlib.Path`,
and call `api_src.lambda_fn.lambda_handler(event, None)` with an API Gateway-style event.

### Deploy (needs the SAM CLI and AWS credentials; not run here)

```bash
sam build
sam deploy --config-file samconfig.toml --config-env dev --guided
```

Then load data by uploading NDJSON to `s3://fhir-rest-api-{Environment}-{AccountId}/{cohort_id}/{Type}/*.ndjson`.
The converter must see `cohort/type/file.ndjson` (it takes the type from the second path segment).

## Config

| Setting | Where | Meaning |
|---|---|---|
| `SOURCE_BUCKET` | Lambda env (`template.yaml`) | Bucket to download cohorts from. Empty = local mode (read `LOCAL_ROOT` directly) |
| `LOCAL_ROOT` | Lambda env / `pyproject.toml` for tests | Where cohorts live on disk: `{LOCAL_ROOT}/{cohort_id}/{Type}/*.parquet` |
| `Environment` | SAM parameter | Suffix for every resource name and the API stage name |
| `limit`/`offset` | Query string | Defaults 50 / 0; no maximum |

## Conventions

- Resource type in the URL is case-insensitive; the S3 directory's own case is looked up from
  the type list and used for file paths.
- Patient filtering matches `Patient/{id}` against the type's reference path from
  `get_patient_path` (default `subject.reference`); `Appointment` matches any `participant`.
- New SQL goes in `api_src/templates/` as Jinja; values are bound as `$n` parameters, not
  interpolated.
- Tests sit beside the module as `test_*.py`; ruff (lint + format) must pass for CI.
- CI's `ruff check .` also lints the skill's Python under `.claude/skills/` (pytest skips it:
  dot-directories aren't collected). Those helpers stay standard-library-only and Python 3.11+.

## Known issues

Current behavior, confirmed by reading and running the code (tests, a local handler run on
converted `test_data`, and read-only requests to the public endpoint on 2026-10-05). Not fixed.

**In this code**

- **Patient filtered by `patients` returns 0 rows when the type directory is `Patient`**, as in
  the deployed bucket (`GET /resources` lists `"Patient"`). `query.filter_and_format_patients_for_resource`
  compares `resource != "patient"` case-sensitively, so it prefixes ids with `Patient/` and matches
  them against `id`. With a lowercase `patient/` directory (the tests' layout) the filter works.
- **`POST /patient/{id}` returns an empty `Patient` array**, from the same comparison. It also
  queries every type with a fixed `limit` of 10000.
- **`pagination.next`/`first`/`last`/`previous` omit the cohort** (`/fhir/{type}/?...`) and end in
  a slash. `next` is always `offset + limit`, even past the end; `last` is
  `floor(total/limit)*limit`, which is past the end when `total` divides evenly.
- **`pagination.count` is the number of pages** (`ceil(total/limit)`), not items.
- **`patients` + `fields` together ignores `limit` and `offset`**: that branch of
  `fhir_query.sql.jinja2` has no `LIMIT`, and every match comes back at once.
- **Every absent field is an explicit `null`**: `read_parquet(union_by_name=true)` yields every
  column, and `to_dict` copies all of them.
- **Shared types ignore `patients`**: `Practitioner`, `PractitionerRole`, `Organization`,
  `Location`, `Medication` (`NON_PATIENT_RESOURCES`) always return the whole cohort's rows.
- **Unknown resource type** returns `404 "Resource {type} not found"` (with a string
  `statusCode`). The public endpoint turns it into a bare 500 (sds-cache).
- **`validate_query_params` is never called**: `determine_route` tests the function object
  (`if not validate_query_params:`), so unknown query params are accepted silently.
- **`limit=0` and non-integer `offset`/`limit` raise** (`ZeroDivisionError`, `ValueError`)
  outside the handled `ValueError` path for the type lookup, so the Lambda errors.
- **Local mode is broken**: `env.local_root` is a `str`, and `s3_utils.get_fhir_resource_types`
  does `env.local_root / cohort_id`, a `TypeError`. Tests hide this by monkeypatching a `Path`.
  The `__main__` CLI also builds `/{cohort}/fhir/...` paths that `determine_route` never matches
  and never prints the result.
- **One cohort cached per container, never refreshed**: the first request downloads the cohort
  to `/tmp`; later requests skip S3 while that directory exists. Uploading new data doesn't show
  until containers recycle (or a redeploy), and warming another cohort `rmtree`s the whole cache.
- **Converter replaces, it doesn't append**: each batch rewrites `{type}_compacted.parquet` from
  the NDJSON currently in that prefix, then deletes them.

**In the deployment / config**

- **A response over ~6 MB fails** (Lambda's synchronous payload limit): all 7,040 Observations at
  `limit=10000` is a 500 on the public endpoint; `limit=10000` on a small type, or with
  `fields: ["id"]`, is fine. There's no cap on `limit` in the code.
- **CORS preflight allows only `X-Forwarded-For`**: `template.yaml`'s `Cors.AllowHeaders`
  makes API Gateway answer `OPTIONS` itself, so a browser calling a stack deployed from this
  template directly can't send `Content-Type: application/json`. The Lambda's own `OPTIONS`
  branch (which allows `Content-Type`) is never reached. The public endpoint is unaffected:
  sds-cache answers the preflight and allows `Content-Type`.
- **The public deployment's patients have `birthDate: null`** (as of 2026-10-05), although the
  source files in https://github.com/smart-on-fhir/cumulus-sim-ibd-patients have real birth
  dates. The code passes columns through, and sds-cache forwards bodies unchanged, so this is
  the deployment's data.
- **`example_samconfig.toml` doesn't match `template.yaml`**: it overrides `FHIRSourceBucket` and
  `WafArn`, which `template.yaml` doesn't declare. `tempate.waf.yaml` imports
  `fhir-rest-api-{Environment}-FhirAPIArn`, but the export is `{StackName}-FhirAPIArn`, and the
  example stack is `fhir-sandbox-api-dev`.
- **DuckDB versions differ**: `pyproject.toml` (tests) resolves `duckdb>=1.5.4`; the Lambdas ship
  `duckdb==1.4.5` from `api_src/requirements.txt` and `convert_src/requirements.txt`.
- **README drift**: it calls the API Flask-based (it's Lambda; Flask is unused), uses
  `/{cohort}/fhir/...` paths (deployed: `/fhir/{cohort}/...`), runs `lambda/lambda.py` (doesn't
  exist), and describes a `FHIRSourceBucket`/`DataFetcher` that aren't in `template.yaml`.

**In the sds-cache proxy (public endpoint only)**: the body is ignored without
`Content-Type: application/json`; a trailing slash 307s to a broken `http://...:8080` URL;
`POST /patient/{id}` is 404; responses are cached for 24 h, so `GET /resources` can be stale.
These live in smart-data-services, not here.

## Skills

| Skill | Use it when |
|---|---|
| `.claude/skills/cohort-fhir-api/` | Calling this API as a consumer: the protocol, its traps, JS and Python client helpers (with offline tests). Cohort contents and codes live with each dataset's own skill. |

Related skills in other repos: `sds-cache` (in smart-data-services, the proxy in front of the
public endpoint), `sim-ibd-patients` (in cumulus-sim-ibd-patients, the source data), and
`clinical-primitives` (in clinical-primitives, rendering the data).
