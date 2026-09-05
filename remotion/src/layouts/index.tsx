import React from 'react';
import {AbsoluteFill, interpolate, useCurrentFrame, useVideoConfig} from 'remotion';
import type {MotionEvent, MotionTheme} from '../schema';
import {Block} from '../Block';
import {fitSize, breakDominant} from '../text';
import {SCALE_BOOST} from '../theme';

export type LayoutProps = {
  event: MotionEvent;
  theme: MotionTheme;
  width: number;
  height: number;
};

const MARGIN = 0.062; // safe-area fraction, matches the domain's safe_margin_fraction

const useFrameGeom = (width: number, height: number) => {
  const mx = Math.round(width * MARGIN);
  const my = Math.round(height * MARGIN);
  return {mx, my, inner: width - mx * 2};
};

// how much bigger the dominant word runs for this event's editorial intensity
const boostOf = (event: MotionEvent): number => SCALE_BOOST[event.scale_hint] ?? 1;

/* ------------------------------------------------------------------ */
/* Surface — how the type meets the picture underneath it              */
/* ------------------------------------------------------------------ */
// `bare` (the default and the whole point of the layer) draws nothing: the type
// lives on the moving asset, held legible by its own halo. `scrim` fades a soft
// dark wash in under the block for a busy plate. `card` is the rare full
// title-card dim, kept for a definition or a chapter break. All of it fades in
// with the event so it never reads as a hard cut to black.
const Surface: React.FC<{surface: MotionEvent['surface']; anchor: 'bottom' | 'center'}> = ({
  surface,
  anchor,
}) => {
  const frame = useCurrentFrame();
  const {durationInFrames} = useVideoConfig();
  if (surface === 'bare') {
    return null;
  }
  const inTo = interpolate(frame, [0, 8], [0, 1], {extrapolateLeft: 'clamp', extrapolateRight: 'clamp'});
  const outTo = interpolate(
    frame,
    [durationInFrames - 8, durationInFrames - 1],
    [1, 0],
    {extrapolateLeft: 'clamp', extrapolateRight: 'clamp'},
  );
  const opacity = Math.min(inTo, outTo);
  if (surface === 'card') {
    return (
      <AbsoluteFill
        style={{
          opacity,
          background:
            'radial-gradient(130% 100% at 50% 46%, rgba(4,5,8,0.42), rgba(4,5,8,0.78) 82%)',
        }}
      />
    );
  }
  // scrim: a band under the type, biased to where the block sits
  const band =
    anchor === 'bottom'
      ? 'linear-gradient(0deg, rgba(4,5,8,0.72) 2%, rgba(4,5,8,0.34) 34%, rgba(4,5,8,0) 62%)'
      : 'radial-gradient(70% 46% at 34% 50%, rgba(4,5,8,0.6), rgba(4,5,8,0) 72%)';
  return <AbsoluteFill style={{opacity, background: band}} />;
};

// A thin accent tick — a small piece of graphic furniture that ties the type to
// the frame without competing with it.
const Rule: React.FC<{theme: MotionTheme; width: number; thickness: number}> = ({
  theme,
  width,
  thickness,
}) => (
  <div
    style={{
      width,
      height: thickness,
      background: theme.accent,
      marginBottom: thickness * 5,
    }}
  />
);

/* ------------------------------------------------------------------ */
/* dominant-word — one word, low and flush-left, the frame kept above  */
/* ------------------------------------------------------------------ */
const DominantWord: React.FC<LayoutProps> = ({event, theme, width, height}) => {
  const {mx, my, inner} = useFrameGeom(width, height);
  const [kicker, word] = event.blocks.length === 2 ? event.blocks : [null, event.blocks[0]];
  const boost = boostOf(event);
  // a boosted word bleeds toward the right margin but never past it; fitSize is
  // handed the real column so a long word shrinks to fit even when the mid-word
  // wrap did not land it inside on its own
  const rightMargin = boost > 1.15 ? Math.round(mx * 0.55) : mx;
  const avail = width - mx - rightMargin;
  const text = breakDominant(word.text, word.text.length >= 8);
  const size = fitSize(text, 'dominant', height, avail, boost);
  return (
    <AbsoluteFill>
      <Surface surface={event.surface} anchor="bottom" />
      <div
        style={{
          position: 'absolute',
          left: mx,
          right: rightMargin,
          bottom: my + height * 0.06,
          display: 'flex',
          flexDirection: 'column',
          alignItems: 'flex-start',
        }}
      >
        {kicker ? (
          <Block
            block={kicker}
            size={fitSize(kicker.text, 'support', height, inner)}
            index={0}
            blockCount={2}
            motion={event.motion}
            theme={theme}
            style={{marginBottom: height * 0.02}}
          />
        ) : null}
        <Rule theme={theme} width={size * 0.36} thickness={Math.max(3, height * 0.006)} />
        <Block
          block={{...word, importance: 'dominant'}}
          size={size}
          index={kicker ? 1 : 0}
          blockCount={event.blocks.length}
          motion={event.motion}
          theme={theme}
        />
      </div>
    </AbsoluteFill>
  );
};

/* ------------------------------------------------------------------ */
/* small-plus-massive — wide-tracked label sitting on a display word   */
/* ------------------------------------------------------------------ */
const SmallPlusMassive: React.FC<LayoutProps> = ({event, theme, width, height}) => {
  const {mx, inner} = useFrameGeom(width, height);
  const support = event.blocks[0];
  const massive = event.blocks[event.blocks.length - 1];
  const boost = boostOf(event);
  const text = breakDominant(massive.text, boost > 1.15);
  const size = fitSize(text, 'dominant', height, inner, boost);
  return (
    <AbsoluteFill>
      <Surface surface={event.surface} anchor="center" />
      <div
        style={{
          position: 'absolute',
          left: mx,
          right: boost > 1.15 ? Math.round(mx * 0.5) : width * 0.34,
          top: '50%',
          transform: 'translateY(-52%)',
          display: 'flex',
          flexDirection: 'column',
          alignItems: 'flex-start',
          gap: height * 0.018,
        }}
      >
        <Block
          block={{...support, importance: 'support'}}
          size={fitSize(support.text, 'support', height, inner)}
          index={0}
          blockCount={2}
          motion={event.motion}
          theme={theme}
        />
        <Block
          block={{...massive, importance: 'dominant', text}}
          size={size}
          index={1}
          blockCount={2}
          motion={event.motion}
          theme={theme}
        />
      </div>
    </AbsoluteFill>
  );
};

/* ------------------------------------------------------------------ */
/* stacked-editorial — a magazine deck, flush-left, each line a weight  */
/* ------------------------------------------------------------------ */
const StackedEditorial: React.FC<LayoutProps> = ({event, theme, width, height}) => {
  const {mx, my} = useFrameGeom(width, height);
  const edge = event.variant === 'edge';
  const left = edge ? Math.round(mx * 0.22) : mx;
  // A hanging deck: each tier steps further in, so the eye walks down and right.
  // This is what keeps it from reading as the same tight stack as
  // small-plus-massive. The edge variant does the opposite — it bleeds flush off
  // the left and sits low — so it gets no step and no kicker rule.
  const step = edge ? 0 : Math.round(width * 0.05);
  const ramp: Array<'support' | 'secondary' | 'dominant'> =
    event.blocks.length >= 3 ? ['support', 'secondary', 'dominant'] : ['support', 'dominant'];
  const avail = width - left - mx - step * (event.blocks.length - 1);
  const boost = boostOf(event);
  return (
    <AbsoluteFill>
      <Surface surface={event.surface} anchor={edge ? 'bottom' : 'center'} />
      <div
        style={{
          position: 'absolute',
          left,
          right: mx,
          ...(edge ? {bottom: my + height * 0.04} : {top: height * 0.22}),
          display: 'flex',
          flexDirection: 'column',
          alignItems: 'flex-start',
          gap: height * 0.016,
        }}
      >
        {event.blocks.map((b, i) => {
          const imp = ramp[Math.min(i, ramp.length - 1)];
          const text = imp === 'dominant' ? breakDominant(b.text, boost > 1.15) : b.text;
          const row = (
            <Block
              block={{...b, importance: imp, text}}
              size={fitSize(text, imp, height, avail, imp === 'dominant' ? boost : 1)}
              index={i}
              blockCount={event.blocks.length}
              motion={event.motion}
              theme={theme}
            />
          );
          return (
            <div
              key={b.text + i}
              style={{marginLeft: i * step, display: 'flex', alignItems: 'center', gap: step * 0.4}}
            >
              {row}
              {i === 0 && !edge ? (
                <div style={{width: width * 0.055, height: Math.max(2, height * 0.004), background: theme.accent}} />
              ) : null}
            </div>
          );
        })}
      </div>
    </AbsoluteFill>
  );
};

/* ------------------------------------------------------------------ */
/* split-contrast — two poles pushed apart, or a stacked X ≠ Y pair    */
/* ------------------------------------------------------------------ */
const SplitContrast: React.FC<LayoutProps> = ({event, theme, width, height}) => {
  const {mx, my, inner} = useFrameGeom(width, height);
  const a = event.blocks[0];
  const b = event.blocks[event.blocks.length - 1];
  const boost = boostOf(event);

  if (event.variant === 'pair') {
    const indent = Math.round(width * 0.08);
    const left = mx + indent;
    const avail = width - left - mx;
    // the X ≠ Y card is a three-tier stack that also has to fit vertically, so
    // it takes only a gentle share of the intensity boost and always wraps a
    // long payoff word rather than letting it bleed off the column
    const pairBoost = Math.min(boost, 1.14);
    const bText = breakDominant(b.text, b.text.length > 9);
    const sizeA = fitSize(a.text, 'secondary', height, avail);
    const sizeB = fitSize(bText, 'dominant', height, avail, pairBoost);
    return (
      <AbsoluteFill>
        <Surface surface={event.surface} anchor="center" />
        <div
          style={{
            position: 'absolute',
            left,
            right: mx,
            top: height * 0.24,
            display: 'flex',
            flexDirection: 'column',
            alignItems: 'flex-start',
            gap: height * 0.01,
          }}
        >
          <Block block={{...a, importance: 'secondary'}} size={sizeA} index={0} blockCount={3} motion={event.motion} theme={theme} />
          <div
            style={{
              fontSize: sizeB * 0.62,
              lineHeight: 1,
              fontWeight: 800,
              color: theme.accent,
              margin: `${height * 0.004}px 0`,
              textShadow: '0 3px 18px rgba(6,7,10,0.6)',
            }}
          >
            ≠
          </div>
          <Block block={{...b, importance: 'dominant', text: bText}} size={sizeB} index={2} blockCount={3} motion={event.motion} theme={theme} />
        </div>
      </AbsoluteFill>
    );
  }

  // default: one block high-left, the other low-right, maximum empty frame between
  const availTop = width * 0.55;
  const availBot = width * 0.62;
  return (
    <AbsoluteFill>
      <Surface surface={event.surface} anchor="center" />
      <div style={{position: 'absolute', left: mx, top: height * 0.13, maxWidth: availTop}}>
        <Block block={{...a, importance: 'secondary'}} size={fitSize(a.text, 'secondary', height, availTop)} index={0} blockCount={2} motion={event.motion} theme={theme} />
      </div>
      <div style={{position: 'absolute', right: mx, bottom: my + height * 0.05, maxWidth: availBot, textAlign: 'right'}}>
        <Block
          block={{...b, importance: 'dominant', text: breakDominant(b.text, boost > 1.15)}}
          size={fitSize(breakDominant(b.text, boost > 1.15), 'dominant', height, availBot, boost)}
          index={1}
          blockCount={2}
          motion={event.motion}
          theme={theme}
          align="right"
        />
      </div>
    </AbsoluteFill>
  );
};

/* ------------------------------------------------------------------ */
/* poster-statement — centred, symmetric, a title card                 */
/* ------------------------------------------------------------------ */
const PosterStatement: React.FC<LayoutProps> = ({event, theme, width, height}) => {
  const {mx, inner} = useFrameGeom(width, height);
  const kicker = event.blocks.length >= 2 ? event.blocks[0] : null;
  const word = event.blocks[event.blocks.length - 1];
  const boost = boostOf(event);
  const text = breakDominant(word.text, boost > 1.15);
  const size = fitSize(text, 'dominant', height, inner, boost);
  return (
    <AbsoluteFill style={{alignItems: 'center', justifyContent: 'center'}}>
      <Surface surface={event.surface} anchor="center" />
      <div
        style={{
          display: 'flex',
          flexDirection: 'column',
          alignItems: 'center',
          gap: height * 0.03,
          marginTop: -height * 0.04,
          maxWidth: inner,
        }}
      >
        {kicker ? (
          <Block
            block={{...kicker, importance: 'support'}}
            size={fitSize(kicker.text, 'support', height, inner)}
            index={0}
            blockCount={2}
            motion={event.motion}
            theme={theme}
            align="center"
          />
        ) : null}
        <Rule theme={theme} width={size * 0.3} thickness={Math.max(3, height * 0.006)} />
        <Block
          block={{...word, importance: 'dominant', text}}
          size={size}
          index={kicker ? 1 : 0}
          blockCount={event.blocks.length}
          motion={event.motion}
          theme={theme}
          align="center"
        />
      </div>
    </AbsoluteFill>
  );
};

const REGISTRY: Record<MotionEvent['layout'], React.FC<LayoutProps>> = {
  'dominant-word': DominantWord,
  'small-plus-massive': SmallPlusMassive,
  'stacked-editorial': StackedEditorial,
  'split-contrast': SplitContrast,
  'poster-statement': PosterStatement,
};

export const Layout: React.FC<LayoutProps> = (props) => {
  const Chosen = REGISTRY[props.event.layout] ?? PosterStatement;
  return <Chosen {...props} />;
};
