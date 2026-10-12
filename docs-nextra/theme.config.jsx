export default {
  logo: <span style={{ fontWeight: 'bold' }}>Speaksy Docs</span>,
  project: {
    link: 'https://github.com/sushrutdeshpande0202/speaksy',
  },
  chat: {
    link: 'https://discord.gg/speaksy',
  },
  docsRepositoryBase: 'https://github.com/sushrutdeshpande0202/speaksy/tree/main/docs-nextra/pages',
  footer: {
    text: 'Speaksy © 2026 · Conversational AI Platform',
  },
  feedback: {
    content: 'Question? Give us feedback →',
  },
  useNextSeoProps() {
    return { titleTemplate: '%s – Speaksy Docs' }
  },
}
