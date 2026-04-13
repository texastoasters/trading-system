// app.js — Phoenix LiveView entry point

import {Socket} from "phoenix"
import {LiveSocket} from "phoenix_live_view"

// ── LiveSocket setup ─────────────────────────────────────────────────────────

let csrfToken = document.querySelector("meta[name='csrf-token']").getAttribute("content")

let Hooks = {}

Hooks.ScrollBottom = {
  mounted() { this.el.scrollTop = this.el.scrollHeight },
  updated() { this.el.scrollTop = this.el.scrollHeight }
}

let liveSocket = new LiveSocket("/live", Socket, {
  longPollFallbackMs: 2500,
  params: {_csrf_token: csrfToken},
  hooks: Hooks
})

liveSocket.connect()
window.liveSocket = liveSocket
