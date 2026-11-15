import { describe, expect, it } from 'vitest';
import { commentLength, normaliseComment, toCreatePayload, toSlaPolicyPayload, validateComment, validateIncidentForm, validateSlaPolicy, type SlaPolicyFormValues } from './validation';

const valid = { title: 'Phishing email', description: 'Reported by finance.', severity: 'medium' as const, assigneeId: '' };

describe('validateIncidentForm', () => {
  it('accepts a valid form', () => {
    expect(validateIncidentForm(valid)).toEqual({});
  });

  it('requires title, description and severity, ignoring whitespace', () => {
    expect(validateIncidentForm({ title: '   ', description: '\n', severity: '', assigneeId: '' })).toEqual({
      title: 'Enter a title.',
      description: 'Enter a description.',
      severity: 'Select a severity.',
    });
  });

  it('limits the title to 200 characters', () => {
    expect(validateIncidentForm({ ...valid, title: 'x'.repeat(200) })).toEqual({});
    expect(validateIncidentForm({ ...valid, title: 'x'.repeat(201) }).title).toMatch(/200 characters/);
  });

  it.each(['0', '-3', '1.5', 'abc', '2147483648'])('rejects assignee ID %s', assigneeId => {
    expect(validateIncidentForm({ ...valid, assigneeId }).assignee_id).toMatch(/whole number/);
  });

  it('accepts a positive integer assignee ID', () => {
    expect(validateIncidentForm({ ...valid, assigneeId: '42' })).toEqual({});
  });
});

describe('toCreatePayload', () => {
  it('trims text and omits an empty assignee', () => {
    expect(toCreatePayload({ ...valid, title: '  Phishing email  ' })).toEqual({
      title: 'Phishing email',
      description: 'Reported by finance.',
      severity: 'medium',
    });
  });

  it('includes a numeric assignee ID', () => {
    expect(toCreatePayload({ ...valid, assigneeId: ' 12 ' }).assignee_id).toBe(12);
  });
});

// Mirrors backend/app/schemas.py::clean_comment_body (see backend/tests/test_comments.py).
describe('comment rules', () => {
  it('normalises line endings to LF and trims the same blank characters as the server', () => {
    expect(normaliseComment('a\r\nb\rc')).toBe('a\nb\nc');
    expect(normaliseComment(' \t\n\u00a0\u3000\u200b\ufeffword\u2028\u202f ')).toBe('word');
    expect(normaliseComment('inner\u00a0space\tand tab')).toBe('inner\u00a0space\tand tab');
  });

  it('counts code points, so an emoji counts once', () => {
    expect(commentLength(String.fromCodePoint(0x1f6a8).repeat(3))).toBe(3);
    expect(commentLength('  ab\r\n  ')).toBe(2);
  });

  it('rejects blank comments, including invisible-only ones', () => {
    for (const value of ['', '   ', '\n\t', '\u3000', '\u200b\ufeff']) expect(validateComment(value)).toBe('Enter a comment.');
  });

  it('rejects control characters but allows tabs and line breaks', () => {
    for (const value of ['a\u0000b', 'a\u0001b', 'a\u000bb', 'a\u001bb', 'a\u007fb', 'a\u0085b', 'a\u009fb']) {
      expect(validateComment(value)).toBe('Comments cannot contain control characters.');
    }
    expect(validateComment('a\tb\nc')).toBeUndefined();
  });

  it('limits comments to 5,000 code points after trimming', () => {
    expect(validateComment('x'.repeat(5000))).toBeUndefined();
    expect(validateComment(`  ${'x'.repeat(5000)}  `)).toBeUndefined();
    expect(validateComment(String.fromCodePoint(0x1f6a8).repeat(5000))).toBeUndefined();
    expect(validateComment('x'.repeat(5001))).toBe('Comments can be at most 5,000 characters.');
  });
});

// Mirrors backend/app/schemas.py::SlaPolicyCreate (see backend/tests/test_sla_policy.py).
describe('SLA policy rules', () => {
  const valid = (): SlaPolicyFormValues => ({
    reason: 'Quarterly review',
    targets: {
      critical: { response: '15', resolution: '240' },
      high: { response: '60', resolution: '1440' },
      medium: { response: '240', resolution: '4320' },
      low: { response: '1440', resolution: '10080' },
    },
  });

  it('accepts a valid policy and builds the payload strictest first', () => {
    expect(validateSlaPolicy(valid())).toEqual({});
    expect(toSlaPolicyPayload({ ...valid(), reason: '  Quarterly review ' })).toEqual({
      reason: 'Quarterly review',
      targets: [
        { severity: 'critical', response_minutes: 15, resolution_minutes: 240 },
        { severity: 'high', response_minutes: 60, resolution_minutes: 1440 },
        { severity: 'medium', response_minutes: 240, resolution_minutes: 4320 },
        { severity: 'low', response_minutes: 1440, resolution_minutes: 10080 },
      ],
    });
  });

  it('requires whole minutes within the bounds', () => {
    for (const [value, message] of [['', 'Enter a whole number of minutes.'], ['1.5', 'Enter a whole number of minutes.'], ['-3', 'Enter a whole number of minutes.'],
      ['0', 'Enter at least 1 minute.'], ['43201', 'Enter at most 43,200 minutes.']]) {
      const values = valid();
      values.targets.low.response = value;
      expect(validateSlaPolicy(values)['low-response']).toBe(message);
    }
    const values = valid();
    values.targets.low.resolution = '525601';
    expect(validateSlaPolicy(values)['low-resolution']).toBe('Enter at most 525,600 minutes.');
  });

  it('keeps response within resolution and stricter severities no slower', () => {
    const inverted = valid();
    inverted.targets.high.response = '2000';
    expect(validateSlaPolicy(inverted)['high-response']).toBe('The response target cannot be longer than the resolution target.');
    const unordered = valid();
    unordered.targets.critical.resolution = '2000';
    expect(validateSlaPolicy(unordered).form).toBe('A more severe incident cannot have a longer target than a less severe one.');
  });

  it('requires a reason of at most 500 characters', () => {
    expect(validateSlaPolicy({ ...valid(), reason: '   ' }).reason).toBe('Enter a reason for the change.');
    expect(validateSlaPolicy({ ...valid(), reason: 'x'.repeat(501) }).reason).toBe('The reason can be at most 500 characters.');
  });
});
