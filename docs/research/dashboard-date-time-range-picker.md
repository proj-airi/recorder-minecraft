# Dashboard recording date/time range picker research

Research snapshot: 2026-08-03.

## Revised recommendation

The resource browser does not primarily need another form-style date-time input. It needs a
**recording calendar**: users should see which days contain replays before choosing a day or a
range. That changes the recommendation.

Build a compact `RecordingCalendarFilter` on **Ark UI Date Picker** and style its headless parts
with UnoCSS. Ark supports range selection, presets, one or more months, a declared time zone,
custom parsing, an inline form, and a documented `setTime(time, index)` operation for setting the
time on either range endpoint. Its cells are composable parts with stable `data-*` state, and its
official keyboard contract covers arrows, Home/End, PageUp/PageDown, Enter and Escape
([Ark UI Date Picker](https://ark-ui.com/docs/components/date-picker)). Ark explicitly supports
Vue through `@ark-ui/vue`, is unstyled, and recommends component subpath imports such as
`@ark-ui/vue/dialog`; the same import shape is available for its date picker
([Ark UI repository](https://github.com/chakra-ui/ark)).

The UI should be an always-visible one-month calendar when the Resources pane is wide enough, or
the same calendar in a popover behind a compact range-summary button when the pane is narrow. Each
day cell should contain:

- the day number;
- a green recording marker when at least one replay exists;
- a small count for `2+` replays, or alternatively a three-step green intensity based on count;
- the normal range selection background, kept visually separate from recording availability;
- an accessible label such as “July 28, 3 recordings”.

Below the calendar, use two explicit rows, `From` and `To`, with compact `HH:mm:ss` fields and a
visible `UTC` label. Calendar selection changes civil dates; the time rows set exact endpoints.
Keep the application contract half-open: `from <= started_at < to`. Presets should include
`All recordings`, `Today`, `Last 7 days`, and `This month`. A single-day selection should default
to `[day 00:00:00, next day 00:00:00)`, which is easier to understand than asking users to enter
`23:59:59.999`.

This is more implementation than swapping one pre-styled picker, but it produces the exact compact
editor UX, keeps styling in UnoCSS, and avoids coupling the dashboard to an unrelated design
system. Before adoption, make a small production-build spike and inspect the emitted chunk: npm's
unpacked package size is not a browser bundle measurement, and Ark exposes subpath imports even
though the package manifest declares many Zag state-machine modules
([npm package metadata](https://registry.npmjs.org/@ark-ui/vue/5.37.2)).

## Why not simply use VCalendar?

VCalendar has the best ready-made data-marking API in this comparison. Its `attributes` attach
content, highlights, dots, bars, popovers, ordering and arbitrary `customData` to a date, date range
or date pattern. Dots accept custom classes/styles, and a scoped day popover receives both the
day and all matching attributes
([VCalendar attributes](https://vcalendar.io/calendar/attributes)). It also supports a date-time
range model and an explicit `timezone="UTC"`
([VCalendar time zones](https://vcalendar.io/i18n/timezones)). If maintenance status were equal,
this would be the first choice.

It is not equal: the Vue 3 installation still uses `v-calendar@next`, requires Vue 3.2 and
Popper, and documents version 3.1.2
([installation](https://vcalendar.io/getting-started/installation.html)). The npm record shows that
3.1.2 was published on 2023-10-13 and depends on older Date-fns, Date-fns-tz, Lodash and
`vue-screen-utils`
([npm package metadata](https://registry.npmjs.org/v-calendar/3.1.2)). Its uncompressed npm
package is also about 9.8 MiB; that is a distribution-size signal, not a runtime bundle size.

Use VCalendar's attribute model as the behavior reference, but do not add its currently dormant
Vue 3 line as a new production foundation. If a fast visual prototype is useful, it is still the
shortest route to validating dots versus counts versus a heat treatment.

## Candidate comparison

The highest-weight column is recording-day visualization, not whether the component can merely
return two dates.

| Candidate | Recording-day cells | Range + time / zone | Accessibility and keyboard | Visual fit and dependency cost | Decision |
| --- | --- | --- | --- | --- | --- |
| **Ark UI Date Picker** | Headless cell parts and state attributes allow the day number, count, dot and heat level to be rendered together. The API includes range mode and per-date cell state. | Range selection, presets, `CalendarDateTime`, `timeZone`, `setTime(time, index)` and a “With Time” example are documented. | Official keyboard table covers grid navigation and dismissal; the project describes the primitives as accessibility-first. | Fully unstyled and compatible with utility CSS. Import the date-picker subpath, then verify the actual chunk. | **Recommended.** Best combination of maintained behavior and owned rendering. |
| **VueDatePicker v14** | Already supports `markers` with dot/line, tooltips and custom positions; `#day`, `#marker`, and `#marker-tooltip` slots can render counts or custom marks ([markers](https://vue3datepicker.com/props/general-configuration/), [content slots](https://vue3datepicker.com/slots/content/)). | Native range, seconds, 24-hour mode, timestamp model and UTC conversion are all documented ([range](https://vue3datepicker.com/props/modes-configuration/), [time](https://vue3datepicker.com/props/time-picker-configuration/), [time zone](https://vue3datepicker.com/props/timezone/)). | Escape, space confirmation and arrow navigation are configurable; ARIA labels are configurable, but the documentation is less explicit than Ark or PrimeVue ([general configuration](https://vue3datepicker.com/props/general-configuration/), [localization](https://vue3datepicker.com/props/localization/)). | Smallest migration and about 231 KiB unpacked, but it retains an opinionated calendar structure and stylesheet despite extensive CSS variables ([theming](https://vue3datepicker.com/customization/theming/), [npm metadata](https://registry.npmjs.org/@vuepic/vue-datepicker/14.0.0)). | **Lowest-effort fallback.** Add markers first if the objection is only missing recording indicators; replace it if the objection is the overall interaction and visual structure. |
| **PrimeVue DatePicker** | A `#date` slot replaces date-cell content, so a dot/count is possible ([DatePicker](https://primevue.org/datepicker/)). | Range mode and time UI are built in, including 12/24-hour modes. No first-class time-zone prop is documented, so UTC normalization remains application-owned. | Strongest pre-styled option: official combobox/dialog/grid semantics, live announcements, focus behavior and detailed key bindings are documented. | Styled themes, unstyled mode, design tokens and pass-through APIs are supported ([PrimeVue introduction](https://primevue.org/introduction/)). The full npm distribution is about 8.9 MiB and brings PrimeVue core/theme machinery, although component imports are modular ([npm metadata](https://registry.npmjs.org/primevue/5.0.0)). | **Best packaged alternative**, especially if the dashboard plans to adopt more PrimeVue controls; disproportionate for one filter today. |
| **Element Plus DatePicker** | The default scoped slot and `cell-class-name` support custom day content/markers ([DatePicker](https://element-plus.org/en-US/component/date-picker.html)). | `type="datetimerange"`, shortcuts, independent panels, display/model formats and endpoint default times are first class ([DateTimePicker](https://element-plus.org/en-US/component/datetime-picker.html)). No time-zone prop is documented. | The component page does not provide the detailed DatePicker keyboard/screen-reader contract that PrimeVue and the headless candidates do. | Dark mode is CSS-variable based; introducing the suite and its theme for one picker is heavy. The npm distribution is about 41 MiB unpacked and declares Day.js, Floating UI, VueUse and Lodash-family dependencies ([dark mode](https://element-plus.org/en-US/guide/dark-mode), [npm metadata](https://registry.npmjs.org/element-plus/2.14.3)). | Capable, but not aligned with this UnoCSS-owned editor unless Element Plus becomes an application-wide choice. |
| **Naive UI DatePicker** | Its DatePicker implementation can be themed and extended, but it has no VCalendar-like declarative date-attribute model; custom density cells require deeper adaptation than VueDatePicker/PrimeVue/Ark. | `datetimerange` exists and uses millisecond timestamps; no first-class time-zone prop is documented ([DatePicker source](https://github.com/tusen-ai/naive-ui/tree/main/src/date-picker), [range discussion](https://github.com/tusen-ai/naive-ui/issues/4448)). | No equally detailed official DatePicker-specific keyboard/screen-reader contract was found. | No stylesheet import, tree-shakeable components and type-safe JS theme overrides are stated goals ([repository](https://github.com/tusen-ai/naive-ui)). The full npm distribution is about 34.5 MiB unpacked; actual imported output may be much smaller ([npm metadata](https://registry.npmjs.org/naive-ui/2.44.1)). | Better visual fit than Element Plus, but weaker evidence for the most important custom-cell and accessibility requirements. |
| **Reka UI Range Calendar / Date Range Picker** | `RangeCalendarCellTrigger` is fully composable and exposes selected, today, unavailable, highlighted and range-edge state, making custom markers straightforward ([Range Calendar](https://www.reka-ui.com/docs/components/range-calendar)). | Its Date Range Picker supports date-time values, second-level granularity, 12/24-hour cycles, locale and a time-zone segment through `@internationalized/date` ([Date Range Picker](https://www.reka-ui.com/docs/components/date-range-picker)). | Full keyboard navigation, segmented input behavior and focus management are explicitly documented. | Headless and a natural UnoCSS fit, but `Range Calendar`, `Date Range Picker`, and related time fields are all explicitly marked **Alpha**. | Excellent API reference; defer production adoption until the date primitives leave Alpha. |
| **VCalendar v3** | Best declarative dots/bars/highlights/popovers/custom-data API in the field. | Range, date-time and UTC/IANA time-zone display are supported. | Official current docs do not provide a comparably complete keyboard/a11y contract. | Built-in CSS is customizable, but the Vue 3 release line has not shipped since October 2023 and remains on the `next` npm tag. | Great prototype/reference, unacceptable maintenance posture for a new foundation. |
| **Vue Cal** | Can show events and counts in month views, but that is a scheduling-calendar model rather than a focused availability marker model. | It is not a compact date-time range control. v5 documentation says stable is still forthcoming on `vue-cal@next`, while advertising future/ongoing time-zone work ([v5 docs](https://antoniandre.github.io/vue-cal/)). | No compact range-picker-specific keyboard/screen-reader contract is documented. | CSS/BEM customization and no dependencies are positives, but the surface includes event scheduling, week/day views and editing behavior that this filter does not need. v4 is the former Vue 3 line ([v4 docs](https://antoniandre.github.io/vue-cal-v4/)). | Reject for this task; it solves a larger, different calendar problem. |

## Proposed data and interaction contract

Aggregate visible replays once after the server/player filter, using a UTC civil-day key such as
`YYYY-MM-DD`. The calendar should consume a small immutable map rather than scan the replay list
for every rendered cell:

```ts
interface RecordingDay {
  count: number
  day: string
  firstStartedAt: number
  lastStartedAt: number
}
```

The marker has three independent states:

1. **Availability**: green dot/count means recordings exist. Never use selection yellow for this.
2. **Selection**: the range start/end and interior use the editor's selection accent.
3. **Focus/today**: retain a visible keyboard focus ring and a neutral today outline.

Do not encode count using color alone. Keep a dot or number and expose the count in the cell's
accessible name. A tooltip may list the first few replay start times, but it must be supplementary;
the marker and accessible label should work without hover.

Click behavior should be progressive:

- first click selects a complete single-day half-open interval;
- Shift-click or a range-selection mode extends to a second date;
- changing either `From`/`To` time refines the selected endpoints;
- `Escape` cancels an uncommitted range in a popup, while `Clear` restores “All recordings”;
- changing server or player recomputes markers, but preserves the range unless it contains no
  matching recordings; in that case show an empty result rather than silently changing dates.

## Suggested implementation sequence

1. Before changing dependencies, add recording markers to the current VueDatePicker using its
   documented `markers` prop. This answers whether a dot, count or heat treatment communicates the
   data best with very little code.
2. Build an isolated Ark UI `RecordingCalendarFilter` spike with one month, UTC, day counts,
   keyboard selection and separate time rows. Measure its production chunk and test it in a narrow
   Dockview pane.
3. Use agent-browser plus a keyboard-only pass to verify month navigation, range selection,
   focus return, day-count labels, dark-mode contrast and popover collision behavior.
4. Replace VueDatePicker only after the spike has equivalent UTC/half-open-range tests. Keep the
   filter adapter responsible for converting calendar values into server timestamps so no picker
   library's native `Date` conventions leak into the API model.

## Bundle-size note

The npm sizes above are `dist.unpackedSize` values from the first-party npm registry records. They
describe the installed package tarball after unpacking, not tree-shaken JavaScript, parsed code,
gzip transfer size or runtime cost. They are useful only for spotting ecosystem scope. The decision
must use a Vite production build with the exact import form used by the dashboard.
