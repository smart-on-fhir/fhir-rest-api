#!/usr/bin/env node
/**
 * Profile a cohort: per resource type, the cohort total, per-patient spread,
 * date range, and the most frequent codes (system|code display). Use it to
 * learn what's actually in the data before building UI on it, e.g. which LOINC
 * a lab is stored under.
 *
 *   node cohort-profile.mjs [--base-url URL] [--types Observation,Condition]
 *                           [--top 15] [--patient ID] [--json out.json]
 *
 * Downloads every resource of each profiled type (prod IBD cohort: ~13.6k
 * resources, ~20 MB, under a minute).
 */
import { parseArgs } from 'node:util';
import { writeFile } from 'node:fs/promises';
import { createClient, PROD_BASE_URL, SHARED_TYPES, resolveMedicationReferences } from './cohort-client.mjs';

const { values: args } = parseArgs({
  options: {
    'base-url': { type: 'string', default: PROD_BASE_URL },
    types: { type: 'string' },
    top: { type: 'string', default: '15' },
    patient: { type: 'string' },
    json: { type: 'string' },
    help: { type: 'boolean', short: 'h', default: false },
  },
});
if (args.help) {
  console.log('Usage: node cohort-profile.mjs [--base-url URL] [--types A,B] [--top N] [--patient ID] [--json out.json]');
  process.exit(0);
}
const TOP = Number(args.top);
const client = createClient({ baseUrl: args['base-url'], log: m => console.error(m) });

/** The CodeableConcept that best names a resource of this type. */
function conceptOf(r) {
  return r.code ?? r.vaccineCode ?? r.medicationCodeableConcept ?? r.type?.[0] ?? r.type
    ?? r.category?.[0] ?? r.procedureCode?.[0] ?? r.class ?? null;
}

/** The clinically meaningful date of a resource, as an ISO string. */
function dateOf(r) {
  return r.effectiveDateTime ?? r.effectivePeriod?.start ?? r.onsetDateTime ?? r.recordedDate
    ?? r.performedDateTime ?? r.performedPeriod?.start ?? r.period?.start ?? r.authoredOn
    ?? r.occurrenceDateTime ?? r.date ?? r.started ?? r.issued ?? null;
}

function patientRefOf(r) {
  return (r.subject ?? r.patient)?.reference ?? null;
}

const patients = await client.listPatients(['id', 'gender', 'birthDate']);
const allTypes = await client.resourceTypes();
const types = args.types ? args.types.split(',').map(s => s.trim()) : allTypes;
const listed = await client.request('/resources').catch(() => []);

console.log(`Cohort ${client.baseUrl}`);
console.log(`Patients: ${patients.length}  (gender: ${JSON.stringify(tally(patients.map(p => p.gender)))}, ` +
  `birthDate missing: ${patients.filter(p => !p.birthDate).length})`);
const missing = allTypes.filter(t => !listed.includes(t));
if (missing.length) console.log(`Types NOT listed by GET /resources (found via otherResources): ${missing.join(', ')}`);
if (args.patient) console.log(`Profiling one patient: ${args.patient}`);

const medications = types.includes('MedicationRequest') || types.includes('MedicationAdministration')
  ? await client.listAll('Medication') : [];

const report = { baseUrl: client.baseUrl, patients: patients.length, types: {} };
const summary = [];
for (const t of types) {
  if (t === 'Patient') continue;
  const shared = SHARED_TYPES.includes(t);
  let rows = await client.listAll(t, args.patient && !shared ? { patients: [args.patient] } : {});
  if (t.startsWith('Medication') && t !== 'Medication') {
    rows = resolveMedicationReferences([...rows, ...medications]).filter(r => r.resourceType === t);
  }
  const perPatient = Object.values(tally(rows.map(patientRefOf).filter(Boolean))).sort((a, b) => a - b);
  const dates = rows.map(dateOf).filter(Boolean).sort();
  const codes = tally(rows.flatMap(r => {
    const c = conceptOf(r);
    const coding = c?.coding?.[0];
    if (!coding && !c?.text) return [];
    return [`${shortSystem(coding?.system)}|${coding?.code ?? ''}  ${coding?.display ?? c?.text ?? ''}`];
  }));
  const topCodes = Object.entries(codes).sort((a, b) => b[1] - a[1]).slice(0, TOP);
  report.types[t] = {
    total: rows.length,
    shared,
    perPatient: perPatient.length ? { min: perPatient[0], median: perPatient[perPatient.length >> 1], max: perPatient.at(-1) } : null,
    dateRange: dates.length ? [dates[0], dates.at(-1)] : null,
    distinctCodes: Object.keys(codes).length,
    topCodes,
  };
  summary.push({
    type: t,
    total: rows.length,
    'per patient (min/med/max)': shared ? 'shared' : perPatient.length
      ? `${perPatient[0]}/${perPatient[perPatient.length >> 1]}/${perPatient.at(-1)}` : '-',
    from: dates[0]?.slice(0, 10) ?? '',
    to: dates.at(-1)?.slice(0, 10) ?? '',
    codes: Object.keys(codes).length,
  });
}

console.table(summary);
for (const [t, info] of Object.entries(report.types)) {
  if (!info.topCodes.length) continue;
  console.log(`\n${t}: top ${info.topCodes.length} of ${info.distinctCodes} codes`);
  for (const [k, n] of info.topCodes) console.log(`  ${String(n).padStart(6)}  ${k}`);
}
if (args.json) {
  await writeFile(args.json, JSON.stringify(report, null, 2));
  console.error(`\nWrote ${args.json}`);
}

function tally(values) {
  const out = {};
  for (const v of values) {
    const key = v ?? 'null';
    out[key] = (out[key] ?? 0) + 1;
  }
  return out;
}

function shortSystem(s) {
  if (!s) return '';
  if (s.includes('loinc')) return 'LOINC';
  if (s.includes('snomed')) return 'SNOMED';
  if (s.includes('rxnorm')) return 'RxNorm';
  if (s.includes('cvx')) return 'CVX';
  return s.replace(/^https?:\/\//, '');
}
