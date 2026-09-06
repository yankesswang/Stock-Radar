import { StrictMode } from 'react'
import { createRoot } from 'react-dom/client'
import { Router } from 'wouter'
import './index.css'
import './theme.js'   // 套 <html data-theme>，並掛系統/跨分頁同步
import App from './App.jsx'

// Vite 設了 base '/stocks/'，所以不論本機直連 :4000 或走 Arthur.OS gateway，
// pathname 都在 /stocks/ 底下，wouter 需要同樣的 base 才比得中 Route。
// 仍然在執行期判斷而不寫死：build 出來的靜態檔若被掛在別的路徑下也能運作。
const base = window.location.pathname.startsWith('/stocks') ? '/stocks' : ''

createRoot(document.getElementById('root')).render(
  <StrictMode>
    <Router base={base}>
      <App />
    </Router>
  </StrictMode>,
)
