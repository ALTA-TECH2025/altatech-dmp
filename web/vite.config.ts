import { defineConfig } from 'vitest/config';
import react from '@vitejs/plugin-react';

// 開發時 /auth 與 /v1 代理到後端（BFF）：cookie 同源，不需要 CORS。
export default defineConfig({
  plugins: [react()],
  resolve: { alias: { '@': new URL('./src', import.meta.url).pathname } },
  server: { port: 5173, proxy: { '/auth': 'http://127.0.0.1:8000', '/v1': 'http://127.0.0.1:8000' } },
  build: { outDir: 'dist', sourcemap: false, rollupOptions: { output: { manualChunks: { mantine: ['@mantine/core', '@mantine/hooks'], echarts: ['echarts/core', 'echarts/charts', 'echarts/components', 'echarts/renderers'] } } } },
  test: { environment: 'node', include: ['src/**/*.test.ts'] },
});
