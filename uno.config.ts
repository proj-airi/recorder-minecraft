import type { WebFontMeta } from '@unocss/preset-web-fonts'

import { setDefaultAutoSelectFamilyAttemptTimeout } from 'node:net'

import {
  defineConfig,
  presetAttributify,
  presetIcons,
  presetTypography,
  presetWind3,
  transformerDirectives,
  transformerVariantGroup,
} from 'unocss'

setDefaultAutoSelectFamilyAttemptTimeout(1000)

export function presetWebFontsFonts(provider: 'fontsource' | 'none'): Record<string, (string | WebFontMeta)[] | string | WebFontMeta> {
  return {
    mono: {
      name: 'DM Mono',
      provider,
    },
    sans: {
      name: provider === 'fontsource' ? 'DM Sans' : 'DM Sans Variable',
      provider,
    },
  }
}

export function sharedUnoConfig() {
  return defineConfig({
    content: {
      pipeline: {
        exclude: [
          /\/node_modules\/(?!(?:\.pnpm\/@proj-airi\+ui@[^/]+\/node_modules\/@proj-airi\/ui|@proj-airi\/ui)\/)/, // DO NOT SCAN THE BLACK HOLE
          /\/dist\//,
        ],
        include: [
          /\.(vue|svelte|[jt]sx|mdx?|astro|elm|php|phtml|html)($|\?)/,
          'apps/**/*.{html,js,ts}',
        ],
      },
    },
    presets: [
      presetWind3(),
      presetAttributify(),
      presetTypography(),
      presetIcons({
        scale: 1.2,
      }),
    ],
    safelist: 'prose prose-sm m-auto text-left'.split(' '),
    transformers: [
      transformerDirectives({
        applyVariable: ['--at-apply'],
      }),
      transformerVariantGroup(),
    ],
  })
}

export default sharedUnoConfig()
