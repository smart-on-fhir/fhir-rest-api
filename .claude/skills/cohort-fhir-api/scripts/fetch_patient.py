#!/usr/bin/env python3
"""Fetch one patient's full record and save it as a FHIR R4 collection Bundle.
Python twin of fetch-patient.mjs. The Bundle holds exactly one Patient (first
entry), every patient-scoped resource, the cohort-wide shared types so
references resolve, and orders with their Medication codes resolved.

    python3 fetch_patient.py <patientId | --index N> [--out file.json] [--base-url URL]
                             [--no-shared] [--keep-nulls] [--infer-birthdate]
"""

import argparse
import json
import sys
import time

from cohort_client import (
    PROD_BASE_URL,
    create_client,
    infer_birth_date,
    strip_nulls,
    to_bundle,
)


def main() -> None:
    ap = argparse.ArgumentParser(
        description="Save one cohort patient's record as a FHIR Bundle."
    )
    ap.add_argument("patient_id", nargs="?")
    ap.add_argument("--base-url", default=PROD_BASE_URL)
    ap.add_argument("--index", type=int)
    ap.add_argument("--out")
    ap.add_argument("--no-shared", action="store_true")
    ap.add_argument("--keep-nulls", action="store_true")
    ap.add_argument("--infer-birthdate", action="store_true")
    args = ap.parse_args()
    if not args.patient_id and args.index is None:
        ap.print_usage()
        sys.exit(1)

    client = create_client(
        base_url=args.base_url, log=lambda m: print(m, file=sys.stderr)
    )

    pid = args.patient_id
    if not pid:
        ids = [p["id"] for p in client.list_patients(["id"])]
        if not 0 <= args.index < len(ids):
            sys.exit(
                f"--index {args.index} out of range (cohort has {len(ids)} patients)"
            )
        pid = ids[args.index]

    t0 = time.time()
    resources = client.fetch_patient_record(
        pid,
        include_shared=not args.no_shared,
        on_type=lambda t, n: print(f"  {t:<26} {n}", file=sys.stderr),
    )
    if not args.keep_nulls:
        resources = [strip_nulls(r) for r in resources]
    if args.infer_birthdate:
        resources = infer_birth_date(resources)

    out = args.out or f"patient-{pid}.json"
    with open(out, "w", encoding="utf-8") as f:
        json.dump(to_bundle(resources), f, indent=2, ensure_ascii=False)
    print(
        f"Wrote {len(resources)} resources for Patient/{pid} to {out} in {time.time() - t0:.1f}s",
        file=sys.stderr,
    )


if __name__ == "__main__":
    main()
