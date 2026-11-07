import axios from 'axios'

const apiClient = axios.create({
  timeout: 30_000,
  headers: { Accept: 'application/json' },
})

apiClient.interceptors.request.use((config) => {
  try {
    const token = window.localStorage.getItem('stock-radar-admin-token')
    if (token) config.headers.set('X-API-Key', token)
  } catch {
    // Storage may be unavailable in private mode; loopback writes still work.
  }
  return config
})

apiClient.interceptors.response.use(
  (response) => response,
  (error) => {
    const detail = error.response?.data?.error?.message
      || error.response?.data?.detail
      || error.message
      || 'Request failed'
    error.userMessage = String(detail)
    return Promise.reject(error)
  },
)

export default apiClient
