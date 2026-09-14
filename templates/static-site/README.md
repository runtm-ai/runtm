# My Site - Static Site Template

A static site template built with Next.js for [Runtm](https://runtm.com). See the [template docs](https://docs.runtm.com/templates/static-site) for more details.

## What is a Static Site?

Fast, zero-backend pages served as files. Perfect for content that doesn't need server-side rendering.

**Use cases:**
- Marketing website
- Product landing page/waitlist
- Docs/changelog
- Status/press page

## Local Development

```bash
# Recommended: Use runtm run (auto-detects runtime and port)
runtm run

# Or manually:
npm install
npm run dev
```

### Other Commands

```bash
npm run build    # Build for production
npm run lint     # Lint code
npm run format   # Format code
```

## Deploy

Get your API key at [app.runtm.com](https://app.runtm.com) and deploy:

```bash
runtm login     # First time only
runtm validate
runtm deploy
```

You can set the tier in `runtm.yaml`:

```yaml
name: my-site
template: static-site
runtime: node
tier: starter  # starter, performance, pro
```

## Project Structure

```
app/
├── layout.tsx        # Root layout, metadata
├── page.tsx          # Homepage
├── globals.css       # Global styles
└── health/
    └── route.ts      # Health check endpoint

components/
├── layout/           # Layout components (header, footer)
├── sections/         # Page sections (hero, features, cta)
└── ui/               # Reusable UI components
```

## Customization

### Colors

Edit `tailwind.config.js` to customize the color palette:

```js
theme: {
  extend: {
    colors: {
      brand: {
        500: '#22c55e', // Your brand color
      },
    },
  },
},
```

Or update CSS variables in `app/globals.css`:

```css
:root {
  --accent: #22c55e;
}
```

### Typography

Fonts are configured in `app/layout.tsx`. Change the imported fonts to customize typography.

### Content

Edit the components in `components/sections/` to update content:
- `Hero.tsx` - Main headline and CTA
- `Features.tsx` - Feature cards
- `CTA.tsx` - Bottom call-to-action section

## Static Export

This template uses Next.js static export (`output: 'export'`). This means:
- No server-side code or API routes
- All pages are pre-rendered at build time
- Fast, secure, and easy to deploy
