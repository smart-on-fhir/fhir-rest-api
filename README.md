# FHIR REST API

A small Flask-based FHIR extractor API for reading parquet FHIR resources and returning paginated results.
Allows for filtration by patient, and field reduction.

## Setup

### Local

Pull dependencies:

```bash
uv sync
```

Set up parquet files. They should have a structure like this:

```txt
storage_root/
├─ my_cohort/
│  ├─ patient/
│  │  ├─ 1.parquet
│  ├─ observation/
│  │  ├─ 1.parquet
│  │  ├─ 2.parquet
│  │  ├─ 3.parquet
|  ├─ encounter/
│  │  ├─ 1.parquet
│  │  ├─ 2.parquet
├─ my_cohort_1/
│  ├─ patient/
│  │  ├─ 1.parquet
...
```

Run query:

```bash
LOCAL_ROOT=/path/to/my/fhir_root/ python3 lambda/lambda.py --fhir_resource patient \
  --body '{"patients": ["Patient/1"]}' \
  --offset 0 --limit 50
```

### AWS

We provide a SAM template to deploy this code, and an example samconfig.
Before deploying, you should have a trove of parquet files in S3 with the same structure as described
above. After editing the example samconfig and uploading your parquets, you can deploy this to AWS:

```bash
sam build
sam deploy --config-file example_samconfig.toml --config-env dev --guided
```

You can load data for the API to consume either by uploading ndjsons or parquet files to your FhirDataBucket,
or by constructing a cohort from existing Cumulus data housed in Athena. To do this:

1. Construct a cohort of patient refs, and save it as a table, ex: `my_cumulus_cohort`. The refs should be
in the format `<anon_id>`, NOT `Patient/<anon_id>`
2. Call the `/build` endpoint. It's probably easiest to just do this via the test functionality
in API gateway, but you could also curl it. Remember that it's a POST. Use your table name as your cohort_id.

## Architecture

This repo consists of a SAM template that deploys a lambda backed REST API, and a small support ecosystem to provide data to that API. The key objects in this ecosystem are as follows:

- FHIRSourceBucket: a S3 bucket, lives outside this template. Where your athena data lives.
- FhirDataBucket: a S3 bucket. Where the template houses the FHIR data it queries and returns.
- FhirApi: Lambda function. The API itself. Reads from FhirDataBucket.
- Converter: Lambda function. Converts ndjsons that land in FhirDataBucket to parquets
- DataFetcher: Lambda function. Fetches data from FHIRSourceBucket via Athena queries and writes the resulting parquets to FhirDataBucket

![Architecture diagram](diagram.png)

## Usage

### 1. Request a resource type

#### 1.1. FHIR

```bash
curl -X POST http://127.0.0.1:5000/fhir/patient/ \
  -H "Content-Type: application/json" \
  -d '{}'
```

#### 1.2. Count

```bash
curl -X POST http://127.0.0.1:5000/fhir/patient/count \
  -H "Content-Type: application/json" \
  -d '{}'
```

### 2. Request a resource type for specific patients

#### 2.1. FHIR

```bash
curl -X POST http://127.0.0.1:5000/fhir/observation/ \
  -H "Content-Type: application/json" \
  -d '{"patients": ["Patient/1", "Patient/2"]}'
```

#### 2.2. Count

```bash
curl -X POST http://127.0.0.1:5000/fhir/observation/count \
  -H "Content-Type: application/json" \
  -d '{"patients": ["Patient/1", "Patient/2"]}'
```

### 3. Request a resource type and filter returned fields

```bash
curl -X POST http://127.0.0.1:5000/fhir/encounter/ \
  -H "Content-Type: application/json" \
  -d '{"patients": ["Patient/1"], "fields": ["id", "status", "code"]}'
```

### 4. Request a paginated page of results

```bash
curl -X POST 'http://127.0.0.1:5000/fhir/condition/?offset=10&limit=5' \
  -H "Content-Type: application/json" \
  -d '{"patients": ["Patient/1"]}'
```

