/** @type {import('tailwindcss').Config} */
export default {
  darkMode: ["class"],
  content: ["./index.html", "./src/**/*.{ts,tsx}"],
  presets: [require("../../packages/tailwind-preset")],
  plugins: [],
};
