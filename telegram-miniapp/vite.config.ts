import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'

// https://vite.dev/config/
export default defineConfig({
  plugins: [react()],
  server: {
    allowedHosts: [
      'tarrotai.ru',  // Add your current CloudPub subdomain here
	'api.tarrotai.ru'
    ],
    // Optional: Expose to network/tunnel if not already
    host: true,  // or '0.0.0.0'
  },
}
)
