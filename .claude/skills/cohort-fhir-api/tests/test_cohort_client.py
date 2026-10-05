"""Offline tests for scripts/cohort_client.py: a fake transport stands in for
the API, so no network is used. tests/cohort-client.test.mjs has the same cases
for the JS client. Run from the skill directory:

    python3 -m unittest discover -s tests
"""

import contextlib
import io
import json
import sys
import unittest
import urllib.error
import urllib.parse
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

from cohort_client import (
    PROD_BASE_URL,
    HttpError,
    create_client,
    infer_birth_date,
    main,
    patient_rows,
    resolve_medication_references,
    strip_nulls,
    to_bundle,
)

PID = "p1"


class FakeApi:
    """Mimics the prod API's observed behavior, including its quirks."""

    def __init__(self, data: dict, listed_types: list, failures: list | None = None):
        self.data = data  # {"Condition": [resources...], ...}
        self.listed_types = listed_types  # what GET /resources reports
        self.failures = list(failures or [])  # statuses / exceptions to return first
        self.calls: list = []

    def __call__(self, url, method, headers, body):
        self.calls.append(
            {"url": url, "method": method, "headers": headers, "body": body}
        )
        if self.failures:
            f = self.failures.pop(0)
            if isinstance(f, Exception):
                raise f
            return f, b"Internal Server Error"
        path = url[len(PROD_BASE_URL) :]
        parsed = urllib.parse.urlparse(path)
        if parsed.path.endswith("/"):
            return 307, b""
        if parsed.path == "/resources":
            return 200, json.dumps(self.listed_types).encode()
        rtype = parsed.path.strip("/").split("/")[0]
        match = [t for t in self.data if t.lower() == rtype]
        if not match:
            return 500, b"Internal Server Error"
        t = match[0]
        # Prod ignores the body unless the content type is JSON.
        req = (
            json.loads(body)
            if headers.get("Content-Type", "").startswith("application/json")
            else {}
        )
        rows = self.data[t]
        patients = req.get("patients") or []
        if patients and t not in (
            "Patient",
            "Practitioner",
            "Organization",
            "Location",
            "Medication",
            "PractitionerRole",
        ):
            refs = {f"Patient/{p}" for p in patients}
            rows = [
                r
                for r in rows
                if (r.get("subject") or r.get("patient") or {}).get("reference") in refs
            ]
        elif patients and t == "Patient":
            rows = []  # the patients filter returns nothing on Patient
        if parsed.path.endswith("/count"):
            return 200, json.dumps(len(rows)).encode()
        if req.get("fields"):
            rows = [{k: v for k, v in r.items() if k in req["fields"]} for r in rows]
        q = urllib.parse.parse_qs(parsed.query)
        offset, limit = int(q.get("offset", ["0"])[0]), int(q.get("limit", ["50"])[0])
        page = (
            rows if (patients and req.get("fields")) else rows[offset : offset + limit]
        )
        return 200, json.dumps(
            {
                "fhir": page,
                "pagination": {"total": len(rows), "offset": offset, "limit": limit},
                "otherResources": [x for x in self.data if x != t],
            }
        ).encode()


def make_data(n_obs=5):
    return {
        "Patient": [
            {"resourceType": "Patient", "id": PID, "birthDate": None},
            {"resourceType": "Patient", "id": "p2", "birthDate": None},
        ],
        "Condition": [
            {
                "resourceType": "Condition",
                "id": "c1",
                "subject": {"reference": f"Patient/{PID}"},
            },
            {
                "resourceType": "Condition",
                "id": "c2",
                "subject": {"reference": "Patient/p2"},
            },
        ],
        "Observation": [
            {
                "resourceType": "Observation",
                "id": f"o{i}",
                "subject": {"reference": f"Patient/{PID}"},
            }
            for i in range(n_obs)
        ],
        "Encounter": [
            {
                "resourceType": "Encounter",
                "id": "e2",
                "subject": {"reference": f"Patient/{PID}"},
                "period": {"start": "2012-05-01T10:00:00.000Z"},
            },
            {
                "resourceType": "Encounter",
                "id": "e1",
                "subject": {"reference": f"Patient/{PID}"},
                "period": {"start": "2011-10-20T02:03:28.000Z"},
            },
        ],
        "MedicationRequest": [
            {
                "resourceType": "MedicationRequest",
                "id": "mr1",
                "subject": {"reference": f"Patient/{PID}"},
                "medicationCodeableConcept": None,
                "medicationReference": {
                    "reference": "Medication/m1",
                    "display": "infliximab",
                },
            }
        ],
        "Medication": [
            {
                "resourceType": "Medication",
                "id": "m1",
                "code": {
                    "coding": [
                        {
                            "system": "http://www.nlm.nih.gov/research/umls/rxnorm",
                            "code": "310994",
                            "display": "infliximab 100 MG Injection",
                        }
                    ],
                    "text": "infliximab 100 MG Injection",
                },
            }
        ],
    }


def client_for(api, **kw):
    return create_client(
        base_url=PROD_BASE_URL, transport=api, sleep=lambda s: None, **kw
    )


class RequestTests(unittest.TestCase):
    def test_posts_send_json_header_and_no_trailing_slash(self):
        api = FakeApi(make_data(), ["Condition", "Patient"])
        client_for(api).list_page("Condition", patients=[PID])
        call = api.calls[0]
        self.assertEqual(call["headers"]["Content-Type"], "application/json")
        self.assertIn("/condition?", call["url"])
        self.assertNotIn("/condition/?", call["url"])
        self.assertEqual(json.loads(call["body"]), {"patients": [PID]})

    def test_retries_5xx_and_network_errors_then_succeeds(self):
        api = FakeApi(
            make_data(), [], failures=[502, urllib.error.URLError("reset"), 500]
        )
        logs = []
        page = client_for(api, log=logs.append).list_page("Condition")
        self.assertEqual(len(page["fhir"]), 2)
        self.assertEqual(len(logs), 3)

    def test_gives_up_after_retries(self):
        api = FakeApi(make_data(), [], failures=[502] * 10)
        with self.assertRaises(HttpError) as ctx:
            client_for(api, retries=2).list_page("Condition")
        self.assertEqual(ctx.exception.status, 502)
        self.assertEqual(len(api.calls), 3)

    def test_does_not_retry_4xx(self):
        api = FakeApi(make_data(), [], failures=[422])
        with self.assertRaises(HttpError):
            client_for(api).list_page("Condition")
        self.assertEqual(len(api.calls), 1)

    def test_backoff_doubles(self):
        api = FakeApi(make_data(), [], failures=[500, 500, 500])
        waits = []
        create_client(transport=api, sleep=waits.append).list_page("Condition")
        self.assertEqual(len(waits), 3)
        for i, w in enumerate(waits):
            self.assertGreaterEqual(w, 0.5 * 2**i)
            self.assertLess(w, 0.5 * 2**i + 0.25)


class PagingTests(unittest.TestCase):
    def test_list_all_pages_by_offset_until_total(self):
        api = FakeApi(make_data(n_obs=25), [])
        rows = client_for(api).list_all("Observation", limit=10)
        self.assertEqual(len(rows), 25)
        offsets = [
            urllib.parse.parse_qs(urllib.parse.urlparse(c["url"]).query)["offset"][0]
            for c in api.calls
        ]
        self.assertEqual(offsets, ["0", "10", "20"])

    def test_patients_plus_fields_returns_everything_in_one_call(self):
        api = FakeApi(make_data(n_obs=25), [])
        rows = client_for(api).list_all(
            "Observation", patients=[PID], fields=["id"], limit=10
        )
        self.assertEqual(len(rows), 25)
        self.assertEqual(len(api.calls), 1)

    def test_count_returns_bare_number(self):
        api = FakeApi(make_data(), [])
        self.assertEqual(client_for(api).count("Condition", [PID]), 1)
        self.assertIn("/condition/count", api.calls[0]["url"])


class RecordTests(unittest.TestCase):
    def test_resource_types_unions_listed_and_other_resources(self):
        # /resources lags: it omits MedicationRequest, like prod does.
        api = FakeApi(make_data(), ["Condition", "Patient", "Observation"])
        types = client_for(api).resource_types()
        self.assertIn("MedicationRequest", types)
        self.assertIn("Medication", types)
        self.assertEqual(types, sorted(types))

    def test_fetch_patient_record_has_one_patient_first_and_resolved_meds(self):
        api = FakeApi(make_data(), ["Condition", "Patient"])
        seen = {}
        rec = client_for(api).fetch_patient_record(
            PID, on_type=lambda t, n: seen.__setitem__(t, n)
        )
        self.assertEqual(rec[0]["id"], PID)
        self.assertEqual(sum(r["resourceType"] == "Patient" for r in rec), 1)
        self.assertEqual(seen["Condition"], 1)  # filtered to this patient
        self.assertEqual(seen["Medication"], 1)  # shared type, unfiltered
        mr = next(r for r in rec if r["resourceType"] == "MedicationRequest")
        self.assertEqual(mr["medicationCodeableConcept"]["coding"][0]["code"], "310994")
        self.assertEqual(
            mr["medicationReference"]["display"], "infliximab"
        )  # existing display kept
        # Shared types are fetched without a patients filter.
        med_call = next(c for c in api.calls if "/medication?" in c["url"])
        self.assertEqual(json.loads(med_call["body"]), {})

    def test_fetch_patient_record_no_shared(self):
        api = FakeApi(make_data(), ["Condition", "Patient"])
        rec = client_for(api).fetch_patient_record(PID, include_shared=False)
        self.assertFalse(any(r["resourceType"] == "Medication" for r in rec))

    def test_unknown_patient_raises(self):
        api = FakeApi(make_data(), [])
        with self.assertRaises(LookupError):
            client_for(api).fetch_patient_record("nope")


class HelperTests(unittest.TestCase):
    def test_strip_nulls_recurses(self):
        self.assertEqual(
            strip_nulls({"a": None, "b": [{"c": None, "d": 1}], "e": {"f": None}}),
            {"b": [{"d": 1}], "e": {}},
        )

    def test_infer_birth_date_uses_earliest_encounter(self):
        out = infer_birth_date(
            [{"resourceType": "Patient", "id": PID}, *make_data()["Encounter"]]
        )
        self.assertEqual(out[0]["birthDate"], "2011-10-20")

    def test_infer_birth_date_keeps_existing(self):
        out = infer_birth_date(
            [
                {"resourceType": "Patient", "birthDate": "2000-01-01"},
                *make_data()["Encounter"],
            ]
        )
        self.assertEqual(out[0]["birthDate"], "2000-01-01")

    def test_resolve_fills_missing_display(self):
        data = make_data()
        mr = dict(
            data["MedicationRequest"][0],
            medicationReference={"reference": "Medication/m1"},
        )
        out = resolve_medication_references([mr, *data["Medication"]])
        self.assertEqual(
            out[0]["medicationReference"]["display"], "infliximab 100 MG Injection"
        )

    def test_resolve_leaves_existing_codeable_concept(self):
        mr = {
            "resourceType": "MedicationRequest",
            "medicationCodeableConcept": {"text": "x"},
            "medicationReference": {"reference": "Medication/m1"},
        }
        self.assertIs(
            resolve_medication_references([mr, *make_data()["Medication"]])[0], mr
        )

    def test_to_bundle(self):
        b = to_bundle([{"resourceType": "Patient", "id": PID}])
        self.assertEqual((b["resourceType"], b["type"]), ("Bundle", "collection"))
        self.assertEqual(b["entry"][0]["fullUrl"], f"urn:uuid:{PID}")
        self.assertTrue(b["timestamp"].endswith("Z"))


def named_data() -> dict:
    """Two patients with names; p1 has an official name after a nickname."""
    data = make_data()
    data["Patient"] = [
        {
            "resourceType": "Patient",
            "id": PID,
            "gender": "female",
            "birthDate": None,
            "name": [
                {"use": "nickname", "given": ["Jan"]},
                {
                    "use": "official",
                    "given": ["Janis361", "Cyndi533"],
                    "family": "Macejkovic424",
                },
            ],
        },
        {
            "resourceType": "Patient",
            "id": "p2",
            "gender": "male",
            "birthDate": "2012-03-04",
            "name": [{"given": ["Rusty501"], "family": "Luettgen772"}],
        },
    ]
    return data


def run_cli(argv: list, api: FakeApi) -> tuple[int, str, str]:
    out, err = io.StringIO(), io.StringIO()
    with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
        try:
            code = main(
                argv,
                make_client=lambda **kw: create_client(
                    transport=api, sleep=lambda s: None, **kw
                ),
            )
        except SystemExit as e:  # argparse usage errors
            code = e.code
    return code, out.getvalue(), err.getvalue()


class PatientsCliTests(unittest.TestCase):
    def test_patient_rows_picks_official_name_and_counts(self):
        rows = patient_rows(
            client_for(FakeApi(named_data(), [])), ["Condition", "Observation"]
        )
        self.assertEqual(
            rows,
            [
                {
                    "id": PID,
                    "name": "Janis361 Cyndi533 Macejkovic424",
                    "gender": "female",
                    "birthDate": "",
                    "Condition": 1,
                    "Observation": 5,
                },
                {
                    "id": "p2",
                    "name": "Rusty501 Luettgen772",
                    "gender": "male",
                    "birthDate": "2012-03-04",
                    "Condition": 1,
                    "Observation": 0,
                },
            ],
        )

    def test_prints_a_table_by_default(self):
        code, out, _ = run_cli(
            ["patients", "--counts", "Condition"], FakeApi(named_data(), [])
        )
        self.assertEqual(code, 0)
        self.assertEqual(
            out.split("\n"),
            [
                f"2 patients at {PROD_BASE_URL}",
                "id  name                             gender  birthDate   Condition",
                "--  -------------------------------  ------  ----------  ---------",
                "p1  Janis361 Cyndi533 Macejkovic424  female              1        ",
                "p2  Rusty501 Luettgen772             male    2012-03-04  1        ",
                "",
            ],
        )

    def test_json_with_no_counts(self):
        code, out, _ = run_cli(
            ["patients", "--counts=", "--json"], FakeApi(named_data(), [])
        )
        self.assertEqual(code, 0)
        keys = [list(r) for r in json.loads(out)]
        self.assertEqual(keys, [["id", "name", "gender", "birthDate"]] * 2)

    def test_usage_errors_exit_2(self):
        api = FakeApi(named_data(), [])
        for argv in ([], ["bogus"], ["patients", "--nope"], ["patients", "--counts"]):
            self.assertEqual(run_cli(argv, api)[0], 2, argv)
        self.assertEqual(api.calls, [])

    def test_api_error_exits_1_with_one_line(self):
        code, _, err = run_cli(["patients"], FakeApi(named_data(), [], [500] * 5))
        self.assertEqual(code, 1)
        self.assertRegex(err.splitlines()[-1], r"^error: .*500")


if __name__ == "__main__":
    unittest.main()
