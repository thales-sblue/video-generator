import React from 'react';
import {AbsoluteFill, Sequence, useVideoConfig, Img, staticFile} from 'remotion';
import type {MotionGraphicsProps} from './schema';
import {ensureFonts} from './fonts';
import {Layout} from './layouts';

// Optional dev/eval backdrop. The pipeline renders this composition with a
// transparent background (`theme.background = null`) and composites the alpha
// overlay in FFmpeg. `background` as a colour or a staticFile path is only for
// stills and studio review, so a paused frame can be judged in context.
const Backdrop: React.FC<{background: string | null}> = ({background}) => {
  if (!background) {
    return null;
  }
  const isImage = /\.(png|jpe?g|webp)$/i.test(background);
  return (
    <AbsoluteFill>
      {isImage ? (
        <Img src={staticFile(background)} style={{width: '100%', height: '100%', objectFit: 'cover'}} />
      ) : (
        <AbsoluteFill style={{background}} />
      )}
      {/* the grade the dark channel already applies to its footage */}
      <AbsoluteFill
        style={{
          background:
            'radial-gradient(120% 90% at 30% 40%, rgba(0,0,0,0.12), rgba(0,0,0,0.52) 78%)',
        }}
      />
    </AbsoluteFill>
  );
};

export const MotionGraphics: React.FC<MotionGraphicsProps> = ({composition, theme, events}) => {
  ensureFonts();
  const {fps, width, height} = useVideoConfig();

  return (
    <AbsoluteFill style={{backgroundColor: 'transparent'}}>
      <Backdrop background={theme.background} />
      {events.map((event) => {
        const from = Math.round(event.start * fps);
        const dur = Math.max(1, Math.round(event.duration * fps));
        return (
          <Sequence key={event.id} from={from} durationInFrames={dur} name={`${event.id} · ${event.layout}`}>
            <Layout event={event} theme={theme} width={width} height={height} />
          </Sequence>
        );
      })}
    </AbsoluteFill>
  );
};
