import type { Config } from "tailwindcss";

const config: Config = {
  content: ["./app/**/*.{ts,tsx}", "./components/**/*.{ts,tsx}"],
  theme: {
    extend: {
      colors: {
        ink: "#1F2933",
        field: "#F6F7F9",
        line: "#D8DEE6",
        accent: "#146C75",
        amber: "#B7791F",
        panel: "#FFFFFF",
        muted: "#64748B"
      }
    }
  },
  plugins: []
};

export default config;
