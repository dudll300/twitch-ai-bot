# Windows studio: UI/UX contract

The current redesign targets Windows and retains PySide6 6.8.3 and all bot data formats.
The UI is Russian. Dark and light themes share semantic tokens in `studio_theme.py`.

## Navigation and hierarchy

- Work: Эфир, История.
- Bot: Поведение, Зрители, Самостоятельные реплики, Знания канала.
- Tools: Тестирование, Безопасность, Настройки.
- Настройки contains Подключение, Интерфейс and Справка.
- First setup is resumable and optional. It reuses the actual editor screens.
- Keep global Start/Stop visible. Stop ends the whole worker; disabling autonomous participation does not stop rewards.
- Show a primary task before secondary configuration. Put long help and diagnostics in disclosures.

## Shared visual rules

- Orbit uses bundled Nunito (OFL), with Cyrillic: 14 logical pixels/500 weight for body text, 13 for help, 17/700 for sections and 40/700 for page titles. Empty-state headings use 26/700 and supporting paragraphs 15/600.
- Spacing uses 4/8/12/16/24/32; card radius 16, field radius 14, pill buttons radius 22.
- Primary buttons use pale blue with dark ink. All colors come from semantic theme tokens.
- Orbit is the visual direction; the top bar displays the product name Twitch AI Bot. Interactive movement is finite and all motion respects the animation preference.
- Panels use a restrained diagonal gradient and a faint, continuous blue/violet halo. A cached 2x distance-field texture is stretched in nine slices, without stacked stroke bands or whole-widget graphics effects. Clip halos at scrolled edges; reserve canvas margins along visible panel edges and gaps. The compact icon rail opts out of these margins.
- Header hover uses continuous damped cursor parallax and a brighter halo; leaving or disabling motion resets it. Irregular stars cover the full header on every page, with independent sizes, brightness, phases and periods. Their timer stops when motion is disabled or the window is hidden/minimized. Captions change separately over the live sky in 220 ms: fade the old text out before introducing the new text, without superimposing the two titles. Restore native labels at rest; all other motion durations remain unchanged.
- Numeric fields have a single integrated step rail with a divider and two native hit areas. Empty error messages always collapse. Captions and collapsed disclosures retain their natural height; only editors and lists absorb extra vertical room.
- `studio_motion.SpringMotion` uses analytic critically damped motion on Qt's frame clock. Retargeting preserves position and velocity; all springs settle exactly and have a finite 720 ms tail limit. Native button press/hover, switches, checkboxes, rail selection and inner-tab indicators share it. Hidden controls and reduced motion apply their final state directly. A press released outside its hit area must still return to rest.
- Disclosures spring to their natural content height, including the top gap, while clipping a stable content layout. Rapid reversal retains velocity; width changes retarget the height. The enclosing scroll area follows the reveal geometry to bring the opening content into view, keeping the heading visible; collapse anchors the heading. Manual wheel, scrollbar or keyboard scrolling cancels this following. Clip card halos at the reveal boundary and invalidate the enclosing glow canvas throughout the movement to avoid trails. At rest, restore the original height constraints so wrapped text remains responsive. History context, request instructions and autonomous advanced settings use the same disclosure as the other secondary sections.
- Main page entrances last 480 ms, with quintic easing, short upward travel, a restrained scale from .985 to 1 and up to six top-level visible card layers in spatial order. Offsets are staggered within the same duration. History details use the same entrance; the row selection travels on the rail's continuous spring with per-pixel list scrolling. Keep the previous detail visible while its replacement loads in the background, disabling its copy actions until the selected record is ready. Unchanged history refreshes retain the existing rows and selection motion. Inner tabs, compact list/detail navigation, testing and behavior modes use a directional soft replacement over 360 ms with stationary panel chrome: only a 24px seam blends, avoiding doubled text and offset corner rims. Theme changes crossfade the actual old and new palettes over 360 ms, without deferring the preference or field changes.
- Behavior and testing mode controls use the same OrbitTabBar indicator as settings. `StudioStack` captures before mutation and retains a fixed viewport. Each behavior mode body scrolls independently; testing actions belong inside the comparison page so changing mode does not move the stack's bottom edge. Preserve the existing hidden combo state and cancellation semantics.
- Page snapshots remain opaque to avoid a dark dip at the midpoint. Capture outgoing tabs before Qt changes the native stack and keep that frame visible while the incoming layout settles. Glow canvases erase the dirty region before drawing translucent halos so scroll-copy, moving and hidden children cannot leave trails. Resize/hide cancels the visible overlay; allow pending captures to use the final layout after caption wrapping. Rapid navigation starts from the displayed composite. The next click, wheel or key exposes native controls before handling the input. Use scoped, transient child event filters, never an application-wide Python event filter. Bind deferred captures and retargets to their QObject context so destruction cancels them. Release snapshots and filters on completion; settled text must be native and sharp. Reduced motion immediately finishes all pending transitions and springs.
- Theme changes retain the shared selector tree and refresh cached palette brushes in parent order. Animation preferences never rebuild the application stylesheet. Verify actual painted colors after switching, not only the theme property.
- Shared StudioButton wraps long action captions and sizes their height; short chip labels stay on one line and retain their full natural minimum width. Dropdowns use opaque, padded rows with a selection mark and native keyboard behavior.
- Interface preferences expose theme and an animation toggle; do not expose animation durations.
- Panels share a separate title strip, body padding and dividers. History, profiles, knowledge and the stream conversation use native row delegates, not multiline text editors as lists.
- Navigation has a filled selected state without a light outline. Keyboard focus remains distinct from mouse selection.
- The wheel never edits combos, numbers or tab selections, even when they have focus. Open catalogs still scroll. Outer pages use a critically damped scroll motion with continuous velocity between wheel events; precision trackpad pixels remain direct. Manual scroll changes, hiding and reduced motion cancel the tail. Native scrollbars retain their hit areas with a slim painted thumb.
- Switches, checkboxes and buttons use antialiased floating-point geometry. Mouse selection has no extra focus outline; keyboard focus remains visible and stays inside the control bounds.
- Step controls and checkmarks are drawn explicitly and use the shared palette. All numeric editors use NumberInput.
- Text contrast target: at least 4.5:1. Important icons and focus indicators must remain visible in both themes.
- Status uses text plus color. A gray or green pill alone must not carry the meaning.
- Native drawn icons avoid missing glyphs. Use Qt palettes in custom-painted controls.
- Do not use real credentials, personal profiles or chat history for screenshots.

## Responsive behavior

- Default 1240x860; target minimum 800x480, constrained to available screen geometry.
- A 74-pixel icon rail remains scrollable and provides tooltips and accessible names at every width.
- Responsive forms use two columns from content width 900.
- MasterDetail switches to list/detail navigation below content width 760 or the combined readable minimum widths of its panes, whichever is larger. Recompute pane limits after layout changes; do not allow a splitter to crush filter labels. The list pane has a scrollable frame so short windows retain readable filters and pagination.
- Reparented panes must be explicitly shown on return to the wide layout.
- Hidden master/detail views defer responsive reparenting until show. A transition snapshot can deliver Qt's pending resize events to hidden descendants; those events must not rebuild the inactive pages.
- Long editors remain scrollable. Never squeeze their labels and fields to make a large form fit.
- Retain NoWheelComboBox and nested editor-to-page wheel handling.
- Master/detail cards have a 20-pixel splitter gap; verify pane bounds as well as the overall window bounds.

## Data and operations

- Draft, saved and used-by-worker are distinct states.
- Connection, behavior and viewers have independent saving and require restart.
- Saving new connection data during a run must not invalidate the running worker's tokens. Invalidate them before the next launch.
- Safety, participation and knowledge keep existing live-application semantics.
- Live toggles use saved values; they never apply unrelated drafts.
- All dirty editors participate in close protection, including autonomous instructions and numbers.
- Global save validates before writing. Files are individually atomic; partial success is reported honestly.
- UI preferences store only appearance, geometry, splitter sizes and setup progress.
- Worker state comes from versioned UI_EVENT data. Chat, EventSub and AI readiness are independent.
- Settings revisions are opaque hashes. Never emit prompt text, raw chat, credentials or raw provider errors in UI_EVENT.
- Cancellable operations retain old queues and cancellation objects in old workers. Late results cannot reach a new operation.
- Testing exposes an editable single-model selector above the question. Its default tracks the current model from settings; catalog loading is explicit. Checked comparison models override this selector and are listed beside it.
- The latest published reply on Эфир comes from the existing background history read. Previews and blocked replies never appear as published.
- The stream conversation shows existing reward questions and bot decisions from session history, not an invented full Twitch chat. Queue detail shows confirmed counts rather than fabricated viewers or question text.
- A configured legacy installation dismisses first-run onboarding regardless of the old setup flag. The setup card depends on saved or active connection values, never unsaved form contents.

## Verification

Run the existing unittest suite and GUI smoke checks, plus `tests/smoke_windows_studio.py`, `tests/smoke_orbit.py`, `tests/smoke_orbit_fidelity.py`, `tests/smoke_orbit_polish.py`, `tests/smoke_orbit_soft.py`, `tests/smoke_orbit_motion.py`, `tests/smoke_orbit_cinema.py`, `tests/smoke_orbit_refinements.py` and `tests/smoke_orbit_follow.py`. Capture settled screens after motion completion. The cinema check exercises velocity continuity, rapid navigation, native input during transitions, press cancellation, disclosure resize, reduced motion and cleanup in both themes/layouts. The refinements check verifies non-overlapping title pixels, readable squeezed filters, stable mode viewports and clipped disclosure halos. The follow check exercises opening content near the viewport edge, anchored collapse, manual scroll takeover, delayed history reads, continuous row selection, unchanged refreshes and outgoing-frame retention during rapid tab changes.
Use `--screenshots` to capture every page in both themes at wide and compact sizes.
Use QT_SCALE_FACTOR 1, 1.25 and 1.5 in separate processes for DPI checks.
Windows packaged checks use `tests/smoke_package.py`; TWITCH_AI_TEST_EXE can select a candidate EXE.

Before a public release, conduct five-user onboarding/usability trials. Local deterministic
tests do not establish that novice users can finish those tasks without help.
