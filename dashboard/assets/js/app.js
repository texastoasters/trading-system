// app.js — Phoenix LiveView entry point

import {Socket} from "phoenix"
import {LiveSocket} from "phoenix_live_view"

// ── LiveSocket setup ─────────────────────────────────────────────────────────

let csrfToken = document.querySelector("meta[name='csrf-token']").getAttribute("content")

let liveSocket = new LiveSocket("/live", Socket, {
  longPollFallbackMs: 2500,
  params: {_csrf_token: csrfToken},
  hooks: {}
})

liveSocket.connect()
window.liveSocket = liveSocket
