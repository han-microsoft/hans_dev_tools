/**
 * Application entry point — mounts the React root and applies
 * global styles. Dark mode is set by default via the HTML class.
 */
import { StrictMode } from 'react';
import { createRoot } from 'react-dom/client';
import App from './App';
import './index.css';

/* Mount the React application into the #root div. */
createRoot(document.getElementById('root')!).render(
  <StrictMode>
    <App />
  </StrictMode>,
);
