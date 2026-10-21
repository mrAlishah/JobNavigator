// Inert hooks for the public demo (https://github.com/vesaias/JobNavigator-demo).
//
// The demo is a build mode: with VITE_DEMO=1 the app answers every /api call from a small in-browser
// server. That layer (sample data, local server, PDF) lives in the separate demo repo and is copied
// into src/demo/ only when the demo is built. Nothing here imports it statically: `layer` is an empty
// map when the folder is absent, and in the normal build (`__DEMO__` false) the glob is dead code, so
// the demo layer never reaches a normal bundle.
export const IS_DEMO = typeof __DEMO__ !== 'undefined' && __DEMO__ === true
export const ROUTER_BASENAME = IS_DEMO ? '/JobNavigator-demo' : undefined

const layer = typeof __DEMO__ !== 'undefined' && __DEMO__ ? import.meta.glob('./demo/*.{js,jsx}') : {}

/** Loads a module of the demo layer by file name, e.g. loadDemo('pdf.js'). Rejects when the layer is absent. */
export function loadDemo(name) {
  const load = layer[`./demo/${name}`]
  return load ? load() : Promise.reject(new Error(`demo layer module ${name} is not available in this build`))
}
