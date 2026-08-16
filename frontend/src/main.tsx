import { StrictMode } from 'react'
import { createRoot } from 'react-dom/client'
import App from './App'
import './styles.css'
import './workflow.css'
import './controls.css'
import './director-controls.css'
import './workflow-ui.css'
import './layout-fix.css'
import './premium.css'
import './white-theme.css'

createRoot(document.getElementById('root')!).render(
  <StrictMode><App /></StrictMode>,
)
