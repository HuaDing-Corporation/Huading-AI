import type { Config } from "tailwindcss";

// 华鼎 design system — every value maps to a CSS variable defined in
// src/app/globals.css so components never hardcode colors and dark mode can be
// swapped by flipping the `.dark` class (tokens reserved, refined in M2).
const config: Config = {
  darkMode: ["class"],
  content: ["./src/**/*.{ts,tsx}"],
  theme: {
    extend: {
      colors: {
        // shadcn compatibility tokens (kept so existing primitives still build)
        border: "hsl(var(--border))",
        background: "hsl(var(--background))",
        foreground: "hsl(var(--foreground))",
        primary: {
          DEFAULT: "hsl(var(--primary))",
          foreground: "hsl(var(--primary-foreground))"
        },
        muted: {
          DEFAULT: "hsl(var(--muted))",
          foreground: "hsl(var(--muted-foreground))"
        },
        // 华鼎 tokens
        ink: "var(--ink)",
        "ink-soft": "var(--ink-soft)",
        "ink-faint": "var(--ink-faint)",
        gold: "var(--gold)",
        "gold-deep": "var(--gold-deep)",
        bronze: "var(--bronze)",
        champ: "var(--champ)",
        base: "var(--bg-base)",
        "line-gold": "var(--bd-gold)",
        "line-glass": "var(--bd-glass)",
        "line-sel": "var(--bd-sel)",
        "glass-fill": "var(--glass-fill)",
        "glass-soft": "var(--glass-soft)",
        "glass-hover": "var(--glass-hover)",
        "chip-sel": "var(--chip-sel)",
        "success-fg": "var(--success-fg)",
        "success-bg": "var(--success-bg)",
        "run-fg": "var(--run-fg)",
        "run-bg": "var(--run-bg)",
        "queue-fg": "var(--queue-fg)",
        "queue-bg": "var(--queue-bg)",
        "error-fg": "var(--error-fg)",
        "error-bg": "var(--error-bg)",
        track: "var(--track)"
      },
      backgroundImage: {
        "grad-gold": "var(--grad-gold)",
        "grad-glass": "var(--glass-bg)",
        "grad-mark": "var(--grad-mark)",
        "grad-done": "var(--grad-done)"
      },
      borderRadius: {
        lg: "var(--radius)",
        md: "calc(var(--radius) - 2px)",
        sm: "calc(var(--radius) - 4px)",
        card: "var(--radius-card)",
        panel: "var(--radius-panel)",
        btn: "var(--radius-btn)",
        field: "var(--radius-field)",
        mark: "var(--radius-mark)",
        chip: "var(--radius-chip)",
        pill: "var(--radius-pill)",
        badge: "var(--radius-badge)"
      },
      boxShadow: {
        glass: "var(--shadow-glass)",
        button: "var(--shadow-button)",
        "nav-active": "var(--shadow-nav-active)",
        avatar: "var(--shadow-avatar)",
        thumb: "var(--shadow-thumb)",
        "thumb-done": "var(--shadow-thumb-done)",
        "thumb-failed": "var(--shadow-thumb-failed)",
        mark: "var(--shadow-mark)",
        "focus-gold": "var(--shadow-focus-gold)"
      },
      fontFamily: {
        sans: [
          "-apple-system",
          "SF Pro Display",
          "PingFang SC",
          "Microsoft YaHei",
          "system-ui",
          "sans-serif"
        ]
      },
      letterSpacing: {
        brand: "3px"
      },
      keyframes: {
        "glass-in": {
          from: { opacity: "0", transform: "translateY(8px)" },
          to: { opacity: "1", transform: "translateY(0)" }
        }
      },
      animation: {
        "glass-in": "glass-in 200ms ease-out both"
      }
    }
  },
  plugins: [require("tailwindcss-animate")]
};

export default config;
