// app.js — Phoenix LiveView entry point

import {Socket} from "phoenix"
import {LiveSocket} from "phoenix_live_view"

// CSRF token for LiveView WebSocket authentication
let csrfToken = document.querySelector("meta[name='csrf-token']").getAttribute("content")

let liveSocket = new LiveSocket("/live", Socket, {
  longPollFallbackMs: 2500,
  params: {_csrf_token: csrfToken}
})

// Connect and expose for debugging
liveSocket.connect()
window.liveSocket = liveSocket
