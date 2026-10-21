import React from 'react'
import ReactDOM from 'react-dom/client'
import App from './App'
import './base.css'
import { loadDemo } from './demoHooks'

async function boot() {
  // Demo build only: __DEMO__ is a build-time constant, so the branch (and the
  // demo modules) are dropped from the normal bundle.
  if (__DEMO__) {
    const { installDemo } = await loadDemo('install.js')
    await installDemo()
  }
  ReactDOM.createRoot(document.getElementById('root')).render(
    <React.StrictMode>
      <App />
    </React.StrictMode>,
  )
}

boot()
