import { describe, expect, it } from 'vitest';
import {
  C,
  actionRank,
  actionStyle,
  fmt,
  normPortfolio,
  oppActionRank,
  setupMeta,
} from './warRoomDomain';

describe('War Room domain rules', () => {
  it('normalizes missing portfolio names into the visible fallback bucket', () => {
    expect(normPortfolio('')).toBe('OTHER');
    expect(normPortfolio('Core')).toBe('Core');
  });

  it('keeps actionable directives ahead of hold directives', () => {
    expect(actionRank('open')).toBeLessThan(actionRank('hold'));
    expect(oppActionRank('buy')).toBeLessThan(oppActionRank('avoid'));
  });

  it('provides safe display fallbacks for unknown API values', () => {
    expect(actionStyle('future_action').label).toBe('future action');
    expect(setupMeta('future_setup').color).toBe(C.muted);
    expect(fmt(null)).toBe('—');
  });
});
