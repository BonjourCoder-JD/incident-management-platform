import { describe, expect, it } from 'vitest';
import { toCreatePayload, validateIncidentForm } from './validation';

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
