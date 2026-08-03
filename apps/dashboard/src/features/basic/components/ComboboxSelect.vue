<script setup lang="ts" generic="T extends AcceptableValue">
import type { AcceptableValue } from 'reka-ui'

import {
  ComboboxAnchor,
  ComboboxContent,
  ComboboxEmpty,
  ComboboxInput,
  ComboboxItem,
  ComboboxItemIndicator,
  ComboboxPortal,
  ComboboxRoot,
  ComboboxTrigger,
  ComboboxViewport,
} from 'reka-ui'

export interface ComboboxSelectOption<T extends AcceptableValue> {
  description?: string
  disabled?: boolean
  icon?: string
  label: string
  value: T
}

const props = withDefaults(defineProps<{
  disabled?: boolean
  emptyLabel?: string
  label: string
  options: ComboboxSelectOption<T>[]
  placeholder?: string
}>(), {
  disabled: false,
  emptyLabel: 'No matching options',
})

const model = defineModel<T>()

function displayValue(value: T): string {
  return props.options.find(option => option.value === value)?.label ?? props.placeholder ?? ''
}
</script>

<template>
  <!-- NOTICE: This props/v-model/option-slot API follows Airi's Reka UI combobox wrapper without
       copying its visual design. See
       `https://github.com/moeru-ai/airi/blob/d5a241b10e72717119b6a804b2a64d8e958b59e5/packages/ui/src/components/form/combobox/combobox.vue#L23-L61`
       and
       `https://github.com/moeru-ai/airi/blob/d5a241b10e72717119b6a804b2a64d8e958b59e5/packages/ui/src/components/form/combobox-select/combobox-select.vue#L27-L53`. -->
  <ComboboxRoot v-model="model" :disabled="disabled" open-on-click class="relative w-full">
    <ComboboxAnchor class="hover:bg-neutral-750 h-9 w-full flex items-center gap-2 border border-white/10 rounded-md bg-neutral-800 px-2.5 text-sm text-neutral-200 transition-colors data-[disabled]:cursor-not-allowed focus-within:border-white/25 data-[disabled]:opacity-45">
      <ComboboxInput
        :aria-label="label"
        class="min-w-0 flex-1 border-0 bg-transparent p-0 text-sm text-neutral-200 outline-none placeholder:text-neutral-500"
        :disabled="disabled"
        :display-value="displayValue"
        :placeholder="placeholder"
      />
      <ComboboxTrigger :aria-label="`Open ${label}`" class="shrink-0 border-0 bg-transparent p-0 text-neutral-500 hover:text-neutral-200" :title="`Open ${label}`">
        <span aria-hidden="true" class="i-mingcute-down-line block text-base transition-transform data-[state=open]:rotate-180" />
      </ComboboxTrigger>
    </ComboboxAnchor>

    <ComboboxPortal>
      <ComboboxContent
        align="start"
        class="bg-neutral-850 z-10000 max-h-72 min-w-[var(--reka-combobox-trigger-width)] overflow-hidden border border-white/12 rounded-md shadow-black/40 shadow-xl"
        position="popper"
        :side-offset="4"
      >
        <ComboboxViewport class="max-h-72 overflow-y-auto p-1">
          <ComboboxEmpty class="px-3 py-4 text-center text-xs text-neutral-500">
            {{ emptyLabel }}
          </ComboboxEmpty>
          <ComboboxItem
            v-for="option in options"
            :key="String(option.value)"
            class="relative grid grid-cols-[1.25rem_minmax(0,1fr)] min-h-10 cursor-pointer select-none items-center gap-2 rounded px-2 py-1.5 text-sm text-neutral-300 outline-none data-[disabled]:pointer-events-none data-[highlighted]:bg-white/8 data-[highlighted]:text-white data-[disabled]:opacity-35"
            :disabled="option.disabled"
            :text-value="option.label"
            :value="option.value"
          >
            <ComboboxItemIndicator class="flex items-center justify-center text-emerald-300">
              <span aria-hidden="true" class="i-mingcute-check-line text-sm" />
            </ComboboxItemIndicator>
            <slot name="option" :option="option">
              <span class="min-w-0 flex items-center gap-2">
                <span v-if="option.icon" aria-hidden="true" class="shrink-0 text-base" :class="option.icon" />
                <span class="min-w-0 flex flex-col">
                  <span class="truncate">{{ option.label }}</span>
                  <span v-if="option.description" class="truncate text-xs text-neutral-500">{{ option.description }}</span>
                </span>
              </span>
            </slot>
          </ComboboxItem>
        </ComboboxViewport>
      </ComboboxContent>
    </ComboboxPortal>
  </ComboboxRoot>
</template>
