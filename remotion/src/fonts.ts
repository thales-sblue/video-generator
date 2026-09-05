import {staticFile, continueRender, delayRender} from 'remotion';

// One vendored variable font (Inter, SIL OFL 1.1 — see ../assets/fonts/OFL.txt).
// Loaded from a local file so a render never touches the network and always
// produces the same glyphs. The full weight axis (100–900) is what lets a
// single family carry the whole hierarchy, from a hairline support label to a
// 900-weight display word.

export const DISPLAY_FAMILY = 'InterVariableLocal';

let started = false;

export const ensureFonts = () => {
  if (started || typeof document === 'undefined') {
    return;
  }
  started = true;
  const handle = delayRender('Loading vendored Inter variable font');
  const face = new FontFace(
    DISPLAY_FAMILY,
    `url(${staticFile('fonts/Inter-Variable.ttf')}) format('truetype')`,
    {weight: '100 900', display: 'block'},
  );
  face
    .load()
    .then((loaded) => {
      (document as unknown as {fonts: {add: (f: FontFace) => void}}).fonts.add(loaded);
      continueRender(handle);
    })
    .catch((err) => {
      // Fail loud in dev, but never hang the render: fall back to the stack.
      console.error('font load failed', err);
      continueRender(handle);
    });
};
