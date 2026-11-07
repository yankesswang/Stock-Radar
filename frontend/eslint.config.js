import js from '@eslint/js'
import globals from 'globals'
import reactHooks from 'eslint-plugin-react-hooks'
import reactRefresh from 'eslint-plugin-react-refresh'
import { defineConfig, globalIgnores } from 'eslint/config'

export default defineConfig([
  globalIgnores(['dist']),
  {
    files: ['**/*.{js,jsx}'],
    extends: [
      js.configs.recommended,
      reactHooks.configs.flat.recommended,
      reactRefresh.configs.vite,
    ],
    languageOptions: {
      ecmaVersion: 2020,
      globals: globals.browser,
      parserOptions: {
        ecmaVersion: 'latest',
        ecmaFeatures: { jsx: true },
        sourceType: 'module',
      },
    },
    rules: {
      'no-unused-vars': ['error', {
        varsIgnorePattern: '^[A-Z_]',
        argsIgnorePattern: '^[A-Z_]',
      }],
      'no-empty': ['error', { allowEmptyCatch: true }],
      // Chinese prose occasionally contains deliberate full-width spacing.
      'no-irregular-whitespace': 'warn',
      // Loading/error resets at request boundaries are intentional in this
      // existing SPA. Keep the React 19 guidance visible without blocking CI.
      'react-hooks/set-state-in-effect': 'warn',
    },
  },
  {
    files: ['vite.config.js'],
    languageOptions: { globals: globals.node },
  },
])
