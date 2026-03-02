/** @type {import('tailwindcss').Config} */
export default {
  content: ['./index.html', './src/**/*.{js,ts,jsx,tsx}'],
  safelist: [
    'from-blue-900', 'to-blue-700', 'from-red-900', 'to-red-700', 'from-orange-900', 'to-persimmon',
    'from-green-900', 'to-neon-green', 'from-cyan-900', 'to-cyan-400', 'from-purple-900', 'to-purple-600',
  ],
  theme: {
    extend: {
      colors: {
        'deep-midnight': '#0a0e14',
        'deep-midnight-2': '#0d1520',
        'deep-midnight-3': '#0a1929',
        'cool-blue': '#4fc3f7',
        'cool-blue-glow': 'rgba(79, 195, 247, 0.25)',
        'neon-green': '#cddc39',
        'wasabi': '#cddc39',
        'persimmon': '#ff7043',
        'card-bg': 'rgba(15, 37, 55, 0.45)',
        'border-glass': 'rgba(255, 255, 255, 0.06)',
        'border-glass-strong': 'rgba(255, 255, 255, 0.1)',
      },
      borderRadius: {
        'fintech': '12px',
        'fintech-sm': '10px',
      },
      spacing: {
        'fintech-gap': '1.25rem',
        'fintech-gap-sm': '0.5rem',
      },
      backdropBlur: {
        'glass': '10px',
      },
      boxShadow: {
        'glass': '0 4px 24px rgba(0,0,0,0.2)',
        'glass-hover': '0 8px 32px rgba(0,0,0,0.25), 0 0 24px rgba(79,195,247,0.15)',
        'hex-glow': '0 0 20px rgba(79,195,247,0.4), inset 0 0 20px rgba(79,195,247,0.08)',
        'cool-blue-glow': '0 0 12px rgba(79,195,247,0.25)',
      },
    },
  },
  plugins: [],
}
