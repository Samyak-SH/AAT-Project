/** @type {import('tailwindcss').Config} */
export default {
  content: ["./index.html", "./src/**/*.{js,jsx,ts,tsx}"],
  theme: {
    extend: {
      colors: {
        ink: {
          900: "#0b0f14",
          800: "#111820",
          700: "#1a2430",
          600: "#273441",
          500: "#3a4a5a",
        },
      },
    },
  },
  plugins: [],
};
