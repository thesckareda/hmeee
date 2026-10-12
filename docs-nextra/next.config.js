const withNextra = require('nextra')({
  theme: 'nextra-theme-docs',
  themeConfig: './theme.config.jsx',
})

module.exports = withNextra({
  reactStrictMode: true,
  basePath: '/docs',
  i18n: { locales: ['en'], defaultLocale: 'en' },
})
