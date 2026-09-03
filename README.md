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

```
fhir_root/
├─ patient/
│  ├─ 1.parquet
├─ observation/
│  ├─ 1.parquet
│  ├─ 2.parquet
│  ├─ 3.parquet
├─ encounter/
│  ├─ 1.parquet
│  ├─ 2.parquet
```

Run query:

```bash
LOCAL_ROOT=/path/to/my/fhir_root/ python3 lambda/lambda.py --fhir_resource patient \
  --body '{"patients": ["my-patient-id"]}' \
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
  -d '{"patients": ["my-patient-id", "my-patient-id-2"]}'
```

#### 2.2. Count

```bash
curl -X POST http://127.0.0.1:5000/fhir/observation/count \
  -H "Content-Type: application/json" \
  -d '{"patients": ["my-patient-id", "my-patient-id-2"]}'
```

### 3. Request a resource type and filter returned fields

```bash
curl -X POST http://127.0.0.1:5000/fhir/encounter/ \
  -H "Content-Type: application/json" \
  -d '{"patients": ["my-patient-id"], "fields": ["id", "status", "code"]}'
```

### 4. Request a paginated page of results

```bash
curl -X POST 'http://127.0.0.1:5000/fhir/condition/?offset=10&limit=5' \
  -H "Content-Type: application/json" \
  -d '{"patients": ["my-patient-id"]}'
```

### 5. View available FHIR resources

```bash
curl 'http://127.0.0.1:5000/fhir/resources'
```

### 6. View all the FHIR data for a specific patient
```bash
curl -X POST 'http://127.0.0.1:5000/fhir/patient/my-patient-id' \
  -H "Content-Type: application/json" \
  -d '{"fields": ["id"]}'
```
