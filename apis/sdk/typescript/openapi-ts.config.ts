import { defineConfig } from '@hey-api/openapi-ts'

// NOTICE: The checked-in OpenAPI document is the source for this generated fetch client. The
// configuration shape follows `https://github.com/hey-api/openapi-ts/blob/c9dc0b94b0bfd53b8614e31f450607e1db4d3c05/packages/openapi-ts/README.md#L293-L297`.
export default defineConfig({
  input: '../../openapi/v3/recorder-minecraft.openapi.yaml',
  output: {
    path: 'src',
  },
  plugins: [
    '@hey-api/typescript',
    {
      name: '@hey-api/client-fetch',
      throwOnError: true,
    },
    '@hey-api/sdk',
  ],
})
