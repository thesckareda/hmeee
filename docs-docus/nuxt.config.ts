export default defineNuxtConfig({
  extends: 'docus-theme',
  modules: [
    '@nuxt/content',
    '@nuxtjs/color-mode',
    '@nuxtjs/tailwindcss',
  ],
  content: {
    sources: {
      content: {
        driver: 'fs',
        base: './content',
      },
    },
  },
  colorMode: {
    preference: 'system',
    fallback: 'dark',
    classSuffix: '',
  },
  app: {
    baseURL: '/docs/',
  },
})
