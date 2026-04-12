// app.js — Phoenix LiveView entry point

import {Socket} from "phoenix"
import {LiveSocket} from "phoenix_live_view"
import Chart from "../vendor/chart.js"

// ── EquityChart hook ─────────────────────────────────────────────────────────
//
// Expects a <canvas> element with:
//   data-points  JSON array of {date, ending_equity, peak_equity, drawdown_pct}
//
// Renders:
//   - Blue equity line (ending_equity)
//   - Gray dashed peak line (peak_equity)
//   - Red fill between equity and peak (drawdown shading)
//   - Three horizontal CB threshold lines (10% / 15% / 20% below max peak)
//   - Hover tooltip showing Date / Equity / Peak / Drawdown%

const EquityChart = {
  mounted() { this._render() },
  updated() { this._render() },

  _render() {
    const raw = JSON.parse(this.el.dataset.points || "[]")
    if (raw.length < 2) return

    const labels = raw.map(p => p.date)
    const equity = raw.map(p => parseFloat(p.ending_equity) || 0)
    const peak   = raw.map(p => parseFloat(p.peak_equity)   || 0)

    const maxPeak = Math.max(...peak)
    const cbCaution    = maxPeak * 0.90
    const cbDefensive  = maxPeak * 0.85
    const cbHalt       = maxPeak * 0.80

    const cbLine = (value, color, label) => ({
      label,
      data: raw.map(() => value),
      borderColor: color,
      borderWidth: 0.8,
      borderDash: [4, 6],
      pointRadius: 0,
      fill: false,
      tension: 0,
      tooltip: { enabled: false }
    })

    const datasets = [
      {
        label: "Equity",
        data: equity,
        borderColor: "#3b82f6",
        borderWidth: 2,
        pointRadius: 0,
        tension: 0.2,
        fill: { target: 1, above: "rgba(239,68,68,0.12)" }
      },
      {
        label: "Peak",
        data: peak,
        borderColor: "#6b7280",
        borderWidth: 1,
        borderDash: [4, 4],
        pointRadius: 0,
        tension: 0,
        fill: false
      },
      cbLine(cbCaution,   "#fbbf24", "10% caution"),
      cbLine(cbDefensive, "#f97316", "15% halt T2"),
      cbLine(cbHalt,      "#ef4444", "20% halt all")
    ]

    const config = {
      type: "line",
      data: { labels, datasets },
      options: {
        responsive: true,
        maintainAspectRatio: false,
        animation: false,
        interaction: { mode: "index", intersect: false },
        plugins: {
          legend: { display: false },
          tooltip: {
            callbacks: {
              title: items => items[0].label,
              label: item => {
                if (item.datasetIndex === 0) {
                  const eq   = item.raw.toLocaleString("en-US", {style: "currency", currency: "USD", maximumFractionDigits: 0})
                  const pk   = peak[item.dataIndex].toLocaleString("en-US", {style: "currency", currency: "USD", maximumFractionDigits: 0})
                  const dd   = raw[item.dataIndex].drawdown_pct
                  const ddStr = dd !== null ? `${parseFloat(dd).toFixed(1)}%` : "—"
                  return [`Equity: ${eq}`, `Peak: ${pk}`, `Drawdown: ${ddStr}`]
                }
                return null
              },
              filter: item => item.datasetIndex === 0
            }
          }
        },
        scales: {
          x: {
            ticks: {
              color: "#6b7280",
              maxTicksLimit: 8,
              font: { size: 10 }
            },
            grid: { color: "#1f2937" }
          },
          y: {
            ticks: {
              color: "#6b7280",
              font: { size: 10 },
              callback: v => "$" + Math.round(v).toLocaleString()
            },
            grid: { color: "#1f2937" }
          }
        }
      }
    }

    if (this._chart) {
      this._chart.destroy()
    }
    this._chart = new Chart(this.el, config)
  }
}

// ── LiveSocket setup ─────────────────────────────────────────────────────────

let csrfToken = document.querySelector("meta[name='csrf-token']").getAttribute("content")

let liveSocket = new LiveSocket("/live", Socket, {
  longPollFallbackMs: 2500,
  params: {_csrf_token: csrfToken},
  hooks: { EquityChart }
})

liveSocket.connect()
window.liveSocket = liveSocket
