/** Tailwind build config for the dashboard. Output: app/web/static/app.css (see scripts/build_assets.sh).
 * Colours are semantic design tokens defined as RGB triplets in tailwind.input.css, switched per theme by
 * <html data-theme="dark|light">. Direction-sensitive spacing uses logical utilities (ms-/me-/ps-/pe-/start-/end-)
 * so the same markup works for Arabic (RTL) and English (LTR). */
const token = (name) => `rgb(var(--${name}) / <alpha-value>)`;

module.exports = {
  content: { relative: true, files: ["./templates/**/*.html", "./static/ui.js", "./static/login3d.js"] },
  theme: {
    extend: {
      colors: {
        bg: token("bg"),
        "bg-2": token("bg-2"),
        surface: token("surface"),
        "surface-2": token("surface-2"),
        line: token("line"),
        ink: token("ink"),
        muted: token("muted"),
        faint: token("faint"),
        accent: token("accent"),
        accent2: token("accent-2"),
        accent3: token("accent-3"),
        ok: token("ok"),
        warn: token("warn"),
        bad: token("bad"),
        info: token("info"),
      },
      fontFamily: {
        sans: ['"IBM Plex Sans Arabic"', "system-ui", "-apple-system", '"Segoe UI"', "Tahoma", "sans-serif"],
        mono: ['"IBM Plex Mono"', "ui-monospace", "SFMono-Regular", "Consolas", "monospace"],
      },
      screens: { xs: "480px" },
    },
  },
  plugins: [],
};
