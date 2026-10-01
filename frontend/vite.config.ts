import react from '@vitejs/plugin-react'
import { defineConfig } from 'vite'

// Library chunks are named by what they are so each one caches on its own:
// an app-code change re-downloads only the (small) page chunks, never the
// libraries. The two chart libraries get their own chunks and are only
// imported by the pages that draw charts, so a page without a chart never
// fetches either one (the Dashboard, Analysis, Trade Plans and Portfolio
// pages draw candlesticks; Portfolio and Analysis also use Chart.js).
const inPackage = (...names: string[]) => (id: string) => {
  const path = id.replaceAll('\\', '/')
  return names.some((name) => path.includes(`/node_modules/${name}/`))
}

// React and the router come first with a higher priority: the chart wrappers
// import React, and without this the bundler pulls React's own modules into a
// chart chunk, which would then load on every page.
const VENDOR_GROUPS = [
  {
    name: 'vendor-react',
    priority: 3,
    test: inPackage('react', 'react-dom', 'react-router', 'react-router-dom', 'scheduler', 'cookie', 'set-cookie-parser'),
  },
  { name: 'vendor-react-query', priority: 2, test: inPackage('@tanstack/react-query', '@tanstack/query-core') },
  { name: 'vendor-lightweight-charts', priority: 1, test: inPackage('lightweight-charts', 'fancy-canvas') },
  { name: 'vendor-chartjs', priority: 1, test: inPackage('chart.js', 'react-chartjs-2', '@kurkle/color') },
]

// https://vite.dev/config/
export default defineConfig({
  plugins: [react()],
  build: {
    rolldownOptions: {
      output: {
        codeSplitting: { groups: VENDOR_GROUPS },
      },
    },
  },
})
