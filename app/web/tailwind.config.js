/** Tailwind build config for the dashboard. Output: app/web/static/app.css (see scripts/build_assets.sh). */
module.exports = {
  content: { relative: true, files: ["./templates/**/*.html"] },
  // Classes assembled at render time in partials/macros.html (badge / stat colours).
  safelist: [{ pattern: /^(bg|text)-(green|red|amber|slate|blue|indigo)-(100|700|800)$/ }],
  theme: { extend: {} },
  plugins: [],
};
