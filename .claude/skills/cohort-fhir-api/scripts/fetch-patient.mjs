#!/usr/bin/env node
/**
 * Fetch one patient's full record and save it as a FHIR R4 collection Bundle.
 * The Bundle holds exactly one Patient (first entry), every patient-scoped
 * resource, the cohort-wide shared types (Practitioner, Organization,
 * Location, Medication, PractitionerRole) so references resolve, and orders
 * with their Medication names resolved.
 *
 *   node fetch-patient.mjs <patientId | --index N> [--out file.json] [--base-url URL]
 *                          [--no-shared] [--keep-nulls] [--infer-birthdate]
 */
import { parseArgs } from 'node:util';
import { writeFile } from 'node:fs/promises';
import { createClient, PROD_BASE_URL, toBundle, stripNulls, inferBirthDate } from './cohort-client.mjs';

const { values: args, positionals } = parseArgs({
  allowPositionals: true,
  options: {
    'base-url': { type: 'string', default: PROD_BASE_URL },
    index: { type: 'string' },
    out: { type: 'string' },
    'no-shared': { type: 'boolean', default: false },
    'keep-nulls': { type: 'boolean', default: false },
    'infer-birthdate': { type: 'boolean', default: false },
    help: { type: 'boolean', short: 'h', default: false },
  },
});
if (args.help || (!positionals[0] && args.index === undefined)) {
  console.log('Usage: node fetch-patient.mjs <patientId | --index N> [--out file.json] [--base-url URL] [--no-shared] [--keep-nulls] [--infer-birthdate]');
  process.exit(args.help ? 0 : 1);
}

const client = createClient({ baseUrl: args['base-url'], log: m => console.error(m) });

let id = positionals[0];
if (!id) {
  const ids = (await client.listPatients(['id'])).map(p => p.id);
  id = ids[Number(args.index)];
  if (!id) throw new Error(`--index ${args.index} out of range (cohort has ${ids.length} patients)`);
}

const t0 = Date.now();
let resources = await client.fetchPatientRecord(id, {
  includeShared: !args['no-shared'],
  onType: (t, n) => console.error(`  ${t.padEnd(26)} ${n}`),
});
if (!args['keep-nulls']) resources = resources.map(stripNulls);
if (args['infer-birthdate']) resources = inferBirthDate(resources);

const out = args.out ?? `patient-${id}.json`;
await writeFile(out, JSON.stringify(toBundle(resources), null, 2));
console.error(`Wrote ${resources.length} resources for Patient/${id} to ${out} in ${((Date.now() - t0) / 1000).toFixed(1)}s`);
