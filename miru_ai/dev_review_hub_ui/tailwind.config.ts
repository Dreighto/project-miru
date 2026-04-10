import type { Config } from "tailwindcss";
import tailwindcssAnimate from "tailwindcss-animate";

export default {
  darkMode: ["class"],
  content: ["./index.html", "./src/**/*.{ts,tsx}"],
  theme: {
    extend: {
      colors: {
        drh: {
          bg: "#121212",
          surface: "#1A1B23",
          text: "#E4E4E4",
          muted: "#9CA3AF",
          stroke: "#2A2D38",
        },
        accent: {
          gold: "#f0a500",
          "gold-soft": "rgba(240, 165, 0, 0.12)",
        },
      },
      spacing: {
        "2": "8px",
      },
      borderRadius: {
        lg: "10px",
        md: "8px",
        sm: "6px",
      },
      maxWidth: {
        iphone: "430px",
      },
      keyframes: {
        "accordion-down": {
          from: { height: "0" },
          to: { height: "var(--radix-accordion-content-height)" },
        },
        "accordion-up": {
          from: { height: "var(--radix-accordion-content-height)" },
          to: { height: "0" },
        },
        shimmer: {
          "0%": { transform: "translateX(-120%)" },
          "100%": { transform: "translateX(120%)" },
        },
      },
      animation: {
        "accordion-down": "accordion-down 0.2s ease-out",
        "accordion-up": "accordion-up 0.2s ease-out",
        shimmer: "shimmer 2.4s ease-in-out infinite",
      },
    },
  },
  plugins: [tailwindcssAnimate],
} satisfies Config;
