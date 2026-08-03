# Dockview Vue panel labels and tab-strip sizing

Research snapshot: 2026-08-03. The dashboard pins `dockview-vue` 7.0.4, so source citations below use the corresponding `v7.0.4` commit.

## Answer

The visible label is **not required application data**, and its current size is **not fixed**:

- `api.addPanel()` requires only a unique `id` and a registered `component`. `title` and `tabComponent` are optional. When the default tab renderer is used without a `title`, Dockview displays the panel `id`, so omitting `title` alone does not produce a label-free tab ([official adding-panels guide](https://dockview.dev/docs/core/panels/add/), [v7.0.4 option types](https://github.com/mathuo/dockview/blob/08097bd22495af8db171698355dffde93b9f5a88/packages/dockview-core/src/dockview/options.ts#L561-L580), [v7.0.4 fallback implementation](https://github.com/mathuo/dockview/blob/08097bd22495af8db171698355dffde93b9f5a88/packages/dockview-core/src/dockview/dockviewComponent.ts#L4670-L4701)).
- A custom Vue tab component may render a shorter title, an icon, controls, or no visible text. Dockview explicitly documents custom tab renderers and says custom renderers may choose their own approach to the title ([official tabs guide](https://dockview.dev/docs/core/panels/tabs/), [official adding-panels guide](https://dockview.dev/docs/core/panels/add/)).
- The standard dark theme's tab-strip height is `35px`, set through `--dv-tabs-and-actions-container-height`; the Visual Studio theme demonstrates that the same variable can be `20px`. The tab-strip CSS consumes that variable as its height (or width for a vertical header), so it is a theme value rather than an invariant ([v7.0.4 theme defaults](https://github.com/mathuo/dockview/blob/08097bd22495af8db171698355dffde93b9f5a88/packages/dockview-core/src/theme.scss#L4-L20), [Visual Studio override](https://github.com/mathuo/dockview/blob/08097bd22495af8db171698355dffde93b9f5a88/packages/dockview-core/src/theme.scss#L126-L132), [tab-strip layout CSS](https://github.com/mathuo/dockview/blob/08097bd22495af8db171698355dffde93b9f5a88/packages/dockview-core/src/dockview/components/titlebar/tabsContainer.scss#L1-L7)).

The important distinction is that a **visible text label** is optional, and a group's **whole tab/header strip can also be hidden**. Dockview does not expose this as a top-level `DockviewOptions` prop; it is group state, set with `panel.group.header.hidden = true` ([official hidden-header guide](https://dockview.dev/docs/core/groups/hiddenHeader/)). This is preferable to hiding `.dv-tabs-and-actions-container` through application CSS.

## Relevant API surface

| Requirement | Dockview API | Effect |
| --- | --- | --- |
| Create a panel | `api.addPanel({ id, component, ... })` | Only `id` and `component` are required. |
| Set or update default label | `title`, then `panel.api.setTitle(...)` | Default renderer displays it; absent title falls back to `id`. |
| Own the tab's visible content | `tabComponent` plus Vue `tabComponents` | Replaces the default title/close rendering with an application Vue component. |
| Use one custom tab everywhere | Vue `defaultTabComponent` | Supplies a default application tab renderer instead of repeating `tabComponent`. The Vue adapter resolves registered tab components and otherwise falls back to Dockview's renderer ([Vue adapter source](https://github.com/mathuo/dockview/blob/08097bd22495af8db171698355dffde93b9f5a88/packages/dockview-vue/src/dockview/dockview.vue#L250-L274)). |
| Resize the strip | Override `--dv-tabs-and-actions-container-height` in the active/scoped theme | Controls horizontal strip height and vertical strip width. |
| Resize label text | `--dv-tabs-and-actions-container-font-size`, `--dv-tab-font-size`, or custom-tab CSS | Controls typography independently of strip height. |
| Make a sole tab fill its strip | `singleTabMode="fullwidth"` | Expands a one-tab group; it does not hide or compact the strip ([official tabs guide](https://dockview.dev/docs/core/panels/tabs/#full-width-tab)). |
| Move the header | `defaultHeaderPosition`; `panel.group.api.setHeaderPosition(...)` | Supports `top`, `bottom`, `left`, and `right`, not `none` ([official header-position guide](https://dockview.dev/docs/core/panels/headerPosition/)). |
| Hide one group's complete header | `panel.group.header.hidden = true` | Removes that group's complete tab/header strip through supported group state ([official hidden-header guide](https://dockview.dev/docs/core/groups/hiddenHeader/)). |
| Stop rearrangement | `disableDnd`, global `locked`, or group locking | Changes interaction; does not remove tabs or labels. |
| Remove overflow menu | `disableTabsOverflowList` | Removes only the overflow-list affordance; overflowing tabs become hidden ([official scrollbar guide](https://dockview.dev/docs/core/scrollbars/)). |
| Remove splitter borders | `hideBorders` | Removes group borders, not the tab strip ([official theming guide](https://dockview.dev/docs/core/theming/#hiding-borders)). |

## What the current dashboard does

Both dashboard Dockview instances pass `themeDark`, whose default tab strip is 35px. Every panel also passes a `title` and the same custom `TimelineDockTab` renderer ([workspace setup](../../apps/dashboard/src/features/editor/components/EditorWorkspace.vue), [timeline Dockview setup](../../apps/dashboard/src/features/timeline/components/TimelineDockLayout.vue)).

`TimelineDockTab` already removes Dockview's default close button and renders only `params.api.title`. Its `h-full` follows the parent strip, while `px-2` and `text-xs` control only its inner horizontal padding and font size ([custom tab](../../apps/dashboard/src/features/timeline/components/TimelineDockTab.vue)). Therefore the perceived large vertical label area comes primarily from the theme's 35px strip, not from a required panel-title size.

## Supported choices for this dashboard

1. **Compact the strip and keep labels.** Scope `--dv-tabs-and-actions-container-height` (for example, 22–24px) and the font-size variables to the dashboard Dockview. This preserves tab selection, drag handles, keyboard tab semantics, and future multi-panel groups.
2. **Keep the strip but simplify individual tabs.** Continue using a custom Vue tab renderer, replacing text with a compact icon or visually hidden accessible label. Dockview's outer tab remains the interaction and drop target.
3. **Hide the whole strip through group state.** Set `panel.group.header.hidden = true`. This removes the main panel drag/rearrange affordance and the visible selector when panels share a group, so use it only for groups that intentionally behave as fixed single-panel regions. If the entire workspace never allows tab stacks or docking, a plain split/grid layout may express that constraint more directly.

An empty custom renderer should not silently erase the accessible name. Dockview's outer wrapper is a `role="tab"` and wires tab/tabpanel accessibility, while the rendered content normally supplies its name ([v7.0.4 tab wrapper](https://github.com/mathuo/dockview/blob/08097bd22495af8db171698355dffde93b9f5a88/packages/dockview-core/src/dockview/components/tab/tab.ts#L75-L90), [default renderer](https://github.com/mathuo/dockview/blob/08097bd22495af8db171698355dffde93b9f5a88/packages/dockview-core/src/dockview/components/tab/defaultTab.ts#L16-L32)). If visible text is removed, the custom renderer should retain a visually hidden text label or otherwise provide an accessible name.
