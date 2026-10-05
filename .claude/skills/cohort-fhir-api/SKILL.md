---
name: cohort-fhir-api
description: How to fetch synthetic patient data from the SMART synthetic cohort FHIR API (smartcumulus.org/synthetic, the smart-on-fhir/fhir-rest-api protocol, the "sim-ibd-patients" IBD cohort). Use this whenever an app or script needs to list cohort patients, load a patient's record, query a resource type across the cohort, or build a hackathon/IHL app on the synthetic IBD patients, even if the user only says "the cohort API", "the synthetic FHIR endpoint", "smartcumulus", "the IBD patients", or pastes a /fhir/sim-ibd-patients URL. It covers the non-standard POST protocol, header and trailing-slash traps, paging, the incomplete resource-type list, missing birth dates and medication names.
---

# SMART synthetic cohort FHIR API

This is **not a FHIR REST server**. There's no `GET /Patient/123`, no search
params, no Bundles, no `$everything`. It serves parquet-backed FHIR R4 JSON
through a small custom protocol, so standard FHIR clients (fhirclient,
fhir-kit-client, SMART launch) won't work. Use the patterns below, or the
bundled client in `scripts/cohort-client.mjs`.

## Endpoint

Base URL: **`https://www.smartcumulus.org/synthetic/fhir/sim-ibd-patients`**
("prod" below). Public, no auth.

- It's the sds-cache proxy (FastAPI, Imperva CDN) in front of a fhir-rest-api
  deployment. It caches each response for 24 h, keyed on URL + body. For proxy
  internals, see the `sds-cache` skill (in smart-data-services).
- Swagger: `https://www.smartcumulus.org/synthetic/docs`.
- Use `www.`: the bare domain doesn't resolve.

The API serves whatever cohorts are uploaded to it; the path segment after `/fhir/` is
the cohort id. This endpoint serves `sim-ibd-patients`, built from
https://github.com/smart-on-fhir/cumulus-sim-ibd-patients. For what's in a cohort
(patients, codes, notes), see that dataset's `sim-ibd-patients` skill (in
cumulus-sim-ibd-patients), or profile it live with `cohort-profile` (below). The
`clinical-primitives` skill (in clinical-primitives) covers rendering.

## Where the protocol is defined in this repo

You don't need the server code to call the API, but when behavior surprises you:

- `template.yaml`: the four API Gateway routes (`POST /fhir/{cohort_id}/{fhir_resource}`,
  `.../{fhir_resource}/count`, `.../patient/{patient_id}`, `GET .../resources`) and the
  gateway's CORS settings.
- `api_src/lambda_fn.py`: routing, body and query parsing (`extract_params`, default
  `limit` 50), and the response shape, including `pagination`.
- `api_src/query.py` and `api_src/templates/*.sql.jinja2`: the DuckDB SQL behind
  `patients` and `fields`, which reference path each type is filtered on, and the
  shared types that are never filtered.
- `README.md#usage`: curl examples. They use `/{cohort}/fhir/...`; deployed paths
  are `/fhir/{cohort}/...`.
- `AGENTS.md` "Known issues": which quirks below come from this code and which from
  the deployment or the sds-cache proxy.

## Protocol

```
GET  {base}/resources                      -> ["AllergyIntolerance", ...]   (can lag, see quirk 3)
POST {base}/{type}?offset=0&limit=1000     -> { fhir: [...], pagination: {total, offset, limit, ...}, otherResources: [...] }
POST {base}/{type}/count                   -> 28   (a bare number)
```

- `{type}` is case-insensitive (`condition`, `Condition`). An unknown type is a bare 500 (the proxy turns every upstream error into one).
- Body (JSON): `{"patients": ["<id>", ...], "fields": ["id", "code", ...]}`. Both are optional; send `{}` for everything.
- `patients` takes bare ids (not `Patient/<id>`). It doesn't apply to `Patient` itself (returns 0; filter the patient list client-side) or to the shared types `Practitioner`, `PractitionerRole`, `Organization`, `Location`, `Medication` (always the whole cohort's).
- `fields` keeps only those top-level keys.
- Paging: default `limit` is 50. Use `limit=1000` and step `offset` until you have `pagination.total`. There's no cap on `limit`, but a page over ~6 MB of JSON fails with a 500: every Observation at once (~7 MB) does, 5000 of them (~5 MB) doesn't (as of 2026-10-05). Don't follow `pagination.next`: it drops the cohort from the path and always points one page further, even past the end.

```bash
B=https://www.smartcumulus.org/synthetic/fhir/sim-ibd-patients
curl -s -X POST "$B/patient?offset=0&limit=100" -H 'Content-Type: application/json' -d '{"fields":["id","name","gender"]}'
curl -s -X POST "$B/observation?offset=0&limit=1000" -H 'Content-Type: application/json' -d '{"patients":["381df067-f214-550d-8a70-cef2e8514c04"]}'
```

## Quirks and workarounds (verified 2026-10-02)

1. **The body is silently ignored without `Content-Type: application/json`.** You get the whole cohort back and no error: a missing header looks like a broken filter. `application/fhir+json` also works. `Accept` makes no difference. The CORS preflight allows `Content-Type` from any origin, so browser apps can send it.
2. **No trailing slash.** `/condition/` returns a 307 to `http://www.smartcumulus.org:8080/...`, which fails.
3. **The resource-type list can be incomplete.** On 2026-10-02, `GET /resources` listed 17 types and omitted `MedicationRequest`, `MedicationAdministration` and `PractitionerRole`. Take the union with `otherResources` from a list call on a non-Patient type such as Condition. The likely cause is the proxy's 24 h cache: an older cached `{}`-body Patient call also returned the old 16-type `otherResources`, while fresh request shapes return all 19. Any response can be up to a day old. As of 2026-10-05 `/resources` listed all 20; keep the union, since the next cached response can lag again.
4. **Intermittent 5xx.** Prod has thrown an occasional bare 500 on a valid request; the retry succeeded. A burst of 40 requests at 20-way concurrency all succeeded, but the endpoint sits behind a CDN/WAF that can start blocking aggressive scripted traffic. Retry 429/5xx/network errors with exponential backoff, and keep concurrency at 4 or below. Browsers report gateway errors as `TypeError: Failed to fetch` (no CORS headers), so retry those too.
5. **No birth dates.** `birthDate` is `null` for every patient, even on uncached requests, so ages can't be computed from it. In the source dataset files (https://github.com/smart-on-fhir/cumulus-sim-ibd-patients) every patient has a real `birthDate`, and for all 10 it equals the date of their earliest `Encounter.period.start`, so `inferBirthDate()` fills it in from that. Patients are ~12–16 years old today.
6. **Explicit nulls everywhere.** Parquet columns come back as `null` (`"medicationCodeableConcept": null`, plus every unused `value[x]` on extensions: ~900 nulls in 532 MedicationRequests). Run resources through `stripNulls()` before using them. Then `'x' in obj` checks and FHIR validators behave.
7. **Medication orders name the drug only through the reference** (in the `sim-ibd-patients` data). Every MedicationRequest and MedicationAdministration has `medicationReference: {reference: "Medication/<id>", display: "infliximab 100 MG Injection"}` and no `medicationCodeableConcept`. The display carries the drug name; the RxNorm code is only on the linked `Medication`. `resolveMedicationReferences()` copies `Medication.code` onto each order, so code-based matching and classification work.
8. **No per-patient "everything" call.** `POST /patient/{id}` is a 404 (this repo's `template.yaml` declares it, but the sds-cache proxy doesn't expose it). Build a record from one filtered call per type plus the shared types. `fetchPatientRecord()` does this. It takes about 1 s and returns ~1.8k resources per patient.
9. **Select resources by code, not by display text.** The API returns whatever the cohort's data says, and display text is not unique (e.g. percentile vitals whose names contain "weight"). For a cohort's actual codes, see its dataset skill or run `cohort-profile`.

## Using the client in an app

Copy `scripts/cohort-client.mjs` (and `cohort-client.d.mts` for TypeScript)
into the app's `src/`. It uses only `fetch`, so it runs in the browser unchanged:

```ts
import { createClient, stripNulls, inferBirthDate } from './cohort-client.mjs';

const api = createClient();   // prod; module-level so caches/settings are shared

const patients = await api.listPatients(['id', 'name', 'gender']);
const record = inferBirthDate(
  (await api.fetchPatientRecord(patients[0].id)).map(stripNulls),
);   // Patient first, then that patient's resources, the shared types, meds resolved
```

To save a record as a static file (offline demos, fixtures), use
`scripts/fetch-patient.mjs`. Each `fetch-patient` result is a FHIR R4 collection
Bundle with exactly one Patient, first. For a cohort-wide view (a patient list, or
cross-patient charts), query each type across the cohort instead of fetching whole
records. The `clinical-primitives` skill (in clinical-primitives) covers rendering.

## Helper scripts

Each helper comes in a Node version (`.mjs`, Node 18.3+, no dependencies) and a
Python version (`.py`, Python 3.11+, standard library only). Both take the same
flags, including `--base-url` (default: prod), retry with backoff, and write the
same `--json` output. `cohort_client.py` is the Python twin of `cohort-client.mjs`,
with snake_case names (`fetch_patient_record`, `strip_nulls`, `infer_birth_date`, …).
`createClient` takes `fetch` and `sleep` overrides and `create_client` takes
`transport` and `sleep`, so tests can run without the network. Run as a script,
each client has one command, `patients`. Importing the JS client never runs it,
and it uses no Node built-ins, so the file stays browser-safe.

From the repo root:

```bash
S=.claude/skills/cohort-fhir-api/scripts
# ids, names, gender, birthDate, per-patient counts (~5 s); --counts '' skips the counts
node $S/cohort-client.mjs patients [--counts Encounter,Condition] [--json]
python3 $S/cohort_client.py patients [--counts Encounter,Condition] [--json]
# one patient -> Bundle JSON (or pass a patient id instead of --index)
node $S/fetch-patient.mjs --index 0 --infer-birthdate --out patient.json
python3 $S/fetch_patient.py --index 0 --infer-birthdate --out patient.json
# type counts, per-patient spread, date ranges, top codes (whole cohort ~15 s)
node $S/cohort-profile.mjs --types Observation --top 50 --json profile.json
python3 $S/cohort_profile.py --types Observation --top 50 --json profile.json
```

Offline tests for both clients (a fake API, no network, same cases), from the
repo root:

```bash
node --test .claude/skills/cohort-fhir-api/tests/cohort-client.test.mjs
python3 -m unittest discover -s .claude/skills/cohort-fhir-api/tests
```

Run `cohort-profile` before hard-coding codes: it shows what a cohort actually
holds, even after the data is refreshed. For full observed response shapes,
per-type counts, error codes and the header test matrix, read `reference.md`.
