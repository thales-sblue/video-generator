import {z} from 'zod';

// The Python -> Remotion contract. Kept small on purpose: Python owns *what*
// (moment, text, importance, timing); this project owns *how* (layout,
// hierarchy, composition, motion). See
// src/video_generator/domain/motion_graphics.py for the producing side.

export const IMPORTANCE = ['dominant', 'secondary', 'support'] as const;
export const ROLES = ['hook', 'keyword', 'contrast', 'statement', 'question'] as const;
export const LAYOUTS = [
  'dominant-word',
  'small-plus-massive',
  'stacked-editorial',
  'split-contrast',
  'poster-statement',
] as const;
export const MOTIONS = ['fade_rise', 'scale_in', 'masked_reveal', 'stagger_rise'] as const;

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
      blocks: [
        {text: 'QUANDO SE REPETE', importance: 'support', accent: false},
        {text: 'FAMILIAR', importance: 'dominant', accent: false},
      ],
    },
  ],
};
