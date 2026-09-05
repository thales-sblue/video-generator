import {interpolate, spring} from 'remotion';
import type {MotionEvent} from './schema';

export type MotionStyle = {
  opacity: number;
  transform: string;
  clipPath?: string;
};

type Args = {
  frame: number;
  fps: number;
  durationInFrames: number;
  motion: MotionEvent['motion'];
  index: number; // block index within the event
  blockCount: number;
};

const STAGGER_FRAMES = 5; // ~150ms at 30fps — the support line lands before the word
const LEAD_FRAMES = 2;
const EXIT_FRAMES = 8;

// All primitives are pure functions of the frame — no Date, no random — so a
// render is byte-for-byte reproducible. Motion here only reveals the hierarchy
// the layout already built; nothing decorative.
export const blockMotion = ({
  frame,
  fps,
  durationInFrames,
  motion,
  index,
}: Args): MotionStyle => {
  const delay =
    motion === 'stagger_rise' ? index * STAGGER_FRAMES : index * LEAD_FRAMES;
  const local = frame - delay;

  const enter = spring({
    frame: local,
    fps,
    config: {damping: 200, mass: 0.7, stiffness: 120},
    durationInFrames: motion === 'scale_in' ? 20 : 16,
  });

  const exit = interpolate(
    frame,
    [durationInFrames - EXIT_FRAMES, durationInFrames - 1],
    [1, 0],
    {extrapolateLeft: 'clamp', extrapolateRight: 'clamp'},
  );

  const opacity = Math.max(0, Math.min(enter, exit));

  if (motion === 'scale_in') {
    const scale = interpolate(enter, [0, 1], [0.86, 1]);
    return {opacity, transform: `scale(${scale.toFixed(4)})`};
  }

  if (motion === 'masked_reveal') {
    const rise = interpolate(enter, [0, 1], [0.22, 0]); // fraction of own height
    const cover = interpolate(enter, [0, 1], [100, 0], {extrapolateRight: 'clamp'});
    return {
      opacity: exit, // the clip does the reveal; don't double it with a fade-in
      transform: `translateY(${(rise * 100).toFixed(2)}%)`,
      clipPath: `inset(0 0 ${cover.toFixed(2)}% 0)`,
    };
  }

  // fade_rise and stagger_rise share the arrival, differ only in delay
  const rise = interpolate(enter, [0, 1], [26, 0]);
  return {opacity, transform: `translateY(${rise.toFixed(2)}px)`};
};
