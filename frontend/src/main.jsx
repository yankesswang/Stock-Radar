import { StrictMode } from 'react'
import { createRoot } from 'react-dom/client'
import { Router } from 'wouter'
import './index.css'
import App from './App.jsx'

// 同一個 dev server 有兩種進入方式：本機直連 :4000（路徑從 / 開始），
// 以及透過 Arthur.OS gateway 掛在 /stocks/ 底下。base 要在執行期判斷，
// 否則走 gateway 時 pathname 是 /stocks/，所有 Route 都比不中而掉進 404。
const base = window.location.pathname.startsWith('/stocks') ? '/stocks' : ''

createRoot(document.getElementById('root')).render(
  <StrictMode>
    <Router base={base}>
      <App />
    </Router>
  </StrictMode>,
)
