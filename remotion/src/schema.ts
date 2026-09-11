import {z} from 'zod';

// The Python -> Remotion contract. Kept small on purpose: Python owns *what*
// (moment, text, importance, timing); this project owns *how* (layout,
// hierarchy, composition, motion). See
// src/video_generator/domain/motion_graphics.py for the producing side.

export const IMPORTANCE = ['dominant', 'secondary', 'support'] as const;
export const ROLES = [
  'hook',
  'keyword',
  'contrast',
  'statement',
  'question',
  'definition',
  'number',
  'annotation',
  'sequence',
  'transition',
] as const;
export const LAYOUTS = [
  'dominant-word',
  'small-plus-massive',
  'stacked-editorial',
  'split-contrast',
  'poster-statement',
] as const;
export const MOTIONS = ['fade_rise', 'scale_in', 'masked_reveal', 'stagger_rise'] as const;

// The editorial-density fields (see src/video_generator/domain/motion_graphics.py).
// How hard the edit leans, which move it is making, how the type meets the
// picture, and how much of the frame the dominant word may take. Every one has
// a default so a scene document that predates them still renders.
export const INTENSITIES = ['low', 'medium', 'high', 'peak'] as const;
export const INTENTS = [
  'impact_word',
  'statement_build',
  'contrast',
  'question',
  'definition',
  'number_hit',
  'sequence',
  'annotation',
  'quote_fragment',
  'chapter_transition',
  'visual_interruption',
] as const;
export const SURFACES = ['bare', 'scrim', 'card'] as const;
export const SCALE_HINTS = ['normal', 'amplified', 'giant'] as const;

export const blockSchema = z.object({
  text: z.string().min(1),
  importance: z.enum(IMPORTANCE),
  accent: z.boolean().default(false),
});

export const eventSchema = z.object({
  id: z.string().min(1),
  start: z.number().min(0), // seconds
  duration: z.number().min(0.1), // seconds
  role: z.enum(ROLES),
  layout: z.enum(LAYOUTS),
  variant: z.string().nullable().default(null),
  motion: z.enum(MOTIONS),
  intensity: z.enum(INTENSITIES).default('medium'),
  intent: z.enum(INTENTS).default('impact_word'),
  surface: z.enum(SURFACES).default('bare'),
  scale_hint: z.enum(SCALE_HINTS).default('normal'),
  chain_position: z.number().int().min(0).default(0),
  chain_length: z.number().int().min(1).default(1),
  blocks: z.array(blockSchema).min(1).max(3),
});

export const themeSchema = z.object({
  foreground: z.string(),
  accent: z.string(),
  muted: z.string(),
  background: z.string().nullable().default(null),
});

export const compositionSchema = z.object({
  width: z.number().int().positive(),
  height: z.number().int().positive(),
  fps: z.number().positive(),
  durationInSeconds: z.number().positive(),
});

export const motionGraphicsSchema = z.object({
  schema_version: z.literal(1),
  composition: compositionSchema,
  theme: themeSchema,
  events: z.array(eventSchema),
});

export type MotionBlock = z.infer<typeof blockSchema>;
export type MotionEvent = z.infer<typeof eventSchema>;
export type MotionTheme = z.infer<typeof themeSchema>;
export type MotionGraphicsProps = z.infer<typeof motionGraphicsSchema>;

export const DEFAULT_PROPS: MotionGraphicsProps = {
  schema_version: 1,
  composition: {width: 1920, height: 1080, fps: 30, durationInSeconds: 6},
  theme: {
    foreground: '#EEF3F5',
    accent: '#3CA3E5',
    muted: '#CEC8C4',
    background: null,
  },
  events: [
    {
      id: 'preview_hook',
      start: 0.4,
      duration: 3.2,
      role: 'hook',
      layout: 'small-plus-massive',
      variant: null,
      motion: 'stagger_rise',
      intensity: 'peak',
      intent: 'impact_word',
      surface: 'bare',
      scale_hint: 'giant',
      chain_position: 0,
      chain_length: 1,
      blocks: [
        {text: 'QUANDO SE REPETE', importance: 'support', accent: false},
        {text: 'FAMILIAR', importance: 'dominant', accent: false},
      ],
    },
  ],
};
