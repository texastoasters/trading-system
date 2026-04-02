// tailwind.config.js
const path = require("path")

module.exports = {
  content: [
    "../lib/dashboard_web/**/*.{heex,ex,js}",
    "../lib/dashboard_web.ex",
  ],
  darkMode: "class",
  theme: {
    extend: {
      fontFamily: {
        mono: ["JetBrains Mono", "Fira Code", "Consolas", "ui-monospace", "monospace"],
      },
    },
  },
  plugins: [],
}
