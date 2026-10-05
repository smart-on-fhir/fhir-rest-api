# Cohort API reference: observed behavior

Everything here was observed with live requests to
`https://www.smartcumulus.org/synthetic/fhir/sim-ibd-patients` ("prod") on 2026-10-02,
unless dated otherwise. Re-run `scripts/cohort-profile.mjs` if the data has been
refreshed since.

Contents: [Sources](#sources) · [Response shapes](#response-shapes) ·
[Status codes](#status-codes) · [Header matrix](#header-matrix) ·
[Per-type counts](#per-type-counts) · [Cohort contents](#cohort-contents)

## Sources

- Protocol: this repo's `README.md#usage`. The Lambda behind the proxy is
  `api_src/lambda_fn.py` and `api_src/query.py`; routes are in
  `template.yaml`. The README examples use `/{cohort}/fhir/...`, but deployed
  paths are `/fhir/{cohort}/...`.
- Prod OpenAPI: `https://www.smartcumulus.org/synthetic/openapi.json`. Routes:
  `POST /fhir/{cohort}/{resource}`, `POST /fhir/{cohort}/{resource}/count`,
  `GET /fhir/{cohort}/resources`, plus `POST /clear_cache` (an admin route: don't call it).
- A working app: https://github.com/James-R-Jones/sample-ihl-app (`src/api.ts`,
  `src/endpoints.ts`, `src/labs.ts`).

## Response shapes

List call (`POST {base}/{type}?offset=0&limit=2`):

```json
{
  "fhir": [ { "resourceType": "Patient", "id": "9fafc0de-…", "birthDate": null, … } ],
  "pagination": {
    "count": 5, "total": 10, "offset": 0, "limit": 2,
    "first": "/fhir/patient/?offset=0&limit=2",
    "last": "/fhir/patient/?offset=10&limit=2",
    "next": "/fhir/patient/?offset=2&limit=2",
    "previous": "/fhir/patient/?offset=0&limit=2"
  },
  "otherResources": ["AllergyIntolerance", "CarePlan", …]
}
```

- `pagination.count` is the number of **pages**, not the number of items.
- The `next`/`last` links are relative, lack the cohort segment, and use a trailing slash
  (which prod redirects to a broken URL). `next` is present on the last page too. Page
  by `offset` yourself.
- `otherResources` = every type except the one requested.
- Sending `patients` and `fields` together **ignores `limit`/`offset`** and returns every
  match in one response (in the server's SQL, that branch has no LIMIT). `fields` alone
  or `patients` alone pages normally.
- `/count` returns a bare JSON number and honors `patients`.
- `GET /resources` returns a bare JSON array of type names.
- Resources are FHIR R4 JSON with `meta.profile` (US Core) and explicit `null` for every
  absent column.
- References are always relative `Type/id` and all resolve within one patient's record,
  as long as the shared types are included.

## Status codes

| Situation | Status |
|---|---|
| OK | 200 |
| Unknown resource type | 500 `Internal Server Error` (text) |
| Malformed JSON body (with JSON content type) | 422 with FastAPI `detail` |
| A page over ~6 MB of JSON (e.g. all 7,040 Observations at `limit=10000`); `limit=10000` on a small type, or with `fields: ["id"]`, is fine (2026-10-05) | 500 |
| GET on a list route | 405 |
| Trailing slash `/condition/` | 307 to `http://www.smartcumulus.org:8080/…` (broken) |
| `POST /patient/{id}` | 404 `{"detail":"Not Found"}` |
| Burst of 40 requests, 20 in parallel | 40×200 |
| Sporadic | one bare 500 seen on a valid `fields` request; the retry succeeded |

Prod sets `Set-Cookie` headers from Imperva (`visid_incap_*`) and `X-CDN: Imperva`. No bot
challenge was hit from curl or Node, but a WAF can start blocking aggressive scripted
traffic, so keep concurrency low.

## Header matrix

Request: `POST /condition?offset=0&limit=1`, body `{"patients":["9fafc0de-…"]}`.
The filter is honored when total = 30, and ignored (whole cohort) when total = 358.

| Headers sent | Total |
|---|---|
| none | 358 (ignored) |
| `Content-Type: application/json` | 30 |
| `Accept: application/json` only | 358 (ignored) |
| CT json + Accept json | 30 |
| CT json + `Accept: application/fhir+json` | 30 |
| `Content-Type: application/fhir+json` | 30 |
| `Content-Type: text/plain` | 358 (ignored) |

CORS preflight (`OPTIONS`, requesting the `content-type` header): 200, echoes any
`Origin`, `access-control-allow-headers: content-type`, so browser apps can send the
JSON header from any origin (localhost, GitHub Pages).

## Per-type counts

Cohort totals, and the counts for patient
`9fafc0de-b03f-547b-b786-f1b7f24409bb` (P0) and `381df067-f214-550d-8a70-cef2e8514c04` (P1):

| Type | Cohort | Per patient min/median/max | Listed by prod `/resources` on 2026-10-02? |
|---|---|---|---|
| Patient | 10 | — | yes |
| AllergyIntolerance | 95 | 8/11/13 (P0 has 0) | yes |
| CarePlan | 67 | 5/6/9 | yes |
| CareTeam | 67 | 5/6/9 | yes |
| Condition | 358 | 28/37/45 | yes |
| Device | 72 | 2/7/14 | yes |
| DiagnosticReport | 315 | 4/36/41 | yes |
| DocumentReference | 1323 | 91/135/172 | yes |
| Encounter | 1323 | 91/135/172 | yes |
| ImagingStudy | 59 | 2/5/12 | yes |
| Immunization | 475 | 46/47/50 | yes |
| MedicationAdministration | 245 | 9/26/34 | **no** |
| MedicationRequest | 532 | 28/46/114 | **no** |
| Observation | 7040 | 277/772/874 | yes |
| Procedure | 1257 | 55/111/213 | yes |
| Location (shared) | 46 | — | yes |
| Medication (shared) | 139 | — | yes |
| Organization (shared) | 46 | — | yes |
| Practitioner (shared) | 66 | — | yes |
| PractitionerRole (shared) | 66 | — | **no** |

One patient's record with the shared types is ~1,800 resources and ~3.4 MB of JSON.

## Cohort contents

The API passes the uploaded data through unchanged, so a cohort's contents (conditions,
medications, LOINC codes, notes, encounter types) are documented with its dataset: for
`sim-ibd-patients`, the `sim-ibd-patients` skill (in cumulus-sim-ibd-patients). The API
returned the same ids, counts and date ranges as those files when checked on 2026-10-02,
except `birthDate`, which prod returns as `null` (SKILL.md quirk 5). To see what any
cohort holds today, run `cohort-profile`.
