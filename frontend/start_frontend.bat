@echo off
title TCC Frontend - Vite (porta 5173)
cd /d C:\Projectos\TCC\frontend
if "%VITE_API_BASE_URL%"=="" (
  echo [FRONTEND] Usando backend em http://127.0.0.1:8000
) else (
  echo [FRONTEND] Backend configurado: %VITE_API_BASE_URL%
)
npm run dev
pause
