/**
 * Minimal client for a SMART synthetic cohort API (smart-on-fhir/fhir-rest-api
 * protocol). Plain ES module, uses only global fetch, so it runs in Node 18+
 * and in the browser: copy it into an app's src/ unchanged.
 *
 * Protocol in one paragraph: every data call is a POST with a JSON body
 * `{ patients?: string[], fields?: string[] }` to `<base>/<resourcetype>` with
 * `?offset=&limit=` in the query string; it returns
 * `{ fhir: Resource[], pagination: { total, offset, limit, ... }, otherResources: string[] }`.
 * `GET <base>/resources` lists resource types. There is no FHIR search, no
 * Bundle, and no per-patient "everything" call.
 */

export const PROD_BASE_URL = 'https://www.smartcumulus.org/synthetic/fhir/sim-ibd-patients';

/** Types the server never filters by patient: fetch once, share across records. */
export const SHARED_TYPES = ['Practitioner', 'PractitionerRole', 'Organization', 'Location', 'Medication'];

const sleep = ms => new Promise(r => setTimeout(r, ms));

/**
 * Every POST sends `Content-Type: application/json`: without it the server
 * silently ignores the body (the patients/fields filters).
 *
 * @param {object} [opts]
 * @param {string} [opts.baseUrl]  cohort base URL (default PROD_BASE_URL); a trailing slash is stripped
 * @param {number} [opts.retries]  extra attempts on network errors, 429 and 5xx
 * @param {(msg: string) => void} [opts.log]  called on each retry
 * @param {typeof fetch} [opts.fetch]  fetch implementation (tests pass a fake)
 * @param {(ms: number) => Promise<void>} [opts.sleep]  backoff delay (tests pass a no-op)
 */
export function createClient({
  baseUrl = PROD_BASE_URL,
  retries = 4,
  log = () => {},
  fetch: fetchImpl = (...a) => fetch(...a),
  sleep: sleepImpl = sleep,
} = {}) {
  const base = baseUrl.replace(/\/+$/, '');

  /** One call with retry and exponential backoff. Returns parsed JSON. Errors carry `status` when the server answered. */
  async function request(path, init = {}) {
    const method = init.method ?? 'GET';
    for (let attempt = 0; ; attempt++) {
      let res, err;
      try {
        res = await fetchImpl(base + path, init);
      } catch (e) {
        err = e; // network error (browsers also report gateway errors without CORS headers this way)
      }
      if (res) {
        if (res.ok) return res.json();
        const text = (await res.text()).slice(0, 200);
        err = Object.assign(new Error(`${res.status} from ${method} ${path}${res.status < 500 ? `: ${text}` : ''}`), { status: res.status });
        if (res.status !== 429 && res.status < 500) throw err; // not retryable
      }
      if (attempt >= retries) throw err;
      const wait = 500 * 2 ** attempt + Math.random() * 250;
      log(`retry ${attempt + 1}/${retries} in ${Math.round(wait)}ms: ${err.message ?? err}`);
      await sleepImpl(wait);
    }
  }

  const postInit = body => ({
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(body),
  });

  /** One page of a resource type. Type is case-insensitive; no trailing slash (prod 307s to a broken http://...:8080 URL). */
  function listPage(resourceType, { patients, fields, offset = 0, limit = 1000 } = {}) {
    const body = {};
    if (patients?.length) body.patients = patients;
    if (fields?.length) body.fields = fields;
    const qs = new URLSearchParams({ offset: String(offset), limit: String(limit) });
    return request(`/${resourceType.toLowerCase()}?${qs}`, postInit(body));
  }

  /** POST /{type}/count, which returns a bare number. Honors the same `patients` filter. */
  function count(resourceType, patients) {
    return request(`/${resourceType.toLowerCase()}/count`, postInit(patients?.length ? { patients } : {}));
  }

  /**
   * Every record of a type, paging by offset. Don't follow `pagination.next`:
   * it omits the cohort and always points one page further, even past the end.
   * limit=1000 is safe; a page over ~6 MB of JSON (e.g. every Observation at
   * once) fails with a 500.
   */
  async function listAll(resourceType, { patients, fields, limit = 1000 } = {}) {
    const out = [];
    for (let offset = 0; ; offset += limit) {
      const page = await listPage(resourceType, { patients, fields, offset, limit });
      out.push(...page.fhir);
      const total = page.pagination?.total ?? out.length;
      // patients + fields together ignores limit and returns everything at once.
      if (page.fhir.length < limit || out.length >= total) break;
    }
    return out;
  }

  /** The cohort's Patient resources. The `patients` filter does not work on Patient, so filter client-side. */
  const listPatients = (fields) => listAll('Patient', { fields });

  /**
   * Resource types in the cohort: GET /resources UNION the `otherResources`
   * reported by a list call. On prod, /resources lags behind the data (it
   * omits MedicationRequest, MedicationAdministration, PractitionerRole), and
   * so does `otherResources` on an unfiltered Patient call, so a Condition call
   * is used as the second source.
   */
  async function resourceTypes() {
    const [listed, probe] = await Promise.all([
      request('/resources').catch(() => []),
      listPage('Condition', { limit: 1, fields: ['id'] }).catch(() => ({ otherResources: [] })),
    ]);
    return [...new Set(['Patient', 'Condition', ...listed, ...(probe.otherResources ?? [])])].sort();
  }

  /**
   * One patient's full record as a flat resource array with exactly one
   * Patient first, then the patient's resources.
   * Shared types (Practitioner, Organization, Medication, ...) come back
   * cohort-wide because the server can't filter them; they're included so
   * references resolve. Medication references are resolved onto each order.
   *
   * @param {string} patientId
   * @param {{ concurrency?: number, includeShared?: boolean, types?: string[], onType?: (type: string, count: number) => void }} [opts]
   */
  async function fetchPatientRecord(patientId, { concurrency = 4, includeShared = true, types, onType = () => {} } = {}) {
    const allTypes = types ?? await resourceTypes();
    const patients = await listPatients();
    const patient = patients.find(p => p.id === patientId);
    if (!patient) throw new Error(`Patient ${patientId} not found in cohort (${patients.length} patients)`);

    const wanted = allTypes.filter(t => t !== 'Patient' && (includeShared || !SHARED_TYPES.includes(t)));
    const results = [];
    let next = 0;
    await Promise.all(Array.from({ length: Math.min(concurrency, wanted.length) }, async () => {
      while (next < wanted.length) {
        const t = wanted[next++];
        const shared = SHARED_TYPES.includes(t);
        const rows = await listAll(t, shared ? {} : { patients: [patientId] });
        onType(t, rows.length);
        results.push(...rows);
      }
    }));
    return [patient, ...resolveMedicationReferences(results)];
  }

  return { baseUrl: base, request, listPage, listAll, count, listPatients, resourceTypes, fetchPatientRecord };
}

/**
 * MedicationRequest / MedicationAdministration in this cohort carry only
 * `medicationReference` (the RxNorm code is on the linked Medication). Copy
 * the Medication's `code` onto each as `medicationCodeableConcept`, so
 * code-based matching works, and fill the reference's display if it's
 * missing. The reference itself is kept.
 */
export function resolveMedicationReferences(resources) {
  const meds = new Map();
  for (const r of resources) if (r.resourceType === 'Medication') meds.set(`Medication/${r.id}`, r);
  return resources.map(r => {
    if (r.resourceType !== 'MedicationRequest' && r.resourceType !== 'MedicationAdministration') return r;
    if (r.medicationCodeableConcept || !r.medicationReference?.reference) return r;
    const med = meds.get(r.medicationReference.reference);
    if (!med?.code) return r;
    const display = med.code.text ?? med.code.coding?.[0]?.display;
    return {
      ...r,
      medicationCodeableConcept: med.code,
      medicationReference: { ...r.medicationReference, display: r.medicationReference.display ?? display },
    };
  });
}

/**
 * The API returns `birthDate: null` for every Patient. In the source dataset
 * files (smart-on-fhir/cumulus-sim-ibd-patients), which do have birth dates,
 * each patient's earliest Encounter starts on the birth date (all 10 patients),
 * so it's a safe stand-in. Returns a new resource array; the Patient gets
 * `birthDate` only if it had none.
 */
export function inferBirthDate(resources) {
  const first = resources
    .filter(r => r.resourceType === 'Encounter' && r.period?.start)
    .map(r => r.period.start).sort()[0];
  if (!first) return resources;
  return resources.map(r => (r.resourceType === 'Patient' && !r.birthDate
    ? { ...r, birthDate: first.slice(0, 10) }
    : r));
}

/** Wrap resources in a FHIR R4 collection Bundle (to hand to a FHIR consumer or save to disk). */
export function toBundle(resources) {
  return {
    resourceType: 'Bundle',
    type: 'collection',
    timestamp: new Date().toISOString(),
    entry: resources.map(resource => ({ fullUrl: `urn:uuid:${resource.id}`, resource })),
  };
}

/**
 * Drop JSON nulls. The server returns parquet columns as explicit nulls
 * (e.g. `"birthDate": null`, `"deceasedDateTime": null`), which strict FHIR
 * validators reject and which `?? fallback` code doesn't expect from absent fields.
 */
export function stripNulls(value) {
  if (Array.isArray(value)) return value.map(stripNulls);
  if (value && typeof value === 'object') {
    const out = {};
    for (const [k, v] of Object.entries(value)) if (v !== null) out[k] = stripNulls(v);
    return out;
  }
  return value;
}

// ---------------------------------------------------------------------------
// CLI (Node only; importing this module never runs it, and it uses no Node
// built-ins, so the module stays browser-safe). Same commands, flags and output
// as cohort_client.py:
//
//   node cohort-client.mjs patients [--base-url URL] [--counts Type,Type,...] [--json]
//
// `patients` lists id, name, gender, birthDate and per-patient counts of a few
// resource types; --counts '' skips the counts (one request per patient per type).

export const DEFAULT_COUNT_TYPES = ['Encounter', 'Condition', 'Observation', 'MedicationRequest'];
const CLI_USAGE = 'usage: cohort-client.mjs patients [--base-url URL] [--counts Type,Type,...] [--json]';

/** One row per patient: id, name (official, else first), gender, birthDate, then a count per type. */
export async function patientRows(client, countTypes = DEFAULT_COUNT_TYPES) {
  const rows = [];
  for (const p of await client.listPatients(['id', 'name', 'gender', 'birthDate'])) {
    const n = p.name?.find(x => x.use === 'official') ?? p.name?.[0];
    const row = {
      id: p.id,
      name: [...(n?.given ?? []), n?.family].filter(Boolean).join(' '),
      gender: p.gender ?? '',
      birthDate: p.birthDate ?? '',
    };
    for (const t of countTypes) row[t] = await client.count(t, [p.id]);
    rows.push(row);
  }
  return rows;
}

/** Plain-text table: a header, a dash rule, then one left-aligned line per row. */
export function formatTable(rows) {
  if (!rows.length) return '';
  const cols = Object.keys(rows[0]);
  const widths = cols.map(c => Math.max(c.length, ...rows.map(r => String(r[c]).length)));
  const line = vals => vals.map((v, i) => String(v).padEnd(widths[i])).join('  ');
  return [line(cols), line(widths.map(w => '-'.repeat(w))), ...rows.map(r => line(cols.map(c => r[c])))].join('\n');
}

function parseCliArgs(argv) {
  const opts = { baseUrl: PROD_BASE_URL, counts: DEFAULT_COUNT_TYPES.join(','), json: false, help: false };
  const positionals = [];
  for (let i = 0; i < argv.length; i++) {
    const a = argv[i];
    const eq = a.indexOf('=');
    const [flag, inline] = a.startsWith('--') && eq > 0 ? [a.slice(0, eq), a.slice(eq + 1)] : [a, undefined];
    const value = () => {
      if (inline !== undefined) return inline;
      if (i + 1 >= argv.length) throw new Error(`${flag} needs a value`);
      return argv[++i];
    };
    if (flag === '--base-url') opts.baseUrl = value();
    else if (flag === '--counts') opts.counts = value();
    else if (flag === '--json' && inline === undefined) opts.json = true;
    else if (flag === '-h' || flag === '--help') opts.help = true;
    else if (a.startsWith('-')) throw new Error(`unrecognized argument: ${a}`);
    else positionals.push(a);
  }
  return { opts, positionals };
}

/**
 * Run the CLI. Resolves to the exit code: 0 ok, 1 API or connection error
 * (one line on stderr), 2 usage error. `makeClient` lets tests pass a fake.
 */
export async function main(argv, { log = console.log, err = console.error, makeClient = createClient } = {}) {
  let parsed;
  try {
    parsed = parseCliArgs(argv);
  } catch (e) {
    err(`${e.message}\n${CLI_USAGE}`);
    return 2;
  }
  const { opts, positionals } = parsed;
  if (opts.help) { log(CLI_USAGE); return 0; }
  if (positionals.length !== 1 || positionals[0] !== 'patients') { err(CLI_USAGE); return 2; }
  const client = makeClient({ baseUrl: opts.baseUrl, log: m => err(m) });
  const countTypes = opts.counts.split(',').map(s => s.trim()).filter(Boolean);
  try {
    const rows = await patientRows(client, countTypes);
    if (opts.json) log(JSON.stringify(rows, null, 2));
    else {
      log(`${rows.length} patients at ${client.baseUrl}`);
      if (rows.length) log(formatTable(rows));
    }
  } catch (e) {
    err(`error: ${e.message ?? e}`);
    return 1;
  }
  return 0;
}

const cliPath = globalThis.process?.argv?.[1];
const baseName = p => p.split(/[\\/]/).pop();
if (cliPath && baseName(cliPath) === baseName(decodeURIComponent(new URL(import.meta.url).pathname))) {
  main(globalThis.process.argv.slice(2)).then(code => { globalThis.process.exitCode = code; });
}
