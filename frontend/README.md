# Tribunal Digital Frontend

Frontend React + Vite + Tailwind para o Sistema Inteligente de Assistencia Juridica Angolana.

## Stack

- React 19
- Vite 8
- TailwindCSS 4
- lucide-react

## Executar

```powershell
cd C:\Projectos\TCC\frontend
copy .env.example .env
npm install
npm run dev
```

Frontend: `http://127.0.0.1:5173`

## Integracao backend

Defina no `.env`:

```env
VITE_API_BASE_URL=http://127.0.0.1:8000
```

Endpoints usados:

- `GET /health`
- `POST /chat`
- `POST /docs/ingest`

## Scripts

- `npm run dev`
- `npm run build`
- `npm run lint`
- `npm run preview`
