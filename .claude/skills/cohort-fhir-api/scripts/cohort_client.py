"""Minimal client for a SMART synthetic cohort API (smart-on-fhir/fhir-rest-api
protocol). Python twin of cohort-client.mjs, standard library only.

Protocol in one paragraph: every data call is a POST with a JSON body
``{"patients"?: [...], "fields"?: [...]}`` to ``<base>/<resourcetype>`` with
``?offset=&limit=`` in the query string; it returns
``{"fhir": [...], "pagination": {"total", "offset", "limit", ...}, "otherResources": [...]}``.
``GET <base>/resources`` lists resource types. There is no FHIR search, no
Bundle, and no per-patient "everything" call.

    from cohort_client import create_client
    api = create_client()
    patients = api.list_patients(["id", "name"])
    record = api.fetch_patient_record(patients[0]["id"])

As a script it has one command, `patients` (see the CLI section at the end):

    python3 cohort_client.py patients [--base-url URL] [--counts Type,Type,...] [--json]
"""

from __future__ import annotations

import argparse
import json
import random
import sys
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime
from typing import Any

PROD_BASE_URL = "https://www.smartcumulus.org/synthetic/fhir/sim-ibd-patients"

#: Types the server never filters by patient: fetch once, share across records.
SHARED_TYPES = [
    "Practitioner",
    "PractitionerRole",
    "Organization",
    "Location",
    "Medication",
]

#: transport(url, method, headers, body_bytes) -> (status, response_bytes).
#: Raises OSError (e.g. urllib.error.URLError) on network failure.
Transport = Callable[[str, str, dict, bytes | None], "tuple[int, bytes]"]


class HttpError(Exception):
    """A non-2xx response. ``status`` holds the HTTP status code."""

    def __init__(self, status: int, message: str):
        super().__init__(message)
        self.status = status


#: What ``request`` can raise once retries run out: an error status, a network
#: failure, or a body that isn't JSON.
REQUEST_ERRORS = (HttpError, OSError, ValueError)


def urllib_transport(
    url: str, method: str, headers: dict, body: bytes | None
) -> tuple[int, bytes]:
    """Default transport. Doesn't follow redirects, matching fetch's handling of
    prod's 307 for a trailing slash (an http://...:8080 URL that fails anyway)."""
    req = urllib.request.Request(url, data=body, method=method, headers=headers)
    try:
        with urllib.request.urlopen(req, timeout=60) as res:
            return res.status, res.read()
    except urllib.error.HTTPError as e:
        return e.code, e.read()


class CohortClient:
    def __init__(
        self,
        base_url: str = PROD_BASE_URL,
        retries: int = 4,
        log: Callable[[str], None] = lambda msg: None,
        transport: Transport = urllib_transport,
        sleep: Callable[[float], None] = time.sleep,
    ):
        """
        Every POST sends ``Content-Type: application/json``: without it the
        server silently ignores the body (the patients/fields filters).

        base_url: cohort base URL (default PROD_BASE_URL); a trailing slash is
          stripped.
        retries: extra attempts on network errors, 429 and 5xx.
        """
        self.base_url = base_url.rstrip("/")
        self.retries = retries
        self.log = log
        self.transport = transport
        self.sleep = sleep

    # -- transport ---------------------------------------------------------

    def request(self, path: str, method: str = "GET", body: dict | None = None) -> Any:
        """One call with retry and exponential backoff. Returns parsed JSON."""
        headers = {
            "User-Agent": "cohort-fhir-api-skill/1.0",
            "Accept": "application/json",
        }
        data = None
        if method == "POST":
            data = json.dumps(body if body is not None else {}).encode()
            # urllib would otherwise default to application/x-www-form-urlencoded.
            headers["Content-Type"] = "application/json"
        attempt = 0
        while True:
            try:
                status, raw = self.transport(
                    self.base_url + path, method, headers, data
                )
            except OSError as e:  # network error: retry
                err: Exception = e
            else:
                if 200 <= status < 300:
                    return json.loads(raw)
                text = raw.decode("utf-8", "replace")[:200]
                if status != 429 and status < 500:
                    raise HttpError(status, f"{status} from {method} {path}: {text}")
                err = HttpError(status, f"{status} from {method} {path}")
            if attempt >= self.retries:
                raise err
            wait = 0.5 * 2**attempt + random.random() * 0.25
            self.log(
                f"retry {attempt + 1}/{self.retries} in {round(wait * 1000)}ms: {err}"
            )
            self.sleep(wait)
            attempt += 1

    # -- list calls --------------------------------------------------------

    def list_page(
        self,
        resource_type: str,
        patients=None,
        fields=None,
        offset: int = 0,
        limit: int = 1000,
    ) -> dict:
        """One page of a resource type. Type is case-insensitive; never a trailing
        slash (prod 307s to a broken http://...:8080 URL)."""
        body: dict = {}
        if patients:
            body["patients"] = list(patients)
        if fields:
            body["fields"] = list(fields)
        qs = urllib.parse.urlencode({"offset": offset, "limit": limit})
        return self.request(f"/{resource_type.lower()}?{qs}", "POST", body)

    def list_all(
        self, resource_type: str, patients=None, fields=None, limit: int = 1000
    ) -> list:
        """Every record of a type, paging by offset. Don't follow
        pagination.next: it omits the cohort and always points one page further.
        limit=1000 is safe; a page over ~6 MB of JSON (e.g. every Observation
        at once) fails with a 500."""
        out: list = []
        offset = 0
        while True:
            page = self.list_page(
                resource_type,
                patients=patients,
                fields=fields,
                offset=offset,
                limit=limit,
            )
            rows = page.get("fhir") or []
            out.extend(rows)
            total = (page.get("pagination") or {}).get("total", len(out))
            # patients + fields together ignores limit and returns everything at once.
            if len(rows) < limit or len(out) >= total:
                return out
            offset += limit

    def list_patients(self, fields=None) -> list:
        """The cohort's Patients. The `patients` filter doesn't work on Patient."""
        return self.list_all("Patient", fields=fields)

    def count(self, resource_type: str, patients=None) -> int:
        """POST /{type}/count, which returns a bare number."""
        return self.request(
            f"/{resource_type.lower()}/count",
            "POST",
            {"patients": list(patients)} if patients else {},
        )

    def resource_types(self) -> list:
        """GET /resources UNION the otherResources of a Condition list call.
        On prod /resources lags behind the data (omits MedicationRequest,
        MedicationAdministration, PractitionerRole)."""
        try:
            listed = self.request("/resources")
        except REQUEST_ERRORS:
            listed = []
        try:
            other = (
                self.list_page("Condition", limit=1, fields=["id"]).get(
                    "otherResources"
                )
                or []
            )
        except REQUEST_ERRORS:
            other = []
        return sorted({"Patient", "Condition", *listed, *other})

    # -- one patient -------------------------------------------------------

    def fetch_patient_record(
        self,
        patient_id: str,
        concurrency: int = 4,
        include_shared: bool = True,
        types=None,
        on_type: Callable[[str, int], None] = lambda t, n: None,
    ) -> list:
        """One patient's full record as a flat list with exactly one Patient
        first, then the patient's resources. Shared types come back cohort-wide (the server can't filter them) so references
        resolve. Medication references are resolved onto each order."""
        all_types = types if types is not None else self.resource_types()
        patients = self.list_patients()
        patient = next((p for p in patients if p.get("id") == patient_id), None)
        if patient is None:
            raise LookupError(
                f"Patient {patient_id} not found in cohort ({len(patients)} patients)"
            )

        wanted = [
            t
            for t in all_types
            if t != "Patient" and (include_shared or t not in SHARED_TYPES)
        ]
        lock = threading.Lock()
        results: list = []

        def fetch(t: str) -> None:
            rows = (
                self.list_all(t)
                if t in SHARED_TYPES
                else self.list_all(t, patients=[patient_id])
            )
            with lock:
                on_type(t, len(rows))
                results.extend(rows)

        with ThreadPoolExecutor(
            max_workers=max(1, min(concurrency, len(wanted)))
        ) as pool:
            for f in [pool.submit(fetch, t) for t in wanted]:
                f.result()  # re-raise the first failure
        return [patient, *resolve_medication_references(results)]


def create_client(**kwargs) -> CohortClient:
    """Same options as CohortClient: base_url, retries, log, transport, sleep."""
    return CohortClient(**kwargs)


def resolve_medication_references(resources: list) -> list:
    """MedicationRequest/MedicationAdministration carry only medicationReference
    (the RxNorm code is on the linked Medication). Copy Medication.code onto each
    as medicationCodeableConcept and fill the reference's display."""
    meds = {
        f"Medication/{r.get('id')}": r
        for r in resources
        if r.get("resourceType") == "Medication"
    }
    out = []
    for r in resources:
        ref = (r.get("medicationReference") or {}).get("reference")
        if (
            r.get("resourceType")
            not in ("MedicationRequest", "MedicationAdministration")
            or r.get("medicationCodeableConcept")
            or not ref
        ):
            out.append(r)
            continue
        med = meds.get(ref)
        code = (med or {}).get("code")
        if not code:
            out.append(r)
            continue
        coding = (code.get("coding") or [{}])[0] or {}
        display = (
            code.get("text") if code.get("text") is not None else coding.get("display")
        )
        mref = dict(r["medicationReference"])
        if mref.get("display") is None:
            mref["display"] = display
        out.append(
            {**r, "medicationCodeableConcept": code, "medicationReference": mref}
        )
    return out


def infer_birth_date(resources: list) -> list:
    """The API returns birthDate null for every Patient. In the source dataset
    files (smart-on-fhir/cumulus-sim-ibd-patients), which do have birth dates,
    each patient's earliest Encounter starts on the birth date (all 10
    patients), so it's a safe stand-in. The Patient gets birthDate only if it
    had none."""
    starts = sorted(
        r["period"]["start"]
        for r in resources
        if r.get("resourceType") == "Encounter" and (r.get("period") or {}).get("start")
    )
    if not starts:
        return resources
    return [
        {**r, "birthDate": starts[0][:10]}
        if r.get("resourceType") == "Patient" and not r.get("birthDate")
        else r
        for r in resources
    ]


def strip_nulls(value: Any) -> Any:
    """Drop JSON nulls (the server returns absent parquet columns as null)."""
    if isinstance(value, list):
        return [strip_nulls(v) for v in value]
    if isinstance(value, dict):
        return {k: strip_nulls(v) for k, v in value.items() if v is not None}
    return value


def to_bundle(resources: list) -> dict:
    """Wrap resources in a FHIR R4 collection Bundle (to hand to a FHIR consumer or save to disk)."""
    return {
        "resourceType": "Bundle",
        "type": "collection",
        "timestamp": datetime.now(UTC)
        .isoformat(timespec="milliseconds")
        .replace("+00:00", "Z"),
        "entry": [
            {"fullUrl": f"urn:uuid:{r.get('id')}", "resource": r} for r in resources
        ],
    }


# ---------------------------------------------------------------------------
# CLI. Same commands, flags and output as cohort-client.mjs:
#
#   python3 cohort_client.py patients [--base-url URL] [--counts Type,Type,...] [--json]
#
# `patients` lists id, name, gender, birthDate and per-patient counts of a few
# resource types; --counts '' skips the counts (one request per patient per type).
# Exit codes: 0 ok, 1 API or connection error (one line on stderr), 2 usage error.

DEFAULT_COUNT_TYPES = ["Encounter", "Condition", "Observation", "MedicationRequest"]


def patient_rows(
    client: CohortClient, count_types: list[str] = DEFAULT_COUNT_TYPES
) -> list[dict]:
    """One row per patient: id, name (official, else first), gender, birthDate,
    then a count per type."""
    rows = []
    for p in client.list_patients(["id", "name", "gender", "birthDate"]):
        names = p.get("name") or []
        n = next(
            (x for x in names if x.get("use") == "official"), names[0] if names else {}
        )
        row = {
            "id": p["id"],
            "name": " ".join(
                x for x in [*(n.get("given") or []), n.get("family")] if x
            ),
            "gender": p.get("gender") or "",
            "birthDate": p.get("birthDate") or "",
        }
        for t in count_types:
            row[t] = client.count(t, [p["id"]])
        rows.append(row)
    return rows


def format_table(rows: list[dict]) -> str:
    """Plain-text table: a header, a dash rule, then one left-aligned line per row."""
    if not rows:
        return ""
    cols = list(rows[0])
    widths = [max(len(c), *(len(str(r[c])) for r in rows)) for c in cols]

    def line(vals: list) -> str:
        return "  ".join(str(v).ljust(w) for v, w in zip(vals, widths, strict=True))

    lines = [line(cols), line(["-" * w for w in widths])]
    return "\n".join(lines + [line([r[c] for c in cols]) for r in rows])


def main(argv: list[str] | None = None, make_client: Callable = create_client) -> int:
    """Run the CLI and return the exit code. ``make_client`` lets tests pass a fake."""
    ap = argparse.ArgumentParser(
        prog="cohort_client.py", description="Query the SMART synthetic cohort API."
    )
    sub = ap.add_subparsers(dest="command", required=True)
    pp = sub.add_parser(
        "patients", help="list patients with per-patient resource counts"
    )
    pp.add_argument("--base-url", default=PROD_BASE_URL)
    pp.add_argument("--counts", default=",".join(DEFAULT_COUNT_TYPES))
    pp.add_argument("--json", action="store_true")
    args = ap.parse_args(argv)

    client = make_client(
        base_url=args.base_url, log=lambda m: print(m, file=sys.stderr)
    )
    count_types = [s.strip() for s in args.counts.split(",") if s.strip()]
    try:
        rows = patient_rows(client, count_types)
    except REQUEST_ERRORS as e:
        print(f"error: {e}", file=sys.stderr)
        return 1
    if args.json:
        print(json.dumps(rows, indent=2, ensure_ascii=False))
    else:
        print(f"{len(rows)} patients at {client.base_url}")
        if rows:
            print(format_table(rows))
    return 0


if __name__ == "__main__":
    sys.exit(main())
