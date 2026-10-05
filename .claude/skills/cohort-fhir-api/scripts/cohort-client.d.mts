// Types for cohort-client.mjs, so TypeScript apps can import it without allowJs.
// Copy both files into the app's src/ together.

export declare const PROD_BASE_URL: string;
export declare const SHARED_TYPES: string[];

/** Loose FHIR resource; cast to fhir/r4 types at the call site. */
export interface CohortResource {
  resourceType: string;
  id: string;
  [key: string]: any;
}

export interface Pagination {
  total: number;
  offset: number;
  limit: number;
  count: number;
  first: string;
  last: string;
  next: string;
  previous: string;
}

export interface ListResponse<T = CohortResource> {
  fhir: T[];
  pagination: Pagination;
  otherResources: string[];
}

export interface ListOptions {
  patients?: string[];
  fields?: string[];
  offset?: number;
  limit?: number;
}

export interface CohortClient {
  baseUrl: string;
  request<T = unknown>(path: string, init?: RequestInit): Promise<T>;
  listPage<T = CohortResource>(resourceType: string, opts?: ListOptions): Promise<ListResponse<T>>;
  listAll<T = CohortResource>(resourceType: string, opts?: Omit<ListOptions, 'offset'>): Promise<T[]>;
  count(resourceType: string, patients?: string[]): Promise<number>;
  listPatients<T = CohortResource>(fields?: string[]): Promise<T[]>;
  resourceTypes(): Promise<string[]>;
  fetchPatientRecord(
    patientId: string,
    opts?: {
      concurrency?: number;
      includeShared?: boolean;
      types?: string[];
      onType?: (type: string, count: number) => void;
    },
  ): Promise<CohortResource[]>;
}

export declare function createClient(opts?: {
  /** Cohort base URL; defaults to PROD_BASE_URL. */
  baseUrl?: string;
  retries?: number;
  log?: (msg: string) => void;
  fetch?: typeof fetch;
  sleep?: (ms: number) => Promise<void>;
}): CohortClient;

export declare function resolveMedicationReferences<T extends CohortResource>(resources: T[]): T[];
export declare function inferBirthDate<T extends CohortResource>(resources: T[]): T[];
export declare function stripNulls<T>(value: T): T;
export declare function toBundle(resources: CohortResource[]): {
  resourceType: 'Bundle';
  type: 'collection';
  timestamp: string;
  entry: { fullUrl: string; resource: CohortResource }[];
};

/** Resource types the `patients` CLI command counts per patient by default. */
export declare const DEFAULT_COUNT_TYPES: string[];

export interface PatientRow {
  id: string;
  name: string;
  gender: string;
  birthDate: string;
  /** One count per requested resource type, keyed by type. */
  [resourceType: string]: string | number;
}

/** One row per patient (official name, else first), plus a count per type; one request per patient per type. */
export declare function patientRows(client: CohortClient, countTypes?: string[]): Promise<PatientRow[]>;
/** Plain-text table: header, dash rule, one left-aligned line per row. */
export declare function formatTable(rows: Record<string, unknown>[]): string;
/** The CLI (`patients` command). Resolves to the exit code: 0 ok, 1 API error, 2 usage error. */
export declare function main(
  argv: string[],
  opts?: {
    log?: (line: string) => void;
    err?: (line: string) => void;
    makeClient?: typeof createClient;
  },
): Promise<number>;
