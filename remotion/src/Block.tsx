import React from 'react';
import {useCurrentFrame, useVideoConfig} from 'remotion';
import type {MotionBlock, MotionEvent, MotionTheme} from './schema';
import {blockMotion} from './motion';
import {FONT_STACK, LINE_HEIGHT, TRACKING, WEIGHT, blockColor, halo} from './theme';

export const Block: React.FC<{
  block: MotionBlock;
  size: number; // resolved pixel size
  index: number;
  blockCount: number;
  motion: MotionEvent['motion'];
  theme: MotionTheme;
  align?: 'left' | 'center' | 'right';
  style?: React.CSSProperties;
}> = ({block, size, index, blockCount, motion, theme, align = 'left', style}) => {
  const frame = useCurrentFrame();
  const {fps, durationInFrames} = useVideoConfig();
  const m = blockMotion({frame, fps, durationInFrames, motion, index, blockCount});

  return (
    <div
      style={{
        opacity: m.opacity,
        transform: m.transform,
        clipPath: m.clipPath,
        WebkitClipPath: m.clipPath,
        willChange: 'transform, opacity, clip-path',
        ...style,
      }}
    >
      <div
        style={{
          fontFamily: FONT_STACK,
          fontWeight: WEIGHT[block.importance],
          fontSize: size,
          lineHeight: LINE_HEIGHT[block.importance],
          letterSpacing: `${TRACKING[block.importance]}em`,
          color: blockColor(block, theme),
          textShadow: halo(size, block.importance),
          textAlign: align,
          textTransform: 'uppercase',
          whiteSpace: 'pre-line',
          fontVariationSettings: `"wght" ${WEIGHT[block.importance]}`,
          margin: 0,
          // room for accents/tildes (ATENÇÃO, OPINIÃO) so a tight line box
          // never crops the diacritic
          paddingTop: size * 0.1,
        }}
      >
        {block.text}
      </div>
    </div>
  );
};
