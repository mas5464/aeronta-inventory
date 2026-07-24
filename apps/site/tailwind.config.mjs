import preset from "../../packages/tailwind-preset/index.js";

/** @type {import('tailwindcss').Config} */
export default {
  presets: [preset],
  content: ["./src/**/*.{astro,tsx,mdx}"],
};
