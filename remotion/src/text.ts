import type {MotionBlock} from './schema';
import {SCALE, TRACKING} from './theme';

// Mean uppercase advance for Inter at display weights, in em. Only used to keep
// a block from leaving the frame — Chrome does the real typesetting, this just
// has to be conservative, so it runs slightly wide on purpose.
const ADVANCE = 0.66;
// Never let a block fill the last sliver of its column: a display word that
// touches both margins reads as "stretched to fit", not as placed.
const BREATHING = 0.9;

export const fitSize = (
  text: string,
  importance: MotionBlock['importance'],
  height: number,
  availableWidth: number,
  boost = 1,
): number => {
  const base = height * SCALE[importance] * boost;
  const longestLine = text.split('\n').reduce((m, l) => Math.max(m, l.length), 1);
  const advance = ADVANCE + Math.max(0, TRACKING[importance]);
  const estimated = longestLine * advance * base;
  // an amplified/giant display word is allowed to run nearly to the margin —
  // that near-bleed is the effect — where a normal one keeps its breathing room
  const room = availableWidth * (boost > 1.15 ? 1.02 : BREATHING);
  if (room > 0 && estimated > room) {
    return Math.max(height * 0.02, base * (room / estimated));
  }
  return base;
};

// Intentional line breaks: a long single word or short phrase set as the
// dominant block is broken near its middle so it stacks instead of shrinking to
// a stripe. Short words and support labels are left as one line.
export const breakDominant = (text: string, aggressive = false): string => {
  if (text.includes('\n')) {
    return text;
  }
  if (text.length <= (aggressive ? 6 : 9)) {
    return text;
  }
  const spaces: number[] = [];
  for (let i = 0; i < text.length; i += 1) {
    if (text[i] === ' ') spaces.push(i);
  }
  if (spaces.length > 0) {
    const mid = text.length / 2;
    const at = spaces.reduce((best, s) => (Math.abs(s - mid) < Math.abs(best - mid) ? s : best), spaces[0]);
    return `${text.slice(0, at)}\n${text.slice(at + 1)}`;
  }
  // hyphenless single word. Split it near the middle so it stacks and fills the
  // frame instead of shrinking to a stripe — always once it is long enough to
  // clip a column, earlier still when the editorial intensity wants it huge.
  if ((aggressive && text.length >= 8) || text.length >= 12) {
    const at = Math.round(text.length / 2);
    return `${text.slice(0, at)}\n${text.slice(at)}`;
  }
  return text;
};
