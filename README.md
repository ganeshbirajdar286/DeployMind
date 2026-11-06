# 🚀 AI DevOps Agent — Full-Stack Cloud Automation Platform

An autonomous, enterprise-grade DevOps platform featuring a **React.js Console**, a **Node.js Express API Gateway with Prisma & Neon PostgreSQL**, and a **Python FastAPI Agent Engine with LangGraph and Cloud Sandboxes**.

---

## 🏛️ System Architecture

```mermaid
flowchart LR
    subgraph Frontend ["React.js Web Console (apps/web)"]
        UI["React 18 + Vite + Tailwind CSS"]
        Redux["Redux Toolkit Store<br/>(system, devops, deploy)"]
        UI <--> Redux
    end

    subgraph Gateway ["Node.js API Gateway (apps/api)"]
        Express["Express Server (:5000)<br/>(Helmet, CORS Whitelist, Rate Limiting)"]
        Prisma["Prisma ORM"]
        Express --> Prisma
        Prisma --> Neon[("Neon Serverless PostgreSQL")]
    end

    subgraph AgentEngine ["Python Agent Engine (services/agent-engine)"]
        FastAPI["FastAPI Microservice (:8000)"]
        LangGraph["LangGraph Autonomous Engine"]
        CloudDeployer["Cloud Deployer (Render + Vercel)"]
        FastAPI --> LangGraph
        FastAPI --> CloudDeployer
    end

    Redux -->|REST / Polling| Express
    Express -->|Inspect / Run / Deploy| FastAPI
    FastAPI -.->|Webhook Event Callbacks| Express
    CloudDeployer -->|Backend| Render["Render Web Service"]
    CloudDeployer -->|Frontend| Vercel["Vercel Project"]
```

---

## 🔒 Security Hardening

- **Command Injection Prevention**: Git operations use `child_process.execFile` with argument arrays instead of raw shell string interpolation, combined with strict repository regex validation (`https://github.com/owner/repo`).
- **Secret Redaction**: Environment secrets (`MOGODB_URL`, `JWT_SECRET`, `RENDER_API_KEY`, `VERCEL_TOKEN`, `DATABASE_URL`) are automatically masked (`ghp_****...`) in database records, webhooks, and live terminal streams.
- **HTTP Security Headers**: `helmet` enables Content Security Policy, Frameguard, and X-Content-Type-Options.
- **CORS Protection**: Restricted to trusted local development origins (`http://localhost:5173`) and designated production origins.
- **Rate Limiting**: `express-rate-limit` prevents denial-of-service on automated build and deploy triggers.
- **SQL Injection Prevention**: Prisma prepared statements guarantee parameterized queries against Neon PostgreSQL.

---

## 🚀 Quick Start Guide

### 1. Configure Neon PostgreSQL (`apps/api/.env`)
Create a free database on [Neon](https://neon.tech) and paste your connection string in `apps/api/.env`:
```env
PORT=5000
DATABASE_URL="postgresql://neondb_owner:YOUR_PASSWORD@ep-sample-pooler.us-east-2.aws.neon.tech/neondb?sslmode=require"
AGENT_ENGINE_URL="http://localhost:8000"
CORS_ORIGIN="http://localhost:5173"
```

To sync your database schema:
```bash
npm run prisma:generate
npm run prisma:push
```

---

### 2. Install Dependencies
In your terminal, install dependencies for both the API and Web apps:
```bash
# Install Node.js API dependencies
cd apps/api
npm install

# Install React Web dependencies
cd ../web
npm install
```

---

### 3. Launch Services

You can run the three services in separate terminal windows:

#### Terminal 1 — Python FastAPI Agent Engine:
```bash
cd services/agent-engine
uv run python main.py
# Engine runs on http://localhost:8000
```

#### Terminal 2 — Node.js Express API Gateway:
```bash
npm run dev:api
# API runs on http://localhost:5000
```

#### Terminal 3 — React.js Web Console:
```bash
npm run dev:web
# Web Console runs on http://localhost:5173
```

Open your browser at **`http://localhost:5173`** to access the AI DevOps Agent Console!
