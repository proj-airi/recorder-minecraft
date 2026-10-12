<script setup lang="ts">
withDefaults(defineProps<{
  /** Lit and sunk, for a key the recording shows held. */
  pressed?: boolean
  /** `hint` is an inline shortcut label; `cap` is a square keycap; `bar` is a short, wide cap. */
  size?: 'bar' | 'cap' | 'hint'
}>(), { pressed: false, size: 'hint' })
</script>

<template>
  <!--
    Raised keycap: the face sits on a darker skirt drawn as a bottom shadow, and a pressed key sinks
    by the skirt height and lights up. The skirt is a shadow, not a border, so pressing a cap does
    not shift the layout.
  -->
  <kbd
    class="transition-[background,box-shadow,top] relative inline-flex select-none items-center gap-[3px] font-semibold font-sans duration-60 ease-out"
    :class="[
      {
        bar: 'h-[1.1rem] min-w-[3.75rem] justify-start rounded-md px-[5px] text-[10px]',
        cap: 'h-[1.55rem] min-w-[1.55rem] justify-center rounded-md px-[5px] text-[11px]',
        hint: 'h-4 min-w-4 justify-center rounded-[3px] px-1 text-[10px]',
      }[size],
      pressed
        ? 'top-px bg-amber-300 text-neutral-900 shadow-[0_0_8px_rgb(252_211_77/35%)]'
        : '-top-px bg-[#3a3a3a] text-[#c8c8c8] shadow-[0_2px_0_0_#1e1e1e,inset_0_1px_0_rgb(255_255_255/7%)]',
    ]"
  >
    <slot />
  </kbd>
</template>
