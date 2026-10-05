#!/usr/bin/env python3
"""Profile a cohort: per resource type, the cohort total, per-patient spread,
date range, and the most frequent codes (system|code display). Python twin of
cohort-profile.mjs; `--json` output has the same structure.

    python3 cohort_profile.py [--base-url URL] [--types Observation,Condition]
                              [--top 15] [--patient ID] [--json out.json]

Downloads every resource of each profiled type (prod IBD cohort: ~13.6k
resources, ~20 MB, under a minute).
"""

import argparse
import json
import sys

from cohort_client import (
    PROD_BASE_URL,
    REQUEST_ERRORS,
    SHARED_TYPES,
    create_client,
    resolve_medication_references,
)


def first(*values):
    """First value that isn't None (JavaScript's ?? chain)."""
    return next((v for v in values if v is not None), None)


def get(obj, *path):
    """Optional chaining: get(r, 'period', 'start'); integer steps index lists."""
    for step in path:
        if obj is None:
            return None
        if isinstance(step, int):
            obj = obj[step] if isinstance(obj, list) and len(obj) > step else None
        else:
            obj = obj.get(step) if isinstance(obj, dict) else None
    return obj


def concept_of(r):
    """The CodeableConcept that best names a resource of this type."""
    typ = r.get("type")
    typ_concept = get(typ, 0) if isinstance(typ, list) else None
    return first(
        r.get("code"),
        r.get("vaccineCode"),
        r.get("medicationCodeableConcept"),
        typ_concept,
        typ,
        get(r, "category", 0),
        get(r, "procedureCode", 0),
        r.get("class"),
    )


def date_of(r):
    """The clinically meaningful date of a resource, as an ISO string."""
    return first(
        r.get("effectiveDateTime"),
        get(r, "effectivePeriod", "start"),
        r.get("onsetDateTime"),
        r.get("recordedDate"),
        r.get("performedDateTime"),
        get(r, "performedPeriod", "start"),
        get(r, "period", "start"),
        r.get("authoredOn"),
        r.get("occurrenceDateTime"),
        r.get("date"),
        r.get("started"),
        r.get("issued"),
    )


def patient_ref_of(r):
    return get(first(r.get("subject"), r.get("patient")), "reference")


def tally(values) -> dict:
    out: dict = {}
    for v in values:
        key = "null" if v is None else str(v)
        out[key] = out.get(key, 0) + 1
    return out


def short_system(s):
    if not s:
        return ""
    if "loinc" in s:
        return "LOINC"
    if "snomed" in s:
        return "SNOMED"
    if "rxnorm" in s:
        return "RxNorm"
    if "cvx" in s:
        return "CVX"
    for prefix in ("https://", "http://"):
        if s.startswith(prefix):
            return s[len(prefix) :]
    return s


def code_key(r):
    c = concept_of(r)
    c = c if isinstance(c, dict) else {}
    coding = get(c, "coding", 0)
    if not coding and not c.get("text"):
        return None
    coding = coding or {}
    code = first(coding.get("code"), "")
    label = first(coding.get("display"), c.get("text"), "")
    return f"{short_system(coding.get('system'))}|{code}  {label}"


def print_table(rows: list) -> None:
    cols = list(rows[0].keys())
    widths = {c: max(len(c), *(len(str(r[c])) for r in rows)) for c in cols}
    print("  ".join(c.ljust(widths[c]) for c in cols))
    print("  ".join("-" * widths[c] for c in cols))
    for r in rows:
        print("  ".join(str(r[c]).ljust(widths[c]) for c in cols))


def main() -> None:
    ap = argparse.ArgumentParser(
        description="Profile resource types, codes and dates in a cohort."
    )
    ap.add_argument("--base-url", default=PROD_BASE_URL)
    ap.add_argument("--types")
    ap.add_argument("--top", type=int, default=15)
    ap.add_argument("--patient")
    ap.add_argument("--json")
    args = ap.parse_args()

    client = create_client(
        base_url=args.base_url, log=lambda m: print(m, file=sys.stderr)
    )
    patients = client.list_patients(["id", "gender", "birthDate"])
    all_types = client.resource_types()
    types = [s.strip() for s in args.types.split(",")] if args.types else all_types
    try:
        listed = client.request("/resources")
    except REQUEST_ERRORS:
        listed = []

    print(f"Cohort {client.base_url}")
    genders = json.dumps(
        tally(p.get("gender") for p in patients), separators=(",", ":")
    )
    print(
        f"Patients: {len(patients)}  (gender: {genders}, "
        f"birthDate missing: {sum(1 for p in patients if not p.get('birthDate'))})"
    )
    missing = [t for t in all_types if t not in listed]
    if missing:
        print(
            f"Types NOT listed by GET /resources (found via otherResources): {', '.join(missing)}"
        )
    if args.patient:
        print(f"Profiling one patient: {args.patient}")

    medications = (
        client.list_all("Medication")
        if "MedicationRequest" in types or "MedicationAdministration" in types
        else []
    )

    report = {"baseUrl": client.base_url, "patients": len(patients), "types": {}}
    summary = []
    for t in types:
        if t == "Patient":
            continue
        shared = t in SHARED_TYPES
        if args.patient and not shared:
            rows = client.list_all(t, patients=[args.patient])
        else:
            rows = client.list_all(t)
        if t.startswith("Medication") and t != "Medication":
            rows = [
                r
                for r in resolve_medication_references([*rows, *medications])
                if r.get("resourceType") == t
            ]
        per_patient = sorted(tally(x for x in map(patient_ref_of, rows) if x).values())
        dates = sorted(d for d in map(date_of, rows) if d)
        codes = tally(k for k in map(code_key, rows) if k is not None)
        top_codes = [
            [k, n] for k, n in sorted(codes.items(), key=lambda kv: -kv[1])[: args.top]
        ]
        mid = len(per_patient) >> 1
        report["types"][t] = {
            "total": len(rows),
            "shared": shared,
            "perPatient": {
                "min": per_patient[0],
                "median": per_patient[mid],
                "max": per_patient[-1],
            }
            if per_patient
            else None,
            "dateRange": [dates[0], dates[-1]] if dates else None,
            "distinctCodes": len(codes),
            "topCodes": top_codes,
        }
        summary.append(
            {
                "type": t,
                "total": len(rows),
                "per patient (min/med/max)": "shared"
                if shared
                else (
                    f"{per_patient[0]}/{per_patient[mid]}/{per_patient[-1]}"
                    if per_patient
                    else "-"
                ),
                "from": dates[0][:10] if dates else "",
                "to": dates[-1][:10] if dates else "",
                "codes": len(codes),
            }
        )

    if summary:
        print_table(summary)
    for t, info in report["types"].items():
        if not info["topCodes"]:
            continue
        print(f"\n{t}: top {len(info['topCodes'])} of {info['distinctCodes']} codes")
        for k, n in info["topCodes"]:
            print(f"  {n:>6}  {k}")
    if args.json:
        with open(args.json, "w", encoding="utf-8") as f:
            json.dump(report, f, indent=2, ensure_ascii=False)
        print(f"\nWrote {args.json}", file=sys.stderr)


if __name__ == "__main__":
    main()
