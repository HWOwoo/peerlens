import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

// 개발 중에는 /api 요청을 FastAPI(8000)로 넘긴다. 배포 시에는 FastAPI가 dist/를 직접 서빙한다.
export default defineConfig({
  plugins: [react()],
  server: { proxy: { "/api": "http://127.0.0.1:8000" } },
});
