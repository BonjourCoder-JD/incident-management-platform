import React from 'react';
import { createRoot } from 'react-dom/client';
import App from './App';
import { applyTheme, storedTheme } from './theme';
import './style.css';

// Apply a stored theme choice before the first render, so pages never flash the other theme.
applyTheme(storedTheme());

createRoot(document.getElementById('root')!).render(<React.StrictMode><App /></React.StrictMode>);
