import { createHash } from 'node:crypto';

const BLOCKED_HTML_TAG = /<\s*\/?\s*(?:script|style|iframe|object|embed|form|input|button|textarea|select|option|link|meta|base)\b/i;
const EVENT_HANDLER_ATTRIBUTE = /\son[a-z][a-z0-9_-]*\s*=/i;
const UNSAFE_URL_ATTRIBUTE = /\b(?:href|src)\s*=\s*(["']?)\s*(?:javascript:|data:text\/html)/i;
const UNSAFE_MARKDOWN_LINK = /!?\[[^\]]*\]\(\s*(?:javascript:|data:text\/html)/i;
const DISALLOWED_CONTROL = /[\u0000-\u0008\u000B\u000C\u000E-\u001F\u007F]/;
const REPLACEMENT_CHARACTER = /\uFFFD/;

export class GuideContentIntegrityError extends Error {
  constructor(message, code = 'invalid_content', details = {}) {
    super(message);
    this.name = 'GuideContentIntegrityError';
    this.code = code;
    this.details = details;
  }
}

function assertUnicodeScalars(value) {
  for (let index = 0; index < value.length; index += 1) {
    const unit = value.charCodeAt(index);
    if (unit >= 0xD800 && unit <= 0xDBFF) {
      const next = value.charCodeAt(index + 1);
      if (!(next >= 0xDC00 && next <= 0xDFFF)) {
        throw new GuideContentIntegrityError(
          'Guide content contains an incomplete Unicode character.',
          'invalid_unicode',
          { index },
        );
      }
      index += 1;
      continue;
    }
    if (unit >= 0xDC00 && unit <= 0xDFFF) {
      throw new GuideContentIntegrityError(
        'Guide content contains an incomplete Unicode character.',
        'invalid_unicode',
        { index },
      );
    }
  }
}

function trimBoundaryBlankLines(value) {
  const lines = value.split('\n');
  while (lines.length && /^[\t ]*$/.test(lines[0])) lines.shift();
  while (lines.length && /^[\t ]*$/.test(lines[lines.length - 1])) lines.pop();
  return lines.join('\n');
}

function normalizeTransportText(value) {
  return String(value ?? '')
    .replace(/^\uFEFF/, '')
    .replace(/\r\n?/g, '\n')
    .replace(/[\u2028\u2029]/g, '\n');
}

function inspectFences(value) {
  const lines = value.split('\n');
  let open = null;
  let fenceCount = 0;

  for (let index = 0; index < lines.length; index += 1) {
    const match = lines[index].match(/^ {0,3}(`{3,}|~{3,})(.*)$/);
    if (!match) continue;

    const marker = match[1];
    const character = marker[0];
    if (!open) {
      open = { character, length: marker.length, line: index + 1 };
      fenceCount += 1;
      continue;
    }

    if (character === open.character && marker.length >= open.length && !match[2].trim()) {
      open = null;
      fenceCount += 1;
    }
  }

  if (open) {
    throw new GuideContentIntegrityError(
      `Guide content has an unclosed code fence starting on line ${open.line}.`,
      'unclosed_code_fence',
      { line: open.line, marker: open.character.repeat(open.length) },
    );
  }

  return fenceCount;
}

function contentOutsideCode(value) {
  const output = [];
  let open = null;

  for (const line of value.split('\n')) {
    const fence = line.match(/^ {0,3}(`{3,}|~{3,})(.*)$/);
    if (fence) {
      const marker = fence[1];
      if (!open) open = { character: marker[0], length: marker.length };
      else if (marker[0] === open.character && marker.length >= open.length && !fence[2].trim()) open = null;
      output.push('');
      continue;
    }
    if (open || /^(?: {4}|\t)/.test(line)) {
      output.push('');
      continue;
    }
    output.push(line.replace(/(`+)(.*?)\1/g, ''));
  }

  return output.join('\n');
}

function blankLineRuns(value) {
  const runs = [];
  let active = 0;
  for (const line of value.split('\n')) {
    if (/^[\t ]*$/.test(line)) {
      active += 1;
      continue;
    }
    if (active) runs.push(active);
    active = 0;
  }
  if (active) runs.push(active);
  return runs;
}

function countOutsideFences(value, matcher) {
  let open = null;
  let count = 0;
  for (const line of value.split('\n')) {
    const fence = line.match(/^ {0,3}(`{3,}|~{3,})(.*)$/);
    if (fence) {
      const marker = fence[1];
      if (!open) open = { character: marker[0], length: marker.length };
      else if (marker[0] === open.character && marker.length >= open.length && !fence[2].trim()) open = null;
      continue;
    }
    if (!open && matcher.test(line)) count += 1;
  }
  return count;
}

export function guideStructureSignature(value) {
  const text = String(value ?? '');
  return {
    lines: text ? text.split('\n').length : 0,
    blankLineRuns: blankLineRuns(text),
    headings: countOutsideFences(text, /^ {0,3}#{1,6}(?:[\t ]+|$)/),
    listItems: countOutsideFences(text, /^ {0,3}(?:[-+*]|\d+[.)])(?:[\t ]+|$)/),
    blockquotes: countOutsideFences(text, /^ {0,3}>(?:[\t ]+|$)/),
    horizontalRules: countOutsideFences(text, /^ {0,3}(?:(?:\*\s*){3,}|(?:-\s*){3,}|(?:_\s*){3,})$/),
  };
}

export function contentFingerprint(value) {
  return createHash('sha256').update(String(value ?? ''), 'utf8').digest('hex');
}

export function prepareGuideBody(value, options = {}) {
  const source = options.source || 'guide';
  const original = String(value ?? '');
  assertUnicodeScalars(original);

  if (DISALLOWED_CONTROL.test(original)) {
    throw new GuideContentIntegrityError(
      `${source} contains a control character that cannot be published safely.`,
      'disallowed_control_character',
    );
  }
  if (options.rejectReplacementCharacter !== false && REPLACEMENT_CHARACTER.test(original)) {
    throw new GuideContentIntegrityError(
      `${source} contains a replacement character, which usually means text was decoded incorrectly.`,
      'replacement_character',
    );
  }

  const transportNormalized = normalizeTransportText(original);
  const body = trimBoundaryBlankLines(transportNormalized);
  assertUnicodeScalars(body);

  if (!body.trim()) {
    throw new GuideContentIntegrityError(`${source} is empty.`, 'empty_content');
  }

  const fenceCount = inspectFences(body);
  const safetyText = contentOutsideCode(body);
  if (
    BLOCKED_HTML_TAG.test(safetyText)
    || EVENT_HANDLER_ATTRIBUTE.test(safetyText)
    || UNSAFE_URL_ATTRIBUTE.test(safetyText)
    || UNSAFE_MARKDOWN_LINK.test(safetyText)
  ) {
    throw new GuideContentIntegrityError(
      `${source} contains unsafe embedded HTML or a dangerous link. Remove it before publishing.`,
      'unsafe_html',
    );
  }

  const canonicalOriginal = trimBoundaryBlankLines(normalizeTransportText(original));
  if (body !== canonicalOriginal) {
    throw new GuideContentIntegrityError(
      `${source} changed unexpectedly while being normalized.`,
      'normalization_mismatch',
    );
  }

  const repairs = [];
  if (/^\uFEFF/.test(original)) repairs.push('removed_utf8_bom');
  if (/\r/.test(original)) repairs.push('normalized_line_endings');
  if (/[\u2028\u2029]/.test(original)) repairs.push('normalized_unicode_line_separators');
  if (transportNormalized !== body) repairs.push('trimmed_boundary_blank_lines');

  return {
    body,
    repairs,
    fingerprint: contentFingerprint(body),
    structure: {
      ...guideStructureSignature(body),
      fenceCount,
    },
  };
}

export function assertGuideBodyRoundTrip(source, saved) {
  const expected = prepareGuideBody(source, { source: 'Source guide content' });
  const actual = prepareGuideBody(saved, { source: 'Saved guide content' });
  if (expected.body !== actual.body || expected.fingerprint !== actual.fingerprint) {
    throw new GuideContentIntegrityError(
      'Saved guide content does not exactly match the normalized source.',
      'round_trip_mismatch',
      {
        expectedFingerprint: expected.fingerprint,
        actualFingerprint: actual.fingerprint,
        expectedStructure: expected.structure,
        actualStructure: actual.structure,
      },
    );
  }
  return actual;
}
