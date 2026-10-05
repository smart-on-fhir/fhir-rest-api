// Offline tests for scripts/cohort-client.mjs: a fake fetch stands in for the
// API, so no network is used. Same cases as tests/test_cohort_client.py.
// Run from the skill directory:
//
//     node --test tests/cohort-client.test.mjs
import { describe, it } from 'node:test';
import assert from 'node:assert/strict';
import {
  PROD_BASE_URL,
  createClient,
  inferBirthDate,
  main,
  patientRows,
  resolveMedicationReferences,
  stripNulls,
  toBundle,
} from '../scripts/cohort-client.mjs';

const PID = 'p1';
const SHARED = ['Patient', 'Practitioner', 'Organization', 'Location', 'Medication', 'PractitionerRole'];

/** A fetch that mimics the prod API's observed behavior, including its quirks. */
function fakeApi(data, listedTypes, failures = []) {
  failures = [...failures];
  const calls = [];
  const json = (status, value) => new Response(JSON.stringify(value), { status });
  const api = async (url, init = {}) => {
    calls.push({ url, method: init.method ?? 'GET', headers: init.headers, body: init.body });
    if (failures.length) {
      const f = failures.shift();
      if (f instanceof Error) throw f;
      return new Response('Internal Server Error', { status: f });
    }
    const u = new URL(url.slice(PROD_BASE_URL.length), 'http://x');
    if (u.pathname.endsWith('/')) return new Response(null, { status: 307 });
    if (u.pathname === '/resources') return json(200, listedTypes);
    const rtype = u.pathname.split('/')[1];
    const t = Object.keys(data).find(k => k.toLowerCase() === rtype);
    if (!t) return new Response('Internal Server Error', { status: 500 });
    // Prod ignores the body unless the content type is JSON.
    const req = (init.headers?.['Content-Type'] ?? '').startsWith('application/json') ? JSON.parse(init.body) : {};
    let rows = data[t];
    const patients = req.patients ?? [];
    if (patients.length && !SHARED.includes(t)) {
      const refs = new Set(patients.map(p => `Patient/${p}`));
      rows = rows.filter(r => refs.has((r.subject ?? r.patient)?.reference));
    } else if (patients.length && t === 'Patient') {
      rows = []; // the patients filter returns nothing on Patient
    }
    if (u.pathname.endsWith('/count')) return json(200, rows.length);
    if (req.fields) rows = rows.map(r => Object.fromEntries(Object.entries(r).filter(([k]) => req.fields.includes(k))));
    const offset = Number(u.searchParams.get('offset') ?? 0);
    const limit = Number(u.searchParams.get('limit') ?? 50);
    const page = patients.length && req.fields ? rows : rows.slice(offset, offset + limit);
    return json(200, {
      fhir: page,
      pagination: { total: rows.length, offset, limit },
      otherResources: Object.keys(data).filter(x => x !== t),
    });
  };
  api.calls = calls;
  return api;
}

function makeData(nObs = 5) {
  return {
    Patient: [{ resourceType: 'Patient', id: PID, birthDate: null }, { resourceType: 'Patient', id: 'p2', birthDate: null }],
    Condition: [
      { resourceType: 'Condition', id: 'c1', subject: { reference: `Patient/${PID}` } },
      { resourceType: 'Condition', id: 'c2', subject: { reference: 'Patient/p2' } },
    ],
    Observation: Array.from({ length: nObs }, (_, i) => ({ resourceType: 'Observation', id: `o${i}`, subject: { reference: `Patient/${PID}` } })),
    Encounter: [
      { resourceType: 'Encounter', id: 'e2', subject: { reference: `Patient/${PID}` }, period: { start: '2012-05-01T10:00:00.000Z' } },
      { resourceType: 'Encounter', id: 'e1', subject: { reference: `Patient/${PID}` }, period: { start: '2011-10-20T02:03:28.000Z' } },
    ],
    MedicationRequest: [{
      resourceType: 'MedicationRequest', id: 'mr1', subject: { reference: `Patient/${PID}` },
      medicationCodeableConcept: null,
      medicationReference: { reference: 'Medication/m1', display: 'infliximab' },
    }],
    Medication: [{
      resourceType: 'Medication', id: 'm1',
      code: {
        coding: [{ system: 'http://www.nlm.nih.gov/research/umls/rxnorm', code: '310994', display: 'infliximab 100 MG Injection' }],
        text: 'infliximab 100 MG Injection',
      },
    }],
  };
}

const noSleep = async () => {};
const clientFor = (api, opts = {}) => createClient({ baseUrl: PROD_BASE_URL, fetch: api, sleep: noSleep, ...opts });
const offsetOf = call => new URL(call.url).searchParams.get('offset');

describe('request', () => {
  it('posts send a JSON Content-Type header and no trailing slash', async () => {
    const api = fakeApi(makeData(), ['Condition', 'Patient']);
    await clientFor(api).listPage('Condition', { patients: [PID] });
    const call = api.calls[0];
    assert.equal(call.headers['Content-Type'], 'application/json');
    assert.match(call.url, /\/condition\?/);
    assert.doesNotMatch(call.url, /\/condition\/\?/);
    assert.deepEqual(JSON.parse(call.body), { patients: [PID] });
  });

  it('retries 5xx and network errors, then succeeds', async () => {
    const api = fakeApi(makeData(), [], [502, new TypeError('fetch failed'), 500]);
    const logs = [];
    const page = await clientFor(api, { log: m => logs.push(m) }).listPage('Condition');
    assert.equal(page.fhir.length, 2);
    assert.equal(logs.length, 3);
  });

  it('gives up after retries', async () => {
    const api = fakeApi(makeData(), [], Array(10).fill(502));
    await assert.rejects(clientFor(api, { retries: 2 }).listPage('Condition'), e => e.status === 502);
    assert.equal(api.calls.length, 3);
  });

  it('does not retry 4xx', async () => {
    const api = fakeApi(makeData(), [], [422]);
    await assert.rejects(clientFor(api).listPage('Condition'), e => e.status === 422);
    assert.equal(api.calls.length, 1);
  });

  it('backoff doubles', async () => {
    const api = fakeApi(makeData(), [], [500, 500, 500]);
    const waits = [];
    await createClient({ fetch: api, sleep: async ms => { waits.push(ms); } }).listPage('Condition');
    assert.equal(waits.length, 3);
    waits.forEach((w, i) => {
      assert.ok(w >= 500 * 2 ** i);
      assert.ok(w < 500 * 2 ** i + 250);
    });
  });
});

describe('paging', () => {
  it('listAll pages by offset until total', async () => {
    const api = fakeApi(makeData(25), []);
    const rows = await clientFor(api).listAll('Observation', { limit: 10 });
    assert.equal(rows.length, 25);
    assert.deepEqual(api.calls.map(offsetOf), ['0', '10', '20']);
  });

  it('patients + fields returns everything in one call', async () => {
    const api = fakeApi(makeData(25), []);
    const rows = await clientFor(api).listAll('Observation', { patients: [PID], fields: ['id'], limit: 10 });
    assert.equal(rows.length, 25);
    assert.equal(api.calls.length, 1);
  });

  it('count returns a bare number', async () => {
    const api = fakeApi(makeData(), []);
    assert.equal(await clientFor(api).count('Condition', [PID]), 1);
    assert.match(api.calls[0].url, /\/condition\/count/);
  });
});

describe('record', () => {
  it('resourceTypes unions listed and otherResources', async () => {
    // /resources lags: it omits MedicationRequest, like prod does.
    const api = fakeApi(makeData(), ['Condition', 'Patient', 'Observation']);
    const types = await clientFor(api).resourceTypes();
    assert.ok(types.includes('MedicationRequest'));
    assert.ok(types.includes('Medication'));
    assert.deepEqual(types, [...types].sort());
  });

  it('fetchPatientRecord has one Patient first and resolved meds', async () => {
    const api = fakeApi(makeData(), ['Condition', 'Patient']);
    const seen = {};
    const rec = await clientFor(api).fetchPatientRecord(PID, { onType: (t, n) => { seen[t] = n; } });
    assert.equal(rec[0].id, PID);
    assert.equal(rec.filter(r => r.resourceType === 'Patient').length, 1);
    assert.equal(seen.Condition, 1); // filtered to this patient
    assert.equal(seen.Medication, 1); // shared type, unfiltered
    const mr = rec.find(r => r.resourceType === 'MedicationRequest');
    assert.equal(mr.medicationCodeableConcept.coding[0].code, '310994');
    assert.equal(mr.medicationReference.display, 'infliximab'); // existing display kept
    // Shared types are fetched without a patients filter.
    const medCall = api.calls.find(c => c.url.includes('/medication?'));
    assert.deepEqual(JSON.parse(medCall.body), {});
  });

  it('fetchPatientRecord without shared types', async () => {
    const api = fakeApi(makeData(), ['Condition', 'Patient']);
    const rec = await clientFor(api).fetchPatientRecord(PID, { includeShared: false });
    assert.ok(!rec.some(r => r.resourceType === 'Medication'));
  });

  it('unknown patient rejects', async () => {
    const api = fakeApi(makeData(), []);
    await assert.rejects(clientFor(api).fetchPatientRecord('nope'), /not found/);
  });
});

describe('helpers', () => {
  it('stripNulls recurses', () => {
    assert.deepEqual(stripNulls({ a: null, b: [{ c: null, d: 1 }], e: { f: null } }), { b: [{ d: 1 }], e: {} });
  });

  it('inferBirthDate uses the earliest Encounter', () => {
    const out = inferBirthDate([{ resourceType: 'Patient', id: PID }, ...makeData().Encounter]);
    assert.equal(out[0].birthDate, '2011-10-20');
  });

  it('inferBirthDate keeps an existing birthDate', () => {
    const out = inferBirthDate([{ resourceType: 'Patient', id: PID, birthDate: '2000-01-01' }, ...makeData().Encounter]);
    assert.equal(out[0].birthDate, '2000-01-01');
  });

  it('resolveMedicationReferences fills a missing display', () => {
    const data = makeData();
    const mr = { ...data.MedicationRequest[0], medicationReference: { reference: 'Medication/m1' } };
    const out = resolveMedicationReferences([mr, ...data.Medication]);
    assert.equal(out[0].medicationReference.display, 'infliximab 100 MG Injection');
  });

  it('resolveMedicationReferences leaves an existing CodeableConcept', () => {
    const mr = { resourceType: 'MedicationRequest', id: 'mr', medicationCodeableConcept: { text: 'x' }, medicationReference: { reference: 'Medication/m1' } };
    assert.equal(resolveMedicationReferences([mr, ...makeData().Medication])[0], mr);
  });

  it('toBundle', () => {
    const b = toBundle([{ resourceType: 'Patient', id: PID }]);
    assert.deepEqual([b.resourceType, b.type], ['Bundle', 'collection']);
    assert.equal(b.entry[0].fullUrl, `urn:uuid:${PID}`);
    assert.ok(b.timestamp.endsWith('Z'));
  });
});

describe('patients CLI', () => {
  /** Two patients with names; p1 has an official name after a nickname. */
  function namedData() {
    const data = makeData();
    data.Patient = [
      { resourceType: 'Patient', id: PID, gender: 'female', birthDate: null,
        name: [{ use: 'nickname', given: ['Jan'] }, { use: 'official', given: ['Janis361', 'Cyndi533'], family: 'Macejkovic424' }] },
      { resourceType: 'Patient', id: 'p2', gender: 'male', birthDate: '2012-03-04', name: [{ given: ['Rusty501'], family: 'Luettgen772' }] },
    ];
    return data;
  }

  async function runCli(argv, api) {
    const out = [], err = [];
    const code = await main(argv, {
      log: s => out.push(s), err: s => err.push(s),
      makeClient: opts => clientFor(api, opts),
    });
    return { code, out: out.join('\n'), err: err.join('\n') };
  }

  it('patientRows picks the official name and counts per patient', async () => {
    const rows = await patientRows(clientFor(fakeApi(namedData(), [])), ['Condition', 'Observation']);
    assert.deepEqual(rows, [
      { id: PID, name: 'Janis361 Cyndi533 Macejkovic424', gender: 'female', birthDate: '', Condition: 1, Observation: 5 },
      { id: 'p2', name: 'Rusty501 Luettgen772', gender: 'male', birthDate: '2012-03-04', Condition: 1, Observation: 0 },
    ]);
  });

  it('prints a table by default', async () => {
    const r = await runCli(['patients', '--counts', 'Condition'], fakeApi(namedData(), []));
    assert.equal(r.code, 0);
    assert.deepEqual(r.out.split('\n'), [
      `2 patients at ${PROD_BASE_URL}`,
      'id  name                             gender  birthDate   Condition',
      '--  -------------------------------  ------  ----------  ---------',
      'p1  Janis361 Cyndi533 Macejkovic424  female              1        ',
      'p2  Rusty501 Luettgen772             male    2012-03-04  1        ',
    ]);
  });

  it('--json with no counts', async () => {
    const r = await runCli(['patients', '--counts=', '--json'], fakeApi(namedData(), []));
    assert.equal(r.code, 0);
    assert.deepEqual(JSON.parse(r.out).map(x => Object.keys(x)), [['id', 'name', 'gender', 'birthDate'], ['id', 'name', 'gender', 'birthDate']]);
  });

  it('usage errors exit 2', async () => {
    const api = fakeApi(namedData(), []);
    for (const argv of [[], ['bogus'], ['patients', '--nope'], ['patients', '--counts']]) {
      assert.equal((await runCli(argv, api)).code, 2, argv.join(' '));
    }
    assert.equal(api.calls.length, 0);
  });

  it('an API error exits 1 with one line', async () => {
    const r = await runCli(['patients'], fakeApi(namedData(), [], [500, 500, 500, 500, 500]));
    assert.equal(r.code, 1);
    assert.match(r.err.split('\n').pop(), /^error: 500 from POST \/patient/);
  });
});
