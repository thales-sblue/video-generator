import {Config} from '@remotion/cli/config';

// Deterministic, offline defaults. Codec / ProRes profile / pixel format are
// chosen per-invocation on the command line (the pipeline needs an alpha ProRes
// overlay; the spike also renders an opaque H.264 for motion review), so they
// are deliberately not pinned here.
Config.setVideoImageFormat('png');
Config.setChromiumOpenGlRenderer('angle');
Config.setDelayRenderTimeoutInMilliseconds(30000);
