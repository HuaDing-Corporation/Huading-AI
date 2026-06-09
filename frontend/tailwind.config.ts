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
        track: "var(--track)"
      },
      backgroundImage: {
        "grad-gold": "var(--grad-gold)",
        "grad-glass": "var(--glass-bg)",
        "grad-mark": "var(--grad-mark)",
        "grad-done": "linear-gradient(135deg,#a7cba0,#6aa67f)"
      },
      borderRadius: {
        lg: "var(--radius)",
        md: "calc(var(--radius) - 2px)",
        sm: "calc(var(--radius) - 4px)",
        card: "26px",
        panel: "20px",
        btn: "17px",
        field: "16px",
        mark: "16px",
        chip: "15px",
        pill: "13px",
        badge: "11px"
      },
      boxShadow: {
        glass:
          "8px 18px 48px -16px rgba(150,118,52,.26),0 0 28px rgba(231,210,162,.24),inset 0 1px 0 rgba(255,255,255,.92)",
        button:
          "0 13px 30px rgba(156,124,62,.42),inset 0 1px 0 rgba(255,255,255,.55)",
        "nav-active":
          "0 8px 20px rgba(156,124,62,.4),inset 0 1px 0 rgba(255,255,255,.45)",
        avatar: "0 5px 14px rgba(156,124,62,.42),inset 0 1px 0 rgba(255,255,255,.5)",
        thumb: "0 6px 15px rgba(156,124,62,.34),inset 0 1px 0 rgba(255,255,255,.5)",
        "thumb-done": "0 6px 15px rgba(106,166,127,.3)",
        mark: "inset 0 1px 0 rgba(255,255,255,.9),0 0 16px rgba(231,210,162,.34)",
        "focus-gold": "0 0 0 3px rgba(189,154,89,.22)"
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
