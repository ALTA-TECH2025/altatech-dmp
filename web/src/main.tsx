import { StrictMode } from 'react';
import { createRoot } from 'react-dom/client';
import { MantineProvider } from '@mantine/core';
import { App } from './app/App';
import { theme } from './ds/theme';
import '@mantine/core/styles.css';
import './styles/app.css';

createRoot(document.getElementById('root')!).render(<StrictMode><MantineProvider theme={theme} defaultColorScheme="auto"><App /></MantineProvider></StrictMode>);
