import { defineConfig } from 'vite';
import react from '@vitejs/plugin-react';
// Keep the previous hashed bundle while a new local build is published.  The
// desktop server may be rendering a long production in an already-open tab;
// deleting that tab's JS/CSS before index.html switches to the new bundle can
// leave it showing only the dark page background.  Release packaging may clean
// the directory before installation, but an in-place update remains live-safe.
export default defineConfig({ plugins: [react()], build: {outDir: '../dist', emptyOutDir: false} });
