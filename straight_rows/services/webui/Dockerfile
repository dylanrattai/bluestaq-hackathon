# Build context is services/webui/ (see compose.yaml). Runs the Vite dev server.
FROM node:24-bookworm-slim
WORKDIR /app
RUN chown node:node /app
USER node
COPY --chown=node:node package.json package-lock.json ./
RUN npm ci
COPY --chown=node:node . .
EXPOSE 5173
# node_modules lives in an anonymous volume that survives rebuilds; sync it to package-lock.json on start (fast when unchanged)
CMD ["sh", "-c", "npm install --no-audit --no-fund && exec npm run dev -- --host 0.0.0.0 --port 5173"]
