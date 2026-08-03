# Dashboard video-player component research

Research snapshot: 2026-08-03.

## Recommendation

Use **Vidstack's native video provider and Default Video Layout** for Preview. This became the
selected direction after comparing the candidates and validating Vidstack against a real rendered
replay in the dashboard.

This is the best fit for this dashboard because it separates concerns cleanly:

- the browser owns MP4/H.264 playback, buffering, play/pause, seeking, and the playback clock;
- Vidstack owns accessible controls, menus, responsive layout, gestures, and player state;
- Vue owns Minecraft-specific overlays and converts media seconds to server ticks;
- the dataset timeline remains a separate editor, not a second controller for the video.

Vidstack documents a Vue path through typed Web Components. Its Default Layout is explicitly a
production-ready UI, adapts to small containers, and includes slider previews, fullscreen,
captions, settings, gestures, and keyboard behavior
([Vue installation](https://vidstack.io/docs/player/getting-started/installation/vue/),
[Default Layout](https://vidstack.io/docs/player/components/layouts/default-layout/)). This matches
the Dockview Preview panel without making the project's Basic components recreate player behavior.

Keep MediaBunny as an optional media-tooling dependency for jobs that genuinely require decoded
frames, such as generating thumbnails, frame inspection, or a future exact-frame fallback. Do not
use `CanvasSink` plus an application-written animation clock for normal preview playback. The
current preview manually converts ticks to seconds and requests decoded canvases
([current implementation](../../apps/dashboard/src/features/media/composables/useMediaPreview.ts));
that is useful editor machinery, but it recreates playback behavior that the browser and mature
player controls already solve.

## Shortlist

| Candidate | Finished UI | Vue / composition | Accessibility | Timeline and thumbnails | Fit here |
| --- | --- | --- | --- | --- | --- |
| **Vidstack** | Strong production-ready Default and Plyr layouts | Official Web Component/Vue path; 30+ headless components | Claims WCAG 2.1, FCC and CVAA support | Chapters, gestures, VTT/JSON thumbnails and hover previews | **Selected**: the complete responsive layout and native provider worked with a real replay |
| **Media Chrome + player.style** | Themes are available; controls alone are intentionally unopinionated | Native Web Components; Vue listed officially; controls and playback engine are separate | Officially states controls expose state accessibly | Time range, thumbnail preview, chapters and theme slots | Best fallback when individually composed controls matter more than an integrated player |
| **Plyr** | Clean, familiar default skin | Vanilla JS initialized from Vue; custom CSS and controls markup | Officially advertises screen-reader, captions and keyboard support | Progress, markers, VTT/sprite thumbnails | Good small conventional player, but less composable than the first two |
| **Video.js** | Mature v8 skin/plugin ecosystem; promising v10 skins | v8 is imperative JS; v10 has HTML components and React, but remains beta | Strong documented ARIA, keyboard, focus, touch and reduced-motion behavior in v10 | Plugins in v8; first-class slider/thumbnail components in v10 | Too much surface for a local MP4 preview; reassess v10 after GA |
| **ArtPlayer** | Polished feature-rich default UI | Official Vue wrapper recipe, but options are not reactive | Official docs make weaker accessibility commitments than Media Chrome/Vidstack/Video.js | Progress preview plus VTT/auto-thumbnail plugins | Attractive visual shortcut, but an imperative player with less accessible/composable UI |

### Media Chrome and player.style

Media Chrome supplies independent custom elements such as play, seek, time display, time range,
thumbnail preview, volume and fullscreen controls
([component catalog](https://www.media-chrome.org/)). The controller relays state and commands
between those controls and the slotted media element, and controls can also be positioned outside
the controller. This matches the requested separation between preview playback and the dataset
timeline ([controller usage](https://www.media-chrome.org/docs/en/get-started#adding-controls)).

The important detail is that its time control is still ultimately a range input. The improvement is
not avoiding `<input type="range">`; it is adopting a complete seek model, keyboard/ARIA behavior,
hover time, buffering, chapters, thumbnails, focus states and a coherent theme around it
([Media time range](https://www.media-chrome.org/docs/en/components/media-time-range)).

Maintenance signal: Media Chrome is MIT-licensed and released
[v4.19.2 on 2026-06-10](https://github.com/muxinc/media-chrome/releases/tag/v4.19.2).
player.style is also MIT-licensed and its official repository describes the themes as usable with
every player and framework ([repository](https://github.com/muxinc/player.style)). No official
compressed-size claim was found, so size should be measured in this Vite application before merge.

### Vidstack

Vidstack has the strongest single-package feature list: production-ready layouts, a standard API
across providers, keyboard and gesture support, accessible headless components, chapters and
thumbnail previews. Its docs explicitly include Vue/Web Component support and claim a 54 kB gzip
full core, with tree shaking available
([official overview and feature list](https://vidstack.io/docs/player/)). Its default layouts accept
thumbnail VTT directly ([default layout](https://vidstack.io/docs/player/components/layouts/default-layout/)),
and its player API exposes `currentTime`, `duration`, `paused` and the normal media controls
([player API](https://vidstack.io/docs/player/components/core/player/)). It is MIT-licensed
([repository](https://github.com/vidstack/player)).

Vidstack was selected because the dashboard wants the complete prebuilt layout with menus,
gestures, responsive small-container behavior, and provider state. A real dashboard smoke test
confirmed that its native provider can load the repository's progressive MP4, render the Default
Layout in Dockview, and advance playback after clicking its play control.

### Plyr

Plyr progressively enhances a native media element and provides a standardized API with
`play()`, `pause`, `currentTime`, `duration`, seek events and `timeupdate`. It supports custom
control markup, CSS variables, keyboard shortcuts and preview thumbnails
([official repository and API](https://github.com/sampotts/plyr)). That makes Vue integration
straightforward through an element ref and mount/unmount lifecycle, but it is a finished player
widget more than a set of independently composable controls.

Plyr is MIT-licensed and released
[v3.8.4 on 2026-01-03](https://github.com/sampotts/plyr/releases/tag/v3.8.4). It is a reasonable
fallback when "good conventional player with minimal design work" matters more than matching the
dashboard's editor UI.

### Video.js

Video.js v8 is the most established option here: a full HTML5 player, common formats including
HLS/DASH, a component system, and a large plugin ecosystem. Its stable line is Apache-2.0 and
released [v8.23.9 on 2026-06-26](https://github.com/videojs/video.js/releases/tag/v8.23.9).
That maturity is valuable for general-purpose streaming products but is unnecessary weight for a
single HTTP MP4 preview.

Video.js v10 is architecturally more relevant: it separates state, UI and media, provides packaged
or ejectable skins, and has first-class slider and thumbnail components
([v10 skins](https://videojs.org/docs/framework/html/concepts/skins),
[thumbnail component](https://videojs.org/docs/framework/html/reference/thumbnail)). Its
accessibility documentation covers WAI-ARIA 1.2, WCAG 2.2, keyboard input, focus, touch targets,
contrast and reduced motion
([accessibility](https://videojs.org/docs/framework/react/concepts/accessibility)). However, the
maintainers still describe v10 APIs as unstable and not ready for a major production migration
([official beta announcement](https://videojs.org/blog/videojs-v10-beta-hello-world-again)); the
2026-07-07 release was still beta.25
([changelog](https://videojs.org/changelog)). Revisit it after GA rather than adopting it now.

### ArtPlayer

ArtPlayer is a polished vanilla-JS HTML5 player with a documented Vue wrapper recipe. It supports
custom controls, arbitrary DOM layers for metadata overlays, `play()`, `pause()`, `seek`,
`currentTime`, standard media events, and thumbnail plugins
([Vue integration](https://artplayer.org/document/en/),
[instance API](https://artplayer.org/document/en/advanced/property),
[controls](https://artplayer.org/document/en/component/controls),
[official plugin list](https://github.com/zhw2590582/ArtPlayer#plugins)). It even documents a
MediaBunny proxy, but the integration remains an imperative ArtPlayer instance rather than Vue
components.

ArtPlayer is MIT-licensed and released
[v5.4.0 on 2026-03-13](https://github.com/zhw2590582/ArtPlayer/releases/tag/5.4.0). Its official
materials do not make accessibility claims comparable to Vidstack, Media Chrome, or Video.js, so it
should not be selected on appearance alone.

## Playback and Minecraft tick synchronization

The preview should treat the media element as the master clock:

1. `video.play()` and `video.pause()` are the only playback commands.
2. A preview seek sets `video.currentTime = tick / 20`; changing `currentTime` is the standard way
   to seek an HTML media element ([HTMLMediaElement currentTime](https://developer.mozilla.org/en-US/docs/Web/API/HTMLMediaElement/currentTime)).
3. While playing, derive the preview tick from Vidstack's media `time-update` event instead of an
   application-owned animation clock. If later overlays require compositor-exact frame identity,
   upgrade that boundary to `requestVideoFrameCallback()`
   ([HTMLVideoElement API](https://developer.mozilla.org/en-US/docs/Web/API/HTMLVideoElement)).
4. Publish `floor(mediaTime * 20)` or the artifact's documented time-to-tick mapping as preview
   state. Inputs and metadata overlays read this preview tick. Do not push it back into the dataset
   timeline's selection/playback controller.
5. On a paused seek, update overlay state after `seeked`; if exact frame identity matters, verify it
   against the encoded 20 fps artifact and retain MediaBunny only as a paused-frame fallback.

Native progressive MP4 seeking is compatible with this repository. HTTP range requests are what
allow media clients to obtain only the needed part of a large file
([HTTP range requests](https://developer.mozilla.org/en-US/docs/Web/HTTP/Guides/Range_requests)),
and the asset server already has a regression test for `206 Partial Content` plus `Content-Range`
([asset range test](../../internal/grpc/servers/middlewares/assets_test.go)). CORS also exposes the
range headers needed by browser clients
([server configuration](../../internal/grpc/servers/server.go)). Actual H.264 playback remains a
browser codec capability, so keep the existing validation/error surface and test the project's
real rendered artifacts in every supported browser.

## Implemented slice

The dashboard implementation now:

1. uses Vidstack's native video provider and Default Video Layout;
2. registers the player, layout, and UI entry points separately, as required by Vidstack's package;
3. derives the shared Preview/Inputs tick from the real media clock;
4. unbinds the previous player when the Preview component is destroyed;
5. leaves the dataset Timeline's playback controller independent;
6. keeps resource selection explicit instead of auto-selecting the first replay.

If real artifacts later expose a frame-accuracy problem, first add a paused-frame or
`requestVideoFrameCallback()` synchronization path around the native video. A custom MediaBunny
provider should remain a fallback rather than returning to hand-built playback controls.
