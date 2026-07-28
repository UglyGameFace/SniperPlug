import {
  HttpError,
  readRepoFile,
  writeRepoFiles,
} from './deal-desk.js';

export const WHOP_SOURCES_PATH = 'website/src/data/whop-sources.json';
export const WHOP_DEFAULT_GROUPS = Object.freeze([
  Object.freeze({ key: 'black-box', label: 'Black Box' }),
  Object.freeze({ key: 'hidden-files', label: 'Hidden Files' }),
]);

const VALID_DECISIONS = new Set(['approved', 'disapproved']);
const MAX_BULK_SOURCES = 100;

export function normalizeWhopGroupName(value) {
  return String(value || '')
    .normalize('NFKC')
    .trim()
    .replace(/\s+/g, ' ')
    .toLocaleLowerCase('en-US');
}

export function whopExperienceId(value) {
  return String(value || '').match(/\bexp_[A-Za-z0-9_-]+\b/)?.[0] || '';
}

function experienceNames(experience) {
  return [
    experience?.company?.title,
    experience?.company?.name,
    experience?.name,
  ].map((value) => String(value || '').trim()).filter(Boolean);
}

function defaultGroupForExperience(experience) {
  const names = new Set(experienceNames(experience).map(normalizeWhopGroupName));
  return WHOP_DEFAULT_GROUPS.find((group) => names.has(normalizeWhopGroupName(group.label))) || null;
}

function sourceLabel(experience) {
  return experienceNames(experience)[0] || 'Whop group';
}

function normalizeRegistry(value) {
  const sources = value?.sources && typeof value.sources === 'object' ? value.sources : {};
  return {
    version: 2,
    sources: Object.fromEntries(Object.entries(sources).flatMap(([id, source]) => {
      const experienceId = whopExperienceId(id || source?.experienceId);
      const decision = VALID_DECISIONS.has(source?.decision) ? source.decision : null;
      if (!experienceId || !decision) return [];
      return [[experienceId, {
        experienceId,
        label: String(source?.label || 'Whop group').slice(0, 120),
        decision,
        defaultKey: String(source?.defaultKey || '') || null,
        companyId: String(source?.companyId || '') || null,
        companyTitle: String(source?.companyTitle || '') || null,
        experienceName: String(source?.experienceName || '') || null,
        updatedAt: String(source?.updatedAt || '') || null,
      }]];
    })),
  };
}

export async function readWhopSourcePolicy() {
  const file = await readRepoFile(WHOP_SOURCES_PATH, { allowMissing: true });
  if (!file.content.trim()) return { sha: file.sha, registry: normalizeRegistry(null) };
  try {
    return { sha: file.sha, registry: normalizeRegistry(JSON.parse(file.content)) };
  } catch {
    throw new HttpError(502, 'The approved Whop source registry contains invalid JSON.');
  }
}

export function serializeWhopSourcePolicy(registry) {
  return `${JSON.stringify(normalizeRegistry(registry), null, 2)}\n`;
}

export function whopSourceDecision(experience, requestedExperienceId, registry) {
  const experienceId = whopExperienceId(requestedExperienceId || experience?.id);
  if (!experienceId) throw new HttpError(422, 'A valid Whop experience ID beginning with exp_ is required.');
  const saved = normalizeRegistry(registry).sources[experienceId] || null;
  const suggested = defaultGroupForExperience(experience);
  return {
    experienceId,
    label: saved?.label || sourceLabel(experience),
    decision: saved?.decision || 'pending',
    suggested: Boolean(suggested),
    defaultKey: saved?.defaultKey || suggested?.key || null,
    builtInLabel: suggested?.label || null,
    saved: Boolean(saved),
  };
}

export function assertApprovedWhopSource(registry, requestedExperienceId) {
  const experienceId = whopExperienceId(requestedExperienceId);
  const source = normalizeRegistry(registry).sources[experienceId];
  if (!source || source.decision !== 'approved') {
    throw new HttpError(403, 'Approve this Whop source in the Control Center before scanning or importing its posts.');
  }
  return source;
}

export async function saveWhopSourceDecisions(entries, decision) {
  if (!VALID_DECISIONS.has(decision)) throw new HttpError(422, 'Choose Approve or Disapprove.');
  const values = Array.isArray(entries) ? entries : [];
  if (!values.length) throw new HttpError(422, 'Select at least one Whop forum source.');
  if (values.length > MAX_BULK_SOURCES) throw new HttpError(422, `Update at most ${MAX_BULK_SOURCES} Whop sources at once.`);

  const unique = new Map();
  for (const entry of values) {
    const experience = entry?.experience || entry;
    const experienceId = whopExperienceId(entry?.experienceId || experience?.id);
    if (!experienceId || !experience) continue;
    unique.set(experienceId, { experience, experienceId });
  }
  if (!unique.size) throw new HttpError(422, 'No valid Whop forum sources were selected.');

  const current = await readWhopSourcePolicy();
  const now = new Date().toISOString();
  const states = [];
  for (const { experience, experienceId } of unique.values()) {
    const state = whopSourceDecision(experience, experienceId, current.registry);
    current.registry.sources[state.experienceId] = {
      experienceId: state.experienceId,
      label: sourceLabel(experience),
      decision,
      defaultKey: state.defaultKey,
      companyId: String(experience?.company?.id || '') || null,
      companyTitle: String(experience?.company?.title || experience?.company?.name || '') || null,
      experienceName: String(experience?.name || '') || null,
      updatedAt: now,
    };
    states.push(whopSourceDecision(experience, state.experienceId, current.registry));
  }

  const write = await writeRepoFiles([
    { path: WHOP_SOURCES_PATH, content: serializeWhopSourcePolicy(current.registry) },
  ], `${decision === 'approved' ? 'Approve' : 'Disapprove'} ${states.length} Whop source${states.length === 1 ? '' : 's'}`);
  return {
    sources: states,
    commit: write.commit?.sha || null,
  };
}

export async function saveWhopSourceDecision(experience, requestedExperienceId, decision) {
  const result = await saveWhopSourceDecisions([{ experience, experienceId: requestedExperienceId }], decision);
  return {
    source: result.sources[0],
    commit: result.commit,
  };
}

function compactExperienceId(value) {
  const id = whopExperienceId(value);
  return id ? `…${id.slice(-6)}` : '';
}

function exactSourceOption(source, group = null) {
  const experienceName = String(source.experienceName || '').trim();
  const groupLabel = group?.label || String(source.label || source.companyTitle || 'Whop group');
  const distinctExperience = experienceName
    && normalizeWhopGroupName(experienceName) !== normalizeWhopGroupName(groupLabel);
  return {
    key: source.experienceId,
    label: distinctExperience
      ? `${groupLabel} · ${experienceName}`
      : `${groupLabel} · ${compactExperienceId(source.experienceId)}`,
    experienceId: source.experienceId,
    decision: source.decision,
    builtIn: Boolean(group),
    groupKey: group?.key || null,
  };
}

export function whopSourceOptions(registry) {
  const sources = Object.values(normalizeRegistry(registry).sources)
    .sort((left, right) => String(left.label).localeCompare(String(right.label)) || String(left.experienceId).localeCompare(String(right.experienceId)));
  const output = [];

  for (const group of WHOP_DEFAULT_GROUPS) {
    const matches = sources.filter((source) => source.defaultKey === group.key);
    if (!matches.length) {
      output.push({
        key: group.key,
        label: group.label,
        experienceId: null,
        decision: 'pending',
        builtIn: true,
        groupKey: group.key,
      });
      continue;
    }
    output.push(...matches.map((source) => exactSourceOption(source, group)));
  }

  output.push(...sources
    .filter((source) => !source.defaultKey)
    .map((source) => exactSourceOption(source)));
  return output;
}
