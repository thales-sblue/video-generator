import React from 'react';
import {Composition} from 'remotion';
import {MotionGraphics} from './MotionGraphics';
import {motionGraphicsSchema, DEFAULT_PROPS, type MotionGraphicsProps} from './schema';

// One composition. Its size, fps and length come entirely from the incoming
// props (the Python contract), so the overlay always matches the video it will
// be composited onto. `remotion render MotionGraphics out.mov --props=scene.json`.
export const RemotionRoot: React.FC = () => {
  return (
    <Composition
      id="MotionGraphics"
      component={MotionGraphics}
      schema={motionGraphicsSchema}
      defaultProps={DEFAULT_PROPS}
      width={DEFAULT_PROPS.composition.width}
      height={DEFAULT_PROPS.composition.height}
      fps={DEFAULT_PROPS.composition.fps}
      durationInFrames={Math.max(
        1,
        Math.round(DEFAULT_PROPS.composition.durationInSeconds * DEFAULT_PROPS.composition.fps),
      )}
      calculateMetadata={({props}: {props: MotionGraphicsProps}) => {
        const {width, height, fps, durationInSeconds} = props.composition;
        return {
          width,
          height,
          fps,
          durationInFrames: Math.max(1, Math.round(durationInSeconds * fps)),
        };
      }}
    />
  );
};
