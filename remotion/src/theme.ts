import {DISPLAY_FAMILY} from './fonts';
import type {MotionBlock, MotionTheme} from './schema';

export const FONT_STACK = `${DISPLAY_FAMILY}, "Segoe UI", Inter, system-ui, sans-serif`;

// Type scale as a fraction of composition height. The gap between steps is
// deliberately violent — a viewer should read the hierarchy before reading a
// word — so `support` and `dominant` sit roughly 1:5 apart, not on a smooth ramp.
export const SCALE: Record<MotionBlock['importance'], number> = {
  support: 0.031,
  secondary: 0.086,
  dominant: 0.152,
};

// Multiplier on the dominant word, driven by the editorial ``scale_hint``. A
// ``peak`` beat gets a word that fills most of the frame and wraps; a ``high``
// beat a clearly bigger-than-usual one; everything else its layout's own size.
// Aggressive on purpose — the point is that a viewer feels the range.
export const SCALE_BOOST: Record<'normal' | 'amplified' | 'giant', number> = {
  normal: 1,
  amplified: 1.32,
  giant: 1.9,
};

export const WEIGHT: Record<MotionBlock['importance'], number> = {
  support: 500,
  secondary: 680,
  dominant: 900,
};

// Tracking in em. Wide on the small label (reads as a kicker, not a lost
// caption), tight to slightly negative on the display word.
export const TRACKING: Record<MotionBlock['importance'], number> = {
  support: 0.34,
  secondary: 0.006,
  dominant: -0.012,
};

export const LINE_HEIGHT: Record<MotionBlock['importance'], number> = {
  support: 1.2,
  secondary: 1.08,
  dominant: 1.02,
};

export const blockColor = (block: MotionBlock, theme: MotionTheme): string => {
  if (block.accent) {
    return theme.accent;
  }
  return block.importance === 'support' ? theme.muted : theme.foreground;
};

// A soft dark halo so light type survives a bright photo underneath without a
// caption band. Scaled by the rendered pixel size of the block.
export const halo = (pixelSize: number, importance: MotionBlock['importance']): string => {
  const spread = importance === 'support' ? pixelSize * 0.09 : pixelSize * 0.05;
  const near = Math.max(1.5, pixelSize * 0.02);
  return [
    `0 ${near.toFixed(1)}px ${(spread * 1.6).toFixed(1)}px rgba(6,7,10,0.62)`,
    `0 0 ${(spread * 2.4).toFixed(1)}px rgba(6,7,10,0.5)`,
  ].join(', ');
};
