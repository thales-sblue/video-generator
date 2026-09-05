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
): number => {
  const base = height * SCALE[importance];
  const longestLine = text.split('\n').reduce((m, l) => Math.max(m, l.length), 1);
  const advance = ADVANCE + Math.max(0, TRACKING[importance]);
  const estimated = longestLine * advance * base;
  const room = availableWidth * BREATHING;
  if (room > 0 && estimated > room) {
    return Math.max(height * 0.02, base * (room / estimated));
  }
  return base;
};

// Intentional line breaks: a long single word or short phrase set as the
// dominant block is broken near its middle so it stacks instead of shrinking to
// a stripe. Short words and support labels are left as one line.
export const breakDominant = (text: string): string => {
  if (text.length <= 9) {
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
  // hyphenless single word: keep whole, fitSize will scale it
  return text;
};
